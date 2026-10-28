-- ===================================================================================================================
-- Grafo de entidades, mesa de analise e padroes de crime
--
-- Tabelas: ENT_TB02_PROMPTS_BUSCA, ENT_TB04_BUSCAS_AUTOMATICAS, ENT_TB01_ENTIDADES, ENT_TB03_RELACOES,
--          ENT_TB05_CANONICAS, ENT_TB06_CANONICA_EVIDENCIAS, ENT_TB07_CANONICA_RELACOES,
--          ENT_TB08_CANONICA_IDENTIFICADORES, PDR_TB01_PADROES, PDR_TB02_EXECUCOES, PDR_TB03_DETECCOES
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 02 · prompts que guiam a busca automática de entidades (biblioteca global, mantida por admin)
CREATE TABLE ENT_TB02_PROMPTS_BUSCA (
    ID_PROMPT           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_TITULO           VARCHAR(3000) NOT NULL,
    TX_PROMPT           TEXT NOT NULL,
    IN_ATIVO            BOOLEAN NOT NULL DEFAULT true,
    DT_CADASTRO         TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR  BIGINT,
    TP_ESCOPO           VARCHAR(3000) NOT NULL DEFAULT 'investigacao',
    CONSTRAINT FK73_ENT_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT CK01_ENT_TB02_TP_ESCOPO CHECK (TP_ESCOPO IN ('investigacao', 'documento'))
);

