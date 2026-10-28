"""Despacho de RIF/SIMBA/IPED para o nó da delegacia (docs/no_descentralizado.md, "nó importa").

Os três importadores parseiam a pasta local do central e gravavam direto no banco. Com o nó,
o arquivo mora NA ESTAÇÃO e o parse roda lá: este módulo é a metade-central do caminho —

1. resolve/cria o inquérito (banco do central, como sempre);
2. cria a linha-pai da fonte (rif_tb01 / smb_tb01 / ipd_tb01, status 'pendente') e a
   LINHA-PONTE em INQ_TB02_EXTRACOES (no_fonte + id_importacao, migração 0065) — é o
   "claim" do canal de entrega: lotes e estados sobem por /api/no/entrega/<id_extracao>/...
   exatamente como uma extração .ufdr;
3. POST /no/importar no nó (no_ponte.importar_remoto) com o caminho RELATIVO da pasta e as
   opções que o parse local precisa (número do inquérito para organizar a pasta depois...).

O resto do fluxo é o canal que já existe: o nó emite registros (rif_*/smb_*/+ os tipos de
domínio do IPED), o central reproduz (canal_extracao.reproduzir_lote) e fecha a entrega
(no_entrega) rodando a cauda específica da fonte.

Falha de despacho (nó caiu entre o teste e o POST): a ponte e a linha-pai voltam para 'erro'
com a mensagem — nada fica preso em 'processando'.
"""

from __future__ import annotations

import logging

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

TPS_FONTE = ("rif", "simba", "iped")


class SemNoAtivoError(ValueError):
    """A delegacia não tem nó ativo — a rota traduz para 409 com a mensagem da tela."""


def _pasta_ok(relativo: str) -> str:
    """Valida o caminho relativo que veio do SELETOR DO NÓ (GET /no/listar): o central nunca
    abre essa pasta — só repassa o string — mas um valor com '..' absoluto jamais pode entrar
    no despacho (o nó revalida com o resolver dele; aqui é a barreira de entrada)."""
    limpo = (relativo or "").strip().strip("/")
    if (not limpo or ".." in limpo.split("/") or limpo.startswith("/")
            or "\\" in limpo or any(part in ("", ".") for part in limpo.split("/"))):
        raise ValueError("Caminho da pasta inválido.")
    return limpo


def _ponte_existente(tp: str, id_inquerito: int, pasta: str) -> dict | None:
    """Já existe importação desta MESMA pasta deste inquérito em andamento? (dedup de dois
    cliques: a ponte 'processando' é a fila — a segunda tentativa recebe a linha existente)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id_extracao, cd_status FROM investigacao.inq_tb02_extracoes
               WHERE no_fonte = %s AND id_inquerito = %s AND no_arquivo = %s
                 AND cd_status IN ('pendente', 'processando')
               ORDER BY id_extracao DESC LIMIT 1""",
            (tp, id_inquerito, pasta))
        row = cur.fetchone()
    return {"id_extracao": row[0], "cd_status": row[1]} if row else None


def _criar_ponte(tp: str, id_inquerito: int, pasta: str, id_usuario: int | None,
                 id_servidor: int, id_importacao: int) -> int:
    """A linha-ponte (claim do canal) já nasce 'processando': o vigia de extrações travadas e
    o batimento do nó operam nela desde o despacho — igual a uma extração .ufdr despachada."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO investigacao.inq_tb02_extracoes
                   (id_inquerito, no_arquivo, cd_status, cd_fase, id_usuario, id_servidor,
                    no_fonte, id_importacao, dt_inicio_processamento, dt_ultima_atividade)
               VALUES (%s, %s, 'processando', 'importando', %s, %s, %s, %s, now(), now())
               RETURNING id_extracao""",
            (id_inquerito, pasta, id_usuario, id_servidor, tp, id_importacao))
        id_extracao = cur.fetchone()[0]
        conn.commit()
    return id_extracao


