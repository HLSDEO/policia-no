-- ===================================================================================================================
-- Auditoria de gestão de usuários
--
-- Tabelas: USR_TB08_AUDITORIA_USUARIOS
--
-- Registra ações de administração de usuários: criação, edição, ativação/desativação,
-- aprovação/revogação, redefinição de senha, configuração de IA própria, alteração de delegacia.
-- ===================================================================================================================

SET search_path TO SEGURANCA, PUBLIC;

CREATE TABLE USR_TB08_AUDITORIA_USUARIOS (
    ID_AUDITORIA        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Quem fez a ação (administrador)
    ID_USUARIO_AUTOR    BIGINT NOT NULL,
    -- Usuário afetado pela ação
    ID_USUARIO_ALVO     BIGINT NOT NULL,
    -- Tipo de ação realizada
    TP_ACAO             VARCHAR(30) NOT NULL,
    -- Detalhes da ação em JSON (valores antes/depois, etc.)
    TX_DETALHES         JSONB,
    -- IP de onde veio a requisição
    CO_IP               VARCHAR(45),
    -- Quando aconteceu
    DT_ACAO             TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK255_USR_TB08_AUTOR FOREIGN KEY (ID_USUARIO_AUTOR)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK256_USR_TB08_ALVO FOREIGN KEY (ID_USUARIO_ALVO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK18_USR_TB08_TP_ACAO CHECK (TP_ACAO IN (
        'criar', 'editar', 'ativar', 'desativar',
        'aprovar', 'revogar_aprovacao',
        'redefinir_senha', 'configurar_ia', 'apagar_ia',
        'alterar_delegacia'
    ))
);

CREATE INDEX IX240_USR_TB08_AUTOR ON USR_TB08_AUDITORIA_USUARIOS (ID_USUARIO_AUTOR, DT_ACAO DESC);
CREATE INDEX IX241_USR_TB08_ALVO ON USR_TB08_AUDITORIA_USUARIOS (ID_USUARIO_ALVO, DT_ACAO DESC);
CREATE INDEX IX242_USR_TB08_ACAO ON USR_TB08_AUDITORIA_USUARIOS (TP_ACAO, DT_ACAO DESC);
CREATE INDEX IX243_USR_TB08_DT ON USR_TB08_AUDITORIA_USUARIOS (DT_ACAO DESC);

RESET search_path;