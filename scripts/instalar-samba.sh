#!/usr/bin/env bash
#
# instalar-samba.sh — implanta o bloqueio de pasta/delegacia no Samba: cada usuário de domínio
# passa a enxergar SOMENTE a pasta da própria delegacia, lendo o vínculo usuário→delegacia do
# PostgreSQL (seguranca.usr_tb01_usuarios → delegacias).
#
# Roda UMA vez por servidor, como root. Idempotente: rodar de novo só reconfere e recarrega.
#
#   sudo bash scripts/instalar-samba.sh [/caminho/da/instalacao]
#   cd /mnt/dados/analyser && sudo bash scripts/instalar-samba.sh   (auto-detecta CWD)
#
# (use `bash scripts/...` se o bit de execução não tiver sobrevivido ao checkout)
#
# NO NÓ (PAPEL=no, docker-compose.no.yml, sem Postgres): o MESMO bloqueio por delegacia
# do central — mas a lista usuário→delegacia vem da API do central
# (GET /api/no/samba-usuarios, filtrada pelas delegacias DESTE nó), com o NO_TOKEN do
# .env. Detecção automática; force com MODO=central|no|simples (simples = expõe data/
# direto, sem filtro — só como escape).
#
# PRÉ-REQUISITO (fora do escopo deste script): o Samba já estar instalado E ingressado no
# domínio (security = ADS, winbind em uso, `net ads testjoin` = OK). Este script só acrescenta a
# camada de acesso por delegacia por cima.
#
# O que faz:
#   1. confere pré-requisitos (root, smbd ativo, python3, pasta data/; + docker no central,
#      + CENTRAL_URL/NO_TOKEN no nó)
#   2. cria SAMBA_HOME (raiz dos symlinks por usuário)
#   3. instala scripts/sincronizar-samba.sh na instalação e as units systemd (timer a cada 5 min)
#   4. ajusta /etc/samba/smb.conf: `unix extensions = no` (senão o `wide links` é desligado em
#      silêncio) e o compartilhamento [Dados] passa a apontar para .../samba-analyser/%u —
#      com backup automático do smb.conf anterior
#   5. valida com testparm, recarrega o Samba e roda a 1ª sincronização
#
# Variáveis (opcionais, via ambiente):
#   SAMBA_HOME            raiz dos symlinks (padrão: /mnt/dados/samba-analyser)
#   SMB_SHARE             nome do compartilhamento (padrão: Dados)
#   POSTGRES_CONTAINER    container do banco no central (padrão: analyser-postgres)
#   PGUSER / PGDATABASE   credenciais (padrão: analyser / analyser)
#   CENTRAL_URL / NO_TOKEN  (modo no: lidos do .env do nó se ausentes)
#   MODO                  central|no|simples (padrão: auto pelo PAPEL)
#   SAMBA_PERMITIR_SEM_X  1 = ignora o bloqueio de travessia (data/ sob home 750); não use
#
# Ver scripts/sincronizar-samba.sh (o que o sincronizador faz) e o smb.conf resultante.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${1:-}" ]; then
	INSTALL_DIR="$1"
elif [ -n "${ANALYSER_DIR:-}" ]; then
	INSTALL_DIR="$ANALYSER_DIR"
elif [ -f "$PWD/docker-compose.yml" ] || [ -f "$PWD/docker-compose.no.yml" ]; then
	INSTALL_DIR="$PWD"
else
	for cand in /mnt/dados/analyser /opt/analyser "$HOME/analyser" ~/analyser; do
		if [ -f "$cand/docker-compose.yml" ] || [ -f "$cand/docker-compose.no.yml" ]; then
			INSTALL_DIR="$cand"
			break
		fi
	done
	INSTALL_DIR="${INSTALL_DIR:-/mnt/dados/analyser}"
fi
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd || echo "$INSTALL_DIR")"

# Papel: .env com PAPEL=no (ou só docker-compose.no.yml presente) = NÓ.
PAPEL="central"
if grep -qE '^[[:space:]]*PAPEL[[:space:]]*=[[:space:]]*no([[:space:]]|$)' "$INSTALL_DIR/.env" 2>/dev/null; then
	PAPEL="no"
