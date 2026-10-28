# Analyser — NÓ de delegacia (PAPEL=no)

Este repositório é o **nó** do esquema descentralizado do analyser: o container que roda no
**servidor da delegacia**, junto da prova (`data/`). Não tem Postgres, web, sessão nem IA de
relatório — só a CPU da estação, a pasta `data/` local e as rotas internas `/no/*` que o
**central** chama (por HTTP direto ou pelo túnel NAT). O central fica com o que o banco
precisa; a prova nunca sai da delegacia.

Servidor do nó = **Ubuntu server 22.04/24.04 com Docker** — é o cenário único deste guia.

## O que roda no nó

Um único serviço de aplicação (`no`, imagem `policia-api-no`, build a partir de `./api`) e,
opcional mas recomendado, o `no-web` (HTTPS 8443 para a **mídia direta** ao navegador na LAN):

| Serviço | O que é | Porta |
|---|---|---|
| `no` | API FastAPI com as rotas `/no/*` + heartbeat/túnel/jobs de fundo | `NO_PORT` (8090) → 3000 |
| `no-web` | nginx que termina TLS e libera só `/no/saude` + `/no/midia?tk=` | 8443 → 443 |

A imagem é `python:3.12-slim` com Tesseract(+pt-BR), JRE (IpedDumper) e ffmpeg: OCR,
classificação CLIP, comparação facial, transcrição (faster-whisper), parse de RIF/SIMBA/IPED e
extração `.ufdr` rodam **na CPU local**, sobre os arquivos deste disco.

Rotas (`api/rotas/no_servico.py`, todas com `Authorization: Bearer <NO_TOKEN>` salvo `/no/saude`):

- `/no/saude` — sonda pública (papel + versão);
- `/no/listar`, `/no/arquivo`, `/no/hash`, `/no/parse` — a pasta `data/` local a serviço do central;
- `/no/extrair` — extração `.ufdr` na CPU do nó (dados voltam pelo canal de lotes);
- `/no/transcrever`, `/no/embed` — whisper e embeddings locais;
- `/no/importar` — parse de RIF/SIMBA/IPED na estação (mesmo canal da extração);
- `/no/iped-blob`, `/no/iped-thumb` — binários do caso IPED (o `storage-N.db` é local e nunca é copiado);
- `/no/midia` — download direto ao navegador com ticket de ~10 min do central;
- `/no/organizar`, `/no/expandir-zip`, `/no/enviar-zip`, `/no/enviar-progresso`, `/no/atualizar`, `/no/config`, `/no/reiniciar`, `/no/estado` — apoio ao ciclo de vida/organização.

---

## Instalação numa delegacia (passo a passo)

Troque `policia-smt.pf.gov.br` (hostname do nó), `10.27.7.19` (IP do central) e `DPF/SMT/ES`
(delegacia atendida) pelos valores da sua unidade. **Tudo abaixo roda no HOST da delegacia.**

### 0. Pré-requisitos

- Ubuntu server com rede da delegacia: alcança o **central** e os **DCs** do domínio `PF.GOV.BR`;
- credencial de domínio para o ingresso AD (o script pede a senha mascarada);
- uma conta **administradora** no central (para gerar o token do nó, passo 3);
- portas liberadas na rede: **central → nó** na `8090` (e `8443` se for usar mídia direta) e
  **nó → central** na porta web do central (`80`/`8080`, a mesma do navegador);
- token de **leitura** do GitHub para o atualizador de host (passo 4 — opcional, recomendado).

### 1. Pacotes de base e Docker

