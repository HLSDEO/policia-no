"""RIF (Relatório de Inteligência Financeira do COAF/UIF) anexado a um inquérito.

Um RIF é uma PASTA em data/ com 'RIF' no nome, contendo os arquivos exportados do SISCOAF:
    RIF{n}_Comunicacoes.csv  · movimentações bancárias (núcleo)
    RIF{n}_Envolvidos.csv    · pessoas/empresas citadas (ligadas por Indexador)
    RIF{n}_Ocorrencias.csv   · enquadramentos/alertas (ligados por Indexador)
    RIF_{n}.pdf              · relatório completo (anexado como documento p/ a IA)

Todos são opcionais: há RIFs sem os CSVs (só o PDF e documentos de trabalho), e nesse caso a
importação anexa o PDF e grava o RIF com zero comunicações/envolvidos/ocorrências.

Os CSVs são delimitados por ';' e codificados em Windows-1252. A importação é
síncrona (os CSVs são pequenos), no mesmo espírito de anexar_documento_inquerito.
Ver db/init/25_rif.sql, rotas/investigacao.py e a página web rif.html.
"""

import csv
import io
import json
import logging
import re
import shutil
import time
from pathlib import Path

from funcoes.cpf_cnpj_utils import valida_cnpj, valida_cpf
from funcoes.entidades_utils import limpar_anotacoes_orfas

from funcoes.db_utils import get_connection
from funcoes.documentos_utils import extrair_texto_documento
from funcoes.hash_utils import calcular_hashes_arquivo
from funcoes.investigacao_utils import PASTA_DADOS, _obter_ou_criar_inquerito
from funcoes.organizacao_arquivos import CATEGORIA_RIF, organizar

logger = logging.getLogger(__name__)


def _subpasta_rif(subpasta: str | None = None) -> Path:
    return PASTA_DADOS / subpasta if subpasta else PASTA_DADOS

# ---------------------------------------------------------------------------
# Descoberta de RIFs em data/
# ---------------------------------------------------------------------------

_RE_NUM_RIF = re.compile(r"RIF[_\s]*0*(\d+)", re.IGNORECASE)


def _normalizar_nome_csv(nome: str) -> str:
    """Normaliza nome de CSV do SISCOAF: minúsculas, sem espaços nas bordas e sem
    repetição de extensão (o Excel/Windows costuma gerar 'RIF115897_Comunicacoes.csv.csv'
    quando o arquivo original já terminava em .csv)."""
    nome = (nome or "").strip().lower()
    while nome.endswith(".csv.csv"):
        nome = nome[:-4]
    return nome


def _achar_csv(pasta: Path, sufixo: str) -> Path | None:
    """Acha o CSV cujo nome termina em <sufixo>.csv (ex.: '_Comunicacoes'), sem depender do número."""
    alvo = sufixo.lower() + ".csv"
    for caminho in pasta.iterdir():
        if caminho.is_file() and _normalizar_nome_csv(caminho.name).endswith(alvo):
            return caminho
    return None


def _achar_pdf(pasta: Path) -> Path | None:
    for caminho in sorted(pasta.iterdir()):
        if caminho.is_file() and caminho.suffix.lower() == ".pdf":
            return caminho
    return None


def _numero_rif(pasta: Path, csv_comunicacoes: Path | None) -> str | None:
    """Deriva o número do RIF (ex.: 104578) do nome do CSV ou da pasta."""
    for nome in ((csv_comunicacoes.name if csv_comunicacoes else ""), pasta.name):
        m = _RE_NUM_RIF.search(nome or "")
        if m:
            return m.group(1)
    return None


def _eh_pasta_rif(pasta: Path) -> bool:
    """Pasta de RIF é toda subpasta de data/ com 'RIF' no nome — os CSVs podem faltar (há RIFs só com
    o PDF/documentos), então o critério é o nome e não a presença do *_Comunicacoes.csv."""
    return pasta.is_dir() and "rif" in pasta.name.lower()


def listar_rifs_disponiveis(subpasta: str | None = None) -> list[dict]:
    """Lista as subpastas de data/ com 'RIF' no nome, para o seletor."""
    pasta_base = _subpasta_rif(subpasta)
    if not pasta_base.is_dir():
        return []
    rifs = []
    for pasta in sorted(pasta_base.iterdir()):
        if not _eh_pasta_rif(pasta):
            continue
        csv_com = _achar_csv(pasta, "_Comunicacoes")
        rifs.append({
            "pasta": pasta.name,
            "nu_rif": _numero_rif(pasta, csv_com),
            "tem_comunicacoes": csv_com is not None,
            "tem_envolvidos": _achar_csv(pasta, "_Envolvidos") is not None,
            "tem_ocorrencias": _achar_csv(pasta, "_Ocorrencias") is not None,
            "tem_pdf": _achar_pdf(pasta) is not None,
        })
    return rifs


# ---------------------------------------------------------------------------
# RIFs avulsos vindos de ZIP (arquivos soltos, sem pasta)
# ---------------------------------------------------------------------------
# O ZIP de prova costuma trazer, no mesmo archive, documentos genéricos + CSVs do
# SISCOAF soltos na raiz (ex.: RIF146310_Comunicacoes.csv, RIF115897_Comunicacoes.csv.csv).
# A expansão do ZIP achata subpastas (_expandir_zipfile, em documentos_utils.py), então
# esses CSVs caem soltos em data/ e a importação em fila os trataria como documento
# genérico — foi o caso do inquérito 2024.0081190 (4 RIFs importados como OUTROS).
# O agrupamento abaixo reconstitui cada RIF pelo NÚMERO no nome do arquivo, antes da
# importação genérica (ver funcoes/zip_import.py).

_RE_RIF_CSV_AVULSO = re.compile(
    r"rif[_\s]*0*(\d+)[_\s\-]*.*?(comunicacoes|envolvidos|ocorrencias)\s*(\.csv)+$",
    re.IGNORECASE,
)
_RE_RIF_PDF_AVULSO = re.compile(
    r"rif[_\s]*0*(\d+)\s*(\.pdf)+$", re.IGNORECASE
)


