"""Busca híbrida (RAG híbrido) do chat de IA: literal + sentido + grafo numa chamada só.

Reúne os três pilares de recuperação que já existiam separados no projeto:

- SEMÂNTICO: funcoes/embed_utils.buscar_semelhantes (cosseno pgvector no Postgres
  sobre EMB_TB01_EMBEDDINGS, índice HNSW) — acha o SENTIDO mesmo com gíria/linguagem cifrada;
- TEXTUAL: ILIKE + pg_trgm (similarity) sobre as mensagens e transcrições de
  áudio — acha a palavra/sigla/código exato, agora RANQUEADA por similaridade
  (a ordem por relevância é o que o RRF usa; buscar_mensagens ordena por data);
- GRAFO: entidades canônicas (ENT_TB05/06/07) — registros ligados às MESMAS
  pessoas/telefones/locais que as evidências já encontradas (evidências irmãs),
  que a busca por texto sozinha não conectaria.

Os resultados de cada pilar são fundidos por Reciprocal Rank Fusion (RRF)
ponderado (k=60), deduplicados por referência e limitados ao topo. Os pesos
são ÚNICOS e globais (sem ajuste por delegacia), configurados em
Configurações → RAG híbrido e gravados no .env como
RAG_PESO_SEMANTICO / RAG_PESO_TEXTUAL / RAG_PESO_GRAFO / RAG_LIMITE.
"""

import copy
import re
import threading
import time
from collections import OrderedDict

from funcoes.db_utils import get_connection
from funcoes.embed_utils import buscar_semelhantes
from funcoes.env_utils import parse_env, read_env_text

_RRF_K = 60                                # constante do RRF (padrão da literatura)
_PESOS_PADRAO = {"semantico": 0.5, "textual": 0.3, "grafo": 0.2}
_LIMITE_PADRAO = 15

# Cache do resultado fundido por consulta: um turno de IA pode chamar a mesma
# ferramenta várias vezes (ou rodar chamadas em paralelo no mesmo inquérito) —
# a busca semântica reembeddaria a consulta e o pilar textual reexecutaria a
# varredura. Chave: (id_inquerito, consulta normalizada, limite).
_CACHE_TTL_SEGUNDOS = 5 * 60
_CACHE_MAX_ITENS = 64
_cache_lock = threading.Lock()
_cache_resultados: "OrderedDict[tuple, tuple]" = OrderedDict()  # chave -> (expira_em, resultado)

# Palavras de ligação do português que não dizem nada sobre o caso (a busca
# literal filtra por elas e só polui o ranking de similaridade).
_STOPWORDS = {
    "a", "ao", "aos", "as", "aquele", "aquela", "aqueles", "aquelas", "aquilo",
    "com", "como", "da", "das", "de", "do", "dos", "e", "em", "entre", "era",
    "essa", "essas", "esse", "esses", "esta", "estas", "este", "estes", "eu",
    "foi", "foram", "ha", "isso", "isto", "ja", "mais", "mas", "me", "meu",
    "minha", "na", "nas", "no", "nos", "o", "os", "ou", "para", "pela", "pelas",
    "pelo", "pelos", "por", "que", "quem", "se", "sem", "ser", "sobre", "sua",
    "suas", "tem", "temos", "teu", "tu", "um", "uma", "umas", "uns", "voce",
}

_WORD = re.compile(r"\w+", re.UNICODE)
_REF_MSG = re.compile(r"^\[msg:(wpp|sms|msg):(\d+)\]$")

# tp_origem de mensagem (domínio de ENT_TB01/ENT_TB06) -> (fonte da referência, config de mensagens)
_TP_ORIGEM_MENSAGEM = {"wpp": "wpp_mensagem", "sms": "sms_mensagem", "msg": "msg_mensagem"}

_TAMANHO_TRECHO = 1200   # teto por trecho devolvido à IA (contém o contexto sem explodir tokens)


def _executar(query: str, params: dict) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# --- Configuração (pesos únicos/globais, .env) ---------------------------------

