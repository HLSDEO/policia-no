-- Mídia direta do nó para o navegador (docs/no_midia_direta.md).
-- Espelho imperativo de: 46_nos.sql (TX_MIDIA_URL, IN_MIDIA_DIRETA). Idempotente.

SET search_path TO INVESTIGACAO, PUBLIC;

-- O navegador da LAN baixa a evidência direto do nó que a gravou; o central só assina o passe
-- de ~10 min (funcoes/no_ticket.py). Sem TX_MIDIA_URL o caminho direto não existe (o central
-- nem emite ticket) e tudo segue pelo proxy de sempre — o default desligado é proposital:
-- depende de DNS + certificado que a TI provisioning por nó.
ALTER TABLE NO_TB01_SERVIDORES
    ADD COLUMN IF NOT EXISTS TX_MIDIA_URL    VARCHAR(500),
    ADD COLUMN IF NOT EXISTS IN_MIDIA_DIRETA BOOLEAN NOT NULL DEFAULT false;

RESET search_path;
