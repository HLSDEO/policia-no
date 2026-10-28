-- ===================================================================================================================
-- Veiculos do vazamento do Detran
--
-- Tabelas: VEI_TB01_DETRAN
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO DADOS_EXTRAIDOS, PUBLIC;

CREATE TABLE VEI_TB01_DETRAN (
    ID_VEICULO                  BIGINT PRIMARY KEY,  -- id
    DT_ATUALIZACAO              TIMESTAMP,  -- data_atualiacao
    NU_CHASSI                   VARCHAR(30),  -- chassi
    CO_PLACA                    VARCHAR(10),  -- placa
    NU_DOC_FATURADO             VARCHAR(20),  -- faturado (CPF/CNPJ)
    NU_ANO_FABRICACAO           SMALLINT,  -- ano_fabricacao
    ID_MUNICIPIO                INTEGER,  -- municipio
    SG_UF_PLACA                 CHAR(2),  -- uf_placa
    ID_MODELO                   INTEGER,  -- marca_modelo
    ID_COMBUSTIVEL              SMALLINT,  -- combustivel
    NU_POTENCIA                 INTEGER,  -- potencia
    QT_CAPACIDADE_CARGA         INTEGER,  -- capacidade_carga
    ID_NACIONALIDADE            SMALLINT,  -- nacionalidade
    CO_LINHA                    BIGINT,  -- linha
    CO_CARROCERIA               VARCHAR(20),  -- carroceria
    TX_CAIXA_CAMBIO             VARCHAR(30),  -- caixa_cambio
    TX_EIXO_TRASEIRO_DIF        VARCHAR(30),  -- eixo_traseiro_dif
    TX_TERCEIRO_EIXO            VARCHAR(30),  -- terceiro_eixo
    NU_MOTOR                    VARCHAR(40),  -- motor
    ID_TIPO_PESSOA_FATURADO     SMALLINT,  -- tipo_doc_faturado
    SG_UF_FATURADO              CHAR(2),  -- uf_faturado
    ID_TIPO_PESSOA_PROP         SMALLINT,  -- tipo_doc_prop
    NU_ANO_MODELO               SMALLINT,  -- ano_modelo
    ID_TIPO_VEICULO             SMALLINT,  -- tipo_veiculo
    ID_ESPECIE_VEICULO          SMALLINT,  -- especie_veiculo
    ID_CARROCERIA               INTEGER,  -- tipo_carroceria
    ID_COR                      SMALLINT,  -- cor_veiculo
    QT_PASSAGEIRO               SMALLINT,  -- quantidade_passageiro
    IN_SITUACAO_CHASSI          CHAR(1),  -- situacao_chassi
    QT_EIXOS                    SMALLINT,  -- eixos
    CO_TIPO_MONTAGEM            SMALLINT,  -- tipo_montagem
    ID_TIPO_PESSOA_IMPORTADORA  SMALLINT,  -- tipo_doc_importadora
    NU_DOC_IMPORTADORA          VARCHAR(20),  -- ident_importadora
    NU_DI                       VARCHAR(20),  -- di
    NU_REGISTRO_DI              VARCHAR(20),  -- registro_di
    CO_UNIDADE_LOCAL_SRF        VARCHAR(10),  -- unidade_local_srf
    DT_ULTIMA_ATUALIZACAO       TIMESTAMP,  -- ultima_atualizacao
    ID_RESTRICAO_1              SMALLINT,  -- restricao_1
    ID_RESTRICAO_2              SMALLINT,  -- restricao_2
    ID_RESTRICAO_3              SMALLINT,  -- restricao_3
    ID_RESTRICAO_4              SMALLINT,  -- restricao_4
    DT_LIMITE_RESTRICAO_TRIB    TIMESTAMP,  -- limite_restricao_trib (data-limite, não valor: campo traz datas, ex. sentinela 2078-12-31 = sem prazo)
    QT_CILINDRADAS              INTEGER,  -- cilindradas
    QT_CAP_MAXIMA_TRACAO        INTEGER,  -- cap_maxima_tracao
    QT_PESO_BRUTO_TOTAL         INTEGER,  -- peso_bruto_total
    IN_SITUACAO_VEICULO         CHAR(1),  -- situacao_veiculo
    CO_PLACA_MODELO_ANTIGO      VARCHAR(10),  -- placa_modelo_antigo
    CO_PLACA_MODELO_NOVO        VARCHAR(10),  -- placa_modelo_novo
    IN_PLACA_NOVA               BOOLEAN,  -- placa_nova
    CONSTRAINT FK06_VEI_TB01_VEI_TB06 FOREIGN KEY (ID_MUNICIPIO)
        REFERENCES DADOS_REFERENCIA.VEI_TB06_MUNICIPIO (ID_MUNICIPIO),
    CONSTRAINT FK07_VEI_TB01_VEI_TB05 FOREIGN KEY (ID_MODELO)
        REFERENCES DADOS_REFERENCIA.VEI_TB05_MODELO (ID_MODELO),
    CONSTRAINT FK08_VEI_TB01_VEI_TB07 FOREIGN KEY (ID_COMBUSTIVEL)
        REFERENCES DADOS_REFERENCIA.VEI_TB07_COMBUSTIVEL (ID_COMBUSTIVEL),
    CONSTRAINT FK09_VEI_TB01_VEI_TB12 FOREIGN KEY (ID_NACIONALIDADE)
        REFERENCES DADOS_REFERENCIA.VEI_TB12_NACIONALIDADE (ID_NACIONALIDADE),
    CONSTRAINT FK10_VEI_TB01_VEI_TB11 FOREIGN KEY (ID_TIPO_VEICULO)
        REFERENCES DADOS_REFERENCIA.VEI_TB11_TIPO_VEICULO (ID_TIPO_VEICULO),
    CONSTRAINT FK11_VEI_TB01_VEI_TB10 FOREIGN KEY (ID_ESPECIE_VEICULO)
        REFERENCES DADOS_REFERENCIA.VEI_TB10_ESPECIE_VEICULO (ID_ESPECIE_VEICULO),
    CONSTRAINT FK12_VEI_TB01_VEI_TB09 FOREIGN KEY (ID_CARROCERIA)
        REFERENCES DADOS_REFERENCIA.VEI_TB09_CARROCERIA (ID_CARROCERIA),
    CONSTRAINT FK13_VEI_TB01_VEI_TB08 FOREIGN KEY (ID_COR)
        REFERENCES DADOS_REFERENCIA.VEI_TB08_COR (ID_COR),
    CONSTRAINT FK14_VEI_TB01_VEI_TB15A FOREIGN KEY (ID_TIPO_PESSOA_FATURADO)
        REFERENCES DADOS_REFERENCIA.VEI_TB15_TIPO_PESSOA (ID_TIPO_PESSOA),
    CONSTRAINT FK15_VEI_TB01_VEI_TB15B FOREIGN KEY (ID_TIPO_PESSOA_PROP)
        REFERENCES DADOS_REFERENCIA.VEI_TB15_TIPO_PESSOA (ID_TIPO_PESSOA),
    CONSTRAINT FK16_VEI_TB01_VEI_TB15C FOREIGN KEY (ID_TIPO_PESSOA_IMPORTADORA)
        REFERENCES DADOS_REFERENCIA.VEI_TB15_TIPO_PESSOA (ID_TIPO_PESSOA),
    CONSTRAINT FK17_VEI_TB01_VEI_TB14A FOREIGN KEY (ID_RESTRICAO_1)
        REFERENCES DADOS_REFERENCIA.VEI_TB14_RESTRICAO (ID_RESTRICAO),
    CONSTRAINT FK18_VEI_TB01_VEI_TB14B FOREIGN KEY (ID_RESTRICAO_2)
        REFERENCES DADOS_REFERENCIA.VEI_TB14_RESTRICAO (ID_RESTRICAO),
    CONSTRAINT FK19_VEI_TB01_VEI_TB14C FOREIGN KEY (ID_RESTRICAO_3)
        REFERENCES DADOS_REFERENCIA.VEI_TB14_RESTRICAO (ID_RESTRICAO),
    CONSTRAINT FK20_VEI_TB01_VEI_TB14D FOREIGN KEY (ID_RESTRICAO_4)
        REFERENCES DADOS_REFERENCIA.VEI_TB14_RESTRICAO (ID_RESTRICAO)
);

CREATE INDEX IX04_VEI_TB01_ID_MODELO     ON VEI_TB01_DETRAN (ID_MODELO);
CREATE INDEX IX05_VEI_TB01_ID_MUNICIPIO  ON VEI_TB01_DETRAN (ID_MUNICIPIO);
CREATE INDEX IX06_VEI_TB01_NU_CHASSI     ON VEI_TB01_DETRAN (NU_CHASSI);
CREATE INDEX IX07_VEI_TB01_CO_PLACA      ON VEI_TB01_DETRAN (CO_PLACA);

RESET search_path;
