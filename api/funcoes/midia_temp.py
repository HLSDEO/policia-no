"""Cache temporário de mídia da galeria ("Extrair temporariamente" na tela Arquivos).

A galeria serve cada arquivo descomprimindo o binário sob demanda — do `storage-N.db`
gzipado do IPED (`iped_cache.abrir_blob_stream`) ou do ZIP do .ufdr. Para foto é barato;
para vídeo de centenas de MB é a conta inteira a cada abertura do modal, e o navegador ainda
perde o seek (o stream não aceita Range). E vídeo sem quadro gerado pelo IPED não tem miniatura
nenhuma: a grade mostra só o ícone (ver `iped_cache.ler_thumb`).

Este módulo materializa os vídeos e imagens de UMA extração em `data/_midia_temp/<inquérito>/
<extração>/<id_arquivo><ext>` — um arquivo de verdade em disco, de onde o seek funciona e a
leitura é direta — e gera por ffmpeg o quadro que o IPED não gerou (`<id_arquivo>.thumb.jpg`,
o mesmo papel do `view/H0/H1/HASH.jpg` do VideoThumbTask). É PROVA, então a cópia é temporária
por construção: expira em `MIDIA_TEMP_TTL_HORAS` (padrão 4 h), sem acesso no toque (a rota
"toca" o arquivo a cada uso, quem está em uso não vence). O original no .ufdr/caso IPED nunca é
tocado — daqui o binário só sai em leitura.

O trabalho corre no job `midia_temporaria` (funcoes/jobs_utils.py), não na requisição: copiar
20 GB e rodar ffmpeg em vídeo não é coisa para se fazer dentro de um GET da grade. As rotas de
conteúdo só consultam `midia_temporaria()` / `thumb_temporaria()`, que são dois stat() best-effort.
"""

import gzip
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile

from pathlib import Path

from funcoes import iped_cache, jobs_utils, ufdr_cache, ufdr_zip
from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import (
    _resolver_arquivo_iped,
    _resolver_arquivo_zip,
    fonte_ufdr,
)
from funcoes.organizacao_arquivos import PASTA_DADOS

logger = logging.getLogger(__name__)

_PASTA_RAIZ = "_midia_temp"


def _ttl_horas() -> int:
    # os.environ direto (mesmo padrão de jobs_utils._minutos_job_stale): desempenho_utils.valor_int
    # só conhece as chaves do dimensionamento recomendado, e uma chave própria nova silencia o .env.
    try:
        return max(1, int((os.environ.get("MIDIA_TEMP_TTL_HORAS") or "").strip() or 4))
    except ValueError:
        return 4


def raiz_midia_temp() -> Path:
    return PASTA_DADOS / _PASTA_RAIZ


def pasta_midia_temp(id_inquerito: int, id_extracao: int) -> Path:
    return raiz_midia_temp() / str(int(id_inquerito)) / str(int(id_extracao))


def _expirado(caminho: Path) -> bool:
    return time.time() - caminho.stat().st_mtime > _ttl_horas() * 3600


def _nome_copia(id_arquivo: int) -> str:
    """A cópia materializada se chama exatamente `<id_arquivo>`, SEM extensão: o mime real vem
    do banco na hora de servir, o ffmpeg sondar o conteúdo dispensa sufixo, e — o que importa
    num aparelho com 200 mil mídias — achar a cópia vira um stat direto, não um glob que
    revarre a pasta inteira a cada célula da galeria."""
    return str(int(id_arquivo))


def _achar_copia(id_inquerito: int, id_extracao: int, id_arquivo: int) -> Path | None:
    """O arquivo materializado do arquivo, se existir e não estiver vencido."""
    caminho = pasta_midia_temp(id_inquerito, id_extracao) / _nome_copia(id_arquivo)
    try:
        if not caminho.is_file():
            return None
        if _expirado(caminho):
            caminho.unlink(missing_ok=True)
            return None
        caminho.touch()  # em uso não vence: a janela de 4 h é deslizante por acesso
    except OSError:
        return None
    return caminho


