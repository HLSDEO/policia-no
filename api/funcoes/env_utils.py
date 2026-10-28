"""Funções acessórias para ler e gravar o arquivo .env do projeto."""

import os
import re
import tempfile
from pathlib import Path

try:
    import fcntl  # POSIX (o container é Linux); no Windows isto não existe — ver _SemFlock
except ImportError:  # pragma: no cover — fallback só fora do container
    fcntl = None

# Caminho do .env dentro do container (bind mount do .env real do host).
ENV_FILE = Path(os.environ.get("ENV_FILE", "/app/.env"))

# Caracteres que não podem ir para uma linha KEY=VALUE do .env (evita injeção de novas variáveis).
CONTROL_CHARS = re.compile(r"[\r\n\0]")


def sanitize(value: str) -> str:
    """Remove quebras de linha/caractere nulo e espaços nas pontas de um valor antes de gravar no .env."""
    return CONTROL_CHARS.sub("", value or "").strip()


def mask_key(key: str) -> str:
    """Mascara uma chave de API para exibição, mantendo só os 4 primeiros e 4 últimos caracteres visíveis."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * max(4, len(key) - 8) + key[-4:]


def read_env_text() -> str:
    """Lê o conteúdo bruto do .env; devolve string vazia se o arquivo ainda não existir.

    Com lock compartilhado (quando há flock): nunca lê meio-arquivo de uma gravação concorrente.
    """
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            return f.read()
    except FileNotFoundError:
        return ""


def parse_env(content: str) -> dict:
    """Converte o texto de um .env em um dict {CHAVE: valor}, ignorando comentários e linhas em branco."""
    values = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def upsert_env_values(content: str, updates: dict) -> str:
    """Atualiza/insere pares KEY=VALUE no texto de um .env, preservando as demais linhas e a ordem original."""
    lines = content.splitlines() if content else []
    seen = set()
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out_lines.append(line)

    # remove linhas em branco no fim antes de anexar as chaves novas
    while out_lines and out_lines[-1].strip() == "":
        out_lines.pop()

    missing = [k for k in updates if k not in seen]
    if missing:
        if out_lines:
            out_lines.append("")
        for key in missing:
            out_lines.append(f"{key}={updates[key]}")

    return "\n".join(out_lines) + "\n"


def write_env_text(content: str) -> None:
    """Grava o novo conteúdo do .env no disco e tenta restringir a permissão do arquivo (0600).

    Atômica + exclusiva (ver C9): grava num temporário no MESMO diretório e troca com os.replace
    sob lock exclusivo — dois admins salvando juntos não perdem um write nem deixam meio-arquivo
    para os leitores (que usam lock compartilhado em read_env_text). Sem flock (fora do
    container), vale só a atomicidade do replace."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(ENV_FILE.parent), delete=False,
            prefix=ENV_FILE.name + ".", suffix=".tmp",
        ) as tmp:
            tmp_path = tmp.name
            if fcntl is not None:
                fcntl.flock(tmp.fileno(), fcntl.LOCK_EX)
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass  # bind mount em host Windows não suporta permissões POSIX
        os.replace(tmp_path, ENV_FILE)
        tmp_path = None
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass  # idem acima
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_modify_write_env(updates: dict) -> dict:
    """Aplica updates ao .env de forma atômica (ler+modificar+gravar sob o mesmo lock exclusivo).

    O padrão antigo (read_env_text + upsert + write_env_text separados) perdia um write quando
    dois admins salvavam juntos: os dois liam o mesmo texto-base e o segundo sobrescrevia o
    primeiro. Use esta função nos fluxos de Configurações. Devolve o dict final parseado."""
    if fcntl is None:  # sem flock, o melhor possível é a sequência simples + replace atômico
        final = upsert_env_values(read_env_text(), updates)
        write_env_text(final)
        return parse_env(final)
    with open(ENV_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        final = upsert_env_values(f.read(), updates)
        f.seek(0)
        f.truncate()
        f.write(final)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    return parse_env(final)
