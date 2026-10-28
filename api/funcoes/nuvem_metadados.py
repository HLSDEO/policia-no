"""Sidecar de nuvem: o JSON que acompanha cada arquivo do Drive e cada foto do Google Photos.

No Google Takeout um arquivo do usuário nunca vem sozinho. O Drive entrega
`<nome>-info.json` ao lado de `<nome>`, e o Google Photos entrega `<nome>.jpg.json` (nas
exportações mais novas, `<nome>.jpg.supplemental-metadata.json`) ao lado de `<nome>.jpg`.
Esses JSON não são arquivos do usuário: **descrevem outro arquivo**, e trazem o que só o
servidor sabe.

Até a v1 da Nuvem IPED eles caíam em `_TABELAS_IGNORADAS` e viravam só mais uma linha de
`.json` em `ARQ_TB01_ARQUIVOS`, com o conteúdo descartado. O que se perdia (conferido contra
um `-info.json` real do caso de exemplo, ver docs/extracao_iped.md):

- **quatro tempos, não um.** `creation_date`/`modified` são o que o cliente informou;
  `server_creation_date`/`server_modified` são carimbo do Google. Numa contestação de data é
  neles que se confia, porque mexer no relógio do aparelho não os altera;
- **quem mais tinha acesso** — `owners`, `creator_id`, `readers`, `writers` e o `raw_acl`, cada
  um com `gaia_id` + `email_address` + `display_name`;
- **estado de exclusão na nuvem** — `trashed` e `is_explicitly_trashed` (o segundo distingue
  "o usuário mandou para a lixeira" de "caiu junto com a pasta");
- **de onde a foto subiu** — `googlePhotosOrigin.mobileUpload.deviceType`/`deviceFolder`. O
  `deviceFolder` é a pasta do aparelho de origem, e é ele que diz "veio de WhatsApp Images";
- **quando a foto foi tirada e onde** — `photoTakenTime` e `geoData`;
- **o log de atividade do arquivo** — `activity_records`.

É este metadado que sustenta a tela "Nuvem × aparelho" (`correlacao_nuvem_utils.py`): sem ele
dá para dizer que um arquivo está na nuvem e não no celular, mas não dá para dizer que ele
subiu DAQUELE aparelho, vindo da pasta do WhatsApp, naquela data — que é o que transforma a
ausência em prova. Grava em `NUV_TB01_METADADOS_ARQUIVO` (`db/init/29_nuvem.sql`).
"""

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

FONTE_DRIVE = "Google Drive"
FONTE_PHOTOS = "Google Photos"

# "2023-04-27 20:30:12 UTC" — formato do Drive
_RE_DATA_DRIVE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(?:UTC|Z)?$")

# sufixos que o Takeout acrescenta ao nome do arquivo descrito, na ordem em que são removidos
_SUFIXOS_SIDECAR = (".supplemental-metadata", ".suplemental-metadata")


def alvo_do_sidecar(nome: str) -> str | None:
    """Nome do arquivo que o sidecar descreve, ou None se `nome` não parece um sidecar.

    `Documento de BRASIL-info.json` -> `Documento de BRASIL`
    `IMG-20230715-WA0042.jpg.json` -> `IMG-20230715-WA0042.jpg`
    `IMG-20230715-WA0042.jpg.supplemental-metadata.json` -> `IMG-20230715-WA0042.jpg`

    O `-info` só é removido depois do `.json`, e só no fim: arquivo que legitimamente se chame
    "cadastro-info.pdf" não é tocado, porque não termina em `.json`."""
    nome = (nome or "").strip()
    if not nome.lower().endswith(".json"):
        return None
    base = nome[: -len(".json")]
    for sufixo in _SUFIXOS_SIDECAR:
        if base.lower().endswith(sufixo):
            base = base[: -len(sufixo)]
            break
    if base.lower().endswith("-info"):
        base = base[: -len("-info")]
    base = base.strip()
    return base or None


def _data_drive(valor) -> datetime | None:
    """"2023-04-27 20:30:12 UTC" -> datetime naive em UTC (é assim que as demais colunas
    TIMESTAMP do banco guardam: sem timezone, sempre UTC)."""
    if not isinstance(valor, str):
        return None
    m = _RE_DATA_DRIVE.match(valor.strip())
    if not m:
        return None
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    except ValueError:
        return None


