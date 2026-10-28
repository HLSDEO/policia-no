# Auditoria

Este documento reúne os dois trilhos de auditoria do sistema, ambos exibidos na tela **Auditoria**
(`web/auditoria.html`, só admin): a **auditoria de acesso a inquérito** (quem leu, quem foi
barrado) e a **auditoria de IA** (o que foi enviado ao modelo e a que custo) — mesmo formato dos
demais documentos (`docs/`).

Permissão diz quem **pode**; auditoria diz quem **fez**. Juntas, as duas trilhas completam o
quadro: quem leu, quem mudou, quem perguntou.

| Seção | Rastro | Rotas |
|---|---|---|
| [Auditoria de acesso a inquérito](#auditoria-de-acesso-a-inquérito) | `SEGURANCA.USR_TB05_ACESSOS_INQUERITO` | `GET /api/admin/acessos` |
| [Auditoria de IA](#auditoria-de-ia) | `INVESTIGACAO.IA_TB04_AUDITORIA` | `GET /api/admin/ia/auditoria` |

## Auditoria de acesso a inquérito

### Visão geral

Permissão diz quem **pode** ler; a auditoria diz quem **leu**. Todo acesso a dado de inquérito —
e toda tentativa barrada por falta de permissão — fica registrado em
`SEGURANCA.USR_TB05_ACESSOS_INQUERITO` (`api/funcoes/auditoria_acesso.py`), visível na tela
**Auditoria** (`web/auditoria.html`, só admin). É a trilha que mostra o uso indevido de quem
**tem** permissão — coisa que o `403` por definição não pega — e complementa as auditorias de
escrita e de IA: quem leu, quem mudou, quem perguntou.

### Fluxo de uso

1. A dependency `exigir_acesso_inquerito` (ou as checagens inline) decide o acesso — ver
   `permissoes_delegacia.md`.
2. `usuario_tem_acesso_inquerito` / `eh_gestor_inquerito` chamam `registrar_acesso(id_usuario,
   id_inquerito, permitido)` a cada decisão, com resultado **permitido ou negado**.
3. O administrador abre **Auditoria** → card "Acesso a dado de inquérito"
   (`web/js/auditoria_acesso.js`), com filtros de período (7/30/90 dias ou todo), "Só tentativas
   negadas" e paginação ("Carregar mais").
4. Cada linha mostra período (hora), usuário, inquérito, recurso, método, nº de requisições, IP e
   resultado. Inquérito já apagado aparece como `#<id> (apagado)`.

Os dois cards da tela (`#acessos-card` e `#auditoria-card` em `web/auditoria.html`) nascem com a
classe `hidden` e só aparecem se o respectivo `GET` responder com sucesso; um usuário não-admin
que chegue à página (ex.: URL direta) recebe `403` das duas rotas e a tela fica em branco — o
`.fail` é silencioso de propósito, sem toast de erro, porque não é uma falha, é o guarda de acesso
funcionando.

### Como funciona

**O gancho** (em `investigacao_utils.py`): `registrar_acesso` é chamado de
`usuario_tem_acesso_inquerito`/`eh_gestor_inquerito`, por onde passa **toda** decisão de acesso —
tanto a dependency das rotas com `id_inquerito` no path quanto as ~20 checagens inline das rotas
que recebem `id_conversa`/`id_anexo`/`id_entidade` e derivam o inquérito. Auditar só a dependency
deixaria de fora justamente **abrir conversa e baixar mídia**, que é o acesso que mais interessa
registrar.

**De onde vem o recurso** (`auditoria_acesso.py`): a checagem não conhece a rota que a chamou,
então o caminho vem de um `ContextVar` (`_escopo_atual`) alimentado pelo middleware ASGI
**`ContextoRequisicao`** — registrado em `api/main.py` via `app.add_middleware(...)`. É ASGI puro
de propósito (não `@app.middleware("http")`, que usa `BaseHTTPMiddleware` e muda a propagação de
`ContextVar` entre tasks). Fora de uma requisição (ex.: thread de extração que também consulta
permissão), o recurso fica registrado como `-`.

**Agregação por hora** (`db/init/07_seguranca_usuarios.sql`): cada linha é
`usuário × inquérito × recurso × método × permitido × hora` (janela = `date_trunc('hour', now())`),
com `QT_ACESSOS` e primeiro/último acesso. A `UNIQUE` (`UX44_USR_TB05_JANELA`) faz o `ON
CONFLICT` do registrador virar "soma 1 no contador" em vez de linha nova. Uma conversa com
dezenas de anexos (ou telas de status em *polling*) vira **uma** linha com o contador alto — numa
medição real, 14 aberturas de conversa viraram uma linha com `qt_acessos = 14`.

**Recurso normalizado**: o path é gravado com os ids trocados por `{id}`
(`_SEGMENTO_NUMERICO` substitui `/123/` por `/{id}/`), ex.:
`/api/investigacao/conversas/{id}/mensagens` — o id individual explodiria a cardinalidade e
arruinaria a agregação. Na tela, `fmtRecurso()` (`web/js/auditoria_acesso.js`) ainda corta o
prefixo `/api/(investigacao|entidades|padroes|relatorios|ia|documentos)/` da coluna Recurso — só
para largura da coluna; o valor completo continua no `title` (tooltip) da célula e é o que fica
gravado em `NO_RECURSO`.

**IP**: mesmo `X-Real-IP` do limite de tentativas de login (lido do escopo ASGI; sem o header,
`client` do scope). `CO_IP` fica fora da chave de agregação (IP de saída pode mudar no meio da
sessão) — o gravado é o IP do primeiro acesso da janela.

**Duas decisões deliberadas**:

- `ID_INQUERITO` **não tem FK** — apagar o inquérito não apaga o rastro de quem o leu enquanto
  existia; a listagem usa `LEFT JOIN` em `INQ_TB01_INQUERITOS` e mostra `#<id> (apagado)` quando o
  número sumiu.
- **O admin também é auditado** — ele enxerga todos os inquéritos, e é de quem *tem* acesso que a
  trilha precisa dar conta.

**Falha de auditoria não bloqueia o acesso**: `registrar_acesso` nunca levanta — a exceção vai
para o log do servidor (`logger.exception`), mesma escolha da auditoria de IA: perder um registro
é ruim, travar a investigação é pior.

**Rotas** (`api/rotas/auditoria_acesso.py`, só admin):
`GET /api/admin/acessos` com filtros `id_inquerito`, `id_usuario`, `somente_negados`, `dias`
(1–3650), `limite` (≤500) e `deslocamento`; devolve `{itens, totais, qt_total}`, onde `totais`
traz `qt_requisicoes`, `qt_janelas_negadas`, `qt_usuarios` e `qt_inqueritos`.

### Modelo de dados

`SEGURANCA.USR_TB05_ACESSOS_INQUERITO` (`db/init/07_seguranca_usuarios.sql`):

| Coluna | Observações |
|---|---|
| `ID_ACESSO` | PK identity |
| `ID_USUARIO` | FK `FK159` → `USR_TB01_USUARIOS` |
| `ID_INQUERITO` | **sem FK** — a trilha sobrevive ao inquérito apagado |
| `NO_RECURSO` | rota normalizada (`/api/.../{id}/...`), até 200 chars |
| `TP_METODO` | GET/POST/DELETE… |
| `IN_PERMITIDO` | `false` = tentativa barrada com 403 |
| `CO_IP` | 45 (IPv6 textual), primeiro acesso da janela |
| `DT_JANELA` | hora cheia (`date_trunc('hour')`) |
| `DT_PRIMEIRO_ACESSO` / `DT_ULTIMO_ACESSO` | limites da janela |
| `QT_ACESSOS` | contador agregado |

Índices: `UX44` (chave da agregação), `IX105` (data), `IX106` (inquérito), `IX107` (usuário).

## Auditoria de IA

### Visão geral

Todo turno de IA grava um registro em `INVESTIGACAO.IA_TB04_AUDITORIA` (nome exato conferido em
`db/init/24_ia.sql`): prompt enviado, contexto utilizado, ferramentas chamadas (com
argumentos), documentos consultados, tempo de resposta, tokens e custo estimado. Cobre o **chat
por inquérito**, a **busca automática de entidades** e a **busca em
documentos** — além da análise de documento do inquérito. As rotas (`api/rotas/auditoria_ia.py`,
só admin) ficam ao lado da auditoria de acesso na tela **Auditoria** (`web/auditoria.html`). Servem
tanto para depuração (o que foi mandado ao modelo) quanto para rastreabilidade (quem perguntou o
quê, quando e a que custo).

### Fluxo de uso

1. Um turno de IA termina (sucesso, cancelado ou com erro) e `registrar_auditoria` grava o
   registro (`api/funcoes/ia_auditoria.py`).
2. O administrador abre **Auditoria** → card "Chamadas de IA" (`web/js/auditoria_ia.js`), com
   filtros por origem, usuário e inquérito, período (`data_de`/`data_ate`), ordenação (data,
   tokens, tempo — em cada sentido) e totais agregados do filtro (tokens, custo, tempo) acima da
   tabela.
3. `GET /api/admin/ia/auditoria` lista um **resumo por turno** (mais recente primeiro): data,
   origem, usuário, modelo, prompt (truncado), chamadas de API/ferramentas, tokens, custo, tempo
   e status; clique numa linha carrega `GET /api/admin/ia/auditoria/{id}` com o **registro
   completo** (prompt e contexto integrais, ferramentas e documentos).

### Como funciona

**Quem grava** (`funcoes/ia_auditoria.py`): `registrar_auditoria(origem, prompt, contexto,
auditoria, modelo, tempo_ms, status, erro, ...)` é chamado com `status="ok"` ao concluir, e com
`status="cancelado"`/`"erro"` no cancelamento ou na falha:

| Origem (`NO_ORIGEM`) | Onde é chamada |
|---|---|
| `chat_inquerito` | `api/funcoes/ia_utils.py` (ok/cancelado/erro) |
| `busca_automatica` | `api/funcoes/busca_automatica_utils.py` (ok/erro — sem status "cancelado" nesta origem) |
| `busca_documentos` | legado do chat de caso removido (linhas antigas continuam filtráveis) |
| `documento_analise` | `api/funcoes/documentos_utils.py` (análise de documento anexado ao inquérito) |

Cada origem grava com três desfechos possíveis — `status="ok"` ao concluir, `"cancelado"` se o
investigador abortou o turno em andamento (erro fixo "Cancelado pelo usuário.") e `"erro"` com a
exceção capturada — exceto `busca_automatica`, que não tem um caminho de cancelamento e por isso
só grava `ok`/`erro`.

**O que fica registrado** (colunas de `IA_TB04_AUDITORIA`):

- `TX_PROMPT` — a pergunta/instrução enviada pelo usuário;
- `TX_CONTEXTO` — o prompt de sistema/contexto, **truncado em 20.000 caracteres**
  (`_MAX_CHARS_CONTEXTO`), com o tamanho real ao lado em `QT_CHARS_CONTEXTO`;
- `TX_FERRAMENTAS` (JSONB) — `[{ferramenta, argumentos}, ...]` de todas as chamadas do turno;
- `TX_DOCUMENTOS` (JSONB) — as ferramentas de leitura de documento
  (`_FERRAMENTAS_DOCUMENTO = {"ler_documento_inquerito", "ler_documento"}`) já filtradas de
  `TX_FERRAMENTAS`, com seus argumentos;
- `QT_CHAMADAS_API`, `QT_FERRAMENTAS` — volume do turno;
- `QT_TOKENS_ENTRADA` / `QT_TOKENS_SAIDA` — uso informado pelo provedor; quando o provedor **não
  informa**, os tokens são **estimados** pelo tamanho dos textos (`~3,3 caracteres por token`,
  `_CHARS_POR_TOKEN`) e `IN_TOKENS_ESTIMADOS = true`;
- `VL_CUSTO_USD` — custo estimado (`estimar_custo_usd` em `ia_provedores.py`, pelo preço do
  modelo); `NULL` quando o preço do modelo é desconhecido;
- `NU_TEMPO_MS` — duração total do turno;
- `NO_PROVEDOR` / `NO_MODELO` — do `.env` (`AI_PROVIDER`/`AI_MODEL`) e da resposta do provedor;
- `ID_INQUERITO` / `ID_CONVERSA_IA` / `ID_USUARIO` — contexto do turno;
- `CD_STATUS` (`ok`/`erro`/`cancelado`) e `TX_ERRO`.

**Falha não derruba o turno**: `registrar_auditoria` nunca levanta — o `try/except` manda o erro
para o log do servidor. A investigação não pode parar porque o log falhou (mesma escolha de
`auditoria_acesso.registrar_acesso`).

**Rotas** (`api/rotas/auditoria_ia.py`, router com `Depends(exigir_admin)`):

- `GET /api/admin/ia/auditoria` — parâmetros `id_inquerito`, `id_usuario`, `origem` (validado
  contra `ORIGENS_VALIDAS`, que inclui `documento_analise` além das quatro origens do comentário
  da tabela), `data_de`/`data_ate` (`AAAA-MM-DD`, sobre a data da chamada), `ordenar`
  (`data_desc`/`data_asc`/`tokens_desc`/`tokens_asc`/`tempo_desc`/`tempo_asc` — fora da lista,
  `400`; padrão `data_desc`), `limite` (1–200), `deslocamento`; devolve `{itens, totais,
  qt_total}` com totais agregados de tokens de entrada/saída, custo em USD e tempo em ms.
- `GET /api/admin/ia/auditoria/{id_auditoria}` — registro completo (`SELECT a.*` + usuário);
  `404` se não existir.

**Tela** (`web/auditoria.html`): o seletor de origem lista as origens
(`chat_inquerito`, `busca_automatica`, `busca_documentos`, `documento_analise`); os
seletores de usuário/inquérito são populados de `/api/investigacao/usuarios-basico` e
`/api/investigacao/inqueritos` (só usuários ativos/aprovados e inquéritos existentes — registros
órfãos continuam acessíveis no filtro "Todos"); a tabela mostra Data / Origem / Usuário / Modelo
/ Prompt / API-Ferr. / Tokens / Custo / Tempo / Status.

### Modelo de dados

`INVESTIGACAO.IA_TB04_AUDITORIA` (`db/init/24_ia.sql`):

| Coluna | Observações |
|---|---|
| `ID_AUDITORIA` | PK identity |
| `NO_ORIGEM` | `chat_inquerito` \| `busca_automatica` \| `busca_documentos` (legado do caso removido) \| `documento_analise` (comentário da tabela; `documento_analise` também é aceito na rota) |
| `ID_INQUERITO` | FK `FK135` → `INQ_TB01_INQUERITOS` |
| `ID_CONVERSA_IA` | conversa de `IA_TB01` |
| `ID_USUARIO` | FK `FK137` → `USR_TB01_USUARIOS` |
| `NO_PROVEDOR` / `NO_MODELO` | provedor e modelo usados |
| `TX_PROMPT` / `TX_CONTEXTO` / `QT_CHARS_CONTEXTO` | prompt e contexto (truncado) |
| `TX_FERRAMENTAS` / `TX_DOCUMENTOS` | JSONB; um registro por turno, várias rodadas de function calling |
| `QT_CHAMADAS_API` / `QT_FERRAMENTAS` | contadores |
| `QT_TOKENS_ENTRADA` / `QT_TOKENS_SAIDA` / `IN_TOKENS_ESTIMADOS` | uso; estimado se o provedor não informar |
| `VL_CUSTO_USD` | `NUMERIC(12,6)`; `NULL` = preço do modelo desconhecido |
| `NU_TEMPO_MS` / `CD_STATUS` / `TX_ERRO` / `DT_CHAMADA` | duração, resultado e data |

Índices: `IX68` (DT_CHAMADA DESC), `IX69` (ID_INQUERITO), `IX70` (ID_USUARIO).

## Referências

- `api/rotas/auditoria_acesso.py` — `GET /api/admin/acessos` (só admin).
- `api/funcoes/auditoria_acesso.py` — `ContextoRequisicao`, `_dados_requisicao`,
  `registrar_acesso`, `listar_acessos`.
- `api/main.py` — `app.add_middleware(ContextoRequisicao)`.
- `api/rotas/auditoria_ia.py` — `GET /api/admin/ia/auditoria` (lista + totais) e
  `GET /api/admin/ia/auditoria/{id}` (detalhe).
- `api/funcoes/ia_auditoria.py` — `registrar_auditoria`, `listar_auditoria`, `obter_auditoria`.
- Pontos de chamada da auditoria de IA: `api/funcoes/ia_utils.py`,
  `busca_automatica_utils.py`, `documentos_utils.py`.
- `db/init/07_seguranca_usuarios.sql`, `db/init/24_ia.sql`.
- `web/auditoria.html`, `web/js/auditoria_acesso.js`, `web/js/auditoria_ia.js`.
- Documentos relacionados: `autenticacao.md` (identidade da sessão), `permissoes_delegacia.md`
  (quem pode ver cada inquérito), `chat_ia.md`/`configuracoes_ia.md` (emissão de consultas).