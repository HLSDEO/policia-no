"""Dados de contas na nuvem sem equivalente no UFDR: Minha Atividade, acessos à conta
(brutos/agregados/conexões entre identificadores), dispositivos conhecidos, perfil da
conta e agenda. Tabelas genéricas (ATV_TB01/ACS_TB01-03/DVC_TB01/PRF_TB01/EVT_TB01, ver
db/init/29_nuvem.sql) — ID_EXTRACAO aponta pra INQ_TB02_EXTRACOES, NO_FONTE rotula
a origem ("Google (MyActivity)", ...). Hoje só o Google Takeout grava aqui
(funcoes/google_takeout_utils.py); nuvem Microsoft/Apple, quando existir, importa os
mesmos _gravar_* e as mesmas telas (atividades.html, acessos.html, ...) funcionam sem
mudança nenhuma.

Cada categoria segue o padrão de listar_paginas_visitadas/facetas_navegacao/
resumo_navegacao (investigacao_utils.py): filtros (busca, campo específico, data_de/
data_ate, periodo), paginação no servidor, resumo com KPIs pra tela."""

import re

from funcoes import canal_extracao
from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import LIMITE_MAX_LISTAGEM, _PERIODOS_ARQUIVO, _hora_local_sql

_RE_DATA_ISO = re.compile(r"^\d{4}-\d\d-\d\d$")


def _filtro_periodo_data(filtros, params, coluna, data_de, data_ate, periodo):
    """Filtros comuns de data_de/data_ate/periodo, aplicados a `coluna` (timestamp)."""
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append(f"{coluna} >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append(f"{coluna} < (%s::date + 1)")
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_hora_local_sql(coluna)} BETWEEN %s AND %s")
        params.extend([hora_ini, hora_fim])


# =============================================================================
# 01 · Minha Atividade
# =============================================================================

def _filtros_atividades(id_extracao, busca, produto, data_de, data_ate, periodo):
    filtros = ["ex.id_inquerito = %s"]
    params = []
    if id_extracao is not None:
        filtros.append("a.id_extracao = %s")
        params.append(id_extracao)
    if produto:
        filtros.append("a.tx_produto = %s")
        params.append(produto)
    _filtro_periodo_data(filtros, params, "a.dt_atividade", data_de, data_ate, periodo)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(a.tx_descricao ILIKE %s OR a.tx_url ILIKE %s)")
        params.extend([termo, termo])
    return filtros, params


def listar_atividades_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, ordenar: str | None = None, pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, os registros de Minha Atividade de uma conta na nuvem.

    Filtros: id_extracao, busca (descrição/URL), produto (tx_produto exato), data_de/
    data_ate ('AAAA-MM-DD'), periodo ∈ madrugada|manha|tarde|noite. ordenar ∈
    recentes|antigos (default recentes). Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros, params_filtro = _filtros_atividades(id_extracao, busca, produto, data_de, data_ate, periodo)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    order_by = "a.dt_atividade ASC NULLS LAST" if ordenar == "antigos" else "a.dt_atividade DESC NULLS LAST"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.atv_tb01_atividades a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT a.id_atividade, a.dt_atividade, a.tx_produto, a.tx_descricao, a.tx_url, a.no_fonte
                    FROM investigacao.atv_tb01_atividades a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def facetas_atividades_conta(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Produtos distintos, para o select de filtro."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT a.tx_produto FROM investigacao.atv_tb01_atividades a
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR a.id_extracao = %s)
                     AND a.tx_produto IS NOT NULL AND a.tx_produto <> ''
                   ORDER BY a.tx_produto""",
                (id_inquerito, id_extracao, id_extracao),
            )
            return {"produtos": [linha[0] for linha in cur.fetchall()]}


def resumo_atividades_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None,
) -> dict:
    """KPIs: total, período coberto, top produtos, distribuição por período do dia."""
    filtros, params_filtro = _filtros_atividades(id_extracao, busca, produto, data_de, data_ate, periodo)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    base_from = f"""FROM investigacao.atv_tb01_atividades a
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
        WHERE {where}"""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), min(a.dt_atividade), max(a.dt_atividade) {base_from}", params)
            total, primeira, ultima = cur.fetchone()
            cur.execute(
                f"""SELECT coalesce(nullif(a.tx_produto, ''), 'Não identificado') AS produto, count(*) AS registros
                    {base_from} GROUP BY 1 ORDER BY registros DESC LIMIT 12""",
                params,
            )
            top_produtos = [{"produto": l[0], "registros": l[1]} for l in cur.fetchall()]
            cur.execute(
                f"""SELECT CASE
                        WHEN hora BETWEEN 0 AND 5 THEN 'madrugada' WHEN hora BETWEEN 6 AND 11 THEN 'manha'
                        WHEN hora BETWEEN 12 AND 17 THEN 'tarde' ELSE 'noite' END AS periodo_dia, count(*)
                    FROM (SELECT {_hora_local_sql('a.dt_atividade')} AS hora {base_from} AND a.dt_atividade IS NOT NULL) t
                    GROUP BY 1""",
                params,
            )
            por_periodo_dia = {l[0]: l[1] for l in cur.fetchall()}
    return {
        "total": total, "primeira": primeira, "ultima": ultima,
        "top_produtos": top_produtos, "por_periodo_dia": por_periodo_dia,
    }


def _gravar_atividade_conta(cur, id_extracao: int, atividade: dict) -> None:
    if canal_extracao.emitir("atividade_conta", (id_extracao, atividade)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.atv_tb01_atividades (id_extracao, dt_atividade, tx_produto, tx_descricao, tx_url, no_fonte)
           VALUES (%(id_extracao)s, %(timestamp)s, %(produto)s, %(descricao)s, %(url)s, %(fonte)s)""",
        {**atividade, "id_extracao": id_extracao},
    )


