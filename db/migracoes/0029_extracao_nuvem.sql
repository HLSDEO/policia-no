-- Extração de conta na nuvem (UFDR "Google Warrant Return"): o report.xml do Cellebrite traz
-- alguns domínios decodificados, mas a maior parte do conteúdo vive em files/Archives/*.zip e é
-- importada como nuvem (Minha Atividade, Drive, Google Fotos, Histórico de localização…). O flag
-- IN_NUVEM faz o cartão de extração renderizar o bloco de nuvem (sem WhatsApp/SMS/ligações), e os
-- QT_* novos guardam as contagens dos produtos de nuvem que não têm coluna própria em
-- INQ_TB02_EXTRACOES (as telas de nuvem lêem das tabelas, mas o cartão resume por aqui).
-- Correspondente declarativo em db/init/09_extracoes.sql (colunas IN_NUVEM e QT_* no CREATE TABLE).
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN IN_NUVEM BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_ATIVIDADES BIGINT;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_EVENTOS BIGINT;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_ACESSOS BIGINT;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_DISPOSITIVOS BIGINT;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_PERFIL BIGINT;
ALTER TABLE INVESTIGACAO.INQ_TB02_EXTRACOES ADD COLUMN QT_METADADOS_NUVEM BIGINT;
