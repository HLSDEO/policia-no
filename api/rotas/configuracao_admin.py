"""Rotas de configuração administrativa: credenciais de API salvas no .env."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import cert_utils, config_ia_usuario, desempenho_utils, ia_prompt_niveis
from funcoes.db_utils import get_connection
from funcoes.ia_provedores import ESFORCOS_RACIOCINIO, PROVEDORES_CHAT
from funcoes.auth_utils import exigir_admin
from funcoes.env_utils import (
    mask_key,
    parse_env,
    read_env_text,
    read_modify_write_env,
    sanitize,
    upsert_env_values,
    write_env_text,
)
from funcoes.ollama_pull import iniciar_pull, status_pull

router = APIRouter(dependencies=[Depends(exigir_admin)])

# Provedores aceitos no campo "provider" do formulário de configuração: os de chat
# (ia_provedores.PROVEDORES_CHAT) mais os de visão, que só existem nas abas OCR e Comparação.
ALLOWED_PROVIDERS = PROVEDORES_CHAT | {"google-vision", "qwen-vision"}


# =====================================================================
# Modelos Pydantic para cada seção
# =====================================================================

class PromptConfigIn(BaseModel):
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    contextLength: int = Field(0, ge=0, le=2_000_000)
    # timeout da requisição ao provedor (segundos). 0 = usa o padrão do provedor (ia_provedores).
    timeout: int = Field(0, ge=0, le=3600)
    anonimizar: bool = False
    # PEM do certificado do gateway (provedor openai-cert). Vazio = manter o atual, como a chave;
    # o limite cobre com folga uma cadeia com duas ou três chaves de 4096 bits.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False
    # esforço de raciocínio do gateway (provedor openai-cert); vazio = padrão "low"
    reasoningEffort: str = Field("", max_length=10)


class PromptConfigOut(BaseModel):
    provider: str
    model: str
    apiUrl: str
    apiKeyMasked: str
    contextLength: int
    timeout: int
    anonimizar: bool
    # descrição do certificado salvo (nome, validade, impressão digital) — nunca o PEM inteiro
    caCertResumo: str
    reasoningEffort: str


class EmbeddingsConfigIn(BaseModel):
    provider: str = Field("", max_length=30)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    mode: str = Field("", max_length=10)  # "api" | "local"
    # PEM do certificado do gateway (provedor openai-cert). Vazio = manter o atual, como a chave;
    # o limite cobre com folga uma cadeia com duas ou três chaves de 4096 bits.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False


class EmbeddingsConfigOut(BaseModel):
    provider: str
    apiUrl: str
    apiKeyMasked: str
    model: str
    mode: str
    caCertResumo: str


class RagConfigIn(BaseModel):
    pesoSemantico: float = Field(0.5, ge=0, le=1)
    pesoTextual: float = Field(0.3, ge=0, le=1)
    pesoGrafo: float = Field(0.2, ge=0, le=1)
    limite: int = Field(15, ge=5, le=50)


class RagConfigOut(BaseModel):
    pesoSemantico: float
    pesoTextual: float
    pesoGrafo: float
    limite: int


class OCRConfigIn(BaseModel):
    mode: str = Field("", max_length=10)  # "local" | "api"
    provider: str = Field("", max_length=30)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    idioma: str = Field("", max_length=50)
    psm: str = Field("", max_length=10)


class OCRConfigOut(BaseModel):
    mode: str
    provider: str
    apiUrl: str
    apiKeyMasked: str
    model: str
    idioma: str
    psm: str


class ImageComparisonConfigIn(BaseModel):
    mode: str = Field("", max_length=10)  # "local" | "api"
    provider: str = Field("", max_length=30)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    model: str = Field("", max_length=200)


class ImageComparisonConfigOut(BaseModel):
    mode: str
    provider: str
    apiUrl: str
    apiKeyMasked: str
    model: str


class ReportAIConfigIn(BaseModel):
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    anonimizar: bool = False
    # PEM do certificado do gateway (provedor openai-cert). Vazio = manter o atual, como a chave;
    # o limite cobre com folga uma cadeia com duas ou três chaves de 4096 bits.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False
    reasoningEffort: str = Field("", max_length=10)


class ReportAIConfigOut(BaseModel):
    provider: str
    model: str
    apiUrl: str
    apiKeyMasked: str
    anonimizar: bool
    caCertResumo: str
    reasoningEffort: str


class DocumentAIConfigIn(BaseModel):
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    # PEM do certificado do gateway (provedor openai-cert). Vazio = manter o atual, como a chave;
    # o limite cobre com folga uma cadeia com duas ou três chaves de 4096 bits.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False
    reasoningEffort: str = Field("", max_length=10)


class DocumentAIConfigOut(BaseModel):
    provider: str
    model: str
    apiUrl: str
    apiKeyMasked: str
    caCertResumo: str
    reasoningEffort: str


class ResumoNoturnoConfigIn(BaseModel):
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    # PEM do certificado do gateway (provedor openai-cert). Vazio = manter o atual, como a chave.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False
    reasoningEffort: str = Field("", max_length=10)
    # agendamento do passe (thread dentro da API — funcoes/agendador.py)
    agendador: bool = True
    horaAgendador: str = Field("", max_length=5)    # "HH:MM"
    fusoAgendador: str = Field("", max_length=60)   # nome IANA, ex.: America/Sao_Paulo


class ResumoNoturnoConfigOut(BaseModel):
    provider: str
    model: str
    apiUrl: str
    apiKeyMasked: str
    caCertResumo: str
    reasoningEffort: str
    agendador: bool
    horaAgendador: str
    fusoAgendador: str
    # só leitura: quando roda de novo e como foi a última execução
    agendadorProxima: str
    agendadorUltima: str


# =====================================================================
# Helpers
# =====================================================================

def _validate_provider(provider: str, field_name: str = "Provedor") -> str:
    provider = sanitize(provider).lower()
    if provider and provider not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"{field_name} inválido.")
    return provider


def _validate_url(api_url: str, field_name: str = "URL da API") -> str:
    api_url = sanitize(api_url)
    if api_url and not re.match(r"^https?://", api_url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail=f"{field_name} deve começar com http:// ou https://.")
    return api_url


def _validate_mode(mode: str, valid: set[str], field_name: str = "Modo") -> str:
    mode = sanitize(mode).lower()
    if mode and mode not in valid:
        raise HTTPException(status_code=400, detail=f"{field_name} inválido.")
    return mode


def _read_env() -> dict:
    return parse_env(read_env_text())


def _write_env(updates: dict) -> None:
    # Atômico (ler+modificar+gravar sob o mesmo lock exclusivo): dois admins salvando juntos
    # não perdem um write — o padrão antigo lia e gravava em dois passos e o segundo
    # sobrescrevia o primeiro (ver C9).
    try:
        read_modify_write_env(updates)
    except OSError:
        raise HTTPException(status_code=500, detail="Falha ao gravar o arquivo .env.")


def _mask(key: str) -> str:
    return mask_key(key)


def _ler_flag(env: dict, chave: str) -> bool:
    return (env.get(chave) or "").strip().lower() in ("1", "true", "sim")


def _validate_esforco(esforco: str) -> str:
    """Esforço de raciocínio aceito: vazio (padrão) ou um dos valores de ESFORCOS_RACIOCINIO.

    Validar aqui e não deixar passar qualquer texto é o que evita o pior caso: o gateway trata
    valor desconhecido como "xhigh", que é exatamente o esforço que devolve resposta vazia."""
    esforco = sanitize(esforco).lower()
    if esforco and esforco not in ESFORCOS_RACIOCINIO:
        raise HTTPException(status_code=400, detail="Esforço de raciocínio inválido.")
    return esforco


def _valor_certificado(payload, current: dict, chave_env: str) -> str:
    """Valor a gravar na variável de certificado da seção (AI_CA_CERT, REPORT_AI_CA_CERT,
    DOC_AI_CA_CERT, EMBED_CA_CERT): o certificado colado no formulário (em base64, porque o .env é
    uma linha por chave), vazio se o usuário pediu para remover, ou o atual se o campo veio em
    branco — mesma regra da chave de API."""
    if payload.removerCaCert:
        return ""
    if not (payload.caCert or "").strip():
        return current.get(chave_env, "")
    try:
        return cert_utils.pem_para_env(cert_utils.normalizar_pem(payload.caCert))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Certificado inválido: {e}.")


def _gravar_flag(ligado: bool) -> str:
    """Valor de um checkbox no .env.

    Diferente dos campos de texto desta tela, um booleano NÃO pode cair na regra "vazio mantém o
    valor atual": desmarcar o checkbox é justamente mandar gravar o vazio, e com aquela regra a
    opção nunca desligaria."""
    return "1" if ligado else ""


# =====================================================================
# 1. Prompt IA — /api/config/ai/prompt
# =====================================================================

@router.get("/api/config/ai/prompt", response_model=PromptConfigOut)
def get_prompt_config():
    env = _read_env()
    try:
        ctx_len = int(env.get("AI_CONTEXT_LENGTH") or 0)
    except ValueError:
        ctx_len = 0
    try:
        timeout = int(env.get("AI_TIMEOUT") or 0)
    except ValueError:
        timeout = 0
    return PromptConfigOut(
        provider=env.get("AI_PROVIDER", ""),
        model=env.get("AI_MODEL", ""),
        apiUrl=env.get("AI_API_URL", ""),
        apiKeyMasked=_mask(env.get("AI_API_KEY", "")),
        contextLength=ctx_len,
        timeout=timeout,
        anonimizar=_ler_flag(env, "AI_ANONIMIZAR"),
        caCertResumo=cert_utils.resumo(env.get("AI_CA_CERT", "")),
        reasoningEffort=env.get("AI_REASONING_EFFORT", ""),
    )


@router.post("/api/config/ai/prompt")
def save_prompt_config(payload: PromptConfigIn):
    provider = _validate_provider(payload.provider)
    model = sanitize(payload.model)
    api_url = _validate_url(payload.apiUrl)
    api_key = sanitize(payload.apiKey)
    ctx_len = payload.contextLength
    esforco = _validate_esforco(payload.reasoningEffort)

    current = _read_env()
    updates = {
        "AI_CA_CERT": _valor_certificado(payload, current, "AI_CA_CERT"),
        "AI_PROVIDER": provider or current.get("AI_PROVIDER", ""),
        "AI_MODEL": model or current.get("AI_MODEL", ""),
        "AI_API_URL": api_url or current.get("AI_API_URL", ""),
        "AI_API_KEY": api_key or current.get("AI_API_KEY", ""),
        "AI_CONTEXT_LENGTH": str(ctx_len) if ctx_len else "",
        "AI_TIMEOUT": str(payload.timeout) if payload.timeout else "",
        "AI_REASONING_EFFORT": esforco or current.get("AI_REASONING_EFFORT", ""),
        "AI_ANONIMIZAR": _gravar_flag(payload.anonimizar),
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 2. Busca Semântica — /api/config/ai/embeddings
# =====================================================================

@router.get("/api/config/ai/embeddings", response_model=EmbeddingsConfigOut)
def get_embeddings_config():
    env = _read_env()
    return EmbeddingsConfigOut(
        provider=env.get("EMBED_PROVIDER", ""),
        apiUrl=env.get("EMBED_API_URL", ""),
        apiKeyMasked=_mask(env.get("EMBED_API_KEY", "")),
        model=env.get("EMBED_MODEL", ""),
        mode=env.get("EMBED_MODE", "") or "api",
        caCertResumo=cert_utils.resumo(env.get("EMBED_CA_CERT", "")),
    )


@router.post("/api/config/ai/embeddings")
def save_embeddings_config(payload: EmbeddingsConfigIn):
    provider = _validate_provider(payload.provider, "Provedor de embeddings")
    api_url = _validate_url(payload.apiUrl)
    api_key = sanitize(payload.apiKey)
    model = sanitize(payload.model)
    mode = _validate_mode(payload.mode, {"api", "local"}, "Modo de embeddings")

    # No modo local, provedor/URL/chave são ignorados (usa Ollama do servidor)
    current = _read_env()
    updates = {
        "EMBED_CA_CERT": _valor_certificado(payload, current, "EMBED_CA_CERT"),
        "EMBED_PROVIDER": provider or current.get("EMBED_PROVIDER", ""),
        "EMBED_API_URL": api_url or current.get("EMBED_API_URL", ""),
        "EMBED_API_KEY": api_key or current.get("EMBED_API_KEY", ""),
        "EMBED_MODEL": model or current.get("EMBED_MODEL", ""),
        "EMBED_MODE": mode or current.get("EMBED_MODE", "") or "api",
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 3. RAG híbrido — /api/config/ai/rag
# =====================================================================

@router.get("/api/config/ai/rag", response_model=RagConfigOut)
def get_rag_config():
    env = _read_env()

    def _peso(chave: str, padrao: float) -> float:
        try:
            return max(0.0, min(1.0, float(env.get(chave) or padrao)))
        except ValueError:
            return padrao

    try:
        limite = int(env.get("RAG_LIMITE") or 15)
    except ValueError:
        limite = 15
    return RagConfigOut(
        pesoSemantico=_peso("RAG_PESO_SEMANTICO", 0.5),
        pesoTextual=_peso("RAG_PESO_TEXTUAL", 0.3),
        pesoGrafo=_peso("RAG_PESO_GRAFO", 0.2),
        limite=max(5, min(50, limite)),
    )


@router.post("/api/config/ai/rag")
def save_rag_config(payload: RagConfigIn):
    _write_env({
        "RAG_PESO_SEMANTICO": f"{payload.pesoSemantico:g}",
        "RAG_PESO_TEXTUAL": f"{payload.pesoTextual:g}",
        "RAG_PESO_GRAFO": f"{payload.pesoGrafo:g}",
        "RAG_LIMITE": str(payload.limite),
    })
    return {"ok": True}


# =====================================================================
# 4. OCR — /api/config/ocr
# =====================================================================

@router.get("/api/config/ocr", response_model=OCRConfigOut)
def get_ocr_config():
    env = _read_env()
    return OCRConfigOut(
        mode=env.get("OCR_MODE", "") or "local",
        provider=env.get("OCR_PROVIDER", ""),
        apiUrl=env.get("OCR_API_URL", ""),
        apiKeyMasked=_mask(env.get("OCR_API_KEY", "")),
        model=env.get("OCR_MODEL", ""),
        idioma=env.get("OCR_IDIOMA", "") or "por+eng",
        psm=env.get("OCR_PSM", "") or "3",
    )


@router.post("/api/config/ocr")
def save_ocr_config(payload: OCRConfigIn):
    mode = _validate_mode(payload.mode, {"local", "api"}, "Modo OCR")
    provider = _validate_provider(payload.provider, "Provedor OCR") if mode == "api" else ""
    api_url = _validate_url(payload.apiUrl) if mode == "api" else ""
    api_key = sanitize(payload.apiKey) if mode == "api" else ""
    model = sanitize(payload.model) if mode == "api" else ""
    idioma = sanitize(payload.idioma) or "por+eng"
    psm = sanitize(payload.psm) or "3"

    current = _read_env()
    updates = {
        "OCR_MODE": mode or current.get("OCR_MODE", "") or "local",
        "OCR_PROVIDER": provider or current.get("OCR_PROVIDER", ""),
        "OCR_API_URL": api_url or current.get("OCR_API_URL", ""),
        "OCR_API_KEY": api_key or current.get("OCR_API_KEY", ""),
        "OCR_MODEL": model or current.get("OCR_MODEL", ""),
        "OCR_IDIOMA": idioma,
        "OCR_PSM": psm,
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 5. Comparação de Imagens — /api/config/image-comparison
# =====================================================================

@router.get("/api/config/image-comparison", response_model=ImageComparisonConfigOut)
def get_image_comparison_config():
    env = _read_env()
    return ImageComparisonConfigOut(
        mode=env.get("IMAGE_CMP_MODE", "") or "local",
        provider=env.get("IMAGE_CMP_PROVIDER", ""),
        apiUrl=env.get("IMAGE_CMP_API_URL", ""),
        apiKeyMasked=_mask(env.get("IMAGE_CMP_API_KEY", "")),
        model=env.get("IMAGE_CMP_MODEL", ""),
    )


@router.post("/api/config/image-comparison")
def save_image_comparison_config(payload: ImageComparisonConfigIn):
    mode = _validate_mode(payload.mode, {"local", "api"}, "Modo comparação")
    provider = _validate_provider(payload.provider, "Provedor comparação") if mode == "api" else ""
    api_url = _validate_url(payload.apiUrl) if mode == "api" else ""
    api_key = sanitize(payload.apiKey) if mode == "api" else ""
    model = sanitize(payload.model) if mode == "api" else ""

    current = _read_env()
    updates = {
        "IMAGE_CMP_MODE": mode or current.get("IMAGE_CMP_MODE", "") or "local",
        "IMAGE_CMP_PROVIDER": provider or current.get("IMAGE_CMP_PROVIDER", ""),
        "IMAGE_CMP_API_URL": api_url or current.get("IMAGE_CMP_API_URL", ""),
        "IMAGE_CMP_API_KEY": api_key or current.get("IMAGE_CMP_API_KEY", ""),
        "IMAGE_CMP_MODEL": model or current.get("IMAGE_CMP_MODEL", ""),
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 6. Comentário IA no Relatório — /api/config/ai/report
# =====================================================================

@router.get("/api/config/ai/report", response_model=ReportAIConfigOut)
def get_report_ai_config():
    env = _read_env()
    return ReportAIConfigOut(
        provider=env.get("REPORT_AI_PROVIDER", ""),
        model=env.get("REPORT_AI_MODEL", ""),
        apiUrl=env.get("REPORT_AI_API_URL", ""),
        apiKeyMasked=_mask(env.get("REPORT_AI_API_KEY", "")),
        anonimizar=_ler_flag(env, "REPORT_AI_ANONIMIZAR"),
        caCertResumo=cert_utils.resumo(env.get("REPORT_AI_CA_CERT", "")),
        reasoningEffort=env.get("REPORT_AI_REASONING_EFFORT", ""),
    )


@router.post("/api/config/ai/report")
def save_report_ai_config(payload: ReportAIConfigIn):
    provider = _validate_provider(payload.provider)
    model = sanitize(payload.model)
    api_url = _validate_url(payload.apiUrl)
    api_key = sanitize(payload.apiKey)

    current = _read_env()
    updates = {
        "REPORT_AI_CA_CERT": _valor_certificado(payload, current, "REPORT_AI_CA_CERT"),
        "REPORT_AI_REASONING_EFFORT": (
            _validate_esforco(payload.reasoningEffort) or current.get("REPORT_AI_REASONING_EFFORT", "")
        ),
        "REPORT_AI_PROVIDER": provider or current.get("REPORT_AI_PROVIDER", ""),
        "REPORT_AI_MODEL": model or current.get("REPORT_AI_MODEL", ""),
        "REPORT_AI_API_URL": api_url or current.get("REPORT_AI_API_URL", ""),
        "REPORT_AI_API_KEY": api_key or current.get("REPORT_AI_API_KEY", ""),
        "REPORT_AI_ANONIMIZAR": _gravar_flag(payload.anonimizar),
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 7. Análise de documentos — /api/config/ai/document
# =====================================================================

@router.get("/api/config/ai/document", response_model=DocumentAIConfigOut)
def get_document_ai_config():
    env = _read_env()
    return DocumentAIConfigOut(
        provider=env.get("DOC_AI_PROVIDER", ""),
        model=env.get("DOC_AI_MODEL", ""),
        apiUrl=env.get("DOC_AI_API_URL", ""),
        apiKeyMasked=_mask(env.get("DOC_AI_API_KEY", "")),
        caCertResumo=cert_utils.resumo(env.get("DOC_AI_CA_CERT", "")),
        reasoningEffort=env.get("DOC_AI_REASONING_EFFORT", ""),
    )


@router.post("/api/config/ai/document")
def save_document_ai_config(payload: DocumentAIConfigIn):
    provider = _validate_provider(payload.provider)
    model = sanitize(payload.model)
    api_url = _validate_url(payload.apiUrl)
    api_key = sanitize(payload.apiKey)

    current = _read_env()
    updates = {
        "DOC_AI_CA_CERT": _valor_certificado(payload, current, "DOC_AI_CA_CERT"),
        "DOC_AI_REASONING_EFFORT": (
            _validate_esforco(payload.reasoningEffort) or current.get("DOC_AI_REASONING_EFFORT", "")
        ),
        "DOC_AI_PROVIDER": provider or current.get("DOC_AI_PROVIDER", ""),
        "DOC_AI_MODEL": model or current.get("DOC_AI_MODEL", ""),
        "DOC_AI_API_URL": api_url or current.get("DOC_AI_API_URL", ""),
        "DOC_AI_API_KEY": api_key or current.get("DOC_AI_API_KEY", ""),
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# 8. Resumo noturno — /api/config/ai/resumo-noturno
# =====================================================================

def _resumo_ultima_execucao(ultima: dict | None) -> str:
    """Uma linha legível sobre a última execução do passe, para a tela.

    Sem execução nenhuma o texto diz isso — é o caso de quem acabou de atualizar (a estreia marca
    o dia sem rodar; ver funcoes/agendador.py)."""
    if not ultima:
        return "Nenhuma execução registrada ainda."
    quando = ultima["dt_referencia"].strftime("%d/%m/%Y")
    origem = {"agendador": "automática", "cli": "manual/host", "estreia": "primeira subida"}.get(
        ultima.get("no_origem") or "", ultima.get("no_origem") or "")
    if ultima["cd_status"] == "concluido" and (ultima.get("no_origem") == "estreia"):
        return f"Ainda não rodou — agendado a partir de {quando} ({origem})."
    if ultima["cd_status"] == "concluido":
        return (f"{quando} ({origem}): {ultima.get('qt_processados') or 0} inquérito(s) resumido(s), "
                f"{ultima.get('qt_erros') or 0} com erro.")
    if ultima["cd_status"] == "processando":
        return f"{quando} ({origem}): em andamento agora."
    return f"{quando} ({origem}): falhou — {(ultima.get('tx_erro') or 'sem detalhe')[:200]}"


def _validate_hora(hora: str) -> str:
    """Horário do agendador: "HH:MM" (o <input type=time> manda exatamente isso). Vazio mantém."""
    hora = sanitize(hora)
    if hora and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", hora):
        raise HTTPException(status_code=400, detail="Horário inválido — use HH:MM (ex.: 02:00).")
    return hora


def _validate_fuso(fuso: str) -> str:
    """Fuso do agendador: nome IANA existente. Validar aqui evita o pior caso — nome errado gravado
    e o passe rodando calado no horário de UTC, três horas fora do que a tela mostra."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    fuso = sanitize(fuso)
    if not fuso:
        return ""
    try:
        ZoneInfo(fuso)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400,
                            detail=f"Fuso horário desconhecido: {fuso} (ex.: America/Sao_Paulo).")
    return fuso


