-- ===================================================================================================================
-- Ligacoes, notificacoes e caixa postal
--
-- Tabelas: LIG_TB01_LIGACOES, NTF_TB01_NOTIFICACOES, VMS_TB01_VOICEMAIL
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · ligações (Call do UFDR) — sem tabela de participantes N:N: é sempre a
-- ligação do dono do aparelho com "o outro lado", já resolvido nas colunas abaixo
CREATE TABLE LIG_TB01_LIGACOES (
    ID_LIGACAO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_APLICATIVO        TEXT,  -- Source
    TP_DIRECAO           VARCHAR(3000),  -- Direction: Incoming | Outgoing
    CD_STATUS            VARCHAR(3000),  -- Status: Missed | Established | ...
    DT_LIGACAO           TIMESTAMP,
    QT_DURACAO_SEGUNDOS  INTEGER,
    IN_VIDEOCHAMADA      BOOLEAN,
    NU_TELEFONE_CONTATO  TEXT,  -- Identifier da Party do outro lado
    NO_CONTATO           TEXT,  -- Name da Party do outro lado, se houver
    CD_STATUS_UFDR       VARCHAR(50),
    -- ---------------------------------------------------------------------
    -- Ligações, localizações, notificações, notas, aplicativos, e-mails,
    -- senhas, autofill e Wi-Fi.
    -- ---------------------------------------------------------------------
    TP_DESCONEXAO        VARCHAR(3000),  -- DisconnectionCause (quem/por que a chamada caiu)
    NO_CONTA             VARCHAR(3000),  -- Account
    CONSTRAINT FK47_LIG_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX19_LIG_TB01_DT_LIGACAO      ON LIG_TB01_LIGACOES (DT_LIGACAO);
-- Ligações: ORDER BY l.dt_ligacao DESC NULLS LAST
CREATE INDEX IF NOT EXISTS IX199_LIG_TB01_EXTRACAO_DATA
    ON LIG_TB01_LIGACOES (ID_EXTRACAO, DT_LIGACAO DESC NULLS LAST);

-- 01 · notificações (Notification do UFDR) — Body costuma vir vazio; o app de
-- origem real normalmente só aparece no texto livre de "Notes" (NO_PACOTE_APP)
CREATE TABLE NTF_TB01_NOTIFICACOES (
    ID_NOTIFICACAO     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO        BIGINT NOT NULL,
    NO_APLICATIVO_LOG  TEXT,  -- Source (quem registrou o evento, ex. Digital Wellbeing)
    NO_PACOTE_APP      TEXT,  -- pacote do app que gerou a notificação, extraído de "Notes"
    TX_CONTEUDO        TEXT,  -- Body, quando disponível
    DT_NOTIFICACAO     TIMESTAMP,
    CD_STATUS_UFDR     VARCHAR(50),
    NO_ASSUNTO         TEXT,  -- Subject
    TP_NOTIFICACAO     VARCHAR(3000),  -- Type
    CD_STATUS          VARCHAR(3000),  -- Status (Unread/Default)
    CONSTRAINT FK49_NTF_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX21_NTF_TB01_DT_NOTIFICACAO  ON NTF_TB01_NOTIFICACOES (DT_NOTIFICACAO);
-- Notificações: ORDER BY n.dt_notificacao DESC NULLS LAST
CREATE INDEX IF NOT EXISTS IX201_NTF_TB01_EXTRACAO_DATA
    ON NTF_TB01_NOTIFICACOES (ID_EXTRACAO, DT_NOTIFICACAO DESC NULLS LAST);

-- =====================================================================
-- Tela de Ligações (aba "Caixa postal") — Voicemail. SÓ iPhone nos
-- extratos auditados, e um terço deles já vem com transcrição pronta.
-- =====================================================================
CREATE TABLE VMS_TB01_VOICEMAIL (
    ID_VOICEMAIL         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_APLICATIVO        VARCHAR(3000),  -- Source
    NU_TELEFONE          VARCHAR(3000),  -- From → Party.Identifier
    NO_CONTATO           VARCHAR(3000),  -- From → Party.Name, ou campo Name
    DT_RECEBIMENTO       TIMESTAMP,  -- Timestamp
    DT_MODIFICACAO       TIMESTAMP,  -- LastModified
    QT_DURACAO_SEGUNDOS  BIGINT,  -- Duration
    IN_OUVIDA            BOOLEAN,  -- WasPlayed
    TP_VOICEMAIL         VARCHAR(3000),  -- Type
    TX_TRANSCRICAO       TEXT,  -- Transcript (vem pronta do Cellebrite)
    CD_STATUS_UFDR       VARCHAR(50),
    CONSTRAINT FK226_VMS_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX182_VMS_TB01_EXTRACAO ON VMS_TB01_VOICEMAIL (ID_EXTRACAO, DT_RECEBIMENTO);

RESET search_path;
