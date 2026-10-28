"""Extração e consulta de redes Wi-Fi salvas no dispositivo (UFDR WirelessNetwork)."""

from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import obter_id_inquerito_de_extracao


def listar_redes_wifi(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista as redes Wi-Fi detectadas no UFDR de um inquérito (ou de uma extração específica)."""
    filtros = ["e.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("w.id_extracao = %s")
        params.append(id_extracao)
    where = " AND ".join(filtros)
    query = f"""
        SELECT w.id_rede_wifi, w.id_extracao, w.no_ssid, w.no_bssid, w.tp_seguranca,
               w.co_senha, w.qt_conexoes, w.dt_primeira_conexao, w.dt_ultima_conexao,
               w.nr_canal, w.nr_sinal, w.in_rede_oculta, w.tp_autenticacao,
               w.no_fabricante, w.in_favorita, w.no_fonte
        FROM investigacao.wifi_tb01_redes w
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = w.id_extracao
        WHERE {where}
        ORDER BY w.qt_conexoes DESC NULLS LAST, w.no_ssid NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def status_wifi_extracao(id_extracao: int) -> dict | None:
    """Devolve {id_inquerito, qt_redes} da extração (qt_redes = None se nunca escaneada)."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT qt_redes_wifi FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
    qt = row[0] if row else None
    return {"id_inquerito": id_inquerito, "qt_redes": qt or 0, "escaneado": qt is not None}
