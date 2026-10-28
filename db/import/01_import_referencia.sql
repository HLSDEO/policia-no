-- =====================================================================
-- Importação das tabelas de domínio (DADOS_REFERENCIA)
-- Origem: db/data/Detran/Referencias/*.csv
-- Rodar dentro do container, ex.:
--   docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
--       -f /db/import/01_import_referencia.sql
-- Pressupõe db/data montado em /db (ver docker-compose.yml, volume ./db:/db:ro)
-- =====================================================================

SET search_path TO DADOS_REFERENCIA;

COPY VEI_TB01_MARCA (ID_MARCA, NO_MARCA)
    FROM '/db/data/Detran/Referencias/marca.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB02_SEGMENTO (ID_SEGMENTO, NO_SEGMENTO)
    FROM '/db/data/Detran/Referencias/segmento_veiculo.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB03_SUB_SEGMENTO (ID_SUB_SEGMENTO, ID_SEGMENTO, NO_SUB_SEGMENTO)
    FROM '/db/data/Detran/Referencias/sub_segmento_veiculo.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

-- Correção de dado: modelo.csv referencia fk_sub_segmento=207, que não existe
-- em sub_segmento_veiculo.csv (dado sujo do vazamento). As linhas afetadas usam
-- fk_segmento=7 (Implemento Rodoviario). Criamos um registro "sentinela" para
-- preservar a integridade referencial, no mesmo padrão já usado no dataset
-- (ex.: id 0 = "Desconhecido"/"Não Identificado").
INSERT INTO VEI_TB03_SUB_SEGMENTO (ID_SUB_SEGMENTO, ID_SEGMENTO, NO_SUB_SEGMENTO)
VALUES (207, 7, 'NÃO IDENTIFICADO')
ON CONFLICT (ID_SUB_SEGMENTO) DO NOTHING;

-- vl_media_preco vem como texto literal "NULL" em 55 linhas (em vez de vazio);
-- normaliza para vazio via sed para não perder o NULL-por-vazio das demais colunas
COPY VEI_TB04_GRUPO_MODELO (ID_GRUPO_MODELO, NO_GRUPO_MODELO, VL_MEDIA_PRECO, NU_VERSION)
    FROM PROGRAM 'sed ''s/;NULL;/;;/g'' < ''/db/data/Detran/Referencias/grupo_modelo.csv'''
    WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB05_MODELO (ID_MODELO, NO_MODELO, ID_MARCA, ID_SEGMENTO, ID_SUB_SEGMENTO, ID_GRUPO_MODELO, VL_MEDIA_PRECO, NU_VERSION)
    FROM '/db/data/Detran/Referencias/modelo.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB06_MUNICIPIO (ID_MUNICIPIO, NO_MUNICIPIO, SG_UF)
    FROM '/db/data/Detran/Referencias/municipio.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB07_COMBUSTIVEL (ID_COMBUSTIVEL, NO_COMBUSTIVEL)
    FROM '/db/data/Detran/Referencias/combustivel.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB08_COR (ID_COR, NO_COR)
    FROM '/db/data/Detran/Referencias/cor.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB09_CARROCERIA (ID_CARROCERIA, NO_CARROCERIA)
    FROM '/db/data/Detran/Referencias/carroceria.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB10_ESPECIE_VEICULO (ID_ESPECIE_VEICULO, NO_ESPECIE_VEICULO)
    FROM '/db/data/Detran/Referencias/especie_veiculo.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB11_TIPO_VEICULO (ID_TIPO_VEICULO, NO_TIPO_VEICULO)
    FROM '/db/data/Detran/Referencias/tipo_veiculo.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB12_NACIONALIDADE (ID_NACIONALIDADE, NO_NACIONALIDADE)
    FROM '/db/data/Detran/Referencias/nacionalidade.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB13_REGIAO (ID_REGIAO, NO_REGIAO)
    FROM '/db/data/Detran/Referencias/regiao.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB14_RESTRICAO (ID_RESTRICAO, NO_RESTRICAO)
    FROM '/db/data/Detran/Referencias/restricoes.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

COPY VEI_TB15_TIPO_PESSOA (ID_TIPO_PESSOA, NO_TIPO_PESSOA)
    FROM '/db/data/Detran/Referencias/tipo_pessoa.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'UTF8');

RESET search_path;
