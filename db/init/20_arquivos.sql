-- ===================================================================================================================
-- Arquivos do aparelho, categorias de imagem e embedding visual
--
-- Tabelas: ARQ_TB01_ARQUIVOS, ARQ_TB02_EMBEDDING_IMAGEM, CATEGORIAS_IMAGEM
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · arquivos do dispositivo (<file> do UFDR). Uma extração de celular tem de
-- dezenas de milhares (Android) a centenas de milhares (iPhone) de linhas aqui —
-- a listagem na tela é paginada/filtrada no servidor por causa disso.
CREATE TABLE ARQ_TB01_ARQUIVOS (
    ID_ARQUIVO         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO        BIGINT NOT NULL,
    NO_ARQUIVO         TEXT,  -- nome do arquivo (último trecho do caminho; truncado em 2000)
    TX_CAMINHO         TEXT,  -- caminho completo no aparelho (path do <file>)
    NO_CATEGORIA       VARCHAR(3000),  -- categoria normalizada: Imagem/Vídeo/Áudio/Documento/Compactado/Outro
    TP_MIME            VARCHAR(3000),  -- MIME derivado da extensão (usado para servir/abrir)
    QT_BYTES           BIGINT,  -- size do <file>
    DT_MODIFICACAO     TIMESTAMP,  -- accessInfo/ModifyTime
    CD_STATUS_ARQUIVO  VARCHAR(3000),  -- atributo deleted (Intact, Deleted, None…)
    CO_CAMINHO_ZIP     TEXT,  -- "Local Path" dentro do ZIP (NULL = binário não extraído)
    CLASSIFICACAO_IA   VARCHAR(200),
    CONFIANCA_IA       DOUBLE PRECISION,
    TX_OCR             TEXT,  -- texto reconhecido na imagem (NULL = nenhum texto legível)
    DT_OCR             TIMESTAMP,  -- quando o OCR rodou (NULL = imagem ainda não processada)
    CO_HASH_MD5        VARCHAR(64),
    CO_HASH_SHA1       VARCHAR(64),
    CO_HASH_SHA256     VARCHAR(64),
    -- id do <file> dentro do report.xml — chave da ligação acima. Não é o
    -- ID_ARQUIVO do banco: é o identificador que o Physical Analyzer usa.
    NU_ID_UFDR         VARCHAR(80),
    -- ---------- o que a Nuvem IPED aproveita do processamento que o IPED já fez ----------
    -- transcrição do áudio, lida de <caso>/text/transcriptions.db (o IPED transcreve durante
    -- o processamento). Só a Nuvem IPED preenche; no UFDR a transcrição de anexo continua em
    -- WPP/SMS/MSG_TB05_ANEXOS.TX_TRANSCRICAO, que é onde o áudio de conversa vive.
    TX_TRANSCRICAO     TEXT,
    -- identificadores que o IPED já extraiu por item com o RegexConfig.txt dele (CPF/CNPJ com
    -- dígito verificador conferido, placa, telefone, e-mail, URL, IP, valor). JSONB e não
    -- tabela filha porque a carga é feita por COPY em lote — que não devolve ID_ARQUIVO — e
    -- porque a pergunta real é sempre "quais arquivos citam este identificador", que o índice
    -- GIN abaixo responde. Forma: {"cpf": ["…"], "cnpj": [...], "placa": [...], …}
    TX_IDENTIFICADORES JSONB,
    -- extensão não bate com o conteúdo detectado (extMismatch do IPED) — sinal clássico de
    -- arquivo renomeado para se esconder da galeria
    IN_EXTENSAO_DIVERGENTE BOOLEAN,
    -- item recuperado do espaço não alocado (carved do IPED). Diferente de CD_STATUS_ARQUIVO
    -- = 'Deleted': lá o sistema de arquivos ainda tinha a entrada, aqui não tinha mais nada.
    IN_RECUPERADO      BOOLEAN,
    -- datas do par timeStamp × timeEvent do índice. `created`/`accessed` do IPED vêm string
    -- VAZIA na maioria dos casos (nos 1.088 itens do caso iCloud do repo, todos), enquanto o
    -- par traz a data de verdade — e diz de ONDE ela veio (sistema de arquivos, metadado do
    -- documento, EXIF da foto). TX_LINHA_TEMPO guarda a lista inteira: {"evento": "ISO"}.
    DT_CRIACAO         TIMESTAMP,
    DT_ACESSO          TIMESTAMP,
    TX_LINHA_TEMPO     JSONB,
    -- identificadores estáveis do item dentro do caso do IPED: CO_TRACK_ID sobrevive a
    -- reprocessamento; NU_ID_ORIGEM/NU_ID_PAI são o id e o parentId, que ligam anexo ao
    -- e-mail e item ao container de onde saiu (e casam os marcadores do perito).
    CO_TRACK_ID        VARCHAR(64),
    NU_ID_ORIGEM       INTEGER,
    NU_ID_PAI          INTEGER,
    -- de QUAL fonte de dados do caso o item veio (evidenceUUID). Um caso do IPED pode ter
    -- vários aparelhos e mídias; sem isto todos colapsam numa extração-ponte só.
    CO_EVIDENCIA       VARCHAR(64),
    -- ---------- triagem que o IPED já fez durante o processamento ----------
    -- detecção de nudez (nudityScore 0-1000 / nudityClass) e contagem de rostos na imagem
    VL_NUDEZ           REAL,
    TX_CLASSE_NUDEZ    VARCHAR(60),
    QT_FACES           SMALLINT,
    -- hashDb:status + hashDb:set. "pedo" significa que o hash bateu com hashset de material
    -- de abuso infantil carregado no caso — é alerta para triagem, não conclusão.
    TX_ALERTA_HASH     VARCHAR(200),
    -- marcadores do perito, lidos de <caso>/iped/bookmarks.iped: o resultado da análise
    -- humana sobre o caso, que é exatamente o que já foi triado como relevante
    TX_MARCADORES      TEXT,
    IN_SELECIONADO     BOOLEAN,
    CONSTRAINT FK138_ARQ_TB01_INQ_TB02 FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO)
);

