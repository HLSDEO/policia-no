"""Ferramentas (function calling) que a IA usa para responder perguntas do investigador no chat.

Não expõe as tabelas cruas: cada função aqui é uma consulta específica, sempre
isolada por ID_INQUERITO quando lida com dado extraído de celular (cadeia de
custódia — a IA nunca deve enxergar dado de uma investigação ao responder
sobre outra). A IA (via function calling) decide quais chamar e em que ordem;
FERRAMENTAS_IA/FUNCOES_IA no fim do arquivo é o que liga isso ao loop do
agente.

Duas fontes bem diferentes:
- Dado extraído (INVESTIGACAO.*): contato salvo no aparelho, apelido, mensagem
  trocada — busca por nome aqui é literal/substring, o universo é pequeno
  (um aparelho por vez).
- Dado de referência (DADOS_EXTRAIDOS.PES_TB01/TSE_TB01): 220M+ linhas, exige
  busca fuzzy (pg_trgm + dmetaphone, ver db/init/01_schemas.sql) já que
  apelido de agenda não bate com nome civil/nome de urna.
"""

import copy
import datetime
import re
import threading
import time
from functools import wraps
from zoneinfo import ZoneInfo

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import TIPOS_ORIGEM_VALIDOS, criar_entidade, criar_relacao, listar_entidades_inquerito
from funcoes.rag_hibrido import buscar_hibrida
from funcoes.relatorios_utils import listar_marcacoes_inquerito
from funcoes.simba_utils import resumo_credito_extrato

_SOMENTE_DIGITOS = re.compile(r"\D")

# Teto de registros que UMA chamada de ferramenta devolve, qualquer que seja o limite pedido pela
# IA (ver _executar_com_meta). Acima do maior padrão do arquivo (300), então nenhuma ferramenta
# muda de comportamento — é só a trava contra o pedido exagerado.
MAX_REGISTROS_FERRAMENTA = 500

# Teto do `limite` das buscas na base de referência (Serasa/IBGE). Alinhado com o de cima: acima
# do maior padrão em uso (20 na ferramenta, 15 no autocomplete), então nada muda de comportamento.
_LIMITE_MAX_REFERENCIA = 500

# Texto de análise do COAF numa comunicação de RIF: passa de 3 mil caracteres em média nos RIFs
# grandes (e chega a 20 mil). Numa listagem, o que interessa é reconhecer a operação; a íntegra
# vem quando a busca isola poucas comunicações — ver buscar_comunicacoes_rif_filtradas.
MAX_CHARS_ANALISE_RIF = 1_200
REGISTROS_COM_ANALISE_INTEGRA_RIF = 5

# Pedaço de documento que ler_documento_inquerito devolve por chamada. Um IPL grande passa de meio
# milhão de caracteres — mais que a janela de contexto de qualquer modelo aqui.
MAX_CHARS_PARTE_DOCUMENTO = 30_000


def _ttl_cache(ttl_seconds: int = 300, maxsize: int = 128):
    """Cache simples com TTL para funções com argumentos hasháveis.

    Usado nas ferramentas "trabalho já feito" (resumo_inquerito, pessoas identificadas, padrões,
    marcações, donos, contas do SIMBA) e no cabeçalho do panorama: dentro da janela de TTL, o
    resultado não é reconsultado no banco — o loop do agente chama essas ferramentas repetidamente
    na mesma conversa, e cada chamada economiza uma consulta pesada (contagens em tabelas de
    centenas de milhares de linhas, subqueries por entidade). TTL curto o bastante para uma
    extração nova ou uma edição do investigador aparecer em poucos minutos; corrida entre threads
    só re-executa a consulta (idempotente), nunca corrompe estado."""
    cache: dict = {}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            entry = cache.get(key)
            if entry and now - entry[1] < ttl_seconds:
                return entry[0]
            resultado = func(*args, **kwargs)
            if len(cache) >= maxsize:
                cache.clear()
            cache[key] = (resultado, now)
            return resultado
        return wrapper
    return decorator


def _normalizar_cpf(cpf: str | None) -> str | None:
    """Remove pontuação de um CPF; devolve None se vier vazio."""
    if not cpf:
        return None
    limpo = _SOMENTE_DIGITOS.sub("", cpf)
    return limpo or None


def _executar(query: str, params: dict) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _executar_com_meta(query_base: str, params: dict, limite: int) -> dict:
    """Executa a query com LIMIT e retorna metadados sobre truncamento.

    Retorna dict com:
    - registros: lista de resultados (até 'limite')
    - total_disponivel: quantidade total sem limite
    - mostrando: quantidade de registros retornados
    - truncado: bool indicando se há mais dados
    - aviso: string sugestiva se truncado

    O limite pedido é aparado em MAX_REGISTROS_FERRAMENTA: ele vem no schema das ferramentas, ou
    seja, quem escolhe o número é a IA — e ela pede o total que a pergunta sugere ("liste as 223
    comunicações"), sem noção do tamanho de cada registro. Um RIF com centenas de comunicações e
    3 mil caracteres de análise em cada uma vira, nesse pedido, mais texto do que a janela do
    modelo inteira. O corte fica aqui, num lugar só, valendo para toda ferramenta de busca.
    """
    limite = max(1, min(int(limite or 0) or MAX_REGISTROS_FERRAMENTA, MAX_REGISTROS_FERRAMENTA))
    if "limite" in params:
        params["limite"] = limite
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Contar total de registros (sem LIMIT) — usa \b para não confundir a
            # palavra-chave com o nome do parâmetro "limite" (que também contém "limit").
            limite_matches = list(re.finditer(r"\bLIMIT\b", query_base, re.IGNORECASE))
            if limite_matches:
                query_count = query_base[:limite_matches[-1].start()].rstrip()
            else:
                query_count = query_base
            query_count = f"SELECT count(*) as total FROM ({query_count}) AS subq"
            cur.execute(query_count, params)
            total = cur.fetchone()[0]

            # Executar query com LIMIT
            cur.execute(query_base, params)
            colunas = [d.name for d in cur.description]
            registros = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    mostrando = len(registros)
    truncado = mostrando < total
    aviso = f"Mostrando {mostrando} de {total} registros. Use filtros mais específicos para refinar a busca." if truncado else None

    return {
        "registros": registros,
        "total_disponivel": total,
        "mostrando": mostrando,
        "truncado": truncado,
        "aviso": aviso,
    }


# Tabelas de contato/mensagem por fonte de conversa extraída do UFDR (WhatsApp e SMS
# têm o mesmo formato — ver db/init/10_whatsapp.sql e 11_sms.sql; a
# fonte "msg" agrega todos os demais mensageiros — Instagram, Telegram, Facebook, Discord,
# TikTok, Twitter… — numa família só, com o app na coluna tp_app da conversa, ver 12_mensageiros.sql).
# A expressão de app (nome legível do mensageiro) é literal para WhatsApp/SMS e vem de conv.tp_app
# para os demais — assim a busca unificada mostra de qual app é cada mensagem.
_TABELAS_CONVERSA = {
    "wpp": {
        "contatos": "wpp_tb01_contatos", "conversas": "wpp_tb02_conversas",
        "participantes": "wpp_tb03_participantes", "mensagens": "wpp_tb04_mensagens",
        "anexos": "wpp_tb05_anexos", "coluna_identificador": "nu_whatsapp_id",
        "app": "'WhatsApp'",
    },
    "sms": {
        "contatos": "sms_tb01_contatos", "conversas": "sms_tb02_conversas",
        "participantes": "sms_tb03_participantes", "mensagens": "sms_tb04_mensagens",
        "anexos": "sms_tb05_anexos", "coluna_identificador": "nu_telefone",
        "app": "'SMS'",
    },
    "msg": {
        "contatos": "msg_tb01_contatos", "conversas": "msg_tb02_conversas",
        "participantes": "msg_tb03_participantes", "mensagens": "msg_tb04_mensagens",
        "anexos": "msg_tb05_anexos", "coluna_identificador": "nu_identificador",
        "app": "conv.tp_app",
    },
}

# Ordem de todas as fontes de conversa — usada quando a busca varre "todos" os apps.
_FONTES_TODAS = ["wpp", "sms", "msg"]


def buscar_contato_extracao(
    id_inquerito: int, termo: str, limite: int = 20, id_extracao: int | None = None
) -> list[dict]:
    """Procura um contato/apelido salvo no aparelho (WhatsApp, SMS ou agenda) de um inquérito.

    Busca nas três origens de uma vez (o investigador raramente sabe de onde vem
    o nome), com ranking por similaridade além do substring simples — cobre o
    caso de "MAIKE BOLINHA" batendo por ILIKE, mas também erro de digitação leve.

    Contatos de WhatsApp/SMS trazem também a associação manual do investigador
    (pessoa da base de referência e/ou local), quando existir — identificação já
    confirmada por humano vale mais que redescobrir por busca fuzzy.
    """
    termo = (termo or "").strip()
    if not termo:
        return []
    query = """
        WITH alvo AS (SELECT %(termo)s::text AS termo)
        SELECT 'whatsapp' AS origem, c.id_contato, c.no_contato AS nome,
               c.nu_whatsapp_id AS identificador,
               p.no_pessoa AS pessoa_associada, p.nu_cpf AS cpf_pessoa_associada,
               coalesce(mu.no_municipio, uf.no_uf) AS local_associado,
               similarity(coalesce(c.no_contato, ''), alvo.termo) AS score
        FROM investigacao.wpp_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        LEFT JOIN dados_extraidos.pes_tb01_pessoas p ON p.id_pessoa = c.id_pessoa_associada
        LEFT JOIN dados_referencia.ibge_tb08_municipio mu ON mu.id_municipio = c.id_municipio_associado
        LEFT JOIN dados_referencia.ibge_tb03_uf uf ON uf.id_uf = c.id_uf_associada, alvo
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND c.no_contato IS NOT NULL
          AND (c.no_contato ILIKE '%%' || alvo.termo || '%%' OR c.no_contato %% alvo.termo)

        UNION ALL

        SELECT 'sms', c.id_contato, c.no_contato, c.nu_telefone,
               p.no_pessoa, p.nu_cpf,
               coalesce(mu.no_municipio, uf.no_uf),
               similarity(coalesce(c.no_contato, ''), alvo.termo)
        FROM investigacao.sms_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        LEFT JOIN dados_extraidos.pes_tb01_pessoas p ON p.id_pessoa = c.id_pessoa_associada
        LEFT JOIN dados_referencia.ibge_tb08_municipio mu ON mu.id_municipio = c.id_municipio_associado
        LEFT JOIN dados_referencia.ibge_tb03_uf uf ON uf.id_uf = c.id_uf_associada, alvo
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND c.no_contato IS NOT NULL
          AND (c.no_contato ILIKE '%%' || alvo.termo || '%%' OR c.no_contato %% alvo.termo)

        UNION ALL

        SELECT 'agenda', c.id_contato, c.no_contato,
               (SELECT string_agg(en.tx_valor, ', ' ORDER BY en.tx_valor)
                  FROM investigacao.ctt_tb02_contato_entradas en WHERE en.id_contato = c.id_contato),
               NULL, NULL, NULL,
               similarity(coalesce(c.no_contato, ''), alvo.termo)
        FROM investigacao.ctt_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao, alvo
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND c.no_contato IS NOT NULL
          AND (c.no_contato ILIKE '%%' || alvo.termo || '%%' OR c.no_contato %% alvo.termo)

        ORDER BY score DESC
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def _select_mensagens_fonte(fonte: str) -> str:
    """Monta o SELECT de mensagens de UMA fonte, já no formato comum a todas (mesmas colunas e
    ordem), para poder ser combinado com UNION ALL entre fontes. Os parâmetros nomeados
    (id_inquerito, patterns, nome_contato) são os mesmos em todas as fontes — o psycopg os
    substitui em cada trecho."""
    t = _TABELAS_CONVERSA[fonte]
    return f"""
        SELECT '{fonte}' AS fonte, {t['app']} AS aplicativo,
               m.id_mensagem, m.id_conversa, m.tx_mensagem, m.dt_mensagem,
               m.in_enviada, m.cd_status_mensagem,
               remet.no_contato AS remetente,
               remet.{t['coluna_identificador']} AS remetente_identificador,
               (SELECT string_agg(a.tx_transcricao, ' | ')
                  FROM investigacao.{t['anexos']} a
                 WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL
               ) AS transcricao_audio
        FROM investigacao.{t['mensagens']} m
        JOIN investigacao.{t['conversas']} conv ON conv.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = conv.id_extracao
        LEFT JOIN investigacao.{t['contatos']} remet ON remet.id_contato = m.id_contato_remetente
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(id_conversa)s::bigint IS NULL OR m.id_conversa = %(id_conversa)s)
          AND (%(patterns)s::text[] IS NULL
               OR m.tx_mensagem ILIKE ANY(%(patterns)s)
               OR EXISTS (
                   SELECT 1 FROM investigacao.{t['anexos']} a
                   WHERE a.id_mensagem = m.id_mensagem
                     AND a.tx_transcricao ILIKE ANY(%(patterns)s)
               ))
          AND (
                %(nome_contato)s::text IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM investigacao.{t['participantes']} p
                    JOIN investigacao.{t['contatos']} ct ON ct.id_contato = p.id_contato
                    WHERE p.id_conversa = m.id_conversa
                      AND NOT coalesce(ct.in_dono_dispositivo, false)
                      AND ct.no_contato ILIKE '%%' || %(nome_contato)s::text || '%%'
                )
              )
    """


def buscar_mensagens(
    id_inquerito: int,
    termo: str | None = None,
    termos: list[str] | None = None,
    nome_contato: str | None = None,
    fonte: str | None = None,
    limite: int = 100,
    id_extracao: int | None = None,
    id_conversa: int | None = None,
) -> list[dict]:
    """Busca mensagens de um inquérito por conteúdo e/ou pelo nome do outro lado da conversa.

    Varre TODOS os apps de mensagem por padrão — WhatsApp, SMS e os demais mensageiros (Instagram,
    Telegram, Facebook, Discord, TikTok, Twitter…). Passe `fonte` só para restringir a um app:
    'wpp' (WhatsApp), 'sms' (SMS), 'msg' (demais mensageiros) ou 'todos'/None (padrão, tudo junto).

    Aceita `termo` (um) OU `termos` (vários numa chamada só, com OR entre eles) — use `termos` para
    varrer sinônimos e gírias de uma vez (ex.: ["pó", "bala", "verde", "bagulho"]) em vez de várias
    chamadas. `nome_contato` filtra pela conversa (participante), trazendo as duas pontas do diálogo.

    Cada termo é procurado no texto da mensagem E na transcrição dos áudios anexados (mensagem de voz
    tem tx_mensagem nulo — o conteúdo vem na transcrição, que também volta em `transcricao_audio`).
    Cada resultado traz `aplicativo` (de qual app é) e `referencia` (ex.: "[msg:wpp:123]",
    "[msg:msg:123]"): cite a referência na resposta para o investigador abrir a mensagem original.
    """
    if fonte is None or fonte in ("todos", "todas", ""):
        fontes = list(_FONTES_TODAS)
    elif fonte in _TABELAS_CONVERSA:
        fontes = [fonte]
    else:
        raise ValueError(f"Fonte inválida: {fonte!r} (use 'todos', 'wpp', 'sms' ou 'msg')")

    lista_termos = [t.strip() for t in (termos or []) if t and t.strip()]
    if not lista_termos and termo and termo.strip():
        lista_termos = [termo.strip()]
    patterns = ["%" + t + "%" for t in lista_termos] or None
    nome_contato = (nome_contato or "").strip() or None

    uniao = "\n        UNION ALL\n".join(_select_mensagens_fonte(f) for f in fontes)
    query = f"""
        SELECT * FROM (
            {uniao}
        ) u
        ORDER BY dt_mensagem DESC NULLS LAST
        LIMIT %(limite)s
    """
    resultados = _executar(
        query,
        {
            "id_inquerito": id_inquerito, "patterns": patterns, "nome_contato": nome_contato,
            "limite": limite, "id_extracao": id_extracao, "id_conversa": id_conversa,
        },
    )
    for r in resultados:
        r["referencia"] = f"[msg:{r['fonte']}:{r['id_mensagem']}]"
    return resultados


def buscar_ligacoes(
    id_inquerito: int,
    nome_contato: str | None = None,
    telefone: str | None = None,
    direcao: str | None = None,
    limite: int = 100,
    id_extracao: int | None = None,
) -> list[dict]:
    """Busca as ligações (telefone/WhatsApp) de um inquérito, opcionalmente filtrando por
    nome do contato, telefone ou direção (Incoming/Outgoing)."""
    nome_contato = (nome_contato or "").strip() or None
    telefone = (telefone or "").strip() or None
    direcao = (direcao or "").strip() or None
    query = """
        SELECT l.id_ligacao, l.no_aplicativo, l.tp_direcao, l.cd_status, l.dt_ligacao,
               l.qt_duracao_segundos, l.in_videochamada, l.nu_telefone_contato, l.no_contato
        FROM investigacao.lig_tb01_ligacoes l
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(nome_contato)s::text IS NULL OR l.no_contato ILIKE '%%' || %(nome_contato)s::text || '%%')
          AND (%(telefone)s::text IS NULL OR l.nu_telefone_contato ILIKE '%%' || %(telefone)s::text || '%%')
          AND (%(direcao)s::text IS NULL OR l.tp_direcao = %(direcao)s::text)
        ORDER BY l.dt_ligacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(
        query,
        {"id_inquerito": id_inquerito, "nome_contato": nome_contato, "telefone": telefone,
         "direcao": direcao, "limite": limite, "id_extracao": id_extracao},
    )


def listar_localizacoes(id_inquerito: int, limite: int = 100, id_extracao: int | None = None) -> list[dict]:
    """Lista as localizações registradas no aparelho (Waze, Maps etc.), mais recente primeiro."""
    query = """
        SELECT l.id_localizacao, l.no_aplicativo, l.dt_localizacao, l.vl_latitude, l.vl_longitude,
               l.tx_endereco, l.no_categoria, l.tx_descricao
        FROM investigacao.loc_tb01_localizacoes l
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
        ORDER BY l.dt_localizacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "limite": limite, "id_extracao": id_extracao})


def buscar_notificacoes(
    id_inquerito: int, termo: str | None = None, pacote_app: str | None = None, limite: int = 100,
    id_extracao: int | None = None,
) -> list[dict]:
    """Busca as notificações registradas no aparelho, por conteúdo e/ou pacote do app de origem
    (ex.: 'com.whatsapp') — útil pra saber que apps a pessoa usava e quando."""
    termo = (termo or "").strip() or None
    pacote_app = (pacote_app or "").strip() or None
    query = """
        SELECT n.id_notificacao, n.no_aplicativo_log, n.no_pacote_app, n.tx_conteudo, n.dt_notificacao
        FROM investigacao.ntf_tb01_notificacoes n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL OR n.tx_conteudo ILIKE '%%' || %(termo)s::text || '%%')
          AND (%(pacote_app)s::text IS NULL OR n.no_pacote_app ILIKE '%%' || %(pacote_app)s::text || '%%')
        ORDER BY n.dt_notificacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "pacote_app": pacote_app, "limite": limite, "id_extracao": id_extracao})


