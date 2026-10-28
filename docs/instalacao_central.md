# Instalação do Servidor Principal (Central)

Passo a passo para instalar o **central** do analyser do zero: é a máquina que guarda o banco
(Postgres), a interface web (nginx), a IA, a busca semântica (Ollama) e o registry de imagens que
alimenta os **nós** das delegacias. O conceito dos nós está em
[no_descentralizado.md](no_descentralizado.md) e a instalação de um nó em
[instalacao_no.md](instalacao_no.md).

Na referência abaixo troque `10.27.7.19` pelo IP da máquina e os nomes de exemplo pelos da sua
unidade.

## 0. Pré-requisitos

- Ubuntu 22.04/24.04-server, boa RAM (Postgres ajustável + OCR/transcrição rodam na API) e
  disco grande em `data/` (as provas);
- saída para Docker Hub e GitHub na primeira subida (build das imagens); depois, atualizações
  pelo `analyser-update` (token de **leitura** do GitHub);
- para login via API-PF (credencial única da PF): a conta de serviço e o segredo do provedor
  (já há um padrão em `.env.cp`; confirme o vigente com a administração);
- portas para liberar na rede: `WEB_PORT` (80 — a URL do sistema), `443`/`WEB_SSL_PORT` (se TLS)
  e **5000** (registry local, alcançável pelos nós). Postgres e API **não** publicam porta.

## 1. Pacotes de base e Docker

```bash
sudo hostnamectl set-hostname policia-central.pf.gov.br

sudo apt update
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common gnupg git

# ---- Instalar dependências iniciais do Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# ---- Adicionar o repositório oficial do Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# ---- Instalar docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2. Registry local para os nós

O nó normalmente não consegue `docker build` (sem saída para a Internet): o central builda a
imagem do nó e faz push no registry local (`docker-compose.yml`, serviço `registry`, porta
**5000**). É HTTP puro na LAN, então o **host do central** precisa declarar o registry como
inseguro (no nó o `analyser-update` já configura sozinho):

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{ "insecure-registries": ["10.27.7.19:5000"] }
EOF
sudo systemctl restart docker
```

Sem nós cadastrados o registry sobe e ninguém usa — pode deixar.

## 3. Código

```bash
git config --global credential.helper store
git clone https://github.com/HLSDEO/analyser.git
cd analyser

# use a tag do release MAIS RECENTE (veja em Releases no GitHub; o atualizador cuida dos próximos)
git fetch --tags && git checkout 4.6.25
```

O `git clone` pede usuário e PAT do GitHub (de **leitura**; o `credential.helper store` guarda em
`~/.git-credentials`). Nunca embuta token em URL remota nem em documento.

## 4. `.env`

```bash
cp .env.cp .env
nano .env
# obrigatório no central:
#   NOME_PROJETO=analyser
#   POSTGRES_PASSWORD=<senha forte!>        # o banco não publica porta, mas senha fraca é convite
#   WEB_PORT=80                             # a URL que a delegacia abre (8080 p/ teste ao lado de outro)
# opcionais (dá para preencher depois pela tela Configurações):
#   AI_PROVIDER/AI_MODEL/AI_API_URL/AI_API_KEY      # IA (chat, relatórios)
#   EMBED_MODE=local                                # busca semântica offline (usa o container ollama)
#   SMTP_* + APP_URL                                # e-mail de recuperação de senha
#   REGISTRY_PORT=5000                              # registry dos nós (padrão)
```

⚠️ O `.env` está no `.gitignore` mas **já foi comitado no passado** e continua rastreado: nunca
coloque segredo novo que possa ser publicado — veja a nota "Dados fora do Git" em
[infraestrutura.md](infraestrutura.md).

No central **não** se define `PAPEL`/`CENTRAL_URL`/`NO_TOKEN` (PAPEL ausente = central).

## 5. Primeira subida

```bash
docker compose up -d --build
```

Sobe `postgres` (roda os `db/init/*.sql` na primeira subida — cria os schemas), `ollama`,
`api` (FastAPI, roda as migrações de `db/migracoes/` na subida) e `web` (nginx). Conferência:

```bash
docker compose ps                      # postgres e api "healthy", web up
curl -s http://localhost/api/health    # ou a WEB_PORT escolhida
```

Duas armadilhas de dono de pasta (o Docker cria como `root` o que não existir):

```bash
sudo chown 1000:1000 logs/ data/       # a API roda como usuário comum e precisa escrever
```

## 6. Primeiro acesso e cadastro

1. Abra `http://<IP>` — login inicial **`admin` / `admin1234`** (troque a senha na hora);
2. tela **Administração → Usuários/Delegacias**: cadastre (ou aprove autocadastros com login
   API-PF) os investigadores e vincule cada um à delegacia — o vínculo usuário↔delegacia é o que
   o Samba usa no passo 8 e o que isola os inquéritos;
3. **Administração → Configurações**: IA, embeddings, OCR, transcrição (ou deixe o `.env`);
4. **Administração → Desempenho**: ajusta Postgres/`API_WORKERS`/workers à máquina real
   (recria containers no `up -d` seguinte);
5. opcional **Ingestão**: bases de referência (`db/data/`, ver [ingestao.md](ingestao.md)).

## 7. Agente de atualização do host

```bash
sudo bash scripts/instalar-host.sh
```

Pede o token de leitura do GitHub, instala `analyser-update` + units systemd. É por ele que a
tela de Servidores atualiza o central (bump de release → build → `apply`) **e publica a imagem
dos nós no registry** ([atualizacao.md](atualizacao.md)). Idempotente.

## 8. AD + Samba por delegacia (opcional, recomendado)

Se a delegacia copia arquivos pela pasta de rede `\servidor\Dados` (em vez de — ou além de —
upload pelo navegador):

```bash
sudo bash scripts/ingresso_ad.sh --usuario xpto@pf.gov.br --hostname policia-central.pf.gov.br
sudo bash scripts/instalar-samba.sh
```

`ingresso_ad.sh`: join no domínio `PF.GOV.BR` (ADS + winbind, DNS nos DCs, NTP; senha mascarada;
reconferir com `--verificar`). A conta de máquina vai para a OU da unidade pelo chefe do AD.
`instalar-samba.sh` no **central** lê o vínculo usuário↔delegacia direto do Postgres
(`seguranca.usr_tb01_usuarios → delegacias`) e monta os symlinks por usuário em
`/mnt/dados/samba-analyser/%u` — cada usuário enxerga só a pasta da própria delegacia
(timer de 5 min). Idempotente, pré-requisito: `net ads testjoin` = OK.

## 9. Nós das delegacias

Com o central no ar: **Administração → Servidores → Novo nó** para cada delegacia — o registro
mostra o `NO_TOKEN` que vai no `.env` do nó ([instalacao_no.md](instalacao_no.md)). Ative o nó e
a flag **"Usa o Nó"** na delegacia; "Testar" = ponto verde.

## 10. Conferência final

- `docker compose ps` tudo healthy e `docker compose logs --tail api` limpo;
- tela de Servidores: versão instalada = tag do checkout, agente do host registrado;
- uma importação real pequena (documento + `.ufdr`) fechando `concluido`;
- backup: volume `postgres_data` (`docker`), `data/`, `db/data/` e `.env` fora do Git.
