-- Um nó pode atender MAIS DE UMA delegacia (ver 46_nos.sql e docs/no_descentralizado.md).
-- O vínculo sai de NO_TB01_SERVIDORES.ID_DELEGACIA (um nó, uma delegacia) e vira a tabela
-- NO_TB02_SERVIDOR_DELEGACIAS, onde "uma delegacia, um nó" passa a ser travado por UX110.
-- Idempotente: em banco novo (init 46 já criou o formato novo) só não encontra a coluna velha.

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS NO_TB02_SERVIDOR_DELEGACIAS (
    ID_SERVIDOR_DELEGACIA BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_SERVIDOR           BIGINT NOT NULL,
    ID_DELEGACIA          BIGINT NOT NULL,
    CONSTRAINT FK403_NO_TB02_NO_TB01 FOREIGN KEY (ID_SERVIDOR)
        REFERENCES NO_TB01_SERVIDORES (ID_SERVIDOR) ON DELETE CASCADE,
    CONSTRAINT FK404_NO_TB02_DELEGACIAS FOREIGN KEY (ID_DELEGACIA)
        REFERENCES SEGURANCA.DELEGACIAS (ID_DELEGACIA) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS UX110_NO_TB02_DELEGACIA ON NO_TB02_SERVIDOR_DELEGACIAS (ID_DELEGACIA);

-- Migra o vínculo antigo, se este banco chegou a ter a coluna velha
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'investigacao'
                 AND table_name = 'no_tb01_servidores'
                 AND column_name = 'id_delegacia') THEN
        EXECUTE 'INSERT INTO investigacao.no_tb02_servidor_delegacias (id_servidor, id_delegacia)
                 SELECT s.id_servidor, s.id_delegacia
                 FROM investigacao.no_tb01_servidores s
                 WHERE s.id_delegacia IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM investigacao.no_tb02_servidor_delegacias v
                                   WHERE v.id_delegacia = s.id_delegacia)';
    END IF;
END $$;

DROP INDEX IF EXISTS UX109_NO_TB01_DELEGACIA;
ALTER TABLE NO_TB01_SERVIDORES DROP CONSTRAINT IF EXISTS FK402_NO_TB01_DELEGACIAS;
ALTER TABLE NO_TB01_SERVIDORES DROP COLUMN IF EXISTS ID_DELEGACIA;

RESET search_path;
