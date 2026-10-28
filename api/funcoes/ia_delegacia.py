"""Portão único dos flags IN_IA_ATIVA — "esta delegacia (ou este usuário) usa a IA do sistema".

Além do flag por delegacia, existe o flag POR USUÁRIO (USR_TB01_USUARIOS.IN_IA_ATIVA, tela
Usuários → "Desativar IA"): desliga a IA de uma só pessoa, sem tocar na conta dela nem na
delegacia. O portão é o mesmo — `bloqueio_ia` confere os dois e qualquer um dos dois negando já
barra a chamada.

A regra vale pelos **dois lados**, e basta um deles negar:

- **quem pede**: a delegacia do usuário logado (`USR_TB01_USUARIOS.ID_DELEGACIA`);
- **sobre o quê**: a delegacia do inquérito (`INQ_TB01_INQUERITOS.NO_DELEGACIA`, com fallback na
  delegacia de quem o criou e, por último, na de algum usuário com permissão — a mesma resolução
  que a listagem mostra no card).

Assim o bloqueio é o mesmo para o investigador da unidade e para o admin que abre um inquérito
dela — antes o backend só olhava o usuário e o front só olhava o seletor de delegacia, e as duas
respostas se contradiziam na tela. Usuário/inquérito sem delegacia definida passam (não há o
que bloquear).

Todo consumidor de IA passa por aqui: chat do inquérito (`rotas/ia.py`), análise de documento
anexado e busca automática de entidades (`rotas/investigacao.py`, `rotas/busca_automatica.py`).
O front não repete a regra: recebe `ia_ativa` pronto nas listagens de inquéritos.
Ver `docs/permissoes_delegacia.md`.
"""

from fastapi import Depends, HTTPException

from funcoes.auth_utils import exigir_aprovado
from funcoes.db_utils import get_connection


def normalizar_delegacia(nome: str | None) -> str:
    """Nome de delegacia comparável: o CRUD grava em maiúsculas, mas o nome também chega pela URL
    do front e fica gravado em INQ_TB01.NO_DELEGACIA, então não dá para confiar no caso."""
    return (nome or "").strip().upper()


def delegacias_sem_ia() -> set[str]:
    """Nomes normalizados das delegacias com IN_IA_ATIVA = false.

    A tabela tem uma dezena de linhas e o admin pode virar o flag a qualquer momento — uma consulta
    por checagem sai mais barato (e mais correto) que um cache a invalidar no CRUD de delegacias."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT no_delegacia FROM seguranca.delegacias WHERE NOT in_ia_ativa")
            return {n for n in (normalizar_delegacia(linha[0]) for linha in cur.fetchall()) if n}


def usuario_com_ia_desativada(id_usuario: int | None) -> bool:
    """True se o próprio usuário tem USR_TB01_USUARIOS.IN_IA_ATIVA = false (tela Usuários).

    Consulta por checagem, como `delegacias_sem_ia`: o admin pode religar a qualquer momento e o
    efeito precisa ser imediato — cache com TTL deixaria o analista bloqueado (ou liberado) por
    minutos depois da decisão."""
    if not id_usuario:
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT in_ia_ativa FROM seguranca.usr_tb01_usuarios WHERE id_usuario = %s",
                (id_usuario,),
            )
            linha = cur.fetchone()
    return bool(linha) and not linha[0]


def delegacia_do_inquerito(id_inquerito: int) -> str | None:
    """Delegacia dona do inquérito, na mesma ordem de resolução que `listar_inqueritos` usa para o
    rótulo do card — se as duas divergissem, o botão apareceria bloqueado sob um nome e liberado
    sob o outro. None quando o inquérito não existe ou ninguém nele tem delegacia."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(i.no_delegacia, d.no_delegacia, (
                           SELECT dd.no_delegacia
                           FROM investigacao.inq_tb03_permissoes p
                           JOIN seguranca.usr_tb01_usuarios u2 ON u2.id_usuario = p.id_usuario
                           JOIN seguranca.delegacias dd ON dd.id_delegacia = u2.id_delegacia
                           WHERE p.id_inquerito = i.id_inquerito
                           LIMIT 1
                       ))
                FROM investigacao.inq_tb01_inqueritos i
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE i.id_inquerito = %s
                """,
                (id_inquerito,),
            )
            linha = cur.fetchone()
    return linha[0] if linha else None



def bloqueio_ia(usuario: dict, id_inquerito: int | None = None) -> str | None:
    """Motivo legível do bloqueio de IA nesta chamada, ou None se pode seguir.

    Confere três pontos e devolve o primeiro negado: a pessoa (flag por usuário, tela Usuários), a
    delegacia de quem pede e a delegacia do que está sendo aberto. O valor volta na mensagem do 403
    e no tooltip do botão, então é sempre algo que faz sentido ler depois de "IA desativada para"."""
    if usuario_com_ia_desativada(usuario.get("id_usuario")):
        return "a sua conta"
    sem_ia = delegacias_sem_ia()
    if not sem_ia:
        return None
    do_usuario = usuario.get("no_delegacia")
    if normalizar_delegacia(do_usuario) in sem_ia:
        return do_usuario
    if id_inquerito is not None:
        dono = delegacia_do_inquerito(id_inquerito)
        if normalizar_delegacia(dono) in sem_ia:
            return dono
    return None


def erro_ia_desativada(motivo: str) -> HTTPException:
    """403 padrão do flag — a mesma frase que a tela mostra no lugar do chat."""
    return HTTPException(status_code=403, detail=f"IA desativada para {motivo}.")


def exigir_ia_ativa(usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do router de IA: barra quem é de delegacia com a IA desligada (lado "quem pede").

    O lado "sobre o quê" precisa do id na rota e fica em `exigir_acesso_ia_inquerito` /
    `exigir_ia_inquerito`."""
    bloqueada = bloqueio_ia(usuario)
    if bloqueada:
        raise erro_ia_desativada(bloqueada)
    return usuario


def exigir_ia_inquerito(id_inquerito: int, usuario: dict) -> None:
    """Checagem inline do flag para rotas que já conferiram o acesso ao inquérito por outro
    caminho (as que recebem id_conversa_ia/id_documento e derivam o inquérito)."""
    bloqueada = bloqueio_ia(usuario, id_inquerito=id_inquerito)
    if bloqueada:
        raise erro_ia_desativada(bloqueada)


def exigir_acesso_ia_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency das rotas de IA com id_inquerito no path: acesso ao inquérito (mesma regra de
    `exigir_acesso_inquerito`, com a auditoria que ela grava) **e** IA ligada dos dois lados."""
    # local para não amarrar este módulo a investigacao_utils, que importa daqui na listagem
    from funcoes.investigacao_utils import exigir_acesso_inquerito

    exigir_acesso_inquerito(id_inquerito, usuario)
    exigir_ia_inquerito(id_inquerito, usuario)
    return usuario
