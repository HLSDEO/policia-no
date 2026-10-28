# Configurações de IA (tela de Configurações)

## Visão geral

A tela **Configurações** (`web/configuracoes.html`, acesso exclusivo de admin) separa em **abas** as configurações de IA do sistema, cada uma isolada (provedor, modelo, URL e chave próprios — variáveis distintas no `.env`):

1. **Prompt IA** — provedor, modelo, URL da API e chave usados pelos chats de IA (`chat_ia.md`). Compatível com qualquer provedor com API HTTP — OpenAI, **OpenAI com certificado** (gateway interno com TLS autoassinado, ex.: LiteLLM), Anthropic (Claude), Google Gemini, Azure OpenAI, DeepSeek, Ollama, LM Studio ou "outro". Tem também o checkbox **Anonimizar dados**, que troca nome de pessoa, CPF, telefone e e-mail por códigos antes de enviar e desfaz a troca na resposta.
1b. **Prompts de IA** — não é credencial de `.env`: são as **instruções adicionais** do prompt de sistema em 4 níveis (geral do sistema, unidade superior, delegacia, usuário) que se acumulam em cascata no chat, gravadas em `SEGURANCA.PROMPTS_IA` e montadas por `funcoes/ia_prompt_niveis.py` — com prévia do bloco efetivo por usuário/delegacia. Ver [unidades_prompts.md](unidades_prompts.md).
2. **Busca semântica** (embeddings) — modo (`api` ou `local`), provedor próprio (`EMBED_PROVIDER`), URL/chave próprias (`EMBED_API_URL`/`EMBED_API_KEY`) e modelo de embeddings; no modo local, o download do modelo no servidor Ollama. É o que alimenta a busca híbrida do chat (`buscar_hibrida`) e a busca em documentos.
3. **OCR** — modo `local` (Tesseract, offline — padrão) ou `api` (Google Vision / Qwen com visão, com URL/chave/modelo próprios). É o que extrai texto de imagens na tela de Arquivos.
4. **Comparação de imagens** — modo `local` (CLIP, offline — padrão) ou `api` (Google Vision/Gemini ou Qwen com visão, com URL/chave/modelo próprios). É a tela de comparar uma foto de referência contra as imagens do inquérito.
5. **Comentário IA no relatório** — IA isolada da do prompt (`REPORT_AI_*`); vazia, cai na IA do prompt. Usada pelas duas opções de IA da geração do relatório: "Resumo das conversas por IA" (resume as mensagens marcadas) e "Reescrever comentários do analista por IA" (só corrige e completa a frase da anotação). Tem o seu próprio checkbox **Anonimizar dados**, independente do da aba Prompt IA.
6. **Análise de documentos** — IA isolada da do prompt (`DOC_AI_*`); vazia, cai na IA do prompt. Usada na leitura de documento genérico (PDF, planilha, texto): a análise automática do documento anexado ao inquérito (resumo + pessoas + achados). **Sem checkbox de anonimização** — ver a limitação no fim deste documento.
7. **Resumo noturno** — IA isolada da do prompt (`RESUMO_NOTURNO_*`); vazia, cai na IA do prompt. Usada pelo passe da madrugada que gera o resumo por inquérito (conversas suspeitas de cada celular + síntese do caso) — ver [resumo_noturno.md](resumo_noturno.md). Como o passe roda sem ninguém esperando a resposta, é a aba onde faz sentido um modelo mais lento e barato que o do chat. É também a única aba com um bloco **Agendamento** (ligar/desligar, horário e fuso do passe, mais a próxima execução e o resultado da última) — quem dispara é a própria API, então não há agendador para instalar no servidor. **Sem checkbox de anonimização** — o passe não monta anonimizador (`_chamar_ia` chama `enviar_mensagem` sem ele), então o conteúdo vai em claro para o provedor.

Tudo aqui é **global**: vale para todo mundo na instalação. A exceção **por pessoa** mora em outra
tela — Usuários → "IA do usuário" —, e ela vence tanto o `.env` quanto as configurações de seção
(`REPORT_AI_*`, `DOC_AI_*`, `RESUMO_NOTURNO_*`) das abas 5 a 7; ver
[ia_por_usuario.md](ia_por_usuario.md).

As credenciais são gravadas no **`.env`** do projeto (arquivo, não banco) e são lidas de novo **a cada uso** — uma mudança vale sem reiniciar o container. Uma chave nunca é devolvida ao front: o `GET` retorna a chave **mascarada** (4 primeiros + 4 últimos caracteres), exibida como placeholder do campo de senha. Campo de chave **vazio ao salvar = manter a atual**.

## Fluxo de uso

1. Menu **Administração → Configurações** (rota `configuracao_admin`, só `exigir_admin`; usuário comum recebe 403).
2. Escolher a **aba** desejada — a aba ativa é lembrada em `localStorage` (`analyser-config-aba`).
3. Em cada aba, escolher o **provedor** — o front troca os modelos sugeridos e as URLs padrão (presets em `web/js/config-tabs.js`; a primeira opção de modelo/URL já vem selecionada; qualquer valor fora dos presets cai em "Outro (digitar)").
4. Preencher **modelo**, **URL da API** e **chave** (campo com "mostrar/ocultar chave").
5. **OpenAI com certificado**: campos **esforço de raciocínio** (baixo/médio/máximo) e **certificado do servidor**, só visíveis com esse provedor selecionado. Existem **em cada aba** — Prompt IA, Comentário IA no relatório, Análise de documentos e Resumo noturno; a Busca semântica tem só o certificado (embeddings não raciocinam). Em branco, cada aba herda o da aba Prompt IA.
6. **Tamanho do contexto** (Prompt IA, todo provedor): quantos tokens o modelo aceita receber de uma vez. É a **régua dos cortes de tamanho do turno** — orçamento do conteúdo anexado, teto do resultado de cada ferramenta e poda da conversa acumulada (ver `chat_ia.md`, "Poda de árvore"). Em branco (0), a janela é deduzida do nome do modelo e cai em **32 mil** quando o nome não é reconhecido — que é o caso do gateway, onde o modelo se chama `text-model`/`auto` e a janela real é de 262.144: sem preencher, o sistema envia bem menos do que caberia. No **Ollama** o mesmo valor também vai como `num_ctx` em cada requisição (0 = deixar o servidor decidir). Ainda no Ollama, botão **baixar modelo**; o progresso ("Baixando… 42% (pulling manifest)") é acompanhado por polling de 2 s até "concluído" ou erro. O mesmo botão existe na aba Busca semântica, só no modo `local`.
7. **Anonimizar dados** (abas Prompt IA e Comentário IA no relatório): marcar liga a troca por códigos naquele caminho de IA; desmarcar desliga. O estado volta marcado ao reabrir a aba.
8. Salvar → `POST` da rota da seção valida e grava no `.env`, a chave do formulário é limpa e o `GET` re-aplica os values salvos.

