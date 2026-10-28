"""Protocolo de quadros do túnel reverso NAT (docs/no_descentralizado.md, seção "túnel NAT").

O desenho: o NÓ abre um WebSocket para o CENTRAL (nó → central a delegacia sempre alcança, mesmo
atrás de NAT) e o central usa esse cano único para fazer as chamadas centrais → nó que hoje vão
direto à `TX_URL_BASE` (rota de leitura de mídia, listagem, upload repassado, hash, parse). O
transporte é quadros binários multiplexados por um `req_id` — vários pedidos simultâneos no mesmo
soquete, com body em streaming nos dois sentidos.

Formato do quadro (tudo big-endian):

    [1B tipo][2B len(id)][id utf-8][4B len(meta)][meta JSON][payload cru]

`meta` é sempre um JSON dict (vazio quando não há); o `payload` só existe nos quadros de corpo
(CHU) e no REQ (primeiro pedaço do body, quase sempre vazio). Um quadro por frame de WebSocket —
a biblioteca já dá bordas de mensagem, não há prefixo de comprimento no stream.

Os tipos seguem a numeração de `_REQ/_CHU/...` abaixo e o SIGNIFICADO depende da direção:

- central → nó:  REQ (abre pedido: meta = {m,p,q,h}), CHU (pedaço do corpo do pedido),
  FIM (corpo do pedido acabou), CAN (cancela o pedido em curso);
- nó → central:  RESP (começou a resposta: meta = {s,h}), CHU (pedaço do corpo da resposta),
  FIM (resposta completa), ERR (a ponte local falhou: meta = {d}), PING/PONG (batimento do cano).

Nada aqui conhece FastAPI, requests ou banco — é puro `struct`/`json`, usado tanto pelo serviço
`tunel` (relay HTTP↔WS) quanto pelo cliente dentro do nó.
"""

from __future__ import annotations

import json
import struct

# tipos de quadro (1B). Os números são estáveis no fio: nunca renumerar.
REQ = 1    # central→nó: abre pedido (meta {m: método, p: caminho, q: query, h: headers})
CHU = 2    # corpo em streaming, nas duas direções
FIM = 3    # corpo acabou / resposta completa
RESP = 4   # nó→central: cabeçalho da resposta (meta {s: status, h: headers}) — abre o streaming
CAN = 5    # central→nó: desiste do pedido (o nó aborta a leitura local)
PING = 6   # nó→central: "cano vivo" (o relay batiza DT_TUNEL_ATIVO de novo)
PONG = 7   # central→nó: eco opcional do PING
ERR = 9    # nó→central: a chamada local falhou (meta {d: detalhe})


def codificar(tipo: int, req_id: str, meta: dict | None = None, payload: bytes = b"") -> bytes:
    """Monta um quadro. `meta` vira JSON compacto; `payload` vai cru (binário seguro)."""
    id_b = req_id.encode("utf-8")
    meta_b = json.dumps(meta or {}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return (struct.pack(">BH", tipo, len(id_b)) + id_b
            + struct.pack(">I", len(meta_b)) + meta_b + (payload or b""))


def decodificar(buf: bytes | bytearray | memoryview) -> tuple[int, str, dict, bytes]:
    """Separa um quadro em (tipo, req_id, meta, payload). Levanta ValueError em quadro malformado
    — quem recebe descarta a conexão inteira, não tem recuperação parcial."""
    mv = memoryview(buf)
    if len(mv) < 3:
        raise ValueError("quadro curto")
    tipo, len_id = struct.unpack(">BH", mv[:3])
    if len(mv) < 3 + len_id + 4:
        raise ValueError("quadro truncado no id")
    req_id = bytes(mv[3:3 + len_id]).decode("utf-8")
    off = 3 + len_id
    (len_meta,) = struct.unpack(">I", mv[off:off + 4])
    off += 4
    if len(mv) < off + len_meta:
        raise ValueError("quadro truncado na meta")
    try:
        meta = json.loads(bytes(mv[off:off + len_meta]) or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"meta ilegível: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("meta não é dict")
    off += len_meta
    return tipo, req_id, meta, bytes(mv[off:])
