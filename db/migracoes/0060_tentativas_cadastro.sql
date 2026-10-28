-- ===================================================================================================================
-- Throttle de tentativas de autocadastro por IP
--
-- Tabela: USR_TB09_TENTATIVAS_CADASTRO
--
-- O POST /api/auth/cadastro é público e respondia "já existe um usuário com esse login/e-mail"
-- sem nenhum limite: um anônimo podia varrer a lista de logins e e-mails do sistema (oracle de
-- enumeração ilimitado — o login tem bloqueio por tentativas desde sempre, o cadastro não tinha
-- nada). A rota passa a registrar cada tentativa com o IP de origem e a recusar 429 quem passa
-- do limite na janela (ver funcoes/auth_utils.py). Aditivo.
-- ===================================================================================================================

SET search_path TO SEGURANCA, PUBLIC;

CREATE TABLE IF NOT EXISTS USR_TB09_TENTATIVAS_CADASTRO (
    ID_TENTATIVA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    CO_IP         VARCHAR(45),  -- origem (45 = IPv6 textual); nulo se não determinada
    IN_SUCESSO    BOOLEAN NOT NULL,
    DT_TENTATIVA  TIMESTAMP NOT NULL DEFAULT now()
);

-- Cobre a contagem da janela ("este IP desde tal instante") — mesmo desenho do IX104 de login.
CREATE INDEX IF NOT EXISTS IX108_USR_TB09_IP ON USR_TB09_TENTATIVAS_CADASTRO (CO_IP, DT_TENTATIVA);

RESET search_path;
