-- ===================================================================================================================
-- Documento avulso que mora no NÓ (docs/no_descentralizado.md — "só no nó")
--
-- Delegacia com nó: o documento importado pela interface fica APENAS na estação (o upload é
-- repassado em streaming e o organize roda lá). O central guarda o texto extraído no banco e o
-- arquivo em si continua no disco do nó — ID_SERVIDOR diz de onde lê-lo quando a aba "Original",
-- a reextração ou os blocos IPL/IRPF/IPEI/telefonia precisarem reler o PDF.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB04_DOCUMENTOS
    ADD COLUMN IF NOT EXISTS ID_SERVIDOR BIGINT;

COMMENT ON COLUMN INQ_TB04_DOCUMENTOS.ID_SERVIDOR IS
    'Nó onde o arquivo mora (NULL = disco do central); leitura sob demanda por GET /no/arquivo';

RESET search_path;
