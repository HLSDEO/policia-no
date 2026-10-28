# Comparação facial

> **Estado do job (mudou).** Progresso, cancelamento e o guard de "já existe um em andamento" não
> vivem mais num dicionário de módulo: são uma linha em `INVESTIGACAO.JOB_TB01_JOBS`
> (`api/funcoes/jobs_utils.py`, `db/init/41_jobs.sql`). O que isso muda na prática:
>
> - **o escopo do "só um por vez" é o inquérito**, não o servidor. Dois analistas em inquéritos
>   diferentes rodam ao mesmo tempo; antes o segundo levava 409 por causa do job do primeiro,
>   ainda que de outra delegacia. Comparar um aparelho só (`?extracao=`) não muda isso: o índice
>   único é `(TP_JOB, ID_INQUERITO, NO_ESCOPO)` — `ID_EXTRACAO` entra na linha para registro, mas
>   duas comparações no mesmo inquérito, mesmo de aparelhos diferentes, continuam alternando;
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

A comparação facial encontra, entre as imagens de um inquérito, as mais parecidas com uma foto
enviada pelo investigador — por exemplo, verificar se o rosto em um print de conversa aparece em
fotos do aparelho extraído. O método é **baseado em embeddings de CLIP**
(`openai/clip-vit-base-patch32`): não é um reconhecedor facial dedicado (MTCNN/FaceNet), mas a
similaridade entre o vetor da imagem enviada e os vetores das imagens do inquérito — suficiente
para o objetivo e barato em CPU (leitura em lote e produto de matrizes NumPy). O modelo é
carregado pela mesma função (`_carregar_modelo`, em `classificacao_imagem_utils.py`) usada pela
classificação de imagens — a 1ª chamada de qualquer uma das duas features paga o custo de carregar
os pesos.

Os embeddings já calculados ficam na tabela `ARQ_TB02_EMBEDDING_IMAGEM` (float16, ~1 KB por
imagem) e são **reaproveitados** entre execuções — a mesma imagem nunca é reprocessada, só a
imagem de referência muda a cada comparação. A comparação roda em background (respeitando o
semáforo de jobs pesados) com progresso e resultados com preview e origem.

Implementação: `api/funcoes/comparar_rosto_utils.py` + `api/rotas/comparar_rosto.py`; tela em
`web/comparar_rosto.html` (JS `web/js/comparar_rosto.js`).

## Fluxo de uso

