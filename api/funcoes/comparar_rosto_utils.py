"""Comparação de uma foto de referência contra as imagens do inquérito.

Modo local (padrão): CLIP rodando na CPU da API. O custo real é o CLIP: ~20 ms por imagem em CPU,
o que dá alguns minutos num inquérito com milhares de fotos. Três coisas seguram esse custo:

- o vetor (embedding) de cada imagem do aparelho não depende da foto de referência, então
  fica guardado em ARQ_TB02_EMBEDDING_IMAGEM. Da 2ª comparação em diante o trabalho é uma
  multiplicação de matriz — milissegundos, mesmo com dezenas de milhares de imagens;
- leitura do .ufdr, decodificação e pré-processamento rodam num pool de threads (PIL e o
  zlib soltam o GIL), com um lote de adiantamento: enquanto a CPU faz o forward do lote N,
  as threads já estão preparando o N+1;
- o pré-processamento é feito à mão (resize bicúbico + center crop + normalização) em vez
  do CLIPProcessor. É bit a bit o mesmo resultado, mas sem as cópias intermediárias do
  processor e, principalmente, paralelizável — o processor só aceita o lote inteiro de uma
  vez, na thread que chamou.

Modo api: cada imagem do inquérito é enviada junto com a referência a uma API de visão
(google-vision via Gemini, qwen-vision ou outro OpenAI-compatível) que devolve a similaridade
0-100. Uma chamada por imagem — caro em inquérito grande, por isso há um teto (ver
_LIMITE_API_COMPARACOES); a comparação local continua sendo a recomendada para volume.
"""

import base64
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image as PILImage

from funcoes import jobs_pesados, jobs_utils, ufdr_cache
from funcoes.classificacao_imagem_utils import _carregar_modelo
from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.ia_provedores import ErroProvedorIA
from funcoes.investigacao_utils import fonte_ufdr

logger = logging.getLogger(__name__)

# Identifica o espaço vetorial gravado no banco: modelo + versão do pré-processamento.
# Mudou qualquer um dos dois? Suba a versão — os vetores antigos param de ser lidos.
MODELO_EMBEDDING = "clip-vit-base-patch32/v1"

# Teto de imagens comparadas por execução no modo api (uma chamada de API por imagem; um
# inquérito com dezenas de milhares de fotos custaria caro demais sem um limite).
_LIMITE_API_COMPARACOES = 200
_TIMEOUT_API_COMPARACAO = 120

_DIM_EMBEDDING = 512
_BATCH_SIZE = 32
# threads que leem/decodificam enquanto a CPU faz o forward. Acima de ~4 não adianta:
# o gargalo passa a ser o CLIP, e as threads só disputam CPU com ele.
_WORKERS_IO = 4

# pré-processamento do CLIP (mesmos valores do preprocessor_config.json do modelo)
_TAM_ENTRADA = 224
_MEDIA = torch.tensor([0.48145466, 0.4578275, 0.40821073]).view(1, 3, 1, 1)
_DESVIO = torch.tensor([0.26862954, 0.26130258, 0.27577711]).view(1, 3, 1, 1)

class _Contadores:
    """Contadores de UMA comparação, espelhados na linha do job em JOB_TB01_JOBS.

    Substitui o dicionário de módulo _PROGRESS, que era estado do processo: o segundo analista
    levava 409 por causa da comparação de outra delegacia, o cancelamento parava a do colega, e o
    GET de progresso devolvia a lista de rostos parecidos do inquérito alheio.

    Os números ficam em memória do worker e vão ao banco a cada lote — gravar por imagem seria uma
    ida ao banco por foto. `avancar` devolve o pedido de cancelamento na mesma escrita, no lugar
    da leitura do antigo _PROGRESS["cancelado"]."""

    def __init__(self, id_job: int) -> None:
        self.id_job = id_job
        self.total = 0
        self.processados = 0
        self.reaproveitados = 0
        self.erros = 0
        self.cancelado = False

    def definir_total(self, total: int, reaproveitados: int = 0) -> None:
        self.total = total
        self.reaproveitados = reaproveitados
        self.processados = reaproveitados
        self.cancelado = jobs_utils.progresso(
            self.id_job, total=total, processados=reaproveitados, erros=0,
            metricas={"reaproveitados": reaproveitados})

    def avancar(self, processados: int = 0, erros: int = 0) -> bool:
        self.processados += processados
        self.erros += erros
        self.cancelado = jobs_utils.progresso(
            self.id_job, processados=self.processados, erros=self.erros)
        return self.cancelado

    def checar_cancelado(self) -> bool:
        self.cancelado = jobs_utils.bater(self.id_job)
        return self.cancelado


