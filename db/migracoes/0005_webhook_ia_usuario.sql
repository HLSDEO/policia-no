-- Webhook de conclusão da IA por usuário (aviso simples, sem o texto da resposta).
--
-- Cada usuário cadastra a SUA url (Discord/Slack/Teams/n8n/...) em "Meu webhook IA" no menu do
-- usuário (rotas /api/auth/webhook-ia, ver api/funcoes/ia_webhook.py). Quando um turno da IA
-- termina — concluído, erro ou cancelado — o backend dispara um POST JSON para o webhook do
-- SOLICITANTE (quem enviou a pergunta) e do CRIADOR da conversa (se for outra pessoa), desde que
-- cada um tenha URL ativa cadastrada. Sem URL cadastrada, nada é enviado (silencioso).
--
-- Payload intencionalmente simples (só aviso, sem pergunta/resposta completas):
--   {evento, origem, status, id_conversa_ia, id_inquerito|id_caso, titulo, id_processamento,
--    id_usuario_solicitante, erro?, dt_evento, link}
--
-- Correspondente declarativo em db/init/07_seguranca_usuarios.sql (tabela USR_TB07_WEBHOOK_IA).

CREATE TABLE IF NOT EXISTS SEGURANCA.USR_TB07_WEBHOOK_IA (
    ID_USUARIO      BIGINT PRIMARY KEY,
    TX_WEBHOOK_URL  TEXT NOT NULL,
    IN_ATIVO        BOOLEAN NOT NULL DEFAULT true,
    DT_CRIACAO      TIMESTAMP NOT NULL DEFAULT now(),
    DT_ATUALIZACAO  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT FK254_USR_TB07_USR_TB01 FOREIGN KEY (ID_USUARIO)
        REFERENCES SEGURANCA.USR_TB01_USUARIOS (ID_USUARIO) ON DELETE CASCADE
);
