# Backup, pooler e réplicas — a base de escala do central

> Três peças que preparam o central para dezenas de nós e milhares de usuários sem trocar
> docker compose por nada: **dump diário verificado** do banco, **PgBouncer** na frente do
> Postgres e **liderança única** para os laços de fundo da API (o pré-requisito de subir a API
> em mais de uma réplica atrás do nginx). Restaurar de verdade e tirar o dump do host são os
> dois scripts de host deste documento.

## 1. Backup do banco — quem faz o quê

| Camada | O que é | Onde roda | Frequência |
|---|---|---|---|
| Dump | `pg_dump -Fc` em `./backups/{last,daily,weekly,monthly}/` (`<db>-AAAA-MM-DD[-HHMMSS].dump`); um dump também na **primeira subida** do sidecar (`BACKUP_ON_START`) | sidecar `backups` (compose) | diário (`BACKUP_AGENDAMENTO`) |
| Retenção local | 14 diários / 8 semanais / 12 mensais (`BACKUP_DIAS/SEMANAS/MESES`) | próprio sidecar | — |
| Verificação | dump existe? > 0 bytes? < 26 h? | agendador da API, tarefa `verificacao_backup` no ledger `AGD_TB01_EXECUCOES` | diário |
| Offsite | `backups/` + `data/` (as provas!) via restic p/ segundo host | `scripts/backup-offsite.sh` no cron do host | diário sugerido |
| Restauração | restaura o dump num Postgres descartável; **qualquer erro do `pg_restore` é falha** (dump fiel não fere as próprias constraints); ignora arquivo escrito há < 10 min (pg_dump em curso) | `scripts/backup-teste-restore.sh` no cron do host | semanal sugerido |

Por que as duas últimas existem: o dump mora no MESMO disco do banco (incêndio leva os dois) e
um dump que nunca foi restaurado é uma esperança, não um backup. A verificação da API pega a
morte **silenciosa** do sidecar (senha, disco cheio) no dia seguinte — o resultado fica no
ledger, visível em:

```sql
SELECT * FROM investigacao.agd_tb01_execucoes WHERE tp_tarefa = 'verificacao_backup'
 ORDER BY dt_referencia DESC LIMIT 14;
```

`./backups` é leitura pura para a API (bind `:ro` em `/app/backups`); só o sidecar escreve. A
pasta está no `.gitignore` — dump contém o conteúdo inteiro do banco.

### Restauração manual (o caminho do desastre)

```bash
cd /mnt/dados/analyser
scripts/backup-teste-restore.sh              # só VERIFICA que restaura (container descartável)
# para restaurar no próprio sistema (apaga o banco atual!): pare a api, e
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean \
  --if-exists --no-owner < backups/<db>-<mais-recente>.dump
```

## 2. PgBouncer (pooler de conexões)

Cada processo da API traz o seu `ConnectionPool` (`funcoes/db_utils.py`); N réplicas ×
`DB_POOL_MAX` estourariam o `PG_MAX_CONNECTIONS`. O serviço `pgbouncer` do compose multiplica
os clientes sobre `default_pool_size=60` conexões reais (ver `pgbouncer/pgbouncer.ini.tpl` —
cada valor tem o porquê lá).

- **Ativar**: `PGBOUNCE_HOST=pgbouncer` no `.env` e `docker compose restart api-1 api-2` (a chave é lida
  ao abrir o pool; o `.env` entra por bind mount, não precisa rebuild).
  Ausente/vazio = a API continua indo direto ao Postgres (comportamento antigo, e é assim que
  a instalação fica até você decidir).
- **`pool_mode = session`**: os `set_config` de timeout por sessão da API e as transações
  longas simplesmente funcionam — o pooler é transparente.
- **Quem NÃO passa pelo pooler** (vai direto ao Postgres de propósito): pool de conexões
  longas `longa=True` (`db_utils._criar_pool_longa`, teto `DB_POOL_LONGA_MAX=8`), conexões de
  liderança (`db_utils.nova_conexao_dedicada`), o `psql` de importação e o sidecar de backup.
  O orçamento do `PG_MAX_CONNECTIONS` é: 60 (pooler) + 8 (longas) + folga de administração.
- A porta 6432 não é publicada no host: só a rede interna do compose alcança o pooler.

## 3. Líder único para os laços de fundo (`funcoes/leader_utils.py`)

Com a API em réplicas, todo `threading.Thread` de fundo subiria N vezes. O estado atual da
casa, por laço:

| Laço | Exclusividade |
|---|---|
| despachante de extrações | já era segura: `pg_advisory_xact_lock` + `SKIP LOCKED` por item (a fila é o banco) |
| jobs pesados (OCR/CLIP) | já era segura: vaga contada na tabela `JOB_TB01` |
| migrações no startup | já eram seguras: `pg_advisory_lock` 72301 |
| resumo noturno / higiene / verificação de backup | ledger `AGD_TB01` (PK = a vaga do dia) |
| **agendador (o varredor)** | **eleição** `leader_utils.AGENDADOR` — uma réplica varre, as outras só disputam o lock |
| **watchdog de análises** | **eleição** `leader_utils.WATCHDOG_ANALISES` |

A eleição é um `pg_try_advisory_lock` de SESSÃO numa conexão dedicada: o lock morre com a
conexão, então a liderança migra sozinha (até 30 s) quando a líder tomba. Não é substituto da
reivindicação por banco — o ledger continua sendo a garantia; a eleição só corta o varrer em
dobro do caminho feliz. Diagnóstico: `funcoes.leader_utils.estado_liderancas()` no log.

## 4. Réplicas de API (implementado na 4.8.0)

A API roda em **duas réplicas** (`api-1`/`api-2`) atrás do nginx — ver
`infraestrutura.md` → "Réplicas de API" para o detalhe de compose/nginx/logs. O que a base
das seções 2 e 3 já garantia (pools pelo pooler, laços por eleição de líder) tornou a réplica
múltipla segura; o que mudou de fato:

- **Compose**: a definição comum vai na âncora `x-api`; `api-1`/`api-2` a copiam com `<<:` e
  trocam só o `container_name` e o `API_REPLICA_ID`. Dois serviços **declarados** (não
  `--scale`, que é incompatível com `container_name` e seria resetado pelo `up -d` do
  `analyser-update`).
- **nginx**: `upstream analyser_api` lista as duas com `max_fails=3 fail_timeout=15s` —
  round-robin puro, **sem ip_hash**. Um pedido HTTP inteiro vai a uma única réplica (o nginx
  não migra uma requisição no meio, e um POST de upload não é reenviado a outra no failover),
  então o upload de horas não "troca de réplica" — a afinidade que se receava no rascunho
  não é necessária. As entregas dos nós (`/api/no/*`) já eram idempotentes por lote no banco.
- **Pré-requisito**: `PGBOUNCE_HOST=pgbouncer` ativo (sem o pooler, `2 × DB_POOL_MAX` + pools
  longos se aproximam de `PG_MAX_CONNECTIONS`).

## 5. Próximas fases (não implementadas)

- **`data/` compartilhado** (MinIO/NFS) apenas se o central um dia tiver mais de um host.
