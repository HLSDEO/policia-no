-- ===================================================================================================================
-- Declaracoes de IRPF
--
-- Tabelas: IRP_TB01_DECLARACOES, IRP_TB02_BENS_DIREITOS, IRP_TB03_DEPENDENTES, IRP_TB04_RENDIMENTOS,
--          IRP_TB05_DIVIDAS, IRP_TB06_DOACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · uma declaração de IRPF analisada, presa ao inquérito
CREATE TABLE IRP_TB01_DECLARACOES (
    ID_IRPF                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO                BIGINT NOT NULL,
    ID_DOCUMENTO                BIGINT,  -- o PDF anexado em INQ_TB04 (texto para a IA)
    NO_ARQUIVO                  VARCHAR(3000) NOT NULL,  -- nome do PDF em data/
    NO_TITULAR                  VARCHAR(3000),  -- nome do contribuinte
    NU_CPF                      VARCHAR(3000),
    DT_NASCIMENTO               DATE,
    NU_TITULO_ELEITORAL         VARCHAR(3000),
    TX_ENDERECO                 VARCHAR(3000),  -- logradouro, número, complemento, bairro
    NO_MUNICIPIO                VARCHAR(3000),
    SG_UF                       VARCHAR(3000),
    NU_CEP                      VARCHAR(3000),
    NU_TELEFONE                 VARCHAR(3000),
    NU_CELULAR                  VARCHAR(3000),
    NO_EMAIL                    VARCHAR(3000),
    TX_NATUREZA_OCUPACAO        VARCHAR(3000),
    TX_OCUPACAO_PRINCIPAL       VARCHAR(3000),
    TX_TIPO_DECLARACAO          VARCHAR(3000),  -- original, retificadora...
    NU_EXERCICIO                INTEGER,  -- ex.: 2024
    NU_ANO_CALENDARIO           INTEGER,  -- ex.: 2023
    VL_RENDIMENTOS_TRIBUTAVEIS  NUMERIC(18,2),
    VL_IMPOSTO_DEVIDO           NUMERIC(18,2),
    VL_IMPOSTO_RESTITUIR        NUMERIC(18,2),
    VL_SALDO_PAGAR              NUMERIC(18,2),
    VL_BENS_ANO_ANTERIOR        NUMERIC(18,2),  -- evolução patrimonial: soma dos
    VL_BENS_ANO_ATUAL           NUMERIC(18,2),  -- bens/dívidas em 31/12 do ano
    VL_DIVIDAS_ANO_ANTERIOR     NUMERIC(18,2),  -- anterior e do atual — calculada
    VL_DIVIDAS_ANO_ATUAL        NUMERIC(18,2),  -- a partir das linhas de TB02/TB05
    -- (o resumo em texto do PDF nessa
    -- seção saiu embaralhado nos testes)
    QT_BENS                     INTEGER NOT NULL DEFAULT 0,
    QT_DEPENDENTES              INTEGER NOT NULL DEFAULT 0,
    QT_RENDIMENTOS              INTEGER NOT NULL DEFAULT 0,
    QT_DIVIDAS                  INTEGER NOT NULL DEFAULT 0,
    QT_DOACOES                  INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO                  BIGINT,  -- quem extraiu
    DT_EXTRACAO                 TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK230_IRP_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK231_IRP_TB01_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK232_IRP_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX192_IRP_TB01_ID_INQUERITO ON IRP_TB01_DECLARACOES (ID_INQUERITO);

-- 02 · bens e direitos declarados (imóveis, veículos, contas, aplicações...)
CREATE TABLE IRP_TB02_BENS_DIREITOS (
    ID_BEM            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IRPF           BIGINT NOT NULL,
    NO_CODIGO         VARCHAR(3000),  -- grupo/código como consta no PDF (ex.: "21 - Depósito em conta")
    TX_DISCRIMINACAO  TEXT,  -- descrição do bem (endereço, banco/conta, ações...)
    VL_ANO_ANTERIOR   NUMERIC(18,2),
    VL_ANO_ATUAL      NUMERIC(18,2),
    CONSTRAINT FK233_IRP_TB02_IRP_TB01 FOREIGN KEY (ID_IRPF)
        REFERENCES IRP_TB01_DECLARACOES (ID_IRPF)
);

CREATE INDEX IX193_IRP_TB02_ID_IRPF       ON IRP_TB02_BENS_DIREITOS (ID_IRPF);

-- 03 · dependentes declarados
CREATE TABLE IRP_TB03_DEPENDENTES (
    ID_DEPENDENTE  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IRPF        BIGINT NOT NULL,
    NO_DEPENDENTE  VARCHAR(3000),
    NU_CPF         VARCHAR(3000),
    TX_RELACAO     VARCHAR(3000),  -- filho(a), cônjuge, companheiro(a)...
    DT_NASCIMENTO  DATE,
    CONSTRAINT FK234_IRP_TB03_IRP_TB01 FOREIGN KEY (ID_IRPF)
        REFERENCES IRP_TB01_DECLARACOES (ID_IRPF)
);

CREATE INDEX IX194_IRP_TB03_ID_IRPF       ON IRP_TB03_DEPENDENTES (ID_IRPF);

-- 04 · rendimentos recebidos de pessoa jurídica (titular e dependentes)
CREATE TABLE IRP_TB04_RENDIMENTOS (
    ID_RENDIMENTO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IRPF            BIGINT NOT NULL,
    TP_ORIGEM          VARCHAR(3000) NOT NULL,  -- titular | dependente
    NO_FONTE_PAGADORA  VARCHAR(3000),
    NU_CNPJ_CPF        VARCHAR(3000),
    VL_RENDIMENTO      NUMERIC(18,2),
    CONSTRAINT FK235_IRP_TB04_IRP_TB01 FOREIGN KEY (ID_IRPF)
        REFERENCES IRP_TB01_DECLARACOES (ID_IRPF)
);

CREATE INDEX IX195_IRP_TB04_ID_IRPF       ON IRP_TB04_RENDIMENTOS (ID_IRPF);

-- 05 · dívidas e ônus reais
CREATE TABLE IRP_TB05_DIVIDAS (
    ID_DIVIDA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IRPF           BIGINT NOT NULL,
    NO_CODIGO         VARCHAR(3000),
    TX_DISCRIMINACAO  TEXT,
    VL_ANO_ANTERIOR   NUMERIC(18,2),
    VL_ANO_ATUAL      NUMERIC(18,2),
    CONSTRAINT FK236_IRP_TB05_IRP_TB01 FOREIGN KEY (ID_IRPF)
        REFERENCES IRP_TB01_DECLARACOES (ID_IRPF)
);

CREATE INDEX IX196_IRP_TB05_ID_IRPF       ON IRP_TB05_DIVIDAS (ID_IRPF);

-- 06 · doações efetuadas e doações a partidos políticos/candidatos
CREATE TABLE IRP_TB06_DOACOES (
    ID_DOACAO        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IRPF          BIGINT NOT NULL,
    TP_DOACAO        VARCHAR(3000) NOT NULL,  -- efetuada | partido
    NO_BENEFICIARIO  VARCHAR(3000),
    NU_CNPJ_CPF      VARCHAR(3000),
    VL_DOACAO        NUMERIC(18,2),
    CONSTRAINT FK237_IRP_TB06_IRP_TB01 FOREIGN KEY (ID_IRPF)
        REFERENCES IRP_TB01_DECLARACOES (ID_IRPF)
);

CREATE INDEX IX197_IRP_TB06_ID_IRPF       ON IRP_TB06_DOACOES (ID_IRPF);

RESET search_path;
