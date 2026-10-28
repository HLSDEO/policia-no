-- Índice do cruzamento da tela "Comparar celulares" (funcoes/comparar_celulares_utils.py):
-- "quais hashes aparecem em mais de uma extração deste inquérito".
--
-- Sem ele o GROUP BY varre ARQ_TB01_ARQUIVOS inteira — a tabela é larga (caminho, OCR, MIME) e
-- tem centenas de milhares de linhas por iPhone, então cada comparação leria dezenas de MB de
-- heap para usar duas colunas. Com CO_HASH_MD5 na frente, o passe dos candidatos vira
-- index-only scan e a busca do detalhe por hash (a página em tela) vira index scan.
--
-- Parcial em CO_HASH_MD5 IS NOT NULL: extração ingerida antes de o parser ler MD5/SHA-256 do
-- <file> tem a coluna vazia em todas as linhas, e essas não interessam ao índice.
--
-- Correspondente declarativo em db/init/20_arquivos.sql (abaixo de ARQ_TB01_ARQUIVOS).

CREATE INDEX IF NOT EXISTS IX250_ARQ_TB01_HASH_MD5
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (CO_HASH_MD5, ID_EXTRACAO)
    WHERE CO_HASH_MD5 IS NOT NULL;
