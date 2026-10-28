-- migracao: sem-transacao
-- Índice do chip de aparelho da tela "Comparar celulares" (funcoes/comparar_celulares_utils.py,
-- listar_aparelhos): "quantos arquivos com hash tem ESTA extração".
--
-- O IX250 tem CO_HASH_MD5 na frente e não atende uma busca por ID_EXTRACAO: a contagem ou varria
-- o índice parcial inteiro (de TODOS os inquéritos do banco) ou caía em bitmap heap scan na
-- tabela larga — centenas de milhares de linhas por celular, o suficiente para estourar o
-- statement_timeout de 60 s e a tela abrir com "consulta demorou mais do que o limite".
-- Com a extração na frente, a contagem vira index-only scan direto nas linhas do inquérito.
--
-- Correspondente declarativo em db/init/20_arquivos.sql (abaixo de ARQ_TB01_ARQUIVOS).

CREATE INDEX CONCURRENTLY IF NOT EXISTS IX263_ARQ_TB01_EXTRACAO_HASH
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO, CO_HASH_MD5)
    WHERE CO_HASH_MD5 IS NOT NULL;