def _data_epoch(bloco) -> datetime | None:
    """{"timestamp": "1689432000", "formatted": "…"} -> datetime naive em UTC. É o formato de
    todo campo de data do sidecar do Google Photos."""
    if not isinstance(bloco, dict):
        return None
    bruto = bloco.get("timestamp")
    try:
        segundos = int(str(bruto).strip())
    except (TypeError, ValueError):
        return None
    if segundos <= 0:
        return None
    try:
        return datetime.fromtimestamp(segundos, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _emails_de(bloco) -> list[str]:
    """Extrai os e-mails de um bloco `{"user_info": [{"user": {"email_address": …}}]}` do
    Drive — a forma que `owners`, `creator_id`, `readers` e `writers` compartilham."""
    if not isinstance(bloco, dict):
        return []
    achados = []
    for entrada in bloco.get("user_info") or []:
        if not isinstance(entrada, dict):
            continue
        usuario = entrada.get("user") if isinstance(entrada.get("user"), dict) else entrada
        email = (usuario or {}).get("email_address")
        if isinstance(email, str) and "@" in email:
            achados.append(email.strip())
    return achados


def _texto(valor, limite: int = 3000) -> str | None:
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return valor[:limite] or None


def parse_drive_info(texto: str, nome_alvo: str) -> dict | None:
    """`<nome>-info.json` do Google Drive -> dict pronto para `gravar_metadado`.

    Todo o conteúdo útil vive sob a chave `lis`; `lis_metadata.activity_records` traz o log de
    atividade. None quando o JSON não tem a cara de um `-info.json` do Drive (o roteamento
    chega aqui pelo nome do arquivo, que outro produto pode coincidentemente ter)."""
    try:
        dados = json.loads(texto)
    except ValueError:
        return None
    if not isinstance(dados, dict):
        return None
    lis = dados.get("lis")
    if not isinstance(lis, dict):
        return None

    compartilhado = []
    for chave in ("readers", "writers", "commenters", "file_organizers", "organizers"):
        compartilhado.extend(_emails_de(lis.get(chave)))
    proprietarios = _emails_de(lis.get("owners")) or _emails_de(lis.get("creator_id"))

    atividade = None
    bloco_atividade = (dados.get("lis_metadata") or {}) if isinstance(dados.get("lis_metadata"), dict) else {}
    registros = (bloco_atividade.get("activity_records") or {}) if isinstance(
        bloco_atividade.get("activity_records"), dict) else {}
    eventos = registros.get("activity_event_records")
    if isinstance(eventos, list) and eventos:
        # teto de 200 eventos: o log de um arquivo muito editado passa de mil entradas e a
        # coluna é para consulta pontual, não para reconstruir o histórico completo
        atividade = eventos[:200]

    tamanho = lis.get("storage_quota_bytes_used")
    try:
        tamanho = int(tamanho) if tamanho not in (None, "") else None
    except (TypeError, ValueError):
        tamanho = None

    return {
        "no_arquivo_alvo": nome_alvo,
        "no_fonte": FONTE_DRIVE,
        "tx_titulo": _texto(lis.get("title")),
        "qt_bytes": tamanho,
        "dt_criacao": _data_drive(lis.get("creation_date")),
        "dt_modificacao": _data_drive(lis.get("modified")),
        "dt_criacao_servidor": _data_drive(lis.get("server_creation_date")),
        "dt_modificacao_servidor": _data_drive(lis.get("server_modified")),
        "dt_visualizacao": _data_drive(lis.get("viewed_by_me")),
        "dt_captura": None,
        "in_lixeira": bool(lis.get("trashed")) if lis.get("trashed") is not None else None,
        "in_lixeira_explicita": (bool(lis.get("is_explicitly_trashed"))
                                 if lis.get("is_explicitly_trashed") is not None else None),
        "in_favorito": bool(lis.get("starred")) if lis.get("starred") is not None else None,
        "no_proprietario": (proprietarios[0][:3000] if proprietarios else None),
        # dedup preservando ordem: o mesmo e-mail aparece em readers e no raw_acl
        "tx_compartilhado_com": ", ".join(dict.fromkeys(
            e for e in compartilhado if e not in proprietarios)) or None,
        "tp_origem_upload": None,
        "no_dispositivo_upload": None,
        "no_pasta_dispositivo": None,
        "vl_latitude": None,
        "vl_longitude": None,
        "tx_atividade": atividade,
    }


# googlePhotosOrigin traz UMA chave, e é ela que diz a procedência da foto. "mobileUpload" é a
# que interessa numa investigação: significa que a foto subiu de um aparelho, e o bloco diz
# qual e de que pasta dele.
_ORIGENS_PHOTOS = {
    "mobileUpload": "Upload de aparelho",
    "webUpload": "Upload pelo navegador",
    "driveDesktopUploader": "Backup do Drive (computador)",
    "fromSharedAlbum": "Álbum compartilhado por terceiro",
    "fromPartnerSharing": "Compartilhamento de parceiro",
    "composition": "Criada pelo Google Fotos",
}


def _coordenada(valor) -> float | None:
    """Latitude/longitude do `geoData`. O Google grava 0.0 quando NÃO há geolocalização, e
    (0, 0) no Golfo da Guiné seria um ponto no mapa — descarta o par zerado."""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n else None


def parse_photos_sidecar(texto: str, nome_alvo: str) -> dict | None:
    """`<foto>.jpg.json` do Google Photos -> dict pronto para `gravar_metadado`."""
    try:
        dados = json.loads(texto)
    except ValueError:
        return None
    if not isinstance(dados, dict):
        return None
    # o sidecar de foto sempre tem title + pelo menos um dos tempos; sem isso é outro JSON
    # qualquer que por acaso está dentro da pasta Google Photos (Photo Features.json etc.)
    if "title" not in dados and "photoTakenTime" not in dados:
        return None

    origem = dados.get("googlePhotosOrigin")
    tp_origem = no_dispositivo = no_pasta = None
    if isinstance(origem, dict):
        for chave, rotulo in _ORIGENS_PHOTOS.items():
            bloco = origem.get(chave)
            if bloco is None:
                continue
            tp_origem = rotulo
            if isinstance(bloco, dict):
                no_dispositivo = _texto(bloco.get("deviceType"))
                pasta = bloco.get("deviceFolder")
                if isinstance(pasta, dict):
                    no_pasta = _texto(pasta.get("localFolderName"))
            break

    geo = dados.get("geoData") if isinstance(dados.get("geoData"), dict) else {}
    geo_exif = dados.get("geoDataExif") if isinstance(dados.get("geoDataExif"), dict) else {}
    latitude = _coordenada(geo.get("latitude")) or _coordenada(geo_exif.get("latitude"))
    longitude = _coordenada(geo.get("longitude")) or _coordenada(geo_exif.get("longitude"))

    return {
        "no_arquivo_alvo": nome_alvo,
        "no_fonte": FONTE_PHOTOS,
        "tx_titulo": _texto(dados.get("description")) or _texto(dados.get("title")),
        "qt_bytes": None,
        "dt_criacao": _data_epoch(dados.get("creationTime")),
        "dt_modificacao": _data_epoch(dados.get("photoLastModifiedTime")),
        "dt_criacao_servidor": None,
        "dt_modificacao_servidor": None,
        "dt_visualizacao": None,
        "dt_captura": _data_epoch(dados.get("photoTakenTime")),
        "in_lixeira": bool(dados.get("trashed")) if dados.get("trashed") is not None else None,
        "in_lixeira_explicita": None,
        "in_favorito": bool(dados.get("favorited")) if dados.get("favorited") is not None else None,
        "no_proprietario": _texto((dados.get("sharedAlbumOwner") or {}).get("name")
                                  if isinstance(dados.get("sharedAlbumOwner"), dict) else None),
        "tx_compartilhado_com": None,
        "tp_origem_upload": tp_origem,
        "no_dispositivo_upload": no_dispositivo,
        "no_pasta_dispositivo": no_pasta,
        "vl_latitude": latitude,
        "vl_longitude": longitude,
        "tx_atividade": None,
    }


# ---------------------------------------------------------------------------
# Sidecars do Google Warrant Return (nuvem Google dentro do .ufdr do Cellebrite)
#
# O Takeout padrão entrega `-info.json` (Drive) e `.jpg.json` (Photos); a extração de nuvem
# do Cellebrite entrega os mesmos metadados em OUTROS formatos: o Drive vem como
# `-metadata.txt` (pares "Chave: valor") e o Google Photos como `.csv` (uma linha por foto,
# com os tempos, EXIF e estado). Os dois alimentam NUV_TB01_METADADOS_ARQUIVO, da mesma forma
# que os sidecars JSON — e sustentam a tela "Nuvem × aparelho".
# ---------------------------------------------------------------------------

_MESES_EN = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# "May 16, 2020 at 9:36 AM UTC" (Drive) e "Oct 31, 2018, 2:43:44 PM UTC" (Photos). O "at" vem
# com espaço antes, a vírgula vem colada no ano — daí o grupo " at |, ".
_RE_DATA_TEXTUAL = re.compile(
    r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})(?: at |, )(\d{1,2}):(\d{2})(?::(\d{2}))? (AM|PM)")


