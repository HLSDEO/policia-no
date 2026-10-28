"""OCR das imagens do aparelho — texto reconhecido pelo Tesseract (local, CPU) ou por API de visão
(Google Vision / Qwen com visão), conforme o modo escolhido em Configurações.

Print de comprovante PIX, boleto, foto de documento e conversa printada são conteúdo que hoje passa
invisível pelo sistema: é pixel, não texto. O OCR grava esse texto em ARQ_TB01_ARQUIVOS.TX_OCR, de
onde ele alimenta de uma vez a busca da tela de Arquivos, o contexto da IA e a detecção de padrões
(fonte `arquivo_ocr` em padroes_utils).

Mesma mecânica da classificação por CLIP (classificacao_imagem_utils): roda em background, uma
varredura por vez no processo, com progresso, cancelamento e stale detection.
"""

import base64
import json
import logging
import re
import urllib.error
import urllib.request
import zipfile
from io import BytesIO

import pytesseract
from PIL import Image as PILImage

from funcoes import jobs_pesados, jobs_utils, ufdr_zip
from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.ia_provedores import ErroProvedorIA
from funcoes.investigacao_utils import fonte_ufdr

try:
    from PIL.Image import Exif
except ImportError:
    Exif = None

logger = logging.getLogger(__name__)

# Imagem muito pequena é ícone/thumbnail de app (não tem texto útil e infla a fila); acima de ~4000px
# o Tesseract fica lento sem ganho, então a imagem é reduzida antes de entrar.
_BYTES_MIN = 102400         # 100 KB
_LADO_MAX = 2500            # px — lado maior da imagem enviada ao Tesseract
_TIMEOUT_IMAGEM = 60        # s por imagem: um PNG patológico não pode travar a varredura inteira
_MIN_CARACTERES = 4         # menos que isso é ruído de borda, não texto
_LIMITE_SELECAO = 200       # teto de imagens numa seleção parcial (ferramenta da IA)


def _config_ocr() -> dict:
    """Configuração do OCR conforme o modo escolhido em Configurações (.env):
    - local: Tesseract — OCR_IDIOMA aceita a sintaxe do próprio Tesseract ('por+eng' = tenta os
      dois); OCR_PSM 3 = segmentação automática, o certo para print de tela e foto de documento;
    - api: provedor de visão (google-vision ou qwen-vision) — URL, chave e modelo próprios.
    Devolve um dict com as chaves do modo ativo."""
    env = parse_env(read_env_text())
    modo = (env.get("OCR_MODE") or "local").strip().lower()
    if modo == "api":
        return {
            "mode": "api",
            "provider": (env.get("OCR_PROVIDER") or "").strip().lower(),
            "apiUrl": (env.get("OCR_API_URL") or "").strip(),
            "apiKey": (env.get("OCR_API_KEY") or "").strip(),
            "model": (env.get("OCR_MODEL") or "").strip(),
        }
    return {
        "mode": "local",
        "idioma": (env.get("OCR_IDIOMA") or "por+eng").strip(),
        "psm": (env.get("OCR_PSM") or "3").strip(),
    }


def ocr_disponivel() -> bool:
    """True se há como rodar o OCR: no modo local, o binário do Tesseract está instalado na
    imagem da API; no modo api, URL e modelo estão configurados no .env."""
    cfg = _config_ocr()
    if cfg["mode"] == "api":
        return bool(cfg.get("apiUrl") and cfg.get("model"))
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _corrigir_orientacao_imagem(img: PILImage.Image) -> PILImage.Image:
    """Detecta e corrige a orientação da imagem usando EXIF data. O Tesseract tem dificuldade
    com imagens rotacionadas, então essa correção melhora a qualidade do OCR."""
    try:
        exif_data = img._getexif() if hasattr(img, '_getexif') else None
        if exif_data is None:
            return img

        # Tag 274 é Orientation no EXIF
        orientation = exif_data.get(274, 1) if isinstance(exif_data, dict) else 1

        rotacoes = {
            1: lambda i: i,  # Normal
            2: lambda i: i.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT),
            3: lambda i: i.rotate(180, expand=False),
            4: lambda i: i.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM),
            5: lambda i: i.transpose(PILImage.Transpose.ROTATE_270).transpose(PILImage.Transpose.FLIP_LEFT_RIGHT),
            6: lambda i: i.rotate(270, expand=False),
            7: lambda i: i.transpose(PILImage.Transpose.ROTATE_90).transpose(PILImage.Transpose.FLIP_LEFT_RIGHT),
            8: lambda i: i.rotate(90, expand=False),
        }

        rotacao_func = rotacoes.get(orientation, lambda i: i)
        return rotacao_func(img)
    except Exception as exc:
        logger.debug("Não foi possível ler EXIF para orientação: %s", exc)
        return img


