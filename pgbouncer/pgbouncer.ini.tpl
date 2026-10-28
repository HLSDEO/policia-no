# Template do PgBouncer — os placeholders __DB__/__USUARIO__/__SENHA__ são trocados no start por
# pgbouncer/entrar.sh (as credenciais vêm do environment do compose, mesmo .env do Postgres).
#
# O QUE ISTO RESOLVE: cada processo da API traz o seu ConnectionPool (funcoes/db_utils.py), e
# N réplicas x pool_max podem passar do max_connections do Postgres — o erro clássico de escala.
# O PgBouncer multiplica as conexões de cliente (milhares de usuários) sobre um pool pequeno e
# fixo de conexões reais ao banco.
#
# POR QUE pool_mode = session: este app aplica timeouts de SESSÃO via set_config por conexão
# (db_utils._aplicar_timeouts) e tem transações de ingestão que duram minutos; em transaction
# mode cada uma dessas coisas precisaria de auditoria. Em session mode o pooler é transparente:
# a conexão ao banco é TÃO sessional quanto seria sem ele. O pool de conexões longas (ingestão/
# migração) nem passa por aqui — vai direto ao Postgres (db_utils._criar_pool_longa).
#
# POR QUE auth_type = any: o pgbouncer não consegue verificar senha SCRAM via auth_query sem
# extensão no servidor (e trust falha no handshake com libpq moderno neste build 1.22 - visto
# em teste local), e montar userlist com hash exige passo manual em todo deploy. A porta 6432
# NÃO é publicada no host: só containers da rede interna do compose alcançam o pgbouncer,
# e esses mesmos containers já alcançavam o Postgres direto com as credenciais do .env montado
# — a superfície de ataque não muda. A autenticação REAL ao Postgres continua acontecendo
# (linha __DB__ abaixo, com a senha verdadeira).
#
# ATENCAO AO FORMATO: os parametros vivem na secao [pgbouncer]; [databases] e a outra secao
# nomeada (tudo que vier depois dela pertence ao banco, nao ao pooler).

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = any

pool_mode = session

# Clientes: um por aba ativa da API, e ainda assim é uma fração da conexão real ao banco.
max_client_conn = 1000
# Conexões REAIS ao Postgres pelo pooler. Tem que deixar folga sob max_connections do banco
# (PG_MAX_CONNECTIONS no .env) para: pool de conexões longas da API (direto, 8), conexões
# dedicadas de liderança, o sidecar de backup, psql à mão e o runner de migração. Com o default
# de fábrica (max_connections=100), 60+8+~10 de folga fecha com sobra.
#
# Em pool_mode = session o pooler amarra 1 conexão de banco por cliente enquanto a sessão vive -
# logo, o teto bom é API_WORKERS x replicase x DB_POOL_MAX (funcoes/db_utils.py) <= este valor.
# Se passar, a outra metade dos clientes da API fica na fila do pooler até query_wait_timeout
# (visto em teste de carga: 6 processos x pool 20 sobre 60 conexões = 30% de erro com o banco
# ocioso). Ajusta-se por PGBOUNCE_POOL_SIZE no .env; sem a variável fica o default de fábrica.
default_pool_size = __POOL_SIZE__
min_pool_size = 5

server_lifetime = 3600
server_idle_timeout = 600
# Cliente esperando mais que isso por uma conexão real = o gargalo virou emergência; devolve
# erro logo em vez de deixar a fila crescer (o 503 honesto do PoolTimeout é preferível).
query_wait_timeout = 120

# Em session mode não há reset entre transações; vazio explícito para que DISCARD ALL nunca
# desfaz os set_config de sessão que a API aplica por conexão.
server_reset_query =

ignore_startup_parameters = extra_float_digits,options
# O usuário do container não escreve em /var/run: socket em /tmp. O nome do parâmetro neste
# build (1.22) é unix_socket_dir (o plural "directories" é rejeitado por ele).
unix_socket_dir = /tmp
# logfile vazio = log no stderr (o `docker logs` do compose é o destino).
logfile =

[databases]
__DB__ = host=postgres port=5432 dbname=__DB__ user=__USUARIO__ password=__SENHA__
