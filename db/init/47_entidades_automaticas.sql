-- ===================================================================================================================
-- Entidades automatizadas: padrões determinísticos sobre documentos genéricos
--
-- Hoje a análise de entidades de um documento anexado (INQ_TB04_DOCUMENTOS) é 100% IA
-- (funcoes/documentos_utils.py): a produção mostra 36% dos achados caindo no tipo genérico
-- "documento" e CPF/CNPJ/CNJ válidos perdidos ali (inquérito 187: 180 + 277 + 3). Estas
-- tabelas são a biblioteca global de regex com validação (mantida por admin na aba
-- "Entidades automatizadas") e as execuções/detecções por inquérito — o determinístico roda
-- primeiro (barato, total, auditável) e a IA entra como complemento no resíduo.
--
-- Tabelas: DOC_TB05_AUTO_PADROES (biblioteca), DOC_TB06_AUTO_EXECUCOES (jobs por inquérito),
--          DOC_TB07_AUTO_DETECCOES (achados de uma execução).
-- Correspondente imperativo em db/migracoes/0041_entidades_automaticas.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

-- 05 · biblioteca global de padrões automáticos (como PDR_TB01, mas só para documentos
-- genéricos). TX_DEFINICAO é JSONB com parâmetros próprios:
--   {"padrao": "\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}", "grupo": 0, "validador": "cpf", "normalizacao": "digitos"}
--   grupo: qual grupo do regex é o valor (0 = match inteiro); validador: nenhum|cpf|cnpj|
--   telefone|email|placa|valor|cnj|conta; normalizacao: digitos|minusculo|placa|valor|cnj|oab|ipl
CREATE TABLE DOC_TB05_AUTO_PADROES (
    ID_AUTO_PADRAO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_PADRAO           VARCHAR(150) NOT NULL,
    TP_PADRAO           VARCHAR(20) NOT NULL DEFAULT 'regex',  -- regex (único tipo nesta versão)
    TP_ACHADO           VARCHAR(40) NOT NULL,  -- cpf|cnpj|telefone|email|valor|conta|agencia|processo|procedimento|oab|placa_veiculo|documento
    NO_CATEGORIA        VARCHAR(80),  -- ex.: identificação, contato, financeiro, judicial
    DS_PADRAO           VARCHAR(500),
    TX_DEFINICAO        JSONB NOT NULL,
    IN_ATIVO            BOOLEAN NOT NULL DEFAULT true,
    DT_CADASTRO         TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR  BIGINT,
    CONSTRAINT FK406_DOC_TB05_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ11_DOC_TB05_NOME UNIQUE (NO_PADRAO)
);

-- 06 · uma linha por execução da extração automática num inquérito — mesmo padrão de job
-- em background com polling de PDR_TB02/ENT_TB04
CREATE TABLE DOC_TB06_AUTO_EXECUCOES (
    ID_EXECUCAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_USUARIO    BIGINT,
    CD_STATUS     VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    TX_ERRO       TEXT,
    QT_DETECCOES  INTEGER,
    DT_INICIO     TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM        TIMESTAMP,
    CONSTRAINT FK407_DOC_TB06_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK408_DOC_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX421_DOC_TB06_INQUERITO ON DOC_TB06_AUTO_EXECUCOES (ID_INQUERITO);

-- Guard de execução única por inquérito (ver db/migracoes/0055_execucoes_unicas.sql).
CREATE UNIQUE INDEX UX115_DOC_TB06_ATIVA ON DOC_TB06_AUTO_EXECUCOES (ID_INQUERITO)
    WHERE CD_STATUS IN ('pendente', 'processando');

-- 07 · achados de uma execução. ID_DOCUMENTO aponta o documento que continha o valor
-- (TP_ORIGEM fixo "documento_generico", mesmo domínio das entidades); CO_CHAVE é o valor
-- normalizado (só dígitos / minúsculo / placa sem traço), que deduplica e casa com a IA.
CREATE TABLE DOC_TB07_AUTO_DETECCOES (
    ID_DETECCAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXECUCAO   BIGINT NOT NULL,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_AUTO_PADRAO BIGINT NOT NULL,
    ID_DOCUMENTO  BIGINT NOT NULL,
    TP_ACHADO     VARCHAR(40) NOT NULL,
    TX_VALOR      VARCHAR(300) NOT NULL,  -- o dado como aparece no texto
    CO_CHAVE      VARCHAR(300) NOT NULL,  -- o dado normalizado (deduplicação)
    TX_TRECHO     TEXT,  -- janela de ~240 caracteres em volta da primeira ocorrência
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

CREATE INDEX IX422_DOC_TB07_EXECUCAO ON DOC_TB07_AUTO_DETECCOES (ID_EXECUCAO);
CREATE INDEX IX423_DOC_TB07_INQUERITO ON DOC_TB07_AUTO_DETECCOES (ID_INQUERITO);
CREATE INDEX IX424_DOC_TB07_CHAVE ON DOC_TB07_AUTO_DETECCOES (ID_INQUERITO, TP_ACHADO, CO_CHAVE);

-- padrões de partida (edite/desative na aba "Entidades automatizadas"). Ordem de inserção
-- é a prioridade na extração: os específicos (procedimento, CNJ, CPF, CNPJ, telefone) vêm
-- antes dos genéricos (conta), e trechos já ocupados não são recontados.
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
     '{"padrao": "R\\$ ?\\d[\\d.,]*", "grupo": 0, "validador": "valor", "normalizacao": "valor"}');

RESET search_path;
