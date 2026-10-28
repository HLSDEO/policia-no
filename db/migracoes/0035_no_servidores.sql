-- Nós descentralizados de extração (ver docs/no_descentralizado.md e db/init/46_nos.sql).
-- Espelho imperativo de: 46_nos.sql (tabela nova) + colunas ID_SERVIDOR em 09_extracoes.sql e
-- 41_jobs.sql. Idempotente como manda a convenção do README deste diretório.

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS NO_TB01_SERVIDORES (
    ID_SERVIDOR           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_SERVIDOR           VARCHAR(100) NOT NULL UNIQUE,
    ID_DELEGACIA          BIGINT NOT NULL,
    TX_URL_BASE           VARCHAR(500) NOT NULL,
    TX_TOKEN              VARCHAR(200) NOT NULL,
    TX_VERSAO             VARCHAR(50),
    DT_ULTIMO_HEARTBEAT   TIMESTAMP,
    DT_CRIACAO            TIMESTAMP NOT NULL DEFAULT now(),
    IN_ATIVA              BOOLEAN NOT NULL DEFAULT true,
    CONSTRAINT FK402_NO_TB01_DELEGACIAS FOREIGN KEY (ID_DELEGACIA)
        REFERENCES SEGURANCA.DELEGACIAS (ID_DELEGACIA)
);

-- UX109 (uma delegacia, um nó) NÃO é criado aqui de propósito: o vínculo mudou de tabela —
-- NO_TB02_SERVIDOR_DELEGACIAS + UX110 (ver 0038_no_multi_delegacias.sql, que também desfaz o
-- ID_DELEGACIA antigo em bancos onde ele chegou a existir).

-- Snapshot do nó que serve a extração (sem FK — ver o comentário em 09_extracoes.sql)
ALTER TABLE INQ_TB02_EXTRACOES ADD COLUMN IF NOT EXISTS ID_SERVIDOR BIGINT;

-- Índice da pergunta "de quem é este .ufdr?" (no_utils.servidor_por_arquivo) — espelho do
-- IX420 parcial em 09_extracoes.sql
CREATE INDEX IF NOT EXISTS IX420_INQ_TB02_ID_SERVIDOR ON INQ_TB02_EXTRACOES (ID_SERVIDOR)
    WHERE ID_SERVIDOR IS NOT NULL;

-- Nó destino do job (sem FK — ver o comentário em 41_jobs.sql)
ALTER TABLE JOB_TB01_JOBS ADD COLUMN IF NOT EXISTS ID_SERVIDOR BIGINT;

RESET search_path;
