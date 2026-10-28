-- ===================================================================================================================
-- Visões salvas da Mesa de Inquérito (privadas por usuário)
--
-- Tabelas: ENT_TB10_MESA_VISOES
--
-- O investigador ajusta filtros, fontes, teto de nós e o enquadramento do quadro e quer voltar
-- àquele recorte num clique (ou abrir por link ?visao=). A linha é DONO do usuário (ID_USUARIO
-- NOT NULL) e toda leitura/apagamento é filtrada por ele na aplicação: cada um vê só as suas,
-- mesmo em inquérito compartilhado. O estado (filtros/fontes/câmera) mora em JT_ESTADO (JSONB)
-- opaco — a tela é a única que entende o formato, então ganha filtro novo sem migration.
--
-- Correspondente declarativo em db/init/49_visoes_mesa.sql.
-- ===================================================================================================================

CREATE TABLE IF NOT EXISTS INVESTIGACAO.ENT_TB10_MESA_VISOES (
    ID_VISAO        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO    BIGINT NOT NULL,
    ID_USUARIO      BIGINT NOT NULL,
    NO_VISAO        VARCHAR(120) NOT NULL,
    JT_ESTADO       JSONB NOT NULL,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO  TIMESTAMP,
    CONSTRAINT FK427_ENT_TB10_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INVESTIGACAO.INQ_TB01_INQUERITOS (ID_INQUERITO) ON DELETE CASCADE,
    CONSTRAINT FK428_ENT_TB10_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE
);

-- Uma visão por nome por usuário por inquérito: é a chave do upsert do "salvar" (substitui em vez
-- de duplicar) e também o índice que atende a lista do dropdown (WHERE id_inquerito AND id_usuario
-- ORDER BY no_visao — prefixo exatamente igual).
CREATE UNIQUE INDEX IF NOT EXISTS UX117_ENT_TB10_VISAO
    ON INVESTIGACAO.ENT_TB10_MESA_VISOES (ID_INQUERITO, ID_USUARIO, NO_VISAO);
