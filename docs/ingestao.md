# Ingestão de dados de referência

## Visão geral

As bases de referência são grandes e ficam **fora do Git**, em `db/data/` (volume `./db:/db:ro` montado no container — ver `docker-compose.yml`). A ingestão cobre quatro conjuntos:

| Conjunto | Origem | Arquivos |
|---|---|---|
| Veículos (Detran) | `db/data/Detran/` | `JBR_Vehicles.csv` (~104M linhas / ~23GB) + `Referencias/` com 15 domínios (marca, modelo, cor, combustível, município…) |
| Pessoas (Serasa) | `db/data/Serasa/` | `basic220M.csv` (~220M linhas): `CPF \| Nome \| Sexo \| Data de Nascimento` |
| Localidades (IBGE) | `db/data/IBGE/` | 10 JSONs crus da API do IBGE: `paises`, `regioes`, `estados`, `mesorregioes`, `microrregioes`, `regioes-intermediarias`, `regioes-imediatas`, `municipios`, `distritos`, `subdistritos` |
| Eleições (TSE) | `db/data/TSE/{ano}/` | para 2014, 2016, 2018, 2020, 2022 e 2024: `candidatos_{ano}.csv`, `votacao_candidato_munzona_{ano}.csv` e `votacao_partido_munzona_{ano}.csv` |

A tela **Ingestão** (`web/ingestao.html`) mostra um card por conjunto, com a contagem de registros já carregados por tabela e um botão **Importar** que roda os scripts de `db/import/` em ordem, em background.

## Fluxo de uso

1. Coloque os arquivos em `db/data/` com os nomes e a estrutura de pastas esperados (ver acima).
2. Abra a tela **Ingestão** (menu *Administração* ou pela busca global — digitar "importar" chega nela). **Só administrador**: o router inteiro declara `Depends(exigir_admin)`. A tela sempre foi de admin, mas as rotas só exigiam `exigir_aprovado` — qualquer analista de qualquer delegacia disparava a reimportação global das bases de referência, uma carga que pesa no banco inteiro e afeta todo mundo ao mesmo tempo.
3. Cada card traz o **total de registros** no cabeçalho e a lista de tabelas do conjunto com a contagem individual, separadas nas seções **Extraídas** (dado de fato) e **Referência** (tabelas de apoio). Quando a tabela ainda não existe, a linha mostra "tabela não criada" em vez de número.
4. Clique em **Importar**. Enquanto o import roda, o botão fica desabilitado como "Importando…" e o card mostra o badge "Em andamento"; a tela faz *polling* de 3 em 3 segundos no endpoint de status até nenhum grupo rodar.
5. Ao terminar, o card mostra o badge **"Última importação OK"** ou **"Falhou"** — no tooltip do badge está a mensagem do resultado (ex.: `02_import_detran.sql: concluído | …`, ou o erro do `psql` que interrompeu a sequência).
6. Se o botão já estiver em "Importando…", um novo disparo não é possível — a rota responde `409` ("Esse grupo já está importando") até o job terminar. Para reimportar (recarregar do zero), apague/trunque as tabelas antes, pois os scripts fazem `COPY`/`INSERT` sem deduplicação.

## Como funciona

A orquestração fica em `api/rotas/ingestao.py`, que define os grupos, e em
`api/funcoes/import_utils.py`, que executa os scripts:

- **`GET /api/ingestao/status`** — para cada grupo, percorre a lista fixa de tabelas
  (`GRUPOS[grupo]["tabelas"]`, com schema, tabela e rótulo) e chama `count_table(schema, tabela)`
  (`api/funcoes/db_utils.py`), que roda `SELECT COUNT(*)` com o nome do schema/tabela em
  minúsculo (o DDL cria os identificadores sem aspas, e o Postgres os dobra para minúsculo) e
  devolve `None` tanto quando a tabela ainda não existe quanto em qualquer outro erro de conexão
  — é o que a tela mostra como "tabela não criada" (ver `banco_dados.md`). Junto, devolve o
  estado do job (`rodando` / `ok` / `mensagem`) via `status_job(grupo)`.
