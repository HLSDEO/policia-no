"""Padrões de crime: biblioteca personalizável de padrões (tela "Padrões de crime")
e o motor que os executa contra os dados de um inquérito (botão "Detectar padrões"
na Mesa de Inquérito) — ver db/init/21_entidades_padroes.sql (PDR_TB01..03).

Três tipos de padrão, todos determinísticos (sem IA — cobrem 100% dos dados e o
resultado é reprodutível/auditável):

- 'lexico'  : lista de termos procurada em mensagens (WhatsApp/SMS), transcrições
              de áudio, notificações e histórico de navegação;
- 'regex'   : expressão regular (POSIX, case-insensitive) nas mesmas fontes;
- 'temporal': comportamento no tempo — 'madrugada' (conversas com volume alto de
              mensagens numa faixa de horário) e 'rajada' (muitas mensagens na
              mesma conversa numa janela de minutos).

Cada achado vira uma DETECÇÃO apontando o registro de origem (mesmo domínio
tp_origem/id_origem das entidades), de onde o investigador cria uma entidade
com um clique. Execução em background com polling, como as demais.
"""

import json
import logging
import re
import threading

import psycopg
from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

TIPOS_PADRAO_VALIDOS = {"lexico", "regex", "temporal", "estrutural"}
SUBTIPOS_TEMPORAIS = {"madrugada", "rajada"}

# diferente de léxico/regex/temporal, não é personalizável pelo usuário como termo ou
# parâmetro — é definição fixa, rodando sobre dado já cruzado pela identificação de
# pessoas (ENT_TB07_CANONICA_RELACOES) e pela ingestão de dispositivos (DVC_TB01).
SUBTIPOS_ESTRUTURAIS = {"imei_compartilhado", "triangulacao_financeira"}

# teto de detecções por padrão em cada fonte — um léxico genérico ("pó") num aparelho
# grande geraria dezenas de milhares de linhas sem ganho investigativo
_LIMITE_POR_FONTE = 200


def validar_definicao(tp_padrao: str, definicao: dict) -> dict:
    """Valida e normaliza a definição JSON de um padrão; devolve a definição limpa.
    Levanta ValueError com mensagem legível quando algo não fecha."""
    if tp_padrao not in TIPOS_PADRAO_VALIDOS:
        raise ValueError(f"Tipo de padrão inválido: {tp_padrao!r}")
    if not isinstance(definicao, dict):
        raise ValueError("Definição precisa ser um objeto JSON.")

    if tp_padrao == "lexico":
        termos = [str(t).strip() for t in definicao.get("termos", []) if str(t).strip()]
        if not termos:
            raise ValueError("Um padrão léxico precisa de pelo menos um termo.")
        return {"termos": termos}

    if tp_padrao == "regex":
        padrao = (definicao.get("padrao") or "").strip()
        if not padrao:
            raise ValueError("Um padrão regex precisa da expressão regular.")
        try:
            re.compile(padrao)
        except re.error as exc:
            raise ValueError(f"Expressão regular inválida: {exc}")
        return {"padrao": padrao}

    if tp_padrao == "estrutural":
        subtipo = definicao.get("subtipo")
        if subtipo not in SUBTIPOS_ESTRUTURAIS:
            raise ValueError("Padrão estrutural precisa de subtipo 'imei_compartilhado' ou 'triangulacao_financeira'.")
        return {"subtipo": subtipo}

    # temporal
    subtipo = definicao.get("subtipo")
    if subtipo not in SUBTIPOS_TEMPORAIS:
        raise ValueError("Padrão temporal precisa de subtipo 'madrugada' ou 'rajada'.")
    if subtipo == "madrugada":
        try:
            hora_inicio = int(definicao.get("hora_inicio", 0))
            hora_fim = int(definicao.get("hora_fim", 5))
            minimo = int(definicao.get("minimo", 10))
        except (TypeError, ValueError):
            raise ValueError("Horas e mínimo do padrão de madrugada precisam ser números inteiros.")
        if not (0 <= hora_inicio <= 23 and 0 <= hora_fim <= 23):
            raise ValueError("Horas precisam estar entre 0 e 23.")
        if minimo < 1:
            raise ValueError("O mínimo de mensagens precisa ser pelo menos 1.")
        return {"subtipo": "madrugada", "hora_inicio": hora_inicio, "hora_fim": hora_fim, "minimo": minimo}
    try:
        janela = int(definicao.get("janela_minutos", 10))
        minimo = int(definicao.get("minimo_mensagens", 20))
    except (TypeError, ValueError):
        raise ValueError("Janela e mínimo do padrão de rajada precisam ser números inteiros.")
    if janela < 1 or minimo < 2:
        raise ValueError("Janela mínima de 1 minuto e mínimo de 2 mensagens.")
    return {"subtipo": "rajada", "janela_minutos": janela, "minimo_mensagens": minimo}


