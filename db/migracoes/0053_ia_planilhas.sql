-- ===================================================================================================================
-- Planilhas .xlsx consolidadas pedidas no chat — registro para a rota de download
--
-- Tabelas: IA_TB07_PLANILHAS (nova)
--
-- Quando a tarefa é grande demais para uma resposta de chat (ex.: "planilha com todas as armas apreendidas
-- em 2026 a partir de 200 PDFs"), a IA oferece acionar o agente em background: um job (JOB_TB01_JOBS,
-- tp_job='consolidacao_ia') varre os documentos do escopo com chamadas em lote à IA (map-reduce), monta a
-- planilha e grava o .xlsx em data/planilhas_ia. Esta tabela aponta o arquivo para a rota
-- /api/investigacao/ia/planilhas/{id}/download, que reconfere o acesso pelo inquérito — mesmo desenho da
-- IA_TB05_RELATORIOS_DOCX. Aditiva: nada em bancos existentes muda.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS IA_TB07_PLANILHAS (
    ID_PLANILHA_IA   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CONVERSA_IA   BIGINT,
    ID_INQUERITO     BIGINT,
    ID_USUARIO       BIGINT,
    NO_ARQUIVO       VARCHAR(500) NOT NULL,
    CAMINHO_ARQUIVO  TEXT NOT NULL,
    QT_LINHAS        INTEGER,
    DT_CRIACAO       TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK425_IA_TB07_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK426_IA_TB07_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IF NOT EXISTS IX433_IA_TB07_ID_CONVERSA_IA ON IA_TB07_PLANILHAS (ID_CONVERSA_IA);
CREATE INDEX IF NOT EXISTS IX434_IA_TB07_DT_CRIACAO ON IA_TB07_PLANILHAS (DT_CRIACAO);

RESET search_path;
