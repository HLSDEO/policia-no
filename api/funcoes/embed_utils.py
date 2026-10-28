"""Busca semântica das mensagens do inquérito por embeddings.

Cada mensagem (texto ou transcrição de áudio) vira um vetor gerado por uma API de embeddings
OpenAI-compatível configurada no .env (EMBED_API_URL / EMBED_API_KEY / EMBED_MODEL). Os vetores
ficam em EMB_TB01_EMBEDDINGS como pgvector e a similaridade (cosseno) é calculada no próprio
Postgres (`vl_vetor <=> q`, com índice HNSW criado por _garantir_dimensao) — o Python não monta
mais matriz nenhuma. Resolve o problema da busca literal não achar gíria/linguagem cifrada. A
indexação roda em background com status em EMB_TB02_INDEXACOES (polling no front), com trava por
inquérito (índice único parcial) e trava por inatividade baseada em DT_HEARTBEAT (progresso real,
não idade da fila), igual aos outros jobs.
"""

import json
import os
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.ia_provedores import ErroProvedorIA, contexto_ssl_do_provedor

_TAMANHO_LOTE_EMBED = 64          # textos por requisição à API de embeddings
_MINUTOS_INDEX_STALE = 60         # libera indexação presa (servidor caiu no meio) sem progresso neste intervalo
_INDICE_HNSW = "ix93_emb_tb01_vetor"  # índice HNSW (cosseno) sobre VL_VETOR, criado por _garantir_dimensao
_EMBED_LOCAL_URL_PADRAO = "http://ollama:11434"  # serviço Ollama do docker-compose (modo local)
_TIMEOUT_EMBED = 300              # embeddings locais em CPU podem ser mais lentos que a API
_TIMEOUT_PULL = 1800             # baixar o modelo local (download único) pode levar minutos


