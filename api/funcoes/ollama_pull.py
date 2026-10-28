"""Download (pull) de modelos no servidor Ollama, disparado pela página de Configurações.

O pull roda em thread de background e o status vai para a tabela de jobs
(JOB_TB01_JOBS, TP_JOB='ollama_pull', com o nome do modelo em NO_ESCOPO). Era um dicionário de
módulo, o que basta com um processo e para de bastar com vários: o POST que dispara cai num worker
e o polling de status cai em outro, que não tem a entrada — a barra ficaria em "nunca iniciado"
para sempre, com o download rodando. FL_PESADO=false: é espera de rede, não CPU, então não ocupa
vaga do limite de jobs pesados.

O job em si continua sendo descartável: se o servidor cair no meio, é só clicar de novo — o
Ollama retoma de onde parou.

Só se aplica ao Ollama: o /api/pull é a API nativa dele (fora do /v1 OpenAI-compatível).
LM Studio não tem endpoint de download — lá o modelo é baixado pela interface do próprio app.
"""

import json
import threading
import urllib.error
import urllib.request

from funcoes import jobs_utils

URL_OLLAMA_PADRAO = "http://ollama:11434"  # serviço Ollama do docker-compose
_TIMEOUT_PULL = 3600  # modelos de chat são grandes (5–15 GB); timeout de inatividade do socket



def _base_url(api_url: str | None) -> str:
    """Base da API nativa do Ollama a partir da URL configurada no formulário (que aponta
    para o endpoint OpenAI-compatível /v1); vazio cai no serviço do compose."""
    base = (api_url or "").strip().rstrip("/") or URL_OLLAMA_PADRAO
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base


def status_pull(modelo: str) -> dict | None:
    """Estado atual do download do modelo, no formato que o front já lê, ou None se nunca foi
    iniciado."""
    job = jobs_utils.ultimo(jobs_utils.TP_OLLAMA_PULL, escopo=modelo)
    if job is None:
        return None
    metricas = job["tx_metricas"] or {}
    if job["rodando"]:
        status = "baixando"
    elif job["cd_status"] == "concluido":
        status = "concluido"
    else:
        status = "erro"
    return {
        "status": status,
        "progresso": job["qt_processados"],
        "detalhe": metricas.get("detalhe", ""),
        "erro": job["tx_erro"] or "",
    }


def iniciar_pull(modelo: str, api_url: str | None = None, id_usuario: int | None = None) -> bool:
    """Dispara o download em background; False se já há um em andamento para este modelo.

    O guard é o índice único da tabela de jobs — dois cliques simultâneos, em workers diferentes,
    não passam os dois."""
    try:
        id_job = jobs_utils.criar(jobs_utils.TP_OLLAMA_PULL, id_usuario or 1,
                                  escopo=modelo, parametros={"modelo": modelo}, pesado=False)
    except jobs_utils.JobOcupado:
        return False
    jobs_utils.marcar_processando(id_job)
    jobs_utils.progresso(id_job, total=100, processados=0, metricas={"detalhe": "iniciando"})
    threading.Thread(target=_baixar, args=(id_job, modelo, _base_url(api_url)), daemon=True).start()
    return True


def _atualizar(id_job: int, **campos) -> None:
    """Espelha na linha do job o que o streaming do Ollama informou. `progresso` é 0-100."""
    metricas = {}
    if "detalhe" in campos:
        metricas["detalhe"] = campos["detalhe"]
    jobs_utils.progresso(id_job, processados=campos.get("progresso"), metricas=metricas or None)


def _baixar(id_job: int, modelo: str, base: str) -> None:
    """Consome o streaming NDJSON do /api/pull; cada linha traz o estágio e, nas camadas de
    download, os bytes total/baixados — vira o percentual mostrado no front."""
    corpo = json.dumps({"name": modelo, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/pull", data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_PULL) as resp:
            for linha in resp:
                try:
                    ev = json.loads(linha.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if ev.get("error"):
                    jobs_utils.falhar(id_job, str(ev["error"])[:2000])
                    return
                total, feito = ev.get("total"), ev.get("completed")
                if total:
                    _atualizar(id_job, progresso=int((feito or 0) * 100 / total),
                               detalhe=ev.get("status", ""))
                elif ev.get("status"):
                    _atualizar(id_job, detalhe=ev["status"])
        jobs_utils.progresso(id_job, processados=100)
        jobs_utils.concluir(id_job, metricas={"detalhe": "concluído"})
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", errors="replace")[:300]
        jobs_utils.falhar(id_job, f"O Ollama respondeu com erro {e.code}: {detalhe}"[:2000])
    except urllib.error.URLError as e:
        jobs_utils.falhar(id_job, f"Não foi possível conectar no Ollama: {e.reason}"[:2000])
    except Exception as e:  # conexão interrompida no meio do stream etc.
        jobs_utils.falhar(id_job, f"Download interrompido: {e}"[:2000])
