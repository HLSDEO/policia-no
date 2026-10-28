-- Aviso da extração: concluída com sucesso, mas com algo por fazer — hoje, o modelo de
-- transcrição (faster-whisper) ou de embeddings não baixado no servidor. Antes isso derrubava a
-- extração inteira para 'erro'. Correspondente declarativo em db/init/09_extracoes.sql.
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN IF NOT EXISTS TX_AVISO TEXT;
