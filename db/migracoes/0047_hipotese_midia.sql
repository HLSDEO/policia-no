-- ===================================================================================================================
-- Hipótese associada a uma mídia importada (par opcional TP_ESCOPO/ID_ESCOPO em HIP_TB01)
--
-- A hipótese passa a poder nascer presa a uma mídia do inquérito (extração UFDR, RIF, SIMBA,
-- IPL, IRPF, IPEI, telefonia ou documento). É por esse vínculo — ou por uma evidência que
-- aponte àquela mídia — que a hipótese entra no chat de IA específico dela e aparece na tela
-- "Hipótese da mídia" (web/hipoteses.html filtrada por escopo). Correspondente declarativo em
-- db/init/48_hipoteses.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE HIP_TB01_HIPOTESES
    ADD COLUMN IF NOT EXISTS TP_ESCOPO VARCHAR(20),
    ADD COLUMN IF NOT EXISTS ID_ESCOPO BIGINT;

ALTER TABLE HIP_TB01_HIPOTESES
    DROP CONSTRAINT IF EXISTS CK29_HIP_TB01_TP_ESCOPO;
ALTER TABLE HIP_TB01_HIPOTESES
    DROP CONSTRAINT IF EXISTS CK30_HIP_TB01_ESCOPO_PAR;
ALTER TABLE HIP_TB01_HIPOTESES
    ADD CONSTRAINT CK29_HIP_TB01_TP_ESCOPO CHECK (TP_ESCOPO IS NULL OR TP_ESCOPO IN ('rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento'));
ALTER TABLE HIP_TB01_HIPOTESES
    ADD CONSTRAINT CK30_HIP_TB01_ESCOPO_PAR CHECK ((TP_ESCOPO IS NULL) = (ID_ESCOPO IS NULL));

CREATE INDEX IF NOT EXISTS IX430_HIP_TB01_ESCOPO ON HIP_TB01_HIPOTESES (TP_ESCOPO, ID_ESCOPO);

RESET search_path;
