-- Avaliação das respostas da IA (👍/👎 por mensagem do assistente). Correspondente
-- declarativo em db/init/24_ia.sql (tabela IA_TB06_FEEDBACK).
--
-- Um voto por usuário e mensagem (UX108): votar de novo no mesmo sentido mantém, no sentido
-- oposto troca, e votar com nulo remove — ver registrar_feedback em funcoes/ia_utils.py.
-- Sem FK para o chat do caso de propósito: esta tabela é só do chat do inquérito
-- (IA_TB02_MENSAGENS); o ON DELETE CASCADE apaga os votos com a mensagem.
CREATE TABLE INVESTIGACAO.IA_TB06_FEEDBACK (
    ID_FEEDBACK   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_MENSAGEM_IA BIGINT NOT NULL,
    ID_USUARIO    BIGINT NOT NULL,
    TP_FEEDBACK   VARCHAR(10) NOT NULL,
    DT_CRIACAO    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK298_IA_TB06_IA_TB02 FOREIGN KEY (ID_MENSAGEM_IA)
        REFERENCES INVESTIGACAO.IA_TB02_MENSAGENS (ID_MENSAGEM_IA) ON DELETE CASCADE,
    CONSTRAINT FK299_IA_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE,
    CONSTRAINT CK17_IA_TB06_TP_FEEDBACK CHECK (TP_FEEDBACK IN ('positivo', 'negativo')),
    CONSTRAINT UX108_IA_TB06_MSG_USUARIO UNIQUE (ID_MENSAGEM_IA, ID_USUARIO)
);

CREATE INDEX IX298_IA_TB06_ID_MENSAGEM ON INVESTIGACAO.IA_TB06_FEEDBACK (ID_MENSAGEM_IA);
