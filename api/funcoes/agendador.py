"""Agendador interno: dispara o passe do resumo noturno de dentro da própria API.

**Por que existe.** O passe da madrugada era disparado por um agendador do HOST — `systemd`
(`scripts/systemd/analyser-resumo@.timer`) no Ubuntu, Agendador de Tarefas no Windows —, e nenhum
dos dois é instalado por `docker compose up -d`: o `scripts/instalar-host.sh` só instala as units
do atualizador. Resultado prático: quem subia o sistema ficava sem resumo nenhum, sem erro e sem
aviso, até alguém descobrir que faltava um passo manual em cada servidor. Aqui a thread sobe junto
com a API, então Windows e Linux têm o mesmo comportamento e não há nada a instalar no host.

**Como decide a hora.** A cada tique calcula a *referência*: o dia a que pertence a última
ocorrência do horário configurado que já passou (02:00 por padrão, no fuso de
`RESUMO_NOTURNO_TZ`). Se ainda não existe execução para aquela referência, roda — o que dá de
graça o comportamento do `Persistent=true` do systemd: máquina desligada às 02:00 e ligada às
08:00 roda ao ligar, ainda como a execução daquele dia. É por isso que a chave do ledger é a DATA
DE REFERÊNCIA e não o instante da execução.

O fuso é explícito porque o container roda em **UTC**: sem isso, "02:00" seria 23:00 no Brasil.
Nunca calcule a referência com `now()` do banco pelo mesmo motivo.

**Estreia.** Na primeiríssima subida (ledger vazio) a referência atual é marcada como feita SEM
rodar nada. Sem isso, atualizar o sistema às 15:00 dispararia na hora um passe completo de IA em
todos os inquéritos — surpresa cara. A partir do dia seguinte vale o comportamento normal,
inclusive a recuperação de atraso.

**Concorrência.** Com `API_WORKERS > 1` há uma thread por worker, e todas acordam juntas. Quem
roda é quem conseguir inserir a linha do dia em `AGD_TB01_EXECUCOES` (PRIMARY KEY = a vaga); os
outros veem o conflito e desistem. Execução em 'processando' que parou de bater há mais de
`_MINUTOS_EXECUCAO_TRAVADA` é considerada órfã (o processo morreu) e pode ser retomada — daí o
batimento em thread separada enquanto o passe roda.

**Convivência com o agendador do host.** Quem já tem o systemd/schtasks instalado não precisa
tirar: o CLI (`api/tools/resumo_noturno.py`) registra a execução no mesmo ledger, então a thread
vê o dia como feito e não repete. Para desligar a thread de vez: `RESUMO_NOTURNO_AGENDADOR=0`.

Ver docs/resumo_noturno.md.
"""

import logging
import os
import socket
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

TP_RESUMO_NOTURNO = "resumo_noturno"
TP_HIGIENE_DATA = "higiene_data"
TP_VERIFICACAO_BACKUP = "verificacao_backup"

# Tarefas conhecidas. O nome vai no ledger e no log.
ROTULOS = {TP_RESUMO_NOTURNO: "resumo noturno", TP_HIGIENE_DATA: "higiene da pasta data/",
           TP_VERIFICACAO_BACKUP: "verificação dos backups"}

HORA_PADRAO = "02:00"
# O sistema é da PF brasileira e o container roda em UTC: sem um fuso explícito, "02:00" cairia
# às 23:00 no horário de quem usa. Trocável por RESUMO_NOTURNO_TZ (qualquer nome IANA).
FUSO_PADRAO = "America/Sao_Paulo"

# O tique é barato (um SELECT por chave primária) e a madrugada não é crítica ao minuto: 5 min de
# resolução basta e mantém o custo perto de zero. A primeira verificação vem antes, para uma
# máquina ligada às 08:00 recuperar o passe da madrugada logo depois de subir, não 5 min depois.
_INTERVALO_TIQUE = 300.0
_ESPERA_INICIAL = 60.0

# Enquanto o passe roda, uma thread bate DT_ULTIMA_ATIVIDADE. Sem batimento não há como
# distinguir "rodando há duas horas" de "o processo morreu no meio".
_INTERVALO_BATIMENTO = 60.0
_MINUTOS_EXECUCAO_TRAVADA = 30