# ---------- CRUD da biblioteca ----------


def listar_padroes() -> list[dict]:
    """Todos os padrões (ativos e inativos), mais recentes primeiro."""
    query = """
        SELECT p.id_padrao, p.no_padrao, p.tp_padrao, p.no_categoria, p.ds_padrao,
               p.tx_definicao, p.in_ativo, p.dt_cadastro, u.no_usuario AS criado_por
        FROM investigacao.pdr_tb01_padroes p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario_criador
        ORDER BY p.dt_cadastro DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def criar_padrao(
    nome: str, tp_padrao: str, definicao: dict, categoria: str | None = None,
    descricao: str | None = None, id_usuario: int | None = None,
) -> int:
    """Cria um padrão novo; devolve o ID. Levanta ValueError se nome/definição não fecharem."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do padrão não pode ser vazio.")
    definicao = validar_definicao(tp_padrao, definicao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.pdr_tb01_padroes
                    (no_padrao, tp_padrao, no_categoria, ds_padrao, tx_definicao, id_usuario_criador)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id_padrao
                """,
                (nome, tp_padrao, categoria, descricao, json.dumps(definicao), id_usuario),
            )
            id_padrao = cur.fetchone()[0]
        conn.commit()
    return id_padrao


def atualizar_padrao(
    id_padrao: int, nome: str, tp_padrao: str, definicao: dict,
    categoria: str | None = None, descricao: str | None = None,
) -> bool:
    """Atualiza um padrão existente; devolve False se o id não existir."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do padrão não pode ser vazio.")
    definicao = validar_definicao(tp_padrao, definicao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.pdr_tb01_padroes
                SET no_padrao = %s, tp_padrao = %s, no_categoria = %s, ds_padrao = %s, tx_definicao = %s
                WHERE id_padrao = %s
                """,
                (nome, tp_padrao, categoria, descricao, json.dumps(definicao), id_padrao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def definir_ativo(id_padrao: int, ativo: bool) -> bool:
    """Ativa/desativa um padrão (só os ativos entram na próxima detecção)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.pdr_tb01_padroes SET in_ativo = %s WHERE id_padrao = %s", (ativo, id_padrao)
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_padrao(id_padrao: int) -> bool:
    """Remove um padrão (as detecções dele caem junto via ON DELETE CASCADE)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.pdr_tb01_padroes WHERE id_padrao = %s", (id_padrao,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


# ---------- execução por inquérito ----------


class ExecucaoOcupada(RuntimeError):
    """Já há uma detecção de padrões ativa neste inquérito. As rotas traduzem em 409."""


def iniciar_deteccao(id_inquerito: int, id_usuario: int | None) -> int:
    """Cria a execução (status 'pendente') e dispara o processamento em background;
    devolve o ID da execução. Mesmo padrão de job/polling das demais.

    Uma ativa por inquérito (UX116_PDR_TB02_ATIVA): o segundo POST viola o índice e vira
    ExecucaoOcupada, em vez de subir N threads sobre o mesmo inquérito. Ver C8."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO investigacao.pdr_tb02_execucoes (id_inquerito, id_usuario) VALUES (%s, %s) RETURNING id_execucao",
                    (id_inquerito, id_usuario),
                )
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                raise ExecucaoOcupada("Já há uma detecção de padrões em andamento neste inquérito.")
            id_execucao = cur.fetchone()[0]
        conn.commit()
    thread = threading.Thread(target=_processar_deteccao, args=(id_execucao, id_inquerito), daemon=True)
    thread.start()
    return id_execucao


