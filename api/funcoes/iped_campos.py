"""Camada de campos do índice Lucene de um caso do IPED — a única porta de entrada para ler
um item que o `IpedDumper` emitiu.

Existe porque um caso do IPED é MUITO variável: a mesma pasta-caso pode ter vindo de um
celular (UFDR/Cellebrite), de uma quebra de sigilo de nuvem (Google Takeout, iCloud), de um
HD de computador ou de tudo isso junto. Nenhum campo pode ser tratado como obrigatório, e o
conjunto de campos que existe muda de caso para caso e de versão para versão do IPED. Toda
função daqui devolve None/lista vazia em vez de levantar, e o chamador segue em frente.

Três armadilhas do índice, todas medidas contra casos reais (ver docs/extracao_iped.md):

1. CAMPO MULTIVALORADO — o Lucene permite o mesmo nome de campo várias vezes no mesmo
   documento e o IPED usa isso a rodo. Depois da correção do dumper, um campo chega como
   string quando tem um valor só e como LISTA quando tem vários: `_valor`/`_valores` são a
   única forma segura de ler.

2. SUFIXO DE TIPO — `IndexItem.addMetadataKeyToDoc` renomeia campo de metadado sem tipo
   declarado: valor numérico vira `chave:number`, data vira `chave:date`, e valor que não
   casa com o tipo declarado vira `chave:string`. No caso de exemplo isso aparece como
   `CarvedOffset` E `CarvedOffset:number`, `image:Version` E `image:Version:number`. Quem
   procura um campo novo tem que tentar os quatro nomes — é o que `_valor_variantes` faz.

3. CATEGORIA NÃO É TRADUZIDA — a localização (`iped-categories_pt_BR.properties`) só é
   aplicada na exibição (`Category.getName()` e o relatório HTML); o índice guarda sempre o
   nome canônico em inglês, e a busca do IPED faz o caminho inverso
   (`QueryBuilder.getNonLocalizedCategory`). Confirmado num caso brasileiro: `Contacts`,
   `Emails`, `PDF Documents`. Por isso é seguro casar categoria em inglês.
"""

import logging
import re
from datetime import datetime

from funcoes.achados_texto import (
    _RE_PLACA,
    _formatar_cnpj,
    _formatar_cpf,
    cnpj_valido,
    cpf_valido,
)

logger = logging.getLogger(__name__)


class LinhaParcial(dict):
    """Linha no formato que os `_gravar_*` de investigacao_utils esperam, tolerante a campo
    ausente.

    Os gravadores nasceram do UFDR, onde o parser sempre monta o dict inteiro, e por isso leem
    cada campo por chave obrigatória (`loc["precisao"]`). Quem monta a linha a partir de um
    item do IPED preenche só o que aquele item tem — um local de UFED não tem precisão nem
    elevação, um contato de HD não tem conta —, então a chave ausente virava KeyError e
    derrubava a importação inteira. Aqui o campo que a origem não tem entra como NULL, que é o
    que a coluna significa.

    Vale para o dict montado a partir do IPED/Takeout, não para o do UFDR: o caminho do
    celular continua exigindo as chaves, e um typo lá segue estourando na hora."""

    def __missing__(self, chave):  # noqa: D105 - ver docstring da classe
        return None


# ---------------------------------------------------------------------------
# Leitura crua de campo
# ---------------------------------------------------------------------------

def valores(campos: dict, chave: str) -> list[str]:
    """Todos os valores do campo, sempre como lista (vazia quando ausente ou nulo).

    O dumper emite `null` para campo binário (`thumbnail`, `imageFeatures`): o Lucene guarda
    os bytes e nem `stringValue()` nem `numericValue()` devolvem algo. Esses `None` são
    filtrados aqui para não virarem a string "None" mais adiante."""
    valor = campos.get(chave)
    if valor is None:
        return []
    if isinstance(valor, list):
        return [v for v in valor if v is not None]
    return [valor]


