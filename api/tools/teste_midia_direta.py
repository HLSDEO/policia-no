"""Verificação de ponta a ponta da MÍDIA DIRETA no nó (docs/no_midia_direta.md).

Roda dentro do container do nó, sem banco e sem dependência de teste (nada de httpx — as
funções de rota são chamadas direto, o Request é um boneco):

    docker exec analyser-no python tools/teste_midia_direta.py

Cobre o ticket (emissão/validação/adulteração/prazo/origem) e o GET /no/midia num .ufdr de
mentirinha: membro STORED (inteiro e com Range do meio, como faz um <video>), 416, DEFLATE em
fluxo, aninhado ZIP::interno (nuvem), download anexado e as recusas (ticket de outro nó,
caminho fora da pasta, membro inexistente, ticket deformado).
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOKEN = "segredo-do-no-para-teste"
# FORÇA o valor (não é setdefault): dentro do container o NO_TOKEN real existe, e a rota lê o
# ambiente a cada validação — sem sobrescrever, o ticket de teste nascia de um segredo e o nó
# validava com outro. Só o ambiente deste processo é tocado; o .env do nó não muda.
os.environ["NO_TOKEN"] = TOKEN

from fastapi import HTTPException  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402

from funcoes import no_ticket  # noqa: E402
from rotas import no_servico  # noqa: E402

falhas: list[str] = []


def caso(nome: str, ok: bool, detalhe: str = "") -> None:
    print(("  ok   " if ok else "  FALHA") + " " + nome + (f" — {detalhe}" if detalhe else ""))
    if not ok:
        falhas.append(nome)


def teste_ticket() -> None:
    passe = no_ticket.emitir(TOKEN, {"r": "SMT/x.ufdr", "i": "a/b.mp4"}, ttl_s=60)
    caso("ida e volta do ticket", no_ticket.validar(TOKEN, passe)["i"] == "a/b.mp4")
    try:
        no_ticket.validar("outro-segredo", passe)
        caso("outro nó recusa", False)
    except no_ticket.TicketInvalido:
        caso("outro nó recusa", True)
    corpo, _, sig = passe.rpartition(".")
    adulterado = corpo + "." + ("0" if sig != "0" else "1") + sig[1:]
    try:
        no_ticket.validar(TOKEN, adulterado)
        caso("assinatura adulterada recusa", False)
    except no_ticket.TicketInvalido:
        caso("assinatura adulterada recusa", True)
    vencido = no_ticket.emitir(TOKEN, {"r": "x"}, ttl_s=-5)
    try:
        no_ticket.validar(TOKEN, vencido)
        caso("prazo vencido recusa", False)
    except no_ticket.TicketInvalido:
        caso("prazo vencido recusa", True)


class _Req:
    """O mínimo de Request que /no/midia lê: só o header Range."""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


def _pedir(destino: str, ufdr: str, extra: dict | None = None, range_hdr: str | None = None):
    """Chama a rota e devolve (status, headers, corpo) — HTTPException vira status também."""
    payload = {"r": ufdr, "i": destino, "t": "application/octet-stream"}
    payload.update(extra or {})
    tk = no_ticket.emitir(TOKEN, payload)
    try:
        resp = no_servico.midia_direta(_Req({"range": range_hdr} if range_hdr else None), tk=tk)
    except HTTPException as exc:
        return exc.status_code, {}, b""
    if isinstance(resp, Response) and not isinstance(resp, StreamingResponse):
        return resp.status_code, dict(resp.headers), resp.body or b""
    import asyncio

    async def _colhe():
        pedaco = b"".join([p async for p in resp.body_iterator])
        return pedaco

    corpo = asyncio.run(_colhe())
    return resp.status_code, dict(resp.headers), corpo


def monta_ufdr(pasta: Path) -> str:
    nome = "SMT-2026/aparelho.ufdr"
    alvo = pasta / nome
    alvo.parent.mkdir(parents=True, exist_ok=True)
    video = bytes((i * 7) % 256 for i in range(3 * 1024 * 1024))
    doc = ("documento de teste " * 5000).encode("utf-8")
    interno = b"membro dentro do zip dentro do ufdr" * 100
    from io import BytesIO

    balde = BytesIO()
    with zipfile.ZipFile(balde, "w") as zi:
        zi.writestr("int/ok.bin", interno, compress_type=zipfile.ZIP_STORED)
    with zipfile.ZipFile(alvo, "w") as zf:
        zf.writestr("canais/video.mp4", video, compress_type=zipfile.ZIP_STORED)
        zf.writestr("docs/doc.txt", doc, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("files/Archives/baldo.zip", balde.getvalue(),
                    compress_type=zipfile.ZIP_STORED)
    return nome, video, doc, interno


def main() -> int:
    pasta = Path(os.environ.get("PASTA_INVESTIGACAO") or tempfile.mkdtemp(prefix="md-teste-"))
    print(f"pasta de dados: {pasta}")
    teste_ticket()
    ufdr, video, doc, interno = monta_ufdr(pasta)

    st, hd, corpo = _pedir("canais/video.mp4", ufdr)
    caso("STORED inteiro", st == 200 and corpo == video, f"{st}, {len(corpo)} bytes")
    caso("STORED anuncia Accept-Ranges", hd.get("accept-ranges") == "bytes")

    st, hd, corpo = _pedir("canais/video.mp4", ufdr,
                           range_hdr="bytes=1000000-1000999")
    caso("Range do meio (como faz o <video>)",
         st == 206 and corpo == video[1000000:1001000]
         and hd.get("content-range") == f"bytes 1000000-1000999/{len(video)}",
         f"{st} {hd.get('content-range')}")

    st, hd, _ = _pedir("canais/video.mp4", ufdr, range_hdr=f"bytes={len(video) + 10}-")
    caso("Range além do fim devolve 416", st == 416)

    st, _, corpo = _pedir("docs/doc.txt", ufdr)
    caso("DEFLATE em fluxo", st == 200 and corpo == doc)

    st, _, corpo = _pedir("files/Archives/baldo.zip::int/ok.bin", ufdr)
    caso("aninhado ZIP::interno (nuvem)", st == 200 and corpo == interno, str(st))

    st, hd, _ = _pedir("canais/video.mp4", ufdr, extra={"d": "1", "n": "video.mp4"})
    caso("download anexa o nome", st == 200 and "attachment" in hd.get("content-disposition", ""))

    tk_outro = no_ticket.emitir("token-de-outro-no", {"r": ufdr, "i": "canais/video.mp4"})
    try:
        no_servico.midia_direta(_Req(), tk=tk_outro)
        caso("ticket de outro nó é recusado", False)
    except HTTPException as exc:
        caso("ticket de outro nó é recusado", exc.status_code == 403, str(exc.status_code))

    st, _, _ = _pedir("../../etc/passwd", ufdr)
    caso("caminho fora da pasta é recusado", st in (403, 404), str(st))

    st, _, _ = _pedir("canais/nao-existe.mp4", ufdr)
    caso("membro inexistente é 404", st == 404)

    try:
        no_servico.midia_direta(_Req(), tk="lixo-sem-ponto")
        caso("ticket deformado não explode", False)
    except HTTPException as exc:
        caso("ticket deformado não explode", exc.status_code == 403)

    print(f"\n{len(falhas)} falha(s)" + (": " + ", ".join(falhas) if falhas else ""))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