-- 04 · uma linha por execução do botão "Buscar entidades automaticamente" — roda em
-- background (mesmo padrão de INQ_TB02_EXTRACOES), front-end faz polling do status
CREATE TABLE ENT_TB04_BUSCAS_AUTOMATICAS (
    ID_BUSCA              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO          BIGINT NOT NULL,
    ID_USUARIO            BIGINT,
    CD_STATUS             VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    TX_ERRO               TEXT,
    QT_ENTIDADES_CRIADAS  INTEGER,
    QT_RELACOES_CRIADAS   INTEGER,
    DT_INICIO             TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM                TIMESTAMP,
    -- tipo da busca automática: 'prompts' (varre os prompts ativos) ou
    -- 'relacionar_manuais' (foca em propor vínculos para entidades manuais soltas)
    TP_BUSCA              VARCHAR(3000) NOT NULL DEFAULT 'prompts',
    CONSTRAINT FK78_ENT_TB04_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK79_ENT_TB04_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX35_ENT_TB04_INQUERITO ON ENT_TB04_BUSCAS_AUTOMATICAS (ID_INQUERITO);

CREATE TABLE ENT_TB01_ENTIDADES (
    ID_ENTIDADE         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO        BIGINT NOT NULL,
    TP_ORIGEM           VARCHAR(30) NOT NULL,  -- wpp_mensagem | sms_mensagem | contato_agenda | contato_wpp | contato_sms | ligacao | localizacao | notificacao
    ID_ORIGEM           BIGINT NOT NULL,  -- PK da linha de origem na tabela correspondente a TP_ORIGEM
    TX_COMENTARIO       TEXT NOT NULL,
    DT_CRIACAO          TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR  BIGINT,
    -- entidade criada pela IA (busca automática) vs. manualmente pelo investigador (botão 📌)
    IN_AUTOMATICO       BOOLEAN NOT NULL DEFAULT false,
    -- ---------- 1/2 · revisão, proveniência e posição ----------
    CD_REVISAO          VARCHAR(3000) NOT NULL DEFAULT 'confirmada',  -- sugerida|confirmada|descartada
    ID_BUSCA            BIGINT,  -- busca automática que criou (nulo = manual)
    TX_JUSTIFICATIVA    TEXT,  -- evidência citada pela IA ao criar
    NU_POS_X            REAL,  -- posição fixada pelo investigador na mesa
    NU_POS_Y            REAL,
    CONSTRAINT FK65_ENT_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK70_ENT_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK95_ENT_TB01_ENT_TB04 FOREIGN KEY (ID_BUSCA)
        REFERENCES ENT_TB04_BUSCAS_AUTOMATICAS (ID_BUSCA)
);

CREATE INDEX IX28_ENT_TB01_INQUERITO ON ENT_TB01_ENTIDADES (ID_INQUERITO);
CREATE INDEX IX29_ENT_TB01_ORIGEM ON ENT_TB01_ENTIDADES (TP_ORIGEM, ID_ORIGEM);
CREATE INDEX IX44_ENT_TB01_BUSCA ON ENT_TB01_ENTIDADES (ID_BUSCA);

-- 03 · relação entre duas entidades (aresta do grafo) — TX_TIPO_RELACAO é texto livre
-- decidido pela IA (ex.: "mencionada em", "esteve em", "contatou"), sem taxonomia fixa
CREATE TABLE ENT_TB03_RELACOES (
    ID_RELACAO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO         BIGINT NOT NULL,
    ID_ENTIDADE_ORIGEM   BIGINT NOT NULL,
    ID_ENTIDADE_DESTINO  BIGINT NOT NULL,
    TX_TIPO_RELACAO      VARCHAR(3000) NOT NULL,
    IN_AUTOMATICO        BOOLEAN NOT NULL DEFAULT false,
    DT_CRIACAO           TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR   BIGINT,
    CD_REVISAO           VARCHAR(3000) NOT NULL DEFAULT 'confirmada',
    ID_BUSCA             BIGINT,
    TX_JUSTIFICATIVA     TEXT,
    CONSTRAINT FK74_ENT_TB03_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK75_ENT_TB03_ENT_TB01A FOREIGN KEY (ID_ENTIDADE_ORIGEM)
        REFERENCES ENT_TB01_ENTIDADES (ID_ENTIDADE),
    CONSTRAINT FK76_ENT_TB03_ENT_TB01B FOREIGN KEY (ID_ENTIDADE_DESTINO)
        REFERENCES ENT_TB01_ENTIDADES (ID_ENTIDADE),
    CONSTRAINT FK77_ENT_TB03_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK96_ENT_TB03_ENT_TB04 FOREIGN KEY (ID_BUSCA)
        REFERENCES ENT_TB04_BUSCAS_AUTOMATICAS (ID_BUSCA)
);

CREATE INDEX IX32_ENT_TB03_INQUERITO ON ENT_TB03_RELACOES (ID_INQUERITO);
CREATE INDEX IX33_ENT_TB03_ORIGEM ON ENT_TB03_RELACOES (ID_ENTIDADE_ORIGEM);
CREATE INDEX IX34_ENT_TB03_DESTINO ON ENT_TB03_RELACOES (ID_ENTIDADE_DESTINO);
CREATE INDEX IX45_ENT_TB03_BUSCA ON ENT_TB03_RELACOES (ID_BUSCA);

-- ---------- 3 · entidades canônicas ----------
-- 05 · a "coisa" investigada, independente de em quantos registros ela aparece.
-- NU_CHAVE é a chave de deduplicação do builder determinístico (telefone
-- normalizado, CPF...); nula em canônicas criadas manualmente sem chave natural.
CREATE TABLE ENT_TB05_CANONICAS (
    ID_CANONICA         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO        BIGINT NOT NULL,
    TP_ENTIDADE         VARCHAR(3000) NOT NULL,  -- pessoa|telefone|local|veiculo|organizacao|outro
    NO_ENTIDADE         VARCHAR(3000) NOT NULL,  -- nome de exibição
    NU_CHAVE            VARCHAR(3000),  -- chave de deduplicação (telefone/CPF/placa normalizado)
    TX_OBSERVACAO       TEXT,
    IN_AUTOMATICO       BOOLEAN NOT NULL DEFAULT false,
    ID_USUARIO_CRIADOR  BIGINT,
    DT_CRIACAO          TIMESTAMP NOT NULL DEFAULT now(),
    NU_POS_X            REAL,
    NU_POS_Y            REAL,
    CONSTRAINT FK97_ENT_TB05_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK98_ENT_TB05_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ04_ENT_TB05_CHAVE UNIQUE (ID_INQUERITO, TP_ENTIDADE, NU_CHAVE)
);

CREATE INDEX IX46_ENT_TB05_INQUERITO ON ENT_TB05_CANONICAS (ID_INQUERITO);

-- 06 · registro extraído que evidencia uma canônica (mesmo domínio TP_ORIGEM/ID_ORIGEM
-- de ENT_TB01; sem FK pela mesma razão — origem heterogênea, validada em código)
CREATE TABLE ENT_TB06_CANONICA_EVIDENCIAS (
    ID_EVIDENCIA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CANONICA   BIGINT NOT NULL,
    TP_ORIGEM     VARCHAR(3000) NOT NULL,
    ID_ORIGEM     BIGINT NOT NULL,
    TX_DETALHE    VARCHAR(3000),  -- de onde o vínculo veio ("mesmo telefone nas ligações", "associação manual"...)
    DT_CRIACAO    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK99_ENT_TB06_ENT_TB05 FOREIGN KEY (ID_CANONICA)
        REFERENCES ENT_TB05_CANONICAS (ID_CANONICA) ON DELETE CASCADE,
    CONSTRAINT UQ05_ENT_TB06_ORIGEM UNIQUE (ID_CANONICA, TP_ORIGEM, ID_ORIGEM)
);

CREATE INDEX IX47_ENT_TB06_CANONICA ON ENT_TB06_CANONICA_EVIDENCIAS (ID_CANONICA);
CREATE INDEX IX48_ENT_TB06_ORIGEM ON ENT_TB06_CANONICA_EVIDENCIAS (TP_ORIGEM, ID_ORIGEM);

-- 07 · relação canônica ↔ canônica (ex.: telefone "pertence a" pessoa). A UNIQUE
-- deixa o builder determinístico idempotente (rodar de novo não duplica aresta).
CREATE TABLE ENT_TB07_CANONICA_RELACOES (
    ID_RELACAO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO         BIGINT NOT NULL,
    ID_CANONICA_ORIGEM   BIGINT NOT NULL,
    ID_CANONICA_DESTINO  BIGINT NOT NULL,
    TX_TIPO_RELACAO      VARCHAR(3000) NOT NULL,
    IN_AUTOMATICO        BOOLEAN NOT NULL DEFAULT false,
    ID_USUARIO_CRIADOR   BIGINT,
    DT_CRIACAO           TIMESTAMP NOT NULL DEFAULT now(),
    TX_DETALHE           VARCHAR(500),
    CONSTRAINT FK100_ENT_TB07_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK101_ENT_TB07_ENT_TB05A FOREIGN KEY (ID_CANONICA_ORIGEM)
        REFERENCES ENT_TB05_CANONICAS (ID_CANONICA) ON DELETE CASCADE,
    CONSTRAINT FK102_ENT_TB07_ENT_TB05B FOREIGN KEY (ID_CANONICA_DESTINO)
        REFERENCES ENT_TB05_CANONICAS (ID_CANONICA) ON DELETE CASCADE,
    CONSTRAINT FK103_ENT_TB07_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ06_ENT_TB07_PAR UNIQUE (ID_CANONICA_ORIGEM, ID_CANONICA_DESTINO, TX_TIPO_RELACAO)
);

CREATE INDEX IX49_ENT_TB07_INQUERITO ON ENT_TB07_CANONICA_RELACOES (ID_INQUERITO);

CREATE TABLE ENT_TB08_CANONICA_IDENTIFICADORES (
    ID_IDENTIFICADOR  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_CANONICA       BIGINT NOT NULL,
    ID_INQUERITO      BIGINT NOT NULL,  -- desnormalizado: a busca por identificador é sempre dentro do inquérito
    TP_IDENTIFICADOR  VARCHAR(20) NOT NULL,  -- telefone|cpf|email|usuario_app|nome
    CO_VALOR          VARCHAR(300) NOT NULL,  -- valor normalizado (só dígitos / minúsculo / sem acento)
    TX_ORIGEM         VARCHAR(300),  -- de onde veio ("contato de WhatsApp", "chave PIX citada na conversa"...)
    DT_CRIACAO        TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK156_ENT_TB08_ENT_TB05 FOREIGN KEY (ID_CANONICA)
        REFERENCES ENT_TB05_CANONICAS (ID_CANONICA) ON DELETE CASCADE,
    CONSTRAINT FK157_ENT_TB08_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT UQ08_ENT_TB08_VALOR UNIQUE (ID_CANONICA, TP_IDENTIFICADOR, CO_VALOR)
);

CREATE INDEX IX99_ENT_TB08_BUSCA
    ON ENT_TB08_CANONICA_IDENTIFICADORES (ID_INQUERITO, TP_IDENTIFICADOR, CO_VALOR);
CREATE INDEX IX142_ENT_TB08_NOME_TRGM ON ENT_TB08_CANONICA_IDENTIFICADORES
    USING gin (CO_VALOR gin_trgm_ops) WHERE TP_IDENTIFICADOR = 'nome';

-- 09 · foto do nó da mesa (a imagem que o investigador põe NO LUGAR da bolinha do grafo:
-- retrato do suspeito, foto do veículo, do local). Vale para as duas camadas de nó, então o
-- alvo é polimórfico (TP_NO/ID_NO), como TP_ORIGEM/ID_ORIGEM em ENT_TB01 — e pela mesma razão
-- não tem FK para ele: 'canonica' aponta ENT_TB05 e 'entidade' aponta ENT_TB01. Quem apaga o
-- nó apaga a imagem junto (cascata manual em canonicas_utils/entidades_utils).
--
-- O binário mora no banco, e não em disco, porque é pequeno e derivado: a imagem é reduzida a
-- LADO_MAX px e reencodada em JPEG na entrada (funcoes/imagens_mesa.py), o que deixa cada foto
-- na casa das dezenas de KB. Guardar em disco custaria um caminho novo para gerenciar, backup
-- separado do resto do inquérito e órfãos a cada exclusão.
CREATE TABLE ENT_TB09_IMAGENS (
    ID_IMAGEM     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    TP_NO         VARCHAR(20) NOT NULL,   -- canonica|entidade (as duas camadas de nó da mesa)
    ID_NO         BIGINT NOT NULL,
    NO_ARQUIVO    VARCHAR(300),           -- nome do arquivo enviado, só para exibir na gaveta
    TP_MIME       VARCHAR(60) NOT NULL DEFAULT 'image/jpeg',
    NU_BYTES      INTEGER NOT NULL,
    IM_CONTEUDO   BYTEA NOT NULL,         -- a imagem já reduzida/reencodada, não o original
    ID_USUARIO    BIGINT,
    DT_ENVIO      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK258_ENT_TB09_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK259_ENT_TB09_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT UQ10_ENT_TB09_NO UNIQUE (TP_NO, ID_NO),
    CONSTRAINT CK12_ENT_TB09_TP_NO CHECK (TP_NO IN ('canonica', 'entidade'))
);

-- a mesa pergunta "quais nós deste inquérito têm foto?" a cada carga do grafo
CREATE INDEX IX249_ENT_TB09_INQUERITO ON ENT_TB09_IMAGENS (ID_INQUERITO);

-- ---------- 4 · padrões de crime ----------
-- 01 · biblioteca global de padrões (como ENT_TB02_PROMPTS_BUSCA: mantida por admin,
-- executada contra qualquer inquérito). TX_DEFINICAO é JSONB porque cada tipo tem
-- parâmetros próprios:
--   lexico   → {"termos": ["farinha", "pó", ...]}
--   regex    → {"padrao": "\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}"}
--   temporal → {"subtipo": "madrugada", "hora_inicio": 0, "hora_fim": 5, "minimo": 10}
--              {"subtipo": "rajada", "janela_minutos": 10, "minimo_mensagens": 20}
CREATE TABLE PDR_TB01_PADROES (
    ID_PADRAO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_PADRAO           VARCHAR(150) NOT NULL,
    TP_PADRAO           VARCHAR(20) NOT NULL,  -- lexico|regex|temporal
    NO_CATEGORIA        VARCHAR(80),  -- ex.: tráfico, financeiro, armas
    DS_PADRAO           VARCHAR(500),
    TX_DEFINICAO        JSONB NOT NULL,
    IN_ATIVO            BOOLEAN NOT NULL DEFAULT true,
    DT_CADASTRO         TIMESTAMP NOT NULL DEFAULT now(),
    ID_USUARIO_CRIADOR  BIGINT,
    CONSTRAINT FK104_PDR_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO_CRIADOR)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

-- 02 · uma linha por execução do botão "Detectar padrões" na Mesa — mesmo padrão
-- de job em background com polling de ENT_TB04/INQ_TB02
CREATE TABLE PDR_TB02_EXECUCOES (
    ID_EXECUCAO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO  BIGINT NOT NULL,
    ID_USUARIO    BIGINT,
    CD_STATUS     VARCHAR(20) NOT NULL DEFAULT 'pendente',  -- pendente|processando|concluido|erro
    TX_ERRO       TEXT,
    QT_DETECCOES  INTEGER,
    DT_INICIO     TIMESTAMP NOT NULL DEFAULT now(),
    DT_FIM        TIMESTAMP,
    CONSTRAINT FK105_PDR_TB02_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK106_PDR_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX50_PDR_TB02_INQUERITO ON PDR_TB02_EXECUCOES (ID_INQUERITO);

-- Guard de execução única por inquérito (ver db/migracoes/0055_execucoes_unicas.sql).
CREATE UNIQUE INDEX UX116_PDR_TB02_ATIVA ON PDR_TB02_EXECUCOES (ID_INQUERITO)
    WHERE CD_STATUS IN ('pendente', 'processando');

-- 03 · achados de uma execução. TP_ORIGEM/ID_ORIGEM apontam o registro que disparou
-- o padrão (mesmo domínio de ENT_TB01 — dali o investigador cria uma entidade).
CREATE TABLE PDR_TB03_DETECCOES (
    ID_DETECCAO    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXECUCAO    BIGINT NOT NULL,
    ID_INQUERITO   BIGINT NOT NULL,
    ID_PADRAO      BIGINT NOT NULL,
    TP_ORIGEM      VARCHAR(30) NOT NULL,
    ID_ORIGEM      BIGINT NOT NULL,
    TX_TRECHO      TEXT,  -- trecho/descrição do achado
    DT_OCORRENCIA  TIMESTAMP,  -- data do registro de origem
    CONSTRAINT FK107_PDR_TB03_PDR_TB02 FOREIGN KEY (ID_EXECUCAO)
        REFERENCES PDR_TB02_EXECUCOES (ID_EXECUCAO) ON DELETE CASCADE,
    CONSTRAINT FK108_PDR_TB03_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK109_PDR_TB03_PDR_TB01 FOREIGN KEY (ID_PADRAO)
        REFERENCES PDR_TB01_PADROES (ID_PADRAO) ON DELETE CASCADE
);

CREATE INDEX IX51_PDR_TB03_EXECUCAO ON PDR_TB03_DETECCOES (ID_EXECUCAO);
CREATE INDEX IX52_PDR_TB03_INQUERITO ON PDR_TB03_DETECCOES (ID_INQUERITO);

-- padrões de partida (edite/desative na tela "Padrões de crime")
INSERT INTO PDR_TB01_PADROES (NO_PADRAO, TP_PADRAO, NO_CATEGORIA, DS_PADRAO, TX_DEFINICAO) VALUES
    ('Gíria de tráfico', 'lexico', 'tráfico',
     'Termos comuns em negociação de entorpecentes em mensagens e transcrições de áudio.',
     '{"termos": ["maconha", "cocaína", "farinha", "pó", "pedra", "bala", "erva", "verdinha", "50 conto o grama", "fecha o pedido", "corre", "biqueira", "vapor", "mula"]}'),
    ('Menção a arma de fogo', 'lexico', 'armas',
     'Termos ligados a armas de fogo e munição.',
     '{"termos": ["pistola", "revólver", "calibre", "munição", "38", "9mm", "12 de repetição", "ferro", "berro", "blicka"]}'),
    ('CPF citado em mensagem', 'regex', 'financeiro',
     'CPF formatado (000.000.000-00) em mensagens — costuma indicar repasse de dados para pagamento ou fraude.',
     '{"padrao": "\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}"}'),
    ('Placa de veículo', 'regex', 'veículos',
     'Placa brasileira (padrão antigo AAA-9999 ou Mercosul AAA9A99) citada em mensagem.',
     '{"padrao": "[A-Za-z]{3}-?\\d[A-Za-z0-9]\\d{2}"}'),
    ('Valor em dinheiro', 'regex', 'financeiro',
     'Valores monetários (R$ 1.500, 2500 reais, 3 mil) citados em mensagens.',
     '{"padrao": "(R\\$ ?\\d[\\d\\.,]*)|(\\d+ ?(mil|reais|conto))"}'),
    ('Atividade na madrugada', 'temporal', 'comportamento',
     'Conversas com volume alto de mensagens entre 0h e 5h — padrão comum em atividade ilícita.',
     '{"subtipo": "madrugada", "hora_inicio": 0, "hora_fim": 5, "minimo": 10}'),
    ('Rajada de mensagens', 'temporal', 'comportamento',
     'Muitas mensagens na mesma conversa em poucos minutos — negociação intensa ou coordenação de ação.',
     '{"subtipo": "rajada", "janela_minutos": 10, "minimo_mensagens": 20}');

INSERT INTO ENT_TB02_PROMPTS_BUSCA (NO_TITULO, TX_PROMPT, TP_ESCOPO) VALUES

-- ---------- Escopo INVESTIGACAO — tipos penais ----------
('Tráfico de drogas',
 $$Procure em mensagens de WhatsApp/SMS e nas transcrições de áudio indícios de tráfico ou uso de drogas: negociação, compra/venda, entrega, quantidades e preços. Vá além da palavra formal — use sinônimos e gírias (pó, branca, farinha, pedra, crack, verde, erva, beck, bagulho, mercadoria, material, bala, doce, K; 'grama', 'bucha', 'trouxinha') e a busca semântica para linguagem cifrada. Crie uma entidade para cada mensagem relevante, citando o trecho e a data na justificativa.$$,
 'investigacao'),

('Corrupção e propina',
 $$Procure indícios de corrupção e propina: pagamento ou pedido de vantagem indevida a agente público, 'caixa 2', 'mesada', 'acerto', 'por fora', 'ajuda de custo' irregular, favorecimento em licitação ou contrato, 'comissão' por dentro. Use gírias e a busca semântica. Crie uma entidade para cada mensagem/áudio relevante, com o trecho, o valor (se houver) e a data.$$,
 'investigacao'),

('Crimes eleitorais',
 $$Procure indícios de crimes eleitorais: compra de votos ('voto por dinheiro/cesta básica/combustível'), 'boca de urna', caixa 2 de campanha, transporte irregular de eleitores, coação, uso da máquina pública e distribuição de material em troca de voto. Quando aparecer um nome de candidato, cruze com a base do TSE. Crie uma entidade para cada indício relevante, com o trecho e a data.$$,
 'investigacao'),

('Moeda falsa',
 $$Procure indícios de moeda falsa: fabricação, guarda ou repasse de cédulas falsas ('nota falsa', 'dinheiro carimbado', 'cédula', 'nota fria', 'passar nota') e falsificação de valores. Use a busca semântica para variações. Crie uma entidade para cada mensagem/áudio relevante, com o trecho e a data.$$,
 'investigacao'),

('Estelionato e fraude',
 $$Procure golpes e fraudes: falsa venda, cobrança indevida, uso de dados ou cartão de terceiros, pix falso, contas ou compras em nome de outra pessoa, falso empréstimo. Crie uma entidade para cada indício relevante, com o trecho e a data.$$,
 'investigacao'),

('Contrabando e descaminho (produtos importados proibidos)',
 $$Procure indícios de contrabando e descaminho: venda de produtos importados proibidos ou sem nota (cigarro paraguaio, réplicas, eletrônicos/celulares 'sem procedência', 'trazido do Paraguai/Uruguai', 'muamba', 'produto sem nota', itens de importação proibida). Crie uma entidade para cada mensagem/áudio relevante, com o produto, o trecho e a data.$$,
 'investigacao'),

-- ---------- Escopo INVESTIGACAO — apoio (amplificam a detecção acima) ----------
('Movimentação financeira suspeita e lavagem',
 $$Procure movimentações de dinheiro suspeitas: transferências, depósitos, pix, valores altos ou fracionados, cobrança de dívidas, 'lavar'/'esquentar' dinheiro. Inclua gírias de valor (pila, conto, grana, 'x paus', 'x mil', troco). Crie uma entidade para cada mensagem/áudio com valor ou transação relevante, citando o trecho, o valor e a data.$$,
 'investigacao'),

('Armas de fogo e munição',
 $$Procure menções a armas de fogo e munição: compra, venda, guarda, transporte ou uso. Use gírias e variações (ferro, peça, brinquedo, 'aparelho', ponto/calibre, doze; munição, cápsula, projétil) e a busca semântica. Crie uma entidade para cada mensagem/áudio relevante, com o trecho e a data.$$,
 'investigacao'),

('Pessoas, apelidos e identidades falsas',
 $$Identifique pessoas citadas (nomes e apelidos) e casos em que o dono do aparelho usa nomes ou identidades diferentes com interlocutores distintos. Se um contato já tiver pessoa associada, use-a. Crie uma entidade para cada pessoa/apelido relevante e, havendo vínculo claro, crie a relação.$$,
 'investigacao'),

('Encontros, entregas e logística',
 $$Procure combinações de encontro e entrega: marcação de local/horário, 'tô a caminho', 'já passei pro entregador', pontos de referência e endereços. Cruze com as localizações do aparelho quando fizer sentido. Crie uma entidade para cada combinação relevante e relacione com o local/pessoa envolvidos.$$,
 'investigacao'),

('Organização criminosa e hierarquia',
 $$Procure indícios de atuação em grupo: ordens, subordinação, menção a 'patrão'/chefe, divisão de tarefas, 'o corre', cobrança de acerto, repasses. Crie uma entidade para cada indício relevante e relacione as pessoas envolvidas (quem manda, quem executa).$$,
 'investigacao'),

-- ---------- Escopo DOCUMENTO (extraem entidades que servem a todos os crimes) ----------
('Pessoas e documentos de identificação',
 $$Nos documentos do caso, identifique pessoas citadas e seus identificadores (nome completo, CPF, RG, data de nascimento, filiação). Crie uma entidade para cada pessoa relevante, citando onde aparece.$$,
 'documento'),

('Contas bancárias e movimentações financeiras',
 $$Nos documentos (extratos, planilhas, relatórios), identifique contas bancárias, agências, titulares e movimentações: depósitos, saques, transferências, pix, valores altos ou fracionados. Crie uma entidade para cada conta/movimentação relevante e relacione com o titular quando identificável.$$,
 'documento'),

('Empresas e CNPJ',
 $$Identifique empresas mencionadas (razão social, nome fantasia, CNPJ) e o papel delas no caso (pagadora, recebedora, fornecedora, de fachada). Crie uma entidade para cada empresa relevante e relacione com pessoas/contas envolvidas.$$,
 'documento'),

('Valores e indícios de lavagem',
 $$Nos documentos, destaque valores expressivos, transações incompatíveis com a renda, fracionamento e fluxos entre contas/pessoas que sugiram lavagem de dinheiro. Crie uma entidade para cada indício relevante, citando o valor e a origem no documento.$$,
 'documento'),

('Endereços, imóveis e veículos',
 $$Identifique endereços, imóveis e veículos (placas, modelos) citados nos documentos. Crie uma entidade para cada um relevante e relacione com a pessoa/empresa associada.$$,
 'documento'),

('Telefones e contatos',
 $$Identifique números de telefone, e-mails e outros contatos nos documentos, e a quem pertencem. Crie uma entidade para cada contato relevante e relacione com a pessoa/empresa dona.$$,
 'documento'),

('Cronologia de fatos relevantes',
 $$Extraia datas e fatos relevantes dos documentos (transações, reuniões, ocorrências) que ajudem a montar a linha do tempo do caso. Crie uma entidade para cada fato datado relevante.$$,
 'documento'),

('Vínculos entre pessoas e empresas',
 $$A partir dos documentos, identifique vínculos entre pessoas e empresas (sócio, procurador, beneficiário, pagamentos recorrentes) e crie relações entre as entidades correspondentes, citando a evidência no documento.$$,
 'documento');

INSERT INTO PDR_TB01_PADROES (NO_PADRAO, TP_PADRAO, NO_CATEGORIA, DS_PADRAO, TX_DEFINICAO) VALUES
    ('Corrupção e propina', 'lexico', 'corrupção',
     'Termos ligados a propina, vantagem indevida e caixa 2.',
     '{"termos": ["propina", "propininha", "caixa 2", "caixa dois", "por fora", "por baixo do pano", "mesada", "molhar a mão", "acerto por fora", "vantagem", "suborno", "achaque", "comissão por fora"]}'),

    ('Crimes eleitorais', 'lexico', 'eleitoral',
     'Termos ligados a compra de votos e ilícitos de campanha.',
     '{"termos": ["compra de voto", "comprar voto", "voto por", "boca de urna", "cabo eleitoral", "curral eleitoral", "santinho", "transporte de eleitor", "caixa 2 de campanha", "cesta pelo voto", "troca por voto"]}'),

    ('Moeda falsa', 'lexico', 'moeda falsa',
     'Termos ligados a cédulas falsas e falsificação de dinheiro.',
     '{"termos": ["nota falsa", "notas falsas", "cédula falsa", "dinheiro falso", "dinheiro carimbado", "nota carimbada", "nota fria", "passar nota falsa", "falsificar dinheiro", "cédula fria"]}'),

    ('Estelionato e golpe', 'lexico', 'estelionato',
     'Termos ligados a golpes, fraudes e uso de dados/contas de terceiros.',
     '{"termos": ["pix falso", "golpe do pix", "caiu no golpe", "cartão clonado", "clonei o cartão", "conta laranja", "falsa venda", "estelionato", "171", "boleto falso", "falso empréstimo"]}'),

    ('Contrabando e descaminho', 'lexico', 'contrabando',
     'Termos ligados a produtos importados proibidos, muamba e mercadoria sem nota.',
     '{"termos": ["muamba", "sem nota", "produto sem nota", "cigarro paraguaio", "trazido do paraguai", "do paraguai", "descaminho", "contrabando", "sem procedência", "réplica", "importado sem nota"]}');

INSERT INTO PDR_TB01_PADROES (NO_PADRAO, TP_PADRAO, NO_CATEGORIA, DS_PADRAO, TX_DEFINICAO) VALUES
    ('Mesmo IMEI, donos diferentes', 'estrutural', 'dispositivos',
     'Dois donos de aparelho distintos, no mesmo inquérito, com o mesmo IMEI — aparelho repassado ou compartilhado.',
     '{"subtipo": "imei_compartilhado"}'),
    ('Triangulação financeira (A→B→C→A)', 'estrutural', 'financeiro',
     'Ciclo de pagamentos entre três pessoas identificadas (SIMBA/RIF) fechando no ponto de partida.',
     '{"subtipo": "triangulacao_financeira"}');

RESET search_path;
