-- Situacao do inquerito (Ativo / Arquivado) — o "Todos os status" da tela Investigações passa a
-- filtrar a situacao do caso, e nao mais o andamento do processamento.
-- Espelho declarativo em: 08_inqueritos.sql (INQ_TB01_INQUERITOS.TP_SITUACAO). Idempotente.
--
-- Nao e o cd_status do processamento (pendente/processando/concluido) — e o estado do inquerito em
-- si: 'ativo' (em trabalho) ou 'arquivado' (caso encerrado, sai do foco mas continua integro).
-- So o gestor do inquerito (criador ou quem tem permissao in_admin) muda, via
-- PUT /api/investigacao/inqueritos/{id}/situacao. Default 'ativo' preserva os casos existentes.

SET search_path TO INVESTIGACAO, PUBLIC;

ALTER TABLE INQ_TB01_INQUERITOS
    ADD COLUMN IF NOT EXISTS TP_SITUACAO VARCHAR(12) NOT NULL DEFAULT 'ativo';

ALTER TABLE INQ_TB01_INQUERITOS DROP CONSTRAINT IF EXISTS CK_INQ_TB01_TP_SITUACAO;
ALTER TABLE INQ_TB01_INQUERITOS ADD CONSTRAINT CK_INQ_TB01_TP_SITUACAO
    CHECK (TP_SITUACAO IN ('ativo', 'arquivado'));

RESET search_path;