## Como funciona

### Endpoints (api/rotas/configuracao_admin.py)

Cada aba tem um par GET/POST próprio:

| Aba | GET | POST | Variáveis no `.env` |
|---|---|---|---|
| Prompt IA | `/api/config/ai/prompt` | `/api/config/ai/prompt` | `AI_PROVIDER`, `AI_MODEL`, `AI_API_URL`, `AI_API_KEY`, `AI_CONTEXT_LENGTH`, `AI_TIMEOUT`, `AI_ANONIMIZAR`, `AI_CA_CERT`, `AI_REASONING_EFFORT` |
| Busca semântica | `/api/config/ai/embeddings` | `/api/config/ai/embeddings` | `EMBED_MODE`, `EMBED_PROVIDER`, `EMBED_API_URL`, `EMBED_API_KEY`, `EMBED_MODEL`, `EMBED_CA_CERT` |
| OCR | `/api/config/ocr` | `/api/config/ocr` | `OCR_MODE`, `OCR_PROVIDER`, `OCR_API_URL`, `OCR_API_KEY`, `OCR_MODEL`, `OCR_IDIOMA`, `OCR_PSM` |
| Comparação de imagens | `/api/config/image-comparison` | `/api/config/image-comparison` | `IMAGE_CMP_MODE`, `IMAGE_CMP_PROVIDER`, `IMAGE_CMP_API_URL`, `IMAGE_CMP_API_KEY`, `IMAGE_CMP_MODEL` |
| Comentário IA no relatório | `/api/config/ai/report` | `/api/config/ai/report` | `REPORT_AI_PROVIDER`, `REPORT_AI_MODEL`, `REPORT_AI_API_URL`, `REPORT_AI_API_KEY`, `REPORT_AI_ANONIMIZAR`, `REPORT_AI_CA_CERT`, `REPORT_AI_REASONING_EFFORT` |
| Análise de documentos | `/api/config/ai/document` | `/api/config/ai/document` | `DOC_AI_PROVIDER`, `DOC_AI_MODEL`, `DOC_AI_API_URL`, `DOC_AI_API_KEY`, `DOC_AI_CA_CERT`, `DOC_AI_REASONING_EFFORT` |
| Resumo noturno | `/api/config/ai/resumo-noturno` | `/api/config/ai/resumo-noturno` | `RESUMO_NOTURNO_PROVIDER`, `RESUMO_NOTURNO_MODEL`, `RESUMO_NOTURNO_API_URL`, `RESUMO_NOTURNO_API_KEY`, `RESUMO_NOTURNO_CA_CERT`, `RESUMO_NOTURNO_REASONING_EFFORT`, `RESUMO_NOTURNO_AGENDADOR`, `RESUMO_NOTURNO_HORA`, `RESUMO_NOTURNO_TZ` |
| — (download de modelo) | `GET /api/config/ollama/pull?model=...` | `POST /api/config/ollama/pull` | — |

Validações comuns aos POSTs:

- `provider` precisa estar em `ALLOWED_PROVIDERS = {"openai", "openai-cert", "anthropic", "google", "azure-openai", "deepseek", "ollama", "lmstudio", "outro", "google-vision", "qwen-vision"}` e é normalizado para minúsculas.
- `apiUrl`, se preenchida, precisa começar com `http://` ou `https://` (mesma regra no pull).
- `mode`/`embedMode` ∈ `{api, local}`; `contextLength` entre 0 e 2.000.000 (Ollama `num_ctx`).
- Campos vazios (exceto `contextLength`: `0` limpa de propósito) **mantêm** o valor atual no `.env` — em especial a chave: vazia = não sobrescrever. Grava via `write_env_text`; falha de escrita vira 500 "Falha ao gravar o arquivo .env.".
- `reasoningEffort` (abas Prompt IA, Comentário IA, Análise de documentos e Resumo noturno) ∈ `{low, medium, xhigh}`; vazio mantém o atual. Valor fora da lista vira 400 — o gateway trata desconhecido como `xhigh`, justamente o esforço que devolve resposta vazia.
- `caCert` (as cinco abas com provedor: Prompt IA, Busca semântica, Comentário IA, Análise de documentos e Resumo noturno) precisa ser um PEM com bloco `BEGIN/END CERTIFICATE` e base64 íntegro; inválido vira 400 "Certificado inválido: …". Vazio mantém o atual (como a chave); `removerCaCert: true` apaga.
- **`anonimizar` é a exceção à regra acima**: booleano é gravado sempre com o valor do formulário (`_gravar_flag`), senão desmarcar o checkbox nunca desligaria a opção.

### Gravação no `.env` (api/funcoes/env_utils.py)

- Arquivo definido por `ENV_FILE` (padrão `/app/.env`), lido e gravado **preservando comentários e a ordem** das linhas (`read_env_text`/`write_env_text`/`upsert_env_values`).
- `sanitize` remove `\r`, `\n` e `\0` de qualquer campo — bloqueia injeção de novas linhas de configuração pelo formulário.
- `mask_key` retorna `4 primeiros caracteres + "•" repetido (mín. 4, cobrindo o miolo) + 4 últimos`; chave com 8 caracteres ou menos vira só bolinhas (`"•" * len(chave)`, para não expor nada de uma chave curta). A chave nunca volta inteira no `GET`.