# ---------------------------------------------------------------- pré-processamento

def _preparar_pixels(dados: bytes) -> "np.ndarray":
    """Bytes de uma imagem -> array uint8 224x224x3 pronto para o CLIP.

    Reproduz exatamente o CLIPImageProcessor: converte para RGB, reduz o lado menor para
    224 px (bicúbico), corta o centro e devolve os pixels crus — a divisão por 255 e a
    normalização são feitas de uma vez só para o lote inteiro, em _tensor_do_lote."""
    img = PILImage.open(BytesIO(dados))
    if img.mode != "RGB":
        img = img.convert("RGB")
    larg, alt = img.size
    if larg <= alt:
        nova = (_TAM_ENTRADA, int(_TAM_ENTRADA * alt / larg))
    else:
        nova = (int(_TAM_ENTRADA * larg / alt), _TAM_ENTRADA)
    img = img.resize(nova, PILImage.BICUBIC)
    larg, alt = img.size
    esq = (larg - _TAM_ENTRADA) // 2
    topo = (alt - _TAM_ENTRADA) // 2
    img = img.crop((esq, topo, esq + _TAM_ENTRADA, topo + _TAM_ENTRADA))
    return np.asarray(img, dtype=np.uint8)


def _tensor_do_lote(arrays: list) -> "torch.Tensor":
    lote = torch.from_numpy(np.stack(arrays)).permute(0, 3, 1, 2).float()
    return lote.div_(255.0).sub_(_MEDIA).div_(_DESVIO)


def _embeddings_do_lote(model, arrays: list) -> "torch.Tensor":
    with torch.no_grad():
        saidas = model.vision_model(pixel_values=_tensor_do_lote(arrays))
        embeddings = model.visual_projection(saidas.pooler_output)
        return F.normalize(embeddings, p=2, dim=1)


def _carregar_pixels(item: dict) -> "np.ndarray | None":
    """Lê a imagem de dentro do .ufdr e devolve os pixels prontos. Roda no pool de threads.

    Não passa pelo cache de conteúdo do ufdr_cache de propósito: cada imagem é lida uma vez
    só numa varredura, e guardá-las despejaria do cache justamente os anexos de conversa que
    estão sendo usados na tela."""
    caminho_ufdr = fonte_ufdr(item["no_arquivo_ufdr"])
    if not caminho_ufdr:
        return None
    dados = None
    for tentativa in (item["co_caminho_zip"], item["co_caminho_zip"].replace("\\", "/")):
        try:
            dados = ufdr_cache.obter_zip(caminho_ufdr).read(tentativa)
            break
        except Exception:
            continue
    if dados is None:
        return None
    try:
        return _preparar_pixels(dados)
    except Exception:
        return None


# ---------------------------------------------------------------- banco

