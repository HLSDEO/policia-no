-- Telefonia — cadastro de linhas, CDR, ERBs, numeros/IMEI e sessoes WiFi entregues pela operadora
-- na quebra de sigilo telefonico. Correspondente declarativo em db/init/43_telefonia.sql.
--
-- Contexto (docs/telefonia.md): o mesmo tratamento que o inquerito em PDF, o IRPF e a IPEI ja
-- tinham — o arquivo importado como "arquivo generico" e reconhecido e vira um bloco navegavel no
-- card da investigacao. A diferenca e que aqui a resposta da operadora vem em VARIOS arquivos, e
-- todos precisam cair num card so: a chave e (inquerito, operadora), e cada linha filha carrega
-- TX_CHAVE para o mesmo registro lido em dois arquivos nao duplicar.
--
-- Nada de backfill: arquivo de operadora ja anexado como documento comum continua sendo so texto
-- para a IA ate ser importado de novo (a leitura pega o arquivo do disco, que continua la).

SET search_path TO INVESTIGACAO, PUBLIC;


-- 01 · o card: uma operadora dentro de um inquerito
CREATE TABLE IF NOT EXISTS TEL_TB01_TELEFONIA (
    ID_TELEFONIA     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO     BIGINT NOT NULL,
    NO_OPERADORA     VARCHAR(3000) NOT NULL,  -- VIVO, CLARO, TIM... ou 'Nao identificada'
    QT_ARQUIVOS      INTEGER NOT NULL DEFAULT 0,
    QT_LINHAS        INTEGER NOT NULL DEFAULT 0,
    QT_CHAMADAS      INTEGER NOT NULL DEFAULT 0,
    QT_ERBS          INTEGER NOT NULL DEFAULT 0,
    QT_NUMEROS       INTEGER NOT NULL DEFAULT 0,
    QT_SESSOES       INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO       BIGINT,  -- quem importou o primeiro arquivo
    DT_EXTRACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO   TIMESTAMP NOT NULL DEFAULT now(),  -- ultimo arquivo que entrou no card
    CONSTRAINT FK283_TEL_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK284_TEL_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IF NOT EXISTS IX284_TEL_TB01_ID_INQUERITO ON TEL_TB01_TELEFONIA (ID_INQUERITO);
-- o card e achado pela operadora antes de ser criado: e esta unicidade que faz o segundo arquivo
-- da VIVO cair no card da VIVO em vez de abrir um novo
CREATE UNIQUE INDEX IF NOT EXISTS UX101_TEL_TB01_INQUERITO_OPERADORA
    ON TEL_TB01_TELEFONIA (ID_INQUERITO, NO_OPERADORA);

-- 02 · cada arquivo que alimentou o card, com o periodo e o oficio que ele cobre
CREATE TABLE IF NOT EXISTS TEL_TB02_ARQUIVOS (
    ID_ARQUIVO_TELEFONIA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_DOCUMENTO          BIGINT,  -- o arquivo anexado em INQ_TB04 (texto para a IA)
    NO_ARQUIVO            VARCHAR(3000) NOT NULL,
    TP_CONTEUDO           VARCHAR(20) NOT NULL,  -- cadastro | planilha | pdf
    TX_SECOES             TEXT,  -- o que este arquivo trouxe ("chamadas, erbs, numeros")
    NU_REFERENCIA         VARCHAR(3000),  -- Ref. do oficio, como a operadora escreve
    NU_LINHA_ALVO         VARCHAR(3000),  -- a linha investigada, quando o oficio nomeia uma
    DT_INICIO_PERIODO     DATE,
    DT_FIM_PERIODO        DATE,
    DT_SOLICITACAO        DATE,
    QT_REGISTROS          INTEGER NOT NULL DEFAULT 0,  -- linhas lidas deste arquivo
    ID_USUARIO            BIGINT,
    DT_IMPORTACAO         TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT CK15_TEL_TB02_TP_CONTEUDO CHECK (TP_CONTEUDO IN ('cadastro', 'planilha', 'pdf')),
    CONSTRAINT FK285_TEL_TB02_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK286_TEL_TB02_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK287_TEL_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IF NOT EXISTS IX285_TEL_TB02_ID_TELEFONIA ON TEL_TB02_ARQUIVOS (ID_TELEFONIA);
-- a listagem de documentos do inquerito faz LEFT JOIN por aqui para nao mostrar o mesmo arquivo
-- duas vezes (uma no card de telefonia e outra como documento avulso)
CREATE INDEX IF NOT EXISTS IX286_TEL_TB02_ID_DOCUMENTO ON TEL_TB02_ARQUIVOS (ID_DOCUMENTO);
-- um documento anexado pertence a um card so. Parcial porque ID_DOCUMENTO e nulo quando o
-- arquivo entrou por fora da tela (ferramenta de linha de comando), e NULL nao conflita com NULL.
-- Reimportar o MESMO arquivo e evitado por nome, em telefonia_utils._registrar_arquivo.
CREATE UNIQUE INDEX IF NOT EXISTS UX102_TEL_TB02_TELEFONIA_DOCUMENTO
    ON TEL_TB02_ARQUIVOS (ID_TELEFONIA, ID_DOCUMENTO) WHERE ID_DOCUMENTO IS NOT NULL;

-- 03 · ficha cadastral de uma linha telefonica (o .txt de cadastro)
CREATE TABLE IF NOT EXISTS TEL_TB03_LINHAS (
    ID_LINHA_TELEFONIA    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA  BIGINT,
    NU_LINHA              VARCHAR(3000) NOT NULL,  -- so digitos: (67) 99806-5096 -> 67998065096
    NO_CLIENTE            VARCHAR(3000),
    NU_CPF_CNPJ           VARCHAR(3000),
    NU_RG                 VARCHAR(3000),
    NO_MAE                VARCHAR(3000),
    DT_NASCIMENTO         DATE,
    TX_ENDERECO           VARCHAR(3000),
    TX_COMPLEMENTO        VARCHAR(3000),
    NO_BAIRRO             VARCHAR(3000),
    NU_CEP                VARCHAR(3000),
    NO_MUNICIPIO          VARCHAR(3000),
    SG_UF                 VARCHAR(10),
    TP_MODALIDADE         VARCHAR(3000),  -- PRE | POS, como a operadora escreve
    TP_SITUACAO           VARCHAR(3000),  -- ATIVO | INATIVO
    DT_HABILITACAO        DATE,
    DT_RESCISAO           DATE,
    TP_FONTE              VARCHAR(20) NOT NULL DEFAULT 'cadastro',
    TX_CHAVE              VARCHAR(64) NOT NULL,
    CONSTRAINT FK288_TEL_TB03_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK289_TEL_TB03_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

CREATE INDEX IF NOT EXISTS IX287_TEL_TB03_ID_TELEFONIA ON TEL_TB03_LINHAS (ID_TELEFONIA);
CREATE INDEX IF NOT EXISTS IX288_TEL_TB03_NU_LINHA     ON TEL_TB03_LINHAS (ID_TELEFONIA, NU_LINHA);
-- a mesma linha aparece varias vezes no mesmo relatorio (uma ficha por contrato/endereco), entao
-- a chave inclui endereco e datas: o que nao pode repetir e a MESMA ficha, nao a mesma linha
CREATE UNIQUE INDEX IF NOT EXISTS UX103_TEL_TB03_TELEFONIA_CHAVE ON TEL_TB03_LINHAS (ID_TELEFONIA, TX_CHAVE);

-- 04 · registro de chamada (CDR). TuGO entra aqui tambem, distinguida por TP_REGISTRO: e a mesma
-- chamada, so que trafegada por IP, e separa-la em outra tabela quebraria a linha do tempo.
CREATE TABLE IF NOT EXISTS TEL_TB04_CHAMADAS (
    ID_CHAMADA_TELEFONIA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA  BIGINT,
    TP_REGISTRO           VARCHAR(10) NOT NULL DEFAULT 'VOZ',  -- VOZ | TUGO
    DT_CHAMADA            TIMESTAMP,
    NU_CHAMADOR           VARCHAR(3000),
    NU_IMEI_CHAMADOR      VARCHAR(3000),
    NU_CHAMADO            VARCHAR(3000),
    NU_IMEI_CHAMADO       VARCHAR(3000),
    HR_DESCONEXAO         TIME,
    QT_DURACAO_SEGUNDOS   INTEGER,
    CD_STATUS             VARCHAR(3000),  -- Completada | Nao Compl. ...
    TP_TRAFEGO            VARCHAR(3000),  -- Voz | Dados | SMS
    NO_SERVICO            VARCHAR(3000),
    NU_GMT_ORIGEM         VARCHAR(3000),
    NU_GMT_DESTINO        VARCHAR(3000),
    NU_CGI_ORIGEM         VARCHAR(3000),  -- CGI da ERB que atendeu (casa com TEL_TB05.NU_CGI)
    NU_CGI_DESTINO        VARCHAR(3000),
    TP_TECNOLOGIA_ORIGEM  VARCHAR(3000),  -- LTE | WCDMA | GSM
    TP_TECNOLOGIA_DESTINO VARCHAR(3000),
    TX_EOT_A              VARCHAR(3000),  -- so TuGO
    TX_EOT_B              VARCHAR(3000),
    TX_IP_ORIGEM          VARCHAR(3000),
    TX_IP_DESTINO         VARCHAR(3000),
    NO_PAIS_ORIGEM        VARCHAR(3000),
    NO_PAIS_DESTINO       VARCHAR(3000),
    TP_FONTE              VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE              VARCHAR(64) NOT NULL,
    CONSTRAINT CK16_TEL_TB04_TP_REGISTRO CHECK (TP_REGISTRO IN ('VOZ', 'TUGO')),
    CONSTRAINT FK290_TEL_TB04_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK291_TEL_TB04_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

-- a tela lista em ordem cronologica e filtra por numero; o CDR de um ano nao cabe sem indice
CREATE INDEX IF NOT EXISTS IX289_TEL_TB04_TELEFONIA_DATA ON TEL_TB04_CHAMADAS (ID_TELEFONIA, DT_CHAMADA);
CREATE INDEX IF NOT EXISTS IX290_TEL_TB04_CHAMADOR       ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CHAMADOR);
CREATE INDEX IF NOT EXISTS IX291_TEL_TB04_CHAMADO        ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CHAMADO);
-- a chave e a IDENTIDADE da chamada (quando, entre quem, por quanto tempo), e nao o retrato de
-- todas as colunas: a planilha traz o CGI e o PDF nao, e as duas leituras precisam colidir
CREATE UNIQUE INDEX IF NOT EXISTS UX104_TEL_TB04_TELEFONIA_CHAVE ON TEL_TB04_CHAMADAS (ID_TELEFONIA, TX_CHAVE);

-- 05 · a antena (ERB) que aparece no CDR, com o endereco e as coordenadas
CREATE TABLE IF NOT EXISTS TEL_TB05_ERBS (
    ID_ERB                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA  BIGINT,
    NU_CGI                VARCHAR(3000) NOT NULL,  -- 724-10-0805342-003 (MCC-MNC-area-setor)
    NU_CCC                VARCHAR(3000),
    NO_CENTRAL            VARCHAR(3000),  -- central que atende a antena (ex.: CORE PS)
    NO_ERB                VARCHAR(3000),
    SG_ERB                VARCHAR(3000),  -- sigla da propria antena (ex.: TRGZ1)
    NU_SETOR              INTEGER,
    NU_AZIMUTE            INTEGER,        -- para onde o setor aponta, em graus
    TP_TECNOLOGIA         VARCHAR(3000),
    -- a operadora manda grau-minuto-segundo colado ("-24-30-44,73"); o texto original fica para
    -- conferencia e o grau decimal e o que vai para o mapa
    TX_LATITUDE           VARCHAR(3000),
    TX_LONGITUDE          VARCHAR(3000),
    VL_LATITUDE           NUMERIC(10, 7),
    VL_LONGITUDE          NUMERIC(10, 7),
    SG_UF                 VARCHAR(10),
    NO_MUNICIPIO          VARCHAR(3000),
    NO_BAIRRO             VARCHAR(3000),
    TX_ENDERECO           VARCHAR(3000),
    TP_FONTE              VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE              VARCHAR(64) NOT NULL,
    CONSTRAINT FK292_TEL_TB05_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK293_TEL_TB05_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

CREATE INDEX IF NOT EXISTS IX292_TEL_TB05_ID_TELEFONIA ON TEL_TB05_ERBS (ID_TELEFONIA);
-- o CDR resolve a antena pelo CGI; sem este indice cada chamada faria varredura na tabela
CREATE INDEX IF NOT EXISTS IX293_TEL_TB05_NU_CGI       ON TEL_TB05_ERBS (ID_TELEFONIA, NU_CGI);
-- a antena e a mesma em todo oficio do inquerito: a chave e so o CGI, e o arquivo que chegar
-- depois completa o que faltava (o PDF nao tem o endereco que a planilha tem)
CREATE UNIQUE INDEX IF NOT EXISTS UX105_TEL_TB05_TELEFONIA_CHAVE ON TEL_TB05_ERBS (ID_TELEFONIA, TX_CHAVE);

-- 06 · o resumo "numero + IMEI" que a operadora fecha no fim do oficio
CREATE TABLE IF NOT EXISTS TEL_TB06_NUMEROS (
    ID_NUMERO_TELEFONIA   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA  BIGINT,
    NU_TELEFONE           VARCHAR(3000) NOT NULL,
    -- IMEI zerado e o que a operadora grava quando nao sabe o aparelho do outro lado; ele entra
    -- como NULL para trinta numeros diferentes nao aparecerem "no mesmo aparelho"
    NU_IMEI               VARCHAR(3000),
    TP_FONTE              VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE              VARCHAR(64) NOT NULL,
    CONSTRAINT FK294_TEL_TB06_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK295_TEL_TB06_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

CREATE INDEX IF NOT EXISTS IX294_TEL_TB06_ID_TELEFONIA ON TEL_TB06_NUMEROS (ID_TELEFONIA);
CREATE INDEX IF NOT EXISTS IX295_TEL_TB06_NU_IMEI      ON TEL_TB06_NUMEROS (ID_TELEFONIA, NU_IMEI);
CREATE UNIQUE INDEX IF NOT EXISTS UX106_TEL_TB06_TELEFONIA_CHAVE ON TEL_TB06_NUMEROS (ID_TELEFONIA, TX_CHAVE);

-- 07 · sessao de chamada por WiFi (a linha ligando por IP, fora da rede movel)
CREATE TABLE IF NOT EXISTS TEL_TB07_SESSOES_WIFI (
    ID_SESSAO_TELEFONIA   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA          BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA  BIGINT,
    NU_LINHA              VARCHAR(3000),
    TX_IP                 VARCHAR(3000),
    NU_PORTA              INTEGER,
    DT_INICIO             TIMESTAMP,
    TP_EVENTO_INICIO      VARCHAR(3000),
    DT_FIM                TIMESTAMP,
    TP_EVENTO_FIM         VARCHAR(3000),
    TP_TRAFEGO            VARCHAR(3000),
    NO_SERVICO            VARCHAR(3000),
    TP_FONTE              VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE              VARCHAR(64) NOT NULL,
    CONSTRAINT FK296_TEL_TB07_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK297_TEL_TB07_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

CREATE INDEX IF NOT EXISTS IX296_TEL_TB07_ID_TELEFONIA ON TEL_TB07_SESSOES_WIFI (ID_TELEFONIA, DT_INICIO);
CREATE UNIQUE INDEX IF NOT EXISTS UX107_TEL_TB07_TELEFONIA_CHAVE ON TEL_TB07_SESSOES_WIFI (ID_TELEFONIA, TX_CHAVE);
