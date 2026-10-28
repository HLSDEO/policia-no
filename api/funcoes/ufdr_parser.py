"""Extrai dados de um relatório UFDR (Cellebrite Physical Analyzer): conversas
(WhatsApp/SMS), contatos, ligações, localizações, notificações, redes Wi-Fi.

Formato verificado contra um extrato real: o .ufdr é um ZIP contendo um
report.xml (namespace pa.cellebrite.com/report/2.0) onde cada <model> de
mais alto nível representa um registro (Chat, Contact, Call, Location,
Notification...), muitas vezes com outros <model> aninhados dentro
(participantes, anexos, coordenadas etc.).
"""

import logging
import re
import zipfile
from collections.abc import Callable
from datetime import datetime
from xml.etree import ElementTree as ET

from funcoes import ufdr_zip, xml_limpo  # ufdr_zip: tolera .ufdr com índice final perdido (cópia truncada)

logger = logging.getLogger(__name__)

NS = "{http://pa.cellebrite.com/report/2.0}"

# fontes de Chat conhecidas — usadas para separar WhatsApp de SMS/MMS nativos
FONTE_WHATSAPP = "WhatsApp"
FONTE_SMS = "Native Messages"

# de quantos em quantos eventos do parser a posição no report.xml é medida (ver iterar_registros)
_PASSO_PROGRESSO = 2000


def _valor_campo(model, nome):
    """Lê o texto de <field name="nome"><value>...</value></field> de um <model>; None se ausente/vazio."""
    campo = model.find(f"{NS}field[@name='{nome}']")
    if campo is None:
        return None
    valor = campo.find(f"{NS}value")
    return valor.text if valor is not None else None


def _valor_campo_bool(model, nome):
    """Como _valor_campo, mas converte 'True'/'False' em bool; None se ausente."""
    txt = _valor_campo(model, nome)
    if txt is None:
        return None
    return txt.strip().lower() == "true"


def _timestamp_iso(model, nome):
    """Lê um campo TimeStamp do UFDR (formattedTimestamp em ISO 8601) e devolve datetime; None se ausente/inválido."""
    campo = model.find(f"{NS}field[@name='{nome}']")
    if campo is None:
        return None
    valor = campo.find(f"{NS}value")
    if valor is None:
        return None
    formatado = valor.get("formattedTimestamp") or valor.text
    if not formatado:
        return None
    try:
        return datetime.fromisoformat(formatado)
    except ValueError:
        return None


_RE_DATA_METADADO = re.compile(r"(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})")


def _parse_data_metadado(txt):
    """Converte o formato 'DD/MM/AAAA HH:MI:SS(UTC-3)' usado nos metadados de extração; None se não bater."""
    if not txt:
        return None
    m = _RE_DATA_METADADO.search(txt)
    if not m:
        return None
    dia, mes, ano, hora, minuto, segundo = m.groups()
    try:
        return datetime(int(ano), int(mes), int(dia), int(hora), int(minuto), int(segundo))
    except ValueError:
        return None


def _duracao_para_segundos(txt):
    """Converte uma duração 'HH:MM:SS' (campo Duration do UFDR) em segundos inteiros; None se não bater.

    Alguns conectores (Voicemail, Recording) mandam a duração já em segundos, como número
    solto — aceitamos os dois formatos."""
    if not txt:
        return None
    partes = txt.split(":")
    if len(partes) != 3:
        try:
            return int(float(txt))
        except (ValueError, TypeError):
            return None
    try:
        h, m, s = (int(float(p)) for p in partes)
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


# =====================================================================
# Leitura das estruturas compostas do UFDR.
#
# Um <model> não guarda tudo em <field>: boa parte do conteúdo vive em
# <modelField> (um submodelo), <multiModelField> (vários) e <multiField>
# (vários valores soltos). Ler só <field> foi a origem de defeitos reais —
# o remetente de e-mail, por exemplo, é um <modelField name="From"> com um
# <model type="Party"> dentro, e por isso todo e-mail era gravado sem
# remetente e sem destinatário.
# =====================================================================


def _curto(valor, limite=3000):
    """Corta um texto no tamanho das colunas VARCHAR(3000) do schema.

    Vale para campo que o Cellebrite trata como texto livre mas cabe numa coluna curta —
    conta, pasta, pacote, identificador. Sem isso um valor fora do comum derruba a extração
    inteira no INSERT: aconteceu em produção com o grupo de acesso do chaveiro do iOS, que
    veio com 3.255 caracteres. Onde o conteúdo é livre por natureza (corpo, assunto, URL) a
    coluna é TEXT e não passa por aqui."""
    if isinstance(valor, str) and len(valor) > limite:
        return valor[:limite]
    return valor


def _estado_ufdr(model):
    """deleted_state do <model>: Intact | Deleted | Trash | Unknown.

    Existe em TODO model do UFDR e é o único sinal de exclusão que o iPhone dá — lá o
    Cellebrite não emite os rótulos de mensagem que o Android traz."""
    return model.get("deleted_state")


def _valor_campo_float(model, nome):
    """Como _valor_campo, mas converte para float; None se ausente ou não numérico."""
    txt = _valor_campo(model, nome)
    if txt is None:
        return None
    try:
        return float(str(txt).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def _submodelo(model, nome):
    """O <model> de dentro de um <modelField name="nome">; None se ausente."""
    return model.find(f"{NS}modelField[@name='{nome}']/{NS}model")


def _submodelos(model, nome):
    """Lista dos <model> de dentro de um <multiModelField name="nome">; vazia se ausente."""
    return model.findall(f"{NS}multiModelField[@name='{nome}']/{NS}model")


def _valores_multi(model, nome):
    """Valores não vazios de um <multiField name="nome">."""
    campo = model.find(f"{NS}multiField[@name='{nome}']")
    if campo is None:
        return []
    return [v.text.strip() for v in campo.findall(f"{NS}value") if v is not None and v.text and v.text.strip()]


def _texto_multi(model, nome, limite=2000):
    """Os valores de um <multiField> juntos numa string; None se não houver nenhum."""
    valores = _valores_multi(model, nome)
    return ", ".join(valores)[:limite] or None


def _pares_chave_valor(model, nome):
    """Pares de um <multiModelField> de KeyValueModel (AdditionalInfo, DeviceIdentifiers...).

    Devolve lista de (chave, valor) preservando a ordem — é onde ficam, entre outras coisas,
    a data de nascimento do contato e o MAC do dispositivo reconhecido."""
    pares = []
    for filho in _submodelos(model, nome):
        chave = _valor_campo(filho, "Key")
        valor = _valor_campo(filho, "Value")
        if chave or valor:
            pares.append((chave, valor))
    return pares


def _rotulo_party(party):
    """Rótulo legível de um <model type="Party">: nome, identificador, ou os dois."""
    if party is None:
        return None
    nome = _valor_campo(party, "Name")
    ident = _valor_campo(party, "Identifier")
    if nome and ident and nome != ident:
        return f"{nome} <{ident}>"
    return nome or ident


def _rotulo_parties(model, nome, limite=2000):
    """Rótulos de todos os Party de um multiModelField, juntos; None se não houver."""
    rotulos = [r for r in (_rotulo_party(p) for p in _submodelos(model, nome)) if r]
    return ", ".join(rotulos)[:limite] or None


def ler_info_dispositivo(caminho_ufdr: str = "", *, zf: zipfile.ZipFile | None = None) -> dict:
    """Lê fabricante/modelo/data de extração/telefone do dono dos metadados no início do report.xml, sem carregar o arquivo inteiro.
    Se zf for informado (ZipFile já aberto), reusa-o em vez de abrir o ZIP pelo caminho."""
    info = {"fabricante": None, "modelo": None, "data_extracao": None, "telefone": None}
    campos_procurados = {
        "DeviceInfoSelectedManufacturer": "fabricante",
        "DeviceInfoSelectedDeviceName": "modelo",
        "DeviceInfoExtractionStartDateTime": "data_extracao",
        "LastUsedMSISDN": "telefone",
    }

    def _parse(zf_obj):
        # Mesma lição do _PilhaConteineres adiante: elem.clear() esvazia o elemento mas NÃO o
        # desprega do pai — num iPhone cujo report.xml acumula milhões de <item> antes do
        # primeiro </model>, só as cascas chegaram a ~16 GB medidos (a leitura destes 4 campos
        # era o pico de memória da importação inteira). Limpar a raiz a cada 'end' desprega o
        # que já foi lido; aqui só interessam folhas <item> e nenhum contexto de ancestral.
        raiz = None
        achados: set[str] = set()
        with xml_limpo.abrir(zf_obj) as f:
            for evento, elem in ET.iterparse(f, events=("start", "end")):
                if evento == "start":
                    if raiz is None:
                        raiz = elem
                    continue
                if elem.tag.endswith("}item"):
                    chave = campos_procurados.get(elem.get("name"))
                    if chave:
                        info[chave] = elem.text
                        achados.add(chave)
                elif elem.tag.endswith("}model"):
                    break
                raiz.clear()
                if len(achados) == len(campos_procurados):
                    break  # os 4 campos são do topo; achou todos, não há por que ler o resto

    if zf is not None:
        _parse(zf)
    else:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            _parse(zf)
    info["data_extracao"] = _parse_data_metadado(info["data_extracao"])
    if info["telefone"]:
        info["telefone"] = re.sub(r"[^\d]", "", info["telefone"])
    return info


_EXTENSOES_AUDIO = (".opus", ".m4a", ".aac", ".mp3", ".amr", ".ogg", ".wav")
_EXTENSOES_IMAGEM = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif", ".tiff", ".tif")
_EXTENSOES_VIDEO = (".mp4", ".3gp", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv")
# documentos/arquivos e qualquer outro anexo que não seja mídia tocável no navegador —
# entram no mesmo índice para que a disponibilidade (e o download) valha para eles também
_EXTENSOES_DOCUMENTO = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".csv",
    ".vcf", ".zip", ".rar", ".7z", ".apk", ".json", ".xml", ".html", ".htm",
)
# união usada na ingestão: um único passe pelo zip cobre todos os tipos de anexo
# Miniatura que o Cellebrite gera do vídeo/localização/link. Fica FORA das listas por
# categoria de propósito (não é um tipo que o investigador filtra), mas PRECISA entrar no
# índice por tamanho: sem ela o índice ficava incompleto e, pior, mentia sobre ambiguidade —
# um .png qualquer virava "o único arquivo com 3.412 bytes" quando o .thumb de mesmo tamanho
# simplesmente não era candidato, e o anexo recebia o binário errado (ver o casamento por
# tamanho em _extrair_anexos).
_EXTENSOES_MINIATURA = (".thumb",)

# arquivos compactados — categoria própria no inventário de arquivos do aparelho
_EXTENSOES_COMPACTADO = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2")

# dict.fromkeys em vez de set: preserva a ordem (só para o índice ficar legível em log/depuração)
# e tira as repetições — .zip/.rar/.7z estão em DOCUMENTO e em COMPACTADO. Os compactados
# entram porque .tar/.gz/.tgz/.bz2 ficavam de fora do índice por tamanho: um anexo desses não
# tinha como ser localizado, e pior, um arquivo de mesmo tamanho não contava como colisão.
_EXTENSOES_ANEXO = tuple(dict.fromkeys(
    _EXTENSOES_AUDIO + _EXTENSOES_IMAGEM + _EXTENSOES_VIDEO
    + _EXTENSOES_DOCUMENTO + _EXTENSOES_MINIATURA + _EXTENSOES_COMPACTADO))

# MIME por extensão — usado para servir o binário e para o front-end decidir se abre
# em modal (imagem/vídeo/áudio) ou apenas oferece download. Só o essencial; o resto
# cai em application/octet-stream (o navegador baixa).
_MIME_POR_EXTENSAO = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".heic": "image/heic", ".heif": "image/heif",
    ".tiff": "image/tiff", ".tif": "image/tiff",
    ".mp4": "video/mp4", ".3gp": "video/3gpp", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".m4v": "video/x-m4v",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
    ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".mp3": "audio/mpeg",
    ".amr": "audio/amr", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip", ".rar": "application/vnd.rar", ".7z": "application/x-7z-compressed",
    ".vcf": "text/vcard", ".json": "application/json", ".xml": "application/xml",
    ".html": "text/html", ".htm": "text/html",
}


def _extensao(nome: str) -> str:
    """Extensão em minúsculas (com o ponto) do nome/caminho informado; '' se não houver."""
    if not nome:
        return ""
    base = nome.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    ponto = base.rfind(".")
    return base[ponto:].lower() if ponto > 0 else ""


# JSONs de metadado de sincronização — não são conteúdo do usuário, e sim a contabilidade
# que Google Drive/Takeout grava ao lado de cada arquivo. Num caso de nuvem real medido,
# ~44 mil dos 50 mil "Documentos" eram isto (sidecars do Google Photos "<foto>.jpg.json",
# metadado do Drive "<arquivo>_file_metadata.json" e o MessageInformation do Gmail, um JSON
# por e-mail na pasta própria), afogando o que interessa na tela Arquivos. Viram a categoria
# "Metadado", que a listagem oculta por padrão (o filtro mostra quando chamado).
_RE_METADADO_SIDE_CAR = re.compile(
    r"\.(?:jpe?g|png|gif|webp|heic|heif|tiff|mp4|mov|m4v|3gp|avi|mkv)\.json$", re.I)


def eh_metadado(nome: str | None, caminho: str | None = None) -> bool:
    """True quando o arquivo é metadado de sincronização do Google (sidecar de foto/vídeo,
    ``_file_metadata.json`` do Drive ou o ``MessageInformation`` do Gmail — este só tem nome
    numérico, só reconhecível pela pasta no caminho)."""
    base = (nome or "").strip().lower()
    if not base.endswith(".json"):
        return False
    if base.endswith("_file_metadata.json"):
        return True
    if _RE_METADADO_SIDE_CAR.search(base):
        return True
    return bool(caminho) and "messageinformation" in caminho.lower().replace("\\", "/")


def categoria_de_extensao(nome: str, caminho: str | None = None) -> str:
    """Categoria normalizada em PT do arquivo, pela extensão: Imagem/Vídeo/Áudio/Documento/Compactado/Outro.

    Deriva da extensão (e não do campo Tags do UFDR) porque Tags vem inconsistente — no
    iPhone quase todo <file> vem sem Tags —, e a tela filtra por essas categorias.

    ``caminho``, quando informado, permite reconhecer o MessageInformation do Gmail (JSON de
    nome só numérico): esses metadados de sincronização viram a categoria "Metadado", que a
    tela de arquivos oculta por padrão (ver ``eh_metadado``)."""
    if eh_metadado(nome, caminho):
        return "Metadado"
    ext = _extensao(nome)
    if ext in _EXTENSOES_IMAGEM:
        return "Imagem"
    if ext in _EXTENSOES_VIDEO:
        return "Vídeo"
    if ext in _EXTENSOES_AUDIO:
        return "Áudio"
    if ext in _EXTENSOES_DOCUMENTO and ext not in _EXTENSOES_COMPACTADO:
        return "Documento"
    if ext in _EXTENSOES_COMPACTADO:
        return "Compactado"
    return "Outro"


def mime_de_extensao(nome: str) -> str:
    """MIME do arquivo pela extensão (ver _MIME_POR_EXTENSAO); application/octet-stream se desconhecida."""
    return _MIME_POR_EXTENSAO.get(_extensao(nome), "application/octet-stream")


def _indexar_por_extensao(
    caminho_ufdr: str = "", extensoes: tuple[str, ...] = (),
    *, zf: zipfile.ZipFile | None = None,
) -> dict[int, list[str]]:
    """Mapeia tamanho em bytes -> caminhos dentro do zip, para os arquivos soltos no UFDR
    (pasta files/Audio/, files/Image/ etc., com todo o binário daquele tipo do aparelho —
    não só WhatsApp) cuja extensão bate com uma das informadas.

    O relatório XML só guarda o tamanho do anexo (dataField Data, length), sem apontar pra qual
    arquivo do zip é; casar pelo tamanho exato é o jeito de encontrar o binário de verdade. Só é
    usado quando o tamanho é único entre os candidatos — colisão vira "não encontrado" em vez de
    arriscar servir o binário errado.
    Se zf for informado, reusa o ZipFile em vez de abrir o ZIP pelo caminho.
    """
    indice: dict[int, list[str]] = {}

    def _build(zf_obj):
        for info in zf_obj.infolist():
            if info.is_dir():
                continue
            if not info.filename.lower().endswith(extensoes):
                continue
            indice.setdefault(info.file_size, []).append(info.filename)

    if zf is not None:
        _build(zf)
    else:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            _build(zf)
    return indice


def indexar_audios(caminho_ufdr: str) -> dict[int, list[str]]:
    """Índice tamanho->caminho (ver _indexar_por_extensao) para os arquivos de áudio soltos no UFDR."""
    return _indexar_por_extensao(caminho_ufdr, _EXTENSOES_AUDIO)


def indexar_imagens(caminho_ufdr: str) -> dict[int, list[str]]:
    """Índice tamanho->caminho (ver _indexar_por_extensao) para as imagens soltas no UFDR."""
    return _indexar_por_extensao(caminho_ufdr, _EXTENSOES_IMAGEM)


def indexar_anexos(caminho_ufdr: str = "", *, zf: zipfile.ZipFile | None = None) -> dict[int, list[str]]:
    """Índice tamanho->caminho para TODOS os tipos de anexo (áudio, imagem, vídeo, documentos),
    num único passe pelo zip. Casar por tamanho é o que permite localizar o binário real de
    vídeo/documento/outros — não só de áudio/imagem (ver _indexar_por_extensao).
    Se zf for informado, reusa o ZipFile em vez de abrir o ZIP pelo caminho."""
    return _indexar_por_extensao(caminho_ufdr, _EXTENSOES_ANEXO, zf=zf)


