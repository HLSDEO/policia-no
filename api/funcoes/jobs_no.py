"""Jobs de imagem RODANDO NO NÓ — a metade central (docs/no_descentralizado.md, "jobs no nó").

OCR, classificação CLIP e comparação facial sempre rodaram no central lendo a imagem pela WAN
(passaporte `FonteRemota`, faixas de 4 MiB). Com a prova na estação e a CPU dela parada, o sentido
certo é o inverso: o central resolve o PLANO (o que falta, em que arquivo do ZIP está) e o NÓ
processa o próprio disco, devolvendo só o resultado — texto de OCR, rótulo+confiança, vetor de
1 KB. Mesmos jobs, mesma tela, mesmo cancelamento; o que muda é onde o Tesseract/CLIP gira.

O protocolo é puxado pelo NÓ (o nó fala com o central por natureza — é assim que o heartbeat
sobrevive a NAT):

    POST /api/no/jobes/claim          → marca 'pendente'→'processando' (SKIP LOCKED) e entrega o
                                        descritor; para comparação facial carrega AQUI a foto de
                                        referência (bytes que subiram pelo navegador, guardados
                                        em data/.jobs_no/ e apagados no primeiro claim)
    GET  /api/no/jobes/<id>/plano     → a próxima janela de trabalho, SEMPRE com cursor
                                        (id_arquivo > depois): o avanço é garantido mesmo quando
                                        uma imagem falha e não deixa carimbo no banco
    POST /api/no/jobes/<id>/lote      → NDJSON de resultados (o central grava com as MESMAS
                                        funções do caminho local — _salvar_resultados,
                                        _gravar_embeddings)
    POST /api/no/jobes/<id>/estado    → progresso/concluir/erro/finalizar/cancelado; a resposta
                                        carrega `cancelar` (é por aí que o "parar" da tela chega
                                        à estação — mesmo desenho dos callbacks da extração)

O central deste repositório NÃO roda modelos: não há Tesseract, CLIP nem whisper aqui. Todo job
de imagem nasce para o nó da delegacia do inquérito — sem nó ativo/online a rota RECUSA o job com
mensagem clara (409). A estação processa o próprio disco e devolve só o resultado.

`ocr_imagem_selecao` (seleção da IA no chat) segue a mesma regra: vira job OCR no nó.
"""

from __future__ import annotations

import base64
import logging

from funcoes import jobs_utils
from funcoes.db_utils import get_connection
from funcoes.organizacao_arquivos import PASTA_DADOS

logger = logging.getLogger(__name__)

# Tipos que podem descer para a estação. OCR é o único que não precisa de modelo no nó.
# A seleção da IA (ocr_imagem_selecao) desce como OCR comum filtrado pela lista de ids que o
# plano resolveu no central (tx_parametros.ids_arquivos) — o nó não precisa conhecer a ferramenta.
TPS_OCR = {jobs_utils.TP_OCR, jobs_utils.TP_OCR_SELECAO}
TPS_CLIP = {jobs_utils.TP_CLASSIFICACAO, jobs_utils.TP_COMPARACAO_FACIAL}
TPS_NO = TPS_OCR | TPS_CLIP


def habilitado(tp_job: str) -> bool:
    """Os três tipos de imagem rodam SEMPRE no nó — o central não tem os modelos."""
    return tp_job in TPS_NO


def no_para_job(tp_job: str, id_inquerito: int | None):
    """O nó que deve rodar este job, ou None. None = o job NÃO pode rodar: o central não tem
    Tesseract/CLIP, e a rota devolve 409 com a causa (nó offline deixaria o job parado na fila
    da estação — recusar agora é mais honesto do que enfileirar para sempre)."""
    if id_inquerito is None or not habilitado(tp_job):
        return None
    from funcoes import no_utils

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT no_delegacia FROM investigacao.inq_tb01_inqueritos "
                        "WHERE id_inquerito = %s", (id_inquerito,))
            row = cur.fetchone()
    except Exception:
        logger.exception("Falha ao resolver a delegacia do inquérito %s para roteamento de job",
                         id_inquerito)
        return None
    if not row or not row[0]:
        return None
    no = no_utils.servidor_ativo_para(row[0])
    if no is None or not no.get("online"):
        return None
    return no