def valor(campos: dict, chave: str) -> str | None:
    """O primeiro valor do campo.

    Primeiro e não último de propósito: para `category` e afins, o que o IPED escreve
    primeiro é a classificação principal do item."""
    achados = valores(campos, chave)
    return achados[0] if achados else None


# Sufixos que o IndexItem acrescenta ao nome do campo conforme o tipo detectado — ver
# armadilha 2 no topo do módulo.
_SUFIXOS_TIPO = ("", ":date", ":number", ":string")


def valor_variantes(campos: dict, *chaves: str) -> str | None:
    """Primeiro valor não vazio entre as chaves dadas, tentando cada uma com e sem os
    sufixos de tipo. Use sempre que ler um campo de METADADO (`pdf:`, `image:`, `ufed:`,
    `Message-*`); para campo básico (`name`, `size`, `path`) `valor()` basta."""
    for chave in chaves:
        for sufixo in _SUFIXOS_TIPO:
            v = valor(campos, chave + sufixo)
            if v is not None and str(v).strip() != "":
                return v
    return None


def juntar(campos: dict, *chaves: str) -> str | None:
    """Junta em uma string os valores de um ou mais campos, sem repetir e preservando a ordem
    — para destinatário de e-mail, que o banco guarda como texto separado por vírgula."""
    achados: list[str] = []
    for chave in chaves:
        for v in valores(campos, chave):
            v = (v or "").strip()
            if v and v not in achados:
                achados.append(v)
    return ", ".join(achados) or None


def booleano(campos: dict, chave: str) -> bool:
    """True só quando o campo existe e vale "true" — campo ausente é False, não None: o
    índice do IPED grava `deleted`/`carved`/`isDir` como string sempre que os grava."""
    return (valor(campos, chave) or "").strip().lower() == "true"


def inteiro(campos: dict, chave: str) -> int | None:
    bruto = valor_variantes(campos, chave)
    if bruto in (None, ""):
        return None
    try:
        return int(float(str(bruto).strip()))
    except (TypeError, ValueError):
        return None


def parse_data(bruto: str | None) -> datetime | None:
    """Data no formato que o IPED grava (`DateUtils.formatDate`, ISO-8601 em UTC com Z).
    Devolve None para vazio ou formato inesperado — item com data corrompida não pode
    derrubar a importação."""
    if not bruto:
        return None
    texto = str(bruto).strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def data_de(campos: dict, *chaves: str) -> datetime | None:
    return parse_data(valor_variantes(campos, *chaves))


# ---------------------------------------------------------------------------
# Linha do tempo do item (timeStamp × timeEvent)
#
# `created`/`accessed`/`changed` do índice vêm STRING VAZIA na maioria dos casos — medido no
# caso iCloud do repo: vazios nos 1.088 itens, enquanto o par `timeStamp`/`timeEvent` trazia
# 920 `modified`, 252 `common:dcterms:created`, 240 `pdf:docinfo:created`, 22 `created`,
# 22 `accessed`, 8 `communication:date` e 4 `image:exif:datetimeoriginal`.
#
# Os dois campos são multivalorados e ALINHADOS POSICIONALMENTE (é por isso que o
# IpedDumper preserva a ordem de aparição dos valores). O nome do evento vem em minúsculas,
# porque o IndexItem normaliza a chave antes de gravar.
# ---------------------------------------------------------------------------

def linha_do_tempo(campos: dict) -> dict[str, str]:
    """{"modified": "2024-08-01T20:39:26Z", ...} com todos os eventos datados do item.

    Quando o mesmo evento aparece mais de uma vez fica a primeira ocorrência. Devolve {}
    quando o caso não tem os campos (versão antiga do IPED, item sem data)."""
    carimbos = valores(campos, "timeStamp")
    eventos = valores(campos, "timeEvent")
    if not carimbos or not eventos:
        return {}
    achados: dict[str, str] = {}
    for evento, carimbo in zip(eventos, carimbos):
        chave = (evento or "").strip().lower()
        if chave and carimbo and chave not in achados:
            achados[chave] = carimbo
    return achados