def _config_rag() -> dict:
    """Pesos e limite da busca híbrida, conforme Configurações → RAG híbrido.

    Valores salvos no .env (RAG_PESO_SEMANTICO, RAG_PESO_TEXTUAL, RAG_PESO_GRAFO,
    RAG_LIMITE). Sem chave configurada, usa os padrões abaixo — o pilar semântico
    tem o maior peso porque é o que pega o que a busca literal não pega (gíria,
    linguagem cifrada), e o grafo entra como expansão (é um complemento, nunca
    a fonte principal)."""
    env = parse_env(read_env_text())

    def _peso(chave: str, padrao: float) -> float:
        try:
            v = float(env.get(chave) or padrao)
        except ValueError:
            v = padrao
        return max(0.0, min(1.0, v))

    try:
        limite = int(env.get("RAG_LIMITE") or _LIMITE_PADRAO)
    except ValueError:
        limite = _LIMITE_PADRAO
    return {
        "pesos": {
            "semantico": _peso("RAG_PESO_SEMANTICO", _PESOS_PADRAO["semantico"]),
            "textual": _peso("RAG_PESO_TEXTUAL", _PESOS_PADRAO["textual"]),
            "grafo": _peso("RAG_PESO_GRAFO", _PESOS_PADRAO["grafo"]),
        },
        "limite": max(1, min(100, limite)),
    }


# --- Pilar textual ------------------------------------------------------------

def _extrair_termos(consulta: str) -> list[str]:
    """Palavras-chave da consulta (sem stopwords, mínimo 2 caracteres). Se a
    consulta for só stopwords (ex.: 'quem e o que'), cai para todas as palavras."""
    palavras = [p for p in _WORD.findall(consulta.lower()) if len(p) >= 2]
    termos = [p for p in palavras if p not in _STOPWORDS]
    return termos or palavras


def _select_textual_fonte(fonte: str) -> str:
    """SELECT de mensagens de UMA fonte no formato comum às três (mesmas colunas
    e ordem, para UNION ALL) — espelha ia_investigacao._select_mensagens_fonte,
    mas traz o texto E a transcrição de áudio concatenados (o ranking de
    similaridade precisa dos dois) e não filtra por contato/conversa."""
    from funcoes.ia_investigacao import _TABELAS_CONVERSA

    t = _TABELAS_CONVERSA[fonte]
    return f"""
        SELECT '{fonte}' AS fonte, m.id_mensagem, m.dt_mensagem,
               coalesce(m.tx_mensagem, '') || ' ' || coalesce(
                   (SELECT string_agg(a.tx_transcricao, ' | ')
                      FROM investigacao.{t['anexos']} a
                     WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL
                   ), '') AS texto
        FROM investigacao.{t['mensagens']} m
        JOIN investigacao.{t['conversas']} conv ON conv.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = conv.id_extracao
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (m.tx_mensagem ILIKE ANY(%(patterns)s)
               OR EXISTS (
                   SELECT 1 FROM investigacao.{t['anexos']} a
                   WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao ILIKE ANY(%(patterns)s)
               ))
    """


def _pilar_textual(id_inquerito: int, consulta: str, limite: int) -> list[dict]:
    """Mensagens por palavra exata/sigla, RANQUEADAS por similarity() do pg_trgm
    contra a consulta inteira (mais próxima primeiro; desempate por data)."""
    from funcoes.ia_investigacao import _FONTES_TODAS

    termos = _extrair_termos(consulta)
    if not termos:
        return []
    patterns = ["%" + t + "%" for t in termos]
    uniao = "\n        UNION ALL\n".join(_select_textual_fonte(f) for f in _FONTES_TODAS)
    query = f"""
        SELECT * FROM (
            {uniao}
        ) u
        ORDER BY greatest(similarity(u.texto, %(consulta)s), 0) DESC, u.dt_mensagem DESC NULLS LAST
        LIMIT %(limite)s
    """
    linhas = _executar(
        query,
        {"id_inquerito": id_inquerito, "patterns": patterns, "consulta": consulta, "limite": limite},
    )
    return [
        {"referencia": f"[msg:{ln['fonte']}:{ln['id_mensagem']}]", "trecho": (ln["texto"] or "").strip()[:_TAMANHO_TRECHO]}
        for ln in linhas
    ]


# --- Pilar semântico -----------------------------------------------------------

def _pilar_semantico(id_inquerito: int, consulta: str, limite: int) -> tuple[list[dict], str | None]:
    """Trechos mais próximos por SENTIDO (embeddings). O aviso de "não indexado"
    é propagado para a IA — o híbrido segue valendo com os outros dois pilares."""
    resp = buscar_semelhantes(id_inquerito, consulta, limite=limite)
    return [
        {"referencia": r["referencia"], "trecho": r["trecho"]}
        for r in resp.get("resultados", [])
    ], resp.get("aviso")


# --- Pilar grafo ---------------------------------------------------------------

