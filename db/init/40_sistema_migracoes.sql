-- ===================================================================================================================
-- Registro de migracoes de schema aplicadas (ledger)
--
-- Tabela: SIS_TB01_MIGRACOES
--
-- Este arquivo (db/init/) descreve o schema COMPLETO e so roda em volume vazio. As mudancas de
-- schema num banco que JA existe entram como arquivos numerados em db/migracoes/, e o runner
-- api/funcoes/migrar_utils.py aplica cada um uma vez, gravando aqui o que ja rodou.
--
-- Numa instalacao nova, db/bootstrap/00_aplicar_migracoes.sh registra todos os deltas de
-- db/migracoes/ como DS_ORIGEM = 'bootstrap' (sem executa-los: o schema acima ja os contem),
-- para o runner nao tentar reaplica-los.
--
-- Schema SEGURANCA porque isto e metadado de infra do proprio servidor, ao lado de sessoes e
-- auditoria. Ver docs/atualizacao.md.
-- ===================================================================================================================

SET search_path TO SEGURANCA, PUBLIC;

CREATE TABLE IF NOT EXISTS SIS_TB01_MIGRACOES (
    NU_MIGRACAO      INTEGER      NOT NULL PRIMARY KEY,  -- prefixo numerico do arquivo (0007_... -> 7)
    NO_ARQUIVO       VARCHAR(200) NOT NULL,              -- '0007_pix_ligacao_contato.sql'
    CO_HASH          CHAR(64)     NOT NULL,              -- sha256 do conteudo; denuncia arquivo editado apos aplicado
    DS_ORIGEM        VARCHAR(20)  NOT NULL,              -- 'bootstrap' (baseline) | 'runner' (aplicada de fato) | 'manual'
    NU_MS_DURACAO    INTEGER,                            -- quanto levou para aplicar; NULL quando veio do bootstrap
    NO_APLICADA_POR  VARCHAR(100),                       -- 'startup', 'cli', 'updater 1.8.0', login do admin...
    DT_APLICACAO     TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT CK11_SIS_TB01_DS_ORIGEM CHECK (DS_ORIGEM IN ('bootstrap', 'runner', 'manual'))
);

RESET search_path;
