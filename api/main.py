"""Ponto de entrada da API: cria a aplicação FastAPI e registra as rotas."""

import logging

import psycopg
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from psycopg_pool import PoolClosed, PoolTimeout

from funcoes.auditoria_acesso import ContextoRequisicao, id_usuario_da_requisicao
from funcoes.auth_utils import exigir_aprovado
from funcoes.db_utils import abrir_pool, fechar_pool
from funcoes.investigacao_utils import InqueritoDeOutraDelegacia
from funcoes.no_ponte import NoIndisponivel
from funcoes.no_utils import eh_no as _eh_no
from rotas import (
    hipoteses,
    api_pf,
    auditoria_acesso,
    auditoria_ia,
    auditoria_usuarios,
    auth,
    busca,
    busca_automatica,
    classificacao_imagem,
    comparar_celulares,
    comparar_rosto,
    configuracao_admin,
    correlacao_nuvem,
    delegacias,
    documentacao,
    documentos,
    entidades,
    entidades_auto,
    ia,
    ingestao,
    investigacao,
    jobs,
    marcadores,
    no_central,
    no_servico,
    ocr_imagem,
    padroes,
    prompts_entidade,
    rede,
    referencia,
    relatorios,
    servidores,
    simba_alertas_admin,
    sistema,
    unidades,
    usuarios,
)

# Dois papéis, um projeto (ver docs/no_descentralizado.md): o CENTRAL tem web + API + Postgres;
# o NÓ de delegacia não tem banco nem web — só as rotas /no/* que o central chama e o heartbeat.
# Toda a divisão parte deste booleano; abrir_pool/migrações/despachante só existem no central.
_PAPEL_NO = _eh_no()

logger = logging.getLogger(__name__)

# openapi_url=None fecha o /api/openapi.json, que sem login entregava o schema inteiro da API
# (rotas, parâmetros, nome e versão da aplicação) — mapa pronto para quem faz reconhecimento
# (achado: ocultar nome/versão e superfície interna nas respostas).
app = FastAPI(title="PolícIA api", docs_url=None, redoc_url=None, openapi_url=None)

# Publica o escopo da requisição num ContextVar para a auditoria de acesso saber QUAL rota foi
# chamada — a checagem de permissão que dispara o registro fica lá no fundo, em
# investigacao_utils, longe da assinatura da rota. add_middleware sem BaseHTTPMiddleware:
# ver o porquê em funcoes/auditoria_acesso.py.
app.add_middleware(ContextoRequisicao)

# sistema (health check) e auth (login/cadastro/logout) ficam públicos de propósito.
# As rotas de funcionalidade exigem sessão aprovada — quem se autocadastrou (exigir_login
# passa, exigir_aprovado não) consegue entrar mas nada mais funciona até um admin aprovar.
protegido = [Depends(exigir_aprovado)]

app.include_router(sistema.router)  # health check: os dois papéis respondem

if _PAPEL_NO:
    # Nó: só as rotas internas que o central chama (hash/parse/arquivo por faixa). Nada de web,
    # sessão ou banco — nenhuma das rotas de usuário existe neste processo.
    app.include_router(no_servico.router)