-- filtro base da tela: sempre por extração, quase sempre também por categoria
CREATE INDEX IX71_ARQ_TB01_EXTRACAO_CAT ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, NO_CATEGORIA);
-- busca por nome/caminho (ILIKE '%termo%') sobre centenas de milhares de linhas:
-- trigrama para não virar SEQ SCAN. pg_trgm/gin_trgm_ops já vêm do 10_busca_fuzzy.sql
CREATE INDEX IX72_ARQ_TB01_NO_ARQUIVO_TRGM ON ARQ_TB01_ARQUIVOS USING gin (NO_ARQUIVO gin_trgm_ops);
CREATE INDEX IX73_ARQ_TB01_TX_CAMINHO_TRGM ON ARQ_TB01_ARQUIVOS USING gin (TX_CAMINHO gin_trgm_ops);
CREATE INDEX IX_ARQ_TB01_CLASSIFICACAO_IA ON ARQ_TB01_ARQUIVOS (CLASSIFICACAO_IA);
-- a busca da tela passa a varrer o texto do OCR junto com nome/caminho (ILIKE '%termo%'):
-- trigrama pelo mesmo motivo dos índices de NO_ARQUIVO/TX_CAMINHO
CREATE INDEX IX77_ARQ_TB01_TX_OCR_TRGM ON ARQ_TB01_ARQUIVOS USING gin (TX_OCR gin_trgm_ops);
-- fila do OCR: "imagens desta extração ainda não processadas" (DT_OCR IS NULL)
CREATE INDEX IX78_ARQ_TB01_OCR_PENDENTE ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO) WHERE DT_OCR IS NULL;
CREATE INDEX IF NOT EXISTS IX152_ARQ_TB01_ID_UFDR ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, NU_ID_UFDR)
    WHERE NU_ID_UFDR IS NOT NULL;
CREATE INDEX IF NOT EXISTS IX203_ARQ_TB01_EXTRACAO_NOME_LOWER
    ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, LOWER(NO_ARQUIVO));
