"""Teste do xml_limpo contra o defeito real que derrubou importações de .ufdr (extrações 70 e 72):
o report.xml do Cellebrite escapa emoji como par de surrogados (`&#xD83D;&#xDE00;`), ilegal em
XML 1.0, e o expat aborta tudo com "reference to invalid character number".

    docker compose exec api-1 python /app/tools/teste_xml_limpo.py
"""
import io
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes.xml_limpo import FluxoXmlSeguro, sanear  # noqa: E402

NS = 'xmlns="http://pa.cellebrite.com/report/2.0"'
CASOS = [
    ("par de surrogados vira emoji", "<a>&#xD83D;&#xDE00;</a>", "\U0001F600", True),
    ("surrogado sozinho vira U+FFFD", "<a>[&#xD83D;]</a>", "[\ufffd]", True),
    ("referência a caractere de controle", "<a>x&#0;y</a>", "x\ufffdy", True),
    ("fora do plano Unicode", "<a>&#x110000;</a>", "\ufffd", True),
    ("referência decimal inválida", "<a>&#11;</a>", "\ufffd", True),
    ("referências válidas passam", "<a>&#65;&#x1F600;&#9;</a>", "A\U0001F600\t", False),
    ("texto que só parece referência", "<a>&amp;#xD83D;</a>", "&#xD83D;", False),
    ("par decimal", "<a>&#55357;&#56832;</a>", "\U0001F600", True),
]

def ler_tudo(dados: bytes, bloco: int) -> bytes:
    f = FluxoXmlSeguro(io.BytesIO(dados), bloco=bloco)
    pedacos = []
    while True:
        pedaco = f.read(bloco)
        if not pedaco:
            return b"".join(pedacos)
        pedacos.append(pedaco)


def main():
    for nome, xml, esperado, explode in CASOS:
        saida = sanear(xml.encode())
        texto = ET.fromstring(saida).text
        assert texto == esperado, f"{nome}: {texto!r} != {esperado!r}"
        # o crú explode sem saneamento (é exatamente o erro das extrações 70 e 72)
        if explode:
            try:
                ET.fromstring(xml.encode())
                raise AssertionError(f"{nome}: era para explodir sem saneamento")
            except ET.ParseError as e:
                assert "invalid character number" in str(e), e
        print(f"sanear: {nome}")

    # streaming: blocos pequenos forçam o corte em toda posição possível de uma referência,
    # incluindo entre as metades do par de surrogados
    bruto = (f'<project {NS}>'
             + "".join(f'<item name="m{i}">a&#xD83D;&#xDE00;b&#0;c&#65;d</item>' for i in range(50))
             + "</project>").encode()
    texto_esperado = "a\U0001F600b\ufffdcAd" * 50
    for bloco in range(1, 40):
        saida = ler_tudo(bruto, bloco)
        raiz = ET.fromstring(saida)
        textos = [e.text for e in raiz]
        assert "".join(textos) == texto_esperado, f"bloco={bloco} corrompeu o texto"
    print(f"streaming: {len(bruto)} bytes parseados íntegros em blocos de 1 a 39")

    # relatório grande: 2 MB de texto limpo + referências no meio e no fim do arquivo
    recheio = b"x" * (2 * 1024 * 1024)
    bruto = f'<project {NS}><item>'.encode() + recheio + b"&#xD83D;&#xDE00;</item></project>"
    raiz = ET.fromstring(ler_tudo(bruto, 65536))
    assert raiz[0].text.endswith("\U0001F600"), "emoji no fim do arquivo se perdeu"
    print("streaming: referência no rabo de um relatório de 2 MB preservada")
    print("OK")


if __name__ == "__main__":
    main()