def _limpar_texto(texto: str) -> str | None:
    """Normaliza a saída do Tesseract: colapsa espaços/linhas em branco que ele produz de sobra e
    devolve None quando o que sobrou é ruído (sem letra nem dígito, ou curto demais)."""
    if not texto:
        return None
    linhas = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in texto.splitlines()]
    limpo = "\n".join(ln for ln in linhas if ln)
    limpo = re.sub(r"\n{3,}", "\n\n", limpo).strip()
    if len(limpo) < _MIN_CARACTERES:
        return None
    if not re.search(r"[0-9A-Za-zÀ-ÿ]", limpo):
        return None
    return limpo


def _ocr_imagem(dados: bytes, idioma: str, config: str) -> str | None:
    """Roda o OCR num binário de imagem. HEIC do iPhone funciona pelo pillow-heif registrado no
    import de investigacao_utils. Detecta e corrige orientação usando EXIF antes do OCR.
    Devolve None quando não há texto legível."""
    img = PILImage.open(BytesIO(dados))

    # Corrige a orientação da imagem se estiver rotacionada
    img = _corrigir_orientacao_imagem(img)

    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    maior = max(img.size)
    if maior > _LADO_MAX:
        escala = _LADO_MAX / maior
        img = img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))))
    texto = pytesseract.image_to_string(img, lang=idioma, config=config, timeout=_TIMEOUT_IMAGEM)
    return _limpar_texto(texto)


def _post_json(url: str, headers: dict, corpo: dict, timeout: int = _TIMEOUT_IMAGEM) -> dict:
    """POST JSON para a API de visão (formato do embed_utils, com timeout próprio do OCR)."""
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


def _imagem_para_envio(dados: bytes) -> tuple[bytes, str]:
    """Pré-processa a imagem para enviar à API de visão (mesma correção de orientação e redução
    do Tesseract) e devolve (bytes_jpeg, mime)."""
    img = PILImage.open(BytesIO(dados))
    img = _corrigir_orientacao_imagem(img)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    maior = max(img.size)
    if maior > _LADO_MAX:
        escala = _LADO_MAX / maior
        img = img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue(), "image/jpeg"


