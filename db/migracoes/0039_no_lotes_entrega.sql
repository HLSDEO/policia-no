-- ===================================================================================================================
-- Entrega de extrações processadas POR NÓ (docs/no_descentralizado.md — "nó extrai, central guarda")
--
-- O nó processa o .ufdr na própria CPU e devolve ao central LOTES JSONL de dados estruturados
-- (funcoes/canal_extracao.py). Esta tabela é o recibo: uma linha por (extração, lote) já
-- reproduzido no banco. Ela dá a IDEMPOTÊNCIA da entrega — o nó, ao retomar depois de uma queda
-- de rede, reenvia do lote que o central ainda não tem; o central que recebe um lote repetido
-- apenas confirma de novo, sem duplicar nenhuma linha.
-- ===================================================================================================================

SET search_path TO INVESTIGACAO, PUBLIC;

CREATE TABLE IF NOT EXISTS NO_TB03_LOTES_ENTREGA (
    ID_LOTE        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ID_EXTRACAO    BIGINT NOT NULL,
    NU_LOTE        INTEGER NOT NULL,
    QT_REGISTROS   INTEGER NOT NULL DEFAULT 0,
    DT_RECEBIDO    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK405_NO_TB03_EXTRACOES FOREIGN KEY (ID_EXTRACAO)
        REFERENCES INQ_TB02_EXTRACOES (ID_EXTRACAO) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS UX111_LOTE_ENTREGA ON NO_TB03_LOTES_ENTREGA (ID_EXTRACAO, NU_LOTE);

RESET search_path;
