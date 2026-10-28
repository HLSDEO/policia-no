"""Limite compartilhado de jobs pesados de CPU rodando ao mesmo tempo: OCR (ocr_utils),
classificação de imagem e comparação facial (ambos usam CLIP, em classificacao_imagem_utils e
comparar_rosto_utils).

Cada um desses três já se restringe a uma execução por inquérito (o índice único da tabela de
jobs) — mas nada impedia um OCR, uma classificação e uma comparação facial de rodarem juntos,
disputando os mesmos núcleos. Este limite é compartilhado pelos três, então vale para o total.

**O limite é do SISTEMA, não do processo.** Antes daqui era um `threading.Semaphore` de módulo, o
que basta com um worker do uvicorn e deixa de bastar com vários: o teto passaria a valer por
worker e o invariante `JOBS_PESADOS_LIMITE × CLIP_NUM_THREADS <= núcleos/2`
(funcoes/desempenho_utils.py) seria violado tantas vezes quantos processos houvesse. A vaga agora
é uma linha de JOB_TB01_JOBS em 'processando' com FL_PESADO.

Por que contagem na tabela e não `pg_try_advisory_lock` de sessão, que se libera sozinho quando o
processo morre: segurar uma vaga por horas com lock de sessão exigiria segurar uma CONEXÃO por
horas — exatamente o que o refactor da extração acabou de tirar do sistema. Aqui o lock é de
transação, dura milissegundos, e o "worker morreu" é resolvido pelo mesmo batimento/reaper que a
tabela de jobs já exige (jobs_utils.liberar_jobs_travados). Uma máquina de liveness no sistema, e
não duas. O custo é que a vaga de um processo morto fica presa até o reaper — um limite suave de
desempenho, não um problema de correção.

De brinde, a espera passa a ser visível: o job fica em 'aguardando_vaga' e a tela mostra a posição
na fila, em vez de uma barra parada em 0%.

JOBS_PESADOS_LIMITE no .env ajusta o limite ao tamanho do servidor (ver README); o default é
conservador o bastante para uma VM pequena não perder o tempo de resposta das telas normais, que
disputam os mesmos núcleos.
"""

import logging
import time
from contextlib import contextmanager

from funcoes import desempenho_utils, jobs_utils
from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

_LIMITE_PADRAO = 2

# Enquanto espera vaga, tenta de novo a cada tantos segundos. O batimento vai junto, para o job na
# fila não ser confundido com job travado pelo reaper.
_INTERVALO_ESPERA = 20.0

# Chave do advisory lock que serializa a reivindicação de vaga. Arbitrária, só não pode colidir
# com as outras do sistema: 72301 é do runner de migrações (funcoes/migrar_utils.py) e 72304 é da
# identificação de RIF (funcoes/rif_utils.py).
_LOCK_VAGA = 72302


class SemVagaCancelado(RuntimeError):
    """O usuário cancelou enquanto o job esperava vaga."""


def _limite() -> int:
    return max(1, desempenho_utils.valor_int("JOBS_PESADOS_LIMITE", _LIMITE_PADRAO))


def _reivindicar(id_job: int, limite: int) -> bool:
    """Tenta ocupar uma vaga, num único statement atômico.

    O advisory lock de TRANSAÇÃO serializa a janela entre contar e marcar — sem ele, dois workers
    contando "3 de 6 ocupadas" ao mesmo tempo ocupariam a 4ª duas vezes. Ele é solto no commit,
    então não segura conexão. A contagem usa o índice parcial IX253_JOB_TB01_ATIVOS."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_VAGA,))
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET cd_status = 'processando', dt_ultima_atividade = now()
                WHERE id_job = %s
                  AND cd_status IN ('pendente', 'aguardando_vaga')
                  AND (SELECT count(*) FROM investigacao.job_tb01_jobs
                        WHERE fl_pesado AND cd_status = 'processando') < %s
                RETURNING id_job
                """,
                (id_job, limite),
            )
            pegou = cur.fetchone() is not None
        conn.commit()
    return pegou


def ocupacao() -> dict:
    """Quem está ocupando as vagas agora, para a tela de administração e para o diagnóstico de
    "por que meu OCR está na fila?"."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.id_job, j.tp_job, j.id_inquerito, i.nu_inquerito, u.no_usuario,
                       j.dt_inicio
                FROM investigacao.job_tb01_jobs j
                LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = j.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = j.id_usuario
                WHERE j.fl_pesado AND j.cd_status = 'processando'
                ORDER BY j.dt_inicio
                """
            )
            colunas = [d.name for d in cur.description]
            em_uso = [dict(zip(colunas, row)) for row in cur.fetchall()]
    return {"limite": _limite(), "ocupadas": len(em_uso), "jobs": em_uso}


@contextmanager
def vaga(id_job: int, nome_job: str):
    """Bloqueia a thread chamadora até haver vaga entre os jobs pesados rodando ao mesmo tempo NO
    SISTEMA. Uso:

        with jobs_pesados.vaga(id_job, "OCR"):
            ... trabalho pesado ...

    Só deve envolver o trecho de CPU pesada (o forward do CLIP, o reconhecimento do Tesseract),
    não a preparação (montar a fila, ler categorias) nem a gravação do resultado no banco.

    Enquanto espera, o job fica em 'aguardando_vaga' e continua batendo — o reaper não o confunde
    com travado, e a tela mostra a posição na fila. Cancelar um job NA FILA passa a funcionar na
    hora; com o semáforo antigo ele ficava preso no acquire() até alguém liberar vaga.

    A vaga não é devolvida aqui: quem a libera é a transição de status em jobs_utils
    (concluir/falhar/marcar_cancelado), porque é ela que tira a linha de 'processando'."""
    limite = _limite()
    if not _reivindicar(id_job, limite):
        logger.info("%s (job %s): aguardando vaga (limite de %s jobs pesados simultâneos atingido)",
                    nome_job, id_job, limite)
        jobs_utils.marcar_aguardando(id_job)
        while True:
            if jobs_utils.bater(id_job):
                raise SemVagaCancelado(f"{nome_job} cancelado enquanto esperava vaga.")
            # devolve a vaga de um processo que morreu segurando uma; fora da transação do
            # advisory lock de propósito — é um UPDATE em massa
            jobs_utils.liberar_jobs_travados()
            if _reivindicar(id_job, _limite()):
                break
            time.sleep(_INTERVALO_ESPERA)
        logger.info("%s (job %s): vaga liberada, iniciando processamento", nome_job, id_job)
    yield
