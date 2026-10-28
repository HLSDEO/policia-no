"""Telefonia — o que a operadora devolve quando o juiz defere a quebra de sigilo telefônico.

Diferente do IPL, do IRPF e da IPEI, aqui não chega **um** arquivo: chega uma pasta com o que a
operadora conseguiu montar, e cada pedaço vem no seu formato:

    cadastro   · "DadosCadastrais - FULANO VIVO.txt" — relatório de pesquisa por CPF/linha, em
                 texto de largura fixa, com uma ficha por linha (cliente, endereço, modalidade,
                 situação, habilitação/rescisão)
    consulta   · "consulta_<linha>_id<protocolo>_<uf>.xlsx" — a planilha do ofício, com uma aba
                 por assunto: Relatório de chamadas (o CDR), Endereço das ERBs, Resumo dos
                 Números (número + IMEI), Histórico de Chamadas WiFi e Histórico de Chamadas TuGO
    o mesmo    · "consulta_....pdf" — o gêmeo impresso da planilha, que é o que costuma ser
                 juntado aos autos

**Tudo isso é um card só.** É o ponto central desta importação e a diferença em relação à IPEI:
lá o par PDF+planilha se acha pelo número da IPEI no nome dos dois arquivos; aqui a quantidade de
arquivos é aberta (as ERBs de um período grande vêm quebradas em vários), e nada garante um
identificador comum entre eles. Por isso a chave do card é o par **(inquérito, operadora)**:
todo arquivo VIVO do inquérito 123 cai no mesmo card "Telefonia — VIVO", em qualquer ordem, e
outra operadora abre outro card. Cada arquivo fica registrado em TEL_TB02 com o que ele trouxe,
para o investigador saber a procedência de cada linha.

Como o mesmo material chega repetido (a planilha e o PDF dela; ERBs que se repetem entre
períodos), **toda linha filha carrega TX_CHAVE**, o sha256 do conteúdo normalizado, com índice
único por card. Reimportar o mesmo arquivo, ou importar o PDF depois da planilha, não duplica
nada — o INSERT bate no índice e é descartado. É isso que deixa "vários arquivos, um card só"
funcionar sem ordem de importação e sem casamento de nome.

O PDF é lido pelo mesmo caminho da planilha, e não por um parser à parte: `pypdf` entrega a
tabela com as colunas vazias colapsadas, então as células são reconstruídas por tipo (15 dígitos
é IMEI, 10-13 é telefone, hh:mm:ss é hora) em vez de por posição. Quando a planilha também
entrou, as linhas coincidem e o TX_CHAVE descarta — o PDF sozinho ainda rende o CDR.

O exemplo acima é da VIVO; cada operadora exporta o mesmo material num leiaute próprio, e é por
isso que nada é lido por posição. Já mapeados: **VIVO** (.xlsx/.pdf do ofício), **Oi/TNL PCS**
(.xls que na verdade é HTML), **TIM** (.xls binário, um arquivo por tipo — VOZ/SMS/GPRS, sem CGI:
a antena é sintetizada de SiteID+SetorID) e **Claro/Vigia** (.xlsx, o alvo em "CD Mobile" e o
sentido em "Tipo de ligação IC"). A seção sai do título da aba, de uma linha de marcador ou do
próprio cabeçalho; a operadora, do nome, do timbre ou da assinatura de colunas (a TIM não se
nomeia). Há ainda o **extrato de antena da TIM** ("Relatório de Eventos por (ERB)", um PDF por
antena): um CDR invertido — todas as chamadas que passaram por uma ERB no período — com três
seções (as chamadas, os procedimentos de assinante e a tabela de antenas com coordenada que põe a
ERB no mapa), parseado por `analisar_eventos_erb_tim`, à parte do ofício. Ver docs/telefonia.md
para o mapa por operadora e as armadilhas de cada formato.

Além da voz, a operadora devolve o **relatório de dados móveis** — as sessões de dados da linha
(que antena atendeu, com que IP/IMEI, em que intervalo, com que volumes). É a seção "dados"
(`TEL_TB08_DADOS_MOVEIS`, aba "Dados móveis" da tela), lida de dois jeitos: com cabeçalho
(`LOCALI/CODIPS/NUMLIN/...`, às vezes com as colunas `ERB_*` de endereço da antena no fim) ou
sem cabeçalho nenhum (o extrato por CGI, só linhas de dados — aí a leitura é posicional, e a
forma da linha é conferida por amostragem antes). A antena da sessão completa TEL_TB05 pelo CGI,
e a operadora, quando nome e timbre calam, sai do MNC do CGI por maioria (`_MNC_OPERADORA`).

Ver db/init/43_telefonia.sql, rotas/investigacao.py e a página web telefonia.html.
"""

import hashlib
import logging
import re
import unicodedata
from datetime import date, datetime, time
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import limpar_anotacoes_orfas
from funcoes.organizacao_arquivos import resolver as resolver_em_dados
from funcoes.pdf_texto import paginas_pdf

logger = logging.getLogger(__name__)

# Teto por arquivo. Um CDR de um ano de uma linha movimentada passa de 100 mil linhas; acima
# disso a importação síncrona da tela deixaria o navegador pendurado, e o corte é preferível ao
# timeout (a contagem do bloco diz quantas linhas entraram).
LIMITE_LINHAS_ARQUIVO = 200_000


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))


def _chave(texto: str) -> str:
    """Rótulo/título comparável: sem acento, sem pontuação, minúsculo, espaço único."""
    return re.sub(r"[^a-z0-9]+", " ", _sem_acento(str(texto or "")).lower()).strip()


def _limpo(valor) -> str | None:
    """Texto aparado, ou None quando a célula é vazia/só espaços (o que a planilha usa como nulo)."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _digitos(valor) -> str | None:
    """Só os dígitos — "(67) 99806-5096" vira "67998065096", "118.331.367-56" vira o CPF puro."""
    if valor is None:
        return None
    numeros = re.sub(r"\D", "", str(valor))
    return numeros or None


_RE_DATA_BR = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
# ano de dois dígitos ("26/09/17" no cadastro do alvo da Claro) — "26" vira 2026, e não 1926,
# porque é data de ativação de linha, sempre recente. Vem depois do padrão de quatro dígitos.
_RE_DATA_BR2 = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")
_RE_DATA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# AAAAMMDD colado, o formato de data da TIM ("20181201"). O ano é exigido em 19xx/20xx para não
# confundir com um telefone ou um protocolo de oito dígitos.
_RE_DATA_COMPACTA = re.compile(r"^\s*(19|20)(\d{2})(\d{2})(\d{2})\s*$")
_RE_HORA = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\b")
# HHMMSS colado, a hora da TIM ("110645" -> 11:06:45). HHMM (quatro dígitos) também é aceito.
_RE_HORA_COMPACTA = re.compile(r"^\s*([01]\d|2[0-3])([0-5]\d)([0-5]\d)?\s*$")


def _data(valor) -> str | None:
    """Data em ISO a partir do que a célula tiver: datetime do openpyxl, "23/12/2024", ISO ou o
    AAAAMMDD colado da TIM."""
    if valor is None:
        return None
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%Y-%m-%d")
    texto = str(valor)
    m = _RE_DATA_BR.search(texto)
    if m:
        dia, mes, ano = (int(g) for g in m.groups())
        try:
            return date(ano, mes, dia).isoformat()
        except ValueError:
            return None
    m = _RE_DATA_ISO.search(texto)
    if m:
        return m.group(0)
    m = _RE_DATA_COMPACTA.match(texto)
    if m:
        ano, mes, dia = int(m.group(1) + m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            return date(ano, mes, dia).isoformat()
        except ValueError:
            return None
    m = _RE_DATA_BR2.search(texto)
    if m:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
        try:
            return date(ano, mes, dia).isoformat()
        except ValueError:
            return None
    return None


def _hora(valor) -> str | None:
    """"10:39:38" -> "10:39:38". Aceita o time do openpyxl e o HHMMSS colado da TIM ("110645")."""
    if valor is None:
        return None
    if isinstance(valor, (datetime, time)):
        return valor.strftime("%H:%M:%S")
    texto = str(valor)
    m = _RE_HORA.search(texto)
    if m:
        return "%02d:%02d:%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m = _RE_HORA_COMPACTA.match(texto)
    if m:
        return "%02d:%02d:%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    return None


def _data_hora(valor_data, valor_hora) -> str | None:
    """As duas colunas separadas do CDR viradas num timestamp — é por ele que a tela ordena.

    Quando a data já vem com a hora embutida (a "Data do Evento" da Claro é um datetime), ela
    manda: a coluna de hora separada não existe nesse formato."""
    if isinstance(valor_data, datetime) and valor_hora is None:
        return valor_data.strftime("%Y-%m-%d %H:%M:%S")
    dia = _data(valor_data)
    if not dia:
        return None
    return f"{dia} {_hora(valor_hora) or '00:00:00'}"


def _data_hora_combinada(texto) -> str | None:
    """Timestamp de uma célula só que já traz data e hora juntas ("24/01/2019 01:26:30" — o
    extrato de antena da TIM não separa as duas em colunas)."""
    dia = _data(texto)
    if not dia:
        return None
    m = _RE_HORA.search(str(texto))
    hora = "%02d:%02d:%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else "00:00:00"
    return f"{dia} {hora}"


_RE_DURACAO_HMS = re.compile(r"^\s*(\d{1,3}):([0-5]?\d):([0-5]?\d)\s*$")


def _duracao_segundos(valor) -> int | None:
    """Duração em segundos. A Oi manda "hh:mm:ss" ("00:01:28" -> 88); TIM e Claro já mandam o
    número de segundos direto."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    texto = str(valor).strip()
    m = _RE_DURACAO_HMS.match(texto)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    numeros = _digitos(texto)
    return int(numeros) if numeros else None


# Latitude/longitude da planilha de ERB vêm em grau-minuto-segundo com o sinal colado e o
# separador repetido: "-24-30-44,73" é 24 graus 30 minutos 44,73 segundos Sul. Alguns registros
# trocam a vírgula por ponto ("-9-33-50.2"), e há operadora que já manda decimal — as três formas
# passam por aqui.
_RE_DMS = re.compile(r"^\s*(-?)\s*(\d{1,3})[-:\s]+(\d{1,2})[-'\s]+(\d{1,2}(?:[.,]\d+)?)")
_RE_DECIMAL = re.compile(r"^\s*(-?\d{1,3}[.,]\d+)\s*$")


def _coordenada(valor) -> float | None:
    """Grau decimal a partir do DMS colado da operadora, ou do decimal quando já vier assim."""
    texto = _limpo(valor)
    if not texto:
        return None
    # o relatório de dados marca texto com apóstrofo (ex.: "'-19-23-49,60'"): é formatação da
    # planilha, não parte da coordenada
    texto = texto.strip("'\"").strip()
    if not texto:
        return None
    m = _RE_DECIMAL.match(texto)
    if m:
        grau = float(m.group(1).replace(",", "."))
        return grau if -180 <= grau <= 180 else None
    m = _RE_DMS.match(texto)
    if not m:
        return None
    sinal = -1 if m.group(1) == "-" else 1
    grau = int(m.group(2)) + int(m.group(3)) / 60 + float(m.group(4).replace(",", ".")) / 3600
    grau *= sinal
    return round(grau, 7) if -180 <= grau <= 180 else None


def _chave_linha(*partes) -> str:
    """sha256 do conteúdo normalizado da linha — é o que impede a mesma linha de entrar duas vezes
    quando o mesmo material chega pela planilha e pelo PDF, ou num segundo arquivo do período."""
    bruto = "|".join("" if p is None else str(p).strip().upper() for p in partes)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Operadora
# ---------------------------------------------------------------------------
# A operadora é a chave do card, então ela precisa sair de QUALQUER um dos arquivos: o TXT de
# cadastro não se identifica em lugar nenhum (só o nome do arquivo diz "VIVO"), e a planilha traz
# a razão social no cabeçalho do ofício ("Ref. 405928/2024 - TBRA - VIVO SP"). Por isso a busca
# roda no nome do arquivo E no texto, nessa ordem de prioridade.

OPERADORAS = [
    ("VIVO", r"\bVIVO\b|\bTELEFONICA\b|\bTBRA\b"),
    # "vigia-template" é o nome do sistema de interceptação da Claro no título das abas; sem \b ao
    # final porque o que vem depois é "_1842" e o sublinhado não abre fronteira de palavra
    ("CLARO", r"\bCLARO\b|\bEMBRATEL\b|\bNET\s*SERVICOS\b|\bNEXTEL\b|\bVIGIA[\s_-]*TEMPLATE"),
    ("TIM", r"\bTIM\b|\bINTELIG\b"),
    ("OI", r"\bOI\s*(?:MOVEL|FIXO|S\.?\s?A)?\b|\bTELEMAR\b|\bBRASIL\s*TELECOM\b|\bTNL\s*PCS\b"),
    ("ALGAR", r"\bALGAR\b|\bCTBC\b"),
    ("SERCOMTEL", r"\bSERCOMTEL\b"),
    ("SURF", r"\bSURF\s*TELECOM\b"),
    ("DATORA", r"\bDATORA\b|\bLINKS\s*FIELD\b"),
    ("AMERICA NET", r"\bAMERICA\s*NET\b"),
]

OPERADORA_DESCONHECIDA = "Não identificada"


def detectar_operadora(*textos) -> str:
    """A operadora dona do arquivo, procurada em nome e conteúdo. `OPERADORA_DESCONHECIDA` quando
    nada bate — o card existe do mesmo jeito, e o investigador renomeia depois se quiser."""
    for texto in textos:
        alvo = _sem_acento(str(texto or "")).upper()
        if not alvo:
            continue
        for nome, padrao in OPERADORAS:
            if re.search(padrao, alvo):
                return nome
    return OPERADORA_DESCONHECIDA


# ---------------------------------------------------------------------------
# Cabeçalho do ofício (planilha e PDF)
# ---------------------------------------------------------------------------
# As três primeiras linhas de toda aba repetem o mesmo cabeçalho:
#     SEGREDO DE JUSTIÇA
#     Relatório de chamadas originadas/recebidas   Ref. 405928/2024 - TBRA - VIVO SP
#     Linha:13997653347   Período:01/12/2024 00:00:00 a 07/01/2025 15:37:09   Data Solicitação:07/01/2025

# "Ref." / "Ref:" / "Referência:" — o \b depois exige que "Ref" termine ali, senão "referentes à
# informação solicitada" (o texto de aba vazia do extrato de antena) casaria e viraria referência
_RE_REFERENCIA = re.compile(r"\bRef(?:er[êe]ncia)?\b\.?\s*:?\s*(\S.{0,80}?)\s*$", re.IGNORECASE)
# "Linha:" (VIVO) e "Número do terminal:" (Oi) nomeiam a mesma coisa — a linha investigada.
_RE_LINHA_ALVO = re.compile(
    r"(?:Linha|N[uú]mero\s+do\s+terminal)\s*:\s*(\d[\d\s().-]{7,20})", re.IGNORECASE)