def _listar_imagens_inquerito(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Fila de imagens da varredura. `id_extracao` restringe a um aparelho — é o que atende ao
    deep-link `?extracao=` das telas de extração: sem ele a comparação roda no inquérito inteiro e
    quem abriu a tela por um aparelho específico recebe resultados dos outros celulares também."""
    filtro_extracao = "AND e.id_extracao = %s" if id_extracao else ""
    query = f"""
        SELECT a.id_arquivo, a.no_arquivo, a.tx_caminho, a.tp_mime,
               a.co_caminho_zip, e.no_arquivo AS no_arquivo_ufdr,
               e.id_extracao, e.no_dispositivo
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE a.no_categoria = 'Imagem'
          AND a.co_caminho_zip IS NOT NULL
          AND a.tp_mime != 'image/webp'
          AND a.qt_bytes >= 102400
          AND e.id_inquerito = %s
          {filtro_extracao}
        ORDER BY a.id_arquivo
    """
    parametros = [id_inquerito] + ([id_extracao] if id_extracao else [])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, parametros)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, row)) for row in cur.fetchall()]


def extracao_pertence_inquerito(id_inquerito: int, id_extracao: int) -> bool:
    """A extração existe e é deste inquérito. A rota confere antes de aceitar `?extracao=`, para
    que um id de outro inquérito (ou de outra delegacia) não vire filtro da varredura."""
    query = "SELECT 1 FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s AND id_inquerito = %s"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_extracao, id_inquerito))
            return cur.fetchone() is not None


def _ler_embeddings_salvos(ids_arquivo: list[int]) -> dict[int, "np.ndarray"]:
    """Vetores já calculados para estes arquivos, por id_arquivo (float32 normalizado)."""
    if not ids_arquivo:
        return {}
    query = """
        SELECT id_arquivo, vt_embedding
        FROM investigacao.arq_tb02_embedding_imagem
        WHERE no_modelo = %s AND id_arquivo = ANY(%s)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (MODELO_EMBEDDING, ids_arquivo))
            linhas = cur.fetchall()
    salvos = {}
    for id_arquivo, dados in linhas:
        vetor = np.frombuffer(bytes(dados), dtype="<f2")
        if vetor.shape[0] != _DIM_EMBEDDING:
            continue  # vetor de outra dimensão (modelo trocado sem subir a versão) — recalcula
        salvos[id_arquivo] = vetor.astype(np.float32)
    return salvos


_SQL_GRAVAR_EMBEDDING = """
    INSERT INTO investigacao.arq_tb02_embedding_imagem (id_arquivo, no_modelo, vt_embedding)
    VALUES (%s, %s, %s)
    ON CONFLICT (id_arquivo, no_modelo) DO NOTHING
"""


def _gravar_embeddings(conn, novos: list[tuple[int, bytes]]) -> None:
    """Guarda os vetores recém-calculados, num lote por vez, na conexão que a varredura já
    mantém aberta. ON CONFLICT porque uma reexecução após cancelamento (ou duas comparações
    que se cruzem) pode recalcular a mesma imagem."""
    if not novos:
        return
    with conn.cursor() as cur:
        cur.executemany(_SQL_GRAVAR_EMBEDDING, [(i, MODELO_EMBEDDING, d) for i, d in novos])
    conn.commit()


# ---------------------------------------------------------------- comparação

def _extrair_emb_referencia(model, imagem_bytes: bytes) -> "torch.Tensor | None":
    try:
        return _embeddings_do_lote(model, [_preparar_pixels(imagem_bytes)])
    except Exception as exc:
        logger.warning("Erro ao extrair embedding da referência: %s: %s", type(exc).__name__, exc)
        return None