### Presets por provedor (web/js/config-tabs.js)

Provedores de chat (abas Prompt IA, Comentário IA, Análise de documentos e Resumo noturno):

| Provedor | URLs sugeridas | Modelos sugeridos | Modelos de embeddings sugeridos |
|---|---|---|---|
| openai | `https://api.openai.com/v1` | gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4.1-mini, o3, o3-mini | text-embedding-3-small/large, text-embedding-ada-002 |
| openai-cert | `https://10.95.7.40:8080/v1` (IP do SAN do certificado) | auto, text-model, image-model, video-model, docs-model | — |
| anthropic | `https://api.anthropic.com/v1` | claude-sonnet-5, claude-opus-4-8, claude-haiku-4-5, claude-fable-5 | — |
| google | `https://generativelanguage.googleapis.com/v1beta` | gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash | text-embedding-004, text-multilingual-embedding-002 |
| azure-openai | (digitar) | gpt-4o, gpt-4o-mini, gpt-35-turbo | text-embedding-3-small/large |
| deepseek | `https://api.deepseek.com` | deepseek-v4-flash, deepseek-v4-pro | — |
| ollama | `http://ollama:11434/v1` (container), `http://host.docker.internal:11434/v1` (host) | qwen3:30b-a3b (~20 GB VRAM), qwen3-next:80b-a3b (~50 GB VRAM), qwen3.8-flash-next (~105–120 GB VRAM, multi-GPU), dengcao/Qwen3-30B-A3B:Q3_K_M (~15–16 GB VRAM) | nomic-embed-text, mxbai-embed-large, bge-m3 |
| lmstudio | `http://host.docker.internal:1234/v1`, `http://localhost:1234/v1` | qwen/qwen3-14b, openai/gpt-oss-20b, mistralai/mistral-small-3.2, qwen/qwen3-8b, meta-llama-3.1-8b-instruct | text-embedding-nomic-embed-text-v1.5, text-embedding-bge-m3 |
| outro | (digitar) | (digitar) | (digitar) |

Provedores de visão (abas OCR e Comparação de imagens):

| Provedor | OCR (URL sugerida) | Comparação (URL sugerida) | Modelos sugeridos |
|---|---|---|---|
| google-vision | `https://vision.googleapis.com/v1` (TEXT_DETECTION) | `https://generativelanguage.googleapis.com/v1beta` (Gemini: `:generateContent` com as duas imagens) | gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash (comparação); OCR não recebe modelo |
| qwen-vision | `https://dashscope.aliyuncs.com/compatible-mode/v1` (OpenAI-compatível, imagem em base64) | mesma URL (OpenAI-compatível, as duas imagens) | qwen-vl-max, qwen-vl-plus, qwen2.5-vl-72b-instruct |
| outro | (digitar) | (digitar) | (digitar) |

### Busca semântica (api/funcoes/embed_utils.py)

- `_config_embed` decide URL/modelo/chave: modo `local` → `EMBED_LOCAL_URL` (padrão `http://ollama:11434`, serviço do docker-compose) em `/v1`, sem chave; modo `api` → `EMBED_API_URL`/`EMBED_API_KEY` próprios, caindo em `AI_API_URL`/`AI_API_KEY` se os próprios não existirem (compatibilidade com quem configurou antes da aba existir). O modelo de chat **não** serve como embedding — por isso `EMBED_MODEL` é separado. Sem `EMBED_MODEL` configurado, `_config_embed` levanta erro descritivo ("Busca semântica não configurada...").
- `gerar_embeddings` chama `POST {url}/embeddings` no formato OpenAI (lotes de 64 textos — `_TAMANHO_LOTE_EMBED`) e ordena por `index`.
- No modo local, o job de indexação chama antes `_garantir_modelo_local`: `POST /api/pull` da **API nativa** do Ollama (campo canônico `name`, `stream: false`, timeout 30 min — `_TIMEOUT_PULL = 1800`) — idempotente; um download demorado não trava a requisição, o status da indexação fica `processando`.
- **O que é indexado**: mensagens de WhatsApp/SMS/outros mensageiros com texto ou transcrição de áudio (`_SQL_MSGS_INDEXAR`, `tp_fonte` `wpp`/`sms`/`msg`) **e** o texto de documentos anexados ao inquérito sem erro de extração (`_SQL_DOCS_INDEXAR`, `tp_fonte = 'doc'`). O texto do documento fica inteiro no `TX_CONTEUDO` — muito além do que qualquer modelo de embeddings aceita de uma vez — então `_fatiar_documento` corta em trechos (`_TAMANHO_TRECHO_DOCUMENTO = 4000` caracteres, com sobreposição de `_SOBREPOSICAO_TRECHO_DOCUMENTO = 300` para uma menção não ficar cortada exatamente na fronteira do trecho); cada fatia vira uma linha própria (`NR_TRECHO` numerando a ordem), até o teto de `_MAX_TRECHOS_DOCUMENTO = 200` fatias por documento — o excedente fica de fora da busca semântica, mas continua no texto completo. Só entram mensagens/documentos ainda sem vetor (`NOT EXISTS` contra `EMB_TB01_EMBEDDINGS`) — reindexar não duplica nem reprocessa o que já foi feito.
- A **indexação** é um job de background por inquérito (`EMB_TB02_INDEXACOES`, lote 64, trava de 60 min para jobs abandonados via `_MINUTOS_INDEX_STALE`, cancelamento cooperativo via `FL_CANCELAR`/checkpoint a cada lote) — endpoints `POST/GET /api/investigacao/inqueritos/{id_inquerito}/indexacao-semantica` em `api/rotas/investigacao.py`, disparados manualmente pelo investigador. Índice único parcial `UX45_EMB_TB02_ATIVA` trava uma indexação ativa por inquérito (`IndexacaoOcupada` se já houver uma rodando).
- **Encadeamento automático**: ao final de toda extração UFDR, `_indexar_semantica_ao_final` (`api/funcoes/investigacao_utils.py`) chama `indexar_sincrono` (mesma lógica do job, mas bloqueante) se `embeddings_configurados()` for `True` — best-effort: sem `EMBED_MODEL` configurado, pula silenciosamente; se já há indexação rodando (`IndexacaoOcupada`) ou qualquer outra falha, engole o erro (fica só no log/status da indexação) para nunca derrubar a extração em si.
- Os vetores ficam em `EMB_TB01_EMBEDDINGS`; a similaridade (cosseno) é calculada em Python/numpy por inquérito, **sem pgvector** — ver "Cache da matriz de vetores" abaixo.

