"""Funções acessórias de acesso ao Postgres (contagem de registros para o painel de ingestão)."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from funcoes import desempenho_utils
from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

# Host/porta internos do container do Postgres na rede do docker compose
# (não confundir com POSTGRES_PORT do .env, que é a porta exposta no host).
# O pool de REQUISIÇÃO pode passar pelo PgBouncer (PGBOUNCE_HOST/PGBOUNCE_PORT no .env —
# ver docs/backup.md e o serviço "pgbouncer" do docker-compose.yml); o Postgres continua sendo
# este host para: pool de conexões longas (ingestão), conexões dedicadas (leader_utils) e o
# psql de importação — caminhos que deliberadamente NÃO passam pelo pooler.
DB_HOST = "postgres"
DB_PORT = 5432

# min_size baixo porque a maior parte das operações é rápida; max_size deixa folga sob o
# max_connections do Postgres, somando também as threads soltas de OCR/classificação de
# imagem/comparação facial que chamam get_connection() fora do request.
#
# Os dois valores agora são dimensionados pelo hardware (funcoes/desempenho_utils.py) em vez de
# fixos: 20 conexões era pouco num servidor de 32 núcleos e demais num notebook. Os números
# abaixo continuam valendo como piso — é o que a instalação usa se o cálculo falhar.
_POOL_MIN_SIZE = 2
_POOL_MAX_SIZE = 20

# Teto do pool de conexões LONGAS (ingestão, migrações, entrega de nó). Separado do pool de
# requisição de propósito: uma ingestão segura a conexão por minutos e não pode canibalizar as
# vagas das telas. E quando o pool de requisição passa pelo PgBouncer, este aqui continua indo
# DIRETO ao Postgres — transação de minutos em cima de um pooler é justamente o que ele não
# deve intermediar. DB_POOL_LONGA_MAX ajusta (8 conexões de longa já é MUITA ingestão junto).
_POOL_LONGA_MAX = 8

# Quanto uma requisição espera por uma conexão livre antes de desistir. O default do
# psycopg_pool é 30 s, silencioso: com trinta analistas, meia dúzia de requisições penduradas
# meio minuto vira F5, que vira mais fila. Um 503 imediato com mensagem (ver o handler de
# PoolTimeout em main.py) é mais honesto. Job de segundo plano pode esperar bem mais: ninguém
# está olhando para a tela, e desistir significaria perder trabalho já começado.
_ESPERA_POOL_S = 10
_ESPERA_POOL_LONGA_S = 120

# Timeouts de sessão, em milissegundos, em dois perfis. Antes disto o sistema não tinha
# nenhum: uma query ruim segurava conexão e locks até alguém reiniciar o container.
#
#  - statement_timeout: teto de uma query. 0 = sem teto.
#  - lock_timeout: quanto esperar por um lock. Requisição não pode empilhar atrás do
#    CREATE INDEX de uma migração; falhar rápido com mensagem é melhor que trinta
#    requisições penduradas.
#  - idle_in_transaction_session_timeout: derruba a sessão que abriu transação e parou de
#    trabalhar. É o que mata conexão vazada — e é o mais perigoso de apertar, porque as fases
#    de ingestão ficam legitimamente idle entre um commit e o próximo, enquanto o Python lê o
#    próximo bloco do XML ou espera a próxima linha do IPED.
#
# Daí os dois perfis: o da requisição é apertado; o de `longa=True` é folgado e existe só para
# os laços de ingestão (ver get_connection). Cada valor pode ser fixado no .env.
_PADROES_TIMEOUT = {
    "DB_STATEMENT_TIMEOUT_MS": 60_000,
    "DB_LOCK_TIMEOUT_MS": 5_000,
    "DB_IDLE_TX_TIMEOUT_MS": 60_000,
    "DB_STATEMENT_TIMEOUT_LONGA_MS": 0,
    "DB_LOCK_TIMEOUT_LONGA_MS": 30_000,
    "DB_IDLE_TX_TIMEOUT_LONGA_MS": 900_000,
}

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_pool_longa: ConnectionPool | None = None
_pool_longa_lock = threading.Lock()
_perfil_web: tuple[str, str, str] = ("60000", "5000", "60000")
_perfil_longa: tuple[str, str, str] = ("0", "30000", "900000")


def _ms(env: dict, chave: str) -> str:
    """Um timeout do .env em milissegundos, como texto (é assim que set_config recebe).

    Aceita 0 (= sem limite), por isso não usa desempenho_utils.valor_int, que garante >= 1."""
    bruto = (env.get(chave) or "").strip()
    if bruto:
        try:
            return str(max(0, int(bruto)))
        except ValueError:
            logger.warning("%s inválido no .env (%r) — usando o padrão", chave, bruto)
    return str(_PADROES_TIMEOUT[chave])


def _aplicar_timeouts(conn: psycopg.Connection, *, longa: bool) -> None:
    """Fixa os três timeouts de sessão na conexão e commita.

    set_config em vez de SET porque SET não aceita placeholder. O `false` do terceiro argumento
    é "não é SET LOCAL": vale para a sessão inteira, não só para a transação corrente.

    O commit no fim não é opcional. Um SET de sessão é transacional no Postgres, então sem ele
    o rollback que o pool faz ao receber a conexão de volta desfaria o ajuste — e o psycopg_pool
    descarta a conexão cujo callback de configure/reset a deixe dentro de uma transação.

    O TimeZone=UTC não é ajuste de performance, é corretivo de relógio: as colunas de expiração
    de sessão/token são TIMESTAMP SEM fuso e o código grava nelas o "agora" em UTC (ver
    auth_utils._agora_utc) enquanto as renovações deslizantes comparam com now() do servidor.
    As duas convenções só coincidem sob sessão UTC — sem este SET, uma instalação cujo Postgres
    herda o fuso do host (America/Sao_Paulo) passa a validar sessão com 3 horas de atraso."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('statement_timeout', %s, false),"
            "       set_config('lock_timeout', %s, false),"
            "       set_config('idle_in_transaction_session_timeout', %s, false),"
            "       set_config('TimeZone', 'UTC', false)",
            _perfil_longa if longa else _perfil_web,
        )
    conn.commit()


