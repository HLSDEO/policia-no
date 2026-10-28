"""Organização dos arquivos de investigação em data/, por inquérito.

O investigador copia o arquivo (ou a pasta) solto para `data/<DELEGACIA>/` e importa. Terminada
a importação, o arquivo é MOVIDO para a pasta do inquérito, separado por categoria:

    data/<DELEGACIA>/<NÚMERO DO INQUÉRITO OU CASO>/CELULAR/    ·  extrações .ufdr (Cellebrite)
                                                  /NUVEM/      ·  pastas-caso do IPED (Takeout etc.)
                                                  /RIF/        ·  pastas de RIF (SISCOAF/COAF)
                                                  /BANCARIO/   ·  R304 do SIMBA
                                                  /OUTROS/     ·  documentos diversos (PDF/CSV/XLS/XLSX/DOCX/TXT)

A delegacia NÃO vem do banco: é o primeiro segmento do caminho atual do arquivo (a pasta de onde
ele foi importado). Assim a organização usa exatamente a pasta que já existe em disco, sem
depender de o nome cadastrado da unidade bater com o nome da pasta ("FICCO/ES" x "FICCO-ES").

Depois de mover, quem chamou grava o caminho relativo devolvido na coluna do banco que aponta
para o arquivo (`inq_tb02_extracoes.no_arquivo`, `ipd_tb01_extracoes.no_pasta`,
`rif_tb01_rifs.no_pasta`, `smb_tb01_extratos.no_arquivo`, `inq_tb04_documentos.no_arquivo`) —
todos são resolvidos como `PASTA_DADOS / valor`, então o caminho relativo funciona no lugar do
nome puro. Ver `api/tools/backfill_organizar_arquivos.py` para o que foi importado antes disto e
`docs/organizacao_arquivos.md`.

Mover é best-effort: qualquer falha é registrada em log e o arquivo fica onde está — uma
importação concluída nunca é derrubada por causa da arrumação da pasta.
"""

import contextlib
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Pasta com os arquivos de investigação, montada a partir de ./data no host (ver docker-compose.yml).
# Definida aqui, e não em investigacao_utils, para este módulo não depender de ninguém (todos os
# módulos de importação dependem dele); investigacao_utils reexporta PASTA_DADOS daqui.
PASTA_DADOS = Path(os.environ.get("PASTA_INVESTIGACAO", "/app/data"))

CATEGORIA_CELULAR = "CELULAR"
CATEGORIA_NUVEM = "NUVEM"
CATEGORIA_RIF = "RIF"
CATEGORIA_BANCARIO = "BANCARIO"
CATEGORIA_OUTROS = "OUTROS"

# As cinco pastas de categoria. Também servem de marca de "já organizado": um caminho que passa
# por uma delas está na estrutura nova (ver ja_organizado).
CATEGORIAS_EXTRACAO = (
    CATEGORIA_CELULAR, CATEGORIA_NUVEM, CATEGORIA_RIF, CATEGORIA_BANCARIO, CATEGORIA_OUTROS,
)

# Pastas em que a busca recursiva não entra: saídas do próprio sistema (relatórios gerados) e
# internals de caso do IPED (index/storage/lib têm dezenas de milhares de arquivos e nunca contêm
# um arquivo importável solto).
PASTAS_PULADAS = frozenset({
    "index", "storage", "lib", "__pycache__", "conf", "profiles",
    "relatorios", "relatorios_ia", "analises_prompts", "_apple_tmp", ".entrega", ".no_cache",
    "_lixeira",
})

# Cópia VOLÁTIL de documento que mora num nó (docs/no_descentralizado.md): a prova fica na
# estação; quando um fluxo antigo exige o arquivo em disco (aba Original, reextração, blocos
# IPL/IRPF/IPEI/telefonia) `resolver()` materializa aqui, sob demanda, por GET /no/arquivo.
# Nunca é listada, nunca é organizada, e pode ser apagada a qualquer momento — refaz sozinha.
PASTA_CACHE_NO = ".no_cache"

