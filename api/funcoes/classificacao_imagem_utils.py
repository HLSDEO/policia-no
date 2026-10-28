import logging
import threading
from io import BytesIO
from typing import Any

import torch
from PIL import Image as PILImage
from torch import nn
from transformers import CLIPModel, CLIPProcessor

from funcoes import jobs_pesados, jobs_utils, ufdr_zip
from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.investigacao_utils import fonte_ufdr

logger = logging.getLogger(__name__)

_MODEL_LOCK = threading.Lock()
_MODEL: dict[str, Any] = {}  # {"model": ..., "processor": ...}


def _carregar_modelo():
    if "model" in _MODEL:
        return _MODEL["model"], _MODEL["processor"]
    with _MODEL_LOCK:
        if "model" in _MODEL:
            return _MODEL["model"], _MODEL["processor"]
        env = parse_env(read_env_text())
        threads = int(env.get("CLIP_NUM_THREADS", "4"))
        torch.set_num_threads(threads)
        logger.info("Carregando modelo CLIP (openai/clip-vit-base-patch32)...")
        model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        model.eval()
        _MODEL["model"] = model
        _MODEL["processor"] = processor
        logger.info("Modelo CLIP carregado.")
        return model, processor


def _extrair_imagem_bytes(id_arquivo: int) -> bytes | None:
    query = """
        SELECT a.co_caminho_zip, e.no_arquivo
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE a.id_arquivo = %s AND a.co_caminho_zip IS NOT NULL
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_arquivo,))
            row = cur.fetchone()
    if row is None:
        logger.warning("Imagem %s: sem co_caminho_zip ou extração não encontrada", id_arquivo)
        return None
    caminho_zip, no_arquivo_ufdr = row
    caminho_ufdr = fonte_ufdr(no_arquivo_ufdr)
    if not caminho_ufdr:
        logger.warning("Imagem %s: arquivo UFDR não encontrado (central nem nó): %s",
                       id_arquivo, no_arquivo_ufdr)
        return None
    try:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            if caminho_zip not in zf.namelist():
                # tenta normalizar barras
                normalizado = caminho_zip.replace("\\", "/")
                if normalizado not in zf.namelist():
                    logger.warning("Imagem %s: caminho '%s' não encontrado no zip %s", id_arquivo, caminho_zip, no_arquivo_ufdr)
                    return None
                caminho_zip = normalizado
            return zf.read(caminho_zip)
    except Exception as exc:
        logger.warning("Erro ao ler imagem %s do zip %s [%s]: %s: %s", id_arquivo, no_arquivo_ufdr, caminho_zip, type(exc).__name__, exc)
        return None


def _classificar_lote(model, processor, imagens_bytes: list[tuple[int, bytes]], termos: list[str]) -> list[dict]:
    resultados = []
    for id_arquivo, dados in imagens_bytes:
        try:
            img = PILImage.open(BytesIO(dados))
            # converte modos que o CLIP não suporta (RGBA, P, CMYK, etc.) para RGB
            if img.mode != "RGB":
                img = img.convert("RGB")
            entradas = processor(text=termos, images=img, return_tensors="pt", padding=True)
            with torch.no_grad():
                saidas = model(**entradas)
            logits_per_image = saidas.logits_per_image
            probs = nn.functional.softmax(logits_per_image, dim=1)
            melhor_idx = probs.argmax().item()
            confianca = probs[0][melhor_idx].item()
            resultados.append({
                "id_arquivo": id_arquivo,
                "classificacao": termos[melhor_idx],
                "confianca": round(confianca, 4),
            })
        except Exception as exc:
            logger.warning("Erro ao classificar imagem %s: %s: %s", id_arquivo, type(exc).__name__, exc)
            resultados.append({
                "id_arquivo": id_arquivo,
                "classificacao": None,
                "confianca": None,
            })
    return resultados


def _salvar_resultados(resultados: list[dict]):
    with get_connection() as conn:
        with conn.cursor() as cur:
            for r in resultados:
                if r["classificacao"] is None:
                    continue
                cur.execute(
                    """UPDATE investigacao.arq_tb01_arquivos
                       SET classificacao_ia = %s, confianca_ia = %s
                       WHERE id_arquivo = %s""",
                    (r["classificacao"], r["confianca"], r["id_arquivo"]),
                )
        conn.commit()


def listar_categorias() -> list[dict]:
    query = """SELECT id_categoria, termo, rotulo_exibicao, in_ativo, criado_em
               FROM investigacao.categorias_imagem
               ORDER BY criado_em DESC"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, row)) for row in cur.fetchall()]


