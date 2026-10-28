-- ===================================================================================================================
-- Jobs disparados pela IA dentro de um turno de chat — amarra a resposta ao processamento em background
--
-- Tabelas: IA_TB02_MENSAGENS (coluna nova CO_JOBS)
--
-- Quando a IA chama processar_texto_em_imagens, o OCR roda em background (JOB_TB01_JOBS) e o turno
-- encerra com uma única mensagem-assistente. Sem esta coluna, o id_job só vivia no resultado da
-- ferramenta (que a trilha TX_RACIOCINIO não guarda) e o investigador ficava sem como acompanhar o
-- resultado depois do turno. CO_JOBS guarda [{id_job, tp_job}, ...] produzidos naquele turno: o front
-- desenha um cartão de progresso (polling em /api/investigacao/jobs/{id_job}) embaixo da bolha, que
-- sobrevive ao F5 e reaparece ao reabrir a conversa. Aditiva, sem backfill: mensagens antigas ficam
-- com NULL e simplesmente não ganham cartão.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE IA_TB02_MENSAGENS ADD COLUMN IF NOT EXISTS CO_JOBS JSONB;

RESET search_path;