```bash
sudo hostnamectl set-hostname policia-smt.pf.gov.br

sudo apt update
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common gnupg git

# dependências iniciais do Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# repositório oficial do Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Não instale o Samba à mão: o `ingresso_ad.sh` (passo 5) instala `samba`, `winbind`,
`libpam-winbind`, `libnss-winbind`, `krb5-user` e `smbclient` já na configuração certa para o
domínio.

### 2. Código

```bash
git config --global credential.helper store
sudo mkdir -p /mnt/dados/analyser
sudo chown "$USER:$USER" /mnt/dados/analyser
git clone <URL deste repositório> /mnt/dados/analyser
cd /mnt/dados/analyser
git fetch --tags && git checkout <tag do release mais recente>
```

O `git clone` pede usuário e PAT de **leitura** do GitHub (o `credential.helper store` guarda no
`~/.git-credentials` do operador — nunca embuta token na URL do remote nem em documentos).

> **Nunca clone no home do usuário** (`~/analyser`). O Samba resolve o compartilhamento
> `\\no\Dados` com `chdir` já como o UID do usuário logado, e `/home/<usuário>` é `750` — o
> `smbd` não atravessa o home e ninguém enxerga a pasta de rede. O `instalar-samba.sh` recusa
> esse cenário. O caminho padrão é `/mnt/dados/analyser`.

### 3. Registro no central e `.env` do nó

1. No **central**, tela **Administração → Servidores → Novo nó**: cadastre (nome, delegacia
   atendida) e **copie o token gerado** — ele é o segredo da comunicação nos dois sentidos.
2. Na máquina do nó:

```bash
cp .env.no.example .env
nano .env
```

| Variável | O que é |
|---|---|
| `PAPEL=no` | liga o modo nó (o compose do nó já fixa; a linha vale para rodar sem Docker) |
| `CENTRAL_URL` | endereço WEB do central (`http://10.27.7.19` — o mesmo que o navegador usa) |
| `NO_TOKEN` | o token copiado no passo 1 — única credencial do nó |
| `NO_PORT` | `8090` — a porta que o central alcança como `TX_URL_BASE` |
| `NO_HEARTBEAT_SEGUNDOS` | `60` — o nó se anuncia sozinho; é o "ponto verde" |
| `TUNEL_NO_ATIVO` | `1` — abre o WebSocket NAT ao central (útil atrás de NAT estrito); `0` desliga |
| `JOBS_NO_LIMITE` | `1` — jobs de OCR/CLIP/facial simultâneos que esta CPU puxa do central |
| `NO_MIDIA_PORT` / `NO_MIDIA_ORIGIN` | mídia direta: `8443` e a URL HTTPS do central (CORS) |

O `.env` é lido **por dentro do container** (montado) — as rotas `/no/config` e `/no/estado`
leem e ajustam este mesmo arquivo.

### 4. Agente de atualização do host (opcional, recomendado)

```bash
sudo bash scripts/instalar-host.sh
```

Roda **uma vez por servidor**, idempotente, e detecta sozinho que é um nó (`.env` com
`PAPEL=no` / `docker-compose.no.yml`): confere pré-requisitos (curl, tar, flock, systemd-escape,
docker compose), cria `.deploy/` (`token`, `controle/`, `versoes/`), instala
`/usr/local/bin/analyser-update` + as units systemd (`@`, uma instância por caminho de
instalação), pula o baseline de migrações (nó não tem banco) e roda a 1ª checagem. Depois
disto o nó passa a ser atualizado pela própria tela de Servidores do central. Ver **Scripts**.

### 5. Ingresso no domínio AD (PF.GOV.BR)

```bash
sudo bash scripts/ingresso_ad.sh --usuario seu.usuario@pf.gov.br --hostname policia-smt.pf.gov.br
```

A senha é pedida mascarada. O script garante DNS nos DCs, hostname FQDN + `/etc/hosts`, relógio
NTP (Kerberos não perdoa >5 min de diferença), escreve `krb5.conf` e configura Samba
`security = ADS` + winbind. Idempotente — para reconferir depois, `--verificar` (sem senha).
**Depois do join**, o chefe do AD precisa mover a conta de máquina para a OU da unidade;
reconfera com `--verificar`.

### 6. Samba por delegacia (a "pasta de rede")

```bash
sudo bash scripts/instalar-samba.sh
```

Pré-requisito: passo 5 concluído (`sudo net ads testjoin` = OK). No nó, a lista
usuário↔delegacia vem da **API do central** (`GET /api/no/samba-usuarios`, autenticada com o
`NO_TOKEN`): cada usuário de domínio enxerga **somente** a pasta da própria delegacia no
compartilhamento `[Dados]`, resolvido por symlink
`/mnt/dados/samba-analyser/<login> → <instalação>/data/<DELEGACIA>`, refeito por um timer a
cada 5 min (`analyser-sincronizar-samba.timer`). Usuário sem vínculo fica **sem acesso**. O
script também corrige o dono do `data/` quando o Docker o criou como `root`.

Delegacia sem AD: o central continua enviando uploads/streaming ao nó normalmente — só não há
a pasta de rede SMB.

### 7. Subir o container do nó

```bash
cd /mnt/dados/analyser
docker compose -f docker-compose.no.yml up -d --build
```

O primeiro build é gordo (torch + faster-whisper + transformers; alguns GB de download). A
subida monta `./data` (a ÚNICA cópia da prova da delegacia), `./logs`, `./.env`, o volume
nomeado `hf_cache` (modelos do Hugging Face — estações sem saída para o HF devem pré-semear
este volume com `docker cp`) e `./nginx/ssl` (certificado da mídia direta; sem cert, o `no-web`
fica parado avisando e o resto do nó funciona igual).

O nó se anuncia ao central sozinho pelo heartbeat em ≤60 s.

### 8. Conferência final

