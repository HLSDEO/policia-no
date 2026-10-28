"""Dimensionamento automático da aplicação ao hardware disponível.

Antes deste módulo os limites de concorrência e de cache eram constantes no código, escolhidas
para um servidor específico: pool de 20 conexões, 8 zips de .ufdr em cache, 4 inquéritos de
matriz de embeddings. O mesmo número servia a um notebook de 8 GB e a um servidor de 247 GB —
apertado demais num, desperdício no outro.

Aqui as constantes viram fórmulas sobre (núcleos, RAM), sempre com piso e teto: o piso é o valor
que estava no código antes (nenhuma instalação piora), e o teto evita que uma máquina enorme
reserve memória que nunca vai usar. Quem quiser fixar um valor escreve a chave correspondente no
.env — explícito sempre vence o calculado.

Ordem de precedência de cada valor:

    chave explícita no .env  >  perfil DESEMPENHO_PERFIL  >  hardware detectado

Nada aqui reinicia nada: os valores de processo (pool, threads, caches) são lidos no startup da
API, e os PG_* são lidos pelo docker compose ao subir o Postgres. A tela Administração →
Desempenho mostra, por campo, qual dos dois casos é.
"""

import logging
import os
from pathlib import Path

from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

MB = 1024 * 1024
GB = 1024 * MB

# Hardware assumido por cada perfil, para quem prefere escolher um tamanho a confiar na detecção
# (ou para simular o servidor pequeno a partir do grande, antes de instalar lá).
PERFIS = {
    "pequeno": (4, 8 * GB),
    "medio": (8, 32 * GB),
    "grande": (16, 64 * GB),
}


def _clamp(minimo: int, valor: float, maximo: int) -> int:
    """Valor inteiro preso entre piso e teto — a forma de toda fórmula deste módulo."""
    return int(max(minimo, min(int(valor), maximo)))


# ---------------------------------------------------------------------------
# Detecção de hardware
# ---------------------------------------------------------------------------
#
# psutil.virtual_memory() e os.cpu_count() enxergam a máquina HOSPEDEIRA, não o container: sem
# limite no compose isso é justamente o que queremos, mas se um dia entrar mem_limit/cpus no
# docker-compose.yml os dois passariam a mentir, e dimensionaríamos caches para uma RAM que o
# container não pode tocar (o OOM killer resolveria por nós). Por isso o cgroup vem primeiro.

_CGROUP_V2_MEM = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEM = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_V2_CPU = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CPU_PERIODO = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")

# "sem limite" no cgroup v1 é um número absurdo (não um sentinela): descarta qualquer coisa
# acima de 1 PB em vez de comparar com o valor mágico exato, que varia com a página de memória.
_LIMITE_IRREAL = 1024 * GB * 1024


def _ler_numero(caminho: Path) -> int | None:
    try:
        return int(caminho.read_text().strip())
    except (OSError, ValueError):
        return None


def _memoria_cgroup() -> int | None:
    valor = _ler_numero(_CGROUP_V2_MEM)  # v2 grava a string "max" quando não há limite
    if valor is None:
        valor = _ler_numero(_CGROUP_V1_MEM)
    if valor is None or valor <= 0 or valor >= _LIMITE_IRREAL:
        return None
    return valor


def _cpus_cgroup() -> int | None:
    try:
        bruto = _CGROUP_V2_CPU.read_text().split()
        if bruto and bruto[0] != "max":
            return max(1, round(int(bruto[0]) / int(bruto[1])))
    except (OSError, ValueError, IndexError):
        pass
    quota = _ler_numero(_CGROUP_V1_CPU_QUOTA)
    periodo = _ler_numero(_CGROUP_V1_CPU_PERIODO)
    if quota and periodo and quota > 0:
        return max(1, round(quota / periodo))
    return None


def detectar_hardware() -> dict:
    """(núcleos, RAM em bytes) realmente disponíveis a este processo, e de onde o número veio.

    A origem interessa à tela: "cgroup" significa que o container tem limite configurado e é ele
    que manda; "host" significa que o container vê a máquina inteira."""
    cpus = _cpus_cgroup()
    origem_cpu = "cgroup"
    if cpus is None:
        cpus = os.cpu_count() or 1
        origem_cpu = "host"

    memoria = _memoria_cgroup()
    origem_memoria = "cgroup"
    if memoria is None:
        origem_memoria = "host"
        try:
            import psutil

            memoria = psutil.virtual_memory().total
        except Exception:  # psutil indisponível/sem permissão: assume um servidor modesto
            logger.warning("Não foi possível detectar a memória; assumindo 8 GB")
            memoria = 8 * GB

    return {
        "cpus": max(1, int(cpus)),
        "memoria_bytes": max(1 * GB, int(memoria)),
        "origem_cpu": origem_cpu,
        "origem_memoria": origem_memoria,
    }


