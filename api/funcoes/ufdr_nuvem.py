"""Extração de nuvem Google embutida no .ufdr do Cellebrite ("Google Warrant Return").

Um .ufdr de nuvem (fabricante "Google", dispositivo "Google Warrant Return") guarda o
export dos produtos do Google em ZIPs ANINHADOS dentro do próprio .ufdr:
`files/Archives/<conta>.<id>.<Produto>[_001].zip`. O report.xml só cobre uma parte
(localizações, e-mails, buscas, páginas visitadas, contatos); o resto — Google Photos,
Minha Atividade, Histórico de localização bruto/semântico, Drive, Agenda, Play Store,
perfil da conta, dispositivos, locais salvos — vive nesses ZIPs e é o que este módulo lê.

Os ZIPs aninhados vêm STORED (sem compressão) dentro do .ufdr, então são abertos por
ufdr_cache.obter_zip_aninhado sem descomprimir os GBs do membro: só o diretório central é
lido. Os binários (fotos, arquivos do Drive) NÃO são copiados para o banco — ficam
registrados em ARQ_TB01 com um ponteiro `files/Archives/<zip>::<caminho_interno>`, servidos
sob demanda como qualquer outro anexo do UFDR (ver _resolver_arquivo_zip).

Os parsers dos produtos reaproveitam takeout_parsers/nuvem_metadados; os formatos próprios
do warrant return (Location History Records/Semantic, Play Store, locais salvos, sidecars
CSV/txt) foram adicionados lá. Ver docs/extração_ufdr.md.
"""

import logging
import re
import zipfile

from funcoes.contas_nuvem_utils import (
    _gravar_atividade_conta,
    _gravar_dispositivo_conta,
    _gravar_evento_conta,
    _gravar_perfil_conta,
)
from funcoes.investigacao_utils import (
    _drenar_arquivos,
    _gravar_aplicativo,
    _gravar_localizacao,
    _gravar_nota,
)
from funcoes.iped_campos import LinhaParcial
from funcoes.nuvem_metadados import (
    gravar_metadado,
    parse_drive_metadata_txt,
    parse_photos_sidecar_csv,
)
from funcoes.takeout_parsers import (
    parse_adcs,
    parse_ics,
    parse_keep_note,
    parse_location_history_records,
    parse_location_history_semantic,
    parse_my_activity,
    parse_playstore_warrant_csv,
    parse_saved_places_txt,
    parse_subscriber_info,
)
from funcoes.ufdr_cache import obter_zip_aninhado
from funcoes.ufdr_parser import categoria_de_extensao, mime_de_extensao

logger = logging.getLogger(__name__)

_PREFIXO_ARQUIVOS = "files/Archives/"

# Produto deduzido do nome do ZIP aninhado. A ordem importa: "drivemobilebackups" e
# "drive.metadata" vêm antes de "drive", e "keep." (com ponto) evita casar outros produtos.
_PREDICADOS_PRODUTO = (
    ("googlephotos", "photos"),
    ("myactivity", "myactivity"),
    ("locationhistory.records", "loc_records"),
    ("locationhistory.semantic", "loc_semantic"),
    ("locationhistory.tombstones", "loc_tombstones"),
    ("drivemobilebackups", "drive_backup"),
    ("drive.metadata", "drive_meta"),
    ("calendar", "calendar"),
    ("googleplaystore", "playstore"),
    ("googleaccount", "subscriber"),
    ("androiddeviceconfigurationservice", "adcs"),
    ("keep.", "keep"),
    ("savedplaces", "savedplaces"),
    ("drive", "drive"),
)


# Um ZIP de warrant return do Google tem a forma "<conta>.<id>.<Produto>[_001].zip", com o
# <id> numérico do Google entre pontos (ex. "...@gmail.com.431340305360.Drive.DriveFiles_001.zip").
# Alguns produtos vêm sem o id ("<conta>.<Produto>.zip", ex. "...1414.Drive.Metadata.zip"), então
# o nome do produto conhecido também conta. É isso que distingue dos ZIPs de sistema que um
# APARELHO FÍSICO guarda em files/Archives/ (APEX do Android, dumps de app, "comm.zip"...):
# um celular Samsung com esses arquivos era classificado como nuvem Google.
_RE_ID_GOOGLE = re.compile(r"\.\d{5,}\.")


