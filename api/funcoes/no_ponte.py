"""Cliente central → nó descentralizado (ver docs/no_descentralizado.md e funcoes/no_utils.py).

Todas as funções recebem o `no` resolvido por no_utils (precisa de `tx_url_base` e `tx_token`) e o
caminho RELATIVO a data/ — o nó tem o seu próprio data(), e o mesmo esquema de subpastas por
delegacia vale dos dois lados. Nada aqui conhece banco: é a borda HTTP pura.

Três capacidades, na ordem de grandeza do que transportam:

- `calcular_hashes` e `parse_documento`: o nó faz o trabalho pesado LOCAL (hashear dezenas de GB,
  abrir o PDF/XLSX) e devolve só o resultado — é a razão de ser do nó;
- `ler_faixa` / `abrir_remoto`: leitura por faixa (HTTP Range) de um arquivo da pasta do nó. O
  `ArquivoRemoto` é seekável e vai direto para `zipfile.ZipFile(...)` — é assim que o central lê o
  `.ufdr` que mora no nó sem nunca transportar o arquivo inteiro;
- `baixar` / `garantir_local`: espelha um arquivo pequeno (documentos soltos) no cache do central
  quando o fluxo exige o arquivo em disco local (reextração, aba "Original").

Todo erro de rede vira `NoIndisponivel` com mensagem em português — quem chama decide entre falhar
a operação (extração) ou degradar para o comportamento de sempre (central sem nó).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import struct
import tempfile
import threading
import time
from pathlib import Path

import requests

from funcoes.organizacao_arquivos import destino_em_dados

logger = logging.getLogger(__name__)

# Timeout de conexão curto: nó offline tem que ser descoberto em segundos, não no timeout global.
_TIMEOUT_CONEXAO = 10.0
# Leitura: hash/parse rodem MINUTOS no nó (arquivo grande em disco local) — o read timeout é
# generoso e vem do .env (NO_TIMEOUT_LEITURA_SEGUNDOS) para o admin ajustar sem rebuild.
_TIMEOUT_LEITURA_PADRAO = 600.0

# Bloco do ArquivoRemoto: o zipfile faz leituras locais (cabeçalhos, membros) — 4 MiB por request
# limita o tráfego desperdiçado sem virar gotejamento de Round-trip.
_BLOCO = 4 * 1024 * 1024

# Timeout da SÓ-PRESENÇA (X-Tamanho com limite=0): é um cabeçalho, não o arquivo — o que atrasa
# aqui é a fila do túnel por trás de uma leitura grande, e a resposta velha não pode travar a
# tela: a listagem/poll degrada para "presença desconhecida" e a página abre.
_TIMEOUT_PROBADA = (_TIMEOUT_CONEXAO, 10.0)


class NoIndisponivel(RuntimeError):
    """O nó não respondeu (fora do ar, rede, token trocado). As rotas traduzem em 503 com
    mensagem dizendo QUAL delegacia não alcança o nó — sem isso o analista vê um 500 seco."""


def _timeout_leitura() -> float:
    import os
    try:
        return max(30.0, float(os.getenv("NO_TIMEOUT_LEITURA_SEGUNDOS") or _TIMEOUT_LEITURA_PADRAO))
    except ValueError:
        return _TIMEOUT_LEITURA_PADRAO


def _url(no: dict, rota: str) -> str:
    """Para onde uma chamada central→nó vai. Com o túnel NAT aberto (DT_TUNEL_ATIVO fresco — ver
    docs/no_descentralizado.md, "túnel NAT"), o caminho é o serviço `tunel` do próprio compose,
    que desce o pedido pelo WebSocket que o nó mantém; sem túnel, a `TX_URL_BASE` de sempre.

    O relay expõe a MESMA superfície (`/t/<id>/no/...` = `/no/...` do nó), então nada aqui embaixo
    (streaming, Range, upload repassado) precisa saber por qual cano a chamada passou."""
    import os

    if no.get("tunel_ativo"):
        base = (os.getenv("TUNEL_BASE_URL") or "http://tunel:3001").rstrip("/")
        return f"{base}/t/{no['id_servidor']}{rota}"
    return f"{no['tx_url_base'].rstrip('/')}{rota}"


def _headers(no: dict) -> dict:
    return {"Authorization": f"Bearer {no['tx_token']}"}


def _get(no: dict, rota: str, **kw) -> requests.Response:
    kw.setdefault("headers", _headers(no))
    kw.setdefault("timeout", (_TIMEOUT_CONEXAO, _timeout_leitura()))
    try:
        return requests.get(_url(no, rota), **kw)
    except requests.RequestException as exc:
        raise NoIndisponivel(f"Não consegui falar com o servidor {no['no_servidor']}: {exc}") from exc


def _post(no: dict, rota: str, json_body: dict, **kw) -> requests.Response:
    kw.setdefault("headers", _headers(no))
    kw.setdefault("timeout", (_TIMEOUT_CONEXAO, _timeout_leitura()))
    try:
        return requests.post(_url(no, rota), json=json_body, **kw)
    except requests.RequestException as exc:
        raise NoIndisponivel(f"Não consegui falar com o servidor {no['no_servidor']}: {exc}") from exc


def iniciar_extracao_remota(no: dict, id_extracao: int, no_arquivo: str,
                            modelo_transcricao: str | None = None,
                            transcrever_video: bool = False,
                            numero_inquerito: str | None = None,
                            geracao: int = 0) -> dict:
    """DELEGA a extração ao nó (docs/no_descentralizado.md, "nó extrai"): o nó abre o próprio
    arquivo na própria CPU e devolve os dados pelo canal de entrega. 404 = o arquivo não está
    mais lá; qualquer outra falha de transporte é NoIndisponivel (o despachante marca erro).
    numero_inquerito: destino do organize na estação (data/<delegacia>/<inquérito>/CELULAR/).
    geracao: número do claim (NU_GERACAO) — o nó o devolve em cada lote/estado; divergindo
    do atual no central, a passada é a de um despacho substituído e não escreve mais."""
    res = _post(no, "/no/extrair", {
        "id_extracao": id_extracao,
        "no_arquivo": no_arquivo,
        "modelo_transcricao": modelo_transcricao,
        "transcrever_video": bool(transcrever_video),
        "numero_inquerito": numero_inquerito,
        "geracao": int(geracao or 0),
    }, timeout=(_TIMEOUT_CONEXAO, 60))
    if res.status_code == 404:
        raise FileNotFoundError((res.json() or {}).get("detail", "arquivo não encontrado no nó"))
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def iniciar_transcricao_remota(no: dict, id_extracao: int, no_arquivo: str,
                               modelo: str, incluir_video: bool,
                               tarefas: list[dict] | None = None) -> dict:
    """DELEGA a transcrição dos áudios de uma extração à estação (POST /no/transcrever): o
    faster-whisper vive no nó, sobre o disco dele. O progresso (fase/progresso/batida), os textos
    ({"t":"transcricao"}) e o fecho ({"t":"transcricao_fim"}) voltam pelos MESMOS callbacks de
    estado da extração (rotas/no_central.py → canal_extracao.aplicar_estado).
    tarefas: [{prefixo, caminho_zip}] deduplicada por binário — quem tem o banco é o central,
    e o nó não consulta nada: recebe a lista pronta e roda o whisper no .ufdr do próprio disco."""
    res = _post(no, "/no/transcrever", {
        "id_extracao": id_extracao,
        "no_arquivo": no_arquivo,
        "modelo": modelo,
        "incluir_video": bool(incluir_video),
        "tarefas": tarefas or [],
    }, timeout=(_TIMEOUT_CONEXAO, 60))
    if res.status_code == 404:
        detail = ""
        try:
            detail = (res.json() or {}).get("detail", "")
        except Exception:
            pass
        if detail:
            raise FileNotFoundError(detail)
        raise NoIndisponivel(
            f"O servidor {no['no_servidor']} ainda não suporta transcrição remota "
            "(/no/transcrever) — atualize o nó.")
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def importar_remoto(no: dict, id_extracao: int, tp: str, pasta: str,
                    opcoes: dict | None = None, geracao: int = 0) -> dict:
    """DELEGA a importação de RIF/SIMBA/IPED ao nó (POST /no/importar — docs/no_descentralizado.md,
    "nó importa"): a pasta mora na estação, o parse roda lá e os dados voltam pelo CANAL de
    entrega (mesmos lotes/estados da extração .ufdr, chaveados pelo id_extracao-ponte criado pelo
    central em funcoes/no_importacao.py). 404 com detalhe = a pasta não está mais lá; 404 sem =
    nó velho que ainda não fala /no/importar (mensagem honesta, não erro de rede genérico)."""
    res = _post(no, "/no/importar", {
        "tp": tp,
        "id_extracao": id_extracao,
        "pasta": pasta,
        "opcoes": opcoes or {},
        "geracao": int(geracao or 0),
    }, timeout=(_TIMEOUT_CONEXAO, 60))
    if res.status_code == 404:
        detail = ""
        try:
            detail = (res.json() or {}).get("detail", "")
        except Exception:
            pass
        if detail:
            raise FileNotFoundError(detail)
        raise NoIndisponivel(
            f"O servidor {no['no_servidor']} ainda não suporta importação remota "
            "(/no/importar) — atualize o nó.")
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def _erro_no(res: requests.Response, no: dict) -> None:
    """401/403/404/500 do nó sempre viram mensagem com o nome do nó — 100 nós sem rótulo no log
    é indecidível."""
    detalhe = ""
    try:
        detalhe = (res.json() or {}).get("detail", "")
    except Exception:
        pass
    raise NoIndisponivel(f"O servidor {no['no_servidor']} respondeu {res.status_code}"
                         + (f": {detalhe}" if detalhe else "."))


# ------------------------------------------------------------------ presença

def esta_no_ar(no: dict) -> bool:
    """Probe barato (GET /no/saude, sem auth) usado pela tela de servidores e pelo badge de
    disponibilidade. Qualquer erro = False; nunca levanta. Atravessa o túnel quando há um —
    /no/saude não exige token, e o relay repassa qualquer /no/* igual."""
    try:
        return requests.get(_url(no, "/no/saude"),
                            timeout=(_TIMEOUT_CONEXAO, 5)).status_code == 200
    except requests.RequestException:
        return False


def listar(no: dict, subpasta: str | None, tipo: str) -> list[dict]:
    """O que tem na data() do nó (GET /no/listar): "ufdr", "diversos", "rif" ou "simba", os
    tipos de seletor da tela. É a resposta do nó que o seletor da web mostra quando a delegacia
    tem nó — o arquivo que o usuário escolhe é o que está na pasta da própria delegacia."""
    params = {"tipo": tipo}
    if subpasta:
        params["subpasta"] = subpasta
    res = _get(no, "/no/listar", params=params)
    if res.status_code >= 400:
        _erro_no(res, no)
    return (res.json() or {}).get("arquivos") or []


def embed(no: dict, textos: list[str]) -> list[list[float]]:
    """Gera embeddings NA ESTAÇÃO (POST /no/embed): o central não chama mais o Ollama/API de
    embeddings — quem carrega o modelo é o nó da delegacia. O nó devolve {"vetores": [[...]]}
    na mesma ordem dos textos. Protocolo novo: exige o nó em versão que suporte /no/embed
    (404 vira mensagem clara, não um erro genérico de rede)."""
    res = _post(no, "/no/embed", {"textos": textos})
    if res.status_code == 404:
        raise NoIndisponivel(
            f"O servidor {no['no_servidor']} ainda não suporta geração de embeddings "
            "(/no/embed) — atualize o nó para liberar a busca semântica.")
    if res.status_code >= 400:
        _erro_no(res, no)
    vetores = (res.json() or {}).get("vetores") or []
    if len(vetores) != len(textos):
        raise NoIndisponivel(f"O servidor {no['no_servidor']} devolveu {len(vetores)} vetores "
                             f"para {len(textos)} textos.")
    return vetores


# ------------------------------------------------------------------ Nuvem IPED no nó

class IpedRemoto:
    """Marcador de "o caso IPED mora no nó": viaja no lugar do Path da pasta-módulo nos
    chamados de iped_cache (ler_blob/abrir_blob_stream/ler_thumb), que o reconhece e pede o
    conteúdo ao nó em vez de abrir o storage-N.db local. Guarda o nó e o caminho RELATIVO da
    pasta do caso (IPD_TB01.NO_PASTA fresco, que sobrevive ao rename do fechamento)."""

    __slots__ = ("no", "pasta")

    def __init__(self, no: dict, pasta: str):
        self.no = no
        self.pasta = pasta

    def __repr__(self) -> str:
        return f"<IpedRemoto {self.no.get('no_servidor')} {self.pasta}>"


def _no_fonte(no: dict, rota: str, params: dict) -> bytes | None:
    """GET autenticado que devolve bytes ou None no 404 — é a pergunta "tem esse blob aí?"."""
    try:
        res = _get(no, rota, params=params, timeout=(_TIMEOUT_CONEXAO, 300))
    except NoIndisponivel:
        raise
    if res.status_code == 404:
        return None
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.content


def iped_blob(no: dict, pasta: str, origem: str, hash_md5: str, id_ds: str) -> bytes | None:
    """Conteúdo de um item do caso IPED que está no NÓ (GET /no/iped-blob): o storage-N.db é
    local à estação e nunca é copiado para o central — o anexo que abre na tela vem daqui,
    descompactado no nó (o wire é gzip quando ajuda, mas a API deste lado é o bytes puro)."""
    return _no_fonte(no, "/no/iped-blob",
                     {"pasta": pasta, "origem": origem, "md5": hash_md5, "id": id_ds})


def iped_thumb(no: dict, pasta: str, hash_md5: str) -> bytes | None:
    """Miniatura do caso IPED no nó (GET /no/iped-thumb) — mesmo storage, chaveada pelo MD5."""
    return _no_fonte(no, "/no/iped-thumb", {"pasta": pasta, "md5": hash_md5})


def arquivo_existe(no: dict, relativo: str) -> bool:
    """O arquivo está na pasta data() do nó? (a mesma pergunta que o central faz com is_file())."""
    res = _get(no, "/no/arquivo", params={"caminho": relativo, "inicio": 0, "limite": 1})
    if res.status_code == 404:
        return False
    if res.status_code >= 400:
        _erro_no(res, no)
    return True


# ------------------------------------------------------------------ leitura por faixa

def ler_faixa(no: dict, relativo: str, inicio: int, limite: int) -> bytes:
    """Lê `limite` bytes a partir de `inicio` (GET /no/arquivo — 206 parcial do próprio nó).

    Uma retentativa em falha de transporte (5xx do relay — o túnel fecha conexões no meio de
    leituras grandes sob carga — ou erro de rede): é um GET idempotente de poucas dezenas de MB
    no máximo, e o leitor por cima (zipfile) não tem como retomar a metade de um bloco."""
    ultimo: NoIndisponivel | None = None
    for tentativa in range(2):
        try:
            res = _get(no, "/no/arquivo",
                       params={"caminho": relativo, "inicio": inicio, "limite": limite})
        except NoIndisponivel as exc:
            ultimo = exc
            time.sleep(1.5)
            continue
        if res.status_code == 404:
            raise FileNotFoundError(relativo)
        if res.status_code == 416:
            return b""  # além do EOF: o servidor fala 416, o cliente lê como fim
        if res.status_code >= 500:
            ultimo = None  # deixa _erro_no montar a mensagem; na 2ª vez ele levanta
            if tentativa == 0:
                time.sleep(1.5)
                continue
        if res.status_code >= 400:
            _erro_no(res, no)
        return res.content
    raise ultimo or NoIndisponivel(f"Não consegui ler {relativo} do servidor {no['no_servidor']}")


def tamanho(no: dict, relativo: str, timeout: tuple | None = None) -> int:
    """Tamanho do arquivo no nó (cabeçalho X-Tamanho do /no/arquivo com limite=0).

    `timeout` vem de quem chama porque a resposta é só um cabeçalho: quando o que atrasa é a fila
    atrás de uma leitura grande num link fino (o nó entregando uma extração), a checagem de
    presença — badge da listagem, poll — usa o _TIMEOUT_PROBADA curto e degrada, enquanto o
    leitor de verdade (ArquivoRemoto) pode esperar o timeout generoso de sempre."""
    kw = {"timeout": timeout} if timeout else {}
    res = _get(no, "/no/arquivo", params={"caminho": relativo, "inicio": 0, "limite": 0}, **kw)
    if res.status_code == 404:
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        _erro_no(res, no)
    try:
        return int(res.headers.get("X-Tamanho", "-1"))
    except ValueError:
        return -1


class ArquivoRemoto(io.RawIOBase):
    """View seekável de um arquivo que mora na pasta data() de um nó — vai cru no
    `zipfile.ZipFile()`: o ZIP lê o diretório central no FIM e depois só os membros que o parser
    pede, tudo por faixa. O `.ufdr` de dezenas de GB nunca atravessa a rede inteiro.

    Cache de UM bloco: os leitores de ZIP fazem leituras próximas entre si (cabeçalho
    local + dados do membro), e o padrão sequencial do iterparse do report.xml acerta o cache
    quase sempre. Sem pretensão de LRU — um handle por extração em andamento, como o ZipFile local.

    `indice=(ini, caminho)` ativa a camada do índice local (ver `_construir_indice`): leituras a
    partir do offset `ini` — o diretório central do zip, imutável — saem de um arquivo em disco no
    central em vez da rede. É o que abre a galeria de um .ufdr de 300 mil entradas num link de
    delegacia: o diretório central custa dezenas de MB UMA vez, não a cada reabertura do handle.
    """

    def __init__(self, no: dict, relativo: str, tamanho_total: int | None = None,
                 indice: "tuple[int, Path] | None" = None) -> None:
        super().__init__()
        self.no = no
        self.relativo = relativo
        self._pos = 0
        self._bloco_ini = -1
        self._bloco: bytes = b""
        self._tam = tamanho_total if tamanho_total is not None else tamanho(no, relativo)
        if self._tam < 0:
            raise FileNotFoundError(relativo)
        self._indice_ini: int | None = None
        self._indice_fh = None
        if indice is not None:
            ini, caminho_idx = indice
            if 0 <= ini < self._tam:
                try:
                    self._indice_fh = open(caminho_idx, "rb")
                    self._indice_ini = ini
                except OSError:
                    self._indice_fh = None

    # -- interface io -----------------------------------------------------------

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            novo = offset
        elif whence == io.SEEK_CUR:
            novo = self._pos + offset
        else:
            novo = self._tam + offset
        self._pos = max(0, min(novo, self._tam))
        return self._pos

    def _carregar(self, pos: int, sugestao: int | None = None) -> None:
        if self._bloco_ini <= pos < self._bloco_ini + len(self._bloco):
            return
        # O tamanho do pedido orienta o bloco: cabeçalhos minúsculos e imagens de 200 KB não
        # pagam 4 MiB de link fino por leitura; o teto continua 4 MiB para não virar gotejamento.
        alvo = _BLOCO
        if sugestao is not None and 0 < sugestao < _BLOCO:
            alvo = max(_BLOCO_MIN, sugestao)
        fim = min(pos + alvo, self._tam)
        if self._indice_fh is not None and pos >= self._indice_ini:
            # o .idx guarda 8 bytes de cabeçalho (offset inicial) antes da cauda em si
            self._indice_fh.seek(8 + pos - self._indice_ini)
            self._bloco = self._indice_fh.read(fim - pos)
        else:
            self._bloco = ler_faixa(self.no, self.relativo, pos, fim - pos)
        self._bloco_ini = pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._tam - self._pos
        size = min(size, self._tam - self._pos)
        if size <= 0:
            return b""
        saida = bytearray()
        while len(saida) < size:
            self._carregar(self._pos, size - len(saida))
            fatia = self._bloco[self._pos - self._bloco_ini:]
            if not fatia:  # nó devolveu menos que o pedido (arquivo encolheu?) — para, sem loop
                break
            falta = size - len(saida)
            saida += fatia[:falta]
            self._pos += len(fatia[:falta])
        return bytes(saida)

    def readinto(self, b) -> int:  # type: ignore[override]
        dados = self.read(len(b))
        b[: len(dados)] = dados
        return len(dados)

    def close(self) -> None:
        self._bloco = b""
        if self._indice_fh is not None:
            try:
                self._indice_fh.close()
            except OSError:
                pass
            self._indice_fh = None
        super().close()


# ------------------------------------------------------------------ índice local do ZIP remoto
#
# O zipfile lê o DIRETÓRIO CENTRAL no fim do .ufdr antes de servir o primeiro membro — num ZIP
# de trezentas mil entradas são dezenas de megabytes. Pelo link fino da delegacia isso custava
# MINUTOS a cada reabertura do handle (TTL, redeploys e as duas réplicas reabrem cada uma), e a
# galeria nunca abria. O índice é IMUTÁVEL enquanto o .ufdr não muda: desce UMA vez por
# (nó, caminho, tamanho) para um arquivo em disco no central e serve dali — conteúdo dos
# membros continua por faixa, ao vivo, como sempre.
#
# A descida roda em thread daemon única por chave (duas requisições não baixam o mesmo índice;
# a conexão que chega enquanto constrói recebe NoIndisponivel com mensagem de "tente de novo",
# em vez de travar uma thread da pool por minutos), recomeça do ponto em que parou (o link cai
# no meio dos dezenas de MB) e é ignorada para arquivos/índices fora do perfil.

_INDICE_MIN_ARQUIVO = 32 * 1024 * 1024      # abaixo disso o índice não paga o round-trip
_INDICE_MIN_CD = 512 * 1024                 # diretório central pequeno: ler da rede mesmo
_INDICE_LIMITE_CD = 512 * 1024 * 1024       # acima disso não cacheia (fora do perfil)
_INDICE_ITENS_MAX = 24                      # .idx mantidos em disco (LRU por mtime)
_INDICE_ESPERA_MAX = 20.0                   # espera máxima de quem chegou durante a construção
_FAIXA_PEDACO = 6 * 1024 * 1024             # < 8 MiB: o teto do /no/arquivo corta em silêncio
_INDICE_TENTATIVAS = 40                     # retomas do .parcial (link que corta em janelas)
_BLOCO_MIN = 256 * 1024

_indice_lock = threading.Lock()
_indice_builds: dict = {}
_indice_falhas: dict = {}


def _pasta_indice() -> Path:
    # /app/.cache/ufdr-indice é bind mount no compose do central: o índice de dezenas de MB
    # sobrevivia a nada — cada deploy recriava o container e o /tmp ia junto, e as duas réplicas
    # ainda reconstruíam a mesma coisa em paralelo (2× o link fino). O caminho persistente é o
    # padrão; /tmp fica como reserva quando não se pode escrever nele (nó, dev, pasta cheia).
    base = os.getenv("NO_INDICE_CACHE_DIR")
    if not base:
        base = "/app/.cache/ufdr-indice"
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            teste = Path(base) / ".escreve"
            teste.write_bytes(b"")
            teste.unlink()
        except OSError:
            base = os.path.join(tempfile.gettempdir(), "ufdr-indice")
    pasta = Path(base)
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _nome_indice(no: dict, relativo: str, tam: int) -> str:
    chave = f"{no.get('id_servidor')}|{relativo}|{tam}".encode("utf-8")
    return hashlib.sha256(chave).hexdigest()[:32] + ".idx"


def _ler_cabecalho_indice(destino: Path, tam: int):
    """(ini, destino) se o .idx no disco bate com o arquivo de hoje; None (e apaga) se não."""
    try:
        stat = destino.stat()
        with open(destino, "rb") as f:
            ini = struct.unpack("<Q", f.read(8))[0]
        if 0 <= ini < tam and stat.st_size - 8 == tam - ini:
            return (ini, destino)
        destino.unlink(missing_ok=True)
    except (OSError, struct.error):
        pass
    return None


def _stream_faixa(no: dict, relativo: str, inicio: int, limite: int):
    """GET /no/arquivo em streaming de verdade (ler_faixa guarda tudo na memória — o índice tem
    dezenas de MB), em janelas de _FAIXA_PEDACO: o nó serve no máximo _LIMITE_FAIXA_BYTES por
    pedido e CORTA EM SILÊNCIO quem pedir mais (200 truncado) — pedir em janelas menores que o
    teto e conferir a entrega é o único jeito de não gravar índice incompleto. Timeout de leitura
    solto: no link fino a janela inteira pode levar um minuto e é o tráfego que existe para
    existir, um vez só."""
    entregue = 0
    while entregue < limite:
        n = min(_FAIXA_PEDACO, limite - entregue)
        res = requests.get(_url(no, "/no/arquivo"),
                           params={"caminho": relativo, "inicio": inicio + entregue, "limite": n},
                           headers=_headers(no),
                           stream=True,
                           timeout=(_TIMEOUT_CONEXAO, None))
        try:
            if res.status_code == 404:
                raise FileNotFoundError(relativo)
            if res.status_code == 416:
                break  # EOF no meio (arquivo encolheu?): acabou, quem valida é quem grava
            if res.status_code >= 400:
                _erro_no(res, no)
            veio = 0
            for pedaco in res.iter_content(chunk_size=1024 * 1024):
                veio += len(pedaco)
                yield pedaco
            if veio < n:
                break  # cano cortou em silêncio (túnel, teto do nó): o .parcial retoma daqui
            entregue += veio
        finally:
            res.close()


def _localizar_cd(no: dict, relativo: str, tam: int):
    """Lê a cauda do arquivo e devolve (offset, tamanho) do diretório central — Zip64 incluso
    (324 mil entradas passam de 65535 e os campos de 32 bits saturam em 0xFFFFFFFF)."""
    cauda_ini = max(0, tam - (64 * 1024 + 655))
    cauda = ler_faixa(no, relativo, cauda_ini, tam - cauda_ini)
    i = cauda.rfind(b"PK\x05\x06")
    if i < 0 or len(cauda) - i < 22:
        return None
    _, _, ent_disco, ent_total, cd_tam, cd_off, _ = struct.unpack("<HHHHLLH", cauda[i + 4:i + 22])
    if cd_tam == 0xFFFFFFFF or cd_off == 0xFFFFFFFF or ent_disco == 0xFFFF or ent_total == 0xFFFF:
        loc = i - 20  # o locator Zip64 cola antes do EOCD
        if loc < 0 or cauda[loc:loc + 4] != b"PK\x06\x07":
            return None
        z64_off = struct.unpack("<Q", cauda[loc + 8:loc + 16])[0]
        rec = ler_faixa(no, relativo, z64_off, min(56, tam - z64_off))
        if len(rec) < 56 or rec[:4] != b"PK\x06\x06":
            return None
        cd_tam = struct.unpack("<Q", rec[40:48])[0]
        cd_off = struct.unpack("<Q", rec[48:56])[0]
    if cd_off <= 0 or cd_tam <= 0 or cd_off + cd_tam > tam:
        return None
    return (cd_off, cd_tam)


def _construir_indice(no: dict, relativo: str, tam: int, nome: str):
    pasta = _pasta_indice()
    destino = pasta / nome
    parcial = pasta / f"{nome}.{os.getpid()}.parcial"
    cd = _localizar_cd(no, relativo, tam)
    if cd is None:
        raise ValueError("diretório central não localizado no fim do arquivo")
    cd_off, cd_tam = cd
    if cd_tam < _INDICE_MIN_CD or cd_tam > _INDICE_LIMITE_CD:
        raise ValueError(f"diretório central fora do perfil ({cd_tam} bytes)")
    limite = tam - cd_off
    feito = 0
    if parcial.is_file():
        with open(parcial, "rb") as f:
            cab = f.read(8)
        feito = max(0, parcial.stat().st_size - 8)
        if cab != struct.pack("<Q", cd_off) or feito > limite:
            parcial.unlink(missing_ok=True)
            feito = 0
    try:
        with open(parcial, "ab" if feito else "wb") as f:
            if not feito:
                f.write(struct.pack("<Q", cd_off))
            if limite > feito:
                for pedaco in _stream_faixa(no, relativo, cd_off + feito, limite - feito):
                    f.write(pedaco)
            f.flush()
        if parcial.stat().st_size - 8 != limite:
            # stream terminou antes da hora (túnel, teto do nó, queda de link): o .parcial é o
            # ponto de recomeço — erra agora para o chamador tentar de novo em vez de publicar
            # um índice truncado que o zipfile leria como lixo
            raise OSError("transferência incompleta (%d/%d bytes)"
                          % (parcial.stat().st_size - 8, limite))
        os.replace(parcial, destino)
    except Exception:
        # o .parcial fica de propósito: é o ponto de recomeço da próxima tentativa
        raise
    return (cd_off, destino)


def _podar_indices(pasta: Path) -> None:
    try:
        indices = sorted(pasta.glob("*.idx"), key=lambda p: p.stat().st_mtime)
        for velho in indices[:-_INDICE_ITENS_MAX] if len(indices) > _INDICE_ITENS_MAX else []:
            velho.unlink(missing_ok=True)
        corte = time.time() - 24 * 3600
        for sobra in pasta.glob("*.parcial"):
            try:
                if sobra.stat().st_mtime < corte:
                    sobra.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _lock_indice(pasta: Path, nome: str):
    """Cadeado entre PROCESSOS para a construção de um índice: o single-flight em memória só vale
    dentro de uma réplica, e no mount compartilhado api-1 + api-2 desceriam os mesmos 50 MB pelo
    link fino ao mesmo tempo. Quem chega e vê o cadeado fresco não constrói — responde
    "construindo" e volta já. Cadeado velho (>30 min) é cadáver de container recriado: vale
    assumir a obra."""
    cadeado = pasta / (nome + ".lock")
    for _ in range(2):
        try:
            fd = os.open(cadeado, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - cadeado.stat().st_mtime > 1800:
                    cadeado.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError:
            return None  # sem como criar o cadeado: segue (o single-flight por processo vale)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return cadeado
    return None


def _rosca_indice(no: dict, relativo: str, tam: int, nome: str, estado: dict,
                  cadeado: Path | None = None) -> None:
    # um link fino de delegacia corta a descida de dezenas de MB em pedaços — a thread tenta e
    # retoma o .parcial até completar (ou esgotar as tentativas); quem chega no meio recebe
    # "tente de novo", ninguém espera os minutos inteiros preso numa requisição
    try:
        for tentativa in range(_INDICE_TENTATIVAS):
            try:
                estado["resultado"] = _construir_indice(no, relativo, tam, nome)
                _podar_indices(_pasta_indice())
                break
            except FileNotFoundError:
                estado["resultado"] = None
                break
            except Exception as exc:
                estado["resultado"] = None
                logger.info("Índice local de %s (tentativa %d/%d): %s",
                            relativo, tentativa + 1, _INDICE_TENTATIVAS, exc)
                time.sleep(3.0)
        else:
            with _indice_lock:
                _indice_falhas[nome] = time.monotonic() + 60.0  # não martela o nó a cada pedido
    finally:
        with _indice_lock:
            _indice_builds.pop(nome, None)
        if cadeado is not None:
            try:
                cadeado.unlink(missing_ok=True)
            except OSError:
                pass
        estado["evento"].set()


class _IndiceConstruindo:
    pass


INDICE_CONSTRUINDO = _IndiceConstruindo()


def montar_indice(no: dict, relativo: str, tam: int | None = None):
    """Índice local do ZIP remoto pronto: devolve (offset, Path); None se não se aplica (arquivo
    pequeno, índice fora do perfil, falha recente — segue lendo da rede); ou INDICE_CONSTRUINDO
    se a descida está em andamento — quem chama degrada com mensagem em vez de esperar minutos."""
    if tam is None:
        try:
            tam = tamanho(no, relativo, timeout=_TIMEOUT_PROBADA)
        except (NoIndisponivel, FileNotFoundError):
            return None
    if not tam or tam < 0 or tam < _INDICE_MIN_ARQUIVO:
        return None
    nome = _nome_indice(no, relativo, tam)
    pronto = _ler_cabecalho_indice(_pasta_indice() / nome, tam)
    if pronto is not None:
        return pronto
    with _indice_lock:
        estado = _indice_builds.get(nome)
        if estado is None and time.monotonic() < _indice_falhas.get(nome, 0.0):
            return None
    dono = estado is None
    if dono:
        estado = {"evento": threading.Event(), "resultado": None}
        _indice_builds[nome] = estado
    cadeado = None
    if dono:
        cadeado = _lock_indice(_pasta_indice(), nome)
        if cadeado is None:
            # outra réplica está descendo exatamente isto: não duplico o link fino
            with _indice_lock:
                _indice_builds.pop(nome, None)
            return INDICE_CONSTRUINDO
    if dono:
        threading.Thread(target=_rosca_indice, args=(no, relativo, tam, nome, estado, cadeado),
                         daemon=True, name=f"ufdr-indice-{nome[:8]}").start()
    if estado["evento"].wait(_INDICE_ESPERA_MAX):
        return estado["resultado"]
    return INDICE_CONSTRUINDO


def abrir_zip_remoto(no: dict, relativo: str, tamanho_total: int | None = None):
    """Abre o .ufdr do nó como ZIP (mesma interface do ufdr_zip.abrir no caminho feliz).

    O modo de recuperação de arquivo truncado (ZipTolerante) exige varredura sequencial de dezenas
    de GB — não existe por sobre a rede; um .ufdr truncado no nó tem que ser re-copiado lá, e a
    mensagem diz isso. Retorna um zipfile.ZipFile; quem chama fecha (with)."""
    import zipfile

    indice = montar_indice(no, relativo, tamanho_total)
    if indice is INDICE_CONSTRUINDO:
        raise NoIndisponivel(
            "O índice do arquivo no servidor da delegacia ainda está sendo preparado para leitura "
            "pelo link fino. Tente novamente em um instante — depois da primeira vez a abertura é "
            "imediata.")
    arq = ArquivoRemoto(no, relativo, tamanho_total, indice=indice)
    try:
        return zipfile.ZipFile(arq)
    except zipfile.BadZipFile as exc:
        arq.close()
        raise zipfile.BadZipFile(
            "O .ufdr no servidor da delegacia não é um ZIP legível (índice final ausente ou "
            "arquivo corrompido). A recuperação de cópia truncada só roda onde o arquivo está — "
            "recopie o arquivo na estação da delegacia.") from exc


# ------------------------------------------------------------------ o passaporte do arquivo remoto


class FonteRemota:
    """Um .ufdr que mora na pasta data() de um nó — o "passaporte" que os leitores aceitam no
    lugar do Path local (ver investigacao_utils.fonte_ufdr).

    A classe é deliberadamente parecida com um arquivo: `is_file()`, `stat().st_size`, e `str()`
    devolvendo uma chave estável ("no://<id_servidor>/<relativo>") — é esta string que vira a
    chave dos caches do ufdr_cache, e ela precisa ser a MESSA em toda requisição (dict novo do
    banco a cada consulta não serve de chave). `abrir_zip()`/`abrir_base()` produzem os handles
    que o ufdr_zip/ufdr_cache aceitam ler por faixa.

    O objeto precisa sobreviver a pickle: os processos do pool de transcrição (spawn) recebem a
    fonte dentro das tarefas, e aqui isso funciona porque só há dicts/strings/ints — nenhum handle
    vivo. O tamanho vem do cabeçalho X-Tamanho do nó e fica na instância: galerias não podem
    pagar um ROUND-TRIP por imagem só para saber o tamanho do arquivo."""

    eh_remoto = True

    def __init__(self, no: dict, relativo: str, tamanho: int | None = None,
                 timeout: tuple | None = None) -> None:
        self.no = no
        self.relativo = relativo
        self._tam = tamanho
        # timeout da SONDA de presença (None = o generoso de sempre): quem quer a resposta
        # rápido — badge de listagem, poll — passa o _TIMEOUT_PROBADA e degrada na falha
        self._timeout = timeout

    # -- cara de arquivo ---------------------------------------------------------

    def __str__(self) -> str:
        return f"no://{self.no.get('id_servidor')}/{self.relativo}"

    def __repr__(self) -> str:  # log legível — nunca vazar o token
        return f"<FonteRemota {self}>"

    def is_file(self) -> bool:
        return self.tamanho() is not None

    def stat(self):
        class _Stat:
            pass

        s = _Stat()
        s.st_size = self.tamanho() or 0
        return s

    def tamanho(self) -> int | None:
        if self._tam is None:
            try:
                self._tam = tamanho(self.no, self.relativo, timeout=self._timeout)
            except (NoIndisponivel, FileNotFoundError):
                return None
            if self._tam < 0:
                return None
        return self._tam

    # -- produção de handles -------------------------------------------------------

    def abrir_zip(self):
        return abrir_zip_remoto(self.no, self.relativo, self._tam)

    def abrir_base(self):
        return ArquivoRemoto(self.no, self.relativo, self._tam)


# ------------------------------------------------------------------ trabalho pesado no nó

def calcular_hashes(no: dict, relativo: str, algoritmos: tuple[str, ...] | None = None) -> dict:
    """MD5/SHA1/SHA256 calculados NO NÓ (POST /no/hash) — é a leitura completa do arquivo, e é
    exatamente ela que não faz sentido atravessar a WAN. Devolve o mesmo dict de
    hash_utils.calcular_hashes_arquivo (só os algoritmos pedidos; padrão: os três)."""
    corpo: dict = {"caminho": relativo}
    if algoritmos:
        corpo["algoritmos"] = list(algoritmos)
    res = _post(no, "/no/hash", corpo)
    if res.status_code == 404:
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def parse_documento(no: dict, relativo: str) -> dict:
    """Parse de documento (IRPF/IPL/IPEI/telefonia/documento genérico) NO NÓ.

    POST /no/parse devolve {"tipo": ..., "texto": ..., "dados": {...}} — o central recebe e faz
    apenas a GRAVAÇÃO (as funções extrair_* continuam donas do SQL; o que mudou de lado foi só a
    leitura do arquivo)."""
    res = _post(no, "/no/parse", {"caminho": relativo})
    if res.status_code == 404:
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def organizar(no: dict, relativo: str, numero_inquerito: str, categoria: str = "OUTROS",
              novo_nome: str | None = None) -> str | None:
    """Manda o NÓ organizar o arquivo dele: ele mesmo move para data/<delegacia>/<inquérito>/
    <categoria>/ na estação (a prova nunca viaja — docs/no_descentralizado.md, "só no nó").
    Aceita PASTA também (o pacote RIF é uma pasta) e `novo_nome` (o RIF_<número> que o central
    calcula depois de parsear). Devolve o caminho relativo novo (no data() do nó) ou None se
    nada foi movido."""
    corpo = {"caminho": relativo, "numero_inquerito": numero_inquerito, "categoria": categoria}
    if novo_nome:
        corpo["novo_nome"] = novo_nome
    res = _post(no, "/no/organizar", corpo)
    if res.status_code == 404:
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        _erro_no(res, no)
    return ((res.json() or {}).get("relativo") or None)


def stream(no: dict, relativo: str):
    """Streaming do arquivo inteiro do nó para o proxy de mídia do central (res.content NÃO —
    chunked de verdade). O chamador (rotas/no_proxy) repassa como StreamingResponse."""
    res = requests.get(_url(no, "/no/arquivo"),
                       params={"caminho": relativo},
                       headers=_headers(no),
                       stream=True,
                       timeout=(_TIMEOUT_CONEXAO, None))
    if res.status_code == 404:
        res.close()
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        res.close()
        _erro_no(res, no)
    return res


# ------------------------------------------------------------------ espelhamento (documentos)

def baixar(no: dict, relativo: str, destino: Path) -> None:
    """Copia o arquivo do nó para o destino local (atômico via .parcial). Usado por
    garantir_local — documentos que os fluxos antigos exigem em disco (reextração, original)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    try:
        with stream(no, relativo) as res, open(parcial, "wb") as f:
            for pedaco in res.iter_content(chunk_size=1024 * 1024):
                f.write(pedaco)
        import os
        os.replace(parcial, destino)
    except Exception:
        try:
            parcial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def enviar_arquivo(no: dict, fluxo, relativo: str, token: str | None = None,
                   max_bytes: int | None = None) -> dict:
    """Espelha UMA cópia do upload do navegador para dentro da pasta data() do nó (PUT
    /no/enviar). É o `salvar_ufdr_enviado`/`salvar_documento_enviado` do cenário com nó: o
    navegador fala com o central como sempre, e o central repassa o corpo em streaming — os
    dezenas de GB nunca ficam na memória de nenhum dos dois. O nó decide o nome final (sufixo
    "(2)" etc.) e devolve {"nome", "tamanho_bytes"} no mesmo formato dos salvadores locais.

    `token` acompanha a gravação para a rota de progresso conseguir seguir o .parcial pela
    pergunta GET /no/enviar-progresso, espelho da varredura por tamanho que o central faz na
    sua própria pasta."""
    import requests

    params = {"caminho": relativo}
    if token:
        params["token"] = token
    if max_bytes:
        params["max_bytes"] = max_bytes
    try:
        res = requests.put(_url(no, "/no/enviar"),
                           params=params,
                           headers=_headers(no),
                           data=fluxo,
                           timeout=(_TIMEOUT_CONEXAO, None))
    except requests.RequestException as exc:
        raise NoIndisponivel(f"Não consegui enviar o arquivo ao servidor {no['no_servidor']}: "
                             f"{exc}") from exc
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def enviar_zip(no: dict, fluxo, subpasta: str | None, nome: str) -> list[dict]:
    """Repassa um ZIP de documentos para o nó expandir na pasta dele (POST /no/enviar-zip) — a
    expansão roda no nó com o MESMO `_salvar_documento_zip` (mesmo código, disco certo), e as
    travas anti-zip-bomb continuam as mesmas. Devolve a lista [{"nome", "tamanho_bytes"} que a
    rota local de upload devolve."""
    import requests

    try:
        res = requests.post(_url(no, "/no/enviar-zip"),
                            params={"subpasta": subpasta or "", "nome": nome},
                            headers=_headers(no),
                            data=fluxo,
                            timeout=(_TIMEOUT_CONEXAO, None))
    except requests.RequestException as exc:
        raise NoIndisponivel(f"Não consegui enviar o ZIP ao servidor {no['no_servidor']}: {exc}") \
            from exc
    if res.status_code >= 400:
        _erro_no(res, no)
    return (res.json() or {}).get("arquivos") or []


def expandir_zip(no: dict, relativo: str) -> dict:
    """Expande um .zip que já está na pasta data() do nó (POST /no/expandir-zip) — o espelho da
    expansão local para ZIP copiado à mão na estação. Devolve {"arquivos", "ignorados",
    "truncado"} com os mesmos nomes que expandir_zip_em_disco produz no central."""
    res = _post(no, "/no/expandir-zip", {"caminho": relativo})
    if res.status_code == 404:
        raise FileNotFoundError(relativo)
    if res.status_code >= 400:
        _erro_no(res, no)
    return res.json()


def progresso_envio(no: dict, relativo_parcial: str) -> int | None:
    """Bytes já gravados do upload em andamento no nó (None se não há .parcial com esse nome)."""
    try:
        res = _get(no, "/no/enviar-progresso", params={"caminho": relativo_parcial})
    except NoIndisponivel:
        return None
    if res.status_code == 404:
        return None
    if res.status_code >= 400:
        _erro_no(res, no)
    try:
        return int((res.json() or {}).get("bytes", 0))
    except (TypeError, ValueError):
        return None


def garantir_local(no: dict, relativo: str) -> Path | None:
    """Garante o arquivo no data() do central, puxando do nó se preciso. Devolve o Path local ou
    None (indisponível). É o bridão para os fluxos de documento que ainda assumem o arquivo em
    disco local — os .ufdr NÃO passam por aqui (esses são lidos por faixa, ao vivo)."""
    # Confinado a data/ (ver F3): `relativo` pode vir do banco e o join cru aceitaria
    # absoluto ou ".." — e aqui ele vira mkdir + escrita de bytes vindos da rede.
    try:
        local = destino_em_dados(relativo)
    except ValueError:
        logger.warning("Caminho fora de data/ pedido ao nó %s: %r", no.get("no_servidor"), relativo)
        return None
    if local.is_file():
        return local
    try:
        baixar(no, relativo, local)
        return local
    except (NoIndisponivel, FileNotFoundError, OSError):
        logger.warning("Falha ao espelhar %s do nó %s", relativo, no.get("no_servidor"))
        return None

# ------------------------------------------------------------------ administração do nó
# (o controle remoto da tela Administração → Servidores: atualizar / atualizacao / config /
# reiniciar / estado). Antes eram requests crus dentro de rotas/servidores.py — vêm para cá
# porque a ponte é a única que sabe se o caminho é a porta publicada ou o túnel NAT. Mesmos
# headers, mesmo NoIndisponivel; o status code continua sendo decisão de quem chama.

def atualizar_no(no: dict, versao: str | None = None) -> requests.Response:
    body = {'versao': versao} if versao else {}
    return _post(no, '/no/atualizar', body, timeout=(_TIMEOUT_CONEXAO, 30))


def atualizacao_estado(no: dict) -> requests.Response:
    return _get(no, '/no/atualizacao', timeout=(_TIMEOUT_CONEXAO, 10))


def config_ler(no: dict) -> requests.Response:
    return _get(no, '/no/config', timeout=(_TIMEOUT_CONEXAO, 15))


def config_aplicar(no: dict, valores: dict) -> requests.Response:
    return _post(no, '/no/config', {'valores': valores}, timeout=(_TIMEOUT_CONEXAO, 20))


def reiniciar(no: dict) -> requests.Response:
    return _post(no, '/no/reiniciar', {}, timeout=(_TIMEOUT_CONEXAO, 15))


def estado_maquina(no: dict) -> requests.Response:
    return _get(no, '/no/estado', timeout=(_TIMEOUT_CONEXAO, 15))
