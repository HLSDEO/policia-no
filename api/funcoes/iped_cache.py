"""Cache em memória para leitura de binários de um caso do IPED (extração Nuvem IPED).

Dois formatos de origem, conforme o `dataSourceDecoder` de cada item (achado real da Fase 0,
rodando o dumper contra data/SIP/iped_google/ — ver docs/extracao_iped.md):

- `iped.engine.task.ExportFileTask$SQLiteInputStreamFactory`: o binário foi extraído pelo
  IPED e vive comprimido (gzip) em storage/storage-N.db (tabela `t1`, chave = hash MD5
  maiúsculo), dentro da pasta módulo do caso. `dataSourcePath` vem como "iped/storage/
  storage-N.db" — uma string gravada em tempo de processamento, relativa à pasta que
  CONTÉM o módulo "iped/"; resolvida aqui removendo esse prefixo fixo e juntando à pasta
  módulo diretamente (funciona tanto para um caso "case_root/iped/" corretamente
  estruturado quanto para o exemplo deste repo, que já É a pasta módulo, sem o wrapper).
- `iped.utils.FileInputStreamFactory`: o item aponta direto pro arquivo original da fonte
  de dados (ex. um ZIP do Google Takeout ainda não expandido) — `dataSourcePath` é um
  caminho relativo com ".." (ex. "../Dados"), que resolve naturalmente a partir da pasta
  módulo com semântica normal de sistema de arquivos. Só funciona se a pasta de evidências
  original ainda estiver ao lado do caso processado — nem toda entrega "portátil" inclui
  isso (não está incluída no exemplo deste repo) — levanta FileNotFoundError quando ausente,
  mesmo espírito do ".ufdr ausente" do UFDR.

Mesmo padrão de cache com TTL do ufdr_cache.py: conexões SQLite abertas por caminho de
storage-N.db, e um cache pequeno de bytes já descomprimidos/lidos, por chave lógica."""

import gzip
import io
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from funcoes import desempenho_utils

TTL_SEGUNDOS = 60 * 60  # 1 hora sem uso e o item expira

# Mesmo dimensionamento do ufdr_cache (funcoes/desempenho_utils.py), pelo mesmo motivo: os
# valores fixos serviam a um analista de cada vez. Continuam valendo como piso.
CONEXOES_MAX = desempenho_utils.valor_int("CACHE_UFDR_ZIPS", 8)

