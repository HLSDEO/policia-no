-- Aproveitamento do que o IPED já calculou durante o processamento do caso, e o metadado de
-- nuvem que sustenta a tela "Nuvem × aparelho". Correspondentes declarativos em
-- db/init/20_arquivos.sql, db/init/29_nuvem.sql e db/init/18_credenciais_financeiro.sql.
--
-- Contexto (docs/extracao_iped.md): a v1 da Nuvem IPED lia do índice Lucene só o suficiente
-- para montar a linha de arquivo. Ficavam no caso, sem uso:
--
--   * o OCR e a transcrição de áudio, que o IPED grava em <caso>/text/ocr-results.db e
--     <caso>/text/transcriptions.db, chaveados pelo MD5 maiúsculo do item — refazê-los aqui
--     custa GPU e tempo para produzir exatamente o mesmo texto;
--   * os identificadores que o IPED extrai por item com o RegexConfig.txt dele (CPF e CNPJ com
--     dígito verificador conferido, placa, telefone, e-mail, URL, IP, valor);
--   * extMismatch (extensão que não bate com o conteúdo — arquivo renomeado para se esconder)
--     e carved (recuperado do não alocado);
--   * o sidecar de nuvem: no Takeout, todo arquivo do Drive vem com "<nome>-info.json" e toda
--     foto do Google Photos com "<nome>.jpg.json", com data de servidor, lixeira,
--     compartilhamento e de qual aparelho a foto subiu. Viravam só mais uma linha de .json em
--     ARQ_TB01_ARQUIVOS e o conteúdo era jogado fora;
--   * X-GM-THRID e Message-ID dos e-mails do Gmail.
--
-- Tudo entra como coluna/tabela nova e nullable: extração já importada continua válida, só sem
-- os campos novos até ser reimportada.

ALTER TABLE INVESTIGACAO.ARQ_TB01_ARQUIVOS
    ADD COLUMN IF NOT EXISTS TX_TRANSCRICAO         TEXT,
    ADD COLUMN IF NOT EXISTS TX_IDENTIFICADORES     JSONB,
    ADD COLUMN IF NOT EXISTS IN_EXTENSAO_DIVERGENTE BOOLEAN,
    ADD COLUMN IF NOT EXISTS IN_RECUPERADO          BOOLEAN;

CREATE INDEX IF NOT EXISTS IX255_ARQ_TB01_TX_TRANSCRICAO_TRGM
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS USING gin (TX_TRANSCRICAO gin_trgm_ops);

CREATE INDEX IF NOT EXISTS IX256_ARQ_TB01_IDENTIFICADORES
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS USING gin (TX_IDENTIFICADORES jsonb_path_ops)
    WHERE TX_IDENTIFICADORES IS NOT NULL;

ALTER TABLE INVESTIGACAO.IPD_TB01_EXTRACOES
    ADD COLUMN IF NOT EXISTS QT_METADADOS_NUVEM INTEGER;

ALTER TABLE INVESTIGACAO.EML_TB01_EMAILS
    ADD COLUMN IF NOT EXISTS NU_THREAD   VARCHAR(80),
    ADD COLUMN IF NOT EXISTS NU_MENSAGEM VARCHAR(998);

CREATE INDEX IF NOT EXISTS IX258_EML_TB01_THREAD
    ON INVESTIGACAO.EML_TB01_EMAILS (ID_EXTRACAO, NU_THREAD)
    WHERE NU_THREAD IS NOT NULL;

CREATE TABLE IF NOT EXISTS INVESTIGACAO.NUV_TB01_METADADOS_ARQUIVO (
    ID_METADADO             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO             BIGINT NOT NULL,
    NO_ARQUIVO_ALVO         TEXT NOT NULL,
    NO_FONTE                VARCHAR(3000),
    TX_TITULO               TEXT,
    QT_BYTES                BIGINT,
    DT_CRIACAO              TIMESTAMP,
    DT_MODIFICACAO          TIMESTAMP,
    DT_CRIACAO_SERVIDOR     TIMESTAMP,
    DT_MODIFICACAO_SERVIDOR TIMESTAMP,
    DT_VISUALIZACAO         TIMESTAMP,
    DT_CAPTURA              TIMESTAMP,
    IN_LIXEIRA              BOOLEAN,
    IN_LIXEIRA_EXPLICITA    BOOLEAN,
    IN_FAVORITO             BOOLEAN,
    NO_PROPRIETARIO         VARCHAR(3000),
    TX_COMPARTILHADO_COM    TEXT,
    TP_ORIGEM_UPLOAD        VARCHAR(3000),
    NO_DISPOSITIVO_UPLOAD   VARCHAR(3000),
    NO_PASTA_DISPOSITIVO    VARCHAR(3000),
    VL_LATITUDE             NUMERIC(10,6),
    VL_LONGITUDE            NUMERIC(10,6),
    TX_ATIVIDADE            JSONB,
    CONSTRAINT FK263_NUV_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INVESTIGACAO.INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

CREATE INDEX IF NOT EXISTS IX257_NUV_TB01_EXTRACAO_ALVO
    ON INVESTIGACAO.NUV_TB01_METADADOS_ARQUIVO (ID_EXTRACAO, NO_ARQUIVO_ALVO);
