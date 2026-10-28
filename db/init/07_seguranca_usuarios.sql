-- ===================================================================================================================
-- Usuarios, sessoes, unidades, delegacias, prompts de IA e auditoria de acesso
--
-- Tabelas: UNIDADES, DELEGACIAS, USR_TB01_USUARIOS, USR_TB02_SESSOES, USR_TB03_REDEFINICAO_SENHA,
--          USR_TB04_TENTATIVAS_LOGIN, USR_TB05_ACESSOS_INQUERITO, USR_TB07_WEBHOOK_IA,
--          USR_TB09_TENTATIVAS_CADASTRO, PROMPTS_IA
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO SEGURANCA, PUBLIC;

-- Árvore de unidades superiores (ver docs/unidades_prompts.md): o pai da delegacia costuma ser a
-- UF (SR/PF-ES), mas há estados com um nível a mais entre a UF e a delegacia — é o caso do DF,
-- com DIP e DICOR abaixo da superintendência. Por isso a hierarquia é uma árvore por ID_PAI e não
-- uma coluna fixa "unidade superior" em DELEGACIAS: profundidade variável sem nova migração.
-- A delegacia NÃO entra na árvore (é a unidade de controle do sistema, com FKs próprias espalhadas
-- por usuários, nós e flags); ela só aponta para o pai imediato em ID_UNIDADE.
CREATE TABLE UNIDADES (
    ID_UNIDADE      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_UNIDADE      VARCHAR(300) NOT NULL UNIQUE,
    -- Sigla curta exibida em listas e no bloco de prompt ("DIP", "SR/DF"); nome completo fica em
    -- NO_UNIDADE. Sem UNIQUE: sigla repete entre UFs sem estragar nada (o que identifica é o nome).
    SG_UNIDADE      VARCHAR(30),
    -- 'uf' = unidade da federação / superintendência; 'coordenacao' = nível intermediário (DIP,
    -- DICOR). A profundidade não é travada: o nível é rótulo de exibição, quem desenha a árvore é
    -- o admin na tela. CHECK pequeno de propósito — lista grande de nível só atrasaria cadastro.
    TP_NIVEL        VARCHAR(20) NOT NULL DEFAULT 'coordenacao',
    -- NULL = raiz (uma UF solta, por exemplo). Anti-ciclo é checagem da aplicação
    -- (api/rotas/unidades.py): no CRUD a árvore tem meia dúzia de nós e o ciclo só nasce por lá.
    ID_UNIDADE_PAI  BIGINT,
    UF              VARCHAR(2),
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT CK31_UNIDADES_TP_NIVEL CHECK (TP_NIVEL IN ('uf', 'coordenacao')),
    CONSTRAINT FK421_UNIDADES_PAI FOREIGN KEY (ID_UNIDADE_PAI)
        REFERENCES UNIDADES (ID_UNIDADE)
);

