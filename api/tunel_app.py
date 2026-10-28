"""Serviço TÚNEL — o relay HTTP ↔ WebSocket que destrava nós atrás de NAT.

O problema (docs/no_descentralizado.md, "nó atrás de NAT"): o desenho exige que o CENTRAL alcance
o nó em `TX_URL_BASE`. Delegacia atrás de NAT/Negócio sem porta aberta = o nó não consegue ser nó.
O nó alcança o central sempre (o heartbeat prova), então o túnel inverte o cano: o NÓ abre um
WebSocket para cá e todo pedido central → nó passa a descer por esse cano.

Por que um serviço separado e não uma rota da API: o soquete WebSocket vive no processo que o
recebeu — com 2 réplicas × N workers da API, uma chamada de leitura cairia num processo sem o
soquete 5/6 das vezes. Este app é um processo ÚNICO (porta interna 3001, nada publicado) que
segura todos os soquetes; a API comum o alcança pela rede do compose em `http://tunel:3001` e o
nó chega por `https://<central>/tunel/ws/node` (nginx faz o upgrade).

A surface é deliberadamente burra — é uma ponte, não uma aplicação:

- `WS /tunel/ws/node`           — o nó se registra (Bearer = mesmo NO_TOKEN de sempre; o relay
  autentica com `no_utils.no_autenticado`, que também bate o heartbeat);
- `ANY /t/<id_no>/no/<rota>`    — a API chama AQUI exatamente como chamaria o nó: o relay empurra
  o pedido pelo WebSocket do nó e devolve a resposta em streaming (mesmo status/headers/corpo);
- `GET /tunel/saude`            — o que o healthcheck do compose e a tela Servidores consultam.

A presença do nó vira `NO_TB01_SERVIDORES.DT_TUNEL_ATIVO` (batida no connect, a cada PING de 30 s
e limpa no disconnect): é a partir dessa coluna que `no_ponte._url()` decide túnel × HTTP direto,
de qualquer réplica da API, sem nenhum estado de processo compartilhado.

Segurança: o relay NÃO interpreta credenciais dos pedidos /t/ — ele repassa o header
Authorization que a API pôs e quem autentica de verdade é o próprio nó (comparação constante-tempo
com o NO_TOKEN dele). Sem token certo, o nó responde 401 e o relay só repassa. A autenticação do
WebSocket (nó→relay) é que exige o token, via no_utils.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from collections import defaultdict

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from funcoes import tunel_proto as proto
from funcoes import no_utils
from funcoes.db_utils import abrir_pool, fechar_pool, get_connection

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _lifespax(app: FastAPI):
    # lifespan (não on_event, deprecated no Starlette): o ciclo de vida inteiro do relay
    abrir_pool()
    logger.info("Serviço túnel no ar (relay HTTP↔WS na porta do compose).")
    yield
    fechar_pool()


app = FastAPI(title="analyser tunel", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespax)

# Soquetes ativos: id_servidor → (websocket, lock de envio). O lock por soquete é o que preserva a
# atomicidade do quadro quando dois pedidos simultâneos enviam pelo mesmo cano.
_SOQUETES: dict[int, WebSocket] = {}
_LOCKES: dict[int, asyncio.Lock] = {}
# Pedidos em aberto: req_id → fila de eventos ("head", status, headers) | ("chu", bytes) |
# ("fim",) | ("err", detalhe). O handler HTTP espera; o laço do WS produz.
_PENDENTES: dict[str, asyncio.Queue] = {}
_POR_NO: dict[int, set[str]] = defaultdict(set)

# Timeouts do relay: cabeçalho da resposta (o nó pode estar hasheando dezenas de GB — o read
# timeout antigo da ponte vinha do .env do CHAMADOR; aqui vale um teto generoso) e corpo entre
# pedaços (streaming de upload/leitura por Range é lento por natureza, não é travado).
_TIMEOUT_CABECALHO = float(os.getenv("TUNEL_TIMEOUT_CABECALHO_SEGUNDOS") or 1200)
_TIMEOUT_CORPO = float(os.getenv("TUNEL_TIMEOUT_CORPO_SEGUNDOS") or 600)
# Fila do pedido cheia NÃO significa "o chamador desistiu" — numa WAN lenta é um consumidor
# saudável, mais devagar que o nó. O relay primeiro aplica backpressure (pausa a leitura do
# cano; o TCP encarrega-se do nó) por esta graça; depois disso o consumidor está morto e o
# pedido é abortado com erro — descartar quadros em silêncio corromperia o corpo.
_GRACA_CONSUMIDOR = float(os.getenv("TUNEL_GRACA_CONSUMIDOR_SEGUNDOS") or 60)
# requisições centrais abortadas (consumidor morto) — observabilidade em /tunel/saude
_ABORTADOS = 0

# Headers de resposta que fazem sentido repassar. O corpo chega DECODIFICADO do requests
# (iter_content aplica content-encoding), então content-length/content-encoding/transfer-encoding
# do nó são apagados na origem — repassar comprimento de um corpo re-encodado mentiria.
_HEADERS_REPASSADOS = ("content-type", "content-range", "accept-ranges", "x-tamanho")


def _bater_tunel(id_servidor: int, ativo: bool) -> None:
    """Marca/desmarca DT_TUNEL_ATIVO — a ÚNICA verdade sobre "o cano deste nó está aberto". Vai ao
    banco direto (este processo é minúsculo: sem pool de requisição, uma conexão por evento
    bastam; connect/heartbeat/disconnect são poucos eventos por nó)."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            if ativo:
                cur.execute("UPDATE investigacao.no_tb01_servidores "
                            "SET dt_tunel_ativo = now(), dt_ultimo_heartbeat = now() "
                            "WHERE id_servidor = %s", (id_servidor,))
            else:
                cur.execute("UPDATE investigacao.no_tb01_servidores "
                            "SET dt_tunel_ativo = NULL "
                            "WHERE id_servidor = %s", (id_servidor,))
            # commit DENTRO do with: na saída, get_connection devolve a conexão ao pool —
            # chamar conn.commit() depois é usar uma conexão que já foi embora (o erro era
            # engolido pelo except e a coluna DT_TUNEL_ATIVO ficava congelada).
            conn.commit()
    except Exception:
        logger.exception("Falha ao marcar túnel (no=%s, ativo=%s)", id_servidor, ativo)