# foto de perfil do WhatsApp: o arquivo em files/Image traz o telefone/jid no próprio nome —
#   Android:  5527999516013@s.whatsapp.net.j
#   iPhone:   5527995083860-1706577586.thumb  (telefone antes do '-', depois o timestamp)
# então dá pra ligar a foto ao contato só pela listagem do zip, sem tocar no report.xml.
_RE_FOTO_PERFIL = re.compile(r"^(\d{10,15})(?:@|-\d)")
# preferimos a imagem cheia (.jpg) à miniatura (.thumb) quando as duas existem para o mesmo número
_PRIORIDADE_FOTO = {".jpg": 0, ".jpeg": 0, ".png": 1, ".thumb": 2}


def indexar_indicadores(caminho_ufdr: str = "", *, zf: zipfile.ZipFile | None = None,
                        ao_progredir: Callable[[int, int], None] | None = None
                        ) -> tuple[dict[int, list[str]], dict[str, str], set[str], dict[int, int]]:
    """Passe único pelo ZipFile para construir os quatro índices:
    (indice_binarios, mapa_fotos, caminhos_zip, contagem_tamanhos).
    indice_binarios: tamanho bytes -> caminhos (anexos áudio/imagem/vídeo/documento).
    mapa_fotos: telefone -> caminho da foto de perfil WhatsApp.
    caminhos_zip: todos os nomes de arquivo do zip, para validar o attachment_extracted_path que
    o Cellebrite grava em cada anexo (ver _extrair_anexos).
    contagem_tamanhos: tamanho bytes -> quantos arquivos do zip têm EXATAMENTE esse tamanho,
    contando TODOS eles e não só os de extensão conhecida. É o que torna honesta a pergunta
    "esse tamanho é inequívoco?" em _extrair_anexos: o índice de anexos só enxerga as extensões
    de _EXTENSOES_ANEXO, então um arquivo sem extensão (ou com uma que não está na lista) de
    mesmo tamanho não contava como colisão e o anexo recebia o binário errado com ar de certeza.
    Foi o mesmo defeito que o .thumb já tinha revelado — ali resolvido caso a caso, aqui de vez.
    Se zf for informado, reusa o ZipFile em vez de abrir o ZIP pelo caminho.
    ao_progredir: callback opcional (feitos, total) chamado periodicamente durante a varredura —
    em .ufdr com centenas de milhares de arquivos ela sozinha leva minutos, e sem feedback a tela
    fica parada em 0% antes de o report.xml começar a ser lido."""
    indice_binarios: dict[int, list[str]] = {}
    melhor_foto: dict[str, tuple[int, str]] = {}
    caminhos_zip: set[str] = set()
    contagem_tamanhos: dict[int, int] = {}

    def _build(zf_obj):
        infos = zf_obj.infolist()
        total = sum(1 for i in infos if not i.is_dir())
        feitos = 0
        for info in infos:
            if info.is_dir():
                continue
            feitos += 1
            fn = info.filename
            fn_lower = fn.lower()
            caminhos_zip.add(fn)
            contagem_tamanhos[info.file_size] = contagem_tamanhos.get(info.file_size, 0) + 1
            # índice de anexos (por extensão)
            if fn_lower.endswith(_EXTENSOES_ANEXO):
                indice_binarios.setdefault(info.file_size, []).append(fn)
            # fotos de perfil WhatsApp
            if "/Image/" in fn or fn.startswith("files/Image/"):
                base = fn.rsplit("/", 1)[-1]
                m = _RE_FOTO_PERFIL.match(base)
                if m:
                    telefone = m.group(1)
                    ext = base[base.rfind("."):].lower() if "." in base else ""
                    prio = _PRIORIDADE_FOTO.get(ext, 3)
                    atual = melhor_foto.get(telefone)
                    if atual is None or prio < atual[0]:
                        melhor_foto[telefone] = (prio, fn)
            if ao_progredir and feitos % 5000 == 0:
                ao_progredir(feitos, total)
        if ao_progredir:
            ao_progredir(total, total)

    if zf is not None:
        _build(zf)
    else:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            _build(zf)
    mapa_fotos = {tel: caminho for tel, (_prio, caminho) in melhor_foto.items()}
    return indice_binarios, mapa_fotos, caminhos_zip, contagem_tamanhos


def indexar_fotos_perfil(caminho_ufdr: str = "", *, zf: zipfile.ZipFile | None = None) -> dict[str, str]:
    """Mapeia telefone (só dígitos) -> caminho da foto de perfil dentro do zip, lendo apenas os
    nomes dos arquivos de files/Image (ver _RE_FOTO_PERFIL). Não parseia o report.xml. Quando há
    mais de um arquivo para o mesmo número, escolhe a melhor (imagem cheia antes da miniatura).
    Se zf for informado, reusa o ZipFile em vez de abrir o ZIP pelo caminho."""
    melhor: dict[str, tuple[int, str]] = {}

    def _build(zf_obj):
        for nome in zf_obj.namelist():
            if "/Image/" not in nome and not nome.startswith("files/Image/"):
                continue
            base = nome.rsplit("/", 1)[-1]
            m = _RE_FOTO_PERFIL.match(base)
            if not m:
                continue
            telefone = m.group(1)
            ext = base[base.rfind("."):].lower() if "." in base else ""
            prio = _PRIORIDADE_FOTO.get(ext, 3)
            atual = melhor.get(telefone)
            if atual is None or prio < atual[0]:
                melhor[telefone] = (prio, nome)

    if zf is not None:
        _build(zf)
    else:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            _build(zf)
    return {tel: caminho for tel, (_prio, caminho) in melhor.items()}


class _PilhaConteineres:
    """Rastreia os elementos de estrutura abertos ACIMA do registro corrente, para que o registro
    já processado possa sair da árvore de verdade.

    `elem.clear()` esvazia o registro, mas NÃO o tira da lista de filhos do pai — e o iterparse
    do ElementTree monta a árvore inteira enquanto lê. Num iPhone com 1,7 milhão de <file> e
    milhões de <model>, sobra uma casca de Element por registro pendurada no pai até o fim do
    passe: centenas de MB retidos por extração, e cada extração simultânea paga a sua. Medido
    num XML sintético de 400 mil registros: 31 MiB só de cascas contra 0,4 MiB limpando o pai.

    O ElementTree não dá o pai de um elemento (não existe getparent(), como no lxml), então a
    pilha é mantida na mão pelos eventos start/end — e só dos elementos de FORA dos registros,
    que são meia dúzia (<project>, <decodedData>, <taggedFiles>…). Dentro de um registro nada é
    empilhado: quem chama só alimenta a pilha quando está na profundidade de topo, então o custo
    por evento é uma comparação de inteiro."""

    __slots__ = ("_abertos",)

    def __init__(self) -> None:
        self._abertos: list = []

    def evento(self, evento: str, elem) -> None:
        """Empilha no 'start', desempilha no 'end'. Chamar só fora dos registros de topo."""
        if evento == "start":
            self._abertos.append(elem)
        elif self._abertos:
            self._abertos.pop()

    def descartar_processados(self) -> None:
        """Tira da árvore os registros já lidos, limpando o elemento que os acumula.

        No 'end' de um registro de topo, o topo da pilha é justamente o pai dele — os filhos do
        registro nunca foram empilhados. Limpar o pai descarta o registro corrente (já consumido
        por quem chama, porque isto roda depois do yield) e os irmãos anteriores, que também já
        foram. O pai continua recebendo os próximos filhos normalmente."""
        if self._abertos:
            self._abertos[-1].clear()


def _iterar_modelos_topo(caminho_ufdr, tipo_alvo, tipos_aninhados):
    """Percorre o report.xml e produz cada <model> de nível superior cujo type == tipo_alvo.

    "Nível superior" é levado a sério: a profundidade de aninhamento de <model> é
    contada, e só um <model> na raiz (profundidade 1) é considerado — um mesmo type
    pode reaparecer aninhado dentro de outro registro (ex. um User dentro de
    InstalledApplication, ou um Contact "relacionado" dentro de outro Contact) e não
    deve ser contado como registro próprio.

    Usa iterparse com limpeza seletiva de memória: elementos cujo type está em
    tipos_aninhados (filhos esperados dentro do tipo_alvo, ex. Party dentro de
    Chat) nunca são limpos individualmente — só quando o <model> de nível superior
    que os contém é limpo, senão a leitura do pai voltaria vazia. Cada <model> de
    nível superior (os ~27 mil ao todo) é limpo assim que termina, liberando junto
    toda a sua subárvore, e o pai dele é limpo junto (ver _PilhaConteineres).
    """
    with ufdr_zip.abrir(caminho_ufdr) as zf:
        with xml_limpo.abrir(zf) as f:
            profundidade = 0
            pilha = _PilhaConteineres()
            for evento, elem in ET.iterparse(f, events=("start", "end")):
                if not elem.tag.endswith("}model"):
                    if profundidade == 0:
                        pilha.evento(evento, elem)
                    continue
                if evento == "start":
                    profundidade += 1
                    continue
                # evento == "end"
                profundidade -= 1
                if elem.get("type") in tipos_aninhados:
                    continue
                if profundidade == 0:
                    if elem.get("type") == tipo_alvo:
                        yield elem
                    elem.clear()
                    pilha.descartar_processados()


# =====================================================================
# Conversas (WhatsApp e SMS/MMS) — Chat + Participants (Party) + Messages (InstantMessage)
# =====================================================================

_ANINHADOS_CHAT = {"Party", "InstantMessage", "Attachment"}


def _extrair_participante(model_party):
    """Extrai (jid, nome, dono_dispositivo, admin_grupo, papel e recibos) de um <model type="Party">.

    Os recibos de entrega/leitura/reprodução ficam AQUI, no Party de From/To, e não na
    mensagem — são 816 mil datas nos oito extratos auditados, e é o que prova que a
    mensagem chegou e foi vista."""
    return {
        "jid": _valor_campo(model_party, "Identifier"),
        "nome": _valor_campo(model_party, "Name"),
        "dono_dispositivo": _valor_campo_bool(model_party, "IsPhoneOwner"),
        "admin_grupo": _valor_campo_bool(model_party, "IsGroupAdmin"),
        "papel": _valor_campo(model_party, "Role"),
        "entregue": _timestamp_iso(model_party, "DateDelivered"),
        "lida": _timestamp_iso(model_party, "DateRead"),
        "reproduzida": _timestamp_iso(model_party, "DatePlayed"),
    }


def _normalizar_coordenada(valor, limite):
    """Coordenada em graus, normalizada quando o Cellebrite a gravou em escala E.

    Alguns conectores de nuvem trazem a coordenada sem o ponto decimal — o SearchedItem do
    Google vem em E6 ("-20089469" em vez de "-20.089469") e o Histórico de localização em E7 —
    e o float cru estoura a coluna NUMERIC(10,6) do banco. Valor fora da faixa geográfica
    (lat > 90, lon > 180) é escalado: divide-se por 10 até voltar para dentro da faixa, o que
    normaliza E5/E6/E7 sem supor a escala. Dentro da faixa, segue como veio."""
    if valor is None:
        return None
    try:
        f = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    while abs(f) > limite:
        f /= 10.0
    return f


def _extrair_posicao(model, nome="Position"):
    """Extrai (latitude, longitude) de um modelField Coordinate, se presente.

    O nome do campo varia por domínio: Position na maioria, EventPosition no calendário,
    FromPoint/ToPoint no trajeto (esses últimos são Location, não Coordinate — ver
    _ponto_de_trajeto)."""
    posicao = model.find(f"{NS}modelField[@name='{nome}']/{NS}model")
    if posicao is None:
        return None, None
    lat_txt = _valor_campo(posicao, "Latitude")
    lon_txt = _valor_campo(posicao, "Longitude")
    return (_normalizar_coordenada(lat_txt, 90),
            _normalizar_coordenada(lon_txt, 180))


def _precisao_posicao(model, nome="Position"):
    """(precisão horizontal em metros, elevação) do Coordinate — presentes em boa parte das
    localizações e sem os quais não dá para saber se o ponto vale 5 m ou 5 km."""
    posicao = model.find(f"{NS}modelField[@name='{nome}']/{NS}model")
    if posicao is None:
        return None, None
    return (_valor_campo_float(posicao, "GpsHorizontalAccuracy"),
            _valor_campo_float(posicao, "Elevation"))


def _endereco_de_modelo(model, nome):
    """Endereço legível de um <modelField> com StreetAddress dentro.

    O parser antes lia um campo "PositionAddress" que não existe em nenhum dos extratos
    auditados — o endereço sempre veio como StreetAddress aninhado."""
    end = model.find(f"{NS}modelField[@name='{nome}']/{NS}model")
    if end is None:
        return None
    partes = [_valor_campo(end, c) for c in
              ("Street1", "HouseNumber", "Street2", "Neighborhood", "City", "State", "Country", "PostalCode")]
    texto = ", ".join(p.strip() for p in partes if p and p.strip())
    return texto or None


def _enderecos_de_lista(model, nome, limite=2000):
    """Endereços de um multiModelField de StreetAddress (Contact/Addresses), juntos."""
    textos = []
    for end in _submodelos(model, nome):
        partes = [_valor_campo(end, c) for c in
                  ("Street1", "HouseNumber", "Street2", "Neighborhood", "City", "State", "Country", "PostalCode")]
        texto = ", ".join(p.strip() for p in partes if p and p.strip())
        if texto:
            textos.append(texto)
    return " | ".join(textos)[:limite] or None


def _nome_base(caminho):
    """Último segmento de um caminho, em minúsculas, aceitando barra normal e invertida."""
    if not caminho:
        return ""
    return caminho.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _casar_por_tamanho(candidatos, nome_anexo, tamanho, contagem_tamanhos):
    """Escolhe, entre os arquivos do zip com o tamanho exato do anexo, qual é o binário dele.

    Duas regras, nesta ordem:

    1. **Nome do arquivo.** Se um único candidato tem o mesmo nome que o anexo declara em
       Filename, é ele — nome igual E tamanho igual não é coincidência. É a mesma evidência que
       _resolver_anexos_por_file_id já usa para SOBRESCREVER um palpite errado, então usá-la
       aqui, antes de palpitar, só antecipa a conclusão. Recupera anexo que antes ficava
       "indisponível" só porque outro arquivo do aparelho tinha o mesmo tamanho.

    2. **Candidato único e tamanho inequívoco.** Sem nome para desempatar, só aceita quando há um
       único candidato E nenhum outro arquivo do zip tem esse tamanho (contagem_tamanhos). A
       segunda condição é nova: o índice de candidatos só enxerga as extensões de
       _EXTENSOES_ANEXO, então um arquivo sem extensão conhecida de mesmo tamanho não aparecia
       como colisão — o anexo recebia o binário errado com toda a aparência de acerto. Em prova
       pericial, "não localizado" é um resultado; binário trocado é um erro.

    contagem_tamanhos=None (chamadores antigos, ex. as ferramentas de backfill) mantém o
    comportamento anterior na regra 2."""
    if not candidatos:
        return None
    base = _nome_base(nome_anexo)
    if base:
        por_nome = [c for c in candidatos if _nome_base(c) == base]
        if len(por_nome) == 1:
            return por_nome[0]
    if len(candidatos) != 1:
        return None
    if contagem_tamanhos is not None and contagem_tamanhos.get(tamanho, 1) > 1:
        return None
    return candidatos[0]


def _extrair_anexos(model_msg, indice_binarios, caminhos_zip=None, *, contagem_tamanhos=None):
    """Extrai metadados de todos os anexos de uma mensagem e tenta achar o arquivo real no zip.

    caminhos_zip é o conjunto de nomes de arquivo do ZIP (ver indexar_indicadores); quando
    informado, permite usar o caminho que o próprio Cellebrite gravou no anexo em vez de adivinhar
    pelo tamanho. contagem_tamanhos (mesma origem) diz quantos arquivos do zip têm cada tamanho —
    ver _casar_por_tamanho."""
    anexos = []
    container = model_msg.find(f"{NS}multiModelField[@name='Attachments']")
    if container is None:
        for embutida in _submodelos(model_msg, "EmbeddedInstantMessage"):
            anexos.extend(_extrair_anexos(embutida, indice_binarios, caminhos_zip,
                                          contagem_tamanhos=contagem_tamanhos))
        return anexos
    for anexo in container.findall(f"{NS}model"):
        tamanho = None
        data_field = anexo.find(f"{NS}dataField[@name='Data']/{NS}source")
        if data_field is not None and data_field.get("length"):
            try:
                tamanho = int(data_field.get("length"))
            except ValueError:
                tamanho = None

        tipo_conteudo = _valor_campo(anexo, "ContentType")
        nome_anexo = _valor_campo(anexo, "Filename")
        # 1ª escolha: o caminho que o Cellebrite gravou ao extrair o binário
        # (attachment_extracted_path). Vem com barra INVERTIDA do Windows —
        # "files\Image\x.jpg" — enquanto o zip usa barra normal, daí o replace. É bem mais
        # confiável que adivinhar pelo tamanho, e resolve os anexos que ficavam "indisponível no
        # arquivo" só porque outro arquivo do zip tinha exatamente o mesmo tamanho.
        caminho_zip = None
        extraido = _valor_campo(anexo, "attachment_extracted_path")
        if extraido:
            candidato = extraido.replace("\\", "/").lstrip("/")
            if caminhos_zip is None or candidato in caminhos_zip:
                caminho_zip = candidato
        # 2ª escolha: casa pelo tamanho exato — pelo nome quando ele desempata, e só quando o
        # casamento é inequívoco no zip inteiro quando não desempata; colisão fica como "não
        # encontrado" em vez de arriscar servir o arquivo errado (ver _casar_por_tamanho)
        if caminho_zip is None and tamanho is not None:
            caminho_zip = _casar_por_tamanho(
                indice_binarios.get(tamanho, []), nome_anexo, tamanho, contagem_tamanhos)

        anexos.append({
            "nome_arquivo": nome_anexo,
            "titulo": _valor_campo(anexo, "Title"),
            "tipo_conteudo": tipo_conteudo,
            "bytes": tamanho,
            "url": _valor_campo(anexo, "URL"),
            "caminho_zip": caminho_zip,
            # 3ª via, resolvida depois do passe: o atributo file_id aponta para o <file id=…>,
            # e o "Local Path" daquele <file> é o binário. Como os <file> e os <model> vêm
            # intercalados no report.xml, a ligação é fechada por UPDATE ao fim da ingestão
            # (ver investigacao_utils._resolver_anexos_por_file_id). Sozinha, ela leva a
            # localização de binário de 69% para 94% dos anexos.
            "file_id": anexo.get("file_id"),
            # transcrição que o próprio Cellebrite já fez do áudio: quando existe, poupa
            # reprocessar o arquivo no faster-whisper (a fase mais lenta da extração)
            "transcricao": _valor_campo(anexo, "Transcript"),
        })

    # Um encaminhamento de mídia pode vir sem anexo no InstantMessage externo:
    # o Cellebrite guarda o vídeo/foto/arquivo no InstantMessage embutido. O mesmo
    # padrão já é usado por _corpo_mensagem para texto encaminhado. Só usamos o
    # embutido quando a mensagem externa não traz anexo, para não exibir a mídia
    # citada como se fosse anexo de uma resposta que tenha seu próprio conteúdo.
    if not anexos:
        for embutida in _submodelos(model_msg, "EmbeddedInstantMessage"):
            anexos.extend(_extrair_anexos(embutida, indice_binarios, caminhos_zip,
                                          contagem_tamanhos=contagem_tamanhos))
    return anexos


