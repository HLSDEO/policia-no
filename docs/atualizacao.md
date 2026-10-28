# Atualização de versão e migração de schema

## Visão geral

A plataforma roda on-prem, uma instalação por servidor, atualizada a partir das **tags de
release** do repositório privado `HLSDEO/analyser` no GitHub. Este documento cobre as três peças
que tornam essa atualização segura e repetível:

1. **Identidade de versão** — o arquivo `VERSION` na raiz, exposto por `GET /api/sistema/versao`
   e mostrado na tela **Administração → Servidor**.
2. **Migração de schema rastreada** — os deltas de `db/migracoes/` são aplicados uma vez cada,
   registrados em `SEGURANCA.SIS_TB01_MIGRACOES`, e rodam **sozinhos no startup da API**. Some o
   *"esquecer a migração manual depois do git pull"*, que era o erro de deploy mais comum.
3. **Atualização pela tela** — um botão na tela Servidor baixa a nova versão, troca os arquivos e
   reconstrói os containers. Quem faz o trabalho privilegiado é um agente `systemd` no host
   (`scripts/analyser-update`), não o container — o token do GitHub nunca entra no container.

Nada aqui depende de a instalação ser um checkout git: o deploy passa a ser a **árvore de
arquivos de uma tag**, extraída sobre `/opt/analyser`, preservando `.env`, `data/`, `db/data/` e
`.deploy/`.

## Fluxo de uso

### Publicar uma versão (quem desenvolve)

