-- ===================================================================================================================
-- Busca semântica sobre pgvector: VL_VETOR vira `vector` e a indexação ganha heartbeat
--
-- Dois problemas de produção motivam o delta:
--
-- 1) VL_VETOR era REAL[] e a similaridade era calculada em Python: cada busca (ou miss do cache
--    de matriz) relia TODOS os vetores do inquérito e convertia lista-Python em numpy — minutos de
--    CPU e dezenas de GB de RAM num inquérito de 1 milhão de mensagens. Com a extensão vector o
--    produto de cosseno passa a ser feito no Postgres (`vl_vetor <=> q`), e o índice HNSW criado
--    pela API (funcoes/embed_utils.py, _garantir_dimensao) devolve o top-K em milissegundos.
--    A coluna nasce SEM dimensão declarada (a dimensão depende do EMBED_MODEL configurado, que o
--    delta não conhece): a API restringe para vector(<dim>) e cria o HNSW na próxima indexação.
--
-- 2) A trava de indexação media a saúde do job por DT_INICIO: qualquer indexação acima de 60 min
--    era marcada 'erro' por inatividade ENQUANTO o worker antigo continuava vivo, e a nova
--    tentativa partia por cima — jobs duplicados somando carga no mesmo servidor (aconteceu no
--    inquérito com milhões de trechos). DT_HEARTBEAT passa a ser atualizado a cada lote gravado;
--    só é considerada presa a indexação sem progresso recente.
--
-- O CONVERT TYPE reescreve a tabela inteira (11 GB nas dimensões atuais dá alguns minutos de
-- deploy, uma única vez). Corresponde ao declarativo atualizado em db/init/22_embeddings.sql.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

-- SCHEMA public de propósito: o search_path do ALTER abaixo é INVESTIGACAO primeiro, e uma
-- CREATE EXTENSION sem schema iria para lá — onde os tipos/operators da API (conexão com
-- search_path padrão) não resolveriam `::vector` nem vector_dims(). Mesma regra do pg_trgm.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- heartbeat do worker: a trava por inatividade passa a olhar o progresso, não a idade da fila
ALTER TABLE EMB_TB02_INDEXACOES
    ADD COLUMN IF NOT EXISTS DT_HEARTBEAT TIMESTAMP NOT NULL DEFAULT now();

-- REAL[] '{0.1,0.2}' -> vector '[0.1,0.2]' (sem dimensão fixa; a API fixa na 1ª indexação)
ALTER TABLE EMB_TB01_EMBEDDINGS
    ALTER COLUMN VL_VETOR TYPE vector
    USING (replace(replace(VL_VETOR::text, '{', '['), '}', ']'))::vector;

RESET search_path;
