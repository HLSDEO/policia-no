"""Rotas de auditoria de acesso a dado de inquérito — auditoria e administradores (ver dependencies do router).

Responde "quem leu o quê, de qual inquérito, quando", incluindo as tentativas barradas por falta de
permissão. Os registros são agregados por hora — ver funcoes/auditoria_acesso.py e
db/init/07_seguranca_usuarios.sql.
"""

from fastapi import APIRouter, Depends, Query

from funcoes.auditoria_acesso import listar_acessos
from funcoes.auth_utils import exigir_auditoria

router = APIRouter(dependencies=[Depends(exigir_auditoria)])


@router.get("/api/admin/acessos")
def auditoria_acessos(
    id_inquerito: int | None = None,
    id_usuario: int | None = None,
    somente_negados: bool = False,
    dias: int | None = Query(None, ge=1, le=3650),
    limite: int = Query(100, ge=1, le=500),
    deslocamento: int = Query(0, ge=0),
):
    """Lista as janelas de acesso a inquérito, mais recentes primeiro, com os totais do filtro."""
    return listar_acessos(id_inquerito, id_usuario, somente_negados, dias, limite, deslocamento)
