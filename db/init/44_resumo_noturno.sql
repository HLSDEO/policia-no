-- ===================================================================================================================
-- Resumo noturno por inquérito (batch da madrugada)
--
-- Tabelas: INQ_TB05_RESUMO_NOTURNO
--
-- Guarda o resultado do passe noturno que resume cada inquérito: um consolidado do caso
-- (TP_FONTE = 'consolidado', ID_EXTRACAO/ID_FONTE nulos) + uma linha por fonte analisada
-- (cada celular, cada RIF/SIMBA/IPEI/IPL/IRPF/telefonia/IPED e o agregado de documentos).
-- A tela investigacao.html?inquerito=ID lê só daqui (nunca chama a IA no request).
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 05 · resumo gerado na madrugada, por inquérito e por fonte
CREATE TABLE INQ_TB05_RESUMO_NOTURNO (
    ID_RESUMO               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO            BIGINT NOT NULL,
    -- NULL = consolidado do inquérito; preenchido só nas linhas por celular
    ID_EXTRACAO             BIGINT,
    -- celular|rif|simba|ipei|ipl|irpf|telefonia|iped|documentos|consolidado
    TP_FONTE                VARCHAR(20) NOT NULL,
    -- ID_RIF / ID_EXTRATO / ID_IPEI / ID_IPL / ... conforme TP_FONTE; NULL no consolidado,
    -- no por-celular (usa ID_EXTRACAO) e no agregado de documentos
    ID_FONTE                BIGINT,
    -- resumo em texto livre (o que a tela mostra) + conversas suspeitas estruturadas
    -- [{contato, app, motivo, pratica_suspeita, refs:[...]}] para o por-celular
    TX_RESUMO               TEXT,
    JQ_CONVERSAS            JSONB,
    -- que modelo gerou, quanto custou (auditoria resumida na própria linha)
    NO_MODELO               VARCHAR(3000),
    QT_TOKENS_ENTRADA       BIGINT,
    QT_TOKENS_SAIDA         BIGINT,
    VL_CUSTO_USD            NUMERIC(12, 6),
    -- hash das entradas (contagens + datas de importação + versões de análise). Igual ao
    -- último concluído = nada mudou = o passe pula sem chamar a IA.
    TX_HASH_ENTRADA         VARCHAR(64),
    CD_STATUS               VARCHAR(20) NOT NULL DEFAULT 'concluido',  -- concluido|erro
    TX_ERRO                 TEXT,
    DT_GERACAO              TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM                  TIMESTAMP,
    CONSTRAINT FK400_INQ_TB05_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK401_INQ_TB05_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT CK20_INQ_TB05_TP_FONTE CHECK (TP_FONTE IN (
        'celular', 'rif', 'simba', 'ipei', 'ipl', 'irpf', 'telefonia', 'iped',
        'documentos', 'consolidado'
    )),
    CONSTRAINT CK21_INQ_TB05_CD_STATUS CHECK (CD_STATUS IN ('concluido', 'erro'))
);

CREATE INDEX IX400_INQ_TB05_ID_INQUERITO ON INQ_TB05_RESUMO_NOTURNO (ID_INQUERITO);
CREATE INDEX IX401_INQ_TB05_EXTRACAO ON INQ_TB05_RESUMO_NOTURNO (ID_EXTRACAO);

RESET search_path;
