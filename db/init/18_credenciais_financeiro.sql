-- ===================================================================================================================
-- Senhas, autofill, contas, e-mails, cartoes e PIX
--
-- Tabelas: AFL_TB01_AUTOFILL, BNC_TB01_CONTAS_FINANCEIRAS, CRT_TB01_CARTOES_CREDITO,
--          CTA_TB01_CONTAS_USUARIO, EML_TB01_EMAILS, MBC_TB01_CARTOES_CARTEIRA, PIX_TB01_CHAVES,
--          SNH_TB01_SENHAS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE AFL_TB01_AUTOFILL (
    ID_AUTOFILL     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    NO_FONTE        VARCHAR(3000),
    NO_CHAVE        TEXT,
    TX_VALOR        TEXT,
    TP_MODELO       VARCHAR(80),
    CD_STATUS_UFDR  VARCHAR(50),
    DT_REGISTRO     TIMESTAMP,  -- TimeStamp
    DT_ULTIMO_USO   TIMESTAMP  -- LastUsedDate
);

CREATE INDEX IX97_AFL_TB01_EXTRACAO ON AFL_TB01_AUTOFILL (ID_EXTRACAO);

CREATE TABLE BNC_TB01_CONTAS_FINANCEIRAS (
    ID_CONTA_FINANCEIRA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    NO_FONTE             VARCHAR(2000),
    NO_INSTITUICAO       VARCHAR(3000),
    NO_TITULAR           VARCHAR(3000),
    NU_CONTA             VARCHAR(3000),
    NU_AGENCIA           VARCHAR(1000),
    TP_CONTA             VARCHAR(100),
    NO_MOEDA             VARCHAR(3000),
    VL_SALDO             VARCHAR(10000),
    TP_MODELO            VARCHAR(80),
    CD_STATUS_UFDR       VARCHAR(50)
);

CREATE INDEX IX94_BNC_TB01_EXTRACAO ON BNC_TB01_CONTAS_FINANCEIRAS (ID_EXTRACAO);

