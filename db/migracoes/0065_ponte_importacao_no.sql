-- Importação remota de RIF/SIMBA/IPED (docs/no_descentralizado.md, "nó importa"): a extração
-- passa a ser processada no servidor da delegacia e entregue pelo canal de entrega, que é
-- todo chaveado por ID_EXTRACAO (lotes e estados em /api/no/entrega/<id>/...). Para o nó
-- processar um RIF/extrato/caso IPED, o central cria ANTES do despacho uma linha-ponte em
-- INQ_TB02_EXTRACOES (o "claim" do canal) e amarra a linha própria da fonte:
--   NO_FONTE      'rif' | 'simba' | 'iped' (NULL = extração .ufdr comum, o caso de sempre);
--   ID_IMPORTACAO id_rif / id_extrato / id_extracao_iped — a ponte aponta para a tabela certa.
-- O replay do canal (canal_extracao.reproduzir_lote) resolve a linha-pai por esta coluna, sem
-- depender de chave natural, e o fechamento da entrega (no_entrega) sabe o que finalizar.
-- Idempotente: ADD COLUMN IF NOT EXISTS.

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB02_EXTRACOES ADD COLUMN IF NOT EXISTS NO_FONTE      VARCHAR(10);
ALTER TABLE INQ_TB02_EXTRACOES ADD COLUMN IF NOT EXISTS ID_IMPORTACAO BIGINT;

-- uma linha-ponte por importação de fonte (denúncia de despacho duplicado na tela de Servidores)
CREATE INDEX IF NOT EXISTS IX02_INQ_TB02_FONTE_IMPORT
    ON INQ_TB02_EXTRACOES (NO_FONTE, ID_IMPORTACAO)
    WHERE NO_FONTE IS NOT NULL;