def _despachar_e_marcar_erro(tp: str, no: dict, id_extracao: int, pasta: str,
                             opcoes: dict, desfazer) -> dict:
    """POST /no/importar; qualquer recusa do nó devolve ponte+pai para 'erro' e sobe
    ValueError com a mensagem (a rota responde 409/503 em vez de deixar fila fantasma)."""
    from funcoes import no_ponte

    try:
        return no_ponte.importar_remoto(no, id_extracao, tp, pasta, opcoes)
    except Exception as exc:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET cd_status = 'erro', cd_fase = NULL, "
                "tx_erro = %s, dt_fim_processamento = now() "
                "WHERE id_extracao = %s AND cd_status = 'processando'",
                (str(exc)[:2000], id_extracao))
            desfazer(cur)
            conn.commit()
        raise ValueError(str(exc))


# --------------------------------------------------------------------------- RIF

def despachar_rif(no: dict, pasta: str, numero_inquerito: str | None, id_usuario: int | None,
                  subpasta: str | None = None, delegacia: str | None = None,
                  in_caso_inteligencia: bool = False) -> dict:
    """RIF remoto: valida o caminho, cria inquérito + rif_tb01 'pendente' + ponte, e manda o
    parse para a estação. O retorno é o shape do importador local + {'remoto': True} (a tela
    acompanha pelo status do RIF, que o fechamento da entrega atualiza)."""
    from funcoes.investigacao_utils import _obter_ou_criar_inquerito

    pasta = _pasta_ok(pasta)
    with get_connection() as conn, conn.cursor() as cur:
        id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                 in_caso_inteligencia)
        cur.execute(
            """INSERT INTO investigacao.rif_tb01_rifs (id_inquerito, no_pasta, id_usuario)
               VALUES (%s, %s, %s) RETURNING id_rif""",
            (id_inquerito, pasta, id_usuario))
        id_rif = cur.fetchone()[0]
        conn.commit()

    existente = _ponte_existente("rif", id_inquerito, pasta)
    if existente:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.rif_tb01_rifs WHERE id_rif = %s", (id_rif,))
            conn.commit()
        raise ValueError("Este RIF já está sendo importado neste inquérito — aguarde terminar.")

    id_extracao = _criar_ponte("rif", id_inquerito, pasta, id_usuario, no["id_servidor"], id_rif)
    opcoes = {"numero_inquerito": numero_inquerito,
              "delegacia_pasta": (subpasta or delegacia or "").strip()}
    _despachar_e_marcar_erro(
        "rif", no, id_extracao, pasta, opcoes,
        desfazer=lambda cur: cur.execute(
            "DELETE FROM investigacao.rif_tb01_rifs WHERE id_rif = %s", (id_rif,)))
    logger.info("RIF %s despachado ao nó %s (ponte %s)", pasta, no.get("no_servidor"), id_extracao)
    return {"id_rif": id_rif, "id_inquerito": id_inquerito, "id_extracao": id_extracao,
            "remoto": True}


# --------------------------------------------------------------------------- SIMBA

def despachar_simba(no: dict, arquivo: str, numero_inquerito: str | None,
                    id_usuario: int | None, subpasta: str | None = None,
                    delegacia: str | None = None, in_caso_inteligencia: bool = False) -> dict:
    """SIMBA remoto (R304): mesma coreografia do RIF — inquérito + smb_tb01 'pendente' +
    ponte + POST. A carga de lançamentos volta em lotes de COPY pelo canal."""
    from funcoes.investigacao_utils import _obter_ou_criar_inquerito

    arquivo = _pasta_ok(arquivo)
    with get_connection() as conn, conn.cursor() as cur:
        id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                 in_caso_inteligencia)
        cur.execute(
            """INSERT INTO investigacao.smb_tb01_extratos (id_inquerito, no_arquivo, cd_status, id_usuario)
               VALUES (%s, %s, 'pendente', %s) RETURNING id_extrato""",
            (id_inquerito, arquivo, id_usuario))
        id_extrato = cur.fetchone()[0]
        conn.commit()

    existente = _ponte_existente("simba", id_inquerito, arquivo)
    if existente:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s",
                        (id_extrato,))
            conn.commit()
        raise ValueError("Este extrato já está sendo importado neste inquérito — aguarde terminar.")

    id_extracao = _criar_ponte("simba", id_inquerito, arquivo, id_usuario, no["id_servidor"],
                               id_extrato)
    opcoes = {"numero_inquerito": numero_inquerito,
              "delegacia_pasta": (subpasta or delegacia or "").strip()}
    _despachar_e_marcar_erro(
        "simba", no, id_extracao, arquivo, opcoes,
        desfazer=lambda cur: cur.execute(
            "UPDATE investigacao.smb_tb01_extratos SET cd_status = 'erro', tx_erro = "
            "'O servidor da delegacia não aceitou a importação.' WHERE id_extrato = %s",
            (id_extrato,)))
    logger.info("SIMBA %s despachado ao nó %s (ponte %s)", arquivo, no.get("no_servidor"),
                id_extracao)
    return {"id_extrato": id_extrato, "id_inquerito": id_inquerito, "id_extracao": id_extracao,
            "remoto": True}


