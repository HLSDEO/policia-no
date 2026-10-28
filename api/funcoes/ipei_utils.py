"""IPEI — Informação de Pesquisa e Investigação da Receita Federal (quebra de sigilo fiscal).

O que o investigador recebe depois de deferida a quebra de sigilo fiscal é uma pasta com um PDF
por alvo, cada um acompanhado de uma planilha de anexo (as notas fiscais completas, a relação de
contas, o RENAVAM). O PDF é emitido pelo Nupei/Copei e segue um roteiro fixo — por isso dá para
levantar deterministicamente, por regex e sem IA (mesmo espírito do RIF, do IPL e do IRPF):

    alvos        · o contribuinte analisado e seus dados cadastrais (PF ou PJ)
    relacionados · sócios, responsáveis legais, empregadores, outorgantes/outorgados de
                   procuração, procuradores perante a RFB e quem transmitiu declaração do mesmo MAC
    anos         · o consolidado anual — rendimentos/receita, movimentação financeira, DECRED,
                   NFe, variação patrimonial e a razão movimentação/rendimento (o sinal mais forte)
    fontes       · fontes pagadoras e pagamentos declarados, por ano
    contas       · contas bancárias da e-Financeira, com crédito/débito/saldo mês a mês
    bens         · bens declarados, veículos do RENAVAM, embarcações, aeronaves e imóveis (DOI)
    notas        · notas fiscais eletrônicas destinadas ao alvo (as do PDF e as do anexo)
    contatos     · endereços, telefones, e-mails e MAC/IP levantados em outras bases da RFB

Dois formatos convivem na mesma pasta e os dois passam por aqui:

* **IPEI de pessoa física** — um alvo por PDF, seções "2.x". O gerador não escreve ToUnicode
  válido, então o pypdf devolveria a página inteira como nome de glifo; quem desfaz isso é
  funcoes/pdf_texto.py, antes de qualquer regex daqui.
* **IPEI de pessoa jurídica** — vários alvos no mesmo PDF, um bloco "3.N" por empresa, com as
  tabelas pesadas empurradas para o anexo.

O que os une é o esqueleto numerado das seções: `_secoes()` reconstrói esse índice a partir do
texto e todo extrator daqui trabalha dentro de uma seção, nunca sobre o PDF inteiro. Um alvo é
toda seção que tem uma subseção "DADOS CADASTRAIS" — é isso que faz o mesmo código servir para
um PDF de uma pessoa e para um PDF de seis empresas.

**PDF e anexo viram um card só.** A planilha que acompanha o PDF não é um documento à parte: ela
é a continuação das tabelas dele (as notas fiscais que o PDF resume, as contas que o PDF diz
estarem "no anexo"). Por isso `vincular_anexo()` costura os dois pelo número da IPEI que aparece
no nome dos dois arquivos — em qualquer ordem de importação — e o resultado é um bloco só na
tela de investigação.

Ver db/init/42_ipei.sql, rotas/investigacao.py e a página web ipei.html.
"""

import logging
import re
import unicodedata
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import limpar_anotacoes_orfas
from funcoes.organizacao_arquivos import resolver as resolver_em_dados
from funcoes.pdf_texto import paginas_pdf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalização de texto, números e datas
# ---------------------------------------------------------------------------


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def _chave(texto: str) -> str:
    """Maiúscula, sem acento e com espaços colapsados — base de toda comparação de rótulo."""
    return re.sub(r"\s+", " ", _sem_acento(texto)).upper().strip()


def _limpo(texto: str | None) -> str | None:
    texto = re.sub(r"\s+", " ", (texto or "")).strip(" :;-–")
    return texto or None


def _corta(texto: str | None, limite: int = 3000) -> str | None:
    texto = _limpo(texto)
    return texto[:limite] if texto else None


# "1.234.567,89", "-828,84", "0,00". Sem casar o "-" de "2018-2019" nem a data "31/12".
_RE_VALOR = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}")
# A célula vazia da tabela vem como "-" ou "x" solto; na leitura posicional ela PRECISA contar
# como coluna, senão todos os valores seguintes da linha escorregam uma casa para a esquerda.
_RE_CELULA = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|(?<![\w,.])[-x](?![\w,.])")

_RE_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_RE_CNPJ = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_RE_DOC = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}")
_RE_DATA_BR = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
# sem fronteira de palavra à esquerda: o extrator cola o mês na última letra do nome do banco
# ("…DO ESPIRITO SANTO01/2018") e com o \b a linha inteira deixaria de ser reconhecida
_RE_MES_ANO = re.compile(r"(0[1-9]|1[0-2])/(20\d{2})\b")
_RE_ANO = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RE_PLACA = re.compile(r"\b[A-Z]{3}[0-9][0-9A-Z][0-9]{2}\b")
_RE_MAC = re.compile(r"\b(?:[0-9A-F]{2}-){5}[0-9A-F]{2}\b|\b(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\b", re.IGNORECASE)
_RE_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_TELEFONE = re.compile(r"\b\d{10,13}\b")
_RE_PERCENTUAL = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*%")

_MESES = {m: i + 1 for i, m in enumerate(
    ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO",
     "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"])}
_RE_DATA_EXTENSO = re.compile(r"(\d{1,2})\s+DE\s+([A-Z]+)\s+DE\s+(\d{4})")


# Teto de sanidade. Nenhum valor legítimo desta fonte chega perto de dez trilhões: um número
# assim só aparece quando o extrator de PDF cola duas células ("AUTORIZACAO: 202300382824725" +
# "1,0000" viram "2023003828247251,0000"). Sem o corte, esse lixo estoura o NUMERIC da coluna e
# derruba a importação inteira por causa de uma linha — melhor a linha entrar sem o número.
_TETO_VALOR = 1e13


def _num(valor: str | None) -> float | None:
    """'1.234,56' -> 1234.56. Célula vazia da tabela ('-', 'x') vira None."""
    if not valor or valor.strip() in ("-", "x", "X"):
        return None
    try:
        numero = float(valor.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return numero if abs(numero) < _TETO_VALOR else None


def _celulas(texto: str) -> list[str | None]:
    """A linha de tabela em colunas, na ordem — valor monetário ou célula vazia."""
    return [_num(m.group(0)) for m in _RE_CELULA.finditer(texto or "")]


def _data(dia: int, mes: int, ano: int) -> str | None:
    if not (1 <= dia <= 31 and 1 <= mes <= 12 and 1900 <= ano <= 2100):
        return None
    return f"{ano:04d}-{mes:02d}-{dia:02d}"


def _primeira_data(texto: str | None) -> str | None:
    m = _RE_DATA_BR.search(texto or "")
    return _data(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _datas(texto: str | None) -> list[str]:
    achadas = [_data(int(d), int(m), int(a)) for d, m, a in _RE_DATA_BR.findall(texto or "")]
    return [d for d in achadas if d]


def _data_extenso(texto: str) -> str | None:
    m = _RE_DATA_EXTENSO.search(_chave(texto))
    if not m or m.group(2) not in _MESES:
        return None
    return _data(int(m.group(1)), _MESES[m.group(2)], int(m.group(3)))


def _so_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def _juntar_documento_quebrado(texto: str) -> str:
    """O PDF quebra a linha no meio do CNPJ da célula estreita ('23.750.632/0001' + '-78' na
    linha de baixo). Sem remendar isso, a linha inteira deixa de ser reconhecida como registro."""
    return re.sub(r"(\d{2}\.\d{3}\.\d{3}/\d{4})\s*\n\s*(-\d{2})", r"\1\2", texto)


# ---------------------------------------------------------------------------
# Detecção e índice de seções
# ---------------------------------------------------------------------------

# O timbre de sigilo se repete no topo de toda página e o rodapé traz a folha e o número da IPEI.
_RE_TIMBRE = re.compile(r"^\s*INFORMA[ÇC][ÃA]O PROTEGIDA POR SIGILO FISCAL\s*$", re.IGNORECASE)
_RE_RODAPE = re.compile(r"^\s*\(?\s*Fl\.\s*\d")
# ponto/traço de preenchimento do sumário ("1 INTRODUÇÃO......4", "3 DESENVOLVIMENTO ------ 7")
_RE_PREENCHIMENTO = re.compile(r"[.\-]{5,}")
_RE_FOLHA = re.compile(r"\bFl\.\s*(\d{1,4})\s*/\s*(\d{1,4})")
# "IPEI nº VT20230001" no rodapé, "VT 20230026" (com espaço) na variante de pessoa jurídica
_RE_NUMERO_IPEI = re.compile(r"IPEI\s*n?[ºo°]?\s*:?\s*([A-Z]{2})\s*(\d{6,10})", re.IGNORECASE)
_RE_PROCESSO = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")

_MARCAS_IPEI = ("INFORMACAO PROTEGIDA POR SIGILO FISCAL", "PESQUISA E INVESTIGACAO")


def parece_ipei(paginas: list[str]) -> bool:
    """Heurística: o timbre de sigilo fiscal, o vocabulário do Nupei e um número de IPEI.
    Conservadora de propósito — um PDF que não passe aqui vira só documento de texto, como antes
    (o ofício de encaminhamento que vem na mesma pasta cai justamente aqui)."""
    if not paginas:
        return False
    chave = _chave("\n".join(paginas[:3]))
    if not all(marca in chave for marca in _MARCAS_IPEI):
        return False
    return bool(_RE_NUMERO_IPEI.search("\n".join(paginas[:3])))


_RE_TITULO_SECAO = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){0,3})\s+([A-Za-zÀ-ÿ][^\n]{2,88}?)\s*$")
_MAX_SECAO_TOPO = 20


def _secoes(paginas: list[str]) -> list[dict]:
    """Reconstrói o índice do documento: [{numero, titulo, texto}] na ordem em que aparecem.

    O relatório é todo numerado ("2.9.1 Movimentação de contas de depósito"), mas uma linha de
    tabela começa por número do mesmo jeito ("2018 38.848,70 …", "2 PARES LED CODE FAROL…").
    O que separa um do outro é a hierarquia: um título ou desce um nível do título corrente
    (2.9 -> 2.9.1, e o irmão 2.9.2) ou abre o PRÓXIMO capítulo (2 -> 3). Uma linha de tabela não
    respeita essa progressão, e é assim que ela é descartada sem depender de fonte ou negrito."""
    secoes: list[dict] = []
    corrente: dict | None = None
    caminho: tuple[int, ...] = ()

    for pagina in paginas:
        # a página de sumário repete todos os títulos e estragaria o índice — some inteira
        if len(_RE_PREENCHIMENTO.findall(pagina)) > 5:
            continue
        for linha in pagina.split("\n"):
            if _RE_TIMBRE.match(linha) or _RE_RODAPE.match(linha):
                continue
            m = _RE_TITULO_SECAO.match(linha)
            numeros = tuple(int(x) for x in m.group(1).split(".")) if m else ()
            titulo_valido = False
            if m and numeros[0] <= _MAX_SECAO_TOPO and not _RE_PREENCHIMENTO.search(linha):
                if len(numeros) == 1:
                    titulo_valido = not caminho or numeros[0] == caminho[0] + 1
                else:
                    titulo_valido = bool(caminho) and numeros[:-1] == caminho[:len(numeros) - 1]
            if titulo_valido:
                corrente = {"numero": m.group(1), "titulo": _limpo(m.group(2)) or "", "linhas": []}
                secoes.append(corrente)
                caminho = numeros
            elif corrente is not None:
                corrente["linhas"].append(linha)

    for secao in secoes:
        secao["texto"] = _dessilabar(_juntar_documento_quebrado("\n".join(secao.pop("linhas")).strip()))
    return secoes


# A variante de pessoa jurídica justifica o texto e separa a palavra em sílabas na quebra de
# linha, inclusive dentro da célula da tabela ("JOSE EDSON CAR-" / "NEIRO RIBEIRO", "admi-" /
# "nistrador"). A letra colada ao hífen é o que distingue isso de um traço de coluna vazia.
_RE_SILABA_QUEBRADA = re.compile(r"(?<=[A-Za-zÀ-ÿ])-[ \t]*\n[ \t]*(?=[A-Za-zÀ-ÿ])")


