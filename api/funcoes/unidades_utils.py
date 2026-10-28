"""Herança de UF na árvore de unidades (ver docs/unidades_prompts.md).

A UF da delegacia deixa de ser digitada quando ela tem unidade superior: quem define é a árvore —
a UF efetiva de um nó é a dele mesmo ou, se vazia, a do ancestral mais próximo. Um único UPDATE
recursivo faz a replicação inteira (a árvore tem dezenas de nós; roda no commit do CRUD de
unidade e no criar/editar de delegacia, e sai mais correto que espelhar campo a campo).

Sempre chamado DENTRO da transação do CRUD, antes do commit — a delegacia salva com a UF que a
árvore tem naquele instante, nunca com a UF que a tela mostrou quando o modal abriu.
"""

from funcoes.db_utils import get_connection

# Desce a árvore carregando a UF do ancestral (COALESCE: o nó manda sobre o herdado) e copia o
# resultado para as delegacias filhas. Só sobrescreve quando há UF na cadeia e quando difere —
# delegacia sem unidade (id_unidade NULL) nunca é tocada: continua com a UF digitada à mão.
_HERDAR_UF_SQL = """
WITH RECURSIVE herdada(id_unidade, uf, profundidade) AS (
    SELECT u.id_unidade, u.uf, 0
    FROM seguranca.unidades u
    WHERE u.id_unidade_pai IS NULL
    UNION ALL
    SELECT c.id_unidade, COALESCE(c.uf, h.uf), h.profundidade + 1
    FROM herdada h
    JOIN seguranca.unidades c ON c.id_unidade_pai = h.id_unidade
    WHERE h.profundidade < 20
)
UPDATE seguranca.delegacias d
SET uf = h.uf
FROM seguranca.unidades eu
JOIN herdada h ON h.id_unidade = eu.id_unidade
WHERE d.id_unidade = eu.id_unidade
  AND h.uf IS NOT NULL
  AND d.uf IS DISTINCT FROM h.uf
"""


def replicar_uf_na_transacao(conn) -> int:
    """Replica a UF efetiva da árvore para as delegacias vinculadas; devolve quantas mudaram."""
    with conn.cursor() as cur:
        cur.execute(_HERDAR_UF_SQL)
        return cur.rowcount


def uf_efetiva_da_unidade(cur, id_unidade: int) -> str | None:
    """UF da própria unidade ou do ancestral mais próximo que a tenha (None em cadeia sem UF).

    Usado pelo CRUD de delegacia: com unidade escolhida, a UF da delegacia passa a ser a herdada —
    o campo digitado à mão só sobrevive em delegacia sem unidade."""
    cur.execute(
        """
        WITH RECURSIVE cadeia(id_unidade, uf, id_pai, profundidade) AS (
            SELECT u.id_unidade, u.uf, u.id_unidade_pai, 0
            FROM seguranca.unidades u
            WHERE u.id_unidade = %s
            UNION ALL
            SELECT p.id_unidade, p.uf, p.id_unidade_pai, c.profundidade + 1
            FROM cadeia c
            JOIN seguranca.unidades p ON p.id_unidade = c.id_pai
            WHERE c.profundidade < 20
        )
        SELECT uf FROM cadeia WHERE uf IS NOT NULL ORDER BY profundidade LIMIT 1
        """,
        (id_unidade,),
    )
    linha = cur.fetchone()
    return linha[0] if linha else None


def replicar_uf_da_arvore() -> int:
    """Versão com conexão própria (chamadas avulsas fora de uma transação de CRUD)."""
    with get_connection() as conn:
        mudaram = replicar_uf_na_transacao(conn)
        conn.commit()
    return mudaram
