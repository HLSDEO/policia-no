#!/bin/sh
# Materializa /etc/pgbouncer/pgbouncer.ini.tpl em um ini real, trocando os placeholders pelas
# credenciais que o compose injetou como variáveis de ambiente (POSTGRES_USER/PASSWORD/DB, as
# mesmas do serviço postgres — uma só fonte de verdade). O resultado vive em /tmp porque o
# container roda como o usuário "pgbouncer" e não escreve em /etc.
#
# awk com index/substr em vez de sed: a senha pode conter &, ", \, |, qualquer coisa — qualquer
# esquema baseado em sed/expression ENCARA o problema de quoting do substituto (e aqui já
# encarou). Os placeholders são literais (__DB__/__USUARIO__/__SENHA__) e a substituição é
# troca de string pura, sem interpretação. (Aceita-se a premissa óbvia de que a senha não
# contém os próprios placeholders.)
set -e

: "${POSTGRES_USER:?POSTGRES_USER ausente no environment}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD ausente no environment}"
: "${POSTGRES_DB:?POSTGRES_DB ausente no environment}"

# default_pool_size do pooler (ver comentario no template). Nao numerico ou ausente = fabrica.
: "${PGBOUNCE_POOL_SIZE:=60}"
case "$PGBOUNCE_POOL_SIZE" in ''|*[!0-9]*) PGBOUNCE_POOL_SIZE=60 ;; esac

awk '
function subst(line, token, val,   i, out) {
    out = ""
    while ((i = index(line, token)) > 0) {
        out = out substr(line, 1, i - 1) val
        line = substr(line, i + length(token))
    }
    return out line
}
{
    $0 = subst($0, "__POOL_SIZE__", ENVIRON["PGBOUNCE_POOL_SIZE"])
    $0 = subst($0, "__DB__",      ENVIRON["POSTGRES_DB"])
    $0 = subst($0, "__USUARIO__", ENVIRON["POSTGRES_USER"])
    $0 = subst($0, "__SENHA__",   ENVIRON["POSTGRES_PASSWORD"])
    print
}
' /etc/pgbouncer/pgbouncer.ini.tpl > /tmp/pgbouncer.ini

# foreground por padrão do pgbouncer (sem flags) — o container é o supervisor
exec pgbouncer /tmp/pgbouncer.ini
