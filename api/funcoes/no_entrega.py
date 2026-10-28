# -*- coding: utf-8 -*-
"""RECEBEDOR de extrações processadas por nó (central; docs/no_descentralizado.md).

O nó manda três coisas (rotas em rotas/no_central.py, todas Bearer = token do nó):

- LOTE (`registrar_lote`): JSONL de registros do canal. A idempotência é a NO_TB03_LOTES_ENTREGA
  — lote repetido só re-confirma. A reprodução chama `canal_extracao.reproduzir_lote`, que usa as
  MESMAS funções de gravação do caminho local: ON CONFLICT, dedup e gatilhos são exatamente os
  mesmos, independentemente de quem parseou o arquivo.
- ESTADO (`aplicar_callback`): fase/progresso/batida (o que mantém a órbita e o vigia vivos),
  hashes (aqui, e SÓ aqui, acontece a checagem de duplicidade interestações — o nó não tem
  banco), contagens, info do aparelho, erro.
- FINALIZAR (callback t="finalizar"): uma thread própria fecha o serviço, espelhando a cauda da
  pipeline local — corrigir direção do dono, PIX, identificação de pessoas e indexação semântica,
  que exigem o banco e que rodariam aqui de qualquer forma — e só então grava 'concluido'
  (ou 'cancelado', se a ordem de parada chegou).
"""
from __future__ import annotations

import logging
import threading
import time

from funcoes import canal_extracao, db_utils

logger = logging.getLogger(__name__)

_ESTADOS_FINAIS = ("concluido", "erro", "cancelado")
# cd_fase que marca "finalizar aceito, cauda rodando" — sobrevive a restart do central e é
# o que recuperar_fechamentos() procura para retomar um fechamento órfão
_FASE_FECHANDO = "fechando"
# a indexação semântica da cauda sobrescreve cd_fase — também é cauda rodando, e o
# recuperar_fechamentos() retoma órfãos parados nela (ver recuperar_fechamentos)
_FASE_CAUDA_INDEXACAO = "indexando_semantica"
# fechamento 'fechando' sem batimento de DT_ULTIMA_ATIVIDADE por mais que isto = órfão.
# A varredura de órfãos NÃO confia no _FECHANDO local (ele só conhece a cauda DESTE processo —
# com duas réplicas da API uma delas roubaria a cauda viva da outra): a posse é reivindicada
# no banco, condicional ao batimento (ver _reivindicar_fechamento), e toda cauda viva bate de
# verdade, com vigia (ver _fechar_entrega).
_MINUTOS_FECHAMENTO_ORFAO = 15

# extrações com a cauda de fechamento rodando NESTE processo — dedup de 'finalizar'
# duplicado (o nó reenvia o callback em retry) e bússola do recuperar_fechamentos
_FECHANDO: set[int] = set()
_FECHANDO_TRAVA = threading.Lock()


def linha_extracao(id_extracao: int) -> dict | None:
    with db_utils.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_extracao, id_servidor, cd_status, fl_cancelar, id_inquerito, "
                    "nu_geracao, no_fonte, id_importacao "
                    "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_extracao,))
        row = cur.fetchone()
    if row is None:
        return None
    return {"id_extracao": row[0], "id_servidor": row[1], "cd_status": row[2],
            "fl_cancelar": bool(row[3]), "id_inquerito": row[4], "nu_geracao": row[5],
            "no_fonte": row[6], "id_importacao": row[7]}


class ConflictError(RuntimeError):
    pass


class LoteInvalidoError(RuntimeError):
    """Lote que o central NUNCA vai aceitar (linha corrompida, tipo de registro desconhecido).
    Vira 422 — o nó quarentena o arquivo local e segue, em vez de reenviar o veneno para
    sempre travando a entrega."""


def _mesma_geracao(linha: dict, geracao) -> bool:
    """Nó velho (pré-geração) não manda `g` → aceita como sempre. Nó novo manda e diverge do
    claim → é uma passada SUBSTITUÍDA pelo redespacho: os dados dela não podem mais entrar
    (o redespacho limpou a tentativa parcial; ver investigacao_utils._despachar_linha)."""
    if geracao is None:
        return True
    try:
        return int(geracao) == int(linha.get("nu_geracao") or 0)
    except (TypeError, ValueError):
        return False