# o período vem com " a " (VIVO/Oi), " - " (Claro: "01/12/2019 - 28/12/2019") ou " até " (o
# extrato de antena da TIM: "24/01/2019 00:00:00 até 24/01/2019 23:59:59")
_RE_PERIODO = re.compile(
    r"Per[ií]odo[^:]*:\s*(\d{2}/\d{2}/\d{4})[^\n]*?\s(?:at[ée]|a|-)\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE)
_RE_SOLICITACAO = re.compile(r"Solicita[cç][aã]o\s*:\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)


def _extrair_cabecalho(textos: list[str]) -> dict:
    """Referência do ofício, linha investigada e período, de onde quer que apareçam primeiro."""
    junto = "\n".join(t for t in textos if t)
    referencia = None
    for linha in junto.splitlines():
        m = _RE_REFERENCIA.search(linha)
        if m:
            referencia = m.group(1).strip()
            break
    periodo = _RE_PERIODO.search(junto)
    alvo = _RE_LINHA_ALVO.search(junto)
    solicitacao = _RE_SOLICITACAO.search(junto)
    return {
        "nu_referencia": referencia,
        "nu_linha_alvo": _digitos(alvo.group(1)) if alvo else None,
        "dt_inicio_periodo": _data(periodo.group(1)) if periodo else None,
        "dt_fim_periodo": _data(periodo.group(2)) if periodo else None,
        "dt_solicitacao": _data(solicitacao.group(1)) if solicitacao else None,
    }


# ---------------------------------------------------------------------------
# Cadastro de linhas (o .txt de largura fixa)
# ---------------------------------------------------------------------------
# Cada ficha é um bloco de linhas "* RÓTULO:....................VALOR *", fechado por uma linha
# só de pontos. O que abre uma ficha nova é o rótulo "NÚMERO DA LINHA" — e não o separador,
# porque a operadora quebra a ficha quando ela não cabe:
#
#     * DATA HABILITAÇÃO:...........02/10/2023 *
#     * ..........................................*
#
#     * DATA RESCISÃO:...............15/07/2024 *      <- ainda é a MESMA linha telefônica
#
# Como o separador não fecha nada, o rescisão órfão cai na ficha certa em vez de virar uma ficha
# vazia. Os rótulos do cabeçalho do relatório ("Data:", "Hora:", o CPF do parâmetro de consulta)
# aparecem antes da primeira ficha e por isso não pegam carona em nenhuma.

_RE_CAMPO_CADASTRO = re.compile(r"^\*\s*([^:*]{2,40}?)\s*:\s*\.*\s*(.*?)\s*\*\s*$")

# rótulo normalizado -> campo da ficha. Os apelidos cobrem o que muda de operadora para operadora
# ("NOME" no lugar de "CLIENTE", "CIDADE" no lugar de "MUNICÍPIO", "PLANO" no lugar de
# "MODALIDADE") sem exigir um parser por operadora.
_CAMPOS_CADASTRO = {
    "numero da linha": "nu_linha",
    "linha": "nu_linha",
    "telefone": "nu_linha",
    "cliente": "no_cliente",
    "nome": "no_cliente",
    "nome do cliente": "no_cliente",
    "razao social": "no_cliente",
    "cpf": "nu_cpf_cnpj",
    "cnpj": "nu_cpf_cnpj",
    "cpf cnpj": "nu_cpf_cnpj",
    "rg": "nu_rg",
    "identidade": "nu_rg",
    "nome da mae": "no_mae",
    "mae": "no_mae",
    "data nascimento": "dt_nascimento",
    "data de nascimento": "dt_nascimento",
    "endereco": "tx_endereco",
    "complemento": "tx_complemento",
    "bairro": "no_bairro",
    "cep": "nu_cep",
    "municipio": "no_municipio",
    "cidade": "no_municipio",
    "estado": "sg_uf",
    "uf": "sg_uf",
    "modalidade": "tp_modalidade",
    "plano": "tp_modalidade",
    "situacao": "tp_situacao",
    "status": "tp_situacao",
    "data habilitacao": "dt_habilitacao",
    "data de habilitacao": "dt_habilitacao",
    "data ativacao": "dt_habilitacao",
    "data rescisao": "dt_rescisao",
    "data de rescisao": "dt_rescisao",
    "data cancelamento": "dt_rescisao",
}

_CAMPOS_DATA_CADASTRO = {"dt_habilitacao", "dt_rescisao", "dt_nascimento"}
_CAMPOS_DIGITOS_CADASTRO = {"nu_linha", "nu_cpf_cnpj", "nu_cep"}


def _pares_cadastro(texto: str):
    """(campo, valor bruto) de cada linha "* RÓTULO:...VALOR *" reconhecida no arquivo."""
    for linha in (texto or "").splitlines():
        m = _RE_CAMPO_CADASTRO.match(linha)
        if not m:
            continue
        campo = _CAMPOS_CADASTRO.get(_chave(m.group(1)))
        if campo:
            yield campo, m.group(2)


def parece_cadastro_telefonia(texto: str) -> bool:
    """Diz se o texto é um relatório de cadastro de linhas de operadora.

    O critério é a ficha em si — pelo menos um "NÚMERO DA LINHA" com dígitos e mais dois campos
    reconhecidos —, e não o timbre: cada operadora escreve um cabeçalho diferente, mas todas
    entregam a mesma ficha emoldurada por asteriscos."""
    tem_linha = False
    reconhecidos = 0
    for campo, valor in _pares_cadastro(texto):
        reconhecidos += 1
        if campo == "nu_linha" and _digitos(valor):
            tem_linha = True
    return tem_linha and reconhecidos >= 3


def analisar_cadastro(texto: str) -> list[dict]:
    """As fichas do relatório de cadastro, uma por linha telefônica encontrada."""
    fichas: list[dict] = []
    atual: dict | None = None
    for campo, valor in _pares_cadastro(texto):
        if campo == "nu_linha":
            numero = _digitos(valor)
            if not numero:
                continue
            atual = {"nu_linha": numero}
            fichas.append(atual)
            continue
        if atual is None:
            # cabeçalho do relatório e parâmetros da consulta, antes da primeira ficha
            continue
        if campo in _CAMPOS_DATA_CADASTRO:
            atual[campo] = _data(valor)
        elif campo in _CAMPOS_DIGITOS_CADASTRO:
            atual[campo] = _digitos(valor)
        else:
            atual[campo] = _limpo(valor)
    for ficha in fichas:
        ficha["tx_chave"] = _chave_linha(
            ficha.get("nu_linha"), ficha.get("no_cliente"), ficha.get("nu_cpf_cnpj"),
            ficha.get("tx_endereco"), ficha.get("dt_habilitacao"), ficha.get("dt_rescisao"),
            ficha.get("tp_modalidade"), ficha.get("tp_situacao"))
    return fichas


# ---------------------------------------------------------------------------
# Seções do ofício — as mesmas na planilha (uma aba cada) e no PDF (uma página cada)
# ---------------------------------------------------------------------------
# TuGO e WiFi vêm ANTES do genérico "relatorio de chamadas" porque os títulos delas também contêm
# "chamadas": "Histórico de Chamadas TuGO" bateria no padrão errado se a ordem fosse outra.

SECOES = [
    ("tugo", "historico de chamadas tugo"),
    ("wifi", "historico de chamadas wifi"),
    ("erbs", "endereco das erbs"),
    # a Claro entrega as ERBs num arquivo à parte ("Relatório de Antenas"); é a mesma seção
    ("erbs", "relatorio de antenas"),
    ("numeros", "resumo dos numeros"),
    # a Oi separa o CDR em três blocos pelo sentido (efetuadas/recebidas/encaminhadas) e a Claro
    # chama o dela de "Eventos QDS Chamada Voz" — todos caem na seção de chamadas; o sentido, quem
    # é o alvo e o interlocutor, cada leitor resolve pelo seu jeito
    ("chamadas", "chamadas efetuadas"),
    ("chamadas", "chamadas recebidas"),
    ("chamadas", "chamadas encaminhadas"),
    ("chamadas", "chamada voz"),
    ("chamadas", "relatorio de chamadas"),
]


def _secao_de(titulo: str) -> str | None:
    """Qual seção do ofício é este título de aba (ou de página do PDF)."""
    normalizado = _chave(titulo)
    for secao, padrao in SECOES:
        if padrao in normalizado:
            return secao
    return None


# O sentido do CDR da Oi está no título do bloco, não numa coluna: "Chamadas Efetuadas" diz que o
# alvo é o chamador (a coluna traz só "Ligou para"), "Recebidas" que ele é o chamado.
def _marcador_secao(celulas: list) -> tuple[str | None, str | None]:
    """(seção, sentido) quando a linha é um título de bloco (Oi/Claro), (None, None) caso contrário.

    Título de bloco é uma linha de poucas células cujo texto casa com um padrão de seção — nunca
    uma linha de dados, que tem a tabela inteira preenchida."""
    preenchidas = [c for c in celulas if _limpo(c)]
    if not (1 <= len(preenchidas) <= 3):
        return None, None
    texto = _chave(" ".join(str(c) for c in preenchidas))
    secao = None
    for candidata, padrao in SECOES:
        if padrao in texto:
            secao = candidata
            break
    if secao is None:
        return None, None
    if "efetuada" in texto or "originada" in texto:
        return secao, "efetuada"
    if "recebida" in texto or "recebeu" in texto:
        return secao, "recebida"
    if "encaminhada" in texto:
        return secao, "encaminhada"
    return secao, None


# Cabeçalho da tabela de cada seção: rótulo normalizado -> campo. Quem manda é o NOME da coluna,
# nunca a posição: a mesma seção chega com colunas a mais ou a menos conforme a operadora e
# conforme o que o ofício pediu.
_COLUNAS_CHAMADAS = {
    "data": "data", "data chamada": "data", "data da chamada": "data",
    "hora": "hora", "hora inicio": "hora",
    "chamador": "nu_chamador", "origem": "nu_chamador",
    "imei chamador": "nu_imei_chamador", "imei origem": "nu_imei_chamador",
    "chamado": "nu_chamado", "destino": "nu_chamado",
    "imei chamado": "nu_imei_chamado", "imei destino": "nu_imei_chamado",
    "hora desc": "hr_desconexao", "hora desconexao": "hr_desconexao",
    "durac": "qt_duracao_segundos", "duracao": "qt_duracao_segundos",
    "status": "cd_status",
    "tra": "tp_trafego", "trafego": "tp_trafego",
    "servico": "no_servico",
    "gmt origem": "nu_gmt_origem", "gmt destino": "nu_gmt_destino", "gmt": "nu_gmt_origem",
    "local origem": "nu_cgi_origem", "local destino": "nu_cgi_destino",
    "erb origem": "nu_cgi_origem", "erb destino": "nu_cgi_destino",
    "tec origem": "tp_tecnologia_origem", "tec destino": "tp_tecnologia_destino",
    "eot a": "tx_eot_a", "eot b": "tx_eot_b",
    "ip origem": "tx_ip_origem", "ip destino": "tx_ip_destino",
    "pais origem": "no_pais_origem", "pais destino": "no_pais_destino",
}

# "Sigla" aparece DUAS vezes no cabeçalho das ERBs: a primeira é a central que atende a antena
# ("CORE PS") e a segunda é a sigla da própria ERB ("TRGZ1"). O leitor de cabeçalho numera a
# repetição ("sigla 2"), e é por esse nome que a segunda entra aqui.
_COLUNAS_ERBS = {
    "cgi": "nu_cgi",
    "ccc": "nu_ccc",
    "sigla": "no_central", "sigla 2": "sg_erb",
    "erb": "no_erb",
    "set": "nu_setor", "setor": "nu_setor",
    "azi": "nu_azimute", "azimute": "nu_azimute",
    "tecnologia": "tp_tecnologia",
    "latitude": "tx_latitude", "longitude": "tx_longitude",
    "uf": "sg_uf", "estado": "sg_uf",
    "cidade": "no_municipio", "municipio": "no_municipio",
    "bairro": "no_bairro",
    "endereco": "tx_endereco", "rua": "tx_endereco",
}

# --- CDR da Oi (TNL PCS), HTML disfarçado de .xls ---------------------------
# O bloco traz, na mesma linha, a chamada + a ERB que a atendeu + o cadastro do titular. O alvo
# não está na linha: vem do cabeçalho ("Número do terminal:") e é injetado pelo sentido do bloco.
_COLUNAS_OI = {
    "data": "data", "hora": "hora",
    "ligou para": "nu_chamado", "recebeu de": "nu_chamador",
    "duracao": "qt_duracao_segundos",
    "imei": "nu_imei_alvo",  # IMEI do aparelho do alvo; o lado (chamador/chamado) vem do sentido
    "cgi": "nu_cgi",
    "endereco erb": "erb_endereco", "bairro erb": "erb_bairro",
    "cidade erb": "erb_municipio", "uf erb": "erb_uf",
    "latitude": "erb_latitude", "longitude": "erb_longitude", "azimute": "erb_azimute",
    # cadastro do titular (o primeiro bloco de endereço; os repetidos ganham sufixo e são ignorados)
    "cliente titular": "cad_nome", "cpf cnpj": "cad_cpf",
    "logradouro": "cad_endereco", "bairro": "cad_bairro", "municipio": "cad_municipio",
    "uf": "cad_uf", "cep": "cad_cep",
}

# --- CDR da Claro (template "Vigia"), .xlsx ---------------------------------
# O sentido está na coluna "Tipo de ligação IC" (OUTGOING/INCOMMING); "CD Mobile" é o alvo e
# "Telefone de Destino" é o outro lado. A antena é só o CGI — as coordenadas vêm do arquivo de
# antenas e completam a ERB pelo mesmo CGI.
_COLUNAS_CLARO = {
    "antena": "nu_cgi",
    "cd mobile": "nu_alvo",
    "data do evento": "data",
    "gmt": "nu_gmt_origem",
    "telefone de destino": "nu_outro",
    "duracao da ligacao s": "qt_duracao_segundos",
    "imei origem": "nu_imei_alvo",
    "tipo de ligacao ic": "cd_sentido",
    "nome do alvo": "cad_nome", "cpf cnpj do alvo": "cad_cpf", "numero do alvo": "cad_numero",
    "rg ie do alvo": "cad_rg", "imsi do alvo": "cad_imsi", "endereco do alvo": "cad_endereco",
    "nome do interlocutor": "int_nome", "cpf cnpj do interlocutor": "int_cpf",
    "numero do interlocutor": "int_numero", "rg ie do interlocutor": "int_rg",
    "endereco do interlocutor": "int_endereco",
}

# --- CDR da TIM, .xls binário -----------------------------------------------
# Duas ERBs por chamada: RE (a que recebeu) e RS (a que saiu). A TIM não manda CGI, manda
# SiteID+SetorID — o CGI sintético que sai daí é o que casa a chamada com a antena no join.
_COLUNAS_TIM = {
    "numero a": "nu_chamador", "imei a": "nu_imei_chamador",
    "numero b": "nu_chamado", "imei b": "nu_imei_chamado",
    "data inicio": "data", "hora inicio": "hora",
    "duracao": "qt_duracao_segundos",
    "status": "cd_status", "central": "no_central",
    "endereco erb re": "re_endereco", "municipio erb re": "re_municipio",
    "latitude re": "re_latitude", "longitude re": "re_longitude", "azimute re": "re_azimute",
    "siteid re": "re_site", "setorid re": "re_setor",
    "endereco erb rs": "rs_endereco", "municipio erb rs": "rs_municipio",
    "latitude rs": "rs_latitude", "longitude rs": "rs_longitude", "azimute rs": "rs_azimute",
    "siteid rs": "rs_site", "setorid rs": "rs_setor",
}

# --- GPRS da TIM ------------------------------------------------------------
# Sessão de dados. ATENÇÃO: o único exemplo em mãos veio com o conteúdo embaralhado na origem
# (cabeçalho íntegro, dados corrompidos) — o mapa existe para um arquivo são, mas não foi validado
# contra dado real. Ver docs/telefonia.md.
_COLUNAS_TIM_GPRS = {
    "numero a": "nu_linha",
    "endereco ip": "tx_ip",
    "data inicio": "data_inicio", "hora inicio": "hora_inicio",
    "data fim": "data_fim", "hora fim": "hora_fim",
}

_COLUNAS_NUMEROS = {
    "numero": "nu_telefone", "telefone": "nu_telefone", "linha": "nu_telefone",
    "imei": "nu_imei",
}

_COLUNAS_WIFI = {
    "linha": "nu_linha",
    "ip": "tx_ip",
    "porta": "nu_porta",
    "data ini": "data_inicio", "data inicio": "data_inicio",
    "hora ini": "hora_inicio", "hora inicio": "hora_inicio",
    "even ini": "tp_evento_inicio", "evento ini": "tp_evento_inicio",
    "data fim": "data_fim",
    "hora fim": "hora_fim",
    "even fim": "tp_evento_fim", "evento fim": "tp_evento_fim",
    "trafego": "tp_trafego",
    "servico": "no_servico",
}

# --- Relatório de dados móveis (sessões de dados: CGI + IP + volumes por intervalo) --------
# O cabeçalho usa os códigos do relatório (LOCALI é o CGI, CODIPS o IP, NUMLIN a linha, CODIME
# o IMEI). "locali" aparece DUAS vezes (a segunda repete a primeira) e "filler" é enchimento —
# nenhum dos dois entra no mapa. TIPASS/STAREQ são constantes nos exemplos em mãos e não são
# guardados (ver docs/telefonia.md); todo o resto vai para TEL_TB08 como a operadora escreve.
_COLUNAS_DADOS = {
    "locali": "nu_cgi",
    "codips": "tx_ip",
    "numlin": "nu_linha",
    "coride": "nu_coride",
    "dtaini": "data_inicio", "horini": "hora_inicio", "eveini": "tp_evento_inicio",
    "dtafim": "data_fim", "horfim": "hora_fim", "evefim": "tp_evento_fim",
    "siteid": "nu_site",
    "volent": "qt_vol_entrada", "volsai": "qt_vol_saida", "univol": "tp_unidade",
    "tiptra": "tp_trafego", "tipser": "tp_servico",
    "tecnol": "tp_tecnologia", "tiploc": "tp_rede",
    "codime": "nu_imei",
    "codope": "nu_codope",
    "tipiac": "tp_tipo_acesso", "oricha": "tp_origem",
    "estado": "sg_uf",
    "ipv6": "tx_ipv6",
    # endereço da antena no fim da linha, quando o relatório traz (hc_dados.xlsx traz; o extrato
    # por CGI traz UF/cidade/bairro/endereço/lat/long sem cabeçalho, nas mesmas posições)
    "erb estado": "erb_uf", "erb cidade": "erb_municipio", "erb bairro": "erb_bairro",
    "erb enderb": "erb_endereco", "erb latitu": "erb_latitude",
    "erb longit": "erb_longitude", "erb azimut": "erb_azimute",
}

# o trio que prova que o cabeçalho é de dados móveis — sem ele, é outra tabela qualquer
_CHAVE_CABECALHO_DADOS = {"nu_linha", "tx_ip", "nu_cgi"}

# o extrato por CGI não tem cabeçalho: a coluna é a posição. Os índices seguem a ordem do
# relatório (LOCALI=0, CODIPS=1, NUMLIN=2, ...); as extras de endereço (30-36) são opcionais.
_MAPA_POSICIONAL_DADOS = {
    "nu_cgi": 0, "tx_ip": 1, "nu_linha": 2, "nu_coride": 3,
    "data_inicio": 4, "hora_inicio": 5, "tp_evento_inicio": 6,
    "data_fim": 7, "hora_fim": 8, "tp_evento_fim": 9,
    "nu_site": 11, "qt_vol_entrada": 13, "qt_vol_saida": 14, "tp_unidade": 15,
    "tp_trafego": 17, "tp_servico": 18, "tp_tecnologia": 20, "tp_rede": 21,
    "nu_imei": 23, "nu_codope": 24, "tp_tipo_acesso": 26, "tp_origem": 27,
    "sg_uf": 28, "tx_ipv6": 29,
    "erb_uf": 30, "erb_municipio": 31, "erb_bairro": 32, "erb_endereco": 33,
    "erb_latitude": 34, "erb_longitude": 35, "erb_azimute": 36,
}

# MCC-MNC brasileiro → operadora, para o relatório de dados que não se nomeia em lugar nenhum
# (só o CGI diz de que rede ele saiu). Mapeados os MNCs consolidados; o resto cai em
# OPERADORA_DESCONHECIDA — o card existe do mesmo jeito.
_MNC_OPERADORA = {
    "02": "TIM",
    "03": "CLARO", "04": "CLARO",
    "05": "VIVO", "06": "VIVO", "10": "VIVO", "11": "VIVO",
    "07": "ALGAR",
    "16": "OI", "31": "OI",
}
_RE_MCC_MNC = re.compile(r"^\s*(\d{3})\s*[-.]\s*(\d{1,3})\s*[-.]")

# A operadora responde "não há registro" em vez de devolver a aba vazia; a frase muda de seção
# para seção, mas sempre começa assim.
_RE_SEM_REGISTRO = re.compile(r"^\s*n[aã]o\s+h[aá]\s+registro", re.IGNORECASE)


def _mapear_cabecalho(celulas: list, colunas: dict) -> dict | None:
    """{campo: índice} a partir da linha de cabeçalho, ou None se ela não for um cabeçalho.

    Nome repetido ganha sufixo (" 2", " 3") antes de ser procurado no mapa — é o que separa as
    duas colunas "Sigla" das ERBs sem depender da posição delas."""
    mapa: dict[str, int] = {}
    vistos: dict[str, int] = {}
    for indice, celula in enumerate(celulas):
        nome = _chave(celula)
        if not nome:
            continue
        vistos[nome] = vistos.get(nome, 0) + 1
        if vistos[nome] > 1:
            nome = f"{nome} {vistos[nome]}"
        campo = colunas.get(nome)
        if campo and campo not in mapa:
            mapa[campo] = indice
    return mapa if len(mapa) >= 2 else None


def _celula(celulas: list, mapa: dict, campo: str):
    """O valor CRU da célula — o openpyxl entrega data como datetime, e converter para texto aqui
    perderia o que `_data`/`_hora` sabem fazer com ele."""
    indice = mapa.get(campo)
    if indice is None or indice >= len(celulas):
        return None
    return celulas[indice]


def _texto(celulas: list, mapa: dict, campo: str) -> str | None:
    return _limpo(_celula(celulas, mapa, campo))


def _numero(celulas: list, mapa: dict, campo: str) -> int | None:
    numeros = _digitos(_celula(celulas, mapa, campo))
    return int(numeros) if numeros else None


def _linha_vazia(celulas: list) -> bool:
    return not any(_limpo(c) for c in celulas)


def _aviso_sem_registro(celulas: list) -> bool:
    primeira = _limpo(celulas[0]) if celulas else None
    return bool(primeira and _RE_SEM_REGISTRO.match(primeira))


# ---------------------------------------------------------------------------
# Uma linha de cada seção, a partir das células já mapeadas por nome de coluna
# ---------------------------------------------------------------------------
# O TX_CHAVE de cada uma é a IDENTIDADE do registro, não o retrato de todas as colunas: a mesma
# chamada aparece na planilha com o CGI e no PDF sem ele, e as duas precisam colidir para virarem
# uma linha só (quem completa o que falta é o ON CONFLICT ... DO UPDATE, em `_gravar`).


def _linha_chamada(celulas: list, mapa: dict, tp_registro: str) -> dict | None:
    dt_chamada = _data_hora(_celula(celulas, mapa, "data"), _celula(celulas, mapa, "hora"))
    chamador = _digitos(_texto(celulas, mapa, "nu_chamador"))
    chamado = _digitos(_texto(celulas, mapa, "nu_chamado"))
    if not dt_chamada or not (chamador or chamado):
        return None
    registro = {
        "tp_registro": tp_registro,
        "dt_chamada": dt_chamada,
        "nu_chamador": chamador,
        "nu_imei_chamador": _digitos(_texto(celulas, mapa, "nu_imei_chamador")),
        "nu_chamado": chamado,
        "nu_imei_chamado": _digitos(_texto(celulas, mapa, "nu_imei_chamado")),
        "hr_desconexao": _hora(_celula(celulas, mapa, "hr_desconexao")),
        "qt_duracao_segundos": _numero(celulas, mapa, "qt_duracao_segundos"),
        "cd_status": _texto(celulas, mapa, "cd_status"),
        "tp_trafego": _texto(celulas, mapa, "tp_trafego"),
        "no_servico": _texto(celulas, mapa, "no_servico"),
        "nu_gmt_origem": _texto(celulas, mapa, "nu_gmt_origem"),
        "nu_gmt_destino": _texto(celulas, mapa, "nu_gmt_destino"),
        "nu_cgi_origem": _texto(celulas, mapa, "nu_cgi_origem"),
        "nu_cgi_destino": _texto(celulas, mapa, "nu_cgi_destino"),
        "tp_tecnologia_origem": _texto(celulas, mapa, "tp_tecnologia_origem"),
        "tp_tecnologia_destino": _texto(celulas, mapa, "tp_tecnologia_destino"),
        "tx_eot_a": _texto(celulas, mapa, "tx_eot_a"),
        "tx_eot_b": _texto(celulas, mapa, "tx_eot_b"),
        "tx_ip_origem": _texto(celulas, mapa, "tx_ip_origem"),
        "tx_ip_destino": _texto(celulas, mapa, "tx_ip_destino"),
        "no_pais_origem": _texto(celulas, mapa, "no_pais_origem"),
        "no_pais_destino": _texto(celulas, mapa, "no_pais_destino"),
    }
    registro["tx_chave"] = _chave_linha(
        tp_registro, dt_chamada, chamador, chamado, registro["qt_duracao_segundos"])
    return registro


# O CGI ("724-10-0805342-003") é MCC-MNC-área-setor. A validação existe por causa da linha de
# rodapé que a VIVO manda quando não achou a antena — ela vem com a coluna CGI em branco e
# "CADASTRO NAO ENCONTRADO" no lugar da cidade, e viraria uma ERB fantasma.
_RE_CGI = re.compile(r"^\d{2,4}[-.]\d{1,3}[-.][\dA-F]{2,10}([-.][\dA-F]{1,4})?$", re.IGNORECASE)
# a Claro manda o CGI sem separador (15 dígitos concatenados: "724393110112351") e a Oi manda
# MCC-MNC-LAC-CI com o CI mais longo do que o _RE_CGI aceita ("724-31-002065-000060011") — é o
# mesmo identificador, com segmentos maiores. As duas formas entram por aqui.
_RE_CGI_COLADO = re.compile(r"^\d{10,20}$")
_RE_CGI_LONGO = re.compile(r"^[\dA-F]{2,4}([-.][\dA-F]{1,12}){2,4}$", re.IGNORECASE)


def _cgi_valido(cgi: str | None) -> bool:
    """Se a string é um CGI de verdade — com separador (VIVO/Oi) ou colado (Claro) —, e não o
    "CADASTRO NAO ENCONTRADO" do rodapé nem uma célula qualquer."""
    if not cgi:
        return False
    limpo = cgi.replace(" ", "")
    return bool(_RE_CGI.match(limpo) or _RE_CGI_LONGO.match(limpo) or _RE_CGI_COLADO.match(limpo))


def _linha_erb(celulas: list, mapa: dict) -> dict | None:
    cgi = _texto(celulas, mapa, "nu_cgi")
    if not _cgi_valido(cgi):
        return None
    cgi = cgi.replace(" ", "")
    tx_latitude = _texto(celulas, mapa, "tx_latitude")
    tx_longitude = _texto(celulas, mapa, "tx_longitude")
    return {
        "nu_cgi": cgi,
        "nu_ccc": _texto(celulas, mapa, "nu_ccc"),
        "no_central": _texto(celulas, mapa, "no_central"),
        "no_erb": _texto(celulas, mapa, "no_erb"),
        "sg_erb": _texto(celulas, mapa, "sg_erb"),
        "nu_setor": _numero(celulas, mapa, "nu_setor"),
        "nu_azimute": _numero(celulas, mapa, "nu_azimute"),
        "tp_tecnologia": _texto(celulas, mapa, "tp_tecnologia"),
        "tx_latitude": tx_latitude,
        "tx_longitude": tx_longitude,
        "vl_latitude": _coordenada(tx_latitude),
        "vl_longitude": _coordenada(tx_longitude),
        "sg_uf": _texto(celulas, mapa, "sg_uf"),
        "no_municipio": _texto(celulas, mapa, "no_municipio"),
        "no_bairro": _texto(celulas, mapa, "no_bairro"),
        "tx_endereco": _texto(celulas, mapa, "tx_endereco"),
        # a antena é a mesma em todo ofício do inquérito: a chave é o CGI, e o que chegar depois
        # completa o que faltava (endereço vem numa planilha e não vem no PDF, por exemplo)
        "tx_chave": _chave_linha(cgi),
    }


# IMEI zerado é o que a operadora grava quando não tem o aparelho do outro lado da chamada —
# guardar 000000000000000 como se fosse um IMEI faria trinta números diferentes "compartilharem"
# o mesmo aparelho na análise.
_RE_IMEI_VAZIO = re.compile(r"^0+$")


def _linha_numero(celulas: list, mapa: dict) -> dict | None:
    telefone = _digitos(_texto(celulas, mapa, "nu_telefone"))
    if not telefone:
        return None
    imei = _digitos(_texto(celulas, mapa, "nu_imei"))
    if imei and _RE_IMEI_VAZIO.match(imei):
        imei = None
    return {
        "nu_telefone": telefone,
        "nu_imei": imei,
        "tx_chave": _chave_linha(telefone, imei),
    }


def _linha_sessao(celulas: list, mapa: dict) -> dict | None:
    dt_inicio = _data_hora(_celula(celulas, mapa, "data_inicio"), _celula(celulas, mapa, "hora_inicio"))
    linha = _digitos(_texto(celulas, mapa, "nu_linha"))
    ip = _texto(celulas, mapa, "tx_ip")
    if not dt_inicio and not ip:
        return None
    return {
        "nu_linha": linha,
        "tx_ip": ip,
        "nu_porta": _numero(celulas, mapa, "nu_porta"),
        "dt_inicio": dt_inicio,
        "tp_evento_inicio": _texto(celulas, mapa, "tp_evento_inicio"),
        "dt_fim": _data_hora(_celula(celulas, mapa, "data_fim"), _celula(celulas, mapa, "hora_fim")),
        "tp_evento_fim": _texto(celulas, mapa, "tp_evento_fim"),
        "tp_trafego": _texto(celulas, mapa, "tp_trafego"),
        "no_servico": _texto(celulas, mapa, "no_servico"),
        "tx_chave": _chave_linha(linha, ip, dt_inicio),
    }


def _numero_azimute(valor) -> int | None:
    """Azimute inteiro a partir do que vier ("280", "280.29", 280.29): o relatório de dados manda
    decimal, e TEL_TB05.NU_AZIMUTE é inteiro."""
    if valor is None:
        return None
    try:
        return int(round(float(str(valor).strip().strip("'\"").replace(",", "."))))
    except (TypeError, ValueError):
        return None


def _linha_dados(celulas: list, mapa: dict) -> dict | None:
    """Uma sessão de dados móveis (TEL_TB08). Exige linha + início: sem os dois, é cabeçalho ou
    resto de formatação — o IP sozinho não identifica sessão."""
    dt_inicio = _data_hora(_celula(celulas, mapa, "data_inicio"), _celula(celulas, mapa, "hora_inicio"))
    linha = _digitos(_texto(celulas, mapa, "nu_linha"))
    if not linha or not dt_inicio:
        return None
    imei = _digitos(_texto(celulas, mapa, "nu_imei"))
    if imei and _RE_IMEI_VAZIO.match(imei):
        imei = None
    cgi = (_texto(celulas, mapa, "nu_cgi") or "").replace(" ", "")
    # o IPv6 vem com enchimento ("2804:18:4801:a8c4::/64<espaços>.") e vazio vira só o ponto
    ipv6 = (_texto(celulas, mapa, "tx_ipv6") or "").strip().rstrip(".").strip() or None
    dt_fim = _data_hora(_celula(celulas, mapa, "data_fim"), _celula(celulas, mapa, "hora_fim"))
    vol_ent = _numero(celulas, mapa, "qt_vol_entrada")
    vol_sai = _numero(celulas, mapa, "qt_vol_saida")
    return {
        "nu_cgi": cgi or None,
        "tx_ip": _texto(celulas, mapa, "tx_ip"),
        "tx_ipv6": ipv6,
        "nu_linha": linha,
        "nu_coride": _texto(celulas, mapa, "nu_coride"),
        "dt_inicio": dt_inicio,
        "tp_evento_inicio": _texto(celulas, mapa, "tp_evento_inicio"),
        "dt_fim": dt_fim,
        "tp_evento_fim": _texto(celulas, mapa, "tp_evento_fim"),
        "nu_site": _texto(celulas, mapa, "nu_site"),
        "qt_vol_entrada": vol_ent,
        "qt_vol_saida": vol_sai,
        "tp_unidade": _texto(celulas, mapa, "tp_unidade"),
        "tp_trafego": _texto(celulas, mapa, "tp_trafego"),
        "tp_servico": _texto(celulas, mapa, "tp_servico"),
        "tp_tecnologia": _texto(celulas, mapa, "tp_tecnologia"),
        "tp_rede": _texto(celulas, mapa, "tp_rede"),
        "nu_imei": imei,
        "nu_codope": _texto(celulas, mapa, "nu_codope"),
        "tp_tipo_acesso": _texto(celulas, mapa, "tp_tipo_acesso"),
        "tp_origem": _texto(celulas, mapa, "tp_origem"),
        "sg_uf": _texto(celulas, mapa, "sg_uf"),
        # a sessão é o intervalo daquela linha naquele IP: o volume entra na chave para dois
        # intervalos iguais com tráfego diferente não colidirem
        "tx_chave": _chave_linha(linha, _texto(celulas, mapa, "tx_ip"),
                                 dt_inicio, dt_fim, vol_ent, vol_sai),
    }


def _linha_parece_dados(celulas: list) -> bool:
    """A forma de uma linha de dados sem cabeçalho: CGI na coluna 0, linha na 2, data na 4 e hora
    na 5. Cada campo é conferido pelo TIPO, e a linha precisa dos quatro — um telefone de 11
    dígitos sozinho também seria um CGI "colado", por isso a data e a hora desambiguam."""
    if len(celulas) < 24:
        return False
    cgi = re.sub(r"\s", "", str(celulas[0] or ""))
    linha = _digitos(celulas[2])
    return (bool(_cgi_valido(cgi)) and linha is not None and 10 <= len(linha) <= 13
            and _data(celulas[4]) is not None and _hora(celulas[5]) is not None)


def _detectar_aba_dados(linhas: list[list], operadora: str) -> str | None:
    """Como a aba traz o relatório de dados móveis: "cabecalho", "posicional" ou None.

    Com cabeçalho, vale o NOME das colunas (o trio LOCALI/CODIPS/NUMLIN prova que é dados).
    Sem cabeçalho (o extrato por CGI), vale a FORMA das linhas — mas só fora da TIM, cujo CDR
    binário também é posicional e passa no mesmo teste de forma por coincidência de colunas.
    Abas com título de seção conhecido nem chegam aqui (o chamador só chama com secao None)."""
    for celulas in linhas[:40]:
        if _linha_vazia(celulas):
            continue
        mapa = _mapear_cabecalho(celulas, _COLUNAS_DADOS)
        if mapa is not None and _CHAVE_CABECALHO_DADOS <= set(mapa):
            return "cabecalho"
    if operadora == "TIM":
        return None
    achados = 0
    vistos = 0
    for celulas in linhas:
        if _linha_vazia(celulas):
            continue
        vistos += 1
        if _linha_parece_dados(celulas):
            achados += 1
            if achados >= 3:
                return "posicional"
        if vistos >= 80:
            break
    return None


def _operadora_por_cgis(cgis: list) -> str | None:
    """A operadora dona dos CGIs, pelo MNC em maioria — o último recurso, para o relatório que não
    se nomeia no nome, no timbre nem nas colunas. Sem maioria mapeada, None (o card nasce como
    "Não identificada", como antes)."""
    votos: dict[str, int] = {}
    for cgi in cgis or []:
        m = _RE_MCC_MNC.match(str(cgi or ""))
        if not m:
            continue
        operadora = _MNC_OPERADORA.get(m.group(2).zfill(2))
        if operadora:
            votos[operadora] = votos.get(operadora, 0) + 1
    if not votos:
        return None
    return max(votos, key=votos.get)


# ---------------------------------------------------------------------------
# Colheita de uma linha — o que ela deposita em cada lista da análise
# ---------------------------------------------------------------------------
# Cada leitor devolve um dicionário {lista: [registros]}, e não um registro só, porque uma linha
# da Oi/TIM/Claro rende ao mesmo tempo a chamada, a(s) ERB(s) que a atenderam e a ficha do
# titular — tudo na mesma linha da planilha. A VIVO, que separa isso em seções distintas, cai no
# caso simples de um registro numa lista só.


def _ficha_cadastral(nu_linha, **campos) -> dict | None:
    """Uma ficha de TEL_TB03 a partir do cadastro embutido no CDR (Oi/Claro). Sem número de linha
    não há ficha — é a chave da tabela. As fichas repetem em toda linha do arquivo e o TX_CHAVE
    colapsa as idênticas, então sobra uma por pessoa distinta."""
    numero = _digitos(nu_linha)
    if not numero:
        return None
    ficha = {"nu_linha": numero}
    for chave, valor in campos.items():
        limpo = _digitos(valor) if chave in ("nu_cpf_cnpj", "nu_cep") else _limpo(valor)
        if limpo:
            ficha[chave] = limpo
    ficha["tx_chave"] = _chave_linha(
        numero, ficha.get("no_cliente"), ficha.get("nu_cpf_cnpj"), ficha.get("tx_endereco"))
    return ficha


def _colher_chamada_vivo(celulas: list, mapa: dict, ctx: dict) -> dict:
    registro = _linha_chamada(celulas, mapa, "VOZ")
    if not registro:
        return {}
    if ctx.get("tp_trafego") and not registro.get("tp_trafego"):
        registro["tp_trafego"] = ctx["tp_trafego"]
    return {"chamadas": [registro]}


def _colher_tugo(celulas: list, mapa: dict, ctx: dict) -> dict:
    registro = _linha_chamada(celulas, mapa, "TUGO")
    return {"chamadas": [registro]} if registro else {}


def _colher_erb(celulas: list, mapa: dict, ctx: dict) -> dict:
    erb = _linha_erb(celulas, mapa)
    return {"erbs": [erb]} if erb else {}


def _colher_numero(celulas: list, mapa: dict, ctx: dict) -> dict:
    numero = _linha_numero(celulas, mapa)
    return {"numeros": [numero]} if numero else {}


def _colher_sessao(celulas: list, mapa: dict, ctx: dict) -> dict:
    sessao = _linha_sessao(celulas, mapa)
    return {"sessoes": [sessao]} if sessao else {}


def _colher_dados(celulas: list, mapa: dict, ctx: dict) -> dict:
    """Sessão de dados móveis + a antena que a atendeu. A antena entra em TEL_TB05 pelo CGI —
    com endereço e coordenada quando o relatório traz as colunas ERB_*, só com o CGI quando não
    (um ofício de voz do mesmo inquérito completa depois, pelo TX_CHAVE)."""
    registro = _linha_dados(celulas, mapa)
    if not registro:
        return {}
    colheita: dict[str, list] = {"dados": [registro]}
    cgi = registro.get("nu_cgi")
    if _cgi_valido(cgi):
        cgi = cgi.replace(" ", "")
        tx_lat = _texto(celulas, mapa, "erb_latitude")
        tx_long = _texto(celulas, mapa, "erb_longitude")
        colheita["erbs"] = [{
            "nu_cgi": cgi,
            "sg_uf": _texto(celulas, mapa, "erb_uf"),
            "no_municipio": _texto(celulas, mapa, "erb_municipio"),
            "no_bairro": _texto(celulas, mapa, "erb_bairro"),
            "tx_endereco": _texto(celulas, mapa, "erb_endereco"),
            "tx_latitude": tx_lat, "tx_longitude": tx_long,
            "vl_latitude": _coordenada(tx_lat), "vl_longitude": _coordenada(tx_long),
            "nu_azimute": _numero_azimute(_celula(celulas, mapa, "erb_azimute")),
            "tx_chave": _chave_linha(cgi),
        }]
    return colheita


def _colher_chamada_oi(celulas: list, mapa: dict, ctx: dict) -> dict:
    """CDR da Oi: chamada + ERB + ficha do titular, todos na mesma linha. O alvo vem do cabeçalho
    ("Número do terminal:") e o sentido do bloco diz de que lado ele está."""
    dt_chamada = _data_hora(_celula(celulas, mapa, "data"), _celula(celulas, mapa, "hora"))
    outro = _digitos(_texto(celulas, mapa, "nu_chamado")) or _digitos(_texto(celulas, mapa, "nu_chamador"))
    if not dt_chamada or not (outro or ctx.get("alvo")):
        return {}
    alvo = ctx.get("alvo")
    recebida = ctx.get("sentido") == "recebida"
    chamador = outro if recebida else alvo
    chamado = alvo if recebida else outro
    imei_alvo = _digitos(_texto(celulas, mapa, "nu_imei_alvo"))
    cgi = _texto(celulas, mapa, "nu_cgi")
    duracao = _duracao_segundos(_celula(celulas, mapa, "qt_duracao_segundos"))

    chamada = {
        "tp_registro": "VOZ",
        "dt_chamada": dt_chamada,
        "nu_chamador": chamador,
        "nu_chamado": chamado,
        "nu_imei_chamado" if recebida else "nu_imei_chamador": imei_alvo,
        "nu_cgi_destino" if recebida else "nu_cgi_origem": cgi if _cgi_valido(cgi) else None,
        "qt_duracao_segundos": duracao,
    }
    chamada["tx_chave"] = _chave_linha("VOZ", dt_chamada, chamador, chamado, duracao)
    colheita: dict[str, list] = {"chamadas": [chamada]}

    if _cgi_valido(cgi):
        tx_lat = _texto(celulas, mapa, "erb_latitude")
        tx_long = _texto(celulas, mapa, "erb_longitude")
        colheita["erbs"] = [{
            "nu_cgi": cgi.replace(" ", ""),
            "tx_endereco": _texto(celulas, mapa, "erb_endereco"),
            "no_bairro": _texto(celulas, mapa, "erb_bairro"),
            "no_municipio": _texto(celulas, mapa, "erb_municipio"),
            "sg_uf": _texto(celulas, mapa, "erb_uf"),
            "nu_azimute": _numero(celulas, mapa, "erb_azimute"),
            "tx_latitude": tx_lat, "tx_longitude": tx_long,
            "vl_latitude": _coordenada(tx_lat), "vl_longitude": _coordenada(tx_long),
            "tx_chave": _chave_linha(cgi.replace(" ", "")),
        }]

    # o bloco "Dados do Cliente" da Oi é do INTERLOCUTOR (o titular varia a cada linha: é quem o
    # alvo ligou/recebeu), não do alvo — por isso a ficha é do outro número, não do terminal
    ficha = _ficha_cadastral(
        outro,
        no_cliente=_texto(celulas, mapa, "cad_nome"),
        nu_cpf_cnpj=_texto(celulas, mapa, "cad_cpf"),
        tx_endereco=_texto(celulas, mapa, "cad_endereco"),
        no_bairro=_texto(celulas, mapa, "cad_bairro"),
        no_municipio=_texto(celulas, mapa, "cad_municipio"),
        sg_uf=_texto(celulas, mapa, "cad_uf"),
        nu_cep=_texto(celulas, mapa, "cad_cep"),
    )
    if ficha:
        colheita["linhas"] = [ficha]
    return colheita


def _colher_chamada_claro(celulas: list, mapa: dict, ctx: dict) -> dict:
    """CDR da Claro (Vigia): o alvo é "CD Mobile", o outro lado é "Telefone de Destino" e o
    sentido está em "Tipo de ligação IC". A antena é só o CGI — as coordenadas chegam pelo arquivo
    de antenas e completam a mesma ERB. Alvo e interlocutor viram fichas de cadastro."""
    dt_chamada = _data_hora(_celula(celulas, mapa, "data"), None)
    alvo = _digitos(_texto(celulas, mapa, "nu_alvo"))
    outro = _digitos(_texto(celulas, mapa, "nu_outro"))
    if not dt_chamada or not (alvo or outro):
        return {}
    entrante = _chave(_texto(celulas, mapa, "cd_sentido") or "").startswith("incom")
    chamador = outro if entrante else alvo
    chamado = alvo if entrante else outro
    imei_alvo = _digitos(_texto(celulas, mapa, "nu_imei_alvo"))
    cgi = _texto(celulas, mapa, "nu_cgi")
    duracao = _duracao_segundos(_celula(celulas, mapa, "qt_duracao_segundos"))

    chamada = {
        "tp_registro": "VOZ",
        "dt_chamada": dt_chamada,
        "nu_chamador": chamador,
        "nu_chamado": chamado,
        "nu_imei_chamado" if entrante else "nu_imei_chamador": imei_alvo,
        "nu_cgi_destino" if entrante else "nu_cgi_origem": cgi if _cgi_valido(cgi) else None,
        "qt_duracao_segundos": duracao,
        "nu_gmt_origem": _texto(celulas, mapa, "nu_gmt_origem"),
    }
    chamada["tx_chave"] = _chave_linha("VOZ", dt_chamada, chamador, chamado, duracao)
    colheita: dict[str, list] = {"chamadas": [chamada]}

    if _cgi_valido(cgi):
        # só o CGI; o endereço e as coordenadas vêm do "Relatório de Antenas" e completam por CGI
        colheita["erbs"] = [{"nu_cgi": cgi.replace(" ", ""), "tx_chave": _chave_linha(cgi.replace(" ", ""))}]

    fichas = []
    alvo_ficha = _ficha_cadastral(
        _texto(celulas, mapa, "cad_numero") or alvo,
        no_cliente=_texto(celulas, mapa, "cad_nome"),
        nu_cpf_cnpj=_texto(celulas, mapa, "cad_cpf"),
        nu_rg=_texto(celulas, mapa, "cad_rg"),
        tx_endereco=_texto(celulas, mapa, "cad_endereco"),
    )
    if alvo_ficha:
        fichas.append(alvo_ficha)
    interlocutor = _ficha_cadastral(
        _texto(celulas, mapa, "int_numero"),
        no_cliente=_texto(celulas, mapa, "int_nome"),
        nu_cpf_cnpj=_texto(celulas, mapa, "int_cpf"),
        nu_rg=_texto(celulas, mapa, "int_rg"),
        tx_endereco=_texto(celulas, mapa, "int_endereco"),
    )
    if interlocutor:
        fichas.append(interlocutor)
    if fichas:
        colheita["linhas"] = fichas
    return colheita


def _erb_tim(celulas: list, mapa: dict, lado: str) -> tuple[str | None, dict | None]:
    """A ERB de um lado (RE = recebeu, RS = saiu) da chamada da TIM. Devolve (CGI sintético, ERB).
    A TIM não manda CGI: o identificador que casa a chamada com a antena é montado do SiteID e do
    SetorID (o mais específico), e é ele que vai para NU_CGI e para NU_CGI_ORIGEM/DESTINO."""
    setor = _texto(celulas, mapa, f"{lado}_setor")
    site = _texto(celulas, mapa, f"{lado}_site")
    cgi = setor or site
    if not cgi:
        return None, None
    tx_lat = _texto(celulas, mapa, f"{lado}_latitude")
    tx_long = _texto(celulas, mapa, f"{lado}_longitude")
    erb = {
        "nu_cgi": cgi,
        "sg_erb": site,
        "no_central": _texto(celulas, mapa, "no_central"),
        "nu_azimute": _numero(celulas, mapa, f"{lado}_azimute"),
        "tx_endereco": _texto(celulas, mapa, f"{lado}_endereco"),
        "no_municipio": _texto(celulas, mapa, f"{lado}_municipio"),
        "tx_latitude": tx_lat, "tx_longitude": tx_long,
        "vl_latitude": _coordenada(tx_lat), "vl_longitude": _coordenada(tx_long),
        "tx_chave": _chave_linha(cgi),
    }
    return cgi, erb


def _colher_chamada_tim(celulas: list, mapa: dict, ctx: dict) -> dict:
    """CDR da TIM: chamada + as duas ERBs (RE do chamador, RS do chamado). A duração já vem em
    segundos; SMS usa o mesmo leiaute e é distinguido pelo TP_TRAFEGO tirado do nome do arquivo."""
    dt_chamada = _data_hora(_celula(celulas, mapa, "data"), _celula(celulas, mapa, "hora"))
    chamador = _digitos(_texto(celulas, mapa, "nu_chamador"))
    chamado = _digitos(_texto(celulas, mapa, "nu_chamado"))
    if not dt_chamada or not (chamador or chamado):
        return {}
    duracao = _duracao_segundos(_celula(celulas, mapa, "qt_duracao_segundos"))
    cgi_re, erb_re = _erb_tim(celulas, mapa, "re")
    cgi_rs, erb_rs = _erb_tim(celulas, mapa, "rs")

    chamada = {
        "tp_registro": "VOZ",
        "dt_chamada": dt_chamada,
        "nu_chamador": chamador,
        "nu_imei_chamador": _digitos(_texto(celulas, mapa, "nu_imei_chamador")),
        "nu_chamado": chamado,
        "nu_imei_chamado": _digitos(_texto(celulas, mapa, "nu_imei_chamado")),
        "qt_duracao_segundos": duracao,
        "cd_status": _texto(celulas, mapa, "cd_status"),
        "tp_trafego": ctx.get("tp_trafego"),
        "nu_cgi_origem": cgi_re,
        "nu_cgi_destino": cgi_rs,
    }
    chamada["tx_chave"] = _chave_linha("VOZ", dt_chamada, chamador, chamado, duracao)
    colheita: dict[str, list] = {"chamadas": [chamada]}
    erbs = [e for e in (erb_re, erb_rs) if e]
    if erbs:
        colheita["erbs"] = erbs
    return colheita


_RE_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _colher_gprs_tim(celulas: list, mapa: dict, ctx: dict) -> dict:
    """Sessão de dados (GPRS) da TIM → TEL_TB07. Guarda o IP e o período. O IP é validado como
    IPv4 de propósito: o único exemplo em mãos veio corrompido na origem, e sem esse filtro a
    sessão entraria com lixo no lugar do IP e da data (ver docs/telefonia.md)."""
    ip = _texto(celulas, mapa, "tx_ip")
    if ip and not _RE_IPV4.match(ip):
        ip = None
    dt_inicio = _data_hora(_celula(celulas, mapa, "data_inicio"), _celula(celulas, mapa, "hora_inicio"))
    if not dt_inicio and not ip:
        return {}
    linha = _digitos(_texto(celulas, mapa, "nu_linha"))
    sessao = {
        "nu_linha": linha,
        "tx_ip": ip,
        "dt_inicio": dt_inicio,
        "dt_fim": _data_hora(_celula(celulas, mapa, "data_fim"), _celula(celulas, mapa, "hora_fim")),
        "tp_trafego": "GPRS",
        "tx_chave": _chave_linha(linha, ip, dt_inicio),
    }
    return {"sessoes": [sessao]}


# Leitor de chamada por operadora — a seção "chamadas" muda de forma conforme quem exportou. As
# demais seções (ERBs, números, WiFi) são iguais entre operadoras e não entram aqui.
_COLHEITA_CHAMADA_POR_OPERADORA = {
    "OI": _colher_chamada_oi,
    "CLARO": _colher_chamada_claro,
    "TIM": _colher_chamada_tim,
}


def _colmap(secao: str, operadora: str):
    """O mapa de colunas certo para a seção — e, na de chamadas, para a operadora."""
    if secao == "erbs":
        return _COLUNAS_ERBS
    if secao == "numeros":
        return _COLUNAS_NUMEROS
    if secao == "wifi":
        return _COLUNAS_WIFI
    if secao == "tugo":
        return _COLUNAS_CHAMADAS
    if secao == "dados":
        return _COLUNAS_DADOS
    return {"OI": _COLUNAS_OI, "CLARO": _COLUNAS_CLARO, "TIM": _COLUNAS_TIM}.get(
        operadora, _COLUNAS_CHAMADAS)


def _handler(secao: str, operadora: str):
    """A função que colhe uma linha desta seção — a de chamadas depende da operadora, e a de WiFi
    é o GPRS da TIM quando é a TIM."""
    if secao == "wifi":
        return _colher_gprs_tim if operadora == "TIM" else _colher_sessao
    fixos = {"erbs": _colher_erb, "numeros": _colher_numero, "tugo": _colher_tugo,
             "dados": _colher_dados}
    if secao in fixos:
        return fixos[secao]
    return _COLHEITA_CHAMADA_POR_OPERADORA.get(operadora, _colher_chamada_vivo)


# A TIM não se nomeia em lugar nenhum — nem no arquivo, nem no conteúdo. Quando o nome e o timbre
# não dizem a operadora, ela é deduzida pelas colunas — mas só pelas colunas ASSINATURA de cada
# uma, nunca pelas geográficas (CGI, latitude, azimute) que várias compartilham: um relatório de
# antenas casaria com meia dúzia de operadoras se essas contassem.
_ASSINATURA_OPERADORA = (
    ("TIM", {"numero a", "numero b", "siteid re", "setorid re", "imei a", "imei b"}),
    ("OI", {"ligou para", "recebeu de", "cliente titular"}),
    ("CLARO", {"cd mobile", "tipo de ligacao ic", "nome do alvo", "antena"}),
)


def _inferir_operadora_por_colunas(abas: list[tuple[str, list[list]]]) -> str | None:
    melhor_por_operadora: dict[str, int] = {}
    for _, linhas in abas:
        for celulas in linhas[:20]:
            nomes = {_chave(c) for c in celulas if _limpo(c)}
            for operadora, assinatura in _ASSINATURA_OPERADORA:
                casados = len(nomes & assinatura)
                if casados:
                    melhor_por_operadora[operadora] = max(
                        melhor_por_operadora.get(operadora, 0), casados)
    if not melhor_por_operadora:
        return None
    operadora = max(melhor_por_operadora, key=melhor_por_operadora.get)
    return operadora if melhor_por_operadora[operadora] >= 2 else None


# Formatos que não trazem título de aba nem linha de marcador (a TIM exporta em "Sheet0", sem
# nada): a seção é adivinhada pelo cabeçalho, escolhendo o mapa que reconhecer mais colunas.
def _sniff_secao(celulas: list, operadora: str) -> tuple[str, dict, dict] | None:
    """(seção, mapa de colunas, mapa {campo: índice}) do cabeçalho, ou None se não for cabeçalho."""
    if operadora == "TIM":
        candidatos = [("chamadas", _COLUNAS_TIM), ("wifi", _COLUNAS_TIM_GPRS)]
    else:
        candidatos = [("chamadas", _colmap("chamadas", operadora)),
                      ("erbs", _COLUNAS_ERBS), ("numeros", _COLUNAS_NUMEROS)]
    melhor = None
    for secao, colmap in candidatos:
        mapa = _mapear_cabecalho(celulas, colmap)
        if mapa and (melhor is None or len(mapa) > len(melhor[2])):
            melhor = (secao, colmap, mapa)
    return melhor


def _analise_vazia() -> dict:
    return {"linhas": [], "chamadas": [], "erbs": [], "numeros": [], "sessoes": [],
            "dados": [], "secoes": []}



# ---------------------------------------------------------------------------
# Leitura da planilha do ofício (.xlsx / .csv)
# ---------------------------------------------------------------------------

def _abas_planilha(caminho: Path) -> list[tuple[str, list[list]]]:
    """[(título da aba, linhas)] de um .xlsx ou de um .csv (que vira uma aba só, com o nome do
    arquivo por título — é assim que a operadora manda quando exporta uma seção isolada)."""
    if caminho.suffix.lower() in (".csv", ".txt"):
        import csv

        from funcoes.documentos_utils import _detectar_encoding

        dados = caminho.read_bytes()
        texto = dados.decode(_detectar_encoding(dados), errors="replace")
        if not texto.strip():
            return []
        try:
            dialeto = csv.Sniffer().sniff(texto[:4000], delimiters=";,\t")
        except csv.Error:
            dialeto = csv.excel
        linhas = list(csv.reader(texto.splitlines(), dialeto))[:LIMITE_LINHAS_ARQUIVO]
        return [(caminho.stem, linhas)]

    if caminho.suffix.lower() == ".xls":
        # ".xls" é ambíguo: a TIM manda BIFF/OLE2 de verdade, a Oi manda HTML com extensão .xls.
        # O magic byte separa os dois — 0xD0CF11E0 é OLE2, um "<" é HTML.
        cabeca = caminho.read_bytes()[:8]
        if cabeca[:4] == b"\xd0\xcf\x11\xe0":
            return _abas_xls_binario(caminho)
        return _abas_html(caminho)

    import openpyxl

    livro = openpyxl.load_workbook(str(caminho), read_only=True, data_only=True)
    try:
        abas = []
        for aba in livro.worksheets:
            # A planilha da operadora não escreve a área usada do XLSX (grava <dimension ref="A1"/>),
            # e em modo read_only o openpyxl acredita nela: a aba de 87 linhas é lida como UMA
            # linha vazia, sem erro nenhum e sem aviso. `reset_dimensions()` descarta o que está
            # declarado e faz a leitura ir até a última célula que existe de verdade. É método, e
            # não atributo — atribuir `aba.reset_dimensions = True` só apaga o método em silêncio.
            aba.reset_dimensions()
            linhas = []
            for indice, linha in enumerate(aba.iter_rows(values_only=True)):
                if indice >= LIMITE_LINHAS_ARQUIVO:
                    break
                linhas.append(list(linha))
            abas.append((aba.title, linhas))
        return abas
    finally:
        livro.close()


def _abas_xls_binario(caminho: Path) -> list[tuple[str, list[list]]]:
    """[(nome da aba, linhas)] de um .xls binário (BIFF/OLE2), o formato do CDR da TIM. O xlrd lê
    célula a célula; o valor cru vai adiante, que `_data`/`_hora`/`_digitos` sabem tratar."""
    import xlrd

    livro = xlrd.open_workbook(str(caminho))
    abas = []
    for aba in livro.sheets():
        linhas = []
        for indice in range(min(aba.nrows, LIMITE_LINHAS_ARQUIVO)):
            linhas.append([aba.cell_value(indice, coluna) for coluna in range(aba.ncols)])
        abas.append((aba.name, linhas))
    return abas


_RE_HTML_TABELA = re.compile(r"(?is)<table", re.IGNORECASE)
_RE_HTML_LINHA = re.compile(r"(?is)<tr\b.*?</tr>")
_RE_HTML_CELULA = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>")
_RE_HTML_TAG = re.compile(r"(?is)<[^>]+>")


def _abas_html(caminho: Path) -> list[tuple[str, list[list]]]:
    """Uma aba só a partir do HTML que a Oi entrega com extensão .xls. Cada <tr> vira uma linha e
    cada <td>/<th> uma célula; as tabelas do arquivo são concatenadas num fluxo único, em que o
    título de cada bloco ("Chamadas Efetuadas") vira uma linha de marcador e a tabela de dados que
    o segue traz o cabeçalho e as linhas — é o que o scanner de seções já sabe percorrer."""
    import html as biblioteca_html

    from funcoes.documentos_utils import _detectar_encoding

    dados = caminho.read_bytes()
    texto = dados.decode(_detectar_encoding(dados), errors="replace")
    linhas: list[list] = []
    for tr in _RE_HTML_LINHA.findall(texto):
        celulas = []
        for bruta in _RE_HTML_CELULA.findall(tr):
            limpa = biblioteca_html.unescape(_RE_HTML_TAG.sub(" ", bruta))
            limpa = re.sub(r"\s+", " ", limpa).strip()
            celulas.append(limpa or None)
        if any(celulas):
            linhas.append(celulas)
        if len(linhas) >= LIMITE_LINHAS_ARQUIVO:
            break
    return [(caminho.stem, linhas)]


def _texto_das_primeiras_linhas(linhas: list[list], quantas: int = 6) -> str:
    """As primeiras linhas da aba viradas em texto — é onde mora o cabeçalho do ofício."""
    partes = []
    for linha in linhas[:quantas]:
        partes.append(" ".join(str(c) for c in linha if c is not None))
    return "\n".join(partes)


def analisar_planilha(caminho: Path) -> dict | None:
    """As seções reconhecidas da planilha do ofício, ou None quando o arquivo não é uma delas.

    A seção pode vir de três lugares, nesta ordem: o título da aba (VIVO), uma linha de marcador
    dentro da aba (Oi/Claro põem "Chamadas Efetuadas"/"Relatório de Antenas" numa célula mesclada)
    ou o próprio cabeçalho, adivinhado pelas colunas quando não há título nem marcador (TIM exporta
    em "Sheet0"). Achada a seção, o cabeçalho da tabela diz onde está cada campo — nada é lido por
    posição, porque o mesmo relatório chega com colunas a mais ou a menos conforme a operadora."""
    try:
        abas = _abas_planilha(caminho)
    except Exception:
        logger.info("Planilha %s não pôde ser aberta para leitura de telefonia", caminho.name, exc_info=True)
        return None

    # a operadora escolhe o leitor de chamadas (Oi/Claro/TIM leem a linha de jeitos diferentes).
    # Nome e timbre mandam; as colunas são o desempate para quem não se nomeia (a TIM).
    texto_topo = "\n".join(_texto_das_primeiras_linhas(linhas) for _, linhas in abas)
    titulos = " ".join(titulo for titulo, _ in abas)
    operadora = detectar_operadora(caminho.name, titulos, texto_topo)
    if operadora == OPERADORA_DESCONHECIDA:
        operadora = _inferir_operadora_por_colunas(abas) or OPERADORA_DESCONHECIDA

    # o alvo do ofício (a Oi nomeia "Número do terminal:"; a Claro traz o alvo em coluna, então
    # aqui pode não vir) e o tráfego que só o nome do arquivo distingue (SMS x VOZ da TIM)
    m_alvo = _RE_LINHA_ALVO.search(texto_topo)
    alvo = _digitos(m_alvo.group(1)) if m_alvo else None
    tp_trafego = "SMS" if "sms" in _chave(caminho.name) else None

    analise = _analise_vazia()
    textos_cabecalho = [caminho.name]
    achou_secao = False
    qt_por_secao: dict[str, int] = {}

    for titulo, linhas in abas:
        textos_cabecalho.append(_texto_das_primeiras_linhas(linhas))
        secao = _secao_de(titulo)
        colmap = _colmap(secao, operadora) if secao else None
        sentido = None
        mapa = None
        if secao is None:
            # relatório de dados móveis: o título da aba não diz a seção ("hc_dados", o nome do
            # arquivo), então ela sai do cabeçalho ou da forma das linhas
            modo_dados = _detectar_aba_dados(linhas, operadora)
            if modo_dados is not None:
                secao, colmap = "dados", _COLUNAS_DADOS
                if modo_dados == "posicional":
                    # sem cabeçalho: o mapa é fixo e cada linha não vazia já é dado
                    mapa = dict(_MAPA_POSICIONAL_DADOS)
                    achou_secao = True
        for celulas in linhas:
            marcador, marcador_sentido = _marcador_secao(celulas)
            if marcador:
                secao, sentido, colmap, mapa = marcador, marcador_sentido, _colmap(marcador, operadora), None
                continue
            if _linha_vazia(celulas):
                continue
            if _aviso_sem_registro(celulas):
                mapa = None
                continue
            if mapa is None:
                if secao is None:
                    # sem título nem marcador: adivinha a seção pelo cabeçalho (TIM)
                    achado = _sniff_secao(celulas, operadora)
                    if achado is not None:
                        secao, colmap, mapa = achado
                        achou_secao = True
                    continue
                mapa = _mapear_cabecalho(celulas, colmap)
                if mapa is not None:
                    achou_secao = True
                continue
            colheita = _handler(secao, operadora)(
                celulas, mapa, {"alvo": alvo, "sentido": sentido, "tp_trafego": tp_trafego})
            if colheita:
                for lista, itens in colheita.items():
                    analise[lista].extend(itens)
                qt_por_secao[secao] = qt_por_secao.get(secao, 0) + 1

    if not achou_secao:
        return None
    # a operadora que não se nomeia em lugar nenhum ainda pode sair do MNC dos CGIs das sessões
    # de dados (a maioria manda): sem isso, o relatório de dados abriria um card "Não
    # identificada" ao lado do card de voz da mesma operadora
    if operadora == OPERADORA_DESCONHECIDA and analise["dados"]:
        por_mnc = _operadora_por_cgis([d.get("nu_cgi") for d in analise["dados"]])
        if por_mnc:
            operadora = por_mnc
    # a ficha do titular e a ERB da antena repetem em toda linha do CDR (Oi/Claro/TIM trazem-nas
    # embutidas); o TX_CHAVE já as colapsaria no banco, mas dedupá-las aqui deixa a contagem do
    # card honesta e alivia o INSERT em lote
    analise["linhas"] = _unicos_por_chave(analise["linhas"])
    analise["erbs"] = _unicos_por_chave(analise["erbs"])
    analise["dados"] = _unicos_por_chave(analise["dados"])
    analise["operadora"] = operadora
    analise["secoes"] = [{"secao": secao, "titulo": secao, "qt_linhas": qt}
                         for secao, qt in qt_por_secao.items()]
    analise["cabecalho"] = _extrair_cabecalho(textos_cabecalho)
    return analise


def _unicos_por_chave(registros: list[dict]) -> list[dict]:
    """Registros sem repetir TX_CHAVE, mantendo o primeiro de cada — para a ficha do titular e a
    antena, que vêm iguais em cada linha do CDR."""
    vistos: dict[str, dict] = {}
    for registro in registros:
        vistos.setdefault(registro["tx_chave"], registro)
    return list(vistos.values())


# ---------------------------------------------------------------------------
# Leitura do PDF do ofício — o gêmeo impresso da planilha
# ---------------------------------------------------------------------------
# Uma seção por página, e o título dela vem no alto da página. O que o pypdf devolve não é uma
# tabela: as colunas vazias somem e as compridas quebram em várias linhas. Por isso aqui as
# células são reconhecidas por FORMATO (15 dígitos é IMEI, 8 a 13 é telefone, hh:mm:ss é hora,
# grau-minuto-segundo é coordenada) em vez de por posição.
#
# O que o PDF rende, portanto, é menos do que a planilha rende: o CGI da chamada, por exemplo,
# quebra em três linhas no meio do registro e fica de fora. Isso não é perda quando os dois
# arquivos são importados — o TX_CHAVE junta as duas leituras da mesma chamada numa linha só, e o
# que veio da planilha completa o que faltava no PDF (ver `_gravar`). Importando SÓ o PDF, o
# investigador fica com o CDR sem as antenas, que é exatamente o que está impresso ali.
#
# WiFi e TuGO não são lidas do PDF: as duas quase sempre voltam "não há registro", e o layout
# delas muda a ordem das colunas (TuGO começa pelo chamador, não pela data), o que sem cabeçalho
# de tabela viraria adivinhação. Vindo registro nessas seções, ele entra pela planilha.

_RE_INICIO_CDR = re.compile(r"^\s*\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}(:\d{2})?\s")

# O pypdf 4.3.1 (a versão fixada em api/requirements.txt) não separa colunas coladas: a linha do
# CDR sai como "...10:39:41 3Nao Compl. Voz-3" e a da ERB como "003CORE PS TRGZ1...". Versões mais
# novas separam, e por isso o problema não aparece rodando o parser fora do container. Reabrir a
# fronteira entre dígito e letra resolve os dois casos; a fronteira INVERSA (letra seguida de
# dígito) fica de fora de propósito, senão a sigla da antena "TRGZ1" viraria "TRGZ 1".
_RE_DIGITO_LETRA = re.compile(r"(?<=\d)(?=[A-Za-zÀ-ÿ])")
_RE_LETRA_SINAL = re.compile(r"(?<=[A-Za-zÀ-ÿ])(?=-\d)")


def _normalizar_linha_pdf(linha: str) -> str:
    """Devolve a linha do PDF com as colunas coladas separadas de novo."""
    return _RE_LETRA_SINAL.sub(" ", _RE_DIGITO_LETRA.sub(" ", linha or ""))
_RE_NUMERO_IMEI_PDF = re.compile(r"^\s*(\d{8,13})\s+(\d{14,17})\s*$")
_RE_FRAGMENTO_CGI = re.compile(r"^[\dA-F]+[-.][\dA-F.-]*$", re.IGNORECASE)
# O último pedaço do CGI quebrado ("003") vem sem hífen nenhum, então só conta como continuação
# quando já há um fragmento aberto — solto, ele seria qualquer número da página.
_RE_CONTINUACAO_CGI = re.compile(r"^[\dA-F]{1,10}$", re.IGNORECASE)
_RE_ROTULO_ENDERECO = re.compile(r"^\s*endere[cç]o\s*:", re.IGNORECASE)

# A palavra da coluna "Tráfego" é o que separa o status (que tem espaço no meio: "Nao Compl.") do
# resto da linha. Sem ela o corte teria de ser por contagem de tokens, que muda por operadora.
_TRAFEGOS = {"voz", "dados", "sms", "video", "torpedo", "gprs", "vas", "roaming"}


def _linha_chamada_pdf(linha: str) -> dict | None:
    """Uma linha do CDR impresso. Os números saem por tamanho, na ordem em que a tabela os põe:
    chamador, IMEI do chamador, chamado, IMEI do chamado — cada IMEI é opcional, e é por isso que
    ler por posição não funcionaria (a coluna vazia simplesmente não existe no texto)."""
    tokens = _normalizar_linha_pdf(linha).split()
    if len(tokens) < 4:
        return None
    dt_chamada = _data_hora(tokens[0], tokens[1])
    if not dt_chamada:
        return None

    indice = 2
    fila = []
    while indice < len(tokens) and tokens[indice].isdigit():
        fila.append(tokens[indice])
        indice += 1

    def _tirar(minimo: int, maximo: int) -> str | None:
        if fila and minimo <= len(fila[0]) <= maximo:
            return fila.pop(0)
        return None

    chamador = _tirar(8, 13)
    imei_chamador = _tirar(14, 17)
    chamado = _tirar(8, 13)
    imei_chamado = _tirar(14, 17)
    if not chamador and not chamado:
        return None

    resto = fila + tokens[indice:]
    hr_desconexao = None
    if resto and _RE_HORA.fullmatch(resto[0]):
        hr_desconexao = _hora(resto.pop(0))
    duracao = None
    if resto and resto[0].isdigit() and len(resto[0]) <= 6:
        duracao = int(resto.pop(0))

    corte = next((i for i, t in enumerate(resto) if _chave(t) in _TRAFEGOS), None)
    status = " ".join(resto[:corte]).strip() or None if corte is not None else None
    trafego = resto[corte] if corte is not None else None

    registro = {
        "tp_registro": "VOZ",
        "dt_chamada": dt_chamada,
        "nu_chamador": chamador,
        "nu_imei_chamador": imei_chamador,
        "nu_chamado": chamado,
        "nu_imei_chamado": imei_chamado,
        "hr_desconexao": hr_desconexao,
        "qt_duracao_segundos": duracao,
        "cd_status": status,
        "tp_trafego": trafego,
    }
    registro["tx_chave"] = _chave_linha("VOZ", dt_chamada, chamador, chamado, duracao)
    return registro


def _linhas_erb_pdf(pagina: str) -> list[dict]:
    """As ERBs de uma página impressa. O CGI vem quebrado em três linhas ("724-10-", "0805342-",
    "003") e o endereço vem numa linha própria depois dos dados — os dois são remontados aqui."""
    erbs: list[dict] = []
    fragmento = ""
    cgi_pendente = None
    for bruta in pagina.splitlines():
        texto = _normalizar_linha_pdf(bruta).strip()
        if not texto:
            continue
        if _RE_ROTULO_ENDERECO.match(texto):
            endereco = _limpo(texto.split(":", 1)[1])
            # "Dta.Chamada.: …" é o rodapé da linha "CADASTRO NAO ENCONTRADO", não um endereço
            if erbs and endereco and not erbs[-1].get("tx_endereco") and "chamada" not in _chave(endereco):
                erbs[-1]["tx_endereco"] = endereco
            continue

        # O pedaço final do CGI ora vem sozinho numa linha, ora colado no começo da linha de
        # dados — os dois caem aqui, e o que sobrar dos tokens é a linha de dados da antena.
        tokens = texto.split()
        while tokens and cgi_pendente is None:
            candidato = tokens[0]
            se_encaixa = _RE_FRAGMENTO_CGI.match(candidato) or (
                fragmento and _RE_CONTINUACAO_CGI.match(candidato))
            if not se_encaixa or _coordenada(candidato) is not None:
                break
            fragmento += candidato
            tokens.pop(0)
            if _RE_CGI.match(fragmento):
                cgi_pendente = fragmento
                fragmento = ""

        if cgi_pendente is None or not tokens:
            continue
        erb = _erb_de_tokens(cgi_pendente, tokens)
        cgi_pendente = None
        if erb:
            erbs.append(erb)
    return erbs


def _erb_de_tokens(cgi: str, tokens: list[str]) -> dict | None:
    """A linha de dados da ERB impressa, ancorada no par latitude/longitude: o que vem antes dele
    é central/sigla/setor/azimute/tecnologia e o que vem depois é UF/cidade/bairro."""
    corte = None
    for i in range(len(tokens) - 1):
        if _coordenada(tokens[i]) is not None and _coordenada(tokens[i + 1]) is not None:
            corte = i
            break
    if corte is None or corte < 1:
        return None

    antes = tokens[:corte]
    tecnologia = antes.pop() if antes else None
    azimute = antes.pop() if antes and antes[-1].isdigit() else None
    setor = antes.pop() if antes and antes[-1].isdigit() else None
    sigla = antes.pop() if antes else None
    central = " ".join(antes) or None

    depois = tokens[corte + 2:]
    uf = depois.pop(0) if depois and len(depois[0]) == 2 and depois[0].isalpha() else None
    # cidade de duas palavras ("SAO PAULO") sai como cidade "SAO" e bairro "PAULO …": sem o
    # cabeçalho de coluna do PDF não há como separar. A planilha do mesmo ofício corrige isso ao
    # ser importada, porque ela sobrepõe a leitura do PDF (ver `_gravar`).
    municipio = depois.pop(0) if depois else None
    bairro = " ".join(depois) or None

    tx_latitude, tx_longitude = tokens[corte], tokens[corte + 1]
    return {
        "nu_cgi": cgi,
        "nu_ccc": None,
        "no_central": central,
        "no_erb": None,
        "sg_erb": sigla,
        "nu_setor": int(setor) if setor else None,
        "nu_azimute": int(azimute) if azimute else None,
        "tp_tecnologia": tecnologia,
        "tx_latitude": tx_latitude,
        "tx_longitude": tx_longitude,
        "vl_latitude": _coordenada(tx_latitude),
        "vl_longitude": _coordenada(tx_longitude),
        "sg_uf": uf,
        "no_municipio": municipio,
        "no_bairro": bairro,
        "tx_endereco": None,
        "tx_chave": _chave_linha(cgi),
    }


# ---------------------------------------------------------------------------
# Extrato de antena da TIM — "Relatório de Eventos por (ERB)"
# ---------------------------------------------------------------------------
# É um CDR invertido: em vez do histórico de uma linha, o histórico de uma ANTENA — todas as
# chamadas que passaram por aquela ERB no período. Vem um PDF por antena (o CGI está no nome do
# arquivo e no título). As colunas são, nesta ordem:
#
#     REGISTRO(MOC/MTC) · CENTRAL · ORIGEM · DESTINO · REDIRECIONADO · IMEI ORIGEM · IMEI DESTINO ·
#     PRIMEIRA CGI · ÚLTIMA CGI · HORA BRASÍLIA · HORA LOCAL · DURAÇÃO · TIPO
#
# O layout do texto muda com a VERSÃO do pypdf: a 4.3.1 fixada no container COLA colunas vizinhas
# ("ZRJO055531984306619" = central+origem, "724-...-5313605/02/2019 20:09:10" = CGI+data), a 6.x
# do host empilha uma por linha. Por isso o registro NÃO é lido por posição de linha: cada evento
# (do MOC/MTC até o próximo) é juntado num texto só e os campos saem por TIPO, na ordem em que
# aparecem — data e CGI têm forma inconfundível, o telefone é 55+DDD+número e o IMEI tem 14-16
# dígitos. Remover data e CGI antes de pescar telefone/IMEI evita que os dígitos delas contaminem.
#
# A antena do relatório não traz coordenada (é consulta reversa): a ERB entra só com o CGI — um
# cadastro de antenas do mesmo inquérito completa depois, pelo CGI.
_RE_ASSINATURA_EVENTOS_ERB = "relatorio de eventos por"
_REGISTRO_EVENTO_ERB = {"MOC", "MTC"}  # SMS/GPRS não aparecem neste extrato
_RE_DT_EVENTO_ERB = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}")
_RE_CGI_EVENTO_ERB = re.compile(r"\d{3}-\d{2}-\d{4,6}-\d{4,6}")
# O telefone é 55 + DDD + assinante, e o assinante distingue móvel (9 seguido de 8 dígitos) de
# fixo (2-5 seguido de 7). Essa forma ESTRITA é o que desfaz a ambiguidade da colagem da 4.3.1:
# quando a central termina em "5" e a origem é um fixo de 12 dígitos ("ZRJO5"+"553741011213"), um
# "55\d{9,11}$" solto casaria o "5" da central junto ("5553741011213"); exigir 9\d{8} ou [2-5]\d{7}
# no fim deixa só o sufixo válido. Origem termina a linha (colada à central na 4.3.1); destino
# começa a sua.
_FONE_BR = r"55[1-9]\d(?:9\d{8}|[2-5]\d{7})"
_RE_FONE_FIM_ERB = re.compile(rf"({_FONE_BR})\s*$")
_RE_FONE_INICIO_ERB = re.compile(rf"\s*({_FONE_BR})")
_RE_IMEI_EVENTO_ERB = re.compile(r"\d{14,16}")
_RE_CENTRAL_EVENTO_ERB = re.compile(r"[A-Z]{2,6}\d{0,2}")
_RE_DURACAO_TIPO_ERB = re.compile(r"(\d{1,6})\s+([A-Za-zÀ-ÿ]{3,})")
# marcadores que encerram o bloco de um evento (rodapé, legenda, cabeçalho da página seguinte)
_PARADAS_EVENTO_ERB = (
    "confidencial", "legenda", "dados suplementares", "dados de antena", "registro da pesquisa",
    "eventos de portabilidade", "a carga de cdr", "o armazenamento", "numero solicitacao",
    "numero protocolo", "numero processo", "relatorio de eventos", "direcao", "periodo de pesquisa",
    "tim brasil", "caixa postal",
)


def _e_parada_evento(linha: str) -> bool:
    chave = _chave(linha)
    return any(chave.startswith(p) for p in _PARADAS_EVENTO_ERB)


def _evento_erb(registro: str, bloco: list[str]) -> dict | None:
    """Um evento do extrato de antena, montado a partir das linhas entre um REGISTRO e o próximo.
    Independe de o pypdf ter colado as colunas ou não — os campos saem por forma, na ordem."""
    # origem e destino saem PRIMEIRO, presos à linha: a origem termina a linha da central (na 4.3.1
    # ela vem colada, "ZRJO055531984306619" — sem o $ o "55" da junção central/origem casaria e a
    # origem sairia deslocada), o destino começa a sua. Uma vez achados, são apagados das linhas
    # para os dígitos deles não virarem IMEI falso no varredura por forma que vem depois.
    linhas = list(bloco)
    origem = destino = None
    for i, linha in enumerate(linhas):
        if origem is None:
            m = _RE_FONE_FIM_ERB.search(linha)
            if m:
                origem = m.group(1)
                linhas[i] = linha[:m.start(1)] + " " + linha[m.end(1):]
                continue
        if origem is not None and destino is None:
            m = _RE_FONE_INICIO_ERB.match(linha)
            if m:
                destino = m.group(1)
                linhas[i] = linha[:m.start(1)] + " " + linha[m.end(1):]

    texto = " ".join(linhas)
    datas = _RE_DT_EVENTO_ERB.findall(texto)
    dt_chamada = _data_hora_combinada(datas[0]) if datas else None
    if not dt_chamada or not (origem or destino):
        return None

    # data e CGI têm forma inconfundível; removidas, o que sobra de 14-16 dígitos é IMEI
    sem_data = _RE_DT_EVENTO_ERB.sub(" ", texto)
    cgis = _RE_CGI_EVENTO_ERB.findall(sem_data)
    sem_cgi = _RE_CGI_EVENTO_ERB.sub(" ", sem_data)

    imeis = _RE_IMEI_EVENTO_ERB.findall(sem_cgi)
    # MOC leva o IMEI na origem, MTC no destino; com dois IMEIs, o primeiro é da origem
    if len(imeis) >= 2:
        imei_origem, imei_destino = imeis[0], imeis[1]
    elif imeis:
        imei_origem, imei_destino = (imeis[0], None) if registro == "MOC" else (None, imeis[0])
    else:
        imei_origem = imei_destino = None

    m = _RE_DURACAO_TIPO_ERB.search(sem_cgi)
    duracao = int(m.group(1)) if m else None
    trafego = m.group(2) if m else None
    m_central = _RE_CENTRAL_EVENTO_ERB.search(bloco[0]) if bloco else None
    central = m_central.group(0) if m_central else None

    cgi_origem = cgis[0] if len(cgis) >= 1 and _cgi_valido(cgis[0]) else None
    cgi_destino = cgis[1] if len(cgis) >= 2 and _cgi_valido(cgis[1]) else None

    chamada = {
        "tp_registro": "VOZ",
        "dt_chamada": dt_chamada,
        "nu_chamador": origem,
        "nu_imei_chamador": imei_origem,
        "nu_chamado": destino,
        "nu_imei_chamado": imei_destino,
        "qt_duracao_segundos": duracao,
        "cd_status": registro,   # MOC | MTC
        "tp_trafego": trafego,   # CHAMADA
        "nu_cgi_origem": cgi_origem,
        "nu_cgi_destino": cgi_destino,
    }
    chamada["tx_chave"] = _chave_linha("VOZ", dt_chamada, origem, destino, duracao)
    return {"chamada": chamada, "central": central,
            "cgis": [c for c in (cgi_origem, cgi_destino) if c]}


# --- as outras duas seções do extrato de antena --------------------------------------------
# DADOS SUPLEMENTARES traz procedimentos de assinante (SS Procedures) — o mais comum é o
# "INTERROGATION / CALL FORWARDING UNCONDITIONAL", a consulta do desvio de chamada. Coloca o
# telefone naquela antena naquele instante, então entra como chamada (sem destino nem duração, com
# o serviço em TP_TRAFEGO). DADOS DE ANTENA é a tabela de antenas COM coordenada e endereço — é o
# que faz a ERB aparecer no mapa (o CDR sozinho só dá o CGI).
_MARCADORES_SECAO_ERB = ("eventos de portabilidade", "registro da pesquisa", "dados suplementares",
                         "dados de antena", "legenda")
_REGISTRO_SUPLEMENTAR_ERB = "ss procedures"
_RE_ANTENA_ANCORA_ERB = re.compile(r"^\s*(\d{3}-\d{2}-\d{4,6}-\d{4,6})\s+(-?\d{1,3},\d+)")
_RE_SERVICO_ERB = re.compile(r"[A-Z][A-Z ]{4,}")


def _fatia_secao_erb(linhas: list[str], marcador: str) -> list[str]:
    """As linhas de uma seção do extrato — do seu cabeçalho até o próximo marcador de seção."""
    inicio = None
    for i, linha in enumerate(linhas):
        chave = _chave(linha)
        if inicio is None:
            if chave.startswith(marcador):
                inicio = i + 1
        elif any(chave.startswith(m) for m in _MARCADORES_SECAO_ERB):
            return linhas[inicio:i]
    return linhas[inicio:] if inicio is not None else []


def _split_endereco_bairro(texto: str) -> tuple[str | None, str | None]:
    """"ESTRADA BR 101 0BEBEDOURO" -> ("ESTRADA BR 101 0", "BEBEDOURO"). O endereço termina em
    número e o bairro é o texto em maiúsculas colado depois (só na 4.3.1; na 6.x já vêm separados)."""
    m = re.match(r"^(.*\d)\s*([A-ZÀ-Ÿ][A-ZÀ-Ÿ '.-]*)$", texto)
    if m:
        return _limpo(m.group(1)), _limpo(m.group(2))
    return _limpo(texto), None


def _split_cidade_uf(texto: str) -> tuple[str | None, str | None]:
    """"LINHARESES" -> ("LINHARES", "ES"): a UF são as duas últimas letras coladas na cidade."""
    m = re.match(r"^(.*[A-ZÀ-Ÿ])\s*([A-Z]{2})$", texto)
    if m:
        return _limpo(m.group(1)), m.group(2)
    return _limpo(texto), None


def _antena_de_bloco(bloco: list[str]) -> dict | None:
    """Uma antena da tabela DADOS DE ANTENA (CGI, coordenada, azimute, sigla, endereço). Lê tanto
    o bloco colado da 4.3.1 (6 linhas) quanto o empilhado da 6.x (10 linhas)."""
    m0 = _RE_ANTENA_ANCORA_ERB.match(bloco[0]) if bloco else None
    if not m0:
        return None
    cgi, tx_lat = m0.group(1), m0.group(2)
    corpo = [l for l in bloco[1:] if l.strip()]
    junto = " ".join(corpo)

    # a longitude é seguida da DATA INÍCIO, que a 4.3.1 cola nela ("-40,11038907/08/2018"); o
    # lookahead para a data (ou espaço/fim) corta a coordenada no ponto certo, sem levar o "07"
    m_long = re.search(r"-?\d{1,3},\d+?(?=\d{2}/\d{2}/\d{4}|\s|$)", junto)
    tx_long = m_long.group(0) if m_long else None
    m_azi = re.search(r"(\d{1,3})\s*[°º]", junto)
    azimute = int(m_azi.group(1)) if m_azi else None
    m_nome = re.search(r"[°º]\s*([A-Z0-9]{3,12})", junto)
    sigla = m_nome.group(1) if m_nome else None

    endereco = bairro = municipio = uf = None
    if len(corpo) >= 8:            # empilhado (6.x): long, data, azimute, nome, endereço, bairro, cidade, uf, cep
        endereco, bairro, municipio = _limpo(corpo[4]), _limpo(corpo[5]), _limpo(corpo[6])
        uf = _limpo(corpo[7])
        if sigla is None:
            sigla = _limpo(corpo[3])
    elif len(corpo) >= 4:         # colado (4.3.1): long+data, azimute+nome, endereço+bairro, cidade+uf, cep
        endereco, bairro = _split_endereco_bairro(corpo[2])
        municipio, uf = _split_cidade_uf(corpo[3])

    erb = {
        "nu_cgi": cgi,
        "sg_erb": sigla,
        "nu_azimute": azimute,
        "tx_latitude": tx_lat, "tx_longitude": tx_long,
        "vl_latitude": _coordenada(tx_lat), "vl_longitude": _coordenada(tx_long),
        "tx_endereco": endereco, "no_bairro": bairro, "no_municipio": municipio,
        "sg_uf": uf if uf and len(uf) == 2 else None,
        "tx_chave": _chave_linha(cgi),
    }
    return erb


def _dados_de_antena(linhas: list[str]) -> list[dict]:
    """As antenas com coordenada da seção DADOS DE ANTENA — vazia na maioria dos extratos."""
    corpo = _fatia_secao_erb(linhas, "dados de antena")
    antenas: list[dict] = []
    indice = 0
    while indice < len(corpo):
        if not _RE_ANTENA_ANCORA_ERB.match(corpo[indice]):
            indice += 1
            continue
        fim = indice + 1
        while (fim < len(corpo) and fim - indice <= 12
               and not _RE_ANTENA_ANCORA_ERB.match(corpo[fim])):
            fim += 1
        antena = _antena_de_bloco(corpo[indice:fim])
        if antena:
            antenas.append(antena)
        indice = fim
    return antenas


def _dados_suplementares(linhas: list[str]) -> list[dict]:
    """Os procedimentos de assinante (SS Procedures) da seção DADOS SUPLEMENTARES, como chamadas
    sem destino nem duração, com o serviço em TP_TRAFEGO ("CALL FORWARDING UNCONDITIONAL")."""
    corpo = _fatia_secao_erb(linhas, "dados suplementares")
    eventos: list[dict] = []
    indice = 0
    while indice < len(corpo):
        if _chave(corpo[indice]) != _REGISTRO_SUPLEMENTAR_ERB:
            indice += 1
            continue
        fim = indice + 1
        while (fim < len(corpo) and fim - indice <= 12
               and _chave(corpo[fim]) != _REGISTRO_SUPLEMENTAR_ERB
               and not _e_parada_evento(corpo[fim])):
            fim += 1
        bloco = corpo[indice + 1:fim]
        indice = fim if fim > indice + 1 else indice + 1
        evento = _evento_erb("MOC", bloco)   # aproveita a leitura por forma (origem, IMEI, CGI, hora)
        if evento is None:
            continue
        chamada = evento["chamada"]
        servico = " ".join(m.group(0).strip() for m in _RE_SERVICO_ERB.finditer(" ".join(bloco)))
        chamada["cd_status"] = "SS Procedures"
        chamada["tp_trafego"] = _limpo(servico) or None
        chamada["tx_chave"] = _chave_linha(
            "SS", chamada["dt_chamada"], chamada.get("nu_chamador"), chamada.get("tp_trafego"))
        eventos.append(chamada)
    return eventos


def analisar_eventos_erb_tim(paginas: list[str]) -> dict | None:
    """O CDR de um extrato de antena da TIM, ou None se o PDF não for um deles.

    Lê as três seções: REGISTRO DA PESQUISA (as chamadas), DADOS SUPLEMENTARES (os procedimentos
    de assinante, ex.: desvio de chamada) e DADOS DE ANTENA (as antenas com coordenada — é o que
    põe a ERB no mapa; o CDR sozinho só entrega o CGI)."""
    if _RE_ASSINATURA_EVENTOS_ERB not in _chave("\n".join(paginas[:1])):
        return None

    linhas = [linha.strip() for pagina in paginas for linha in pagina.splitlines()]
    analise = _analise_vazia()
    erbs: dict[str, dict] = {}
    indice = 0
    total = len(linhas)
    while indice < total:
        if linhas[indice] not in _REGISTRO_EVENTO_ERB:
            indice += 1
            continue
        registro = linhas[indice]
        # junta as linhas do evento: do REGISTRO até o próximo REGISTRO, um marcador de rodapé/
        # legenda, ou no máximo 12 linhas (o evento tem 6 a 11 conforme a versão do pypdf)
        fim = indice + 1
        while (fim < total and fim - indice <= 12 and linhas[fim] not in _REGISTRO_EVENTO_ERB
               and not _e_parada_evento(linhas[fim])):
            fim += 1
        evento = _evento_erb(registro, linhas[indice + 1:fim])
        indice = fim if fim > indice + 1 else indice + 1
        if evento is None:
            continue

        analise["chamadas"].append(evento["chamada"])
        for cgi in evento["cgis"]:
            if cgi not in erbs:
                erbs[cgi] = {"nu_cgi": cgi, "no_central": evento["central"],
                             "tx_chave": _chave_linha(cgi)}

    analise["chamadas"].extend(_dados_suplementares(linhas))

    # a tabela de antenas COMPLETA a ERB que o CDR só conhecia pelo CGI (coordenada, endereço...)
    for antena in _dados_de_antena(linhas):
        registro = erbs.setdefault(antena["nu_cgi"], {"nu_cgi": antena["nu_cgi"],
                                                       "tx_chave": antena["tx_chave"]})
        for campo, valor in antena.items():
            if valor is not None:
                registro[campo] = valor

    if not analise["chamadas"] and not any(e.get("vl_latitude") for e in erbs.values()):
        return None
    analise["erbs"] = list(erbs.values())
    analise["operadora"] = "TIM"
    analise["secoes"] = [{"secao": "chamadas", "titulo": "eventos por erb",
                          "qt_linhas": len(analise["chamadas"])}]
    analise["cabecalho"] = _extrair_cabecalho(paginas[:1])
    return analise


def analisar_pdf(paginas: list[str]) -> dict | None:
    """As seções reconhecidas no PDF do ofício, ou None quando o PDF não é um deles."""
    eventos_erb = analisar_eventos_erb_tim(paginas)
    if eventos_erb is not None:
        return eventos_erb

    analise = _analise_vazia()
    achou_secao = False

    for pagina in paginas:
        secao = _secao_de(pagina)
        if secao is None:
            continue
        achou_secao = True
        qt_secao = 0
        if secao == "chamadas":
            for linha in pagina.splitlines():
                if not _RE_INICIO_CDR.match(linha):
                    continue
                registro = _linha_chamada_pdf(linha)
                if registro:
                    analise["chamadas"].append(registro)
                    qt_secao += 1
        elif secao == "erbs":
            for erb in _linhas_erb_pdf(pagina):
                analise["erbs"].append(erb)
                qt_secao += 1
        elif secao == "numeros":
            for linha in pagina.splitlines():
                m = _RE_NUMERO_IMEI_PDF.match(linha)
                if not m:
                    continue
                imei = m.group(2)
                analise["numeros"].append({
                    "nu_telefone": m.group(1),
                    "nu_imei": None if _RE_IMEI_VAZIO.match(imei) else imei,
                    "tx_chave": _chave_linha(m.group(1), None if _RE_IMEI_VAZIO.match(imei) else imei),
                })
                qt_secao += 1
        analise["secoes"].append({"secao": secao, "titulo": secao, "qt_linhas": qt_secao})

    if not achou_secao:
        return None
    analise["cabecalho"] = _extrair_cabecalho(paginas[:2])
    return analise


# ---------------------------------------------------------------------------
# Reconhecimento do arquivo — a porta de entrada da importação genérica
# ---------------------------------------------------------------------------

_EXTENSOES_PLANILHA = (".xlsx", ".xlsm", ".csv", ".xls")


def analisar_arquivo(caminho: Path) -> dict | None:
    """O que este arquivo é e o que ele traz, ou None quando ele não é de telefonia.

    A ordem importa para o `.txt`: ele é o formato do cadastro de linhas, mas há operadora que
    exporta uma seção do ofício como texto separado por ponto e vírgula com a mesma extensão —
    por isso o cadastro é tentado primeiro e a planilha fica de reserva."""
    sufixo = caminho.suffix.lower()

    if sufixo == ".txt":
        from funcoes.documentos_utils import _detectar_encoding

        dados = caminho.read_bytes()
        texto = dados.decode(_detectar_encoding(dados), errors="replace")
        if parece_cadastro_telefonia(texto):
            fichas = analisar_cadastro(texto)
            return {
                "tp_conteudo": "cadastro",
                "tp_fonte": "cadastro",
                "linhas": fichas,
                "chamadas": [], "erbs": [], "numeros": [], "sessoes": [], "dados": [],
                "secoes": [{"secao": "cadastro", "titulo": "cadastro", "qt_linhas": len(fichas)}],
                "cabecalho": _extrair_cabecalho([texto[:4000]]),
                "texto": texto[:4000],
            }

    if sufixo in _EXTENSOES_PLANILHA or sufixo == ".txt":
        analise = analisar_planilha(caminho)
        if analise is not None:
            # "linhas" já pode vir preenchida — Oi e Claro trazem a ficha do titular embutida no
            # CDR —, então NÃO é reinicializada aqui como no cadastro/PDF
            analise.update({"tp_conteudo": "planilha", "tp_fonte": "planilha",
                            "texto": _texto_operadora_planilha(caminho)})
            return analise
        return None

    if sufixo == ".pdf":
        try:
            paginas = paginas_pdf(caminho)
        except Exception:
            logger.info("PDF %s não pôde ser lido para telefonia", caminho.name, exc_info=True)
            return None
        analise = analisar_pdf(paginas)
        if analise is not None:
            analise.update({"tp_conteudo": "pdf", "tp_fonte": "pdf",
                            "texto": "\n".join(paginas[:2])})
            return analise

    return None


def _texto_operadora_planilha(caminho: Path) -> str:
    """O cabeçalho da planilha, só para a detecção da operadora — a razão social aparece ali
    ("Ref. 405928/2024 - TBRA - VIVO SP") e é o único lugar do arquivo que a nomeia."""
    try:
        return "\n".join(_texto_das_primeiras_linhas(linhas) for _, linhas in _abas_planilha(caminho))[:4000]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------
# Todo INSERT de linha filha passa por `_gravar`, que resolve o conflito de TX_CHAVE em vez de
# estourar. Duas regras moram ali:
#
#   COALESCE(excluded.coluna, tabela.coluna) — o arquivo novo COMPLETA o que faltava, em vez de
#   apagar. O PDF não traz o endereço da ERB; a planilha traz. Importando os dois, a ERB fica com
#   o endereço, tanto faz a ordem.
#
#   WHERE excluded.tp_fonte <> 'pdf' OR tabela.tp_fonte = 'pdf' — a planilha tem mais coluna que o
#   PDF e por isso ganha dele. Sem isso, importar o PDF depois da planilha rebaixaria linhas que
#   já estavam melhores (o PDF não tem cabeçalho de coluna, então cidade e bairro dele são um
#   palpite; ver `_erb_de_tokens`).

_COLUNAS_LINHAS = [
    "nu_linha", "no_cliente", "nu_cpf_cnpj", "nu_rg", "no_mae", "dt_nascimento", "tx_endereco",
    "tx_complemento", "no_bairro", "nu_cep", "no_municipio", "sg_uf", "tp_modalidade",
    "tp_situacao", "dt_habilitacao", "dt_rescisao",
]
_COLUNAS_CHAMADAS_BD = [
    "tp_registro", "dt_chamada", "nu_chamador", "nu_imei_chamador", "nu_chamado",
    "nu_imei_chamado", "hr_desconexao", "qt_duracao_segundos", "cd_status", "tp_trafego",
    "no_servico", "nu_gmt_origem", "nu_gmt_destino", "nu_cgi_origem", "nu_cgi_destino",
    "tp_tecnologia_origem", "tp_tecnologia_destino", "tx_eot_a", "tx_eot_b", "tx_ip_origem",
    "tx_ip_destino", "no_pais_origem", "no_pais_destino",
]
_COLUNAS_ERBS_BD = [
    "nu_cgi", "nu_ccc", "no_central", "no_erb", "sg_erb", "nu_setor", "nu_azimute",
    "tp_tecnologia", "tx_latitude", "tx_longitude", "vl_latitude", "vl_longitude", "sg_uf",
    "no_municipio", "no_bairro", "tx_endereco",
]
_COLUNAS_NUMEROS_BD = ["nu_telefone", "nu_imei"]
_COLUNAS_SESSOES_BD = [
    "nu_linha", "tx_ip", "nu_porta", "dt_inicio", "tp_evento_inicio", "dt_fim", "tp_evento_fim",
    "tp_trafego", "no_servico",
]
_COLUNAS_DADOS_BD = [
    "nu_cgi", "tx_ip", "tx_ipv6", "nu_linha", "nu_coride", "dt_inicio", "tp_evento_inicio",
    "dt_fim", "tp_evento_fim", "nu_site", "qt_vol_entrada", "qt_vol_saida", "tp_unidade",
    "tp_trafego", "tp_servico", "tp_tecnologia", "tp_rede", "nu_imei", "nu_codope",
    "tp_tipo_acesso", "tp_origem", "sg_uf",
]

# (tabela, colunas, chave da lista na análise) de cada filha, na ordem em que são gravadas.
_FILHAS = [
    ("tel_tb03_linhas", _COLUNAS_LINHAS, "linhas"),
    ("tel_tb04_chamadas", _COLUNAS_CHAMADAS_BD, "chamadas"),
    ("tel_tb05_erbs", _COLUNAS_ERBS_BD, "erbs"),
    ("tel_tb06_numeros", _COLUNAS_NUMEROS_BD, "numeros"),
    ("tel_tb07_sessoes_wifi", _COLUNAS_SESSOES_BD, "sessoes"),
    ("tel_tb08_dados_moveis", _COLUNAS_DADOS_BD, "dados"),
]


def _gravar(cur, tabela: str, colunas: list[str], linhas: list[dict],
            id_telefonia: int, id_arquivo: int, tp_fonte: str) -> int:
    """INSERT em lote das linhas de uma seção, com o conflito de TX_CHAVE resolvido por
    complemento. Devolve quantas linhas o arquivo trouxe (não quantas eram inéditas — o card
    reconta do banco em `_atualizar_contagens`)."""
    if not linhas:
        return 0
    nomes = ["id_telefonia", "id_arquivo_telefonia", "tp_fonte", *colunas, "tx_chave"]
    marcadores = ", ".join(["%s"] * len(nomes))
    atualizacoes = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, {tabela}.{c})" for c in colunas)
    sql = (
        f"INSERT INTO investigacao.{tabela} ({', '.join(nomes)}) VALUES ({marcadores}) "
        f"ON CONFLICT (id_telefonia, tx_chave) DO UPDATE SET {atualizacoes}, "
        f"    id_arquivo_telefonia = EXCLUDED.id_arquivo_telefonia, tp_fonte = EXCLUDED.tp_fonte "
        f"WHERE EXCLUDED.tp_fonte <> 'pdf' OR {tabela}.tp_fonte = 'pdf'"
    )
    valores = [
        (id_telefonia, id_arquivo, tp_fonte, *[linha.get(c) for c in colunas], linha["tx_chave"])
        for linha in linhas
    ]
    cur.executemany(sql, valores)
    return len(valores)


