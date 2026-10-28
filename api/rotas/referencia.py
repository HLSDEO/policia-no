"""Rotas de busca em dados de referência (pessoa, município, UF), usadas pelo autocomplete
da tela de associação de contato de conversa (WhatsApp/SMS)."""

from fastapi import APIRouter, Query

from funcoes.ia_investigacao import buscar_pessoa_referencia
from funcoes.referencia_utils import buscar_municipios, listar_ufs

router = APIRouter()

# Teto do que o autocomplete pode pedir. `limite: int` sem máximo era uma rota autenticada em que
# qualquer analista mandava ?limite=100000000 contra a base de 220 M pessoas — uma requisição
# derrubando o sistema para os outros. As funções chamadas aplicam o mesmo teto por dentro (elas
# também são ferramentas de IA); este Query() existe para o pedido inválido ser recusado com 422
# antes de chegar ao banco. Padrão copiado de rotas/comparar_celulares.py.
_LIMITE_MAX = 200


@router.get("/api/referencia/pessoas")
def pessoas(termo: str = "", limite: int = Query(15, ge=1, le=_LIMITE_MAX)):
    """Busca fuzzy de pessoas (base de referência) por nome, para autocomplete."""
    return buscar_pessoa_referencia(termo=termo, limite=limite)


@router.get("/api/referencia/municipios")
def municipios(termo: str = "", limite: int = Query(15, ge=1, le=_LIMITE_MAX)):
    """Busca de municípios por nome, para autocomplete."""
    return buscar_municipios(termo, limite)


@router.get("/api/referencia/ufs")
def ufs():
    """Lista as UFs, para o seletor de estado."""
    return listar_ufs()
