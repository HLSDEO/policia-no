"""Auditoria completa das chamadas de IA (investigacao.ia_tb04_auditoria).

Cada turno de IA — chat por inquérito, chat por caso, busca automática de entidades e busca em
documentos — gera um registro com o prompt enviado, o contexto utilizado, as ferramentas chamadas
(com argumentos), os documentos consultados, o tempo de resposta, a quantidade de tokens e o custo
estimado. A gravação nunca levanta exceção: falha de auditoria não pode derrubar o turno que ela
está registrando (o erro vai para o log do servidor).
"""

import json
import logging
import re

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.ia_provedores import AuditoriaIA, estimar_custo_usd

logger = logging.getLogger(__name__)

_RE_DATA_ISO = re.compile(r"^\d{4}-\d\d-\d\d$")

# Lê um timestamp naive como hora de São Paulo e devolve o naive equivalente em UTC — usado para
# traduzir os limites do filtro de data (dia local) para o fuso em que a coluna está gravada.
# Fuso IANA, e não "-03" fixo: registro antigo, de quando ainda havia horário de verão, sai certo.
_PARA_UTC = "AT TIME ZONE 'America/Sao_Paulo' AT TIME ZONE 'UTC'"

# Valores aceitos pelo filtro ?feedback= da tela de auditoria (só fazem sentido para o
# chat do inquérito, a única origem com voto 👍/👎 — ver ia_tb06_feedback).
_FEEDBACKS_VALIDOS = {"com_avaliacao", "sem_avaliacao", "positivas", "negativas"}

# Ordenações aceitas pelo ?ordenar= da tela de auditoria (valor fora da lista cai no padrão).
_FEEDBACKS_VALIDOS = {"com_avaliacao", "sem_avaliacao", "positivas", "negativas"}
_ORDENACOES = {
    "data_desc": "a.dt_chamada DESC, a.id_auditoria DESC",
    "data_asc": "a.dt_chamada ASC, a.id_auditoria ASC",
    "tokens_desc": "(a.qt_tokens_entrada + a.qt_tokens_saida) DESC NULLS LAST, a.dt_chamada DESC",
    "tokens_asc": "(a.qt_tokens_entrada + a.qt_tokens_saida) ASC NULLS LAST, a.dt_chamada DESC",
    "tempo_desc": "a.nu_tempo_ms DESC NULLS LAST, a.dt_chamada DESC",
    "tempo_asc": "a.nu_tempo_ms ASC NULLS LAST, a.dt_chamada DESC",
}

# O contexto do modo "conteúdo completo" pode ter megabytes (o conteúdo do inquérito vai anexado ao
# prompt de sistema); guardamos um prefixo suficiente para depuração e o tamanho real ao lado.
_MAX_CHARS_CONTEXTO = 20_000

# ~caracteres por token em português — usado só como último recurso, quando o provedor não informa
# o uso (mesma constante conservadora de funcoes.ia_utils).
_CHARS_POR_TOKEN = 3.3

# Ferramentas cuja chamada significa "a IA consultou um documento" — vira a coluna TX_DOCUMENTOS.
_FERRAMENTAS_DOCUMENTO = {"ler_documento_inquerito", "ler_documento"}


