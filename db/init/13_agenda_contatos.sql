-- ===================================================================================================================
-- Agenda do aparelho e suas entradas
--
-- Tabelas: CTT_TB01_CONTATOS, CTT_TB02_CONTATO_ENTRADAS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · agenda de contatos (Contact do UFDR — mais rico que o Party dos chats:
-- traz de qual app veio, e os telefones/e-mails associados em CTT_TB02)
CREATE TABLE CTT_TB01_CONTATOS (
    ID_CONTATO            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO           BIGINT NOT NULL,
    NO_CONTATO            TEXT,
    NO_APLICATIVO_ORIGEM  TEXT,  -- Source (WhatsApp, Contacts, etc.)
    NO_GRUPO              TEXT,  -- Group
    TP_CONTATO            VARCHAR(3000),  -- Type
    CD_STATUS_UFDR        VARCHAR(50),
    DT_CRIACAO            TIMESTAMP,  -- TimeCreated (quando o contato foi salvo)
    DT_MODIFICACAO        TIMESTAMP,  -- TimeModified
    TX_NOTAS              TEXT,  -- multiField Notes (anotação livre do contato)
    TX_ENDERECO           TEXT,  -- Addresses → StreetAddress
    NO_ORGANIZACAO        VARCHAR(3000),  -- Organizations → Organization
    NO_CONTA              VARCHAR(3000),  -- Account (agenda de qual conta)
    TX_NASCIMENTO         VARCHAR(200),  -- AdditionalInfo "Date of Birth", como veio
    TX_INFO_ADICIONAL     TEXT,  -- demais pares chave/valor do AdditionalInfo
    CO_CAMINHO_FOTO       TEXT,  -- ContactPhoto → arquivo dentro do .ufdr
    CONSTRAINT FK45_CTT_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX151_CTT_TB01_NASCIMENTO ON CTT_TB01_CONTATOS (ID_EXTRACAO)
    WHERE TX_NASCIMENTO IS NOT NULL;
-- Contatos: ORDER BY c.no_contato NULLS LAST (ASC já é NULLS LAST no Postgres,
-- por isso a coluna entra sem sufixo aqui).
CREATE INDEX IF NOT EXISTS IX198_CTT_TB01_EXTRACAO_NOME
    ON CTT_TB01_CONTATOS (ID_EXTRACAO, NO_CONTATO);

-- 02 · telefones/e-mails de cada contato (Entries do Contact)
CREATE TABLE CTT_TB02_CONTATO_ENTRADAS (
    ID_ENTRADA    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONTATO    BIGINT NOT NULL,
    TP_ENTRADA    VARCHAR(3000),  -- PhoneNumber | EmailAddress
    NO_CATEGORIA  TEXT,  -- Category (Phone/Mobile/Home/Work...)
    TX_VALOR      TEXT,  -- número ou e-mail em si
    -- Domain diz de qual serviço é o identificador; sem ele um <UserID> solto não
    -- informa se é conta de WhatsApp, Instagram ou telefone.
    NO_DOMINIO    VARCHAR(3000),
    CONSTRAINT FK46_CTT_TB02_CTT_TB01 FOREIGN KEY (ID_CONTATO)
        REFERENCES CTT_TB01_CONTATOS (ID_CONTATO)
);

CREATE INDEX IF NOT EXISTS IX150_CTT_TB02_ID_CONTATO ON CTT_TB02_CONTATO_ENTRADAS (ID_CONTATO);

RESET search_path;
