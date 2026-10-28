"""Rotas de auditoria de gestão de usuários — auditoria e administradores."""

from fastapi import APIRouter, Depends, Query

from funcoes.auditoria_usuarios import listar_auditoria_usuarios
from funcoes.auth_utils import exigir_auditoria

router = APIRouter(dependencies=[Depends(exigir_auditoria)])


@router.get("/api/admin/usuarios/auditoria")
def auditoria_usuarios(
    id_usuario_autor: int | None = Query(default=None),
    id_usuario_alvo: int | None = Query(default=None),
    tp_acao: str | None = Query(default=None),
    dias: int | None = Query(default=None, ge=1, le=3650),
    limite: int = Query(default=100, ge=1, le=500),
    deslocamento: int = Query(default=0, ge=0),
    usuario: dict = Depends(exigir_auditoria),
):
    """Lista registros de auditoria de gestão de usuários com filtros opcionais."""
    return listar_auditoria_usuarios(
        id_usuario_autor=id_usuario_autor,
        id_usuario_alvo=id_usuario_alvo,
        tp_acao=tp_acao,
        dias=dias,
        limite=limite,
        deslocamento=deslocamento,
    )