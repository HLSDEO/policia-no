"""Fluxo de eventos (SSE) do turno de IA em andamento.

O front acompanhava o turno com um GET a cada 2,5 s no status do processamento: a etapa atual
("Lendo as conversas do WhatsApp…") só aparecia na batida seguinte, e um turno de dez minutos
custava umas 240 requisições autenticadas por analista. Aqui a mesma informação sai por uma
conexão só, empurrada assim que muda.

Quem observa o banco continua sendo o servidor — o que se ganha é latência (sub-segundo em vez de
2,5 s) e o fim do vaivém HTTP, não uma notificação do Postgres: um LISTEN/NOTIFY prenderia uma
conexão do pool por analista com o chat aberto, que é justamente o recurso escasso (ver
_POOL_MAX_SIZE em funcoes/db_utils.py). A leitura roda em thread (run_in_threadpool) para o laço
de eventos não travar no I/O do banco.

Genérico de propósito (recebe a função que lê o status): serve o chat do inquérito e, com a mesma
assinatura, o do caso e o de documentos.
"""

import asyncio
import json
import logging
import time
from typing import Callable

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# Cabeçalhos que fazem o SSE chegar inteiro do outro lado do nginx. X-Accel-Buffering é o que
# desliga o proxy_buffering (ligado por padrão) só para esta resposta: sem ele o nginx segura os
# eventos no buffer e o front recebe tudo de uma vez, no fim — exatamente o que o SSE evita.
CABECALHOS_SSE = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Ritmo da leitura no banco: apertado no começo (é quando as etapas se sucedem e alguém está
# olhando) e mais espaçado depois dos primeiros minutos, quando o turno virou map-reduce longo e
# cada parte demora.
_INTERVALO_CURTO_S = 0.7
_INTERVALO_LONGO_S = 2.0
_SEGUNDOS_ATE_ESPACAR = 120.0

# Comentário SSE periódico: mantém a conexão viva através do proxy_read_timeout de 300 s do nginx
# e faz o navegador perceber na hora se a conexão morreu.
_INTERVALO_KEEPALIVE_S = 15.0

# Teto de vida do fluxo. Um turno que passa disso é anormal (worker preso); o front cai no polling
# de reserva em vez de segurar a conexão para sempre.
_TETO_S = 3600.0

_STATUS_FINAIS = ("concluido", "cancelado", "erro")


def _evento(nome: str, dado: dict) -> str:
    return f"event: {nome}\ndata: {json.dumps(dado, ensure_ascii=False, default=str)}\n\n"


async def fluxo_processamento(obter_status: Callable[[int], dict | None], id_processamento: int):
    """Gera o corpo do SSE de um processamento até ele terminar (ou o cliente desconectar).

    Eventos:
      - ``estado``: o mesmo dicionário que a rota de polling devolve, emitido só quando algum
        campo muda (status, etapa atual, lotes concluídos) — o front reaproveita o tratamento.
      - ``fim``: o fluxo se encerrou sem status final (processamento sumiu, ou teto de tempo);
        cd_status diz qual dos dois, e o front volta ao polling.
    """
    anterior: dict | None = None
    inicio = time.monotonic()
    ultimo_envio = inicio
    while True:
        proc = await run_in_threadpool(obter_status, id_processamento)
        if proc is None:
            # apagado no meio do caminho (conversa removida em outra aba): não há o que acompanhar
            yield _evento("fim", {"cd_status": "desconhecido"})
            return

        if proc != anterior:
            anterior = proc
            ultimo_envio = time.monotonic()
            yield _evento("estado", proc)
            if proc.get("cd_status") in _STATUS_FINAIS:
                return
        elif time.monotonic() - ultimo_envio >= _INTERVALO_KEEPALIVE_S:
            ultimo_envio = time.monotonic()
            yield ": keepalive\n\n"

        decorrido = time.monotonic() - inicio
        if decorrido >= _TETO_S:
            logger.warning(
                "SSE de IA encerrado pelo teto de tempo", extra={"id_processamento": id_processamento}
            )
            yield _evento("fim", {"cd_status": "tempo_esgotado"})
            return
        await asyncio.sleep(_INTERVALO_CURTO_S if decorrido < _SEGUNDOS_ATE_ESPACAR else _INTERVALO_LONGO_S)
