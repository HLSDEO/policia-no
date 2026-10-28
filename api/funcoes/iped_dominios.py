"""Roteamento dos itens de um caso do IPED para as tabelas de domínio do analyser
(conversas, ligações, contatos, localizações, notas, contas, senhas, redes, …).

Por que existe: até aqui a importação desviava DUAS das ~188 categorias que o IPED define
(`Emails` e `Contacts`) e mandava todo o resto para ARQ_TB01_ARQUIVOS como arquivo genérico.
Só que o IPED não indexa arquivo: ele DECODIFICA. Cada mensagem de WhatsApp, cada ligação,
cada contato de aplicativo vira um item próprio do índice, com metadado semântico já pronto
(`WhatsAppParser.extractMessages`, `UfedXmlReader`) — e tudo isso estava virando linha de
arquivo com nome "chat_message_37" e nada mais.

PRINCÍPIO DESTE MÓDULO: nada aqui é obrigatório. Um caso do IPED tanto pode ser um celular
(UFDR/Cellebrite decodificado), quanto uma quebra de sigilo de nuvem (Google Takeout,
iCloud), quanto um HD de computador indexado, quanto os três na mesma pasta. Um caso de HD
não tem uma única mensagem de WhatsApp e não pode nem desacelerar por causa disso. Então:

- a decisão é por `contentType`/`category`, e item que não casa com nenhum domínio sai
  daqui sem consumir nada (vira arquivo, como sempre foi);
- toda gravação é best-effort: parser que falha vira aviso no log e o item segue como
  arquivo, nunca derruba a importação;
- nenhuma tabela é tocada se o caso não tem itens daquele domínio — sem chat, nenhuma linha
  em MSG_TB0x, nenhum arquivo temporário criado.

CONVERSAS PRECISAM DE DOIS PASSES. A mensagem é um item filho do chat (`parentId` aponta
para o `id` do chat), e o dumper entrega os documentos na ordem do índice — que não garante
o chat antes das suas mensagens. Guardar tudo em memória não serve: um celular tem centenas
de milhares de mensagens. Por isso as mensagens vão para um SQLite temporário indexado por
`parentId` (`_BufferMensagens`) e só no fim, chat a chat, são lidas de volta em ordem e
gravadas por `_gravar_conversa_msg`. Mensagem cujo chat não apareceu cai numa conversa
sintética por aplicativo, no mesmo espírito de `CHAT_SEM_CONVERSA` do UFDR.
"""