# =============================================================================
# 02 · Acessos (login/sincronização/app)
# =============================================================================

def _filtros_acessos(id_extracao, busca, produto, data_de, data_ate, periodo):
    filtros = ["ex.id_inquerito = %s"]
    params = []
    if id_extracao is not None:
        filtros.append("a.id_extracao = %s")
        params.append(id_extracao)
    if produto:
        filtros.append("a.tx_produto = %s")
        params.append(produto)
    _filtro_periodo_data(filtros, params, "a.dt_acesso", data_de, data_ate, periodo)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            "(a.tx_ip ILIKE %s OR a.tx_user_agent ILIKE %s OR a.tx_produto ILIKE %s"
            " OR a.tx_android_id ILIKE %s OR a.tx_oauth_login_id ILIKE %s OR a.no_conta ILIKE %s)"
        )
        params.extend([termo, termo, termo, termo, termo, termo])
    return filtros, params


def listar_acessos_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, ordenar: str | None = None, pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, os eventos de acesso à conta. Filtros: id_extracao, busca (IP/
    user-agent/produto/identificadores), produto, data_de/data_ate, periodo. ordenar ∈
    recentes|antigos. Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros, params_filtro = _filtros_acessos(id_extracao, busca, produto, data_de, data_ate, periodo)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    order_by = "a.dt_acesso ASC NULLS LAST" if ordenar == "antigos" else "a.dt_acesso DESC NULLS LAST"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.acs_tb01_acessos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                 f"""SELECT a.id_acesso, a.dt_acesso, a.dt_fim_acesso, a.tx_ip, a.tx_ip_proxy,
                            a.in_ip_nao_roteavel,
                            a.tx_user_agent, a.tx_produto, a.tx_subproduto, a.tp_acesso, a.tx_canal,
                            a.tx_android_id, a.tx_ios_id, a.tx_outro_dispositivo, a.tx_imap_id,
                            a.tx_activesync_id, a.tx_resolucao, a.tx_oauth_login_id,
                            a.tx_stateful_token_id, a.tx_cookie_auth_time, a.no_conta, a.no_fonte
                    FROM investigacao.acs_tb01_acessos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def facetas_acessos_conta(id_inquerito: int, id_extracao: int | None = None) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT a.tx_produto FROM investigacao.acs_tb01_acessos a
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR a.id_extracao = %s)
                     AND a.tx_produto IS NOT NULL AND a.tx_produto <> ''
                   ORDER BY a.tx_produto""",
                (id_inquerito, id_extracao, id_extracao),
            )
            return {"produtos": [linha[0] for linha in cur.fetchall()]}