def _base_dimensionamento(env: dict) -> dict:
    """Hardware que serve de base para as fórmulas: o detectado, ou o do perfil escolhido."""
    perfil = (env.get("DESEMPENHO_PERFIL") or "auto").strip().lower()
    if perfil in PERFIS:
        cpus, memoria = PERFIS[perfil]
        return {"cpus": cpus, "memoria_bytes": memoria, "origem_cpu": "perfil",
                "origem_memoria": "perfil", "perfil": perfil}
    hardware = detectar_hardware()
    hardware["perfil"] = "auto"
    return hardware


# ---------------------------------------------------------------------------
# Fórmulas
# ---------------------------------------------------------------------------
#
# Cada entrada é (chave no .env, função que calcula a partir de cpus/RAM, quando passa a valer).
# O "quando" é o que a tela usa para dizer se falta reiniciar algo — ver QUANDO_* abaixo.

QUANDO_JA = "ja"                 # o módulo relê o .env a cada chamada
QUANDO_REINICIAR_API = "api"     # lido no startup do processo da API
QUANDO_REINICIAR_BANCO = "banco"  # lido pelo compose ao subir o Postgres
QUANDO_RECRIAR_CONTAINER = "container"  # entra no comando do uvicorn: exige `docker compose up -d`


def _calcular(cpus: int, memoria: int, workers: int = 1) -> dict:
    """Os valores recomendados para este hardware. Só aritmética — sem ler .env, sem efeito.

    `workers` é quantos processos do uvicorn vão subir. Tudo que é POR PROCESSO — o limiter de
    threads, o pool de conexões, os caches — é dividido por ele, senão N workers multiplicariam
    por N a pressão no banco e na memória. Com workers=1, que é o padrão, toda fórmula devolve
    exatamente o número de antes desta divisão existir."""
    ram_gb = memoria / GB
    workers = max(1, workers)

    def por_worker(piso: int, valor: float, teto: int) -> int:
        """clamp() dividindo TAMBÉM o piso pelos workers.

        Sem dividir o piso, subir de 1 para 2 workers não reduziria pela metade um valor que
        estivesse encostado no piso — o total do servidor dobraria justamente nas máquinas
        pequenas, que são as que menos aguentam. E, no sentido inverso, com workers=1 cada
        fórmula devolve exatamente o número de antes desta divisão existir, que é o que torna o
        rollout reversível."""
        return _clamp(max(1, piso // workers), valor / workers, teto)

    # Teto de requisições processadas ao mesmo tempo. TODA rota da API é `def` síncrono, então
    # roda no threadpool do AnyIO e este número É o limite de concorrência HTTP (o default do
    # AnyIO, 40, nunca foi ajustado). Como as threads passam a maior parte do tempo esperando o
    # banco, vale mais que o número de núcleos.
    # Por worker: o limiter é de processo, então o total do servidor continua cpus*2 em vez de
    # cpus*2*workers.
    threads = por_worker(16, cpus * 2, 64)

    # Conexões de banco. Metade das threads de requisição costuma bastar (nem toda rota fala com
    # o banco ao mesmo tempo), mais uma folga fixa para as threads de background — extração,
    # indexação, OCR — que pegam conexão fora do ciclo de requisição.
    pool_max = _clamp(10, threads // 2 + 10, 40)

    # Handles de .ufdr/.db abertos em cache. O custo de um miss não é abrir o arquivo, é
    # reconstruir o diretório central de um zip com centenas de milhares de entradas; com poucos
    # slots e vários analistas em inquéritos diferentes, a LRU passa a evictar a cada acesso.
    # Por worker: cada processo tem o seu cache, e o diretório central de um .ufdr com centenas
    # de milhares de entradas ocupa dezenas de MB no NameToInfo do Python.
    zips = por_worker(8, max(cpus, ram_gb / 8), 64)

    # Bytes de anexo já lidos/processados mantidos em memória (vale para ufdr_cache e iped_cache,
    # cada um com o seu). Só entram itens pequenos; vídeo continua sendo lido direto do zip.
    conteudo = por_worker(200 * MB, memoria * 0.01, 4 * GB)

    # Busca semântica: sem cache para dimensionar desde a migração 0048 — o ranking passou a ser
    # feito no Postgres (pgvector + HNSW) e a API não monta mais matriz de embeddings nenhuma.

    # Jobs pesados de CPU. Metade dos núcleos fica reservada para atender requisição, banco e SO;
    # a outra metade é o orçamento dos jobs, e o invariante que o .env já documentava
    # (JOBS_PESADOS_LIMITE * CLIP_NUM_THREADS <= núcleos) é respeitado por construção.
    orcamento_jobs = max(1, cpus // 2)
    jobs_pesados = _clamp(1, orcamento_jobs / 4, 8)
    clip_threads = _clamp(2, orcamento_jobs / jobs_pesados, 8)

    # Transcrição sobe processos separados (spawn), cada um carregando o próprio modelo do
    # faster-whisper — 0,5 a 1 GB de RSS por processo. O teto real aqui é memória, não CPU.
    transcricao = _clamp(1, min(cpus / 4, ram_gb / 4), 8)

    # Extrações rodando ao mesmo tempo no SISTEMA (o despachante em investigacao_utils respeita
    # este teto). Cada uma lê um .ufdr de dezenas de GB do disco e sobe TRANSCRICAO_THREADS
    # processos de whisper: o gargalo é disco e memória, não núcleo. Antes disto não havia teto
    # nenhum — uma thread por extração, quantas o analista disparasse.
    extracoes = _clamp(1, min(cpus / 8, ram_gb / 16), 4)

    # Processos do uvicorn. Toda rota é síncrona e roda no threadpool do AnyIO, então a
    # concorrência de I/O já existe com um worker só: o que N processos compram é alívio de GIL no
    # trabalho de CPU DENTRO da requisição (montar a exportação de um milhão de linhas, gerar
    # .docx, redimensionar prévia de imagem) e resiliência — um worker preso não derruba a API
    # inteira. O custo é RAM: cada worker carrega o seu CLIP, os seus caches e o seu pool. Por
    # isso o teto é 4, e não o número de núcleos.
    workers_recomendado = _clamp(1, cpus / 8, 4)

    # ----- Postgres -----
    # pool_max é POR WORKER (o pool é de processo), então o que o Postgres precisa aguentar é o
    # produto. Esquecer esta multiplicação é o erro mais fácil de cometer ao subir os workers, e o
    # mais barulhento: o pool simplesmente começa a dar PoolTimeout.
    max_conexoes = pool_max * workers + 50  # folga para psql, manutenção e picos de background

    # /dev/shm do container do banco (shm_size do compose). Os workers paralelos do Postgres
    # reservam memória compartilhada POSIX aí: com o padrão do Docker (64 MB) uma agregação
    # paralela enche o shm e a consulta morre com "could not resize shared memory segment".
    # 2% da RAM com piso de 256 MB cobre work_mem × workers paralelos com folga; o teto de 4 GB
    # existe porque ninguém precisa de mais que isso — o shm do container não é RAM alocada, é
    # teto de uso, mas contar com 4 GB de RAM livre já é generoso em qualquer perfil.
    shm = _clamp(256 * MB, memoria * 0.02, 4 * GB)

    # 20% e não os 25% de manual: a API (torch, CLIP, caches) e o Ollama dividem esta máquina.
    shared_buffers = _clamp(128 * MB, memoria * 0.20, 64 * GB)
    # Não reserva memória — só informa ao planner quanto o SO provavelmente tem em cache.
    effective_cache = _clamp(1 * GB, memoria * 0.60, 192 * GB)
    # Por operação de sort/hash, e uma query pode ter várias: por isso divide pelas conexões.
    # É o parâmetro que mais muda de patamar as agregações grandes, que a 4 MB derramam em disco.
    work_mem = _clamp(4 * MB, memoria * 0.15 / max_conexoes, 256 * MB)
    manutencao = _clamp(64 * MB, memoria * 0.02, 4 * GB)
    # Paralelismo: sem isto o Postgres usa no máximo 2 workers por consulta, em qualquer máquina.
    paralelos_por_consulta = _clamp(2, cpus / 8, 8)
    # A ingestão de um .ufdr escreve muito de uma vez; com o default de 1 GB o Postgres entra em
    # checkpoint o tempo todo justamente durante a importação.
    max_wal = _clamp(1 * GB, memoria * 0.02, 16 * GB)

    # Limite de upload de documento genérico (aba "Arquivo genérico" e modal Importar).
    # Fixo em 50 MB por padrão — não é dimensionado pelo hardware, só entra aqui para ser
    # ajustável pela tela Desempenho (UPLOAD_DOCUMENTO_MB no .env). Vale na hora, sem
    # reiniciar: documentos_utils lê via valor_int() a cada upload.
    upload_documento_mb = 50

    return {
        "API_THREADS_REQUISICAO": threads,
        "UPLOAD_DOCUMENTO_MB": upload_documento_mb,
        "DB_POOL_MAX": pool_max,
        "CACHE_UFDR_ZIPS": zips,
        "CACHE_CONTEUDO_MB": conteudo // MB,
        "JOBS_PESADOS_LIMITE": jobs_pesados,
        "CLIP_NUM_THREADS": clip_threads,
        "TRANSCRICAO_THREADS": transcricao,
        # Não derivadas do hardware — qualidades de transcrição com default de fábrica (o
        # `_calcular` tem de cobrir TODAS as chaves de PARAMETROS: resumo() indexa direto e a
        # tela Administração → Desempenho quebrou com KeyError quando VAD/BEAM entraram na
        # lista sem entrar aqui). VAD ligado é o recomendado (30-70% de silêncio em gravação
        # policial); feixe 1 = busca gulosa, 2-3x mais rápida em CPU.
        "TRANSCRICAO_VAD": "sim",
        "TRANSCRICAO_BEAM": 1,
        "EXTRACOES_SIMULTANEAS": extracoes,
        "API_WORKERS": workers_recomendado,
        "PG_SHM_SIZE": f"{shm // MB}MB",
        "PG_MAX_CONNECTIONS": max_conexoes,
        "PG_SHARED_BUFFERS": f"{shared_buffers // MB}MB",
        "PG_EFFECTIVE_CACHE_SIZE": f"{effective_cache // MB}MB",
        "PG_WORK_MEM": f"{work_mem // MB}MB",
        "PG_MAINTENANCE_WORK_MEM": f"{manutencao // MB}MB",
        "PG_MAX_WAL_SIZE": f"{max_wal // MB}MB",
        "PG_MAX_WORKER_PROCESSES": max(8, cpus),
        "PG_MAX_PARALLEL_WORKERS": cpus,
        "PG_MAX_PARALLEL_WORKERS_PER_GATHER": paralelos_por_consulta,
        "PG_RANDOM_PAGE_COST": "1.1",
    }


# Metadados de cada parâmetro: rótulo, grupo e ajuda ficam aqui, e não no JavaScript, para a
# tela ser um laço genérico sobre o que a API mandar — acrescentar um parâmetro novo é mexer
# só neste arquivo. `tipo` é o que o POST usa para validar antes de gravar no .env.
#
# tipo "inteiro"  -> número puro
# tipo "tamanho"  -> tamanho aceito pelo Postgres (128MB, 4GB…)
# tipo "decimal"  -> número com casas (random_page_cost)
PARAMETROS = [
    ("API_THREADS_REQUISICAO", "Requisições simultâneas", "API", "inteiro",
     "Teto de requisições processadas ao mesmo tempo. Como toda rota é síncrona, este é o limite "
     "real de concorrência da API (o padrão do AnyIO, 40, servia a qualquer máquina)."),
    ("DB_POOL_MAX", "Conexões com o banco", "API", "inteiro",
     "Tamanho do pool. Precisa caber no max_connections do Postgres, contando também as threads "
     "de extração, OCR e indexação, que pegam conexão fora do ciclo de requisição."),
    ("API_WORKERS", "Processos da API", "API", "inteiro",
     "Quantos processos do uvicorn sobem. Mais de um só ajuda em trabalho de CPU dentro da "
     "requisição (exportação, .docx, prévia de imagem) e em resiliência; o custo é RAM, porque "
     "cada processo carrega os próprios caches e o próprio pool. Threads, pool e caches abaixo "
     "já são divididos por este número — e o max_connections do Postgres passa a contar "
     "pool × processos."),
    ("EXTRACOES_SIMULTANEAS", "Extrações ao mesmo tempo", "API", "inteiro",
     "Quantas extrações de .ufdr rodam juntas no servidor. As demais ficam na fila, e a fila "
     "sobrevive a um restart. Cada extração lê dezenas de GB do disco e sobe os processos de "
     "transcrição, então o limite aqui é disco e memória."),
    ("UPLOAD_DOCUMENTO_MB", "Limite de upload por arquivo (MB)", "Upload", "inteiro",
     "Teto de cada arquivo enviado pelo navegador na aba Arquivo genérico e no modal Importar "
     "(PDF, CSV, XLS, XLSX, DOCX, TXT, HTML) — o .zip é a exceção: sem limite, como o .ufdr (vai "
     "por POST /api/documentos/upload-zip). Vale na hora, sem reiniciar. O nginx aceita até "
     "520 MB por requisição — acima do teto máximo aqui (500 MB) de propósito, para quem "
     "recusar ser a API, com mensagem em JSON que a tela sabe ler."),
    ("CACHE_UFDR_ZIPS", "Arquivos .ufdr em cache", "Caches", "inteiro",
     "Quantos .ufdr (e bancos do IPED) ficam com handle aberto. Um miss reconstrói o índice de um "
     "zip com centenas de milhares de entradas, então este número acompanha quantos analistas "
     "trabalham em extrações diferentes ao mesmo tempo."),
    ("CACHE_CONTEUDO_MB", "Anexos em cache (MB)", "Caches", "inteiro",
     "Memória para anexos já lidos/processados. Vídeo e arquivo grande não entram — continuam "
     "sendo lidos direto do zip."),
    ("JOBS_PESADOS_LIMITE", "Jobs pesados simultâneos", "Jobs pesados", "inteiro",
     "OCR + classificação de imagem + comparação facial somados. Multiplicado pelas threads do "
     "CLIP não deve passar dos núcleos reservados a jobs."),
    ("CLIP_NUM_THREADS", "Threads por job de imagem", "Jobs pesados", "inteiro",
     "Threads de PyTorch/OpenMP usadas por UM job de classificação ou comparação facial."),
     ("TRANSCRICAO_THREADS", "Processos de transcrição", "Jobs pesados", "inteiro",
      "Processos do faster-whisper em paralelo. Cada um carrega o próprio modelo (0,5 a 1 GB), "
      "então quem limita aqui costuma ser a memória, não a CPU."),
     ("TRANSCRICAO_VAD", "Pular silêncio na transcrição", "Jobs pesados", "booleano",
      "Filtro de atividade de voz: o modelo só vê os trechos com fala. Em gravação policial "
      "(30-70% de silêncio) é o maior barateador da transcrição em CPU. Desligar deixa o áudio "
      "inteiro passar pelo modelo — mais lento, mesmo texto."),
     ("TRANSCRICAO_BEAM", "Feixe de decodificação da transcrição", "Jobs pesados", "inteiro",
      "1 = busca gulosa (padrão): ~2-3x mais rápida em CPU, perda de qualidade mínima com o "
      "modelo small em português. 5 = feixe largo clássico do whisper."),
    ("PG_SHARED_BUFFERS", "shared_buffers", "Postgres", "tamanho",
     "Memória que o Postgres reserva para páginas de dados. 20% da RAM, e não os 25% de manual, "
     "porque a API e o Ollama dividem a mesma máquina."),
    ("PG_EFFECTIVE_CACHE_SIZE", "effective_cache_size", "Postgres", "tamanho",
     "Não reserva memória: informa ao planner quanto o sistema operacional provavelmente tem em "
     "cache, o que muda a escolha entre índice e varredura."),
    ("PG_WORK_MEM", "work_mem", "Postgres", "tamanho",
     "Memória por operação de ordenação/hash. É o parâmetro que mais pesa nas agregações grandes "
     "(timeline, listagens filtradas), que no padrão de 4 MB derramam em disco."),
    ("PG_MAINTENANCE_WORK_MEM", "maintenance_work_mem", "Postgres", "tamanho",
     "Usado por CREATE INDEX e VACUUM. Vale na criação dos índices e na manutenção, não nas "
     "consultas do dia a dia."),
    ("PG_MAX_CONNECTIONS", "max_connections", "Postgres", "inteiro",
      "Precisa ficar acima do pool da API com folga para psql e manutenção."),
    ("PG_SHM_SIZE", "Memória compartilhada do banco (/dev/shm)", "Postgres", "tamanho",
      "Tamanho do /dev/shm do container do Postgres (shm_size do compose). Os workers paralelos "
      "reservam memória compartilhada aí; com o padrão do Docker (64 MB) consultas pesadas morrem "
      "com \"could not resize shared memory segment — No space left on device\". Requer recriar o "
      "container do banco (docker compose up -d), o que reinicia o Postgres por alguns segundos."),
    ("PG_MAX_WAL_SIZE", "max_wal_size", "Postgres", "tamanho",
     "A ingestão de um .ufdr escreve muito de uma vez; com o padrão de 1 GB o banco entra em "
     "checkpoint o tempo todo justamente durante a importação."),
    ("PG_MAX_WORKER_PROCESSES", "max_worker_processes", "Postgres", "inteiro",
     "Teto global de processos auxiliares. Sem subir este, pedir mais workers paralelos não tem "
     "efeito nenhum."),
    ("PG_MAX_PARALLEL_WORKERS", "max_parallel_workers", "Postgres", "inteiro",
     "Quantos workers paralelos o banco pode usar somando todas as consultas."),
    ("PG_MAX_PARALLEL_WORKERS_PER_GATHER", "max_parallel_workers_per_gather", "Postgres", "inteiro",
     "Workers paralelos por consulta. O padrão é 2 em qualquer máquina, o que subaproveita um "
     "servidor de muitos núcleos nas consultas mais pesadas."),
    ("PG_RANDOM_PAGE_COST", "random_page_cost", "Postgres", "decimal",
     "Custo relativo de leitura aleatória. O padrão 4 descreve disco rotacional e faz o planner "
     "evitar índices que, em SSD, seriam a melhor escolha."),
]

TIPOS = {chave: tipo for chave, _r, _g, tipo, _a in PARAMETROS}

# Quando cada chave passa a valer, para a tela avisar o que ainda falta reiniciar.
QUANDO = {
    "API_WORKERS": QUANDO_RECRIAR_CONTAINER,
    "UPLOAD_DOCUMENTO_MB": QUANDO_JA,
    "EXTRACOES_SIMULTANEAS": QUANDO_JA,
    "API_THREADS_REQUISICAO": QUANDO_REINICIAR_API,
    "DB_POOL_MAX": QUANDO_REINICIAR_API,
    "CACHE_UFDR_ZIPS": QUANDO_REINICIAR_API,
    "CACHE_CONTEUDO_MB": QUANDO_REINICIAR_API,
    "JOBS_PESADOS_LIMITE": QUANDO_JA,
    "CLIP_NUM_THREADS": QUANDO_JA,
    "TRANSCRICAO_THREADS": QUANDO_JA,
    "TRANSCRICAO_VAD": QUANDO_JA,
    "TRANSCRICAO_BEAM": QUANDO_JA,
    "PG_MAX_CONNECTIONS": QUANDO_REINICIAR_BANCO,
    "PG_SHM_SIZE": QUANDO_REINICIAR_BANCO,
    "PG_SHARED_BUFFERS": QUANDO_REINICIAR_BANCO,
    "PG_EFFECTIVE_CACHE_SIZE": QUANDO_REINICIAR_BANCO,
    "PG_WORK_MEM": QUANDO_REINICIAR_BANCO,
    "PG_MAINTENANCE_WORK_MEM": QUANDO_REINICIAR_BANCO,
    "PG_MAX_WAL_SIZE": QUANDO_REINICIAR_BANCO,
    "PG_MAX_WORKER_PROCESSES": QUANDO_REINICIAR_BANCO,
    "PG_MAX_PARALLEL_WORKERS": QUANDO_REINICIAR_BANCO,
    "PG_MAX_PARALLEL_WORKERS_PER_GATHER": QUANDO_REINICIAR_BANCO,
    "PG_RANDOM_PAGE_COST": QUANDO_REINICIAR_BANCO,
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def workers_efetivos(env: dict | None = None) -> int:
    """Quantos processos do uvicorn estão REALMENTE rodando.

    Precisa ser o número real, e não o recomendado, porque é por ele que as outras fórmulas
    dividem: recomendar 2 workers num servidor que subiu com 1 cortaria pela metade o pool, as
    threads e os caches de um processo que está sozinho.

    A fonte de verdade é a variável de ambiente, que é exatamente o que o `docker compose up`
    passou ao container (docker-compose.yml interpola `${API_WORKERS:-1}` do .env). O arquivo
    .env é só o fallback para quem roda a API fora do compose; 1 é o padrão de sempre.
    """
    for bruto in (os.getenv("API_WORKERS"), ((env or {}).get("API_WORKERS"))):
        bruto = (bruto or "").strip()
        if bruto:
            try:
                return max(1, int(bruto))
            except ValueError:
                logger.warning("API_WORKERS inválido (%r) — assumindo 1", bruto)
                return 1
    return 1


def recomendados(env: dict | None = None) -> dict:
    """Valores que as fórmulas produzem para este hardware (ou para o perfil escolhido)."""
    env = _read_env() if env is None else env
    base = _base_dimensionamento(env)
    return _calcular(base["cpus"], base["memoria_bytes"], workers_efetivos(env))


def efetivos(env: dict | None = None) -> dict:
    """O que de fato vale: o recomendado, com cada chave presente no .env sobrescrevendo a sua.

    Vazio no .env conta como ausente — é a mesma regra das outras telas de configuração, e evita
    que uma linha `DB_POOL_MAX=` deixada para trás zere o pool."""
    env = _read_env() if env is None else env
    valores = recomendados(env)
    for chave in valores:
        bruto = (env.get(chave) or "").strip()
        if bruto:
            valores[chave] = bruto
    return valores


def _read_env() -> dict:
    return parse_env(read_env_text())


def valor_int(chave: str, padrao: int) -> int:
    """Um valor inteiro do dimensionamento, com fallback se a chave for desconhecida ou inválida.

    `padrao` é o valor que a constante tinha antes deste módulo existir: se qualquer coisa der
    errado (env ilegível, número escrito à mão com lixo), a instalação volta ao comportamento
    antigo em vez de subir com um limite absurdo."""
    try:
        return max(1, int(str(efetivos()[chave]).strip()))
    except (KeyError, TypeError, ValueError):
        return padrao


def registrar_no_log() -> None:
    """Deixa no log de startup o hardware detectado e os valores escolhidos.

    É por aqui que se responde "por que este servidor está com pool 40 e o meu com 18" sem ter
    que abrir a tela — e é o que prova, num container com limite, que o cgroup foi respeitado."""
    env = _read_env()
    base = _base_dimensionamento(env)
    valores = efetivos(env)
    manuais = sorted(c for c in valores if (env.get(c) or "").strip())
    logger.info(
        "Dimensionamento: perfil=%s cpus=%s (%s) memoria=%.1fGB (%s) | threads=%s pool=%s "
        "zips=%s | fixados no .env: %s",
        base.get("perfil", "auto"), base["cpus"], base["origem_cpu"],
        base["memoria_bytes"] / GB, base["origem_memoria"],
        valores["API_THREADS_REQUISICAO"], valores["DB_POOL_MAX"],
        valores["CACHE_UFDR_ZIPS"],
        ", ".join(manuais) or "nenhum",
    )


# Nome do parâmetro correspondente no Postgres, para ler o que ESTÁ valendo lá com
# current_setting() em vez de supor pelo .env — o banco pode ter subido antes da última edição.
_PG_SETTING = {
    "PG_SHARED_BUFFERS": "shared_buffers",
    "PG_EFFECTIVE_CACHE_SIZE": "effective_cache_size",
    "PG_WORK_MEM": "work_mem",
    "PG_MAINTENANCE_WORK_MEM": "maintenance_work_mem",
    "PG_MAX_CONNECTIONS": "max_connections",
    "PG_MAX_WAL_SIZE": "max_wal_size",
    "PG_MAX_WORKER_PROCESSES": "max_worker_processes",
    "PG_MAX_PARALLEL_WORKERS": "max_parallel_workers",
    "PG_MAX_PARALLEL_WORKERS_PER_GATHER": "max_parallel_workers_per_gather",
    "PG_RANDOM_PAGE_COST": "random_page_cost",
}

_threads_aplicadas: int | None = None


def registrar_threads_aplicadas(total: int) -> None:
    """Chamado pelo startup da API (main.py) com o valor que o limiter do AnyIO realmente ficou."""
    global _threads_aplicadas
    _threads_aplicadas = total


def _vigentes_postgres() -> dict:
    """O que o Postgres está usando agora, lido dele mesmo.

    Nunca levanta: banco fora do ar não pode derrubar a tela de configuração — justamente a tela
    que alguém abriria para entender por que o banco não sobe."""
    try:
        from funcoes.db_utils import get_connection, tamanho_pool_vigente

        # Se o pool ainda não subiu, não é esta tela que vai abri-lo: com o banco fora do ar a
        # abertura só falha depois do timeout do psycopg_pool (30 s), e quem está aqui provavelmente
        # veio justamente investigar por que o banco não sobe.
        if tamanho_pool_vigente() is None:
            return {}

        nomes = list(_PG_SETTING.values())
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, setting, unit FROM pg_settings WHERE name = ANY(%s)", (nomes,)
                )
                bruto = {nome: (valor, unidade) for nome, valor, unidade in cur.fetchall()}
    except Exception:
        logger.warning("Não foi possível ler os parâmetros do Postgres", exc_info=True)
        return {}

    vigentes = {}
    for chave, nome in _PG_SETTING.items():
        if nome not in bruto:
            continue
        valor, unidade = bruto[nome]
        # pg_settings devolve tamanho em múltiplos de `unit` ("8kB", "kB", "MB"); converte para MB
        # para bater com o formato que esta tela grava e mostra.
        if unidade in ("8kB", "kB", "MB", "GB"):
            fator = {"8kB": 8 * 1024, "kB": 1024, "MB": MB, "GB": GB}[unidade]
            vigentes[chave] = f"{int(valor) * fator // MB}MB"
        else:
            vigentes[chave] = str(valor)
    return vigentes


def vigentes() -> dict:
    """O que cada parâmetro vale AGORA, lido de onde ele de fato mora.

    É o que separa esta tela de um editor de .env: pool, threads e caches foram lidos no startup
    do processo, e os PG_* quando o container do banco subiu. Editar o .env não muda nenhum dos
    dois — e é exatamente essa diferença que a coluna "em vigor" existe para mostrar."""
    atual: dict = {}

    if _threads_aplicadas:
        atual["API_THREADS_REQUISICAO"] = str(_threads_aplicadas)

    try:
        from funcoes.db_utils import tamanho_pool_vigente

        tamanho = tamanho_pool_vigente()
        if tamanho:
            atual["DB_POOL_MAX"] = str(tamanho)
    except Exception:
        pass

    # Constantes calculadas no import de cada módulo: são literalmente o que o processo usa.
    try:
        from funcoes import ufdr_cache

        atual["CACHE_UFDR_ZIPS"] = str(ufdr_cache.ZIPS_MAX)
        atual["CACHE_CONTEUDO_MB"] = str(ufdr_cache.CONTEUDO_TOTAL_TAMANHO_MAX // MB)
    except Exception:
        pass

    atual.update(_vigentes_postgres())
    return atual


def _estatisticas_pool() -> dict:
    """Números do pool de conexões, ou {} se ele ainda não subiu ou o módulo falhar.

    Import tardio e best-effort pelo mesmo motivo de vigentes(): esta tela não pode quebrar
    porque o banco está fora do ar — é justamente quando alguém vai abri-la."""
    try:
        from funcoes.db_utils import estatisticas_pool

        return estatisticas_pool()
    except Exception:
        return {}


def resumo(perfil_simulado: str | None = None) -> dict:
    """Tudo que a tela Administração → Desempenho precisa, num round-trip só.

    `perfil_simulado` responde "como ficaria se o perfil fosse este", sem tocar no .env — é o que
    permite comparar o dimensionamento de um servidor menor antes de salvar."""
    env = _read_env()
    if perfil_simulado:
        env = {**env, "DESEMPENHO_PERFIL": perfil_simulado}
    base = _base_dimensionamento(env)
    detectado = detectar_hardware()
    recomendado = recomendados(env)
    # O que vai valer no próximo start (recomendado + o que estiver fixado no .env)...
    proximo = efetivos(env)
    # ...e o que vale agora, lido do processo e do banco. Onde não der para saber, cai no
    # próximo: melhor mostrar a estimativa do que uma lacuna sem explicação.
    agora = vigentes()
    return {
        "perfil": base.get("perfil", "auto"),
        "hardware": {
            "cpus": detectado["cpus"],
            "memoria_bytes": detectado["memoria_bytes"],
            "origem_cpu": detectado["origem_cpu"],
            "origem_memoria": detectado["origem_memoria"],
        },
        # Estado do pool AGORA, não o dimensionamento dele. É o par de números que diz se o
        # tamanho configurado está dando conta: `requests_waiting` acima de zero de forma
        # sustentada significa requisição na fila esperando alguém devolver uma conexão — o
        # sintoma de job longo segurando conexão, que nenhum outro número desta tela mostra.
        "pool": _estatisticas_pool(),
        "base": {"cpus": base["cpus"], "memoria_bytes": base["memoria_bytes"]},
        "parametros": [
            {
                "chave": chave,
                "rotulo": rotulo,
                "grupo": grupo,
                "tipo": tipo,
                "ajuda": ajuda,
                # .get, não índice direto: uma chave listada em PARAMETROS mas esquecida no
                # _calcular derrubava a tela inteira com KeyError (500). Melhor mostrar vazio
                # (a tela trata como "automático") do que quebrar o único lugar onde se ajusta
                # o desempenho — que é justamente quando o servidor está mal.
                "recomendado": str(recomendado.get(chave, (env.get(chave) or "").strip())),
                "configurado": (env.get(chave) or "").strip(),
                "proximo": str(proximo.get(chave, (env.get(chave) or "").strip())),
                "efetivo": str(agora.get(chave, proximo.get(chave, (env.get(chave) or "").strip()))),
                "quando": QUANDO.get(chave, QUANDO_REINICIAR_API),
            }
            for chave, rotulo, grupo, tipo, ajuda in PARAMETROS
        ],
    }
