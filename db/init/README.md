# db/init — schema do banco `analyser`

Estes arquivos descrevem o **estado atual** do banco, um arquivo por domínio, e não um
histórico de mudanças. `db/bootstrap/00_aplicar_migracoes.sh` aplica todos em ordem numérica
na primeira subida do Postgres (volume vazio); num banco que já existe eles não rodam.

## Organização

Um arquivo por assunto, e tudo que diz respeito a uma tabela mora junto: as colunas, as
constraints e os índices dela estão no mesmo `CREATE TABLE`, seguidos dos `CREATE INDEX`.
Não existe `ALTER TABLE` avulso aqui — coluna nova entra na definição da tabela.

A numeração é só ordem de execução, ditada pelas foreign keys: um arquivo só referencia
tabelas de arquivos anteriores. `01_schemas.sql` cria os quatro schemas
(`DADOS_REFERENCIA`, `DADOS_EXTRAIDOS`, `INVESTIGACAO`, `SEGURANCA`) e as extensões, e é
pré-requisito de todos os outros.

## Como mudar o schema

São **dois passos**, porque estes arquivos só rodam em volume vazio:

1. Edite o arquivo do domínio (coluna nova na tabela, índice novo abaixo dela). É isso que
   uma instalação nova vai receber.
2. Aplique a mesma mudança à mão nos bancos que já existem:

```bash
docker compose exec -T postgres psql -U analyser -d analyser -v ON_ERROR_STOP=1 -c "ALTER TABLE ..."
```

Um arquivo novo só se aparecer um domínio novo — aí ele entra com o próximo número livre,
depois dos arquivos cujas tabelas ele referencia.

Convenção de nomes: constraints e índices usam numeração global sequencial
(`FK207_...`, `IX104_...`, `CK03_...`) — pegue o próximo livre do conjunto todo, não do arquivo.

## Conferindo uma mudança

Para saber se o conjunto ainda nasce inteiro num banco zerado (o `ON_ERROR_STOP` do bootstrap
derruba a subida no primeiro erro, e o banco fica pela metade):

```bash
docker exec analyser-postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -q -c "DROP DATABASE IF EXISTS teste_init;" -c "CREATE DATABASE teste_init;" && cd /db/init && for f in $(ls *.sql | sort -t_ -k1,1n); do psql -U "$POSTGRES_USER" -d teste_init -v ON_ERROR_STOP=1 -q -f "$f" || exit 1; done && echo OK'
```