def resumo_acessos_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None,
) -> dict:
    """KPIs: total, período coberto, IPs distintos, top IPs, distribuição por período do dia."""
    filtros, params_filtro = _filtros_acessos(id_extracao, busca, produto, data_de, data_ate, periodo)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    base_from = f"""FROM investigacao.acs_tb01_acessos a
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
        WHERE {where}"""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*), count(DISTINCT a.tx_ip) FILTER (WHERE a.tx_ip IS NOT NULL AND a.tx_ip <> ''),
                           min(a.dt_acesso), max(a.dt_acesso) {base_from}""",
                params,
            )
            total, ips_distintos, primeiro, ultimo = cur.fetchone()
            cur.execute(
                f"""SELECT a.tx_ip AS ip, count(*) AS registros
                    {base_from} AND a.tx_ip IS NOT NULL AND a.tx_ip <> ''
                    GROUP BY 1 ORDER BY registros DESC LIMIT 12""",
                params,
            )
            top_ips = [{"ip": l[0], "registros": l[1]} for l in cur.fetchall()]
            cur.execute(
                f"""SELECT CASE
                        WHEN hora BETWEEN 0 AND 5 THEN 'madrugada' WHEN hora BETWEEN 6 AND 11 THEN 'manha'
                        WHEN hora BETWEEN 12 AND 17 THEN 'tarde' ELSE 'noite' END AS periodo_dia, count(*)
                    FROM (SELECT {_hora_local_sql('a.dt_acesso')} AS hora {base_from} AND a.dt_acesso IS NOT NULL) t
                    GROUP BY 1""",
                params,
            )
            por_periodo_dia = {l[0]: l[1] for l in cur.fetchall()}
    return {
        "total": total, "ips_distintos": ips_distintos, "primeiro": primeiro, "ultimo": ultimo,
        "top_ips": top_ips, "por_periodo_dia": por_periodo_dia,
    }


def _gravar_acesso_conta(cur, id_extracao: int, acesso: dict) -> None:
    if canal_extracao.emitir("acesso_conta", (id_extracao, acesso)):  # nó entrega ao central em vez de gravar
        return
    """Grava um evento/uma sessão de acesso em ACS_TB01_ACESSOS.

    `dt_fim` e `no_conta` são opcionais (None para o Takeout, que é evento instantâneo e
    monousuário). O Cellebrite (RemoteServiceSession) preenche os dois — a sessão tem
    início e fim e o dono visível no report."""
    acesso = {**acesso}
    acesso.setdefault("dt_fim", None)
    acesso.setdefault("no_conta", None)
    cur.execute(
        """INSERT INTO investigacao.acs_tb01_acessos
           (id_extracao, dt_acesso, dt_fim_acesso, tx_ip, tx_ip_proxy, in_ip_nao_roteavel, tx_user_agent,
            tx_produto,
            tx_subproduto, tp_acesso, tx_canal, tx_android_id, tx_ios_id, tx_outro_dispositivo, tx_imap_id,
            tx_activesync_id, tx_resolucao, tx_oauth_login_id, tx_stateful_token_id, tx_cookie_auth_time,
            no_conta, no_fonte)
           VALUES (%(id_extracao)s, %(timestamp)s, %(dt_fim)s, %(ip)s, %(ip_proxy)s, %(ip_nao_roteavel)s,
                   %(user_agent)s, %(produto)s, %(subproduto)s, %(tipo)s, %(canal)s, %(android_id)s,
                   %(ios_id)s,
                   %(outro_dispositivo)s, %(imap_id)s, %(activesync_id)s, %(resolucao)s, %(oauth_login_id)s,
                   %(stateful_token_id)s, %(cookie_auth_time)s, %(no_conta)s, %(fonte)s)""",
        {**acesso, "id_extracao": id_extracao},
    )


# =============================================================================
# 03 · Acessos agregados (serviços por identificador)
# =============================================================================

def _filtros_agregados(id_extracao, busca, tipo, data_de, data_ate):
    filtros = ["ex.id_inquerito = %s"]
    params = []
    if id_extracao is not None:
        filtros.append("a.id_extracao = %s")
        params.append(id_extracao)
    if tipo:
        filtros.append("a.tp_identificador = %s")
        params.append(tipo)
    _filtro_periodo_data(filtros, params, "a.dt_ultima", data_de, data_ate, None)
    if busca:
        filtros.append("a.tx_identificador ILIKE %s")
        params.append(f"%{busca.strip()}%")
    return filtros, params


def listar_agregados_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    tipo: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    ordenar: str | None = None, pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, os identificadores agregados (dias ativos por identificador).
    ordenar ∈ recentes|dias_ativos (default recentes, por dt_ultima)."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros, params_filtro = _filtros_agregados(id_extracao, busca, tipo, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    order_by = "a.qt_dias_ativos DESC NULLS LAST" if ordenar == "dias_ativos" else "a.dt_ultima DESC NULLS LAST"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.acs_tb02_agregados a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT a.id_acesso_agr, a.dt_primeira, a.dt_ultima, a.qt_dias_ativos, a.tp_identificador,
                           a.tx_identificador, a.in_multilogin, a.tp_canal_autenticacao, a.no_fonte
                    FROM investigacao.acs_tb02_agregados a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def facetas_agregados_conta(id_inquerito: int, id_extracao: int | None = None) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT a.tp_identificador FROM investigacao.acs_tb02_agregados a
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR a.id_extracao = %s)
                     AND a.tp_identificador IS NOT NULL AND a.tp_identificador <> ''
                   ORDER BY a.tp_identificador""",
                (id_inquerito, id_extracao, id_extracao),
            )
            return {"tipos": [linha[0] for linha in cur.fetchall()]}