def _obter_ou_criar_card(cur, id_inquerito: int, operadora: str, id_usuario: int | None) -> int:
    """O card daquela operadora dentro do inquérito, criado na primeira vez e reencontrado depois.

    O ON CONFLICT não é zelo excessivo: dois arquivos selecionados na tela sobem em requisições
    seguidas, e nada garante que a primeira já tenha commitado quando a segunda procura o card."""
    cur.execute(
        """
        INSERT INTO investigacao.tel_tb01_telefonia (id_inquerito, no_operadora, id_usuario)
        VALUES (%s, %s, %s)
        ON CONFLICT (id_inquerito, no_operadora)
        DO UPDATE SET dt_atualizacao = now()
        RETURNING id_telefonia
        """,
        (id_inquerito, operadora, id_usuario),
    )
    return cur.fetchone()[0]


def _registrar_arquivo(cur, id_telefonia: int, no_arquivo: str, id_documento: int | None,
                       analise: dict, id_usuario: int | None) -> int:
    """A procedência: qual arquivo entrou no card, que seções trouxe e que período cobre.

    Reimportar o MESMO arquivo atualiza o registro em vez de empilhar outro — cada anexo cria um
    documento novo em INQ_TB04 (a rota não deduplica), então sem isso o card diria "3 arquivos"
    tendo recebido a mesma planilha três vezes. O que distingue um arquivo de outro aqui é o nome,
    que é o que o investigador vê na tela."""
    cabecalho = analise.get("cabecalho") or {}
    secoes = ", ".join(s["secao"] for s in analise.get("secoes", [])) or None
    qt_registros = sum(len(analise.get(destino) or []) for destino in
                       ("linhas", "chamadas", "erbs", "numeros", "sessoes", "dados"))
    campos = (analise["tp_conteudo"], secoes, cabecalho.get("nu_referencia"),
              cabecalho.get("nu_linha_alvo"), cabecalho.get("dt_inicio_periodo"),
              cabecalho.get("dt_fim_periodo"), cabecalho.get("dt_solicitacao"), qt_registros)

    cur.execute(
        """
        SELECT id_arquivo_telefonia FROM investigacao.tel_tb02_arquivos
        WHERE id_telefonia = %s AND no_arquivo = %s
        ORDER BY id_arquivo_telefonia LIMIT 1
        """,
        (id_telefonia, no_arquivo),
    )
    existente = cur.fetchone()
    if existente:
        cur.execute(
            """
            UPDATE investigacao.tel_tb02_arquivos SET
                id_documento = %s, tp_conteudo = %s, tx_secoes = %s, nu_referencia = %s,
                nu_linha_alvo = %s, dt_inicio_periodo = %s, dt_fim_periodo = %s,
                dt_solicitacao = %s, qt_registros = %s, dt_importacao = now()
            WHERE id_arquivo_telefonia = %s
            """,
            (id_documento, *campos, existente[0]),
        )
        return existente[0]

    cur.execute(
        """
        INSERT INTO investigacao.tel_tb02_arquivos
            (id_telefonia, id_documento, no_arquivo, tp_conteudo, tx_secoes, nu_referencia,
             nu_linha_alvo, dt_inicio_periodo, dt_fim_periodo, dt_solicitacao, qt_registros,
             id_usuario)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_arquivo_telefonia
        """,
        (id_telefonia, id_documento, no_arquivo, *campos, id_usuario),
    )
    return cur.fetchone()[0]


