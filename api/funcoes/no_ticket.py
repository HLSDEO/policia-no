"""Ticket de mídia direta (docs/no_midia_direta.md) — a credencial CURTA de quem assiste no navegador.

O nó das delegacias passou a servir mídia direto para o navegador do analista (quando ele está na
mesma LAN — é o caminho físico mais curto: o arquivo nasce no disco do nó e volta para a mesma
cidade, sem o rinque pelo link fino central↔nó). O navegador não tem o token do nó (e não pode
ter: é o segredo central↔nó, que autoriza extrair, gravar, apagar). O ticket resolve isso: o
CENTRAL — que autentica o usuário e confere o inquérito — assina um passe de ~10 minutos para UM
arquivo específico, e o nó valida o HMAC com o NO_TOKEN que já é seu, sem banco e sem sessão.

O que o ticket DELIBERADAMENTE não é:

- autorização ampla: o payload amarra o caminho do .ufdr e o membro exato dentro dele; um ticket
  não serve para listar a pasta nem pedir outro arquivo (o HMAC cobre o payload inteiro);
- sessão: sem estado, sem revogação fina — o TTL curto é a revogação. Revogar o NO_TOKEN ("trocar
  token" na tela) mata os tickets em aberto também, o que é exatamente o comportamento desejado;
- sigilo: o payload vai em base64url LIDO (caminhos internos da evidência ficam visíveis ao
  portador do ticket — ele já veria o conteúdo de qualquer forma). A assinatura é integridade e
  procedência, não confidencialidade.

Formato: b64url(json) + "." + hex(hmac-sha256(segredo, b64url(json))). Um segredo POR NÓ
(o TX_TOKEN/NO_TOKEN daquele registro) dá a propriedade chata e desejável: o ticket emitido para
o nó A não vale no nó B.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

TTL_PADRAO_S = 600  # 10 minutos: dá o tempo de abrir o modal e baixar um vídeo grande


class TicketInvalido(ValueError):
    """Assinatura que não bate, JSON deformado ou prazo vencido — o nó responde 403 e o navegador
    cai sozinho para o caminho de sempre (proxy do central)."""


def _b64e(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64d(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def emitir(segredo: str, payload: dict, ttl_s: int = TTL_PADRAO_S) -> str:
    """Assina o payload (dict de chaves curtas e valores JSON) com o segredo do nó e devolve o
    ticket pronto para virar query string. `x` (expiração epoch) é carimbado aqui — quem recebe
    não pode escolher o próprio prazo."""
    if not segredo:
        raise ValueError("sem segredo não há ticket a emitir")
    corpo = dict(payload)
    corpo["x"] = int(time.time()) + int(ttl_s)
    bruto = json.dumps(corpo, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    texto = _b64e(bruto).encode("ascii")
    assinatura = hmac.new(segredo.encode("utf-8"), texto, hashlib.sha256).hexdigest()
    return texto.decode("ascii") + "." + assinatura


def validar(segredo: str, ticket: str) -> dict:
    """Confere assinatura e prazo e devolve o payload. Nunca diz QUAL parte falhou por detalhe —
    um 403 seco já basta para quem está do lado de fora."""
    if not segredo:
        raise TicketInvalido("nó sem credencial configurada")
    bruto, _, assinatura = (ticket or "").rpartition(".")
    if not bruto or not assinatura:
        raise TicketInvalido("formato inválido")
    esperado = hmac.new(segredo.encode("utf-8"), bruto.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, assinatura.lower()):
        raise TicketInvalido("assinatura inválida")
    try:
        payload = json.loads(_b64d(bruto).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TicketInvalido("payload ilegível") from exc
    if not isinstance(payload, dict):
        raise TicketInvalido("payload inválido")
    try:
        expira = int(payload.get("x") or 0)
    except (TypeError, ValueError):
        raise TicketInvalido("prazo inválido")
    if expira < time.time():
        raise TicketInvalido("ticket expirado")
    return payload
