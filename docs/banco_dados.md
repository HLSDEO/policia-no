# Banco de dados

## Visão geral

O banco é um PostgreSQL 16 rodando no container do serviço `postgres` do Docker Compose
(`${NOME_PROJETO:-analyser}-postgres` — ver `infraestrutura.md`), sem porta
publicada no host — só a API o alcança, pela rede do compose (ver `docker-compose.yml`). Toda a
estrutura (schemas, tabelas, índices, FKs e seeds) é criada pelos scripts de migração de `db/init/`,
aplicados na primeira subida com volume vazio. Os dados de referência em massa (Detran, Serasa, TSE,
IBGE) ficam **fora** do banco até serem importados — ver `ingestao.md`.

O banco está dividido em quatro schemas:

| Schema | Papel |
|---|---|
| `DADOS_REFERENCIA` | tabelas de domínio/de-para (marca, modelo, município, cor, localidades IBGE…), sem dado pessoal |
| `DADOS_EXTRAIDOS` | dados brutos importados/vazados (veículos do Detran, pessoas do Serasa, candidatos do TSE), com FK para os domínios quando aplicável |
| `INVESTIGACAO` | dados extraídos de celulares (UFDR/Cellebrite) **por inquérito**, além de entidades, grafo, conversas de IA e permissões; carregado sob demanda, um inquérito por vez |
| `SEGURANCA` | usuários, sessões e auditoria |

Duas decisões estruturais merecem destaque: os **anexos (imagens/áudios) não são copiados** para o
banco (o binário é lido sob demanda do `.ufdr`, com cache em memória), e a **busca semântica não usa
pgvector** (os embeddings ficam em `EMB_TB01_EMBEDDINGS` e a similaridade é calculada em
Python/numpy).

## Como funciona

### Schema completo (`db/init/`) e deltas (`db/migracoes/`)

A estrutura mora em **dois lugares**, e toda mudança de schema entra nos dois — ver
[atualizacao.md](atualizacao.md) ("Como mudar o schema") e `db/init/README.md`:

| Pasta | Forma | Quando roda |
|---|---|---|
| `db/init/NN_dominio.sql` | **declarativa** — descreve o estado atual (`CREATE TABLE` com todas as colunas, constraints e índices da tabela; não há `ALTER TABLE` avulso) | só na **primeira subida** do Postgres, com o volume `postgres_data` vazio |
| `db/migracoes/NNNN_descricao.sql` | **imperativa** — o delta (`ALTER TABLE …`) | **sozinho, no startup da API**, uma vez cada, registrado em `SEGURANCA.SIS_TB01_MIGRACOES` |

- `db/init/` tem hoje **43 arquivos** (numerados de `01_schemas.sql` a `47_entidades_automaticas.sql`),
  um por domínio, aplicados em ordem **numérica**. A numeração tem **vagos** (23, 32, 34, 36):
  arquivos fundidos/removidos deixaram o número para trás — número publicado **não se reusa**. O
  número é só ordem de execução, ditada pelas foreign keys: um arquivo só referencia tabelas de
  arquivos anteriores. Arquivo novo entra com o próximo número livre.
- Rodam em ordem **numérica**, aplicados por `db/bootstrap/00_aplicar_migracoes.sh` — o único
  arquivo montado em `/docker-entrypoint-initdb.d`. O entrypoint do Postgres ordenaria os
  arquivos como texto, e nessa ordem `100_` viria logo depois de `09_` (o `0` vem antes do `_`
  no ASCII), o que já derrubou a inicialização inteira num `ON_ERROR_STOP`. O runner evita isso.
- **Num banco que já existe, `db/init/` não roda.** Quem atualiza esse banco é o runner de
  `db/migracoes/` (`api/funcoes/migrar_utils.py`), no startup da API — e, se um delta pendente
  falha, a API **recusa subir** com a causa no log.
- **O número de um delta é a identidade dele no ledger** (`NU_MIGRACAO`, chave primária), então
  dois arquivos com o mesmo prefixo são um defeito silencioso: o runner aplica um, registra o
  número e pula o outro sem erro — e qual sobra depende da ordem em que cada instalação aplicou.
  Aconteceu com dois `0021_` vindos de branches diferentes (hoje `0023_ia_progresso_orbit.sql` e
  `0024_telefonia_paginacao.sql`, ambos idempotentes). Desde então o runner **recusa aplicar
  qualquer migração** enquanto houver número repetido, e `estado()` devolve `numeros_duplicados`
  para a tela Administração → Servidor mostrar. Ao integrar branch, confira se o número escolhido
  continua livre — o git não acusa esse conflito. Aplicar `psql -f` à mão só é necessário fora
  desse fluxo (`MIGRAR_NO_STARTUP=0`, ou um domínio inteiramente novo em `db/init/`):
  ```
  docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
      -f /db/init/20_arquivos.sql
  ```
  (o volume `./db:/db:ro` do compose expõe os scripts dentro do container).