def _atualizar_contagens(cur, id_telefonia: int) -> dict:
    """Reconta as filhas direto do banco — cada arquivo novo mexe nos números do card."""
    cur.execute(
        """
        UPDATE investigacao.tel_tb01_telefonia SET
            qt_arquivos = (SELECT count(*) FROM investigacao.tel_tb02_arquivos WHERE id_telefonia = %(id)s),
            qt_linhas   = (SELECT count(*) FROM investigacao.tel_tb03_linhas WHERE id_telefonia = %(id)s),
            qt_chamadas = (SELECT count(*) FROM investigacao.tel_tb04_chamadas WHERE id_telefonia = %(id)s),
            qt_erbs     = (SELECT count(*) FROM investigacao.tel_tb05_erbs WHERE id_telefonia = %(id)s),
            qt_numeros  = (SELECT count(*) FROM investigacao.tel_tb06_numeros WHERE id_telefonia = %(id)s),
            qt_sessoes  = (SELECT count(*) FROM investigacao.tel_tb07_sessoes_wifi WHERE id_telefonia = %(id)s),
            qt_dados    = (SELECT count(*) FROM investigacao.tel_tb08_dados_moveis WHERE id_telefonia = %(id)s),
            dt_atualizacao = now()
        WHERE id_telefonia = %(id)s
        RETURNING qt_arquivos, qt_linhas, qt_chamadas, qt_erbs, qt_numeros, qt_sessoes, qt_dados
        """,
        {"id": id_telefonia},
    )
    arquivos, linhas, chamadas, erbs, numeros, sessoes, dados = cur.fetchone()
    return {"qt_arquivos": arquivos, "qt_linhas": linhas, "qt_chamadas": chamadas,
            "qt_erbs": erbs, "qt_numeros": numeros, "qt_sessoes": sessoes,
            "qt_dados": dados}


