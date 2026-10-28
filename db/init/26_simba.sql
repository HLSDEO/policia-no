-- ===================================================================================================================
-- Extrato bancario do SIMBA (R304)
--
-- Tabelas: SMB_TB01_EXTRATOS, SMB_TB02_CONTAS, SMB_TB03_LANCAMENTOS, SMB_TB04_CONTRAPARTES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · um R304 importado (o arquivo inteiro), preso ao inquérito
CREATE TABLE SMB_TB01_EXTRATOS (
    ID_EXTRATO               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO             BIGINT NOT NULL,
    NO_ARQUIVO               VARCHAR(3000) NOT NULL,  -- caminho do CSV relativo a data/ (ex.: "FICCO-ES/2025.0079407/BANCARIO/002-PF_R304.csv")
    ID_DOCUMENTO_SINTESE     BIGINT,  -- síntese textual anexada em INQ_TB04 (p/ a IA)
    QT_CONTAS                INTEGER NOT NULL DEFAULT 0,
    QT_TITULARES             INTEGER NOT NULL DEFAULT 0,
    QT_LANCAMENTOS           INTEGER NOT NULL DEFAULT 0,  -- linhas do CSV (pernas), não lançamentos distintos
    QT_CONTRAPARTES          INTEGER NOT NULL DEFAULT 0,
    VL_CREDITO               NUMERIC(18,2) NOT NULL DEFAULT 0,
    VL_DEBITO                NUMERIC(18,2) NOT NULL DEFAULT 0,
    DT_PRIMEIRO_LANCAMENTO   DATE,
    DT_ULTIMO_LANCAMENTO     DATE,
    ID_USUARIO               BIGINT,  -- quem importou
    DT_IMPORTACAO            TIMESTAMP NOT NULL DEFAULT now(),
    CD_STATUS                VARCHAR(20) NOT NULL DEFAULT 'concluido',
    CD_FASE                  VARCHAR(30),
    QT_PROGRESSO_FASE        SMALLINT,
    TX_ERRO                  TEXT,
    FL_CANCELAR              BOOLEAN NOT NULL DEFAULT false,
    DT_INICIO_PROCESSAMENTO  TIMESTAMP,
    DT_FIM_PROCESSAMENTO     TIMESTAMP,
    CO_HASH_MD5              CHAR(32),
    CO_HASH_SHA1             CHAR(40),
    CO_HASH_SHA256           CHAR(64),
    CONSTRAINT FK160_SMB_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK161_SMB_TB01_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO_SINTESE)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK162_SMB_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX108_SMB_TB01_ID_INQUERITO  ON SMB_TB01_EXTRATOS (ID_INQUERITO);

-- 02 · conta bancária afastada (uma por conta_codigo_simba), com os totais já apurados
CREATE TABLE SMB_TB02_CONTAS (
    ID_CONTA_SIMBA          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRATO              BIGINT NOT NULL,
    CD_CONTA_SIMBA          VARCHAR(3000),  -- conta_codigo_simba (chave do SIMBA)
    TX_IDENTIFICADOR        VARCHAR(3000),  -- conta_identificador_com_titular
    NO_TITULAR              VARCHAR(3000),
    NU_CPF_CNPJ             VARCHAR(3000),
    TP_PESSOA               VARCHAR(4),  -- PF | PJ
    NU_BANCO                VARCHAR(3000),
    NO_BANCO                VARCHAR(3000),
    NU_AGENCIA              VARCHAR(3000),
    NU_CONTA                VARCHAR(3000),
    TP_CONTA                VARCHAR(3000),  -- Corrente | Poupança | Outros...
    DT_ABERTURA             DATE,
    DT_ENCERRAMENTO         DATE,
    TX_ATENDIMENTO          VARCHAR(3000),  -- atendimento_identificador (ofício/decisão)
    QT_LANCAMENTOS          INTEGER NOT NULL DEFAULT 0,
    VL_CREDITO              NUMERIC(18,2) NOT NULL DEFAULT 0,
    VL_DEBITO               NUMERIC(18,2) NOT NULL DEFAULT 0,
    DT_PRIMEIRO_LANCAMENTO  DATE,
    DT_ULTIMO_LANCAMENTO    DATE,
    CONSTRAINT FK163_SMB_TB02_SMB_TB01 FOREIGN KEY (ID_EXTRATO)
        REFERENCES SMB_TB01_EXTRATOS (ID_EXTRATO)
);

CREATE INDEX IX109_SMB_TB02_ID_EXTRATO    ON SMB_TB02_CONTAS (ID_EXTRATO);

