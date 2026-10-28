# Jobs & Filas (administração de processamento em segundo plano)

## Visão geral

Todo trabalho longo do sistema — OCR, classificação de imagem, comparação facial, importação de
base de referência, download de modelo no Ollama, cópia de pasta de rede, ZIP, documento pesado,
consolidação da IA e extração temporária de mídia — é um **job** com estado no banco
(`INVESTIGACAO.JOB_TB01_JOBS`), não mais um dicionário de módulo na memória do processo. A tela
**Administração → Jobs & Filas** (`web/filas.html`, só para administradores) mostra as duas filas
que existem no sistema em um lugar só e responde, sem SSH, à pergunta "por que meu OCR está na
fila?":

- **Jobs de processamento** — o que está rodando, o que espera vaga de CPU, de qual inquérito é e
  de quem é; com barra de progresso, posição na fila, tempo decorrido e botões **Cancelar** /
  **Liberar**;
- **Fila de extrações (`.ufdr`)** — a outra fila, durável: `INQ_TB02_EXTRACOES` com a fase e o
  progresso que o worker grava, quem está esperando e quantas vagas simultâneas (locais e em nós)
  estão em uso.

Antes da tabela de jobs, o estado vivia num dicionário de módulo e tinha três defeitos que só
apareciam com dois analistas usando o sistema ao mesmo tempo: o guard "já existe um em andamento"
valia para o servidor inteiro (o segundo analista levava 409 de graça), o cancelamento parava o job
de quem quer que estivesse rodando, e o GET de progresso da comparação facial devolvia os rostos do
inquérito alheio. Nada disso sobrevivia a `--workers > 1` (o POST caía num processo e o polling em
outro). Com o estado no banco, o polling cai em qualquer worker — inclusive nas duas réplicas da
API (`infraestrutura.md`) — e encontra o job.

## Fluxo de uso

1. Admin entra pela barra do topo em **Administração → Jobs & Filas** (também alcançável pela busca
   global e pelo Ctrl+K do tema clássico — item `filas.html` em `MapaTelas.TELAS_BUSCA`).
2. Os **KPIs do topo** resumem o servidor agora: vagas de CPU em uso (`x / limite`), jobs na fila,
   jobs processando, extrações locais em execução (com o acréscimo "· N em nós") e extrações
   aguardando.
3. No card **Jobs de processamento**: filtro por status (Em andamento / Pendentes / Aguardando
   vaga / Processando / Concluídos / Cancelados / Erro / Todos (histórico)), por tipo (o seletor é
   preenchido com os rótulos que a API devolve) e busca por número do inquérito, usuário, escopo ou
   tipo. Cada linha traz dono, badge de status (com "cancelamento pedido" quando `FL_CANCELAR` está
   ligado), barra `processados / total`, erros, posição na fila ("N na fila à frente"), tempo
   decorrido e o `host:pid` do worker que toca o job.
4. **Cancelar** pede a parada (`FL_CANCELAR`): o worker ancora na próxima etapa e o job vira
   `cancelado` — funciona até com o job ainda na fila de vagas. **Liberar** encerra o job na mão
   (vira `erro` e libera a vaga de CPU): é a saída manual para o processo que morreu sem que o
   batimento tenha ceifado o job ainda. As duas ações pedem confirmação em diálogo e reusam as
   rotas do dono do job.
5. No card **Fila de extrações**: filtro por status e busca por inquérito/arquivo/dispositivo/
   usuário; linha em execução mostra a fase (`cd_fase`) com o progresso da fase; pendente mostra
   quantas estão na frente (por data de upload, só as do central — as de nó têm fila própria).
6. A tela se atualiza sozinha a cada **5 s** (um `setTimeout` por vez — se o servidor atrasar, o
   próximo ciclo só começa depois da resposta; aba oculta não sonda). Quem não é admin vê só
   "Esta página é só para administradores."

## Como funciona

### A tabela de jobs (`api/funcoes/jobs_utils.py`)

- **Tipos** (`ROTULOS`): `ocr_imagem`, `ocr_imagem_selecao` (a seleção parcial pedida pela IA no
  chat — tipo separado de propósito para o guard não deixar a seleção da IA colidir com o OCR
  completo da tela Arquivos), `classificacao_imagem`, `comparacao_facial`, `import_referencia`,
  `ollama_pull`, `copia_rede`, `import_zip`, `import_documento`, `consolidacao_ia`,
  `midia_temporaria`.
