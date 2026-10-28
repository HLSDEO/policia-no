-- ===================================================================================================================
-- Conversas com a IA, processamento em lote e auditoria
--
-- Tabelas: IA_TB01_CONVERSAS, IA_TB02_MENSAGENS, IA_TB03_PROCESSAMENTOS, IA_TB04_ARQUIVOS_PROMPT,
--          IA_TB04_AUDITORIA, IA_TB05_RELATORIOS_DOCX
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · uma conversa (sessão de chat) por inquérito
CREATE TABLE IA_TB01_CONVERSAS (
    ID_CONVERSA_IA            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO              BIGINT NOT NULL,
    NO_TITULO                 VARCHAR(3000),  -- gerado a partir da 1ª mensagem, se não informado
    DT_CRIACAO                TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR        BIGINT,
    TP_ESCOPO                 VARCHAR(20),  -- NULL = inquérito inteiro (comportamento de sempre) | 'rif' | 'simba' | 'ipl'
    ID_ESCOPO                 BIGINT,  -- ID_RIF / ID_EXTRATO / ID_IPL conforme TP_ESCOPO
    -- Chat por inquérito
    TX_RESUMO_HISTORICO       TEXT,
    ID_MENSAGEM_RESUMIDA_ATE  BIGINT,
    DT_RESUMO_HISTORICO       TIMESTAMP,
    CONSTRAINT FK57_IA_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK68_IA_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK05_IA_TB01_TP_ESCOPO CHECK (
        TP_ESCOPO IS NULL OR TP_ESCOPO IN (
        'rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento',
        'wpp', 'sms', 'msg',
        'conversa_wpp', 'conversa_sms', 'conversa_msg'
        )
        )
);

CREATE INDEX IX23_IA_TB01_ID_INQUERITO ON IA_TB01_CONVERSAS (ID_INQUERITO);

-- 02 · mensagens da conversa (usuário e assistente)
CREATE TABLE IA_TB02_MENSAGENS (
    ID_MENSAGEM_IA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA  BIGINT NOT NULL,
    TP_PAPEL        VARCHAR(3000) NOT NULL,  -- user | assistant
    TX_CONTEUDO     TEXT NOT NULL,
    NO_MODELO       VARCHAR(3000),  -- modelo que respondeu (só em mensagens do assistente)
    -- trilha do turno exibida recolhida na bolha da resposta (raciocínio do modelo, quando o
    -- provedor devolve, + ferramentas chamadas por rodada); nulo quando não há o que mostrar
    TX_RACIOCINIO   JSONB,
    -- jobs pesados que o turno disparou (OCR via processar_texto_em_imagens): [{id_job, tp_job}]; o
    -- front desenha o cartão de progresso na bolha a partir daqui (ver migracoes/0045_ia_mensagem_jobs)
    CO_JOBS         JSONB,
    DT_MENSAGEM     TIMESTAMP NOT NULL DEFAULT now(),
    -- nulo nas mensagens do assistente (não foi "enviada" por um usuário humano)
    ID_USUARIO      BIGINT,
    CONSTRAINT FK58_IA_TB02_IA_TB01 FOREIGN KEY (ID_CONVERSA_IA)
        REFERENCES IA_TB01_CONVERSAS (ID_CONVERSA_IA),
    CONSTRAINT FK69_IA_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX24_IA_TB02_ID_CONVERSA_IA ON IA_TB02_MENSAGENS (ID_CONVERSA_IA);

-- 03 · um processamento em lotes disparado por uma mensagem do usuário
CREATE TABLE IA_TB03_PROCESSAMENTOS (
    ID_PROCESSAMENTO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA        BIGINT NOT NULL,
    ID_MENSAGEM_USUARIO   BIGINT NOT NULL,  -- a pergunta que disparou o processamento
    CD_STATUS             VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    QT_LOTES_TOTAL        INTEGER NOT NULL,
    QT_LOTES_CONCLUIDOS   INTEGER NOT NULL DEFAULT 0,
    TX_ERRO               TEXT,
    ID_MENSAGEM_RESPOSTA  BIGINT,  -- mensagem do assistente gerada ao concluir
    DT_INICIO             TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM                TIMESTAMP,
    FL_CANCELAR           BOOLEAN NOT NULL DEFAULT false,
    TX_ETAPA_ATUAL        VARCHAR(200),
    TX_PROGRESSO          JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT FK110_IA_TB03_IA_TB01 FOREIGN KEY (ID_CONVERSA_IA)
        REFERENCES IA_TB01_CONVERSAS (ID_CONVERSA_IA),
    CONSTRAINT FK111_IA_TB03_IA_TB02 FOREIGN KEY (ID_MENSAGEM_USUARIO)
        REFERENCES IA_TB02_MENSAGENS (ID_MENSAGEM_IA),
    CONSTRAINT FK112_IA_TB03_IA_TB02_RESP FOREIGN KEY (ID_MENSAGEM_RESPOSTA)
        REFERENCES IA_TB02_MENSAGENS (ID_MENSAGEM_IA)
);

CREATE INDEX IX90_IA_TB03_ID_CONVERSA ON IA_TB03_PROCESSAMENTOS (ID_CONVERSA_IA);
-- Trava por conversa: no máximo um processamento ativo (pendente/processando) por conversa. O
-- segundo envio concorrente (outra aba/usuário) viola este índice e é rejeitado com 409, em vez de
-- criar respostas concorrentes que embaralham o histórico. Ver funcoes/ia_utils.py.
CREATE UNIQUE INDEX UX41_IA_TB03_CONVERSA_ATIVA ON IA_TB03_PROCESSAMENTOS (ID_CONVERSA_IA)
    WHERE CD_STATUS IN ('pendente', 'processando');

CREATE TABLE IA_TB04_ARQUIVOS_PROMPT (
    ID_ARQUIVO_PROMPT  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA     BIGINT NOT NULL,
    ID_INQUERITO       BIGINT NOT NULL,
    ID_USUARIO         BIGINT,
    NO_ARQUIVO         VARCHAR(500) NOT NULL,
    CAMINHO_ARQUIVO    TEXT NOT NULL,
    DT_CRIACAO         TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK65_IA_TB04_IA_TB01 FOREIGN KEY (ID_CONVERSA_IA)
        REFERENCES IA_TB01_CONVERSAS (ID_CONVERSA_IA),
    CONSTRAINT FK66_IA_TB04_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO)
);