def _resolver_referencias(referencias: list[str]) -> tuple[list[str], list[int]]:
    """Converte referências [msg:fonte:id] em pares (tp_origem, id_origem) do
    domínio de ENT_TB01/ENT_TB06. Referências de documento ([doc:...]) não têm
    evidência no grafo e são ignoradas."""
    tps, ids = [], []
    for ref in referencias:
        m = _REF_MSG.match(ref or "")
        if not m:
            continue
        tps.append(_TP_ORIGEM_MENSAGEM[m.group(1)])
        ids.append(int(m.group(2)))
    return tps, ids


def _pilar_grafo(id_inquerito: int, referencias: list[str], limite: int) -> tuple[list[dict], list[str]]:
    """Expande pelas entidades canônicas evidenciadas pelas referências conhecidas.

    Passo 1: acha as canônicas que têm qualquer uma das referências como
    evidência (ENT_TB06, índice IX48_ENT_TB06_ORIGEM).
    Passo 2: puxa as OUTRAS evidências dessas canônicas que forem mensagens
    (vínculo "irmão": a mesma pessoa/telefone aparece em outra conversa ou app)
    — viram resultados clicáveis [msg:...] com o texto buscado.
    Passo 3: os demais tipos de vínculo (ligação, contato, localização…) não
    têm marcador clicável no chat — entram só como contexto textual
    (ex.: "FULANO (pessoa) — mesmo número nas ligações").
    """
    tps, ids = _resolver_referencias(referencias)
    if not tps:
        return [], []

    canonicas = _executar(
        """
        SELECT DISTINCT ev.id_canonica
        FROM investigacao.ent_tb06_canonica_evidencias ev
        JOIN investigacao.ent_tb05_canonicas c ON c.id_canonica = ev.id_canonica
        WHERE c.id_inquerito = %(id_inquerito)s
          AND EXISTS (
              SELECT 1 FROM unnest(%(tps)s::text[], %(ids)s::bigint[]) AS par(tp, id_origem)
              WHERE ev.tp_origem = par.tp AND ev.id_origem = par.id_origem
          )
        """,
        {"id_inquerito": id_inquerito, "tps": tps, "ids": ids},
    )
    if not canonicas:
        return [], []
    ids_canonica = [c["id_canonica"] for c in canonicas]

    contexto = _executar(
        """
        SELECT c.no_entidade, c.tp_entidade, ev.tx_detalhe
        FROM investigacao.ent_tb05_canonicas c
        JOIN investigacao.ent_tb06_canonica_evidencias ev ON ev.id_canonica = c.id_canonica
        WHERE c.id_canonica = ANY(%(canonicas)s::bigint[])
          AND EXISTS (
              SELECT 1 FROM unnest(%(tps)s::text[], %(ids)s::bigint[]) AS par(tp, id_origem)
              WHERE ev.tp_origem = par.tp AND ev.id_origem = par.id_origem
          )
        ORDER BY c.no_entidade
        """,
        {"canonicas": ids_canonica, "tps": tps, "ids": ids},
    )
    contexto_grafo = [
        f"{c['no_entidade']} ({c['tp_entidade']})" + (f" — {c['tx_detalhe']}" if c["tx_detalhe"] else "")
        for c in contexto
    ]

    irmas = _executar(
        """
        SELECT ev.tp_origem, ev.id_origem
        FROM investigacao.ent_tb06_canonica_evidencias ev
        WHERE ev.id_canonica = ANY(%(canonicas)s::bigint[])
          AND ev.tp_origem IN ('wpp_mensagem', 'sms_mensagem', 'msg_mensagem')
          AND NOT EXISTS (
              SELECT 1 FROM unnest(%(tps)s::text[], %(ids)s::bigint[]) AS par(tp, id_origem)
              WHERE ev.tp_origem = par.tp AND ev.id_origem = par.id_origem
          )
        ORDER BY ev.id_evidencia
        LIMIT %(limite)s
        """,
        {"canonicas": ids_canonica, "tps": tps, "ids": ids, "limite": limite},
    )
    if not irmas:
        return [], contexto_grafo

    from funcoes.ia_investigacao import _TABELAS_CONVERSA

    por_fonte: dict[str, list[int]] = {}
    for ev in irmas:
        fonte = {"wpp_mensagem": "wpp", "sms_mensagem": "sms", "msg_mensagem": "msg"}[ev["tp_origem"]]
        por_fonte.setdefault(fonte, []).append(ev["id_origem"])

    resultados = []
    for fonte, ids_msg in por_fonte.items():
        t = _TABELAS_CONVERSA[fonte]
        textos = _executar(
            f"""
            SELECT m.id_mensagem,
                   coalesce(m.tx_mensagem,
                       (SELECT string_agg(a.tx_transcricao, ' | ')
                          FROM investigacao.{t['anexos']} a
                         WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL)) AS texto
            FROM investigacao.{t['mensagens']} m
            WHERE m.id_mensagem = ANY(%(ids)s::bigint[])
            """,
            {"ids": ids_msg},
        )
        for ln in textos:
            if not (ln["texto"] or "").strip():
                continue
            resultados.append({
                "referencia": f"[msg:{fonte}:{ln['id_mensagem']}]",
                "trecho": ln["texto"].strip()[:_TAMANHO_TRECHO],
            })
    return resultados, contexto_grafo


