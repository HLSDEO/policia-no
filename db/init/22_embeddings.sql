-- ===================================================================================================================
-- Busca semantica: vetores e controle de indexacao
--
-- Tabelas: EMB_TB01_EMBEDDINGS, EMB_TB02_INDEXACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- busca semântica com pgvector (imagem pgvector/pgvector do Postgres): a similaridade é
-- calculada no banco (vl_vetor <=> q) e o índice HNSW é criado pela API com a dimensão do
-- EMBED_MODEL configurado (funcoes/embed_utils.py, _garantir_dimensao)
-- SCHEMA public: sem ele a extensão nasceria em INVESTIGACAO (primeiro do search_path) e a
-- conexão da API, que roda com search_path padrão, não resolveria `::vector`/vector_dims()
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- vetor de uma mensagem, escopado por inquérito (cadeia de custódia)
CREATE TABLE EMB_TB01_EMBEDDINGS (
    ID_EMBEDDING  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    TP_FONTE      VARCHAR(3) NOT NULL,  -- wpp | sms
    ID_MENSAGEM   BIGINT NOT NULL,
    TX_TRECHO     TEXT NOT NULL,  -- texto embeddado (mensagem ou transcrição)
    VL_VETOR      vector NOT NULL,  -- sem dimensão fixa: depende do EMBED_MODEL (a API fixa na indexação)
    NR_TRECHO     BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT FK113_EMB_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO)
);

CREATE INDEX IX91_EMB_TB01_INQUERITO ON EMB_TB01_EMBEDDINGS (ID_INQUERITO);
CREATE UNIQUE INDEX IF NOT EXISTS UX43_EMB_TB01_MSG
    ON EMB_TB01_EMBEDDINGS (ID_INQUERITO, TP_FONTE, ID_MENSAGEM, NR_TRECHO);

-- status da indexação semântica por inquérito (job em background, polling no front)
CREATE TABLE EMB_TB02_INDEXACOES (
    ID_INDEXACAO  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    CD_STATUS     VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    QT_TOTAL      INTEGER NOT NULL DEFAULT 0,
    QT_FEITOS     INTEGER NOT NULL DEFAULT 0,
    TX_ERRO       TEXT,
    DT_INICIO     TIMESTAMP NOT NULL DEFAULT now(),
    DT_HEARTBEAT  TIMESTAMP NOT NULL DEFAULT now(),  -- atualizado a cada lote gravado; trava por inatividade
    DT_FIM        TIMESTAMP,
    FL_CANCELAR   BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT FK114_EMB_TB02_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO)
);

CREATE INDEX IX92_EMB_TB02_INQUERITO ON EMB_TB02_INDEXACOES (ID_INQUERITO);
CREATE UNIQUE INDEX UX45_EMB_TB02_ATIVA ON EMB_TB02_INDEXACOES (ID_INQUERITO)
    WHERE CD_STATUS IN ('pendente', 'processando');

RESET search_path;
