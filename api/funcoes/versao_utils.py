"""Identidade de versão da instalação e ponte com o atualizador do host.

**Versão instalada.** Vive no arquivo `VERSION` da raiz do repositório, montado em `/app/VERSION`
pelo docker-compose.yml (volume read-only do serviço `api`). É a mesma string que vira tag de
release no GitHub — ex.: `1.8.0`.

**Checagem de "tem versão nova?".** A API NÃO fala com o GitHub nem enxerga o token: quem faz isso
é o script `analyser-update` no host (`scripts/analyser-update`, instalado por
`scripts/instalar-host.sh`), que roda de tempos em tempos e grava
`/opt/analyser/.deploy/controle/disponivel.json`. A API só lê esse arquivo. Assim o token do
GitHub fica exclusivamente no host, com permissão 0600, e o container não tem como vazá-lo.

  On-demand: o botão "Verificar" (`forcar=True`) grava `checar.req` no diretório de controle; o
  path unit `analyser-update-check-req@` do host dispara um `analyser-update check` na hora, e a
  API espera (até ~12 s) o `disponivel.json` ser reescrito antes de responder. Sem esse handshake
  o botão só releria a última checagem do timer (4x/dia).

  Release malformado: o `check` também confere que o `VERSION` da tag bate com o nome dela. Se
  não bater (corte sem bump — já houve com 1.7.11 e 1.7.19), `disponivel.json` recebe
  `malformado: true` + `erro`, a tela mostra o aviso e o update NÃO é oferecido (o `apply`
  recusaria de qualquer forma).

  Fallback: se `disponivel.json` não existir mas houver um token em `/app/.deploy/token` legível
  pelo container, a API consulta o GitHub diretamente (inclusive a conferência de VERSION×tag).
  É o caminho simples para quem ainda não montou o agente de host; ver docs/atualizacao.md.

**Aplicar a atualização.** O clique de "Atualizar" grava `pedido.json` no diretório de controle
(sub-mount `:rw`); um path unit do systemd no host acorda o `analyser-update apply`, que baixa,
extrai, sobe com `--build` e escreve `progresso.log` + `resultado.json`, lidos de volta pela tela.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ARQUIVO_VERSAO = Path("/app/VERSION")
DIR_DEPLOY = Path("/app/.deploy")
DIR_CONTROLE = DIR_DEPLOY / "controle"

ARQUIVO_TOKEN = DIR_DEPLOY / "token"
ARQUIVO_BUILD_INFO = DIR_DEPLOY / "build-info"
# Marcador que o `scripts/analyser-update` do host cria/atualiza a cada execução. Só existe onde
# o agente systemd foi instalado (Linux + instalar-host.sh) — no Docker Desktop / Windows local
# ele nunca aparece, e é o que impede o botão "Atualizar" de virar um clique que trava para
# sempre (não há systemd para consumir o pedido.json).
ARQUIVO_AGENTE = DIR_DEPLOY / "agente"
ARQUIVO_DISPONIVEL = DIR_CONTROLE / "disponivel.json"
ARQUIVO_PEDIDO = DIR_CONTROLE / "pedido.json"
ARQUIVO_PROGRESSO = DIR_CONTROLE / "progresso.log"
ARQUIVO_RESULTADO = DIR_CONTROLE / "resultado.json"
# "Verificar" na tela grava isto; o path unit `analyser-update-check-req@` do host dispara um
# `analyser-update check` na hora e o próprio check apaga o arquivo. Sem esse handshake o botão
# só relê o disponivel.json que o timer (4x/dia) tiver deixado — a origem da confusão de alguém
# publicar um release e o servidor seguir dizendo "em dia" por horas.
ARQUIVO_CHECAR_REQ = DIR_CONTROLE / "checar.req"

REPO_PADRAO = "HLSDEO/analyser"
_TTL_CACHE_GITHUB_S = 3600
_VALIDADE_DISPONIVEL_S = 7 * 24 * 3600  # disponivel.json mais velho que isto = "desatualizado"
_ESPERA_CHECK_HOST_S = 12.0  # quanto o "Verificar" espera o host reescrever o disponivel.json

_cache_github: dict = {"consultado_em": 0.0, "resultado": None}


# ---------------------------------------------------------------------------------------------------
# Versão instalada
# ---------------------------------------------------------------------------------------------------

def versao_instalada() -> str:
    """String de `/app/VERSION`, sem espaços; `"desconhecida"` se o arquivo não estiver montado."""
    try:
        return ARQUIVO_VERSAO.read_text(encoding="utf-8").strip() or "desconhecida"
    except OSError:
        return "desconhecida"


def como_tupla(versao: str) -> tuple:
    """`"1.8.0"` -> `(1, 8, 0)`. Ignora prefixo/sufixo (`v1.8.0`, `1.8.0-rc1`). `(0,0,0)` se não casar."""
    import re

    m = re.match(r"^\D*(\d+)\.(\d+)\.(\d+)", versao or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def _ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _build_info() -> dict:
    """Commit/data do build, se o analyser-update tiver deixado o arquivo. Só informativo."""
    return _ler_json(ARQUIVO_BUILD_INFO)


def resumo() -> dict:
    """Payload de `/api/sistema/versao` — versão instalada + build info. Não toca em rede nem em banco."""
    return {"instalada": versao_instalada(), "build": _build_info()}


# ---------------------------------------------------------------------------------------------------
# Checagem de atualização disponível
# ---------------------------------------------------------------------------------------------------

def _ler_token() -> str | None:
    try:
        texto = ARQUIVO_TOKEN.read_text(encoding="utf-8").strip()
        if texto:
            return texto
    except OSError:
        pass
    return os.environ.get("UPDATE_GITHUB_TOKEN") or None


def _versao_no_ref(repo: str, ref: str, token: str) -> str | None:
    """Conteúdo de `VERSION` no ref (tag) dado, sem espaços. `None` se não deu para ler —
    nesse caso NÃO se conclui nada (não marca malformado por falha de rede)."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/contents/VERSION",
            params={"ref": ref},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return (resp.text or "").strip().lstrip("v") or None