def _post(url: str, headers: dict, corpo: dict, timeout: int = _TIMEOUT_EMBED, contexto_ssl=None) -> dict:
    """POST JSON com timeout configurável (o de ia_provedores é fixo em 60s, curto para CPU local)."""
    dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=contexto_ssl) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", errors="replace")[:500]
        raise ErroProvedorIA(f"A API de embeddings respondeu com erro {e.code}: {detalhe}")
    except urllib.error.URLError as e:
        raise ErroProvedorIA(f"Não foi possível conectar na API de embeddings: {e.reason}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ErroProvedorIA(f"Resposta da API de embeddings em formato inesperado: {e}")


class IndexacaoOcupada(Exception):
    """Já existe uma indexação semântica em andamento para este inquérito."""


# --- API de embeddings --------------------------------------------------------

def _modo_local() -> bool:
    return (parse_env(read_env_text()).get("EMBED_MODE", "api") or "api").strip() == "local"


def _config_embed() -> tuple[str, str, str, object]:
    """Config da busca semântica, conforme o modo escolhido em Configurações:
    - local: usa o Ollama do servidor (EMBED_LOCAL_URL, padrão o serviço do compose) — offline, sem chave;
    - api:   usa a URL e a chave próprias da busca semântica (EMBED_API_URL / EMBED_API_KEY);
             se não configuradas, reaproveita as da IA do prompt (AI_API_URL / AI_API_KEY) —
             compatibilidade com quem configurou antes de existir a aba própria.
    Nos dois casos o modelo de embeddings é EMBED_MODEL (o modelo de chat não serve como embedding).
    Devolve (url_base_openai, modelo, chave, contexto_ssl) — o contexto só é diferente de None no
    provedor com gateway de certificado próprio (EMBED_CA_CERT; vazio herda o AI_CA_CERT da aba
    Prompt IA); o modo local é HTTP, não tem TLS a validar."""
    env = parse_env(read_env_text())
    modelo = (env.get("EMBED_MODEL") or "").strip()
    if not modelo:
        raise ErroProvedorIA("Busca semântica não configurada. Em Configurações, defina o Modelo de embeddings.")

    if _modo_local():
        base = (env.get("EMBED_LOCAL_URL") or _EMBED_LOCAL_URL_PADRAO).strip().rstrip("/")
        return base + "/v1", modelo, "", None

    url = (env.get("EMBED_API_URL") or env.get("AI_API_URL") or "").strip()
    if not url:
        raise ErroProvedorIA("Busca semântica por API sem URL configurada. Configure a URL/chave da busca semântica.")
    chave = (env.get("EMBED_API_KEY") or env.get("AI_API_KEY") or "").strip()
    contexto = contexto_ssl_do_provedor(
        (env.get("EMBED_PROVIDER") or "").strip(),
        env.get("EMBED_CA_CERT") or env.get("AI_CA_CERT", ""),
    )
    return url, modelo, chave, contexto


def _garantir_modelo_local() -> None:
    """No modo local, garante que o modelo esteja baixado no Ollama antes de indexar (idempotente:
    se já existe, o pull retorna rápido). Roda no job de background, então um download demorado não
    trava a requisição — o status fica 'processando'."""
    if not _modo_local():
        return
    env = parse_env(read_env_text())
    base = (env.get("EMBED_LOCAL_URL") or _EMBED_LOCAL_URL_PADRAO).strip().rstrip("/")
    modelo = (env.get("EMBED_MODEL") or "").strip()
    # /api/pull é a API nativa do Ollama (fora do /v1); o campo canônico é "name"
    _post(f"{base}/api/pull", {}, {"name": modelo, "stream": False}, timeout=_TIMEOUT_PULL)


def gerar_embeddings(textos: list[str]) -> list[list[float]]:
    """Gera os vetores de uma lista de textos via a API de embeddings configurada (formato OpenAI)."""
    if not textos:
        return []
    url, modelo, chave, contexto = _config_embed()
    cabecalhos = {"Authorization": f"Bearer {chave}"} if chave else {}
    resp = _post(
        f"{url.rstrip('/')}/embeddings", cabecalhos, {"model": modelo, "input": textos},
        contexto_ssl=contexto,
    )
    dados = sorted(resp["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in dados]


# --- Indexação em background --------------------------------------------------

_SQL_MSGS_INDEXAR = """
    SELECT tp_fonte, id_mensagem, texto FROM (
        SELECT 'wpp' AS tp_fonte, m.id_mensagem,
               coalesce(m.tx_mensagem,
                   (SELECT string_agg(a.tx_transcricao, ' | ') FROM investigacao.wpp_tb05_anexos a
                     WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL)) AS texto
        FROM investigacao.wpp_tb04_mensagens m
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id)s
        UNION ALL
        SELECT 'sms', m.id_mensagem, m.tx_mensagem
        FROM investigacao.sms_tb04_mensagens m
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id)s
        UNION ALL
        SELECT 'msg', m.id_mensagem,
               coalesce(m.tx_mensagem,
                   (SELECT string_agg(a.tx_transcricao, ' | ') FROM investigacao.msg_tb05_anexos a
                     WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL)) AS texto
        FROM investigacao.msg_tb04_mensagens m
        JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE e.id_inquerito = %(id)s
    ) t
    WHERE nullif(btrim(texto), '') IS NOT NULL
      AND length(btrim(texto)) >= %(min_texto)s
      AND btrim(texto) ~ '[0-9A-Za-zÀ-ÿ][\\s\\S]*[0-9A-Za-zÀ-ÿ]'
      AND btrim(texto) !~* '^(https?://|www\\.)[^[:space:]]+$'
      AND NOT EXISTS (
          SELECT 1 FROM investigacao.emb_tb01_embeddings x
          WHERE x.id_inquerito = %(id)s AND x.tp_fonte = t.tp_fonte AND x.id_mensagem = t.id_mensagem
      )
"""


_SQL_DOCS_INDEXAR = """
    SELECT id_documento AS id_mensagem, tx_conteudo AS texto
    FROM investigacao.inq_tb04_documentos
    WHERE id_inquerito = %(id)s
      AND tx_erro IS NULL
      AND nullif(btrim(tx_conteudo), '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM investigacao.emb_tb01_embeddings x
          WHERE x.id_inquerito = %(id)s AND x.tp_fonte = 'doc' AND x.id_mensagem = id_documento
      )
"""

# Documentos anexados entram na indexação como trechos ("chunks") de tamanho fixo com sobreposição:
# o texto de um documento pode passar de um milhão de caracteres, muito além do contexto de
# qualquer modelo de embeddings — um trecho de ~4 mil caracteres cabe folgado em todos. Cada trecho
# vira uma linha com tp_fonte = 'doc', id_mensagem = id do documento e nr_trecho = ordem da fatia.
# _MAX_TRECHOS_DOCUMENTO trava o número de fatias por documento: sem o teto, um único PDF gigante
# geraria centenas de chamadas de embedding e horas de indexação — o resto do documento fica de
# fora da busca semântica, mas continua inteiro no TX_CONTEUDO para a tela e a IA lerem.
_TAMANHO_TRECHO_DOCUMENTO = 4000
_SOBREPOSICAO_TRECHO_DOCUMENTO = 200
_MAX_TRECHOS_DOCUMENTO = 400


# --- Política de conteúdo (o que MERECE ir para o embedding) -------------------
# A fila de um inquérito grande passa de 3 milhões de trechos, e a maior parte não agrega
# nada à busca semântica: a mesma corrente encaminhada mil vezes, "ok"/emoji, extrato
# bancário de 400 páginas. Cada chamada economizada são centenas de ms no Ollama/API.
# O conteúdo filtrado não desaparece: continua na busca literal, na tela e para a IA —
# só não ganha vetor.
def _min_texto() -> int:
    """Comprimento mínimo de uma mensagem para entrar no índice semântico
    (EMBED_MIN_TEXTO no ambiente, padrão 15; 0 desliga o corte por tamanho)."""
    try:
        return max(0, int(os.environ.get("EMBED_MIN_TEXTO", "15")))
    except ValueError:
        return 15


def _dedup_ativo() -> bool:
    """Deduplicação de lote ligada? (EMBED_DEDUP=0 desliga; padrão ligada)."""
    return (os.environ.get("EMBED_DEDUP", "1") or "1").strip().lower() not in ("0", "false", "nao", "off")


_DENSIDADE_ALFA_MINIMA = 0.30  # fatia de documento com menos de 30% letras é tabela/número/OCR lixo
_RE_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÿ]{4,}")


def _chave_dedup(texto: str) -> str:
    """Chave de deduplicação: caixa e espaços colapsados (o mesmo texto com formatação
    diferente é o mesmo vetor)."""
    return " ".join((texto or "").lower().split())


def _fatia_relevante(trecho: str) -> bool:
    """Heurística de valor semântico de uma fatia de documento: exige densidade mínima de
    letras e ao menos 3 palavras de 4+ caracteres. Derruba extratos, planilhas numéricas e
    lixo de OCR antes da chamada de embedding."""
    if not trecho:
        return False
    alfa = sum(1 for ch in trecho if ch.isalpha())
    if alfa / len(trecho) < _DENSIDADE_ALFA_MINIMA:
        return False
    return len(_RE_PALAVRA.findall(trecho)) >= 3


def _fatiar_documento(texto: str) -> list[str]:
    """Fatia um texto longo em trechos de tamanho fixo com sobreposição, para a indexação
    semântica, descartando as fatias sem conteúdo textual (_fatia_relevante). Trecho único
    quando o texto cabe inteiro; além de _MAX_TRECHOS_DOCUMENTO fatias, o resto é descartado
    (só a busca semântica perde — o texto completo segue no documento)."""
    texto = (texto or "").strip()
    if not texto:
        return []
    if len(texto) <= _TAMANHO_TRECHO_DOCUMENTO:
        return [texto] if _fatia_relevante(texto) else []
    fatias = []
    inicio = 0
    n = len(texto)
    while inicio < n and len(fatias) < _MAX_TRECHOS_DOCUMENTO:
        fim = min(inicio + _TAMANHO_TRECHO_DOCUMENTO, n)
        fatia = texto[inicio:fim]
        if _fatia_relevante(fatia):
            fatias.append(fatia)
        if fim == n:
            break
        inicio = fim - _SOBREPOSICAO_TRECHO_DOCUMENTO
    return fatias


def _liberar_indexacoes_travadas(id_inquerito: int) -> None:
    """Libera a trava por inatividade olhando o HEARTBEAT, não a idade da fila.

    Com dt_inicio qualquer indexação legítima acima de 60 minutos (milhões de trechos em CPU leva
    horas) era marcada 'erro' enquanto o worker antigo seguia vivo — e a tentativa nova partia por
    cima, com dois jobs somando carga no mesmo servidor. O worker grava dt_heartbeat a cada lote;
    sem progresso recente, aí sim o processo morreu e a trava pode ser quebrada."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.emb_tb02_indexacoes
                SET cd_status = 'erro', tx_erro = 'Indexação interrompida (sem progresso recente).', dt_fim = now()
                WHERE id_inquerito = %s AND cd_status IN ('pendente', 'processando')
                  AND dt_heartbeat < now() - make_interval(mins => %s)
                """,
                (id_inquerito, _MINUTOS_INDEX_STALE),
            )
        conn.commit()


def embeddings_configurados() -> bool:
    """True se a busca semântica está configurada (modelo de embeddings definido, URL quando via API).
    Usado para decidir se a extração encadeia a indexação automática sem levantar erro."""
    try:
        _config_embed()
        return True
    except ErroProvedorIA:
        return False


def _criar_linha_indexacao(id_inquerito: int) -> int:
    """Cria a linha de indexação (trava por inquérito via índice único parcial); devolve o id.
    Levanta ErroProvedorIA se não configurado e IndexacaoOcupada se já houver uma ativa."""
    _config_embed()  # falha cedo, com mensagem clara, se não estiver configurado
    _liberar_indexacoes_travadas(id_inquerito)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO investigacao.emb_tb02_indexacoes (id_inquerito) VALUES (%s) RETURNING id_indexacao",
                    (id_inquerito,),
                )
                id_indexacao = cur.fetchone()[0]
            conn.commit()
    except psycopg.errors.UniqueViolation:
        raise IndexacaoOcupada("Já existe uma indexação semântica em andamento para este inquérito.")
    return id_indexacao


