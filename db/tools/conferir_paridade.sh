#!/usr/bin/env bash
#
# conferir_paridade.sh — a rede de segurança dos "dois toques" do schema (ver db/migracoes/README.md).
#
# Toda mudança de schema entra em db/init/NN_dominio.sql (declarativo) E em db/migracoes/NNNN_x.sql
# (ALTER). Esquecer um dos dois faz a instalação NOVA divergir da ATUALIZADA. Este script pega isso:
#
#   banco A  ←  db/init/*.sql  atual                              (como nasce uma instalação nova)
#   banco B  ←  db/init/*.sql da tag anterior  +  db/migracoes/*  (como um banco existente chega aqui)
#   diff  pg_dump --schema-only A  vs  B   →  precisa ser vazio
#
# Uso (com o container analyser-postgres no ar):
#   db/tools/conferir_paridade.sh [tag-anterior]
# tag-anterior default: a última tag antes de HEAD (git describe HEAD^).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

PG=analyser-postgres
U="$(docker exec "$PG" printenv POSTGRES_USER)"
TAG_ANT="${1:-$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)}"
[ -n "$TAG_ANT" ] || { echo "não achei a tag anterior; passe como argumento"; exit 2; }

# A comparação só faz sentido contra uma tag que JÁ tinha o sistema de migrações — senão a
# "divergência" é só a evolução normal do schema entre a tag e HEAD, sem deltas porque ainda não
# havia deltas. A primeira release com db/migracoes/ é o piso.
if ! git cat-file -e "$TAG_ANT:db/migracoes/README.md" 2>/dev/null; then
	echo "aviso: $TAG_ANT é anterior ao sistema de migrações (sem db/migracoes/) — nada a conferir."
	echo "       rode de novo quando houver uma tag mais nova para comparar."
	exit 0
fi
echo "comparando db/init atual  vs  db/init@$TAG_ANT + db/migracoes"

# `sort -V` e nao `sort -t_ -k1,1n -k2`: aquele separava por "_", e o campo 1 virava
# "db/init/20" — texto, nao numero —, entao o -n nao fazia nada e a ordem acabava sendo
# ALFABETICA pelo dominio. Na pratica 20_arquivos.sql rodava antes de 09_extracoes.sql e o
# script morria em "relation inq_tb02_extracoes does not exist", nunca chegando a comparar nada.
# O -V compara as sequencias de digitos numericamente, que e o que o prefixo NN_ pede.
aplicar_dir_atual() { # <db>  — db/init/ do working tree
	for f in $(ls db/init/*.sql | sort -V); do
		docker exec -i "$PG" psql -U "$U" -d "$1" -v ON_ERROR_STOP=1 -q < "$f"
	done
}

# Os deltas que um banco instalado NA TAG ainda nao recebeu: os que existem no working tree e nao
# existiam na tag. Nao e "todos os deltas".
#
# A razao esta em db/bootstrap/00_aplicar_migracoes.sh: numa instalacao nova o db/init/ cria o
# schema completo e os deltas daquele momento sao REGISTRADOS NO LEDGER SEM SEREM EXECUTADOS —
# porque o init ja contem o que eles fariam. Um banco nascido na tag, portanto, ja tem os deltas
# ate a tag embutidos; reaplica-los e que morria em "column ... already exists", e o script parava
# ali sem nunca comparar nada.
deltas_apos_tag() {
	local na_tag
	na_tag="$(git ls-tree -r --name-only "$TAG_ANT" -- db/migracoes/ | grep '\.sql$' || true)"
	for f in $(ls db/migracoes/*.sql 2>/dev/null | sort -V); do
		grep -qxF "$f" <<<"$na_tag" || echo "$f"
	done
}

aplicar_baseline() { # <db>  — db/init/ da tag anterior + os deltas POSTERIORES a ela
	# grep '\.sql$': o ls-tree traz o diretorio inteiro, e o db/init/README.md ia parar no psql
	for f in $(git ls-tree -r --name-only "$TAG_ANT" -- db/init/ | grep '\.sql$' | sort -V); do
		git show "$TAG_ANT:$f" | docker exec -i "$PG" psql -U "$U" -d "$1" -v ON_ERROR_STOP=1 -q
	done
	local novos
	novos="$(deltas_apos_tag)"
	if [ -z "$novos" ]; then
		echo "  (nenhum delta novo desde $TAG_ANT — a comparacao vale como regressao do db/init/)"
	else
		echo "  deltas aplicados sobre $TAG_ANT: $(tr '\n' ' ' <<<"$novos")"
	fi
	for f in $novos; do
		docker exec -i "$PG" psql -U "$U" -d "$1" -v ON_ERROR_STOP=1 -q < "$f"
	done
}

for db in paridade_a paridade_b; do
	docker exec "$PG" psql -U "$U" -d postgres -q -c "DROP DATABASE IF EXISTS $db;" -c "CREATE DATABASE $db;"
done

aplicar_dir_atual paridade_a
aplicar_baseline  paridade_b

# grep -v restrict: o pg_dump 17+ emite as meta-instrucoes restrict/unrestrict com um token
# ALEATORIO a cada execucao — sem tirar essas linhas o diff NUNCA fecha, por mais identicos
# que os dois schemas sejam.
A="$(docker exec "$PG" pg_dump -U "$U" --schema-only --no-owner --no-privileges paridade_a | grep -v restrict)"
B="$(docker exec "$PG" pg_dump -U "$U" --schema-only --no-owner --no-privileges paridade_b | grep -v restrict)"

for db in paridade_a paridade_b; do
	docker exec "$PG" psql -U "$U" -d postgres -q -c "DROP DATABASE $db;"
done

if diff <(printf '%s\n' "$A") <(printf '%s\n' "$B"); then
	echo "OK — schema idêntico pelos dois caminhos"
else
	echo
	echo "DIVERGÊNCIA — falta o delta em db/migracoes/ para uma mudança do db/init/ (ou vice-versa)"
	exit 1
fi