def _data_textual(valor) -> datetime | None:
    if not isinstance(valor, str):
        return None
    v = valor.strip().replace(" UTC", "").replace("Z", "").strip()
    m = _RE_DATA_TEXTUAL.match(v)
    if not m:
        return None
    hora = int(m.group(4)) % 12 + (12 if m.group(7) == "PM" else 0)
    try:
        return datetime(int(m.group(3)), _MESES_EN[m.group(1)], int(m.group(2)),
                        hora, int(m.group(5)), int(m.group(6) or 0))
    except (ValueError, KeyError):
        return None


def _bool_texto(valor) -> bool | None:
    if valor is None:
        return None
    return str(valor).strip().lower() in ("true", "1", "yes")


def parse_drive_metadata_txt(texto: str, nome_alvo: str) -> dict | None:
    """`<arquivo>-metadata.txt` do Google Warrant Return (Drive) -> dict para gravar_metadado.

    O arquivo é uma lista de pares "Chave: valor" (ID, Creator, Owner, Created Date,
    Service Name, ACL Owner, Visibility State, Is Trashed, Title, Parents). None quando o
    texto não tem essa cara."""
    pares: dict[str, str] = {}
    for linha in texto.replace("\r\n", "\n").split("\n"):
        if ":" not in linha:
            continue
        chave, _, valor = linha.partition(":")
        chave = chave.strip()
        if chave and chave not in pares:
            pares[chave] = valor.strip()
    if not pares.get("Title") and not pares.get("ID"):
        return None
    return {
        "no_arquivo_alvo": nome_alvo,
        "no_fonte": FONTE_DRIVE,
        "tx_titulo": _texto(pares.get("Title")),
        "qt_bytes": None,
        "dt_criacao": _data_textual(pares.get("Created Date")),
        "dt_modificacao": None,
        "dt_criacao_servidor": None,
        "dt_modificacao_servidor": None,
        "dt_visualizacao": None,
        "dt_captura": None,
        "in_lixeira": _bool_texto(pares.get("Is Trashed")),
        "in_lixeira_explicita": None,
        "in_favorito": None,
        "no_proprietario": _texto(pares.get("Owner")) or _texto(pares.get("Creator")),
        "tx_compartilhado_com": None,
        "tp_origem_upload": None,
        "no_dispositivo_upload": None,
        "no_pasta_dispositivo": None,
        "vl_latitude": None,
        "vl_longitude": None,
        "tx_atividade": None,
    }