def _configurar(conn: psycopg.Connection) -> None:
    """Roda uma vez, quando o pool abre uma conexão física nova."""
    _aplicar_timeouts(conn, longa=False)


def _devolver(conn: psycopg.Connection) -> None:
    """Roda quando a conexão volta para o pool (callback `reset`), DEPOIS de o `with
    pool.connection()` ter comitado a saída normal ou feito rollback em caso de exceção —
    é no `__exit__` da conexão, não aqui, que a transação pendente é desfeita.

    Devolve ao perfil de requisição (quem pegou com longa=True afrouxou os timeouts desta
    conexão) e ao autocommit desligado: migrar_utils liga o autocommit numa conexão do pool para
    rodar migração fora de transação e restaura no fim, mas uma conexão que voltasse ligada
    faria o commit() de uma rota virar no-op silencioso."""
    if conn.autocommit:
        conn.autocommit = False
    _aplicar_timeouts(conn, longa=False)


def _conninfo_base(env: dict, host: str, port: int) -> str:
    return psycopg.conninfo.make_conninfo(
        host=host,
        port=port,
        user=env.get("POSTGRES_USER", ""),
        password=env.get("POSTGRES_PASSWORD", ""),
        dbname=env.get("POSTGRES_DB", ""),
        connect_timeout=5,
        # detecta conexão morta por queda de rede ou reinício do banco, em vez de bloquear
        # para sempre numa leitura que nunca vai responder
        keepalives=1,
        keepalives_idle=30,
    )


def _host_web(env: dict) -> tuple[str, int]:
    """Host/porta do pool de REQUISIÇÃO: PgBouncer quando PGBOUNCE_HOST está no .env, senão o
    Postgres direto (instalação antiga e desenvolvimento seguem exatamente como antes)."""
    host = (env.get("PGBOUNCE_HOST") or "").strip()
    if not host:
        return DB_HOST, DB_PORT
    bruto = (env.get("PGBOUNCE_PORT") or "").strip()
    try:
        return host, int(bruto) if bruto else 6432
    except ValueError:
        logger.warning("PGBOUNCE_PORT inválido (%r) — usando 6432", bruto)
        return host, 6432


def _criar_pool(conninfo: str, max_size: int, timeout: float = _ESPERA_POOL_S,
                min_size: int | None = None) -> ConnectionPool:
    return ConnectionPool(
        conninfo,
        min_size=min(_POOL_MIN_SIZE if min_size is None else min_size, max_size),
        max_size=max_size,
        timeout=timeout,
        max_lifetime=1800,  # recicla a conexão a cada 30 min (memória do backend, plano velho)
        max_idle=300,
        check=ConnectionPool.check_connection,  # descarta conexão presa se o Postgres reiniciar
        configure=_configurar,
        reset=_devolver,
        open=False,
    )


