"""Emissão de rotas diretas de mídia no CENTRAL (docs/no_midia_direta.md).

Quem decide SE o navegador pode baixar do nó é toda aqui — é o central que autentica o usuário,
confere o inquérito e conhece o nó do arquivo. O que ele entrega ao navegador é uma URL com um
ticket de ~10 minutos (funcoes/no_ticket.py) assinado com o segredo DAQUELE nó: o nó valida o
HMAC com o NO_TOKEN que já é seu e serve o membro do .ufdr do próprio disco.

Um arquivo só vira rota direta quando TODOS os dedos apontam para o mesmo nó:

- o binário existe e não é o formato IPED (esse mora numa pasta de caso, não no .ufdr do nó);
- a fonte do .ufdr é REMOTA (fonte_ufdr devolve FonteRemota — se alguém copiou o .ufdr para o
  central, o central serve do próprio disco, mais rápido que qualquer ticket);
- o nó tem IN_MIDIA_DIRETA + TX_MIDIA_URL (o admin ligou o interruptor depois do checklist da
  TI: DNS + certificado no no-web);
- o nó reporta versão com o endpoint /no/midia (emitir ticket para nó velho é convidar o
  navegador a tomar 404 e cair no fallback — melhor devolver None logo).

Qualquer ausência devolve None e o chamador segue o caminho de sempre (proxy do central): a
mídia direta é um ATALHO otimista, nunca um pré-requisito.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from funcoes import no_ticket, no_utils, versao_utils
from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# versão em que o nó ganhou GET /no/midia + o no-web no compose — antes disso não há o que emitir
VERSAO_MINIMA_NO = (4, 9, 23)

# 10 min dá para abrir o modal e baixar um vídeo grande; renova-se com um clique (a rota pede
# outro ticket), então um prazo maior não agrega e só aumenta a janela de replay na LAN
TTL_TICKET_S = 600

LOTE_MAX = 400  # páginas grandes de galeria cabem; o resto é o front pedir em levas


def no_apto(no: dict | None) -> bool:
    """O nó tem mídia direta configurada E código para servi-la? (versão lida do heartbeat)"""
    if not no or not no.get("in_midia_direta") or not (no.get("tx_midia_url") or "").strip():
        return False
    try:
        return versao_utils.como_tupla(no.get("tx_versao") or "") >= VERSAO_MINIMA_NO
    except (TypeError, ValueError):
        return False  # versão ilegível no heartbeat é atraso — mesma regra do /verificar


def info_arquivos(ids: list[int]) -> dict[int, dict]:
    """Os campos que a rota de conteúdo usa, para uma leva de arquivos, numa consulta só."""
    if not ids:
        return {}
    consulta = """
        SELECT a.id_arquivo, a.tp_mime, a.co_caminho_zip, a.no_arquivo,
               e.no_arquivo, e.id_inquerito, e.id_extracao
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE a.id_arquivo = ANY(%s)
    """
    saida: dict[int, dict] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(consulta, (list(ids),))
            for row in cur.fetchall():
                saida[row[0]] = {
                    "tp_mime": row[1],
                    "caminho_zip": row[2],
                    "no_arquivo": row[3],
                    "no_arquivo_ufdr": row[4],
                    "id_inquerito": row[5],
                    "id_extracao": row[6],
                }
    return saida


def emitir(info: dict, download: bool = False) -> str | None:
    """URL direta (https://no-...:8443/no/midia?tk=...) para UM arquivo resolvido, ou None —
    ver módulo: só .ufdr remoto em nó apto, nunca IPED, nunca arquivo local."""
    from funcoes.investigacao_utils import fonte_ufdr  # tardio: puxa o módulo inteiro à toa

    caminho_zip = info.get("caminho_zip") or ""
    if not caminho_zip or caminho_zip.startswith("IPED::"):
        return None
    nome_ufdr = info.get("no_arquivo_ufdr")
    if not nome_ufdr:
        return None
    fonte = fonte_ufdr(nome_ufdr)
    if fonte is None or not getattr(fonte, "eh_remoto", False):
        return None  # sem arquivo, ou cópia local no central: o proxy de sempre é o caminho
    no = getattr(fonte, "no", None)
    if not no_apto(no):
        return None
    try:
        ticket = no_ticket.emitir(
            no["tx_token"],
            {"r": fonte.relativo,
             "i": caminho_zip,
             "t": (info.get("tp_mime") or "application/octet-stream")[:120],
             "d": 1 if download else 0,
             "n": (info.get("no_arquivo") or "")[:300]},
            TTL_TICKET_S)
    except ValueError:
        logger.warning("nó %s sem segredo para emitir ticket de mídia", no.get("no_servidor"))
        return None
    base = (no.get("tx_midia_url") or "").rstrip("/")
    return f"{base}/no/midia?tk={quote(ticket, safe='')}"