def listar_execucoes(id_inquerito: int) -> list[dict]:
    """Histórico de execuções da detecção de padrões, mais recente primeiro (polling)."""
    query = """
        SELECT x.id_execucao, x.cd_status, x.tx_erro, x.qt_deteccoes, x.dt_inicio, x.dt_fim,
               u.no_usuario AS iniciado_por
        FROM investigacao.pdr_tb02_execucoes x
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = x.id_usuario
        WHERE x.id_inquerito = %s
        ORDER BY x.dt_inicio DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_deteccoes(id_inquerito: int, id_execucao: int | None = None) -> list[dict]:
    """Detecções de uma execução (a mais recente concluída, se não informada), com os
    dados do padrão — o front agrupa por padrão."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if id_execucao is None:
                cur.execute(
                    """
                    SELECT id_execucao FROM investigacao.pdr_tb02_execucoes
                    WHERE id_inquerito = %s AND cd_status = 'concluido'
                    ORDER BY dt_inicio DESC LIMIT 1
                    """,
                    (id_inquerito,),
                )
                row = cur.fetchone()
                if row is None:
                    return []
                id_execucao = row[0]
            cur.execute(
                """
                SELECT d.id_deteccao, d.id_execucao, d.id_padrao, d.tp_origem, d.id_origem,
                       d.tx_trecho, d.dt_ocorrencia,
                       p.no_padrao, p.tp_padrao, p.no_categoria
                FROM investigacao.pdr_tb03_deteccoes d
                JOIN investigacao.pdr_tb01_padroes p ON p.id_padrao = d.id_padrao
                WHERE d.id_inquerito = %s AND d.id_execucao = %s
                ORDER BY p.no_padrao, d.dt_ocorrencia
                """,
                (id_inquerito, id_execucao),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _processar_deteccao(id_execucao: int, id_inquerito: int) -> None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.pdr_tb02_execucoes SET cd_status = 'processando' WHERE id_execucao = %s",
                    (id_execucao,),
                )
            conn.commit()

        padroes = [p for p in listar_padroes() if p["in_ativo"]]
        if not padroes:
            raise ValueError("Nenhum padrão ativo cadastrado na tela Padrões de crime.")

        total = 0
        erros = []
        for p in padroes:
            definicao = p["tx_definicao"] if isinstance(p["tx_definicao"], dict) else json.loads(p["tx_definicao"])
            try:
                if p["tp_padrao"] == "lexico":
                    total += _detectar_texto(id_execucao, id_inquerito, p["id_padrao"], definicao["termos"], None)
                elif p["tp_padrao"] == "regex":
                    total += _detectar_texto(id_execucao, id_inquerito, p["id_padrao"], None, definicao["padrao"])
                elif p["tp_padrao"] == "estrutural":
                    if definicao.get("subtipo") == "imei_compartilhado":
                        total += _detectar_imei_compartilhado(id_execucao, id_inquerito, p["id_padrao"])
                    else:
                        total += _detectar_triangulacao_financeira(id_execucao, id_inquerito, p["id_padrao"])
                elif definicao.get("subtipo") == "madrugada":
                    total += _detectar_madrugada(id_execucao, id_inquerito, p["id_padrao"], definicao)
                else:
                    total += _detectar_rajada(id_execucao, id_inquerito, p["id_padrao"], definicao)
            except Exception as exc:  # um padrão quebrado (ex.: regex que o Postgres rejeita) não derruba os demais
                erros.append(f"{p['no_padrao']}: {exc}")

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE investigacao.pdr_tb02_execucoes
                    SET cd_status = 'concluido', dt_fim = now(), qt_deteccoes = %s, tx_erro = %s
                    WHERE id_execucao = %s
                    """,
                    (total, "; ".join(erros)[:2000] if erros else None, id_execucao),
                )
            conn.commit()
    except Exception as exc:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE investigacao.pdr_tb02_execucoes
                        SET cd_status = 'erro', tx_erro = %s, dt_fim = now()
                        WHERE id_execucao = %s
                        """,
                        (str(exc)[:2000], id_execucao),
                    )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar status de erro da execução de padrões %s", id_execucao)