def _e_zip_warrant(base: str) -> bool:
    """O nome base de um entry em files/Archives/ é um ZIP de produto de warrant return?"""
    if not base.lower().endswith(".zip"):
        return False
    sem_ext = base[:-4]
    if _RE_ID_GOOGLE.search(sem_ext):
        return True
    baixo = sem_ext.lower()
    for marcador, _produto in _PREDICADOS_PRODUTO:
        idx = baixo.find(marcador)
        if idx > 0:  # > 0 e não >= 0: o marcador precisa vir DEPOIS da conta ("conta.Drive...")
            return True
    return False


def _zip_warrant_de(zf: zipfile.ZipFile, i) -> str | None:
    """O nome base (sem files/Archives/) do entry se ele for um ZIP de warrant return; None caso contrário."""
    if not i.filename.startswith(_PREFIXO_ARQUIVOS) or i.is_dir():
        return None
    base = i.filename[len(_PREFIXO_ARQUIVOS):].rsplit("/", 1)[-1]
    return base if _e_zip_warrant(base) else None


def tem_nuvem_google(zf: zipfile.ZipFile) -> bool:
    """True quando o .ufdr tem ZIPs de warrant return do Google aninhados em files/Archives/
    — o sinal de que esta é uma extração de nuvem Google (e não um aparelho físico)."""
    return any(_zip_warrant_de(zf, i) is not None for i in zf.infolist())


def _conta_de_zip_warrant(base: str) -> str | None:
    """A conta do Google dona de um ZIP de warrant return, do nome "<conta>.<id>.<Produto>.zip".

    A conta é tudo ANTES do id numérico ("...@gmail.com" vem completo, com o domínio — dividir
    pelo primeiro ponto truncaria o e-mail) ou, sem id, tudo antes do nome do produto."""
    sem_ext = base[:-4]
    m = _RE_ID_GOOGLE.search(sem_ext)
    if m:
        return sem_ext[:m.start()].strip()
    baixo = sem_ext.lower()
    pos = len(sem_ext)
    for marcador, _produto in _PREDICADOS_PRODUTO:
        idx = baixo.find(marcador)
        if 0 < idx < pos:
            pos = idx
    if pos < len(sem_ext):
        return sem_ext[:pos].strip(".")
    return None


def detectar_conta_google(zf: zipfile.ZipFile) -> str | None:
    """O identificador da conta do Google dona da extração, lido do nome dos ZIPs aninhados.

    O nome é "<conta>.<id>.<Produto>[_001].zip" (ex.
    "agnaldoterapeutaholistico1414.247770575867.GooglePhotos.PhotoResource_001.zip"). Devolve
    None quando não há ZIP de nuvem."""
    for i in zf.infolist():
        base = _zip_warrant_de(zf, i)
        if base is None:
            continue
        conta = _conta_de_zip_warrant(base)
        if conta:
            return conta
    return None


def _produto_zip(base_low: str) -> str | None:
    for marcador, produto in _PREDICADOS_PRODUTO:
        if marcador in base_low:
            return produto
    return None


# Entradas que não são dado do usuário nem metadado aproveitável.
_IGNORAR_SUFIXOS = (
    "exportsummary.txt",
    "norecords.txt",
)


def _ignorar_entrada(nome: str) -> bool:
    baixo = nome.lower()
    if baixo.endswith(_IGNORAR_SUFIXOS):
        return True
    # metadata.csv é o descritivo do ÁLBUM no Google Photos, não do arquivo
    if baixo.endswith("metadata.csv") and "/google photos/" in ("/" + baixo):
        return True
    if baixo.endswith("photo features.json"):
        return True
    return False