- **`POST /api/ingestao/importar/{grupo}`** — valida o grupo (`404` para desconhecido) e chama
  `iniciar_import`, que cria a linha do job e abre uma **thread daemon** para executar os
  scripts. O guard de "um por grupo" é o **índice único parcial** `UX100_JOB_TB01_ATIVO` da
  tabela de jobs, não um lock de processo: duas chamadas simultâneas (duas abas, ou um `POST`
  direto enquanto o botão já está desabilitado na tela) tentam as duas o `INSERT`, e a que perde
  recebe `JobOcupado` → `409 "Esse grupo já está importando."`. Como o guard é no banco, ele vale
  igual com qualquer número de workers do uvicorn.
- A thread `_executar_scripts` roda os scripts do grupo **em sequência** via `psql` (subprocess,
  conectando em `postgres:5432` — o host/porta internos da rede do compose, não
  `POSTGRES_PORT` do `.env`, que é só a porta publicada no host e nem chega a ser usada aqui),
  com `-v ON_ERROR_STOP=1`, `PGPASSWORD` lido do `.env` a cada disparo (não cacheado, ao contrário
  do pool de `db_utils.py`) e os arquivos em `IMPORT_SCRIPTS_DIR = /app/db/import`. Ela para no
  primeiro script que falhar — a mensagem de erro guardada é só a **última linha** do `stderr` do
  `psql` (`resultado.stderr.strip().splitlines()[-1]`), não a saída inteira. O estado mora em
  **`INVESTIGACAO.JOB_TB01_JOBS`** (`TP_JOB = 'import_referencia'`, o grupo no escopo — ver
  `api/funcoes/jobs_utils.py` e `db/init/41_jobs.sql`), não mais num dicionário de módulo:
  `jobs_utils.progresso` marca o avanço script a script e bate `DT_ULTIMA_ATIVIDADE`, e
  `status_job(grupo)` lê o último job do grupo e o devolve no mesmo formato `{rodando, ok,
  mensagem}` que a tela já lia. Como o rastro está no banco, ele **sobrevive** a um restart da
  API no meio de um import de 23 GB — e o `psql`, sendo processo filho independente, continua
  rodando de qualquer forma.
- A tela (`web/js/ingestao.js`) renderiza os cards (dividindo as tabelas em duas seções,
  **Extraídas** primeiro e **Referência** depois, com subtítulo só quando o grupo tem as duas),
  agrupa as linhas por schema e, enquanto qualquer grupo estiver rodando, agenda o *polling* de
  3s. Falha de disparo abre o modal de erro (`Dialogo.alerta`, `web/js/utils/dialogo.js`).

### Scripts em `db/import/` (ordem de execução por grupo)

