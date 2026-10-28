# Analyser — deploy no OpenShift (Red Hat)

Porto do `C:\Git\analyser` (Docker Compose) para o OpenShift, seguindo os padrões que já
funcionavam neste repositório (namespace `pf-policia`, label `app: pf-policia`,
Route TLS, porta 8080 no front, imagens Red Hat).

## O que mudou na arquitetura

| Compose | OpenShift | Observação |
|---|---|---|
| `api-1`/`api-2` + `API_REPLICA_ID` | Deployment `api` (replicas>1 com storage RWX) | estado já vive no banco; nginx do compose → nginx próprio |
| `web` (nginx + bind mount `./web`) | **imagem `analyser-web`** (estáticos embutidos — ~30 MB não cabem em ConfigMap) | `nginx-unprivileged` na 8080, Route edge |
| `postgres` (pgvector/pgvector:pg16) | **imagem `analyser-postgres`**: `registry.redhat.io/rhel9/postgresql-16` + hook `db/bootstrap/openshift_postgresql_start.sh` | a tag atual da imagem RHEL **já traz o pacote `pgvector`**; o hook aplica `db/init/*.sql` em ordem numérica no primeiro boot e registra o baseline de `db/migracoes` |
| `pgbouncer` (bind .env) | Deployment `pgbouncer`, credenciais via Secret | session-mode, sem Route |
| `tunel` (mesma imagem da API) | Deployment `tunel` com a **mesma imagem** `analyser-api` e command `tunel_app:app` | sempre 1 réplica (WebSocket) |
| `ollama`, `backups`, `registry` | **fora do escopo** por decisão | embeddings por API externa; backup via `oc exec`/pg_dump agendado fora |
| bind mounts `./.env`, `./VERSION`, `./db`, `./docs`, `./data`, `./logs`, `./.cache` | Secret+emptyDir (`.env`), embutido na imagem (`VERSION`, `db/migracoes`, `db/import`, `docs`), PVCs (`data`, `logs`, `cache`) | ver abaixo |

### O `.env` (lido **e escrito** pela aplicação)

`api/funcoes/env_utils.py` faz read-modify-write num arquivo `.env`. No OpenShift:

- o `.env` vive num **Secret** `analyser-env` (montado read-only em `/seed`);
- um **initContainer** copia para um **emptyDir** gravável em `/app/runtime`;
- o container usa `ENV_FILE=/app/runtime/.env`.

**Consequência**: edições feitas na tela Administração voltam ao valor do Secret quando o
pod reinicia. Para tornar uma mudança permanente, atualize o Secret e faça
`oc rollout restart deployment/api`.

## Pré-requisitos

- `oc` logado no projeto `pf-policia` (ajuste os labels/namespace no `openshift.yaml` se for outro).
- Para a **Opção B** (build local): `podman` (ou `docker`) com acesso aos repositórios upstream
  (`python:3.12-slim`, `alpine`, `nginx-unprivileged`) e ao `registry.redhat.io` (login para a
  imagem base do Postgres). Na **Opção A** quem baixa tudo é o próprio cluster.
- StorageClass default no cluster (os PVCs não fixam `storageClassName`). Para `api.replicas > 1`,
  a classe precisa suportar `ReadWriteMany` (NFS/CephFS) — os PVCs estão como `ReadWriteOnce`.

## 1. Build das imagens

### Opção A — build DENTRO do cluster (recomendada; usa este repositório Git)

`openshift-builds.yaml` define 4 BuildConfigs (strategy Docker, um `dockerfilePath` por
imagem) + ImageStreams. Cole o arquivo no console (**Developer → Import YAML**) ou:

```bash
oc apply -f openshift-builds.yaml
oc start-build analyser-pgbouncer            # comece pelo leve...
oc start-build analyser-postgres
oc start-build analyser-web
oc start-build analyser-api                  # ...e o pesado em paralelo (fila Serial por BC)
oc get builds -w                             # acompanhe; Logs no console em Builds → <nome>
```

O build da API é leve agora: o central **não** carrega modelos (sem torch/transformers/
faster-whisper/pytesseract, sem Tesseract e sem JRE na imagem — eles vivem no nó da delegacia,
docs/no_descentralizado.md). ffmpeg ficou só para miniaturas. Rode de 1 a 5 min. O
`completionDeadlineSeconds` do YAML já cobre isso. Os ImageStreams de saída (`analyser-*:latest`)
são exatamente os que o `openshift.yaml` consome — nada para ajustar.

Se o build do Postgres falhar ao puxar `registry.redhat.io/rhel9/postgresql-16` (cluster sem
entitlement), edite o BuildConfig `analyser-postgres` e descomente o `buildArgs`
com `POSTGRES_BASE=quay.io/sclorg/postgresql-16-c9s` (imagem comunitária idêntica, sem login).

Depois de qualquer rebuild: `oc rollout restart deployment/<nome>` — os Deployments puxam por
tag `:latest` (sem trigger automático de ImageStream).

### Opção B — build local + push (podman/docker na sua máquina)