def _consultar_github(repo: str) -> dict:
    token = _ler_token()
    if not token:
        return {"erro": "checagem de atualização não configurada (agente do host — ver docs/atualizacao.md)"}
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return {"erro": f"falha ao contatar o GitHub: {exc}"}

    if resp.status_code == 404:
        return {"erro": "o repositório não tem nenhum release publicado"}
    if resp.status_code in (401, 403):
        return {"erro": "token de atualização inválido ou sem permissão de leitura"}
    if resp.status_code != 200:
        return {"erro": f"GitHub respondeu {resp.status_code}"}

    dados = resp.json()
    tag_bruta = (dados.get("tag_name") or "").strip()
    tag = tag_bruta.lstrip("v")
    # O VERSION da tag tem que bater com o nome dela; senão é release cortado sem bump do
    # VERSION (já aconteceu com 1.7.11 e 1.7.19) e o `analyser-update apply` recusaria.
    versao_na_tag = _versao_no_ref(repo, tag_bruta or tag, token) if tag else None
    malformado = bool(versao_na_tag) and como_tupla(versao_na_tag) != como_tupla(tag)
    return {
        "tag": tag,
        "notas": dados.get("body") or "",
        "publicada_em": dados.get("published_at"),
        "url": dados.get("html_url"),
        "malformado": malformado,
        "erro": (
            f"release {tag} malformado: VERSION={versao_na_tag} na tag (bump esquecido no corte)"
            if malformado else None
        ),
    }


def _fonte_disponivel(repo: str, forcar: bool) -> dict:
    """De onde vem a info de "última versão publicada": o arquivo do host, ou o GitHub direto."""
    do_host = _ler_json(ARQUIVO_DISPONIVEL)
    if do_host.get("tag"):
        idade = time.time() - do_host.get("verificado_em", 0)
        # erro do próprio check (release malformado — VERSION da tag não bate) tem prioridade
        # sobre "info velha"; ambos são mostrados mas não bloqueiam a leitura.
        erro = do_host.get("erro")
        if not erro and idade >= _VALIDADE_DISPONIVEL_S:
            erro = "info do host desatualizada (o agente não roda há dias)"
        return {
            "tag": str(do_host["tag"]).lstrip("v"),
            "notas": do_host.get("notas") or "",
            "publicada_em": do_host.get("publicada_em"),
            "url": do_host.get("url"),
            "malformado": bool(do_host.get("malformado")),
            "verificado_em": do_host.get("verificado_em"),
            "erro": erro,
        }

    agora = time.time()
    if not forcar and _cache_github["resultado"] and agora - _cache_github["consultado_em"] < _TTL_CACHE_GITHUB_S:
        return _cache_github["resultado"]
    resultado = _consultar_github(repo)
    _cache_github["resultado"] = resultado
    _cache_github["consultado_em"] = agora
    return resultado


def _mtime(caminho: Path) -> float:
    try:
        return caminho.stat().st_mtime
    except OSError:
        return 0.0


def _solicitar_check_host() -> bool:
    """Pede ao agente do host um `analyser-update check` na hora (grava `checar.req`) e espera
    o `disponivel.json` ser reescrito, até `_ESPERA_CHECK_HOST_S`.

    Retorna `True` se o arquivo foi atualizado dentro da janela. No-op (retorna `False`) quando
    não há agente de host — aí o "Verificar" cai no caminho GitHub-direto de sempre. Num host
    anterior a esta versão (sem o path unit `analyser-update-check-req@`) a espera simplesmente
    expira e segue-se com o `disponivel.json` que houver; o `checar.req` órfão é limpo pelo
    próximo `check` do timer."""
    if not agente_host_instalado() or not DIR_CONTROLE.is_dir():
        return False
    antes = _mtime(ARQUIVO_DISPONIVEL)
    try:
        # se sobrou um checar.req (host sem o path unit), recria — truncar arquivo de outro dono
        # daria EPERM, apagar+recriar não (controle/ é 777 sem sticky). Mesmo motivo do pedido.json.
        try:
            ARQUIVO_CHECAR_REQ.unlink()
        except FileNotFoundError:
            pass
        ARQUIVO_CHECAR_REQ.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
    except OSError:
        return False
    limite = time.time() + _ESPERA_CHECK_HOST_S
    while time.time() < limite:
        time.sleep(0.5)
        if _mtime(ARQUIVO_DISPONIVEL) > antes:
            return True
    return False


