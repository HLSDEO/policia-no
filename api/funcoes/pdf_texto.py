"""Leitura do texto de um PDF — o ponto único por onde IPL, IRPF, IPEI e os documentos avulsos
passam antes de virar texto para regex, para o banco e para a IA.

Existe por causa de um detalhe que só aparece em PDF de órgão público: quando o gerador não
escreve um ToUnicode válido, o pypdf devolve o NOME do glifo em vez do caractere, e a página
inteira sai como `/uni0049/uni004E/uni0046...` ("INF..."). O texto fica ilegível para o
investigador, inútil para a IA e invisível para qualquer regex de rótulo — a IPEI da Receita
Federal (funcoes/ipei_utils.py) é exatamente assim. Como `/uniXXXX` é o codepoint em hexa, dá
para desfazer sem perda: `/uni00C7` volta a ser 'Ç'.

`/gNNN` é o outro nome de glifo do mesmo PDF (índice na fonte, sem codepoint associado) — esse
não tem volta, então some. Os dois só são tocados quando a página tem `/uniXXXX` de verdade,
para um texto legítimo que por acaso contenha "/g120" não ser mutilado.
"""

import re
from pathlib import Path

# NUL (0x00) e demais controles saem do pypdf em PDF com ToUnicode/fonte quebrada; o Postgres
# recusa NUL em coluna TEXT (mesmo tratamento de documentos_utils._limpar_texto_para_banco).
_RE_CTRL_INVALIDO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_RE_GLIFO_UNI = re.compile(r"/uni([0-9A-Fa-f]{4})")
_RE_GLIFO_ID = re.compile(r"/g\d+")

# Um par solto ("/uni0041" no meio de uma frase) é ruído; a partir daqui a página é claramente
# uma página de nomes de glifo, e aí vale desfazer tudo — inclusive os /gNNN sem volta.
_MIN_GLIFOS = 3


def desglifar(texto: str) -> str:
    """Devolve o texto com os nomes de glifo do pypdf trocados pelos caracteres que representam."""
    if len(_RE_GLIFO_UNI.findall(texto or "")) < _MIN_GLIFOS:
        return texto or ""
    texto = _RE_GLIFO_UNI.sub(lambda m: chr(int(m.group(1), 16)), texto)
    return _RE_GLIFO_ID.sub("", texto)


def limpar(texto: str) -> str:
    """Tira surrogate solto e caractere de controle — o que o encoder UTF-8 e o Postgres recusam."""
    return _RE_CTRL_INVALIDO.sub("", (texto or "").encode("utf-8", "replace").decode("utf-8"))


def paginas_pdf(caminho: Path | str) -> list[str]:
    """O texto de cada página do PDF, já legível e já seguro para gravar."""
    from pypdf import PdfReader

    return [limpar(desglifar(pagina.extract_text() or "")) for pagina in PdfReader(str(caminho)).pages]
