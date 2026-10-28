"""Rotas do workspace de hipóteses: hipóteses do inquérito inteiro (não presas a um documento)
com status kanban, responsável, evidências a favor/contra e pendências. Ver funcoes/hipoteses_utils.py —
o chat de IA do inquérito injeta bloco_hipoteses_inquerito como contexto."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_aprovado
from funcoes.hipoteses_utils import (
    apagar_evidencia,
    apagar_hipotese,
    apagar_pendencia,
    criar_evidencia,
    criar_hipotese,
    criar_pendencia,
    editar_hipotese,
    editar_pendencia,
    listar_hipoteses,
    obter_hipotese,
    obter_id_hipotese_de_evidencia,
    obter_id_hipotese_de_pendencia,
    obter_id_inquerito_de_hipotese,
    usuarios_para_responsavel,
)
from funcoes.ia_provedores import ErroProvedorIA
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito, usuario_tem_acesso_inquerito

router = APIRouter()

Status = Literal["proposta", "em_analise", "confirmada", "refutada", "arquivada"]
Natureza = Literal["hipotese", "normativa", "fato"]


class NovaHipoteseIn(BaseModel):
    """Corpo aceito pelo POST que registra uma nova hipótese no workspace do inquérito. O par
    tp_escopo/id_escopo é opcional: quando presente, associa a hipótese a uma mídia importada —
    ela passa a entrar também no chat de IA específico daquela mídia."""

    texto: str = Field(..., min_length=1, max_length=4000)
    cd_status: Status = "proposta"
    id_usuario_responsavel: int | None = None
    tp_natureza: Natureza = "hipotese"
    fl_contestavel_ia: bool = False
    tp_escopo: str | None = Field(None, max_length=20)
    id_escopo: int | None = None


class EditarHipoteseIn(BaseModel):
    """Corpo aceito pelo PUT que edita hipótese existente (inclusive mover de painel no quadro)."""

    texto: str = Field(..., min_length=1, max_length=4000)
    cd_status: Status
    id_usuario_responsavel: int | None = None
    tp_natureza: Natureza = "hipotese"
    fl_contestavel_ia: bool = False
    tp_escopo: str | None = Field(None, max_length=20)
    id_escopo: int | None = None


class NovaEvidenciaIn(BaseModel):
    """Corpo aceito pelo POST que registra evidência (a favor ou contra) numa hipótese. O par
    tp_escopo/id_escopo é opcional: quando presente, ancora a evidência a um item importado."""

    tp_lado: Literal["favoravel", "contraria"]
    texto: str = Field(..., min_length=1, max_length=4000)
    tp_escopo: str | None = Field(None, max_length=20)
    id_escopo: int | None = None


class NovaPendenciaIn(BaseModel):
    """Corpo aceito pelo POST que registra uma pendência (diligência em aberto) numa hipótese."""

    texto: str = Field(..., min_length=1, max_length=2000)


class EditarPendenciaIn(BaseModel):
    """Corpo aceito pelo PUT que corrige o texto e/ou marca a pendência como concluída."""

    texto: str = Field(..., min_length=1, max_length=2000)
    in_concluida: bool = False


def _exigir_acesso_via_hipotese(id_hipotese: int, usuario: dict, *, escrita: bool = False) -> int:
    """Resolve o inquérito dono da hipótese e confere acesso — para rotas que só recebem
    id_hipotese. Devolve o id_inquerito (a edição precisa dele para validar o vínculo de mídia)."""
    id_inquerito = obter_id_inquerito_de_hipotese(id_hipotese)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Hipótese não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return id_inquerito


def _exigir_acesso_via_evidencia(id_evidencia: int, usuario: dict, *, escrita: bool = False) -> None:
    id_hipotese = obter_id_hipotese_de_evidencia(id_evidencia)
    if id_hipotese is None:
        raise HTTPException(status_code=404, detail="Evidência não encontrada.")
    _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=escrita)


def _exigir_acesso_via_pendencia(id_pendencia: int, usuario: dict, *, escrita: bool = False) -> None:
    id_hipotese = obter_id_hipotese_de_pendencia(id_pendencia)
    if id_hipotese is None:
        raise HTTPException(status_code=404, detail="Pendência não encontrada.")
    _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=escrita)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/hipoteses")
def listar(
    id_inquerito: int,
    tp_escopo: str | None = None,
    id_escopo: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista as hipóteses do inquérito com contagens — carga inicial do quadro. Com
    tp_escopo/id_escopo vira a visão "Hipótese da mídia" (só as ligadas àquela mídia)."""
    return listar_hipoteses(id_inquerito, tp_escopo, id_escopo)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/hipoteses/usuarios")