def _dessilabar(texto: str) -> str:
    return _RE_SILABA_QUEBRADA.sub("", texto)


def _achar(secoes: list[dict], padrao: str, prefixo: str | None = None) -> dict | None:
    """Primeira seção cujo título casa com o padrão (sem acento, maiúscula), opcionalmente dentro
    de um alvo — `prefixo` é o número da seção do alvo ("2", "3.4")."""
    for secao in secoes:
        if prefixo and not secao["numero"].startswith(prefixo + "."):
            continue
        if re.search(padrao, _chave(secao["titulo"])):
            return secao
    return None


def _texto(secao: dict | None) -> str:
    return (secao or {}).get("texto", "")


# ---------------------------------------------------------------------------
# Cabeçalho do documento
# ---------------------------------------------------------------------------

_RE_UNIDADE = re.compile(r"[–-]\s*(RFB/[^)\n]{3,120})\)")
_RE_DECISAO = re.compile(r"decis[ãa]o\s+judicial[^.\n]{0,60}?em\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_RE_ASSINANTE = re.compile(r"Assinado\s+digitalmente\s*\n\s*([^\n]{4,80})", re.IGNORECASE)


def _extrair_cabecalho(paginas: list[str], secoes: list[dict]) -> dict:
    """Número, data, unidade emissora, processo judicial e os textos de fecho da IPEI."""
    todo = "\n".join(paginas)
    rodapes = [l for pagina in paginas for l in pagina.split("\n") if _RE_RODAPE.match(l)]
    linha_rodape = rodapes[0] if rodapes else ""

    m = _RE_NUMERO_IPEI.search(todo)
    nu_ipei = f"{m.group(1).upper()}{m.group(2)}" if m else None

    folhas = [int(total) for _, total in _RE_FOLHA.findall(todo)]
    unidade = _RE_UNIDADE.search(linha_rodape) or _RE_UNIDADE.search(todo)

    introducao = _texto(_achar(secoes, r"^INTRODUCAO"))
    processo = _RE_PROCESSO.search(introducao) or _RE_PROCESSO.search(todo)
    decisao = _RE_DECISAO.search(introducao) or _RE_DECISAO.search(todo)

    assinantes = [_limpo(a) for a in _RE_ASSINANTE.findall(paginas[-1] if paginas else "")]

    return {
        "nu_ipei": nu_ipei,
        "dt_ipei": _data_extenso(linha_rodape) or _data_extenso(todo),
        "no_unidade": _corta(unidade.group(1) if unidade else None),
        "nu_processo_judicial": processo.group(0) if processo else None,
        "dt_decisao": _primeira_data(decisao.group(1)) if decisao else None,
        "tx_conclusao": _corta(_texto(_achar(secoes, r"^CONCLUSAO|^CONSIDERACOES FINAIS")), 20000),
        "tx_encaminhamento": _corta(_texto(_achar(secoes, r"^ENCAMINHAMENTO")), 5000),
        "tx_anexos": _corta(_texto(_achar(secoes, r"^ANEXO")), 5000),
        "no_assinantes": _corta("; ".join(a for a in assinantes if a), 2000),
        "qt_folhas": max(folhas) if folhas else len(paginas),
        "qt_paginas": len(paginas),
    }


# ---------------------------------------------------------------------------
# Alvos — o contribuinte analisado e seus dados cadastrais
# ---------------------------------------------------------------------------

# Rótulos de "Dados Cadastrais" nas duas variantes. O PDF de pessoa física cola rótulo e valor
# sem separador nenhum ("CPF144.558.737-83", "SexoMASCULINO"), então o valor é o que vem entre o
# fim de um rótulo e o começo do próximo — mesma técnica do IRPF. "Nome" é prefixo de "Nome da
# Mãe" e de "Nome Empresarial": por isso os rótulos longos entram ANTES na lista e a posição já
# consumida por um deles não pode ser reaproveitada por um mais curto.
_ROTULOS_CADASTRO = [
    ("nome_empresarial", r"Nome Empresarial\s*:?"),
    ("mae", r"Nome da M[ãa]e\s*:?"),
    ("cnpj", r"CNPJ Matriz\s*:?"),
    ("inscricao", r"Data de Inscri[çc][ãa]o no\s*\n?\s*CPF\s*:?"),
    ("nascimento", r"Data de Nascimento\s*:?"),
    ("abertura", r"Data (?:de )?Abertura\s*:?"),
    ("baixa", r"Data (?:de )?Baixa\s*:?"),
    ("endereco", r"Endere[çc]o Completo\s*:?"),
    ("cnae", r"CNAE principal\s*:?"),
    ("capital", r"Capital Social\s*:?"),
    ("ocupacao", r"Ocupa[çc][ãa]o Principal\s*:?"),
    ("descricao_ocupacao", r"Descri[çc][ãa]o\s*:?"),
    ("situacao", r"Situa[çc][ãa]o Cadastral\s*:?"),
    ("obito", r"Indicador de [ÓO]bito\s*:?"),
    ("nacionalidade", r"Nacionalidade\s*:?"),
    ("contador", r"Contador\s*:?"),
    ("dte", r"Optante DTE\s*:?"),
    # ancorados no início da linha: no PDF de pessoa jurídica esses rótulos abrem a linha, e
    # sem a âncora "CEP" casaria dentro do "Endereço CompletoCEP 29050600 - …" da pessoa física
    # e comeria o endereço inteiro (o CEP dali sai por _cep(), do próprio endereço)
    ("logradouro", r"(?m)^\s*Logradouro\s*:"),
    ("complemento", r"(?m)^\s*Complemento\s*:"),
    ("bairro", r"(?m)^\s*Bairro\s*:"),
    ("municipio", r"(?m)^\s*Munic[íi]pio\s*:"),
    ("cpf", r"CPF\s*:?(?!/)"),
    ("nome", r"Nome\s*:?"),
    ("sexo", r"Sexo\s*:?"),
    ("cep", r"(?m)^\s*CEP\s*:"),
    ("uf", r"(?m)^\s*UF\s*:"),
]


def _campos_por_rotulo(texto: str, rotulos: list[tuple[str, str]]) -> dict[str, str | None]:
    """Pares rótulo->valor: o valor de um campo é o texto entre o fim do seu rótulo e o começo do
    próximo rótulo QUE APARECE NO TEXTO (por posição real, não pela ordem declarada). Assim um
    campo em branco — dois rótulos colados, sem separador — vira vazio em vez de roubar o valor
    do campo seguinte."""
    achados: list[tuple[int, int, str]] = []
    ocupado: list[tuple[int, int]] = []
    for chave, padrao in rotulos:
        for m in re.finditer(padrao, texto, re.IGNORECASE):
            # rótulo mais longo ganha: "Nome da Mãe" já consumiu a posição em que "Nome" casaria
            if any(ini <= m.start() < fim for ini, fim in ocupado):
                continue
            achados.append((m.start(), m.end(), chave))
            ocupado.append((m.start(), m.end()))
            break
    achados.sort()
    campos: dict[str, str | None] = {chave: None for chave, _ in rotulos}
    for i, (_, fim, chave) in enumerate(achados):
        proximo = achados[i + 1][0] if i + 1 < len(achados) else len(texto)
        campos[chave] = _limpo(texto[fim:proximo])
    return campos


_RE_ALVO_TITULO = re.compile(
    r"^(?:INFORMA[ÇC][ÕO]ES FISCAIS D[EO]S?\s+)?(.+?)(?:\s*[–-]\s*CNPJ.*)?$", re.IGNORECASE)


def _nome_do_titulo(titulo: str) -> str | None:
    m = _RE_ALVO_TITULO.match(_limpo(titulo) or "")
    return _limpo(m.group(1)) if m else _limpo(titulo)


def _valor_solto(texto: str | None) -> float | None:
    m = _RE_VALOR.search(texto or "")
    return _num(m.group(0)) if m else None


_RE_CEP_NO_ENDERECO = re.compile(r"CEP\s*([\d.-]{8,10})")


_RE_ROTULO_DESCRICAO = re.compile(r"Descri[çc][ãa]o", re.IGNORECASE)


def _sem_rotulo_descricao(texto: str | None) -> str | None:
    """A ocupação da pessoa física vem partida em dois rótulos ("Ocupação Principal:" numa linha,
    "Descrição" na outra) e o segundo se repete na página seguinte, grudando no valor vizinho
    ("REGULAR Descrição", "DescriçãoREGULAR"). Só o rótulo sai; o valor fica."""
    return _limpo(_RE_ROTULO_DESCRICAO.sub(" ", texto or ""))


def _cep(campo: str | None, endereco: str | None) -> str | None:
    """O CEP vem em campo próprio (pessoa jurídica) ou embutido no endereço completo
    ("CEP 29050600 - R CARLOS ALVES, 170 - …", pessoa física)."""
    if campo:
        return _so_digitos(campo)[:8] or None
    m = _RE_CEP_NO_ENDERECO.search(endereco or "")
    return _so_digitos(m.group(1))[:8] if m else None


def _extrair_alvos(secoes: list[dict]) -> list[dict]:
    """Um alvo por seção que tenha uma subseção "Dados Cadastrais" — é o que faz o mesmo código
    servir para o PDF de uma pessoa física (um alvo, seções 2.x) e para o de várias empresas
    (um bloco 3.N por CNPJ)."""
    alvos = []
    for secao in secoes:
        if _chave(secao["titulo"]) != "DADOS CADASTRAIS":
            continue
        prefixo = secao["numero"].rsplit(".", 1)[0]
        if prefixo == secao["numero"]:  # "Dados Cadastrais" como capítulo solto: sem alvo próprio
            continue
        pai = next((s for s in secoes if s["numero"] == prefixo), None)
        titulo_pai = pai["titulo"] if pai else ""
        campos = _campos_por_rotulo(secao["texto"], _ROTULOS_CADASTRO)

        m = (_RE_DOC.search(campos["cnpj"] or campos["cpf"] or "")
             or _RE_DOC.search(titulo_pai) or _RE_DOC.search(secao["texto"]))
        documento = m.group(0) if m else None
        nome = campos["nome_empresarial"] or campos["nome"] or _nome_do_titulo(titulo_pai)
        endereco = campos["endereco"] or " ".join(
            p for p in (campos["logradouro"], campos["complemento"], campos["bairro"]) if p)

        alvos.append({
            "nu_secao": prefixo,
            "tp_pessoa": "PJ" if documento and len(_so_digitos(documento)) == 14 else "PF",
            "no_alvo": _corta(nome),
            "nu_cpf_cnpj": documento,
            "no_mae": _corta(campos["mae"]),
            "dt_nascimento": _primeira_data(campos["nascimento"] or campos["abertura"]),
            "dt_inscricao": _primeira_data(campos["inscricao"] or campos["abertura"]),
            "dt_baixa": _primeira_data(campos["baixa"]),
            "tx_endereco": _corta(endereco),
            "no_municipio": _corta(campos["municipio"]),
            "sg_uf": _corta(campos["uf"], 2),
            "nu_cep": _cep(campos["cep"], endereco),
            "tx_ocupacao": _corta(_sem_rotulo_descricao(
                campos["cnae"] or campos["descricao_ocupacao"] or campos["ocupacao"])),
            "tx_situacao_cadastral": _corta(_sem_rotulo_descricao(campos["situacao"]), 200),
            "vl_capital_social": _valor_solto(campos["capital"]),
            "tx_sexo": _corta(campos["sexo"], 30),
            "tx_nacionalidade": _corta(campos["nacionalidade"], 60),
            "fl_obito": bool(campos["obito"] and _chave(campos["obito"]).startswith("SIM")),
            "no_contador": _corta(campos["contador"]),
        })
    return alvos


# ---------------------------------------------------------------------------
# Registros de tabela — o tijolo de quase todo extrator daqui
# ---------------------------------------------------------------------------
# O extrator de PDF não devolve tabela, devolve as células empilhadas em ordem de leitura, com a
# linha quebrada onde a coluna era estreita. Não dá para ler "por coluna"; dá para achar a
# ÂNCORA que abre cada linha (o CPF do alvo, a placa, o MAC, a data de emissão) e tratar tudo o
# que vem até a próxima âncora como um registro. Daí cada extrator colhe do registro o que
# reconhece — data, valor, documento, nome em caixa alta — em vez de contar posições.


def _registros(texto: str, ancora: re.Pattern) -> list[tuple[str, str]]:
    """[(texto casado pela âncora, resto do registro)] — o resto vai até a próxima âncora."""
    marcas = list(ancora.finditer(texto or ""))
    saida = []
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        saida.append((m.group(0), re.sub(r"\s+", " ", texto[m.end():fim]).strip()))
    return saida


def _ancora_documento(documento: str | None) -> re.Pattern:
    """Âncora na linha de tabela cuja primeira coluna é o CPF/CNPJ do alvo, repetido em todas."""
    return re.compile(re.escape(documento)) if documento else _RE_DOC


# Nome de pessoa ou empresa nessas tabelas vem sempre em CAIXA ALTA, e o que vem depois dele
# (qualificação, situação) vem em Capitalizada — é esse degrau que marca onde o nome termina. Daí
# o lookahead em cada letra: a maiúscula seguida de minúscula já é do campo seguinte, não do
# nome, e sem isso "…GRAOS LTDA Socio" viraria "…GRAOS LTDA S" + "ocio" (o PDF costuma colar os
# dois campos, "LTDAAtiva").
_RE_NOME_CAIXA_ALTA = re.compile(
    r"[A-ZÀ-Ý0-9](?![a-zà-ÿ])(?:[A-ZÀ-Ý0-9](?![a-zà-ÿ])|[&/.'’() -]){2,120}")


def _nome_caixa_alta(texto: str) -> tuple[str | None, str]:
    """(nome, resto) — o nome em caixa alta que abre o texto, e o que sobra depois dele."""
    bruto = (texto or "").lstrip()
    m = _RE_NOME_CAIXA_ALTA.match(bruto)
    if not m:
        return None, texto or ""
    nome = _limpo(m.group(0))
    if not nome or len(nome) < 3:
        return None, texto or ""
    return nome, bruto[m.end():]


# Quatro palavras minúsculas seguidas: isso é frase, não célula de tabela. Serve de fim de linha
# onde o extrator de PDF emenda a tabela ao parágrafo que vem logo abaixo dela ("… -756.049,27 A
# declaração referente ao ano calendário 2022 apresenta incoerências, como…"); sem esse corte, os
# valores da frase entram na leitura posicional e sobrescrevem os da própria linha.
_RE_TEXTO_CORRIDO = re.compile(r"(?:[a-zà-ÿ-]{2,}[,.;:]?\s+){3}[a-zà-ÿ-]{2,}")


def _ate_o_texto_corrido(texto: str) -> str:
    m = _RE_TEXTO_CORRIDO.search(texto or "")
    return (texto or "")[:m.start()] if m else (texto or "")


def _antes_dos_valores(texto: str) -> str:
    """O trecho anterior ao primeiro valor monetário — onde ainda cabe nome e natureza."""
    m = _RE_VALOR.search(texto or "")
    return (texto or "")[:m.start()] if m else (texto or "")


# ---------------------------------------------------------------------------
# Relacionados — quem aparece ligado ao alvo
# ---------------------------------------------------------------------------
# Sócio, responsável legal, empregador, outorgante/outorgado de procuração, procurador perante a
# RFB e quem transmitiu declaração do mesmo endereço MAC. Todas essas tabelas têm a mesma
# anatomia — documento, nome em caixa alta, uma qualificação e datas —, então o mesmo colhedor
# serve para as seis; o que muda é qual seção é lida e que rótulo o vínculo recebe.

_RELACOES = [
    # (padrão do título da seção, tipo de relação, quem é o documento da linha)
    (r"PARTICIPAC(AO|OES)", "societario"),
    (r"QUADRO SOCIETARIO|RELACAO DE SOCIOS", "societario"),
    (r"VINCULOS EMPREGATICIOS", "empregaticio"),
    (r"OUTORGANTES E OUTORGADOS", "procuracao"),
    (r"ATOS CENSEC", "procuracao"),
    (r"PROCURACOES RFB", "procuracao_rfb"),
]

# "Socio administrador", "Empregado", "Outorgante", "Empresário", "Ativa", "Regular": o que vem
# depois do nome em caixa alta e antes da primeira data ou percentual é a qualificação da parte.
_RE_ATE_DATA_OU_PERCENTUAL = re.compile(r"^(.*?)(?=\d{1,2}/\d{1,2}/\d{4}|-?\d+(?:[.,]\d+)?\s*%|$)", re.DOTALL)

# Sigla de sistema/declaração que aparece na COLUNA SEGUINTE à do nome em algumas tabelas ("…
# RAMON COELHO DOS SANTOS IRPF 2022 Original 2021", "… WESLEY PEREIRA LOURENCO E-CAC 12/04/2022").
# São todas em caixa alta, então o nome não termina sozinho ali — e o nome contaminado é o que
# depois vira rótulo do nó na mesa, onde o estrago aparece.
_RE_SIGLA_DE_SISTEMA = re.compile(r"\b(IRPF|IRPJ|DIRF|ECF|E-CAC|DECRED|DIMOB|DOI|CENSEC|CNIS)\b")


def _sem_sigla_de_sistema(nome: str | None) -> str | None:
    m = _RE_SIGLA_DE_SISTEMA.search(nome or "")
    return _limpo(nome[:m.start()]) if m else _limpo(nome)


def _colher_relacionado(documento: str, resto: str, tp_relacao: str) -> dict | None:
    nome, depois = _nome_caixa_alta(resto)
    nome = _sem_sigla_de_sistema(nome)
    if not nome:
        return None
    m = _RE_ATE_DATA_OU_PERCENTUAL.match(depois)
    qualificacao = _limpo(m.group(1)) if m else None
    datas = _datas(depois)
    percentuais = _RE_PERCENTUAL.findall(depois)
    return {
        "tp_relacao": tp_relacao,
        "no_relacionado": _corta(nome),
        "nu_cpf_cnpj": documento,
        "tx_qualificacao": _corta(qualificacao, 300),
        "dt_inicio": datas[0] if datas else None,
        # a linha traz até três datas (ingresso, retirada e a da situação cadastral) em ordem que
        # varia de tabela para tabela, então só vira "fim" a última que for POSTERIOR ao início —
        # data de situação cadastral antiga não pode virar data de saída. A linha crua fica em
        # TX_CONTEXTO justamente para o investigador conferir quando a leitura estiver apertada.
        "dt_fim": datas[-1] if len(datas) > 1 and datas[-1] > datas[0] else None,
        "pc_participacao": _num(percentuais[-1].replace(".", ",")) if percentuais else None,
        "tx_contexto": _corta(resto, 1000),
    }


def _extrair_relacionados(secoes: list[dict], alvo: dict) -> list[dict]:
    """Os vínculos do alvo, um por linha de tabela reconhecida nas seções de relação."""
    saida = []
    vistos = set()
    for padrao, tp_relacao in _RELACOES:
        for secao in secoes:
            if not secao["numero"].startswith(alvo["nu_secao"] + "."):
                continue
            if not re.search(padrao, _chave(secao["titulo"])):
                continue
            for documento, resto in _registros(secao["texto"], _RE_DOC):
                if documento == alvo["nu_cpf_cnpj"]:
                    continue  # a própria linha do alvo não é um vínculo dele com terceiro
                linha = _colher_relacionado(documento, resto, tp_relacao)
                if not linha:
                    continue
                chave = (tp_relacao, documento, linha["no_relacionado"], linha["dt_inicio"])
                if chave in vistos:
                    continue
                vistos.add(chave)
                saida.append(linha)
    return saida


# "94-DE-80-F7-66-9D 45.7.164.140 23/03/2022 15:09:43 109.472.477-77 RAMON COELHO DOS SANTOS
#  IRPF 2022 Original 2021" — a linha que mostra que dois investigados transmitiram declaração da
# mesma máquina. É o achado mais forte da IPEI para ligar pessoas, então vira vínculo, não texto.

def _extrair_mac(secoes: list[dict], alvo: dict) -> tuple[list[dict], list[dict]]:
    """(vínculos com quem compartilha o MAC, contatos de MAC/IP do próprio alvo)."""
    secao = _achar(secoes, r"ENDERECOS MAC|MAC COINCIDENTES", alvo["nu_secao"])
    if secao is None:
        return [], []
    vinculos, contatos, vistos = [], [], set()
    for mac, resto in _registros(secao["texto"], _RE_MAC):
        ip = _RE_IP.search(resto)
        data = _primeira_data(resto)
        doc = _RE_DOC.search(resto)
        if ip and (mac.upper(), ip.group(0)) not in vistos:
            vistos.add((mac.upper(), ip.group(0)))
            contatos.append({"tp_contato": "mac", "tx_valor": f"{mac.upper()} · {ip.group(0)}",
                             "nu_ano": int(data[:4]) if data else None, "tx_contexto": _corta(resto, 500)})
        if not doc or doc.group(0) == alvo["nu_cpf_cnpj"]:
            continue
        nome, _ = _nome_caixa_alta(resto[doc.end():])
        vinculos.append({
            "tp_relacao": "mac_coincidente",
            # a coluna seguinte à do nome é o tipo de declaração ("… DOS SANTOS IRPF 2022"), e ela
            # também é caixa alta — sem o corte, a sigla vira parte do nome do nó na mesa
            "no_relacionado": _corta(_sem_sigla_de_sistema(nome)),
            "nu_cpf_cnpj": doc.group(0),
            "tx_qualificacao": _corta(f"MAC {mac.upper()}" + (f" · IP {ip.group(0)}" if ip else "")),
            "dt_inicio": data,
            "dt_fim": None,
            "pc_participacao": None,
            "tx_contexto": _corta(resto, 1000),
        })
    return vinculos, contatos


# ---------------------------------------------------------------------------
# Consolidado anual
# ---------------------------------------------------------------------------
# Uma linha por ano-calendário, montada a partir de várias tabelas do relatório. A leitura é
# posicional, mas ALINHADA À DIREITA: a coluna que falta numa linha é sempre a da esquerda (a
# tributação em texto da tabela de pessoa jurídica, por exemplo, não é célula numérica), enquanto
# os totais ficam na ponta direita e são justamente o que interessa. A célula vazia da tabela
# ("-", "x") conta como coluna em _celulas() — sem isso a linha inteira escorregaria.

_COLUNAS_ANO = [
    # (padrão do título, colunas da direita para a esquerda invertidas — ordem real da tabela)
    (r"^RENDIMENTOS DECLARADOS$",
     ["vl_rend_tributaveis", "vl_rend_isentos", "vl_rend_exclusiva", "vl_rend_total"]),
    (r"RENDIMENTOS TRIBUTAVEIS RECEBIDOS DE PESSOAS FISICAS", ["vl_rend_pessoa_fisica"]),
    (r"RENDIMENTOS INFORMADOS EM DIRF",
     ["vl_dirf_trabalho", None, None, None, None, None, None, "vl_dirf_total"]),
    (r"COMPARATIVO ENTRE RENDIMENTOS", ["vl_rend_total", "vl_dirf_total", "vl_ecf_total"]),
    (r"RENDIMENTOS DECLARADOS X MOVIMENTACAO",
     ["vl_rend_tributaveis", "vl_rend_isentos", "vl_rend_exclusiva", "vl_rend_total",
      "vl_mov_creditos", "vl_razao_mov_rend"]),
    (r"MOVIMENTACAO FINANCEIRA X ENTRADAS",
     ["vl_rend_total", "vl_mov_creditos", "vl_razao_mov_rend", "vl_receita_rural",
      "vl_alienacao_imoveis", "vl_alienacao_bens", "vl_dividas_contraidas"]),
    (r"APURACAO DA VARIACAO PATRIMONIAL",
     ["vl_bens_ano", "vl_bens_ano_anterior", "vl_dividas_ano", "vl_dividas_ano_anterior",
      "vl_variacao_patrimonial", "vl_dif_transposicao"]),
    (r"CARTOES DE CREDITO \(DECRED\)|DECRED", ["vl_decred"]),
    (r"NOTAS FISCAIS DE SERVICOS INFORMADAS EM EFD", ["vl_nfs_servicos"]),
    (r"RECEITA BRUTA X MOVIMENTACAO",
     ["vl_receita_bruta", "vl_mov_creditos", "vl_razao_mov_rend", "vl_tributos_pagos"]),
    (r"NOTAS FISCAIS ELETRONICAS", ["vl_nfe_compras", "vl_nfe_vendas"]),
]

CAMPOS_ANO = sorted({c for _, colunas in _COLUNAS_ANO for c in colunas if c})


def _linhas_por_ano(texto: str, documento: str | None) -> list[tuple[int, list]]:
    """[(ano, células)] de uma tabela cuja linha começa pelo CPF/CNPJ do alvo — ou, quando o
    relatório omite o documento (tabelas da pessoa jurídica), pelo próprio ano."""
    saida = []
    if documento and documento in (texto or ""):
        for _, resto in _registros(texto, _ancora_documento(documento)):
            m = _RE_ANO.search(resto)
            if m:
                saida.append((int(m.group(0)), _celulas(_ate_o_texto_corrido(resto[m.end():]))))
        return saida
    # a linha da tabela de pessoa jurídica abre pelo ano e continua na linha de baixo quando a
    # coluna de texto quebra ("2020 Lucro Pre-" / "sumido 4.745.709,00 x x x") — por isso o
    # registro vai do ano até o PRÓXIMO ano, e não até o fim da linha
    for ano, resto in _registros(texto, re.compile(r"(?m)^\s*(?:19|20)\d{2}\b")):
        saida.append((int(_limpo(ano)), _celulas(_ate_o_texto_corrido(resto))))
    return saida


def _extrair_anos(secoes: list[dict], alvo: dict) -> list[dict]:
    anos: dict[int, dict] = {}
    for padrao, colunas in _COLUNAS_ANO:
        for secao in secoes:
            if not secao["numero"].startswith(alvo["nu_secao"] + "."):
                continue
            if not re.search(padrao, _chave(secao["titulo"])):
                continue
            texto = _so_consolidado(secao["texto"])
            for ano, celulas in _linhas_por_ano(texto, alvo["nu_cpf_cnpj"]):
                if not (1990 <= ano <= 2100) or len(celulas) < len(colunas):
                    continue
                linha = anos.setdefault(ano, {"nu_ano": ano, **{c: None for c in CAMPOS_ANO}})
                for coluna, valor in zip(colunas, celulas[-len(colunas):]):
                    if coluna and valor is not None:
                        linha[coluna] = valor
    return [anos[a] for a in sorted(anos)]


_RE_CONSOLIDADO = re.compile(r"CONSOLIDADO POR ANO(.*?)(?:DETALHADO POR ITEM|$)", re.DOTALL | re.IGNORECASE)


def _so_consolidado(texto: str) -> str:
    """A seção de NFe traz o consolidado por ano E o detalhe item a item, e o detalhe tem data e
    valor em toda linha — lido como tabela anual, viraria um ano por nota. Fica só o consolidado."""
    m = _RE_CONSOLIDADO.search(texto or "")
    return m.group(1) if m else (texto or "")


# ---------------------------------------------------------------------------
# Fontes pagadoras e pagamentos declarados
# ---------------------------------------------------------------------------

_FONTES = [
    # (padrão do título, tipo, índice da célula que é o valor principal)
    (r"RENDIMENTOS TRIBUTAVEIS RECEBIDOS DE PESSOAS JURIDICAS", "pessoa_juridica", 0),
    (r"RENDIMENTOS ISENTOS", "isento", -1),
    (r"RENDIMENTOS DE TRIBUTACAO EXCLUSIVA", "exclusiva", -1),
    (r"RELACAO DE PAGAMENTOS DECLARADOS", "pagamento", -1),
    (r"NOTAS FISCAIS DE SERVICOS INFORMADAS EM EFD", "servico_efd", -1),
]


def _extrair_fontes(secoes: list[dict], alvo: dict) -> list[dict]:
    """Uma linha por (ano, fonte pagadora/beneficiário) — de onde veio ou para onde foi o dinheiro
    declarado. A âncora é o CPF do alvo, que abre toda linha dessas tabelas; dentro do registro, o
    PRIMEIRO documento que não é o dele é a contraparte."""
    saida = []
    for padrao, tp_registro, indice in _FONTES:
        for secao in secoes:
            if not secao["numero"].startswith(alvo["nu_secao"] + "."):
                continue
            if not re.search(padrao, _chave(secao["titulo"])):
                continue
            for _, bruto in _registros(secao["texto"], _ancora_documento(alvo["nu_cpf_cnpj"])):
                resto = _ate_o_texto_corrido(bruto)
                ano = _RE_ANO.search(resto)
                contraparte = _RE_DOC.search(resto)
                if not ano:
                    continue
                depois = resto[contraparte.end():] if contraparte else resto[ano.end():]
                nome, natureza = _nome_caixa_alta(_antes_dos_valores(depois))
                celulas = [c for c in _celulas(depois) if c is not None]
                if not nome and not celulas:
                    continue
                saida.append({
                    "tp_registro": tp_registro,
                    "nu_ano": int(ano.group(0)),
                    "no_fonte": _corta(nome),
                    "nu_cpf_cnpj": contraparte.group(0) if contraparte else None,
                    "tx_natureza": _corta(_RE_VALOR.split(natureza or "")[0], 300),
                    "vl_rendimento": celulas[indice] if celulas else None,
                    "vl_imposto": celulas[2] if tp_registro == "pessoa_juridica" and len(celulas) > 2 else None,
                })
    return saida


# ---------------------------------------------------------------------------
# Contas bancárias e movimentação mensal (e-Financeira)
# ---------------------------------------------------------------------------

_TIPOS_CONTA = [
    (r"CONTAS DE DEPOSITO", "deposito"),
    (r"CONTAS DE INVESTIMENTO", "investimento"),
    (r"CONTAS DE SEGURO OU PREVIDENCIA", "previdencia"),
    (r"CONTAS DE CUSTODIA", "custodia"),
    (r"OPERACOES DE CAMBIO", "cambio"),
]


def _extrair_contas(secoes: list[dict], alvo: dict) -> list[dict]:
    """Uma linha por conta/mês: quem declarou (o banco), o número da conta, e crédito, débito e
    saldo do mês.

    A ordem das colunas muda de relatório para relatório — num deles é banco, conta, mês,
    titulares, valores; noutro é banco, mês, conta, tipo de relação, valores —, e o nome do banco
    vem quebrado em cinco linhas. Por isso a leitura não é posicional: o registro é cortado no
    PRIMEIRO valor monetário (à esquerda a identificação, à direita os montantes), e da metade
    esquerda cada campo é reconhecido pela forma — mês é "MM/AAAA", conta é o bloco com mais
    dígitos, titulares é o número de um ou dois dígitos solto no fim, e o que sobra é o banco."""
    saida = []
    for padrao, tp_conta in _TIPOS_CONTA:
        for secao in secoes:
            if not secao["numero"].startswith(alvo["nu_secao"] + "."):
                continue
            if not re.search(padrao, _chave(secao["titulo"])):
                continue
            for _, bruto in _registros(secao["texto"], _ancora_documento(alvo["nu_cpf_cnpj"])):
                linha = _linha_de_conta(_ate_o_texto_corrido(bruto), tp_conta)
                if linha:
                    saida.append(linha)
    return saida


# um bloco de dígitos com pontuação de conta/agência: "0277.101.0000022", "4021.3.77895", "542203"
_RE_TOKEN_CONTA = re.compile(r"\d[\d.\-/]{3,44}")
_RE_TITULARES_FINAL = re.compile(r"(?:^|\s)(\d{1,2})\s*$")
_RE_RELACAO_CONTA = re.compile(r"TITULAR|REPRESENTANTE LEGAL|PROCURADOR|BENEFICIARIO")


def _linha_de_conta(registro: str, tp_conta: str) -> dict | None:
    mes = _RE_MES_ANO.search(registro)
    if not mes:
        return None
    valor = _RE_VALOR.search(registro)
    cabeca = registro[:valor.start()] if valor else registro
    montantes = _celulas(registro[valor.start():]) if valor else []

    # o mês sai da cabeça antes de tudo: ele é "12/2018" e passaria por token de conta
    cabeca = (cabeca[:mes.start()] + " " + cabeca[mes.end():]) if mes.end() <= len(cabeca) else cabeca
    relacao = _RE_RELACAO_CONTA.search(_chave(cabeca))
    titulares = _RE_TITULARES_FINAL.search(cabeca)
    if titulares:
        cabeca = cabeca[:titulares.start()]

    # a conta é o bloco com MAIS dígitos: na linha inteira vêm conta e agência lado a lado
    # ("0277.101.0000022 542203") e é a conta que identifica; o ano solto no começo da linha
    # ("2018 BANESTES SA…") tem quatro dígitos e perde de qualquer número de conta real
    tokens = [t for t in _RE_TOKEN_CONTA.findall(cabeca) if len(_so_digitos(t)) >= 5]
    conta = max(tokens, key=lambda t: len(_so_digitos(t))) if tokens else None

    banco = cabeca
    for token in _RE_TOKEN_CONTA.findall(cabeca):
        banco = banco.replace(token, " ")
    if relacao:
        banco = re.sub(relacao.group(0), " ", banco, flags=re.IGNORECASE)

    return {
        "tp_conta": tp_conta,
        "no_banco": _corta(banco, 500),
        "nu_conta": _corta(conta.strip(" .-/") if conta else None, 100),
        "qt_titulares": int(titulares.group(1)) if titulares else None,
        "nu_ano": int(mes.group(2)),
        "nu_mes": int(mes.group(1)),
        "vl_creditos": montantes[0] if len(montantes) > 0 else None,
        "vl_debitos": montantes[1] if len(montantes) > 1 else None,
        "vl_saldo": montantes[2] if len(montantes) > 2 else None,
        "no_titular": None,
        "nu_titular": None,
        "tx_relacao": _corta(relacao.group(0) if relacao else None, 100),
    }


# ---------------------------------------------------------------------------
# Bens — declarados, veículos, imóveis, embarcações e aeronaves
# ---------------------------------------------------------------------------

_BENS_DECLARADOS = [
    (r"RELACAO DE BENS E DIREITOS DECLARADOS", "declarado"),
    (r"RELACAO DE DIVIDAS E ONUS REAIS", "divida"),
    (r"ALIENACOES E AQUISICOES DE IMOVEIS|OPERACOES IMOBILIARIAS", "imovel"),
    (r"INFORMACOES PRESTADAS EM DIMOB", "imovel_dimob"),
    (r"OPERACOES COM CRIPTOATIVOS", "criptoativo"),
    (r"PAGAMENTOS EM ESPECIE|MOEDA EM ESPECIE", "especie"),
]


def _extrair_bens(secoes: list[dict], alvo: dict) -> list[dict]:
    """Bem por bem: o que o alvo declarou no IRPF (com o valor de cada 31/12), o que o RENAVAM
    registra em nome dele e o que outras bases da RFB apontam como dele."""
    saida = []
    for padrao, tp_bem in _BENS_DECLARADOS:
        for secao in secoes:
            if not secao["numero"].startswith(alvo["nu_secao"] + "."):
                continue
            if not re.search(padrao, _chave(secao["titulo"])):
                continue
            for _, bruto in _registros(secao["texto"], _ancora_documento(alvo["nu_cpf_cnpj"])):
                resto = _ate_o_texto_corrido(bruto)
                ano = _RE_ANO.search(resto)
                depois = resto[ano.end():] if ano else resto
                descricao, _ = _nome_caixa_alta(_antes_dos_valores(depois))
                celulas = _celulas(depois)
                if not descricao and not celulas:
                    continue
                saida.append({
                    "tp_bem": tp_bem,
                    "nu_ano": int(ano.group(0)) if ano else None,
                    "nu_identificador": None,
                    "tx_descricao": _corta(descricao or _limpo(depois), 1000),
                    "vl_ano_anterior": celulas[0] if len(celulas) > 1 else None,
                    "vl_ano": celulas[1] if len(celulas) > 1 else (celulas[0] if celulas else None),
                    "tx_situacao": None,
                    "tx_contexto": _corta(resto, 1000),
                })
    saida.extend(_extrair_veiculos(secoes, alvo))
    saida.extend(_extrair_bens_outros_sistemas(secoes, alvo))
    return saida


def _extrair_veiculos(secoes: list[dict], alvo: dict) -> list[dict]:
    """A linha do RENAVAM abre pela placa nas duas variantes do relatório; o resto da linha
    (marca/modelo, cor, ano, restrições) fica na descrição, e a restrição sai destacada porque
    "ALIENACAO FIDUCIARIA" muda o que dá para constringir."""
    saida = []
    for secao in secoes:
        if not secao["numero"].startswith(alvo["nu_secao"] + "."):
            continue
        if not re.search(r"RENAVAM|VEICULOS REGISTRADOS", _chave(secao["titulo"])):
            continue
        for placa, resto in _registros(secao["texto"], _RE_PLACA):
            limpo = _limpo(re.sub(_RE_DOC.pattern, " ", resto)) or ""
            ano = re.search(r"\b(19|20)\d{2}\b", limpo)
            restricao = re.search(r"(ALIENACAO FIDUCIARIA|NAO HA|ARRENDAMENTO|RESERVA DE DOMINIO)",
                                  _chave(limpo))
            saida.append({
                "tp_bem": "veiculo",
                "nu_ano": int(ano.group(0)) if ano else None,
                "nu_identificador": placa,
                "tx_descricao": _corta(limpo, 1000),
                "vl_ano_anterior": None,
                "vl_ano": None,
                "tx_situacao": _corta(restricao.group(0) if restricao else None, 200),
                "tx_contexto": _corta(resto, 1000),
            })
    return saida


_ROTULOS_OUTROS_BENS = ((r"EMBARCACOES", "embarcacao"), (r"AERONAVES", "aeronave"))


def _extrair_bens_outros_sistemas(secoes: list[dict], alvo: dict) -> list[dict]:
    """A tabela "Bens relevantes informados em outros sistemas" traz, por ano, uma coluna para
    embarcação, uma para aeronave e uma para placa de veículo — e quase sempre só uma delas
    preenchida. É por aí que aparece o carro que não está no nome do alvo no RENAVAM."""
    saida = []
    secao = _achar(secoes, r"BENS (RELEVANTES|CITADOS).*OUTROS SISTEMAS", alvo["nu_secao"])
    if secao is None:
        return saida
    for _, resto in _registros(secao["texto"], _ancora_documento(alvo["nu_cpf_cnpj"])):
        ano = _RE_ANO.search(resto)
        placa = _RE_PLACA.search(resto)
        if not placa and not _limpo(re.sub(r"[-\s]", "", resto)):
            continue
        saida.append({
            "tp_bem": "veiculo_outras_bases" if placa else "outro",
            "nu_ano": int(ano.group(0)) if ano else None,
            "nu_identificador": placa.group(0) if placa else None,
            "tx_descricao": _corta(_limpo(resto), 1000),
            "vl_ano_anterior": None, "vl_ano": None, "tx_situacao": None,
            "tx_contexto": _corta(resto, 1000),
        })
    return saida


# ---------------------------------------------------------------------------
# Notas fiscais eletrônicas do corpo do relatório
# ---------------------------------------------------------------------------

# "26/05/2023 G. B. CARIACICA COMERCIO DE PECAS LTDA 144.558.737-83 LEONARDO MARTINS BIELETA DA
#  BARRA ESTABILIZADORA 2,0000 390,00" — a linha abre pela data de emissão e fecha por
# quantidade (quatro casas) + valor total (duas). O miolo é emitente, destinatário e produto.
_RE_DATA_LINHA = re.compile(r"(?m)^\s*\d{2}/\d{2}/\d{4}\b")
# A quantidade só é lida quando começa depois de um espaço: quando a descrição termina em número
# ("AUTORIZACAO: 202300382824725"), o extrator cola os dois e não há como saber onde um acaba e o
# outro começa — aí fica só o valor total, que é o que a linha tem de confiável.
_RE_QUANTIDADE_VALOR = re.compile(r"(?:(?<=\s)|^)([\d.]{1,10},\d{4})\s*([\d.]{1,15},\d{2})\s*$")
_RE_VALOR_FINAL = re.compile(r"([\d.]{1,15},\d{2})\s*$")


def _extrair_notas_pdf(secoes: list[dict], alvo: dict) -> list[dict]:
    saida = []
    for secao in secoes:
        if not secao["numero"].startswith(alvo["nu_secao"] + "."):
            continue
        if not re.search(r"NOTAS FISCAIS DE TERCEIROS|NOTAS FISCAIS ELETRONICAS", _chave(secao["titulo"])):
            continue
        detalhe = _RE_CONSOLIDADO.sub("", secao["texto"])
        for data, resto in _registros(detalhe, _RE_DATA_LINHA):
            quantidade = _RE_QUANTIDADE_VALOR.search(resto)
            total = quantidade or _RE_VALOR_FINAL.search(resto)
            miolo = resto[:total.start()] if total else resto
            documento = _RE_DOC.search(miolo)
            emitente, _ = _nome_caixa_alta(miolo[:documento.start()] if documento else miolo)
            descricao = _sem_nome_do_alvo(miolo[documento.end():] if documento else miolo, alvo)
            saida.append({
                "tp_origem": "pdf",
                "dt_emissao": _primeira_data(data),
                "nu_nota": None,
                "nu_chave": None,
                "no_emitente": _corta(emitente),
                "nu_cnpj_emitente": None,
                "no_destinatario": _corta(alvo["no_alvo"]),
                "nu_cpf_destinatario": documento.group(0) if documento else alvo["nu_cpf_cnpj"],
                "tx_descricao": _corta(descricao, 1000),
                "qt_item": _num(quantidade.group(1)) if quantidade else None,
                "vl_unitario": None,
                "vl_total": _num(total.group(2 if quantidade else 1)) if total else None,
            })
    return saida


def _sem_nome_do_alvo(texto: str, alvo: dict) -> str | None:
    """Na coluna do destinatário está sempre o próprio alvo, às vezes com um apelido de cadastro
    junto ("LEONARDO MARTINS ZEKEL (LEONARDOMARTINSZEKEL)", "2982925 - LEONARDO MARTINS ZEKEL").
    O que interessa da linha é o produto, então o nome dele sai da frente da descrição.

    Sem fronteira de palavra no fim de cada pedaço, de propósito: o extrator emenda a última
    palavra do nome na primeira da descrição ("ZEKELPLACA MOTO"), e é justamente aí que o corte
    precisa acontecer."""
    resto = re.sub(r"^\s*\d{4,10}\s*-\s*", "", texto or "")
    for palavra in (alvo["no_alvo"] or "").split():
        resto = re.sub(r"^\s*" + re.escape(palavra), "", resto)
    resto = re.sub(r"^\s*\([^)]{0,80}\)", "", resto)
    return _limpo(resto)


# ---------------------------------------------------------------------------
# Endereços, telefones e e-mails de outras bases
# ---------------------------------------------------------------------------


def _extrair_contatos(secoes: list[dict], alvo: dict) -> list[dict]:
    """A seção traz duas tabelas seguidas — endereços numa, telefone + e-mail na outra — e as duas
    começam a linha pelo CPF do alvo e o ano. Quem separa as duas é o próprio conteúdo: registro
    com e-mail é da segunda tabela."""
    saida = []
    secao = _achar(secoes, r"ENDERECOS, NUMEROS DE TELEFONES|TELEFONES E E-MAILS", alvo["nu_secao"])
    if secao is None:
        return saida
    for _, resto in _registros(secao["texto"], _ancora_documento(alvo["nu_cpf_cnpj"])):
        ano = _RE_ANO.search(resto)
        nu_ano = int(ano.group(0)) if ano else None
        depois = resto[ano.end():] if ano else resto
        email = _RE_EMAIL.search(depois)
        if email:
            telefone = _RE_TELEFONE.search(depois[:email.start()])
            if telefone:
                saida.append({"tp_contato": "telefone", "tx_valor": telefone.group(0),
                              "nu_ano": nu_ano, "tx_contexto": None})
            saida.append({"tp_contato": "email", "tx_valor": _corta(email.group(0), 300),
                          "nu_ano": nu_ano, "tx_contexto": None})
            continue
        endereco = _limpo(depois)
        if endereco and len(endereco) > 5:
            saida.append({"tp_contato": "endereco", "tx_valor": _corta(endereco, 1000),
                          "nu_ano": nu_ano, "tx_contexto": None})
    return saida


def _sem_repetidos(linhas: list[dict], campos: tuple[str, ...]) -> list[dict]:
    """A mesma linha pode ser lida duas vezes quando a tabela atravessa a virada de página."""
    vistos, saida = set(), []
    for linha in linhas:
        chave = tuple(linha.get(c) for c in campos)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(linha)
    return saida


# ---------------------------------------------------------------------------
# Análise completa (sem banco) — usada na importação e testável isoladamente
# ---------------------------------------------------------------------------


def analisar_ipei(caminho: Path) -> dict | None:
    """Lê o PDF e devolve {cabecalho, alvos}, com cada alvo carregando as próprias listas; None se
    o PDF não parecer uma IPEI (aí ele segue sendo só documento de texto para a IA)."""
    paginas = paginas_pdf(caminho)
    if not parece_ipei(paginas):
        return None
    secoes = _secoes(paginas)
    alvos = _extrair_alvos(secoes)
    for alvo in alvos:
        relacionados = _extrair_relacionados(secoes, alvo)
        vinculos_mac, contatos_mac = _extrair_mac(secoes, alvo)
        alvo["relacionados"] = _sem_repetidos(
            relacionados + vinculos_mac, ("tp_relacao", "nu_cpf_cnpj", "no_relacionado", "dt_inicio"))
        alvo["anos"] = _extrair_anos(secoes, alvo)
        alvo["fontes"] = _sem_repetidos(
            _extrair_fontes(secoes, alvo), ("tp_registro", "nu_ano", "nu_cpf_cnpj", "vl_rendimento"))
        alvo["contas"] = _sem_repetidos(
            _extrair_contas(secoes, alvo), ("nu_conta", "nu_ano", "nu_mes", "vl_creditos", "vl_debitos"))
        alvo["bens"] = _sem_repetidos(
            _extrair_bens(secoes, alvo), ("tp_bem", "nu_ano", "nu_identificador", "tx_descricao"))
        alvo["notas"] = _sem_repetidos(
            _extrair_notas_pdf(secoes, alvo), ("dt_emissao", "no_emitente", "tx_descricao", "vl_total"))
        alvo["contatos"] = _sem_repetidos(
            _extrair_contatos(secoes, alvo) + contatos_mac, ("tp_contato", "tx_valor", "nu_ano"))
    return {"cabecalho": _extrair_cabecalho(paginas, secoes), "alvos": alvos}


# ---------------------------------------------------------------------------
# Anexo — a planilha que vem junto do PDF
# ---------------------------------------------------------------------------
# O PDF resume ("segue em anexo a planilha completa de todas as notas fiscais", "a relação de
# contas da empresa encontra-se no anexo desse relatório") e a planilha detalha. Por isso ela não
# é um documento à parte: é a continuação das mesmas tabelas, e entra nas mesmas listas.
#
# Os cabeçalhos vêm do gerador de relatórios da Receita e são enormes ("Razao Social ou Nome do
# emitente C01 - Emitente da NFe"), variam entre a planilha de pessoa física e a de pessoa
# jurídica e mudam de aba para aba. Em vez de exigir um layout, cada coluna é reconhecida por um
# pedaço característico do cabeçalho, e é o CONJUNTO de colunas reconhecidas que diz o que a aba
# é — notas fiscais, contas, movimentação ou RENAVAM.

_LIMITE_LINHAS_ANEXO = 50_000  # trava uma planilha gigante de virar meio milhão de INSERTs

_COLUNAS_NOTA = {
    "dt_emissao": ("DATA DE EMISSAO",),
    "nu_nota": ("NUMERO DA NFE",),
    "nu_chave": ("CHAVE DA NFE", "CHAVE DA NF-E"),
    "no_emitente": ("RAZAO SOCIAL OU NOME DO EMITENTE", "NOME EMPRESARIAL ATUAL", "NOME EMPRESARIAL"),
    # "EMITENTE/REMETENTE C01" primeiro, e por uma razão concreta: o prefixo "Emitente NFe :
    # A01 (8 dig)" abre TAMBÉM as colunas de endereço do emitente ("... - End. Logradouro
    # Atual", "... - Munic. Atual"), e como o casamento é por trecho contido, o genérico
    # capturava o logradouro no lugar do documento (visto em produção: CNPJ do emitente
    # gravado como "IBIRACU"). O guarda de formato em _linha_nota_anexo fecha o resto.
    "nu_cnpj_emitente": ("EMITENTE/REMETENTE", "EMITENTE NFE"),
    "no_destinatario": ("DESTINATARIO - NOME", "NOME DO DESTINATARIO", "RAZAO SOCIAL OU NOME DO DESTINATARIO"),
    "nu_cpf_destinatario": ("DESTINATARIO - NI", "DESTINATARIO E01"),
    "tx_descricao": ("ITEM - DESCRICAO", "DESCRICAO DO PRODUTO"),
    "qt_item": ("QUANTIDADE",),
    "vl_unitario": ("VALOR UNITARIO",),
    "vl_total": ("VALOR TOTAL",),
}

_COLUNAS_CONTA = {
    "no_banco": ("DECLARANTE H001",),
    "nu_banco": ("BANCO H200",),
    "nu_agencia": ("AGENCIA H200",),
    "nu_conta": ("NUMERO COMPLETO DA CONTA", "NUMERO DA CONTA"),
    "tp_conta": ("DESCRICAO TIPO DE CONTA", "DESCRICAO SUBTIPO DE CONTA"),
    "qt_titulares": ("NUMERO DE TITULARES",),
    "nu_titular": ("NI DO DECLARADO",),
    "no_titular": ("NOME DO DECLARADO",),
    "tx_relacao": ("TIPO DE RELACAO DO DECLARADO",),
    "nu_ano": ("MES H001",),
    "vl_creditos": ("CREDITO EFETIVO", "CREDITOS LIQUIDOS"),
    "vl_debitos": ("DEBITO EFETIVO", "DEBITOS LIQUIDOS"),
}

_COLUNAS_VEICULO = {
    "nu_identificador": ("PLACA",),
    "nu_renavam": ("RENAVAM",),
    "tx_marca": ("MARCA/MODELO",),
    "tx_cor": ("COR",),
    "tx_tipo": ("TIPO-VEICULO",),
    "nu_ano": ("ANO-MODELO",),
    "nu_documento": ("CNPJ/CPF",),
    "tx_situacao": ("RESTRICOES",),
    "nu_chassi": ("CHASSI",),
}


def _mapear_colunas(cabecalho: list, mapa: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """{campo: índice da coluna} — o cabeçalho é longo e prolixo, então basta conter o trecho."""
    chaves = [_chave(str(c or "")) for c in cabecalho]
    achados: dict[str, int] = {}
    for campo, trechos in mapa.items():
        # os trechos vão do mais específico para o mais genérico e essa ordem tem que valer sobre
        # a ordem das colunas: senão "Numero da conta" (que vem antes na planilha) ganharia de
        # "Numero completo da conta", que é a que identifica a conta de ponta a ponta
        for trecho in trechos:
            for i, chave in enumerate(chaves):
                if i not in achados.values() and trecho in chave:
                    achados[campo] = i
                    break
            if campo in achados:
                break
    return achados


def _celula(linha: list, indice: int | None):
    if indice is None or indice >= len(linha):
        return None
    valor = linha[indice]
    return None if valor is None or str(valor).strip() in ("", "<N/D>", "-") else valor


def _texto_celula(linha: list, colunas: dict, campo: str, limite: int = 3000) -> str | None:
    return _corta(str(_celula(linha, colunas.get(campo)) or "") or None, limite)


def _numero_celula(linha: list, colunas: dict, campo: str) -> float | None:
    valor = _celula(linha, colunas.get(campo))
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    # a planilha mistura os dois formatos: "1.773.851,31" numa aba e "1773851.31" na outra
    if "," in texto:
        return _num(texto)
    try:
        return float(texto)
    except ValueError:
        return None


def _data_celula(linha: list, colunas: dict, campo: str) -> str | None:
    valor = _celula(linha, colunas.get(campo))
    if valor is None:
        return None
    if hasattr(valor, "strftime"):
        return valor.strftime("%Y-%m-%d")
    return _primeira_data(str(valor)) or (str(valor)[:10] if re.match(r"\d{4}-\d{2}-\d{2}", str(valor)) else None)


def _ano_celula(linha: list, colunas: dict, campo: str) -> int | None:
    valor = _celula(linha, colunas.get(campo))
    m = _RE_ANO.search(str(valor or ""))
    return int(m.group(0)) if m else None


def _abas_planilha(caminho: Path) -> list[tuple[str, list[list]]]:
    """[(nome da aba, linhas)] de um .xlsx ou de um .csv (uma aba só)."""
    if caminho.suffix.lower() == ".csv":
        import csv

        from funcoes.documentos_utils import _detectar_encoding

        dados = caminho.read_bytes()
        texto = dados.decode(_detectar_encoding(dados), errors="replace")
        dialeto = csv.Sniffer().sniff(texto[:4000], delimiters=";,\t") if texto.strip() else csv.excel
        return [(caminho.stem, list(csv.reader(texto.splitlines(), dialeto))[:_LIMITE_LINHAS_ANEXO])]

    import openpyxl

    livro = openpyxl.load_workbook(str(caminho), read_only=True, data_only=True)
    try:
        abas = []
        for aba in livro.worksheets:
            linhas = []
            for i, linha in enumerate(aba.iter_rows(values_only=True)):
                if i >= _LIMITE_LINHAS_ANEXO:
                    break
                linhas.append(list(linha))
            abas.append((aba.title, linhas))
        return abas
    finally:
        livro.close()


_MIN_COLUNAS_CABECALHO = 3


def _linha_de_cabecalho(linhas: list[list], mapa: dict) -> tuple[int, dict[str, int]] | None:
    """A planilha às vezes abre com duas linhas de título do relatório antes do cabeçalho real
    ("Modelo Analítico Dinâmico de …"), então o cabeçalho é procurado nas primeiras linhas."""
    for i, linha in enumerate(linhas[:10]):
        colunas = _mapear_colunas(linha, mapa)
        if len(colunas) >= _MIN_COLUNAS_CABECALHO:
            return i, colunas
    return None


def analisar_anexo(caminho: Path) -> dict:
    """{notas, contas, bens} lidos da planilha de anexo, aba por aba. Aba que não for reconhecida
    como nenhuma das três é ignorada aqui — ela continua indo inteira para a IA como texto do
    documento, que é o tratamento que ela já teria sozinha."""
    saida: dict[str, list[dict]] = {"notas": [], "contas": [], "bens": []}
    for _, linhas in _abas_planilha(caminho):
        if not linhas:
            continue
        for mapa, leitor, destino in (
            (_COLUNAS_NOTA, _linha_nota_anexo, "notas"),
            (_COLUNAS_CONTA, _linha_conta_anexo, "contas"),
            (_COLUNAS_VEICULO, _linha_veiculo_anexo, "bens"),
        ):
            achado = _linha_de_cabecalho(linhas, mapa)
            if achado is None:
                continue
            inicio, colunas = achado
            for linha in linhas[inicio + 1:]:
                if not any(c is not None and str(c).strip() for c in linha):
                    continue
                registro = leitor(list(linha), colunas)
                if registro:
                    saida[destino].append(registro)
            break  # cada aba é de um tipo só
    return saida


def _documento_emitente(linha: list, colunas: dict) -> str | None:
    """Documento do emitente da NF-e, só quando o valor TEM cara de documento.

    A planilha da RFB dá a RAIZ do CNPJ (8 dígitos, "19111420"), não o CNPJ inteiro — os dois
    formatos valem. O que não vale é texto: o cabeçalho do anexo repete o prefixo "Emitente
    NFe" em meia dúzia de colunas de endereço, e sem este guarda o campo acaba guardando um
    logradouro ou um município como se fosse o documento do emitente."""
    valor = _texto_celula(linha, colunas, "nu_cnpj_emitente", 100)
    digitos = re.sub(r"\D", "", valor or "")
    return valor if len(digitos) in (8, 14) else None


def _linha_nota_anexo(linha: list, colunas: dict) -> dict | None:
    descricao = _texto_celula(linha, colunas, "tx_descricao", 1000)
    emitente = _texto_celula(linha, colunas, "no_emitente")
    if not descricao and not emitente:
        return None
    return {
        "tp_origem": "anexo",
        "dt_emissao": _data_celula(linha, colunas, "dt_emissao"),
        "nu_nota": _texto_celula(linha, colunas, "nu_nota", 100),
        "nu_chave": _texto_celula(linha, colunas, "nu_chave", 100),
        "no_emitente": emitente,
        "nu_cnpj_emitente": _documento_emitente(linha, colunas),
        "no_destinatario": _texto_celula(linha, colunas, "no_destinatario"),
        "nu_cpf_destinatario": _texto_celula(linha, colunas, "nu_cpf_destinatario", 100),
        "tx_descricao": descricao,
        "qt_item": _numero_celula(linha, colunas, "qt_item"),
        "vl_unitario": _numero_celula(linha, colunas, "vl_unitario"),
        "vl_total": _numero_celula(linha, colunas, "vl_total"),
    }


def _nome_do_declarante(linha: list, colunas: dict) -> str | None:
    """O banco que declarou a conta ocupa DUAS colunas com o mesmo cabeçalho na planilha — o CNPJ
    dele e o nome, nessa ordem. Reconhecida a primeira, o nome é a coluna logo ao lado."""
    indice = colunas.get("no_banco")
    valor = _texto_celula(linha, colunas, "no_banco", 500)
    if valor and _RE_DOC.fullmatch(valor) and indice is not None:
        vizinho = _celula(linha, indice + 1)
        return _corta(str(vizinho), 500) if vizinho else valor
    return valor


def _linha_conta_anexo(linha: list, colunas: dict) -> dict | None:
    conta = _texto_celula(linha, colunas, "nu_conta", 100)
    titular = _texto_celula(linha, colunas, "nu_titular", 100)
    if not conta and not titular:
        return None
    agencia = _texto_celula(linha, colunas, "nu_agencia", 20)
    return {
        "tp_conta": _texto_celula(linha, colunas, "tp_conta", 100) or "anexo",
        "no_banco": _nome_do_declarante(linha, colunas),
        "nu_conta": conta or (f"{agencia}/{titular}" if agencia else titular),
        "qt_titulares": int(_numero_celula(linha, colunas, "qt_titulares") or 0) or None,
        "nu_ano": _ano_celula(linha, colunas, "nu_ano"),
        "nu_mes": None,  # o anexo consolida por ano; o mês a mês é o do corpo do PDF
        "vl_creditos": _numero_celula(linha, colunas, "vl_creditos"),
        "vl_debitos": _numero_celula(linha, colunas, "vl_debitos"),
        "vl_saldo": None,
        "no_titular": _texto_celula(linha, colunas, "no_titular"),
        "nu_titular": titular,
        "tx_relacao": _texto_celula(linha, colunas, "tx_relacao", 100),
    }


def _linha_veiculo_anexo(linha: list, colunas: dict) -> dict | None:
    placa = _texto_celula(linha, colunas, "nu_identificador", 20)
    if not placa:
        return None
    descricao = " · ".join(p for p in (
        _texto_celula(linha, colunas, "tx_marca", 200),
        _texto_celula(linha, colunas, "tx_cor", 60),
        _texto_celula(linha, colunas, "tx_tipo", 60),
        f"RENAVAM {_texto_celula(linha, colunas, 'nu_renavam', 30)}" if colunas.get("nu_renavam") is not None else None,
        f"chassi {_texto_celula(linha, colunas, 'nu_chassi', 40)}" if colunas.get("nu_chassi") is not None else None,
    ) if p)
    return {
        "tp_bem": "veiculo",
        "nu_ano": _ano_celula(linha, colunas, "nu_ano"),
        "nu_identificador": placa,
        "tx_descricao": _corta(descricao, 1000),
        "vl_ano_anterior": None,
        "vl_ano": None,
        "tx_situacao": _texto_celula(linha, colunas, "tx_situacao", 200),
        "tx_contexto": _texto_celula(linha, colunas, "nu_documento", 100),
    }


# ---------------------------------------------------------------------------
# Gravação — chamada logo depois de anexar o PDF como documento do inquérito
# ---------------------------------------------------------------------------

_EXTENSOES_ANEXO = (".xlsx", ".csv")

_COLUNAS_TB04 = ["nu_ano"] + CAMPOS_ANO


def _inserir(cur, tabela: str, colunas: list[str], linhas: list[dict], fixos: dict) -> int:
    """INSERT em lote de uma lista de dicionários; devolve quantas linhas entraram."""
    if not linhas:
        return 0
    nomes = list(fixos) + colunas
    marcadores = ", ".join(["%s"] * len(nomes))
    valores = [tuple(fixos.values()) + tuple(linha.get(c) for c in colunas) for linha in linhas]
    cur.executemany(
        f"INSERT INTO investigacao.{tabela} ({', '.join(nomes)}) VALUES ({marcadores})", valores)
    return len(valores)


def extrair_ipei(no_arquivo: str, id_inquerito: int, id_documento: int | None,
                 id_usuario: int | None) -> dict | None:
    """Analisa o arquivo de data/ e grava a IPEI com seus alvos e listas. Devolve o resumo da
    extração, ou None se o arquivo não for uma IPEI — nesse caso ele segue sendo só texto para a
    IA. Chamada logo depois de anexar_documento_inquerito, que já validou o nome do arquivo
    (mesmo padrão de extrair_inquerito, para o IPL, e de extrair_irpf).

    A planilha de anexo entra por aqui também: não sendo PDF, ela é oferecida a `vincular_anexo`,
    que a costura na IPEI já importada em vez de criar uma segunda — é o que faz o PDF e o anexo
    virarem um card só."""
    caminho = resolver_em_dados(no_arquivo)
    if caminho is None:
        return None
    if caminho.suffix.lower() in _EXTENSOES_ANEXO:
        return vincular_anexo(no_arquivo, id_inquerito, id_documento)
    if caminho.suffix.lower() != ".pdf":
        return None

    analise = analisar_ipei(caminho)
    if analise is None:
        return None
    cabecalho, alvos = analise["cabecalho"], analise["alvos"]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ipe_tb01_ipeis
                    (id_inquerito, id_documento, no_arquivo, nu_ipei, dt_ipei, no_unidade,
                     nu_processo_judicial, dt_decisao, tx_conclusao, tx_encaminhamento, tx_anexos,
                     no_assinantes, qt_folhas, qt_paginas, qt_alvos, id_usuario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_ipei
                """,
                (id_inquerito, id_documento, caminho.name, cabecalho["nu_ipei"], cabecalho["dt_ipei"],
                 cabecalho["no_unidade"], cabecalho["nu_processo_judicial"], cabecalho["dt_decisao"],
                 cabecalho["tx_conclusao"], cabecalho["tx_encaminhamento"], cabecalho["tx_anexos"],
                 cabecalho["no_assinantes"], cabecalho["qt_folhas"], cabecalho["qt_paginas"],
                 len(alvos), id_usuario),
            )
            id_ipei = cur.fetchone()[0]
            contagens = _gravar_alvos(cur, id_ipei, alvos)
            _atualizar_contagens(cur, id_ipei)
        conn.commit()

    resumo = {"id_ipei": id_ipei, "nu_ipei": cabecalho["nu_ipei"], "qt_alvos": len(alvos), **contagens}
    # o anexo pode ter sido importado ANTES do PDF (a tela manda os arquivos escolhidos em ordem
    # alfabética, e "VT20230001 Anexo …" vem depois de "VT20230001 - NOME…" só por sorte do nome)
    resumo.update(_absorver_anexo_pendente(id_inquerito, id_ipei, cabecalho["nu_ipei"]))
    return resumo


def _gravar_alvos(cur, id_ipei: int, alvos: list[dict]) -> dict:
    contagens = {"qt_relacionados": 0, "qt_contas": 0, "qt_bens": 0, "qt_notas": 0, "qt_contatos": 0}
    for alvo in alvos:
        cur.execute(
            """
            INSERT INTO investigacao.ipe_tb02_alvos
                (id_ipei, tp_pessoa, no_alvo, nu_cpf_cnpj, no_mae, dt_nascimento, dt_inscricao,
                 dt_baixa, tx_endereco, no_municipio, sg_uf, nu_cep, tx_ocupacao,
                 tx_situacao_cadastral, vl_capital_social, tx_sexo, tx_nacionalidade, fl_obito,
                 no_contador, nu_secao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_alvo
            """,
            (id_ipei, alvo["tp_pessoa"], alvo["no_alvo"], alvo["nu_cpf_cnpj"], alvo["no_mae"],
             alvo["dt_nascimento"], alvo["dt_inscricao"], alvo["dt_baixa"], alvo["tx_endereco"],
             alvo["no_municipio"], alvo["sg_uf"], alvo["nu_cep"], alvo["tx_ocupacao"],
             alvo["tx_situacao_cadastral"], alvo["vl_capital_social"], alvo["tx_sexo"],
             alvo["tx_nacionalidade"], alvo["fl_obito"], alvo["no_contador"], alvo["nu_secao"]),
        )
        id_alvo = cur.fetchone()[0]
        fixos = {"id_ipei": id_ipei, "id_alvo": id_alvo}
        contagens["qt_relacionados"] += _inserir(
            cur, "ipe_tb03_relacionados",
            ["tp_relacao", "no_relacionado", "nu_cpf_cnpj", "tx_qualificacao", "dt_inicio",
             "dt_fim", "pc_participacao", "tx_contexto"], alvo["relacionados"], fixos)
        _inserir(cur, "ipe_tb04_anos", _COLUNAS_TB04, alvo["anos"], fixos)
        _inserir(cur, "ipe_tb05_fontes",
                 ["tp_registro", "nu_ano", "no_fonte", "nu_cpf_cnpj", "tx_natureza",
                  "vl_rendimento", "vl_imposto"], alvo["fontes"], fixos)
        contagens["qt_contas"] += _inserir(cur, "ipe_tb06_contas", _COLUNAS_CONTAS, alvo["contas"], fixos)
        contagens["qt_bens"] += _inserir(cur, "ipe_tb07_bens", _COLUNAS_BENS, alvo["bens"], fixos)
        contagens["qt_notas"] += _inserir(cur, "ipe_tb08_notas_fiscais", _COLUNAS_NOTAS, alvo["notas"], fixos)
        contagens["qt_contatos"] += _inserir(
            cur, "ipe_tb09_contatos", ["tp_contato", "tx_valor", "nu_ano", "tx_contexto"],
            alvo["contatos"], fixos)
    return contagens


_COLUNAS_CONTAS = ["tp_conta", "no_banco", "nu_conta", "qt_titulares", "nu_ano", "nu_mes",
                   "vl_creditos", "vl_debitos", "vl_saldo", "no_titular", "nu_titular", "tx_relacao"]
_COLUNAS_BENS = ["tp_bem", "nu_ano", "nu_identificador", "tx_descricao", "vl_ano_anterior",
                 "vl_ano", "tx_situacao", "tx_contexto"]
_COLUNAS_NOTAS = ["tp_origem", "dt_emissao", "nu_nota", "nu_chave", "no_emitente",
                  "nu_cnpj_emitente", "no_destinatario", "nu_cpf_destinatario", "tx_descricao",
                  "qt_item", "vl_unitario", "vl_total"]


def _atualizar_contagens(cur, id_ipei: int) -> None:
    """Reconta as linhas filhas direto do banco — o anexo pode chegar depois e mexer nos números."""
    cur.execute(
        """
        UPDATE investigacao.ipe_tb01_ipeis SET
            qt_alvos        = (SELECT count(*) FROM investigacao.ipe_tb02_alvos WHERE id_ipei = %(id)s),
            qt_relacionados = (SELECT count(*) FROM investigacao.ipe_tb03_relacionados WHERE id_ipei = %(id)s),
            qt_contas       = (SELECT count(*) FROM investigacao.ipe_tb06_contas WHERE id_ipei = %(id)s),
            qt_bens         = (SELECT count(*) FROM investigacao.ipe_tb07_bens WHERE id_ipei = %(id)s),
            qt_notas        = (SELECT count(*) FROM investigacao.ipe_tb08_notas_fiscais WHERE id_ipei = %(id)s),
            qt_contatos     = (SELECT count(*) FROM investigacao.ipe_tb09_contatos WHERE id_ipei = %(id)s)
        WHERE id_ipei = %(id)s
        """,
        {"id": id_ipei},
    )


# ---------------------------------------------------------------------------
# Costura entre o PDF e a planilha de anexo
# ---------------------------------------------------------------------------
# Os dois arquivos trazem o número da IPEI no nome ("VT20230001 - LEONARDO….pdf" e "VT20230001
# Anexo LEONARDO… .xlsx"), e é só isso que precisa bater. A ordem de importação não importa: se o
# PDF já entrou, o anexo se encaixa nele; se o anexo veio antes, ele fica como documento comum e
# o PDF o recolhe ao ser importado.

_RE_NUMERO_NO_NOME = re.compile(r"\b([A-Z]{2})\s*(\d{8,10})\b", re.IGNORECASE)


def numero_ipei_do_nome(nome_arquivo: str) -> str | None:
    """"VT20230001 Anexo LEONARDO … .xlsx" -> "VT20230001"."""
    m = _RE_NUMERO_NO_NOME.search(Path(nome_arquivo or "").name)
    return f"{m.group(1).upper()}{m.group(2)}" if m else None


def vincular_anexo(no_arquivo: str, id_inquerito: int, id_documento: int | None) -> dict | None:
    """Liga uma planilha à IPEI de mesmo número já importada no inquérito e grava as linhas dela.
    None quando não há a que ligar — aí a planilha segue sendo um documento comum."""
    caminho = resolver_em_dados(no_arquivo)
    nu_ipei = numero_ipei_do_nome(no_arquivo)
    if caminho is None or not nu_ipei:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_ipei FROM investigacao.ipe_tb01_ipeis
                WHERE id_inquerito = %s AND nu_ipei = %s AND id_documento_anexo IS NULL
                ORDER BY id_ipei LIMIT 1
                """,
                (id_inquerito, nu_ipei),
            )
            linha = cur.fetchone()
            if linha is None:
                return None
            id_ipei = linha[0]
            resumo = _gravar_anexo(cur, id_ipei, caminho, no_arquivo, id_documento)
        conn.commit()
    return {"id_ipei": id_ipei, "nu_ipei": nu_ipei, "anexo": True, **resumo}


def _absorver_anexo_pendente(id_inquerito: int, id_ipei: int, nu_ipei: str | None) -> dict:
    """Recolhe a planilha que já tinha sido anexada como documento comum antes do PDF chegar."""
    if not nu_ipei:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id_documento, d.no_arquivo
                FROM investigacao.inq_tb04_documentos d
                WHERE d.id_inquerito = %s
                  AND lower(d.no_arquivo) ~ '[.](xlsx|csv)$'
                  AND NOT EXISTS (SELECT 1 FROM investigacao.ipe_tb01_ipeis i
                                  WHERE i.id_documento_anexo = d.id_documento)
                ORDER BY d.id_documento
                """,
                (id_inquerito,),
            )
            candidatos = [(doc, nome) for doc, nome in cur.fetchall()
                          if numero_ipei_do_nome(nome) == nu_ipei]
            if not candidatos:
                return {}
            id_documento, nome = candidatos[0]
            caminho = resolver_em_dados(nome)
            if caminho is None:
                return {}
            resumo = _gravar_anexo(cur, id_ipei, caminho, nome, id_documento)
        conn.commit()
    return resumo


def _gravar_anexo(cur, id_ipei: int, caminho: Path, no_arquivo: str, id_documento: int | None) -> dict:
    """Lê a planilha e distribui as linhas entre os alvos da IPEI: cada nota fiscal e cada conta
    vai para o alvo cujo CPF/CNPJ ela cita; o que não cita ninguém reconhecível fica com o
    primeiro alvo, que na IPEI de pessoa física é o único que existe."""
    cur.execute(
        "SELECT id_alvo, nu_cpf_cnpj FROM investigacao.ipe_tb02_alvos WHERE id_ipei = %s ORDER BY id_alvo",
        (id_ipei,),
    )
    alvos = cur.fetchall()
    if not alvos:
        return {}
    por_documento = {_so_digitos(doc): id_alvo for id_alvo, doc in alvos if doc}
    padrao = alvos[0][0]

    def _alvo_de(*textos) -> int:
        for texto in textos:
            id_alvo = por_documento.get(_so_digitos(texto))
            if id_alvo:
                return id_alvo
        return padrao

    try:
        conteudo = analisar_anexo(caminho)
    except Exception:  # planilha protegida, corrompida ou num formato inesperado
        # o anexo não pode derrubar a importação do PDF, que é a parte principal — mas o traceback
        # fica no log, senão a planilha some em silêncio e ninguém sabe por quê
        logger.warning("Não foi possível ler o anexo %s da IPEI %s", no_arquivo, id_ipei, exc_info=True)
        return {}

    for nota in conteudo["notas"]:
        nota["_id_alvo"] = _alvo_de(nota.get("nu_cpf_destinatario"), nota.get("nu_cnpj_emitente"))
    for conta in conteudo["contas"]:
        conta["_id_alvo"] = _alvo_de(conta.get("nu_titular"))
    for bem in conteudo["bens"]:
        bem["_id_alvo"] = _alvo_de(bem.get("tx_contexto"))

    for tabela, colunas, linhas in (
        ("ipe_tb08_notas_fiscais", _COLUNAS_NOTAS, conteudo["notas"]),
        ("ipe_tb06_contas", _COLUNAS_CONTAS, conteudo["contas"]),
        ("ipe_tb07_bens", _COLUNAS_BENS, conteudo["bens"]),
    ):
        for id_alvo in {linha["_id_alvo"] for linha in linhas}:
            _inserir(cur, tabela, colunas, [l for l in linhas if l["_id_alvo"] == id_alvo],
                     {"id_ipei": id_ipei, "id_alvo": id_alvo})

    cur.execute(
        """
        UPDATE investigacao.ipe_tb01_ipeis
        SET id_documento_anexo = %s, no_arquivo_anexo = %s
        WHERE id_ipei = %s
        """,
        (id_documento, Path(no_arquivo).name, id_ipei),
    )
    _atualizar_contagens(cur, id_ipei)
    return {"qt_notas_anexo": len(conteudo["notas"]), "qt_contas_anexo": len(conteudo["contas"]),
            "qt_bens_anexo": len(conteudo["bens"])}


# ---------------------------------------------------------------------------
# Consultas (bloco no card + página ipei.html) e remoção
# ---------------------------------------------------------------------------


def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _consultar(query: str, params: tuple) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _linhas(cur)


def listar_ipeis_inquerito(id_inquerito: int) -> list[dict]:
    """As IPEIs do inquérito, para os blocos do card de investigação."""
    return _consultar(
        """
        SELECT i.id_ipei, i.id_documento, i.id_documento_anexo, i.no_arquivo, i.no_arquivo_anexo,
               i.nu_ipei, i.dt_ipei, i.no_unidade, i.nu_processo_judicial, i.dt_decisao,
               i.qt_folhas, i.qt_alvos, i.qt_relacionados, i.qt_contas, i.qt_bens, i.qt_notas,
               i.qt_contatos, i.dt_extracao, u.no_usuario AS extraido_por,
               ap.no_apelido,
               -- hash do PDF (e da planilha de anexo) na origem, quando o documento os guarda
               d.co_hash_md5, d.co_hash_sha1, d.co_hash_sha256,
               da.co_hash_md5 AS co_hash_md5_anexo, da.co_hash_sha1 AS co_hash_sha1_anexo,
               da.co_hash_sha256 AS co_hash_sha256_anexo,
               (SELECT string_agg(a.no_alvo, ' · ' ORDER BY a.id_alvo)
                  FROM investigacao.ipe_tb02_alvos a WHERE a.id_ipei = i.id_ipei) AS no_alvos
        FROM investigacao.ipe_tb01_ipeis i
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = i.id_usuario
        LEFT JOIN investigacao.inq_tb04_documentos d ON d.id_documento = i.id_documento
        LEFT JOIN investigacao.inq_tb04_documentos da ON da.id_documento = i.id_documento_anexo
        -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
        LEFT JOIN investigacao.inq_tb06_apelidos ap
               ON ap.tp_prova = 'ipei' AND ap.id_prova = i.id_ipei
        WHERE i.id_inquerito = %s
        ORDER BY i.nu_ipei, i.id_ipei
        """,
        (id_inquerito,),
    )


def obter_ipei(id_ipei: int) -> dict | None:
    linhas = _consultar(
        """
        SELECT i.*, u.no_usuario AS extraido_por
        FROM investigacao.ipe_tb01_ipeis i
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = i.id_usuario
        WHERE i.id_ipei = %s
        """,
        (id_ipei,),
    )
    return linhas[0] if linhas else None


def obter_id_inquerito_de_ipei(id_ipei: int) -> int | None:
    linhas = _consultar(
        "SELECT id_inquerito FROM investigacao.ipe_tb01_ipeis WHERE id_ipei = %s", (id_ipei,))
    return linhas[0]["id_inquerito"] if linhas else None


def listar_alvos_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        "SELECT * FROM investigacao.ipe_tb02_alvos WHERE id_ipei = %s ORDER BY id_alvo", (id_ipei,))


def listar_relacionados_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT r.*, a.no_alvo, a.nu_cpf_cnpj AS nu_cpf_cnpj_alvo
        FROM investigacao.ipe_tb03_relacionados r
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = r.id_alvo
        WHERE r.id_ipei = %s
        ORDER BY r.tp_relacao, r.no_relacionado
        """,
        (id_ipei,),
    )


def listar_anos_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT n.*, a.no_alvo, a.nu_cpf_cnpj AS nu_cpf_cnpj_alvo
        FROM investigacao.ipe_tb04_anos n
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = n.id_alvo
        WHERE n.id_ipei = %s
        ORDER BY a.id_alvo, n.nu_ano
        """,
        (id_ipei,),
    )


def listar_fontes_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT f.*, a.no_alvo
        FROM investigacao.ipe_tb05_fontes f
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = f.id_alvo
        WHERE f.id_ipei = %s
        ORDER BY f.nu_ano, f.vl_rendimento DESC NULLS LAST
        """,
        (id_ipei,),
    )


def listar_contas_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT c.*, a.no_alvo
        FROM investigacao.ipe_tb06_contas c
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = c.id_alvo
        WHERE c.id_ipei = %s
        ORDER BY c.nu_conta, c.nu_ano, c.nu_mes
        """,
        (id_ipei,),
    )


def listar_bens_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT b.*, a.no_alvo
        FROM investigacao.ipe_tb07_bens b
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = b.id_alvo
        WHERE b.id_ipei = %s
        ORDER BY b.tp_bem, b.nu_ano, b.id_bem
        """,
        (id_ipei,),
    )