# Prefixo com que o cartão de contato compartilhado é gravado em TX_MENSAGEM. É CONTRATO com
# identificacao_pessoas._cartoes_compartilhados, que desmonta esse texto de volta em nome/
# telefone/e-mail para identificar a pessoa do cartão: mudar o formato aqui exige mudar lá e
# reprocessar, porque o que já está gravado no banco tem o texto antigo.
PREFIXO_CONTATO_COMPARTILHADO = "Contato compartilhado:"

# ARMADILHA (facebook): no Messenger, o Cellebrite modela como Contact Type=Shared tambem o
# cartao de PERFIL compartilhado na conversa — sem Name e com uma unica entrada de ID do app
# (Category "Facebook Id", Domain "User ID"). Nao e contato digitado: no app o destinatario ve o
# cartao de perfil da pessoa (com a fotinho que o Facebook mostra), mas o Cellebrite NAO extrai
# o nome nem a foto desse perfil — so o ID numerico (auditado em producao, extracao 11: a tabela
# attachments do fb-msys nao tem a mensagem, nem attachment_fbid igual ao ID; report.xml traz
# Attachments/Photos/EmbeddedInstantMessage vazios). Entao nao ha imagem a exibir: o rotulo diz
# "perfil compartilhado" para nao prometer foto nem virar contato na identificacao de pessoas.
PREFIXO_PERFIL_COMPARTILHADO = "Perfil compartilhado:"


def _so_ids_de_app(entradas):
    return bool(entradas) and all("id" in linha.split(":")[0].lower() for linha in entradas)


def _texto_contatos_compartilhados(model_msg):
    """Cartão(ões) de contato de um ``<multiModelField name="SharedContacts">`` como texto.

    Cada cartão é um ``<model type="Contact">`` com ``Name`` e ``Entries`` (telefone, e-mail,
    ID do app…). Vira texto para ficar pesquisável e aparecer na bolha, já que a mensagem que
    compartilha contato não traz ``Body`` nenhum. Cartão sem nome cujas entradas são só IDs de
    app (ver PREFIXO_PERFIL_COMPARTILHADO) sai como perfil compartilhado. None quando
    não há cartão aproveitável."""
    contatos = []
    for contato in _submodelos(model_msg, "SharedContacts"):
        nome = _valor_campo(contato, "Name")
        entradas = []
        for entrada in _submodelos(contato, "Entries"):
            valor = _valor_campo(entrada, "Value")
            if not valor or not valor.strip():
                continue
            categoria = _valor_campo(entrada, "Category") or _valor_campo(entrada, "Domain")
            linha = f"{categoria}: {valor}" if categoria else valor
            # o mesmo par categoria/valor costuma vir repetido no cartao (o vCard traz o
            # WhatsApp User Id duas vezes em boa parte dos casos); repetir na bolha so
            # polui. Categorias diferentes para o mesmo numero (CELL e HOME) ficam.
            if linha not in entradas:
                entradas.append(linha)
        detalhes = "\n".join(entradas)
        prefixo = (PREFIXO_PERFIL_COMPARTILHADO if not nome and _so_ids_de_app(entradas)
                   else PREFIXO_CONTATO_COMPARTILHADO)
        if nome and detalhes:
            contatos.append(f"{prefixo} {nome}\n{detalhes}")
        elif nome:
            contatos.append(f"{prefixo} {nome}")
        elif detalhes:
            contatos.append(f"{prefixo}\n{detalhes}")
    return "\n\n".join(contatos) or None


def _propriedades_vcard(vcard):
    """Propriedades de um vCard como listas [grupo, nome, params, valor].

    Cuida de duas coisas que o WhatsApp usa e quebram um split ingênuo: o
    dobramento de linha do RFC 2425 (linha começada em espaço/tab continua a
    anterior) e o agrupamento ``itemN.`` do iOS — ``item1.TEL`` e ``item1.X-ABLabel``
    são a MESMA entrada (o número e a etiqueta dele), e no Android a etiqueta vem
    no parâmetro ``TYPE`` (``TEL;type=CELL``)."""
    props = []
    anterior = None
    for bruta in re.split(r"\r\n|\n", vcard or ""):
        if not bruta.strip():
            anterior = None
            continue
        if bruta[0] in " \t" and anterior is not None:
            anterior[3] += bruta[1:]
            continue
        cabecalho, separador, valor = bruta.partition(":")
        if not separador:
            anterior = None
            continue
        pedacos = cabecalho.split(";")
        nome = pedacos[0].strip().upper()
        grupo = ""
        if "." in nome:
            grupo, nome = nome.split(".", 1)
        params = {}
        for pedaco in pedacos[1:]:
            chave, _, val = pedaco.partition("=")
            # um mesmo par pode vir mais de uma vez (TEL;type=CELL;type=VOICE): o segundo
            # não apaga o primeiro, vira lista — é o TYPE que etiqueta o número no Android
            val = val.strip()
            chave = chave.strip().upper()
            params[chave] = f"{params[chave]},{val}" if chave in params else val
        anterior = [grupo, nome, params, valor.strip()]
        props.append(anterior)
    return props


# TYPEs que só dizem "é voz" — a etiqueta útil (CELL, HOME, WORK) costuma vir junto; se só
# existir VOICE, ela mesma vira a categoria
_TIPOS_DE_APARATO = {"VOICE", "PAGER", "MAIN"}


def _rotulo_de_tipo(tipos):
    for tipo in tipos.split(","):
        if tipo.strip().upper() not in _TIPOS_DE_APARATO:
            return tipo.strip()
    return tipos.split(",")[0].strip()


def _texto_de_vcard(nome_midia, vcard):
    """Cartão de contato gravado como vCard (ZWAMEDIAITEM.ZVCARDSTRING) no texto que
    _texto_contatos_compartilhados usa para os SharedContacts do XML — mesmo prefixo,
    mesmo formato ``Categoria: valor`` — para a bolha, a busca e o
    identificacao_pessoas._cartoes_compartilhados tratarem os dois como um só caso.

    O nome sai do FN (o do meio do item, ZVCARDNAME, fica como queda); de cada TEL/EMAIL
    a categoria é a etiqueta do próprio cartão (X-ABLabel/TYPE), que é o que o usuário
    vê. Entradas repetidas são descartadas, como no outro leitor de cartão."""
    props = _propriedades_vcard(vcard)
    rotulos = {}
    for grupo, nome, _params, valor in props:
        if grupo and nome == "X-ABLABEL" and grupo not in rotulos and valor:
            rotulos[grupo] = valor
    nome = (nome_midia or "").strip() or None
    entradas = []
    for grupo, nome_prop, params, valor in props:
        if not valor or nome_prop in ("VERSION", "X-ABLABEL"):
            continue
        if nome_prop == "FN":
            nome = valor
            continue
        if nome_prop == "N" and not nome:
            pedacos = [p for p in valor.split(";") if p.strip()]
            nome = " ".join(p.strip() for p in reversed(pedacos[:2])) or None
            continue
        rotulo = (rotulos.get(grupo) or _rotulo_de_tipo(params.get("TYPE", ""))).strip()
        if nome_prop == "TEL":
            categoria = rotulo or "Celular"
        elif nome_prop == "EMAIL":
            categoria = rotulo or "E-mail"
        elif nome_prop == "ORG":
            categoria = "Empresa"
        elif nome_prop == "TITLE":
            categoria = "Cargo"
        else:
            continue
        linha = f"{categoria}: {valor}"
        if linha not in entradas:
            entradas.append(linha)
    detalhes = "\n".join(entradas)
    if nome and detalhes:
        return f"{PREFIXO_CONTATO_COMPARTILHADO} {nome}\n{detalhes}"
    if nome:
        return f"{PREFIXO_CONTATO_COMPARTILHADO} {nome}"
    if detalhes:
        return f"{PREFIXO_CONTATO_COMPARTILHADO}\n{detalhes}"
    return None


# caminho do banco do WhatsApp iOS dentro do .ufdr (o dump FFS da partição do aparelho)
CAMINHO_CHATSTORAGE = "files/Database/ChatStorage.sqlite"


def contatos_compartilhados_chatstorage(zf):
    """Cartões de contato que o Cellebrite NÃO copiou para o report.xml, lidos direto do
    ChatStorage.sqlite (WhatsApp iOS) que o próprio .ufdr carrega.

    Por que isto existe: o cartão enviado pelo WhatsApp Web fica na mídia da mensagem na
    base do app (ZWAMESSAGE sem texto + ZWAMEDIAITEM com ZVCARDNAME/ZVCARDSTRING), e o
    parser ZWA do Cellebrite devolve no <model> um SharedContacts VAZIO — o Reader e o
    IPED mostram o cartão porque leem as bases deles, não o report.xml. No analyser a
    mensagem chegava como "(sem conteúdo)" (caso real: extração 57 do inquérito 196,
    mensagem 4017709, encaminhada do Web; o banco interno do Cellebrite no DbData também
    está vazio para ela).

    CUIDADO COM A ARMADILHA: nas versões recentes do iOS o WhatsApp REAPROVEITA as colunas
    ZVCARDSTRING/ZVCARDNAME da mídia para gravar o MIME do anexo (``audio/ogg; codecs=opus``)
    e a chave base64 da mídia — num iPhone auditado, 130 mil mídias tinham "vCard" e só 1.290
    eram cartões de verdade. Exigir ZMESSAGETYPE=4 (o tipo do cartão) E o vCard começando de
    fato em ``BEGIN:VCARD`` é o que separa um caso do outro; só um dos dois já deixou passar
    "cartão" em áudio.

    Devolve {ZSTANZAID -> texto} — o ZSTANZAID é exatamente o Identifier do WhatsApp, a
    mesma chave que vai para NU_IDENTIFICADOR_ORIGINAL — no formato de
    _texto_contatos_compartilhados. {} quando o artefato não existe (Android, nuvem) ou
    o sqlite não puder ser lido: isto é leitura de artefato, estritamente best-effort."""
    import os
    import shutil
    import sqlite3
    import tempfile

    alvo = next((n for n in zf.namelist() if n.lower() == CAMINHO_CHATSTORAGE.lower()), None)
    if alvo is None:
        return {}
    mapa = {}
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        # o sqlite precisa de arquivo real (seek/leitura aleatória) — o membro do ZIP é copiado
        # para um temporário; o modo ro garante que nada é gravado de volta no pacote
        with zf.open(alvo) as origem, open(tmp, "wb") as destino:
            shutil.copyfileobj(origem, destino, 1 << 22)
        try:
            conexao = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            linhas = conexao.execute(
                """SELECT m.ZSTANZAID, i.ZVCARDNAME, i.ZVCARDSTRING
                     FROM ZWAMEDIAITEM i
                     JOIN ZWAMESSAGE m ON m.Z_PK = i.ZMESSAGE
                    WHERE m.ZMESSAGETYPE = 4
                      AND i.ZVCARDSTRING LIKE 'BEGIN:VCARD%'
                      AND m.ZSTANZAID IS NOT NULL""").fetchall()
        except sqlite3.Error:
            return {}  # schema do artefato mudou ou a tabela não existe: não é nosso problema
        finally:
            conexao.close()
        for stanza, nome_midia, vcard in linhas:
            texto = _texto_de_vcard(nome_midia, vcard)
            if stanza and texto:
                anterior = mapa.get(stanza)
                mapa[stanza] = f"{anterior}\n\n{texto}" if anterior else texto
    except (OSError, ValueError):
        logger.warning("Falha ao ler %s do .ufdr para contatos compartilhados (best-effort)",
                       CAMINHO_CHATSTORAGE)
        return {}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return mapa


def _corpo_mensagem(model_msg):
    """Texto da mensagem. Mensagem com contato compartilhado não traz ``Body``: o cartão fica
    em ``SharedContacts`` (ver _texto_contatos_compartilhados).

    Mensagem ENCAMINHADA também pode não trazer Body próprio: o conteúdo fica no
    <model type="InstantMessage"> aninhado em EmbeddedInstantMessage (IsEmbeddedOnly=True) — é
    esse conteúdo que o Cellebrite Reader e o IPED mostram na bolha. Sem isto a mensagem aparecia
    como "(sem conteúdo)" mesmo tendo texto (visto na prática: uma chave PIX aleatória
    encaminhada, que é exatamente o dado de interesse).

    O embutido é varrido DUAS vezes, e nessa ordem: primeiro atrás de Body, depois atrás de
    SharedContacts. A segunda volta é o cartão de contato ENCAMINHADO — aí o SharedContacts fica
    SÓ no embutido e a mensagem de fora chega sem nada (88 casos num iPhone auditado, todos do
    WhatsApp). Body vem antes porque, quando os dois existem, o texto é o que o usuário escreveu.

    Só cai no aninhado quando a mensagem não tem Body nem cartão próprio — numa resposta que cita
    outra mensagem, o embutido é a mensagem CITADA e o texto de quem respondeu está no Body de cima."""
    corpo = _valor_campo(model_msg, "Body")
    if corpo is not None and corpo.strip():
        return corpo

    cartao = _texto_contatos_compartilhados(model_msg)
    if cartao:
        return cartao

    embutidas = _submodelos(model_msg, "EmbeddedInstantMessage")
    for embutida in embutidas:
        corpo_embutido = _valor_campo(embutida, "Body")
        if corpo_embutido is not None and corpo_embutido.strip():
            return corpo_embutido
    for embutida in embutidas:
        cartao = _texto_contatos_compartilhados(embutida)
        if cartao:
            return cartao
    return corpo


# Rótulos que o Cellebrite pendura na mensagem. "Recalled" é a mensagem apagada para todos
# pelo remetente; "Forwarded" a encaminhada; "Reply" a resposta que cita outra mensagem.
#
# Vêm em DOIS lugares, e é preciso ler os dois: MessageExtraData → MessageLabel (Android) e
# <multiField name="Labels"> pendurado direto na mensagem (iPhone). A auditoria anterior
# concluiu que iPhone não tinha rótulo nenhum porque só olhava o primeiro lugar — no
# NB2026.66437 (iPhone D84AP) os Forwarded/Reply estão todos no multiField, e sem eles uma
# mensagem encaminhada aparecia na tela sem nenhuma marca, ao contrário do IPED.
_ROTULO_ENCAMINHADA = "Forwarded"
_ROTULO_APAGADA = "Recalled"
_ROTULO_TEMPORARIA = "SelfDestruct"
_ROTULO_EDITADA = "Edited"

# GUID nulo com que o Cellebrite preenche ReferenceId quando não há referência nenhuma
_GUID_VAZIO = "00000000-0000-0000-0000-000000000000"


def _extrair_rotulos(model_msg):
    """Rótulos da mensagem e o identificador da mensagem citada, quando é uma resposta
    (MessageExtraData → QuotedMessageData).

    O citado sai de OriginalMessageID — o mesmo formato do campo Identifier da mensagem
    original, que é como ela é reencontrada depois. ReferenceId só entra como queda, e só
    quando NÃO há EmbeddedInstantMessage: numa mensagem encaminhada ele aponta para o
    <model> embutido (o conteúdo repetido dentro da própria mensagem), e não para outra
    mensagem da extração — gravá-lo ali gerava referência que nunca resolvia.

    Devolve (lista de rótulos, identificador citado)."""
    rotulos = []
    citado = None
    referencia = None
    for extra in _submodelos(model_msg, "MessageExtraData"):
        tipo = extra.get("type")
        if tipo == "MessageLabel":
            rotulo = _valor_campo(extra, "Label")
            if rotulo and rotulo not in rotulos:
                rotulos.append(rotulo)
        elif tipo == "QuotedMessageData":
            citado = citado or _valor_campo(extra, "OriginalMessageID")
            ref = _valor_campo(extra, "ReferenceId")
            if ref and ref != _GUID_VAZIO:
                referencia = referencia or ref
    for rotulo in _valores_multi(model_msg, "Labels"):
        if rotulo not in rotulos:
            rotulos.append(rotulo)
    if not citado and referencia and not _submodelos(model_msg, "EmbeddedInstantMessage"):
        citado = referencia
    return rotulos, citado


