# Busca semântica

## Visão geral

A busca semântica permite "achados" por significado, não por palavra exata: o sistema gera
vetores (embeddings) para mensagens e documentos do inquérito e, na hora da busca, ordena os
trechos pela proximidade de significado (cosseno) com a pergunta. Ela alimenta a busca híbrida
do chat de IA (`buscar_hibrida`, via `rag_hibrido`) e a busca semântica em documentos — o
resultado fica indisponível (devolve aviso em vez de resultado) quando o inquérito não foi indexado.

Os embeddings são gerados por API compatível com OpenAI ou pelo servidor Ollama local (ver
Configuração). Os vetores ficam em `EMB_TB01_EMBEDDINGS` como coluna `vector` (pgvector — a imagem
do banco é `pgvector/pgvector:pg16` desde a migração `0048_busca_semantica_pgvector.sql`) e a
similaridade é calculada no próprio Postgres (`ORDER BY vl_vetor <=> q LIMIT n`, servida pelo
índice HNSW `IX93_EMB_TB01_VETOR`). O Python não monta matriz de embeddings: envia só o vetor da
pergunta e recebe o top-K com o texto — foi essa mudança que tirou da API os minutos de CPU e as
dezenas de GB de RAM que o antigo ranking em NumPy custava em inquéritos de 1 milhão de mensagens.

Implementação: `api/funcoes/embed_utils.py` (configuração, geração, indexação em background,
cache, `buscar_semelhantes`) e `api/rotas/investigacao.py` (endpoints de indexação).

## Fluxo de uso

1. **Configurar** o modelo de embeddings nas Configurações (ver seção Configuração) — sem modelo
   (`EMBED_MODEL` vazio), a indexação é pulada silenciosamente (a extração continua concluindo
   normalmente).
2. **Indexar**: ao final de cada extração UFDR, a fase `indexando_semantica` indexa
   automaticamente tudo do inquérito, de forma síncrona e encadeada (`indexar_sincrono` — a
   extração só é dada como concluída depois que a indexação termina; falhas na indexação em si não
   derrubam a extração, ficam registradas no status da própria indexação). Também é possível
   indexar manualmente: o card do inquérito na tela de **Investigação**
   (`web/investigacao.html`/`web/js/investigacao.js`, botão "🧠 Indexar busca semântica" — não há
   controle disso numa tela dedicada de IA) chama
   `POST /api/investigacao/inqueritos/{id}/indexacao-semantica`, que responde `IndexacaoOcupada`
   409 se já houver indexação em andamento para o inquérito e `ErroProvedorIA` 400 se a
   configuração estiver ausente/errada.
3. **Acompanhar**: `GET /api/investigacao/inqueritos/{id}/indexacao-semantica` retorna o status
   mais recente (`cd_status`, `qt_total`, `qt_feitos`, `qt_indexado`); o botão do card faz
   *polling* e se repinta como "🧠 Indexando… feitos/total" enquanto `pendente`/`processando`.
4. **Buscar**: no chat de IA do caso, perguntar algo — os trechos semelhantes voltam com a
   mensagem/documento de origem (referência `[msg:fonte:id]` ou `[doc:id]`, com texto e contexto)
   e a resposta cita as fontes.
5. O cancelamento da indexação acompanha o da extração (a coluna `FL_CANCELAR` de
   `EMB_TB02_INDEXACOES`) — não há botão próprio
   de cancelar indexação isolada da extração na tela hoje.

## Como funciona

### Geração de embeddings

- `gerar_embeddings(textos)` chama a API de embeddings configurada para **uma lista de textos de
  uma vez** e devolve os vetores na ordem de entrada (reordenando pelo `index` que a resposta
  traz) — quem faz o **loteamento** (`_TAMANHO_LOTE_EMBED = 64` textos por chamada) e o `INSERT ...
  ON CONFLICT DO NOTHING` no banco é `_processar_indexacao`, não `gerar_embeddings` em si.
- No modo `api`, a URL/chave usadas são as **próprias da busca semântica**: `EMBED_API_URL`/
  `EMBED_API_KEY` (configuráveis na aba "Busca semântica" da tela de Configurações; sem elas
  configuradas, `_config_embed` cai nas do provedor de chat `AI_API_URL`/`AI_API_KEY`, para não
  exigir uma segunda credencial). No modo `local`, chama o Ollama (`EMBED_LOCAL_URL`, com
  `_garantir_modelo_local` que baixa o modelo via `POST {base}/api/pull` — a API nativa do Ollama,
  fora do `/v1` — com timeout de 1800 s na primeira vez; chamadas seguintes são rápidas porque o
  pull é idempotente). Nos dois modos o modelo de embeddings é sempre `EMBED_MODEL` — o modelo de
  chat configurado em `AI_MODEL` não serve como embedding, são endpoints e formatos diferentes.
