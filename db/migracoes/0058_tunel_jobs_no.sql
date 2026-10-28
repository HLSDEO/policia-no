-- Túnel NAT, jobs no nó e rollout de update (docs/no_descentralizado.md).
-- Espelho imperativo de: 46_nos.sql (DT_TUNEL_ATIVO) + 41_jobs.sql (IX435 do claim).
-- Idempotente como manda a convenção do README deste diretório.

SET search_path TO INVESTIGACAO, PUBLIC;

-- Batida do CANO do túnel NAT: o serviço `tunel` grava now() no connect e a cada ping de 30 s do
-- nó, NULL no disconnect. TIMESTAMP (não booleano) de propósito: se o serviço morrer sem se
-- despedir, a coluna congela e deixa de valer sozinha (no_utils.SEGUNDOS_TUNEL_FRESCO).
ALTER TABLE NO_TB01_SERVIDORES ADD COLUMN IF NOT EXISTS DT_TUNEL_ATIVO TIMESTAMP;

-- Índice do claim de jobs pelo nó (funcoes/jobs_no.py): "quais jobs pendentes são MEUS".
-- Parcial nos pendentes com nó: a tabela inteira é histórico; a fila viva é o pedaço quente.
CREATE INDEX IF NOT EXISTS IX435_JOB_TB01_NO_PENDENTE ON JOB_TB01_JOBS (ID_SERVIDOR, ID_JOB)
    WHERE CD_STATUS = 'pendente' AND ID_SERVIDOR IS NOT NULL;

RESET search_path;
