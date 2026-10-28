# Entrypoint do no-web (TLS da mídia direta — docs/no_midia_direta.md).
#
# Sem certificado em /etc/nginx/ssl o container fica aqui, dormindo e dizendo por quê: a porta
# 8443 existe mas ninguém atende, e o navegador cai no central — o mesmo comportamento de um nó
# sem mídia direta, que é o default honesto. Colocaram o certificado depois? Basta
# `docker compose -f docker-compose.no.yml restart no-web`.
if [ -f /etc/nginx/ssl/midia.crt ] && [ -f /etc/nginx/ssl/midia.key ]; then
	envsubst '${NO_ALLOWED_ORIGIN}' < /templates/no-midia.conf.template \
		> /etc/nginx/conf.d/default.conf || exit 1
	exec nginx -g 'daemon off;'
fi
echo 'midia direta: sem certificado em nginx/ssl (midia.crt/midia.key) - servico parado; ver docs/no_midia_direta.md'
exec sleep infinity
