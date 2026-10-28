"""Texto que o IPED já produziu durante o processamento do caso — OCR de imagem/PDF e
transcrição de áudio — lido de volta em vez de refeito.

Onde estava escondido (achado ao inspecionar um caso real, ver docs/extracao_iped.md): não é
campo do índice Lucene, e por isso a v1 da Nuvem IPED não achou. São dois SQLite ao lado do
índice, na pasta `text/` do módulo do caso:

    <caso>/text/ocr-results.db     ->  CREATE TABLE ocr(id TEXT PRIMARY KEY, text TEXT)
    <caso>/text/transcriptions.db  ->  CREATE TABLE transcriptions(id TEXT PRIMARY KEY,
                                                                  text TEXT, score REAL)

A chave `id` é o **MD5 do item em maiúsculas** — exatamente o que o índice devolve em `hash`/
`md5`, e o mesmo que vai para `ARQ_TB01_ARQUIVOS.CO_HASH_MD5`. Quando o item rende mais de um
bloco de texto (página de PDF, região de imagem), o IPED grava uma linha por bloco com sufixo
`-child-N`, N começando em zero.

Por que isso importa: o analyser tem OCR (`ocr_utils.py`) e transcrição (`transcricao.py`)
próprios, que custam GPU/CPU e minutos por extração para produzir o mesmo texto que já está
gravado aqui. Ler o pronto é de graça.

Uso — uma instância por importação, fechada antes de renomear/mover a pasta do caso:

    textos = TextosDoCaso(pasta_modulo)
    try:
        ocr = textos.ocr(md5_do_item)
    finally:
        textos.fechar()

As tabelas são carregadas INTEIRAS em memória na construção (são pequenas: um caso de celular
real tem ~50 mil linhas de OCR e ~4 mil de transcrição, poucos MB) e os blocos de cada item já
são agrupados e ordenados uma única vez. Depois `ocr()`/`transcricao()` são lookup O(1) num
dict. A alternativa — uma query SQLite por item (faixa `id >= hash AND id < hash+'~'`), duas
vezes por item — virava centenas de milhares de round-trips ao SQLite para um caso de centenas
de milhares de itens, mesmo para os ~99% que não têm OCR nenhum. A conexão é fechada logo após
a carga, o que também elimina o risco de segurar a pasta no Windows durante o rename do fim da
importação.
"""

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Separa "<MD5>-child-<N>" para ordenar os blocos pelo N numérico. Ordenar como texto colocaria
# child-10 antes de child-2 e embaralharia as páginas de um PDF.
_RE_BLOCO = re.compile(r"^(?P<hash>[0-9A-Fa-f]+)(?:-child-(?P<n>\d+))?$")

# Limite do texto guardado por item. Um PDF de centenas de páginas rende megabytes de OCR, e
# TX_OCR/TX_TRANSCRICAO alimentam índices trigrama — deixar entrar inteiro incha o índice sem
# ganho de busca. Mesmo espírito do corte que ocr_utils já aplica.
LIMITE_CARACTERES = 200_000


class TextosDoCaso:
    """Leitor dos dois SQLite de texto de um caso, inteiros em memória. Tolerante a ausência:
    caso processado sem OCR nem transcrição simplesmente não tem os arquivos, e aí todo método
    devolve None."""

    def __init__(self, pasta_modulo: Path):
        self._ocr = self._carregar(pasta_modulo / "text" / "ocr-results.db", "ocr")
        self._transcricao = self._carregar(pasta_modulo / "text" / "transcriptions.db",
                                           "transcriptions")

    @staticmethod
    def _carregar(caminho: Path, tabela: str) -> dict | None:
        """Lê a tabela inteira e devolve {hash maiúsculo: texto já juntado e truncado}, com os
        blocos `-child-N` na ordem numérica. None quando o arquivo não existe ou não pôde ser
        lido — banco truncado/corrompido não derruba a importação inteira."""
        if not caminho.is_file():
            return None
        try:
            # somente leitura: o caso é evidência, e uma abertura normal criaria journal ao lado
            con = sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            logger.warning("Nuvem IPED: não foi possível abrir %s", caminho, exc_info=True)
            return None
        try:
            linhas = con.execute(f"SELECT id, text FROM {tabela}").fetchall()
        except sqlite3.Error:
            logger.warning("Nuvem IPED: não foi possível ler %s", caminho, exc_info=True)
            return None
        finally:
            con.close()

        blocos: dict[str, list[tuple[int, str]]] = {}
        for id_bruto, texto in linhas:
            m = _RE_BLOCO.match(id_bruto or "")
            if not m:
                continue
            chave = m.group("hash").upper()
            n = int(m.group("n")) if m.group("n") else -1
            parte = (texto or "").strip()
            if parte:
                blocos.setdefault(chave, []).append((n, parte))

        resultado: dict[str, str] = {}
        for chave, partes in blocos.items():
            texto = "\n".join(p for _, p in sorted(partes)).strip()
            if texto:
                resultado[chave] = texto[:LIMITE_CARACTERES]
        return resultado or None

    @staticmethod
    def _buscar(mapa: dict | None, hash_md5: str | None) -> str | None:
        if mapa is None or not hash_md5:
            return None
        return mapa.get(hash_md5.upper())

    def ocr(self, hash_md5: str | None) -> str | None:
        """Texto reconhecido pelo IPED para o item, páginas na ordem. None se não houver."""
        return self._buscar(self._ocr, hash_md5)

    def transcricao(self, hash_md5: str | None) -> str | None:
        """Transcrição do áudio feita pelo IPED. None se não houver."""
        return self._buscar(self._transcricao, hash_md5)

    @property
    def tem_ocr(self) -> bool:
        return self._ocr is not None

    @property
    def tem_transcricao(self) -> bool:
        return self._transcricao is not None

    def fechar(self) -> None:
        self._ocr = None
        self._transcricao = None
