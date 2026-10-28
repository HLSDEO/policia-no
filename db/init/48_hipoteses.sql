-- ===================================================================================================================
-- Workspace de hipóteses do inquérito
--
-- Tabelas: HIP_TB01_HIPOTESES, HIP_TB02_EVIDENCIAS, HIP_TB03_PENDENCIAS
--
-- Substituem os antigos achados do analista (ACH_TB01, removidos na migração 0046): a hipótese é
-- do inquérito inteiro — não presa a um único documento — e traz estrutura de investigação:
-- evidências a favor e contra (texto livre e/ou vínculo a um item importado), pendências com
-- responsável e status kanban (proposta → em análise → confirmada / refutada / arquivada).
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · hipótese: o objeto central do workspace, dono do quadro por status. O par opcional
-- (TP_ESCOPO, ID_ESCOPO) — os dois juntos — associa a hipótese a uma MÍDIA importada (extração
-- UFDR, RIF, SIMBA, IPL, IRPF, IPEI, telefonia ou documento); é por ele (ou por uma evidência
-- que aponte àquela mídia) que a hipótese entra no chat de IA específico dela.
CREATE TABLE HIP_TB01_HIPOTESES (
    ID_HIPOTESE            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO           BIGINT NOT NULL,
    TX_HIPOTESE            TEXT NOT NULL,
    CD_STATUS              VARCHAR(20) NOT NULL DEFAULT 'proposta',
    ID_USUARIO_RESPONSAVEL BIGINT,  -- investigador encarregado de confirmar ou refutar
    ID_USUARIO_CRIADOR     BIGINT,
    DT_CRIACAO             TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO         TIMESTAMP,
    TP_NATUREZA            VARCHAR(20) NOT NULL DEFAULT 'hipotese',  -- legado dos achados: nem todo registro migrado era hipótese
    FL_CONTESTAVEL_IA      BOOLEAN NOT NULL DEFAULT FALSE,
    TP_ESCOPO              VARCHAR(20),
    ID_ESCOPO              BIGINT,
    CONSTRAINT FK414_HIP_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK415_HIP_TB01_USR_RESP FOREIGN KEY (ID_USUARIO_RESPONSAVEL)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK416_HIP_TB01_USR_CRIADOR FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK24_HIP_TB01_CD_STATUS CHECK (CD_STATUS IN ('proposta', 'em_analise', 'confirmada', 'refutada', 'arquivada')),
    CONSTRAINT CK25_HIP_TB01_TP_NATUREZA CHECK (TP_NATUREZA IN ('hipotese', 'normativa', 'fato')),
    CONSTRAINT CK29_HIP_TB01_TP_ESCOPO CHECK (TP_ESCOPO IS NULL OR TP_ESCOPO IN ('rif', 'simba', 'ipl', 'irpf', 'ipei', 'telefonia', 'extracao', 'documento')),
    CONSTRAINT CK30_HIP_TB01_ESCOPO_PAR CHECK ((TP_ESCOPO IS NULL) = (ID_ESCOPO IS NULL))
);

CREATE INDEX IX425_HIP_TB01_ID_INQUERITO ON HIP_TB01_HIPOTESES (ID_INQUERITO);
CREATE INDEX IX426_HIP_TB01_STATUS ON HIP_TB01_HIPOTESES (ID_INQUERITO, CD_STATUS);
CREATE INDEX IX430_HIP_TB01_ESCOPO ON HIP_TB01_HIPOTESES (TP_ESCOPO, ID_ESCOPO);

-- 02 · evidência: um argumento a favor ou contra a hipótese. Texto livre sempre; o par
-- (TP_ESCOPO, ID_ESCOPO) — opcional, os dois juntos — prende a evidência a um item importado,
-- endereçado do mesmo jeito polimórfico que o chat de IA usa (ver _TABELA_ESCOPO em ia_utils).
CREATE TABLE HIP_TB02_EVIDENCIAS (
    ID_EVIDENCIA       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_HIPOTESE        BIGINT NOT NULL,
    TP_LADO            VARCHAR(10) NOT NULL,  -- 'favoravel' | 'contraria'
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

CREATE INDEX IX427_HIP_TB02_ID_HIPOTESE ON HIP_TB02_EVIDENCIAS (ID_HIPOTESE);
CREATE INDEX IX428_HIP_TB02_ESCOPO ON HIP_TB02_EVIDENCIAS (TP_ESCOPO, ID_ESCOPO);

-- 03 · pendência: diligência ou verificação em aberto que a hipótese ainda precisa ver respondida
CREATE TABLE HIP_TB03_PENDENCIAS (
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

CREATE INDEX IX429_HIP_TB03_ID_HIPOTESE ON HIP_TB03_PENDENCIAS (ID_HIPOTESE);

RESET search_path;