def registrar_auditoria(
    origem: str,
    prompt: str,
    contexto: str | None,
    auditoria: AuditoriaIA | None,
    modelo: str | None,
    tempo_ms: int,
    status: str = "ok",
    erro: str | None = None,
    resposta: str | None = None,
    id_inquerito: int | None = None,
    id_conversa_ia: int | None = None,
    id_mensagem: int | None = None,
    id_usuario: int | None = None,
) -> None:
    """Grava um registro de auditoria de um turno de IA. Nunca levanta exceção.

    id_mensagem é a resposta do assistente gerada neste turno, quando já foi gravada: é por ele
    que a bolha da resposta mostra tokens/tempo/custo do próprio turno (turno cancelado ou com
    erro não tem resposta, e fica sem)."""
    try:
        auditoria = auditoria or AuditoriaIA()
        contexto = contexto or ""
        env = parse_env(read_env_text())

        tokens_entrada = auditoria.qt_tokens_entrada
        tokens_saida = auditoria.qt_tokens_saida
        tokens_estimados = False
        if tokens_entrada == 0 and tokens_saida == 0 and auditoria.qt_chamadas_api > 0:
            # provedor não informou uso — estima pelo tamanho dos textos, só pra não ficar sem nada
            tokens_entrada = int((len(contexto) + len(prompt or "")) / _CHARS_POR_TOKEN)
            tokens_saida = int(len(resposta or "") / _CHARS_POR_TOKEN)
            tokens_estimados = True

        documentos = [
            {"ferramenta": f["ferramenta"], **(f.get("argumentos") or {})}
            for f in auditoria.ferramentas
            if f["ferramenta"] in _FERRAMENTAS_DOCUMENTO
        ]

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO investigacao.ia_tb04_auditoria
                        (no_origem, id_inquerito, id_conversa_ia, id_mensagem, id_usuario,
                         no_provedor, no_modelo, tx_prompt, tx_contexto, qt_chars_contexto,
                         tx_ferramentas, tx_documentos, qt_chamadas_api, qt_ferramentas,
                         qt_tokens_entrada, qt_tokens_saida, in_tokens_estimados,
                         vl_custo_usd, nu_tempo_ms, cd_status, tx_erro)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        origem, id_inquerito, id_conversa_ia, id_mensagem, id_usuario,
                        env.get("AI_PROVIDER", "") or None, modelo or env.get("AI_MODEL", "") or None,
                        prompt, contexto[:_MAX_CHARS_CONTEXTO], len(contexto),
                        json.dumps(auditoria.ferramentas, ensure_ascii=False, default=str),
                        json.dumps(documentos, ensure_ascii=False, default=str),
                        auditoria.qt_chamadas_api, len(auditoria.ferramentas),
                        tokens_entrada, tokens_saida, tokens_estimados,
                        estimar_custo_usd(modelo or env.get("AI_MODEL", ""), tokens_entrada, tokens_saida),
                        tempo_ms, status, (erro or None) and str(erro)[:2000],
                    ),
                )
            conn.commit()
    except Exception:  # auditoria nunca derruba o turno que está registrando
        logger.exception(
            "Falha ao gravar auditoria de IA",
            extra={"origem": origem, "id_inquerito": id_inquerito, "id_usuario": id_usuario},
        )


