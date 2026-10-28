-- ===================================================================================================================
-- Configuração de IA por usuário e por unidade/delegacia
-- (fallback: quem não tem linha aplicável usa a das Configurações / .env)
--
-- Tabelas: USR_TB06_CONFIG_IA, CONFIG_IA_ENTIDADE
--
-- A tela Configurações grava provedor/modelo/URL/chave no .env, e até aqui isso valia para TODO
-- mundo. Esta tabela é a exceção por usuário: o administrador abre "IA do usuário" na tela
-- Usuários e define, só para aquele analista, outro provedor/modelo/chave — típico quando cada um
-- tem a sua cota no gateway. Sem linha (ou com IN_ATIVA = false) nada muda: cai no .env, que
-- continua sendo o padrão da instalação.
--
-- A chave de API fica em texto claro na coluna, do mesmo jeito que já fica no .env — quem tem o
-- banco tem as chaves de qualquer forma. A API nunca devolve a chave: a tela recebe só a máscara
-- (mask_key), igual às Configurações.
-- ===================================================================================================================

SET search_path TO SEGURANCA, PUBLIC;

CREATE TABLE USR_TB06_CONFIG_IA (
    -- PK = FK: é no máximo UMA configuração por usuário
    ID_USUARIO             BIGINT PRIMARY KEY,
    -- Desligar sem apagar: o admin volta o usuário para o padrão mantendo o que estava preenchido
    IN_ATIVA               BOOLEAN NOT NULL DEFAULT true,
    CO_PROVEDOR            VARCHAR(30) NOT NULL,
    NO_MODELO              VARCHAR(200) NOT NULL,
    TX_API_URL             VARCHAR(500) NOT NULL,
    -- Vazio é legítimo: Ollama e LM Studio locais não pedem chave
    TX_API_KEY             TEXT,
    -- PEM em base64, mesmo formato de AI_CA_CERT no .env (ver funcoes/cert_utils.py) — só o
    -- provedor openai-cert usa
    TX_CA_CERT             TEXT,
    CO_ESFORCO_RACIOCINIO  VARCHAR(10),
    -- num_ctx do Ollama; NULL = padrão do servidor
    QT_CONTEXTO            INTEGER,
    -- Quem configurou (é sempre um administrador) — a trilha de "por que este usuário usa outro
    -- modelo" começa aqui
    ID_USUARIO_AUTOR       BIGINT,
    DT_CRIACAO             TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO         TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK248_USR_TB06_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO),
    CONSTRAINT FK249_USR_TB06_AUTOR FOREIGN KEY (ID_USUARIO_AUTOR)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX239_USR_TB06_AUTOR ON USR_TB06_CONFIG_IA (ID_USUARIO_AUTOR);

-- IA própria das UNIDADES e DELEGACIAS — o elo do meio da hierarquia de conexão de IA
-- (ver docs/unidades_prompts.md): .env (sistema) → unidade superior (raiz→folha) → delegacia →
-- usuário (USR_TB06 acima). Mesma forma da config por usuário, com uma regra a mais por ser
-- hierarquia: uma linha vencedora INTEIRA (o primeiro nível mais específico ativo vence o
-- nível todo — nunca mistura "provedor da delegacia com a chave do usuário", que é o tipo de
-- frankenstein impossível de depurar quando a IA de uma unidade inteira para de responder).
-- Sem linha em nenhum nível = .env, como sempre.
-- ID_ENTIDADE é polimórfico (UNIDADES ou DELEGACIAS por TP_NIVEL — mesma escolha do PROMPTS_IA:
-- sem FK, existência conferida na rota e limpeza na exclusão da entidade).
CREATE TABLE CONFIG_IA_ENTIDADE (
    ID_CONFIG             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_NIVEL              VARCHAR(10) NOT NULL,
    ID_ENTIDADE           BIGINT NOT NULL,
    -- Desligar sem apagar: a unidade volta a herdar o nível de cima mantendo o que estava gravado
    IN_ATIVA              BOOLEAN NOT NULL DEFAULT true,
    CO_PROVEDOR           VARCHAR(30) NOT NULL,
    NO_MODELO             VARCHAR(200) NOT NULL,
    TX_API_URL            VARCHAR(500) NOT NULL,
    -- Vazio é legítimo: Ollama e LM Studio locais não pedem chave
    TX_API_KEY            TEXT,
    -- PEM em base64 (funcoes/cert_utils.py) — só o provedor openai-cert usa
    TX_CA_CERT            TEXT,
    CO_ESFORCO_RACIOCINIO VARCHAR(10),
    -- num_ctx do Ollama; NULL = padrão do servidor
    QT_CONTEXTO           INTEGER,
    ID_USUARIO_AUTOR      BIGINT,
    DT_CRIACAO            TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO        TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT CK34_CONFIG_IA_ENT_NIVEL CHECK (TP_NIVEL IN ('unidade', 'delegacia')),
    CONSTRAINT FK424_CONFIG_IA_ENT_AUTOR FOREIGN KEY (ID_USUARIO_AUTOR)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

-- Uma linha por nível+entidade (o CRUD faz update-then-insert; UX114 é a rede de segurança
-- contra dois admins salvando ao mesmo tempo — só linhas ativas conflituam).
CREATE UNIQUE INDEX UX114_CONFIG_IA_ENT_ATIVA
    ON CONFIG_IA_ENTIDADE (TP_NIVEL, ID_ENTIDADE)
    WHERE IN_ATIVA;
-- resolução do turno (a cadeia do usuário busca unidade+delegacia de uma vez)
CREATE INDEX IX432_CONFIG_IA_ENT_ENTIDADE ON CONFIG_IA_ENTIDADE (TP_NIVEL, ID_ENTIDADE);
