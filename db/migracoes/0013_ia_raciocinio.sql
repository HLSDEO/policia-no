-- Trilha do turno de IA (raciocínio do modelo, quando o provedor devolve, mais as ferramentas
-- chamadas em cada rodada), exibida recolhida na própria bolha da resposta. Correspondentes
-- declarativos em db/init/24_ia.sql (chat do inquérito) e db/init/23_casos.sql (chat do caso).
-- Nulo nas mensagens já existentes: elas simplesmente não ganham o botão.
ALTER TABLE INVESTIGACAO.IA_TB02_MENSAGENS ADD COLUMN TX_RACIOCINIO JSONB;
ALTER TABLE INVESTIGACAO.CAS_TB07_MENSAGENS_IA ADD COLUMN TX_RACIOCINIO JSONB;