# Lixeira de prova avulsa: "Remover documento" não apaga o arquivo em seco — ele vem PARA
# AQUI, com carimbo de tempo no nome, e só é expurgado depois do prazo (funcoes/higiene_data.py).
# Ninguém destrói prova por um clique distraído.
PASTA_LIXEIRA = "_lixeira"

# Profundidade máxima da busca recursiva: data/<delegacia>/<inquérito>/<categoria>/<arquivo> são
# 4 níveis de pasta acima do arquivo.
_PROFUNDIDADE_BUSCA = 4

# Tentativas de mover antes de desistir. O .ufdr acabou de ser lido e o caso do IPED acabou de ter
# o índice Lucene mapeado em memória — no Docker Desktop (Windows, bind mount de data/) o SO
# demora alguns segundos para soltar os handles depois que o processo/arquivo já foi fechado
# (mesmo achado de _renomear_pasta_para_conta, em iped_utils.py).
_TENTATIVAS_MOVER = 8
_ESPERA_ENTRE_TENTATIVAS = 1.0


def sanitizar_inquerito(numero: str | None) -> str | None:
    """Converte o número do inquérito (ou o nome do caso) em nome de pasta: sem separador de
    caminho e sem caractere proibido no Windows. "064/2025" vira "064-2025"."""
    if not numero:
        return None
    nome = str(numero).strip()
    nome = re.sub(r"[<>:\"/\\|?*]", "-", nome)
    nome = re.sub(r"\s+", " ", nome).strip().strip(".")
    return nome[:120] if nome else None


def _relativo(caminho: Path) -> Path | None:
    """Caminho relativo a PASTA_DADOS, ou None se estiver fora da pasta de dados."""
    try:
        return caminho.resolve().relative_to(PASTA_DADOS.resolve())
    except (ValueError, OSError):
        return None


def ja_organizado(caminho: Path) -> bool:
    """True se o caminho já vive na estrutura nova (algum segmento é pasta de categoria)."""
    relativo = _relativo(caminho)
    if relativo is None:
        return False
    return any(parte in CATEGORIAS_EXTRACAO for parte in relativo.parts[:-1])


def delegacia_de(caminho: Path) -> str | None:
    """Pasta da delegacia: primeiro segmento do caminho relativo a PASTA_DADOS. None quando o
    arquivo está direto na raiz de data/ (instalação sem multidelegacia)."""
    relativo = _relativo(caminho)
    if relativo is None or len(relativo.parts) < 2:
        return None
    return relativo.parts[0]


def subpasta_categoria(delegacia: str | None, numero_inquerito: str, categoria: str) -> Path:
    """Pasta de destino: data/[<delegacia>/]<inquérito>/<categoria>/ (ainda não criada)."""
    base = PASTA_DADOS / delegacia if delegacia else PASTA_DADOS
    return base / numero_inquerito / categoria


def destino(caminho: Path, delegacia: str | None, numero_inquerito: str,
            categoria: str, novo_nome: str | None = None) -> Path:
    """Caminho final do arquivo/pasta na estrutura nova, já com colisão de nome resolvida por
    sufixo numérico — não move nada."""
    pasta = subpasta_categoria(delegacia, numero_inquerito, categoria)
    nome = novo_nome or caminho.name
    alvo = pasta / nome
    # pasta mantém o nome inteiro ("RIF_128302 (2)"); arquivo põe o sufixo antes da extensão
    raiz, extensao = (nome, "") if caminho.is_dir() else (Path(nome).stem, Path(nome).suffix)
    sufixo = 2
    while alvo.exists():
        alvo = pasta / f"{raiz} ({sufixo}){extensao}"
        sufixo += 1
    return alvo