def criar_categoria(termo: str, rotulo_exibicao: str) -> dict:
    query = """INSERT INTO investigacao.categorias_imagem (termo, rotulo_exibicao)
               VALUES (%s, %s)
               RETURNING id_categoria, termo, rotulo_exibicao, in_ativo, criado_em"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (termo.strip(), rotulo_exibicao.strip()))
            colunas = [d.name for d in cur.description]
            row = cur.fetchone()
        conn.commit()
    return dict(zip(colunas, row))


def excluir_categoria(id_categoria: int) -> bool:
    query = """DELETE FROM investigacao.categorias_imagem WHERE id_categoria = %s"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_categoria,))
            deleted = cur.rowcount
        conn.commit()
    return deleted > 0


def alternar_ativo_categoria(id_categoria: int) -> dict | None:
    query = """UPDATE investigacao.categorias_imagem
               SET in_ativo = NOT in_ativo
               WHERE id_categoria = %s
               RETURNING id_categoria, termo, rotulo_exibicao, in_ativo, criado_em"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_categoria,))
            row = cur.fetchone()
            if row is None:
                return None
            colunas = [d.name for d in cur.description]
        conn.commit()
    return dict(zip(colunas, row))


def classificar_imagens_pendentes(
    id_job: int,
    id_inquerito: int,
    id_extracao: int | None = None,
    batch_size: int = 16,
    max_imagens: int | None = None,
):
    """Classifica com CLIP as imagens do inquérito ainda sem classificação.

    Todo o estado (progresso, contadores, pedido de parar) vive na linha `id_job` de
    JOB_TB01_JOBS — ver funcoes/jobs_utils.py. O guard de "só uma por inquérito" é o índice único
    da tabela, aplicado no jobs_utils.criar() que a rota chama antes de subir esta thread."""
    env = parse_env(read_env_text())
    threads = int(env.get("CLIP_NUM_THREADS", "4"))
    torch.set_num_threads(threads)

    processados = 0
    erros = 0
    try:
        query_categorias = """SELECT termo FROM investigacao.categorias_imagem
                              WHERE in_ativo = TRUE ORDER BY id_categoria"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query_categorias)
                termos = [row[0] for row in cur.fetchall()]

        if not termos:
            raise RuntimeError("Nenhuma categoria ativa cadastrada.")

        query_imagens = """SELECT a.id_arquivo
                           FROM investigacao.arq_tb01_arquivos a
                           JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                           WHERE a.no_categoria = 'Imagem'
                             AND a.classificacao_ia IS NULL
                             AND a.co_caminho_zip IS NOT NULL
                             AND a.tp_mime != 'image/webp'
                             AND a.qt_bytes >= 51200
                             AND e.id_inquerito = %s"""
        params: list = [id_inquerito]
        if id_extracao is not None:
            query_imagens += " AND a.id_extracao = %s"
            params.append(id_extracao)
        if max_imagens is not None:
            query_imagens += " LIMIT %s"
            params.append(max_imagens)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query_imagens, params)
                todos_ids = [row[0] for row in cur.fetchall()]

        total_imagens = len(todos_ids)
        jobs_utils.progresso(id_job, total=total_imagens)

        if total_imagens == 0:
            jobs_utils.concluir(id_job)
            return

        cancelar = False
        with jobs_pesados.vaga(id_job, "Classificação de imagem"):
            model, processor = _carregar_modelo()
            # cold-load pode levar minutos: batimento antes do primeiro lote (ver C2)
            jobs_utils.bater(id_job)

            for inicio in range(0, total_imagens, batch_size):
                if cancelar:
                    break
                lote_ids = todos_ids[inicio:inicio + batch_size]
                imagens_bytes: list[tuple[int, bytes]] = []
                for id_arq in lote_ids:
                    dados = _extrair_imagem_bytes(id_arq)
                    if dados:
                        imagens_bytes.append((id_arq, dados))

                if imagens_bytes:
                    resultados = _classificar_lote(model, processor, imagens_bytes, termos)

                    resultados_validos = [r for r in resultados if r["classificacao"] is not None]
                    if resultados_validos:
                        _salvar_resultados(resultados_validos)

                    erros_lote = sum(1 for r in resultados if r["classificacao"] is None)
                else:
                    erros_lote = len(lote_ids)

                processados += len(lote_ids)
                erros += erros_lote
                # uma ida ao banco por lote grava o progresso E devolve o pedido de cancelamento
                cancelar = jobs_utils.progresso(id_job, processados=processados, erros=erros)

        if cancelar:
            jobs_utils.marcar_cancelado(id_job)
        else:
            jobs_utils.concluir(id_job)
    except jobs_pesados.SemVagaCancelado:
        jobs_utils.marcar_cancelado(id_job)
    except Exception as exc:
        logger.exception("Falha na classificação de imagens do inquérito %s (job %s)",
                         id_inquerito, id_job)
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])