def extrair_telefonia(no_arquivo: str, id_inquerito: int, id_documento: int | None,
                      id_usuario: int | None) -> dict | None:
    """Lê o arquivo de data/ e o costura no card de telefonia da operadora dele. Devolve o resumo
    do card (já com o arquivo dentro), ou None se o arquivo não for de telefonia — nesse caso ele
    segue sendo só texto para a IA. Chamada logo depois de anexar_documento_inquerito, no mesmo
    ponto de extrair_inquerito, extrair_irpf e extrair_ipei.

    Não há costura por nome de arquivo como na IPEI: o que junta os arquivos é a operadora, e o
    que impede a linha repetida é o TX_CHAVE. Por isso importar a planilha e o PDF do mesmo ofício,
    ou a segunda leva de ERBs do mesmo período, continua dando um card só."""
    caminho = resolver_em_dados(no_arquivo)
    if caminho is None:
        return None

    analise = analisar_arquivo(caminho)
    if analise is None:
        return None

    # a planilha já resolve a operadora (inclusive a TIM, que só se identifica pelas colunas);
    # nome/timbre são a reserva para o cadastro e o PDF, que não passam por lá
    operadora = analise.get("operadora") or OPERADORA_DESCONHECIDA
    if operadora == OPERADORA_DESCONHECIDA:
        operadora = detectar_operadora(caminho.name, analise.get("texto"))
    tp_fonte = analise["tp_fonte"]

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_telefonia = _obter_ou_criar_card(cur, id_inquerito, operadora, id_usuario)
            id_arquivo = _registrar_arquivo(cur, id_telefonia, caminho.name, id_documento,
                                            analise, id_usuario)
            trazidos = {}
            for tabela, colunas, destino in _FILHAS:
                trazidos[destino] = _gravar(cur, tabela, colunas, analise.get(destino) or [],
                                            id_telefonia, id_arquivo, tp_fonte)
            contagens = _atualizar_contagens(cur, id_telefonia)
        conn.commit()

    return {
        "id_telefonia": id_telefonia,
        "id_arquivo_telefonia": id_arquivo,
        "no_operadora": operadora,
        "tp_conteudo": analise["tp_conteudo"],
        "secoes": analise.get("secoes", []),
        "trazidos": trazidos,
        **contagens,
    }


