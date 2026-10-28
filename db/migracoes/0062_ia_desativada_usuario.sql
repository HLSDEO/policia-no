-- Desligar a IA de um usuário específico (tela Usuários → "Desativar IA").
-- Espelho imperativo de: 07_seguranca_usuarios.sql (USR_TB01_USUARIOS.IN_IA_ATIVA). Idempotente.
--
-- Desativar a conta derruba o acesso inteiro; aqui é só a IA: o analista segue trabalhando, mas
-- nenhuma chamada de IA dele passa (chat, busca automática, análise de documento, relatório) —
-- nem a própria, nem a das Configurações. O portão é o mesmo do flag por delegacia
-- (funcoes/ia_delegacia.py).

SET search_path TO SEGURANCA, PUBLIC;

ALTER TABLE USR_TB01_USUARIOS
    ADD COLUMN IF NOT EXISTS IN_IA_ATIVA BOOLEAN NOT NULL DEFAULT true;

RESET search_path;
