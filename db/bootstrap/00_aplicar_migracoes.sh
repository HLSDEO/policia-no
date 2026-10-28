#!/bin/bash
# Primeira subida do Postgres (volume vazio). Duas fases:
#
#   Fase 1 — aplica db/init/*.sql em ordem numérica: o schema COMPLETO, um arquivo por domínio.
#   Fase 2 — registra db/migracoes/*.sql no ledger como 'bootstrap', SEM executá-los (a Fase 1
#            já criou tudo que eles fariam). Isso impede o runner (api/funcoes/migrar_utils.py)
#            de tentar reaplicar um delta num banco recém-nascido.
#
# Por que a Fase 1 existe: o entrypoint do Postgres roda os arquivos de /docker-entrypoint-initdb.d
# em ordem *lexicográfica*, e nela "100_" cai logo depois de "09_" (o dígito '0' vem antes do '_'
# na tabela ASCII). Da migração 100 em diante isso aplicava ALTER TABLE antes do CREATE TABLE
# correspondente; o psql do entrypoint roda com ON_ERROR_STOP, abortava ali e o banco nascia
# incompleto — a API subia saudável e quebrava no primeiro login. Aqui a ordem é numérica de
# verdade, então basta numerar a próxima (115, 200...) sem se preocupar com zeros à esquerda.
#
# Só esta pasta é montada em /docker-entrypoint-initdb.d; os .sql são lidos de /db/init e
# /db/migracoes, que o compose expõe pelo volume ./db:/db:ro.
#
# Sem `set -e`/`set -u`: quando o arquivo não está marcado como executável (o caso no Windows), o
# entrypoint faz `source` dele, e a opção vazaria para o shell do entrypoint. Erro é tratado na mão
# — o `exit 1` derruba a subida de propósito, para o banco não ficar pela metade em silêncio.

DIR_INIT=/db/init
DIR_DELTAS=/db/migracoes

psql_analyser() {
	PGHOST= PGHOSTADDR= psql \
		-v ON_ERROR_STOP=1 \
		--username "$POSTGRES_USER" \
		--dbname "${POSTGRES_DB:-$POSTGRES_USER}" \
		--no-password --no-psqlrc "$@"
}

# ---------------------------------------------------------------------------------------------------
# Fase 1 — schema completo (db/init/)
# ---------------------------------------------------------------------------------------------------

if ! ls "$DIR_INIT"/*.sql >/dev/null 2>&1; then
	echo "ERRO: nenhum arquivo em $DIR_INIT (o volume ./db:/db:ro do compose está montado?)" >&2
	exit 1
fi

# sort -t_ -k1,1n: separa no "_" e ordena o prefixo como número (01, 02, ... 99, 100, 114).
# O -k2 desempata pelo resto do nome, para a ordem ser sempre a mesma.
aplicadas=0
for arquivo in $(ls "$DIR_INIT"/*.sql | sed 's#.*/##' | sort -t_ -k1,1n -k2); do
	echo "init: $arquivo"
	if ! psql_analyser -f "$DIR_INIT/$arquivo"; then
		echo "ERRO: falha em $arquivo — o banco ficou incompleto." >&2
		echo "Corrija o arquivo e suba de novo com volume vazio (docker compose down -v)." >&2
		exit 1
	fi
	aplicadas=$((aplicadas + 1))
done
echo "init aplicados: $aplicadas"

# ---------------------------------------------------------------------------------------------------
# Fase 2 — registrar db/migracoes/ como baseline (sem executar)
# ---------------------------------------------------------------------------------------------------
#
# db/init/40_sistema_migracoes.sql (Fase 1) já criou SEGURANCA.SIS_TB01_MIGRACOES. Aqui só
# inserimos uma linha por delta, com DS_ORIGEM = 'bootstrap': o schema recém-criado já contém o
# efeito de todos eles, então reaplicá-los seria erro. ON CONFLICT DO NOTHING para o script ser
# seguro de rodar de novo.

if ls "$DIR_DELTAS"/*.sql >/dev/null 2>&1; then
	registrados=0
	for delta in $(ls "$DIR_DELTAS"/*.sql | sed 's#.*/##' | sort -t_ -k1,1n -k2); do
		numero=$(printf '%s' "$delta" | sed -E 's/^([0-9]+)_.*/\1/')
		case "$numero" in
			''|*[!0-9]*)
				echo "aviso: delta ignorado (prefixo não numérico): $delta" >&2
				continue ;;
		esac
		numero=$((10#$numero))  # 0007 -> 7, sem interpretar como octal
		hash=$(sha256sum "$DIR_DELTAS/$delta" | cut -d' ' -f1)
		if ! psql_analyser -c "INSERT INTO SEGURANCA.SIS_TB01_MIGRACOES
			(NU_MIGRACAO, NO_ARQUIVO, CO_HASH, DS_ORIGEM)
			VALUES ($numero, '$delta', '$hash', 'bootstrap')
			ON CONFLICT (NU_MIGRACAO) DO NOTHING"; then
			echo "ERRO: falha ao registrar baseline de $delta." >&2
			exit 1
		fi
		registrados=$((registrados + 1))
	done
	echo "migrações registradas como baseline: $registrados"
else
	echo "sem deltas em $DIR_DELTAS (nada a registrar)"
fi
