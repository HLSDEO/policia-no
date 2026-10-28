"""Rotas que SÓ existem no papel `no` (PAPEL=no) — o que o central chama.

Registradas em main.py apenas quando o processo é um nó. Não há Postgres aqui: nada deste módulo
toca o banco. As rotas são a cara do que o nó faz de útil:

- GET  /no/saude           — probe público, sem token: o central usa para o ponto verde e o
                             instalar-host para conferir o token digitado;
- POST /no/hash            — MD5/SHA1/SHA256 de um arquivo da pasta data() local (a leitura inteira
                             do .ufdr, que é justamente o que não pode atravessar a WAN);
- POST /no/parse           — IRPF/IPL/IPEI/telefonia/documento: roda o PARSER local
                             (funcoes.analisar_*, que já devolviam dict) e devolve o dict + o texto.
                             A gravação no banco fica inteiramente no central;
- GET  /no/arquivo         — lê bytes da pasta local por faixa (inicio/limite) ou inteira, sempre
                             com o cabeçalho X-Tamanho. É o backend do ArquivoRemoto (zip por
                             Range) e do proxy de mídia;
- PUT  /no/enviar          — RECEBE um upload em streaming (corpo cru) e grava na pasta local: é o
                             "salvar_ufdr_enviado" do cenário com nó — o navegador fala com o
                             central, o central repassa o corpo sem nunca segurá-lo inteiro;
- GET  /no/enviar-progresso — bytes de um .parcial em andamento (a tela acompanha a barra pelo
                             central, que repassa esta pergunta).

Autenticação (todas menos /saude): Authorization: Bearer <token>, comparado em comparação
constante-tempo com NO_TOKEN do .env do nó. O nó tem UMA credencial só — é a mesma que o central
guarda em TX_TOKEN e usa para chamá-lo e para o heartbeat dele.

Segurança de caminho: todo `caminho` é relativo a data/ e passa pelo guard desta página (o mesmo
espírito do resolver de organizacao_arquivos, que exige arquivo existente — para destino de upload
isso não serve) — o nó jamais lê nem escreve fora da pasta de evidências.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from funcoes.organizacao_arquivos import PASTA_DADOS, resolver as resolver_em_dados

logger = logging.getLogger(__name__)

router = APIRouter()


class CaminhoIn(BaseModel):
    caminho: str = Field(..., min_length=1, max_length=3000)


# --------------------------------------------------------------------------------- rate limit
# O nó tem UMA credencial só e nenhuma sessão: sem freio, um endereço varrendo portas testa
# tokens contra /no/* sem custo. Não confunde o nó legítimo — quem acerta o token nunca é
# contabilizado; só as tentativas frustradas por IP. (Espelho do freio de login do central.)
_FALHAS: dict[str, list[float]] = {}
_FALHAS_JANELA_S = 300.0
_FALHAS_LIMITE = 40


def _ip_cliente(request) -> str | None:
    """X-Real-IP quando há proxy na frente (TLS no proxy é a instalação recomendada);
    senão o soquete direto (rede local confiável, é o caso do central alcançando o nó)."""
    if request is None:
        return None
    return (request.headers.get("x-real-ip") or "").strip()[:45] or (
        request.client.host if getattr(request, "client", None) else None)


def _freio_token(ip: str | None, ok: bool) -> None:
    import time as _t

    if not ip:
        return
    agora = _t.time()
    if ok:
        _FALHAS.pop(ip, None)
        return
    tentativas = [t for t in _FALHAS.setdefault(ip, []) if agora - t < _FALHAS_JANELA_S]
    tentativas.append(agora)
    _FALHAS[ip] = tentativas
    if len(_FALHAS) > 2000:  # o freio não pode virar um segundo banco
        _FALHAS.clear()


def exigir_token_no(request: Request,
                    authorization: str | None = Header(default=None)) -> None:
    ip = _ip_cliente(request)
    faltas = len(_FALHAS.get(ip or "", ()))
    if faltas >= _FALHAS_LIMITE:
        raise HTTPException(status_code=429,
                            detail="Demasiadas tentativas de token a partir deste endereço.")
    if not _token_ok(authorization):
        _freio_token(ip, ok=False)
        raise HTTPException(status_code=401, detail="Token do central inválido neste nó.")
    _freio_token(ip, ok=True)


def _token_ok(authorization: str | None) -> bool:
    esperado = (os.getenv("NO_TOKEN") or "").strip()
    if not esperado or not authorization or not authorization.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(esperado, authorization[7:].strip())


def _resolver_ou_404(caminho: str) -> Path:
    resolveu = resolver_em_dados(caminho)
    if resolveu is None or not resolveu.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado na pasta de dados do nó.")
    return resolveu


@router.get("/no/saude")
def saude():
    """Probe público: existe um nó aqui, qual papel e versão. Sem token de propósito — é a
    primeira coisa que o central (e o instalar-host) chamam, antes de qualquer segredo.
    Não revela se o NO_TOKEN está configurado: um endpoint sem autenticação não tem o que
    informar sobre segredo alheio (quem bate sem token recebe 401 de qualquer forma — é a
    mesma resposta de um nó com token errado, e é por aí que a tela 'Testar' vê)."""
    try:
        from funcoes import versao_utils
        versao = versao_utils.versao_instalada()
    except Exception:
        versao = None
    return {"ok": True, "papel": "no", "versao": versao}


@router.get("/no/listar", dependencies=[Depends(exigir_token_no)])
def listar(subpasta: str | None = None, tipo: str = "ufdr"):
    """O que tem na pasta data() do nó — os MESMOS lists que o central faz da sua própria pasta
    (as funções são do mesmo código), para o seletor da web mostrar os arquivos que estão aqui.
    subpasta é a pasta da delegacia; ".." e separadores não passam do sanitizer de baixo."""
    if subpasta and (".." in subpasta or subpasta.startswith("/") or re.match(r"^[A-Za-z]:", subpasta)):
        raise HTTPException(status_code=400, detail="Subpasta inválida.")
    if tipo == "ufdr":
        from funcoes.investigacao_utils import listar_arquivos_ufdr
        return {"arquivos": listar_arquivos_ufdr(subpasta)}
    if tipo == "rif":
        from funcoes.rif_utils import listar_rifs_disponiveis
        return {"arquivos": listar_rifs_disponiveis(subpasta)}
    if tipo == "simba":
        from funcoes.simba_utils import listar_arquivos_simba
        return {"arquivos": listar_arquivos_simba(subpasta)}
    from funcoes.documentos_utils import listar_arquivos_diversos
    return {"arquivos": listar_arquivos_diversos(subpasta)}


class ExtrairIn(BaseModel):
    id_extracao: int
    no_arquivo: str = Field(..., max_length=1000)
    modelo_transcricao: str | None = Field(default=None, max_length=50)
    transcrever_video: bool = False
    numero_inquerito: str | None = Field(default=None, max_length=50)
    # geração do despacho (NU_GERACAO no claim): carimbada em toda entrega; o central recusa
    # a passada antiga quando o watchdog redespachou esta extração (partição de rede)
    geracao: int = Field(default=0, ge=0)


@router.post("/no/extrair", dependencies=[Depends(exigir_token_no)])
def extrair(payload: ExtrairIn):
    """O central DELEGA a extração: o nó processa o próprio arquivo na própria CPU e devolve
    ao central só os dados estruturados (lotes do canal — funcoes/no_extracao.py). Responde
    202 na hora; progresso, erros e o finalizar seguem pelos callbacks de estado."""
    from funcoes import no_extracao

    try:
        aceito = no_extracao.aceitar(payload.id_extracao, payload.no_arquivo,
                                     payload.modelo_transcricao, payload.transcrever_video,
                                     payload.numero_inquerito, geracao=payload.geracao)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except no_extracao.DiscoInsuficienteError as exc:
        # 507 (Insufficient Storage): o central marca a extração com a mensagem na cara do
        # investigador em vez de o nó morrer de disco cheio no meio do parse
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    return {"aceito": aceito["ok"], "em_andamento": not aceito["fila"],
            "rodando": no_extracao.rodando()}


@router.get("/no/extrair", dependencies=[Depends(exigir_token_no)])
def extrair_fila():
    """Fila local do nó (diagnóstico na tela de Servidores)."""
    from funcoes import no_extracao

    return {"rodando": no_extracao.rodando()}


class TranscreverIn(BaseModel):
    id_extracao: int
    no_arquivo: str = Field(..., max_length=1000)
    modelo: str | None = Field(default=None, max_length=50)
    incluir_video: bool = False
    # a lista de pendentes vem PRONTA do central (é ele quem tem o banco): [{prefixo,
    # caminho_zip}] — o nó deduplica por binário e roda o whisper no .ufdr do próprio disco
    tarefas: list[dict] = Field(default_factory=list)


@router.post("/no/transcrever", dependencies=[Depends(exigir_token_no)])
def transcrever(payload: TranscreverIn):
    """O central PEDE a transcrição dos áudios de uma extração (botão "Transcrever"): o faster-
    whisper vive aqui, sobre o arquivo deste disco. Responde 202 na hora; os textos e o fecho
    seguem pelos callbacks de estado da extração (funcoes.no_transcrever)."""
    from funcoes import no_transcrever

    try:
        aceito = no_transcrever.aceitar(payload.id_extracao, payload.no_arquivo,
                                        payload.modelo, payload.incluir_video,
                                        payload.tarefas)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"aceito": bool(aceito["ok"]), "em_andamento": bool(aceito["em_andamento"]),
            "tarefas": aceito["tarefas"], "rodando": no_transcrever.rodando()}


@router.get("/no/transcrever", dependencies=[Depends(exigir_token_no)])
def transcrever_fila():
    """Transcrições remotas em andamento neste nó (diagnóstico)."""
    from funcoes import no_transcrever

    return {"rodando": no_transcrever.rodando()}


class EmbedIn(BaseModel):
    textos: list[str] = Field(..., max_length=2000)


@router.post("/no/embed", dependencies=[Depends(exigir_token_no)])
def embed(payload: EmbedIn):
    """Gera embeddings AQUI (POST /no/embed): o modelo (Ollama local da estação ou API) é
    configurado no .env deste nó e o central nunca mais vê a URL do modelo — só os vetores,
    na mesma ordem dos textos (funcoes.embed_utils.gerar_embeddings)."""
    textos = [t for t in (payload.textos or []) if isinstance(t, str)]
    if not textos:
        raise HTTPException(status_code=400, detail="Nenhum texto no pedido.")
    from funcoes.embed_utils import gerar_embeddings

    try:
        vetores = gerar_embeddings(textos)
    except Exception as exc:
        logger.exception("Falha ao gerar embeddings no nó")
        raise HTTPException(status_code=500,
                            detail=f"Falha ao gerar embeddings nesta estação: {exc}") from exc
    return {"vetores": vetores}


class HashIn(CaminhoIn):
    algoritmos: list[str] | None = None  # padrão: os três; o central pede ("md5","sha1") p/ UFDR


@router.post("/no/hash", dependencies=[Depends(exigir_token_no)])
def hash_arquivo(payload: HashIn):
    """MD5/SHA-1/SHA-256 de um arquivo da pasta do nó, calculados AQUI (é a leitura inteira, que
    não faz sentido atravessar a WAN). Sidecar .hashes.json primeiro: se o upload que chegou pelo
    nó (PUT /no/enviar) já deixou os digestos da gravação, nada de reler os GB."""
    from funcoes.hash_utils import calcular_hashes_arquivo, gravar_sidecar_hashes, ler_sidecar_hashes

    caminho = _resolver_ou_404(payload.caminho)
    algoritmos = tuple(payload.algoritmos) if payload.algoritmos else ("md5", "sha1", "sha256")
    sidecar = ler_sidecar_hashes(caminho)
    if sidecar and all(nome in sidecar for nome in algoritmos):
        return {nome: sidecar[nome] for nome in algoritmos}
    try:
        hashes = calcular_hashes_arquivo(caminho, algoritmos=algoritmos)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao ler o arquivo: {exc}") from exc
    gravar_sidecar_hashes(caminho, hashes, caminho.stat().st_size)
    return hashes


@router.post("/no/parse", dependencies=[Depends(exigir_token_no)])
def parse_documento(payload: CaminhoIn):
    """Roda o parser local do documento e devolve {tipo, texto, dados}. O tipo é detectado pelas
    MESMAS funções que o central usa para decidir o bloco do card — o nó não reimplementa nada,
    só executa mais perto do arquivo."""
    caminho = _resolver_ou_404(payload.caminho)

    resultado: dict = {"tipo": "documento", "texto": None, "erro": None, "dados": {}}

    # texto do documento (base de todos os blocos e da análise de IA no central)
    try:
        from funcoes.documentos_utils import extrair_texto_documento
        texto, erro = extrair_texto_documento(caminho)
        resultado["texto"] = texto
        resultado["erro"] = erro
    except Exception:
        logger.exception("Falha ao extrair texto de %s no nó", caminho.name)
        resultado["erro"] = "Falha inesperada ao ler o documento no servidor da estação."

    # os quatro parsers "dict" — puros, sem banco; o central só grava o que vier aqui
    try:
        from funcoes.ipl_utils import analisar_inquerito
        dados = analisar_inquerito(caminho)
        if dados:
            resultado["tipo"] = "ipl"
            resultado["dados"]["ipl"] = dados
    except Exception:
        logger.exception("analisar_inquerito falhou para %s no nó", caminho.name)

    try:
        from funcoes.irpf_utils import analisar_irpf
        dados = analisar_irpf(caminho)
        if dados:
            resultado["tipo"] = "irpf"
            resultado["dados"]["irpf"] = dados
    except Exception:
        logger.exception("analisar_irpf falhou para %s no nó", caminho.name)

    try:
        from funcoes.ipei_utils import analisar_ipei
        dados = analisar_ipei(caminho)
        if dados:
            resultado["tipo"] = "ipei"
            resultado["dados"]["ipei"] = dados
    except Exception:
        logger.exception("analisar_ipei falhou para %s no nó", caminho.name)

    try:
        from funcoes.telefonia_utils import analisar_arquivo as analisar_telefonia
        dados = analisar_telefonia(caminho)
        if dados:
            resultado["tipo"] = "telefonia"
            resultado["dados"]["telefonia"] = dados
    except Exception:
        logger.exception("analisar_telefonia falhou para %s no nó", caminho.name)

    return resultado


class OrganizarIn(BaseModel):
    caminho: str = Field(..., max_length=1000)
    numero_inquerito: str = Field(..., max_length=50)
    categoria: str = Field(default="OUTROS", max_length=20)
    novo_nome: str | None = Field(default=None, max_length=200)


@router.post("/no/organizar", dependencies=[Depends(exigir_token_no)])
def organizar_arquivo(payload: OrganizarIn):
    """O central manda o nó ARRUMAR o arquivo dele: move para data/<delegacia>/<inquérito>/
    <categoria>/ na estação (a mesma regra do organizador do central, rodando longe dele). É o
    organize do documento importado numa delegacia com nó — o arquivo nunca pisa no central.
    RIF/BANCARIO acompanham porque o pacote importado (pasta do RIF, CSV do SIMBA) também mora
    aqui; `organizar()` aceita PASTA (o RIF é uma pasta), e `novo_nome` é o padronizado
    RIF_<número> que o central calcula depois de parsear."""
    from funcoes.organizacao_arquivos import (CATEGORIA_BANCARIO, CATEGORIA_CELULAR,
                                              CATEGORIA_OUTROS, CATEGORIA_RIF, destino_em_dados,
                                              organizar)

    if payload.categoria not in (CATEGORIA_OUTROS, CATEGORIA_CELULAR, CATEGORIA_RIF, CATEGORIA_BANCARIO):
        raise HTTPException(status_code=400, detail="Categoria inválida.")
    # destino_em_dados + exists e NÃO _resolver_ou_404: o pacote RIF é PASTA (o resolver comum só
    # aceita arquivo) e o caminho que o central manda é o relativo exato — varrer data/ atrás de
    # homônimo aqui moveria a prova do inquérito errado
    try:
        caminho = destino_em_dados(payload.caminho)
    except ValueError:
        raise HTTPException(status_code=400, detail="Caminho fora da pasta de dados.")
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="Arquivo/pasta não encontrado na pasta de dados do nó.")
    return {"relativo": organizar(caminho, payload.numero_inquerito, payload.categoria,
                                  novo_nome=payload.novo_nome)}


# Teto de uma leitura por faixa fora do streaming: sem ele, ?limite=99999999999 materializa
# o arquivo inteiro na RAM do nó (OOM remoto com o token do central). O ArquivoRemoto do central
# lê em blocos de 4 MiB (ver _BLOCO em no_ponte.py) — 8 MiB dá folga sem virar vetor de DoS. Ver F5.
_LIMITE_FAIXA_BYTES = 8 * 1024 * 1024


@router.get("/no/arquivo", dependencies=[Depends(exigir_token_no)])
def ler_arquivo(caminho: str, inicio: int | None = None, limite: int | None = None):
    """Lê a pasta local por faixa. X-Tamanho acompanha SEMPRE (o ArquivoRemoto o usa como
    seek-end). inicio/limite ausentes = arquivo inteiro, em streaming (proxy de mídia)."""
    resolveu = _resolver_ou_404(caminho)
    try:
        total = resolveu.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao inspecionar o arquivo: {exc}") from exc

    cabecalhos = {"X-Tamanho": str(total), "Accept-Ranges": "bytes"}

    if inicio is None and limite is None:
        def _gerar():
            with open(resolveu, "rb") as f:
                while True:
                    pedaco = f.read(1024 * 1024)
                    if not pedaco:
                        break
                    yield pedaco

        return StreamingResponse(_gerar(), media_type="application/octet-stream",
                                 headers=cabecalhos)

    if (inicio is not None and inicio < 0) or (limite is not None and limite < 0):
        raise HTTPException(status_code=400, detail="inicio/limite inválidos.")
    ini = inicio or 0
    if ini >= total:
        # Fora do arquivo: 416 com o tamanho real, para o cliente ajustar o cursor —
        # antes era 200 vazio, que o ArquivoRemoto lia como "fim" silencioso. Ver F5.
        return Response(content=b"", status_code=416, media_type="application/octet-stream",
                        headers={**cabecalhos, "Content-Range": f"bytes */{total}"})
    n = limite if limite is not None else (total - ini)
    n = max(0, min(n, total - ini, _LIMITE_FAIXA_BYTES))
    with open(resolveu, "rb") as f:
        f.seek(ini)
        dados = f.read(n)
    cabecalhos["Content-Range"] = f"bytes {ini}-{ini + n - 1}/{total}"
    return Response(content=dados, media_type="application/octet-stream", headers=cabecalhos)


# ------------------------------------------------------------------ mídia direta ao navegador
#
# docs/no_midia_direta.md: o analista sentado ao lado do nó não tem por que receber o anexo pelo
# rinque São Mateus→Vila Velha→São Mateus. O central (que autentica o usuário e confere o
# inquérito) emite um ticket de ~10 min para UM membro do .ufdr; o navegador pede o ticket ao
# central e baixa o REST do nó, direto, por HTTPS (nginx no-web). A única porta pública deste
# servidor na LAN é /no/saude + /no/midia — o resto do /no/* continua só para o central (a
# whitelist é do nginx). A leitura sai do NVMe local: o mesmo zipfile/ufdr_cache do sempre, só
# que do lado certo da rede.

_MIDIA_CHUNK = 1024 * 1024


def _offset_dados_local(fp, info) -> int:
    """Offset do primeiro byte de dados de um membro STORED (mesma regra do
    ufdr_cache._offset_dados_stored: cabeçalho local = 30 + nome + extra). É o que permite
    atender Range de um vídeo sem descomprimir nada — STORED é o formato da mídia do Cellebrite."""
    fp.seek(info.header_offset + 26)
    nome_len = int.from_bytes(fp.read(2), "little")
    extra_len = int.from_bytes(fp.read(2), "little")
    return info.header_offset + 30 + nome_len + extra_len


def _interpreta_range(cabecalho: str | None, total: int):
    """Range de UMA faixa (o suficiente para player de mídia): devolve (inicio, fim) inclusivos
    ou None para o arquivo inteiro. Faixa absurda devolve (−1,−1) — quem chama responde 416."""
    if not cabecalho:
        return None
    tipo, _, lista = cabecalho.partition("=")
    if tipo.strip().lower() != "bytes" or "," in lista:
        return None  # multi-range/desconhecido: serve inteiro (o browser aceita sem Range)
    campo = lista.strip()
    ini, _, fim = campo.partition("-")
    try:
        if not ini:                      # "bytes=-N": os N últimos
            n = int(fim)
            if n <= 0:
                return (-1, -1)
            return (max(0, total - n), total - 1)
        inicio = int(ini)
        final = int(fim) if fim else total - 1
    except ValueError:
        return None
    if inicio >= total or final < inicio:
        return (-1, -1)
    return (inicio, min(final, total - 1))


@router.get("/no/midia")
def midia_direta(request: Request, tk: str = Query(default="", min_length=8, max_length=16000)):
    """Serve um membro do .ufdr para o navegador com o ticket como única credencial.

    O payload assinado (funcoes/no_ticket) amarra o .ufdr (``r``), o membro (``i``, com o ``::``
    dos aninhados da nuvem), o mime (``t``) e o modo anexar (``d``/``n``) — alterar QUALQUER byte
    do caminho invalida o HMAC na hora. Range só é atendido em membro STORED (vídeo com seek sem
    descomprimir); o resto sai em fluxo puro."""
    from funcoes import metricas_no, no_ticket

    def _recusa(motivo: str) -> HTTPException:
        metricas_no.incr("midia_direta_recusada")
        return HTTPException(status_code=403, detail=motivo)

    try:
        passe = no_ticket.validar((os.getenv("NO_TOKEN") or "").strip(), tk)
    except no_ticket.TicketInvalido:
        raise _recusa("Ticket inválido.")
    metricas_no.incr("midia_direta")

    relativo = str(passe.get("r") or "")[:3000]
    interno = str(passe.get("i") or "")[:3000]
    mime = str(passe.get("t") or "application/octet-stream")[:120]
    if not relativo or not interno:
        raise _recusa("Ticket sem destino.")
    resolveu = _resolver_ou_404(relativo)

    cabecalhos: dict[str, str] = {"Accept-Ranges": "none"}
    if str(passe.get("d") or "") == "1":
        from funcoes.resposta_utils import cabecalho_disposicao

        cabecalhos["Content-Disposition"] = cabecalho_disposicao(
            "attachment", str(passe.get("n") or "arquivo")[:300] or "arquivo")
    else:
        cabecalhos["Content-Disposition"] = "inline"

    from funcoes import ufdr_cache, ufdr_zip

    aninhado = ufdr_cache.desmembrar_aninhado(interno)
    if aninhado:
        # nuvem Google/iCloud: o binário vive dentro de um ZIP/TAR solto no .ufdr. Descomprimir a
        # janela toda é o custo local do nó — Range aqui fica de fora (o modal toca sem seek).
        membro_externo, caminho_interno = aninhado

        def _gerar_aninhado():
            with ufdr_cache.abrir_membro_aninhado(resolveu, membro_externo, caminho_interno) as fp:
                while True:
                    pedaco = fp.read(_MIDIA_CHUNK)
                    if not pedaco:
                        break
                    yield pedaco

        try:
            if not ufdr_cache.membro_aninhado_existe(resolveu, membro_externo, caminho_interno):
                raise HTTPException(status_code=404, detail="Arquivo não encontrado no .ufdr.")
        except OSError:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado no .ufdr.")
        return StreamingResponse(_gerar_aninhado(), media_type=mime, headers=cabecalhos)

    zf = ufdr_zip.abrir(resolveu)
    try:
        try:
            info = zf.getinfo(interno)
        except KeyError:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado no .ufdr.")
        stored = info.compress_type == 0 and not info.flag_bits & 0x1  # STORED sem criptografia
        faixa = _interpreta_range(request.headers.get("range"), info.file_size) if stored else None
        if faixa and faixa[0] < 0:
            return Response(status_code=416, headers={
                **cabecalhos, "Content-Range": f"bytes */{info.file_size}"})
        if not stored:
            def _gerar_zip():
                with zf.open(info) as fp:
                    while True:
                        pedaco = fp.read(_MIDIA_CHUNK)
                        if not pedaco:
                            break
                        yield pedaco
            return StreamingResponse(_gerar_zip(), media_type=mime, headers=cabecalhos)

        with open(resolveu, "rb") as fp:
            base = _offset_dados_local(fp, info)

        def _gerar_stored(inicio_faixa: int, fim_faixa: int):
            restante = fim_faixa - inicio_faixa + 1
            with open(resolveu, "rb") as fp:
                fp.seek(base + inicio_faixa)
                while restante > 0:
                    pedaco = fp.read(min(_MIDIA_CHUNK, restante))
                    if not pedaco:
                        break
                    restante -= len(pedaco)
                    yield pedaco

        if faixa:
            inicio, fim = faixa
            cabecalhos.update({
                "Content-Range": f"bytes {inicio}-{fim}/{info.file_size}",
                "Content-Length": str(fim - inicio + 1),
            })
            return StreamingResponse(_gerar_stored(inicio, fim), status_code=206,
                                     media_type=mime, headers=cabecalhos)
        cabecalhos.update({"Accept-Ranges": "bytes", "Content-Length": str(info.file_size)})
        return StreamingResponse(_gerar_stored(0, info.file_size - 1),
                                 media_type=mime, headers=cabecalhos)
    except HTTPException:
        zf.close()
        raise
    except Exception:
        zf.close()
        raise


# ------------------------------------------------------------------ upload (central → nó)


def _destino_upload(caminho: str) -> Path:
    """Resolve o DESTINO de um upload dentro de data() — o guard do `resolver` de leitura exige
    arquivo existente, o que não serve para gravar; o espírito é o mesmo: só caminho relativo,
    sem "..", sem absoluto, sem drives."""
    valor = (caminho or "").strip().replace("\\", "/").strip("/")
    if not valor or ".." in valor.split("/") or re.match(r"^[A-Za-z]:", valor) or "\x00" in valor:
        raise HTTPException(status_code=400, detail="Caminho de destino inválido.")
    destino = (PASTA_DADOS / valor).resolve()
    base = PASTA_DADOS.resolve()
    if base != destino and base not in destino.parents:
        raise HTTPException(status_code=400, detail="Caminho de destino inválido.")
    return destino


def _reivindicar_nome(destino: Path) -> Path:
    """Mesma regra do salvador local: nunca sobrescrever prova — se o nome existe, ganha
    " (2)", " (3)"... e a posse é por criação exclusiva ("xb")."""
    if destino.parent != PASTA_DADOS.resolve() and not destino.parent.is_dir():
        destino.parent.mkdir(parents=True, exist_ok=True)
    sufixo = 1
    while True:
        alvo = destino if sufixo == 1 else destino.with_name(
            f"{destino.stem} ({sufixo}){destino.suffix}")
        try:
            open(alvo, "xb").close()
            return alvo
        except FileExistsError:
            sufixo += 1
            if sufixo > 999:
                raise HTTPException(status_code=409,
                                    detail="Já existem arquivos demais com este nome no nó.")


_RE_TOKEN_PARCIAL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Tetos quando o CENTRAL não mandou max_bytes (um cliente qualquer com o token na mão):
# sem eles, o único guard de disco do nó era a boa vontade de quem chama. .ufdr legítimo
# tem dezenas de GB — o teto padrão é a barra do absurdo, não a barra do produto; quem
# envia documento (UPLOAD_DOCUMENTO_MB do central) continua passando o próprio limite.
_ENVIO_SEM_LIMITE_MB = int(os.getenv("NO_ENVIO_MAX_MB") or 512 * 1024)   # 512 GiB
_ZIP_SEM_LIMITE_MB = int(os.getenv("NO_ZIP_MAX_MB") or 4096)             # 4 GiB


@router.put("/no/enviar", dependencies=[Depends(exigir_token_no)])
async def enviar(caminho: str, request: Request, token: str | None = None,
                 max_bytes: int | None = None):
    """Grava na pasta local o corpo da requisição, vindo em streaming (o central repassa o upload
    do navegador sem materializar os GB em RAM nenhuma). O nome chega em `caminho` (relativo a
    data/); se já existe, o sufixo "(2)" é decidido AQUI (o dono da pasta decide o nome, como no
    central) e devolvido no JSON — é ele que a tela manda de volta na extração.

    `token`: grava em `<token><ext>.parcial` para GET /no/enviar-progresso acompanhar pela
    varredura de tamanho — mesmo mecanismo do upload local, sem estado compartilhado.
    `max_bytes`: teto por arquivo (UPLOAD_DOCUMENTO_MB do CENTRAL, que é quem conhece a
    configuração) — estourou, o corpo é abortado e nada fica na estante. Corpo vazio é erro
    (nunca um .ufdr de 0 bytes)."""
    from starlette.concurrency import run_in_threadpool

    destino = _destino_upload(caminho)
    # delegacia nova na estação: a pasta-mãe do upload pode não existir ainda (o caminho do
    # token abre o .parcial direto, sem passar pela reivindicação de nome que cria a pasta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if token and not _RE_TOKEN_PARCIAL.match(token):
        raise HTTPException(status_code=400, detail="Token de acompanhamento inválido.")
    # sem max_bytes do chamador vale o teto do nó — o upload deixa de ser DoS de disco
    # garantido para quem quer que tenha o token em mãos
    teto_bytes = max_bytes if max_bytes else _ENVIO_SEM_LIMITE_MB * 1024 * 1024
    if token:
        # mesmo nome do .parcial do salvador LOCAL (<token><ext>.parcial ao lado do destino) — é
        # por esse nome que o central acompanha o progresso sem precisar saber o nome final
        parcial = destino.parent / f"{token}{destino.suffix}.parcial"
        alvo = parcial
    else:
        # sem token não há o que acompanhar: reivindicar o nome ANTES de gravar (criação
        # exclusiva, como _abrir_destino_unico do central) — nunca truncar prova existente
        alvo = _reivindicar_nome(destino)
    total = 0
    # .ufdr: os digestos saem dos bytes que passam pela gravação e vão para o sidecar
    # <nome>.hashes.json (funcoes/hash_utils.py) — quando o central pedir POST /no/hash na
    # extração, o nó devolve o sidecar em vez de reler as dezenas de GB. Só .ufdr: o resto é
    # pequeno e hasheado na hora em que precisar.
    hasheador = None
    if alvo.suffix.lower() == ".ufdr":
        from funcoes.hash_utils import HasheadorStreaming
        hasheador = HasheadorStreaming(("md5", "sha1"))
    try:
        with open(alvo, "wb") as saida:
            async for pedaco in request.stream():
                # escrita no threadpool: esta rota é async (streaming de dezenas de GB não
                # pode bloquear o loop), e write() de disco em cada chunk do laço pararia
                # toda a API do nó (saúde, Range reads) enquanto o upload avança
                await run_in_threadpool(saida.write, pedaco)
                total += len(pedaco)
                if hasheador:
                    hasheador.atualizar(pedaco)
                if total > teto_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{destino.name}: maior que o limite de {teto_bytes // (1024 * 1024)} MB "
                               "por arquivo.")
        if total == 0:
            raise HTTPException(status_code=400, detail=f"{destino.name}: arquivo vazio.")
        if token:
            final = _reivindicar_nome(destino)
            os.replace(alvo, final)
            alvo = final
        if hasheador:
            from funcoes.hash_utils import gravar_sidecar_hashes
            gravar_sidecar_hashes(alvo, hasheador.finalizar(), total)
        # o NOME PURO é o contrato da tela (a subpasta volta separada, como no central)
        return {"nome": alvo.name, "tamanho_bytes": total}
    except BaseException:
        # nada de meio-arquivo na estante de prova (mesma regra do salvador local)
        try:
            alvo.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@router.get("/no/enviar-progresso", dependencies=[Depends(exigir_token_no)])
def enviar_progresso(caminho: str):
    """Bytes gravados até agora do upload em andamento — varredura de tamanho do .parcial, sem
    estado no processo (qualquer worker responde a mesma pergunta). 404 = não há envio aberto."""
    parcial = _destino_upload(caminho)
    if not parcial.is_file():
        raise HTTPException(status_code=404, detail="Nenhum envio em andamento com esse nome.")
    return {"bytes": parcial.stat().st_size}


@router.post("/no/expandir-zip", dependencies=[Depends(exigir_token_no)])
def expandir_zip(payload: CaminhoIn):
    """Expande um .zip que já está na pasta data() do nó — o espelho da expansão local para ZIP
    copiado à mão na estação (ver documentos_utils.expandir_zip_em_disco: mesmo núcleo, mesmas
    travas anti-zip-bomb). O ZIP original fica onde está; quem chama (o central, ao importar)
    o organiza depois junto com o resto."""
    from funcoes.documentos_utils import expandir_zip_em_disco
    from funcoes.organizacao_arquivos import PASTA_DADOS as _DADOS

    caminho = _resolver_ou_404(payload.caminho)
    try:
        # subpasta = primeiro segmento do relativo (a delegacia), para os expandidos caírem
        # na mesma pasta de entrega que o central usaria
        rel = caminho.relative_to(_DADOS).as_posix()
        subpasta = rel.split("/")[0] if "/" in rel else None
        return expandir_zip_em_disco(caminho, subpasta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/no/enviar-zip", dependencies=[Depends(exigir_token_no)])
async def enviar_zip(request: Request, subpasta: str | None = None, nome: str = "upload.zip"):
    """Recebe um ZIP de documentos em streaming e o EXPANDE na pasta local — o espelho exato de
    documentos_utils._salvar_documento_zip, que roda AQUI porque o nó tem o mesmo código e a mesma
    data(), mas o disco certo. O central repassa os bytes crus do navegador; devolve a mesma lista
    {"arquivos": [{"nome", "tamanho_bytes"}]} da rota local (nomes puros, como o front espera).
    Travas anti-zip-bomb são as mesmas do central — é a mesma função."""
    import tempfile

    from starlette.concurrency import run_in_threadpool

    # o spool vive em TMPDIR — frequentemente tmpfs = RAM: sem teto, um ZIP gordo derruba
    # o container antes mesmo da expansão (as travas anti-zip-bomb são do conteúdo, não do
    # spool). Teto próprio + escrita no threadpool (mesma regra do PUT /no/enviar).
    teto_bytes = _ZIP_SEM_LIMITE_MB * 1024 * 1024
    spool = tempfile.NamedTemporaryFile(prefix="no_zip_", delete=False)
    try:
        total = 0
        with spool as tmp:
            async for pedaco in request.stream():
                await run_in_threadpool(tmp.write, pedaco)
                total += len(pedaco)
                if total > teto_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{nome}: maior que o limite de {_ZIP_SEM_LIMITE_MB} MB por ZIP.")
        if total == 0:
            raise HTTPException(status_code=400, detail=f"{nome}: arquivo vazio.")
        from funcoes.documentos_utils import _salvar_documento_zip

        with open(spool.name, "rb") as f:
            salvos = _salvar_documento_zip(f, nome, subpasta)
        return {"arquivos": salvos}
    finally:
        try:
            os.unlink(spool.name)
        except OSError:
            pass


# ------------------------------------------------------------------ atualização remota (central → nó)


class AtualizarIn(BaseModel):
    versao: str | None = Field(default=None, max_length=20)  # None = a mais nova que o check achou


@router.post("/no/atualizar", dependencies=[Depends(exigir_token_no)])
def atualizar(payload: AtualizarIn):
    """O central manda o nó SE ATUALIZAR: grava `pedido.json` no .deploy/controle local — o MESMO
    handshake da tela Servidor do central, disparado daqui. O path unit `analyser-update-apply@`
    do host vê o arquivo e roda o `analyser-update apply` (pull do release, build, up -d, health
    /no/saude). Exige o agente instalado no host (scripts/instalar-host.sh) e o bind
    `.deploy/controle` do compose do nó. Sem `versao`, usa a tag mais nova que o check periódico
    do próprio nó já descobriu (`disponivel.json`)."""
    from funcoes import versao_utils

    alvo = (payload.versao or "").strip().lstrip("v")
    if not alvo:
        disponivel = versao_utils._ler_json(versao_utils.ARQUIVO_DISPONIVEL)
        alvo = str(disponivel.get("tag", "")).lstrip("v")
    if not alvo:
        return {"aceito": False,
                "erro": "nenhuma versão disponível conhecida — o check do host ainda não achou "
                        "release novo (analiser-update check)"}
    resultado = versao_utils.solicitar_atualizacao(alvo, "central")
    if not resultado.get("aceito") and "não é mais nova" in str(resultado.get("erro", "")):
        # alvo veio do disponivel.json velho do próprio nó (o central não passou versão) — a
        # mensagem crua ("4.6.5 não é mais nova que 4.6.9") só confunde o operador
        resultado["erro"] = (f"o nó já está em dia ({versao_utils.versao_instalada()}) segundo o "
                             f"check local — clique em 'Verificar' no central e tente de novo")
    return resultado


@router.get("/no/atualizacao", dependencies=[Depends(exigir_token_no)])
def atualizacao_estado():
    """Progresso da atualização em curso (o central repassa para a tela Servidores). Durante o
    build/restart o próprio nó não responde — é esperado; a tela tolera as falhas de polling."""
    from funcoes import versao_utils

    estado = versao_utils.progresso_atualizacao()
    try:
        estado["versao_instalada"] = versao_utils.versao_instalada()
    except Exception:
        estado["versao_instalada"] = None
    return estado


# ------------------------------------------------------------- desempenho do nó (central → nó)

# As chaves que o central pode ajustar NA ESTAÇÃO do nó. Cada uma: (rótulo, mínimo, máximo,
# exige reiniciar o container para valer).
# - EXTRACOES_SIMULTANEAS: o semaphore da fila é criado uma vez por processo (funcoes/
#   no_extracao.py) — mexer no .env sem reiniciar não muda o teto em vigor;
# - TRANSCRICAO_THREADS: lido a cada transcrição (desempenho_utils.valor_int) — vale na hora;
# - API_WORKERS: entra no comando do uvicorn via compose — só pega recriando o container.
_PARAMETROS_NO = {
    "EXTRACOES_SIMULTANEAS": ("Extrações simultâneas no nó", 1, 16, True),
    "TRANSCRICAO_THREADS": ("Processos de transcrição", 1, 8, False),
    "API_WORKERS": ("Processos da API do nó", 1, 4, True),
    # teto de jobs OCR/CLIP/facial que a CPU desta estação puxa do central ao mesmo tempo
    # (funcoes/no_jobs.py); 0 = operário desligado. O semaphore nasce com o valor do processo —
    # mexer sem reiniciar não muda o teto em vigor.
    "JOBS_NO_LIMITE": ("Jobs de imagem simultâneos no nó", 0, 8, True),
}


@router.get("/no/config", dependencies=[Depends(exigir_token_no)])
def config_ler():
    """Configuração de desempenho deste nó: o que está fixado no .env local + o que as fórmulas
    recomendam para a HARDWARE DO NÓ (o nó tem o mesmo desempenho_utils do central — e é ele quem
    conhece a própria máquina, não o central). Não há banco aqui: o .env da estação É a fonte."""
    from funcoes import desempenho_utils
    from funcoes.env_utils import parse_env, read_env_text

    env = parse_env(read_env_text())
    recomendados = desempenho_utils.recomendados()
    # O que o PROCESSO usa agora (≠ .env quando alguém aplicou config sem reiniciar — é o que o
    # banner "reinício pendente" da tela compara):
    # - EXTRACOES: o semaphore nasceu com o teto de quem o criou primeiro (funcoes/no_extracao);
    #   nunca criado = vai nascer com o valor do arquivo, nada pendente.
    # - API_WORKERS: entra no comando do uvicorn via ambiente do compose — o env do processo É a
    #   verdade em vigor.
    # - TRANSCRICAO: relido a cada uso, nunca fica pendente.
    from funcoes import no_extracao

    # EXTRACOES em vigor = capacidade do semaphore deste processo convertida para o total do nó
    # (cap × workers); 0 = semaphore ainda não nasceu, nada pendente.
    cap_vigor = (no_extracao._LIMITE_CAP * no_extracao._workers()
                 if no_extracao._LIMITE_CAP else 0)
    em_vigor = {
        "EXTRACOES_SIMULTANEAS": str(cap_vigor or (env.get("EXTRACOES_SIMULTANEAS") or "").strip()
                                     or recomendados.get("EXTRACOES_SIMULTANEAS", "")),
        "API_WORKERS": (os.getenv("API_WORKERS") or "").strip(),
        "TRANSCRICAO_THREADS": (env.get("TRANSCRICAO_THREADS") or "").strip(),
    }
    parametros = []
    pendente = False
    for chave, info in _PARAMETROS_NO.items():
        alvo = (env.get(chave) or "").strip() or str(recomendados.get(chave, ""))
        vivo = em_vigor.get(chave) or alvo
        if info[3] and alvo and vivo != alvo:
            pendente = True
        parametros.append({
            "chave": chave,
            "rotulo": info[0],
            "minimo": info[1],
            "maximo": info[2],
            "exige_reiniciar": info[3],
            "configurado": (env.get(chave) or "").strip(),
            "recomendado": str(recomendados.get(chave, "")),
            "em_vigor": vivo,
        })
    return {"parametros": parametros, "reinicio_pendente": pendente}


class ConfigIn(BaseModel):
    valores: dict[str, str | int] = Field(..., min_length=1, max_length=len(_PARAMETROS_NO))


@router.post("/no/config", dependencies=[Depends(exigir_token_no)])
def config_aplicar(payload: ConfigIn):
    """Grava as chaves no .env LOCAL (o mesmo upsert com flock do central) e diz se precisa
    reiniciar. Não reinicia nada sozinho — quem quer o restart chama POST /no/reiniciar logo
    depois (o central faz isso no mesmo pedido quando `reiniciar=true`)."""
    from funcoes.env_utils import parse_env, read_env_text, read_modify_write_env

    valores: dict[str, str] = {}
    limpar: list[str] = []  # valor vazio = "voltar ao automático": a linha sai do .env
    for chave, bruto in payload.valores.items():
        info = _PARAMETROS_NO.get(chave)
        if info is None:
            raise HTTPException(status_code=400, detail=f"Chave não permitida neste nó: {chave}")
        texto = str(bruto).strip()
        if texto == "":
            limpar.append(chave)
            continue
        try:
            numero = int(texto)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"{info[0]}: valor deve ser inteiro, veio {bruto!r}") from None
        if not info[1] <= numero <= info[2]:
            raise HTTPException(status_code=400,
                                detail=f"{info[0]}: use um número entre {info[1]} e {info[2]}")
        valores[chave] = str(numero)

    antes = parse_env(read_env_text())
    if limpar:
        # upsert não apaga linha nenhuma: aqui é o mesmo texto SEM as linhas das chaves
        # limpas (com as novas embutidas no mesmo upsert) — uma escrita só, mesmo flock
        from funcoes.env_utils import upsert_env_values, write_env_text

        mantem = [linha for linha in read_env_text().splitlines()
                  if linha.split("=", 1)[0].strip() not in limpar or "=" not in linha]
        write_env_text(upsert_env_values("\n".join(mantem), valores))
    elif valores:
        read_modify_write_env(valores)
    mudou_reiniciar = any(
        (antes.get(chave) or "").strip() != valores.get(chave, "")
        for chave in list(valores) + limpar
        if _PARAMETROS_NO[chave][3]
    )
    logger.info("Config de desempenho aplicada no nó: %s", ", ".join(
        [f"{c}={v}" for c, v in sorted(valores.items())] + [f"{c}=(auto)" for c in sorted(limpar)]))
    return {"ok": True, "aplicado": valores, "limpo": limpar, "precisa_reiniciar": mudou_reiniciar}


@router.post("/no/reiniciar", dependencies=[Depends(exigir_token_no)])
def reiniciar():
    """Reinicia o container do nó (grava reiniciar.req — a path unit do host roda
    `analyser-update reiniciar`). É o braço remoto do botão da tela Desempenho do central, e o
    complemento do POST /no/config quando a chave exige recriar o container."""
    from funcoes import reiniciar_utils

    return reiniciar_utils.solicitar_reiniciar("central")


def _estado_midia() -> dict:
    """Situação do TLS da mídia direta (docs/no_midia_direta.md). O no-web é quem termina o TLS,
    mas quem LÊ o certificado é aqui — a mesma pasta vem montada só-leitura em /app/certs. É o
    sinal que a tela Administração → Servidores usa para amarelar "sem certificado" ou
    "expira em N dias" antes do navegador de todo mundo começar a cair no fallback."""
    import datetime
    import ssl

    caminho = Path(os.getenv("NO_MIDIA_CERT") or "/app/certs/midia.crt")
    try:
        # a pasta de certificados pode ser 700/root (a chave mora ali do lado): stat() aqui
        # levanta PermissionError em vez de False, e um GET de telemetria nunca pode derrubar
        # o /no/estado inteiro — sem visibilidade é como sem certificado
        presente = caminho.is_file()
    except OSError:
        presente = False
    saida = {"certificado": presente, "expira_em": None, "dias_restantes": None}
    if not saida["certificado"]:
        return saida
    try:
        # _test_decode_cert é API privada do stdlib, mas é o único parser de X.509 que existe
        # dentro do container sem acrescentar dependência só para ler uma data
        dados = ssl._ssl._test_decode_cert(str(caminho))
        quando = datetime.datetime.strptime(
            dados["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
        saida["expira_em"] = quando.isoformat(timespec="seconds")
        saida["dias_restantes"] = (quando - datetime.datetime.now(datetime.timezone.utc)).days
    except Exception:
        pass  # certificado presente porém ilegível: "certificado: True" + sem datas já denuncia
    return saida


@router.get("/no/estado", dependencies=[Depends(exigir_token_no)])
def estado():
    """Uso da máquina do nó (CPU/memória/disco/uptime) + a fila local de extração — o central
    repassa para a tela Administração → Servidores. Mesma filosofia do /api/sistema/metricas
    do central (psutil visto de dentro do container; sem limites no compose, é o host)."""
    import time

    import psutil

    from funcoes import metricas_no, no_extracao, no_jobs, versao_utils

    mem = psutil.virtual_memory()

    def _disco(rotulo: str, caminho: str) -> dict:
        try:
            u = psutil.disk_usage(caminho)
            return {"rotulo": rotulo, "caminho": caminho, "total": u.total, "usado": u.used,
                    "livre": u.free, "percentual": u.percent}
        except OSError:
            return {"rotulo": rotulo, "caminho": caminho, "erro": "inacessível"}

    try:
        versao = versao_utils.versao_instalada()
    except Exception:
        versao = None
    # a última atualização do agente viaja junto (leitura local de arquivo, barata): a linha da
    # tela mostra "⟳ atualizando… / ✓ 4.6.17 / ✗ falhou" sem abrir o modal de log
    try:
        prog = versao_utils.progresso_atualizacao()
        atualizacao = {"rodando": prog.get("rodando"), "resultado": prog.get("resultado")}
    except Exception:
        atualizacao = None
    return {
        "ok": True,
        "versao": versao,
        "atualizacao": atualizacao,
        "uptime_segundos": int(time.time() - psutil.boot_time()),
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_cores": psutil.cpu_count() or 1,
        "memoria": {"total": mem.total, "usado": mem.total - mem.available,
                    "percentual": mem.percent},
        "discos": [
            _disco("Dados (evidências)", "/app/data"),
            _disco("Sistema (container)", "/"),
        ],
        "fila": no_extracao.fila_estado(),
        # fila do operário de jobs (OCR/CLIP/facial puxados do central — funcoes/no_jobs.py)
        "jobs": no_jobs.fila_estado(),
        # profundidade do spool de entrega + contadores de resiliência (funcoes/metricas_no.py):
        # spool crescendo ou *falhas subindo com o nó "verde" é a queda que ninguém viu
        "spool": no_extracao.spool_estado(),
        "metricas": metricas_no.snapshot(),
        # TLS da mídia direta: presença/validade do certificado em /app/certs (a porta 8443 da
        # LAN é o assunto do docs/no_midia_direta.md)
        "midia": _estado_midia(),
    }
