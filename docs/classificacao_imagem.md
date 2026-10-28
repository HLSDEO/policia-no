# Classificação de imagens

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

A classificação automática de imagens usa um modelo **CLIP** (`openai/clip-vit-base-patch32`)
rodando localmente na CPU — o mesmo modelo (e a mesma instância carregada em memória, via
`_carregar_modelo`) usado pela comparação de rostos (ver [Comparação facial](comparar_rosto.md)).
A ideia é perguntar em linguagem natural se a imagem mostra aquilo: as **categorias** cadastradas
na tela "Categorias de imagem" são termos soltos (ex. "documento de identidade, RG, CNH ou
passaporte") e o sistema responde, para cada imagem, o termo mais provável entre as categorias
ativas (com a confiança).

O resultado (`classificacao_ia`/`confianca_ia`) fica gravado em `ARQ_TB01_ARQUIVOS` e é usado
para filtrar a lista de arquivos e como insumo na mesa de investigação. A fila de classificação é
disparada manualmente pela tela **Arquivos** (não pela tela de categorias — ver Fluxo de uso),
roda em thread daemon à parte (respeitando o limite de jobs pesados — ver Configuração) e o
`POST` devolve o `id_job` pelo qual a tela acompanha progresso e cancelamento.

Implementação: `api/funcoes/classificacao_imagem_utils.py` + `api/rotas/classificacao_imagem.py`;
a tela de administração das categorias é `web/categorias_imagem.html` (JS:
`web/js/categorias_imagem.js`), e o botão que dispara a classificação vive em `web/arquivos.html`
(JS: `web/js/arquivos.js`).

## Fluxo de uso

1. **Categorias** (tela "Categorias de imagem", `web/categorias_imagem.html`): listar/criar/
   excluir/desativar categorias — cada uma com o **termo** (`TERMO`, o que a CLIP compara, ex.
   "passaporte") e o **rótulo de exibição** (`ROTULO_EXIBICAO`, ex. "Documento — Passaporte"). O
   banco já vem com uma carga inicial de 21 categorias (ver Modelo de dados). Só categorias com
   `IN_ATIVO = true` entram na classificação — desativar uma categoria não apaga as imagens já
   classificadas com ela, só a tira da lista de termos da próxima rodada.
2. **Classificar**: na tela **Arquivos**, botão "Classificar imagens por IA" abre um modal de
   confirmação; ao confirmar, o front dispara `POST /api/arquivos/classificar-imagens` com
   `id_inquerito`, `id_extracao` (o da extração aberta na tela, via `?extracao=` na URL, ou `null`
   para o inquérito inteiro) e **`batch_size` fixo em 16** — o campo é ajustável pela API (1–128)
   mas a tela não expõe esse controle nem `max_imagens` (teto de imagens); esses dois só são úteis
   chamando a API diretamente. O sistema processa as imagens da fila: imagens (`no_categoria =
   'Imagem'`) com binário localizado (`co_caminho_zip IS NOT NULL`), sem classificação ainda
   (`classificacao_ia IS NULL`), MIME diferente de `image/webp` e com pelo menos ~50 KB
   (`qt_bytes >= 51200`).
3. **Acompanhar**: a interface faz *polling* de `GET /api/investigacao/jobs/{id_job}` (o id vem
   no POST que disparou) a cada 2 s e mostra processadas/total — ou a posição na fila, enquanto o
   job está em `aguardando_vaga`. Cancelar é `POST /api/investigacao/jobs/{id_job}/cancelar`
   (pedido cooperativo, checado a cada lote de `batch_size` imagens, não a cada imagem) e liberar
   um job travado é `POST /api/investigacao/jobs/{id_job}/liberar` — as duas exigem ser o dono do
   job, gestor do inquérito ou admin. Fechar o modal não para o *polling* da barra da toolbar — a
   classificação roda no servidor, então fechar a janela não pode fazer a porcentagem "congelar"
   sem terminar.
4. **Usar**: no filtro da listagem de arquivos (`filtro-classif-ia`), a UI só oferece **"com"**/
   **"sem"** classificação — a API aceita também o termo exato de uma categoria
   (`classificacao_ia=<termo>`), mas isso não tem controle próprio na tela, só via chamada direta.
   A confiança aparece ao lado do termo na coluna "Classif. IA" (`(confianca_ia*100).toFixed(0)%`).
   Roda **uma classificação por inquérito** por vez (índice único parcial `UX100_JOB_TB01_ATIVO`)
   — a segunda tentativa no mesmo inquérito recebe 409; inquéritos diferentes rodam em paralelo,
   dentro do limite de `JOBS_PESADOS_LIMITE`.

## Como funciona

1. `classificar_imagens_pendentes` recebe o `id_job` e monta a fila (o estado fica em
   `JOB_TB01_JOBS`, não mais em variáveis globais ao processo): `no_categoria = 'Imagem'`, `classificacao_ia IS NULL`,
   `co_caminho_zip IS NOT NULL`, `tp_mime != 'image/webp'`, `qt_bytes >= 51200`, com
   `id_extracao`/`max_imagens` opcionais aplicados como filtro/`LIMIT` na mesma query (sem
   agrupar por `.ufdr` como o OCR faz — cada imagem é buscada individualmente do zip que contém).
2. `_extrair_imagem_bytes` localiza o `.ufdr` (`_localizar_ufdr`) e lê os bytes de dentro do ZIP
   (normalizando separadores `\` → `/` quando o caminho gravado não bate direto — o mesmo ajuste
   usado pelo OCR).
3. `_classificar_lote`: cada imagem é convertida (PIL, modos diferentes de RGB — RGBA, P, CMYK —
   viram RGB), passada pelo `CLIPProcessor` junto com a lista de termos das categorias ativas, e o
   `softmax` sobre os `logits_per_image` (similaridades imagem-texto) dá a distribuição — a
   categoria vencedora é o `argmax`. O resultado é gravado em `classificacao_ia` + `confianca_ia`
   (arredondada em 4 casas decimais) de volta no arquivo. Falha na imagem individual (erro de
   decodificação, etc.) grava `classificacao=None` e conta como erro, sem interromper o lote.
4. `_salvar_resultados` faz um `UPDATE` por imagem (não em lote) e só grava as que tiveram
   classificação válida — imagens que falharam continuam com `classificacao_ia IS NULL` e voltam
   a entrar na fila na próxima rodada (diferente do OCR, que carimba mesmo o resultado nulo para
   não reprocessar).
5. O modelo CLIP é carregado **uma única vez por processo** (`_carregar_modelo`, com
   `threading.Lock` para a corrida entre a 1ª classificação e a 1ª comparação facial, que reaproveita
   a mesma função) e o número de threads do torch é ajustado por `CLIP_NUM_THREADS` antes do
   carregamento. O laço de lotes roda dentro de `with jobs_pesados.vaga("Classificação de
   imagem", ...)` — só o carregamento do modelo e o forward do CLIP disputam o semáforo
   `JOBS_PESADOS_LIMITE`; montar a fila e ler as categorias fica fora da vaga.
6. **Detecção de trava (stale)**: cada lote chama `jobs_utils.progresso(...)`, que grava o avanço
   e bate `DT_ULTIMA_ATIVIDADE`. Cinco minutos sem batimento e `jobs_utils.liberar_jobs_travados()`
   fecha o job, destravando o índice único — `POST /api/investigacao/jobs/{id_job}/liberar` é o
   caminho manual para o mesmo efeito.

## Modelo de dados

- `INVESTIGACAO.CATEGORIAS_IMAGEM` (`db/init/20_arquivos.sql`): `ID_CATEGORIA` (identity),
  `TERMO VARCHAR(500)` (texto que a CLIP compara), `ROTULO_EXIBICAO VARCHAR(200)` (texto
  amigável), `IN_ATIVO BOOLEAN DEFAULT TRUE`, `CRIADO_EM TIMESTAMP DEFAULT NOW()`. Sem `UNIQUE`
  em `TERMO` — nada impede cadastrar o mesmo termo duas vezes.
- Carga inicial (`db/init/20_arquivos.sql`, 21 categorias, todas `IN_ATIVO = TRUE`):
  4 "neutras" (Animal/Pet, Paisagem/Natureza, Objeto Comum, Alimento — comentário no arquivo
  explica que existem "para absorver falsos positivos", evitando que fotos irrelevantes sejam
  forçadas em categorias criminais), Pessoa, Documento de Identidade, e um bloco de
  ilícitos/evidências (Droga, Arma de Fogo, Munição, Arma Branca, Balança de Precisão), valores e
  finanças (Dinheiro em Espécie, Comprovante/PIX, Cartão Bancário, Cheque), documentos e textos
  (Nota Fiscal/Recibo, Contrato/Documento, Planilha/Tabela), tecnologia e mídia (Print de Tela,
  Eletrônico) e veículos/locais (Veículo, Imóvel/Fachada). O comentário do arquivo documenta a
  regra de prompting seguida: frases curtas e descritivas, evitando listas longas de palavras.
- `ARQ_TB01_ARQUIVOS` (`db/init/20_arquivos.sql`): `CLASSIFICACAO_IA VARCHAR(200)` (o
  termo vencedor, não o rótulo de exibição) e `CONFIANCA_IA DOUBLE PRECISION` (0–1), com índice
  `IX_ARQ_TB01_CLASSIFICACAO_IA` sobre `CLASSIFICACAO_IA`.

## Configuração

- `CLIP_NUM_THREADS` — threads do torch para o CLIP (padrão 4); lido de `.env` a cada início de
  rodada (`classificar_imagens_pendentes` e `_carregar_modelo` fazem `parse_env(read_env_text())`
  de novo, sem cache — mudar o valor só tem efeito na próxima rodada, já que o modelo carregado
  fica em memória entre chamadas).
- `JOBS_PESADOS_LIMITE` (padrão 2) — semáforo compartilhado entre OCR, classificação de imagem e
  comparação facial (`api/funcoes/jobs_pesados.py`); os três competem pelos mesmos núcleos de CPU
  do container da API, então o limite vale para o total simultâneo entre os três, não por tipo de
  job.
- Sem Tesseract/CLIP instalados na imagem do servidor, a rota de classificação não valida
  disponibilidade antes de iniciar (diferente do OCR, que checa `ocr_disponivel()` e devolve 503) —
  a falha aparece só quando `_carregar_modelo` tenta baixar/carregar os pesos e lança exceção.

## Referências

- `api/funcoes/classificacao_imagem_utils.py` — fila, modelo CLIP, lote, progresso/cancelamento.
- `api/rotas/classificacao_imagem.py` — `/api/categorias-imagem` (CRUD + alternar-ativo, escrita
  só de admin no `router_admin`) e `POST /api/arquivos/classificar-imagens` (dispara e devolve o
  `id_job`); progresso, cancelamento e liberação ficam nas rotas genéricas de `api/rotas/jobs.py`.
- `api/funcoes/jobs_utils.py`, `db/init/41_jobs.sql` — o estado do job (`TP_JOB =
  'classificacao_imagem'`).
- `db/init/20_arquivos.sql` — tabela `CATEGORIAS_IMAGEM` e colunas em `ARQ_TB01_ARQUIVOS`.
- `db/init/20_arquivos.sql` — categorias iniciais e regras de prompting.
- `web/categorias_imagem.html` (JS `web/js/categorias_imagem.js`) — CRUD de categorias.
- `web/arquivos.html` (JS `web/js/arquivos.js`) — botão de classificar, modal de
  progresso, filtro "Classif. IA" e coluna da listagem.
- Relacionados: `comparar_rosto.md` (mesmo modelo CLIP, mesmo `_carregar_modelo`, mesmo limite de
  jobs pesados), `ocr_imagem.md` (mesma mecânica de fila/progresso/batimento, mesmos limites de
  tamanho mínimo de imagem), `banco_dados.md` (`JOB_TB01_JOBS`).
