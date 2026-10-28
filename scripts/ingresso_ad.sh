#!/usr/bin/env bash
#
# ingresso_ad.sh — ingressa um servidor (tipicamente um NÓ do analyser) no domínio AD PF
# (realm PF.GOV.BR), espelhando a configuração do central: security = ADS + winbind +
# idmap rid, exatamente como em /etc/samba/smb.conf no central.
#
#   sudo bash scripts/ingresso_ad.sh --usuario xpto@pf.gov.br [--senha 'senha'] [--hostname no01.pf.gov.br]
#
# (use `bash scripts/...` se o bit de execução não tiver sobrevivido ao checkout)
#
# --senha é OPCIONAL e tem um porém: passada na linha de comando fica visível no `ps` e no
# history do shell. Prefira OMITIR — o script pede com `read -s` (mascarado). Também é
# aceito via variável de ambiente AD_SENHA.
#
# Fluxo real do AD de vocês (por isso o script é dividido em fases):
#   1. o join cria a conta de máquina e ela cai no container padrão de máquinas novas
#      ("New computer domain" / container Computers);
#   2. o chefe do AD MOVE a máquina para a OU da unidade — isso é manual, do lado do AD,
#      e nenhuma GPO/OU afeta o que o script valida aqui;
#   3. por isso o teste do script é: testjoin + wbinfo --ping-dc + getent de um login —
#      autenticação e enumeração já funcionam ANTES da movição para a OU. Depois que o
#      chefe mover, basta reconferir com --verificar (sem --usuario/--senha).
#
# O que faz (idempotente — rodar de novo só reconfere/conserta):
#   1. instala pacotes (samba winbind libpam-winbind libnss-winbind krb5-user smbclient)
#   2. garante DNS apontando para os DCs (drop-in do systemd-resolved — não toca no netplan)
#   3. garante hostname FQDN (+ /etc/hosts) e o relógio no padrão America/Sao_Paulo (UTC-3)
#      com NTP ligado — o AD exige os dois lados com a MESMA hora (kerberos tolera < 5 min)
#   4. escreve /etc/krb5.conf.d/analyser-pf.conf (default_realm + kdcs PF.GOV.BR)
#   5. reescreve o [global] do /etc/samba/smb.conf para ADS (backup automático; PRESERVA
#      os compartilhamentos já definidos — ex.: [Dados] do instalar-samba.sh)
#   6. nsswitch (passwd/group: winbind) + PAM (pam-auth-update --enable winbind)
#   7. `net ads join` com a conta informada e sobe/habilita winbind+smbd
#   8. valida (testjoin, ping-dc, getent) e lembra de pedir a movição da máquina para a OU
#
# Variáveis (opcionais, via ambiente):
#   REALM / WORKGROUP       domínio AD (padrão: PF.GOV.BR / PF)
#   DC_ips                  IPs dos DCs para DNS/kinit (padrão: 10.27.4.8 10.27.4.9)
#   AD_SENHA                senha da conta com permissão de join (alternativa a --senha)
#   TZ_LOCAL                fuso horário (padrão: America/Sao_Paulo — UTC-3)
#   PULAR_JOIN=1            só prepara/configura, sem ingressar (útil p/ pré-DFY)
#   MAQUINA_TESTE           login p/ validar getent pós-join (padrão: primeiro do wbinfo -u)
#
# Ver scripts/instalar-samba.sh (o que vem DEPOIS do ingresso) e docs/no_descentralizado.md.

set -euo pipefail

REALM="${REALM:-PF.GOV.BR}"
WORKGROUP="${WORKGROUP:-PF}"
DC_ips="${DC_ips:-10.27.4.8 10.27.4.9}"
PULAR_JOIN="${PULAR_JOIN:-0}"
MAQUINA_TESTE="${MAQUINA_TESTE:-}"

USUARIO=""
SENHA="${AD_SENHA:-}"
FQDN_NOVO=""
VERIFICAR=0

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }
linha()    { printf '\033[36m== %s\033[0m\n' "$*"; }

usage() {
	sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
}

while [ $# -gt 0 ]; do
	case "$1" in
		--usuario)   USUARIO="${2:?--usuario exige xpto@pf.gov.br}"; shift 2 ;;
		--senha)     SENHA="${2:?--senha exige o valor (ou omita e use AD_SENHA / prompt)}"; shift 2 ;;
		--hostname)  FQDN_NOVO="${2:?--hostname exige no01.pf.gov.br}"; shift 2 ;;
		--verificar) VERIFICAR=1; shift ;;
		-h|--help)   usage ;;
		*) vermelho "opção desconhecida: $1"; usage ;;
	esac
