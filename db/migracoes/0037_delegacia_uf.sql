-- UF na gerência de delegacias (ver 07_seguranca_usuarios.sql). Espelho imperativo. Idempotente.

SET search_path TO SEGURANCA, PUBLIC;

ALTER TABLE DELEGACIAS ADD COLUMN IF NOT EXISTS UF VARCHAR(2);

RESET search_path;
