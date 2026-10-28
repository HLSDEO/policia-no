"""Formatter de log em JSON, usado tanto pelos loggers da aplicação (funcoes.*, rotas.*) quanto
pelos do uvicorn (uvicorn, uvicorn.access, uvicorn.error) — a config em si fica em api/logging.json,
carregada via `--log-config` (Dockerfile). Não dá para configurar isso daqui de dentro de main.py:
o uvicorn aplica seu próprio logging (Config.configure_logging) antes de importar main:app, então
qualquer dictConfig feito depois, no módulo da aplicação, chegaria tarde demais para os loggers
uvicorn.* — por isso o --log-config no comando de start, que roda primeiro.
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Onde o arquivo de erros é gravado dentro do container. É um bind mount (ver docker-compose.yml),
# então o mesmo arquivo aparece em ./logs/ no host: dá para ler um erro de ontem sem
# `docker compose logs` e sem entrar no container.
PASTA_LOGS = Path(os.getenv("PASTA_LOGS", "/app/logs"))

# Quantos dias de erro ficam guardados (um arquivo por dia, girado à meia-noite).
DIAS_HISTORICO_ERROS = 30

# Atributos padrão de um LogRecord (Python 3.12) — tudo que não estiver aqui veio de `extra={...}`
# num chamado de log e entra solto no JSON, permitindo campos contextuais (ex.: id_inquerito).
_ATRIBUTOS_PADRAO = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        for chave, valor in record.__dict__.items():
            if chave not in _ATRIBUTOS_PADRAO and chave not in dados:
                dados[chave] = valor
        if record.exc_info:
            dados["exception"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False, default=str)


def _nome_arquivo_erros() -> str:
    """`erros.log`, ou `erros-<rótulo>-<pid>.log` com mais de um worker do uvicorn OU com
    `API_REPLICA_ID` definido (o compose marca cada réplica — api-1/api-2 — com o seu).

    Dois processos girando o mesmo TimedRotatingFileHandler disputam o rename da meia-noite e
    perdem registros — não há trava entre processos aqui, e o bind mount ./logs é o MESMO no
    host para todas as réplicas. Um arquivo por processo é mais chato de ler, mas é o que não
    perde erro. Com um worker numa única API (o padrão antigo) o nome continua o simples."""
    rotulo = (os.getenv("API_REPLICA_ID") or "").strip()
    try:
        varios = int((os.getenv("API_WORKERS") or "1").strip()) > 1
    except ValueError:
        varios = False
    if not rotulo and not varios:
        return "erros.log"
    return f"erros-{rotulo}-{os.getpid()}.log" if rotulo else f"erros-{os.getpid()}.log"


def handler_erros() -> logging.Handler:
    """Handler de arquivo para ERROR e acima, com rotação diária, referenciado por logging.json.

    Existe porque o log só ia para o stdout, capturado pelo driver json-file do Docker: para ver
    um erro de ontem era preciso `docker compose logs` no servidor, e o histórico dependia da
    configuração do driver (que era a de fábrica, sem teto). Aqui o erro fica num arquivo
    previsível, no mesmo JSON do JsonFormatter — uma linha por erro, com o stack trace no campo
    `exception` e, quando quem loga informa `extra={...}`, a rota e o usuário no mesmo registro.

    Falhar aqui não pode impedir a API de subir: dictConfig propaga a exceção do factory, e uma
    pasta de log sem permissão de escrita derrubaria o processo inteiro. Nesse caso o handler vira
    um NullHandler e o aviso sai no stderr — o log de stdout continua funcionando."""
    try:
        PASTA_LOGS.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            PASTA_LOGS / _nome_arquivo_erros(),
            when="midnight",
            backupCount=DIAS_HISTORICO_ERROS,
            encoding="utf-8",
            # SEM delay=True de propósito: abrir o arquivo já na subida faz uma falha de
            # permissão estourar aqui (vira NullHandler + aviso no stderr, visível no startup)
            # em vez de só na 1ª linha de erro — quando o próprio logging engole o problema e
            # o arquivo simplesmente nunca é criado. A pasta não pode ser escrita é o caso
            # típico: o Docker cria o bind mount como root e a API roda como usuário comum.
            utc=True,    # mesmo fuso do campo timestamp do JsonFormatter
        )
        handler.setFormatter(JsonFormatter())
    except OSError as exc:
        print(
            f"[logging] sem arquivo de erros em {PASTA_LOGS}: {exc} — confira se a pasta existe "
            "e é gravável pelo usuário da API (ex.: chown <usuario-do-container> logs/). "
            "Os erros continuam saindo só no stdout enquanto isso.",
            file=sys.stderr,
        )
        handler = logging.NullHandler()
    handler.setLevel(logging.ERROR)
    return handler