def iniciar_indexacao(id_inquerito: int) -> int:
    """Cria a linha de indexação e dispara o job em background; devolve o id da indexação.
    Levanta IndexacaoOcupada se já houver uma ativa."""
    id_indexacao = _criar_linha_indexacao(id_inquerito)
    threading.Thread(target=_processar_indexacao, args=(id_indexacao, id_inquerito), daemon=True).start()
    return id_indexacao


def indexar_sincrono(id_inquerito: int, ao_progredir: Callable[[int, int], None] | None = None) -> int:
    """Roda a indexação semântica INLINE (mesma lógica do job de background), para encadear ao fim da
    extração. Bloqueia até terminar. Levanta ErroProvedorIA se não configurado / IndexacaoOcupada se
    já houver uma ativa. Falhas durante o processamento em si não são relançadas — ficam registradas
    no status da indexação (EMB_TB02), como no modo background.
    ao_progredir: opcional — chamado com (feitos, total) de trechos a cada lote embeddado; a
    extração usa para espelhar o progresso no badge dela (e manter o batimento do watchdog)."""
    id_indexacao = _criar_linha_indexacao(id_inquerito)
    _processar_indexacao(id_indexacao, id_inquerito, ao_progredir=ao_progredir)
    return id_indexacao


def _atualizar_indexacao(id_indexacao: int, set_sql: str, params: tuple,
                         com_guarda_status: bool = False) -> int:
    """Aplica um SET à linha de indexação; devolve o rowcount.

    `com_guarda_status=True` é a guarda de status dos FECHAMENTOS (concluido/erro/cancelado):
    sem ela, um worker que teve a linha ceifada pelo reaper (_liberar_indexacoes_travadas —
    que devolve a trava a outro worker) acorda no fim e carimba 'concluido' na linha morta,
    escondendo o 'erro' honesto e deixando dois pipelines rodando no mesmo inquérito sem
    ninguém ver. É o padrão já estabelecido em jobs_utils.progresso/_fechar ('um worker que
    foi ceifado não ressuscita a linha'). Os SETs de progresso/heartbeat do meio do laço têm
    guarda própria (ver _processar_indexacao)."""
    guarda = " AND cd_status IN ('pendente', 'processando')" if com_guarda_status else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE investigacao.emb_tb02_indexacoes SET {set_sql} "  # noqa: S608 (set_sql interno)
                f"WHERE id_indexacao = %s{guarda}",
                (*params, id_indexacao),
            )
            linhas = cur.rowcount
        conn.commit()
    return linhas


