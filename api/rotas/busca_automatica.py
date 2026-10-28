"""Rotas da busca automática de entidades (Mesa de Inquérito)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.busca_automatica_utils import iniciar_busca_automatica, listar_buscas
from funcoes.ia_delegacia import exigir_ia_inquerito
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito

router = APIRouter()


class BuscaIn(BaseModel):
    """'prompts' varre os prompts ativos; 'relacionar_manuais' propõe vínculos entre
    as entidades criadas manualmente pelo investigador."""

    tp_busca: str = Field("prompts", pattern="^(prompts|relacionar_manuais)$")


@router.get("/api/investigacao/inqueritos/{id_inquerito}/busca-automatica")
def buscas(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Histórico de execuções da busca automática deste inquérito, para polling de status."""
    return listar_buscas(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/busca-automatica")
def iniciar(id_inquerito: int, payload: BuscaIn | None = None, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Dispara em background uma busca automática de entidades (prompts ou relacionar manuais).

    Chama o provedor de IA, então passa pelo flag IN_IA_ATIVA (delegacia de quem pede e do
    inquérito) — só a listagem acima fica livre, que é polling de status e não chama IA."""
    exigir_ia_inquerito(id_inquerito, usuario)
    tp_busca = payload.tp_busca if payload else "prompts"
    try:
        id_busca = iniciar_busca_automatica(id_inquerito, usuario["id_usuario"], tp_busca)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_busca": id_busca}
