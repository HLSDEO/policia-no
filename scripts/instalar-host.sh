#!/usr/bin/env bash
#
# instalar-host.sh — prepara um servidor para o atualizador do analyser. Roda UMA vez por
# instalação, como root. Idempotente: rodar de novo só reconfere e recarrega.
#
#   sudo bash scripts/instalar-host.sh [/caminho/da/instalacao]
#   cd /mnt/dados/analyser && sudo bash scripts/instalar-host.sh   (auto-detecta CWD e PAPEL=no)
#
# (use `bash scripts/...` se o bit de execução não tiver sobrevivido ao checkout)
#
# O que faz:
#   1. confere pré-requisitos (curl, tar, flock, systemd-escape, docker compose; rsync/python3
#      recomendados)
#   2. cria .deploy/ (token, controle/, versoes/) na instalação
#   3. pede o token de leitura do GitHub e grava em .deploy/token com permissão 0600
#   4. instala /usr/local/bin/analyser-update e as units systemd — templates `@`, uma instância
#      por caminho de instalação, então vários analyser no mesmo host não colidem
#   5. cutover: confere VERSION x tag, avisa sobre alterações locais, registra as migrações
#      atuais como baseline (sem executá-las)
#   6. roda a 1ª checagem
#
# Ver docs/atualizacao.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# INSTALL_DIR: arg > $ANALYSER_DIR > auto-detecção. A auto-detecção existe porque o central
# mora em /opt/analyser ou /mnt/dados/analyser e o NÓ pode estar em qualquer pasta — sem ela,
# `sudo bash scripts/instalar-host.sh` dentro da pasta do nó tentava preparar /opt/analyser e
# morria no check do compose. Instalar o NÓ sob o home (~/$HOME/analyser) é desencorajado:
# /home/<usuario> e 750 e o smbd nao atravessa — ninguem acessa a pasta de rede no no
# (ver docs/instalacao_no.md, passo 2). ~/analyser continua na lista so para nao quebrar
# instalacoes antigas.
if [ -n "${1:-}" ]; then
	INSTALL_DIR="$1"
elif [ -n "${ANALYSER_DIR:-}" ]; then
	INSTALL_DIR="$ANALYSER_DIR"
else
	# Se o CWD é uma instalação (tem compose + scripts/), usa ele — cobre o
	# `sudo bash scripts/instalar-host.sh` sem argumento tanto no central quanto no nó.
	# (sudo preserva o CWD, então $PWD aqui ainda é a pasta clonada do usuário.)
	if [ -f "$PWD/docker-compose.yml" ] || [ -f "$PWD/docker-compose.no.yml" ]; then
		INSTALL_DIR="$PWD"
	else
		for cand in /mnt/dados/analyser /opt/analyser "$HOME/analyser" ~/analyser; do
			if [ -f "$cand/docker-compose.yml" ] || [ -f "$cand/docker-compose.no.yml" ]; then
				INSTALL_DIR="$cand"
				break
			fi
		done
		INSTALL_DIR="${INSTALL_DIR:-/opt/analyser}"
	fi
fi
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd || echo "$INSTALL_DIR")"
DEPLOY_DIR="$INSTALL_DIR/.deploy"
REPO="${ANALYSER_REPO:-HLSDEO/analyser}"

# Papel da instalação (central x nó): o .env manda (PAPEL=no), com fallback para o compose
# presente. O nó usa docker-compose.no.yml, não tem Postgres e o health é /no/saude —
# as etapas 5 (baseline de migrações) e 6 (health) se adaptam abaixo.
PAPEL="central"
if grep -qE '^[[:space:]]*PAPEL[[:space:]]*=[[:space:]]*no([[:space:]]|$)' "$INSTALL_DIR/.env" 2>/dev/null; then
	PAPEL="no"
elif [ ! -f "$INSTALL_DIR/docker-compose.yml" ] && [ -f "$INSTALL_DIR/docker-compose.no.yml" ]; then
	PAPEL="no"