# fontes de texto varridas pelos padrões léxico/regex: (tp_origem, SELECT id, texto, data).
# As transcrições de áudio entram junto com a mensagem dona do anexo — mensagem de voz
# não tem texto, o conteúdo dela só existe na transcrição.
_FONTES_TEXTO = {
    "wpp_mensagem": """
        SELECT m.id_mensagem AS id_origem, m.tx_mensagem AS texto, m.dt_mensagem AS dt_ocorrencia
        FROM investigacao.wpp_tb04_mensagens m
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(inq)s AND m.tx_mensagem IS NOT NULL

        UNION ALL

        SELECT m.id_mensagem, a.tx_transcricao, m.dt_mensagem
        FROM investigacao.wpp_tb05_anexos a
        JOIN investigacao.wpp_tb04_mensagens m ON m.id_mensagem = a.id_mensagem
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(inq)s AND a.tx_transcricao IS NOT NULL
    """,
    "sms_mensagem": """
        SELECT m.id_mensagem AS id_origem, m.tx_mensagem AS texto, m.dt_mensagem AS dt_ocorrencia
        FROM investigacao.sms_tb04_mensagens m
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(inq)s AND m.tx_mensagem IS NOT NULL

        UNION ALL

        SELECT m.id_mensagem, a.tx_transcricao, m.dt_mensagem
        FROM investigacao.sms_tb05_anexos a
        JOIN investigacao.sms_tb04_mensagens m ON m.id_mensagem = a.id_mensagem
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(inq)s AND a.tx_transcricao IS NOT NULL
    """,
    "notificacao": """
        SELECT n.id_notificacao AS id_origem, n.tx_conteudo AS texto, n.dt_notificacao AS dt_ocorrencia
        FROM investigacao.ntf_tb01_notificacoes n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE e.id_inquerito = %(inq)s AND n.tx_conteudo IS NOT NULL
    """,
    "nota": """
        SELECT n.id_nota AS id_origem,
               concat_ws(': ', n.tx_titulo, n.tx_corpo) AS texto,
               COALESCE(n.dt_modificacao, n.dt_criacao) AS dt_ocorrencia
        FROM investigacao.not_tb01_notas n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE e.id_inquerito = %(inq)s AND n.tx_corpo IS NOT NULL
    """,
    "navegacao": """
        SELECT p.id_pagina AS id_origem, concat_ws(' · ', p.tx_titulo, p.tx_url) AS texto, p.dt_visita AS dt_ocorrencia
        FROM investigacao.nav_tb01_paginas_visitadas p
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
        WHERE e.id_inquerito = %(inq)s AND (p.tx_titulo IS NOT NULL OR p.tx_url IS NOT NULL)
    """,
    # texto reconhecido nas imagens (ocr_utils): print de comprovante PIX, boleto, foto de documento
    # e conversa printada só têm conteúdo aqui — sem isto, nenhum padrão léxico/regex os alcança.
    # A data é a de modificação do arquivo (é o carimbo que o UFDR dá à imagem). O tp_origem é
    # 'arquivo' (o mesmo das marcações/entidades da tela Arquivos), e não um tipo novo, para que
    # "Criar entidade" a partir da detecção continue funcionando.
    "arquivo": """
        SELECT a.id_arquivo AS id_origem, a.tx_ocr AS texto, a.dt_modificacao AS dt_ocorrencia
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(inq)s AND a.tx_ocr IS NOT NULL
    """,
}


