"""Marcadores nomeados — as "tags" do IPED trazidas para as marcações de relatório.

O investigador já marcava registros para o relatório (REL_TB03_MARCACOES, ver
relatorios_utils.py), mas só podia segmentá-los por COR ANÔNIMA: no .docx saía "[Vermelho]" e
era ele quem tinha de lembrar o que o vermelho significava naquele caso. Aqui a etiqueta ganha
NOME ("TRÁFICO DE DROGAS", "PENDENTES DE ANÁLISE"), como os Marcadores do IPED.

Duas regras moldam tudo neste módulo (ver db/init/31_marcadores_achados.sql):

  1) O marcador é PESSOAL do usuário e PRESO ao inquérito. Toda função recebe id_usuario e
     filtra por ele — não é decoração: dois investigadores no mesmo caso têm listas separadas,
     e o único UX98 é (inquérito, usuário, nome) justamente para permitir isso.

  2) A MARCAÇÃO em si continua COMPARTILHADA no inquérito (índice único UX95, ver
     30_relatorios.sql). Por isso o marcador não mora numa coluna de REL_TB03: ele se
     pendura nela pela tabela de aplicação N:N, e cada usuário só enxerga/mexe nas aplicações
     dos marcadores dele.

CO_COR de REL_TB03 continua existindo como fallback dos casos anteriores aos marcadores.
"""

import re
from datetime import datetime

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import _VERIFICACAO_ORIGEM
from funcoes.relatorios_utils import criar_marcacao

_COR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Mensageiros: cada família (WhatsApp / SMS / demais apps) tem o mesmo desenho de tabelas
# (TB01 contatos, TB02 conversas, TB03 participantes, TB04 mensagens, TB05 anexos), só mudam os
# nomes e a coluna que guarda o identificador do contato. Este mapa é o que permite uma consulta
# só servir aos três — os valores são constantes deste arquivo, nunca entrada do usuário, e a
# fonte é validada contra as chaves antes de qualquer interpolação.
FONTES = {
    "wpp": {
        "tp_mensagem": "wpp_mensagem", "tp_conversa": "wpp_conversa",
        "mensagens": "investigacao.wpp_tb04_mensagens",
        "conversas": "investigacao.wpp_tb02_conversas",
        "contatos": "investigacao.wpp_tb01_contatos",
        "participantes": "investigacao.wpp_tb03_participantes",
        "anexos": "investigacao.wpp_tb05_anexos",
        "identificador": "nu_whatsapp_id",
        "app": "'WhatsApp'",
    },
    "sms": {
        "tp_mensagem": "sms_mensagem", "tp_conversa": "sms_conversa",
        "mensagens": "investigacao.sms_tb04_mensagens",
        "conversas": "investigacao.sms_tb02_conversas",
        "contatos": "investigacao.sms_tb01_contatos",
        "participantes": "investigacao.sms_tb03_participantes",
        "anexos": "investigacao.sms_tb05_anexos",
        "identificador": "nu_telefone",
        "app": "'SMS'",
    },
    "msg": {
        "tp_mensagem": "msg_mensagem", "tp_conversa": "msg_conversa",
        "mensagens": "investigacao.msg_tb04_mensagens",
        "conversas": "investigacao.msg_tb02_conversas",
        "contatos": "investigacao.msg_tb01_contatos",
        "participantes": "investigacao.msg_tb03_participantes",
        "anexos": "investigacao.msg_tb05_anexos",
        "identificador": "nu_identificador",
        "app": "cv.tp_app",
    },
}


def _validar_fonte(fonte: str) -> dict:
    cfg = FONTES.get((fonte or "").strip())
    if cfg is None:
        raise ValueError(f"Fonte inválida: {fonte!r} (use wpp, sms ou msg).")
    return cfg


