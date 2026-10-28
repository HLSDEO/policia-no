"""Transcrição REMOTA pedida pelo central (POST /no/transcrever) — o whisper da estação.

O botão "Transcrever" do central não tem faster-whisper nem Postgres: ele resolve a lista de
pendentes no banco dele (áudio por caminho dentro do .ufdr, dedup por binário) e MANDA a lista
pronta (`tarefas`). O .ufdr está neste disco — o trabalho pesado (CPU da delegacia) roda aqui e
só o TEXTO volta, pelos mesmos callbacks de estado da extração:

- {"t":"fase"/"progresso"/"batida"} ......... badge vivo na tela do central;
- {"t":"transcricao", prefixo, caminho_zip, texto} .. um áudio pronto (central grava nos anexos);
- {"t":"transcricao_fim", qt} ................ fecho: a extração volta a 'concluido';
- {"t":"erro", mensagem} ..................... falha de tudo (modelo ausente, arquivo sumido).

Nenhum SELECT aqui: o nó não tem banco. Cancelamento vem da resposta do próprio callback
({"cancelar": true}) — igual ao BatimentoPonte da extração.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

_RODANDO: set[int] = set()
_TRAVA = threading.Lock()

_TIMEOUT = (10, 120)


def rodando() -> list[int]:
    with _TRAVA:
        return sorted(_RODANDO)


def aceitar(id_extracao: int, no_arquivo: str, modelo: str | None,
            incluir_video: bool, tarefas: list[dict]) -> dict:
    """Valida o pedido e sobe a thread. `tarefas` é [{prefixo, caminho_zip}] — quem monta é o
    central (ele tem o banco); o nó só deduplica por binário como a pipeline local sempre fez.
    FileNotFoundError quando o .ufdr não está nesta pasta (a rota devolve 404)."""
    from funcoes.organizacao_arquivos import resolver

    if not tarefas:
        raise ValueError("Nenhuma tarefa de transcrição no pedido.")
    caminho = resolver(no_arquivo)
    if caminho is None or not caminho.is_file():
        raise FileNotFoundError(f"O arquivo {no_arquivo} não está na pasta deste servidor.")

    vistos: set[str] = set()
    entradas: list[tuple[str, str]] = []
    for t in tarefas:
        chave_zip = str(t.get("caminho_zip") or "")
        prefixo = str(t.get("prefixo") or "")
        if not chave_zip or not prefixo or chave_zip in vistos:
            continue  # o mesmo binário em várias conversas: transcreve uma vez
        vistos.add(chave_zip)
        entradas.append((f"{prefixo}|{chave_zip}", chave_zip))
    if not entradas:
        raise ValueError("Nenhuma tarefa de transcrição válida no pedido.")

    with _TRAVA:
        if id_extracao in _RODANDO:
            return {"ok": True, "em_andamento": True, "tarefas": len(entradas)}
        _RODANDO.add(id_extracao)

    threading.Thread(target=_operario, daemon=True, name=f"transcrever-{id_extracao}",
                     args=(id_extracao, caminho, modelo, entradas)).start()
    return {"ok": True, "em_andamento": False, "tarefas": len(entradas)}


# ----------------------------------------------------------------------------- ponte de estado

def _base() -> str:
    url = (os.getenv("CENTRAL_URL") or "").rstrip("/")
    if not url:
        raise RuntimeError("CENTRAL_URL não configurada no .env do nó")
    return url


def _headers() -> dict:
    token = (os.getenv("NO_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("NO_TOKEN não configurada no .env do nó")
    return {"Authorization": f"Bearer {token}"}


class _Ponte:
    """Estado da transcrição ao central + cache do sinal de cancelamento (a resposta do
    callback é que responde {cancelar}) — o mesmo desenho do BatimentoPonte, sem canal:
    a transcrição não emite lotes, só estado."""

    def __init__(self, id_extracao: int):
        self.id_extracao = id_extracao
        self._cancelar = False
        self._ult_chk = 0.0

    def estado(self, payload: dict) -> None:
        try:
            r = requests.post(f"{_base()}/api/no/entrega/{self.id_extracao}/estado",
                              json=payload, headers=_headers(), timeout=_TIMEOUT)
            if r.status_code == 409:
                # extração saiu de 'processando' (cancelada/apagada no central): para limpo
                self._cancelar = True
                return
            r.raise_for_status()
            corpo = r.json() if r.content else {}
            self._cancelar = self._cancelar or bool(corpo.get("cancelar"))
        except Exception as exc:
            logger.warning("Estado da transcrição %s não chegou ao central: %s",
                           self.id_extracao, exc)
        finally:
            self._ult_chk = time.time()

    def cancelar(self) -> bool:
        if self._cancelar:
            return True
        if time.time() - self._ult_chk > 2:
            self.estado({"t": "batida"})
        return self._cancelar


# ----------------------------------------------------------------------------- operário

def _operario(id_extracao: int, caminho, modelo: str | None,
              entradas: list[tuple[str, str]]) -> None:
    ponte = _Ponte(id_extracao)
    try:
        ponte.estado({"t": "fase", "cd_fase": "transcrevendo", "qt_progresso_fase": 0})
        ultimo = {"pct": -1}

        def ao_progredir(feitos: int, total: int) -> None:
            if not total:
                return
            pct = min(99, max(0, int(feitos * 100 / total)))
            if pct != ultimo["pct"]:
                ultimo["pct"] = pct
                ponte.estado({"t": "progresso", "qt_progresso_fase": pct})

        from funcoes.transcricao import ModeloTranscricaoIndisponivel, transcrever_lote

        try:
            resultado = transcrever_lote(
                caminho, entradas, modelo,
                deve_cancelar=ponte.cancelar,
                ao_progredir=ao_progredir,
            )
        except ModeloTranscricaoIndisponivel as exc:
            ponte.estado({"t": "erro",
                          "mensagem": f"O modelo de transcrição '{exc}' não está disponível "
                                      "nesta estação (sem internet e sem o modelo em cache)."})
            return

        for chave, texto in resultado.items():
            if ponte.cancelar:
                break
            prefixo, caminho_zip = chave.split("|", 1)
            ponte.estado({"t": "transcricao", "prefixo": prefixo,
                          "caminho_zip": caminho_zip, "texto": texto})
        ponte.estado({"t": "transcricao_fim", "qt": len(resultado)})
    except Exception as exc:
        logger.exception("Transcrição remota da extração %s falhou no nó", id_extracao)
        try:
            ponte.estado({"t": "erro",
                          "mensagem": f"Transcrição no nó: {type(exc).__name__}: {exc}"[:2000]})
        except Exception:
            pass
    finally:
        with _TRAVA:
            _RODANDO.discard(id_extracao)
