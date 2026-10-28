"""Cache em memória para leitura de anexos/binários dentro dos .ufdr das conversas.

Evita, ao abrir uma conversa no WhatsApp, reabrir o .ufdr (reler o diretório central do
zip, que pode ter dezenas de milhares de entradas) e redecodificar a mesma imagem a cada
uma das requisições de anexo que a tela dispara. Dois caches independentes, cada um LRU
com expiração por inatividade (TTL renovado a cada acesso — se a conversa continuar sendo
usada o cache permanece; se não, expira sozinho):

- zips abertos, por caminho de .ufdr (só o diretório central fica em memória, não o
  conteúdo dos arquivos — leve mesmo para um .ufdr de centenas de GB);
- bytes já lidos/processados de um membro do zip, por chave lógica (ufdr, caminho interno,
  variante). Só entra quem for menor que CONTEUDO_ITEM_TAMANHO_MAX — vídeos e arquivos
  grandes continuam sendo lidos direto do zip a cada vez, para não inchar a RAM.

Não há thread/processo de limpeza em segundo plano: a expiração é verificada de forma
preguiçosa (a cada chamada), o que é suficiente para um app com tráfego constante.
"""

import io
import struct
import tarfile
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path

from funcoes import desempenho_utils, ufdr_zip

TTL_SEGUNDOS = 60 * 60  # 1 hora sem uso e o item expira

# Os dois tetos abaixo são dimensionados pelo hardware (funcoes/desempenho_utils.py); os valores
# que estavam fixos aqui (8 zips, 200 MB) viraram o piso. Com poucos slots e vários analistas em
# .ufdr diferentes, a LRU passa a evictar a cada acesso — e um miss não custa "abrir um arquivo",
# custa reconstruir o diretório central de um zip com centenas de milhares de entradas, que é
# exatamente o que este cache existe para evitar.
ZIPS_MAX = desempenho_utils.valor_int("CACHE_UFDR_ZIPS", 8)

