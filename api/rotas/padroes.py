"""Rotas dos padrões de crime: a biblioteca (personalizada na tela "Padrões de crime",
escrita só por admin) e a execução/consulta por inquérito (Mesa de Inquérito)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito
from funcoes.padroes_utils import (
    ExecucaoOcupada,
    atualizar_padrao,
    criar_padrao,
    definir_ativo,
    iniciar_deteccao,
    listar_deteccoes,
    listar_execucoes,
    listar_padroes,
    remover_padrao,
)

router = APIRouter()


class PadraoIn(BaseModel):
    nome: str = Field(..., max_length=150)
    tp_padrao: str = Field(..., pattern="^(lexico|regex|temporal)$")
    categoria: str | None = Field(None, max_length=80)
    descricao: str | None = Field(None, max_length=500)
    definicao: dict


class AtivoIn(BaseModel):
    ativo: bool


@router.get("/api/padroes", dependencies=[Depends(exigir_aprovado)])
def padroes():
    """Lista todos os padrões — leitura liberada para qualquer aprovado (a Mesa mostra
    o que vai rodar), escrita só admin."""
    return listar_padroes()


@router.post("/api/padroes")
def criar(payload: PadraoIn, usuario: dict = Depends(exigir_admin)):
    """Cria um padrão novo."""
    try:
        id_padrao = criar_padrao(
            payload.nome, payload.tp_padrao, payload.definicao, payload.categoria,
            payload.descricao, usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_padrao": id_padrao}


@router.post("/api/padroes/{id_padrao}")
def atualizar(id_padrao: int, payload: PadraoIn, usuario: dict = Depends(exigir_admin)):
    """Atualiza um padrão existente."""
    try:
        atualizado = atualizar_padrao(
            id_padrao, payload.nome, payload.tp_padrao, payload.definicao, payload.categoria, payload.descricao
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizado:
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.post("/api/padroes/{id_padrao}/ativo")
def alterar_ativo(id_padrao: int, payload: AtivoIn, usuario: dict = Depends(exigir_admin)):
    """Ativa ou desativa um padrão (só os ativos entram na próxima detecção)."""
    if not definir_ativo(id_padrao, payload.ativo):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.delete("/api/padroes/{id_padrao}")
def remover(id_padrao: int, usuario: dict = Depends(exigir_admin)):
    """Remove um padrão definitivamente (com as detecções dele)."""
    if not remover_padrao(id_padrao):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


# ---------- execução por inquérito ----------


@router.post("/api/investigacao/inqueritos/{id_inquerito}/deteccao")
def iniciar(id_inquerito: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Dispara em background a detecção de todos os padrões ativos sobre o inquérito."""
    try:
        id_execucao = iniciar_deteccao(id_inquerito, usuario["id_usuario"])
    except ExecucaoOcupada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "id_execucao": id_execucao}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/deteccao")
def execucoes(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Histórico de execuções da detecção, para polling de status."""
    return listar_execucoes(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/deteccoes")
def deteccoes(id_inquerito: int, execucao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Detecções de uma execução (a mais recente concluída, se não informada)."""
    return listar_deteccoes(id_inquerito, execucao)