@router.get("/api/config/ai/resumo-noturno", response_model=ResumoNoturnoConfigOut)
def get_resumo_noturno_config():
    from funcoes import agendador

    env = _read_env()
    estado = agendador.estado()
    return ResumoNoturnoConfigOut(
        provider=env.get("RESUMO_NOTURNO_PROVIDER", ""),
        model=env.get("RESUMO_NOTURNO_MODEL", ""),
        apiUrl=env.get("RESUMO_NOTURNO_API_URL", ""),
        apiKeyMasked=_mask(env.get("RESUMO_NOTURNO_API_KEY", "")),
        caCertResumo=cert_utils.resumo(env.get("RESUMO_NOTURNO_CA_CERT", "")),
        reasoningEffort=env.get("RESUMO_NOTURNO_REASONING_EFFORT", ""),
        agendador=estado["ligado"],
        horaAgendador=estado["horario"],
        fusoAgendador=estado["fuso"],
        agendadorProxima=estado["proxima"],
        agendadorUltima=_resumo_ultima_execucao(estado["ultima"]),
    )


@router.post("/api/config/ai/resumo-noturno")
def save_resumo_noturno_config(payload: ResumoNoturnoConfigIn):
    provider = _validate_provider(payload.provider)
    model = sanitize(payload.model)
    api_url = _validate_url(payload.apiUrl)
    api_key = sanitize(payload.apiKey)
    hora = _validate_hora(payload.horaAgendador)
    fuso = _validate_fuso(payload.fusoAgendador)

    current = _read_env()
    updates = {
        "RESUMO_NOTURNO_CA_CERT": _valor_certificado(payload, current, "RESUMO_NOTURNO_CA_CERT"),
        "RESUMO_NOTURNO_REASONING_EFFORT": (
            _validate_esforco(payload.reasoningEffort) or current.get("RESUMO_NOTURNO_REASONING_EFFORT", "")
        ),
        "RESUMO_NOTURNO_PROVIDER": provider or current.get("RESUMO_NOTURNO_PROVIDER", ""),
        "RESUMO_NOTURNO_MODEL": model or current.get("RESUMO_NOTURNO_MODEL", ""),
        "RESUMO_NOTURNO_API_URL": api_url or current.get("RESUMO_NOTURNO_API_URL", ""),
        "RESUMO_NOTURNO_API_KEY": api_key or current.get("RESUMO_NOTURNO_API_KEY", ""),
        "RESUMO_NOTURNO_HORA": hora or current.get("RESUMO_NOTURNO_HORA", ""),
        "RESUMO_NOTURNO_TZ": fuso or current.get("RESUMO_NOTURNO_TZ", ""),
        # "1"/"0" explícitos, e não a regra "vazio mantém o atual" dos campos de texto: desmarcar
        # o checkbox É o pedido de desligar. Vazio/ausente (nunca configurado) = ligado, que é o
        # que faz o agendador funcionar sozinho numa instalação nova.
        "RESUMO_NOTURNO_AGENDADOR": "1" if payload.agendador else "0",
    }
    _write_env(updates)
    return {"ok": True}