def organizar(caminho: Path, numero_inquerito: str | None, categoria: str,
              novo_nome: str | None = None) -> str | None:
    """Move o arquivo/pasta importado para a pasta do inquérito e devolve o novo caminho
    RELATIVO a data/ (posix), para quem chamou gravar no banco.

    Devolve None quando não há nada a fazer (já organizado, sem número de inquérito, fora da
    pasta de dados) ou quando o movimento falhou — nesses casos o arquivo continua onde estava e
    o valor gravado no banco não deve mudar."""
    numero = sanitizar_inquerito(numero_inquerito)
    if not numero or categoria not in CATEGORIAS_EXTRACAO:
        return None
    if not caminho.exists() or _relativo(caminho) is None or ja_organizado(caminho):
        return None

    alvo = destino(caminho, delegacia_de(caminho), numero, categoria, novo_nome)
    if alvo == caminho:
        return None
    try:
        alvo.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Não foi possível criar a pasta de destino %s", alvo.parent)
        return None

    for tentativa in range(_TENTATIVAS_MOVER):
        try:
            shutil.move(str(caminho), str(alvo))
            break
        except OSError:
            if tentativa == _TENTATIVAS_MOVER - 1:
                logger.warning("Não foi possível mover %s para %s — o arquivo fica onde está",
                               caminho, alvo, exc_info=True)
                return None
            time.sleep(_ESPERA_ENTRE_TENTATIVAS)

    # o sidecar de hashes do upload (<arquivo>.hashes.json, funcoes/hash_utils.py) viaja junto —
    # largado para trás, a importação perderia o pulo da passada de hash e rehashearia o arquivo
    sidecar = caminho.with_name(caminho.name + ".hashes.json")
    if sidecar.exists():
        with contextlib.suppress(OSError):
            shutil.move(str(sidecar), str(alvo.with_name(alvo.name + ".hashes.json")))

    relativo = alvo.relative_to(PASTA_DADOS).as_posix()
    invalidar_indice_arquivos()  # o índice cacheado não sabe da mudança; a próxima varre fresco
    logger.info("Arquivo organizado: %s -> %s", caminho.name, relativo)
    return relativo


# ---------------------------------------------------------------------------
# Localização de arquivos/pastas em data/
#
# O que foi importado ANTES desta estrutura guarda no banco só o nome do arquivo, sem pasta —
# e o investigador também pode ter movido algo à mão. As buscas abaixo são o plano B de quem
# resolve `PASTA_DADOS / <valor do banco>` e não encontra nada.
# ---------------------------------------------------------------------------

def _varrer(nome: str, procurar_pasta: bool, profundidade: int) -> Path | None:
    """Varre data/ NÍVEL A NÍVEL (não em profundidade): o que está solto na raiz ou na pasta da
    delegacia — o formato antigo, que é a razão de esta busca existir — sai no primeiro ou no
    segundo nível, sem descer numa pasta-caso do IPED inteira antes de olhar a pasta ao lado."""
    nivel = [PASTA_DADOS]
    for _ in range(profundidade + 1):
        proximo = []
        for pasta in nivel:
            try:
                entradas = list(os.scandir(pasta))
            except OSError:
                continue
            for entrada in entradas:
                try:
                    eh_pasta = entrada.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if entrada.name == nome and eh_pasta == procurar_pasta:
                    return Path(entrada.path)
                if eh_pasta and entrada.name not in PASTAS_PULADAS:
                    proximo.append(Path(entrada.path))
        if not proximo:
            return None
        nivel = proximo
    return None


def buscar_arquivo(nome: str, profundidade: int = _PROFUNDIDADE_BUSCA) -> Path | None:
    """Procura um ARQUIVO pelo nome em data/ e abaixo (inclusive na estrutura nova)."""
    if not nome or "/" in nome or "\\" in nome:
        return None
    return _varrer(nome, False, profundidade)


def buscar_pasta(nome: str, profundidade: int = _PROFUNDIDADE_BUSCA) -> Path | None:
    """Procura uma PASTA pelo nome em data/ e abaixo (inclusive na estrutura nova)."""
    if not nome or "/" in nome or "\\" in nome:
        return None
    return _varrer(nome, True, profundidade)


def indice_arquivos(profundidade: int = _PROFUNDIDADE_BUSCA) -> dict[str, Path]:
    """Índice {nome do arquivo: Path} de data/ inteiro (ou {relativo: Path} com chave "" — ver
    indice_entrada). Cache de 30 s: ver indice_entrada."""
    return indice_entrada(profundidade)[0]