def _indexacao_cancelada(id_indexacao: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT fl_cancelar FROM investigacao.emb_tb02_indexacoes WHERE id_indexacao = %s", (id_indexacao,))
            row = cur.fetchone()
            return bool(row and row[0])


def solicitar_cancelamento_indexacao(id_inquerito: int) -> bool:
    """Sinaliza a indexação ativa; o worker encerra no próximo checkpoint."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.emb_tb02_indexacoes SET fl_cancelar = true WHERE id_inquerito = %s AND cd_status IN ('pendente', 'processando')",
                (id_inquerito,),
            )
            alteradas = cur.rowcount
        conn.commit()
    return bool(alteradas)


def _contar_pendentes(id_inquerito: int) -> int:
    """Total de trechos ainda sem embedding (para o QT_TOTAL da tela). Os trechos de documento
    são ESTIMADOS (comprimento / tamanho do trecho, com o teto por documento): a contagem exata
    exigira ler o texto inteiro de todos os documentos duas vezes. Progresso é exibição — a
    diferença de algumas dezenas de itens para o valor real não atrapalha; o que não pode é o
    número de itens em si faltar."""
    linhas = _executar(
        """
        SELECT (
            SELECT count(*) FROM ( """ + _SQL_MSGS_INDEXAR + """ ) m
        ) + (
            SELECT coalesce(sum(LEAST(%(max_trechos)s,
                GREATEST(1, ceil(length(btrim(tx_conteudo))::numeric / %(tam)s)))), 0)
            FROM investigacao.inq_tb04_documentos
            WHERE id_inquerito = %(id)s
              AND tx_erro IS NULL
              AND nullif(btrim(tx_conteudo), '') IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM investigacao.emb_tb01_embeddings x
                  WHERE x.id_inquerito = %(id)s AND x.tp_fonte = 'doc' AND x.id_mensagem = id_documento
              )
        ) AS total
        """,
        {"id": id_inquerito, "max_trechos": _MAX_TRECHOS_DOCUMENTO, "tam": _TAMANHO_TRECHO_DOCUMENTO,
         "min_texto": _min_texto()},
    )
    return int(linhas[0]["total"])


def _iterar_pendentes(id_inquerito: int, tamanho_lote: int):
    """Gera lotes de itens pendentes SEM empilhar tudo na memória.

    A versão antiga materializava a lista inteira antes do primeiro lote: num inquérito com
    milhões de trechos (o da retomada do 187 passou de 3,8 milhões) isso são vários GB de dicionários
    no processo da API — e o servidor pagava essa RAM por job, em cima do whisper e do Ollama.
    O cursor de servidor lê um lote por vez; o NOT EXISTS das queries congela o retrato do que
    estava pendente na abertura, e o ON CONFLICT do INSERT cobre qualquer corrida com reindexação
    concorrente."""
    with get_connection(longa=True) as conn:
        lote: list[dict] = []
        for sql, nome, extra in ((_SQL_MSGS_INDEXAR, "mensagens", {"min_texto": _min_texto()}),
                                 (_SQL_DOCS_INDEXAR, "documentos", {})):
            with conn.cursor(name=f"emb_pendentes_{nome}", row_factory=dict_row) as cur:
                cur.itersize = tamanho_lote
                cur.execute(sql, {"id": id_inquerito, **extra})
                for r in cur:
                    if "tp_fonte" in r:
                        itens = [{"tp_fonte": r["tp_fonte"], "id_mensagem": r["id_mensagem"],
                                  "nr_trecho": 0, "texto": r["texto"]}]
                    else:
                        # documento anexado: cada fatia é uma linha própria, numerada por nr_trecho
                        # (o índice único inclui o trecho)
                        itens = [{"tp_fonte": "doc", "id_mensagem": r["id_mensagem"],
                                  "nr_trecho": nr, "texto": trecho}
                                 for nr, trecho in enumerate(_fatiar_documento(r["texto"]))]
                    for item in itens:
                        lote.append(item)
                        if len(lote) >= tamanho_lote:
                            yield lote
                            lote = []
        if lote:
            yield lote


def _formatar_vetor(vetor: list[float]) -> str:
    """Forma textual que o pgvector lê ('[0.1,0.2,...]'). Usada no %s::vector das consultas —
    sem a dependência do pacote pgvector no Python.

    `repr` do float (a forma mais curta que round-tripa o float64 vindo do JSON) em vez de
    arredondamento fixo: o pgvector grava float32 e guarda até 9 dígitos, então str() preserva o
    valor exatamente como o modelo o devolveu — nada de perturbar o cosseno na formatação."""
    return "[" + ",".join(map(repr, vetor)) + "]"


def _dimensao_coluna(cur) -> int:
    """Dimensão declarada de VL_VETOR (atttypmod do pgvector), ou -1 com a coluna vector sem
    restrição — o estado logo depois da migração 0048, antes da primeira indexação."""
    cur.execute(
        """
        SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'investigacao.emb_tb01_embeddings'::regclass AND attname = 'vl_vetor'
        """
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else -1


def _indice_hnsw_existe(cur) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'investigacao' AND indexname = %s",
        (_INDICE_HNSW,),
    )
    return cur.fetchone() is not None


def _criar_indice_hnsw() -> None:
    """CONCURRENTLY para construir o HNSW sem travar buscas nem a própria indexação gravando —
    num milhão de vetores a construção é longa, e é exatamente durante ela que alguém vai perguntar.
    Exige autocommit (CREATE INDEX CONCURRENTLY não roda em transação); o commit pendente do lote
    anterior já fechou quando chegamos aqui."""
    with get_connection(longa=True) as conn:
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDICE_HNSW} "
                    "ON investigacao.emb_tb01_embeddings USING hnsw (vl_vetor vector_cosine_ops)"
                )
        finally:
            conn.autocommit = False


def _garantir_dimensao(dim: int) -> None:
    """Põe a coluna VL_VETOR em vector(<dim>) com o índice HNSW pronto — é por aqui que a troca
    de modelo de embeddings é absorvida:

    - com a coluna ainda sem dimensão (pós-migração 0048), descarta os vetores de outra dimensão
      (a regra antiga do array_length <> dim, agora sobre o tipo vector) — trocados no meio de uma
      reindexação deixariam duas dimensões misturadas na mesma tabela;
    - restringe a coluna (só depois de limpar: o cast vector→vector(dim) reprova linha torto);
    - cria o índice se não houver. Trocar a dimensão exige reconstruir o índice, que era do modelo
      velho — daí o DROP antes do ALTER.

    Raro (troca de modelo), pesado (a restrição reescreve a tabela) e uma vez só por troca."""
    with get_connection(longa=True) as conn:
        with conn.cursor() as cur:
            atual = _dimensao_coluna(cur)
            precisa_indice = False
            if atual != dim:
                if atual == -1:
                    cur.execute(
                        "DELETE FROM investigacao.emb_tb01_embeddings WHERE vector_dims(vl_vetor) <> %s",
                        (dim,),
                    )
                cur.execute(f"DROP INDEX IF EXISTS investigacao.{_INDICE_HNSW}")
                # o typmod (`vector(N)`) tem que ser CONSTANTE LITERAL no DDL: como %s o bind
                # parameter vira "type modifiers must be simple constants or identifiers".
                # dim vem do len() de um vetor gerado por nós — inteiro validado, sem input.
                cur.execute(
                    "ALTER TABLE investigacao.emb_tb01_embeddings "
                    f"ALTER COLUMN vl_vetor TYPE vector({int(dim)})"
                )
                precisa_indice = True
            else:
                precisa_indice = not _indice_hnsw_existe(cur)
        conn.commit()
    if precisa_indice:
        _criar_indice_hnsw()


def _processar_indexacao(id_indexacao: int, id_inquerito: int,
                         ao_progredir: Callable[[int, int], None] | None = None) -> None:
    try:
        _atualizar_indexacao(id_indexacao, "cd_status = 'processando', dt_heartbeat = now()", ())
        if _indexacao_cancelada(id_indexacao):
            _atualizar_indexacao(id_indexacao, "cd_status = 'cancelado', dt_fim = now()", (),
                                 com_guarda_status=True)
            return
        _garantir_modelo_local()  # no modo local, baixa o modelo no Ollama se ainda não houver

        # sonda a dimensão do modelo atual e põe coluna + índice nesse estado (modelo trocado desde
        # a última indexação: os vetores velhos são descartados e re-embeddados, não ficam presos)
        dim_atual = len(gerar_embeddings(["."])[0])
        _garantir_dimensao(dim_atual)

        total = _contar_pendentes(id_inquerito)
        _atualizar_indexacao(id_indexacao, "qt_total = %s, dt_heartbeat = now()", (total,))

        feitos = 0
        for lote in _iterar_pendentes(id_inquerito, _TAMANHO_LOTE_EMBED):
            if _indexacao_cancelada(id_indexacao):
                _atualizar_indexacao(id_indexacao, "cd_status = 'cancelado', dt_fim = now()", (),
                                     com_guarda_status=True)
                return
            if _dedup_ativo():
                # o mesmo texto no lote (corrente encaminhada, "bom dia" repetido) paga UMA
                # chamada de API: embeda os distintos e redistribui o vetor para cada cópia
                chaves: dict[str, int] = {}
                distintos: list[str] = []
                posicoes: list[int] = []
                for r in lote:
                    chave = _chave_dedup(r["texto"])
                    if chave not in chaves:
                        chaves[chave] = len(distintos)
                        distintos.append(r["texto"])
                    posicoes.append(chaves[chave])
                vetores_distintos = gerar_embeddings(distintos)
                vetores = [vetores_distintos[i] for i in posicoes]
            else:
                vetores = gerar_embeddings([r["texto"] for r in lote])
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO investigacao.emb_tb01_embeddings
                            (id_inquerito, tp_fonte, id_mensagem, nr_trecho, tx_trecho, vl_vetor)
                        VALUES (%s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (id_inquerito, tp_fonte, id_mensagem, nr_trecho) DO NOTHING
                        """,
                        [(id_inquerito, r["tp_fonte"], r["id_mensagem"], r["nr_trecho"], r["texto"],
                          _formatar_vetor(vetor)) for r, vetor in zip(lote, vetores)],
                    )
                    # progresso e heartbeat no MESMO commit da gravacao: o heartbeat da trava por
                    # inatividade so vale se testemunhar trabalho de verdade. A guarda
                    # cd_status='processando' e o rowcount abaixo fazem do proprio heartbeat o
                    # checkpoint de posse: se o reaper ceifou esta linha (ou outro caminho a
                    # fechou), o proximo pipeline ja e' dono do inquerito e este para na hora,
                    # em vez de somar carga e embeddings em dobrado por horas a fio
                    cur.execute(
                        "UPDATE investigacao.emb_tb02_indexacoes SET qt_feitos = qt_feitos + %s, dt_heartbeat = now() "
                        "WHERE id_indexacao = %s AND cd_status = 'processando'",
                        (len(lote), id_indexacao),
                    )
                    ainda_nossa = cur.rowcount > 0
                conn.commit()
            if not ainda_nossa:
                logger.warning(
                    "Indexação %s (inquérito %s) interrompida: a linha deixou de ser 'processando' "
                    "— outro worker assumiu o inquérito, este para para não indexar em dobrado",
                    id_indexacao, id_inquerito)
                return
            feitos += len(lote)
            if ao_progredir:
                try:
                    ao_progredir(feitos, total)
                except Exception:
                    pass

        _atualizar_indexacao(id_indexacao, "cd_status = 'concluido', dt_fim = now()", (),
                             com_guarda_status=True)
    except Exception as exc:
        # com_guarda_status: se a linha ja foi ceifada ('erro' do reaper) ou fechada por outro
        # caminho, este worker nao a ressuscita nem pisa o estado dela (ver _atualizar_indexacao)
        _atualizar_indexacao(id_indexacao, "cd_status = 'erro', tx_erro = %s, dt_fim = now()",
                             (str(exc)[:2000],), com_guarda_status=True)


