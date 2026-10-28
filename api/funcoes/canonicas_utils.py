"""Entidades canônicas: a "coisa" investigada (pessoa, telefone, local, veículo,
organização), separada dos REGISTROS extraídos que a evidenciam (ENT_TB05..08 —
ver db/init/21_entidades_padroes.sql e db/init/21_entidades_padroes.sql).

Quem POVOA essa camada automaticamente é funcoes/identificacao_pessoas.py, na
ingestão: cruza contatos, agenda, ligações, chaves PIX, contas bancárias e contas
de aplicativo e materializa as pessoas identificadas, com os identificadores
(telefone/CPF/e-mail) em ENT_TB08 e cada registro de origem como evidência. Aqui
ficam só as operações da mesa sobre essa camada (criar à mão, listar, relacionar,
apagar).
"""

import logging
import threading
import time

from funcoes.db_utils import get_connection, nova_conexao_dedicada
from funcoes.entidades_utils import _VERIFICACAO_ORIGEM
from funcoes.imagens_mesa import apagar_imagem_no, transferir_imagem
from funcoes.investigacao_utils import _filtro_acesso_sql

logger = logging.getLogger(__name__)

TIPOS_CANONICA_VALIDOS = {"pessoa", "telefone", "local", "veiculo", "organizacao", "outro"}


