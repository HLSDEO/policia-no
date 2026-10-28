"""Abertura tolerante de .ufdr com o índice final (diretório central) perdido.

Um .ufdr é um ZIP: o que o `zipfile.ZipFile` lê na abertura é o diretório central, gravado no
FIM do arquivo. Cópia para data/ interrompida perto do fim costuma deixar um arquivo com o
tamanho PRE-ALOCADO completo cujo final é zero — `zipfile` abre e morre em BadZipFile
("File is not a zip file") sem ler NADA, embora quase todo o conteúdo esteja gravado: os
cabeçalhos locais, que descrevem cada entrada, ficam ESPALHADOS pelo arquivo e sobrevivem
intactos até o ponto em que a cópia parou.

Este módulo recupera esse conteúdo: `abrir()` tenta o zipfile normal e, se o índice não
existe, varre os cabeçalhos locais e devolve um `ZipTolerante` com a mesma interface mínima
que o parser usa (namelist/infolist/getinfo/open/read). O importador então lê o que sobreviveu
e ignora o resto — em vez de recusar a prova inteira.

É modo de emergência, não validação: as entradas listadas são só as completas ANTES do ponto
de truncamento (uma entrada cortada no meio é descartada, junto com tudo que viesse depois),
e a extração que usa este caminho ganha um aviso na tela dizendo que a importação é parcial.
A leitura de cada membro ainda confere CRC e integridade do deflate: entrada podre levanta
BadZipFile, como no zipfile — quem já tratava arquivo podre por item continua pulando só ele.
"""
from __future__ import annotations

import io
import logging
import os
import struct
import zipfile
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipInfo

logger = logging.getLogger(__name__)

_ASSINATURA_LOCAL = b"PK\x03\x04"
_CAB_LOCAL = 30  # tamanho fixo do cabeçalho local, antes de nome + extra

# bits de flag do cabeçalho local que inviabilizam pular o membro sem ler os dados
_FLAG_STREAMING = 0x08   # tamanhos só no data descriptor, DEPOIS dos dados
_FLAG_CRIPTOGRAFADO = 0x40


def abrir(caminho):
    """Abre o .ufdr para leitura: um `zipfile.ZipFile` quando o índice existe, um
    `ZipTolerante` reconstruído dos cabeçalhos locais quando ele não existe (arquivo
    truncado). Levanta BadZipFile quando nem mesmo os cabeçalhos locais recoveram
    alguma coisa — aí o arquivo realmente não é um ZIP aproveitável.

    `caminho` também aceita uma FonteRemota (no_ponte) — o passaporte de um .ufdr que mora na
    pasta de um NÓ descentralizado (docs/no_descentralizado.md). Nesse caminho o ZIP abre sobre
    o `ArquivoRemoto` (leitura por faixa via HTTP) e a tolerância a truncamento não existe: a
    varredura sequencial de dezenas de GB não faz sentido por sobre a rede, e a mensagem do
    BadZipFile diz para recopiar o arquivo na delegacia. É esta única linha que faz os ~25
    `ufdr_zip.abrir` do sistema (rotas de anexo, OCR, CLIP, facial, transcrição, relatórios)
    funcionarem dos dois lados sem tocar em nenhum chamador."""
    if getattr(caminho, "eh_remoto", False):
        return caminho.abrir_zip()
    if isinstance(caminho, str) and caminho.startswith("no://"):
        # string de cache NUNCA é fonte de verdade (não carrega URL nem token): chegar aqui é
        # alguém stringificando a fonte em algum lugar — erro de programação, diz o nome certo.
        raise BadZipFile(f"fonte remota stringificada ({caminho[:80]}) — passe o objeto FonteRemota")
    try:
        return zipfile.ZipFile(str(caminho))
    except BadZipFile:
        tolerante = ZipTolerante(caminho)
        logger.warning(
            "%s: índice do ZIP ausente (arquivo truncado?) — importação parcial ativada: "
            "%s entradas recuperadas dos cabeçalhos locais, %s bytes perdidos no fim",
            caminho, len(tolerante._infos), tolerante.bytes_descartados)
        return tolerante


def _data_dos(b: bytes) -> tuple:
    """Converte time+date DOS do cabeçalho local na tupla date_time do ZipInfo."""
    t, d = struct.unpack("<HH", b)
    ano = ((d & 0xFE00) >> 9) + 1980
    mes, dia = (d & 0x01E0) >> 5, d & 0x001F
    if not (1 <= mes <= 12 and 1 <= dia <= 31 and 1980 <= ano <= 2107):
        return (1980, 1, 1, 0, 0, 0)
    return (ano, mes, dia, (t & 0xF800) >> 11, (t & 0x07E0) >> 5, (t & 0x003F) * 2)


def _tamanhos_zip64(extra: bytes) -> tuple[int | None, int | None]:
    """(uncompressed, compressed) do campo extra ZIP64 (0x0001) do cabeçalho local, quando
    presentes. O ordem no extra ZIP64 é sempre uncompressed antes de compressed, e só os
    campos que são 0xFFFFFFFF no cabeçalho aparecem aqui."""
    i = 0
    while i + 4 <= len(extra):
        mid, mlen = struct.unpack_from("<HH", extra, i)
        i += 4
        bloco = extra[i:i + mlen]
        i += mlen
        if mid == 0x0001:
            uncomp = comp = None
            j = 0
            if len(bloco) >= 8:
                uncomp = struct.unpack_from("<Q", bloco, 0)[0]
                j = 8
            if len(bloco) >= j + 8:
                comp = struct.unpack_from("<Q", bloco, j)[0]
            return uncomp, comp
    return None, None