def buscar_notas(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Busca notas de blocos de notas do aparelho (Samsung Notes, Apple Notes/iCloud Backup, Google
    Keep etc.) por trecho do título ou corpo — costuma trazer anotações do tipo "caderno" (ex.:
    listas de quantidade/preço em casos de tráfico), então vale a pena buscar por termos do caso."""
    termo = (termo or "").strip() or None
    query = """
        SELECT n.id_nota, n.no_aplicativo, n.tx_titulo, n.tx_corpo, n.dt_criacao, n.dt_modificacao,
               n.cd_status
        FROM investigacao.not_tb01_notas n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR n.tx_titulo ILIKE '%%' || %(termo)s::text || '%%'
               OR n.tx_corpo ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY COALESCE(n.dt_modificacao, n.dt_criacao) DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_navegacao(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Busca o histórico de navegação (páginas visitadas no navegador) de um inquérito, por
    trecho do título ou da URL — mostra o que a pessoa acessou na web e quando."""
    termo = (termo or "").strip() or None
    query = """
        SELECT p.id_pagina, p.no_aplicativo, p.tx_titulo, p.tx_url, p.dt_visita, p.qt_visitas
        FROM investigacao.nav_tb01_paginas_visitadas p
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR p.tx_titulo ILIKE '%%' || %(termo)s::text || '%%'
               OR p.tx_url ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY p.dt_visita DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_aplicativos(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Lista os aplicativos instalados no aparelho de um inquérito, opcionalmente filtrando por
    trecho do pacote/nome (ex.: 'bank', 'whatsapp', 'vpn') — útil pra saber que apps a pessoa tinha."""
    termo = (termo or "").strip() or None
    query = """
        SELECT a.id_aplicativo, a.nu_pacote, a.no_aplicativo, a.nu_versao, a.dt_instalacao, a.dt_ultimo_uso
        FROM investigacao.app_tb01_aplicativos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR a.nu_pacote ILIKE '%%' || %(termo)s::text || '%%'
               OR a.no_aplicativo ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY a.nu_pacote NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


@_ttl_cache(ttl_seconds=300)
def listar_donos(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista o(s) dono(s)/usuário(s) do aparelho de um inquérito (identidade do titular:
    nome, tipo de usuário e último login)."""
    query = """
        SELECT d.id_dono, d.no_dono, d.tp_usuario, d.nu_identificador, d.dt_ultimo_login
        FROM investigacao.don_tb01_donos d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
        ORDER BY d.no_dono NULLS LAST
    """
    return _executar(query, {"id_inquerito": id_inquerito, "id_extracao": id_extracao})


def listar_documentos_inquerito(id_inquerito: int) -> list[dict]:
    """Lista os documentos anexados ao inquérito ("Extrair de PDF" na tela de Investigação —
    ex.: o PDF do próprio inquérito no ePol), com nome, tipo, tamanho do texto e erro se houver.

    in_ipl/in_rif/in_simba/in_irpf/in_ipei/in_telefonia marcam documentos que já têm bloco próprio
    no card de investigação (inquérito em PDF, PDF do RIF, síntese do SIMBA, declaração de IRPF,
    IPEI de quebra de sigilo fiscal, arquivo de operadora) — a tela usa isso para não renderizar o
    mesmo documento duas vezes. A IPEI aparece em duas colunas da mesma tabela (o PDF e a planilha
    de anexo), e o LEFT JOIN casa pelas duas: é assim que o anexo some da lista em vez de virar um
    card sozinho. A telefonia é o caso extremo disso — a resposta da operadora vem em vários
    arquivos e TODOS somem daqui, porque todos viraram um card só."""
    query = """
        SELECT d.id_documento, d.no_arquivo, d.tp_arquivo, d.qt_caracteres, d.tx_erro,
               d.dt_processado, u.no_usuario AS anexado_por,
               d.co_hash_md5, d.co_hash_sha1, d.co_hash_sha256,
               ap.no_apelido,
               d.cd_analise, d.dt_inicio_analise, d.dt_fim_analise, d.tx_erro_analise,
               d.tx_resumo_ia, d.jq_pessoas, d.jq_achados,
               (p.id_documento IS NOT NULL) AS in_ipl,
               (r.id_documento_pdf IS NOT NULL) AS in_rif,
               (s.id_documento_sintese IS NOT NULL) AS in_simba,
               (x.id_documento IS NOT NULL) AS in_irpf,
               (f.id_ipei IS NOT NULL) AS in_ipei,
               (tel.id_telefonia IS NOT NULL) AS in_telefonia
        FROM investigacao.inq_tb04_documentos d
        LEFT JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_documento = d.id_documento
        LEFT JOIN investigacao.rif_tb01_rifs r ON r.id_documento_pdf = d.id_documento
        LEFT JOIN investigacao.smb_tb01_extratos s ON s.id_documento_sintese = d.id_documento
        LEFT JOIN investigacao.irp_tb01_declaracoes x ON x.id_documento = d.id_documento
        LEFT JOIN investigacao.ipe_tb01_ipeis f
               ON f.id_documento = d.id_documento OR f.id_documento_anexo = d.id_documento
        LEFT JOIN investigacao.tel_tb02_arquivos tel ON tel.id_documento = d.id_documento
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = d.id_usuario
        -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
        LEFT JOIN investigacao.inq_tb06_apelidos ap
               ON ap.tp_prova = 'documento' AND ap.id_prova = d.id_documento
        WHERE d.id_inquerito = %(id_inquerito)s
        ORDER BY d.id_documento
    """
    return _executar(query, {"id_inquerito": id_inquerito})


def ler_documento_inquerito_completo(id_inquerito: int, id_documento: int) -> dict:
    """Texto INTEIRO do documento, sem partes — o que a tela do investigador mostra.

    A ferramenta da IA (abaixo) pagina porque a janela de contexto do modelo é finita; a tela não
    tem esse limite, e cortar o texto ali esconderia do investigador o que está no papel."""
    linha = _documento_do_inquerito(id_inquerito, id_documento)
    if "erro" in linha:
        return linha
    return {"conteudo": linha["conteudo"]}


def _documento_do_inquerito(id_inquerito: int, id_documento: int) -> dict:
    """{'conteudo': texto} do documento, ou {'erro': ...} se não existir/a extração ter falhado."""
    query = """
        SELECT tx_conteudo, tx_erro FROM investigacao.inq_tb04_documentos
        WHERE id_documento = %(id_documento)s AND id_inquerito = %(id_inquerito)s
    """
    linhas = _executar(query, {"id_documento": id_documento, "id_inquerito": id_inquerito})
    if not linhas:
        return {"erro": "Documento não encontrado neste inquérito."}
    if linhas[0]["tx_erro"]:
        return {"erro": linhas[0]["tx_erro"]}
    return {"conteudo": linhas[0]["tx_conteudo"] or ""}


def ler_documento_inquerito(id_inquerito: int, id_documento: int, parte: int = 1) -> dict:
    """Texto extraído de um documento anexado ao inquérito; erro descritivo se não existir ou a
    extração tiver falhado.

    Documento longo (um IPL de centenas de páginas passa de meio milhão de caracteres) volta em
    partes de MAX_CHARS_PARTE_DOCUMENTO caracteres — sem isso, um único documento ocupa a janela
    de contexto inteira e o provedor recusa o turno. `qt_partes` e `tem_mais` dizem se falta lê-lo:
    para continuar, chame de novo com parte=2, 3…

    O retorno traz id_documento e referencia ([doc:ID]) — cite a referencia na resposta para gerar
    o link clicável."""
    linha = _documento_do_inquerito(id_inquerito, id_documento)
    if "erro" in linha:
        return linha

    resultado = documento_em_partes(linha["conteudo"], parte)
    resultado["id_documento"] = id_documento
    resultado["referencia"] = f"[doc:{id_documento}]"
    return resultado


def documento_em_partes(conteudo: str, parte: int = 1) -> dict:
    """Fatia o texto de um documento em partes de MAX_CHARS_PARTE_DOCUMENTO para a IA ler.

    Compartilhada com o chat de Caso (funcoes/documentos_utils.py), que tem o mesmo problema com
    os documentos dele. Parte fora da faixa é aparada em vez de virar erro: a IA às vezes chama
    parte=4 num documento de 3 — devolver a última é mais útil do que uma mensagem de erro."""
    conteudo = conteudo or ""
    qt_partes = max(1, -(-len(conteudo) // MAX_CHARS_PARTE_DOCUMENTO))  # divisão para cima
    parte = max(1, min(int(parte or 1), qt_partes))
    inicio = (parte - 1) * MAX_CHARS_PARTE_DOCUMENTO
    trecho = conteudo[inicio:inicio + MAX_CHARS_PARTE_DOCUMENTO]
    if qt_partes == 1:
        return {"conteudo": trecho, "parte": 1, "qt_partes": 1, "tem_mais": False}
    return {
        "conteudo": trecho,
        "parte": parte,
        "qt_partes": qt_partes,
        "tem_mais": parte < qt_partes,
        "aviso": (
            f"Documento longo ({len(conteudo)} caracteres): esta é a parte {parte} de {qt_partes}. "
            f"Chame de novo com parte={parte + 1} para continuar."
            if parte < qt_partes else
            f"Parte {parte} de {qt_partes} (última) — as anteriores estão nas chamadas anteriores."
        ),
    }


def buscar_pessoas_ipl(id_inquerito: int, limite: int = 200, id_ipl: int | None = None) -> dict:
    """Lista pessoas citadas nos IPLs anexados ao inquérito (investigados, advogados, delegados, etc.).

    Retorna nome, CPF/CNPJ, papel na investigação, folha da primeira citação, número de menções e
    contexto da citação. Use para saber quem é mencionado no inquérito e em qual contexto.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Cada registro traz id_ipl, id_documento e referencia ([doc:ID]) — cite a referencia na resposta
    para o investigador abrir o documento original. nu_folha é a folha do processo (rodapé do papel),
    NÃO a página do arquivo: para link com página ([doc:ID:N]), confirme o N nas linhas [[página N]]
    via ler_documento_inquerito; sem isso, cite só [doc:ID].

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT p.id_pessoa_ipl, i.id_ipl, i.id_documento, i.nu_ipl as ipl_numero, p.no_pessoa, p.no_variantes, p.nu_cpf_cnpj,
               p.no_vulgo, p.tp_papel, p.nu_folha, p.qt_mencoes, p.tx_contexto
        FROM investigacao.ipl_tb03_pessoas p
        JOIN investigacao.ipl_tb01_inqueritos_pdf i ON i.id_ipl = p.id_ipl
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipl)s::bigint IS NULL OR i.id_ipl = %(id_ipl)s)
        ORDER BY p.qt_mencoes DESC, p.no_pessoa
        LIMIT %(limite)s
    """
    resultado = _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipl": id_ipl}, limite)
    for r in resultado.get("registros", []):
        r["referencia"] = f"[doc:{r['id_documento']}]" if r.get("id_documento") else None
    return resultado


def buscar_achados_ipl(
    id_inquerito: int, tipo_achado: str | None = None, limite: int = 200, id_ipl: int | None = None
) -> dict:
    """Lista achados (telefones, e-mails, valores, contas, endereços, processos, tipificações)
    encontrados nos IPLs anexados ao inquérito.

    Se tipo_achado for informado, filtra por tipo específico (telefone, email, valor, conta,
    agencia, processo, procedimento, tipificacao, endereco, oab).
    Retorna o valor, número de ocorrências, folha da primeira ocorrência e contexto.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Cada registro traz id_ipl, id_documento e referencia ([doc:ID]) — cite a referencia na resposta
    para o investigador abrir o documento original. nu_folha é a folha do processo (rodapé do papel),
    NÃO a página do arquivo: para link com página ([doc:ID:N]), confirme o N nas linhas [[página N]]
    via ler_documento_inquerito; sem isso, cite só [doc:ID].

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT a.id_achado_ipl, i.id_ipl, i.id_documento, i.nu_ipl as ipl_numero, a.tp_achado, a.tx_valor, a.vl_numerico,
               a.nu_folha, a.qt_ocorrencias, a.tx_contexto
        FROM investigacao.ipl_tb04_achados a
        JOIN investigacao.ipl_tb01_inqueritos_pdf i ON i.id_ipl = a.id_ipl
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipl)s::bigint IS NULL OR i.id_ipl = %(id_ipl)s)
    """
    params = {"id_inquerito": id_inquerito, "limite": limite, "id_ipl": id_ipl}
    if tipo_achado:
        query += " AND a.tp_achado = %(tipo_achado)s"
        params["tipo_achado"] = tipo_achado
    query += " ORDER BY a.qt_ocorrencias DESC, a.tp_achado, a.tx_valor LIMIT %(limite)s"
    resultado = _executar_com_meta(query, params, limite)
    for r in resultado.get("registros", []):
        r["referencia"] = f"[doc:{r['id_documento']}]" if r.get("id_documento") else None
    return resultado


def obter_declaracao_irpf(id_inquerito: int, id_irpf: int | None = None) -> dict:
    """Identificação do contribuinte e resumo (rendimentos, imposto, evolução patrimonial) das
    declarações de IRPF anexadas ao inquérito.

    Evolução patrimonial (bens e dívidas em 31/12 do ano anterior e do atual) é o sinal mais
    forte para apuração patrimonial — compare com os rendimentos declarados. Se id_irpf não for
    informado, devolve todas as declarações anexadas ao inquérito.

    Retorna dict com: registros (uma linha por declaração)."""
    query = """
        SELECT p.id_irpf, p.no_titular, p.nu_cpf, p.dt_nascimento, p.nu_titulo_eleitoral,
               p.tx_endereco, p.no_municipio, p.sg_uf, p.tx_natureza_ocupacao,
               p.tx_ocupacao_principal, p.tx_tipo_declaracao, p.nu_exercicio, p.nu_ano_calendario,
               p.vl_rendimentos_tributaveis, p.vl_imposto_devido, p.vl_imposto_restituir,
               p.vl_saldo_pagar, p.vl_bens_ano_anterior, p.vl_bens_ano_atual,
               p.vl_dividas_ano_anterior, p.vl_dividas_ano_atual,
               p.qt_bens, p.qt_dependentes, p.qt_rendimentos, p.qt_dividas, p.qt_doacoes
        FROM investigacao.irp_tb01_declaracoes p
        WHERE p.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR p.id_irpf = %(id_irpf)s)
        ORDER BY p.dt_extracao DESC
    """
    return {"registros": _executar(query, {"id_inquerito": id_inquerito, "id_irpf": id_irpf})}


def buscar_bens_direitos_irpf(id_inquerito: int, limite: int = 200, id_irpf: int | None = None) -> dict:
    """Lista bens e direitos declarados nas declarações de IRPF anexadas ao inquérito (imóveis,
    veículos, contas, aplicações financeiras...).

    Retorna código/discriminação do bem e o valor declarado em 31/12 do ano anterior e do ano
    atual — a diferença entre os dois é a variação patrimonial daquele bem no ano.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT b.id_bem, i.nu_cpf AS irpf_cpf, b.no_codigo, b.tx_discriminacao,
               b.vl_ano_anterior, b.vl_ano_atual
        FROM investigacao.irp_tb02_bens_direitos b
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = b.id_irpf
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR i.id_irpf = %(id_irpf)s)
        ORDER BY b.vl_ano_atual DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_irpf": id_irpf}, limite)


def buscar_dependentes_irpf(id_inquerito: int, limite: int = 200, id_irpf: int | None = None) -> dict:
    """Lista dependentes declarados nas declarações de IRPF anexadas ao inquérito (nome, CPF,
    relação de dependência, data de nascimento) — útil para mapear vínculos familiares.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT d.id_dependente, i.nu_cpf AS irpf_cpf, d.no_dependente, d.nu_cpf, d.tx_relacao,
               d.dt_nascimento
        FROM investigacao.irp_tb03_dependentes d
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR i.id_irpf = %(id_irpf)s)
        ORDER BY d.no_dependente
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_irpf": id_irpf}, limite)


def buscar_rendimentos_irpf(id_inquerito: int, limite: int = 200, id_irpf: int | None = None) -> dict:
    """Lista rendimentos recebidos de pessoa jurídica (titular e dependentes) nas declarações de
    IRPF anexadas ao inquérito — fonte pagadora, CNPJ/CPF e valor recebido no ano.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT r.id_rendimento, i.nu_cpf AS irpf_cpf, r.tp_origem, r.no_fonte_pagadora,
               r.nu_cnpj_cpf, r.vl_rendimento
        FROM investigacao.irp_tb04_rendimentos r
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = r.id_irpf
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR i.id_irpf = %(id_irpf)s)
        ORDER BY r.vl_rendimento DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_irpf": id_irpf}, limite)


def buscar_dividas_irpf(id_inquerito: int, limite: int = 200, id_irpf: int | None = None) -> dict:
    """Lista dívidas e ônus reais declarados nas declarações de IRPF anexadas ao inquérito.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT d.id_divida, i.nu_cpf AS irpf_cpf, d.no_codigo, d.tx_discriminacao,
               d.vl_ano_anterior, d.vl_ano_atual
        FROM investigacao.irp_tb05_dividas d
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR i.id_irpf = %(id_irpf)s)
        ORDER BY d.vl_ano_atual DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_irpf": id_irpf}, limite)


def buscar_doacoes_irpf(id_inquerito: int, limite: int = 200, id_irpf: int | None = None) -> dict:
    """Lista doações (efetuadas e a partidos políticos/candidatos) nas declarações de IRPF
    anexadas ao inquérito — beneficiário, CNPJ/CPF e valor doado.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT d.id_doacao, i.nu_cpf AS irpf_cpf, d.tp_doacao, d.no_beneficiario, d.nu_cnpj_cpf,
               d.vl_doacao
        FROM investigacao.irp_tb06_doacoes d
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_irpf)s::bigint IS NULL OR i.id_irpf = %(id_irpf)s)
        ORDER BY d.vl_doacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_irpf": id_irpf}, limite)


def obter_ipei(id_inquerito: int, id_ipei: int | None = None) -> dict:
    """Cabeçalho das IPEIs (quebra de sigilo fiscal da Receita Federal) anexadas ao inquérito e os
    contribuintes analisados em cada uma, com dados cadastrais.

    Uma IPEI de pessoa física traz um alvo; uma de pessoa jurídica traz várias empresas no mesmo
    PDF. Se id_ipei não for informado, devolve todas as IPEIs anexadas ao inquérito.

    Retorna dict com: registros (uma linha por alvo, com o cabeçalho da IPEI repetido)."""
    query = """
        SELECT i.id_ipei, i.nu_ipei, i.dt_ipei, i.no_unidade, i.nu_processo_judicial, i.dt_decisao,
               i.tx_conclusao, i.no_arquivo, i.no_arquivo_anexo, i.id_documento,
               a.id_alvo, a.tp_pessoa, a.no_alvo, a.nu_cpf_cnpj, a.no_mae, a.dt_nascimento,
               a.tx_endereco, a.no_municipio, a.sg_uf, a.tx_ocupacao, a.tx_situacao_cadastral,
               a.vl_capital_social, a.no_contador
        FROM investigacao.ipe_tb01_ipeis i
        LEFT JOIN investigacao.ipe_tb02_alvos a ON a.id_ipei = i.id_ipei
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
        ORDER BY i.nu_ipei, a.id_alvo
    """
    resultado = {"registros": _executar(query, {"id_inquerito": id_inquerito, "id_ipei": id_ipei})}
    for r in resultado["registros"]:
        r["referencia"] = f"[doc:{r['id_documento']}]" if r.get("id_documento") else None
    return resultado


def buscar_relacionados_ipei(id_inquerito: int, limite: int = 300, id_ipei: int | None = None,
                             tipo_relacao: str | None = None) -> dict:
    """Vínculos levantados na IPEI: sócios e responsáveis legais das empresas do alvo, empregadores
    do CNIS, outorgantes/outorgados de procuração (CENSEC e RFB) e — o achado mais forte para ligar
    investigados entre si — quem transmitiu declaração de imposto do MESMO endereço MAC que o alvo.

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT r.id_relacionado, i.nu_ipei, a.no_alvo, a.nu_cpf_cnpj AS cpf_cnpj_alvo,
               r.tp_relacao, r.no_relacionado, r.nu_cpf_cnpj, r.tx_qualificacao,
               r.dt_inicio, r.dt_fim, r.pc_participacao, r.tx_contexto
        FROM investigacao.ipe_tb03_relacionados r
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = r.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = r.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
          AND (%(tipo_relacao)s::text IS NULL OR r.tp_relacao = %(tipo_relacao)s)
        ORDER BY r.tp_relacao, r.no_relacionado
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei,
                "tipo_relacao": tipo_relacao}, limite)


def buscar_anos_ipei(id_inquerito: int, limite: int = 200, id_ipei: int | None = None) -> dict:
    """Consolidado ano a ano de cada alvo da IPEI: rendimentos declarados, receita bruta,
    movimentação financeira da e-Financeira, DECRED, notas fiscais e variação patrimonial.

    vl_razao_mov_rend é a movimentação financeira dividida pelo rendimento declarado — é o
    indicador central de incompatibilidade patrimonial do relatório (um salto de 1,47 para 94,23
    entre anos é o tipo de coisa que ele existe para mostrar).

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT n.id_ano, i.nu_ipei, a.no_alvo, a.nu_cpf_cnpj, n.nu_ano,
               n.vl_rend_tributaveis, n.vl_rend_isentos, n.vl_rend_exclusiva, n.vl_rend_total,
               n.vl_receita_bruta, n.vl_dirf_total, n.vl_ecf_total, n.vl_mov_creditos,
               n.vl_razao_mov_rend, n.vl_bens_ano, n.vl_bens_ano_anterior,
               n.vl_variacao_patrimonial, n.vl_dif_transposicao, n.vl_decred,
               n.vl_nfe_compras, n.vl_nfe_vendas, n.vl_nfs_servicos, n.vl_tributos_pagos
        FROM investigacao.ipe_tb04_anos n
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = n.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = n.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
        ORDER BY a.no_alvo, n.nu_ano
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei}, limite)


def buscar_fontes_ipei(id_inquerito: int, limite: int = 200, id_ipei: int | None = None) -> dict:
    """Fontes pagadoras e pagamentos declarados na IPEI — quem pagou ao alvo e a quem ele pagou,
    por ano, com CNPJ/CPF e valor.

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT f.id_fonte, i.nu_ipei, a.no_alvo, f.tp_registro, f.nu_ano, f.no_fonte,
               f.nu_cpf_cnpj, f.tx_natureza, f.vl_rendimento, f.vl_imposto
        FROM investigacao.ipe_tb05_fontes f
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = f.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = f.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
        ORDER BY f.vl_rendimento DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei}, limite)


def buscar_contas_ipei(id_inquerito: int, limite: int = 300, id_ipei: int | None = None,
                       ano: int | None = None) -> dict:
    """Contas bancárias informadas à Receita Federal pela e-Financeira, com crédito, débito e saldo
    mês a mês (as do corpo do PDF) e a relação de contas da planilha de anexo.

    Estes são MONTANTES MENSAIS, não lançamentos: a IPEI não traz quem pagou a quem. Para o
    extrato lançamento a lançamento é preciso a quebra de sigilo bancário (SIMBA).

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT c.id_conta, i.nu_ipei, a.no_alvo, c.tp_conta, c.no_banco, c.nu_conta,
               c.qt_titulares, c.nu_ano, c.nu_mes, c.vl_creditos, c.vl_debitos, c.vl_saldo,
               c.no_titular, c.nu_titular, c.tx_relacao
        FROM investigacao.ipe_tb06_contas c
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = c.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
          AND (%(ano)s::integer IS NULL OR c.nu_ano = %(ano)s)
        ORDER BY c.vl_creditos DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei, "ano": ano}, limite)


def buscar_bens_ipei(id_inquerito: int, limite: int = 200, id_ipei: int | None = None,
                     tipo_bem: str | None = None) -> dict:
    """Bens ligados ao alvo na IPEI: os declarados no IRPF (com valor em 31/12), os veículos do
    RENAVAM (placa e restrição), imóveis da DOI/Dimob, embarcações, aeronaves e criptoativos.

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT b.id_bem, i.nu_ipei, a.no_alvo, b.tp_bem, b.nu_ano, b.nu_identificador,
               b.tx_descricao, b.vl_ano_anterior, b.vl_ano, b.tx_situacao
        FROM investigacao.ipe_tb07_bens b
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = b.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = b.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
          AND (%(tipo_bem)s::text IS NULL OR b.tp_bem = %(tipo_bem)s)
        ORDER BY b.tp_bem, b.vl_ano DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei, "tipo_bem": tipo_bem}, limite)


def buscar_notas_ipei(id_inquerito: int, limite: int = 200, id_ipei: int | None = None,
                      termo: str | None = None) -> dict:
    """Notas fiscais eletrônicas destinadas ao alvo — o que ele comprou, de quem, quando e por
    quanto. Vêm do corpo do PDF e da planilha de anexo, que costuma trazer a lista completa.

    `termo` filtra pela descrição do produto ou pelo nome do emitente (ex.: "arma", "veiculo",
    "joia") — é assim que se acha compra incompatível com a renda declarada.

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT n.id_nota, i.nu_ipei, a.no_alvo, n.tp_origem, n.dt_emissao, n.nu_nota,
               n.no_emitente, n.nu_cnpj_emitente, n.no_destinatario, n.tx_descricao,
               n.qt_item, n.vl_unitario, n.vl_total
        FROM investigacao.ipe_tb08_notas_fiscais n
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = n.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = n.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
          AND (%(termo)s::text IS NULL
               OR n.tx_descricao ILIKE '%%' || %(termo)s || '%%'
               OR n.no_emitente ILIKE '%%' || %(termo)s || '%%')
        ORDER BY n.vl_total DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei, "termo": termo}, limite)


def buscar_contatos_ipei(id_inquerito: int, limite: int = 300, id_ipei: int | None = None,
                         tipo_contato: str | None = None) -> dict:
    """Endereços, telefones, e-mails e endereços MAC/IP do alvo levantados em outras bases da RFB,
    por ano da informação — útil para cruzar com o que aparece no aparelho apreendido.

    Retorna dict com: registros, total_encontrado, limite_aplicado."""
    query = """
        SELECT c.id_contato, i.nu_ipei, a.no_alvo, c.tp_contato, c.tx_valor, c.nu_ano
        FROM investigacao.ipe_tb09_contatos c
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = c.id_alvo
        WHERE i.id_inquerito = %(id_inquerito)s
          AND (%(id_ipei)s::bigint IS NULL OR i.id_ipei = %(id_ipei)s)
          AND (%(tipo_contato)s::text IS NULL OR c.tp_contato = %(tipo_contato)s)
        ORDER BY c.tp_contato, c.nu_ano DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_ipei": id_ipei,
                "tipo_contato": tipo_contato}, limite)


@_ttl_cache(ttl_seconds=300)
def listar_contas_simba(id_inquerito: int, limite: int = 100, id_extrato: int | None = None) -> dict:
    """Contas bancárias afastadas nos extratos do SIMBA (relatório R304) anexados ao inquérito.

    Uma linha por conta, já com o total de crédito, o total de débito, a quantidade de lançamentos
    e o período coberto — é o retrato de quanto passou por cada conta de cada investigado.
    Limitado a 100 registros por padrão para evitar contextos muito grandes.

    ATENÇÃO: vl_credito/vl_debito são o total BRUTO da conta — incluem transferência entre contas
    do próprio titular, aplicação financeira automática (e o resgate/rendimento correspondente) e
    estorno/devolução. NÃO use esses campos para responder "quanto X recebeu/pagou de terceiros" —
    para isso use resumo_credito_simba (a quebra certa) ou listar_contrapartes_simba com
    in_titular_no_extrato=false.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    query = """
        SELECT c.id_conta_simba, s.no_arquivo, c.no_titular, c.nu_cpf_cnpj, c.tp_pessoa,
               c.no_banco, c.nu_agencia, c.nu_conta, c.tp_conta, c.dt_abertura, c.dt_encerramento,
               c.qt_lancamentos, c.vl_credito, c.vl_debito,
               c.dt_primeiro_lancamento, c.dt_ultimo_lancamento
        FROM investigacao.smb_tb02_contas c
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = c.id_extrato
        WHERE s.id_inquerito = %(id_inquerito)s
          AND (%(id_extrato)s::bigint IS NULL OR s.id_extrato = %(id_extrato)s)
        ORDER BY c.vl_credito + c.vl_debito DESC
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "limite": limite, "id_extrato": id_extrato}, limite)


# ---------------------------------------------------------------------------
# Telefonia (retorno da operadora na quebra de sigilo telefônico)
# ---------------------------------------------------------------------------
# Todas aceitam id_telefonia para prender a busca a UMA operadora — é o que a conversa de escopo
# usa. Sem ele, a busca varre todas as operadoras do inquérito, que é o que a conversa do
# inquérito inteiro quer.


def obter_telefonia_operadora(id_inquerito: int, id_telefonia: int | None = None) -> dict:
    """O que foi importado da operadora: quais arquivos, que período cobrem, qual linha foi pedida
    no ofício e quantas linhas cadastrais, chamadas, antenas, números e sessões de dados vieram.

    É por aqui que se começa: diz o que existe antes de sair buscando.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT t.id_telefonia, t.no_operadora, t.qt_arquivos, t.qt_linhas, t.qt_chamadas,
               t.qt_erbs, t.qt_numeros, t.qt_sessoes, t.qt_dados,
               (SELECT string_agg(DISTINCT a.nu_linha_alvo, ', ')
                  FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS linhas_pedidas_no_oficio,
               (SELECT min(a.dt_inicio_periodo) FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS inicio_periodo,
               (SELECT max(a.dt_fim_periodo) FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS fim_periodo,
               (SELECT string_agg(a.no_arquivo, ', ' ORDER BY a.id_arquivo_telefonia)
                  FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS arquivos
        FROM investigacao.tel_tb01_telefonia t
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
        ORDER BY t.no_operadora
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "id_telefonia": id_telefonia, "limite": 50}, 50)


def buscar_linhas_telefonia(id_inquerito: int, limite: int = 200, id_telefonia: int | None = None,
                            termo: str | None = None) -> dict:
    """Fichas cadastrais das linhas que a operadora devolveu: titular, CPF, RG, endereço completo,
    modalidade (pré/pós), situação e as datas de habilitação e rescisão.

    `termo` casa com o número da linha, o nome do titular ou o CPF — é assim que se responde
    "de quem é este número" e "que outras linhas estão no nome dele".

    A mesma linha aparece várias vezes quando trocou de titular ou de endereço: cada ficha é um
    contrato, e a sequência de habilitação/rescisão é o histórico daquele número.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT l.id_linha_telefonia, t.no_operadora, l.nu_linha, l.no_cliente, l.nu_cpf_cnpj,
               l.nu_rg, l.tx_endereco, l.no_bairro, l.no_municipio, l.sg_uf, l.nu_cep,
               l.tp_modalidade, l.tp_situacao, l.dt_habilitacao, l.dt_rescisao
        FROM investigacao.tel_tb03_linhas l
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = l.id_telefonia
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
          AND (%(termo)s::text IS NULL
               OR l.nu_linha ILIKE '%%' || %(termo)s || '%%'
               OR l.no_cliente ILIKE '%%' || %(termo)s || '%%'
               OR l.nu_cpf_cnpj ILIKE '%%' || %(termo)s || '%%')
        ORDER BY l.nu_linha, l.dt_habilitacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_telefonia": id_telefonia,
                "termo": termo}, limite)


def buscar_chamadas_telefonia(id_inquerito: int, limite: int = 300, id_telefonia: int | None = None,
                              numero: str | None = None, cgi: str | None = None,
                              data_de: str | None = None, data_ate: str | None = None,
                              so_completadas: bool = False) -> dict:
    """Registros de chamada (CDR) da operadora: quando, entre quais números, por quanto tempo, com
    qual IMEI de cada lado e por qual antena (já com o endereço da ERB resolvido pelo CGI).

    `numero` traz as chamadas de um telefone específico, esteja ele chamando ou sendo chamado.
    `cgi` traz as chamadas que passaram por uma antena específica (o CGI da ERB, na grafia da
    relação de antenas — cobre também a grafia curta central+ERB+setor que o CDR usa). É assim que
    se responde "qual linha usou esta antena".
    `data_de`/`data_ate` recortam o período (AAAA-MM-DD). `so_completadas` descarta as tentativas
    que não completaram — que são a maioria do CDR e costumam poluir a leitura.

    O endereço da antena é o que liga a chamada a um lugar: é assim que se mostra onde o
    investigado estava na hora da ligação.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT c.id_chamada_telefonia, t.no_operadora, c.tp_registro, c.dt_chamada,
               c.nu_chamador, c.nu_imei_chamador, c.nu_chamado, c.nu_imei_chamado,
               c.qt_duracao_segundos, c.cd_status, c.tp_trafego,
               c.nu_cgi_origem, c.tp_tecnologia_origem,
               eo.tx_endereco AS endereco_antena_origem, eo.no_municipio AS municipio_antena_origem,
               eo.sg_uf AS uf_antena_origem,
               c.nu_cgi_destino, c.tp_tecnologia_destino,
               ed.tx_endereco AS endereco_antena_destino, ed.no_municipio AS municipio_antena_destino,
               ed.sg_uf AS uf_antena_destino
        FROM investigacao.tel_tb04_chamadas c
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
        LEFT JOIN investigacao.tel_tb05_erbs eo
               ON eo.id_telefonia = c.id_telefonia AND eo.nu_cgi = c.nu_cgi_origem
        LEFT JOIN investigacao.tel_tb05_erbs ed
               ON ed.id_telefonia = c.id_telefonia AND ed.nu_cgi = c.nu_cgi_destino
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
          AND (%(numero)s::text IS NULL
               OR c.nu_chamador ILIKE '%%' || %(numero)s || '%%'
               OR c.nu_chamado ILIKE '%%' || %(numero)s || '%%')
          AND (%(cgi)s::text IS NULL
               OR c.nu_cgi_origem = %(cgi)s OR c.nu_cgi_destino = %(cgi)s
               OR EXISTS (
                    SELECT 1 FROM investigacao.tel_tb05_erbs e
                     WHERE e.id_telefonia = c.id_telefonia AND e.nu_cgi = %(cgi)s
                       AND NULLIF(e.nu_ccc, '') IS NOT NULL AND NULLIF(e.no_erb, '') IS NOT NULL
                       AND e.nu_setor IS NOT NULL
                       AND (c.nu_cgi_origem = e.nu_ccc || ' ' || e.no_erb || ' ' || e.nu_setor::text
                            OR c.nu_cgi_destino = e.nu_ccc || ' ' || e.no_erb || ' ' || e.nu_setor::text)))
          AND (%(data_de)s::date IS NULL OR c.dt_chamada >= %(data_de)s::date)
          AND (%(data_ate)s::date IS NULL OR c.dt_chamada < (%(data_ate)s::date + 1))
          AND (NOT %(so_completadas)s OR c.cd_status ILIKE 'complet%%')
        ORDER BY c.dt_chamada DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_telefonia": id_telefonia,
                "numero": numero, "cgi": cgi, "data_de": data_de, "data_ate": data_ate,
                "so_completadas": bool(so_completadas)}, limite)