def _gravar_linha(fn, cur, id_extracao, linha, contagens, chave):
    fn(cur, id_extracao, LinhaParcial(linha))
    if chave in contagens:
        contagens[chave] += 1


def _linha_arquivo(id_extracao, nome_zip_externo, caminho_interno, info):
    """Tupla no formato do buffer de ARQ_TB01 (ver _laco_report_xml em investigacao_utils),
    com o ponteiro de dois níveis `files/Archives/<zip>::<caminho_interno>` em CO_CAMINHO_ZIP."""
    nome = caminho_interno.rsplit("/", 1)[-1] or caminho_interno
    return (
        id_extracao, nome[:2000], caminho_interno, categoria_de_extensao(nome),
        mime_de_extensao(nome), info.file_size, None, None,
        f"{nome_zip_externo}::{caminho_interno}",
        None, None, None, None, None, None, None, None, None, None,
    )


def _tratar_entrada(cur, id_extracao, contagens, produto, nome_zip_externo,
                    caminho_interno, info, zf_interno, buffer_arquivos) -> None:
    """Roteia uma entrada de um ZIP aninhado: vira linha de domínio (via parser) ou arquivo
    em ARQ_TB01 (binário com ponteiro aninhado), conforme o produto."""
    if _ignorar_entrada(caminho_interno):
        return
    nome_low = caminho_interno.lower()

    def _ler_texto():
        return zf_interno.read(caminho_interno).decode("utf-8", "replace")

    if produto == "photos":
        if nome_low.endswith(".csv"):
            alvo = caminho_interno.rsplit("/", 1)[-1][:-len(".csv")]
            lado = parse_photos_sidecar_csv(_ler_texto(), alvo)
            if lado is not None:
                gravar_metadado(cur, id_extracao, lado)
                contagens["metadados_nuvem"] = contagens.get("metadados_nuvem", 0) + 1
            return
        # o restante (fora o metadata.csv do álbum, já ignorado) é o binário da foto/vídeo
        buffer_arquivos.append(_linha_arquivo(id_extracao, nome_zip_externo, caminho_interno, info))
        contagens["arquivos"] = contagens.get("arquivos", 0) + 1
        return

    if produto == "myactivity":
        if nome_low.endswith(".html"):
            for linha in parse_my_activity(_ler_texto()):
                _gravar_linha(_gravar_atividade_conta, cur, id_extracao, linha, contagens, "atividades")
        else:
            buffer_arquivos.append(_linha_arquivo(id_extracao, nome_zip_externo, caminho_interno, info))
            contagens["arquivos"] = contagens.get("arquivos", 0) + 1
        return

    if produto in ("loc_records", "loc_semantic"):
        if nome_low.endswith(".json"):
            parser = (parse_location_history_records if produto == "loc_records"
                      else parse_location_history_semantic)
            for linha in parser(_ler_texto()):
                _gravar_linha(_gravar_localizacao, cur, id_extracao, linha, contagens, "localizacoes")
        # o .csv do Records repete o .json — ignorado para não duplicar
        return

    if produto == "loc_tombstones":
        return  # marcador de exclusão: baixo valor probatório, fica de fora

    if produto == "drive_backup":
        buffer_arquivos.append(_linha_arquivo(id_extracao, nome_zip_externo, caminho_interno, info))
        contagens["arquivos"] = contagens.get("arquivos", 0) + 1
        return

    if produto == "drive_meta":
        if nome_low.endswith("-metadata.txt"):
            alvo = caminho_interno.rsplit("/", 1)[-1][:-len("-metadata.txt")]
            lado = parse_drive_metadata_txt(_ler_texto(), alvo)
            if lado is not None:
                gravar_metadado(cur, id_extracao, lado)
                contagens["metadados_nuvem"] = contagens.get("metadados_nuvem", 0) + 1
        return

    if produto == "drive":
        buffer_arquivos.append(_linha_arquivo(id_extracao, nome_zip_externo, caminho_interno, info))
        contagens["arquivos"] = contagens.get("arquivos", 0) + 1
        return

    if produto == "calendar":
        if nome_low.endswith(".ics"):
            for linha in parse_ics(_ler_texto()):
                _gravar_linha(_gravar_evento_conta, cur, id_extracao, linha, contagens, "eventos")
        return

    if produto == "playstore":
        if nome_low.endswith(".csv"):
            for bloco in parse_playstore_warrant_csv(zf_interno.read(caminho_interno)):
                for linha in bloco:
                    _gravar_linha(_gravar_aplicativo, cur, id_extracao, linha, contagens, "aplicativos")
        return

    if produto == "subscriber":
        if nome_low.endswith(".html"):
            linha = parse_subscriber_info(_ler_texto())
            if linha is not None:
                _gravar_linha(_gravar_perfil_conta, cur, id_extracao, linha, contagens, "perfil")
        return

    if produto == "adcs":
        if nome_low.endswith(".html"):
            for linha in parse_adcs(_ler_texto(), "warrant-return"):
                _gravar_linha(_gravar_dispositivo_conta, cur, id_extracao, linha, contagens, "dispositivos")
        return

    if produto == "keep":
        if nome_low.endswith(".json"):
            linha = parse_keep_note(_ler_texto())
            if linha is not None:
                _gravar_linha(_gravar_nota, cur, id_extracao, linha, contagens, "notas")
        return

    if produto == "savedplaces":
        if nome_low.endswith("places.txt"):
            for linha in parse_saved_places_txt(_ler_texto()):
                _gravar_linha(_gravar_localizacao, cur, id_extracao, linha, contagens, "localizacoes")
        return


