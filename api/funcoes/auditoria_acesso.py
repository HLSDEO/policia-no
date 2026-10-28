"""Auditoria de acesso a dado de inquérito (seguranca.usr_tb05_acessos_inquerito).

Responde "quem leu o quê, de qual inquérito, quando" — inclusive as tentativas barradas por falta
de permissão. Complementa as outras duas trilhas que já existiam: a de escrita
(24_ia.sql, quem extraiu/associou/criou) e a de IA (IA_TB04, quem perguntou o quê
ao modelo).

O gancho é `usuario_tem_acesso_inquerito` (funcoes/investigacao_utils.py), por onde passa TODA
decisão de acesso — tanto a dependency `exigir_acesso_inquerito` das rotas com id_inquerito no
path quanto as ~20 checagens inline das rotas que recebem id_conversa/id_anexo/id_entidade e
derivam o inquérito. Auditar só a dependency deixaria de fora justamente a abertura de conversa e
o download de mídia, que é o acesso que mais interessa registrar.

Como a função de checagem não conhece a rota que a chamou, o caminho da requisição vem de um
ContextVar alimentado por um middleware ASGI (`ContextoRequisicao`, registrado em main.py). Isso
evita ter que acrescentar `request: Request` na assinatura de dezenas de rotas só para auditar.
"""

import logging
import re
from contextvars import ContextVar

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# Escopo ASGI da requisição em curso; None fora de uma requisição (ex.: thread de extração, que
# também consulta permissão em alguns caminhos) — aí o recurso fica registrado como "-".
_escopo_atual: ContextVar[dict | None] = ContextVar("escopo_requisicao", default=None)

# Troca segmentos que são só dígitos por {id}: o que interessa auditar é QUAL RECURSO foi acessado
# ("conversas", "anexos/{id}/imagem"), não cada id individual — que já está na coluna do inquérito e
# que, solto no rótulo, explodiria a cardinalidade e arruinaria a agregação por hora.
_SEGMENTO_NUMERICO = re.compile(r"/\d+(?=/|$)")

_RECURSO_DESCONHECIDO = "-"
_MAX_CHARS_RECURSO = 200


class ContextoRequisicao:
    """Middleware ASGI que publica o escopo da requisição num ContextVar.

    É ASGI puro de propósito, e não `@app.middleware("http")`: aquele usa BaseHTTPMiddleware, que
    roda o restante da aplicação em outra task, e a propagação de ContextVar através dessa
    fronteira já mudou de comportamento entre versões do Starlette. Aqui não há task nova — o
    contexto vale para todo o processamento da requisição, sem depender desse detalhe.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _escopo_atual.set(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            _escopo_atual.reset(token)


# Chave onde exigir_login pendura o usuário da sessão dentro do escopo ASGI. É o MESMO dicionário
# em todas as threads da requisição (o middleware guarda a referência no ContextVar; a thread do
# threadpool recebe uma cópia do contexto, mas apontando para o mesmo dict) — por isso a dependency
# pode escrever aqui e a rota, noutra thread, lê. Rebindar o ContextVar não funcionaria: a
# atribuição morreria junto com a cópia de contexto da thread da dependency.
_CHAVE_USUARIO = "analyser_usuario"


def definir_usuario_da_requisicao(usuario: dict) -> None:
    """Publica o usuário autenticado no escopo da requisição (chamado por auth_utils.exigir_login).

    Serve a quem precisa saber QUEM está chamando lá no fundo da pilha, sem passar id_usuario por
    dez assinaturas — hoje, a configuração de IA própria do usuário (funcoes/config_ia_usuario.py).
    """
    escopo = _escopo_atual.get()
    if escopo is not None:
        escopo[_CHAVE_USUARIO] = usuario


def id_usuario_da_requisicao() -> int | None:
    """Id do usuário da requisição em curso; None fora de uma requisição (thread de extração, job
    de busca automática) ou em rota pública — nesses casos quem chama cai no padrão."""
    escopo = _escopo_atual.get()
    usuario = (escopo or {}).get(_CHAVE_USUARIO)
    return usuario.get("id_usuario") if usuario else None


def _dados_requisicao() -> tuple[str, str, str | None]:
    """(recurso normalizado, método, ip) da requisição em curso."""
    escopo = _escopo_atual.get()
    if not escopo:
        return _RECURSO_DESCONHECIDO, _RECURSO_DESCONHECIDO, None

    caminho = _SEGMENTO_NUMERICO.sub("/{id}", escopo.get("path") or "")
    metodo = escopo.get("method") or _RECURSO_DESCONHECIDO

    # mesmo X-Real-IP do limite de tentativas de login (ver rotas/auth.py): sem ele, todo acesso
    # sairia com o IP do container do nginx
    ip = None
    for chave, valor in escopo.get("headers") or []:
        if chave == b"x-real-ip":
            ip = valor.decode("latin-1").strip()[:45]
            break
    if not ip:
        cliente = escopo.get("client")
        ip = cliente[0][:45] if cliente else None

    return (caminho or _RECURSO_DESCONHECIDO)[:_MAX_CHARS_RECURSO], metodo[:10], ip


def registrar_acesso(id_usuario: int, id_inquerito: int, permitido: bool) -> None:
    """Contabiliza um acesso (ou uma tentativa negada) a dado de um inquérito. Nunca levanta.

    Agrega na janela da hora corrente: a primeira chamada da hora insere a linha, as seguintes
    somam no contador e empurram DT_ULTIMO_ACESSO (ver a UNIQUE em
    db/init/07_seguranca_usuarios.sql). Uma tela que faz polling ou uma conversa cheia de
    anexos vira UMA linha com o contador alto, em vez de centenas de linhas iguais.

    Falha de auditoria não derruba o acesso que ela está registrando — mesma escolha de
    ia_auditoria.registrar_auditoria: derrubar a investigação porque o log falhou seria pior que
    perder o registro, e o erro fica no log do servidor para ser percebido.
    """
    try:
        recurso, metodo, ip = _dados_requisicao()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seguranca.usr_tb05_acessos_inquerito
                        (id_usuario, id_inquerito, no_recurso, tp_metodo, in_permitido, co_ip, dt_janela)
                    VALUES (%s, %s, %s, %s, %s, %s, date_trunc('hour', now()))
                    ON CONFLICT (id_usuario, id_inquerito, no_recurso, tp_metodo, in_permitido, dt_janela)
                    DO UPDATE SET qt_acessos = seguranca.usr_tb05_acessos_inquerito.qt_acessos + 1,
                                  dt_ultimo_acesso = now()
                    """,
                    (id_usuario, id_inquerito, recurso, metodo, permitido, ip),
                )
            conn.commit()
    except Exception:  # auditoria nunca derruba o acesso que está registrando
        logger.exception("Falha ao gravar acesso", extra={"id_usuario": id_usuario, "id_inquerito": id_inquerito})