# ---------------------------------------------------------------------------
# Consultas — o que o card, a página e a IA leem
# ---------------------------------------------------------------------------

def _linhas(cur) -> list[dict]:
    colunas = [d[0] for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _consultar(query: str, params: tuple) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _linhas(cur)


def listar_telefonia_inquerito(id_inquerito: int) -> list[dict]:
    """Os cards de telefonia do inquérito — um por operadora —, para os blocos da investigação."""
    return _consultar(
        """
        SELECT t.id_telefonia, t.no_operadora, t.qt_arquivos, t.qt_linhas, t.qt_chamadas,
               t.qt_erbs, t.qt_numeros, t.qt_sessoes, t.qt_dados, t.dt_extracao, t.dt_atualizacao,
               u.no_usuario AS extraido_por,
               ap.no_apelido,
               (SELECT min(a.dt_inicio_periodo) FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS dt_inicio_periodo,
               (SELECT max(a.dt_fim_periodo) FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia) AS dt_fim_periodo,
               -- as linhas investigadas nomeadas nos ofícios, para o subtítulo do bloco
               (SELECT string_agg(DISTINCT a.nu_linha_alvo, ' · ')
                  FROM investigacao.tel_tb02_arquivos a
                 WHERE a.id_telefonia = t.id_telefonia AND a.nu_linha_alvo IS NOT NULL) AS nu_linhas_alvo,
               (SELECT count(*) FROM investigacao.tel_tb05_erbs e
                 WHERE e.id_telefonia = t.id_telefonia AND e.vl_latitude IS NOT NULL) AS qt_erbs_no_mapa
        FROM investigacao.tel_tb01_telefonia t
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = t.id_usuario
        -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
        LEFT JOIN investigacao.inq_tb06_apelidos ap
               ON ap.tp_prova = 'telefonia' AND ap.id_prova = t.id_telefonia
        WHERE t.id_inquerito = %s
        ORDER BY t.no_operadora, t.id_telefonia
        """,
        (id_inquerito,),
    )


def obter_telefonia(id_telefonia: int) -> dict | None:
    linhas = _consultar(
        """
        SELECT t.*, i.nu_inquerito, u.no_usuario AS extraido_por
        FROM investigacao.tel_tb01_telefonia t
        JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = t.id_inquerito
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = t.id_usuario
        WHERE t.id_telefonia = %s
        """,
        (id_telefonia,),
    )
    return linhas[0] if linhas else None


def obter_id_inquerito_de_telefonia(id_telefonia: int) -> int | None:
    linhas = _consultar(
        "SELECT id_inquerito FROM investigacao.tel_tb01_telefonia WHERE id_telefonia = %s",
        (id_telefonia,))
    return linhas[0]["id_inquerito"] if linhas else None


def listar_arquivos_telefonia(id_telefonia: int) -> list[dict]:
    """A procedência do card: cada arquivo importado, o que trouxe e que período cobre."""
    return _consultar(
        """
        SELECT a.*, u.no_usuario AS importado_por
        FROM investigacao.tel_tb02_arquivos a
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = a.id_usuario
        WHERE a.id_telefonia = %s
        ORDER BY a.dt_importacao, a.id_arquivo_telefonia
        """,
        (id_telefonia,),
    )


# ---------------------------------------------------------------------------
# Listagens paginadas — o que a tela telefonia.html lê
# ---------------------------------------------------------------------------
# O card grande (815 mil chamadas, 200 mil números, 4 mil antenas em produção) não cabe no
# navegador: a tela antiga carregava as 6 abas inteiras de uma vez e filtrava em memória, o que
# travava tudo. Estas listagens filtram e paginam NO SERVIDOR e devolvem
# {total, pagina, limite, itens} — o mesmo contrato das listagens de extração
# (ver investigacao_utils.listar_ligacoes e js/utils/tabela_paginada.js).
#
# Duas decisões de desempenho moram aqui e não na tela:
#
#   1. qt_chamadas por ERB/número é calculada SÓ para a página atual (um LEFT JOIN de 100 CGIs
#      contra o CDR indexado), e não por subselect correlacionado sobre as 200 mil linhas — que é
#      o que deixava a aba de números lenta por minutos em produção.
#   2. sem filtro, o total sai de TEL_TB01 (uma linha) em vez de COUNT(*) sobre 815 mil linhas.

_LIMITE_MAX_TEL = 20000
_LIMITE_PAGINA_TEL = 100


def _paginar_tel(limite: int = _LIMITE_PAGINA_TEL, deslocamento: int = 0,
                 pagina: int | None = None) -> tuple[int, int, int]:
    """(pagina, limite, offset) normalizados. `pagina` (1-based) manda; sem ela, vale o par
    legado limite/deslocamento — a rota ainda aceita os dois, para clientes antigos."""
    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = _LIMITE_PAGINA_TEL
    limite = max(1, min(limite, _LIMITE_MAX_TEL))
    if pagina is not None:
        try:
            pagina = max(1, int(pagina))
        except (TypeError, ValueError):
            pagina = 1
        return pagina, limite, (pagina - 1) * limite
    try:
        deslocamento = max(0, int(deslocamento or 0))
    except (TypeError, ValueError):
        deslocamento = 0
    return deslocamento // limite + 1, limite, deslocamento


def _termos_busca(busca: str | None, max_termos: int = 5) -> list[str]:
    """A busca da tela em termos: "6722 centro" são dois filtros AND, cada um podendo casar em
    qualquer coluna (OR) — o mesmo comportamento do filtro em memória antigo."""
    if not busca or not str(busca).strip():
        return []
    return [t[:50] for t in str(busca).split() if t][:max_termos]


def _escapar_ilike(termo: str) -> str:
    """Escapa %, _ e \\ do termo para o ILIKE com ESCAPE '\\' não virar curinga."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cond_termos(termos: list[str], colunas: list[str]) -> tuple[list[str], list]:
    """Um AND por termo, um OR por coluna: cada termo precisa aparecer em PELO MENOS uma coluna.
    Devolve (condições, params) para emendar no WHERE."""
    condicoes: list[str] = []
    params: list = []
    for termo in termos:
        like = f"%{_escapar_ilike(termo)}%"
        or_colunas = " OR ".join(f"{col} ILIKE %s ESCAPE '\\'" for col in colunas)
        condicoes.append(f"({or_colunas})")
        params.extend([like] * len(colunas))
    return condicoes, params


def _total_card(id_telefonia: int, campo: str) -> int | None:
    """O total sem filtro, direto do card — evita o COUNT(*) sobre o CDR inteiro."""
    linhas = _consultar(
        f"SELECT {campo} FROM investigacao.tel_tb01_telefonia WHERE id_telefonia = %s",
        (id_telefonia,),
    )
    return int(linhas[0][campo.lower()]) if linhas else 0


def _data_iso_ok(valor: str | None) -> str | None:
    trama = str(valor or "").strip()[:10]
    return trama if _RE_DATA_ISO.fullmatch(trama) else None


def listar_linhas_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                            deslocamento: int = 0, *, pagina: int | None = None,
                            busca: str | None = None,
                            situacao: str | None = None) -> dict:
    """Fichas cadastrais, paginadas no servidor. `situacao` é igualdade exata (vem do select)."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    filtros = ["l.id_telefonia = %s"]
    params: list = [id_telefonia]
    if situacao:
        filtros.append("l.tp_situacao = %s")
        params.append(situacao)
    if termos:
        conds, p = _cond_termos(termos, [
            "l.nu_linha", "l.no_cliente", "l.nu_cpf_cnpj", "l.nu_rg", "l.tx_endereco",
            "l.no_bairro", "l.no_municipio", "l.sg_uf", "l.tp_modalidade", "l.tp_situacao"])
        filtros.extend(conds)
        params.extend(p)
    where = " AND ".join(filtros)
    tem_filtro = bool(termos or situacao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if tem_filtro:
                cur.execute(
                    f"SELECT count(*) FROM investigacao.tel_tb03_linhas l WHERE {where}", params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_linhas") or 0
            cur.execute(
                f"""SELECT l.*, a.no_arquivo AS no_arquivo_origem
                    FROM investigacao.tel_tb03_linhas l
                    LEFT JOIN investigacao.tel_tb02_arquivos a
                           ON a.id_arquivo_telefonia = l.id_arquivo_telefonia
                    WHERE {where}
                    ORDER BY l.nu_linha, l.dt_habilitacao NULLS LAST, l.id_linha_telefonia
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def listar_chamadas_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                              deslocamento: int = 0, *, pagina: int | None = None,
                              busca: str | None = None, data_de: str | None = None,
                              data_ate: str | None = None, tipo: str | None = None,
                              so_completadas: bool = False,
                              so_com_antena: bool = False) -> dict:
    """O CDR do card, em ordem cronológica, já com a antena resolvida pelo CGI.

    O LEFT JOIN com as ERBs é o que dá valor ao CDR: sem ele o investigador vê
    "724-10-0805342-003" e precisa procurar numa outra aba onde fica isso."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    data_de = _data_iso_ok(data_de)
    data_ate = _data_iso_ok(data_ate)
    tipo = tipo if tipo in ("VOZ", "TUGO") else None
    filtros = ["c.id_telefonia = %s"]
    params: list = [id_telefonia]
    if tipo:
        filtros.append("c.tp_registro = %s")
        params.append(tipo)
    if so_completadas:
        filtros.append("c.cd_status ILIKE 'complet%%'")
    if so_com_antena:
        filtros.append("(NULLIF(c.nu_cgi_origem, '') IS NOT NULL"
                       " OR NULLIF(c.nu_cgi_destino, '') IS NOT NULL)")
    if data_de:
        filtros.append("c.dt_chamada >= %s::date")
        params.append(data_de)
    if data_ate:
        filtros.append("c.dt_chamada < (%s::date + 1)")
        params.append(data_ate)
    if termos:
        conds, p = _cond_termos(termos, [
            "c.nu_chamador", "c.nu_chamado", "c.nu_imei_chamador", "c.nu_imei_chamado",
            "c.nu_cgi_origem", "c.nu_cgi_destino", "c.cd_status", "c.tp_trafego",
            "eo.tx_endereco", "eo.no_municipio", "ed.tx_endereco", "ed.no_municipio"])
        filtros.extend(conds)
        params.extend(p)
    where = " AND ".join(filtros)
    juncao = ("FROM investigacao.tel_tb04_chamadas c "
              "LEFT JOIN investigacao.tel_tb05_erbs eo "
              "       ON eo.id_telefonia = c.id_telefonia AND eo.nu_cgi = c.nu_cgi_origem "
              "LEFT JOIN investigacao.tel_tb05_erbs ed "
              "       ON ed.id_telefonia = c.id_telefonia AND ed.nu_cgi = c.nu_cgi_destino")
    tem_filtro = bool(termos or tipo or so_completadas or so_com_antena or data_de or data_ate)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if tem_filtro:
                cur.execute(f"SELECT count(*) {juncao} WHERE {where}", params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_chamadas") or 0
            cur.execute(
                f"""SELECT c.*,
                           eo.tx_endereco AS tx_endereco_origem,
                           eo.no_municipio AS no_municipio_origem,
                           eo.sg_uf AS sg_uf_origem, eo.vl_latitude AS vl_latitude_origem,
                           eo.vl_longitude AS vl_longitude_origem,
                           eo.tp_tecnologia AS tp_tecnologia_origem_join,
                           ed.tx_endereco AS tx_endereco_destino,
                           ed.no_municipio AS no_municipio_destino,
                           ed.sg_uf AS sg_uf_destino, ed.vl_latitude AS vl_latitude_destino,
                           ed.vl_longitude AS vl_longitude_destino,
                           ed.tp_tecnologia AS tp_tecnologia_destino_join
                    {juncao} WHERE {where}
                    ORDER BY c.dt_chamada NULLS LAST, c.id_chamada_telefonia
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
            # o JOIN traz a tecnologia da ERB para conferência sem sobrescrever a da chamada:
            # vale a da chamada quando ela existe
            for item in itens:
                if not item.get("tp_tecnologia_origem"):
                    item["tp_tecnologia_origem"] = item.pop("tp_tecnologia_origem_join", None)
                else:
                    item.pop("tp_tecnologia_origem_join", None)
                if not item.get("tp_tecnologia_destino"):
                    item["tp_tecnologia_destino"] = item.pop("tp_tecnologia_destino_join", None)
                else:
                    item.pop("tp_tecnologia_destino_join", None)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def _qt_chamadas_por_cgi(id_telefonia: int, cgis: list[str]) -> dict[str, int]:
    """Quantas chamadas passaram por cada CGI da página — um JOIN por lote, e não um subselect
    por linha. O OR preserva a semântica antiga (a chamada conta uma vez mesmo com origem e
    destino no mesmo CGI)."""
    cgis = [c for c in dict.fromkeys(c for c in (cgis or []) if c)]
    if not cgis:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.cgi, count(c.id_chamada_telefonia)
                   FROM unnest(%s::text[]) AS g(cgi)
                   LEFT JOIN investigacao.tel_tb04_chamadas c
                          ON c.id_telefonia = %s
                         AND (c.nu_cgi_origem = g.cgi OR c.nu_cgi_destino = g.cgi)
                   GROUP BY g.cgi""",
                (cgis, id_telefonia))
            return {cgi: int(qt) for cgi, qt in cur.fetchall()}


def listar_erbs_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                          deslocamento: int = 0, *, pagina: int | None = None,
                          busca: str | None = None, uf: str | None = None,
                          so_com_coord: bool = False) -> dict:
    """As antenas do card, com quantas chamadas passaram por cada uma (é o que diz quais
    importam: uma ERB com uma chamada é passagem, uma com trezentas é rotina)."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    filtros = ["e.id_telefonia = %s"]
    params: list = [id_telefonia]
    if uf:
        filtros.append("e.sg_uf = %s")
        params.append(uf)
    if so_com_coord:
        filtros.append("e.vl_latitude IS NOT NULL")
    if termos:
        conds, p = _cond_termos(termos, [
            "e.nu_cgi", "e.sg_erb", "e.no_erb", "e.no_central", "e.no_municipio",
            "e.no_bairro", "e.tx_endereco", "e.sg_uf", "e.tp_tecnologia"])
        filtros.extend(conds)
        params.extend(p)
    where = " AND ".join(filtros)
    tem_filtro = bool(termos or uf or so_com_coord)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if tem_filtro:
                cur.execute(
                    f"SELECT count(*) FROM investigacao.tel_tb05_erbs e WHERE {where}", params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_erbs") or 0
            cur.execute(
                f"""SELECT e.* FROM investigacao.tel_tb05_erbs e
                    WHERE {where}
                    ORDER BY e.sg_uf NULLS LAST, e.no_municipio NULLS LAST, e.nu_cgi
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
    contagens = _qt_chamadas_por_cgi(id_telefonia, [i.get("nu_cgi") for i in itens])
    for item in itens:
        item["qt_chamadas"] = contagens.get(item.get("nu_cgi"), 0)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


# Faixas de horário local, iguais às da tela de localizações (js/localizacoes.html): a camada de
# antenas obedece aos MESMOS filtros da página, senão as duas metades do mapa falariam de períodos
# diferentes.
_PERIODOS_ERB = {
    "madrugada": (0, 5),
    "manha": (6, 11),
    "tarde": (12, 17),
    "noite": (18, 23),
}

# A hora sai convertida de UTC para Brasília porque é assim que o resto do app trata timestamp
# (utils.js converte na exibição): filtrar por outra regra faria a antena sumir num horário
# diferente do que a tela mostra na chamada.
_SQL_HORA_LOCAL_CHAMADA = (
    "EXTRACT(HOUR FROM (c.dt_chamada AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo'))"
)
# o mesmo, para o início da sessão de dados (é por ele que a faixa do dia filtra os dados móveis)
_SQL_HORA_LOCAL_DADOS = (
    "EXTRACT(HOUR FROM (d.dt_inicio AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo'))"
)

# O CGI da antena no cadastro e o CGI no CDR nem sempre são a MESMA string. A Vivo, por exemplo,
# manda "724-11-43321-03476" (MCC-MNC-LAC-CI) na relação de antenas e "2571 347 2" no CDR — que é
# central + ERB + setor da mesma torre. Sem casar os dois formatos, a antena fica sem nenhuma
# chamada: some do mapa quando há filtro de data e mente "0 chamadas" quando não há.
_SQL_CHAVE_CURTA_ERB = (
    "CASE WHEN NULLIF(e.nu_ccc, '') IS NOT NULL AND NULLIF(e.no_erb, '') IS NOT NULL "
    "          AND e.nu_setor IS NOT NULL "
    "     THEN e.nu_ccc || ' ' || e.no_erb || ' ' || e.nu_setor::text END"
)


def listar_erbs_inquerito(
    id_inquerito: int,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
) -> list[dict]:
    """As ERBs com coordenada do inquérito — a camada de antenas do mapa de localizações
    (js/localizacoes_erbs.js). Sem coordenada não há o que desenhar, então a consulta já filtra: a
    operadora manda a antena "CADASTRO NAO ENCONTRADO" sem lat/long.

    **A antena não tem data; a chamada e a sessão de dados têm.** Quem dá tempo à torre é o CDR e
    o relatório de dados: cada chamada e cada sessão guardam o CGI por onde passaram e quando. Por
    isso os filtros temporais da tela chegam aqui e são aplicados às CHAMADAS e às SESSÕES DE
    DADOS — e o que a camada devolve deixa de ser "as antenas do ofício" e passa a ser "as antenas
    por onde a linha passou neste período", com quantas vezes (voz e dados, contados à parte) e
    quando foi a primeira e a última passagem (o mínimo e o máximo dos dois). Com filtro de data ou
    de faixa do dia, antena sem passagem no recorte não volta: ela não esteve lá naquele período, e
    desenhá-la seria afirmar o contrário.

    A sessão de dados é um INTERVALO, não um instante: o recorte por data pega sobreposição
    (a sessão vale enquanto está ativa), e não só o início — sem isso, uma sessão de três dias
    sumiria do mapa filtrando pelo segundo dia. A faixa do dia filtra pelo início.

    Sem filtro nenhum, o comportamento é o de antes: todas as antenas com coordenada, com a
    contagem cheia — inclusive as que não casaram com nenhuma chamada, que ainda são o cadastro
    entregue pela operadora.

    A contagem sai de UMA passada agregada sobre o CDR, e não de um subselect por antena: com um
    ofício grande (4.449 antenas × 815 mil chamadas) o subselect correlacionado custava ~77 ms por
    antena, quase 6 minutos no total, e a requisição não voltava. O `count(DISTINCT)` sobre o UNION
    das duas pontas preserva a semântica do OR antigo: chamada que entrou e saiu pela mesma antena
    conta uma vez."""
    data_de = _data_iso_ok(data_de)     # descarta o que não for AAAA-MM-DD: nada cru entra no SQL
    data_ate = _data_iso_ok(data_ate)
    filtros_chamada = []
    params_chamada: list = []
    filtros_dados = []
    params_dados: list = []
    if data_de:
        filtros_chamada.append("c.dt_chamada >= %s::date")
        params_chamada.append(data_de)
        filtros_dados.append("COALESCE(d.dt_fim, d.dt_inicio) >= %s::date")
        params_dados.append(data_de)
    if data_ate:
        filtros_chamada.append("c.dt_chamada < (%s::date + 1)")
        params_chamada.append(data_ate)
        filtros_dados.append("d.dt_inicio < (%s::date + 1)")
        params_dados.append(data_ate)
    if periodo in _PERIODOS_ERB:
        hora_ini, hora_fim = _PERIODOS_ERB[periodo]
        filtros_chamada.append(f"{_SQL_HORA_LOCAL_CHAMADA} BETWEEN %s AND %s")
        params_chamada.extend([hora_ini, hora_fim])
        filtros_dados.append(f"{_SQL_HORA_LOCAL_DADOS} BETWEEN %s AND %s")
        params_dados.extend([hora_ini, hora_fim])

    recorte = (" AND " + " AND ".join(filtros_chamada)) if filtros_chamada else ""
    recorte_dados = (" AND " + " AND ".join(filtros_dados)) if filtros_dados else ""
    # o UNION das duas pontas da voz usa o mesmo recorte, então os parâmetros entram duas vezes
    params = [id_inquerito] + params_chamada + params_chamada + params_dados + [id_inquerito]
    # com recorte temporal, antena sem passagem (voz ou dados) no período não é desenhada
    so_usadas = ("AND (COALESCE(u.qt_chamadas, 0) + COALESCE(u.qt_dados, 0)) > 0"
                 if (filtros_chamada or filtros_dados) else "")

    return _consultar(
        f"""
        WITH tel AS (
            SELECT id_telefonia FROM investigacao.tel_tb01_telefonia WHERE id_inquerito = %s
        ),
        usos_voz AS (
            SELECT id_telefonia, cgi,
                   count(DISTINCT id_chamada_telefonia) AS qt,
                   min(dt_chamada) AS dt_primeira,
                   max(dt_chamada) AS dt_ultima
            FROM (
                SELECT c.id_chamada_telefonia, c.id_telefonia, c.dt_chamada,
                       NULLIF(c.nu_cgi_origem, '') AS cgi
                  FROM investigacao.tel_tb04_chamadas c
                  JOIN tel ON tel.id_telefonia = c.id_telefonia
                 WHERE NULLIF(c.nu_cgi_origem, '') IS NOT NULL{recorte}
                UNION ALL
                SELECT c.id_chamada_telefonia, c.id_telefonia, c.dt_chamada,
                       NULLIF(c.nu_cgi_destino, '') AS cgi
                  FROM investigacao.tel_tb04_chamadas c
                  JOIN tel ON tel.id_telefonia = c.id_telefonia
                 WHERE NULLIF(c.nu_cgi_destino, '') IS NOT NULL{recorte}
            ) x
            GROUP BY 1, 2
        ),
        usos_dados AS (
            SELECT d.id_telefonia, NULLIF(d.nu_cgi, '') AS cgi,
                   count(*) AS qt,
                   min(d.dt_inicio) AS dt_primeira,
                   max(COALESCE(d.dt_fim, d.dt_inicio)) AS dt_ultima
              FROM investigacao.tel_tb08_dados_moveis d
              JOIN tel ON tel.id_telefonia = d.id_telefonia
             WHERE NULLIF(d.nu_cgi, '') IS NOT NULL{recorte_dados}
             GROUP BY 1, 2
        ),
        antenas AS (
            SELECT e.id_erb, e.id_telefonia, e.nu_cgi, e.sg_erb, e.no_central, e.nu_setor,
                   e.nu_azimute, e.tp_tecnologia, e.vl_latitude, e.vl_longitude, e.sg_uf,
                   e.no_municipio, e.no_bairro, e.tx_endereco,
                   {_SQL_CHAVE_CURTA_ERB} AS chave_curta
              FROM investigacao.tel_tb05_erbs e
              JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
             WHERE t.id_inquerito = %s
               AND e.vl_latitude IS NOT NULL AND e.vl_longitude IS NOT NULL
        ),
        -- as duas grafias possíveis do CGI da mesma antena, para casar com o CDR de qualquer jeito
        chaves AS (
            SELECT id_erb, id_telefonia, nu_cgi AS cgi FROM antenas
            UNION
            SELECT id_erb, id_telefonia, chave_curta FROM antenas WHERE chave_curta IS NOT NULL
        ),
        por_antena AS (
            SELECT k.id_erb,
                   COALESCE(sum(v.qt), 0) AS qt_chamadas,
                   COALESCE(sum(d.qt), 0) AS qt_dados,
                   LEAST(min(v.dt_primeira), min(d.dt_primeira)) AS dt_primeira,
                   GREATEST(max(v.dt_ultima), max(d.dt_ultima)) AS dt_ultima
              FROM chaves k
              LEFT JOIN usos_voz v
                     ON v.id_telefonia = k.id_telefonia AND v.cgi = k.cgi
              LEFT JOIN usos_dados d
                     ON d.id_telefonia = k.id_telefonia AND d.cgi = k.cgi
             GROUP BY 1
        )
        SELECT a.id_erb, a.nu_cgi, a.sg_erb, a.no_central, a.nu_setor, a.nu_azimute,
               a.tp_tecnologia, a.vl_latitude, a.vl_longitude, a.sg_uf, a.no_municipio,
               a.no_bairro, a.tx_endereco, a.id_telefonia, t.no_operadora,
               COALESCE(u.qt_chamadas, 0) AS qt_chamadas,
               COALESCE(u.qt_dados, 0) AS qt_dados,
               u.dt_primeira, u.dt_ultima
        FROM antenas a
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = a.id_telefonia
        LEFT JOIN por_antena u ON u.id_erb = a.id_erb
        WHERE TRUE {so_usadas}
        ORDER BY t.no_operadora, a.nu_cgi
        """,
        tuple(params),
    )


def _enriquecer_numeros(id_telefonia: int, itens: list[dict]) -> None:
    """Completa a página de números com qt_chamadas, titular e IMEI compartilhado — três consultas
    por lote sobre os ~100 da página, em vez de três subselects por linha sobre os 200 mil."""
    telefones = [i.get("nu_telefone") for i in itens if i.get("nu_telefone")]
    imeis = list(dict.fromkeys(i.get("nu_imei") for i in itens if i.get("nu_imei")))
    if not itens:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            qt: dict[str, int] = {}
            if telefones:
                cur.execute(
                    """SELECT g.numero, count(c.id_chamada_telefonia)
                       FROM unnest(%s::text[]) AS g(numero)
                       LEFT JOIN investigacao.tel_tb04_chamadas c
                              ON c.id_telefonia = %s
                             AND (c.nu_chamador = g.numero OR c.nu_chamado = g.numero)
                       GROUP BY g.numero""",
                    (telefones, id_telefonia))
                qt = {numero: int(q) for numero, q in cur.fetchall()}
                cur.execute(
                    """SELECT l.nu_linha, max(l.no_cliente)
                       FROM investigacao.tel_tb03_linhas l
                       WHERE l.id_telefonia = %s AND l.nu_linha = ANY(%s)
                       GROUP BY l.nu_linha""",
                    (id_telefonia, telefones))
                titulares = {linha: titular for linha, titular in cur.fetchall()}
            else:
                titulares = {}
            irmaos: dict[str, list[str]] = {}
            if imeis:
                cur.execute(
                    """SELECT n.nu_imei, array_agg(DISTINCT n.nu_telefone)
                       FROM investigacao.tel_tb06_numeros n
                       WHERE n.id_telefonia = %s AND n.nu_imei = ANY(%s)
                       GROUP BY n.nu_imei""",
                    (id_telefonia, imeis))
                irmaos = {imei: sorted([t for t in tels if t]) for imei, tels in cur.fetchall()}
    for item in itens:
        tel = item.get("nu_telefone")
        imei = item.get("nu_imei")
        item["qt_chamadas"] = qt.get(tel, 0)
        if item.get("no_cliente") is None:
            item["no_cliente"] = titulares.get(tel)
        todos = irmaos.get(imei, []) if imei else []
        item["nu_outros_telefones"] = [t for t in todos if t != tel]


def listar_numeros_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                             deslocamento: int = 0, *, pagina: int | None = None,
                             busca: str | None = None, so_com_imei: bool = False,
                             so_compartilhado: bool = False,
                             ordenar: str | None = None) -> dict:
    """Os números do resumo, paginados. A ordem padrão é por telefone (barata); `ordenar=chamadas`
    traz o ranking de interlocutores a partir do CDR agregado — mais caro, e por isso opt-in."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    filtros = ["n.id_telefonia = %s"]
    params: list = [id_telefonia]
    if so_com_imei:
        filtros.append("NULLIF(n.nu_imei, '') IS NOT NULL")
    if so_compartilhado:
        filtros.append(
            """n.nu_imei IN (
                   SELECT n2.nu_imei FROM investigacao.tel_tb06_numeros n2
                   WHERE n2.id_telefonia = %s
                     AND NULLIF(n2.nu_imei, '') IS NOT NULL
                   GROUP BY n2.nu_imei HAVING count(DISTINCT n2.nu_telefone) > 1)""")
        params.append(id_telefonia)
    if termos:
        for termo in termos:
            like = f"%{_escapar_ilike(termo)}%"
            filtros.append(
                """(n.nu_telefone ILIKE %s ESCAPE '\\'
                    OR n.nu_imei ILIKE %s ESCAPE '\\'
                    OR EXISTS (SELECT 1 FROM investigacao.tel_tb03_linhas l
                               WHERE l.id_telefonia = n.id_telefonia
                                 AND l.nu_linha = n.nu_telefone
                                 AND l.no_cliente ILIKE %s ESCAPE '\\'))""")
            params.extend([like, like, like])
    where = " AND ".join(filtros)
    tem_filtro = bool(termos or so_com_imei or so_compartilhado)

    # ranking de interlocutores: agrega o CDR (uma varredura) em vez de contar por número
    # (200 mil varreduras). Só faz sentido sem busca textual — com busca, o conjunto já é pequeno
    # e a ordem por telefone basta.
    if ordenar == "chamadas" and not tem_filtro:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT count(*) FROM investigacao.tel_tb06_numeros n
                       WHERE n.id_telefonia = %s""", (id_telefonia,))
                total = cur.fetchone()[0]
                cur.execute(
                    """WITH chamadas AS (
                           SELECT c.nu_chamador AS numero
                           FROM investigacao.tel_tb04_chamadas c
                           WHERE c.id_telefonia = %s AND NULLIF(c.nu_chamador, '') IS NOT NULL
                           UNION ALL
                           SELECT c.nu_chamado
                           FROM investigacao.tel_tb04_chamadas c
                           WHERE c.id_telefonia = %s AND NULLIF(c.nu_chamado, '') IS NOT NULL
                       ), ranking AS (
                           SELECT numero, count(*) AS qt
                           FROM chamadas GROUP BY numero
                       )
                       SELECT n.*, COALESCE(r.qt, 0) AS qt_chamadas,
                              (SELECT max(l.no_cliente)
                               FROM investigacao.tel_tb03_linhas l
                               WHERE l.id_telefonia = n.id_telefonia
                                 AND l.nu_linha = n.nu_telefone) AS no_cliente
                       FROM investigacao.tel_tb06_numeros n
                       LEFT JOIN ranking r ON r.numero = n.nu_telefone
                       WHERE n.id_telefonia = %s
                       ORDER BY qt_chamadas DESC, n.nu_telefone
                       LIMIT %s OFFSET %s""",
                    (id_telefonia, id_telefonia, id_telefonia, limite, offset))
                itens = _linhas(cur)
        _enriquecer_numeros_irmaos(id_telefonia, itens)
        return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}

    with get_connection() as conn:
        with conn.cursor() as cur:
            if tem_filtro:
                cur.execute(
                    f"SELECT count(*) FROM investigacao.tel_tb06_numeros n WHERE {where}",
                    params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_numeros") or 0
            cur.execute(
                f"""SELECT n.* FROM investigacao.tel_tb06_numeros n
                    WHERE {where}
                    ORDER BY n.nu_telefone
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
    _enriquecer_numeros(id_telefonia, itens)
    if ordenar == "chamadas":
        itens.sort(key=lambda i: (-int(i.get("qt_chamadas") or 0), str(i.get("nu_telefone") or "")))
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def _enriquecer_numeros_irmaos(id_telefonia: int, itens: list[dict]) -> None:
    """Só o IMEI compartilhado para o caminho do ranking (qt e titular já vieram na query)."""
    imeis = list(dict.fromkeys(i.get("nu_imei") for i in itens if i.get("nu_imei")))
    if not imeis:
        for item in itens:
            item["nu_outros_telefones"] = []
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.nu_imei, array_agg(DISTINCT n.nu_telefone)
                   FROM investigacao.tel_tb06_numeros n
                   WHERE n.id_telefonia = %s AND n.nu_imei = ANY(%s)
                   GROUP BY n.nu_imei""",
                (id_telefonia, imeis))
            irmaos = {imei: sorted([t for t in tels if t]) for imei, tels in cur.fetchall()}
    for item in itens:
        todos = irmaos.get(item.get("nu_imei"), []) if item.get("nu_imei") else []
        item["nu_outros_telefones"] = [t for t in todos if t != item.get("nu_telefone")]


def listar_sessoes_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                             deslocamento: int = 0, *, pagina: int | None = None,
                             busca: str | None = None) -> dict:
    """Sessões de chamada por WiFi (a linha falando por IP, fora da rede móvel)."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    filtros = ["s.id_telefonia = %s"]
    params: list = [id_telefonia]
    if termos:
        conds, p = _cond_termos(termos, [
            "s.nu_linha", "s.tx_ip", "s.no_servico", "s.tp_trafego",
            "s.tp_evento_inicio", "s.tp_evento_fim"])
        filtros.extend(conds)
        params.extend(p)
    where = " AND ".join(filtros)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if termos:
                cur.execute(
                    f"SELECT count(*) FROM investigacao.tel_tb07_sessoes_wifi s WHERE {where}",
                    params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_sessoes") or 0
            cur.execute(
                f"""SELECT s.* FROM investigacao.tel_tb07_sessoes_wifi s
                    WHERE {where}
                    ORDER BY s.dt_inicio NULLS LAST, s.id_sessao_telefonia
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def listar_dados_moveis_telefonia(id_telefonia: int, limite: int = _LIMITE_PAGINA_TEL,
                                  deslocamento: int = 0, *, pagina: int | None = None,
                                  busca: str | None = None,
                                  data_de: str | None = None,
                                  data_ate: str | None = None) -> dict:
    """As sessões de dados móveis do card, em ordem cronológica, já com a antena resolvida pelo
    CGI — o mesmo LEFT JOIN do CDR: o endereço canônico mora em TEL_TB05 (a sessão traz só o
    CGI, e o ofício de voz completa o resto)."""
    pagina, limite, offset = _paginar_tel(limite, deslocamento, pagina)
    termos = _termos_busca(busca)
    data_de = _data_iso_ok(data_de)
    data_ate = _data_iso_ok(data_ate)
    filtros = ["d.id_telefonia = %s"]
    params: list = [id_telefonia]
    if data_de:
        filtros.append("d.dt_inicio >= %s::date")
        params.append(data_de)
    if data_ate:
        filtros.append("d.dt_inicio < (%s::date + 1)")
        params.append(data_ate)
    if termos:
        conds, p = _cond_termos(termos, [
            "d.nu_linha", "d.tx_ip", "d.tx_ipv6", "d.nu_imei", "d.nu_cgi",
            "d.tp_tecnologia", "d.tp_rede", "e.tx_endereco", "e.no_municipio"])
        filtros.extend(conds)
        params.extend(p)
    where = " AND ".join(filtros)
    juncao = ("FROM investigacao.tel_tb08_dados_moveis d "
              "LEFT JOIN investigacao.tel_tb05_erbs e "
              "       ON e.id_telefonia = d.id_telefonia AND e.nu_cgi = d.nu_cgi")
    tem_filtro = bool(termos or data_de or data_ate)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if tem_filtro:
                cur.execute(f"SELECT count(*) {juncao} WHERE {where}", params)
                total = cur.fetchone()[0]
            else:
                total = _total_card(id_telefonia, "qt_dados") or 0
            cur.execute(
                f"""SELECT d.*,
                           e.tx_endereco AS tx_endereco_erb,
                           e.no_municipio AS no_municipio_erb,
                           e.no_bairro AS no_bairro_erb,
                           e.sg_uf AS sg_uf_erb,
                           e.vl_latitude AS vl_latitude_erb,
                           e.vl_longitude AS vl_longitude_erb
                    {juncao} WHERE {where}
                    ORDER BY d.dt_inicio NULLS LAST, d.id_dado_movel
                    LIMIT %s OFFSET %s""",
                params + [limite, offset])
            itens = _linhas(cur)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def listar_filtros_telefonia(id_telefonia: int) -> dict:
    """Os valores reais dos selects da tela — só as UFs e situações que este card tem, para não
    oferecer filtro de UF que não existe neste ofício. Duas DISTINCT baratas, sem paginação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT e.sg_uf FROM investigacao.tel_tb05_erbs e
                   WHERE e.id_telefonia = %s AND e.sg_uf IS NOT NULL AND e.sg_uf <> ''
                   ORDER BY e.sg_uf""",
                (id_telefonia,))
            ufs = [linha[0] for linha in cur.fetchall()]
            cur.execute(
                """SELECT DISTINCT l.tp_situacao FROM investigacao.tel_tb03_linhas l
                   WHERE l.id_telefonia = %s AND l.tp_situacao IS NOT NULL AND l.tp_situacao <> ''
                   ORDER BY l.tp_situacao""",
                (id_telefonia,))
            situacoes = [linha[0] for linha in cur.fetchall()]
    return {"ufs": ufs, "situacoes": situacoes}


