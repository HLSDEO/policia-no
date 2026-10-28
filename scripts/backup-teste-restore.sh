#!/usr/bin/env bash
# Teste de restauração do dump mais recente de ./backups (docs/backup.md).
#
# Backup sem restore testado não é backup. Este script restaura o último dump num Postgres
# DESCARTÁVEL (container que morre no fim), conta as tabelas dos schemas da aplicação e diz
# se o arquivo é restaurável de verdade. Não toca no banco nem nos dados do sistema — o
# container de teste sobe sem porta publicada e com volume efêmero.
#
# Agendar no host (semanal, fora do horário do dump):
#   crontab -e
#   30 05 * * 6 cd /mnt/dados/analyser && scripts/backup-teste-restore.sh >> logs/backup-restore.log 2>&1
#
# Saída: 0 = restaurou; 1 = falhou (com o motivo no stdout). Feito para rodar como o mesmo
# usuário do docker compose (precisa de acesso ao docker).
set -euo pipefail

cd "$(dirname "$0")/.."
RAIZ="$PWD"
NOME_TESTE="analyser-restaurar-teste"
IMAGEM="pgvector/pgvector:pg16"   # a mesma do compose: o dump tem CREATE EXTENSION vector

# Lê uma chave do .env sem fazer source (o .env tem valores com espaços/acentos).
env_val() { awk -F= -v k="$1" '$1==k { sub(/^[^=]*=/, ""); print; exit }' .env; }

DB="$(env_val POSTGRES_DB)"
[ -n "$DB" ] || { echo "FALHA: POSTGRES_DB ausente no .env"; exit 1; }

# O dump mais recente por mtime (.dump é o formato do sidecar; .sql.gz aceita por tolerância).
# -mmin +10: ignora arquivo EM ESCRITA (o sidecar pode estar no meio de um pg_dump grande —
# restaurar arquivo incompleto dá "end of file" e assusta à toa).
DUMP="$(find backups -type f \( -name '*.dump' -o -name '*.sql.gz' -o -name '*.sql' \) -mmin +10 \
        -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
if [ -z "${DUMP:-}" ]; then
  echo "FALHA: nenhum dump em ./backups — o sidecar 'backups' está subindo?"
  exit 1
fi
echo "Restaurando: $DUMP ($(du -h "$DUMP" | cut -f1))"

docker rm -f "$NOME_TESTE" >/dev/null 2>&1 || true
trap 'docker rm -f "$NOME_TESTE" >/dev/null 2>&1 || true' EXIT

# --rm não basta para saber o exit code do RESTORE, então o container fica até o trap limpar.
docker run -d --name "$NOME_TESTE" \
  -e POSTGRES_DB=restaurar -e POSTGRES_USER=restaurar -e POSTGRES_PASSWORD=restaurar \
  "$IMAGEM" postgres -c max_connections=200 >/dev/null

echo "Aguardando o Postgres descartável subir..."
for _ in $(seq 1 60); do
  if docker exec "$NOME_TESTE" pg_isready -U restaurar -d restaurar >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$NOME_TESTE" pg_isready -U restaurar -d restaurar >/dev/null \
  || { echo "FALHA: Postgres descartável não subiu"; exit 1; }

echo "Restaurando (schema + dados, erros CONTAM como falha — dump fiel de um banco vivo não
deve ferir nenhuma constraint própria)..."
ERROS_RESTORE=0
case "$DUMP" in
  *.dump)
    SAIDA="$(docker exec -i "$NOME_TESTE" pg_restore -U restaurar -d restaurar \
      --no-owner --no-privileges 2>&1 < "$DUMP")" || ERROS_RESTORE=1
    ;;
  *.sql.gz)
    SAIDA="$(gunzip -c "$DUMP" | docker exec -i "$NOME_TESTE" psql -U restaurar -d restaurar \
      -v ON_ERROR_STOP=1 -q 2>&1)" || ERROS_RESTORE=1
    ;;
  *)
    SAIDA="$(docker exec -i "$NOME_TESTE" psql -U restaurar -d restaurar \
      -v ON_ERROR_STOP=1 -q < "$DUMP" 2>&1)" || ERROS_RESTORE=1
    ;;
esac
if [ "$ERROS_RESTORE" -ne 0 ]; then
  echo "FALHA: pg_restore reportou erros:"
  printf '%s\n' "$SAIDA" | grep -E "^(pg_restore|ERROR|psql).*(error|ERROR)" | head -10
  echo "Um dump fiel de um banco saudável restaura sem violar as constraints do próprio dump —"
  echo "erros aqui indicam dados duplicados/órfãos no banco de origem (ou dump truncado)."
  exit 1
fi

TABELAS="$(docker exec "$NOME_TESTE" psql -U restaurar -d restaurar -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('investigacao','seguranca')")"
USUARIOS="$(docker exec "$NOME_TESTE" psql -U restaurar -d restaurar -Atc \
  "SELECT count(*) FROM seguranca.usr_tb01_usuarios" 2>/dev/null || echo 0)"

if [ "${TABELAS:-0}" -lt 50 ]; then
  echo "FALHA: restauração terminou com só $TABELAS tabelas de aplicação (<50) — dump truncado?"
  exit 1
fi
echo "OK: dump restaurável — $TABELAS tabelas de aplicação, $USUARIOS usuários."