-- cruzamento entre aparelhos do mesmo inquérito ("Comparar celulares"): agrupa por hash para
-- achar o arquivo que está em dois celulares, e depois busca o detalhe pelo hash da página em
-- tela. CO_HASH_MD5 na frente serve aos dois passes; parcial porque extração antiga (anterior à
-- leitura de MD5/SHA-256 do <file>) tem a coluna vazia na tabela inteira.
CREATE INDEX IF NOT EXISTS IX250_ARQ_TB01_HASH_MD5 ON ARQ_TB01_ARQUIVOS (CO_HASH_MD5, ID_EXTRACAO)
    WHERE CO_HASH_MD5 IS NOT NULL;
-- o mesmo cruzamento no sentido inverso: "quantos arquivos com hash tem ESTA extração" (os chips
-- do topo da tela Comparar celulares). Com só o IX250 (hash na frente) a contagem não consegue
-- buscar por extração e varre o índice inteiro, de todos os inquéritos.
CREATE INDEX IF NOT EXISTS IX263_ARQ_TB01_EXTRACAO_HASH ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, CO_HASH_MD5)
    WHERE CO_HASH_MD5 IS NOT NULL;

-- anexo de mensagem -> arquivo do aparelho: os relatórios de análise imprimem o MD5 embaixo do
-- nome do anexo, e o anexo só guarda o ponteiro para o binário no ZIP (CO_CAMINHO_ZIP). Sem
-- este índice a busca do hash varreria a tabela inteira uma vez por anexo do relatório.
-- Parcial porque arquivo sem binário extraído nunca é alvo dessa busca.
CREATE INDEX IF NOT EXISTS IX251_ARQ_TB01_CAMINHO_ZIP ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, CO_CAMINHO_ZIP)
    WHERE CO_CAMINHO_ZIP IS NOT NULL;

-- a busca da tela varre a transcrição junto com nome/caminho/OCR, pelo mesmo motivo dos outros
CREATE INDEX IF NOT EXISTS IX255_ARQ_TB01_TX_TRANSCRICAO_TRGM
    ON ARQ_TB01_ARQUIVOS USING gin (TX_TRANSCRICAO gin_trgm_ops);
-- "quais arquivos citam este CPF/placa/telefone": jsonb_path_ops é metade do tamanho do
-- gin_trgm_ops padrão e serve exatamente ao operador @> que a busca usa. Parcial porque só a
-- Nuvem IPED preenche a coluna — no UFDR ela é NULL na tabela inteira.
CREATE INDEX IF NOT EXISTS IX256_ARQ_TB01_IDENTIFICADORES
    ON ARQ_TB01_ARQUIVOS USING gin (TX_IDENTIFICADORES jsonb_path_ops)
    WHERE TX_IDENTIFICADORES IS NOT NULL;

-- casar o marcador do perito (que chega por id de item do caso, depois da carga em COPY) com
-- a linha já gravada, e resolver anexo→e-mail por parentId
CREATE INDEX IF NOT EXISTS IX259_ARQ_TB01_ID_ORIGEM ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, NU_ID_ORIGEM)
    WHERE NU_ID_ORIGEM IS NOT NULL;
-- "o que o perito marcou" e "o que o IPED sinalizou" são as duas primeiras perguntas da
-- triagem; sem índice parcial varrem centenas de milhares de linhas por extração
CREATE INDEX IF NOT EXISTS IX260_ARQ_TB01_MARCADORES ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_MARCADORES IS NOT NULL;
CREATE INDEX IF NOT EXISTS IX261_ARQ_TB01_ALERTA_HASH ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_ALERTA_HASH IS NOT NULL;
-- linha do tempo por extração (segmento "arquivo_iped" da timeline unificada)
CREATE INDEX IF NOT EXISTS IX262_ARQ_TB01_LINHA_TEMPO ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_LINHA_TEMPO IS NOT NULL;

