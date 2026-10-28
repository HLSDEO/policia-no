-- Trilha parcial do turno enquanto a IA trabalha. O tema Orbit a apresenta ao vivo; ao concluir,
-- a trilha definitiva continua sendo gravada em IA_TB02_MENSAGENS.TX_RACIOCINIO.
-- JSONB mantém o estado entre workers diferentes da API e também alimenta o SSE já existente.
-- Correspondente declarativo em db/init/24_ia.sql (coluna TX_PROGRESSO no CREATE TABLE).
--
-- RENUMERADA de 0021 para 0023. Este arquivo nasceu como 0021_ia_progresso_orbit.sql, no mesmo
-- número de 0021_telefonia_paginacao.sql (colisão do merge do Orbit). Como o prefixo numérico é a
-- identidade da migração no ledger, o runner registrava UM dos dois como NU_MIGRACAO = 21 e pulava
-- o outro em silêncio — e qual dos dois sobrava dependia da ordem em que cada instalação aplicou.
-- Por isso o statement virou IF NOT EXISTS: numa base onde o 0021 aplicado foi este, o 0023 tem
-- que ser inócuo; naquela onde foi o outro, é aqui que a coluna finalmente aparece.
ALTER TABLE INVESTIGACAO.IA_TB03_PROCESSAMENTOS
    ADD COLUMN IF NOT EXISTS TX_PROGRESSO JSONB NOT NULL DEFAULT '[]'::jsonb;
