"""Webhook de conclusão da IA — aviso simples quando o turno termina.

Cada usuário cadastra a SUA url ("Meu webhook IA" no menu do usuário) e passa a receber um POST
JSON sempre que um turno da IA termina — concluído, erro ou cancelado — nos chats que lhe dizem
respeito: a pergunta que ELE enviou (solicitante) ou a conversa que ELE criou (criador). Vale para
o chat de IA por inquérito (ia.html + painel flutuante ia_flutuante.js + mesa): funcoes.ia_utils.

O payload é SÓ aviso (sem pergunta/resposta completas, de propósito — dado de investigação não
trafega para fora sem necessidade):

    {evento, origem, status, id_conversa_ia, id_inquerito, titulo, id_processamento,
     id_usuario_solicitante, papel, erro?, dt_evento, link}

Disparo é fire-and-forget em thread daemon, com timeout curto: webhook lento ou fora do ar nunca
trava nem quebra o turno da IA. Sem URL cadastrada/ativa, nada é enviado (silencioso). A tabela
pode ainda não existir (banco antigo sem a migração 0005): nesse caso as leituras devolvem None e
as escritas levantam erro amigável pedindo para aplicar as migrações.
"""

import logging
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

_TIMEOUT_SEG = 10
_MAX_URL_CHARS = 2000

_EVENTO_POR_STATUS = {
    "concluido": "ia.resposta_concluida",
    "erro": "ia.resposta_erro",
    "cancelado": "ia.resposta_cancelada",
}


def validar_url(url: str) -> str:
    """Normaliza e valida a URL do webhook; levanta ValueError se inválida."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Informe a URL do webhook.")
    if len(url) > _MAX_URL_CHARS:
        raise ValueError("URL do webhook muito longa (máximo 2000 caracteres).")
    partes = urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ValueError("URL do webhook inválida — use http(s)://host/caminho.")
    return url


def obter(id_usuario: int | None) -> dict | None:
    """{url, ativo} do webhook do usuário, ou None (sem cadastro, inativo sem URL, sem tabela)."""
    if not id_usuario:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tx_webhook_url, in_ativo FROM seguranca.usr_tb07_webhook_ia WHERE id_usuario = %s",
                    (id_usuario,),
                )
                linha = cur.fetchone()
    except Exception:
        # banco ainda sem a migração 0005 — não quebra o turno da IA por causa disso
        logger.debug("webhook IA: tabela ausente (migração 0005 pendente?), ignorando.", exc_info=True)
        return None
    if not linha or not (linha[0] or "").strip():
        return None
    return {"url": linha[0].strip(), "ativo": bool(linha[1])}


def salvar(id_usuario: int, url: str, ativo: bool = True) -> None:
    """Cria/substitui o webhook do usuário. URL vazia = remover (apaga a linha)."""
    url = (url or "").strip()
    if not url:
        remover(id_usuario)
        return
    url = validar_url(url)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seguranca.usr_tb07_webhook_ia (id_usuario, tx_webhook_url, in_ativo, dt_atualizacao)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (id_usuario) DO UPDATE SET
                        tx_webhook_url = EXCLUDED.tx_webhook_url,
                        in_ativo = EXCLUDED.in_ativo,
                        dt_atualizacao = now()
                    """,
                    (id_usuario, url, bool(ativo)),
                )
            conn.commit()
    except Exception as exc:
        logger.exception("webhook IA: falha ao salvar para o usuário %s", id_usuario)
        msg = str(exc)
        if "does not exist" in msg or "não existe" in msg.lower() or "usr_tb07" in msg:
            raise ValueError("Tabela de webhook ainda não criada — aplique as migrações pendentes e tente de novo.")
        raise