### OCR (api/funcoes/ocr_utils.py)

- `_config_ocr` decide o modo: `local` (Tesseract, `OCR_IDIOMA`/`OCR_PSM`) ou `api` (`OCR_PROVIDER`/`OCR_API_URL`/`OCR_API_KEY`/`OCR_MODEL`).
- `ocr_disponivel` reflete o modo: no local, o binário do Tesseract instalado; no api, URL e modelo configurados (para `google-vision` basta a URL — o modelo não é usado).
- `_executar_ocr` despacha: Tesseract (`image_to_string` com idioma/PSM) ou API (`_ocr_imagem_api`). No modo api, a imagem é pré-processada (orientação EXIF + redução para `_LADO_MAX`) e enviada em base64:
  - **google-vision**: `POST {url}/images:annotate?key={chave}` com `TEXT_DETECTION`, resposta em `responses[0].fullTextAnnotation.text`;
  - **qwen-vision/outro**: `POST {url}/chat/completions` (OpenAI-compatível) com a imagem em `image_url` (data URL) e pedido de extração de texto, resposta em `choices[0].message.content`.
- O texto passa por `_limpar_texto` nos dois modos (mesma normalização/ruído).

### Comparação de imagens (api/funcoes/comparar_rosto_utils.py)

- `_config_comparacao` decide o modo: `local` (CLIP, vetores em cache em `ARQ_TB02_EMBEDDING_IMAGEM`) ou `api`.
- No modo **api**, `_executar_comparacao_api` percorre as imagens do inquérito (teto `_LIMITE_API_COMPARACOES = 200` por execução — uma chamada por imagem; a local continua a recomendada para volume) e pergunta ao provedor a similaridade 0-100 entre a referência e a candidata (`_perguntar_similaridade_api`):
  - **google-vision**: `POST {url}/models/{modelo}:generateContent?key={chave}` (Gemini) com as duas imagens em `inline_data`; resposta em `candidates[0].content.parts[0].text`;
  - **qwen-vision/outro**: `POST {url}/chat/completions` (OpenAI-compatível) com as duas imagens em `image_url`; resposta em `choices[0].message.content`.
- A resposta esperada é um número inteiro 0-100 (regex); resultado inválido conta como erro da imagem, não derruba a execução. Resultados ordenados por similaridade, top `limite`.

### Comentário IA no relatório (api/funcoes/relatorios_utils.py)

- `resumir_mensagens_ia` monta a configuração a partir de `REPORT_AI_*` (se qualquer um dos três campos estiver preenchido) e passa como `config=` para `enviar_mensagem` (`ia_provedores.py`) — que sobrescreve o `.env` só com os campos preenchidos; o resto cai na configuração padrão (`AI_*`). Sem `REPORT_AI_*`, o resumo usa a IA do prompt (comportamento antigo).
- `enviar_mensagem` ganhou o parâmetro opcional `config: dict` com chaves `provider`/`model`/`apiUrl`/`apiKey`/`contextLength` — campos ausentes ou vazios caem na configuração padrão do `.env`. Nenhum outro chamador muda.

### Análise de documentos (api/funcoes/documentos_utils.py)

- `_config_ia_documentos` monta a configuração a partir de `DOC_AI_*` (se qualquer um dos três campos estiver preenchido) e passa como `config=` para `enviar_mensagem` — mesmo mecanismo da IA do relatório: campo vazio cai no `AI_*` correspondente. Sem `DOC_AI_*`, a análise usa a IA do prompt (comportamento antigo).
- Vale para o turno de IA sobre documento genérico: `_processar_analise_documento` (resumo + pessoas + achados do documento anexado ao inquérito, `INQ_TB04_DOCUMENTOS`).
- `_ia_configurada` — usada para decidir se a análise dispara sozinha ao anexar um documento — passou a olhar o valor **efetivo** de cada campo (`DOC_AI_<campo>` ou, vazio, `AI_<campo>`), do mesmo jeito que o `enviar_mensagem` resolve: configurar só a aba nova, sem nada na do prompt, também liga a análise automática.
- É o caminho de IA que mais consome contexto de uma vez: o texto do documento vai inteiro no prompt até `_LIMITE_ANALISE_CARACTERES` (100 mil caracteres), e o que passa disso é cortado. É a razão de a aba existir — dá para deixar o chat num modelo barato e a leitura de documento num modelo de janela grande, ou o contrário.

### Resumo noturno (api/funcoes/resumo_noturno.py)

- `_config_resumo_noturno` monta a configuração a partir de `RESUMO_NOTURNO_*` (se qualquer um de
  provedor/modelo/URL estiver preenchido) e passa como `config=` para `enviar_mensagem` — mesmo
  mecanismo das duas abas anteriores: campo vazio cai no `AI_*` correspondente. Sem
  `RESUMO_NOTURNO_*`, o passe usa a IA do prompt.
- `resumo_noturno_configurado` — usada para o passe decidir se tem IA utilizável antes de começar —
  olha o valor **efetivo** de cada campo (`RESUMO_NOTURNO_<campo>` ou, vazio, `AI_<campo>`), do
  mesmo jeito que `enviar_mensagem` resolve; a IA por usuário, quando existe, vence as duas.
- São várias chamadas por inquérito (uma por celular, uma por fonte financeira e uma de síntese),
  todas de madrugada e sem ninguém esperando — é a aba onde um modelo mais lento e barato cabe. Os
  tetos de caracteres por fonte (`RESUMO_NOTURNO_MAX_CHARS_CELULAR`, `..._MAX_CHARS_SINTESE`) não
  têm campo na tela; ver [resumo_noturno.md](resumo_noturno.md).
