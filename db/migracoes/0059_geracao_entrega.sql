-- Geração de despacho da entrega de nó (docs/no_descentralizado.md, "retomada").
-- Espelho imperativo de: 09_extracoes.sql (NU_GERACAO). Idempotente.

SET search_path TO INVESTIGACAO, PUBLIC;

-- Todo claim do despachante sobe este número; o nó o carimba em cada lote/estado de entrega
-- (funcoes/no_extracao.py) e o central recusa 409 a passada de uma geração anterior
-- (funcoes/no_entrega.py). Sem isto, o redespacho do watchdog (baseado só em tempo) podia
-- limpar a tentativa parcial enquanto uma entrega viva de uma partição de rede recém-curada
-- ainda escrevia — e a extração nascia 'concluida' faltando tudo o que foi apagado.
ALTER TABLE INQ_TB02_EXTRACOES
    ADD COLUMN IF NOT EXISTS NU_GERACAO INTEGER NOT NULL DEFAULT 0;

RESET search_path;
