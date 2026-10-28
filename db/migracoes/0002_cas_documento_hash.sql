-- Hash SHA-256 do arquivo de origem de um documento de caso, para detectar a reimportação do
-- MESMO arquivo (o mesmo PDF selecionado duas vezes, ou re-adicionado ao caso depois) sem
-- gastar uma segunda extração de texto + rodada de IA. Correspondente declarativo em
-- db/init/23_casos.sql (coluna CO_HASH no CREATE TABLE de CAS_TB02_DOCUMENTOS + índice IX244).
ALTER TABLE INVESTIGACAO.CAS_TB02_DOCUMENTOS ADD COLUMN CO_HASH CHAR(64);

CREATE INDEX IX244_CAS_TB02_HASH ON INVESTIGACAO.CAS_TB02_DOCUMENTOS (ID_CASO, CO_HASH);
