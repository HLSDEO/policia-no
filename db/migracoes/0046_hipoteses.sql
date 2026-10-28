-- ===================================================================================================================
-- Workspace de hipóteses do inquérito — substitui os achados do analista
--
-- Tabelas: HIP_TB01_HIPOTESES, HIP_TB02_EVIDENCIAS, HIP_TB03_PENDENCIAS (+ drop de ACH_TB01)
--
-- A hipótese passa a ser do inquérito inteiro (não presa a um único documento) e ganha estrutura:
-- evidências a favor/contra, pendências, responsável e status kanban. Cada achado antigo vira uma
-- hipótese — confiança 'verificada' cai em 'confirmada', 'em_analise' em 'em_analise' — e o
-- documento de origem do achado entra como evidência favorável vinculada (par TP_ESCOPO/ID_ESCOPO
-- preservado). A natureza do achado é preservada em TP_NATUREZA: registro que nunca foi hipótese
-- (fato/normativa) continua identificável como tal. Correspondente declarativo em
-- db/init/48_hipoteses.sql (e ACH_TB01 sai de db/init/31_marcadores_achados.sql).
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS HIP_TB01_HIPOTESES (
    ID_HIPOTESE            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO           BIGINT NOT NULL,
    TX_HIPOTESE            TEXT NOT NULL,
    CD_STATUS              VARCHAR(20) NOT NULL DEFAULT 'proposta',
    ID_USUARIO_RESPONSAVEL BIGINT,
    ID_USUARIO_CRIADOR     BIGINT,
    DT_CRIACAO             TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO         TIMESTAMP,
    TP_NATUREZA            VARCHAR(20) NOT NULL DEFAULT 'hipotese',
    FL_CONTESTAVEL_IA      BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT FK414_HIP_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK415_HIP_TB01_USR_RESP FOREIGN KEY (ID_USUARIO_RESPONSAVEL)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK416_HIP_TB01_USR_CRIADOR FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK24_HIP_TB01_CD_STATUS CHECK (CD_STATUS IN ('proposta', 'em_analise', 'confirmada', 'refutada', 'arquivada')),
    CONSTRAINT CK25_HIP_TB01_TP_NATUREZA CHECK (TP_NATUREZA IN ('hipotese', 'normativa', 'fato'))
);

CREATE INDEX IF NOT EXISTS IX425_HIP_TB01_ID_INQUERITO ON HIP_TB01_HIPOTESES (ID_INQUERITO);
CREATE INDEX IF NOT EXISTS IX426_HIP_TB01_STATUS ON HIP_TB01_HIPOTESES (ID_INQUERITO, CD_STATUS);

CREATE TABLE IF NOT EXISTS HIP_TB02_EVIDENCIAS (
    ID_EVIDENCIA       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_HIPOTESE        BIGINT NOT NULL,
    TP_LADO            VARCHAR(10) NOT NULL,
    TX_EVIDENCIA       TEXT NOT NULL,
    TP_ESCOPO          VARCHAR(20),
    ID_ESCOPO          BIGINT,
    ID_USUARIO_CRIADOR BIGINT,
    DT_CRIACAO         TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK417_HIP_TB02_HIP_TB01 FOREIGN KEY (ID_HIPOTESE)
        REFERENCES HIP_TB01_HIPOTESES (ID_HIPOTESE) ON DELETE CASCADE,
    CONSTRAINT FK418_HIP_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK26_HIP_TB02_TP_LADO CHECK (TP_LADO IN ('favoravel', 'contraria')),
    CONSTRAINT CK27_HIP_TB02_TP_ESCOPO CHECK (TP_ESCOPO IS NULL OR TP_ESCOPO IN ('rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento')),
    CONSTRAINT CK28_HIP_TB02_ESCOPO_PAR CHECK ((TP_ESCOPO IS NULL) = (ID_ESCOPO IS NULL))
);

CREATE INDEX IF NOT EXISTS IX427_HIP_TB02_ID_HIPOTESE ON HIP_TB02_EVIDENCIAS (ID_HIPOTESE);
CREATE INDEX IF NOT EXISTS IX428_HIP_TB02_ESCOPO ON HIP_TB02_EVIDENCIAS (TP_ESCOPO, ID_ESCOPO);

CREATE TABLE IF NOT EXISTS HIP_TB03_PENDENCIAS (
    ID_PENDENCIA       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_HIPOTESE        BIGINT NOT NULL,
    TX_PENDENCIA       TEXT NOT NULL,
    IN_CONCLUIDA       BOOLEAN NOT NULL DEFAULT FALSE,
    ID_USUARIO_CRIADOR BIGINT,
    DT_CRIACAO         TIMESTAMP NOT NULL DEFAULT now(),
    DT_CONCLUSAO       TIMESTAMP,
    CONSTRAINT FK419_HIP_TB03_HIP_TB01 FOREIGN KEY (ID_HIPOTESE)
        REFERENCES HIP_TB01_HIPOTESES (ID_HIPOTESE) ON DELETE CASCADE,
    CONSTRAINT FK420_HIP_TB03_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IF NOT EXISTS IX429_HIP_TB03_ID_HIPOTESE ON HIP_TB03_PENDENCIAS (ID_HIPOTESE);

-- Migração dos achados: um achado → uma hipótese + uma evidência favorável presa ao item de
-- origem. Banco novo (só init) não tem ACH_TB01 — o bloco simplesmente não roda.
DO $$
DECLARE
    r   RECORD;
    v_id_hipotese BIGINT;
BEGIN
    IF to_regclass('investigacao.ach_tb01_achados_analista') IS NULL THEN
        RETURN;
    END IF;
    FOR r IN SELECT * FROM INVESTIGACAO.ACH_TB01_ACHADOS_ANALISTA ORDER BY DT_CRIACAO LOOP
        INSERT INTO INVESTIGACAO.HIP_TB01_HIPOTESES
            (id_inquerito, tx_hipotese, cd_status, id_usuario_criador, dt_criacao, dt_atualizacao,
             tp_natureza, fl_contestavel_ia)
        VALUES
            (r.ID_INQUERITO, r.TX_ACHADO,
             CASE r.TP_CONFIANCA WHEN 'verificada' THEN 'confirmada' ELSE 'em_analise' END,
             r.ID_USUARIO_CRIADOR, r.DT_CRIACAO, r.DT_ATUALIZACAO,
             r.TP_NATUREZA, r.FL_CONTESTAVEL_IA)
        RETURNING ID_HIPOTESE INTO v_id_hipotese;

        INSERT INTO INVESTIGACAO.HIP_TB02_EVIDENCIAS
            (id_hipotese, tp_lado, tx_evidencia, tp_escopo, id_escopo, id_usuario_criador, dt_criacao)
        VALUES
            (v_id_hipotese, 'favoravel', r.TX_ACHADO, r.TP_ESCOPO, r.ID_ESCOPO,
             r.ID_USUARIO_CRIADOR, r.DT_CRIACAO);
    END LOOP;
END $$;

DROP TABLE IF EXISTS INVESTIGACAO.ACH_TB01_ACHADOS_ANALISTA;

RESET search_path;