def _criar_pool_web(env: dict) -> ConnectionPool:
    # Credenciais do Postgres só mudam editando o .env à mão e reiniciando o container (a tela de
    # Configurações só grava as chaves AI_*/EMBED_*) — por isso é seguro lê-las uma vez aqui em vez
    # de reparsear o .env a cada conexão, como o código fazia antes do pool.
    global _perfil_web, _perfil_longa
    _perfil_web = (
        _ms(env, "DB_STATEMENT_TIMEOUT_MS"),
        _ms(env, "DB_LOCK_TIMEOUT_MS"),
        _ms(env, "DB_IDLE_TX_TIMEOUT_MS"),
    )
    _perfil_longa = (
        _ms(env, "DB_STATEMENT_TIMEOUT_LONGA_MS"),
        _ms(env, "DB_LOCK_TIMEOUT_LONGA_MS"),
        _ms(env, "DB_IDLE_TX_TIMEOUT_LONGA_MS"),
    )
    host, port = _host_web(env)
    max_size = desempenho_utils.valor_int("DB_POOL_MAX", _POOL_MAX_SIZE)
    return _criar_pool(_conninfo_base(env, host, port), max_size)


def _criar_pool_longa(env: dict) -> ConnectionPool:
    """Pool das conexões longas: SEMPRE direto no Postgres (ver _POOL_LONGA_MAX). min_size 1 —
    a maior parte do tempo ninguém está ingerindo nada."""
    max_size = max(1, desempenho_utils.valor_int("DB_POOL_LONGA_MAX", _POOL_LONGA_MAX))
    return _criar_pool(_conninfo_base(env, DB_HOST, DB_PORT), max_size,
                       timeout=_ESPERA_POOL_LONGA_S, min_size=1)


def _abrir_pool_seguro(pool: ConnectionPool, espera_s: float) -> None:
    """Abre um pool recém-criado esperando as conexões mínimas COM teto.

    open(wait=True) sem timeout pendura a thread para sempre quando o Postgres não vem (ou
    quando a máquina é um NÓ, que não tem banco nenhum — foi assim que um /no/hash de arquivo
    inexistente congelou o threadpool inteiro da estação).

    Com timeout há uma armadilha: o wait() do psycopg_pool FECHA o pool quando estoura o prazo,
    e um pool fechado não reabre jamais — se o objeto quebrado ficasse publicado no global, todo
    get_connection() dali em diante devolveria PoolClosed até reiniciar o processo (o comentário
    antigo dizia "o scheduler segue tentando em segundo plano": falso, close() derruba o
    scheduler). Então, na falha, o pool morto sai de cena e a próxima chamada tenta de novo do
    zero — Postgres voltou, a próxima conexão sobe; não voltou, ela toma PoolTimeout de novo."""
    try:
        pool.open(wait=True, timeout=espera_s)
    except Exception:
        try:
            pool.close()
        except Exception:
            pass
        raise


def _obter_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # outra thread pode ter criado enquanto esperávamos o lock
                pool = _criar_pool_web(parse_env(read_env_text()))
                try:
                    _abrir_pool_seguro(pool, _ESPERA_POOL_S)
                except Exception:
                    _pool = None  # nunca publica pool que não abriu (ver _abrir_pool_seguro)
                    raise
                _pool = pool
    return _pool


def _obter_pool_longa() -> ConnectionPool:
    """Pool das conexões longas, criado na primeira `longa=True` (lazy: a subida da API não
    paga 1 conexão extra que pode passar o dia inteira sem uso)."""
    global _pool_longa
    if _pool_longa is None:
        with _pool_longa_lock:
            if _pool_longa is None:
                pool = _criar_pool_longa(parse_env(read_env_text()))
                try:
                    _abrir_pool_seguro(pool, _ESPERA_POOL_LONGA_S)
                except Exception:
                    _pool_longa = None  # idem: pool fechado não volta (ver _abrir_pool_seguro)
                    raise
                _pool_longa = pool
    return _pool_longa


def abrir_pool() -> None:
    """Abre o pool no startup da aplicação, para falhar cedo se o Postgres estiver inacessível
    em vez de só descobrir isso na primeira requisição."""
    _obter_pool()


def fechar_pool() -> None:
    """Fecha os dois pools de conexões — chamado no shutdown da aplicação."""
    global _pool, _pool_longa
    if _pool is not None:
        _pool.close()
        _pool = None
    if _pool_longa is not None:
        _pool_longa.close()
        _pool_longa = None