_NO_WORKER = f"{socket.gethostname()}:{os.getpid()}"[:100]

_agendador_ligado = False


# =====================================================================
# Configuração (.env, relida a cada tique — igual ao resto do sistema)
# =====================================================================

def _falso(valor: str) -> bool:
    return (valor or "").strip().lower() in ("0", "false", "nao", "não", "off")


def _hora_minuto(bruto: str) -> tuple[int, int]:
    """"02:00" / "2" / "2:30" -> (2, 0) / (2, 0) / (2, 30). Valor estranho cai no padrão."""
    texto = (bruto or "").strip() or HORA_PADRAO
    try:
        partes = texto.split(":")
        hora = int(partes[0])
        minuto = int(partes[1]) if len(partes) > 1 else 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return hora, minuto
    except (ValueError, IndexError):
        pass
    logger.warning("agendador: horário inválido (%r); usando %s", bruto, HORA_PADRAO)
    return _hora_minuto(HORA_PADRAO)


def _fuso(nome: str) -> ZoneInfo:
    """Fuso configurado; nome desconhecido cai no padrão, e só então em UTC.

    O `tzdata` está no requirements.txt justamente para isto — a imagem `python:3.12-slim` não
    garante o banco de fusos do sistema."""
    for tentativa in ((nome or "").strip(), FUSO_PADRAO):
        if not tentativa:
            continue
        try:
            return ZoneInfo(tentativa)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("agendador: fuso desconhecido (%r)", tentativa)
    return ZoneInfo("UTC")


def config() -> dict:
    """Configuração do agendador do resumo noturno, lida do .env a cada uso."""
    env = parse_env(read_env_text())
    hora, minuto = _hora_minuto(env.get("RESUMO_NOTURNO_HORA"))
    nome_fuso = (env.get("RESUMO_NOTURNO_TZ") or "").strip() or FUSO_PADRAO
    return {
        "ligado": not _falso(env.get("RESUMO_NOTURNO_AGENDADOR") or "1"),
        "hora": hora,
        "minuto": minuto,
        "horario": f"{hora:02d}:{minuto:02d}",
        "fuso": nome_fuso,
        "tz": _fuso(nome_fuso),
    }


# =====================================================================
# Relógio
# =====================================================================

def referencia_atual(agora: datetime, hora: int, minuto: int) -> date:
    """Dia a que pertence a execução mais recente que já deveria ter acontecido.

    Antes do horário do dia, a referência ainda é a de ontem — é o que faz a máquina que acorda
    às 08:00 rodar o passe "de hoje de madrugada" e não o de amanhã."""
    marca = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return agora.date() if agora >= marca else (agora - timedelta(days=1)).date()


def proxima_execucao(agora: datetime, hora: int, minuto: int) -> datetime:
    """Próximo disparo depois de `agora` (só para exibir na tela de Configurações)."""
    marca = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return marca if marca > agora else marca + timedelta(days=1)


# =====================================================================
# Ledger (AGD_TB01_EXECUCOES)
# =====================================================================

def _tabela_existe() -> bool:
    """A migração 0022 pode não ter rodado (MIGRAR_NO_STARTUP=0). Sem a tabela o agendador fica
    quieto em vez de estourar a cada tique."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'investigacao' AND table_name = 'agd_tb01_execucoes'"
                )
                return cur.fetchone() is not None
    except Exception:
        logger.exception("agendador: falha ao conferir a tabela de execuções")
        return False


def execucao(tp_tarefa: str, dt_referencia: date) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tp_tarefa, dt_referencia, cd_status, no_origem, no_worker,
                       qt_processados, qt_erros, tx_erro, dt_inicio, dt_ultima_atividade, dt_fim
                FROM investigacao.agd_tb01_execucoes
                WHERE tp_tarefa = %s AND dt_referencia = %s
                """,
                (tp_tarefa, dt_referencia),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([d.name for d in cur.description], row))