```bash
REG=$(oc registry info)                      # ex.: default-registry.apps.<cluster>...
PROJ=pf-policia
oc registry login                            # ou: podman login -u $(oc whoami) -p $(oc whoami -t) $REG

# ImageStreams de destino (o push cria as tags)
for im in analyser-api analyser-web analyser-postgres analyser-pgbouncer; do
  oc create imagestream $im --dry-run=client -o yaml | oc apply -f -
done

podman build -f Dockerfile.api       -t $REG/$PROJ/analyser-api:latest .
podman build -f Dockerfile.web       -t $REG/$PROJ/analyser-web:latest .
podman build -f Dockerfile.pgbouncer -t $REG/$PROJ/analyser-pgbouncer:latest .
podman build -f Dockerfile.postgres  -t $REG/$PROJ/analyser-postgres:latest .
podman push --authfile=$XDG_RUNTIME_DIR/containers/auth.json -a $REG/$PROJ/analyser-api:latest
# (ou: podman push ... para cada uma das quatro tags)
```

O build da API é leve (python-slim + psycopg + ffmpeg para miniaturas — os modelos e o JRE moram
no nó da delegacia).
O `Dockerfile.postgres` falha **no build** se a tag base não tiver `pgvector` — atualize o tag.

## 2. Secret `analyser-env`

```bash
cp env.openshift.example openshift.env.local   # preencha as credenciais
oc create secret generic analyser-env \
  --from-file=.env=openshift.env.local \
  --from-literal=POSTGRES_USER=<mesmo valor do .env> \
  --from-literal=POSTGRES_PASSWORD=<mesmo valor do .env> \
  --from-literal=POSTGRES_DB=<mesmo valor do .env>
```

Os `--from-literal` alimentam os Deployments do Postgres (`POSTGRESQL_*`) e do PgBouncer
(`POSTGRES_*`); o arquivo `.env` alimenta a API. **Devem bater** — uma fonte de verdade.

## 3. Aplicar

```bash
oc apply -f openshift.yaml
oc rollout status deployment/postgres   # primeiro boot aplica db/init + baseline (~1-2 min)
oc rollout status deployment/pgbouncer
oc rollout status deployment/api        # startupProbe espera as migrações pendentes
oc rollout status deployment/tunel
oc rollout status deployment/web
oc get route web                        # URL https gerada pelo cluster
```

O banco nasce com **admin** definido por `db/init/07_seguranca_usuarios.sql` (mesma regra do
compose). Primeiro acesso: `https://<host-da-route>/login.html`.

### Limpeza dos recursos do teste anterior

```bash
oc delete deployment nginx --ignore-not-found
oc delete configmap nginx-conf --ignore-not-found
```

## 4. Verificação rápida

```bash
R=$(oc get route web -o jsonpath='{.spec.host}')
curl -k https://$R/api/health        # {"status":"ok",...} (só rota pública)
curl -k https://$R/login.html -o /dev/null -w '%{http_code}\n'   # 200
oc logs deployment/postgres | grep analyser-bootstrap             # schema aplicado
```

## Operação

- ** Migrações**: `db/migracoes/*.sql` vão **dentro da imagem** (`Dockerfile.api`). Novo
  delta = rebuild + push + `oc rollout restart deployment/api` (o `migrar_utils.py` aplica
  no startup; `MIGRAR_NO_STARTUP=0` no environment desliga).
- **Backups**: não há sidecar `backups` aqui. Agende fora do cluster:
  `oc rsync`/`pg_dump` via `oc exec deployment/postgres -- pg_dump -U appuser -Fc analyser > dump.dump`.
- **Escala da API**: suba `spec.replicas` do Deployment `api` — exige StorageClass RWX nos
  PVCs `analyser-data`/`analyser-logs`/`analyser-cache` (hoje RWO = 1 réplica).
- **Nós de delegacia** (papel `no`): continuam rodando com o `docker-compose.no.yml` do
  analyser fora do cluster; alcançam este central pela URL da Route (HTTPS) — o túnel NAT
  (`/tunel/ws/node`) atravessa a Route normalmente (WebSocket suportado).
- **Tiles OSM**: o `/osm-tiles/` exige saída HTTPS do pod web para `tile.openstreetmap.org`
  e o resolver do cluster `172.30.0.10` (padrão OpenShift; ajuste em `nginx/openshift.conf`
  se o serviceNetwork do cluster for outro).

## Diferenças funcionais conhecidas (v1)

- `ollama` local fora do cluster → use embeddings/IA por **API externa** nas Configurações
  (ou aponte `OLLAMA_BASE_URL`/equivalente para um Ollama externo acessível).
- Tela "Atualização" (atualizador de host) e sidecar de backup não se aplicam: neste deploy
  a atualização é **rebuild da imagem + rollout**.
- Uploads de .ufdr/zip de GB dependem do `client_max_body_size 0` do nginx (mantido) **e**
  dos timeouts da Route; a Route edge do OpenShift não tem limite de corpo — sem ação.