# Eventos que descrevem a CRIAÇÃO do item, em ordem de preferência. O sistema de arquivos
# (`created`) ganha do metadado do documento, que ganha do EXIF da foto — mas numa extração
# de nuvem só existe o metadado, e é ele que responde "quando isso foi feito".
_EVENTOS_CRIACAO = (
    "created", "common:dcterms:created", "pdf:docinfo:created", "pdf:xmp:createdate",
    "image:exif:datetimeoriginal", "image:exif subifd:date/time original",
    "image:exif ifd0:date/time", "office:creation-date", "downloaddate",
    # UFED (UfedXmlReader): o parser grava o início do chat/conta no par timeStamp×timeEvent,
    # e esses nomes de evento não têm correspondente nas famílias acima.
    "ufed:starttime", "ufed:timecreated", "ufed:startdate", "ufed:timestamp",
)
_EVENTOS_MODIFICACAO = (
    "modified", "common:dcterms:modified", "pdf:docinfo:modified", "pdf:xmp:modifydate",
    "ufed:lastactivity",
)
_EVENTOS_ACESSO = ("accessed", "ipedlastaccesseddate", "visitdate")


def _primeiro_evento(tempos: dict[str, str], candidatos) -> datetime | None:
    for evento in candidatos:
        data = parse_data(tempos.get(evento))
        if data is not None:
            return data
    return None


def datas_do_item(campos: dict) -> dict:
    """Datas normalizadas do item: {"criacao", "acesso", "modificacao", "tempos"}.

    `tempos` é a linha do tempo inteira (dict evento→ISO), guardada em
    ARQ_TB01_ARQUIVOS.TX_LINHA_TEMPO para a tela de linha do tempo poder mostrar de onde
    cada data veio. Os três campos escalares são o que as colunas datadas já existentes
    consomem."""
    tempos = linha_do_tempo(campos)
    modificacao = data_de(campos, "modified") or _primeiro_evento(tempos, _EVENTOS_MODIFICACAO)
    criacao = data_de(campos, "created") or _primeiro_evento(tempos, _EVENTOS_CRIACAO)
    acesso = data_de(campos, "accessed") or _primeiro_evento(tempos, _EVENTOS_ACESSO)
    return {"criacao": criacao, "acesso": acesso, "modificacao": modificacao, "tempos": tempos}


# ---------------------------------------------------------------------------
# Coordenada
#
# `ExtraProperties.LOCATIONS` = "common:geo:locations", gravado pelo IndexItem como a string
# "lat;lon" (além do LatLonPoint, que não é stored). É o campo que uma mensagem de
# localização do WhatsApp e um item `x-ufed-location` preenchem. Foto com GPS no EXIF cai em
# `image:geo:lat`/`image:geo:long` (Tika) — as duas formas são aceitas aqui.
# ---------------------------------------------------------------------------