# --------------------------------------------------------------------------- IPED

def despachar_iped(no: dict, pasta: str, numero_inquerito: str | None, id_usuario: int | None,
                   subpasta: str | None = None, conta: str | None = None,
                   delegacia: str | None = None, in_caso_inteligencia: bool = False) -> dict:
    """IPED remoto: o dumper Java roda NA ESTAÇÃO (é leitura de índice Lucene + storage local,
    nunca atravessaria a WAN). Os itens viram os MESMOS registros de domínio do UFDR pelo
    canal; os binários ficam no nó e são servidos por GET /no/iped-blob (iped_cache roteia).

    A linha-ponte é criada PRIMEIRO (ipd_tb01.id_extracao aponta para ela — igual ao caminho
    local), e o fecho da entrega aplica o relatório do nó (conta detectada, pasta renomeada,
    contagens) nas duas pontas."""
    from funcoes.investigacao_utils import _obter_ou_criar_inquerito

    pasta = _pasta_ok(pasta)
    with get_connection() as conn, conn.cursor() as cur:
        id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                 in_caso_inteligencia)
        cur.execute(
            "SELECT 1 FROM investigacao.ipd_tb01_extracoes WHERE id_inquerito = %s AND no_pasta = %s",
            (id_inquerito, pasta))
        if cur.fetchone():
            raise ValueError("Essa pasta já foi importada neste inquérito — apague a importação "
                             "existente para importar de novo.")
        conn.commit()

    existente = _ponte_existente("iped", id_inquerito, pasta)
    if existente:
        raise ValueError("Este caso IPED já está sendo importado neste inquérito — aguarde terminar.")

    # ponte primeiro: ipd_tb01 referencia id_extracao (a UQ02_IPD_TB01_INQUERITO_PASTA protege
    # a duplicidade de pasta no mesmo inquérito, como no caminho local)
    id_extracao = _criar_ponte("iped", id_inquerito, pasta, id_usuario, no["id_servidor"], 0)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO investigacao.ipd_tb01_extracoes
                   (id_inquerito, no_pasta, no_conta, cd_status, id_usuario, id_extracao)
               VALUES (%s, %s, %s, 'pendente', %s, %s) RETURNING id_extracao_iped""",
            (id_inquerito, pasta, conta, id_usuario, id_extracao))
        id_extracao_iped = cur.fetchone()[0]
        cur.execute("UPDATE investigacao.inq_tb02_extracoes SET id_importacao = %s "
                    "WHERE id_extracao = %s", (id_extracao_iped, id_extracao))
        conn.commit()

    opcoes = {"numero_inquerito": numero_inquerito, "conta": conta,
              "delegacia_pasta": (subpasta or delegacia or "").strip()}
    _despachar_e_marcar_erro(
        "iped", no, id_extracao, pasta, opcoes,
        desfazer=lambda cur: cur.execute(
            "UPDATE investigacao.ipd_tb01_extracoes SET cd_status = 'erro', tx_erro = "
            "'O servidor da delegacia não aceitou a importação.' WHERE id_extracao_iped = %s",
            (id_extracao_iped,)))
    logger.info("IPED %s despachado ao nó %s (ponte %s)", pasta, no.get("no_servidor"),
                id_extracao)
    return {"id_inquerito": id_inquerito, "id_extracao_iped": id_extracao_iped,
            "id_extracao": id_extracao, "remoto": True}