class ZipTolerante:
    """Vista de leitura de um ZIP reconstruída apenas dos cabeçalhos locais. Implementa o
    mínimo que o importador/visualizador de .ufdr usa: namelist, infolist, getinfo, open,
    read, close (+ context manager). Os ZipInfo devolvidos são objetos zipfile.ZipInfo de
    verdade (o ufdr_cache usa header_offset/compress_type para montar janelas de ZIP
    aninhado STORED, e isso continua funcionando sem mudança)."""

    recuperado = True

    def __init__(self, caminho) -> None:
        self.filename = str(caminho)
        self._infos: dict[str, ZipInfo] = {}
        self._dados: dict[str, tuple[int, int]] = {}  # nome -> (offset dos dados, bytes comprimidos)
        self.bytes_descartados = 0
        self._escanear()
        if not self._infos:
            raise BadZipFile(
                "File is not a zip file (nenhuma entrada recuperável nos cabeçalhos locais)")

    def _escanear(self) -> None:
        tam = os.path.getsize(self.filename)
        offset = 0
        motivo = "arquivo vazio"
        with open(self.filename, "rb") as f:
            while offset + _CAB_LOCAL <= tam:
                f.seek(offset)
                cab = f.read(_CAB_LOCAL)
                if len(cab) < _CAB_LOCAL or cab[:4] != _ASSINATURA_LOCAL:
                    motivo = "fim dos dados (cabeçalho local ausente)"
                    break
                flags, metodo = struct.unpack("<HH", cab[6:10])
                crc = struct.unpack("<I", cab[14:18])[0]
                csize, usize = struct.unpack("<II", cab[18:26])
                nlen, elen = struct.unpack("<HH", cab[26:30])
                if flags & _FLAG_STREAMING:
                    # tamanho só sai depois de ler os dados até o data descriptor; a posição
                    # do próximo membro deixaria de ser confiável — para e fica o que já veio
                    motivo = "entrada em streaming (tamanho só no data descriptor)"
                    break
                resto = f.read(nlen + elen)
                if len(resto) < nlen + elen:
                    motivo = "entrada cortada no truncamento"
                    break
                nome = resto[:nlen].decode("utf-8", "replace")
                if csize == 0xFFFFFFFF or usize == 0xFFFFFFFF:
                    uncomp64, comp64 = _tamanhos_zip64(resto[nlen:])
                    if csize == 0xFFFFFFFF:
                        csize = comp64
                    if usize == 0xFFFFFFFF:
                        usize = uncomp64
                    if csize is None:
                        motivo = "tamanho ZIP64 ausente no extra"
                        break
                fim = offset + _CAB_LOCAL + nlen + elen + csize
                if fim > tam:
                    motivo = "entrada cortada no truncamento"
                    break
                info = ZipInfo(nome, date_time=_data_dos(cab[10:14]))
                info.compress_type = metodo
                info.flag_bits = flags
                info.CRC = crc
                info.file_size = usize if usize is not None else csize
                info.compress_size = csize
                info.header_offset = offset
                self._infos[nome] = info  # nome repetido: vale o último, como no zipfile
                self._dados[nome] = (offset + _CAB_LOCAL + nlen + elen, csize)
                offset = fim
        self.bytes_descartados = max(0, tam - offset)
        logger.info("%s: varredura dos cabeçalhos locais parou em %s (%s)",
                    self.filename, offset, motivo)

    # ------------------------------------------------------------------ interface

    def namelist(self) -> list[str]:
        return list(self._infos)

    def infolist(self) -> list[ZipInfo]:
        return list(self._infos.values())

    def getinfo(self, nome: str) -> ZipInfo:
        try:
            return self._infos[nome]
        except KeyError:
            raise KeyError(nome)

    def read(self, nome: str) -> bytes:
        info = self.getinfo(nome)
        if info.flag_bits & _FLAG_CRIPTOGRAFADO:
            raise BadZipFile(f"{nome}: entrada criptografada não é legível")
        offset, csize = self._dados[nome]
        with open(self.filename, "rb") as f:
            f.seek(offset)
            bruto = f.read(csize)
        if len(bruto) < csize:
            raise BadZipFile(f"{nome}: dados cortados no truncamento")
        if info.compress_type == ZIP_STORED:
            dados = bruto
        elif info.compress_type == ZIP_DEFLATED:
            d = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                dados = d.decompress(bruto) + d.flush()
            except zlib.error as exc:
                raise BadZipFile(f"{nome}: deflate corrompido ({exc})") from exc
            if len(dados) != info.file_size:
                raise BadZipFile(
                    f"{nome}: descomprimiu {len(dados)} de {info.file_size} bytes")
        else:
            raise BadZipFile(f"{nome}: método de compressão {info.compress_type} não suportado")
        if zlib.crc32(dados) & 0xFFFFFFFF != info.CRC:
            raise BadZipFile(f"{nome}: CRC não confere (entrada corrompida)")
        return dados

    def open(self, nome: str, mode: str = "r", pwd=None):
        if pwd is not None:
            raise BadZipFile("ZipTolerante não lê entradas com senha")
        # o streaming real não é possível sem índice: o membro vai inteiro para a memória.
        # É o mesmo custo do zf.read() que os chamadores faziam; os GBs do arquivo ficam no disco.
        return io.BytesIO(self.read(nome))

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