def exigir_no_para_job(tp_job: str, id_inquerito: int | None) -> dict:
    """O nó do job, ou 409 com a causa: o central não roda os modelos (Tesseract/CLIP/whisper
    vivem na estação), então sem nó ativo/online o job de imagem simplesmente não pode rodar."""
    from fastapi import HTTPException

    no = no_para_job(tp_job, id_inquerito)
    if no is None:
        from funcoes.no_utils import SEM_NO_ATIVO_MSG

        raise HTTPException(
            status_code=409,
            detail="Este processamento roda no servidor da delegacia (nó) — o sistema central "
                   "não executa OCR, classificação nem comparação facial. " + SEM_NO_ATIVO_MSG)
    return no


# ------------------------------------------------------------------ referência facial

def _pasta_referencias():
    pasta = PASTA_DADOS / ".jobs_no"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def guardar_referencia(id_job: int, dados: bytes) -> None:
    """A foto de referência sobe pelo navegador e cai num request do central; se o job for para o
    nó, os bytes precisam chegar até a estação. Um arquivo escondido em data/ (mesma família do
    .no_cache) é o jeito mais honesto: o claim o entrega e apaga; o jobs_utils._limpar_referencia
    varre o resto (cancelamento, erro, reaper)."""
    _pasta_referencias().joinpath(f"{id_job}.ref").write_bytes(dados)


def _ler_referencia(id_job: int) -> bytes | None:
    caminho = PASTA_DADOS / ".jobs_no" / f"{id_job}.ref"
    if not caminho.is_file():
        return None
    try:
        return caminho.read_bytes()
    except OSError:
        return None


# ------------------------------------------------------------------ claim


def claim(id_servidor: int, limite: int) -> list[dict]:
    """O nó pergunta "o que é meu?". SKIP LOCKED: dois workers do MESMO nó (API_WORKERS>1) nunca
    pegam o mesmo job. A transição 'pendente'→'processando' é um UPDATE único — o mesmo guard de
    sempre impede o job de aparecer em duas filas."""
    limites = sorted(TPS_NO)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH vagas AS (
                SELECT id_job FROM investigacao.job_tb01_jobs
                WHERE id_servidor = %s AND cd_status = 'pendente' AND tp_job = ANY(%s)
                ORDER BY id_job
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE investigacao.job_tb01_jobs j
            SET cd_status = 'processando', no_worker = %s, dt_ultima_atividade = now()
            FROM vagas WHERE j.id_job = vagas.id_job
            RETURNING j.id_job, j.tp_job, j.id_inquerito, j.id_extracao, j.tx_parametros
            """,
            (id_servidor, limites, max(1, min(int(limite or 1), 8)), f"no:{id_servidor}"),
        )
        colunas = [d.name for d in cur.description]
        claimed = [dict(zip(colunas, row)) for row in cur.fetchall()]
        conn.commit()

    for job in claimed:
        if job["tp_job"] == jobs_utils.TP_COMPARACAO_FACIAL:
            ref = _ler_referencia(job["id_job"])
            (PASTA_DADOS / ".jobs_no" / f"{job['id_job']}.ref").unlink(missing_ok=True)
            if ref is None:
                jobs_utils.falhar(job["id_job"], "A foto de referência da comparação se perdeu "
                                                 "antes do resgate no nó.")
                job["erro"] = "referencia_perdida"
            else:
                job["referencia_b64"] = base64.b64encode(ref).decode("ascii")
        logger.info("Job %s (%s) reivindicado pelo nó %s", job["id_job"], job["tp_job"],
                    id_servidor)
    return [j for j in claimed if "erro" not in j]


# ------------------------------------------------------------------ plano

_LIMITE_LOTE = 300  # linhas por página de plano — 300 descritores de OCR pesam ~30 KB


def _conferir(id_servidor: int, id_job: int) -> dict | None:
    """O job é deste nó e continua vivo? None = não (a rota devolve 404/410)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tp_job, id_inquerito, id_extracao, tx_parametros, cd_status, fl_cancelar "
            "FROM investigacao.job_tb01_jobs WHERE id_job = %s AND id_servidor = %s",
            (id_job, id_servidor))
        row = cur.fetchone()
    if row is None:
        return None
    colunas = ("tp_job", "id_inquerito", "id_extracao", "tx_parametros", "cd_status",
               "fl_cancelar")
    job = dict(zip(colunas, row))
    if job["cd_status"] != "processando":
        return None
    return job


