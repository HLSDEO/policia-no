-- ===================================================================================================================
-- Extracao de aparelho (.ufdr) — cabecalho e progresso
--
-- Tabelas: INQ_TB02_EXTRACOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 02 · uma linha por arquivo .ufdr processado
CREATE TABLE INQ_TB02_EXTRACOES (
    ID_EXTRACAO                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO                  BIGINT NOT NULL,
    NO_ARQUIVO                    VARCHAR(3000) NOT NULL,  -- caminho do .ufdr relativo a data/ (ex.: "FICCO-ES/2025.0079407/CELULAR/Relatorio.ufdr") — ver docs/organizacao_arquivos.md
    NO_DISPOSITIVO                VARCHAR(3000),  -- ex.: SM-A032M; em extração de nuvem, a conta (e-mail/label)
    NO_FABRICANTE                 VARCHAR(3000),  -- ex.: samsung
    IN_NUVEM                      BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE = extração de conta na nuvem (UFDR "Google Warrant Return"), não de aparelho
    DT_EXTRACAO                   TIMESTAMP,  -- data da extração no aparelho (relatório Cellebrite)
    DT_UPLOAD                     TIMESTAMP NOT NULL DEFAULT now(),
    CD_STATUS                     VARCHAR(3000) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    TX_ERRO                       TEXT,
    TX_AVISO                      TEXT,  -- concluída, mas algo ficou por fazer (modelo de transcrição/embeddings não baixado)
    QT_CONVERSAS                  INTEGER,
    QT_MENSAGENS                  INTEGER,
    -- contagens dos novos domínios, ao lado de qt_conversas/qt_mensagens (WhatsApp) já existentes
    QT_CONTATOS                   BIGINT,
    QT_LIGACOES                   BIGINT,
    QT_LOCALIZACOES               BIGINT,
    QT_NOTIFICACOES               BIGINT,
    QT_SMS_CONVERSAS              BIGINT,
    QT_SMS_MENSAGENS              BIGINT,
    QT_AUDIOS_TRANSCRITOS         BIGINT,
    ID_USUARIO                    BIGINT,
    -- contagens dos novos domínios, ao lado das já existentes em INQ_TB02_EXTRACOES
    QT_NAVEGACAO                  BIGINT,
    QT_APLICATIVOS                BIGINT,
    QT_DONOS                      BIGINT,
    DT_INICIO_PROCESSAMENTO       TIMESTAMP,  -- início do parsing (status -> processando)
    DT_FIM_PROCESSAMENTO          TIMESTAMP,  -- fim do parsing (status -> concluido/erro)
    -- contagens agregadas por extração, para a linha de resumo do card (paridade com QT_CONVERSAS etc.)
    QT_MSG_CONVERSAS              BIGINT,
    QT_MSG_MENSAGENS              BIGINT,
    CD_FASE                       VARCHAR(30),
    -- contagem do novo domínio, ao lado das já existentes em INQ_TB02_EXTRACOES
    QT_ARQUIVOS                   BIGINT,
    -- QT_CHAVES_PIX: NULL = extração ainda não escaneada; >= 0 = já escaneada (evita rodar de novo à toa)
    QT_CHAVES_PIX                 BIGINT,
    QT_EMAILS                     BIGINT,
    QT_CONTAS_FINANCEIRAS         BIGINT,
    FL_CANCELAR                   BOOLEAN NOT NULL DEFAULT false,
    TX_MODELO_TRANSCRICAO         VARCHAR(3000),
    -- QT_REDES_WIFI: NULL = extração ainda não escaneada; >= 0 = já escaneada
    QT_REDES_WIFI                 BIGINT,
    QT_NOTAS                      BIGINT,
    QT_SENHAS                     BIGINT,
    QT_AUTOFILL                   BIGINT,
    -- NULL = extração ainda não passou pela identificação (extrações antigas);
    -- >= 0 = já passou, e o número é quantas pessoas ela reconheceu.
    QT_PESSOAS_IDENTIFICADAS      BIGINT,
    QT_PROGRESSO_FASE             SMALLINT,
    -- Transcrever também o áudio dos vídeos (escolha do usuário no formulário de extração).
    IN_TRANSCREVER_VIDEO          BOOLEAN NOT NULL DEFAULT FALSE,
    CO_HASH_MD5                   CHAR(32),
    CO_HASH_SHA1                  CHAR(40),
    CO_HASH_SHA256                CHAR(64),
    -- Contadores por domínio, ao lado dos já existentes.
    -- NULL = extração antiga, carregada antes destes domínios existirem
    -- (a tela usa isso para não mostrar "0" onde na verdade não foi lido).
    QT_USO_APPS                   BIGINT,
    QT_DISPOSITIVOS_RECONHECIDOS  BIGINT,
    QT_CONEXOES_DISPOSITIVO       BIGINT,
    QT_COOKIES                    BIGINT,
    QT_ATIVIDADE_SOCIAL           BIGINT,
    QT_BUSCAS                     BIGINT,
    QT_ENVIOS_NUVEM               BIGINT,
    QT_CALENDARIO                 BIGINT,
    QT_USO_APLICATIVO             BIGINT,
    QT_TRAJETOS                   BIGINT,
    QT_INFO_APARELHO              BIGINT,
    QT_ANTENAS                    BIGINT,
    QT_VOICEMAIL                  BIGINT,
    QT_FAVORITOS                  BIGINT,
    QT_DOWNLOADS                  BIGINT,
    QT_TRANSFERENCIAS             BIGINT,
    QT_CARTOES_CARTEIRA           BIGINT,
    QT_GRAVACOES                  BIGINT,
    QT_CARTOES_CREDITO            BIGINT,
    QT_USO_REDE                   BIGINT,
    -- contagens dos produtos de nuvem (UFDR "Google Warrant Return"): só preenchidas quando
    -- IN_NUVEM = TRUE. Não têm telas dedicadas no card do aparelho — aparecem no card de nuvem.
    QT_ATIVIDADES                 BIGINT,
    QT_EVENTOS                    BIGINT,
    QT_ACESSOS                    BIGINT,
    QT_DISPOSITIVOS               BIGINT,
    QT_PERFIL                     BIGINT,
    QT_METADADOS_NUVEM            BIGINT,
    DT_ULTIMA_ATIVIDADE           timestamp,
    -- PROVA COMPARTILHADA (docs/compartilhar_prova.md): esta extração não veio de um .ufdr
    -- importado aqui — é a cópia de uma extração de OUTRO inquérito, feita pelo menu
    -- "Compartilhar prova". NO_ARQUIVO continua apontando para o .ufdr da origem (o arquivo
    -- não é duplicado no disco) e os dados foram copiados linha a linha.
    --
    -- ID_EXTRACAO_ORIGEM é de propósito SEM foreign key: a extração de origem pode ser apagada
    -- depois (a cópia é independente dela), e uma FK impediria esse DELETE. NU_INQUERITO_ORIGEM
    -- guarda o número do inquérito de onde a prova veio, para a tela continuar dizendo isso
    -- mesmo depois que a origem sumir. NULL nas três = extração importada normalmente.
    ID_EXTRACAO_ORIGEM            BIGINT,
    NU_INQUERITO_ORIGEM           VARCHAR(3000),
    DT_COMPARTILHAMENTO           TIMESTAMP,
    -- Apelido livre dado pelo investigador no menu "Mais ações" do bloco
    -- (ex.: "Celular do João") — só identificação no card, ver 0031_extracao_apelido.sql.
    NO_APELIDO                    VARCHAR(200),
    -- Nó descentralizado que SERVE esta extração (ver 46_nos.sql e docs/no_descentralizado.md).
    -- NULL = extração do central: o arquivo está no data() do central e tudo roda aqui, como
    -- sempre. Não-nulo = a evidência mora na pasta data/ do nó: o hash é calculado lá, o .ufdr é
    -- lido por faixas via HTTP (Range), e o arquivo não é movido para a pasta do inquérito no
    -- central — ele fica no nó. É snapshot de propósito: trocar o nó da delegacia depois não
    -- reaponta extrações antigas, que continuam apontando para onde o arquivo de fato está.
    ID_SERVIDOR                   BIGINT,
    -- Geração do despacho de entrega (ver 0059_geracao_entrega.sql): cada claim do despachante
    -- sobe; o nó devolve o número em cada lote/estado e o central recusa a passada de uma
    -- geração substituída pelo redespacho do watchdog. Irrelevante em extração local.
    NU_GERACAO                    INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT FK37_INQ_TB02_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK67_INQ_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
    -- ID_SERVIDOR fica SEM foreign key, como ID_EXTRACAO_ORIGEM logo acima: apagar um nó
    -- inativado não pode ser bloqueado pelo histórico de extrações, e a 46_nos.sql nasce DEPOIS
    -- desta — uma FK apontando para ela quebraria o init em ordem numérica.
);

CREATE INDEX IX18_INQ_TB02_ID_INQUERITO ON INQ_TB02_EXTRACOES (ID_INQUERITO);

-- Índice da pergunta "de quem é este .ufdr?" (no_utils.servidor_por_arquivo), feita quando o
-- leitor de mídia não acha o arquivo no disco do central. Parcial de propósito: só extrações
-- de nó têm ID_SERVIDOR — são uma fração pequena da tabela, e NO_ARQUIVO (VARCHAR 3000) não
-- entra no índice para não esbarrar no limite de tamanho de chave do btree.
CREATE INDEX IX420_INQ_TB02_ID_SERVIDOR ON INQ_TB02_EXTRACOES (ID_SERVIDOR)
    WHERE ID_SERVIDOR IS NOT NULL;

RESET search_path;