# Cache do índice: o poll da tela de investigações (poll a cada 2-5 s por aba aberta) chama
# _situacoes_arquivos_ufdr a CADA batida, e cada batida pagava a varredura inteira + a reconstrução
# do mapa de relativos (~800 ms com 24 mil arquivos, sob GIL — 12 abas em paralelo viravam 11 s).
# O índice só interessa como resposta de "onde este arquivo PARA"; 30 s de atraso para um .ufdr
# recém-movido é invisível (o caminho direto do banco resolve na hora, e o plano B é raro).
# Os dois dicionários são COMPARTILHADOS: quem recebe não modifica.
_INDICE_TTL_S = 30.0
_indice_cache: tuple[float, dict, dict] | None = None
_indice_lock = threading.Lock()


def indice_entrada(profundidade: int = _PROFUNDIDADE_BUSCA) -> tuple[dict, dict]:
    """( {nome: Path}, {caminho relativo a data/: Path} ) de data/ inteiro, varridos num único
    passeio nível a nível e cacheados por _INDICE_TTL_S. Mesma ordem e mesmos cortes de _varrer,
    então o achado é o mesmo que buscar_arquivo devolveria.

    O mapa de relativos sai do passeio porque o consumidor (badge de .ufdr na listagem) procura por
    CAMINHO, não por nome — reconstruí-lo a cada batida com Path.relative_to custava mais que a
    varredura em si (~730 ms para 24 mil arquivos). Aqui o relativo sai de um corte de string."""
    global _indice_cache
    agora = time.monotonic()
    cache = _indice_cache
    if cache is not None and agora - cache[0] < _INDICE_TTL_S:
        return cache[1], cache[2]
    with _indice_lock:
        cache = _indice_cache  # outra thread pode ter varrido enquanto esperávamos o lock
        if cache is not None and time.monotonic() - cache[0] < _INDICE_TTL_S:
            return cache[1], cache[2]
        por_nome, por_relativo = _varrer_indice(profundidade)
        _indice_cache = (time.monotonic(), por_nome, por_relativo)
        return por_nome, por_relativo


def invalidar_indice_arquivos() -> None:
    """Esquece o índice cacheado (após operações que MOVEM/apagam arquivos em data/, para a
    próxima consulta varrer fresco — o TTL cuida do resto)."""
    global _indice_cache
    _indice_cache = None


def _varrer_indice(profundidade: int) -> tuple[dict, dict]:
    indice: dict[str, Path] = {}
    relativos: dict[str, Path] = {}
    prefixo = str(PASTA_DADOS) + os.sep
    nivel = [PASTA_DADOS]
    for _ in range(profundidade + 1):
        proximo = []
        for pasta in nivel:
            try:
                entradas = list(os.scandir(pasta))
            except OSError:
                continue
            for entrada in entradas:
                try:
                    eh_pasta = entrada.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if eh_pasta:
                    if entrada.name not in PASTAS_PULADAS:
                        proximo.append(Path(entrada.path))
                else:
                    indice.setdefault(entrada.name, Path(entrada.path))
                    if entrada.path.startswith(prefixo):
                        relativos.setdefault(
                            entrada.path[len(prefixo):].replace(os.sep, "/"), Path(entrada.path))
        if not proximo:
            break
        nivel = proximo
    return indice, relativos


def _normalizar_relativo(valor: str | None) -> str | None:
    """Normaliza um caminho relativo a data/ e o valida: só relativo dentro de data/.

    Devolve o valor normalizado, ou None se for vazio, absoluto (inclusive "C:/…") ou
    contiver segmento ".." — o valor vem do banco, mas pode ter sido gravado por um parser
    sobre conteúdo do investigado, então todo join `PASTA_DADOS / valor` precisa passar aqui.
    """
    valor = (valor or "").strip().replace("\\", "/").strip("/")
    if not valor or ".." in valor.split("/") or re.match(r"^[A-Za-z]:", valor):
        return None
    return valor


def resolver_estrito(valor: str | None) -> Path | None:
    """Como resolver(), mas sem fallback por nome: só o caminho exato dentro de data/.

    Para ponteiros que já são o caminho relativo puro (ex. CO_CAMINHO_ZIP "pasta/x.ufdr"),
    onde cair num arquivo homônimo de outro inquérito seria vazar prova alheia."""
    normalizado = _normalizar_relativo(valor)
    if normalizado is None:
        return None
    candidato = PASTA_DADOS / normalizado
    return candidato if candidato.is_file() else None


