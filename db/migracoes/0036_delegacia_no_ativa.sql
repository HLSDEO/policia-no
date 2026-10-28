-- Botão "usa o nó" por delegacia (ver docs/no_descentralizado.md e 07_seguranca_usuarios.sql).
-- Espelho imperativo da coluna IN_NO_ATIVA em DELEGACIAS. Idempotente.

SET search_path TO SEGURANCA, PUBLIC;

-- TRUE (padrão) = delegacia com nó cadastrado extrai na pasta DO NÓ.
-- FALSE = o central finge que o nó não existe e extrai na pasta local, sem descadastrar nada.
ALTER TABLE DELEGACIAS ADD COLUMN IF NOT EXISTS IN_NO_ATIVA BOOLEAN NOT NULL DEFAULT TRUE;

RESET search_path;
