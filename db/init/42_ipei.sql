-- ===================================================================================================================
-- IPEI — Informacao de Pesquisa e Investigacao da Receita Federal (quebra de sigilo fiscal)
--
-- Tabelas: IPE_TB01_IPEIS, IPE_TB02_ALVOS, IPE_TB03_RELACIONADOS, IPE_TB04_ANOS, IPE_TB05_FONTES,
--          IPE_TB06_CONTAS, IPE_TB07_BENS, IPE_TB08_NOTAS_FISCAIS, IPE_TB09_CONTATOS
--
-- Um relatorio de quebra de sigilo fiscal chega em DOIS arquivos: o PDF da IPEI e uma planilha de
-- anexo (as notas fiscais completas, a relacao de contas, o RENAVAM). Os dois sao o MESMO material
-- — o PDF resume e diz "segue em anexo" —, entao aqui eles compartilham a mesma linha de TB01:
-- ID_DOCUMENTO guarda o PDF e ID_DOCUMENTO_ANEXO a planilha, e na tela de investigacao os dois
-- viram um card so. As linhas vindas do anexo se distinguem pelo TP_ORIGEM/TP_CONTA de cada tabela.
--
-- O nivel do meio e o ALVO (TB02): o PDF de pessoa fisica traz um contribuinte, e o de pessoa
-- juridica traz meia duzia de empresas no mesmo arquivo, cada uma com o seu bloco completo. Toda
-- tabela filha pendura em ID_ALVO, e nao direto na IPEI, para as duas formas caberem sem ramo
-- especial no codigo (ver api/funcoes/ipei_utils.py e docs/ipei.md).
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO INVESTIGACAO, PUBLIC;

-- 01 · uma IPEI analisada, presa ao inquerito
CREATE TABLE IPE_TB01_IPEIS (
    ID_IPEI               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_INQUERITO          BIGINT NOT NULL,
    ID_DOCUMENTO          BIGINT,  -- o PDF anexado em INQ_TB04 (texto para a IA)
    ID_DOCUMENTO_ANEXO    BIGINT,  -- a planilha que acompanha o PDF, quando veio junto
    NO_ARQUIVO            VARCHAR(3000) NOT NULL,  -- nome do PDF em data/
    NO_ARQUIVO_ANEXO      VARCHAR(3000),           -- nome da planilha em data/
    NU_IPEI               VARCHAR(3000),  -- ex.: VT20230001 — e por ele que o anexo acha o PDF
    DT_IPEI               DATE,
    NO_UNIDADE            VARCHAR(3000),  -- ex.: RFB/Copei/Nupei em Vitoria/ES
    NU_PROCESSO_JUDICIAL  VARCHAR(3000),
    DT_DECISAO            DATE,           -- data da decisao que afastou o sigilo
    TX_CONCLUSAO          TEXT,
    TX_ENCAMINHAMENTO     TEXT,
    TX_ANEXOS             TEXT,           -- o que o proprio relatorio diz que segue em anexo
    NO_ASSINANTES         VARCHAR(3000),  -- auditores-fiscais que assinaram
    QT_FOLHAS             INTEGER NOT NULL DEFAULT 0,
    QT_PAGINAS            INTEGER NOT NULL DEFAULT 0,
    QT_ALVOS              INTEGER NOT NULL DEFAULT 0,
    QT_RELACIONADOS       INTEGER NOT NULL DEFAULT 0,
    QT_CONTAS             INTEGER NOT NULL DEFAULT 0,
    QT_BENS               INTEGER NOT NULL DEFAULT 0,
    QT_NOTAS              INTEGER NOT NULL DEFAULT 0,
    QT_CONTATOS           INTEGER NOT NULL DEFAULT 0,
    ID_USUARIO            BIGINT,  -- quem extraiu
    DT_EXTRACAO           TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK264_IPE_TB01_INQ_TB01 FOREIGN KEY (ID_INQUERITO)
        REFERENCES INQ_TB01_INQUERITOS (ID_INQUERITO),
    CONSTRAINT FK265_IPE_TB01_INQ_TB04 FOREIGN KEY (ID_DOCUMENTO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK266_IPE_TB01_INQ_TB04_ANEXO FOREIGN KEY (ID_DOCUMENTO_ANEXO)
        REFERENCES INQ_TB04_DOCUMENTOS (ID_DOCUMENTO),
    CONSTRAINT FK267_IPE_TB01_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO)
);