def _ids_alvo_localizacao(model_msg):
    """IDs de <model> que uma localização compartilhada pode citar por jumptarget.

    Compartilhar localização no WhatsApp gera um InstantMessage com IsLocationSharing=True.
    Quando o Cellebrite grava a coordenada dentro da mensagem (modelField Position), não há
    nada a fazer aqui. Mas quando a localização é ENCAMINHADA, o iPhone não repete a
    coordenada na mensagem: ela fica num <model type="Location"> de topo, longe daqui no
    report.xml, que aponta de volta para o <model> embutido via
    <jumptargets><targetid targetType="InstantMessage">. Sem casar os dois, a mensagem cai
    na tela como "(sem conteúdo)" — foi o que aconteceu com a localização encaminhada da
    extração NB2026.66437, que o IPED mostra com latitude e longitude.

    Devolve os ids candidatos (o da própria mensagem e o dos embutidos que marcam
    IsLocationSharing), para quem ingere casar com o índice montado a partir das Locations."""
    ids = []
    for modelo in [model_msg] + _submodelos(model_msg, "EmbeddedInstantMessage"):
        if _valor_campo_bool(modelo, "IsLocationSharing") and modelo.get("id"):
            ids.append(modelo.get("id"))
    return ids


def _recibos_mensagem(model_msg, remetente):
    """(entregue, lida, reproduzida) da mensagem.

    As datas ficam no Party: no destinatário (To) para mensagem enviada, no próprio
    remetente (From) para recebida. Pegamos a mais recente entre os destinatários — numa
    conversa em grupo, "lida" só faz sentido como "alguém leu"."""
    entregue = lida = reproduzida = None
    for party in _submodelos(model_msg, "To"):
        p = _extrair_participante(party)
        entregue = max(filter(None, (entregue, p["entregue"])), default=None)
        lida = max(filter(None, (lida, p["lida"])), default=None)
        reproduzida = max(filter(None, (reproduzida, p["reproduzida"])), default=None)
    if remetente:
        entregue = entregue or remetente.get("entregue")
        lida = lida or remetente.get("lida")
        reproduzida = reproduzida or remetente.get("reproduzida")
    return entregue, lida, reproduzida


def _extrair_mensagem(model_msg, indice_binarios, caminhos_zip=None, *, contagem_tamanhos=None):
    """Extrai remetente, corpo, timestamp, status, anexos e localização de um <model type="InstantMessage">.

    Mensagem de "Localização compartilhada" (IsLocationSharing=True) não tem Body — vem só com um
    modelField Position/Coordinate (mesmo formato do Location de topo, ver _extrair_localizacao)."""
    from_field = model_msg.find(f"{NS}modelField[@name='From']/{NS}model")
    remetente = _extrair_participante(from_field) if from_field is not None else None
    latitude, longitude = _extrair_posicao(model_msg)
    # localização ENCAMINHADA: a mensagem de fora não traz Position, quem traz é o
    # InstantMessage embutido (ver _ids_alvo_localizacao para o terceiro caso, o da
    # coordenada que só existe num <model type="Location"> de topo)
    if latitude is None and longitude is None:
        for embutida in _submodelos(model_msg, "EmbeddedInstantMessage"):
            latitude, longitude = _extrair_posicao(embutida)
            if latitude is not None or longitude is not None:
                break
    rotulos, citado = _extrair_rotulos(model_msg)
    entregue, lida, reproduzida = _recibos_mensagem(model_msg, remetente)
    return {
        "remetente": remetente,
        "corpo": _corpo_mensagem(model_msg),
        "timestamp": _timestamp_iso(model_msg, "TimeStamp"),
        "tipo": _valor_campo(model_msg, "Type"),
        "status": _valor_campo(model_msg, "Status"),
        "pasta": _valor_campo(model_msg, "Folder"),
        "identificador_original": _valor_campo(model_msg, "Identifier"),
        "anexos": _extrair_anexos(model_msg, indice_binarios, caminhos_zip,
                                  contagem_tamanhos=contagem_tamanhos),
        "latitude": latitude,
        "longitude": longitude,
        "estado": _estado_ufdr(model_msg),
        "encaminhada": _ROTULO_ENCAMINHADA in rotulos or None,
        "apagada_remetente": _ROTULO_APAGADA in rotulos or None,
        "temporaria": _ROTULO_TEMPORARIA in rotulos or None,
        "editada": _ROTULO_EDITADA in rotulos or None,
        "rotulos": ", ".join(rotulos)[:3000] or None,
        "identificador_citado": _curto(citado),
        # só quando a coordenada não veio na própria mensagem: quem ingere usa estes ids para
        # casar com o <model type="Location"> de topo que aponta de volta para cá
        "ids_alvo_localizacao": [] if latitude is not None else _ids_alvo_localizacao(model_msg),
        "destinatarios": _rotulo_parties(model_msg, "To"),
        "assunto": _valor_campo(model_msg, "Subject"),
        "motivo_exclusao": _curto(_valor_campo(model_msg, "DeletionReason")),
        "smsc": _curto(_valor_campo(model_msg, "SMSC")),
        "entregue": entregue,
        "lida": lida,
        "reproduzida": reproduzida,
    }


def _extrair_chat(model_chat, indice_binarios, caminhos_zip=None, *, contagem_tamanhos=None):
    """Extrai id/nome/participantes/mensagens de um <model type="Chat">."""
    participantes = []
    container_part = model_chat.find(f"{NS}multiModelField[@name='Participants']")
    if container_part is not None:
        for party in container_part.findall(f"{NS}model"):
            p = _extrair_participante(party)
            if p["jid"]:
                participantes.append(p)

    mensagens = []
    container_msg = model_chat.find(f"{NS}multiModelField[@name='Messages']")
    if container_msg is not None:
        for msg in container_msg.findall(f"{NS}model"):
            mensagens.append(_extrair_mensagem(msg, indice_binarios, caminhos_zip,
                                              contagem_tamanhos=contagem_tamanhos))

    return {
        "chat_id": _valor_campo(model_chat, "Id"),
        "nome_grupo": _valor_campo(model_chat, "Name"),
        "inicio": _timestamp_iso(model_chat, "StartTime"),
        "ultima_atividade": _timestamp_iso(model_chat, "LastActivity"),
        "participantes": participantes,
        "mensagens": mensagens,
        # ChatType do UFDR: OneOnOne | Group | Broadcast | Unknown. O tipo vinha sendo
        # deduzido da contagem de participantes, o que classifica lista de transmissão
        # como conversa individual (1.080 delas nos oito extratos auditados).
        "tipo_chat": _curto(_valor_campo(model_chat, "ChatType"), 50),
        "descricao": _valor_campo(model_chat, "Description"),
        "conta": _curto(_valor_campo(model_chat, "Account")),
        "status_chat": _texto_multi(model_chat, "ChatStatus"),
        "estado": _estado_ufdr(model_chat),
    }


def iterar_chats(caminho_ufdr: str, fonte: str, indice_binarios: dict | None = None,
                 caminhos_zip: set | None = None):
    """Gerador que produz um dict por conversa cujo campo Source == fonte (ex. "WhatsApp", "Native Messages").

    indice_binarios (combinação de indexar_audios/indexar_imagens) é opcional — sem ele, anexos de
    áudio/imagem ficam sem caminho_zip (metadado normal, sem tentativa de casar com o binário)."""
    if indice_binarios is None:
        indice_binarios = {}
    for model_chat in _iterar_modelos_topo(caminho_ufdr, "Chat", _ANINHADOS_CHAT):
        if _valor_campo(model_chat, "Source") == fonte:
            yield _extrair_chat(model_chat, indice_binarios, caminhos_zip)


# Fontes de Chat que têm tabelas próprias (WPP_TB0x / SMS_TB0x); todo o resto (Instagram,
# Telegram, Facebook, Discord, TikTok, Twitter…) vai para as tabelas genéricas MSG_TB0x.
FONTES_NATIVAS = {FONTE_WHATSAPP, FONTE_SMS}
APP_DESCONHECIDO = "Desconhecido"


def iterar_chats_outros(caminho_ufdr: str, indice_binarios: dict | None = None,
                        caminhos_zip: set | None = None):
    """Gerador dos chats de mensageiros diversos (todo Chat cujo Source não é WhatsApp nem SMS).

    Produz (app, chat) onde `app` é o Source do chat (ex.: "Instagram", "Telegram") ou
    APP_DESCONHECIDO quando o campo vem vazio. Mesmo dict de _extrair_chat usado pelo WhatsApp."""
    if indice_binarios is None:
        indice_binarios = {}
    for model_chat in _iterar_modelos_topo(caminho_ufdr, "Chat", _ANINHADOS_CHAT):
        fonte = _valor_campo(model_chat, "Source")
        if fonte in FONTES_NATIVAS:
            continue
        yield (fonte or APP_DESCONHECIDO), _extrair_chat(model_chat, indice_binarios, caminhos_zip)


def iterar_todos_chats(caminho_ufdr: str, indice_binarios: dict | None = None,
                       caminhos_zip: set | None = None):
    """Gerador de TODOS os chats (qualquer app) num único passe pelo report.xml — desempenho.

    Produz (fonte, chat) para cada Chat, onde `fonte` é o Source cru (ex.: "WhatsApp",
    "Native Messages", "Instagram") ou None quando ausente. Quem chama roteia: WhatsApp/SMS têm
    tabelas próprias, os demais vão para MSG_TB0x. Substitui as 3 leituras separadas do report.xml
    (uma por iterar_chats(wpp)/iterar_chats(sms)/iterar_chats_outros) por uma só."""
    if indice_binarios is None:
        indice_binarios = {}
    for model_chat in _iterar_modelos_topo(caminho_ufdr, "Chat", _ANINHADOS_CHAT):
        yield _valor_campo(model_chat, "Source"), _extrair_chat(model_chat, indice_binarios, caminhos_zip)


# =====================================================================
# Contatos (Contact + Entries: PhoneNumber/EmailAddress)
# =====================================================================

_ANINHADOS_CONTATO = {"PhoneNumber", "EmailAddress", "ContactPhoto"}

# chave do AdditionalInfo que traz a data de nascimento do contato (8.799 num único
# aparelho auditado) — vem como texto livre, então é guardada como veio
_CHAVE_NASCIMENTO = "date of birth"


def indexar_por_nome_base(caminhos_zip):
    """Mapa nome-do-arquivo -> caminho completo dentro do ZIP.

    Serve para resolver referências que trazem só o nome do arquivo, sem a pasta — é o caso do
    campo Name do <model type="ContactPhoto"> no iPhone, que vem "5527988602100-133254625.thumb"
    enquanto o ZIP guarda "files/Image/5527988602100-133254625.thumb".

    Quando o mesmo nome aparece em pastas diferentes, ganha o que está em uma pasta de imagem —
    é onde as fotos de contato ficam, e o resto seria falso positivo."""
    bases: dict[str, str] = {}
    for caminho in caminhos_zip or ():
        base = caminho.rsplit("/", 1)[-1]
        atual = bases.get(base)
        if atual is None or ("/Image/" in caminho and "/Image/" not in atual):
            bases[base] = caminho
    return bases


def _foto_de_contato(model_contact, caminhos_zip=None, bases_zip=None):
    """Caminho, dentro do ZIP, da foto do contato (<model type="ContactPhoto">).

    O campo Name do ContactPhoto aponta para o arquivo extraído, mas só com o nome — sem a
    pasta —, daí o índice por nome base. Isso cobre a agenda INTEIRA do iPhone (284 de 287
    conferidos num aparelho real), enquanto o índice de fotos de perfil que já existia
    (indexar_fotos_perfil) só resolve quem tem WhatsApp. As Url do ContactPhoto são endereços
    remotos de CDN e não servem para achar binário.
    Devolve None quando o nome não corresponde a nenhum arquivo do ZIP."""
    for foto in _submodelos(model_contact, "Photos"):
        nome = _valor_campo(foto, "Name")
        if not nome:
            continue
        candidato = nome.replace("\\", "/").lstrip("/")
        if caminhos_zip is not None and candidato in caminhos_zip:
            return candidato
        if bases_zip is not None:
            achado = bases_zip.get(candidato.rsplit("/", 1)[-1])
            if achado:
                return achado
        if caminhos_zip is None and bases_zip is None:
            return candidato
    return None


def _extrair_contato(model_contact, caminhos_zip=None, bases_zip=None):
    """Extrai nome/origem/entradas (telefones e e-mails) de um <model type="Contact">."""
    entradas = []
    container = model_contact.find(f"{NS}multiModelField[@name='Entries']")
    if container is not None:
        for entrada in container.findall(f"{NS}model"):
            valor = _valor_campo(entrada, "Value")
            if not valor:
                continue
            entradas.append({
                "tipo": entrada.get("type"),
                "categoria": _valor_campo(entrada, "Category"),
                "valor": valor,
                # Domain diz de que serviço é o identificador: sem ele um <UserID> solto
                # não informa se é conta de WhatsApp, Instagram ou telefone
                "dominio": _valor_campo(entrada, "Domain"),
            })

    nascimento = None
    outros = []
    for chave, valor in _pares_chave_valor(model_contact, "AdditionalInfo"):
        if not valor:
            continue
        if chave and chave.strip().lower() == _CHAVE_NASCIMENTO and not nascimento:
            nascimento = valor.strip()[:200]
        else:
            outros.append(f"{chave}: {valor}" if chave else valor)

    nome = _valor_campo(model_contact, "Name")
    return {
        # IX198 (ID_EXTRACAO, NO_CONTATO) é um btree: entrada com mais de ~2,7 KB derruba o
        # INSERT inteiro ("index row size exceeds btree maximum" — extração 73). Nome de
        # contato com mais de 500 caracteres já é lixo de vCard colado no campo; corta.
        "nome": nome[:500] if nome else None,
        "aplicativo_origem": _valor_campo(model_contact, "Source"),
        "grupo": _valor_campo(model_contact, "Group"),
        "tipo": _valor_campo(model_contact, "Type"),
        "entradas": entradas,
        "criacao": _timestamp_iso(model_contact, "TimeCreated"),
        "modificacao": _timestamp_iso(model_contact, "TimeModified"),
        "notas": _texto_multi(model_contact, "Notes"),
        "endereco": _enderecos_de_lista(model_contact, "Addresses"),
        "organizacao": _curto(next((_valor_campo(o, "Name") for o in _submodelos(model_contact, "Organizations")
                                    if _valor_campo(o, "Name")), None)),
        "conta": _curto(_valor_campo(model_contact, "Account")),
        "nascimento": nascimento,
        "info_adicional": " | ".join(outros)[:4000] or None,
        "caminho_foto": _foto_de_contato(model_contact, caminhos_zip, bases_zip),
        "estado": _estado_ufdr(model_contact),
    }


def iterar_contatos(caminho_ufdr: str):
    """Gerador que produz um dict por registro da agenda de contatos (Contact)."""
    for model in _iterar_modelos_topo(caminho_ufdr, "Contact", _ANINHADOS_CONTATO):
        yield _extrair_contato(model)


# =====================================================================
# Ligações (Call + Parties)
# =====================================================================

_ANINHADOS_LIGACAO = {"Party"}


def _extrair_ligacao(model_call):
    """Extrai direção/status/duração/o-outro-lado de um <model type="Call">."""
    outro_lado = None
    parties = model_call.find(f"{NS}multiModelField[@name='Parties']")
    if parties is not None:
        candidatos = [_extrair_participante(p) for p in parties.findall(f"{NS}model")]
        outro_lado = next((c for c in candidatos if not c["dono_dispositivo"]), None) or (
            candidatos[0] if candidatos else None
        )

    return {
        "aplicativo": _valor_campo(model_call, "Source"),
        "direcao": _valor_campo(model_call, "Direction"),
        "status": _valor_campo(model_call, "Status"),
        "timestamp": _timestamp_iso(model_call, "TimeStamp"),
        "duracao_segundos": _duracao_para_segundos(_valor_campo(model_call, "Duration")),
        "videochamada": _valor_campo_bool(model_call, "VideoCall"),
        "telefone": outro_lado["jid"] if outro_lado else None,
        "nome_contato": outro_lado["nome"] if outro_lado else None,
        "desconexao": _curto(_valor_campo(model_call, "DisconnectionCause")),
        "conta": _curto(_valor_campo(model_call, "Account")),
        "estado": _estado_ufdr(model_call),
    }


def iterar_ligacoes(caminho_ufdr: str):
    """Gerador que produz um dict por ligação (Call) do relatório."""
    for model in _iterar_modelos_topo(caminho_ufdr, "Call", _ANINHADOS_LIGACAO):
        yield _extrair_ligacao(model)


# =====================================================================
# Localizações (Location + Coordinate/StreetAddress aninhados)
# =====================================================================

_ANINHADOS_LOCALIZACAO = {"Coordinate", "StreetAddress"}


def _jumptarget(model, tipo_alvo):
    """Id do <model> de outro domínio a que este registro se liga, via
    <jumptargets><targetid targetType="…">. None quando não há ligação daquele tipo."""
    for alvo in model.findall(f"{NS}jumptargets/{NS}targetid"):
        if alvo.get("targetType") == tipo_alvo and alvo.text and alvo.text.strip():
            return alvo.text.strip()
    return None


def _extrair_localizacao(model_loc):
    """Extrai app de origem/coordenadas/endereço de um <model type="Location">.

    O endereço vem de <modelField name="Address"> com um StreetAddress dentro. O campo
    "PositionAddress" que o parser lia antes não existe em nenhum dos oito extratos
    auditados, então TX_ENDERECO era sempre nulo — ele fica como alternativa porque outros
    conectores podem emiti-lo."""
    latitude, longitude = _extrair_posicao(model_loc)
    precisao, elevacao = _precisao_posicao(model_loc)
    return {
        "aplicativo": _valor_campo(model_loc, "Source"),
        # mensagem de "Localização compartilhada" a que esta coordenada pertence, quando é o
        # caso (ver _ids_alvo_localizacao) — não vai para LOC_TB01, serve para a ingestão
        # devolver a coordenada à bolha da conversa
        "id_mensagem_ufdr": _jumptarget(model_loc, "InstantMessage"),
        "timestamp": _timestamp_iso(model_loc, "TimeStamp"),
        "latitude": latitude,
        "longitude": longitude,
        "endereco": _endereco_de_modelo(model_loc, "Address") or _valor_campo(model_loc, "PositionAddress"),
        "categoria": _valor_campo(model_loc, "Category"),
        "descricao": _valor_campo(model_loc, "Description") or _valor_campo(model_loc, "Name"),
        "precisao": precisao,
        "elevacao": elevacao,
        "tipo": _curto(_valor_campo(model_loc, "Type")),
        "origem": _curto(_valor_campo(model_loc, "Origin")),
        "fim": _timestamp_iso(model_loc, "EndTime"),
        "conta": _curto(_valor_campo(model_loc, "Account")),
        "estado": _estado_ufdr(model_loc),
    }


