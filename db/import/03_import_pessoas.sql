-- =====================================================================
-- Importação de DADOS_EXTRAIDOS.PES_TB01_PESSOAS (~220M linhas)
-- Rodar dentro do container, ex.:
--   docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
--       -f /db/import/03_import_pessoas.sql
-- Pressupõe db/data montado em /db (ver docker-compose.yml, volume ./db:/db:ro)
--
-- Carrega primeiro em uma staging table 100% texto (COPY nunca falha por
-- tipo/formato) e só depois converte a data com uma função "safe_to_date"
-- que devolve NULL em vez de abortar o import inteiro por causa de uma
-- data inválida em algum lugar no meio de 220M linhas.
--
-- O arquivo mistura fim-de-linha CRLF (cabeçalho) com LF (demais linhas),
-- o que faz o parser de CSV do Postgres travar com "unquoted newline
-- found in data". Por isso o COPY é feito via FROM PROGRAM, filtrando os
-- \r com `tr` no meio do caminho, sem precisar duplicar o arquivo de 14GB.
-- =====================================================================

SET search_path TO DADOS_EXTRAIDOS;

CREATE UNLOGGED TABLE STG_PES_TB01_PESSOAS (
    CPF        TEXT,
    NOME       TEXT,
    SEXO       TEXT,
    DATA_NASC  TEXT
);

COPY STG_PES_TB01_PESSOAS (CPF, NOME, SEXO, DATA_NASC)
    FROM PROGRAM 'tr -d ''\r'' < ''/db/data/Serasa/basic220M.csv'''
    WITH (FORMAT csv, DELIMITER '|', HEADER true, ENCODING 'UTF8');

CREATE OR REPLACE FUNCTION PG_TEMP.SAFE_TO_DATE(TXT TEXT) RETURNS DATE AS $$
BEGIN
    RETURN TO_DATE(TXT, 'DD/MM/YYYY');
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

INSERT INTO PES_TB01_PESSOAS (NU_CPF, NO_PESSOA, DS_SEXO, DT_NASCIMENTO)
SELECT
    NULLIF(CPF, ''),
    NULLIF(NOME, ''),
    NULLIF(SEXO, ''),
    PG_TEMP.SAFE_TO_DATE(DATA_NASC)
FROM STG_PES_TB01_PESSOAS;

DROP TABLE STG_PES_TB01_PESSOAS;

RESET search_path;
