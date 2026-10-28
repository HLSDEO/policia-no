-- ===================================================================================================================
-- Índices de performance para bases de referência (TSE/Pessoas)
-- Motivação: busca fuzzy de identidades contra TSE/PES (~220M linhas) sem índice funcional
-- para CPF normalizado e dmetaphone.
-- Ver api/funcoes/ia_investigacao.py
-- ===================================================================================================================

-- TSE/PES ficam em DADOS_EXTRAIDOS (ver db/init/01_schemas.sql)
SET search_path TO DADOS_EXTRAIDOS, PUBLIC;

-- CPF normalizado do TSE: buscar_candidato_referencia faz
--   regexp_replace(NU_CPF_CANDIDATO, '\D','g') = :cpf
-- sem índice funcional vira Seq Scan em toda TSE_TB01_CANDIDATOS.
-- regexp_replace é STABLE, não IMMUTABLE, então índice direto falha
-- ("functions in index expression must be marked IMMUTABLE").
-- Criamos wrapper IMMUTABLE e índice sobre ele. Também criamos índice
-- simples em translate() como fallback rápido (cobre '.' '-' '/').
CREATE OR REPLACE FUNCTION dados_extraidos.cpf_normalizado(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT regexp_replace($1, '\D', '', 'g')
$$;

CREATE INDEX IF NOT EXISTS IX28_TSE_TB01_CPF_NORM
    ON TSE_TB01_CANDIDATOS ((dados_extraidos.cpf_normalizado(NU_CPF_CANDIDATO)));

CREATE INDEX IF NOT EXISTS IX28B_TSE_TB01_CPF_TRANSLATE
    ON TSE_TB01_CANDIDATOS ((translate(NU_CPF_CANDIDATO, '.-/ ', '')));

-- dmetaphone() é usado como fallback de busca fuzzy quando similarity não atinge threshold.
-- Sem índice, o OR (no_candidato %% termo OR dmetaphone(...) = dmetaphone(termo)) força
-- Seq Scan mesmo com GIN trigram. Índice btree funcional permite Index Scan no braço
-- fonético. PES tem 220M linhas, TSE tem formato variável por ano - ambos se beneficiam.
-- Nota: PES_TB01_PESSOAS é UNLOGGED gigante; CREATE INDEX nela trava por horas - em
-- produção use CREATE INDEX CONCURRENTLY (ver docs/ingestao.md). Em init (DB vazio) o
-- índice regular é aplicado sem lock.
CREATE INDEX IF NOT EXISTS IX29_PES_TB01_DMETA
    ON PES_TB01_PESSOAS ((dmetaphone(NO_PESSOA)));

CREATE INDEX IF NOT EXISTS IX30_TSE_TB01_DMETA_CAND
    ON TSE_TB01_CANDIDATOS ((dmetaphone(NO_CANDIDATO)));

CREATE INDEX IF NOT EXISTS IX31_TSE_TB01_DMETA_URNA
    ON TSE_TB01_CANDIDATOS ((dmetaphone(NO_URNA_CANDIDATO)));

RESET search_path;