def resumo_agregados_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    tipo: str | None = None, data_de: str | None = None, data_ate: str | None = None,
) -> dict:
    """KPIs: total de identificadores, quantos com multilogin, top por dias ativos."""
    filtros, params_filtro = _filtros_agregados(id_extracao, busca, tipo, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    base_from = f"""FROM investigacao.acs_tb02_agregados a
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
        WHERE {where}"""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*), count(*) FILTER (WHERE a.in_multilogin) {base_from}", params
            )
            total, multilogin = cur.fetchone()
            cur.execute(
                f"""SELECT coalesce(nullif(a.tx_identificador, ''), 'Não identificado') AS identificador,
                           a.qt_dias_ativos
                    {base_from} ORDER BY a.qt_dias_ativos DESC NULLS LAST LIMIT 12""",
                params,
            )
            top_identificadores = [{"identificador": l[0], "dias_ativos": l[1]} for l in cur.fetchall()]
    return {"total": total, "multilogin": multilogin, "top_identificadores": top_identificadores}


def _gravar_agregado_conta(cur, id_extracao: int, agregado: dict) -> None:
    cur.execute(
        """INSERT INTO investigacao.acs_tb02_agregados
           (id_extracao, dt_primeira, dt_ultima, qt_dias_ativos, tp_identificador, tx_identificador,
            in_multilogin, tp_canal_autenticacao, no_fonte)
           VALUES (%(id_extracao)s, %(primeira)s, %(ultima)s, %(dias_ativos)s, %(tipo)s, %(identificador)s,
                   %(multilogin)s, %(canal_autenticacao)s, %(fonte)s)""",
        {**agregado, "id_extracao": id_extracao},
    )


# =============================================================================
# 04 · Conexões entre identificadores
# =============================================================================

def _filtros_conexoes(id_extracao, busca, data_de, data_ate):
    filtros = ["ex.id_inquerito = %s"]
    params = []
    if id_extracao is not None:
        filtros.append("c.id_extracao = %s")
        params.append(id_extracao)
    _filtro_periodo_data(filtros, params, "c.dt_ultima", data_de, data_ate, None)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(c.tx_ident_1 ILIKE %s OR c.tx_ident_2 ILIKE %s)")
        params.extend([termo, termo])
    return filtros, params