- Referências de conteúdo: `01_schemas.sql` cria os **quatro** schemas (`DADOS_REFERENCIA`,
  `DADOS_EXTRAIDOS`, `INVESTIGACAO`, `SEGURANCA`) e as extensões (`pg_trgm`, `fuzzystrmatch`,
  `pgcrypto`, no schema `public`); `30_relatorios.sql` traz os seeds dos padrões de relatório e
  `20_arquivos.sql` o seed das categorias de imagem; `40_sistema_migracoes.sql` cria o ledger
  `SIS_TB01_MIGRACOES` e `41_jobs.sql` a tabela de jobs.
- Credenciais vêm do `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`). Para entrar no
  psql: `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"`.

### Convenção de nomes

- **Tabelas**: `{PREFIXO}_TB{NN}_{CONTEUDO}` — prefixo por assunto, número sequencial **por
  prefixo** (`VEI_TB01` e `PES_TB01` não conflitam) e nome descritivo do conteúdo.
- **Colunas** (padrão BR):

  | Prefixo | Significado | Exemplo real |
  |---|---|---|
  | `ID_` | identificador (PK/FK) | `ID_MARCA` |
  | `NO_` | nome curto | `NO_PESSOA`, `NO_MODELO` |
  | `DS_` | descrição/texto curto | `DS_SEXO`, `DS_CARGO` |
  | `SG_` | sigla | `SG_UF`, `SG_PARTIDO` |
  | `NU_` | número/código (texto, preserva zero à esquerda) | `NU_CHASSI`, `NU_CPF` |
  | `CO_` | código sem domínio conhecido | `CO_PLACA`, `CO_LINHA` |
  | `DT_` | data/timestamp | `DT_NASCIMENTO`, `DT_LIGACAO` |
  | `VL_` | valor monetário | `VL_MEDIA_PRECO` |
  | `QT_` | quantidade | `QT_PASSAGEIRO`, `QT_VOTOS_NOMINAIS` |
  | `IN_` | indicador/flag | `IN_PLACA_NOVA` |
  | `TX_` | texto livre | `TX_CAIXA_CAMBIO`, `TX_OCR` |

- **Foreign keys**: `FK{NN}_{TABELA_ORIGEM}_{TABELA_DESTINO}`, numeração **global** sequencial.
  Exemplos reais: `FK06_VEI_TB01_VEI_TB06`, `FK37_INQ_TB02_INQ_TB01`, `FK59_WPP_TB01_PES_TB01`.
  Quando a mesma tabela destino é referenciada mais de uma vez, o sufixo vira `A/B/C/D`
  (`FK14_VEI_TB01_VEI_TB15A` … `FK16_VEI_TB01_VEI_TB15C`).
- **Índices**: `IX{NN}_{TABELA}_{COLUNA}`, numeração global. Exemplos reais: `IX01_VEI_TB05_ID_MARCA`,
  `IX04_VEI_TB01_ID_MODELO`, `IX08_PES_TB01_NU_CPF`, `IX16_WPP_TB04_ID_CONVERSA`.
- **Exceções conhecidas** à convenção: `CATEGORIAS_IMAGEM` (tabela anterior à convenção de
  prefixos, mantida com o nome original); `IA_TB04_AUDITORIA` e `IA_TB04_ARQUIVOS_PROMPT` coexistem
  com o mesmo número de sequência; o mesmo vale para `RIF_TB06_IDENTIFICACOES` e `RIF_TB06_ARQUIVOS`, e para os dois índices
  únicos `UX98` de `db/init/31_marcadores_achados.sql` (`UX98_MRC_TB01_NOME` e
  `UX98_MRC_TB02_PAR`).
- **Nota de operação**: as FKs de `VEI_TB01_DETRAN` são *dropadas* antes do COPY do import e
  recriadas no final (ver `db/import/02_import_detran.sql`), para não checar ~104M linhas uma a uma.
  Se a recriação falhar por valor órfão (dado sujo do próprio vazamento), a constraint fica
  ausente — `SELECT conname FROM pg_constraint WHERE NOT convalidated;` lista qual(is) faltam,
  para decidir como tratar os órfãos antes de tentar de novo (ver `ingestao.md`).

### Pool de conexões (`api/funcoes/db_utils.py`)

- **Dois pools.** `get_connection()` devolve uma conexão do pool de **requisição**
  (`psycopg_pool.ConnectionPool`, aberto no `startup` da API por `abrir_pool()` e fechado no
  `shutdown`); todo o código de `api/funcoes/` usa esse pool — não há conexão avulsa por
  requisição. `get_connection(longa=True)` usa um segundo pool, o de **conexões longas**
  (teto `DB_POOL_LONGA_MAX`, default 8), e há ainda `nova_conexao_dedicada()` — uma conexão
  avulsa, fora de qualquer pool, usada apenas pela eleição de liderança (`leader_utils.py`),
  que precisa de lock **de sessão** vivo enquanto o laço respira.
- **O pool de requisição pode passar pelo PgBouncer.** Com `PGBOUNCE_HOST` no `.env` ele aponta
  para o pooler (session mode, porta 6432 da rede interna); as conexões **longas** e as
  dedicadas de liderança vão **sempre direto ao Postgres**, por fora do pooler — lock de sessão
  e transação de minutos são justamente o que não se deve dividir com um multiplexador. Com a
  chave ausente, tudo vai direto como sempre. Ver `infraestrutura.md` ("PgBouncer").