def midia_temporaria(id_inquerito: int, id_extracao: int | None,
                     id_arquivo: int) -> Path | None:
    """Caminho do binário materializado (e fresco) para o arquivo, ou None. Best-effort por
    definição: qualquer erro de disco é "não tem cópia", nunca erro de requisição."""
    if not id_extracao:
        return None
    try:
        return _achar_copia(int(id_inquerito), int(id_extracao), int(id_arquivo))
    except (OSError, ValueError):
        return None


def thumb_temporaria(id_inquerito: int, id_extracao: int | None,
                     id_arquivo: int) -> bytes | None:
    """JPEG do quadro gerado para o vídeo (e fresco), ou None. Também "toca" o arquivo: a grade
    que mostra a thumb de um vídeo mantém viva a cópia que a gerou."""
    if not id_extracao:
        return None
    try:
        caminho = pasta_midia_temp(int(id_inquerito), int(id_extracao)) / f"{id_arquivo}.thumb.jpg"
        if not caminho.is_file() or _expirado(caminho):
            return None
        dados = caminho.read_bytes()
        caminho.touch()
        return dados or None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- limpeza


def limpar_expirados() -> int:
    """Apaga arquivos vencidos pela raiz do cache e as pastas que ficarem vazias. Devolve
    quantos arquivos saíram. Chamado no início de cada job (e só lá): o volume é pequeno e o
    caso "abrir a galeria 5 h depois" resolve-se no próprio acesso, em _achar_copia."""
    raiz = raiz_midia_temp()
    if not raiz.is_dir():
        return 0
    apagados = 0
    for caminho in raiz.rglob("*"):
        if not caminho.is_file():
            continue
        try:
            if _expirado(caminho):
                caminho.unlink(missing_ok=True)
                apagados += 1
        except OSError:
            continue
    for pasta in sorted((p for p in raiz.rglob("*") if p.is_dir()), key=lambda p: len(p.parts),
                        reverse=True):
        try:
            next(pasta.iterdir())
        except StopIteration:
            try:
                pasta.rmdir()
            except OSError:
                pass
        except OSError:
            continue
    return apagados


# --------------------------------------------------------------------------- cópia


def _zip_persistente(zfs: dict, caminho):
    """Handle de zip do job: o .ufdr de um aparelho grande tem CENTENAS de milhares de entradas
    e abri-lo relê o diretório central inteiro — abrir por arquivo tornava a cópia de 200 mil
    mídias inviável (foi o que engargalou o primeiro job em produção). Para fonte LOCAL o handle
    vive no dicionário do job e é fechado no fim; para fonte REMOTA (nó) abre-se por arquivo
    mesmo: segurar uma ponte HTTP aberta por horas é pior que o custo do re-abrir."""
    if getattr(caminho, "eh_remoto", False):
        return ufdr_zip.abrir(caminho), True
    chave = str(caminho)
    handle = zfs.get(chave)
    if handle is None:
        handle = ufdr_zip.abrir(caminho)
        zfs[chave] = handle
    return handle, False


def _abrir_leitor(info: dict, zfs: dict):
    """Gerador de bytes do binário do arquivo, pelos mesmos resolutores da rota de conteúdo:
    ponteiro IPED::, ZIP aninhado da nuvem ou membro do .ufdr. Levanta FileNotFoundError quando
    a origem não existe mais — o job conta como erro e segue."""
    caminho_zip = info["caminho_zip"]
    if caminho_zip.startswith("IPED::"):
        resolvido = _resolver_arquivo_iped(caminho_zip)
        if not resolvido:
            raise FileNotFoundError("Pasta do caso do IPED não encontrada.")
        pasta_modulo, origem, hash_md5, id_fonte = resolvido
        return iped_cache.abrir_blob_stream(pasta_modulo, origem, hash_md5, id_fonte)

    aninhado = ufdr_cache.desmembrar_aninhado(caminho_zip)
    if aninhado:
        membro_externo, caminho_interno = aninhado
        caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
        if caminho_ufdr is None:
            raise FileNotFoundError(".ufdr original não encontrado.")

        def _gerador_aninhado():
            with ufdr_cache.abrir_membro_aninhado(caminho_ufdr, membro_externo,
                                                  caminho_interno) as fp:
                while True:
                    pedaco = fp.read(1024 * 1024)
                    if not pedaco:
                        break
                    yield pedaco
        return _gerador_aninhado()

    resolvido = _resolver_arquivo_zip(caminho_zip, info["no_arquivo_ufdr"])
    if not resolvido:
        raise FileNotFoundError("Arquivo de origem (ZIP) não encontrado.")
    caminho_zip_real, caminho_interno = resolvido

    def _gerador_zip():
        zf, de_fecho = _zip_persistente(zfs, caminho_zip_real)
        try:
            with zf.open(caminho_interno) as fp:
                while True:
                    pedaco = fp.read(1024 * 1024)
                    if not pedaco:
                        break
                    yield pedaco
        finally:
            if de_fecho:      # remoto: a ponte fecha com o arquivo; local fica para o próximo
                zf.close()
    return _gerador_zip()