CREATE TABLE DELEGACIAS (
    ID_DELEGACIA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_DELEGACIA  VARCHAR(300) NOT NULL UNIQUE,
    -- Unidade da federação da delegacia ("SP", "ES"...), para a gerência ordenar/filtrar e o
    -- admin reconhecer a unidade numa lista grande. VARCHAR(2) e não CHECK de lista: a lista
    -- de UF é estável demais para virar constraint e rigor aqui só atrapalharia o cadastro.
    UF            VARCHAR(2),
    IN_IA_ATIVA   BOOLEAN NOT NULL DEFAULT TRUE,
    -- A delegacia tem um nó cadastrado (investigacao.no_tb01_servidores, ver 46_nos.sql)? Com
    -- TRUE (padrão) upload, seletor e extração passam a trabalhar na pasta DO NÓ; com FALSE o
    -- central finge que o nó não existe e faz tudo na pasta local — é a opção "continuar
    -- extraindo pelo principal" ligada/desligada na tela Administração → Delegacias.
    -- Só afeta o caminho novo: extrações que já nasceram no nó continuam sendo lidas por ele
    -- (ID_SERVIDOR na INQ_TB02_EXTRACOES), flag nenhuma desliga a leitura do que já é do nó.
    IN_NO_ATIVA   BOOLEAN NOT NULL DEFAULT TRUE,
    DT_CRIACAO    TIMESTAMP NOT NULL DEFAULT now(),
    -- Pai imediato na árvore de UNIDADES (NULL = sem unidade superior, comportamento antigo). É o
    -- gancho da herança de prompt de IA: o bloco do chat concatena geral → unidades ancestrais →
    -- delegacia → usuário (funcoes/ia_prompt_niveis.py). ON DELETE SET NULL: apagar a unidade não
    -- apaga a delegacia — só desfaz o vínculo, como já acontece com o nó. Por último na tabela
    -- porque é assim que o ALTER 0050 acrescenta num banco que já existe (paridade do pg_dump).
    ID_UNIDADE    BIGINT,
    CONSTRAINT FK422_DELEGACIAS_UNIDADES FOREIGN KEY (ID_UNIDADE)
        REFERENCES UNIDADES (ID_UNIDADE) ON DELETE SET NULL
);

CREATE TABLE USR_TB01_USUARIOS (
    ID_USUARIO       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_LOGIN         VARCHAR(3000) NOT NULL UNIQUE,
    NO_USUARIO       VARCHAR(3000) NOT NULL,
    -- Aceita NULL: quem entra pela API-PF nasce sem senha DAQUI (a dele fica na PF e nunca
    -- passa por este servidor) e e obrigado a criar uma no primeiro acesso. Enquanto for NULL,
    -- o login local recusa a conta. Ver db/init/35_autenticacao_api_pf.sql.
    TX_SENHA_HASH    TEXT,
    IN_ADMIN         BOOLEAN NOT NULL DEFAULT false,
    -- Perfil do usuario no sistema: o que define as capacidades (ver api/funcoes/auth_utils.py
    -- _capacidades). IN_ADMIN continua existindo como espelho de 'administrador' (mantido em
    -- sincronia na escrita), para nao reescrever todos os pontos que checam o privilegio global.
    -- Valores: 'analista' (padrao) | 'inteligencia' (chefia) | 'agente_inteligencia' | 'auditoria'
    -- | 'administrador'.
    TP_PERFIL        VARCHAR(20) NOT NULL DEFAULT 'analista',
    IN_ATIVO         BOOLEAN NOT NULL DEFAULT true,
    -- Desligar a IA so deste usuario (tela Usuarios -> "Desativar IA"): sem IA nenhuma, nem a
    -- propria, nem a das Configuracoes. Espelho do DELEGACIAS.IN_IA_ATIVA, so que por pessoa
    -- (ver funcoes/ia_delegacia.py).
    IN_IA_ATIVA      BOOLEAN NOT NULL DEFAULT true,
    DT_CADASTRO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ULTIMO_LOGIN  TIMESTAMP,
    IN_APROVADO      BOOLEAN NOT NULL DEFAULT true,
    -- Id do tema da interface escolhido pelo usuario. Sem CHECK de valores: a lista de temas
    -- vive em web/js/utils/theme.js (TEMAS) e e validada em api/funcoes/auth_utils.py
    -- (TEMAS_VALIDOS); ter a lista tambem aqui obrigava uma migracao a cada tema novo.
    TP_TEMA          VARCHAR(30) NOT NULL DEFAULT 'dark',
    NO_EMAIL         VARCHAR(3000),
    ID_DELEGACIA     BIGINT,
    -- Origem do cadastro (onde ele nasceu), nao a forma de entrar: quem veio da API-PF tambem
    -- passa a ter senha local e entra pelos dois caminhos.
    CO_PROVEDOR      VARCHAR(20) NOT NULL DEFAULT 'POLICIA',
    CONSTRAINT CK10_USR_TB01_CO_PROVEDOR CHECK (CO_PROVEDOR IN ('POLICIA', 'API_PF')),
    CONSTRAINT CK23_USR_TB01_TP_PERFIL CHECK (TP_PERFIL IN ('analista', 'inteligencia', 'agente_inteligencia', 'auditoria', 'administrador')),
    CONSTRAINT FK73_USR_TB01_DELEGACIAS FOREIGN KEY (ID_DELEGACIA)
        REFERENCES DELEGACIAS (ID_DELEGACIA)
);