- **O guard "um por vez" é o banco**: índice único parcial `UX100_JOB_TB01_ATIVO` sobre
  `(TP_JOB, COALESCE(ID_INQUERITO,0), COALESCE(NO_ESCOPO,''))` nos status ativos. Segundo POST do
  mesmo inquérito vira `JobOcupado` → **409 com o nome de quem está com o job e desde quando**
  (a mensagem "já existe um em andamento" sozinha não ajuda a decidir se espera ou procura o
  colega); o de outro inquérito passa. Ser índice, e não SELECT+INSERT, fecha a corrida de dois
  cliques.
- **Job global** (import de base, pull do Ollama, cópia de rede) entra sem inquérito, usando
  `NO_ESCOPO` como chave (grupo de import, nome do modelo).
- **Ciclo de vida**: `pendente → aguardando_vaga → processando → concluido | cancelado | erro`.
  `progresso()` grava contadores, bate `DT_ULTIMA_ATIVIDADE` e devolve `FL_CANCELAR` numa única ida
  ao banco — o laço de trabalho recebe "devo parar?" de volta. **Guarda de status**: os UPDATE só
  valem enquanto o job está ativo, então um worker ceifado pelo reaper não ressuscita a linha para
  `concluido` nem queima CPU num job morto.
- **Reaper** (`liberar_jobs_travados`): fecha jobs sem batimento há mais de `MINUTOS_JOB_STALE`
  (default 30 min, piso 5). O predicado é puramente temporal — com vários workers não há como
  distinguir processo morto de vivo, e um "libera tudo no startup" mataria o trabalho de um colega.
  `criar()` chama o reaper antes de tentar, para um processo morto não segurar o índice único para
  sempre.
- **Resultado** (`TX_RESULTADO`, teto de 256 KB): é como a comparação facial devolve a lista de
  rostos parecidos ao dono — visível só para quem pode ver o inquérito, ao contrário do
  `/progresso` antigo.

### A vaga de CPU (`api/funcoes/jobs_pesados.py`)

OCR, classificação e comparação facial disputam os mesmos núcleos. O limite
(`JOBS_PESADOS_LIMITE`, default 2) é **do sistema, não do processo**: a vaga é a própria linha em
`JOB_TB01_JOBS` com `FL_PESADO` e `CD_STATUS='processando'`, contada sob um `pg_try_advisory_xact_lock`
(lock de transação — dura milissegundos, não segura conexão; lock de sessão por horas era
justamente o que o projeto evitou). A reivindicação é um único UPDATE atômico: pega a vaga se a
contagem de pesados `processando` ainda couber no limite.

`jobs_pesados.vaga(id_job, nome)` envolve **só o trecho de CPU pesada** (o forward do CLIP, o
Tesseract), nunca a preparação nem a gravação do resultado. Enquanto espera, o job fica em
`aguardando_vaga`, continua batendo (o reaper não o confunde com travado) e tenta de novo a cada
20 s — e cancelar na fila funciona na hora. A vaga é liberada pela transição de status em
`jobs_utils` (concluir/falhar/cancelar), não pelo context manager. O invariante de dimensionamento
é `JOBS_PESADOS_LIMITE × CLIP_NUM_THREADS ≤ núcleos/2` (`desempenho_utils.py`, ver
`interface.md` — tela Desempenho).

### Rotas (`api/rotas/jobs.py`)

- `GET /api/investigacao/jobs/{id_job}` — estado de um job (o polling do front); acresce
  `qt_na_frente` quando o job espera vaga.
- `GET /api/investigacao/inqueritos/{id}/jobs` — jobs do inquérito (default: só os ativos).
- `GET /api/investigacao/inqueritos/{id}/jobs/{tp_job}` — o último job do tipo no inquérito, ativo
  ou terminado: é o que a tela pede ao abrir para **retomar o acompanhamento** de um job que ficou
  rodando (ou mostrar o resultado do que acabou). Devolve `null` se nunca houve — e nunca devolve o
  job de outra delegacia.
- `POST /api/investigacao/jobs/{id_job}/cancelar` e `.../liberar` — só o dono, um gestor do
  inquérito ou admin (`jobs_utils.pode_controlar`).
- `GET /api/admin/jobs` (admin) — jobs do servidor com `nu_inquerito`, `no_usuario` e `qt_na_frente`
  resolvidos numa consulta só (sem N+1), mais `vagas` (ocupação), `tipos` e `resumo` por status.
- `GET /api/admin/filas/extracoes` (admin) — a fila de `.ufdr` espelhando o que o despachante
  (`investigacao_utils._despachante_extracoes`) enxerga, com `qt_na_frente` por data de upload e o
  resumo (pendentes, rodando local, rodando em nós, limite local de `EXTRACOES_SIMULTANEAS`).