def criar_canonica(
    id_inquerito: int, tp_entidade: str, nome: str, chave: str | None = None,
    observacao: str | None = None, id_usuario: int | None = None, automatico: bool = False,
) -> int:
    """Cria uma entidade canônica; devolve o ID. Levanta ValueError em tipo/nome inválido."""
    if tp_entidade not in TIPOS_CANONICA_VALIDOS:
        raise ValueError(f"Tipo de entidade canônica inválido: {tp_entidade!r}")
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome da entidade não pode ser vazio.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb05_canonicas
                    (id_inquerito, tp_entidade, no_entidade, nu_chave, tx_observacao, in_automatico, id_usuario_criador)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id_canonica
                """,
                (id_inquerito, tp_entidade, nome, chave, observacao, automatico, id_usuario),
            )
            id_canonica = cur.fetchone()[0]
        conn.commit()
    return id_canonica


def listar_canonicas(
    id_inquerito: int,
    tipos: list[str] | None = None,
    busca: str | None = None,
    min_evidencias: int = 0,
    limite: int | None = None,
    fontes: dict[str, list[int]] | None = None,
) -> list[dict]:
    """Canônicas de um inquérito, com a contagem de evidências de cada uma. Os
    identificadores (ENT_TB08) não vêm aqui de propósito: o dono do aparelho junta
    dezenas deles, e o que interessa na tela já está resumido em TX_OBSERVACAO.

    Filtros server-side (para inquéritos com milhares de nós — ex. SIMBA/RIF):
    ``tipos`` restringe TP_ENTIDADE, ``busca`` faz ILIKE em nome/chave/observação,
    ``min_evidencias`` corta a cauda longa, ``limite`` impõe teto ordenado por criação.
    A contagem usa JOIN + GROUP BY (uma passada) em vez de subselect correlacionado
    por linha (N+1).

    ``fontes`` ({tp: [ids]}, painel "🗂 Fontes" da mesa — ver fontes_mesa_utils) mantém
    só as canônicas com evidência numa das fontes marcadas; as sem evidência alguma
    (criadas à mão) ficam sempre."""
    tipos = [t for t in (tipos or []) if t]
    conds = ["c.id_inquerito = %s"]
    params: list = [id_inquerito]
    if tipos:
        conds.append("c.tp_entidade = ANY(%s)")
        params.append(tipos)
    if busca:
        conds.append("(c.no_entidade ILIKE %s OR c.nu_chave ILIKE %s OR c.tx_observacao ILIKE %s)")
        like = "%" + str(busca).strip().replace("%", "").replace("_", "") + "%"
        params.extend([like, like, like])
    if fontes:
        # Painel "🗂 Fontes" da mesa (ver fontes_mesa_utils): fica a canônica se alguma
        # evidência dela nasceu numa fonte marcada — ou se ela não tem evidência alguma
        # (criada à mão): o que o investigador desenhou à mão nunca some do recorte.
        from funcoes.fontes_mesa_utils import fragmento_fontes  # local: evita import circular
        frag = fragmento_fontes(fontes, "x")
        if frag:
            conds.append(
                "(NOT EXISTS (SELECT 1 FROM investigacao.ent_tb06_canonica_evidencias x0"
                " WHERE x0.id_canonica = c.id_canonica)"
                " OR EXISTS (SELECT 1 FROM investigacao.ent_tb06_canonica_evidencias x"
                " WHERE x.id_canonica = c.id_canonica AND " + frag[0] + "))"
            )
            params.extend(frag[1])
    where = "WHERE " + " AND ".join(conds)
    having = "HAVING count(ev.id_evidencia) >= %s" if min_evidencias else ""
    if min_evidencias:
        params.append(int(min_evidencias))
    lim = "LIMIT %s" if limite else ""
    if limite:
        params.append(int(limite))
    query = f"""
        SELECT c.id_canonica, c.tp_entidade, c.no_entidade, c.nu_chave, c.tx_observacao,
               c.in_automatico, c.dt_criacao, c.nu_pos_x, c.nu_pos_y, u.no_usuario AS criado_por,
               count(ev.id_evidencia) AS qt_evidencias
        FROM investigacao.ent_tb05_canonicas c
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = c.id_usuario_criador
        LEFT JOIN investigacao.ent_tb06_canonica_evidencias ev ON ev.id_canonica = c.id_canonica
        {where}
        GROUP BY c.id_canonica, c.tp_entidade, c.no_entidade, c.nu_chave, c.tx_observacao,
                 c.in_automatico, c.dt_criacao, c.nu_pos_x, c.nu_pos_y, u.no_usuario
        {having}
        ORDER BY c.dt_criacao
        {lim}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def resumo_grafo(id_inquerito: int) -> dict:
    """Resumo leve do grafo (para a mesa decidir o recorte antes de baixar tudo).

    Conta nós por tipo/camada, arestas por camada e monta o histograma de grau
    a partir só dos ids das arestas (sem rótulos, sem 60 JOINs). É o que permite
    um inquérito de 6k nós abrir rápido: resumo primeiro, recorte depois."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tp_entidade, count(*) FROM investigacao.ent_tb05_canonicas"
                " WHERE id_inquerito = %s GROUP BY tp_entidade",
                (id_inquerito,),
            )
            canonicas_por_tipo = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(
                "SELECT tp_origem, cd_revisao, count(*) FROM investigacao.ent_tb01_entidades"
                " WHERE id_inquerito = %s GROUP BY tp_origem, cd_revisao",
                (id_inquerito,),
            )
            entidades_por_tipo: dict = {}
            entidades_por_revisao: dict = {}
            for tp, rev, qt in cur.fetchall():
                entidades_por_tipo[tp] = entidades_por_tipo.get(tp, 0) + int(qt)
                entidades_por_revisao[rev] = entidades_por_revisao.get(rev, 0) + int(qt)
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb03_relacoes WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            qt_rel = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb07_canonica_relacoes WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            qt_rel_canon = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb06_canonica_evidencias ev"
                " JOIN investigacao.ent_tb05_canonicas c ON c.id_canonica = ev.id_canonica"
                " WHERE c.id_inquerito = %s",
                (id_inquerito,),
            )
            qt_evid = int(cur.fetchone()[0])
            # grau por nó (só ids — barato): relações entidade↔entidade e canônica↔canônica
            cur.execute(
                "SELECT id_entidade_origem, id_entidade_destino FROM investigacao.ent_tb03_relacoes"
                " WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            grau_e: dict = {}
            for a, b in cur.fetchall():
                grau_e["e" + str(a)] = grau_e["e" + str(a)] + 1 if "e" + str(a) in grau_e else 1
                grau_e["e" + str(b)] = grau_e["e" + str(b)] + 1 if "e" + str(b) in grau_e else 1
            cur.execute(
                "SELECT id_canonica_origem, id_canonica_destino FROM investigacao.ent_tb07_canonica_relacoes"
                " WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            grau_c: dict = {}
            for a, b in cur.fetchall():
                grau_c["c" + str(a)] = grau_c["c" + str(a)] + 1 if "c" + str(a) in grau_c else 1
                grau_c["c" + str(b)] = grau_c["c" + str(b)] + 1 if "c" + str(b) in grau_c else 1
            # camada de documentos (estimativa barata, sem deduplicar): quantos documentos
            # concluídos e quantas citações brutas de pessoas/achados há neles — é o que diz
            # se o caso é "de documentos" antes de baixar o grafo inteiro.
            cur.execute(
                "SELECT count(*),"
                " COALESCE(sum(CASE WHEN jsonb_typeof(jq_pessoas) = 'array'"
                " THEN jsonb_array_length(jq_pessoas) ELSE 0 END), 0),"
                " COALESCE(sum(CASE WHEN jsonb_typeof(jq_achados) = 'array'"
                " THEN jsonb_array_length(jq_achados) ELSE 0 END), 0)"
                " FROM investigacao.inq_tb04_documentos"
                " WHERE id_inquerito = %s AND cd_analise = 'concluido'"
                " AND (jq_pessoas IS NOT NULL OR jq_achados IS NOT NULL)",
                (id_inquerito,),
            )
            qt_docs, qt_pes_bruto, qt_ach_bruto = cur.fetchone()
    graus = list(grau_e.values()) + list(grau_c.values())
    graus.sort(reverse=True)
    total_nos = sum(canonicas_por_tipo.values()) + sum(entidades_por_tipo.values())
    hist = {"1": 0, "2_3": 0, "4_10": 0, "11_50": 0, "51mais": 0}
    for g in graus:
        if g <= 1:
            hist["1"] += 1
        elif g <= 3:
            hist["2_3"] += 1
        elif g <= 10:
            hist["4_10"] += 1
        elif g <= 50:
            hist["11_50"] += 1
        else:
            hist["51mais"] += 1
    return {
        "total_nos": total_nos,
        "total_arestas": qt_rel + qt_rel_canon,
        "canonicas_por_tipo": canonicas_por_tipo,
        "entidades_por_tipo": entidades_por_tipo,
        "entidades_por_revisao": entidades_por_revisao,
        "qt_relacoes": qt_rel,
        "qt_relacoes_canonicas": qt_rel_canon,
        "qt_evidencias": qt_evid,
        "hist_grau": hist,
        "top_graus": graus[:20],
        "grafo_grande": total_nos >= 1000,
        "qt_documentos": int(qt_docs or 0),
        "doc_pessoas_bruto": int(qt_pes_bruto or 0),
        "doc_achados_bruto": int(qt_ach_bruto or 0),
        "total_nos_doc_estimado": int(qt_docs or 0) + int(qt_pes_bruto or 0) + int(qt_ach_bruto or 0),
    }


def listar_relacoes_canonicas(id_inquerito: int) -> list[dict]:
    """Arestas canônica ↔ canônica de um inquérito."""
    query = """
        SELECT r.id_relacao, r.id_canonica_origem, r.id_canonica_destino, r.tx_tipo_relacao,
               r.in_automatico, r.dt_criacao, r.tx_detalhe, u.no_usuario AS criado_por
        FROM investigacao.ent_tb07_canonica_relacoes r
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = r.id_usuario_criador
        WHERE r.id_inquerito = %s
        ORDER BY r.dt_criacao
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_vinculos_evidencia(id_inquerito: int) -> list[dict]:
    """Arestas canônica → entidade da mesa: uma evidência da canônica aponta o MESMO
    registro (tp_origem/id_origem) que uma entidade-comentário do inquérito marca."""
    query = """
        SELECT DISTINCT ev.id_canonica, e.id_entidade, ev.tx_detalhe
        FROM investigacao.ent_tb06_canonica_evidencias ev
        JOIN investigacao.ent_tb05_canonicas c ON c.id_canonica = ev.id_canonica
        JOIN investigacao.ent_tb01_entidades e
          ON e.tp_origem = ev.tp_origem AND e.id_origem = ev.id_origem
         AND e.id_inquerito = c.id_inquerito
        WHERE c.id_inquerito = %s AND e.cd_revisao <> 'descartada'
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_evidencias(id_canonica: int) -> list[dict]:
    """Evidências (registros de origem) de uma canônica, para o modal de detalhes."""
    query = """
        SELECT ev.id_evidencia, ev.tp_origem, ev.id_origem, ev.tx_detalhe, ev.dt_criacao
        FROM investigacao.ent_tb06_canonica_evidencias ev
        WHERE ev.id_canonica = %s
        ORDER BY ev.tp_origem, ev.id_origem
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_canonica,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def obter_id_inquerito_de_canonica(id_canonica: int) -> int | None:
    """Resolve o id_inquerito dono de uma canônica, para checar permissão. None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.ent_tb05_canonicas WHERE id_canonica = %s", (id_canonica,))
            row = cur.fetchone()
            return row[0] if row else None


def remover_canonica(id_canonica: int) -> bool:
    """Remove uma canônica (evidências e relações caem junto via ON DELETE CASCADE; a foto do
    nó sai em cascata manual — ENT_TB09 aponta o nó por TP_NO/ID_NO, sem FK)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            apagar_imagem_no(cur, "canonica", id_canonica)
            cur.execute("DELETE FROM investigacao.ent_tb05_canonicas WHERE id_canonica = %s", (id_canonica,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def atualizar_posicao_canonica(id_canonica: int, pos_x: float | None, pos_y: float | None) -> bool:
    """Fixa (ou limpa, com None) a posição de uma canônica arrastada na mesa — gêmea de
    entidades_utils.atualizar_posicao, mas para ENT_TB05 em vez de ENT_TB01."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb05_canonicas SET nu_pos_x = %s, nu_pos_y = %s WHERE id_canonica = %s",
                (pos_x, pos_y, id_canonica),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def fundir_canonicas(id_canonica_origem: int, id_canonica_destino: int, id_inquerito: int) -> dict:
    """Funde ORIGEM em DESTINO: reatribui evidências (ENT_TB06), identificadores (ENT_TB08) e
    relações (ENT_TB07) de ORIGEM para DESTINO, concatena a observação de ORIGEM em DESTINO se
    este não tiver uma própria, passa a foto do nó (ENT_TB09) se o destino não tiver a sua, e
    apaga ORIGEM. TP_ORIGEM/ID_ORIGEM de cada evidência ficam
    intactos — a proveniência já mora no registro, só a FK de dono muda. Só funde canônicas do
    mesmo TP_ENTIDADE (telefone com telefone, pessoa com pessoa): fundir tipos diferentes não
    faz sentido investigativo. Levanta ValueError se origem/destino forem iguais, não existirem
    neste inquérito, ou tiverem tipos diferentes."""
    if id_canonica_origem == id_canonica_destino:
        raise ValueError("Não é possível fundir uma entidade com ela mesma.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_canonica, tp_entidade FROM investigacao.ent_tb05_canonicas "
                "WHERE id_canonica IN (%s, %s) AND id_inquerito = %s",
                (id_canonica_origem, id_canonica_destino, id_inquerito),
            )
            tipos = {row[0]: row[1] for row in cur.fetchall()}
            if id_canonica_origem not in tipos or id_canonica_destino not in tipos:
                raise ValueError("Uma das entidades canônicas não existe neste inquérito.")
            if tipos[id_canonica_origem] != tipos[id_canonica_destino]:
                raise ValueError("Só é possível fundir entidades canônicas do mesmo tipo.")

            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb06_canonica_evidencias WHERE id_canonica = %s",
                (id_canonica_origem,),
            )
            qt_evidencias = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb08_canonica_identificadores WHERE id_canonica = %s",
                (id_canonica_origem,),
            )
            qt_identificadores = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb07_canonica_relacoes "
                "WHERE id_canonica_origem = %s OR id_canonica_destino = %s",
                (id_canonica_origem, id_canonica_origem),
            )
            qt_relacoes = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO investigacao.ent_tb06_canonica_evidencias (id_canonica, tp_origem, id_origem, tx_detalhe)
                SELECT %s, tp_origem, id_origem, tx_detalhe
                FROM investigacao.ent_tb06_canonica_evidencias WHERE id_canonica = %s
                ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
                """,
                (id_canonica_destino, id_canonica_origem),
            )
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb08_canonica_identificadores
                    (id_canonica, id_inquerito, tp_identificador, co_valor, tx_origem)
                SELECT %s, id_inquerito, tp_identificador, co_valor, tx_origem
                FROM investigacao.ent_tb08_canonica_identificadores WHERE id_canonica = %s
                ON CONFLICT (id_canonica, tp_identificador, co_valor) DO NOTHING
                """,
                (id_canonica_destino, id_canonica_origem),
            )
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb07_canonica_relacoes
                    (id_inquerito, id_canonica_origem, id_canonica_destino, tx_tipo_relacao,
                     in_automatico, id_usuario_criador, tx_detalhe)
                SELECT id_inquerito, %(destino)s, id_canonica_destino, tx_tipo_relacao,
                       in_automatico, id_usuario_criador, tx_detalhe
                FROM investigacao.ent_tb07_canonica_relacoes
                WHERE id_canonica_origem = %(origem)s AND id_canonica_destino <> %(destino)s
                ON CONFLICT (id_canonica_origem, id_canonica_destino, tx_tipo_relacao) DO NOTHING
                """,
                {"origem": id_canonica_origem, "destino": id_canonica_destino},
            )
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb07_canonica_relacoes
                    (id_inquerito, id_canonica_origem, id_canonica_destino, tx_tipo_relacao,
                     in_automatico, id_usuario_criador, tx_detalhe)
                SELECT id_inquerito, id_canonica_origem, %(destino)s, tx_tipo_relacao,
                       in_automatico, id_usuario_criador, tx_detalhe
                FROM investigacao.ent_tb07_canonica_relacoes
                WHERE id_canonica_destino = %(origem)s AND id_canonica_origem <> %(destino)s
                ON CONFLICT (id_canonica_origem, id_canonica_destino, tx_tipo_relacao) DO NOTHING
                """,
                {"origem": id_canonica_origem, "destino": id_canonica_destino},
            )

            cur.execute(
                """
                UPDATE investigacao.ent_tb05_canonicas dest
                SET tx_observacao = coalesce(dest.tx_observacao, orig.tx_observacao)
                FROM investigacao.ent_tb05_canonicas orig
                WHERE dest.id_canonica = %s AND orig.id_canonica = %s
                """,
                (id_canonica_destino, id_canonica_origem),
            )

            transferir_imagem(cur, id_canonica_origem, id_canonica_destino)
            cur.execute("DELETE FROM investigacao.ent_tb05_canonicas WHERE id_canonica = %s", (id_canonica_origem,))
        conn.commit()
    return {"evidencias": qt_evidencias, "identificadores": qt_identificadores, "relacoes": qt_relacoes}


def criar_relacao_canonica(
    id_inquerito: int, id_origem: int, id_destino: int, tipo_relacao: str,
    id_usuario: int | None = None, automatico: bool = False,
) -> int:
    """Cria uma aresta canônica ↔ canônica; devolve o ID (ou o da já existente, pela UNIQUE)."""
    tipo_relacao = (tipo_relacao or "").strip()
    if not tipo_relacao:
        raise ValueError("Tipo de relação não pode ser vazio.")
    if id_origem == id_destino:
        raise ValueError("Uma relação precisa de duas entidades diferentes.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb05_canonicas WHERE id_canonica IN (%s, %s) AND id_inquerito = %s",
                (id_origem, id_destino, id_inquerito),
            )
            if cur.fetchone()[0] != 2:
                raise ValueError("Uma das entidades canônicas não existe neste inquérito.")
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb07_canonica_relacoes
                    (id_inquerito, id_canonica_origem, id_canonica_destino, tx_tipo_relacao, in_automatico, id_usuario_criador)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_canonica_origem, id_canonica_destino, tx_tipo_relacao) DO NOTHING
                RETURNING id_relacao
                """,
                (id_inquerito, id_origem, id_destino, tipo_relacao, automatico, id_usuario),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    SELECT id_relacao FROM investigacao.ent_tb07_canonica_relacoes
                    WHERE id_canonica_origem = %s AND id_canonica_destino = %s AND tx_tipo_relacao = %s
                    """,
                    (id_origem, id_destino, tipo_relacao),
                )
                row = cur.fetchone()
        conn.commit()
    return row[0]


def remover_relacao_canonica(id_relacao: int) -> int | None:
    """Remove uma aresta canônica; devolve o id_inquerito dela (para log) ou None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.ent_tb07_canonica_relacoes WHERE id_relacao = %s RETURNING id_inquerito",
                (id_relacao,),
            )
            row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def obter_id_inquerito_de_relacao_canonica(id_relacao: int) -> int | None:
    """Resolve o id_inquerito de uma aresta canônica, para checar permissão."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.ent_tb07_canonica_relacoes WHERE id_relacao = %s", (id_relacao,)
            )
            row = cur.fetchone()
            return row[0] if row else None


def adicionar_evidencia(
    id_canonica: int, id_inquerito: int, tp_origem: str, id_origem: int, detalhe: str | None = None,
) -> bool:
    """Pendura um registro extraído como evidência de uma canônica (valida que o registro
    existe e pertence ao mesmo inquérito). Devolve False se a evidência já existia."""
    verificacao = _VERIFICACAO_ORIGEM.get(tp_origem)
    if verificacao is None:
        raise ValueError(f"Tipo de origem inválido: {tp_origem!r}")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(verificacao, {"id_origem": id_origem, "id_inquerito": id_inquerito})
            if cur.fetchone() is None:
                raise ValueError("Registro de origem não encontrado neste inquérito.")
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb06_canonica_evidencias (id_canonica, tp_origem, id_origem, tx_detalhe)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
                """,
                (id_canonica, tp_origem, id_origem, detalhe),
            )
            inserido = cur.rowcount > 0
        conn.commit()
    return inserido