fi
COMPOSE_FILE="docker-compose.yml"
[ "$PAPEL" = "no" ] && COMPOSE_FILE="docker-compose.no.yml"

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { vermelho "rode como root (sudo)."; exit 1; }
if [ ! -f "$INSTALL_DIR/docker-compose.yml" ] && [ ! -f "$INSTALL_DIR/docker-compose.no.yml" ]; then
	vermelho "nao parece uma instalacao do analyser: nem docker-compose.yml nem docker-compose.no.yml em $INSTALL_DIR"
	vermelho "dica (no): sudo ANALYSER_DIR=/mnt/dados/analyser bash /mnt/dados/analyser/scripts/instalar-host.sh"
	vermelho "   ou: cd /mnt/dados/analyser && sudo bash scripts/instalar-host.sh"
	exit 1
fi
echo "instalacao: $INSTALL_DIR"
echo "papel: $PAPEL   (compose: $COMPOSE_FILE)"
# Instalacao sob home de usuario quebra o SMB do no: /home/<usuario> (750) barra o chdir do
# smbd no caminho data/<DELEGACIA> e NENHUM usuario do dominio ve a pasta de rede — a
# sincronizacao de symlinks segue "ok" em silencio (caso real: no em /home/pc/analyser).
case "$INSTALL_DIR" in
	/home/*)
		amarelo "ATENCAO: instalacao sob home de usuario ($INSTALL_DIR)."
		amarelo "   /home/<usuario> costuma ser 750 e o smbd nao atravessa: ninguem acessa a pasta"
		amarelo "   de rede neste no. Prefira /mnt/dados/analyser (ver docs/instalacao_no.md, passo 2)."
		;;
esac

# ------------------------------------------------------------------------------------------------
echo
echo "== 1. pre-requisitos =="
faltando=""
for bin in curl tar flock systemd-escape systemctl; do
	command -v "$bin" >/dev/null 2>&1 || faltando="$faltando $bin"
done
docker compose version >/dev/null 2>&1 || faltando="$faltando docker-compose-plugin"
[ -n "$faltando" ] && { vermelho "faltam:$faltando"; exit 1; }
command -v rsync   >/dev/null 2>&1 || amarelo "rsync ausente — apply usara 'cp -a' (nao remove arquivo que saiu do repo)"
command -v python3 >/dev/null 2>&1 || amarelo "python3 ausente — notas de release e JSON usarao parser simples"
verde "ok"

# ------------------------------------------------------------------------------------------------
echo
echo "== 2. diretorio .deploy =="
mkdir -p "$DEPLOY_DIR/controle" "$DEPLOY_DIR/versoes"
# 751 (nao 750): o container da API (uid 1000, appuser) precisa ATRAVESSAR .deploy para chegar
# em controle/. Sem o 'x' para outros, um bind mount :rw de controle/ nao adianta — o kernel
# barra no diretorio pai. 751 = atravessa mas nao lista; o token continua protegido pelo 0600
# do proprio arquivo.
chmod 751 "$DEPLOY_DIR"
# controle/ precisa ser gravavel pelo container (escreve pedido.json). 777 e o caminho simples e
# o conteudo nao e sensivel (pedido/progresso/estado). Ajuste para 775 + chown 1000 se preferir.
chmod 777 "$DEPLOY_DIR/controle"
chmod 644 "$DEPLOY_DIR"/controle/*.json 2>/dev/null || true
# Cache dos indices .ufdr dos no (bind mount da API, docker-compose.yml): se a pasta nao existir
# o Docker a cria como root e a API (uid 1000) nao escreve - ai o indice cai no /tmp e morre a
# cada deploy (link fino rebaixado de novo a cada upgrade). Crie ja gravavel.
mkdir -p "$INSTALL_DIR/.cache/ufdr-indice"
chown -R 1000:1000 "$INSTALL_DIR/.cache" 2>/dev/null || chmod 777 "$INSTALL_DIR/.cache" "$INSTALL_DIR/.cache/ufdr-indice"
verde "criado: $DEPLOY_DIR"

# ------------------------------------------------------------------------------------------------
echo
echo "== 3. token do GitHub =="
if [ -s "$DEPLOY_DIR/token" ]; then
	verde "token ja presente — mantido"
else
	echo "Cole um Personal Access Token FINE-GRAINED com 'Contents: Read-only' em $REPO."
	echo "Onde criar: github.com/settings/personal-access-tokens/new"
	echo "(e nas configuracoes da SUA CONTA, nao do repositorio)"
	echo "Ele fica so aqui, 0600, e NUNCA entra no container nem no .env."
	read -rsp "token: " TOKEN; echo
	[ -n "$TOKEN" ] || { vermelho "vazio — abortando"; exit 1; }
	printf '%s' "$TOKEN" > "$DEPLOY_DIR/token"
	chmod 600 "$DEPLOY_DIR/token"
	verde "gravado em $DEPLOY_DIR/token"
fi
if [ "$REPO" != "HLSDEO/analyser" ]; then
	printf 'REPO=%s\n' "$REPO" > "$DEPLOY_DIR/config"
	verde "REPO=$REPO gravado em .deploy/config"
fi

echo "   testando o token..."
if curl -fsSL -H "Authorization: Bearer $(tr -d '[:space:]' < "$DEPLOY_DIR/token")" \
	-H "Accept: application/vnd.github+json" \
	"https://api.github.com/repos/$REPO/releases/latest" >/dev/null 2>&1; then
	verde "   token OK (releases de $REPO acessiveis)"
else
	vermelho "   token NAO acessou os releases de $REPO — confira permissao/expiracao"
fi

# ------------------------------------------------------------------------------------------------
echo
echo "== 4. binario + systemd =="
install -m 0755 "$SCRIPT_DIR/analyser-update" /usr/local/bin/analyser-update
verde "instalado: /usr/local/bin/analyser-update"

# Units sao templates (@): a instancia e o caminho da instalacao escapado por systemd-escape -p,
# e %I dentro da unit desescapa de volta. Assim mais de uma instalacao no mesmo host (ver
# NOME_PROJETO no docker-compose.yml) tem cada uma a sua unit, sem colisao de nome.
INSTANCIA="$(systemd-escape -p "$INSTALL_DIR")"
for unit in analyser-update-apply@.service analyser-update-apply@.path \
            analyser-update-check@.service analyser-update-check@.timer \
            analyser-update-check-req@.path \
            analyser-reiniciar@.service analyser-reiniciar@.path; do
	install -m 0644 "$SCRIPT_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl reset-failed "analyser-update-apply@$INSTANCIA.path" "analyser-update-check@$INSTANCIA.timer" \
	"analyser-update-check-req@$INSTANCIA.path" 2>/dev/null || true
systemctl enable --now "analyser-update-apply@$INSTANCIA.path"
systemctl enable --now "analyser-update-check@$INSTANCIA.timer"
systemctl enable --now "analyser-update-check-req@$INSTANCIA.path"   # botao "Verificar" -> check na hora
systemctl enable --now "analyser-reiniciar@$INSTANCIA.path"          # botao "Reiniciar servidor" -> up -d
for p in "analyser-update-apply@$INSTANCIA.path" "analyser-update-check-req@$INSTANCIA.path" \
         "analyser-reiniciar@$INSTANCIA.path"; do
	systemctl is-active --quiet "$p" || vermelho "!! a unit $p nao ficou ativa — 'systemctl status $p'"
done
verde "units ativas (instancia: $INSTANCIA)"
echo "   ver:   systemctl status analyser-update-apply@$INSTANCIA.path"
echo "   log:   journalctl -u analyser-update-apply@$INSTANCIA.service -f"

# marcador que faz a API mostrar o botao "Atualizar" (so existe onde ha agente de host)
printf 'instancia=%s\natualizado=%s\n' "$INSTANCIA" "$(date '+%Y-%m-%dT%H:%M:%S%z')" > "$DEPLOY_DIR/agente"

# ------------------------------------------------------------------------------------------------
echo
echo "== 5. cutover =="

TAG=""
if git -C "$INSTALL_DIR" rev-parse --git-dir >/dev/null 2>&1; then
	TAG="$(git -C "$INSTALL_DIR" describe --tags --exact-match 2>/dev/null || true)"
	TAG="${TAG#v}"
fi

if [ ! -f "$INSTALL_DIR/VERSION" ]; then
	v="${TAG:-$(git -C "$INSTALL_DIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || true)}"
	v="${v:-0.0.0}"
	amarelo "sem arquivo VERSION — criando com '$v' (ajuste a mao se nao for a versao real no ar)"
	printf '%s\n' "$v" > "$INSTALL_DIR/VERSION"
fi
VERSAO="$(tr -d '[:space:]' < "$INSTALL_DIR/VERSION")"
echo "VERSION: $VERSAO${TAG:+   (checkout na tag $TAG)}"

if [ -n "$TAG" ] && [ "$TAG" != "$VERSAO" ]; then
	vermelho "!! O checkout esta na tag $TAG mas o VERSION diz $VERSAO."
	vermelho "   O 'apply' recusa uma tag cujo VERSION nao bate com o nome dela."
	vermelho "   Corrija no repositorio (bump do VERSION + nova tag) antes de atualizar pela tela."
fi

if git -C "$INSTALL_DIR" rev-parse --git-dir >/dev/null 2>&1; then
	sujos="$(git -C "$INSTALL_DIR" status --porcelain | grep -vE '(^| )(\.env|data/|db/data/|\.deploy/)' || true)"
	if [ -n "$sujos" ]; then
		vermelho "!! ESTA INSTALACAO TEM ALTERACOES LOCAIS NAO PUBLICADAS:"
		echo "$sujos" | sed 's/^/     /'
		vermelho "   O primeiro 'apply' vai SOBRESCREVER esses arquivos pelo conteudo da release."
		vermelho "   Commite/publique o que precisar guardar ANTES de atualizar pela tela."
	else
		verde "checkout limpo — nada local para perder"
	fi
	amarelo "   Recomendado apos o cutover: 'rm -rf $INSTALL_DIR/.git'. O deploy deixa de ser um"
	amarelo "   checkout git (o updater usa rsync sobre a arvore de uma tag). Enquanto o .git ficar,"
	amarelo "   'git status'/'git describe' no servidor mostram a versao ANTERIOR e enganam."
fi

# Registra os deltas de db/migracoes/ existentes como ja aplicados, SEM executa-los: este banco
# foi mantido a mao ate aqui e ja contem o efeito deles. Numa instalacao nova o bootstrap faz o
# equivalente (Fase 2). Idempotente — ON CONFLICT DO NOTHING.
# No NÓ não há banco nem Postgres: pula sem erro.
if [ "$PAPEL" = "no" ]; then
	echo "   papel=no — sem banco local, baseline de migracoes nao se aplica (pulado)"
elif [ -d "$INSTALL_DIR/db/migracoes" ] && ls "$INSTALL_DIR/db/migracoes"/*.sql >/dev/null 2>&1; then
	echo "   registrando migracoes atuais como baseline (sem executar)..."
	if ( cd "$INSTALL_DIR" && docker compose exec -T api-1 python /app/tools/migrar.py --marcar-baseline ); then
		verde "   baseline registrado"
	else
		amarelo "   nao foi possivel registrar agora (API no ar?). Rode depois:"
		echo "      cd $INSTALL_DIR && docker compose exec -T api-1 python /app/tools/migrar.py --marcar-baseline"
	fi
else
	echo "   sem deltas em db/migracoes/ — nada a registrar"
fi

# ------------------------------------------------------------------------------------------------
echo
echo "== 6. primeira checagem =="
ANALYSER_DIR="$INSTALL_DIR" /usr/local/bin/analyser-update check || amarelo "check falhou agora — o timer tenta de novo; confira o token"
echo
ANALYSER_DIR="$INSTALL_DIR" /usr/local/bin/analyser-update status
echo
if [ "$PAPEL" = "no" ]; then
	verde "pronto. Recrie o container do no uma vez:"
	echo "   cd $INSTALL_DIR && docker compose -f $COMPOSE_FILE up -d"
	echo "Depois: no central, Administracao -> Servidores -> Testar mostra o ponto verde."
else
	verde "pronto. Recrie o container da API uma vez para pegar os binds do .deploy:"
	echo "   cd $INSTALL_DIR && docker compose up -d api-1 api-2"
	echo "Depois: Administracao -> Servidor mostra a versao e o botao Atualizar."
fi
