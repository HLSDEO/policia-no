-- ===================================================================================================================
-- Índices GIN trigram para buscas livres (ILIKE '%termo%') + índices compostos de performance
--
-- Motivação: api/funcoes/ia_investigacao.py e api/funcoes/ufdr_dominios_utils.py fazem
--   ILIKE '%termo%' em 12+ domínios (NTF, NAV, LIG, APP, CNX, CAL, COO, USO, etc.) mas
--   apenas BSC, RDS, NOT, ARQ, WPP/SMS/MSG e WIFI já tinham gin_trgm_ops — os demais
--   caíam em Seq Scan (ex.: USO_TB01 413k linhas, NAV centenas de k). Ver relatório
--   de bugs 08/2025. Também adiciona índices compostos para paginação e auditoria.
-- Ver api/funcoes/ia_investigacao.py:buscar_pessoa_referencia (similarity > 0.3)
--     api/funcoes/ufdr_dominios_utils.py:listar_dominio
-- ===================================================================================================================

-- INVESTIGACAO.* ficam em INVESTIGACAO (ver db/init/01_schemas.sql)
SET search_path TO INVESTIGACAO, PUBLIC;

-- Busca livre: cada um desses campos é usado em "WHERE campo ILIKE '%termo%'" sem
-- índice funcional, forçando Seq Scan. GIN trigram permite Index Scan mesmo com
-- wildcard nos dois lados. Criação em DB vazio (init) é instantânea; em produção
-- com dados use CREATE INDEX CONCURRENTLY.
CREATE INDEX IF NOT EXISTS IX210_NTF_TX_CONTEUDO_TRGM ON NTF_TB01_NOTIFICACOES USING gin (TX_CONTEUDO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX211_NTF_PACOTE_TRGM ON NTF_TB01_NOTIFICACOES USING gin (NO_PACOTE_APP gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX212_NAV_URL_TRGM ON NAV_TB01_PAGINAS_VISITADAS USING gin (TX_URL gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX213_NAV_TITULO_TRGM ON NAV_TB01_PAGINAS_VISITADAS USING gin (TX_TITULO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX214_LIG_CONTATO_TRGM ON LIG_TB01_LIGACOES USING gin (NO_CONTATO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX215_LIG_TELEFONE_TRGM ON LIG_TB01_LIGACOES USING gin (NU_TELEFONE_CONTATO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX216_APP_PACOTE_TRGM ON APP_TB01_APLICATIVOS USING gin (NU_PACOTE gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX217_APP_NOME_TRGM ON APP_TB01_APLICATIVOS USING gin (NO_APLICATIVO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX218_COO_DOMINIO_TRGM ON COO_TB01_COOKIES USING gin (NO_DOMINIO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX219_USO_PACOTE_TRGM ON USO_TB01_USO_APPS USING gin (NU_PACOTE gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX220_USO_APLICATIVO_TRGM ON USO_TB01_USO_APPS USING gin (NO_APLICATIVO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX221_CAL_ASSUNTO_TRGM ON CAL_TB01_CALENDARIO USING gin (TX_ASSUNTO gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX222_CAL_DETALHES_TRGM ON CAL_TB01_CALENDARIO USING gin (TX_DETALHES gin_trgm_ops);
CREATE INDEX IF NOT EXISTS IX223_WPP_CONTATO_TRGM ON WPP_TB01_CONTATOS USING gin (NO_CONTATO gin_trgm_ops);

-- Auditoria IA: filtro frequente por inquérito + ordenação por data
-- api/funcoes/ia_auditoria.py filtra WHERE id_inquerito=%s AND dt_chamada BETWEEN ... ORDER BY dt_chamada DESC
CREATE INDEX IF NOT EXISTS IX224_AUD_INQ_DT ON IA_TB04_AUDITORIA (ID_INQUERITO, DT_CHAMADA DESC);

-- Relações de entidades: subquery correlacionada em busca_automatica_utils.py
--   WHERE (origem=id OR destino=id) AND cd_revisao<>'descartada' sem índice composto
CREATE INDEX IF NOT EXISTS IX225_REL_ORIGEM_REVISAO ON ENT_TB03_RELACOES (ID_ENTIDADE_ORIGEM, CD_REVISAO);
CREATE INDEX IF NOT EXISTS IX226_REL_DESTINO_REVISAO ON ENT_TB03_RELACOES (ID_ENTIDADE_DESTINO, CD_REVISAO);

-- Extrações: paginação e filtro por status (investigacao_utils.py:listar_extracoes)
CREATE INDEX IF NOT EXISTS IX227_EXTRACAO_STATUS ON INQ_TB02_EXTRACOES (ID_INQUERITO, CD_STATUS) WHERE CD_STATUS IN ('pendente','processando');
CREATE INDEX IF NOT EXISTS IX228_EXTRACAO_UPLOAD ON INQ_TB02_EXTRACOES (DT_UPLOAD DESC);

-- TSE: filtro por ano/UF em buscar_candidato_referencia (api/funcoes/ia_investigacao.py:2149)
-- O índice simples em NU_ANO_ELEICAO já acelera, mas composto com SG_UF evita BitmapAnd
SET search_path TO DADOS_EXTRAIDOS, PUBLIC;

CREATE INDEX IF NOT EXISTS IX229_TSE_ANO_UF ON TSE_TB01_CANDIDATOS (NU_ANO_ELEICAO, SG_UF);
CREATE INDEX IF NOT EXISTS IX230_TSE_CARGO ON TSE_TB01_CANDIDATOS (CD_CARGO);

RESET search_path;