def _validar_nome(nome: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("O nome do marcador é obrigatório.")
    if len(nome) > 120:
        raise ValueError("O nome do marcador é muito longo (máximo de 120 caracteres).")
    return nome


def _validar_cor(cor: str | None) -> str:
    cor = (cor or "").strip().upper()
    if not _COR_RE.match(cor):
        raise ValueError("Cor inválida: use o formato #RRGGBB.")
    return cor


# =====================================================================
# CRUD dos marcadores
# =====================================================================


def listar_marcadores(id_inquerito: int, id_usuario: int) -> list[dict]:
    """Meus marcadores neste inquérito, com quantos registros cada um tem aplicado.

    Além do total (qt_itens), devolve o desdobramento por tipo (qt_conversas /
    qt_mensagens) a partir do tp_origem da marcação — é o que a página Marcações
    usa para mostrar "2 conv · 15 msg" sem precisar abrir o sumário."""
    query = """
        SELECT mc.id_marcador, mc.no_marcador, mc.co_cor, mc.tx_descricao, mc.nu_ordem,
               mc.dt_cadastro, count(ap.id_aplicacao) AS qt_itens,
               count(*) FILTER (WHERE RIGHT(rel.tp_origem, 9) = '_conversa') AS qt_conversas,
               count(*) FILTER (WHERE RIGHT(rel.tp_origem, 9) = '_mensagem') AS qt_mensagens
        FROM investigacao.mrc_tb01_marcadores mc
        LEFT JOIN investigacao.mrc_tb02_aplicacoes ap ON ap.id_marcador = mc.id_marcador
        LEFT JOIN investigacao.rel_tb03_marcacoes rel ON rel.id_marcacao = ap.id_marcacao
        WHERE mc.id_inquerito = %s AND mc.id_usuario = %s
        GROUP BY mc.id_marcador
        ORDER BY mc.nu_ordem, lower(mc.no_marcador)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito, id_usuario))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def obter_marcador(id_marcador: int) -> dict | None:
    """Marcador cru (inclusive o dono), para as rotas conferirem acesso antes de agir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_marcador, id_inquerito, id_usuario, no_marcador, co_cor, tx_descricao
                FROM investigacao.mrc_tb01_marcadores WHERE id_marcador = %s
                """,
                (id_marcador,),
            )
            linha = cur.fetchone()
            if linha is None:
                return None
            return dict(zip([d.name for d in cur.description], linha))


def criar_marcador(id_inquerito: int, id_usuario: int, nome: str, cor: str,
                   descricao: str | None = None) -> dict:
    """Cria o marcador e devolve-o; se já existir um com o mesmo nome (ignorando maiúsculas),
    devolve o existente em vez de estourar.

    Criar "Dinheiro" duas vezes não é erro do usuário — é ele reencontrando a própria etiqueta.
    Levantar 400 aqui obrigaria a tela a tratar um caso que não interessa a ninguém."""
    nome = _validar_nome(nome)
    cor = _validar_cor(cor)
    descricao = (descricao or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.mrc_tb01_marcadores
                    (id_inquerito, id_usuario, no_marcador, co_cor, tx_descricao)
                VALUES (%(inq)s, %(usr)s, %(nome)s, %(cor)s, %(desc)s)
                ON CONFLICT (id_inquerito, id_usuario, lower(no_marcador)) DO UPDATE
                   SET no_marcador = mrc_tb01_marcadores.no_marcador
                RETURNING id_marcador, no_marcador, co_cor, tx_descricao, nu_ordem
                """,
                {"inq": id_inquerito, "usr": id_usuario, "nome": nome, "cor": cor, "desc": descricao},
            )
            marcador = dict(zip([d.name for d in cur.description], cur.fetchone()))
        conn.commit()
    marcador["qt_itens"] = 0
    return marcador