_ROTULO_IDENTIFICADOR = {
    "cpf": "CPF",
    "cnpj": "CNPJ",
    "telefone": "Telefone",
    "email": "E-mail",
    "usuario_app": "Usuário de app",
    "nome": "Nome",
    "imei": "IMEI do aparelho",
    "iccid": "ICCID do chip",
    "imsi": "IMSI do chip",
    "serial": "Nº de série do aparelho",
    "mac": "MAC do aparelho",
    "placa": "Placa de veículo",
    "local": "Local em comum",
    "local_rede": "Ponto de rede em comum",
    "co_presenca": "Aparelho em comum por perto",
}

# sinais exatos (mesmo valor normalizado) vs. aproximados (parecido, pode ser coincidência —
# o investigador precisa checar antes de tratar como confirmado)
_SINAIS_EXATOS = {"cpf", "cnpj", "telefone", "email",
                  "imei", "iccid", "imsi", "serial", "mac", "placa"}

# Os identificadores que sustentam o sinal EXATO da convergência — a mesma lista nas três
# consultas (par a par, resumo por card e visão global), para não divergirem com o tempo.
#
# CNPJ e PLACA entraram quando a IPEI, o IRPF e a telefonia passaram a materializar empresa e
# veículo como nó (ver identificacao_pessoas.py e mesa_inquerito.md): a mesma empresa nos dois
# casos é sócio/empregador/fonte pagadora em comum, e o mesmo carro em dois casos é o vínculo
# mais concreto que existe depois do documento da pessoa.
_IDENTIFICADORES_CONVERGENCIA = ("cpf", "cnpj", "telefone", "email",
                                 "imei", "iccid", "imsi", "serial", "mac", "placa")
_SQL_IDENTIFICADORES_CONVERGENCIA = ", ".join(f"'{t}'" for t in _IDENTIFICADORES_CONVERGENCIA)

# CNPJ presente em inquéritos demais não é vínculo: é banco, operadora, prefeitura ou o grande
# empregador da região — a empresa que aparece na declaração de IRPF de meio mundo. Mesmo
# raciocínio (e mesma forma) do corte _MAX_INQUERITOS_MAC da co-presença, só que mais folgado:
# CNPJ é documental e esparso, e uma empresa de fachada compartilhada por quatro inquéritos
# relacionados é exatamente o desenho que se quer ver — cortar em 3 mataria o achado.
# Placa NÃO tem corte: carro não é infraestrutura pública, e o mesmo veículo em dois casos
# sempre merece ser olhado.
_MAX_INQUERITOS_CNPJ = 5


def _sql_cnpj_nao_frequente(alias: str) -> str:
    """Recorte do CNPJ ubíquo, para colar no WHERE de quem cruza ENT_TB08 consigo mesma.

    Subconsulta NÃO correlacionada de propósito: assim ela roda UMA vez e vira hash, em vez de
    um count() por linha candidata. A correlacionada (`... WHERE f.co_valor = e8.co_valor`) não
    tem índice que a sirva — IX99 começa por ID_INQUERITO —, então seria uma varredura de
    ENT_TB08 por linha. `co_valor` é NOT NULL, então o NOT IN é seguro."""
    return f"""
          AND ({alias}.tp_identificador <> 'cnpj' OR {alias}.co_valor NOT IN (
                SELECT f.co_valor
                FROM investigacao.ent_tb08_canonica_identificadores f
                WHERE f.tp_identificador = 'cnpj'
                GROUP BY f.co_valor
                HAVING count(DISTINCT f.id_inquerito) > {_MAX_INQUERITOS_CNPJ}))
    """

