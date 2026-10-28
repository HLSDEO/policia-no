-- ===================================================================================================================
-- Inquerito em PDF do ePol
--
-- Tabelas: IPL_TB01_INQUERITOS_PDF, IPL_TB02_PECAS, IPL_TB03_PESSOAS, IPL_TB04_ACHADOS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · um PDF de inquérito analisado, preso ao inquérito
CREATE TABLE IPL_TB01_INQUERITOS_PDF (
    ID_IPL                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO          BIGINT NOT NULL,
    ID_DOCUMENTO          BIGINT,  -- o PDF anexado em INQ_TB04 (texto para a IA)
    NO_ARQUIVO            VARCHAR(3000) NOT NULL,  -- nome do PDF em data/
    NU_IPL                VARCHAR(3000),  -- nº do IPL como consta na portaria
    NO_UNIDADE            VARCHAR(3000),  -- sigla da unidade (ex.: SR/PF/ES)
    NO_AUTORIDADE         VARCHAR(3000),  -- delegado(a) que preside
    DT_INSTAURACAO        DATE,  -- data da portaria inaugural
    TX_TIPIFICACAO        VARCHAR(3000),  -- crime(s) apurado(s) na portaria
    TX_RESUMO_FATOS       TEXT,  -- "RESUMO DO(s) FATO(s) INVESTIGADO(s)"
    NU_PROCESSO_JUDICIAL  VARCHAR(3000),  -- nº CNJ, quando citado
    QT_FOLHAS             INTEGER NOT NULL DEFAULT 0,
    QT_PAGINAS            INTEGER NOT NULL DEFAULT 0,
    QT_PECAS              INTEGER NOT NULL DEFAULT 0,
    QT_PESSOAS            INTEGER NOT NULL DEFAULT 0,
    QT_ACHADOS            INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO            BIGINT,  -- quem extraiu
    DT_EXTRACAO           TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK148_IPL_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK149_IPL_TB01_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK150_IPL_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX83_IPL_TB01_ID_INQUERITO ON IPL_TB01_INQUERITOS_PDF (ID_INQUERITO);

-- 02 · peça (documento interno do inquérito) — o índice, ordenado por folha
CREATE TABLE IPL_TB02_PECAS (
    ID_PECA           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPL            BIGINT NOT NULL,
    TP_PECA           VARCHAR(3000),  -- Portaria, Ofício, Despacho, Termo...
    NU_PECA           VARCHAR(3000),  -- nº da peça (ex.: 1094822/2026)
    DT_PECA           DATE,
    NU_FOLHA_INICIAL  INTEGER,
    NU_FOLHA_FINAL    INTEGER,
    QT_FOLHAS         INTEGER,
    NO_ORGAO          VARCHAR(3000),  -- timbre do topo da folha
    NO_ASSINANTE      VARCHAR(3000),  -- quem assinou eletronicamente
    TX_RESUMO         TEXT,  -- 1º parágrafo do corpo
    CONSTRAINT FK151_IPL_TB02_IPL_TB01 FOREIGN KEY (ID_IPL)
        REFERENCES IPL_TB01_INQUERITOS_PDF (ID_IPL)
);

CREATE INDEX IX84_IPL_TB02_ID_IPL       ON IPL_TB02_PECAS (ID_IPL);

-- 03 · pessoa citada (por CPF/CNPJ, vulgo ou posto/cargo)
CREATE TABLE IPL_TB03_PESSOAS (
    ID_PESSOA_IPL  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPL         BIGINT NOT NULL,
    NO_PESSOA      VARCHAR(3000),  -- a grafia mais completa encontrada
    NO_VARIANTES   VARCHAR(3000),  -- as demais grafias fundidas nesta linha
    NU_CPF_CNPJ    VARCHAR(3000),
    NO_VULGO       VARCHAR(3000),
    TP_PAPEL       VARCHAR(3000),  -- Investigado, Advogado, Delegado...
    NU_FOLHA       INTEGER,  -- folha da primeira citação
    QT_MENCOES     INTEGER NOT NULL DEFAULT 1,
    TX_CONTEXTO    TEXT,  -- trecho em volta da citação
    CONSTRAINT FK152_IPL_TB03_IPL_TB01 FOREIGN KEY (ID_IPL)
        REFERENCES IPL_TB01_INQUERITOS_PDF (ID_IPL)
);

CREATE INDEX IX85_IPL_TB03_ID_IPL       ON IPL_TB03_PESSOAS (ID_IPL);

-- 04 · achado (dado de interesse solto no corpo das peças)
CREATE TABLE IPL_TB04_ACHADOS (
    ID_ACHADO_IPL   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPL          BIGINT NOT NULL,
    TP_ACHADO       VARCHAR(3000) NOT NULL,  -- telefone, email, valor, conta, agencia,
    -- processo, procedimento, tipificacao,
    -- endereco, oab
    TX_VALOR        VARCHAR(3000) NOT NULL,  -- o dado já normalizado
    VL_NUMERICO     NUMERIC(18,2),  -- só para tp_achado = 'valor'
    NU_FOLHA        INTEGER,  -- folha da primeira ocorrência
    QT_OCORRENCIAS  INTEGER NOT NULL DEFAULT 1,
    TX_CONTEXTO     TEXT,
    CONSTRAINT FK153_IPL_TB04_IPL_TB01 FOREIGN KEY (ID_IPL)
        REFERENCES IPL_TB01_INQUERITOS_PDF (ID_IPL)
);

CREATE INDEX IX86_IPL_TB04_ID_IPL       ON IPL_TB04_ACHADOS (ID_IPL);
CREATE INDEX IX87_IPL_TB04_TIPO         ON IPL_TB04_ACHADOS (ID_IPL, TP_ACHADO);

RESET search_path;