def plano(id_servidor: int, id_job: int, depois: int = 0, lote: int = 100) -> dict:
    """A próxima janela de trabalho do job. `depois` é o cursor por id_arquivo: a fila viva
    (dt_ocr IS NULL etc.) não avança quando a imagem é ilegível, e sem cursor o nó reprocessaria
    o mesmo cabeça de fila para sempre."""
    job = _conferir(id_servidor, id_job)
    if job is None:
        raise LookupError("job não é deste nó ou já saiu de 'processando'")
    lote = max(1, min(int(lote or 100), _LIMITE_LOTE))
    depois = max(0, int(depois or 0))
    if job["tp_job"] in (jobs_utils.TP_OCR, jobs_utils.TP_OCR_SELECAO):
        return _plano_ocr(job, depois, lote)
    if job["tp_job"] == jobs_utils.TP_CLASSIFICACAO:
        return _plano_classificacao(job, depois, lote)
    if job["tp_job"] == jobs_utils.TP_COMPARACAO_FACIAL:
        return _plano_facial(job, depois, lote)
    raise LookupError(f"tipo de job não roteável: {job['tp_job']}")


def _plano_ocr(job: dict, depois: int, lote: int) -> dict:
    from funcoes.ocr_utils import _BYTES_MIN

    # seleção da IA: a fila é a lista de ids resolvida no central na hora do disparo
    ids_selecao = None
    if job["tp_job"] == jobs_utils.TP_OCR_SELECAO:
        ids_selecao = [int(i) for i in ((job["tx_parametros"] or {}).get("ids_arquivos") or [])]
        if not ids_selecao:
            return {"tp": job["tp_job"], "grupos": [], "restantes": 0, "cursor": depois}

    query = """SELECT e.no_arquivo, a.id_arquivo, a.co_caminho_zip
               FROM investigacao.arq_tb01_arquivos a
               JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
               WHERE a.no_categoria = 'Imagem'
                 AND a.dt_ocr IS NULL
                 AND a.co_caminho_zip IS NOT NULL
                 AND a.tp_mime <> 'image/webp'
                 AND a.qt_bytes >= %s
                 AND a.id_arquivo > %s
                 AND e.id_inquerito = %s"""
    params: list = [_BYTES_MIN, depois, job["id_inquerito"]]
    if ids_selecao is not None:
        query += " AND a.id_arquivo = ANY(%s::bigint[])"
        params.append(ids_selecao)
    if job["id_extracao"] is not None:
        query += " AND a.id_extracao = %s"
        params.append(job["id_extracao"])
    query += " ORDER BY a.id_arquivo LIMIT %s"
    params.append(lote)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        linhas = cur.fetchall()
        # "ainda há mais?" — a barra da tela e a estimativa de total do nó vêm daqui (uma contagem
        # por página de 300 imagens é invisível perto do Tesseract que a página alimenta)
        cur.execute(
            """SELECT count(*) FROM investigacao.arq_tb01_arquivos a
               JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
               WHERE a.no_categoria = 'Imagem' AND a.dt_ocr IS NULL
                 AND a.co_caminho_zip IS NOT NULL AND a.tp_mime <> 'image/webp'
                 AND a.qt_bytes >= %s AND e.id_inquerito = %s""",
            (_BYTES_MIN, job["id_inquerito"]))
        restantes = int(cur.fetchone()[0])
    grupos: dict[str, list] = {}
    for no_ufdr, id_arquivo, caminho_zip in linhas:
        grupos.setdefault(no_ufdr, []).append([id_arquivo, caminho_zip])
    return {"tp": job["tp_job"],
            "grupos": [[k, v] for k, v in sorted(grupos.items())],
            "restantes": restantes,
            "cursor": linhas[-1][1] if linhas else depois}