# =====================================================================
# Ollama Pull (mantido igual)
# =====================================================================

class OllamaPullIn(BaseModel):
    model: str = Field(..., max_length=200)
    apiUrl: str = Field("", max_length=500)


@router.post("/api/config/ollama/pull")
def iniciar_download_modelo(payload: OllamaPullIn, usuario: dict = Depends(exigir_admin)):
    modelo = sanitize(payload.model)
    api_url = sanitize(payload.apiUrl)
    if not modelo:
        raise HTTPException(status_code=400, detail="Informe o modelo a baixar.")
    if api_url and not re.match(r"^https?://", api_url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="URL da API deve começar com http:// ou https://.")
    if not iniciar_pull(modelo, api_url, usuario["id_usuario"]):
        raise HTTPException(status_code=409, detail="Este modelo já está sendo baixado.")
    return {"ok": True}


@router.get("/api/config/ollama/pull")
def status_download_modelo(model: str):
    st = status_pull(sanitize(model))
    return st or {"status": "nenhum", "progresso": 0, "detalhe": "", "erro": ""}

# =====================================================================
# 9. Desempenho — /api/config/desempenho
# =====================================================================
#
# Diferente das outras seções desta tela, aqui quase nada vale na hora: pool, threads e caches
# são lidos no startup da API, e os PG_* pelo docker compose ao subir o Postgres. O campo
# "quando" de cada parâmetro (funcoes/desempenho_utils.py) é o que a tela usa para dizer, por
# campo, o que ainda falta reiniciar — gravar e fingir que já valeu seria pior que não ter a tela.