# Identificadores do APARELHO, gravados na canônica 'dispositivo' pela identificação de pessoas
# (ver identificacao_pessoas._materializar_dispositivos). O ICCID é o mais útil na prática: o
# chip migra de celular, então ele reencontra a mesma LINHA em outro caso, coisa que o IMEI
# (que é do aparelho) não pega.
_IDENTIFICADORES_APARELHO = ("imei", "iccid", "imsi", "serial", "mac")

# Co-presença: mesmo DISPOSITIVO reconhecido por perto (RecognizedDevice) em inquéritos
# diferentes — dois investigados que estiveram junto do mesmo aparelho Bluetooth/Wi-Fi, sem
# nunca terem trocado uma mensagem.
#
# O BSSID de roteador ficou de fora daqui de propósito, e a medição em produção mostra por quê:
# incluindo-o, dois inquéritos "convergiam" por 1.256 endereços em comum, e o primeiro exemplo
# era um hotspot de operadora (#CLARO-WIFI) — equipamento público que qualquer um que ande pela
# cidade enxerga. Pior: as duas fontes dividem o mesmo espaço de endereço (o roteador de um é
# "dispositivo reconhecido" para o outro), o que multiplicava os pares. A posição do roteador
# continua valendo, mas pelo sinal certo: ela entra em "local em comum", que é sobre lugar.
#
# Fica APROXIMADO mesmo assim: MAC aleatorizado existe (o próprio UFDR rotula alguns como
# "Randomized"), e equipamento público também aparece nessa fonte — daí o corte estatístico de
# _MAX_INQUERITOS_MAC, que descarta o endereço visto em inquéritos demais para ser encontro.
_MINIMO_MACS_CO_PRESENCA = 3
_MAX_INQUERITOS_MAC = 3

# nome: similaridade mínima por trigrama pra entrar como convergência (abaixo disso é
# ruído — nome curto/comum bate com qualquer coisa). Local: mínimo de registros de GPS
# na mesma coordenada arredondada (~11 m) pra não ser passagem única/coincidência de rota.
_SIMILARIDADE_MINIMA_NOME = 0.5
_MINIMO_PONTOS_LOCAL = 3
# coordenada de rede: mínimo próprio, mais alto — ver locais_rede na consulta global
_MINIMO_REDES_LOCAL = 10