def _plano_classificacao(job: dict, depois: int, lote: int) -> dict:
    query = """SELECT a.id_arquivo, a.co_caminho_zip, e.no_arquivo
               FROM investigacao.arq_tb01_arquivos a
               JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
               WHERE a.no_categoria = 'Imagem'
                 AND a.classificacao_ia IS NULL
                 AND a.co_caminho_zip IS NOT NULL
                 AND a.tp_mime != 'image/webp'
                 AND a.qt_bytes >= 51200
                 AND a.id_arquivo > %s
                 AND e.id_inquerito = %s"""
    params: list = [depois, job["id_inquerito"]]
    if job["id_extracao"] is not None:
        query += " AND a.id_extracao = %s"
        params.append(job["id_extracao"])
    query += " ORDER BY a.id_arquivo LIMIT %s"
    params.append(lote)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT termo FROM investigacao.categorias_imagem
                       WHERE in_ativo = TRUE ORDER BY id_categoria""")
        termos = [r[0] for r in cur.fetchall()]
        if not termos:
            return {"tp": job["tp_job"], "erro": "Nenhuma categoria ativa cadastrada."}
        cur.execute(query, params)
        linhas = cur.fetchall()
        cur.execute(
            """SELECT count(*) FROM investigacao.arq_tb01_arquivos a
               JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
               WHERE a.no_categoria = 'Imagem' AND a.classificacao_ia IS NULL
                 AND a.co_caminho_zip IS NOT NULL AND a.tp_mime != 'image/webp'
                 AND a.qt_bytes >= 51200 AND e.id_inquerito = %s""",
            (job["id_inquerito"],))
        restantes = int(cur.fetchone()[0])
    return {"tp": job["tp_job"], "termos": termos,
            "imagens": [[id_arq, ufdr, zip_] for id_arq, zip_, ufdr in linhas],
            "restantes": restantes,
            "cursor": linhas[-1][0] if linhas else depois}


def _plano_facial(job: dict, depois: int, lote: int) -> dict:
    from funcoes.comparar_rosto_utils import (
        _ler_embeddings_salvos, _listar_imagens_inquerito)

    todas = _listar_imagens_inquerito(job["id_inquerito"], job["id_extracao"])
    total = len(todas)
    reaproveitados = 0
    imagens = []
    pagina = [i for i in todas if int(i["id_arquivo"]) > depois][:lote]
    if pagina:
        salvos = _ler_embeddings_salvos([i["id_arquivo"] for i in pagina])
        reaproveitados = len(salvos)
        imagens = [i for i in pagina if i["id_arquivo"] not in salvos]
    # total de reaproveitados do inquérito inteiro (só na 1ª página — é o "pulo do cache" que a
    # tela mostra como processado antes mesmo de o nó começar)
    if depois == 0 and todas:
        todos_salvos = _ler_embeddings_salvos([i["id_arquivo"] for i in todas])
        reaproveitados = len(todos_salvos)
    cursor = pagina[-1]["id_arquivo"] if pagina else depois
    return {"tp": job["tp_job"], "imagens": imagens, "cursor": cursor,
            "total": total, "reaproveitados": reaproveitados,
            "limite": int((job["tx_parametros"] or {}).get("limite") or 50)}


# ------------------------------------------------------------------ resultado (lote)


def aplicar_lote(id_servidor: int, id_job: int, corpo: str) -> dict:
    """Recebe o NDJSON de resultados e grava com as MESMAS funções do caminho local do central
    (paridade estrutural — ON CONFLICT dos embeddings, carimbo DT_OCR, tudo igual)."""
    job = _conferir(id_servidor, id_job)
    if job is None:
        raise LookupError("job não é deste nó ou já saiu de 'processando'")
    # NDJSON separado por \n (o no junta por \n com ensure_ascii=False, que deixa U+2028/
    # U+2029/NEL crus na string). splitlines() cortaria por esses caracteres e o json.loads
    # estouraria em texto de OCR. Fender so por \n.
    linhas = [linha for linha in corpo.split("\n") if linha.strip()]
    if not linhas:
        return {"gravados": 0}
    import json as _json

    registros = [_json.loads(linha) for linha in linhas]
    tp = job["tp_job"]
    if tp == jobs_utils.TP_OCR:
        from funcoes.ocr_utils import _salvar_resultados

        _salvar_resultados([(int(r["a"]), r.get("t")) for r in registros])
    elif tp == jobs_utils.TP_CLASSIFICACAO:
        from funcoes.classificacao_imagem_utils import _salvar_resultados

        _salvar_resultados([{"id_arquivo": int(r["a"]), "classificacao": r.get("c"),
                             "confianca": r.get("f")}
                            for r in registros if r.get("c") is not None])
    elif tp == jobs_utils.TP_COMPARACAO_FACIAL:
        from funcoes.comparar_rosto_utils import _gravar_embeddings

        novos = [(int(r["a"]), base64.b64decode(r["v"])) for r in registros if r.get("v")]
        with get_connection() as conn:
            _gravar_embeddings(conn, novos)
    else:
        raise LookupError(f"tipo de job não roteável: {tp}")
    return {"gravados": len(registros), "cancelar": _cancelar_agora(id_job)}


def _cancelar_agora(id_job: int) -> bool:
    from funcoes.db_utils import get_connection as _gc

    with _gc() as conn, conn.cursor() as cur:
        cur.execute("SELECT fl_cancelar FROM investigacao.job_tb01_jobs WHERE id_job = %s",
                    (id_job,))
        row = cur.fetchone()
    return bool(row and row[0])


# ------------------------------------------------------------------ estado / finais


def estado(id_servidor: int, id_job: int, payload: dict) -> dict:
    """Os callbacks de estado do job-irmão da entrega de extração: progresso, concluir, erro,
    finalizar (facial) e cancelado. A resposta carrega `cancelar` (o "parar" da tela)."""
    job = _conferir(id_servidor, id_job)
    if job is None:
        # já fechado: o nó interpreta 404 como "pare" — responder cancelar=True seria mais útil,
        # mas a rota devolve 404 e o no_jobs trata como parar; ver no_jobs._esperar
        raise LookupError("job não é deste nó ou já terminou")
    tipo = (payload or {}).get("t")
    if tipo == "progresso":
        cancelar = jobs_utils.progresso(
            id_job,
            processados=payload.get("processados"),
            total=payload.get("total"),
            erros=payload.get("erros"),
            metricas=payload.get("metricas"))
        return {"cancelar": cancelar}
    if tipo == "concluir":
        jobs_utils.concluir(id_job, metricas=payload.get("metricas"))
        return {"ok": True}
    if tipo == "erro":
        jobs_utils.falhar(id_job, str(payload.get("erro") or "erro no nó")[:2000])
        return {"ok": True}
    if tipo == "cancelado":
        jobs_utils.marcar_cancelado(id_job)
        return {"ok": True}
    if tipo == "finalizar":
        return _facial_finalizar(job, payload, id_servidor)
    raise ValueError(f"callback de estado desconhecido: {tipo!r}")


def _facial_finalizar(job: dict, payload: dict, id_servidor: int | None = None) -> dict:
    """A estação devolveu os vetores que calculou; a comparação final é uma multiplicação de
    matriz contra TODOS os vetores do inquérito (cache antigo + recém-chegados) — milissegundos.
    O vetor da referência veio no próprio callback (a estação é quem tem o CLIP carregado); sem
    CLIP no central, sem nenhuma imagem atravessando a rede."""
    import base64 as b64

    import numpy as np

    from funcoes.comparar_rosto_utils import (
        _ler_embeddings_salvos, _listar_imagens_inquerito)

    ref_b64 = payload.get("ref_v")
    if not ref_b64:
        jobs_utils.falhar(job["id_job"], "finalize sem o vetor da referência.")
        return {"ok": False}
    limite = int((job["tx_parametros"] or {}).get("limite") or 50)
    imagens = _listar_imagens_inquerito(job["id_inquerito"], job["id_extracao"])
    salvos = _ler_embeddings_salvos([i["id_arquivo"] for i in imagens])
    itens = [i for i in imagens if i["id_arquivo"] in salvos]
    if not itens:
        jobs_utils.concluir(job["id_job"], resultado={"matches": []})
        return {"ok": True, "matches": 0}

    ref = np.frombuffer(b64.b64decode(ref_b64), dtype="<f2").astype(np.float32)
    matriz = np.stack([salvos[i["id_arquivo"]] for i in itens]).astype(np.float32)
    similaridades = np.clip(matriz @ ref, 0.0, 1.0)
    k = min(limite, len(itens))
    ordem = np.argsort(-similaridades)[:k]
    resultados = []
    for indice in ordem.tolist():
        item = itens[indice]
        valor = float(similaridades[indice])
        resultados.append({
            "id_arquivo": item["id_arquivo"],
            "no_arquivo": item["no_arquivo"],
            "tx_caminho": item["tx_caminho"],
            "tp_mime": item["tp_mime"],
            "id_extracao": item["id_extracao"],
            "no_dispositivo": item.get("no_dispositivo"),
            "similaridade": round(valor * 100, 2),
        })
    jobs_utils.concluir(job["id_job"], resultado={"matches": resultados},
                        metricas={"no": id_servidor or True})
    logger.info("Comparação facial do job %s concluída pelo nó com %s candidatas",
                job["id_job"], len(itens))
    return {"ok": True, "matches": len(resultados)}

# ------------------------------------------------------------------ rollout de update em levas
# (docs/no_descentralizado.md, 'rollout de update'). O botao 'Atualizar todos' da tela
# Administracao -> Servidores cria um job global (escopo 'nos') e este worker conduz as levas:
# no maximo N nos se atualizando ao mesmo tempo, cada um confirmado pela volta da versao certa
# no heartbeat / GET /no/estado. Um no que falha NAO trava as levas seguintes � falha individual
# anotada em tx_metricas.falhas, o rollout segue. Cancelar o job para de disparar levas novas
# (o que ja comecou se atualiza sozinho la, nao ha como voltar atras com honra).

_ROLLOUT_TIMEOUT_NO_S = 30 * 60   # build+reboot na estacao pode passar de 20 min em link lento
_ROLLOUT_POLL_S = 10.0


def _mais_atrasada(atual: str | None, alvo: str) -> bool:
    from funcoes import versao_utils

    atual = (atual or '').lstrip('v')
    alvo = (alvo or '').lstrip('v')
    if not alvo:
        return False
    if not atual:
        return True
    try:
        return versao_utils.como_tupla(atual) < versao_utils.como_tupla(alvo)
    except ValueError:
        return True  # versao ilegivel no heartbeat tambem e atraso (mesma regra do /verificar)


def rollout_nos(id_job: int, max_parallel: int = 3) -> None:
    '''Conduz o rollout. Roda na thread que a rota criou; o reaper de jobs (batimento a cada
    leva) cobre o processo que morrer no meio.'''
    import threading
    import time

    from funcoes import no_ponte, no_utils, versao_utils

    try:
        d = versao_utils.checar_atualizacao(forcar=True)
        alvo = str(d.get('versao_disponivel') or '').lstrip('v')
    except Exception:
        alvo = ''
        logger.warning('Rollout: check de versao falhou � nos vao usar o disponivel.json local')
        jobs_utils.bater(id_job)

    atrasados = [n for n in no_utils.listar() if n.get('in_ativa')
                 and _mais_atrasada(n.get('tx_versao'), alvo)]
    if not atrasados:
        jobs_utils.concluir(id_job, metricas={'alvo': alvo or None, 'ok': [], 'falhas': {},
                                              'mensagem': 'nenhum n� atr�s da vers�o alvo'})
        return

    total = len(atrasados)
    cancelar = jobs_utils.progresso(id_job, total=total, processados=0, erros=0,
                                    metricas={'alvo': alvo or None})
    ok: list[str] = []
    falhas: dict[str, str] = {}
    fila = list(atrasados)
    em_curso: dict[int, dict] = {}   # id_servidor -> {no_servidor, desde}

    def _grava() -> bool:
        return jobs_utils.progresso(id_job, processados=len(ok) + len(falhas), erros=len(falhas),
                                    metricas={'ok': ok, 'falhas': falhas})

    try:
        while (fila or em_curso) and not cancelar:
            while fila and len(em_curso) < max(1, max_parallel):
                no = fila.pop(0)
                nome = no['no_servidor']
                try:
                    res = no_ponte.atualizar_no(no, alvo or None)
                    corpo = {}
                    try:
                        corpo = res.json() or {}
                    except ValueError:
                        pass
                    if res.status_code >= 400 or not corpo.get('aceito'):
                        detalhe = str(corpo.get('erro') or f'HTTP {res.status_code}')[:300]
                        if 'j� est� em dia' in detalhe.lower() or 'n�o � mais nova' in detalhe.lower():
                            ok.append(nome)   # o n� j� estava na vers�o � conta como feito
                        else:
                            falhas[nome] = detalhe
                        continue
                except no_ponte.NoIndisponivel as exc:
                    falhas[nome] = str(exc)[:300]
                    continue
                em_curso[no['id_servidor']] = {'no_servidor': nome, 'desde': time.time()}
            if not em_curso:
                cancelar = _grava()
                if fila and not cancelar:
                    time.sleep(1.0)
                continue
            time.sleep(_ROLLOUT_POLL_S)
            for id_no, info in list(em_curso.items()):
                atual = _versao_reportada(id_no)
                if atual and not _mais_atrasada(atual, alvo):
                    ok.append(info['no_servidor'])
                    em_curso.pop(id_no)
                    continue
                recusa = _recusa_atualizacao(id_no)
                if recusa:
                    falhas[info['no_servidor']] = recusa
                    em_curso.pop(id_no)
                    continue
                if time.time() - info['desde'] > _ROLLOUT_TIMEOUT_NO_S:
                    falhas[info['no_servidor']] = (
                        'o n� n�o voltou na vers�o alvo dentro de 30 min (acompanhe no modal)')
                    em_curso.pop(id_no)
            cancelar = _grava()

        if cancelar:
            for info in em_curso.values():
                falhas[info['no_servidor']] = 'rollout cancelado � o n� pode ter atualizado sozinho'
            jobs_utils.marcar_cancelado(id_job)
        elif falhas and not ok:
            jobs_utils.falhar(id_job, f'nenhum n� atualizou: {list(falhas.items())[:5]}')
        else:
            jobs_utils.concluir(id_job, metricas={'ok': ok, 'falhas': falhas})
            if falhas:
                logger.warning('Rollout conclu�do com falhas: %s', falhas)
    except Exception as exc:
        logger.exception('Rollout de n�s (job %s) morreu no meio', id_job)
        jobs_utils.falhar(id_job, f'{type(exc).__name__}: {exc}'[:2000])


def _versao_reportada(id_servidor: int) -> str | None:
    '''A versao que o no reporta � heartbeat (DT/TX_VERSAO) e, se o cano respondeu agora, o
    GET /no/estado (o heartbeat de 60 s atrasaria demais a confirmacao da leva).'''
    from funcoes import no_ponte, no_utils

    no = no_utils.obter(id_servidor)
    if no is None:
        return None
    versao = no.get('tx_versao')
    if versao:
        try:
            res = no_ponte.estado_maquina(no)
            if res.status_code == 200:
                versao = (res.json() or {}).get('versao') or versao
        except Exception:
            pass  # no reiniciando agora e esperado
    return versao


def _recusa_atualizacao(id_servidor: int) -> str | None:
    '''O agente do no registrou falha explicita no progresso da atualizacao? None = segue.'''
    from funcoes import no_ponte, no_utils

    no = no_utils.obter(id_servidor)
    if no is None:
        return 'n� removido do registro no meio do rollout'
    try:
        res = no_ponte.atualizacao_estado(no)
        if res.status_code != 200:
            return None   # fora do ar no meio do build e esperado
        prog = res.json() or {}
    except Exception:
        return None
    resultado = prog.get('resultado')
    if isinstance(resultado, dict) and not prog.get('rodando'):
        if resultado.get('ok'):
            return None
        return str(resultado.get('erro') or resultado)[:300]
    return None