def _ocr_imagem_api(dados: bytes, cfg: dict) -> str | None:
    """Roda o OCR por API de visão (modo api), conforme o provedor configurado:
    - google-vision: Google Cloud Vision (images:annotate, TEXT_DETECTION);
    - qwen-vision: modelo com visão via formato OpenAI-compatível (ex.: DashScope);
    - outro: tenta o formato OpenAI-compatível (mesmo do qwen-vision).
    Devolve None quando não há texto legível."""
    provider = cfg.get("provider", "qwen-vision")
    api_url = cfg.get("apiUrl", "")
    api_key = cfg.get("apiKey", "")
    modelo = cfg.get("model", "")
    if not api_url or not modelo:
        raise ErroProvedorIA("OCR por API sem URL/modelo configurados. Configure em Configurações.")

    bytes_img, mime = _imagem_para_envio(dados)
    b64 = base64.b64encode(bytes_img).decode("ascii")

    if provider == "google-vision":
        corpo = {
            "requests": [{
                "image": {"content": b64},
                "features": [{"type": "TEXT_DETECTION"}],
            }]
        }
        url = f"{api_url.rstrip('/')}/images:annotate"
        if api_key:
            url += f"?key={api_key}"
        resp = _post_json(url, {}, corpo)
        texto = ((resp.get("responses") or [{}])[0].get("fullTextAnnotation") or {}).get("text") or ""
    else:
        # qwen-vision / outro — OpenAI-compatível com imagem (data URL base64)
        conteudo = [
            {"type": "text", "text": (
                "Extraia todo o texto visível nesta imagem. Devolva apenas o texto extraído, "
                "sem comentários, mantendo a ordem e as quebras de linha aproximadas."
            )},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        corpo = {"model": modelo, "messages": [{"role": "user", "content": conteudo}]}
        cabecalhos = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = _post_json(f"{api_url.rstrip('/')}/chat/completions", cabecalhos, corpo)
        texto = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    return _limpar_texto(texto)


def _executar_ocr(dados: bytes, cfg: dict) -> str | None:
    """Roda o OCR pelo modo configurado: Tesseract (local) ou API de visão."""
    if cfg.get("mode") == "api":
        return _ocr_imagem_api(dados, cfg)
    return _ocr_imagem(dados, cfg.get("idioma", "por+eng"), f"--psm {cfg.get('psm', '3')}")


def _salvar_resultados(resultados: list[tuple[int, str | None]]):
    """Grava o texto e carimba DT_OCR. Sem texto também carimba (com TX_OCR NULL) — é o que impede
    a imagem de voltar para a fila na próxima varredura."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            for id_arquivo, texto in resultados:
                cur.execute(
                    """UPDATE investigacao.arq_tb01_arquivos
                       SET tx_ocr = %s, dt_ocr = now()
                       WHERE id_arquivo = %s""",
                    (texto, id_arquivo),
                )
        conn.commit()


def obter_texto_ocr(id_arquivo: int) -> dict:
    """Texto do OCR de um arquivo, para o modal da tela de Arquivos.
    Devolve {} quando a imagem ainda não passou pelo OCR."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tx_ocr, dt_ocr FROM investigacao.arq_tb01_arquivos WHERE id_arquivo = %s",
                (id_arquivo,),
            )
            row = cur.fetchone()
    if row is None or row[1] is None:
        return {}
    return {"ocr": row[0], "ocr_em": row[1]}


def status_ocr_extracao(id_extracao: int) -> dict:
    """Retorna o status do OCR de uma extração: 'completo' | 'parcial' | 'pendente'.

    - completo: todas as imagens da extração já têm OCR (dt_ocr != NULL)
    - pendente: nenhuma imagem tem OCR
    - parcial: algumas têm, outras não
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                     COUNT(*) as total,
                     SUM(CASE WHEN dt_ocr IS NOT NULL THEN 1 ELSE 0 END) as com_ocr
                   FROM investigacao.arq_tb01_arquivos
                   WHERE id_extracao = %s
                     AND no_categoria = 'Imagem'
                     AND co_caminho_zip IS NOT NULL""",
                (id_extracao,),
            )
            total, com_ocr = cur.fetchone() or (0, 0)

    if total == 0:
        return {"status": "sem_imagens", "total": 0, "com_ocr": 0}
    elif com_ocr == total:
        return {"status": "completo", "total": total, "com_ocr": com_ocr}
    elif com_ocr == 0:
        return {"status": "pendente", "total": total, "com_ocr": com_ocr}
    else:
        return {"status": "parcial", "total": total, "com_ocr": com_ocr}