def listar_convergencia_inquerito(id_inquerito: int, usuario: dict) -> list[dict]:
    """Identificadores canônicos deste inquérito que também aparecem em OUTROS
    inquéritos visíveis ao usuário. Devolve uma lista de ocorrências:
    {tp_identificador, co_valor, id_inquerito_relacionado, nu_inquerito_relacionado,
     no_entidade_origem, no_entidade_destino}."""
    filtro, params = _filtro_acesso_sql(usuario, "i2")
    query = f"""
        SELECT DISTINCT e8.tp_identificador, e8.co_valor,
               i2.id_inquerito AS id_inquerito_relacionado,
               i2.nu_inquerito AS nu_inquerito_relacionado,
               c1.no_entidade AS no_entidade_origem,
               c2.no_entidade AS no_entidade_destino
        FROM investigacao.ent_tb08_canonica_identificadores e8
        JOIN investigacao.ent_tb05_canonicas c1 ON c1.id_canonica = e8.id_canonica
        JOIN investigacao.ent_tb08_canonica_identificadores e8_outro
          ON e8_outro.co_valor = e8.co_valor
         AND e8_outro.tp_identificador = e8.tp_identificador
         AND e8_outro.id_inquerito <> e8.id_inquerito
        JOIN investigacao.ent_tb05_canonicas c2 ON c2.id_canonica = e8_outro.id_canonica
        JOIN investigacao.inq_tb01_inqueritos i2 ON i2.id_inquerito = e8_outro.id_inquerito
        WHERE e8.id_inquerito = %s
          AND e8.tp_identificador IN ({_SQL_IDENTIFICADORES_CONVERGENCIA})
          {_sql_cnpj_nao_frequente('e8')}
          {filtro}
        ORDER BY e8.tp_identificador, e8.co_valor, i2.nu_inquerito
    """
    params = (id_inquerito,) + params
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def resumo_convergencia_inqueritos(ids_inqueritos: list[int], usuario: dict) -> dict[str, list[dict]]:
    """O mesmo sinal de listar_convergencia_inquerito, para VÁRIOS inquéritos de uma vez e já
    contado: `{id_inquerito: [{id_inquerito_relacionado, nu_inquerito_relacionado,
    qt_identificadores}]}`.

    Existe para o alerta de convergência dos cards da lista de investigações, que fazia uma
    requisição POR CARD — dez consultas de cruzamento entre inquéritos para desenhar uma página, e
    dez toasts de erro quando a rota falhava. O front também baixava todas as ocorrências só para
    contar os identificadores distintos no navegador; a contagem sai daqui.

    ACESSO: o filtro é aplicado nos DOIS lados. O lado relacionado (i2) é o mesmo de sempre — não
    revelar inquérito que o usuário não pode ver. O lado de origem (i1) é novo e obrigatório
    aqui: a rota unitária recebe o id no path e passa por exigir_acesso_inquerito, mas esta recebe
    uma lista solta do cliente e não tem como usar aquela dependência. Sem o filtro de origem,
    bastaria pedir ?inqueritos=1,2,3,... para descobrir com quem cada inquérito alheio converge."""
    ids = [int(i) for i in ids_inqueritos]
    if not ids:
        return {}
    filtro_origem, params_origem = _filtro_acesso_sql(usuario, "i1")
    filtro_relacionado, params_relacionado = _filtro_acesso_sql(usuario, "i2")
    query = f"""
        SELECT e8.id_inquerito,
               i2.id_inquerito AS id_inquerito_relacionado,
               i2.nu_inquerito AS nu_inquerito_relacionado,
               COUNT(DISTINCT (e8.tp_identificador, e8.co_valor)) AS qt_identificadores
        FROM investigacao.ent_tb08_canonica_identificadores e8
        JOIN investigacao.inq_tb01_inqueritos i1 ON i1.id_inquerito = e8.id_inquerito
        JOIN investigacao.ent_tb08_canonica_identificadores e8_outro
          ON e8_outro.co_valor = e8.co_valor
         AND e8_outro.tp_identificador = e8.tp_identificador
         AND e8_outro.id_inquerito <> e8.id_inquerito
        JOIN investigacao.inq_tb01_inqueritos i2 ON i2.id_inquerito = e8_outro.id_inquerito
        WHERE e8.id_inquerito = ANY(%s)
          AND e8.tp_identificador IN ({_SQL_IDENTIFICADORES_CONVERGENCIA})
          {_sql_cnpj_nao_frequente('e8')}
          {filtro_origem}
          {filtro_relacionado}
        GROUP BY e8.id_inquerito, i2.id_inquerito, i2.nu_inquerito
        ORDER BY e8.id_inquerito, i2.nu_inquerito
    """
    params = (ids,) + params_origem + params_relacionado
    resumo: dict[str, list[dict]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            for id_inquerito, id_rel, nu_rel, qt in cur.fetchall():
                # chave em texto: é assim que ela chega no JSON do front de qualquer forma
                resumo.setdefault(str(id_inquerito), []).append({
                    "id_inquerito_relacionado": id_rel,
                    "nu_inquerito_relacionado": nu_rel,
                    "qt_identificadores": qt,
                })
    return resumo


# ------------------------------------------------------------------------------------------------
# Convergência entre inquéritos — cálculo em cache + leituras paginadas
#
# **Por que cache.** O cruzamento completo (seis sinais) virou query de >100 s em produção — só o
# nome aproximado por pg_trgm sobre ~30 mil nomes leva ~100 s — e morria no statement_timeout de
# requisição (60 s): a tela de convergência nunca carregava. O resultado é UMA função dos DADOS,
# não do usuário — o que muda por usuário é só QUAL inquérito ele pode ver. Então a conta pesada
# roda num laço de fundo (líder, de hora em hora) e grava os sinais em
# INVESTIGACAO.CONV_TB01_SINAIS; as rotas viram SELECT paginado sobre a tabela, com o
# _filtro_acesso_sql aplicado na LEITURA (a tabela em si não tem restrição nenhuma — vazamento
# zero vem do mesmo filtro de sempre, aplicado nos dois lados do par como antes).
#
# **Os seis sinais** (do mais forte ao mais ruidoso), inalterados desde docs/convergencia.md:
# 1. identificador exato (CPF/CNPJ/telefone/e-mail/IMEI/ICCID/IMSI/série/MAC/placa);
# 2. nome aproximado (similaridade pg_trgm ≥ _SIMILARIDADE_MINIMA_NOME);
# 3. mesmo IMEI físico (DVC_TB01);
# 4. local em comum (coordenada ~11 m com ≥ _MINIMO_PONTOS_LOCAL registros);
# 5. co-presença (≥ _MINIMO_MACS_CO_PRESENCA endereços físicos em comum);
# 6. ponto de rede em comum (coordenada de roteador/antena, mínimo próprio).
# ------------------------------------------------------------------------------------------------

# Caducidade do cache: 1 h. O laço recalcula quando a tabela está mais velha que isso; o botão
# "Atualizar" da tela força o mesmo cálculo imediatamente (mesma trava, nada roda em dobro).
CACHE_CONVERGENCIA_INTERVALO_S = 3600.0

# Chaves fixas de advisory lock (ver a lista no cabeçalho de leader_utils.py): o laço de hora em
# hora é líder (só uma réplica varre); o cálculo em si tem uma segunda trava, disputada pelo
# laço e pelo "Atualizar" manual — duas réplicas nunca rodam os ~100 s de query juntas, e quem
# perde a disputa desiste em silêncio porque o resultado na tabela é o mesmo.
_CONV_CHAVE_LACO = 72312
_CONV_CHAVE_REFRESH = 72313

# A consulta grande, SEM filtro de usuario (o filtro de acesso e de delegacia moram na leitura).
# E o UNION ALL que a tela chamava request sim, request nao — agora roda uma vez por hora.
# `%%` e o operador de similaridade pg_trgm (psycopg engole `%` simples).
_QUERY_SINAIS_CONVERGENCIA = f"""
        WITH locais_agrupados AS (
            SELECT ex.id_inquerito,
                   round(l.vl_latitude, 4) AS lat, round(l.vl_longitude, 4) AS lon,
                   max(l.tx_endereco) AS tx_endereco,
                   max(l.tx_descricao) AS tx_descricao,
                   count(*) AS qt_pontos
            FROM investigacao.loc_tb01_localizacoes l
            JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = l.id_extracao
            WHERE l.vl_latitude IS NOT NULL AND l.vl_longitude IS NOT NULL
            GROUP BY ex.id_inquerito, round(l.vl_latitude, 4), round(l.vl_longitude, 4)
            HAVING count(*) >= {_MINIMO_PONTOS_LOCAL}
        ),
        locais_rede AS (
            -- Coordenada que vem de infraestrutura de rede: a do roteador (Wi-Fi) identifica um
            -- ponto fisico com precisao bem maior que endereco de texto livre, e a da antena
            -- cobre deslocamento com o GPS desligado. Pool SEPARADO do historico de GPS de
            -- proposito: uma varredura de Wi-Fi devolve dezenas de redes na mesma coordenada.
            SELECT id_inquerito, round(lat, 4) AS lat, round(lon, 4) AS lon,
                   max(rotulo) AS rotulo, count(*) AS qt_pontos
            FROM (
                SELECT ex.id_inquerito, w.vl_latitude AS lat, w.vl_longitude AS lon,
                       'Rede Wi-Fi ' || coalesce(w.no_ssid, w.no_bssid, '') AS rotulo
                FROM investigacao.wifi_tb01_redes w
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = w.id_extracao
                WHERE w.vl_latitude IS NOT NULL AND w.vl_longitude IS NOT NULL
                UNION ALL
                SELECT ex.id_inquerito, a.vl_latitude, a.vl_longitude,
                       'Antena ' || coalesce(a.nu_cid, '')
                FROM investigacao.erb_tb01_antenas a
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                WHERE a.vl_latitude IS NOT NULL AND a.vl_longitude IS NOT NULL
            ) p
            GROUP BY id_inquerito, round(lat, 4), round(lon, 4)
            HAVING count(*) >= {_MINIMO_REDES_LOCAL}
        ),
        macs_brutos AS (
            SELECT ex.id_inquerito, lower(d.no_mac) AS mac,
                   max(coalesce(d.no_dispositivo, d.no_modelo, 'dispositivo')) AS rotulo
            FROM investigacao.rcd_tb01_dispositivos d
            JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
            WHERE d.no_mac IS NOT NULL AND length(d.no_mac) >= 11
            GROUP BY ex.id_inquerito, lower(d.no_mac)
        ),
        macs_por_inquerito AS (
            -- fora a infraestrutura: endereco presente em muitos inqueritos e equipamento
            -- publico, nao lugar de encontro
            SELECT m.* FROM macs_brutos m
            JOIN (
                SELECT mac FROM macs_brutos
                GROUP BY mac HAVING count(DISTINCT id_inquerito) <= {_MAX_INQUERITOS_MAC}
            ) ok ON ok.mac = m.mac
        ),
        sinais AS (
            -- 1) identificador exato (CPF, CNPJ, telefone, e-mail — inclui chave PIX —,
            -- identidade do aparelho e placa de veiculo)
            SELECT e8_a.id_inquerito AS id_a, e8_b.id_inquerito AS id_b,
                   e8_a.tp_identificador AS tp_sinal, e8_a.co_valor AS ds_valor,
                   c_a.no_entidade AS entidade_a, c_b.no_entidade AS entidade_b,
                   1.0::real AS vl_score
            FROM investigacao.ent_tb08_canonica_identificadores e8_a
            JOIN investigacao.ent_tb05_canonicas c_a ON c_a.id_canonica = e8_a.id_canonica
            JOIN investigacao.ent_tb08_canonica_identificadores e8_b
              ON e8_b.co_valor = e8_a.co_valor
             AND e8_b.tp_identificador = e8_a.tp_identificador
             AND e8_b.id_inquerito > e8_a.id_inquerito
            JOIN investigacao.ent_tb05_canonicas c_b ON c_b.id_canonica = e8_b.id_canonica
            WHERE e8_a.tp_identificador IN ({_SQL_IDENTIFICADORES_CONVERGENCIA})
              {_sql_cnpj_nao_frequente('e8_a')}

            UNION ALL

            -- 2) nome aproximado (trigrama — operador de similaridade usa o indice IX142; o
            -- limiar da sessao em `atualizar_cache_convergencia` faz o %% cortar ja em
            -- _SIMILARIDADE_MINIMA_NOME em vez do 0.3 padrao, que era metade do tempo de tela)
            SELECT e8_a.id_inquerito, e8_b.id_inquerito,
                   'nome', CASE WHEN e8_a.co_valor = e8_b.co_valor THEN e8_a.co_valor
                                ELSE e8_a.co_valor || ' ≈ ' || e8_b.co_valor END,
                   c_a.no_entidade, c_b.no_entidade,
                   similarity(e8_a.co_valor, e8_b.co_valor)
            FROM investigacao.ent_tb08_canonica_identificadores e8_a
            JOIN investigacao.ent_tb05_canonicas c_a ON c_a.id_canonica = e8_a.id_canonica
            JOIN investigacao.ent_tb08_canonica_identificadores e8_b
              ON e8_b.tp_identificador = 'nome'
             AND e8_a.co_valor %% e8_b.co_valor
             AND e8_b.id_inquerito > e8_a.id_inquerito
            JOIN investigacao.ent_tb05_canonicas c_b ON c_b.id_canonica = e8_b.id_canonica
            WHERE e8_a.tp_identificador = 'nome'
              AND similarity(e8_a.co_valor, e8_b.co_valor) >= {_SIMILARIDADE_MINIMA_NOME}

            UNION ALL

            -- 3) mesmo IMEI (aparelho fisico repetido entre inqueritos)
            SELECT ex_a.id_inquerito, ex_b.id_inquerito,
                   'imei', d_a.tx_imei,
                   coalesce(d_a.no_dispositivo, 'Aparelho'), coalesce(d_b.no_dispositivo, 'Aparelho'),
                   1.0::real
            FROM investigacao.dvc_tb01_dispositivos d_a
            JOIN investigacao.inq_tb02_extracoes ex_a ON ex_a.id_extracao = d_a.id_extracao
            JOIN investigacao.dvc_tb01_dispositivos d_b ON d_b.tx_imei = d_a.tx_imei
            JOIN investigacao.inq_tb02_extracoes ex_b ON ex_b.id_extracao = d_b.id_extracao
            WHERE d_a.tx_imei IS NOT NULL AND length(trim(d_a.tx_imei)) >= 14
              AND ex_b.id_inquerito > ex_a.id_inquerito

            UNION ALL

            -- 4) local em comum (coordenada arredondada ~11 m, visitada de forma recorrente)
            SELECT la.id_inquerito, lb.id_inquerito,
                   'local',
                   coalesce(la.tx_endereco, lb.tx_endereco, la.lat::text || ', ' || la.lon::text),
                   coalesce(la.tx_descricao, 'Local visitado') || ' (' || la.qt_pontos || ' registro(s))',
                   coalesce(lb.tx_descricao, 'Local visitado') || ' (' || lb.qt_pontos || ' registro(s))',
                   NULL::real
            FROM locais_agrupados la
            JOIN locais_agrupados lb ON lb.lat = la.lat AND lb.lon = la.lon AND lb.id_inquerito > la.id_inquerito

            UNION ALL

            -- 5) co-presenca: aparelhos de inqueritos diferentes que enxergaram os MESMOS
            -- enderecos fisicos por perto. Exige {_MINIMO_MACS_CO_PRESENCA} enderecos em comum.
            SELECT ma.id_inquerito, mb.id_inquerito,
                   'co_presenca',
                   count(*)::text || ' endereco(s) em comum, ex.: ' || min(ma.mac),
                   min(ma.rotulo), min(mb.rotulo),
                   NULL::real
            FROM macs_por_inquerito ma
            JOIN macs_por_inquerito mb ON mb.mac = ma.mac AND mb.id_inquerito > ma.id_inquerito
            GROUP BY ma.id_inquerito, mb.id_inquerito
            HAVING count(*) >= {_MINIMO_MACS_CO_PRESENCA}

            UNION ALL

            -- 6) ponto de rede em comum (coordenada de roteador/antena, minimo proprio)
            SELECT ra.id_inquerito, rb.id_inquerito,
                   'local_rede',
                   ra.lat::text || ', ' || ra.lon::text,
                   ra.rotulo || ' (' || ra.qt_pontos || ' ponto(s))',
                   rb.rotulo || ' (' || rb.qt_pontos || ' ponto(s))',
                   NULL::real
            FROM locais_rede ra
            JOIN locais_rede rb ON rb.lat = ra.lat AND rb.lon = ra.lon AND rb.id_inquerito > ra.id_inquerito
        )
        SELECT DISTINCT
               id_a, id_b, tp_sinal, ds_valor, entidade_a, entidade_b, vl_score,
               CASE WHEN tp_sinal IN ({_SQL_IDENTIFICADORES_CONVERGENCIA})
                    THEN 'exata' ELSE 'aproximada' END AS tp_confianca
        FROM sinais
        WHERE id_a <> id_b
"""


def atualizar_cache_convergencia() -> int:
    """Recalcula TODOS os sinais de convergência da base e troca o conteúdo de
    INVESTIGACAO.CONV_TB01_SINAIS de uma vez. Devolve o número de sinais gravados.

    Roda na conexão LONGA (statement_timeout 0): a conta leva minutos e é exatamente para isso
    que o perfil existe. A troca é `TRUNCATE + INSERT` dentro da MESMA transação — um leitor
    nunca vê a tabela no meio: ou vê o resultado velho inteiro, ou o novo inteiro. O TRUNCATE
    pega o lock da tabela só depois que a conta pesada terminou (o `CREATE TEMP TABLE` roda
    primeiro), então a janela de bloqueio é a do INSERT de ~100 mil linhas, não a dos 100 s.

    O limiar do operador `%%` (pg_trgm) é fixado em `_SIMILARIDADE_MINIMA_NOME` na transação via
    `set_config` (SET não aceita placeholder): o default 0.3 do operador deixava a varredura
    gerar o dobro de candidatos para fora do que a consulta aceita — o WHERE corta no mesmo
    lugar, mas depois do trabalho feito (medição em produção: ~96 s → ~38 s só com o limiar)."""
    t0 = time.monotonic()
    with get_connection(longa=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)",
                        (str(_SIMILARIDADE_MINIMA_NOME),))
            # params=() de propósito: a query carrega `%%` (o operador pg_trgm escapado para
            # psycopg), e SEM params o psycopg3 não faz a interpolação — o `%%` chegaria
            # literal ao Postgres ("operator does not exist: character varying %% character
            # varying", que era o erro silencioso do refresh em segundo plano)
            cur.execute(
                "CREATE TEMP TABLE tmp_conv_sinais ON COMMIT DROP AS\n"
                + _QUERY_SINAIS_CONVERGENCIA,
                (),
            )
            cur.execute("TRUNCATE TABLE investigacao.conv_tb01_sinais")
            cur.execute(
                """
                INSERT INTO investigacao.conv_tb01_sinais
                    (id_inquerito_a, id_inquerito_b, tp_sinal, tp_confianca,
                     vl_score, ds_valor, no_entidade_a, no_entidade_b, dt_geracao)
                SELECT id_a, id_b, tp_sinal, tp_confianca,
                       vl_score, ds_valor, entidade_a, entidade_b, now()
                FROM tmp_conv_sinais
                """
            )
            gravados = cur.rowcount
        conn.commit()
    logger.info("cache convergencia: %d sinais recalculados em %.1f s", gravados, time.monotonic() - t0)
    return gravados


