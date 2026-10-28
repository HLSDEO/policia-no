-- ===================================================================================================================
-- Pessoas do vazamento (busca fuzzy por nome)
--
-- Tabelas: PES_TB01_PESSOAS
-- ===================================================================================================================

-- PUBLIC no fim do search_path: e onde ficam pg_trgm, fuzzystrmatch e pgcrypto —
-- sem ele, gin_trgm_ops e crypt()/gen_salt() nao sao encontrados sem qualificar.
SET search_path TO DADOS_EXTRAIDOS, PUBLIC;

CREATE TABLE PES_TB01_PESSOAS (
    ID_PESSOA      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NU_CPF         CHAR(11),  -- CPF
    NO_PESSOA      VARCHAR(150),  -- Nome Completo
    DS_SEXO        VARCHAR(20),  -- Sexo / Genero
    DT_NASCIMENTO  DATE  -- Data de Nascimento
);

CREATE INDEX IX08_PES_TB01_NU_CPF ON PES_TB01_PESSOAS (NU_CPF);
CREATE INDEX IX25_PES_TB01_NO_PESSOA_TRGM ON PES_TB01_PESSOAS USING gin (NO_PESSOA gin_trgm_ops);

RESET search_path;
