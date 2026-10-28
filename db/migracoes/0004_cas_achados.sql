-- Achados determinísticos (regex, sem IA) levantados do texto de cada documento de um Caso —
-- CPF, CNPJ, telefone, e-mail, valor, conta, agência, processo, procedimento, tipificação,
-- endereço, OAB, placa de veículo. Mesmo papel de IPL_TB04_ACHADOS para o IPL, menos a folha.
-- Alimenta a aba "Achados do caso" e a aba "Casos" da convergência. Correspondente declarativo
-- em db/init/23_casos.sql (CREATE TABLE CAS_TB11_ACHADOS + índices IX245/IX246).
CREATE TABLE INVESTIGACAO.CAS_TB11_ACHADOS (
    ID_ACHADO       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CASO         BIGINT NOT NULL,
    ID_DOCUMENTO    BIGINT NOT NULL,
    TP_ACHADO       VARCHAR(30) NOT NULL,  -- cpf|cnpj|telefone|email|valor|conta|agencia|processo|procedimento|tipificacao|endereco|oab|placa_veiculo
    TX_VALOR        VARCHAR(400) NOT NULL,
    VL_NUMERICO     NUMERIC(18,2),
    QT_OCORRENCIAS  INTEGER NOT NULL DEFAULT 1,
    TX_CONTEXTO     TEXT,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK254_CAS_TB11_CAS_TB01 FOREIGN KEY (ID_CASO)
        REFERENCES INVESTIGACAO.CAS_TB01_CASOS (ID_CASO),
    CONSTRAINT FK255_CAS_TB11_CAS_TB02 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INVESTIGACAO.CAS_TB02_DOCUMENTOS (ID_DOCUMENTO)
);

CREATE INDEX IX245_CAS_TB11_ID_CASO ON INVESTIGACAO.CAS_TB11_ACHADOS (ID_CASO, TP_ACHADO);
CREATE INDEX IX246_CAS_TB11_VALOR ON INVESTIGACAO.CAS_TB11_ACHADOS (TP_ACHADO, TX_VALOR);
