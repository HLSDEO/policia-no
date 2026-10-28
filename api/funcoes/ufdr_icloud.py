"""Extração de nuvem Apple (iCloud Backup) embutida no .ufdr do Cellebrite.

Um .ufdr "ICLOUD_BACKUP" não traz NADA decodificado no report.xml (zero <model>): o Cellebrite
só empacotou os dados crus do iCloud. O conteúdo útil:

- agenda de contatos: vCards dentro de files/Archives/<id>-CONTACTS-*.zip;
- histórico de SMS/iMessage: files/Text/MessagesMetadata.txt (JSON CloudKit, com o corpo em
  msgProto base64→gzip→protobuf);
- mídia do WhatsApp: files/Archives/Media*.tar / Document.tar / Stickers.tar, com o caminho
  organizado por JID ("/Media/55279…@s.whatsapp.net/…") — é o que permite ligar cada mídia ao
  contato pelo número. O banco de conversas (ChatStorage.sqlite.enc) vem CRIPTOGRAFADO (.enc) e
  não é legível, mas a mídia não é criptografada.

As mensagens reaproveitam as tabelas de SMS (svc='SMS') e de mensageiros (iMessage, via
MSG_TB0x com tp_app='iMessage'), os contatos vão para CTT_TB0x e a mídia para ARQ_TB0x — as
MESMAS tabelas do UFDR. A mídia do WhatsApp vem do .tar (JID no caminho) porque a cópia que o
Cellebrite extrai em files/Image perde o JID e só traz imagens (não o áudio .opus).
"""

import logging
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

from funcoes import ufdr_zip
from funcoes.investigacao_utils import (
    _drenar_arquivos,
    _gravar_contato_agenda,
    _gravar_conversa,
    _gravar_conversa_msg,
    _selo_mensagem,
)
from funcoes.iped_campos import LinhaParcial
from funcoes.takeout_parsers import parse_messages_icloud, parse_vcf
from funcoes.ufdr_cache import obter_tar_aninhado, obter_zip_aninhado
from funcoes.ufdr_parser import categoria_de_extensao, mime_de_extensao

logger = logging.getLogger(__name__)

_PREFIXO = "files/Archives/"
# e-mail com TLD obrigatório: evita que o "-<id>" que o Cellebrite concatena no caminho
# (ex. "gauxin171@hotmail.com-429667") entre no domínio.
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")

# tars de mídia do WhatsApp que viram arquivo individual (com o JID no caminho). As miniaturas
# (Thumbnail*.tar) e os GIFs (vazios) ficam de fora: são derivados/redundantes.
_TARS_MIDIA = ("Media.tar", "Media_1.tar", "Document.tar", "Stickers.tar")


def tem_icloud(zf: zipfile.ZipFile) -> bool:
    """True quando o .ufdr é um backup do iCloud (Cellebrite), não um aparelho nem a nuvem Google.

    `files/Configuration/Backup.plist` NÃO é sinal de iCloud: o dump de um aparelho Apple físico
    também carrega um plist com esse nome em files/Configuration/ (a extração 45 da produção foi
    classificada como nuvem por causa disso — e com o flag errado o passe de arquivos pulou
    files/Image e files/Text, perdendo as ~800 mil mídias do aparelho). O que só o backup cru do
    iCloud tem é o payload que este módulo lê: os vCards em files/Archives/<id>-CONTACTS-*.zip e
    o histórico CloudKit em files/Text/MessagesMetadata.txt."""
    for i in zf.infolist():
        n = i.filename
        if n.startswith(_PREFIXO) and "-CONTACTS-" in n:
            return True
        if n.startswith("files/Text/") and n.endswith("MessagesMetadata.txt"):
            return True
    return False


def detectar_conta_icloud(caminho_ufdr) -> str | None:
    """O e-mail da conta do iCloud, lido do caminho interno do ZIP de contatos (ex.
    ".../gauxin171@hotmail.com-429667//Contacts/vCards/*.vcf"). None quando não acha."""
    with ufdr_zip.abrir(caminho_ufdr) as zf:
        alvos = [i.filename for i in zf.infolist()
                 if i.filename.startswith(_PREFIXO) and "-CONTACTS-" in i.filename]
    for nome in alvos:
        try:
            inner = obter_zip_aninhado(caminho_ufdr, nome)
        except Exception:
            continue
        for membro in inner.namelist():
            m = _RE_EMAIL.search(membro)
            if m:
                return m.group(0)
        return None
    return None


def _mensagem_icloud(corpo, timestamp, remetente, guid, apagada) -> dict:
    """Dict de mensagem completo, no formato que _linha_mensagem consome."""
    return {
        "remetente": remetente,
        "corpo": corpo,
        "timestamp": timestamp,
        "tipo": None,
        "status": None,
        "pasta": None,
        "identificador_original": guid,
        "anexos": [],
        "latitude": None,
        "longitude": None,
        "estado": "Deleted" if apagada else "Intact",
        "encaminhada": None,
        "apagada_remetente": None,
        "temporaria": None,
        "editada": None,
        "rotulos": None,
        "identificador_citado": None,
        "destinatarios": None,
        "assunto": None,
        "motivo_exclusao": None,
        "smsc": None,
        "entregue": None,
        "lida": None,
        "reproduzida": None,
    }


def _gravar_mensagens_icloud(cur, id_extracao, mensagens, contagens) -> None:
    """Agrupa as mensagens do Messages in iCloud por chatID e grava nas tabelas de SMS (svc=SMS)
    e de mensageiros (iMessage, tp_app='iMessage')."""
    por_chat = defaultdict(list)
    for msg in mensagens:
        por_chat[msg.get("chat_id") or msg.get("dc_id") or "(sem conversa)"].append(msg)

    for chat_id, msgs in por_chat.items():
        handle = chat_id.split(";-;", 1)[-1].strip() if ";-;" in chat_id else chat_id
        dc_id = next((m["dc_id"] for m in msgs if m.get("dc_id")), None)
        svc = next((m["svc"] for m in msgs if m.get("svc")), None)

        def _remetente(m):
            jid = m.get("sender") or dc_id
            return {"jid": jid, "nome": None} if jid else None

        # guias repetidos no mesmo MessagesMetadata.txt são a mesma mensagem regravada pelo
        # CloudKit — o selo (guid+data+corpo+remetente) casa com o filtro do report.xml
        vistas: set = set()
        mensagens_ok = []
        for m in msgs:
            msg = _mensagem_icloud(m["corpo"], m["timestamp"], _remetente(m),
                                   m.get("guid"), m.get("apagada"))
            selo = _selo_mensagem(msg)
            if msg["identificador_original"]:
                if selo in vistas:
                    continue
                vistas.add(selo)
            mensagens_ok.append(msg)

        if not mensagens_ok:
            continue

        participantes = []
        for jid, dono in ((handle, False), (dc_id, True)):
            if jid and jid not in {p["jid"] for p in participantes}:
                participantes.append({"jid": jid, "nome": None,
                                      "dono_dispositivo": dono, "admin_grupo": False})

        chat = {
            "chat_id": chat_id,
            "nome_grupo": None,
            "inicio": min((m["timestamp"] for m in mensagens_ok if m["timestamp"]), default=None),
            "ultima_atividade": max((m["timestamp"] for m in mensagens_ok if m["timestamp"]), default=None),
            "tipo_chat": "OneOnOne",
            "descricao": None,
            "conta": None,
            "status_chat": None,
            "estado": None,
            "participantes": participantes,
            "mensagens": mensagens_ok,
        }

        if svc == "SMS":
            _gravar_conversa(cur, "sms", id_extracao, chat, jid_dono=dc_id)
            contagens["sms_conversas"] += 1
            contagens["sms_mensagens"] += len(mensagens_ok)
        else:
            _gravar_conversa_msg(cur, id_extracao, "iMessage", chat, jid_dono=dc_id)
            contagens["msg_conversas"] += 1
            contagens["msg_mensagens"] += len(mensagens_ok)


def _ler_messages_metadata(zf, caminho_ufdr) -> str | None:
    """O conteúdo do MessagesMetadata.txt (SMS/iMessage). O Cellebrite o extrai em
    files/Text/MessagesMetadata.txt; fallback para o ZIP de origem se a extração não tiver."""
    alvo = "files/Text/MessagesMetadata.txt"
    try:
        return zf.read(alvo).decode("utf-8", "replace")
    except KeyError:
        pass
    for nome in ("files/Archives/MessagesMetadata.txt.zip",):
        try:
            zf.getinfo(nome)  # só a existência: KeyError abaixo segue o resto do ZIP
        except KeyError:
            continue
        try:
            inner = obter_zip_aninhado(caminho_ufdr, nome)
            return inner.read("MessagesMetadata.txt").decode("utf-8", "replace")
        except Exception:
            return None
    return None


def _linha_arquivo(id_extracao, membro_externo, caminho_interno, bytes_, mtime=None) -> tuple:
    """Tupla no formato do buffer de ARQ_TB01, com ponteiro de dois níveis
    `files/Archives/<tar>::<caminho_interno>`. O caminho interno preserva o JID do WhatsApp
    (ex. "/Media/55279…@s.whatsapp.net/…"), que é o que liga a mídia ao contato; o mtime do tar
    é a data/hora da mensagem original."""
    nome = caminho_interno.rsplit("/", 1)[-1] or caminho_interno
    dt_mod = None
    if mtime:
        try:
            dt_mod = datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            dt_mod = None
    return (
        id_extracao, nome[:2000], caminho_interno, categoria_de_extensao(nome),
        mime_de_extensao(nome), bytes_, dt_mod, None,
        f"{membro_externo}::{caminho_interno}",
        None, None, None, None, None, None, None, None, None, None,
    )


def _indexar_tar(cur, id_extracao, caminho_ufdr, membro_externo, buffer_arquivos, contagens) -> None:
    try:
        tf = obter_tar_aninhado(caminho_ufdr, membro_externo)
        membros = [m for m in tf.getmembers() if m.isfile()]
    except Exception:
        logger.warning("Extração %s: falha ao listar tar %s", id_extracao, membro_externo, exc_info=True)
        return
    for m in membros:
        buffer_arquivos.append(_linha_arquivo(id_extracao, membro_externo, m.name, m.size, m.mtime))
        contagens["arquivos"] = contagens.get("arquivos", 0) + 1


def processar_icloud(zf, caminho_ufdr, cur, conn, bat, id_extracao, contagens) -> bool:
    """Passe pelo backup do iCloud dentro do .ufdr: agenda (vCards), mensagens (SMS/iMessage) e
    a mídia do WhatsApp (tars com o JID no caminho). Devolve True se o usuário pediu cancelamento."""
    buffer_arquivos: list[tuple] = []
    for info in zf.infolist():
        nome = info.filename
        if not nome.startswith(_PREFIXO) or info.is_dir():
            continue
        base = nome.rsplit("/", 1)[-1]

        # --- contatos (vCards) ---
        if "-CONTACTS-" in base:
            try:
                inner = obter_zip_aninhado(caminho_ufdr, nome)
            except Exception:
                logger.warning("Extração %s: falha ao abrir %s", id_extracao, nome, exc_info=True)
                continue
            for membro in inner.namelist():
                if not membro.lower().endswith(".vcf"):
                    continue
                try:
                    texto = inner.read(membro).decode("utf-8", "replace")
                except Exception:
                    continue
                for contato in parse_vcf(texto, membro):
                    _gravar_contato_agenda(
                        cur, id_extracao,
                        LinhaParcial({**contato,
                                      "entradas": [LinhaParcial(e) for e in contato["entradas"]]}))
                    contagens["contatos"] = contagens.get("contatos", 0) + 1
            continue

        # --- mídia do WhatsApp (tar, com o JID no caminho) ---
        if base in _TARS_MIDIA:
            _indexar_tar(cur, id_extracao, caminho_ufdr, nome, buffer_arquivos, contagens)
            continue

        if bat.cancelar_pedido():
            break

    if buffer_arquivos:
        _drenar_arquivos(cur, buffer_arquivos)
        buffer_arquivos.clear()

    # --- mensagens SMS/iMessage ---
    texto = _ler_messages_metadata(zf, caminho_ufdr)
    if texto:
        mensagens = parse_messages_icloud(texto)
        _gravar_mensagens_icloud(cur, id_extracao, mensagens, contagens)
        conn.commit()

    return False
