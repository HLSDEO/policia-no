-- ===================================================================================================================
-- Conta na nuvem: perfil, dispositivos, acessos e atividades
--
-- Tabelas: ACS_TB01_ACESSOS, ACS_TB02_AGREGADOS, ACS_TB03_CONEXOES, ATV_TB01_ATIVIDADES,
--          DVC_TB01_DISPOSITIVOS, EVT_TB01_EVENTOS, PRF_TB01_PERFIL, NUV_TB01_METADADOS_ARQUIVO
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 02 · eventos de acesso à conta (login, sincronização, app) — IP, agente,
-- produto e os identificadores de sessão/dispositivo vistos juntos naquele
-- evento
CREATE TABLE ACS_TB01_ACESSOS (
    ID_ACESSO             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO           BIGINT NOT NULL,
    DT_ACESSO             TIMESTAMP,
    DT_FIM_ACESSO         TIMESTAMP,  -- fim da sessao (Cellebrite RemoteServiceSession; NULL p/ evento instantaneo)
    NO_CONTA              VARCHAR(3000),  -- dono da conta na nuvem (Cellebrite Account; NULL p/ Takeout)
    TX_IP                 VARCHAR(3000),
    TX_IP_PROXY           VARCHAR(3000),
    IN_IP_NAO_ROTEAVEL    BOOLEAN,
    TX_USER_AGENT         TEXT,
    TX_PRODUTO            VARCHAR(3000),
    TX_SUBPRODUTO         VARCHAR(3000),
    TP_ACESSO             VARCHAR(3000),
    TX_CANAL              VARCHAR(3000),  -- canal de acesso (ex.: Gmail Access Channel)
    TX_ANDROID_ID         VARCHAR(3000),
    TX_IOS_ID             VARCHAR(3000),
    TX_OUTRO_DISPOSITIVO  VARCHAR(3000),
    TX_IMAP_ID            VARCHAR(3000),
    TX_ACTIVESYNC_ID      VARCHAR(3000),
    TX_RESOLUCAO          VARCHAR(3000),
    TX_OAUTH_LOGIN_ID     VARCHAR(3000),
    TX_STATEFUL_TOKEN_ID  VARCHAR(3000),
    TX_COOKIE_AUTH_TIME   VARCHAR(3000),
    NO_FONTE              VARCHAR(3000),
    CONSTRAINT FK191_ACS_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX135_ACS_TB01_ID_EXTRACAO ON ACS_TB01_ACESSOS (ID_EXTRACAO, DT_ACESSO);
CREATE INDEX IX136_ACS_TB01_TX_IP       ON ACS_TB01_ACESSOS (TX_IP);

-- 03 · serviços acessados por identificador (dias ativos, multilogin, canal)
CREATE TABLE ACS_TB02_AGREGADOS (
    ID_ACESSO_AGR          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO            BIGINT NOT NULL,
    DT_PRIMEIRA            TIMESTAMP,
    DT_ULTIMA              TIMESTAMP,
    QT_DIAS_ATIVOS         INTEGER,
    TP_IDENTIFICADOR       VARCHAR(3000),
    TX_IDENTIFICADOR       TEXT,
    IN_MULTILOGIN          BOOLEAN,
    TP_CANAL_AUTENTICACAO  VARCHAR(3000),
    NO_FONTE               VARCHAR(3000),
    CONSTRAINT FK192_ACS_TB02_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX137_ACS_TB02_ID_EXTRACAO ON ACS_TB02_AGREGADOS (ID_EXTRACAO);

-- 04 · conexões entre identificadores vistos juntos numa sessão autenticada
-- (IP ↔ cookie de sessão, Android device ID ↔ OAuth login ID, ...)
CREATE TABLE ACS_TB03_CONEXOES (
    ID_CONEXAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO  BIGINT NOT NULL,
    DT_PRIMEIRA  TIMESTAMP,
    DT_ULTIMA    TIMESTAMP,
    TP_IDENT_1   VARCHAR(3000),
    TX_IDENT_1   TEXT,
    TP_IDENT_2   VARCHAR(3000),
    TX_IDENT_2   TEXT,
    NO_FONTE     VARCHAR(3000),
    CONSTRAINT FK193_ACS_TB03_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX138_ACS_TB03_ID_EXTRACAO ON ACS_TB03_CONEXOES (ID_EXTRACAO);

-- 01 · Minha Atividade — busca, YouTube, Maps, Chrome etc., por produto, com
-- descrição e horário
CREATE TABLE ATV_TB01_ATIVIDADES (
    ID_ATIVIDADE  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO   BIGINT NOT NULL,
    DT_ATIVIDADE  TIMESTAMP,
    TX_PRODUTO    VARCHAR(3000),  -- "Image Search", "YouTube", "Chrome", ...
    TX_DESCRICAO  TEXT,
    TX_URL        VARCHAR(3000),
    NO_FONTE      VARCHAR(3000),
    CONSTRAINT FK190_ATV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX134_ATV_TB01_ID_EXTRACAO ON ATV_TB01_ATIVIDADES (ID_EXTRACAO, DT_ATIVIDADE);

-- 05 · dispositivos conhecidos pela conta
CREATE TABLE DVC_TB01_DISPOSITIVOS (
    ID_DISPOSITIVO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_DISPOSITIVO  VARCHAR(3000),  -- modelo
    TX_FABRICANTE   VARCHAR(3000),
    TX_ANDROID_ID   VARCHAR(3000),
    TX_IMEI         VARCHAR(3000),
    TX_VERSAO       VARCHAR(3000),
    TX_SERIAL       VARCHAR(3000),
    TX_OUTROS       TEXT,  -- demais pares do relatório (JSON)
    TP_ORIGEM       VARCHAR(20),
    NO_FONTE        VARCHAR(3000),
    CONSTRAINT FK194_DVC_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX139_DVC_TB01_ID_EXTRACAO ON DVC_TB01_DISPOSITIVOS (ID_EXTRACAO);
CREATE INDEX IX144_DVC_TB01_IMEI ON DVC_TB01_DISPOSITIVOS (TX_IMEI)
    WHERE TX_IMEI IS NOT NULL AND length(trim(TX_IMEI)) >= 14;

-- 07 · agenda (eventos de calendário)
CREATE TABLE EVT_TB01_EVENTOS (
    ID_EVENTO         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL,
    DT_INICIO         TIMESTAMP,
    DT_FIM            TIMESTAMP,
    DT_CRIACAO        TIMESTAMP,
    DT_ALTERACAO      TIMESTAMP,
    TX_TITULO         VARCHAR(3000),
    TX_LOCAL          VARCHAR(3000),
    TX_DESCRICAO      TEXT,
    TX_ORGANIZADOR    VARCHAR(3000),
    TX_PARTICIPANTES  TEXT,  -- um por linha
    NO_FONTE          VARCHAR(3000),
    CONSTRAINT FK196_EVT_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX141_EVT_TB01_ID_EXTRACAO ON EVT_TB01_EVENTOS (ID_EXTRACAO, DT_INICIO);

-- 06 · perfil da conta (nome, e-mail, criação, país, idioma, ...)
CREATE TABLE PRF_TB01_PERFIL (
    ID_PERFIL              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO            BIGINT NOT NULL,
    NO_NOME                VARCHAR(3000),
    NO_NOME_DADO           VARCHAR(3000),
    NO_NOME_FAMILIA        VARCHAR(3000),
    TX_EMAIL               VARCHAR(3000),
    TX_EMAIL_ALTERNATIVOS  TEXT,
    DT_CRIACAO             TIMESTAMP,
    TX_IP_TOS              VARCHAR(3000),
    TX_IDIOMA              VARCHAR(3000),
    TX_PAIS                VARCHAR(3000),
    TX_NASCIMENTO          VARCHAR(3000),
    TX_SERVICOS            TEXT,
    DT_ULTIMA_ATUALIZACAO  TIMESTAMP,
    DT_EXCLUSAO            TIMESTAMP,
    TX_STATUS              VARCHAR(3000),
    NO_FONTE               VARCHAR(3000),
    CONSTRAINT FK195_PRF_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX140_PRF_TB01_ID_EXTRACAO ON PRF_TB01_PERFIL (ID_EXTRACAO);

-- 08 · metadado que a nuvem guarda SOBRE um arquivo (o "sidecar"), separado do arquivo em si.
--
-- No Google Takeout todo arquivo do Drive vem acompanhado de "<nome>-info.json" e toda foto do
-- Google Photos de "<nome>.jpg.json". Esses JSON não são arquivos do usuário: descrevem outro
-- arquivo, e trazem o que só o servidor sabe — data de criação/modificação DO SERVIDOR (que o
-- suspeito não altera mexendo no relógio do aparelho), quem mais tinha acesso, se está na
-- lixeira, de qual aparelho a foto subiu e onde ela foi tirada.
--
-- Até a v1 esses itens viravam só mais uma linha de arquivo .json em ARQ_TB01_ARQUIVOS e o
-- conteúdo era descartado. É este metadado que sustenta a tela "Nuvem × aparelho"
-- (funcoes/correlacao_nuvem_utils.py): sem ele não dá para dizer que a foto que sumiu do
-- celular subiu DAQUELE aparelho, nem quando.
--
-- A ligação com ARQ_TB01_ARQUIVOS é por (ID_EXTRACAO, NO_ARQUIVO_ALVO) e não por FK: o sidecar
-- é lido na mesma passada em que os arquivos entram por COPY em lote, que não devolve
-- ID_ARQUIVO. O índice IX203_ARQ_TB01_EXTRACAO_NOME_LOWER é o outro lado dessa junção.
CREATE TABLE NUV_TB01_METADADOS_ARQUIVO (
    ID_METADADO             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO             BIGINT NOT NULL,
    NO_ARQUIVO_ALVO         TEXT NOT NULL,  -- nome do arquivo que este sidecar descreve, minúsculo
    NO_FONTE                VARCHAR(3000),  -- 'Google Drive' | 'Google Photos'
    TX_TITULO               TEXT,  -- title/description que a nuvem guarda (pode diferir do nome do arquivo)
    QT_BYTES                BIGINT,
    -- os quatro tempos. DT_CRIACAO/DT_MODIFICACAO são o que o cliente informou; os
    -- DT_*_SERVIDOR são carimbo do Google e é neles que se confia numa contestação de data.
    DT_CRIACAO              TIMESTAMP,
    DT_MODIFICACAO          TIMESTAMP,
    DT_CRIACAO_SERVIDOR     TIMESTAMP,
    DT_MODIFICACAO_SERVIDOR TIMESTAMP,
    DT_VISUALIZACAO         TIMESTAMP,  -- viewed_by_me
    DT_CAPTURA              TIMESTAMP,  -- photoTakenTime (Google Photos): quando a foto foi TIRADA
    IN_LIXEIRA              BOOLEAN,  -- trashed
    IN_LIXEIRA_EXPLICITA    BOOLEAN,  -- is_explicitly_trashed: o usuário mandou pra lixeira, não caiu por arrasto de pasta
    IN_FAVORITO             BOOLEAN,  -- starred
    NO_PROPRIETARIO         VARCHAR(3000),  -- e-mail do dono
    TX_COMPARTILHADO_COM    TEXT,  -- e-mails de leitores/escritores/comentadores, separados por vírgula
    -- de onde a foto subiu (googlePhotosOrigin): 'mobileUpload' | 'webUpload' | 'sharedAlbum' | 'driveBackup'…
    TP_ORIGEM_UPLOAD        VARCHAR(3000),
    NO_DISPOSITIVO_UPLOAD   VARCHAR(3000),  -- deviceType/modelo do aparelho que subiu
    NO_PASTA_DISPOSITIVO    VARCHAR(3000),  -- deviceFolder: a pasta do aparelho de origem (ex.: "WhatsApp Images")
    VL_LATITUDE             NUMERIC(10,6),  -- geoData da foto
    VL_LONGITUDE            NUMERIC(10,6),
    TX_ATIVIDADE            JSONB,  -- activity_records do Drive: quem fez o quê e quando, cru
    CONSTRAINT FK263_NUV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

-- a junção da tela é sempre "os sidecars desta extração, por nome do arquivo descrito"
CREATE INDEX IX257_NUV_TB01_EXTRACAO_ALVO ON NUV_TB01_METADADOS_ARQUIVO (ID_EXTRACAO, NO_ARQUIVO_ALVO);

RESET search_path;