def ultima_execucao(tp_tarefa: str) -> dict | None:
    """A execução mais recente desta tarefa (a tela de Configurações mostra esta linha)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tp_tarefa, dt_referencia, cd_status, no_origem, no_worker,
                       qt_processados, qt_erros, tx_erro, dt_inicio, dt_ultima_atividade, dt_fim
                FROM investigacao.agd_tb01_execucoes
                WHERE tp_tarefa = %s
                ORDER BY dt_referencia DESC LIMIT 1
                """,
                (tp_tarefa,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([d.name for d in cur.description], row))


def _reivindicar(tp_tarefa: str, dt_referencia: date, origem: str) -> bool:
    """Tenta pegar a vaga do dia. True = é este processo que roda.

    Uma instrução só, e o guard é a PRIMARY KEY: dois workers acordando no mesmo segundo passariam
    os dois por um "SELECT e depois INSERT". O DO UPDATE ... WHERE cobre o único caso em que a
    linha existente não vale — execução em 'processando' cujo processo morreu (parou de bater)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.agd_tb01_execucoes
                    (tp_tarefa, dt_referencia, cd_status, no_origem, no_worker)
                VALUES (%s, %s, 'processando', %s, %s)
                ON CONFLICT (tp_tarefa, dt_referencia) DO UPDATE
                   SET cd_status = 'processando',
                       no_origem = EXCLUDED.no_origem,
                       no_worker = EXCLUDED.no_worker,
                       dt_inicio = now(),
                       dt_ultima_atividade = now(),
                       dt_fim = NULL,
                       tx_erro = NULL
                 WHERE investigacao.agd_tb01_execucoes.cd_status = 'processando'
                   AND investigacao.agd_tb01_execucoes.dt_ultima_atividade
                       < now() - (%s || ' minutes')::interval
                RETURNING dt_referencia
                """,
                (tp_tarefa, dt_referencia, origem[:20], _NO_WORKER, _MINUTOS_EXECUCAO_TRAVADA),
            )
            pegou = cur.fetchone() is not None
        conn.commit()
    return pegou


def _bater(tp_tarefa: str, dt_referencia: date) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.agd_tb01_execucoes SET dt_ultima_atividade = now() "
                "WHERE tp_tarefa = %s AND dt_referencia = %s",
                (tp_tarefa, dt_referencia),
            )
        conn.commit()