# ---------------------------------------------------------------------------
# Encontros por antena + âncoras (residência/trabalho provável) — nível inquérito
# ---------------------------------------------------------------------------
# Responde "quem estava com quem, onde e quando": duas linhas na mesma ERB dentro de uma
# janela curta. A antena é REGIÃO, não posição (mesmo aviso da tela de telefonia e do mapa):
# o encontro é indício de co-localização, não prova de que estiveram juntos.
#
# Desenho para não explodir no CDR grande:
#   · só entram as linhas do cadastro (TEL_TB03) — os alvos do afastamento — e, sem cadastro,
#     as 20 linhas de maior volume no CDR (para não fazer cartesiano de 200 mil números);
#   · cada chamada rende até 2 presenças (chamador→CGI origem, chamado→CGI destino) e cada
#     sessão de dados rende 1 (linha→CGI no DT_INICIO — aproximação: a sessão é um intervalo,
#     aqui vale o início; o overlap exato de intervalos fica para uma segunda passada);
#   · o self-join é por (CGI, janela temporal) só entre essas presenças, com o par ordenado
#     para não devolver o espelho.

_MIN_DIAS_ANCORA_TEL = 3


# Normalização de número para cruzamento: só dígitos, sem o 55 do país. O cadastro traz
# "67998065096" e o CDR da outra operadora pode trazer "5567998065096" — sem cortar o prefixo a
# mesma linha nunca se encontra. right(..., 11) preserva DDD+numero (fixo de 10 dígitos passa
# intacto, já que right devolve tudo quando a string é mais curta que o pedido).
_SQL_NUMERO_NORMALIZADO = "right(regexp_replace({0}, '\\D', '', 'g'), 11)"


