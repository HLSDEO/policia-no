-- ===================================================================================================================
-- Telefonia — o retorno da operadora na quebra de sigilo telefonico
--
-- Tabelas: TEL_TB01_TELEFONIA, TEL_TB02_ARQUIVOS, TEL_TB03_LINHAS, TEL_TB04_CHAMADAS,
--          TEL_TB05_ERBS, TEL_TB06_NUMEROS, TEL_TB07_SESSOES_WIFI, TEL_TB08_DADOS_MOVEIS
--
-- Diferente do inquerito em PDF, do IRPF e da IPEI, aqui nao chega UM arquivo: chega uma pasta.
-- O cadastro das linhas vem num .txt de largura fixa, o oficio vem numa planilha com uma aba por
-- assunto (chamadas, ERBs, numeros/IMEI, WiFi, TuGO) e o PDF da mesma planilha vem junto porque
-- e ele que e juntado aos autos. As ERBs de um periodo grande ainda chegam quebradas em varios
-- arquivos.
--
-- Por isso a chave do card NAO e o arquivo, e sim o par (inquerito, operadora): UX101 garante uma
-- linha de TB01 por operadora dentro do inquerito, e todo arquivo cai nela — em qualquer ordem.
-- TB02 registra cada arquivo importado com o que ele trouxe, para a procedencia nao se perder.
--
-- Toda tabela filha tem TX_CHAVE (sha256 do conteudo que IDENTIFICA a linha) com indice unico por
-- card. E isso que faz "varios arquivos, um card so" funcionar: a mesma chamada lida na planilha e
-- no PDF colide numa linha so, e reimportar o mesmo arquivo nao duplica nada. TP_FONTE diz de
-- onde a linha veio, e serve de criterio de desempate — a planilha tem mais coluna que o PDF, e
-- por isso ela sobrepoe (ver api/funcoes/telefonia_utils.py e docs/telefonia.md).
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · o card: uma operadora dentro de um inquerito
CREATE TABLE TEL_TB01_TELEFONIA (
    ID_TELEFONIA     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO     BIGINT NOT NULL,
    NO_OPERADORA     VARCHAR(3000) NOT NULL,  -- VIVO, CLARO, TIM... ou 'Nao identificada'
    QT_ARQUIVOS      INTEGER NOT NULL DEFAULT 0,
    QT_LINHAS        INTEGER NOT NULL DEFAULT 0,
    QT_CHAMADAS      INTEGER NOT NULL DEFAULT 0,
    QT_ERBS          INTEGER NOT NULL DEFAULT 0,
    QT_NUMEROS       INTEGER NOT NULL DEFAULT 0,
    QT_SESSOES       INTEGER NOT NULL DEFAULT 0,
    QT_DADOS         INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO       BIGINT,  -- quem importou o primeiro arquivo
    DT_EXTRACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO   TIMESTAMP NOT NULL DEFAULT now(),  -- ultimo arquivo que entrou no card
    CONSTRAINT FK283_TEL_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK284_TEL_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX284_TEL_TB01_ID_INQUERITO ON TEL_TB01_TELEFONIA (ID_INQUERITO);
-- o card e achado pela operadora antes de ser criado: e esta unicidade que faz o segundo arquivo
-- da VIVO cair no card da VIVO em vez de abrir um novo
CREATE UNIQUE INDEX UX101_TEL_TB01_INQUERITO_OPERADORA
    ON TEL_TB01_TELEFONIA (ID_INQUERITO, NO_OPERADORA);

-- 02 · cada arquivo que alimentou o card, com o periodo e o oficio que ele cobre
CREATE TABLE TEL_TB02_ARQUIVOS (
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

CREATE INDEX IX285_TEL_TB02_ID_TELEFONIA ON TEL_TB02_ARQUIVOS (ID_TELEFONIA);
-- a listagem de documentos do inquerito faz LEFT JOIN por aqui para nao mostrar o mesmo arquivo
-- duas vezes (uma no card de telefonia e outra como documento avulso)
CREATE INDEX IX286_TEL_TB02_ID_DOCUMENTO ON TEL_TB02_ARQUIVOS (ID_DOCUMENTO);
-- um documento anexado pertence a um card so. Parcial porque ID_DOCUMENTO e nulo quando o
-- arquivo entrou por fora da tela (ferramenta de linha de comando), e NULL nao conflita com NULL.
-- Reimportar o MESMO arquivo e evitado por nome, em telefonia_utils._registrar_arquivo.
CREATE UNIQUE INDEX UX102_TEL_TB02_TELEFONIA_DOCUMENTO
    ON TEL_TB02_ARQUIVOS (ID_TELEFONIA, ID_DOCUMENTO) WHERE ID_DOCUMENTO IS NOT NULL;

-- 03 · ficha cadastral de uma linha telefonica (o .txt de cadastro)
CREATE TABLE TEL_TB03_LINHAS (
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

CREATE INDEX IX287_TEL_TB03_ID_TELEFONIA ON TEL_TB03_LINHAS (ID_TELEFONIA);
CREATE INDEX IX288_TEL_TB03_NU_LINHA     ON TEL_TB03_LINHAS (ID_TELEFONIA, NU_LINHA);
-- o filtro de situacao da aba Cadastro de linhas
CREATE INDEX IX305_TEL_TB03_SITUACAO     ON TEL_TB03_LINHAS (ID_TELEFONIA, TP_SITUACAO);
-- a mesma linha aparece varias vezes no mesmo relatorio (uma ficha por contrato/endereco), entao
-- a chave inclui endereco e datas: o que nao pode repetir e a MESMA ficha, nao a mesma linha
CREATE UNIQUE INDEX UX103_TEL_TB03_TELEFONIA_CHAVE ON TEL_TB03_LINHAS (ID_TELEFONIA, TX_CHAVE);

-- 04 · registro de chamada (CDR). TuGO entra aqui tambem, distinguida por TP_REGISTRO: e a mesma
-- chamada, so que trafegada por IP, e separa-la em outra tabela quebraria a linha do tempo.
CREATE TABLE TEL_TB04_CHAMADAS (
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
CREATE INDEX IX289_TEL_TB04_TELEFONIA_DATA ON TEL_TB04_CHAMADAS (ID_TELEFONIA, DT_CHAMADA);
CREATE INDEX IX290_TEL_TB04_CHAMADOR       ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CHAMADOR);
CREATE INDEX IX291_TEL_TB04_CHAMADO        ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CHAMADO);
-- a paginacao no servidor (ver docs/telefonia.md): o filtro por tipo e a contagem por CGI
-- entram por aqui, e nao por varredura no CDR inteiro
CREATE INDEX IX299_TEL_TB04_TP_REGISTRO ON TEL_TB04_CHAMADAS (ID_TELEFONIA, TP_REGISTRO);
CREATE INDEX IX300_TEL_TB04_CGI_ORIGEM  ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CGI_ORIGEM)
    WHERE NU_CGI_ORIGEM IS NOT NULL AND NU_CGI_ORIGEM <> '';
CREATE INDEX IX301_TEL_TB04_CGI_DESTINO ON TEL_TB04_CHAMADAS (ID_TELEFONIA, NU_CGI_DESTINO)
    WHERE NU_CGI_DESTINO IS NOT NULL AND NU_CGI_DESTINO <> '';
-- a chave e a IDENTIDADE da chamada (quando, entre quem, por quanto tempo), e nao o retrato de
-- todas as colunas: a planilha traz o CGI e o PDF nao, e as duas leituras precisam colidir
CREATE UNIQUE INDEX UX104_TEL_TB04_TELEFONIA_CHAVE ON TEL_TB04_CHAMADAS (ID_TELEFONIA, TX_CHAVE);

-- 05 · a antena (ERB) que aparece no CDR, com o endereco e as coordenadas
CREATE TABLE TEL_TB05_ERBS (
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

CREATE INDEX IX292_TEL_TB05_ID_TELEFONIA ON TEL_TB05_ERBS (ID_TELEFONIA);
-- o CDR resolve a antena pelo CGI; sem este indice cada chamada faria varredura na tabela
CREATE INDEX IX293_TEL_TB05_NU_CGI       ON TEL_TB05_ERBS (ID_TELEFONIA, NU_CGI);
-- os filtros da aba Antenas (UF e "so com coordenada")
CREATE INDEX IX302_TEL_TB05_UF           ON TEL_TB05_ERBS (ID_TELEFONIA, SG_UF);
CREATE INDEX IX303_TEL_TB05_COM_COORD    ON TEL_TB05_ERBS (ID_TELEFONIA)
    WHERE VL_LATITUDE IS NOT NULL;
-- a antena e a mesma em todo oficio do inquerito: a chave e so o CGI, e o arquivo que chegar
-- depois completa o que faltava (o PDF nao tem o endereco que a planilha tem)
CREATE UNIQUE INDEX UX105_TEL_TB05_TELEFONIA_CHAVE ON TEL_TB05_ERBS (ID_TELEFONIA, TX_CHAVE);

-- 06 · o resumo "numero + IMEI" que a operadora fecha no fim do oficio
CREATE TABLE TEL_TB06_NUMEROS (
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

CREATE INDEX IX294_TEL_TB06_ID_TELEFONIA ON TEL_TB06_NUMEROS (ID_TELEFONIA);
CREATE INDEX IX295_TEL_TB06_NU_IMEI      ON TEL_TB06_NUMEROS (ID_TELEFONIA, NU_IMEI);
-- a ordem padrao (por telefone) e a busca da aba Numeros e IMEI
CREATE INDEX IX304_TEL_TB06_TELEFONE     ON TEL_TB06_NUMEROS (ID_TELEFONIA, NU_TELEFONE);
CREATE UNIQUE INDEX UX106_TEL_TB06_TELEFONIA_CHAVE ON TEL_TB06_NUMEROS (ID_TELEFONIA, TX_CHAVE);

-- 07 · sessao de chamada por WiFi (a linha ligando por IP, fora da rede movel)
CREATE TABLE TEL_TB07_SESSOES_WIFI (
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

CREATE INDEX IX296_TEL_TB07_ID_TELEFONIA ON TEL_TB07_SESSOES_WIFI (ID_TELEFONIA, DT_INICIO);
CREATE UNIQUE INDEX UX107_TEL_TB07_TELEFONIA_CHAVE ON TEL_TB07_SESSOES_WIFI (ID_TELEFONIA, TX_CHAVE);

-- 08 · sessão de dados móveis (o relatório de dados da operadora: CGI + IP + volumes por
-- intervalo). Cai no mesmo card da operadora que o CDR de voz; a antena da sessão completa
-- TEL_TB05 pelo CGI. Ver db/migracoes/0025_telefonia_dados_moveis.sql e docs/telefonia.md.
CREATE TABLE TEL_TB08_DADOS_MOVEIS (
    ID_DADO_MOVEL          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA           BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA   BIGINT,
    NU_CGI                 VARCHAR(3000),
    TX_IP                  VARCHAR(3000),
    TX_IPV6                VARCHAR(3000),
    NU_LINHA               VARCHAR(3000),
    NU_CORIDE              VARCHAR(3000),
    DT_INICIO              TIMESTAMP,
    TP_EVENTO_INICIO       VARCHAR(3000),
    DT_FIM                 TIMESTAMP,
    TP_EVENTO_FIM          VARCHAR(3000),
    NU_SITE                VARCHAR(3000),
    QT_VOL_ENTRADA         INTEGER,
    QT_VOL_SAIDA           INTEGER,
    TP_UNIDADE             VARCHAR(10),
    TP_TRAFEGO             VARCHAR(3000),
    TP_SERVICO             VARCHAR(3000),
    TP_TECNOLOGIA          VARCHAR(3000),
    TP_REDE                VARCHAR(3000),
    NU_IMEI                VARCHAR(3000),
    NU_CODOPE              VARCHAR(3000),
    TP_TIPO_ACESSO         VARCHAR(3000),
    TP_ORIGEM              VARCHAR(3000),
    SG_UF                  VARCHAR(10),
    TP_FONTE               VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE               VARCHAR(64) NOT NULL,
    CONSTRAINT FK300_TEL_TB08_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK301_TEL_TB08_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

CREATE INDEX IX306_TEL_TB08_TELEFONIA_DATA ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, DT_INICIO);
CREATE INDEX IX307_TEL_TB08_NU_LINHA ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, NU_LINHA);
CREATE INDEX IX308_TEL_TB08_NU_CGI ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, NU_CGI)
    WHERE NU_CGI IS NOT NULL AND NU_CGI <> '';
CREATE UNIQUE INDEX UX108_TEL_TB08_TELEFONIA_CHAVE ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, TX_CHAVE);
