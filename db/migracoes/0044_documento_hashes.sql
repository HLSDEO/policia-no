-- ===================================================================================================================
-- Hash do arquivo original de um documento anexado — cadeia de custódia
--
-- Tabelas: INQ_TB04_DOCUMENTOS
--
-- Extração (.ufdr), SIMBA e RIF já guardavam os digestos do arquivo de origem; o documento
-- anexado (o PDF do próprio inquérito, um parecer…) não guardava nenhum. Passa a calcular
-- MD5/SHA-1/SHA-256 no momento da anexação (funcoes/documentos_utils.anexar_documento_inquerito).
-- Sem backfill: documento importado antes deste delta fica com os digestos nulos, e o card diz
-- isso em vez de inventar. Documento que mora num NÓ (ID_SERVIDOR) também fica nulo — o
-- central nunca tem o arquivo em disco para hashear.
-- Correspondente declarativo em db/init/08_inqueritos.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB04_DOCUMENTOS
    ADD COLUMN IF NOT EXISTS CO_HASH_MD5    CHAR(32),
    ADD COLUMN IF NOT EXISTS CO_HASH_SHA1   CHAR(40),
    ADD COLUMN IF NOT EXISTS CO_HASH_SHA256 CHAR(64);

RESET search_path;
