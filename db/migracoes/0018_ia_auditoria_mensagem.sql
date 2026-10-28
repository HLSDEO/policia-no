-- Liga cada registro de auditoria à mensagem do assistente que ele registra, para a bolha da
-- resposta poder mostrar tokens/tempo/custo daquele turno (antes só existia o agregado da
-- conversa). Correspondente declarativo em db/init/24_ia.sql.
--
-- Sem FK de propósito: a coluna aponta ora para IA_TB02_MENSAGENS (chat do inquérito), ora para
-- CAS_TB07_MENSAGENS_IA (chat do caso) — a mesma ambiguidade que já vale para ID_CONVERSA_IA. E
-- apagar uma conversa não pode apagar a auditoria dela: o registro sobrevive à mensagem.
ALTER TABLE INVESTIGACAO.IA_TB04_AUDITORIA ADD COLUMN ID_MENSAGEM BIGINT;

CREATE INDEX IX297_IA_TB04_ID_MENSAGEM ON INVESTIGACAO.IA_TB04_AUDITORIA (ID_MENSAGEM);

-- Backfill dos turnos já gravados: a auditoria é registrada logo DEPOIS de a resposta ser
-- gravada, na mesma thread e com o now() do mesmo servidor — então a mensagem do turno é a
-- última do assistente daquela conversa antes de DT_CHAMADA. A janela de 1 minuto evita casar
-- um registro de turno que falhou (sem resposta gravada) com a resposta de um turno anterior;
-- o DISTINCT ON garante uma auditoria por mensagem quando dois registros disputam a mesma.
WITH candidatos AS (
    SELECT a.id_auditoria,
           m.id_mensagem_ia
      FROM INVESTIGACAO.IA_TB04_AUDITORIA a
      JOIN LATERAL (
           SELECT m.id_mensagem_ia
             FROM INVESTIGACAO.IA_TB02_MENSAGENS m
            WHERE m.id_conversa_ia = a.id_conversa_ia
              AND m.tp_papel = 'assistant'
              AND m.dt_mensagem <= a.dt_chamada
              AND a.dt_chamada - m.dt_mensagem < INTERVAL '1 minute'
            ORDER BY m.dt_mensagem DESC, m.id_mensagem_ia DESC
            LIMIT 1
      ) m ON true
     WHERE a.no_origem = 'chat_inquerito'
       AND a.cd_status = 'ok'
       AND a.id_conversa_ia IS NOT NULL
), unicos AS (
    SELECT DISTINCT ON (id_mensagem_ia) id_auditoria, id_mensagem_ia
      FROM candidatos
     ORDER BY id_mensagem_ia, id_auditoria DESC
)
UPDATE INVESTIGACAO.IA_TB04_AUDITORIA a
   SET ID_MENSAGEM = u.id_mensagem_ia
  FROM unicos u
 WHERE u.id_auditoria = a.id_auditoria;
