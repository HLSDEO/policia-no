FROM python:3.12-slim

WORKDIR /app

# psql é usado para rodar os scripts de import (suportam meta-comandos como \set/\if).
# tesseract-ocr (+ o pacote de português) é o motor do OCR das imagens do aparelho — roda local,
# em CPU: nenhuma imagem de inquérito sai do servidor. O pytesseract é só o wrapper Python dele.
# default-jre-headless roda o IpedDumper (extração Nuvem IPED, funcoes/iped_utils.py) — só
# precisa executar bytecode Java 11 (compilado com --release 11, ver api/tools/iped_dumper/),
# nunca compilar, por isso JRE e não JDK. O classpath em runtime usa os jars do PRÓPRIO caso
# sendo importado (lib/*.jar), não algo instalado aqui — ver docs/extracao_java_iped.md.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        postgresql-client \
        tesseract-ocr \
        tesseract-ocr-por \
        default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch e transformers ocupam bastante, mas são necessários para o CLIP (classificação de imagens)
RUN pip install --no-cache-dir torch==2.5.1 --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg extrai o quadro que vira miniatura dos vídeos sem thumb do IPED na extração temporária
# da galeria (funcoes/midia_temp.py) — só -frames:v 1, nunca reencode completo. Camada PRÓPRIA,
# DEPOIS do pip: acrescentá-lo ao apt de cima invalidaria o cache das duas camadas de pip
# (centenas de MB de torch re-baixados em todo rebuild).
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY main.py .
COPY logging.json .
# sitecustomize.py é importado pelo Python em todo interpretador — é o ÚNICO ponto que roda no
# processo-PAI do uvicorn (--workers) antes de o supervisor nascer (main:app só é importado nos
# filhos). Desliga o ping/pong de 5s que fazia o pai dar SIGKILL em worker vivo e ocupado
# (extração grande de .ufdr segura a GIL, o "pong" atrasa, o pai mata o worker — ver o arquivo).
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY funcoes ./funcoes
COPY rotas ./rotas
COPY tools ./tools

# cache dos modelos do faster-whisper (baixados do Hugging Face na primeira transcrição);
# fica num volume nomeado (docker-compose.yml) pra não baixar de novo a cada rebuild
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p /app/.cache/huggingface

RUN useradd --no-create-home --shell /usr/sbin/nologin appuser \
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
