#!/usr/bin/env bash
#
# sincronizar-samba.sh — mantém o acesso SMB de cada usuário restrito à pasta da própria
# delegacia, lendo o vínculo usuário→delegacia do PostgreSQL (central) ou da API do
# central (nó — GET /api/no/samba-usuarios, que já filtra pelas delegacias do nó).
#
# Por que existe: o compartilhamento [Dados] do Samba (security = ADS, domínio PF) apontava
# para data/ inteiro, então QUALQUER usuário do domínio enxergava as pastas de TODAS as
# delegacias. Este script gera, para cada usuário ativo/aprovado com delegacia, um symlink
#
#     /mnt/dados/samba-analyser/<login>            ->  <INSTALL>/data/<DELEGACIA>
#     /mnt/dados/samba-analyser/<DOMINIO>\<login>  ->  <INSTALL>/data/<DELEGACIA>
#
# e o smb.conf expõe `path = .../samba-analyser/%u`. Com security = ADS o Samba substitui
# `%u` pelo usuário COM domínio (ex.: `PF\helison.hld`), por isso criamos também o link
# com o prefixo do domínio — o nome "limpo" fica como garantia para o caso de a conta
# chegar sem domínio. A delegacia é sanitizada com a MESMA regra do app
# (`api/funcoes/investigacao_utils.py`), ex.: FICCO/ES -> FICCO-ES. Usuário sem vínculo
# (ou com delegacia NULL) não ganha symlink e fica SEM acesso — o `path` não existe e o
# Samba nega a conexão.
#
# De onde vem a lista:
#   - CENTRAL (tem Postgres local): `docker exec <POSTGRES_CONTAINER> psql ...` (original);
#   - NÓ (PAPEL=no, sem banco): `curl $CENTRAL_URL/api/no/samba-usuarios` com o NO_TOKEN
#     do .env (mesmo token do heartbeat). Detecção automática: se o container do Postgres
#     não existe/responde, cai para a API. Force com FONTE=samba-usuarios=local|remota.
#
# Idempotente: rodar de novo só recria os links que mudaram e apaga os órfãos. Deve rodar como
# root (grava em SAMBA_HOME e recarrega o Samba). Agende a cada poucos minutos
# (cron/systemd) para acompanhar cadastro/troca de delegacia sem reiniciar nada na mão.
#
#   sudo bash scripts/sincronizar-samba.sh
#
# Variaveis (opcionais):
#   ANALYSER_DIR           instalação do analyser (padrão: /mnt/dados/analyser)
#   SAMBA_HOME             raiz dos symlinks (padrão: /mnt/dados/samba-analyser)
#   SAMBA_DOMINIO          domínio (padrão: wbinfo --own-domain / workgroup do smb.conf)
#   POSTGRES_CONTAINER     container do banco no central (padrão: analyser-postgres)
#   PGUSER / PGDATABASE    credenciais (padrão: analyser / analyser)
#   CENTRAL_URL            base do central p/ o modo nó (lida do .env se ausente)
#   NO_TOKEN               token do nó p/ a API (lido do .env se ausente)
#   FONTE                  local|remota (padrão: auto — tenta local, cai p/ remota)
#   SINCRONIZAR_SAMBA_SEM_RECARREGAR=1   pula o reload (útil em teste)

set -euo pipefail

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { vermelho "rode como root (sudo)."; exit 1; }

ANALYSER_DIR="${ANALYSER_DIR:-/mnt/dados/analyser}"
SAMBA_HOME="${SAMBA_HOME:-/mnt/dados/samba-analyser}"
DATA_DIR="$ANALYSER_DIR/data"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-analyser-postgres}"
PGUSER="${PGUSER:-analyser}"
PGDATABASE="${PGDATABASE:-analyser}"
FONTE="${FONTE:-auto}"

# No modo nó, CENTRAL_URL e NO_TOKEN vêm do .env da instalação (mesmos do heartbeat);
# permite sobrescrever via ambiente.
if [ -f "$ANALYSER_DIR/.env" ]; then
	_env_val() { sed -n "s/^$1=//p" "$ANALYSER_DIR/.env" 2>/dev/null | tr -d '[:space:]' | tail -1; }
	[ -z "${CENTRAL_URL:-}" ] && CENTRAL_URL="$(_env_val CENTRAL_URL)"
	[ -z "${NO_TOKEN:-}" ] && NO_TOKEN="$(_env_val NO_TOKEN)"
fi
CENTRAL_URL="${CENTRAL_URL:-}"
CENTRAL_URL="${CENTRAL_URL%/}"
NO_TOKEN="${NO_TOKEN:-}"