| Script | Grupo | O que faz |
|---|---|---|
| `01_import_referencia.sql` | veiculos | 15 `COPY` dos CSVs de `Detran/Referencias/` (delimitador `;`, `ENCODING 'UTF8'`) para `DADOS_REFERENCIA.VEI_TB01` a `VEI_TB15`; normaliza o texto literal `NULL` de `grupo_modelo.csv` via `sed 's/;NULL;/;;/g'` (55 linhas afetadas — sem isso o `COPY` gravaria a string `"NULL"` em vez do `NULL` de banco em `VL_MEDIA_PRECO`) e cria o sub-segmento-sentinela `VEI_TB03_SUB_SEGMENTO (207, 7, 'NÃO IDENTIFICADO')` via `INSERT ... ON CONFLICT DO NOTHING` — `modelo.csv` referencia `fk_sub_segmento=207`, que não existe em `sub_segmento_veiculo.csv` (dado sujo do próprio vazamento); `207` usa `id_segmento=7` ("Implemento Rodoviário"), no mesmo padrão sentinela já usado no dataset (id `0` = "Desconhecido") |
| `02_import_detran.sql` | veiculos | `COPY` de `JBR_Vehicles.csv` (~104M linhas/23GB) para `DADOS_EXTRAIDOS.VEI_TB01_DETRAN`; **remove as 15 FKs antes** do `COPY` (evita checar 104M linhas uma a uma) e **recria no final**; aceita `-v csv_path=/caminho/amostra.csv` para testar com um arquivo menor antes de rodar contra o arquivo completo; roda **depois** de `01_import_referencia.sql`, cujas tabelas de domínio essas FKs referenciam |
| `03_import_pessoas.sql` | pessoas | `COPY` de `basic220M.csv` (delimitador `\|`) para uma *staging* `UNLOGGED` 100% texto (via `FROM PROGRAM "tr -d '\r' < ..."`, porque o arquivo mistura CRLF no cabeçalho com LF nas demais linhas — o parser de CSV do Postgres travaria com "unquoted newline found in data"; filtrar com `tr` evita duplicar um arquivo de 14 GB só para normalizar quebra de linha), converte a data com `SAFE_TO_DATE` (`TO_DATE` dentro de um bloco `EXCEPTION WHEN OTHERS THEN RETURN NULL`, para uma data inválida no meio de 220M linhas não abortar o import inteiro) e insere em `PES_TB01_PESSOAS` com `NULLIF(coluna, '')` nos campos de texto |
| `04_import_ibge.sql` | ibge | arquivos são **JSON** (resposta crua da API do IBGE), não CSV — por isso não usa `COPY`: cada arquivo é lido inteiro com `pg_read_file()` e expandido com `jsonb_array_elements()` direto no `INSERT` das tabelas `IBGE_TB01` a `IBGE_TB10` (país a subdistrito, seguindo a hierarquia com FK); `IBGE_TB01_PAIS` filtra `WHERE (elem #>> '{id,M49}') IS NOT NULL` — alguns países da resposta do IBGE não têm código M49 (a chave primária da tabela), e entrariam como `NULL` de PK se não fossem descartados |
| `05_import_tse.sql` | tse | 6 anos (2014, 2016, 2018, 2020, 2022, 2024) com **formatos de colunas diferentes** e **`ENCODING 'LATIN1'`** (diferente dos demais grupos, que são UTF-8 — os CSVs do TSE vêm nessa codificação): candidatos 2016 tem 75 colunas contra 50 nos demais anos; votação por candidato 2014 tem 38 colunas contra 50 nos demais; votação por partido varia 28 (2014) / 36 (2018) / 38 (2016, 2020, 2022, 2024). Cada formato distinto ganha sua própria *staging* `UNLOGGED` (`STG_CAND_A/B`, `STG_VOTCAND_A/B`, `STG_VOTPART_A/B/C`) e o `INSERT` final faz a correspondência de colunas equivalentes entre formatos, com funções `SAFE_DATE/SAFE_INT/SAFE_BIGINT/SAFE_SMALLINT` (mesmo padrão de `TO_X(...) EXCEPTION WHEN OTHERS THEN RETURN NULL` do script de pessoas); `TSE_TB02_PARTIDOS` e `TSE_TB03_ELEICOES` são **derivadas** — `SELECT DISTINCT ON` sobre a união de todas as *stagings*, com `ON CONFLICT DO NOTHING`, já que não existe um arquivo próprio de partidos/eleições |

As funções `SAFE_*`/`PG_TEMP.SAFE_TO_DATE` são criadas em `PG_TEMP` (schema de sessão) — ficam
implicitamente descartadas ao fim da conexão do `psql`, sem sujar o catálogo permanente. As
*staging tables* são `UNLOGGED` (sem WAL — mais rápido para carga descartável) e **dropadas
dentro do próprio script** — não sobram estruturas temporárias no banco depois de um import
bem-sucedido. Os scripts pressupõem que `db/data` está montado em `/db` dentro do container
(volume `./db:/db:ro`).

## Modelo de dados

A ingestão preenche apenas os schemas `DADOS_REFERENCIA` e `DADOS_EXTRAIDOS`; nada do schema `INVESTIGACAO` é tocado. Por conjunto (`GRUPOS` em `api/rotas/ingestao.py`):