```bash
curl -s http://localhost:8090/no/saude   # {"ok":true,"papel":"no","versao":...}
sudo net ads testjoin                    # OK
ls -ld data/                             # dono = usuário do container (não root)
```

1. No central, Administração → Servidores: **Testar** → ponto verde, mesma versão;
2. marque o nó **ativo** e, na tela Delegacias, ligue **"Usa o Nó"** na delegacia atendida
   (sem a flag o central finge que o nó não existe);
3. teste real: envie um `.ufdr` pequeno da delegacia e veja a extração rodando NO NÓ
   (os dados chegam ao central pelo canal de lotes, com retomada automática de rede).

Com o nó ativo, **tudo** fica na estação: uploads do navegador (`.ufdr`, documento, pasta RIF,
R304 SIMBA) são repassados em streaming para o `data/` do nó, a cópia SMB sobe direto para a
estação, e extração/parse/organize rodam lá — o central não guarda cópia da prova.

---

## Scripts (o que roda no HOST do nó)

Os scripts em `scripts/` rodam **no host**, nunca dentro do container — a API dentro do
container não alcança o `docker compose` nem o systemd; a comunicação é por arquivos em
`.deploy/controle/` (pasta montada rw para o container).

### Instalação (descritos acima)

| Script | Quando | O que faz |
|---|---|---|
| `instalar-host.sh [dir]` | passo 4, uma vez | prepara o servidor para o atualizador: `.deploy/`, `/usr/local/bin/analyser-update`, units systemd, 1ª checagem. Auto-detecta o nó. Idempotente |
| `ingresso_ad.sh --usuario …` | passo 5, uma vez | ingresso no domínio PF.GOV.BR (krb5 + samba ADS + winbind + NTP + DNS). Reconferência: `--verificar` |
| `instalar-samba.sh` | passo 6, uma vez | compartilhamento `[Dados]` por usuário via symlinks + timer de sincronização; corrige dono do `data/`. Idempotente |

### Atualização e reinício remotos (instalados pelo `instalar-host.sh`)

`analyser-update` (vai para `/usr/local/bin`) é o agente; o systemd só o dispara:

| Unit (template `@<instância>`) | Gatilho | Ação |
|---|---|---|
| `analyser-update-check@.timer` | a cada 15 min | consulta o GitHub, grava `.deploy/controle/disponivel.json` (a tela do central mostra a versão disponível) |
| `analyser-update-check-req@.path` | botão **Verificar** da tela | checagem na hora |
| `analyser-update-apply@.path` | botão **Atualizar** (grava `pedido.json`) | baixa a tag, faz snapshot em `.deploy/versoes/`, aplica com `--build` e `up -d`; `analyser-update rollback` desfaz |
| `analyser-reiniciar@.path` | botão **Reiniciar servidor** | `docker compose -f docker-compose.no.yml up -d` |

O token do GitHub fica em `.deploy/token` (0600) — só o host o lê; a API apenas grava pedidos.
Uso manual: `analyser-update status | check | apply [--versao X] | rollback | reiniciar`.

### Samba

| Script/unit | O que faz |
|---|---|
| `sincronizar-samba.sh` + `analyser-sincronizar-samba.{service,timer}` | a cada 5 min, pergunta ao central (`/api/no/samba-usuarios`) o vínculo usuário↔delegacia e refaz os symlinks de `/mnt/dados/samba-analyser/` |

### Apoio manual (raro)

| Script | Uso |
|---|---|
| `extrair_zip_para_data.py` | expande um ZIP de prova GB-sized direto em `data/<DELEGACIA>/` (contorno para uploads maiores que o corte do nginx), com as mesmas regras de segurança do app (sem zip slip, sem `__MACOSX`) |
| `observar-reinicio.ps1` | EQUIVALENTE DE DEV (Windows/Docker Desktop, sem systemd): vigia `reiniciar.req` e roda `compose up -d`. No servidor Ubuntu com `instalar-host.sh` não serve para nada |

---

## Layout

```
api/                  código do nó (main.py = só /no/* + laços de fundo)
api/Dockerfile        o ÚNICO Dockerfile (build da imagem do nó)
docker-compose.no.yml sobe no + no-web
.env.no.example       modelo do .env (copiar para .env)
nginx/                config + entrypoint do no-web (mídia direta); ssl/ fica no servidor
scripts/              os scripts de host acima
VERSION               a versão instalada (o atualizador compara com a tag do GitHub)
```

Os utilitários em `api/funcoes/` são compartilhados com o central (os MESMOS parsers e
gravadores — o que o nó produz é byte a byte o que o central produziria). O papel do central
(banco, web, rotas de usuário, OpenShift) não existe neste repositório.