async def _enviar(id_no: int, tipo: int, req_id: str, meta: dict | None = None,
                  payload: bytes = b"") -> None:
    ws = _SOQUETES.get(id_no)
    if ws is None:
        raise ConnectionError("o nó fechou o túnel")
    quadro = proto.codificar(tipo, req_id, meta, payload)
    async with _LOCKES[id_no]:
        await ws.send_bytes(quadro)


async def _cancelar_cano(req_id: str, id_no: int) -> None:
    try:
        await _enviar(id_no, proto.CAN, req_id)
    except Exception:
        pass  # cano já fechado: não há para quem avisar


async def _despachar(req_id: str, evento: tuple, id_no: int | None = None,
                     espera: float | None = None) -> None:
    """Entrega um evento do cano ao pedido em aberto. Fila cheia = consumidor mais lento que
    o nó (WAN) OU morto: aplica backpressure por uma graça e, estourada, CANcela o nó e
    entrega erro ao chamador. Descartar o quadro e seguir produziria corpo corrompido em
    silêncio (checksum/zip estourariam longe daqui). `espera=0` é para a limpeza de um cano
    que já fechou — não há o que esperar."""
    fila = _PENDENTES.get(req_id)
    if fila is None:
        return
    try:
        fila.put_nowait(evento)
        return
    except asyncio.QueueFull:
        pass
    graça = _GRACA_CONSUMIDOR if espera is None else espera
    if graça > 0:
        try:
            await asyncio.wait_for(fila.put(evento), timeout=graça)
            return
        except asyncio.TimeoutError:
            pass
    if id_no is not None:
        try:
            await _enviar(id_no, proto.CAN, req_id)
        except Exception:
            pass
    # esvazia o que ainda estava em buffer e entrega o erro — o leitor vê a resposta falhar
    # em vez de um EOF truncado que pareceria sucesso
    while True:
        try:
            fila.get_nowait()
        except asyncio.QueueEmpty:
            break
    try:
        fila.put_nowait(("err", "o chamador parou de consumir a resposta pelo túnel"))
    except asyncio.QueueFull:
        pass
    global _ABORTADOS
    _ABORTADOS += 1
    logger.warning("Pedido %s abortado no relay: consumidor parado com a fila cheia", req_id)


