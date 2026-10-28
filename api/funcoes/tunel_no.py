"""Cliente do túnel reverso — roda DENTRO DO NÓ (docs/no_descentralizado.md, "túnel NAT").

O nó alcança o central (é assim que o heartbeat sobrevive a qualquer NAT); este módulo vira essa
estrada de mão única em uma ponte de dois sentidos: um WebSocket persistente até o serviço `tunel`
do central (`<CENTRAL_URL>/tunel/ws/node`) que recebe pedidos FRAME-by-FRAME como se fossem
requisições HTTP e os executa LOCALMENTE contra o próprio uvicorn do nó (`127.0.0.1:3000/no/*`) —
mesmo código, mesmos guards, mesma autenticação de sempre; o túnel é só um novo cano de chegada.

Por que executar contra o próprio HTTP em vez de chamar as funções direto: as rotas de
`no_servico.py` fazem o parse de query, o guard de caminho, o streaming de resposta e a autenti-
cação Bearer que o central já espera encontrar. Reimplementar isso dentro do cliente seria um
segundo `/no/*` para manter em dia para sempre. Aqui a paridade é estrutural: se a rota funciona
pela porta publicada, funciona pelo túnel.

Corpo nos dois sentidos em streaming: o upload repassado (PUT /no/enviar, dezenas de GB) chega em
pedaços por quadros CHU e entra no requests local por um gerador que lê de uma fila — nada é
materializado inteiro em RAM aqui nem lá.

Processo único: com API_WORKERS > 1 cada worker rodaria o seu túnel e o relay veria conexões se
substituírem em loop. Um flock em /tmp elege o worker túnel; os outros seguem servindo /no/* pela
porta como sempre (o túnel é um caminho A MAIS, nunca o único).
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import random
import threading

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover — fora do container (dev Windows) não há túnel nem flock
    fcntl = None

from funcoes import tunel_proto as proto

logger = logging.getLogger(__name__)

# A fila de corpo em trânsito (central→nó) é thread-safe: o laço do WS (event loop) enche, o
# gerador do requests (thread) consome. None = FIM do corpo.
_CORPOS: dict[str, queue.Queue] = {}
_CANCELADOS: dict[str, threading.Event] = {}
_TRAVA_ESTADO = threading.Lock()

_PING_INTERVALO = 30.0
_LOCAL = "http://127.0.0.1:3000"
_MAX_QUADRO = 16 * 1024 * 1024  # 8 MiB de faixa + folga de headers — acima disso é bug, não tráfego
# Fila de corpo LIMITADA: sem teto, um uvicorn local lento (disco ocupado, upload preso)
# acumulava o upload inteiro em RAM até o OOM matar a estação. Cheia, o laço do WS aplica
# backpressure ao relay (o put bloqueia a leitura do cano → TCP segura o relay) — dentro de
# uma graça; passada dela, quem consome está morto e o corpo é abortado, nunca enfileirado.
_FILA_CORPO_MAX = 128          # ~128 MB no pior caso (chunks de 1 MiB do relay)
_FILA_CORPO_GRAÇA_S = 60       # put bloqueado no laço: pausa o cano, não estoura a fila
_FILA_CORPO_FIM_S = 300        # gerador local órfão (relay sem FIM): a thread se solta em 5 min


def _lock_path() -> str:
    return os.path.join(os.getenv("TMPDIR") or "/tmp", "analyser-tunel.lock")


def _eh_worker_tunel() -> bool:
    """O flock não-bloqueante elege UM worker por container para segurar o túnel. O kernel solta o
    lock se o processo morrer — um `restart` do container reelege naturalmente."""
    if fcntl is None:
        return True  # dev fora do Linux: sem multiprocessamento real, vale o próprio processo
    try:
        fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def iniciar_tunel_central() -> None:
    """Só no papel `no`: liga o laço do túnel em thread daemon. Sem CENTRAL_URL/NO_TOKEN é
    desligado em silêncio (o heartbeat já avisa no log que faltam); TUNEL_NO_ATIVO=0 no .env
    desliga de propósito em um nó específico."""
    if (os.getenv("TUNEL_NO_ATIVO") or "").strip() == "0":
        logger.info("Túnel NAT desligado (TUNEL_NO_ATIVO=0).")
        return
    if not _eh_worker_tunel():
        logger.info("Outro worker deste container já segura o túnel — esta processo só atende /no/.")
        return
    central = (os.getenv("CENTRAL_URL") or "").strip().rstrip("/")
    token = (os.getenv("NO_TOKEN") or "").strip()
    if not central or not token:
        return  # o heartbeat já loga a falta dessas chaves
    thread = threading.Thread(target=_laço_processo, args=(central, token),
                              daemon=True, name="no-tunel")
    thread.start()


def _ws_url(central: str) -> str:
    if central.startswith("https://"):
        return "wss://" + central[len("https://"):] + "/tunel/ws/node"
    if central.startswith("http://"):
        return "ws://" + central[len("http://"):] + "/tunel/ws/node"
    return central.rstrip("/") + "/tunel/ws/node"  # wss:// explícito no .env


def _laço_processo(central: str, token: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_laço_conexões(central, token))
    except Exception:
        logger.exception("O laço do túnel morreu — nó segue só com heartbeat/porta publicada")
    finally:
        loop.close()


async def _laço_conexões(central: str, token: str) -> None:
    import websockets

    url = _ws_url(central)
    cabecalhos = {"Authorization": f"Bearer {token}"}
    atraso = 1.0
    mostrar_erro = True
    while True:
        try:
            try:
                ctx = websockets.connect(url, additional_headers=cabecalhos,
                                         max_size=_MAX_QUADRO, ping_interval=20,
                                         ping_timeout=60, close_timeout=5)
            except TypeError:  # websockets < 12 chamava extra_headers
                ctx = websockets.connect(url, extra_headers=cabecalhos,
                                         max_size=_MAX_QUADRO, ping_interval=20,
                                         ping_timeout=60, close_timeout=5)
            async with ctx as ws:
                logger.info("Túnel NAT conectado em %s", url)
                atraso = 1.0
                mostrar_erro = True
                await _sessão(ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 4401/4403 = relay recusou o token (revogado/nó inativado) — repetir de minuto em
            # minuto não conserta; espaçar mais e falar uma vez só por fase de queda
            if mostrar_erro:
                logger.warning("Túnel NAT fora do ar (%s) — tentando de novo em %.0f s. "
                               "Se o central ainda não tem o serviço 'tunel', o nó funciona "
                               "normalmente pela porta publicada.", exc, atraso)
                mostrar_erro = False
            await asyncio.sleep(atraso + random.random() * atraso * 0.3)
            atraso = min(60.0, atraso * 2)


async def _sessão(ws) -> None:
    """Soquete aberto: um laço recebe, tarefas executam, um coroutine bate o PING do cano."""
    lock_envio = asyncio.Lock()
    tarefas: set[asyncio.Task] = set()
    ping = asyncio.create_task(_pingueiro(ws, lock_envio))
    try:
        async for bruto in ws:
            try:
                tipo, req_id, meta, payload = proto.decodificar(bruto)
            except ValueError:
                logger.warning("Quadro malformado do relay — reabrindo o túnel")
                break
            if tipo == proto.REQ:
                # registra o canal do corpo ANTES de despachar: o relay pode emendar o CHU
                # seguinte no mesmo tick do event loop
                with _TRAVA_ESTADO:
                    _CORPOS[req_id] = queue.Queue(maxsize=_FILA_CORPO_MAX)
                    _CANCELADOS[req_id] = threading.Event()
                tarefa = asyncio.create_task(_executar_local(ws, lock_envio, req_id, meta))
                tarefas.add(tarefa)
                tarefa.add_done_callback(tarefas.discard)
            elif tipo == proto.CHU:
                fila = _CORPOS.get(req_id)
                if fila is not None:
                    await _encher_corpo(req_id, fila, payload)
            elif tipo == proto.FIM:
                fila = _CORPOS.get(req_id)
                if fila is not None:
                    await _encher_corpo(req_id, fila, None)
            elif tipo == proto.CAN:
                ev = _CANCELADOS.get(req_id)
                if ev is not None:
                    ev.set()
                fila = _CORPOS.get(req_id)
                if fila is not None:
                    # corpo abortado: o gerador local termina e o requests fecha
                    try:
                        fila.put(None, timeout=_FILA_CORPO_GRAÇA_S)
                    except queue.Full:
                        pass
            elif tipo == proto.PONG:
                pass
    finally:
        ping.cancel()
        for tarefa in list(tarefas):
            tarefa.cancel()
        # o cano fechou: libera qualquer thread local ainda esperando corpo do pedido.
        # put_nowait + dreno: com a fila limitada um put() bloqueante aqui penduraria o
        # event loop para sempre (o consumidor pode estar morto justamente por isso)
        with _TRAVA_ESTADO:
            for fila in _CORPOS.values():
                for _ in range(_FILA_CORPO_MAX + 1):
                    try:
                        fila.put_nowait(None)
                        break
                    except queue.Full:
                        try:
                            fila.get_nowait()
                        except queue.Empty:
                            pass


async def _encher_corpo(req_id: str, fila: "queue.Queue", payload) -> None:
    """put() BLOQUEANTE de caso proposital: com a fila cheia, parar de ler o WebSocket é o
    backpressure que o relay sente (o TCP fecha a janela até o nó voltar a consumir). A
    graça limita o estrago: passada dela, quem consumia está morto — cancela o pedido em
    vez de acumular corpo em RAM (o OOM clássico da fila sem teto)."""
    try:
        fila.put(payload, timeout=_FILA_CORPO_GRAÇA_S)
    except queue.Full:
        from funcoes import metricas_no
        metricas_no.incr("corpo_cancelado")
        ev = _CANCELADOS.get(req_id)
        if ev is not None:
            ev.set()
        logger.warning("Corpo do pedido %s sem consumo há %ss — cancelando (o uvicorn local "
                       "não está acompanhando o relay)", req_id, _FILA_CORPO_GRAÇA_S)


async def _pingueiro(ws, lock_envio: asyncio.Lock) -> None:
    """Batimento do CANO (distinto do heartbeat HTTP de 60 s): renova DT_TUNEL_ATIVO no relay,
    que é o que a API lê para escolher túnel × porta direta."""
    try:
        while True:
            await asyncio.sleep(_PING_INTERVALO)
            async with lock_envio:
                await ws.send(proto.codificar(proto.PING, "ping"))
    except asyncio.CancelledError:
        raise
    except Exception:
        pass  # o laço principal vê a conexão morta e fecha a sessão junto


async def _executar_local(ws, lock_envio: asyncio.Lock, req_id: str, meta: dict) -> None:
    """Executa o pedido tunelado contra o uvicorn LOCAL e faz a resposta fluir em streaming de
    volta pelo cano. Threads: o blocking do requests (leitura do corpo de entrada + iteração da
    resposta) anda para fora do event loop; o envio dos quadros volta para cá por uma fila."""
    corpo_queue = _CORPOS.get(req_id)
    cancelado = _CANCELADOS.get(req_id) or threading.Event()
    loop = asyncio.get_running_loop()
    fila_resposta: asyncio.Queue = asyncio.Queue(maxsize=64)

    def _lote():
        def _corpo():
            while True:
                try:
                    # sem FIM do relay (cano morreu sem despedida), a thread se solta em
                    # 5 min — antes eram 60 min de thread e corpo preso por upload órfão
                    pedaco = corpo_queue.get(timeout=_FILA_CORPO_FIM_S) if corpo_queue else None
                except queue.Empty:
                    return
                if pedaco is None:
                    return
                yield pedaco

        tem_corpo = meta.get("m") in ("POST", "PUT", "PATCH")
        try:
            resposta = requests.request(
                meta.get("m") or "GET",
                f"{_LOCAL}{meta.get('p') or '/'}",
                params=meta.get("q") or None,
                headers=meta.get("h") or None,
                data=_corpo() if tem_corpo else None,
                stream=True,
                timeout=(5, 3600),
            )
        except Exception as exc:
            loop.call_soon_threadsafe(fila_resposta.put_nowait,
                                      ("err", f"{type(exc).__name__}: {exc}"))
            return
        loop.call_soon_threadsafe(fila_resposta.put_nowait,
                                  ("head", resposta.status_code, dict(resposta.headers)))
        try:
            for pedaco in resposta.iter_content(chunk_size=1024 * 1024):
                if cancelado.is_set():
                    break
                if pedaco:
                    loop.call_soon_threadsafe(fila_resposta.put_nowait, ("chu", pedaco))
        except Exception as exc:
            loop.call_soon_threadsafe(fila_resposta.put_nowait,
                                      ("err", f"leitura local falhou: {exc}"))
        finally:
            try:
                resposta.close()
            except Exception:
                pass
            loop.call_soon_threadsafe(fila_resposta.put_nowait, ("fim",))

    threading.Thread(target=_lote, daemon=True,
                     name=f"tunel-local-{req_id[:8]}").start()
    try:
        while True:
            evento = await fila_resposta.get()
            tipo_ev = evento[0]
            if tipo_ev == "head":
                async with lock_envio:
                    await ws.send(proto.codificar(
                        proto.RESP, req_id, {"s": evento[1], "h": evento[2]}))
            elif tipo_ev == "chu":
                async with lock_envio:
                    await ws.send(proto.codificar(proto.CHU, req_id, None, evento[1]))
            elif tipo_ev == "err":
                async with lock_envio:
                    await ws.send(proto.codificar(proto.ERR, req_id, {"d": evento[1]}))
                return
            elif tipo_ev == "fim":
                async with lock_envio:
                    await ws.send(proto.codificar(proto.FIM, req_id))
                return
    except Exception:
        cancelado.set()  # soquete morreu no meio da resposta: solta a thread local
    finally:
        with _TRAVA_ESTADO:
            _CORPOS.pop(req_id, None)
            _CANCELADOS.pop(req_id, None)