def resetar_ocr_imagens(id_inquerito: int, id_extracao: int | None = None, mime_types: list[str] | None = None) -> dict:
    """Reseta o OCR de imagens específicas (marca como não processadas) para re-rodar.
    Útil após melhorias no OCR ou quando imagens foram processadas com problemas.

    - id_extracao=None: reseta TODAS as imagens do inquérito
    - mime_types: filtrar por tipo MIME (ex: ['image/jpeg', 'image/png'])
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            query = """UPDATE investigacao.arq_tb01_arquivos
                       SET tx_ocr = NULL, dt_ocr = NULL
                       WHERE id_arquivo IN (
                           SELECT a.id_arquivo
                           FROM investigacao.arq_tb01_arquivos a
                           JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                           WHERE e.id_inquerito = %s
                             AND a.no_categoria = 'Imagem'
                             AND a.co_caminho_zip IS NOT NULL"""
            params = [id_inquerito]

            if id_extracao is not None:
                query += " AND a.id_extracao = %s"
                params.append(id_extracao)

            if mime_types:
                placeholders = ",".join(["%s"] * len(mime_types))
                query += f" AND a.tp_mime IN ({placeholders})"
                params.extend(mime_types)

            query += ")"
            cur.execute(query, params)
            # dentro do bloco do cursor de propósito: lido depois do `with`, o rowcount vinha de um
            # cursor fechado numa conexão já devolvida ao pool — funcionava só porque o psycopg3
            # preserva o valor após o close(), o que nenhuma versão promete.
            linhas_afetadas = cur.rowcount
        conn.commit()

    return {
        "linhas_resetadas": linhas_afetadas,
        "mensagem": f"{linhas_afetadas} imagens marcadas para re-OCR"
    }


def ocr_imagem_especifica(id_arquivo: int, id_inquerito: int) -> dict:
    """Faz OCR sob demanda em uma imagem específica (sem fila). Retorna o texto reconhecido e
    salva o resultado no banco."""
    cfg_ocr = _config_ocr()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id_extracao, a.co_caminho_zip, e.no_arquivo, a.tp_mime
                   FROM investigacao.arq_tb01_arquivos a
                   JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                   WHERE a.id_arquivo = %s AND e.id_inquerito = %s""",
                (id_arquivo, id_inquerito),
            )
            row = cur.fetchone()

    if row is None:
        raise ValueError(f"Arquivo {id_arquivo} não encontrado ou não pertence ao inquérito")

    id_extracao, caminho_zip, no_ufdr, tp_mime = row
    if not caminho_zip:
        raise ValueError("Arquivo não tem binário associado no UFDR")

    caminho_ufdr = fonte_ufdr(no_ufdr)
    if not caminho_ufdr:
        raise ValueError(f"UFDR {no_ufdr} não encontrado")

    try:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            dados = _ler_membro(zf, caminho_zip)
            if dados is None:
                raise ValueError(f"Imagem {caminho_zip} não encontrada no UFDR")
            texto = _executar_ocr(dados, cfg_ocr)
    except zipfile.BadZipFile as e:
        raise ValueError(f"Erro ao abrir UFDR: {e}")

    # Salva o resultado
    _salvar_resultados([(id_arquivo, texto)])

    return {
        "id_arquivo": id_arquivo,
        "ocr": texto,
        "mime_type": tp_mime,
    }


def _fila_ocr(id_inquerito: int, id_extracao: int | None, max_imagens: int | None) -> list[tuple]:
    """Imagens pendentes de OCR, agrupadas por .ufdr para abrir cada ZIP uma única vez.
    Devolve [(no_arquivo_ufdr, [(id_arquivo, caminho_zip), ...]), ...].

    `webp` fica de fora pelo mesmo motivo da classificação por CLIP: além de ser figurinha (sem texto
    de interesse), boa parte do que o UFDR grava com essa extensão em files/Archives/ nem é imagem —
    é um ZIP de pacote de figurinhas com nome de .webp."""
    query = """SELECT e.no_arquivo, a.id_arquivo, a.co_caminho_zip
               FROM investigacao.arq_tb01_arquivos a
               JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
               WHERE a.no_categoria = 'Imagem'
                 AND a.dt_ocr IS NULL
                 AND a.co_caminho_zip IS NOT NULL
                 AND a.tp_mime <> 'image/webp'
                 AND a.qt_bytes >= %s
                 AND e.id_inquerito = %s"""
    params: list = [_BYTES_MIN, id_inquerito]
    if id_extracao is not None:
        query += " AND a.id_extracao = %s"
        params.append(id_extracao)
    query += " ORDER BY e.no_arquivo, a.id_arquivo"
    if max_imagens is not None:
        query += " LIMIT %s"
        params.append(max_imagens)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            linhas = cur.fetchall()

    grupos: dict[str, list[tuple[int, str]]] = {}
    for no_ufdr, id_arquivo, caminho_zip in linhas:
        grupos.setdefault(no_ufdr, []).append((id_arquivo, caminho_zip))
    return list(grupos.items())