-- Um e-mail não pode servir dois usuários (senão "esqueci minha senha" ficaria
-- ambíguo). Único por lower() porque endereço não diferencia maiúscula na
-- prática, e parcial porque vários usuários sem e-mail (NULL) é o normal.
CREATE UNIQUE INDEX IX100_USR_TB01_EMAIL
    ON USR_TB01_USUARIOS (lower(NO_EMAIL))
    WHERE NO_EMAIL IS NOT NULL;

-- guarda o HASH do token de sessão, não o token — um dump do banco não
-- basta pra sequestrar sessões ativas
CREATE TABLE USR_TB02_SESSOES (
    ID_SESSAO_HASH  TEXT PRIMARY KEY,
    ID_USUARIO      BIGINT NOT NULL,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_EXPIRACAO    TIMESTAMP NOT NULL,
    CONSTRAINT FK66_USR_TB02_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX30_USR_TB02_USUARIO ON USR_TB02_SESSOES (ID_USUARIO);
CREATE INDEX IX31_USR_TB02_EXPIRACAO ON USR_TB02_SESSOES (DT_EXPIRACAO);

-- Guarda o HASH do token, não o token — mesmo raciocínio de USR_TB02_SESSOES:
-- um dump do banco não permite redefinir a senha de ninguém.
-- Uso único: DT_USO marcado no momento em que a senha é trocada, e o token
-- deixa de valer (não é apagado, para o histórico continuar auditável).
CREATE TABLE USR_TB03_REDEFINICAO_SENHA (
    ID_TOKEN_HASH  TEXT PRIMARY KEY,
    ID_USUARIO     BIGINT NOT NULL,
    TP_ORIGEM      VARCHAR(10) NOT NULL,
    DT_CRIACAO     TIMESTAMP NOT NULL DEFAULT now(),
    DT_EXPIRACAO   TIMESTAMP NOT NULL,
    DT_USO         TIMESTAMP,
    CONSTRAINT CK04_USR_TB03_TP_ORIGEM CHECK (TP_ORIGEM IN ('admin', 'email')),
    CONSTRAINT FK158_USR_TB03_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX101_USR_TB03_USUARIO ON USR_TB03_REDEFINICAO_SENHA (ID_USUARIO);
CREATE INDEX IX102_USR_TB03_EXPIRACAO ON USR_TB03_REDEFINICAO_SENHA (DT_EXPIRACAO);

CREATE TABLE USR_TB04_TENTATIVAS_LOGIN (
    ID_TENTATIVA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_LOGIN      VARCHAR(60) NOT NULL,  -- login digitado, já normalizado (minúsculo, sem espaços)
    CO_IP         VARCHAR(45),  -- origem (45 = IPv6 textual); nulo se não determinada
    IN_SUCESSO    BOOLEAN NOT NULL,
    DT_TENTATIVA  TIMESTAMP NOT NULL DEFAULT now()
);

-- Os dois índices cobrem exatamente as duas contagens da janela (por login e
-- por IP). DT_TENTATIVA vem depois da coluna de escopo porque o filtro é
-- sempre "este login/IP" + "desde tal instante".
CREATE INDEX IX103_USR_TB04_LOGIN ON USR_TB04_TENTATIVAS_LOGIN (NO_LOGIN, DT_TENTATIVA);
CREATE INDEX IX104_USR_TB04_IP ON USR_TB04_TENTATIVAS_LOGIN (CO_IP, DT_TENTATIVA);

-- Tentativas de AUTOcadastro por IP (ver db/migracoes/0060): o POST /api/auth/cadastro é
-- público e entregava "já existe tal login/e-mail" sem limite — sem esta contagem a rota
-- era um oracle de enumeração ilimitado. Só o IP importa (não há login "alvo" fixo aqui).
CREATE TABLE USR_TB09_TENTATIVAS_CADASTRO (
    ID_TENTATIVA  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    CO_IP         VARCHAR(45),  -- origem (45 = IPv6 textual); nulo se não determinada
    IN_SUCESSO    BOOLEAN NOT NULL,
    DT_TENTATIVA  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IX108_USR_TB09_IP ON USR_TB09_TENTATIVAS_CADASTRO (CO_IP, DT_TENTATIVA);

CREATE TABLE USR_TB05_ACESSOS_INQUERITO (
    ID_ACESSO           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_USUARIO          BIGINT NOT NULL,
    ID_INQUERITO        BIGINT NOT NULL,  -- sem FK: a trilha sobrevive ao inquérito apagado
    NO_RECURSO          VARCHAR(200) NOT NULL,  -- rota normalizada, ex.: /api/investigacao/inqueritos/{id}/conversas
    TP_METODO           VARCHAR(10) NOT NULL,  -- GET | POST | DELETE…
    IN_PERMITIDO        BOOLEAN NOT NULL,  -- false = tentativa barrada com 403
    CO_IP               VARCHAR(45),
    DT_JANELA           TIMESTAMP NOT NULL,  -- hora cheia (date_trunc('hour')) a que a linha se refere
    DT_PRIMEIRO_ACESSO  TIMESTAMP NOT NULL DEFAULT now(),
    DT_ULTIMO_ACESSO    TIMESTAMP NOT NULL DEFAULT now(),
    QT_ACESSOS          BIGINT NOT NULL DEFAULT 1,
    CONSTRAINT FK159_USR_TB05_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

-- É esta UNIQUE que faz a agregação funcionar: o ON CONFLICT do registrador cai
-- aqui e vira "soma 1 no contador" em vez de linha nova. CO_IP fica de fora da
-- chave (IP de saída pode mudar no meio da sessão e quebraria a agregação); o
-- que fica gravado é o IP do primeiro acesso da janela.
CREATE UNIQUE INDEX UX44_USR_TB05_JANELA
    ON USR_TB05_ACESSOS_INQUERITO (ID_USUARIO, ID_INQUERITO, NO_RECURSO, TP_METODO, IN_PERMITIDO, DT_JANELA);
-- listagem da tela de auditoria (mais recentes primeiro, filtrando por inquérito/usuário)
CREATE INDEX IX105_USR_TB05_JANELA ON USR_TB05_ACESSOS_INQUERITO (DT_JANELA DESC);
CREATE INDEX IX106_USR_TB05_INQUERITO ON USR_TB05_ACESSOS_INQUERITO (ID_INQUERITO, DT_JANELA DESC);
CREATE INDEX IX107_USR_TB05_USUARIO ON USR_TB05_ACESSOS_INQUERITO (ID_USUARIO, DT_JANELA DESC);

-- Webhook de conclusão da IA por usuário (ver db/migracoes/0005_webhook_ia_usuario.sql).
-- Um por usuário: URL para onde o backend dispara o aviso simples quando um turno da IA
-- termina (concluído/erro/cancelado). Sem linha ou com IN_ATIVO = false, nada é enviado.
CREATE TABLE USR_TB07_WEBHOOK_IA (
    ID_USUARIO      BIGINT PRIMARY KEY,
    TX_WEBHOOK_URL  TEXT NOT NULL,
    IN_ATIVO        BOOLEAN NOT NULL DEFAULT true,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK254_USR_TB07_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE
);

-- Prompt de IA complementar por nível (ver docs/unidades_prompts.md). O prompt de sistema base
-- continua em código (funcoes/ia_utils.py, SISTEMA_PROMPT); esta tabela guarda só instruções
-- ADICIONAIS da administração, em 4 níveis que se acumulam em cascata na ordem
-- geral → unidade superior (raiz→folha) → delegacia → usuário, montados por
-- funcoes/ia_prompt_niveis.py e injetados no contexto de toda conversa de IA.
--
-- ID_ENTIDADE é polimórfico de propósito (aponta para UNIDADES, DELEGACIAS ou USR_TB01_USUARIOS
-- conforme TP_NIVEL, e é NULL em 'sistema') — por isso não tem FK; a existência da entidade é
-- conferida na rota (api/rotas/configuracao_admin.py) e a exclusão das entidades limpa os prompts
-- órfãos. CK33 trava a correspondência nível↔entidade; UX113 garante UMA linha ativa por entidade
-- (desligar sem apagar continua possível com IN_ATIVA = false, igual a USR_TB06_CONFIG_IA).
CREATE TABLE PROMPTS_IA (
    ID_PROMPT        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    TP_NIVEL         VARCHAR(10) NOT NULL,
    ID_ENTIDADE      BIGINT,
    TX_PROMPT        TEXT NOT NULL,
    IN_ATIVA         BOOLEAN NOT NULL DEFAULT TRUE,
    -- Quem escreveu (é sempre um administrador) — a trilha de "de onde veio esta instrução" começa aqui
    ID_USUARIO_AUTOR BIGINT,
    DT_CRIACAO       TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO   TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT CK32_PROMPTS_IA_TP_NIVEL CHECK (TP_NIVEL IN ('sistema', 'unidade', 'delegacia', 'usuario')),
    CONSTRAINT CK33_PROMPTS_IA_ENTIDADE CHECK (
        (TP_NIVEL = 'sistema' AND ID_ENTIDADE IS NULL) OR
        (TP_NIVEL <> 'sistema' AND ID_ENTIDADE IS NOT NULL)
    ),
    CONSTRAINT FK423_PROMPTS_IA_AUTOR FOREIGN KEY (ID_USUARIO_AUTOR)
        REFERENCES USR_TB01_USUARIOS (ID_USUARIO)
);

-- Uma linha ATIVA por nível+entidade (COALESCE entra porque UNIQUE ignora NULL e o nível 'sistema'
-- tem entidade nula). Linhas inativas podem se acumular: são histórico reativável.
CREATE UNIQUE INDEX UX113_PROMPTS_IA_ATIVA
    ON PROMPTS_IA (TP_NIVEL, COALESCE(ID_ENTIDADE, 0))
    WHERE IN_ATIVA;
-- resolução do turno (busca os prompts de uma pessoa/delegacia/unidades de uma vez)
CREATE INDEX IX431_PROMPTS_IA_ENTIDADE ON PROMPTS_IA (TP_NIVEL, ID_ENTIDADE);

-- Usuário administrador inicial. A senha abaixo é pública (está no código-fonte) —
-- TROQUE assim que logar pela primeira vez, na tela "Usuários".
INSERT INTO USR_TB01_USUARIOS (NO_LOGIN, NO_USUARIO, TX_SENHA_HASH, IN_ADMIN, TP_PERFIL)
VALUES ('admin', 'Administrador', crypt('admin1234', gen_salt('bf')), true, 'administrador');

INSERT INTO DELEGACIAS (NO_DELEGACIA) VALUES
    ('DELECOR'),
    ('DELEFAZ'),
    ('DRE'),
    ('DELECIBER'),
    ('DELEPAT'),
    ('DMA'),
    ('DELEPREV'),
    ('DELINST'),
    ('FICCO/ES'),
    ('DPF/CIT/ES'),
    ('DPF/SMT/ES'),
    ('SIP'),
    ('DRPJ');

RESET search_path;