elif [ ! -f "$INSTALL_DIR/docker-compose.yml" ] && [ -f "$INSTALL_DIR/docker-compose.no.yml" ]; then
	PAPEL="no"
fi

SAMBA_HOME="${SAMBA_HOME:-/mnt/dados/samba-analyser}"
SMB_SHARE="${SMB_SHARE:-Dados}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-analyser-postgres}"
PGUSER="${PGUSER:-analyser}"
PGDATABASE="${PGDATABASE:-analyser}"

# MODO: central = symlinks via Postgres LOCAL; no = symlinks via API do central
# (GET /api/no/samba-usuarios); simples = expõe data/ direto, sem filtro (escape).
# O nó cai em "no" sozinho (não tem banco); force com MODO=central|no|simples.
MODO="${MODO:-}"
if [ -z "$MODO" ]; then
	if [ "$PAPEL" = "no" ]; then
		MODO="no"
	else
		MODO="central"
	fi
fi
DATA_DIR="$INSTALL_DIR/data"
if [ -f "$INSTALL_DIR/.env" ]; then
	_env_val() { sed -n "s/^$1=//p" "$INSTALL_DIR/.env" 2>/dev/null | tr -d '[:space:]' | tail -1; }
	[ -z "${CENTRAL_URL:-}" ] && CENTRAL_URL="$(_env_val CENTRAL_URL)"
	[ -z "${NO_TOKEN:-}" ] && NO_TOKEN="$(_env_val NO_TOKEN)"
fi

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { vermelho "rode como root (sudo)."; exit 1; }
if [ ! -f "$INSTALL_DIR/docker-compose.yml" ] && [ ! -f "$INSTALL_DIR/docker-compose.no.yml" ]; then
	vermelho "não parece uma instalação do analyser: $INSTALL_DIR (sem compose)"
	exit 1
fi
[ -d "$DATA_DIR" ] || { vermelho "pasta de dados não encontrada: $DATA_DIR"; exit 1; }
# dono do data/: quando a pasta não existia, foi o Docker quem a criou (root) — e o container
# (appuser, uid do dono da instalação) precisa escrever nela: spool de entrega do nó
# (.entrega), pastas de delegacia novas e organização de arquivos. Sem isto a extração
# descentralizada nasce quebrada.
DATA_OWNER=$(stat -c '%u:%g' "$INSTALL_DIR")
if [ "$(stat -c '%u' "$DATA_DIR")" != "${DATA_OWNER%%:*}" ]; then
	chown "$DATA_OWNER" "$DATA_DIR"
	amarelo "data/ era de outro dono — corrigido para $DATA_OWNER (uid do usuário da instalação)"
fi
# Travessia: o compartilhamento é .../samba-analyser/%u (symlink -> $DATA_DIR/<DELEGACIA>) e o
# smbd faz o chdir JÁ como o uid do usuário SMB (winbind). Todo ancestral de data/ precisa de
# 'x' para outros. Com a instalação sob /home/<usuario> (750 por padrão) o chdir falha e
# NENHUM usuário do domínio vê a pasta — o sincronizador segue "ok" em silêncio porque os
# symlinks ficam corretos (caso real: nó instalado em /home/pc/analyser, 23/10).
primeiro_sem_x() {
	local alvo="$1" p="" parte
	local -a partes
	IFS='/' read -r -a partes <<< "$alvo"
	for parte in "${partes[@]}"; do
		[ -z "$parte" ] && continue
		p="$p/$parte"
		[ -d "$p" ] || break
		if [ $(( $(stat -c '%a' "$p") & 1 )) -eq 0 ]; then
			printf '%s' "$p"
			return 0
		fi
	done
	return 1
}
if SEM_X="$(primeiro_sem_x "$DATA_DIR")"; then
	vermelho "$SEM_X não tem 'x' para outros — o smbd não atravessa e NENHUM usuário verá a pasta."
	vermelho "prefira instalar em /mnt/dados/analyser (docs/instalacao_no.md, passo 2);"
	vermelho "ou libere só a travessia: sudo chmod o+x $SEM_X  (repita até $DATA_DIR) e rode de novo."
	[ "${SAMBA_PERMITIR_SEM_X:-0}" = "1" ] || exit 1
	amarelo "seguindo SEM travessia (SAMBA_PERMITIR_SEM_X=1) — o acesso SMB vai falhar em silêncio."
