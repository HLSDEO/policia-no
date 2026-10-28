-- ===================================================================================================================
-- Entidades automatizadas: padrões determinísticos sobre documentos genéricos.
-- Correspondente declarativo em db/init/47_entidades_automaticas.sql (forma completa, para
-- instalação nova). Idempotente: CREATE TABLE IF NOT EXISTS + seeds por ON CONFLICT, porque
-- instalação nova recebe o init já com as tabelas e este delta também passa por ela.
-- Sem backfill: documentos já analisados pela IA não são reprocessados.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS DOC_TB05_AUTO_PADROES (
    ID_AUTO_PADRAO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_PADRAO           VARCHAR(150) NOT NULL,
    TP_PADRAO           VARCHAR(20) NOT NULL DEFAULT 'regex',
    TP_ACHADO           VARCHAR(40) NOT NULL,
    NO_CATEGORIA        VARCHAR(80),
    DS_PADRAO           VARCHAR(500),
    TX_DEFINICAO        JSONB NOT NULL,
    IN_ATIVO            BOOLEAN NOT NULL DEFAULT true,
    DT_CADASTRO         TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR  BIGINT,
    CONSTRAINT FK406_DOC_TB05_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ11_DOC_TB05_NOME UNIQUE (NO_PADRAO)
);

CREATE TABLE IF NOT EXISTS DOC_TB06_AUTO_EXECUCOES (
    ID_EXECUCAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_USUARIO    BIGINT,
    CD_STATUS     VARCHAR(20) NOT NULL DEFAULT 'pendente',
    TX_ERRO       TEXT,
    QT_DETECCOES  INTEGER,
    DT_INICIO     TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM        TIMESTAMP,
    CONSTRAINT FK407_DOC_TB06_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK408_DOC_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IF NOT EXISTS IX421_DOC_TB06_INQUERITO ON DOC_TB06_AUTO_EXECUCOES (ID_INQUERITO);

CREATE TABLE IF NOT EXISTS DOC_TB07_AUTO_DETECCOES (
    ID_DETECCAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXECUCAO   BIGINT NOT NULL,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_AUTO_PADRAO BIGINT NOT NULL,
    ID_DOCUMENTO  BIGINT NOT NULL,
    TP_ACHADO     VARCHAR(40) NOT NULL,
    TX_VALOR      VARCHAR(300) NOT NULL,
    CO_CHAVE      VARCHAR(300) NOT NULL,
    TX_TRECHO     TEXT,
    QT_OCORRENCIAS INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT FK409_DOC_TB07_DOC_TB06 FOREIGN KEY (ID_EXECUCAO)
        REFERENCES DOC_TB06_AUTO_EXECUCOES (ID_EXECUCAO) ON DELETE CASCADE,
    CONSTRAINT FK410_DOC_TB07_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK411_DOC_TB07_DOC_TB05 FOREIGN KEY (ID_AUTO_PADRAO)
        REFERENCES DOC_TB05_AUTO_PADROES (ID_AUTO_PADRAO) ON DELETE CASCADE,
    CONSTRAINT FK412_DOC_TB07_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO) ON DELETE CASCADE,
    CONSTRAINT UQ12_DOC_TB07_DEDUP UNIQUE (ID_EXECUCAO, ID_DOCUMENTO, ID_AUTO_PADRAO, CO_CHAVE)
);

CREATE INDEX IF NOT EXISTS IX422_DOC_TB07_EXECUCAO ON DOC_TB07_AUTO_DETECCOES (ID_EXECUCAO);
CREATE INDEX IF NOT EXISTS IX423_DOC_TB07_INQUERITO ON DOC_TB07_AUTO_DETECCOES (ID_INQUERITO);
CREATE INDEX IF NOT EXISTS IX424_DOC_TB07_CHAVE ON DOC_TB07_AUTO_DETECCOES (ID_INQUERITO, TP_ACHADO, CO_CHAVE);

