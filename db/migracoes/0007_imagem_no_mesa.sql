-- Foto do nó da mesa de inquérito: a imagem que o investigador põe no lugar da bolinha do
-- grafo (retrato do suspeito, foto do veículo, do local). Alvo polimórfico TP_NO/ID_NO —
-- 'canonica' aponta ENT_TB05, 'entidade' aponta ENT_TB01 —, sem FK para ele pela mesma razão
-- de ENT_TB01.TP_ORIGEM/ID_ORIGEM; a exclusão do nó apaga a imagem em cascata manual
-- (funcoes/canonicas_utils.py, funcoes/entidades_utils.py, funcoes/investigacao_utils.py).
--
-- Correspondente declarativo em db/init/21_entidades_padroes.sql (ENT_TB09_IMAGENS).

CREATE TABLE IF NOT EXISTS INVESTIGACAO.ENT_TB09_IMAGENS (
    ID_IMAGEM     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    TP_NO         VARCHAR(20) NOT NULL,
    ID_NO         BIGINT NOT NULL,
    NO_ARQUIVO    VARCHAR(300),
    TP_MIME       VARCHAR(60) NOT NULL DEFAULT 'image/jpeg',
    NU_BYTES      INTEGER NOT NULL,
    IM_CONTEUDO   BYTEA NOT NULL,
    ID_USUARIO    BIGINT,
    DT_ENVIO      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK258_ENT_TB09_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INVESTIGACAO.INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK259_ENT_TB09_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ10_ENT_TB09_NO UNIQUE (TP_NO, ID_NO),
    CONSTRAINT CK12_ENT_TB09_TP_NO CHECK (TP_NO IN ('canonica', 'entidade'))
);

CREATE INDEX IF NOT EXISTS IX249_ENT_TB09_INQUERITO
    ON INVESTIGACAO.ENT_TB09_IMAGENS (ID_INQUERITO);
