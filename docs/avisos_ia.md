# Avisos de conclusão da IA — in-app e webhook

## Visão geral

Uma resposta da IA pode levar minutos: o turno roda em segundo plano (`IA_TB03_PROCESSAMENTOS`
— ver [chat_ia.md](chat_ia.md)), e o analista sai do chat para
trabalhar em outra tela. Sem aviso, ele volta ao chat "no chute" para ver se ficou pronto.

São **duas peças independentes**, que resolvem a mesma frustração em lugares diferentes:

| Peça | Onde entrega | Precisa de cadastro? | Módulo |
|---|---|---|---|
| **Avisos in-app** | dentro do próprio sistema — toast em qualquer tela + notificação do navegador | não (o toast é automático; só a notificação do navegador pede permissão) | `api/funcoes/ia_avisos.py` + `web/js/utils/avisos_ia.js` |
| **Webhook** | **fora** do sistema — Discord, Slack, Teams, n8n, Make, endpoint próprio | sim, cada usuário cadastra a sua URL | `api/funcoes/ia_webhook.py` |

As duas usam o **mesmo critério de "interessa a quem"**: recebe aviso quem **enviou a pergunta**
(*solicitante*) e quem **criou a conversa** (*criador*), quando são pessoas diferentes. E as duas
cobrem o chat de inquérito (`ia.html`, painel flutuante e Mesa).

Nenhuma das duas carrega a **pergunta ou a resposta**: são avisos de que o turno terminou, com o
link para abrir a conversa. Dado de investigação não trafega para fora sem necessidade.

## Avisos in-app

### Fluxo de uso

1. O analista pergunta no chat de IA e navega para outra tela do sistema.
2. Quando o turno termina — **concluído, erro ou cancelado** —, aparece um **toast** com o título
   da conversa e um botão **Abrir**, que leva à conversa exata (`ia.html?inquerito=…&conversa=…`). O tipo do toast segue o desfecho: sucesso, erro ou aviso.
3. Se o usuário tiver autorizado, sai também uma **notificação do navegador** — o que cobre a aba
   em segundo plano ou a janela minimizada.
4. **A permissão do navegador nunca é pedida de surpresa.** Ela só é solicitada em dois gestos
   explícitos: ao **enviar uma pergunta** no chat, ou pelo item **"Avisos da IA"** do menu do
   usuário (que também liga e desliga a notificação depois de concedida).
5. Na tela do chat que já está com aquela conversa aberta, o toast **não** aparece — a resposta
   surge na própria conversa, o toast seria ruído. Com a aba em segundo plano, a notificação do
   navegador ainda sai.

### Como funciona

**Sem estado novo no banco.** Não há tabela de "aviso lido": a janela de tempo é o parâmetro
`desde`, que o cliente recebe pronto na resposta anterior (campo `agora`) e devolve na chamada
seguinte.

- **`GET /api/auth/avisos-ia?desde=`** (`api/rotas/auth.py`, `exigir_login`) chama
  `ia_avisos.listar(id_usuario, desde)`, que consulta o processamento + conversa + mensagem do
  usuário do chat de inquérito, filtrando `cd_status IN ('concluido','erro',
  'cancelado')`, `dt_fim > desde` e `(m.id_usuario = eu OR c.id_usuario_criador = eu)`.
- **Job de consolidação de planilha** (`origem: "planilha_ia"`): o turno que dispara a
  `gerar_planilha_consolidada` termina na hora (o aviso dele é sobre a resposta), mas o `.xlsx` fica
  pronto minutos depois — um segundo `SELECT` em `JOB_TB01_JOBS` (`tp_job='consolidacao_ia'`, dono =
  eu) avisa "sua planilha está pronta" com link para a conversa que o disparou
  (`tx_parametros->>'id_conversa_ia'`). O texto do toast sai de `textoAviso` por origem.
- **O relógio de referência é o do banco** (`SELECT now()`), nunca o do navegador: `agora` volta na
  resposta e vira o `desde` da próxima chamada. Assim um relógio de cliente atrasado ou adiantado
  não some com aviso nem o repete. `desde` com fuso horário, inválido ou no futuro é descartado e
  cai na janela padrão.
- **Duas janelas de segurança**: sem `desde`, olha os últimos **3 minutos** (`_JANELA_INICIAL_MIN`)
  — o suficiente para pegar a resposta que terminou durante a troca de página, sem ressuscitar
  aviso antigo a cada F5. E `desde` muito antigo é limitado a **60 minutos**
  (`_JANELA_MAXIMA_MIN`), para uma aba que dormiu a noite inteira não despejar dezenas de toasts ao
  voltar. Teto de 20 avisos por chamada (`_LIMITE`).