def tamanho_pool_vigente() -> int | None:
    """max_size do pool REALMENTE aberto, ou None se ele ainda não subiu.

    Diferente de ler DB_POOL_MAX do .env: se alguém editou o .env depois que a API subiu, o
    processo continua com o valor antigo — e é essa diferença que a tela Desempenho mostra."""
    return _pool.max_size if _pool is not None else None


def estatisticas_pool() -> dict:
    """Números do pool para a tela Desempenho e para o health check.

    `requests_waiting` sustentadamente acima de zero é o sintoma de conexão retida por job
    longo: as requisições normais ficam na fila esperando alguém devolver. É o número que diz
    que o pool está apertado ANTES de a aplicação começar a responder 503.

    O pool das conexões LONGAS (ingestão/migrações — ver _POOL_LONGA_MAX) aparece no campo
    `longa`: quando ele enche, o sintoma na tela é ingestão parada, e não requisição na fila."""
    if _pool is None:
        return {"aberto": False}
    dados = dict(_pool.get_stats())
    dados["aberto"] = True
    dados["max_size"] = _pool.max_size
    dados["min_size"] = _pool.min_size
    if _pool_longa is not None:
        longos = dict(_pool_longa.get_stats())
        longos["max_size"] = _pool_longa.max_size
        dados["longa"] = longos
    return dados


@contextmanager
def get_connection(longa: bool = False, espera_s: float | None = None) -> Iterator[psycopg.Connection]:
    """Obtém uma conexão do pool compartilhado com o Postgres.

    Uso inalterado: `with get_connection() as conn:` — a conexão volta pro pool (em vez de
    fechar) ao sair do bloco.

    `longa=True` é para os laços de ingestão que legitimamente seguram a conexão por minutos
    trabalhando (o laço do report.xml na extração, a carga do CSV do SIMBA, o laço do stdout do
    IPED, as migrações de schema): sem statement_timeout, com lock_timeout e idle_in_transaction
    folgados, e disposto a esperar mais por uma vaga no pool. NÃO usar para "esta consulta é
    pesada" numa rota — o teto de tempo do caminho de requisição é justamente o que impede uma
    tela de derrubar as outras.

    `espera_s` sobrescreve quanto tempo esperar por uma vaga no pool. Serve a quem não pode entrar
    na fila junto com as requisições de verdade — hoje o /api/health, que precisa responder
    "ocupado" rápido em vez de ficar pendurado esperando uma conexão.

    Semântica da transação na saída do bloco (psycopg_pool): saída NORMAL comita, saída por
    EXCEÇÃO desfaz. Os `conn.commit()` explícitos espalhados pelo código são redundantes com
    isso (mantidos por clareza), mas quem precisar DESCARTAR escrita tem que levantar exceção —
    sair do bloco sem commitar NÃO descarta nada, comita. O callback `reset` (_devolver) roda
    depois, só para devolver a conexão ao perfil de requisição."""
    if espera_s is None:
        espera_s = _ESPERA_POOL_LONGA_S if longa else None
    pool = _obter_pool_longa() if longa else _obter_pool()
    with pool.connection(timeout=espera_s) as conn:
        if longa:
            _aplicar_timeouts(conn, longa=True)
        yield conn


def nova_conexao_dedicada() -> psycopg.Connection:
    """Abre UMA conexão física direta com o Postgres (sem pool, sem PgBouncer), autocommit.

    É para donos de lock de SESSÃO (`pg_try_advisory_lock` de funcoes/leader_utils.py): o lock
    advisory de sessão vive enquanto a conexão vive, então precisa de uma conexão estável que
    ninguém mais pegue de volta — um pool (e qualquer pooler) está fora por definição. Morreu a
    conexão, morreu o lock: é exatamente esse o mecanismo de failover do líder."""
    env = parse_env(read_env_text())
    return psycopg.connect(
        _conninfo_base(env, DB_HOST, DB_PORT),
        # mesmo fuso UTC fixado nas conexões do pool (_aplicar_timeouts): donos de lock de
        # sessão também compar agora() com timestamps naive-UTC gravados no banco
        options="-c timezone=UTC",
        autocommit=True,
    )


def count_table(schema: str, table: str) -> int | None:
    """Conta as linhas de uma tabela; devolve None se a tabela ainda não existir (schema não inicializado)."""
    # nomes vêm de uma lista fixa no código (não de input do usuário); minúsculo porque o DDL cria as
    # tabelas sem aspas, e o Postgres dobra identificadores não citados para minúsculo automaticamente
    query = f'SELECT COUNT(*) FROM "{schema.lower()}"."{table.lower()}"'
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                return row[0] if row else 0
    except psycopg.errors.UndefinedTable:
        return None
    except psycopg.Error:
        return None