def _calcular_pendentes(model, pendentes: list[dict],
                        contadores: "_Contadores") -> dict[int, "np.ndarray"]:
    """Calcula o embedding das imagens que ainda não estão em cache, em lotes, com um lote
    de adiantamento no pool de threads. Devolve {} se a comparação foi cancelada no meio."""
    calculados: dict[int, np.ndarray] = {}
    lotes = [pendentes[i:i + _BATCH_SIZE] for i in range(0, len(pendentes), _BATCH_SIZE)]
    if not lotes:
        return calculados

    # uma conexão só para a varredura inteira, em vez de uma por lote. ExitStack porque
    # get_connection() é um gerenciador de contexto (a conexão só sai do pool no __enter__) e o
    # cache é best-effort: precisamos poder abrir mão dela no meio do laço e ainda assim garantir
    # a devolução ao pool no fim. Antes daqui o código guardava o próprio gerenciador em `conn`
    # e chamava conn.cursor()/conn.close() nele, o que levantava AttributeError — o cache de
    # embeddings da comparação facial nunca chegou a gravar nada.
    pilha = ExitStack()
    conn = None
    try:
        conn = pilha.enter_context(get_connection())
    except Exception:
        logger.warning("Sem conexão para gravar embeddings em cache; seguindo sem cache", exc_info=True)

    try:
        with ThreadPoolExecutor(max_workers=_WORKERS_IO) as pool:
            fila = deque()

            def enfileirar(indice: int) -> None:
                if indice < len(lotes):
                    lote = lotes[indice]
                    fila.append((lote, [pool.submit(_carregar_pixels, item) for item in lote]))

            enfileirar(0)
            proximo = 1
            while fila:
                if contadores.cancelado:
                    return calculados

                lote, futuros = fila.popleft()
                # o lote seguinte já vai sendo lido/decodificado enquanto a CPU faz o forward deste
                enfileirar(proximo)
                proximo += 1

                arrays, itens_ok, erros = [], [], 0
                for item, futuro in zip(lote, futuros):
                    pixels = futuro.result()
                    if pixels is None:
                        erros += 1
                        continue
                    arrays.append(pixels)
                    itens_ok.append(item)

                if arrays:
                    try:
                        embeddings = _embeddings_do_lote(model, arrays).numpy()
                    except Exception:
                        logger.warning("Erro no forward do CLIP para um lote de imagens", exc_info=True)
                        embeddings = None
                    if embeddings is None:
                        erros += len(arrays)
                    else:
                        novos = []
                        for item, emb in zip(itens_ok, embeddings):
                            # passa pelo float16 antes de usar, não só ao gravar: assim esta
                            # execução dá exatamente o mesmo número que as próximas, que vão
                            # ler o vetor já arredondado de volta do banco
                            emb16 = emb.astype("<f2")
                            calculados[item["id_arquivo"]] = emb16.astype(np.float32)
                            novos.append((item["id_arquivo"], emb16.tobytes()))
                        if conn is not None:
                            try:
                                _gravar_embeddings(conn, novos)
                            except Exception:
                                # o cache é otimização: não pode derrubar a comparação
                                logger.warning("Falha ao gravar embeddings em cache", exc_info=True)
                                pilha.close()
                                conn = None

                contadores.avancar(processados=len(lote), erros=erros)
    finally:
        pilha.close()

    return calculados


# ---------------------------------------------------------------- modo api

def _config_comparacao() -> dict:
    """Configuração da comparação de imagens (.env):
    - local (padrão): CLIP rodando na CPU da API;
    - api: provedor de visão (google-vision ou qwen-vision) com URL/chave/modelo próprios."""
    env = parse_env(read_env_text())
    modo = (env.get("IMAGE_CMP_MODE") or "local").strip().lower()
    if modo == "api":
        return {
            "mode": "api",
            "provider": (env.get("IMAGE_CMP_PROVIDER") or "").strip().lower(),
            "apiUrl": (env.get("IMAGE_CMP_API_URL") or "").strip(),
            "apiKey": (env.get("IMAGE_CMP_API_KEY") or "").strip(),
            "model": (env.get("IMAGE_CMP_MODEL") or "").strip(),
        }
    return {"mode": "local"}


def _post_json(url: str, headers: dict, corpo: dict, timeout: int = _TIMEOUT_API_COMPARACAO) -> dict:
    dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", errors="replace")[:500]
        raise ErroProvedorIA(f"A API de visão respondeu com erro {e.code}: {detalhe}")
    except urllib.error.URLError as e:
        raise ErroProvedorIA(f"Não foi possível conectar na API de visão: {e.reason}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ErroProvedorIA(f"Resposta da API de visão em formato inesperado: {e}")


def _imagem_base64_jpeg(dados: bytes) -> str:
    """Imagem para base64 JPEG (reduzida, para não inflar o corpo da requisição)."""
    img = PILImage.open(BytesIO(dados))
    if img.mode != "RGB":
        img = img.convert("RGB")
    maior = max(img.size)
    if maior > 1600:
        escala = 1600 / maior
        img = img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))), PILImage.BICUBIC)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


_PROMPT_COMPARACAO_API = (
    "Compare a pessoa da primeira imagem com a pessoa da segunda imagem. "
    "Responda APENAS com um número inteiro de 0 a 100 indicando a probabilidade de "
    "serem a mesma pessoa (0 = certamente diferentes, 100 = certamente a mesma). "
    "Sem explicações, sem texto, só o número."
)


