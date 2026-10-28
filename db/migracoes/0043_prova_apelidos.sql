-- ===================================================================================================================
-- Apelido livre para qualquer prova do card de investigação — nome dado pelo investigador
--
-- Tabelas: INQ_TB06_APELIDOS
--
-- O NO_APELIDO do 0031 só existe na extração (.ufdr). Esta tabela estende o mesmo recurso aos
-- demais blocos do card (RIF, extrato SIMBA, documento anexado, IPED, IPL, IRPF, IPEI, telefonia)
-- sem coluna nova em cada tabela de domínio: a prova é endereçada pelo par (TP_PROVA, ID_PROVA), o mesmo
-- par que o front usa para nomear cada bloco. Como no apelido da extração, é só rótulo do card —
-- não muda arquivo, escopo de IA nem permissão.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS INQ_TB06_APELIDOS (
    ID_APELIDO      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_PROVA        VARCHAR(20)  NOT NULL,
    ID_PROVA        BIGINT       NOT NULL,
    NO_APELIDO      VARCHAR(200) NOT NULL,
    ID_USUARIO      BIGINT,
    DT_ATUALIZACAO  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT UX112_INQ_TB06_PROVA UNIQUE (TP_PROVA, ID_PROVA),
    CONSTRAINT FK413_INQ_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

RESET search_path;