def iterar_localizacoes(caminho_ufdr: str):
    """Gerador que produz um dict por localização (Location) registrada no relatório."""
    for model in _iterar_modelos_topo(caminho_ufdr, "Location", _ANINHADOS_LOCALIZACAO):
        yield _extrair_localizacao(model)


# =====================================================================
# Notificações (Notification) — Body costuma vir vazio; o app de origem real
# geralmente só aparece no texto livre de "Notes" (ex. "Notification From: com.x.y")
# =====================================================================

_RE_NOTES_PACOTE = re.compile(r"Notification From:\s*(\S+)")
_ANINHADOS_NOTIFICACAO = {"Attachment", "WebAddress"}


def _extrair_notificacao(model_notif):
    """Extrai app de origem/pacote real/conteúdo/timestamp de um <model type="Notification">.

    O pacote do app tinha uma fonte só — a regex sobre "Notes" — que existe em um único
    dos oito aparelhos auditados (um Galaxy S25+). Nos quatro iPhones e nos demais Android
    o pacote está em ServiceIdentifier, e por isso 17 mil notificações eram gravadas sem
    identificar o aplicativo. Agora lê os dois, nessa ordem de preferência.

    O conteúdo (Body) praticamente não existe: 10 ocorrências em 20.243 notificações. O que
    sobra de texto é o Subject, então ele entra como conteúdo quando não há Body — melhor
    que a linha vazia que ficava na tela."""
    pacote = None
    notas = model_notif.find(f"{NS}multiField[@name='Notes']/{NS}value")
    if notas is not None and notas.text:
        m = _RE_NOTES_PACOTE.search(notas.text)
        if m:
            pacote = m.group(1)

    assunto = _valor_campo(model_notif, "Subject")
    return {
        "aplicativo_log": _valor_campo(model_notif, "Source"),
        "pacote_app": _curto(pacote or _valor_campo(model_notif, "ServiceIdentifier")),
        "conteudo": _valor_campo(model_notif, "Body") or assunto,
        "timestamp": _timestamp_iso(model_notif, "TimeStamp"),
        "assunto": assunto,
        "tipo": _curto(_valor_campo(model_notif, "Type")),
        "status": _curto(_valor_campo(model_notif, "Status")),
        "estado": _estado_ufdr(model_notif),
    }


def iterar_notificacoes(caminho_ufdr: str):
    """Gerador que produz um dict por notificação (Notification) registrada no relatório."""
    for model in _iterar_modelos_topo(caminho_ufdr, "Notification", _ANINHADOS_NOTIFICACAO):
        yield _extrair_notificacao(model)


# =====================================================================
# Blocos de notas (Note) — Samsung Notes (Android), Apple Notes/iCloud Backup
# (iPhone), Google Keep etc. Muito usado como "caderno" de vendas em casos de
# tráfico (quantidade/preço/apelido do comprador), daí ter tabela própria.
#
# O formato varia por plataforma (checado em extratos reais): Android traz
# Title e Modification; iOS geralmente vem sem Title e Creation pode vir como
# <empty/> em vez de <value> — campos ausentes ficam None (ver _valor_campo).
# =====================================================================

_ANINHADOS_NOTA = {"Attachment"}


def _extrair_anexos_nota(model_nota):
    """Nomes dos anexos (fotos/arquivos) de uma nota — só o nome, sem tentar achar o binário
    no zip (diferente dos anexos de mensagem, que já têm essa necessidade coberta por Arquivos)."""
    nomes = []
    container = model_nota.find(f"{NS}multiModelField[@name='Attachments']")
    if container is not None:
        for anexo in container.findall(f"{NS}model"):
            nome = _valor_campo(anexo, "Filename")
            if nome:
                nomes.append(nome)
    return nomes


def _extrair_nota(model_nota):
    """Extrai app de origem/título/corpo/datas/status/anexos de um <model type="Note">."""
    return {
        "aplicativo": _valor_campo(model_nota, "Source"),
        "titulo": _valor_campo(model_nota, "Title"),
        "corpo": _valor_campo(model_nota, "Body"),
        "criacao": _timestamp_iso(model_nota, "Creation"),
        "modificacao": _timestamp_iso(model_nota, "Modification"),
        "status": _estado_ufdr(model_nota),
        "anexos": _extrair_anexos_nota(model_nota),
        "resumo": _valor_campo(model_nota, "Summary"),
        "pasta": _curto(_valor_campo(model_nota, "Folder")),
        "conta": _curto(_valor_campo(model_nota, "Account")),
    }


def iterar_notas(caminho_ufdr: str):
    """Gerador que produz um dict por nota de bloco de notas (Note) do relatório."""
    for model in _iterar_modelos_topo(caminho_ufdr, "Note", _ANINHADOS_NOTA):
        yield _extrair_nota(model)


# =====================================================================
# Histórico de navegação (VisitedPage) — sem <model> aninhado
# =====================================================================

_SEM_ANINHADOS = frozenset()


def _valor_campo_int(model, nome):
    """Como _valor_campo, mas converte o texto para int; None se ausente ou não numérico."""
    txt = _valor_campo(model, nome)
    if txt is None:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def _extrair_pagina_visitada(model_pagina):
    """Extrai navegador/título/URL/data/contagem de um <model type="VisitedPage">."""
    return {
        "aplicativo": _valor_campo(model_pagina, "Source"),
        "titulo": _valor_campo(model_pagina, "Title"),
        "url": _valor_campo(model_pagina, "Url"),
        "timestamp": _timestamp_iso(model_pagina, "LastVisited"),
        "qt_visitas": _valor_campo_int(model_pagina, "VisitCount"),
        "conta": _valor_campo(model_pagina, "Account"),
        "estado": _estado_ufdr(model_pagina),
    }


def iterar_paginas_visitadas(caminho_ufdr: str):
    """Gerador que produz um dict por página visitada (VisitedPage) do navegador."""
    for model in _iterar_modelos_topo(caminho_ufdr, "VisitedPage", _SEM_ANINHADOS):
        yield _extrair_pagina_visitada(model)


# =====================================================================
# Aplicativos instalados (InstalledApplication) — sem <model> aninhado
# =====================================================================


def _extrair_aplicativo(model_app):
    """Extrai pacote/nome/versão/datas de um <model type="InstalledApplication">."""
    return {
        "pacote": _valor_campo(model_app, "Identifier"),
        "nome": _valor_campo(model_app, "Name"),
        "versao": _valor_campo(model_app, "Version"),
        "descricao": _valor_campo(model_app, "Description"),
        "instalacao": _timestamp_iso(model_app, "PurchaseDate"),
        "ultimo_uso": _timestamp_iso(model_app, "LastLaunched"),
        "categorias": _texto_multi(model_app, "Categories"),
        "permissoes": _texto_multi(model_app, "Permissions", limite=8000),
        "diretorios": _texto_multi(model_app, "AssociatedDirectoryPaths", limite=8000),
        "estado": _estado_ufdr(model_app),
    }


def iterar_aplicativos(caminho_ufdr: str):
    """Gerador que produz um dict por aplicativo instalado (InstalledApplication)."""
    for model in _iterar_modelos_topo(caminho_ufdr, "InstalledApplication", _SEM_ANINHADOS):
        yield _extrair_aplicativo(model)


# =====================================================================
# Dono do aparelho (User) — sem <model> aninhado
# =====================================================================


def _extrair_dono(model_user):
    """Extrai nome/tipo/identificador/último login de um <model type="User">."""
    return {
        "nome": _valor_campo(model_user, "Name"),
        "tipo_usuario": _valor_campo(model_user, "UserType"),
        "identificador": _valor_campo(model_user, "Identifier"),
        "ultimo_login": _timestamp_iso(model_user, "TimeLastLoggedIn"),
    }


def iterar_donos(caminho_ufdr: str):
    """Gerador que produz um dict por dono/usuário do aparelho (User)."""
    for model in _iterar_modelos_topo(caminho_ufdr, "User", _SEM_ANINHADOS):
        yield _extrair_dono(model)


# =====================================================================
# Contas de usuário (UserAccount) — como o iOS identifica o titular
# =====================================================================
# O Cellebrite só emite <model type="User"> para Android. No iPhone, o titular aparece
# como <model type="UserAccount"> (Apple ID/iCloud, e-mail, contas de apps) e pelo campo
# IsPhoneOwner dos participantes de conversa. Extraímos a conta crua aqui; a ingestão
# filtra a(s) conta(s) Apple/iCloud e sintetiza o "dono" quando não há User (ver
# investigacao_utils._derivar_dono_ios).


def _extrair_conta_usuario(model_conta):
    """Extrai fonte/serviço/usuário/nome/data de um <model type="UserAccount">."""
    return {
        "fonte": _valor_campo(model_conta, "Source"),
        "servico": _valor_campo(model_conta, "ServiceType") or _valor_campo(model_conta, "ServiceIdentifier"),
        "usuario": _valor_campo(model_conta, "Username"),
        "nome": _valor_campo(model_conta, "Name"),
        "criado_em": _timestamp_iso(model_conta, "TimeCreated"),
        "estado": _estado_ufdr(model_conta),
    }


# =====================================================================
# Catálogo de instituições financeiras: bancos, fintechs/carteiras de pagamento,
# exchanges de cripto, casas de aposta e serviços de crédito.
#
# É usado para reconhecer dinheiro em três lugares onde o UFDR não modela conta nenhuma:
# o pacote do app instalado (InstalledApplication/Identifier), o serviço da credencial
# salva (Password/Service, que no Google Password Manager vem como pacote Android OU como
# URL de login) e a conta de aplicativo (UserAccount/ServiceType). Por isso cada padrão é
# comparado por substring contra pacote, domínio e nome de exibição ao mesmo tempo.
#
# A ordem importa: vence o primeiro padrão que casar, então marca derivada vem antes da
# marca-mãe ("com.itau.iti" antes de "com.itau").
# =====================================================================

CAT_BANCO = "banco"
CAT_PAGAMENTO = "pagamento"
CAT_CRIPTO = "cripto"
CAT_APOSTA = "aposta"
CAT_CREDITO = "credito"
CAT_CAPITALIZACAO = "capitalizacao"

_INSTITUICOES_FIN = (
    # --- bancos ---
    ("com.itau.iti", "iti (Itaú)", CAT_BANCO),
    ("com.itau", "Itaú", CAT_BANCO),
    ("itau.com.br", "Itaú", CAT_BANCO),
    ("com.santander", "Santander", CAT_BANCO),
    ("santander.com.br", "Santander", CAT_BANCO),
    ("bradesco.next", "Next (Bradesco)", CAT_BANCO),
    ("com.bradesco", "Bradesco", CAT_BANCO),
    ("bradesco.com.br", "Bradesco", CAT_BANCO),
    ("br.gov.caixa.auxilio", "Caixa Auxílio Emergencial", CAT_BANCO),
    ("br.gov.caixa.tem", "Caixa Tem", CAT_BANCO),
    ("br.com.gabba.caixa", "Caixa Econômica Federal", CAT_BANCO),
    ("br.com.caixa", "Caixa Econômica Federal", CAT_BANCO),
    ("caixa.gov.br", "Caixa Econômica Federal", CAT_BANCO),
    ("com.bb.android", "Banco do Brasil", CAT_BANCO),
    ("br.com.bb", "Banco do Brasil", CAT_BANCO),
    ("bb.com.br", "Banco do Brasil", CAT_BANCO),
    ("com.nu.production", "Nubank", CAT_BANCO),
    ("com.nubank", "Nubank", CAT_BANCO),
    ("nubank.com.br", "Nubank", CAT_BANCO),
    ("br.com.intermedium", "Banco Inter", CAT_BANCO),
    ("br.com.inter", "Banco Inter", CAT_BANCO),
    ("bancointer.com.br", "Banco Inter", CAT_BANCO),
    ("c6bank", "C6 Bank", CAT_BANCO),
    ("br.com.sicoob", "Sicoob", CAT_BANCO),
    ("br.com.sicredi", "Sicredi", CAT_BANCO),
    ("br.com.neon", "Neon", CAT_BANCO),
    ("br.com.original", "Banco Original", CAT_BANCO),
    ("br.com.bmg", "BMG", CAT_BANCO),
    ("br.com.banrisul", "Banrisul", CAT_BANCO),
    ("br.com.bancopan", "Banco Pan", CAT_BANCO),
    ("com.safra", "Safra", CAT_BANCO),
    ("br.com.willbank", "Will Bank", CAT_BANCO),
    ("com.digio", "Digio", CAT_BANCO),
    ("com.btgpactual", "BTG Pactual", CAT_BANCO),
    ("com.xpi", "XP Investimentos", CAT_BANCO),
    # --- carteiras e meios de pagamento ---
    ("com.picpay", "PicPay", CAT_PAGAMENTO),
    ("picpay", "PicPay", CAT_PAGAMENTO),
    ("br.com.uol.ps", "PagBank", CAT_PAGAMENTO),
    ("br.com.pagseguro", "PagBank", CAT_PAGAMENTO),
    ("pagseguro", "PagBank", CAT_PAGAMENTO),
    ("pagbank", "PagBank", CAT_PAGAMENTO),
    ("mercadopago", "Mercado Pago", CAT_PAGAMENTO),
    ("mercadolibre.mpwallet", "Mercado Pago", CAT_PAGAMENTO),
    ("com.b2w.ame", "Ame Digital", CAT_PAGAMENTO),
    ("com.paypal", "PayPal", CAT_PAGAMENTO),
    ("paypal.com", "PayPal", CAT_PAGAMENTO),
    ("com.transferwise", "Wise", CAT_PAGAMENTO),
    ("com.skrill", "Skrill/NETELLER", CAT_PAGAMENTO),
    ("stone.co", "Stone", CAT_PAGAMENTO),
    ("com.infinitepay", "InfinitePay", CAT_PAGAMENTO),
    ("recargapay", "RecargaPay", CAT_PAGAMENTO),
    ("br.com.jeitto", "Jeitto", CAT_PAGAMENTO),
    ("instamoney", "InstaMoney", CAT_PAGAMENTO),
    # --- cripto ---
    ("com.binance", "Binance", CAT_CRIPTO),
    ("binance.com", "Binance", CAT_CRIPTO),
    ("com.coinbase", "Coinbase", CAT_CRIPTO),
    ("com.bybit", "Bybit", CAT_CRIPTO),
    ("bitso", "Bitso", CAT_CRIPTO),
    ("mercadobitcoin", "Mercado Bitcoin", CAT_CRIPTO),
    ("foxbit", "Foxbit", CAT_CRIPTO),
    ("novadax", "NovaDAX", CAT_CRIPTO),
    ("trustapp", "Trust Wallet", CAT_CRIPTO),
    ("io.metamask", "MetaMask", CAT_CRIPTO),
    ("exodus.wallet", "Exodus", CAT_CRIPTO),
    ("piuk.blockchain", "Blockchain.com", CAT_CRIPTO),
    # --- crédito / consulta ---
    ("serasaexperian", "Serasa", CAT_CREDITO),
    ("serasa.com.br", "Serasa", CAT_CREDITO),
    ("acordocerto", "Acordo Certo", CAT_CREDITO),
    ("com.spc", "SPC Brasil", CAT_CREDITO),
    # --- capitalização / sorteios ---
    ("capixabacap", "Capixaba Cap", CAT_CAPITALIZACAO),
    ("apcapsp", "APCAP", CAT_CAPITALIZACAO),
    ("poticap", "Poti Cap", CAT_CAPITALIZACAO),
    ("vivasorte", "Viva Sorte", CAT_CAPITALIZACAO),
    # --- casas de aposta e cassinos (as que não caem na heurística de nome) ---
    ("betano", "Betano", CAT_APOSTA),
    ("betfair", "Betfair", CAT_APOSTA),
    ("pixbet", "Pixbet", CAT_APOSTA),
    ("betpix", "BetPix365", CAT_APOSTA),
    ("esportesdasorte", "Esportes da Sorte", CAT_APOSTA),
    ("superbet", "Superbet", CAT_APOSTA),
    ("sportingbet", "Sportingbet", CAT_APOSTA),
    ("vaidebet", "Vai de Bet", CAT_APOSTA),
    ("estrelabet", "EstrelaBet", CAT_APOSTA),
    ("brazino", "Brazino777", CAT_APOSTA),
    ("blaze", "Blaze", CAT_APOSTA),
    ("stake.com", "Stake", CAT_APOSTA),
    ("bet365", "Bet365", CAT_APOSTA),
    ("novibet", "Novibet", CAT_APOSTA),
    ("betsvips", "BetsVIP", CAT_APOSTA),
    ("primebets", "PrimeBets", CAT_APOSTA),
    ("seubet", "SeuBet", CAT_APOSTA),
    ("realsbet", "RealsBet", CAT_APOSTA),
    ("bullsbet", "BullsBet", CAT_APOSTA),
    ("aposta1", "Aposta1", CAT_APOSTA),
    ("galera.bet", "Galera.bet", CAT_APOSTA),
    ("tigervip", "TigerVIP", CAT_APOSTA),
    ("poplottery", "PopLottery", CAT_APOSTA),
    ("fortune.tigers", "Fortune Tiger", CAT_APOSTA),
    ("okbet", "OKBet", CAT_APOSTA),
)