def status_cache_convergencia() -> dict:
    """Estado do cache para a tela e para o disparo automático:
    {gerado_em, idade_s, sinais, recalculando}.

    `recalculando` lê o pg_locks em vez de uma flag de processo: o refresh pode estar rodando na
    OUTRA réplica da API, e é a vaga de cálculo (advisory lock) que importa para "não rode em
    dobro" — não quem a detém."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(dt_geracao), count(*) FROM investigacao.conv_tb01_sinais")
            gerado_em, sinais = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = %s",
                (_CONV_CHAVE_REFRESH,),
            )
            trava = cur.fetchone()[0]
    # O `or ... < 240` cobre a janela entre o disparo (thread recém-nascida, lock ainda não
    # tomado) e o primeiro pg_locks lido: sem ele, o "Atualizar" clicado nesse intervalo via
    # "não há nada recalculando" e a tela recarregava o cache velho achando que era o novo.
    idade = None
    if gerado_em is not None:
        idade = max(0.0, (time.time() - gerado_em.timestamp()))
    return {
        "gerado_em": gerado_em.isoformat(sep=" ", timespec="seconds") if gerado_em else None,
        "idade_s": int(idade) if idade is not None else None,
        "sinais": sinais,
        "recalculando": bool(trava) or (time.monotonic() - _ultimo_disparo) < 240.0,
    }


def _refresh_em_segundo_plano() -> None:
    """Roda `atualizar_cache_convergencia` numa thread, protegido pela trava de sessão do
    advisory lock: quem chega junto enquanto alguém calcula desiste em silêncio (a tabela vai
    refletir o mesmo resultado de qualquer forma). Conexão dedicada porque lock de SESSÃO morre
    com a conexão — é o que libera a vaga se o processo tombar no meio."""
    try:
        with nova_conexao_dedicada() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (_CONV_CHAVE_REFRESH,))
                if not cur.fetchone()[0]:
                    return
                try:
                    atualizar_cache_convergencia()
                finally:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_CONV_CHAVE_REFRESH,))
    except Exception:
        logger.exception("cache convergencia: refresh em segundo plano falhou")


_ultimo_disparo = 0.0
_disparo_trava = threading.Lock()


def disparar_refresh_convergencia(forcar: bool = False) -> bool:
    """Dispara o recálculo em thread se o cache estiver vazio/vencido (ou `forcar`). Devolve
    True se uma thread foi disparada agora.

    O throttle de 60 s evita um exército de threads quando meia dúzia de abas abre a tela com o
    cache vazio: a primeira dispara, as outras vão ler o resultado quando ele nascer (a tela
    mostra "recalculando" e tenta de novo sozinha)."""
    global _ultimo_disparo
    if not forcar:
        estado = status_cache_convergencia()
        if estado["idade_s"] is not None and estado["idade_s"] <= CACHE_CONVERGENCIA_INTERVALO_S:
            return False
    with _disparo_trava:
        agora = time.monotonic()
        if agora - _ultimo_disparo < 60.0 and not forcar:
            return False
        _ultimo_disparo = agora
    threading.Thread(target=_refresh_em_segundo_plano, daemon=True,
                     name="conv-refresh").start()
    return True


def _tique_cache_convergencia() -> None:
    """Tique do laço líder: recalcula só se o cache estiver mais velho que o intervalo (ou
    vazio). Com a base parada, o laço não acorda o banco para nada."""
    estado = status_cache_convergencia()
    if estado["idade_s"] is None or estado["idade_s"] > CACHE_CONVERGENCIA_INTERVALO_S:
        disparar_refresh_convergencia(forcar=True)


def iniciar_cache_convergencia() -> None:
    """Sobe o laço do cache de convergência no startup da API (main.py). Idempotente.

    LÍDER (leader_utils): com duas réplicas, só uma varre; se a líder tombar, a outra assume em
    até 30 s. O primeiro tique não espera a hora inteira — se a tabela está vazia (primeira
    subida pós-migração), o cache nasce junto com a API."""
    from funcoes import leader_utils

    leader_utils.iniciar_loop_lider(
        "cache-convergencia", _CONV_CHAVE_LACO, _tique_cache_convergencia,
        CACHE_CONVERGENCIA_INTERVALO_S, espera_inicial_s=15.0,
    )


# ---------------- leituras (rotas da tela de convergência) ----------------

_ORDENS_CONVERGENCIA = {
    "sinais": "qt_sinais DESC, nu_a, nu_b",
    "inquerito": "nu_a, nu_b",
}

# teto da exportação: o conjunto filtrado inteiro vai para a planilha; um filtro vazio num
# servidor com centenas de milhares de linhas não deve derrubar o navegador com um JSON sem fim
_EXPORTE_LIMITE = 200_000


def _escapa_like(termo: str) -> str:
    """Protege os curingas do LIKE/ILIKE digitados na busca livre (`%`, `_`, `\\`)."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtros_convergencia_sql(tipo=None, confianca=None, busca=None, min_score=None,
                              usuario=None, delegacia=None):
    """Monta o WHERE da CTE `sinais_filtrados` (e os params, na MESMA ordem) a partir dos
    filtros da tela + o filtro de acesso do usuário + delegacia. Compartilhado por listagem,
    arestas do grafo e exportação para os três nunca divergirem no que "o filtro selecionou".

    Semântica da busca (igual à que a tela tinha no cliente): casar com o NÚMERO do inquérito
    mantém o par inteiro; casar com valor/entidade/tipo mantém só os sinais que casam. O `OR`
    dos números dentro da mesma condição faz isso sozinho — por isso a busca entra ANTES do
    GROUP BY e não precisa de duas passadas.

    `min_score` (0–1): quando ativo, ficam só os exatos e os aproximados COM score acima do
    corte — os aproximados sem score (local, ponto de rede, co-presença) saem junto. É o
    "quero ver só o que é forte" que o campo "Score ≥ %" da tela pede."""
    partes = ["TRUE"]
    params: list = []

    if tipo:
        partes.append("s.tp_sinal = %s")
        params.append(tipo)
    if confianca:
        partes.append("s.tp_confianca = %s")
        params.append(confianca)
    if min_score is not None:
        partes.append("(s.tp_confianca = 'exata' OR (s.vl_score IS NOT NULL AND s.vl_score >= %s))")
        params.append(min_score)
    if busca:
        like = f"%{_escapa_like(busca)}%"
        partes.append(
            "(i_a.nu_inquerito ILIKE %s OR i_b.nu_inquerito ILIKE %s"
            " OR s.tp_sinal ILIKE %s OR s.ds_valor ILIKE %s"
            " OR s.no_entidade_a ILIKE %s OR s.no_entidade_b ILIKE %s)"
        )
        params.extend([like] * 6)

    if usuario is not None:
        filtro_a, params_a = _filtro_acesso_sql(usuario, "i_a")
        filtro_b, params_b = _filtro_acesso_sql(usuario, "i_b")
        if filtro_a:  # admin/ve-todas: fragmento vazio — " AND ".join não pode ver buraco
            partes.append(filtro_a.removeprefix(" AND "))
            params.extend(params_a)
        if filtro_b:
            partes.append(filtro_b.removeprefix(" AND "))
            params.extend(params_b)

    if delegacia:
        # mesma regra de antes: filtro de APRESENTAÇÃO, só estreita; quem restringe é o acesso.
        pode = usuario is not None and (
            usuario.get("ve_todas_delegacias") or usuario.get("no_delegacia") == delegacia
        )
        if pode:
            partes.append("i_a.no_delegacia = %s")
            partes.append("i_b.no_delegacia = %s")
            params.extend([delegacia, delegacia])

    return " AND ".join(partes), tuple(params)