def _num(bruto) -> float | None:
    try:
        v = float(str(bruto).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return v


def coordenada(campos: dict) -> tuple[float, float] | None:
    """(latitude, longitude) do item, ou None. Descarta o par fora de faixa e o (0, 0), que
    no mundo real é sempre coordenada não preenchida e não o golfo da Guiné."""
    bruto = valor_variantes(campos, "common:geo:locations")
    lat = lon = None
    if bruto and ";" in str(bruto):
        partes = str(bruto).split(";", 1)
        lat, lon = _num(partes[0]), _num(partes[1])
    if lat is None or lon is None:
        lat = _num(valor_variantes(campos, "image:geo:lat", "geo:lat", "ufed:Latitude", "latitude"))
        lon = _num(valor_variantes(campos, "image:geo:long", "geo:long", "ufed:Longitude", "longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None
    return (lat, lon)


# ---------------------------------------------------------------------------
# Identificadores que o IPED já extraiu (campos "Regex:<TIPO>")
#
# Os campos vêm das regras do RegexConfig.txt DO PRÓPRIO CASO, e esse arquivo muda: o caso
# iCloud do repo tem 34 tipos, um caso mais antigo tinha 18, e uma operação pode acrescentar
# regra própria. Por isso aqui NÃO existe lista fixa de campos aceitos — varre-se todo campo
# que comece com "Regex:", com normalizador dedicado quando há um e um passthrough
# conservador quando não há. Assim, tipo novo entra sozinho.
#
# A grafia dos tipos de cripto MUDOU entre versões do IPED (CRIPTOCOIN_* → CRYPTOCOIN_*,
# CRIPTO_POSSIBLE_* → CRYPTO_POSSIBLE_*). `_canonico` uniformiza antes de olhar o mapa — sem
# isso o mesmo dado entra com duas chaves diferentes conforme a versão que processou o caso.
#
# O valor cru do IPED é ruidoso e passá-lo direto para o banco seria pior do que não ter a
# coluna. Medido em caso real: "Regex:BR_CAR_PLATE" traz 1.462 ocorrências de "iso-8859" (a
# declaração de charset de um cabeçalho de e-mail) e "gmt-0300", que têm exatamente a forma
# de placa antiga; "Regex:PHONE" traz centenas de carimbos epoch de cabeçalho. Daí os
# normalizadores — que são os do próprio projeto (achados_texto), para o CPF que entra aqui
# ser o mesmo CPF que a tela de achados considera válido.
# ---------------------------------------------------------------------------

# Tokens técnicos com forma de placa antiga que aparecem em cabeçalho de e-mail. Comparados
# pelo prefixo de 3 letras porque a variação está nos números (ISO-8859-1/-15, GMT-0300).
_PREFIXOS_FALSA_PLACA = ("ISO", "GMT", "UTC", "UTF", "SHA", "MD5", "RFC", "IEC")


def _norm_cpf(bruto: str) -> str | None:
    digitos = re.sub(r"\D", "", bruto)
    return _formatar_cpf(digitos) if cpf_valido(digitos) else None


def _norm_cnpj(bruto: str) -> str | None:
    digitos = re.sub(r"\D", "", bruto)
    return _formatar_cnpj(digitos) if cnpj_valido(digitos) else None


def _norm_placa(bruto: str) -> str | None:
    """Placa em forma canônica sem hífen (ABC1D23). Descarta o token técnico com forma de
    placa — é o único filtro possível, porque "ISO-8859" e "ABC-1234" são a mesma coisa para
    qualquer regex de placa."""
    texto = (bruto or "").strip().upper()
    if texto[:3] in _PREFIXOS_FALSA_PLACA:
        return None
    m = _RE_PLACA.fullmatch(texto)
    return "".join(m.groups()) if m else None


def _norm_telefone(bruto: str) -> str | None:
    """Telefone brasileiro. Exige celular de 11 dígitos (com o 9 na frente) OU que o valor
    original venha formatado — "(11) 3777-0657", "+55 11 3777-0657".

    O corte pela formatação é o que separa um fixo de um carimbo epoch: os dois têm 10
    dígitos e DDD na faixa válida, e no corpo de um e-mail a corrida de 10 dígitos crua é
    quase sempre timestamp ou número de pedido. Perde-se o fixo digitado sem pontuação; em
    troca, a coluna não vira uma lista de epochs."""
    formatado = any(c in bruto for c in "()+-")
    digitos = re.sub(r"\D", "", bruto)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return None
    ddd, numero = digitos[:2], digitos[2:]
    if not (11 <= int(ddd) <= 99):
        return None
    # só descarta o número de um dígito só (0000-0000). O piso de DOIS dígitos distintos que
    # ipl_utils usa é agressivo demais aqui: derruba "3333-4444", um fixo perfeitamente comum.
    if len(set(numero)) == 1:
        return None
    if len(numero) == 9:
        if not numero.startswith("9"):
            return None
    elif not formatado:
        return None
    return f"({ddd}) {numero[:-4]}-{numero[-4:]}"


def _norm_email(bruto: str) -> str | None:
    texto = (bruto or "").strip().strip(".,;:<>").lower()
    if texto.count("@") != 1:
        return None
    _, _, dominio = texto.partition("@")
    return texto[:320] if ("." in dominio and len(dominio) > 3) else None


def _norm_pis(bruto: str) -> str | None:
    digitos = re.sub(r"\D", "", bruto)
    return digitos if (len(digitos) == 11 and len(set(digitos)) > 1) else None


def _norm_cnh(bruto: str) -> str | None:
    """CNH: 11 dígitos. Sem dígito verificador conferido de propósito — a regra oficial do
    DENATRAN reprova numeração antiga que ainda circula, e aqui o custo de um falso positivo
    (uma linha a mais no JSONB) é muito menor que o de perder a habilitação do investigado."""
    digitos = re.sub(r"\D", "", bruto)
    return digitos if (len(digitos) == 11 and len(set(digitos)) > 1) else None


def _norm_titulo_eleitor(bruto: str) -> str | None:
    digitos = re.sub(r"\D", "", bruto)
    return digitos if (12 <= len(digitos) <= 13 and len(set(digitos)) > 1) else None


def _norm_luhn(bruto: str) -> str | None:
    """Número de cartão com dígito de Luhn conferido — sem isso qualquer sequência de 16
    dígitos (número de pedido, código de rastreio) entraria como cartão."""
    d = re.sub(r"\D", "", bruto)
    if not (13 <= len(d) <= 19):
        return None
    soma, dobra = 0, False
    for c in reversed(d):
        n = int(c)
        if dobra:
            n *= 2
            if n > 9:
                n -= 9
        soma += n
        dobra = not dobra
    return d if soma % 10 == 0 else None


def _norm_digitos(minimo: int, maximo: int):
    def _norm(bruto: str) -> str | None:
        digitos = re.sub(r"\D", "", bruto)
        return digitos if (minimo <= len(digitos) <= maximo and len(set(digitos)) > 1) else None
    return _norm


def _norm_mac(bruto: str) -> str | None:
    texto = (bruto or "").strip().upper().replace("-", ":")
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", texto):
        return None
    return None if texto in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF") else texto


def _norm_cripto(bruto: str) -> str | None:
    """Endereço/chave de cripto: preserva a caixa (Base58 e bech32 são sensíveis a ela) e só
    barra o que claramente não é endereço."""
    texto = (bruto or "").strip()
    return texto if 20 <= len(texto) <= 130 else None


def _norm_seed(bruto: str) -> str | None:
    """Seed phrase BIP-39: só aceita a contagem de palavras que o padrão define. Sem esse
    corte, qualquer frase de 12 caracteres do corpo de um e-mail entraria como semente de
    carteira — e essa é uma coluna que ninguém pode duvidar."""
    palavras = (bruto or "").split()
    return " ".join(palavras).lower()[:400] if len(palavras) in (12, 15, 18, 21, 24) else None


def _norm_simples(bruto: str) -> str | None:
    texto = (bruto or "").strip()
    return texto[:200] or None


# Tipo canônico do RegexConfig -> (chave gravada em ARQ_TB01.TX_IDENTIFICADORES, normalizador).
# Tipo não listado aqui e não ignorado abaixo entra com _norm_simples e a chave em minúsculas —
# é o que faz uma regra customizada da operação aparecer sem mexer neste arquivo.
_NORMALIZADORES: dict[str, tuple[str, object]] = {
    "BR_CPF": ("cpf", _norm_cpf),
    "BR_CNPJ": ("cnpj", _norm_cnpj),
    "BR_CAR_PLATE": ("placa", _norm_placa),
    "BR_PISPASEP": ("pis", _norm_pis),
    "BR_CNH": ("cnh", _norm_cnh),
    "BR_TITULO_ELEITOR": ("titulo_eleitor", _norm_titulo_eleitor),
    "BR_BOLETO": ("boleto", _norm_digitos(44, 48)),
    "BR_CHAVE_DANFE": ("chave_danfe", _norm_digitos(44, 44)),
    "BR_BANK_ACCOUNT": ("conta_bancaria", _norm_simples),
    "PHONE": ("telefone", _norm_telefone),
    "EMAIL": ("email", _norm_email),
    "CREDIT_CARD": ("cartao", _norm_luhn),
    "MAC_ADDRESS": ("mac", _norm_mac),
    "SWIFT": ("swift", _norm_simples),
    "CRYPTO_POSSIBLE_SEED_PHRASE_EN": ("seed_phrase", _norm_seed),
    "CRYPTO_POSSIBLE_SEED_PHRASE_PT": ("seed_phrase", _norm_seed),
}

# Endereço e chave privada de cripto: todos caem em duas chaves só (`cripto` e
# `cripto_chave_privada`), porque para a investigação o que importa é "há carteira aqui" e o
# valor em si — não vale uma coluna por moeda. O sufixo da moeda fica registrado no valor.
_CRIPTO_CHAVE_PRIVADA = ("PRIV_K", "XPRV")


def _canonico(sufixo: str) -> str:
    """Uniformiza a grafia do tipo entre versões do IPED. A troca CRIPTO→CRYPTO aconteceu de
    fato: o caso do repo (IPED 4.2.2) grava `Regex:CRIPTOCOIN_BITCOIN_ADDRESS` e o IPED atual
    grava `Regex:CRYPTOCOIN_BITCOIN_ADDRESS`."""
    return sufixo.strip().upper().replace("CRIPTO", "CRYPTO")


# Tipos ignorados de propósito. URL e IP, num item de e-mail, rendem milhares de valores de
# rastreador e de cabeçalho de trânsito, que enchem o JSONB sem dizer nada sobre quem usou a
# conta; MONEY não é identificador (não se procura por um valor); o resto não é regra.
_TIPOS_IGNORADOS = {"URL", "IP", "MONEY", "FORMATREGEXMATCHES"}

# Teto por tipo dentro de um item. Uma newsletter rende centenas de e-mails de remetente
# técnico; passar disso não acrescenta e só engorda o índice GIN.
MAX_POR_TIPO = 50


def _destino_regex(tipo: str):
    """(chave, normalizador) para um tipo canônico, ou None se o tipo é para ignorar."""
    if tipo in _TIPOS_IGNORADOS:
        return None
    conhecido = _NORMALIZADORES.get(tipo)
    if conhecido is not None:
        return conhecido
    if tipo.startswith("CRYPTOCOIN_"):
        privada = any(marca in tipo for marca in _CRIPTO_CHAVE_PRIVADA)
        return ("cripto_chave_privada" if privada else "cripto", _norm_cripto)
    if tipo.startswith("CRYPTO_POSSIBLE_SEED_PHRASE"):
        return ("seed_phrase", _norm_seed)
    # regra customizada do caso: entra com a chave em minúsculas e validação conservadora
    return (tipo.lower(), _norm_simples)


def identificadores_do_item(campos: dict) -> dict | None:
    """{"cpf": [...], "placa": [...], "cripto": [...]} com o que o IPED extraiu do item, já
    normalizado e validado, ou None se não sobrar nada.

    Deduplica preservando a ordem de aparição — a primeira ocorrência costuma ser a do corpo
    do documento e as seguintes, repetição de rodapé. Junta tipos diferentes que vão para a
    mesma chave (as várias moedas em `cripto`, EN e PT em `seed_phrase`)."""
    achados: dict[str, list[str]] = {}
    for campo in campos:
        if not campo.startswith("Regex:"):
            continue
        # `Regex:X:number`/`:date` não existe hoje (o IPED declara todo Regex como String em
        # metadataTypes.txt), mas cortar o sufixo é barato e evita uma chave duplicada se
        # mudar.
        tipo = _canonico(campo[len("Regex:"):].split(":")[0])
        destino = _destino_regex(tipo)
        if destino is None:
            continue
        chave, normalizar = destino
        vistos = achados.setdefault(chave, [])
        for bruto in valores(campos, campo):
            if len(vistos) >= MAX_POR_TIPO:
                break
            try:
                v = normalizar(bruto or "")
            except Exception:  # normalizador novo com entrada inesperada não derruba o item
                v = None
            if v and v not in vistos:
                vistos.append(v)
    return {k: v for k, v in achados.items() if v} or None


# ---------------------------------------------------------------------------
# QR Code
#
# O IPED decodifica QR code durante o processamento e grava o conteúdo em `QRCodeText`. Num
# caso real do repo são chaves de NF-e; num celular, é onde apareceria um BR Code de PIX —
# que é exatamente o que a tela de Chaves PIX procura.
# ---------------------------------------------------------------------------

def qrcodes(campos: dict) -> list[str]:
    """Textos de QR code lidos pelo IPED no item, sem repetição."""
    achados: list[str] = []
    for bruto in valores(campos, "QRCodeText"):
        texto = (bruto or "").strip()
        if texto and texto not in achados:
            achados.append(texto[:2000])
    return achados[:MAX_POR_TIPO]


# ---------------------------------------------------------------------------
# EXIF / metadado de mídia, direto do índice
#
# O IPED já rodou o Tika em todo item e gravou o resultado com prefixo por família
# (`image:`, `video:`, `audio:`, `pdf:`, `office:`, `common:`). Ler daqui evita reabrir o
# binário só para redescobrir o que já está no índice.
# ---------------------------------------------------------------------------

# rótulo exibido -> nomes candidatos no índice (o Tika grava o mesmo dado com nomes
# diferentes conforme o decodificador que atendeu a imagem)
_CAMPOS_EXIF = (
    ("largura",       ("image:Width", "image:tiff:ImageWidth", "image:Exif SubIFD:Exif Image Width")),
    ("altura",        ("image:Height", "image:tiff:ImageLength", "image:Exif SubIFD:Exif Image Height")),
    ("fabricante",    ("image:tiff:Make", "image:Exif IFD0:Make")),
    ("modelo",        ("image:tiff:Model", "image:Exif IFD0:Model")),
    ("software",      ("image:tiff:Software", "image:Exif IFD0:Software")),
    ("lente",         ("image:exif:LensModel", "image:Exif SubIFD:Lens Model")),
    ("orientacao",    ("image:tiff:Orientation", "image:Exif IFD0:Orientation")),
    ("fuso",          ("image:Exif SubIFD:Time Zone Original", "image:Exif SubIFD:Time Zone")),
    ("data_captura",  ("image:exif:DateTimeOriginal", "image:Exif SubIFD:Date/Time Original",
                       "image:Exif IFD0:Date/Time")),
    ("duracao",       ("video:duration", "audio:duration", "common:xmpDM:duration")),
)


def metadados_de_midia(campos: dict) -> dict:
    """Metadado de imagem/vídeo que o IPED já extraiu, no mesmo vocabulário que
    `investigacao_utils.extrair_metadados_imagem` devolve. {} quando o item não tem nenhum —
    caso processado sem parser de imagem, item só de metadado, container."""
    meta: dict = {}
    for rotulo, candidatos in _CAMPOS_EXIF:
        v = valor_variantes(campos, *candidatos)
        if v is None:
            continue
        if rotulo in ("largura", "altura"):
            # o Tika grava tanto "2556" quanto "1080 pixels" conforme o decodificador
            m = re.search(r"\d+", str(v))
            if m:
                meta[rotulo] = int(m.group())
            continue
        meta[rotulo] = str(v)[:300]
    coord = coordenada(campos)
    if coord:
        meta["latitude"], meta["longitude"] = coord
    return meta