CREATE INDEX IX262_IPE_TB01_ID_INQUERITO ON IPE_TB01_IPEIS (ID_INQUERITO);
-- o anexo chega depois do PDF (ou antes) e se acha pelo numero da IPEI dentro do mesmo inquerito
CREATE INDEX IX263_IPE_TB01_NU_IPEI      ON IPE_TB01_IPEIS (ID_INQUERITO, NU_IPEI);

-- 02 · o contribuinte analisado — um por PDF de pessoa fisica, varios por PDF de pessoa juridica
CREATE TABLE IPE_TB02_ALVOS (
    ID_ALVO                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI                BIGINT NOT NULL,
    TP_PESSOA              VARCHAR(2) NOT NULL DEFAULT 'PF',  -- PF | PJ
    NO_ALVO                VARCHAR(3000),
    NU_CPF_CNPJ            VARCHAR(3000),
    NO_MAE                 VARCHAR(3000),
    DT_NASCIMENTO          DATE,  -- data de abertura, quando o alvo e pessoa juridica
    DT_INSCRICAO           DATE,
    DT_BAIXA               DATE,
    TX_ENDERECO            VARCHAR(3000),
    NO_MUNICIPIO           VARCHAR(3000),
    SG_UF                  VARCHAR(2),
    NU_CEP                 VARCHAR(8),
    TX_OCUPACAO            VARCHAR(3000),  -- ocupacao principal (PF) ou CNAE (PJ)
    TX_SITUACAO_CADASTRAL  VARCHAR(3000),
    VL_CAPITAL_SOCIAL      NUMERIC(18,2),
    TX_SEXO                VARCHAR(30),
    TX_NACIONALIDADE       VARCHAR(60),
    FL_OBITO               BOOLEAN NOT NULL DEFAULT FALSE,
    NO_CONTADOR            VARCHAR(3000),
    NU_SECAO               VARCHAR(20),  -- secao do PDF ("2", "3.4") de onde o bloco foi lido
    CONSTRAINT FK268_IPE_TB02_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI)
);

CREATE INDEX IX264_IPE_TB02_ID_IPEI     ON IPE_TB02_ALVOS (ID_IPEI);
CREATE INDEX IX265_IPE_TB02_NU_CPF_CNPJ ON IPE_TB02_ALVOS (NU_CPF_CNPJ);

