"""Prompts que guiam a busca automática de entidades pela IA — biblioteca global mantida
pelo administrador na página "Prompts de entidade" (ver funcoes/busca_automatica_utils.py,
que consome listar_prompts_ativos ao rodar a busca na Mesa de Inquérito, e
funcoes/documentos_utils.py, que consome listar_prompts_por_ids na busca em documentos).

TP_ESCOPO separa as duas pipelines: um prompt escrito pra "Procure nas mensagens de
WhatsApp/SMS..." não faz sentido rodando sobre um PDF, e vice-versa — ver 21_entidades_padroes.sql."""

from funcoes.db_utils import get_connection

ESCOPOS_VALIDOS = {"investigacao", "documento"}


def _validar_escopo(tp_escopo: str) -> str:
    tp_escopo = (tp_escopo or "").strip()
    if tp_escopo not in ESCOPOS_VALIDOS:
        raise ValueError(f"Escopo inválido: {tp_escopo!r} (use 'investigacao' ou 'documento').")
    return tp_escopo


def criar_prompt(titulo: str, texto: str, tp_escopo: str, id_usuario: int | None = None) -> int:
    """Cria um prompt; levanta ValueError se título/texto vierem vazios ou o escopo for inválido."""
    titulo = (titulo or "").strip()
    texto = (texto or "").strip()
    if not titulo or not texto:
        raise ValueError("Título e texto do prompt são obrigatórios.")
    tp_escopo = _validar_escopo(tp_escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb02_prompts_busca (no_titulo, tx_prompt, tp_escopo, id_usuario_criador)
                VALUES (%s, %s, %s, %s)
                RETURNING id_prompt
                """,
                (titulo, texto, tp_escopo, id_usuario),
            )
            id_prompt = cur.fetchone()[0]
        conn.commit()
    return id_prompt


def listar_prompts() -> list[dict]:
    """Lista todos os prompts (ativos e inativos, os dois escopos), mais recente primeiro."""
    query = """
        SELECT p.id_prompt, p.no_titulo, p.tx_prompt, p.tp_escopo, p.in_ativo, p.dt_cadastro, u.no_usuario AS criado_por
        FROM investigacao.ent_tb02_prompts_busca p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario_criador
        ORDER BY p.dt_cadastro DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_prompts_ativos(tp_escopo: str) -> list[dict]:
    """Só os prompts ativos de um escopo, com título e texto — é o que a busca automática de
    fato usa (Mesa de Inquérito passa 'investigacao')."""
    tp_escopo = _validar_escopo(tp_escopo)
    query = """
        SELECT id_prompt, no_titulo, tx_prompt
        FROM investigacao.ent_tb02_prompts_busca
        WHERE in_ativo AND tp_escopo = %s
        ORDER BY dt_cadastro
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (tp_escopo,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_prompts_por_ids(ids: list[int], tp_escopo: str) -> list[dict]:
    """Prompts específicos por ID (ativos ou não), restritos a um escopo — usado pela busca em
    documentos diversos, que deixa o usuário escolher quais prompts rodar em vez de sempre usar
    todos os ativos. Um id de escopo errado (ex.: prompt de inquérito escolhido por engano numa
    chamada direta à API) é ignorado, não vira erro."""
    tp_escopo = _validar_escopo(tp_escopo)
    if not ids:
        return []
    query = """
        SELECT id_prompt, no_titulo, tx_prompt
        FROM investigacao.ent_tb02_prompts_busca
        WHERE id_prompt = ANY(%s) AND tp_escopo = %s
        ORDER BY dt_cadastro
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (ids, tp_escopo))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def atualizar_prompt(id_prompt: int, titulo: str, texto: str, tp_escopo: str) -> bool:
    """Atualiza título/texto/escopo de um prompt; devolve False se o id não existir."""
    titulo = (titulo or "").strip()
    texto = (texto or "").strip()
    if not titulo or not texto:
        raise ValueError("Título e texto do prompt são obrigatórios.")
    tp_escopo = _validar_escopo(tp_escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb02_prompts_busca SET no_titulo = %s, tx_prompt = %s, tp_escopo = %s WHERE id_prompt = %s",
                (titulo, texto, tp_escopo, id_prompt),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def definir_ativo(id_prompt: int, ativo: bool) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb02_prompts_busca SET in_ativo = %s WHERE id_prompt = %s",
                (ativo, id_prompt),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_prompt(id_prompt: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.ent_tb02_prompts_busca WHERE id_prompt = %s", (id_prompt,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido
