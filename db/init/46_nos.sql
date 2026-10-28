-- ===================================================================================================================
-- Nós descentralizados de extração (ver docs/no_descentralizado.md)
--
-- Tabelas: NO_TB01_SERVIDORES, NO_TB02_SERVIDOR_DELEGACIAS
--
-- O sistema roda em dois papéis a partir do MESMO projeto (PAPEL no .env): o CENTRAL, com a web,
-- a API e o banco, e os NÓS das delegacias, que guardam as evidências na própria pasta data/ e
-- atendem chamadas do central (hash, parse de documento, leitura de arquivo por faixa). Este
-- registro é o que liga os dois: uma linha por nó, com a URL onde ele atende e o token que
-- autentica os dois sentidos da conversa (mostrado uma única vez na criação, e depois só na
-- operação explícita "trocar token").
--
-- UM ATENDE VÁRIAS: um nó pode servir MAIS DE UMA delegacia (delegacias pequenas de uma mesma
-- cidade costumam dividir um servidor) — o vínculo vive em NO_TB02_SERVIDOR_DELEGACIAS. O
-- contrário continua proibido: UMA DELEGACIA TEM NO MÁXIMO UM NÓ (UX110 no vínculo) — o
-- roteamento é delegacia → nó, e dois nós para a mesma unidade levantariam a pergunta "qual
-- deles tem o arquivo?" — que não tem resposta boa.
--
-- DT_ULTIMO_HEARTBEAT é batido por toda chamada do nó ao central (POST /api/no/ping e qualquer
-- /api/no/*); a tela Administração → Servidores mostra online/offline a partir dele, e o
-- TX_VERSAO deixa o central (e o admin) verem o nó desatualizado antes de mandar trabalho.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE NO_TB01_SERVIDORES (
    ID_SERVIDOR           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_SERVIDOR           VARCHAR(100) NOT NULL UNIQUE,   -- rótulo único (ex.: "DELECOR-SP")
    TX_URL_BASE           VARCHAR(500) NOT NULL,          -- ex.: http://10.11.5.15:8080 — alcançável pelo central
    -- Token compartilhado dos DOIS sentidos (nó→central e central→nó). Em claro de propósito:
    -- o central precisa APRESENTAR o token quando chama o nó, e hash não se apresenta — o segredo
    -- já mora em claro no .env do nó, e o banco é justamente o cofre que a rede não alcança.
    -- Revoga-se com "trocar token" na tela (o nó para de autenticar nos dois sentidos).
    TX_TOKEN              VARCHAR(200) NOT NULL,
    TX_VERSAO             VARCHAR(50),                    -- VERSION reportada no último batimento
    DT_ULTIMO_HEARTBEAT   TIMESTAMP,
    -- Batida do CANO do túnel NAT (serviço tunel: connect, ping de 30 s, NULL no disconnect).
    -- TIMESTAMP e não booleano: se o serviço túnel morrer sem se despedir, a coluna congela e
    -- deixa de valer sozinha (no_utils.SEGUNDOS_TUNEL_FRESCO); um booleano mentiria para sempre.
    DT_TUNEL_ATIVO        TIMESTAMP,
    DT_CRIACAO            TIMESTAMP NOT NULL DEFAULT now(),
    IN_ATIVA              BOOLEAN NOT NULL DEFAULT true,  -- inativa o roteamento: as delegacias voltam a processar no central
    -- Mídia direta (docs/no_midia_direta.md): base HTTPS do no-web (porta 8443 em todos os nós)
    -- onde o NAVEGADOR da LAN baixa a evidência com um ticket do central. Diferente de
    -- TX_URL_BASE: aquela é o cano central→nó (http/IP); esta é o caminho navegador→nó, que só
    -- existe onde a TI deu nome DNS + certificado. IN_MIDIA_DIRETA desligado = nada muda.
    TX_MIDIA_URL          VARCHAR(500),
    IN_MIDIA_DIRETA       BOOLEAN NOT NULL DEFAULT false
);

-- Quais delegacias cada nó atende (N de cada lado, com UX110 travando "uma delegacia, um nó").
-- ON DELETE CASCADE dos dois lados: apagar o nó ou a delegacia derruba só o VÍNCULO — o
-- histórico de extrações continua apontando para o ID_SERVIDOR sem FK (ver 09_extracoes.sql).
CREATE TABLE NO_TB02_SERVIDOR_DELEGACIAS (
    ID_SERVIDOR_DELEGACIA BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_SERVIDOR           BIGINT NOT NULL,
    ID_DELEGACIA          BIGINT NOT NULL,
    CONSTRAINT FK403_NO_TB02_NO_TB01 FOREIGN KEY (ID_SERVIDOR)
        REFERENCES NO_TB01_SERVIDORES (ID_SERVIDOR) ON DELETE CASCADE,
    CONSTRAINT FK404_NO_TB02_DELEGACIAS FOREIGN KEY (ID_DELEGACIA)
        REFERENCES SEGURANCA.DELEGACIAS (ID_DELEGACIA) ON DELETE CASCADE
);

-- Uma delegacia, um nó. Sem filtro nos ativos de propósito: a inativa continua "ocupando" a
-- delegacia para forçar o admin a desfazer o vínculo (ou reativar o nó) antes de amarrá-la a
-- outro, em vez de deixar dois registros convivendo em silêncio.
CREATE UNIQUE INDEX UX110_NO_TB02_DELEGACIA ON NO_TB02_SERVIDOR_DELEGACIAS (ID_DELEGACIA);

-- Recibo da entrega de extrações processadas PELO NÓ (funcoes/canal_extracao.py): uma linha por
-- (extração, lote) já reproduzido no central. É a idempotência da retomada — o nó reenvia do
-- último lote não confirmado e o central ignora duplicata sem gravar duas vezes.
CREATE TABLE NO_TB03_LOTES_ENTREGA (
    ID_LOTE        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO    BIGINT NOT NULL,
    NU_LOTE        INTEGER NOT NULL,
    QT_REGISTROS   INTEGER NOT NULL DEFAULT 0,
    DT_RECEBIDO    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK405_NO_TB03_EXTRACOES FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO) ON DELETE CASCADE
);

CREATE UNIQUE INDEX UX111_LOTE_ENTREGA ON NO_TB03_LOTES_ENTREGA (ID_EXTRACAO, NU_LOTE);

RESET search_path;