- **Bloco Agendamento**: `RESUMO_NOTURNO_AGENDADOR` (checkbox — aqui o valor gravado é `1`/`0`
  explícito, e não a regra "vazio mantém o atual" dos campos de texto: desmarcar É o pedido de
  desligar, e *ausente* significa ligado, que é o que faz uma instalação nova rodar sozinha),
  `RESUMO_NOTURNO_HORA` (`HH:MM`, validado no POST) e `RESUMO_NOTURNO_TZ` (nome IANA, conferido
  com `ZoneInfo` — nome errado vira 400 em vez de um passe rodando calado no horário de UTC). O
  `GET` ainda devolve dois campos só de leitura, `agendadorProxima` e `agendadorUltima`, montados
  a partir de `AGD_TB01_EXECUCOES` por `agendador.estado()`.

### Anonimizar dados (api/funcoes/anonimizacao.py)

Checkbox em **duas** abas, com efeito independente: em *Prompt IA* (`AI_ANONIMIZAR`) e em
*Comentário IA no relatório* (`REPORT_AI_ANONIMIZAR`). Ligado, o que sai para o provedor troca nome
de pessoa, CPF, telefone e e-mail por códigos — `FULANO A`, `CPF A`, `TELEFONE A`, `EMAIL A` — e a
resposta é descodificada antes de chegar ao investigador. **Uma pessoa é uma letra**: o telefone de
`FULANO A` é `TELEFONE A`, para o modelo não perder o vínculo entre a pessoa e o identificador dela.

O gancho é único, em `enviar_mensagem` (`ia_provedores.py`), porque o dado sai por três portas:

1. **prompt de sistema** — codificado sem a heurística (ali é instrução; trocar "Mesa de Inquérito"
   por um FULANO estragaria o método que o modelo tem de seguir). O dump do aparelho, que é a única
   parte do sistema que é dado, é codificado *com* heurística por `ia_utils` antes de entrar;
2. **histórico da conversa** — inclusive a compactação de `ia_historico`;
3. **resultado de ferramenta** — o maior volume. Os ARGUMENTOS que o modelo devolve são
   descodificados antes de a ferramenta rodar (senão `buscar_contato_extracao(nome="FULANO A")` não
   acharia nada) e antes da auditoria, que mostra o valor real.

O dicionário é montado **uma vez por turno**, por inquérito: entidades canônicas do tipo `pessoa`
(`ENT_TB05`/`ENT_TB08` — é ali que a fusão "mesmo telefone = mesma pessoa" já foi feita) mais os
nomes de contato, agenda, dono do aparelho, IPL, RIF, SIMBA, IRPF e documentos analisados. Cada
consulta roda no seu `SAVEPOINT`: uma base sem alguma migração perde aquela fonte, não o turno.
O que não está no dicionário ainda pode ser pego pela heurística de nome próprio (2 a 5 palavras
capitalizadas, filtradas pelo `_PALAVRAS_NAO_PESSOA` de `ipl_utils` mais uma lista de ruído de
conversa) e pelos regex de CPF/telefone/e-mail.

Onde vale: chat de IA do inquérito, busca automática de entidades e as duas opções de IA da
geração do relatório. **Não** vale na análise/busca em documentos anexados (aba *Análise de
documentos*) — lá o serviço é justamente extrair pessoas novas do texto.

Auditoria e `.docx` continuam com os nomes reais: o de-para vive só dentro da chamada.

### Provedor com certificado próprio (api/funcoes/cert_utils.py)

Gateway interno (o LiteLLM da SR/RR é o caso que motivou isto) publica a API OpenAI-compatível em
**HTTPS com certificado assinado por ele mesmo**. O Python valida a cadeia contra as autoridades
públicas do sistema e recusa a conexão *antes* de mandar a requisição — o erro chega como
"certificate verify failed", sem relação aparente com a configuração de IA. O provedor
**`openai-cert`** existe para esse caso.

- O certificado (`.crt` entregue pelo administrador do gateway) é escolhido ou colado no campo
  **Certificado do servidor**, que só aparece na aba *Prompt IA* com esse provedor selecionado. O
  front lê o arquivo com `FileReader` e manda o texto PEM no mesmo POST dos demais campos.
- `cert_utils.normalizar_pem` valida (bloco `BEGIN/END CERTIFICATE` + base64 íntegro — o erro comum é
  ter copiado o certificado do terminal e perdido um pedaço no meio) e `pem_para_env` grava em
  **base64** na variável de certificado da aba: o `.env` é uma linha por chave e um PEM tem várias. Um
  **caminho absoluto** para um `.crt` montado no container também é aceito no lugar do base64.
- `cert_utils.contexto_ssl` monta um `ssl.SSLContext` que confia **naquele** certificado
  (`create_default_context(cadata=…)`, com cache por valor) e é ele que vai no `context=` do
  `urlopen`. **Não existe opção de ignorar a verificação** (o `-k` do curl): validação desligada
  deixaria qualquer máquina no caminho se passar pelo gateway e ficar com o conteúdo do inquérito.
  Hostname e validade continuam sendo verificados — por isso a URL precisa ser a que o certificado
  cobre (no LiteLLM da SR/RR o SAN é o **IP**, não um nome DNS).
- **Cada aba tem o seu certificado**, como já tem provedor, modelo, URL e chave: `AI_CA_CERT`
  (Prompt IA), `EMBED_CA_CERT` (Busca semântica), `REPORT_AI_CA_CERT` (Comentário IA no relatório),
  `DOC_AI_CA_CERT` (Análise de documentos) e `RESUMO_NOTURNO_CA_CERT` (Resumo noturno) — dá para
  apontar cada seção para um gateway
  diferente. Vale a mesma herança dos outros campos: **vazio usa o da aba Prompt IA**, então quem
  tem um gateway só configura em um lugar e as demais abas seguem junto. Quem monta a configuração
  da seção (`_config_ia_relatorio`, `_config_ia_documentos`, `_config_resumo_noturno`,
  `_config_embed`) passa a chave
  `caCert` e `enviar_mensagem` resolve `cfg.get("caCert") or env.get("AI_CA_CERT")`;
  `contexto_ssl_do_provedor` recebe o valor já resolvido.