done

[ "$(id -u)" = 0 ] || { vermelho "rode como root (sudo)."; exit 1; }

# ------------------------------------------------------------------------------------------------
# --verificar: só revalida (uso depois que o chefe do AD mover a máquina para a OU)
if [ "$VERIFICAR" = 1 ]; then
	linha "verificação do ingresso"
	net ads testjoin && verde "testjoin OK — máquina ingressada" || { vermelho "testjoin FALHOU"; exit 1; }
	wbinfo --ping-dc 2>&1 | tail -1
	u="${MAQUINA_TESTE:-$(wbinfo -u 2>/dev/null | head -1)}"
	if [ -n "$u" ] && getent passwd "$u" >/dev/null; then
		verde "winbind resolvendo usuários ($(id -u "$u" 2>/dev/null) = uid de '$u' — faixa idmap ok)"
	else
		vermelho "winbind não resolveu '$u' — confira systemctl status winbind e o nsswitch"
		exit 1
	fi
	verde "pronto. Se ainda não pediram, peça ao chefe do AD para MOVER $(hostname -f) para a OU da unidade."
	exit 0
fi

[ -n "$USUARIO" ] || { vermelho "falta --usuario xpto@pf.gov.br (ou rode com --verificar)"; usage; }
if [ -z "$SENHA" ]; then
	read -rs -p "Senha de ${USUARIO} (digitado fica mascarado): " SENHA; echo
fi

linha "1. pacotes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq samba winbind libpam-winbind libnss-winbind krb5-user smbclient keyutils >/dev/null
verde "ok"

# ------------------------------------------------------------------------------------------------
linha "2. DNS -> DCs ($DC_ips)"
PRIMARIO="$(echo "$DC_ips" | awk '{print $1}')"
if getent hosts "ses0001.${REALM,,}" >/dev/null 2>&1 && host -t srv "_ldap._tcp.${REALM,,}" >/dev/null 2>&1; then
	verde "DNS já resolve o domínio (nada a fazer)"
else
	mkdir -p /etc/systemd/resolved.conf.d
	cat > /etc/systemd/resolved.conf.d/analyser-pf-dns.conf <<EOF
# criado por scripts/ingresso_ad.sh — resolução do domínio AD PF
[Resolve]
DNS=${DC_ips// / }
Domains=${REALM,,}
EOF
	systemctl restart systemd-resolved
	for i in $(seq 1 10); do
		host -t srv "_ldap._tcp.${REALM,,}" >/dev/null 2>&1 && break
		sleep 2
	done
	host -t srv "_ldap._tcp.${REALM,,}" >/dev/null 2>&1 \
		|| { vermelho "não consegui resolver _ldap._tcp.${REALM,,} nem com DNS=${DC_ips} — a rede deste servidor alcança os DCs?"; exit 1; }
	verde "DNS ok (SRV _ldap._tcp.${REALM,,} resolvido)"
fi

# ------------------------------------------------------------------------------------------------
linha "3. hostname FQDN"
if [ -n "$FQDN_NOVO" ]; then
	case "$FQDN_NOVO" in
		*.*) : ;;
		*) vermelho "--hostname precisa ser FQDN (ex.: no01.${REALM,,})"; exit 1 ;;
	esac
	hostnamectl set-hostname "$FQDN_NOVO"
	IP_PRINCIPAL="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
	sed -i "/[Pp]f\.gov/[Bb]r/d" /etc/hosts || true
	echo "${IP_PRINCIPAL:-127.0.1.1} ${FQDN_NOVO} ${FQDN_NOVO%%.*}" >> /etc/hosts
	verde "hostname = $(hostname -f)"
elif hostname -f | grep -q '\.'; then
	verde "hostname já é FQDN: $(hostname -f)"
else
	vermelho "hostname atual ('$(hostname)') não é FQDN e o AD usa isso como nome da máquina."
	vermelho "rode de novo com: --hostname no01.${REALM,,}"
	exit 1
fi
# o join exige resolução do PRÓPRIO nome e horário coerente (kerberos: skew < 5 min)
getent hosts "$(hostname -f)" >/dev/null || { vermelho "$(hostname -f) não resolve (vejam /etc/hosts/DNS)"; exit 1; }

# relógio: fuso America/Sao_Paulo (UTC-3, sem horário de verão hoje) + NTP ligado. O que o
# kerberos cobra é o INSTANTE igual nos dois lados — o fuso não altera o skew — mas padroniza
# os logs/datetimes do servidor com os do central e dos postos.
TZ_LOCAL="${TZ_LOCAL:-America/Sao_Paulo}"
ATUAL="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [ "$ATUAL" != "$TZ_LOCAL" ]; then
	timedatectl set-timezone "$TZ_LOCAL" && verde "fuso horário = $(timedatectl show -p Timezone --value) (UTC-3, São Paulo)"