def _ler_membro(zf: zipfile.ZipFile, caminho_zip: str) -> bytes | None:
    try:
        return zf.read(caminho_zip)
    except KeyError:
        normalizado = caminho_zip.replace("\\", "/")   # mesma normalização da classificação por CLIP
        if normalizado == caminho_zip:
            return None
        try:
            return zf.read(normalizado)
        except Exception:
            return None
    except Exception:
        return None


def _processa_grupos(
    id_job: int, grupos: list[tuple], cfg_ocr: dict, lote: int = 20,
) -> tuple[int, int, int, bool]:
    """Laço comum das varreduras de OCR: reconhece os grupos (formato de `_fila_ocr`) e grava o
    texto por lote. Devolve (processados, com_texto, erros, cancelar).

    A vaga de job pesado e o fecho do job (concluir/cancelar/falhar) ficam com a chamadora — aqui
    é só o laço, com cancelamento cooperativo (checado a cada imagem/lote) e uma ida ao banco por
    lote para progresso + batimento + fl_cancelar (`jobs_utils.progresso`)."""
    processados = 0
    com_texto = 0
    erros = 0
    cancelar = False
    for no_ufdr, itens in grupos:
        if cancelar:
            break
        caminho_ufdr = fonte_ufdr(no_ufdr)
        if not caminho_ufdr:
            logger.warning("OCR: arquivo UFDR não encontrado (central nem nó): %s (%s imagens)",
                           no_ufdr, len(itens))
            processados += len(itens)
            erros += len(itens)
            cancelar = jobs_utils.progresso(id_job, processados=processados, erros=erros)
            continue

        for inicio in range(0, len(itens), lote):
            if cancelar:
                break
            fatia = itens[inicio:inicio + lote]
            resultados: list[tuple[int, str | None]] = []
            erros_lote = 0
            try:
                with ufdr_zip.abrir(caminho_ufdr) as zf:
                    for id_arquivo, caminho_zip in fatia:
                        dados = _ler_membro(zf, caminho_zip)
                        if dados is None:
                            logger.warning("OCR: imagem %s não encontrada no zip %s [%s]",
                                           id_arquivo, no_ufdr, caminho_zip)
                            erros_lote += 1
                            continue
                        try:
                            resultados.append((id_arquivo, _executar_ocr(dados, cfg_ocr)))
                        except Exception as exc:
                            # falha de decodificação é determinística (binário corrompido, ou
                            # arquivo que só TEM nome de imagem): carimba DT_OCR do mesmo jeito,
                            # senão essa imagem volta para a fila em toda varredura futura
                            logger.warning("OCR: erro na imagem %s: %s: %s",
                                           id_arquivo, type(exc).__name__, exc)
                            resultados.append((id_arquivo, None))
                            erros_lote += 1
            except Exception as exc:
                logger.warning("OCR: erro ao abrir %s: %s: %s", no_ufdr, type(exc).__name__, exc)
                erros_lote = len(fatia)

            if resultados:
                _salvar_resultados(resultados)

            processados += len(fatia)
            com_texto += sum(1 for _, t in resultados if t)
            erros += erros_lote
            # uma ida ao banco por lote grava o progresso E devolve o pedido de
            # cancelamento, no lugar do par _PROGRESS/_CANCELAR de antes
            cancelar = jobs_utils.progresso(
                id_job, processados=processados, erros=erros,
                metricas={"com_texto": com_texto})
    return processados, com_texto, erros, cancelar


