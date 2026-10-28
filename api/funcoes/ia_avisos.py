"""Avisos in-app de conclusão da IA — "sua resposta ficou pronta", em qualquer tela do sistema.

Complementa o webhook (funcoes/ia_webhook.py), que só serve para destinos EXTERNOS (Discord,
Slack, n8n...). Aqui o aviso é do próprio sistema: qualquer página faz polling curto em
GET /api/auth/avisos-ia e mostra o toast/notificação do navegador quando um turno da IA que
interessa ao usuário logado termina — mesmo que ele tenha saído do chat e ido para outra tela.

"Interessa ao usuário" é o mesmo critério do webhook: ele enviou a pergunta (solicitante) OU
criou a conversa (criador). Cobre o chat por inquérito
(IA_TB03_PROCESSAMENTOS + IA_TB01_CONVERSAS + IA_TB02_MENSAGENS).

Sem estado novo no banco: a janela é o parâmetro "desde", que o cliente recebe pronto na resposta
anterior (campo "agora") e devolve na chamada seguinte. Como o relógio de referência é sempre o do
banco, defasagem entre o relógio do navegador e o do servidor não faz o aviso sumir nem repetir.
"""

import logging
from datetime import datetime, timedelta

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# Janela inicial (sem "desde"): o suficiente para pegar a resposta que terminou enquanto a página
# estava sendo trocada, sem ressuscitar aviso antigo em cada F5.
_JANELA_INICIAL_MIN = 3
# Teto de segurança para "desde" muito antigo (aba dormindo, notebook suspenso): evita despejar
# dezenas de toasts de uma vez ao voltar.
_JANELA_MAXIMA_MIN = 60
_LIMITE = 20

_STATUS_AVISADOS = ("concluido", "erro", "cancelado")

# Consolidações de planilha pedidas no chat (job TP_CONSOLIDACAO): o turno que dispara o job fecha
# na hora e o aviso dele (SQL abaixo) é sobre a RESPOSTA, não sobre o job — minutos depois, quando
# o .xlsx fica pronto, é este SELECT que avisa "sua planilha ficou pronta" em qualquer tela.
_SQL_JOBS = """
    SELECT j.id_job,
           j.cd_status,
           j.tx_erro,
           j.dt_fim,
           (j.tx_parametros->>'id_conversa_ia')::bigint,
           j.id_inquerito,
           coalesce(j.tx_resultado->>'no_arquivo', 'planilha consolidada')
      FROM investigacao.job_tb01_jobs j
     WHERE j.tp_job = 'consolidacao_ia'
       AND j.cd_status = ANY(%(status)s)
       AND j.dt_fim IS NOT NULL
       AND j.dt_fim > %(desde)s
       AND j.id_usuario = %(id_usuario)s
     ORDER BY j.dt_fim DESC
     LIMIT %(limite)s
"""

_SQL_INQUERITO = """
    SELECT p.id_processamento,
           p.cd_status,
           p.tx_erro,
           p.dt_fim,
           c.id_conversa_ia,
           c.id_inquerito,
           c.no_titulo,
           m.id_usuario
      FROM investigacao.ia_tb03_processamentos p
      JOIN investigacao.ia_tb01_conversas c ON c.id_conversa_ia = p.id_conversa_ia
      JOIN investigacao.ia_tb02_mensagens m ON m.id_mensagem_ia = p.id_mensagem_usuario
     WHERE p.cd_status = ANY(%(status)s)
       AND p.dt_fim IS NOT NULL
       AND p.dt_fim > %(desde)s
       AND (m.id_usuario = %(id_usuario)s OR c.id_usuario_criador = %(id_usuario)s)
      ORDER BY p.dt_fim DESC
      LIMIT %(limite)s
"""


def _parse_desde(desde: str | None, agora: datetime) -> datetime:
    """Converte o "desde" devolvido pelo cliente; ausente/inválido/antigo demais vira janela padrão."""
    piso = agora - timedelta(minutes=_JANELA_MAXIMA_MIN)
    if not desde:
        return agora - timedelta(minutes=_JANELA_INICIAL_MIN)
    try:
        dt = datetime.fromisoformat(desde.strip().replace("Z", "+00:00"))
    except ValueError:
        return agora - timedelta(minutes=_JANELA_INICIAL_MIN)
    if dt.tzinfo is not None:
        # o relógio de referência é o do banco (timestamp sem fuso); não misturamos os dois
        return agora - timedelta(minutes=_JANELA_INICIAL_MIN)
    if dt > agora:
        return agora  # relógio adiantado do cliente não pode cegar o aviso
    return max(dt, piso)


def _titulo(valor: str | None) -> str:
    valor = (valor or "").strip()
    return (valor[:120] or "Conversa sem título")


def listar(id_usuario: int | None, desde: str | None = None) -> dict:
    """Avisos de turnos de IA terminados desde `desde` que interessam ao usuário logado.

    Devolve {agora, avisos:[...]}. `agora` é o relógio do banco e deve voltar como `desde` na
    próxima chamada. Nunca levanta por causa de tabela ausente/banco antigo: devolve lista vazia.
    """
    vazio = {"agora": datetime.now().isoformat(timespec="seconds"), "avisos": []}
    if not id_usuario:
        return vazio

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT now()")
                agora = cur.fetchone()[0].replace(tzinfo=None)
                params = {
                    "status": list(_STATUS_AVISADOS),
                    "desde": _parse_desde(desde, agora),
                    "id_usuario": id_usuario,
                    "limite": _LIMITE,
                }

                avisos: list[dict] = []
                cur.execute(_SQL_JOBS, params)
                for lin in cur.fetchall():
                    if lin[4] and lin[5]:
                        link = f"ia.html?inquerito={lin[5]}&conversa={lin[4]}"
                    elif lin[4]:
                        link = f"ia.html?conversa={lin[4]}"
                    else:
                        link = f"investigacao.html?inquerito={lin[5]}"
                    avisos.append({
                        "origem": "planilha_ia",
                        "id_processamento": lin[0],  # é o id_job — a chave do aviso é (origem, id)
                        "status": lin[1],
                        "erro": (lin[2] or "")[:300] or None,
                        "dt_fim": lin[3].isoformat(timespec="seconds"),
                        "id_conversa_ia": lin[4],
                        "id_inquerito": lin[5],
                        "titulo": _titulo(lin[6]),
                        "papel": "solicitante",
                        "link": link,
                    })

                cur.execute(_SQL_INQUERITO, params)
                for lin in cur.fetchall():
                    avisos.append({
                        "origem": "chat_inquerito",
                        "id_processamento": lin[0],
                        "status": lin[1],
                        "erro": (lin[2] or "")[:300] or None,
                        "dt_fim": lin[3].isoformat(timespec="seconds"),
                        "id_conversa_ia": lin[4],
                        "id_inquerito": lin[5],
                        "titulo": _titulo(lin[6]),
                        "papel": "solicitante" if lin[7] == id_usuario else "criador",
                        "link": f"ia.html?inquerito={lin[5]}&conversa={lin[4]}",
                    })
    except Exception:
        # banco antigo / tabela de caso ausente: o aviso é acessório, não pode quebrar a página
        logger.debug("avisos de IA: falha ao consultar, devolvendo lista vazia.", exc_info=True)
        return vazio

    avisos.sort(key=lambda a: a["dt_fim"])
    return {"agora": agora.isoformat(timespec="seconds"), "avisos": avisos}
