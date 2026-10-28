-- Etapa em andamento de uma execução de extração/busca de caso ("Extraindo texto (34/50):
-- nota.pdf", "Analisando com a IA…", "Cruzando identidades…"), para o polling da tela mostrar
-- em que pé está uma busca com dezenas de documentos, em vez de só um "Processando…" opaco.
-- Correspondente declarativo em db/init/23_casos.sql (coluna TX_ETAPA_ATUAL no CREATE TABLE de
-- CAS_TB05_BUSCAS — o mesmo nome/tamanho de CAS_TB08_PROCESSAMENTOS_IA.TX_ETAPA_ATUAL).
ALTER TABLE INVESTIGACAO.CAS_TB05_BUSCAS ADD COLUMN TX_ETAPA_ATUAL VARCHAR(200);