def usuarios(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Quem pode ser responsável por uma hipótese deste inquérito (seletor da tela)."""
    return usuarios_para_responsavel(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/hipoteses")
def criar(id_inquerito: int, payload: NovaHipoteseIn, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Registra uma nova hipótese no workspace do inquérito."""
    try:
        id_hipotese = criar_hipotese(
            id_inquerito,
            payload.texto.strip(),
            usuario["id_usuario"],
            payload.cd_status,
            payload.id_usuario_responsavel,
            payload.tp_natureza,
            payload.fl_contestavel_ia,
            payload.tp_escopo,
            payload.id_escopo,
        )
    except (ErroProvedorIA, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_hipotese": id_hipotese}


@router.get("/api/investigacao/hipoteses/{id_hipotese}")
def detalhe(id_hipotese: int, usuario: dict = Depends(exigir_aprovado)):
    """Hipótese com evidências e pendências completas — painel de detalhe da tela."""
    _exigir_acesso_via_hipotese(id_hipotese, usuario)
    hipotese = obter_hipotese(id_hipotese)
    if hipotese is None:
        raise HTTPException(status_code=404, detail="Hipótese não encontrada.")
    return hipotese


@router.put("/api/investigacao/hipoteses/{id_hipotese}")
def editar(id_hipotese: int, payload: EditarHipoteseIn, usuario: dict = Depends(exigir_aprovado)):
    """Edita hipótese existente: texto, status no quadro, responsável, natureza."""
    id_inquerito = _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=True)
    try:
        atualizou = editar_hipotese(
            id_hipotese,
            payload.texto.strip(),
            payload.cd_status,
            payload.id_usuario_responsavel,
            payload.tp_natureza,
            payload.fl_contestavel_ia,
            id_inquerito,
            payload.tp_escopo,
            payload.id_escopo,
        )
    except (ErroProvedorIA, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizou:
        raise HTTPException(status_code=404, detail="Hipótese não encontrada.")
    return {"ok": True}


@router.delete("/api/investigacao/hipoteses/{id_hipotese}")
def apagar(id_hipotese: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga uma hipótese (evidências e pendências saem junto)."""
    _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=True)
    if not apagar_hipotese(id_hipotese):
        raise HTTPException(status_code=404, detail="Hipótese não encontrada.")
    return {"ok": True}


@router.post("/api/investigacao/hipoteses/{id_hipotese}/evidencias")
def nova_evidencia(id_hipotese: int, payload: NovaEvidenciaIn, usuario: dict = Depends(exigir_aprovado)):
    """Registra evidência a favor ou contra a hipótese, com ou sem vínculo a um item importado."""
    _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=True)
    try:
        id_evidencia = criar_evidencia(
            id_hipotese,
            payload.tp_lado,
            payload.texto.strip(),
            payload.tp_escopo,
            payload.id_escopo,
            usuario["id_usuario"],
        )
    except (ErroProvedorIA, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_evidencia": id_evidencia}


@router.delete("/api/investigacao/evidencias/{id_evidencia}")
def apagar_evidencia_rota(id_evidencia: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga uma evidência de hipótese."""
    _exigir_acesso_via_evidencia(id_evidencia, usuario, escrita=True)
    if not apagar_evidencia(id_evidencia):
        raise HTTPException(status_code=404, detail="Evidência não encontrada.")
    return {"ok": True}


@router.post("/api/investigacao/hipoteses/{id_hipotese}/pendencias")
def nova_pendencia(id_hipotese: int, payload: NovaPendenciaIn, usuario: dict = Depends(exigir_aprovado)):
    """Registra uma pendência (diligência em aberto) na hipótese."""
    _exigir_acesso_via_hipotese(id_hipotese, usuario, escrita=True)
    id_pendencia = criar_pendencia(id_hipotese, payload.texto.strip(), usuario["id_usuario"])
    return {"ok": True, "id_pendencia": id_pendencia}


@router.put("/api/investigacao/pendencias/{id_pendencia}")
def editar_pendencia_rota(id_pendencia: int, payload: EditarPendenciaIn, usuario: dict = Depends(exigir_aprovado)):
    """Marca/desmarca a pendência como concluída e/ou corrige o texto."""
    _exigir_acesso_via_pendencia(id_pendencia, usuario, escrita=True)
    if not editar_pendencia(id_pendencia, payload.texto.strip(), payload.in_concluida):
        raise HTTPException(status_code=404, detail="Pendência não encontrada.")
    return {"ok": True}


@router.delete("/api/investigacao/pendencias/{id_pendencia}")
def apagar_pendencia_rota(id_pendencia: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga uma pendência de hipótese."""
    _exigir_acesso_via_pendencia(id_pendencia, usuario, escrita=True)
    if not apagar_pendencia(id_pendencia):
        raise HTTPException(status_code=404, detail="Pendência não encontrada.")
    return {"ok": True}