1. Na tela de Comparação facial, escolher o inquérito, o **aparelho** (ou "Todos os aparelhos do
   inquérito") e quantos resultados exibir (20/50/100), arrastar (ou selecionar) a foto de
   referência (`accept="image/*"`, drag-and-drop com preview via `URL.createObjectURL` — evita
   carregar a imagem como base64 na memória da aba) e clicar em "Comparar rostos". O `?extracao=`
   que as telas de extração usam no deep-link já vem selecionado e é honrado pela varredura.
2. `POST /api/investigacao/inqueritos/{id}/comparar-rosto` valida: arquivo enviado (multipart),
   `content_type` começando com `image/`, conteúdo não vazio, tamanho até 10 MB
   (`MAX_TAMANHO_UPLOAD`, lido em blocos por `ler_limitado`; a tela também confere os 10 MB antes
   de subir) e — quando enviado — `extracao` pertencente àquele inquérito
   (`extracao_pertence_inquerito`, para um id de outra delegacia não
   virar filtro da varredura). Esses 10 MB só são alcançáveis porque o nginx declara
   `client_max_body_size 520m` (`nginx/default.conf`): com o default de 1 MB do nginx, qualquer
   foto de celular era barrada no proxy antes de chegar aqui. O `limite` de resultados vem do
   formulário (o teto de 100 continua sendo do servidor, em `min(limite, 100)`).
3. Acompanhar o progresso em `GET /api/investigacao/jobs/{id_job}` (o id vem no POST; *polling* a cada
   800 ms) — inclui o total de imagens, processadas, **reaproveitadas** (já tinham embedding
   salvo) e erros; a tela usa a diferença entre processadas e reaproveitadas para estimar o tempo
   restante, já que as reaproveitadas saem do cache instantaneamente e distorceriam a ETA se
   entrassem na conta. Cancelar via `POST /api/investigacao/jobs/{id_job}/cancelar` (rota genérica
   de jobs), no botão "Cancelar" que aparece enquanto o job está ativo — o cancelamento é obedecido
   no fim do lote em curso, então a tela continua o polling até o job fechar e mostra o que já
   tinha sido calculado.
   Ao abrir a tela (e a cada troca de inquérito) ela pergunta o **último** job do inquérito em
   `GET /api/investigacao/inqueritos/{id}/jobs/comparacao_facial`: se ainda estiver rodando, o
   acompanhamento é retomado (um F5 no meio da varredura não deixa mais o job invisível); se já
   terminou, o resultado anterior volta sem novo upload. Um 409 no POST — outra comparação do mesmo
   inquérito em andamento — vira uma oferta de cancelar a do colega e reenviar, em vez de só
   devolver o texto do erro.
4. Resultado: os arquivos mais parecidos, ordenados pela similaridade (0–100%, com cor verde ≥
   80%, amarelo ≥ 60%, vermelho abaixo — as três faixas vêm de `.cmp-sim-alta/.media/.baixa` em
   `css/comparar_rosto.css`, sobre os tokens `--success/--warning/--danger` do tema), com thumbnail
   (`GET /api/investigacao/comparar-rosto/arquivo/{id}/preview` — redimensiona para 400 px,
   qualidade 70, com cache em `ufdr_cache`). Um clique na miniatura abre a imagem inteira em modal
   (`GET /api/investigacao/arquivos/{id}/conteudo?preview=true`, que reencoda em JPEG e abre até
   HEIC de iPhone), e "Ver origem" abre
   `GET /api/investigacao/comparar-rosto/arquivo/{id}/origem` (junta arquivo + extração +
   inquérito, com link direto para a tela Arquivos já filtrada pelo nome do arquivo). Busca,
   similaridade mínima e ordenação são filtros **no cliente**, sobre o resultado já calculado, e o
   botão Excel (`js/utils/exportar_excel.js`) exporta exatamente o que está visível.
5. Se a imagem enviada não puder ser processada (ex.: formato que o PIL não abre), aparece a
   mensagem "Não foi possível processar a imagem enviada." e o progresso encerra sem resultados.

## Como funciona

### Embeddings

- `_carregar_pixels` lê a imagem de dentro do `.ufdr` via `ufdr_cache.obter_zip` (propositalmente
  **sem** o cache de conteúdo — a imagem é lida uma única vez por varredura, e guardá-la
  despejaria do cache justamente os anexos de conversa que a tela está usando em paralelo); tenta
  o caminho como veio e, se falhar, com `\` trocado por `/`.
- `_preparar_pixels`: reproduz o `CLIPImageProcessor` à mão em vez de usá-lo — converte para RGB,
  redimensiona bicúbico até o lado menor bater `_TAM_ENTRADA` (224 px), faz **center-crop** para
  224×224 e devolve pixels crus em `uint8`; a divisão por 255 e a normalização (média/desvio do
  preprocessor_config.json do modelo) são feitas de uma vez só para o lote inteiro em
  `_tensor_do_lote`. A vantagem sobre o `CLIPProcessor` de fato é que o pré-processamento manual é
  **paralelizável** (o processor só aceita o lote inteiro de uma vez, na thread que chamou);
  reproduzir bit a bit o mesmo resultado evita cópias intermediárias.
- `_embeddings_do_lote` usa `model.vision_model` + `visual_projection` do CLIP (o mesmo objeto
  `model` carregado por `_carregar_modelo`) e normaliza com `F.normalize(p=2)` (norma L2 = 1). A
  referência (foto enviada) segue o mesmo `_preparar_pixels` → `_embeddings_do_lote`
  (`_extrair_emb_referencia`), não o `CLIPProcessor` da classificação.

### Varredura

- `_listar_imagens_inquerito` monta a fila: imagens do inquérito (`no_categoria = 'Imagem'`),
  `co_caminho_zip` presente, `tp_mime != 'image/webp'` e `qt_bytes >= 102400` (~100 KB) — os
  mesmos filtros do OCR e da classificação de imagem — e, quando a comparação foi pedida para um
  aparelho só, `AND e.id_extracao = %s`. A consulta também traz `e.no_dispositivo`, que é o nome do
  aparelho mostrado no cartão do resultado (sem isso a tela só dizia "Extração #42").
- Para cada imagem: se já tem embedding salvo (`_ler_embeddings_salvos`, por `id_arquivo` +
  `no_modelo`), lê direto do banco (converte float16 → float32); senão entra em `_calcular_pendentes`,
  que roda em lotes de `_BATCH_SIZE` = 32 com um **lote de adiantamento**: um
  `ThreadPoolExecutor` de `_WORKERS_IO` = 4 threads lê/decodifica o próximo lote (PIL e zlib soltam
  o GIL) enquanto a CPU faz o forward do CLIP no lote atual — acima de ~4 workers não compensa
  porque o gargalo passa a ser o próprio CLIP, e as threads a mais só disputam CPU com ele.
- Ao calcular, grava `(id_arquivo, no_modelo='clip-vit-base-patch32/v1', vt_embedding float16,
  dt_calculo)` com `ON CONFLICT (id_arquivo, no_modelo) DO NOTHING` — protege contra duas
  comparações que se cruzem recalculando a mesma imagem. A gravação usa uma única conexão para
  toda a varredura (não uma por lote); se gravar falhar, a comparação **não é interrompida** — o
  cache é otimização, a conexão problemática é descartada e a varredura segue sem cache para o
  resto do lote.
- **Round-trip determinístico**: o vetor calculado passa pelo float16 (`emb.astype("<f2")`) **antes
  de ser usado na própria execução**, não só ao gravar — assim a execução atual dá exatamente o
  mesmo número que as próximas, que vão ler o vetor já arredondado de volta do banco. O resultado
  fica estável entre varreduras diferentes.
- Cancelamento é checado a cada lote (não a cada imagem), na mesma escrita que grava o progresso
  na tabela de jobs; se cancelado no
  meio de `_calcular_pendentes`, a função devolve o que já tinha calculado até ali (sem descartar
  o trabalho parcial, diferente do SIMBA, que descarta a carga inteira ao cancelar).

### Similaridade

- Monta a matriz `N × 512` dos embeddings de todas as imagens resolvidas (calculadas + reaproveitadas,
  todos já normalizados), faz o produto de matrizes contra a referência (`matriz @
  emb_referencia`), `clamp(0, 1)` e usa `torch.topk` para pegar os `k = min(limite, N)` melhores —
  a similaridade percentual é `valor × 100`, arredondada em 2 casas.

## Modelo de dados

- `ARQ_TB02_EMBEDDING_IMAGEM` (`db/init/20_arquivos.sql`):
  - `ID_ARQUIVO` (FK `FK168` → `ARQ_TB01_ARQUIVOS` com `ON DELETE CASCADE` — apagar uma extração
    já apaga `ARQ_TB01_ARQUIVOS`, e os vetores vão junto);
  - `NO_MODELO VARCHAR(120)` (`clip-vit-base-patch32/v1`, constante `MODELO_EMBEDDING`) — parte da
    **chave primária composta** com `ID_ARQUIVO`, não coluna solta: identifica o espaço vetorial
    (modelo + versão do pré-processamento). Mudar qualquer um dos dois exige subir a versão no
    nome — os vetores antigos, sob o nome velho, simplesmente param de ser lidos (não há migração
    automática; um `DELETE` pelo `no_modelo` antigo limpa o que sobrou);
  - `VT_EMBEDDING BYTEA` — 512 dimensões em float16 little-endian (~1 KB por imagem; a perda de
    precisão do float16 muda a similaridade em menos de 0,02 ponto percentual e corta o
    armazenamento pela metade frente a float32);
  - `DT_CALCULO TIMESTAMP DEFAULT now()`.
  - `CREATE TABLE IF NOT EXISTS` — migração idempotente, reaplicável num banco já existente via
    `psql`.

## Configuração

- `CLIP_NUM_THREADS` — threads do torch para CLIP (padrão 4; mesma variável da classificação de
  imagens, lida por `_carregar_modelo` na 1ª chamada de qualquer uma das duas features).
- `JOBS_PESADOS_LIMITE` (padrão 2) — limite GLOBAL do servidor (contado na tabela de jobs, não um
  semáforo de processo), compartilhado entre OCR, classificação de imagens e
  comparação facial (CPU); `_executar_comparacao` roda inteira (carregar modelo + extrair
  referência + varredura) dentro de `jobs_pesados.vaga("Comparação facial")` — mais abrangente que
  o OCR/classificação, que só envolvem o laço de processamento pesado na vaga.
- Sem CLIP instalado no servidor, a 1ª chamada a `_carregar_modelo` lança exceção — a rota não faz
  checagem prévia como o OCR faz com `ocr_disponivel()`; o erro aparece no `_PROGRESS["erro"]` da
  comparação em andamento.

## Referências

- `api/funcoes/comparar_rosto_utils.py` — pixels, preparo, `_embeddings_do_lote`, varredura com
  prefetch, similaridade, `obter_origem_arquivo`.
- `api/funcoes/classificacao_imagem_utils.py` — `_carregar_modelo` (compartilhado com a
  classificação de imagens).
- `api/rotas/comparar_rosto.py` — upload/início (com o filtro opcional de aparelho) e, por arquivo,
  prévia e origem; progresso e cancelamento ficam nas rotas genéricas de `api/rotas/jobs.py`.
- `api/rotas/jobs.py` — `GET /api/investigacao/jobs/{id_job}` (polling), `GET
  /api/investigacao/inqueritos/{id}/jobs/comparacao_facial` (retomar o último) e `POST
  .../jobs/{id_job}/cancelar`.
- `db/init/20_arquivos.sql` — tabela `ARQ_TB02_EMBEDDING_IMAGEM`.
- `web/comparar_rosto.html` / `web/js/comparar_rosto.js` / `web/css/comparar_rosto.css` — tela com
  upload, escopo por aparelho, polling com cancelamento, filtros sobre o resultado e exportação.
- Relacionados: `classificacao_imagem.md` (mesmo modelo CLIP, mesmo `_carregar_modelo`, mesmo
  semáforo de jobs pesados), `ocr_imagem.md` (mesmos filtros de fila: `webp` fora, `qt_bytes >=
  102400`).