_PERFIS_VALIDOS = {"auto"} | set(desempenho_utils.PERFIS)

# Tamanho no formato que o Postgres aceita. Sem unidade ele interpreta como blocos de 8 kB, o que
# transformaria "512" (pensado como MB) em 4 MB silenciosamente — por isso a unidade é obrigatória.
_RE_TAMANHO = re.compile(r"^\d{1,7}(kB|MB|GB)$")
_RE_DECIMAL = re.compile(r"^\d{1,4}(\.\d{1,3})?$")


def _validar_valor(chave: str, bruto: str) -> str:
    """Valida um valor do formulário conforme o tipo declarado do parâmetro.

    Vazio é válido e significa "voltar ao automático" — é assim que se desfaz um ajuste manual.

    A validação não é só ergonomia: um `shared_buffers` malformado impede o Postgres de subir, e
    o erro só apareceria no `docker compose up -d` seguinte, longe daqui."""
    valor = sanitize(bruto)
    if not valor:
        return ""
    tipo = desempenho_utils.TIPOS[chave]
    if chave == "UPLOAD_DOCUMENTO_MB":
        # Teto do nginx é 520 MB (ver nginx/default.conf): acima disso quem recusaria seria o
        # proxy com um 413 opaco, e não a API com mensagem em JSON. 1 MB é o mínimo útil.
        if not valor.isdigit() or not 1 <= int(valor) <= 500:
            raise HTTPException(status_code=400, detail=f"{chave}: informe um número inteiro entre 1 e 500 (MB).")
    elif tipo == "inteiro":
        if not valor.isdigit() or not 1 <= int(valor) <= 100_000:
            raise HTTPException(status_code=400, detail=f"{chave}: informe um número inteiro entre 1 e 100000.")
    elif tipo == "tamanho":
        if not _RE_TAMANHO.match(valor):
            raise HTTPException(
                status_code=400,
                detail=f"{chave}: informe um tamanho com unidade (ex.: 512MB, 4GB).",
            )
    elif tipo == "decimal":
        if not _RE_DECIMAL.match(valor):
            raise HTTPException(status_code=400, detail=f"{chave}: informe um número (ex.: 1.1).")
    return valor


