"""Rotas genéricas dos jobs de segundo plano (OCR, classificação de imagem, comparação facial).

Substituem os trios /progresso + /cancelar + /reset que cada um desses três tinha. Aqueles não
recebiam id nenhum, porque o estado era um dicionário global do processo: o progresso que voltava
era o de quem estivesse rodando, e o cancelamento parava o job de quem fosse. Aqui tudo é por
id_job, e toda leitura resolve o inquérito dono e confere acesso.

Ver funcoes/jobs_utils.py e db/init/41_jobs.sql.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from funcoes import jobs_utils
from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito, usuario_tem_acesso_inquerito

logger = logging.getLogger(__name__)

router = APIRouter()
router_admin = APIRouter(dependencies=[Depends(exigir_admin)])


def _job_visivel(id_job: int, usuario: dict, *, escrita: bool = False) -> dict:
    """O job, se quem pergunta puder vê-lo. 404 quando não existe, 403 quando é de outro
    inquérito. Job global (sem inquérito) é só do dono ou de admin."""
    job = jobs_utils.obter(id_job)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if job["id_inquerito"] is not None:
        if not usuario_tem_acesso_inquerito(job["id_inquerito"], usuario, escrita=escrita):
            raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    elif not (usuario.get("in_admin") or job["id_usuario"] == usuario.get("id_usuario")):
        raise HTTPException(status_code=403, detail="Sem permissão para este job.")
    return job


def _com_fila(job: dict) -> dict:
    """Acrescenta quantos jobs estão à frente, quando o job está esperando vaga — é o que a tela
    mostra no lugar de uma barra parada em 0%."""
    if job["cd_status"] in ("pendente", "aguardando_vaga"):
        job["qt_na_frente"] = jobs_utils.posicao_na_fila(job["id_job"])
    else:
        job["qt_na_frente"] = 0
    return job


@router.get("/api/investigacao/jobs/{id_job}")
def obter_job(id_job: int, usuario: dict = Depends(exigir_aprovado)):
    """Estado de um job — é o endpoint que o polling do front consulta."""
    return _com_fila(_job_visivel(id_job, usuario))


@router.get("/api/investigacao/inqueritos/{id_inquerito}/jobs")
def jobs_do_inquerito(id_inquerito: int, ativos: bool = True,
                      usuario: dict = Depends(exigir_acesso_inquerito)):
    """Jobs deste inquérito (por padrão só os em andamento)."""
    return [_com_fila(j) for j in jobs_utils.listar(id_inquerito, so_ativos=ativos)]


@router.get("/api/investigacao/inqueritos/{id_inquerito}/jobs/{tp_job}")
def ultimo_job(id_inquerito: int, tp_job: str, usuario: dict = Depends(exigir_acesso_inquerito)):
    """O último job deste tipo neste inquérito, em andamento ou terminado.

    É o que a tela pede ao abrir: retoma o acompanhamento de um job que ficou rodando, ou mostra
    o resultado do que acabou. Devolve null quando nunca houve nenhum — e, ao contrário do
    /progresso antigo, nunca devolve o job de outra delegacia."""
    if tp_job not in jobs_utils.ROTULOS:
        raise HTTPException(status_code=400, detail="Tipo de job desconhecido.")
    job = jobs_utils.ultimo(tp_job, id_inquerito=id_inquerito)
    return _com_fila(job) if job else None


@router.post("/api/investigacao/jobs/{id_job}/cancelar")
def cancelar_job(id_job: int, usuario: dict = Depends(exigir_aprovado)):
    """Pede para o job parar. Só o dono, um gestor do inquérito ou um admin."""
    job = _job_visivel(id_job, usuario, escrita=True)
    if not jobs_utils.pode_controlar(job, usuario):
        raise HTTPException(status_code=403,
                            detail="Só quem iniciou o processamento (ou um gestor do inquérito) pode cancelá-lo.")
    if not jobs_utils.solicitar_cancelamento(id_job):
        raise HTTPException(status_code=409, detail="Este processamento já terminou.")
    return {"ok": True, "mensagem": "Cancelamento solicitado."}


@router.post("/api/investigacao/jobs/{id_job}/liberar")
def liberar_job(id_job: int, usuario: dict = Depends(exigir_aprovado)):
    """Destrava um job que ficou preso (o antigo botão "resetar estado"), agora com alvo e dono.

    O reaper por batimento já cobre o caso normal em poucos minutos; isto é a saída manual para
    quem não quer esperar."""
    job = _job_visivel(id_job, usuario, escrita=True)
    if not jobs_utils.pode_controlar(job, usuario):
        raise HTTPException(status_code=403,
                            detail="Só quem iniciou o processamento (ou um gestor do inquérito) pode liberá-lo.")
    if not jobs_utils.liberar(id_job):
        raise HTTPException(status_code=409, detail="Este processamento já terminou.")
    return {"ok": True, "mensagem": "Processamento liberado."}


STATUS_VALIDOS = ("pendente", "aguardando_vaga", "processando",
                  "concluido", "cancelado", "erro")

_FILA = ("pendente", "aguardando_vaga", "processando")


@router_admin.get("/api/admin/jobs")
def jobs_do_sistema(ativos: bool = True, tp_job: str | None = None,
                    status: str | None = None, busca: str | None = None,
                    limite: int = 200):
    """Todos os jobs do servidor + ocupação das vagas de CPU — a tela Administração → Jobs & Filas.

    Responde "por que meu OCR está na fila?" sem SSH: mostra o que está ocupando as vagas, de
    qual inquérito e de quem. `status` é um cd_status único ou a palavra "fila" (os três ativos);
    `ativos=false` é o histórico (qualquer status, com `tp_job`/`busca`/`status` refinando).
    Cada job já vem com nu_inquerito, no_usuario e qt_na_frente (jobs_utils.listar_admin)."""
    from funcoes import jobs_pesados

    if tp_job is not None and tp_job not in jobs_utils.ROTULOS:
        raise HTTPException(status_code=400, detail="Tipo de job desconhecido.")

    if status == "fila":
        lista_status: list[str] | None = list(_FILA)
    elif status:
        if status not in STATUS_VALIDOS:
            raise HTTPException(status_code=400, detail="Status desconhecido.")
        lista_status = [status]
    else:
        lista_status = list(_FILA) if ativos else None

    return {
        "jobs": jobs_utils.listar_admin(tp_job=tp_job, status=lista_status,
                                        busca=busca, limite=limite),
        "vagas": jobs_pesados.ocupacao(),
        "tipos": jobs_utils.ROTULOS,
        "resumo": jobs_utils.contagem_status(),
    }


@router_admin.get("/api/admin/filas/extracoes")
def fila_de_extracoes(status: str = "fila", busca: str | None = None, limite: int = 100):
    """A outra fila do sistema: extrações de .ufdr (INQ_TB02_EXTRACOES).

    A fila é a própria tabela — o despachante (investigacao_utils._despachante_extracoes) tira as
    'pendente' conforme abre vaga, respeitando EXTRACOES_SIMULTANEAS. Esta consulta é a leitura
    espelhada do que ele enxerga: quem está esperando, quem está rodando (com a fase e o
    progresso que o worker grava), e o resumo que alimenta os KPIs da tela."""
    from funcoes.db_utils import get_connection
    from funcoes.investigacao_utils import _limite_extracoes

    if status not in ("fila", "concluido", "erro", "todas"):
        raise HTTPException(status_code=400, detail="Status desconhecido.")

    condicoes: list[str] = []
    parametros: list = []
    if status == "fila":
        condicoes.append("e.cd_status IN ('pendente', 'processando')")
        ordem = "e.cd_status = 'pendente' DESC, e.dt_upload"
    elif status == "todas":
        ordem = "e.id_extracao DESC"
    else:
        condicoes.append("e.cd_status = %s")
        parametros.append(status)
        ordem = "e.id_extracao DESC"
    if busca:
        condicoes.append(
            "(i.nu_inquerito::text ILIKE %s OR e.no_arquivo ILIKE %s "
            "OR e.no_dispositivo ILIKE %s OR u.no_usuario ILIKE %s)"
        )
        like = f"%{busca}%"
        parametros.extend([like, like, like, like])
    onde = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    parametros.append(max(1, min(500, limite)))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT e.id_extracao, e.id_inquerito, i.nu_inquerito, e.no_arquivo,
                       e.no_dispositivo, e.cd_status, e.cd_fase, e.qt_progresso_fase,
                       e.tx_erro, e.fl_cancelar, e.dt_upload, e.dt_inicio_processamento,
                       e.dt_ultima_atividade, e.dt_fim_processamento, e.id_servidor,
                       s.no_servidor, u.no_usuario,
                       (SELECT count(*) FROM investigacao.inq_tb02_extracoes f
                         WHERE f.cd_status = 'pendente'
                           AND f.id_servidor IS NULL
                           AND f.dt_upload < e.dt_upload) AS qt_na_frente
                FROM investigacao.inq_tb02_extracoes e
                LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = e.id_usuario
                LEFT JOIN investigacao.no_tb01_servidores s ON s.id_servidor = e.id_servidor
                {onde}
                ORDER BY {ordem}
                LIMIT %s
                """,
                tuple(parametros),
            )
            colunas = [d.name for d in cur.description]
            extracoes = [dict(zip(colunas, row)) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT count(*) FILTER (WHERE cd_status = 'pendente'),
                       count(*) FILTER (WHERE cd_status = 'processando'
                                        AND id_servidor IS NULL),
                       count(*) FILTER (WHERE cd_status = 'processando'
                                        AND id_servidor IS NOT NULL)
                FROM investigacao.inq_tb02_extracoes
                """
            )
            pendentes, rodando_local, rodando_remotas = cur.fetchone()

    return {
        "extracoes": extracoes,
        "resumo": {
            "pendentes": int(pendentes),
            "rodando_local": int(rodando_local),
            "rodando_remotas": int(rodando_remotas),
            "limite_local": _limite_extracoes(),
        },
    }