- `min_size=2` (a maior parte das operações é rápida, não precisa de conexão pronta) e
  `max_size` dimensionado pelo hardware (`funcoes/desempenho_utils.py`; o antigo 20 fixo virou o
  piso), com folga sob o `max_connections` do Postgres — a folga soma também
  as threads soltas fora do ciclo de requisição (OCR, classificação CLIP, comparação facial,
  extração de UFDR/SIMBA/RIF em background) que chamam `get_connection()` por conta própria, sem
  passar por uma rota HTTP. Os scripts de `db/import/` (ver `ingestao.md`) **não** usam esse pool
  — rodam via `psql` como processo `subprocess` à parte.
- `check=ConnectionPool.check_connection` descarta uma conexão presa da pool se o Postgres tiver
  reiniciado no meio do caminho (senão o primeiro uso depois de um restart do container Postgres
  devolveria erro de conexão morta em vez de reabrir sozinho).
- **Timeouts de sessão, em dois perfis.** O sistema não tinha nenhum: uma query ruim segurava
  conexão e locks até alguém reiniciar o container. O perfil de requisição é apertado
  (`statement_timeout` 60 s, `lock_timeout` 5 s, `idle_in_transaction_session_timeout` 60 s) e é
  aplicado pelo `configure` a cada conexão física nova e restaurado pelo `reset` quando a conexão
  volta ao pool. `get_connection(longa=True)` — que vive no pool longo dedicado, direto ao banco —
  usa o perfil folgado (sem `statement_timeout`, `idle_in_transaction` de 15 min) e existe **só**
  para os laços de ingestão que legitimamente seguram a conexão trabalhando: o laço do
  `report.xml` da extração, a carga do CSV do SIMBA, o laço do stdout do IPED e o runner de
  migrações. Não use `longa=True` para "esta consulta é pesada" numa rota — o teto de tempo do
  caminho de requisição é justamente o que impede uma tela de derrubar as outras.
- **Espera por conexão de 10 s** (`timeout=`), no lugar dos 30 s implícitos do `psycopg_pool`: com
  muita gente usando, meio minuto pendurado vira F5, que vira mais fila. O `PoolTimeout` tem
  handler próprio em `main.py` e vira 503 com mensagem, não 500 genérico.
- `max_lifetime=1800` recicla cada conexão a cada 30 min, e `estatisticas_pool()` expõe
  `requests_waiting`/`pool_available` dos **dois** pools (o segundo sob a chave `longa`) para a
  tela Administração → Desempenho e para o `/api/health` — `requests_waiting` acima de zero de
  forma sustentada é o sintoma de conexão retida por job longo. **São números DO PROCESSO que
  respondeu** — com as duas réplicas (`api-1`/`api-2`) cada uma tem seu pool próprio; o campo
  `replica` do `/api/health` diz de quem é o número.
- As credenciais (`POSTGRES_USER/PASSWORD/DB`) são lidas do `.env` **uma única vez**, ao montar o
  pool — não a cada conexão como antes de o pool existir — porque só mudam editando o `.env` à
  mão e reiniciando o container (a tela Configurações só grava as chaves `AI_*`/`EMBED_*`).
- `count_table(schema, tabela)` (usado pelo painel de Ingestão) roda `SELECT COUNT(*)` com o
  nome do schema/tabela **em minúsculo** — o DDL de `db/init/` cria os identificadores sem aspas,
  e o Postgres dobra identificador não citado para minúsculo automaticamente; sem o `.lower()`
  explícito, uma tabela referenciada em maiúsculo (como aparece em todo o resto da
  documentação/código) nunca seria encontrada. Devolve `None` tanto para `UndefinedTable`
  (schema/tabela ainda não criado) quanto para qualquer outro erro de conexão — para o painel de
  Ingestão, "não consegui contar" e "a tabela não existe" mostram o mesmo "tabela não criada".

## Modelo de dados

### Grupos de tabelas por prefixo

Todos os nomes abaixo foram conferidos nos `CREATE TABLE` de `db/init/*.sql` (e na rota
`api/rotas/ingestao.py` para os grupos de referência).