- **Falha nunca quebra a página**: qualquer exceção na consulta devolve lista vazia e vai
  para o log em nível `debug`. O aviso é acessório.

**No front** (`web/js/utils/avisos_ia.js`, o global `window.AvisosIA`, carregado em toda página
protegida):

- *Polling* de **15 s** com a aba visível e **30 s** oculta; ao voltar para a aba, confere na hora
  em vez de esperar o próximo tique. Começa sozinho no evento `analyser:usuario` disparado por
  `auth.js`.
- A lista dos avisos já mostrados fica em `localStorage` **por usuário** (até 80, com o `desde`
  junto), para o F5 não repetir o mesmo toast. `localStorage` bloqueado degrada para "no máximo
  repete um aviso depois do F5".
- No máximo **3 toasts por rodada**, para uma rajada de respostas não cobrir a tela.
- **Silenciamento** — duas formas, porque há dois tipos de tela:

  | Método | Para quem | Como |
  |---|---|---|
  | `AvisosIA.silenciar(origem, idConversa)` | telas de chat (`ia.js`) | declaram a conversa aberta ao trocar de conversa; a troca limpa só as chaves daquela origem, então `ia.js` silencia `chat_inquerito` **e** `planilha_ia` da mesma conversa (o cartão na bolha já mostra o progresso do job) |
  | `AvisosIA.silenciarSe(fn)` | painéis flutuantes (`ia_flutuante.js`) | registram uma função consultada **na hora do aviso**, porque o painel abre e fecha |

- `AvisosIA.pedirPermissao`, `notificacaoLigada`, `definirNotificacao`, `permissao` e
  `verificarAgora` completam a API pública. O item "Avisos da IA" do menu do usuário
  (`web/js/utils/sidebar.js`) usa as quatro primeiras.

## Webhook de conclusão da IA

### Fluxo de uso

1. Menu do usuário → **"Meu webhook IA"** (`web/js/utils/auth.js` monta o modal).
2. Informar a **URL** (`http`/`https`, até 2000 caracteres) e o checkbox **"Notificações ativas"**.
3. **Salvar**, ou **Salvar e testar** — o segundo dispara na hora um evento
   `ia.webhook_teste` e mostra o HTTP de resposta, para não descobrir a URL errada só no primeiro
   turno de verdade.
4. A partir daí, todo turno de IA que lhe diga respeito dispara um `POST` JSON. **URL em branco
   remove o cadastro** e para de notificar.

Cada usuário cadastra **a sua própria** URL: as rotas são `exigir_login` e sempre operam sobre
`usuario["id_usuario"]` da sessão — não há como um usuário ver ou mexer no webhook de outro, e não
há tela de administração disso.

### Como funciona

- **Disparo**: o chat por inquérito (`ia_utils`) chama
  `notificar_inquerito` ao fim do turno. A função resolve o **criador** da
  conversa, monta a lista de interessados (solicitante + criador, sem duplicar, cada um com o seu
  `papel`) e agenda um `POST` por destino que tenha URL **ativa** cadastrada.
- **Fire-and-forget**: cada entrega roda numa `threading.Thread` daemon com timeout de **10 s**
  (`_TIMEOUT_SEG`). Webhook lento, fora do ar ou devolvendo 500 **nunca** trava nem quebra o turno
  da IA — o erro só vai para o log. Sem URL cadastrada, nada acontece, em silêncio.
- **Payload** — só aviso, sem pergunta nem resposta:

  ```json
  {
    "evento": "ia.resposta_concluida",
    "origem": "chat_inquerito",
    "status": "concluido",
    "id_conversa_ia": 42,
    "id_inquerito": 7,
    "id_processamento": 915,
    "id_usuario_solicitante": 3,
    "papel": "solicitante",
    "titulo": "Movimentações do alvo",
    "erro": null,
    "dt_evento": "2026-09-06T14:02:11+00:00",
    "link": "ia.html?inquerito=7"
  }
  ```

  `evento` é `ia.resposta_concluida`, `ia.resposta_erro` ou `ia.resposta_cancelada`, conforme
  `status`; `origem` é `chat_inquerito`; `titulo` é cortado em 120 caracteres e `erro` em 500.