def registrar_lote(id_extracao: int, nu_lote: int, corpo: str, id_servidor_dono: int,
                   geracao=None) -> int:
    """Reproduz um lote do nó no banco. Idempotente por (extração, lote); devolve o seq
    confirmado (o nó usa isso para retomar exatamente de onde parou)."""
    linha = linha_extracao(id_extracao)
    if linha is None:
        raise LookupError("extração não existe mais")
    if linha["id_servidor"] != id_servidor_dono:
        raise PermissionError("este nó não é o dono desta extração")
    if linha["cd_status"] in _ESTADOS_FINAIS:
        raise ConflictError("extração já encerrada")
    if not _mesma_geracao(linha, geracao):
        raise ConflictError("esta entrega foi substituída por um despacho mais novo")

    with db_utils.get_connection(longa=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO investigacao.no_tb03_lotes_entrega (id_extracao, nu_lote, qt_registros)
            VALUES (%s, %s, 0)
            ON CONFLICT (id_extracao, nu_lote) DO NOTHING
            """,
            (id_extracao, nu_lote),
        )
        if cur.rowcount == 0:  # lote repetido: já processado, só reconfirma
            return nu_lote
        try:
            qt = canal_extracao.reproduzir_lote(cur, corpo, id_extracao)
        except ValueError as exc:
            # sem commit: o rollback desfaz o recibo E a reprodução parcial — o lote inteiro
            # vira 422 e o nó o quarentena (a extração é marcada erro pelo nó, nunca
            # 'concluida' com um lote faltando)
            raise LoteInvalidoError(str(exc)) from exc
        cur.execute("UPDATE investigacao.no_tb03_lotes_entrega SET qt_registros = %s "
                    "WHERE id_extracao = %s AND nu_lote = %s", (qt, id_extracao, nu_lote))
        conn.commit()
    return nu_lote


def aplicar_callback(id_extracao: int, payload: dict, id_servidor_dono: int) -> dict:
    """Aplica um estado vindo do nó e responde {cancelar} — a resposta é o canal por onde o
    pedido de parar da tela chega ao nó (sem o nó precisar consultar nada)."""
    linha = linha_extracao(id_extracao)
    if linha is None:
        raise LookupError("extração não existe mais")
    if linha["id_servidor"] != id_servidor_dono:
        raise PermissionError("este nó não é o dono desta extração")
    if not _mesma_geracao(linha, payload.get("g")):
        raise ConflictError("esta entrega foi substituída por um despacho mais novo")
    t = payload.get("t")

    if t == "finalizar":
        if linha["cd_status"] not in _ESTADOS_FINAIS:
            with _FECHANDO_TRAVA:
                # 'finalizar' duplicado (reenvio do nó em retry) não dispara duas caudas —
                # identificação/PIX/indexação rodando juntas no mesmo inquérito brigariam
                if id_extracao not in _FECHANDO:
                    _FECHANDO.add(id_extracao)
                    # marca 'fechando' (persiste a intenção): restart do central no meio da
                    # cauda não deixa a extração presa em 'processando' esperando um
                    # fechamento que morreu com o processo — recuperar_fechamentos() refaz
                    try:
                        with db_utils.get_connection() as conn, conn.cursor() as cur:
                            cur.execute(
                                "UPDATE investigacao.inq_tb02_extracoes "
                                "SET cd_fase = %s, dt_ultima_atividade = now() "
                                "WHERE id_extracao = %s AND cd_status = 'processando'",
                                (_FASE_FECHANDO, id_extracao))
                            conn.commit()
                    except Exception:
                        logger.exception("Falha ao marcar 'fechando' (entrega %s)", id_extracao)
                    try:
                        threading.Thread(target=_fechar_entrega,
                                         args=(id_extracao,
                                               payload.get("audios_transcritos") or 0,
                                               payload.get("aviso"),
                                               payload),
                                         daemon=True,
                                         name=f"fechar-entrega-{id_extracao}").start()
                    except Exception:
                        # exaustão de threads: libera o selo para o reenvio do nó (ou a
                        # retomada periódica) poder tentar de novo
                        logger.exception("Falha ao subir a thread de fechamento %s",
                                         id_extracao)
                        with _FECHANDO_TRAVA:
                            _FECHANDO.discard(id_extracao)
        return {"cancelar": linha["fl_cancelar"]}

    if linha["cd_status"] in _ESTADOS_FINAIS:
        return {"cancelar": True}

    resposta = canal_extracao.aplicar_estado(id_extracao, payload)
    if t == "hashes":
        # a checagem de duplicidade interestações mora AQUI (só o central vê os outros aparelhos)
        from funcoes.investigacao_utils import ExtracaoDuplicada, _recusar_se_duplicada
        try:
            _recusar_se_duplicada(id_extracao, {k: payload.get(k) for k in ("md5", "sha1", "sha256")})
        except ExtracaoDuplicada as exc:
            _marcar_erro(id_extracao, str(exc))
            return {"cancelar": True}
    linha = linha_extracao(id_extracao)
    resposta["cancelar"] = bool(linha and linha["fl_cancelar"]) or resposta.get("cancelar", False)
    return resposta


def ja_tem_entrega(id_extracao: int) -> bool:
    """Algum lote desta extração já foi reproduzido? (redespacho precisa saber)."""
    with db_utils.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM investigacao.no_tb03_lotes_entrega WHERE id_extracao = %s LIMIT 1",
                    (id_extracao,))
        return cur.fetchone() is not None


def reiniciar_entrega(id_extracao: int) -> None:
    """Redespacho (watchdog devolveu a extração para a fila depois de o nó cair no meio da
    entrega): a passada nova reemite o arquivo INTEIRO a partir do lote 0, então o recibo e os
    dados da tentativa parcial têm de ser apagados antes — senão o UX111 pulava lotes "repetidos"
    (que não são repetidos: são a numeração da corrida velha) e a extração nascia incompleta.
    É o mesmo efeito de apagar e reimportar, só que automático."""
    from funcoes.investigacao_utils import _apagar_dados_extracao

    with db_utils.get_connection(longa=True) as conn, conn.cursor() as cur:
        _apagar_dados_extracao(cur, id_extracao)
        cur.execute("DELETE FROM investigacao.no_tb03_lotes_entrega WHERE id_extracao = %s",
                    (id_extracao,))
        cur.execute("UPDATE investigacao.inq_tb02_extracoes SET cd_fase = NULL, "
                    "qt_progresso_fase = NULL WHERE id_extracao = %s", (id_extracao,))
        conn.commit()
    logger.info("Entrega da extração %s reiniciada (redespacho após queda do nó)", id_extracao)


def _marcar_erro(id_extracao: int, mensagem: str) -> None:
    with db_utils.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE investigacao.inq_tb02_extracoes SET cd_status = 'erro', cd_fase = NULL, "
                    "qt_progresso_fase = NULL, tx_erro = %s, dt_fim_processamento = now() "
                    "WHERE id_extracao = %s AND cd_status = 'processando'",
                    (mensagem[:2000], id_extracao))
        conn.commit()


def recuperar_fechamentos() -> int:
    """Chamado pelo despachante (central): 'finalizar' chegou, a cauda ia rodando, e o central
    morreu no meio — a linha está em 'processando' com cd_fase='fechando' e nada acontece.
    Retoma o fechamento das órfãs (mesma thread/idempotência do caminho normal). Duas guardas
    contra retomar cauda viva: o _FECHANDO local (cauda deste processo) e a reivindicação
    atômica no batimento (_reivindicar_fechamento — cauda viva de OUTRA réplica bate, e o
    WHERE condicional não a libera).

    'indexando_semantica' também é fase de cauda (o bat.fase da indexação sobrescreve o
    'fechando'): um redeploy no meio de uma indexação de horas deixava a extração presa em
    'processando' SEM ninguém para retomá-la — a varredura só conhecia 'fechando'. A cauda é
    idempotente (PIX/identificação/indexação pulam o que já foi feito, o fechamento tem WHERE
    condicional) e a indexação viva é protegida pelo próprio batimento: progresso recente =
    não está órfã."""
    try:
        with db_utils.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id_extracao FROM investigacao.inq_tb02_extracoes "
                "WHERE cd_status = 'processando' AND cd_fase = ANY(%s) AND id_servidor IS NOT NULL "
                "AND COALESCE(dt_ultima_atividade, dt_inicio_processamento) "
                "    < now() - make_interval(mins => %s)",
                ([_FASE_FECHANDO, _FASE_CAUDA_INDEXACAO], _MINUTOS_FECHAMENTO_ORFAO))
            ids = [r[0] for r in cur.fetchall()]
    except Exception:
        logger.exception("Falha ao procurar fechamentos órfãos")
        return 0
    retomadas = 0
    for id_extracao in ids:
        with _FECHANDO_TRAVA:
            if id_extracao in _FECHANDO:
                continue  # a cauda original ainda vive neste processo
        # Reivindicação atômica no banco, não no _FECHANDO local: com duas réplicas, o set
        # uma não vê a cauda viva da outra — quem decide é o batimento. O UPDATE condicional
        # reavalia o WHERE contra a linha travada pela transação da outra réplica, então só
        # UMA das duas leva a retomada (e uma cauda viva que bate nunca é reivindicada).
        if not _reivindicar_fechamento(id_extracao):
            continue
        with _FECHANDO_TRAVA:
            _FECHANDO.add(id_extracao)
        logger.warning("Retomando fechamento órfão da entrega %s (central reiniciou no meio?)",
                       id_extracao)
        threading.Thread(target=_fechar_entrega, args=(id_extracao, 0, None),
                         daemon=True, name=f"fechar-entrega-{id_extracao}").start()
        retomadas += 1
    return retomadas


def _reivindicar_fechamento(id_extracao: int) -> bool:
    """Reivindica a cauda de fechamento de uma extração aparentemente órfã: fecha o negócio no
    próprio WHERE condicional + batimento (mesmo padrão claim dos jobs) — rowcount 1 = a posse
    é minha; 0 = ou a cauda original está viva e batendo, ou a outra réplica foi mais rápida."""
    try:
        with db_utils.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET dt_ultima_atividade = now() "
                "WHERE id_extracao = %s AND cd_status = 'processando' AND cd_fase = ANY(%s) "
                "AND COALESCE(dt_ultima_atividade, dt_inicio_processamento) "
                "    < now() - make_interval(mins => %s)",
                (id_extracao, [_FASE_FECHANDO, _FASE_CAUDA_INDEXACAO], _MINUTOS_FECHAMENTO_ORFAO))
            revendicou = cur.rowcount > 0
            conn.commit()
        return revendicou
    except Exception:
        logger.exception("Falha ao reivindicar fechamento da entrega %s", id_extracao)
        return False


def _fechar_entrega(id_extracao: int, audios_transcritos: int, aviso: str | None,
                    payload: dict | None = None) -> None:
    """Cauda da extração que o nó não pode rodar (exige o banco) — os MESMOS passos que o
    caminho local roda depois do laço, na mesma ordem e com o mesmo best-effort. O FECHAMENTO
    em si não é best-effort: falhou, tenta de novo e, no limite, marca 'erro' com mensagem
    honesta — 'processando' para sempre com todos os dados entregues era o pior resultado.

    A cauda INTEIRA bate DT_ULTIMA_ATIVIDADE via vigia: PIX, correção de direção e
    identificação de pessoas rodam minutos sem medir nada, e sem batimento a varredura da
    OUTRA réplica (recuperar_fechamentos) veria esta cauda viva como órfã e rodaria tudo de
    novo em paralelo."""
    from funcoes import investigacao_utils as IU

    bat = IU._Batimento(id_extracao)
    bat.iniciar_vigia()
    try:
        _fechar_entrega_interno(id_extracao, audios_transcritos, aviso, payload)
    finally:
        bat.parar_vigia()
        with _FECHANDO_TRAVA:
            _FECHANDO.discard(id_extracao)


def _fechar_importacao_no(id_extracao: int, no_fonte: str, id_importacao: int,
                          payload: dict | None, avisos: list) -> None:
    """Cauda das importações que o NÓ processou (RIF/SIMBA/IPED — docs/no_descentralizado.md,
    "nó importa"). Os dados já foram reproduzidos nos lotes; aqui só o que é específico da
    fonte e só o banco faz:

    - simba: consolidar contrapartes + síntese p/ IA (SQL sobre o que já entrou);
    - rif:   identificação de pessoas (envolvidos/contrapartes entram no grafo da Mesa);
    - iped:  aplica o relatório que o nó devolveu no 'finalizar' (conta detectada, pasta
             renomeada, versão) na linha-pai e roda a identificação do caso.

    A ponte (INQ_TB02) é o que o card mostra como status, e ela é fechada pelo chamador
    (_fechar_entrega_interno) logo depois — por isso aqui NÃO se mexe em cd_status dela."""
    if no_fonte == "simba" and id_importacao:
        try:
            from funcoes.simba_utils import fechar_importacao_remota
            aviso = fechar_importacao_remota(id_importacao)
            if aviso:
                avisos.append(aviso)
        except Exception:
            logger.exception("cauda SIMBA (importação %s) — melhor esforço", id_importacao)
    elif no_fonte == "rif" and id_importacao:
        try:
            from funcoes.identificacao_pessoas import agendar_identificacao_pos_importacao
            linha = linha_extracao(id_extracao)
            if linha and linha["id_inquerito"]:
                with db_utils.get_connection() as conn, conn.cursor() as cur:
                    cur.execute("SELECT nu_rif FROM investigacao.rif_tb01_rifs WHERE id_rif = %s",
                                (id_importacao,))
                    r = cur.fetchone()
                agendar_identificacao_pos_importacao(linha["id_inquerito"],
                                                     f"RIF {(r and r[0]) or id_importacao}")
        except Exception:
            logger.exception("identificação de pessoas RIF %s — melhor esforço", id_importacao)
    elif no_fonte == "iped" and id_importacao:
        try:
            from funcoes.iped_utils import aplicar_relatorio_importacao_no
            aplicar_relatorio_importacao_no(id_importacao, id_extracao, payload or {})
        except Exception:
            logger.exception("cauda IPED (importação %s) — melhor esforço", id_importacao)


def _fechar_entrega_interno(id_extracao: int, audios_transcritos: int, aviso: str | None,
                            payload: dict | None = None) -> None:
    from funcoes import investigacao_utils as IU

    bat = IU._Batimento(id_extracao)
    avisos = [aviso] if aviso else []

    # importação de fonte (RIF/SIMBA/IPED) despachada ao nó: cauda própria, nada do caminho
    # .ufdr abaixo (PIX/direção/indexação semântica são do report.xml, que aqui não existe)
    linha_fonte = linha_extracao(id_extracao)
    if linha_fonte and linha_fonte.get("no_fonte"):
        _fechar_importacao_no(id_extracao, linha_fonte["no_fonte"], linha_fonte["id_importacao"],
                              payload, avisos)
        ultimo_erro: Exception | None = None
        for tentativa in range(3):
            try:
                IU._finalizar_extracao(id_extracao, audios_transcritos,
                                       "\n".join(avisos)[:2000] if avisos else None)
                logger.info("Importação do nó fechada: extração-ponte %s (%s)",
                            id_extracao, linha_fonte["no_fonte"])
                return
            except Exception as exc:
                ultimo_erro = exc
                logger.exception("finalize importação (ponte %s) falhou (tentativa %s/3)",
                                 id_extracao, tentativa + 1)
                time.sleep(5 * (tentativa + 1))
        _marcar_erro(id_extracao,
                     "Todos os dados desta importação foram entregues pelo nó, mas o fechamento "
                     f"falhou no central: {ultimo_erro}.")
        return

    try:
        IU._corrigir_direcao_dono_extracao(id_extracao)
    except Exception:
        logger.exception("corrigir direção (entrega %s) — best-effort", id_extracao)
    try:
        from funcoes.pix_utils import detectar_chaves_pix
        detectar_chaves_pix(id_extracao)
    except Exception:
        logger.exception("PIX (entrega %s) — best-effort", id_extracao)
    try:
        from funcoes.identificacao_pessoas import (
            IdentificacaoEmAndamento,
            identificar_pessoas_inquerito,
        )
        linha = linha_extracao(id_extracao)
        if linha and linha["id_inquerito"]:
            identificar_pessoas_inquerito(linha["id_inquerito"], incluir_extracao=id_extracao)
    except IdentificacaoEmAndamento:
        # duas entregas fechando juntas no mesmo inquérito: a varredura em andamento já enxerga
        # esta extração também — não é falha (mesma regra do caminho local)
        logger.info("identificação de pessoas da entrega %s pulada: já havia uma no inquérito",
                    id_extracao)
    except Exception:
        logger.exception("identificação de pessoas (entrega %s) — best-effort", id_extracao)
    try:
        aviso_idx = IU._indexar_semantica_ao_final(id_extracao, bat)
        if aviso_idx:
            avisos.append(aviso_idx)
    except Exception:
        logger.exception("indexação semântica (entrega %s) — best-effort", id_extracao)
    # o fechamento é o ÚNICO passo não best-effort: com todos os dados entregues, existir
    # só a gravação de 'concluido' — tenta 3× e, no limite, marca 'erro' com mensagem clara
    ultimo_erro: Exception | None = None
    for tentativa in range(3):
        try:
            IU._finalizar_extracao(id_extracao, audios_transcritos,
                                   "\n".join(avisos)[:2000] if avisos else None)
            logger.info("Entrega do nó fechada: extração %s", id_extracao)
            return
        except Exception as exc:
            ultimo_erro = exc
            logger.exception("finalize (entrega %s) falhou (tentativa %s/3)",
                             id_extracao, tentativa + 1)
            time.sleep(5 * (tentativa + 1))
    _marcar_erro(id_extracao,
                 "Todos os dados desta extração foram entregues pelo nó, mas o fechamento "
                 f"falhou no central: {ultimo_erro}. Os dados estão preservados — rode o "
                 "fechamento reprocessando a extração ou confira os logs do central.")