def _linhas_alvo_inquerito(cur, id_inquerito: int, limite_fallback: int = 20) -> list[str]:
    """Linhas investigadas do inquérito (dígitos, sem 55). Sem cadastro, as de maior volume no CDR."""
    cur.execute(
        f"""
        SELECT DISTINCT {_SQL_NUMERO_NORMALIZADO.format('l.nu_linha')}
        FROM investigacao.tel_tb03_linhas l
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = l.id_telefonia
        WHERE t.id_inquerito = %s
          AND NULLIF({_SQL_NUMERO_NORMALIZADO.format('l.nu_linha')}, '') IS NOT NULL
        """,
        (id_inquerito,),
    )
    alvos = [r[0] for r in cur.fetchall() if r[0]]
    if alvos:
        return alvos
    cur.execute(
        f"""
        SELECT numero FROM (
            SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')} AS numero, count(*) AS qt
            FROM investigacao.tel_tb04_chamadas c
            JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
            WHERE t.id_inquerito = %s
              AND NULLIF({_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')}, '') IS NOT NULL
            GROUP BY 1
            UNION ALL
            SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')} AS numero, count(*) AS qt
            FROM investigacao.tel_tb04_chamadas c
            JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
            WHERE t.id_inquerito = %s
              AND NULLIF({_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')}, '') IS NOT NULL
            GROUP BY 1
        ) x
        GROUP BY numero
        ORDER BY sum(qt) DESC
        LIMIT %s
        """,
        (id_inquerito, id_inquerito, limite_fallback),
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def _cobertura_antena_inquerito(cur, id_inquerito: int) -> dict:
    """Quanto do material tem antena: sem CGI não há encontro nem âncora que se apure.

    Leitura barata (usa os índices parciais de CGI) para a tela explicar o vazio em vez de
    mostrar uma tabela de traços: "CDR com 25 de 81 chamadas com antena" diz mais que "—".
    """
    cur.execute(
        """
        SELECT (SELECT count(*) FROM investigacao.tel_tb04_chamadas c
                 JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
                WHERE t.id_inquerito = %s) AS qt_chamadas,
               (SELECT count(*) FROM investigacao.tel_tb04_chamadas c
                 JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
                WHERE t.id_inquerito = %s
                  AND (NULLIF(c.nu_cgi_origem, '') IS NOT NULL
                       OR NULLIF(c.nu_cgi_destino, '') IS NOT NULL)) AS qt_chamadas_com_cgi,
               (SELECT count(*) FROM investigacao.tel_tb08_dados_moveis d
                 JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia
                WHERE t.id_inquerito = %s) AS qt_dados,
               (SELECT count(*) FROM investigacao.tel_tb08_dados_moveis d
                 JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia
                WHERE t.id_inquerito = %s AND NULLIF(d.nu_cgi, '') IS NOT NULL) AS qt_dados_com_cgi,
               (SELECT count(*) FROM investigacao.tel_tb05_erbs e
                 JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
                WHERE t.id_inquerito = %s AND e.vl_latitude IS NOT NULL) AS qt_erbs_no_mapa
        """,
        (id_inquerito, id_inquerito, id_inquerito, id_inquerito, id_inquerito),
    )
    linhas = _linhas(cur)
    return linhas[0] if linhas else {}


def listar_encontros_inquerito(id_inquerito: int, janela_min: int = 30, min_encontros: int = 2,
                               data_de: str | None = None, data_ate: str | None = None,
                               limite: int = 200) -> dict:
    """Pares de linhas na mesma ERB dentro da janela — com a antena resolvida.

    janela_min (5–480, padrão 30) é a tolerância entre as duas passagens pela mesma torre;
    min_encontros (padrão 2) corta o par que se cruzou uma única vez por acaso. data_de/data_ate
    (AAAA-MM-DD) restringem o período. Devolve {alvos, encontros, aviso}.
    """
    try:
        janela_min = max(5, min(int(janela_min or 30), 480))
    except (TypeError, ValueError):
        janela_min = 30
    try:
        min_encontros = max(1, min(int(min_encontros or 2), 100))
    except (TypeError, ValueError):
        min_encontros = 2
    try:
        limite = max(1, min(int(limite or 200), 1000))
    except (TypeError, ValueError):
        limite = 200
    data_de = _data_iso_ok(data_de)
    data_ate = _data_iso_ok(data_ate)
    with get_connection() as conn:
        with conn.cursor() as cur:
            alvos = _linhas_alvo_inquerito(cur, id_inquerito)
            if not alvos:
                return {"alvos": [], "encontros": [],
                        "aviso": "Sem linhas nem chamadas com número identificado neste inquérito."}
            filtro_data_voz = ""
            params_data: list = []
            if data_de:
                filtro_data_voz += " AND c.dt_chamada >= %s::date"
                params_data.append(data_de)
            if data_ate:
                filtro_data_voz += " AND c.dt_chamada < (%s::date + 1)"
                params_data.append(data_ate)
            filtro_data_dados = filtro_data_voz.replace("c.dt_chamada", "d.dt_inicio")
            cur.execute(
                f"""
                WITH tel AS (
                    SELECT id_telefonia FROM investigacao.tel_tb01_telefonia WHERE id_inquerito = %s
                ),
                alvos(numero) AS (
                    SELECT unnest(%s::text[])
                ),
                pres_voz AS (
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')} AS numero,
                           NULLIF(c.nu_cgi_origem, '') AS cgi, c.dt_chamada AS dt, 'voz' AS fonte
                    FROM investigacao.tel_tb04_chamadas c
                    JOIN tel ON tel.id_telefonia = c.id_telefonia
                    WHERE c.dt_chamada IS NOT NULL AND NULLIF(c.nu_cgi_origem, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')} IN (SELECT numero FROM alvos)
                      {filtro_data_voz}
                    UNION ALL
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')} AS numero,
                           NULLIF(c.nu_cgi_destino, '') AS cgi, c.dt_chamada AS dt, 'voz' AS fonte
                    FROM investigacao.tel_tb04_chamadas c
                    JOIN tel ON tel.id_telefonia = c.id_telefonia
                    WHERE c.dt_chamada IS NOT NULL AND NULLIF(c.nu_cgi_destino, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')} IN (SELECT numero FROM alvos)
                      {filtro_data_voz}
                ),
                pres_dados AS (
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('d.nu_linha')} AS numero,
                           NULLIF(d.nu_cgi, '') AS cgi, d.dt_inicio AS dt, 'dados' AS fonte
                    FROM investigacao.tel_tb08_dados_moveis d
                    JOIN tel ON tel.id_telefonia = d.id_telefonia
                    WHERE d.dt_inicio IS NOT NULL AND NULLIF(d.nu_cgi, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('d.nu_linha')} IN (SELECT numero FROM alvos)
                      {filtro_data_dados}
                ),
                pres AS (
                    SELECT * FROM pres_voz UNION ALL SELECT * FROM pres_dados
                ),
                pares AS (
                    SELECT LEAST(a.numero, b.numero) AS linha_a,
                           GREATEST(a.numero, b.numero) AS linha_b,
                           a.cgi AS cgi,
                           a.dt AS dt_a, b.dt AS dt_b,
                           a.fonte AS fonte_a, b.fonte AS fonte_b
                    FROM pres a
                    JOIN pres b ON b.cgi = a.cgi AND b.numero > a.numero
                               AND abs(extract(epoch FROM (b.dt - a.dt))) <= %s * 60
                ),
                agg AS (
                    SELECT linha_a, linha_b, cgi, count(*) AS qt_encontros,
                           min(LEAST(dt_a, dt_b)) AS dt_primeiro,
                           max(GREATEST(dt_a, dt_b)) AS dt_ultimo
                    FROM pares
                    GROUP BY 1, 2, 3
                    HAVING count(*) >= %s
                )
                SELECT a.linha_a, a.linha_b, a.cgi, a.qt_encontros, a.dt_primeiro, a.dt_ultimo,
                       e.tx_endereco, e.no_municipio, e.sg_uf, e.vl_latitude, e.vl_longitude
                FROM agg a
                LEFT JOIN LATERAL (
                    SELECT e.tx_endereco, e.no_municipio, e.sg_uf, e.vl_latitude, e.vl_longitude
                    FROM investigacao.tel_tb05_erbs e
                    JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
                    WHERE t.id_inquerito = %s AND e.nu_cgi = a.cgi
                    ORDER BY (e.vl_latitude IS NOT NULL) DESC, e.id_erb
                    LIMIT 1
                ) e ON true
                ORDER BY a.qt_encontros DESC, a.dt_primeiro
                LIMIT %s
                """,
                (id_inquerito, alvos, *params_data, *params_data, *params_data,
                 janela_min, min_encontros, id_inquerito, limite),
            )
            encontros = _linhas(cur)
            cobertura = _cobertura_antena_inquerito(cur, id_inquerito)
    return {"alvos": alvos, "encontros": encontros,
            "janela_min": janela_min, "min_encontros": min_encontros,
            "cobertura": cobertura,
            "aviso": "Antena é região, não posição: o encontro é co-localização na mesma torre "
                     "dentro da janela, não prova de que estiveram juntos."}


def listar_ancoras_inquerito(id_inquerito: int) -> dict:
    """Residência/trabalho provável por linha: a ERB mais repetida de madrugada e em horário comercial.

    Residência = CGI com mais madrugadas distintas (22h–06h, hora de Brasília); trabalho = CGI com
    mais dias úteis distintos 09h–18h (excluída a da residência). Só marca com pelo menos
    _MIN_DIAS_ANCORA_TEL dias — abaixo disso é passagem, não rotina. Mesmo critério de
    investigacao_utils._marcar_ancoras, aplicado às linhas do afastamento (voz + início da sessão
    de dados).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            alvos = _linhas_alvo_inquerito(cur, id_inquerito)
            if not alvos:
                return {"alvos": [], "ancoras": [],
                        "cobertura": _cobertura_antena_inquerito(cur, id_inquerito)}
            cur.execute(
                f"""
                WITH tel AS (
                    SELECT id_telefonia FROM investigacao.tel_tb01_telefonia WHERE id_inquerito = %s
                ),
                alvos(numero) AS (
                    SELECT unnest(%s::text[])
                ),
                pres AS (
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')} AS numero,
                           NULLIF(c.nu_cgi_origem, '') AS cgi, c.dt_chamada AS dt
                    FROM investigacao.tel_tb04_chamadas c
                    JOIN tel ON tel.id_telefonia = c.id_telefonia
                    WHERE c.dt_chamada IS NOT NULL AND NULLIF(c.nu_cgi_origem, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamador')} IN (SELECT numero FROM alvos)
                    UNION ALL
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')} AS numero,
                           NULLIF(c.nu_cgi_destino, '') AS cgi, c.dt_chamada AS dt
                    FROM investigacao.tel_tb04_chamadas c
                    JOIN tel ON tel.id_telefonia = c.id_telefonia
                    WHERE c.dt_chamada IS NOT NULL AND NULLIF(c.nu_cgi_destino, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('c.nu_chamado')} IN (SELECT numero FROM alvos)
                    UNION ALL
                    SELECT {_SQL_NUMERO_NORMALIZADO.format('d.nu_linha')} AS numero,
                           NULLIF(d.nu_cgi, '') AS cgi, d.dt_inicio AS dt
                    FROM investigacao.tel_tb08_dados_moveis d
                    JOIN tel ON tel.id_telefonia = d.id_telefonia
                    WHERE d.dt_inicio IS NOT NULL AND NULLIF(d.nu_cgi, '') IS NOT NULL
                      AND {_SQL_NUMERO_NORMALIZADO.format('d.nu_linha')} IN (SELECT numero FROM alvos)
                ),
                com_hora AS (
                    SELECT numero, cgi,
                           (dt AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo') AS local,
                           (dt AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')::date AS dia
                    FROM pres
                ),
                noite AS (
                    SELECT numero, cgi, count(DISTINCT dia) AS qt_noites,
                           count(*) AS qt_usos, min(dia) AS dt_primeiro, max(dia) AS dt_ultimo
                    FROM com_hora
                    WHERE extract(hour FROM local) IN (22, 23, 0, 1, 2, 3, 4, 5)
                    GROUP BY 1, 2
                ),
                dia_util AS (
                    SELECT numero, cgi, count(DISTINCT dia) AS qt_dias_uteis,
                           count(*) AS qt_usos
                    FROM com_hora
                    WHERE extract(hour FROM local) BETWEEN 9 AND 17
                      AND extract(isodow FROM local) BETWEEN 1 AND 5
                    GROUP BY 1, 2
                ),
                rank_noite AS (
                    SELECT numero, cgi, qt_noites, qt_usos, dt_primeiro, dt_ultimo,
                           row_number() OVER (PARTITION BY numero ORDER BY qt_noites DESC, qt_usos DESC) AS rn
                    FROM noite WHERE qt_noites >= %s
                ),
                rank_dia AS (
                    SELECT numero, cgi, qt_dias_uteis, qt_usos,
                           row_number() OVER (PARTITION BY numero ORDER BY qt_dias_uteis DESC, qt_usos DESC) AS rn
                    FROM dia_util WHERE qt_dias_uteis >= %s
                )
                SELECT a.numero AS linha,
                       n.cgi AS cgi_residencia, n.qt_noites, n.qt_usos AS qt_usos_noite,
                       n.dt_primeiro AS dt_primeiro_noite, n.dt_ultimo AS dt_ultimo_noite,
                       er.tx_endereco AS endereco_residencia, er.no_municipio AS municipio_residencia,
                       er.sg_uf AS uf_residencia, er.vl_latitude AS lat_residencia,
                       er.vl_longitude AS lon_residencia,
                       d.cgi AS cgi_trabalho, d.qt_dias_uteis, d.qt_usos AS qt_usos_dia,
                       et.tx_endereco AS endereco_trabalho, et.no_municipio AS municipio_trabalho,
                       et.sg_uf AS uf_trabalho, et.vl_latitude AS lat_trabalho,
                       et.vl_longitude AS lon_trabalho
                FROM (SELECT DISTINCT numero FROM alvos) a
                LEFT JOIN rank_noite n ON n.numero = a.numero AND n.rn = 1
                LEFT JOIN rank_dia d ON d.numero = a.numero AND d.rn = 1
                       AND (n.cgi IS NULL OR d.cgi <> n.cgi)
                LEFT JOIN LATERAL (
                    SELECT e.tx_endereco, e.no_municipio, e.sg_uf, e.vl_latitude, e.vl_longitude
                    FROM investigacao.tel_tb05_erbs e
                    JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
                    WHERE t.id_inquerito = %s AND e.nu_cgi = n.cgi
                    ORDER BY (e.vl_latitude IS NOT NULL) DESC, e.id_erb
                    LIMIT 1
                ) er ON true
                LEFT JOIN LATERAL (
                    SELECT e.tx_endereco, e.no_municipio, e.sg_uf, e.vl_latitude, e.vl_longitude
                    FROM investigacao.tel_tb05_erbs e
                    JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = e.id_telefonia
                    WHERE t.id_inquerito = %s AND e.nu_cgi = d.cgi
                    ORDER BY (e.vl_latitude IS NOT NULL) DESC, e.id_erb
                    LIMIT 1
                ) et ON true
                ORDER BY a.numero
                """,
                (id_inquerito, alvos, _MIN_DIAS_ANCORA_TEL, _MIN_DIAS_ANCORA_TEL,
                 id_inquerito, id_inquerito),
            )
            ancoras = _linhas(cur)
            cobertura = _cobertura_antena_inquerito(cur, id_inquerito)
    for linha in ancoras:
        if linha.get("cgi_residencia"):
            linha["tp_ancora_residencia"] = "residencia_provavel"
        if linha.get("cgi_trabalho"):
            linha["tp_ancora_trabalho"] = "trabalho_provavel"
    return {"alvos": alvos, "ancoras": ancoras, "cobertura": cobertura}


# Ordem inversa das dependências: as filhas primeiro, TB02 depois (as filhas apontam para ela).
_TABELAS_FILHAS_TELEFONIA = [
    "tel_tb03_linhas", "tel_tb04_chamadas", "tel_tb05_erbs", "tel_tb06_numeros",
    "tel_tb07_sessoes_wifi", "tel_tb08_dados_moveis", "tel_tb02_arquivos",
]


def apagar_telefonia(id_telefonia: int, apagar_documentos: bool = True) -> bool:
    """Remove o card de telefonia inteiro e o que ele levantou. Por padrão leva junto os documentos
    de texto que alimentam a IA — todos os arquivos daquela operadora vieram para este card.
    False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM investigacao.tel_tb01_telefonia WHERE id_telefonia = %s",
                (id_telefonia,))
            if cur.fetchone() is None:
                return False
            cur.execute(
                """
                SELECT id_documento FROM investigacao.tel_tb02_arquivos
                WHERE id_telefonia = %s AND id_documento IS NOT NULL
                """,
                (id_telefonia,))
            documentos = [linha[0] for linha in cur.fetchall()]
            for tabela in _TABELAS_FILHAS_TELEFONIA:
                cur.execute(f"DELETE FROM investigacao.{tabela} WHERE id_telefonia = %s", (id_telefonia,))
            cur.execute("DELETE FROM investigacao.tel_tb01_telefonia WHERE id_telefonia = %s",
                        (id_telefonia,))
            # As anotações da mesa apontam para a linha por TP_ORIGEM/ID_ORIGEM, sem FK
            # (vínculo polimórfico): sem esta limpeza, a evidência do nó fica pendurada
            # num ID morto — e o ID é IDENTITY, então a próxima importação o reaproveita
            # e a evidência passa a apontar para OUTRO registro (ver limpar_anotacoes_orfas).
            limpar_anotacoes_orfas(cur, "telefonia_linha", "telefonia_chamada",
                                   "telefonia_numero")
            if apagar_documentos and documentos:
                cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = ANY(%s)",
                            (documentos,))
        conn.commit()
    return True