def atualizar_marcador(id_marcador: int, nome: str, cor: str, descricao: str | None = None) -> bool:
    """Renomeia / troca a cor. A rota confere antes que o marcador é do usuário logado."""
    nome = _validar_nome(nome)
    cor = _validar_cor(cor)
    descricao = (descricao or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.mrc_tb01_marcadores
                   SET no_marcador = %s, co_cor = %s, tx_descricao = %s
                 WHERE id_marcador = %s
                """,
                (nome, cor, descricao, id_marcador),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_marcador(id_marcador: int) -> bool:
    """Apaga o marcador. As aplicações vão junto (FK205 ... ON DELETE CASCADE), mas as
    MARCAÇÕES não: o registro continua marcado para o relatório, só perde a etiqueta."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.mrc_tb01_marcadores WHERE id_marcador = %s", (id_marcador,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def mesclar_marcadores(id_origem: int, id_destino: int) -> int:
    """Move as aplicações de um marcador para outro e apaga o de origem; devolve quantas foram.

    Existe porque "Dinheiro" e "dinheiro vivo" nascem em dias diferentes e acabam dividindo o
    mesmo conjunto de provas ao meio. O ON CONFLICT cobre os registros que já estavam nos dois."""
    if id_origem == id_destino:
        raise ValueError("Escolha dois marcadores diferentes para mesclar.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.mrc_tb02_aplicacoes (id_marcacao, id_marcador)
                SELECT ap.id_marcacao, %(destino)s FROM investigacao.mrc_tb02_aplicacoes ap
                 WHERE ap.id_marcador = %(origem)s
                ON CONFLICT DO NOTHING
                """,
                {"origem": id_origem, "destino": id_destino},
            )
            movidas = cur.rowcount
            cur.execute("DELETE FROM investigacao.mrc_tb01_marcadores WHERE id_marcador = %s", (id_origem,))
        conn.commit()
    return movidas


# =====================================================================
# Aplicação dos marcadores nas marcações
# =====================================================================


def _ids_marcadores_do_usuario(cur, id_inquerito: int, id_usuario: int, ids: list[int]) -> list[int]:
    """Filtra a lista recebida da tela para os marcadores que são MESMO deste usuário neste
    inquérito. Sem isso, um id chutado no payload penduraria a etiqueta de outra pessoa."""
    if not ids:
        return []
    cur.execute(
        """
        SELECT id_marcador FROM investigacao.mrc_tb01_marcadores
         WHERE id_inquerito = %s AND id_usuario = %s AND id_marcador = ANY(%s)
        """,
        (id_inquerito, id_usuario, list(ids)),
    )
    return [linha[0] for linha in cur.fetchall()]


def definir_marcadores_da_marcacao(id_marcacao: int, id_inquerito: int, id_usuario: int,
                                   ids_marcadores: list[int]) -> list[int]:
    """Troca o conjunto de marcadores DESTE usuário nesta marcação pelo conjunto recebido.

    O DELETE é restrito às aplicações de marcadores do próprio usuário — a marcação é
    compartilhada no inquérito, então as etiquetas de um colega no mesmo registro não podem
    sumir porque eu salvei as minhas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            validos = _ids_marcadores_do_usuario(cur, id_inquerito, id_usuario, ids_marcadores or [])
            cur.execute(
                """
                DELETE FROM investigacao.mrc_tb02_aplicacoes ap
                 USING investigacao.mrc_tb01_marcadores mc
                 WHERE mc.id_marcador = ap.id_marcador
                   AND ap.id_marcacao = %s AND mc.id_usuario = %s
                """,
                (id_marcacao, id_usuario),
            )
            if validos:
                cur.executemany(
                    """
                    INSERT INTO investigacao.mrc_tb02_aplicacoes (id_marcacao, id_marcador)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """,
                    [(id_marcacao, id_marcador) for id_marcador in validos],
                )
        conn.commit()
    return validos


def aplicar_marcadores_lote(id_inquerito: int, id_usuario: int, itens: list[dict],
                            ids_marcadores: list[int], acao: str = "adicionar",
                            comentario: str | None = None, cor: str | None = None) -> dict:
    """Aplica (ou remove) marcadores em N registros de uma vez — os botões Adicionar/Remover do
    diálogo em lote, espelhando o diálogo de marcadores do IPED.

    Uma requisição para o lote inteiro: marcar um dia de conversa disparava uma por mensagem.

    Em "adicionar", garante primeiro que a marcação existe (criar_marcacao é idempotente por
    registro e mantém a validação de isolamento por caso). Em "remover", só tira a etiqueta —
    a marcação continua de pé, porque desmarcar do relatório é outra ação, com outro botão."""
    if acao not in ("adicionar", "remover"):
        raise ValueError(f"Ação inválida: {acao!r} (use adicionar ou remover).")

    aplicados = 0
    falhas: list[str] = []
    ids_marcacoes: list[int] = []

    pares: list[tuple[str, int]] = []
    for item in itens or []:
        tp_origem = (item.get("tp_origem") or "").strip()
        id_origem = item.get("id_origem")
        if tp_origem not in _VERIFICACAO_ORIGEM or id_origem is None:
            falhas.append(f"{tp_origem}:{id_origem}")
            continue
        pares.append((tp_origem, int(id_origem)))

    if acao == "adicionar":
        for tp_origem, id_origem in pares:
            try:
                id_marcacao, _ = criar_marcacao(
                    id_inquerito, tp_origem, id_origem, comentario, id_usuario, cor=cor
                )
                ids_marcacoes.append(id_marcacao)
            except ValueError:
                falhas.append(f"{tp_origem}:{id_origem}")
    else:
        # remover não cria nada: basta achar as marcações que já existem, numa consulta só
        ids_marcacoes = _ids_marcacoes_de(id_inquerito, pares)

    if ids_marcacoes:
        with get_connection() as conn:
            with conn.cursor() as cur:
                meus = _ids_marcadores_do_usuario(cur, id_inquerito, id_usuario, ids_marcadores or [])
                if meus and acao == "adicionar":
                    cur.executemany(
                        """
                        INSERT INTO investigacao.mrc_tb02_aplicacoes (id_marcacao, id_marcador)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                        """,
                        [(id_marcacao, id_marcador) for id_marcacao in ids_marcacoes for id_marcador in meus],
                    )
                    aplicados = len(ids_marcacoes)
                elif meus:
                    cur.execute(
                        """
                        DELETE FROM investigacao.mrc_tb02_aplicacoes
                         WHERE id_marcacao = ANY(%s) AND id_marcador = ANY(%s)
                        """,
                        (ids_marcacoes, meus),
                    )
                    aplicados = cur.rowcount
            conn.commit()

    return {"marcacoes": len(ids_marcacoes), "aplicados": aplicados, "falhas": falhas}


def _ids_marcacoes_de(id_inquerito: int, pares: list[tuple[str, int]]) -> list[int]:
    """Marcações já existentes para uma lista de (tp_origem, id_origem) — uma consulta só, em vez
    de uma ida ao banco por registro do lote."""
    if not pares:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_marcacao FROM investigacao.rel_tb03_marcacoes
                 WHERE id_inquerito = %s AND (tp_origem, id_origem) IN (
                       SELECT * FROM unnest(%s::text[], %s::bigint[])
                 )
                """,
                (id_inquerito, [p[0] for p in pares], [p[1] for p in pares]),
            )
            return [linha[0] for linha in cur.fetchall()]


def ids_marcacoes_dos_marcadores(id_inquerito: int, id_usuario: int,
                                 ids_marcadores: list[int] | None) -> list[int]:
    """Marcações alcançadas por um conjunto de marcadores meus — a ponte para reaproveitar
    listar_chat_marcado() (relatorios_utils) na exportação em .docx."""
    query = """
        SELECT DISTINCT ap.id_marcacao
        FROM investigacao.mrc_tb02_aplicacoes ap
        JOIN investigacao.mrc_tb01_marcadores mc ON mc.id_marcador = ap.id_marcador
        JOIN investigacao.rel_tb03_marcacoes rel ON rel.id_marcacao = ap.id_marcacao
        WHERE mc.id_inquerito = %(inq)s AND mc.id_usuario = %(usr)s AND rel.id_inquerito = %(inq)s
          {filtro}
    """
    params: dict = {"inq": id_inquerito, "usr": id_usuario}
    filtro = ""
    if ids_marcadores:
        filtro = "AND mc.id_marcador = ANY(%(ids)s)"
        params["ids"] = list(ids_marcadores)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            return [linha[0] for linha in cur.fetchall()]


# =====================================================================
# Sumário do mensageiro (marcador → conversa → mensagem)
# =====================================================================

_SQL_ITENS = """
    WITH marcadas AS (
        SELECT mc.id_marcador, mc.no_marcador, mc.co_cor, mc.nu_ordem,
               rel.id_marcacao, rel.tp_origem, rel.id_origem, rel.tx_comentario
          FROM investigacao.mrc_tb01_marcadores mc
          JOIN investigacao.mrc_tb02_aplicacoes ap ON ap.id_marcador = mc.id_marcador
          JOIN investigacao.rel_tb03_marcacoes rel ON rel.id_marcacao = ap.id_marcacao
         WHERE mc.id_inquerito = %(inq)s AND mc.id_usuario = %(usr)s
           AND rel.id_inquerito = %(inq)s
           AND rel.tp_origem IN (%(tp_msg)s, %(tp_conv)s)
           {filtro_marcador}
    )
    -- mensagens marcadas uma a uma
    SELECT mk.id_marcador, mk.no_marcador, mk.co_cor, mk.nu_ordem, mk.id_marcacao,
           mk.tx_comentario, 'mensagem'::text AS tp_item,
           m.id_mensagem, cv.id_conversa, {app} AS tp_app,
           m.tx_mensagem, m.dt_mensagem, m.in_enviada,
           ct.no_contato AS remetente, ct.{identificador} AS remetente_id,
           cv.no_grupo, itl.no_contato AS interlocutor, cv.id_extracao,
           (SELECT count(*) FROM {anexos} a WHERE a.id_mensagem = m.id_mensagem) AS qt_anexos,
           NULL::bigint AS qt_mensagens_conversa
      FROM marcadas mk
      JOIN {mensagens} m ON mk.tp_origem = %(tp_msg)s AND m.id_mensagem = mk.id_origem
      JOIN {conversas} cv ON cv.id_conversa = m.id_conversa
      LEFT JOIN {contatos} ct ON ct.id_contato = m.id_contato_remetente
      LEFT JOIN LATERAL (
            SELECT p.no_contato FROM {participantes} pp
              JOIN {contatos} p ON p.id_contato = pp.id_contato
                               AND NOT COALESCE(p.in_dono_dispositivo, false)
             WHERE pp.id_conversa = cv.id_conversa LIMIT 1
      ) itl ON true
     WHERE true {filtro_extracao}

    UNION ALL

    -- conversas marcadas inteiras (pela lista lateral do WhatsApp)
    SELECT mk.id_marcador, mk.no_marcador, mk.co_cor, mk.nu_ordem, mk.id_marcacao,
           mk.tx_comentario, 'conversa'::text,
           NULL::bigint, cv.id_conversa, {app},
           NULL::text, cv.dt_ultima_atividade, NULL::boolean,
           NULL::text, NULL::text,
           cv.no_grupo, itl.no_contato, cv.id_extracao,
           NULL::bigint,
           (SELECT count(*) FROM {mensagens} m WHERE m.id_conversa = cv.id_conversa)
      FROM marcadas mk
      JOIN {conversas} cv ON mk.tp_origem = %(tp_conv)s AND cv.id_conversa = mk.id_origem
      LEFT JOIN LATERAL (
            SELECT p.no_contato FROM {participantes} pp
              JOIN {contatos} p ON p.id_contato = pp.id_contato
                               AND NOT COALESCE(p.in_dono_dispositivo, false)
             WHERE pp.id_conversa = cv.id_conversa LIMIT 1
      ) itl ON true
     WHERE true {filtro_extracao}
"""


def _consultar_itens(id_inquerito: int, id_usuario: int, fonte: str,
                     id_extracao: int | None = None,
                     ids_marcadores: list[int] | None = None) -> list[dict]:
    cfg = _validar_fonte(fonte)
    params: dict = {
        "inq": id_inquerito, "usr": id_usuario,
        "tp_msg": cfg["tp_mensagem"], "tp_conv": cfg["tp_conversa"],
    }
    filtro_extracao = ""
    if id_extracao:
        filtro_extracao = "AND cv.id_extracao = %(ext)s"
        params["ext"] = int(id_extracao)
    filtro_marcador = ""
    if ids_marcadores:
        filtro_marcador = "AND mc.id_marcador = ANY(%(ids)s)"
        params["ids"] = list(ids_marcadores)

    query = _SQL_ITENS.format(
        filtro_extracao=filtro_extracao, filtro_marcador=filtro_marcador, **cfg
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _titulo(linha: dict) -> str:
    """Nome do grupo, senão o interlocutor que não é o dono do aparelho."""
    return linha.get("no_grupo") or linha.get("interlocutor") or "Conversa"


def _trecho(linha: dict, limite: int = 120) -> str:
    texto = (linha.get("tx_mensagem") or "").strip()
    if not texto:
        qt = linha.get("qt_anexos") or 0
        return f"({qt} anexo(s))" if qt else "(sem conteúdo)"
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def sumario_mensageiro(id_inquerito: int, id_usuario: int, fonte: str = "todas",
                       id_extracao: int | None = None, q: str | None = None,
                       limit: int | None = None, offset: int | None = None) -> list[dict]:
    """A árvore do painel flutuante e da página Marcações: marcador → conversa → mensagens.

    'marcada' na conversa diz que o fio inteiro foi marcado, não só mensagens soltas dele — é o
    que a exportação usa para decidir entre "sai a conversa completa" e "saem só estas bolhas".

    fonte aceita 'wpp', 'sms', 'msg' ou 'todas' (união das três; usado pela página Marcações
    para não esconder etiquetas de outro mensageiro). Cada conversa/mensagem carrega 'fonte'
    e os ids (id_conversa, id_mensagem, id_marcacao, tp_origem) para a página poder remover ou
    mover o item sem voltar ao mensageiro.

    q filtra por substring (título, texto, remetente, comentário) e limit/offset paginam as
    conversas de cada marcador — proteção contra um marcador com milhares de itens."""
    fontes = list(FONTES.keys()) if (fonte or "").strip() == "todas" else [(fonte or "").strip()]
    for f in fontes:
        if f not in FONTES:
            raise ValueError(f"Fonte inválida: {fonte!r} (use wpp, sms, msg ou todas).")
    cfg_por_fonte = {f: FONTES[f] for f in fontes}

    linhas: list[dict] = []
    for f, cfg in cfg_por_fonte.items():
        parcial = _consultar_itens(id_inquerito, id_usuario, f, id_extracao)
        for linha in parcial:
            linha["_fonte"] = f
            linha["_tp_msg"] = cfg["tp_mensagem"]
            linha["_tp_conv"] = cfg["tp_conversa"]
        linhas.extend(parcial)

    termo = (q or "").strip().lower()
    marcadores: dict[int, dict] = {}
    for linha in linhas:
        marcador = marcadores.setdefault(linha["id_marcador"], {
            "id_marcador": linha["id_marcador"],
            "no_marcador": linha["no_marcador"],
            "co_cor": linha["co_cor"],
            "nu_ordem": linha["nu_ordem"],
            "_conversas": {},
        })
        # id_conversa pode colidir entre famílias (tabelas distintas): a chave interna leva a
        # fonte junto; o id exposto continua o numérico, com 'fonte' ao lado para remontar o link.
        chave = f"{linha['_fonte']}:{linha['id_conversa']}"
        conversa = marcador["_conversas"].setdefault(chave, {
            "id_conversa": linha["id_conversa"],
            "fonte": linha["_fonte"],
            "titulo": _titulo(linha),
            "tp_app": linha.get("tp_app"),
            "id_extracao": linha.get("id_extracao"),
            "marcada": False,
            "id_marcacao_conversa": None,
            "tp_origem_conversa": linha["_tp_conv"],
            "tx_comentario": None,
            "qt_mensagens_conversa": 0,
            "mensagens": [],
        })
        if linha["tp_item"] == "conversa":
            conversa["marcada"] = True
            conversa["id_marcacao_conversa"] = linha.get("id_marcacao")
            conversa["tx_comentario"] = linha.get("tx_comentario")
            conversa["qt_mensagens_conversa"] = linha.get("qt_mensagens_conversa") or 0
            continue
        conversa["mensagens"].append({
            "id_mensagem": linha["id_mensagem"],
            "id_marcacao": linha["id_marcacao"],
            "tp_origem": linha["_tp_msg"],
            "fonte": linha["_fonte"],
            "trecho": _trecho(linha),
            "tx_mensagem": linha.get("tx_mensagem"),
            "dt_mensagem": linha.get("dt_mensagem"),
            "in_enviada": linha.get("in_enviada"),
            "remetente": linha.get("remetente") or linha.get("remetente_id"),
            "qt_anexos": linha.get("qt_anexos") or 0,
            "tx_comentario": linha.get("tx_comentario"),
        })

    if termo:
        for marcador in marcadores.values():
            filtradas = []
            for conversa in marcador["_conversas"].values():
                msgs = [m for m in conversa["mensagens"] if (
                    termo in (conversa["titulo"] or "").lower()
                    or termo in ((m.get("tx_mensagem") or "")).lower()
                    or termo in ((m.get("trecho") or "")).lower()
                    or termo in ((m.get("remetente") or "")).lower()
                    or termo in ((m.get("tx_comentario") or "")).lower()
                    or termo in ((conversa.get("tx_comentario") or "")).lower()
                )]
                conversa_ok = termo in (conversa["titulo"] or "").lower() or (
                    conversa["marcada"] and termo in ((conversa.get("tx_comentario") or "")).lower()
                )
                if conversa_ok:
                    filtradas.append(conversa)
                elif msgs:
                    conversa["mensagens"] = msgs
                    filtradas.append(conversa)
            marcador["_conversas"] = {f"{c['fonte']}:{c['id_conversa']}": c for c in filtradas}

    resultado = []
    for marcador in marcadores.values():
        conversas = list(marcador.pop("_conversas").values())
        for conversa in conversas:
            # datetime.min como sentinela: duas mensagens sem data cairiam num None < None, que
            # é TypeError — e mensagem sem dt_mensagem existe (registro truncado no UFDR)
            conversa["mensagens"].sort(key=lambda m: m["dt_mensagem"] or datetime.min)
        conversas.sort(key=lambda c: c["titulo"].lower())
        if offset:
            conversas = conversas[offset:]
        if limit:
            conversas = conversas[:limit]
        marcador["conversas"] = conversas
        marcador["qt_conversas"] = sum(1 for c in conversas if c["marcada"])
        marcador["qt_mensagens"] = sum(len(c["mensagens"]) for c in conversas)
        resultado.append(marcador)

    resultado.sort(key=lambda m: (m["nu_ordem"], m["no_marcador"].lower()))
    return resultado