import json
import logging
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from funcoes import iped_campos as C
from funcoes.contas_nuvem_utils import (
    _gravar_evento_conta,
)
from funcoes.investigacao_utils import (
    _gravar_aplicativo,
    _gravar_autofill,
    _gravar_contato_agenda,
    _gravar_conta_financeira,
    _gravar_conta_usuario,
    _gravar_conversa_msg,
    _gravar_dominio_novo,
    _gravar_ligacao,
    _gravar_localizacao,
    _gravar_nota,
    _gravar_pagina_visitada,
    _gravar_rede_wifi,
    _gravar_senha,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulário do IPED
#
# As categorias vêm de conf/CategoriesConfig.json (188 no padrão). O nome guardado no índice
# é sempre o canônico em INGLÊS: a tradução de iped-categories_pt_BR.properties só é aplicada
# na exibição (Category.getName(), relatório HTML) e a busca desfaz a tradução antes de
# consultar (QueryBuilder.getNonLocalizedCategory). Conferido num caso brasileiro real, que
# grava "Contacts"/"Emails"/"PDF Documents".
#
# Um item traz a categoria FOLHA ("WhatsApp", "Phone Calls"), não a raiz da árvore ("Chats",
# "Calls") — daí as folhas listadas uma a uma.
# ---------------------------------------------------------------------------

CAT_CHATS = {
    "Chats", "WhatsApp", "Threema", "Messenger", "Skype", "Telegram", "Jabber", "Facebook",
    "Instagram", "Signal", "Snapchat", "TikTok", "Viber", "Discord", "Others Chats",
}
CAT_MENSAGENS = {"Instant Messages", "SMS Messages", "MMS Messages", "Chat Activities"}
CAT_ANEXOS_MENSAGEM = {"Message Attachments"}
CAT_LIGACOES = {
    "Calls", "Discord Calls", "Facebook Calls", "Phone Calls", "Signal Calls",
    "Telegram Calls", "Threema Calls", "WhatsApp Calls", "Other Calls",
}
CAT_CONTATOS = {"Contacts", "Shared Contacts"}
CAT_LOCALIZACOES = {"Locations", "Journeys", "Cell Towers"}
CAT_NOTAS = {"Notes"}
CAT_EVENTOS = {"Calendar", "Appointments"}
CAT_CONTAS = {"User Accounts"}
CAT_SENHAS = {"Passwords"}
CAT_AUTOFILL = {"Autofill"}
CAT_REDES = {"Wireless Networks"}
CAT_APPS = {"Applications Usage", "Android Applications"}
CAT_NAVEGACAO = {"Internet History", "Internet History Entries", "Web Bookmarks"}
# "Searches" saiu de CAT_NAVEGACAO: o termo buscado é conteúdo próprio (BSC_TB01_BUSCAS,
# o mesmo destino do SearchedItem do UFDR), não página visitada.
CAT_BUSCAS = {"Searches"}
# o IPED grava o singular em alguns casos ("Financial Account"), o UFDR só o plural
CAT_FINANCEIRO = {"Financial Accounts", "Financial Account", "Credit Cards",
                  "Transfers of Funds", "Mobile Cards"}
CAT_SOCIAL = {"Social Media Activities"}
CAT_CONECTIVIDADE = {"Device Connectivity"}
CAT_COOKIES = {"Cookies"}
CAT_VOICEMAIL = {"Voicemail"}
CAT_GRAVACOES = {"Recordings"}
# Decodificado pelo UFDR mas ignorado POR DECISÃO DE ESCOPO no UFDR (ver
# _TIPOS_IGNORADOS_DE_PROPOSITO em investigacao_utils.py): muito volume para o valor probatório.
# Aqui o item não vira nem arquivo nem linha de domínio — some junto com o ruído.
CAT_IGNORADOS = {"Device Events"}
CAT_EMAILS = {"Emails", "Mailboxes"}

# MIME que identifica o item independentemente da categoria. Vale mais que a categoria porque
# o parser que decodificou o item é quem grava o MIME (`StandardParser.INDEXER_CONTENT_TYPE`),
# enquanto a categoria vem de uma regra de classificação por extensão/MIME que pode ter sido
# customizada no caso.
MIME_CHAT = {
    "application/x-whatsapp-chat", "application/x-telegram-chat", "application/x-discord-chat",
    "application/x-discord-chat+json", "application/x-threema-chat",
    "application/x-skype-conversation", "application/x-ufed-chat",
}
MIME_MENSAGEM = {
    "message/x-whatsapp-message", "message/x-telegram-message", "message/x-discord-message",
    "message/x-threema-message", "message/x-skype-message", "message/x-chat-message",
    "application/x-ufed-instantmessage", "application/x-ufed-sms", "application/x-ufed-mms",
}
MIME_ANEXO_MENSAGEM = {
    "message/x-whatsapp-attachment", "message/x-telegram-attachment",
    "application/x-ufed-attachment",
}
MIME_LIGACAO = {
    "call/x-whatsapp-call", "call/x-telegram-call", "call/x-discord-call",
    "application/x-ufed-call",
}
MIME_CONTATO = {"text/x-vcard", "text/vcard", "contact/x-whatsapp-contact",
                "application/x-ufed-contact"}
MIME_CONTA = {"application/x-ufed-useraccount", "application/x-whatsapp-account",
              "application/x-telegram-account"}
MIME_LOCALIZACAO = {"application/x-ufed-location"}
MIME_BUSCA = {"application/x-ufed-searcheditem"}
MIME_SOCIAL = {"application/x-ufed-socialmediaactivity"}
MIME_CONECTIVIDADE = {"application/x-ufed-deviceconnectivity"}
MIME_COOKIE = {"application/x-ufed-cookie"}
MIME_VOICEMAIL = {"application/x-ufed-voicemail"}
MIME_GRAVACAO = {"application/x-ufed-recording"}
# Aplicativo instalado vem do UfedXmlReader com categoria "Programs and Libraries" (não
# "Applications Usage"), então a categoria não resolve — o MIME é a única pista estável.
MIME_APP = {"application/x-ufed-installedapplication"}
# Prévia HTML de um chat: o UfedChatParser também indexa o preview renderizado (o HTML do chat)
# com a MESMA categoria folha do chat de verdade ("WhatsApp"/"Others Chats"). Roteá-los como
# conversa duplicaria cada chat (a prévia repete `Conversation:id`/participantes mas não tem
# mensagem nenhuma). São arquivos de verdade (têm hash/storage) — devolvidos como arquivo.
MIME_PREVIEW_CHAT = {
    "application/x-ufed-chat-preview", "application/x-ufed-chat-preview-whatsapp",
    "application/x-ufed-chat-preview-instagram",
}


def _mime(campos: dict) -> str:
    return (C.valor(campos, "contentType") or "").split(";")[0].strip().lower()


def _categorias(campos: dict) -> set[str]:
    return {c.strip() for c in C.valores(campos, "category") if c}


# ---------------------------------------------------------------------------
# Nome do aplicativo
# ---------------------------------------------------------------------------

# MIME "application/x-whatsapp-chat" -> "WhatsApp". O rótulo bate com o que o UFDR grava em
# MSG_TB01_CONTATOS.TP_APP, para que a mesma tela sirva as duas origens.
_APPS_CONHECIDOS = {
    "whatsapp": "WhatsApp", "telegram": "Telegram", "discord": "Discord", "threema": "Threema",
    "skype": "Skype", "signal": "Signal", "instagram": "Instagram", "facebook": "Facebook",
    "messenger": "Messenger", "snapchat": "Snapchat", "tiktok": "TikTok", "viber": "Viber",
    "jabber": "Jabber",
}

# `ufed:Source` traz o NOME DO ARQUIVO de origem quando o parser decodificou um banco SQLite —
# comum em notas (Apple Notes -> NoteStore.sqlite, Samsung Notes -> notes.sqlite). O nome do
# arquivo não é nome de app; aqui ele vira o rótulo amigável que o UFDR já grava.
_FONTES_SQLITE = {
    "notestore.sqlite": "Apple Notes",
    "notes.sqlite": "Samsung Notes",
}


def app_do_item(campos: dict) -> str:
    """Nome do aplicativo do item. Tenta, em ordem: o campo que o parser preenche
    (`accountType`, `ufed:Source`), o MIME (`application/x-whatsapp-chat`), a categoria folha
    ("WhatsApp") e o caminho. Cai em "IPED" quando nada identifica — melhor um rótulo
    genérico do que perder a conversa."""
    declarado = C.valor_variantes(campos, "accountType", "ufed:Source", "ufed:ServiceIdentifier")
    if declarado:
        bruto = str(declarado).strip()
        # `ufed:Source` pode trazer o caminho inteiro até o banco (".../NoteStore.sqlite");
        # o nome-base é o que interessa para casar com os mapas.
        chave = bruto.lower().replace("\\", "/").rsplit("/", 1)[-1]
        return _APPS_CONHECIDOS.get(chave) or _FONTES_SQLITE.get(chave) or bruto[:60]
    alvo = f"{_mime(campos)} {' '.join(_categorias(campos))} {C.valor(campos, 'path') or ''}".lower()
    for chave, rotulo in _APPS_CONHECIDOS.items():
        if chave in alvo:
            return rotulo
    for cat in _categorias(campos):
        if cat in CAT_CHATS and cat != "Chats":
            return cat
    return "IPED"


# ---------------------------------------------------------------------------
# Buffer de mensagens em SQLite temporário
# ---------------------------------------------------------------------------

class _BufferMensagens:
    """Guarda as mensagens do caso fora da memória, indexadas pelo `parentId` (o `id` do chat
    dono), para serem lidas de volta chat a chat no fim da importação.

    O arquivo só é criado quando a PRIMEIRA mensagem chega: caso de HD ou de nuvem, que não
    tem conversa nenhuma, não paga nada por isso. `ordem` é o número do documento no índice —
    preserva a ordem em que o IPED emitiu as mensagens dentro de um chat, que é a ordem
    cronológica que o parser montou."""

    def __init__(self):
        self._con: sqlite3.Connection | None = None
        self._caminho: Path | None = None
        self.total = 0

    def _abrir(self) -> sqlite3.Connection:
        if self._con is None:
            fd, nome = tempfile.mkstemp(prefix="iped_msg_", suffix=".db")
            os.close(fd)
            self._caminho = Path(nome)
            con = sqlite3.connect(nome)
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("CREATE TABLE msg (pai TEXT, ordem INTEGER, dados TEXT)")
            self._con = con
        return self._con

    def guardar(self, pai: str, ordem: int, mensagem: dict) -> None:
        con = self._abrir()
        con.execute("INSERT INTO msg (pai, ordem, dados) VALUES (?, ?, ?)",
                    (pai or "", ordem, json.dumps(mensagem, default=str)))
        self.total += 1

    def indexar(self) -> None:
        """Índice criado só no fim, depois de todas as inserções — construir durante a
        importação custaria a cada insert e não serve a ninguém antes da leitura."""
        if self._con is not None:
            self._con.execute("CREATE INDEX ix_msg_pai ON msg (pai, ordem)")
            self._con.commit()

    def pais(self) -> list[str]:
        if self._con is None:
            return []
        return [linha[0] for linha in self._con.execute("SELECT DISTINCT pai FROM msg")]

    def mensagens(self, pai: str) -> list[dict]:
        if self._con is None:
            return []
        linhas = self._con.execute(
            "SELECT dados FROM msg WHERE pai = ? ORDER BY ordem", (pai or "",)).fetchall()
        return [json.loads(linha[0]) for linha in linhas]

    def fechar(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            except sqlite3.Error:
                pass
            self._con = None
        if self._caminho is not None:
            try:
                self._caminho.unlink(missing_ok=True)
            except OSError:
                logger.warning("Nuvem IPED: não foi possível apagar %s", self._caminho)
            self._caminho = None


# ---------------------------------------------------------------------------
# Conversão item -> dict do domínio
#
# Todas as funções abaixo são tolerantes: campo ausente vira None e o registro entra com o
# que houver. Nenhuma levanta.
# ---------------------------------------------------------------------------

def _texto(campos: dict, *chaves: str, limite: int = 3000) -> str | None:
    v = C.valor_variantes(campos, *chaves)
    return str(v).strip()[:limite] if v is not None and str(v).strip() else None


_RE_ENTRE_PARENTESES = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _identificador(bruto: str | None) -> str:
    """Normaliza um identificador de participante. O IPED escreve de três formas — o JID pronto
    (`5511999...@s.whatsapp.net`), o número puro (`557399087401`) ou o número formatado
    (`+55 73 99987-0909`). A chave natural do contato (`NU_IDENTIFICADOR` em MSG_TB01_CONTATOS)
    tem que ser a MESMA entre o chat e as suas mensagens, então número vira dígitos (o domínio
    `@s.whatsapp.net`/`@g.us` é redundante) e JID não-numérico fica como está."""
    texto = (bruto or "").strip()
    if not texto:
        return ""
    if "@" in texto:
        local = texto.split("@", 1)[0].strip()
        return re.sub(r"\D", "", local)[:300] if re.fullmatch(r"[+\d]+", local) else texto[:300]
    if re.fullmatch(r"[+\d][\d\s().\-]+", texto):
        return re.sub(r"\D", "", texto)[:300]
    return texto[:300]


def _participante(bruto: str | None) -> dict | None:
    """Participante a partir das formas que o IPED escreve em `Message-From/To`,
    `Communication:From/To` e `Conversation:Participants`:

    - `Fulano (id:5511999...)` — o formato do WhatsAppParser;
    - `Fulano (+557398256412)` / `Fulano (557398256412)` — o formato do UfedMessageParser;
    - `5511999...@s.whatsapp.net` ou só o nome.

    Os campos `:name`/`:id` (que o UfedXmlReader também grava) NÃO são usados de propósito:
    num chat com vários participantes os três ficam em ordens diferentes (medido num caso
    real), então a única forma que preserva o par nome×número correto é parsear a string
    exibida "Fulano (numero)" — que já traz os dois juntos.

    O identificador é a chave natural do contato (UQ07 em MSG_TB01_CONTATOS); sem ele não dá
    para ligar a mesma pessoa entre conversas, então quando só há nome ele vira o próprio
    identificador."""
    texto = (bruto or "").strip()
    if not texto:
        return None
    ident = nome = None
    if "(id:" in texto:
        nome, _, resto = texto.partition("(id:")
        ident = resto.rstrip(") ").strip()
        nome = nome.strip() or None
    else:
        m = _RE_ENTRE_PARENTESES.match(texto)
        if m:
            nome = m.group(1).strip() or None
            ident = m.group(2).strip()
        else:
            ident = texto
    ident = _identificador(ident)
    return {"jid": ident, "nome": (nome or None) and nome[:300]} if ident else None


def _participantes(campos: dict, *chaves: str) -> list[dict]:
    """Participantes de um chat, parseando a forma exibida (a única que preserva o par
    nome×número) e quebrando no `;` que o WhatsAppParser usa para juntar vários."""
    achados: list[dict] = []
    for chave in chaves:
        for bruto in C.valores(campos, chave):
            for pedaco in str(bruto).split(";"):
                p = _participante(pedaco)
                if p and p not in achados:
                    achados.append(p)
    return achados


def _remetente_da_mensagem(campos: dict) -> dict | None:
    return _participante(C.valor(campos, "Message-From") or C.valor(campos, "Communication:From"))


def _destinatarios_da_mensagem(campos: dict) -> list[dict]:
    return _participantes(campos, "Message-To", "Communication:To")


def _corpo_e_legenda(campos: dict) -> tuple[str | None, str | None]:
    """(corpo, legenda) da mensagem.

    O `Message-Body` do WhatsAppParser é MULTIVALORADO e a ordem dos valores tem significado:
    o parser grava primeiro o TEXTO da mensagem (`m.getData()`) e, quando não há texto (mídia),
    um marcador interno `! TIPO` (`MESSAGE_TYPE_PREFIX` em WhatsAppParser.java); a LEGENDA da
    mídia entra DEPOIS (`mediaCaption`). Ou seja: primeiro valor sem `! ` é o corpo; se o
    primeiro valor é `! TIPO`, a mensagem é mídia sem corpo e o valor seguinte é a legenda.

    O marcador `! TIPO` é artefato do IPED e não é conteúdo — cai fora. A legenda, que era
    perdida por aqui, vira o `titulo` do anexo (mesma convenção do UFDR: legenda no Title do
    anexo, ver `_legenda_anexo` em relatorios_docx.py)."""
    corpo = _texto(campos, "ufed:Body", "ufed:Snippet", limite=100_000)
    legenda: str | None = None
    valores = [str(v).strip() for v in C.valores(campos, "Message-Body") if str(v).strip()]
    if not valores:
        return corpo, legenda
    primeiro = valores[0]
    if primeiro.startswith("! "):
        legenda = valores[1][:3000] if len(valores) > 1 else None
    elif corpo is None:
        corpo = primeiro[:100_000]
    return corpo, legenda


def _sha256_do_anexo(campos: dict) -> str | None:
    """SHA-256 do binário vinculado à mensagem (`linkedItems` do WhatsAppParser: `sha-256:...`).
    É a chave que liga a mensagem ao item de mídia do índice, onde está a transcrição."""
    bruto = C.valor(campos, "linkedItems")
    if bruto and str(bruto).strip().lower().startswith("sha-256:"):
        return str(bruto).strip()[len("sha-256:"):].strip().upper() or None
    return None


def mensagem_de_item(campos: dict) -> dict:
    """Item de mensagem -> dict no formato que `_linha_mensagem` consome."""
    coord = C.coordenada(campos)
    datas = C.datas_do_item(campos)
    corpo, legenda = _corpo_e_legenda(campos)
    return {
        "corpo": corpo,
        "timestamp": (C.data_de(campos, "Communication:Date", "ufed:TimeStamp")
                      or datas["criacao"] or datas["modificacao"]),
        "tipo": _texto(campos, "ufed:MessageType", "mediaMime", limite=30),
        "status": _texto(campos, "messageStatus", "ufed:Status", limite=20),
        "pasta": _texto(campos, "ufed:Folder", limite=20),
        "identificador_original": _texto(campos, "ufed:id", "Message:Raw-Header:Message-Id",
                                         limite=300),
        "latitude": coord[0] if coord else None,
        "longitude": coord[1] if coord else None,
        # `IpedDeletedEmbeddedItem` é como o parser marca mensagem apagada recuperada do
        # banco do aplicativo — não é o mesmo que `deleted` (arquivo do sistema de arquivos)
        "estado": "Deleted" if (C.booleano(campos, "IpedDeletedEmbeddedItem")
                                or C.booleano(campos, "deleted")) else "Intact",
        "encaminhada": None,
        "apagada_remetente": C.booleano(campos, "IpedDeletedEmbeddedItem") or None,
        "temporaria": None,
        "editada": None,
        "rotulos": None,
        "identificador_citado": None,
        "destinatarios": C.juntar(campos, "Message-To", "Communication:To",
                                  "Message-Recipient-Address"),
        "assunto": _texto(campos, "Message-Subject", "ufed:Subject"),
        "motivo_exclusao": None,
        "smsc": _texto(campos, "ufed:SMSC", limite=100),
        "entregue": None,
        "lida": None,
        "reproduzida": None,
        # não vai para MSG_TB04; usados por quem monta a conversa
        "_remetente": _remetente_da_mensagem(campos),
        "_destinatarios": _destinatarios_da_mensagem(campos),
        "_grupo": C.booleano(campos, "isGroupMessage"),
        "_anexos": _anexos_da_mensagem(campos, legenda),
    }


def _anexos_da_mensagem(campos: dict, legenda: str | None = None) -> list[dict]:
    """Anexo declarado no próprio item da mensagem (`mediaName`/`mediaMime`/`mediaSize`, que
    o WhatsAppParser preenche). O binário fica noutro item do índice; a ligação é feita
    depois pelo hash (`linkedItems`).

    O anexo nasce também quando só há `mediaMime` e/ou legenda, sem `mediaName`: é a mídia que
    o IPED decodificou mas cujo binário não localizou — sem o anexo, a mensagem perderia a
    legenda e o tipo, e viraria só "! IMAGE_MESSAGE" (ou nada)."""
    nome = _texto(campos, "mediaName", "ufed:attachment_extracted_path", limite=500)
    tipo_conteudo = _texto(campos, "mediaMime", limite=200)
    titulo = _texto(campos, "ufed:Title", limite=500) or legenda
    if not nome and not tipo_conteudo and not titulo:
        return []
    return [{
        "nome_arquivo": nome,
        "titulo": titulo,
        "tipo_conteudo": tipo_conteudo,
        "bytes": C.inteiro(campos, "mediaSize"),
        "url": _texto(campos, "url", limite=2000),
        "caminho_zip": None,
        "file_id": _texto(campos, "ufed:file_id", limite=80),
        "transcricao": None,
        "_sha256": _sha256_do_anexo(campos),
    }]


def chat_de_item(campos: dict) -> dict:
    """Item de chat -> dict no formato que `_gravar_conversa_msg` consome (sem `mensagens`,
    que são anexadas na finalização).

    O caso de celular (UFED) é servido por DOIS parsers, e cada um grava a conversa com um
    conjunto de campos próprio: o `UfedChatParser` escreve `Conversation:*`/`ufed:*` (o chat
    decodificado do `_DecodedData/Chat`) e o `WhatsAppParser` escreve `Communication:*`
    (o chat extraído do `ChatStorage.sqlite`). Por isso o `chat_id` tenta as duas famílias, e a
    data vem dos campos `ufed:StartTime`/`ufed:LastActivity` antes do par `timeStamp`×
    `timeEvent`, que o `UfedChatParser` grava mas o `datas_do_item` não reconhecia."""
    participantes = _participantes(campos, "Conversation:Participants", "Communication:Participants")
    # O dono do aparelho vem explícito no UFED (`ufed:PhoneOwner`/`Conversation:Account`). Marcá-lo
    # no participante é o que deixa `_gravar_conversa_msg` saber qual remetente é o investigado e,
    # a partir daí, preencher o IN_ENVIADA de cada mensagem.
    dono = _identificador(
        C.valor(campos, "ufed:PhoneOwner:id") or C.valor(campos, "Conversation:Account:id")
        or C.valor(campos, "ufed:PhoneOwner") or C.valor(campos, "Conversation:Account"))
    if dono:
        for p in participantes:
            if p.get("jid") == dono:
                p["dono_dispositivo"] = True
                break
    datas = C.datas_do_item(campos)
    return {
        "chat_id": _texto(campos, "Conversation:id", "ufed:ChatId", "ufed:id", "GroupID",
                          "html:chatId", limite=300),
        "nome_grupo": _texto(campos, "Conversation:Name", "common:dc:title", "ufed:Name",
                             "name", limite=500),
        "tipo_chat": _texto(campos, "Conversation:Type", "ufed:ChatType", limite=50),
        "descricao": _texto(campos, "ufed:Description"),
        "conta": _texto(campos, "Conversation:Account", "userAccount", limite=300),
        "status_chat": None,
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
        "inicio": C.data_de(campos, "ufed:StartTime") or datas["criacao"],
        "ultima_atividade": C.data_de(campos, "ufed:LastActivity") or datas["modificacao"],
        "participantes": participantes,
        "mensagens": [],
    }


def contato_de_item(campos: dict) -> dict | None:
    """Item de contato -> dict de `_gravar_contato_agenda`, usando os campos que o IPED já
    decodificou.

    Achado que motivou isto: no caso iCloud do repo o VCardParser do IPED quebrou a agenda em
    554 itens, um contato cada, com `userName`/`phoneNumber`/`userOrganization` prontos no
    índice — e a importação baixava o blob de cada um do storage para reparsear o vCard.
    Quando o item não tem nenhum campo semântico (caso em que o .vcf inteiro é um item só),
    devolve None e quem chama cai no parser de vCard sobre o blob.

    Contato "Unknown" (Recents): o UFDR não decodificou nome nem telefone e gravou o número no
    próprio `name` ("Contact-Unknown-[01573991224014]") — sem este desvio, o contato era
    descartado inteiro (não há blob para reparsear nesses itens)."""
    nome = _texto(campos, "userName", "ufed:Name", limite=500)
    telefones = C.valores(campos, "phoneNumber") + C.valores(campos, "ufed:PhoneNumber")
    emails = C.valores(campos, "emailAddress") + C.valores(campos, "ufed:EmailAddress")
    if not (nome or telefones or emails):
        item_name = (_texto(campos, "name") or "")
        m = re.match(r"^Contact-Unknown-\[([\d\s()+-]+)\]$", item_name)
        if m:
            digitos = re.sub(r"\D", "", m.group(1))
            if len(digitos) >= 10:
                telefones = [digitos]
        if not (nome or telefones or emails):
            return None
    datas = C.datas_do_item(campos)
    entradas = []
    for tel in telefones:
        v = (tel or "").strip()
        if v:
            entradas.append({"tipo": "Telefone", "categoria": None, "valor": v[:300],
                             "dominio": None})
    for mail in emails:
        v = (mail or "").strip()
        if v:
            entradas.append({"tipo": "E-mail", "categoria": None, "valor": v[:300],
                             "dominio": v.partition("@")[2][:200] or None})
    for url in C.valores(campos, "userUrls"):
        v = (url or "").strip()
        if v:
            entradas.append({"tipo": "URL", "categoria": None, "valor": v[:300], "dominio": None})
    return {
        "nome": nome or (entradas[0]["valor"] if entradas else None),
        "aplicativo_origem": app_do_item(campos),
        "grupo": None,
        "tipo": None,
        "criacao": datas["criacao"],
        "modificacao": datas["modificacao"],
        "notas": _texto(campos, "userNotes"),
        "endereco": _texto(campos, "userAddress"),
        "organizacao": _texto(campos, "userOrganization"),
        "conta": _texto(campos, "userAccount", "contactOfAccount", limite=300),
        "nascimento": _texto(campos, "userBirthday", limite=100),
        "info_adicional": None,
        "caminho_foto": None,
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
        "entradas": entradas,
    }


def ligacao_de_item(campos: dict) -> dict:
    datas = C.datas_do_item(campos)
    direcao = _texto(campos, "Communication:Direction", "ufed:Direction", limite=30)
    origem = C.valor(campos, "Message-From") or C.valor(campos, "Communication:From")
    destino = C.valor(campos, "Message-To") or C.valor(campos, "Communication:To")
    # O "outro" da ligação é quem não é o dono do aparelho: na entrada é quem originou
    # (Communication:From), na saída é o destino (Communication:To) — quando o campo To não
    # vem (comum no UfedXmlReader, que só grava From), cai em From.
    if (direcao or "").lower().startswith("out"):
        p = _participante(destino) or {}
    else:
        p = _participante(origem) or {}
    return {
        "aplicativo": app_do_item(campos),
        "direcao": direcao,
        "status": _texto(campos, "ufed:Status", "messageStatus", limite=50),
        "timestamp": (C.data_de(campos, "Communication:Date", "ufed:TimeStamp")
                      or datas["criacao"]),
        # o WhatsAppParser grava a duração já formatada como MM:SS; o UFED grava em segundos
        "duracao_segundos": _duracao_segundos(C.valor_variantes(campos, "duration", "ufed:Duration")),
        "videochamada": _video(campos),
        "telefone": (p.get("jid") or "")[:300] or None,
        "nome_contato": p.get("nome"),
        "desconexao": None,
        "conta": _texto(campos, "userAccount", limite=300),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def _duracao_segundos(bruto) -> int | None:
    """Aceita "125", "02:05" e "01:02:05" — o WhatsAppParser grava MM:SS, o UFED grava
    segundos, e casos antigos gravam HH:MM:SS."""
    if bruto in (None, ""):
        return None
    texto = str(bruto).strip()
    if ":" in texto:
        partes = texto.split(":")
        try:
            valores = [int(p) for p in partes]
        except ValueError:
            return None
        total = 0
        for v in valores:
            total = total * 60 + v
        return total
    try:
        return int(float(texto))
    except ValueError:
        return None


def _video(campos: dict) -> bool | None:
    if C.booleano(campos, "ufed:VideoCall"):
        return True
    alvo = " ".join(C.valores(campos, "ufed:Type") + C.valores(campos, "ufed:MessageType")).lower()
    if not alvo:
        return None
    return "video" in alvo or None


def localizacao_de_item(campos: dict) -> dict | None:
    coord = C.coordenada(campos)
    datas = C.datas_do_item(campos)
    # O UfedXmlReader monta o lugar por PARTES (Name/City/State/Country) e quase nunca grava
    # coordenada — exigir lat/long (como antes) descartava a localização inteira, com o nome e
    # o endereço de que o investigador precisa.
    nome = _texto(campos, "ufed:Name")
    endereco = _texto(campos, "ufed:Address", "ufed:Street")
    if not endereco:
        endereco = ", ".join(p for p in (_texto(campos, "ufed:City"), _texto(campos, "ufed:State"),
                                         _texto(campos, "ufed:Country")) if p) or None
    if coord is None and not (nome or endereco):
        return None
    return {
        "aplicativo": app_do_item(campos),
        "timestamp": (C.data_de(campos, "ufed:TimeStamp", "Communication:Date")
                      or datas["criacao"] or datas["modificacao"]),
        "latitude": coord[0] if coord else None,
        "longitude": coord[1] if coord else None,
        "endereco": endereco,
        "categoria": _texto(campos, "ufed:Category", limite=300),
        "descricao": _texto(campos, "ufed:Description", "ufed:Name", "name"),
        "precisao": None,
        "elevacao": None,
        "tipo": _texto(campos, "ufed:Type", limite=100),
        "origem": _texto(campos, "ufed:Source", limite=300) or "IPED",
        "fim": C.data_de(campos, "ufed:EndTime"),
        "conta": _texto(campos, "userAccount", limite=300),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def nota_de_item(campos: dict) -> dict:
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": app_do_item(campos),
        "titulo": _texto(campos, "ufed:Title", "Message-Subject", "name"),
        "corpo": _texto(campos, "ufed:Body", "Message-Body", limite=100_000),
        "criacao": datas["criacao"],
        "modificacao": datas["modificacao"],
        "status": "Deleted" if C.booleano(campos, "deleted") else "Intact",
        "anexos": None,
        "resumo": None,
        "pasta": _texto(campos, "ufed:Folder", limite=300),
        "conta": _texto(campos, "userAccount", limite=300),
    }


def evento_de_item(campos: dict) -> dict:
    datas = C.datas_do_item(campos)
    return {
        "inicio": C.data_de(campos, "ufed:StartDate", "ufed:TimeStamp") or datas["criacao"],
        "fim": C.data_de(campos, "ufed:EndDate"),
        "criacao": datas["criacao"],
        "alteracao": datas["modificacao"],
        "titulo": _texto(campos, "ufed:Subject", "Message-Subject", "name"),
        "local": _texto(campos, "ufed:Location"),
        "descricao": _texto(campos, "ufed:Details", "Message-Body", limite=10_000),
        "organizador": _texto(campos, "ufed:Organizer"),
        "participantes": C.juntar(campos, "ufed:Attendees", "Communication:Participants"),
        "fonte": "IPED",
    }


def conta_de_item(campos: dict) -> dict | None:
    # O `name` (nome do arquivo) NÃO serve de usuário: num caso iCloud real, a categoria
    # "User Accounts" pegava arquivos SQLite (graphstore_stash.db, storage.sqlitedb…) e o
    # fallback para `name` os transformava em "contas de usuário" cujo titular era o nome do
    # arquivo. Devolver None aqui manda o item pro caminho de arquivo genérico, que é o certo.
    usuario = _texto(campos, "userAccount", "ufed:Username", "phoneNumber", limite=300)
    if not usuario:
        return None
    datas = C.datas_do_item(campos)
    return {
        "fonte": "IPED",
        "servico": _texto(campos, "accountType", "ufed:ServiceType", "ufed:Source", limite=300)
                   or app_do_item(campos),
        "usuario": usuario,
        "nome": _texto(campos, "userName", "ufed:Name", limite=300),
        "criado_em": datas["criacao"],
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def financeiro_de_item(campos: dict) -> dict | None:
    instituicao = _texto(campos, "ufed:Institution", "ufed:ServiceType", limite=300)
    titular = _texto(campos, "userName", "ufed:Name", "ufed:AccountHolder", limite=300)
    numero_conta = _texto(campos, "ufed:AccountNumber", "ufed:CardNumber", limite=300)
    # item sem nenhum dado real de conta (o UfedXmlReader emite FinancialAccount vazio):
    # devolver None manda pro caminho de arquivo em vez de gravar linha oca
    if not (instituicao or titular or numero_conta):
        return None
    return {
        "fonte": "IPED",
        "instituicao": instituicao,
        "titular": titular,
        "numero_conta": numero_conta,
        "agencia": _texto(campos, "ufed:BranchNumber", limite=300),
        "tipo_conta": _texto(campos, "ufed:AccountType", limite=300),
        "moeda": _texto(campos, "ufed:Currency", limite=20),
        "saldo": None,
        "tipo_modelo": _mime(campos)[:300] or None,
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def senha_de_item(campos: dict) -> dict:
    return {
        "fonte": "IPED",
        "servico": _texto(campos, "ufed:Service", "ufed:Source", "url", "name", limite=300),
        "usuario": _texto(campos, "ufed:Username", "userAccount", limite=300),
        "dados": _texto(campos, "ufed:Password", "ufed:Data", "Message-Body"),
        "tipo_modelo": _mime(campos)[:300] or None,
        "tipo_senha": _texto(campos, "ufed:PasswordType", limite=300),
        "grupo_acesso": _texto(campos, "ufed:AccessGroup", limite=300),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def autofill_de_item(campos: dict) -> dict:
    datas = C.datas_do_item(campos)
    return {
        "fonte": "IPED",
        "chave": _texto(campos, "ufed:Field", "ufed:Name", "name", limite=300),
        "valor": _texto(campos, "ufed:Value", "Message-Body"),
        "tipo_modelo": _mime(campos)[:300] or None,
        "registro": datas["criacao"],
        "ultimo_uso": datas["modificacao"],
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def rede_de_item(campos: dict) -> dict:
    coord = C.coordenada(campos)
    datas = C.datas_do_item(campos)
    # o UfedXmlReader grava o SSID como `ufed:SSId` (com o 'd' minúsculo no fim — conferido
    # num caso real: 703 de 703 redes só tinham essa grafia). Sem isso, a tela de Wi-Fi
    # mostrava o nome interno "WirelessNetwork_<uuid>" no lugar da rede.
    return {
        "ssid": _texto(campos, "ufed:SSID", "ufed:SSId", "ufed:Name"),
        "bssid": _texto(campos, "ufed:BSSID", "ufed:MACAddress", limite=300),
        "seguranca": _texto(campos, "ufed:SecurityMode", limite=300),
        "senha": _texto(campos, "ufed:Password", limite=300),
        "qt_conexoes": C.inteiro(campos, "ufed:TimesJoined"),
        "primeira_conexao": datas["criacao"],
        "ultima_conexao": C.data_de(campos, "ufed:LastConnection") or datas["modificacao"],
        "canal": C.inteiro(campos, "ufed:Channel"),
        "sinal": C.inteiro(campos, "ufed:SignalStrength"),
        "rede_oculta": None,
        "autenticacao": _texto(campos, "ufed:AuthenticationType", limite=300),
        "fabricante": None,
        "favorita": None,
        "fonte": "IPED",
        "latitude": coord[0] if coord else None,
        "longitude": coord[1] if coord else None,
        "tipo_conexao": _texto(campos, "ufed:Type", limite=300),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def aplicativo_de_item(campos: dict) -> dict:
    datas = C.datas_do_item(campos)
    # AppsUsageLog (Biome/KnowledgeC) quase nunca traz `ufed:Name`: o nome legível está no
    # pacote (`ufed:Identifier`). Usar o `name` do item ("AppsUsageLog_<uuid>") como último
    # recurso enchia a tela de aplicativos com UUIDs — vale mais o pacote do que o UUID.
    return {
        "pacote": _texto(campos, "ufed:Identifier", "ufed:PackageName", limite=300),
        "nome": _texto(campos, "ufed:Name", "ufed:Identifier", "ufed:PackageName"),
        "versao": _texto(campos, "ufed:Version", limite=300),
        "descricao": _texto(campos, "ufed:Description"),
        "instalacao": C.data_de(campos, "ufed:InstallDate") or datas["criacao"],
        "ultimo_uso": C.data_de(campos, "ufed:LastLaunch") or datas["modificacao"],
        "categorias": _texto(campos, "ufed:Categories"),
        "permissoes": _texto(campos, "ufed:Permissions"),
        "diretorios": _texto(campos, "ufed:Path", "path"),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def navegacao_de_item(campos: dict) -> dict | None:
    url = _texto(campos, "url", "ufed:URL", "ufed:Url", "ufed:Source", limite=4000)
    if not url:
        return None
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": app_do_item(campos),
        "titulo": _texto(campos, "ufed:Title", "common:dc:title", "name", limite=2000),
        "url": url,
        "timestamp": (C.data_de(campos, "visitDate", "ufed:TimeStamp") or datas["criacao"]
                      or datas["modificacao"]),
        "qt_visitas": C.inteiro(campos, "ufed:VisitCount"),
        "conta": _texto(campos, "userAccount", limite=300),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def busca_de_item(campos: dict) -> dict | None:
    """Item de busca (SearchedItem do UFDR): o TERMO digitado vive em `ufed:Value` — não há
    URL. Vai para BSC_TB01_BUSCAS (mesmo destino do SearchedItem do UFDR), não para navegação."""
    termo = _texto(campos, "ufed:Value", "ufed:SearchText", limite=2000)
    if not termo:
        return None
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "origem": _texto(campos, "ufed:Origin", limite=300),
        "termo": termo,
        "timestamp": C.data_de(campos, "ufed:TimeStamp") or datas["criacao"],
        "conta": _texto(campos, "ufed:Account", limite=300),
        "resultados": None,
        "latitude": None,
        "longitude": None,
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def social_de_item(campos: dict) -> dict:
    """Atividade de rede social (SocialMediaActivity do UFDR) → RDS_TB01_ATIVIDADE_SOCIAL, o
    mesmo destino do parser do UFDR."""
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "plataforma": None,
        "tipo_atividade": _texto(campos, "ufed:ActivityType", limite=300),
        "titulo": _texto(campos, "ufed:Title", limite=2000),
        "corpo": _texto(campos, "ufed:Description", "ufed:Value", limite=4000),
        "url": _texto(campos, "ufed:URL", limite=4000),
        "timestamp": C.data_de(campos, "ufed:TimeStamp") or datas["criacao"],
        "autor_nome": None,
        "autor_id": None,
        "conta": _texto(campos, "ufed:Account", limite=300),
        "situacao": _texto(campos, "ufed:Status", limite=100),
        "canal": None,
        "privacidade": None,
        "reacoes": None,
        "compartilhamentos": None,
        "comentarios": None,
        "latitude": None,
        "longitude": None,
        "marcados": None,
        "anexos": None,
        "post_original": None,
        "do_log_atividade": None,
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def conectividade_de_item(campos: dict) -> dict:
    """Conexão de dispositivo (DeviceConnectivity do UFDR) → CNX_TB01_CONEXOES."""
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "metodo": _texto(campos, "ufed:ConnectivityMethod", "ufed:NWConnectionType",
                         limite=300),
        "natureza": _texto(campos, "ufed:ConnectivityNature", limite=300),
        "tipo_dispositivo": _texto(campos, "ufed:DeviceType", limite=300),
        "nome_dispositivo": _texto(campos, "ufed:DeviceName", limite=300),
        "mac": _texto(campos, "ufed:MACAddress", limite=300),
        "timestamp": C.data_de(campos, "ufed:StartTime") or datas["criacao"],
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def cookie_de_item(campos: dict) -> dict:
    """Cookie (modelo Cookie do UFDR) → COO_TB01_COOKIES."""
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "nome": _texto(campos, "ufed:Name", limite=500),
        "dominio": _texto(campos, "ufed:Domain", limite=500),
        "caminho": _texto(campos, "ufed:Path", limite=1000),
        "valor": _texto(campos, "ufed:Value", limite=4000),
        "app_relacionado": _texto(campos, "ufed:RelatedApplication", limite=300),
        "criacao": C.data_de(campos, "ufed:CreationTime") or datas["criacao"],
        "ultimo_acesso": C.data_de(campos, "ufed:LastAccessTime") or datas["modificacao"],
        "expiracao": C.data_de(campos, "ufed:Expiry"),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def voicemail_de_item(campos: dict) -> dict:
    """Recado de caixa postal → VMS_TB01_VOICEMAIL."""
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "telefone": _texto(campos, "ufed:PhoneNumber", "ufed:FromNumber", limite=300),
        "nome_contato": _texto(campos, "ufed:ContactName", limite=300),
        "timestamp": C.data_de(campos, "ufed:StartTime", "ufed:TimeStamp") or datas["criacao"],
        "modificacao": datas["modificacao"],
        "duracao_segundos": _duracao_segundos(C.valor_variantes(campos, "ufed:Duration")),
        "ouvida": None,
        "tipo": _texto(campos, "ufed:Type", limite=300),
        "transcricao": _texto(campos, "ufed:Transcript", limite=4000),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


def gravacao_de_item(campos: dict) -> dict:
    """Gravação de tela/áudio (modelo Recording do UFDR) → GRV_TB01_GRAVACOES."""
    datas = C.datas_do_item(campos)
    return {
        "aplicativo": _texto(campos, "ufed:Source", limite=300) or app_do_item(campos),
        "titulo": _texto(campos, "ufed:Name", "ufed:Title", limite=500),
        "tipo": _texto(campos, "ufed:Type", limite=300),
        "timestamp": C.data_de(campos, "ufed:TimeStamp", "ufed:StartTime") or datas["criacao"],
        "duracao_segundos": _duracao_segundos(C.valor_variantes(campos, "ufed:Duration")),
        "estado": "Deleted" if C.booleano(campos, "deleted") else "Intact",
    }


# ---------------------------------------------------------------------------
# Roteador
# ---------------------------------------------------------------------------

# Adapters para _gravar_dominio_novo: o contrato dela é (cur, tipo, id_extracao, dados), e o
# Roteador chama (cur, id_extracao, dados) — o tipo fica fechado no nome da função.
def _gravar_dominio_busca(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "busca", id_extracao, dados)


def _gravar_dominio_social(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "atividade_social", id_extracao, dados)


def _gravar_dominio_conectividade(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "conexao_dispositivo", id_extracao, dados)


def _gravar_dominio_cookie(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "cookie", id_extracao, dados)


def _gravar_dominio_voicemail(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "voicemail", id_extracao, dados)


def _gravar_dominio_gravacao(cur, id_extracao, dados):
    _gravar_dominio_novo(cur, "gravacao", id_extracao, dados)


# destino -> (função de gravação, chave do contador em IPD_TB01_EXTRACOES)
_SIMPLES = {
    "ligacoes":     (_gravar_ligacao, "ligacoes"),
    "localizacoes": (_gravar_localizacao, "localizacoes"),
    "notas":        (_gravar_nota, "notas"),
    "eventos":      (_gravar_evento_conta, "eventos"),
    "contas":       (_gravar_conta_usuario, "contas_usuario"),
    "financeiro":   (_gravar_conta_financeira, "contas_financeiras"),
    "senhas":       (_gravar_senha, "senhas"),
    "autofill":     (_gravar_autofill, "autofill"),
    "redes":        (_gravar_rede_wifi, "redes"),
    "apps":         (_gravar_aplicativo, "apps"),
    "navegacao":    (_gravar_pagina_visitada, "navegacao"),
    # domínios que o UFDR já importava pela tabela genérica (_DOMINIOS_NOVOS em
    # investigacao_utils.py) e o IPED decodifica igual — mesma tabela, mesma tela
    # (social_buscas.html, conexões em wifi.html, cookies em navegacao.html, agenda etc.)
    "busca":        (_gravar_dominio_busca, "buscas"),
    "social":       (_gravar_dominio_social, "social"),
    "conectividade": (_gravar_dominio_conectividade, "conectividade"),
    "cookie":       (_gravar_dominio_cookie, "cookies"),
    "voicemail":    (_gravar_dominio_voicemail, "voicemail"),
    "gravacao":     (_gravar_dominio_gravacao, "gravacoes"),
}

_CONVERSORES = {
    "ligacoes": ligacao_de_item,
    "localizacoes": localizacao_de_item,
    "notas": nota_de_item,
    "eventos": evento_de_item,
    "contas": conta_de_item,
    "financeiro": financeiro_de_item,
    "senhas": senha_de_item,
    "autofill": autofill_de_item,
    "redes": rede_de_item,
    "apps": aplicativo_de_item,
    "navegacao": navegacao_de_item,
    "busca": busca_de_item,
    "social": social_de_item,
    "conectividade": conectividade_de_item,
    "cookie": cookie_de_item,
    "voicemail": voicemail_de_item,
    "gravacao": gravacao_de_item,
}


def classificar(campos: dict) -> str | None:
    """Domínio do item, ou None quando o item não é de nenhum domínio decodificado (o caso da
    esmagadora maioria dos itens de um HD ou de uma nuvem: são arquivos mesmo).

    O MIME decide primeiro porque é o parser que o escreve; a categoria é o desempate."""
    mime = _mime(campos)
    if mime in MIME_CHAT:
        return "chat"
    if mime in MIME_MENSAGEM:
        return "mensagem"
    if mime in MIME_ANEXO_MENSAGEM:
        return "anexo_mensagem"
    if mime in MIME_LIGACAO:
        return "ligacoes"
    if mime in MIME_CONTATO:
        return "contato"
    if mime in MIME_CONTA:
        return "contas"
    if mime in MIME_LOCALIZACAO:
        return "localizacoes"
    if mime in MIME_APP:
        return "apps"
    if mime in MIME_PREVIEW_CHAT:
        return None  # prévia HTML do chat, não a conversa — vira arquivo de verdade
    if mime in MIME_BUSCA:
        return "busca"
    if mime in MIME_SOCIAL:
        return "social"
    if mime in MIME_CONECTIVIDADE:
        return "conectividade"
    if mime in MIME_COOKIE:
        return "cookie"
    if mime in MIME_VOICEMAIL:
        return "voicemail"
    if mime in MIME_GRAVACAO:
        return "gravacao"

    cats = _categorias(campos)
    if not cats:
        return None
    # ordem importa: "Chats" e "Calls" compartilham nomes de aplicativo como folha, e um item
    # de ligação do WhatsApp tem categoria "WhatsApp Calls", não "WhatsApp"
    if cats & CAT_IGNORADOS:
        return "ignorado"
    if cats & CAT_LIGACOES:
        return "ligacoes"
    if cats & CAT_ANEXOS_MENSAGEM:
        return "anexo_mensagem"
    if cats & CAT_MENSAGENS:
        return "mensagem"
    if cats & CAT_CHATS:
        return "chat"
    if cats & CAT_CONTATOS:
        return "contato"
    if cats & CAT_LOCALIZACOES:
        return "localizacoes"
    if cats & CAT_NOTAS:
        return "notas"
    if cats & CAT_EVENTOS:
        return "eventos"
    if cats & CAT_CONTAS:
        return "contas"
    if cats & CAT_SENHAS:
        return "senhas"
    if cats & CAT_AUTOFILL:
        return "autofill"
    if cats & CAT_REDES:
        return "redes"
    if cats & CAT_APPS:
        return "apps"
    if cats & CAT_BUSCAS:
        return "busca"
    if cats & CAT_NAVEGACAO:
        return "navegacao"
    if cats & CAT_SOCIAL:
        return "social"
    if cats & CAT_CONECTIVIDADE:
        return "conectividade"
    if cats & CAT_COOKIES:
        return "cookie"
    if cats & CAT_VOICEMAIL:
        return "voicemail"
    if cats & CAT_GRAVACOES:
        return "gravacao"
    if cats & CAT_FINANCEIRO:
        return "financeiro"
    return None


class Roteador:
    """Consome os itens de domínio de um caso e devolve as contagens no fim.

    Uso, dentro do laço de importação:

        roteador = Roteador(id_extracao)
        try:
            for campos in itens:
                if roteador.consumir(cur, campos, doc):
                    continue          # o item virou registro de domínio
                ...                   # segue para ARQ_TB01 como arquivo
            roteador.finalizar(cur)
        finally:
            roteador.fechar()

    `consumir` devolve o NOME do domínio quando o item foi tratado, e None quando o item não é
    de domínio nenhum. Quem chama decide se o item vai TAMBÉM para ARQ_TB01 a partir desse
    nome — ver `_DOMINIOS_SEM_ARQUIVO` e `_tem_binario` em iped_utils, que é onde essa regra
    mora (é decisão da importação, não do roteamento)."""

    def __init__(self, id_extracao: int):
        self.id_extracao = id_extracao
        self.contagens: dict[str, int] = {}
        self._chats: dict[str, dict] = {}     # id do item de chat -> dict do chat
        self._buffer = _BufferMensagens()
        self._ordem = 0
        self._donos: dict[str, str] = {}      # app -> identificador do dono do aparelho
        self._transcricoes: dict[str, str] = {}  # sha-256 (maiúsculo) -> transcrição do áudio
        # Anexo do UFDR (application/x-ufed-attachment) é item FILHO da mensagem, não do chat:
        # fica acumulado aqui por id da mensagem pai e é pendurado nela em _gravar_um. Já o
        # anexo do WhatsAppParser É a própria mensagem (message/x-*-attachment) e segue o
        # fluxo de mensagem normal.
        self._anexos_de_mensagem: dict[str, list[dict]] = {}
        self._ids_de_mensagem: set[str] = set()  # ids de itens tratados como mensagem
        # binários do caso por sha-256 (maiúsculo): liga o anexo da mensagem ao ponteiro
        # "IPED::..." do item de mídia no índice — é o que faz o áudio/vídeo/foto da conversa
        # abrir na tela (ver registrar_binario e _gravar_um).
        self._binarios: dict[str, str] = {}

    @property
    def tem_conversas(self) -> bool:
        """True quando o caso rendeu chat ou mensagem — o que só acontece com fonte de celular.
        Quem chama usa para não anunciar uma fase "montando conversas" que não vai acontecer."""
        return bool(self._chats or self._buffer.total)

    def _contar(self, chave: str, n: int = 1) -> None:
        self.contagens[chave] = self.contagens.get(chave, 0) + n

    def consumir(self, cur, campos: dict) -> str | None:
        """Trata o item se ele for de algum domínio. Devolve o nome do domínio (para quem
        chama decidir sobre ARQ_TB01) ou None quando o item não é de domínio nenhum.

        Falha de conversão/gravação vira aviso e devolve None — o item cai de volta no
        caminho de arquivo genérico, que é sempre seguro."""
        destino = classificar(campos)
        if destino is None:
            return None
        try:
            return self._tratar(cur, destino, campos)
        except Exception:
            logger.warning("Nuvem IPED: falha ao rotear item %s como %s",
                           C.valor(campos, "id"), destino, exc_info=True)
            return None

    def _tratar(self, cur, destino: str, campos: dict) -> str | None:
        if destino == "chat":
            item_id = C.valor(campos, "id")
            if item_id is None:
                return None
            self._chats[str(item_id)] = {**chat_de_item(campos), "_app": app_do_item(campos)}
            return "chat"

        if destino == "anexo_mensagem":
            if _mime(campos) == "application/x-ufed-attachment":
                # Anexo do UFDR: item filho da MENSAGEM (o pai é o id da mensagem, não o do
                # chat). Antes virava uma "mensagem" vazia numa conversa sintética; agora é
                # pendurado na mensagem pai em _gravar_um — legenda (ufed:Title) incluída.
                pai = C.valor(campos, "parentId") or ""
                anexos = _anexos_da_mensagem(campos)
                if anexos and pai:
                    self._anexos_de_mensagem.setdefault(str(pai), []).extend(anexos)
                return "mensagem"
            # WhatsApp/Telegram: o item de anexo É a mensagem (o parser só troca o
            # INDEXER_CONTENT_TYPE quando há mídia), e descartá-lo perderia a mensagem
            # inteira, legenda incluída — cai no fluxo de mensagem abaixo.
            destino = "mensagem"

        if destino == "mensagem":
            self._ordem += 1
            item_id = C.valor(campos, "id") or ""
            if item_id:
                self._ids_de_mensagem.add(str(item_id))
            pai = C.valor(campos, "parentId") or C.valor(campos, "parentVirtualIdentifier") or ""
            mensagem = mensagem_de_item(campos)
            mensagem["_app"] = app_do_item(campos)
            mensagem["_item_id"] = str(item_id)
            self._buffer.guardar(str(pai), self._ordem, mensagem)
            return "mensagem"

        if destino == "contato":
            contato = contato_de_item(campos)
            if contato is None:
                # Item de contato que o IPED não decodificou em campos — o .vcf inteiro como
                # item único. Quem chama cai no parser de vCard sobre o blob; o rótulo é
                # próprio para o chamador não confundir com "não é de domínio nenhum".
                return "contato_sem_campos"
            _gravar_contato_agenda(cur, self.id_extracao, C.LinhaParcial(contato))
            self._contar("contatos")
            return "contato"

        if destino == "contas":
            self._capturar_dono(campos)

        gravar, chave = _SIMPLES[destino]
        linha = _CONVERSORES[destino](campos)
        if linha is None:
            return None
        # LinhaParcial e não dict: os `_gravar_*` nasceram do UFDR e leem cada campo por chave
        # obrigatória. Um item do IPED preenche só o que aquele item tem, e chave ausente
        # derrubaria a importação inteira em vez de gravar NULL na coluna.
        gravar(cur, self.id_extracao, C.LinhaParcial(linha))
        self._contar(chave)
        return destino

    # -- dono do aparelho e transcrição (registrados durante o laço) --------------

    def _capturar_dono(self, campos: dict) -> None:
        """Registra o dono do aparelho a partir do item de conta do mensageiro
        (`application/x-whatsapp-account`, `x-telegram-account`, …), que traz `userAccount`/
        `phoneNumber`.

        O `chat_de_item` marca o dono pelos campos do UFED (`ufed:PhoneOwner`/
        `Conversation:Account`), que só o UfedChatParser grava — os chats do WhatsAppParser
        (ChatStorage.sqlite) não têm esses campos, então as mensagens deles ficariam todas
        "recebidas". A conta é a única pista estável do dono nesse caminho."""
        app = app_do_item(campos)
        ident = _identificador(
            C.valor(campos, "userAccount") or C.valor(campos, "phoneNumber"))
        if ident and app not in self._donos:
            self._donos[app] = ident

    def registrar_transcricao(self, sha256: str, texto: str) -> None:
        """Registra a transcrição de um binário de áudio/vídeo, chaveada pelo SHA-256 — a
        mesma chave que `linkedItems` usa para ligar a mensagem ao seu binário."""
        chave = (sha256 or "").strip().upper()
        if chave and texto:
            self._transcricoes.setdefault(chave, texto)

    def registrar_binario(self, sha256: str | None, caminho_zip: str | None) -> None:
        """Registra o ponteiro "IPED::..." de um item de mídia do caso, chaveado pelo SHA-256.

        É a chave que o `linkedItems` das mensagens do WhatsAppParser usa para apontar o
        binário; em _gravar_um o anexo da mensagem ganha esse ponteiro e a mídia passa a abrir
        na tela de conversa (áudio/vídeo/foto), em vez de ficar "indisponível" com o binário
        existindo no storage do caso."""
        chave = (sha256 or "").strip().upper()
        if chave and caminho_zip and chave not in self._binarios:
            self._binarios[chave] = caminho_zip

    # -- finalização das conversas -------------------------------------------------

    # Conversas gravadas entre um commit e outro. Um aparelho rende centenas de milhares de
    # mensagens; deixar tudo numa transação só faria o Postgres segurar o WAL inteiro e um
    # erro no último chat desfazer os 4.000 anteriores.
    _CHATS_POR_COMMIT = 50

    def finalizar(self, cur, commit=None, progresso=None) -> dict:
        """Grava as conversas acumuladas e devolve as contagens. Chamar uma vez só, no fim da
        importação, antes de `fechar()`. Sem mensagem nenhuma no caso, não faz nada — é o que
        acontece num caso de HD ou de nuvem pura.

        `commit`, quando informado, é chamado a cada `_CHATS_POR_COMMIT` conversas gravadas.
        `progresso`, quando informado, recebe (feitas, total) para a barra do card não ficar
        parada em 99% enquanto um celular grande é montado."""
        if self._buffer.total == 0 and not self._chats:
            return self.contagens
        try:
            self._buffer.indexar()
            self._gravar_conversas(cur, commit, progresso)
        except Exception:
            logger.exception("Nuvem IPED: falha ao gravar as conversas do caso")
        return self.contagens

    def _gravar_conversas(self, cur, commit=None, progresso=None) -> None:
        pendentes = set(self._buffer.pais())
        # 2) mensagens cujo chat não apareceu no índice (chat não decodificado, item podado
        #    do caso, ou chat com id fora do padrão) — agrupadas por aplicativo, mesmo
        #    tratamento que o UFDR dá às mensagens sem Chat pai
        soltas: dict[str, list[dict]] = {}
        for pai in pendentes - set(self._chats):
            for msg in self._buffer.mensagens(pai):
                soltas.setdefault(msg.get("_app") or "IPED", []).append(msg)

        total = len(self._chats) + len(soltas)
        feitas = 0

        def _passo():
            nonlocal feitas
            feitas += 1
            if commit is not None and feitas % self._CHATS_POR_COMMIT == 0:
                commit()
            if progresso is not None:
                progresso(feitas, total)

        # 1) chats identificados, com as suas mensagens
        for item_id, chat in self._chats.items():
            self._gravar_um(cur, chat.get("_app") or "IPED", chat,
                            self._buffer.mensagens(item_id))
            _passo()
        for app, mensagens in soltas.items():
            self._gravar_um(cur, app, self._chat_sintetico(app, mensagens), mensagens)
            _passo()
        if commit is not None:
            commit()

    @staticmethod
    def _chat_sintetico(app: str, mensagens: list[dict]) -> dict:
        datas = [m["timestamp"] for m in mensagens if m.get("timestamp")]
        rotulo = f"(mensagens sem conversa) {app}"
        return {
            "chat_id": rotulo, "nome_grupo": rotulo, "tipo_chat": None, "descricao": None,
            "conta": None, "status_chat": None, "estado": None,
            "inicio": min(datas, default=None), "ultima_atividade": max(datas, default=None),
            "participantes": [], "mensagens": [],
        }

    def _gravar_um(self, cur, app: str, chat: dict, mensagens: list[dict]) -> None:
        """Monta o dict no formato de `_gravar_conversa_msg` e grava. As datas voltam do
        SQLite como string ISO (passaram por json.dumps) — `_data` as reconstrói."""
        # As mensagens chegam na ordem do índice (que o `_BufferMensagens` preserva por `ordem`),
        # e essa ordem NÃO é a cronológica — o IPED emite as mensagens fora de ordem quando há
        # backup mesclado ou registros apagados recuperados. Como a tela pagina por `id_mensagem`
        # (= ordem de inserção), reordenar aqui por timestamp devolve a cronologia de verdade.
        mensagens = sorted(mensagens, key=_chave_ordenacao)

        participantes = list(chat.get("participantes") or [])
        vistos = {p["jid"] for p in participantes if p.get("jid")}
        prontas = []
        for msg in mensagens:
            remetente = msg.get("_remetente")
            if remetente and remetente.get("jid") and remetente["jid"] not in vistos:
                participantes.append(remetente)
                vistos.add(remetente["jid"])
            for dest in msg.get("_destinatarios") or []:
                if dest.get("jid") and dest["jid"] not in vistos:
                    participantes.append(dest)
                    vistos.add(dest["jid"])
            anexos = []
            # anexos do UFDR pendurados nesta mensagem (item filho com parentId = id da
            # mensagem, ver _tratar) + os declarados no próprio item
            anexos_extra = self._anexos_de_mensagem.get(msg.get("_item_id") or "") or []
            for a in (msg.get("_anexos") or []) + anexos_extra:
                if not a.get("transcricao"):
                    a["transcricao"] = self._transcricoes.get(a.get("_sha256") or "")
                # sem binário declarado, tenta o ponteiro do item de mídia do caso (sha-256):
                # é o que faz o áudio/vídeo/foto da conversa abrir na tela, direto do storage
                if not a.get("caminho_zip"):
                    a["caminho_zip"] = self._binarios.get(a.get("_sha256") or "")
                anexos.append(C.LinhaParcial({k: v for k, v in a.items()
                                              if not k.startswith("_")}))
            prontas.append(C.LinhaParcial({
                **{k: v for k, v in msg.items() if not k.startswith("_")},
                "timestamp": _data(msg.get("timestamp")),
                "entregue": _data(msg.get("entregue")),
                "lida": _data(msg.get("lida")),
                "reproduzida": _data(msg.get("reproduzida")),
                "remetente": remetente,
                "anexos": anexos,
            }))
        completo = {
            **chat,
            "inicio": _data(chat.get("inicio")),
            "ultima_atividade": _data(chat.get("ultima_atividade")),
            "participantes": participantes,
            "mensagens": prontas,
        }
        completo.pop("_app", None)
        _gravar_conversa_msg(cur, self.id_extracao, app, completo,
                             jid_dono=self._donos.get(app))
        self._contar("conversas")
        self._contar("mensagens", len(prontas))

    def fechar(self) -> None:
        self._buffer.fechar()


def _data(bruto):
    """Reconstrói datetime a partir do que o json.dumps(default=str) gravou. Aceita já ser
    datetime (chat, que fica em memória) ou string ISO (mensagem, que passou pelo SQLite)."""
    if bruto is None or not isinstance(bruto, str):
        return bruto
    return C.parse_data(bruto) or C.parse_data(bruto.replace(" ", "T"))


def _chave_ordenacao(msg: dict) -> tuple[bool, datetime]:
    """Chave de ordenação cronológica estável: `(sem data?, data)`. Mensagem sem timestamp vai
    para o fim; as com o mesmo timestamp preservam a ordem relativa original (sorted é estável)."""
    t = _data(msg.get("timestamp"))
    return (t is None, t if isinstance(t, datetime) else datetime.max)
