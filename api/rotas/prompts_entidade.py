"""Rotas de administração dos prompts que guiam a busca automática de entidades —
todas exigem administrador (ver dependencies do router)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin
from funcoes.prompts_entidade_utils import (
    atualizar_prompt,
    criar_prompt,
    definir_ativo,
    listar_prompts,
    remover_prompt,
)

router = APIRouter(dependencies=[Depends(exigir_admin)])


class PromptIn(BaseModel):
    titulo: str = Field(..., max_length=150)
    texto: str = Field(..., max_length=4000)
    tp_escopo: str = Field(..., max_length=20)


class AtivoIn(BaseModel):
    ativo: bool


@router.get("/api/prompts-entidade")
def prompts():
    """Lista todos os prompts (ativos e inativos)."""
    return listar_prompts()


@router.post("/api/prompts-entidade")
def criar(payload: PromptIn, usuario: dict = Depends(exigir_admin)):
    """Cria um prompt novo."""
    try:
        id_prompt = criar_prompt(payload.titulo, payload.texto, payload.tp_escopo, usuario["id_usuario"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_prompt": id_prompt}


@router.post("/api/prompts-entidade/{id_prompt}")
def atualizar(id_prompt: int, payload: PromptIn):
    """Atualiza título/texto/escopo de um prompt existente."""
    try:
        atualizado = atualizar_prompt(id_prompt, payload.titulo, payload.texto, payload.tp_escopo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizado:
        raise HTTPException(status_code=404, detail="Prompt não encontrado.")
    return {"ok": True}


@router.post("/api/prompts-entidade/{id_prompt}/ativo")
def alterar_ativo(id_prompt: int, payload: AtivoIn):
    """Ativa ou desativa um prompt (só os ativos entram na próxima busca automática)."""
    if not definir_ativo(id_prompt, payload.ativo):
        raise HTTPException(status_code=404, detail="Prompt não encontrado.")
    return {"ok": True}


@router.delete("/api/prompts-entidade/{id_prompt}")
def remover(id_prompt: int):
    """Remove um prompt definitivamente."""
    if not remover_prompt(id_prompt):
        raise HTTPException(status_code=404, detail="Prompt não encontrado.")
    return {"ok": True}