CREATE INDEX IX25_IA_TB04_ID_CONVERSA_IA ON IA_TB04_ARQUIVOS_PROMPT (ID_CONVERSA_IA);
CREATE INDEX IX26_IA_TB04_ID_INQUERITO ON IA_TB04_ARQUIVOS_PROMPT (ID_INQUERITO);

-- 04 · um registro por turno de IA (um turno pode ter várias chamadas à API:
-- rodadas de function calling e/ou lotes do modo "conteúdo completo")
CREATE TABLE IA_TB04_AUDITORIA (
    ID_AUDITORIA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_ORIGEM            VARCHAR(30) NOT NULL,  -- chat_inquerito | busca_automatica | busca_documentos | documento_analise | rif_analise_ia
    ID_INQUERITO         BIGINT,  -- preenchido nas origens por inquérito
    ID_CONVERSA_IA       BIGINT,  -- conversa (ia_tb01), conforme a origem
    -- resposta do assistente deste turno (IA_TB02, conforme a origem): é o que
    -- deixa a bolha da resposta mostrar tokens/tempo/custo daquele turno. Sem FK pelo mesmo
    -- motivo de ID_CONVERSA_IA e para a auditoria sobreviver à conversa apagada.
    ID_MENSAGEM          BIGINT,
    ID_USUARIO           BIGINT,  -- quem disparou o turno
    NO_PROVEDOR          VARCHAR(3000),
    NO_MODELO            VARCHAR(3000),
    TX_PROMPT            TEXT,  -- pergunta/instrução enviada pelo usuário
    TX_CONTEXTO          TEXT,  -- prompt de sistema/contexto (truncado se muito grande)
    QT_CHARS_CONTEXTO    INTEGER,  -- tamanho real do contexto antes do truncamento
    TX_FERRAMENTAS       JSONB,  -- [{"ferramenta": nome, "argumentos": {...}}, ...]
    TX_DOCUMENTOS        JSONB,  -- documentos consultados via ferramentas de leitura
    QT_CHAMADAS_API      INTEGER NOT NULL DEFAULT 0,
    QT_FERRAMENTAS       INTEGER NOT NULL DEFAULT 0,
    QT_TOKENS_ENTRADA    BIGINT,
    QT_TOKENS_SAIDA      BIGINT,
    IN_TOKENS_ESTIMADOS  BOOLEAN NOT NULL DEFAULT false,  -- true quando o provedor não informou uso e os tokens foram estimados por tamanho de texto
    VL_CUSTO_USD         NUMERIC(12, 6),  -- custo estimado em dólares (NULL = preço do modelo desconhecido)
    NU_TEMPO_MS          BIGINT,  -- duração total do turno
    CD_STATUS            VARCHAR(10) NOT NULL DEFAULT 'ok',  -- ok | erro
    TX_ERRO              TEXT,
    DT_CHAMADA           TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK135_IA_TB04_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK137_IA_TB04_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX68_IA_TB04_DT_CHAMADA ON IA_TB04_AUDITORIA (DT_CHAMADA DESC);
CREATE INDEX IX69_IA_TB04_ID_INQUERITO ON IA_TB04_AUDITORIA (ID_INQUERITO);
CREATE INDEX IX70_IA_TB04_ID_USUARIO ON IA_TB04_AUDITORIA (ID_USUARIO);
CREATE INDEX IX297_IA_TB04_ID_MENSAGEM ON IA_TB04_AUDITORIA (ID_MENSAGEM);

CREATE TABLE IA_TB05_RELATORIOS_DOCX (
    ID_RELATORIO_IA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA   BIGINT NOT NULL,  -- IA_TB01_CONVERSAS
    ID_INQUERITO     BIGINT,
    ID_USUARIO       BIGINT,
    NO_ARQUIVO       VARCHAR(500) NOT NULL,
    CAMINHO_ARQUIVO  TEXT NOT NULL,
    DT_CRIACAO       TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK243_IA_TB05_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK245_IA_TB05_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX208_IA_TB05_ID_CONVERSA_IA ON IA_TB05_RELATORIOS_DOCX (ID_CONVERSA_IA);
CREATE INDEX IX209_IA_TB05_DT_CRIACAO ON IA_TB05_RELATORIOS_DOCX (DT_CRIACAO);

-- 05 · avaliação das respostas da IA (👍/👎 por mensagem do assistente) — um voto por
-- usuário e mensagem. Correspondente imperativo em db/migracoes/0019_ia_feedback.sql.
CREATE TABLE IA_TB06_FEEDBACK (
    ID_FEEDBACK   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_MENSAGEM_IA BIGINT NOT NULL,
    ID_USUARIO    BIGINT NOT NULL,
    TP_FEEDBACK   VARCHAR(10) NOT NULL,
    DT_CRIACAO    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK298_IA_TB06_IA_TB02 FOREIGN KEY (ID_MENSAGEM_IA)
        REFERENCES IA_TB02_MENSAGENS (ID_MENSAGEM_IA) ON DELETE CASCADE,
    CONSTRAINT FK299_IA_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE,
    CONSTRAINT CK17_IA_TB06_TP_FEEDBACK CHECK (TP_FEEDBACK IN ('positivo', 'negativo')),
    CONSTRAINT UX108_IA_TB06_MSG_USUARIO UNIQUE (ID_MENSAGEM_IA, ID_USUARIO)
);

CREATE INDEX IX298_IA_TB06_ID_MENSAGEM ON IA_TB06_FEEDBACK (ID_MENSAGEM_IA);

-- 07 · planilhas .xlsx consolidadas pedidas no chat e geradas pelo job de consolidação
-- (funcoes/ia_planilha_utils.py). Mesmo desenho da IA_TB05: o binário fica em
-- data/planilhas_ia, a linha só aponta o caminho para a rota de download. Correspondente
-- imperativo em db/migracoes/0053_ia_planilhas.sql.
CREATE TABLE IA_TB07_PLANILHAS (
    ID_PLANILHA_IA   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA   BIGINT,  -- IA_TB01_CONVERSAS, sem FK (sobrevive à conversa apagada)
    ID_INQUERITO     BIGINT,
    ID_USUARIO       BIGINT,
    NO_ARQUIVO       VARCHAR(500) NOT NULL,
    CAMINHO_ARQUIVO  TEXT NOT NULL,
    QT_LINHAS        INTEGER,
    DT_CRIACAO       TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK425_IA_TB07_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK426_IA_TB07_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX433_IA_TB07_ID_CONVERSA_IA ON IA_TB07_PLANILHAS (ID_CONVERSA_IA);
CREATE INDEX IX434_IA_TB07_DT_CRIACAO ON IA_TB07_PLANILHAS (DT_CRIACAO);

RESET search_path;