@app.get("/tunel/saude")
def saude():
    """Probe do healthcheck e da tela: quantos túneis abertos, quais nós — e os contadores
    do próprio relay (queda de cano e requisições abortadas por consumidor parado), para a
    queda ser vista como número que cresce, não como mato sem cachorro."""
    return {"ok": True, "nos": sorted(_SOQUETES.keys()),
            "pedidos_ativos": len(_PENDENTES),
            "pedidos_abortados": _ABORTADOS}


# ------------------------------------------------------------------ o cano (nó → relay)


def _resolver_token(authorization: str | None):
    """Autentica o soquete com o mesmo trilho de no_utils (constante-tempo + heartbeat). Roda no
    threadpool: é DB e o laço do túnel é o event loop inteiro deste processo."""
    return no_utils.no_autenticado(authorization)


@app.websocket("/tunel/ws/node")
async def ws_no(ws: WebSocket):
    bruto = ws.headers.get("authorization") or ""
    if not bruto.lower().startswith("bearer "):
        # token na query vai para o log de acesso de qualquer proxy/nginx no caminho — o
        # mesmo segredo autentica os dois sentidos, então isso é vazamento. O header é o
        # trilho oficial; a query só existe se o admin ligar TUNEL_TOKEN_QUERY=1 (proxy
        # corporativo que come o header), ciente do risco.
        if (os.getenv("TUNEL_TOKEN_QUERY") or "").strip() == "1":
            token_query = ws.query_params.get("token") or ""
            bruto = f"Bearer {token_query}" if token_query else ""
    try:
        no = await run_in_threadpool(_resolver_token, bruto or None)
    except Exception as exc:
        status = getattr(exc, "status_code", 401)
        await ws.close(code=4401 if status == 401 else 4403)
        return

    id_no = no["id_servidor"]
    antigo = _SOQUETES.get(id_no)
    if antigo is not None:
        # reconexão com o cano velho ainda de pé (rede instável): o novo manda no velho — sem
        # isto o relay ficaria mandado pedidos para um soquete morto.
        try:
            await antigo.close(code=4000)
        except Exception:
            pass
    _SOQUETES[id_no] = ws
    _LOCKES[id_no] = asyncio.Lock()
    await ws.accept()
    await run_in_threadpool(_bater_tunel, id_no, True)
    logger.info("Túnel aberto pelo nó %s (%s)", id_no, no.get("no_servidor"))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            bruto_msg = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
            try:
                tipo, req_id, meta, payload = proto.decodificar(bruto_msg)
            except ValueError:
                logger.warning("Quadro malformado do nó %s — fechando o túnel", id_no)
                break
            if tipo == proto.PING:
                # batimento do cano: renova DT_TUNEL_ATIVO (a tela continua verde mesmo sem
                # tráfego; o disconnect limpa)
                await run_in_threadpool(_bater_tunel, id_no, True)
                continue
            if tipo == proto.RESP:
                await _despachar(req_id, ("head", int(meta.get("s") or 502),
                                          {str(k).lower(): str(v)
                                           for k, v in (meta.get("h") or {}).items()}), id_no)
            elif tipo == proto.CHU:
                await _despachar(req_id, ("chu", payload), id_no)
            elif tipo == proto.FIM:
                await _despachar(req_id, ("fim",), id_no)
            elif tipo == proto.ERR:
                await _despachar(req_id, ("err", str(meta.get("d") or "falha no nó")), id_no)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Túnel do nó %s morreu no meio de um quadro", id_no)
    finally:
        if _SOQUETES.get(id_no) is ws:
            _SOQUETES.pop(id_no, None)
            _LOCKES.pop(id_no, None)
            await run_in_threadpool(_bater_tunel, id_no, False)
        # todo pedido em aberto deste nó vira erro — senão o chamador esperaria o timeout inteiro
        # por um cano que já era
        for rid in _POR_NO.pop(id_no, set()):
            await _despachar(rid, ("err", "o túnel do nó fechou no meio do pedido"),
                             espera=0)
        logger.info("Túnel do nó %s fechou", id_no)


# ------------------------------------------------------------------ a ponte (API → relay → cano)

# Headers de pedido que fazem sentido atravessar o túnel. Host/Content-Length/Connection são do
# salto HTTP atual (o requests local do nó refaz); o resto (Cookie, X-Forwarded) não diz nada ao
# nó. Authorization é o único que importa — quem valida é o nó.
_HEADERS_RECEPTIVOS = ("authorization", "content-type")


def _filtrar_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() in _HEADERS_RECEPTIVOS}


