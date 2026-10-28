"""Testes da extração temporária da galeria (funcoes/midia_temp.py) — sem banco, sem servidor.

Rodar da pasta api/:  python tools/teste_midia_temp.py

Cobre as promessas que importam para a prova e para a tela:
  1. o TTL: arquivo fresco é devolvido (e "tocado"), vencido some e vira None;
  2. a cópia se chama exatamente <id_arquivo> (sem extensão) e a thumb dela é o único vizinho
     com sufixo — a busca é um stat direto, não um glob da pasta inteira;
  3. limpar_expirados apaga só o que venceu e remove as pastas vazias;
  4. a cópia é atômica: destino nunca existe pela metade e o .tmp não sobra;
  5. o handle do .ufdr é reaberto uma única vez pelo job (o gargalo do primeiro job em
     produção era reler o diretório central do ZIP a cada arquivo);
  6. gerar_thumb (com ffmpeg, quando houver) produz JPEG pequeno a partir de um vídeo real.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MIDIA_TEMP_TTL_HORAS"] = "1"

from funcoes import midia_temp  # noqa: E402


def _preparar(raiz: Path) -> None:
    midia_temp.PASTA_DADOS = raiz


def _vencer(caminho: Path) -> None:
    passado = time.time() - 2 * 3600   # TTL é 1 h no teste
    os.utime(caminho, (passado, passado))


def teste_nome_sem_extensao(falhas: list) -> None:
    if midia_temp._nome_copia(77) != "77":
        falhas.append("a cópia deve se chamar exatamente <id_arquivo>, sem extensão")


def teste_ttl_e_busca(falhas: list) -> None:
    with tempfile.TemporaryDirectory() as td:
        _preparar(Path(td))
        pasta = midia_temp.pasta_midia_temp(1, 2)
        pasta.mkdir(parents=True)
        copia = pasta / midia_temp._nome_copia(77)
        copia.write_bytes(b"video")

        achado = midia_temp.midia_temporaria(1, 2, 77)
        if achado is None:
            falhas.append("cópia fresca deveria ser encontrada")
        elif achado.name != "77":
            falhas.append("a busca deveria achar exatamente <id_arquivo>")

        thumb = pasta / "77.thumb.jpg"
        thumb.write_bytes(b"jpeg")
        if midia_temp.thumb_temporaria(1, 2, 77) != b"jpeg":
            falhas.append("thumb temporária não lida")
        achado = midia_temp.midia_temporaria(1, 2, 77)
        if achado is not None and achado.name.endswith(".thumb.jpg"):
            falhas.append("thumb vazou como mídia na busca")

        _vencer(copia)
        if midia_temp.midia_temporaria(1, 2, 77) is not None:
            falhas.append("cópia vencida deveria valer None")
        if copia.exists():
            falhas.append("cópia vencida deveria ter sido apagada no acesso")
        _vencer(thumb)
        if midia_temp.thumb_temporaria(1, 2, 77) is not None:
            falhas.append("thumb vencida deveria valer None")


def teste_limpeza(falhas: list) -> None:
    with tempfile.TemporaryDirectory() as td:
        _preparar(Path(td))
        pasta = midia_temp.pasta_midia_temp(5, 6)
        pasta.mkdir(parents=True)
        velho = pasta / "1"
        velho.write_bytes(b"x")
        novo = pasta / "2"
        novo.write_bytes(b"x")
        _vencer(velho)

        apagados = midia_temp.limpar_expirados()
        if apagados != 1 or velho.exists() or not novo.is_file():
            falhas.append("limpar_expirados não apagou exatamente o vencido")
        if not pasta.exists():
            falhas.append("pasta que ainda tem arquivo fresco não deveria sumir")

        pasta2 = midia_temp.pasta_midia_temp(5, 7)
        pasta2.mkdir(parents=True)
        vazio = pasta2 / "9"
        vazio.write_bytes(b"x")
        _vencer(vazio)
        midia_temp.limpar_expirados()
        if pasta2.exists():
            falhas.append("pasta vazia deveria ser removida pela varredura")


def teste_copia_atomica(falhas: list) -> None:
    with tempfile.TemporaryDirectory() as td:
        _preparar(Path(td))
        destino = midia_temp.pasta_midia_temp(3, 4) / midia_temp._nome_copia(9)

        original = midia_temp._abrir_leitor
        try:
            midia_temp._abrir_leitor = lambda info, zfs: iter([b"abc", b"def"])
            total = midia_temp._copiar_para_temp({}, destino, {})
        finally:
            midia_temp._abrir_leitor = original
        if total != 6 or destino.read_bytes() != b"abcdef":
            falhas.append("cópia não saiu completa/atômica")
        if (destino.parent / "9.tmp").exists():
            falhas.append(".tmp sobrou depois da cópia")

        def _estoura_no_meio(info, zfs):
            yield b"ab"
            raise OSError("metade")
        try:
            midia_temp._abrir_leitor = _estoura_no_meio
            midia_temp._copiar_para_temp({}, destino, {})
            falhas.append("erro no meio da leitura deveria subir")
        except OSError:
            pass
        finally:
            midia_temp._abrir_leitor = original
        if (destino.parent / "9.tmp").exists():
            falhas.append(".tmp deveria sumir também na cópia que falhou")
        if destino.read_bytes() != b"abcdef":
            falhas.append("falha no meio não pode corromper a cópia anterior")


class _ZipFalso:
    def __init__(self):
        self.aberturas = 0
        self.fechado = False

    def close(self):
        self.fechado = True


def teste_zip_persistente(falhas: list) -> None:
    """O handle local é criado uma vez e fica no dicionário do job; o remoto é de uso único."""
    original = midia_temp.ufdr_zip.abrir
    falso = _ZipFalso()
    try:
        def _abrir_contado(caminho):
            falso.aberturas += 1
            return falso
        midia_temp.ufdr_zip.abrir = _abrir_contado
        zfs = {}
        h1, fecha1 = midia_temp._zip_persistente(zfs, "/data/x.ufdr")
        h2, fecha2 = midia_temp._zip_persistente(zfs, "/data/x.ufdr")
        if falso.aberturas != 1 or h1 is not h2 or fecha1 or fecha2:
            falhas.append("fonte local deveria reabrir o zip uma única vez por job")

        class _Remoto:
            eh_remoto = True
        remoto, fecha_remoto = midia_temp._zip_persistente(zfs, _Remoto())
        if not fecha_remoto:
            falhas.append("fonte remota deveria fechar o handle com o arquivo")
    finally:
        midia_temp.ufdr_zip.abrir = original


def teste_gerar_thumb(falhas: list) -> None:
    if shutil.which("ffmpeg") is None:
        return   # máquina sem ffmpeg: o caminho "sem ffmpeg → False" já é o do servidor legado
    with tempfile.TemporaryDirectory() as td:
        video = Path(td) / "v.mp4"
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=duration=1:size=640x360:rate=10", str(video)],
            capture_output=True, timeout=60)
        if r.returncode != 0:
            falhas.append("ffmpeg não conseguiu criar o vídeo de teste")
            return
        # a cópia do job NÃO tem extensão — o ffmpeg tem de sondar o conteúdo e funcionar assim
        sem_ext = Path(td) / "v"
        shutil.copyfile(video, sem_ext)
        thumb = Path(td) / "v.thumb.jpg"
        if not midia_temp.gerar_thumb(sem_ext, thumb):
            falhas.append("gerar_thumb falhou num vídeo válido sem extensão")
        elif len(thumb.read_bytes()) == 0:
            falhas.append("thumb gerada vazia")
        if midia_temp.gerar_thumb(Path(td) / "nao_existe", Path(td) / "x.jpg"):
            falhas.append("gerar_thumb deveria falhar em vídeo inexistente")


def teste_intervalo_alcance(falhas: list) -> None:
    if midia_temp._intervalo_alcance(None, 1000) != (0, 999, False):
        falhas.append("sem Range deve dar arquivo inteiro (200)")
    if midia_temp._intervalo_alcance("bytes=0-1023", 1000) != (0, 999, True):
        falhas.append("Range além do arquivo deve ser aparada no fim")
    if midia_temp._intervalo_alcance("bytes=500-", 1000) != (500, 999, True):
        falhas.append("bytes=500- deveria ir do 500 ao fim")
    if midia_temp._intervalo_alcance("bytes=-400", 1000) != (600, 999, True):
        falhas.append("bytes=-400 deveria dar os 400 últimos (o seek do .mov)")
    if midia_temp._intervalo_alcance("bytes=1200-1500", 1000) != (-1, -1, True):
        falhas.append("faixa fora do arquivo deveria pedir 416")
    if midia_temp._intervalo_alcance("bytes=0-1,4-5", 1000) != (0, 999, False):
        falhas.append("multi-intervalo deveria ser respondido como arquivo inteiro")
    if midia_temp._intervalo_alcance("bananas", 1000) != (0, 999, False):
        falhas.append("Range inválido deveria ser ignorado")


def teste_resposta_arquivo(falhas: list) -> None:
    import asyncio

    with tempfile.TemporaryDirectory() as td:
        caminho = Path(td) / "9"
        caminho.write_bytes(b"A" * 3 * 1024 * 1024)

        def _corpo(resposta) -> bytes:
            pedacos = asyncio.run(_junta(resposta.body_iterator))
            return b"".join(pedacos)

        r = midia_temp.resposta_arquivo(caminho, "video/mp4", "bytes=1048576-2097151")
        if r.status_code != 206:
            falhas.append("faixa deveria responder 206")
        if r.headers.get("content-range") != "bytes 1048576-2097151/3145728":
            falhas.append(f"Content-Range errado: {r.headers.get('content-range')}")
        if r.headers.get("accept-ranges") != "bytes":
            falhas.append("faltou Accept-Ranges")
        if _corpo(r) != b"A" * 1024 * 1024:
            falhas.append("corpo da faixa não bate com o pedido")

        cheio = midia_temp.resposta_arquivo(caminho, "video/mp4", None)
        if cheio.status_code != 200 or len(_corpo(cheio)) != 3 * 1024 * 1024:
            falhas.append("sem Range deveria devolver os 3 MiB inteiros")
        if cheio.headers.get("content-length") != str(3 * 1024 * 1024):
            falhas.append("faltou Content-Length no 200")

        fora = midia_temp.resposta_arquivo(caminho, "video/mp4", "bytes=99999999-")
        if fora.status_code != 416:
            falhas.append("faixa além do arquivo deveria dar 416")

        try:
            midia_temp.resposta_arquivo(Path(td) / "sumiu", "video/mp4", None)
            falhas.append("arquivo sumido deveria levantar OSError (e o chamador cair no .ufdr)")
        except OSError:
            pass


async def _junta(iterador) -> list:
    out = []
    async for pedaco in iterador:
        out.append(pedaco)
    return out


def main() -> int:
    falhas: list = []
    for teste in (teste_nome_sem_extensao, teste_ttl_e_busca, teste_limpeza,
                  teste_copia_atomica, teste_zip_persistente, teste_gerar_thumb,
                  teste_intervalo_alcance, teste_resposta_arquivo):
        teste(falhas)
    if falhas:
        for f in falhas:
            print("FALHA:", f)
        return 1
    print("teste_midia_temp: tudo ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
