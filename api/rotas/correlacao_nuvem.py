"""Rotas da tela "Nuvem × aparelho": o arquivo que sumiu do celular e continua na conta na nuvem.

Três endpoints, pelo mesmo motivo que comparar_celulares.py tem um por eixo: `fontes` e `resumo`
são baratos e a tela precisa deles antes de qualquer filtro (para montar os chips e o panorama
"38 apagados, 12 recuperados"), enquanto `arquivos` cruza as duas extrações e é o único caro —
juntá-los faria a abertura da tela pagar o preço do cruzamento.

Ver funcoes/correlacao_nuvem_utils.py e docs/correlacao_nuvem.md.
"""

from fastapi import APIRouter, Depends, Query

from funcoes.correlacao_nuvem_utils import (
    correlacionar_arquivos,
    listar_fontes,
    resumo,
)
from funcoes.investigacao_utils import exigir_acesso_inquerito

router = APIRouter()

BASE = "/api/investigacao/inqueritos/{id_inquerito}/correlacao-nuvem"


def _extracoes(extracoes: str | None) -> list[int] | None:
    """?extracoes=1,2,5 -> [1, 2, 5]. Vazio/ausente = usar todas as extrações do inquérito.

    Id que não seja número é descartado em silêncio: a lista é só um recorte de tela, e quem
    manda o que vale é o banco — _separar_extracoes só aceita extração DESTE inquérito, então id
    de fora não vaza dado de outro caso (mesma guarda de rotas/comparar_celulares.py)."""
    if not extracoes:
        return None
    ids = [p.strip() for p in extracoes.split(",")]
    return [int(p) for p in ids if p.lstrip("-").isdigit()] or None


@router.get(BASE + "/fontes")
def fontes_route(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """As extrações do inquérito separadas em nuvem e aparelho, com quantos arquivos cada uma
    tem, quantos têm hash, quantos têm nome de mídia do WhatsApp e quantos têm sidecar de nuvem
    — é com esses números que a tela decide quais avisos mostrar."""
    return listar_fontes(id_inquerito)


@router.get(BASE + "/resumo")
def resumo_route(
    id_inquerito: int,
    extracoes: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Contagem por estado (só nuvem / apagado / recuperado) da mídia do WhatsApp."""
    return resumo(id_inquerito, _extracoes(extracoes))


@router.get(BASE + "/arquivos")
def arquivos_route(
    id_inquerito: int,
    extracoes: str | None = None,
    criterio: str = "nome",
    estado: str | None = None,
    categoria: str | None = None,
    so_whatsapp: bool = True,
    so_do_aparelho: bool = False,
    busca: str | None = None,
    tamanho_min: int | None = None,
    ordenar: str = "relevancia",
    pagina: int = 1,
    limite: int = Query(25, ge=1, le=200),
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Arquivos da nuvem sem par no aparelho, com o estado de cada um e o contexto da conversa
    em que trafegaram."""
    return correlacionar_arquivos(
        id_inquerito, _extracoes(extracoes), criterio, estado, categoria,
        so_whatsapp, so_do_aparelho, busca, tamanho_min, ordenar, pagina, limite,
    )