def listar_acessos(
    id_inquerito: int | None = None,
    id_usuario: int | None = None,
    somente_negados: bool = False,
    dias: int | None = None,
    limite: int = 100,
    deslocamento: int = 0,
) -> dict:
    """Lista as janelas de acesso (mais recentes primeiro) com filtros opcionais, mais os totais
    do filtro — devolve {'itens', 'totais', 'qt_total'}."""
    filtros = ["1=1"]
    params: dict = {"limite": limite, "deslocamento": deslocamento}
    if id_inquerito:
        filtros.append("a.id_inquerito = %(id_inquerito)s")
        params["id_inquerito"] = id_inquerito
    if id_usuario:
        filtros.append("a.id_usuario = %(id_usuario)s")
        params["id_usuario"] = id_usuario
    if somente_negados:
        filtros.append("NOT a.in_permitido")
    if dias:
        filtros.append("a.dt_janela > now() - make_interval(days => %(dias)s)")
        params["dias"] = dias
    where = " AND ".join(filtros)

    # o inquérito sai por LEFT JOIN: linha de inquérito já apagado continua valendo (é justamente
    # o caso em que a trilha importa), só fica sem o número para exibir
    query_itens = f"""
        SELECT a.id_acesso, a.id_usuario, u.no_usuario, u.no_login,
               a.id_inquerito, i.nu_inquerito,
               a.no_recurso, a.tp_metodo, a.in_permitido, a.co_ip,
               a.dt_janela, a.dt_primeiro_acesso, a.dt_ultimo_acesso, a.qt_acessos
        FROM seguranca.usr_tb05_acessos_inquerito a
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = a.id_usuario
        LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = a.id_inquerito
        WHERE {where}
        ORDER BY a.dt_ultimo_acesso DESC, a.id_acesso DESC
        LIMIT %(limite)s OFFSET %(deslocamento)s
    """
    query_totais = f"""
        SELECT count(*) AS qt_total,
               coalesce(sum(a.qt_acessos), 0) AS qt_requisicoes,
               count(*) FILTER (WHERE NOT a.in_permitido) AS qt_janelas_negadas,
               count(DISTINCT a.id_usuario) AS qt_usuarios,
               count(DISTINCT a.id_inquerito) AS qt_inqueritos
        FROM seguranca.usr_tb05_acessos_inquerito a
        WHERE {where}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_itens, params)
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
            cur.execute(query_totais, params)
            colunas = [d.name for d in cur.description]
            totais = dict(zip(colunas, cur.fetchone()))
    qt_total = totais.pop("qt_total")
    return {"itens": itens, "totais": totais, "qt_total": qt_total}
