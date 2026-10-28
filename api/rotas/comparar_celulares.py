"""Rotas da tela "Comparar celulares": o que os aparelhos de um mesmo inquérito têm em comum.

Um endpoint por eixo (arquivos, contatos, conversas, Wi-Fi) em vez de um só com ?eixo=, porque a
tela carrega cada aba sob demanda e as três últimas são baratas enquanto a de arquivos cruza
centenas de milhares de linhas — misturá-las num endpoint só faria a aba leve pagar o preço da
pesada. Ver funcoes/comparar_celulares_utils.py e docs/comparar_celulares.md."""

from fastapi import APIRouter, Depends, Query

from funcoes.comparar_celulares_utils import (
    comparar_arquivos,
    comparar_contatos,
    comparar_conversas,
    comparar_wifi,
    listar_aparelhos,
)
from funcoes.investigacao_utils import exigir_acesso_inquerito

router = APIRouter()

BASE = "/api/investigacao/inqueritos/{id_inquerito}/comparar-celulares"


def _extracoes(extracoes: str | None) -> list[int] | None:
    """?extracoes=1,2,5 → [1, 2, 5]. Vazio/ausente = comparar todos os aparelhos do inquérito.

    Ids que não sejam número são descartados em silêncio: a lista é só um recorte de tela, e
    quem manda o que vale é o banco — _extracoes_do_inquerito só aceita extração DESTE
    inquérito, então id de fora não vaza dado de outro caso."""
    if not extracoes:
        return None
    ids = [p.strip() for p in extracoes.split(",")]
    return [int(p) for p in ids if p.lstrip("-").isdigit()] or None


@router.get(BASE + "/aparelhos")
def aparelhos_route(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Aparelhos comparáveis (extrações concluídas), com dono, total de arquivos e quantos deles
    têm hash — a tela avisa quando uma extração antiga não tem hash e por isso não entra no
    cruzamento de arquivos."""
    return listar_aparelhos(id_inquerito)


@router.get(BASE + "/arquivos")
def arquivos_route(
    id_inquerito: int,
    extracoes: str | None = None,
    relevancia: str = "usuario",
    categoria: str | None = None,
    busca: str | None = None,
    tamanho_min: int | None = None,
    ordenar: str = "relevancia",
    pagina: int = 1,
    limite: int = Query(25, ge=1, le=200),
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Arquivos idênticos (mesmo MD5) em dois ou mais aparelhos — o eixo principal da tela."""
    return comparar_arquivos(
        id_inquerito, _extracoes(extracoes), relevancia, categoria, busca,
        tamanho_min, ordenar, pagina, limite,
    )


@router.get(BASE + "/contatos")
def contatos_route(
    id_inquerito: int,
    extracoes: str | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = Query(50, ge=1, le=200),
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Telefones/e-mails na agenda de dois ou mais aparelhos."""
    return comparar_contatos(id_inquerito, _extracoes(extracoes), busca, pagina, limite)


@router.get(BASE + "/conversas")
def conversas_route(
    id_inquerito: int,
    extracoes: str | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = Query(50, ge=1, le=200),
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Interlocutores em comum nas conversas — inclui os aparelhos que falaram entre si."""
    return comparar_conversas(id_inquerito, _extracoes(extracoes), busca, pagina, limite)


@router.get(BASE + "/wifi")
def wifi_route(
    id_inquerito: int,
    extracoes: str | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = Query(50, ge=1, le=200),
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Redes Wi-Fi (mesmo BSSID) conhecidas por dois ou mais aparelhos."""
    return comparar_wifi(id_inquerito, _extracoes(extracoes), busca, pagina, limite)