def listar_auditoria(
    id_inquerito: int | None = None,
    id_usuario: int | None = None,
    id_conversa_ia: int | None = None,
    origem: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    ordenar: str | None = None,
    limite: int = 50,
    deslocamento: int = 0,
    feedback: str | None = None,
) -> dict:
    """Lista os registros de auditoria (mais recentes primeiro) com filtros opcionais, mais os
    totais agregados do filtro (tokens, custo, tempo, votos 👍/👎) — devolve
    {'itens', 'totais', 'qt_total'}.

    Filtros: id_inquerito, id_usuario, id_conversa_ia, origem, data_de/data_ate ('AAAA-MM-DD',
    sobre a data da chamada), feedback ('com_avaliacao' | 'sem_avaliacao' | 'positivas' |
    'negativas' — só o chat do inquérito tem voto; com filtro ativo as demais origens somem).
    Ordenação: ?ordenar= (ver _ORDENACOES; valor fora da lista cai
    no padrão, mais recentes primeiro).

    Cada item traz qt_feedback_pos/qt_feedback_neg (votos na resposta deste turno; 0 quando o
    turno não é do chat do inquérito ou ninguém votou). Os totais trazem as mesmas chaves,
    contando cada voto uma vez mesmo que duas auditorias apontem para a mesma mensagem."""
    filtros = ["1=1"]
    params: dict = {"limite": limite, "deslocamento": deslocamento}
    if id_inquerito:
        filtros.append("a.id_inquerito = %(id_inquerito)s")
        params["id_inquerito"] = id_inquerito
    if id_conversa_ia:
        filtros.append("a.id_conversa_ia = %(id_conversa_ia)s")
        params["id_conversa_ia"] = id_conversa_ia
    if id_usuario:
        filtros.append("a.id_usuario = %(id_usuario)s")
        params["id_usuario"] = id_usuario
    if origem:
        filtros.append("a.no_origem = %(origem)s")
        params["origem"] = origem
    # DT_CHAMADA é TIMESTAMP sem fuso, gravado com now() em UTC, mas o "de/até" que o usuário
    # digita é o dia de São Paulo (UTC-3) que a tela mostra. Convertemos o LIMITE (e não a coluna)
    # para UTC: o dia fecha onde o usuário espera e o índice IX68 sobre DT_CHAMADA continua valendo.
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append(f"a.dt_chamada >= (%(data_de)s::timestamp {_PARA_UTC})")
        params["data_de"] = data_de
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append(f"a.dt_chamada < ((%(data_ate)s::date + 1)::timestamp {_PARA_UTC})")
        params["data_ate"] = data_ate
    if feedback:
        if feedback not in _FEEDBACKS_VALIDOS:
            raise ValueError("Filtro de avaliação inválido.")
        # voto só existe no chat do inquérito (ia_tb06_feedback aponta para IA_TB02_MENSAGENS);
        # com filtro ativo, as demais origens somem da lista em vez de poluírem o "sem avaliação"
        filtros.append("a.no_origem = 'chat_inquerito' AND a.id_mensagem IS NOT NULL")
        if feedback == "com_avaliacao":
            filtros.append(
                "EXISTS (SELECT 1 FROM investigacao.ia_tb06_feedback f"
                " WHERE f.id_mensagem_ia = a.id_mensagem)"
            )
        elif feedback == "sem_avaliacao":
            filtros.append(
                "NOT EXISTS (SELECT 1 FROM investigacao.ia_tb06_feedback f"
                " WHERE f.id_mensagem_ia = a.id_mensagem)"
            )
        elif feedback == "positivas":
            filtros.append(
                "EXISTS (SELECT 1 FROM investigacao.ia_tb06_feedback f"
                " WHERE f.id_mensagem_ia = a.id_mensagem AND f.tp_feedback = 'positivo')"
            )
        elif feedback == "negativas":
            filtros.append(
                "EXISTS (SELECT 1 FROM investigacao.ia_tb06_feedback f"
                " WHERE f.id_mensagem_ia = a.id_mensagem AND f.tp_feedback = 'negativo')"
            )
    where = " AND ".join(filtros)
    order_by = _ORDENACOES.get(ordenar, _ORDENACOES["data_desc"])

    query_itens = f"""
        SELECT a.id_auditoria, a.no_origem, a.id_inquerito, a.id_conversa_ia,
               u.no_usuario, a.no_provedor, a.no_modelo,
               left(a.tx_prompt, 200) AS tx_prompt_resumo,
               a.qt_chamadas_api, a.qt_ferramentas, a.qt_tokens_entrada, a.qt_tokens_saida,
               a.in_tokens_estimados, a.vl_custo_usd, a.nu_tempo_ms, a.cd_status, a.dt_chamada,
               a.qt_chars_contexto,
               COALESCE(fb.qt_pos, 0) AS qt_feedback_pos,
               COALESCE(fb.qt_neg, 0) AS qt_feedback_neg
        FROM investigacao.ia_tb04_auditoria a
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = a.id_usuario
        -- um voto por usuário e mensagem: o agregado nunca multiplica a linha (sem GROUP BY aqui)
        LEFT JOIN LATERAL (
            SELECT COUNT(*) FILTER (WHERE f.tp_feedback = 'positivo') AS qt_pos,
                   COUNT(*) FILTER (WHERE f.tp_feedback = 'negativo') AS qt_neg
            FROM investigacao.ia_tb06_feedback f
            WHERE a.no_origem = 'chat_inquerito' AND a.id_mensagem IS NOT NULL
              AND f.id_mensagem_ia = a.id_mensagem
        ) fb ON true
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %(limite)s OFFSET %(deslocamento)s
    """
    # Cada voto contado uma vez (EXISTS contra a mensagem), mesmo que dois registros de
    # auditoria apontem para a mesma resposta — somar o fb.qt_pos das linhas multiplicaria.
    where_a2 = where.replace("a.", "a2.")
    query_totais = f"""
        SELECT count(*) AS qt_total,
               coalesce(sum(a.qt_tokens_entrada), 0) AS qt_tokens_entrada,
               coalesce(sum(a.qt_tokens_saida), 0) AS qt_tokens_saida,
               sum(a.vl_custo_usd) AS vl_custo_usd,
               coalesce(sum(a.nu_tempo_ms), 0) AS nu_tempo_ms,
               (SELECT COUNT(*) FROM investigacao.ia_tb06_feedback f
                 WHERE f.tp_feedback = 'positivo'
                   AND EXISTS (SELECT 1 FROM investigacao.ia_tb04_auditoria a2
                               WHERE {where_a2} AND a2.no_origem = 'chat_inquerito'
                                 AND a2.id_mensagem = f.id_mensagem_ia)) AS qt_feedback_pos,
               (SELECT COUNT(*) FROM investigacao.ia_tb06_feedback f
                 WHERE f.tp_feedback = 'negativo'
                   AND EXISTS (SELECT 1 FROM investigacao.ia_tb04_auditoria a2
                               WHERE {where_a2} AND a2.no_origem = 'chat_inquerito'
                                 AND a2.id_mensagem = f.id_mensagem_ia)) AS qt_feedback_neg
        FROM investigacao.ia_tb04_auditoria a
        WHERE {where}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_itens, params)
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
            cur.execute(query_totais, params)
            colunas = [d.name for d in cur.description]
            totais = dict(zip(colunas, cur.fetchone()))
    qt_total = totais.pop("qt_total")
    return {"itens": itens, "totais": totais, "qt_total": qt_total}


def obter_auditoria(id_auditoria: int) -> dict | None:
    """Um registro completo de auditoria (prompt e contexto inteiros, ferramentas, documentos);
    None se não existir.

    No chat do inquérito, traz também os votos 👍/👎 da resposta deste turno
    (qt_feedback_pos/qt_feedback_neg + a lista `feedbacks` com quem votou o quê e quando)."""
    query = """
        SELECT a.*, u.no_usuario
        FROM investigacao.ia_tb04_auditoria a
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = a.id_usuario
        WHERE a.id_auditoria = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_auditoria,))
            linha = cur.fetchone()
            if not linha:
                return None
            colunas = [d.name for d in cur.description]
            registro = dict(zip(colunas, linha))
            registro["qt_feedback_pos"] = 0
            registro["qt_feedback_neg"] = 0
            registro["feedbacks"] = []
            # voto só existe no chat do inquérito (ia_tb06_feedback → IA_TB02_MENSAGENS); nas
            # demais origens id_mensagem aponta para outra tabela (ou é nulo) e o detalhe sai zerado
            if registro.get("no_origem") == "chat_inquerito" and registro.get("id_mensagem"):
                cur.execute(
                    """
                    SELECT u.no_usuario, f.tp_feedback, f.dt_criacao
                    FROM investigacao.ia_tb06_feedback f
                    LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = f.id_usuario
                    WHERE f.id_mensagem_ia = %s
                    ORDER BY f.dt_criacao, f.id_feedback
                    """,
                    (registro["id_mensagem"],),
                )
                for no_usuario, tp_feedback, dt_criacao in cur.fetchall():
                    registro["feedbacks"].append(
                        {
                            "no_usuario": no_usuario,
                            "tp_feedback": tp_feedback,
                            "dt_criacao": dt_criacao.isoformat() if dt_criacao else None,
                        }
                    )
                    if tp_feedback == "positivo":
                        registro["qt_feedback_pos"] += 1
                    elif tp_feedback == "negativo":
                        registro["qt_feedback_neg"] += 1
            return registro