class DesempenhoIn(BaseModel):
    perfil: str = Field("auto", max_length=20)
    # Só as chaves conhecidas são aceitas (ver validação abaixo): sem isso, um POST poderia
    # gravar qualquer par no .env — inclusive POSTGRES_PASSWORD ou AI_API_KEY.
    valores: dict[str, str] = Field(default_factory=dict)


@router.get("/api/config/desempenho")
def get_desempenho_config(perfil: str | None = None):
    """Hardware detectado, valores recomendados, o que está gravado no .env e o que está em vigor.

    `?perfil=` calcula a resposta como se aquele perfil estivesse escolhido, SEM gravar nada — é
    a prévia da tela: dá para ver com que valores o sistema subiria num servidor menor antes de
    decidir salvar."""
    if perfil is None:
        return desempenho_utils.resumo()
    perfil = sanitize(perfil).lower()
    if perfil not in _PERFIS_VALIDOS:
        raise HTTPException(status_code=400, detail="Perfil inválido.")
    return desempenho_utils.resumo(perfil_simulado=perfil)


@router.post("/api/config/desempenho")
def save_desempenho_config(payload: DesempenhoIn):
    perfil = sanitize(payload.perfil).lower() or "auto"
    if perfil not in _PERFIS_VALIDOS:
        raise HTTPException(status_code=400, detail="Perfil inválido.")

    desconhecidas = sorted(set(payload.valores) - set(desempenho_utils.TIPOS))
    if desconhecidas:
        raise HTTPException(status_code=400, detail=f"Parâmetro desconhecido: {desconhecidas[0]}.")

    updates = {chave: _validar_valor(chave, bruto) for chave, bruto in payload.valores.items()}
    updates["DESEMPENHO_PERFIL"] = "" if perfil == "auto" else perfil

    # Coerência entre o pool da API e o teto do banco: com pool maior que max_connections, a API
    # sobe normalmente e só quebra depois, quando o pool tenta abrir a conexão que o Postgres
    # recusa — erro que aparece longe da causa. Checa contra o que VAI valer, não contra o atual.
    futuro = desempenho_utils.efetivos({**_read_env(), **{k: v for k, v in updates.items() if v}})
    try:
        if int(futuro["DB_POOL_MAX"]) >= int(futuro["PG_MAX_CONNECTIONS"]):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Conexões com o banco ({futuro['DB_POOL_MAX']}) precisa ser menor que "
                    f"max_connections ({futuro['PG_MAX_CONNECTIONS']}), com folga para psql e manutenção."
                ),
            )
    except (TypeError, ValueError):
        pass  # valor não numérico já foi recusado em _validar_valor; não é aqui que se trata

    _write_env(updates)
    return desempenho_utils.resumo()


