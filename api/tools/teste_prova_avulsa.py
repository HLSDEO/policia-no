"""Ferramenta de verificação da prova avulsa melhorada — sem banco, sem servidor.

Rodar da pasta api:  python tools/teste_prova_avulsa.py

Cobre o que não depende de requisição nem de Postgres:
- assinatura (magic bytes): PDF/PNG/JPG verdadeiros passam, extensão que mente (.pdf que é
  executável, .docx que é texto) vira falha nomeada;
- renomeio por colisão: segundo arquivo com o mesmo nome entra como "nome (2).ext" e a
  resposta marca renomeado=True (o front avisa);
- ZIP: entradas suportadas (inclusive imagem) são gravadas, a que mente na assinatura e a
  de tipo não suportado viram "ignorados";
- imagem como prova: extrair_texto_documento devolve (None, None) — sem texto POR DESIGN;
- lixeira: mover_para_lixeira carimba o tempo no nome, expurgar_lixeira só toca no velho;
- uploads órfãos: só o solto velho, de extensão de upload e sem registro sai da pasta de
  entrega — o novo, o referenciado e o .ufdr ficam onde estão.

Usa pasta temporária no lugar de data/ (patch nos módulos) e não escreve em lugar nenhum.
"""

import io
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import documentos_utils, higiene_data  # noqa: E402
from funcoes.documentos_utils import (  # noqa: E402
    _assinatura_bate,
    _expandir_zipfile,
    extrair_texto_documento,
    mover_para_lixeira,
    salvar_documento_enviado,
)

PDF = b"%PDF-1.7\n...prova..."
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
DOCX_FALSO = b"isto e um txt renomeado para docx"
MENTIROSO = b"MZ\x90\x00" + b"\x00" * 40  # cara de executavel

FALHAS = []


def checa(cond, rotulo):
    print(("  ok  " if cond else "  FALHA  ") + rotulo)
    if not cond:
        FALHAS.append(rotulo)


def main() -> int:
    raiz = Path(tempfile.mkdtemp(prefix="teste_prova_avulsa_"))
    documentos_utils.PASTA_DADOS = raiz
    higiene_data.PASTA_DADOS = raiz
    higiene_data._nomes_referenciados = lambda: {"referenciado.pdf"}
    print(f"data/ falsa em {raiz}\n")

    print("assinatura (magic bytes):")
    for nome, bruto, esperado in [
        ("a.pdf", PDF, True), ("a.png", PNG, True), ("a.jpg", JPG, True),
        ("a.txt", b"qualquer texto", True),  # tipo sem assinatura: passa sempre
        ("a.pdf", MENTIROSO, False), ("a.docx", DOCX_FALSO, False), ("a.png", PDF, False),
    ]:
        alvo = raiz / f"sig_{nome}"
        alvo.write_bytes(bruto)
        checa(_assinatura_bate(alvo) is esperado, f"{nome} assinatura={esperado}")

    print("\nupload com renomeio e assinatura:")
    salgo1 = salvar_documento_enviado(io.BytesIO(PDF), "laudo.pdf")
    checa(salgo1[0]["nome"] == "laudo.pdf" and salgo1[0]["renomeado"] is False,
          "primeiro laudo.pdf entra com o nome pedido")
    salgo2 = salvar_documento_enviado(io.BytesIO(PDF), "laudo.pdf")
    checa(salgo2[0]["nome"] == "laudo (2).pdf" and salgo2[0]["renomeado"] is True,
          "segundo laudo.pdf entra como 'laudo (2).pdf' marcado como renomeado")
    try:
        salvar_documento_enviado(io.BytesIO(MENTIROSO), "malware.pdf")
        checa(False, "extensao mentirosa recusada")
    except ValueError as exc:
        checa("não bate com a extensão" in str(exc), f"extensao mentirosa recusada: {exc}")
    sobras = [p.name for p in raiz.iterdir() if p.is_file() and "malware" in p.name]
    checa(not sobras, "o recusado não deixou meia-cópia em data/")
    salgo_img = salvar_documento_enviado(io.BytesIO(PNG), "print.png")
    checa(salgo_img[0]["nome"] == "print.png", "PNG entra como prova avulsa")
    texto, erro = extrair_texto_documento(raiz / "print.png")
    checa(texto is None and erro is None, "imagem extrai (None, None): sem texto por design")

    print("\nZIP: suportados dentro, mentirosos fora:")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dentro/ok.pdf", PDF)
        zf.writestr("foto.png", PNG)
        zf.writestr("mentiroso.pdf", MENTIROSO)
        zf.writestr("planilha.docx", DOCX_FALSO)
        zf.writestr("binario.exe", MENTIROSO)
    with zipfile.ZipFile(buf) as zf:
        pasta_zip = raiz / "zip_alvo"
        pasta_zip.mkdir()
        salvos, ignorados, truncado = _expandir_zipfile(zf, pasta_zip)
    nomes = {s["nome"] for s in salvos}
    checa(nomes == {"ok.pdf", "foto.png"}, f"ZIP gravou só os verdadeiros: {sorted(nomes)}")
    checa(ignorados == 3 and not truncado, f"ZIP ignorou mentiroso+docx+exe (ignorados={ignorados})")

    print("\nlixeira e órfãos:")
    prova = raiz / "100" / "OUTROS"
    prova.mkdir(parents=True)
    (prova / "removido.pdf").write_bytes(PDF)
    destino = mover_para_lixeira(prova / "removido.pdf", "7")
    checa(destino is not None and "__removido.pdf" in destino.name
          and not (prova / "removido.pdf").exists(),
          f"documento removido foi para a lixeira carimbado: {destino.name}")

    (raiz / "abandonado.pdf").write_bytes(PDF)
    (raiz / "novo.pdf").write_bytes(PDF)
    (raiz / "referenciado.pdf").write_bytes(PDF)
    (raiz / "extracao.ufdr").write_bytes(b"ufdr antigo")
    velho = time.time() - 30 * 86400
    for nome in ("abandonado.pdf", "referenciado.pdf", "extracao.ufdr"):
        os.utime(raiz / nome, (velho, velho))
    movidos, erros = higiene_data.limpar_uploads_orfaos()
    checa(movidos == 1 and erros == 0, f"so o upload velho e sem registro saiu da raiz (movidos={movidos})")
    checa((raiz / "novo.pdf").is_file(), "upload novo (dentro do prazo) ficou")
    checa((raiz / "referenciado.pdf").is_file(), "nome referenciado no banco ficou")
    checa((raiz / "extracao.ufdr").is_file(), ".ufdr nunca e tocado pela higiene")
    orfaos = list((raiz / "_lixeira" / "uploads").iterdir())
    checa(len(orfaos) == 1 and orfaos[0].name.endswith("__abandonado.pdf"),
          f"o órfão está na lixeira carimbado, não apagado: {[p.name for p in orfaos]}")

    os.utime(destino, (velho, velho))
    for p in orfaos:  # o que acabou de cair na lixeira conta como novo: ainda não vence hoje
        os.utime(p, None)
    apagados = higiene_data.expurgar_lixeira()
    checa(apagados == 1 and not destino.exists(), "lixeira vencida foi expurgada")
    vivos = list((raiz / "_lixeira" / "uploads").iterdir())
    checa(len(vivos) == 1 and vivos[0].name.endswith("__abandonado.pdf"),
          "o órfão recém-movido (dentro do prazo) continua recuperável")

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("tudo certo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
