"""Rotas de sistema: disponibilidade da API, métricas do servidor, versão instalada e atualização."""

import logging
import os
import shutil

import psutil
from fastapi import APIRouter, Depends, Response
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

from funcoes import migrar_utils, reiniciar_utils, versao_utils
from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes.db_utils import estatisticas_pool, get_connection
from funcoes.desempenho_utils import detectar_hardware

logger = logging.getLogger(__name__)

router = APIRouter()
router_admin = APIRouter(dependencies=[Depends(exigir_admin)])


@router.get("/api/health")
def health(resposta: Response):
    """Health check usado pelo healthcheck do compose e por monitoramento.

    Antes daqui isto devolvia `{"ok": True}` literal, sem tocar em nada: com o pool de conexões
    esgotado por um job longo — o modo de falha típico com muita gente usando ao mesmo tempo — a
    aplicação inteira respondia 500 e o container continuava marcado como *healthy*. Agora o
    health faz o que promete: pede uma conexão e roda um SELECT 1.

    Três desfechos, de propósito diferentes entre si:
      - banco responde  -> 200, `banco: "ok"`;
      - pool cheio      -> 200, `banco: "ocupado"` — a API está viva, só sem vaga no pool; marcar
        o container como doente a cada pico de carga só geraria ruído. O número que importa
        (`requests_waiting`) vai no corpo;
      - banco inacessível -> 503, `banco: "indisponivel"`, e aí o container fica *unhealthy*.

    Timeout de 1 s para pegar a conexão: o healthcheck do compose desiste em 3 s, e este endpoint
    não pode entrar na fila do pool junto com as requisições de verdade.

    `replica` identifica qual processo está respondendo (as métricas de `pool` são POR
    PROCESSO — com api-1/api-2 atrás do nginx, sem este rótulo não dá para saber de quem é
    o número que a tela de Desempenho mostra). Ausente = instalação de API única."""
    replica = (os.getenv("API_REPLICA_ID") or "").strip() or None
    estatisticas = estatisticas_pool()
    try:
        with get_connection(espera_s=1) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"ok": True, "banco": "ok", "pool": estatisticas, "replica": replica}
    except PoolTimeout:
        logger.warning("Health check: pool sem vaga (%s)", estatisticas)
        return {"ok": True, "banco": "ocupado", "pool": estatisticas, "replica": replica}
    except Exception as exc:
        # A rota é pública (o healthcheck do compose bate nela sem sessão), então o corpo não leva o
        # str(exc): a mensagem do psycopg costuma trazer host, porta, banco e usuário, e isso é a
        # topologia interna entregue a quem só alcança a porta do nginx. O erro completo, com stack
        # trace, fica no log de quem opera.
        logger.error("Health check: banco inacessível", exc_info=exc)
        resposta.status_code = 503
        return {"ok": False, "banco": "indisponivel", "pool": estatisticas}


@router.get("/api/sistema/versao", dependencies=[Depends(exigir_aprovado)])
def versao():
    """Versão instalada (arquivo VERSION) — para o rodapé e para suporte. Não toca na rede."""
    return versao_utils.resumo()


@router_admin.get("/api/sistema/migracoes")
def migracoes():
    """Situação das migrações de schema neste banco (não toca na rede) — usado ao abrir a tela."""
    return migrar_utils.estado()


@router_admin.get("/api/sistema/atualizacao")
def atualizacao(forcar: bool = False):
    """Versão instalada × última publicada + migrações.

    `?forcar=1` (clique em "Verificar") pede ao agente do host uma checagem nova e espera o
    resultado (~12 s); sem isso devolve a última do timer (4x/dia). Ver versao_utils."""
    return {
        "versao": versao_utils.checar_atualizacao(forcar=forcar),
        "migracoes": migrar_utils.estado(),
    }