# =====================================================================
# 11. IA própria de unidade/delegacia — /api/config/ia-entidade
# =====================================================================
#
# O elo do meio da hierarquia de CONEXÃO de IA (não confundir com os prompts da seção 10):
# .env → unidade superior (raiz→folha) → delegacia → usuário (rotas em usuarios.py). A linha
# vencedora vale inteira — ver funcoes/config_ia_usuario.py. A tela é Gerenciar delegacias
# (botão "IA" nas linhas de unidade e delegacia). Mesmas regras de chave/certificado da
# "IA do usuário": em branco mantém o gravado, a API nunca devolve a chave, só a máscara.

class ConfigIaEntidadeIn(BaseModel):
    tp_nivel: str = Field(..., max_length=10)
    id_entidade: int
    ativa: bool = True
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    apiKey: str = Field("", max_length=500)
    contextLength: int = Field(0, ge=0, le=2_000_000)
    reasoningEffort: str = Field("", max_length=10)
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False


_NIVEIS_IA_ENTIDADE = ("unidade", "delegacia")


@router.get("/api/config/ia-entidade/{tp_nivel}/{id_entidade}")
def get_ia_entidade(tp_nivel: str, id_entidade: int):
    if tp_nivel not in _NIVEIS_IA_ENTIDADE:
        raise HTTPException(status_code=400, detail="Nível deve ser 'unidade' ou 'delegacia'.")
    linha = config_ia_usuario.obter_entidade(tp_nivel, id_entidade)
    if linha is None:
        return {"definida": False, "ativa": False, "provider": "", "model": "", "apiUrl": "",
                "apiKeyMasked": "", "contextLength": 0, "reasoningEffort": "",
                "caCertResumo": "", "dt_atualizacao": None}
    return {
        "definida": True,
        "ativa": linha["in_ativa"],
        "provider": linha["co_provedor"],
        "model": linha["no_modelo"],
        "apiUrl": linha["tx_api_url"],
        "apiKeyMasked": mask_key(linha["tx_api_key"]),
        "contextLength": linha["qt_contexto"] or 0,
        "reasoningEffort": linha["co_esforco_raciocinio"],
        "caCertResumo": cert_utils.resumo(linha["tx_ca_cert"]),
        "dt_atualizacao": linha["dt_atualizacao"].isoformat() if linha["dt_atualizacao"] else None,
    }


