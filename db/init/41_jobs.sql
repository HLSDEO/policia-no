-- ===================================================================================================================
-- Jobs de segundo plano com escopo por inquérito
--
-- Tabelas: JOB_TB01_JOBS
--
-- Substitui os dicionários de módulo (_PROGRESS/_CANCELAR) de ocr_utils, classificacao_imagem_utils
-- e comparar_rosto_utils. Aqueles eram estado de PROCESSO, ou seja, valiam para o servidor inteiro:
-- o segundo analista — de outra delegacia, em outro inquérito — levava 409 "Já existe um OCR em
-- andamento", o cancelamento de um parava o job do outro, e o GET de progresso da comparação facial
-- devolvia os rostos encontrados no inquérito alheio. Aqui o escopo é (TP_JOB, ID_INQUERITO), o dono
-- é ID_USUARIO, e o "só um por vez" é um índice único parcial — o mesmo padrão de
-- UX41_IA_TB03_CONVERSA_ATIVA em db/init/24_ia.sql.
--
-- ID_INQUERITO e NO_ESCOPO são nulos em casos diferentes, de propósito: job global (importação de
-- base de referência, download de modelo no Ollama) entra aqui sem mudança de schema, usando
-- NO_ESCOPO como chave. Ver api/funcoes/jobs_utils.py.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE JOB_TB01_JOBS (
    ID_JOB               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_JOB               VARCHAR(30) NOT NULL,   -- ocr_imagem | classificacao_imagem | comparacao_facial | import_referencia | ollama_pull
    ID_INQUERITO         BIGINT,                 -- NULL = job global (não ligado a inquérito)
    ID_EXTRACAO          BIGINT,                 -- escopo opcional dentro do inquérito
    NO_ESCOPO            VARCHAR(100),           -- chave livre do job global (grupo de import, nome do modelo)
    ID_USUARIO           BIGINT NOT NULL,        -- dono: só ele, ou um gestor do inquérito, cancela
    CD_STATUS            VARCHAR(20) NOT NULL DEFAULT 'pendente',
        -- pendente | aguardando_vaga | processando | concluido | cancelado | erro
    QT_TOTAL             INTEGER NOT NULL DEFAULT 0,
    QT_PROCESSADOS       INTEGER NOT NULL DEFAULT 0,
    QT_ERROS             INTEGER NOT NULL DEFAULT 0,
    FL_CANCELAR          BOOLEAN NOT NULL DEFAULT false,
    FL_PESADO            BOOLEAN NOT NULL DEFAULT true,   -- ocupa vaga do limite global de CPU (funcoes/jobs_pesados.py)
    TX_ERRO              TEXT,
    TX_PARAMETROS        JSONB,   -- o que a rota recebeu (lote, max_imagens, batch_size, limite)
    TX_METRICAS          JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- contadores específicos do tipo: {"com_texto": N} no OCR, {"reaproveitados": N} na facial
    TX_RESULTADO         JSONB,   -- comparação facial: a lista de rostos parecidos (era _PROGRESS["resultados"])
    NO_WORKER            VARCHAR(100),   -- host:pid do processo que está tocando o job (diagnóstico)
    -- Nó descentralizado destino do job (ver 46_nos.sql). NULL = roda no central, como sempre.
    -- Preenchido no criar() quando a delegacia do inquérito tem nó ativo — a fila do job passa a
    -- ser reivindicada por ele (claim), e o batimento/reaper existentes valem para o nó também.
    -- SEM foreign key de propósito (mesmo motivo de ID_SERVIDOR em 09_extracoes.sql: o histórico
    -- de jobs não pode impedir a exclusão de um nó).
    ID_SERVIDOR          BIGINT,
    DT_INICIO            TIMESTAMP NOT NULL DEFAULT now(),
    DT_ULTIMA_ATIVIDADE  TIMESTAMP NOT NULL DEFAULT now(),  -- batimento; liberar_jobs_travados usa
    DT_FIM               TIMESTAMP,
    CONSTRAINT FK260_JOB_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK261_JOB_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT FK262_JOB_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK13_JOB_TB01_STATUS CHECK (CD_STATUS IN
        ('pendente', 'aguardando_vaga', 'processando', 'concluido', 'cancelado', 'erro')),
    CONSTRAINT CK14_JOB_TB01_ESCOPO CHECK (ID_INQUERITO IS NOT NULL OR NO_ESCOPO IS NOT NULL)
);

-- O guard. Um job ativo por (tipo, inquérito/escopo): o segundo POST do MESMO inquérito viola este
-- índice e vira 409; o de OUTRO inquérito passa — que é exatamente o que faltava. Ser um índice, e
-- não um SELECT antes do INSERT, é o que fecha a janela de corrida entre dois cliques simultâneos.
-- COALESCE porque ID_INQUERITO e NO_ESCOPO são nulos em casos diferentes, e NULL não colide com
-- NULL num índice único comum — dois jobs globais do mesmo grupo passariam.
CREATE UNIQUE INDEX UX100_JOB_TB01_ATIVO ON JOB_TB01_JOBS
    (TP_JOB, COALESCE(ID_INQUERITO, 0), COALESCE(NO_ESCOPO, ''))
    WHERE CD_STATUS IN ('pendente', 'aguardando_vaga', 'processando');

-- polling do front: o último job deste tipo neste inquérito
CREATE INDEX IX252_JOB_TB01_INQUERITO ON JOB_TB01_JOBS (ID_INQUERITO, TP_JOB, ID_JOB DESC);

-- contagem de vagas ocupadas (jobs_pesados) e varredura do reaper; parcial, então são poucas linhas
CREATE INDEX IX253_JOB_TB01_ATIVOS ON JOB_TB01_JOBS (CD_STATUS, DT_ULTIMA_ATIVIDADE)
    WHERE CD_STATUS IN ('pendente', 'aguardando_vaga', 'processando');

-- "meus jobs" / auditoria por analista
CREATE INDEX IX254_JOB_TB01_USUARIO ON JOB_TB01_JOBS (ID_USUARIO, ID_JOB DESC);

-- claim de jobs pelo NÓ (funcoes/jobs_no.py, docs/no_descentralizado.md "jobs no nó"):
-- "quais jobs pendentes são deste servidor". Parcial: a tabela inteira é histórico, a fila
-- viva dos pendentes com nó é o pedaço quente (ver migracoes/0058).
CREATE INDEX IX435_JOB_TB01_NO_PENDENTE ON JOB_TB01_JOBS (ID_SERVIDOR, ID_JOB)
    WHERE CD_STATUS = 'pendente' AND ID_SERVIDOR IS NOT NULL;

RESET search_path;
