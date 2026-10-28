"""Rotas de auditoria das chamadas de IA — auditoria e administradores (ver dependencies do router).

A listagem devolve um resumo por turno (com totais agregados de tokens/custo/tempo do filtro);
o detalhe traz o registro completo: prompt, contexto, ferramentas chamadas e documentos consultados.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from funcoes.auth_utils import exigir_auditoria
from funcoes.ia_auditoria import _FEEDBACKS_VALIDOS, _ORDENACOES, listar_auditoria, obter_auditoria

router = APIRouter(dependencies=[Depends(exigir_auditoria)])

ORIGENS_VALIDAS = {"chat_inquerito", "busca_automatica", "busca_documentos", "documento_analise"}


@router.get("/api/admin/ia/auditoria")
def auditoria_ia(
    id_inquerito: int | None = None,
    id_usuario: int | None = None,
    origem: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    ordenar: str | None = None,
    feedback: str | None = None,
    limite: int = Query(50, ge=1, le=200),
    deslocamento: int = Query(0, ge=0),
):
    """Lista os registros de auditoria de IA, mais recentes primeiro, com totais do filtro.

    Filtros: ?id_inquerito=, ?id_usuario=, ?origem=, ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD
    (sobre a data da chamada), ?feedback=com_avaliacao|sem_avaliacao|positivas|negativas (voto
    👍/👎 da resposta — só o chat do inquérito tem voto; com ele ativo as demais origens somem).
    Ordenação: ?ordenar= (ver _ORDENACOES; fora da lista cai no padrão)."""
    if origem and origem not in ORIGENS_VALIDAS:
        raise HTTPException(status_code=400, detail="Origem inválida.")
    if ordenar and ordenar not in _ORDENACOES:
        raise HTTPException(status_code=400, detail="Ordenação inválida.")
    if feedback and feedback not in _FEEDBACKS_VALIDOS:
        raise HTTPException(status_code=400, detail="Filtro de avaliação inválido.")
    # nomeados de propósito: listar_auditoria tem id_conversa_ia como 3º parâmetro, e a chamada
    # posicional entregava `origem` para ele (e o `limite` para `origem`), quebrando a tela com 500
    try:
        return listar_auditoria(
            id_inquerito=id_inquerito,
            id_usuario=id_usuario,
            origem=origem,
            data_de=data_de,
            data_ate=data_ate,
            ordenar=ordenar,
            feedback=feedback,
            limite=limite,
            deslocamento=deslocamento,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/admin/ia/auditoria/{id_auditoria}")
def detalhe_auditoria_ia(id_auditoria: int):
    """Um registro completo de auditoria (prompt, contexto, ferramentas, documentos)."""
    registro = obter_auditoria(id_auditoria)
    if registro is None:
        raise HTTPException(status_code=404, detail="Registro de auditoria não encontrado.")
    return registro
