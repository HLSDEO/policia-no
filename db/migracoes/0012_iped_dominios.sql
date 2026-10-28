-- Segunda rodada de aproveitamento do processamento que o IPED já fez. Correspondentes
-- declarativos em db/init/19_iped.sql e db/init/20_arquivos.sql; relato em
-- docs/extracao_iped.md.
--
-- Contexto: a importação lia do índice Lucene o suficiente para montar a linha de arquivo,
-- desviava DUAS das ~188 categorias que o IPED define (Emails e Contacts) e mandava todo o
-- resto para ARQ_TB01_ARQUIVOS como arquivo genérico. Só que o IPED não indexa arquivo, ele
-- DECODIFICA: cada mensagem de WhatsApp, cada ligação, cada senha, cada rede Wi-Fi vira um
-- item próprio do índice com metadado semântico pronto — e tudo isso estava virando linha de
-- arquivo com nome "chat_message_37" e nada mais.
--
-- Ficavam também sem uso, medidos contra casos reais:
--
--   * a linha do tempo do item (par timeStamp × timeEvent). `created`/`accessed` vêm string
--     VAZIA na maioria dos casos — no caso iCloud do repo, vazios nos 1.088 itens, enquanto o
--     par trazia 920 `modified`, 252 `common:dcterms:created`, 240 `pdf:docinfo:created`,
--     22 `created`, 22 `accessed` e 4 `image:exif:datetimeoriginal`;
--   * os sinais de triagem do IPED: nudityScore/nudityClass (detecção de nudez), face_count
--     (quantos rostos na imagem) e hashDb:status (que vale "pedo" quando o hash bate com
--     hashset de CSAM — ver WhatsAppParser.extractMessages);
--   * os identificadores estáveis do item: trackId (sobrevive a reprocessamento), o id e o
--     parentId (que ligam anexo ao e-mail e mensagem ao chat) e evidenceUUID (de QUAL
--     dispositivo/fonte de dados o item veio, num caso com vários);
--   * os marcadores do perito, em <caso>/iped/bookmarks.iped — o resultado da análise humana
--     sobre o caso, que é justamente o que já foi triado como relevante.
--
-- Tudo entra como coluna nova e nullable: extração já importada continua válida, só sem os
-- campos novos até ser reimportada.

-- ---------------------------------------------------------------------------------------
-- ARQ_TB01_ARQUIVOS
-- ---------------------------------------------------------------------------------------

ALTER TABLE INVESTIGACAO.ARQ_TB01_ARQUIVOS
    -- datas que o par timeStamp/timeEvent entrega e as colunas antigas não tinham onde pôr
    ADD COLUMN IF NOT EXISTS DT_CRIACAO       TIMESTAMP,
    ADD COLUMN IF NOT EXISTS DT_ACESSO        TIMESTAMP,
    -- a linha do tempo inteira, {"evento": "ISO"} — guarda de ONDE cada data veio, que é o
    -- que separa "criado pelo usuário" de "gravado pelo Ghostscript ao converter o PDF"
    ADD COLUMN IF NOT EXISTS TX_LINHA_TEMPO   JSONB,
    -- identificadores estáveis do item no caso
    ADD COLUMN IF NOT EXISTS CO_TRACK_ID      VARCHAR(64),
    ADD COLUMN IF NOT EXISTS NU_ID_ORIGEM     INTEGER,
    ADD COLUMN IF NOT EXISTS NU_ID_PAI        INTEGER,
    -- qual fonte de dados (aparelho/mídia) do caso. Um caso do IPED com 3 celulares e 1
    -- computador colapsava numa extração-ponte só, sem como dizer de onde veio cada arquivo
    ADD COLUMN IF NOT EXISTS CO_EVIDENCIA     VARCHAR(64),
    -- triagem que o IPED já fez
    ADD COLUMN IF NOT EXISTS VL_NUDEZ         REAL,
    ADD COLUMN IF NOT EXISTS TX_CLASSE_NUDEZ  VARCHAR(60),
    ADD COLUMN IF NOT EXISTS QT_FACES         SMALLINT,
    -- hashDb:status + hashDb:set. "pedo" aqui significa que o hash do item bateu com hashset
    -- de material de abuso infantil carregado no caso — é alerta, não conclusão
    ADD COLUMN IF NOT EXISTS TX_ALERTA_HASH   VARCHAR(200),
    -- marcadores do perito (bookmarks.iped), separados por vírgula, e a marca de "selecionado"
    ADD COLUMN IF NOT EXISTS TX_MARCADORES    TEXT,
    ADD COLUMN IF NOT EXISTS IN_SELECIONADO   BOOLEAN;

-- casar marcador (que vem por id de item do caso) com a linha já gravada, e resolver
-- anexo→e-mail por parentId. Parciais porque só a Nuvem IPED preenche as colunas.
CREATE INDEX IF NOT EXISTS IX259_ARQ_TB01_ID_ORIGEM
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO, NU_ID_ORIGEM)
    WHERE NU_ID_ORIGEM IS NOT NULL;

-- "o que o perito marcou" e "o que o IPED sinalizou" são as duas primeiras perguntas da
-- triagem; sem índice parcial elas varrem centenas de milhares de linhas por extração
CREATE INDEX IF NOT EXISTS IX260_ARQ_TB01_MARCADORES
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_MARCADORES IS NOT NULL;
CREATE INDEX IF NOT EXISTS IX261_ARQ_TB01_ALERTA_HASH
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_ALERTA_HASH IS NOT NULL;

-- ---------------------------------------------------------------------------------------
-- IPD_TB01_EXTRACOES · contadores dos domínios novos
-- ---------------------------------------------------------------------------------------

ALTER TABLE INVESTIGACAO.IPD_TB01_EXTRACOES
    ADD COLUMN IF NOT EXISTS QT_MENSAGENS      INTEGER,
    ADD COLUMN IF NOT EXISTS QT_CONTAS_USUARIO INTEGER,
    ADD COLUMN IF NOT EXISTS QT_SENHAS         INTEGER,
    ADD COLUMN IF NOT EXISTS QT_AUTOFILL       INTEGER,
    ADD COLUMN IF NOT EXISTS QT_REDES          INTEGER,
    ADD COLUMN IF NOT EXISTS QT_MARCADORES     INTEGER;
