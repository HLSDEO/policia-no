"""Rotas internos que o NÓ chama no CENTRAL (prefixo /api/no, Bearer = token do nó).

Por enquanto é só o ping — e ele já carrega todo o peso do desenho:

- é o heartbeat que mantém o ponto verde na tela Administração → Servidores (funcoes/no_utils.
  exigir_no bate DT_ULTIMO_HEARTBEAT em TODA chamada autenticada, então hash/parse/leitura de
  arquivo também batem — o ping só cobre o nó ocioso);
- devolve a versão do central: nó e central desatualizados são o erro de deploy clássico deste
  desenho (parser novo no central, nó velho produzindo dict no formato velho), e a resposta do
  ping é onde os dois se denunciam mutuamente.

Rotas de fila (claim de jobs/extrações) entram aqui conforme os pipelines pesados — UFDR/IPED —
forem portados (docs/no_descentralizado.md, seção "o que ainda roda no central").
"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from funcoes import no_utils
from pydantic import BaseModel, Field

router = APIRouter()

# Teto do corpo de um lote/resultado: o emissor do nó fecha lotes em ~8 MiB
# (canal_extracao._LOTE_BYTES); o dobro é folga de protocolo, não licença de memória.
# Sem teto, um nó mal-comportado (ou um token vazado) materializa o que quiser no POST.
_LIMITE_CORPO_BYTES = 16 * 1024 * 1024


async def _corpo_texto(request: Request) -> str:
    """Corpo cru com teto de tamanho e UTF-8 honesto: estourou → 413, binário → 400 — nunca
    um 500 de UnicodeDecodeError que o nó reenvia em loop sem entender."""
    from fastapi import HTTPException

    bruto = await request.body()
    if len(bruto) > _LIMITE_CORPO_BYTES:
        raise HTTPException(status_code=413, detail="corpo maior que o limite de um lote")
    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"corpo não é UTF-8: {exc}") from exc


class PingIn(BaseModel):
    versao: str | None = Field(default=None, max_length=50)


@router.post("/api/no/ping")
def ping(no: dict = Depends(no_utils.exigir_no), payload: PingIn | None = None):
    from funcoes import versao_utils

    try:
        versao_central = versao_utils.versao_instalada()
    except Exception:
        versao_central = None
    if payload and payload.versao:
        no_utils.bater(no["id_servidor"], payload.versao)
    return {"ok": True, "id_servidor": no["id_servidor"], "no": no["no_servidor"],
            "versao_central": versao_central,
            "mesma_versao": bool(versao_central) and payload is not None
                           and payload.versao == versao_central}


# ---------------------------------------------------------------------------
# Entrega da extração processada PELO NÓ (funcoes/no_entrega.py). O lote chega cru
# (application/x-ndjson) para o central reproduzir byte a byte o que o nó produziu — o corpo
# pode ter megabytes, por isso `request.body()` e não um modelo Pydantic. O estado é um
# dicionário pequeno e aberto (t = tipo do callback); o validador fica no no_entrega.
# ---------------------------------------------------------------------------


@router.post("/api/no/entrega/{id_extracao}/lote/{nu_lote}")
async def receber_lote(id_extracao: int, nu_lote: int, request: Request,
                       geracao: int | None = None,
                       no: dict = Depends(no_utils.exigir_no)):
    from fastapi import HTTPException
    from funcoes import no_entrega

    corpo = await _corpo_texto(request)
    try:
        # registrar_lote é uma transação de SEGUNDOS (reproduz até 2000 registros) — no
        # threadpool: chamada por lote de vários nós ao mesmo tempo no event loop pararia
        # a réplica inteira (esta rota é async por causa do body() em streaming)
        confirmado = await run_in_threadpool(
            no_entrega.registrar_lote, id_extracao, nu_lote, corpo, no["id_servidor"], geracao)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except no_entrega.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except no_entrega.LoteInvalidoError as exc:
        # 422 e não 500: o nó quarentena este lote e segue, em vez de reenviar o veneno
        # para sempre (deadlock de entrega em lote corrompido/desatualizado)
        raise HTTPException(status_code=422, detail=f"lote rejeitado: {exc}") from exc
    return {"recebido": confirmado}


@router.post("/api/no/entrega/{id_extracao}/estado")
def receber_estado(id_extracao: int, payload: dict,
                   no: dict = Depends(no_utils.exigir_no)):
    from fastapi import HTTPException
    from funcoes import no_entrega

    try:
        return no_entrega.aplicar_callback(id_extracao, payload, no["id_servidor"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except no_entrega.ConflictError as exc:
        # despacho substituído (geração nova): 409 leva o nó a cancelar a passada velha
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/no/samba-usuarios")
def samba_usuarios(no: dict = Depends(no_utils.exigir_no)):
    """Lista usuário→delegacia para o sincronizador SMB do NÓ (scripts/sincronizar-samba.sh).

    O nó não tem banco, então não pode rodar o SELECT do central. Ele chama aqui com o
    próprio Bearer token e recebe SÓ os usuários das delegacias que ELE atende
    (NO_TB02_SERVIDOR_DELEGACIAS) — ativos e aprovados, com a pasta já sanitizada pela
    mesma regra do app (`_sanitizar_delegacia`, ex.: FICCO/ES -> FICCO-ES). O script cria
    um symlink por login no SAMBA_HOME do nó, igual ao central, e quem não está na lista
    fica sem acesso.
    """
    from funcoes.db_utils import get_connection
    from funcoes.investigacao_utils import _sanitizar_delegacia

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT u.no_login, d.no_delegacia "
            "FROM seguranca.usr_tb01_usuarios u "
            "JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia "
            "JOIN investigacao.no_tb02_servidor_delegacias v ON v.id_delegacia = d.id_delegacia "
            "WHERE u.in_ativo AND u.in_aprovado AND v.id_servidor = %s "
            "ORDER BY u.no_login",
            (no["id_servidor"],))
        linhas = cur.fetchall()
    usuarios = []
    for login, delegacia in linhas:
        pasta = _sanitizar_delegacia(delegacia)
        if not login or not pasta:
            continue
        usuarios.append({"login": login.strip(), "delegacia": delegacia, "pasta": pasta})
    return {"usuarios": usuarios}


@router.get("/api/no/entrega/{id_extracao}")
def status_entrega(id_extracao: int, no: dict = Depends(no_utils.exigir_no)):
    """O maior lote confirmado pelo central — o nó usa ao retomar uma entrega interrompida
    (recomeça do próximo; nada é reprocessado)."""
    from fastapi import HTTPException
    from funcoes.db_utils import get_connection

    linha = None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_servidor, cd_status FROM investigacao.inq_tb02_extracoes "
                    "WHERE id_extracao = %s", (id_extracao,))
        linha = cur.fetchone()
        if linha is None or linha[0] != no["id_servidor"]:
            raise HTTPException(status_code=404, detail="extração não é deste nó")
        cur.execute("SELECT COALESCE(MAX(nu_lote), -1) FROM investigacao.no_tb03_lotes_entrega "
                    "WHERE id_extracao = %s", (id_extracao,))
        ultimo = cur.fetchone()[0]
    return {"recebido_ate": ultimo, "cd_status": linha[1]}


# ---------------------------------------------------------------------------
# Jobs de imagem rodando NO NÓ (funcoes/jobs_no.py, docs/no_descentralizado.md "jobs no nó").
# O nó é o operário que PERGUNTA: claim → plano → lote(s) de resultado → estado. Todo request
# autentica com o mesmo Bearer do heartbeat e vale como batimento (no_autenticado). O 404 em
# plano/estado é semântico para o nó: "pare de trabalhar neste job" (cancelado, liberado, morto).
# ---------------------------------------------------------------------------


class ClaimIn(BaseModel):
    limite: int = Field(default=1, ge=1, le=8)


@router.post("/api/no/jobes/claim")
def reivindicar_jobs(payload: ClaimIn, no: dict = Depends(no_utils.exigir_no)):
    from fastapi import HTTPException
    from funcoes import jobs_no

    try:
        return {"jobs": jobs_no.claim(no["id_servidor"], payload.limite)}
    except Exception as exc:
        # claim é SKIP LOCKED puro: exceção aqui é schema/DB — 500 honesto, o nó tenta de novo
        raise HTTPException(status_code=500, detail=f"claim falhou: {exc}") from exc


@router.get("/api/no/jobes/{id_job}/plano")
def job_plano(id_job: int, depois: int = 0, lote: int = 100,
              no: dict = Depends(no_utils.exigir_no)):
    from fastapi import HTTPException
    from funcoes import jobs_no

    try:
        return jobs_no.plano(no["id_servidor"], id_job, depois=depois, lote=lote)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/no/jobes/{id_job}/lote")
async def job_lote(id_job: int, request: Request, no: dict = Depends(no_utils.exigir_no)):
    """NDJSON de resultados (uma linha por imagem) — o corpo cru vai para o mesmo trilho de
    parsing da entrega de extração: sem Pydantic num payload que pode ter megabytes."""
    from fastapi import HTTPException
    from funcoes import jobs_no

    corpo = await _corpo_texto(request)
    try:
        # idem entrega: a reprodução do lote é transação longa — fora do event loop
        return await run_in_threadpool(jobs_no.aplicar_lote, no["id_servidor"], id_job, corpo)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"linha de resultado inválida: {exc}") from exc


@router.post("/api/no/jobes/{id_job}/estado")
def job_estado(id_job: int, payload: dict, no: dict = Depends(no_utils.exigir_no)):
    from fastapi import HTTPException
    from funcoes import jobs_no

    try:
        return jobs_no.estado(no["id_servidor"], id_job, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
