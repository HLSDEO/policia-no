# Instalação de um NÓ do zero (delegacia)

Passo a passo para colocar uma máquina nova da delegacia como **nó descentralizado** do
analyser (`PAPEL=no`): ela guarda os arquivos da própria delegacia em disco local, serve a pasta
pelo Samba e executa a extração perto dos arquivos — o central só conversa com ela pela API
interna `/no/*`. O conceito completo está em [no_descentralizado.md](no_descentralizado.md).

Na referência abaixo troque `policia-smt.pf.gov.br` (hostname do nó), `10.27.7.19` (IP do
central) e `DPF/SMT/ES` (delegacia atendida) pelos valores da sua unidade.

## 0. Pré-requisitos

- Ubuntu 22.04/24.04-server com rede da delegacia (alcança o central e os DCs do domínio
  `PF.GOV.BR`);
- credencial de domínio para o ingresso (o mesmo script aceita `--usuario` sem `--senha` e pede
  mascarado — prefira assim);
- uma conta **administradora** no analyser do central (para gerar o token do nó no passo 3);
- liberar na rede: central → nó na porta **8090** e nó → central na porta do web
  (`80`/`8080`, a mesma que a delegacia usa para abrir o sistema).

## 1. Pacotes de base e Docker

```bash
sudo hostnamectl set-hostname policia-smt.pf.gov.br

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

Não precisa instalar o Samba à mão: o `ingresso_ad.sh` (passo 5) instala `samba`, `winbind`,
`libpam-winbind`, `libnss-winbind`, `krb5-user` e `smbclient` já na configuração certa para o
domínio.

## 2. Código

```bash
git config --global credential.helper store
sudo mkdir -p /mnt/dados/analyser
sudo chown "$USER:$USER" /mnt/dados/analyser
git clone https://github.com/HLSDEO/analyser.git /mnt/dados/analyser
cd /mnt/dados/analyser

# use a tag do release MAIS RECENTE (veia em Releases no GitHub; o atualizador cuida dos próximos)
git fetch --tags && git checkout 4.6.25
```

**Nunca clone no home do usuário** (`~/analyser`): o Samba resolve o `[Dados]`
(`.../samba-analyser/%u`) com `chdir` **já como o UID do usuário logado**, e `/home/<usuário>`
é `750` por padrão — o `smbd` não atravessa o home e **nenhum usuário enxerga a pasta de
rede** (`vfs_ChDir ... Permission denied` no log do Samba), mesmo com a sincronização de
symlinks rodando "ok" a cada 5 min. Visto em produção num nó instalado em
`/home/pc/analyser`. O `instalar-samba.sh` agora recusa esse cenário.

O `git clone` pede usuário e PAT do GitHub (o `credential.helper store` guarda no
`~/.git-credentials` do operador — use uma conta sua, de leitura, e **nunca** embuta o token na
URL do remote nem em documentos).

## 3. Registro no central e `.env` do nó

1. No **central**, tela **Administração → Servidores → Novo nó**: cadastre o nó (nome, delegacia
   atendida) e **copie o token gerado** — ele é o segredo da comunicação nos dois sentidos.
2. Na máquina do nó:

```bash
cp .env.cp .env
nano .env
# preencher OBRIGATÓRIO (ver .env.cp):
#   PAPEL=no
#   CENTRAL_URL=http://10.27.7.19        # IP/porta do WEB do central (a mesma do navegador)
#   NO_TOKEN=<token gerado no passo 1>
#   NO_PORT=8090                          # porta que o central alcança como TX_URL_BASE
#   NOME_PROJETO=analyser
```

## 4. Agente de atualização do host (opcional, recomendado)

```bash
sudo bash scripts/instalar-host.sh
```

Pede um token de **leitura** do GitHub, instala o `analyser-update` + units systemd e faz a 1ª
checagem — depois disto o nó passa a ser atualizado pela própria tela de Servidores do central
([atualizacao.md](atualizacao.md)). Idempotente.

## 5. Ingresso no domínio AD (PF.GOV.BR)

```bash
sudo bash scripts/ingresso_ad.sh --usuario teste@pf.gov.br --hostname policia-smt.pf.gov.br
# a senha é pedida mascarada (read -s); --senha existe, mas na linha de comando fica no ps/history
```

O script garante DNS nos DCs, hostname FQDN + `/etc/hosts`, relógio NTP (o Kerberos não perdoa
>5 min de diferença), escreve `krb5.conf` e configura Samba `security = ADS` + winbind.
Idempotente: para re-conferir depois, `--verificar` (sem usuário/senha).

**Depois do join**: o chefe do AD precisa mover a conta de máquina para a OU da unidade —
autenticação e enumeração já funcionam antes da movida; reconfera com `--verificar` depois.

## 6. Samba por delegacia

```bash
sudo bash scripts/instalar-samba.sh
```

Pré-requisito: passo 5 concluído (`net ads testjoin` = OK). No nó, a lista
usuário↔delegacia vem da API do central (`GET /api/no/samba-usuarios`, com o `NO_TOKEN` do
`.env`): cada usuário de domínio enxerga **somente** a pasta da própria delegacia no
compartilhamento `[Dados]`, que vira `.../samba-analyser/%u` (symlinks, timer a cada 5 min).
O script também corrige o dono do `data/` quando o Docker o criou como `root`. Idempotente.

## 7. Subir o container do nó

```bash
docker compose -f docker-compose.no.yml up -d --build
```

Só a API com as rotas `/no/*` (sem Postgres/Ollama/nginx — ver `docker-compose.no.yml`). O nó
se anuncia ao central sozinho pelo heartbeat (`NO_HEARTBEAT_SEGUNDOS`, padrão 60 s).

## 8. Conferência final

```bash
curl -s http://localhost:8090/no/saude     # papel, versão e fôlego do nó
sudo net ads testjoin                       # OK
ls -ld data/                                # dono = usuário do container (não root)
```

1. No central, tela Administração → Servidores: **Testar** → ponto verde, mesma versão;
2. marque o nó **ativo** e, na tela Delegacias, ligue **"Usa o Nó"** na delegacia atendida
   (sem a flag o central finge que o nó não existe);
3. importação real de um `.ufdr` pequeno da delegacia e olhar a extração rodando no nó.

## O que os arquivos passam a fazer no nó

Com o nó ativo para a delegacia, **tudo** fica na estação: uploads do navegador (`.ufdr`,
documento, pasta do RIF, R304 do SIMBA) são repassados em streaming para o `data/` do nó, a
cópia SMB ("pasta de rede") sobe direto para a estação, extração/parse/organize rodam lá, e o
central fica só com o que o banco precisa (nenhuma cópia da prova). Detalhes e exceções em
[no_descentralizado.md](no_descentralizado.md).
