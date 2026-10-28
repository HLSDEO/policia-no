"""Operário de jobs no NÓ — OCR/CLIP/facial rodando na CPU da estação (docs/no_descentralizado.md,
"jobs no nó"). Irmão de `no_extracao.py` para os jobs de imagem.

O nó PERGUNTA, o central RESPONDE — nada aqui precisa de porta aberta:

    while vivo:
        POST /api/no/jobes/claim   → jobs marcados para este nó
        GET  /api/no/jobes/<i>/plano → janela de trabalho (cursor por id_arquivo)
        ... Tesseract/CLIP locais, na imagem que já está no disco daqui ...
        POST /api/no/jobes/<i>/lote  → NDJSON de resultado (o central grava)
        POST /api/no/jobes/<i>/estado → progresso / concluir / erro / finalizar

Mesmas funções puras do caminho local (`ocr_utils._executar_ocr`,
`classificacao_imagem_utils._classificar_lote`, `comparar_rosto_utils._embeddings_do_lote`) —
o que caiu fora foi só a beirada de banco/ZIP-remoto, substituída pelo plano+entrega acima. Um
job nunca roda nas duas pontas: o claim move o status e a rota central não sobe thread quando
roteia (jobs_no.no_para_job).

`cancelar: true` em qualquer resposta = a tela apertou "parar": devolve estado `cancelado` e
sai. 404 em plano/estado = o job já morreu no central (reaper/admin) — para igual, sem drama.

Processo único por container (mesmo flock do túnel, lock próprio): o teto JOBS_NO_LIMITE é do
NÓ, não por worker — com API_WORKERS>1 a fila inteira seria multiplicada.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover — dev fora do Linux
    fcntl = None

logger = logging.getLogger(__name__)

_INTERVALO_POLL = 15.0
_INTERVALO_CHEIO = 60.0
_TIMEOUT_HTTP = (10, 300)

_SEM: threading.BoundedSemaphore | None = None
_SEM_CAP = 0
_TRAVA = threading.Lock()


class _Parar(Exception):
    """Sinal interno: o central mandou parar (cancelamento/404) — não é erro."""


def _workers() -> int:
    try:
        return max(1, int(os.getenv("API_WORKERS") or 1))
    except ValueError:
        return 1


def _limite_local() -> int:
    """Teto do NÓ dividido pelos workers (ceil), mesmo desenho do EXTRACOES_SIMULTANEAS: a
    capacidade é da estação; o semaphore é por processo."""
    try:
        from funcoes import desempenho_utils

        total = desempenho_utils.valor_int("JOBS_NO_LIMITE", 1)
        return max(1, -(-total // _workers()))
    except Exception:
        return 1


def _semaphore() -> threading.BoundedSemaphore:
    global _SEM, _SEM_CAP
    if _SEM is None:
        with _TRAVA:
            if _SEM is None:
                _SEM_CAP = _limite_local()
                _SEM = threading.BoundedSemaphore(_SEM_CAP)
    return _SEM


def _lock_path() -> str:
    return os.path.join(os.getenv("TMPDIR") or "/tmp", "analyser-jobs.lock")


def iniciar_operario_jobs() -> None:
    """Só no papel `no` (chamado pelo startup do main). TUNEL_NO_ATIVO=0 não desliga isto — o
    interruptor do LADO DO NÓ é o JOBS_NO_LIMITE... na verdade o off total do recurso mora no
    central (JOBS_NO_ATIVO): com ele desligado nenhum job é marcado e o claim volta sempre vazio
    (um poll leve a cada 15 s). JOBS_NO_LIMITE=0 no .env do nó desliga o operário daqui."""
    try:
        from funcoes import desempenho_utils

        if desempenho_utils.valor_int("JOBS_NO_LIMITE", 1) <= 0:
            logger.info("Operário de jobs do nó desligado (JOBS_NO_LIMITE=0).")
            return
    except Exception:
        pass
    if fcntl is not None:
        try:
            fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.info("Outro worker deste container já é o operário de jobs.")
            return
    threading.Thread(target=_laço, daemon=True, name="no-jobs").start()
    logger.info("Operário de jobs do nó ligado (limite %s por worker).", _limite_local())


def _base() -> str:
    url = (os.getenv("CENTRAL_URL") or "").rstrip("/")
    if not url:
        raise RuntimeError("CENTRAL_URL não configurada no .env do nó")
    return url


def _headers() -> dict:
    return {"Authorization": f"Bearer {(os.getenv('NO_TOKEN') or '').strip()}"}


# ------------------------------------------------------------------ os botões de HTTP


def _claim(vagas: int) -> list[dict]:
    r = requests.post(f"{_base()}/api/no/jobes/claim",
                      json={"limite": max(1, vagas)},
                      headers=_headers(), timeout=(10, 30))
    if r.status_code == 404:
        return []  # central sem as rotas de job (versão antiga) — nada a fazer
    r.raise_for_status()
    return (r.json() or {}).get("jobs") or []


def _plano(id_job: int, depois: int, lote: int = 100) -> dict:
    r = requests.get(f"{_base()}/api/no/jobes/{id_job}/plano",
                     params={"depois": depois, "lote": lote},
                     headers=_headers(), timeout=_TIMEOUT_HTTP)
    if r.status_code == 404:
        raise _Parar
    r.raise_for_status()
    return r.json()


def _lote(id_job: int, linhas: list[dict]) -> bool:
    """NDJSON de resultados; devolve `cancelar`."""
    if not linhas:
        return False
    corpo = "\n".join(json.dumps(l, ensure_ascii=False, default=str) for l in linhas)
    r = requests.post(f"{_base()}/api/no/jobes/{id_job}/lote",
                      data=corpo.encode("utf-8"),
                      headers={**_headers(), "Content-Type": "application/x-ndjson"},
                      timeout=_TIMEOUT_HTTP)
    if r.status_code == 404:
        raise _Parar
    r.raise_for_status()
    return bool((r.json() or {}).get("cancelar"))


def _estado(id_job: int, payload: dict) -> dict:
    try:
        r = requests.post(f"{_base()}/api/no/jobes/{id_job}/estado",
                          json=payload, headers=_headers(), timeout=(10, 60))
    except requests.RequestException as exc:
        # o retorno {} esconde o canal do 'cancelar' (um job longo pode rodar horas sem
        # saber que a tela pediu parada) — conta e loga para a queda ser visível
        from funcoes import metricas_no
        metricas_no.incr("job_estado_falhas")
        logger.warning("Estado %r do job %s não chegou ao central: %s",
                       payload.get("t"), id_job, exc)
        return {}
    if r.status_code == 404:
        raise _Parar
    try:
        return r.json() or {}
    except ValueError:
        return {}


def _forcar_erro(id_job: int, mensagem: str) -> None:
    try:
        requests.post(f"{_base()}/api/no/jobes/{id_job}/estado",
                      json={"t": "erro", "erro": mensagem[:2000]},
                      headers=_headers(), timeout=(10, 30))
    except Exception:
        pass


# ------------------------------------------------------------------ o laço

def _laço() -> None:
    atraso = _INTERVALO_POLL
    while True:
        sem = _semaphore()
        livres = max(0, min(_SEM_CAP, getattr(sem, "_value", 0)))
        if livres <= 0:
            time.sleep(_INTERVALO_POLL)
            continue
        try:
            jobs = _claim(livres)
        except Exception as exc:
            from funcoes import metricas_no
            metricas_no.incr("job_claim_falhas")
            # warning (não debug): claim quebra em silêncio = o operário parou de trabalhar
            # e a única pista era uma linha invisível no log
            logger.warning("Claim de jobs falhou (central fora do ar?): %s", exc)
            time.sleep(atraso)
            atraso = min(_INTERVALO_CHEIO, atraso * 2)
            continue
        atraso = _INTERVALO_POLL
        for job in jobs:
            sem.acquire()
            try:
                threading.Thread(target=_guardar, args=(job,), daemon=True,
                                 name=f"no-job-{job.get('id_job')}").start()
            except Exception:
                # start() falhou (exaustão de threads): sem devolver a vaga, o teto do nó
                # encolhia PERMANENTEMENTE, job reivindicado órfão e fila travada no 0
                sem.release()
                logger.exception("Job %s: não consegui subir o worker no nó",
                                 job.get("id_job"))
                _forcar_erro(int(job["id_job"]),
                             "O nó não conseguiu iniciar o worker deste job (recuse-o ou "
                             "rode de novo).")
        time.sleep(_INTERVALO_POLL if jobs else atraso)


def _guardar(job: dict) -> None:
    try:
        _trabalhar(job)
    except _Parar:
        logger.info("Job %s interrompido a pedido do central.", job.get("id_job"))
    except Exception as exc:
        logger.exception("Job %s (%s) falhou no nó", job.get("id_job"), job.get("tp_job"))
        _forcar_erro(int(job["id_job"]), f"{type(exc).__name__}: {exc}")
    finally:
        _semaphore().release()


def _trabalhar(job: dict) -> None:
    from funcoes import jobs_utils

    tp = job.get("tp_job")
    if tp in (jobs_utils.TP_OCR, jobs_utils.TP_OCR_SELECAO):
        # a seleção de imagens da IA vem com ids_arquivos no plano (jobs_no._plano_ocr) — o
        # formato de grupos é o mesmo, e o OCR daqui não distingue um do outro
        _correr_ocr(job)
    elif tp == jobs_utils.TP_CLASSIFICACAO:
        _correr_classificacao(job)
    elif tp == jobs_utils.TP_COMPARACAO_FACIAL:
        _correr_facial(job)
    else:
        _forcar_erro(int(job["id_job"]), f"tipo de job desconhecido no nó: {tp}")


def _resolver_local(no_ufdr: str):
    from funcoes.organizacao_arquivos import resolver as _resolver

    caminho = _resolver(no_ufdr)
    return caminho if caminho is not None and caminho.is_file() else None


def _abrir_ufdr(caminho):
    from funcoes import ufdr_zip

    return ufdr_zip.abrir(caminho)


# ------------------------------------------------------------------ OCR


def _correr_ocr(job: dict) -> None:
    """Igual ao `_processa_grupos` do central, sem banco: o plano chega do central (com cursor),
    o ZIP é o LOCAL (disco da estação), e o resultado volta em NDJSON. Falha de leitura carimba
    texto vazio — a MESMA regra do central para a imagem não voltar para a fila nunca mais."""
    id_job = int(job["id_job"])
    from funcoes.ocr_utils import _config_ocr, _executar_ocr, _ler_membro

    cfg = _config_ocr()
    depois, processados, erros, com_texto = 0, 0, 0, 0
    while True:
        plano = _plano(id_job, depois)
        grupos = plano.get("grupos") or []
        if not grupos:
            break
        cancelar = _estado(id_job, {"t": "progresso", "processados": processados,
                                    "total": processados + int(plano.get("restantes") or 0),
                                    "erros": erros})
        if cancelar.get("cancelar"):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        linhas: list[dict] = []
        for no_ufdr, itens in grupos:
            caminho = _resolver_local(no_ufdr)
            if caminho is None:
                logger.warning("OCR no nó: %s não está na pasta desta estação", no_ufdr)
                for id_arq, _ in itens:
                    linhas.append({"a": id_arq, "t": None})
                erros += len(itens)
                processados += len(itens)
                continue
            try:
                with _abrir_ufdr(caminho) as zf:
                    for id_arq, caminho_zip in itens:
                        dados = _ler_membro(zf, caminho_zip)
                        if dados is None:
                            linhas.append({"a": id_arq, "t": None})
                            erros += 1
                        else:
                            try:
                                texto = _executar_ocr(dados, cfg)
                            except Exception:
                                texto = None
                                erros += 1
                            if texto:
                                com_texto += 1
                            linhas.append({"a": id_arq, "t": texto})
                        processados += 1
            except Exception as exc:
                logger.warning("OCR no nó: erro ao abrir %s: %s", no_ufdr, exc)
                for id_arq, _ in itens:
                    linhas.append({"a": id_arq, "t": None})
                erros += len(itens)
                processados += len(itens)
        if _lote(id_job, linhas):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        depois = int(plano.get("cursor") or 0)
    _estado(id_job, {"t": "concluir", "metricas": {"com_texto": com_texto}})


# ------------------------------------------------------------------ classificação CLIP


def _correr_classificacao(job: dict) -> None:
    id_job = int(job["id_job"])
    from funcoes.classificacao_imagem_utils import (
        _carregar_modelo, _classificar_lote)

    try:
        model, processor = _carregar_modelo()
    except Exception as exc:
        raise RuntimeError(
            "o nó não conseguiu carregar o modelo CLIP (sem cache do Hugging Face e sem saída "
            f"para o HF na estação?) — {type(exc).__name__}: {exc}") from exc
    depois, processados, erros = 0, 0, 0
    while True:
        plano = _plano(id_job, depois)
        if plano.get("erro"):
            _estado(id_job, {"t": "erro", "erro": plano["erro"]})
            return
        imagens = plano.get("imagens") or []
        if not imagens:
            break
        cancelar = _estado(id_job, {"t": "progresso", "processados": processados,
                                    "total": processados + int(plano.get("restantes") or 0),
                                    "erros": erros})
        if cancelar.get("cancelar"):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        termos = plano.get("termos") or []
        entradas: list[tuple[int, bytes]] = []
        linhas: list[dict] = []
        # grupos por .ufdr: um ZIP aberto por arquivo por página, não por imagem
        por_ufdr: dict[str, list] = {}
        for id_arq, no_ufdr, caminho_zip in imagens:
            por_ufdr.setdefault(no_ufdr, []).append((id_arq, caminho_zip))
        for no_ufdr, itens in por_ufdr.items():
            caminho = _resolver_local(no_ufdr)
            if caminho is None:
                for id_arq, _ in itens:
                    linhas.append({"a": id_arq, "c": None, "f": None})
                    erros += 1
                    processados += 1
                continue
            pendentes = {id_arq for id_arq, _ in itens}
            try:
                with _abrir_ufdr(caminho) as zf:
                    for id_arq, caminho_zip in itens:
                        dados = None
                        for tentativa in (caminho_zip, caminho_zip.replace("\\", "/")):
                            try:
                                dados = zf.read(tentativa)
                                break
                            except KeyError:
                                continue
                        pendentes.discard(id_arq)
                        if dados is None:
                            linhas.append({"a": id_arq, "c": None, "f": None})
                            erros += 1
                            processados += 1
                            continue
                        entradas.append((id_arq, dados))
            except Exception:
                # o ZIP abriu e morreu no meio (or nem abriu): o que não chegou a ser lido é erro
                for id_arq in sorted(pendentes):
                    linhas.append({"a": id_arq, "c": None, "f": None})
                    erros += 1
                    processados += 1
        if entradas:
            for r in _classificar_lote(model, processor, entradas, termos):
                linhas.append({"a": r["id_arquivo"], "c": r["classificacao"],
                               "f": r["confianca"]})
                processados += 1
                if r["classificacao"] is None:
                    erros += 1
        if _lote(id_job, linhas):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        depois = int(plano.get("cursor") or 0)
    _estado(id_job, {"t": "concluir"})


# ------------------------------------------------------------------ comparação facial


def _correr_facial(job: dict) -> None:
    """A estação roda o CLIP nas imagens DO SEU DISCO e devolve só os vetores (1 KB de float16
    por imagem, via NDJSON). O vetor da referência é calculado aqui mesmo — o central fecha a
    comparação com a multiplicação de matriz, sem precisar de CLIP nem de imagem nenhuma."""
    id_job = int(job["id_job"])
    from funcoes.classificacao_imagem_utils import _carregar_modelo
    from funcoes.comparar_rosto_utils import _embeddings_do_lote, _preparar_pixels

    ref_b64 = job.get("referencia_b64")
    if not ref_b64:
        raise RuntimeError("o central não entregou a foto de referência no claim")
    try:
        model, _ = _carregar_modelo()
    except Exception as exc:
        raise RuntimeError(
            "o nó não conseguiu carregar o modelo CLIP (sem cache do Hugging Face e sem saída "
            f"para o HF na estação?) — {type(exc).__name__}: {exc}") from exc
    emb_ref = _embeddings_do_lote(model, [_preparar_pixels(base64.b64decode(ref_b64))])[0]
    # float16 ida-e-volta: exatamente o mesmo número que o central vê para as imagens em cache
    emb_ref16 = emb_ref.astype("<f2")

    depois, processados, erros = 0, 0, 0
    while True:
        plano = _plano(id_job, depois)
        imagens = plano.get("imagens") or []
        if depois == 0:
            processados = int(plano.get("reaproveitados") or 0)
        total = int(plano.get("total") or 0)
        if not imagens:
            break
        cancelar = _estado(id_job, {"t": "progresso", "processados": processados,
                                    "total": total, "erros": erros})
        if cancelar.get("cancelar"):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        linhas: list[dict] = []
        por_ufdr: dict[str, list] = {}
        for item in imagens:
            por_ufdr.setdefault(item["no_arquivo_ufdr"], []).append(item)
        for no_ufdr, itens in por_ufdr.items():
            caminho = _resolver_local(no_ufdr)
            if caminho is None:
                erros += len(itens)
                processados += len(itens)
                continue
            try:
                with _abrir_ufdr(caminho) as zf:
                    for item in itens:
                        dados = None
                        for tentativa in (item["co_caminho_zip"],
                                          item["co_caminho_zip"].replace("\\", "/")):
                            try:
                                dados = zf.read(tentativa)
                                break
                            except KeyError:
                                continue
                        if dados is None:
                            erros += 1
                            processados += 1
                            continue
                        try:
                            pixels = _preparar_pixels(dados)
                            vetor = _embeddings_do_lote(model, [pixels])[0].astype("<f2")
                            linhas.append({"a": item["id_arquivo"],
                                           "v": base64.b64encode(vetor.tobytes()).decode("ascii")})
                        except Exception:
                            erros += 1
                        processados += 1
            except Exception as exc:
                logger.warning("Facial no nó: erro ao abrir %s: %s", no_ufdr, exc)
                erros += len(itens)
                processados += len(itens)
        if _lote(id_job, linhas):
            _estado(id_job, {"t": "cancelado"})
            raise _Parar
        depois = int(plano.get("cursor") or 0)
    resposta = _estado(id_job, {"t": "finalizar",
                                "ref_v": base64.b64encode(emb_ref16.tobytes()).decode("ascii")})
    if not resposta.get("ok"):
        _forcar_erro(id_job, "o central recusou a finalização da comparação")


def fila_estado() -> dict:
    """Telemetria para o GET /no/estado (tela Administração → Servidores): limite e vagas."""
    sem = _semaphore()
    livres = max(0, min(_SEM_CAP, getattr(sem, "_value", 0)))
    return {"limite": _SEM_CAP * _workers(), "em_andamento": max(0, _SEM_CAP - livres)}
