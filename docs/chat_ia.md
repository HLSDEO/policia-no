# Chat de IA e function calling

## Visão geral

O sistema tem um chat com a IA, com *function calling* (a IA nunca consulta o banco diretamente — ela só pede dados por ferramentas definidas no servidor, e o servidor decide o que ela pode ver):

- **Chat por inquérito** (`web/ia.html`): conversa sobre os dados extraídos dos aparelhos (mensagens, ligações, localizações, notas...), documentos anexados, IPLs, RIFs do COAF e extratos do SIMBA de um inquérito — incluindo os inquéritos marcados como **caso de inteligência**, que usam os mesmos dados e as mesmas telas.

A resposta é gerada **em background** (thread) porque um turno pode envolver dezenas de chamadas de ferramenta e ultrapassar o timeout do proxy. O chat acompanha o turno por **SSE** (`web/ia.html`). Cada envio vira um registro de processamento acompanhado pelo front; o investigador pode **cancelar** o turno a qualquer momento.

O provedor (OpenAI, OpenAI com certificado, Anthropic, Gemini, Azure OpenAI, DeepSeek, Ollama, LM Studio ou outro) é configurado na tela **Configurações** — ver `configuracoes_ia.md`.

## Fluxo de uso

### Chat por inquérito

1. Na tela de Investigação, abrir **IA** em um inquérito (`web/ia.html`), que carrega via `?inquerito=`.
2. Criar ou selecionar uma conversa na barra lateral ("Conversas de IA do inquérito"); uma conversa sem título ganha o início da primeira mensagem como título.
3. (Opcional) Restringir o alcance da conversa:
   - **Prompt de IA específico** — botão disponível ao lado de um RIF, extrato do SIMBA, IPL, aparelho (extração), documento, mensageiro ou conversa; abre o chat já restrito àquele item (ver "Escopo fechado no servidor" abaixo; para "documento", ver a inconsistência de `CK05` registrada em "Modelo de dados").
   - **Painel flutuante** em `web/whatsapp.html` / `web/mensageiros.html` (`web/js/utils/ia_flutuante.js`) — nova conversa restrita ao mensageiro ou àquela conversa específica. O mesmo painel aparece nas telas de grafo (`grafo_rif.html`, `grafo_simba.html`, `mesa_inquerito.html`), onde além de responder ele **propõe filtros do grafo** — ver "Filtrar o grafo pelo chat" abaixo.
   - **Painel "Fontes"** no chat geral — coluna à direita, no estilo do painel de fontes do NotebookLM: o inventário do inquérito em grupos, com uma **caixa de seleção por item** — Aparelhos (extrações), RIFs, Extratos SIMBA, IPLs (inquéritos em PDF) e Documentos anexados (itens carregados do backend). Começa com **tudo marcado = todo o inquérito**; desmarcar é que restringe. O botão "Fontes: N de M" no compositor abre/fecha o painel e resume o estado; abaixo de 1000px o painel vira gaveta sobre a conversa. Os dois extremos dão no mesmo — tudo marcado e nada marcado mandam lista vazia, e o backend considera o inquérito inteiro. Passando de 8 itens aparece um filtro por texto, e aí as caixas "Selecionar todas"/de grupo valem para o que está à vista (`web/js/ia.js`, `web/css/chat.css`).
   - **Hipóteses do inquérito** (`web/hipoteses.html`) — botão ao lado de "Prompt de IA específico" em cada RIF/extrato do SIMBA/IPL/extração/documento (e no menu "Navegar" do inquérito); abre o workspace do inquérito inteiro onde o investigador gerencia hipóteses com responsável, evidências a favor/contra, pendências e status (ver `hipoteses.md`).
4. (Opcional) marcar **"Não enviar+exibir prompt"**: o turno monta o prompt completo e entrega como arquivo para copiar em outra IA, em vez de responder.
5. Digitar a pergunta e enviar; acompanhar o progresso ("Buscando mensagens…") e, se quiser, **Cancelar resposta** (o acompanhamento vê o status `cancelado` e mostra "Resposta cancelada." — `web/js/ia.js`).
 6. Citar evidência: a IA responde com marcadores clicáveis `[msg:wpp:ID]` / `[msg:sms:ID]` que abrem a mensagem original, e `[doc:ID]` que abre o documento anexado. Cada citação traz ainda uma pastilha **ℹ️** ao lado do link (🔎/📄): abre um modal com a cadeia de custódia da prova — apelido da mídia, nome do arquivo, dispositivo, data/hora + timestamp da mensagem e hashes MD5/SHA-1/SHA-256 (`GET /api/investigacao/evidencias/{fonte}/{id}`, `web/js/utils/evidencia_info.js`).
7. Pedir um relatório ("gere um relatório...") devolve, além da resposta na conversa, um link para **baixar o mesmo relatório em Word (.docx)** — ver "Relatório em Word (.docx) quando o investigador pede relatório" abaixo.

### Regras de acesso

- Os endpoints de IA exigem `exigir_aprovado` + o flag `SEGURANCA.DELEGACIAS.IN_IA_ATIVA`, conferido pelos **dois lados** em `api/funcoes/ia_delegacia.py` (`bloqueio_ia`): a delegacia do usuário logado **e** a do inquérito aberto. Qualquer uma delas com `IN_IA_ATIVA = false` retorna **403 "IA desativada para \<DELEGACIA\>."**. Vale para o chat por inquérito, a análise de documento anexado e a busca automática de entidades — ver `permissoes_delegacia.md`.
- Listar/criar conversas do chat por inquérito pede ainda acesso ao inquérito (`exigir_acesso_ia_inquerito`, que chama `exigir_acesso_inquerito` e depois o flag); o apagamento, envio e cancelamento validam a propriedade via conversa (`_exigir_acesso_via_conversa_ia`, em `ia.py`).
- A auditoria da IA é exclusiva de admin (`api/rotas/auditoria_ia.py`) — ver `auditoria.md`.

## Como funciona

### Endpoints (chat por inquérito)

Todas em `api/rotas/ia.py`, com os esquemas `NovaConversaIn`/`NovaMensagemIn`:

| Método | Rota | Função (`api/funcoes/ia_utils.py`) |
|---|---|---|
| GET/POST | `/api/investigacao/inqueritos/{id_inquerito}/ia/conversas` | `listar_conversas_ia` / `criar_conversa` |
| DELETE | `/api/investigacao/ia/conversas/{id_conversa_ia}` | `apagar_conversa_ia` |
| GET/POST | `/api/investigacao/ia/conversas/{id_conversa_ia}/mensagens` | `listar_mensagens_ia` / `enviar_mensagem_usuario` |
| GET | `/api/investigacao/ia/processamentos/{id_processamento}` | `obter_processamento` (polling) |
| GET | `/api/investigacao/ia/processamentos/{id_processamento}/eventos` | SSE do turno (`ia_eventos.fluxo_processamento`) — o caminho normal do front |
| GET | `/api/investigacao/ia/parametros` | janela de contexto do usuário e cotação do dólar, para a bolha mostrar o gasto |
| POST | `/api/investigacao/ia/processamentos/{id_processamento}/cancelar` | `solicitar_cancelamento` |
| GET | `/api/investigacao/ia/conversas/{id_conversa_ia}/auditoria` | auditoria da conversa |
| GET | `/api/investigacao/ia/prompts/{id_arquivo_prompt}/download` | `obter_arquivo_prompt` |
| GET | `/api/investigacao/ia/relatorios/{id_relatorio_ia}/download` | `obter_relatorio_ia` (`ia_relatorio_docx.py`) |

### Ordenação e limpeza de conversas vazias (chat por inquérito)

`listar_conversas_ia` (`api/funcoes/ia_utils.py`):

- **Ordena pela última atividade**, não pela criação: `ORDER BY COALESCE(max(m.dt_mensagem), c.dt_criacao) DESC` — a conversa em que se acabou de escrever sobe para o topo. A listagem devolve `dt_ultima_mensagem` e `id_usuario_criador` além dos campos de antes; `web/js/ia.js` usa o primeiro na meta do item ("N mensagens · \<última atividade\>") e o segundo no filtro **"Todos os usuários"** da barra lateral (abaixo de "Nova conversa"; só aparece com mais de um criador, recorte feito no cliente).
- **Apaga sozinha as conversas vazias e antigas**: antes de listar, `_apagar_conversas_vazias_antigas` remove as conversas deste inquérito **sem nenhuma mensagem** criadas há mais de `_MINUTOS_CONVERSA_VAZIA` (5 min). É limpeza preguiçosa — não há agendador no projeto e toda tela de IA (e o painel flutuante, e as sondas de `mesa_inquerito.js`) lista as conversas ao abrir. Motivo: `web/js/utils/ia_flutuante.js` cria uma conversa por thread de mensageiro assim que o painel abre, então quem só navega entre contatos no WhatsApp acumulava dezenas de conversas vazias.
- **A conversa aberta agora é poupada**: o front passa `?conversa_ativa=<id>` no GET (`web/js/ia.js`, `web/js/utils/ia_flutuante.js`), e essa conversa nunca é apagada pela limpeza mesmo vazia e velha — o investigador pode estar redigindo a primeira mensagem sem ter enviado. Se ainda assim uma conversa aberta some (outra aba/usuário, ou a aba do painel que ficou para trás), o GET de mensagens responde 404 e os dois fronts se recuperam sozinhos (o painel recria no mesmo escopo; a tela `ia.html` volta ao estado inicial).