def ocr_imagens_pendentes(
    id_job: int,
    id_inquerito: int,
    id_extracao: int | None = None,
    lote: int = 20,
    max_imagens: int | None = None,
):
    """Varre as imagens do inquérito ainda sem OCR e grava o texto reconhecido.

    Processa só o que tem binário no .ufdr e passa de _BYTES_MIN (abaixo disso é ícone de app).
    O ZIP de cada extração é aberto uma vez por lote — abrir por imagem custaria caro numa fila de
    dezenas de milhares.

    Roda em background, e todo o estado (progresso, contadores, pedido de cancelamento) vive na
    linha `id_job` de JOB_TB01_JOBS: quem lê é o polling da tela, por rotas/jobs.py. Antes disto o
    estado era um dicionário de módulo, ou seja, do servidor inteiro — ver funcoes/jobs_utils.py.

    O guard de "só um por inquérito" NÃO está mais aqui: é o índice único da tabela, garantido no
    jobs_utils.criar() que a rota chama antes de subir esta thread."""
    cfg_ocr = _config_ocr()
    try:
        grupos = _fila_ocr(id_inquerito, id_extracao, max_imagens)
        total = sum(len(itens) for _, itens in grupos)
        jobs_utils.progresso(id_job, total=total)
        if total == 0:
            jobs_utils.concluir(id_job, metricas={"com_texto": 0})
            return

        with jobs_pesados.vaga(id_job, "OCR"):
            _, com_texto, _, cancelar = _processa_grupos(id_job, grupos, cfg_ocr, lote)

        if cancelar:
            jobs_utils.marcar_cancelado(id_job)
        else:
            jobs_utils.concluir(id_job, metricas={"com_texto": com_texto})
    except jobs_pesados.SemVagaCancelado:
        jobs_utils.marcar_cancelado(id_job)
    except Exception as exc:
        # antes daqui uma exceção só deixava rodando=False, sem mensagem nenhuma para o usuário:
        # a tela voltava a "nada em andamento" e ninguém sabia que tinha falhado
        logger.exception("Falha no OCR do inquérito %s (job %s)", id_inquerito, id_job)
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])