| Grupo | Scripts | Tabelas |
|---|---|---|
| `veiculos` | `01_import_referencia.sql`, `02_import_detran.sql` | `DADOS_REFERENCIA.VEI_TB01_MARCA` … `VEI_TB15_TIPO_PESSOA`; `DADOS_EXTRAIDOS.VEI_TB01_DETRAN` |
| `pessoas` | `03_import_pessoas.sql` | `DADOS_EXTRAIDOS.PES_TB01_PESSOAS` |
| `ibge` | `04_import_ibge.sql` | `DADOS_REFERENCIA.IBGE_TB01_PAIS` … `IBGE_TB10_SUBDISTRITO` |
| `tse` | `05_import_tse.sql` | `DADOS_EXTRAIDOS.TSE_TB01_CANDIDATOS`, `TSE_TB02_PARTIDOS`, `TSE_TB03_ELEICOES`, `TSE_TB04_VOTACAO_CANDIDATO`, `TSE_TB05_VOTACAO_PARTIDO` |

Detalhes de colunas, FKs e índices de cada tabela: ver `docs/banco_dados.md`.

### Quem consome os dados depois de importados

A ingestão só carrega as tabelas; quem lê depois é espalhado por outras features:

- **`api/rotas/referencia.py`** + **`api/funcoes/referencia_utils.py`** — autocomplete da tela de
  associação de contato (WhatsApp/SMS): `GET /api/referencia/municipios` busca em
  `IBGE_TB08_MUNICIPIO` (com a UF resolvida subindo a hierarquia
  município→microrregião→mesorregião→UF) e `GET /api/referencia/ufs` lista `IBGE_TB03_UF`; a
  busca de pessoa (`GET /api/referencia/pessoas`) reaproveita
  `funcoes.ia_investigacao.buscar_pessoa_referencia` — a mesma busca fuzzy usada como ferramenta
  do chat de IA, exposta aqui como endpoint comum para alimentar o autocomplete da tela.
- Sem os grupos `ibge`/`pessoas` importados, esses autocompletes simplesmente devolvem lista
  vazia (não há erro) — a tela de associação de contato continua utilizável, só sem sugestão.

## Configuração

Não há variável nova de `.env` para a ingestão. O que ela usa:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — credenciais repassadas ao `psql` que roda os
  scripts (`PGPASSWORD` via `api/funcoes/env_utils.py`).
- Volume `./db:/db:ro` no compose — é o que expõe `db/data/` e `db/import/` dentro dos containers
  (nos caminhos `/db/data/...` e `/app/db/import`).
- Os arquivos de `db/data/` **precisam existir** com os nomes exatos usados pelos scripts; se
  faltarem, o `psql` falha e o badge do card mostra "Falhou" com o erro.

## Referências

- `README.md` na raiz — visão geral (a estrutura de `db/data/` está aqui).
- `api/rotas/ingestao.py` — grupos, contagem e disparo (`GET /api/ingestao/status`,
  `POST /api/ingestao/importar/{grupo}`).
- `api/funcoes/import_utils.py` — execução em thread via `psql`; `api/funcoes/jobs_utils.py` +
  `db/init/41_jobs.sql` — o estado do job (`TP_JOB = 'import_referencia'`).
- `api/funcoes/db_utils.py` — `count_table` (contagem por tabela, `None` para tabela inexistente).
- `web/ingestao.html` e `web/js/ingestao.js` — cards, contagens, *polling* de 3s e badges.
- `db/import/01_import_referencia.sql` … `05_import_tse.sql` — os scripts executados.
- `api/rotas/referencia.py`, `api/funcoes/referencia_utils.py` — autocomplete que consome
  `IBGE_TB03_UF`/`IBGE_TB08_MUNICIPIO` depois de importados.
- `docs/banco_dados.md` — modelo de dados e convenção de nomes das tabelas preenchidas.
- `docs/infraestrutura.md` — volumes do compose (`./db:/db:ro`), rebuild da API vs. mudança em
  `db/init/`.