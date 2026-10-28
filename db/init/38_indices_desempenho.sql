-- Índices que faltavam para as telas mais usadas.
--
-- Não são "otimização preventiva": cada um abaixo corresponde a um WHERE ou ORDER BY que hoje
-- existe no código e não tem índice que sirva. O caso mais gritante é SMS_TB02_CONVERSAS, que
-- não tem índice NENHUM — nem em ID_EXTRACAO — enquanto as tabelas irmãs de WhatsApp
-- (UQ_WPP_TB02_EXTRACAO_CHAT, 10_whatsapp.sql) e de mensageiros (IX58, 12_mensageiros.sql) têm.
-- Chave estrangeira no Postgres NÃO cria índice, então a listagem de conversas de SMS e todo o
-- ramo SMS da linha do tempo vinham fazendo varredura sequencial.
--
-- IF NOT EXISTS em todos: este arquivo precisa poder ser aplicado à mão num banco que já existe
-- (ver db/init/README.md — os scripts de init só rodam sozinhos com o volume vazio).
--
-- EM PRODUÇÃO, com o sistema no ar, aplique um a um trocando "CREATE INDEX IF NOT EXISTS" por
-- "CREATE INDEX CONCURRENTLY IF NOT EXISTS": sem CONCURRENTLY o CREATE INDEX trava a tabela
-- contra escrita durante toda a construção, e ARQ_TB01/WPP_TB04 têm centenas de milhares de
-- linhas por extração. CONCURRENTLY não roda dentro de bloco de transação, e é por isso que ele
-- não está escrito assim aqui — este arquivo é executado inteiro pelo runner de migrações.
-- Mesma observação já registrada em 37_indices_busca_trgm.sql.

-- INVESTIGACAO.* ficam em INVESTIGACAO (ver db/init/01_schemas.sql), mesma convenção do 37_.
SET search_path TO INVESTIGACAO, PUBLIC;

-- ---------------------------------------------------------------------------
-- Conversas de SMS: a tabela não tinha índice algum
-- ---------------------------------------------------------------------------
-- funcoes/investigacao_utils.py listar_conversas(fonte='sms') e o ramo SMS de listar_timeline
-- filtram por ID_EXTRACAO.
CREATE INDEX IF NOT EXISTS IX231_SMS_TB02_EXTRACAO ON SMS_TB02_CONVERSAS (ID_EXTRACAO);

-- ---------------------------------------------------------------------------
-- Última mensagem de cada conversa
-- ---------------------------------------------------------------------------
-- listar_conversas monta a prévia com uma subconsulta correlacionada POR CONVERSA:
--     SELECT tx_mensagem ... WHERE id_conversa = c.id_conversa
--     ORDER BY dt_mensagem DESC NULLS LAST LIMIT 1
-- (investigacao_utils.py, ~linha 3403). Os índices existentes são (ID_CONVERSA) e (DT_MENSAGEM)
-- separados: nenhum dos dois evita ler e ordenar TODAS as mensagens da conversa. Com o composto
-- na mesma ordem do ORDER BY, o Postgres para na primeira linha.
CREATE INDEX IF NOT EXISTS IX232_WPP_TB04_CONVERSA_DATA
    ON WPP_TB04_MENSAGENS (ID_CONVERSA, DT_MENSAGEM DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS IX233_SMS_TB04_CONVERSA_DATA
    ON SMS_TB04_MENSAGENS (ID_CONVERSA, DT_MENSAGEM DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS IX234_MSG_TB04_CONVERSA_DATA
    ON MSG_TB04_MENSAGENS (ID_CONVERSA, DT_MENSAGEM DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- Tela Arquivos: filtro e ordenação por data e por tamanho
-- ---------------------------------------------------------------------------
-- _filtros_arquivos (investigacao_utils.py, ~3792) filtra por DT_MODIFICACAO e QT_BYTES, e
-- _ORDENACOES_ARQUIVO ordena por esses mesmos campos ("recentes"/"antigos", "maiores"/"menores").
-- Os índices que existem cobrem categoria, nome e caminho — nenhum cobre estes dois.
CREATE INDEX IF NOT EXISTS IX235_ARQ_TB01_EXTRACAO_MODIFICACAO
    ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, DT_MODIFICACAO DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS IX236_ARQ_TB01_EXTRACAO_BYTES
    ON ARQ_TB01_ARQUIVOS (ID_EXTRACAO, QT_BYTES DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- E-mails e notas: ordenação por data dentro da extração
-- ---------------------------------------------------------------------------
-- Ambas as tabelas só tinham índice em (ID_EXTRACAO), enquanto as listagens ordenam por data.
-- As demais tabelas da linha do tempo (LIG, NTF, LOC, NAV, CNX, CAL…) já têm o composto
-- equivalente; estas duas ficaram para trás.
CREATE INDEX IF NOT EXISTS IX237_EML_TB01_EXTRACAO_DATA
    ON EML_TB01_EMAILS (ID_EXTRACAO, DT_EMAIL DESC NULLS LAST);
-- listar_notas ordena por COALESCE(dt_modificacao, dt_criacao) DESC NULLS LAST
-- (investigacao_utils.py:5030), então o índice tem que ser sobre a MESMA expressão — um índice
-- em DT_MODIFICACAO sozinho o planner não reconhece.
-- Ressalva honesta: a listagem filtra pelo inquérito (JOIN com INQ_TB02), não pela extração, e
-- só passa ID_EXTRACAO quando a tela restringe a um aparelho. Este índice resolve esse caso e
-- ajuda a junção; na visão do inquérito inteiro a ordenação final ainda acontece.
CREATE INDEX IF NOT EXISTS IX238_NOT_TB01_EXTRACAO_DATA
    ON NOT_TB01_NOTAS (ID_EXTRACAO, (COALESCE(DT_MODIFICACAO, DT_CRIACAO)) DESC NULLS LAST);
