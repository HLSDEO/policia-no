-- ===================================================================================================================
-- Localizacoes, trajetos, antenas e geocodificacao
--
-- Tabelas: ERB_TB01_ANTENAS, GEO_TB01_ENDERECOS, GEO_TB02_CAMADAS, GEO_TB03_FEICOES,
--          GEO_TB04_ANEIS, JRN_TB01_TRAJETOS, LOC_TB01_LOCALIZACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- Antenas de telefonia registradas pelo aparelho (CellTower) — SÓ iPhone nos
-- extratos auditados. MCC/MNC/LAC/CID identificam a ERB; com a coordenada,
-- é rastro de deslocamento independente de GPS.
CREATE TABLE ERB_TB01_ANTENAS (
    ID_ANTENA       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    TP_REDE         VARCHAR(3000),  -- Type (GSM, LTE…)
    NU_MCC          VARCHAR(20),  -- código do país
    NU_MNC          VARCHAR(20),  -- código da operadora
    NU_LAC          VARCHAR(50),  -- área de localização
    NU_CID          VARCHAR(50),  -- identificador da célula
    VL_LATITUDE     NUMERIC(10,6),
    VL_LONGITUDE    NUMERIC(10,6),
    DT_REGISTRO     TIMESTAMP,
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK225_ERB_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX180_ERB_TB01_EXTRACAO ON ERB_TB01_ANTENAS (ID_EXTRACAO, DT_REGISTRO);
CREATE INDEX IF NOT EXISTS IX181_ERB_TB01_CELULA ON ERB_TB01_ANTENAS (NU_MCC, NU_MNC, NU_LAC, NU_CID);

CREATE TABLE GEO_TB01_ENDERECOS (
    ID_ENDERECO        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- coordenada arredondada a 5 casas (~1 m), o que agrupa o jitter de GPS de um mesmo ponto num só
    -- endereço (menos chamadas ao serviço externo). Todo o arredondamento é feito NO SQL (round(x,5)) —
    -- tanto ao gravar aqui quanto no JOIN com LOC_TB01 — para os dois lados usarem a MESMA regra de
    -- desempate (Python e JS arredondam diferente do PostgreSQL na 6ª casa = 5, o que quebrava o JOIN).
    VL_LATITUDE        NUMERIC(10,5) NOT NULL,
    VL_LONGITUDE       NUMERIC(10,5) NOT NULL,
    TX_ENDERECO        TEXT,  -- endereço formatado (NULL = geocodificado, porém sem resultado)
    NO_FONTE           VARCHAR(3000),  -- serviço usado (ex.: 'nominatim')
    DT_GEOCODIFICACAO  TIMESTAMP DEFAULT now(),
    CONSTRAINT UQ50_GEO_TB01_COORD UNIQUE (VL_LATITUDE, VL_LONGITUDE)
);

-- =====================================================================
-- Camadas geoespaciais importadas de shapefile (.shp), desenhadas por
-- cima do mapa da aba "Grafo" das localizações: perímetro de operação,
-- limite de bairro, setor censitário, área de risco.
--
-- O arquivo chega em qualquer projeção (SIRGAS 2000, UTM, WGS 84) e é
-- reprojetado para lat/lon na importação — daqui para baixo tudo já é
-- WGS 84, igual às coordenadas de LOC_TB01.
-- =====================================================================
CREATE TABLE GEO_TB02_CAMADAS (
    ID_CAMADA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO      BIGINT NOT NULL,
    NO_CAMADA         VARCHAR(200) NOT NULL,  -- nome exibido (padrão: nome do arquivo)
    NO_ARQUIVO        VARCHAR(300),  -- arquivo enviado, como veio do navegador
    CO_HASH           CHAR(64),  -- sha256 do conteúdo, para avisar de reimportação do mesmo arquivo
    NO_PROJECAO       VARCHAR(200),  -- projeção de origem, legível (ex.: 'SIRGAS 2000 / UTM zone 23S')
    TX_PROJECAO       TEXT,  -- WKT cru do .prj, guardado para auditoria da reprojeção
    QT_FEICOES        INTEGER NOT NULL DEFAULT 0,
    QT_VERTICES       INTEGER NOT NULL DEFAULT 0,  -- depois da simplificação
    VL_TOLERANCIA     NUMERIC(12,8),  -- tolerância de simplificação usada, em graus (NULL = geometria intacta)
    VL_LAT_MIN        NUMERIC(10,6),
    VL_LAT_MAX        NUMERIC(10,6),
    VL_LON_MIN        NUMERIC(10,6),
    VL_LON_MAX        NUMERIC(10,6),
    CO_COR            VARCHAR(9) NOT NULL DEFAULT '#f59e0b',  -- cor de desenho, '#rrggbb'
    DT_IMPORTACAO     TIMESTAMP NOT NULL DEFAULT now(),
    NO_IMPORTADA_POR  VARCHAR(200),
    CONSTRAINT FK250_GEO_TB02_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO)
);

