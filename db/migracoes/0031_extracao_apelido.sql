-- ===================================================================================================================
-- Apelido da extração (.ufdr) — nome curto dado pelo investigador no card
--
-- Tabelas: INQ_TB02_EXTRACOES
--
-- O nome do arquivo .ufdr e o modelo do aparelho vêm da importação e não dizem de quem é o
-- aparelho ("RELATORIO.ufdr", "SM-A032M"). O apelido é um rótulo livre por extração
-- (ex.: "Celular do João"), editado no menu "Mais ações" do bloco em investigacao.html,
-- só para identificação no card — não altera arquivo, escopo de IA nem achados.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB02_EXTRACOES
    ADD COLUMN IF NOT EXISTS NO_APELIDO VARCHAR(200);

RESET search_path;
