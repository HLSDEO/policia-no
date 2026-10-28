-- Agendador interno do passe noturno. Correspondente declarativo em
-- db/init/45_agendador.sql (tabela AGD_TB01_EXECUCOES).
--
-- Ledger de "esta tarefa já rodou no dia X": a PRIMARY KEY (TP_TAREFA, DT_REFERENCIA) é a vaga
-- do dia, então de vários workers (ou de duas instalações no mesmo banco) só um roda o passe.
-- DT_REFERENCIA é o dia no fuso configurado, calculado em Python — o container roda em UTC.
-- Ver api/funcoes/agendador.py.
CREATE TABLE INVESTIGACAO.AGD_TB01_EXECUCOES (
    TP_TAREFA            VARCHAR(30)  NOT NULL,
    DT_REFERENCIA        DATE         NOT NULL,
    CD_STATUS            VARCHAR(20)  NOT NULL DEFAULT 'processando',
    NO_ORIGEM            VARCHAR(20)  NOT NULL DEFAULT 'agendador',
    NO_WORKER            VARCHAR(100),
    QT_PROCESSADOS       INTEGER      NOT NULL DEFAULT 0,
    QT_ERROS             INTEGER      NOT NULL DEFAULT 0,
    TX_ERRO              TEXT,
    DT_INICIO            TIMESTAMP    NOT NULL DEFAULT now(),
    DT_ULTIMA_ATIVIDADE  TIMESTAMP    NOT NULL DEFAULT now(),
    DT_FIM               TIMESTAMP,
    PRIMARY KEY (TP_TAREFA, DT_REFERENCIA),
    CONSTRAINT CK22_AGD_TB01_STATUS CHECK (CD_STATUS IN ('processando', 'concluido', 'erro'))
);