# Domínios e pacotes de aposta que não estão no catálogo: o mercado tem centenas de marcas
# efêmeras ("8787bet.xyz", "m.zeroum.bet", "cassino.bet.br.twa") e listar todas é inviável.
# O lookbehind de letra evita casar "alphabet"/"betterhelp"; dígito antes é permitido de
# propósito porque metade desses domínios é numérica ("79x16.com", "8839i.com").
_RE_APOSTA = re.compile(
    r"(?<![a-z])(bet|bets|aposta|apostas|cassino|casino|loteria|lottery|bingo)(?![a-z])", re.I)


def _rotulo_servico(texto):
    """Reduz um Service/ServiceIdentifier a um rótulo legível: de 'https://m.7games.bet/'
    tira 'm.7games.bet'; de um pacote ou nome solto devolve o próprio texto."""
    txt = (texto or "").strip()
    if "://" in txt:
        txt = txt.split("://", 1)[1]
    txt = txt.split("/", 1)[0].split("?", 1)[0].strip().strip(".")
    if txt.lower().startswith("www."):
        txt = txt[4:]
    return txt or (texto or "").strip() or None


def _identificar_instituicao(*textos):
    """Reconhece a instituição financeira em pacote de app, domínio de login ou nome de
    exibição. Devolve (nome, categoria) ou (None, None).

    Faz duas passadas: primeiro o catálogo (nome canônico), depois a heurística de aposta —
    assim 'br.betano.com' vira 'Betano' e não o domínio cru."""
    for texto in textos:
        if not texto:
            continue
        alvo = texto.strip().lower()
        for padrao, nome, categoria in _INSTITUICOES_FIN:
            if padrao in alvo:
                return nome, categoria
    for texto in textos:
        if texto and _RE_APOSTA.search(texto):
            return _rotulo_servico(texto), CAT_APOSTA
    return None, None


# Rótulo de cada evidência que faz uma instituição virar linha na tela de contas financeiras.
FONTE_APP_INSTALADO = "Aplicativo instalado"
FONTE_CONTA_APP = "Conta de aplicativo"


def _acumular_conta_derivada(acumulador, instituicao, categoria, titular, fonte, modelo):
    """Junta em um só registro as evidências de uma mesma instituição+titular.

    Sem isso o mesmo banco aparece três vezes na tela (app instalado, credencial salva e
    conta de aplicativo são três modelos distintos do UFDR para a mesma conta)."""
    chave = (instituicao.strip().lower(), (titular or "").strip().lower())
    registro = acumulador.get(chave)
    if registro is None:
        registro = {
            "instituicao": instituicao.strip(),
            "categoria": categoria,
            "titular": (titular or "").strip() or None,
            "fontes": [],
            "modelos": [],
        }
        acumulador[chave] = registro
    if fonte and fonte not in registro["fontes"]:
        registro["fontes"].append(fonte)
    if modelo and modelo not in registro["modelos"]:
        registro["modelos"].append(modelo)


def _consolidar_contas_derivadas(acumulador):
    """Fecha o acumulador em registros de conta financeira prontos para gravar.

    Quando a instituição tem exatamente uma linha com titular, as linhas sem titular dela
    (app instalado, por exemplo, que nunca traz usuário) são absorvidas nessa — o app do
    PicPay e a senha do PicPay do mesmo aparelho são a mesma conta."""
    por_instituicao = {}
    for (inst, _titular), registro in acumulador.items():
        por_instituicao.setdefault(inst, []).append(registro)

    contas = []
    for registros in por_instituicao.values():
        com_titular = [r for r in registros if r["titular"]]
        sem_titular = [r for r in registros if not r["titular"]]
        if len(com_titular) == 1 and sem_titular:
            alvo = com_titular[0]
            for outro in sem_titular:
                alvo["fontes"] += [f for f in outro["fontes"] if f not in alvo["fontes"]]
                alvo["modelos"] += [m for m in outro["modelos"] if m not in alvo["modelos"]]
            registros = com_titular
        for registro in registros:
            contas.append({
                "fonte": " + ".join(registro["fontes"])[:2000] or None,
                "instituicao": registro["instituicao"],
                "titular": registro["titular"],
                "numero_conta": None,
                "agencia": None,
                "tipo_conta": registro["categoria"],
                "moeda": None,
                "saldo": None,
                "tipo_modelo": "+".join(registro["modelos"])[:80] or None,
                # conta deduzida de app/credencial/conta de app: não é um <model> próprio,
                # então não tem deleted_state
                "estado": None,
            })
    return contas


# E-mails e contas financeiras. Os campos variam entre conectores UFDR;
# aceitamos os aliases mais usuais e mantemos a fonte/modelo para rastreabilidade.
def _primeiro_campo(model, *nomes):
    for nome in nomes:
        valor = _valor_campo(model, nome)
        if valor:
            return valor
    return None


def _extrair_email(model_email):
    """Extrai um <model type="Email">.

    Remetente e destinatários NÃO são <field>: o UFDR entrega
    <modelField name="From"><model type="Party"> e <multiModelField name="To">, o mesmo
    formato de participante das conversas. Como _valor_campo só casa a tag <field>, todo
    e-mail era gravado sem remetente e sem destinatário — 15.119 de 15.119 nos oito
    extratos auditados, um deles com 11.375 e-mails. Os aliases de <field> continuam como
    alternativa porque outros conectores podem emiti-los assim."""
    de = _submodelo(model_email, "From")
    return {
        "fonte": _primeiro_campo(model_email, "Source", "SourceApplication"),
        "remetente": _rotulo_party(de) or _primeiro_campo(model_email, "From", "Sender", "FromAddress"),
        "destinatarios": (_rotulo_parties(model_email, "To")
                          or _primeiro_campo(model_email, "To", "Recipients", "ToAddress")),
        "cc": _rotulo_parties(model_email, "Cc") or _primeiro_campo(model_email, "Cc", "CC"),
        "cco": _rotulo_parties(model_email, "Bcc") or _primeiro_campo(model_email, "Bcc", "BCC"),
        "assunto": _primeiro_campo(model_email, "Subject", "Title"),
        "corpo": _primeiro_campo(model_email, "Body", "Text", "Content", "Snippet"),
        "data": _timestamp_iso(model_email, "TimeStamp") or _timestamp_iso(model_email, "DateSent") or _timestamp_iso(model_email, "Date"),
        "tipo": model_email.get("type"),
        "pasta": _valor_campo(model_email, "Folder"),
        "status": _curto(_valor_campo(model_email, "Status")),
        "conta": _curto(_valor_campo(model_email, "Account")),
        "rotulos": _texto_multi(model_email, "Labels"),
        "anexos": ", ".join(
            n for n in (_primeiro_campo(a, "Filename", "Title") for a in _submodelos(model_email, "Attachments")) if n
        )[:4000] or None,
        "estado": _estado_ufdr(model_email),
    }


def _ativos_financeiros(model_conta):
    """Lê as moedas de <multiModelField name='Assets'> (modelos FinancialAsset aninhados).

    O CryptoTracer põe o ativo da carteira aqui, não em um <field> simples — por isso
    _valor_campo não enxerga esse dado."""
    campo = model_conta.find(f"{NS}multiModelField[@name='Assets']")
    if campo is None:
        return None
    moedas = []
    for filho in campo.findall(f"{NS}model"):
        moeda = _valor_campo(filho, "Currency")
        if moeda and moeda not in moedas:
            moedas.append(moeda)
    return ", ".join(moedas) or None


# FinancialAccountType do Cellebrite → categoria usada na coluna "Tipo" da tela de contas
_TIPOS_CONTA_UFDR = {
    "CryptocurrencyWallet": "cripto",
    "BankAccount": "banco",
    "CreditCard": "cartao",
    "DebitCard": "cartao",
}


def _extrair_conta_financeira(model_conta):
    """Extrai conta bancária/financeira de <model type='FinancialAccount'|'BankAccount'|'Account'>.

    Dois formatos bem diferentes caem aqui. O primeiro é a conta modelada pelos conectores de
    apps bancários (Institution/AccountNumber/Balance). O segundo — o único presente nos
    extratos Android que analisamos — são as carteiras de criptomoeda que o CryptoTracer acha
    varrendo nomes de arquivo e mensagens: ali o endereço vem em AccountID, o ativo em
    <multiModelField name='Assets'> e não existe titular, agência nem saldo. Como nenhum dos
    aliases antigos batia nesses campos, essas contas viravam linhas totalmente vazias na tela.

    Devolve None quando o modelo não tem nenhum dado aproveitável — melhor não gravar do que
    encher a tela de linhas com traço em todas as colunas.
    """
    tipo_bruto = _primeiro_campo(model_conta, "FinancialAccountType", "AccountType", "Type")
    categoria = _TIPOS_CONTA_UFDR.get(tipo_bruto or "") or (tipo_bruto or None)

    instituicao = _primeiro_campo(
        model_conta, "Institution", "Bank", "BankName", "FinancialInstitution", "ServiceProvider")
    servico = _primeiro_campo(model_conta, "Service", "ServiceIdentifier", "ServiceType")
    origem = _primeiro_campo(model_conta, "Source", "SourceApplication", "UserMapping")
    if not instituicao:
        nome_reconhecido, categoria_reconhecida = _identificar_instituicao(servico, origem)
        instituicao = nome_reconhecido
        categoria = categoria_reconhecida or categoria

    numero = _primeiro_campo(
        model_conta, "AccountNumber", "AccountID", "AccountId", "Address", "WalletAddress",
        "Number", "Identifier", "IBAN")
    moeda = _primeiro_campo(model_conta, "Currency") or _ativos_financeiros(model_conta)

    if categoria == "cripto" and not instituicao:
        instituicao = "Carteira de criptomoeda" + (f" ({moeda})" if moeda else "")

    # Procedência: o CryptoTracer registra onde achou o endereço (FoundInField/FoundInModelType).
    # Isso é o que permite ao analista separar uma carteira real de um falso positivo lido de
    # um nome de arquivo, então vai junto da fonte em vez de se perder.
    achado_em = _primeiro_campo(model_conta, "FoundInField")
    achado_modelo = _primeiro_campo(model_conta, "FoundInModelType")
    fonte = origem
    if achado_em or achado_modelo:
        detalhe = " em ".join(x for x in (achado_em, achado_modelo) if x)
        fonte = f"{origem or 'UFDR'} ({detalhe})"

    conta = {
        "fonte": fonte,
        "instituicao": instituicao,
        "titular": _primeiro_campo(model_conta, "Owner", "AccountHolder", "Name", "Username"),
        "numero_conta": numero,
        "agencia": _primeiro_campo(model_conta, "Branch", "BranchNumber", "Agency"),
        "tipo_conta": categoria,
        "moeda": moeda,
        "saldo": _primeiro_campo(model_conta, "Balance", "AvailableBalance"),
        "tipo_modelo": model_conta.get("type"),
        "estado": _estado_ufdr(model_conta),
    }
    if not any(conta[campo] for campo in ("instituicao", "titular", "numero_conta", "saldo")):
        return None
    return conta


# =====================================================================
# Senhas e credenciais salvas (Password) — inclui Google Password Manager, contas de
# apps (Accounts), tokens de autenticação etc. Pode conter credenciais bancárias.
# =====================================================================


def _extrair_senha(model_senha):
    """Extrai credencial salva de <model type='Password'>: fonte, serviço, usuário e dados."""
    servico = _primeiro_campo(model_senha, "Service", "ServiceIdentifier")
    instituicao, categoria = _identificar_instituicao(servico, _primeiro_campo(model_senha, "Label"))
    return {
        "fonte": _primeiro_campo(model_senha, "Source"),
        "servico": servico,
        "usuario": _primeiro_campo(model_senha, "Account", "Username"),
        "dados": _primeiro_campo(model_senha, "Data", "Password"),
        "tipo_modelo": model_senha.get("type"),
        # Type distingue senha salva de token/cookie de sessão; AccessGroup é o chaveiro
        # do iOS que agrupa credenciais do mesmo app
        "tipo_senha": _curto(_valor_campo(model_senha, "Type")),
        "grupo_acesso": _valor_campo(model_senha, "AccessGroup"),
        "estado": _estado_ufdr(model_senha),
        # instituição financeira do serviço, se for uma (None quando não é)
        "instituicao_fin": instituicao,
        "categoria_fin": categoria,
    }


# =====================================================================
# Autofill (dados de preenchimento automático) — salvo por navegadores e
# gerenciadores de senha; pode conter dados bancários.
# =====================================================================


def _extrair_autofill(model_autofill):
    """Extrai entrada de autofill: fonte, chave e valor."""
    return {
        "fonte": _primeiro_campo(model_autofill, "Source"),
        "chave": _primeiro_campo(model_autofill, "Key"),
        "valor": _primeiro_campo(model_autofill, "Value"),
        "tipo_modelo": model_autofill.get("type"),
        "registro": _timestamp_iso(model_autofill, "TimeStamp"),
        "ultimo_uso": _timestamp_iso(model_autofill, "LastUsedDate"),
        "estado": _estado_ufdr(model_autofill),
    }


# =====================================================================
# Redes Wi-Fi / Conexões de rede — WirelessNetwork, NetworkActivity, WifiConnection, etc.
# O Cellebrite nomeia os campos de forma diferente dependendo da versão, SO do
# dispositivo e conector usado. Por isso cada campo tenta várias alternativas.
# =====================================================================

# Modelos que representam dados Wi-Fi/rede e devem ser tratados como "wifi"
_TIPOS_WIFI = frozenset({"WirelessNetwork", "WifiConnection", "WiFiConnection", "WiFiNetwork"})


def _extrair_rede_wifi(model_wifi):
    """Extrai SSID/BSSID/segurança/senha/conexões/metadados de modelos de rede Wi-Fi."""
    ssid = _primeiro_campo(
        model_wifi, "SSId", "SSID", "Ssid", "ssid", "NetworkName", "WifiName",
        "ConnectionName", "Name", "Identifier", "Description", "Title",
        "Notes", "Network",
    )
    bssid = _primeiro_campo(
        model_wifi, "BSSId", "BSSID", "Bssid", "bssid", "MAC", "MacAddress",
        "RouterMac", "AccessPointMac", "BSSIDAddress",
        "RemoteIdentifier", "RemoteAddress", "DestinationAddress",
        "TargetAddress",
    )
    seguranca = _primeiro_campo(
        model_wifi, "SecurityMode", "Security", "EncryptionType", "Encryption",
        "KeyManagement", "AuthAlgorithm", "AuthenticationAlgorithm",
        "SecurityType", "NetworkSecurity",
    )
    senha = _primeiro_campo(
        model_wifi, "Password", "password", "PreSharedKey", "PSK", "psk",
        "NetworkKey", "Key", "WPAKey", "WEPKey", "Passphrase",
    )
    qt_txt = _primeiro_campo(
        model_wifi, "ConnectionCount", "TimesConnected", "ConnectCount",
        "connectCount", "UsageCount", "VisitCount",
    )
    primeira = (
        _timestamp_iso(model_wifi, "FirstConnection")
        or _timestamp_iso(model_wifi, "FirstConnected")
        or _timestamp_iso(model_wifi, "FirstUsage")
        or _timestamp_iso(model_wifi, "DateFirstSeen")
    )
    ultima = (
        _timestamp_iso(model_wifi, "LastConnection")
        or _timestamp_iso(model_wifi, "LastAutoConnection")
        or _timestamp_iso(model_wifi, "LastConnected")
        or _timestamp_iso(model_wifi, "LastUsage")
        or _timestamp_iso(model_wifi, "TimeStamp")
        or _timestamp_iso(model_wifi, "DateLastSeen")
    )
    canal_txt = _primeiro_campo(model_wifi, "Channel", "ChannelNumber")
    sinal_txt = _primeiro_campo(model_wifi, "SignalStrength", "RSSI", "Signal", "SignalLevel")
    oculta = _valor_campo_bool(model_wifi, "IsHidden") or _valor_campo_bool(model_wifi, "HiddenSSID")
    autenticacao = _primeiro_campo(
        model_wifi, "AuthenticationAlgorithm", "AuthAlgorithm", "EAPMethod",
        "AuthenticationType",
    )
    fabricante = _primeiro_campo(
        model_wifi, "Manufacturer", "OEM", "Vendor", "RouterManufacturer",
    )
    favorita = _valor_campo_bool(model_wifi, "IsFavorite") or _valor_campo_bool(model_wifi, "IsPreferred")

    try:
        qt_int = int(qt_txt) if qt_txt else None
    except (ValueError, TypeError):
        qt_int = None
    try:
        canal_int = int(canal_txt) if canal_txt else None
    except (ValueError, TypeError):
        canal_int = None
    try:
        sinal_int = int(sinal_txt) if sinal_txt else None
    except (ValueError, TypeError):
        sinal_int = None

    # 86% das redes dos extratos auditados trazem coordenada em <modelField name="Position">.
    # É georreferenciamento fino de onde o aparelho esteve: o BSSID identifica o roteador e a
    # coordenada diz onde ele fica. Antes nenhuma era importada.
    latitude, longitude = _extrair_posicao(model_wifi)

    return {
        "ssid": ssid,
        "bssid": bssid,
        "seguranca": seguranca,
        "senha": senha,
        "qt_conexoes": qt_int,
        "primeira_conexao": primeira,
        "ultima_conexao": ultima,
        "canal": canal_int,
        "sinal": sinal_int,
        "rede_oculta": oculta,
        "autenticacao": autenticacao,
        "fabricante": fabricante,
        "favorita": favorita,
        "fonte": model_wifi.get("type"),
        "latitude": latitude,
        "longitude": longitude,
        "tipo_conexao": _curto(_valor_campo(model_wifi, "NWConnectionType")),
        "estado": _estado_ufdr(model_wifi),
    }


