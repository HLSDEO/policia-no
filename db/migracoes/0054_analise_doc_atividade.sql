-- ===================================================================================================================
-- Batimento da análise de documento (DT_ULTIMA_ATIVIDADE_ANALISE em INQ_TB04_DOCUMENTOS)
--
-- Tabela: INQ_TB04_DOCUMENTOS (coluna nova)
--
-- O watchdog de análises presas (liberar_analises_documentos_travadas, em
-- api/funcoes/documentos_utils.py) media a idade pelo INÍCIO da análise — mas como todas as
-- análises de IA do sistema serializam num candado advisory global, uma fila legítima de ZIP
-- grande passa fácil de 6 h e o trabalho vivo era marcado 'erro' (ver C3). Com o batimento, o
-- predicado passa a usar a última atividade: só morre quem parou de verdade. Aditiva.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB04_DOCUMENTOS
    ADD COLUMN IF NOT EXISTS DT_ULTIMA_ATIVIDADE_ANALISE TIMESTAMP;

RESET search_path;