Visibilidade (`_job_visivel`): job com inquérito segue o acesso ao inquérito (404 se não existe,
403 se é de outro); job global é só do dono ou de admin. O OCR pedido no chat acompanha o job pelo
mesmo trilho — `CO_JOBS` da mensagem de IA aponta para `/api/investigacao/jobs/{id_job}`
(`chat_ia.md`, `ocr_imagem.md`).

### O front (`web/js/filas.js`, `web/css/filas.css`)

Um `setTimeout` por vez (nunca dois ciclos em voo; duas abas abertas não dobram a sondagem),
`document.hidden` pula o ciclo, busca com debounce de 400 ms, filtros de status/tipo e botão
Atualizar manual. 403 troca a tela para o aviso de sem permissão; erros de rede depois de
autorizado viram "sem resposta do servidor" no carimbo, sem derrubar a tabela.

## Modelo de dados

| Tabela | O que é |
|---|---|
| `INVESTIGACAO.JOB_TB01_JOBS` (`db/init/41_jobs.sql`) | `ID_JOB` (PK), `TP_JOB`, `ID_INQUERITO` (nulo = job global), `ID_EXTRACAO` (escopo opcional), `NO_ESCOPO` (chave do job global), `ID_USUARIO` (dono), `CD_STATUS` (CK: `pendente\|aguardando_vaga\|processando\|concluido\|cancelado\|erro`), `QT_TOTAL/QT_PROCESSADOS/QT_ERROS`, `FL_CANCELAR`, `FL_PESADO` (disputa vaga de CPU), `TX_ERRO`, `TX_PARAMETROS`/`TX_METRICAS`/`TX_RESULTADO` (JSONB), `NO_WORKER` (`host:pid`, diagnóstico), `ID_SERVIDOR` (nó que reivindicou o job — nulo roda no central; sem FK de propósito), `DT_INICIO`, `DT_ULTIMA_ATIVIDADE` (batimento; o reaper usa), `DT_FIM` |
| índices | `UX100_JOB_TB01_ATIVO` (guard único parcial), `IX252` (polling por inquérito/tipo), `IX253` (vagas/reaper, parcial nos ativos), `IX254` (jobs por usuário) |
| `INVESTIGACAO.INQ_TB02_EXTRACOES` | a segunda fila: `CD_STATUS` pendente/processando + `CD_FASE`/`QT_PROGRESSO_FASE` + `DT_UPLOAD` (ordem da fila) + `ID_SERVIDOR` (extração em nó) |

A mesma tabela também guarda os jobs que **não** são pesados (`FL_PESADO=false`): importação de ZIP
e de documento (I/O), consolidação da IA (disputa cota de IA, não CPU) e extração temporária de
mídia. Ver `banco_dados.md` (`JOB_TB{NN}`).

## Configuração (`.env`)

| Variável | Default | O que faz |
|---|---|---|
| `JOBS_PESADOS_LIMITE` | 2 | vagas simultâneas de CPU para OCR + classificação + comparação facial, no sistema inteiro |
| `MINUTOS_JOB_STALE` | 30 (piso 5) | sem batimento por mais que isso, o reaper fecha o job como `erro` (ou `cancelado` se havia cancelamento pedido) |
| `EXTRACOES_SIMULTANEAS` | ver `infraestrutura.md` | vagas do despachante de extrações `.ufdr` no central |

## Referências

- Backend: `api/funcoes/jobs_utils.py` (ciclo de vida, guard, reaper), `api/funcoes/jobs_pesados.py`
  (vaga de CPU), `api/rotas/jobs.py` (rotas), `api/funcoes/investigacao_utils.py`
  (`_despachante_extracoes`).
- Tela: `web/filas.html`, `web/js/filas.js`, `web/css/filas.css`; entrada em
  `web/js/utils/mapa_telas.js` (`filas.html`, admin).
- Quem cria jobs: `ocr_utils.py`, `classificacao_imagem_utils.py`, `comparar_rosto_utils.py`,
  `zip_import.py`, `documentos_utils.py`, `rede_smb.py`, `ia_investigacao.py` (consolidação),
  `midia_temp.py`, `ingestao`, `configuracoes_ia` (pull do Ollama).
- Documentos relacionados: [ocr_imagem.md](ocr_imagem.md), [classificacao_imagem.md](classificacao_imagem.md),
  [comparar_rosto.md](comparar_rosto.md), [importacao.md](importacao.md),
  [extração_ufdr.md](extração_ufdr.md) (a fila de extrações), [banco_dados.md](banco_dados.md),
  [interface.md](interface.md) (tela Desempenho — dimensionamento), [no_descentralizado.md](no_descentralizado.md)
  (jobs com `ID_SERVIDOR`), [chat_ia.md](chat_ia.md) (cartão de progresso via `CO_JOBS`).
