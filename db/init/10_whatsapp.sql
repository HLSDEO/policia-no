-- ===================================================================================================================
-- WhatsApp: contatos, conversas, mensagens e anexos
--
-- Tabelas: WPP_TB01_CONTATOS, WPP_TB02_CONVERSAS, WPP_TB03_PARTICIPANTES, WPP_TB04_MENSAGENS,
--          WPP_TB05_ANEXOS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · contato/participante (Party do UFDR), por extração
CREATE TABLE WPP_TB01_CONTATOS (
    ID_CONTATO              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO             BIGINT NOT NULL,
    NU_WHATSAPP_ID          TEXT NOT NULL,  -- JID, ex.: 555196772307@s.whatsapp.net
    NO_CONTATO              TEXT,
    IN_DONO_DISPOSITIVO     BOOLEAN,  -- IsPhoneOwner
    ID_PESSOA_ASSOCIADA     BIGINT,
    ID_MUNICIPIO_ASSOCIADO  INTEGER,
    ID_UF_ASSOCIADA         SMALLINT,
    -- quem fez a associação de contato -> pessoa/local mais recente (associar_contato
    -- sobrescreve a cada chamada, inclusive ao limpar a associação)
    ID_USUARIO_ASSOCIOU     BIGINT,
    CO_CAMINHO_FOTO         TEXT,  -- caminho da foto dentro do .ufdr; NULL = sem foto
    CONSTRAINT FK38_WPP_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT UQ01_WPP_TB01_EXTRACAO_JID UNIQUE (ID_EXTRACAO, NU_WHATSAPP_ID),
    CONSTRAINT FK59_WPP_TB01_PES_TB01 FOREIGN KEY (ID_PESSOA_ASSOCIADA)
        REFERENCES DADOS_EXTRAIDOS.PES_TB01_PESSOAS (ID_PESSOA),
    CONSTRAINT FK60_WPP_TB01_IBGE_TB08 FOREIGN KEY (ID_MUNICIPIO_ASSOCIADO)
        REFERENCES DADOS_REFERENCIA.IBGE_TB08_MUNICIPIO (ID_MUNICIPIO),
    CONSTRAINT FK61_WPP_TB01_IBGE_TB03 FOREIGN KEY (ID_UF_ASSOCIADA)
        REFERENCES DADOS_REFERENCIA.IBGE_TB03_UF (ID_UF),
    CONSTRAINT FK71_WPP_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO_ASSOCIOU)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

-- 02 · conversa (Chat do UFDR)
CREATE TABLE WPP_TB02_CONVERSAS (
    ID_CONVERSA          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NU_WHATSAPP_CHAT_ID  TEXT,  -- campo "Id" do Chat
    NO_GRUPO             TEXT,  -- campo "Name" do Chat (grupos)
    TP_CONVERSA          VARCHAR(3000) NOT NULL,  -- individual|grupo (derivado da qtd de participantes)
    DT_INICIO            TIMESTAMP,  -- StartTime
    DT_ULTIMA_ATIVIDADE  TIMESTAMP,  -- LastActivity
    CD_STATUS_UFDR       VARCHAR(50),
    TP_CHAT_UFDR         VARCHAR(50),  -- ChatType: OneOnOne | Group | Broadcast | Unknown
    TX_DESCRICAO         TEXT,  -- Description (descrição do grupo)
    NO_CONTA             VARCHAR(3000),  -- Account (conta dona da conversa no aparelho)
    TX_STATUS_CHAT       VARCHAR(3000),  -- ChatStatus: Favorite, Locked…
    CONSTRAINT FK39_WPP_TB02_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT uq_wpp_tb02_extracao_chat UNIQUE (id_extracao, nu_whatsapp_chat_id)
);

-- 03 · participantes de cada conversa (N:N)
CREATE TABLE WPP_TB03_PARTICIPANTES (
    ID_CONVERSA     BIGINT NOT NULL,
    ID_CONTATO      BIGINT NOT NULL,
    IN_ADMIN_GRUPO  BOOLEAN,  -- IsGroupAdmin
    PRIMARY KEY (ID_CONVERSA, ID_CONTATO),
    CONSTRAINT FK40_WPP_TB03_WPP_TB02 FOREIGN KEY (ID_CONVERSA)
        REFERENCES WPP_TB02_CONVERSAS (ID_CONVERSA),
    CONSTRAINT FK41_WPP_TB03_WPP_TB01 FOREIGN KEY (ID_CONTATO)
        REFERENCES WPP_TB01_CONTATOS (ID_CONTATO)
);

-- *_TB03_PARTICIPANTES já tem PK (ID_CONVERSA, ID_CONTATO) — não serve pra
-- buscar só por ID_CONTATO (não é a coluna líder), que é como o Postgres
-- precisa checar ao apagar de *_TB01_CONTATOS.
CREATE INDEX IF NOT EXISTS IX151_WPP_TB03_ID_CONTATO ON WPP_TB03_PARTICIPANTES (ID_CONTATO);

