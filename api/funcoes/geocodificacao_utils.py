"""Geocodificação reversa (coordenada -> endereço) sob demanda, via OpenStreetMap/Nominatim.

Acionada pelo botão "Buscar endereços" da página de Localizações — nunca automática. O resultado
fica no cache global GEO_TB01_ENDERECOS (por coordenada arredondada a 5 casas, ~1 m), então cada
coordenada é consultada no serviço externo uma única vez; repetições, jitter de GPS do mesmo ponto e
páginas futuras reaproveitam o cache. Todo o arredondamento é feito no SQL (round(x,5)) — igual ao do
JOIN em listar_localizacoes — para os dois lados usarem a mesma regra de desempate.

Respeita as regras de uso do Nominatim: no máximo 1 requisição por segundo e User-Agent identificável.
"""

import time

import requests

from funcoes.db_utils import get_connection

# endpoint público de geocodificação reversa do OpenStreetMap
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# o Nominatim exige um User-Agent identificável (bloqueia requisições sem um); política de uso pede
# 1 req/s no máximo — respeitado com o sleep entre chamadas em geocodificar_coordenadas()
_HEADERS = {"User-Agent": "analyser-forense/1.0 (geocodificacao reversa de localizacoes)"}
_TIMEOUT = 10          # segundos por requisição HTTP ao Nominatim
_INTERVALO = 1.1       # segundos entre chamadas (folga sobre o limite de 1 req/s)
# teto de coordenadas NOVAS (não cacheadas) geocodificadas por chamada — limita a duração da requisição
# (a ~1 req/s) bem abaixo do timeout do proxy; o que sobrar é resolvido nas chamadas seguintes do laço
LIMITE_POR_CHAMADA = 25


def _reverse_nominatim(lat: float, lon: float) -> str | None:
    """Consulta o Nominatim e devolve o endereço formatado (display_name) ou None se não resolver."""
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"format": "jsonv2", "lat": lat, "lon": lon, "accept-language": "pt-BR", "zoom": 18},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        return (resp.json() or {}).get("display_name") or None
    except (requests.RequestException, ValueError):
        return None


def geocodificar_coordenadas(coordenadas: list[list[float]]) -> dict:
    """Geocodifica (reversa) as coordenadas informadas, usando/atualizando o cache GEO_TB01.

    `coordenadas` é uma lista de [lat, lon] (as coordenadas cruas das linhas). O arredondamento a 5
    casas e a deduplicação são feitos no SQL. Processa no máximo LIMITE_POR_CHAMADA coordenadas novas
    por chamada; o frontend repete a chamada até `restantes` chegar a 0 e então recarrega a tabela
    (os endereços aparecem pelo LEFT JOIN, sem casamento de chave no cliente).

    Devolve {total, cacheadas, geocodificadas, restantes} (contagens de coordenadas distintas)."""
    lats: list[float] = []
    lons: list[float] = []
    for par in coordenadas:
        try:
            lats.append(float(par[0]))
            lons.append(float(par[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if not lats:
        return {"total": 0, "cacheadas": 0, "geocodificadas": 0, "restantes": 0}

    with get_connection() as conn:
        with conn.cursor() as cur:
            # coordenadas distintas (arredondadas a 5 casas no SQL) e quais ainda não estão no cache
            cur.execute(
                """
                WITH pedidos AS (
                    SELECT DISTINCT round(t.lat::numeric, 5) AS lat, round(t.lon::numeric, 5) AS lon
                    FROM unnest(%s::numeric[], %s::numeric[]) AS t(lat, lon)
                )
                SELECT p.lat, p.lon,
                       EXISTS (SELECT 1 FROM investigacao.geo_tb01_enderecos g
                               WHERE g.vl_latitude = p.lat AND g.vl_longitude = p.lon) AS cacheada
                FROM pedidos p
                """,
                (lats, lons),
            )
            linhas = cur.fetchall()
            total = len(linhas)
            pendentes = [(lat, lon) for lat, lon, cacheada in linhas if not cacheada]
            cacheadas = total - len(pendentes)
            restantes = max(0, len(pendentes) - LIMITE_POR_CHAMADA)

            geocodificadas = 0
            for lat, lon in pendentes[:LIMITE_POR_CHAMADA]:
                if geocodificadas > 0:
                    time.sleep(_INTERVALO)   # espaça as chamadas externas (1 req/s)
                endereco = _reverse_nominatim(float(lat), float(lon))
                cur.execute(
                    """INSERT INTO investigacao.geo_tb01_enderecos (vl_latitude, vl_longitude, tx_endereco, no_fonte)
                       VALUES (%s, %s, %s, 'nominatim')
                       ON CONFLICT ON CONSTRAINT uq50_geo_tb01_coord
                       DO UPDATE SET tx_endereco = EXCLUDED.tx_endereco,
                                     no_fonte = EXCLUDED.no_fonte,
                                     dt_geocodificacao = now()""",
                    (lat, lon, endereco),
                )
                geocodificadas += 1
        conn.commit()

    return {"total": total, "cacheadas": cacheadas, "geocodificadas": geocodificadas, "restantes": restantes}