fi
echo "instalação: $INSTALL_DIR  (papel=$PAPEL, modo=$MODO)"
if [ "$MODO" = "simples" ]; then
	echo "compartilhamento SMB: [$SMB_SHARE]  →  $DATA_DIR  (direto, sem filtro por delegacia)"
else
	echo "compartilhamento SMB: [$SMB_SHARE]  →  $SAMBA_HOME/%u"
	[ "$MODO" = "no" ] && echo "fonte de usuarios: API do central (${CENTRAL_URL:-<CENTRAL_URL vazio!>})"
fi

# ------------------------------------------------------------------------------------------------
echo
echo "== 1. pré-requisitos =="
faltando=""
# central exige docker (Postgres local); nó exige curl (API) — o resto é comum.
if [ "$MODO" = "central" ]; then
	for bin in docker smbd smbcontrol testparm systemctl python3 curl; do
		command -v "$bin" >/dev/null 2>&1 || faltando="$faltando $bin"
	done
elif [ "$MODO" = "no" ]; then
	for bin in curl smbd smbcontrol testparm systemctl python3; do
		command -v "$bin" >/dev/null 2>&1 || faltando="$faltando $bin"
	done
	[ -n "${CENTRAL_URL:-}" ] || { vermelho "CENTRAL_URL vazio — preencha no .env do no antes (ex.: CENTRAL_URL=http://<central>:8080)"; exit 1; }
	[ -n "${NO_TOKEN:-}" ] || { vermelho "NO_TOKEN vazio — cole no .env do no o token da tela Servidores do central"; exit 1; }
else
	for bin in smbd smbcontrol testparm systemctl python3; do
		command -v "$bin" >/dev/null 2>&1 || faltando="$faltando $bin"
	done
fi
[ -n "$faltando" ] && { vermelho "faltam:$faltando"; exit 1; }
systemctl is-active --quiet smbd || systemctl is-active --quiet smb || { vermelho "smbd não está ativo — suba/configure o Samba antes."; exit 1; }
testparm -s >/dev/null 2>&1 || { vermelho "smb.conf inválido — corrija antes de continuar."; exit 1; }
if command -v net >/dev/null 2>&1; then
	net ads testjoin >/dev/null 2>&1 || amarelo "aviso: net ads testjoin falhou — confirme o ingress no domínio (AD)."
fi
verde "ok"

# ------------------------------------------------------------------------------------------------
# MODO SIMPLES (escape, sem filtro): só garante data/ e aponta o compartilhamento direto
# para ela. Útil se o central estiver inalcançável; fora isso prefira modo=no no nó.
if [ "$MODO" = "simples" ]; then
	echo
	echo "== 2. pasta de dados (modo simples — sem symlinks) =="
	mkdir -p "$DATA_DIR"
	verde "ok: $DATA_DIR"
	echo "   (sem SAMBA_HOME / sem sincronizador: não há banco no nó para o vínculo usuário→delegacia)"

	echo
	echo "== 3. smb.conf apontando direto para data/ =="
	cp -a /etc/samba/smb.conf "/etc/samba/smb.conf.backup-$(date +%Y%m%d-%H%M%S)"
	verde "backup: $(ls -1 /etc/samba/smb.conf.backup-* | tail -1)"

	SHARE_PATH="$DATA_DIR" python3 - "$SMB_SHARE" <<'PY'
import os, re, sys
share = sys.argv[1]
share_path = os.environ["SHARE_PATH"]
path = "/etc/samba/smb.conf"
data = open(path).read()

if re.search(r'^\s*unix\s+extensions\s*=', data, re.M | re.I):
    data = re.sub(r'^(\s*)unix\s+extensions\s*=.*$', r'\1unix extensions = no',
                  data, flags=re.M | re.I)
