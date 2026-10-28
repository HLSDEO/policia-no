"""Ponto de entrada da API do NÓ (docs/no_descentralizado.md).

Este repositório É o nó de delegacia: sem Postgres, sem web, sem sessão. A superfície exposta
são as rotas /no/* que o central chama (rotas/no_servico.py) + o health/versão de sonda
(rotas/sistema.py), e os laços de fundo que fazem o nó existir: heartbeat ao central, túnel NAT
e o operário de jobs da CPU local. Tudo o que é central — banco, migrações, rotas de usuário,
web — vive no repositório do central e não existe aqui.
"""

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from funcoes.auditoria_acesso import ContextoRequisicao
from rotas import no_servico, sistema

logger = logging.getLogger(__name__)

# openapi_url=None fecha o /api/openapi.json, que sem login entregava o schema inteiro da API
# (rotas, parâmetros, nome e versão da aplicação) — mapa pronto para quem faz reconhecimento
# (achado: ocultar nome/versão e superfície interna nas respostas).
app = FastAPI(title="PolícIA nó", docs_url=None, redoc_url=None, openapi_url=None)

# Publica o escopo da requisição num ContextVar (funcoes/auditoria_acesso.py). No nó não há
# auditoria para gravar — o middleware existe para o contexto existir: as rotas /no/* e os
# logs compartilhados leem estas chaves sem precisar de banco.
app.add_middleware(ContextoRequisicao)

app.include_router(sistema.router)  # health check: a sonda do instalar-host e o ponto verde
app.include_router(no_servico.router)  # /no/* — hash, parse, arquivo por faixa, extrair, importar


@app.on_event("startup")
async def _dimensionar_threadpool() -> None:
    """Ajusta o teto de requisições processadas ao mesmo tempo ao tamanho da máquina.

    TODA rota desta API é `def` síncrono (nenhuma é `async def`), então o FastAPI as despacha para
    o threadpool do AnyIO — e o limite desse threadpool É o limite de requisições concorrentes.
    O default do AnyIO é 40 e nunca foi ajustado: num servidor de 32 núcleos isso deixava a
    máquina ociosa, e num notebook 40 threads brigando pelo mesmo disco era demais.

    Precisa ser `async def`: o limiter vive num RunVar do event loop, e um handler `def` rodaria
    numa thread onde esse RunVar não existe.
    """
    import anyio.to_thread

    from funcoes import desempenho_utils

    desempenho_utils.registrar_no_log()
    limiter = anyio.to_thread.current_default_thread_limiter()
    anterior = limiter.total_tokens
    novo = desempenho_utils.valor_int("API_THREADS_REQUISICAO", int(anterior))
    if novo != anterior:
        limiter.total_tokens = novo
        logger.info("Threads de requisição: %s -> %s", anterior, novo)
    desempenho_utils.registrar_threads_aplicadas(int(limiter.total_tokens))


@app.on_event("startup")
def _preparar_no() -> None:
    """O nó não tem Postgres — nem pool, nem migrações, nem despachante. O único laço de fundo
    obrigatório dele é o heartbeat ao central (mantém o ponto verde e denuncia versão velha).
    Antes, varre os .parcial de uploads que morreram no meio do PUT (espelho do que o central
    faz com os .ufdr.parciais dele)."""
    try:
        from funcoes.no_utils import limpar_parciais_orfaos_no
        limpar_parciais_orfaos_no()
    except Exception:
        logger.exception("Falha ao varrer .parcial órfãos no nó")
    try:
        from funcoes.no_utils import limpar_spools_entrega_orfaos
        limpar_spools_entrega_orfaos()
    except Exception:
        logger.exception("Falha ao varrer spools .entrega órfãos no nó")
    # e as duas varreduras voltam periodicamente: nó que nunca reinicia não pode
    # depender do startup para manter o disco limpo
    try:
        from funcoes.no_utils import iniciar_varredura_no
        iniciar_varredura_no()
    except Exception:
        logger.exception("Falha ao ligar a varredura periódica do nó")
    try:
        from funcoes.no_utils import iniciar_heartbeat_central
        iniciar_heartbeat_central()
    except Exception:
        logger.exception("Falha ao subir o heartbeat do nó")
    # Túnel NAT: o nó abre um WebSocket ao serviço `tunel` do central para o central conseguir
    # chamá-lo mesmo atrás de NAT (docs/no_descentralizado.md). Opcional por natureza — sem o
    # serviço no central, o laço só loga uma vez e tenta de novo em backoff.
    try:
        from funcoes.tunel_no import iniciar_tunel_central
        iniciar_tunel_central()
    except Exception:
        logger.exception("Falha ao subir o túnel NAT do nó")
    # Operário de jobs (OCR/CLIP/facial) puxados do central — a CPU da estação trabalhando na
    # prova que já está no seu disco (docs/no_descentralizado.md, "jobs no nó").
    try:
        from funcoes.no_jobs import iniciar_operario_jobs
        iniciar_operario_jobs()
    except Exception:
        logger.exception("Falha ao subir o operário de jobs do nó")


@app.exception_handler(404)
async def not_found(request, exc):
    """Devolve um corpo JSON padronizado em português para rotas inexistentes — o mesmo formato
    {"detail": ...} do HTTPException, o campo que a chamada do central lê."""
    return JSONResponse(status_code=404, content={"detail": "Não encontrado."})


@app.exception_handler(Exception)
async def erro_interno(request, exc):
    """Rede de segurança para exceção não tratada em rota: sem isto, o 500 default do Starlette
    devolve texto simples (não JSON), quebrando o cliente, que sempre lê o `detail`. Também é o
    único lugar que garante que TODA exceção não tratada fica no log, com stack trace."""
    logger.exception("Exceção não tratada em %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor."})
