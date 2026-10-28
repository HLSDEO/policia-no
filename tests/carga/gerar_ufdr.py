# -*- coding: utf-8 -*-
"""Monta um .ufdr sintético em memória (bytes) ou em disco.

É o mesmo formato validado no teste E2E do nó (TESTE_NO.ufdr): um zip com um único
report.xml no namespace da Cellebrite, com N conversas Chat e M InstantMessage cada.
Serve duas coisas: o seed (cada usuário CARGA extrai um) e o cenário B do Locust
(tempestade de ingestão — cada iteração gera o seu, com nome único).
"""
import io
import zipfile

NS = "http://pa.cellebrite.com/report/2.0"


def montar_ufdr_bytes(conversas: int = 2, mensagens: int = 50, sufixo: str = "carga") -> bytes:
    """Devolve o .ufdr pronto (bytes) com `conversas` × `mensagens` mensagens de teste."""
    partes = [f'<project xmlns="{NS}" id="p1">',
              '<metadata section="Extraction Data">',
              '<item name="DeviceInfoSelectedManufacturer">Apple</item>',
              '<item name="LastUsedMSISDN">5527999990000</item>',
              '</metadata>', '<decodedData>']
    for c in range(conversas):
        partes.append(f'<model type="Chat" id="c{c}" deleted_state="Intact">')
        partes.append('<field name="Source"><value type="String">WhatsApp</value></field>')
        partes.append(f'<field name="Id"><value type="String">{sufixo}{c}@g.us</value></field>')
        partes.append('<multiModelField name="Messages">')
        for m in range(mensagens):
            partes.append(f'<model type="InstantMessage" id="m{c}_{m}">')
            partes.append(f'<field name="Body"><value type="String">mensagem carga {c}-{m}</value></field>')
            partes.append('</model>')
        partes.append('</multiModelField></model>')
    partes.append('</decodedData></project>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.xml", "".join(partes))
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    destino = sys.argv[1] if len(sys.argv) > 1 else "exemplo.ufdr"
    with open(destino, "wb") as f:
        f.write(montar_ufdr_bytes())
    print("gerado:", destino)