else:
    m = re.search(r'^(\s*\[global\]\s*)$', data, re.M | re.I)
    if m:
        data = data[:m.end()] + "\n   unix extensions = no" + data[m.end():]
    else:
        data = "[global]\n   unix extensions = no\n" + data

bloco = f"""
[{share}]
   comment = Pasta da Delegacia (no)
   path = {share_path}

   read only = no
   browsable = yes

   create mask = 0660
   directory mask = 0770
   inherit permissions = yes

   veto files = /docker/
"""
pattern = re.compile(r'(?ms)^\s*\[' + re.escape(share) + r'\].*?(?=^\s*\[|\Z)')
data = pattern.sub('', data, count=1)
data = data.rstrip() + "\n" + bloco + "\n"
open(path, "w").write(data)
print("   smb.conf atualizado: compartilhamento [%s] -> %s" % (share, share_path))
PY

	echo
	echo "== 4. valida + recarrega =="
	testparm -s >/dev/null || { vermelho "smb.conf ficou inválido — restaure o backup e confira."; exit 1; }
	verde "testparm ok"
	smbcontrol all reload-config 2>/dev/null || systemctl reload smbd 2>/dev/null || systemctl reload smb 2>/dev/null || true
	verde "Samba recarregado"
	echo
	verde "pronto (modo simples). O compartilhamento [$SMB_SHARE] expoe $DATA_DIR."
	verde "O controle de quem ve o que continua no central; aqui o isolamento e fisico."
	exit 0
fi

# ------------------------------------------------------------------------------------------------
# MODOS central/no: symlinks por usuário + timer. A diferença é só a FONTE da lista:
# central = Postgres local; no = API do central (o sincronizador decide sozinho, mas a
# unit do nó já leva CENTRAL_URL/NO_TOKEN e não depende do docker).
echo
echo "== 2. raiz dos symlinks =="
mkdir -p "$SAMBA_HOME"
verde "criado: $SAMBA_HOME"

# ------------------------------------------------------------------------------------------------
echo
echo "== 3. sincronizador + systemd =="
# install recusa src==dst (e é o caso normal: rodar o script DE DENTRO da instalação).
# Copia só quando a origem é outra cópia do repo; o chmod +x garante os dois casos — o
# ExecStart do systemd chama o script direto e morre sem bit de execução.
SRC_SYNC="$(cd "$SCRIPT_DIR" && pwd)/sincronizar-samba.sh"
if [ "$SRC_SYNC" != "$INSTALL_DIR/scripts/sincronizar-samba.sh" ]; then
	install -m 0755 "$SRC_SYNC" "$INSTALL_DIR/scripts/sincronizar-samba.sh"
else
	chmod 0755 "$INSTALL_DIR/scripts/sincronizar-samba.sh"
fi
verde "instalado: $INSTALL_DIR/scripts/sincronizar-samba.sh"

install -m 0644 "$SCRIPT_DIR/systemd/analyser-sincronizar-samba.timer" \
	/etc/systemd/system/analyser-sincronizar-samba.timer

# A unit .service do repo é um template com ExecStart fixo; geramos a instância real com o
# caminho da instalação e as variáveis de ambiente corretas.
if [ "$MODO" = "no" ]; then
	cat > /etc/systemd/system/analyser-sincronizar-samba.service <<EOF
[Unit]
Description=Sincroniza acesso SMB por delegacia via API do central (no -> Samba)
After=network.target
Wants=network.target

[Service]
Type=oneshot
Environment=ANALYSER_DIR=$INSTALL_DIR
Environment=SAMBA_HOME=$SAMBA_HOME
Environment=FONTE=remota
Environment=CENTRAL_URL=$CENTRAL_URL
Environment=NO_TOKEN=$NO_TOKEN
ExecStart=$INSTALL_DIR/scripts/sincronizar-samba.sh
EOF
else
	cat > /etc/systemd/system/analyser-sincronizar-samba.service <<EOF
[Unit]
Description=Sincroniza acesso SMB por delegacia (analyser -> Samba)
After=network.target docker.service
Wants=docker.service