fi
timedatectl set-ntp true 2>/dev/null || amarelo "aviso: não consegui ligar o NTP (timedatectl set-ntp) — confira manualmente; sem hora certa o kinit/join falha."
for i in $(seq 1 15); do
	timedatectl show 2>/dev/null | grep -q 'NTPSynchronized=yes' && break
	sleep 2
done
if timedatectl show 2>/dev/null | grep -q 'NTPSynchronized=yes'; then
	verde "relógio sincronizado via NTP: $(date '+%F %T %Z')"
else
	amarelo "aviso: NTP ainda não sincronizou — confira se este servidor alcança o servidor de hora (date vs. DC)."
fi

# ------------------------------------------------------------------------------------------------
linha "4. kerberos (realm ${REALM})"
mkdir -p /etc/krb5.conf.d
cat > /etc/krb5.conf.d/analyser-pf.conf <<EOF
# criado por scripts/ingresso_ad.sh — espelha o /etc/krb5.conf do central
[libdefaults]
	default_realm = ${REALM}
	rdns = false
	udp_preference_limit = 0

[realms]
	${REALM} = {
		kdc = $(echo "$DC_ips" | awk '{print $1}')
		kdc = $(echo "$DC_ips" | awk '{print $2}')
		admin_server = $(echo "$DC_ips" | awk '{print $1}')
	}
EOF
verde "ok (/etc/krb5.conf.d/analyser-pf.conf)"

# ------------------------------------------------------------------------------------------------
linha "5. smb.conf [global] -> security = ADS"
cp -a /etc/samba/smb.conf "/etc/samba/smb.conf.backup-$(date +%Y%m%d-%H%M%S)"
verde "backup: $(ls -1 /etc/samba/smb.conf.backup-* | tail -1)"

# Reescreve SÓ o [global] (mesmas linhas do central) e preserva os compartilhamentos
# existentes — ex.: um [Dados] já criado pelo instalar-samba.sh não é perdido.
REALM_MAIUSC="${REALM}" WORKGROUP_MAIS="${WORKGROUP}" python3 - <<'PY'
import os, re

realm = os.environ["REALM_MAIUSC"]
workgroup = os.environ["WORKGROUP_MAIS"]
path = "/etc/samba/smb.conf"
data = open(path).read()

global_novo = f"""[global]
   # ingresso via scripts/ingresso_ad.sh — espelho do central (AD {realm})
   workgroup = {workgroup}
   realm = {realm}
   security = ADS
   server role = active directory domain controller client
   kerberos method = secrets and keytab
   server min protocol = SMB2
   unix extensions = no
   disable spoolss = Yes
   load printers = No
   log file = /var/log/samba/log.%m
   max log size = 1000
   template homedir = /home/%U
   template shell = /bin/bash
   winbind use default domain = Yes
   winbind enum users = Yes
   winbind enum groups = Yes
   idmap config * : backend = tdb
   idmap config * : range = 3000-7999
   idmap config {workgroup} : backend = rid
   idmap config {workgroup} : range = 10000-999999
"""
# Varredura LINHA a linha: o [global] vai do header até o PRÓXIMO header de seção REAL
# (linha '[nome]' sozinha) — um regex que para no primeiro '[' atravessa comentários do
# smb.conf padrão do Debian/Ubuntu (ex.: "...a [profiles] share to be setup...") e deixa
# um lixo de seção falsa. Header só conta se a linha inteira for [algo].
import re
lines = data.splitlines(keepends=True)
cabecalho = re.compile(r'^\s*\[[^\]]+\]\s*$')
ini = next((i for i, l in enumerate(lines) if re.match(r'^\s*\[global\]\s*$', l, re.I)), None)
if ini is None:
    data = global_novo + "\n" + data
else:
    fim = next((j for j in range(ini + 1, len(lines)) if cabecalho.match(lines[j])), len(lines))
    data = "".join(lines[:ini]) + global_novo + "\n" + "".join(lines[fim:])

# derruba restos do modo standalone que brigam com o ADS
for pad in (r'(?m)^\s*server role\s*=.*$', r'(?m)^\s*map to guest\s*=.*$',
            r'(?m)^\s*security\s*=\s*(user|share|domain)\b.*$'):
    data = re.sub(pad, '', data)