def _fechar(tp_tarefa: str, dt_referencia: date, status: str, *, processados: int = 0,
            erros: int = 0, erro: str | None = None) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.agd_tb01_execucoes
                SET cd_status = %s, qt_processados = %s, qt_erros = %s,
                    tx_erro = %s, dt_fim = now(), dt_ultima_atividade = now()
                WHERE tp_tarefa = %s AND dt_referencia = %s
                """,
                (status, processados, erros, (erro or None), tp_tarefa, dt_referencia),
            )
        conn.commit()


def registrar_execucao(tp_tarefa: str, dt_referencia: date, origem: str, *,
                       processados: int = 0, erros: int = 0) -> None:
    """Marca o dia como feito sem passar pelo ciclo da thread.

    Serve a dois casos: a estreia (ledger vazio — ver o cabeçalho do módulo) e o passe rodado pelo
    CLI, inclusive o do agendador do host. Sem isto, quem mantém o systemd/schtasks teria o passe
    duas vezes na mesma noite — a segunda quase de graça pelo hash, mas ainda assim uma varredura
    inteira do banco à toa."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.agd_tb01_execucoes
                    (tp_tarefa, dt_referencia, cd_status, no_origem, no_worker,
                     qt_processados, qt_erros, dt_fim)
                VALUES (%s, %s, 'concluido', %s, %s, %s, %s, now())
                ON CONFLICT (tp_tarefa, dt_referencia) DO UPDATE
                   SET cd_status = 'concluido',
                       no_origem = EXCLUDED.no_origem,
                       no_worker = EXCLUDED.no_worker,
                       qt_processados = EXCLUDED.qt_processados,
                       qt_erros = EXCLUDED.qt_erros,
                       dt_ultima_atividade = now(),
                       dt_fim = now()
                """,
                (tp_tarefa, dt_referencia, origem[:20], _NO_WORKER, processados, erros),
            )
        conn.commit()


def registrar_passe_manual(processados: int = 0, erros: int = 0) -> None:
    """Registra, como execução do dia, um passe completo rodado fora da thread (CLI).

    Best-effort de propósito: um erro aqui não pode derrubar um passe que já terminou."""
    try:
        if not _tabela_existe():
            return
        cfg = config()
        ref = referencia_atual(datetime.now(cfg["tz"]), cfg["hora"], cfg["minuto"])
        registrar_execucao(TP_RESUMO_NOTURNO, ref, "cli", processados=processados, erros=erros)
    except Exception:
        logger.exception("agendador: falha ao registrar o passe manual (best-effort)")


# =====================================================================
# Ciclo
# =====================================================================

def _contar(resultados: dict) -> tuple[int, int]:
    processados = sum(1 for r in resultados.values() if r.get("status") == "gerado")
    erros = sum(1 for r in resultados.values() if r.get("status") == "erro")
    return processados, erros


def _com_batimento(tp_tarefa: str, dt_referencia: date, parar: threading.Event) -> None:
    while not parar.wait(_INTERVALO_BATIMENTO):
        try:
            _bater(tp_tarefa, dt_referencia)
        except Exception:
            logger.exception("agendador: falha no batimento (seguindo)")


def _rodar_resumo_noturno(dt_referencia: date) -> None:
    from funcoes.resumo_noturno import processar_todos

    parar = threading.Event()
    threading.Thread(target=_com_batimento, args=(TP_RESUMO_NOTURNO, dt_referencia, parar),
                     daemon=True, name="agendador-batimento").start()
    inicio = time.monotonic()
    try:
        resultados = processar_todos()
        processados, erros = _contar(resultados)
        _fechar(TP_RESUMO_NOTURNO, dt_referencia, "concluido",
                processados=processados, erros=erros)
        logger.info("agendador: resumo noturno de %s concluído — %s inquérito(s) resumido(s), "
                    "%s com erro, %.1f min", dt_referencia, processados, erros,
                    (time.monotonic() - inicio) / 60)
    except Exception as exc:
        logger.exception("agendador: resumo noturno de %s falhou", dt_referencia)
        _fechar(TP_RESUMO_NOTURNO, dt_referencia, "erro", erro=str(exc)[:2000])
    finally:
        parar.set()


def _rodar_higiene_data(dt_referencia: date) -> None:
    """Passe diário da lixeira/uploads órfãos (funcoes/higiene_data.py). Barato e sem IA: roda
    no mesmo horário do resumo, mas independente dele — IA desligada não pode parar higiene."""
    if not _reivindicar(TP_HIGIENE_DATA, dt_referencia, "agendador"):
        return  # outro worker pegou a vaga do dia
    try:
        from funcoes.higiene_data import higienizar

        resultado = higienizar()
        _fechar(TP_HIGIENE_DATA, dt_referencia, "concluido",
                processados=resultado.get("orfaos_movidos", 0) + resultado.get("lixeira_expurgada", 0),
                erros=resultado.get("erros", 0))
    except Exception as exc:
        logger.exception("agendador: higiene de data/ de %s falhou", dt_referencia)
        _fechar(TP_HIGIENE_DATA, dt_referencia, "erro", erro=str(exc)[:2000])


def _rodar_verificacao_backup(dt_referencia: date) -> None:
    """Passe diário da saúde do backup (funcoes/backup_utils.py): o dump noturno existe, não é
    vazio e não está velho. Sem isto o backup só é descoberto morto no dia do desastre. A
    instalação sem o sidecar de backup (pasta ./backups não montada) não registra nada — a
    verificação só cobra quem contratou o serviço."""
    from funcoes import backup_utils

    if not backup_utils.pasta_configurada():
        return
    if not _reivindicar(TP_VERIFICACAO_BACKUP, dt_referencia, "agendador"):
        return  # outro worker pegou a vaga do dia
    try:
        resultado = backup_utils.verificar()
        if resultado["ok"]:
            _fechar(TP_VERIFICACAO_BACKUP, dt_referencia, "concluido",
                    processados=resultado.get("arquivos", 0))
        else:
            _fechar(TP_VERIFICACAO_BACKUP, dt_referencia, "erro",
                    erro=resultado.get("motivo") or "backup falhou na verificação")
    except Exception as exc:
        logger.exception("agendador: verificação de backup de %s falhou", dt_referencia)
        _fechar(TP_VERIFICACAO_BACKUP, dt_referencia, "erro", erro=str(exc)[:2000])


def _tique() -> None:
    """Uma verificação. Separada do laço para poder ser chamada em teste."""
    if not _tabela_existe():
        return
    cfg = config()
    ref = referencia_atual(datetime.now(cfg["tz"]), cfg["hora"], cfg["minuto"])

    # verificação do backup FORA do guard RESUMO_NOTURNO_AGENDADOR: o desliga-o-passe é do
    # passe de IA; desligar o resumo nunca pode significar "pare de olhar o backup" — hoje
    # mesmo há instalação em produção com o resumo desligado e backups em andamento
    if execucao(TP_VERIFICACAO_BACKUP, ref) is None:
        _rodar_verificacao_backup(ref)

    if not cfg["ligado"]:
        return

    # higiene primeiro e por fora dos guards do resumo: ela não depende de IA configurada nem
    # do ledger de estreia (rodar cedo demais aqui não custa nada — no limite, move nada)
    if execucao(TP_HIGIENE_DATA, ref) is None:
        _rodar_higiene_data(ref)

    if execucao(TP_RESUMO_NOTURNO, ref) is not None:
        return  # o dia já foi (por esta thread, por outro worker ou pelo CLI)

    if ultima_execucao(TP_RESUMO_NOTURNO) is None:
        # Estreia: marca o dia como feito sem rodar. Ver o cabeçalho do módulo — atualizar o
        # sistema no meio da tarde não pode disparar um passe completo de IA na hora.
        registrar_execucao(TP_RESUMO_NOTURNO, ref, "estreia")
        logger.info("agendador: primeira subida — o resumo noturno começa no próximo %s (%s)",
                    cfg["horario"], cfg["fuso"])
        return

    from funcoes.resumo_noturno import resumo_noturno_configurado

    if not resumo_noturno_configurado():
        # Sem IA configurada não adianta rodar — e não se marca o dia como feito: configurar a IA
        # ainda hoje faz o passe acontecer no tique seguinte, sem esperar a próxima madrugada.
        return

    if not _reivindicar(TP_RESUMO_NOTURNO, ref, "agendador"):
        return  # outro worker pegou a vaga

    logger.info("agendador: iniciando o resumo noturno de %s", ref)
    _rodar_resumo_noturno(ref)


def _tique_seguro() -> None:
    try:
        _tique()
    except Exception:
        logger.exception("agendador: falha no ciclo; seguindo no próximo tique")


def iniciar_agendador() -> None:
    """Sobe o agendador no startup da API (main.py). Idempotente.

    O laço roda como LÍDER (funcoes/leader_utils.py): com mais de uma réplica da API só uma
    varre por tique — e se a líder tombar, outra assume em segundos. A reivindicação por ledger
    (_reivindicar) continua intacta embaixo: ela é a garantia contra dois executores mesmo
    (CLI do host, troca de líder no meio do tique); a eleição só elimina o varrer-em-dobro."""
    global _agendador_ligado
    if _agendador_ligado:
        return
    _agendador_ligado = True
    from funcoes import leader_utils

    leader_utils.iniciar_loop_lider("agendador", leader_utils.AGENDADOR, _tique_seguro,
                                    _INTERVALO_TIQUE, _ESPERA_INICIAL)


# =====================================================================
# Leitura para a tela de Configurações
# =====================================================================

def estado() -> dict:
    """Como o agendador está: ligado, horário, fuso, última execução e próximo disparo."""
    cfg = config()
    agora = datetime.now(cfg["tz"])
    ultima = None
    try:
        if _tabela_existe():
            ultima = ultima_execucao(TP_RESUMO_NOTURNO)
    except Exception:
        logger.exception("agendador: falha ao ler a última execução")
    return {
        "ligado": cfg["ligado"],
        "horario": cfg["horario"],
        "fuso": cfg["fuso"],
        "proxima": proxima_execucao(agora, cfg["hora"], cfg["minuto"]).strftime("%d/%m/%Y %H:%M"),
        "ultima": ultima,
    }