def _perguntar_similaridade_api(ref_b64: str, cand_b64: str, cfg: dict) -> float | None:
    """Pergunta a similaridade entre a referência e uma candidata à API de visão.
    Devolve 0-1 ou None se a resposta não trouxer um número interpretável."""
    provider = cfg.get("provider", "qwen-vision")
    api_url = cfg.get("apiUrl", "")
    api_key = cfg.get("apiKey", "")
    modelo = cfg.get("model", "")

    if provider == "google-vision":
        # Google Gemini (generativelanguage) — formato nativo com inline_data
        corpo = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": _PROMPT_COMPARACAO_API},
                    {"inline_data": {"mime_type": "image/jpeg", "data": ref_b64}},
                    {"inline_data": {"mime_type": "image/jpeg", "data": cand_b64}},
                ],
            }]
        }
        url = f"{api_url.rstrip('/')}/models/{modelo}:generateContent"
        if api_key:
            url += f"?key={api_key}"
        resp = _post_json(url, {}, corpo)
        texto = (resp.get("candidates") or [{}])[0].get("content", {}).get("parts", [{}])[0].get("text") or ""
    else:
        # qwen-vision / outro — formato OpenAI-compatível com imagem (data URL base64)
        conteudo = [
            {"type": "text", "text": _PROMPT_COMPARACAO_API},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{cand_b64}"}},
        ]
        corpo = {"model": modelo, "messages": [{"role": "user", "content": conteudo}]}
        cabecalhos = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = _post_json(f"{api_url.rstrip('/')}/chat/completions", cabecalhos, corpo)
        texto = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    m = re.search(r"\b(\d{1,3})\b", texto or "")
    if not m:
        return None
    valor = min(100, max(0, int(m.group(1))))
    return valor / 100.0


def _executar_comparacao_api(id_job: int, id_inquerito: int, imagem_upload_bytes: bytes,
                             limite: int, cfg: dict, id_extracao: int | None = None):
    """Comparação por API de visão: uma chamada por imagem do inquérito (com referência),
    até o teto _LIMITE_API_COMPARACOES. Sem cache de embeddings (não há vetores locais)."""
    try:
        ref_b64 = _imagem_base64_jpeg(imagem_upload_bytes)

        imagens = _listar_imagens_inquerito(id_inquerito, id_extracao)
        if not imagens:
            jobs_utils.concluir(id_job, resultado={"matches": []})
            return

        if len(imagens) > _LIMITE_API_COMPARACOES:
            logger.warning("Comparação por API: %s imagens no inquérito, limitando às %s primeiras",
                           len(imagens), _LIMITE_API_COMPARACOES)
            imagens = imagens[:_LIMITE_API_COMPARACOES]

        contadores = _Contadores(id_job)
        contadores.definir_total(len(imagens))

        resultados: list[dict] = []
        for item in imagens:
            if contadores.cancelado:
                jobs_utils.marcar_cancelado(id_job)
                return

            caminho_ufdr = fonte_ufdr(item["no_arquivo_ufdr"])
            dados = None
            if caminho_ufdr:
                for tentativa in (item["co_caminho_zip"], item["co_caminho_zip"].replace("\\", "/")):
                    try:
                        dados = ufdr_cache.obter_zip(caminho_ufdr).read(tentativa)
                        break
                    except Exception:
                        continue
            if dados is None:
                contadores.avancar(processados=1, erros=1)
                continue

            try:
                cand_b64 = _imagem_base64_jpeg(dados)
                sim = _perguntar_similaridade_api(ref_b64, cand_b64, cfg)
            except Exception as exc:
                logger.warning("Comparação por API: erro na imagem %s: %s: %s",
                               item["id_arquivo"], type(exc).__name__, exc)
                sim = None

            contadores.avancar(processados=1, erros=1 if sim is None else 0)
            if sim is not None:
                resultados.append({
                    "id_arquivo": item["id_arquivo"],
                    "no_arquivo": item["no_arquivo"],
                    "tx_caminho": item["tx_caminho"],
                    "tp_mime": item["tp_mime"],
                    "id_extracao": item["id_extracao"],
                    "no_dispositivo": item.get("no_dispositivo"),
                    "similaridade": round(sim * 100, 2),
                })

        resultados.sort(key=lambda r: r["similaridade"], reverse=True)
        jobs_utils.concluir(id_job, resultado={"matches": resultados[:limite]})
    except Exception as exc:
        logger.exception("Erro na comparação de rostos por API")
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])