[ -d "$DATA_DIR" ] || { vermelho "pasta de dados nao encontrada: $DATA_DIR"; exit 1; }

# Travessia (ver instalar-samba.sh): o smbd faz chdir na pasta da delegacia ja como o uid do
# usuario SMB; se algum ancestral de DATA nao tem 'x' para outros (instalacao sob
# /home/<usuario> 750), NINGUEM acessa o share enquanto a sincronizacao segue "ok" em silencio
# (os symlinks ficam corretos). Aqui so avisa — o timer nao pode travar sozinho.
_avisa_sem_x() {
	local alvo="$1" p="" parte
	local -a partes
	IFS='/' read -r -a partes <<< "$alvo"
	for parte in "${partes[@]}"; do
		[ -z "$parte" ] && continue
		p="$p/$parte"
		[ -d "$p" ] || break
		if [ $(( $(stat -c '%a' "$p") & 1 )) -eq 0 ]; then
			amarelo "ATENCAO: $p nao tem 'x' para outros — o smbd nao atravessa e nenhum usuario"
			amarelo "  vera a pasta de rede. Mova a instalacao p/ /mnt/dados/analyser ou rode"
			amarelo "  'sudo chmod o+x' nos ancestrais ate $DATA_DIR."
			return
		fi
	done
}
_avisa_sem_x "$DATA_DIR"

# Decide a fonte: local (docker exec no Postgres) ou remota (API do central). Auto =
# tenta local se o container existe e responde; senão, remota (o caso do nó, que não
# tem banco). Erro aqui é explícito para não gerar "sem acesso para todo mundo" em
# silêncio por falha de credencial/rede.
escolher_fonte() {
	if [ "$FONTE" = "local" ] || [ "$FONTE" = "remota" ]; then
		echo "$FONTE"
		return
	fi
	if docker exec "$POSTGRES_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" -t -A -c "SELECT 1" >/dev/null 2>&1; then
		echo "local"
	else
		echo "remota"
	fi
}
MODO_FONTE="$(escolher_fonte)"
echo "fonte de usuarios: $MODO_FONTE"
if [ "$MODO_FONTE" = "remota" ]; then
	[ -n "$CENTRAL_URL" ] || { vermelho "CENTRAL_URL vazio — preencha no .env do no (ex.: CENTRAL_URL=http://<central>:8080)"; exit 1; }
	[ -n "$NO_TOKEN" ] || { vermelho "NO_TOKEN vazio — cole no .env do no o token da tela Servidores do central"; exit 1; }
	command -v curl >/dev/null 2>&1 || { vermelho "curl ausente — instale para o modo no (apt install curl)"; exit 1; }
fi

# Domínio que o Samba prefixa no %u (ex.: PF\helison.hld). Preferência: variável, wbinfo,
# workgroup do smb.conf, e por fim 'PF'.
DOMINIO="${SAMBA_DOMINIO:-}"
if [ -z "$DOMINIO" ]; then
	DOMINIO="$(wbinfo --own-domain 2>/dev/null | tr -d '[:space:]' || true)"
fi
if [ -z "$DOMINIO" ] && [ -r /etc/samba/smb.conf ]; then
	DOMINIO="$(awk -F= '/^[[:space:]]*workgroup[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /etc/samba/smb.conf 2>/dev/null || true)"
fi
DOMINIO="${DOMINIO:-PF}"

# ------------------------------------------------------------------------------------------------
# Mesma regra de api/funcoes/investigacao_utils.py::_sanitizar_delegacia:
# maiúsculas, '/' e '\' viram espaço, só [A-Z0-9 _-] sobrevive, espaços viram '-'.
# 'FICCO/ES' -> 'FICCO-ES'.
sanitizar_delegacia() {
	printf '%s' "$1" \
		| tr '[:lower:]' '[:upper:]' \
		| tr '/\\' '  ' \
		| tr -cd 'A-Z0-9 _-' \
		| tr -s ' ' \
		| sed 's/^ //; s/ $//' \
		| tr ' ' '-'
}

# ------------------------------------------------------------------------------------------------
mkdir -p "$SAMBA_HOME"

# Só usuários ativos, aprovados e com delegacia vinculada têm pasta. Formato interno:
# uma linha "login|delegacia" por usuário — fácil de dividir com read.
if [ "$MODO_FONTE" = "local" ]; then
	RESULTADO="$(docker exec "$POSTGRES_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" \
		-t -A -F'|' -c \
		"SELECT u.no_login, d.no_delegacia
		 FROM seguranca.usr_tb01_usuarios u
		 JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
		 WHERE u.in_ativo AND u.in_aprovado
		 ORDER BY u.no_login;")"