@router.post("/api/config/ia-entidade")
def save_ia_entidade(payload: ConfigIaEntidadeIn, admin: dict = Depends(exigir_admin)):
    if payload.tp_nivel not in _NIVEIS_IA_ENTIDADE:
        raise HTTPException(status_code=400, detail="Nível deve ser 'unidade' ou 'delegacia'.")
    _conferir_entidade_prompt(payload.tp_nivel, payload.id_entidade)  # mesma checagem da seção 10

    atual = config_ia_usuario.obter_entidade(payload.tp_nivel, payload.id_entidade) or {}
    provider = sanitize(payload.provider).lower()
    modelo = sanitize(payload.model)
    api_url = sanitize(payload.apiUrl)
    esforco = sanitize(payload.reasoningEffort).lower()
    api_key = sanitize(payload.apiKey) or atual.get("tx_api_key", "")
    if payload.removerCaCert:
        ca_cert = ""
    elif (payload.caCert or "").strip():
        try:
            ca_cert = cert_utils.pem_para_env(cert_utils.normalizar_pem(payload.caCert))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Certificado inválido: {exc}.")
    else:
        ca_cert = atual.get("tx_ca_cert", "")

    try:
        config_ia_usuario.validar(provider, modelo, api_url, esforco, api_key, ca_cert)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    config_ia_usuario.salvar_entidade(
        payload.tp_nivel, payload.id_entidade,
        ativa=payload.ativa,
        provedor=provider,
        modelo=modelo,
        api_url=api_url,
        api_key=api_key,
        ca_cert=ca_cert,
        esforco=esforco,
        contexto=payload.contextLength or None,
        id_autor=admin.get("id_usuario"),
    )
    return {"ok": True}


@router.delete("/api/config/ia-entidade/{tp_nivel}/{id_entidade}")
def delete_ia_entidade(tp_nivel: str, id_entidade: int):
    if tp_nivel not in _NIVEIS_IA_ENTIDADE:
        raise HTTPException(status_code=400, detail="Nível deve ser 'unidade' ou 'delegacia'.")
    if not config_ia_usuario.remover_entidade(tp_nivel, id_entidade):
        raise HTTPException(status_code=404, detail="Esta entidade não tem IA própria configurada.")
    return {"ok": True}


# =====================================================================
# 10. Prompts de IA por nível — /api/config/prompts-ia
# =====================================================================
#
# Instruções ADICIONAIS de administração em 4 níveis (geral / unidade / delegacia / usuário) que
# se acumulam em cascata no prompt de sistema do chat — ver docs/unidades_prompts.md e
# funcoes/ia_prompt_niveis.py. O prompt base continua em código; a tabela SEGURANCA.PROMPTS_IA
# guarda uma linha ativa por entidade (UX113). Escrita só de admin global: quem escolhe o que a
# IA de TODO mundo recebe é quem administra o sistema inteiro.

_NIVEIS_PROMPT = ("sistema", "unidade", "delegacia", "usuario")

# nível → (tabela, PK) da tabela que valida a entidade. Sem FK na tabela (ID_ENTIDADE é
# polimórfico), então a existência é conferida aqui, na única porta de escrita.
_ENTIDADES_PROMPT = {
    "unidade": ("seguranca.unidades", "id_unidade"),
    "delegacia": ("seguranca.delegacias", "id_delegacia"),
    "usuario": ("seguranca.usr_tb01_usuarios", "id_usuario"),
}


class PromptNivelIn(BaseModel):
    tp_nivel: str = Field(..., max_length=10)
    id_entidade: int | None = None
    tx_prompt: str = Field("", max_length=20_000)


def _conferir_entidade_prompt(tp_nivel: str, id_entidade: int | None) -> None:
    if tp_nivel not in _NIVEIS_PROMPT:
        raise HTTPException(status_code=400, detail="Nível inválido.")
    if tp_nivel == "sistema":
        return
    if id_entidade is None:
        raise HTTPException(status_code=400, detail="Escolha a entidade deste prompt.")
    tabela, pk = _ENTIDADES_PROMPT[tp_nivel]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {tabela} WHERE {pk} = %s", (id_entidade,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=400, detail="Entidade do prompt não encontrada.")