- O `GET` nunca devolve o PEM: devolve `caCertResumo` — nome, validade e impressão digital SHA-256
  (`litellm.local · válido até 17/11/2028 · SHA-256 5F:2C:…`), que dá para conferir contra o que o
  administrador informou. O botão **Remover certificado** apaga a variável.
- Provedor `openai-cert` **sem** certificado gravado não tenta conectar: `enviar_mensagem` levanta
  `ErroProvedorIA` mandando informar o `.crt` em Configurações.

**Raciocínio (`reasoning_effort`)**: os modelos do gateway vêm com raciocínio ativo em `xhigh`, e o
raciocínio consome o mesmo orçamento de `max_tokens` da resposta — o sintoma é HTTP 200 com
`content` vazio e `finish_reason: length`, que **não** melhora só aumentando `max_tokens` (o
raciocínio se expande para preencher). A alavanca é o esforço, que é campo da tela
(select **Esforço de raciocínio**, visível só com esse provedor) e vai no corpo junto com um teto
de saída proporcional:

| Tela | `reasoning_effort` | `max_tokens` |
|---|---|---|
| Baixo (recomendado) — padrão | `low` | 4096 |
| Médio | `medium` | 8192 |
| Máximo | `xhigh` | 16384 |

O teto sobe junto de propósito (`MAX_TOKENS_POR_ESFORCO` em `ia_provedores.py`): subir o esforço
sem subir o teto só troca resposta curta por resposta vazia. O esforço também é **por aba**
(`AI_REASONING_EFFORT`, `REPORT_AI_REASONING_EFFORT`, `DOC_AI_REASONING_EFFORT`,
`RESUMO_NOTURNO_REASONING_EFFORT`), com a mesma
herança do certificado: vazio usa o da aba Prompt IA. Vazio dos dois lados, ou valor estranho, cai
em `low` (`esforco_raciocinio_valido`) — o gateway, esse, trataria desconhecido como `xhigh`.

O raciocínio que o gateway devolver no campo `reasoning_content` é o que alimenta o bloco
recolhido "Raciocínio da IA" na bolha da resposta (ver `chat_ia.md`, "Auditoria de pensamento").
Se o modelo upstream não devolver o campo, o bloco continua aparecendo — só com as ferramentas
que a IA consultou. Esforço maior costuma render um raciocínio mais longo, não um mais confiável:
o painel é para auditar o caminho, não para escolher o esforço.

### Download de modelo no Ollama (api/funcoes/ollama_pull.py)

- Disparado por `iniciar_pull` (`POST /api/config/ollama/pull`); roda em thread de background e consome o **streaming NDJSON** da API nativa `/api/pull` (fora do `/v1`): cada linha traz o estágio e, nas camadas de download, `total`/`completed` → o percentual mostrado no front ("Baixando… X% (pulling <camada>)").
- `_base_url` deriva a base da URL do formulário (que aponta para `/v1`): remove o sufixo `/v1`; vazio cai em `http://ollama:11434`.
- Status fica na **tabela de jobs** (`INVESTIGACAO.JOB_TB01_JOBS`, `TP_JOB = 'ollama_pull'`, o modelo no escopo — `api/funcoes/jobs_utils.py`), não mais num dicionário de módulo: o guard de "já está baixando" é o índice único parcial `UX100_JOB_TB01_ATIVO`, e o progresso sobrevive a um restart da API (o Ollama retoma o download de qualquer forma). Timeout de inatividade do socket: 1 h (`_TIMEOUT_PULL = 3600`).
- Modelo nunca iniciado nesta sessão: `GET` devolve `{status: "nenhum", progresso: 0, detalhe: "", erro: ""}` (não `404`). 409 ("já está sendo baixado", ex.: página recarregada no meio) apenas segue acompanhando; `concluido` mostra "Modelo disponível no servidor ✓".
- O front tem **dois botões**: um para o modelo de chat (com a URL atual do provedor) e um para o modelo de embeddings — só no modo `local` e com URL vazia (cai no padrão do backend). **LM Studio não tem endpoint de download** — o botão não aparece para ele.

### Como cada provedor é chamado (resumo)

- `openai`, `deepseek`, `azure-openai`, `outro` e o endpoint `/v1` do Ollama: mesmo formato OpenAI-compatível (`_chamar_openai_compativel`).
- `openai-cert`: mesmo `_chamar_openai_compativel`, com duas diferenças — a conexão usa o contexto TLS do certificado configurado (ver abaixo) e o corpo leva `reasoning_effort` + `max_tokens` (esforço escolhido na tela; ver abaixo).
- `anthropic`: formato Messages do Anthropic, com `cache_control: ephemeral` nas ferramentas e no sistema (economia de tokens em turnos longos).
- `ollama`: **API nativa `/api/chat`** (não o `/v1`), que permite fixar `num_ctx` por requisição — é onde entra `AI_CONTEXT_LENGTH`.
- `google`: sem function calling (texto puro) — ver `chat_ia.md`.
- Timeouts por provedor: Ollama 1200 s; `openai-cert` 300 s; DeepSeek 90 s; demais 60 s (`TIMEOUTS_POR_PROVEDOR`); máximo de 25 iterações de ferramentas (`MAX_ITERACOES_FERRAMENTAS`). O campo "Timeout da requisição" da aba Prompt IA (`AI_TIMEOUT`) sobrescreve esse padrão para todas as IAs que não têm timeout próprio.
- Os chats e a busca automática **não dependem da tela**: `ia_provedores.py` relê o `.env` a cada turno (`parse_env(read_env_text())`), então uma edição à mão no arquivo vale no próximo turno, sem reiniciar — e sem precisar passar pela tela.

## Limitações conhecidas