# =====================================================================
# Domínios que o parser descartava inteiros (caíam no "Model type desconhecido").
#
# A auditoria de oito extratos reais mostrou 861 mil registros de topo nessa
# situação. Os extratores abaixo cobrem os tipos com valor probatório; ficaram
# de fora, por decisão de escopo, LogEntry (log do sistema iOS),
# DictionaryWord (dicionário do teclado) e RemoteServiceSession.
#
# Os tipos marcados "só iPhone" simplesmente não aparecem num Android — a
# tabela fica vazia, como qualquer outro domínio ausente.
# =====================================================================


def _extrair_uso_app(model):
    """<model type="AppsUsageLog">: um evento de uso de aplicativo (o maior domínio do
    UFDR — 413 mil linhas nos oito extratos). É o que responde "o que a pessoa estava
    usando às 22h14"."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "pacote": _valor_campo(model, "Identifier"),
        "acao": _valor_campo(model, "Action"),
        "inicio": _timestamp_iso(model, "StartTime"),
        "fim": _timestamp_iso(model, "EndTime"),
        "submodulo": _valor_campo(model, "SubModule"),
        "servico": _valor_campo(model, "ServiceIdentifier"),
        "estado": _estado_ufdr(model),
    }




# chave do KeyValueModel que carrega o MAC nos dispositivos reconhecidos/conectados
_CHAVE_MAC = "mac address"


def _mac_e_resto(model, nome):
    """(MAC, demais pares) de um multiModelField de KeyValueModel."""
    mac = None
    resto = []
    for chave, valor in _pares_chave_valor(model, nome):
        if chave and chave.strip().lower() == _CHAVE_MAC and not mac:
            mac = (valor or "").strip() or None
        elif valor:
            resto.append(f"{chave}: {valor}" if chave else valor)
    return mac, (" | ".join(resto)[:2000] or None)


def _extrair_dispositivo_reconhecido(model):
    """<model type="RecognizedDevice">: aparelho que o celular enxergou por perto. Liga dois
    aparelhos que estiveram no mesmo lugar sem depender de conversa entre eles."""
    mac, resto = _mac_e_resto(model, "DeviceIdentifiers")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "nome": _valor_campo(model, "Name"),
        "tipo_dispositivo": _valor_campo(model, "DeviceType"),
        "modelo": _valor_campo(model, "DeviceModel"),
        "serie": _valor_campo(model, "SerialNumber"),
        "mac": mac,
        "identificadores": resto,
        "estado": _estado_ufdr(model),
    }


def _extrair_conexao_dispositivo(model):
    """<model type="DeviceConnectivity">: conexão Wi-Fi ou Bluetooth, com data e o outro lado."""
    mac, _resto = _mac_e_resto(model, "DeviceIdentifiers")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "metodo": _valor_campo(model, "ConnectivityMethod"),
        "natureza": _valor_campo(model, "ConnectivityNature"),
        "tipo_dispositivo": _valor_campo(model, "DeviceType"),
        "nome_dispositivo": _valor_campo(model, "DeviceName"),
        "mac": mac,
        "timestamp": _timestamp_iso(model, "StartTime"),
        "estado": _estado_ufdr(model),
    }


def _extrair_cookie(model):
    """<model type="Cookie">: domínio, valor e datas — mostra em que sites houve sessão."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "nome": _valor_campo(model, "Name"),
        "dominio": _valor_campo(model, "Domain"),
        "caminho": _valor_campo(model, "Path"),
        "valor": _valor_campo(model, "Value"),
        "app_relacionado": _valor_campo(model, "RelatedApplication"),
        "criacao": _timestamp_iso(model, "CreationTime"),
        "ultimo_acesso": _timestamp_iso(model, "LastAccessTime"),
        "expiracao": _timestamp_iso(model, "Expiry"),
        "estado": _estado_ufdr(model),
    }


def _extrair_atividade_social(model):
    """<model type="SocialMediaActivity">: post, comentário ou check-in, com autor, texto,
    URL, coordenada e anexos. É conteúdo de rede social que só existia no sistema quando
    virava conversa."""
    latitude, longitude = _extrair_posicao(model)
    autor = _submodelo(model, "Author")
    anexos = [n for n in (_primeiro_campo(a, "Filename", "Title")
                          for a in _submodelos(model, "Attachments")) if n]
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "plataforma": _valor_campo(model, "Platform"),
        "tipo_atividade": _valor_campo(model, "SocialActivityType"),
        "titulo": _valor_campo(model, "Title"),
        "corpo": _valor_campo(model, "Body"),
        "url": _valor_campo(model, "Url"),
        "timestamp": _timestamp_iso(model, "TimeStamp"),
        "autor_nome": _valor_campo(autor, "Name") if autor is not None else None,
        "autor_id": _valor_campo(autor, "Identifier") if autor is not None else None,
        "conta": _valor_campo(model, "Account"),
        "situacao": _valor_campo(model, "Status"),
        "canal": _valor_campo(model, "ChannelType"),
        "privacidade": _valor_campo(model, "PrivacySetting"),
        "reacoes": _valor_campo_int(model, "ReactionsCount"),
        "compartilhamentos": _valor_campo_int(model, "SharesCount"),
        "comentarios": _valor_campo_int(model, "CommentCount"),
        "latitude": latitude,
        "longitude": longitude,
        "marcados": _rotulo_parties(model, "TaggedParties"),
        "anexos": ", ".join(anexos)[:4000] or None,
        "post_original": _valor_campo(model, "OriginalPostId"),
        "do_log_atividade": _valor_campo_bool(model, "IsFromActivityLog"),
        "estado": _estado_ufdr(model),
    }


def _extrair_busca(model):
    """<model type="SearchedItem">: termo que a pessoa digitou (Safari, Chrome, Gmail,
    Instagram, WhatsApp, Maps). Prova direta de intenção."""
    latitude, longitude = _extrair_posicao(model)
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "origem": _valor_campo(model, "Origin"),
        "termo": _valor_campo(model, "Value"),
        "timestamp": _timestamp_iso(model, "TimeStamp"),
        "conta": _valor_campo(model, "Account"),
        "resultados": _texto_multi(model, "SearchResults", limite=4000),
        "latitude": latitude,
        "longitude": longitude,
        "estado": _estado_ufdr(model),
    }


def _extrair_envio_nuvem(model):
    """<model type="FileUpload"> — só iPhone nos extratos auditados. O que saiu do aparelho
    para a nuvem, com conta, pasta e configuração de compartilhamento."""
    dono = _submodelo(model, "Owner")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "nome_arquivo": _valor_campo(model, "Name"),
        "titulo": _valor_campo(model, "Title"),
        "url": _valor_campo(model, "Url"),
        "tipo_arquivo": _valor_campo(model, "FileType"),
        "pasta": _valor_campo(model, "Folder"),
        "conta": _valor_campo(model, "Account"),
        "dono": _rotulo_party(dono),
        "compartilhamento": _valor_campo(model, "ShareSettings"),
        "dt_envio": _timestamp_iso(model, "DateUploaded"),
        "dt_modificacao": _timestamp_iso(model, "DateLastModified"),
        "estado": _estado_ufdr(model),
    }


def _extrair_sessao_conta(model):
    """<model type="RemoteServiceSession">: sessão de login de uma conta na nuvem
    (Facebook, Instagram, Google…), com IP, user-agent, tipo de dispositivo e período.

    É dado de CONTA, não do aparelho — vai para ACS_TB01_ACESSOS (a mesma tela "Acessos"
    da Nuvem Google/IPED, ver funcoes/contas_nuvem_utils.py). O dict já vem no formato
    que _gravar_acesso_conta consome. Nesses extratos a sessão é um período
    (DateStarted→DateEnded), não um evento instantâneo — daí dt_fim_acesso."""
    pares = _pares_chave_valor(model, "SessionIdentifiers")
    ip = proxy = None
    outros = []
    for chave, valor in pares:
        baixa = (chave or "").lower()
        if not valor:
            continue
        if "proxy" in baixa:
            proxy = proxy or valor
        elif "ip" in baixa:
            ip = ip or valor
        else:
            outros.append(f"{chave}={valor}")
    user_agent = _valor_campo(model, "UserAgent")
    dispositivo = _valor_campo(model, "DeviceType")
    return {
        "timestamp": _timestamp_iso(model, "DateStarted"),
        "dt_fim": _timestamp_iso(model, "DateEnded"),
        "ip": ip,
        "ip_proxy": proxy,
        "ip_nao_roteavel": None,
        "user_agent": None if user_agent in ("-", "") else user_agent,
        "produto": _valor_campo(model, "Source"),
        "subproduto": None if dispositivo in ("Unknown", "-", "") else dispositivo,
        "tipo": "sessão de login",
        "canal": None,
        "android_id": None,
        "ios_id": None,
        "outro_dispositivo": "; ".join(outros)[:3000] or None,
        "imap_id": None,
        "activesync_id": None,
        "resolucao": None,
        "oauth_login_id": None,
        "stateful_token_id": None,
        "cookie_auth_time": None,
        "no_conta": _valor_campo(model, "Account"),
        "fonte": f"Cellebrite ({_valor_campo(model, 'Source') or 'RemoteServiceSession'})",
    }


def _extrair_compromisso(model):
    """<model type="CalendarEntry">: compromisso com assunto, detalhes, período, local,
    recorrência e convidados."""
    latitude, longitude = _extrair_posicao(model, "EventPosition")
    repeticao = ", ".join(
        p for p in (_valor_campo(model, "RepeatRule"), _valor_campo(model, "RepeatDay"),
                    _valor_campo(model, "RepeatInterval")) if p and p.strip())
    organizador = _submodelo(model, "Organizer")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "categoria": _valor_campo(model, "Category"),
        "assunto": _valor_campo(model, "Subject"),
        "detalhes": _valor_campo(model, "Details"),
        "local": _valor_campo(model, "Location"),
        "inicio": _timestamp_iso(model, "StartDate"),
        "fim": _timestamp_iso(model, "EndDate"),
        "convidados": _rotulo_parties(model, "Attendees"),
        "organizador": _rotulo_party(organizador),
        "repeticao": repeticao or None,
        "lembretes": _texto_multi(model, "Reminders"),
        "situacao": _valor_campo(model, "Status"),
        "prioridade": _valor_campo(model, "Priority"),
        "disponibilidade": _valor_campo(model, "Availability"),
        "conta": _valor_campo(model, "Account"),
        "latitude": latitude,
        "longitude": longitude,
        "estado": _estado_ufdr(model),
    }


def _extrair_uso_aplicativo(model):
    """<model type="ApplicationUsage">: total acumulado por app (tempo em primeiro plano,
    aberturas). O AppsUsageLog é evento a evento; este é o agregado."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "pacote": _valor_campo(model, "Identifier"),
        "aberturas": _valor_campo_int(model, "LaunchCount"),
        "ativacoes": _valor_campo_int(model, "ActivationCount"),
        "tempo_ativo": _valor_campo_int(model, "ActiveTime"),
        "tempo_fundo": _valor_campo_int(model, "BackgroundTime"),
        "ultima_duracao": _valor_campo_int(model, "LastUsageDuration"),
        "inicio": _timestamp_iso(model, "StartTime"),
        "fim": _timestamp_iso(model, "EndTime"),
        "ultima_abertura": _timestamp_iso(model, "LastLaunch"),
        "estado": _estado_ufdr(model),
    }


def _ponto_de_trajeto(model, nome):
    """(latitude, longitude, endereço) de FromPoint/ToPoint de um Journey.

    Diferente das outras coordenadas, o ponto do trajeto é um <model type="Location">
    completo — a coordenada está um nível mais abaixo, no Position dele."""
    ponto = _submodelo(model, nome)
    if ponto is None:
        return None, None, None
    latitude, longitude = _extrair_posicao(ponto)
    endereco = (_endereco_de_modelo(ponto, "Address")
                or _valor_campo(ponto, "PositionAddress")
                or _valor_campo(ponto, "Description")
                or _valor_campo(ponto, "Name"))
    return latitude, longitude, endereco


def _extrair_trajeto(model):
    """<model type="Journey">: deslocamento com origem, destino e pontos intermediários."""
    lat_o, lon_o, end_o = _ponto_de_trajeto(model, "FromPoint")
    lat_d, lon_d, end_d = _ponto_de_trajeto(model, "ToPoint")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "tipo": _valor_campo(model, "Type"),
        "nome": _valor_campo(model, "Name"),
        "lat_origem": lat_o,
        "lon_origem": lon_o,
        "endereco_origem": end_o,
        "lat_destino": lat_d,
        "lon_destino": lon_d,
        "endereco_destino": end_d,
        "qt_pontos": len(_submodelos(model, "WayPoints")) or None,
        "inicio": _timestamp_iso(model, "StartTime"),
        "fim": _timestamp_iso(model, "EndTime"),
        "conta": _valor_campo(model, "Account"),
        "estado": _estado_ufdr(model),
    }


def _extrair_info_aparelho(model):
    """<model type="DeviceInfoEntry">: um par nome/valor da identidade do aparelho —
    IMEI, IMSI, ICCID de cada chip, MSISDN, número de série, MAC, ID do Android, ID da
    Apple, fuso horário, data de reset de fábrica, até a senha do aparelho.

    É de onde sai o insumo de convergência entre casos, e é o dado explícito que
    _derivar_dono_ios tentava deduzir varrendo as contas de aplicativo do iPhone."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "categoria": _valor_campo(model, "EntryCategory"),
        "campo": _valor_campo(model, "EntryName"),
        "valor": _valor_campo(model, "EntryValue"),
        "timestamp": _timestamp_iso(model, "TimeStamp"),
        "estado": _estado_ufdr(model),
    }


def _extrair_antena(model):
    """<model type="CellTower"> — só iPhone nos extratos auditados. MCC/MNC/LAC/CID
    identificam a ERB; com a coordenada, é rastro de deslocamento independente de GPS."""
    latitude, longitude = _extrair_posicao(model)
    return {
        "tipo_rede": _valor_campo(model, "Type"),
        "mcc": _valor_campo(model, "MCC"),
        "mnc": _valor_campo(model, "MNC"),
        "lac": _valor_campo(model, "LAC"),
        "cid": _valor_campo(model, "CID"),
        "latitude": latitude,
        "longitude": longitude,
        "timestamp": _timestamp_iso(model, "TimeStamp"),
        "estado": _estado_ufdr(model),
    }


def _extrair_voicemail(model):
    """<model type="Voicemail"> — só iPhone nos extratos auditados. Um terço já vem com a
    transcrição pronta do Cellebrite."""
    de = _submodelo(model, "From")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "telefone": _valor_campo(de, "Identifier") if de is not None else None,
        "nome_contato": (_valor_campo(de, "Name") if de is not None else None) or _valor_campo(model, "Name"),
        "timestamp": _timestamp_iso(model, "Timestamp") or _timestamp_iso(model, "TimeStamp"),
        "modificacao": _timestamp_iso(model, "LastModified"),
        "duracao_segundos": _duracao_para_segundos(_valor_campo(model, "Duration")),
        "ouvida": _valor_campo_bool(model, "WasPlayed"),
        "tipo": _valor_campo(model, "Type"),
        "transcricao": _valor_campo(model, "Transcript"),
        "estado": _estado_ufdr(model),
    }


def _extrair_favorito(model):
    """<model type="WebBookmark">: favorito do navegador."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "titulo": _valor_campo(model, "Title"),
        "url": _valor_campo(model, "Url"),
        "pasta": _valor_campo(model, "Path"),
        "qt_visitas": _valor_campo_int(model, "VisitCount"),
        "ultima_visita": _timestamp_iso(model, "LastVisited") or _timestamp_iso(model, "TimeStamp"),
        "estado": _estado_ufdr(model),
    }


def _extrair_download(model):
    """<model type="FileDownload">: o que foi baixado, de onde e para onde."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "url": _valor_campo(model, "Url"),
        "destino": _valor_campo(model, "TargetPath"),
        "bytes": _valor_campo_int(model, "FileSize"),
        "bytes_recebidos": _valor_campo_int(model, "BytesReceived"),
        "situacao": _valor_campo(model, "DownloadState"),
        "inicio": _timestamp_iso(model, "StartTime"),
        "fim": _timestamp_iso(model, "EndTime"),
        "ultimo_acesso": _timestamp_iso(model, "LastAccessed"),
        "redirecionamentos": _texto_multi(model, "DownloadURLChains", limite=4000),
        "estado": _estado_ufdr(model),
    }


def _extrair_transferencia(model):
    """<model type="TransferOfFunds"> — só iPhone nos extratos auditados. Dinheiro modelado
    pelo próprio Cellebrite: valor, moeda, participantes e situação. Entra ao lado das
    contas financeiras porque é o mesmo assunto das telas de RIF e SIMBA."""
    preco = _submodelo(model, "TransferAmount")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "tipo": _valor_campo(model, "TransferType"),
        "descricao": _valor_campo(model, "Description"),
        "valor": _valor_campo_float(preco, "Amount") if preco is not None else None,
        "moeda": _valor_campo(preco, "Currency") if preco is not None else None,
        "situacao": _valor_campo(model, "Status"),
        "participantes": _rotulo_parties(model, "Participants"),
        "dt_envio": _timestamp_iso(model, "DateSent"),
        "dt_processamento": _timestamp_iso(model, "DateProcessed"),
        "estado": _estado_ufdr(model),
    }