- **Validação da URL** (`validar_url`): esquema `http`/`https` obrigatório, host não vazio, até
  2000 caracteres. Erro vira `400` com a mensagem que o modal exibe.
- **Banco sem a migração**: a tabela pode ainda não existir (`db/migracoes/0005_webhook_ia_usuario.sql`
  não aplicada). A leitura devolve `None` e o turno da IA segue normalmente; a **escrita** levanta
  um erro amigável pedindo para aplicar as migrações pendentes, em vez de vazar o erro cru do
  Postgres.

### Rotas

| Rota | O quê |
|---|---|
| `GET /api/auth/webhook-ia` | `{definido, ativo, url}` do próprio usuário — a URL vai inteira, é dado dele |
| `PUT /api/auth/webhook-ia` | grava `{url, ativo}`; URL vazia remove |
| `POST /api/auth/webhook-ia/teste` | dispara o evento de teste **síncrono** e devolve `{ok, http_status}` ou `{ok: false, erro}` |

## Modelo de dados

Os avisos in-app **não têm tabela** — só leem `IA_TB03_PROCESSAMENTOS` / `IA_TB01_CONVERSAS` /
`IA_TB02_MENSAGENS`.

O webhook tem uma tabela, `SEGURANCA.USR_TB07_WEBHOOK_IA`
(`db/init/07_seguranca_usuarios.sql`; delta em `db/migracoes/0005_webhook_ia_usuario.sql`):

| Coluna | Observações |
|---|---|
| `ID_USUARIO` | **PK = FK** (`FK254` → `USR_TB01_USUARIOS`, `ON DELETE CASCADE`) — no máximo um webhook por usuário, e apagar o usuário leva o webhook junto |
| `TX_WEBHOOK_URL` | `TEXT NOT NULL` — a URL validada |
| `IN_ATIVO` | desligar sem apagar a URL |
| `DT_CRIACAO` / `DT_ATUALIZACAO` | `now()`; a gravação é `INSERT … ON CONFLICT (id_usuario) DO UPDATE` |

## Configuração

Nenhuma variável de `.env`. O webhook é autosserviço por usuário, e os avisos in-app não têm nada
a configurar no servidor — só a permissão de notificação, que é do navegador de cada um.

Os dois respeitam os portões da IA: quem não pode usar a IA não gera turno, logo não gera aviso
(ver `IN_IA_ATIVA` em [permissoes_delegacia.md](permissoes_delegacia.md)).

> **O webhook sai da rede do servidor.** É o único ponto do sistema em que a máquina do inquérito
> faz uma requisição de saída para um endereço escolhido pelo usuário. O payload é deliberadamente
> pobre (ids, título e link — sem pergunta nem resposta), mas o título da conversa é texto que o
> analista escreveu e o `id_inquerito` identifica o caso: numa instalação sem saída para a
> internet, o `POST` simplesmente falha e vai para o log, sem afetar o chat.

## Referências

- Funções: `api/funcoes/ia_avisos.py` (`listar`, `_parse_desde`), `api/funcoes/ia_webhook.py`
  (`validar_url`, `obter`, `salvar`, `remover`, `notificar_inquerito`,
  `enviar_teste`, `_interessados`).
- Pontos de disparo do webhook: `api/funcoes/ia_utils.py` (chat por inquérito).
- Rotas: `api/rotas/auth.py` — `GET /api/auth/avisos-ia`, `GET`/`PUT /api/auth/webhook-ia`,
  `POST /api/auth/webhook-ia/teste` (modelo `WebhookIaIn`).
- Front: `web/js/utils/avisos_ia.js` (`window.AvisosIA`), `web/js/utils/auth.js` (modal "Meu
  webhook IA"), `web/js/utils/sidebar.js` (itens "Meu webhook IA" e "Avisos da IA" no menu do
  usuário), `web/js/ia.js`, `web/js/utils/ia_flutuante.js`
  (silenciamento e pedido de permissão).
- Esquema: `db/init/07_seguranca_usuarios.sql`, `db/migracoes/0005_webhook_ia_usuario.sql`.
- Documentos relacionados: [chat_ia.md](chat_ia.md) (os processamentos em background que geram o
  evento), [interface.md](interface.md) (o módulo de toast e o menu do usuário),
  [ia_por_usuario.md](ia_por_usuario.md) (a outra exceção por pessoa nas telas de IA),
  [permissoes_delegacia.md](permissoes_delegacia.md).