def listar_conexoes_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None,
    pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, as conexões entre identificadores vistos juntos numa sessão."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros, params_filtro = _filtros_conexoes(id_extracao, busca, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.acs_tb03_conexoes c
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT c.id_conexao, c.dt_primeira, c.dt_ultima, c.tp_ident_1, c.tx_ident_1,
                           c.tp_ident_2, c.tx_ident_2, c.no_fonte
                    FROM investigacao.acs_tb03_conexoes c
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE {where}
                    ORDER BY c.dt_ultima DESC NULLS LAST
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def resumo_conexoes_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None,
) -> dict:
    """KPIs: total de conexões, período coberto."""
    filtros, params_filtro = _filtros_conexoes(id_extracao, busca, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*), min(c.dt_primeira), max(c.dt_ultima)
                    FROM investigacao.acs_tb03_conexoes c
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE {where}""",
                params,
            )
            total, primeira, ultima = cur.fetchone()
    return {"total": total, "primeira": primeira, "ultima": ultima}


def _gravar_conexao_conta(cur, id_extracao: int, conexao: dict) -> None:
    cur.execute(
        """INSERT INTO investigacao.acs_tb03_conexoes
           (id_extracao, dt_primeira, dt_ultima, tp_ident_1, tx_ident_1, tp_ident_2, tx_ident_2, no_fonte)
           VALUES (%(id_extracao)s, %(primeira)s, %(ultima)s, %(tipo_1)s, %(identificador_1)s,
                   %(tipo_2)s, %(identificador_2)s, %(fonte)s)""",
        {**conexao, "id_extracao": id_extracao},
    )


# =============================================================================
# 05 · Dispositivos conhecidos pela conta
# =============================================================================

def listar_dispositivos_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, os dispositivos conhecidos pela conta."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros = ["ex.id_inquerito = %s"]
    params = [id_inquerito]
    if id_extracao is not None:
        filtros.append("d.id_extracao = %s")
        params.append(id_extracao)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(d.no_dispositivo ILIKE %s OR d.tx_fabricante ILIKE %s OR d.tx_android_id ILIKE %s OR d.tx_imei ILIKE %s)")
        params.extend([termo, termo, termo, termo])
    where = " AND ".join(filtros)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.dvc_tb01_dispositivos d
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT d.id_dispositivo, d.no_dispositivo, d.tx_fabricante, d.tx_android_id, d.tx_imei,
                           d.tx_versao, d.tx_serial, d.tx_outros, d.tp_origem, d.no_fonte
                    FROM investigacao.dvc_tb01_dispositivos d
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
                    WHERE {where}
                    ORDER BY d.no_dispositivo NULLS LAST
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def resumo_dispositivos_conta(id_inquerito: int, id_extracao: int | None = None, busca: str | None = None) -> dict:
    """KPIs: total de dispositivos, distribuição por fabricante."""
    filtros = ["ex.id_inquerito = %s"]
    params = [id_inquerito]
    if id_extracao is not None:
        filtros.append("d.id_extracao = %s")
        params.append(id_extracao)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(d.no_dispositivo ILIKE %s OR d.tx_fabricante ILIKE %s)")
        params.extend([termo, termo])
    where = " AND ".join(filtros)
    base_from = f"""FROM investigacao.dvc_tb01_dispositivos d
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
        WHERE {where}"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) {base_from}", params)
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT coalesce(nullif(d.tx_fabricante, ''), 'Não identificado') AS fabricante, count(*)
                    {base_from} GROUP BY 1 ORDER BY 2 DESC LIMIT 12""",
                params,
            )
            por_fabricante = [{"fabricante": l[0], "registros": l[1]} for l in cur.fetchall()]
    return {"total": total, "por_fabricante": por_fabricante}


def _gravar_dispositivo_conta(cur, id_extracao: int, dispositivo: dict) -> None:
    if canal_extracao.emitir("dispositivo_conta", (id_extracao, dispositivo)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.dvc_tb01_dispositivos
           (id_extracao, no_dispositivo, tx_fabricante, tx_android_id, tx_imei, tx_versao, tx_serial,
            tx_outros, tp_origem, no_fonte)
           VALUES (%(id_extracao)s, %(nome)s, %(fabricante)s, %(android_id)s, %(imei)s, %(versao)s,
                   %(serial)s, %(outros)s, %(origem)s, %(fonte)s)""",
        {**dispositivo, "id_extracao": id_extracao},
    )


# =============================================================================
# 06 · Perfil da conta
# =============================================================================

