#!/usr/bin/env bash
# Cópia OFFSITE dos backups para um SEGUNDO host (docs/backup.md).
#
# O dump diário em ./backups mora no MESMO disco do banco: incêndio/roubo/criptocoder no host
# leva os dois. Este script usa restic para mandar para outro máquina (SFTP) duas coisas:
#
#   backups/  -> os dumps do sidecar (o banco recriável)
#   data/     -> as PROVAS das delegacias sem nó — o que não é recriável NUNCA
#
# A pasta data/ dos nós NÃO está aqui: cada nó tem a sua (docs/no_descentralizado.md) e o
# offsite dela é responsabilidade da delegacia (mesmo script, no compose dela, sem ./backups).
#
# CONFIGURAÇÃO (uma linha no .env, mais a senha do repositório num arquivo fora do git):
#   RESTIC_REPOSITORIO=sftp:backup@10.0.0.x:/srv/backups/analyser
#   echo 'SENHA-FORTE' > ~/.config/restic-analyser-senha && chmod 600 ~/.config/restic-analyser-senha
#
# Agendar no host (diário, depois do dump noturno do sidecar):
#   0 06 * * * cd /mnt/dados/analyser && scripts/backup-offsite.sh >> logs/backup-offsite.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."

env_val() { awk -F= -v k="$1" '$1==k { sub(/^[^=]*=/, ""); print; exit }' .env; }

export RESTIC_REPOSITORY="${RESTIC_REPOSITORIO:-$(env_val RESTIC_REPOSITORIO)}"
SENHA_ARQUIVO="${RESTIC_SENHA_ARQUIVO:-$(env_val RESTIC_SENHA_ARQUIVO)}"
if [ -z "${RESTIC_REPOSITORY:-}" ] || [ ! -r "${SENHA_ARQUIVO:-/nao/existe}" ]; then
  echo "FALHA: faltam RESTIC_REPOSITORIO e/ou RESTIC_SENHA_ARQUIVO no .env (ver cabeçalho do script)"
  exit 1
fi
export RESTIC_PASSWORD_FILE="$SENHA_ARQUIVO"

command -v restic >/dev/null || { echo "FALHA: restic não instalado no host (apt install restic)"; exit 1; }

# Primeiro uso: inicializa o repositório (idempotente — `restic cat config` detecta existente).
if ! restic cat config >/dev/null 2>&1; then
  echo "Repositório novo — inicializando em $RESTIC_REPOSITORY"
  restic init
fi

# data/.entrega é o spool de passagem das entregas dos nós (docs/no_descentralizado.md):
# trafega de novo a cada lote, nunca é fonte de verdade — fora do backup. Mesma lógica para
# *.parcial (uploads interrompidos) e caches locais.
restic backup backups data \
  --exclude 'data/.entrega' \
  --exclude '*.parcial' \
  --tag analyser

# Retenção igual à do sidecar: 14 diários, 8 semanais, 12 mensais. O prune só apaga snapshots
# deste restic — os dumps locais continuam sob a retenção do próprio sidecar.
restic forget --prune --keep-daily 14 --keep-weekly 8 --keep-monthly 12
restic check --read-data-subset=2%

echo "OK: offsite concluído em $(date '+%F %T')"