else:
    app.include_router(servidores.router)  # Administração → Servidores (nós descentralizados)
    app.include_router(no_central.router)  # /api/no/* autenticados por token de nó (ping/heartbeat)
    app.include_router(sistema.router_admin)  # já se autoprotege com exigir_admin no router
    app.include_router(auth.router)
    app.include_router(usuarios.router)  # já se autoprotege com exigir_admin no router
    app.include_router(delegacias.router)  # GET público (lista para cadastro); admin nos demais
    app.include_router(delegacias.router_admin)  # POST/PUT/DELETE exigem admin
    app.include_router(unidades.router_admin)  # árvore de unidades superiores: só Administração
    app.include_router(prompts_entidade.router)  # já se autoprotege com exigir_admin no router
    app.include_router(auditoria_ia.router)  # já se autoprotege com exigir_auditoria no router
    app.include_router(auditoria_acesso.router)  # já se autoprotege com exigir_auditoria no router
    app.include_router(auditoria_usuarios.router)  # já se autoprotege com exigir_auditoria no router
    app.include_router(configuracao_admin.router)  # já se autoprotege com exigir_admin no router
    app.include_router(api_pf.router)  # já se autoprotege com exigir_admin no router
    app.include_router(simba_alertas_admin.router)  # já se autoprotege com exigir_admin no router
    app.include_router(documentacao.router)  # já se autoprotege com exigir_admin no router
    app.include_router(ingestao.router)  # já se autoprotege com exigir_admin no router
    app.include_router(investigacao.router, dependencies=protegido)
    app.include_router(ia.router, dependencies=protegido)
    app.include_router(hipoteses.router, dependencies=protegido)
    app.include_router(referencia.router, dependencies=protegido)
    app.include_router(entidades.router, dependencies=protegido)
    app.include_router(entidades_auto.router, dependencies=protegido)  # escrita da biblioteca exige admin por rota
    app.include_router(busca.router, dependencies=protegido)
    app.include_router(busca_automatica.router, dependencies=protegido)
    app.include_router(padroes.router, dependencies=protegido)  # escrita da biblioteca exige admin por rota
    app.include_router(documentos.router, dependencies=protegido)
    app.include_router(rede.router, dependencies=protegido)
    app.include_router(relatorios.router, dependencies=protegido)  # padrões exigem admin por rota; marcações/geração conferem acesso ao inquérito
    app.include_router(marcadores.router, dependencies=protegido)  # marcadores são pessoais: cada rota confere o dono além do acesso ao inquérito
    app.include_router(classificacao_imagem.router, dependencies=protegido)
    app.include_router(classificacao_imagem.router_admin)  # categorias são configuração global: escrita só de admin
    app.include_router(ocr_imagem.router, dependencies=protegido)
    app.include_router(comparar_rosto.router, dependencies=protegido)
    app.include_router(comparar_celulares.router, dependencies=protegido)
    app.include_router(correlacao_nuvem.router, dependencies=protegido)
    app.include_router(jobs.router, dependencies=protegido)  # cada rota resolve o inquérito do job e confere acesso
    app.include_router(jobs.router_admin)  # já se autoprotege com exigir_admin no router


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
    # Guarda o que de fato ficou valendo: a tela Desempenho mostra ESTE número na coluna
    # "em vigor", e é comparando com ele que ela sabe dizer que falta reiniciar.
    desempenho_utils.registrar_threads_aplicadas(int(limiter.total_tokens))


