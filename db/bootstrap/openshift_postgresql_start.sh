#!/bin/bash
# ===================================================================================================================
# Bootstrap do schema do Analyser na imagem rhel9/postgresql-16 (OpenShift).
#
# Equivalente em imagem do OpenShift do db/bootstrap/00_aplicar_migracoes.sh do compose. O
# entrypoint da imagem sclorg (run-postgresql) SOURCE este arquivo de
# /opt/app-root/src/postgresql-start/ com o servidor temporário JÁ NO AR e antes de
# `exec postgres` — o momento exato para criar schema num volume recém-nascido.
#
# Diferenças para o script do compose:
#  - usuário/banco vêm de POSTGRESQL_USER/POSTGRESQL_DATABASE (a imagem RHEL não usa
#    POSTGRES_*); o banco é criado pelo entrypoint com `createdb --owner=$POSTGRESQL_USER`,
#    então o schema nasce de propriedade do MESMO usuário com que a API conecta;
#  - extensões (pg_trgm, fuzzystrmatch, pgcrypto, vector) são criadas antes como SUPERUSUÁRIO
#    no banco da aplicação — `vector` não é marked trusted em todas as versões e a API roda
#    como usuário comum. Os CREATE EXTENSION IF NOT EXISTS dos .sql de init apenas
#    "notice-pulam" quando a extensão já existe;
#  - GUARDA DE IDEMPOTÊNCIA: roda Fase 1/2 só quando SEGURANCA.SIS_TB01_MIGRACOES ainda não
#    existe. O hook é SOURCED (source file) pelo entrypoint: sem a guarda, um `exit 1` num
#    boot comum derrubaria o pod. Toda saída por erro aqui é deliberada — melhor crash-loop
#    com causa no log do que banco pela metade em silêncio.
#
# Cuidados por ser sourced: roda sob `set -eu` do entrypoint — sem variáveis não definidas,
# sem `set -` globais novos, exit só em erro fatal (derruba o boot de propósito).
# ===================================================================================================================

_analyser_bootstrap_schema() {
	local dir_init=/opt/postgresql-init/db/init
	local dir_deltas=/opt/postgresql-init/db/migracoes
	local db="${POSTGRESQL_DATABASE:-}"
	local dbuser="${POSTGRESQL_USER:-}"

	if [ -z "$db" ] || [ -z "$dbuser" ]; then
		echo "ERRO: POSTGRESQL_DATABASE/POSTGRESQL_USER ausentes — nada a bootstrapar." >&2
		exit 1
	fi

	_analyser_psql() {
		psql -v ON_ERROR_STOP=1 --username "$dbuser" --dbname "$db" \
			--no-password --no-psqlrc "$@"
	}

	# Guarda: schema já aplicado? O ledger é o marcador. to_regclass porque os schemas do
	# init nascem MINÚSCULOS (identificadores sem aspas são case-folded pelo Postgres) e
	# qualquer igualdade com 'SEGURANCA' em information_schema nunca casaria — a guarda
	# passaria a reaplicar o init em todo restart do pod.
	local existente
	existente=$(psql --username "$dbuser" --dbname "$db" --no-password --no-psqlrc -tA \
		-c "SELECT COALESCE(to_regclass('seguranca.sis_tb01_migracoes')::text, '')" 2>/dev/null || echo "")
	if [ -n "$existente" ]; then
		echo "analyser-bootstrap: schema já aplicado — nada a fazer."
		return 0
	fi

	echo "analyser-bootstrap: banco '$db' recém-nascido, aplicando db/init ..."

	# Extensoes como superusuario (socket local, PGUSER=postgres do environment da imagem).
	local ext
	for ext in pg_trgm fuzzystrmatch pgcrypto vector; do
		if ! psql --username postgres --dbname "$db" --no-password --no-psqlrc \
			-v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS $ext WITH SCHEMA public"; then
			echo "ERRO: CREATE EXTENSION $ext falhou. A imagem rhel9/postgresql-16 atual ja" >&2
			echo "traz o pacote pgvector; se o tag e antigo, atualize-o (rpm -q pgvector)." >&2
			exit 1
		fi
	done

	if ! ls "$dir_init"/*.sql >/dev/null 2>&1; then
		echo "ERRO: nenhum arquivo em $dir_init (a imagem foi montada sem /opt/postgresql-init?)" >&2
		exit 1
	fi

	# Ordem NUMERICA (01, 02, ... 50) — a ordem lexicográfica do initdb.d do compose quebraria
	# da migração 100 em diante (ver comentário no 00_aplicar_migracoes.sh original).
	local aplicadas=0 arquivo
	for arquivo in $(ls "$dir_init"/*.sql | sed 's#.*/##' | sort -t_ -k1,1n -k2); do
		echo "analyser-bootstrap init: $arquivo"
		if ! _analyser_psql -f "$dir_init/$arquivo"; then
			echo "ERRO: falha em $arquivo — o banco ficou incompleto." >&2
			echo "Corrija e remova o PVC (oc delete pvc postgres-data) para recriar do zero." >&2
			exit 1
		fi
		aplicadas=$((aplicadas + 1))
	done
	echo "analyser-bootstrap: init aplicados: $aplicadas"

	# Fase 2 — registrar db/migracoes/ como baseline no ledger (DS_ORIGEM='bootstrap'), sem
	# executá-los: o schema recém-criado já contém o efeito de todos. Impede o runner da API
	# (migrar_utils.py) de reaplicar um delta num banco recém-nascido.
	if ls "$dir_deltas"/*.sql >/dev/null 2>&1; then
		local registrados=0 delta numero hash
		for delta in $(ls "$dir_deltas"/*.sql | sed 's#.*/##' | sort -t_ -k1,1n -k2); do
			numero=$(printf '%s' "$delta" | sed -E 's/^([0-9]+)_.*/\1/')
			case "$numero" in
				''|*[!0-9]*)
					echo "aviso: delta ignorado (prefixo não numérico): $delta" >&2
					continue ;;
			esac
			numero=$((10#$numero))
			# hash sobre o conteúdo SEM \\r: o build pode carregar os .sql com CRLF (checkout
			# Windows) enquanto o runner da API lê o bytes do /app/db/migracoes da imagem — se a
			# baseline registrar o hash com \\r, todo startup vira um coro de "migração editada".
			hash=$(sed 's/\r$//' "$dir_deltas/$delta" | sha256sum | cut -d' ' -f1)
			if ! _analyser_psql -c "INSERT INTO SEGURANCA.SIS_TB01_MIGRACOES
				(NU_MIGRACAO, NO_ARQUIVO, CO_HASH, DS_ORIGEM)
				VALUES ($numero, '$delta', '$hash', 'bootstrap')
				ON CONFLICT (NU_MIGRACAO) DO NOTHING"; then
				echo "ERRO: falha ao registrar baseline de $delta." >&2
				exit 1
			fi
			registrados=$((registrados + 1))
		done
		echo "analyser-bootstrap: migrações registradas como baseline: $registrados"
	else
		echo "analyser-bootstrap: sem deltas em $dir_deltas (nada a registrar)"
	fi
}

_analyser_bootstrap_schema