def checar_atualizacao(repo: str = REPO_PADRAO, forcar: bool = False) -> dict:
    """`{instalada, disponivel, versao_disponivel, notas, publicada_em, url, verificado_em,
    malformado, erro}`. Nunca levanta.

    `forcar=True` (botão "Verificar") dispara uma checagem real no host antes de ler — sem isso
    o retorno é só a última do timer."""
    instalada = versao_instalada()
    if forcar:
        _solicitar_check_host()
    base = _fonte_disponivel(repo, forcar)

    if base.get("erro") and not base.get("tag"):
        return {
            "instalada": instalada, "disponivel": False, "versao_disponivel": None,
            "notas": None, "publicada_em": None, "url": None, "verificado_em": None,
            "malformado": False, "erro": base["erro"],
            "agente_host": agente_host_instalado(),
        }

    tag = base["tag"]
    malformado = bool(base.get("malformado"))
    return {
        "instalada": instalada,
        # release malformado (VERSION da tag não bate) não é oferecido — o apply recusaria.
        "disponivel": como_tupla(tag) > como_tupla(instalada) and not malformado,
        "versao_disponivel": tag,
        "notas": base.get("notas"),
        "publicada_em": base.get("publicada_em"),
        "url": base.get("url"),
        "verificado_em": base.get("verificado_em"),
        "malformado": malformado,
        "erro": base.get("erro"),  # "info do host desatualizada" / "release X malformado" — mostra, não bloqueia
        # O botão "Atualizar" só faz sentido onde há agente systemd para consumir o pedido.
        "agente_host": agente_host_instalado(),
    }


# ---------------------------------------------------------------------------------------------------
# Aplicar atualização — handshake com o analyser-update do host
# ---------------------------------------------------------------------------------------------------

def _atualizacao_em_andamento() -> bool:
    return ARQUIVO_PEDIDO.exists()


def agente_host_instalado() -> bool:
    """True só onde o `analyser-update` do host já rodou (cria/atualiza `.deploy/agente`).
    É o que separa uma instalação on-prem Linux de um `docker compose` no Windows/dev."""
    return ARQUIVO_AGENTE.is_file()


def solicitar_atualizacao(versao_alvo: str, solicitante: str) -> dict:
    """Grava `pedido.json` para o path unit do host disparar o `analyser-update apply`.

    Recusa se: não há agente de host instalado, já há um pedido em andamento, a versão alvo não é
    mais nova que a instalada, ou o release alvo está marcado como malformado (VERSION da tag)."""
    if not agente_host_instalado() or not DIR_CONTROLE.is_dir():
        return {"aceito": False, "erro": "agente de atualização do host não instalado — atualize pelo servidor (ver docs/atualizacao.md)"}
    if _atualizacao_em_andamento():
        return {"aceito": False, "erro": "já existe uma atualização em andamento"}

    instalada = versao_instalada()
    if como_tupla(versao_alvo) <= como_tupla(instalada):
        return {"aceito": False, "erro": f"versão {versao_alvo} não é mais nova que a instalada ({instalada})"}

    base = _ler_json(ARQUIVO_DISPONIVEL)
    if base.get("malformado") and str(base.get("tag", "")).lstrip("v") == versao_alvo.lstrip("v"):
        return {"aceito": False, "erro": base.get("erro") or f"release {versao_alvo} malformado — o corte não subiu o VERSION; corrija o release antes"}

    pedido = {
        "versao": versao_alvo,
        "solicitante": solicitante,
        "instalada_no_pedido": instalada,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # progresso.log / resultado.json de uma atualização anterior foram criados pelo
    # analyser-update rodando como root (0644). Aqui somos o uid do container: não dá para
    # truncar um arquivo de root, mas dá para apagá-lo (o diretório controle/ é 777 sem sticky)
    # e recriar. O analyser-update recria de novo, como root, ao começar o apply.
    try:
        for antigo in (ARQUIVO_PROGRESSO, ARQUIVO_RESULTADO):
            try:
                antigo.unlink()
            except FileNotFoundError:
                pass
        ARQUIVO_PROGRESSO.write_text("", encoding="utf-8")
        # pedido.json por último: é o gatilho que o path unit observa.
        ARQUIVO_PEDIDO.write_text(json.dumps(pedido, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {"aceito": False, "erro": f"não foi possível gravar o pedido: {exc}"}
    return {"aceito": True, "pedido": pedido}


def progresso_atualizacao() -> dict:
    """`{rodando, linhas: [str], resultado: {ok, versao, erro?}|None}` para a tela acompanhar."""
    linhas: list[str] = []
    try:
        linhas = ARQUIVO_PROGRESSO.read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    resultado = _ler_json(ARQUIVO_RESULTADO) or None
    return {
        "rodando": _atualizacao_em_andamento(),
        "linhas": linhas,
        "resultado": resultado,
    }