INSERT INTO DOC_TB05_AUTO_PADROES (NO_PADRAO, TP_ACHADO, NO_CATEGORIA, DS_PADRAO, TX_DEFINICAO) VALUES
    ('Procedimento IPL', 'procedimento', 'judicial',
     'Número de IPL (IPL 2024.0081190) ou de procedimento da PF (2024.0081190-SR/PF/ES).',
     '{"padrao": "IPL ?\\d{4}\\.\\d{7}|\\d{4}\\.\\d{7}-SR/PF/[A-Z]{2}", "grupo": 0, "validador": "nenhum", "normalizacao": "ipl"}'),
    ('Processo CNJ', 'processo', 'judicial',
     'Numeração única de processo (0000000-00.0000.0.00.0000).',
     '{"padrao": "\\d{7}-\\d{2}\\.\\d{4}\\.\\d\\.\\d{2}\\.\\d{4}", "grupo": 0, "validador": "cnj", "normalizacao": "cnj"}'),
    ('CPF', 'cpf', 'identificação',
     'CPF formatado com dígitos verificadores válidos.',
     '{"padrao": "\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}", "grupo": 0, "validador": "cpf", "normalizacao": "digitos"}'),
    ('CNPJ', 'cnpj', 'identificação',
     'CNPJ formatado com dígitos verificadores válidos.',
     '{"padrao": "\\d{2}\\.\\d{3}\\.\\d{3}/\\d{4}-\\d{2}", "grupo": 0, "validador": "cnpj", "normalizacao": "digitos"}'),
    ('Telefone brasileiro', 'telefone', 'contato',
     'Telefone com DDD, fixo ou celular com 9 (ex.: (27) 99847-7194).',
     '{"padrao": "\\(?\\d{2}\\)? ?\\d{4,5}-\\d{4}", "grupo": 0, "validador": "telefone", "normalizacao": "digitos"}'),
    ('E-mail', 'email', 'contato',
     'Endereço de e-mail.',
     '{"padrao": "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "grupo": 0, "validador": "email", "normalizacao": "minusculo"}'),
    ('OAB', 'oab', 'judicial',
     'Inscrição na OAB (OAB/SP 432.301).',
     '{"padrao": "OAB/[A-Z]{2} ?\\d{3,6}(?:\\.\\d{3})?", "grupo": 0, "validador": "nenhum", "normalizacao": "oab"}'),
    ('Placa de veículo', 'placa_veiculo', 'veículos',
     'Placa brasileira (padrão antigo AAA-9999 ou Mercosul AAA9A99).',
     '{"padrao": "[A-Z]{3}-?\\d[A-Z0-9]\\d{2}", "grupo": 0, "validador": "placa", "normalizacao": "placa"}'),
    ('Agência bancária', 'agencia', 'financeiro',
     'Agência após o rótulo ("Agência: 1643", "Ag. 4291").',
     '{"padrao": "(?:ag[eê]ncia|ag\\.?)[^\\d]{0,10}(\\d{3,5}-?\\d?)", "grupo": 1, "validador": "conta", "normalizacao": "digitos"}'),
    ('Conta bancária', 'conta', 'financeiro',
     'Conta após o rótulo ("Conta: 1288310-0", "c/c 274259") ou número avulso com dígito.',
     '{"padrao": "(?:conta(?: corrente)?|c/c|c\\.c\\.)[^\\d]{0,10}([\\d.\\-]{4,15})|\\b\\d{5,10}-\\d\\b", "grupo": 1, "validador": "conta", "normalizacao": "digitos"}'),
    ('Valor em dinheiro', 'valor', 'financeiro',
     'Valor monetário em reais (R$ 1.500,00).',
     '{"padrao": "R\\$ ?\\d[\\d.,]*", "grupo": 0, "validador": "valor", "normalizacao": "valor"}')
ON CONFLICT (NO_PADRAO) DO NOTHING;

RESET search_path;