def buscar_erbs_telefonia(id_inquerito: int, limite: int = 200, id_telefonia: int | None = None,
                          termo: str | None = None, numero: str | None = None) -> dict:
    """As antenas (ERBs) que aparecem no CDR, com endereço, município, UF, coordenada e quantas
    chamadas passaram por cada uma — e, quando a operadora identifica a linha pedida no ofício,
    quais dessas linhas (terminais) usaram cada antena (`linhas_alvo`).

    `termo` casa com o CGI, a cidade, o bairro ou o endereço. `numero` restringe às antenas por
    onde uma linha específica passou (chamando, sendo chamada ou trafegando dados). Ordenadas por
    movimento: a antena com muitas chamadas é onde o investigado fica, e a com uma só é passagem —
    essa diferença é o que responde "onde ele mora/trabalha" e "onde ele esteve naquele dia".

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT e.id_erb, t.no_operadora, e.nu_cgi, e.sg_erb, e.nu_setor, e.nu_azimute,
               e.tp_tecnologia, e.vl_latitude, e.vl_longitude, e.sg_uf, e.no_municipio,
               e.no_bairro, e.tx_endereco,
               (SELECT count(*) FROM investigacao.tel_tb04_chamadas c
                 WHERE c.id_telefonia = e.id_telefonia
                   AND (c.nu_cgi_origem = e.nu_cgi OR c.nu_cgi_destino = e.nu_cgi)) AS qt_chamadas,
               (SELECT string_agg(DISTINCT a.nu_linha_alvo, ', ')
                  FROM investigacao.tel_tb04_chamadas c
                  JOIN investigacao.tel_tb02_arquivos a ON a.id_arquivo_telefonia = c.id_arquivo_telefonia
                 WHERE c.id_telefonia = e.id_telefonia
                   AND (c.nu_cgi_origem = e.nu_cgi OR c.nu_cgi_destino = e.nu_cgi)
                   AND a.nu_linha_alvo IS NOT NULL) AS linhas_alvo
        FROM investigacao.tel_tb05_erbs e
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
          AND (%(termo)s::text IS NULL
               OR e.nu_cgi ILIKE '%%' || %(termo)s || '%%'
               OR e.no_municipio ILIKE '%%' || %(termo)s || '%%'
               OR e.no_bairro ILIKE '%%' || %(termo)s || '%%'
               OR e.tx_endereco ILIKE '%%' || %(termo)s || '%%')
          AND (%(numero)s::text IS NULL
               OR EXISTS (
                    SELECT 1 FROM investigacao.tel_tb04_chamadas c
                     WHERE c.id_telefonia = e.id_telefonia
                       AND (c.nu_cgi_origem = e.nu_cgi OR c.nu_cgi_destino = e.nu_cgi)
                       AND (c.nu_chamador ILIKE '%%' || %(numero)s || '%%'
                            OR c.nu_chamado ILIKE '%%' || %(numero)s || '%%'))
               OR EXISTS (
                    SELECT 1 FROM investigacao.tel_tb08_dados_moveis d
                     WHERE d.id_telefonia = e.id_telefonia
                       AND d.nu_cgi = e.nu_cgi
                       AND d.nu_linha ILIKE '%%' || %(numero)s || '%%'))
        ORDER BY qt_chamadas DESC, e.nu_cgi
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_telefonia": id_telefonia,
                "termo": termo, "numero": numero}, limite)


def buscar_numeros_telefonia(id_inquerito: int, limite: int = 200, id_telefonia: int | None = None,
                             numero: str | None = None, imei: str | None = None) -> dict:
    """Os números que trocaram chamada com a linha investigada, cada um com o IMEI do aparelho e
    quantas chamadas fez — o ranking de interlocutores.

    `imei` acha os números que usaram o MESMO aparelho: dois chips no mesmo IMEI é um dos achados
    mais fortes desta base, porque liga um número novo a alguém já identificado.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT n.id_numero_telefonia, t.no_operadora, n.nu_telefone, n.nu_imei,
               (SELECT max(l.no_cliente) FROM investigacao.tel_tb03_linhas l
                 WHERE l.id_telefonia = n.id_telefonia AND l.nu_linha = n.nu_telefone) AS titular,
               (SELECT count(*) FROM investigacao.tel_tb04_chamadas c
                 WHERE c.id_telefonia = n.id_telefonia
                   AND (c.nu_chamador = n.nu_telefone OR c.nu_chamado = n.nu_telefone)) AS qt_chamadas
        FROM investigacao.tel_tb06_numeros n
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = n.id_telefonia
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
          AND (%(numero)s::text IS NULL OR n.nu_telefone ILIKE '%%' || %(numero)s || '%%')
          AND (%(imei)s::text IS NULL OR n.nu_imei ILIKE '%%' || %(imei)s || '%%')
        ORDER BY qt_chamadas DESC, n.nu_telefone
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_telefonia": id_telefonia,
                "numero": numero, "imei": imei}, limite)


def buscar_dados_moveis_telefonia(id_inquerito: int, limite: int = 200,
                                  id_telefonia: int | None = None,
                                  numero: str | None = None, data_de: str | None = None,
                                  data_ate: str | None = None) -> dict:
    """As sessões de dados móveis da linha investigada: em que intervalo ela ficou presa a cada
    antena (CGI com endereço resolvido), com que IP e IMEI e com que volumes de entrada e saída.

    É o paradeiro por dados, complementar ao CDR de voz: mostra onde a linha estava mesmo sem
    ligar para ninguém. `numero` filtra a linha; `data_de`/`data_ate` recortam o período
    (AAAA-MM-DD).

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso."""
    query = """
        SELECT d.id_dado_movel, t.no_operadora, d.nu_linha, d.dt_inicio, d.dt_fim,
               d.tx_ip, d.tx_ipv6, d.nu_imei, d.nu_cgi, e.tx_endereco AS endereco_antena,
               e.no_municipio AS municipio_antena, e.sg_uf AS uf_antena,
               d.qt_vol_entrada, d.qt_vol_saida, d.tp_unidade,
               d.tp_tecnologia, d.tp_rede
        FROM investigacao.tel_tb08_dados_moveis d
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia
        LEFT JOIN investigacao.tel_tb05_erbs e
               ON e.id_telefonia = d.id_telefonia AND e.nu_cgi = d.nu_cgi
        WHERE t.id_inquerito = %(id_inquerito)s
          AND (%(id_telefonia)s::bigint IS NULL OR t.id_telefonia = %(id_telefonia)s)
          AND (%(numero)s::text IS NULL OR d.nu_linha ILIKE '%%' || %(numero)s || '%%')
          AND (%(data_de)s::date IS NULL OR d.dt_inicio >= %(data_de)s::date)
          AND (%(data_ate)s::date IS NULL OR d.dt_inicio < (%(data_ate)s::date + 1))
        ORDER BY d.dt_inicio NULLS LAST, d.id_dado_movel
        LIMIT %(limite)s
    """
    return _executar_com_meta(
        query, {"id_inquerito": id_inquerito, "limite": limite, "id_telefonia": id_telefonia,
                "numero": numero, "data_de": data_de, "data_ate": data_ate}, limite)


def buscar_encontros_telefonia(id_inquerito: int, janela_min: int = 30, min_encontros: int = 2,
                               data_de: str | None = None, data_ate: str | None = None,
                               limite: int = 50) -> dict:
    """Pares de linhas na mesma antena (ERB) dentro da janela — quem estava com quem, onde e quando.

    Só entram as linhas do cadastro (os alvos); sem cadastro, as de maior volume no CDR. Antena é
    região, não posição: o encontro é co-localização na mesma torre, não prova de contato.
    `janela_min` (5–480, padrão 30) é a tolerância entre as passagens; `min_encontros` corta o par
    que se cruzou uma vez por acaso; datas em AAAA-MM-DD.
    """
    from funcoes.telefonia_utils import listar_encontros_inquerito
    return listar_encontros_inquerito(id_inquerito, janela_min, min_encontros, data_de, data_ate, limite)


def listar_ancoras_telefonia(id_inquerito: int) -> dict:
    """Residência/trabalho provável por linha, pela rotina na antena.

    Residência = ERB com mais madrugadas distintas (22h–06h, Brasília); trabalho = mais dias úteis
    09h–18h, excluída a da residência. Só marca com 3+ dias — abaixo disso é passagem, não rotina.
    É indício para orientar diligência, não conclusão.
    """
    from funcoes.telefonia_utils import listar_ancoras_inquerito
    return listar_ancoras_inquerito(id_inquerito)


def timeline_financeira(id_inquerito: int, pessoa: str | None = None) -> dict:
    """Linha do tempo financeira do inquérito: SIMBA + RIF por mês, no mesmo eixo temporal.

    Junta a série mensal dos extratos concluídos com as comunicações do RIF por data da operação.
    Mostra quando a movimentação apareceu, parou ou mudou de patamar — e se as duas fontes contam
    a mesma história no mesmo mês. Mês sem uma das fontes volta zerado nela.
    `pessoa` (chave do array `pessoas` do próprio retorno) restringe tudo a uma pessoa só —
    titular da conta no SIMBA, envolvido da comunicação no RIF; sem ela vem a soma de todas.
    """
    from funcoes.simba_utils import timeline_financeira_inquerito
    return timeline_financeira_inquerito(id_inquerito, pessoa=pessoa)


def listar_passagens_simba(id_inquerito: int, limite: int = 50,
                           id_extrato: int | None = None) -> list[dict]:
    """Contas de passagem (possível laranja): entra quase o mesmo que sai, com giro alto.

    Conta com muito crédito E muito débito, saldo residual pequeno diante do giro e vários
    lançamentos — o desenho de quem só repassa. Usa os mesmos pesos de Administração > Alertas
    SIMBA. `id_extrato` restringe a um extrato; sem ele, varre os concluídos do inquérito.
    """
    from funcoes.simba_utils import calcular_alertas, listar_extratos_inquerito
    limite = max(1, min(int(limite or 50), 200))
    if id_extrato is not None:
        alertas = calcular_alertas(int(id_extrato))
        return (alertas.get("passagens") or [])[:limite]
    saidas: list[dict] = []
    for ext in listar_extratos_inquerito(id_inquerito):
        if ext.get("cd_status") != "concluido":
            continue
        alertas = calcular_alertas(ext["id_extrato"])
        for linha in alertas.get("passagens") or []:
            copia = dict(linha)
            copia["id_extrato"] = ext["id_extrato"]
            copia["no_arquivo"] = ext.get("no_arquivo")
            saidas.append(copia)
    saidas.sort(key=lambda x: -float(x.get("vl_giro") or 0))
    return saidas[:limite]


def listar_contrapartes_simba(
    id_inquerito: int, termo: str | None = None, so_internas: bool = False, limite: int = 100,
    id_extrato: int | None = None,
) -> list[dict]:
    """Contrapartes dos extratos do SIMBA, consolidadas por pessoa (somando todas as contas).

    Responde "quem movimentou dinheiro com os investigados": quanto cada CPF/CNPJ creditou nas
    contas afastadas, quanto recebeu delas, em quantos lançamentos e em que período. O campo
    `in_titular_no_extrato` marca quem também é titular de uma conta afastada — ou seja, dinheiro
    circulando entre os próprios investigados.
    """
    limite = max(1, min(int(limite or 100), 500))
    filtros = ["s.id_inquerito = %(id_inquerito)s"]
    params: dict = {"id_inquerito": id_inquerito, "limite": limite}
    if id_extrato is not None:
        filtros.append("s.id_extrato = %(id_extrato)s")
        params["id_extrato"] = id_extrato
    if so_internas:
        filtros.append("cp.in_titular_no_extrato")
    if termo and termo.strip():
        filtros.append("(cp.no_contraparte ILIKE %(termo)s OR cp.nu_cpf_cnpj ILIKE %(termo)s)")
        params["termo"] = "%" + termo.strip() + "%"
    query = f"""
        SELECT (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS no_contraparte,
               cp.nu_cpf_cnpj,
               count(DISTINCT cp.id_conta_simba) AS qt_contas,
               sum(cp.qt_lancamentos) AS qt_lancamentos,
               coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_creditou,
               coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_recebeu,
               min(cp.dt_primeiro) AS dt_primeiro, max(cp.dt_ultimo) AS dt_ultimo,
               bool_or(cp.in_titular_no_extrato) AS in_titular_no_extrato
        FROM investigacao.smb_tb04_contrapartes cp
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = cp.id_extrato
        WHERE {" AND ".join(filtros)}
        GROUP BY coalesce(cp.nu_cpf_cnpj, 'NOME:' || upper(cp.no_contraparte)), cp.nu_cpf_cnpj
        ORDER BY sum(cp.vl_total) DESC
        LIMIT %(limite)s
    """
    return _executar(query, params)


def buscar_lancamentos_simba(
    id_inquerito: int, termo: str | None = None, cpf_cnpj: str | None = None,
    natureza: str | None = None, valor_minimo: float | None = None,
    data_inicio: str | None = None, data_fim: str | None = None, limite: int = 100,
    id_extrato: int | None = None,
) -> list[dict]:
    """Lançamentos individuais dos extratos do SIMBA, com a contraparte de cada um.

    `termo` procura na descrição do lançamento e no nome da contraparte; `cpf_cnpj` filtra pelo
    documento do titular OU da contraparte (só dígitos, sem pontuação); `natureza` é 'C' (entrou)
    ou 'D' (saiu); datas em 'AAAA-MM-DD'. Sempre limitado — para volume total, use
    listar_contas_simba ou listar_contrapartes_simba.
    """
    limite = max(1, min(int(limite or 100), 500))
    filtros = ["s.id_inquerito = %(id_inquerito)s"]
    params: dict = {"id_inquerito": id_inquerito, "limite": limite}
    if id_extrato is not None:
        filtros.append("s.id_extrato = %(id_extrato)s")
        params["id_extrato"] = id_extrato
    if termo and termo.strip():
        filtros.append("(l.tx_descricao ILIKE %(termo)s OR l.no_od ILIKE %(termo)s)")
        params["termo"] = "%" + termo.strip() + "%"
    if cpf_cnpj:
        params["doc"] = "".join(ch for ch in str(cpf_cnpj) if ch.isdigit())
        if params["doc"]:
            filtros.append("(l.nu_cpf_cnpj_od = %(doc)s OR c.nu_cpf_cnpj = %(doc)s)")
    if natureza in ("C", "D"):
        filtros.append("l.cd_natureza = %(nat)s")
        params["nat"] = natureza
    if valor_minimo:
        filtros.append("l.vl_operacao >= %(vmin)s")
        params["vmin"] = valor_minimo
    if data_inicio:
        filtros.append("l.dt_lancamento >= %(dt_ini)s")
        params["dt_ini"] = data_inicio
    if data_fim:
        filtros.append("l.dt_lancamento <= %(dt_fim)s")
        params["dt_fim"] = data_fim
    query = f"""
        SELECT l.id_lancamento_simba, c.no_titular, c.nu_cpf_cnpj AS doc_titular, c.no_banco,
               c.nu_agencia, c.nu_conta, l.dt_lancamento, l.tx_descricao, l.vl_operacao,
               l.cd_natureza, l.vl_saldo, l.no_od AS contraparte, l.nu_cpf_cnpj_od AS doc_contraparte,
               l.nu_banco_od, l.nu_agencia_od, l.nu_conta_od, l.tx_observacao_od
        FROM investigacao.smb_tb03_lancamentos l
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = l.id_conta_simba
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
        WHERE {" AND ".join(filtros)}
        ORDER BY l.vl_operacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, params)


def resumo_credito_simba(id_inquerito: int, id_extrato: int | None = None) -> list[dict]:
    """Quebra o crédito e o débito de cada extrato do SIMBA em quatro categorias — É A FERRAMENTA
    CERTA para "quanto X recebeu/pagou de terceiros", "quanto entrou de fora", "quanto recebeu de
    outras contas/outros titulares" e perguntas parecidas. NÃO some vl_credito/vl_debito de
    listar_contas_simba para isso — aquele total é bruto e mistura tudo junto.

    Uma linha por extrato (ou só o extrato pedido em id_extrato), com:
    - vl_credito_externo / vl_debito_externo: contraparte identificada que NÃO é titular de conta
      afastada aqui — é o número que responde "recebido/pago de terceiros";
    - vl_credito_interno / vl_debito_interno: contraparte identificada que TAMBÉM é titular de
      conta afastada no mesmo extrato — dinheiro circulando entre os próprios investigados, NÃO
      conta como "de terceiros";
    - vl_credito_financeiro_proprio / vl_debito_financeiro_proprio: aplicação financeira
      automática/resgate/rendimento ou estorno/devolução, sem contraparte — o banco girando o
      próprio saldo do titular ou desfazendo uma operação, também NÃO é "de terceiros";
    - vl_credito_outros_sem_contraparte / vl_debito_outros_sem_contraparte: o resto sem contraparte
      identificada (saque, depósito em espécie, tarifa, juros).

    A soma das quatro categorias de crédito é o crédito total do extrato (idem para débito).
    """
    query = """
        SELECT s.id_extrato, s.no_arquivo
        FROM investigacao.smb_tb01_extratos s
        WHERE s.id_inquerito = %(id_inquerito)s
          AND (%(id_extrato)s::bigint IS NULL OR s.id_extrato = %(id_extrato)s)
        ORDER BY s.id_extrato
    """
    extratos = _executar(query, {"id_inquerito": id_inquerito, "id_extrato": id_extrato})
    resultado = []
    for e in extratos:
        linha = {"id_extrato": e["id_extrato"], "no_arquivo": e["no_arquivo"]}
        linha.update(resumo_credito_extrato(e["id_extrato"]))
        resultado.append(linha)
    return resultado


def buscar_chaves_pix(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Chaves PIX citadas nas conversas do inquérito, já ligadas a quem enviou e à conversa.

    A detecção é determinística (funcoes/pix_utils.py), com dígito verificador conferido em
    CPF/CNPJ — o que volta aqui é chave de verdade, não número solto parecido. `referencia` aponta
    a mensagem de origem, para o investigador abrir a conversa e ler o contexto inteiro.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT p.id_chave_pix, p.co_chave, p.tp_chave, p.tx_chave_original,
               p.no_remetente, p.in_remetente_dono, p.no_conversa,
               p.no_fonte, p.no_app, p.tx_trecho, p.dt_mensagem, p.id_mensagem
        FROM investigacao.pix_tb01_chaves p
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR p.co_chave ILIKE '%%' || %(termo)s::text || '%%'
               OR p.tx_chave_original ILIKE '%%' || %(termo)s::text || '%%'
               OR p.no_remetente ILIKE '%%' || %(termo)s::text || '%%'
               OR p.no_conversa ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY p.dt_mensagem DESC NULLS LAST
        LIMIT %(limite)s
    """
    resultados = _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})
    for r in resultados:
        # mesma convenção de buscar_mensagens — a IA cita e o investigador abre a mensagem original
        r["referencia"] = f"[msg:{r['no_fonte']}:{r['id_mensagem']}]"
    return resultados