def processar_nuvem_google(zf: zipfile.ZipFile, caminho_ufdr, cur, conn, bat,
                           id_extracao: int, contagens: dict) -> bool:
    """Passe pelos ZIPs aninhados em files/Archives/: grava os produtos do Google nas tabelas
    de domínio e os binários em ARQ_TB01. Roda depois do report.xml, dentro da MESMA conexão
    longa, e respeita cancelamento/progresso como o laço do XML. Devolve True se o usuário
    pediu cancelamento no meio.

    Falha ao abrir/interpretar um ZIP aninhado é engolida (best-effort, como o resto da
    extração): um produto com formato inesperado não pode derrubar a nuvem inteira."""
    buffer_arquivos: list[tuple] = []
    # total de entradas aninhadas para o progresso (barato: só lê o diretório central)
    arquivos_nuvem = [i for i in zf.infolist()
                      if i.filename.startswith(_PREFIXO_ARQUIVOS) and not i.is_dir()]
    total = 0
    feitos = 0

    for info in arquivos_nuvem:
        produto = _produto_zip(info.filename.rsplit("/", 1)[-1].lower())
        if produto is None:
            continue
        try:
            zf_interno = obter_zip_aninhado(caminho_ufdr, info.filename)
        except Exception:
            logger.warning("Extração %s: falha ao abrir ZIP aninhado %s",
                           id_extracao, info.filename, exc_info=True)
            continue
        try:
            entradas = [e for e in zf_interno.infolist() if not e.is_dir()]
        except Exception:
            logger.warning("Extração %s: falha ao listar ZIP aninhado %s",
                           id_extracao, info.filename, exc_info=True)
            continue
        total += len(entradas)
        for iinfo in entradas:
            _tratar_entrada(cur, id_extracao, contagens, produto, info.filename,
                            iinfo.filename, iinfo, zf_interno, buffer_arquivos)
            feitos += 1
            if feitos % 500 == 0:
                conn.commit()
                bat.progresso(feitos, total)
                if bat.cancelar_pedido():
                    if buffer_arquivos:
                        _drenar_arquivos(cur, buffer_arquivos)
                        buffer_arquivos.clear()
                    return True

    if buffer_arquivos:
        _drenar_arquivos(cur, buffer_arquivos)
        buffer_arquivos.clear()
    bat.progresso(total, total)
    return False