def _cte_sinais_filtrados(where: str) -> str:
    return f"""
        WITH sinais_filtrados AS (
            SELECT s.id_inquerito_a, s.id_inquerito_b,
                   s.tp_sinal, s.tp_confianca, s.vl_score, s.ds_valor,
                   s.no_entidade_a, s.no_entidade_b,
                   i_a.nu_inquerito AS nu_a, i_b.nu_inquerito AS nu_b
            FROM investigacao.conv_tb01_sinais s
            JOIN investigacao.inq_tb01_inqueritos i_a ON i_a.id_inquerito = s.id_inquerito_a
            JOIN investigacao.inq_tb01_inqueritos i_b ON i_b.id_inquerito = s.id_inquerito_b
            WHERE {where}
        )"""


def listar_convergencia(usuario: dict, delegacia: str | None = None,
                        tipo: str | None = None, confianca: str | None = None,
                        busca: str | None = None, min_sinais: int = 0,
                        min_score: float | None = None, ordem: str = "sinais",
                        pagina: int = 1, tamanho: int = 10,
                        previa_sinais: int = 10) -> dict:
    """Página de PARES de inquéritos que convergem, com prévia de sinais — leitura do cache,
    com o filtro de acesso aplicado nos dois lados.

    Todo o trabalho (tipo/confianca/busca/score, contagem por par, "pelo menos X sinais",
    ordenação e paginação) acontece no SQL: a tela pede UMA página e baixa UMA página, não a
    base inteira como descia antes (o payload antigo passava de dezenas de MB e a tela nunca
    abria). Resposta:
    {totais: {pares, sinais}, pagina, tamanho, previa_sinais,
     pares: [{id_inquerito_a, nu_inquerito_a, id_inquerito_b, nu_inquerito_b,
              qt_sinais, convergencias: [prévia de `previa_sinais` sinais]}]}"""
    if ordem not in _ORDENS_CONVERGENCIA:
        ordem = "sinais"
    pagina = max(1, pagina)
    tamanho = min(max(1, tamanho), 50)
    previa_sinais = min(max(1, previa_sinais), 50)

    where, params = _filtros_convergencia_sql(tipo, confianca, busca, min_score, usuario, delegacia)
    offset = (pagina - 1) * tamanho

    query = _cte_sinais_filtrados(where) + f""",
        pares AS (
            SELECT id_inquerito_a, id_inquerito_b,
                   min(nu_a) AS nu_a, min(nu_b) AS nu_b, count(*) AS qt_sinais
            FROM sinais_filtrados
            GROUP BY id_inquerito_a, id_inquerito_b
            HAVING count(*) >= %s
        ),
        pares_num AS (
            SELECT p.*,
                   count(*) OVER () AS qt_pares,
                   coalesce(sum(qt_sinais) OVER (), 0) AS qt_sinais_totais,
                   row_number() OVER (ORDER BY {_ORDENS_CONVERGENCIA[ordem]}) AS rn
            FROM pares p
        ),
        pagina AS (
            SELECT * FROM pares_num WHERE rn > %s AND rn <= %s + %s
        ),
        previa AS (
            SELECT f.*, row_number() OVER (
                       PARTITION BY f.id_inquerito_a, f.id_inquerito_b
                       ORDER BY (f.tp_confianca = 'aproximada'),
                                f.vl_score DESC NULLS LAST, f.ds_valor
                   ) AS rn_sinal
            FROM sinais_filtrados f
            JOIN pagina pg ON pg.id_inquerito_a = f.id_inquerito_a
                          AND pg.id_inquerito_b = f.id_inquerito_b
        )
        SELECT pg.id_inquerito_a, pg.nu_a, pg.id_inquerito_b, pg.nu_b,
               pg.qt_sinais, pg.qt_pares, pg.qt_sinais_totais, pg.rn,
               p.tp_sinal, p.tp_confianca, p.vl_score, p.ds_valor,
               p.no_entidade_a, p.no_entidade_b, p.rn_sinal
        FROM pagina pg
        LEFT JOIN previa p ON p.id_inquerito_a = pg.id_inquerito_a
                          AND p.id_inquerito_b = pg.id_inquerito_b
                          AND p.rn_sinal <= %s
        ORDER BY pg.rn, p.rn_sinal
    """
    params = params + (max(1, min_sinais), offset, offset, tamanho, previa_sinais)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            linhas = cur.fetchall()

    if not linhas:
        return {"totais": {"pares": 0, "sinais": 0}, "pagina": pagina, "tamanho": tamanho,
                "previa_sinais": previa_sinais, "pares": []}

    pares: dict[tuple[int, int], dict] = {}
    ordem_pares: list[tuple[int, int]] = []
    qt_pares = 0
    qt_sinais_totais = 0
    for (id_a, nu_a, id_b, nu_b, qt_sinais, tot_pares, tot_sinais, _rn,
         tp_sinal, tp_confianca, vl_score, ds_valor, ent_a, ent_b, _rn_s) in linhas:
        chave = (id_a, id_b)
        if chave not in pares:
            qt_pares = tot_pares
            qt_sinais_totais = tot_sinais
            pares[chave] = {
                "id_inquerito_a": id_a, "nu_inquerito_a": nu_a,
                "id_inquerito_b": id_b, "nu_inquerito_b": nu_b,
                "qt_sinais": qt_sinais, "convergencias": [],
            }
            ordem_pares.append(chave)
        if tp_sinal is not None:
            pares[chave]["convergencias"].append({
                "tp_identificador": tp_sinal, "tp_confianca": tp_confianca,
                "vl_score": vl_score, "co_valor": ds_valor,
                "no_entidade_a": ent_a, "no_entidade_b": ent_b,
            })

    return {
        "totais": {"pares": qt_pares, "sinais": qt_sinais_totais},
        "pagina": pagina, "tamanho": tamanho, "previa_sinais": previa_sinais,
        "pares": [pares[c] for c in ordem_pares],
    }