@router.get("/api/config/prompts-ia")
def get_prompts_ia():
    """Todos os prompts cadastrados (ativos e inativos), com o nome da entidade resolvido."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id_prompt, p.tp_nivel, p.id_entidade, p.tx_prompt, p.in_ativa,
                       p.dt_atualizacao,
                       CASE p.tp_nivel
                           WHEN 'sistema'   THEN 'Sistema (todos os usuários)'
                           WHEN 'usuario'   THEN COALESCE(u.no_usuario, '(usuário removido)')
                           WHEN 'delegacia' THEN COALESCE(d.no_delegacia, '(delegacia removida)')
                           ELSE COALESCE(n.no_unidade, '(unidade removida)')
                       END AS no_entidade
                FROM seguranca.prompts_ia p
                LEFT JOIN seguranca.usr_tb01_usuarios u
                       ON p.tp_nivel = 'usuario' AND u.id_usuario = p.id_entidade
                LEFT JOIN seguranca.delegacias d
                       ON p.tp_nivel = 'delegacia' AND d.id_delegacia = p.id_entidade
                LEFT JOIN seguranca.unidades n
                       ON p.tp_nivel = 'unidade' AND n.id_unidade = p.id_entidade
                ORDER BY CASE p.tp_nivel WHEN 'sistema' THEN 0 WHEN 'unidade' THEN 1
                                         WHEN 'delegacia' THEN 2 ELSE 3 END,
                         no_entidade
                """
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


@router.get("/api/config/prompts-ia/opcoes")
def get_prompts_ia_opcoes():
    """As três listas que alimentam os seletores da aba (unidades, delegacias, usuários ativos).
    Vêm daqui e não de /api/unidades ou /api/usuarios para a aba não depender do shape de outras
    telas — só o mínimo que o seletor precisa."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_unidade, no_unidade, sg_unidade FROM seguranca.unidades ORDER BY no_unidade"
            )
            unidades = [
                {"id": i, "nome": n + (f" ({s})" if s else "")} for i, n, s in cur.fetchall()
            ]
            cur.execute("SELECT id_delegacia, no_delegacia FROM seguranca.delegacias ORDER BY no_delegacia")
            delegacias = [{"id": i, "nome": n} for i, n in cur.fetchall()]
            cur.execute(
                "SELECT id_usuario, no_usuario FROM seguranca.usr_tb01_usuarios "
                "WHERE in_ativo ORDER BY no_usuario"
            )
            usuarios = [{"id": i, "nome": n} for i, n in cur.fetchall()]
    return {"unidades": unidades, "delegacias": delegacias, "usuarios": usuarios}


@router.post("/api/config/prompts-ia")
def save_prompt_ia(payload: PromptNivelIn, usuario: dict = Depends(exigir_admin)):
    """Grava (ou limpa) o prompt de um nível+entidade.

    Texto vazio apaga a linha ativa — na tela "sem instrução" e "instrução inativa mas guardada"
    são coisas diferentes: a inativa continua na lista, o vazio some dela. Uma linha ativa por
    entidade (UX113): editar sobrescreve em vez de acumular."""
    _conferir_entidade_prompt(payload.tp_nivel, payload.id_entidade)
    texto = (payload.tx_prompt or "").strip()
    with get_connection() as conn:
        with conn.cursor() as cur:
            if not texto:
                cur.execute(
                    "DELETE FROM seguranca.prompts_ia "
                    "WHERE tp_nivel = %s AND COALESCE(id_entidade, 0) = COALESCE(%s, 0) AND in_ativa",
                    (payload.tp_nivel, payload.id_entidade),
                )
                conn.commit()
                return {"ok": True, "limpo": True}
            cur.execute(
                "UPDATE seguranca.prompts_ia SET tx_prompt = %s, dt_atualizacao = now(), "
                "id_usuario_autor = %s "
                "WHERE tp_nivel = %s AND COALESCE(id_entidade, 0) = COALESCE(%s, 0) AND in_ativa",
                (texto, usuario.get("id_usuario"), payload.tp_nivel, payload.id_entidade),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO seguranca.prompts_ia (tp_nivel, id_entidade, tx_prompt, "
                    "id_usuario_autor) VALUES (%s, %s, %s, %s)",
                    (payload.tp_nivel, payload.id_entidade, texto, usuario.get("id_usuario")),
                )
        conn.commit()
    return {"ok": True}


@router.delete("/api/config/prompts-ia/{id_prompt}")
def delete_prompt_ia(id_prompt: int):
    """Apaga uma linha qualquer (inclusive inativa — é a remoção do histórico reativável)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM seguranca.prompts_ia WHERE id_prompt = %s", (id_prompt,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Prompt não encontrado.")
        conn.commit()
    return {"ok": True}


@router.get("/api/config/prompts-ia/efetivo")
def get_prompt_ia_efetivo(tp_nivel: str = "usuario", id: int | None = None):
    """Prévia da cascata: o bloco EXATO que o prompt de sistema daquela pessoa/delegacia recebe
    hoje — é o que a tela mostra em "ver como fica" antes de alguém perguntar por quê."""
    if tp_nivel == "usuario":
        bloco = ia_prompt_niveis.prompt_niveis(id_usuario=id)
    elif tp_nivel == "delegacia":
        bloco = ia_prompt_niveis.prompt_niveis(id_delegacia=id)
    else:
        raise HTTPException(status_code=400, detail="Prévia disponível para 'usuario' ou 'delegacia'.")
    return {"bloco": bloco}