def separar_grupos_rif_avulsos(nomes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Separa arquivos soltos de RIF (pelo nome) do resto.

    Recebe os nomes como estão em data/ (só o nome, sem diretório — é o que
    expandir_zip_para_importacao devolve) e devolve (grupos, restantes):
    - grupos: {nu_rif: {"comunicacoes": nome, "envolvidos": nome,
      "ocorrencias": nome, "pdf": nome}} — só grupos com o CSV núcleo
      (_Comunicacoes); sem ele, é genérico (evita transformar um PDF avulso
      "RIF_xxx.pdf" em RIF vazio);
    - restantes: tudo que não entrou em nenhum grupo válido, na ordem original.

    Tolera '.csv.csv' (dupla extensão do Excel), caixa alta/baixa e separadores
    '_', ' ' ou '-' entre número e tipo.
    """
    candidatos: dict[str, dict] = {}
    for nome in nomes or []:
        base = (nome or "").strip()
        if not base or "/" in base or "\\" in base:
            continue
        normalizado = _normalizar_nome_csv(base)
        m = _RE_RIF_CSV_AVULSO.search(normalizado)
        if m:
            nu, tipo = m.group(1), m.group(2).lower()
            grupo = candidatos.setdefault(nu, {})
            # primeiro vale (expansão já desduplicou com " (2)"); não sobrescreve
            if tipo not in grupo:
                grupo[tipo] = base
            continue
        if base.lower().endswith(".pdf"):
            m2 = _RE_RIF_PDF_AVULSO.search(base.strip().lower())
            if m2:
                nu = m2.group(1)
                grupo = candidatos.setdefault(nu, {})
                if "pdf" not in grupo:
                    grupo["pdf"] = base

    grupos = {nu: g for nu, g in candidatos.items() if g.get("comunicacoes")}
    em_grupo = {nome for g in grupos.values() for nome in g.values()}
    restantes = [n for n in (nomes or []) if n not in em_grupo]
    return grupos, restantes


def montar_pasta_grupo_rif(nu_rif: str, arquivos_grupo: dict, subpasta: str | None = None) -> str:
    """Cria data/[<delegacia>/]RIF_<nu>_zip_N com cópias dos CSVs/PDF avulsos do grupo.

    Renomeia '.csv.csv' para '.csv' na cópia (é o que _achar_csv entende) e devolve
    o nome da pasta criada, pronta para importar_rif(). Levanta FileNotFoundError
    quando algum arquivo do grupo não está mais em disco (caso de nó remoto).
    """
    import shutil

    base = _subpasta_rif(subpasta)
    base.mkdir(parents=True, exist_ok=True)
    pasta = base / f"RIF_{nu_rif}_zip"
    sufixo = 2
    while pasta.exists():
        pasta = base / f"RIF_{nu_rif}_zip ({sufixo})"
        sufixo += 1
        if sufixo > 999:
            raise ValueError(f"RIF {nu_rif}: arquivos demais com este nome em data/.")
    pasta.mkdir(parents=True)
    try:
        for tipo, nome in arquivos_grupo.items():
            origem = base / nome
            if not origem.is_file():
                raise FileNotFoundError(nome)
            destino_nome = nome
            if tipo in ("comunicacoes", "envolvidos", "ocorrencias"):
                # 'RIF115897_Comunicacoes.csv.csv' vira 'RIF115897_Comunicacoes.csv'
                raiz = nome
                while raiz.lower().endswith(".csv"):
                    raiz = raiz[: -len(".csv")]
                destino_nome = raiz + ".csv"
            shutil.copy2(origem, pasta / destino_nome)
    except BaseException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise
    return pasta.name


# ---------------------------------------------------------------------------
# Upload do RIF pelo navegador
# ---------------------------------------------------------------------------

_EXTENSOES_RIF_UPLOAD = (".csv", ".pdf")


def salvar_rif_enviado(itens, subpasta: str | None = None) -> dict:
    """Grava em data/[<delegacia>/<PASTA>/] os arquivos de um RIF enviados pelo navegador.

    `itens` é a lista de (fluxo, nome_relativo): o nome relativo vem do webkitRelativePath do
    navegador (ex.: "RIF_104578/RIF_104578_Comunicacoes.csv") e é ele que preserva a estrutura
    de PASTA que o RIF exige — o primeiro componente do caminho é o nome da pasta. O nome ganha
    prefixo "RIF_" quando falta "RIF" nele (a listagem e a importação só reconhecem pastas com
    "RIF" no nome), e colisão de nome ganha sufixo "(2)", como no upload de documento.

    Só CSV e PDF entram (é o que o import usa — os *_Comunicacoes/*_Envolvidos/*_Ocorrencias e
    o 1º PDF); o resto volta em "falhas". Limite por arquivo: limite_upload_bytes() — o mesmo do
    documento genérico (UPLOAD_DOCUMENTO_MB). Qualquer erro de gravação remove a pasta inteira,
    para não deixar RIF pela metade em data/.

    Devolve {"pasta", "arquivos": [{nome, tamanho_bytes}], "falhas": [msg]}. Levanta ValueError
    quando nada pôde ser salvo."""
    from funcoes.documentos_utils import _BLOCO_UPLOAD, _nome_arquivo_seguro, limite_upload_bytes, limite_upload_mb

    if not itens:
        raise ValueError("Nenhum arquivo enviado.")

    def _relativo_seguro(nome_relativo: str) -> str | None:
        """Sanitiza cada componente do caminho relativo; devolve None se algum virar vazio."""
        partes = [(p or "").replace("\\", "/") for p in nome_relativo.split("/")]
        seguras = []
        for p in partes:
            s = _nome_arquivo_seguro(p)
            if not s:
                return None
            seguras.append(s)
        return "/".join(seguras)

    # a pasta é o primeiro componente do primeiro caminho relativo válido
    pasta_bruta = ""
    for _, nome_relativo in itens:
        partes = (nome_relativo or "").replace("\\", "/").split("/")
        primeiro = (partes[0] if partes else "").strip().strip(".")
        if primeiro and len(partes) >= 2:
            pasta_bruta = primeiro
            break
    if not pasta_bruta:
        raise ValueError(
            "Envie a pasta inteira do RIF — arraste a pasta ou use o botão de escolher pasta "
            "(os arquivos precisam vir com o caminho da pasta)."
        )
    pasta_bruta = _nome_arquivo_seguro(pasta_bruta)
    if not pasta_bruta:
        raise ValueError("Nome de pasta inválido.")
    # comparação com o caminho relativo de cada arquivo usa o nome SEM o prefixo (o prefixo só
    # muda o nome final em data/, não o que veio do navegador)
    pasta_final = pasta_bruta if "rif" in pasta_bruta.lower() else "RIF_" + pasta_bruta

    # Delegacia com nó ativo (docs/no_descentralizado.md): a pasta inteira do RIF vai para a
    # data() DO NÓ — o central repassa cada arquivo em streaming e não fica com cópia nenhuma
    # (a mesma regra do upload de documento e do .ufdr).
    from funcoes import no_utils

    no = no_utils.servidor_ativo_para(subpasta)
    if no is not None:
        return _salvar_rif_no(no, itens, subpasta, pasta_bruta, pasta_final, _relativo_seguro)

    base = _subpasta_rif(subpasta)
    base.mkdir(parents=True, exist_ok=True)

    # mkdir sem parents e sem exist_ok: é a reserva exclusiva do nome — dois uploads simultâneos
    # da mesma pasta não se sobrescrevem, o segundo ganha sufixo (como em salvar_documento_enviado)
    alvo = base / pasta_final
    sufixo = 2
    while True:
        try:
            alvo.mkdir()
            break
        except FileExistsError:
            if sufixo > 999:
                raise ValueError(f"{pasta_final}: já existem pastas demais com este nome em data/.")
            alvo = base / f"{pasta_final} ({sufixo})"
            sufixo += 1

    limite_bytes = limite_upload_bytes()
    limite_mb = limite_upload_mb()
    salvos: list[dict] = []
    falhas: list[str] = []
    try:
        for fluxo, nome_relativo in itens:
            relativo = _relativo_seguro(nome_relativo or "")
            if not relativo or "/" not in relativo:
                falhas.append(f"{nome_relativo or '(sem nome)'}: envie a pasta inteira do RIF, não arquivos soltos.")
                continue
            partes = relativo.split("/")
            if partes[0] != pasta_bruta:
                falhas.append(f"{relativo}: arquivo fora da pasta '{pasta_bruta}' — não foi salvo.")
                continue
            nome = partes[-1]
            if Path(nome).suffix.lower() not in _EXTENSOES_RIF_UPLOAD:
                falhas.append(f"{nome}: só CSV e PDF entram na pasta do RIF.")
                continue

            destino = alvo
            for p in partes[1:-1]:
                destino = destino / p
            destino.mkdir(parents=True, exist_ok=True)
            arquivo_destino = destino / nome
            # "xb" (criação exclusiva) e não "wb": nunca sobrescrever arquivo de outro upload
            s = 2
            while True:
                try:
                    handle = open(arquivo_destino, "xb")
                    break
                except FileExistsError:
                    raiz, extensao = Path(nome).stem, Path(nome).suffix
                    arquivo_destino = destino / f"{raiz} ({s}){extensao}"
                    s += 1

            total = 0
            try:
                with handle:
                    while True:
                        bloco = fluxo.read(_BLOCO_UPLOAD)
                        if not bloco:
                            break
                        total += len(bloco)
                        if total > limite_bytes:
                            raise ValueError(f"{nome}: maior que o limite de {limite_mb} MB por arquivo.")
                        handle.write(bloco)
                if total == 0:
                    raise ValueError(f"{nome}: arquivo vazio.")
            except ValueError as exc:
                arquivo_destino.unlink(missing_ok=True)
                falhas.append(str(exc))
                continue
            salvos.append({"nome": nome, "tamanho_bytes": total})
    except BaseException:
        # nada de pasta pela metade em data/: ela apareceria no seletor como um RIF inteiro
        shutil.rmtree(alvo, ignore_errors=True)
        raise

    if not salvos:
        shutil.rmtree(alvo, ignore_errors=True)
        raise ValueError("; ".join(falhas) if falhas else "Nenhum arquivo CSV/PDF pôde ser salvo.")
    logger.info("RIF enviado pelo navegador: pasta %s, %s arquivo(s)", alvo.name, len(salvos))
    return {"pasta": alvo.name, "arquivos": salvos, "falhas": falhas}


def _salvar_rif_no(no, itens, subpasta: str | None, pasta_bruta: str, pasta_final: str,
                   relativo_seguro) -> dict:
    """RIF em delegacia com NÓ: a pasta vai inteira para a data() DO NÓ (docs/
    no_descentralizado.md) — o central repassa cada arquivo em streaming (PUT /no/enviar) e não
    fica com cópia. O nome livre da pasta é reivindicado contra a listagem "rif" do próprio nó
    (mesmo sufixo "(2)" do salvador local); o nó cria a pasta-mãe sozinho no primeiro arquivo.

    Diferença assumida para o salvador local: sem mkdir exclusivo no nó, dois uploads SIMULTÂNEOS
    do mesmo nome de pasta poderiam interleir — RIF é arraste manual de um investigador, não corrida.
    Arquivo recusado (extensão, fora da pasta, vazio, grande demais) volta em "falhas" como no
    local; se NADA salvar, nada foi gravado no nó (a pasta só nasce com o primeiro arquivo bom)."""
    from funcoes import no_ponte
    from funcoes.documentos_utils import limite_upload_bytes, limite_upload_mb

    try:
        existentes = {a.get("pasta") for a in no_ponte.listar(no, subpasta, "rif")}
    except no_ponte.NoIndisponivel as exc:
        raise ValueError(str(exc)) from exc
    alvo = pasta_final
    sufixo = 2
    while alvo in existentes:
        if sufixo > 999:
            raise ValueError(f"{pasta_final}: já existem pastas demais com este nome no nó.")
        alvo = f"{pasta_final} ({sufixo})"
        sufixo += 1

    base_rel = f"{subpasta}/" if subpasta else ""
    limite_bytes = limite_upload_bytes()
    salvos: list[dict] = []
    falhas: list[str] = []
    for fluxo, nome_relativo in itens:
        relativo = relativo_seguro(nome_relativo or "")
        if not relativo or "/" not in relativo:
            falhas.append(f"{nome_relativo or '(sem nome)'}: envie a pasta inteira do RIF, não arquivos soltos.")
            continue
        partes = relativo.split("/")
        if partes[0] != pasta_bruta:
            falhas.append(f"{relativo}: arquivo fora da pasta '{pasta_bruta}' — não foi salvo.")
            continue
        nome = partes[-1]
        if Path(nome).suffix.lower() not in _EXTENSOES_RIF_UPLOAD:
            falhas.append(f"{nome}: só CSV e PDF entram na pasta do RIF.")
            continue
        remoto = base_rel + alvo + "/" + "/".join(partes[1:])
        try:
            res = no_ponte.enviar_arquivo(no, fluxo, remoto, max_bytes=limite_bytes)
        except no_ponte.NoIndisponivel as exc:
            falhas.append(str(exc))
            continue
        salvos.append({"nome": res["nome"], "tamanho_bytes": res["tamanho_bytes"]})

    if not salvos:
        raise ValueError("; ".join(falhas) if falhas else "Nenhum arquivo CSV/PDF pôde ser salvo.")
    logger.info("RIF enviado pelo navegador AO NÓ %s: pasta %s, %s arquivo(s)",
                no.get("no_servidor"), alvo, len(salvos))
    return {"pasta": alvo, "arquivos": salvos, "falhas": falhas}


# ---------------------------------------------------------------------------
# Parsing de CSV / valores / datas (formato SISCOAF, pt-BR)
# ---------------------------------------------------------------------------

def _ler_csv(caminho: Path) -> list[dict]:
    """Lê um CSV do SISCOAF (';' + Windows-1252) como lista de dicts. Tenta UTF-8 e cai para cp1252."""
    dados = caminho.read_bytes()
    texto = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = dados.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        texto = dados.decode("latin-1", errors="replace")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    return [linha for linha in leitor]


def _txt(valor) -> str | None:
    """Normaliza um campo textual: '-'/vazio viram None; recorta espaços.
    Detecta números em notação científica (ex: '6,07012E+13') e converte para número real."""
    if valor is None:
        return None
    v = str(valor).strip()
    if v == "" or v == "-":
        return None
    # Detecta notação científica (ex: "6,07012E+13" ou "1.5E+10") e converte para número
    if re.search(r'[eE][+-]?\d+', v):
        try:
            # Converte vírgula para ponto, depois para float, depois para int (sem casas decimais)
            v_norm = v.replace(',', '.')
            num = float(v_norm)
            # Se é um número inteiro (sem casas decimais), devolve como int
            if num == int(num):
                return str(int(num))
            # Senão, devolve com precisão máxima (sem notação científica)
            return f"{num:.0f}" if num > 1e10 else str(num)
        except (ValueError, OverflowError):
            # Se falhar a conversão, mantém como estava
            pass
    return v


def _indexador(valor) -> int | None:
    """Indexador da linha como inteiro puro. None quando vazio/'-' ou quando não é só dígitos.

    Os CSVs do SISCOAF trazem, DEPOIS das linhas de dados, uma legenda dos códigos de segmento
    (Indexador = '45 - DPF - ...') e um rodapé com um hash. Ambos têm Indexador não-numérico, então
    exigir dígito puro no Indexador é o filtro que descarta esse lixo de rodapé em qualquer RIF."""
    v = _txt(valor)
    if v is None or not v.isdigit():
        return None
    try:
        num = int(v)
        # Indexador deve ser um pequeno inteiro (1-9999). Valores muito grandes indicam erro de parsing.
        if num > 9999:
            return None
        return num
    except (ValueError, OverflowError):
        return None


def _valor_br(valor) -> float | None:
    """Converte '281.500,00' (pt-BR) em 281500.00. Devolve None para vazio/'-'."""
    v = _txt(valor)
    if v is None:
        return None
    v = v.replace("R$", "").replace(" ", "")
    # remove separador de milhar '.' e troca a vírgula decimal por ponto
    v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _data_br(valor):
    """Converte 'dd/mm/aaaa' (com hora opcional) em datetime.date. None se vazio/'-'/inválido."""
    from datetime import datetime

    v = _txt(valor)
    if v is None:
        return None
    parte = v.split(" ")[0]
    try:
        return datetime.strptime(parte, "%d/%m/%Y").date()
    except ValueError:
        return None


def _datahora_br(valor):
    """Converte 'dd/mm/aaaa HH:MM:SS' em datetime (ou date se não houver hora). None se vazio/'-'."""
    from datetime import datetime

    v = _txt(valor)
    if v is None:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Estruturação das contrapartes a partir do texto livre (informacoesAdicionais)
# ---------------------------------------------------------------------------
# O texto de cada comunicação lista "quem creditou e quem foi debitado" em um de
# três formatos padrão do COAF. Parsing determinístico (sem IA), auditável.

_VAL_RE = r"\d+(?:\.\d{3})*,\d{2}"               # valor pt-BR: 131.673,59 ou 30689,00 (sem separador de milhar)
_DOC_RE = r"\d[\d./\-]{6,}\d"                     # CPF/CNPJ com pontuação


def _frouxo(*atomos: str) -> str:
    """Regex que casa a sequência de átomos mesmo partida por espaços no meio ('TOTA LIZOU').

    A quebra de linha do relatório original do SISCOAF às vezes sobrevive como um espaço solto
    DENTRO de uma palavra (ver _remonta_quebra_pagina). Como os marcadores de seção ('A DÉBITO
    TOTALIZOU') decidem a direção de TODAS as contrapartes da comunicação, um espaço perdido aí
    jogava a movimentação inteira para o lado errado — daí casar cada letra com \\s* no meio.
    Uso: _frouxo(*"TOTALIZOU") p/ palavra simples, _frouxo("D", "[EÉ]", "B", ...) com acento opcional."""
    return r"\s*".join(atomos)


def _valor_texto(s: str) -> float:
    if "," in s:
        return float(s.replace(".", "").replace(",", "."))
    return float(s)


def _limpa_contraparte(n: str | None) -> str | None:
    v = re.sub(r"\s+", " ", n or "").strip(" -.").strip()
    return v or None


def _normaliza_doc(doc: str | None) -> str | None:
    """Normaliza o CPF/CNPJ da contraparte, conferindo o dígito verificador.

    O SISCOAF grava o documento num campo de largura fixa de 14, então CPF vem alargado com zeros
    à esquerda ("00003253383610" é o CPF 032.533.836-10) e o banco às vezes faz o mesmo com o CNPJ
    ("040.953.545/0001-37"). Guardar isso como veio faz o mesmo CPF virar duas pessoas diferentes
    no cruzamento entre RIFs. Aqui o documento só é encurtado quando os dígitos restantes fecham o
    DV — se não fecharem, devolve o que veio (perder o dado seria pior que exibi-lo torto)."""
    if not doc:
        return doc
    d = re.sub(r"\D", "", doc)
    if len(d) == 11 and valida_cpf(d):
        return d
    if len(d) == 14 and valida_cnpj(d):
        return d
    if len(d) > 11 and d[: len(d) - 11].strip("0") == "" and valida_cpf(d[-11:]):
        return d[-11:]
    if len(d) > 14 and d[: len(d) - 14].strip("0") == "" and valida_cnpj(d[-14:]):
        return d[-14:]
    # caminho inverso: o zero à esquerda se PERDEU no texto ("095.917.246-75" chegando como
    # "95.917.246-75"). Só recompõe se o DV fechar com o zero de volta.
    if len(d) == 10 and valida_cpf("0" + d):
        return "0" + d
    if len(d) == 13 and valida_cnpj("0" + d):
        return "0" + d
    return doc


def _parece_data(doc: str | None) -> bool:
    """True quando o 'documento' é na verdade uma data (10.06.2024, 23/01/2025).

    _DOC_RE casa qualquer sequência de dígitos com ponto/barra/traço, então uma data cabe nele.
    Nos textos que trazem tabelas de datas e valores (parcelas de consórcio, movimentação por
    período) o Formato A — o fallback frouxo — casava essas linhas e inventava contrapartes com
    a data no lugar do CPF e um pedaço da tabela no lugar do nome. CPF/CNPJ nunca tem essa forma,
    então a linha inteira é descartada (ver extrair_contrapartes)."""
    return bool(re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", (doc or "").strip()))


def _extrair_totais_texto(t: str) -> dict:
    """Extrai totais de crédito e débito do texto normalizado da comunicação."""
    totais = {}
    # "Total a crédito: R$ X" / "Total a crdito R$X"
    m = re.search(r"(?:total|Total)\s+a\s+cr[eé]dito\s*:?\s*R?\$?\s*(\d+(?:\.\d{3})*[.,]\d{2})", t)
    if m:
        totais["credito"] = _valor_texto(m.group(1))
    m = re.search(r"(?:total|Total)\s+a\s+d[ée]bito\s*:?\s*R?\$?\s*(\d+(?:\.\d{3})*[.,]\d{2})", t)
    if m:
        totais["debito"] = _valor_texto(m.group(1))
    # "créditos somaram R$ X" / "créditos totalizaram R$ X" (Mercado Pago e outros)
    if "credito" not in totais:
        m = re.search(
            r"cr[eé]ditos?\s+(?:somaram|totalizaram|recebeu\s+a\s+cr[eé]dito\s+o\s+"
            r"montante\s+de\s+)?\s*R?\$?\s*(\d+(?:\.\d{3})*[.,]\d{2})", t, re.I)
        if m:
            totais["credito"] = _valor_texto(m.group(1))
    if "debito" not in totais:
        m = re.search(r"d[ée]bitos?\s+(?:totalizaram|somaram)\s+R?\$?\s*(\d+(?:\.\d{3})*[.,]\d{2})", t, re.I)
        if m:
            totais["debito"] = _valor_texto(m.group(1))
    return totais


def _completar_contrapartes(itens: list[dict], t: str) -> list[dict]:
    """Ordena contrapartes: crédito antes de débito, maior valor primeiro.
    DESCONHECIDO (nome=None) ao final de cada direção.

    Ponto único por onde todo formato passa, então é aqui que o documento é normalizado
    (ver _normaliza_doc) e a contraparte-fantasma é descartada (ver _parece_data) — vale para os
    ~15 formatos de uma vez."""
    itens = [it for it in itens if not _parece_data(it.get("doc"))]
    for it in itens:
        it["doc"] = _normaliza_doc(it.get("doc"))
    # Alguns textos repetem o MESMO parágrafo (um resumo em "Característica da Movimentação" e de
    # novo sob "ORIGEM DOS RECURSOS:"), o que fazia a contraparte ser contada duas vezes e inflar o
    # total. Duplicata exata — mesma direção, nome, documento E valor — é repetição de texto: duas
    # operações realmente distintas viriam somadas numa linha só, com QTDE maior.
    vistos, unicos = set(), []
    for it in itens:
        chave = (it["direcao"], it.get("nome"), it.get("doc"), it["valor"])
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(it)
    unicos.sort(key=lambda x: (x["direcao"] != "credito", x["nome"] is None, -x["valor"]))
    return unicos


# Cabeçalhos "A CRÉDITO(,) TOTALIZOU/FORAM R$..." do padrão Banestes & cia. A vírgula é opcional
# ("A DEBITO, TOTALIZOU") e as palavras são frouxas porque a paginação as parte ("A DEBITO TOTA
# LIZOU") ou as cola ("A CREDITOTOTALIZOU").
_PAPEL_E = r"(origem|destino|depositante|sacador)"   # papel da contraparte no extrato Sicoob (formato E)
_VERBO_TOTAL = r"(?:" + _frouxo(*"TOTALIZOU") + r"|" + _frouxo(*"FORAM") + r")"
_MARCA_CREDITO = r"\bA\s*" + _frouxo("C", "R", "[EÉ]", "D", "I", "T", "O") + r"\s*,?\s*" + _VERBO_TOTAL
_MARCA_DEBITO = r"\bA\s*" + _frouxo("D", "[EÉ]", "B", "I", "T", "O") + r"\s*,?\s*" + _VERBO_TOTAL


def _pos_debito(t: str) -> int:
    """Offset do início da seção de débito/saídas — separa remetentes (crédito) de favorecidos (débito)."""
    posicoes = []
    for pat in (r"principais favorecidos", r"principais destinat", r"Valor Enviado",
                r"lan[çc]amentos a d[ée]bito", r"Os d[ée]bitos", r"[Dd]estino dos d[ée]bitos",
                r"[Dd]estino dos recursos",
                r"I+\s*\)\s*[Dd][ée]bitos",
                r"movimenta[çc][õo]es? a d[ée]bitos?",
                _MARCA_DEBITO):
        for m in re.finditer(pat, t, re.I):
            # Ignora "Os débitos" quando é apenas um resumo ("totalizaram" logo após)
            if pat == r"Os d[ée]bitos" and re.search(r"totalizaram", t[m.end():m.end()+50], re.I):
                continue
            posicoes.append(m.start())
    return min(posicoes) if posicoes else len(t)


def _soma_qtde_clausula(clausula: str) -> int:
    """Soma as quantidades citadas num trecho 'sendo: N PIX - R$x N OUTRO - R$y ...'; 1 se não achar nada."""
    qtdes = re.findall(r"(\d+)\s+[A-ZÀ-Ú][A-ZÀ-Ú/\.\s]*?\s*-\s*R?\$?\s*" + _VAL_RE, clausula, re.I)
    return sum(int(q) for q in qtdes) if qtdes else 1


def _rastreador_direcao(t: str, marcadores: list[tuple[str, str]]):
    """P/ formatos onde crédito e débito se intercalam no texto (sem um único ponto de corte): devolve
    uma função `direcao_em(pos)` que acha o ÚLTIMO marcador de seção antes de `pos` (None se nenhum)."""
    eventos = []
    for pat, direc in marcadores:
        for m in re.finditer(pat, t, re.I):
            eventos.append((m.start(), direc))
    eventos.sort()

    def direcao_em(pos: int) -> str | None:
        atual = None
        for ini, direc in eventos:
            if ini <= pos:
                atual = direc
            else:
                break
        return atual

    return direcao_em


def _valor_flex(s: str) -> float:
    """Valor que tanto pode vir em pt-BR ('620.400,00') quanto no formato US ('93,000.00').

    As carteiras digitais misturam os dois no MESMO texto, então não dá para escolher um dos dois
    por comunicação. Decide pela forma: vírgula como separador de milhar (ou ponto com exatamente
    dois decimais e nenhum outro separador) é US; o resto é pt-BR."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}", s):
        return float(s.replace(",", ""))
    return _valor_texto(s)


def _valor_texto_sem_centavos(s: str) -> float:
    """Valor com ponto de milhar e SEM centavos (ex.: '64.500' = 64500,00, não 64,5) — Formato L."""
    if "," in s:
        return _valor_texto(s)
    return float(s.replace(".", ""))


def _remonta_quebra_pagina(texto: str) -> str:
    """Alguns RIFs trazem o texto de informacoesAdicionais já formatado em 'linhas' de largura fixa
    (do relatório original do SISCOAF), concatenadas sem quebra real — o que sobra no lugar da quebra
    é uma tira enorme de espaços (200+), que na maioria das vezes cai entre duas palavras (inofensivo),
    mas às vezes cai NO MEIO de uma palavra ou número, produzindo lixo como 'FAB IOLA' ou
    '798.58 8.567-68' (CPF partido). Uma tira de espaços grande com letra/dígito nos dois lados é
    fechada (sem deixar espaço nenhum) em vez de virar um espaço — as demais tiras (curtas, ou que
    caem numa borda de pontuação) só colapsam para um espaço, como antes."""
    def fecha(m):
        s = m.group()
        if len(s) < 4:
            return " "
        antes = m.string[m.start() - 1] if m.start() > 0 else ""
        depois = m.string[m.end()] if m.end() < len(m.string) else ""
        return "" if antes.isalnum() and depois.isalnum() else " "
    return _repara_numeros(re.sub(r"[ \t]+", fecha, texto))


# Espaços que sobraram DENTRO de um número/documento depois de colapsar a paginação. Só contextos
# em que o espaço é comprovadamente lixo (separador decimal, grupo de milhar de 3 dígitos, último
# dígito do CPF/CNPJ) — nunca "digito espaço digito" solto, que costuma separar dois números.
_REPAROS = (
    (r"R\s+\$", "R$"),                                     # "R $ 32.000,00"
    (r"(?<=\d)\s+(?=,\d{2}(?!\d))", ""),                   # "1.200.000 ,00"
    (r"(?<=\d[.,])\s+(?=\d{3}(?!\d))", ""),                # "107. 985,24"
    (r"(?<=\d\.\d{2})\s+(?=\d,\d{2}(?!\d))", ""),          # "32.00 0,00"
    (r"(?<=\d{3}\.\d{3}\.\d{3}-\d)\s+(?=\d(?!\d))", ""),   # CPF "798.588.567-6 8"
    (r"(?<=\d{2}\.\d{3}\.\d{3}/\d{4}-\d)\s+(?=\d(?!\d))", ""),  # CNPJ
    (r"(?<=\d{2}\.\d{3})\s+(?=\.\d{3}/\d{4})", ""),        # CNPJ "11.852 .758/0001-74"
)


def _repara_numeros(t: str) -> str:
    for padrao, troca in _REPAROS:
        t = re.sub(padrao, troca, t)
    return t


def normalizar_texto_analise(texto: str | None) -> str | None:
    """Versão legível do texto da análise, para exibir/enviar à IA — mesma normalização que o parser
    já faz internamente (desfaz a paginação de largura fixa do SISCOAF e remonta números partidos).

    O texto continua sendo o do analista: só some o preenchimento de layout, que na tela vira tira de
    200+ espaços (e no meio do parágrafo abre uma segunda 'coluna' fantasma). O cru fica no banco —
    é dele que sai a informação de quanto media cada tira, necessária para melhorar a remontagem."""
    if not texto:
        return texto
    return _remonta_quebra_pagina(texto).strip()


def extrair_contrapartes(texto: str | None) -> list[dict]:
    """Extrai as contrapartes (remetentes/favorecidos) de informacoesAdicionais.

    Devolve lista de dicts {direcao: 'credito'|'debito', nome, doc, valor, qtde, obs}, ordenada
    por direção e valor decrescente. Reconhece os formatos padrão do COAF observados em RIFs reais
    (Bradesco/clássico, Santander — nos dois estilos de encerramento —, Banco do Brasil, Nu Pagamentos,
    Sicoob, Itaú (Origem/Destino e detalhado por amostra), Mercado Pago, percentual "DESTACANDO",
    "Transferências Instantâneas cujos principais..." e carteira digital/Stark Bank); texto narrativo
    sem nenhuma dessas estruturas devolve lista vazia — o front mostra o texto cru nesse caso."""
    if not texto:
        return []
    t = _remonta_quebra_pagina(texto)
    corte = _pos_debito(t)

    def direcao(pos: int) -> str:
        return "debito" if pos >= corte else "credito"

    itens: list[dict] = []

    # Formato P (registro de movimentação em espécie do BB, segmento 42): uma linha de terminal, sem
    # texto analítico — "DEPOSITO EM DINHEIRO E CHEQUE <depositante>-<doc><agência><conta> - R$ <valor>",
    # tudo em campos de largura fixa e zero-preenchidos (doc 14, agência 9, conta 11; valor em reais
    # inteiros, sem centavos). O depositante é a contraparte a crédito.
    for m in re.finditer(
        r"DEP[OÓ]SITO\s+EM\s+DINHEIRO(?:\s*E\s*CHEQUE)?\s*([A-ZÀ-Ú][A-ZÀ-Ú\s]{4,60}?)\s*-\s*"
        r"(\d{14})\s*\d{9}\s*\d{11}\s*-\s*R\$\s*0*(\d+)\b", t, re.I):
        doc = m.group(2)
        doc = doc[3:] if doc.startswith("000") else doc   # CPF vem preenchido até a largura de CNPJ
        itens.append({"direcao": "credito", "nome": _limpa_contraparte(m.group(1)), "doc": doc,
                      "valor": float(m.group(3)), "qtde": 1, "obs": "depósito em espécie"})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato B (Santander): "- NOME - CPF/CNPJ: num - Valor Recebido/Enviado: R$valor" seguido de
    # "por meio de N lancamento(s)" OU ", sendo: <detalhamento>" (variante sem contagem explícita —
    # a quantidade é somada a partir do detalhamento, ex.: "sendo: 7 PIX - R$43.000,00").
    matches_b = list(re.finditer(
        r"-\s*([^-\n]+?)\s*-\s*(?:CPF|CNPJ)\s*:?\s*([\d./\-]+)\s*-\s*Valor\s+(Recebido|Enviado)\s*:?\s*"
        r"R?\$?\s*(" + _VAL_RE + r")\s*(?:por meio de\s*(\d+)\s*lan[çc]amento|,?\s*sendo\s*:?)", t, re.I))
    for idx, m in enumerate(matches_b):
        if m.group(5):
            qtde = int(m.group(5))
        else:
            fim = min(len(t), m.end() + 150, matches_b[idx + 1].start() if idx + 1 < len(matches_b) else len(t))
            clausula = t[m.end():fim]
            m_fim = re.search(r"Resumo|Principais|J[áa] quanto|Concluíd|Ao analisar|Cliente e|Adicionalmente|"
                               r"Diante do exposto", clausula, re.I)
            if m_fim:
                clausula = clausula[:m_fim.start()]
            qtde = _soma_qtde_clausula(clausula)
        itens.append({"direcao": "credito" if m.group(3).lower() == "recebido" else "debito",
                      "nome": _limpa_contraparte(m.group(1)), "doc": m.group(2),
                      "valor": _valor_texto(m.group(4)), "qtde": qtde, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato F (Itaú "ORIGEM/DESTINO DOS RECURSOS"): "sendo o(s) principal(ais) emitentes(s):
    # NOME, CPF (R$ VALOR), ..." — débito similar com "favorecidos(s)". A direção é determinada
    # pela seção (ORIGEM=crédito, DESTINO=débito), independentemente do corte global.
    for secao, direcao_f in (
        (r"ORIGEM\s+DOS\s+RECURSOS", "credito"),
        (r"DESTINO\s+DOS\s+RECURSOS", "debito"),
    ):
        m_sec = re.search(secao + r":(.*?)(?:DESTINO\s+DOS\s+RECURSOS|ENQUADRAMENTO|$)", t, re.I | re.DOTALL)
        if not m_sec:
            continue
        marcador = r"emitentes?\s*\(\s*s\s*\)?\s*:" if direcao_f == "credito" else r"favorecidos?\s*\(\s*s\s*\)?\s*:"
        m_marc = re.search(marcador + r"\s*(.+)", m_sec.group(1), re.I)
        if not m_marc:
            continue
        for m in re.finditer(
            r"([A-ZÀ-Ú][A-ZÀ-Ú\s\.]{2,}?)\s*[,;]+\s*(\d[\d./\-]{6,}\d)(?:[^()]*?)"
            r"\(\s*R?\$?\s*(" + _VAL_RE + r")\s*\)", m_marc.group(1), re.I
        ):
            itens.append({"direcao": direcao_f,
                          "nome": _limpa_contraparte(m.group(1)), "doc": m.group(2),
                          "valor": _valor_texto(m.group(3)), "qtde": 1, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato G (Itaú detalhado / "em amostra"): "R$ VALOR - N transações - NOME - DOCUMENTO: CPF"
    # Aparece nas seções "Em amostra identificamos como principais contrapartes:" dentro de
    # ORIGEM (crédito) e DESTINO (débito) — direção pelo corte global.
    # Aceita "transações", "transacoes" ou "transaes" (sem acento).
    for m in re.finditer(
        r"R\$\s*(\d+(?:\.\d{3})*[.,]\d{2})\s*-\s*(\d+)\s*transa[çc]?[õo]?es?\s*-\s*"
        r"((?:(?!R\s*\$).)+?)"
        r"\s*-\s*(?:DOCUMENTO)\s*:?\s*(\d[\d./\-]{6,}\d)", t, re.I
    ):
        itens.append({"direcao": direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(3)), "doc": m.group(4),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato H (Mercado Pago / transferências): "R$ VALOR - NOME - CPF/CNPJ via N transferências"
    # O nome não pode conter "R$" para evitar overmatching em parágrafos longos.
    # O CPF/CNPJ é opcional porque a mesma lista traz "R$X - Mesma titularidade via N Transferências"
    # (conta do próprio investigado em outro banco) — contraparte real, e das maiores, que ficava de fora.
    for m in re.finditer(
        r"R\$\s*(\d+(?:\.\d{3})*[.,]\d{2})\s*-\s*"
        r"((?:(?!R\s*\$).)+?)"
        r"(?:\s*-\s*(?:CPF|CNPJ)\s*(\d[\d./\-]{6,}\d))?"
        r"\s*via\s*(\d+)\s*transfer[eê]?ncia", t, re.I
    ):
        itens.append({"direcao": direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(2)), "doc": m.group(3),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(4)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato D (Nu Pagamentos / estilo "% + contraparte"): "R$valor em N transação(ões)) via/para
    # CPF/CNPJ doc (NOME)" — "via" = origem do crédito, "para" = destino do débito. "transa\w*" +
    # parêntese opcional cobre tanto "1 transação)" (singular, 1 parêntese) quanto "N transação(ões))"
    # (plural, 2 parênteses).
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s*em\s*(\d+)\s*transa\w*(?:\([^)]*\))?\)\s*(via|para)\s+(?:CPF|CNPJ)\s+"
        r"([\d./\-]+)\s*\(([^)]+)\)", t, re.I):
        itens.append({"direcao": "credito" if m.group(3).lower() == "via" else "debito",
                      "nome": _limpa_contraparte(m.group(5)), "doc": m.group(4),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato I ("DESTACANDO"): "[XX,XX%] R$ VALOR NOME - CPF/CNPJ[:] DOC (OBS)?" — usado por bancos
    # que detalham amostras de "A CREDITO/DEBITO TOTALIZOU R$X, DESTACANDO: ...". O percentual e os
    # dois-pontos depois de CPF/CNPJ são opcionais: as duas variantes convivem no mesmo RIF, às vezes
    # na mesma comunicação. A direção vem do cabeçalho "A CRÉDITO/A DÉBITO ..." mais próximo antes da
    # ocorrência — as duas seções se repetem quando a comunicação analisa mais de uma conta —, caindo
    # no corte global só quando nenhum cabeçalho precede.
    direcao_i = _rastreador_direcao(t, [(_MARCA_CREDITO, "credito"), (_MARCA_DEBITO, "debito")])
    for m in re.finditer(
        r"(?:\d{1,3}(?:,\d{1,2})?%|[:;,?])\s*R\$\s*(" + _VAL_RE + r")\s+(?:(?i:mesma\s+titularidade)\s*\(\s*)?"
        r"([A-ZÀ-Ú][^,\n]{1,70}?)\s*[-‐-―]\s*"
        r"(?:CPF|CNPJ)\s*:?\s*(" + _DOC_RE + r")\s*(?:\(([^)]*)\))?", t):
        itens.append({"direcao": direcao_i(m.start()) or direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(2)),
                      "doc": m.group(3), "valor": _valor_texto(m.group(1)), "qtde": 1,
                      "obs": _limpa_contraparte(m.group(4))})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato J: "R$ VALOR - NOME, CPF/CNPJ[:] DOC (detalhe)" — cobre tanto "Transferências
    # Instantâneas cujos principais remetentes/beneficiários foram" quanto "PRINCIPAIS CONTRAPARTES A
    # CRÉDITO/DÉBITO". Direção pelo marcador mais próximo antes da ocorrência (crédito e débito podem
    # se intercalar no mesmo texto). O "(detalhe)" pode vir colado (sem hífen) ao doc.
    # O "principais" antes de remetentes/beneficiários é opcional e às vezes vem DEPOIS ("cujos
    # beneficiários principais foram") — a mesma Caixa alterna as três formas, e sem casar todas a
    # comunicação inteira ficava sem direção (descartada) ou, pior, com a seção de débito herdando a
    # direção do último marcador de crédito. Os cabeçalhos de seção ORIGEM/DESTINO DOS RECURSOS
    # entram como rede de segurança, já que abrem os dois blocos.
    direcao_j = _rastreador_direcao(t, [
        (r"ORIGEM\s+DOS\s+RECURSOS", "credito"),
        (r"principais\s+cr[ée]ditos\s+foram", "credito"),
        (r"(?:principais\s+)?remetentes(?:\s+principais)?\s+foram", "credito"),
        (r"DESTINO\s+DOS\s+RECURSOS", "debito"),
        (r"principais\s+d[ée]bitos\s+foram", "debito"),
        (r"(?:principais\s+)?benefici[aá]rios(?:\s+principais)?\s+foram", "debito"),
        (r"PRINCIPAIS\s+CONTRAPARTES\s+A\s+CR[ÉE]DITO", "credito"),
        (r"PRINCIPAIS\s+CONTRAPARTES\s+A\s+D[ÉE]BITO", "debito"),
    ])
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s*-\s*((?:(?!R\$).)+?)\s*[.,]?\s*(?:CPF/CNPJ|CPF|CNPJ)\s*:?\s*"
        r"(" + _DOC_RE + r")\s*[-‐-―]?\s*\(([^)]*)\)", t, re.I):
        d = direcao_j(m.start())
        if d is None:
            continue
        qtdes = re.findall(r"(\d+)\s*PIXs?\s*(?:\w+\s+)?(?:Enviad|Recebid)", m.group(4), re.I)
        itens.append({"direcao": d, "nome": _limpa_contraparte(m.group(2)), "doc": m.group(3),
                      "valor": _valor_texto(m.group(1)),
                      "qtde": sum(int(q) for q in qtdes) if qtdes else 1, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato O (Banco Inter, "CRÉDITOS/DÉBITOS MAIS EXPRESSIVOS"): "- R$ VALOR - N PIX RECEBIDOS/
    # ENVIADOS PARA CONTAS NO <banco>, CUJA TITULAR É A EMPRESA <NOME>, INSCRITA NO CNPJ: <doc>". A
    # direção vem do próprio "RECEBIDOS"/"ENVIADOS", não da seção. O valor vem sem centavos e a
    # contraparte pode ser a conta do próprio investigado em outro banco ("DE MESMA TITULARIDADE").
    for m in re.finditer(
        r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*[-‐-―]\s*(\d+)\s*PIX\s+(RECEBIDO|ENVIADO)\w*\b"
        r"(?:(?!\bR\$).){0,400}?"
        r"(?:EMPRESA\s+(.+?)\s*,?\s*INSCRITA\s+NO\s+CNPJ\s*:?\s*(\d{11,14})|MESMA\s+TITULARIDADE)", t, re.I):
        itens.append({"direcao": "credito" if m.group(3).upper() == "RECEBIDO" else "debito",
                      "nome": _limpa_contraparte(m.group(4)) or "Mesma titularidade",
                      "doc": m.group(5), "valor": _valor_texto_sem_centavos(m.group(1)),
                      "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato L (narrativo "referente a N operações PIX"): "R$ VALOR, referente a N operações PIX,
    # recebida de/enviada para NOME, CPF/CNPJ DOC, de conta mantida..." — "recebida de" = crédito,
    # "enviada para" = débito. O valor aqui vem SEM centavos (ex.: "R$ 64.500", ponto só de milhar —
    # daí o parser dedicado). Contas de mesma titularidade (sem nome de terceiro) não casam — ok, não
    # há contraparte real a mostrar nesse caso.
    for m in re.finditer(
        r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*,?\s*referente\s+a\s+(\d+)\s*"
        r"(?:opera[çc][õo]es|opera[çc][ãa]o)\s+PIX\s*,?\s*"
        r"(recebida\s+de|enviada\s+para)\s+([^,\n]+?)\s*,\s*(?:CPF|CNPJ)\s*(" + _DOC_RE + r")", t, re.I):
        itens.append({"direcao": "credito" if "receb" in m.group(3).lower() else "debito",
                      "nome": _limpa_contraparte(m.group(4)), "doc": m.group(5),
                      "valor": _valor_texto_sem_centavos(m.group(1)), "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato N (tabela "em amostragem", colunas separadas por "/"): "NOME / DOC / R$ VALOR / PIX
    # C|D / QTDE / instituição /" — direção vem direto da coluna "PIX C" (crédito) / "PIX D" (débito),
    # sem precisar de corte por seção.
    for m in re.finditer(
        r"([^/\n]+?)\s*/\s*(\d{6,14})\s*/\s*R\$\s*(" + _VAL_RE + r")\s*/\s*PIX\s*(C|D)\s*/\s*(\d+)\s*/",
        t, re.I):
        # a 1ª linha da amostra vem colada ao fim do cabeçalho "...Instituições de Origem/Destino NOME"
        nome = re.sub(r"^(?:Origem|Destino)\s+", "", m.group(1), flags=re.I)
        itens.append({"direcao": "credito" if m.group(4).upper() == "C" else "debito",
                      "nome": _limpa_contraparte(nome), "doc": m.group(2),
                      "valor": _valor_texto(m.group(3)), "qtde": int(m.group(5)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato M ("Movimentação a Crédito/Débito" com composição por instituição): "R$ VALOR (XX%) –
    # NOME – CPF/CNPJ: DOC (Transações: N)". Direção pelo cabeçalho de seção mais próximo antes da
    # ocorrência. As linhas aninhadas "Composição por Instituição/Banco: ... / Ag: X / Conta: Y" não
    # têm o marcador CPF/CNPJ, então não casam (evita contar o mesmo valor em duplicidade).
    direcao_m = _rastreador_direcao(t, [
        (r"Movimenta[çc][ãa]o\s+a\s+Cr[ée]dito\s*:", "credito"),
        (r"Movimenta[çc][ãa]o\s+a\s+D[ée]bito\s*:", "debito"),
    ])
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s*\([\d,]+%\)\s*[-‐-―]\s*((?:(?!R\$).)+?)\s*[-‐-―]\s*"
        r"(?:CPF/CNPJ|CPF|CNPJ)\s*:?\s*(" + _DOC_RE + r")\s*\(Transa[çc][õo]es?:\s*(\d+)\)", t, re.I):
        d = direcao_m(m.start())
        if d is None:
            continue
        itens.append({"direcao": d, "nome": _limpa_contraparte(m.group(2)), "doc": m.group(3),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(4)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato K (carteira digital / Stark Bank — valores em formato US "93,000.00"): "NOME - CPF/CNPJ\
    # Chave TIPO \R$ VALOR ... Qtde Transação(ões): N." sob marcadores "PIX/SPB CREDITO"/"...DEBITO".
    # O "[^\\\n]" na captura do nome barra o cruzamento de um "\" (separador de campo deste formato),
    # o que impede o regex de "vazar" para o banco/agência anterior ao tentar casar a partir de um
    # ponto de início errado.
    direcao_k = _rastreador_direcao(t, [
        (r"PIX\s+CREDITO", "credito"), (r"SPB\s+CREDITO", "credito"),
        (r"PIX\s+DEBITO", "debito"), (r"SPB\s+DEBITO", "debito"),
    ])
    for m in re.finditer(
        r"([A-ZÀ-Úa-zà-ú][^\\\n]*?)\s*-\s*(" + _DOC_RE + r")[\\\s]*(?:Chave\s+\S+[\\\s]*)?R\$\s*"
        r"(\d[\d.,]*\d)[\s\S]*?Qtde\s*Transa\w*:\s*(\d+)", t, re.I):
        d = direcao_k(m.start())
        if d is None:
            continue
        itens.append({"direcao": d, "nome": _limpa_contraparte(m.group(1)), "doc": m.group(2),
                      "valor": _valor_flex(m.group(3)), "qtde": int(m.group(4)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato E (Sicoob / extrato narrativo por lançamento): "R$valor - origem: NOME, doc." (crédito)
    # ou "R$valor - destino: NOME, doc." (débito), com ou sem "Qtd. N totalizando" antes do valor.
    # O "-" às vezes vem como travessão/en dash (–/—) em vez de hífen ASCII, daí "[-‐-―]".
    # Best-effort: cada ocorrência isolada de "R$valor" imediatamente antes do marcador vira 1 item —
    # valores soltos entre marcadores (sem "- origem/destino:" logo em seguida) são ignorados, para
    # não atribuir a alguém um total que na verdade pertence a outro lançamento do mesmo parágrafo.
    # "depositante" (quem depositou em espécie) é crédito; "sacador" (quem sacou) é débito.
    for m in re.finditer(
        r"(?:Qtd\.\s*(\d+)\s*totalizando\s*)?R\$\s*(" + _VAL_RE + r")\s*[-‐-―]\s*" + _PAPEL_E + r"\s*:\s*"
        r"([^,]+?)\s*,\s*(" + _DOC_RE + r")", t, re.I):
        itens.append({"direcao": "credito" if m.group(3).lower() in ("origem", "depositante") else "debito",
                      "nome": _limpa_contraparte(m.group(4)), "doc": m.group(5),
                      "valor": _valor_texto(m.group(2)), "qtde": int(m.group(1)) if m.group(1) else 1,
                      "obs": None})
    # Variante do mesmo extrato Sicoob em que a contraparte vem DEPOIS do valor, sem "- origem/destino:":
    # "Qtd. N totalizando R$ VALOR NOME, DOC. Bc./ISPB ..." (seguida das datas de cada lançamento).
    # A direção sai do corte global, já que essa forma só aparece dentro dos blocos de crédito/débito.
    # O lookahead evita casar de novo o que a forma com papel explícito acima já pegou (com direção
    # melhor) — sem ele o mesmo lançamento entrava duas vezes, uma delas com o papel colado no nome.
    for m in re.finditer(
        r"Qtd\.\s*(\d+)\s*totalizando\s*R\$\s*(" + _VAL_RE + r")\s+"
        r"(?![-‐-―]\s*" + _PAPEL_E + r"\s*:)([^,\n]{2,80}?)\s*,\s*(\d{11,14})\b", t, re.I):
        itens.append({"direcao": direcao(m.start()), "nome": _limpa_contraparte(m.group(3)),
                      "doc": m.group(4), "valor": _valor_texto(m.group(2)), "qtde": int(m.group(1)),
                      "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato C (Banco do Brasil): "NOME - cpf/cnpj ( qualif )? [- MIDIA] - N lançamento(s) no total
    # de: R$valor". O nome pode trazer dígitos e "/" (MEIs vêm como "49.460.214 FULANO", empresas
    # como "... S/A"); a qualificação pode ter parênteses aninhados ("( CONSULTOR(A) NATURA )"), daí
    # o ".*?" ancorado no "- N lançamento(s)" que vem logo depois; e entre a qualificação e a
    # contagem o BB às vezes intercala um marcador solto ("- MIDIA -").
    for m in re.finditer(
        r"([A-ZÀ-Ú0-9][A-ZÀ-Ú0-9\s\./&]*?)\s*-\s*(" + _DOC_RE + r")\s*(?:\((.*?)\))?"
        r"(?:\s*-\s*[A-ZÀ-Ú]{2,})?\s*-\s*(\d+)\s*"
        r"lan[çc]amento\(s\)\s*no total de\s*:?\s*R?\$?\s*(" + _VAL_RE + r")", t, re.I):
        itens.append({"direcao": direcao(m.start()), "nome": _limpa_contraparte(m.group(1)),
                      "doc": m.group(2), "valor": _valor_texto(m.group(5)), "qtde": int(m.group(4)),
                      "obs": _limpa_contraparte(m.group(3))})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato R ("da contraparte NOME, CPF"): narrativa em bullets, valores normalmente inteiros
    # (sem centavos) — "- R$ 22800 - 6 transações (PIX) recebidos da contraparte Marcelo Caldeira
    # Maia, CPF 14216479740 - Picpay e Mercado Pago." A direção vem do particípio do próprio item
    # (recebido=crédito, enviado/pago=débito), não do corte global: crédito e débito se alternam
    # na mesma lista. O banco que fecha o item vira obs.
    for m in re.finditer(
        r"R\$\s*(\d+(?:\.\d{3})*(?:,\d{2})?)\s*-\s*(\d+)\s*transa[çc][õoã]?[eo]?s?\s*"
        r"(?:\([^)]{0,40}\)\s*)?"
        r"(recebidos?|enviados?|pagos?|debitados?)\s+(?:d[aeo]s?|para|a)\s+contraparte\s+"
        r"([^,]{3,80}?)\s*,\s*(?:CPF|CNPJ)\s*:?\s*(\d[\d./\-]{8,}\d)"
        r"(?:\s*-\s*([^.\n]{2,60}))?", t, re.I,
    ):
        itens.append({"direcao": "credito" if m.group(3).lower().startswith("receb") else "debito",
                      "nome": _limpa_contraparte(m.group(4)),
                      "doc": m.group(5), "valor": _valor_texto_sem_centavos(m.group(1)),
                      "qtde": int(m.group(2)), "obs": _limpa_contraparte(m.group(6))})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato S (bloco "INFORMACOES DE CONTRAPARTES"): cabeçalho declara as colunas
    # "Montante - Qtde De Transacoes - Nome - Documento" e cada linha segue
    # "R$ 4.995,00 - 5 - RONICLEIDSON SANTOS PEREIRA - 86526611540". A direção vem do subtítulo
    # da lista (Remetentes = crédito, Favorecidos/Destinatários = débito).
    direcao_s = _rastreador_direcao(t, [
        (r"Remetentes?\s*\(?\s*Principais", "credito"),
        (r"(?:Favorecidos?|Destinat[áa]rios?|Beneficiados?)\s*\(?\s*Principais", "debito"),
    ])
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s*-\s*(\d+)\s*-\s*"
        r"([A-ZÀ-Ú][A-ZÀ-Ú\s\.\&']{3,70}?)\s*-\s*(\d[\d./\-]{8,}\d)", t):
        itens.append({"direcao": direcao_s(m.start()) or direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(3)), "doc": m.group(4),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato T ("principais ordenantes/favorecidos", itens marcados com 'o'): "R$ 293.887,00 em 13
    # operacoes NATIVIDAD BEATRIZ SALINAS SALINAS CPF:24426302811". O documento vem colado no
    # rótulo ("CPF:24426302811") ou separado, e o nome pode ser razão social seguida de parênteses
    # com o ramo — que fica de fora do nome por causa do corte em CPF/CNPJ.
    direcao_t = _rastreador_direcao(t, [
        (r"principais\s+ordenantes", "credito"),
        (r"principais\s+(?:favorecidos|beneficiários|beneficiarios|destinat[áa]rios)", "debito"),
    ])
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s+em\s+(\d+)\s+opera[çc][õo]es\s+"
        r"([A-ZÀ-Ú][^\n]{3,70}?)\s*(?:CPF|CNPJ)\s*:?\s*(\d[\d./\-]{8,}\d)", t):
        itens.append({"direcao": direcao_t(m.start()) or direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(3)), "doc": m.group(4),
                      "valor": _valor_texto(m.group(1)), "qtde": int(m.group(2)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato U (Transferências Instantâneas detalhadas por conta): "R$ 102.789,00 YOHANNA NICOLLY
    # SILVA ARAUJO CPF 119.895.364-09 - ( 38 PIXs Crédito Recebidos - Conta ..., Agência 1, Banco
    # 260, 3 PIXs ... )". Diferente do formato J, aqui NÃO há vírgula entre nome e documento, e a
    # quantidade está dentro do parêntese, podendo vir repartida em várias contas — somadas.
    for m in re.finditer(
        r"R\$\s*\xa0?\s*(" + _VAL_RE + r")\s+([A-ZÀ-Ú][A-ZÀ-Ú\s\.\&']{3,70}?)\s*"
        r"(?:CPF|CNPJ)\s*:?\s*(\d[\d./\-]{8,}\d)\s*-\s*\(([^)]{5,400})\)", t):
        detalhe = m.group(4)
        qtde = sum(int(x) for x in re.findall(r"(\d+)\s*PIXs?\b", detalhe, re.I)) or 1
        eh_debito = bool(re.search(r"D[ée]bito|Enviad", detalhe, re.I))
        itens.append({"direcao": "debito" if eh_debito else "credito",
                      "nome": _limpa_contraparte(m.group(2)), "doc": m.group(3),
                      "valor": _valor_texto(m.group(1)), "qtde": qtde, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato V (Cora e afins): "R MARTINS COMERCIAL ONLINE LTDA - 55.240.920/0001-79, sendo 5
    # transações, totalizando R$ 350.000,00." O par nome-documento sozinho aparece o tempo todo no
    # texto (sócio, titular, empresa citada), então o que qualifica a linha como contraparte é o
    # "sendo N transações, totalizando R$" logo depois — é nele que a regex se ancora.
    direcao_v = _rastreador_direcao(t, [
        (r"entrada\s+de\s+recursos|seguintes\s+origens", "credito"),
        (r"sa[íi]da\s+de\s+recursos|seguintes\s+destinos", "debito"),
    ])
    # sem re.I de propósito: com ele o [A-ZÀ-Ú] inicial passaria a aceitar minúscula e o nome
    # começaria no meio de uma frase ("aixa capacidade financeira segundo o bureau de dados").
    # Nome tem de COMEÇAR em maiúscula; daí em diante aceita minúscula (razão social mista).
    for m in re.finditer(
        r"([A-ZÀ-Ú][A-Za-zÀ-ÿ0-9\s\.\&']{3,70}?)\s*-\s*(\d[\d./\-]{8,}\d)\s*,\s*[Ss]endo\s+(\d+)\s*"
        r"transa[çc][õo]es?\s*,\s*[Tt]otalizando\s+R\$\s*(" + _VAL_RE + r")", t):
        itens.append({"direcao": direcao_v(m.start()) or direcao(m.start()),
                      "nome": _limpa_contraparte(m.group(1)), "doc": m.group(2),
                      "valor": _valor_texto(m.group(4)), "qtde": int(m.group(3)), "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato W ("R$ X enviados por NOME, DOC"): a direção vem do particípio + preposição, que aqui
    # são inequívocos — quem ENVIA credita o titular, quem RECEBE o debita —, e não do corte global.
    for m in re.finditer(
        r"R\$\s*(" + _VAL_RE + r")\s+([Ee]nviad|[Rr]ecebid)[oa]s?\s+([Pp]or|[Pp]ara)\s+"
        r"([A-ZÀ-Ú][A-Za-zÀ-ÿ\s\.\&']{3,70}?)\s*,\s*(\d[\d./\-]{8,}\d)", t):
        enviou = m.group(2).lower().startswith("enviad") and m.group(3).lower() == "por"
        itens.append({"direcao": "credito" if enviou else "debito",
                      "nome": _limpa_contraparte(m.group(4)), "doc": m.group(5),
                      "valor": _valor_texto(m.group(1)), "qtde": 1, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato X ("referem-se a recebimento via PIX de NOME DOC"): narrativa curta em que o valor vem
    # antes, entre parênteses do percentual — "100% (R$ 4.500,00), referem-se a recebimento via PIX
    # de MAIRA JENNIFER DA SILVA 150838697-80". Sem vírgula entre nome e documento.
    for m in re.finditer(
        r"\(\s*R\$\s*(" + _VAL_RE + r")\s*\)\s*,?\s*[Rr]eferem-se\s+a\s+"
        r"([Rr]ecebimento|[Pp]agamento|[Ee]nvio|[Tt]ransfer[êe]ncia)[^.]{0,40}?\s+(?:de|para|a)\s+"
        r"([A-ZÀ-Ú][A-Za-zÀ-ÿ\s\.\&']{3,70}?)\s+(\d[\d./\-]{8,}\d)", t):
        recebeu = m.group(2).lower().startswith("receb")
        itens.append({"direcao": "credito" if recebeu else "debito",
                      "nome": _limpa_contraparte(m.group(3)), "doc": m.group(4),
                      "valor": _valor_texto(m.group(1)), "qtde": 1, "obs": None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato Q (tabela com cabeçalho explícito, Itaú entre outros): as colunas vêm em
    #   "Valor CPF/CNPJ Nome/Razao social Qte Instituicoes de origem|destino"
    # ou seja, documento e quantidade TROCADOS em relação ao Formato A. Sem este bloco o texto caía
    # no Formato A e saía toda errada: a Qte grudava no nome ("FULANO 2"), o código ISPB da
    # instituição virava o CPF, o CPF real virava a contagem (e o guard de qtde o zerava para 1) e
    # o resto da tabela era engolido em obs — só a primeira contraparte sobrevivia, torta.
    #
    # Ancorado no cabeçalho literal: só entra em texto que traz essa marca, e por isso fica logo
    # antes do Formato A (fallback), sem tirar match de nenhum formato anterior. A direção vem do
    # próprio cabeçalho ("de origem" = crédito, "de destino" = débito), que é mais confiável que o
    # corte global do texto. A seção termina no próximo cabeçalho de tabela — inclusive o da tabela
    # de espécie ("Valor CPF/CNPJ Descricao Ag. Origem..."), cujas linhas não têm documento e
    # portanto nem casariam aqui.
    for m_sec in re.finditer(
        r"Valor\s+CPF\s*/\s*CNPJ\s+Nome\s*/\s*Raz[aã]o\s+social\s+Qte\s+"
        r"Institui[cç][oõ]es\s+de\s+(origem|destino)(.*?)(?=Valor\s+CPF\s*/\s*CNPJ|$)",
        t, re.I | re.DOTALL,
    ):
        direcao_q = "credito" if m_sec.group(1).lower() == "origem" else "debito"
        for m in re.finditer(
            r"R\$\s*(" + _VAL_RE + r")\s+"      # valor
            r"(\d{11}|\d{14})\s+"                # CPF ou CNPJ (sem pontuação nesta tabela)
            r"([^\d]+?)\s+"                      # nome (a coluna não tem dígitos)
            r"(\d{1,4})"                         # Qte
            r"(?:\s+(\d{5,9}))?"                 # ISPB da instituição, quando informado
            r"(?=\s*R\$|\s*$)",
            m_sec.group(2),
        ):
            itens.append({"direcao": direcao_q, "nome": _limpa_contraparte(m.group(3)),
                          "doc": m.group(2), "valor": _valor_texto(m.group(1)),
                          "qtde": int(m.group(4)),
                          "obs": f"instituição {m.group(5)}" if m.group(5) else None})
    if itens:
        return _completar_contrapartes(itens, t)

    # Formato A (Bradesco / clássico COAF): tabela "VALOR R$ QTDE NOME CPF/CNPJ BANCO"
    for m in re.finditer(
        r"(" + _VAL_RE + r")\s+(\d+)\s+(.+?)\s+(" + _DOC_RE + r")\s+(.+?)"
        r"(?=\s+" + _VAL_RE + r"\s+\d+\s|\s+Notas:|\s+Os d[ée]bitos|\s+Demonstramos|$)", t):
        banco = re.split(r"\bComunicado em", m.group(5), maxsplit=1)[0]
        itens.append({"direcao": direcao(m.start()), "nome": _limpa_contraparte(m.group(3)),
                      "doc": m.group(4), "valor": _valor_texto(m.group(1)), "qtde": int(m.group(2)),
                      "obs": _limpa_contraparte(banco)})
    return _completar_contrapartes(itens, t)


def _ordena_contrapartes(itens: list[dict]) -> list[dict]:
    itens.sort(key=lambda x: (x["direcao"] != "credito", -x["valor"]))
    return itens


# ---------------------------------------------------------------------------
# Importação
# ---------------------------------------------------------------------------

def _gravar_contrapartes(cur, id_comunicacao_rif: int, id_rif: int, info: str | None,
                         cd_segmento: str | None, vl_campo_b, vl_campo_c) -> int:
    """Estrutura as contrapartes do texto livre de uma comunicação e as grava, fechando a diferença
    com uma linha DESCONHECIDO (sem nome/documento) por direção. Devolve quantas linhas gravou."""
    return _gravar_contrapartes_ja_extraidas(
        cur, id_comunicacao_rif, id_rif, extrair_contrapartes(info), info, cd_segmento, vl_campo_b, vl_campo_c)


def _gravar_contrapartes_ja_extraidas(cur, id_comunicacao_rif: int, id_rif: int, cps: list[dict],
                                      texto: str | None, cd_segmento: str | None, vl_campo_b, vl_campo_c) -> int:
    """Grava uma lista de contrapartes JÁ estruturadas (vindas do parser REGEX ou da IA), fechando
    a diferença com uma linha DESCONHECIDO (sem nome/documento) por direção. Devolve quantas linhas
    gravou.

    Os campos B (crédito) e C (débito) do CSV são o total da movimentação; o texto detalha só as
    principais contrapartes, então o que sobra vira o DESCONHECIDO — é o que impede o grafo de
    sugerir que a soma dos nomes citados é a movimentação inteira.

    `texto` é o texto da comunicação (só usado no fallback de totais quando o segmento não é
    41/42 e os campos B/C não servem)."""
    for ordem, cp in enumerate(cps):
        # Valida qtde: se for muito grande (provavelmente erro de parsing de notação científica), limita a 1
        qtde = cp["qtde"]
        if not isinstance(qtde, int) or qtde < 1 or qtde > 999999999:
            qtde = 1
        # Trunca campos VARCHAR(3000) para evitar "value too long" quando texto é muito grande
        nome = (cp["nome"] or "")[:3000] if cp["nome"] else None
        doc = (cp["doc"] or "")[:3000] if cp["doc"] else None
        obs = (cp["obs"] or "")[:3000] if cp["obs"] else None
        cur.execute(
            """
            INSERT INTO investigacao.rif_tb05_contrapartes
                (id_comunicacao_rif, id_rif, cd_direcao, no_contraparte, nu_cpf_cnpj,
                 qt_lancamentos, vl_total, tx_obs, nu_ordem)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (id_comunicacao_rif, id_rif, cp["direcao"], nome, doc,
             qtde, cp["valor"], obs, ordem),
        )

    vls: dict[str, float | None] = {}
    if cd_segmento in ("41", "42"):
        vls["credito"] = vl_campo_b
        vls["debito"] = vl_campo_c
    if not any(v is not None for v in vls.values()):
        # fallback: tenta extrair totais do texto
        vls = _extrair_totais_texto(_remonta_quebra_pagina(texto or ""))

    nu_ordem = len(cps)
    if vls:
        soma: dict[str, float] = {"credito": 0.0, "debito": 0.0}
        for cp in cps:
            soma[cp["direcao"]] += cp["valor"]
        for direcao in ("credito", "debito"):
            total = vls.get(direcao)
            if total is None:
                continue
            resto = float(total) - soma[direcao]
            if resto > 0.01:
                cur.execute(
                    """
                    INSERT INTO investigacao.rif_tb05_contrapartes
                        (id_comunicacao_rif, id_rif, cd_direcao, no_contraparte,
                         nu_cpf_cnpj, qt_lancamentos, vl_total, tx_obs, nu_ordem)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (id_comunicacao_rif, id_rif, direcao, None, None,
                     1, round(resto, 2), None, nu_ordem),
                )
                nu_ordem += 1
    return nu_ordem


def reprocessar_contrapartes_rif(id_rif: int) -> dict:
    """Reextrai as contrapartes de um RIF já importado, a partir do texto guardado no banco.

    Serve para aplicar melhorias no parser (extrair_contrapartes) sem reimportar a pasta — o texto
    original de cada comunicação fica em rif_tb02_comunicacoes.tx_informacoes_adicionais.

    RIF_TB05 é apagada e regravada, e o ID é IDENTITY: as linhas novas ganham IDs novos. Quem
    aponta para elas por (TP_ORIGEM='rif_contraparte', ID_ORIGEM) — evidência de pessoa canônica,
    entidade, detecção de padrão, marcação de relatório — não tem FK que segure, então ficaria
    apontando para ID que não existe mais. Essas anotações órfãs somem aqui; para reconstruí-las
    apontando para as contrapartes novas, rode identificar_pessoas_inquerito (a tela de
    investigação faz isso no botão de identificação de pessoas)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_comunicacao_rif, tx_informacoes_adicionais, cd_segmento, vl_campo_b, vl_campo_c
                FROM investigacao.rif_tb02_comunicacoes
                WHERE id_rif = %s
                ORDER BY nu_indexador
                """,
                (id_rif,),
            )
            comunicacoes = cur.fetchall()
            cur.execute("DELETE FROM investigacao.rif_tb05_contrapartes WHERE id_rif = %s", (id_rif,))
            total = 0
            for id_comunicacao_rif, info, cd_segmento, vl_b, vl_c in comunicacoes:
                total += _gravar_contrapartes(cur, id_comunicacao_rif, id_rif, info, cd_segmento, vl_b, vl_c)
            orfas = limpar_anotacoes_orfas(cur, "rif_contraparte")
        conn.commit()
    return {"id_rif": id_rif, "qt_comunicacoes": len(comunicacoes), "qt_contrapartes": total,
            "anotacoes_orfas_removidas": orfas}


# ---------------------------------------------------------------------------
# Reanálise de UMA comunicação com IA (substitui o parser REGEX só nela)
# ---------------------------------------------------------------------------
# O botão "Reanalisar com IA" de rif.html manda só o texto da comunicação para a IA e regrava as
# contrapartes dela a partir do que a IA identificou — útil quando o texto não segue nenhum dos
# formatos do parser REGEX (ver extrair_contrapartes) e as tabelas de Entradas/Saídas saem vazias
# ou incompletas. As demais comunicações do RIF não são tocadas.

_LIMITE_ANALISE_RIF_CARACTERES = 50_000   # cabe folgado na janela de contexto dos provedores
_TETO_SAIDA_ANALISE_RIF = 16384           # 2ª tentativa com teto maior (ver documentos_utils)

SISTEMA_PROMPT_ANALISE_RIF = (
    "Você é um analista de inteligência financeira de uma força-tarefa policial. Você recebeu o "
    "texto de uma comunicação bancária do SISCOAF (RIF). Sua tarefa é identificar TODAS as "
    "contrapartes da movimentação descrita: as entradas (quem creditou — remetentes, depositantes, "
    "ordenantes, origens dos recursos) e as saídas (quem foi debitado — favorecidos, destinatários, "
    "beneficiários, destinos dos recursos).\n"
    "Devolva SOMENTE um objeto JSON válido — sem markdown, sem texto antes ou depois — neste "
    "formato exato:\n"
    "{\n"
    '  "contrapartes": [\n'
    '    {"direcao": "credito", "nome": "nome da pessoa/empresa como consta no texto", '
    '"doc": "CPF/CNPJ citado, ou vazio", "qtde": 1, "valor": 12345.67, "obs": "observação curta ou vazio"}\n'
    '  ]\n'
    "}\n"
    "Regras: direcao é \"credito\" para quem ENVIOU dinheiro para a conta do titular e \"debito\" "
    "para quem RECEBEU. valor é número decimal em reais (nunca string). qtde é a quantidade de "
    "lançamentos/transações citada (inteiro, no mínimo 1; 1 quando o texto não citar). Conta do "
    "próprio titular (\"mesma titularidade\") entra com nome \"Mesma titularidade\" e doc vazio. "
    "Se o texto detalhar a mesma pessoa em mais de uma linha na mesma direção, some os valores e as "
    "quantidades numa entrada só. NUNCA invente nome, documento ou valor que não esteja no texto. "
    "Linhas que só tragam datas, percentuais ou totais de seção não são contrapartes. Se o texto "
    "não citar nenhuma contraparte, devolva a lista vazia. Inclua no máximo 200 contrapartes."
)


def _extrair_json_contrapartes(resposta: str) -> dict:
    """Extrai o objeto JSON da resposta da IA, tolerando cercas ```json e texto em volta."""
    resposta = (resposta or "").strip()
    inicio = resposta.find("{")
    fim = resposta.rfind("}")
    if inicio == -1 or fim == -1 or fim <= inicio:
        raise ValueError("A IA não devolveu um JSON válido na reanálise da comunicação.")
    try:
        dados = json.loads(resposta[inicio:fim + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na resposta da reanálise da comunicação: {exc}")
    if not isinstance(dados, dict):
        raise ValueError("A resposta da reanálise da comunicação não é um objeto.")
    return dados


def analisar_contrapartes_comunicacao_ia(id_comunicacao_rif: int, id_rif: int,
                                         id_usuario: int | None = None) -> dict:
    """Reanalisa o texto de UMA comunicação com IA e regrava as contrapartes identificadas.

    A IA recebe só o texto normalizado (o mesmo que a tela exibe em "Texto original da análise") e
    devolve a lista estruturada de contrapartes, que substitui as linhas de RIF_TB05 daquela
    comunicação — a diferença para os totais (campos B/C) continua virando DESCONHECIDO, como no
    parser REGEX. As identificações manuais (RIF_TB06) não são tocadas. Levanta ValueError quando
    a comunicação não existe/não tem texto, e ErroProvedorIA quando a IA não está configurada ou
    falhou. Devolve a quantidade de linhas gravadas."""
    from funcoes.ia_auditoria import registrar_auditoria
    from funcoes.ia_provedores import AuditoriaIA, enviar_mensagem

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tx_informacoes_adicionais, cd_segmento, vl_campo_b, vl_campo_c
                FROM investigacao.rif_tb02_comunicacoes
                WHERE id_comunicacao_rif = %s AND id_rif = %s
                """,
                (id_comunicacao_rif, id_rif),
            )
            linha = cur.fetchone()
    if linha is None:
        raise ValueError("Comunicação não encontrada neste RIF.")
    info, cd_segmento, vl_b, vl_c = linha
    texto = normalizar_texto_analise(info)
    if not texto:
        raise ValueError("Esta comunicação não tem texto de análise.")

    mensagem_usuario = f"Texto da comunicação do RIF:\n\n{texto[:_LIMITE_ANALISE_RIF_CARACTERES]}"

    auditoria = AuditoriaIA()
    inicio_turno = time.monotonic()
    resposta, modelo = enviar_mensagem(
        [{"role": "user", "content": mensagem_usuario}],
        sistema=SISTEMA_PROMPT_ANALISE_RIF,
        auditoria=auditoria,
        id_usuario=id_usuario,
    )
    try:
        dados = _extrair_json_contrapartes(resposta)
    except ValueError:
        # a resposta pode ter vindo cortada pelo teto de saída do provedor (JSON pela metade) —
        # tenta uma vez com teto maior antes de desistir (ver documentos_utils)
        resposta, modelo = enviar_mensagem(
            [{"role": "user", "content": mensagem_usuario}],
            sistema=SISTEMA_PROMPT_ANALISE_RIF,
            auditoria=auditoria,
            id_usuario=id_usuario,
            max_tokens_saida=_TETO_SAIDA_ANALISE_RIF,
        )
        dados = _extrair_json_contrapartes(resposta)

    cps: list[dict] = []
    for item in dados.get("contrapartes") or []:
        if not isinstance(item, dict):
            continue
        direcao = str(item.get("direcao") or "").strip().lower()
        if direcao not in ("credito", "debito"):
            continue
        try:
            valor = round(float(item.get("valor")), 2)
        except (TypeError, ValueError):
            continue
        if valor <= 0:
            continue
        try:
            qtde = int(item.get("qtde")) if item.get("qtde") is not None else 1
        except (TypeError, ValueError):
            qtde = 1
        if qtde < 1:
            qtde = 1
        if qtde > 999999999:
            qtde = 1
        nome = _limpa_contraparte(str(item.get("nome") or "")) if item.get("nome") else None
        doc = _normaliza_doc(str(item.get("doc") or "")) if item.get("doc") else None
        obs = _limpa_contraparte(str(item.get("obs") or "")) if item.get("obs") else None
        if not nome and not doc:
            continue  # sem nome E sem documento não é uma contraparte identificada
        cps.append({"direcao": direcao, "nome": nome, "doc": doc, "valor": valor,
                    "qtde": qtde, "obs": obs})
    cps = _completar_contrapartes(cps, texto)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.rif_tb05_contrapartes WHERE id_comunicacao_rif = %s",
                (id_comunicacao_rif,),
            )
            total = _gravar_contrapartes_ja_extraidas(
                cur, id_comunicacao_rif, id_rif, cps, texto, cd_segmento, vl_b, vl_c)
            orfas = limpar_anotacoes_orfas(cur, "rif_contraparte")
        conn.commit()

    registrar_auditoria(
        "rif_analise_ia", mensagem_usuario, SISTEMA_PROMPT_ANALISE_RIF, auditoria, modelo,
        int((time.monotonic() - inicio_turno) * 1000), resposta=resposta,
        id_inquerito=obter_id_inquerito_de_rif(id_rif), id_usuario=id_usuario,
    )
    return {"ok": True, "id_comunicacao_rif": id_comunicacao_rif, "qt_contrapartes": total,
            "anotacoes_orfas_removidas": orfas}


def _validar_pasta(no_pasta: str, subpasta: str | None = None) -> Path:
    """Resolve a subpasta do RIF em data/, barrando path traversal. Levanta ValueError/FileNotFoundError.

    Delegacia com NÓ (docs/no_descentralizado.md): o seletor listou a pasta DO NÓ — se ela não
    está no central, espelha do nó antes de recusar (o pacote RIF é uma pasta plana de XML/PDF,
    pequena; o parse roda aqui mesmo)."""
    no_pasta = (no_pasta or "").strip()
    if not no_pasta or "/" in no_pasta or "\\" in no_pasta or no_pasta in (".", ".."):
        raise ValueError("Pasta inválida.")
    caminho = _subpasta_rif(subpasta) / no_pasta
    if not caminho.is_dir():
        from funcoes.documentos_utils import espelhar_pasta_do_no

        if not espelhar_pasta_do_no(caminho, subpasta):
            raise FileNotFoundError(no_pasta)
    if not _eh_pasta_rif(caminho):
        raise ValueError("A pasta não é de RIF (o nome precisa conter 'RIF').")
    return caminho


def _organizar_pasta_rif(id_rif: int, pasta: Path, numero_inquerito: str, nu_rif: str | None,
                         delegacia: str | None = None) -> None:
    """Move a pasta do RIF para data/<delegacia>/<inquérito>/RIF/ e grava o caminho novo
    (relativo a data/) em NO_PASTA. Best-effort: falha aqui não desfaz a importação.

    Delegacia com NÓ (docs/no_descentralizado.md): a prova mora na estação — quem organiza é o
    próprio nó (POST /no/organizar, mesmo organizador, disco certo); o central apaga o ESPELHO
    que baixou para parsear, para não deixar a cópia da prova onde ela não manda. Nó indisponível:
    melhor deixar o espelho no lugar de onde o usuário vê do que movê-lo para o inquérito do
    central — a reimportação do dia seguinte readquire do nó."""
    try:
        novo_nome = f"RIF_{nu_rif}" if nu_rif else None
        from funcoes import no_ponte, no_utils

        no = no_utils.servidor_ativo_para(delegacia)
        if no is not None:
            try:
                relativo = no_ponte.organizar(no, pasta.relative_to(PASTA_DADOS).as_posix(),
                                              numero_inquerito, CATEGORIA_RIF, novo_nome=novo_nome)
            except (no_ponte.NoIndisponivel, FileNotFoundError) as exc:
                logger.warning("Falha ao organizar o RIF %s no nó (%s); espelho fica no central", id_rif, exc)
                return
            if not relativo:
                return
        else:
            relativo = organizar(pasta, numero_inquerito, CATEGORIA_RIF, novo_nome=novo_nome)
            if not relativo:
                return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.rif_tb01_rifs SET no_pasta = %s WHERE id_rif = %s",
                    (relativo, id_rif),
                )
            conn.commit()
        if no is not None:
            # a prova foi arrumada DENTRO do nó; o espelho que baixei para parsear não tem o que
            # fazer no central — sobrá-lo seria exatamente o bug que isto corrige (cópia no principal)
            shutil.rmtree(pasta, ignore_errors=True)
    except Exception:
        logger.exception("Falha ao organizar a pasta do RIF %s (best-effort)", id_rif)


def importar_rif(no_pasta: str, numero_inquerito: str, id_usuario: int | None, subpasta: str | None = None, delegacia: str | None = None, in_caso_inteligencia: bool = False) -> dict:
    """Importa um RIF (pasta em data/) para um inquérito: parseia os 3 CSVs em tabelas próprias e
    anexa o PDF como documento do inquérito (para a IA). Cria o inquérito se ainda não existir."""
    pasta = _validar_pasta(no_pasta, subpasta)

    csv_com = _achar_csv(pasta, "_Comunicacoes")
    csv_env = _achar_csv(pasta, "_Envolvidos")
    csv_oco = _achar_csv(pasta, "_Ocorrencias")
    pdf = _achar_pdf(pasta)
    nu_rif = _numero_rif(pasta, csv_com)

    # só linhas de dados reais (Indexador inteiro puro) — descarta legenda/rodapé no fim dos CSVs
    def _dados(caminho):
        return [l for l in _ler_csv(caminho) if _indexador(l.get("Indexador")) is not None]

    comunicacoes = _dados(csv_com) if csv_com else []
    envolvidos = _dados(csv_env) if csv_env else []
    ocorrencias = _dados(csv_oco) if csv_oco else []

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                     in_caso_inteligencia)
            if in_caso_inteligencia and not (numero_inquerito or "").strip():
                cur.execute(
                    "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                    (id_inquerito,),
                )
                numero_inquerito = cur.fetchone()[0]

            # PDF -> documento do inquérito (mesma tabela do "Extrair de PDF")
            id_documento_pdf = None
            erro_pdf = None
            if pdf is not None:
                texto, erro_pdf = extrair_texto_documento(pdf)
                cur.execute(
                    """
                    INSERT INTO investigacao.inq_tb04_documentos
                        (id_inquerito, no_arquivo, tp_arquivo, tx_conteudo, qt_caracteres, tx_erro, id_usuario)
                    VALUES (%s, %s, 'pdf', %s, %s, %s, %s)
                    RETURNING id_documento
                    """,
                    (id_inquerito, pdf.name, texto, len(texto) if texto else None, erro_pdf, id_usuario),
                )
                id_documento_pdf = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO investigacao.rif_tb01_rifs
                    (id_inquerito, nu_rif, no_pasta, id_documento_pdf,
                     qt_comunicacoes, qt_envolvidos, qt_ocorrencias, id_usuario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_rif
                """,
                (id_inquerito, nu_rif, pasta.name, id_documento_pdf,
                 len(comunicacoes), len(envolvidos), len(ocorrencias), id_usuario),
            )
            id_rif = cur.fetchone()[0]

            for tipo, caminho_arquivo in (
                ("comunicacoes", csv_com), ("envolvidos", csv_env),
                ("ocorrencias", csv_oco), ("pdf", pdf),
            ):
                if caminho_arquivo is None:
                    continue
                hashes = calcular_hashes_arquivo(caminho_arquivo)
                cur.execute(
                    """
                    INSERT INTO investigacao.rif_tb06_arquivos
                        (id_rif, no_arquivo, tp_arquivo, qt_bytes, co_hash_md5, co_hash_sha1, co_hash_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (id_rif, caminho_arquivo.name, tipo, caminho_arquivo.stat().st_size,
                     hashes["md5"], hashes["sha1"], hashes["sha256"]),
                )

            for c in comunicacoes:
                info = _txt(c.get("informacoesAdicionais"))
                cur.execute(
                    """
                    INSERT INTO investigacao.rif_tb02_comunicacoes
                        (id_rif, nu_indexador, id_comunicacao, nu_ocorrencia_bc, dt_recebimento,
                         dt_operacao, dt_fim_fato, nu_cpf_cnpj_comunicante, no_comunicante,
                         no_cidade_agencia, sg_uf_agencia, no_agencia, nu_agencia,
                         tx_informacoes_adicionais, vl_campo_a, vl_campo_b, vl_campo_c, vl_campo_d,
                         vl_campo_e, cd_segmento)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id_comunicacao_rif
                    """,
                    (
                        id_rif, _indexador(c.get("Indexador")), _txt(c.get("idComunicacao")),
                        _txt(c.get("NumeroOcorrenciaBC")), _datahora_br(c.get("Data_do_Recebimento")),
                        _data_br(c.get("Data_da_operacao")), _data_br(c.get("DataFimFato")),
                        _txt(c.get("cpfCnpjComunicante")), _txt(c.get("nomeComunicante")),
                        _txt(c.get("CidadeAgencia")), _txt(c.get("UFAgencia")), _txt(c.get("NomeAgencia")),
                        _txt(c.get("NumeroAgencia")), info,
                        _valor_br(c.get("CampoA")), _valor_br(c.get("CampoB")), _valor_br(c.get("CampoC")),
                        _valor_br(c.get("CampoD")), _valor_br(c.get("CampoE")), _txt(c.get("CodigoSegmento")),
                    ),
                )
                id_comunicacao_rif = cur.fetchone()[0]

                _gravar_contrapartes(cur, id_comunicacao_rif, id_rif, info,
                                     _txt(c.get("CodigoSegmento")),
                                     _valor_br(c.get("CampoB")), _valor_br(c.get("CampoC")))

            for e in envolvidos:
                cur.execute(
                    """
                    INSERT INTO investigacao.rif_tb03_envolvidos
                        (id_rif, nu_indexador, nu_cpf_cnpj, no_envolvido, tp_envolvido, nu_agencia,
                         nu_conta, dt_abertura_conta, dt_atualizacao_conta, tx_pep, tx_pessoa_obrigada,
                         tx_servidor)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        id_rif, _indexador(e.get("Indexador")), _txt(e.get("cpfCnpjEnvolvido")),
                        _txt(e.get("nomeEnvolvido")), _txt(e.get("tipoEnvolvido")),
                        _txt(e.get("agenciaEnvolvido")), _txt(e.get("contaEnvolvido")),
                        _data_br(e.get("DataAberturaConta")), _data_br(e.get("DataAtualizacaoConta")),
                        _txt(e.get("bitPepCitado")), _txt(e.get("bitPessoaObrigadaCitado")),
                        _txt(e.get("intServidorCitado")),
                    ),
                )

            for o in ocorrencias:
                cur.execute(
                    """
                    INSERT INTO investigacao.rif_tb04_ocorrencias
                        (id_rif, nu_indexador, id_ocorrencia, tx_ocorrencia)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (id_rif, _indexador(o.get("Indexador")), _txt(o.get("idOcorrencia")), _txt(o.get("Ocorrencia"))),
                )
        conn.commit()

    # importação concluída: a pasta do RIF sai da pasta de entrega da delegacia e vai para
    # data/<delegacia>/<inquérito>/RIF/, com o nome padronizado RIF_<número> quando o número foi
    # reconhecido (as pastas do SISCOAF chegam como "RIF 128302", "RIF_128302", "RIF 128302 - x"…)
    _organizar_pasta_rif(id_rif, pasta, numero_inquerito, nu_rif, delegacia or subpasta)

    # Envolvido e contraparte do RIF são gente na Mesa: identifica o inquérito numa thread daemon
    # (a requisição não pode esperar a varredura) para o RIF entrar no grafo sem depender do botão
    # "Identificar pessoas" — o mesmo que a ingestão do UFDR já faz no fim da extração.
    from funcoes.identificacao_pessoas import agendar_identificacao_pos_importacao
    agendar_identificacao_pos_importacao(id_inquerito, f"RIF {nu_rif}")

    return {
        "id_rif": id_rif,
        "id_inquerito": id_inquerito,
        "nu_rif": nu_rif,
        "qt_comunicacoes": len(comunicacoes),
        "qt_envolvidos": len(envolvidos),
        "qt_ocorrencias": len(ocorrencias),
        "tem_pdf": pdf is not None,
        "tx_erro_pdf": erro_pdf if pdf is not None else None,
    }


# ---------------------------------------------------------------------------
# Importação NO NÓ (no_importar.py): parse local + emissão dos registros rif_*
#
# O nó não tem banco: parseia a pasta da estação com os MESMOS helpers do importador local e
# emite rif_meta/rif_arquivos/rif_comunicacoes/rif_envolvidos/rif_ocorrencias pelo canal — o
# central reproduz com os mesmos INSERTs (ver reproduzir_rif) e fecha a linha-pai da ponte.
# ---------------------------------------------------------------------------

_LOTE_RIF_LINHAS = 400  # linhas por registro (o texto da análise é grande; o lote-8MB cuida do resto)


def importar_rif_no(canal, no_pasta: str, opcoes: dict) -> dict:
    """RIF processado NA ESTAÇÃO (funcoes/no_importar.py). `opcoes` traz o que o parse local
    conhece de sempre: delegacia_pasta (subpasta), numero_inquerito (destino do organize).
    Devolve o resumo (também vai no 'finalizar' do canal, que fecha a ponte no central)."""
    pasta = _validar_pasta(no_pasta, opcoes.get("delegacia_pasta") or None)

    csv_com = _achar_csv(pasta, "_Comunicacoes")
    csv_env = _achar_csv(pasta, "_Envolvidos")
    csv_oco = _achar_csv(pasta, "_Ocorrencias")
    pdf = _achar_pdf(pasta)
    nu_rif = _numero_rif(pasta, csv_com)

    def _dados(caminho):
        return [l for l in _ler_csv(caminho) if _indexador(l.get("Indexador")) is not None]

    comunicacoes = _dados(csv_com) if csv_com else []
    envolvidos = _dados(csv_env) if csv_env else []
    ocorrencias = _dados(csv_oco) if csv_oco else []
    if not (comunicacoes or envolvidos or ocorrencias):
        raise ValueError("A pasta não tem nenhum CSV de RIF com dados — confira o conteúdo.")

    # PDF: o TEXTO sai daqui (o arquivo mora na estação; o central só grava o texto, como
    # sempre — os INSERTs/IA rodam lá)
    pdf_nome = pdf_texto = pdf_erro = None
    if pdf is not None:
        from funcoes.documentos_utils import extrair_texto_documento

        pdf_texto, pdf_erro = extrair_texto_documento(pdf)
        pdf_nome = pdf.name

    canal.emitir("rif_meta", (nu_rif, no_pasta, len(comunicacoes), len(envolvidos),
                              len(ocorrencias), pdf_nome, pdf_texto, pdf_erro))

    arquivos = []
    for tipo, caminho_arquivo in (("comunicacoes", csv_com), ("envolvidos", csv_env),
                                  ("ocorrencias", csv_oco), ("pdf", pdf)):
        if caminho_arquivo is None:
            continue
        hashes = calcular_hashes_arquivo(caminho_arquivo)
        arquivos.append([caminho_arquivo.name, tipo, caminho_arquivo.stat().st_size,
                         hashes["md5"], hashes["sha1"], hashes["sha256"]])
    if arquivos:
        canal.emitir("rif_arquivos", (arquivos,))

    for i in range(0, len(comunicacoes), _LOTE_RIF_LINHAS):
        if canal.cancelar:
            raise ValueError("Importação cancelada no central.")
        linhas = []
        for c in comunicacoes[i:i + _LOTE_RIF_LINHAS]:
            # MESMA ordem de colunas do INSERT do importador local (reproduzir_rif)
            linhas.append([
                _indexador(c.get("Indexador")), _txt(c.get("idComunicacao")),
                _txt(c.get("NumeroOcorrenciaBC")), _datahora_br(c.get("Data_do_Recebimento")),
                _data_br(c.get("Data_da_operacao")), _data_br(c.get("DataFimFato")),
                _txt(c.get("cpfCnpjComunicante")), _txt(c.get("nomeComunicante")),
                _txt(c.get("CidadeAgencia")), _txt(c.get("UFAgencia")), _txt(c.get("NomeAgencia")),
                _txt(c.get("NumeroAgencia")), _txt(c.get("informacoesAdicionais")),
                _valor_br(c.get("CampoA")), _valor_br(c.get("CampoB")), _valor_br(c.get("CampoC")),
                _valor_br(c.get("CampoD")), _valor_br(c.get("CampoE")),
                _txt(c.get("CodigoSegmento")),
            ])
        canal.emitir("rif_comunicacoes", (linhas,))
        canal.commit()

    for i in range(0, len(envolvidos), _LOTE_RIF_LINHAS):
        linhas = [[
            _indexador(e.get("Indexador")), _txt(e.get("cpfCnpjEnvolvido")),
            _txt(e.get("nomeEnvolvido")), _txt(e.get("tipoEnvolvido")),
            _txt(e.get("agenciaEnvolvido")), _txt(e.get("contaEnvolvido")),
            _data_br(e.get("DataAberturaConta")), _data_br(e.get("DataAtualizacaoConta")),
            _txt(e.get("bitPepCitado")), _txt(e.get("bitPessoaObrigadaCitado")),
            _txt(e.get("intServidorCitado")),
        ] for e in envolvidos[i:i + _LOTE_RIF_LINHAS]]
        canal.emitir("rif_envolvidos", (linhas,))
        canal.commit()

    for i in range(0, len(ocorrencias), _LOTE_RIF_LINHAS):
        linhas = [[
            _indexador(o.get("Indexador")), _txt(o.get("idOcorrencia")), _txt(o.get("Ocorrencia")),
        ] for o in ocorrencias[i:i + _LOTE_RIF_LINHAS]]
        canal.emitir("rif_ocorrencias", (linhas,))
        canal.commit()

    # organiza a pasta DENTRO da estação (data/<delegacia>/<inquérito>/RIF/RIF_<n>) — é só
    # arrumação local: o que o central guarda em NO_PASTA é o caminho de origem, e nada lê o
    # binário depois (o PDF virou texto). Melhor esforço, como no caminho local.
    try:
        numero = opcoes.get("numero_inquerito")
        if numero:
            organizar(pasta, numero, CATEGORIA_RIF,
                      novo_nome=f"RIF_{nu_rif}" if nu_rif else None)
    except Exception:
        logger.exception("organizar pasta do RIF no nó (melhor esforço)")

    resumo = {"qt_comunicacoes": len(comunicacoes), "qt_envolvidos": len(envolvidos),
              "qt_ocorrencias": len(ocorrencias), "nu_rif": nu_rif}
    canal.estado({"t": "contagens", "contagens": {
        "arquivos": len(arquivos)}})
    canal.finalizar({"audios_transcritos": 0,
                     "aviso": f"RIF {nu_rif or no_pasta}: {len(comunicacoes)} comunicações, "
                              f"{len(envolvidos)} envolvidos, {len(ocorrencias)} ocorrências "
                              "(importado na estação)."})
    return resumo


def reproduzir_rif(cur, tipo: str, args: tuple, fonte: dict) -> None:
    """(paridade com o central — o replay roda no central, nunca aqui) Reproduz um registro
    rif_* do nó; ver a versão do central em api/funcoes/rif_utils.py."""
    raise NotImplementedError("o replay do RIF roda só no central")


# ---------------------------------------------------------------------------
# Consultas (card + página rif.html) e remoção
# ---------------------------------------------------------------------------

def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_rifs_inquerito(id_inquerito: int) -> list[dict]:
    """RIFs importados de um inquérito (com contagens), para o bloco no card da investigação.
    Inclui o titular mais citado (por nº de comunicações em que aparece como Titular) — o mesmo
    critério usado no cabeçalho de rif.html — para identificar o RIF de relance no card."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id_rif, r.nu_rif, r.no_pasta, r.id_documento_pdf,
                       r.qt_comunicacoes, r.qt_envolvidos, r.qt_ocorrencias,
                       r.dt_importacao, u.no_usuario AS importado_por,
                       ap.no_apelido,
                       tit.no_envolvido AS no_titular, tit.nu_cpf_cnpj AS nu_cpf_cnpj_titular,
                       tit.qt_distintos AS qt_titulares_distintos,
                       arq.hashes AS hashes_arquivos
                FROM investigacao.rif_tb01_rifs r
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = r.id_usuario
                -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
                LEFT JOIN investigacao.inq_tb06_apelidos ap
                       ON ap.tp_prova = 'rif' AND ap.id_prova = r.id_rif
                LEFT JOIN LATERAL (
                    SELECT json_agg(json_build_object(
                               'no_arquivo', a.no_arquivo, 'tp_arquivo', a.tp_arquivo,
                               'qt_bytes', a.qt_bytes, 'co_hash_md5', a.co_hash_md5,
                               'co_hash_sha1', a.co_hash_sha1, 'co_hash_sha256', a.co_hash_sha256
                           ) ORDER BY a.tp_arquivo) AS hashes
                    FROM investigacao.rif_tb06_arquivos a
                    WHERE a.id_rif = r.id_rif
                ) arq ON true
                LEFT JOIN LATERAL (
                    SELECT (array_agg(no_envolvido ORDER BY qtd DESC))[1] AS no_envolvido,
                           (array_agg(nu_cpf_cnpj ORDER BY qtd DESC))[1] AS nu_cpf_cnpj,
                           count(*) AS qt_distintos
                    FROM (
                        SELECT no_envolvido, nu_cpf_cnpj, count(*) AS qtd
                        FROM investigacao.rif_tb03_envolvidos
                        WHERE id_rif = r.id_rif AND trim(tp_envolvido) = 'Titular'
                        GROUP BY no_envolvido, nu_cpf_cnpj
                    ) x
                ) tit ON true
                WHERE r.id_inquerito = %s
                ORDER BY r.dt_importacao DESC
                """,
                (id_inquerito,),
            )
            return _linhas(cur)


def obter_id_inquerito_de_rif(id_rif: int) -> int | None:
    """Resolve o inquérito dono de um RIF, para checar permissão nas rotas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.rif_tb01_rifs WHERE id_rif = %s", (id_rif,))
            row = cur.fetchone()
            return row[0] if row else None


def listar_comunicacoes_rif(id_rif: int) -> list[dict]:
    """Comunicações de um RIF. O texto da análise sai NORMALIZADO para leitura (ver
    normalizar_texto_analise) — o cru, com a paginação do SISCOAF, continua no banco."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_comunicacao_rif, nu_indexador, id_comunicacao, nu_ocorrencia_bc,
                       dt_recebimento, dt_operacao, dt_fim_fato, nu_cpf_cnpj_comunicante,
                       no_comunicante, no_cidade_agencia, sg_uf_agencia, no_agencia, nu_agencia,
                       tx_informacoes_adicionais, vl_campo_a, vl_campo_b, vl_campo_c, vl_campo_d,
                       vl_campo_e, cd_segmento
                FROM investigacao.rif_tb02_comunicacoes
                WHERE id_rif = %s
                ORDER BY nu_indexador
                """,
                (id_rif,),
            )
            linhas = _linhas(cur)
    for linha in linhas:
        linha["tx_informacoes_adicionais"] = normalizar_texto_analise(linha["tx_informacoes_adicionais"])
    return linhas


def listar_contrapartes_rif(id_rif: int) -> list[dict]:
    """Contrapartes estruturadas (remetentes/favorecidos) de todas as comunicações do RIF.
    O front agrupa por id_comunicacao_rif + cd_direcao para as tabelas do 'Ver análise'."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_contraparte_rif, id_comunicacao_rif, cd_direcao, no_contraparte,
                       nu_cpf_cnpj, qt_lancamentos, vl_total, tx_obs, nu_ordem
                FROM investigacao.rif_tb05_contrapartes
                WHERE id_rif = %s
                ORDER BY id_comunicacao_rif, cd_direcao, nu_ordem
                """,
                (id_rif,),
            )
            return _linhas(cur)


# ---------------------------------------------------------------------------
# Contrapartes consolidadas por pessoa — a visão financeira do RIF inteiro
# ---------------------------------------------------------------------------
# As contrapartes estruturadas (TB05) só existem dentro de cada comunicação: a mesma pessoa
# que recebeu em doze comunicações aparece doze vezes, e a pergunta financeira ("quem MAIS
# movimentou com os investigados, quanto e quando") não tem onde ser respondida. Aqui a
# pessoa é a MESMA chave da linha do tempo financeira (simba_utils): CPF/CNPJ quando existe,
# senão NOME:+maiúsculas — o que permite casar o envolvido do RIF, a contraparte estruturada
# e a identificação manual na mesma pessoa. O DESCONHECIDO (linha sem nome e sem documento)
# volta à parte, com o que já foi identificado à mão somado por cima — é o que alimenta a
# barra de cobertura ("quanto do movimentado tem dono"). O cruzamento com o SIMBA do mesmo
# inquérito marca a pessoa que o COAF cita E que também aparece no extrato — a prova mais
# forte que as duas fontes independentes podem dar.

_CHAVE_CONTRAPARTE = (
    "coalesce(nullif(btrim({a}.nu_cpf_cnpj), ''),"
    " CASE WHEN nullif(btrim({a}.no_contraparte), '') IS NOT NULL"
    " THEN 'NOME:' || upper(btrim({a}.no_contraparte)) END)"
)
_CHAVE_ENVOLVIDO_RIF = (
    "coalesce(nullif(btrim(e.nu_cpf_cnpj), ''),"
    " 'NOME:' || upper(btrim(e.no_envolvido)))"
)


def _digitos_doc(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def consolidar_contrapartes_rif(id_rif: int) -> dict:
    """Contrapartes do RIF consolidadas por pessoa, com o DESCONHECIDO e a cobertura à parte.

    Uma linha por pessoa (chave CPF/CNPJ, senão nome): crédito (creditou), débito (foi
    debitado), nº de comunicações, período, badge de PEP/titular (dos envolvidos) e o
    cruzamento com o SIMBA do mesmo inquérito (``simba`` quando o documento também movimenta
    lá). Identificações manuais entram na pessoa (``vl_identificado_*``), não somem do
    desconhecido sem rastre.``ids_comunicacoes`` permite ao front filtrar as comunicações
    da pessoa."""
    id_inquerito = obter_id_inquerito_de_rif(id_rif)
    base_ct = _CHAVE_CONTRAPARTE.format(a="cp")
    base_id = _CHAVE_CONTRAPARTE.format(a="i")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {base_ct} AS chave,
                       (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS nome,
                       (array_agg(cp.nu_cpf_cnpj ORDER BY cp.vl_total DESC))[1] AS documento,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_credito,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_debito,
                       count(DISTINCT cp.id_comunicacao_rif) AS qt_comunicacoes,
                       min(coalesce(c.dt_operacao, c.dt_recebimento::date)) AS dt_primeiro,
                       max(coalesce(c.dt_operacao, c.dt_recebimento::date)) AS dt_ultimo,
                       (array_agg(cp.tx_obs ORDER BY cp.vl_total DESC))[1] AS tx_obs,
                       array_agg(DISTINCT cp.id_comunicacao_rif) AS ids_comunicacoes
                FROM investigacao.rif_tb05_contrapartes cp
                JOIN investigacao.rif_tb02_comunicacoes c ON c.id_comunicacao_rif = cp.id_comunicacao_rif
                WHERE cp.id_rif = %(rif)s AND {base_ct} IS NOT NULL
                GROUP BY 1
                """,
                {"rif": id_rif},
            )
            estruturadas = _linhas(cur)
            cur.execute(
                """
                SELECT coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_credito,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_debito,
                       count(DISTINCT cp.id_comunicacao_rif) AS qt_comunicacoes,
                       array_agg(DISTINCT cp.id_comunicacao_rif) AS ids_comunicacoes
                FROM investigacao.rif_tb05_contrapartes cp
                WHERE cp.id_rif = %(rif)s
                  AND nullif(btrim(coalesce(cp.no_contraparte, '')), '') IS NULL
                  AND nullif(btrim(coalesce(cp.nu_cpf_cnpj, '')), '') IS NULL
                """,
                {"rif": id_rif},
            )
            desconhecido = (_linhas(cur) or [{}])[0]
            cur.execute(
                f"""
                SELECT {base_id} AS chave,
                       (array_agg(i.no_contraparte ORDER BY i.vl_total DESC))[1] AS nome,
                       (array_agg(i.nu_cpf_cnpj ORDER BY i.vl_total DESC))[1] AS documento,
                       coalesce(sum(i.vl_total) FILTER (WHERE i.cd_direcao = 'credito'), 0) AS vl_credito,
                       coalesce(sum(i.vl_total) FILTER (WHERE i.cd_direcao = 'debito'), 0) AS vl_debito,
                       count(DISTINCT i.id_comunicacao_rif) AS qt_comunicacoes,
                       array_agg(DISTINCT i.id_comunicacao_rif) AS ids_comunicacoes
                FROM investigacao.rif_tb06_identificacoes i
                JOIN investigacao.rif_tb02_comunicacoes c ON c.id_comunicacao_rif = i.id_comunicacao_rif
                WHERE c.id_rif = %(rif)s AND {base_id} IS NOT NULL
                GROUP BY 1
                """,
                {"rif": id_rif},
            )
            identificadas = _linhas(cur)
            cur.execute(
                f"""
                SELECT {_CHAVE_ENVOLVIDO_RIF} AS chave,
                       bool_or(coalesce(e.tx_pep, '') ILIKE 's%%') AS pep,
                       bool_or(coalesce(e.tx_servidor, '') ILIKE 's%%') AS servidor,
                       bool_or(trim(coalesce(e.tp_envolvido, '')) = 'Titular') AS titular
                FROM investigacao.rif_tb03_envolvidos e
                WHERE e.id_rif = %(rif)s
                  AND (nullif(btrim(coalesce(e.nu_cpf_cnpj, '')), '') IS NOT NULL
                       OR nullif(btrim(coalesce(e.no_envolvido, '')), '') IS NOT NULL)
                GROUP BY 1
                """,
                {"rif": id_rif},
            )
            flags = {l["chave"]: l for l in _linhas(cur)}
            cur.execute(
                """
                SELECT coalesce(sum(c.vl_campo_b), 0) AS vl_credito,
                       coalesce(sum(c.vl_campo_c), 0) AS vl_debito,
                       count(*) AS qt_comunicacoes
                FROM investigacao.rif_tb02_comunicacoes c
                WHERE c.id_rif = %(rif)s
                """,
                {"rif": id_rif},
            )
            totais = (_linhas(cur) or [{}])[0]

            simba: dict[str, dict] = {}
            if id_inquerito:
                cur.execute(
                    """
                    SELECT dig, sum(vl) AS vl_total, bool_or(eh_titular) AS titular
                    FROM (
                        SELECT regexp_replace(coalesce(cp.nu_cpf_cnpj, ''), '\\D', '', 'g') AS dig,
                               cp.vl_total AS vl, false AS eh_titular
                        FROM investigacao.smb_tb04_contrapartes cp
                        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = cp.id_extrato
                        WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                        UNION ALL
                        SELECT regexp_replace(coalesce(ct.nu_cpf_cnpj, ''), '\\D', '', 'g'),
                               ct.vl_credito + ct.vl_debito, true
                        FROM investigacao.smb_tb02_contas ct
                        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = ct.id_extrato
                        WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                    ) d
                    WHERE length(dig) >= 11
                    GROUP BY dig
                    """,
                    {"inq": id_inquerito},
                )
                simba = {l["dig"]: {"vl_total": float(l["vl_total"] or 0),
                                    "titular": bool(l["titular"])} for l in _linhas(cur)}

    por_chave: dict[str, dict] = {}

    def _vazar(linha: dict, prefixo: str) -> None:
        chave = linha.get("chave")
        if not chave:
            return
        alvo = por_chave.setdefault(chave, {
            "chave": chave, "nome": None, "documento": None, "pep": False, "servidor": False,
            "titular": False, "qt_comunicacoes": 0,
            "vl_credito": 0.0, "vl_debito": 0.0,
            "vl_identificado_credito": 0.0, "vl_identificado_debito": 0.0,
            "dt_primeiro": None, "dt_ultimo": None, "tx_obs": None,
            "tem_identificacao": False, "ids_comunicacoes": [],
        })
        alvo[prefixo + "_credito"] = alvo[prefixo + "_credito"] + float(linha.get("vl_credito") or 0)
        alvo[prefixo + "_debito"] = alvo[prefixo + "_debito"] + float(linha.get("vl_debito") or 0)
        alvo["qt_comunicacoes"] = max(alvo["qt_comunicacoes"], int(linha.get("qt_comunicacoes") or 0))
        if not alvo["nome"] and linha.get("nome"):
            alvo["nome"], alvo["documento"] = linha["nome"], linha.get("documento")
        if linha.get("tx_obs") and not alvo["tx_obs"]:
            alvo["tx_obs"] = linha["tx_obs"]
        for campo in ("dt_primeiro", "dt_ultimo"):
            valor = linha.get(campo)
            if valor and (alvo[campo] is None or (campo == "dt_primeiro" and valor < alvo[campo])
                          or (campo == "dt_ultimo" and valor > alvo[campo])):
                alvo[campo] = valor
        alvo["ids_comunicacoes"] = sorted(set(alvo["ids_comunicacoes"])
                                          | set(linha.get("ids_comunicacoes") or []))

    for linha in estruturadas:
        _vazar(linha, "vl")
    for linha in identificadas:
        _vazar(linha, "vl_identificado")
        alvo = por_chave.get(linha.get("chave"))
        if alvo:
            alvo["tem_identificacao"] = True

    pessoas = []
    for chave, p in por_chave.items():
        f = flags.get(chave) or {}
        p["pep"], p["servidor"], p["titular"] = bool(f.get("pep")), bool(f.get("servidor")), bool(f.get("titular"))
        dig = _digitos_doc(p["documento"])
        p["tp_pessoa"] = {11: "PF", 14: "PJ"}.get(len(dig))
        p["saldo"] = round(p["vl_credito"] + p["vl_identificado_credito"]
                           - p["vl_debito"] - p["vl_identificado_debito"], 2)
        p["simba"] = simba.get(dig) if len(dig) >= 11 else None
        pessoas.append(p)
    pessoas.sort(key=lambda p: (-(p["vl_credito"] + p["vl_identificado_credito"]
                                  + p["vl_debito"] + p["vl_identificado_debito"]),
                                p["nome"] or p["chave"]))

    total_cred = float(totais.get("vl_credito") or 0)
    total_deb = float(totais.get("vl_debito") or 0)
    nomeado_cred = sum(p["vl_credito"] for p in pessoas)
    nomeado_deb = sum(p["vl_debito"] for p in pessoas)
    ident_cred = sum(p["vl_identificado_credito"] for p in pessoas)
    ident_deb = sum(p["vl_identificado_debito"] for p in pessoas)
    total_mov = total_cred + total_deb
    return {
        "pessoas": pessoas,
        "desconhecido": {
            "credito": float(desconhecido.get("vl_credito") or 0),
            "debito": float(desconhecido.get("vl_debito") or 0),
            "identificado_credito": ident_cred,
            "identificado_debito": ident_deb,
            "qt_comunicacoes": int(desconhecido.get("qt_comunicacoes") or 0),
            "ids_comunicacoes": sorted(desconhecido.get("ids_comunicacoes") or []),
        },
        "cobertura": {
            "qt_comunicacoes": int(totais.get("qt_comunicacoes") or 0),
            "vl_credito": total_cred,
            "vl_debito": total_deb,
            "nomeado_credito": nomeado_cred,
            "nomeado_debito": nomeado_deb,
            "identificado_credito": ident_cred,
            "identificado_debito": ident_deb,
            "pct": round(100.0 * (nomeado_cred + nomeado_deb + ident_cred + ident_deb) / total_mov, 1)
                   if total_mov > 0 else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Identificação manual de parte de uma contraparte DESCONHECIDA (RIF_TB06)
# ---------------------------------------------------------------------------
# A linha sem nome/documento de RIF_TB05 é o resto entre o total da comunicação e
# o que o texto nomeia. Quando o investigador descobre por outra fonte que uma fatia
# desse resto é de alguém, grava aqui — sem mexer na TB05, que é reconstruída inteira
# pelo "Reprocessar contrapartes". Ver db/init/25_rif.sql.

def _valor_desconhecido(cur, id_comunicacao_rif: int, cd_direcao: str) -> float:
    """Total da(s) linha(s) DESCONHECIDO (sem nome e sem documento) de uma comunicação/direção."""
    cur.execute(
        """
        SELECT COALESCE(SUM(vl_total), 0)
        FROM investigacao.rif_tb05_contrapartes
        WHERE id_comunicacao_rif = %s AND cd_direcao = %s
          AND (no_contraparte IS NULL OR no_contraparte = '')
          AND (nu_cpf_cnpj IS NULL OR nu_cpf_cnpj = '')
        """,
        (id_comunicacao_rif, cd_direcao),
    )
    return float(cur.fetchone()[0] or 0)


def _valor_identificado(cur, id_comunicacao_rif: int, cd_direcao: str, excluir: int | None = None) -> float:
    """Soma já identificada naquela comunicação/direção. `excluir` tira a própria linha, na edição."""
    cur.execute(
        """
        SELECT COALESCE(SUM(vl_total), 0)
        FROM investigacao.rif_tb06_identificacoes
        WHERE id_comunicacao_rif = %s AND cd_direcao = %s
          AND (%s::bigint IS NULL OR id_identificacao_rif <> %s::bigint)
        """,
        (id_comunicacao_rif, cd_direcao, excluir, excluir),
    )
    return float(cur.fetchone()[0] or 0)


# Namespace do advisory lock que serializa as identificações de uma mesma comunicação. Número
# arbitrário, só não pode colidir com os outros advisory locks do sistema (72301 é o do runner de
# migrações, em funcoes/migrar_utils.py).
_LOCK_IDENTIFICACAO = 72304


def _validar_identificacao(cur, id_comunicacao_rif: int, cd_direcao: str, valor: float,
                           excluir: int | None = None) -> None:
    """Recusa valor não positivo ou que estoure o desconhecido daquela comunicação/direção.

    Serializa por comunicação antes de ler os totais. Sem isso, isto era um
    ler-conferir-gravar clássico em READ COMMITTED: dois analistas do mesmo RIF identificando ao
    mesmo tempo liam ambos "disponível: R$ 10.000", ambos passavam na conferência e ambos
    gravavam R$ 8.000 — R$ 16 mil identificados sobre R$ 10 mil de desconhecido, sem erro nenhum
    na tela e sem constraint no banco que barrasse. E o número segue para o .docx assinado.

    Lock de TRANSAÇÃO (`_xact_`), não de sessão: some no commit, não segura conexão do pool e não
    precisa de unlock explícito nem no caminho de erro. Quem chama já está numa transação (o
    SELECT de conferência do RIF veio antes), e o INSERT/UPDATE acontece na mesma — que é o que
    faz a conferência valer. Se outro analista estiver com o lock por mais que o lock_timeout
    (5 s, ver funcoes/db_utils.py), a rota responde 503 "tente de novo" em vez de gravar errado."""
    if cd_direcao not in ("credito", "debito"):
        raise ValueError("Direção inválida.")
    if valor is None or valor <= 0:
        raise ValueError("O valor identificado precisa ser maior que zero.")
    # as duas direções de uma comunicação são independentes, mas travar a comunicação inteira é
    # mais simples e o custo é nulo: são milissegundos, e ninguém identifica crédito e débito da
    # mesma comunicação no mesmo instante
    cur.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        (_LOCK_IDENTIFICACAO, id_comunicacao_rif % 2_147_483_647),
    )
    total = _valor_desconhecido(cur, id_comunicacao_rif, cd_direcao)
    if total <= 0:
        raise ValueError("Esta comunicação não tem valor desconhecido nesta direção.")
    disponivel = round(total - _valor_identificado(cur, id_comunicacao_rif, cd_direcao, excluir), 2)
    if round(valor, 2) > disponivel + 0.005:
        # 1234.5 -> "1.234,50" (formato pt-BR, trocando os separadores do format do Python)
        formatado = f"{disponivel:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
        raise ValueError(f"Valor acima do que resta identificar (disponível: R$ {formatado}).")


def listar_identificacoes_rif(id_rif: int) -> list[dict]:
    """Identificações manuais de todo o RIF; o front agrupa por comunicação + direção."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id_identificacao_rif, i.id_comunicacao_rif, i.cd_direcao, i.no_contraparte,
                       i.nu_cpf_cnpj, i.qt_lancamentos, i.vl_total, i.tx_obs,
                       i.dt_criacao, i.dt_atualizacao, u.no_usuario AS identificado_por
                FROM investigacao.rif_tb06_identificacoes i
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = i.id_usuario
                WHERE i.id_rif = %s
                ORDER BY i.id_comunicacao_rif, i.cd_direcao, i.vl_total DESC
                """,
                (id_rif,),
            )
            return _linhas(cur)


def criar_identificacao_rif(id_rif: int, id_comunicacao_rif: int, cd_direcao: str, nome: str | None,
                            doc: str | None, valor: float, qtde: int | None, obs: str | None,
                            id_usuario: int | None) -> dict:
    """Registra que uma fatia do desconhecido é de alguém. ValueError se o valor estourar o resto."""
    if not (nome or "").strip() and not (doc or "").strip():
        raise ValueError("Informe ao menos o nome ou o CPF/CNPJ.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM investigacao.rif_tb02_comunicacoes WHERE id_comunicacao_rif = %s AND id_rif = %s",
                (id_comunicacao_rif, id_rif),
            )
            if cur.fetchone() is None:
                raise ValueError("Comunicação não pertence a este RIF.")
            _validar_identificacao(cur, id_comunicacao_rif, cd_direcao, valor)
            cur.execute(
                """
                INSERT INTO investigacao.rif_tb06_identificacoes
                    (id_comunicacao_rif, id_rif, cd_direcao, no_contraparte, nu_cpf_cnpj,
                     qt_lancamentos, vl_total, tx_obs, id_usuario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_identificacao_rif
                """,
                (id_comunicacao_rif, id_rif, cd_direcao, (nome or "").strip() or None,
                 (doc or "").strip() or None, qtde, round(float(valor), 2),
                 (obs or "").strip() or None, id_usuario),
            )
            id_identificacao = cur.fetchone()[0]
        conn.commit()
    return {"id_identificacao_rif": id_identificacao}


def editar_identificacao_rif(id_identificacao: int, nome: str | None, doc: str | None, valor: float,
                             qtde: int | None, obs: str | None) -> bool:
    """Atualiza uma identificação já registrada. False se ela não existir."""
    if not (nome or "").strip() and not (doc or "").strip():
        raise ValueError("Informe ao menos o nome ou o CPF/CNPJ.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_comunicacao_rif, cd_direcao
                FROM investigacao.rif_tb06_identificacoes
                WHERE id_identificacao_rif = %s
                """,
                (id_identificacao,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            _validar_identificacao(cur, row[0], row[1], valor, excluir=id_identificacao)
            cur.execute(
                """
                UPDATE investigacao.rif_tb06_identificacoes
                SET no_contraparte = %s, nu_cpf_cnpj = %s, qt_lancamentos = %s, vl_total = %s,
                    tx_obs = %s, dt_atualizacao = now()
                WHERE id_identificacao_rif = %s
                """,
                ((nome or "").strip() or None, (doc or "").strip() or None, qtde,
                 round(float(valor), 2), (obs or "").strip() or None, id_identificacao),
            )
        conn.commit()
    return True


def apagar_identificacao_rif(id_identificacao: int) -> bool:
    """Remove a identificação — o valor volta a ser desconhecido. False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.rif_tb06_identificacoes WHERE id_identificacao_rif = %s",
                (id_identificacao,),
            )
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def obter_id_rif_de_identificacao(id_identificacao: int) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_rif FROM investigacao.rif_tb06_identificacoes WHERE id_identificacao_rif = %s",
                (id_identificacao,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def listar_envolvidos_rif(id_rif: int) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_envolvido_rif, nu_indexador, nu_cpf_cnpj, no_envolvido, tp_envolvido,
                       nu_agencia, nu_conta, dt_abertura_conta, dt_atualizacao_conta,
                       tx_pep, tx_pessoa_obrigada, tx_servidor
                FROM investigacao.rif_tb03_envolvidos
                WHERE id_rif = %s
                ORDER BY nu_indexador, no_envolvido
                """,
                (id_rif,),
            )
            return _linhas(cur)


def listar_ocorrencias_rif(id_rif: int) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_ocorrencia_rif, nu_indexador, id_ocorrencia, tx_ocorrencia
                FROM investigacao.rif_tb04_ocorrencias
                WHERE id_rif = %s
                ORDER BY nu_indexador, id_ocorrencia
                """,
                (id_rif,),
            )
            return _linhas(cur)


def apagar_rif(id_rif: int) -> bool:
    """Remove um RIF e todas as suas linhas filhas + o documento PDF vinculado. False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_documento_pdf FROM investigacao.rif_tb01_rifs WHERE id_rif = %s", (id_rif,))
            row = cur.fetchone()
            if row is None:
                return False
            id_documento_pdf = row[0]
            cur.execute("DELETE FROM investigacao.rif_tb06_identificacoes WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb06_arquivos WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb05_contrapartes WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb02_comunicacoes WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb03_envolvidos WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb04_ocorrencias WHERE id_rif = %s", (id_rif,))
            cur.execute("DELETE FROM investigacao.rif_tb01_rifs WHERE id_rif = %s", (id_rif,))
            if id_documento_pdf is not None:
                cur.execute(
                    "DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = %s",
                    (id_documento_pdf,)
                )
        conn.commit()
    return True
