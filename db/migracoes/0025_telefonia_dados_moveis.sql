-- Dados móveis da telefonia — as sessões de dados (CGI + IP + volumes) que a operadora
-- devolve na quebra de sigilo, no mesmo card da operadora. Correspondente declarativo em
-- db/init/43_telefonia.sql (tabela TEL_TB08_DADOS_MOVEIS + coluna QT_DADOS em TEL_TB01).
--
-- Contexto: além do CDR de voz, a operadora entrega o histórico de sessões de dados da linha —
-- cada linha é um intervalo (DTAINI/HORINI → DTAFIM/HORFIM) em que a linha ficou presa a uma
-- antena (LOCALI, o CGI) com um IP (CODIPS/CODIPV6), um IMEI (CODIME) e volumes de entrada e
-- saída (VOLENT/VOLSAI, em UNIVOL). O arquivo às vezes traz cabeçalho (hc_dados.xlsx, com as
-- colunas ERB_* de endereço da antena no fim) e às vezes não (o extrato por CGI, sem nenhuma
-- linha de título — ver api/funcoes/telefonia_utils.py, seção "dados").
--
-- Como o resto da telefonia, a chave é o par (inquérito, operadora): as sessões caem no mesmo
-- card do CDR de voz, e a antena da sessão (LOCALI + colunas ERB_*) completa TEL_TB05_ERBS pelo
-- CGI — é o que põe no mapa antena que o ofício de voz não listou.

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE TEL_TB01_TELEFONIA ADD COLUMN IF NOT EXISTS QT_DADOS INTEGER NOT NULL DEFAULT 0;

-- 08 · sessão de dados móveis (uma linha do relatório de dados da operadora)
CREATE TABLE IF NOT EXISTS TEL_TB08_DADOS_MOVEIS (
    ID_DADO_MOVEL          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_TELEFONIA           BIGINT NOT NULL,
    ID_ARQUIVO_TELEFONIA   BIGINT,
    NU_CGI                 VARCHAR(3000),   -- LOCALI: a antena que atendeu (casa com TEL_TB05.NU_CGI)
    TX_IP                  VARCHAR(3000),   -- CODIPS: o IPv4 da sessão (faixa privada/NAT ou pública)
    TX_IPV6                VARCHAR(3000),   -- IPV6: o prefixo IPv6, quando a operadora manda
    NU_LINHA               VARCHAR(3000),   -- NUMLIN, só dígitos
    NU_CORIDE              VARCHAR(3000),   -- CORIDE: o identificador da conexão no relatório
    DT_INICIO              TIMESTAMP,       -- DTAINI + HORINI
    TP_EVENTO_INICIO       VARCHAR(3000),   -- EVEINI
    DT_FIM                 TIMESTAMP,       -- DTAFIM + HORFIM
    TP_EVENTO_FIM          VARCHAR(3000),   -- EVEFIM
    NU_SITE                VARCHAR(3000),   -- SITEID
    QT_VOL_ENTRADA         INTEGER,         -- VOLENT, em TP_UNIDADE
    QT_VOL_SAIDA           INTEGER,         -- VOLSAI, em TP_UNIDADE
    TP_UNIDADE             VARCHAR(10),     -- UNIVOL (ex.: K)
    TP_TRAFEGO             VARCHAR(3000),   -- TIPTRA (ex.: H)
    TP_SERVICO             VARCHAR(3000),   -- TIPSER (ex.: 2)
    TP_TECNOLOGIA          VARCHAR(3000),   -- TECNOL, como a operadora escreve (ex.: 5, 6, 7)
    TP_REDE                VARCHAR(3000),   -- TIPLOC, como a operadora escreve (ex.: L, G)
    NU_IMEI                VARCHAR(3000),   -- CODIME (zerado entra como NULL, como no resumo de números)
    NU_CODOPE              VARCHAR(3000),   -- CODOPE, como a operadora escreve
    TP_TIPO_ACESSO         VARCHAR(3000),   -- TIPIAC, como a operadora escreve
    TP_ORIGEM              VARCHAR(3000),   -- ORICHA, como a operadora escreve
    SG_UF                  VARCHAR(10),     -- ESTADO
    TP_FONTE               VARCHAR(20) NOT NULL DEFAULT 'planilha',
    TX_CHAVE               VARCHAR(64) NOT NULL,
    CONSTRAINT FK300_TEL_TB08_TEL_TB01 FOREIGN KEY (ID_TELEFONIA)
        REFERENCES TEL_TB01_TELEFONIA (ID_TELEFONIA),
    CONSTRAINT FK301_TEL_TB08_TEL_TB02 FOREIGN KEY (ID_ARQUIVO_TELEFONIA)
        REFERENCES TEL_TB02_ARQUIVOS (ID_ARQUIVO_TELEFONIA)
);

-- a tela lista em ordem cronológica e filtra por linha/IP/CGI
CREATE INDEX IF NOT EXISTS IX306_TEL_TB08_TELEFONIA_DATA
    ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, DT_INICIO);
CREATE INDEX IF NOT EXISTS IX307_TEL_TB08_NU_LINHA
    ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, NU_LINHA);
CREATE INDEX IF NOT EXISTS IX308_TEL_TB08_NU_CGI
    ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, NU_CGI)
    WHERE NU_CGI IS NOT NULL AND NU_CGI <> '';
-- a sessão é o intervalo daquela linha naquele IP: reimportar o mesmo arquivo colide aqui
CREATE UNIQUE INDEX IF NOT EXISTS UX108_TEL_TB08_TELEFONIA_CHAVE
    ON TEL_TB08_DADOS_MOVEIS (ID_TELEFONIA, TX_CHAVE);