1. Suba o `VERSION` da raiz para a nova versão (`1.8.0` → `1.8.1` / `1.9.0`).
2. Se houve mudança de schema, ela está nos **dois lugares** — ver
   [Como mudar o schema](#como-mudar-o-schema).
3. Commit, tag `X.Y.Z`, push da tag. **O `VERSION` no commit da tag tem que ser exatamente
   `X.Y.Z`.** Cortar a tag num commit onde o `VERSION` ainda está atrás (bump esquecido — já
   aconteceu com `1.7.11` e `1.7.19`) gera um **release malformado**: o `analyser-update check`
   marca `malformado: true`, a tela mostra o aviso e **não oferece** o update, e o `apply`
   recusaria de qualquer forma ("VERSION divergente na tag"). Conserte cortando `X.Y.(Z+1)` com o
   `VERSION` certo (não é preciso mover a tag publicada).
4. Crie o **release** no GitHub a partir da tag (é o que `releases/latest` devolve). O corpo do
   release vira as "notas" mostradas na tela.

### Atualizar um servidor (quem opera)

1. **Administração → Servidor**, cartão **Versão e atualização**.
2. **Verificar** — grava `checar.req`; o path unit `analyser-update-check-req@` do host roda um
   `analyser-update check` na hora e a tela espera (~12 s) o `disponivel.json` novo. Mostra
   "versão X disponível" com link, ou "instalação em dia (X) · verificado agora há pouco". (O
   agente do host também checa sozinho 4×/dia, então mesmo sem clicar o atraso máximo é ~6 h.)
3. **Atualizar para X** → confirma → a tela passa a mostrar o log ao vivo (`docker compose up
   -d --build`, health-check, migrações). A plataforma fica alguns minutos indisponível durante
   o build.
4. Fim: "Concluído — versão X". Em caso de falha, o log fica na tela e a versão anterior segue no
   ar (o build só troca a imagem quando termina); para regredir de vez, `analyser-update
   rollback` no host.

### Atualizar um NÓ (tela Servidores)

O nó também se atualiza sozinho — o central só dá o gatilho:

1. **Administração → Servidores**, botão **Atualizar** na linha do nó (confirmação modal).
2. O central chama `POST /no/atualizar` no nó (token do nó); o nó grava `pedido.json` no seu
   `.deploy/controle` — exatamente o handshake acima, mas rodando na máquina da delegacia. O
   `analyser-update` do host do nó faz o resto (release, build, `up -d`, health `/no/saude`).
3. A tela acompanha pelo `GET /api/servidores/{id}/atualizacao` (repassa o `GET /no/atualizacao`
   do nó); durante o build/restart o nó fica offline 1–2 min de propósito — o polling tolera.

Pré-requisitos no nó: `scripts/instalar-host.sh` rodado (agente + `.deploy`), bind
`.deploy/controle` presente no `docker-compose.no.yml` e o check periódico do host já ter
achado o release (ou passe `{"versao": "X.Y.Z"}` no corpo). Como o endpoint novo só existe a
partir desta versão, **a primeira atualização de um nó antigo ainda é manual** (`sudo
analyser-update apply` no host dele); daí em diante, o botão funciona para sempre.

O botão **Atualizar** SEMPRE manda a tag-alvo (a tela também passa), obtida de um
`checar_atualizacao(forcar=True)` do central — inclusive quando o central já está nela; sem
alvo o nó cairia no `disponivel.json` velho dele e recusaria com "já está em dia". Se o check
do host devolver uma carga atrás do push e o nó recusar, a rota refaz o check e tenta uma vez
com o alvo fresco. A tela ainda tem: **Verificar** (`POST /api/servidores/verificar` — release
mais novo + quais nós estão atrás), a coluna Versão vermelha quando o nó está atrás do
central, e o badge `✓/✗/⟳` da última atualização de cada nó (vem de `atualizacao` no
`GET /no/estado`, lido do resultado que o agente do nó deixa em `.deploy/controle`).

#### Registry local (nó sem saída para o Docker Hub)

A estação do nó normalmente não consegue `docker build` — sem saída para `registry-1.docker.io`
e `deb.debian.org`, o `apt-get`/`FROM` do build morre. O fluxo alternativo usa o central como
fábrica de imagem:

1. `REGISTRO_NO=<ip-do-central>:5000` no `.env` dos DOIS (e `REGISTRY_DIR=/mnt/dados/registry`
   no central, para as imagens sobreviverem ao apply).
2. O `docker-compose.yml` do central traz um serviço `registry` (imagem oficial `registry:2`);
   o `up -d` do apply do central sobe e publica cada release em
   `<registry>/analyser-api-no:<versao>` + `:latest` (o build reaproveita o cache da API —
   sai em segundos). Falha de publish é só aviso: nunca derruba o update do central.
3. No nó, `analyser-update apply`/`rollback` com `REGISTRO_NO` faz `docker pull` da versão
   pinada, retaga para `analyser-api-no:latest` e sobe **sem build**. O nó registra sozinho o
   registry em `insecure-registries` (daemon.json + restart do docker, roda como root). Pull
   falhou → cai para o build local (comportamento antigo).

### Reiniciar o servidor (aplicar o Desempenho)

1. **Administração → Desempenho**, botão **Reiniciar servidor** (ao lado de Salvar).
2. Confirma → a API grava `reiniciar.req`; a path unit `analyser-reiniciar@` do host roda
   `docker compose up -d`, **reinicia as duas réplicas uma de cada vez** (`api-1` e depois
   `api-2` — enquanto uma sobe a outra atende, sem indisponibilidade no modo réplica), recarrega
   o nginx e espera o `/api/health`. Os valores gravados no `.env` (pool, caches, `PG_*`) passam
   a valer. No nó (container único) ele reinicia o serviço `no`.
3. A tela sonda sozinha e recarrega com a coluna "em vigor" atualizada; se o compose falhar
   no host, ela mostra o motivo (o log completo está em `.deploy/controle/reiniciar.log`).

Sem agente de host nem vigia (e sem docker ao alcance da API), a tela mostra o comando
para rodar na mão. Instalações que já rodaram o `instalar-host.sh` antes deste botão existir
precisam rodá-lo de novo — é ele que instala as units do reinício.

### Dev local sem systemd (Windows, Docker Desktop)

No dev não há systemd para consumir o pedido; quem faz esse papel é o vigia
`scripts/observar-reinicio.ps1`, rodando **no host** (não no container):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\observar-reinicio.ps1
```

Deixe o terminal aberto (rode uma instância só; Ctrl+C para parar). Ele toca um "coração"
em `.deploy/controle/observador-reinicio.ok` a cada 5 s — é vendo esse arquivo fresco que a
API decide gravar o `reiniciar.req` em vez de mandar rodar na mão. Ao clicar no botão, o
vigia roda `docker compose up -d`, espera o `/api/health` (porta do `WEB_PORT` do `.env`) e
escreve `reiniciar-resultado.json`, que a tela lê para dizer se deu certo. O log vai para
`.deploy/controle/reiniciar.log`. Como o vigia é código do repo e a API vai no build da
imagem, depois de atualizar o checkout rode `docker compose up -d --build api-1 api-2` uma vez.

### Pela linha de comando (host)

```bash
analyser-update status                 # versão instalada, última checada, pedido em curso
analyser-update check                  # força a checagem, grava .deploy/controle/disponivel.json
analyser-update apply --versao 1.9.0   # aplica uma versão específica
analyser-update apply                  # aplica a última release
analyser-update rollback               # volta ao snapshot pré-update mais recente
analyser-update rollback --para 1.8.0  # volta a um snapshot específico
analyser-update reiniciar              # docker compose up -d + espera a API (pedido da tela Desempenho)
```

## Como funciona

### Identidade de versão — `api/funcoes/versao_utils.py`

- `VERSION` é **bind mount** (`docker-compose.yml`: `./VERSION:/app/VERSION:ro`), não `COPY` no
  Dockerfile — bumpar a versão não exige rebuild, e o `analyser-update` sobe com `--build` de
  qualquer forma, então código e `VERSION` andam juntos.
- `versao_instalada()` lê o arquivo; `resumo()` é o payload de `/api/sistema/versao` (não toca em
  rede nem em banco); `checar_atualizacao()` compara com a última publicada.
- **A API nunca fala com o GitHub nem vê o token.** A fonte de "última versão publicada" é
  `/opt/analyser/.deploy/controle/disponivel.json`, escrito pelo `analyser-update check` no host.
  *Fallback:* se esse arquivo não existir mas houver um `/app/.deploy/token` legível pelo
  container, a API consulta `releases/latest` direto — caminho simples para quem ainda não montou
  o agente.
- **"Verificar" força uma checagem real.** `checar_atualizacao(forcar=True)` grava
  `.deploy/controle/checar.req`; o path unit `analyser-update-check-req@<instância>.path` no host
  dispara o mesmo `.service` do check periódico, que reescreve `disponivel.json` e apaga o
  `checar.req`. A API espera até ~12 s por esse arquivo novo antes de responder. Sem agente de
  host (dev/Windows) o botão cai no fallback GitHub-direto de sempre.
- **Release malformado.** O `check` confere `VERSION` na tag × nome da tag (`GET
  /contents/VERSION?ref=<tag>`). Divergiu → `disponivel.json` ganha `malformado: true` + `erro`,
  `checar_atualizacao` devolve `disponivel: false` com o `erro` e `solicitar_atualizacao`
  recusa. É a checagem que pega o "esqueci de subir o VERSION antes de taguear" cedo, na tela,
  em vez de só no meio do `apply`.

### Migração de schema — `api/funcoes/migrar_utils.py`

`db/init/*.sql` descreve o schema **completo** e só roda em volume vazio (primeira subida do
Postgres, via `db/bootstrap/00_aplicar_migracoes.sh`). Um banco que já existe não recebe nada de
lá. `db/migracoes/NNNN_*.sql` é o **diário incremental**:

- O runner varre `db/migracoes/*.sql`, ordena pelo prefixo numérico, e aplica cada um que não
  está em `SEGURANCA.SIS_TB01_MIGRACOES` — uma transação por arquivo, `pg_advisory_lock` para
  serializar instâncias concorrentes, sha256 do arquivo gravado no ledger.
- **Roda no startup da API** (`main.py`, `_preparar_banco`), a menos que `MIGRAR_NO_STARTUP=0`.
  Se um delta pendente falha, a API **recusa subir** com a causa no log — em vez de subir e
  quebrar na primeira rota que usa a coluna que não existe.
- Também roda pelo `analyser-update apply` (explícito, para capturar no log) e pelo botão
  "Aplicar pendentes" da tela / `python /app/tools/migrar.py`.
- **Forward-only.** Não há "down". Reverter é um delta compensatório novo.
- Arquivo cuja 1ª linha é `-- migracao: sem-transacao` roda em autocommit (para
  `CREATE INDEX CONCURRENTLY`, `VACUUM`); precisa ser idempotente.

Numa **instalação nova**, a Fase 2 do bootstrap insere uma linha `DS_ORIGEM = 'bootstrap'` por
delta **sem executá-lo** (o schema de `db/init/` já contém o efeito), para o runner não
reaplicar. Num servidor **que já existe**, o `instalar-host.sh` faz o mesmo, uma vez, no cutover.

#### Como mudar o schema

Toda alteração entra em **dois lugares** (ver `db/migracoes/README.md`):

| Arquivo | Forma | Para |
|---|---|---|
| `db/init/NN_dominio.sql` | declarativa (coluna no `CREATE TABLE`) | instalação nova |
| `db/migracoes/NNNN_descricao.sql` | imperativa (`ALTER TABLE …`) | bancos que já existem |

Esquecer um dos dois faz a instalação nova divergir da atualizada. O
`db/tools/conferir_paridade.sh` (opcional, CI) pega isso comparando `pg_dump --schema-only` de um
banco só do `init/` contra um banco do `init/` da versão anterior + os deltas novos.

### Atualização pela tela — o agente do host

```
Tela Servidor  ──POST /api/sistema/atualizacao/aplicar──►  API grava .deploy/controle/pedido.json
                                                                     │
                              systemd analyser-update-apply@<instância>.path (PathExists)
                                                                     ▼
                                            analyser-update apply --pedido …
                                              1. curl tarball da tag (token do host)
                                              2. extrai para staging, confere VERSION
                                              3. snapshot do estado atual → .deploy/versoes/
                                              4. rsync --delete (exclui .env, data/, db/data/, .deploy/)
                                              5. docker compose up -d --build
                                              6. espera /api/health
                                              7. python /app/tools/migrar.py
                                              8. escreve build-info + resultado.json, remove pedido.json
                                                                     │
Tela Servidor  ◄──GET /api/sistema/atualizacao/progresso──  API lê progresso.log + resultado.json
```

- O container tem `/app/.deploy` montado **`:ro`**, com `/app/.deploy/controle` como sub-mount
  **`:rw`** — a API só escreve `pedido.json` ali; todo o resto é leitura.
- **Mudou o `nginx/default.conf` nesta versão? O apply não faz efeito sozinho no nginx.** O passo 5
  não recria o `web` (imagem e compose inalterados), e o conf é bind-mount de arquivo único — o
  `rsync` do passo 4 troca o inode e o container continua vendo o antigo. Depois de um apply que
  mexeu no conf, rodar uma vez no host:
  `docker compose up -d --force-recreate web` (visto na 4.5.10: a CSP nova só apareceu depois
  disto). Arquivos de `web/` (html/js/css) não têm o problema: o bind é de **pasta**, o conteúdo
  novo vale na hora.
- As units são **templates `@`**: a instância é o caminho da instalação escapado
  (`systemd-escape -p /mnt/dados/analyser` → `mnt-dados-analyser`), então vários `analyser` no
  mesmo host (ver `NOME_PROJETO` no `docker-compose.yml`) têm cada um as suas units, sem colisão.
   O `instalar-host.sh` habilita `analyser-update-apply@<instância>.path`,
   `analyser-update-check@<instância>.timer` e `analyser-update-check-req@<instância>.path` (este
   último dispara o mesmo `.service` do timer, via `Unit=` na `[Path]`, ao aparecer o `checar.req`),
   além de `analyser-reiniciar@<instância>.path` (botão "Reiniciar servidor" da tela Desempenho →
   `analyser-update reiniciar`, que faz `docker compose up -d` e espera o `/api/health`).
  **Um `apply` não reinstala as units** — se elas mudarem entre versões, rode `instalar-host.sh`
  de novo no host (o binário `analyser-update` esse sim se auto-reinstala no `apply`).
- `analyser-update-apply@.service` roda como **root** (precisa de `docker compose` e de escrever
  na instalação); o `systemd` já o inicia como root, sem `sudo`. `Environment=ANALYSER_DIR=%I`
  diz ao script qual instalação é.
- A porta do health-check sai do `WEB_PORT` do `.env` da instalação (ex.: `80` em produção,
  `8080` no dev) — o script lê sozinho.
- Falha em qualquer passo: `resultado.json` recebe `{"ok": false, "erro": …}`, o `pedido.json` é
  removido (re-arma o `.path`), e a versão anterior continua no ar — o `docker compose up
  --build` só substitui o container quando a imagem nova termina de construir.
- **Rollback** reextrai o `.tar.gz` de `.deploy/versoes/` (snapshot do estado real de antes,
  incluindo qualquer ajuste local) e sobe de novo. Migração **não** é revertida (forward-only) —
  coluna nova fica no banco; código velho a ignora.

## Modelo de dados

`db/init/40_sistema_migracoes.sql`, schema `SEGURANCA`:

| Coluna | Tipo | Observação |
|---|---|---|
| `NU_MIGRACAO` | `INTEGER` PK | prefixo numérico do arquivo (`0007_…` → `7`) |
| `NO_ARQUIVO` | `VARCHAR(200)` | `0007_pix_ligacao_contato.sql` |
| `CO_HASH` | `CHAR(64)` | sha256 do conteúdo; denuncia arquivo editado após aplicado |
| `DS_ORIGEM` | `VARCHAR(20)` | `bootstrap` (baseline, não executada) \| `runner` (aplicada) \| `manual` |
| `NU_MS_DURACAO` | `INTEGER` | tempo de aplicação; `NULL` para as de bootstrap |
| `NO_APLICADA_POR` | `VARCHAR(100)` | `startup` \| `cli` \| `updater 1.8.0` \| login do admin |
| `DT_APLICACAO` | `TIMESTAMP` | `now()` |

Estado de runtime do atualizador (fora do Git, criado por `instalar-host.sh`):

```
/opt/analyser/.deploy/
├── token                 0600, só o host lê — Contents:Read em HLSDEO/analyser
├── config                (opcional) REPO=…, HEALTH_TIMEOUT=…
├── build-info            {tag, aplicada_em} — escrito a cada apply
├── versoes/              pre-<versao>-<ts>.tar.gz  (snapshots para rollback, guarda os 5 últimos)
└── controle/             sub-mount :rw no container da API
    ├── disponivel.json   {tag, notas, url, verificado_em, malformado, erro}  — do `analyser-update check`
    ├── checar.req        <ts>  — da API ao clicar "Verificar"; o check apaga ao rodar
    ├── pedido.json       {versao, solicitante, ts}         — da API, gatilho do systemd
    ├── progresso.log     linha a linha do apply em curso
    ├── resultado.json    {ok, versao, erro, ts}            — do fim do apply
    ├── reiniciar.req     {solicitante, ts}                 — da API, gatilho do systemd (botão Reiniciar)
    ├── reiniciar.log     linha a linha do `up -d` em curso
    └── reiniciar-resultado.json  {ok, versao, erro, ts}   — do fim do reinício
```

## Configuração

### `.env`

| Variável | Default | Efeito |
|---|---|---|
| `MIGRAR_NO_STARTUP` | `1` | `0` desliga a aplicação automática de migrações no startup da API (deploy que prefere `python /app/tools/migrar.py` na mão) |

### `docker-compose.yml` (serviço `api`, volumes)

```yaml
- ./VERSION:/app/VERSION:ro
- ./db/migracoes:/app/db/migracoes:ro
- ./.deploy:/app/.deploy:ro
- ./.deploy/controle:/app/.deploy/controle:rw
```

### Instalação do agente do host (uma vez por servidor)

```bash
sudo bash scripts/instalar-host.sh /caminho/da/instalacao   # default /opt/analyser
```

(use `bash scripts/...` — o bit de execução pode não sobreviver ao checkout no Windows.)

Confere pré-requisitos (`curl`, `tar`, `flock`, `systemd-escape`, `docker compose`; `rsync` e
`python3` recomendados), cria `.deploy/`, pede o token, instala `/usr/local/bin/analyser-update`
e as units `systemd` (templates `@`, uma instância por caminho), confere `VERSION` × tag do
checkout, **avisa sobre alterações locais não publicadas** e registra os deltas atuais como
baseline (`migrar.py --marcar-baseline`). Depois:

```bash
cd /caminho/da/instalacao && docker compose up -d api-1 api-2   # recria as réplicas p/ pegar os binds do .deploy
```

### Cutover de uma instalação existente

1. **Publique o que estiver pendente no servidor.** O deploy passa a ser a árvore de uma tag;
   qualquer `.py`/`.html`/`.sql` alterado à mão e não commitado é perdido no primeiro `apply`.
   `git -C <instalação> status` e resolva antes.
2. Confirme que `VERSION` (no repositório, na tag) bate com o nome da tag, e que `db/init/`
   reflete o schema real do banco com os deltas correspondentes em `db/migracoes/` (para bancos
   que evoluíram por `ALTER` manual, gere os deltas do que falta).
3. `git fetch --tags && git checkout <tag>` + `docker compose up -d --build` (esta subida ainda
   é manual — o botão só existe a partir da versão que o traz).
4. `sudo bash scripts/instalar-host.sh <instalação>` — registra os deltas atuais como baseline
   (`SIS_TB01_MIGRACOES` recebe uma linha `bootstrap` por delta, **sem executá-los**: o banco já
   os contém).
5. `docker compose up -d api-1 api-2` e confira **Administração → Servidor**.

Depois do primeiro `apply` pela tela, o `apply` reescreve os arquivos via `rsync` e o `.git` da
instalação fica **vestigial** — `analyser-update` realinha o `HEAD` com a tag (só o ref, não o
working tree, para não reverter o `.env`), mas a fonte de verdade da versão passa a ser o arquivo
`VERSION` / `analyser-update status` / a tela Servidor, não `git log`. Não rode `git pull` no
servidor.

## Referências

- Funções: `api/funcoes/versao_utils.py`, `api/funcoes/reiniciar_utils.py`, `api/funcoes/migrar_utils.py`; `api/main.py`
  (`_preparar_banco`); `api/tools/migrar.py` (CLI).
- Rotas: `api/rotas/sistema.py` — `GET /api/sistema/versao` (qualquer aprovado),
  `GET /api/sistema/migracoes`, `GET /api/sistema/atualizacao`,
  `POST /api/sistema/migracoes/aplicar`, `POST /api/sistema/atualizacao/aplicar`,
  `GET /api/sistema/atualizacao/progresso`, `POST /api/sistema/reiniciar`,
  `GET /api/sistema/reiniciar` (admin).
- Host: `scripts/analyser-update`, `scripts/instalar-host.sh`,
  `scripts/systemd/analyser-update-apply@.{service,path}`,
  `scripts/systemd/analyser-update-check@.{service,timer}`,
  `scripts/systemd/analyser-update-check-req@.path`,
  `scripts/systemd/analyser-reiniciar@.{service,path}` (templates `@`).
- Esquema: `db/init/40_sistema_migracoes.sql`; `db/migracoes/README.md`;
  `db/bootstrap/00_aplicar_migracoes.sh` (Fase 2).
- Telas: `web/servidor.html`, `web/js/servidor.js` (cartão "Versão e atualização");
  `web/desempenho.html`, `web/js/desempenho.js` (botão "Reiniciar servidor").
- Relacionados: `infraestrutura.md` (Docker, `.env`, "O que cada mudança exige"),
  `banco_dados.md` (schemas e convenção de nomes de migração), `README.md` da raiz.
