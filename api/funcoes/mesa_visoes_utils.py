"""Visões salvas da Mesa de Inquérito — PRIVADAS por usuário.

Um recorte do quadro é caro de montar: filtros de tipo/natureza, grau mínimo, período, fontes
marcadas (recorte no servidor), teto de nós e o enquadramento da câmera. O investigador ajusta
tudo isso e quer voltar àquele estado num clique — ou abrir direto por um link (?visao=). É o que
esta tabela guarda: o estado serializado (JT_ESTADO, JSONB) amarrado ao par (ID_INQUERITO,
ID_USUARIO).

Privacidade é o ponto. A linha é DONO do usuário (ID_USUARIO NOT NULL) e TODA leitura/deleção é
filtrada por ele: cada investigador vê e mexe só nas suas visões, nunca nas dos colegas — mesmo
tendo acesso ao mesmo inquérito. Guardar o estado como JSONB opaco (a tela é a única que entende o
formato) evita migration a cada novo filtro que a mesa ganha.
"""

import json

from funcoes.db_utils import get_connection

# Nome curto, mas com folga: dá para nomear "hubs do núcleo financeiro, jan-mar". O UNIQUE é por
# (inquérito, usuário, nome), então "salvar de novo" com o mesmo nome SUBSTITUI a visão (upsert).
NOME_MAXIMO = 120


def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_visoes(id_inquerito: int, id_usuario: int) -> list[dict]:
    """As visões do usuário naquele inquérito (sem o estado, que é pesado) — a lista do dropdown.
    Filtrada por ID_USUARIO: é isto que torna a visão privada. Não devolve as de outro usuário."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_visao, no_visao, dt_criacao, dt_atualizacao
                FROM investigacao.ent_tb10_mesa_visoes
                WHERE id_inquerito = %s AND id_usuario = %s
                ORDER BY no_visao
                """,
                (id_inquerito, id_usuario),
            )
            return _linhas(cur)


def obter_visao(id_visao: int, id_usuario: int) -> dict | None:
    """A visão com o estado serializado — só se for do próprio usuário (ID_USUARIO na WHERE é a
    guarda de privacidade: pedir a visão de outra pessoa devolve None, cai em 404 na rota)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_visao, no_visao, jt_estado
                FROM investigacao.ent_tb10_mesa_visoes
                WHERE id_visao = %s AND id_usuario = %s
                """,
                (id_visao, id_usuario),
            )
            linha = cur.fetchone()
    if linha is None:
        return None
    estado = linha[2]
    # jt_estado chega dict (psycopg decodifica JSONB); por robustez, aceita string também.
    if isinstance(estado, str):
        try:
            estado = json.loads(estado)
        except (ValueError, TypeError):
            estado = {}
    return {"id_visao": linha[0], "no_visao": linha[1], "estado": estado}


def salvar_visao(id_inquerito: int, id_usuario: int, nome: str, estado: dict) -> int:
    """Cria ou substitui a visão do usuário com aquele nome (upsert pelo UNIQUE). Devolve o id.
    Levanta ValueError em nome vazio/longo demais ou estado não serializável."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Dê um nome à visão.")
    if len(nome) > NOME_MAXIMO:
        raise ValueError(f"Nome da visão muito longo (máximo {NOME_MAXIMO} caracteres).")
    if not isinstance(estado, dict):
        raise ValueError("Estado da visão inválido.")
    try:
        serializado = json.dumps(estado, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Não foi possível guardar o estado da visão.") from exc
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb10_mesa_visoes
                    (id_inquerito, id_usuario, no_visao, jt_estado)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (id_inquerito, id_usuario, no_visao)
                    DO UPDATE SET jt_estado = EXCLUDED.jt_estado, dt_atualizacao = now()
                RETURNING id_visao
                """,
                (id_inquerito, id_usuario, nome, serializado),
            )
            id_visao = cur.fetchone()[0]
        conn.commit()
    return id_visao


def apagar_visao(id_visao: int, id_usuario: int) -> bool:
    """Apaga a visão — mas só se for do próprio usuário. Devolve True se apagou, False se não
    havia (não existe ou é de outra pessoa: a tela não distingue os dois, de propósito)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.ent_tb10_mesa_visoes WHERE id_visao = %s AND id_usuario = %s",
                (id_visao, id_usuario),
            )
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou
