"""Mídia do WhatsApp no backup do Google Drive — a "conversa-mídia" em linha do tempo.

O backup automático do WhatsApp no Drive (Takeout → Drive Mobile Backups → ``wa_<numero>/``)
preserva a mídia com o nome determinístico que o próprio WhatsApp dá ao arquivo —
``IMG-AAAAMMDD-WAnnnn.jpg``, ``AUD-…opus``, ``VID-….mp4`` — mas NÃO preserva a conversa:

- o banco de mensagens vai em ``Backups/*.crypt14``, criptografado sem chave no caso;
- os ``*_file_metadata.json`` ao lado de cada mídia são metadado de SINCRONIZAÇÃO do Drive
  (md5, tamanho, data de upload) — contato nenhum (verificado byte a byte num caso real);
- diferente do backup do iCloud (funcoes/ufdr_icloud.py), o caminho NO Drive não tem o JID
  do WhatsApp (``@s.whatsapp.net``) — a árvore é achatada por tipo de mídia, não por contato.

Ou seja: reconstruir conversa POR CONTATO a partir deste pacote é impossível — e inventar
remetente seria pior que nada. O que dá para devolver, e é o que este módulo faz, é UMA
conversa sintética por conta ``wa_<numero>`` com uma mensagem por mídia, datada pelo nome do
arquivo e com o binário pendurado no anexo (ponteiro ``IPED::``, o mesmo da tela Arquivos).
O investigador ganha o que a lista plana de arquivos não dá: linha do tempo cronológica,
busca dentro das conversas de mensageiro e o visualizador de mídia de conversa padrão — e a
sequência ``WAnnnn`` por dia sobrevive na ordem das mensagens.

As mídias CONTINUAM entrando como linha de ARQ_TB01 (quem chama cuida disso); aqui é só o
enriquecimento em MSG_TB0x, no mesmo espírito do ``_chat_sinteticas`` dos iPhones.
"""

import logging
import re
from datetime import datetime

from funcoes.investigacao_utils import _gravar_conversa_msg
from funcoes.ufdr_parser import mime_de_extensao

logger = logging.getLogger(__name__)

# tp_app da conversa sintética — aparece no seletor de app da tela de conversas
APP = "WhatsApp (backup Google Drive)"

# "Drive Mobile Backups/wa_5527999035786/" — a pasta do backup é o número da conta (DDI+DDD),
# não um contato. Aceita hífens por cautela com variações de versão do app.
_RE_CONTA = re.compile(r"drive mobile backups/wa_(\d{8,15})(?:_|/)", re.I)

# Nome determinístico do WhatsApp, com ou sem o prefixo achatado do Drive
# ("Media_WhatsApp Images_IMG-20260717-WA0052.jpg"); TT = animatedgif, PTT = audio de voz,
# STK = figurinha. A âncora no fim do nome mantém de fora os *_file_metadata.json.
_RE_MIDIA = re.compile(r"[-_](IMG|VID|AUD|PTT|DOC|STK|TT)-(\d{8})-WA(\d{3,4})\.(\w+)$")

_ROTULO = {"IMG": "Imagem", "VID": "Vídeo", "AUD": "Áudio", "PTT": "Áudio (mensagem de voz)",
           "DOC": "Documento", "STK": "Figurinha", "TT": "Animação"}


def _v(campos: dict, chave: str) -> str:
    valor = campos.get(chave)
    if isinstance(valor, list):
        valor = valor[0] if valor else None
    return str(valor).strip() if valor not in (None, "") else ""


def conta_drive(caminho: str | None) -> str | None:
    """O número da conta WhatsApp do backup do Drive em um caminho (``.../wa_<numero>/...``),
    ou None quando o caminho não é de um backup do Drive. Usa substring barata primeiro
    porque é chamado por TODOS os itens de um caso de nuvem (dezenas de milhares)."""
    c = (caminho or "").replace("\\", "/").lower()
    if "drive mobile backups/wa_" not in c:
        return None
    m = _RE_CONTA.search(c + "/")
    return m.group(1) if m else None


def nome_midia(nome: str | None):
    """(TIPO, datetime do dia, sequência WA####) do nome canônico do WhatsApp, ou None quando
    o nome não é mídia (``*_file_metadata.json``, ``.crypt14`` etc.)."""
    m = _RE_MIDIA.search(nome or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(2), "%Y%m%d")
    except ValueError:
        return None
    return m.group(1).upper(), dt, int(m.group(3))


def detectar(campos: dict) -> dict | None:
    """Item de mídia do WhatsApp no backup do Drive → dados para a linha do tempo; None para
    qualquer outro item. O primeiro teste é uma substring barata porque isto roda por TODOS
    os itens do caso (um caso de nuvem tem dezenas de milhares)."""
    numero = conta_drive(_v(campos, "path"))
    if not numero:
        return None
    nome = _v(campos, "name")
    midi = nome_midia(nome)
    if midi is None:
        return None
    tipo, dt, seq = midi
    origem, md5 = _v(campos, "dataSourcePath"), _v(campos, "md5") or _v(campos, "hash")
    if not (origem and md5):
        # sem binário no storage não há mídia para mostrar na mensagem — nem cria linha vazia
        return None
    try:
        tamanho = int(_v(campos, "size"))
    except ValueError:
        tamanho = None
    return {
        "conta": numero,
        "dt": dt,
        "seq": seq,
        "tipo": tipo,
        "nome": nome,
        "mime": mime_de_extensao(nome),
        "bytes": tamanho,
        "ponteiro": f"IPED::{{ponte}}::{origem}::{md5}::{_v(campos, 'idInDataSource')}",
    }


def _mensagem(item: dict, id_ponte: int) -> dict:
    """Mensagem no formato que investigacao_utils._gravar_conversa_msg espera: sem remetente
    (o backup não preserva quem enviou/recebeu — inventar seria criar prova falsa) e com a
    mídia no anexo, servida pelo mesmo resolutor IPED:: da tela Arquivos."""
    return {
        "corpo": None,
        "timestamp": item["dt"],
        "tipo": _ROTULO.get(item["tipo"], "Mídia"),
        "status": None, "pasta": None,
        "identificador_original": item["nome"][:200],
        "latitude": None, "longitude": None, "estado": None,
        "encaminhada": None, "apagada_remetente": None, "temporaria": None,
        "editada": None, "rotulos": None, "identificador_citado": None,
        "destinatarios": None, "assunto": None, "motivo_exclusao": None,
        "smsc": None, "entregue": None, "lida": None, "reproduzida": None,
        "remetente": None,
        "anexos": [{
            "nome_arquivo": item["nome"][:2000],
            "titulo": None,
            "tipo_conteudo": item["mime"],
            "bytes": item["bytes"],
            "url": None,
            "caminho_zip": item["ponteiro"].format(ponte=id_ponte),
            "file_id": None,
            "transcricao": None,
        }],
    }


class Colecao:
    """Acumula as mídias durante o passe do índice e monta as conversas ao final — a ordem
    do dumper não é cronológica, e o chat precisa de inicio/fim e das mensagens em ordem."""

    def __init__(self, id_extracao_ponte: int):
        self._id_ponte = id_extracao_ponte
        self._por_conta: dict[str, list[dict]] = {}

    def coletar(self, campos: dict) -> None:
        item = detectar(campos)
        if item is not None:
            self._por_conta.setdefault(item["conta"], []).append(item)

    @property
    def vazia(self) -> bool:
        return not self._por_conta

    def resumo(self) -> tuple[int, int]:
        """(número de contas, total de mídias) coletadas — para o --dry-run mostrar antes de gravar."""
        return len(self._por_conta), sum(len(v) for v in self._por_conta.values())

    def gravar(self, cur) -> dict:
        """Grava uma MSG_TB02 por conta ``wa_<numero>`` (mensagens em ordem de data e
        sequência WA####); devolve as contagens para o resumo da importação."""
        conversas = mensagens = 0
        for numero, itens in sorted(self._por_conta.items()):
            itens.sort(key=lambda i: (i["dt"], i["seq"], i["nome"]))
            msg = [_mensagem(i, self._id_ponte) for i in itens]
            chat = {
                "chat_id": f"backup-drive:{numero}",
                "nome_grupo": f"WhatsApp · mídia do backup do Drive ({numero})",
                "inicio": msg[0]["timestamp"],
                "ultima_atividade": msg[-1]["timestamp"],
                # dono_dispositivo fica de fora de propósito: marcaria todas as mídias como
                # "enviadas" pelo titular, e o backup não diz isso — metade é recebida
                "participantes": [{"jid": numero, "nome": None, "admin_grupo": None,
                                   "dono_dispositivo": None}],
                "mensagens": msg,
                "tipo_chat": None,
                "descricao": ("Mídia do WhatsApp preservada pelo backup do Google Drive. O "
                              "backup não guarda os contatos de cada arquivo (banco de "
                              "mensagens criptografado no caso) — cada mensagem é uma mídia, "
                              "datada pelo nome que o WhatsApp dá ao arquivo, na sequência "
                              "WA#### do dia."),
                "conta": None, "status_chat": None, "estado": None,
            }
            # idempotência (a ferramenta de backfill pode rodar de novo sem duplicar). No MODO
            # CANAL (nó processando a importação — docs/no_descentralizado.md) não há banco para
            # consultar: cada importação tem uma ponte nova e o replay do central é aplicado uma
            # única vez por lote (ack por seq), então a checagem não tem o que proteger aqui.
            from funcoes import canal_extracao

            if canal_extracao.emissor_ativo() is None:
                cur.execute(
                    """SELECT 1 FROM investigacao.msg_tb02_conversas
                        WHERE id_extracao = %s AND tp_app = %s AND nu_chat_id = %s""",
                    (self._id_ponte, APP, chat["chat_id"]),
                )
                if cur.fetchone():
                    continue
            try:
                # jid_dono de propósito ausente: com ele, toda mídia sairia como "enviada"
                _gravar_conversa_msg(cur, self._id_ponte, APP, chat)
            except Exception:
                logger.warning("Nuvem IPED: falha ao montar a conversa-mídia do backup "
                               "do Drive (conta %s)", numero, exc_info=True)
                continue
            conversas += 1
            mensagens += len(msg)
        return {"wa_midia_conversas": conversas, "wa_midia_mensagens": mensagens}
