-- Índice do casamento anexo -> arquivo do aparelho, usado ao montar os relatórios de análise
-- (funcoes/relatorios_utils.py: listar_whatsapp_marcado e anexos_de_marcacoes).
--
-- As tabelas de anexo (WPP/SMS/MSG_TB05_ANEXOS) não guardam hash: só o ponteiro para o binário
-- no ZIP (CO_CAMINHO_ZIP). O MD5 que sai embaixo do nome do arquivo no .docx vem da linha
-- correspondente em ARQ_TB01_ARQUIVOS, buscada por (ID_EXTRACAO, CO_CAMINHO_ZIP).
--
-- Sem o índice é uma varredura da tabela inteira POR ANEXO — centenas de milhares de linhas por
-- iPhone vezes as dezenas ou centenas de mensagens marcadas de um relatório. Com ele, cada
-- busca é um index scan que devolve uma linha.
--
-- Parcial em CO_CAMINHO_ZIP IS NOT NULL: arquivo listado só como metadado (binário não extraído)
-- nunca é alvo desta busca, e em iPhone essa fatia é grande.
--
-- Correspondente declarativo em db/init/20_arquivos.sql (abaixo de ARQ_TB01_ARQUIVOS).

CREATE INDEX IF NOT EXISTS IX251_ARQ_TB01_CAMINHO_ZIP
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (ID_EXTRACAO, CO_CAMINHO_ZIP)
    WHERE CO_CAMINHO_ZIP IS NOT NULL;
