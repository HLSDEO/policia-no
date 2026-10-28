"""Auditoria de gestão de usuários (seguranca.usr_tb08_auditoria_usuarios).

Registra ações de administração de usuários para trilha de auditoria.
"""

import logging
import json
from typing import Any

from funcoes.auditoria_acesso import id_usuario_da_requisicao
from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# Tipos de ação válidos
ACOES_VALIDAS = {
    "criar",
    "editar",
    "ativar",
    "desativar",
    "aprovar",
    "revogar_aprovacao",
    "redefinir_senha",
    "configurar_ia",
    "apagar_ia",
    "alterar_delegacia",
    "ativar_ia",
    "desativar_ia",
}


def _obter_ip_da_requisicao() -> str | None:
    """Obtém o IP da requisição atual via ContextVar (mesmo padrão de auditoria_acesso)."""
    from funcoes.auditoria_acesso import _escopo_atual

    escopo = _escopo_atual.get()
    if not escopo:
        return None

    for chave, valor in escopo.get("headers") or []:
        if chave == b"x-real-ip":
            return valor.decode("latin-1").strip()[:45]
    cliente = escopo.get("client")
    return cliente[0][:45] if cliente else None


def registrar_auditoria_usuario(
    id_usuario_alvo: int,
    tp_acao: str,
    detalhes: dict[str, Any] | None = None,
) -> None:
    """Registra uma ação de gestão de usuário na auditoria.

    Nunca levanta exceção: falha de auditoria não derruba a operação principal.
    """
    if tp_acao not in ACOES_VALIDAS:
        logger.warning("Ação de auditoria de usuário inválida: %s", tp_acao)
        return

    id_autor = id_usuario_da_requisicao()
    if id_autor is None:
        logger.warning("Tentativa de auditar ação de usuário sem usuário autenticado")
        return

    ip = _obter_ip_da_requisicao()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seguranca.usr_tb08_auditoria_usuarios
                        (id_usuario_autor, id_usuario_alvo, tp_acao, tx_detalhes, co_ip)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        id_autor,
                        id_usuario_alvo,
                        tp_acao,
                        json.dumps(detalhes) if detalhes else None,
                        ip,
                    ),
                )
            conn.commit()
    except Exception:
        logger.exception(
            "Falha ao gravar auditoria de usuário",
            extra={
                "id_usuario_autor": id_autor,
                "id_usuario_alvo": id_usuario_alvo,
                "tp_acao": tp_acao,
            },
        )


def listar_auditoria_usuarios(
    id_usuario_autor: int | None = None,
    id_usuario_alvo: int | None = None,
    tp_acao: str | None = None,
    dias: int | None = None,
    limite: int = 100,
    deslocamento: int = 0,
) -> dict:
    """Lista registros de auditoria de gestão de usuários com filtros opcionais.

    Devolve {'itens', 'totais', 'qt_total'}.
    """
    filtros = ["1=1"]
    params: dict = {"limite": limite, "deslocamento": deslocamento}

    if id_usuario_autor:
        filtros.append("a.id_usuario_autor = %(id_usuario_autor)s")
        params["id_usuario_autor"] = id_usuario_autor
    if id_usuario_alvo:
        filtros.append("a.id_usuario_alvo = %(id_usuario_alvo)s")
        params["id_usuario_alvo"] = id_usuario_alvo
    if tp_acao:
        filtros.append("a.tp_acao = %(tp_acao)s")
        params["tp_acao"] = tp_acao
    if dias:
        filtros.append("a.dt_acao > now() - make_interval(days => %(dias)s)")
        params["dias"] = dias

    where = " AND ".join(filtros)

    query_itens = f"""
        SELECT a.id_auditoria, a.id_usuario_autor, au.no_usuario AS no_autor, au.no_login AS login_autor,
               a.id_usuario_alvo, al.no_usuario AS no_alvo, al.no_login AS login_alvo,
               a.tp_acao, a.tx_detalhes, a.co_ip, a.dt_acao
        FROM seguranca.usr_tb08_auditoria_usuarios a
        LEFT JOIN seguranca.usr_tb01_usuarios au ON au.id_usuario = a.id_usuario_autor
        LEFT JOIN seguranca.usr_tb01_usuarios al ON al.id_usuario = a.id_usuario_alvo
        WHERE {where}
        ORDER BY a.dt_acao DESC, a.id_auditoria DESC
        LIMIT %(limite)s OFFSET %(deslocamento)s
    """
    query_totais = f"""
        SELECT count(*) AS qt_total,
               count(DISTINCT a.id_usuario_autor) AS qt_autores,
               count(DISTINCT a.id_usuario_alvo) AS qt_alvos,
               count(DISTINCT a.tp_acao) AS qt_tipos_acao
        FROM seguranca.usr_tb08_auditoria_usuarios a
        WHERE {where}
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_itens, params)
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
            cur.execute(query_totais, params)
            colunas = [d.name for d in cur.description]
            totais = dict(zip(colunas, cur.fetchone()))

    qt_total = totais.pop("qt_total")
    return {"itens": itens, "totais": totais, "qt_total": qt_total}


ROTULO_ACAO = {
    "criar": "Criar usuário",
    "editar": "Editar usuário",
    "ativar": "Ativar usuário",
    "desativar": "Desativar usuário",
    "aprovar": "Aprovar usuário",
    "revogar_aprovacao": "Revogar aprovação",
    "redefinir_senha": "Redefinir senha",
    "configurar_ia": "Configurar IA própria",
    "apagar_ia": "Remover IA própria",
    "alterar_delegacia": "Alterar delegacia",
    "ativar_ia": "Reativar IA do usuário",
    "desativar_ia": "Desativar IA do usuário",
}


def rotulo_acao(tp_acao: str) -> str:
    """Devolve o rótulo amigável para um tipo de ação."""
    return ROTULO_ACAO.get(tp_acao, tp_acao)