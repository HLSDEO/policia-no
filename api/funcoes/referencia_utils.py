"""Busca em dados de referência (município, UF) para a tela de associação de contato de conversa.

Busca de pessoa reaproveita funcoes.ia_investigacao.buscar_pessoa_referencia — mesma
lógica fuzzy usada pela IA, exposta aqui como endpoint pra alimentar o autocomplete da tela.
"""

from funcoes.db_utils import get_connection


# Teto do `limite`, aplicado aqui e não só na rota — /api/referencia/municipios recebia
# `limite: int` sem máximo e repassava direto para o LIMIT da SQL.
_LIMITE_MAX = 500


def buscar_municipios(termo: str, limite: int = 15) -> list[dict]:
    """Busca município por nome (substring), com a UF já resolvida pela hierarquia do IBGE."""
    termo = (termo or "").strip()
    if not termo:
        return []
    limite = max(1, min(_LIMITE_MAX, limite or 1))
    query = """
        SELECT m.id_municipio, m.no_municipio, uf.sg_uf, uf.no_uf
        FROM dados_referencia.ibge_tb08_municipio m
        LEFT JOIN dados_referencia.ibge_tb05_microrregiao mic ON mic.id_microrregiao = m.id_microrregiao
        LEFT JOIN dados_referencia.ibge_tb04_mesorregiao mes ON mes.id_mesorregiao = mic.id_mesorregiao
        LEFT JOIN dados_referencia.ibge_tb03_uf uf ON uf.id_uf = mes.id_uf
        WHERE m.no_municipio ILIKE %(termo)s
        ORDER BY m.no_municipio
        LIMIT %(limite)s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"termo": f"%{termo}%", "limite": limite})
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_ufs() -> list[dict]:
    """Lista as UFs, para o seletor de estado."""
    query = "SELECT id_uf, sg_uf, no_uf FROM dados_referencia.ibge_tb03_uf ORDER BY no_uf"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]
