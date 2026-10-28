"""Cópia de pasta de rede (SMB) para data/<DELEGACIA>/ — ver funcoes/rede_smb.py.

Quatro passos, iguais aos do modal de importacao.html:

1. `POST /api/rede/listar` — conecta com as credenciais informadas e devolve um nível do
   compartilhamento, para o usuário marcar o que copiar;
2. `POST /api/rede/copiar` — valida o destino com `delegacia_permitida` (usuário comum só
   copia para a PRÓPRIA delegacia, como no upload e nas listagens) e dispara a cópia em
   segundo plano, devolvendo o id do job;
3. `GET /api/rede/copiar` — minhas cópias (ativas ou recentes), para o painel "Cópias" —
   é o que permite fechar a página e acompanhar depois: o progresso mora em JOB_TB01_JOBS,
   não na memória do processo;
4. `GET /api/rede/copiar/{id_job}` (+ `POST .../cancelar`) — polling de progresso do modal.

A senha da rede chega só no corpo do POST, vive só na memória da requisição/thread e nunca
é registrada em log nem devolvida em resposta — ver o aviso em funcoes/rede_smb.py.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import jobs_utils
from funcoes.auth_utils import exigir_aprovado
from funcoes.rede_smb import (
    ErroRedeSmb,
    dividir_unc,
    iniciar_copia,
    listar_pasta_rede,
    resumo_copia,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ListarRedeIn(BaseModel):
    """Corpo do POST que lista um nível do compartilhamento."""

    caminho: str = Field(..., min_length=3, max_length=500)
    usuario: str | None = Field(default=None, max_length=256)
    senha: str | None = Field(default=None, max_length=256)
    dominio: str | None = Field(default=None, max_length=128)
    subpasta: str = Field(default="", max_length=400)


class CopiarRedeIn(BaseModel):
    """Corpo do POST que dispara a cópia para a pasta da delegacia."""

    caminho: str = Field(..., min_length=3, max_length=500)
    itens: list[str] = Field(..., min_length=1, max_length=200)
    delegacia: str | None = Field(default=None, max_length=100)
    usuario: str | None = Field(default=None, max_length=256)
    senha: str | None = Field(default=None, max_length=256)
    dominio: str | None = Field(default=None, max_length=128)


def _job_copia_visivel(id_job: int, usuario: dict) -> dict:
    """A linha do job de cópia, se for deste tipo e quem pergunta puder vê-la.

    404 quando não existe, não é cópia de rede, ou é de outro usuário (sem distinguir —
    job global só o dono ou admin vê, como em rotas/jobs.py).
    """
    try:
        id_job = int(id_job)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Cópia não encontrada.")
    job = jobs_utils.obter(id_job)
    if job is None or job["tp_job"] != jobs_utils.TP_COPIA_REDE:
        raise HTTPException(status_code=404, detail="Cópia não encontrada.")
    if not (usuario.get("in_admin") or job["id_usuario"] == usuario.get("id_usuario")):
        raise HTTPException(status_code=404, detail="Cópia não encontrada.")
    return job


@router.post("/api/rede/listar")
def listar_rede(payload: ListarRedeIn, usuario: dict = Depends(exigir_aprovado)):
    """Lista um nível da pasta de rede com as credenciais informadas (sem gravar nada)."""
    try:
        dividir_unc(payload.caminho)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return listar_pasta_rede(payload.caminho, usuario=payload.usuario,
                                 senha=payload.senha, dominio=payload.dominio,
                                 subpasta=payload.subpasta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ErroRedeSmb as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/api/rede/copiar")
def copiar_rede(payload: CopiarRedeIn, usuario: dict = Depends(exigir_aprovado)):
    """Dispara a cópia dos itens marcados para data/<DELEGACIA>/, em segundo plano."""
    from funcoes.investigacao_utils import delegacia_permitida

    try:
        subpasta = delegacia_permitida(payload.delegacia, usuario)
    except HTTPException:
        raise
    if not subpasta:
        raise HTTPException(
            status_code=400,
            detail="Escolha a delegacia de destino na topbar antes de copiar.",
        )
    # delegacia_permitida devolve o nome sanitizado da pasta — é ele que vira diretório.
    try:
        id_job = iniciar_copia(caminho_unc=payload.caminho, itens=payload.itens,
                               delegacia=subpasta, id_usuario=usuario["id_usuario"],
                               usuario_rede=payload.usuario, senha_rede=payload.senha,
                               dominio_rede=payload.dominio)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ErroRedeSmb as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    job = jobs_utils.obter(id_job)
    return {"ok": True, "id_job": id_job, "total": job["qt_total"] if job else len(payload.itens),
            "delegacia": subpasta}


@router.get("/api/rede/copiar")
def minhas_copias(ativas: bool = True, limite: int = 20,
                  usuario: dict = Depends(exigir_aprovado)):
    """Minhas cópias de rede, da mais recente à mais antiga — o painel "Cópias".

    `?ativas=true` (padrão) só as em andamento, para o badge da topbar; `?ativas=false`
    traz o histórico recente (concluídas, erros, canceladas). Sobrevive a fechar a página
    e a reiniciar a API: o estado mora em JOB_TB01_JOBS, não na memória do processo.
    """
    jobs = jobs_utils.listar_por_usuario(jobs_utils.TP_COPIA_REDE, usuario["id_usuario"],
                                         so_ativos=ativas, limite=limite)
    return [resumo_copia(j) for j in jobs]


@router.get("/api/rede/copiar/{id_job}")
def status_copia(id_job: int, usuario: dict = Depends(exigir_aprovado)):
    """Progresso da cópia — o polling do modal consulta aqui a cada 2 s."""
    return resumo_copia(_job_copia_visivel(id_job, usuario))


@router.post("/api/rede/copiar/{id_job}/cancelar")
def cancelar_copia(id_job: int, usuario: dict = Depends(exigir_aprovado)):
    """Pede para a cópia parar. Só o dono ou um admin."""
    _job_copia_visivel(id_job, usuario)
    if not jobs_utils.solicitar_cancelamento(int(id_job)):
        raise HTTPException(status_code=409, detail="Esta cópia já terminou.")
    return {"ok": True, "mensagem": "Cancelamento solicitado."}