def parse_photos_sidecar_csv(texto: str, nome_alvo: str) -> dict | None:
    """`<foto>.jpg.csv` do Google Warrant Return (Google Photos) -> dict para gravar_metadado.

    Uma linha CSV por foto, com Photo Title, Creation/Modification/Photo Taken date/time,
    EXIF - Latitude/Longitude, Upload IP, Status, Is Archived/Favorited/Trashed, Tagged
    Names/Emails, Album ID. O `metadata.csv` (por álbum) NÃO cai aqui — é descritivo do álbum,
    não do arquivo. None quando o CSV não tem cara de sidecar de foto."""
    try:
        linhas = list(csv.DictReader(io.StringIO(texto.replace("\x00", ""), newline="")))
    except (csv.Error, UnicodeDecodeError):
        return None
    if not linhas:
        return None
    linha = linhas[0]
    if "Photo Title" not in linha and "Photo Taken date/time" not in linha:
        return None
    compartilhado = ", ".join(
        t for t in (_texto(linha.get("Tagged Names")), _texto(linha.get("Tagged Emails"))) if t
    ) or None
    return {
        "no_arquivo_alvo": nome_alvo,
        "no_fonte": FONTE_PHOTOS,
        "tx_titulo": _texto(linha.get("Photo Title")),
        "qt_bytes": None,
        "dt_criacao": _data_textual(linha.get("Creation date/time")),
        "dt_modificacao": _data_textual(linha.get("Modification date/time")),
        "dt_criacao_servidor": None,
        "dt_modificacao_servidor": None,
        "dt_visualizacao": None,
        "dt_captura": _data_textual(linha.get("Photo Taken date/time")),
        "in_lixeira": _bool_texto(linha.get("Is Trashed")),
        "in_lixeira_explicita": None,
        "in_favorito": _bool_texto(linha.get("Is Favorited")),
        "no_proprietario": None,
        "tx_compartilhado_com": compartilhado,
        "tp_origem_upload": None,
        "no_dispositivo_upload": None,
        "no_pasta_dispositivo": None,
        "vl_latitude": _coordenada(linha.get("EXIF - Latitude")) or _coordenada(linha.get("User Edited Latitude")),
        "vl_longitude": _coordenada(linha.get("EXIF - Longitude")) or _coordenada(linha.get("User Edited Longitude")),
        "tx_atividade": None,
    }