### Escopo fechado no servidor

O ponto central de segurança é `ferramentas_para_inquerito` (`api/funcoes/ia_investigacao.py`):

- O campo `id_inquerito` **some do schema exposto à IA** e é injetado em cada chamada (`_vincular_inquerito`) — a IA nunca escolhe o escopo.
- Ferramentas "com escopo" são todas as que tocam dados do inquérito (`_FERRAMENTAS_COM_ESCOPO`).
- Conversa restrita (escopo único): a lista vira só as ferramentas do tipo (`_FERRAMENTAS_POR_ESCOPO_DOCUMENTO`) e os IDs do escopo são fixados (`_vincular_kwargs_fixos`) — ex.: escopo `rif` trava `id_rif`; escopo `extracao` trava `id_extracao`; escopo de conversa de mensageiro trava `fonte` + `id_conversa`. O front passa `escopo_tipo`/`escopo_id`/`escopo_titulo` na URL (`web/js/ia.js`); o banner mostra o rótulo (`RIF`, `extrato do SIMBA`, `inquérito em PDF (IPL)`, `aparelho (extração)`, `documento anexado`).
- Multisseleção (chat geral): a lista é a união das ferramentas dos itens selecionados; campo com um único valor em todos os itens é fixado no schema; campo com valores diferentes fica visível, mas validado na chamada (`_vincular_kwargs_validados`) — valor fora do conjunto selecionado vira erro descritivo devolvido ao modelo como resultado de ferramenta. Item de escopo inválido na mensagem vira erro 400 antes de qualquer chamada (`ia_utils.py`).
- As ferramentas de escrita (`listar_entidades_existentes`, `criar_entidade`, `criar_relacao`) **não existem no chat**: são exclusivas da busca automática (`ferramentas_para_busca_automatica`; ver `mesa_inquerito.md`). O chat é propositalmente de leitura — com **duas** exceções de ação (`_FERRAMENTAS_ACAO`), que só existem quando a conversa tem usuário dono (`id_usuario` injetado por binding, nunca a critério da IA):
  - `processar_texto_em_imagens` — OCR de um recorte limitado de imagens; cria job visível/cancelável na tela Arquivos (ver `ocr_imagem.md`, "OCR parcial a pedido da IA").
  - `gerar_planilha_consolidada` — consolidação de muitos documentos num `.xlsx` gerado em background (ver "Consolidação em planilha" abaixo). Só existe em conversas cujo recorte tem documentos ou IPLs (`_ESCOPOS_COM_CONSOLIDACAO`); o recorte do painel Fontes é injetado no binding (`escopos`), a IA não escolhe nem amplia.

### Hipóteses do inquérito (sucessor dos "achados do analista")

Os antigos achados por documento (ACH_TB01, `web/achados.html`) foram substituídos pelo
**workspace de hipóteses do inquérito inteiro** — quadro kanban com responsável, evidências a
favor/contra e pendências. Botão **Hipóteses do inquérito** ao lado de "Prompt de IA específico"
em cada bloco da tela de Investigação, nas topbars de IPL/IRPF/IPEI/telefonia e no menu
"Navegar" do inquérito. Documento completo: `hipoteses.md`.

Para o chat de IA importa aqui:

