-- Tabela de jobs de segundo plano com escopo por inquérito. Correspondente declarativo em
-- db/init/41_jobs.sql, onde está a explicação completa do porquê.
--
-- Resumo: OCR, classificação de imagem e comparação facial guardavam progresso e cancelamento em
-- dicionário de módulo. Isso é estado de processo, então valia para o servidor inteiro — o segundo
-- analista levava 409 por causa do inquérito de outra delegacia, o cancelamento de um parava o job
-- do outro, e o progresso da comparação facial entregava os rostos encontrados no inquérito alheio.

CREATE TABLE IF NOT EXISTS INVESTIGACAO.JOB_TB01_JOBS (
    ID_JOB               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_JOB               VARCHAR(30) NOT NULL,
    ID_INQUERITO         BIGINT,
    ID_EXTRACAO          BIGINT,
    NO_ESCOPO            VARCHAR(100),
    ID_USUARIO           BIGINT NOT NULL,
    CD_STATUS            VARCHAR(20) NOT NULL DEFAULT 'pendente',
    QT_TOTAL             INTEGER NOT NULL DEFAULT 0,
    QT_PROCESSADOS       INTEGER NOT NULL DEFAULT 0,
    QT_ERROS             INTEGER NOT NULL DEFAULT 0,
    FL_CANCELAR          BOOLEAN NOT NULL DEFAULT false,
    FL_PESADO            BOOLEAN NOT NULL DEFAULT true,
    TX_ERRO              TEXT,
    TX_PARAMETROS        JSONB,
    TX_METRICAS          JSONB NOT NULL DEFAULT '{}'::jsonb,
    TX_RESULTADO         JSONB,
    NO_WORKER            VARCHAR(100),
    DT_INICIO            TIMESTAMP NOT NULL DEFAULT now(),
    DT_ULTIMA_ATIVIDADE  TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM               TIMESTAMP,
    CONSTRAINT FK260_JOB_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INVESTIGACAO.INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK261_JOB_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INVESTIGACAO.INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT FK262_JOB_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK13_JOB_TB01_STATUS CHECK (CD_STATUS IN
        ('pendente', 'aguardando_vaga', 'processando', 'concluido', 'cancelado', 'erro')),
    CONSTRAINT CK14_JOB_TB01_ESCOPO CHECK (ID_INQUERITO IS NOT NULL OR NO_ESCOPO IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS UX100_JOB_TB01_ATIVO ON INVESTIGACAO.JOB_TB01_JOBS
    (TP_JOB, COALESCE(ID_INQUERITO, 0), COALESCE(NO_ESCOPO, ''))
    WHERE CD_STATUS IN ('pendente', 'aguardando_vaga', 'processando');

CREATE INDEX IF NOT EXISTS IX252_JOB_TB01_INQUERITO ON INVESTIGACAO.JOB_TB01_JOBS
    (ID_INQUERITO, TP_JOB, ID_JOB DESC);

CREATE INDEX IF NOT EXISTS IX253_JOB_TB01_ATIVOS ON INVESTIGACAO.JOB_TB01_JOBS
    (CD_STATUS, DT_ULTIMA_ATIVIDADE)
    WHERE CD_STATUS IN ('pendente', 'aguardando_vaga', 'processando');

CREATE INDEX IF NOT EXISTS IX254_JOB_TB01_USUARIO ON INVESTIGACAO.JOB_TB01_JOBS
    (ID_USUARIO, ID_JOB DESC);
