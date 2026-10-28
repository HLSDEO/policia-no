-- Perfil do usuario (substitui o binario "admin ou nao" por quatro perfis de capacidade).
-- Correspondente declarativo em db/init/07_seguranca_usuarios.sql (coluna TP_PERFIL no
-- CREATE TABLE de USR_TB01_USUARIOS + CHECK CK23_USR_TB01_TP_PERFIL).
--
-- IN_ADMIN e mantido como espelho de 'administrador' (escrito em sincronia nas rotas de
-- criacao/edicao), para nao reescrever todos os pontos que checam o privilegio global. As novas
-- capacidades derivadas (ve_todas_delegacias, pode_caso_generico, pode_auditoria) sao calculadas
-- a partir de TP_PERFIL em api/funcoes/auth_utils.py (_capacidades).
ALTER TABLE seguranca.usr_tb01_usuarios ADD COLUMN tp_perfil VARCHAR(20) NOT NULL DEFAULT 'analista';

-- Backfill: quem era admin vira 'administrador'; os demais permanecem 'analista' (padrao).
UPDATE seguranca.usr_tb01_usuarios SET tp_perfil = 'administrador' WHERE in_admin;

ALTER TABLE seguranca.usr_tb01_usuarios
    ADD CONSTRAINT CK23_USR_TB01_TP_PERFIL
    CHECK (tp_perfil IN ('analista', 'inteligencia', 'corregedoria', 'administrador'));