@app.on_event("startup")
def _preparar_banco() -> None:
    import os

    if _PAPEL_NO:
        # O nó não tem Postgres — nem pool, nem migrações, nem despachante. O único laço de
        # fundo dele é o heartbeat ao central (mantém o ponto verde e denuncia versão velha).
        # Antes, varre os .parcial de uploads que morreram no meio do PUT (espelho do que o
        # central faz com os .ufdr.parciais dele).
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
        return

    abrir_pool()

    # Aplica os deltas de db/migracoes/ que ainda não rodaram — ANTES de qualquer consulta desta
    # subida, para nenhuma rota quebrar em runtime por coluna/tabela que a migração ainda não
    # criou (o erro de deploy mais comum). MIGRAR_NO_STARTUP=0 no .env desliga (deploy que
    # prefere rodar `python /app/tools/migrar.py` na mão). Ver funcoes/migrar_utils.py.
    if os.getenv("MIGRAR_NO_STARTUP", "1") != "0":
        try:
            from funcoes import migrar_utils

            resultado = migrar_utils.executar(aplicada_por="startup")
            if resultado["erro"]:
                # Subir com schema incompleto é o cenário que este bloco existe para evitar —
                # falha o startup com a causa no log, em vez de quebrar na primeira rota.
                raise RuntimeError(f"migração pendente falhou: {resultado['erro']}")
            if resultado["aplicadas"]:
                logger.info("Migrações aplicadas no startup: %s", ", ".join(resultado["aplicadas"]))
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Falha inesperada ao aplicar migrações no startup")
            raise

    # Extrações que ficaram em 'processando' quando o processo anterior morreu. NÃO é "tudo que
    # está ativo é órfão": com API_WORKERS > 1 há outros processos vivos ao lado, e as extrações
    # 'pendente' agora são a FILA, não threads mortas. Quem decide é o predicado temporal dentro
    # de liberar_extracoes_travadas — não mexa nele sem ler o alerta que está lá.
    try:
        from funcoes.investigacao_utils import liberar_extracoes_travadas
        liberar_extracoes_travadas()
    except Exception:
        logger.exception("Falha ao liberar extrações presas no startup")

    # Uploads de .ufdr interrompidos por um processo que morreu deixam um `.ufdr.parcial` em
    # data/ (o nome final só nasce no os.replace de salvar_ufdr_enviado). Só os antigos são
    # removidos — um upload em andamento em outro worker não pode ser apagado por engano.
    try:
        from funcoes.investigacao_utils import limpar_ufdr_parciais_orfas
        limpar_ufdr_parciais_orfas()
    except Exception:
        logger.exception("Falha ao limpar .ufdr.parcial órfão no startup")

    # Mesmo raciocínio para os jobs de imagem (OCR, classificação, comparação facial): as threads
    # que os tocavam morreram com o processo anterior. O predicado é temporal, não "tudo que está
    # ativo" — ver o alerta em jobs_utils.liberar_jobs_travados.
    try:
        from funcoes import jobs_utils
        jobs_utils.liberar_jobs_travados()
    except Exception:
        logger.exception("Falha ao liberar jobs presos no startup")

    # Mesma faxina para as análises de documento anexado (cd_analise): a fila vive na memória do
    # processo, então um restart no meio deixava a linha presa em 'processando' para sempre — e
    # um documento preso congela o card do inquérito inteiro em "Processando…" (ver
    # liberar_analises_documentos_travadas). O watchdog por trás do gancho cobre também o worker
    # que morre sozinho (API_WORKERS > 1), caso em que não há startup.
    try:
        from funcoes.documentos_utils import (
            iniciar_watchdog_analises,
            liberar_analises_documentos_travadas,
            reenfileirar_analises_pendentes,
        )
        liberar_analises_documentos_travadas()
        reenfileirar_analises_pendentes()
        iniciar_watchdog_analises()
    except Exception:
        logger.exception("Falha ao liberar análises de documento presas no startup")

    # Despachante da fila de extrações: é ele que tira as 'pendente' da fila conforme abre vaga.
    # Subir aqui também é o que faz uma extração enfileirada sobreviver a um restart — antes, a
    # thread por extração morria com o processo e a linha só virava 'erro' 60 min depois.
    try:
        from funcoes.investigacao_utils import iniciar_despachante_extracoes
        iniciar_despachante_extracoes()
    except Exception:
        logger.exception("Falha ao subir o despachante de extrações")

    # Agendador do passe noturno (resumo por inquérito). Sobe aqui para o sistema não depender de
    # um agendador instalado no host — que era o caso antes, e só no Ubuntu com o systemd
    # instalado à mão. A thread é barata (um SELECT a cada 5 min) e só um worker roda o passe:
    # a vaga do dia é uma linha em AGD_TB01_EXECUCOES. Ver funcoes/agendador.py.
    try:
        from funcoes.agendador import iniciar_agendador
        iniciar_agendador()
    except Exception:
        logger.exception("Falha ao subir o agendador do resumo noturno")

    # Cache da convergência entre inquéritos: a consulta pesada (cruzamento de nomes por
    # trigrama, GPS, co-presença) passou de 100 s em produção e a tela nunca abria. O cálculo
    # roda aqui, num laço líder de hora em hora, e a tela lê a tabela pronta
    # (investigacao.conv_tb01_sinais). Ver funcoes/canonicas_utils.py e docs/convergencia.md.
    try:
        from funcoes.canonicas_utils import iniciar_cache_convergencia
        iniciar_cache_convergencia()
    except Exception:
        logger.exception("Falha ao subir o cache da convergência")


@app.on_event("shutdown")
def _fechar_pool_banco() -> None:
    fechar_pool()


@app.exception_handler(404)
async def not_found(request, exc):
    """Devolve um corpo JSON padronizado em português para rotas inexistentes.

    Mesmo formato {"detail": ...} do HTTPException (ver handler de Exception abaixo) —
    é o campo que todo o frontend lê (xhr.responseJSON.detail)."""
    return JSONResponse(status_code=404, content={"detail": "Não encontrado."})


def _contexto_erro(request) -> dict:
    """Campos que acompanham o erro no log, via `extra`.

    O JsonFormatter promove qualquer chave de `extra` para o JSON da linha
    (funcoes/logging_config.py), então cada erro em logs/erros.log passa a dizer qual rota, qual
    método e qual analista — sem isso o arquivo é uma pilha de stack traces sem dono, o que com
    trinta pessoas usando ao mesmo tempo é quase inútil. O id do usuário vem do escopo publicado
    por exigir_login; é None em rota pública."""
    try:
        return {
            "rota": request.url.path,
            "metodo": request.method,
            "id_usuario": id_usuario_da_requisicao(),
        }
    except Exception:  # nada aqui pode impedir a resposta de erro de sair
        return {}


# Falhas de infraestrutura do banco traduzidas em respostas que dizem o que houve, em vez do 500
# genérico do handler abaixo. Os três precisam LOGAR: sem isso um pool esgotado — justamente o
# sintoma que mais aparece com muita gente ao mesmo tempo — vira um 503 silencioso, invisível no
# arquivo de erros. O Starlette escolhe o handler pelo MRO da exceção, e as três são irmãs sob
# OperationalError, então nenhuma rouba a outra.