CONTEUDO_ITEM_TAMANHO_MAX = 5 * 1024 * 1024  # 5 MB — acima disso não cacheia
CONTEUDO_TOTAL_TAMANHO_MAX = desempenho_utils.valor_int("CACHE_CONTEUDO_MB", 200) * 1024 * 1024
CONTEUDO_MAX_ITENS = max(500, CONTEUDO_TOTAL_TAMANHO_MAX // (100 * 1024))

_lock = threading.Lock()

_conexoes: "OrderedDict[str, tuple[sqlite3.Connection, float]]" = OrderedDict()
_conteudo: "OrderedDict[tuple, tuple[bytes, float]]" = OrderedDict()
_conteudo_bytes_total = 0


def _purgar_conexoes(agora: float) -> None:
    """Tira do cache as conexões paradas há mais de TTL_SEGUNDOS. NÃO chama close(): quem
    saiu do cache pode estar em uso por outra thread (uma importação em background lendo
    itens enquanto uma requisição HTTP serve um arquivo, por exemplo) e fechar debaixo dela
    dá 'sqlite3.InterfaceError: bad parameter or other API misuse'. Sem referência no cache,
    a conexão fecha sozinha quando o último usuário solta — só fechar_conexoes() fecha na
    marra, e ela só roda quando ninguém está lendo (antes de renomear a pasta do caso)."""
    expirados = [k for k, (_, ultimo) in _conexoes.items() if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        _conexoes.pop(k)


def _purgar_conteudo(agora: float) -> None:
    global _conteudo_bytes_total
    expirados = [k for k, (_, ultimo) in _conteudo.items() if agora - ultimo > TTL_SEGUNDOS]
    for k in expirados:
        dados, _ = _conteudo.pop(k)
        _conteudo_bytes_total -= len(dados)


def _obter_conexao(caminho: Path) -> sqlite3.Connection:
    """Devolve uma conexão sqlite3 aberta para `caminho` (reaproveitando do cache se já
    existir, renovando o TTL). check_same_thread=False porque o processamento em background
    e as requisições HTTP de leitura rodam em threads diferentes."""
    chave = str(caminho)
    agora = time.monotonic()
    with _lock:
        _purgar_conexoes(agora)
        item = _conexoes.get(chave)
        if item is not None:
            con, _ = item
            _conexoes[chave] = (con, agora)
            _conexoes.move_to_end(chave)
            return con

    con = sqlite3.connect(str(caminho), check_same_thread=False)
    with _lock:
        existente = _conexoes.get(chave)
        if existente is not None:
            con.close()
            con, _ = existente
            _conexoes[chave] = (con, agora)
            _conexoes.move_to_end(chave)
            return con
        _conexoes[chave] = (con, agora)
        _conexoes.move_to_end(chave)
        while len(_conexoes) > CONEXOES_MAX:
            _conexoes.popitem(last=False)  # sem close(): pode estar em uso — ver _purgar_conexoes
    return con


def obter_conteudo(chave: tuple) -> bytes | None:
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


def _raiz_caso(pasta_modulo: Path) -> Path:
    """Raiz do caso para confinamento: o módulo pode ser <caso>/iped (caso novo) ou o próprio
    <caso> (caso antigo) — mesmo critério de iped_utils._pasta_modulo_iped."""
    pasta_modulo = pasta_modulo.resolve()
    return pasta_modulo.parent if pasta_modulo.name == "iped" else pasta_modulo


def _dentro_do_caso(pasta_modulo: Path, caminho: Path) -> bool:
    """True se o caminho resolvido está dentro da raiz do caso."""
    raiz = _raiz_caso(pasta_modulo)
    resolvido = caminho.resolve()
    return resolvido == raiz or raiz in resolvido.parents


def _resolver_caminho_origem(pasta_modulo: Path, data_source_path: str) -> Path:
    """Resolve dataSourcePath (string gravada pelo IPED em tempo de processamento) contra a
    pasta módulo do caso hoje em disco. Remove um prefixo "iped/" literal quando presente
    (caso do storage — ver docstring do módulo); caminhos com ".." (caso de arquivo
    original) resolvem com semântica normal de sistema de arquivos.

    Confinado à raiz do caso (ver F2): dataSourcePath e idInDataSource vêm do índice Lucene
    de um caso artesanal, e sem o confine um "../..../etc/passwd" virava read_bytes fora do
    caso. Legítimo ("../Dados" do FileInputStream) continua dentro da raiz."""
    normalizado = data_source_path.replace("\\", "/").lstrip("/")
    partes = normalizado.split("/", 1)
    if partes[0] == "iped" and len(partes) == 2:
        candidato = pasta_modulo / partes[1]
    else:
        candidato = pasta_modulo / normalizado
    resolvido = candidato.resolve()
    raiz = _raiz_caso(pasta_modulo)
    if resolvido != raiz and raiz not in resolvido.parents:
        raise FileNotFoundError(f"Caminho do IPED fora do caso: {data_source_path!r}")
    return resolvido


def _no_remoto(pasta_modulo):
    """Caso IPED que mora no nó da delegacia (importação despachada — no_importacao.py):
    o storage-N.db é local à estação e NUNCA é copiado; o blob chega por GET /no/iped-blob.
    O marcador IpedRemoto entra no lugar do Path em _resolver_arquivo_iped."""
    from funcoes import no_ponte

    return pasta_modulo if isinstance(pasta_modulo, no_ponte.IpedRemoto) else None


def ler_blob(pasta_modulo: Path, data_source_path: str, hash_md5: str,
             id_in_data_source: str = "") -> bytes:
    """Lê o binário de um item do IPED. `pasta_modulo` é a pasta com index/storage/lib
    direto dentro (ver iped_utils._pasta_modulo_iped) — OU um no_ponte.IpedRemoto, quando o
    caso está no nó da delegacia (a leitura vai buscar o blob lá). Levanta FileNotFoundError
    se o binário não puder ser localizado."""
    remoto = _no_remoto(pasta_modulo)
    if remoto is not None:
        from funcoes import no_ponte

        dados = no_ponte.iped_blob(remoto.no, remoto.pasta, data_source_path, hash_md5,
                                   id_in_data_source)
        if dados is None:
            raise FileNotFoundError("Blob não encontrado no servidor da delegacia.")
        return dados
    if not data_source_path or not hash_md5:
        raise FileNotFoundError("Item sem ponteiro de binário.")

    chave = (str(pasta_modulo), data_source_path, hash_md5)
    dados = obter_conteudo(chave)
    if dados is not None:
        return dados

    dados = _carregar_blob(pasta_modulo, data_source_path, hash_md5, id_in_data_source)

    guardar_conteudo(chave, dados)
    return dados


def _carregar_blob(pasta_modulo: Path, data_source_path: str, hash_md5: str,
                   id_in_data_source: str = "") -> bytes:
    """Lê o blob sem passar pelo cache de conteúdo (usado por ler_blob e pelo streaming)."""
    normalizado = data_source_path.replace("\\", "/")
    if normalizado.startswith("iped/"):
        caminho_storage = _resolver_caminho_origem(pasta_modulo, data_source_path)
        if not caminho_storage.is_file():
            raise FileNotFoundError(f"storage do IPED não encontrado: {caminho_storage}")
        con = _obter_conexao(caminho_storage)
        linha = con.execute("SELECT data FROM t1 WHERE id = ?", (hash_md5,)).fetchone()
        if linha is None:
            raise FileNotFoundError("Blob não encontrado no storage do IPED.")
        return gzip.decompress(linha[0])
    pasta_origem = _resolver_caminho_origem(pasta_modulo, data_source_path)
    caminho_original = pasta_origem / id_in_data_source
    if not _dentro_do_caso(pasta_modulo, caminho_original):
        raise FileNotFoundError("Caminho do IPED fora do caso.")
    if not caminho_original.is_file():
        raise FileNotFoundError(
            f"Arquivo original não encontrado (pasta de evidências ausente?): {caminho_original}")
    return caminho_original.read_bytes()


def abrir_blob_stream(pasta_modulo: Path, data_source_path: str, hash_md5: str,
                      id_in_data_source: str = "", pedacos: int = 1024 * 1024):
    """Gerador que devolve o binário do item em pedaços DESCOMPRIMIDOS, sem carregar o
    conteúdo inteiro em memória — um vídeo grande de storage-N.db era descomprimido inteiro
    antes de ser servido (ler_blob), e agora só o pedaço atual existe na RAM. O blob comprimido
    ainda vem inteiro do SQLite (limitação do sqlite3), mas o vídeo descomprimido não.

    Levanta FileNotFoundError na PRIMEIRA iteração quando o binário não pode ser localizado."""
    remoto = _no_remoto(pasta_modulo)
    if remoto is not None:
        # o caso está no nó: o blob inteiro vem da estação (descomprimido lá) e é reemitido em
        # pedaços daqui — o streaming existe para não estourar a RAM, não para ler de vez o
        # storage remoto (a WAN já imporia isso de qualquer forma; o anexo abre, e é o que importa)
        from funcoes import no_ponte

        dados = no_ponte.iped_blob(remoto.no, remoto.pasta, data_source_path, hash_md5,
                                   id_in_data_source)
        if dados is None:
            raise FileNotFoundError("Blob não encontrado no servidor da delegacia.")
        for i in range(0, len(dados), pedacos):
            yield dados[i:i + pedacos]
        return

    if not data_source_path or not hash_md5:
        raise FileNotFoundError("Item sem ponteiro de binário.")

    normalizado = data_source_path.replace("\\", "/")
    if normalizado.startswith("iped/"):
        caminho_storage = _resolver_caminho_origem(pasta_modulo, data_source_path)
        if not caminho_storage.is_file():
            raise FileNotFoundError(f"storage do IPED não encontrado: {caminho_storage}")
        con = _obter_conexao(caminho_storage)
        linha = con.execute("SELECT data FROM t1 WHERE id = ?", (hash_md5,)).fetchone()
        if linha is None:
            raise FileNotFoundError("Blob não encontrado no storage do IPED.")
        with gzip.GzipFile(fileobj=io.BytesIO(linha[0])) as gz:
            while True:
                pedaco = gz.read(pedacos)
                if not pedaco:
                    break
                yield pedaco
        return

    pasta_origem = _resolver_caminho_origem(pasta_modulo, data_source_path)
    caminho_original = pasta_origem / id_in_data_source
    if not _dentro_do_caso(pasta_modulo, caminho_original):
        raise FileNotFoundError("Caminho do IPED fora do caso.")
    if not caminho_original.is_file():
        raise FileNotFoundError(
            f"Arquivo original não encontrado (pasta de evidências ausente?): {caminho_original}")
    with open(caminho_original, "rb") as fp:
        while True:
            pedaco = fp.read(pedacos)
            if not pedaco:
                break
            yield pedaco


# ---------------------------------------------------------------------------
# Miniatura pronta
#
# O IPED gera miniatura de imagem e de vídeo durante o processamento e guarda numa segunda
# tabela DO MESMO storage-N.db: `thumbs(id TEXT PRIMARY KEY, thumb BLOB)`, chaveada pelo MD5
# em maiúsculas (ver ThumbTask.java). O campo `hasThumb` do índice diz quais itens têm.
#
# Qual storage-N.db: `ExportFileTask.getStorageSuffix` usa `(hash[0] & 0xFF) >> 4` sobre os
# BYTES do MD5 — o que é exatamente o primeiro dígito hexadecimal do hash. Conferido no caso
# do repo: hash começando em "DB…" está em storage-13.db. Calcular assim (em vez de usar o
# `dataSourcePath` do item) importa porque item cujo binário veio do arquivo original
# (FileInputStreamFactory) não aponta para storage nenhum e mesmo assim tem miniatura.
#
# Diferente de ler_blob, aqui NÃO se levanta em falta: miniatura ausente é o caso normal
# (caso processado sem geração de thumb, item que não é mídia) e quem chama simplesmente
# gera a miniatura a partir do binário, como sempre fez.
# ---------------------------------------------------------------------------

def ler_thumb(pasta_modulo: Path, hash_md5: str | None) -> bytes | None:
    """Miniatura JPEG que o IPED já gerou para o item, ou None.

    Duas origens: a tabela `thumbs` do storage-N.db (ImageThumbTask) e, quando o item é VÍDEO
    (que não vai para a tabela `thumbs`), o arquivo em `view/H0/H1/HASH.jpg` que o
    VideoThumbTask grava (mesmo layout de Util.getFileFromHash)."""
    remoto = _no_remoto(pasta_modulo)
    if remoto is not None:
        from funcoes import no_ponte

        return no_ponte.iped_thumb(remoto.no, remoto.pasta, hash_md5) if hash_md5 else None
    if not hash_md5:
        return None
    chave_hash = hash_md5.strip().upper()
    if len(chave_hash) < 1:
        return None
    try:
        sufixo = int(chave_hash[0], 16)
    except ValueError:
        return None

    chave = (str(pasta_modulo), "thumb", chave_hash)
    dados = obter_conteudo(chave)
    if dados is not None:
        return dados or None  # bytes vazios = "já procurei e não tem" (ver abaixo)

    caminho = pasta_modulo / "storage" / f"storage-{sufixo}.db"
    if caminho.is_file():
        try:
            con = _obter_conexao(caminho)
            linha = con.execute("SELECT thumb FROM thumbs WHERE id = ?", (chave_hash,)).fetchone()
        except sqlite3.Error:
            # storage sem a tabela `thumbs` (caso de versão antiga do IPED) ou banco travado:
            # não é erro, é ausência de miniatura
            linha = None
    else:
        linha = None

    if linha is None or not linha[0]:
        # vídeo: o VideoThumbTask grava o quadro em view/H0/H1/HASH.jpg (ver PreviewConstants
        # e HTMLReportTask no iped-engine). Lido do arquivo direto — o vídeo em si nem é aberto.
        caminho_view = pasta_modulo / "view" / chave_hash[0] / chave_hash[1] / f"{chave_hash}.jpg"
        if caminho_view.is_file():
            try:
                dados = caminho_view.read_bytes()
            except OSError:
                dados = None
            if dados:
                guardar_conteudo(chave, dados)
                return dados

    # o IPED grava thumb de tamanho zero para marcar "tentei gerar e falhou"; guardar o vazio
    # no cache evita reconsultar o SQLite a cada célula da grade que rola pela tela
    dados = linha[0] if (linha and linha[0]) else b""
    guardar_conteudo(chave, dados)
    return dados or None


def fechar_conexoes(pasta_modulo: Path) -> None:
    """Fecha e descarta do cache toda conexão sqlite aberta para storage-N.db dentro de
    `pasta_modulo`. Preciso ANTES de renomear a pasta do caso (iped_utils._renomear_pasta_
    para_conta): num teste real, uma conexão cacheada (TTL de até 1h, ver topo do módulo)
    ainda aberta para um storage-N.db dentro da pasta bloqueou o rename da pasta pai com
    PermissionError — bind mount de data/ no Docker Desktop do Windows aplica semântica de
    arquivo aberto do NTFS, que o Linux dentro do container não teria."""
    prefixo = str(pasta_modulo)
    with _lock:
        chaves = [k for k in _conexoes if k.startswith(prefixo)]
        for k in chaves:
            con, _ = _conexoes.pop(k)
            con.close()