CREATE TABLE CRT_TB01_CARTOES_CREDITO (
    ID_CARTAO       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- Source
    NO_BANDEIRA     VARCHAR(3000),  -- Company
    NU_CARTAO       VARCHAR(3000),  -- CreditCardNumber (como veio; costuma vir mascarado)
    DT_VALIDADE     VARCHAR(50),  -- ExpirationDate (texto: MM/AA)
    NO_TITULAR      VARCHAR(3000),  -- NameOnCard
    DT_ULTIMO_USO   TIMESTAMP,  -- DateLastUsed
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK228_CRT_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX184_CRT_TB01_EXTRACAO ON CRT_TB01_CARTOES_CREDITO (ID_EXTRACAO);

CREATE TABLE CTA_TB01_CONTAS_USUARIO (
    ID_CONTA_USUARIO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    NO_FONTE          VARCHAR(3000),
    NO_SERVICO        VARCHAR(3000),
    NO_USUARIO        TEXT,
    NO_TITULAR        VARCHAR(3000),
    DT_CRIACAO        TIMESTAMP,
    CD_STATUS_UFDR    VARCHAR(50)
);

CREATE INDEX IX95_CTA_TB01_EXTRACAO ON CTA_TB01_CONTAS_USUARIO (ID_EXTRACAO);

CREATE TABLE EML_TB01_EMAILS (
    ID_EMAIL          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    NO_FONTE          VARCHAR(2000),
    NO_REMETENTE      TEXT,
    TX_DESTINATARIOS  TEXT,
    TX_CC             TEXT,
    TX_CCO            TEXT,
    TX_ASSUNTO        TEXT,
    TX_CORPO          TEXT,
    DT_EMAIL          TIMESTAMP,
    TP_MODELO         VARCHAR(80),
    CD_STATUS_UFDR    VARCHAR(50),
    NO_PASTA          TEXT,  -- Folder (Inbox, Social, Updates…)
    CD_STATUS_EMAIL   VARCHAR(3000),  -- Status (Read/Unread)
    NO_CONTA          VARCHAR(3000),  -- Account
    TX_ROTULOS        TEXT,  -- Labels
    TX_ANEXOS         TEXT,  -- nomes dos anexos
    -- Thread do Gmail (cabeçalho X-GM-THRID) e Message-ID RFC 5322. O IPED indexa os dois em
    -- todo item de e-mail de um Takeout do Gmail e eles eram descartados: o X-GM-THRID é o que
    -- reconstrói a conversa (todos os e-mails de uma troca compartilham o mesmo valor), e o
    -- Message-ID é o que amarra a resposta ao original quando o assunto foi reescrito.
    NU_THREAD         VARCHAR(80),
    NU_MENSAGEM       VARCHAR(998)  -- limite de linha do RFC 5322; na prática ~100 caracteres
);

CREATE INDEX IX93_EML_TB01_EXTRACAO ON EML_TB01_EMAILS (ID_EXTRACAO);
-- "os outros e-mails desta mesma conversa" — parcial porque só e-mail vindo do Gmail tem thread
CREATE INDEX IF NOT EXISTS IX258_EML_TB01_THREAD ON EML_TB01_EMAILS (ID_EXTRACAO, NU_THREAD)
    WHERE NU_THREAD IS NOT NULL;

-- Cartões e passes guardados na carteira do aparelho (MobileCard): embarque,
-- fidelidade, ingresso. Traz emissor, código de barras e onde foi comprado.
CREATE TABLE MBC_TB01_CARTOES_CARTEIRA (
    ID_CARTAO_CARTEIRA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    TX_DESCRICAO        TEXT,  -- Description
    NO_ORGANIZACAO      VARCHAR(3000),  -- Organization → Organization.Name
    TP_CARTAO           VARCHAR(3000),  -- Type
    CO_BARRAS           VARCHAR(3000),  -- Barcode
    DT_COMPRA           TIMESTAMP,  -- PurchaseTime
    DT_MODIFICACAO      TIMESTAMP,  -- ModifyTime
    DT_EXPIRACAO        TIMESTAMP,  -- ExpirationTime
    VL_LATITUDE         NUMERIC(10,6),  -- Position (onde foi comprado/usado)
    VL_LONGITUDE        NUMERIC(10,6),
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK229_MBC_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX185_MBC_TB01_EXTRACAO ON MBC_TB01_CARTOES_CARTEIRA (ID_EXTRACAO);

CREATE TABLE PIX_TB01_CHAVES (
    ID_CHAVE_PIX          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO           BIGINT NOT NULL,
    NO_FONTE              VARCHAR(3000) NOT NULL,  -- wpp | sms | msg
    NO_APP                VARCHAR(3000),  -- app do mensageiro quando fonte = msg (Instagram, …)
    CO_CHAVE              VARCHAR(3000) NOT NULL,  -- chave normalizada (dígitos / e-mail minúsculo / uuid)
    TP_CHAVE              VARCHAR(3000) NOT NULL,  -- cpf | cnpj | email | telefone | aleatoria
    TX_CHAVE_ORIGINAL     VARCHAR(3000),  -- como a chave apareceu no texto
    ID_MENSAGEM           BIGINT NOT NULL,  -- mensagem onde apareceu (deep-link)
    ID_CONVERSA           BIGINT,
    NO_CONVERSA           VARCHAR(3000),  -- grupo, ou o outro lado da conversa (denormalizado)
    ID_CONTATO_REMETENTE  BIGINT,  -- quem enviou a mensagem (nulo se não resolvido)
    NO_REMETENTE          VARCHAR(3000),
    IN_REMETENTE_DONO     BOOLEAN,  -- remetente é o dono do aparelho?
    TX_TRECHO             TEXT,  -- trecho da mensagem (evidência)
    DT_MENSAGEM           TIMESTAMP,
    CONSTRAINT FK139_PIX_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX74_PIX_TB01_EXTRACAO ON PIX_TB01_CHAVES (ID_EXTRACAO);
CREATE INDEX IX75_PIX_TB01_CHAVE_TRGM ON PIX_TB01_CHAVES USING gin (CO_CHAVE gin_trgm_ops);

CREATE TABLE SNH_TB01_SENHAS (
    ID_SENHA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO      BIGINT NOT NULL REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    NO_FONTE         VARCHAR(3000),
    NO_SERVICO       TEXT,
    NO_USUARIO       TEXT,
    TX_DADOS         TEXT,
    TP_MODELO        VARCHAR(80),
    CD_STATUS_UFDR   VARCHAR(50),
    TP_SENHA         VARCHAR(3000),  -- Type (distingue senha de token/cookie de sessão)
    NO_GRUPO_ACESSO  TEXT  -- AccessGroup (chaveiro do iOS)
);

CREATE INDEX IX96_SNH_TB01_EXTRACAO ON SNH_TB01_SENHAS (ID_EXTRACAO);

RESET search_path;