def obter_indexacao(id_inquerito: int) -> dict | None:
    """Status da indexação semântica mais recente do inquérito (para polling), ou None."""
    query = """
        SELECT id_indexacao, cd_status, qt_total, qt_feitos, tx_erro, dt_inicio, dt_fim,
               (SELECT count(*) FROM investigacao.emb_tb01_embeddings x WHERE x.id_inquerito = %(id)s) AS qt_indexado
        FROM investigacao.emb_tb02_indexacoes
        WHERE id_inquerito = %(id)s
        ORDER BY id_indexacao DESC LIMIT 1
    """
    linhas = _executar(query, {"id": id_inquerito})
    return linhas[0] if linhas else None


# --- Busca por similaridade (pgvector) -----------------------------------------


def _executar(query: str, params: dict) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# A busca era o caminho caro da feature: sem pgvector, cada pergunta (e o RAG híbrido chama o
# buscar_semelhantes varias vezes no mesmo turno) relia TODOS os vetores do inquérito do Postgres
# e remontava uma matriz numpy no processo — minutos de CPU e dezenas de GB de RAM num inquérito
# de 1 milhão de mensagens, e o "cache" de matriz só adiava o miss. Agora o ranking mora no banco:
# `ORDER BY vl_vetor <=> %(q)s::vector LIMIT n`, servido pelo índice HNSW de _garantir_dimensao.
# O Python só envia o vetor da pergunta (uma chamada de embedding) e recebe o top-K com o texto.


