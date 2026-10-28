-- ===================================================================================================================
-- Cache da Convergência entre inquéritos
--
-- Tabelas: CONV_TB01_SINAIS
--
-- A consulta de convergência (canonicas_utils._QUERY_SINAIS_CONVERGENCIA) cruza a base inteira
-- — similaridade de nomes por trigrama, GPS agrupado por coordenada, co-presença de MACs — e em
-- produção passa de 100 s, estourando o statement_timeout de requisição (a tela não carregava).
-- O resultado é UMA função dos dados, não do usuário: o que muda por usuário é só QUAL par ele
-- pode ver. Então o cálculo pesado roda num laço de fundo (líder, a cada hora) e esta tabela
-- guarda os sinais já calculados; as rotas de leitura pagam um SELECT com o filtro de acesso.
--
-- A tabela NÃO contém restrição de usuário: quem restringe é a leitura (_filtro_acesso_sql nos
-- dois lados do par), exatamente como na consulta direta de antes.
--
-- Delta correspondente em db/migracoes/0057_cache_convergencia.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS CONV_TB01_SINAIS (
    ID_INQUERITO_A   BIGINT        NOT NULL,  -- sempre o menor id do par (mesma convenção da consulta)
    ID_INQUERITO_B   BIGINT        NOT NULL,
    TP_SINAL         VARCHAR(20)   NOT NULL,  -- cpf, telefone, nome, local, co_presenca, ...
    TP_CONFIANCA     VARCHAR(10)   NOT NULL,  -- exata | aproximada
    VL_SCORE         REAL,                    -- similaridade do nome; NULL nos sinais sem grau
    DS_VALOR         TEXT          NOT NULL,  -- o valor do sinal (CPF, coordenada, "a ≈ b")
    NO_ENTIDADE_A    VARCHAR(3000),
    NO_ENTIDADE_B    VARCHAR(3000),
    DT_GERACAO       TIMESTAMP     NOT NULL DEFAULT now()
);

-- A leitura filtra por par (modal de um par) e varre por tipo (filtro de tela); nunca por linha
-- avulsa. Índice de data não precisa: o status lê max(DT_GERACAO) numa tabela de ~100 mil linhas.
CREATE INDEX IF NOT EXISTS IX150_CONV_TB01_PAR   ON CONV_TB01_SINAIS (ID_INQUERITO_A, ID_INQUERITO_B);
CREATE INDEX IF NOT EXISTS IX151_CONV_TB01_TIPO  ON CONV_TB01_SINAIS (TP_SINAL);