@app.exception_handler(PoolTimeout)
async def banco_sem_conexao(request, exc):
    """Todas as conexões do pool ocupadas por mais de DB_POOL_TIMEOUT (ver db_utils)."""
    logger.error("Pool de conexões esgotado em %s %s", request.method, request.url.path,
                 exc_info=exc, extra=_contexto_erro(request))
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "O sistema está com todas as conexões de banco ocupadas — alguém pode "
                           "estar importando um arquivo grande. Tente de novo em alguns segundos."},
    )


@app.exception_handler(PoolClosed)
async def pool_fechado(request, exc):
    """Pool fechado no meio da vida do processo (o open() inicial estourou o prazo e o
    psycopg_pool fechou o pool). db_utils já descarta o pool quebrado e refaz na próxima
    chamada — aqui é só a resposta honesta para ESTA requisição, em vez do 500 genérico."""
    logger.error("Pool de conexões fechado em %s %s", request.method, request.url.path,
                 exc_info=exc, extra=_contexto_erro(request))
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "O banco de dados não está respondendo — o sistema vai tentar "
                           "reconectar sozinho. Tente de novo em alguns segundos."},
    )


@app.exception_handler(psycopg.errors.QueryCanceled)
@app.exception_handler(psycopg.errors.IdleInTransactionSessionTimeout)
async def banco_demorou(request, exc):
    """statement_timeout ou idle_in_transaction_session_timeout estourado."""
    logger.error("Consulta abortada por timeout em %s %s", request.method, request.url.path,
                 exc_info=exc, extra=_contexto_erro(request))
    return JSONResponse(
        status_code=504,
        content={"detail": "A consulta demorou mais do que o limite e foi interrompida. "
                           "Reduza o período ou os filtros e tente de novo."},
    )


@app.exception_handler(InqueritoDeOutraDelegacia)
async def inquerito_de_outra_delegacia(request, exc):
    """Tentativa de anexar/extrair para um inquérito de outra unidade (ver
    investigacao_utils._conferir_delegacia_do_inquerito).

    Handler global e não `try/except` em cada rota porque são cinco rotas que chegam lá
    (extrair, extrair-documento, extrair-rif, extrair-simba, extrair-iped) e uma sexta futura
    não pode esquecer de traduzir. Fica no log como aviso: não é erro do sistema, mas é
    exatamente o evento que interessa a quem audita."""
    logger.warning("Bloqueado acesso a inquérito de outra delegacia em %s %s: %s",
                   request.method, request.url.path, exc, extra=_contexto_erro(request))
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(psycopg.errors.LockNotAvailable)
async def banco_travado(request, exc):
    """lock_timeout estourado — tipicamente uma migração de schema segurando a tabela."""
    logger.error("Lock indisponível em %s %s", request.method, request.url.path,
                 exc_info=exc, extra=_contexto_erro(request))
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "O registro está sendo alterado por outra operação. Tente de novo em "
                           "alguns segundos."},
    )


@app.exception_handler(NoIndisponivel)
async def no_fora_do_ar(request, exc):
    """O nó descentralizado não atendeu (fora do ar, link fino saturado, túnel fechado no meio,
    índice do .ufdr ainda descendo): é 503 com Retry-After e a mensagem diz QUAL servidor e o que
    esperar — Handler global pelo mesmo motivo do inquérito de outra delegacia: meia dúzia de
    rotas de anexo/toff de nó levantam a exceção e a sexta não pode esquecer de traduzir; sem
    ela o analista via 500 seco ("Erro interno do servidor.") por um link congestionado."""
    logger.warning("Nó indisponível em %s %s: %s", request.method, request.url.path, exc,
                   extra=_contexto_erro(request))
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "15"},
        content={"detail": str(exc)},
    )


@app.exception_handler(Exception)
async def erro_interno(request, exc):
    """Rede de segurança para exceção não tratada em rota: sem isto, o 500 default do Starlette
    devolve texto simples (não JSON), quebrando o front, que sempre lê xhr.responseJSON.detail.
    Também é o único lugar que garante que TODA exceção não tratada fica no log, com stack trace —
    antes dela simplesmente sumia numa rota que só fazia "except Exception: pass"."""
    logger.exception("Exceção não tratada em %s %s", request.method, request.url.path,
                     extra=_contexto_erro(request))
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor."})