_LIMITE_NOTAS = 5000  # a planilha de anexo passa de três mil notas; a tela pagina o resto


def listar_notas_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT n.*, a.no_alvo
        FROM investigacao.ipe_tb08_notas_fiscais n
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = n.id_alvo
        WHERE n.id_ipei = %s
        ORDER BY n.dt_emissao DESC NULLS LAST, n.id_nota
        LIMIT %s
        """,
        (id_ipei, _LIMITE_NOTAS),
    )


def listar_contatos_ipei(id_ipei: int) -> list[dict]:
    return _consultar(
        """
        SELECT c.*, a.no_alvo
        FROM investigacao.ipe_tb09_contatos c
        JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = c.id_alvo
        WHERE c.id_ipei = %s
        ORDER BY c.tp_contato, c.nu_ano, c.tx_valor
        """,
        (id_ipei,),
    )


_TABELAS_FILHAS_IPEI = ("ipe_tb03_relacionados", "ipe_tb04_anos", "ipe_tb05_fontes",
                        "ipe_tb06_contas", "ipe_tb07_bens", "ipe_tb08_notas_fiscais",
                        "ipe_tb09_contatos")


def apagar_ipei(id_ipei: int, apagar_documento: bool = True) -> bool:
    """Remove uma IPEI extraída e suas linhas filhas. Por padrão leva junto os documentos de texto
    que alimentam a IA — o PDF e a planilha vieram no mesmo clique. False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_documento, id_documento_anexo FROM investigacao.ipe_tb01_ipeis WHERE id_ipei = %s",
                (id_ipei,))
            linha = cur.fetchone()
            if linha is None:
                return False
            documentos = [d for d in linha if d is not None]
            for tabela in _TABELAS_FILHAS_IPEI:
                cur.execute(f"DELETE FROM investigacao.{tabela} WHERE id_ipei = %s", (id_ipei,))
            cur.execute("DELETE FROM investigacao.ipe_tb02_alvos WHERE id_ipei = %s", (id_ipei,))
            cur.execute("DELETE FROM investigacao.ipe_tb01_ipeis WHERE id_ipei = %s", (id_ipei,))
            # As anotações da mesa apontam para a linha por TP_ORIGEM/ID_ORIGEM, sem FK
            # (vínculo polimórfico): sem esta limpeza, a evidência do nó fica pendurada
            # num ID morto — e o ID é IDENTITY, então a próxima importação o reaproveita
            # e a evidência passa a apontar para OUTRO registro (ver limpar_anotacoes_orfas).
            limpar_anotacoes_orfas(cur, "ipei_alvo", "ipei_relacionado", "ipei_conta",
                                   "ipei_bem", "ipei_nota", "ipei_contato")
            if apagar_documento and documentos:
                cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = ANY(%s)",
                            (documentos,))
        conn.commit()
    return True