def _copiar_para_temp(info: dict, destino: Path, zfs: dict) -> int:
    """Copia o binário para `destino.tmp` e troca no lugar (os.replace) — uma leitura pela
    metade nunca é servida como cópia completa. Devolve os bytes escritos."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    total = 0
    try:
        with open(tmp, "wb") as fp:
            for pedaco in _abrir_leitor(info, zfs):
                fp.write(pedaco)
                total += len(pedaco)
        os.replace(tmp, destino)
    finally:
        tmp.unlink(missing_ok=True)
    return total


# --------------------------------------------------------------------------- miniatura

_LADO_MINIATURA = 320  # mesmo _TAMANHO_MINIATURA da rota de conteúdo


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def gerar_thumb(caminho_video: Path, destino_jpg: Path) -> bool:
    """Um quadro do começo do vídeo em JPEG pequeno — o que o VideoThumbTask do IPED não gerou
    para este item. `ffmpeg -frames:v 1` para no primeiro quadro decodável: o tamanho do vídeo
    quase não conta, o que conta é conseguir ler o início (arquivo em disco resolve o seek que o
    blob gzipado não tem). False quando não há ffmpeg, o vídeo não decodifica ou estoura o tempo."""
    binario = _ffmpeg()
    if not binario:
        return False
    # o TMP mantém a extensão .jpg no fim: o ffmpeg escolhe o formato de saída pela extensão,
    # e um ".jpg.tmp" morre em "Unable to choose an output format"
    tmp = destino_jpg.with_name(destino_jpg.stem + ".tmp.jpg")
    comando = [binario, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(caminho_video),
               "-frames:v", "1",
               "-vf", f"scale={_LADO_MINIATURA}:-2",
               "-q:v", "5",
               str(tmp)]
    try:
        r = subprocess.run(comando, capture_output=True, timeout=90)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            return False
        tmp.replace(destino_jpg)
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _tem_thumb_iped(caminho_zip: str) -> bool:
    """O IPED já tem quadro pronto para este item? Se sim, a rota serve o dele e a cópia não
    precisa de thumb (ler_thumb é barato e já sabe onde procurar)."""
    if not caminho_zip.startswith("IPED::"):
        return False
    resolvido = _resolver_arquivo_iped(caminho_zip)
    if not resolvido:
        return False
    pasta_modulo, _origem, hash_md5, _id_fonte = resolvido
    return bool(iped_cache.ler_thumb(pasta_modulo, hash_md5))


# --------------------------------------------------------------------------- serving com Range

_ALCANCE = re.compile(r"^bytes=(\d*)-(\d*)$")

_PEDACO = 1024 * 1024


def _intervalo_alcance(alcance: str | None, total: int) -> tuple[int, int, bool]:
    """(inicio, fim, parcial) do cabeçalho Range pedido. Um único intervalo — o padrão do
    <video>. Sem header, sintaxe inválida ou multi-intervalo: o arquivo inteiro (200), que é o
    comportamento que o player aceita de qualquer forma. Faixa fora do arquivo: (-1, -1, True)
    para o chamador responder 416 com o tamanho real."""
    if not alcance:
        return 0, total - 1, False
    m = _ALCANCE.match(alcance.strip())
    if not m:
        return 0, total - 1, False
    inicio_s, fim_s = m.groups()
    if not inicio_s and not fim_s:
        return 0, total - 1, False
    if inicio_s:
        inicio = int(inicio_s)
        fim = int(fim_s) if fim_s else total - 1
    else:  # "bytes=-N": os N últimos — é exatamente o que o player busca num .mov com moov no fim
        inicio = max(total - int(fim_s), 0)
        fim = total - 1
    fim = min(fim, total - 1)
    if inicio > fim or inicio >= max(total, 1):
        return -1, -1, True
    return inicio, fim, True


def resposta_arquivo(caminho: Path, tp_mime: str | None, alcance: str | None,
                     disposicao: str = "inline", nome: str = "arquivo"):
    """Responde um arquivo LOCAL com suporte real a Range (206 por faixa, StreamingResponse por
    blocos de 1 MiB). O FileResponse do Starlette desta versão NÃO trata Range: o <video> pede
    uma faixa, recebe 200 com o arquivo inteiro, e num .mov de iPhone — cujo átomo moov fica no
    fim — o player encalha esperando o metadado enquanto baixa os 300 MB todos.
    É o que a casa já faz em /no/arquivo (no_servico.py), agora para a cópia materializada."""
    from fastapi import Response
    from fastapi.responses import StreamingResponse

    from funcoes.resposta_utils import cabecalho_disposicao

    total = caminho.stat().st_size  # sumiu no meio (TTL)? OSError sobe e o chamador cai no UFDR
    # nome percent-codificado no filename* UTF-8: um acento no header latin-1 do Starlette
    # derrubava a resposta com UnicodeEncodeError (ver resposta_utils.cabecalho_disposicao)
    cabecalhos = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": cabecalho_disposicao(disposicao, nome),
    }
    if total == 0:
        return Response(status_code=200, headers=cabecalhos)

    inicio, fim, parcial = _intervalo_alcance(alcance, total)
    if inicio < 0:
        cabecalhos["Content-Range"] = f"bytes */{total}"
        return Response(status_code=416, headers=cabecalhos)

    def _gerar():
        with open(caminho, "rb") as fp:
            fp.seek(inicio)
            restante = fim - inicio + 1
            while restante > 0:
                pedaco = fp.read(min(_PEDACO, restante))
                if not pedaco:
                    break
                restante -= len(pedaco)
                yield pedaco

    cabecalhos["Content-Length"] = str(fim - inicio + 1)
    if parcial:
        cabecalhos["Content-Range"] = f"bytes {inicio}-{fim}/{total}"
    return StreamingResponse(_gerar(),
                             status_code=206 if parcial else 200,
                             media_type=tp_mime or "application/octet-stream",
                             headers=cabecalhos)


# --------------------------------------------------------------------------- job


def _min_bytes_imagem() -> int:
    """Piso de tamanho para COPIAR imagens (vídeos não têm piso). Um iPhone traz dezenas de
    milhares de ícones, figurinhas e .thumb de 10–50 KB: copiar isso é o que transformava o job
    em dia inteiro, e não melhora nada na tela — a thumb dessas já sai do IPED/ufdr em milissegundos.
    MIDIA_TEMP_MIN_IMAGEM_KB no .env ajusta."""
    try:
        return max(0, int((os.environ.get("MIDIA_TEMP_MIN_IMAGEM_KB") or "").strip() or 100)) * 1024
    except ValueError:
        return 100 * 1024


def _listar_candidatos(id_extracao: int) -> list[dict]:
    """Vídeos e imagens da extração com binário apontado. Vídeos primeiro (são os que sofrem na
    galeria: sem thumb e sem seek), maiores primeiro dentro de cada grupo."""
    query = """
        SELECT a.id_arquivo, a.tp_mime, a.qt_bytes, a.co_caminho_zip, a.no_arquivo
        FROM investigacao.arq_tb01_arquivos a
        WHERE a.id_extracao = %s
          AND a.co_caminho_zip IS NOT NULL AND a.co_caminho_zip <> ''
          AND (a.tp_mime LIKE 'video/%%'
               OR (a.tp_mime LIKE 'image/%%' AND coalesce(a.qt_bytes, 0) >= %s))
        ORDER BY (a.tp_mime LIKE 'video/%%') DESC, a.qt_bytes DESC NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_extracao, _min_bytes_imagem()))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _info_de_candidato(item: dict, nome_ufdr: str | None) -> dict:
    return {
        "tp_mime": item["tp_mime"],
        "caminho_zip": item["co_caminho_zip"],
        "no_arquivo": item["no_arquivo"],
        "no_arquivo_ufdr": nome_ufdr,
    }


