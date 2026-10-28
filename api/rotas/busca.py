"""Rota da busca unificada de conteúdo (tela Busca de evidências).

Reaproveita o mesmo motor que o chat de IA já usa como ferramenta interna
(funcoes/rag_hibrido.py: literal + sentido + grafo fundidos por RRF — ver
docs/rag_hibrido.md e docs/busca_semantica.md). O que esta rota acrescenta é só a
porta de entrada para gente: exige sessão aprovada + acesso ao inquérito (o motor
em si não conhece usuários) e limita o tamanho da consulta.
"""

from fastapi import APIRouter, Depends

from funcoes.investigacao_utils import exigir_acesso_inquerito
from funcoes.rag_hibrido import buscar_hibrida

router = APIRouter()

_TX_MAX_BUSCA = 300


@router.get("/api/investigacao/inqueritos/{id_inquerito}/busca-hibrida")
def busca_hibrida_rota(
    id_inquerito: int, q: str, limite: int = 30, usuario: dict = Depends(exigir_acesso_inquerito)
):
    """Busca híbrida de evidências do inquérito (mensagens, transcrições e documentos).

    Devolve {resultados: [{referencia, trecho, score, fonte}], total, origens, aviso?}
    — a referência no formato [msg:wpp:123] / [doc:456:2] é a mesma que o chat usa
    em citações, então a tela pode linkar direto para o registro original.
    """
    consulta = (q or "").strip()[:_TX_MAX_BUSCA]
    return buscar_hibrida(id_inquerito, consulta, limite)