def _detectar_texto(
    id_execucao: int, id_inquerito: int, id_padrao: int, termos: list[str] | None, regex: str | None
) -> int:
    """Roda um padrão léxico (termos) ou regex sobre as fontes de texto; devolve o total inserido."""
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tp_origem, fonte in _FONTES_TEXTO.items():
                if termos is not None:
                    filtro = "texto ILIKE ANY (%(termos)s)"
                    params = {"inq": id_inquerito, "termos": [f"%{t}%" for t in termos]}
                else:
                    filtro = "texto ~* %(regex)s"
                    params = {"inq": id_inquerito, "regex": regex}
                cur.execute(
                    f"""
                    INSERT INTO investigacao.pdr_tb03_deteccoes
                        (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, tx_trecho, dt_ocorrencia)
                    SELECT %(execucao)s, %(inq)s, %(padrao)s, %(tp_origem)s, id_origem, left(texto, 500), dt_ocorrencia
                    FROM ({fonte}) fonte
                    WHERE {filtro}
                    LIMIT {_LIMITE_POR_FONTE}
                    """,
                    {**params, "execucao": id_execucao, "padrao": id_padrao, "tp_origem": tp_origem},
                )
                total += cur.rowcount
        conn.commit()
    return total


# conversas de WhatsApp e SMS têm o mesmo formato — os padrões temporais rodam nos dois
_FONTES_CONVERSA = {
    "wpp_mensagem": ("wpp_tb04_mensagens", "wpp_tb02_conversas", "wpp_tb03_participantes", "wpp_tb01_contatos"),
    "sms_mensagem": ("sms_tb04_mensagens", "sms_tb02_conversas", "sms_tb03_participantes", "sms_tb01_contatos"),
}


def _rotulo_conversa(tabelas: tuple[str, str, str, str]) -> str:
    """Fragmento SQL que dá um nome legível à conversa: nome do grupo ou o primeiro
    participante que não é o dono do aparelho."""
    _, _, participantes, contatos = tabelas
    return f"""
        coalesce(c.no_grupo, (
            SELECT ct.no_contato FROM investigacao.{participantes} pa
            JOIN investigacao.{contatos} ct ON ct.id_contato = pa.id_contato
            WHERE pa.id_conversa = c.id_conversa AND coalesce(ct.in_dono_dispositivo, false) = false
              AND ct.no_contato IS NOT NULL
            ORDER BY ct.id_contato LIMIT 1
        ), 'conversa ' || c.id_conversa)
    """


def _detectar_madrugada(id_execucao: int, id_inquerito: int, id_padrao: int, definicao: dict) -> int:
    """Uma detecção por conversa com >= mínimo de mensagens na faixa de horário, ancorada
    na última mensagem da faixa."""
    hora_inicio, hora_fim, minimo = definicao["hora_inicio"], definicao["hora_fim"], definicao["minimo"]
    if hora_inicio <= hora_fim:
        faixa = "extract(hour FROM m.dt_mensagem) BETWEEN %(hi)s AND %(hf)s"
    else:  # faixa cruzando a meia-noite (ex.: 22h às 5h)
        faixa = "(extract(hour FROM m.dt_mensagem) >= %(hi)s OR extract(hour FROM m.dt_mensagem) <= %(hf)s)"

    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tp_origem, tabelas in _FONTES_CONVERSA.items():
                mensagens, conversas = tabelas[0], tabelas[1]
                cur.execute(
                    f"""
                    INSERT INTO investigacao.pdr_tb03_deteccoes
                        (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, tx_trecho, dt_ocorrencia)
                    SELECT %(execucao)s, %(inq)s, %(padrao)s, %(tp_origem)s,
                           max(m.id_mensagem),
                           count(*) || ' mensagens entre ' || %(hi)s || 'h e ' || %(hf)s || 'h na conversa «'
                               || {_rotulo_conversa(tabelas)} || '»',
                           max(m.dt_mensagem)
                    FROM investigacao.{mensagens} m
                    JOIN investigacao.{conversas} c ON c.id_conversa = m.id_conversa
                    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(inq)s AND m.dt_mensagem IS NOT NULL AND {faixa}
                    GROUP BY c.id_conversa, c.no_grupo
                    HAVING count(*) >= %(minimo)s
                    LIMIT {_LIMITE_POR_FONTE}
                    """,
                    {
                        "execucao": id_execucao, "inq": id_inquerito, "padrao": id_padrao,
                        "tp_origem": tp_origem, "hi": hora_inicio, "hf": hora_fim, "minimo": minimo,
                    },
                )
                total += cur.rowcount
        conn.commit()
    return total