# --- Fusão RRF -----------------------------------------------------------------

def _rrf_fusion(pilares: dict[str, list[dict]], pesos: dict[str, float], limite: int) -> list[dict]:
    """Reciprocal Rank Fusion ponderado: score(ref) = soma de peso_pilar / (K + rank_pilar).

    O rank é por posição dentro de cada pilar (0-based) — por isso o pilar
    textual precisa vir ORDENADO por relevância, não por data. A dedup por
    referência mantém o trecho do primeiro pilar que a trouxe e registra a
    fonte; um registro que aparece em mais de um pilar acumula pontuação.
    """
    acumulado: dict[str, dict] = {}
    for pilar, itens in pilares.items():
        peso = pesos.get(pilar, 0.0)
        if peso <= 0 or not itens:
            continue
        for rank, item in enumerate(itens):
            ref = item["referencia"]
            entrada = acumulado.get(ref)
            if entrada is None:
                entrada = {"referencia": ref, "trecho": item.get("trecho", ""), "score": 0.0, "fonte": pilar}
                acumulado[ref] = entrada
            entrada["score"] += peso / (_RRF_K + rank + 1)
    return sorted(acumulado.values(), key=lambda x: -x["score"])[:limite]


# --- Orquestração --------------------------------------------------------------

def _copiar_resultado(resultado: dict) -> dict:
    return copy.deepcopy(resultado)


def buscar_hibrida(id_inquerito: int, consulta: str, limite: int = _LIMITE_PADRAO) -> dict:
    """Busca híbrida de evidências de um inquérito: literal + sentido + grafo,
    fundidas por RRF ponderado (pesos únicos do .env) e deduplicadas.

    Retorna dict com:
    - resultados: [{referencia ([msg:...]/[doc:...]), trecho, score, fonte
      (semantico|textual|grafo)}] — a IA cita a referência para o investigador
      abrir o registro original;
    - origens: quantos candidatos cada pilar encontrou (transparência);
    - aviso: se o pilar semântico não está disponível (inquérito não indexado);
    - contexto_grafo: vínculos de canônicas sem marcador clicável, como texto.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return {"resultados": [], "total": 0, "aviso": "Consulta vazia."}
    try:
        limite = int(limite or _LIMITE_PADRAO)
    except (TypeError, ValueError):
        limite = _LIMITE_PADRAO
    limite = max(1, min(100, limite))

    chave = (id_inquerito, consulta.lower(), limite)
    agora = time.monotonic()
    with _cache_lock:
        item = _cache_resultados.get(chave)
        if item is not None and item[0] > agora:
            _cache_resultados.move_to_end(chave)
            return _copiar_resultado(item[1])

    pesos = _config_rag()["pesos"]
    candidatos = 3 * limite  # candidatos por pilar antes da fusão (a fusão estreita)

    semantico, aviso = _pilar_semantico(id_inquerito, consulta, candidatos)
    textual = _pilar_textual(id_inquerito, consulta, candidatos)
    grafo, contexto_grafo = _pilar_grafo(
        id_inquerito, [i["referencia"] for i in semantico + textual], candidatos
    )
    fundidos = _rrf_fusion(
        {"semantico": semantico, "textual": textual, "grafo": grafo}, pesos, limite
    )

    resultado: dict = {
        "resultados": fundidos,
        "total": len(fundidos),
        "origens": {
            "semantico": len(semantico), "textual": len(textual), "grafo": len(grafo),
        },
    }
    if aviso:
        resultado["aviso"] = aviso
    if contexto_grafo:
        resultado["contexto_grafo"] = contexto_grafo

    with _cache_lock:
        _cache_resultados[chave] = (agora + _CACHE_TTL_SEGUNDOS, resultado)
        _cache_resultados.move_to_end(chave)
        while len(_cache_resultados) > _CACHE_MAX_ITENS:
            _cache_resultados.popitem(last=False)

    return _copiar_resultado(resultado)