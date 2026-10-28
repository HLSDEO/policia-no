-- Índice parcial para a linha do tempo dos arquivos da Nuvem IPED (coluna TX_LINHA_TEMPO,
-- JSONB gravado por iped_utils._item_para_arquivo). O segmento "arquivo_iped" da timeline
-- unificada (investigacao_utils.listar_timeline) lê cada evento do JSONB por extração; sem
-- índice, cada pedido varre as centenas de milhares de linhas da extração inteira (a coluna
-- só é preenchida pelo IPED — UFDR fica NULL). Correspondente declarativo em
-- db/init/20_arquivos.sql.
CREATE INDEX IF NOT EXISTS IX262_ARQ_TB01_LINHA_TEMPO
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO)
    WHERE TX_LINHA_TEMPO IS NOT NULL;