CREATE INDEX IX240_GEO_TB02_INQUERITO ON GEO_TB02_CAMADAS (ID_INQUERITO, DT_IMPORTACAO DESC);

-- Uma feição = um polígono do shapefile (com os atributos do .dbf). TX_GEOMETRIA é o GeoJSON
-- que vai inteiro para o Leaflet desenhar; quem responde "este ponto está dentro?" é
-- GEO_TB04_ANEIS, abaixo. São a MESMA geometria guardada duas vezes de propósito: JSONB não
-- tem teste de contenção, e o tipo POLYGON do Postgres não representa buraco nem multipolígono.
CREATE TABLE GEO_TB03_FEICOES (
    ID_FEICAO     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CAMADA     BIGINT NOT NULL,
    NU_ORDEM      INTEGER NOT NULL,  -- posição no shapefile (1..n)
    NO_FEICAO     TEXT,  -- rótulo derivado dos atributos (NM_BAIRRO, NOME, NAME…)
    TX_ATRIBUTOS  JSONB,  -- registro do .dbf inteiro, como veio
    TX_GEOMETRIA  JSONB NOT NULL,  -- GeoJSON Polygon/MultiPolygon em WGS 84
    VL_LAT_MIN    NUMERIC(10,6) NOT NULL,
    VL_LAT_MAX    NUMERIC(10,6) NOT NULL,
    VL_LON_MIN    NUMERIC(10,6) NOT NULL,
    VL_LON_MAX    NUMERIC(10,6) NOT NULL,
    CONSTRAINT FK251_GEO_TB03_GEO_TB02 FOREIGN KEY (ID_CAMADA)
        REFERENCES GEO_TB02_CAMADAS (ID_CAMADA) ON DELETE CASCADE
);

CREATE INDEX IX241_GEO_TB03_CAMADA ON GEO_TB03_FEICOES (ID_CAMADA, NU_ORDEM);

-- Um anel = um contorno fechado, no tipo POLYGON nativo do Postgres (sem PostGIS). É ele que
-- permite "só as localizações dentro desta área" com o operador de contenção `poligono @> ponto`.
-- IN_BURACO separa o contorno externo do vazado (ilha dentro do polígono): dentro = está em
-- algum anel externo E em nenhum buraco.
--
-- A caixa (VL_*_MIN/MAX) está repetida aqui porque `@>` percorre todos os vértices do anel: sem
-- rejeitar antes pela caixa, filtrar 30 mil localizações contra uma camada de centenas de
-- polígonos vira minutos de CPU.
CREATE TABLE GEO_TB04_ANEIS (
    ID_ANEL     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_FEICAO   BIGINT NOT NULL,
    ID_CAMADA   BIGINT NOT NULL,  -- desnormalizado: o filtro por camada inteira não precisa do JOIN
    IN_BURACO   BOOLEAN NOT NULL DEFAULT false,
    VL_LAT_MIN  NUMERIC(10,6) NOT NULL,
    VL_LAT_MAX  NUMERIC(10,6) NOT NULL,
    VL_LON_MIN  NUMERIC(10,6) NOT NULL,
    VL_LON_MAX  NUMERIC(10,6) NOT NULL,
    PG_ANEL     POLYGON NOT NULL,  -- vértices em (x=longitude, y=latitude)
    CONSTRAINT FK252_GEO_TB04_GEO_TB03 FOREIGN KEY (ID_FEICAO)
        REFERENCES GEO_TB03_FEICOES (ID_FEICAO) ON DELETE CASCADE,
    CONSTRAINT FK253_GEO_TB04_GEO_TB02 FOREIGN KEY (ID_CAMADA)
        REFERENCES GEO_TB02_CAMADAS (ID_CAMADA) ON DELETE CASCADE
);

