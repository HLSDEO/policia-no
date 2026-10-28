"""O nó como OPERÁRIO de importação RIF/SIMBA/IPED (docs/no_descentralizado.md, "nó importa").

Espelho do `no_extracao.py` (que é o nó-extrai do .ufdr), para as três importações cuja pasta
mora NA ESTAÇÃO e cujo parse consome a CPU local:

1. O central cria a linha-ponte (INQ_TB02, no_fonte+id_importacao) e chama POST /no/importar
   (no_servico.py) com {tp, id_extracao, pasta, opcoes}, e sai da frente;
2. aqui uma thread monta um `CanalCentral` (o mesmo CanalEntrega da extração) sobre a pasta de
   entrega da ponte e roda o importador da fonte EM MODO CANAL: o parse lê o CSV/caso LOCAL e
   cada linha produzida vira um registro JSON rif_*/smb_*/domínio que sobe em lotes — o central
   reproduz com as MESMAS funções do importador local (mesmo dedup/COPY/contrapartes);
3. fase/progresso/batida/finalizar são callbacks na ponte, que é o que a tela acompanha.

Nada deste módulo toca o banco: o nó não tem Postgres. O `id_extracao` é sempre o da PONTE
(claim do canal) — a linha-pai (rif_tb01/smb_tb01/ipd_tb01) só existe no central e o replay a
resolve pela própria ponte (no_fonte+id_importacao).

Concorrência: compartilhada com a extração (o teto local é o MESMO — a CPU da estação faz uma
grande por vez), e um id despachado duas vezes com a mesma geração não processa de novo.
"""
from __future__ import annotations

import logging
import threading

from funcoes import canal_extracao
from funcoes.no_extracao import (CanalCentral, _esperar_flock, _pasta_entrega_livre,
                                 _reportar_erro, _semaphore, _ESPERA_VAGA_SEGUNDOS, _TRAVA,
                                 _RODANDO)
import os

try:
    import fcntl
except ImportError:  # pragma: no cover — fora do container vale só o _RODANDO em memória
    fcntl = None

logger = logging.getLogger(__name__)

# o operário de importação usa o MESMO semaphore do de extração (a CPU da estação é uma só)


def rodando() -> list[int]:
    with _TRAVA:
        return sorted(_RODANDO)


def _prevalidar(tp: str, pasta: str, opcoes: dict) -> None:
    """Confere ANTES do 202 que a prova está mesmo neste disco (as MESMAS validações do
    importador de cada fonte, que o worker repetirá). O central conta com o 404 do
    POST /no/importar para desfazer a linha-pai que criou no despacho
    (no_importacao._despachar_e_marcar_erro); com 202 + erro adiante a rif_tb01/smb_tb01/
    ipd_tb01 ficaria 'pendente' para sempre."""
    subpasta = (opcoes.get("delegacia_pasta") or "").strip() or None
    if tp == "rif":
        from funcoes.rif_utils import _validar_pasta

        _validar_pasta(pasta, subpasta)
    elif tp == "simba":
        from funcoes.simba_utils import _validar_arquivo

        _validar_arquivo(pasta, subpasta)
    else:
        from funcoes.iped_utils import _resolver_pasta_caso

        _resolver_pasta_caso(pasta)


def aceitar(tp: str, id_extracao: int, pasta: str, opcoes: dict, geracao: int = 0) -> dict:
    """Valida o trabalho e sobe a thread. Idempotente por (id, geração), como no_extracao.
    FileNotFoundError quando a pasta/arquivo não está no disco daqui (a rota devolve 404 e o
    central marca erro com a mensagem)."""
    if tp not in ("rif", "simba", "iped"):
        raise ValueError(f"tipo de importação desconhecido: {tp}")
    with _TRAVA:
        entrada = _RODANDO.get(id_extracao)
        if entrada is not None and entrada.get("geracao") == geracao:
            return {"ok": True, "fila": False, "motivo": "já em andamento"}
        _RODANDO[id_extracao] = {"tp": tp, "pasta": pasta, "geracao": geracao,
                                 "cancelar_troca": threading.Event()}
    try:
        _prevalidar(tp, pasta, opcoes)
        threading.Thread(target=_operario,
                         args=(tp, id_extracao, pasta, opcoes, geracao),
                         daemon=True, name=f"no-importa-{tp}-{id_extracao}").start()
    except Exception:
        with _TRAVA:
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)
        logger.exception("Importação %s (%s): não consegui subir o worker", id_extracao, tp)
        raise
    return {"ok": True, "fila": True}


def _operario(tp: str, id_extracao: int, pasta: str, opcoes: dict, geracao: int) -> None:
    sem = _semaphore()
    if not sem.acquire(timeout=_ESPERA_VAGA_SEGUNDOS):
        _reportar_erro(id_extracao, "A fila local do nó está ocupada há tempo demais.")
        with _TRAVA:
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)
        return
    fd = None
    canal = None
    try:
        entrega = _pasta_entrega_livre(id_extracao)
        if fcntl is not None:
            try:
                fd = os.open(entrega / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                fd = None
            if fd is not None and not _esperar_flock(fd):
                _reportar_erro(id_extracao, "Outra tentativa desta importação não liberou a "
                                            "pasta de entrega.")
                os.close(fd)
                fd = None
                return
        for velho in entrega.glob("lote_*"):
            try:
                velho.unlink()
            except OSError:
                pass
        canal = CanalCentral(id_extracao, entrega, geracao=geracao)
        canal.numero_inquerito = opcoes.get("numero_inquerito")
        with _TRAVA:
            entrada = _RODANDO.get(id_extracao)
            if entrada is not None and entrada.get("geracao") == geracao:
                canal._cancelar_extra = entrada["cancelar_troca"]
        token = canal_extracao.ativar(canal)
        try:
            _rodar_fonte(tp, id_extracao, pasta, opcoes, canal)
        except FileNotFoundError as exc:
            # arquivo/pasta sumiu: o central marca erro com a causa (a rota já devolvera 202)
            canal.estado({"t": "erro", "mensagem": str(exc)[:2000]})
        except Exception as exc:  # noqa: BLE001 — melhor devolver erro claro que presa
            logger.exception("Importação %s (%s) falhou no nó", id_extracao, tp)
            try:
                canal.estado({"t": "erro",
                              "mensagem": f"{type(exc).__name__}: {exc}"[:2000]})
            except Exception:
                pass
        finally:
            canal_extracao.desativar(token)
            canal.finalizar_lotes_soltos()
            canal.limpar()
    except Exception as exc:  # noqa: BLE001 — nem chegou a rodar
        logger.exception("Importação %s (%s) não conseguiu começar", id_extracao, tp)
        _reportar_erro(id_extracao, f"O nó não conseguiu preparar a entrega: {exc}")
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        sem.release()
        with _TRAVA:
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)


def _rodar_fonte(tp: str, id_extracao: int, pasta: str, opcoes: dict, canal) -> None:
    """Despacha para o importador da fonte em MODO CANAL (cada um emite rif_*/smb_*/domínio e
    fecha com o relatório no 'finalizar'). O id da ponte chega como id_extracao — a linha-pai
    no central é resolvida pela própria ponte, então o nó não precisa conhecê-la."""
    if tp == "rif":
        from funcoes.rif_utils import importar_rif_no
        importar_rif_no(canal, pasta, opcoes)
    elif tp == "simba":
        from funcoes.simba_utils import importar_simba_no
        importar_simba_no(canal, pasta, opcoes)
    elif tp == "iped":
        from funcoes.iped_utils import importar_iped_no
        importar_iped_no(canal, pasta, opcoes)
