"""CRUD da árvore de unidades superiores (SEGURANCA.UNIDADES — ver docs/unidades_prompts.md).

Toda a tela é administração: lista plana com ID_UNIDADE_PAI (a árvore é montada no front, meia
dúzia de nós) e as checagens de sanidade ficam aqui — a árvore só é desenhada por este CRUD,
então é aqui (e não com CONSTRAINT no banco) que ciclo e pai inexistente são barrados.
"""

from fastapi import APIRouter, Depends, HTTPException
from psycopg import errors as psycopg_errors
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin
from funcoes.db_utils import get_connection
from funcoes.unidades_utils import replicar_uf_na_transacao

router_admin = APIRouter(dependencies=[Depends(exigir_admin)])

NIVEIS_VALIDOS = ("uf", "coordenacao")


class UnidadeIn(BaseModel):
    no_unidade: str = Field(..., min_length=1, max_length=300)
    sg_unidade: str | None = Field(default=None, max_length=30)
    tp_nivel: str = Field("coordenacao", max_length=20)
    id_unidade_pai: int | None = None
    uf: str | None = Field(default=None, max_length=2)


def _limpar(payload: UnidadeIn) -> tuple[str, str | None, str, int | None, str | None]:
    """Valida e normaliza o payload: nome em maiúsculas, sigla/UF aparadas, nível checado."""
    nome = payload.no_unidade.strip().upper()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome da unidade é obrigatório.")
    if payload.tp_nivel not in NIVEIS_VALIDOS:
        raise HTTPException(status_code=400, detail="Nível deve ser 'uf' ou 'coordenacao'.")
    sigla = (payload.sg_unidade or "").strip().upper() or None
    uf = (payload.uf or "").strip().upper() or None
    if uf and (len(uf) != 2 or not uf.isalpha()):
        raise HTTPException(status_code=400, detail="UF deve ter 2 letras (ex.: SP).")
    return nome, sigla, payload.tp_nivel, payload.id_unidade_pai, uf


def _existe(cur, id_unidade: int) -> bool:
    cur.execute("SELECT 1 FROM seguranca.unidades WHERE id_unidade = %s", (id_unidade,))
    return cur.fetchone() is not None


def _descendente_de(cur, id_candidate: int, id_ancestral: int) -> bool:
    """id_candidate está no sub-árvore de id_ancestral? É o teste que impede o PUT de pendurar o
    pai num filho (ciclo). CTE com teto de profundidade — a mesma defesa do ia_prompt_niveis."""
    cur.execute(
        """
        WITH RECURSIVE sub(id_unidade, profundidade) AS (
            SELECT id_unidade, 0 FROM seguranca.unidades WHERE id_unidade = %s
            UNION ALL
            SELECT u.id_unidade, s.profundidade + 1
            FROM seguranca.unidades u JOIN sub s ON u.id_unidade_pai = s.id_unidade
            WHERE s.profundidade < 20
        )
        SELECT 1 FROM sub WHERE id_unidade = %s LIMIT 1
        """,
        (id_ancestral, id_candidate),
    )
    return cur.fetchone() is not None


def _conferir_pai(cur, id_unidade: int | None, pai: int | None) -> None:
    if pai is None:
        return
    if not _existe(cur, pai):
        raise HTTPException(status_code=400, detail="Unidade pai não encontrada.")
    if id_unidade is not None and (pai == id_unidade or _descendente_de(cur, pai, id_unidade)):
        raise HTTPException(
            status_code=400,
            detail="A unidade escolhida como pai é a própria ou uma descendente — a hierarquia "
                   "não pode ter ciclo.",
        )