- **`num_ctx` só existe no Ollama nativo** — o valor do campo *tamanho do contexto* só vira parâmetro de requisição nesse provedor; nos demais (inclusive LM Studio) ele não é enviado a lugar nenhum, mas **continua valendo como régua interna** dos cortes de tamanho do turno, e por isso o campo aparece para todos desde 2026-09-03 (`config-tabs.js`).
- **LM Studio não tem endpoint de download de modelo** — o botão de baixar só aparece com o provedor `ollama` (e para embeddings, só no modo `local`, que roda no Ollama).
- **Anthropic e DeepSeek não sugerem modelos de embeddings** (`embModels: []`) — no modo `api` com esses provedores, o modelo de embeddings precisa ser digitado.
- **Azure OpenAI e "outro" não têm URL preselecionada** — a URL é sempre digitada.
- **A Busca semântica não tem campo de esforço de raciocínio** — e não é esquecimento: o endpoint
  `/v1/embeddings` não gera texto, não há raciocínio a dosar. Certificado ela tem.
- **O esforço de raciocínio vale só para `openai-cert`** — nos demais provedores o campo não é
  enviado, mesmo em modelo que aceitaria (`o1`/`o3` da OpenAI, por exemplo).
- **`AI_CONTEXT_LENGTH` continua sendo um só**, configurado na aba Prompt IA — esse não virou por aba.
  As demais abas (relatório, análise de documentos) usam a janela deduzida do nome do modelo delas.
- **OCR e Comparação de imagens não aceitam `openai-cert`** — as duas abas têm lista própria de
  provedores (visão) e continuam com a validação TLS padrão do sistema.
- **OCR por API tem custo por imagem** — um inquérito com milhares de imagens custa uma chamada por imagem; o modo local (Tesseract) é o indicado para volume.
- **Comparação por API tem teto de 200 imagens por execução** (`_LIMITE_API_COMPARACOES`) — a comparação local (CLIP) não tem teto e é a recomendada para volume.
- O download do Ollama **não persiste entre restarts do servidor** (estado em memória); o modelo em si sobrevive no disco do Ollama, e o pull retoma de onde parou.
- **A anonimização é melhor esforço, não garantia** — nome que não está no dicionário do inquérito e
  não é pego pela heurística sai em claro; a heurística, por outro lado, erra para o outro lado
  (rua, empresa ou órgão viram `FULANO X`). O erro é reversível — o código volta ao original na
  resposta —, mas o modelo enxerga um rótulo onde havia um nome próprio real, e a qualidade da
  análise cai um pouco quando ele raciocina sobre rótulos.
- **A busca semântica (`EMBED_*`) não é anonimizada** — o checkbox existe só nas abas Prompt IA e
  Comentário IA no relatório; o texto indexado continua indo em claro para o provedor de
  embeddings. Mesma coisa para OCR e comparação de imagens, que enviam imagem, não texto.
- **A aba Análise de documentos também não tem anonimização, e não é esquecimento** — o serviço ali
  é justamente extrair do texto pessoas que o sistema ainda não conhece; trocar nome por código
  antes de enviar apagaria exatamente o que a análise precisa achar. O texto do documento vai em
  claro para o provedor.
- **A aba Análise de documentos não tem campo de `num_ctx`** — mesmo com o provedor Ollama, o
  tamanho de contexto continua vindo de `AI_CONTEXT_LENGTH` (aba Prompt IA). Idem na aba Resumo
  noturno.
- **A aba Resumo noturno também não tem anonimização** — o passe da madrugada chama
  `enviar_mensagem` sem anonimizador (`_chamar_ia` em `api/funcoes/resumo_noturno.py`), então o
  conteúdo do aparelho vai em claro para o provedor, marque-se o que se marcar nas outras abas. Os
  tetos de caracteres por fonte (`RESUMO_NOTURNO_MAX_CHARS_CELULAR`/`..._MAX_CHARS_SINTESE`)
  também continuam só no `.env`.
- `EMBED_MODEL` vazio desabilita a busca por sentido — o chat continua funcionando, mas `buscar_hibrida` falha com erro descritivo até configurar.

## Modelo de dados

As credenciais vivem **só no `.env`** — nunca no banco (um dump do banco não vaza chave; o `.env` está no `.gitignore`). O pull do Ollama tem estado apenas em memória.

Tabelas **consumidoras** da configuração (em `INVESTIGACAO`):

| Tabela | Colunas principais | Uso da configuração |
|---|---|---|
| `EMB_TB01_EMBEDDINGS` | `ID_EMBEDDING`, `ID_INQUERITO` (FK), `TP_FONTE VARCHAR(3)` (`wpp`/`sms`/`msg`/`doc`), `ID_MENSAGEM`, `NR_TRECHO`, `TX_TRECHO`, `VL_VETOR REAL[]`; índice único `UX43` (inquérito, fonte, mensagem, trecho) | vetores gerados com `EMBED_MODEL` (migração `db/init/22_embeddings.sql`; `NR_TRECHO`/`TP_FONTE = 'doc'`/`UX43` ampliado na migração) |
| `EMB_TB02_INDEXACOES` | `ID_INDEXACAO`, `ID_INQUERITO`, `CD_STATUS` (`pendente\|processando\|concluido\|erro`), `QT_TOTAL`/`QT_FEITOS`, `TX_ERRO`, `DT_INICIO`/`DT_FIM`; índice único parcial `UX45` (uma indexação ativa por inquérito) | job de indexação disparado na tela do inquérito |
| `IA_TB04_AUDITORIA` | `NO_PROVEDOR`, `NO_MODELO`, `TX_PROMPT`, `TX_CONTEXTO`, `QT_CHARS_CONTEXTO`, `TX_FERRAMENTAS` (JSONB), `QT_TOKENS_*`, `VL_CUSTO_USD`, `NU_TEMPO_MS` | registra qual provedor/modelo respondeu cada turno (migração `38`) |
| `IA_TB02_MENSAGENS`, `CAS_TB07_MENSAGENS_IA` | `NO_MODELO` | modelo que respondeu cada mensagem do chat |

## Configuração

Exemplo de bloco no `.env` após salvar pela tela (valores ilustrativos — a chave real fica só no arquivo):

