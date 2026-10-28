-- ===================================================================================================================
-- RIF do SISCOAF
--
-- Tabelas: RIF_TB01_RIFS, RIF_TB02_COMUNICACOES, RIF_TB03_ENVOLVIDOS, RIF_TB04_OCORRENCIAS,
--          RIF_TB05_CONTRAPARTES, RIF_TB06_ARQUIVOS, RIF_TB06_IDENTIFICACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · um RIF importado (a pasta inteira), preso ao inquérito
CREATE TABLE RIF_TB01_RIFS (
    ID_RIF            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO      BIGINT NOT NULL,
    NU_RIF            VARCHAR(3000),  -- número do RIF (ex.: 104578), derivado do nome do arquivo
    NO_PASTA          VARCHAR(3000) NOT NULL,  -- caminho da pasta do RIF relativo a data/ (ex.: "FICCO-ES/2025.0079407/RIF/RIF_128302")
    ID_DOCUMENTO_PDF  BIGINT,  -- o PDF anexado em INQ_TB04 (nulo se não houver PDF)
    QT_COMUNICACOES   INTEGER NOT NULL DEFAULT 0,
    QT_ENVOLVIDOS     INTEGER NOT NULL DEFAULT 0,
    QT_OCORRENCIAS    INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO        BIGINT,  -- quem importou
    DT_IMPORTACAO     TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK142_RIF_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK143_RIF_TB01_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO_PDF)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK144_RIF_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX79_RIF_TB01_ID_INQUERITO   ON RIF_TB01_RIFS (ID_INQUERITO);

-- 02 · comunicação (movimentação bancária) — 1 linha por linha do _Comunicacoes.csv
CREATE TABLE RIF_TB02_COMUNICACOES (
    ID_COMUNICACAO_RIF         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_RIF                     BIGINT NOT NULL,
    NU_INDEXADOR               INTEGER,  -- Indexador (chave de ligação com envolvidos/ocorrências)
    ID_COMUNICACAO             VARCHAR(3000),  -- idComunicacao
    NU_OCORRENCIA_BC           VARCHAR(3000),  -- NumeroOcorrenciaBC
    DT_RECEBIMENTO             TIMESTAMP,  -- Data_do_Recebimento
    DT_OPERACAO                DATE,  -- Data_da_operacao
    DT_FIM_FATO                DATE,  -- DataFimFato
    NU_CPF_CNPJ_COMUNICANTE    VARCHAR(3000),  -- cpfCnpjComunicante (banco)
    NO_COMUNICANTE             VARCHAR(3000),  -- nomeComunicante (banco)
    NO_CIDADE_AGENCIA          VARCHAR(3000),  -- CidadeAgencia
    SG_UF_AGENCIA              VARCHAR(4),  -- UFAgencia
    NO_AGENCIA                 VARCHAR(3000),  -- NomeAgencia
    NU_AGENCIA                 VARCHAR(3000),  -- NumeroAgencia
    TX_INFORMACOES_ADICIONAIS  TEXT,  -- informacoesAdicionais (análise completa)
    VL_CAMPO_A                 NUMERIC(18,2),  -- CampoA
    VL_CAMPO_B                 NUMERIC(18,2),  -- CampoB (créditos)
    VL_CAMPO_C                 NUMERIC(18,2),  -- CampoC (débitos)
    VL_CAMPO_D                 NUMERIC(18,2),  -- CampoD
    VL_CAMPO_E                 NUMERIC(18,2),  -- CampoE
    CD_SEGMENTO                VARCHAR(3000),  -- CodigoSegmento
    CONSTRAINT FK145_RIF_TB02_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF)
);

CREATE INDEX IX80_RIF_TB02_ID_RIF         ON RIF_TB02_COMUNICACOES (ID_RIF);

-- 03 · envolvidos citados (pessoas/empresas)
CREATE TABLE RIF_TB03_ENVOLVIDOS (
    ID_ENVOLVIDO_RIF      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_RIF                BIGINT NOT NULL,
    NU_INDEXADOR          INTEGER,  -- Indexador (comunicação a que pertence)
    NU_CPF_CNPJ           VARCHAR(3000),  -- cpfCnpjEnvolvido
    NO_ENVOLVIDO          VARCHAR(3000),  -- nomeEnvolvido
    TP_ENVOLVIDO          VARCHAR(3000),  -- tipoEnvolvido (Remetente/Beneficiário/Outros...)
    NU_AGENCIA            VARCHAR(3000),  -- agenciaEnvolvido
    NU_CONTA              VARCHAR(3000),  -- contaEnvolvido
    DT_ABERTURA_CONTA     DATE,  -- DataAberturaConta
    DT_ATUALIZACAO_CONTA  DATE,  -- DataAtualizacaoConta
    TX_PEP                VARCHAR(3000),  -- bitPepCitado (texto original)
    TX_PESSOA_OBRIGADA    VARCHAR(3000),  -- bitPessoaObrigadaCitado
    TX_SERVIDOR           VARCHAR(3000),  -- intServidorCitado
    CONSTRAINT FK146_RIF_TB03_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF)
);

CREATE INDEX IX81_RIF_TB03_ID_RIF         ON RIF_TB03_ENVOLVIDOS (ID_RIF);