def buscar_semelhantes(id_inquerito: int, consulta: str, limite: int = 20) -> dict:
    """Busca as mensagens e trechos de documentos anexados semanticamente mais próximos de uma
    consulta em linguagem natural (ex.: 'negociação de venda de drogas', 'ameaça de morte',
    'combinação de encontro'). Complementa a busca literal — acha o sentido mesmo com
    gíria/linguagem cifrada. Requer o inquérito indexado.

    O filtro por dimensão (vector_dims) é cinto e suspensório: a coluna restringe a dimensão na
    indexação, mas entre a migração 0048 e a primeira reindexação a tabela pode responder por um
    modelo que não é o atual — comparar dimensões diferentes é erro no cast, então essas linhas
    ficam de fora e o aviso orienta a reindexar."""
    consulta = (consulta or "").strip()
    if not consulta:
        return {"resultados": [], "aviso": "Consulta vazia."}

    q = _formatar_vetor(gerar_embeddings([consulta])[0])

    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # afinação só desta transação. A busca é sempre filtrada por inquérito, e o HNSW
                # filtrado precisa de ef_search generoso + varredura iterativa para não voltar com
                # menos que o pedido quando o filtro descarta metade do índice. GUC desconhecido
                # (versão do pgvector sem o parâmetro) não pode derrubar a busca — daí o try.
                for ajuste in (f"SET LOCAL hnsw.ef_search = {max(100, limite * 4)}",
                               "SET LOCAL hnsw.iterative_scan = relaxed_order"):
                    try:
                        cur.execute(ajuste)
                    except psycopg.Error:
                        conn.rollback()
                cur.execute(
                    """
                    SELECT tp_fonte, id_mensagem, tx_trecho,
                           1 - (vl_vetor <=> %(q)s::vector) AS score,
                           count(*) OVER () AS total
                    FROM investigacao.emb_tb01_embeddings
                    WHERE id_inquerito = %(id)s AND vector_dims(vl_vetor) = vector_dims(%(q)s::vector)
                    ORDER BY vl_vetor <=> %(q)s::vector
                    LIMIT %(lim)s
                    """,
                    {"q": q, "id": id_inquerito, "lim": limite},
                )
                linhas = cur.fetchall()
            conn.commit()
    except psycopg.Error as exc:
        # coluna vector ainda não existe (migração 0048 pendente) ou extensão fora do ar: aviso,
        # não 500 — a busca literal e o resto do RAG continuam funcionando
        return {"resultados": [], "aviso": f"Busca semântica indisponível no banco: {str(exc).splitlines()[0]}"}

    if not linhas:
        sonda = _executar(
            "SELECT count(*) AS qt FROM investigacao.emb_tb01_embeddings WHERE id_inquerito = %(id)s",
            {"id": id_inquerito},
        )
        if not sonda or sonda[0]["qt"] == 0:
            return {"resultados": [], "aviso": "Inquérito ainda não indexado para busca semântica — peça ao investigador para indexar."}
        return {"resultados": [], "aviso": "Os vetores indexados têm dimensão diferente do modelo atual — reindexe o inquérito."}

    resultados = []
    for ln in linhas:
        # mensagem é [msg:fonte:id] (abre a mensagem original); documento anexado é [doc:id]
        # (abre o texto extraído na tela de investigação)
        if ln["tp_fonte"] == "doc":
            referencia = f"[doc:{ln['id_mensagem']}]"
        else:
            referencia = f"[msg:{ln['tp_fonte']}:{ln['id_mensagem']}]"
        resultados.append({
            "referencia": referencia,
            "trecho": ln["tx_trecho"],
            "score": round(float(ln["score"]), 3),
        })
    return {"resultados": resultados, "total_indexado": int(linhas[0]["total"])}