CREATE INDEX IX242_GEO_TB04_CAMADA ON GEO_TB04_ANEIS (ID_CAMADA, VL_LAT_MIN, VL_LAT_MAX);
CREATE INDEX IX243_GEO_TB04_FEICAO ON GEO_TB04_ANEIS (ID_FEICAO);

-- =====================================================================
-- Tela de Localizações (aba "Trajetos") — Journey.
-- Origem e destino são <model type="Location"> completos; guardamos as
-- coordenadas direto aqui porque o trajeto é a unidade de leitura.
-- =====================================================================
CREATE TABLE JRN_TB01_TRAJETOS (
    ID_TRAJETO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_APLICATIVO        VARCHAR(3000),  -- Source (Maps, Waze…)
    TP_TRAJETO           VARCHAR(3000),  -- Type (Route…)
    NO_TRAJETO           VARCHAR(3000),  -- Name
    VL_LAT_ORIGEM        NUMERIC(10,6),
    VL_LON_ORIGEM        NUMERIC(10,6),
    TX_ENDERECO_ORIGEM   TEXT,
    VL_LAT_DESTINO       NUMERIC(10,6),
    VL_LON_DESTINO       NUMERIC(10,6),
    TX_ENDERECO_DESTINO  TEXT,
    QT_PONTOS            BIGINT,  -- WayPoints (quantidade de pontos intermediários)
    DT_INICIO            TIMESTAMP,
    DT_FIM               TIMESTAMP,
    NO_CONTA             VARCHAR(3000),
    CD_STATUS_UFDR       VARCHAR(50),
    CONSTRAINT FK223_JRN_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX177_JRN_TB01_EXTRACAO ON JRN_TB01_TRAJETOS (ID_EXTRACAO, DT_INICIO);

-- 01 · localizações (Location do UFDR)
CREATE TABLE LOC_TB01_LOCALIZACOES (
    ID_LOCALIZACAO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   TEXT,  -- Source (Waze, Google Maps, etc.)
    DT_LOCALIZACAO  TIMESTAMP,
    VL_LATITUDE     NUMERIC(10,6),
    VL_LONGITUDE    NUMERIC(10,6),
    TX_ENDERECO     TEXT,  -- PositionAddress
    NO_CATEGORIA    TEXT,  -- Category
    TX_DESCRICAO    TEXT,  -- Description/Name
    CD_STATUS_UFDR  VARCHAR(50),
    VL_PRECISAO     NUMERIC(12,2),  -- GpsHorizontalAccuracy, em metros
    VL_ELEVACAO     NUMERIC(12,2),  -- Elevation, em metros
    TP_LOCALIZACAO  VARCHAR(3000),  -- Type
    NO_ORIGEM       VARCHAR(3000),  -- Origin
    DT_FIM          TIMESTAMP,  -- EndTime (permanência num ponto)
    NO_CONTA        VARCHAR(3000),
    CONSTRAINT FK48_LOC_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX20_LOC_TB01_DT_LOCALIZACAO  ON LOC_TB01_LOCALIZACOES (DT_LOCALIZACAO);
CREATE INDEX IX143_LOC_TB01_COORD_ARRED ON LOC_TB01_LOCALIZACOES
    (round(VL_LATITUDE, 4), round(VL_LONGITUDE, 4))
    WHERE VL_LATITUDE IS NOT NULL AND VL_LONGITUDE IS NOT NULL;
-- Localizações: ORDER BY lo.dt_localizacao DESC NULLS LAST, lo.id_localizacao
-- (ver _ORDENACOES_LOCALIZACAO; a ordem "antigos" usa ASC e lê o mesmo índice
-- de trás pra frente)
CREATE INDEX IF NOT EXISTS IX200_LOC_TB01_EXTRACAO_DATA
    ON LOC_TB01_LOCALIZACOES (ID_EXTRACAO, DT_LOCALIZACAO DESC NULLS LAST, ID_LOCALIZACAO);

RESET search_path;