def remover(id_usuario: int) -> bool:
    """Apaga o webhook do usuário; devolve se havia algo."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM seguranca.usr_tb07_webhook_ia WHERE id_usuario = %s", (id_usuario,))
            except Exception:
                logger.debug("webhook IA: tabela ausente ao remover, ignorando.", exc_info=True)
                return False
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def _post(url: str, payload: dict) -> None:
    """POST JSON com timeout curto; erro só vai para o log (nunca levanta)."""
    try:
        import requests

        resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEG)
        if resp.status_code >= 400:
            logger.warning("webhook IA: %s respondeu HTTP %s", url[:80], resp.status_code)
    except Exception:
        logger.warning("webhook IA: falha ao entregar em %s", (url or "")[:80], exc_info=True)


def _disparar_para(id_usuario: int | None, payload: dict) -> bool:
    """Agenda o POST para o webhook do usuário, se houver URL ativa. Não bloqueia, não levanta."""
    if not id_usuario:
        return False
    cfg = obter(id_usuario)
    if not cfg or not cfg["ativo"] or not cfg["url"]:
        return False
    threading.Thread(target=_post, args=(cfg["url"], payload), daemon=True).start()
    return True


def _payload_base(
    status: str, origem: str, id_conversa_ia: int, id_processamento: int,
    id_usuario_solicitante: int | None, titulo: str | None, erro: str | None = None,
) -> dict:
    return {
        "evento": _EVENTO_POR_STATUS.get(status, "ia.resposta_concluida"),
        "origem": origem,  # "chat_inquerito"
        "status": status,  # "concluido" | "erro" | "cancelado"
        "id_conversa_ia": id_conversa_ia,
        "id_processamento": id_processamento,
        "id_usuario_solicitante": id_usuario_solicitante,
        "titulo": (titulo or "")[:120],
        "erro": (erro or "")[:500] if erro else None,
        "dt_evento": datetime.now(timezone.utc).isoformat(),
    }


def _criador_conversa_inquerito(id_conversa_ia: int) -> int | None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id_usuario_criador FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s",
                    (id_conversa_ia,),
                )
                linha = cur.fetchone()
                return int(linha[0]) if linha and linha[0] is not None else None
    except Exception:
        logger.debug("webhook IA: falha ao buscar criador (inquérito).", exc_info=True)
        return None


def _interessados(id_solicitante: int | None, id_criador: int | None) -> list[tuple[int, str]]:
    """Quem deve ser avisado: solicitante + criador (sem duplicar). Com o papel de cada um."""
    alvos: list[tuple[int, str]] = []
    vistos: set[int] = set()
    if id_solicitante and id_solicitante not in vistos:
        vistos.add(id_solicitante)
        alvos.append((id_solicitante, "solicitante"))
    if id_criador and id_criador not in vistos:
        vistos.add(id_criador)
        alvos.append((id_criador, "criador"))
    return alvos


def notificar_inquerito(
    status: str, id_conversa_ia: int, id_inquerito: int, id_processamento: int,
    id_usuario_solicitante: int | None, titulo: str | None = None, erro: str | None = None,
) -> int:
    """Avisa solicitante + criador sobre o fim do turno no chat por inquérito. Devolve qts agendados."""
    base = _payload_base(status, "chat_inquerito", id_conversa_ia, id_processamento, id_usuario_solicitante, titulo, erro)
    base["id_inquerito"] = id_inquerito
    base["link"] = f"ia.html?inquerito={id_inquerito}"
    enviados = 0
    try:
        criador = _criador_conversa_inquerito(id_conversa_ia)
    except Exception:
        criador = None
    for id_destino, papel in _interessados(id_usuario_solicitante, criador):
        payload = dict(base)
        payload["papel"] = papel
        try:
            if _disparar_para(id_destino, payload):
                enviados += 1
        except Exception:
            logger.exception("webhook IA: falha inesperada ao agendar (inquérito).")
    return enviados


def enviar_teste(id_usuario: int) -> dict:
    """Dispara um evento de teste para o webhook cadastrado do usuário (síncrono, para dar feedback)."""
    cfg = obter(id_usuario)
    if not cfg or not cfg["url"]:
        raise ValueError("Nenhum webhook cadastrado — salve a URL antes de testar.")
    payload = {
        "evento": "ia.webhook_teste",
        "origem": "teste",
        "status": "teste",
        "mensagem": "Teste do webhook de conclusão da IA — se você recebeu isto, está funcionando.",
        "dt_evento": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import requests

        resp = requests.post(cfg["url"], json=payload, timeout=_TIMEOUT_SEG)
        return {"ok": resp.status_code < 400, "http_status": resp.status_code}
    except Exception as exc:
        return {"ok": False, "erro": str(exc)[:300]}