def _executar_comparacao(id_job: int, id_inquerito: int, imagem_upload_bytes: bytes, limite: int,
                         id_extracao: int | None = None):
    cfg = _config_comparacao()
    if cfg["mode"] == "api":
        _executar_comparacao_api(id_job, id_inquerito, imagem_upload_bytes, limite, cfg, id_extracao)
        return

    contadores = _Contadores(id_job)
    try:
        with jobs_pesados.vaga(id_job, "Comparação facial"):
            model, _ = _carregar_modelo()
            # cold-load do torch+CLIP pode levar minutos em hardware modesto: sem este batimento
            # o reaper consideraria o job morto ainda antes do primeiro lote (ver C2)
            jobs_utils.bater(id_job)

            emb_referencia = _extrair_emb_referencia(model, imagem_upload_bytes)
            if emb_referencia is None:
                jobs_utils.falhar(id_job, "Não foi possível processar a imagem enviada.")
                return

            imagens = _listar_imagens_inquerito(id_inquerito, id_extracao)
            if not imagens:
                jobs_utils.concluir(id_job, resultado={"matches": []})
                return

            salvos = _ler_embeddings_salvos([i["id_arquivo"] for i in imagens])
            pendentes = [i for i in imagens if i["id_arquivo"] not in salvos]

            contadores.definir_total(len(imagens), reaproveitados=len(salvos))

            salvos.update(_calcular_pendentes(model, pendentes, contadores))

            if contadores.checar_cancelado():
                jobs_utils.marcar_cancelado(id_job)
                return

            # uma multiplicação de matriz só, contra todos os vetores de uma vez
            itens = [i for i in imagens if i["id_arquivo"] in salvos]
            if not itens:
                jobs_utils.concluir(id_job, resultado={"matches": []})
                return

            matriz = torch.from_numpy(np.stack([salvos[i["id_arquivo"]] for i in itens]))
            similaridades = (matriz @ emb_referencia.squeeze(0)).clamp_(0.0, 1.0)

            k = min(limite, len(itens))
            melhores = torch.topk(similaridades, k)
            resultados = []
            for valor, indice in zip(melhores.values.tolist(), melhores.indices.tolist()):
                item = itens[indice]
                resultados.append({
                    "id_arquivo": item["id_arquivo"],
                    "no_arquivo": item["no_arquivo"],
                    "tx_caminho": item["tx_caminho"],
                    "tp_mime": item["tp_mime"],
                    "id_extracao": item["id_extracao"],
                    "no_dispositivo": item.get("no_dispositivo"),
                    "similaridade": round(valor * 100, 2),
                })

            jobs_utils.concluir(id_job, resultado={"matches": resultados},
                                metricas={"reaproveitados": contadores.reaproveitados})

    except jobs_pesados.SemVagaCancelado:
        jobs_utils.marcar_cancelado(id_job)
    except Exception as exc:
        logger.exception("Erro na comparação de rostos")
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])


def iniciar_comparacao(id_job: int, id_inquerito: int, imagem_upload_bytes: bytes,
                       limite: int = 20, id_extracao: int | None = None):
    """Sobe a comparação em segundo plano. O guard de "só uma por inquérito" não está mais aqui:
    é o índice único da tabela de jobs, aplicado no jobs_utils.criar() que a rota chama antes."""
    thread = threading.Thread(
        target=_executar_comparacao,
        args=(id_job, id_inquerito, imagem_upload_bytes, limite, id_extracao),
        daemon=True,
    )
    thread.start()


def obter_origem_arquivo(id_arquivo: int) -> dict | None:
    query = """
        SELECT a.id_arquivo, a.no_arquivo, a.tx_caminho, a.tp_mime,
               a.qt_bytes, a.dt_modificacao, a.no_categoria,
               a.classificacao_ia, a.confianca_ia,
               e.id_extracao, e.no_arquivo AS no_arquivo_ufdr,
               e.no_dispositivo, e.id_inquerito,
               i.nu_inquerito
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
        WHERE a.id_arquivo = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_arquivo,))
            row = cur.fetchone()
            if row is None:
                return None
            colunas = [d.name for d in cur.description]
            return dict(zip(colunas, row))