-- 03 · lançamento (uma linha por perna od_ do lançamento — ver nota de grão no topo)
CREATE TABLE SMB_TB03_LANCAMENTOS (
    ID_LANCAMENTO_SIMBA      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRATO               BIGINT NOT NULL,
    ID_CONTA_SIMBA           BIGINT NOT NULL,
    CD_LANCAMENTO_SIMBA      VARCHAR(3000),  -- lancamento_codigo_simba (repete nas pernas)
    CD_OD_SIMBA              VARCHAR(3000),  -- od_codigo_simba (único por linha)
    DT_LANCAMENTO            DATE,
    TP_LANCAMENTO            VARCHAR(3000),  -- lancamento_tipo (código numérico do SIMBA)
    TX_DESCRICAO             VARCHAR(3000),
    NU_DOCUMENTO             VARCHAR(3000),
    TX_LOCAL                 VARCHAR(3000),  -- lancamento_local_transacao
    VL_OPERACAO              NUMERIC(18,2),  -- od_valor_operacao (valor da perna)
    CD_NATUREZA              VARCHAR(1),  -- 'C' | 'D' | NULL (o CSV traz '*' quando a instituição não informou)
    VL_SALDO                 NUMERIC(18,2),
    CD_NATUREZA_SALDO        VARCHAR(1),
    NU_CPF_CNPJ_OD           VARCHAR(3000),  -- contraparte
    NO_OD                    VARCHAR(3000),
    TP_PESSOA_OD             VARCHAR(4),
    NU_BANCO_OD              VARCHAR(3000),
    NU_AGENCIA_OD            VARCHAR(3000),
    NU_CONTA_OD              VARCHAR(3000),
    TP_CONTA_OD              VARCHAR(3000),
    TX_OBSERVACAO_OD         VARCHAR(3000),
    TX_DOC_IDENTIFICACAO_OD  VARCHAR(3000),
    NO_ENDOSSANTE_CHEQUE     VARCHAR(3000),
    CONSTRAINT FK164_SMB_TB03_SMB_TB01 FOREIGN KEY (ID_EXTRATO)
        REFERENCES SMB_TB01_EXTRATOS (ID_EXTRATO),
    CONSTRAINT FK165_SMB_TB03_SMB_TB02 FOREIGN KEY (ID_CONTA_SIMBA)
        REFERENCES SMB_TB02_CONTAS (ID_CONTA_SIMBA)
);

CREATE INDEX IX110_SMB_TB03_ID_EXTRATO    ON SMB_TB03_LANCAMENTOS (ID_EXTRATO);
CREATE INDEX IX111_SMB_TB03_ID_CONTA      ON SMB_TB03_LANCAMENTOS (ID_CONTA_SIMBA, DT_LANCAMENTO);
CREATE INDEX IX112_SMB_TB03_CPF_CNPJ_OD   ON SMB_TB03_LANCAMENTOS (NU_CPF_CNPJ_OD);

-- 04 · contrapartes consolidadas por conta e direção (quem creditou / quem foi debitado),
-- agregadas de SMB_TB03 no fim da importação — é a base do "Ver análise" e do grafo.
CREATE TABLE SMB_TB04_CONTRAPARTES (
    ID_CONTRAPARTE_SIMBA   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRATO             BIGINT NOT NULL,
    ID_CONTA_SIMBA         BIGINT NOT NULL,
    CD_DIRECAO             VARCHAR(20),  -- 'credito' (entrou na conta) | 'debito' (saiu da conta)
    NO_CONTRAPARTE         VARCHAR(3000),
    NU_CPF_CNPJ            VARCHAR(3000),
    NU_BANCO               VARCHAR(3000),
    NU_AGENCIA             VARCHAR(3000),
    NU_CONTA               VARCHAR(3000),
    QT_LANCAMENTOS         INTEGER NOT NULL DEFAULT 0,
    VL_TOTAL               NUMERIC(18,2) NOT NULL DEFAULT 0,
    DT_PRIMEIRO            DATE,
    DT_ULTIMO              DATE,
    IN_TITULAR_NO_EXTRATO  BOOLEAN NOT NULL DEFAULT false,  -- a contraparte também é titular de uma conta afastada aqui
    CONSTRAINT FK166_SMB_TB04_SMB_TB01 FOREIGN KEY (ID_EXTRATO)
        REFERENCES SMB_TB01_EXTRATOS (ID_EXTRATO),
    CONSTRAINT FK167_SMB_TB04_SMB_TB02 FOREIGN KEY (ID_CONTA_SIMBA)
        REFERENCES SMB_TB02_CONTAS (ID_CONTA_SIMBA)
);

CREATE INDEX IX113_SMB_TB04_ID_EXTRATO    ON SMB_TB04_CONTRAPARTES (ID_EXTRATO);
CREATE INDEX IX114_SMB_TB04_ID_CONTA      ON SMB_TB04_CONTRAPARTES (ID_CONTA_SIMBA);

RESET search_path;
