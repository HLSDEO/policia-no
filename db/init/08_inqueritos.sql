-- ===================================================================================================================
-- Inquerito, permissoes e documentos anexados
--
-- Tabelas: INQ_TB01_INQUERITOS, INQ_TB03_PERMISSOES, INQ_TB04_DOCUMENTOS, INQ_TB06_APELIDOS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · inquérito (ePol)
CREATE TABLE INQ_TB01_INQUERITOS (
    ID_INQUERITO              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NU_INQUERITO              VARCHAR(1000) NOT NULL UNIQUE,  -- número do inquérito no ePol (ou "CASO-AAAA.NNNN" gerado, quando caso de inteligência)
    IN_CASO_INTELIGENCIA      BOOLEAN NOT NULL DEFAULT false,  -- true = caso de inteligência (mesmas fontes do inquérito, sem número de ePol)
    DT_CADASTRO               TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR        BIGINT,
    TX_CONTEXTO_INVESTIGACAO  TEXT,
    NO_DELEGACIA              VARCHAR(300),
    -- Situacao do inquerito em si (nao o andamento do processamento): 'ativo' | 'arquivado'.
    -- Arquivado e um caso encerrado que sai do foco da listagem mas continua integro e navegavel.
    -- So o gestor do inquerito (criador ou in_admin) muda - ver rotas/investigacao.py.
    TP_SITUACAO               VARCHAR(12) NOT NULL DEFAULT 'ativo',
    CONSTRAINT CK_INQ_TB01_TP_SITUACAO CHECK (TP_SITUACAO IN ('ativo', 'arquivado')),
    CONSTRAINT FK80_INQ_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX402_INQ_TB01_CASO_INTELIGENCIA
    ON INQ_TB01_INQUERITOS (IN_CASO_INTELIGENCIA) WHERE IN_CASO_INTELIGENCIA;

-- 03 · permissão adicional de acesso (além do criador) a um inquérito
CREATE TABLE INQ_TB03_PERMISSOES (
    ID_PERMISSAO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_USUARIO    BIGINT NOT NULL,
    IN_ADMIN      BOOLEAN NOT NULL DEFAULT FALSE,
    DT_CONCESSAO  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK81_INQ_TB03_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK82_INQ_TB03_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ03_INQ_TB03_INQUERITO_USUARIO UNIQUE (ID_INQUERITO, ID_USUARIO)
);

CREATE INDEX IX36_INQ_TB03_ID_INQUERITO ON INQ_TB03_PERMISSOES (ID_INQUERITO);

-- 04 · documento anexado ao inquérito — texto já extraído, pra IA ler via ferramenta
CREATE TABLE INQ_TB04_DOCUMENTOS (
    ID_DOCUMENTO        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO        BIGINT NOT NULL,
    NO_ARQUIVO          VARCHAR(3000) NOT NULL,  -- caminho do documento relativo a data/ (ex.: "FICCO-ES/2025.0079407/OUTROS/parecer.pdf")
    TP_ARQUIVO          VARCHAR(3000) NOT NULL,  -- pdf | csv | xlsx | docx | txt
    TX_CONTEUDO         TEXT,  -- texto extraído (nulo se TX_ERRO preenchido)
    QT_CARACTERES       INTEGER,
    TX_ERRO             TEXT,
    -- Hash do arquivo original (cadeia de custódia), calculado no momento da anexação. Nulo para
    -- documento importado antes deste recurso e para documento que mora num NÓ (o central não tem
    -- o arquivo em disco para hashear) — ver db/migracoes/0044_documento_hashes.sql.
    CO_HASH_MD5         CHAR(32),
    CO_HASH_SHA1        CHAR(40),
    CO_HASH_SHA256      CHAR(64),
    ID_USUARIO          BIGINT,  -- quem anexou
    DT_PROCESSADO       TIMESTAMP NOT NULL DEFAULT now(),
    CD_ANALISE          VARCHAR(20) NOT NULL DEFAULT '',
    DT_INICIO_ANALISE   TIMESTAMP,
    DT_FIM_ANALISE      TIMESTAMP,
    -- Batimento da análise em andamento: o watchdog de presas mede por aqui, não pelo início
    -- (ver db/migracoes/0054_analise_doc_atividade.sql).
    DT_ULTIMA_ATIVIDADE_ANALISE TIMESTAMP,
    TX_ERRO_ANALISE     TEXT,
    TX_RESUMO_IA        TEXT,
    JQ_PESSOAS          JSONB,
    JQ_ACHADOS          JSONB,
    ID_USUARIO_ANALISE  BIGINT,
    -- Documento que mora num NÓ descentralizado (docs/no_descentralizado.md): preenchido quando o
    -- arquivo vive na estação — a prova fica lá, o central guarda só o texto. O central lê o
    -- arquivo sob demanda (aba Original, reextração, blocos IPL/IRPF) por GET /no/arquivo.
    ID_SERVIDOR         BIGINT,
    CONSTRAINT FK122_INQ_TB04_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK123_INQ_TB04_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX57_INQ_TB04_ID_INQUERITO ON INQ_TB04_DOCUMENTOS (ID_INQUERITO);
CREATE INDEX IF NOT EXISTS IX92_INQ_TB04_DOC_ANALISE
    ON INQ_TB04_DOCUMENTOS (CD_ANALISE);

-- 06 · apelido livre de qualquer prova do card de investigação — o NO_APELIDO do 09 é só da
-- extração (.ufdr); esta tabela estende o rótulo aos demais blocos (rif, simba, documento, iped,
-- ipl, irpf, ipei, telefonia) endereçados pelo par (TP_PROVA, ID_PROVA). É só identificação no
-- card — não muda arquivo, escopo de IA nem permissão (ver js/investigacao.js, menu "Mais ações").
CREATE TABLE INQ_TB06_APELIDOS (
    ID_APELIDO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_PROVA        VARCHAR(20)  NOT NULL,
    ID_PROVA        BIGINT       NOT NULL,
    NO_APELIDO      VARCHAR(200) NOT NULL,
    ID_USUARIO      BIGINT,
    DT_ATUALIZACAO  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT UX112_INQ_TB06_PROVA UNIQUE (TP_PROVA, ID_PROVA),
    CONSTRAINT FK413_INQ_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

RESET search_path;