def extrair_midia_temporaria(id_job: int, id_inquerito: int, id_extracao: int) -> None:
    """Worker do job: materializa os candidatos da extração e gera as thumbs que faltam.
    Cancelável a cada arquivo (jobs_utils.progresso devolve FL_CANCELAR)."""
    jobs_utils.marcar_processando(id_job)
    limpar_expirados()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_arquivo FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,))
            linha = cur.fetchone()
    nome_ufdr = linha[0] if linha else None

    candidatos = _listar_candidatos(id_extracao)
    total = len(candidatos)
    if not total:
        jobs_utils.concluir(id_job, metricas={"copiados": 0, "thumbs": 0, "bytes": 0})
        return

    pasta = pasta_midia_temp(id_inquerito, id_extracao)
    pasta.mkdir(parents=True, exist_ok=True)

    # O progresso vai para o banco em lotes (a cada _LOTE_PROGRESSO arquivos): com 200 mil
    # candidatos, um UPDATE por arquivo seria horas de idas ao banco disputando pool com a tela.
    # O mesmo chamado devolve FL_CANCELAR — cancelar continua funcionando, só com granularidade
    # de lote (os arquivos lentos, vídeo grande, são checados um a um de qualquer forma).
    _LOTE_PROGRESSO = 25
    copiados = erros = thumbs = 0
    bytes_copiados = 0
    zfs: dict = {}      # handles de .ufdr abertos uma única vez para o job inteiro
    try:
        if jobs_utils.progresso(id_job, processados=0, total=total):
            jobs_utils.marcar_cancelado(id_job)
            return
        for indice, item in enumerate(candidatos, start=1):
            id_arquivo = item["id_arquivo"]
            eh_video = (item["tp_mime"] or "").startswith("video/")
            # vídeo (lento, um por um) e fim de lote batem no banco; miúdo de lote só a cada 25
            if indice % _LOTE_PROGRESSO == 1 or eh_video:
                if jobs_utils.progresso(id_job, processados=indice - 1, total=total, erros=erros):
                    jobs_utils.marcar_cancelado(id_job)
                    return
            destino = pasta / _nome_copia(id_arquivo)
            try:
                if not _achar_copia(id_inquerito, id_extracao, id_arquivo):
                    info = _info_de_candidato(item, nome_ufdr)
                    bytes_copiados += _copiar_para_temp(info, destino, zfs)
                    copiados += 1
                thumb_existe = (pasta / f"{id_arquivo}.thumb.jpg").is_file()
                if eh_video and not thumb_existe and not _tem_thumb_iped(item["co_caminho_zip"]):
                    if gerar_thumb(destino, pasta / f"{id_arquivo}.thumb.jpg"):
                        thumbs += 1
            except (FileNotFoundError, OSError, ValueError,
                    zipfile.BadZipFile, gzip.BadGzipFile) as exc:
                erros += 1
                logger.warning("midia-temp: arquivo %s da extração %s não copiado: %s",
                               id_arquivo, id_extracao, exc)
            except Exception:
                erros += 1
                logger.exception("midia-temp: erro inesperado no arquivo %s da extração %s",
                                 id_arquivo, id_extracao)
    finally:
        for zf in zfs.values():
            try:
                zf.close()
            except OSError:
                pass

    jobs_utils.progresso(id_job, processados=total, total=total, erros=erros)
    jobs_utils.concluir(id_job, metricas={
        "copiados": copiados,
        "thumbs": thumbs,
        "bytes": bytes_copiados,
        "expira_em_horas": _ttl_horas(),
    })
