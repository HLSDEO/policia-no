-- ===================================================================================================================
-- Visões salvas da Mesa de Inquérito (privadas por usuário)
--
-- Tabela: ENT_TB10_MESA_VISOES
--
-- Recorte do quadro que o investigador guarda num nome só — filtros, fontes, teto de nós e o
-- enquadramento da câmera — para voltar a ele num clique ou abrir por link (?visao=). A linha é
-- DONO do usuário (ID_USUARIO NOT NULL): a aplicação filtra por ele em toda leitura/apagamento,
-- então cada um vê só as suas visões, mesmo num inquérito em que mais gente trabalha. O estado é
-- JSONB opaco (só a tela entende o formato). Correspondente de aplicação em
-- funcoes/mesa_visoes_utils.py e rotas/entidades.py; migração equivalente em 0056_visoes_mesa.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

-- 10 · visão salva: o par (ID_INQUERITO, ID_USUARIO, NO_VISAO) é único — o "salvar" de novo com o
-- mesmo nome substitui o estado em vez de criar um segundo registro com o mesmo nome.
CREATE TABLE ENT_TB10_MESA_VISOES (
    ID_VISAO        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO    BIGINT NOT NULL,
    ID_USUARIO      BIGINT NOT NULL,
    NO_VISAO        VARCHAR(120) NOT NULL,
    JT_ESTADO       JSONB NOT NULL,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO  TIMESTAMP,
    CONSTRAINT FK427_ENT_TB10_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO) ON DELETE CASCADE,
    CONSTRAINT FK428_ENT_TB10_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE
);

CREATE UNIQUE INDEX UX117_ENT_TB10_VISAO ON ENT_TB10_MESA_VISOES (ID_INQUERITO, ID_USUARIO, NO_VISAO);

RESET search_path;
