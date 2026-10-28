-- 35 · entrada pela API-PF (autenticacao externa)
--
-- O login do PolicIA continua sendo o principal: e o cadastro deste banco que decide quem
-- entra, quem e admin, quem foi aprovado e de que delegacia a pessoa e. A API-PF nao e um
-- segundo cadastro — e outra forma de PROVAR quem a pessoa e. Quem entra por la tem o
-- cadastro criado/atualizado aqui a partir das claims do token, e passa a existir no PolicIA
-- como qualquer outro usuario.
--
-- Duas mudancas em USR_TB01_USUARIOS sustentam isso:
--
--   TX_SENHA_HASH passa a aceitar NULL — quem chega pela API-PF ainda nao tem senha DAQUI (a
--   senha dele fica na PF e nunca passa por este servidor). O primeiro acesso obriga a criar
--   uma (POST /api/auth/primeiro-acesso), e e ela que garante a entrada quando a rede da PF
--   estiver fora. Enquanto for NULL, o login local recusa a conta: ver o filtro
--   "TX_SENHA_HASH IS NOT NULL" em funcoes/auth_utils.py:autenticar.
--
--   CO_PROVEDOR registra a ORIGEM do cadastro (onde ele nasceu), nao por onde a pessoa entra:
--   quem veio da API-PF e criou a senha local passa a entrar pelos dois caminhos.

SET search_path TO SEGURANCA, PUBLIC;

ALTER TABLE USR_TB01_USUARIOS ALTER COLUMN TX_SENHA_HASH DROP NOT NULL;

ALTER TABLE USR_TB01_USUARIOS
    ADD COLUMN IF NOT EXISTS CO_PROVEDOR VARCHAR(20) NOT NULL DEFAULT 'POLICIA';

-- Lista fechada e curta: sao as duas abas da tela de login. Um provedor novo exige migracao,
-- que e o preco de nao deixar a coluna virar texto livre num campo de seguranca.
ALTER TABLE USR_TB01_USUARIOS DROP CONSTRAINT IF EXISTS CK10_USR_TB01_CO_PROVEDOR;
ALTER TABLE USR_TB01_USUARIOS
    ADD CONSTRAINT CK10_USR_TB01_CO_PROVEDOR CHECK (CO_PROVEDOR IN ('POLICIA', 'API_PF'));

COMMENT ON COLUMN USR_TB01_USUARIOS.CO_PROVEDOR IS
    'Origem do cadastro: POLICIA (criado aqui) ou API_PF (criado no primeiro login pela API da PF).';
COMMENT ON COLUMN USR_TB01_USUARIOS.TX_SENHA_HASH IS
    'bcrypt da senha do PolicIA. NULL enquanto quem veio da API-PF nao definiu a sua — o '
    'primeiro acesso obriga a definir antes de usar o sistema.';

RESET search_path;