def _extrair_cartao_credito(model):
    """<model type="CreditCard">: bandeira, número (costuma vir mascarado), validade e titular."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "bandeira": _valor_campo(model, "Company"),
        "numero": _valor_campo(model, "CreditCardNumber"),
        "validade": _valor_campo(model, "ExpirationDate"),
        "titular": _valor_campo(model, "NameOnCard"),
        "ultimo_uso": _timestamp_iso(model, "DateLastUsed"),
        "estado": _estado_ufdr(model),
    }


def _extrair_cartao_carteira(model):
    """<model type="MobileCard"> — só iPhone nos extratos auditados. Passe de embarque,
    ingresso ou cartão de fidelidade guardado na carteira do aparelho."""
    latitude, longitude = _extrair_posicao(model)
    org = _submodelo(model, "Organization")
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "descricao": _valor_campo(model, "Description"),
        "organizacao": _valor_campo(org, "Name") if org is not None else None,
        "tipo": _valor_campo(model, "Type"),
        "codigo_barras": _valor_campo(model, "Barcode"),
        "dt_compra": _timestamp_iso(model, "PurchaseTime"),
        "dt_modificacao": _timestamp_iso(model, "ModifyTime"),
        "dt_expiracao": _timestamp_iso(model, "ExpirationTime"),
        "latitude": latitude,
        "longitude": longitude,
        "estado": _estado_ufdr(model),
    }


def _extrair_gravacao(model):
    """<model type="Recording">: gravação de áudio feita no aparelho."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "titulo": _valor_campo(model, "Title"),
        "tipo": _valor_campo(model, "Type"),
        "timestamp": _timestamp_iso(model, "TimeStamp"),
        "duracao_segundos": _duracao_para_segundos(_valor_campo(model, "Duration")),
        "estado": _estado_ufdr(model),
    }


def _extrair_uso_rede(model):
    """<model type="NetworkUsage">: consumo de dados por rede/período."""
    return {
        "aplicativo": _valor_campo(model, "Source"),
        "ssid": _valor_campo(model, "SSId"),
        "tipo_conexao": _valor_campo(model, "NetworkConnectionType"),
        "tipo_uso": _valor_campo(model, "UsageMode"),
        "roaming": _valor_campo_bool(model, "IsRoaming"),
        "bytes_recebidos": _valor_campo_int(model, "NumberOfBytesReceived"),
        "bytes_enviados": _valor_campo_int(model, "NumberOfBytesSent"),
        "inicio": _timestamp_iso(model, "DateStarted"),
        "fim": _timestamp_iso(model, "DateEnded"),
        "estado": _estado_ufdr(model),
    }


# =====================================================================
# Passe ÚNICO — lê o report.xml UMA vez e despacha cada registro de topo relevante já extraído.
# Substitui as ~8 leituras completas do arquivo (uma por tipo: Chat, Contact, Call, Location,
# Notification, VisitedPage, InstalledApplication, User). Como o report.xml é enorme (GBs) e
# o parse em Python é o gargalo, este é o maior ganho de desempenho da extração.
# =====================================================================

# Os <model> aninhados são preservados pela própria contagem de profundidade: só o registro de
# topo (profundidade 0 ao fechar) é limpo, e a limpeza dele libera a subárvore inteira de uma vez.
# Não existe aqui uma lista de tipos aninhados a proteger — ela era redundante com a contagem e
# descartava os registros de topo cujo tipo também ocorre aninhado (ver iterar_registros).


# =====================================================================
# Arquivos do dispositivo (elementos <file> — o filesystem inteiro do aparelho).
# Ficam FORA de qualquer <model> (verificado no extrato real: todos em profundidade
# de modelo 0), então entram no mesmo passe único, interceptados pela tag.
# =====================================================================


def _extrair_arquivo(elem_file):
    """Extrai nome/caminho/categoria/mime/tamanho/data/status/caminho-no-zip de um <file>.

    O binário só existe no ZIP quando o metadado "Local Path" está presente (ex.
    'files\\Image\\IMG.jpg'); convertemos as barras para o formato do zip. Sem ele, o
    arquivo é listado só como metadado (sem abrir/baixar)."""
    caminho = elem_file.get("path") or ""
    nome = caminho.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or caminho

    tamanho = None
    if elem_file.get("size"):
        try:
            tamanho = int(elem_file.get("size"))
        except ValueError:
            tamanho = None

    # "Local Path": ponteiro direto para o binário dentro do zip (barras invertidas -> normais).
    # MD5/SHA256 vêm prontos do Cellebrite em praticamente todo arquivo (1.700.636 de
    # 1.703.877 nos oito extratos auditados) e as colunas de hash já existiam na tabela,
    # recebendo None — sem eles não há confronto com base de hashes conhecidos,
    # deduplicação nem convergência entre os aparelhos do mesmo caso.
    caminho_zip = None
    hash_md5 = None
    hash_sha256 = None
    for md in elem_file.findall(f"{NS}metadata[@section='File']"):
        for item in md.findall(f"{NS}item"):
            nome_item = item.get("name")
            if not item.text:
                continue
            if nome_item == "Local Path":
                caminho_zip = item.text.replace("\\", "/")
            elif nome_item == "MD5":
                hash_md5 = item.text.strip() or None
            elif nome_item == "SHA256":
                hash_sha256 = item.text.strip() or None

    # data de modificação: accessInfo/timestamp name="ModifyTime" (ISO 8601 com offset)
    dt_mod = None
    ts = elem_file.find(f"{NS}accessInfo/{NS}timestamp[@name='ModifyTime']")
    if ts is not None:
        formatado = ts.get("formattedTimestamp") or ts.text
        if formatado:
            try:
                dt_mod = datetime.fromisoformat(formatado)
            except ValueError:
                dt_mod = None

    return {
        "nome": nome,
        "caminho": caminho,
        "categoria": categoria_de_extensao(nome),
        "mime": mime_de_extensao(nome),
        "bytes": tamanho,
        "dt_modificacao": dt_mod,
        "status": elem_file.get("deleted"),
        "caminho_zip": caminho_zip,
        "hash_md5": hash_md5,
        "hash_sha256": hash_sha256,
        # id do <file> no report.xml: é o alvo do file_id dos anexos (ver _extrair_anexos)
        "id_ufdr": elem_file.get("id"),
    }


def iterar_arquivos(caminho_ufdr: str):
    """Gera SÓ os arquivos do aparelho (<file>) num passe pelo report.xml — para backfill em
    extrações já carregadas, sem reprocessar chats/contatos/etc.

    Mesma gestão de memória de iterar_registros: cada <model> de topo é limpo inteiro assim que
    termina (sem extrair nada dele), liberando sua subárvore; cada <file> é extraído e liberado.
    Assim a memória fica limitada mesmo num report.xml de dezenas de GB."""
    with ufdr_zip.abrir(caminho_ufdr) as zf:
        with xml_limpo.abrir(zf) as f:
            profundidade = 0
            pilha = _PilhaConteineres()
            for evento, elem in ET.iterparse(f, events=("start", "end")):
                tag = elem.tag
                if tag.endswith("}file"):
                    if evento == "end":
                        yield _extrair_arquivo(elem)
                        elem.clear()
                        if profundidade == 0:
                            pilha.descartar_processados()
                    continue
                if not tag.endswith("}model"):
                    if profundidade == 0:
                        pilha.evento(evento, elem)
                    continue
                if evento == "start":
                    profundidade += 1
                    continue
                # evento == "end"
                profundidade -= 1
                if profundidade == 0:
                    elem.clear()
                    pilha.descartar_processados()


def iterar_registros(caminho_ufdr: str = "", indice_binarios: dict | None = None,
                    *, caminhos_zip: set | None = None, contagem_tamanhos: dict | None = None,
                    zf: zipfile.ZipFile | None = None, progresso: dict | None = None,
                    desconhecidos: dict | None = None):
    """Passe único pelo report.xml: produz (tipo, dados) para cada registro de topo relevante,
    já extraído. tipo ∈ {chat, contato, ligacao, localizacao, notificacao, nota, navegacao,
    aplicativo, dono, conta_usuario, arquivo}. Para 'chat', dados = (fonte, chat_dict) — quem chama
    roteia WhatsApp/SMS/mensageiros.

    Mesma lógica de limpeza seletiva de _iterar_modelos_topo, mas com a UNIÃO dos tipos aninhados e
    um despacho por tipo do registro de topo — em vez de filtrar por um único tipo-alvo. Os <file>
    (arquivos do aparelho) são interceptados pela tag no mesmo passe (ficam fora dos <model>).
    Se zf for informado, reusa o ZipFile em vez de abrir o ZIP pelo caminho.

    progresso: dict opcional que este gerador MANTÉM ATUALIZADO com {'lidos', 'total'} em bytes
    descomprimidos do report.xml, para quem consome exibir porcentagem sem ter como contar
    registros de antemão (não dá para saber quantos são sem ler o arquivo inteiro antes). O
    tamanho vem do índice do ZIP e a posição de f.tell() — ambos baratos, mas tell() é chamado a
    cada _PASSO_PROGRESSO eventos para não pesar num XML de milhões de elementos.

    desconhecidos: dict opcional, também MANTIDO ATUALIZADO no lugar, com {type: quantidade} dos
    <model> de topo que este passe não sabe interpretar. Antes eles saíam só em log de nível
    debug, um por registro — ou seja, na prática ninguém via. O Cellebrite cria tipos novos a
    cada versão do Physical Analyzer, e um tipo novo é dado do aparelho sendo descartado em
    silêncio; com o resumo, quem opera enxerga o que ficou de fora e em que volume."""
    if indice_binarios is None:
        indice_binarios = {}

    def _generate(zf_obj):
        if progresso is not None:
            progresso["total"] = zf_obj.getinfo("report.xml").file_size
            progresso["lidos"] = 0
        # Contas financeiras deduzidas de app instalado / credencial salva / conta de app.
        # Ficam acumuladas até o fim do passe porque as três evidências da MESMA conta chegam
        # em pontos distintos do report.xml e só dá para juntá-las depois de ver todas.
        contas_derivadas = {}
        # índice nome-do-arquivo -> caminho, montado uma vez: a foto de contato do iPhone é
        # referenciada só pelo nome (ver _foto_de_contato)
        bases_zip = indexar_por_nome_base(caminhos_zip) if caminhos_zip else None
        with xml_limpo.abrir(zf_obj) as f:
            profundidade = 0
            desde_medicao = 0
            # sem isto, cada <model>/<file> já processado fica pendurado no pai como casca
            # vazia até o fim do passe — ver _PilhaConteineres
            pilha = _PilhaConteineres()
            for evento, elem in ET.iterparse(f, events=("start", "end")):
                if progresso is not None:
                    desde_medicao += 1
                    if desde_medicao >= _PASSO_PROGRESSO:
                        desde_medicao = 0
                        try:
                            progresso["lidos"] = f.tell()
                        except Exception:
                            progresso["total"] = 0  # stream não seekable: desiste da porcentagem
                tag = elem.tag
                if tag.endswith("}file"):
                    if evento == "end":
                        yield "arquivo", _extrair_arquivo(elem)
                        elem.clear()
                        # só na raiz: um <file> aninhado num <model> (não é o que o Cellebrite
                        # emite hoje, mas nada garante) teria como "pai" na pilha o contêiner do
                        # <model> ainda aberto, e limpá-lo tiraria da árvore um registro em curso
                        if profundidade == 0:
                            pilha.descartar_processados()
                    continue
                if not tag.endswith("}model"):
                    if profundidade == 0:
                        pilha.evento(evento, elem)
                    continue
                if evento == "start":
                    profundidade += 1
                    continue
                profundidade -= 1
                # A checagem de profundidade já protege os aninhados: um <model> filho nunca
                # chega aqui com profundidade 0. O teste por TIPO que existia antes desta linha
                # descartava, além disso, os registros de topo cujo tipo também aparece
                # aninhado — e InstantMessage é um deles, então toda mensagem sem Chat pai
                # sumia em silêncio (755 delas nos quatro iPhones auditados).
                if profundidade != 0:
                    continue
                tp = elem.get("type")
                if tp == "Chat":
                    yield "chat", (_valor_campo(elem, "Source"),
                                   _extrair_chat(elem, indice_binarios, caminhos_zip,
                                                 contagem_tamanhos=contagem_tamanhos))
                elif tp == "InstantMessage":
                    # mensagem solta, sem conversa: quem grava a coloca numa conversa
                    # sintética do app, para não perder o conteúdo nem inventar participante
                    fonte = _primeiro_campo(elem, "Source", "SourceApplication")
                    yield "mensagem_solta", (fonte, _extrair_mensagem(elem, indice_binarios, caminhos_zip,
                                                                      contagem_tamanhos=contagem_tamanhos))
                elif tp == "Contact":
                    yield "contato", _extrair_contato(elem, caminhos_zip, bases_zip)
                elif tp == "Call":
                    yield "ligacao", _extrair_ligacao(elem)
                elif tp == "Location":
                    yield "localizacao", _extrair_localizacao(elem)
                elif tp == "Notification":
                    yield "notificacao", _extrair_notificacao(elem)
                elif tp == "Note":
                    yield "nota", _extrair_nota(elem)
                elif tp == "VisitedPage":
                    yield "navegacao", _extrair_pagina_visitada(elem)
                elif tp == "InstalledApplication":
                    dados_app = _extrair_aplicativo(elem)
                    yield "aplicativo", dados_app
                    nome_inst, categoria = _identificar_instituicao(
                        dados_app.get("pacote"), dados_app.get("nome"))
                    if nome_inst:
                        _acumular_conta_derivada(
                            contas_derivadas, nome_inst, categoria, None,
                            FONTE_APP_INSTALADO, "InstalledApplication")
                elif tp == "User":
                    yield "dono", _extrair_dono(elem)
                elif tp == "UserAccount":
                    dados_conta_app = _extrair_conta_usuario(elem)
                    yield "conta_usuario", dados_conta_app
                    nome_inst, categoria = _identificar_instituicao(
                        dados_conta_app.get("servico"), dados_conta_app.get("nome"))
                    if nome_inst:
                        _acumular_conta_derivada(
                            contas_derivadas, nome_inst, categoria,
                            dados_conta_app.get("usuario"), FONTE_CONTA_APP, "UserAccount")
                elif tp in _TIPOS_WIFI:
                    yield "wifi", _extrair_rede_wifi(elem)
                elif tp in {"NetworkActivity", "WifiNetworkActivity", "ConnectionEvent", "WifiConnectionEvent"}:
                    yield "wifi", _extrair_rede_wifi(elem)
                elif tp in {"Email", "EMail", "EmailMessage"}:
                    yield "email", _extrair_email(elem)
                elif tp in {"FinancialAccount", "BankAccount", "Account"}:
                    conta = _extrair_conta_financeira(elem)
                    if conta:
                        yield "conta_financeira", conta
                elif tp == "Password":
                    dados_senha = _extrair_senha(elem)
                    yield "senha", dados_senha
                    if dados_senha.get("instituicao_fin"):
                        _acumular_conta_derivada(
                            contas_derivadas, dados_senha["instituicao_fin"],
                            dados_senha["categoria_fin"], dados_senha["usuario"],
                            dados_senha["fonte"] or "Credencial salva", "Password")
                elif tp == "Autofill":
                    yield "autofill", _extrair_autofill(elem)
                elif tp == "FormData":
                    # formulário salvo: o conteúdo são <model type="Autofill"> aninhados,
                    # que vão para a mesma tabela do autofill solto
                    for entrada in _submodelos(elem, "Entries"):
                        yield "autofill", _extrair_autofill(entrada)
                elif tp == "AppsUsageLog":
                    yield "uso_app", _extrair_uso_app(elem)
                elif tp == "RecognizedDevice":
                    yield "dispositivo_reconhecido", _extrair_dispositivo_reconhecido(elem)
                elif tp == "DeviceConnectivity":
                    yield "conexao_dispositivo", _extrair_conexao_dispositivo(elem)
                elif tp == "Cookie":
                    yield "cookie", _extrair_cookie(elem)
                elif tp == "SocialMediaActivity":
                    yield "atividade_social", _extrair_atividade_social(elem)
                elif tp == "SearchedItem":
                    yield "busca", _extrair_busca(elem)
                elif tp == "FileUpload":
                    yield "envio_nuvem", _extrair_envio_nuvem(elem)
                elif tp == "CalendarEntry":
                    yield "compromisso", _extrair_compromisso(elem)
                elif tp == "ApplicationUsage":
                    yield "uso_aplicativo", _extrair_uso_aplicativo(elem)
                elif tp == "Journey":
                    yield "trajeto", _extrair_trajeto(elem)
                elif tp == "DeviceInfoEntry":
                    yield "info_aparelho", _extrair_info_aparelho(elem)
                elif tp == "CellTower":
                    yield "antena", _extrair_antena(elem)
                elif tp == "Voicemail":
                    yield "voicemail", _extrair_voicemail(elem)
                elif tp == "WebBookmark":
                    yield "favorito", _extrair_favorito(elem)
                elif tp == "FileDownload":
                    yield "download", _extrair_download(elem)
                elif tp == "TransferOfFunds":
                    yield "transferencia", _extrair_transferencia(elem)
                elif tp == "CreditCard":
                    yield "cartao_credito", _extrair_cartao_credito(elem)
                elif tp == "MobileCard":
                    yield "cartao_carteira", _extrair_cartao_carteira(elem)
                elif tp == "Recording":
                    yield "gravacao", _extrair_gravacao(elem)
                elif tp == "NetworkUsage":
                    yield "uso_rede", _extrair_uso_rede(elem)
                elif tp == "RemoteServiceSession":
                    yield "acesso_conta", _extrair_sessao_conta(elem)
                else:
                    # LogEntry (log do sistema iOS) e DictionaryWord (dicionário do teclado)
                    # ficaram fora de propósito — muito volume para o
                    # valor probatório que têm. O que NÃO é deliberado (tipo novo de uma versão
                    # nova do Physical Analyzer) some junto, então o resumo por tipo vai para
                    # `desconhecidos` e quem chama registra o total no fim do passe.
                    if desconhecidos is not None:
                        desconhecidos[tp] = desconhecidos.get(tp, 0) + 1
                    logger.debug("Model type desconhecido ignorado: %s", tp)
                elem.clear()
                pilha.descartar_processados()

        for conta_derivada in _consolidar_contas_derivadas(contas_derivadas):
            yield "conta_financeira", conta_derivada

    if zf is not None:
        yield from _generate(zf)
    else:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            yield from _generate(zf)