-- 04 · mensagem (InstantMessage do UFDR)
CREATE TABLE WPP_TB04_MENSAGENS (
    ID_MENSAGEM                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA                BIGINT NOT NULL,
    ID_CONTATO_REMETENTE       BIGINT,  -- nulo quando o remetente não pôde ser resolvido
    TX_MENSAGEM                TEXT,  -- Body
    DT_MENSAGEM                TIMESTAMP,  -- TimeStamp
    TP_MENSAGEM                VARCHAR(3000),  -- Type (ex.: AppMessage)
    CD_STATUS_MENSAGEM         VARCHAR(3000),  -- Status: Read/Delivered/Unread
    NO_PASTA                   VARCHAR(3000),  -- Folder (normalmente vazio no WhatsApp; existe para paridade com SMS)
    IN_ENVIADA                 BOOLEAN,  -- derivado: remetente é o dono do aparelho
    NU_IDENTIFICADOR_ORIGINAL  TEXT,  -- Identifier do UFDR (hash interno), para rastreabilidade
    VL_LATITUDE                NUMERIC(10,6),
    VL_LONGITUDE               NUMERIC(10,6),
    CD_STATUS_UFDR             VARCHAR(50),
    IN_ENCAMINHADA             BOOLEAN,  -- MessageLabel "Forwarded"
    IN_APAGADA_REMETENTE       BOOLEAN,  -- MessageLabel "Recalled" (apagada para todos)
    IN_TEMPORARIA              BOOLEAN,  -- MessageLabel "SelfDestruct"
    IN_EDITADA                 BOOLEAN,  -- MessageLabel "Edited"
    TX_ROTULOS                 VARCHAR(3000),  -- todos os labels, crus, separados por vírgula
    NU_IDENT_CITADO            VARCHAR(3000),  -- QuotedMessageData: mensagem que esta responde
    TX_DESTINATARIOS           TEXT,  -- multiModelField To → Party
    TX_ASSUNTO                 TEXT,  -- Subject
    TX_MOTIVO_EXCLUSAO         VARCHAR(3000),  -- DeletionReason
    NU_SMSC                    VARCHAR(3000),  -- central SMS (só faz sentido em SMS/MMS)
    DT_ENTREGUE                TIMESTAMP,  -- Party.DateDelivered
    DT_LIDA                    TIMESTAMP,  -- Party.DateRead
    DT_REPRODUZIDA             TIMESTAMP,  -- Party.DatePlayed (áudio ouvido)
    CONSTRAINT FK42_WPP_TB04_WPP_TB02 FOREIGN KEY (ID_CONVERSA)
        REFERENCES WPP_TB02_CONVERSAS (ID_CONVERSA),
    CONSTRAINT FK43_WPP_TB04_WPP_TB01 FOREIGN KEY (ID_CONTATO_REMETENTE)
        REFERENCES WPP_TB01_CONTATOS (ID_CONTATO)
);

CREATE INDEX IX16_WPP_TB04_ID_CONVERSA ON WPP_TB04_MENSAGENS (ID_CONVERSA);
CREATE INDEX IX17_WPP_TB04_DT_MENSAGEM ON WPP_TB04_MENSAGENS (DT_MENSAGEM);
CREATE INDEX IX62_WPP_TB04_CONVERSA_MSG ON WPP_TB04_MENSAGENS (ID_CONVERSA, ID_MENSAGEM);
CREATE INDEX IF NOT EXISTS IX147_WPP_TB04_CONTATO_REMETENTE ON WPP_TB04_MENSAGENS (ID_CONTATO_REMETENTE);
-- os filtros de tela são sempre "só o que foi apagado", então o índice
-- parcial cobre o caso útil sem inchar com as centenas de milhares de Intact
CREATE INDEX IF NOT EXISTS IX147_WPP_TB04_APAGADA ON WPP_TB04_MENSAGENS (ID_CONVERSA)
    WHERE CD_STATUS_UFDR IS NOT NULL AND CD_STATUS_UFDR <> 'Intact';
CREATE INDEX IF NOT EXISTS IX186_WPP_TB04_TX_MENSAGEM_TRGM
    ON WPP_TB04_MENSAGENS USING gin (TX_MENSAGEM gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX205_WPP_TB04_IDENT_ORIGINAL
    ON WPP_TB04_MENSAGENS (NU_IDENTIFICADOR_ORIGINAL)
    WHERE NU_IDENTIFICADOR_ORIGINAL IS NOT NULL;

-- 05 · anexo (Attachment do UFDR) — só metadados; binário não é copiado nesta versão
CREATE TABLE WPP_TB05_ANEXOS (
    ID_ANEXO         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_MENSAGEM      BIGINT NOT NULL,
    NO_ARQUIVO       TEXT,  -- Filename
    TP_CONTEUDO      TEXT,  -- ContentType (mime)
    QT_BYTES         BIGINT,  -- tamanho declarado (dataField/source length)
    CO_URL_ORIGINAL  TEXT,  -- URL do CDN do WhatsApp, se presente (normalmente já expirada)
    CO_CAMINHO_ZIP   TEXT,
    TX_TRANSCRICAO   TEXT,
    TX_TITULO        TEXT,
    CO_FILE_ID       VARCHAR(80),
    CONSTRAINT FK44_WPP_TB05_WPP_TB04 FOREIGN KEY (ID_MENSAGEM)
        REFERENCES WPP_TB04_MENSAGENS (ID_MENSAGEM)
);

CREATE INDEX IX65_WPP_TB05_ID_MENSAGEM ON WPP_TB05_ANEXOS (ID_MENSAGEM);
CREATE INDEX IX79_WPP_TB05_CAMINHO_ZIP ON WPP_TB05_ANEXOS (CO_CAMINHO_ZIP);
CREATE INDEX IF NOT EXISTS IX153_WPP_TB05_FILE_ID ON WPP_TB05_ANEXOS (CO_FILE_ID) WHERE CO_FILE_ID IS NOT NULL;
CREATE INDEX IF NOT EXISTS IX189_WPP_TB05_TX_TRANSCRICAO_TRGM
    ON WPP_TB05_ANEXOS USING gin (TX_TRANSCRICAO gin_trgm_ops);

RESET search_path;