| Prefixo | Schema | Tabelas (reais) | Conteúdo |
|---|---|---|---|
| `VEI_TB{NN}` | DADOS_REFERENCIA | `VEI_TB01_MARCA`, `VEI_TB02_SEGMENTO`, `VEI_TB03_SUB_SEGMENTO`, `VEI_TB04_GRUPO_MODELO`, `VEI_TB05_MODELO`, `VEI_TB06_MUNICIPIO`, `VEI_TB07_COMBUSTIVEL`, `VEI_TB08_COR`, `VEI_TB09_CARROCERIA`, `VEI_TB10_ESPECIE_VEICULO`, `VEI_TB11_TIPO_VEICULO`, `VEI_TB12_NACIONALIDADE`, `VEI_TB13_REGIAO`, `VEI_TB14_RESTRICAO`, `VEI_TB15_TIPO_PESSOA` | os 15 domínios de veículos do Detran (`marca/modelo/cor/…`), carregados por `01_import_referencia.sql` |
| `VEI_TB{NN}` | DADOS_EXTRAIDOS | `VEI_TB01_DETRAN` | tabela-fato do vazamento: chassi, placas, ano, município, modelo, restrições, FKs para os domínios acima (+20 FKs reusando-as); ~104M linhas |
| `PES_TB{NN}` | DADOS_EXTRAIDOS | `PES_TB01_PESSOAS` | pessoas do Serasa: `NU_CPF`, `NO_PESSOA`, `DS_SEXO`, `DT_NASCIMENTO` (~220M linhas) |
| `TSE_TB{NN}` | DADOS_EXTRAIDOS | `TSE_TB01_CANDIDATOS`, `TSE_TB02_PARTIDOS`, `TSE_TB03_ELEICOES`, `TSE_TB04_VOTACAO_CANDIDATO`, `TSE_TB05_VOTACAO_PARTIDO` | candidatos e votação das eleições 2014 a 2024; partidos e eleições são tabelas derivadas |
| `IBGE_TB{NN}` | DADOS_REFERENCIA | `IBGE_TB01_PAIS`, `IBGE_TB02_REGIAO`, `IBGE_TB03_UF`, `IBGE_TB04_MESORREGIAO`, `IBGE_TB05_MICRORREGIAO`, `IBGE_TB06_REGIAO_INTERMEDIARIA`, `IBGE_TB07_REGIAO_IMEDIATA`, `IBGE_TB08_MUNICIPIO`, `IBGE_TB09_DISTRITO`, `IBGE_TB10_SUBDISTRITO` | hierarquia de localidades do IBGE, de país a subdistrito |
| `JOB_TB{NN}` | INVESTIGACAO | `JOB_TB01_JOBS` | jobs de segundo plano com escopo por inquérito (OCR, classificação de imagem, comparação facial; e os globais de importação de base e download de modelo). Substitui os dicionários de módulo que guardavam progresso/cancelamento em memória do processo. O guard "só um por inquérito" é o índice único parcial `UX100_JOB_TB01_ATIVO`, e job órfão é detectado pelo batimento em `DT_ULTIMA_ATIVIDADE` — ver `db/init/41_jobs.sql` |
| `AGD_TB{NN}` | INVESTIGACAO | `AGD_TB01_EXECUCOES` | ledger do agendador interno (`api/funcoes/agendador.py`): uma linha por (`TP_TAREFA`, `DT_REFERENCIA`), que é ao mesmo tempo a vaga do dia — a chave primária impede dois workers de rodarem o mesmo passe — e o histórico ("rodou, quando, por quem, com que resultado"). `DT_REFERENCIA` é o **dia no fuso configurado**, calculado em Python: o container roda em UTC. Ver `db/init/45_agendador.sql` |
| `INQ_TB{NN}` | INVESTIGACAO | `INQ_TB01_INQUERITOS`, `INQ_TB02_EXTRACOES`, `INQ_TB03_PERMISSOES`, `INQ_TB04_DOCUMENTOS`, `INQ_TB05_RESUMO_NOTURNO`, `INQ_TB06_APELIDOS` | inquéritos (ePol) e contexto da investigação; extrações `.ufdr` com fase/cancelamento (`CD_FASE`, `QT_PROGRESSO_FASE`, `DT_INICIO/FIM_PROCESSAMENTO`) e contagens por domínio; permissões de acesso; documentos anexados ao inquérito (com hash do arquivo original); e o resumo gerado pelo passe da madrugada (uma linha por fonte + o consolidado — ver `docs/resumo_noturno.md`); e o apelido livre de qualquer prova do card (`TP_PROVA`+`ID_PROVA` — rif, simba, documento, iped, ipl, irpf, ipei, telefonia), editado no menu "Mais ações" de `investigacao.html` |
| `WPP_TB{NN}` | INVESTIGACAO | `WPP_TB01_CONTATOS`, `WPP_TB02_CONVERSAS`, `WPP_TB03_PARTICIPANTES`, `WPP_TB04_MENSAGENS`, `WPP_TB05_ANEXOS` | WhatsApp: contatos (com foto de perfil), conversas (inclusive as Atualizações de Status como pseudo-chat), participantes, mensagens com `TX_TRANSCRICAO` e anexos |
| `SMS_TB{NN}` | INVESTIGACAO | `SMS_TB01_CONTATOS`, `SMS_TB02_CONVERSAS`, `SMS_TB03_PARTICIPANTES`, `SMS_TB04_MENSAGENS`, `SMS_TB05_ANEXOS` | SMS/MMS nativos, mesmo formato do WhatsApp |
| `MSG_TB{NN}` | INVESTIGACAO | `MSG_TB01_CONTATOS`, `MSG_TB02_CONVERSAS`, `MSG_TB03_PARTICIPANTES`, `MSG_TB04_MENSAGENS`, `MSG_TB05_ANEXOS` | mensageiros diversos (Instagram, Telegram, Facebook, Discord…) numa família única de tabelas, com `TP_APP` separando cada app |
| `CTT_TB{NN}` | INVESTIGACAO | `CTT_TB01_CONTATOS`, `CTT_TB02_CONTATO_ENTRADAS` | agenda de contatos (Contact) com telefones/e-mails |
| `LIG_TB{NN}` | INVESTIGACAO | `LIG_TB01_LIGACOES` | ligações (Call): direção, duração, vídeo |
| `LOC_TB{NN}` | INVESTIGACAO | `LOC_TB01_LOCALIZACOES` | localizações (Location): coordenadas, endereço, app |
| `GEO_TB{NN}` | INVESTIGACAO | `GEO_TB01_ENDERECOS` | cache **global** de geocodificação reversa (coordenada arredondada a 5 casas → endereço), preenchido sob demanda pelo OpenStreetMap/Nominatim — cada coordenada é consultada uma única vez |
| `NTF_TB{NN}` | INVESTIGACAO | `NTF_TB01_NOTIFICACOES` | notificações (Notification) |
| `NAV_TB{NN}` | INVESTIGACAO | `NAV_TB01_PAGINAS_VISITADAS` | histórico de navegação (VisitedPage) |
| `APP_TB{NN}` | INVESTIGACAO | `APP_TB01_APLICATIVOS` | aplicativos instalados (InstalledApplication) |
| `NOT_TB{NN}` | INVESTIGACAO | `NOT_TB01_NOTAS` | notas de bloco de notas do aparelho (Samsung Notes, Apple Notes, Google Keep…) |
| `ARQ_TB{NN}` | INVESTIGACAO | `ARQ_TB01_ARQUIVOS`, `ARQ_TB02_EMBEDDING_IMAGEM` | inventário do filesystem (elementos `<file>`), com classificação de imagem por IA (`CLASSIFICACAO_IA`/`CONFIANCA_IA`), OCR (`TX_OCR`/`DT_OCR`) e, para a Nuvem IPED, transcrição (`TX_TRANSCRICAO`), identificadores extraídos (`TX_IDENTIFICADORES`, JSONB) e as flags `IN_EXTENSAO_DIVERGENTE`/`IN_RECUPERADO` |
| `CATEGORIAS_IMAGEM` | INVESTIGACAO | `CATEGORIAS_IMAGEM` | categorias (termo em linguagem natural + rótulo) da classificação por CLIP; anterior à convenção de prefixos, mantida com o nome original |
| `EML_TB{NN}` | INVESTIGACAO | `EML_TB01_EMAILS` | e-mails extraídos do UFDR e da Nuvem IPED (remetente, destinatários, assunto, corpo); `NU_THREAD`/`NU_MENSAGEM` guardam o `X-GM-THRID` e o `Message-ID` do Gmail, que só a Nuvem IPED preenche |
| `BNC_TB{NN}` | INVESTIGACAO | `BNC_TB01_CONTAS_FINANCEIRAS` | contas financeiras/bancárias identificadas no aparelho (inclusive carteiras de cripto) |
| `CTA_TB{NN}` | INVESTIGACAO | `CTA_TB01_CONTAS_USUARIO` | contas de usuário do aparelho (Apple ID, Google, e-mail, redes sociais…) |
| `WIFI_TB{NN}` | INVESTIGACAO | `WIFI_TB01_REDES` | redes Wi-Fi conhecidas/salvas (SSID, BSSID, senha, conexões) |
| `PIX_TB{NN}` | INVESTIGACAO | `PIX_TB01_CHAVES` | chaves PIX detectadas nas conversas, ligadas ao remetente e à conversa de origem |
| `DON_TB{NN}` | INVESTIGACAO | `DON_TB01_DONOS` | dono(s)/usuário(s) do aparelho (User) |
| `IPD_TB{NN}` | INVESTIGACAO | `IPD_TB01_EXTRACOES` | Nuvem IPED: uma linha por pasta-caso do IPED importada, com status/fase/progresso, conta identificada e contadores por destino (arquivos/e-mails/contatos e um por produto do Google) |
| `ATV/ACS/DVC/PRF/EVT_TB{NN}` | INVESTIGACAO | `ATV_TB01_ATIVIDADES`, `ACS_TB01_ACESSOS`, `ACS_TB02_AGREGADOS`, `ACS_TB03_CONEXOES`, `DVC_TB01_DISPOSITIVOS`, `PRF_TB01_PERFIL`, `EVT_TB01_EVENTOS` | dados de conta na nuvem sem equivalente no aparelho: atividades (My Activity), acessos/eventos agregados, conexões, dispositivos vinculados, perfil da conta e eventos de agenda; tabelas genéricas, alimentadas hoje pela Nuvem IPED e reaproveitáveis por um futuro import de Microsoft/Apple |
| `NUV_TB{NN}` | INVESTIGACAO | `NUV_TB01_METADADOS_ARQUIVO` | o *sidecar* que a nuvem guarda **sobre** um arquivo (`<nome>-info.json` do Drive, `<foto>.jpg.json` do Google Fotos): datas de servidor, lixeira, compartilhamento, de qual aparelho e de qual pasta dele a mídia subiu, e o log de atividade. Ligado a `ARQ_TB01_ARQUIVOS` por `(ID_EXTRACAO, LOWER(NO_ARQUIVO))`, não por FK — os arquivos entram por `COPY` em lote, que não devolve `ID_ARQUIVO` |
| `EMB_TB{NN}` | INVESTIGACAO | `EMB_TB01_EMBEDDINGS`, `EMB_TB02_INDEXACOES` | embeddings das mensagens/transcrições (busca semântica), escopados por inquérito — **sem pgvector** |
| `ENT_TB{NN}` | INVESTIGACAO | `ENT_TB01_ENTIDADES`, `ENT_TB02_PROMPTS_BUSCA`, `ENT_TB03_RELACOES`, `ENT_TB04_BUSCAS_AUTOMATICAS`, `ENT_TB05_CANONICAS`, `ENT_TB06_CANONICA_EVIDENCIAS`, `ENT_TB07_CANONICA_RELACOES`, `ENT_TB08_CANONICA_IDENTIFICADORES`, `ENT_TB09_IMAGENS` | entidades (nós), prompts de busca, relações (arestas), histórico de busca automática, entidades canônicas + evidências + relações + identificadores |
| `PDR_TB{NN}` | INVESTIGACAO | `PDR_TB01_PADROES`, `PDR_TB02_EXECUCOES`, `PDR_TB03_DETECCOES` | padrões de crime (léxico/regex/temporal), execuções e detecções |
| `IA_TB{NN}` | INVESTIGACAO | `IA_TB01_CONVERSAS`, `IA_TB02_MENSAGENS`, `IA_TB03_PROCESSAMENTOS`, `IA_TB04_AUDITORIA`, `IA_TB04_ARQUIVOS_PROMPT`, `IA_TB05_RELATORIOS_DOCX`, `IA_TB07_PLANILHAS` | conversas e mensagens do chat de IA por inquérito, processamentos em background (polling/cancelamento), auditoria das chamadas de IA, arquivos/prompt do contexto, os .docx gerados quando o investigador pede um relatório no chat e os .xlsx das consolidações pedidas no chat (ver `chat_ia.md`) |
| `HIP_TB{NN}` | INVESTIGACAO | `HIP_TB01_HIPOTESES`, `HIP_TB02_EVIDENCIAS`, `HIP_TB03_PENDENCIAS` | workspace de hipóteses do inquérito inteiro: hipótese com status kanban (`proposta/em_analise/confirmada/refutada/arquivada`) e responsável, evidências a favor/contra (texto livre e/ou vínculo polimórfico `TP_ESCOPO`/`ID_ESCOPO` a um item importado) e pendências de diligência. Substituiu `ACH_TB01_ACHADOS_ANALISTA` (migração `0046`) — ver `hipoteses.md` |
| `INQ_TB01` | INVESTIGACAO | `INQ_TB01_INQUERITOS` | o inquérito; `IN_CASO_INTELIGENCIA = true` marca um caso de inteligência (número `CASO-AAAA.NNNN` gerado) |
| `RIF_TB{NN}` | INVESTIGACAO | `RIF_TB01_RIFS`, `RIF_TB02_COMUNICACOES`, `RIF_TB03_ENVOLVIDOS`, `RIF_TB04_OCORRENCIAS`, `RIF_TB05_CONTRAPARTES`, `RIF_TB06_IDENTIFICACOES`, `RIF_TB06_ARQUIVOS` | RIFs do COAF/UIF (SISCOAF): comunicações com contrapartes estruturadas, envolvidos, ocorrências, identificações e arquivos |
| `SMB_TB{NN}` | INVESTIGACAO | `SMB_TB01_EXTRATOS`, `SMB_TB02_CONTAS`, `SMB_TB03_LANCAMENTOS`, `SMB_TB04_CONTRAPARTES` | extratos bancários do SIMBA (R304): extrato importado, contas afastadas, lançamentos (uma linha por perna) e contrapartes consolidadas por conta/direção |
| `IPL_TB{NN}` | INVESTIGACAO | `IPL_TB01_INQUERITOS_PDF`, `IPL_TB02_PECAS`, `IPL_TB03_PESSOAS`, `IPL_TB04_ACHADOS` | inquérito policial em PDF (ePol): índice das peças por folha, pessoas citadas e achados (telefone, e-mail, valor, conta, processo, tipificação…) |
| `REL_TB{NN}` | INVESTIGACAO | `REL_TB01_PADROES`, `REL_TB02_BLOCOS`, `REL_TB03_MARCACOES`, `REL_TB04_GERACOES` | padrões de relatório (biblioteca global de modelos e blocos) e marcações "marcar para o relatório" |
| `MRC_TB{NN}` | INVESTIGACAO | `MRC_TB01_MARCADORES`, `MRC_TB02_APLICACOES` | marcadores nomeados (as *tags* do IPED): a etiqueta é pessoal do usuário e presa ao inquérito, e a aplicação N:N a pendura na marcação compartilhada de `REL_TB03` |
| `VMS_TB{NN}` | INVESTIGACAO | `VMS_TB01_VOICEMAIL` | caixa postal (Voicemail): remetente, duração e a transcrição que o próprio aparelho já traz em parte dos casos |
| `ERB_TB{NN}` | INVESTIGACAO | `ERB_TB01_ANTENAS` | antenas de telefonia (`CellTower`): MCC/MNC/LAC/CID e coordenada — rastro de deslocamento independente do GPS |
| `JRN_TB{NN}` | INVESTIGACAO | `JRN_TB01_TRAJETOS` | trajetos (`Journey`) com as coordenadas gravadas na própria linha, porque o trajeto é a unidade de leitura |
| `GEO_TB{NN}` (camadas) | INVESTIGACAO | `GEO_TB02_CAMADAS`, `GEO_TB03_FEICOES`, `GEO_TB04_ANEIS` | camadas geoespaciais importadas (SHP/GeoJSON) desenhadas sobre o mapa de localizações — ver `db/migracoes/0001_camadas_geoespaciais.sql` |
| `BSC_TB{NN}` | INVESTIGACAO | `BSC_TB01_BUSCAS` | o que a pessoa procurou (`SearchedItem`) em Safari, Chrome, Gmail, Instagram, WhatsApp, Maps — termo de busca é prova direta de intenção |
| `COO/FAV/DWN/RDE_TB{NN}` | INVESTIGACAO | `COO_TB01_COOKIES`, `FAV_TB01_FAVORITOS`, `DWN_TB01_DOWNLOADS`, `RDE_TB01_USO_REDE` | as demais abas da tela Navegação: cookies, favoritos, downloads e uso de rede por app |
| `USO/APU_TB{NN}` | INVESTIGACAO | `USO_TB01_USO_APPS`, `APU_TB01_USO_APLICATIVO` | uso de aplicativo — `USO_TB01` é evento a evento (abrir/fechar, o volume mais alto do UFDR), `APU_TB01` é o total acumulado por app |
| `RDS_TB{NN}` | INVESTIGACAO | `RDS_TB01_ATIVIDADE_SOCIAL` | conteúdo de rede social fora de conversa (autor, texto, URL, coordenada e anexos) |
| `CAL_TB{NN}` | INVESTIGACAO | `CAL_TB01_CALENDARIO` | compromissos do aparelho (`CalendarEntry`), tela "Calendário" |
| `CNX/RCD_TB{NN}` | INVESTIGACAO | `CNX_TB01_CONEXOES`, `RCD_TB01_DISPOSITIVOS` | conexões do aparelho (`DeviceConnectivity`) e dispositivos reconhecidos por MAC — ligam dois aparelhos que estiveram no mesmo lugar sem depender de conversa |
| `DEV_TB{NN}` | INVESTIGACAO | `DEV_TB01_INFO_APARELHO` | identidade do aparelho (IMEI, serial, modelo, versão), insumo direto da convergência entre casos |
| `GRV_TB{NN}` | INVESTIGACAO | `GRV_TB01_GRAVACOES` | gravações de áudio feitas no aparelho (`Recording`: gravador, chamada) |
| `UPL_TB{NN}` | INVESTIGACAO | `UPL_TB01_ENVIOS_NUVEM` | envios para nuvem feitos pelo aparelho, com conta, pasta e compartilhamento (só iPhone nos extratos auditados) |
| `SNH/AFL_TB{NN}` | INVESTIGACAO | `SNH_TB01_SENHAS`, `AFL_TB01_AUTOFILL` | senhas salvas e dados de preenchimento automático do aparelho |
| `CRT/MBC_TB{NN}` | INVESTIGACAO | `CRT_TB01_CARTOES_CREDITO`, `MBC_TB01_CARTOES_CARTEIRA` | cartões de crédito e os cartões/passes da carteira do aparelho (`MobileCard`: embarque, fidelidade, ingresso) |
| `TRF_TB{NN}` | INVESTIGACAO | `TRF_TB01_TRANSFERENCIAS` | transferências financeiras vistas no aparelho — mesmo assunto das telas de RIF e SIMBA |
| `IRP_TB{NN}` | INVESTIGACAO | `IRP_TB01_DECLARACOES`, `IRP_TB02_BENS_DIREITOS`, `IRP_TB03_DEPENDENTES`, `IRP_TB04_RENDIMENTOS`, `IRP_TB05_DIVIDAS`, `IRP_TB06_DOACOES` | declaração de IRPF em PDF (`db/init/28_irpf.sql`) — ver `irpf.md` |
| `IPE_TB{NN}` | INVESTIGACAO | `IPE_TB01_IPEIS`, `IPE_TB02_ALVOS`, `IPE_TB03_RELACIONADOS`, `IPE_TB04_ANOS`, `IPE_TB05_FONTES`, `IPE_TB06_CONTAS`, `IPE_TB07_BENS`, `IPE_TB08_NOTAS_FISCAIS`, `IPE_TB09_CONTATOS` | quebra de sigilo fiscal: a IPEI da Receita Federal, PDF + planilha de anexo na MESMA linha de TB01 (`db/init/42_ipei.sql`) — ver `ipei.md` |
| `TEL_TB{NN}` | INVESTIGACAO | `TEL_TB01_TELEFONIA`, `TEL_TB02_ARQUIVOS`, `TEL_TB03_LINHAS`, `TEL_TB04_CHAMADAS`, `TEL_TB05_ERBS`, `TEL_TB06_NUMEROS`, `TEL_TB07_SESSOES_WIFI` | quebra de sigilo telefônico: o card é (inquérito, operadora), e não o arquivo; `TX_CHAVE` com índice único por card é o que faz vários arquivos virarem um card só sem duplicar (`db/init/43_telefonia.sql`) — ver `telefonia.md` |
| `NO_TB{NN}` | INVESTIGACAO | `NO_TB01_SERVIDORES`, `NO_TB02_SERVIDOR_DELEGACIAS`, `NO_TB03_LOTES_ENTREGA` | nós descentralizados: o servidor registrado na central (token, heartbeat), o atendimento por delegacia e o recibo de entrega por lote (`db/init/46_nos.sql`) — ver `no_descentralizado.md` |
| `DOC_TB{NN}` | INVESTIGACAO | `DOC_TB05_AUTO_PADROES`, `DOC_TB06_AUTO_EXECUCOES`, `DOC_TB07_AUTO_DETECCOES` | entidades automatizadas: a biblioteca global de regex com validação sobre documentos genéricos, as execuções por inquérito e os achados deduplicados por chave normalizada (`db/init/47_entidades_automaticas.sql`) — ver `entidades_auto.md` |
| `SIS_TB{NN}` | SEGURANCA | `SIS_TB01_MIGRACOES` | ledger das migrações de `db/migracoes/` já aplicadas (arquivo, sha256, origem, duração) — ver `atualizacao.md` |
| `DELEGACIAS` | SEGURANCA | `DELEGACIAS` | as unidades (multidelegacia) e o flag `IN_IA_ATIVA`; anterior à convenção de prefixos, mantida com o nome original — ver `permissoes_delegacia.md` |
| `USR_TB{NN}` | SEGURANCA | `USR_TB01_USUARIOS`, `USR_TB02_SESSOES`, `USR_TB03_REDEFINICAO_SENHA`, `USR_TB04_TENTATIVAS_LOGIN`, `USR_TB05_ACESSOS_INQUERITO`, `USR_TB06_CONFIG_IA`, `USR_TB07_WEBHOOK_IA` | usuários, sessões (hash SHA-256 do token), tokens de redefinição de senha, tentativas de login, auditoria de acesso a inquérito, a IA própria do usuário (`db/init/39_config_ia_usuario.sql`, ver `ia_por_usuario.md`) e o webhook de conclusão da IA (ver `avisos_ia.md`) |