- **Injeção no prompt**: `bloco_hipoteses_inquerito(id_inquerito, tp_escopo, id_escopo)`
  (`hipoteses_utils.py`) é concatenado ao **contexto da investigação** de toda conversa —
  `ia_utils.py`, logo após `_bloco_contexto_caso`. A conversa do inquérito inteiro vê o quadro
  completo; a conversa **restrita a uma mídia** vê só as hipóteses daquela mídia (associadas a
  ela pelo vínculo próprio da hipótese ou por evidência que aponte a ela — a "hipótese da
  mídia" no chat específico dela; ver `hipoteses.md`). Monta um item por hipótese ativa
  (arquivadas ficam de fora) com status, responsável, natureza, evidências dos dois lados e
  pendências em aberto; só instrui a IA a **tratar como verdade já estabelecida** as hipóteses
  com `FL_CONTESTAVEL_IA = falso`. Import feito **dentro** da função para evitar import
  circular: `hipoteses_utils.py` importa `_TABELA_ESCOPO`/`_validar_escopo_documento` de
  `ia_utils.py` no nível do módulo.

### Loop do agente

`api/funcoes/ia_provedores.py` — `enviar_mensagem`, com `AuditoriaIA`:

- **Chat**: chamada com *tools* → modelo pede ferramentas → servidor executa (podendo rodar várias em **paralelo** com `ThreadPoolExecutor`) → devolve os resultados → nova chamada. No máximo **25 iterações** (`MAX_ITERACOES_FERRAMENTAS = 25`); o erro de cada ferramenta volta como resultado do tool, permitindo o modelo se corrigir.
- **Validação de argumentos** (`_validar_argumentos`): antes de executar, os argumentos da chamada são conferidos contra o `input_schema` exposto — formato errado com conserto brando é corrigido (`"10"`→`10`, `"sim"`→`true`, enum case-insensitive), e campo faltando/inventado/valor fora do enum volta como erro descritivo **sem executar** a ferramenta. `arguments` que nem parseiam como JSON viravam `{}` em silêncio; hoje viram erro instrutivo (`_parsear_argumentos`).
- **Fim de turno digno**: esgotadas as 25 iterações, em vez do erro seco o loop faz **uma última chamada sem `tools`** (`_ultima_chamada_sem_tools`) com a instrução de responder com o que já coletou; se essa chamada também falhar, o erro de sempre volta.
- Formatos por provedor: OpenAI-compatível (`_chamar_openai_compativel`: OpenAI, `openai-cert` — gateway com certificado próprio, ver `configuracoes_ia.md` —, DeepSeek, Azure OpenAI, "outro"; também o endpoint `/v1` do Ollama); **Anthropic** com `cache_control: ephemeral` na última ferramenta e nos blocos do prompt de sistema; **Ollama** pela API **nativa** `/api/chat` (não o `/v1`), que permite fixar `num_ctx` por requisição (`AI_CONTEXT_LENGTH`).
- **Google Gemini é texto puro**: sem function calling — se a pergunta exigir dados, responde com instrução, e o sistema tenta o modo "conteúdo completo" (abaixo) para ainda ser útil.
- Timeouts por provedor: Ollama 1200 s (CPU/downloads), DeepSeek 90 s, demais 60 s.

### Envio, trava e acompanhamento do turno

`enviar_mensagem_usuario` (`ia_utils.py`):

- Grava a mensagem do usuário e o processamento **na mesma transação** e dispara `_processar_mensagem` numa `thread` daemon; devolve `{"modo": "async", "id_processamento", "qt_lotes"}` na hora.
- **Trava por conversa**: índice único parcial `UX41_IA_TB03_CONVERSA_ATIVA` (um processamento ativo por conversa). Envio concorrente viola o índice → `ConversaOcupada` → rollback (a pergunta nem fica gravada, evita mensagem órfã). Processamentos presos há mais de 30 min (`_MINUTOS_TRAVA_STALE`) são liberados a cada envio.
- Título da conversa: gerado da primeira mensagem (até `TAMANHO_TITULO = 60`).
- Status: `pendente → processando → concluido | erro` e `cancelado` quando o usuário aborta. O front acompanha por **SSE** (`GET .../eventos`, `web/js/ia.js`), mostrando a etapa atual (`TX_ETAPA_ATUAL` — os rótulos vivos de `NOME_PARA_DESCRICAO`, `ia_utils.py`: "Buscando mensagens…", "Gerando resumo do inquérito…" etc.); em `erro` exibe `TX_ERRO` na conversa; em `concluido` recarrega a conversa do banco para trazer a resposta gravada.
- **SSE em vez de polling** (`api/funcoes/ia_eventos.py`): o front abre `GET .../processamentos/{id}/eventos` e recebe um evento `estado` — o mesmo dicionário da rota de polling — **sempre que algo muda** (status, `TX_ETAPA_ATUAL`, lotes concluídos), mais um comentário de *keepalive* a cada 15 s. Antes eram ~240 requisições autenticadas num turno de dez minutos, e a etapa atual chegava com até 2,5 s de atraso.
  - Quem observa o banco continua sendo o servidor (leitura a cada 0,7 s nos dois primeiros minutos, 2 s depois, em `run_in_threadpool` para não travar o laço de eventos). **Não** é `LISTEN/NOTIFY`: isso prenderia uma conexão do pool por analista com o chat aberto, e o pool é o recurso escasso.
  - `X-Accel-Buffering: no` na resposta é o que desliga o `proxy_buffering` do nginx só para esta rota — sem ele os eventos chegariam todos juntos, no fim.
  - **Polling continua como reserva**: o front cai nele se o `EventSource` não existir, não abrir em 8 s (proxy que segura `text/event-stream`) ou desistir (`readyState === CLOSED`), e também quando o servidor encerra o fluxo sem status final (evento `fim` — processamento sumiu, ou o teto de 1 h).
  - A permissão é conferida **antes** de o fluxo abrir: depois que a resposta começa a sair não há mais como devolver 403/404.
- Cancelamento cooperativo (migração `db/init/24_ia.sql`): `POST .../cancelar` só seta `FL_CANCELAR`; o worker checa a flag em `_checkpoint_cancelamento` (antes de cada chamada cara) e **não grava resposta** — marca `cancelado` e encerra sem consumir mais tokens. Processamentos `pendentes` cancelados com o servidor de pé não chegam a rodar.
- **Fim do turno avisa quem interessa** — nos três desfechos (`concluido`, `erro`, `cancelado`), o worker dispara o **webhook** de conclusão (`ia_webhook.notificar_inquerito`) para o solicitante e o criador da conversa; e o mesmo turno passa a ser devolvido pelo endpoint de **avisos in-app** (`GET /api/auth/avisos-ia`), que faz o toast aparecer em qualquer tela do sistema. Nenhum dos dois carrega a pergunta ou a resposta — ver [avisos_ia.md](avisos_ia.md).

### Montagem do prompt

- **SISTEMA_PROMPT** (`ia_utils.py`): analista criminal; dados do aparelho + documentos/IPLs/RIFs/SIMBA; recomenda começar por `resumo_inquerito` em perguntas abertas; método de trabalho (`_METODO_INVESTIGATIVO`): persistência com sinônimos/gírias (listas por tipo penal: drogas, dinheiro/lavagem, armas, homicídio/ameaça, polícia/fuga), busca híbrida (`buscar_hibrida`) quando for conceito (literal + sentido + grafo — substitui as buscas avulsas em paralelo), evidência citável com marcador `[msg:wpp:ID]`/`[msg:sms:ID]` (nunca inventar id), fato × inferência, padrões suspeitos, identificação via `cruzar_identidade` ou `pessoa_associada` já confirmada.
- **Contexto da investigação**: `INQ_TB01_INQUERITOS.TX_CONTEXTO_INVESTIGACAO` — texto livre editável pelo investigador (`GET/PUT /api/investigacao/inqueritos/{id_inquerito}/contexto`, qualquer usuário com acesso) e injetado no prompt de cada conversa.
- **SISTEMA_PROMPT_ESCOPO** (`ia_utils.py`): conversas restritas têm prompt próprio por tipo (`rif`, `simba`, `ipl`, `extracao`, `documento`, `wpp/sms/msg`, `conversa_*`) — as ferramentas fora do escopo nem existem na conversa; descrevê-las confundiria o modelo. Os de mensageiro/conversa são gerados em laço (`_NOME_MENSAGEIRO`). O modo "conteúdo completo" é ignorado silenciosamente em conversas com escopo (o front nem mostra o controle).
- **Hipóteses do inquérito**: `bloco_hipoteses_inquerito(id_inquerito)` (`hipoteses_utils.py`) é concatenado junto do contexto da investigação em toda conversa do inquérito — ver "Hipóteses do inquérito" acima e `hipoteses.md`.
- **Resumo do histórico**: em conversa longa, as mensagens antigas entram como um bloco `## RESUMO DA CONVERSA ATÉ AQUI` no fim do prompt de sistema, no lugar de irem inteiras no histórico — ver "Compactação do histórico" acima. Como bloco **dinâmico**, ele é sempre o último do prompt de sistema, fora do prefixo cacheável (ver "Cache de prompt e de resultados de ferramentas" abaixo).
- **Modo conteúdo completo** (opcional, por envio): o conteúdo datado do inquérito (WhatsApp/SMS/mensageiros, com transcrições) é anexado ao prompt — uma chamada se couber na janela, ou dividido **por data em lotes** com map-reduce (`montar_conteudo_inquerito`/`montar_lotes_por_data`, `ia_investigacao.py`; `SISTEMA_PROMPT_LOTE`/`SISTEMA_PROMPT_SINTESE`) se não couber. O orçamento de conteúdo (`_orcamento_chars_conteudo`) desconta o que não é conteúdo: prompt base, **schemas das ferramentas medidos** (um `json.dumps` do catálogo real — `_tokens_ferramentas`; era const. fixa defasada), histórico e reserva de saída; conversões conservadoras de ~3,3 chars/token. O painel de fontes restringe o conteúdo anexado às extrações marcadas; sem aparelho marcado, nada é anexado.
- **Não enviar+exibir prompt** (`exibir_prompt`): **nenhuma chamada ao provedor acontece no turno** — o prompt é serializado localmente por `_formatar_prompt_completo` e a compactação do histórico é desligada (`compactar=not exibir_prompt`). Como a IA não é chamada, ela não escolhe ferramentas: o prompt sai com o **catálogo** delas, não com resultados de busca (para ter dado no prompt sem chamar a IA, use o modo conteúdo completo, que anexa o conteúdo datado do mesmo jeito, também sem chamada). O resultado vai para a conversa via `_empacotar_resposta_prompt` (bloco ``` e aviso "não enviado à IA para gerar resposta") com modelo `(somente prompt)`, salvo em `data/analises_prompts/` + registro em `IA_TB04_ARQUIVOS_PROMPT` (`_salvar_prompt_para_analise`), devolvendo `id_arquivo_prompt` para o front oferecer o download. Arquivos com mais de 30 dias são apagados (`_limpar_prompts_antigos`).
  Até 2026-08-23 esse modo chamava o provedor: o flag era lido dentro do loop de `enviar_mensagem`, **depois** da resposta HTTP — gastava tokens, executava as buscas de verdade e exibia o histórico do fim do loop (com `tool_result`) no lugar do prompt inicial. O parâmetro `exibir_prompt` não existe mais em `ia_provedores.py`.
  **Conteúdo anexado por padrão**: com `exibir_prompt`, `incluir_conteudo` é ligado sozinho (`enviar_mensagem_usuario`) — sem chamada à IA nenhuma ferramenta de busca roda, então o conteúdo anexado é a única forma de o arquivo sair com dado dentro. Em conversa com escopo (RIF/SIMBA/IPL/documento) não há conteúdo de aparelho a anexar e o prompt continua sendo só o prompt de sistema + catálogo + histórico.
  **Tamanho das seções**: nesse modo o orçamento NÃO vem da janela do modelo configurado (o texto não vai para ele) — é `MAX_CHARS_CONTEUDO` (200 mil chars) por seção. Com a janela como régua, um modelo de 32k fatiava o inquérito em dezenas de pedaços, estourava `MAX_LOTES` e o fim do período ficava de fora; medido num inquérito de 40 mil eventos, o arquivo passou de 1,2 MB (40 seções, no teto) para 3,2 MB (26 seções, nenhuma truncada). No teto ainda, o prompt sai com um aviso explícito de corte.
  **Um prompt, não um map-reduce**: as seções entram sob `SISTEMA_PROMPT` + `_INTRO_CONTEUDO_SECOES`, com o cabeçalho do inquérito só na primeira. `SISTEMA_PROMPT_LOTE`/`SISTEMA_PROMPT_SINTESE` ficaram restritos ao map-reduce de verdade (modo conteúdo completo com envio) — no arquivo eles mandavam a outra IA "juntar as análises parciais" que não existem lá.
  **Exibição x arquivo**: acima de `_MAX_CHARS_PROMPT_INLINE` (60 mil chars) a bolha do chat recebe só o começo do prompt, com o total em caracteres e o apontamento para o .md — a conversa é recarregada do banco a cada envio e megabytes numa bolha travam o navegador. O .md nunca é cortado.

### Compactação do histórico (resumo das mensagens antigas)

Todo turno reenvia a conversa ao provedor — antes, ela ia **inteira**: cada pergunta pagava de novo
os tokens de tudo o que já havia sido dito e, numa conversa longa, o prompt acabava estourando a
janela do modelo (erro do provedor, turno perdido, sem nenhum tratamento).

`api/funcoes/ia_historico.py` (`montar_historico`) transforma o histórico em **resumo + últimas
mensagens**, e serve aos dois chats (as tabelas entram por parâmetro):

- **Quando dispara**: o histórico (resumo já gravado + mensagens ainda não resumidas) passa de
  `_FRACAO_JANELA_HISTORICO = 15%` da janela do modelo, com piso de `_MIN_TOKENS_HISTORICO = 3000`
  tokens (janela pequena de modelo local não compacta a conversa no segundo turno). Mesma conversão
  conservadora de ~3,3 chars/token (`CHARS_POR_TOKEN`, agora importada por `ia_utils` — uma só verdade).
- **O que faz**: as mensagens antigas (tudo menos as `_MENSAGENS_RECENTES = 4` últimas, sendo a
  última a pergunta deste turno) viram um resumo, numa chamada ao próprio provedor **sem
  ferramentas** (`_SISTEMA_RESUMO`: preservar identificadores literais — `[msg:wpp:ID]`, CPF/CNPJ,
  telefones, valores, datas —, as buscas que não deram em nada, pendências; nunca concluir nada novo).
- **Onde entra**: no **prompt de sistema** (`bloco_resumo`), não na lista de mensagens — assim não
  há risco de quebrar a alternância `user`/`assistant` exigida pela API da Anthropic, e o bloco
  aparece também no "não enviar+exibir prompt" e na auditoria (`TX_CONTEXTO`).
- **Persistência**: `TX_RESUMO_HISTORICO` + `ID_MENSAGEM_RESUMIDA_ATE` na própria conversa — os
  turnos seguintes reaproveitam o resumo **sem nenhuma chamada extra**; só recompacta quando o
  histórico crescer de novo. As mensagens originais continuam gravadas e visíveis na tela: a
  compactação é do prompt, não da conversa.
- **Quando não roda**: no modo "não enviar+exibir prompt" (`compactar=False`) — o investigador pediu
  justamente para não chamar a IA neste turno.
- **Se o resumo falhar** (`ErroProvedorIA`): manda o histórico inteiro, o comportamento de antes —
  pior gastar tokens do que perder o turno. O cancelamento é respeitado (o `checkpoint` roda entre
  as rodadas de resumo) e a etapa aparece no front como "Resumindo a conversa anterior…".
- **Conversa que já estava gigante** quando isto entrou no ar: o resumo é fatiado em no máximo
  `_MAX_CHUNKS_RESUMO = 6` chamadas, começando pelo trecho mais recente; o que ficou de fora é
  declarado dentro do próprio resumo, em vez de sumir em silêncio.

### Cache de prompt e de resultados de ferramentas

Dois mecanismos evitam pagar tokens pelo mesmo trabalho mais de uma vez no mesmo turno (e entre
turnos da mesma conversa):

- **Cache de prompt** (`ia_provedores.py`): `enviar_mensagem` aceita `sistema` como **lista de
  blocos** — `ia_utils` manda `[parte_estática, resumo_do_histórico]`. Na **Anthropic**
  cada bloco vira um `system` block e todo bloco **exceto o último** recebe
  `cache_control: ephemeral` (o último é o resumo do histórico, que muda entre turnos — marcá-lo
  quebraria o prefixo em cache, e a própria API rejeita breakpoint no fim). Nos demais provedores
  (OpenAI/DeepSeek, Ollama, Google) os blocos são unidos num único texto com separador fixo
  (`\n\n`) — o prefixo estático permanece idêntico entre turnos, o que é exatamente o que o cache
  automático por prefixo desses provedores exige. As ferramentas continuam com o marcador na
  última do catálogo (Anthropic).
- **Cache de resultados de ferramentas** (`_ttl_cache`, `ia_investigacao.py`): ferramentas
  cujo resultado representa "trabalho já feito" (não mudam com a pergunta) são decoradas com TTL e
  devolvem o resultado anterior sem consultar o banco quando chamadas de novo com os mesmos
  argumentos — o efeito é direto no custo dos turnos, que muitas vezes chamam a mesma ferramenta
  várias vezes. TTL de 300 s: `resumo_inquerito`, `listar_pessoas_identificadas`, `listar_donos`,
  `listar_contas_simba` (também o `_cabecalho_inquerito`, que as chama); TTL de 120 s (mudam por
  ação manual do investigador): `listar_deteccoes_padroes`, `listar_marcacoes_relatorio`.
  `listar_documentos_inquerito` **não** é cacheada de propósito: a lista da tela de Investigação
  (rota `api/rotas/investigacao.py`, mesma função) precisa refletir uploads na hora.

### Poda de árvore: o modelo só vê o necessário

**A metáfora**: imagine a massa de dados de um inquérito como uma árvore — o tronco e as raízes são o banco (centenas de milhares de mensagens, arquivos com OCR, documentos extraídos), e cada folha é um dado. O modelo **nunca recebe a árvore**: a cada pergunta, o servidor **poda os galhos** e entrega apenas as folhas que a pergunta pediu. O que a IA "enxerga" num turno é só o que as ferramentas retornaram naquele turno — nada mais. Isso é segurança por **minimização de dados** (least privilege aplicado a modelo de linguagem): o que não foi enviado não pode vazar, ser alucinado nem ser devolvido a um pedido hostil.

A poda acontece em três camadas:

1. **Poda estática (o escopo é fechado antes da pergunta)**: a IA recebe apenas o **esquema** (nome, parâmetros e descrição) das ferramentas permitidas — o `id_inquerito` é removido do schema exposto e injetado pelo servidor em cada chamada (`_vincular_inquerito`, `ia_investigacao.py`). Conversas restritas reduzem o catálogo às ferramentas do tipo (`_FERRAMENTAS_POR_ESCOPO_DOCUMENTO`). Num convite, o modelo nem sabe que os galhos dos outros inquéritos existem.

2. **Poda dinâmica (cada ferramenta devolve uma fatia limitada, nunca o domínio inteiro)**:
   - Toda busca é executada com `LIMIT %(limite)s` via `_executar_com_meta` (`ia_investigacao.py`), que devolve `{registros, total_disponivel, mostrando, truncado, aviso}` — quando há mais dados, o resultado carrega o aviso "Mostrando X de Y registros.
     Use filtros mais específicos para refinar a busca." e o modelo é instruído a refinar em vez de somar às cegas. Limites por padrão: `buscar_contato_extracao` 20; `buscar_pessoas_ipl` / `buscar_achados_ipl` 200 ("para evitar contextos muito grandes", comentário no código);
     `buscar_lancamentos_simba` sempre limitado (no máximo 500 registros, ordenado pelo maior valor) — não pagina; para volume/totais use `listar_contas_simba`, `listar_contrapartes_simba` ou, para "quanto recebeu/pagou de terceiros", `resumo_credito_simba`.
   - **Panorama em vez de despejo**: `resumo_inquerito` entrega contagens por domínio, período coberto pelos dados e os contatos mais ativos — "mostra onde está a massa de dados" sem mandar a massa. O prompt recomenda começar por ele em perguntas abertas.
   - **Documentos**: `listar_documentos_inquerito` devolve só o catálogo (nome, tipo, `qt_caracteres`, erro); o texto de um documento é lido apenas **sob demanda**, documento a documento e **em partes de 30 mil caracteres** (`ler_documento_inquerito` → `documento_em_partes`; a resposta traz `parte`/`qt_partes`/`tem_mais` e a IA pede a próxima com `parte=2, 3…`) — a poda aqui é na seleção: o modelo escolhe qual folha abrir, não recebe a biblioteca inteira, e um IPL de meio milhão de caracteres não entra de uma vez na janela. A tela do investigador continua mostrando o texto **inteiro** (`ler_documento_inquerito_completo`, usada pela rota `GET /api/investigacao/inqueritos/{id}/documentos/{id_documento}`): a paginação é limitação do modelo, não do investigador.
   - **Nenhum binário vai ao modelo**: áudio vira transcrição (`transcricao_audio`), imagem vira texto de OCR (`buscar_texto_em_imagens`), anexo vira texto extraído — imagem, vídeo e áudio originais nunca saem do servidor da investigação.
   - A **auditoria** também poda: o contexto gravado em `IA_TB04_AUDITORIA` vai **truncado em 20.000 caracteres** (`_MAX_CHARS_CONTEXTO`), com o tamanho real ao lado — o registro diz o que foi enviado sem duplicar tudo.

3. **Poda por orçamento (a janela do modelo é finita, o servidor calcula o que cabe)**:
   `_orcamento_chars_conteudo` (`ia_utils.py`) desconta da janela de contexto configurada (`janela_contexto_configurada()`) tudo o que **não** é conteúdo — reserva de saída (`_TOKENS_RESERVA_SAIDA = 4096`, espaço da resposta), prompt base (`_TOKENS_RESERVA_BASE = 1500`), os **schemas das ferramentas medidos de verdade** (`_tokens_ferramentas`: um `json.dumps` do catálogo real, cacheado; era uma constante fixa de 3000 que desatualizava silenciosamente conforme o catálogo crescia — e subestimar inflava o orçamento, estourando a janela) e o histórico da conversa (`_tokens_historico`). A conversão usa ~3,3 caracteres por token de propósito conservadora (`_CHARS_POR_TOKEN`), com piso de 2.000 tokens (`_MIN_TOKENS_CONTEUDO`) para janelas pequenas não zerarem o orçamento. O que "sobra" é o
   teto do conteúdo anexado — melhor enviar um pouco menos do que arriscar o erro do provedor.

4. **Poda por tamanho do turno (o que volta das ferramentas não pode estourar a janela)**:
   o `LIMIT` da camada 2 conta *registros*, não *caracteres* — e é a **IA** quem escolhe o `limite`,
   sem saber o tamanho de cada registro. Foi assim que uma pergunta legítima ("liste as 223
   comunicações do RIF") derrubou um turno em produção: o texto de análise do COAF
   (`TX_INFORMACOES_ADICIONAIS`) tem ~3 mil caracteres por comunicação nos RIFs grandes (chega a 20
   mil), 250 delas dão ~900 mil caracteres, e o gateway recusou o turno inteiro com
   `ContextWindowExceededError` (253.953 tokens de prompt para uma janela de 262.144). Quatro travas,
   todas em cima do caminho comum — nenhuma ferramenta precisa lembrar de se limitar:
   - **Teto de registros por chamada** (`MAX_REGISTROS_FERRAMENTA = 500`, aparado em
     `_executar_com_meta`): acima do maior padrão do arquivo (300), então nenhuma ferramenta muda de
     comportamento; é só a trava contra o pedido exagerado.
   - **Texto de análise do RIF cortado em listagem** (`_cortar_analise_rif`, `MAX_CHARS_ANALISE_RIF =
     1200`): com mais de 5 comunicações no resultado, cada análise vem cortada e marcada
     (`analise_truncada`, `analise_tamanho_total`), com aviso dizendo como ler a íntegra (busca que
     isole a operação — período, valor, banco). Com até 5 resultados, o texto vem completo.
   - **Teto por resultado de ferramenta** (`_serializar_resultado`, `ia_provedores.py`): antes de
     virar mensagem, todo resultado é medido; se passar do orçamento (¼ da janela do modelo, entre 20
     mil e 200 mil caracteres), leva três cortes, do menos ao mais destrutivo — encolher campos de
     texto livre, descartar itens da maior lista, e por fim cortar o JSON bruto. Sempre sai JSON
     válido e **sempre com aviso** (`truncado_por_tamanho`/`aviso_tamanho`): a IA tem que saber que o
     dado está incompleto, senão responde como se aquilo fosse tudo.
   - **Poda da conversa acumulada** (`_podar_conversa`): o turno guarda a chamada e o resultado de
     cada rodada, por até 25 rodadas — mesmo cada resultado cabendo, a soma estoura. Antes de cada
     requisição, os resultados **mais antigos** viram um marcador ("[resultado descartado para a
     conversa caber na janela…]") até a conversa caber em 75% da janela menos o prompt de sistema e
     os schemas. Perguntas e respostas nunca são podadas; a conversa gravada no banco não muda — a
     poda é do prompt.
   Se ainda assim o provedor recusar por contexto, o erro é reconhecido (`ErroContextoExcedido`,
   `_e_erro_de_contexto`), a conversa é podada pela metade e a requisição é **repetida uma vez**
   (`_post_com_poda`). Só se falhar de novo o turno morre — e aí com uma mensagem em português
   ("A conversa passou do tamanho que este modelo aceita de uma vez. Comece uma conversa nova…"),
   não com o JSON cru do LiteLLM.

   **A régua é o "Tamanho do contexto" das Configurações** (`AI_CONTEXT_LENGTH`, ou o campo da IA
   própria do usuário): num gateway o nome do modelo é um apelido interno (`text-model`, `auto`) que
   não casa com `JANELAS_CONTEXTO` e cai no padrão conservador de 32 mil — com o campo preenchido
   (262144, no gateway da instalação), os cortes usam a janela real em vez de encolher tudo à toa.
   Antes esse campo só valia para o Ollama (`num_ctx`); hoje vale para todo provedor e aparece na
   tela para todos.

Resultado de segurança: o tráfego de saída de cada turno é **limitado, mínimo e auditável** — a combinação ferramentas + conteúdo enviados por vez vira o registro de `IA_TB04_AUDITORIA` (`TX_FERRAMENTAS`/`TX_DOCUMENTOS`, JSONB), e o isolamento por inquérito (`id_inquerito` injetado, nunca escolhido pela IA) garante que a poda nunca cruze a cerca de um inquérito para outro.

### Relatório em Word (.docx) quando o investigador pede relatório

Pedir um relatório no chat — no chat do inquérito, no "Prompt de IA específico" (conversa restrita a
um documento) ou no painel flutuante — devolve, além da resposta na conversa, um
**.docx pronto para baixar**, por um link no fim da própria mensagem
(`📄 Baixar este relatório em Word (.docx)`). Como o link fica gravado no texto da mensagem, quem
reabrir a conversa depois continua achando o arquivo. Tudo em `api/funcoes/ia_relatorio_docx.py`:

- **Gatilho** (`pedido_de_relatorio`): o texto do investigador, sem acento e em minúsculas, com uma
  palavra-alvo (`relatório`/`word`/`docx`) precedida de um verbo de geração dentro de 80 caracteres
  (`_JANELA_VERBO`), ou começando pela própria palavra — o comando telegráfico "Relatório..."
  do botão **Gerar relatório**. É deliberadamente generoso: falso positivo custa um .docx a mais,
  falso negativo custa o recurso inteiro. "O que diz o relatório do COAF?" não dispara (não há verbo
  de geração). **Não** dispara no modo "não enviar+exibir prompt": ali não existe resposta da IA
  para converter.
- **Prompt** (`INSTRUCAO_RELATORIO`): o mesmo gatilho acrescenta ao fim do prompt de sistema um
  bloco `## FORMATO DE RELATÓRIO` pedindo documento (título com `# `, seções com `## `, sem
  saudação, marcadores de evidência junto de cada afirmação, fato separado de inferência). Sem isso
  a IA responde em tom de conversa e o Word sai com cara de chat. No map-reduce do modo "conteúdo
  completo" o bloco entra **só na síntese** — as parciais por período são insumo, não documento.
- **Conversão** (`escrever_markdown`): mesmo subconjunto de markdown do renderizador do chat
  (`web/js/ia.js`) — título, negrito/itálico/código, lista, lista numerada (com o número literal, que
  a IA costuma citar), tabela, citação e bloco de código. O que não é reconhecido vira parágrafo:
  nenhuma linha se perde. Link markdown vira só o rótulo; os marcadores `[msg:wpp:ID]`/`[doc:ID]`
  vão **literais** para o documento — são a rastreabilidade da prova. Cabeçalho (Ministério/DPF),
  rodapé com número de página e sombreado de célula são reaproveitados de `relatorios_docx.py`
  (funções privadas, importadas de propósito: reimplementar faria os dois documentos divergirem).
- **Capa**: título (o `# ` que a IA escreveu, que sai do corpo para não aparecer duas vezes), ficha
  com inquérito, data, investigador e modelo, o pedido literal do investigador e o aviso de que
  o documento foi gerado por IA e deve ser conferido nas fontes.
- **Arquivo**: `PASTA_INVESTIGACAO/relatorios_ia`, registrado em `IA_TB05_RELATORIOS_DOCX` (o
  binário não vai para o banco). Apagados depois de 30 dias (`_limpar_relatorios_antigos`, limpeza
  preguiçosa na geração seguinte) e junto com a conversa (`apagar_relatorios_da_conversa`); a rota
  de download responde 404 explicando que o relatório expirou, em vez de 500 de arquivo sumido.
- **Nunca derruba o turno**: `anexar_relatorio` engole qualquer erro (log) e devolve a resposta sem
  o anexo — neste ponto a resposta da IA já existe e vai ser gravada de qualquer jeito. Resposta com
  menos de `_MIN_CHARS_RESPOSTA = 400` caracteres também não vira Word ("não encontrei nada" não é
  relatório).

### Auditoria

`api/funcoes/ia_auditoria.py` grava um registro por turno em `IA_TB04_AUDITORIA` (origem `chat_inquerito`/`busca_automatica`/`busca_documentos`): prompt, contexto (truncado a `_MAX_CHARS_CONTEXTO = 20000`, com o tamanho real em `QT_CHARS_CONTEXTO`), ferramentas chamadas (`TX_FERRAMENTAS`), documentos consultados (`_FERRAMENTAS_DOCUMENTO` = `ler_documento_inquerito`/`ler_documento`), chamadas de API, tokens (estimados se o provedor não informar), custo estimado em USD e tempo. Desde a migração `0018` o registro guarda também `ID_MENSAGEM` — a resposta do assistente daquele turno —, que é o que permite mostrar o gasto na própria bolha (abaixo). A gravação **nunca levanta exceção** — falha de auditoria não derruba o turno. A tela de admin (ver `auditoria.md`) lista via `GET /api/admin/ia/auditoria` e detalha via `GET /api/admin/ia/auditoria/{id_auditoria}` (`api/rotas/auditoria_ia.py`).

### Gasto do turno na bolha (tokens, tempo e custo)

Cada resposta do assistente mostra, na linha de meta ao lado do modelo, **o que aquele turno
custou**: `↑ entrada / ↓ saída tokens · tempo · US$`. Antes esses números só existiam somados, no
cartão de resumo no topo da conversa — que não dizia qual pergunta saiu cara.

- **De onde vem**: `listar_mensagens_ia` (`ia_utils.py`) faz um `LEFT JOIN LATERAL` da mensagem com
  `IA_TB04_AUDITORIA` por `ID_MENSAGEM`. Resposta sem registro casado (anterior à migração `0018`
  e fora do alcance do backfill) e resposta do modo "não enviar+exibir prompt" (nada foi ao
  provedor) simplesmente não ganham os campos.
- **`~` antes dos tokens** = `IN_TOKENS_ESTIMADOS`: o provedor não informou o uso e os números
  foram estimados pelo tamanho dos textos.
- **Custo em reais**: a auditoria grava US$ (é o preço tabelado dos provedores, `PRECOS_USD_POR_MILHAO`
  em `ia_provedores.py`). O valor em R$ aparece ao lado **quando há `AI_COTACAO_BRL` no `.env`** —
  sem cotação configurada, só o US$; inventar uma taxa de câmbio seria pior do que não mostrar.
  A chave não tem campo na tela de Configurações: é editada no `.env` (o `upsert` da tela preserva
  as chaves que ela não conhece).
- **Aviso de contexto cheio**: passando de **80%** da janela do modelo, a bolha ganha uma tarja
  dizendo em que percentual o turno ficou e sugerindo abrir uma conversa nova — é a partir daí que
  o histórico começa a ser resumido/podado antes de ir ao provedor, com perda de detalhe. A janela
  é a **daquele usuário** (a IA própria dele, se tiver), servida por `GET /api/investigacao/ia/parametros`.

### Auditoria de pensamento (o bloco recolhido na bolha)

Cada resposta do assistente pode trazer, no pé da bolha, um `<details>` **recolhido** com a
trilha do turno: o raciocínio que o modelo devolveu e, rodada a rodada, as ferramentas que ele
chamou com os argumentos. É a mesma informação da tela de auditoria, ao lado da resposta —
para quem vai citar o resultado num relatório e precisa saber de onde ele veio.

- **Onde é montada:** `AuditoriaIA.trilha` (`ia_provedores.py`). Uma entrada por rodada HTTP —
  o limite de rodada sai de `registrar_chamada`, que os quatro despachantes já chamam uma vez
  por requisição. `trilha_para_gravar()` descarta as rodadas vazias; turno sem raciocínio e sem
  ferramenta grava `NULL` e não ganha botão.
- **Onde é gravada:** coluna `TX_RACIOCINIO JSONB` de `IA_TB02_MENSAGENS` e de
  `CAS_TB07_MENSAGENS_IA` (migração `0013`). Fica no histórico: reabrir a conversa meses depois
  ainda mostra. Mensagens anteriores à migração têm `NULL` — sem botão, nada quebra.
- **Onde é desenhada:** `web/js/utils/ia_raciocinio.js`, chamado por `js/ia.js`.
  Tudo entra por `.text()`, nunca por `.html()`: é texto do modelo sobre dados do aparelho.
- **Anonimização:** o raciocínio é descodificado **na captura** (`_capturar_raciocinio`), não no
  fim como a resposta — ele viaja pelo objeto de auditoria e nunca passa pelo texto de retorno.
  Sem isso o investigador leria "FULANO A" no raciocínio e o nome real na resposta.

**Nem todo provedor devolve o raciocínio.** A trilha de ferramentas aparece sempre; o
pensamento, só onde a API expõe:

| Provedor | Campo | Devolve? |
|---|---|---|
| `openai` (API pública) | — | **Não.** Cobra os tokens de reasoning e só expõe um resumo pela Responses API, que este app não usa |
| `openai-cert` (gateway LiteLLM) | `reasoning_content` | Depende do modelo upstream |
| `deepseek` | `reasoning_content` | Sim |
| `ollama` / `lmstudio` | `thinking`, ou `<think>…</think>` dentro do `content` | Conforme o modelo (DeepSeek-R1, Qwen) |
| `anthropic` | blocos `{"type": "thinking"}` | Só se o corpo pedir `thinking` — hoje não pedimos; o código já lê quando vier |
| `google` | — | Não (resposta é texto puro) |

O `<think>…</think>` dos modelos locais é **separado do texto da resposta** (`_separar_think`):
antes ele aparecia cru na bolha.

## Consolidação em planilha (agente em background)

Pedido do tipo "planilha com todas as armas apreendidas em 2026 a partir dos 200 PDFs" **não cabe
numa resposta de chat**: a tabela é cortada pelo teto de tokens no meio e o "continue" não recupera
o resto. A ferramenta `gerar_planilha_consolidada` (`ia_investigacao.py`) resolve isso em duas
etapas, com confirmação do investigador no meio:

1. **Avaliar** — a IA chama com `avaliar=true`; `avaliar_consolidacao` (`ia_planilha_utils.py`)
   conta os documentos do recorte (painel Fontes: documentos anexados e PDFs de IPL; sem seleção,
   o inquérito inteiro) e devolve `qt_documentos`/`qt_lotes_estimados`/`minutos_estimados`. Nada é
   disparado. A IA explica o funcionamento e o tempo ao investigador e **pergunta se pode acionar**
   (instrução no `SISTEMA_PROMPT` + na descrição da ferramenta).
2. **Disparar** — depois do "sim", a IA chama com `avaliar=false`, `pedido` e `colunas`. O job
   `consolidacao_ia` (`JOB_TB01_JOBS`, `fl_pesado=False` — disputa cota de IA, não CPU) roda em
   thread: **map** = uma chamada à IA por lote de 60k chars de cada documento pedindo as ocorrências
   em JSON com as colunas; **reduce** = dedup determinístico (mesmos valores = mesma linha, fontes
   acumuladas na coluna `Fonte`, que o job acrescenta sozinho); o `.xlsx` (openpyxl) vai para
   `data/planilhas_ia` e a linha para `IA_TB07_PLANILHAS` (migração `0053`; arquivos vivem 30 dias).

Acompanhamento: o `id_job` entra em `CO_JOBS` da resposta (mecanismo da migração `0045`, o mesmo do
OCR) e o cartão na bolha faz polling de `/api/investigacao/jobs/{id}` — progresso, cancelamento e,
no fim, o link `GET /api/investigacao/ia/planilhas/{id}/download` (que reconfere o acesso pelo
inquérito). Se o investigador saiu do chat, o aviso in-app (`avisos_ia.md`) também dispara quando o
job termina. O batimento do job é por lote (watchdog de 5 min).

## Function calling e MCP

### O que é function calling (no sistema)

*Function calling* é o recurso do provedor de IA que permite ao modelo **pedir a execução de funções** em vez de responder direto: o servidor envia um catálogo de ferramentas como **JSON Schema** (nome, parâmetros e descrição — ver `FERRAMENTAS_IA`, `ia_investigacao.py`), o modelo responde com "quero chamar `buscar_mensagens` com `{termo: 'pix', limite: 50}`", o servidor **executa a função real** (SQL no banco, com os limites e o escopo injetado) e devolve o resultado
como se fosse uma mensagem nova — e o ciclo recomeça até o modelo ter o que precisa ou bater o teto de `MAX_ITERACOES_FERRAMENTAS = 25`. Ponto-chave: o modelo **não tem acesso ao banco nem aos arquivos** — ele só conhece descrições de capacidades e só recebe o que as funções devolvem (a poda da seção acima).

### O que é MCP

**MCP (Model Context Protocol)** é um **protocolo aberto que padroniza a conexão entre aplicações de IA e ferramentas/dados externos**. Resolve um problema de integração: hoje cada aplicação de IA precisava de uma integração sob medida para cada ferramenta (o GitHub, o banco de dados, o sistema de tickets...) — com MCP, um **host** (o app de IA) conversa com **servidores MCP** (cada ferramenta/dado exposto) por um **contrato comum** baseado em **JSON-RPC 2.0**, com três
primitivas:

| Primitiva | O que é | Análogo no sistema |
|---|---|---|
| **Tools** | ações que o modelo pode pedir para executar | `FERRAMENTAS_IA` (schemas + `FUNCOES_IA`) |
| **Resources** | dados/contexto que o app pode ler | o conteúdo podado (rota de ferramentas, modo "conteúdo completo", `ler_documento*`) |
| **Prompts** | templates de instrução reutilizáveis | `SISTEMA_PROMPT`/`SISTEMA_PROMPT_ESCOPO` e os prompts de lote |

O modelo decide **o quê** chamar; o servidor MCP decide **como** executar e o que expor — a separação entre raciocínio e acesso a dados é a mesma ideia central. Por isso o MCP é comparado a um "USB-C da IA": um só padrão de conexão para vários periféricos.

### Onde o sistema se parece — e onde difere

**Semelhanças (a arquitetura é a mesma de um cliente MCP):**

- O modelo só vê **schema + descrição**, nunca o dado bruto por padrão — tools do MCP e  `FERRAMENTAS_IA` são o mesmo contrato mental;
- A execução é **server-side**, com o servidor decidindo permissões e escopo; o resultado volta ao modelo como ferramenta executada;
- O isolamento é declarativo: no MCP, cada servidor define o que expõe e exige autorização; aqui,  `_vincular_inquerito`/`_FERRAMENTAS_COM_ESCOPO` fazem o mesmo papel **em código**, dentro do processo.

**Diferenças (por que aqui é function calling direto, e não MCP):**

- **Transporte**: MCP é um protocolo de **rede** (JSON-RPC sobre stdio/HTTP/SSE) para conectar *muitos* servidores de terceiros a um mesmo cliente, com descoberta de servidores e handshake.
  No sistema, as "ferramentas" são **funções Python no mesmo processo** da API, chamando o mesmo pool de conexões — não há rede, descoberta nem handshake entre o "cliente" e o "servidor": são uma coisa só.
- **Confiança**: MCP existe para integrar sistemas **independentes** (cada servidor é uma fronteira de confiança). Aqui há **um único domínio de confiança** (a API + o banco do inquérito), com um só dono — o protocolo traria overhead sem ganho de segurança ou flexibilidade.
- **Autorização**: no MCP, a autenticação/autorização é entre host e servidor remoto; aqui, a autorização é **por linha de dado** (permissões por inquérito, escopo fixado por conversa) — mais fina do que o contrato MCP oferece por padrão.
- **"Poda" é local**: o orçamento de contexto, os limites de fatia e o truncamento da auditoria (seção acima) são decisões do servidor sobre **seu próprio** dado; no MCP isso fica a cargo de cada aplicação cliente, não é parte do protocolo.

## Limitações conhecidas

- **Google Gemini**: sem function calling — perguntas que exigem consultas ficam limitadas ao modo "conteúdo completo".
- **`buscar_lancamentos_simba`** é sempre uma amostra (por página); totais vêm de `listar_contas_simba`/`listar_contrapartes_simba` — o prompt instrui o modelo a não somar com base nela.
- **`buscar_hibrida`** depende da busca semântica configurada (`EMBED_MODEL`/`EMBED_MODE` — ver `configuracoes_ia.md`); sem ela, a ferramenta falha com erro descritivo.
- Conversas restritas a `extracao` não enxergam entidades/curadoria (que são cruzadas no nível do inquérito): `listar_pessoas_identificadas`, `listar_deteccoes_padroes` e `listar_marcacoes_relatorio` ficam de fora (`_FERRAMENTAS_POR_ESCOPO_DOCUMENTO`, `ia_investigacao.py`).
- O processamento roda em thread no próprio servidor: **sem fila externa** nem sobrevivência a restart — processamentos travados são só liberados após 30 min como stale.

## Ferramentas: catálogo real

Especificações em `FERRAMENTAS_IA` (`ia_investigacao.py`) com descrição apta a *function calling*; execução em `FUNCOES_IA`. Nenhuma recebe `id_inquerito` do modelo (ver acima).

| Categoria | Ferramentas | O que fazem |
|---|---|---|
| Conversas e contatos | `buscar_contato_extracao`, `buscar_mensagens`, `buscar_hibrida`, `buscar_ligacoes` | contatos do aparelho; mensagens por conteúdo/pessoa (com `transcricao_audio` dos áudios — mensagem de voz não tem texto); busca híbrida (literal + sentido por embeddings + expansão pelo grafo de entidades canônicas — substitui rodar as buscas avulsas em paralelo; precisa da busca semântica configurada — `configuracoes_ia.md`); ligações |
| Aparelho (não-WhatsApp) | `listar_localizacoes`, `buscar_notificacoes`, `buscar_notas`, `buscar_navegacao`, `buscar_aplicativos`, `listar_donos`, `buscar_chaves_pix`, `buscar_redes_wifi`, `buscar_emails`, `listar_contas_financeiras`, `listar_contas_usuario`, `buscar_arquivos_dispositivo`, `buscar_texto_em_imagens`, `processar_texto_em_imagens` | localizações; notificações; notas; navegação; apps instalados; dono(s); chaves PIX citadas; Wi-Fi; e-mails; contas bancárias/financeiras; contas de aplicativo logadas; arquivos (OCR); texto dentro de imagens; disparar OCR de um recorte de imagens (job em background, teto de 200/chamada) |
| Aparelho — domínios da auditoria de aproveitamento | `buscar_buscas`, `buscar_atividade_social`, `buscar_compromissos`, `buscar_conexoes_dispositivo`, `listar_antenas`, `listar_trajetos`, `listar_transferencias`, `listar_cartoes_carteira` | termos digitados em buscas (navegador, e-mail, mapas e dentro de apps — prova direta de intenção); publicações/comentários/check-ins de rede social; compromissos da agenda com convidados; conexões Wi-Fi/Bluetooth com outro aparelho (o MAC cruza aparelhos de extrações diferentes); antenas de telefonia (ERB) como rastro de deslocamento independente de GPS; trajetos de-para de apps de mapa; transferências de valores vistas no aparelho; cartões e passes da carteira. Ver `ufdr_dominios.md` |
| Conta na nuvem (Nuvem IPED) | `buscar_atividades_conta`, `buscar_acessos_conta`, `listar_dispositivos_conta`, `buscar_perfil_conta`, `buscar_eventos_agenda` | atividades, acessos (IP/user-agent/produto), dispositivos vinculados, perfil e agenda da conta importada |
| Trabalho já feito | `resumo_inquerito`, `listar_pessoas_identificadas`, `listar_deteccoes_padroes`, `listar_marcacoes_relatorio` | panorama do inquérito (volumes, período, top contatos); pessoas identificadas pelo investigador; padrões de crime detectados; marcações para relatório. Todas com **cache TTL** (ver "Cache de prompt e de resultados de ferramentas") — são chamadas repetidas dentro de um turno sem custo extra |
| Documentos/IPL/RIF/SIMBA | `listar_documentos_inquerito`, `ler_documento_inquerito`, `buscar_pessoas_ipl`, `buscar_achados_ipl`, `buscar_comunicacoes_rif_filtradas`, `buscar_envolvidos_rif_filtrados`, `listar_contas_simba`, `resumo_credito_simba`, `listar_contrapartes_simba`, `buscar_lancamentos_simba` | ler documentos anexados; pessoas/achados em IPLs em PDF; transações e envolvidos de RIFs do COAF (versões filtradas — as únicas; as versões sem filtro foram removidas por serem redundantes e caras); contas do SIMBA (totais **brutos**, por conta); `resumo_credito_simba` quebra o crédito/débito de cada extrato em externo/interno/financeiro-próprio/outros-sem-contraparte — é a ferramenta certa para "quanto X recebeu/pagou de terceiros" (ver `simba.md`, `_resumo_credito_extrato`); contrapartes consolidadas por pessoa; lançamentos individuais (sempre amostra limitada, não serve para somar) |
| Consolidação (ação, job) | `gerar_planilha_consolidada` | varre documentos anexados/IPLs do recorte com chamadas em lote à IA (map-reduce) e monta um `.xlsx` em background — para pedidos que não caberiam numa resposta ("planilha com todas as armas apreendidas em 2026 a partir dos 200 PDFs"). Em duas etapas: `avaliar=true` conta e estima o tempo (a IA explica e pede confirmação); `avaliar=false` dispara o job `consolidacao_ia`. Ver "Consolidação em planilha" |
| Bases de referência | `buscar_pessoa_referencia`, `buscar_candidato_referencia`, `cruzar_identidade` | pessoas (base Serasa) e candidatos (TSE); cruzamento de identidade entre bases |
| Escrita (só busca automática) | `listar_entidades_existentes`, `criar_entidade`, `criar_relacao` | listar entidades para não duplicar; criação presa a um registro real (nunca inventar id) com **justificativa obrigatória**; entidades entram como **sugestão** (tracejada) no grafo até o investigador confirmar |

## Modelo de dados

Tabelas em `INVESTIGACAO` (migrações em `db/init/`):

| Tabela | Colunas principais | Migração |
|---|---|---|
| `IA_TB01_CONVERSAS` | `ID_CONVERSA_IA` (PK), `ID_INQUERITO` (FK→`INQ_TB01_INQUERITOS`), `NO_TITULO VARCHAR(3000)`, `DT_CRIACAO`; **+** `TP_ESCOPO`/`ID_ESCOPO` (FK polimórfica, sem FK declarada) quando criada de um escopo; **+** `TX_RESUMO_HISTORICO`/`ID_MENSAGEM_RESUMIDA_ATE`/`DT_RESUMO_HISTORICO` (compactação do histórico; NULL = conversa nunca compactada) | `09`, `78`, `79`, `80`, `99` |
| `IA_TB02_MENSAGENS` | `ID_MENSAGEM_IA`, `ID_CONVERSA_IA` (FK), `TP_PAPEL` (`user`/`assistant`), `TX_CONTEUDO`, `NO_MODELO` (modelo que respondeu), `DT_MENSAGEM`; **+** `TX_RACIOCINIO` (JSONB: trilha do turno exibida recolhida na bolha — raciocínio por rodada + ferramentas chamadas; NULL quando não há o que mostrar) | `09`, `0013` |
| `IA_TB03_PROCESSAMENTOS` | um por envio: `ID_MENSAGEM_USUARIO` (FK), `CD_STATUS` (`pendente\|processando\|concluido\|erro\|cancelado`), `QT_LOTES_TOTAL`/`QT_LOTES_CONCLUIDOS`, `TX_ERRO`, `ID_MENSAGEM_RESPOSTA`, `DT_INICIO`/`DT_FIM`, `FL_CANCELAR`, `TX_ETAPA_ATUAL`; índice único parcial `UX41_IA_TB03_CONVERSA_ATIVA` (trava) | `26`, `41`, `63` |
| `IA_TB04_AUDITORIA` | `NO_ORIGEM`, `ID_INQUERITO`/`ID_CONVERSA_IA`/`ID_USUARIO`, `NO_PROVEDOR`, `NO_MODELO`, `TX_PROMPT`, `TX_CONTEXTO`+`QT_CHARS_CONTEXTO`, `TX_FERRAMENTAS`/`TX_DOCUMENTOS` (JSONB), `QT_CHAMADAS_API`, `QT_FERRAMENTAS`, `QT_TOKENS_ENTRADA`/`SAIDA`, `IN_TOKENS_ESTIMADOS`, `VL_CUSTO_USD`, `NU_TEMPO_MS`, `CD_STATUS` (`ok`/`erro`), `TX_ERRO`, `DT_CHAMADA` | `38` |
| `IA_TB04_ARQUIVOS_PROMPT` | `ID_ARQUIVO_PROMPT`, `ID_CONVERSA_IA`, `ID_INQUERITO`, `ID_USUARIO`, `NO_ARQUIVO VARCHAR(500)`, `CAMINHO_ARQUIVO`, `DT_CRIACAO` — arquivo em `data/analises_prompts/` | `57` |
| `IA_TB05_RELATORIOS_DOCX` | `ID_RELATORIO_IA`, `ID_CONVERSA_IA` (sem FK), `ID_INQUERITO`, `ID_USUARIO`, `NO_ARQUIVO VARCHAR(500)`, `CAMINHO_ARQUIVO`, `DT_CRIACAO` — arquivo em `PASTA_INVESTIGACAO/relatorios_ia` | `120` |
| `IA_TB07_PLANILHAS` | `ID_PLANILHA_IA`, `ID_CONVERSA_IA` (sem FK, pode ser NULL), `ID_INQUERITO`, `ID_USUARIO`, `NO_ARQUIVO VARCHAR(500)`, `CAMINHO_ARQUIVO`, `QT_LINHAS`, `DT_CRIACAO` — `.xlsx` do job `consolidacao_ia` em `PASTA_INVESTIGACAO/planilhas_ia` (30 dias) | `0053` |
| `INQ_TB01_INQUERITOS.TX_CONTEXTO_INVESTIGACAO` | contexto livre (tipo penal, alvo, hipóteses) injetado no prompt | `27` |
| `HIP_TB01_HIPOTESES` (+ `HIP_TB02_EVIDENCIAS`, `HIP_TB03_PENDENCIAS`) | workspace de hipóteses do inquérito inteiro — substituiu `ACH_TB01_ACHADOS_ANALISTA` na migração `0046`; ver `hipoteses.md` | `48` |

Tipos de escopo de `IA_TB01_CONVERSAS.TP_ESCOPO` (CHECK `CK05_IA_TB01_TP_ESCOPO`, em `db/init/24_ia.sql`): `rif` | `simba` | `ipl` | `irpf` | `ipei` | `extracao` | `documento` | `wpp` | `sms` | `msg` | `conversa_wpp` | `conversa_sms` | `conversa_msg`. `HIP_TB02_EVIDENCIAS.TP_ESCOPO` (CHECK `CK27_HIP_TB02_TP_ESCOPO`, em `db/init/48_hipoteses.sql`) aceita os de documento único — `rif` | `simba` | `ipl` | `irpf` | `ipei` | `extracao` | `documento` — e é opcional: uma evidência pode ser texto livre sem vínculo a item.

## Filtrar o grafo pelo chat

Nas telas que desenham um grafo — **Grafo do RIF** (`grafo_rif.html`), **Grafo do SIMBA**
(`grafo_simba.html`) e **Mesa de inquérito** (`mesa_inquerito.html`) — o painel flutuante não só
responde: ele propõe um **recorte do desenho**. Pedidos como "me mostra as principais relações do
fulano", "só as entradas acima de 50 mil" ou "esconde os não identificados" viram um botão
**"Aplicar no grafo"** no fim da resposta.

Como funciona, ponta a ponta:

1. **A tela se descreve.** Cada página de grafo registra o painel de filtros dela em
   `window.GrafoFiltros` (`web/js/utils/grafo_filtros.js`): nome lógico, rótulo, tipo (`texto`,
   `select`, `data`, `moeda`, `numero`, `bool`, `lista`) e o elemento de origem. Campo que não mora
   num input — os tipos de nó desligados na legenda da mesa — entra como campo **virtual**, com
   `ler`/`escrever` próprios.
2. **A pergunta leva o retrato da tela.** O painel flutuante manda `contexto_tela` no POST da
   mensagem: os campos, as **opções válidas lidas do DOM na hora** (a lista de bancos só existe
   depois que os dados carregam) e o que já está filtrado.
3. **O prompt ganha um bloco.** `api/funcoes/ia_contexto_tela.py` valida o que veio do navegador
   (página fora de `PAGINAS_CONHECIDAS`, tipo estranho ou texto gigante são descartados — nada
   entra no prompt sem passar por aí) e monta o bloco com os filtros e o protocolo: **descobrir os
   valores concretos com as ferramentas antes de propor**, explicar o recorte e terminar com um
   marcador `[filtro:{...}]`.
4. **O marcador vira botão.** O front separa o marcador do texto e desenha o chip com o que vai
   mudar, o botão que aplica e, depois de aplicar, um **Desfazer** que volta ao recorte anterior.

Duas decisões importantes:

- **A IA nunca mexe na tela sozinha.** O marcador é proposta; quem filtra é o investigador,
  clicando. Por isso o prompt manda escrever "o filtro vai mostrar", não "apliquei o filtro".
- **O marcador descreve a visão inteira.** Campo que ele não cita volta ao padrão — misturar a
  proposta com a sobra de um filtro anterior produziria um grafo que não é nem um nem outro.

Valor que não existe na tela (banco inexistente, data ilegível, tipo de nó que não está no grafo)
não é aplicado: vira a lista de "não aplicado" no chip, em vez de virar um filtro silenciosamente
diferente do que a IA propôs. Fora das telas de grafo nada muda — sem `contexto_tela` o bloco não
entra no prompt, e `web/js/ia.js` descarta um marcador que chegue por uma conversa começada numa
tela de grafo e reaberta lá.

## Configuração

- **Provedor/modelo/chave/URL**: `.env` via tela Configurações (`configuracoes_ia.md`) — ou, para um analista específico, a IA própria dele (`ia_por_usuario.md`), que vence o `.env`. Para a busca híbrida (`buscar_hibrida`) é necessário configurar `EMBED_MODEL`/`EMBED_MODE` — ver `configuracoes_ia.md`.
- **Ollama**: `AI_CONTEXT_LENGTH` (num_ctx por requisição) e download de modelo pela tela de Configurações (`configuracoes_ia.md`).
- **IA por delegacia**: `SEGURANCA.DELEGACIAS.IN_IA_ATIVA` (padrão `TRUE`; seed em `db/init/07_seguranca_usuarios.sql`), conferido em `api/funcoes/ia_delegacia.py` para o usuário **e** para o inquérito — ver `permissoes_delegacia.md`.
- **Permissões**: usuário aprovado (`exigir_aprovado`) com acesso ao inquérito; auditoria é de admin.

## Referências

- `api/funcoes/ia_utils.py` (conversas, processamento, prompts, orçamento), `api/funcoes/ia_investigacao.py` (catálogo de ferramentas, escopos), `api/funcoes/ia_provedores.py` (loop e formatos), `api/funcoes/ia_historico.py` (compactação do histórico), `api/funcoes/ia_auditoria.py`, `api/funcoes/embed_utils.py`, `api/funcoes/hipoteses_utils.py` (workspace de hipóteses), `api/funcoes/ia_relatorio_docx.py` (relatório em Word a pedido do investigador).
- `api/rotas/ia.py`, `api/rotas/auditoria_ia.py`, `api/rotas/hipoteses.py`.
- `web/ia.html` + `web/js/ia.js`, `web/js/utils/ia_flutuante.js` (painel flutuante em `whatsapp.html`/`mensageiros.html` e nas telas de grafo), `web/js/utils/grafo_filtros.js` + `api/funcoes/ia_contexto_tela.py` (filtro do grafo proposto pelo chat), `web/hipoteses.html` + `web/js/hipoteses.js`.
- Esquema: `db/init/24_ia.sql` (conversas, mensagens, processamentos, auditoria e relatórios do chat) e `db/init/48_hipoteses.sql` (`HIP_TB01..03`).
- `README.md` na raiz (visão geral), `docs/configuracoes_ia.md`, `docs/ia_por_usuario.md` (provedor próprio de um analista), `docs/avisos_ia.md` (o aviso de que a resposta ficou pronta), `docs/auditoria.md`, `docs/mesa_inquerito.md`, `docs/permissoes_delegacia.md`, `docs/banco_dados.md`.