def listar_perfil_conta(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista os perfis de conta do inquérito (normalmente 0-1 por extração)."""
    filtros = ["ex.id_inquerito = %s"]
    params = [id_inquerito]
    if id_extracao is not None:
        filtros.append("p.id_extracao = %s")
        params.append(id_extracao)
    where = " AND ".join(filtros)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT p.id_perfil, p.id_extracao, p.no_nome, p.no_nome_dado, p.no_nome_familia,
                           p.tx_email, p.tx_email_alternativos, p.dt_criacao, p.tx_ip_tos, p.tx_idioma,
                           p.tx_pais, p.tx_nascimento, p.tx_servicos, p.dt_ultima_atualizacao,
                           p.dt_exclusao, p.tx_status, p.no_fonte
                    FROM investigacao.prf_tb01_perfil p
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
                    WHERE {where}
                    ORDER BY p.id_perfil""",
                params,
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _gravar_perfil_conta(cur, id_extracao: int, perfil: dict) -> None:
    if canal_extracao.emitir("perfil_conta", (id_extracao, perfil)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.prf_tb01_perfil
           (id_extracao, no_nome, no_nome_dado, no_nome_familia, tx_email, tx_email_alternativos,
            dt_criacao, tx_ip_tos, tx_idioma, tx_pais, tx_nascimento, tx_servicos,
            dt_ultima_atualizacao, dt_exclusao, tx_status, no_fonte)
           VALUES (%(id_extracao)s, %(nome)s, %(nome_dado)s, %(nome_familia)s, %(email)s,
                   %(email_alternativos)s, %(criacao)s, %(ip_tos)s, %(idioma)s, %(pais)s, %(nascimento)s,
                   %(servicos)s, %(ultima_atualizacao)s, %(exclusao)s, %(status)s, %(fonte)s)""",
        {**perfil, "id_extracao": id_extracao},
    )


# =============================================================================
# 07 · Agenda
# =============================================================================

def _filtros_eventos(id_extracao, busca, data_de, data_ate):
    filtros = ["ex.id_inquerito = %s"]
    params = []
    if id_extracao is not None:
        filtros.append("e.id_extracao = %s")
        params.append(id_extracao)
    _filtro_periodo_data(filtros, params, "e.dt_inicio", data_de, data_ate, None)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(e.tx_titulo ILIKE %s OR e.tx_local ILIKE %s OR e.tx_descricao ILIKE %s)")
        params.extend([termo, termo, termo])
    return filtros, params


def listar_eventos_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None, ordenar: str | None = None,
    pagina: int = 1, limite: int = 100,
) -> dict:
    """Lista, paginado, os eventos de agenda. ordenar ∈ recentes|antigos (default antigos,
    já que agenda normalmente se lê em ordem cronológica)."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite
    filtros, params_filtro = _filtros_eventos(id_extracao, busca, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    order_by = "e.dt_inicio DESC NULLS LAST" if ordenar == "recentes" else "e.dt_inicio ASC NULLS LAST"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM investigacao.evt_tb01_eventos e
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = e.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT e.id_evento, e.dt_inicio, e.dt_fim, e.dt_criacao, e.dt_alteracao, e.tx_titulo,
                           e.tx_local, e.tx_descricao, e.tx_organizador, e.tx_participantes, e.no_fonte
                    FROM investigacao.evt_tb01_eventos e
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = e.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def resumo_eventos_conta(
    id_inquerito: int, id_extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None,
) -> dict:
    """KPIs: total de eventos, período coberto, distribuição por mês."""
    filtros, params_filtro = _filtros_eventos(id_extracao, busca, data_de, data_ate)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    base_from = f"""FROM investigacao.evt_tb01_eventos e
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = e.id_extracao
        WHERE {where}"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), min(e.dt_inicio), max(e.dt_inicio) {base_from}", params)
            total, primeiro, ultimo = cur.fetchone()
            cur.execute(
                f"""SELECT to_char(e.dt_inicio, 'YYYY-MM') AS mes, count(*)
                    {base_from} AND e.dt_inicio IS NOT NULL
                    GROUP BY 1 ORDER BY 1 LIMIT 24""",
                params,
            )
            por_mes = [{"mes": l[0], "eventos": l[1]} for l in cur.fetchall()]
    return {"total": total, "primeiro": primeiro, "ultimo": ultimo, "por_mes": por_mes}


def _gravar_evento_conta(cur, id_extracao: int, evento: dict) -> None:
    if canal_extracao.emitir("evento_conta", (id_extracao, evento)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.evt_tb01_eventos
           (id_extracao, dt_inicio, dt_fim, dt_criacao, dt_alteracao, tx_titulo, tx_local, tx_descricao,
            tx_organizador, tx_participantes, no_fonte)
           VALUES (%(id_extracao)s, %(inicio)s, %(fim)s, %(criacao)s, %(alteracao)s, %(titulo)s, %(local)s,
                   %(descricao)s, %(organizador)s, %(participantes)s, %(fonte)s)""",
        {**evento, "id_extracao": id_extracao},
    )