- A cada início de indexação, `_processar_indexacao` sonda a dimensão do modelo atual
  (`gerar_embeddings(["."])`) e chama `_garantir_dimensao(dim)`: com a coluna `VL_VETOR` ainda sem
  dimensão (estado logo após a migração 0048), apaga os vetores de outra dimensão
  (`vector_dims(vl_vetor) <> dim`), restringe a coluna para `vector(<dim>)` e constrói o índice
  HNSW `IX93_EMB_TB01_VETOR` com `CREATE INDEX CONCURRENTLY` (não trava buscas durante a
  construção). Cobrir o modelo trocado (ex. ~384 dimensões → `nomic-embed-text`, ~768) sempre foi
  necessário: sem isso os vetores da dimensão antiga ficariam presos na tabela, nunca re-embeddados
  porque a query de pendentes só olha o que **não tem** linha em `EMB_TB01_EMBEDDINGS`.

### O que é indexado

- **Mensagens** das três fontes (WhatsApp `wpp`, SMS `sms`, mensageiro `msg`): o texto da
  mensagem, ou — quando `tx_mensagem` é nulo — a concatenação das transcrições de áudio dos
  anexos da mensagem (`string_agg(..., ' | ')`) — mensagem de voz não tem texto próprio, o único
  conteúdo semântico dela é a transcrição. Só entram mensagens sem embedding ainda (`NOT EXISTS`
  contra `EMB_TB01_EMBEDDINGS` pela chave `tp_fonte`+`id_mensagem`) e com texto não vazio depois de
  `btrim`, **respeitada a política de conteúdo** (abaixo).
- **Documentos** (`INQ_TB04_DOCUMENTOS`, com `tx_erro IS NULL`): documentos grandes são fatiados
  (`_fatiar_documento`) em trechos de até `_TAMANHO_TRECHO_DOCUMENTO` = 4000 caracteres com
  sobreposição de `_SOBREPOSICAO_TRECHO_DOCUMENTO` = 200 (a fatia seguinte recua 200 caracteres
  antes de continuar, para não cortar uma frase relevante bem na borda entre dois trechos); o
  texto do documento entra **inteiro** no `TX_CONTEUDO` — muito além do contexto de qualquer
  modelo de embeddings — então ele vira dezenas de trechos, cada um com seu próprio embedding,
  `tp_fonte = 'doc'`, `id_mensagem = id_documento` e `nr_trecho` sequencial, até o teto de
  `_MAX_TRECHOS_DOCUMENTO` = 400 fatias por documento (o que passar disso fica de fora da busca
  semântica, mas continua no texto completo do documento).

### Política de conteúdo (o que merece ir para o embedding)

A fila de um inquérito grande passa de 3 milhões de trechos e a maior parte não agrega nada à
busca semântica — cada chamada economizada são centenas de ms a menos no Ollama/API. **Nada é
apagado**: o conteúdo filtrado continua na busca literal, na tela e para a IA; só não ganha
vetor. A política vale para o que ainda não foi indexado (o que já tem vetor é aproveitado).

- **Corte por tamanho de mensagem** (`_SQL_MSGS_INDEXAR`): ficam fora mensagens com menos de
  `EMBED_MIN_TEXTO` caracteres (padrão 15; `0` desliga o corte), sem ao menos dois caracteres
  alfanuméricos (só emoji/pontuação) ou que sejam uma URL sozinha.
- **Deduplicação por lote** (`EMBED_DEDUP=0` desliga; padrão ligada): no mesmo lote de 64,
  o texto repetido (corrente encaminhada mil vezes, "bom dia" em série) é embedado **uma** vez
  pela chave de dedup (`_chave_dedup`: caixa baixa + espaços colapsados) e o mesmo vetor é
  gravado para todas as cópias — nenhuma perda de qualidade.
- **Filtro de fatia de documento** (`_fatia_relevante`): fatia com menos de 30% de letras ou sem
  ao menos 3 palavras de 4+ letras é descartada antes da chamada — extrato bancário, planilha
  numérica e lixo de OCR não viram vetor. A estimativa de `QT_TOTAL` não aplica esse filtro
  (continua sendo estimativa de exibição).
- Teste: `api/tools/teste_politica_embed.py` (parte pura sempre; os filtros SQL contra um
  Postgres real quando `EMBED_TEST_PG_DSN` estiver definido).

### Estado e fila