else
	# Nó: o central filtra pelas delegacias DESTE nó (NO_TB02) — o script recebe só quem
	# pode ter pasta aqui. Resposta: {"usuarios":[{"login","delegacia","pasta"}]}.
	RESPOSTA="$(curl -fsSL --max-time 30 -H "Authorization: Bearer $NO_TOKEN" \
		"$CENTRAL_URL/api/no/samba-usuarios")" || {
		vermelho "falha ao buscar usuarios em $CENTRAL_URL/api/no/samba-usuarios"
		vermelho "confira CENTRAL_URL/NO_TOKEN e se o central esta acessivel (401 = token trocado?)"
		exit 1
	}
	RESULTADO="$(printf '%s' "$RESPOSTA" | python3 -c \
		"import json,sys; d=json.load(sys.stdin); print('\n'.join(f\"{u['login']}|{u['delegacia']}\" for u in d.get('usuarios',[])))")"
	[ -n "$RESULTADO" ] || amarelo "aviso: o central retornou 0 usuarios para este no (delegacias vinculadas? usuarios ativos?)"
fi

LISTA_LOGINS="$(mktemp)"

criados=0
atualizados=0
pulados=0

# Cria/atualiza UM symlink em $SAMBA_HOME/$nome apontando para $alvo e registra $nome na lista
# dos que continuam valendo.
vincular() {
	local nome="$1" alvo="$2"
	local link="$SAMBA_HOME/$nome"
	printf '%s\n' "$nome" >> "$LISTA_LOGINS"
	if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$(readlink -f "$alvo")" ]; then
		pulados=$((pulados + 1))
	elif [ -L "$link" ]; then
		ln -sfn "$alvo" "$link"
		atualizados=$((atualizados + 1))
		echo "  $nome -> $pasta (atualizado)"
	else
		ln -s "$alvo" "$link"
		criados=$((criados + 1))
		echo "  $nome -> $pasta"
	fi
}

while IFS='|' read -r login delegacia; do
	[ -z "$login" ] && continue
	login="$(printf '%s' "$login" | xargs)"          # tira espaços do próprio read
	delegacia="$(printf '%s' "$delegacia" | xargs)"

	# login com separador de caminho não pode virar nome de symlink — ignora com aviso.
	case "$login" in */*|*\\*) amarelo "login ignorado (separador de caminho): $login"; continue ;; esac

	pasta="$(sanitizar_delegacia "$delegacia")"
	[ -n "$pasta" ] || { amarelo "delegacia inválida para $login: '$delegacia' — sem acesso"; continue; }

	alvo="$DATA_DIR/$pasta"

	# Cria a pasta da delegacia se ainda não existir (usuário novo / unidade nova), com a
	# mesma posse/permissão das demais (o container escreve como nobody:nogroup).
	if [ ! -d "$alvo" ]; then
		mkdir -p "$alvo"
		chown nobody:nogroup "$alvo"
		chmod 2777 "$alvo"
		amarelo "pasta criada: $alvo"
	fi

	vincular "$login" "$alvo"
	if [ -n "$DOMINIO" ]; then
		vincular "$DOMINIO\\$login" "$alvo"
	fi
done <<< "$RESULTADO"

# Remove symlinks órfãos: usuário saiu do banco, foi desativado ou perdeu a delegacia.
removidos=0
for link in "$SAMBA_HOME"/*; do
	[ -L "$link" ] || continue          # só gerencia symlinks; nunca toca em arquivo/pasta alheio
	nome="$(basename "$link")"
	if ! grep -Fxq "$nome" "$LISTA_LOGINS"; then
		rm -f "$link"
		echo "  $nome (removido — sem vínculo ativo)"
		removidos=$((removidos + 1))
	fi
done
rm -f "$LISTA_LOGINS"

echo
verde "sync: $criados criado(s), $atualizados atualizado(s), $pulados inalterado(s), $removidos removido(s)"

# ------------------------------------------------------------------------------------------------
if [ "${SINCRONIZAR_SAMBA_SEM_RECARREGAR:-0}" = "1" ]; then
	amarelo "reload do Samba pulado (SINCRONIZAR_SAMBA_SEM_RECARREGAR=1)"
	exit 0
fi

if command -v smbcontrol >/dev/null 2>&1; then
	smbcontrol all reload-config
	verde "Samba recarregado (smbcontrol reload-config)"
elif command -v systemctl >/dev/null 2>&1; then
	systemctl reload smbd 2>/dev/null || systemctl reload smb 2>/dev/null || true
	verde "Samba recarregado (systemctl reload smbd)"
fi
