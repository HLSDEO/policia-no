-- ===================================================================================================================
-- Extracao de conta na nuvem via IPED
--
-- Tabelas: IPD_TB01_EXTRACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 02 · uma linha por pasta-caso do IPED importada
CREATE TABLE IPD_TB01_EXTRACOES (
    ID_EXTRACAO_IPED         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO             BIGINT NOT NULL,
    NO_PASTA                 TEXT NOT NULL,  -- caminho da pasta do caso relativo a data/, já renomeada pro e-mail do alvo (Fase 4) e movida pra pasta do inquérito (ex.: "SIP/2025.0079407/NUVEM/alvo@gmail.com" — ver docs/organizacao_arquivos.md)
    NO_CONTA                 VARCHAR(3000),  -- e-mail do alvo, quando identificado (Fase 4); NULL até lá
    DS_VERSAO_IPED           VARCHAR(200),  -- versão do IPED detectada nos jars do caso (ex.: "4.2.2") — best-effort, só para diagnóstico
    QT_ITENS_TOTAL           INTEGER,  -- total de itens no índice Lucene do caso (maxDoc do dumper)
    QT_ARQUIVOS              INTEGER,
    QT_EMAILS                INTEGER,
    QT_CONTATOS              INTEGER,
    QT_CONVERSAS             INTEGER,
    QT_LOCALIZACOES          INTEGER,
    QT_CONTAS_FINANCEIRAS    INTEGER,
    CD_STATUS                VARCHAR(3000) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro|cancelado
    CD_FASE                  VARCHAR(3000),  -- extraindo|contagens|identificando_pessoas|indexando_semantica|cancelando
    QT_PROGRESSO_FASE        INTEGER,  -- 0-99 (100 só quando conclui)
    TX_ERRO                  TEXT,
    FL_CANCELAR              BOOLEAN NOT NULL DEFAULT false,
    ID_USUARIO               BIGINT,  -- quem importou
    DT_IMPORTACAO            TIMESTAMP NOT NULL DEFAULT now(),
    DT_INICIO_PROCESSAMENTO  TIMESTAMP,
    DT_FIM_PROCESSAMENTO     TIMESTAMP,
    ID_EXTRACAO              BIGINT,  -- ponte para INQ_TB02_EXTRACOES (NO_FABRICANTE = 'IPED')
    QT_NAVEGACAO             INTEGER,
    QT_ACESSOS               INTEGER,
    QT_ACESSOS_AGREGADOS     INTEGER,
    QT_CONEXOES              INTEGER,
    QT_ATIVIDADES            INTEGER,
    QT_APPS                  INTEGER,
    QT_DISPOSITIVOS          INTEGER,
    QT_PERFIL                INTEGER,
    QT_EVENTOS               INTEGER,
    QT_LIGACOES              INTEGER,
    QT_NOTAS                 INTEGER,
    -- sidecars de nuvem lidos (Drive "<nome>-info.json", Google Photos "<foto>.jpg.json") —
    -- ver funcoes/nuvem_metadados.py e NUV_TB01_METADADOS_ARQUIVO em db/init/29_nuvem.sql
    QT_METADADOS_NUVEM       INTEGER,
    -- domínios decodificados que o IPED entrega quando a fonte é um celular (UFDR decodificado,
    -- bancos de aplicativo lidos pelos parsers): cada mensagem, ligação, senha ou rede Wi-Fi é
    -- um item próprio do índice. Num caso de HD ou de nuvem pura ficam todos em NULL — e é
    -- assim que se lê "este caso não tinha conversa nenhuma".
    QT_MENSAGENS             INTEGER,
    QT_CONTAS_USUARIO        INTEGER,
    QT_SENHAS                INTEGER,
    QT_AUTOFILL              INTEGER,
    QT_REDES                 INTEGER,
    -- itens que o perito marcou dentro do IPED (<caso>/iped/bookmarks.iped)
    QT_MARCADORES            INTEGER,
    CONSTRAINT FK200_IPD_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK201_IPD_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO),
    CONSTRAINT UQ02_IPD_TB01_INQUERITO_PASTA UNIQUE (ID_INQUERITO, NO_PASTA)
);

CREATE INDEX IX74_IPD_TB01_ID_INQUERITO ON IPD_TB01_EXTRACOES (ID_INQUERITO);

RESET search_path;