CREATE TABLE ARQ_TB02_EMBEDDING_IMAGEM (
    ID_ARQUIVO    BIGINT NOT NULL,
    NO_MODELO     VARCHAR(120) NOT NULL,  -- modelo + versão do pré-processamento
    VT_EMBEDDING  BYTEA NOT NULL,  -- 512 floats float16 LE, normalizados
    DT_CALCULO    TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (ID_ARQUIVO, NO_MODELO),
    -- ON DELETE CASCADE: apagar uma extração já apaga ARQ_TB01_ARQUIVOS
    -- (investigacao_utils._apagar_anotacoes_de_extracao) e os vetores vão junto
    CONSTRAINT FK168_ARQ_TB02_ARQ_TB01 FOREIGN KEY (ID_ARQUIVO)
        REFERENCES ARQ_TB01_ARQUIVOS (ID_ARQUIVO) ON DELETE CASCADE
);

CREATE TABLE CATEGORIAS_IMAGEM (
    ID_CATEGORIA     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TERMO            VARCHAR(500) NOT NULL,
    ROTULO_EXIBICAO  VARCHAR(200) NOT NULL,
    IN_ATIVO         BOOLEAN NOT NULL DEFAULT TRUE,
    CRIADO_EM        TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO CATEGORIAS_IMAGEM (TERMO, ROTULO_EXIBICAO, IN_ATIVO) VALUES

-- =====================================================================
-- CATEGORIAS NEUTRAS (Fundamentais para absorver falsos positivos)
-- =====================================================================
('foto de animal, gato, cachorro ou pet',                                         'Animal / Pet',             TRUE),
('paisagem natural, planta, árvore, jardim ou céu',                               'Paisagem / Natureza',      TRUE),
('foto de objeto comum, móvel, ferramenta ou utensílio doméstico',                'Objeto Comum',             TRUE),
('comida, prato de refeição, bebida ou alimento',                                 'Alimento',                 TRUE),

-- =====================================================================
-- PESSOAS E IDENTIFICAÇÃO
-- =====================================================================
('rosto de pessoa, selfie ou foto de família',                                   'Pessoa',                   TRUE),
('documento de identidade, RG, CNH ou passaporte',                               'Documento de Identidade',   TRUE),

-- =====================================================================
-- ILÍCITOS E EVIDÊNCIAS (Prompts enxutos e diretos)
-- =====================================================================
('substância ilícita, droga embalada ou entorpecente',                            'Droga / Entorpecente',     TRUE),
('arma de fogo, revólver, pistola ou fuzil',                                      'Arma de Fogo',             TRUE),
('munição, cartucho ou estojo de munição',                                       'Munição',                  TRUE),
('faca, facão, canivete ou arma branca',                                         'Arma Branca',              TRUE),
('balança de precisão digital',                                                  'Balança de Precisão',      TRUE),

-- =====================================================================
-- VALORES E FINANÇAS
-- =====================================================================
('dinheiro em espécie, maço de notas ou cédulas',                                 'Dinheiro em Espécie',      TRUE),
('comprovante de transferência, pix ou extrato bancário',                        'Comprovante / PIX',        TRUE),
('cartão de crédito ou cartão bancário',                                         'Cartão Bancário',          TRUE),
('cheque bancário ou folha de cheque',                                           'Cheque',                   TRUE),

-- =====================================================================
-- DOCUMENTOS E TEXTOS
-- =====================================================================
('nota fiscal, cupom fiscal ou recibo de compra',                                'Nota Fiscal / Recibo',     TRUE),
('contrato impresso, folha de papel com texto ou documento assinado',            'Contrato / Documento',     TRUE),
('planilha, tabela financeira ou gráfico contábil',                              'Planilha / Tabela',        TRUE),

-- =====================================================================
-- TECNOLOGIA E MÍDIA
-- =====================================================================
('captura de tela de celular, print de mensagem ou aplicativo',                   'Print de Tela',            TRUE),
('smartphone, computador, notebook ou equipamento eletrônico',                   'Eletrônico',               TRUE),

-- =====================================================================
-- VEÍCULOS E LOCAIS
-- =====================================================================
('veículo, carro, moto, caminhão ou placa de veículo',                            'Veículo',                  TRUE),
('fachada de imóvel, prédio, casa ou rua',                                       'Imóvel / Fachada',         TRUE);

RESET search_path;