def buscar_redes_wifi(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Redes Wi-Fi conhecidas/salvas pelo aparelho (SSID, BSSID, segurança, senha quando o UFDR
    trouxe, nº de conexões e 1ª/última conexão).

    Vale como prova de presença física: aparelho que guardou a rede de um endereço esteve lá, e o
    BSSID (MAC do roteador) identifica o ponto de acesso mesmo quando o SSID é genérico
    ("NET_2G", "Wi-Fi"). Duas extrações que compartilham BSSID estiveram no mesmo lugar.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT w.id_rede_wifi, w.no_ssid, w.no_bssid, w.tp_seguranca, w.co_senha,
               w.qt_conexoes, w.dt_primeira_conexao, w.dt_ultima_conexao,
               w.in_favorita, w.in_rede_oculta, w.no_fabricante
        FROM investigacao.wifi_tb01_redes w
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = w.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR w.no_ssid ILIKE '%%' || %(termo)s::text || '%%'
               OR w.no_bssid ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY w.qt_conexoes DESC NULLS LAST, w.dt_ultima_conexao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


# =====================================================================
# Domínios do UFDR importados na auditoria de aproveitamento.
#
# Todos têm o mesmo formato das demais ferramentas: recorte por inquérito, opcionalmente por
# aparelho (id_extracao), termo livre e limite. Ficaram de fora de propósito os domínios de
# volume alto e leitura melhor na própria tela — uso de aplicativo, cookies e uso de rede.
# =====================================================================


def buscar_buscas(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Termos que a pessoa digitou em buscas — navegador, e-mail, mapas e dentro de aplicativos.

    É prova direta de intenção, e costuma anteceder o fato: o que foi procurado, onde e quando.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT b.id_busca, b.tx_termo, b.no_aplicativo, b.no_origem, b.dt_busca, b.no_conta,
               b.vl_latitude, b.vl_longitude, b.cd_status_ufdr
        FROM investigacao.bsc_tb01_buscas b
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = b.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR b.tx_termo ILIKE '%%' || %(termo)s::text || '%%'
               OR b.no_aplicativo ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY b.dt_busca DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_atividade_social(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Publicações, comentários e check-ins em redes sociais, com autor, texto, URL e coordenada.

    É conteúdo público que a pessoa produziu — diferente das conversas, que são privadas.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT s.id_atividade, s.dt_atividade, s.tp_atividade, s.no_plataforma, s.no_aplicativo,
               s.no_autor, s.nu_autor, LEFT(s.tx_corpo, 2000) AS tx_corpo,
               (length(s.tx_corpo) > 2000) AS corpo_truncado,
               s.tx_titulo, s.tx_url, s.no_conta, s.tx_marcados, s.tx_anexos,
               s.qt_reacoes, s.qt_comentarios, s.vl_latitude, s.vl_longitude, s.cd_status_ufdr
        FROM investigacao.rds_tb01_atividade_social s
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = s.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR s.tx_corpo ILIKE '%%' || %(termo)s::text || '%%'
               OR s.tx_titulo ILIKE '%%' || %(termo)s::text || '%%'
               OR s.no_autor ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY s.dt_atividade DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_compromissos(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Compromissos da agenda do aparelho: assunto, detalhes, período, local e convidados.

    Encontro marcado é combinação registrada — vale como plano, e os convidados ligam pessoas.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT c.id_compromisso, c.dt_inicio, c.dt_fim, c.tx_assunto, c.tx_detalhes, c.tx_local,
               c.tx_convidados, c.no_organizador, c.tx_repeticao, c.no_categoria, c.no_conta,
               c.no_aplicativo, c.vl_latitude, c.vl_longitude, c.cd_status_ufdr
        FROM investigacao.cal_tb01_calendario c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR c.tx_assunto ILIKE '%%' || %(termo)s::text || '%%'
               OR c.tx_detalhes ILIKE '%%' || %(termo)s::text || '%%'
               OR c.tx_local ILIKE '%%' || %(termo)s::text || '%%'
               OR c.tx_convidados ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY c.dt_inicio DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_antenas(
    id_inquerito: int, termo: str | None = None, limite: int = 300, id_extracao: int | None = None
) -> list[dict]:
    """Antenas de telefonia (ERBs) que o aparelho registrou: MCC, MNC, LAC, CID, coordenada e hora.

    É rastro de deslocamento independente de GPS — o aparelho registra a antena mesmo com a
    localização desligada. Só aparece em iPhone nos extratos analisados até aqui.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT a.id_antena, a.dt_registro, a.tp_rede, a.nu_mcc, a.nu_mnc, a.nu_lac, a.nu_cid,
               a.vl_latitude, a.vl_longitude, a.cd_status_ufdr
        FROM investigacao.erb_tb01_antenas a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR a.nu_cid ILIKE '%%' || %(termo)s::text || '%%'
               OR a.nu_lac ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY a.dt_registro DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_conexoes_dispositivo(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Conexões de Wi-Fi e Bluetooth com outro aparelho, com data e identificação do outro lado.

    Traz só as que identificam com o quê o aparelho falou (nome ou MAC): conexão anônima não
    prova nada. O MAC permite cruzar aparelhos de extrações diferentes do mesmo caso.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT cx.id_conexao, cx.dt_conexao, cx.tp_metodo, cx.tp_natureza, cx.tp_dispositivo,
               cx.no_dispositivo, cx.no_mac, cx.no_aplicativo, cx.cd_status_ufdr
        FROM investigacao.cnx_tb01_conexoes cx
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = cx.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (cx.no_dispositivo IS NOT NULL OR cx.no_mac IS NOT NULL)
          AND (%(termo)s::text IS NULL
               OR cx.no_dispositivo ILIKE '%%' || %(termo)s::text || '%%'
               OR cx.no_mac ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY cx.dt_conexao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_trajetos(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Deslocamentos registrados por apps de mapa: origem, destino, coordenadas e período.

    Traz só os que têm ao menos uma coordenada — trajeto sem origem nem destino não diz nada.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT t.id_trajeto, t.dt_inicio, t.dt_fim, t.tp_trajeto, t.no_trajeto,
               t.tx_endereco_origem, t.vl_lat_origem, t.vl_lon_origem,
               t.tx_endereco_destino, t.vl_lat_destino, t.vl_lon_destino,
               t.qt_pontos, t.no_aplicativo, t.no_conta, t.cd_status_ufdr
        FROM investigacao.jrn_tb01_trajetos t
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = t.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (t.vl_lat_origem IS NOT NULL OR t.vl_lat_destino IS NOT NULL)
          AND (%(termo)s::text IS NULL
               OR t.tx_endereco_origem ILIKE '%%' || %(termo)s::text || '%%'
               OR t.tx_endereco_destino ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY t.dt_inicio DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_transferencias(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Transferências de valores registradas no aparelho: valor, moeda, participantes e situação.

    É dinheiro que o próprio Cellebrite modelou a partir do app de pagamento/carteira — some ao
    que vem de RIF e SIMBA, mas com a vantagem de já vir amarrado ao aparelho periciado.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT t.id_transferencia, t.dt_envio, t.dt_processamento, t.vl_valor, t.no_moeda,
               t.tp_transferencia, t.tx_participantes, t.tx_descricao, t.cd_situacao,
               t.no_aplicativo, t.cd_status_ufdr
        FROM investigacao.trf_tb01_transferencias t
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = t.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR t.tx_participantes ILIKE '%%' || %(termo)s::text || '%%'
               OR t.tx_descricao ILIKE '%%' || %(termo)s::text || '%%'
               OR t.no_aplicativo ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY t.dt_envio DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_cartoes_carteira(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Cartões e passes guardados na carteira do aparelho: emissor, tipo, validade e onde foi comprado.

    Passe de embarque, ingresso e cartão de fidelidade datam e localizam a pessoa.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT c.id_cartao_carteira, c.tx_descricao, c.no_organizacao, c.tp_cartao, c.co_barras,
               c.dt_compra, c.dt_modificacao, c.dt_expiracao, c.vl_latitude, c.vl_longitude,
               c.no_aplicativo, c.cd_status_ufdr
        FROM investigacao.mbc_tb01_cartoes_carteira c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR c.tx_descricao ILIKE '%%' || %(termo)s::text || '%%'
               OR c.no_organizacao ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY c.dt_compra DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_emails(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """E-mails extraídos do aparelho, por trecho do remetente, destinatário, assunto ou corpo.

    O corpo volta truncado (primeiros 2000 caracteres): o suficiente para julgar relevância sem
    estourar a janela do modelo quando a busca traz muitos.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT m.id_email, m.no_remetente, m.tx_destinatarios, m.tx_cc, m.tx_assunto,
               left(m.tx_corpo, 2000) AS tx_corpo,
               length(m.tx_corpo) > 2000 AS corpo_truncado,
               m.dt_email, m.no_fonte
        FROM investigacao.eml_tb01_emails m
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = m.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR m.no_remetente ILIKE '%%' || %(termo)s::text || '%%'
               OR m.tx_destinatarios ILIKE '%%' || %(termo)s::text || '%%'
               OR m.tx_assunto ILIKE '%%' || %(termo)s::text || '%%'
               OR m.tx_corpo ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY m.dt_email DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_atividades_conta(
    id_inquerito: int, termo: str | None = None, produto: str | None = None, limite: int = 100,
    id_extracao: int | None = None,
) -> list[dict]:
    """"Minha Atividade" da conta Google (Takeout): busca, YouTube, Maps, Chrome etc., com produto,
    descrição, URL e horário.

    Dado que NÃO existe no UFDR — só chega via importação de Google Takeout. Mostra o que a conta
    fez mesmo fora do aparelho periciado (outro device logado na mesma conta), o que buscar_navegacao
    (histórico local do navegador) não alcança.
    """
    termo = (termo or "").strip() or None
    produto = (produto or "").strip() or None
    query = """
        SELECT a.id_atividade, a.dt_atividade, a.tx_produto, a.tx_descricao, a.tx_url, a.no_fonte
        FROM investigacao.atv_tb01_atividades a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR a.tx_descricao ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_url ILIKE '%%' || %(termo)s::text || '%%')
          AND (%(produto)s::text IS NULL OR a.tx_produto ILIKE '%%' || %(produto)s::text || '%%')
        ORDER BY a.dt_atividade DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "produto": produto, "limite": limite, "id_extracao": id_extracao})


def buscar_acessos_conta(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Log de acessos/login à conta na nuvem (Google Takeout — AccessLogActivity): data, IP, user-agent,
    produto e os identificadores de dispositivo/sessão vistos naquele evento (Android ID, iOS ID,
    OAuth login ID etc.).

    Dado que NÃO existe no UFDR. Serve pra situar a conta geograficamente por IP e pra ligar a conta a
    um dispositivo que não é o aparelho periciado (outro Android/iOS que também logou nela).
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT a.id_acesso, a.dt_acesso, a.tx_ip, a.tx_ip_proxy, a.in_ip_nao_roteavel, a.tx_user_agent,
               a.tx_produto, a.tx_subproduto, a.tp_acesso, a.tx_canal, a.tx_android_id, a.tx_ios_id,
               a.tx_outro_dispositivo, a.no_fonte
        FROM investigacao.acs_tb01_acessos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR a.tx_ip ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_produto ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_android_id ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_ios_id ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY a.dt_acesso DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_dispositivos_conta(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Dispositivos conhecidos pela conta na nuvem (Google Takeout): modelo, fabricante, Android ID,
    IMEI, versão, serial.

    Dado que NÃO existe no UFDR. É a lista de TODOS os aparelhos que já logaram nessa conta — pode
    revelar um segundo celular/tablet que nunca foi apreendido/periciado.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT d.id_dispositivo, d.no_dispositivo, d.tx_fabricante, d.tx_android_id, d.tx_imei,
               d.tx_versao, d.tx_serial, d.tp_origem, d.no_fonte
        FROM investigacao.dvc_tb01_dispositivos d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR d.no_dispositivo ILIKE '%%' || %(termo)s::text || '%%'
               OR d.tx_fabricante ILIKE '%%' || %(termo)s::text || '%%'
               OR d.tx_android_id ILIKE '%%' || %(termo)s::text || '%%'
               OR d.tx_imei ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY d.no_dispositivo NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_perfil_conta(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Perfil da conta na nuvem (Google Takeout): nome, e-mails (principal e alternativos), data de
    criação da conta, idioma, país, serviços contratados, status.

    Dado que NÃO existe no UFDR — é a identidade declarada da conta perante o provedor, não a do
    dono do aparelho. Normalmente 0-1 registro por extração de Takeout.
    """
    query = """
        SELECT p.id_perfil, p.id_extracao, p.no_nome, p.no_nome_dado, p.no_nome_familia, p.tx_email,
               p.tx_email_alternativos, p.dt_criacao, p.tx_idioma, p.tx_pais, p.tx_nascimento,
               p.tx_servicos, p.dt_ultima_atualizacao, p.dt_exclusao, p.tx_status, p.no_fonte
        FROM investigacao.prf_tb01_perfil p
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
        ORDER BY p.id_perfil
    """
    return _executar(query, {"id_inquerito": id_inquerito, "id_extracao": id_extracao})


def buscar_eventos_agenda(
    id_inquerito: int, termo: str | None = None, limite: int = 100, id_extracao: int | None = None
) -> list[dict]:
    """Eventos de agenda/calendário da conta na nuvem (Google Takeout): título, local, período,
    organizador e participantes.

    Dado que NÃO existe no UFDR. Útil pra achar compromissos marcados (encontro, viagem, reunião) com
    hora e local, inclusive futuros (o UFDR só traz o que já aconteceu no aparelho).
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT e2.id_evento, e2.dt_inicio, e2.dt_fim, e2.dt_criacao, e2.dt_alteracao, e2.tx_titulo,
               e2.tx_local, e2.tx_descricao, e2.tx_organizador, e2.tx_participantes, e2.no_fonte
        FROM investigacao.evt_tb01_eventos e2
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = e2.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR e2.tx_titulo ILIKE '%%' || %(termo)s::text || '%%'
               OR e2.tx_local ILIKE '%%' || %(termo)s::text || '%%'
               OR e2.tx_descricao ILIKE '%%' || %(termo)s::text || '%%'
               OR e2.tx_participantes ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY e2.dt_inicio DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_contas_financeiras(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Contas bancárias/financeiras identificadas no aparelho (instituição, titular, agência,
    conta, tipo, saldo).

    Vem dos apps financeiros logados no próprio celular — é a conta que a pessoa usa, não a que ela
    declarou. tp_conta diz a natureza da instituição: banco, pagamento, cripto (aí nu_conta é o
    endereço da carteira), aposta, capitalizacao ou credito. Cruza bem com os envolvidos do RIF
    (buscar_envolvidos_rif_filtrados) e com as chaves PIX.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT c.id_conta_financeira, c.no_instituicao, c.no_titular, c.nu_agencia, c.nu_conta,
               c.tp_conta, c.no_moeda, c.vl_saldo, c.no_fonte
        FROM investigacao.bnc_tb01_contas_financeiras c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR c.no_instituicao ILIKE '%%' || %(termo)s::text || '%%'
               OR c.no_titular ILIKE '%%' || %(termo)s::text || '%%'
               OR c.nu_conta ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY c.no_instituicao NULLS LAST, c.no_titular NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def listar_contas_usuario(
    id_inquerito: int, termo: str | None = None, limite: int = 200, id_extracao: int | None = None
) -> list[dict]:
    """Contas de aplicativo logadas no aparelho (Apple ID, Google, e-mail, redes sociais…).

    É a forma mais direta de amarrar o aparelho a uma identidade: o Apple ID/Google do celular é
    do dono, não de um contato. Use quando a pergunta for "de quem é este aparelho" ou "que
    perfis essa pessoa mantém".
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT c.id_conta_usuario, c.no_servico, c.no_usuario, c.no_titular, c.dt_criacao, c.no_fonte
        FROM investigacao.cta_tb01_contas_usuario c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR c.no_servico ILIKE '%%' || %(termo)s::text || '%%'
               OR c.no_usuario ILIKE '%%' || %(termo)s::text || '%%'
               OR c.no_titular ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY c.no_servico NULLS LAST, c.no_usuario NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


def buscar_arquivos_dispositivo(
    id_inquerito: int,
    termo: str | None = None,
    categoria: str | None = None,
    apagados: bool = False,
    limite: int = 100,
    id_extracao: int | None = None,
) -> list[dict]:
    """Inventário do filesystem do aparelho — não só anexo de conversa: câmera, downloads, mídia
    de app e sistema, com caminho, tamanho, data e status.

    É a maior tabela da extração (um celular tem centenas de milhares de arquivos), então SEMPRE
    filtre por `termo` e/ou `categoria` — sem filtro isto devolve só os mais recentes, o que
    raramente responde alguma coisa. `apagados=True` restringe ao que o Cellebrite recuperou
    depois de excluído, que costuma ser o mais probatório.
    """
    termo = (termo or "").strip() or None
    categoria = (categoria or "").strip() or None
    query = """
        SELECT a.id_arquivo, a.no_arquivo, a.tx_caminho, a.no_categoria, a.tp_mime,
               a.qt_bytes, a.dt_modificacao, a.cd_status_arquivo,
               a.co_caminho_zip IS NOT NULL AS binario_disponivel,
               a.tx_ocr IS NOT NULL AS tem_texto_ocr
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND (%(termo)s::text IS NULL
               OR a.no_arquivo ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_caminho ILIKE '%%' || %(termo)s::text || '%%'
               OR a.tx_ocr ILIKE '%%' || %(termo)s::text || '%%')
          AND (%(categoria)s::text IS NULL OR a.no_categoria = %(categoria)s)
          AND (NOT %(apagados)s
               OR (a.cd_status_arquivo IS NOT NULL AND a.cd_status_arquivo <> 'Intact'))
        ORDER BY a.dt_modificacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(
        query,
        {
            "id_inquerito": id_inquerito, "termo": termo, "categoria": categoria,
            "apagados": apagados, "limite": limite, "id_extracao": id_extracao,
        },
    )


def buscar_texto_em_imagens(
    id_inquerito: int, termo: str | None = None, limite: int = 50, id_extracao: int | None = None
) -> list[dict]:
    """Texto reconhecido por OCR dentro das IMAGENS do aparelho (print de comprovante PIX, boleto,
    foto de documento, conversa printada).

    É conteúdo que não existe como texto em lugar nenhum: se a resposta pode estar num print, é
    aqui que ela está. Só enxerga imagens que já passaram pelo OCR — a varredura completa é o botão
    "Extrair texto (OCR)" da tela Arquivos, e um recorte pode ser disparado por
    processar_texto_em_imagens.
    """
    termo = (termo or "").strip() or None
    query = """
        SELECT a.id_arquivo, a.no_arquivo, a.tx_caminho, a.dt_modificacao, a.cd_status_arquivo,
               a.classificacao_ia, left(a.tx_ocr, 2000) AS texto_ocr
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR e.id_extracao = %(id_extracao)s)
          AND a.tx_ocr IS NOT NULL
          AND (%(termo)s::text IS NULL OR a.tx_ocr ILIKE '%%' || %(termo)s::text || '%%')
        ORDER BY a.dt_modificacao DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, {"id_inquerito": id_inquerito, "termo": termo, "limite": limite, "id_extracao": id_extracao})


_OCR_SELECAO_LIMITE_PADRAO = 50
_OCR_SELECAO_LIMITE_MAX = 200


def _parse_data_ocr(valor: str | None, campo: str) -> str | None:
    """Aceita apenas data/hora ISO (AAAA-MM-DD ou AAAA-MM-DD HH:MM) — erro descritivo devolve à IA
    em vez de deixar o psycopg estourar no meio da chamada."""
    if not valor:
        return None
    try:
        datetime.datetime.fromisoformat(valor)
    except ValueError:
        raise ValueError(f"Campo '{campo}' precisa de data no formato AAAA-MM-DD (recebi '{valor}').")
    return valor


def processar_texto_em_imagens(
    id_inquerito: int,
    ids_arquivos: list[int] | None = None,
    termo: str | None = None,
    inicio: str | None = None,
    fim: str | None = None,
    id_extracao: int | None = None,
    limite: int = _OCR_SELECAO_LIMITE_PADRAO,
    id_job: int | None = None,
    id_usuario: int | None = None,
    id_conversa_ia: int | None = None,
    id_processamento: int | None = None,
) -> dict:
    """Dispara (e depois acompanha) o OCR de um RECORTE de imagens do aparelho — nunca o corpus
    inteiro: ou ids explícitos de buscar_arquivos_dispositivo, ou termo/período com teto rígido de
    200 imagens por chamada. Só imagens pendentes entram; o resto é devolvido como 'ignoradas'
    para a IA justificar o recorte ao investigador.

    O reconhecimento roda em background (job TP_OCR_SELECAO na tabela de jobs — mesmo mecanismo da
    tela Arquivos, com fila de jobs pesados, progresso e cancelamento), porque o Tesseract leva
    segundos/minutos por imagem: o turno do chat não pode esperar. A IA acompanha chamando a mesma
    ferramenta com 'id_job' e lê o resultado por buscar_texto_em_imagens.

    id_usuario (dono do job, injetado pelo binding da conversa — nunca a critério da IA) é o que
    amarra o processamento a quem conversou: cancelamento pela tela exige ser o dono, gestor ou
    admin (jobs_utils.pode_controlar)."""
    # import tardio de propósito: ocr_utils puxa pytesseract/PIL, e esta lista de ferramentas é
    # importada por caminhos que nunca fazem OCR (tamanho de prompt, busca automática de entidades)
    from funcoes import jobs_utils, ocr_utils
    from funcoes import ia_jobs_turno

    if id_usuario is None:
        return {"erro": "Esta conversa não pode disparar processamento de imagens."}

    if id_job is not None:
        job = jobs_utils.obter(id_job)
        if job is None or job.get("id_inquerito") != id_inquerito:
            return {"erro": f"Job {id_job} não encontrado neste inquérito."}
        status = {
            "id_job": job["id_job"],
            "status": job["cd_status"],
            "total": job["qt_total"],
            "processados": job["qt_processados"],
            "erros": job["qt_erros"],
            "com_texto": (job.get("tx_metricas") or {}).get("com_texto"),
            "erro": job.get("tx_erro"),
        }
        if job["cd_status"] == "aguardando_vaga":
            status["posicao_fila"] = jobs_utils.posicao_na_fila(id_job)
            status["aviso"] = ("O job espera vaga na fila de jobs pesados do servidor (OCR completo, "
                               "classificação de imagem e comparação facial disputam a mesma CPU). "
                               "Siga com outras consultas e consulte de novo em alguns instantes.")
        elif job["cd_status"] in jobs_utils.ATIVOS:
            status["aviso"] = "Ainda em andamento — consulte de novo daqui a pouco."
        elif job["cd_status"] == "concluido":
            status["aviso"] = ("Concluído. O texto das imagens agora sai por buscar_texto_em_imagens "
                               "(use 'termo': comprovante, PIX, CPF, valores...).")
        elif job["cd_status"] == "cancelado":
            status["aviso"] = "Cancelado pela interface."
        return status

    if not ids_arquivos and not ((termo or "").strip() or inicio or fim or id_extracao):
        return {"erro": ("Diga o que processar: 'ids_arquivos' (de buscar_arquivos_dispositivo) ou um "
                         "recorte por 'termo'/'inicio'/'fim'/'id_extracao'. Esta ferramenta não varre "
                         "o corpus inteiro — para isso existe o botão da tela Arquivos.")}
    if not ocr_utils.ocr_disponivel():
        return {"erro": ("OCR indisponível no servidor (sem Tesseract local e sem API de visão "
                         "configurada). Informe o administrador — não há o que processar agora.")}

    try:
        inicio = _parse_data_ocr(inicio, "inicio")
        fim = _parse_data_ocr(fim, "fim")
    except ValueError as exc:
        return {"erro": str(exc)}

    selecao = ocr_utils.fila_ocr_selecionada(
        id_inquerito,
        ids_arquivos=ids_arquivos,
        termo=(termo or "").strip() or None,
        inicio=inicio, fim=fim, id_extracao=id_extracao,
        limite=limite or _OCR_SELECAO_LIMITE_PADRAO,
    )
    if selecao["selecionadas"] == 0:
        return {
            "erro": "Nenhuma imagem pendente de OCR neste recorte.",
            "ignoradas": selecao["ignoradas"],
            "aviso": ("'ja_processadas' já passaram pelo OCR (leia com buscar_texto_em_imagens); "
                      "'inativeis' são ids que não existem neste inquérito, não são imagem ou estão "
                      "sem binário — confirme os arquivos com buscar_arquivos_dispositivo."),
        }

    # extração do job: a pedida, ou — quando o recorte caiu num único aparelho — a única tocada.
    # É o que faz o link "Ver na tela Arquivos" do cartão abrir o aparelho certo (a tela exige extração)
    extracao_job = id_extracao if id_extracao is not None else (
        selecao["extracoes"][0] if len(selecao["extracoes"]) == 1 else None
    )
    try:
        novo_job = jobs_utils.criar(
            jobs_utils.TP_OCR_SELECAO, id_usuario, id_inquerito=id_inquerito,
            id_extracao=extracao_job,
            parametros={
                "ids_arquivos": list(ids_arquivos)[:_OCR_SELECAO_LIMITE_MAX] if ids_arquivos else None,
                "termo": (termo or "").strip() or None,
                "inicio": inicio, "fim": fim, "id_extracao": extracao_job,
                "limite": selecao["limite"],
                # amarra o job ao turno/conversa que o disparou: é o que permite, ao gravar a resposta,
                # pôr o id_job em CO_JOBS e o front desenhar o cartão de progresso na bolha (0045)
                "id_conversa_ia": id_conversa_ia, "id_processamento": id_processamento,
            },
        )
    except jobs_utils.JobOcupado as exc:
        return {
            "erro": str(exc),
            "aviso": "Já há uma seleção de OCR em andamento neste inquérito — acompanhe pelo id_job "
                     "dela (últimos jobs aparecem na tela Arquivos) ou aguarde terminar.",
        }

    threading.Thread(
        target=ocr_utils.ocr_imagens_selecionadas,
        args=(novo_job, selecao["grupos"]),
        daemon=True,
    ).start()
    # corredor memória→worker: o turno grava este id_job em CO_JOBS da resposta, e o cartão de
    # progresso aparece na bolha (e na tela Arquivos) mesmo depois de o turno encerrar
    ia_jobs_turno.registrar(id_processamento, novo_job, jobs_utils.TP_OCR_SELECAO, id_extracao=extracao_job)
    resultado = {
        "id_job": novo_job,
        "selecionadas": selecao["selecionadas"],
        "ignoradas": selecao["ignoradas"],
        "mensagem": (f"OCR de {selecao['selecionadas']} imagem(ns) iniciado em background "
                     "(acompanhe pelo cartão de progresso desta mensagem, ou na tela Arquivos; "
                     "cancelável por quem dispara). Acompanhe com esta mesma ferramenta via 'id_job'; "
                     "quando concluir, o texto sai por buscar_texto_em_imagens."),
    }
    if selecao.get("truncado"):
        resultado["aviso"] = (f"Foram cortados {selecao['truncado']} ids além do teto de "
                              f"{_OCR_SELECAO_LIMITE_MAX} por chamada — dispare outra chamada para "
                              "o restante, depois de este job concluir.")
    return resultado


def gerar_planilha_consolidada(
    id_inquerito: int,
    pedido: str | None = None,
    colunas: list[str] | None = None,
    avaliar: bool = False,
    id_job: int | None = None,
    id_usuario: int | None = None,
    id_conversa_ia: int | None = None,
    id_processamento: int | None = None,
    escopos: list[dict] | None = None,
) -> dict:
    """Dispara (e depois acompanha) a consolidação de muitos documentos numa planilha .xlsx.

    Existe para o caso em que a resposta não cabe no chat: "planilha com todas as armas apreendidas
    em 2026 a partir dos 200 PDFs". O turno não resolve isso — a tabela é cortada pelo teto de
    tokens e o "continue" não recupera o resto. O job (TP_CONSOLIDACAO, fora da fila de CPU porque
    o recurso disputado é a IA, não o Tesseract) lê os documentos em lotes com chamadas à IA,
    extrai as ocorrências e monta o .xlsx; o cartão na bolha acompanha e entrega o download.

    avaliar=true só conta documentos e estima o tempo — é o passo ANTES de disparar: a IA usa os
    números para explicar o funcionamento ao investigador e pedir confirmação. escopos (injetado
    pelo binding da conversa, como id_usuario) é o recorte do painel "Fontes": documentos e IPLs;
    sem recorte, o inquérito inteiro."""
    from funcoes import ia_jobs_turno, ia_planilha_utils, jobs_utils

    if id_usuario is None:
        return {"erro": "Esta conversa não pode disparar consolidações."}

    if id_job is not None:
        job = jobs_utils.obter(id_job)
        if (job is None or job.get("id_inquerito") != id_inquerito
                or job.get("tp_job") != jobs_utils.TP_CONSOLIDACAO):
            return {"erro": f"Job {id_job} não encontrado neste inquérito."}
        status = {
            "id_job": job["id_job"],
            "status": job["cd_status"],
            "total": job["qt_total"],
            "processados": job["qt_processados"],
            "erros": job["qt_erros"],
            "erro": job.get("tx_erro"),
        }
        resultado = job.get("tx_resultado") or {}
        if job["cd_status"] in jobs_utils.ATIVOS:
            status["aviso"] = ("Ainda em andamento — não repita a chamada: o progresso e o download "
                               "ficam no cartão da conversa. Se o investigador perguntar, diga para "
                               "ele olhar o cartão.")
        elif job["cd_status"] == "concluido":
            status["planilha"] = resultado
            status["aviso"] = ("Concluído. O link de download está no cartão desta conversa "
                               f"({resultado.get('link') or 'sem arquivo gerado'}); confirme ao "
                               "investigador em uma frase.")
        elif job["cd_status"] == "cancelado":
            status["aviso"] = "Cancelado pela interface."
        return status

    if len(colunas or []) > 15:
        return {"erro": "No máximo 15 colunas — escolha as essenciais."}
    colunas = ia_planilha_utils.limpar_colunas(colunas or [])
    if not (pedido or "").strip() or not colunas:
        return {"erro": ("Diga o que consolidar: 'pedido' (o que procurar nos documentos) e "
                         "'colunas' (os títulos das colunas da planilha). A coluna 'Fonte' (o "
                         "documento de cada linha) é acrescentada automaticamente.")}

    try:
        avaliacao = ia_planilha_utils.avaliar_consolidacao(id_inquerito, escopos)
    except Exception as exc:
        return {"erro": f"Falha ao avaliar os documentos do inquérito: {exc}"}
    if avaliacao["qt_documentos"] == 0:
        resposta = {"erro": ("Nenhum documento com texto extraído no recorte atual — não há o que "
                             "consolidar. Peça ao investigador para anexar/extrair os PDFs na tela "
                             "de investigação, ou limpar a seleção de Fontes.")}
        if avaliacao.get("aviso_escopos"):
            resposta["aviso_escopos"] = avaliacao["aviso_escopos"]
        return resposta

    if avaliar:
        return {
            **avaliacao,
            "aviso": (
                "NADA foi disparado ainda. Explique agora ao investigador: o que o agente faz (lê "
                "todos os documentos do recorte em lote, extrai as ocorrências e monta um .xlsx com "
                f"as colunas pedidas + a fonte de cada linha), que são {avaliacao['qt_documentos']} "
                f"documentos (~{avaliacao['qt_lotes_estimados']} leituras de IA) com duração "
                f"estimada de {avaliacao['minutos_estimados']} minutos, e pergunte se pode acionar. "
                "Só dispare (avaliar=false, mesmo pedido/colunas) depois de um 'sim' explícito."
            ),
        }

    try:
        novo_job = jobs_utils.criar(
            jobs_utils.TP_CONSOLIDACAO, id_usuario, id_inquerito=id_inquerito,
            parametros={
                "pedido": pedido.strip(),
                "colunas": colunas[:15],
                "ids_documentos": avaliacao["ids_documentos"],
                # amarra o job à bolha que o disparou: o id_job vai para CO_JOBS da resposta e o
                # front desenha o cartão de progresso + download (migração 0045)
                "id_conversa_ia": id_conversa_ia, "id_processamento": id_processamento,
            },
            pesado=False,
        )
    except jobs_utils.JobOcupado as exc:
        return {
            "erro": str(exc),
            "aviso": "Já há uma consolidação em andamento neste inquérito — acompanhe pelo cartão "
                     "dela; só dispare outra depois que terminar.",
        }

    ia_planilha_utils.disparar(novo_job)
    ia_jobs_turno.registrar(id_processamento, novo_job, jobs_utils.TP_CONSOLIDACAO)
    return {
        "id_job": novo_job,
        "qt_documentos": avaliacao["qt_documentos"],
        "minutos_estimados": avaliacao["minutos_estimados"],
        "mensagem": (
            f"Consolidação de {avaliacao['qt_documentos']} documento(s) iniciada em background "
            f"(estimativa ~{avaliacao['minutos_estimados']} min). Diga ao investigador uma frase só: "
            "o progresso e o download da planilha aparecem no cartão desta mensagem, e o job "
            "sobrevive a F5. Não fique re-consultando."
        ),
    }


@_ttl_cache(ttl_seconds=300)
def listar_pessoas_identificadas(
    id_inquerito: int, termo: str | None = None, tipo: str | None = None, limite: int = 200
) -> dict:
    """As entidades canônicas do inquérito — a "coisa" investigada (pessoa, telefone, local,
    veículo, organização), com os identificadores que a definem e as relações já afirmadas.

    Esta é a melhor PRIMEIRA chamada quando a pergunta é sobre gente ("quem são os envolvidos?",
    "quem é fulano?"): a identificação de pessoas já cruzou, sem IA e sem achismo, contato +
    agenda + ligações + chaves PIX + titular de conta bancária + contas de app, fundindo em UMA
    pessoa tudo que compartilha telefone/CPF/e-mail/usuário. Partir daqui evita reconstruir na
    marra um cruzamento que o sistema já fez — e que já foi visto pelo investigador na mesa.

    `qt_evidencias` diz em quantos registros extraídos aquela pessoa aparece: quanto maior, mais
    central ela é no caso.
    Limitado a 200 registros por padrão para evitar contextos muito grandes.

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    termo = (termo or "").strip() or None
    tipo = (tipo or "").strip() or None
    query = """
        SELECT c.id_canonica, c.tp_entidade, c.no_entidade, c.tx_observacao, c.in_automatico,
               (SELECT count(*) FROM investigacao.ent_tb06_canonica_evidencias v
                 WHERE v.id_canonica = c.id_canonica) AS qt_evidencias,
               (SELECT string_agg(DISTINCT i.tp_identificador || '=' || i.co_valor, ' | ')
                  FROM investigacao.ent_tb08_canonica_identificadores i
                 WHERE i.id_canonica = c.id_canonica) AS identificadores,
               (SELECT string_agg(r.tx_tipo_relacao || ' → ' || d.no_entidade, ' | ')
                  FROM investigacao.ent_tb07_canonica_relacoes r
                  JOIN investigacao.ent_tb05_canonicas d ON d.id_canonica = r.id_canonica_destino
                 WHERE r.id_canonica_origem = c.id_canonica) AS relacoes
        FROM investigacao.ent_tb05_canonicas c
        WHERE c.id_inquerito = %(id_inquerito)s
          AND (%(tipo)s::text IS NULL OR c.tp_entidade = %(tipo)s)
          AND (%(termo)s::text IS NULL
               OR c.no_entidade ILIKE '%%' || %(termo)s::text || '%%'
               OR EXISTS (
                   SELECT 1 FROM investigacao.ent_tb08_canonica_identificadores i
                    WHERE i.id_canonica = c.id_canonica
                      AND i.co_valor ILIKE '%%' || %(termo)s::text || '%%'
               ))
        ORDER BY qt_evidencias DESC, c.no_entidade
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, {"id_inquerito": id_inquerito, "termo": termo, "tipo": tipo, "limite": limite}, limite)


# --- Funções de busca com filtros refinados (Opção 3) ---

def buscar_comunicacoes_rif_filtradas(
    id_inquerito: int, data_inicio: str | None = None, data_fim: str | None = None,
    valor_minimo: float | None = None, valor_maximo: float | None = None,
    banco: str | None = None, ordenar_por: str = "valor", limite: int = 50,
    id_rif: int | None = None,
) -> dict:
    """Busca comunicações de RIF com filtros refinados para reduzir volume de dados.

    Filtros:
    - data_inicio, data_fim: período em 'AAAA-MM-DD' (usa dt_operacao)
    - valor_minimo, valor_maximo: limites de crédito ou débito
    - banco: nome parcial do banco comunicante
    - ordenar_por: 'valor' (maior valor) ou 'data' (mais recente)

    O texto de análise do COAF (tx_informacoes_adicionais) volta cortado em MAX_CHARS_ANALISE_RIF
    caracteres quando a busca traz mais de REGISTROS_COM_ANALISE_INTEGRA_RIF comunicações, com a
    flag analise_truncada em cada registro cortado. É a diferença entre uma listagem caber na
    janela do modelo e um único pedido ("liste as 223 comunicações") consumi-la inteira: nos RIFs
    grandes esse campo tem ~3 mil caracteres por comunicação, às vezes 20 mil. Para ler a íntegra,
    refaça a busca isolando a operação (período estreito, valor_minimo/valor_maximo, banco).

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    filtros = ["r.id_inquerito = %(id_inquerito)s"]
    params: dict = {"id_inquerito": id_inquerito, "limite": limite}
    if id_rif is not None:
        filtros.append("r.id_rif = %(id_rif)s")
        params["id_rif"] = id_rif

    if data_inicio:
        filtros.append("c.dt_operacao >= %(data_inicio)s::date")
        params["data_inicio"] = data_inicio
    if data_fim:
        filtros.append("c.dt_operacao <= %(data_fim)s::date")
        params["data_fim"] = data_fim
    if valor_minimo is not None:
        filtros.append("(c.vl_campo_b + c.vl_campo_c >= %(valor_minimo)s)")
        params["valor_minimo"] = valor_minimo
    if valor_maximo is not None:
        filtros.append("(c.vl_campo_b + c.vl_campo_c <= %(valor_maximo)s)")
        params["valor_maximo"] = valor_maximo
    if banco:
        filtros.append("c.no_comunicante ILIKE %(banco)s")
        params["banco"] = "%" + banco + "%"

    ordem = "c.dt_operacao DESC" if ordenar_por == "data" else "GREATEST(c.vl_campo_b, c.vl_campo_c) DESC"
    query = f"""
        SELECT c.id_comunicacao_rif, r.nu_rif, c.dt_operacao, c.dt_recebimento,
               c.no_comunicante as banco, c.no_agencia, c.nu_agencia,
               c.no_cidade_agencia, c.sg_uf_agencia,
               c.vl_campo_b as valor_credito, c.vl_campo_c as valor_debito,
               c.tx_informacoes_adicionais, c.cd_segmento, c.nu_indexador
        FROM investigacao.rif_tb02_comunicacoes c
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
        WHERE {" AND ".join(filtros)}
        ORDER BY {ordem}
        LIMIT %(limite)s
    """
    resultado = _executar_com_meta(query, params, limite)
    return _cortar_analise_rif(resultado)


def _cortar_analise_rif(resultado: dict) -> dict:
    """Corta o texto de análise do COAF quando a listagem traz muitas comunicações.

    Poucos registros = a IA está lendo uma operação específica e precisa do texto inteiro; muitos =
    é panorama, e a íntegra de cada um estouraria a janela do modelo."""
    registros = resultado.get("registros") or []
    if len(registros) <= REGISTROS_COM_ANALISE_INTEGRA_RIF:
        return resultado
    cortados = 0
    for registro in registros:
        texto = registro.get("tx_informacoes_adicionais") or ""
        if len(texto) > MAX_CHARS_ANALISE_RIF:
            registro["tx_informacoes_adicionais"] = texto[:MAX_CHARS_ANALISE_RIF] + "…"
            registro["analise_truncada"] = True
            registro["analise_tamanho_total"] = len(texto)
            cortados += 1
    if cortados:
        resultado["aviso_analise"] = (
            f"O texto de análise do COAF veio cortado em {MAX_CHARS_ANALISE_RIF} caracteres em "
            f"{cortados} das {len(registros)} comunicações (analise_truncada). Para ler a íntegra "
            "de uma delas, refaça a busca isolando a operação (período estreito, valor_minimo/"
            "valor_maximo, banco) — com até "
            f"{REGISTROS_COM_ANALISE_INTEGRA_RIF} resultados o texto vem completo."
        )
    return resultado


def buscar_envolvidos_rif_filtrados(
    id_inquerito: int, tipo_envolvido: str | None = None, so_pep: bool = False,
    so_servidores: bool = False, termo: str | None = None, limite: int = 50,
    id_rif: int | None = None,
) -> dict:
    """Busca envolvidos de RIF com filtros refinados.

    Filtros:
    - tipo_envolvido: 'Remetente', 'Beneficiário', 'Titular', etc.
    - so_pep: apenas Pessoas Politicamente Expostas
    - so_servidores: apenas pessoas marcadas como servidor público
    - termo: busca por nome ou CPF/CNPJ

    Retorna dict com: registros, total_disponivel, mostrando, truncado, aviso (se truncado).
    """
    filtros = ["r.id_inquerito = %(id_inquerito)s"]
    params: dict = {"id_inquerito": id_inquerito, "limite": limite}
    if id_rif is not None:
        filtros.append("r.id_rif = %(id_rif)s")
        params["id_rif"] = id_rif

    if tipo_envolvido:
        filtros.append("e.tp_envolvido = %(tipo_envolvido)s")
        params["tipo_envolvido"] = tipo_envolvido
    if so_pep:
        filtros.append("e.tx_pep IS NOT NULL AND e.tx_pep != ''")
    if so_servidores:
        filtros.append("e.tx_servidor IS NOT NULL AND e.tx_servidor != ''")
    if termo:
        filtros.append("(e.no_envolvido ILIKE %(termo)s OR e.nu_cpf_cnpj ILIKE %(termo)s)")
        params["termo"] = "%" + termo + "%"

    query = f"""
        SELECT e.id_envolvido_rif, r.nu_rif, e.no_envolvido, e.nu_cpf_cnpj,
               e.tp_envolvido, e.nu_agencia, e.nu_conta,
               e.dt_abertura_conta, e.dt_atualizacao_conta,
               e.tx_pep, e.tx_pessoa_obrigada, e.tx_servidor,
               e.nu_indexador
        FROM investigacao.rif_tb03_envolvidos e
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = e.id_rif
        WHERE {" AND ".join(filtros)}
        ORDER BY e.no_envolvido
        LIMIT %(limite)s
    """
    return _executar_com_meta(query, params, limite)


@_ttl_cache(ttl_seconds=120)
def listar_deteccoes_padroes(
    id_inquerito: int, categoria: str | None = None, limite: int = 200
) -> dict:
    """Achados da última execução de "Detectar padrões" na mesa de inquérito.

    Padrões são determinísticos (léxico/gíria, regex de CPF/placa/valor, e temporal — atividade de
    madrugada, rajada de mensagens), cadastrados por administrador. São achados que um humano já
    definiu como relevantes para o tipo penal: valem mais do que a IA redescobrir sozinha o que
    procurar. `tp_origem`/`id_origem` apontam o registro que disparou cada achado.

    Devolve dict com `deteccoes` e, quando não há o que mostrar, um `aviso` explicando se é
    porque a detecção nunca rodou (aí peça ao investigador para rodar) ou porque rodou sem achar.
    """
    categoria = (categoria or "").strip() or None
    execucao = _executar(
        """
        SELECT id_execucao, dt_fim, qt_deteccoes
        FROM investigacao.pdr_tb02_execucoes
        WHERE id_inquerito = %(id_inquerito)s AND cd_status = 'concluido'
        ORDER BY id_execucao DESC LIMIT 1
        """,
        {"id_inquerito": id_inquerito},
    )
    if not execucao:
        return {
            "deteccoes": [],
            "aviso": "A detecção de padrões nunca foi executada neste inquérito — peça ao "
                     "investigador para rodar 'Detectar padrões' na mesa de inquérito.",
        }

    # só a última execução: rodar de novo regrava os mesmos achados, e somar todas as execuções
    # faria a IA contar a mesma evidência várias vezes
    query = """
        SELECT d.id_deteccao, p.no_padrao, p.tp_padrao, p.no_categoria, p.ds_padrao,
               d.tp_origem, d.id_origem, d.tx_trecho, d.dt_ocorrencia
        FROM investigacao.pdr_tb03_deteccoes d
        JOIN investigacao.pdr_tb01_padroes p ON p.id_padrao = d.id_padrao
        WHERE d.id_execucao = %(id_execucao)s
          AND (%(categoria)s::text IS NULL OR p.no_categoria = %(categoria)s)
        ORDER BY p.no_categoria NULLS LAST, p.no_padrao, d.dt_ocorrencia DESC NULLS LAST
        LIMIT %(limite)s
    """
    deteccoes = _executar(
        query,
        {"id_execucao": execucao[0]["id_execucao"], "categoria": categoria, "limite": limite},
    )
    resposta = {"deteccoes": deteccoes, "detectado_em": execucao[0]["dt_fim"]}
    if not deteccoes:
        resposta["aviso"] = (
            "A detecção rodou, mas nenhum padrão cadastrado bateu neste inquérito"
            + (f" na categoria '{categoria}'." if categoria else ".")
        )
    return resposta


@_ttl_cache(ttl_seconds=120)
def listar_marcacoes_relatorio(id_inquerito: int, limite: int = 200) -> list[dict]:
    """Registros que o investigador marcou para entrar no relatório final ("Marcar para o
    relatório"), com o comentário dele e o trecho legível de cada origem.

    É a leitura mais direta do que o INVESTIGADOR já considera relevante neste caso — julgamento
    humano, não achado automático. Use para ancorar respostas de síntese ("resuma o caso", "o que
    já está provado") na linha de apuração que ele vem seguindo, em vez de propor outra do zero.
    """
    marcacoes = listar_marcacoes_inquerito(id_inquerito)[:limite]
    return [
        {
            "id_marcacao": m["id_marcacao"],
            "tp_origem": m["tp_origem"],
            "id_origem": m["id_origem"],
            "conteudo": m.get("rotulo_origem"),
            "comentario_do_investigador": m.get("tx_comentario"),
            "cor": m.get("co_cor"),
            "dt_origem": m.get("dt_origem"),
            "marcado_por": m.get("criado_por"),
            "extracao": m.get("no_dispositivo") or m.get("no_arquivo"),
        }
        for m in marcacoes
    ]


@_ttl_cache(ttl_seconds=300)
def resumo_inquerito(id_inquerito: int) -> dict:
    """Panorama do inquérito: volumes por domínio, período coberto pelas mensagens, contatos com
    mais mensagens trocadas e telefones com mais ligações. Bom primeiro passo antes das buscas
    específicas — mostra onde está a massa de dados."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  (SELECT count(*) FROM investigacao.wpp_tb04_mensagens m
                     JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS mensagens_whatsapp,
                  (SELECT count(*) FROM investigacao.sms_tb04_mensagens m
                     JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS mensagens_sms,
                  (SELECT count(*) FROM investigacao.msg_tb04_mensagens m
                     JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS mensagens_outros_apps,
                  (SELECT count(*) FROM investigacao.wpp_tb05_anexos a
                     JOIN investigacao.wpp_tb04_mensagens m ON m.id_mensagem = a.id_mensagem
                     JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s AND a.tx_transcricao IS NOT NULL) AS audios_transcritos,
                  (SELECT count(*) FROM investigacao.arq_tb01_arquivos a
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
                    WHERE e.id_inquerito = %(id)s AND a.tx_ocr IS NOT NULL) AS imagens_com_texto_ocr,
                  (SELECT count(*) FROM investigacao.lig_tb01_ligacoes l
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS ligacoes,
                  (SELECT count(*) FROM investigacao.loc_tb01_localizacoes l
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS localizacoes,
                  (SELECT count(*) FROM investigacao.ntf_tb01_notificacoes n
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS notificacoes,
                  (SELECT min(m.dt_mensagem) FROM investigacao.wpp_tb04_mensagens m
                     JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS primeira_mensagem,
                  (SELECT max(m.dt_mensagem) FROM investigacao.wpp_tb04_mensagens m
                     JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                     JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                    WHERE e.id_inquerito = %(id)s) AS ultima_mensagem
                """,
                {"id": id_inquerito},
            )
            colunas = [d.name for d in cur.description]
            totais = dict(zip(colunas, cur.fetchone()))

            cur.execute(
                """
                SELECT ct.no_contato, ct.nu_whatsapp_id, count(*) AS qt_mensagens
                FROM investigacao.wpp_tb04_mensagens m
                JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                JOIN investigacao.wpp_tb03_participantes p ON p.id_conversa = m.id_conversa
                JOIN investigacao.wpp_tb01_contatos ct ON ct.id_contato = p.id_contato
                WHERE e.id_inquerito = %(id)s AND NOT coalesce(ct.in_dono_dispositivo, false)
                GROUP BY ct.no_contato, ct.nu_whatsapp_id
                ORDER BY qt_mensagens DESC
                LIMIT 10
                """,
                {"id": id_inquerito},
            )
            colunas = [d.name for d in cur.description]
            top_conversas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

            cur.execute(
                """
                SELECT l.no_contato, l.nu_telefone_contato, count(*) AS qt_ligacoes,
                       sum(coalesce(l.qt_duracao_segundos, 0)) AS duracao_total_segundos
                FROM investigacao.lig_tb01_ligacoes l
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
                WHERE e.id_inquerito = %(id)s
                GROUP BY l.no_contato, l.nu_telefone_contato
                ORDER BY qt_ligacoes DESC
                LIMIT 10
                """,
                {"id": id_inquerito},
            )
            colunas = [d.name for d in cur.description]
            top_ligacoes = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

            # Volume por app dos demais mensageiros (Instagram, Telegram, Facebook…), para o
            # panorama deixar claro que existem conversas fora do WhatsApp/SMS a investigar.
            cur.execute(
                """
                SELECT c.tp_app AS aplicativo, count(*) AS qt_mensagens
                FROM investigacao.msg_tb04_mensagens m
                JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                WHERE e.id_inquerito = %(id)s
                GROUP BY c.tp_app
                ORDER BY qt_mensagens DESC
                """,
                {"id": id_inquerito},
            )
            colunas = [d.name for d in cur.description]
            mensagens_por_app = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {
        "totais": totais,
        "mensagens_outros_apps_por_app": mensagens_por_app,
        "contatos_com_mais_mensagens": top_conversas,
        "telefones_com_mais_ligacoes": top_ligacoes,
    }


# =====================================================================
# Conteúdo completo do inquérito — dump textual para o modo "conteúdo completo"
# do chat, em que o investigador manda tudo de uma vez em vez de deixar a IA
# buscar sob demanda pelas ferramentas. O conteúdo datado (mensagens, áudios,
# ligações, localizações, notificações, navegação) vira um único fluxo em ordem
# cronológica — o que permite dividi-lo por data em lotes quando não cabe na
# janela do modelo (ver funcoes.ia_utils). O cabeçalho (dono, resumo, apps) é
# pequeno e acompanha cada lote como contexto.
# =====================================================================

# Teto de caracteres de um bloco montado — rede de segurança contra estourar o contexto.
# ~200k chars ≈ 50k tokens; ao passar, o texto é cortado com uma nota. No modo em lotes o
# intervalo de datas de cada lote já é escolhido para caber, então isto raramente age lá.
MAX_CHARS_CONTEUDO = 200_000

# Limite de linhas do cabeçalho não-datado (lista de apps).
_LIMITE_SECAO = 500
# Estimativa de overhead do prefixo "[data · origem · rótulo] " por linha, somado ao tamanho do
# texto para dimensionar o conteúdo sem precisar montá-lo inteiro.
_OVERHEAD_LINHA = 45
# Trava de eventos ao montar um lote — o intervalo de datas já foi escolhido para caber no
# orçamento; isto é só uma rede contra carregar demais em memória.
_LIMITE_EVENTOS_LOTE = 20_000
# Piso do orçamento de um lote (chars): abaixo disso não vale a pena continuar bisseccionando.
_MIN_CHARS_LOTE = 20_000
# Teto de lotes, para não disparar um número absurdo de chamadas num inquérito gigantesco
# (40 lotes × ~40k tokens cada ≈ 1,6M tokens de cobertura).
MAX_LOTES = 40


_TZ_SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _formatar_data(valor) -> str:
    """Formata um datetime/date do banco (naive, em UTC) como 'AAAA-MM-DD HH:MM' no horário de
    São Paulo — assim as datas que a IA cita ao investigador batem com o fuso local. '?' se nulo."""
    if not valor:
        return "?"
    try:
        if getattr(valor, "tzinfo", "sem") is None:  # datetime naive do Postgres = UTC
            valor = valor.replace(tzinfo=datetime.timezone.utc).astimezone(_TZ_SAO_PAULO)
        elif getattr(valor, "tzinfo", None) is not None:
            valor = valor.astimezone(_TZ_SAO_PAULO)
        return valor.strftime("%Y-%m-%d %H:%M")
    except AttributeError:  # date puro (sem hora) — formata direto
        return str(valor)


# Fluxo unificado de eventos datados do inquérito: cada fonte vira (dt, origem, rotulo, texto). O
# chamador filtra por intervalo de data e ordena. Mantido como string única, reaproveitada tanto
# para estimar tamanho quanto para montar o texto — assim estimativa e montagem nunca divergem.
# O marcador __FILTRO_EXTRACOES__ (uma ocorrência por branch) é resolvido por
# _sql_eventos_com_filtro conforme o filtro de aparelhos da multisseleção do chat.
_SQL_EVENTOS = """
    SELECT 'WhatsApp' AS origem, m.dt_mensagem AS dt, remet.no_contato AS rotulo,
           coalesce(m.tx_mensagem,
               (SELECT string_agg(a.tx_transcricao, ' | ')
                  FROM investigacao.wpp_tb05_anexos a
                 WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL)
           ) AS texto,
           '[msg:wpp:' || m.id_mensagem || ']' AS ref
    FROM investigacao.wpp_tb04_mensagens m
    JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
    LEFT JOIN investigacao.wpp_tb01_contatos remet ON remet.id_contato = m.id_contato_remetente
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'SMS', m.dt_mensagem, remet.no_contato, m.tx_mensagem,
           '[msg:sms:' || m.id_mensagem || ']'
    FROM investigacao.sms_tb04_mensagens m
    JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
    LEFT JOIN investigacao.sms_tb01_contatos remet ON remet.id_contato = m.id_contato_remetente
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT c.tp_app, m.dt_mensagem, remet.no_contato,
           coalesce(m.tx_mensagem,
               (SELECT string_agg(a.tx_transcricao, ' | ')
                  FROM investigacao.msg_tb05_anexos a
                 WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL)
           ),
           '[msg:msg:' || m.id_mensagem || ']'
    FROM investigacao.msg_tb04_mensagens m
    JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
    LEFT JOIN investigacao.msg_tb01_contatos remet ON remet.id_contato = m.id_contato_remetente
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'Ligação', l.dt_ligacao, l.tp_direcao,
           concat_ws(' · ', nullif(coalesce(l.no_contato, l.nu_telefone_contato), ''),
                     CASE WHEN l.qt_duracao_segundos > 0 THEN l.qt_duracao_segundos || 's' END),
           NULL
    FROM investigacao.lig_tb01_ligacoes l
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'Localização', l.dt_localizacao, l.no_aplicativo,
           coalesce(nullif(l.tx_endereco, ''), nullif(l.tx_descricao, ''),
                    concat_ws(',', l.vl_latitude, l.vl_longitude)),
           NULL
    FROM investigacao.loc_tb01_localizacoes l
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'Notificação', n.dt_notificacao, coalesce(n.no_aplicativo_log, n.no_pacote_app), n.tx_conteudo,
           NULL
    FROM investigacao.ntf_tb01_notificacoes n
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'Nota', coalesce(nt2.dt_modificacao, nt2.dt_criacao), nt2.no_aplicativo,
           concat_ws(': ', nullif(nt2.tx_titulo, ''), nt2.tx_corpo),
           NULL
    FROM investigacao.not_tb01_notas nt2
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = nt2.id_extracao
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__

    UNION ALL
    SELECT 'Navegação', p.dt_visita, NULL,
           concat_ws(' — ', nullif(p.tx_titulo, ''), nullif(p.tx_url, '')),
           NULL
    FROM investigacao.nav_tb01_paginas_visitadas p
    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
    WHERE e.id_inquerito = %(id)s__FILTRO_EXTRACOES__
"""

# Filtro de intervalo meio-aberto [inicio, fim) — nulos significam "sem limite" daquele lado.
_FILTRO_INTERVALO = (
    " AND (%(inicio)s::timestamp IS NULL OR dt >= %(inicio)s)"
    " AND (%(fim)s::timestamp IS NULL OR dt < %(fim)s)"
)

# Filtro por aparelhos (multisseleção do chat): entra em cada branch do _SQL_EVENTOS.
_FILTRO_EXTRACOES = " AND e.id_extracao = ANY(%(extracoes)s)"


def _sql_eventos_com_filtro(extracoes: list[int] | None) -> str:
    """_SQL_EVENTOS com o marcador __FILTRO_EXTRACOES__ resolvido (ou removido) conforme o filtro."""
    return _SQL_EVENTOS.replace("__FILTRO_EXTRACOES__", _FILTRO_EXTRACOES if extracoes else "")


def estimar_chars_datados(id_inquerito: int, inicio=None, fim=None, extracoes: list[int] | None = None) -> dict:
    """Estima o tamanho (chars) do conteúdo datado num intervalo, sem montá-lo, e devolve também
    a contagem de eventos e min/max data. Base para decidir se cabe e para dividir por data."""
    params: dict = {"id": id_inquerito, "inicio": inicio, "fim": fim}
    if extracoes:
        params["extracoes"] = list(extracoes)
    query = f"""
        SELECT count(*) AS qt, coalesce(sum(length(texto)), 0) AS chars,
               min(dt) AS dt_min, max(dt) AS dt_max
        FROM ({_sql_eventos_com_filtro(extracoes)}) ev
        WHERE nullif(btrim(texto), '') IS NOT NULL{_FILTRO_INTERVALO}
    """
    linha = _executar(query, params)[0]
    chars = int(linha["chars"]) + int(linha["qt"]) * _OVERHEAD_LINHA
    return {"chars": chars, "qt": int(linha["qt"]), "dt_min": linha["dt_min"], "dt_max": linha["dt_max"]}


def _eventos_datados(id_inquerito: int, inicio, fim, limite: int, extracoes: list[int] | None = None) -> list[dict]:
    """Os eventos datados do intervalo, em ordem cronológica (limitados por segurança)."""
    params: dict = {"id": id_inquerito, "inicio": inicio, "fim": fim, "limite": limite}
    if extracoes:
        params["extracoes"] = list(extracoes)
    query = f"""
        SELECT dt, origem, rotulo, texto, ref
        FROM ({_sql_eventos_com_filtro(extracoes)}) ev
        WHERE nullif(btrim(texto), '') IS NOT NULL{_FILTRO_INTERVALO}
        ORDER BY dt NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query, params)


def _ttl_cache(ttl_seconds: int = 30, maxsize: int = 128):
    """Cache simples com TTL para funções com argumentos hasháveis."""
    cache: dict = {}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            entry = cache.get(key)
            if entry and now - entry[1] < ttl_seconds:
                return entry[0]
            resultado = func(*args, **kwargs)
            if len(cache) >= maxsize:
                cache.clear()
            cache[key] = (resultado, now)
            return resultado
        return wrapper
    return decorator


@_ttl_cache(ttl_seconds=300)
def _cabecalho_inquerito(id_inquerito: int, com_apps: bool = True) -> str:
    """Cabeçalho pequeno e não-datado (dono, resumo e — opcionalmente — apps) que acompanha cada lote
    como contexto. com_apps=False dá um panorama enxuto (sem a lista de apps) para o modo normal."""
    linhas = [f"=== CONTEÚDO EXTRAÍDO DO APARELHO — INQUÉRITO {id_inquerito} ==="]

    donos = listar_donos(id_inquerito)
    if donos:
        linhas.append("\n--- DONO(S) DO APARELHO ---")
        for d in donos:
            partes = [p for p in (d.get("no_dono"), d.get("tp_usuario"), d.get("nu_identificador")) if p]
            linhas.append("• " + " · ".join(str(p) for p in partes))

    resumo = resumo_inquerito(id_inquerito)
    t = resumo["totais"]
    linhas.append("\n--- RESUMO ---")
    linhas.append(
        f"WhatsApp: {t['mensagens_whatsapp']} mensagens · SMS: {t['mensagens_sms']} · "
        f"outros mensageiros: {t.get('mensagens_outros_apps', 0)} · "
        f"áudios transcritos: {t['audios_transcritos']} · ligações: {t['ligacoes']} · "
        f"localizações: {t['localizacoes']} · notificações: {t['notificacoes']}"
    )
    por_app = resumo.get("mensagens_outros_apps_por_app") or []
    if por_app:
        detalhe = " · ".join(f"{a['aplicativo']}: {a['qt_mensagens']}" for a in por_app)
        linhas.append(f"Outros mensageiros por app: {detalhe}")
    if t.get("primeira_mensagem"):
        linhas.append(f"Período das mensagens: {_formatar_data(t['primeira_mensagem'])} a {_formatar_data(t['ultima_mensagem'])}")

    documentos = listar_documentos_inquerito(id_inquerito)
    if documentos:
        linhas.append("\n--- DOCUMENTOS ANEXADOS AO INQUÉRITO (leia com ler_documento_inquerito) ---")
        for d in documentos:
            situacao = "erro de extração" if d["tx_erro"] else f"{d['qt_caracteres'] or 0} caracteres"
            linhas.append(f"• [id_documento {d['id_documento']}] {d['no_arquivo']} ({d['tp_arquivo']}) — {situacao}")

    if com_apps:
        aplicativos = buscar_aplicativos(id_inquerito, limite=_LIMITE_SECAO)
        nomes = [a.get("no_aplicativo") or a.get("nu_pacote") for a in aplicativos if (a.get("no_aplicativo") or a.get("nu_pacote"))]
        if nomes:
            linhas.append("\n--- APLICATIVOS INSTALADOS ---")
            linhas.append(", ".join(nomes))

    return "\n".join(linhas)


def montar_conteudo_inquerito(
    id_inquerito: int, max_chars: int = MAX_CHARS_CONTEUDO, inicio=None, fim=None, com_cabecalho: bool = True,
    extracoes: list[int] | None = None,
) -> str:
    """Monta o texto do conteúdo extraído do aparelho: cabeçalho (dono/resumo/apps) + eventos datados
    (mensagens/áudios, ligações, localizações, notificações, navegação) em ordem cronológica, dentro
    do intervalo [inicio, fim). extracoes (multisseleção do chat) restringe aos aparelhos escolhidos.
    Corta em max_chars e sinaliza — rede de segurança contra estouro."""
    partes: list[str] = []
    if com_cabecalho:
        partes.append(_cabecalho_inquerito(id_inquerito))

    eventos = _eventos_datados(id_inquerito, inicio, fim, _LIMITE_EVENTOS_LOTE, extracoes)
    if eventos:
        periodo = f" [{_formatar_data(inicio)} a {_formatar_data(fim)}]" if (inicio or fim) else ""
        partes.append(f"\n--- EVENTOS EM ORDEM CRONOLÓGICA{periodo} ---")
        partes.append("\n".join(
            f"[{_formatar_data(ev['dt'])} · {ev['origem']}"
            + (f" · {ev['rotulo']}" if ev.get("rotulo") else "")
            + f"] {ev['texto']}"
            + (f" {ev['ref']}" if ev.get("ref") else "")
            for ev in eventos
        ))

    texto = "\n".join(partes)
    if len(texto) > max_chars:
        texto = texto[:max_chars] + "\n\n[... conteúdo cortado por tamanho — use as ferramentas de busca para o restante ...]"
    return texto


def montar_lotes_por_data(id_inquerito: int, budget_chars: int, extracoes: list[int] | None = None) -> list[tuple]:
    """Divide o período do conteúdo datado em lotes que caibam em budget_chars cada, de forma
    adaptativa (bisecção por data onde a massa de dados se concentra). Devolve intervalos meio-abertos
    [inicio, fim) em ordem cronológica cobrindo todo o período; lista vazia se não há conteúdo datado.
    extracoes (multisseleção do chat) restringe aos aparelhos escolhidos."""
    raiz = estimar_chars_datados(id_inquerito, extracoes=extracoes)
    if not raiz["qt"] or raiz["dt_min"] is None:
        return []

    # fim exclusivo: 1µs além do último evento, para incluí-lo no último lote (intervalo meio-aberto)
    fim_total = raiz["dt_max"] + datetime.timedelta(microseconds=1)
    lotes: list[tuple] = []

    def dividir(inicio, fim, chars_est):
        if len(lotes) >= MAX_LOTES:
            lotes.append((inicio, fim))  # o resto entra num último lote (truncado ao montar, se preciso)
            return
        intervalo_minimo = (fim - inicio) <= datetime.timedelta(seconds=1)
        if chars_est <= budget_chars or budget_chars <= _MIN_CHARS_LOTE or intervalo_minimo:
            lotes.append((inicio, fim))
            return
        meio = inicio + (fim - inicio) / 2
        esq = estimar_chars_datados(id_inquerito, inicio, meio, extracoes=extracoes)
        dir_ = estimar_chars_datados(id_inquerito, meio, fim, extracoes=extracoes)
        if esq["qt"] == 0 or dir_["qt"] == 0:
            # a bisecção não separou nada (tudo caiu de um lado) — aceita o lote pra não repetir
            lotes.append((inicio, fim))
            return
        dividir(inicio, meio, esq["chars"])
        dividir(meio, fim, dir_["chars"])

    dividir(raiz["dt_min"], fim_total, raiz["chars"])
    return lotes[:MAX_LOTES]


def buscar_pessoa_referencia(termo: str | None = None, cpf: str | None = None, limite: int = 20) -> list[dict]:
    """Busca na base de referência de pessoas (Serasa, 220M linhas) por nome (fuzzy) ou CPF (exato).

    O teto do limite é aplicado AQUI, e não só na rota, porque esta função tem dois chamadores
    que escolhem o número livremente: o autocomplete (/api/referencia/pessoas, que recebia
    `limite: int` cru) e a IA, para quem ela é ferramenta (ver FERRAMENTAS). O CTE abaixo já é
    podado pelo `similarity > 0.3`, então o risco não é varrer as 220 M linhas — é montar e
    serializar em memória do processo uma lista arbitrariamente grande, uma requisição derrubando
    a API para os outros analistas."""
    limite = max(1, min(_LIMITE_MAX_REFERENCIA, limite or 1))
    cpf_normalizado = _normalizar_cpf(cpf)
    if cpf_normalizado:
        query = """
            SELECT id_pessoa, nu_cpf, no_pessoa, ds_sexo, dt_nascimento, 1.0 AS score
            FROM dados_extraidos.pes_tb01_pessoas
            WHERE nu_cpf = %(cpf)s
            LIMIT %(limite)s
        """
        return _executar(query, {"cpf": cpf_normalizado, "limite": limite})

    termo = (termo or "").strip()
    if not termo:
        return []
    # PES tem 220M linhas: sem filtro de similaridade o CTE materializa
    # todas as linhas que casam no braço fonético. O filtro explícito
    # similarity > 0.3 no ramo trigram permite ao planner podar via
    # GIN trigram (IX25) e evita calcular ROW_NUMBER sobre milhões de
    # linhas de baixa relevância. O OR dmetaphone permanece como fallback
    # fonético para apelidos (ex.: "Maicon" vs "Maycon") sem threshold,
    # mas o planner usa o índice btree IX29. Ver db/init/33_indices_tse_pes_caso.sql.
    query = """
        WITH ranked AS (
            SELECT id_pessoa, nu_cpf, no_pessoa, ds_sexo, dt_nascimento,
                   similarity(coalesce(no_pessoa, ''), %(termo)s) AS score,
                   ROW_NUMBER() OVER (
                       PARTITION BY nu_cpf
                       ORDER BY similarity(coalesce(no_pessoa, ''), %(termo)s) DESC
                   ) AS rn
            FROM dados_extraidos.pes_tb01_pessoas
            WHERE (no_pessoa %% %(termo)s AND similarity(coalesce(no_pessoa, ''), %(termo)s) > 0.3)
               OR dmetaphone(coalesce(no_pessoa, '')) = dmetaphone(%(termo)s)
        )
        SELECT id_pessoa, nu_cpf, no_pessoa, ds_sexo, dt_nascimento, score
        FROM ranked
        WHERE rn = 1
        ORDER BY score DESC
        LIMIT %(limite)s
    """
    return _executar(query, {"termo": termo, "limite": limite})


def buscar_candidato_referencia(
    termo: str | None = None,
    cpf: str | None = None,
    ano_eleicao: int | None = None,
    limite: int = 20,
) -> list[dict]:
    """Busca na base de candidatos/eleição do TSE por nome (fuzzy, nome civil ou de urna) ou CPF.

    Separado em dois planos: busca por CPF usa índice funcional
    IX28_TSE_TB01_CPF_NORM (regexp_replace), busca por nome usa GIN trigram
    IX26/IX27 + IX30/IX31 dmetaphone. O OR genérico anterior impedia o planner
    de escolher o índice certo (generic plan com %(cpf)s IS NOT NULL).
    """
    cpf_normalizado = _normalizar_cpf(cpf)
    termo_norm = (termo or "").strip() or None
    if not cpf_normalizado and not termo_norm:
        return []
    # CPF tem prioridade: quando informado, ignora termo (mesmo comportamento do OR anterior)
    if cpf_normalizado:
        query_cpf = """
            WITH ranked AS (
                SELECT id_candidato, nu_sq_candidato, nu_ano_eleicao, no_candidato, no_urna_candidato,
                       ds_cargo, sg_uf, no_ue, nu_cpf_candidato,
                       1.0 AS score,
                       ROW_NUMBER() OVER (
                           PARTITION BY nu_cpf_candidato
                           ORDER BY nu_ano_eleicao DESC
                       ) AS rn
                FROM dados_extraidos.tse_tb01_candidatos
                WHERE dados_extraidos.cpf_normalizado(nu_cpf_candidato) = %(cpf)s::text
            )
            SELECT id_candidato, nu_sq_candidato, nu_ano_eleicao, no_candidato, no_urna_candidato,
                   ds_cargo, sg_uf, no_ue, score
            FROM ranked
            WHERE (%(ano_eleicao)s::smallint IS NULL OR nu_ano_eleicao = %(ano_eleicao)s::smallint)
              AND (%(ano_eleicao)s::smallint IS NOT NULL OR rn = 1)
            ORDER BY nu_ano_eleicao DESC
            LIMIT %(limite)s
        """
        return _executar(query_cpf, {"cpf": cpf_normalizado, "ano_eleicao": ano_eleicao, "limite": limite})
    # TSE: mesmo padrão de PES — adiciona threshold explícito no ramo
    # trigram para o planner usar GIN IX26/IX27 e podar baixa relevância.
    # O fallback fonético (IX30/IX31) permanece sem threshold.
    query_termo = """
        WITH ranked AS (
            SELECT id_candidato, nu_sq_candidato, nu_ano_eleicao, no_candidato, no_urna_candidato,
                   ds_cargo, sg_uf, no_ue, nu_cpf_candidato,
                   GREATEST(
                       similarity(coalesce(no_candidato, ''), %(termo)s::text),
                       similarity(coalesce(no_urna_candidato, ''), %(termo)s::text)
                   ) AS score,
                   ROW_NUMBER() OVER (
                       PARTITION BY nu_cpf_candidato
                       ORDER BY nu_ano_eleicao DESC
                   ) AS rn
            FROM dados_extraidos.tse_tb01_candidatos
            WHERE (
                        (no_candidato %% %(termo)s::text AND similarity(coalesce(no_candidato, ''), %(termo)s::text) > 0.3)
                        OR (no_urna_candidato %% %(termo)s::text AND similarity(coalesce(no_urna_candidato, ''), %(termo)s::text) > 0.3)
                        OR dmetaphone(coalesce(no_candidato, '')) = dmetaphone(%(termo)s::text)
                        OR dmetaphone(coalesce(no_urna_candidato, '')) = dmetaphone(%(termo)s::text)
                    )
        )
        SELECT id_candidato, nu_sq_candidato, nu_ano_eleicao, no_candidato, no_urna_candidato,
               ds_cargo, sg_uf, no_ue, score
        FROM ranked
        WHERE (%(ano_eleicao)s::smallint IS NULL OR nu_ano_eleicao = %(ano_eleicao)s::smallint)
          AND (%(ano_eleicao)s::smallint IS NOT NULL OR rn = 1)
        ORDER BY score DESC NULLS LAST
        LIMIT %(limite)s
    """
    return _executar(query_termo, {"termo": termo_norm, "ano_eleicao": ano_eleicao, "limite": limite})


def cruzar_identidade(nome: str | None = None, cpf: str | None = None, limite: int = 10) -> dict:
    """Tenta ligar um nome/apelido (vindo de um contato extraído) a uma identidade nas bases de referência.

    Cruza contra pessoas (Serasa) e candidatos (TSE) em paralelo lógico — não
    há telefone nessas bases, então esse cruzamento só funciona por nome/CPF.
    Devolve as duas listas separadas: quem chama decide o que fazer com cada
    fonte (ex.: só citar TSE se o caso tiver relevância eleitoral).
    """
    if not nome and not cpf:
        return {"pessoas": [], "candidatos": []}
    return {
        "pessoas": buscar_pessoa_referencia(termo=nome, cpf=cpf, limite=limite),
        "candidatos": buscar_candidato_referencia(termo=nome, cpf=cpf, limite=limite),
    }


# =====================================================================
# Especificação das ferramentas para function calling — formato JSON Schema
# (compatível com Anthropic `tools`/OpenAI `functions`, únicas diferenças de
# wrapping ficam por conta do cliente de IA que orquestra o loop do agente).
# FUNCOES_IA liga o nome da ferramenta à implementação, para o dispatch.
# =====================================================================

FERRAMENTAS_IA = [
    {
        "name": "buscar_contato_extracao",
        "description": (
            "Procura um contato/apelido salvo no aparelho (WhatsApp, SMS ou agenda) do inquérito, "
            "por nome ou apelido (busca tolerante a erro de digitação). Primeiro passo quando a "
            "pergunta envolve uma pessoa pelo nome salvo no celular."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer", "description": "ID do inquérito (escopo obrigatório)."},
                "termo": {"type": "string", "description": "Nome ou apelido a procurar."},
                "limite": {"type": "integer", "default": 20},
            },
            "required": ["id_inquerito", "termo"],
        },
    },
    {
        "name": "buscar_mensagens",
        "description": (
            "Busca mensagens de QUALQUER app (WhatsApp, SMS, demais mensageiros) por conteúdo e/ou "
            "pelo nome do contato do outro lado da conversa. Os termos são procurados no texto E na "
            "transcrição dos áudios. Use 'termos' (lista) para varrer sinônimos/gírias numa chamada "
            "só. Cada resultado traz 'referencia' (ex.: \"[msg:wpp:123]\") — cite-a na resposta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Um texto a procurar (use 'termos' para vários de uma vez)."},
                "termos": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Vários textos a procurar numa chamada só (OR entre eles) — ideal para sinônimos e gírias.",
                },
                "nome_contato": {"type": "string", "description": "Filtra pela conversa com esse contato."},
                "fonte": {
                    "type": "string", "enum": ["todos", "wpp", "sms", "msg"], "default": "todos",
                    "description": "Restringe a um app (padrão 'todos' varre tudo): wpp = WhatsApp, sms = SMS, msg = demais mensageiros.",
                },
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_hibrida",
        "description": (
            "Busca híbrida de evidências numa ÚNICA chamada: literal por palavras + semântica "
            "(embeddings, pega gíria/linguagem cifrada que a literal não pega) + expansão pelo grafo "
            "de entidades do inquérito, fundindo tudo por relevância sem duplicatas. PRIMEIRA opção "
            "em perguntas abertas, de conceito ou que cruzem evidências (ex.: 'o que se sabe sobre a "
            "venda de drogas'); para algo específico de um pilar use a ferramenta individual. Cada "
            "resultado traz 'referencia' ([msg:...] ou [doc:...]) para citar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "consulta": {"type": "string", "description": "Descrição em linguagem natural do que procurar (palavras exatas e/ou conceito)."},
                "limite": {"type": "integer", "default": 15, "description": "Máximo de resultados fundidos a retornar (padrão 15)."},
            },
            "required": ["id_inquerito", "consulta"],
        },
    },
    {
        "name": "buscar_ligacoes",
        "description": (
            "Busca ligações (telefone nativo e apps como WhatsApp): direção (Incoming/Outgoing), "
            "status, duração e o outro lado da chamada. Filtre por nome do contato, telefone ou "
            "direção quando fizer sentido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "nome_contato": {"type": "string", "description": "Filtra pelo nome do outro lado."},
                "telefone": {"type": "string", "description": "Filtra pelo telefone do outro lado (busca parcial)."},
                "direcao": {"type": "string", "enum": ["Incoming", "Outgoing"], "description": "Só recebidas ou só feitas."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_localizacoes",
        "description": (
            "Lista localizações registradas no aparelho (Waze, Google Maps etc.): coordenadas, "
            "endereço e descrição. Use quando a pergunta for onde a pessoa esteve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_notificacoes",
        "description": (
            "Busca notificações do aparelho por conteúdo e/ou pacote do app de origem (ex.: "
            "'com.nubank'). Revela apps usados (inclusive bancos que não aparecem nas conversas) "
            "e horários de atividade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Texto a procurar no conteúdo da notificação."},
                "pacote_app": {"type": "string", "description": "Pacote Android do app de origem (busca parcial)."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_notas",
        "description": (
            "Busca notas do aparelho (Samsung Notes, Apple Notes, Google Keep etc.) por trecho do "
            "título ou corpo. Costuma trazer 'caderno' do caso (listas de quantidade/preço/apelido) "
            "— vale buscar termos do caso mesmo sem confirmação prévia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Texto a procurar no título ou no corpo da nota."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_navegacao",
        "description": (
            "Busca histórico de navegação (páginas visitadas no navegador) por trecho do título ou "
            "da URL. Sem termo, traz as páginas mais recentes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Texto a procurar no título ou na URL da página."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_aplicativos",
        "description": (
            "Lista aplicativos instalados no aparelho (pacote, versão, datas), opcionalmente "
            "filtrando por trecho do pacote/nome (ex.: 'bank', 'whatsapp', 'vpn'). Revela apps "
            "usados, inclusive os que não aparecem nas conversas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Trecho do pacote ou nome do app (busca parcial)."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_donos",
        "description": (
            "Lista o(s) dono(s)/usuário(s) do aparelho do inquérito: nome do titular, tipo de "
            "usuário (Owner etc.) e último login. Use para confirmar de quem é o aparelho."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "resumo_inquerito",
        "description": (
            "Panorama do inquérito: volumes por domínio (mensagens, áudios, ligações, localizações, "
            "notificações), período coberto, contatos mais ativos. Primeiro passo em perguntas "
            "abertas ('resuma o caso', 'com quem ele mais falava')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_documentos_inquerito",
        "description": (
            "Lista os documentos anexados ao inquérito (PDF do inquérito no ePol, relatórios, "
            "planilhas), com nome, tipo e tamanho do texto extraído. Use quando a pergunta envolver "
            "o teor do papel; para ler o conteúdo, use ler_documento_inquerito. Cite como [doc:ID], "
            "ou [doc:ID:N] quando souber a página (ver ler_documento_inquerito)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "ler_documento_inquerito",
        "description": (
            "Lê o texto extraído de um documento anexado ao inquérito, pelo id_documento de "
            "listar_documentos_inquerito — a fonte do que consta no papel (depoimentos, relatórios, "
            "autos), complementando o que foi extraído do celular. Documento longo volta em partes: "
            "a resposta traz parte/qt_partes/tem_mais — chame de novo com parte=2, 3… para continuar. "
            "Linhas [[página N]] marcam onde cada página do arquivo começa: cite o trecho como "
            "[doc:ID:N] com esse N (e nunca com o número impresso no rodapé, que é a folha do "
            "processo) para o investigador abrir o documento já na página certa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "id_documento": {"type": "integer"},
                "parte": {"type": "integer", "default": 1,
                          "description": "Parte do texto a ler, a partir de 1 (documentos longos)"},
            },
            "required": ["id_inquerito", "id_documento"],
        },
    },
    {
        "name": "buscar_pessoas_ipl",
        "description": (
            "Lista pessoas citadas nos IPLs (Inquéritos Policiais em PDF) do inquérito: nome, "
            "CPF/CNPJ, papel (investigado, advogado, delegado, testemunha), folha e contexto. "
            "Cada registro traz referencia ([doc:ID]) — cite-a na resposta para gerar o link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_achados_ipl",
        "description": (
            "Lista achados dos IPLs do inquérito (telefones, e-mails, valores, contas, endereços, "
            "OAB, tipificações...), opcionalmente filtrados por tipo. Use para dados de contato, "
            "locais e valores mencionados no inquérito em PDF. "
            "Cada registro traz referencia ([doc:ID]) — cite-a na resposta para gerar o link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "tipo_achado": {
                    "type": "string",
                    "enum": ["telefone", "email", "valor", "conta", "agencia", "processo",
                             "procedimento", "tipificacao", "endereco", "oab"],
                    "description": "Filtro opcional por tipo de achado.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "obter_declaracao_irpf",
        "description": (
            "Identificação do contribuinte e resumo das declarações de IRPF do inquérito "
            "(rendimentos, imposto, evolução patrimonial — bens e dívidas em 31/12, o sinal mais "
            "forte para apuração patrimonial)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_bens_direitos_irpf",
        "description": (
            "Lista bens e direitos declarados no IRPF do inquérito (imóveis, veículos, contas, "
            "aplicações), com o valor em 31/12 do ano anterior e do atual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_dependentes_irpf",
        "description": (
            "Lista dependentes declarados no IRPF do inquérito (nome, CPF, relação, nascimento) — "
            "vínculos familiares."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_rendimentos_irpf",
        "description": (
            "Lista rendimentos recebidos de pessoa jurídica (titular e dependentes) nas "
            "declarações de IRPF anexadas ao inquérito — fonte pagadora, CNPJ/CPF e valor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_dividas_irpf",
        "description": "Lista dívidas e ônus reais declarados nas declarações de IRPF anexadas ao inquérito.",
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_doacoes_irpf",
        "description": (
            "Lista doações (efetuadas e a partidos políticos/candidatos) nas declarações de IRPF "
            "anexadas ao inquérito — beneficiário, CNPJ/CPF e valor doado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "obter_ipei",
        "description": (
            "Cabeçalho das IPEIs (quebra de sigilo fiscal da Receita Federal) do inquérito e os "
            "contribuintes analisados em cada uma, com dados cadastrais — pessoa física ou jurídica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_relacionados_ipei",
        "description": (
            "Vínculos levantados na IPEI: sócios, responsáveis legais, empregadores do CNIS, "
            "outorgantes/outorgados de procuração e quem transmitiu declaração do MESMO endereço "
            "MAC que o alvo (o achado mais forte para ligar investigados entre si)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "tipo_relacao": {
                    "type": "string",
                    "enum": ["societario", "empregaticio", "procuracao", "procuracao_rfb",
                             "mac_coincidente"],
                    "description": "Filtro opcional por tipo de vínculo.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_anos_ipei",
        "description": (
            "Consolidado ano a ano de cada alvo da IPEI: rendimentos declarados, receita bruta, "
            "movimentação financeira, DECRED, notas fiscais e variação patrimonial. O campo "
            "vl_razao_mov_rend (movimentação / rendimento declarado) é o indicador central de "
            "incompatibilidade patrimonial."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_fontes_ipei",
        "description": (
            "Fontes pagadoras e pagamentos declarados na IPEI — quem pagou ao alvo e a quem ele "
            "pagou, por ano, com CNPJ/CPF e valor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_contas_ipei",
        "description": (
            "Contas bancárias informadas pela e-Financeira na IPEI, com crédito, débito e saldo mês "
            "a mês. São MONTANTES MENSAIS, não lançamentos — a IPEI não diz quem pagou a quem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "ano": {"type": "integer", "description": "Filtro opcional por ano-calendário."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_bens_ipei",
        "description": (
            "Bens ligados ao alvo na IPEI: declarados no IRPF (com valor em 31/12), veículos do "
            "RENAVAM (placa e restrição), imóveis, embarcações, aeronaves e criptoativos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "tipo_bem": {
                    "type": "string",
                    "enum": ["declarado", "divida", "imovel", "imovel_dimob", "criptoativo",
                             "especie", "veiculo", "veiculo_outras_bases", "outro"],
                    "description": "Filtro opcional por tipo de bem.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_notas_ipei",
        "description": (
            "Notas fiscais eletrônicas destinadas ao alvo na IPEI — o que comprou, de quem, quando "
            "e por quanto. Use `termo` para filtrar por produto ou emitente (ex.: 'arma', 'joia')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtro por descrição do produto ou nome do emitente."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_contatos_ipei",
        "description": (
            "Endereços, telefones, e-mails e MAC/IP do alvo levantados em outras bases da Receita "
            "Federal, por ano — útil para cruzar com o que aparece no aparelho apreendido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "tipo_contato": {
                    "type": "string",
                    "enum": ["endereco", "telefone", "email", "mac"],
                    "description": "Filtro opcional por tipo de contato.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "obter_telefonia_operadora",
        "description": (
            "O que a operadora devolveu na quebra de sigilo telefonico: quais arquivos entraram, "
            "que periodo cobrem, qual linha foi pedida no oficio e quantas linhas cadastrais, "
            "chamadas, antenas e numeros vieram. Comece por aqui antes de buscar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_linhas_telefonia",
        "description": (
            "Fichas cadastrais das linhas na operadora: titular, CPF, RG, endereco, modalidade "
            "(pre/pos), situacao e datas de habilitacao/rescisao. Use `termo` com o numero, o nome "
            "do titular ou o CPF para responder 'de quem e este numero' e 'que outras linhas estao "
            "no nome dele'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Numero da linha, nome do titular ou CPF."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_chamadas_telefonia",
        "description": (
            "Registros de chamada (CDR) da operadora: quando, entre quais numeros, duracao, IMEI de "
            "cada lado e a antena com endereco ja resolvido. `numero` filtra um telefone dos dois "
            "lados; `cgi` filtra por uma antena especifica (o CGI da relacao de antenas — cobre "
            "tambem a grafia curta central+ERB+setor do CDR), para responder 'qual linha usou esta "
            "antena'; `data_de`/`data_ate` recortam o periodo (AAAA-MM-DD); `so_completadas` descarta "
            "as tentativas que nao completaram, que sao a maioria do CDR."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "numero": {"type": "string", "description": "Telefone, chamando ou sendo chamado."},
                "cgi": {"type": "string", "description": "CGI de uma antena (ERB), para listar as chamadas que passaram por ela."},
                "data_de": {"type": "string", "description": "Data inicial, AAAA-MM-DD."},
                "data_ate": {"type": "string", "description": "Data final, AAAA-MM-DD (inclusiva)."},
                "so_completadas": {"type": "boolean", "description": "So chamadas completadas."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_erbs_telefonia",
        "description": (
            "As antenas (ERBs) do CDR, com endereco, municipio, coordenada, quantas chamadas "
            "passaram por cada uma e, quando o oficio identifica a linha, quais linhas (terminais) "
            "usaram cada antena (campo `linhas_alvo`). Use `termo` para filtrar por CGI, cidade, "
            "bairro ou endereco; `numero` para as antenas por onde uma linha especifica passou. A "
            "antena com muitas chamadas indica onde a pessoa fica; a com uma so, passagem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "CGI, cidade, bairro ou endereco."},
                "numero": {"type": "string", "description": "Linha (telefone) para restringir as antenas que ela usou."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_numeros_telefonia",
        "description": (
            "Os numeros que trocaram chamada com a linha investigada, com o IMEI do aparelho e o "
            "total de chamadas. Use `imei` para achar numeros diferentes que usaram o MESMO "
            "aparelho — e um dos achados mais fortes desta base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "numero": {"type": "string", "description": "Filtro por telefone."},
                "imei": {"type": "string", "description": "Filtro por IMEI do aparelho."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_dados_moveis_telefonia",
        "description": (
            "As sessoes de dados moveis da linha investigada: em que intervalo ela ficou presa a "
            "cada antena (com endereco), com que IP e IMEI e com que volumes de entrada e saida. "
            "E o paradeiro por dados, complementar ao CDR de voz: mostra onde a linha estava "
            "mesmo sem ligar para ninguem. `numero` filtra a linha; `data_de`/`data_ate` recortam "
            "o periodo (AAAA-MM-DD)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "numero": {"type": "string", "description": "Filtro pela linha."},
                "data_de": {"type": "string", "description": "Data inicial, AAAA-MM-DD."},
                "data_ate": {"type": "string", "description": "Data final, AAAA-MM-DD (inclusiva)."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_encontros_telefonia",
        "description": (
            "Pares de linhas na mesma antena (ERB) dentro da janela: quem estava com quem, onde e "
            "quando. So entram as linhas do cadastro (os alvos). Antena e regiao, nao posicao. "
            "`janela_min` e a tolerancia em minutos entre as passagens (padrao 30); "
            "`min_encontros` corta o par que se cruzou uma vez por acaso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "janela_min": {"type": "integer", "description": "Tolerancia em minutos (5-480)."},
                "min_encontros": {"type": "integer", "description": "Minimo de encontros do par."},
                "data_de": {"type": "string", "description": "Data inicial, AAAA-MM-DD."},
                "data_ate": {"type": "string", "description": "Data final, AAAA-MM-DD (inclusiva)."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_ancoras_telefonia",
        "description": (
            "Residencia/trabalho provavel por linha, pela rotina na antena: residencia e a ERB com "
            "mais madrugadas distintas (22h-06h), trabalho a com mais dias uteis 09h-18h. So marca "
            "com 3+ dias. E indicio para diligencia, nao conclusao."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "timeline_financeira",
        "description": (
            "Linha do tempo financeira do inquerito: SIMBA + RIF por mes no mesmo eixo temporal. "
            "Mostra quando a movimentacao apareceu, parou ou mudou de patamar, e se as duas fontes "
            "contam a mesma historia no mesmo mes. Sem `pessoa` os valores somam TODOS os "
            "investigados; para falar do movimento de um so, passe `pessoa` com a chave do array "
            "`pessoas` (CPF/CNPJ, ou NOME:<maiusculas> quando nao ha documento)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "pessoa": {
                    "type": "string",
                    "description": "Chave da pessoa (array `pessoas`): restringe a timeline a ela.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_passagens_simba",
        "description": (
            "Contas de passagem (possivel laranja): entra quase o mesmo que sai, com giro alto. "
            "Usa os pesos de Administracao > Alertas SIMBA. `id_extrato` restringe a um extrato."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "id_extrato": {"type": "integer", "description": "Restringe a um extrato."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_comunicacoes_rif_filtradas",
        "description": (
            "Busca transações financeiras dos RIFs do COAF do inquérito com filtros para reduzir "
            "volume: período (data_inicio/data_fim em AAAA-MM-DD), valor_minimo/valor_maximo, "
            "banco (nome parcial), ordenar_por (valor/data). Retorna as transações mais relevantes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "data_inicio": {"type": "string", "description": "AAAA-MM-DD"},
                "data_fim": {"type": "string", "description": "AAAA-MM-DD"},
                "valor_minimo": {"type": "number", "description": "Valor mínimo em R$"},
                "valor_maximo": {"type": "number", "description": "Valor máximo em R$"},
                "banco": {"type": "string", "description": "Nome parcial do banco"},
                "ordenar_por": {"type": "string", "enum": ["valor", "data"], "default": "valor"},
                "limite": {"type": "integer", "default": 50},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_envolvidos_rif_filtrados",
        "description": (
            "Busca pessoas/empresas envolvidas nos RIFs do COAF do inquérito (nome, CPF/CNPJ, "
            "tipo de envolvimento, agência/conta, PEP, pessoa obrigada, servidor público) com "
            "filtros: tipo_envolvido (Remetente/Beneficiário/Titular/etc.), so_pep, so_servidores, "
            "termo (nome ou CPF/CNPJ)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "tipo_envolvido": {"type": "string"},
                "so_pep": {"type": "boolean", "default": False},
                "so_servidores": {"type": "boolean", "default": False},
                "termo": {"type": "string", "description": "Nome ou CPF/CNPJ a buscar"},
                "limite": {"type": "integer", "default": 50},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_contas_simba",
        "description": (
            "Lista as contas bancárias afastadas nos extratos do SIMBA (quebra de sigilo, R304) do "
            "inquérito: titular, CPF/CNPJ, banco/agência/conta, total de crédito/débito, quantidade "
            "de lançamentos e período. Primeiro passo em qualquer pergunta sobre movimentação "
            "bancária. ATENÇÃO: crédito/débito aqui é BRUTO (inclui transferência entre contas do "
            "próprio titular, aplicação automática e estorno) — para 'quanto recebeu/pagou de "
            "terceiros' use resumo_credito_simba."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id_inquerito": {"type": "integer"}},
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "resumo_credito_simba",
        "description": (
            "Quebra o crédito/débito de cada extrato do SIMBA em categorias: contraparte EXTERNA "
            "(terceiro de verdade), conta do PRÓPRIO titular afastado (dinheiro entre os próprios "
            "investigados), APLICAÇÃO FINANCEIRA AUTOMÁTICA/resgate/estorno (banco girando o saldo, "
            "sem contraparte) e outras sem contraparte (saque, depósito em espécie, tarifa). USE "
            "ESTA quando a pergunta for 'quanto X recebeu/pagou de terceiros' — o total bruto de "
            "listar_contas_simba mistura as categorias e responde a pergunta errada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "id_extrato": {"type": "integer", "description": "Restringe a um extrato; por padrão retorna uma linha por extrato do inquérito."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_contrapartes_simba",
        "description": (
            "Contrapartes dos extratos do SIMBA consolidadas por pessoa, somando todas as contas: "
            "quanto cada CPF/CNPJ creditou e recebeu das contas afastadas, em quantos lançamentos e "
            "período. Responde 'quem mandava e quem recebia dinheiro dos investigados'. "
            "in_titular_no_extrato marca quem também é titular afastado (dinheiro entre os próprios "
            "investigados)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtro por nome ou CPF/CNPJ da contraparte."},
                "so_internas": {
                    "type": "boolean",
                    "description": "Só contrapartes que também são titulares de conta afastada.",
                },
                "limite": {"type": "integer", "description": "Máximo de contrapartes (padrão 100)."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_lancamentos_simba",
        "description": (
            "Lançamentos individuais dos extratos do SIMBA, cada um com a contraparte. Use para "
            "detalhar uma movimentação específica depois de listar_contas_simba/"
            "listar_contrapartes_simba. Filtros: termo, cpf_cnpj (titular OU contraparte), natureza "
            "('C' entrou / 'D' saiu), valor_minimo, período. SEMPRE limitado — não serve para somar "
            "totais."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string"},
                "cpf_cnpj": {"type": "string", "description": "CPF/CNPJ, com ou sem pontuação."},
                "natureza": {"type": "string", "enum": ["C", "D"]},
                "valor_minimo": {"type": "number"},
                "data_inicio": {"type": "string", "description": "AAAA-MM-DD."},
                "data_fim": {"type": "string", "description": "AAAA-MM-DD."},
                "limite": {"type": "integer", "description": "Máximo de lançamentos (padrão 100)."},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_chaves_pix",
        "description": (
            "Lista as chaves PIX citadas nas conversas do inquérito (CPF, CNPJ, e-mail, telefone ou "
            "aleatória/EVP), ligadas a quem enviou a mensagem e à conversa. Detecção determinística "
            "com verificação de dígito verificador — não são números soltos. Use quando a pergunta "
            "envolver dinheiro/pagamento/'para quem ele mandava PIX'. Cada resultado traz "
            "'referencia' ([msg:...]) para citar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por trecho da chave, do remetente ou da conversa."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_redes_wifi",
        "description": (
            "Lista as redes Wi-Fi que o aparelho conhece: SSID, BSSID (MAC do roteador), tipo de "
            "segurança, senha quando o UFDR trouxe, nº de conexões e datas. Indício de PRESENÇA "
            "FÍSICA; o BSSID identifica o ponto de acesso mesmo com SSID genérico — dois aparelhos "
            "com o mesmo BSSID estiveram no mesmo lugar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por trecho do SSID ou do BSSID."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_buscas",
        "description": (
            "Termos que a pessoa DIGITOU em buscas — navegador, e-mail, mapas e dentro de apps "
            "(Safari, Chrome, Gmail, Instagram, WhatsApp, Maps). Prova direta de intenção que "
            "costuma anteceder o fato. Diferente de buscar_navegacao, que traz a página visitada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por trecho do termo buscado ou do aplicativo."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_atividade_social",
        "description": (
            "Publicações, comentários e check-ins em redes sociais, com autor, texto, URL, marcados "
            "e coordenada quando houver — o conteúdo PÚBLICO que a pessoa produziu, complementando "
            "as conversas privadas. Corpo truncado em 2000 caracteres ('corpo_truncado' avisa)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por trecho do texto, do título ou do autor."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_compromissos",
        "description": (
            "Compromissos da agenda do aparelho: assunto, detalhes, início/fim, local, recorrência "
            "e CONVIDADOS — encontro marcado é combinação registrada, e os convidados ligam pessoas "
            "entre si."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por assunto, detalhes, local ou convidado."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_antenas",
        "description": (
            "Antenas de telefonia (ERBs) registradas pelo aparelho: MCC, MNC, LAC, CID, coordenada "
            "e horário. Rastro de DESLOCAMENTO independente de GPS (registra mesmo com localização "
            "desligada); nos extratos analisados só aparece em iPhone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por CID ou LAC."},
                "limite": {"type": "integer", "default": 300},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_conexoes_dispositivo",
        "description": (
            "Conexões de Wi-Fi e Bluetooth com outro aparelho (nome ou MAC do outro lado). O MAC "
            "permite CRUZAR APARELHOS de extrações diferentes: dois celulares que conectaram no "
            "mesmo dispositivo estiveram perto dele."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por nome do dispositivo ou MAC."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_trajetos",
        "description": (
            "Deslocamentos registrados por apps de mapa: endereço/coordenada de origem e destino, "
            "período e pontos do percurso — o par de-para de um mesmo deslocamento, diferente de "
            "listar_localizacoes (pontos soltos)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por trecho do endereço de origem ou destino."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_transferencias",
        "description": (
            "Transferências de valores registradas no aparelho (app de pagamento/carteira): valor, "
            "moeda, participantes, descrição, situação e datas — dinheiro amarrado ao aparelho "
            "periciado, que soma ao que vem de RIF e SIMBA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por participante, descrição ou aplicativo."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_cartoes_carteira",
        "description": (
            "Cartões e passes guardados na carteira do aparelho: descrição, emissor, tipo, código "
            "de barras, validade e local de compra — passagem, ingresso e cartão de fidelidade "
            "datam e localizam a pessoa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por descrição ou emissor."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_emails",
        "description": (
            "Busca os e-mails extraídos do aparelho por trecho do remetente, destinatário, assunto "
            "ou corpo (truncado em 2000 caracteres, 'corpo_truncado' avisa). Útil para comprovante, "
            "contrato, cadastro e conversa formal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Texto a procurar em remetente, destinatário, assunto ou corpo."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_atividades_conta",
        "description": (
            "\"Minha Atividade\" da conta Google (Google Takeout): busca, YouTube, Maps, Chrome "
            "etc., com produto, descrição, URL e horário. Dado que NÃO existe no UFDR — mostra o "
            "que a conta fez fora do aparelho periciado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Texto a procurar na descrição ou na URL da atividade."},
                "produto": {"type": "string", "description": "Filtra por produto (ex.: 'YouTube', 'Maps', 'Chrome', 'Image Search')."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_acessos_conta",
        "description": (
            "Log de acessos/login à conta na nuvem (Google Takeout, AccessLogActivity): data, IP, "
            "user-agent, produto e IDs de dispositivo/sessão. Dado que NÃO existe no UFDR — situe a "
            "conta por IP ou ache um aparelho que logou nela e não é o periciado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por IP, produto ou Android/iOS ID."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_dispositivos_conta",
        "description": (
            "Dispositivos conhecidos pela conta na nuvem (Google Takeout): modelo, fabricante, "
            "Android ID, IMEI, versão, serial. Dado que NÃO existe no UFDR — pode revelar um "
            "segundo celular/tablet que nunca foi apreendido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por modelo, fabricante, Android ID ou IMEI."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_perfil_conta",
        "description": (
            "Perfil da conta na nuvem (Google Takeout): nome, e-mails, data de criação, idioma, "
            "país, serviços e status. Dado que NÃO existe no UFDR — a identidade declarada perante "
            "o provedor, não necessariamente a do dono do aparelho."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_eventos_agenda",
        "description": (
            "Eventos da agenda/calendário da conta na nuvem (Google Takeout): título, local, "
            "período, organizador e participantes. Dado que NÃO existe no UFDR — inclui compromissos "
            "futuros, que o UFDR não traz."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por título, local, descrição ou participante."},
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_contas_financeiras",
        "description": (
            "Lista as contas bancárias/financeiras identificadas no aparelho (instituição, titular, "
            "agência, conta, tipo, saldo), vindas dos apps financeiros logados — a conta que a "
            "pessoa REALMENTE usa. tp_conta: banco, pagamento, cripto (nu_conta = endereço da "
            "carteira), aposta, capitalizacao, credito. Cruze com os RIFs (buscar_envolvidos_rif_"
            "filtrados) e chaves PIX em perguntas de movimentação financeira."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por instituição, titular ou número da conta."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_contas_usuario",
        "description": (
            "Lista as contas de aplicativo logadas no aparelho (Apple ID, Google, e-mail, redes "
            "sociais e demais apps) — a forma mais direta de amarrar o aparelho a uma identidade: o "
            "Apple ID/Google do celular é do DONO. Use para 'de quem é este aparelho' e para achar "
            "perfis/usernames em outros serviços."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por serviço, usuário ou titular."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_arquivos_dispositivo",
        "description": (
            "Busca no inventário do filesystem do aparelho — TODO arquivo, não só anexo de conversa: "
            "câmera, downloads, mídia de app, documentos, sistema. ATENÇÃO: é a maior tabela da "
            "extração (centenas de milhares de linhas) — filtre SEMPRE por 'termo' e/ou 'categoria'; "
            "sem filtro volta só os mais recentes. apagados=true mostra só o que foi excluído e "
            "recuperado (costuma ser o mais probatório)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Trecho do nome do arquivo ou do caminho (ex.: 'comprovante', 'DCIM', '.pdf')."},
                "categoria": {
                    "type": "string",
                    "enum": ["Imagem", "Vídeo", "Áudio", "Documento", "Compactado", "Outro"],
                    "description": "Restringe a uma categoria de arquivo.",
                },
                "apagados": {
                    "type": "boolean", "default": False,
                    "description": "Só arquivos com status diferente de 'Intact' (apagados/recuperados).",
                },
                "limite": {"type": "integer", "default": 100},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_texto_em_imagens",
        "description": (
            "Busca no TEXTO RECONHECIDO POR OCR dentro das imagens do aparelho: print de comprovante "
            "PIX, boleto, foto de documento (RG, CNH), conversa printada. Conteúdo que não existe em "
            "nenhuma outra tabela — se a resposta pode estar num print (valor, CPF, chave PIX), é "
            "aqui. Sem 'termo' devolve as imagens com texto mais recentes. Se vier vazio, o OCR "
            "pode ainda não ter rodado — ofereça processar um recorte das imagens com "
            "processar_texto_em_imagens (nunca o corpus inteiro de uma vez)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Trecho a procurar no texto da imagem (ex.: 'comprovante', 'PIX', um CPF, um valor)."},
                "limite": {"type": "integer", "default": 50},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "processar_texto_em_imagens",
        "description": (
            "DISPARA o OCR de um RECORTE de imagens do aparelho — nunca o corpus inteiro: ou "
            "'ids_arquivos' (ids de buscar_arquivos_dispositivo; o ideal, priorize screenshots da "
            "DCIM, inclusive apagados) OU recorte por 'termo' (nome/caminho, ex.: 'DCIM', 'IMG_') + "
            "'inicio'/'fim' (AAAA-MM-DD, data de modificação) + 'id_extracao'. Teto de 200 imagens "
            "por chamada; só entram imagens ainda sem OCR. Avise o investigador antes de disparar: "
            "é tarefa pesada de CPU. O job roda em background e o investigador acompanha o progresso "
            "(e cancela) pelo cartão que aparece nesta própria conversa e na tela Arquivos — não "
            "precisa ficar re-pollando: dispare uma vez, diga que começou e que o resultado sai no "
            "cartão. Ainda pode consultar com 'id_job' se precisar, e quando concluir leia o texto com "
            "buscar_texto_em_imagens."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_job": {"type": "integer", "description": "Consulta o status de um OCR já disparado por esta ferramenta."},
                "ids_arquivos": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Ids de imagens retornados por buscar_arquivos_dispositivo.",
                },
                "termo": {"type": "string", "description": "Recorte por trecho do nome ou do caminho do arquivo."},
                "inicio": {"type": "string", "description": "Data inicial de modificação (AAAA-MM-DD)."},
                "fim": {"type": "string", "description": "Data final de modificação (AAAA-MM-DD), inclusiva."},
                "id_extracao": {"type": "integer", "description": "Restringe a um aparelho (extração)."},
                "limite": {"type": "integer", "default": 50, "description": "Máximo de imagens nesta chamada (teto 200)."},
            },
        },
    },
    {
        "name": "gerar_planilha_consolidada",
        "description": (
            "Consolida MUITOS documentos anexados/IPLs do inquérito numa PLANILHA .xlsx gerada em "
            "background — use quando a resposta seria uma tabela grande demais para o chat (dezenas "
            "ou centenas de arquivos, linhas que não caberiam numa mensagem). NUNCA despeje uma "
            "tabela gigante na resposta para 'substituir' esta ferramenta: ela é cortada pelo teto "
            "de tokens e o 'continue' do investigador não recupera o resto. FLUXO EM DUAS ETAPAS: "
            "(1) chame com avaliar=true (com o mesmo pedido/colunas que você usaria) para contar os "
            "documentos e obter a duração estimada; explique então ao investigador o que o agente "
            "de IA faz, o tamanho da varredura e o tempo estimado, e PERGUNTE se pode acionar; "
            "(2) só depois de um 'sim' explícito chame de novo com avaliar=false, 'pedido' (o que "
            "procurar nos documentos) e 'colunas' (títulos das colunas — a coluna 'Fonte', com o "
            "documento de cada linha, é acrescentada sozinha). O job roda em background mesmo se o "
            "chat fechar: progresso, cancelamento e o download do .xlsx ficam no cartão desta "
            "própria mensagem — não fique re-pollando; se precisar, consulte com 'id_job'. Escopos "
            "de aparelho/RIF/SIMBA não são lidos — só documentos anexados e PDFs de IPL (o painel "
            "Fontes do chat define o recorte; sem seleção, todos os documentos do inquérito)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "avaliar": {
                    "type": "boolean", "default": False,
                    "description": "true = só contar documentos e estimar o tempo (passo 1, não dispara nada).",
                },
                "pedido": {
                    "type": "string",
                    "description": "O que extrair dos documentos, em uma frase clara (ex.: 'armas de fogo apreendidas, com registro, modelo, proprietário e data da apreensão').",
                },
                "colunas": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Títulos das colunas da planilha (máx. 15), na ordem desejada.",
                },
                "id_job": {"type": "integer", "description": "Consulta o status de uma consolidação já disparada por esta ferramenta."},
            },
        },
    },
    {
        "name": "listar_pessoas_identificadas",
        "description": (
            "Lista as entidades canônicas do inquérito — PESSOAS (e telefones, locais, veículos, "
            "organizações) que o sistema já identificou, com identificadores (telefone, CPF, e-mail, "
            "usuário) e relações afirmadas. COMECE POR AQUI em perguntas sobre gente ('quem são os "
            "envolvidos?', 'quem é fulano?'): o cruzamento já fundiu contatos + agenda + ligações + "
            "chaves PIX + contas, inclusive entre aparelhos. 'qt_evidencias' indica centralidade no caso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "termo": {"type": "string", "description": "Filtra por nome ou por identificador (telefone, CPF, e-mail…)."},
                "tipo": {
                    "type": "string",
                    "enum": ["pessoa", "telefone", "local", "veiculo", "organizacao", "outro"],
                    "description": "Restringe a um tipo de entidade.",
                },
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_deteccoes_padroes",
        "description": (
            "Lista os achados da última execução de 'Detectar padrões' na mesa de inquérito — padrões "
            "determinísticos cadastrados por um administrador (léxico, regex, temporal). Use antes de "
            "procurar por conta própria; se vier 'aviso' de que nunca rodou, informe o investigador "
            "em vez de tratar como 'nada encontrado'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "categoria": {"type": "string", "description": "Restringe a uma categoria de padrão (ex.: 'tráfico', 'financeiro', 'armas')."},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "listar_marcacoes_relatorio",
        "description": (
            "Lista os registros que o INVESTIGADOR marcou para o relatório final, com o comentário "
            "dele — julgamento humano sobre o que importa neste caso, diferente das detecções "
            "automáticas. Use em perguntas de síntese ('resuma o caso') para ancorar na linha de "
            "apuração que ele já segue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_inquerito": {"type": "integer"},
                "limite": {"type": "integer", "default": 200},
            },
            "required": ["id_inquerito"],
        },
    },
    {
        "name": "buscar_pessoa_referencia",
        "description": (
            "Busca na base de referência de pessoas (dados públicos/vazados, ~220M linhas: CPF, nome, "
            "sexo, nascimento) por nome (tolerante a erro de digitação/variação fonética) ou CPF exato."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "termo": {"type": "string", "description": "Nome a procurar (ignorado se cpf for informado)."},
                "cpf": {"type": "string", "description": "CPF, com ou sem pontuação."},
                "limite": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "buscar_candidato_referencia",
        "description": (
            "Busca na base de candidatos/eleições do TSE por nome civil, nome de urna ou CPF, "
            "opcionalmente filtrando por ano da eleição."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "termo": {"type": "string"},
                "cpf": {"type": "string"},
                "ano_eleicao": {"type": "integer"},
                "limite": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "cruzar_identidade",
        "description": (
            "Tenta ligar um nome/apelido dos dados extraídos a uma identidade real, cruzando contra "
            "pessoas (Serasa) e candidatos (TSE) por nome/CPF. Use depois de achar um contato "
            "relevante, quando o investigador quiser saber 'quem é essa pessoa'. Não cruza por "
            "telefone (as bases de referência não têm essa coluna)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "cpf": {"type": "string"},
                "limite": {"type": "integer", "default": 10},
            },
        },
    },
]

FUNCOES_IA = {
    "buscar_contato_extracao": buscar_contato_extracao,
    "buscar_mensagens": buscar_mensagens,
    "buscar_hibrida": buscar_hibrida,
    "buscar_ligacoes": buscar_ligacoes,
    "listar_localizacoes": listar_localizacoes,
    "buscar_notificacoes": buscar_notificacoes,
    "buscar_notas": buscar_notas,
    "buscar_navegacao": buscar_navegacao,
    "buscar_aplicativos": buscar_aplicativos,
    "listar_donos": listar_donos,
    "resumo_inquerito": resumo_inquerito,
    "listar_documentos_inquerito": listar_documentos_inquerito,
    "ler_documento_inquerito": ler_documento_inquerito,
    "buscar_pessoas_ipl": buscar_pessoas_ipl,
    "buscar_achados_ipl": buscar_achados_ipl,
    "obter_ipei": obter_ipei,
    "buscar_relacionados_ipei": buscar_relacionados_ipei,
    "buscar_anos_ipei": buscar_anos_ipei,
    "buscar_fontes_ipei": buscar_fontes_ipei,
    "buscar_contas_ipei": buscar_contas_ipei,
    "buscar_bens_ipei": buscar_bens_ipei,
    "buscar_notas_ipei": buscar_notas_ipei,
    "buscar_contatos_ipei": buscar_contatos_ipei,
    "obter_telefonia_operadora": obter_telefonia_operadora,
    "buscar_linhas_telefonia": buscar_linhas_telefonia,
    "buscar_chamadas_telefonia": buscar_chamadas_telefonia,
    "buscar_erbs_telefonia": buscar_erbs_telefonia,
    "buscar_numeros_telefonia": buscar_numeros_telefonia,
    "buscar_dados_moveis_telefonia": buscar_dados_moveis_telefonia,
    "buscar_encontros_telefonia": buscar_encontros_telefonia,
    "listar_ancoras_telefonia": listar_ancoras_telefonia,
    "timeline_financeira": timeline_financeira,
    "listar_passagens_simba": listar_passagens_simba,
    "obter_declaracao_irpf": obter_declaracao_irpf,
    "buscar_bens_direitos_irpf": buscar_bens_direitos_irpf,
    "buscar_dependentes_irpf": buscar_dependentes_irpf,
    "buscar_rendimentos_irpf": buscar_rendimentos_irpf,
    "buscar_dividas_irpf": buscar_dividas_irpf,
    "buscar_doacoes_irpf": buscar_doacoes_irpf,
    "buscar_comunicacoes_rif_filtradas": buscar_comunicacoes_rif_filtradas,
    "buscar_envolvidos_rif_filtrados": buscar_envolvidos_rif_filtrados,
    "listar_contas_simba": listar_contas_simba,
    "resumo_credito_simba": resumo_credito_simba,
    "listar_contrapartes_simba": listar_contrapartes_simba,
    "buscar_lancamentos_simba": buscar_lancamentos_simba,
    "buscar_chaves_pix": buscar_chaves_pix,
    "buscar_redes_wifi": buscar_redes_wifi,
    "buscar_buscas": buscar_buscas,
    "buscar_atividade_social": buscar_atividade_social,
    "buscar_compromissos": buscar_compromissos,
    "listar_antenas": listar_antenas,
    "buscar_conexoes_dispositivo": buscar_conexoes_dispositivo,
    "listar_trajetos": listar_trajetos,
    "listar_transferencias": listar_transferencias,
    "listar_cartoes_carteira": listar_cartoes_carteira,
    "buscar_emails": buscar_emails,
    "buscar_atividades_conta": buscar_atividades_conta,
    "buscar_acessos_conta": buscar_acessos_conta,
    "listar_dispositivos_conta": listar_dispositivos_conta,
    "buscar_perfil_conta": buscar_perfil_conta,
    "buscar_eventos_agenda": buscar_eventos_agenda,
    "listar_contas_financeiras": listar_contas_financeiras,
    "listar_contas_usuario": listar_contas_usuario,
    "buscar_arquivos_dispositivo": buscar_arquivos_dispositivo,
    "buscar_texto_em_imagens": buscar_texto_em_imagens,
    "processar_texto_em_imagens": processar_texto_em_imagens,
    "gerar_planilha_consolidada": gerar_planilha_consolidada,
    "listar_pessoas_identificadas": listar_pessoas_identificadas,
    "listar_deteccoes_padroes": listar_deteccoes_padroes,
    "listar_marcacoes_relatorio": listar_marcacoes_relatorio,
    "buscar_pessoa_referencia": buscar_pessoa_referencia,
    "buscar_candidato_referencia": buscar_candidato_referencia,
    "cruzar_identidade": cruzar_identidade,
}

# Ferramentas cujo escopo (id_inquerito) não pode ficar a critério da IA.
_FERRAMENTAS_COM_ESCOPO = {
    "buscar_contato_extracao", "buscar_mensagens", "buscar_hibrida", "buscar_ligacoes",
    "listar_localizacoes", "buscar_notificacoes", "buscar_notas", "buscar_navegacao",
    "buscar_aplicativos", "listar_donos", "resumo_inquerito",
    "listar_documentos_inquerito", "ler_documento_inquerito",
    "buscar_pessoas_ipl", "buscar_achados_ipl",
    "obter_declaracao_irpf", "buscar_bens_direitos_irpf", "buscar_dependentes_irpf",
    "buscar_rendimentos_irpf", "buscar_dividas_irpf", "buscar_doacoes_irpf",
    "obter_ipei", "buscar_relacionados_ipei", "buscar_anos_ipei", "buscar_fontes_ipei",
    "buscar_contas_ipei", "buscar_bens_ipei", "buscar_notas_ipei", "buscar_contatos_ipei",
    "obter_telefonia_operadora", "buscar_linhas_telefonia", "buscar_chamadas_telefonia",
    "buscar_erbs_telefonia", "buscar_numeros_telefonia", "buscar_dados_moveis_telefonia",
    "buscar_encontros_telefonia", "listar_ancoras_telefonia",
    "timeline_financeira", "listar_passagens_simba",
    "buscar_comunicacoes_rif_filtradas",
    "buscar_envolvidos_rif_filtrados",
    "listar_contas_simba", "resumo_credito_simba", "listar_contrapartes_simba", "buscar_lancamentos_simba",
    "timeline_financeira", "listar_passagens_simba",
    "buscar_chaves_pix", "buscar_redes_wifi", "buscar_emails", "listar_contas_financeiras",
    "listar_contas_usuario",     "buscar_arquivos_dispositivo", "buscar_texto_em_imagens",
    "processar_texto_em_imagens", "gerar_planilha_consolidada",
    "buscar_atividades_conta", "buscar_acessos_conta", "listar_dispositivos_conta",
    "buscar_perfil_conta", "buscar_eventos_agenda",
    # domínios da auditoria de aproveitamento do UFDR
    "buscar_buscas", "buscar_atividade_social", "buscar_compromissos", "listar_antenas",
    "buscar_conexoes_dispositivo", "listar_trajetos", "listar_transferencias",
    "listar_cartoes_carteira",
    "listar_pessoas_identificadas",
    "listar_deteccoes_padroes", "listar_marcacoes_relatorio",
}


# Ferramentas que PROCESSAM (job de background, CPU do servidor), não só leem. Diferente das de
# escrita da busca automática (que exigem conversa própria), estas ficam no chat normal — mas só
# quando a conversa tem um usuário dono (id_usuario): sem dono não há quem o job atribua, e a
# ferramenta some do schema (é o caso da busca automática de entidades e do cálculo de prompt).
_FERRAMENTAS_ACAO = {"processar_texto_em_imagens", "gerar_planilha_consolidada"}

# A consolidação de planilha só faz sentido onde há documentos de texto livre — fica de fora das
# conversas presas a aparelho/RIF/SIMBA/mensageiro (ver ferramentas_para_inquerito).
_ESCOPOS_COM_CONSOLIDACAO = {"ipl", "documento"}


def _vincular_inquerito(fn, id_inquerito: int):
    """Fixa id_inquerito na chamada, ignorando qualquer valor que a IA tente mandar pra esse campo."""
    def chamada(**kwargs):
        kwargs.pop("id_inquerito", None)
        return fn(id_inquerito=id_inquerito, **kwargs)
    return chamada


def _vincular_kwargs_fixos(fn, fixos: dict):
    """Fixa um ou mais kwargs extras na chamada, por cima de _vincular_inquerito — a IA nunca vê
    esses campos no schema nem consegue sobrescrevê-los (ex.: id_rif; ou fonte+id_extracao juntos
    para restringir buscar_mensagens a um único mensageiro/conversa)."""
    def chamada(**kwargs):
        for campo in fixos:
            kwargs.pop(campo, None)
        return fn(**fixos, **kwargs)
    return chamada


# Conversa restrita a um único documento/mensageiro/conversa (via botão "Prompt de IA
# específico" ou o painel flutuante em whatsapp.html/mensageiros.html): só as ferramentas do
# tipo escolhido ficam disponíveis, cada uma presa ao(s) ID(s) daquele escopo — a IA não enxerga
# os demais RIFs/SIMBAs/IPLs/extrações/mensageiros/conversas do mesmo inquérito.
#
# "extracao" (um aparelho) fica de fora ferramentas que não são por-aparelho: buscar_hibrida
# (o RAG híbrido combina a semântica com o grafo
# de entidades canônicas, os dois por inquérito inteiro — ver funcoes/rag_hibrido.py),
# resumo_inquerito (agrega todas as extrações), documentos/RIF/
# SIMBA/IPL (outros tipos de anexo) e listar_pessoas_identificadas/listar_deteccoes_padroes/
# listar_marcacoes_relatorio (entidades e curadoria do investigador, cruzadas no nível do
# inquérito — uma "pessoa" pode ter evidência em mais de uma extração).
#
# "wpp"/"sms"/"msg" (um mensageiro, de um aparelho ou do inquérito inteiro se nenhum aparelho
# específico estiver selecionado) e "conversa_wpp"/"conversa_sms"/"conversa_msg" (uma única
# conversa) só liberam buscar_mensagens, já travada na fonte (e id_extracao ou id_conversa).
_FERRAMENTAS_POR_ESCOPO_DOCUMENTO = {
    "rif": {"buscar_comunicacoes_rif_filtradas",
            "buscar_envolvidos_rif_filtrados"},
    "simba": {"listar_contas_simba", "resumo_credito_simba", "listar_contrapartes_simba", "buscar_lancamentos_simba",
              "timeline_financeira", "listar_passagens_simba"},
    "ipl": {"buscar_pessoas_ipl", "buscar_achados_ipl"},
    "irpf": {"obter_declaracao_irpf", "buscar_bens_direitos_irpf", "buscar_dependentes_irpf",
             "buscar_rendimentos_irpf", "buscar_dividas_irpf", "buscar_doacoes_irpf"},
    "ipei": {"obter_ipei", "buscar_relacionados_ipei", "buscar_anos_ipei", "buscar_fontes_ipei",
             "buscar_contas_ipei", "buscar_bens_ipei", "buscar_notas_ipei", "buscar_contatos_ipei"},
    "telefonia": {"obter_telefonia_operadora", "buscar_linhas_telefonia",
                  "buscar_chamadas_telefonia", "buscar_erbs_telefonia",
                  "buscar_numeros_telefonia", "buscar_dados_moveis_telefonia",
                  "buscar_encontros_telefonia", "listar_ancoras_telefonia"},
    "extracao": {
        "buscar_contato_extracao", "buscar_mensagens", "buscar_ligacoes", "listar_localizacoes",
        "buscar_notificacoes", "buscar_notas", "buscar_navegacao", "buscar_aplicativos",
        "listar_donos", "buscar_chaves_pix", "buscar_redes_wifi", "buscar_emails",
        "listar_contas_financeiras", "listar_contas_usuario", "buscar_arquivos_dispositivo",
        "buscar_texto_em_imagens", "processar_texto_em_imagens",
        "buscar_atividades_conta", "buscar_acessos_conta",
        "listar_dispositivos_conta", "buscar_perfil_conta", "buscar_eventos_agenda",
        # domínios da auditoria de aproveitamento do UFDR
        "buscar_buscas", "buscar_atividade_social", "buscar_compromissos", "listar_antenas",
        "buscar_conexoes_dispositivo", "listar_trajetos", "listar_transferencias",
        "listar_cartoes_carteira",
    },
    "documento": {"ler_documento_inquerito"},
    "wpp": {"buscar_mensagens"}, "sms": {"buscar_mensagens"}, "msg": {"buscar_mensagens"},
    "conversa_wpp": {"buscar_mensagens"}, "conversa_sms": {"buscar_mensagens"}, "conversa_msg": {"buscar_mensagens"},
}

# {tp_escopo: {nome_ferramenta: {kwarg_fixo: "campo do _CAMPO_ESCOPO_DOCUMENTO" | valor literal}}} —
# construído dinamicamente em _extras_por_ferramenta, que é onde o id_escopo de verdade entra.
_CAMPO_ESCOPO_DOCUMENTO = {
    "rif": "id_rif", "simba": "id_extrato", "ipl": "id_ipl", "irpf": "id_irpf", "ipei": "id_ipei",
    "telefonia": "id_telefonia",
    "extracao": "id_extracao", "documento": "id_documento",
}


def _extras_por_ferramenta(tp_escopo: str | None, id_escopo: int | None) -> dict:
    """kwargs extras (além de id_inquerito) fixados por ferramenta, conforme o tipo de escopo.

    RIF/SIMBA/IPL/extração/documento: um único campo (id_rif/id_extrato/id_ipl/id_extracao/
    id_documento) = id_escopo.
    Mensageiro (wpp/sms/msg): fixa fonte=tp_escopo e id_extracao=id_escopo em buscar_mensagens —
    id_escopo pode ser None aqui (nenhum aparelho específico selecionado = todo o inquérito,
    só aquele mensageiro).
    Conversa (conversa_wpp/conversa_sms/conversa_msg): fixa fonte + id_conversa=id_escopo.
    """
    if not tp_escopo:
        return {}
    if tp_escopo in _CAMPO_ESCOPO_DOCUMENTO:
        campo = _CAMPO_ESCOPO_DOCUMENTO[tp_escopo]
        return {nome: {campo: id_escopo} for nome in _FERRAMENTAS_POR_ESCOPO_DOCUMENTO[tp_escopo]}
    if tp_escopo in ("wpp", "sms", "msg"):
        return {"buscar_mensagens": {"fonte": tp_escopo, "id_extracao": id_escopo}}
    if tp_escopo.startswith("conversa_"):
        return {"buscar_mensagens": {"fonte": tp_escopo[len("conversa_"):], "id_conversa": id_escopo}}
    return {}


def _extras_por_escopos(escopos: list[tuple[str, int | None]]) -> dict:
    """Junta os kwargs fixos de vários escopos por ferramenta: cada campo vira o conjunto dos
    valores que os itens selecionados impõem. Campo com um único valor em todos os escopos é
    fixado no schema (como no escopo único); campo com valores diferentes vira validação na
    chamada — a IA escolhe, mas só vale o que foi selecionado."""
    por_ferramenta: dict[str, dict[str, set]] = {}
    for tp, id_ in escopos:
        for nome, fixos in _extras_por_ferramenta(tp, id_).items():
            alvo = por_ferramenta.setdefault(nome, {})
            for campo, valor in fixos.items():
                alvo.setdefault(campo, set()).add(valor)
    return por_ferramenta


def _vincular_kwargs_validados(fn, permitidos: dict):
    """Restringe kwargs a valores permitidos em tempo de chamada (multisseleção de escopos): a IA
    vê o campo no schema, mas valor fora do conjunto selecionado vira erro descritivo — o loop do
    agente devolve o erro como resultado de ferramenta e a IA re-chama com um valor válido."""
    def chamada(**kwargs):
        for campo, valores in permitidos.items():
            v = kwargs.get(campo)
            if v is not None and v not in valores:
                raise ValueError(
                    f"Campo '{campo}' está restrito nesta conversa: valores permitidos {sorted(valores)}."
                )
        return fn(**kwargs)
    return chamada


def ferramentas_para_inquerito(
    id_inquerito: int, tp_escopo: str | None = None, id_escopo: int | None = None,
    escopos: list[dict] | None = None, id_usuario: int | None = None,
    id_conversa_ia: int | None = None, id_processamento: int | None = None,
) -> tuple[list[dict], dict]:
    """Especificação de ferramentas + dispatch table prontos pro loop do agente, presos a um inquérito.

    O parâmetro id_inquerito some do schema exposto à IA (ela nunca escolhe o
    escopo) e é injetado automaticamente em cada chamada de ferramenta que precisa dele.

    Se tp_escopo/id_escopo forem informados (conversa criada a partir de "Prompt de IA
    específico" ou do painel flutuante — um RIF/extrato do SIMBA/IPL/aparelho/mensageiro/
    conversa), a lista de ferramentas se reduz às daquele escopo, e cada uma fica presa também
    ao(s) ID(s) do escopo — não só ao inquérito.

    Se escopos for informado (multisseleção do chat geral do inquérito — lista de
    {tp_escopo, id_escopo} com 'rif'/'simba'/'ipl'/'extracao'/'documento'), a lista vira a união das
    ferramentas dos itens selecionados; campos que os itens disputam com valores diferentes
    ficam visíveis à IA, porém validados contra o conjunto selecionado.
    """
    nomes_permitidos = None
    extras_validaveis = None  # {nome_ferramenta: {campo: set(valores)}} — validação na chamada
    if escopos:
        nomes_permitidos = set()
        for e in escopos:
            nomes_permitidos |= _FERRAMENTAS_POR_ESCOPO_DOCUMENTO.get(e["tp_escopo"], set())
        extras_validaveis = _extras_por_escopos(
            [(e["tp_escopo"], e.get("id_escopo")) for e in escopos]
        )
        # a consolidação varre os documentos do recorte inteiro de uma vez — entra na lista sempre
        # que o recorte tenha documento ou IPL, mesmo sem essas ferramentas individuais liberadas
        if any(e["tp_escopo"] in _ESCOPOS_COM_CONSOLIDACAO for e in escopos):
            nomes_permitidos.add("gerar_planilha_consolidada")
    elif tp_escopo:
        nomes_permitidos = _FERRAMENTAS_POR_ESCOPO_DOCUMENTO.get(tp_escopo)
        if tp_escopo in _ESCOPOS_COM_CONSOLIDACAO and nomes_permitidos is not None:
            nomes_permitidos = set(nomes_permitidos) | {"gerar_planilha_consolidada"}
    extras = _extras_por_ferramenta(tp_escopo, id_escopo)

    schemas = []
    for spec in FERRAMENTAS_IA:
        if nomes_permitidos is not None and spec["name"] not in nomes_permitidos:
            continue
        if spec["name"] in _FERRAMENTAS_ACAO and id_usuario is None:
            # sem usuário dono do job a ferramenta de ação não existe nesta conversa
            continue
        spec = copy.deepcopy(spec)
        if extras_validaveis:
            fixos = {
                campo: list(valores)[0]
                for campo, valores in extras_validaveis.get(spec["name"], {}).items()
                if len(valores) == 1
            }
            validaveis = {
                campo: valores
                for campo, valores in extras_validaveis.get(spec["name"], {}).items()
                if len(valores) > 1
            }
        else:
            fixos = extras.get(spec["name"], {})
            validaveis = {}
        if spec["name"] in _FERRAMENTAS_COM_ESCOPO:
            # some do schema exposto tanto id_inquerito (sempre) quanto qualquer campo que este
            # escopo já tenha fixado (ex.: 'fonte' em buscar_mensagens sob escopo de mensageiro/
            # conversa) — sem isso a IA veria a opção como se ainda pudesse escolher.
            campos_fora = {"id_inquerito"} | set(fixos)
            propriedades = spec["input_schema"]["properties"]
            for campo in campos_fora:
                propriedades.pop(campo, None)
            obrigatorios = spec["input_schema"].get("required", [])
            spec["input_schema"]["required"] = [c for c in obrigatorios if c not in campos_fora]
            for campo, valores in validaveis.items():
                prop = propriedades.get(campo)
                if prop:
                    prop = dict(prop)
                    lista = ", ".join(str(v) for v in sorted(valores))
                    prop["description"] = (
                        (prop.get("description") + " " if prop.get("description") else "")
                        + f"Valores permitidos nesta conversa: {lista}."
                    )
                    propriedades[campo] = prop
        schemas.append(spec)

    funcoes = {}
    for nome, fn in FUNCOES_IA.items():
        if nomes_permitidos is not None and nome not in nomes_permitidos:
            continue
        if nome in _FERRAMENTAS_ACAO:
            if id_usuario is None:
                continue
            # id_usuario dono do job + amarração do turno (conversa/processamento): a ferramenta de
            # ação nunca vê estes campos no schema — o worker injeta, para o job saber de que bolha veio
            fixos_acao = {
                "id_usuario": id_usuario,
                "id_conversa_ia": id_conversa_ia,
                "id_processamento": id_processamento,
            }
            if nome == "gerar_planilha_consolidada":
                # o recorte do painel "Fontes" é o que a consolidação varre — a IA não escolhe nem
                # pode ampliar; sem recorte (chat geral) vai None e o worker pega o inquérito todo
                fixos_acao["escopos"] = escopos or (
                    [{"tp_escopo": tp_escopo, "id_escopo": id_escopo}] if tp_escopo else None
                )
            fn = _vincular_kwargs_fixos(fn, fixos_acao)
        if nome in _FERRAMENTAS_COM_ESCOPO:
            fn = _vincular_inquerito(fn, id_inquerito)
            if extras_validaveis and nome in extras_validaveis:
                fixos = {
                    campo: list(valores)[0]
                    for campo, valores in extras_validaveis[nome].items()
                    if len(valores) == 1
                }
                if fixos:
                    fn = _vincular_kwargs_fixos(fn, fixos)
                validaveis = {
                    campo: valores
                    for campo, valores in extras_validaveis[nome].items()
                    if len(valores) > 1
                }
                if validaveis:
                    fn = _vincular_kwargs_validados(fn, validaveis)
            elif nome in extras:
                fn = _vincular_kwargs_fixos(fn, extras[nome])
        funcoes[nome] = fn
    return schemas, funcoes


# =====================================================================
# Ferramentas de escrita — só usadas pela busca automática de entidades (Mesa de
# Inquérito), nunca pelo chat normal do investigador (que fica só leitura, de propósito:
# ninguém quer que uma pergunta qualquer no chat crie entidade sem o investigador pedir).
# =====================================================================

FERRAMENTAS_ESCRITA_ENTIDADE = [
    {
        "name": "listar_entidades_existentes",
        "description": (
            "Lista as entidades já criadas neste inquérito (manuais, criadas pelo investigador, "
            "ou de buscas automáticas anteriores). Use antes de criar uma entidade nova pra não "
            "duplicar, e pra saber a quais entidades já existentes uma relação nova pode apontar."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "criar_entidade",
        "description": (
            "Cria uma entidade (um nó novo no grafo de investigação) presa a um registro de origem "
            "específico. tp_origem/id_origem devem vir de um resultado real de outra ferramenta "
            "(ex.: id_mensagem de buscar_mensagens vira tp_origem='wpp_mensagem', 'sms_mensagem' ou "
            "'msg_mensagem' conforme a fonte da mensagem — veja o campo 'fonte'/'referencia' do "
            "resultado (wpp→wpp_mensagem, sms→sms_mensagem, msg→msg_mensagem); "
            "id_contato de buscar_contato_extracao vira 'contato_wpp', "
            "'contato_sms' ou 'contato_agenda' conforme a origem retornada) — nunca invente um id. "
            "A entidade entra no grafo como SUGESTÃO, tracejada, até o investigador confirmar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tp_origem": {"type": "string", "enum": sorted(TIPOS_ORIGEM_VALIDOS)},
                "id_origem": {"type": "integer"},
                "comentario": {"type": "string", "description": "O que essa entidade representa e por que é relevante."},
                "justificativa": {
                    "type": "string",
                    "description": (
                        "A evidência concreta que embasa a criação: cite o dado retornado pela "
                        "ferramenta (trecho da mensagem, nome/telefone do contato, data) que "
                        "justifica marcar este registro. O investigador lê isto ao revisar a sugestão."
                    ),
                },
            },
            "required": ["tp_origem", "id_origem", "comentario", "justificativa"],
        },
    },
    {
        "name": "criar_relacao",
        "description": (
            "Cria uma relação (aresta do grafo) entre duas entidades já existentes — criadas "
            "nesta mesma busca ou anteriormente (ver listar_entidades_existentes). tipo_relacao é "
            "uma descrição curta e livre do vínculo (ex.: 'mencionada em', 'esteve em', 'contatou'). "
            "A relação entra no grafo como SUGESTÃO até o investigador confirmar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_entidade_origem": {"type": "integer"},
                "id_entidade_destino": {"type": "integer"},
                "tipo_relacao": {"type": "string"},
                "justificativa": {
                    "type": "string",
                    "description": "A evidência concreta que liga as duas entidades (dado real retornado por ferramenta).",
                },
            },
            "required": ["id_entidade_origem", "id_entidade_destino", "tipo_relacao", "justificativa"],
        },
    },
]


def ferramentas_para_busca_automatica(
    id_inquerito: int, id_usuario: int | None, id_busca: int | None = None
) -> tuple[list[dict], dict]:
    """Ferramentas de leitura (as mesmas do chat normal, escopadas) + as de escrita acima,
    prontas pro loop da busca automática de entidades. id_inquerito, id_usuario e id_busca
    nunca ficam a critério da IA — são fechados aqui dentro, não aparecem no schema exposto.
    id_busca marca a proveniência: cada entidade/relação criada sabe de qual execução veio."""
    schemas_leitura, funcoes_leitura = ferramentas_para_inquerito(id_inquerito)

    def _listar_existentes(**_kwargs):
        entidades = listar_entidades_inquerito(id_inquerito)
        return [
            {
                "id_entidade": e["id_entidade"],
                "tipo_origem": e["tp_origem"],
                "resumo": (e["rotulo_origem"] or "")[:150],
                "comentario": e["tx_comentario"],
                "criada_automaticamente": e["in_automatico"],
                "revisao": e["cd_revisao"],
            }
            for e in entidades
            if e["cd_revisao"] != "descartada"  # descartada pelo investigador não volta como contexto
        ]

    def _criar_entidade(tp_origem, id_origem, comentario, justificativa=None):
        id_entidade = criar_entidade(
            id_inquerito, tp_origem, id_origem, comentario, id_usuario,
            automatico=True, id_busca=id_busca, justificativa=justificativa,
        )
        return {"id_entidade": id_entidade}

    def _criar_relacao(id_entidade_origem, id_entidade_destino, tipo_relacao, justificativa=None):
        id_relacao = criar_relacao(
            id_inquerito, id_entidade_origem, id_entidade_destino, tipo_relacao, id_usuario,
            automatico=True, id_busca=id_busca, justificativa=justificativa,
        )
        return {"id_relacao": id_relacao}

    funcoes_escrita = {
        "listar_entidades_existentes": _listar_existentes,
        "criar_entidade": _criar_entidade,
        "criar_relacao": _criar_relacao,
    }

    return schemas_leitura + FERRAMENTAS_ESCRITA_ENTIDADE, {**funcoes_leitura, **funcoes_escrita}
