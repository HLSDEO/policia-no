# OCR de imagens de aparelho

> **Estado do job (mudou).** Progresso, cancelamento e o guard de "já existe um em andamento" não
> vivem mais num dicionário de módulo: são uma linha em `INVESTIGACAO.JOB_TB01_JOBS`
> (`api/funcoes/jobs_utils.py`, `db/init/41_jobs.sql`). O que isso muda na prática:
>
> - **o escopo do "só um por vez" é o inquérito**, não o servidor. Dois analistas em inquéritos
>   diferentes rodam ao mesmo tempo; antes o segundo levava 409 por causa do job do primeiro,
>   ainda que de outra delegacia;
> - **cancelar e liberar exigem ser o dono** (ou gestor do inquérito, ou admin). Antes qualquer
>   usuário aprovado parava o job de qualquer outro;
> - **o progresso é lido por `id_job`**, em `GET /api/investigacao/jobs/{id_job}`. Os antigos
>   `/progresso`, `/cancelar` e `/reset` desta funcionalidade não existem mais — foram
>   substituídos pelas rotas genéricas de `api/rotas/jobs.py`;
> - **job travado é detectado por batimento**: `DT_ULTIMA_ATIVIDADE` sem atualização há 5 minutos
>   (`jobs_utils.liberar_jobs_travados`), e não mais por um `_progresso_estale()` de processo;
> - **a espera por vaga é visível**: enquanto não há vaga entre os jobs pesados, o job fica em
>   `aguardando_vaga` e a tela mostra a posição na fila, em vez de uma barra parada em 0%.


## Visão geral

O OCR extrai o texto de imagens (prints de conversa, capturas de tela, fotos de documentos) dos
arquivos extraídos do aparelho, usando o **Tesseract** instalado localmente no servidor (CPU, sem
chamada externa). O resultado vai para `ARQ_TB01_ARQUIVOS.TX_OCR` e passa a alimentar:

- a **busca de arquivos** da tela Arquivos (filtro "OCR: com/sem/pendente" e busca de texto nos
  arquivos por trigrama);
- o **contexto da IA** — a ferramenta `buscar_texto_em_imagens` devolve trechos do texto
  OCRizado (até 2000 caracteres por imagem) para a resposta do caso;
- a **detecção de padrões** — a fonte de texto `arquivo` em `_FONTES_TEXTO`
  (`api/funcoes/padroes_utils.py`) varre `TX_OCR IS NOT NULL`; sem OCR, print de comprovante PIX,
  boleto ou conversa printada não existem como texto para nenhum padrão léxico/regex alcançar.

A fila é disparada manualmente pela tela Arquivos, roda em thread daemon à parte (respeitando o
limite global de jobs pesados — ver Configuração) e o `POST` devolve o `id_job` pelo qual a tela
acompanha o progresso. A IA do chat pode pedir OCR **parcial** (ferramenta
`processar_texto_em_imagens`, ver adiante) — nunca o corpus inteiro. Se o
Tesseract não estiver disponível no servidor, `POST /api/arquivos/ocr-imagens` responde 503 com a
mensagem de habilitação (checagem feita **antes** de disparar a thread, diferente da classificação
de imagem, que só falha depois, ao tentar carregar o CLIP).

Implementação: `api/funcoes/ocr_utils.py`; rotas em `api/rotas/ocr_imagem.py`; tela em
`web/arquivos.html` (JS `web/js/arquivos.js`).

## Fluxo de uso

1. Na tela Arquivos, escolher o inquérito (e, opcionalmente, a extração via `?extracao=` na URL e
   `max_imagens`) e clicar em "Extrair texto (OCR)". Um lote padrão de 20 imagens por rodada é
   processado (`lote`, ajustável 1–200 na API — a tela não expõe esse campo, sempre manda o
   padrão).
2. Acompanhar o progresso (imagens processadas/total — o progresso em porcentagem avança por
   imagem concluída, não por bytes lidos). Cancelar interrompe cooperativamente (checado a cada
   imagem, dentro do lote); reset limpa estado travado.