open(path, "w").write(data)
print("   [global] reescrito; compartilhamentos preservados")
PY
testparm -s >/dev/null || { vermelho "smb.conf ficou inválido — restaure o backup e confira."; exit 1; }
verde "testparm ok"

# ------------------------------------------------------------------------------------------------
linha "6. nsswitch + PAM (winbind)"
grep -qE '^passwd:.*winbind' /etc/nsswitch.conf \
	|| sed -i -E 's/^(passwd:[[:space:]]+files)(.*)$/\1 winbind\2/' /etc/nsswitch.conf
grep -qE '^group:.*winbind' /etc/nsswitch.conf \
	|| sed -i -E 's/^(group:[[:space:]]+files)(.*)$/\1 winbind\2/' /etc/nsswitch.conf
grep -E '^(passwd|group):' /etc/nsswitch.conf
pam-auth-update --enable winbind 2>/dev/null || amarelo "aviso: pam-auth-update falhou — login por winbind via console pode precisar de ajuste manual"
verde "ok"

# ------------------------------------------------------------------------------------------------
if [ "$PULAR_JOIN" = 1 ]; then
	verde "PULAR_JOIN=1 — configuração pronta, JOIN não executado. Rode de novo sem a flag para ingressar."
	exit 0
fi

linha "7. ingresso no domínio (net ads join)"
if net ads testjoin >/dev/null 2>&1; then
	verde "já ingressado — pulando o join"
else
	# a senha vai no argv do net (visível no `ps` por alguns segundos); o processo é filho
	# exclusivo deste script root e a janela é curta — aceite, ou rode via `read -s` (padrão).
	net ads join -U "${USUARIO}%${SENHA}" || {
		vermelho "JOIN FALHOU — causas comuns: conta sem permissão de join, máquina pré-cadastrada em outro lugar,"
		vermelho "DNS do DC inalcançável ou relógio adiantado/atrasado > 5 min (kinit ${USUARIO} p/ depurar)."
		exit 1
	}
fi

# REINICIAR o winbind depois do join é obrigatório: se ele já estava no ar (instalação/
# habilitação nas fases anteriores), continua com o secrets.tdb antigo em memória e o
# wbinfo --ping-dc morre em WBC_ERR_DOMAIN_NOT_FOUND com `dc connection to ""`.
systemctl enable --now winbind
systemctl restart winbind
systemctl enable --now smbd
systemctl restart smbd
smbcontrol all reload-config 2>/dev/null || true
verde "winbind + smbd ativos (reiniciados pós-join)"

# ------------------------------------------------------------------------------------------------
linha "8. validação"
# tolerante a set -e: wbinfo pode falhar enquanto o canal com o DC assenta — tenta algumas vezes
net ads testjoin >/dev/null 2>&1 && verde "testjoin OK" || { vermelho "testjoin falhou logo após o join — veja 'tail /var/log/samba/log.*'"; exit 1; }
OK=0
for i in 1 2 3 4 5; do
	if wbinfo --ping-dc >/dev/null 2>&1; then OK=1; break; fi
	sleep 5
done
if [ "$OK" = 1 ]; then
	verde "winbind falou com o DC: $(wbinfo --ping-dc 2>&1 | tail -1)"
else
	vermelho "wbinfo --ping-dc falhou mesmo após o join (WBC_ERR_DOMAIN_NOT_FOUND?)"
	vermelho "depurar: systemctl restart winbind; journalctl -u winbind -n 30; kinit ${USUARIO}"
	exit 1
fi
USERS="$(wbinfo -u 2>/dev/null | wc -l || echo 0)"
verde "wbinfo lista ${USERS} usuários do domínio"
u="${MAQUINA_TESTE:-}"
[ -n "$u" ] || u="$(wbinfo -u 2>/dev/null | head -1 || true)"
if [ -n "$u" ] && getent passwd "$u" >/dev/null 2>&1; then
	verde "getent '$u' -> $(id "$u" 2>/dev/null || true)"
else
	amarelo "aviso: getent não resolveu '$u' — confira nsswitch/winbind e tente com MAQUINA_TESTE=<login>"
fi

echo
verde "INGRESSO CONCLUÍDO: $(hostname -f) está no domínio ${REALM}."
amarelo "Lembrete do fluxo de vocês: a conta de máquina caiu no container padrão ('New computer domain')."
amarelo "Peça ao chefe do AD para mover '$(hostname -s)' para a OU da unidade (GPO etc.)."
amarelo "Depois da movição, reconfira sem join: sudo bash $0 --verificar"
amarelo "E então siga com: sudo bash scripts/instalar-samba.sh (bloco por delegacia via API do central)."