@router_admin.post("/api/sistema/migracoes/aplicar")
def aplicar_migracoes():
    """Aplica os deltas de schema pendentes agora (o mesmo que o runner faz no startup).
    Útil quando `MIGRAR_NO_STARTUP=0` ou para não precisar reiniciar a API."""
    return migrar_utils.executar(aplicada_por="tela servidor")


class AtualizarIn(BaseModel):
    versao: str


@router_admin.post("/api/sistema/atualizacao/aplicar")
def aplicar_atualizacao(payload: AtualizarIn, usuario: dict = Depends(exigir_admin)):
    """Enfileira a atualização: grava o pedido para o agente `analyser-update` do host executar
    (baixar a tag, extrair, `docker compose up -d --build`). A tela acompanha por `.../progresso`.
    A API não baixa nem reconstrói nada — ver funcoes/versao_utils.py e docs/atualizacao.md."""
    return versao_utils.solicitar_atualizacao(payload.versao.strip(), usuario.get("no_login", "?"))


@router_admin.get("/api/sistema/atualizacao/progresso")
def progresso_atualizacao():
    """Linhas do log do `analyser-update` em curso + o resultado final, quando houver."""
    return versao_utils.progresso_atualizacao()


@router_admin.post("/api/sistema/reiniciar")
def solicitar_reiniciar(usuario: dict = Depends(exigir_admin)):
    """Pede o reinicio do servidor (`docker compose up -d`) — o que aplica os valores da tela
    Desempenho que ainda estao so no .env (pool, caches, PG_*).

    Sempre assincrono: com agente de host, grava `reiniciar.req` para a path unit
    `analyser-reiniciar@` executar; sem agente (dev), tenta disparar o compose direto. Sem
    nenhum dos dois, devolve o comando para rodar na mao — sempre 200 com `aceito`, no mesmo
    padrao de `/api/sistema/atualizacao/aplicar`."""
    r = reiniciar_utils.solicitar_reiniciar(usuario.get("no_login") or "?")
    if r["aceito"]:
        logger.info("Reinicio solicitado por %s (via=%s)", usuario.get("no_login") or "?", r["via"])
    return {**r, "comando": reiniciar_utils.COMANDO_MANUAL}


@router_admin.get("/api/sistema/reiniciar")
def status_reiniciar():
    """Pedido de reinicio ainda pendente + resultado do ultimo — para a tela acompanhar a
    volta sem precisar do journald do host."""
    return reiniciar_utils.status_reiniciar()


def _uso_disco(caminho: str) -> dict:
    total, usado, _livre = shutil.disk_usage(caminho)
    return {"total": total, "usado": usado, "percentual": round(usado / total * 100, 1)}


@router_admin.get("/api/sistema/metricas")
def metricas():
    """CPU, memória e disco vistos de dentro do container da API — /app/data é o volume onde
    fica o material de investigação (o que mais importa para planejamento de capacidade);
    "/" é o filesystem do próprio container, útil para notar disco cheio por logs/cache."""
    memoria = psutil.virtual_memory()
    # O psutil enxerga a máquina hospedeira. Enquanto não houver mem_limit/cpus no compose isso é
    # o que queremos, mas se um dia houver, os números acima passam a descrever recursos que o
    # container não pode usar — "disponivel" abaixo respeita o cgroup e é o que o dimensionamento
    # automático (funcoes/desempenho_utils.py) de fato usa. Vem ao lado, e não no lugar, para não
    # mudar o painel do servidor que já lê estas chaves.
    disponivel = detectar_hardware()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "cpu_cores": psutil.cpu_count() or 1,
        "disponivel": disponivel,
        "memoria": {
            "total": memoria.total,
            "usado": memoria.total - memoria.available,
            "percentual": memoria.percent,
        },
        "discos": [
            {"rotulo": "Dados (evidências)", "caminho": "/app/data", **_uso_disco("/app/data")},
            {"rotulo": "Sistema (container)", "caminho": "/", **_uso_disco("/")},
        ],
    }