CONTEUDO_ITEM_TAMANHO_MAX = 5 * 1024 * 1024  # 5 MB — acima disso não cacheia
CONTEUDO_TOTAL_TAMANHO_MAX = desempenho_utils.valor_int("CACHE_CONTEUDO_MB", 200) * 1024 * 1024
# Acompanha o teto em bytes: deixar 500 fixo faria o limite por quantidade morder muito antes do
# limite por tamanho assim que a máquina permitisse um cache maior.
CONTEUDO_MAX_ITENS = max(500, CONTEUDO_TOTAL_TAMANHO_MAX // (100 * 1024))

_lock = threading.Lock()


# ------------------------------------------------------------------ fontes: local (Path) ou nó (FonteRemota)
#
# Toda função deste módulo aceita no lugar do Path local uma `no_ponte.FonteRemota` — o passaporte
# de um .ufdr que mora na pasta data() de um NÓ descentralizado (docs/no_descentralizado.md). As
# trêshelpers abaixo são os únicos lugares que conhecem os dois mundos:
# - _chave: a chave de cache. O str() de uma FonteRemota é estável ("no://<id>/<relativo>"), e é
#   ela que faz o cache de conteúdo acertar entre requisições (dict do registro muda a cada consulta);
# - _abrir_base: o "open(path, 'rb')" das janelas de ZIP/TAR aninhados — um ArquivoRemoto é um
#   arquivo seekable igual, lido por faixa via HTTP;
# - fechar_zip: não fecha fonte remota por fora — quem evence o LRU cuida do handle.

def _chave(caminho) -> str:
    return str(caminho)


def _lock_abertura(chave: str) -> threading.Lock:
    with _lock:
        trava = _locks_abertura.get(chave)
        if trava is None:
            trava = _locks_abertura[chave] = threading.Lock()
        return trava


def _abrir_base(caminho):
    if getattr(caminho, "eh_remoto", False):
        return caminho.abrir_base()
    return open(caminho, "rb")

_zips: "OrderedDict[str, tuple[zipfile.ZipFile, float]]" = OrderedDict()
# Uma trava por caminho: sem ela, os 30 pedidos de imagem de uma galeria que chega junto em um
# .ufdr frio (ou no link fino de um nó) cada um reabre o zip — dezenas de leituras do diretório
# central em paralelo, o link lota, nenhuma abre. A primeira abre, as demais esperam e pegam o
# handle pronto.
_locks_abertura: "dict[str, threading.Lock]" = {}
_zips_aninhados: "OrderedDict[tuple, tuple[zipfile.ZipFile, object, float]]" = OrderedDict()
# (TarFile, janela, lock, último acesso). O lock serializa extractfile+read: o tarfile lê do
# MESMO arquivo externo (a janela) em seek+read separados, e 80+ imagens carregando juntas
# (página do WhatsApp parcial) corrompiam a posição uma da outra — sem o lock, um seek de uma
# thread atropelava o read de outra.
_tars_aninhados: "OrderedDict[tuple, tuple[tarfile.TarFile, object, threading.Lock, float]]" = OrderedDict()
_conteudo: "OrderedDict[tuple, tuple[bytes, float]]" = OrderedDict()
_conteudo_bytes_total = 0


def _purgar_zips(agora: float) -> None:
    """Tira do cache os handles velhos — SEM fecha-los. Regra do descartar(): uma requisição
    pode estar no meio de um read/streaming com a referência que pegou antes da purga; fechar
    por baixo dela é ValueError: read of closed file no meio de um anexo. Quem fecha é o GC
    quando a última referência morrer (e ele fecha: ZipFile e o fp que ele carrega)."""
    expirados = [k for k, (_, ultimo) in _zips.items() if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        _zips.pop(k, None)


def _purgar_conteudo(agora: float) -> None:
    global _conteudo_bytes_total
    expirados = [k for k, (_, ultimo) in _conteudo.items() if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        dados, _ = _conteudo.pop(k)
        _conteudo_bytes_total -= len(dados)


def obter_zip(caminho) -> zipfile.ZipFile:
    """Devolve um zipfile.ZipFile aberto para `caminho` (Path local ou FonteRemota de um nó),
    reaproveitando o handle em cache se já existir (renova o TTL). Levanta FileNotFoundError
    se o arquivo não existir (ou o nó não o tiver)."""
    chave = _chave(caminho)
    agora = time.monotonic()
    with _lock:
        _purgar_zips(agora)
        item = _zips.get(chave)
        if item is not None:
            zf, _ = item
            _zips[chave] = (zf, agora)
            _zips.move_to_end(chave)
            return zf

    # abertura do zip fica fora do lock GLOBAL (I/O), mas serializada por caminho (ver
    # _locks_abertura) — e o dicionário é atualizado sob lock.
    # ufdr_zip.abrir tolera .ufdr truncado (índice perdido) — o visualizador de anexos
    # continua servindo o que sobreviveu de um arquivo recuperado.
    with _lock_abertura(chave):
        agora = time.monotonic()
        with _lock:
            item = _zips.get(chave)
            if item is not None:
                zf, _ = item
                _zips[chave] = (zf, agora)
                _zips.move_to_end(chave)
                return zf
        zf = ufdr_zip.abrir(caminho)
    with _lock:
        existente = _zips.get(chave)
        if existente is not None:
            # outra thread já abriu enquanto liberamos o lock — descarta a nossa e usa a dela
            zf.close()
            zf, _ = existente
            _zips[chave] = (zf, agora)
            _zips.move_to_end(chave)
            return zf
        _zips[chave] = (zf, agora)
        _zips.move_to_end(chave)
        # evicção LRU também não fecha (ver _purgar_zips): o handle pode estar em uso
        # numa requisição concorrente; o GC cuida dele
        while len(_zips) > ZIPS_MAX:
            _zips.popitem(last=False)
    return zf


def descartar(caminho) -> None:
    """Invalida o handle e os bytes em cache daquele .ufdr (leitura suspeita pela ponte:
    a próxima leitura abre conexão NOVA). Não fecha o ZipFile — outra thread pode estar
    usando a referência que já pegou; o GC cuida dele."""
    chave = _chave(caminho)
    with _lock:
        _zips.pop(chave, None)
        for k in [k for k in _conteudo if k[0] == chave]:
            _conteudo.pop(k, None)


def _purgar_zips_aninhados(agora: float) -> None:
    # não fecha nada (ver _purgar_zips): o download de um membro aninhado segura o
    # ZipFile E a janela do arquivo externo pelo tempo inteiro do streaming — fechar aqui
    # corta o chão de quem está lendo. GC fecha quando a última referência morrer.
    expirados = [k for k, (_zf, _faixa, ultimo) in _zips_aninhados.items()
                 if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        _zips_aninhados.pop(k, None)


class _FaixaArquivo:
    """Apresenta um intervalo de bytes de um arquivo como um stream seekable — é o que permite
    abrir um ZIP aninhado (arquivo STORED dentro do .ufdr) com zipfile.ZipFile sem ler os GBs
    do membro para a memória. zipfile só precisa de read/seek/tell/seekable, todos mapeados
    para a janela [inicio, inicio+tamanho) do arquivo externo."""

    __slots__ = ("_fp", "_inicio", "_tamanho", "_pos")

    def __init__(self, fp, inicio: int, tamanho: int) -> None:
        self._fp = fp
        self._inicio = inicio
        self._tamanho = tamanho
        self._pos = 0

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._tamanho + offset
        self._pos = max(0, min(self._pos, self._tamanho))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self._tamanho - self._pos
        n = max(0, min(n, self._tamanho - self._pos))
        if n == 0:
            return b""
        self._fp.seek(self._inicio + self._pos)
        dados = self._fp.read(n)
        self._pos += len(dados)
        return dados


def _fechar_faixa(faixa) -> None:
    try:
        faixa._fp.close()
    except Exception:
        pass


def _offset_dados_stored(fp, info: zipfile.ZipInfo) -> int:
    """Offset do primeiro byte dos dados de um membro STORED, logo após o cabeçalho local.

    zipfile não expõe esse offset (só `header_offset`, que aponta para o início do cabeçalho
    local), e ele é necessário para apresentar o ZIP aninhado como uma janela no arquivo
    externo. O cabeçalho local tem 30 bytes fixos + nome + campo extra (ver especificação)."""
    fp.seek(info.header_offset + 26)
    nome_len = struct.unpack("<H", fp.read(2))[0]
    extra_len = struct.unpack("<H", fp.read(2))[0]
    return info.header_offset + 30 + nome_len + extra_len


def obter_zip_aninhado(caminho: Path, membro_externo: str) -> zipfile.ZipFile:
    """Abre, como zipfile.ZipFile, um ZIP que está DENTRO do .ufdr (`files/Archives/*.zip`).

    Os archives de nuvem do Cellebrite vêm STORED (sem compressão) dentro do .ufdr, então dá
    para abri-los sem descomprimir os GBs do membro: apresenta-se a janela de bytes do membro
    como um arquivo seekable e deixa o zipfile ler só o diretório central. Um membro
    COMPRIMIDO não pode ser aberto assim — nesse caso o conteúdo inteiro é lido (fallback).

    O handle é cacheado como os zips externos, junto com o handle do arquivo que sustenta a
    janela; a evicção fecha os dois."""
    chave = (_chave(caminho), membro_externo)
    agora = time.monotonic()
    with _lock:
        _purgar_zips_aninhados(agora)
        item = _zips_aninhados.get(chave)
        if item is not None:
            zf, _faixa, _ = item
            _zips_aninhados[chave] = (zf, _faixa, agora)
            _zips_aninhados.move_to_end(chave)
            return zf

    zf_externo = obter_zip(caminho)
    info = zf_externo.getinfo(membro_externo)
    fp = _abrir_base(caminho)
    if info.compress_type == zipfile.ZIP_STORED:
        faixa = _FaixaArquivo(fp, _offset_dados_stored(fp, info), info.file_size)
    else:
        # fallback raro: membro comprimido — descomprime inteiro (aceitável para os archives
        # pequenos que não são STORED; os grandes do Cellebrite são STORED)
        faixa = _FaixaArquivo(_BytesFaixa(zf_externo.read(membro_externo)), 0, info.file_size)
    zf = zipfile.ZipFile(faixa)
    with _lock:
        existente = _zips_aninhados.get(chave)
        if existente is not None:
            zf.close()
            _fechar_faixa(faixa)
            zf, faixa, _ = existente
            _zips_aninhados[chave] = (zf, faixa, agora)
            _zips_aninhados.move_to_end(chave)
            return zf
        _zips_aninhados[chave] = (zf, faixa, agora)
        _zips_aninhados.move_to_end(chave)
        while len(_zips_aninhados) > ZIPS_MAX:
            _zips_aninhados.popitem(last=False)  # sem close: ver _purgar_zips_aninhados
    return zf


def ler_membro_aninhado(caminho_ufdr: Path, membro_externo: str, caminho_interno: str,
                        variante: str = "raw") -> bytes:
    """Lê um membro de um ZIP ou TAR aninhado dentro do .ufdr (com o cache de conteúdo)."""
    chave = (str(caminho_ufdr), membro_externo, caminho_interno, variante)
    dados = obter_conteudo(chave)
    if dados is not None:
        return dados
    if membro_externo.lower().endswith(".tar"):
        dados = _ler_membro_tar(caminho_ufdr, membro_externo, caminho_interno)
    else:
        zf = obter_zip_aninhado(caminho_ufdr, membro_externo)
        dados = zf.read(caminho_interno)  # KeyError se o membro não existir
    guardar_conteudo(chave, dados)
    return dados


def abrir_membro_aninhado(caminho_ufdr: Path, membro_externo: str, caminho_interno: str):
    """Abre (streaming) um membro de um ZIP ou TAR aninhado dentro do .ufdr."""
    if membro_externo.lower().endswith(".tar"):
        return _abrir_membro_tar(caminho_ufdr, membro_externo, caminho_interno)
    return obter_zip_aninhado(caminho_ufdr, membro_externo).open(caminho_interno)


def _purgar_tars_aninhados(agora: float) -> None:
    # não fecha nada (ver _purgar_zips_aninhados). Aqui fechar era ainda pior: a leitura do
    # tar é seek+read na janela COMPARTILHADA serializada pelo _tar_lock — fechar o TarFile
    # e a janela sem o _tar_lock (como esta purga fazia) derrotava justamente a trava que
    # existe porque "80+ imagens carregando juntas corrompiam a posição uma da outra".
    expirados = [k for k, (_tf, _faixa, _lock, ultimo) in _tars_aninhados.items()
                 if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        _tars_aninhados.pop(k, None)


def obter_tar_aninhado(caminho: Path, membro_externo: str) -> tarfile.TarFile:
    """Abre um TAR que está DENTRO do .ufdr (files/Archives/*.tar, mídia do iCloud) como
    tarfile, sem descomprimir os GBs do membro: os tars vêm STORED e são abertos por janela de
    bytes no arquivo externo (mesmo mecanismo de obter_zip_aninhado)."""
    chave = (_chave(caminho), membro_externo)
    agora = time.monotonic()
    with _lock:
        _purgar_tars_aninhados(agora)
        item = _tars_aninhados.get(chave)
        if item is not None:
            tf, _faixa, _tl, _ = item
            _tars_aninhados[chave] = (tf, _faixa, _tl, agora)
            _tars_aninhados.move_to_end(chave)
            return tf

    zf_externo = obter_zip(caminho)
    info = zf_externo.getinfo(membro_externo)
    fp = _abrir_base(caminho)
    if info.compress_type == zipfile.ZIP_STORED:
        faixa = _FaixaArquivo(fp, _offset_dados_stored(fp, info), info.file_size)
    else:
        faixa = _FaixaArquivo(_BytesFaixa(zf_externo.read(membro_externo)), 0, info.file_size)
    tf = tarfile.open(fileobj=faixa, mode="r:")
    with _lock:
        existente = _tars_aninhados.get(chave)
        if existente is not None:
            tf.close()
            _fechar_faixa(faixa)
            tf, faixa, tl, _ = existente
            _tars_aninhados[chave] = (tf, faixa, tl, agora)
            _tars_aninhados.move_to_end(chave)
            return tf
        _tars_aninhados[chave] = (tf, faixa, _tar_lock(caminho, membro_externo), agora)
        _tars_aninhados.move_to_end(chave)
        while len(_tars_aninhados) > ZIPS_MAX:
            _tars_aninhados.popitem(last=False)  # sem close: ver _purgar_tars_aninhados
    return tf


# Lock por (ufdr, membro) VIVO FORA do cache de entradas: a entrada pode ser purgada/evictada
# no meio de uma leitura, e duas threads lendo o mesmo tar não podem acabar em locks
# diferentes só porque a tupla do cache morreu entre uma chamada e outra. O custo é um Lock
# por par já visto (dezenas numa instalação grande — nada) em troca de uma trava estável.
_tar_locks: "dict[tuple, threading.Lock]" = {}


def _tar_lock(caminho, membro_externo: str) -> threading.Lock:
    """A trava de leitura deste tar aninhado. Não precisa da entrada no cache existir:
    setdefault no dict é a prova de corrida no CPython, e a mesma chave sempre devolve o
    mesmo Lock (era daqui que saía o KeyError quando a entrada era evictada entre o
    obter_tar_aninhado e a leitura da tupla)."""
    return _tar_locks.setdefault((_chave(caminho), membro_externo), threading.Lock())


def _tar_membro(tf: tarfile.TarFile, nome_interno: str) -> tarfile.TarInfo | None:
    """Acha o membro do tar pelo nome, aceitando com ou sem a barra inicial."""
    try:
        return tf.getmember(nome_interno)
    except KeyError:
        sem_barra = nome_interno.lstrip("/")
        for m in tf.getmembers():
            if m.name == sem_barra or m.name.lstrip("/") == sem_barra:
                return m
    return None


def _ler_membro_tar(caminho_ufdr: Path, membro_externo: str, nome_interno: str) -> bytes:
    tf = obter_tar_aninhado(caminho_ufdr, membro_externo)
    membro = _tar_membro(tf, nome_interno)
    if membro is None:
        raise KeyError(nome_interno)
    with _tar_lock(caminho_ufdr, membro_externo):
        f = tf.extractfile(membro)
        if f is None:
            return b""
        return f.read()


def _abrir_membro_tar(caminho_ufdr: Path, membro_externo: str, nome_interno: str):
    tf = obter_tar_aninhado(caminho_ufdr, membro_externo)
    membro = _tar_membro(tf, nome_interno)
    if membro is None:
        raise KeyError(nome_interno)
    # lê o membro inteiro sob o lock e devolve um BytesIO: a janela do arquivo externo é
    # compartilhada entre as threads, e um stream "preguiçoso" seguraria seek/read concorrente.
    with _tar_lock(caminho_ufdr, membro_externo):
        f = tf.extractfile(membro)
        if f is None:
            raise KeyError(nome_interno)
        return io.BytesIO(f.read())


def membro_aninhado_existe(caminho_ufdr: Path, membro_externo: str, nome_interno: str) -> bool:
    """True se o membro existe no ZIP/TAR aninhado — sem ler o conteúdo. Usado para devolver 404
    ANTES de começar o streaming (que já teria enviado o cabeçalho 200)."""
    if membro_externo.lower().endswith(".tar"):
        return _tar_membro(obter_tar_aninhado(caminho_ufdr, membro_externo), nome_interno) is not None
    try:
        obter_zip_aninhado(caminho_ufdr, membro_externo).getinfo(nome_interno)
        return True
    except KeyError:
        return False


def desmembrar_aninhado(caminho_zip: str) -> tuple[str, str] | None:
    """Desmonta o ponteiro de dois níveis 'files/Archives/<zip>::<caminho_interno>' em
    (membro_externo, caminho_interno). None quando não é um ponteiro aninhado."""
    if not caminho_zip or "::" not in caminho_zip or caminho_zip.startswith("IPED::"):
        return None
    membro, interno = caminho_zip.split("::", 1)
    return (membro, interno) if interno else None


class _BytesFaixa:
    """Adapta um bytes na memória à interface mínima que _FaixaArquivo espera (fallback)."""

    __slots__ = ("_dados", "_pos")

    def __init__(self, dados: bytes) -> None:
        self._dados = dados
        self._pos = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._dados) + offset
        self._pos = max(0, min(self._pos, len(self._dados)))
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._dados) - self._pos
        dados = self._dados[self._pos:self._pos + n]
        self._pos += len(dados)
        return dados

    def close(self) -> None:
        pass


def fechar_zip(caminho) -> None:
    """Fecha e descarta do cache o handle aberto para `caminho`, se houver. Preciso ANTES de
    mover o .ufdr para a pasta do inquérito (funcoes/organizacao_arquivos.py): um handle aberto
    (TTL de até 1h) impede o rename no bind mount de data/ do Docker Desktop no Windows — mesmo
    motivo de iped_cache.fechar_conexoes. Fontes remotas (nó) não têm o que fechar daqui: o
    arquivo não vai ser movido (fica no nó) e o handle vive e morre no LRU, como o local."""
    if getattr(caminho, "eh_remoto", False):
        return
    chave = str(caminho)
    with _lock:
        item = _zips.pop(chave, None)
        aninhados = [k for k in _zips_aninhados if k[0] == chave]
        for k in aninhados:
            zf, faixa, _ = _zips_aninhados.pop(k)
            zf.close()
            _fechar_faixa(faixa)
        tars = [k for k in _tars_aninhados if k[0] == chave]
        for k in tars:
            tf, faixa, _, _ = _tars_aninhados.pop(k)
            tf.close()
            _fechar_faixa(faixa)
    if item is not None:
        item[0].close()


def obter_conteudo(chave: tuple) -> bytes | None:
    """Bytes já cacheados para `chave` (renova o TTL), ou None se não estiver em cache."""
    agora = time.monotonic()
    with _lock:
        _purgar_conteudo(agora)
        item = _conteudo.get(chave)
        if item is None:
            return None
        dados, _ = item
        _conteudo[chave] = (dados, agora)
        _conteudo.move_to_end(chave)
        return dados


def guardar_conteudo(chave: tuple, dados: bytes) -> None:
    """Guarda `dados` em cache sob `chave`, se não ultrapassar o limite por item."""
    global _conteudo_bytes_total
    if len(dados) > CONTEUDO_ITEM_TAMANHO_MAX:
        return
    agora = time.monotonic()
    with _lock:
        _purgar_conteudo(agora)
        anterior = _conteudo.pop(chave, None)
        if anterior is not None:
            _conteudo_bytes_total -= len(anterior[0])
        _conteudo[chave] = (dados, agora)
        _conteudo.move_to_end(chave)
        _conteudo_bytes_total += len(dados)
        while _conteudo and (
            _conteudo_bytes_total > CONTEUDO_TOTAL_TAMANHO_MAX or len(_conteudo) > CONTEUDO_MAX_ITENS
        ):
            _, (dados_velhos, _) = _conteudo.popitem(last=False)
            _conteudo_bytes_total -= len(dados_velhos)


def ler_membro(caminho_ufdr: Path, caminho_zip: str, variante: str = "raw") -> bytes:
    """Lê um membro do .ufdr usando os dois caches acima. `variante` diferencia, para o
    mesmo membro, versões processadas (ex.: imagem já redimensionada) da versão crua —
    passe uma callable em `processar` (via ler_membro_processado) quando precisar disso."""
    chave = (str(caminho_ufdr), caminho_zip, variante)
    dados = obter_conteudo(chave)
    if dados is not None:
        return dados
    zf = obter_zip(caminho_ufdr)
    dados = zf.read(caminho_zip)  # levanta KeyError se o membro não existir
    guardar_conteudo(chave, dados)
    return dados