def _detectar_imei_compartilhado(id_execucao: int, id_inquerito: int, id_padrao: int) -> int:
    """Duas extrações do MESMO inquérito com o mesmo IMEI, donos diferentes — aparelho
    repassado ou compartilhado (versão intra-inquérito do sinal 3 de listar_convergencia_global,
    canonicas_utils.py, que compara entre inquéritos diferentes). DVC_TB01_DISPOSITIVOS só é
    populado por ingestão de conta na nuvem hoje (mesma limitação documentada em
    db/init/21_entidades_padroes.sql) — este padrão só dispara quando esse tipo de
    extração existe no inquérito. Gera uma detecção para CADA dono envolvido, apontando
    tp_origem='dono', para "Criar entidade" funcionar a partir de qualquer um dos dois lados."""
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d1.tx_imei,
                       dn1.id_dono, coalesce(dn1.no_dono, 'Dono não identificado'),
                       dn2.id_dono, coalesce(dn2.no_dono, 'Dono não identificado')
                FROM investigacao.dvc_tb01_dispositivos d1
                JOIN investigacao.inq_tb02_extracoes e1 ON e1.id_extracao = d1.id_extracao
                JOIN investigacao.don_tb01_donos dn1 ON dn1.id_extracao = e1.id_extracao
                JOIN investigacao.dvc_tb01_dispositivos d2 ON d2.tx_imei = d1.tx_imei AND d2.id_extracao > d1.id_extracao
                JOIN investigacao.inq_tb02_extracoes e2 ON e2.id_extracao = d2.id_extracao
                JOIN investigacao.don_tb01_donos dn2 ON dn2.id_extracao = e2.id_extracao
                WHERE e1.id_inquerito = %(inq)s AND e2.id_inquerito = %(inq)s
                  AND d1.tx_imei IS NOT NULL AND length(trim(d1.tx_imei)) >= 14
                  AND dn1.id_dono <> dn2.id_dono
                LIMIT {_LIMITE_POR_FONTE}
                """,
                {"inq": id_inquerito},
            )
            for imei, id_dono1, nome1, id_dono2, nome2 in cur.fetchall():
                trecho = f"Mesmo IMEI {imei} usado por {nome1} e {nome2} — aparelho repassado ou compartilhado."
                for id_dono in (id_dono1, id_dono2):
                    cur.execute(
                        """
                        INSERT INTO investigacao.pdr_tb03_deteccoes
                            (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, tx_trecho)
                        VALUES (%s, %s, %s, 'dono', %s, %s)
                        """,
                        (id_execucao, id_inquerito, id_padrao, id_dono, trecho[:500]),
                    )
                    total += cur.rowcount
        conn.commit()
    return total


def _detectar_triangulacao_financeira(id_execucao: int, id_inquerito: int, id_padrao: int) -> int:
    """Ciclo de pagamentos A→B→C→A entre canônicas já ligadas por RIF/SIMBA
    (ENT_TB07_CANONICA_RELACOES, tipo 'pagou a (SIMBA/RIF)'). Puramente estrutural — existe o
    ciclo — sem conferir se os valores das pernas batem: a relação não guarda valor hoje, só o
    tipo. Serve para levantar hipótese de triangulação, não para confirmar sozinha. Cada ciclo
    gera 3 detecções, uma por canônica, ancoradas na evidência mais antiga dela (o mesmo domínio
    tp_origem/id_origem de sempre, para "Criar entidade" continuar funcionando)."""
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r1.id_canonica_origem AS a, r1.id_canonica_destino AS b, r2.id_canonica_destino AS c
                FROM investigacao.ent_tb07_canonica_relacoes r1
                JOIN investigacao.ent_tb07_canonica_relacoes r2
                  ON r2.id_canonica_origem = r1.id_canonica_destino AND r2.tx_tipo_relacao LIKE 'pagou a%%'
                JOIN investigacao.ent_tb07_canonica_relacoes r3
                  ON r3.id_canonica_origem = r2.id_canonica_destino AND r3.id_canonica_destino = r1.id_canonica_origem
                     AND r3.tx_tipo_relacao LIKE 'pagou a%%'
                WHERE r1.tx_tipo_relacao LIKE 'pagou a%%' AND r1.id_inquerito = %(inq)s
                """,
                {"inq": id_inquerito},
            )
            ciclos_vistos: set[frozenset] = set()
            for a, b, c in cur.fetchall():
                chave = frozenset((a, b, c))
                if len(chave) < 3 or chave in ciclos_vistos:
                    continue
                ciclos_vistos.add(chave)

                cur.execute(
                    "SELECT id_canonica, no_entidade FROM investigacao.ent_tb05_canonicas WHERE id_canonica = ANY(%s)",
                    ([a, b, c],),
                )
                nomes = dict(cur.fetchall())
                trecho = (
                    f"Possível triangulação financeira: {nomes.get(a, '?')} → {nomes.get(b, '?')} → "
                    f"{nomes.get(c, '?')} → {nomes.get(a, '?')} (ciclo de pagamentos, valores não conferidos)."
                )[:500]

                for id_canonica in (a, b, c):
                    cur.execute(
                        """
                        SELECT tp_origem, id_origem FROM investigacao.ent_tb06_canonica_evidencias
                        WHERE id_canonica = %s ORDER BY dt_criacao LIMIT 1
                        """,
                        (id_canonica,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        continue
                    tp_origem, id_origem = row
                    cur.execute(
                        """
                        INSERT INTO investigacao.pdr_tb03_deteccoes
                            (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, tx_trecho)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, trecho),
                    )
                    total += cur.rowcount
        conn.commit()
    return total


def _detectar_rajada(id_execucao: int, id_inquerito: int, id_padrao: int, definicao: dict) -> int:
    """Uma detecção por conversa/dia em que alguma janela deslizante de N minutos teve
    >= mínimo de mensagens, ancorada na mensagem que fechou a maior janela do dia."""
    janela, minimo = definicao["janela_minutos"], definicao["minimo_mensagens"]
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tp_origem, tabelas in _FONTES_CONVERSA.items():
                mensagens, conversas = tabelas[0], tabelas[1]
                cur.execute(
                    f"""
                    INSERT INTO investigacao.pdr_tb03_deteccoes
                        (id_execucao, id_inquerito, id_padrao, tp_origem, id_origem, tx_trecho, dt_ocorrencia)
                    SELECT DISTINCT ON (j.id_conversa, date_trunc('day', j.dt_mensagem))
                           %(execucao)s, %(inq)s, %(padrao)s, %(tp_origem)s,
                           j.id_mensagem,
                           j.qt || ' mensagens em ' || %(janela)s || ' minutos na conversa «' || j.rotulo || '»',
                           j.dt_mensagem
                    FROM (
                        SELECT m.id_mensagem, m.id_conversa, m.dt_mensagem,
                               {_rotulo_conversa(tabelas)} AS rotulo,
                               count(*) OVER (
                                   PARTITION BY m.id_conversa ORDER BY m.dt_mensagem
                                   RANGE BETWEEN (%(janela)s * interval '1 minute') PRECEDING AND CURRENT ROW
                               ) AS qt
                        FROM investigacao.{mensagens} m
                        JOIN investigacao.{conversas} c ON c.id_conversa = m.id_conversa
                        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                        WHERE e.id_inquerito = %(inq)s AND m.dt_mensagem IS NOT NULL
                    ) j
                    WHERE j.qt >= %(minimo)s
                    ORDER BY j.id_conversa, date_trunc('day', j.dt_mensagem), j.qt DESC
                    LIMIT {_LIMITE_POR_FONTE}
                    """,
                    {
                        "execucao": id_execucao, "inq": id_inquerito, "padrao": id_padrao,
                        "tp_origem": tp_origem, "janela": janela, "minimo": minimo,
                    },
                )
                total += cur.rowcount
        conn.commit()
    return total