def destino_em_dados(valor: str | None) -> Path:
    """Caminho de ESCRITA dentro de data/ a partir de um valor do banco: validado e confinado.

    Diferente de resolver(), não exige que o destino exista (restore pode recriar). Levanta
    ValueError se o valor escapar de data/ — use ANTES de qualquer mkdir/open/os.replace,
    nunca `PASTA_DADOS / valor_do_banco` cru."""
    normalizado = _normalizar_relativo(valor)
    if normalizado is None:
        raise ValueError(f"Caminho fora de data/: {valor!r}")
    base = PASTA_DADOS.resolve()
    destino = (PASTA_DADOS / normalizado).resolve()
    if destino != base and base not in destino.parents:
        raise ValueError(f"Caminho fora de data/: {valor!r}")
    return destino


def resolver(valor: str | None, *, pasta: bool = False) -> Path | None:
    """Resolve o valor gravado no banco (caminho relativo da estrutura nova OU nome puro do
    formato antigo) no caminho real em data/. None se não estiver mais lá."""
    valor = _normalizar_relativo(valor)
    # só caminho relativo dentro de data/: ".." e caminho absoluto (inclusive "C:/…") ficam de
    # fora — o valor vem do banco, mas é o único ponto por onde um caminho vira leitura de disco
    if valor is None:
        return None
    candidato = PASTA_DADOS / valor
    if (candidato.is_dir() if pasta else candidato.is_file()):
        return candidato
    nome = valor.split("/")[-1]
    if pasta:
        return buscar_pasta(nome)
    # plano B por nome: primeiro o índice cacheado (o plano B quente — galerias e o poll caem
    # aqui a cada batida), depois a varredura a frio (achado que o cache ainda não viu)
    achado = indice_entrada()[0].get(nome)
    if achado is None or not achado.is_file():
        achado = buscar_arquivo(nome)
    if achado is not None:
        return achado
    # arquivo que MORA num nó (documento importado numa delegacia com nó — "só no nó"): o central
    # não tem o disco da estação, mas consegue lê-lo pela ponte
    return _cache_no(valor)


def _cache_no(valor: str) -> Path | None:
    """Lê um documento que mora num NÓ (docs/no_descentralizado.md, "só no nó"): a prova fica na
    estação e o central guarda só o texto extraído no banco. Quando um fluxo antigo exige o
    arquivo em disco (aba Original, reextração, blocos IPL/IRPF/IPEI/telefonia), materializa aqui
    uma cópia VOLÁTIL puxada por GET /no/arquivo — escondida em data/.no_cache/, nunca listada,
    nunca organizada, e apagável a qualquer momento (a próxima leitura refaz). None se nenhum nó
    ativo é dono deste arquivo."""
    if (os.environ.get("PAPEL") or "central").strip().lower() == "no":
        # este cache é um truque do CENTRAL (materializar aqui o que está lá). No NÓ o resolver
        # já terminou: o arquivo existe no disco daqui ou não existe — e buscar no banco daqui
        # sem banco aqui pendura a thread no pool para sempre (visto em produção: POST /no/hash
        # de arquivo inexistente travava sem nunca responder).
        return None
    try:
        from funcoes.db_utils import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id_servidor FROM investigacao.inq_tb04_documentos "
                    "WHERE no_arquivo = %s AND id_servidor IS NOT NULL "
                    "ORDER BY id_documento DESC LIMIT 1",
                    (valor,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        from funcoes import no_ponte, no_utils
        no = no_utils.obter(row[0])
        if no is None or not no.get("in_ativa"):
            logger.warning("Documento %s mora no nó %s, mas ele não está ativo", valor, row[0])
            return None
        destino = destino_em_dados(f"{PASTA_CACHE_NO}/{valor}")
        if destino.is_file():
            return destino
        no_ponte.baixar(no, valor, destino)
        return destino if destino.is_file() else None
    except Exception:
        logger.warning("Falha ao ler %s do nó para o cache do central", valor)
        return None
