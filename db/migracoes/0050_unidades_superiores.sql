-- Árvore de unidades superiores das delegacias (ver docs/unidades_prompts.md e db/init/07_seguranca_usuarios.sql).
-- Espelho imperativo de: UNIDADES + coluna ID_UNIDADE em DELEGACIAS, ambos no 07. Idempotente.

SET search_path TO SEGURANCA, PUBLIC;

CREATE TABLE IF NOT EXISTS UNIDADES (
    ID_UNIDADE      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NO_UNIDADE      VARCHAR(300) NOT NULL UNIQUE,
    SG_UNIDADE      VARCHAR(30),
    TP_NIVEL        VARCHAR(20) NOT NULL DEFAULT 'coordenacao',
    ID_UNIDADE_PAI  BIGINT,
    UF              VARCHAR(2),
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT CK31_UNIDADES_TP_NIVEL CHECK (TP_NIVEL IN ('uf', 'coordenacao')),
    CONSTRAINT FK421_UNIDADES_PAI FOREIGN KEY (ID_UNIDADE_PAI)
        REFERENCES UNIDADES (ID_UNIDADE)
);

-- Pai imediato da delegacia na árvore (NULL = sem unidade superior, comportamento antigo).
ALTER TABLE DELEGACIAS ADD COLUMN IF NOT EXISTS ID_UNIDADE BIGINT;

-- DROP primeiro para o re-run não falhar com "constraint já existe" (convenção do 0038).
ALTER TABLE DELEGACIAS DROP CONSTRAINT IF EXISTS FK422_DELEGACIAS_UNIDADES;
ALTER TABLE DELEGACIAS ADD CONSTRAINT FK422_DELEGACIAS_UNIDADES
    FOREIGN KEY (ID_UNIDADE) REFERENCES UNIDADES (ID_UNIDADE) ON DELETE SET NULL;

RESET search_path;
