# Infraestrutura, configuração e API

Este documento reúne a **plataforma** (Docker Compose, nginx, volumes e `.env`) e a **arquitetura da API** (FastAPI, routers, funções e logging JSON) — mesmo formato dos demais documentos (`docs/`).

| Seção | Cobre |
|---|---|
| [Docker Compose, nginx e configuração](#docker-compose-nginx-e-configuração) | serviços, rede, volumes, nginx, `.env` |
| [API — arquitetura FastAPI](#api--arquitetura-fastapi) | `api/main.py`, routers, `api/funcoes/`, logging JSON |
| [Configuração](#configuração) | tabela completa do `.env` |

## Docker Compose, nginx e configuração

### Visão geral

A plataforma roda inteira em Docker via `docker-compose.yml` na raiz do repositório: oito
serviços (`registry`, `postgres`, `ollama`, `pgbouncer`, `backups`, `api-1`, `api-2`, `web`)
sobem com um único `docker compose up -d`. A API tem **duas réplicas idênticas** (`api-1` e
`api-2`) atrás do nginx, que faz balanceamento e failover entre elas — ver "Réplicas de API"
abaixo. Os containers se chamam
`${NOME_PROJETO:-analyser}-<serviço>` — com o `.env` padrão, `analyser-postgres`,
`analyser-api-1`, `analyser-api-2`, `analyser-web` etc. (ver "Nome do projeto (`NOME_PROJETO`)"
abaixo). Só `web`
(`WEB_PORT`/`WEB_SSL_PORT`) e `registry` (`REGISTRY_PORT`, usado apenas para distribution das
imagens dos nós) publicam porta no host; API, banco, pooler e Ollama ficam na rede interna do
compose e são alcançáveis apenas entre os containers. O volume de dados de investigação (`data/`)
e as bases de referência (`db/data/`) ficam fora do Git, montados por bind mount de
leitura/escrita dentro da API; os dumps do sidecar `backups` caem em `./backups/` (fora do Git,
montado **somente leitura** na API — ver [backup.md](backup.md)).

### Mapa do sistema

Quatro mapas pequenos — o caminho de um pedido, o disco/backup, o atualizador e os nós — cada
linha puxa para a seção que detalha (nós: `no_descentralizado.md`; atualização:
`atualizacao.md`; backup: [backup.md](backup.md)).

**1. O caminho de um pedido** (o que acontece quando o analista clica em algo):

```
  Navegador do analista
        │  HTTP — única porta do host para o usuário (WEB_PORT)
        ▼
  web (nginx)   serve web/ (bind :ro), CSP, uploads sem teto (ufdr/zip)
        │  proxy_pass /api/* — round-robin + failover
        ▼
  api-1  ·  api-2 (FastAPI :3000 — réplicas idênticas, sem porta)
        │  rotas finas em api/rotas/ → toda lógica em api/funcoes/
        │
        ├─ pool de REQUISIÇÃO ──► pgbouncer :6432 ──► postgres :5432
        │     opt-in via .env;        session mode,         (pgvector/pg16,
        │     vazio = DIRETO            só rede interna)     volume próprio)
        ├─ pool LONGO (ingestão, migrações) ────────────► postgres
        ├─ conexões da LIDERANÇA (dedicadas) ───────────► postgres
        └─ embeddings ──► ollama :11434 (EMBED_MODE=local)
```

Os binds da API explicam como ela se configura sem rebuild: `.env` **rw** (as telas
Configurações/Desempenho gravam nele), `VERSION` + `db/migracoes` (versão e deltas de schema),
`docs` (tela Documentação), `logs/erros.log`, `data/` rw (as provas), `backups` **ro** e
`.deploy/controle` (a ponte com o atualizador do mapa 3).

**2. O que roda em segundo plano e o backup do banco:**

```
  laços de fundo da api:
    AGENDADOR e WATCHDOG rodam no ÚNICO líder (pg_try_advisory_lock
    de sessão, leader_utils.py); o resto já se exclusiviza pelo banco
    (xact lock + SKIP LOCKED, vaga contada em tabela, PK idempotente)

  backups (sidecar) ── pg_dump -Fc ──► ./backups/{last,daily,weekly,...}
     na 1ª subida e depois               (fora do Git; bind :ro na api)
     no BACKUP_AGENDAMENTO (@daily)
                     │
  agendador ── verificação diária ──◄┘   "existe? > 0 bytes? < 26 h?"
     └──► resultado no ledger verificacao_backup

  cron do host (semanal): backup-teste-restore.sh → Postgres descartável
  cron do host (diário):  backup-offsite.sh (restic de backups/ + data/)
                          → 2º HOST — backup de verdade só sai do host
```

**3. O atualizador (systemd no host — o Docker nunca vê o token do GitHub):**

```
  api ── escreve pedido.json / lê disponivel.json ──► .deploy/controle (bind)

  agente scripts/analyser-update (systemd, root) — o ÚNICO que lê o
  .deploy/token 0600; a API nunca vê o token
    ├─ check ── tags/releases do GitHub (HLSDEO/analyser)
    └─ apply ── snapshot p/ rollback → tarball da tag → up -d --build
                └─ build + push da IMAGEM DO NÓ ──► registry :5000
                    (volume ./registry) ── é daqui que o nó faz pull
```

**4. Entrada de material e os nós descentralizados:**

```
  Estação Windows ──SMB──► share [Dados] ──► data/<DELEGACIA>/...
     instalar-samba.sh cria o share; sincronizar-samba.timer mantém um
     symlink POR USUÁRIO (samba-analyser/<login> → data/<DELEGACIA>);
     quem não tem vínculo com delegacia fica sem acesso
  Tela Nova importação (upload) e cópia de rede SMB (job "Cópias") → data/

  NÓ na delegacia — o MESMO código (docker-compose.no.yml, PAPEL=no,
  sem banco): extrai .ufdr/RIF/SIMBA/IPED na CPU DA ESTACAO e manda só
  o resultado — lotes JSONL idempotentes + heartbeat:
      POST /api/no/* (Bearer token) ────────────────► api do central
  Atualização: gatilho "Atualizar" na tela → o nó faz PULL da imagem
  publicada no registry do central (mapa 3) — sem Docker Hub na estação
```

Em uma linha cada: o pedido entra **só** pelo nginx; a escrita vai por **dois pools** (o de
requisição pode passar pelo PgBouncer, o longo/dedicado nunca); os laços de fundo têm **líder
único** por advisory lock; quem faz update é o **agente do host** (a API só pede); o dump só é
backup depois do **restic no 2º host**; e o nó manda **extraído**, não arquivo bruto.

### Fluxo de uso

1. Não existe `.env.example`: o próprio `.env` já vem no repositório, comentado, com valores de
   exemplo prontos para editar (usuário/senha do banco, portas, credenciais de IA) — ver a nota
   de segurança em "Dados fora do Git" abaixo antes de decidir se a edição pode ser comitada.
2. `docker compose up -d` — na primeira subida, o Postgres executa os scripts de schema de `db/init/` (`01_`…`49_`, aplicados em ordem numérica pelo runner de `db/bootstrap/` — ver `banco_dados.md`) e a API baixa o cache dos modelos do faster-whisper no primeiro uso. O sidecar `backups` faz um dump na primeira subida (`BACKUP_ON_START`) e depois segue o cron (`BACKUP_AGENDAMENTO`, padrão `@daily`).
3. Acesse `http://localhost:${WEB_PORT}` — o login inicial é `admin` / `admin1234`.
4. Para `psql` (aplicar migração manual, inspecionar dados), entre no próprio container:
   `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"`.
5. Mudança em código da API (`api/rotas/`, `api/funcoes/`) exige rebuild:
   `docker compose up -d --build api-1 api-2` — o container **não tem bind mount do código**.
6. Mudança em `web/` (HTML/CSS/JS) é **live**: o volume é bind mount somente leitura
   (`./web:/usr/share/nginx/html:ro`) e o nginx manda `Cache-Control: no-cache` para
   `*.js|css|html` — basta recarregar a página, sem rebuild nem restart de container.
7. **Mudança de schema entra em dois arquivos** (ver `atualizacao.md` e `banco_dados.md`): o
   `db/init/NN_dominio.sql` declarativo, que só roda **num Postgres novo, com o volume
   `postgres_data` vazio** (o Postgres oficial trata `docker-entrypoint-initdb.d` como
   inicialização de primeira subida, não como migração incremental), **e** o delta
   `db/migracoes/NNNN_descricao.sql`, que o runner de `api/funcoes/migrar_utils.py` aplica
   **sozinho no startup da API** nos bancos que já existem — se um delta pendente falha, a API
   recusa subir com a causa no log. Era justamente o "esquecer a migração manual depois do `git
   pull`" que fazia a API subir sem a coluna nova e quebrar em runtime; o runner acabou com isso.
   Esquecer um dos dois arquivos, sim, ainda faz a instalação nova divergir da atualizada.
8. Quem usa apenas embeddings por API externa pode parar o Ollama (`docker compose stop ollama`);
   as demais funcionalidades não dependem dele.

Resumindo o que cada mudança de código exige:

| Mudou | Precisa de |
|---|---|
| `api/rotas/`, `api/funcoes/`, `requirements.txt`, `Dockerfile` | rebuild (`--build api`) |
| `web/*.html`, `web/js/`, `web/css/` | nada — recarregar a página |
| `db/migracoes/NNNN_novo.sql` (banco já existente) | nada — o runner aplica no startup da API (item 7) |
| `db/init/NN_novo.sql` (banco novo/volume vazio) | nada — roda sozinho no próximo `up` |
| `nginx/default.conf` | `docker compose up -d --force-recreate web` (sem rebuild de imagem). **`up -d` sozinho NÃO basta**: o conf entra como bind-mount de **arquivo único**, e um `rsync`/edição que troca o arquivo troca o inode — o container fica preso no inode antigo, e o `up -d` não recria o `web` porque nada no compose mudou. Visto na prática no apply da 4.5.10: nginx rodando com a CSP velha depois do apply; só com `--force-recreate` o arquivo novo entrou (confirmar de dentro: `docker exec <web> grep <diretiva nova> /etc/nginx/conf.d/default.conf`) |
| `.env` | `docker compose up -d` para variáveis do compose (`NOME_PROJETO`, `WEB_PORT`, `POSTGRES_*`, `PGBOUNCE_*`, `BACKUP_*`); variáveis lidas em runtime pela API (`AI_*`, `EMBED_*`, `OCR_*`...) pegam sozinhas na próxima chamada, sem restart — ver `env_utils.py` abaixo. Exceção notável: `PGBOUNCE_HOST` é lido ao abrir o pool — mexeu nele, reinicie a API |
| `pgbouncer/pgbouncer.ini.tpl` ou `Dockerfile.pgbouncer` | `docker compose up -d --build pgbouncer` (o ini é gerado do template na subida do container) |
| `NOME_PROJETO` (caso especial) | `docker compose up -d` **recria todos os containers** com o nome novo (o Compose acha os antigos pelos rótulos de projeto/serviço, não pelo nome, e os substitui). Volumes e rede não são tocados — eles seguem o nome do *projeto* do Compose (a pasta), não o `NOME_PROJETO`, então nenhum dado se perde |

### Como funciona

#### Serviços

| Serviço | Container | Imagem | Porta no host | Papel |
|---|---|---|---|---|
| `registry` | `${NOME_PROJETO}-registry` | `registry:2` | `${REGISTRY_PORT:-5000}` | registry local de imagens para os **nós**: o central builda e faz push da imagem do nó no `analyser-update apply`; o nó faz pull (a estação normalmente não tem saída para o Docker Hub) — ver `atualizacao.md`. `REGISTRO_NO` ausente no `.env` = ninguém usa |
| `postgres` | `${NOME_PROJETO}-postgres` | `pgvector/pgvector:pg16` (Postgres 16 + extensão `vector`, usada pela busca semântica) | nenhuma (interna 5432) | banco; roda `db/init/*.sql` na primeira subida com o volume vazio |
| `ollama` | `${NOME_PROJETO}-ollama` | build de `Dockerfile.ollama` (base `ollama/ollama:0.31.0`) | nenhuma | embeddings locais (`EMBED_MODE=local`); a API o alcança em `http://ollama:11434` |
| `pgbouncer` | `${NOME_PROJETO}-pgbouncer` | build de `Dockerfile.pgbouncer` (Alpine + pgbouncer) | nenhuma (interna 6432) | pooler de conexões na frente do Postgres (session mode); a API o usa quando `PGBOUNCE_HOST=pgbouncer` no `.env` — ver "PgBouncer" abaixo |
| `backups` | `${NOME_PROJETO}-backups` | `prodrigestivill/postgres-backup-local:16` | nenhuma | dump lógico diário (`pg_dump -Fc`) em `./backups/` com retenção 14/8/12; saúde conferida pelo agendador da API — ver [backup.md](backup.md) |
| `api-1` / `api-2` | `${NOME_PROJETO}-api-1` / `-api-2` | build de `./api` (`api/Dockerfile`) — **uma imagem, dois containers** | nenhuma (interna 3000) | FastAPI, **duas réplicas idênticas**; sem porta publicada, só alcançadas pelo nginx (round-robin + failover) — ver "Réplicas de API" |
| `web` | `${NOME_PROJETO}-web` | `nginx:alpine` | `${WEB_PORT}` → 80 | serve o `web/` estático e faz proxy de `/api/*` para a API |

A coluna "Container" é o `container_name` do compose: `${NOME_PROJETO:-analyser}-<serviço>`, ou
seja `analyser-postgres`, `analyser-api-1`… quando `NOME_PROJETO` não está definido. **O nome do
*serviço* (1ª coluna) não muda** — é por ele que os containers se resolvem na rede interna
(`http://api-1:3000`/`http://api-2:3000` no upstream do nginx, `http://ollama:11434`, `postgres`
como host do banco) e é ele que vai nos comandos do dia a dia (`docker compose exec postgres …`,
`docker compose exec -T api-1 python /app/tools/migrar.py`, `docker compose up -d --build api-1
api-2`).

Não há bloco `networks` no compose: os serviços compartilham a rede padrão criada pelo Compose e se resolvem pelo nome do serviço (`postgres`, `api-1`, `api-2`, `web`, `ollama`, `pgbouncer`, `backups`, `registry`). O `depends_on` garante a ordem: cada réplica espera Postgres *healthy*, PgBouncer *healthy* e Ollama *started*; o nginx espera **as duas** APIs *healthy*. `registry`, `pgbouncer` e `backups` sobem em qualquer instalação mas ficam ociosos até algo usá-los (respectivamente: `REGISTRO_NO`, `PGBOUNCE_HOST` no `.env`, e o cron do sidecar).

#### Nome do projeto (`NOME_PROJETO`)

`container_name` é global no daemon Docker: dois containers com o mesmo nome não coexistem na
máquina. Com o nome fixo (`analyser-api`), subir **uma segunda instância** — homologação ao lado
de produção, ou a instância de outra delegacia no mesmo servidor — falhava no conflito de nome.
Por isso todos os containers usam `${NOME_PROJETO:-analyser}-<serviço>`:

```
NOME_PROJETO=analyser-hml   →  analyser-hml-postgres, analyser-hml-api-1, analyser-hml-api-2,
                               analyser-hml-web, analyser-hml-ollama, analyser-hml-pgbouncer,
                               analyser-hml-backups, analyser-hml-registry
(variável ausente/vazia)    →  analyser-postgres, analyser-api-1, analyser-api-2, analyser-web, …
```

O `:-analyser` é o *default* do Compose: instalação antiga que não tenha a variável no `.env`
continua com os nomes de sempre, sem nenhuma mudança.

O que **não** muda junto:

- **Nome do projeto do Compose** — continua vindo da pasta do checkout (ou de
  `COMPOSE_PROJECT_NAME`), e é ele que prefixa a rede e os volumes nomeados
  (`postgres_data`, `whisper_cache`, `ollama_models`). Duas instâncias só ficam de fato isoladas
  se estiverem em **pastas diferentes**: trocar só o `NOME_PROJETO` dentro da mesma pasta renomeia
  os containers, mas os dois `up` disputariam o mesmo volume de banco.
- **Nome dos serviços** — `postgres`, `api`, `web`, `ollama`, `pgbouncer`, `backups`, `registry` na
  rede interna e nos comandos `docker compose …` (que agem por serviço, não por container).
- **Imagem do Ollama** — o `image: analyser-ollama-fixed` do `docker-compose.yml` é fixo; as
  instâncias compartilham a mesma imagem construída, o que é desejável (mesmo `Dockerfile.ollama`).
- **Nomes de aplicação** — cookie de sessão (`analyser_sessao`), chaves de `localStorage`
  (`analyser-theme`, `analyser-config-aba`) e o `User-Agent` da geocodificação não leem essa
  variável.

Ao rodar duas instâncias na mesma máquina, `WEB_PORT` também tem que ser diferente em cada `.env`
— é a única porta publicada no host.

#### Postgres

- Sem porta publicada de propósito — o banco guarda dado de inquérito; se um cliente externo for mesmo necessário, publique só no loopback (`127.0.0.1:5432:5432`), nunca em `0.0.0.0`.
- Volumes: `postgres_data` (dados, nomeado) em `/var/lib/postgresql/data`, `./db/init` (migrações, somente leitura) e `./db` (scripts de import e referência) em `/db:ro`.
- Healthcheck: `pg_isready` a cada 5 s.

#### API (FastAPI)

- Build (`api/Dockerfile`): `python:3.12-slim`; instala `postgresql-client` (os scripts de import  usam meta-comandos como `\set`/`\if`), `tesseract-ocr` + `tesseract-ocr-por` (OCR local), `torch` em CPU (CLIP/classificação de imagens) e o `requirements.txt`; copia `main.py`, `logging.json` e as pastas `funcoes/` e `rotas/`.
- `ENV HF_HOME=/app/.cache/huggingface` — cache dos modelos do faster-whisper num volume nomeado (`whisper_cache`), para não baixar de novo a cada rebuild.
- Roda como `appuser` (usuário sem login) e inicia com `uvicorn main:app --host 0.0.0.0 --port 3000 --log-config logging.json`.
- Volumes: `./.env` em `/app/.env` (leitura **e escrita** — sem `:ro` no compose — porque a tela
  Configurações grava ali via `env_utils.write_env_text`), `./db/import` em `/app/db/import`
  (somente leitura), `./data` em `/app/data:rw` (evidências), `./VERSION` + `./db/migracoes` +
  `./.deploy` em bind (versão instalada, deltas de schema e a ponte com o atualizador do host —
  ver `atualizacao.md`), `./backups` em `/app/backups:ro` (só para a verificação diária dos
  dumps), `./logs` em `/app/logs:rw` e `whisper_cache` (modelo faster-whisper). **O código não é
  montado por bind mount** — rebuild é a única forma de atualizar rotas/funções.
- Healthcheck: `python -c urllib ... http://localhost:3000/api/health` (rota pública, ver `api/rotas/sistema.py`).
- **Laços de fundo com líder único** (`api/funcoes/leader_utils.py`): o agendador (resumo noturno, verificação de backup, limpeza) e o watchdog de análises usam eleição por `pg_try_advisory_lock` de SESSÃO em conexão dedicada — com qualquer número de réplicas/instâncias da API, cada laço roda uma única vez por sistema; a líder morrendo (queda do container ou da conexão), outra assume em ≤ 30 s com novo tique. O despachante de extrações, os jobs pesados e as entregas de nó não precisam disso — já são seguros por banco (`pg_advisory_xact_lock` + `SKIP LOCKED`, vaga contada na tabela, PK idempotente).

#### Web (nginx)

- Serve `./web` (somente leitura) em `/usr/share/nginx/html` e usa `nginx/default.conf`:
  - `location /api/` → `proxy_pass http://analyser_api/api/` com `Host` e `X-Real-IP`; o upstream `analyser_api` é o round-robin com failover entre `api-1`/`api-2` (ver "Réplicas de API");
  - `location ~* \.(js|css|html)$` com `Cache-Control: no-cache` — o front não tem build nem hash no nome dos arquivos, então sem revalidação o navegador guardaria cópia antiga do JS/CSS;
  - cabeçalhos de segurança (`X-Content-Type-Options`, `X-Frame-Options DENY`,  `Referrer-Policy`, CSP restrita a `'self'`) e gzip para texto/JS/JSON. **Atenção à herança do `add_header`**:
    um `add_header` em qualquer `location` ANULA todos os declarados no nível `server`. Como o
    location do `no-cache` tem `add_header` próprio, ele precisa **repetir as quatro diretivas de
    segurança** dentro de si — sem isso, exatamente as páginas/assets entregues ao usuário
    (html/js/css) ficam nuas enquanto `/` e `/api/` seguem protegidas, e o pentest (achado IV.3,
    "páginas sem proteções adicionais do navegador") aponta a diferença. `scripts/testar-headers-seguranca.ps1`
    sonda as páginas, os assets e a API e falha se faltar qualquer um dos quatro cabeçalhos em
    qualquer alvo — com as linhas removidas do location, os alvos `.html`/`.js`/`/` falham e só a
    API passa, exatamente a assimetria do achado.
  - A CSP exige `script-src 'self'`, então **não há `<script>` inline em nenhum `.html`**: o
    script anti-piscada de tema (e o despacho de tema do `inquerito.html`) viviam inline no
    `<head>` das 73 telas e foram extraídos para `js/utils/boot_tema.js` e
    `js/utils/boot_inquerito.js`, carregados com `<script src>` no mesmo lugar — um externo
    síncrono no `<head>` roda antes do `<body>` do mesmo jeito. Estilos inline continuam
    permitidos (`style-src 'unsafe-inline'`). O teste dos scripts também recusa `<script>` inline
    de volta num `.html`: com a CSP ligada ele seria bloqueado no navegador e o anti-piscada
    morreria;
  - A CSP também declara **`worker-src 'self' blob:`**: é o que autoriza o parser do PDF do
    visualizador de documento (pdf.js vendorizado em `web/vendor/pdfjs/`) a rodar num `Worker` da
    mesma origem. Nada externo entra na CSP — nem o PDF, que o pdf.js lê do `/original` pela mesma
    rota autenticada do botão "Original"; ver [visualizador_documento.md](visualizador_documento.md).
  - **Não revela nome/versão de software** (achado de pentest: "divulgação de versão em cabeçalhos
    e páginas de erro"). Três peças, todas no `nginx/default.conf`:
    `server_tokens off` enxuga o header de resposta para `Server: nginx` — sem a linha, cada
    resposta sai `Server: nginx/x.y.z`, e a imagem oficial `nginx:alpine` não traz o módulo
    headers-more, então apagar o header inteiro exigiria recompilar (esconder a versão é o que o
    achado pede); os `location` nomeados `@erro_404/@erro_405/@erro_500/@erro_502/@erro_503/@erro_504`
    (mais o `@erro_413` que já existia) trocam as páginas de erro padrão do nginx — HTML assinado
    com `nginx/x.y.z` no rodapé — por corpos JSON no formato `{"detail": ...}` que o front já lê,
    preservando o status real de cada um; e os três locations de proxy (`/api/`, `ufdr-upload`,
    `upload-zip`) declaram `proxy_hide_header Server`/`X-Powered-By` — defesa em profundidade: o
    uvicorn responde `Server: uvicorn`, e embora o nginx normalmente já descarte o `Server` do
    upstream, nada disso passa pelo proxy se o comportamento mudar. A API entra na mesma
    cartada (`openapi_url=None` em `api/main.py`, ver "Detalhes de `main.py`" na seção da API).
  - `client_max_body_size 520m` e uma página de erro **413 em JSON**. O default do nginx é 1 MB,
    e era ele que quebrava a comparação facial: a rota aceita 10 MB
    (`comparar_rosto.py:MAX_TAMANHO_UPLOAD`), mas foto de celular passa de 1 MB e o envio
    morria no proxy. O limite fica **acima** do maior da aplicação de propósito (upload de
    documento, até 500 MB via `UPLOAD_DOCUMENTO_MB` em Desempenho), para quem recusar ser a
    rota, com a mensagem dela, em vez de um 413 opaco. Subir muito este valor exige antes trocar
    o `arquivo.file.read()` de `investigacao.py` por escrita em blocos: aquela rota carrega o
    upload inteiro na memória do processo.
  - **Exceções ao teto**: o `location = /api/investigacao/ufdr-upload` (envio de `.ufdr` pelo
    navegador, aba "Indexação Cellebrite" da tela Nova importação) e o
    `location = /api/documentos/upload-zip` (envio de `.zip` de prova, aba "Arquivo genérico")
    declaram `client_max_body_size 0` (sem teto), `proxy_request_buffering off` (sem bufferizar
    os GB em disco antes de repassar à API) e timeouts longos (`client_body_timeout`/
    `proxy_send_timeout`/`proxy_read_timeout` de 3600 s, para links lentos entre blocos).
    A API grava em blocos (`salvar_ufdr_enviado` em `funcoes/investigacao_utils.py`,
    `_salvar_documento_zip` em `funcoes/documentos_utils.py`), nunca na memória. **Custo para a
    infraestrutura**: o spool do UploadFile do uvicorn/starlette fica no `/tmp` do container da
    API — um `.ufdr` de 100 GB em upload exige ~100 GB livres em `/tmp` (além do `.parcial` em
    `data/`); vale conferir o espaço em disco antes de usar a rota em produção.
- `X-Real-IP` é o que permite ao login usar o IP real do cliente no limite de tentativas por IP (sem ele, toda requisição pareceria vir do container `web`).

#### Ollama

- `Dockerfile.ollama` desabilita a biblioteca `libggml-cpu-sapphirerapids` (patch de estabilidade) na imagem base `ollama/ollama:0.31.0`.
- Variáveis de ambiente: `OLLAMA_DEBUG=1`, `OLLAMA_NUM_PARALLEL` (default `1`), `OLLAMA_CONTEXT_LENGTH` (default `32000`) e `OLLAMA_KEEP_ALIVE` (default `30m` — mantém o modelo de embeddings carregado entre buscas, em vez de descarregar a cada 5 min ocioso e a pergunta seguinte pagar a recarga), lidas do `.env` pelo compose (`${VAR:-default}` — mudou, `docker compose up -d` recria); modelos ficam em `ollama_models:/root/.ollama`. Sem bloco `deploy.resources.reservations.devices` (NVIDIA, comentado no compose) o Ollama roda só em CPU/RAM — o log mostra `CPU model buffer`/`CPU KV buffer`.
- O modelo de embeddings é baixado sob demanda pela API na primeira indexação (`POST /api/config/ollama/pull`, `api/funcoes/ollama_pull.py`) e os textos nunca saem do servidor.

#### PgBouncer (pooler de conexões)

- Build (`Dockerfile.pgbouncer`): Alpine + pacote `pgbouncer` do próprio repositório (sem download de código externo); roda como usuário sem privilégio, com o socket e o ini gerado em `/tmp`.
- O `pgbouncer/pgbouncer.ini.tpl` é materializado no start por `pgbouncer/entrar.sh` (substitui os placeholders `__DB__/__USUARIO__/__SENHA__` pelo environment do compose — o awk é literal, a senha pode conter qualquer caractere sem escapamento).
- **Session mode**: cada conexão de cliente ganha uma conexão real ao banco e fica com ela — transparente para os `set_config` de sessão da API (`db_utils._aplicar_timeouts`) e para as transações longas da ingestão. O porquê de cada valor do ini está comentado no próprio template.
- Sem porta publicada: só a rede interna do compose alcança a 6432. A autenticação REAL ao Postgres acontece no pooler (linha `[databases]` com a senha verdadeira); entre os containers vale o mesmo modelo de confiança que já existia quando a API falava direto com o banco.
- **Ativação opt-in**: com `PGBOUNCE_HOST=pgbouncer` no `.env`, o pool de *requisição* da API passa a apontar para o pooler (`docker compose restart api-1 api-2` para aplicar, é lido ao abrir o pool). Com a chave ausente/vazia, comportamento antigo — direto no Postgres — e o serviço sobe ocioso. O pool de **conexões longas** (ingestão/migrações) e as conexões dedicadas de liderança vão SEMPRE direto ao banco, por fora do pooler — ver `banco_dados.md`.
- É a base da escala horizontal: N réplicas de API × `DB_POOL_MAX` cada deixam de estourar o `max_connections` do banco (o `default_pool_size` do ini é o teto de conexões reais pelo pooler; deixe folga sob `PG_MAX_CONNECTIONS` para o pool longo, o sidecar e o psql à mão). **Pré-requisito das duas réplicas**: com o pooler desligado, `2 × DB_POOL_MAX` + pools longos podem se aproximar de `PG_MAX_CONNECTIONS`.

#### Réplicas de API (`api-1` / `api-2`)

A API roda em **duas réplicas idênticas** atrás do nginx — a Etapa 4 da escala. Nenhuma delas tem estado de requisição: sessões vivem no banco (`usr_tb02_sessoes`), progresso e fila em tabelas (`job_tb01` + `pg_advisory_xact_lock` + `SKIP LOCKED`), e os laços de segundo plano (agendador, watchdog) se exclusivizam por advisory lock de sessão (`leader_utils.py`) — com duas réplicas subindo, **uma** ganha o lock e a outra só tenta de novo no próximo ciclo. Qualquer réplica atende qualquer pedido, então **não há afinidade/ip_hash**: o upload é um POST único e o progresso volta a ser lido de qualquer uma.

- **Como o compose declara**: a definição comum mora na âncora `x-api` (topo do YAML) e `api-1`/`api-2` a copiam com `<<: *api_def`, mudando só o `container_name` e o `API_REPLICA_ID`. São dois *serviços* declarados (não `--scale`) porque `container_name` fixo é incompatível com scale e o `up -d` do `analyser-update` resetaria a contagem; declarados, os nomes ficam determinísticos e o updater segue funcionando.
- **O nginx** (`upstream analyser_api`): round-robin com `max_fails=3 fail_timeout=15s` por réplica — uma morta sai de rotação por 15 s e a requisição em curso é reenviada à outra (nos casos de conexão recusada/timeout do `proxy_next_upstream` padrão; POST de upload não é reenviado, para não duplicar corpo). O nginx resolve o upstream **no boot**, então quando uma réplica é recriada com IP novo o `web` precisa **recarregar** para re-resolver. O `analyser-update` já faz `nginx -s reload` (quente, sem derrubar conexão) após o `up -d` em `apply`/`reiniciar`/`rollback`. ⚠️ Só fica de fora o restart **manual solto** de uma réplica (`docker restart analyser-api-2`) com a outra saudável: a health via nginx segue verde, o auto-cura não dispara, e a réplica reiniciada pode sair de rotação em silêncio — nesse caso rode `docker compose exec web nginx -s reload` em seguida.
- **`API_REPLICA_ID`** (`api-1`/`api-2`): muda só o nome do log de erros (`erros-api-1-<pid>.log`, para as duas não disputarem o `rename` da meia-noite no mesmo `erros.log`) e o rótulo `replica` no `/api/health`. O `/api/health` responde de qual réplica veio — as métricas de `pool` na tela Desempenho são **por processo**, então o número é o da réplica que respondeu àquela consulta.
- **Migrações e laços de manutenção** rodam via `docker compose exec -T api-1` (as migrações são idempotentes e têm trava própria; qualquer uma das duas serve). O `analyser-update`, o `analyser-resumo` e o `instalar-host.sh` já apontam para `api-1`.
- **Custo**: cada réplica soma pools, threadpool e caches próprios (os zips `.ufdr` abertos em RAM, por exemplo) — o uso de memória e CPU do host é ~2× o de uma única API. Com o PgBouncer ativo o teto de conexões reais ao banco **não** dobra (é o `default_pool_size`).
- **Aplicação de configuração (`.env` / tela Desempenho)**: o `.env` é um bind mount **compartilhado, somente um arquivo no host** — as duas réplicas leem o MESMO ao abrir pool/cache, e a escrita da tela é atômica + `flock` (duas não corrompem). Não há por isso "configurar uma e esquecer a outra": um `restart` faz as duas relerem. A tela Desempenho é um laço sobre os parâmetros e mostra a coluna "em vigor" lida do processo que respondeu ao `/api/health` (o campo `replica` diz qual) — como pool/threads/caches são POR PROCESSO, as duas têm números próprios, e ambos corretos para a sua réplica. O botão **Reiniciar servidor** reinicia as duas réplicas **uma de cada vez** (sem indisponibilidade) para reler o `.env` — ver `atualizacao.md`.

#### Backups (sidecar de dump)

- Imagem pronta `prodrigestivill/postgres-backup-local:16`: `pg_dump -Fc` do banco num cron interno (`BACKUP_AGENDAMENTO`, padrão `@daily`) + um dump na primeira subida (`BACKUP_ON_START`), com retenção própria: `BACKUP_DIAS` diários (14) / `BACKUP_SEMANAS` semanais (8) / `BACKUP_MESES` mensais (12), em `./backups/{daily,weekly,monthly,last}/` no host.
- A API monta `./backups` **somente leitura** e o agendador verifica todo dia se o dump existe, não é vazio e não passou de 26 h (`funcoes/backup_utils.py`; resultado no ledger `verificacao_backup`) — um backup que parou de rodar é descoberto no dia seguinte.
- O dump no mesmo host ainda não é backup: o runbook, o offsite via restic (`scripts/backup-offsite.sh`) e o teste de restauração semanal (`scripts/backup-teste-restore.sh`) estão em [backup.md](backup.md).

#### Dados fora do Git

- `data/` — arquivos `.ufdr` (Cellebrite), pastas-caso do IPED, RIFs, extratos SIMBA (R304) e documentos diversos. Montado em `/app/data` na API.
  A entrega é em `data/<DELEGACIA>/`; terminada a importação, o arquivo é movido para
  `data/<DELEGACIA>/<INQUÉRITO>/<CELULAR|NUVEM|RIF|BANCARIO|OUTROS>/` — ver
  [organizacao_arquivos.md](organizacao_arquivos.md).
- `db/data/` — bases de referência (Detran, Serasa, TSE, IBGE), lidas pelos scripts de `db/import/` disparados pela tela Ingestão (`POST /api/ingestao/importar/{grupo}`).
- Ambos estão listados no `.gitignore`, junto com `.env` e artefatos (`*.pyc`, `*.log`) — e, para
  `data/`/`db/data/`, isso de fato funciona: nenhum arquivo dessas pastas chegou a ser comitado.

**`.env` é uma exceção perigosa a essa regra**: apesar de estar no `.gitignore`, o arquivo foi
comitado *antes* da entrada existir no ignore — `git log -- .env` mostra histórico desde
`c85c31c` ("Banco base") — e `.gitignore` **não desfaz o rastreamento de um arquivo já
versionado**, só impede que arquivos *novos* iguais àquele padrão sejam adicionados por engano.
Resultado: `git ls-files | grep .env` ainda devolve `.env`, e cada `git commit` que tocar o
arquivo (inclusive uma troca de senha do Postgres ou de `AI_API_KEY` pela tela Configurações,
se alguém rodar `git add -A`/`git add .env` depois) publica o segredo no histórico do
repositório remoto. Duas formas de sair dessa armadilha:

1. `git rm --cached .env` (uma vez, com commit) — o arquivo continua no disco (o `.gitignore`
   passa a valer de verdade a partir daí), mas os segredos **já publicados permanecem no
   histórico** até uma reescrita (`git filter-repo`/BFG) + rotação de todas as credenciais que já
   passaram pelo arquivo (senha do Postgres, `AI_API_KEY`).
2. Enquanto isso não acontece, tratar o repositório remoto como **já comprometido** para essas
   credenciais — rotacionar a chave de IA e a senha do Postgres é mais barato que confiar que
   ninguém clonou o repo no meio tempo.

Isso é diferente de "documentar a configuração": as variáveis (`POSTGRES_PASSWORD`,
`AI_API_KEY`...) são citadas pelo **nome** neste documento, nunca pelo valor.

## Por que estas tecnologias

Este bloco explica as escolhas de stack à luz do que o sistema **faz de verdade**: parse forense de texto/XML/regex, muita consulta SQL, processamento de mídia local (OCR, transcrição, classificação) e uma interface para analistas — tudo implantado em **uma máquina por delegacia**.

### Python 3 + FastAPI

- **O ecossistema de IA e mídia é nativo**: `torch` + `transformers` (CLIP), `faster-whisper` (transcrição), `pytesseract` (OCR), `Pillow`/`pillow-heif` (HEIC/EXIF) e `numpy` (o cosseno da busca semântica é calculado em Python/numpy, ver `busca_semantica.md`) são bibliotecas Python de  primeira classe. Em Java, cada uma dessas peças viria por ponte (DJL/ONNX Runtime) com peso/formatos gerenciados à mão — o esforço de "cola" seria maior que o próprio sistema.
- **A carga de trabalho é I/O, não CPU**: parse de `report.xml` em streaming, regex de IPL/RIF/SIMBA, várias dezenas de consultas SQL por tela e chamadas HTTP aos provedores de IA. Python  resolve com menos código, e o GIL não é gargalo porque o trabalho pesado (whisper, torch, Tesseract) executa em C/C++ por baixo e roda em threads/jobs com limite (`JOBS_PESADOS_LIMITE`).
- **FastAPI especificamente**: validação por Pydantic dos schemas (`NovaConversaIn`,  `NovaMensagemIn`), *dependency injection* nativa — que é o que torna triviais as cadeias de autorização `exigir_login → exigir_aprovado → exigir_admin` aplicadas por router — e async para o polling/streaming, sem abrir mão de rodar jobs pesados em threads.
- **Uma só linguagem para tudo**: backend, loops de chamadas aos provedores, scripts de import (`db/import/`), leitura do `.env` e o front usa o mesmo `JSON {detail}` — sem fronteiras de stack dentro do projeto.

### PostgreSQL 16

- **Os recursos usados pelo sistema são específicos do Postgres**: `JSONB` (`TX_FERRAMENTAS`/`TX_DOCUMENTOS` da auditoria, `TX_OUTROS` dos dispositivos), índices de
  **trigrama** (`gin_trgm_ops`) para `ILIKE '%termo%'` em corpo de notas e OCR, índice **único parcial** que implementa a trava de conversa (`UX41_IA_TB03_CONVERSA_ATIVA`), `ON CONFLICT`  para idempotência (permissões, agregação da auditoria), `date_trunc` para janelas por hora,  `count(*) OVER()` para total com paginação.
- **Integridade da cadeia de custódia**: FKs, `UNIQUE` e CHECK constraints exigidas pelo domínio (chave PIX única por extração, grafia por e-mail único...) — e o esquema é versionado em migrações `db/init/01_..NN_` revisáveis, em vez de ORM gerando DDL escondido.
- **PostgreSQL como dono dos dados sensíveis**: banco sem porta publicada (ver acima), e as consultas são escritas à mão com parâmetros vinculados — o SQL é o contrato de acesso.

### Front HTML/CSS/JS puro + jQuery (sem build)

- **Implantação em delegacia sem ferramental**: um arquivo editado em `web/` aparece no próximo reload (o nginx manda `Cache-Control: no-cache`) — não há cadeia Node/npm, bundler nem build  step para o analista manter.
- **A complexidade é de dados, não de estado reativo**: telas de listagem/filtro/export sobre a API; jQuery 3.7.1 + `tom-select` + `leaflet` cobrem o necessário, e o tratado de cada página (menu, tema, guarda de sessão) é compartilhado por `sidebar.js`/`theme.js`/`auth.js` — um framework SPA adicionaria estado duplicado do lado do cliente sem ganho perceptível.

### nginx + Docker Compose (em vez de orquestrador maior)

- O alvo de implantação é **uma unidade por delegacia**: Compose com 4 containers, healthcheck e porta única publicada (`WEB_PORT`) é o tamanho certo — Kubernetes e afins serviriam para vários hosts, que o modelo de dados (subpastas `data/<delegacia>`) nem pede.
- O nginx não é só "servidor de arquivos": é o único ponto de saída para o banco (sem porta publicada), injeta `X-Real-IP` (base do limite de tentativas e da auditoria), aplica headers de segurança e serve `web/` com no-cache — uma camada de fronteira de graça entre o navegador e a API.

### IA e mídia locais (Ollama, whisper, CLIP, Tesseract)

- **Privacidade por construção**: embeddings (Ollama), transcrição (faster-whisper), classificação (CLIP) e OCR (Tesseract) rodam no servidor do inquérito — o texto de evidência só sai da máquina se o operador configurar um provedor de chat externo (`AI_PROVIDER`), e mesmo assim com a poda de contexto descrita em `chat_ia.md`. Geocodificação (Nominatim) envia apenas coordenadas.

### Por que não Java (ou Node/Go/.NET)

| Candidato | O que teria de custo aqui | Onde ganharia |
|---|---|---|
| **Java** | (1) **Ecossistema de ML**: whisper/CLIP/OCR/embeddings exigiriam ponte ONNX/DJL para cada modelo — o ponto mais caro; (2) **memória**: uma JVM com heap + metaspace ao lado de `torch` em CPU disputa um host modesto (o painel Servidor mede exatamente isso — ver `interface.md`); (3) **build/deploy**: Maven/Gradle, jar e versionamento de biblioteca para cada ajuste, enquanto hoje é `docker compose up -d --build api-1 api-2`; (4) **ritmo de iteração**: o esquema evoluiu em dezenas de migrações e a carga é I/O-bound (parse regex/XML + SQL + HTTP) — o JVM brilha em concorrência massiva e latência, que **não** é o gargalo real | sistemas *enterprise* multi-serviço com times Java, milhares de usuários concorrentes e exigência de interoperabilidade corporativa pesada — não é o perfil de uma ferramenta de análise pericial single-host |
| **Node/TypeScript** | async/bancada similar, mas ecossistema de ML local (whisper, CLIP, embeddings CPU) bem mais imaturo que o Python | backends web puros e tempo real com muitas conexões |
| **Go** | excelente para serviços concorrentes e binários pequenos, mas o custo de integrar whisper/CLIP/OCR/Python-first pesa igual ao Java | infraestrutura, alta concorrência, tooling CLI |
| **.NET** | mesmo entrave do ecossistema de IA/ML (ONNX) + menos exemplos forenses prontos | plataformas corporativas Windows-centradas |

Em resumo: a stack foi escolhida pelo **dominante do problema** — parse forense, SQL pesado e IA local —, e o Java, sendo ótimo em muitos domínios, é o mais distante justamente no que aqui mais importa (modelos de linguagem/visão em CPU e iteração rápida numa linha de comando de uma delegacia); a lógica da escolha não é "qual linguagem é melhor", e sim "qual entrega o mesmo resultado com menos custo de cola, memória e manutenção".

## API — arquitetura FastAPI

### Visão geral

A API fica em `api/` e é um FastAPI com **rotas finas**: `api/main.py` só cria a aplicação, registra os routers de `api/rotas/` e define os handlers globais de erro — toda consulta, SQL, parser, chamada de IA e regra de negócio mora em `api/funcoes/`. A API roda na porta 3000 dentro dos containers `api-1`/`api-2` (`${NOME_PROJETO:-analyser}-api-1/-2`, duas réplicas idênticas — ver "Réplicas de API") e só é alcançada pelo navegador através do proxy `/api/*` do nginx; nenhuma porta dela é publicada no host.

Há três níveis de proteção: rotas **públicas** (health e auth), rotas **protegidas** por `exigir_aprovado` (usuário com sessão e aprovado) e rotas **de admin** (`exigir_admin`); além do isolamento por inquérito (`exigir_acesso_inquerito`) que o backend confere em toda rota de dados - trocar o id na URL não dá acesso, o servidor responde `403`.

### Fluxo de uso

O front nunca chama a API diretamente na porta 3000: toda chamada vai para `http://host:${WEB_PORT}/api/...`, o nginx faz `proxy_pass` para `http://api:3000/api/...` e a
resposta volta como JSON. Em rotas de dados de inquérito, o parâmetro `?extracao=ID` restringe a consulta a um único aparelho e é sempre combinado com `id_inquerito`, preservando o isolamento.

### Como funciona

#### Registro dos routers (`api/main.py`)

```python
protegido = [Depends(exigir_aprovado)]
app.include_router(sistema.router)                 # público: GET /api/health
app.include_router(sistema.router_admin)           # admin: GET /api/sistema/metricas
app.include_router(auth.router)                    # público (login/cadastro)
app.include_router(usuarios.router)                # admin declarado no próprio router
app.include_router(delegacias.router)              # GET público; demais admin
app.include_router(delegacias.router_admin)        # POST/PUT/DELETE admin
app.include_router(prompts_entidade.router)        # admin no router
app.include_router(auditoria_ia.router)            # admin no router
app.include_router(auditoria_acesso.router)        # admin no router
app.include_router(configuracao_admin.router)      # admin no router
app.include_router(ingestao.router, dependencies=protegido)   # demais idem, investigacao, ia, referencia, entidades, busca_automatica, padroes, documentos, relatorios, classificacao_imagem, ocr_imagem, comparar_rosto
```

Detalhes de `main.py`:

- A aplicação nasce com `FastAPI(title="PolícIA api", docs_url=None, redoc_url=None, openapi_url=None)`:
  Swagger, ReDoc e `/api/openapi.json` **não existem**. Os dois primeiros já eram desligados de
  fábrica no projeto; o `openapi_url=None` fechou o último buraco — sem ele, o schema em JSON com
  o mapa inteiro da API (rotas, parâmetros, nome e versão da aplicação) ficava acessível **sem
  login** por `http://host:${WEB_PORT}/api/openapi.json`, um atalho de reconhecimento para quem
  procura falhas (achado de pentest sobre ocultar nome/versão — mesmo racional do
  `server_tokens off` do nginx).
- Middleware `ContextoRequisicao` (em `api/funcoes/auditoria_acesso.py`) publica o caminho da rota num `ContextVar`, para a auditoria de acesso saber qual rota foi chamada sem acrescentar  `request: Request` na assinatura de dezenas de rotas.
- `@app.on_event("startup")` abre o pool de conexões (`funcoes/db_utils.py:abrir_pool`); o shutdown o fecha.
- Handlers globais de erro: `404` → `{"detail": "Não encontrado."}` e qualquer exceção não tratada → `500 {"detail": "Erro interno do servidor."}` + log com stack trace. O corpo sempre respeita o formato `{"detail": ...}` que todo o front lê em `xhr.responseJSON.detail`.

#### Routers e o que cobrem

| Módulo (`api/rotas/`) | Cobre |
|---|---|
| `sistema` | `GET /api/health` (público, usado pelo healthcheck do compose) e `GET /api/sistema/metricas` (admin; CPU, memória e disco via psutil — ver o painel Servidor em `interface.md`). Não existe `/api/sistema/health`: o health check é a rota `/api/health` |
| `auth` | login/logout, usuário logado (`/api/auth/eu`), autocadastro (`/api/auth/cadastro`), troca da própria senha/e-mail, tema por usuário (`/api/auth/tema`), recuperação de senha (`/api/auth/esqueci`, `GET/POST /api/auth/redefinir`, `GET /api/auth/recuperacao`) — ver `autenticacao.md` |
| `usuarios` | administração: listar/criar usuários, ativar/desativar, aprovar, gerar link de redefinição de senha — tudo admin — ver `autenticacao.md` |
| `configuracao_admin` | `GET/POST /api/config/ai/prompt`, `/api/config/ai/embeddings`, `/api/config/ocr`, `/api/config/image-comparison` e `/api/config/ai/report` (configurações de IA por aba — provedor de chat, busca semântica, OCR, comparação de imagens e comentário IA no relatório — lidos/gravados no `.env`, chave sempre mascarada) e `GET/POST /api/config/ollama/pull` (download de modelo do Ollama em background com polling) — admin — ver `configuracoes_ia.md` |
| `configuracao_admin` (Desempenho) | `GET/POST /api/config/desempenho` — hardware detectado, valores recomendados, o que está gravado no `.env` e o que está **em vigor** (lido do processo e do próprio Postgres via `pg_settings`). `GET ?perfil=` devolve a prévia de outro perfil sem gravar — admin — ver "Desempenho" abaixo |
| `auditoria_ia` | `GET /api/admin/ia/auditoria` (listagem com totais) e `GET .../{id_auditoria}` (detalhe: prompt, contexto, ferramentas, custo) — admin — ver `auditoria.md` |
| `auditoria_acesso` | `GET /api/admin/acessos` — quem leu o quê, agregado por hora, e o que foi negado — admin — ver `auditoria.md` |
| `auditoria_usuarios` | auditoria de **gestão de usuários** (quem criou/aprovou/ativou/alterou quem) — perfil auditoria/admin — ver `auditoria.md` |
| `ingestao` | `GET /api/ingestao/status` (contagens por conjunto de dados) e `POST /api/ingestao/importar/{grupo}` — dispara os scripts de `db/import/` em background — ver `ingestao.md` |
| `investigacao` | o maior router: arquivos `.ufdr`, extrações (disparar/cancelar/restaurar/apagar), inquéritos (listar/criar/apagar/renumerar/contexto), conversas e mensagens (WhatsApp/SMS/mensageiros) com participantes e associação, contatos, ligações, localizações (+ geocodificação e grafo), notificações, notas, navegação, aplicativos, e-mails, contas financeiras, arquivos, Wi-Fi, chaves PIX, donos, timeline unificada, anexos (binário sob demanda do `.ufdr`, com cache — `api/funcoes/ufdr_cache.py`), documentos do inquérito, RIF, SIMBA, Nuvem IPED, inquérito em PDF, convergência, permissões e apagar extração/inquérito — ver `investigacao_celular.md`, `rif.md`, `simba.md`, `extracao_iped.md`, `documentos_diversos.md`, `convergencia.md`, `permissoes_delegacia.md` |
| `referencia` | busca fuzzy de pessoas (Serasa), municípios e UFs — autocomplete da associação de contatos |
| `entidades` | entidades e relações manuais, comentários, posição dos nós, revisão de sugestões, entidades canônicas + evidências + relações canônicas, grafo da Mesa e `identificar-pessoas` — ver `mesa_inquerito.md` |
| `busca_automatica` | `GET/POST /api/investigacao/inqueritos/{id}/busca-automatica` — busca de entidades por IA, modos `prompts` e `relacionar_manuais` — ver `busca_semantica.md` |
| `entidades_auto` | biblioteca de regex com validação sobre documentos genéricos (`/api/entidades-auto`, escrita admin) e a extração por inquérito (`.../extracao-auto` com histórico e detecções) — ver `entidades_auto.md` |
| `padroes` | biblioteca de padrões de crime (escrita admin, detecção por rota) e execução da detecção por inquérito — ver `padroes_crime.md` |
| `prompts_entidade` | CRUD dos prompts da busca automática, com escopo (mensagem/documento) e ativação — admin |
| `documentos` | Casos de documentos diversos (não-UFDR): arquivos, casos, grafo/KPIs por IA e chat de IA por caso (conversas, mensagens, processamentos com polling e cancelamento) — ver `documentos_diversos.md` |
| `ia` | chat de IA por inquérito: conversas, mensagens, processamentos em background (`IA_TB03`, polling e cancelamento), auditoria da conversa e download do prompt usado — ver `chat_ia.md` |
| `relatorios` | padrões de relatório e blocos (admin), catálogo de variáveis, marcações "marcar para o relatório" e geração do `.docx` (`POST .../relatorios/{id_padrao}/gerar`) — ver `relatorio_word.md` |
| `classificacao_imagem` | CRUD + ativar/desativar de categorias de imagem (`/api/categorias-imagem`) e classificação por CLIP (`/api/arquivos/classificar-imagens` com progresso/cancelar/reset) — ver `classificacao_imagem.md` |
| `comparar_rosto` | `POST .../inqueritos/{id}/comparar-rosto` (upload de imagem, máx. 10 MB, `extracao` opcional para varrer um aparelho só), prévia e origem de arquivo — progresso e cancelamento ficam na rota `jobs` — ver `comparar_rosto.md` |
| `ocr_imagem` | OCR em lote (`/api/arquivos/ocr-imagens`) com progresso/cancelar/reset, OCR de imagem específica, re-rodar por filtro (mime_types) e status por extração (`/api/arquivos/ocr-extracao/{id}/status`) — ver `ocr_imagem.md` |
| `jobs` | jobs de segundo plano (OCR, classificação, comparação facial, import de referência, pull do Ollama): `GET /api/investigacao/jobs/{id_job}` (progresso), `.../inqueritos/{id}/jobs[/{tp_job}]`, `POST .../jobs/{id_job}/cancelar` e `.../liberar` e `GET /api/admin/jobs` (admin) — ver `banco_dados.md` (`JOB_TB01_JOBS`) |
| `hipoteses` | workspace de hipóteses do inquérito: hipóteses com status, responsável, evidências a favor/contra e pendências, que alimentam o prompt de toda conversa de IA do inquérito — ver `hipoteses.md` |
| `marcadores` | marcadores nomeados (as *tags* do IPED): CRUD, aplicação individual e em lote, sumário e exportação — cada rota confere o dono além do acesso ao inquérito — ver `marcadores.md` |
| `comparar_celulares` | cruzamento entre os aparelhos de um inquérito (mesmo arquivo por hash, interlocutores, agenda e Wi-Fi em comum) — ver `comparar_celulares.md` |
| `correlacao_nuvem` | Nuvem × aparelho: `GET .../correlacao-nuvem/fontes`, `/resumo` e `/arquivos` — ver `correlacao_nuvem.md` |
| `rede` | cópia de pasta de rede (SMB) para `data/<DELEGACIA>/`: `/api/rede/listar`, `/copiar` (job em `JOB_TB01`, painel "Cópias") — ver `importacao.md` e `funcoes/rede_smb.py` |
| `api_pf` | `GET/PUT /api/config/api-pf` e `POST /api/config/api-pf/testar` — admin — ver `autenticacao.md` |
| `simba_alertas_admin` | pesos e limiares dos alertas do SIMBA (`/api/config/simba-alertas`) — admin — ver `simba.md` |
| `documentacao` | `GET /api/documentacao` e `GET /api/documentacao/{nome}` — os `.md` desta pasta lidos dentro do sistema — admin — ver `interface.md` |
| `delegacias` | multidelegacia: `GET /api/delegacias` público (usado no cadastro e edição de usuários); criar/atualizar/excluir exigem admin (routers `router` e `router_admin`) — ver `permissoes_delegacia.md` |
| `servidores` | Administração → Servidores: CRUD dos nós descentralizados; o token é gerado ali, mostrado uma única vez e nunca volta numa listagem — ver `no_descentralizado.md` |
| `no_central` | rotas `/api/no/*` no **central**, autenticadas pelo token do nó (Bearer): ping/heartbeat, entrega de lotes, recibos, jobs (claim/plano/lote/estado) — ver `no_descentralizado.md` |
| (nó) `/no/*` | as rotas que o central chama na estação (`/no/saude`, `/no/hash`, `/no/parse`, `/no/extrair`, `/no/transcrever`, `/no/embed`, leitura por faixas) vivem no repositório do nó — não existem neste |

Padrões transversais: jobs pesados (OCR, classificação CLIP, comparação facial) **rodam no nó da
delegacia** — o central só cria a fila (`ID_SERVIDOR`) e reproduz os resultados; sem nó ativo a
rota responde `409` com o motivo;
processamento de extração/import/IA roda em background com *polling* da tela.

#### Lógica em `api/funcoes/`

As funções de suporte estão distribuídas por módulo de assunto:

| Área | Módulos |
|---|---|
| Autenticação/segurança | `auth_utils.py` (sessões, bcrypt, tema, dependências `exigir_*`), `auditoria_acesso.py`, `hash_utils.py` |
| Banco/ambiente | `db_utils.py` (dois pools: requisição — opcionalmente via PgBouncer — e conexões longas sempre direto ao Postgres; `nova_conexao_dedicada()` para a liderança), `leader_utils.py` (eleição de líder por advisory lock de sessão para os laços de fundo), `backup_utils.py` (verificação diária dos dumps), `env_utils.py` (leitura/escrita do `.env`), `cert_utils.py` (certificado de CA próprio para gateway de IA com TLS autoassinado) |
| UFDR/Nuvem IPED | `ufdr_parser.py` (parse streaming do `report.xml`), `ufdr_zip.py`/`ufdr_nuvem.py`/`ufdr_icloud.py` (formatos por fonte), `ufdr_cache.py` (cache de binário em memória), `iped_utils.py`, `iped_cache.py`, `iped_wa_drive.py` + `whatsapp_parcial_utils.py` (mídia do WhatsApp sem texto), `takeout_parsers.py` (produtos do Google dentro do caso), `nuvem_metadados.py` |
| Investigação | `investigacao_utils.py` (SQL, acesso por inquérito, binário de arquivos), `identificacao_pessoas.py`, `pix_utils.py`, `entidades_utils.py`, `entidades_auto_utils.py` (regex com validação — ver `entidades_auto.md`), `canonicas_utils.py`, `busca_entidades_utils.py`, `padroes_utils.py`, `relatorios_utils.py`, `relatorios_docx.py`, `marcadores_utils.py`, `compartilhar_prova.py`, `comparar_celulares_utils.py`, `correlacao_nuvem_utils.py` |
| Importação/parse | `import_utils.py`, `rif_utils.py`, `simba_utils.py`, `ipl_utils.py`, `irpf_utils.py`, `ipei_utils.py`, `telefonia_utils.py`, `ufdr_parser.py`, `zip_import.py`, `upload_utils.py`, `organizacao_arquivos.py`, `rede_smb.py` |
| IA | `ia_provedores.py` (loop de chamadas OpenAI-compatível/Anthropic/Google), `ia_investigacao.py` (ferramentas do chat), `ia_historico.py`/`ia_eventos.py` (conversas e a trilha de raciocínio do SSE), `ia_auditoria.py`, `ia_utils.py` (prompt de sistema), `ia_avisos.py`/`ia_webhook.py` (avisos de conclusão), `ia_delegacia.py`/`config_ia_usuario.py` (IA por delegacia e do usuário), `rag_hibrido.py` (busca híbrida RRF), `embed_utils.py` (busca semântica), `ollama_pull.py`, `busca_automatica_utils.py`, `prompts_entidade_utils.py`, `resumo_noturno.py` + `agendador.py` (batch da madrugada) |
| Mídia local | `documentos_utils.py`; `transcricao.py`/`ocr_utils.py`/`classificacao_imagem_utils.py`/`comparar_rosto_utils.py` guardam só o **replay de resultados** do nó (os modelos — whisper, Tesseract, CLIP — rodam na estação; imports pesados são tardios) |
| Nós descentralizados | `no_utils.py` (registro, token, batimento, `SEM_NO_ATIVO_MSG`), `no_entrega.py` (recebimento da entrega no central), `no_ponte.py` (o que o central chama no nó), `canal_extracao.py` (replay dos registros), `jobs_no.py` (jobs no nó) — ver `no_descentralizado.md` |
| Diversos | `geocodificacao_utils.py` (Nominatim), `geoespacial_utils.py` (camadas SHP/GeoJSON), `email_utils.py` (SMTP de recuperação), `wifi_utils.py`, `contas_nuvem_utils.py`, `logging_config.py`, `migrar_utils.py` (runner de migrações), `versao_utils.py`, `desempenho_utils.py`, `anonimizacao.py` |

#### Logging estruturado (JSON)

Toda saída de log da API — aplicação (`funcoes.*`, `rotas.*`) e uvicorn (`uvicorn`, `uvicorn.access`, `uvicorn.error`) — sai em **JSON**, uma linha por evento, pronta para coletor de log:

- O formatter é `JsonFormatter` em `api/funcoes/logging_config.py`: emite `timestamp` (UTC, ISO), `level`, `logger`, `message`, `module`, `line` e `exception` quando há traceback. Campos
  passados via `extra={...}` num `logger.info(...)`/`logger.warning(...)` entram soltos no JSON (ex.: `id_inquerito`).
- A configuração fica em `api/logging.json` (root e loggers do uvicorn no nível INFO, handler de stream para stdout) e é carregada pelo uvicorn com `--log-config` no `CMD` do  `api/Dockerfile`. É obrigatório ser assim, e não um `logging.config.dictConfig` dentro de `main.py`: o uvicorn configura o próprio logging **antes** de importar `main:app` — feito depois, chegaria tarde para os loggers `uvicorn.*`.

## Configuração

Todas as variáveis ficam no `.env` (na raiz); a API lê o arquivo a cada chamada de configuração (`api/funcoes/env_utils.py`) e a tela **Configurações de IA** o edita via `configuracao_admin` (ver `configuracoes_ia.md`). A chave de IA nunca é devolvida ao front: só o valor mascarado (`mask_key`), e chave em branco no formulário preserva a salva.

### Leitura/escrita do `.env` (`api/funcoes/env_utils.py`)

Não há biblioteca de `.env` (`python-dotenv` etc.) — o parsing é feito à mão, de propósito, para
a escrita **preservar comentários e a ordem original das linhas** (o `.env` é comentado e serve
de documentação de si mesmo — reescrevê-lo do zero a cada save perderia isso):

- `ENV_FILE = Path(os.environ.get("ENV_FILE", "/app/.env"))` — caminho fixo dentro do container
  (o bind mount do compose aponta pro `.env` real do host); `read_env_text()` devolve string
  vazia se o arquivo ainda não existir, em vez de levantar.
- `parse_env(content)` — só para **leitura**: ignora comentários/linhas em branco, devolve
  `{CHAVE: valor}` da primeira ocorrência de cada chave.
- `upsert_env_values(content, updates)` — para **escrita**: percorre linha a linha, substitui só
  as linhas cuja chave está em `updates` (mantém comentários e as demais linhas intactas) e
  acrescenta no final, depois de uma linha em branco, as chaves de `updates` que não existiam
  ainda — nunca duplica uma chave.
- `sanitize(value)` — remove `\r`, `\n` e `\0` do valor antes de gravar; sem isso, um valor com
  quebra de linha (colado sem querer no formulário) viraria uma **linha `KEY=VALUE` nova e
  arbitrária** no `.env` — o vetor de injeção que o nome do campo sugere.
- `mask_key(key)` — mostra só os 4 primeiros e 4 últimos caracteres (`abcd••••••wxyz`); chave com
  8 caracteres ou menos vira só `•` repetido (não sobra nada identificável de uma chave curta).
- `write_env_text(content)` grava e tenta `os.chmod(ENV_FILE, 0o600)`; a exceção é engolida
  (`except OSError: pass`) porque um bind mount a partir de um host **Windows** não suporta
  permissão POSIX — a gravação já se efetivou antes da tentativa de `chmod`, então a falha aí é
  cosmética, não motivo para abortar o save.

| Grupo | Variáveis | Efeito |
|---|---|---|
| Projeto | `NOME_PROJETO` | prefixo do `container_name` de todos os serviços (`${NOME_PROJETO:-analyser}-postgres`, `-api`, `-web`, `-ollama`, `-pgbouncer`, `-backups`, `-registry`); permite mais de uma instância na mesma máquina. Só o compose lê — a API a ignora. Ver "Nome do projeto (`NOME_PROJETO`)" acima |
| Banco | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | credenciais do Postgres (usadas no compose e no healthcheck); `POSTGRES_PORT` existe no arquivo mas o compose não publica porta do banco |
| Pooler | `PGBOUNCE_HOST` (ex.: `pgbouncer`), `PGBOUNCE_PORT` (padrão 6432) | pool de *requisição* da API passa a apontar para o PgBouncer em vez do Postgres; ausente/vazio = direto ao banco (comportamento antigo). O pool de conexões longas nunca passa pelo pooler. Lido ao abrir o pool — mudou, reinicie a API. Ver "PgBouncer" acima |
| Backups | `BACKUP_AGENDAMENTO` (cron do sidecar, padrão `@daily`), `BACKUP_DIAS`/`BACKUP_SEMANAS`/`BACKUP_MESES` (retenção, 14/8/12) | entram no ambiente do container `backups` via compose; exigem `docker compose up -d`. Dumps em `./backups/`; verificação diária no agendador da API — ver [backup.md](backup.md) |
| Registry dos nós | `REGISTRO_NO` (ex.: `10.27.7.19:5000`), `REGISTRY_PORT` (padrão 5000), `REGISTRY_DIR` (padrão `./registry`) | registry local onde o central publica a imagem do nó no `apply`; `REGISTRO_NO` é lido pelo `analyser-update` no host, não pela API — ver `atualizacao.md` |
| Web | `WEB_PORT` | porta do host publicada pelo nginx (`${WEB_PORT}:80`) |
| IA | `AI_PROVIDER`, `AI_MODEL`, `AI_API_URL`, `AI_API_KEY` | provedor do chat de IA (OpenAI-compatível, Anthropic, Google etc.) |
| Transcrição | `TRANSCRICAO_THREADS`, `TRANSCRICAO_MODELO`, `TRANSCRICAO_VAD`, `TRANSCRICAO_BEAM` | paralelismo e modelo do faster-whisper (local, sem GPU); VAD ligado e decodificação gulosa (BEAM=1) por padrão — os dois maiores barateadores em CPU. `TRANSCRICAO_THREADS` ausente = dimensionado pelo hardware (ver Desempenho abaixo) |
| Embeddings | `EMBED_MODE` (`local`/`api`), `EMBED_MODEL` | busca semântica; `local` usa o Ollama; modelo em branco desabilita |
| Classificação | `CLIP_NUM_THREADS` | threads do PyTorch/OpenMP por job de CLIP ou comparação facial |
| Jobs pesados | `JOBS_PESADOS_LIMITE` | quantos jobs de OCR + classificação + comparação facial rodam juntos **no servidor** (default 2 no código se a variável faltar). O limite é contado na tabela de jobs, não num semáforo de processo, então vale igual com qualquer número de workers; um job além do limite fica em `aguardando_vaga` e a tela mostra a posição na fila |
| OCR | `OCR_IDIOMA` (padrão `por+eng`), `OCR_PSM` (padrão `3`) | idiomas e segmentação do Tesseract |
| Desempenho — base | `DESEMPENHO_PERFIL` (`auto`/`pequeno`/`medio`/`grande`) | hardware sobre o qual as fórmulas calculam. `auto` (padrão) usa o detectado; os demais fingem 4/8, 8/32 e 16/64 núcleos/GB |
| Desempenho — API | `API_THREADS_REQUISICAO`, `DB_POOL_MAX`, `DB_POOL_LONGA_MAX` | teto de requisições simultâneas e tamanho do pool de requisição, **por worker**; `DB_POOL_LONGA_MAX` é o teto do pool de conexões longas (ingestão/migrações, sempre direto ao Postgres — default 8). Valem ao **reiniciar a API** |
| Desempenho — processos | `API_WORKERS` | quantos processos do uvicorn sobem (default 1). **Vale só ao recriar o container** (`docker compose up -d`), porque entra no comando do uvicorn. Threads, pool e caches são divididos por ele, e `PG_MAX_CONNECTIONS` passa a contar `DB_POOL_MAX × API_WORKERS` — esquecer essa multiplicação é o erro mais fácil e o mais barulhento (`PoolTimeout` sem aviso) |
| Desempenho — fila | `EXTRACOES_SIMULTANEAS` | quantas extrações de `.ufdr` rodam juntas (default 2). As demais ficam em "Na fila", e a fila sobrevive a restart da API. Vale **já** |
| Timeouts do banco | `DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, `DB_IDLE_TX_TIMEOUT_MS` | tetos de sessão do caminho de requisição (60 s / 5 s / 60 s). As fases de ingestão usam um perfil próprio, folgado — ver `banco_dados.md`. Valem ao **reiniciar a API** |
| Desempenho — caches | `CACHE_UFDR_ZIPS`, `CACHE_CONTEUDO_MB` | tamanho dos caches de `.ufdr`/IPED. Valem ao **reiniciar a API**. (`CACHE_EMBED_INQUERITOS`/`CACHE_EMBED_MB` não existem mais: o cache de matrizes da busca semântica saiu com a migração 0048 — o ranking passou ao pgvector; valores residuais no `.env` são ignorados) |
| Desempenho — Postgres | `PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`, `PG_MAX_CONNECTIONS`, `PG_SHM_SIZE`, `PG_MAX_WAL_SIZE`, `PG_MAX_WORKER_PROCESSES`, `PG_MAX_PARALLEL_WORKERS`, `PG_MAX_PARALLEL_WORKERS_PER_GATHER`, `PG_RANDOM_PAGE_COST` | entram no `command:` do serviço `postgres`. Valem ao rodar `docker compose up -d`, que **recria o container do banco** |
| E-mail (opcional) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_SEGURANCA` (`starttls`/`ssl`/`nenhuma`), `SMTP_USUARIO`, `SMTP_SENHA`, `SMTP_REMETENTE` | usado só na recuperação de senha por e-mail; sem eles o administrador gera o link manualmente |
| URL pública | `APP_URL` | endereço público do sistema; sem ele o e-mail de recuperação sai com o código em vez do link — ver `autenticacao.md` |
| IA — chat avançado | `AI_TIMEOUT`, `AI_CONTEXT_LENGTH`, `AI_REASONING_EFFORT`, `AI_COTACAO_BRL`, `AI_CA_CERT` | timeout da chamada (s; 0 = padrão do provedor), janela de contexto, esforço de raciocínio (modelos "pensantes"), cotação USD→BRL usada pela auditoria para precificar custo, bundle de CA próprio (proxy/SSL-inspeção). Os cinco são editáveis na tela **Configurações de IA** — ver `configuracoes_ia.md` |
| IA — provedor por função | `DOC_AI_PROVIDER/MODEL/API_URL/API_KEY/CA_CERT/REASONING_EFFORT` (análise de documento), `REPORT_AI_…` (mesmas chaves, comentário IA no relatório Word) | ausentes = a função segue o `AI_*` geral; presentes = roda com modelo próprio — ver `configuracoes_ia.md`, `documentos_diversos.md`, `relatorio_word.md` |
| Embeddings além do básico | `EMBED_PROVIDER`, `EMBED_API_URL`, `EMBED_API_KEY`, `EMBED_CA_CERT`, `EMBED_LOCAL_URL` | com `EMBED_MODE=api`, endpoint/chave do provedor (caem no `AI_API_URL`/`AI_API_KEY`/`AI_CA_CERT` se vazios); `EMBED_LOCAL_URL` troca o endereço do Ollama (padrão `http://ollama:11434`) — ver `busca_semantica.md` |
| OCR por API | `OCR_MODE` (`local`/`api`), `OCR_PROVIDER`, `OCR_MODEL`, `OCR_API_URL`, `OCR_API_KEY` | `local` (padrão) é o Tesseract no container; `api` manda a imagem para um modelo de visão — ver `ocr_imagem.md` |
| Comparação facial por API | `IMAGE_CMP_MODE` (`local`/`api`), `IMAGE_CMP_PROVIDER`, `IMAGE_CMP_MODEL`, `IMAGE_CMP_API_URL`, `IMAGE_CMP_API_KEY` | idem: `local` usa CLIP/ArcFace no container; `api` delega ao provedor — ver `comparar_rosto.md` |
| Resumo noturno | `RESUMO_NOTURNO_HORA`, `RESUMO_NOTURNO_AGENDADOR` (`0` desliga a thread), `RESUMO_NOTURNO_PROVIDER/MODEL/API_URL/API_KEY/CA_CERT/REASONING_EFFORT` e `RESUMO_NOTURNO_TZ` | o batch da madrugada e seu provedor próprio — ver `resumo_noturno.md` |
| Nós descentralizados | `PAPEL` (`central`/`no`), `CENTRAL_URL`, `NO_TOKEN`, `NO_HEARTBEAT_SEGUNDOS`, `NO_TIMEOUT_LEITURA_SEGUNDOS` | papel da instância e o canal nó↔central (token Bearer, batimento, timeout de leitura por faixas) — ver `no_descentralizado.md` |
| Alertas do SIMBA | `SIMBA_ALERTA_*` (pesos e limiares dos seis padrões) | sem eles vale o padrão de fábrica; normalmente editados pela tela **Administração → Alertas SIMBA** — ver `simba.md` |
| Busca híbrida do chat | `RAG_LIMITE` (padrão 15), `RAG_PESO_SEMANTICO`, `RAG_PESO_TEXTUAL`, `RAG_PESO_GRAFO` | tamanho e pesos da fusão RRF da ferramenta de busca do chat — ver `rag_hibrido.md` |
| Upload | `UPLOAD_DOCUMENTO_MB` (padrão 50) | teto por arquivo na importação de documentos/camadas geoespaciais; editável na tela **Desempenho**, vale na hora |
| Ollama | `OLLAMA_CONTEXT_LENGTH` (padrão 32000), `OLLAMA_NUM_PARALLEL` (padrão 1) | entram no ambiente do container `ollama` via compose; exigem `docker compose up -d` |
| Runtime | `PASTA_LOGS` (padrão `/app/logs`), `DOCS_DIR`, `ENV_FILE` (padrão `/app/.env`), `MIGRAR_NO_STARTUP` (`0` desliga o runner no startup) | pasta dos logs em arquivo, onde a tela Documentação lê os `.md`, caminho do `.env` dentro do container e a migration gate — ver `atualizacao.md` |

### Desempenho — dimensionamento automático (`api/funcoes/desempenho_utils.py`)

Até aqui os limites de concorrência e de cache eram constantes no código, escolhidas para um
servidor específico: pool de 20 conexões, 8 `.ufdr` em cache, 4 inquéritos de matriz de
embeddings, e o Postgres inteiro nos defaults de fábrica. O mesmo número servia a um notebook de
8 GB e a um servidor de 247 GB — apertado num, desperdício no outro.

Agora as constantes são fórmulas sobre (núcleos, RAM), **sempre com piso e teto**. O piso é
exatamente o valor que estava no código antes, então nenhuma instalação piora; o teto evita que
uma máquina grande reserve memória que nunca vai usar.

**Precedência:** chave explícita no `.env` › `DESEMPENHO_PERFIL` › hardware detectado.

#### Detecção respeita o cgroup

`psutil.virtual_memory()` e `os.cpu_count()` enxergam a máquina **hospedeira**. Enquanto não
houver `mem_limit`/`cpus` no compose isso é justamente o que queremos, mas se um dia houver, os
dois passariam a mentir — e dimensionaríamos cache para uma RAM que o container não pode tocar.
Por isso a detecção lê primeiro `/sys/fs/cgroup/memory.max` (v2) ou `memory.limit_in_bytes` (v1),
e o `cpu.max`/`cpu.cfs_quota_us` correspondente; só cai no `psutil` quando não há limite. A
origem de cada número (`cgroup` ou `host`) aparece na tela e no log de startup.

#### O que vale quando

Esta é a distinção que a tela existe para deixar clara — gravar no `.env` **não** é o mesmo que
estar valendo:

| Quando | Parâmetros | Como aplicar |
|---|---|---|
| **Já** | `JOBS_PESADOS_LIMITE`, `CLIP_NUM_THREADS`, `TRANSCRICAO_THREADS`, `EXTRACOES_SIMULTANEAS` | nada: cada módulo relê o `.env` a cada chamada |
| **Ao reiniciar a API** | `API_THREADS_REQUISICAO`, `DB_POOL_MAX`, os quatro `CACHE_*`, os três `DB_*_TIMEOUT_MS` | `docker compose up -d` |
| **Ao recriar o container da API** | `API_WORKERS` | `docker compose up -d` (um `restart` não basta: o valor entra no comando do uvicorn, e o comando só é remontado quando o container é recriado) |
| **Ao reiniciar o Postgres** | os dez `PG_*` | `docker compose up -d` (recria o container do banco) |

A coluna **em vigor** da tela não é lida do `.env`: vem do processo e do próprio banco — o
`max_size` do pool aberto, as constantes que cada módulo de cache calculou no import, o valor que
o limiter do AnyIO realmente recebeu, e `pg_settings` para os `PG_*`. É comparando esse valor com
o que valerá no próximo start que a tela sabe dizer que falta reiniciar, e quais campos.

#### Por que o threadpool importa tanto

**Nenhuma rota desta API é `async def`** — as ~320 são `def` síncrono, então o FastAPI as despacha
para o threadpool do AnyIO, e o limite desse threadpool **é** o limite de requisições concorrentes.
O default do AnyIO é 40 e nunca havia sido ajustado: num servidor de 32 núcleos isso deixava a
máquina ociosa. `API_THREADS_REQUISICAO` é esse número, e `main.py` o aplica num handler de
startup `async def` — precisa ser `async`, porque o limiter vive num RunVar do event loop e um
handler `def` rodaria numa thread onde esse RunVar não existe.

#### Postgres pelo `command:` do compose

O serviço `postgres` recebe um `command:` com `-c chave=${PG_*:-default}` para cada parâmetro. É
`${VAR:-default}` e não `${VAR-default}` de propósito: a tela grava `CHAVE=` (vazio) para dizer
"volta ao automático", e só a primeira forma trata vazio como ausente. Um clone novo, sem nenhuma
dessas variáveis, sobe com os defaults de fábrica — igual a antes.

> Se o container do Postgres não subir depois de mudar isto, o suspeito é `shared_buffers` grande
> demais para a memória compartilhada do host. O rollback é apagar as variáveis do `.env` e subir
> de novo.

#### O que a tela não faz

Não reinicia nada. Fazer isso exigiria expor o socket do Docker ao container da API, o que
transformaria qualquer comprometimento da API em controle do host — a tela grava e mostra o
comando a rodar. O `POST` só aceita as chaves conhecidas: sem esse filtro, um corpo arbitrário
poderia gravar qualquer par no `.env`, inclusive `POSTGRES_PASSWORD` ou `AI_API_KEY`.

O campo de **workers do uvicorn existe** nesta tela (`API_WORKERS`, default 1), e subir esse
número é o maior ganho isolado num servidor de muitos núcleos. Ele só passou a ser seguro depois
que o estado dos jobs pesados saiu dos dicionários de processo (`ocr_utils._PROGRESS` e irmãos) e
foi para `JOB_TB01_JOBS`: com o progresso no banco, o polling cai em qualquer worker e encontra o
job. É por isso que `JOBS_PESADOS_LIMITE` é contado na tabela, e não num semáforo de processo.

Ao mexer nele, lembre que threads, pool e caches são **por worker**: `PG_MAX_CONNECTIONS` passa a
precisar de `DB_POOL_MAX × API_WORKERS` (+ `DB_POOL_LONGA_MAX`), e esquecer essa multiplicação é o
erro mais fácil e o mais barulhento (`PoolTimeout` sem aviso). É exatamente esse estouro que o
**PgBouncer** resolve quando a conta fica grande demais: com `PGBOUNCE_HOST` ligado, o pool de
requisição de todas as réplicas se multiplica sobre o `default_pool_size` do pooler em vez de
abrir conexão direta no banco — ver "PgBouncer" acima. O valor entra no comando do uvicorn, então vale só ao
**recriar** o container (`docker compose up -d`) — um `restart` não basta.

## Referências

- `docker-compose.yml`, `api/Dockerfile`, `Dockerfile.ollama`, `Dockerfile.pgbouncer`,
  `pgbouncer/pgbouncer.ini.tpl` + `pgbouncer/entrar.sh`, `nginx/default.conf`, `.env`,
  `.gitignore` — na raiz.
- `api/main.py`, `api/logging.json`, `api/requirements.txt`.
- Routers: `api/rotas/*.py`, `api/rotas/sistema.py` (`GET /api/health`, `GET /api/sistema/metricas`); lógica: `api/funcoes/*.py`, `api/funcoes/env_utils.py` (leitura/escrita do `.env`), `api/funcoes/db_utils.py` (dois pools de conexões), `api/funcoes/leader_utils.py` (liderança dos laços de fundo), `api/funcoes/backup_utils.py` (verificação dos dumps).
- Backup: [backup.md](backup.md), `scripts/backup-offsite.sh`, `scripts/backup-teste-restore.sh` — sidecar `backups`, offsite restic e teste de restauração.
- Migrações: `db/init/`; import de referência: `db/import/`, `db/data/` — ver `banco_dados.md` e `ingestao.md`.
- `api/funcoes/desempenho_utils.py` (detecção de hardware e fórmulas), `web/desempenho.html`,
  `web/js/desempenho.js`, `web/css/desempenho.css` — tela Administração → Desempenho.
- `interface.md` — painel Servidor (métricas de `GET /api/sistema/metricas` vistas de dentro do
  container da API).