@app.api_route("/t/{id_no}/no/{rota:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def repassar(id_no: int, rota: str, request: Request):
    """O espelho do /no/* do nó: recebe como a API chamaria o nó direto, devolve como o nó
    respondeu. Nada aqui conhece o conteúdo — é túnel, não aplicação."""
    ws = _SOQUETES.get(id_no)
    if ws is None:
        return JSONResponse(status_code=503,
                            content={"detail": "túnel fechado — o nó não está conectado agora "
                                               "(o cano WebSocket NAT ainda não foi aberto)"})
    req_id = uuid.uuid4().hex
    fila: asyncio.Queue = asyncio.Queue(maxsize=512)
    _PENDENTES[req_id] = fila
    _POR_NO[id_no].add(req_id)
    encerrado = False

    def _liquidar() -> None:
        nonlocal encerrado
        if not encerrado:
            encerrado = True
            _PENDENTES.pop(req_id, None)
            _POR_NO[id_no].discard(req_id)

    meta = {"m": request.method, "p": f"/no/{rota}", "q": request.url.query or "",
            "h": _filtrar_headers(request)}
    tem_corpo = request.method in ("POST", "PUT", "PATCH")
    try:
        await _enviar(id_no, proto.REQ, req_id, meta)
        if tem_corpo:
            # corpo em streaming: o relay NUNCA segura o upload inteiro (são dezenas de GB no
            # caso do .ufdr) — cada pedaço que chega do chamador vira um quadro na hora
            try:
                async for pedaco in request.stream():
                    if pedaco:
                        await _enviar(id_no, proto.CHU, req_id, None, pedaco)
            except Exception:  # o chamador sumiu no meio do upload: aborta também no nó
                try:
                    await _enviar(id_no, proto.CAN, req_id)
                except Exception:
                    pass
                _liquidar()
                return JSONResponse(status_code=499,
                                    content={"detail": "upload cancelado pelo chamador"})
        await _enviar(id_no, proto.FIM, req_id)

        try:
            evento = await asyncio.wait_for(fila.get(), timeout=_TIMEOUT_CABECALHO)
        except asyncio.TimeoutError:
            await _enviar(id_no, proto.CAN, req_id)
            _liquidar()
            return JSONResponse(status_code=504,
                                content={"detail": "o nó não respondeu ao pedido pelo túnel "
                                                   "(timeout de cabeçalho)"})
        if evento[0] == "err":
            _liquidar()
            return JSONResponse(status_code=502, content={"detail": f"túnel: {evento[1]}"})
        if evento[0] != "head":
            _liquidar()
            return JSONResponse(status_code=502, content={"detail": "túnel: resposta sem cabeçalho"})
        status, headers = evento[1], evento[2]

        async def _corpo():
            # A liquidação do pedido mora AQUI de propósito: o generator roda DEPOIS de o
            # handler retornar (o Starlette o consome durante a resposta) — um finally no
            # handler teria sumido com a fila antes do primeiro chunk chegar.
            terminou = False
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(fila.get(), timeout=_TIMEOUT_CORPO)
                    except asyncio.TimeoutError:
                        return
                    if ev[0] == "chu":
                        yield ev[1]
                    elif ev[0] == "fim":
                        terminou = True
                        return
                    else:  # ("err", ...) no meio do corpo: corta o streaming (o leitor do
                        logger.warning("Túnel: nó retornou erro no meio do corpo de %s: %s",
                                       req_id, ev[1] if len(ev) > 1 else "")
                        terminou = True
                        return   # chamador vê o EOF antecipado — é o contrato de soquete morto
            finally:
                if not terminou:
                    # o chamador sumiu no meio do download (GeneratorExit) ou o corpo morreu:
                    # avisa o nó para de ler o disco e encher o cano para ninguém — sem o CAN
                    # um .ufdr de dezenas de GB seria streamingado para o vazio até o fim.
                    # Dispara como tarefa: dentro de um generator sendo fechado, await direto
                    # seria interrompido junto pelo event loop.
                    try:
                        asyncio.get_running_loop().create_task(_cancelar_cano(req_id, id_no))
                    except RuntimeError:
                        pass
                _liquidar()

        cabecalhos = {k: v for k, v in headers.items() if k in _HEADERS_REPASSADOS}
        return StreamingResponse(_corpo(), status_code=status, headers=cabecalhos)
    except ConnectionError:
        _liquidar()
        return JSONResponse(status_code=503,
                            content={"detail": "o túnel do nó fechou no meio do pedido"})
    except Exception:
        _liquidar()
        raise
