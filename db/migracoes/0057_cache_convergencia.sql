-- ===================================================================================================================
-- Cache da Convergência entre inquéritos (tabela CONV_TB01_SINAIS)
--
-- A consulta de convergência passou de 100 s em produção (similaridade de nomes por trigrama
-- sobre 30 mil nomes) e morria no statement_timeout de requisição — a tela nunca carregava.
-- O cálculo pesado passa a rodar num laço de fundo (líder, 1 h) e esta tabela guarda os sinais
-- já calculados para as rotas de leitura paginarem com o filtro de acesso aplicado na leitura.
--
-- Correspondente declarativo em db/init/50_cache_convergencia.sql.
-- ===================================================================================================================

CREATE TABLE IF NOT EXISTS INVESTIGACAO.CONV_TB01_SINAIS (
    ID_INQUERITO_A   BIGINT        NOT NULL,
    ID_INQUERITO_B   BIGINT        NOT NULL,
    TP_SINAL         VARCHAR(20)   NOT NULL,
    TP_CONFIANCA     VARCHAR(10)   NOT NULL,
    VL_SCORE         REAL,
    DS_VALOR         TEXT          NOT NULL,
    NO_ENTIDADE_A    VARCHAR(3000),
    NO_ENTIDADE_B    VARCHAR(3000),
    DT_GERACAO       TIMESTAMP     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS IX150_CONV_TB01_PAR   ON INVESTIGACAO.CONV_TB01_SINAIS (ID_INQUERITO_A, ID_INQUERITO_B);
CREATE INDEX IF NOT EXISTS IX151_CONV_TB01_TIPO  ON INVESTIGACAO.CONV_TB01_SINAIS (TP_SINAL);
