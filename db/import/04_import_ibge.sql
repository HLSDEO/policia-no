-- =====================================================================
-- Importação de DADOS_REFERENCIA (localidades IBGE)
-- Rodar dentro do container, ex.:
--   docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
--       -f /db/import/04_import_ibge.sql
-- Pressupõe db/data montado em /db (ver docker-compose.yml, volume ./db:/db:ro)
--
-- Os arquivos são JSON (resposta crua da API do IBGE), não CSV — por isso
-- não usamos COPY aqui. Cada arquivo é lido inteiro com pg_read_file() e
-- expandido com jsonb_array_elements() direto no INSERT.
-- =====================================================================

SET search_path TO DADOS_REFERENCIA;

-- 01 · país (catálogo mundial, sem FK com o restante)
INSERT INTO IBGE_TB01_PAIS (
    CD_PAIS_M49, SG_PAIS, SG_PAIS_ALPHA3, NO_PAIS, NO_PAIS_EN,
    QT_AREA_KM2, NO_REGIAO_MUNDIAL, NO_SUB_REGIAO_MUNDIAL, NO_CAPITAL, TX_HISTORICO
)
SELECT
    (elem #>> '{id,M49}')::INTEGER,
    elem #>> '{id,ISO-3166-1-ALPHA-2}',
    elem #>> '{id,ISO-3166-1-ALPHA-3}',
    elem #>> '{nome,abreviado}',
    elem #>> '{nome,abreviado-EN}',
    NULLIF(REPLACE(elem #>> '{area,total}', ',', '.'), '')::NUMERIC,
    elem #>> '{localizacao,regiao,nome}',
    elem #>> '{localizacao,sub-regiao,nome}',
    elem #>> '{governo,capital,nome}',
    elem ->> 'historico'
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/paises.json')::jsonb) elem
WHERE (elem #>> '{id,M49}') IS NOT NULL;

-- 02 · região
INSERT INTO IBGE_TB02_REGIAO (ID_REGIAO, SG_REGIAO, NO_REGIAO)
SELECT (elem->>'id')::SMALLINT, elem->>'sigla', elem->>'nome'
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/regioes.json')::jsonb) elem;

-- 03 · UF
INSERT INTO IBGE_TB03_UF (ID_UF, SG_UF, NO_UF, ID_REGIAO)
SELECT (elem->>'id')::SMALLINT, elem->>'sigla', elem->>'nome', (elem#>>'{regiao,id}')::SMALLINT
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/estados.json')::jsonb) elem;

-- 04 · mesorregião
INSERT INTO IBGE_TB04_MESORREGIAO (ID_MESORREGIAO, NO_MESORREGIAO, ID_UF)
SELECT (elem->>'id')::INTEGER, elem->>'nome', (elem#>>'{UF,id}')::SMALLINT
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/mesorregioes.json')::jsonb) elem;

-- 05 · microrregião
INSERT INTO IBGE_TB05_MICRORREGIAO (ID_MICRORREGIAO, NO_MICRORREGIAO, ID_MESORREGIAO)
SELECT (elem->>'id')::INTEGER, elem->>'nome', (elem#>>'{mesorregiao,id}')::INTEGER
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/microrregioes.json')::jsonb) elem;

-- 06 · região intermediária
INSERT INTO IBGE_TB06_REGIAO_INTERMEDIARIA (ID_REGIAO_INTERMEDIARIA, NO_REGIAO_INTERMEDIARIA, ID_UF)
SELECT (elem->>'id')::INTEGER, elem->>'nome', (elem#>>'{UF,id}')::SMALLINT
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/regioes-intermediarias.json')::jsonb) elem;

-- 07 · região imediata
INSERT INTO IBGE_TB07_REGIAO_IMEDIATA (ID_REGIAO_IMEDIATA, NO_REGIAO_IMEDIATA, ID_REGIAO_INTERMEDIARIA)
SELECT (elem->>'id')::INTEGER, elem->>'nome', (elem#>>'{regiao-intermediaria,id}')::INTEGER
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/regioes-imediatas.json')::jsonb) elem;

-- 08 · município
INSERT INTO IBGE_TB08_MUNICIPIO (ID_MUNICIPIO, NO_MUNICIPIO, ID_MICRORREGIAO, ID_REGIAO_IMEDIATA)
SELECT (elem->>'id')::INTEGER, elem->>'nome',
       (elem#>>'{microrregiao,id}')::INTEGER,
       (elem#>>'{regiao-imediata,id}')::INTEGER
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/municipios.json')::jsonb) elem;

-- 09 · distrito
INSERT INTO IBGE_TB09_DISTRITO (ID_DISTRITO, NO_DISTRITO, ID_MUNICIPIO, ID_REGIAO_IMEDIATA)
SELECT (elem->>'id')::BIGINT, elem->>'nome',
       (elem#>>'{municipio,id}')::INTEGER,
       (elem#>>'{regiao-imediata,id}')::INTEGER
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/distritos.json')::jsonb) elem;

-- 10 · subdistrito
INSERT INTO IBGE_TB10_SUBDISTRITO (ID_SUBDISTRITO, NO_SUBDISTRITO, ID_DISTRITO)
SELECT (elem->>'id')::BIGINT, elem->>'nome', (elem#>>'{distrito,id}')::BIGINT
FROM jsonb_array_elements(pg_read_file('/db/data/IBGE/subdistritos.json')::jsonb) elem;

RESET search_path;
