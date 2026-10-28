"""Reinicio do servidor (`docker compose up -d`) pedido pela tela Desempenho.

Por que nao um subprocess direto na API: a API roda DENTRO de um container — a imagem nao
tem o binario do docker, nao monta o socket e roda como appuser. `docker compose` so existe
no host. O caminho e o mesmo do atualizador (ver funcoes/versao_utils.py e
docs/atualizacao.md): a API grava um pedido em /app/.deploy/controle (bind com escrita
para o host) e uma path unit do systemd no host dispara o `analyser-update reiniciar`, que
sobe os containers, espera a API voltar e apaga o pedido ao terminar.

Sem agente de host (dev local, Docker Desktop), cai no caminho direto: dispara
`docker compose up -d` como subprocesso destacado a partir da pasta do compose e devolve na
hora. Destacado de proposito: o compose recria o proprio container da API segundos depois,
entao esperar pelo processo seria a propria requisicao morrendo no meio.

Terceiro caso: host sem systemd mas COM o vigia `scripts/observar-reinicio.ps1` rodando
(dev no Windows). O vigia toca um "coracao" em controle/ a cada poucos segundos; vendo um
coracao fresco, a API grava o `reiniciar.req` do mesmo jeito que com o agente — e o vigia
consome. Sem coracao fresco e sem docker ao alcance, a resposta orienta a rodar na mao.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from funcoes.versao_utils import DIR_CONTROLE, agente_host_instalado

logger = logging.getLogger(__name__)

ARQUIVO_REINICIAR_REQ = DIR_CONTROLE / "reiniciar.req"
ARQUIVO_REINICIAR_LOG = DIR_CONTROLE / "reiniciar.log"
ARQUIVO_REINICIAR_RESULTADO = DIR_CONTROLE / "reiniciar-resultado.json"
# "Coracao" do vigia de dev (scripts/observar-reinicio.ps1): ele reescreve este arquivo a cada
# poucos segundos. Fresco = ha alguem no host consumindo o .req; velho/ausente = o vigia morreu
# (ou nunca rodou) e gravar um pedido seria um reinicio que ninguem executa.
ARQUIVO_OBSERVADOR_OK = DIR_CONTROLE / "observador-reinicio.ok"
_LIMITE_CORACAO_S = 120

COMANDO_MANUAL = "docker compose up -d"


def _ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pedido_em_andamento() -> bool:
    return ARQUIVO_REINICIAR_REQ.exists()


def observador_vivo() -> bool:
    """Ha um vigia no host consumindo `reiniciar.req` agora?

    Nunca levanta: controle/ inexistente ou coracao velho = False, e o chamador cai no
    caminho direto ou orienta o comando manual."""
    try:
        idade = time.time() - ARQUIVO_OBSERVADOR_OK.stat().st_mtime
    except OSError:
        return False
    return idade < _LIMITE_CORACAO_S


def _dir_compose() -> Path | None:
    """Pasta com o docker-compose.yml, subindo a partir do cwd (caminho de dev).

    Dentro do container nao ha compose (o Dockerfile nao o copia), entao aqui retorna None
    e o chamador orienta a rodar na mao — que e o comportamento certo onde ha agente.
    """
    aqui = Path.cwd().resolve()
    for base in [aqui, *list(aqui.parents)[:4]]:
        if (base / "docker-compose.yml").is_file():
            return base
    return None


def _disparar_direto() -> dict:
    """Roda `docker compose up -d` em background e devolve na hora (só dev/host sem agente)."""
    pasta = _dir_compose()
    if pasta is None:
        return {"aceito": False, "via": None,
                "erro": "não foi possível localizar o docker-compose.yml por aqui"}
    docker = shutil.which("docker")
    if not docker:
        return {"aceito": False, "via": None,
                "erro": "docker não encontrado neste ambiente"}
    try:
        opcoes: dict = {"cwd": str(pasta), "stdout": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
        if os.name == "posix":
            opcoes["start_new_session"] = True
        else:
            opcoes["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen([docker, "compose", "up", "-d"], **opcoes)
    except OSError as exc:
        logger.warning("Reinicio direto falhou: %s", exc)
        return {"aceito": False, "via": None, "erro": f"falha ao disparar o compose: {exc}"}
    return {"aceito": True, "via": "direto", "erro": None}


def solicitar_reiniciar(solicitante: str) -> dict:
    """Registra o pedido de reinicio. Sempre assincrono: quem executa e o host ou o compose
    em background, nunca esta requisicao (o container da API cai no meio do `up -d`)."""
    if _pedido_em_andamento():
        return {"aceito": False, "via": None, "erro": "já existe um reinício em andamento"}

    if (agente_host_instalado() or observador_vivo()) and DIR_CONTROLE.is_dir():
        pedido = {"solicitante": solicitante, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        try:
            # log/resultado de um reinicio anterior foram criados pelo analyser-update como root:
            # nao da para truncar como uid do container, mas da para apagar (controle/ e 777 sem
            # sticky) e recriar. Mesmo padrao do pedido.json em versao_utils.solicitar_atualizacao.
            for antigo in (ARQUIVO_REINICIAR_LOG, ARQUIVO_REINICIAR_RESULTADO):
                try:
                    antigo.unlink()
                except FileNotFoundError:
                    pass
            ARQUIVO_REINICIAR_LOG.write_text("", encoding="utf-8")
            # reiniciar.req por ultimo: e o gatilho que a path unit observa.
            ARQUIVO_REINICIAR_REQ.write_text(json.dumps(pedido, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            return {"aceito": False, "via": None, "erro": f"não foi possível gravar o pedido: {exc}"}
        logger.info("Reinicio do servidor solicitado por %s (via agente do host)", solicitante)
        return {"aceito": True, "via": "host", "erro": None}

    logger.info("Reinicio do servidor solicitado por %s (sem agente de host — tentativa direta)", solicitante)
    return _disparar_direto()


def status_reiniciar() -> dict:
    """`{reiniciando, agente_host, resultado, comando}` para a tela acompanhar.

    `reiniciando` e o pedido ainda nao consumido pelo host; `resultado` e o que o host
    escreveu ao terminar (`{ok, erro?, ts}`) — e como a tela descobre que o compose falhou
    sem precisar do journald. Nunca levanta."""
    return {
        "reiniciando": _pedido_em_andamento(),
        "agente_host": agente_host_instalado(),
        "observador": observador_vivo(),
        "resultado": _ler_json(ARQUIVO_REINICIAR_RESULTADO) or None,
        "comando": COMANDO_MANUAL,
    }
