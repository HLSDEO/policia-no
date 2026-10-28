-- ===================================================================================================================
-- Schemas e extensoes do banco "analyser"
--
-- Este arquivo e o unico pre-requisito dos demais: cria os quatro schemas e as
-- extensoes usadas pelas migracoes seguintes. Ver db/bootstrap/00_aplicar_migracoes.sh
-- para a ordem em que db/init/*.sql roda na primeira subida do Postgres.
-- ===================================================================================================================

-- Schemas do banco "analyser"
-- DADOS_REFERENCIA: tabelas de domínio (listas de apoio / de-para)
-- DADOS_EXTRAIDOS : dados brutos extraídos (vazamento Detran)

CREATE SCHEMA IF NOT EXISTS DADOS_REFERENCIA;

CREATE SCHEMA IF NOT EXISTS DADOS_EXTRAIDOS;

-- =====================================================================
-- Schema INVESTIGACAO — extrações forenses (UFDR/Cellebrite) por inquérito
-- Origem: arquivos .ufdr apontados na página "Extração de dados de
-- investigação" da interface web. Formato verificado contra um extrato
-- real (Cellebrite Physical Analyzer, XML namespace pa.cellebrite.com/report/2.0):
-- ZIP contendo report.xml, com <model type="Chat"> aninhando Participants
-- (Party) e Messages (InstantMessage) inline.
--
-- Por ora só WhatsApp é extraído (Source/SourceApplication = "WhatsApp"),
-- mas o schema é genérico o bastante para outros apps de mensagem no futuro.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS INVESTIGACAO;

-- =====================================================================
-- Extensões de busca fuzzy — usadas pelas ferramentas de IA em
-- api/funcoes/ia_investigacao.py para casar nomes/apelidos (contato do
-- celular, texto livre do investigador) contra as bases de referência
-- (PES_TB01_PESSOAS, TSE_TB01_CANDIDATOS), que não têm grafia exata.
--
-- pg_trgm: similarity() por trigramas (tolera erro de digitação/abreviação).
-- fuzzystrmatch: dmetaphone() por som (tolera grafia diferente do mesmo nome).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- =====================================================================
-- Usuários e sessões — autenticação da API (ver api/funcoes/auth_utils.py).
-- Sessão é server-side (token opaco em cookie httpOnly; só o hash do token
-- fica no banco), não JWT — permite revogar sessão (logout/desativação)
-- sem precisar de blocklist.
--
-- pgcrypto/crypt()+gen_salt('bf') gera hash bcrypt padrão ($2a$/$2b$...),
-- compatível com a lib bcrypt do Python usada no resto do código — só é
-- usado aqui pra semear o admin inicial sem precisar rodar Python à parte.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS SEGURANCA;