-- 03 · quem aparece ligado ao alvo (socio, empregador, outorgante, mesmo endereco MAC...)
CREATE TABLE IPE_TB03_RELACIONADOS (
    ID_RELACIONADO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI          BIGINT NOT NULL,
    ID_ALVO          BIGINT NOT NULL,
    TP_RELACAO       VARCHAR(40) NOT NULL,  -- societario | empregaticio | procuracao |
                                            -- procuracao_rfb | mac_coincidente
    NO_RELACIONADO   VARCHAR(3000),
    NU_CPF_CNPJ      VARCHAR(3000),
    TX_QUALIFICACAO  VARCHAR(3000),  -- "Socio administrador", "Empregado", "Outorgado"...
    -- a linha traz ate tres datas (ingresso, retirada, situacao cadastral) em ordem que varia de
    -- tabela para tabela; DT_FIM so recebe a ultima que for POSTERIOR a DT_INICIO, e a linha crua
    -- fica em TX_CONTEXTO para o investigador conferir
    DT_INICIO        DATE,
    DT_FIM           DATE,
    PC_PARTICIPACAO  NUMERIC(9,4),
    TX_CONTEXTO      TEXT,
    CONSTRAINT FK269_IPE_TB03_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK270_IPE_TB03_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX266_IPE_TB03_ID_IPEI     ON IPE_TB03_RELACIONADOS (ID_IPEI);
CREATE INDEX IX267_IPE_TB03_ID_ALVO     ON IPE_TB03_RELACIONADOS (ID_ALVO);
CREATE INDEX IX268_IPE_TB03_NU_CPF_CNPJ ON IPE_TB03_RELACIONADOS (NU_CPF_CNPJ);

-- 04 · o consolidado anual do alvo, uma linha por ano-calendario
CREATE TABLE IPE_TB04_ANOS (
    ID_ANO                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI                    BIGINT NOT NULL,
    ID_ALVO                    BIGINT NOT NULL,
    NU_ANO                     INTEGER NOT NULL,
    VL_REND_TRIBUTAVEIS        NUMERIC(18,2),
    VL_REND_ISENTOS            NUMERIC(18,2),
    VL_REND_EXCLUSIVA          NUMERIC(18,2),
    VL_REND_PESSOA_FISICA      NUMERIC(18,2),
    VL_REND_TOTAL              NUMERIC(18,2),
    VL_RECEITA_BRUTA           NUMERIC(18,2),  -- pessoa juridica
    VL_DIRF_TRABALHO           NUMERIC(18,2),
    VL_DIRF_TOTAL              NUMERIC(18,2),
    VL_ECF_TOTAL               NUMERIC(18,2),
    VL_MOV_CREDITOS            NUMERIC(18,2),  -- e-Financeira: creditos liquidos no ano
    -- movimentacao financeira dividida pelo rendimento declarado. E o numero que o relatorio usa
    -- para dizer que algo nao fecha (1,47 num ano e 94,23 no outro), entao vale coluna propria.
    VL_RAZAO_MOV_REND          NUMERIC(18,4),
    VL_RECEITA_RURAL           NUMERIC(18,2),
    VL_ALIENACAO_IMOVEIS       NUMERIC(18,2),
    VL_ALIENACAO_BENS          NUMERIC(18,2),
    VL_DIVIDAS_CONTRAIDAS      NUMERIC(18,2),
    VL_BENS_ANO                NUMERIC(18,2),
    VL_BENS_ANO_ANTERIOR       NUMERIC(18,2),
    VL_DIVIDAS_ANO             NUMERIC(18,2),
    VL_DIVIDAS_ANO_ANTERIOR    NUMERIC(18,2),
    VL_VARIACAO_PATRIMONIAL    NUMERIC(18,2),
    VL_DIF_TRANSPOSICAO        NUMERIC(18,2),
    VL_DECRED                  NUMERIC(18,2),  -- pagamentos a administradoras de cartao
    VL_NFE_COMPRAS             NUMERIC(18,2),
    VL_NFE_VENDAS              NUMERIC(18,2),
    VL_NFS_SERVICOS            NUMERIC(18,2),
    VL_TRIBUTOS_PAGOS          NUMERIC(18,2),
    CONSTRAINT FK271_IPE_TB04_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK272_IPE_TB04_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX269_IPE_TB04_ID_IPEI ON IPE_TB04_ANOS (ID_IPEI);
CREATE INDEX IX270_IPE_TB04_ID_ALVO ON IPE_TB04_ANOS (ID_ALVO, NU_ANO);

-- 05 · fontes pagadoras e pagamentos declarados, ano a ano
CREATE TABLE IPE_TB05_FONTES (
    ID_FONTE       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI        BIGINT NOT NULL,
    ID_ALVO        BIGINT NOT NULL,
    TP_REGISTRO    VARCHAR(40) NOT NULL,  -- pessoa_juridica | isento | exclusiva | pagamento |
                                          -- servico_efd
    NU_ANO         INTEGER,
    NO_FONTE       VARCHAR(3000),
    NU_CPF_CNPJ    VARCHAR(3000),
    TX_NATUREZA    VARCHAR(3000),
    VL_RENDIMENTO  NUMERIC(18,2),
    VL_IMPOSTO     NUMERIC(18,2),
    CONSTRAINT FK273_IPE_TB05_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK274_IPE_TB05_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX271_IPE_TB05_ID_IPEI     ON IPE_TB05_FONTES (ID_IPEI);
CREATE INDEX IX272_IPE_TB05_ID_ALVO     ON IPE_TB05_FONTES (ID_ALVO);
CREATE INDEX IX273_IPE_TB05_NU_CPF_CNPJ ON IPE_TB05_FONTES (NU_CPF_CNPJ);

-- 06 · contas bancarias da e-Financeira, com o mes a mes do corpo do PDF e as do anexo
CREATE TABLE IPE_TB06_CONTAS (
    ID_CONTA      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI       BIGINT NOT NULL,
    ID_ALVO       BIGINT NOT NULL,
    TP_CONTA      VARCHAR(100),  -- deposito | investimento | previdencia | custodia | cambio,
                                 -- ou a descricao que a planilha de anexo traz
    NO_BANCO      VARCHAR(3000),
    NU_CONTA      VARCHAR(100),
    QT_TITULARES  INTEGER,
    NU_ANO        INTEGER,
    NU_MES        SMALLINT,  -- nulo na linha vinda do anexo, que consolida por ano
    VL_CREDITOS   NUMERIC(18,2),
    VL_DEBITOS    NUMERIC(18,2),
    VL_SALDO      NUMERIC(18,2),
    NO_TITULAR    VARCHAR(3000),  -- do anexo: quem figura na conta (titular ou representante)
    NU_TITULAR    VARCHAR(3000),
    TX_RELACAO    VARCHAR(100),
    CONSTRAINT FK275_IPE_TB06_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK276_IPE_TB06_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX274_IPE_TB06_ID_IPEI  ON IPE_TB06_CONTAS (ID_IPEI);
CREATE INDEX IX275_IPE_TB06_ID_ALVO  ON IPE_TB06_CONTAS (ID_ALVO, NU_ANO, NU_MES);
CREATE INDEX IX276_IPE_TB06_NU_CONTA ON IPE_TB06_CONTAS (NU_CONTA);

-- 07 · bens: declarados no IRPF, veiculos do RENAVAM, imoveis, embarcacoes e aeronaves
CREATE TABLE IPE_TB07_BENS (
    ID_BEM            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI           BIGINT NOT NULL,
    ID_ALVO           BIGINT NOT NULL,
    TP_BEM            VARCHAR(40) NOT NULL,  -- declarado | divida | imovel | imovel_dimob |
                                             -- criptoativo | especie | veiculo |
                                             -- veiculo_outras_bases | outro
    NU_ANO            INTEGER,
    NU_IDENTIFICADOR  VARCHAR(100),  -- placa, quando ha
    TX_DESCRICAO      TEXT,
    VL_ANO_ANTERIOR   NUMERIC(18,2),
    VL_ANO            NUMERIC(18,2),
    TX_SITUACAO       VARCHAR(3000),  -- restricao do RENAVAM (ex.: ALIENACAO FIDUCIARIA)
    TX_CONTEXTO       TEXT,
    CONSTRAINT FK277_IPE_TB07_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK278_IPE_TB07_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX277_IPE_TB07_ID_IPEI ON IPE_TB07_BENS (ID_IPEI);
CREATE INDEX IX278_IPE_TB07_ID_ALVO ON IPE_TB07_BENS (ID_ALVO, TP_BEM);

-- 08 · notas fiscais eletronicas — as do corpo do PDF e as da planilha de anexo
CREATE TABLE IPE_TB08_NOTAS_FISCAIS (
    ID_NOTA              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI              BIGINT NOT NULL,
    ID_ALVO              BIGINT NOT NULL,
    TP_ORIGEM            VARCHAR(10) NOT NULL DEFAULT 'pdf',  -- pdf | anexo
    DT_EMISSAO           DATE,
    NU_NOTA              VARCHAR(100),
    NU_CHAVE             VARCHAR(100),
    NO_EMITENTE          VARCHAR(3000),
    NU_CNPJ_EMITENTE     VARCHAR(100),
    NO_DESTINATARIO      VARCHAR(3000),
    NU_CPF_DESTINATARIO  VARCHAR(100),
    TX_DESCRICAO         TEXT,
    QT_ITEM              NUMERIC(18,4),
    VL_UNITARIO          NUMERIC(18,4),
    VL_TOTAL             NUMERIC(18,2),
    CONSTRAINT FK279_IPE_TB08_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK280_IPE_TB08_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX279_IPE_TB08_ID_IPEI    ON IPE_TB08_NOTAS_FISCAIS (ID_IPEI);
CREATE INDEX IX280_IPE_TB08_ID_ALVO    ON IPE_TB08_NOTAS_FISCAIS (ID_ALVO, DT_EMISSAO);
CREATE INDEX IX281_IPE_TB08_NO_EMITENTE ON IPE_TB08_NOTAS_FISCAIS (NO_EMITENTE);

-- 09 · enderecos, telefones, e-mails e MAC/IP do alvo em outras bases da RFB
CREATE TABLE IPE_TB09_CONTATOS (
    ID_CONTATO   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_IPEI      BIGINT NOT NULL,
    ID_ALVO      BIGINT NOT NULL,
    TP_CONTATO   VARCHAR(20) NOT NULL,  -- endereco | telefone | email | mac
    TX_VALOR     TEXT,
    NU_ANO       INTEGER,
    TX_CONTEXTO  TEXT,
    CONSTRAINT FK281_IPE_TB09_IPE_TB01 FOREIGN KEY (ID_IPEI)
        REFERENCES IPE_TB01_IPEIS (ID_IPEI),
    CONSTRAINT FK282_IPE_TB09_IPE_TB02 FOREIGN KEY (ID_ALVO)
        REFERENCES IPE_TB02_ALVOS (ID_ALVO)
);

CREATE INDEX IX282_IPE_TB09_ID_IPEI ON IPE_TB09_CONTATOS (ID_IPEI);
CREATE INDEX IX283_IPE_TB09_ID_ALVO ON IPE_TB09_CONTATOS (ID_ALVO, TP_CONTATO);

RESET search_path;
