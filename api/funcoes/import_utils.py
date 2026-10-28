"""Funções acessórias para disparar e acompanhar os scripts de import (db/import/*.sql) em background.

O estado do import mora na tabela de jobs (JOB_TB01_JOBS, TP_JOB='import_referencia', com o nome
do grupo em NO_ESCOPO), e não mais num dicionário de módulo. Com mais de um worker do uvicorn,
aquele dicionário quebrava de duas formas ao mesmo tempo: o POST que dispara cai num processo e o
GET de status cai em outro, que não tem a entrada — a tela mostraria "nunca iniciado" com o import
rodando —, e o guard "esse grupo já está importando" valeria por processo, deixando dois `psql`
rodarem o mesmo script de carga simultaneamente sobre a mesma tabela.

FL_PESADO=false: este import pesa no banco, não na CPU da API, então não deve ocupar uma das vagas
reservadas ao OCR/CLIP (ver funcoes/jobs_pesados.py).
"""

import logging
import os
import subprocess
import threading
from pathlib import Path

from funcoes import jobs_utils
from funcoes.db_utils import DB_HOST, DB_PORT
from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

# Pasta com os scripts .sql, montada (read-only) a partir de db/import no host.
IMPORT_SCRIPTS_DIR = Path("/app/db/import")


def esta_rodando(grupo: str) -> bool:
    """Diz se já existe um import em andamento para esse grupo (evita disparar dois ao mesmo tempo)."""
    job = jobs_utils.ultimo(jobs_utils.TP_IMPORT_REFERENCIA, escopo=grupo)
    return bool(job and job["rodando"])


def status_job(grupo: str) -> dict:
    """Estado do último import disparado para o grupo, no formato que a tela de ingestão já lê."""
    job = jobs_utils.ultimo(jobs_utils.TP_IMPORT_REFERENCIA, escopo=grupo)
    if not job:
        return {"rodando": False, "ok": None, "mensagem": ""}
    if job["rodando"]:
        return {"rodando": True, "ok": None, "mensagem": ""}
    return {
        "rodando": False,
        "ok": job["cd_status"] == "concluido",
        "mensagem": (job["tx_metricas"] or {}).get("mensagem") or job["tx_erro"] or "",
    }


def _executar_scripts(id_job: int, grupo: str, scripts: list[str]) -> None:
    """Roda os scripts do grupo em sequência via psql, na thread em background; atualiza o estado ao final."""
    env_vars = parse_env(read_env_text())
    processo_env = os.environ.copy()
    processo_env["PGPASSWORD"] = env_vars.get("POSTGRES_PASSWORD", "")
    usuario = env_vars.get("POSTGRES_USER", "")
    banco = env_vars.get("POSTGRES_DB", "")

    ok = True
    mensagens = []
    try:
        jobs_utils.marcar_processando(id_job)
        jobs_utils.progresso(id_job, total=len(scripts))
        for indice, script in enumerate(scripts, start=1):
            script_path = IMPORT_SCRIPTS_DIR / script
            resultado = subprocess.run(
                [
                    "psql", "-h", DB_HOST, "-p", str(DB_PORT),
                    "-U", usuario, "-d", banco,
                    "-v", "ON_ERROR_STOP=1",
                    "-f", str(script_path),
                ],
                env=processo_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if resultado.returncode != 0:
                ok = False
                erro = (resultado.stderr or "").strip().splitlines()
                mensagens.append(f"{script}: {erro[-1] if erro else 'erro desconhecido'}")
                break
            mensagens.append(f"{script}: concluído")
            # o batimento evita que uma carga longa seja confundida com job travado pelo reaper
            jobs_utils.progresso(id_job, processados=indice)
    except Exception as exc:
        logger.exception("Falha ao rodar os scripts de import do grupo %s", grupo)
        jobs_utils.falhar(id_job, f"{type(exc).__name__}: {exc}"[:2000])
        return

    mensagem = " | ".join(mensagens)
    if ok:
        jobs_utils.concluir(id_job, metricas={"mensagem": mensagem})
    else:
        jobs_utils.falhar(id_job, mensagem)


def iniciar_import(grupo: str, scripts: list[str], id_usuario: int) -> bool:
    """Dispara os scripts de um grupo em background; devolve False se esse grupo já estiver importando.

    O guard agora é o índice único da tabela de jobs, e não uma checagem antes do INSERT: dois
    cliques simultâneos (ou dois workers) passariam os dois pela checagem."""
    try:
        id_job = jobs_utils.criar(jobs_utils.TP_IMPORT_REFERENCIA, id_usuario,
                                  escopo=grupo, parametros={"scripts": scripts}, pesado=False)
    except jobs_utils.JobOcupado:
        return False

    threading.Thread(target=_executar_scripts, args=(id_job, grupo, scripts), daemon=True).start()
    return True