### Anexos fora do banco

As tabelas de anexos guardam apenas **metadados** (caminho no ZIP, tamanho, tipo MIME, datas,
transcrição). O binário (imagem/áudio/vídeo/foto de perfil) é servido sob demanda, lido direto do
`.ufdr` original em `data/` quando o investigador abre a mídia. A leitura usa um cache em memória
(handle do zip + conteúdo pequeno já lido/redimensionado) com TTL de até **1 hora** de inatividade —
renovado a cada acesso e expirado sozinho se a conversa parar de ser usada
(`api/funcoes/ufdr_cache.py`). Se o `.ufdr` sumir ou mudar de nome, os dados continuam navegáveis,
mas o conteúdo para de abrir.

### Embeddings sem pgvector

`EMB_TB01_EMBEDDINGS` guarda o vetor de cada mensagem/transcrição escopado por inquérito
(`EMB_TB02_INDEXACOES` registra as indexações). A similaridade (cosseno) é calculada em
Python/numpy — sem pgvector, para não mexer na infra do Postgres — e a matriz de vetores de cada
inquérito fica em cache LRU em memória (TTL 30 min, teto 1 GB, `api/funcoes/embed_utils.py`), com
invalidação por assinatura (quantidade + maior id). Isso **adia** pgvector, não o substitui: num
volume em que a varredura linear passe a doer, o caminho planejado é um índice `ivfflat` no banco.

## Configuração

- `.env`: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (criam o banco na primeira subida e
  são reutilizados pelo import de referência, que chama `psql` com `PGPASSWORD`).
- Nada mais no `.env` diz respeito ao banco diretamente; busca semântica e embeddings são
  controlados por `EMBED_MODE`/`EMBED_MODEL`.

## Referências

- `README.md` na raiz — visão geral e resumo da convenção de nomes (o detalhe está aqui).
- `db/init/*.sql` — 41 arquivos de schema (schemas, tabelas, índices, FKs e seeds); `db/migracoes/*.sql` — os deltas aplicados em banco existente (ver `atualizacao.md`).
- `api/funcoes/db_utils.py` — pool de conexões compartilhado e `count_table`.
- `api/funcoes/ufdr_cache.py` — cache em memória dos anexos lidos do `.ufdr`.
- `api/funcoes/embed_utils.py` — embeddings sem pgvector (cache LRU, invalidação por assinatura).
- `docker-compose.yml` — serviço `postgres` (container `${NOME_PROJETO:-analyser}-postgres`) e volume `./db:/db:ro`.
- `docs/ingestao.md` — como as tabelas de referência são preenchidas.