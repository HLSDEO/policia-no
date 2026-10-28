-- ===================================================================================================================
-- Navegador, aplicativos instalados e uso
--
-- Tabelas: APP_TB01_APLICATIVOS, APU_TB01_USO_APLICATIVO, BSC_TB01_BUSCAS, COO_TB01_COOKIES,
--          DWN_TB01_DOWNLOADS, FAV_TB01_FAVORITOS, NAV_TB01_PAGINAS_VISITADAS, RDS_TB01_ATIVIDADE_SOCIAL,
--          USO_TB01_USO_APPS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · aplicativos instalados (InstalledApplication do UFDR)
CREATE TABLE APP_TB01_APLICATIVOS (
    ID_APLICATIVO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NU_PACOTE       TEXT,  -- Identifier (pacote Android, ex.: com.whatsapp)
    NO_APLICATIVO   TEXT,  -- Name (rótulo do app; costuma vir vazio no Android)
    NU_VERSAO       TEXT,  -- Version
    TX_DESCRICAO    TEXT,  -- Description
    DT_INSTALACAO   TIMESTAMP,  -- PurchaseDate (data de instalação/compra, quando disponível)
    DT_ULTIMO_USO   TIMESTAMP,  -- LastLaunched (último uso, quando disponível)
    CD_STATUS_UFDR  VARCHAR(50),
    TX_CATEGORIAS   TEXT,  -- Categories
    TX_PERMISSOES   TEXT,  -- Permissions concedidas
    TX_DIRETORIOS   TEXT,  -- AssociatedDirectoryPaths
    CONSTRAINT FK93_APP_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX42_APP_TB01_ID_EXTRACAO  ON APP_TB01_APLICATIVOS (ID_EXTRACAO);

-- =====================================================================
-- Tela de Aplicativos (aba "Uso") — ApplicationUsage, o agregado por app
-- (o USO_TB01 é evento a evento; este é o total acumulado).
-- =====================================================================
CREATE TABLE APU_TB01_USO_APLICATIVO (
    ID_USO              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    NU_PACOTE           VARCHAR(3000),  -- Identifier
    QT_ABERTURAS        BIGINT,  -- LaunchCount
    QT_ATIVACOES        BIGINT,  -- ActivationCount
    QT_TEMPO_ATIVO      BIGINT,  -- ActiveTime, em segundos
    QT_TEMPO_FUNDO      BIGINT,  -- BackgroundTime, em segundos
    QT_ULTIMA_DURACAO   BIGINT,  -- LastUsageDuration, em segundos
    DT_INICIO           TIMESTAMP,
    DT_FIM              TIMESTAMP,
    DT_ULTIMA_ABERTURA  TIMESTAMP,  -- LastLaunch
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK222_APU_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX176_APU_TB01_EXTRACAO ON APU_TB01_USO_APLICATIVO (ID_EXTRACAO);

-- O que a pessoa procurou (SearchedItem): Safari, Chrome, Gmail, Instagram,
-- Facebook, WhatsApp, Maps. Termo de busca é prova direta de intenção.
CREATE TABLE BSC_TB01_BUSCAS (
    ID_BUSCA        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- Source (onde buscou)
    NO_ORIGEM       VARCHAR(3000),  -- Origin (perfil/mecanismo)
    TX_TERMO        TEXT,  -- Value (o termo digitado)
    DT_BUSCA        TIMESTAMP,
    NO_CONTA        VARCHAR(3000),  -- Account
    TX_RESULTADOS   TEXT,  -- SearchResults
    VL_LATITUDE     NUMERIC(10,6),  -- Position (busca por lugar)
    VL_LONGITUDE    NUMERIC(10,6),
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK218_BSC_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX171_BSC_TB01_EXTRACAO ON BSC_TB01_BUSCAS (ID_EXTRACAO, DT_BUSCA);
CREATE INDEX IF NOT EXISTS IX172_BSC_TB01_TERMO_TRGM ON BSC_TB01_BUSCAS USING gin (TX_TERMO gin_trgm_ops);

-- =====================================================================
-- Tela de Navegação — cookies, favoritos, downloads e uso de rede.
-- =====================================================================
CREATE TABLE COO_TB01_COOKIES (
    ID_COOKIE           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    NO_COOKIE           VARCHAR(3000),  -- Name
    NO_DOMINIO          VARCHAR(3000),  -- Domain
    TX_CAMINHO          TEXT,  -- Path
    TX_VALOR            TEXT,  -- Value
    NO_APP_RELACIONADO  VARCHAR(3000),  -- RelatedApplication
    DT_CRIACAO          TIMESTAMP,
    DT_ULTIMO_ACESSO    TIMESTAMP,
    DT_EXPIRACAO        TIMESTAMP,
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK213_COO_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX164_COO_TB01_EXTRACAO ON COO_TB01_COOKIES (ID_EXTRACAO);
CREATE INDEX IF NOT EXISTS IX165_COO_TB01_DOMINIO ON COO_TB01_COOKIES (ID_EXTRACAO, NO_DOMINIO);

CREATE TABLE DWN_TB01_DOWNLOADS (
    ID_DOWNLOAD           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO           BIGINT NOT NULL,
    NO_APLICATIVO         VARCHAR(3000),  -- Source
    TX_URL                TEXT,  -- Url
    TX_DESTINO            TEXT,  -- TargetPath (onde ficou no aparelho)
    QT_BYTES              BIGINT,  -- FileSize
    QT_BYTES_RECEBIDOS    BIGINT,  -- BytesReceived
    CD_SITUACAO           VARCHAR(3000),  -- DownloadState
    DT_INICIO             TIMESTAMP,
    DT_FIM                TIMESTAMP,
    DT_ULTIMO_ACESSO      TIMESTAMP,
    TX_REDIRECIONAMENTOS  TEXT,  -- DownloadURLChains (cadeia até o arquivo final)
    CD_STATUS_UFDR        VARCHAR(50),
    CONSTRAINT FK215_DWN_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX167_DWN_TB01_EXTRACAO ON DWN_TB01_DOWNLOADS (ID_EXTRACAO);

CREATE TABLE FAV_TB01_FAVORITOS (
    ID_FAVORITO       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL,
    NO_APLICATIVO     VARCHAR(3000),  -- Source (Safari, Chrome…)
    TX_TITULO         TEXT,  -- Title
    TX_URL            TEXT,  -- Url
    TX_PASTA          TEXT,  -- Path (pasta de favoritos)
    QT_VISITAS        BIGINT,  -- VisitCount
    DT_ULTIMA_VISITA  TIMESTAMP,  -- LastVisited
    CD_STATUS_UFDR    VARCHAR(50),
    CONSTRAINT FK214_FAV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX166_FAV_TB01_EXTRACAO ON FAV_TB01_FAVORITOS (ID_EXTRACAO);

-- 01 · páginas visitadas no navegador (VisitedPage do UFDR)
CREATE TABLE NAV_TB01_PAGINAS_VISITADAS (
    ID_PAGINA       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   TEXT,  -- Source (Chrome, Samsung Internet, etc.)
    TX_TITULO       TEXT,  -- Title
    TX_URL          TEXT,  -- Url
    DT_VISITA       TIMESTAMP,  -- LastVisited
    QT_VISITAS      INTEGER,  -- VisitCount (quantas vezes a página foi aberta)
    TX_CONTA        TEXT,  -- Account (conta logada no navegador, quando houver)
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK92_NAV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX41_NAV_TB01_DT_VISITA    ON NAV_TB01_PAGINAS_VISITADAS (DT_VISITA);
-- Navegação: ORDER BY p.dt_visita DESC NULLS LAST, p.id_pagina
-- (ver _ORDENACOES_NAVEGACAO)
CREATE INDEX IF NOT EXISTS IX202_NAV_TB01_EXTRACAO_DATA
    ON NAV_TB01_PAGINAS_VISITADAS (ID_EXTRACAO, DT_VISITA DESC NULLS LAST, ID_PAGINA);

-- =====================================================================
-- Tela "Atividade social e buscas".
-- =====================================================================
-- O que a pessoa publicou (SocialMediaActivity): post, comentário, check-in.
-- Traz autor, texto, URL, coordenada e anexos — conteúdo de rede social que
-- hoje só existia se tivesse virado conversa.
CREATE TABLE RDS_TB01_ATIVIDADE_SOCIAL (
    ID_ATIVIDADE          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO           BIGINT NOT NULL,
    NO_APLICATIVO         VARCHAR(3000),  -- Source
    NO_PLATAFORMA         VARCHAR(3000),  -- Platform
    TP_ATIVIDADE          VARCHAR(3000),  -- SocialActivityType: Post | Comment | CheckIn | Update
    TX_TITULO             TEXT,  -- Title
    TX_CORPO              TEXT,  -- Body
    TX_URL                TEXT,  -- Url
    DT_ATIVIDADE          TIMESTAMP,  -- TimeStamp
    NO_AUTOR              VARCHAR(3000),  -- Author → Party.Name
    NU_AUTOR              VARCHAR(3000),  -- Author → Party.Identifier
    NO_CONTA              VARCHAR(3000),  -- Account
    CD_SITUACAO           VARCHAR(3000),  -- Status
    TP_CANAL              VARCHAR(3000),  -- ChannelType
    TX_PRIVACIDADE        TEXT,  -- PrivacySetting
    QT_REACOES            BIGINT,  -- ReactionsCount
    QT_COMPARTILHAMENTOS  BIGINT,  -- SharesCount
    QT_COMENTARIOS        BIGINT,  -- CommentCount
    VL_LATITUDE           NUMERIC(10,6),  -- Position → Coordinate
    VL_LONGITUDE          NUMERIC(10,6),
    TX_MARCADOS           TEXT,  -- TaggedParties (quem foi marcado)
    TX_ANEXOS             TEXT,  -- nomes dos anexos
    NU_POST_ORIGINAL      VARCHAR(3000),  -- OriginalPostId
    IN_DO_LOG_ATIVIDADE   BOOLEAN,  -- IsFromActivityLog
    CD_STATUS_UFDR        VARCHAR(50),
    CONSTRAINT FK217_RDS_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX169_RDS_TB01_EXTRACAO ON RDS_TB01_ATIVIDADE_SOCIAL (ID_EXTRACAO, DT_ATIVIDADE);
CREATE INDEX IF NOT EXISTS IX170_RDS_TB01_CORPO_TRGM ON RDS_TB01_ATIVIDADE_SOCIAL USING gin (TX_CORPO gin_trgm_ops);

-- =====================================================================
-- Tela "Atividade do aparelho" — o que o aparelho estava fazendo e quando.
-- =====================================================================
-- 01 · uso de aplicativo, evento a evento (AppsUsageLog). É o maior domínio
-- do UFDR: 413 mil linhas nos oito extratos. Responde "o que a pessoa estava
-- usando às 22h14" com granularidade de abrir/fechar.
CREATE TABLE USO_TB01_USO_APPS (
    ID_USO_APP      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- Source (quem registrou: Digital Wellbeing, KnowledgeC…)
    NU_PACOTE       VARCHAR(3000),  -- Identifier (com.whatsapp, com.itau…)
    TP_ACAO         VARCHAR(3000),  -- Action: BringToFocus | SendToBackground | Close | Copy
    DT_INICIO       TIMESTAMP,
    DT_FIM          TIMESTAMP,
    NO_SUBMODULO    VARCHAR(3000),  -- SubModule (tela/parte do app, quando o conector informa)
    NO_SERVICO      VARCHAR(3000),  -- ServiceIdentifier
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK208_USO_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX156_USO_TB01_EXTRACAO ON USO_TB01_USO_APPS (ID_EXTRACAO, DT_INICIO);
CREATE INDEX IF NOT EXISTS IX157_USO_TB01_PACOTE ON USO_TB01_USO_APPS (ID_EXTRACAO, NU_PACOTE);

RESET search_path;
