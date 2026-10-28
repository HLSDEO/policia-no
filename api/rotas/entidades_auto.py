"""Rotas das entidades automatizadas: a biblioteca de padrões determinísticos
(personalizada na aba "Entidades automatizadas", escrita só por admin) e a
execução/consulta por inquérito."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes.entidades_auto_utils import (
    ExecucaoOcupada,
    atualizar_padrao,
    criar_padrao,
    definir_ativo,
    iniciar_extracao,
    listar_deteccoes,
    listar_execucoes,
    listar_padroes,
    remover_padrao,
)
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito

router = APIRouter()


class PadraoAutoIn(BaseModel):
    nome: str = Field(..., max_length=150)
    tp_achado: str = Field(..., max_length=40)
    categoria: str | None = Field(None, max_length=80)
    descricao: str | None = Field(None, max_length=500)
    definicao: dict


class AtivoIn(BaseModel):
    ativo: bool


@router.get("/api/entidades-auto", dependencies=[Depends(exigir_aprovado)])
def padroes():
    """Lista todos os padrões automáticos — leitura liberada para qualquer aprovado
    (a aba mostra o que vai rodar), escrita só admin."""
    return listar_padroes()


@router.post("/api/entidades-auto")
def criar(payload: PadraoAutoIn, usuario: dict = Depends(exigir_admin)):
    """Cria um padrão automático novo."""
    try:
        id_padrao = criar_padrao(
            payload.nome, payload.tp_achado, payload.definicao, payload.categoria,
            payload.descricao, usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_padrao": id_padrao}


@router.post("/api/entidades-auto/{id_padrao}")
def atualizar(id_padrao: int, payload: PadraoAutoIn, usuario: dict = Depends(exigir_admin)):
    """Atualiza um padrão automático existente."""
    try:
        atualizado = atualizar_padrao(
            id_padrao, payload.nome, payload.tp_achado, payload.definicao, payload.categoria, payload.descricao
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizado:
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.post("/api/entidades-auto/{id_padrao}/ativo")
def alterar_ativo(id_padrao: int, payload: AtivoIn, usuario: dict = Depends(exigir_admin)):
    """Ativa ou desativa um padrão (só os ativos entram na próxima extração)."""
    if not definir_ativo(id_padrao, payload.ativo):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.delete("/api/entidades-auto/{id_padrao}")
def remover(id_padrao: int, usuario: dict = Depends(exigir_admin)):
    """Remove um padrão definitivamente (com as detecções dele)."""
    if not remover_padrao(id_padrao):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


# ---------- execução por inquérito ----------


@router.post("/api/investigacao/inqueritos/{id_inquerito}/extracao-auto")
def iniciar(id_inquerito: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Dispara em background a extração automática sobre os documentos do inquérito."""
    try:
        id_execucao = iniciar_extracao(id_inquerito, usuario["id_usuario"])
    except ExecucaoOcupada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "id_execucao": id_execucao}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/extracao-auto")
def execucoes(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Histórico de execuções da extração automática, para polling de status."""
    return listar_execucoes(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/extracao-auto/deteccoes")
def deteccoes(id_inquerito: int, execucao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Detecções de uma execução (a mais recente concluída, se não informada)."""
    return listar_deteccoes(id_inquerito, execucao)