[Service]
Type=oneshot
Environment=ANALYSER_DIR=$INSTALL_DIR
Environment=SAMBA_HOME=$SAMBA_HOME
Environment=POSTGRES_CONTAINER=$POSTGRES_CONTAINER
Environment=PGUSER=$PGUSER
Environment=PGDATABASE=$PGDATABASE
ExecStart=$INSTALL_DIR/scripts/sincronizar-samba.sh
EOF
fi

systemctl daemon-reload
systemctl enable --now analyser-sincronizar-samba.timer
verde "units instaladas e timer ativo (a cada 5 min)"
echo "   ver:   systemctl status analyser-sincronizar-samba.timer"
echo "   log:   journalctl -u analyser-sincronizar-samba.service -f"

# ------------------------------------------------------------------------------------------------
echo
echo "== 4. ajuste do smb.conf =="
cp -a /etc/samba/smb.conf "/etc/samba/smb.conf.backup-$(date +%Y%m%d-%H%M%S)"
verde "backup: $(ls -1 /etc/samba/smb.conf.backup-* | tail -1)"

python3 - "$INSTALL_DIR" "$SAMBA_HOME" "$SMB_SHARE" <<'PY'
import re, sys

install_dir, samba_home, share = sys.argv[1], sys.argv[2], sys.argv[3]
path = "/etc/samba/smb.conf"
data = open(path).read()

# 1. `unix extensions = no` no [global]: sem isso, wide links é desligado em silêncio.
if re.search(r'^\s*unix\s+extensions\s*=', data, re.M | re.I):
    data = re.sub(r'^(\s*)unix\s+extensions\s*=.*$', r'\1unix extensions = no',
                  data, flags=re.M | re.I)
else:
    m = re.search(r'^(\s*\[global\]\s*)$', data, re.M | re.I)
    if m:
        data = data[:m.end()] + "\n   unix extensions = no" + data[m.end():]
    else:
        data = "[global]\n   unix extensions = no\n" + data

# 2. remove a definição atual do compartilhamento (se existir) e acrescenta a nova no fim.
bloco = f"""
[{share}]
   comment = Pasta da Delegacia
   path = {samba_home}/%u

   read only = no
   browsable = yes

   create mask = 0660
   directory mask = 0770
   inherit permissions = yes

   veto files = /docker/

   follow symlinks = yes
   wide links = yes
"""
pattern = re.compile(r'(?ms)^\s*\[' + re.escape(share) + r'\].*?(?=^\s*\[|\Z)')
data = pattern.sub('', data, count=1)
data = data.rstrip() + "\n" + bloco + "\n"

open(path, "w").write(data)
print("   smb.conf atualizado: unix extensions = no + compartilhamento [%s]" % share)
PY

# ------------------------------------------------------------------------------------------------
echo
echo "== 5. valida + recarrega + 1ª sincronização =="
testparm -s >/dev/null || { vermelho "smb.conf ficou inválido — restaure o backup e confira."; exit 1; }
verde "testparm ok"
smbcontrol all reload-config
verde "Samba recarregado"
if [ "$MODO" = "no" ]; then
	ANALYSER_DIR="$INSTALL_DIR" SAMBA_HOME="$SAMBA_HOME" FONTE=remota \
		CENTRAL_URL="$CENTRAL_URL" NO_TOKEN="$NO_TOKEN" \
		"$INSTALL_DIR/scripts/sincronizar-samba.sh"
else
	ANALYSER_DIR="$INSTALL_DIR" SAMBA_HOME="$SAMBA_HOME" \
		POSTGRES_CONTAINER="$POSTGRES_CONTAINER" PGUSER="$PGUSER" PGDATABASE="$PGDATABASE" \
		"$INSTALL_DIR/scripts/sincronizar-samba.sh"
fi

echo
if [ "$MODO" = "no" ]; then
	verde "pronto. Cada usuario das delegacias DESTE no (lista vinda do central) ve apenas a propria pasta;"
else
	verde "pronto. Cada usuário com delegacia no banco passa a ver apenas a própria pasta;"
fi
verde "os demais ficam sem acesso. A sincronização roda sozinha a cada 5 minutos."