@router_admin.get("/api/unidades")
def listar():
    """Lista plana com o pai — o front (js/unidades ou Gerenciar delegacias) monta a árvore."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # qt_delegacias conta a sub-árvore inteira (a própria unidade + descendentes): na
            # tela, "SR/PF-ES: 12 delegacias" com DIP embaixo deve bater com o que o admin
            # espera — senão o pai parece vazio enquanto as filhas concentram tudo.
            cur.execute(
                """
                WITH RECURSIVE sub(id_unidade, id_raiz, profundidade) AS (
                    SELECT u.id_unidade, u.id_unidade, 0 FROM seguranca.unidades u
                    UNION ALL
                    SELECT c.id_unidade, s.id_raiz, s.profundidade + 1
                    FROM sub s
                    JOIN seguranca.unidades c ON c.id_unidade_pai = s.id_unidade
                    WHERE s.profundidade < 20
                ), contagem AS (
                    SELECT s.id_raiz, count(d.id_delegacia) AS qt
                    FROM sub s
                    LEFT JOIN seguranca.delegacias d ON d.id_unidade = s.id_unidade
                    GROUP BY s.id_raiz
                )
                SELECT u.id_unidade, u.no_unidade, u.sg_unidade, u.tp_nivel, u.id_unidade_pai,
                       u.uf, u.dt_criacao, COALESCE(c.qt, 0) AS qt_delegacias
                FROM seguranca.unidades u
                LEFT JOIN contagem c ON c.id_raiz = u.id_unidade
                ORDER BY u.no_unidade
                """
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


@router_admin.post("/api/unidades")
def criar(payload: UnidadeIn):
    nome, sigla, nivel, pai, uf = _limpar(payload)
    with get_connection() as conn:
        with conn.cursor() as cur:
            _conferir_pai(cur, None, pai)
            try:
                cur.execute(
                    "INSERT INTO seguranca.unidades (no_unidade, sg_unidade, tp_nivel, "
                    "id_unidade_pai, uf) VALUES (%s, %s, %s, %s, %s) RETURNING id_unidade",
                    (nome, sigla, nivel, pai, uf),
                )
                id_unidade = cur.fetchone()[0]
            except psycopg_errors.UniqueViolation:
                raise HTTPException(status_code=409, detail="Unidade já cadastrada.")
            # UF da árvore vale para as delegacias (criar unidade com UF já herda quem nela entrar)
            replicar_uf_na_transacao(conn)
        conn.commit()
    return {"ok": True, "id_unidade": id_unidade}


@router_admin.put("/api/unidades/{id_unidade}")
def atualizar(id_unidade: int, payload: UnidadeIn):
    nome, sigla, nivel, pai, uf = _limpar(payload)
    with get_connection() as conn:
        with conn.cursor() as cur:
            _conferir_pai(cur, id_unidade, pai)
            try:
                cur.execute(
                    "UPDATE seguranca.unidades SET no_unidade = %s, sg_unidade = %s, "
                    "tp_nivel = %s, id_unidade_pai = %s, uf = %s WHERE id_unidade = %s",
                    (nome, sigla, nivel, pai, uf, id_unidade),
                )
            except psycopg_errors.UniqueViolation:
                raise HTTPException(status_code=409, detail="Unidade já cadastrada.")
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Unidade não encontrada.")
            # mudou a árvore (UF, pai): as delegacias de todo o sub-árvore acompanharam junto
            replicar_uf_na_transacao(conn)
        conn.commit()
    return {"ok": True}


@router_admin.delete("/api/unidades/{id_unidade}")
def excluir(id_unidade: int):
    """Sem cascata escondida: unidade com filhos ou delegacias vinculadas não sai (409) — apagar
    a árvore inteira por engano apagaria o contexto de prompt de meia superintendência. O prompt
    da própria unidade (se houver) vai junto: sem dona, não serve para nada."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM seguranca.unidades WHERE id_unidade_pai = %s LIMIT 1",
                (id_unidade,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Esta unidade tem unidades abaixo dela — remova ou realoque as "
                           "descendentes primeiro.",
                )
            cur.execute(
                "SELECT 1 FROM seguranca.delegacias WHERE id_unidade = %s LIMIT 1",
                (id_unidade,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Há delegacias vinculadas a esta unidade — desvincule-as na tela "
                           "Gerenciar delegacias primeiro.",
                )
            cur.execute("DELETE FROM seguranca.prompts_ia WHERE tp_nivel = 'unidade' AND id_entidade = %s",
                        (id_unidade,))
            cur.execute(
                "DELETE FROM seguranca.config_ia_entidade WHERE tp_nivel = 'unidade' AND id_entidade = %s",
                (id_unidade,),
            )
            cur.execute("DELETE FROM seguranca.unidades WHERE id_unidade = %s", (id_unidade,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Unidade não encontrada.")
            # a unidade pode ter sido o ancestral que emprestava a UF a delegacias de cima dela
            replicar_uf_na_transacao(conn)
        conn.commit()
    return {"ok": True}