def fila_ocr_selecionada(
    id_inquerito: int,
    ids_arquivos: list[int] | None = None,
    termo: str | None = None,
    inicio: str | None = None,
    fim: str | None = None,
    id_extracao: int | None = None,
    limite: int = 50,
) -> dict:
    """Resolve a seleção PARCIAL de imagens para o OCR disparado pela IA (ferramenta
    `processar_texto_em_imagens`) — nunca o corpus inteiro: ou um punhado de ids vindos de
    `buscar_arquivos_dispositivo`, ou um recorte por termo/período com teto rígido.

    Só valem imagens deste inquérito, com binário no UFDR e pendentes (`DT_OCR IS NULL`) — já
    processada não re-OCRiza a pedido da IA (para isso existe o resetar da tela Arquivos). Ids
    explícitos dispensam o piso de tamanho e o corte de webp (quem pediu sabe o que pede); busca
    por termo/período herda os mesmos filtros da fila em lote (`_fila_ocr`), para thumbnail de
    app não comer o limite.

    Devolve {'grupos': [(no_ufdr, [(id_arquivo, caminho_zip), ...]), ...], 'selecionadas': int,
    'ignoradas': {'ja_processadas': n, 'inativeis': n}}."""
    limite = max(1, min(int(limite or 50), _LIMITE_SELECAO))
    grupos: dict[str, list[tuple[int, str]]] = {}
    ignoradas = {"ja_processadas": 0, "inativeis": 0}
    truncado = 0
    # extrações (aparelhos) efetivamente tocadas pela seleção — quando o recorte cai num único
    # aparelho, a ferramenta amarra esse id_extracao ao job, e o link "Ver na tela Arquivos" do
    # cartão no chat abre direto naquele aparelho (a tela exige inquérito + extração)
    extracoes: set[int] = set()
    if ids_arquivos:
        # o teto vale também para ids explícitos: lista maior que _LIMITE_SELECAO é cortada, e a
        # chamadora devolve o aviso — processamento pesado não se dispara em doses indefinidas
        ids_arquivos = list(dict.fromkeys(int(i) for i in ids_arquivos))
        truncado = max(0, len(ids_arquivos) - _LIMITE_SELECAO)
        ids_arquivos = ids_arquivos[:_LIMITE_SELECAO]

    with get_connection() as conn:
        with conn.cursor() as cur:
            if ids_arquivos:
                cur.execute(
                    """SELECT e.no_arquivo, a.id_arquivo, a.co_caminho_zip,
                              a.no_categoria, a.dt_ocr, a.id_extracao
                       FROM investigacao.arq_tb01_arquivos a
                       JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                       WHERE e.id_inquerito = %s AND a.id_arquivo = ANY(%s::bigint[])""",
                    (id_inquerito, list(ids_arquivos)),
                )
                for no_ufdr, id_arquivo, caminho_zip, categoria, dt_ocr, id_ext in cur.fetchall():
                    if dt_ocr is not None:
                        ignoradas["ja_processadas"] += 1
                    elif categoria != "Imagem" or not caminho_zip:
                        ignoradas["inativeis"] += 1
                    else:
                        grupos.setdefault(no_ufdr, []).append((id_arquivo, caminho_zip))
                        extracoes.add(id_ext)
                # ids que não existem neste inquérito (ou não existem) também contam como inválidos
                encontrados = sum(len(v) for v in grupos.values()) + sum(ignoradas.values())
                ignoradas["inativeis"] += max(0, len(set(ids_arquivos)) - encontrados)
            else:
                query = """SELECT e.no_arquivo, a.id_arquivo, a.co_caminho_zip, a.id_extracao
                           FROM investigacao.arq_tb01_arquivos a
                           JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                           WHERE a.no_categoria = 'Imagem'
                             AND a.dt_ocr IS NULL
                             AND a.co_caminho_zip IS NOT NULL
                             AND a.tp_mime <> 'image/webp'
                             AND a.qt_bytes >= %s
                             AND e.id_inquerito = %s"""
                params: list = [_BYTES_MIN, id_inquerito]
                if id_extracao is not None:
                    query += " AND a.id_extracao = %s"
                    params.append(id_extracao)
                if termo:
                    query += " AND (a.no_arquivo ILIKE %s OR a.tx_caminho ILIKE %s)"
                    like = f"%{termo}%"
                    params.extend([like, like])
                if inicio:
                    query += " AND a.dt_modificacao >= %s::timestamp"
                    params.append(inicio)
                if fim:
                    # fim é dia-limite inclusivo: '2026-05-20' alcança as 23h59 do dia 20
                    query += " AND a.dt_modificacao < (%s::timestamp + interval '1 day')"
                    params.append(fim)
                query += " ORDER BY a.dt_modificacao DESC NULLS LAST LIMIT %s"
                params.append(limite)
                cur.execute(query, params)
                for no_ufdr, id_arquivo, caminho_zip, id_ext in cur.fetchall():
                    grupos.setdefault(no_ufdr, []).append((id_arquivo, caminho_zip))
                    extracoes.add(id_ext)

    return {
        "grupos": list(grupos.items()),
        "selecionadas": sum(len(v) for v in grupos.values()),
        "ignoradas": ignoradas,
        "limite": limite,
        "truncado": truncado,
        "extracoes": sorted(extracoes),
    }


def ocr_imagens_selecionadas(id_job: int, grupos: list[tuple], lote: int = 20):
    """OCR da seleção parcial resolvida por `fila_ocr_selecionada` (ferramenta da IA).

    Mesma mecânica de `ocr_imagens_pendentes` — vaga de job pesado, progresso/batimento por lote,
    cancelamento cooperativo — a diferença é o escopo: exatamente as imagens escolhidas, e o job
    termina ali, sem varrer o resto do inquérito."""
    cfg_ocr = _config_ocr()
    try:
        total = sum(len(itens) for _, itens in grupos)
        jobs_utils.progresso(id_job, total=total)
        if total == 0:
            jobs_utils.concluir(id_job, metricas={"com_texto": 0})
            return

        with jobs_pesados.vaga(id_job, "OCR de imagens selecionadas"):
            _, com_texto, _, cancelar = _processa_grupos(id_job, grupos, cfg_ocr, lote)

        if cancelar:
            jobs_utils.marcar_cancelado(id_job)
        else:
            jobs_utils.concluir(id_job, metricas={"com_texto": com_texto})
    except jobs_pesados.SemVagaCancelado:
        jobs_utils.marcar_cancelado(id_job)
    except Exception as exc:
        logger.exception("Falha no OCR selecionado (job %s)", id_job)
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])