3. Na listagem, filtrar por situação do OCR: **com** texto (existe `TX_OCR`), **sem** (`TX_OCR`
   nulo mas `DT_OCR` preenchido — já rodou e não achou nada) ou **pendente** (`DT_OCR IS NULL`,
   ainda não processada). O texto fica disponível no modal da imagem.
4. Há também a opção de OCR **de uma imagem específica** (detalhe da imagem, sem entrar na fila —
   útil para reprocessar uma imagem com problema de orientação ou testar configuração) e o status
   por extração — `GET /api/arquivos/ocr-extracao/{id}/status` responde `completo`, `parcial`,
   `pendente` ou `sem_imagens` (para o card da extração decidir entre mostrar "Extrair texto
   (OCR)" ou "Refazer OCR", e para orientar a re-detecção de padrões).
5. `POST /api/arquivos/ocr-imagens/resetar` marca imagens como não processadas para forçar
   reprocessamento (todo o inquérito, uma extração específica, ou filtrando por `mime_types`) —
   usado depois de melhorias no reconhecimento ou quando um lote saiu com problema; não tem botão
   dedicado na tela hoje, é chamada direta na API.
6. O texto OCRizado entra na pesquisa do caso pela IA (ferramenta `buscar_texto_em_imagens`) e na
   detecção de padrões de interesse.

### OCR parcial a pedido da IA (chat)

A ferramenta `processar_texto_em_imagens` (`api/funcoes/ia_investigacao.py`) deixa a IA disparar o
reconhecimento de um **recorte** de imagens quando o investigador concorda (a descrição da
ferramenta manda avisar antes):

- **Escopo nunca é o corpus inteiro**: ou `ids_arquivos` explícitos (saídos de
  `buscar_arquivos_dispositivo`), ou recorte por `termo` (nome/caminho) + `inicio`/`fim`
  (`DT_MODIFICACAO`, dia final inclusivo) + `id_extracao` — com teto rígido de **200 imagens por
  chamada** (`_LIMITE_SELECAO`), deduplicando ids e cortando o excedente com aviso. Só entram
  imagens **pendentes** (`DT_OCR IS NULL`) com binário; já-processadas e inválidas voltam como
  `ignoradas` para a IA justificar o recorte. Resolução em `ocr_utils.fila_ocr_selecionada`.
- **Job de tipo próprio** (`ocr_imagem_selecao`): o guard único por (tipo, inquérito) deixa a
  seleção da IA rodar em paralelo com um OCR completo da tela, sem colidir. O dono (`ID_USUARIO`) é
  quem estava conversando — cancelamento/liberação pelas rotas genéricas de jobs valem para ele.
  O worker `ocr_utils.ocr_imagens_selecionadas` é o mesmo laço do lote (vaga de job pesado,
  progresso/batimento por lote, cancelamento cooperativo) — extraído para `_processa_grupos`.
- **Assíncrono de propósito**: com `_TIMEOUT_IMAGEM` de 60 s, um lote travaria o turno do chat. A
  primeira chamada cria o job e devolve `id_job`; a mesma ferramenta chamada com `id_job` devolve
  status (`aguardando_vaga` inclui a posição na fila de jobs pesados); concluído, o texto sai por
  `buscar_texto_em_imagens` — e o job aparece na listagem de jobs da tela Arquivos.
- **Disponibilidade da ferramenta**: só existe em conversas com usuário dono (`id_usuario`
  injetado por binding — nunca a critério da IA). Sem isso — busca automática de entidades, cálculo
  de tamanho de prompt — a ferramenta some do schema, e o chat continua 100% leitura.

## Como funciona

1. **Fila** (`_fila_ocr`): imagens pendentes — `no_categoria = 'Imagem'`, `dt_ocr IS NULL`,
   binário localizado (`co_caminho_zip IS NOT NULL`), `tp_mime <> 'image/webp'` e `qt_bytes >=
   102400` (~100 KB — abaixo disso costuma ser ícone/thumbnail de app, sem texto útil, e só
   infla a fila). `webp` fica de fora pelo mesmo motivo da classificação por CLIP: além de ser
   figurinha (sem texto de interesse), boa parte do que o UFDR grava com essa extensão em
   `files/Archives/` nem é imagem — é um ZIP de pacote de figurinhas com nome de `.webp`. As
   imagens voltam agrupadas por `.ufdr` (`{no_arquivo: [(id_arquivo, caminho_zip), ...]}`) para
   abrir cada ZIP uma única vez por lote em vez de uma vez por imagem.
2. **Preparo**: `_corrigir_orientacao_imagem` lê a tag EXIF 274 (Orientation) e aplica a rotação/
   flip correspondente antes do OCR — o Tesseract tem dificuldade com imagem rotacionada; falha ao
   ler EXIF é silenciosa (devolve a imagem como veio). A imagem é convertida para `L` ou `RGB`
   (outros modos viram `RGB`); se o lado maior passar de `_LADO_MAX` (2500 px) é redimensionada —
   acima disso o Tesseract fica lento sem ganho de qualidade.
3. **Reconhecimento**: `pytesseract.image_to_string(...)` com idioma configurável (padrão
   `por+eng` — tenta os dois) e `--psm` configurável (padrão 3, segmentação automática — o modo
   certo para print de tela e foto de documento, com blocos de texto em posições variadas),
   timeout por imagem (`_TIMEOUT_IMAGEM` = 60 s — um PNG patológico não pode travar a varredura
   inteira).
4. **Limpeza** (`_limpar_texto`): colapsa espaços/tabs repetidos e linhas em branco em excesso
   (`\n{3,}` → `\n\n`) que o Tesseract produz de sobra; o resultado só é mantido se tiver pelo
   menos `_MIN_CARACTERES` (4) **e** alguma letra ou dígito (`[0-9A-Za-zÀ-ÿ]`) — imagens sem texto
   útil (ruído de borda, linhas espúrias) ficam com `TX_OCR` nulo, mas `DT_OCR` é gravado do mesmo
   jeito.
5. **Persistência** (`_salvar_resultados`): `TX_OCR` + `DT_OCR = now()` gravados no arquivo, um
   `UPDATE` por imagem — inclusive quando `TX_OCR` é `None`, porque é exatamente essa combinação
   (`DT_OCR` preenchido, `TX_OCR` nulo) que marca "já processou, não achou nada" e impede a imagem
   de voltar para a fila na próxima varredura. Erro de decodificação (binário corrompido, ou
   arquivo que só tem nome de imagem) recebe o mesmo tratamento — carimba `DT_OCR` mesmo assim,
   pelo mesmo motivo.
6. **Integrações**: filtro de listagem de arquivos (`a.tx_ocr ILIKE %termo%`, via índice trigrama);
   ferramenta da IA `buscar_texto_em_imagens` (`left(a.tx_ocr, 2000)`, com filtro opcional por
   `termo`/`id_extracao`, `LIMIT` padrão 50); padrões léxico/regex — fonte de texto `arquivo` em
   `_FONTES_TEXTO` (`api/funcoes/padroes_utils.py`), com o **mesmo `tp_origem` das
   marcações/entidades da tela Arquivos** (não um tipo de origem novo), para que "Criar entidade" a
   partir de uma detecção continue funcionando; a data usada é `dt_modificacao` do arquivo (o
   carimbo que o UFDR dá à imagem), não `dt_ocr`.
7. **Vida útil do job**: uma linha em `JOB_TB01_JOBS` com `TP_JOB='ocr_imagem'` e o inquérito no
   escopo — o índice único parcial `UX100_JOB_TB01_ATIVO` é o guard de "um por inquérito", e é ele
   (e não uma checagem antes do INSERT) que fecha a corrida entre dois cliques simultâneos. Cada
   lote chama `jobs_utils.progresso(...)`, que numa ida ao banco grava o avanço, bate
   `DT_ULTIMA_ATIVIDADE` e devolve o `FL_CANCELAR`. Cinco minutos sem batimento e
   `jobs_utils.liberar_jobs_travados()` fecha o job. `jobs_pesados.vaga(id_job, "OCR")` envolve só
   o laço de reconhecimento (a montagem da fila fica fora), disputando o limite global
   `JOBS_PESADOS_LIMITE` com a classificação de imagem e a comparação facial.

## Modelo de dados

- `ARQ_TB01_ARQUIVOS` (`db/init/20_arquivos.sql`):
  - `TX_OCR` (text) — texto reconhecido (nulo quando não há texto legível ou ainda não
    processada — as duas situações se distinguem por `DT_OCR`);
  - `DT_OCR` (timestamp) — quando foi processada (nulo = pendente; preenchido = já passou pelo
    OCR, com ou sem texto).
  - Índices: `IX77_ARQ_TB01_TX_OCR_TRGM` (`gin gin_trgm_ops` em `TX_OCR`, para o `ILIKE` da busca
    de texto — `pg_trgm` mora no schema `public`, daí o `search_path` da migração incluir
    `public`) e `IX78_ARQ_TB01_OCR_PENDENTE` (índice parcial em `ID_EXTRACAO` `WHERE dt_ocr IS
    NULL`, para a fila).

## Configuração

- `OCR_IDIOMA` — idioma(s) do Tesseract, sintaxe própria do Tesseract (padrão `por+eng` — tenta
  português e inglês).
- `OCR_PSM` — modo page-segmentation (padrão 3, automático).
- `JOBS_PESADOS_LIMITE` (padrão 2) — semáforo compartilhado entre OCR, classificação de imagens e
  comparação facial (CPU) — ver `api/funcoes/jobs_pesados.py`.
- `CLIP_NUM_THREADS` não afeta o OCR (o Tesseract não usa torch); é só a variável da classificação
  de imagem/comparação facial.
- O build da imagem do servidor precisa instalar `tesseract-ocr` + `tesseract-ocr-por` (ver
  Dockerfile); `ocr_disponivel()` chama `pytesseract.get_tesseract_version()` para checar — se
  falhar, `POST /api/arquivos/ocr-imagens` e `POST /api/arquivos/ocr-imagem-especifica` devolvem
  503 antes de iniciar qualquer trabalho.

## Referências

- `api/funcoes/ocr_utils.py` — fila, preparo de imagem, Tesseract, limpeza, progresso/
  cancelamento/reset, OCR sob demanda, status por extração.
- `api/rotas/ocr_imagem.py` — `POST /api/arquivos/ocr-imagens` (lote),
  `POST .../ocr-imagem-especifica`, `POST .../ocr-imagens/resetar`,
  e — genéricas, em `api/rotas/jobs.py` — `GET /api/investigacao/jobs/{id_job}`,
  `POST /api/investigacao/jobs/{id_job}/cancelar` e `.../liberar`,
  `GET .../ocr-extracao/{id}/status`.
- `api/funcoes/ia_investigacao.py` — ferramentas `buscar_texto_em_imagens` (leitura) e
  `processar_texto_em_imagens` (OCR parcial sob pedido da IA, tipo `ocr_imagem_selecao`).
- `api/funcoes/padroes_utils.py` — fonte de texto `arquivo` em `_FONTES_TEXTO`.
- `db/init/20_arquivos.sql` — colunas `TX_OCR`/`DT_OCR` e índices.
- `web/arquivos.html` / `web/js/arquivos.js` — botão, modal de progresso, filtros e modal de
  texto OCR da imagem.
- Relacionados: `classificacao_imagem.md` (mesma mecânica de fila/progresso/stale, mesmo
  semáforo de jobs pesados), `comparar_rosto.md`, `padroes_crime.md` (detecção de padrões),
  `chat_ia.md` (ferramenta `buscar_texto_em_imagens`).