-- 04 · ocorrências (enquadramentos/alertas)
CREATE TABLE RIF_TB04_OCORRENCIAS (
    ID_OCORRENCIA_RIF  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_RIF             BIGINT NOT NULL,
    NU_INDEXADOR       INTEGER,  -- Indexador (comunicação a que pertence)
    ID_OCORRENCIA      VARCHAR(3000),  -- idOcorrencia
    TX_OCORRENCIA      TEXT,  -- Ocorrencia (descrição do enquadramento)
    CONSTRAINT FK147_RIF_TB04_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF)
);

CREATE INDEX IX82_RIF_TB04_ID_RIF         ON RIF_TB04_OCORRENCIAS (ID_RIF);

-- 05 · contrapartes estruturadas (remetentes = crédito/entrada, favorecidos = débito/saída),
-- extraídas do texto livre de cada comunicação por funcoes/rif_utils.extrair_contrapartes().
CREATE TABLE RIF_TB05_CONTRAPARTES (
    ID_CONTRAPARTE_RIF  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_COMUNICACAO_RIF  BIGINT NOT NULL,
    ID_RIF              BIGINT NOT NULL,
    CD_DIRECAO          VARCHAR(20),  -- 'credito' | 'debito'
    NO_CONTRAPARTE      VARCHAR(3000),  -- nome do remetente/favorecido
    NU_CPF_CNPJ         VARCHAR(3000),  -- CPF/CNPJ do remetente/favorecido
    QT_LANCAMENTOS      INTEGER NOT NULL,  -- quantidade de lançamentos
    VL_TOTAL            NUMERIC(18,2) NOT NULL,  -- valor total
    TX_OBS              VARCHAR(3000),  -- banco de origem/destino ou qualificação (PEP, profissão)
    NU_ORDEM            INTEGER NOT NULL,  -- ordem de apresentação (ranking por valor na direção)
    CONSTRAINT FK148_RIF_TB05_RIF_TB02 FOREIGN KEY (ID_COMUNICACAO_RIF)
        REFERENCES RIF_TB02_COMUNICACOES (ID_COMUNICACAO_RIF),
    CONSTRAINT FK149_RIF_TB05_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF)
);

CREATE INDEX IX83_RIF_TB05_ID_COMUNICACAO ON RIF_TB05_CONTRAPARTES (ID_COMUNICACAO_RIF);
CREATE INDEX IX84_RIF_TB05_ID_RIF          ON RIF_TB05_CONTRAPARTES (ID_RIF);

-- 06 · hash de cada arquivo da pasta do RIF (um dos 3 CSVs ou o PDF)
CREATE TABLE RIF_TB06_ARQUIVOS (
    ID_ARQUIVO_RIF  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_RIF          BIGINT NOT NULL,
    NO_ARQUIVO      VARCHAR(3000) NOT NULL,
    TP_ARQUIVO      VARCHAR(30) NOT NULL,  -- comunicacoes|envolvidos|ocorrencias|pdf
    QT_BYTES        BIGINT,
    CO_HASH_MD5     CHAR(32),
    CO_HASH_SHA1    CHAR(40),
    CO_HASH_SHA256  CHAR(64),
    CONSTRAINT FK172_RIF_TB06_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF)
);

CREATE INDEX IX117_RIF_TB06_ID_RIF ON RIF_TB06_ARQUIVOS (ID_RIF);

CREATE TABLE RIF_TB06_IDENTIFICACOES (
    ID_IDENTIFICACAO_RIF  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_COMUNICACAO_RIF    BIGINT NOT NULL,
    ID_RIF                BIGINT NOT NULL,
    CD_DIRECAO            VARCHAR(20) NOT NULL,  -- 'credito' | 'debito' (mesma direção da linha desconhecida)
    NO_CONTRAPARTE        VARCHAR(3000),  -- nome apurado pelo investigador
    NU_CPF_CNPJ           VARCHAR(3000),  -- CPF/CNPJ apurado
    QT_LANCAMENTOS        INTEGER,  -- quantos lançamentos daquele total (opcional)
    VL_TOTAL              NUMERIC(18,2) NOT NULL,  -- fatia identificada do valor desconhecido
    TX_OBS                VARCHAR(3000),  -- fundamento/origem da identificação
    ID_USUARIO            BIGINT,  -- quem identificou
    DT_CRIACAO            TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO        TIMESTAMP,
    CONSTRAINT FK169_RIF_TB06_RIF_TB02 FOREIGN KEY (ID_COMUNICACAO_RIF)
        REFERENCES RIF_TB02_COMUNICACOES (ID_COMUNICACAO_RIF),
    CONSTRAINT FK170_RIF_TB06_RIF_TB01 FOREIGN KEY (ID_RIF)
        REFERENCES RIF_TB01_RIFS (ID_RIF),
    CONSTRAINT FK171_RIF_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX115_RIF_TB06_ID_COMUNICACAO ON RIF_TB06_IDENTIFICACOES (ID_COMUNICACAO_RIF);
CREATE INDEX IX116_RIF_TB06_ID_RIF          ON RIF_TB06_IDENTIFICACOES (ID_RIF);

RESET search_path;
