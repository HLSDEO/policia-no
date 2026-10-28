# -*- coding: utf-8 -*-
"""Contadores de robustez do caminho central ↔ nó (docs/no_descentralizado.md).

Só isto: um dicionário de contadores por processo, incrementado nos pontos onde a
robustez foi projetada para DEGRADAR em vez de quebrar — reenvio de lote, callback de
estado perdido, quarentena, requisição abortada no relay, heartbeat que não chegou.
Sem esses números, tudo isso acontece em silêncio e o operador só vê a consequência
dias depois (disco cheio, extração presa, nó "verde porém cego").

Leve por construção: dict + GIL (contadores aproximados são exatamente o que a tela
precisa — nada aqui decide execução, ver a mesma filosofia de no_extracao.fila_estado).
Sai em GET /no/estado ("metricas") no nó e em GET /tunel/saude no central.
"""
from __future__ import annotations

_CONTADORES: dict[str, int] = {}


def incr(nome: str, n: int = 1) -> None:
    _CONTADORES[nome] = _CONTADORES.get(nome, 0) + n


def zera(nome: str) -> None:
    _CONTADORES.pop(nome, None)


def snapshot() -> dict[str, int]:
    return dict(_CONTADORES)