- `EMB_TB02_INDEXACOES` guarda uma linha por rodada de indexação (não uma por inquérito — cada
  chamada cria uma linha nova) com `CD_STATUS` (`pendente`/`processando`/`concluido`/`erro`/
  `cancelado`), `QT_TOTAL`, `QT_FEITOS`, `TX_ERRO`, `DT_INICIO`/`DT_FIM` e `FL_CANCELAR`; o índice
  único parcial `UX45_EMB_TB02_ATIVA` (`WHERE cd_status IN ('pendente','processando')`) garante
  **uma indexação ativa por inquérito** — uma 2ª tentativa concorrente recebe `UniqueViolation` no
  `INSERT`, convertida em `IndexacaoOcupada` por `_criar_linha_indexacao`.
- Indexações abandonadas (status `pendente`/`processando` com `dt_heartbeat` há mais de
  `_MINUTOS_INDEX_STALE` = 60) são liberadas por `_liberar_indexacoes_travadas` — chamada sempre
  antes de criar uma linha nova, marcando a antiga como `erro` (mensagem "Indexação interrompida
  (sem progresso recente)") para destravar o índice único. O heartbeat é gravado no MESMO commit
  de cada lote de `QT_FEITOS`, e a trava olha o heartbeat — não `DT_INICIO`: com `dt_inicio` toda
  indexação legítima acima de 60 min (milhões de trechos em CPU leva horas) era quebrada enquanto
  o worker antigo continuava vivo, e a tentativa nova partia por cima em cima — jobs duplicados
  somando carga no mesmo servidor (aconteceu num inquérito com 3,8 milhões de trechos).
- Os pendentes são lidos por **cursor de servidor** (`_iterar_pendentes`, `itersize` = tamanho do
  lote) e gravados com `executemany` + `ON CONFLICT DO NOTHING` — a RAM do job é constante. A
  versão antiga materializava a lista inteira de pendentes antes do primeiro lote.
- A indexação roda em thread própria (`iniciar_indexacao`) quando disparada manualmente pela tela;
  a extração UFDR usa `indexar_sincrono` (mesma lógica de `_processar_indexacao`, mas bloqueante),
  encadeada inline ao final da extração — o cancelamento da extração também interrompe a indexação,
  checado a cada lote de `_TAMANHO_LOTE_EMBED`.

### Busca (`buscar_semelhantes`)

- Recebe a pergunta, o inquérito e `limite` (padrão 20). Gera o vetor da pergunta (uma chamada de
  embedding) e faz o ranking no Postgres:
  `SELECT ... 1 - (vl_vetor <=> %(q)s::vector) AS score ... WHERE id_inquerito = %(id)s ORDER BY
  vl_vetor <=> %(q)s::vector LIMIT %(lim)s` — servido pelo índice HNSW, com
  `SET LOCAL hnsw.ef_search = max(100, limite×4)` e `hnsw.iterative_scan = relaxed_order` (a busca
  é sempre filtrada por inquérito, e o HNSW filtrado precisa dessa afinação para não voltar com
  menos que o pedido; GUC desconhecido no pgvector da vez é ignorado, não derruba a busca).
- O filtro `vector_dims(vl_vetor) = vector_dims(q)` deixa de fora linhas de outra dimensão
  (possível entre a migração 0048 e a primeira reindexação): o resultado vazio é distinguido por
  uma sonda — inquérito sem vetores devolve "ainda não indexado", com vetores de outra dimensão
  devolve "reindexe o inquérito".
- O top-K volta com o texto (`TX_TRECHO`) na mesma consulta — `count(*) OVER ()` traz o
  `total_indexado` sem passada extra. A referência devolvida é `[doc:id]` para documento ou
  `[msg:fonte:id]` para mensagem — é essa string que o modelo usa para citar a fonte na resposta.
- Falha no banco (extensão ausente, coluna `REAL[]` pré-migração) devolve aviso descritivo em vez
  de erro — a busca literal e o resto do RAG híbrido continuam funcionando.

### O que aconteceu com o cache de matriz

- Não existe mais. O cache LRU de matrizes NumPy (4 inquéritos / 1 GB / TTL 30 min, invalidado por
  assinatura `count+max(id)`) só existia para amortizar o custo de reler todos os vetores a cada
  pergunta — custo que o pgvector removeu da raiz. As knobs `CACHE_EMBED_INQUERITOS`/
  `CACHE_EMBED_MB` saíram da tela Desempenho com a migração 0048; valores residuais no `.env` são
  ignorados.

## Modelo de dados

- `EMB_TB01_EMBEDDINGS` (`db/init/22_embeddings.sql`):
  - `ID_EMBEDDING` (PK identity), `ID_INQUERITO` (FK `FK113` → `INQ_TB01_INQUERITOS`), `TP_FONTE
    VARCHAR(3)` (`wpp`/`sms`/`msg`/`doc`), `ID_MENSAGEM` (chave primária da origem — id da
    mensagem ou do documento), `NR_TRECHO BIGINT DEFAULT 0` (0 para mensagens; sequencial para
    trechos de documento), `TX_TRECHO TEXT` (o texto que foi embeddado), `VL_VETOR vector`
    (pgvector; nasce sem dimensão na migração 0048 e a API restringe para `vector(<dim>)` na
    indexação — ver `_garantir_dimensao`).
  - Índices: `IX91_EMB_TB01_INQUERITO` (por inquérito), `UX43_EMB_TB01_MSG` — único por
    `(ID_INQUERITO, TP_FONTE, ID_MENSAGEM, NR_TRECHO)` (a chave ganhou `NR_TRECHO` quando os
    documentos passaram a ser fatiados) — e `IX93_EMB_TB01_VETOR`, o HNSW
    (`vector_cosine_ops`) criado por `_garantir_dimensao` fora do DDL declarativo porque depende
    da dimensão do modelo configurado.
- `EMB_TB02_INDEXACOES` (`db/init/22_embeddings.sql`): `ID_INDEXACAO` (PK), `ID_INQUERITO` (FK `FK114`),
  `CD_STATUS VARCHAR(20) DEFAULT 'pendente'`, `QT_TOTAL`, `QT_FEITOS`, `TX_ERRO`, `DT_INICIO
  TIMESTAMP DEFAULT now()`, `DT_HEARTBEAT TIMESTAMP DEFAULT now()` (progresso real do worker),
  `DT_FIM`, `FL_CANCELAR BOOLEAN DEFAULT false`; índice
  `IX92_EMB_TB02_INQUERITO` e único parcial `UX45_EMB_TB02_ATIVA` (por inquérito, só
  `pendente`/`processando`).

## Configuração

- `EMBED_MODE` — `api` (padrão) ou `local` (Ollama).
- `EMBED_API_URL` / `EMBED_API_KEY` — URL e chave **próprias da busca semântica** no modo `api`
  (aba "Busca semântica" da tela de Configurações). Em branco, `_config_embed` cai nas do provedor
  de chat (`AI_API_URL` / `AI_API_KEY`), para não exigir uma segunda credencial.
- `EMBED_LOCAL_URL` — URL do Ollama (padrão `http://ollama:11434`, `_EMBED_LOCAL_URL_PADRAO`); o
  container `ollama` sobe no `docker compose` e `EMBED_MODE=local` ativa o download automático do
  modelo na 1ª indexação.
- `EMBED_MODEL` — modelo de embeddings; **vazio = busca semântica desabilitada** (indexação
  pulada silenciosamente; `buscar_hibrida` do chat continua chamável mas falha com erro descritivo).
- `OLLAMA_KEEP_ALIVE` (lido pelo compose, padrão `30m`) — mantém o modelo carregado no Ollama entre
  buscas; sem ele o modelo descarrega a cada 5 min ocioso e a pergunta seguinte paga a recarga.
- `EMBED_MIN_TEXTO` — comprimento mínimo de mensagem para o índice semântico (padrão 15; `0`
  desliga o corte por tamanho; valor inválido volta ao padrão).
- `EMBED_DEDUP` — `0`/`false`/`nao`/`off` desliga a deduplicação de lote (padrão ligada).

## Referências

- `README.md` na raiz (visão geral); índice em `docs/README.md`.
- `api/funcoes/embed_utils.py` — configuração, geração, indexação em background/síncrona,
  `_garantir_dimensao` (coluna + HNSW), `buscar_semelhantes` (pgvector).
- `api/rotas/investigacao.py` — `POST`/`GET /api/investigacao/inqueritos/{id}/indexacao-semantica`.
- `api/tools/teste_politica_embed.py` — teste da política de conteúdo (knobs, dedup, fatias; SQL
  dos filtros de pendentes contra Postgres real com `EMBED_TEST_PG_DSN`).
- `db/init/22_embeddings.sql` — `EMB_TB01_EMBEDDINGS` (com `NR_TRECHO` e `VL_VETOR vector`) e
  `EMB_TB02_INDEXACOES` (com `FL_CANCELAR` e `DT_HEARTBEAT`).
- `db/migracoes/0048_busca_semantica_pgvector.sql` — `CREATE EXTENSION vector`, conversão
  `REAL[] → vector` e `DT_HEARTBEAT` nos bancos que já existem.
- `web/investigacao.html` / `web/js/investigacao.js` — botão "Indexar busca semântica" no card do
  inquérito.
- Consumo: `api/funcoes/rag_hibrido.py` (busca híbrida do chat — `buscar_hibrida`) e `api/funcoes/documentos_utils.py` (busca semântica dentro de um Caso).
- Relacionados: `configuracoes_ia.md` (tela onde `EMBED_MODE`/`EMBED_MODEL` são editados),
  `chat_ia.md` (uso da ferramenta no chat), `extração_ufdr.md` (fase `indexando_semantica`).
