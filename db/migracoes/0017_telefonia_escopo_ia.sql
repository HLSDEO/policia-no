-- Escopo 'telefonia' nos dois CHECK de TP_ESCOPO. Correspondentes declarativos em
-- db/init/24_ia.sql (IA_TB01_CONVERSAS) e db/init/31_marcadores_achados.sql (ACH_TB01).
--
-- Faltou em 0016_telefonia.sql: os botoes "Prompt de IA da operadora" e "Achados do analista" do
-- bloco de telefonia abriam a tela normalmente e so estouravam no INSERT da primeira conversa
-- (500 com CheckViolation em ck05_ia_tb01_tp_escopo). E o mesmo delta que 0015_ipei.sql precisou
-- fazer quando a IPEI entrou: escopo novo exige tocar nesta lista, senao a feature parece pronta
-- e quebra no primeiro uso.

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE IA_TB01_CONVERSAS DROP CONSTRAINT IF EXISTS CK05_IA_TB01_TP_ESCOPO;
ALTER TABLE IA_TB01_CONVERSAS ADD CONSTRAINT CK05_IA_TB01_TP_ESCOPO CHECK (
    TP_ESCOPO IS NULL OR TP_ESCOPO IN (
    'rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento',
    'wpp', 'sms', 'msg',
    'conversa_wpp', 'conversa_sms', 'conversa_msg'
    )
);

ALTER TABLE ACH_TB01_ACHADOS_ANALISTA DROP CONSTRAINT IF EXISTS CK06_ACH_TB01_TP_ESCOPO;
ALTER TABLE ACH_TB01_ACHADOS_ANALISTA ADD CONSTRAINT CK06_ACH_TB01_TP_ESCOPO
    CHECK (TP_ESCOPO IN ('rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento'));
