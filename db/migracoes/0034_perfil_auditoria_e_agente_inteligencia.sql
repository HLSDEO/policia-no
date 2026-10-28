-- Renomeia o perfil 'corregedoria' para 'auditoria' (mesmo rotulo de tela, mesmo alcance) e cria
-- o perfil 'agente_inteligencia': igual ao de chefia ('inteligencia') para importar caso de
-- inteligencia, mas sem ve_todas_delegacias — so enxerga os inqueritos que criou ou em que tem
-- permissao (ver _capacidades em api/funcoes/auth_utils.py).
-- Correspondente declarativo em db/init/07_seguranca_usuarios.sql (CHECK CK23_USR_TB01_TP_PERFIL).
ALTER TABLE seguranca.usr_tb01_usuarios DROP CONSTRAINT CK23_USR_TB01_TP_PERFIL;

UPDATE seguranca.usr_tb01_usuarios SET tp_perfil = 'auditoria' WHERE tp_perfil = 'corregedoria';

ALTER TABLE seguranca.usr_tb01_usuarios
    ADD CONSTRAINT CK23_USR_TB01_TP_PERFIL
    CHECK (tp_perfil IN ('analista', 'inteligencia', 'agente_inteligencia', 'auditoria', 'administrador'));
