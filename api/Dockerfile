FROM python:3.12-slim

WORKDIR /app

# psql é usado para rodar os scripts de import (suportam meta-comandos como \set/\if).
# ffmpeg extrai o quadro que vira miniatura dos vídeos na extração temporária da galeria
# (funcoes/midia_temp.py) — só -frames:v 1, nunca reencode completo.
# SEM tesseract, SEM JRE, SEM torch/transformers/faster-whisper: os modelos (Tesseract, CLIP,
# whisper, embeddings) e o dumper do IPED vivem no nó da delegacia — o central navega e
# reproduz resultados (docs/no_descentralizado.md).
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        postgresql-client \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
# tunel_app:app é o ASGI do serviço `tunel` (túnel NAT — docs/no_descentralizado.md); a imagem é
# a mesma da API (mesmo contexto, cache de camadas dividido), mas o COPY é de arquivo único e
# esquecê-lo aqui deixa o serviço em crash-loop ("Could not import module tunel_app")
COPY tunel_app.py .
COPY logging.json .
# sitecustomize.py é importado pelo Python em todo interpretador — é o ÚNICO ponto que roda no
# processo-PAI do uvicorn (--workers) antes de o supervisor nascer (main:app só é importado nos
# filhos). Desliga o ping/pong de 5s que fazia o pai dar SIGKILL em worker vivo e ocupado
# (extração grande de .ufdr segura a GIL, o "pong" atrasa, o pai mata o worker — ver o arquivo).
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY funcoes ./funcoes
COPY rotas ./rotas
COPY tools ./tools

RUN useradd --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/.cache \
    && chown -R appuser:appuser /app/.cache
USER appuser

ENV PORT=3000
EXPOSE 3000

# shell form com `exec` (para o uvicorn continuar sendo PID 1 e receber o SIGTERM do compose)
# porque --workers precisa vir de variável de ambiente. API_WORKERS chega pelo bloco
# `environment:` do serviço api no docker-compose.yml, que o interpola do .env; o default 1 mantém
# o comportamento de sempre para quem não configurou nada. Ver funcoes/desempenho_utils.py, que
# divide threads, pool e caches por este número.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 3000 --workers ${API_WORKERS:-1} --log-config logging.json"]