def listar_convergencia_par(usuario: dict, id_a: int, id_b: int,
                            busca: str | None = None,
                            pagina: int = 1, tamanho: int = 50) -> dict:
    """Sinais de UM par de inquéritos (o modal da tela), paginados, restritos ao que o usuário
    pode ver — os dois lados passam pelo filtro de acesso, e um par com inquérito invisível não
    produz linha nenhuma (não vira oráculo de existência).

    `id_a`/`id_b` chegam do cliente em qualquer ordem; o cache guarda o menor id em
    `id_inquerito_a`, então normaliza-se antes de comparar."""
    lo, hi = sorted((int(id_a), int(id_b)))
    pagina = max(1, pagina)
    tamanho = min(max(1, tamanho), 200)

    partes = ["s.id_inquerito_a = %s", "s.id_inquerito_b = %s"]
    params: list = [lo, hi]
    if busca:
        like = f"%{_escapa_like(busca)}%"
        partes.append(
            "(s.tp_sinal ILIKE %s OR s.ds_valor ILIKE %s"
            " OR s.no_entidade_a ILIKE %s OR s.no_entidade_b ILIKE %s)"
        )
        params.extend([like] * 4)

    filtro_a, params_a = _filtro_acesso_sql(usuario, "i_a")
    filtro_b, params_b = _filtro_acesso_sql(usuario, "i_b")
    if filtro_a:
        partes.append(filtro_a.removeprefix(" AND "))
    if filtro_b:
        partes.append(filtro_b.removeprefix(" AND "))
    params.extend(params_a + params_b)

    query = f"""
        SELECT s.tp_sinal, s.tp_confianca, s.vl_score, s.ds_valor,
               s.no_entidade_a, s.no_entidade_b, count(*) OVER () AS qt_total
        FROM investigacao.conv_tb01_sinais s
        JOIN investigacao.inq_tb01_inqueritos i_a ON i_a.id_inquerito = s.id_inquerito_a
        JOIN investigacao.inq_tb01_inqueritos i_b ON i_b.id_inquerito = s.id_inquerito_b
        WHERE {" AND ".join(partes)}
        ORDER BY (s.tp_confianca = 'aproximada'), s.vl_score DESC NULLS LAST, s.ds_valor
        LIMIT %s OFFSET %s
    """
    params.extend([tamanho, (pagina - 1) * tamanho])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            linhas = cur.fetchall()

    total = linhas[0][6] if linhas else 0
    sinais = [{
        "tp_identificador": tp, "tp_confianca": conf, "vl_score": score,
        "co_valor": valor, "no_entidade_a": ent_a, "no_entidade_b": ent_b,
    } for (tp, conf, score, valor, ent_a, ent_b, _t) in linhas]
    return {"total": total, "pagina": pagina, "tamanho": tamanho, "sinais": sinais}


def listar_convergencia_arestas(usuario: dict, delegacia: str | None = None,
                                tipo: str | None = None, confianca: str | None = None,
                                busca: str | None = None, min_sinais: int = 0,
                                min_score: float | None = None) -> dict:
    """Só as ARESTAS do grafo (um par = uma linha com contagem e se tem sinal exato), sem
    texto de sinal nenhum — o grafo precisa da forma da rede inteira que o filtro selecionou,
    e baixar todos os sinais só para contar era metade do payload antigo.

    {gerado_em, arestas: [{id_a, nu_a, id_b, nu_b, qt_sinais, tem_exata}]}"""
    where, params = _filtros_convergencia_sql(tipo, confianca, busca, min_score, usuario, delegacia)
    query = _cte_sinais_filtrados(where) + """
        SELECT id_inquerito_a, min(nu_a) AS nu_a, id_inquerito_b, min(nu_b) AS nu_b,
               count(*) AS qt_sinais, bool_or(tp_confianca = 'exata') AS tem_exata
        FROM sinais_filtrados
        GROUP BY id_inquerito_a, id_inquerito_b
        HAVING count(*) >= %s
        ORDER BY count(*) DESC, nu_a, nu_b
    """
    params = params + (max(1, min_sinais),)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            linhas = cur.fetchall()
    return {
        "gerado_em": status_cache_convergencia()["gerado_em"],
        "arestas": [{
            "id_a": id_a, "nu_a": nu_a, "id_b": id_b, "nu_b": nu_b,
            "qt_sinais": qt, "tem_exata": tem,
        } for (id_a, nu_a, id_b, nu_b, qt, tem) in linhas],
    }


def exportar_convergencia(usuario: dict, delegacia: str | None = None,
                          tipo: str | None = None, confianca: str | None = None,
                          busca: str | None = None, min_sinais: int = 0,
                          min_score: float | None = None) -> list[dict]:
    """O conjunto filtrado INTEIRO, uma linha por sinal (o mesmo corte que a tela mostra), para
    o Excel e para a exportação do grafo i2. Com a lista paginada no servidor, "exportar o que
    está na tela" deixou de existir: exporta-se o que o FILTRO selecionou, vindo do banco — o
    teto de `_EXPORTE_LIMITE` linhas existe só para um filtro acidental não despejar meio
    milhão de linhas no navegador."""
    where, params = _filtros_convergencia_sql(tipo, confianca, busca, min_score, usuario, delegacia)
    query = _cte_sinais_filtrados(where) + f"""
        SELECT nu_a, nu_b, tp_sinal, tp_confianca, vl_score, ds_valor,
               no_entidade_a, no_entidade_b
        FROM sinais_filtrados f
        JOIN (
            SELECT id_inquerito_a, id_inquerito_b
            FROM sinais_filtrados
            GROUP BY id_inquerito_a, id_inquerito_b
            HAVING count(*) >= %s
        ) pq ON pq.id_inquerito_a = f.id_inquerito_a AND pq.id_inquerito_b = f.id_inquerito_b
        ORDER BY nu_a, nu_b, tp_sinal, vl_score DESC NULLS LAST, ds_valor
        LIMIT {_EXPORTE_LIMITE}
    """
    params = params + (max(1, min_sinais),)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            linhas = cur.fetchall()
    return [{
        "nu_a": nu_a, "nu_b": nu_b, "tp_sinal": tp, "tp_confianca": conf,
        "vl_score": score, "ds_valor": valor,
        "no_entidade_a": ent_a, "no_entidade_b": ent_b,
    } for (nu_a, nu_b, tp, conf, score, valor, ent_a, ent_b) in linhas]
