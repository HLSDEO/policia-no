-- ===================================================================================================================
-- Aparelho, dono, redes e transferencias
--
-- Tabelas: CAL_TB01_CALENDARIO, CNX_TB01_CONEXOES, DEV_TB01_INFO_APARELHO, DON_TB01_DONOS,
--          GRV_TB01_GRAVACOES, NOT_TB01_NOTAS, RCD_TB01_DISPOSITIVOS, RDE_TB01_USO_REDE,
--          TRF_TB01_TRANSFERENCIAS, UPL_TB01_ENVIOS_NUVEM, WIFI_TB01_REDES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- =====================================================================
-- Tela "Calendário" — CalendarEntry.
-- =====================================================================
CREATE TABLE CAL_TB01_CALENDARIO (
    ID_COMPROMISSO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    NO_CATEGORIA        VARCHAR(3000),  -- Category (agenda de origem)
    TX_ASSUNTO          TEXT,  -- Subject
    TX_DETALHES         TEXT,  -- Details
    TX_LOCAL            TEXT,  -- Location
    DT_INICIO           TIMESTAMP,  -- StartDate
    DT_FIM              TIMESTAMP,  -- EndDate
    TX_CONVIDADOS       TEXT,  -- Attendees → Party
    NO_ORGANIZADOR      VARCHAR(3000),  -- Organizer → Party
    TX_REPETICAO        TEXT,  -- RepeatRule/RepeatDay/RepeatInterval consolidados
    TX_LEMBRETES        TEXT,  -- Reminders
    CD_SITUACAO         VARCHAR(3000),  -- Status
    TX_PRIORIDADE       TEXT,  -- Priority
    TX_DISPONIBILIDADE  TEXT,  -- Availability
    NO_CONTA            VARCHAR(3000),  -- Account
    VL_LATITUDE         NUMERIC(10,6),  -- EventPosition → Coordinate
    VL_LONGITUDE        NUMERIC(10,6),
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK221_CAL_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX175_CAL_TB01_EXTRACAO ON CAL_TB01_CALENDARIO (ID_EXTRACAO, DT_INICIO);

-- =====================================================================
-- Tela de Wi-Fi (aba "Conexões") — DeviceConnectivity.
-- =====================================================================
CREATE TABLE CNX_TB01_CONEXOES (
    ID_CONEXAO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- Source
    TP_METODO       VARCHAR(3000),  -- ConnectivityMethod: WiFi | Bluetooth
    TP_NATUREZA     VARCHAR(3000),  -- ConnectivityNature (conectou, pareou…)
    TP_DISPOSITIVO  VARCHAR(3000),  -- DeviceType
    NO_DISPOSITIVO  VARCHAR(3000),  -- DeviceName
    NO_MAC          VARCHAR(3000),  -- DeviceIdentifiers → MAC
    DT_CONEXAO      TIMESTAMP,  -- StartTime
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK212_CNX_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX163_CNX_TB01_EXTRACAO ON CNX_TB01_CONEXOES (ID_EXTRACAO, DT_CONEXAO);

-- =====================================================================
-- Tela "Identidade do aparelho" — DeviceInfoEntry e CellTower.
-- =====================================================================
-- DeviceInfoEntry é o bloco de identidade que o Cellebrite monta: IMEI, IMSI,
-- ICCID de cada chip, MSISDN, número de série, MAC, ID do Android, Apple ID,
-- fuso horário, data de reset de fábrica e até a senha do aparelho. É de onde
-- sai o insumo de convergência entre casos — e o que hoje era deduzido a
-- custo alto em _derivar_dono_ios.
CREATE TABLE DEV_TB01_INFO_APARELHO (
    ID_INFO         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- Source
    NO_CATEGORIA    VARCHAR(3000),  -- EntryCategory (Geral, Metadata…)
    NO_CAMPO        VARCHAR(3000),  -- EntryName (IMEI1, ICCID, MSISDN, ID da Apple…)
    TX_VALOR        TEXT,  -- EntryValue
    DT_REGISTRO     TIMESTAMP,
    CD_STATUS_UFDR  VARCHAR(50),
    CONSTRAINT FK224_DEV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX178_DEV_TB01_EXTRACAO ON DEV_TB01_INFO_APARELHO (ID_EXTRACAO);
-- busca por identificador (IMEI/ICCID/MSISDN) atravessando casos
CREATE INDEX IF NOT EXISTS IX179_DEV_TB01_VALOR ON DEV_TB01_INFO_APARELHO (TX_VALOR);

-- 01 · dono(s) do aparelho (User do UFDR) — identidade do titular, além do que
-- se infere pelo IsPhoneOwner dos contatos de conversa
CREATE TABLE DON_TB01_DONOS (
    ID_DONO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL,
    NO_DONO           TEXT,  -- Name
    TP_USUARIO        VARCHAR(3000),  -- UserType (Owner, etc.)
    NU_IDENTIFICADOR  TEXT,  -- Identifier (id do perfil no aparelho)
    DT_ULTIMO_LOGIN   TIMESTAMP,  -- TimeLastLoggedIn
    CONSTRAINT FK94_DON_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX43_DON_TB01_ID_EXTRACAO  ON DON_TB01_DONOS (ID_EXTRACAO);

-- Recording: gravações de áudio feitas no aparelho (gravador, chamada).
CREATE TABLE GRV_TB01_GRAVACOES (
    ID_GRAVACAO          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_APLICATIVO        VARCHAR(3000),  -- Source
    TX_TITULO            TEXT,  -- Title
    TP_GRAVACAO          VARCHAR(3000),  -- Type
    DT_GRAVACAO          TIMESTAMP,
    QT_DURACAO_SEGUNDOS  BIGINT,  -- Duration
    CD_STATUS_UFDR       VARCHAR(50),
    CONSTRAINT FK220_GRV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX174_GRV_TB01_EXTRACAO ON GRV_TB01_GRAVACOES (ID_EXTRACAO);

CREATE TABLE NOT_TB01_NOTAS (
    ID_NOTA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO     BIGINT NOT NULL,
    NO_APLICATIVO   VARCHAR(3000),  -- app de origem: Samsung Notes, Apple iCloud Backup, Notes, Google Keep…
    TX_TITULO       TEXT,  -- ausente no iOS na prática
    TX_CORPO        TEXT,
    DT_CRIACAO      TIMESTAMP,  -- pode vir vazio (<empty/>) no UFDR, principalmente no iOS
    DT_MODIFICACAO  TIMESTAMP,
    CD_STATUS       VARCHAR(3000),  -- deleted_state do UFDR: Intact, Trash, Deleted…
    TX_ANEXOS       TEXT,  -- nomes dos anexos (fotos/arquivos) separados por vírgula; sem binário
    TX_RESUMO       TEXT,  -- Summary (prévia da nota no iOS)
    NO_PASTA        VARCHAR(3000),  -- Folder
    NO_CONTA        VARCHAR(3000),  -- Account
    CONSTRAINT FK154_NOT_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX88_NOT_TB01_EXTRACAO ON NOT_TB01_NOTAS (ID_EXTRACAO);
CREATE INDEX IX89_NOT_TB01_CORPO_TRGM ON NOT_TB01_NOTAS USING gin (TX_CORPO gin_trgm_ops);

-- =====================================================================
-- Tela "Dispositivos reconhecidos" — quem estava por perto.
-- =====================================================================
-- Aparelhos que o celular reconheceu ao redor (RecognizedDevice): 64 mil nos
-- oito extratos, quase todos identificados por MAC. Liga dois aparelhos que
-- estiveram no mesmo lugar sem depender de conversa entre eles.
CREATE TABLE RCD_TB01_DISPOSITIVOS (
    ID_DISPOSITIVO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    NO_DISPOSITIVO      VARCHAR(3000),  -- Name
    TP_DISPOSITIVO      VARCHAR(3000),  -- DeviceType
    NO_MODELO           VARCHAR(3000),  -- DeviceModel
    NU_SERIE            VARCHAR(3000),  -- SerialNumber
    NO_MAC              VARCHAR(3000),  -- DeviceIdentifiers → "MAC Address"
    TX_IDENTIFICADORES  TEXT,  -- demais pares chave/valor dos DeviceIdentifiers
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK211_RCD_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX161_RCD_TB01_EXTRACAO ON RCD_TB01_DISPOSITIVOS (ID_EXTRACAO);
-- o MAC é o que permite cruzar aparelhos entre extrações do mesmo caso
CREATE INDEX IF NOT EXISTS IX162_RCD_TB01_MAC ON RCD_TB01_DISPOSITIVOS (NO_MAC) WHERE NO_MAC IS NOT NULL;

CREATE TABLE RDE_TB01_USO_REDE (
    ID_USO_REDE         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO         BIGINT NOT NULL,
    NO_APLICATIVO       VARCHAR(3000),  -- Source
    NO_SSID             VARCHAR(3000),  -- SSId da rede usada
    TP_CONEXAO          VARCHAR(3000),  -- NetworkConnectionType
    TP_USO              VARCHAR(3000),  -- UsageMode
    IN_ROAMING          BOOLEAN,  -- IsRoaming
    QT_BYTES_RECEBIDOS  BIGINT,  -- NumberOfBytesReceived
    QT_BYTES_ENVIADOS   BIGINT,  -- NumberOfBytesSent
    DT_INICIO           TIMESTAMP,  -- DateStarted
    DT_FIM              TIMESTAMP,  -- DateEnded
    CD_STATUS_UFDR      VARCHAR(50),
    CONSTRAINT FK216_RDE_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX168_RDE_TB01_EXTRACAO ON RDE_TB01_USO_REDE (ID_EXTRACAO);

-- =====================================================================
-- Tela de Contas bancárias — transferências, cartão de crédito e carteira.
-- =====================================================================
-- TransferOfFunds é dinheiro modelado pelo próprio Cellebrite: valor, moeda,
-- participantes e situação. Entra ao lado das contas porque é o mesmo assunto
-- das telas de RIF e SIMBA.
CREATE TABLE TRF_TB01_TRANSFERENCIAS (
    ID_TRANSFERENCIA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO       BIGINT NOT NULL,
    NO_APLICATIVO     VARCHAR(3000),  -- Source (app que registrou)
    TP_TRANSFERENCIA  VARCHAR(3000),  -- TransferType
    TX_DESCRICAO      TEXT,  -- Description
    VL_VALOR          NUMERIC(18,2),  -- TransferAmount → Price.Amount
    NO_MOEDA          VARCHAR(50),  -- TransferAmount → Price.Currency
    CD_SITUACAO       VARCHAR(3000),  -- Status
    TX_PARTICIPANTES  TEXT,  -- Participants → Party
    DT_ENVIO          TIMESTAMP,  -- DateSent
    DT_PROCESSAMENTO  TIMESTAMP,  -- DateProcessed
    CD_STATUS_UFDR    VARCHAR(50),
    CONSTRAINT FK227_TRF_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX183_TRF_TB01_EXTRACAO ON TRF_TB01_TRANSFERENCIAS (ID_EXTRACAO, DT_ENVIO);

-- =====================================================================
-- Tela de Arquivos — aba de envios para a nuvem e aba de gravações.
-- =====================================================================
-- FileUpload: o que saiu do aparelho para a nuvem (Drive, iCloud, etc.),
-- com conta, pasta e configuração de compartilhamento. SÓ iPhone nos
-- extratos auditados.
CREATE TABLE UPL_TB01_ENVIOS_NUVEM (
    ID_ENVIO             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_APLICATIVO        VARCHAR(3000),  -- Source
    NO_ARQUIVO           VARCHAR(3000),  -- Name
    TX_TITULO            TEXT,  -- Title
    TX_URL               TEXT,  -- Url
    TP_ARQUIVO           VARCHAR(3000),  -- FileType
    NO_PASTA             VARCHAR(3000),  -- Folder
    NO_CONTA             VARCHAR(3000),  -- Account
    NO_DONO              VARCHAR(3000),  -- Owner → Party
    TX_COMPARTILHAMENTO  TEXT,  -- ShareSettings (quem podia ver)
    DT_ENVIO             TIMESTAMP,  -- DateUploaded
    DT_MODIFICACAO       TIMESTAMP,  -- DateLastModified
    CD_STATUS_UFDR       VARCHAR(50),
    CONSTRAINT FK219_UPL_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX173_UPL_TB01_EXTRACAO ON UPL_TB01_ENVIOS_NUVEM (ID_EXTRACAO, DT_ENVIO);

CREATE TABLE WIFI_TB01_REDES (
    ID_REDE_WIFI         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO          BIGINT NOT NULL,
    NO_SSID              VARCHAR(3000),  -- Nome da rede (Service Set Identifier)
    NO_BSSID             VARCHAR(3000),  -- MAC address do access point (Basic SSID)
    TP_SEGURANCA         VARCHAR(3000),  -- WPA2, WPA, WEP, Open, …
    CO_SENHA             VARCHAR(3000),  -- Senha / Pre-Shared Key (quando disponível)
    QT_CONEXOES          BIGINT,  -- Quantidade de vezes que conectou
    DT_PRIMEIRA_CONEXAO  TIMESTAMP,  -- Primeira vez que conectou
    DT_ULTIMA_CONEXAO    TIMESTAMP,  -- Última vez que conectou
    NR_CANAL             BIGINT,  -- Canal de frequência
    NR_SINAL             BIGINT,  -- Intensidade do sinal (dBm)
    IN_REDE_OCULTA       BOOLEAN,  -- SSID oculto (hidden network)
    TP_AUTENTICACAO      VARCHAR(3000),  -- Algoritmo de autenticação
    NO_FABRICANTE        VARCHAR(3000),  -- Fabricante/OEM do roteador (se identificado)
    IN_FAVORITA          BOOLEAN,  -- Rede marcada como favorita
    NO_FONTE             VARCHAR(3000),  -- Fonte do dado no UFDR (ex.: "WirelessNetwork")
    CD_STATUS_UFDR       VARCHAR(50),
    -- 86% das redes Wi-Fi dos extratos auditados trazem coordenada geográfica em
    -- <modelField name="Position">, e nenhuma era importada. É georreferenciamento
    -- fino de onde o aparelho esteve. O SSID falta em 81% dos registros (só há
    -- BSSID + posição), daí a tela precisar saber lidar com rede sem nome.
    VL_LATITUDE          NUMERIC(10,6),  -- NWConnectionType
    VL_LONGITUDE         NUMERIC(10,6),
    TP_CONEXAO           VARCHAR(3000),  -- NWConnectionType
    CONSTRAINT FK155_WIFI_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IX96_WIFI_TB01_EXTRACAO ON WIFI_TB01_REDES (ID_EXTRACAO);
CREATE INDEX IX97_WIFI_TB01_SSID_TRGM ON WIFI_TB01_REDES USING gin (NO_SSID gin_trgm_ops);
CREATE INDEX IX98_WIFI_TB01_BSSID ON WIFI_TB01_REDES (NO_BSSID);
CREATE INDEX IF NOT EXISTS IX150_WIFI_TB01_COORD ON WIFI_TB01_REDES (VL_LATITUDE, VL_LONGITUDE)
    WHERE VL_LATITUDE IS NOT NULL;

RESET search_path;
