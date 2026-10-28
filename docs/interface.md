# Interface web e painel de servidor

Este documento reúne o **front** (HTML/CSS/JS puro servido pelo nginx) e o **painel Servidor**
(monitor de saúde da máquina, exclusivo de admins) — mesmo formato dos demais documentos
(`docs/`).

| Seção | Cobre |
|---|---|
| [Interface web](#interface-web--front-sem-build-menu-busca-e-diálogos) | front sem build, menu, busca global, diálogos, tema |
| [Componentes de UX compartilhados](#componentes-de-ux-compartilhados) | toast, camada de API, carregando, estados vazio/erro, tabela paginada, estado na URL, ícones e mapa de telas, acessibilidade |
| [Painel Servidor](#painel-servidor--saúde-da-máquina) | `web/servidor.html`, `GET /api/sistema/metricas`, `GET /api/health` |

## Interface web — front sem build, menu, busca e diálogos

### Visão geral

O front é **HTML/CSS/JS puro com jQuery**, sem build step nem framework: os arquivos de `web/`
são servidos direto pelo nginx (serviço `web` do compose) e uma edição em `web/` aparece recarregando a
página (o nginx manda `Cache-Control: no-cache` para `*.js|css|html` justamente para o navegador
revalidar). jQuery 3.7.1 fica em `web/vendor/jquery-3.7.1.min.js`, junto de `tom-select` e
`leaflet`; o estilo central é `web/css/style.css`, com tema claro/escuro por usuário e a marca
**PolícIA** (`web/img/logo-mark.png`/`logo-full.png`). Três peças de JS valem destaque: a barra de
navegação do topo, injetada (`web/js/utils/sidebar.js`), a busca global da tela Início (`web/js/home.js`) e os
diálogos próprios em modal (`web/js/utils/dialogo.js`) — mais o conjunto de componentes de UX
compartilhados (toast, indicador de carregamento, estados vazio/erro, ícones e mapa de telas) da
seção [Componentes de UX compartilhados](#componentes-de-ux-compartilhados) abaixo.

Toda página protegida carrega, nesta ordem: jQuery, `js/utils/utils.js`, `js/utils/icones.js` (biblioteca de
ícones SVG), `js/utils/mapa_telas.js` (mapa único das telas do sistema), `js/utils/toast.js`, `js/utils/api.js`
(rede de segurança de erro de API), `js/utils/carregando.js`, `js/utils/estados.js`, `js/utils/estado_url.js`,
`js/utils/dialogo.js`, `js/utils/theme.js` (aplica o tema salvo), `js/utils/sidebar.js` (injeta o menu), `js/utils/auth.js`
(guarda de autenticação — valida a sessão via `GET /api/auth/eu`, redireciona ao login se
expirada, cobre a página com aviso se o autocadastro ainda não foi aprovado e completa o rodapé do
menu com nome do usuário e os botões "Trocar senha", "Meu e-mail" e "Sair") e por fim o JS
específico da página. Os sete módulos entre `utils.js` e `theme.js` carregam em toda página
(inclusive `login.html`/`cadastro.html`/`redefinir_senha.html`, fora do guarda) — são leves e
ficam prontos para qualquer JS de página os chamar, mesmo sem edição de HTML.

### Fluxo de uso

1. `login.html` (com `cadastro.html` e `redefinir_senha.html` fora do guarda) → após o login, o
   `index.html` recebe o usuário pelo evento `analyser:usuario` (disparado por `auth.js`) e mostra
   a saudação com a busca global.
2. A busca acha **inquéritos pelo número** e **telas pelo nome/keywords**; com o campo vazio ela
   lista os inquéritos mais recentes.
3. Por qualquer tela, a barra do topo dá acesso a todas as seções (dropdown **Administração**,
   só para admin) e à direita ficam o dropdown de tema e o menu do usuário.
4. Ações destrutivas ou de entrada de texto usam os modais de `dialogo.js`.

### Como funciona

#### Barra de navegação (`web/js/utils/sidebar.js`)

O módulo ainda se chama `sidebar.js` por histórico — o menu vertical (`.sidebar`) foi substituído
por uma **barra horizontal no topo** (`.topnav`), usada hoje em **todas** as páginas ("Sempre usa
topnav horizontal (menu B) em todas as páginas", no próprio arquivo). O que sobrou de vertical no
CSS é resíduo do desenho anterior.

- A lista de seções (`NAV_ITEMS`) vem de `web/js/utils/mapa_telas.js` (`window.MapaTelas`) —
  **Início**, **Inquéritos / casos**, **Nova importação**, **Convergência** e o grupo recolhível
  **Administração** (Ingestão, Usuários, IA - Mesa de inquérito, Padrão de relatórios, Categorias
  de imagem, Gerencia de delegacias, Auditoria, Configurações de IA, API-PF, Alertas SIMBA,
  Servidor, Servidores (nós), Jobs & Filas, Desempenho, Documentação de código). `sidebar.js` só monta o HTML a partir dessa
  lista; ver [Ícones e mapa de telas compartilhados](#ícones-e-mapa-de-telas-compartilhados)
  abaixo para por que o mapa saiu do arquivo do menu. Os HTML não têm `<aside>` fixo — nada de 30
  cópias divergentes da navegação.
- Layout da barra: à **esquerda** a marca (`topnav-brand`, link para Início), os itens de
  `NAV_ITEMS` e o grupo Administração como dropdown (`topnav-group-toggle` +
  `topnav-group-dropdown`); à **direita** o dropdown **Temas** (montado por `theme.js`) e o menu
  do usuário (`topnav-user`, com Trocar senha / Meu e-mail / Sair).
- **Item do inquérito aberto**: o único item não fixo. Quando a URL traz `?inquerito=`, entra um
  chip `topnav-inquerito` logo depois de "Inquéritos / casos", com o número lido de
  `/api/investigacao/inqueritos` (cache de sessão). Com o cache quente ele já nasce escrito; senão
  nasce escondido e aparece quando a resposta chega — nunca fica um "Inquérito undefined" no meio
  da barra. Se a busca falhar, ou o inquérito não estiver na lista do usuário, ele não aparece.
  O aparelho e o nome da tela ficam na trilha do canto direito
  (`web/js/utils/navegacao_inquerito.js`).
- Só o rótulo de alguns itens aparece em tela estreita; os demais ficam só com o ícone e o
  `title`/`aria-label` como tooltip.
- Acessibilidade: `<nav role="navigation" aria-label="Navegação principal">`; o botão do grupo
  Administração leva `aria-expanded`/`aria-controls` sincronizados no clique; cada link ativo leva
  `aria-current="page"`. `sidebar.js` também injeta o **skip-link** ("Pular para o conteúdo",
  primeiro elemento focável da página) e marca `.content` com `id="conteudo-principal"` +
  `role="main"` — ver
  [Acessibilidade: foco visível e skip-link](#acessibilidade-foco-visível-e-skip-link).
- O grupo **Administração é escondido de quem não é admin**: os itens marcados `admin: true` no
  `MapaTelas` nascem com `.hidden` e só são revelados depois que `GET /api/auth/eu` confirma
  `in_admin` (quem não é admin tem o nó **removido** do DOM). O backend continua sendo o portão de
  verdade (`exigir_admin` nas rotas); esconder é a parte da tela.

#### Busca global (`web/js/home.js`)

- Roda no `index.html`: `#busca-global` filtra em dois conjuntos:
  - **inquéritos** — vêm de `GET /api/investigacao/inqueritos` e casam pelo **número**
    (`nu_inquerito`), com a lista limitada a 8 (`MAX_INQUERITOS`); cada resultado mostra um resumo
    (contagens de extrações, mensagens, contatos, RIFs, IPLs, administrador) e atalhos **Mesa** e
    **IA** (chips `span[data-href]` — `<a>` aninhado seria HTML inválido);
  - **telas do sistema** — a lista fixa `MapaTelas.TELAS_BUSCA` (Investigação, Inquéritos, Nova
    importação, Ingestão, Usuários, Auditoria, as duas abas de IA - Mesa de inquérito, Padrão de
    relatórios, Categorias de imagem, Configurações de IA, API-PF, Busca de evidências, Servidores
    descentralizados, Jobs & Filas, Desempenho, Documentação de
    código — mesmo módulo `mapa_telas.js` da barra de navegação) é comparada por `normalizar()` (NFD remove acentos + `toLowerCase`) sobre nome +
    descrição + `termos` extras do dia a dia — quem digita "permissões" chega em Usuários,
    "importar" em Ingestão.
- Com o campo vazio mostra os **inquéritos mais recentes**; com busca sem resultado, aviso
  "Nada encontrado". Teclado: `↑`/`↓` percorrem (com scroll), `Enter` abre o selecionado, `Esc`
  limpa; clique também seleciona.

#### Diálogos (`web/js/utils/dialogo.js`)

Substituem `window.alert/confirm/prompt`, que o navegador pode suprimir silenciosamente em aba de
fundo. O módulo injeta o próprio DOM (`modal-overlay` + `modal`, sem depender de HTML na página)
e expõe três funções em `window.Dialogo`, todas retornando `Promise`:

| Função | Retorna | Opções |
|---|---|---|
| `Dialogo.alerta(mensagem)` | `Promise<void>` | `titulo`, `rotuloOk` |
| `Dialogo.confirmar(opts)` | `Promise<boolean>` | `titulo`, `mensagem`, `rotuloOk`, `rotuloCancelar`, `perigoso` (botão vermelho) |
| `Dialogo.prompt(opts)` | `Promise<string>` ou `null` (cancelou) | `titulo`, `mensagem`, `valor`, `placeholder`, `maxlength`, `multilinha` (textarea) |

Passar string no lugar do objeto é atalho para `{ mensagem }`. Fechar é consistente em todos:
`Esc` (cancelar/OK), `Enter` (confirmar/salvar), clique no X ou fora do modal; o listener de
teclado é removido ao fechar para não vazar no `document`.

**Onde `Dialogo` não é usado, de propósito**: `web/js/utils/auth.js` monta os modais de "Trocar senha"
e "Meu e-mail" com o próprio `modal-overlay`/`modal` (mesma classe CSS de `dialogo.js`, mas HTML
próprio), porque são **formulários com múltiplos campos** — `Dialogo.prompt` só cobre um campo de
texto por chamada. É a mesma convenção visual (overlay + modal + `Esc`/clique fora fecham), só
sem passar pelo módulo genérico, que não foi desenhado para isso.

#### Menu do usuário na barra (`web/js/utils/auth.js`)

Depois que `GET /api/auth/eu` confirma a sessão, `auth.js` preenche o nome do usuário no
dropdown `topnav-user` (montado por `sidebar.js`), que traz cinco itens — os três de conta abaixo,
mais **"Meu webhook IA"** e **"Avisos da IA"**, documentados em [avisos_ia.md](avisos_ia.md):

| Botão | Ação | Rota |
|---|---|---|
| Trocar senha | modal com senha atual + nova (mínimo 8 caracteres); usado também na tela de cadastro pendente | `POST /api/auth/senha` |
| Meu e-mail | modal com e-mail (opcional) + senha atual; campo vazio remove o e-mail salvo | `POST /api/auth/email` |
| Sair | `POST /api/auth/logout` e redireciona ao login, mesmo se a chamada falhar (`.always`) | `POST /api/auth/logout` |

O e-mail é usado só pela recuperação de senha (ver `autenticacao.md`) — trocar ou remover exige a
senha atual, pelo mesmo motivo da troca de senha: é uma credencial de recuperação de conta, não
um dado de perfil solto. Se a sessão não estiver aprovada (`in_aprovado = false`), a página é
coberta por um overlay de "Cadastro pendente de aprovação" com apenas Trocar senha e Sair — as
chamadas de API da própria página continuam batendo em `403`, então não haveria nada útil para
mostrar de qualquer forma.

#### Temas

- `web/js/utils/theme.js` é o dono do assunto: a lista `TEMAS` (`{ id, nome, icone }`), a troca e o
  **dropdown "Temas"** do menu, que ele devolve pronto em `Tema.dropdownHtml()` para
  `sidebar.js` encaixar na direita da topnav. Hoje a lista tem Escuro (`dark`) e Claro (`light`).
- Cache local `localStorage["analyser-theme"]` aplica o tema no `<html data-theme="...">` antes do
  CSS carregar (script inline no topo de cada página — ele aceita qualquer id de formato válido,
  justamente para não precisar ser editado nas ~70 páginas a cada tema novo).
- Quem "manda" é o **banco**: a escolha chama `POST /api/auth/tema` (`api/funcoes/auth_utils.py:
  definir_tema`, validando contra `TEMAS_VALIDOS`), que grava `tp_tema` do usuário — o tema segue
  o usuário entre navegadores e dispositivos. `localStorage` é só cache para aplicar sem esperar
  a rede; quando o banco discorda, `auth.js` chama `Tema.definirDoServidor()`.
- **Criar um tema novo** são três lugares (a lista está repetida no cabeçalho de `theme.js`):
  1. `web/css/style.css` — um bloco `:root[data-theme="<id>"]` redefinindo **todos** os tokens do
     bloco do tema escuro (o `:root` base é o escuro: token esquecido não fica sem valor, fica com
     a cor do escuro no meio do tema novo), mais as regras fora do bloco de tokens que dependem do
     tema — as de superfície do menu, por exemplo;
  2. `TEMAS` em `theme.js` — a opção aparece no dropdown sozinha;
  3. `TEMAS_VALIDOS` em `api/funcoes/auth_utils.py`. A coluna `TP_TEMA` é `VARCHAR(30)` sem CHECK
     de valores, de propósito: tema novo não precisa de migração.

## Componentes de UX compartilhados

### Visão geral

Seis módulos pequenos (`web/js/utils/toast.js`, `api.js`, `carregando.js`, `estados.js`, `estado_url.js`,
`icones.js` + `mapa_telas.js`) carregam em toda página — ver a ordem em
["Toda página protegida carrega, nesta ordem..."](#interface-web--front-sem-build-menu-busca-e-diálogos)
acima. Cada um segue a mesma convenção de `dialogo.js`: IIFE, injeta o próprio DOM quando precisa,
expõe um único global, zero dependência de build. Cobrem três lacunas que existiam em todas as
telas antes deles: **feedback** (sucesso/erro silenciosos, chamadas de API sem tratamento de
erro, nenhum indicador de carregamento), **navegação** (mapa do site duplicado em três arquivos)
e **acessibilidade** (foco invisível, sem skip-link). Nenhuma tela de listagem foi migrada para
usá-los na lógica própria — isso é trabalho futuro (ver nota no fim desta seção); o que já vale
para toda página hoje é a rede de segurança de `api.js` e a acessibilidade do menu.

### Toast (`web/js/utils/toast.js`)

Notificação rápida e não bloqueante — o que faltava entre "silencioso" e o modal bloqueante de
`Dialogo.alerta`. Injeta uma pilha (`.toast-pilha`) fixada no canto inferior direito.

| Função | Duração padrão | Uso típico |
|---|---|---|
| `Toast.sucesso(msg)` | ~5 s, some sozinho | "Importação concluída" |
| `Toast.erro(msg)` | fica até fechar | "Não foi possível salvar" |
| `Toast.aviso(msg)` | fica até fechar | "Sessão expira em breve" |
| `Toast.info(msg)` | ~5 s, some sozinho | mensagens neutras |
| `Toast.mostrar(opts)` | — | forma genérica: `{ tipo, mensagem, duracao, acao: { rotulo, aoClicar } }` |

Todas devolvem `{ fechar() }`. O timer de auto-fechar pausa com o mouse ou o foco em cima do
toast; `Esc` fecha o mais recente. Cada toast é sua própria *live region* (`role="status"`/
`aria-live="polite"` para sucesso/info, `role="alert"`/`aria-live="assertive"` para erro/aviso) —
não a pilha inteira, para não empilhar dois contêineres `position: fixed` na mesma posição da
tela.

### Camada de API e rede de segurança (`web/js/utils/api.js`)

`Api.get(url, params)` / `Api.post(url, dados)` / `Api.enviar(opts)` devolvem o mesmo Deferred
que `$.getJSON`/`$.ajax` devolvem — dá para trocar uma chamada existente sem mudar o resto do
código, ganhando de graça `Api.mensagemErro(xhr)` (extrai `xhr.responseJSON.detail` com
fallback), hoje reimplementado em dezenas de arquivos.

A peça que vale para o projeto inteiro, sem precisar trocar nenhuma chamada existente, é o
listener global `$(document).ajaxError()`: das centenas de chamadas de API espalhadas pelas
páginas, boa parte não tem `.fail()`, então uma queda de rede ou um erro 500 não avisava o
usuário — a tela só não carregava. O listener cobre isso:

| Situação | Reação |
|---|---|
| `status === 0` (rede caiu / servidor fora do ar) | `Toast.erro("Sem conexão com o servidor...")` |
| `status >= 500` | `Toast.erro(Api.mensagemErro(xhr, ...))` |
| `status === 401` | `Toast.aviso("Sua sessão expirou...")` + redireciona ao login após ~1,2 s |
| `GET /api/auth/eu` com `401` | **nada** — é a própria checagem de sessão de `auth.js`, chamada em toda página mesmo sem nenhum login ainda; ali um 401 não é "sessão expirou", `auth.js` já redireciona sozinho sem aviso |
| `4xx` (exceto 401) | nada — inclui o `403` esperado em `servidor.html` e nas rotas de admin, que a própria página já trata mostrando "sem permissão" |
| `abort` | nada (busca cancelada por uma nova, por exemplo) |

Uma chamada que já trata o próprio erro pode passar `erroSilencioso: true` (só via `Api.enviar`)
para pular o toast global e não duplicar a mensagem.

### Indicadores de carregamento (`web/js/utils/carregando.js`)

Formaliza o padrão que já existia improvisado (o botão "Importar" de `ingestao.html`, que troca o
texto por um spinner) e cobre o que não tinha nada — usa a classe `.spinner` que já existia em
`style.css`.

| Função | Efeito |
|---|---|
| `Carregando.botao($btn, "Salvando…")` / `Carregando.restaurar($btn)` | desabilita + troca por spinner e texto; guarda o HTML original em `data-*` e devolve ao restaurar |
| `Carregando.emTabela($tbody, nColunas)` | 6 linhas de skeleton pulsante no lugar de uma tabela em branco |
| `Carregando.emBloco($el)` / `Carregando.limpar($el)` | overlay com spinner grande sobre um card/mapa/grafo |

### Estados vazio/erro (`web/js/utils/estados.js`)

Substitui o `<p class="empty-hint">` solto (presente em dezenas de HTML) e o padrão de
sobrescrever esse mesmo texto quando a chamada falha.

- `Estados.vazio($alvo, { titulo, dica, filtrado, acao })` — `filtrado: true` troca a mensagem
  para "Nenhum resultado para este filtro" e permite oferecer um botão (ex.: "Limpar filtros").
  A distinção importa numa ferramenta forense: "não há esse dado no aparelho" e "nenhum
  resultado bate com este filtro" são conclusões diferentes para um investigador.
- `Estados.erro($alvo, { mensagem, aoTentarDeNovo })` — com botão "Tentar de novo" quando fornecido.
- `Estados.limpar($alvo)` — tira o estado antes de renderizar dado de verdade.

### Tabela paginada (`web/js/utils/tabela_paginada.js`)

Encapsula o padrão que estava copiado em ~20 pares HTML/JS de telas de extração (busca a página
atual, desenha cabeçalho/linhas, total, vazio/erro/skeleton, paginação). A tela continua dona da
barra de filtros (`.arq-filtros`) e, quando existir, do resumo de KPIs/gráficos acima da tabela —
`TabelaPaginada` só cuida do que é idêntico em todas: da tabela pra baixo.

```js
var tabela = TabelaPaginada.criar({
  alvo: "#tabela",                       // <div> vazio onde a tabela/paginação são injetadas
  url: "/api/investigacao/inqueritos/" + idInquerito + "/contatos",
  params: function () { return { extracao: idExtracao, busca: $busca.val().trim() }; },
  colunas: [
    { rotulo: "Nome", render: function (r) { return r.no_contato || "—"; } },
    { rotulo: "Entidade", exportavel: false, render: function (r) { return EntidadesUI.botao({...}); } }
  ],
  exportar: { nome: "contatos", linha: function (r) { return [r.no_contato || "", ...]; } }
});
$filtroTipo.on("change", function () { tabela.recarregar(); });  // filtro específico da tela
```

Ganhos embutidos em toda tela que usa o módulo: cabeçalho como `<th scope="col">` (era `<td>`, sem
semântica de tabela pra leitor de tela — `css/style.css` ganhou as regras `.grupo-tabela th`
correspondentes), skeleton no lugar de tabela em branco durante o carregamento, `Estados.vazio`/
`Estados.erro` no lugar do `<p class="empty-hint">` solto, `role="status"` no contador de total,
navegação por `←`/`→` quando o foco não está em campo de texto/select, e filtros + página
refletidos na URL via `window.EstadoUrl` a cada carga (ver seção abaixo).

**Migradas**: `contatos`, `ligacoes`, `notificacoes`, `aplicativos`, `emails`, `agenda`,
`conexoes`, `atividades`, `acessos`, `acessos_agregados`, `dispositivos_conta`, `navegacao`,
`localizacoes` — 13 das ~20 telas de extração inicialmente cotadas.

**Ficaram de fora** (não seguiam de fato o padrão paginado — migrar forçaria mudança de
comportamento, não só refatoração):
- `wifi.html`, `notas.html`, `chaves_pix.html` — buscam a lista inteira de uma vez e filtram no
  cliente (conjunto pequeno por extração), sem paginação real nem cabeçalho dinâmico.
- `timeline.html` — eventos agrupados por dia em cartões (`.tl-dia`/`.tl-item`), não é uma
  `<table>`.
- `contas_financeiras.html` — busca tudo de uma vez e filtra/pagina no cliente (a API devolve um
  array simples, não `{ itens, total }`). A barra de filtros da aba "Contas" é da própria tela
  (categoria, instituição, titular, fonte, moeda, saldo, situação no aparelho e "só as marcadas"),
  com os selects montados a partir dos valores que a extração tem e o estado espelhado na URL via
  `EstadoUrl`; as demais abas usam os filtros do componente compartilhado (ver
  [extração_ufdr.md](extração_ufdr.md)). Não há filtro por período aqui: o modelo de conta do
  UFDR não tem data nenhuma — datas existem nas transferências e nos cartões.
- `perfil_conta.html` — um cartão por perfil de conta, não é uma listagem tabular.
- `arquivos.html` — já estava fora de escopo desde o plano original (galeria, trilha de pastas e
  visões múltiplas — o caso mais complexo do projeto).

Essas telas mantêm sua implementação própria; forçá-las no molde da tabela paginada trocaria
comportamento real (por ex. inventar paginação onde a tela sempre mostrou tudo de uma vez) em
troca de uma uniformidade que não existia de fato no código original.

### Estado de filtro na URL (`web/js/utils/estado_url.js`)

`EstadoUrl.salvar({ busca, tipo, pagina })` grava pares chave-valor na URL atual via
`history.replaceState` (sem recarregar nem mexer no histórico do navegador); valor `""`/`null`/
`undefined`/`false` remove a chave em vez de gravar vazio. `EstadoUrl.ler(["busca", "tipo"])` lê
de volta, para restaurar o estado no load da página. Só mexe nas chaves passadas — `?inquerito=`/
`?extracao=`/etc. já presentes na URL não são tocados.

`TabelaPaginada` (acima) é o consumidor: a cada carga persiste os filtros da tela + a página atual
na URL, e restaura a página no load — os filtros específicos de cada tela (selects, datas)
continuam por conta da própria tela, que já lê o valor deles do DOM.

### Ícones e mapa de telas compartilhados (`web/js/utils/icones.js` + `web/js/utils/mapa_telas.js`)

Antes, os mesmos ~20 ícones SVG (Início, Investigação, WhatsApp, Contatos…) e a lista "quais
telas existem e para onde levam" estavam duplicados em até três arquivos — `sidebar.js`
(`NAV_ITEMS`, a barra de navegação), `navegacao_inquerito.js` (`ITENS_INQUERITO`/`ITENS_EXTRACAO`, o
menu "Navegar" dentro de um inquérito) e `home.js` (`TELAS`, a busca global) — mantidos em
sincronia manualmente.

- `Icones.html("investigacao")` devolve a string SVG; `Icones.html("investigacao", "minha-classe")`
  injeta `class="minha-classe"` na tag. `Icones.MAPA` expõe o dicionário cru.
- `MapaTelas` reúne `NAV_ITEMS`/`PAGINAS_MENU_COMPLETO` (barra de navegação), `ITENS_INQUERITO`/
  `ITENS_EXTRACAO`/`ROTULO_PAGINA` (menu "Navegar" e trilha) e `TELAS_BUSCA` (busca global) — cada
  item leva a chave do ícone (`icone: "investigacao"`), não mais o SVG já renderizado.

`sidebar.js`, `home.js` e `navegacao_inquerito.js` só leem daqui agora; uma tela nova só precisa
ser adicionada uma vez, em `mapa_telas.js`.

### Acessibilidade: foco visível e skip-link

- `css/style.css` tinha só 3 seletores de `:focus` em todo o arquivo (campo de formulário e busca
  de investigação) — quem navega por teclado não via onde estava em nenhum botão, link ou item de
  menu. Agora `a:focus-visible`, `button:focus-visible`, `.btn:focus-visible`,
  `.nav-item:focus-visible`, `input/select/textarea:focus-visible` e `[tabindex]:focus-visible`
  aplicam `box-shadow: var(--focus-ring)` — o token já existia e quase não era usado.
  `:focus-visible` só acende no que o navegador entende como navegação por teclado, não em todo
  clique de mouse.
- **Skip-link**: `sidebar.js` injeta `<a href="#conteudo-principal" class="skip-link">Pular para
  o conteúdo</a>` como primeiro elemento focável do `body` (fica fora da tela até receber foco) e
  marca `.content` com `id="conteudo-principal"` + `role="main"` — zero edição de HTML por
  página.
- `.sr-only` (texto só para leitor de tela) e `@media (prefers-reduced-motion: reduce)`
  (desliga transições/animação do spinner e do skeleton) completam a base, em `style.css`.
- Tokens novos usados só pelos componentes desta seção, ao lado dos de cor em `style.css`:
  espaçamento (`--esp-1` a `--esp-6`), raio (`--raio-sm/md/lg`), tipografia (`--fs-xs` a `--fs-lg`)
  e camadas (`--z-modal: 100`, `--z-toast: 300`). O restante do CSS (1.982 linhas antes desta
  lista) continua com valores soltos em px — não é objetivo reescrevê-lo.

### Bloco de extração/importação: cabeçalho e caixas de acesso

Cada extração e cada importação da tela de Investigação vira um `.extracao-bloco` montado em
`web/js/investigacao.js` (`renderExtracaoBloco`, `renderRifBloco`, `renderSimbaBloco`,
`renderIplBloco`, `renderIrpfBloco`, `renderIpedBloco`, `renderDocumentoBloco`). Os sete
compartilham três helpers, no lugar de cada um montar o próprio cabeçalho:

| Helper | Papel |
|---|---|
| `montarCabecalho($bloco, opts)` | `.extracao-cab` com `.extracao-avatar` (ícone), `.extracao-modelo` (título em destaque) e `.extracao-arquivo` (subtítulo). Devolve `{ $badges, $acoes }`: badges na mesma linha, à direita; botões de gestão (Hash, apagar…) na linha de baixo. |
| `criarGrupoAcoes($destino, titulo)` | uma caixa rotulada (`.acoes-grupo`) com um `.investigacao-actions` dentro. |
| `podarVaziosBloco($bloco)` | some com caixa e fileira que ficaram sem botão (importação em andamento, usuário sem permissão de gestão). |

A lista de blocos de cada aba (`.inquerito-categoria-wrap`) é uma grade de **duas colunas fixas**
— empilhados, os cartões de um inquérito com vários aparelhos viravam uma rolagem longa. Abaixo de
1180px cai para uma coluna. Os blocos que chegam por AJAX ficam dentro de um `<div class="blocos-async">`
que guarda o lugar na ordem certa; ele é `display: contents`, então não gasta uma célula da grade.

O celular usa **duas** caixas dentro de `.acoes-grupos`: *Principais* (WhatsApp, Notas,
Localizações, Timeline, Contas bancárias, Chaves PIX, Wi-Fi, Identidade do aparelho) e *Demais*
(o resto, inclusive os botões de mensageiro que chegam depois do render). São umas vinte telas
por aparelho, e em fluxo único o que se abre em toda análise ficava com o mesmo peso visual do que
se abre uma vez por caso. RIF, SIMBA, IPL, IRPF, Nuvem IPED e documento avulso têm poucas ações e
usam **uma** caixa, *Ações*, ocupando a largura inteira.

O ícone da plataforma (`sistemaDoAparelho`) sai por heurística do fabricante/modelo — não há campo
de sistema operacional em `inq_tb02_extracoes`. Sem marca reconhecível, cai no ícone genérico de
celular em vez de chutar Android.

## Painel Servidor — saúde da máquina

### Visão geral

O painel **Servidor** (`web/servidor.html`) é uma tela exclusiva de administradores, à parte dos
dados de investigação: mostra o estado da máquina que hospeda o sistema — uso de CPU (% e número
de núcleos), uso de memória (% e usado/total) e uso de disco de dois pontos de vista (`/app/data`,
onde ficam `.ufdr`, as pastas-caso do IPED e os documentos, e `/`, o filesystem do container da API) — atualizado
automaticamente a cada 5 segundos. Quem não é admin não vê os gráficos: a página mostra apenas o
aviso "Esta página é só para administradores."

### Fluxo de uso

1. Com uma sessão de administrador, entre pela barra do topo em **Administração → Servidor**
   (o link está em `NAV_ITEMS` de `web/js/utils/mapa_telas.js`). Além dos KPIs de máquina, a
   tela traz o cartão **Versão e atualização** — ver `atualizacao.md`.
2. A página carrega e dispara a primeira leitura; a partir daí recarrega sozinha a cada 5 s,
   mostrando o horário da última atualização ("Atualizado às HH:MM:SS").
3. Se a sessão não for de admin (ou expirou), a API responde `403` e a tela troca os KPIs pelo
   aviso de tela restrita — o administrador deslogado cai no guarda de `js/utils/auth.js`, que
   redireciona ao login.

### Como funciona

#### Frontend (`web/servidor.html` + `web/js/servidor.js`)

- `servidor.html` segue o layout padrão (`div.app` → `div.main` → topbar de título), carrega
  `css/servidor.css` além do `style.css` e, antes do CSS, um script inline que aplica o tema
  salvo em `localStorage["analyser-theme"]` (mesmo padrão das demais páginas, ver acima). Os
  scripts carregados seguem a ordem padrão de toda página protegida (jQuery, `utils.js`, os
  componentes compartilhados, `theme.js`, `sidebar.js`, `auth.js`) e por fim `servidor.js`.
- O HTML tem dois estados controlados pela classe `hidden`:
  - `#admin-card` — o painel (`kpi-tiles` com CPU e memória, o bloco de disco `#kpi-discos` e o
    rodapé `#kpi-atualizado`);
  - `#sem-permissao-hint` — o aviso de tela restrita.
- `servidor.js`:
  - chama `$.getJSON("/api/sistema/metricas")`; no `done`, revela o card e esconde o aviso;
    no `fail`, faz o inverso (403 = não administrador);
  - `#kpi-cpu` recebe `cpu_percent` com 1 casa e cor por faixa, e `#kpi-cpu-label` monta
    "uso de CPU · N núcleos" (plural correto);
  - `#kpi-memoria` idem, com o rótulo em bytes formatados (`formatarBytes`: B/KB/MB/GB/TB,
    ex. "18,1 GB / 31,3 GB de memória");
  - cada disco vira uma barra (`.kpi-barra-*`) com rótulo, percentual colorido, preenchimento e
    "usado / total";
  - `corPercentual(p)`: `>= 90` → `var(--danger)`, `>= 75` → `var(--warning)`, abaixo →
    `var(--success)`;
  - agenda a próxima leitura com `setTimeout(carregarMetricas, INTERVALO_MS)` no fim do `done`
    (em vez de `setInterval`), com `INTERVALO_MS = 5000`.

#### Backend (`api/rotas/sistema.py`)

- `GET /api/health` — router público; responde `{"ok": true}` e é o endpoint usado pelo
  healthcheck do container da API no `docker-compose.yml`
  (`urllib.request.urlopen('http://localhost:3000/api/health')`, a cada 10 s). Não existe rota
  `/api/sistema/health` — o health check é o `/api/health`.
- `GET /api/sistema/metricas` — fica no `router_admin` (dependência `exigir_admin`), então
  qualquer chamada sem sessão de admin devolve `403` antes de medir qualquer coisa. Mede tudo
  **de dentro do container da API** com `psutil` e `shutil`:
  - `cpu_percent = psutil.cpu_percent(interval=0.3)` — espera 300 ms para uma leitura
    representativa; `cpu_cores = psutil.cpu_count()`;
  - `memoria = psutil.virtual_memory()` → `total`, `usado = total - available`, `percentual`;
  - `discos` — dois itens gerados por `_uso_disco(caminho)` (`shutil.disk_usage` → `total`,
    `usado`, `percentual` com 1 casa): `{"rotulo": "Dados (evidências)", "caminho": "/app/data"}`
    (o volume montado de `data/` — o que mais importa para planejamento de capacidade) e
    `{"rotulo": "Sistema (container)", "caminho": "/"}` (filesystem do container, útil para
    notar disco cheio por logs/cache).
  - Resposta JSON completa:

    | Campo | Tipo | Origem |
    |---|---|---|
    | `cpu_percent` | float | `psutil.cpu_percent(interval=0.3)` |
    | `cpu_cores` | int | `psutil.cpu_count()` (fallback 1) |
    | `memoria.total` / `memoria.usado` / `memoria.percentual` | int / int / float | `psutil.virtual_memory()`; `usado = total - available` |
    | `discos[]` | lista | um item por caminho medido (`rotulo`, `caminho`, `total`, `usado`, `percentual`) via `shutil.disk_usage` |

Como a leitura é o que o **container** da API enxerga, `cpu_percent` reflete o uso global do
host (os núcleos do container coincidem com os da máquina) e a memória aparece contida pelos
limites que o container tiver — é a visão correta para decidir se o servidor está perto do
teto de capacidade, não para diagnóstico do host em si.

## Documentação de código — os `.md` do repositório dentro do sistema

### Visão geral

A tela **Documentação de código** (`web/documentacao.html`) é a própria pasta `docs/` — este
documento inclusive — lida dentro do sistema: lista os arquivos `.md`, renderiza o markdown e
permite buscar por título ou por conteúdo. É exclusiva de administradores, como o painel Servidor,
e é **somente leitura**: a documentação continua sendo editada no repositório e versionada no git.
O que a tela resolve é o acesso — antes dela, só quem tinha o código aberto no editor conseguia
ler os documentos que explicam como cada feature funciona.

### Fluxo de uso

1. Com sessão de administrador, entre em **Administração → Documentação de código**.
2. A lista lateral abre agrupada pelas seções de `docs/README.md` (Plataforma, Autenticação,
   Extração forense…) e já mostra o `README.md`, que é o índice da documentação.
3. Clicar num documento o abre à direita, com o sumário "Nesta página" (títulos de nível 2 e 3)
   e a URL virando `?doc=arquivo.md` — link compartilhável e botão Voltar funcionando.
4. Os links entre documentos que os textos usam (`[extracao_iped.md](extracao_iped.md)`) trocam o
   documento na própria tela, sem recarregar a página.
5. A busca cobre nome, título **e conteúdo**; quando o acerto foi no meio do texto, o resultado
   mostra as linhas que casaram, para não devolver um nome de arquivo sem explicação.

### Como funciona

#### Renderizador de markdown (`web/js/utils/markdown.js`)

`Markdown.renderizar(texto, { linkDoc })` devolve `{ html, titulos }` — o HTML e a lista de
títulos que vira o sumário. É um módulo à parte do markdown do chat (`web/js/ia.js`), que é
mínimo de propósito: lá todo título vira o mesmo `.chat-md-titulo` sem nível, lista aninhada vira
lista plana e citação não existe — o bastante para uma bolha de conversa, insuficiente para um
documento de 500 linhas com hierarquia de seções. Cobre o que a pasta `docs/` usa: títulos de 1 a
6 (com âncora `#secao` gerada sem acento), parágrafo com linhas quebradas por limite de coluna,
ênfase, código inline e em bloco, listas numeradas/não numeradas e aninhadas, citação e tabela.

Duas decisões que não são óbvias:

- **ênfase só com asterisco.** `_` é caractere de nome em quase todo identificador citado nos
  documentos (`nm_arquivo`, `_processar_extracao`); tratá-lo como itálico transformaria nome de
  função em texto torto.
- **todo o texto é escapado antes de virar tag**, e trechos entre crases saem da frente antes do
  escape (senão `**` dentro de um exemplo de código viraria negrito). `href` aceita apenas
  http(s), âncora e caminho relativo — os `.md` são do próprio repositório, mas o módulo é
  genérico e não depende disso.

Arquivo salvo com BOM (`mesa_inquerito.md` é um) cola o BOM no primeiro `#`: o título viraria
parágrafo. O BOM é removido nas duas pontas — no `renderizar` e na leitura da API
(`utf-8-sig`).

#### Frontend (`web/documentacao.html` + `web/js/documentacao.js` + `web/css/documentacao.css`)

- Layout de duas colunas: painel da lista (grudado no topo enquanto o texto rola, `position:
  sticky`) e o documento. Abaixo de 1000px vira uma coluna só, e tabela larga rola dentro do
  próprio bloco — a página nunca rola na horizontal.
- Estado na URL via `EstadoUrl` (`?doc=`/`?busca=`) e `history.pushState` a cada documento aberto;
  o `popstate` reabre o documento anterior. `scroll-margin-top` nos títulos compensa a topbar
  sticky ao pular por uma âncora.
- Mesmo par de estados do painel Servidor: `403` da API esconde o layout e mostra o aviso "Esta
  página é só para administradores.".

#### Backend (`api/rotas/documentacao.py`)

- Router inteiro sob `exigir_admin`. Duas rotas de leitura, nenhuma de escrita:
  - `GET /api/documentacao?busca=` — lista `nome`, `titulo` (primeiro `# ` do arquivo), `grupo`,
    `bytes`, `linhas`, `atualizado_em` e, na busca, os `trechos` que casaram. A busca normaliza
    caixa e acento nos dois lados (procurar "extracao ufdr" acha `extração_ufdr.md`) e lê os
    arquivos a cada chamada — são algumas dezenas de arquivos pequenos, índice seria complexidade
    sem ganho perceptível.
  - `GET /api/documentacao/{nome}` — o markdown cru; quem renderiza é a tela.
- **Agrupamento vem do próprio `docs/README.md`**: a rota lê os títulos `##`/`###` e os links do
  índice para saber em que seção cada arquivo está e em que ordem listá-los. Se o README mudar de
  formato, a listagem cai na ordem alfabética em vez de quebrar.
- O nome pedido passa por uma regex restrita (`^[\w .\-]+\.md$`, sem `..`) **e** pela conferência
  de que o caminho resolvido tem `docs/` como pasta-mãe — nome inválido é `400`, fora da pasta é
  `404`.
- A pasta entra no container por **bind mount** (`./docs:/app/docs:ro` no `docker-compose.yml`),
  não por `COPY` no Dockerfile: editar um `.md` aparece na hora na tela, sem rebuild da imagem da
  API. Fora do Docker, cai no `docs/` da raiz do repositório; `DOCS_DIR` no `.env` sobrepõe.

## Modelo de dados

- O **tema** fica em `SEGURANCA.USR_TB01_USUARIOS` (coluna `tp_tema`, ver `autenticacao.md`);
  a busca global não guarda estado no banco — carrega a lista de inquéritos em memória a partir de
  `GET /api/investigacao/inqueritos`.
- O **painel Servidor** não persiste nada: as métricas são lidas em tempo real a cada requisição e
  não são gravadas no banco; o painel não cria nem altera tabelas.
- A **Documentação de código** também não toca o banco: a fonte são os arquivos `.md` da pasta
  `docs/`, lidos do disco a cada chamada.

## Configuração

- Nenhuma variável de `.env` é lida pelo front: tudo passa por rotas da API (sessão, tema,
  dados). A única exigência de infra é o nginx servindo `web/` e o proxy de `/api/*` (ver
  `infraestrutura.md`) — os caminhos das chamadas são relativos ao host (`/api/...`).
- O painel Servidor não tem variável própria: depende apenas de (a) usuário com `IN_ADMIN`
  (não há flag de configuração para liberar a tela) e (b) do container da API enxergar os
  filesystems medidos — `data/` montado em `/app/data` e o próprio `/`.

Como ler o painel na prática:

- **Disco "Dados (evidências)"** é o primeiro a olhar: é onde crescem `.ufdr`, as pastas-caso do IPED, RIFs e
  extratos; quando enche, a ingestão de novos arquivos e a leitura de anexos passam a falhar.
- **Disco "Sistema (container)"** cobre logs do uvicorn e o cache do Hugging Face
  (`/app/.cache/huggingface`, volume `whisper_cache`) — cresce com o uso de transcrição.
- **CPU/memória** conversam com o dimensionamento dos jobs pesados: o painel mostra os núcleos
  que `CLIP_NUM_THREADS` e `JOBS_PESADOS_LIMITE` do `.env` devem respeitar
  (`JOBS_PESADOS_LIMITE * CLIP_NUM_THREADS` não deve passar dos núcleos disponíveis — ver
  `infraestrutura.md`).

## Referências

- Front: `web/index.html`, `web/js/home.js`, `web/js/utils/sidebar.js`, `web/js/utils/dialogo.js`,
  `web/js/utils/auth.js`, `web/js/utils/theme.js`, `web/js/utils/utils.js`, `web/js/utils/exportar_excel.js`
  (exportação xlsx no navegador), `web/css/style.css`, `web/vendor/`.
- Componentes de UX compartilhados: `web/js/utils/toast.js`, `web/js/utils/api.js`, `web/js/utils/carregando.js`,
  `web/js/utils/estados.js`, `web/js/utils/tabela_paginada.js`, `web/js/utils/estado_url.js`,
  `web/js/utils/icones.js`, `web/js/utils/mapa_telas.js`; `web/js/utils/avisos_ia.js` também
  carrega em toda página protegida, mas é assunto de [avisos_ia.md](avisos_ia.md).
- Painel: `web/servidor.html`, `web/js/servidor.js`, `web/css/servidor.css`.
- Documentação de código: `web/documentacao.html`, `web/js/documentacao.js`,
  `web/css/documentacao.css`, `web/js/utils/markdown.js`, `api/rotas/documentacao.py`.
- Backend: `api/rotas/auth.py` (`/api/auth/eu`, `/api/auth/tema`), `api/rotas/investigacao.py`
  (`/api/investigacao/inqueritos`), `api/rotas/sistema.py` (`/api/health`,
  `/api/sistema/metricas`), `api/funcoes/auth_utils.py` (`exigir_admin`),
  `docker-compose.yml` (healthcheck do serviço `api`).
- `infraestrutura.md` — nginx (no-cache), volumes `data/` e redes, rebuild da API;
  `autenticacao.md` — sessão, tema e guarda de sessão.