```
AI_PROVIDER=deepseek
AI_MODEL=deepseek-v4-pro
AI_API_URL=https://api.deepseek.com
AI_API_KEY=sk-...
EMBED_MODE=local
EMBED_MODEL=nomic-embed-text
AI_CONTEXT_LENGTH=
# AI_TIMEOUT=120
EMBED_LOCAL_URL=http://ollama:11434

# Gateway OpenAI-compatível com certificado próprio (provedor openai-cert). Cada aba tem o seu
# par certificado + esforço; vazio herda o da aba Prompt IA (AI_*).
# O certificado é o .crt inteiro em base64, gravado pela tela — ou o caminho de um .crt montado
# no container. O esforço é low (padrão), medium ou xhigh.
# AI_CA_CERT=LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t...
# AI_REASONING_EFFORT=low
# EMBED_CA_CERT=
# REPORT_AI_CA_CERT=
# REPORT_AI_REASONING_EFFORT=
# DOC_AI_CA_CERT=
# DOC_AI_REASONING_EFFORT=
# RESUMO_NOTURNO_CA_CERT=
# RESUMO_NOTURNO_REASONING_EFFORT=

# OCR — modo local (Tesseract) ou api
OCR_MODE=local
OCR_IDIOMA=por+eng
OCR_PSM=3
# OCR_PROVIDER=google-vision
# OCR_API_URL=https://vision.googleapis.com/v1
# OCR_API_KEY=
# OCR_MODEL=qwen-vl-max

# Comparação de imagens — modo local (CLIP) ou api
IMAGE_CMP_MODE=local
# IMAGE_CMP_PROVIDER=google-vision
# IMAGE_CMP_API_URL=https://generativelanguage.googleapis.com/v1beta
# IMAGE_CMP_API_KEY=
# IMAGE_CMP_MODEL=gemini-2.0-flash

# Comentário IA no relatório (vazio = usa a IA do prompt)
# REPORT_AI_PROVIDER=
# REPORT_AI_MODEL=
# REPORT_AI_API_URL=
# REPORT_AI_API_KEY=

# Análise de documentos genéricos (vazio = usa a IA do prompt)
# DOC_AI_PROVIDER=
# DOC_AI_MODEL=
# DOC_AI_API_URL=
# DOC_AI_API_KEY=

# Resumo noturno por inquérito (vazio = usa a IA do prompt)
# RESUMO_NOTURNO_PROVIDER=
# RESUMO_NOTURNO_MODEL=
# RESUMO_NOTURNO_API_URL=
# RESUMO_NOTURNO_API_KEY=
# Agendamento do passe (ausente = ligado, 02:00, America/Sao_Paulo)
# RESUMO_NOTURNO_AGENDADOR=1
# RESUMO_NOTURNO_HORA=02:00
# RESUMO_NOTURNO_TZ=America/Sao_Paulo

# Anonimizar dados antes de enviar ao provedor (1 = ligado, vazio = desligado)
AI_ANONIMIZAR=
REPORT_AI_ANONIMIZAR=

# Cotação do dólar para exibir em R$ o custo estimado de cada turno de IA (sem campo na tela)
# AI_COTACAO_BRL=5,45
```

Observações:

- O caminho do arquivo é definido pela variável de ambiente `ENV_FILE` (padrão `/app/.env` dentro do container — ver `api/funcoes/db_utils.py`, mesmo padrão usado pelas demais configurações por arquivo).
- `AI_CONTEXT_LENGTH` vazio (a tela grava vazio quando o campo é 0) deixa o Ollama decidir o contexto e faz o resto do sistema deduzir a janela pelo nome do modelo (32 mil quando não reconhece — ver o item 6 do passo a passo).
- As variáveis `*_CA_CERT` só são usadas pelos provedores de `PROVEDORES_COM_CERTIFICADO` (hoje `openai-cert`); com qualquer outro provedor são ignoradas e valem as autoridades públicas do sistema.
- `EMBED_LOCAL_URL` é opcional (padrão `http://ollama:11434` — serviço do docker-compose; não há campo na tela).
- `AI_COTACAO_BRL` também não tem campo na tela: é a cotação do dólar usada só para **exibir** o custo de cada turno em reais ao lado do US$, na bolha da resposta e no resumo da conversa (a auditoria continua gravando US$ — ver [chat_ia.md](chat_ia.md)). Vazio ou ausente = só US$. Aceita vírgula ou ponto como separador decimal.
- Pode-se editar `.env` à mão — o sistema relê o arquivo a cada chamada.
- A chave não é exibida nem devolvida pelo `GET`; para **trocar**, preencher o campo; para **manter**, deixar vazio.
- A tela é acessível apenas por admins; usuários comuns recebem 403 (mesma regra das outras telas de administração).

## Referências

- `api/funcoes/anonimizacao.py` (tabela de-para dos códigos, dicionário do inquérito, `anonimizacao_ativa`).
- `api/funcoes/agendador.py` (thread do passe noturno, ledger `AGD_TB01_EXECUCOES` e o `estado()`
  que a aba Resumo noturno exibe).
- `api/rotas/configuracao_admin.py` (GET/POST por seção, pull `/api/config/ollama/pull`), `api/funcoes/env_utils.py`, `api/funcoes/cert_utils.py` (certificado do gateway), `api/funcoes/ollama_pull.py`, `api/funcoes/embed_utils.py`, `api/funcoes/ia_provedores.py`, `api/funcoes/ocr_utils.py`, `api/funcoes/comparar_rosto_utils.py`, `api/funcoes/relatorios_utils.py`,
  `api/funcoes/documentos_utils.py` (`_config_ia_documentos`, `_ia_configurada`).
- `web/configuracoes.html` e `web/js/config-tabs.js` (abas, presets, polling do pull, campo de chave).
- `db/init/22_embeddings.sql`, `db/init/24_ia.sql` (indexação de documentos anexados).
- `README.md` na raiz (visão geral), `docs/chat_ia.md`, `docs/busca_semantica.md`, `docs/auditoria.md`, `docs/banco_dados.md`, `docs/ia_por_usuario.md` (a exceção por usuário, que vence tudo o que é configurado aqui).
- `docs/README.md` — padrão de estrutura usado por estes documentos.