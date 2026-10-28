-- ===================================================================================================================
-- Sessoes de conta na nuvem do Cellebrite (RemoteServiceSession) em ACS_TB01_ACESSOS
--
-- Tabelas: ACS_TB01_ACESSOS
--
-- A tela "Acessos" existia so para a Nuvem Google/IPED (evento instantaneo, conta = a do
-- Takeout). O report.xml do Cellebrite traz <model type="RemoteServiceSession">: sessoes de
-- login de contas (Facebook/Instagram/Google) com IP, user-agent, tipo de dispositivo e o
-- PERIODO da sessao (DateStarted/DateEnded) mais o nome da conta. Duas colunas novas,
-- opcionais (NULL para quem grava evento instantaneo): DT_FIM_ACESSO e NO_CONTA.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE ACS_TB01_ACESSOS
    ADD COLUMN IF NOT EXISTS DT_FIM_ACESSO TIMESTAMP,
    ADD COLUMN IF NOT EXISTS NO_CONTA      VARCHAR(3000);

RESET search_path;