def parse_sidecar(nome: str, conteudo: bytes) -> dict | None:
    """Decide o parser pelo conteúdo, não pelo nome: o `-info.json` do Drive se reconhece pela
    chave `lis` e o do Photos por `title`/`photoTakenTime`. Um `.json` que não seja sidecar
    (`Photo Features.json`, `Deletion Markers.json`, `user_info.json`) devolve None e segue
    sendo só um arquivo comum."""
    alvo = alvo_do_sidecar(nome)
    if not alvo:
        return None
    try:
        texto = conteudo.decode("utf-8", "replace")
    except (AttributeError, UnicodeDecodeError):
        return None
    return parse_drive_info(texto, alvo) or parse_photos_sidecar(texto, alvo)


def gravar_metadado(cur, id_extracao: int, dados: dict) -> None:
    from funcoes import canal_extracao  # tardio: só o caminho do nó lê o canal
    if canal_extracao.emitir("metadado_nuvem", (id_extracao, dados)):  # nó entrega ao central
        return
    if canal_extracao.emitir("metadado_nuvem", (id_extracao, dados)):  # nó entrega ao central em vez de gravar
        return
    """Insere uma linha em NUV_TB01_METADADOS_ARQUIVO. `NO_ARQUIVO_ALVO` vai em minúsculas
    porque a junção com ARQ_TB01_ARQUIVOS é por `LOWER(NO_ARQUIVO)` (índice IX203)."""
    cur.execute(
        """INSERT INTO investigacao.nuv_tb01_metadados_arquivo
           (id_extracao, no_arquivo_alvo, no_fonte, tx_titulo, qt_bytes, dt_criacao,
            dt_modificacao, dt_criacao_servidor, dt_modificacao_servidor, dt_visualizacao,
            dt_captura, in_lixeira, in_lixeira_explicita, in_favorito, no_proprietario,
            tx_compartilhado_com, tp_origem_upload, no_dispositivo_upload,
            no_pasta_dispositivo, vl_latitude, vl_longitude, tx_atividade)
           VALUES (%(id_extracao)s, %(no_arquivo_alvo)s, %(no_fonte)s, %(tx_titulo)s,
                   %(qt_bytes)s, %(dt_criacao)s, %(dt_modificacao)s, %(dt_criacao_servidor)s,
                   %(dt_modificacao_servidor)s, %(dt_visualizacao)s, %(dt_captura)s,
                   %(in_lixeira)s, %(in_lixeira_explicita)s, %(in_favorito)s,
                   %(no_proprietario)s, %(tx_compartilhado_com)s, %(tp_origem_upload)s,
                   %(no_dispositivo_upload)s, %(no_pasta_dispositivo)s, %(vl_latitude)s,
                   %(vl_longitude)s, %(tx_atividade)s)""",
        {**dados,
         "id_extracao": id_extracao,
         "no_arquivo_alvo": (dados.get("no_arquivo_alvo") or "").lower(),
         "tx_atividade": (Jsonb(dados["tx_atividade"])
                          if dados.get("tx_atividade") is not None else None)},
    )
