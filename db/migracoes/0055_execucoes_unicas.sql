-- ===================================================================================================================
-- Guarda de execução única de padrões/entidades automáticas por inquérito
--
-- Tabelas: DOC_TB06_AUTO_EXECUCOES, PDR_TB02_EXECUCOES (índices novos)
--
-- Cada POST de detecção inseria uma linha e subia uma thread sem limite nem guarda por inquérito:
-- N cliques = N threads varrendo os mesmos documentos, e se o processo morria a linha ficava em
-- 'processando' para sempre (ver C8). Mesmo desenho do guard de jobs (UX100_JOB_TB01_ATIVO em
-- db/init/41_jobs.sql): índice único parcial sobre as linhas ativas — o segundo POST viola o
-- índice e vira 409 em vez de duplicar o trabalho. Aditivo.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE UNIQUE INDEX IF NOT EXISTS UX115_DOC_TB06_ATIVA ON DOC_TB06_AUTO_EXECUCOES (ID_INQUERITO)
    WHERE CD_STATUS IN ('pendente', 'processando');

CREATE UNIQUE INDEX IF NOT EXISTS UX116_PDR_TB02_ATIVA ON PDR_TB02_EXECUCOES (ID_INQUERITO)
    WHERE CD_STATUS IN ('pendente', 'processando');

RESET search_path;
