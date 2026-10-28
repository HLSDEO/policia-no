-- Resumo noturno por inquérito (batch da madrugada). Correspondente
-- declarativo em db/init/44_resumo_noturno.sql (tabela INQ_TB05_RESUMO_NOTURNO).
--
-- Uma linha por fonte analisada (cada celular, cada RIF/SIMBA/..., agregado de
-- documentos) + uma linha consolidada do inquérito. A tela lê só daqui.
CREATE TABLE INVESTIGACAO.INQ_TB05_RESUMO_NOTURNO (
    ID_RESUMO               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO            BIGINT NOT NULL,
    ID_EXTRACAO             BIGINT,
    TP_FONTE                VARCHAR(20) NOT NULL,
    ID_FONTE                BIGINT,
    TX_RESUMO               TEXT,
    JQ_CONVERSAS            JSONB,
    NO_MODELO               VARCHAR(3000),
    QT_TOKENS_ENTRADA       BIGINT,
    QT_TOKENS_SAIDA         BIGINT,
    VL_CUSTO_USD            NUMERIC(12, 6),
    TX_HASH_ENTRADA         VARCHAR(64),
    CD_STATUS               VARCHAR(20) NOT NULL DEFAULT 'concluido',
    TX_ERRO                 TEXT,
    DT_GERACAO              TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM                  TIMESTAMP,
    CONSTRAINT FK400_INQ_TB05_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INVESTIGACAO.INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK401_INQ_TB05_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INVESTIGACAO.INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT CK20_INQ_TB05_TP_FONTE CHECK (TP_FONTE IN (
        'celular', 'rif', 'simba', 'ipei', 'ipl', 'irpf', 'telefonia', 'iped',
        'documentos', 'consolidado'
    )),
    CONSTRAINT CK21_INQ_TB05_CD_STATUS CHECK (CD_STATUS IN ('concluido', 'erro'))
);

CREATE INDEX IX400_INQ_TB05_ID_INQUERITO ON INVESTIGACAO.INQ_TB05_RESUMO_NOTURNO (ID_INQUERITO);
CREATE INDEX IX401_INQ_TB05_EXTRACAO ON INVESTIGACAO.INQ_TB05_RESUMO_NOTURNO (ID_EXTRACAO);
