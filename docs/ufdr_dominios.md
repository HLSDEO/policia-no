# Domínios do UFDR (auditoria de aproveitamento)

Este documento cobre os domínios do UFDR que o parser descartava inteiros e passaram a ser
importados depois da **auditoria de aproveitamento**, o registro genérico que os consulta
(`ufdr_dominios_utils.py` + `ufdr_dominio.js`) e as telas/abas que eles alimentam.

## Visão geral

A auditoria de oito extratos reais (quatro iPhones, três Samsung e um Motorola) varreu o
`report.xml` inteiro e comparou cada `<field>`/`<modelField>` com o que o parser lia. O resultado:
**861 mil registros de topo** caíam no `logger.debug("Model type desconhecido")` — dado que estava
no arquivo, tinha sido pago na extração e nunca chegava à tela.

Os tipos que valem prova viraram tabelas (`db/init/17_aparelho_redes.sql`): onde o aparelho
esteve (antenas de telefonia, trajetos), o que a pessoa procurou e publicou, o que enviou para a
nuvem, dinheiro que passou pelo aparelho e a identidade do próprio aparelho. Ficaram **de fora, por
decisão de escopo**: `LogEntry` (log do sistema iOS), `DictionaryWord` (dicionário do teclado) e
`RemoteServiceSession` — muito volume, pouco valor probatório. `DeviceEvent` e `ActivitySensorData`
chegaram a ser importados e saíram depois (`18_credenciais_financeiro.sql`) pelo mesmo motivo:
eram os dois de maior volume e menor uso na análise.

Do ponto de vista da tela, **todos são a mesma coisa**: uma lista paginada de uma tabela filha da
extração, sem tabela-neta e sem chave natural, com busca livre e o filtro "só o que foi apagado".
Por isso não existem 23 rotas nem 23 páginas quase idênticas — existe **um registro de domínios** no
back-end, **uma rota** e **um componente** no front. Uma tela nova é HTML sem JS próprio; uma aba
nova numa tela existente é uma linha.

Serve ao investigador que precisa responder "o que a pessoa estava fazendo às 22h14", "o que ela
pesquisou antes do fato", "que aparelho é este" e "que dinheiro passou por aqui" — perguntas que o
UFDR sempre respondeu e o sistema não mostrava.

## Fluxo de uso

1. Extrair o `.ufdr` normalmente (ver [extração_ufdr.md](extração_ufdr.md)) — os domínios são
   gravados no **próprio passe de extração**, sem etapa extra e sem custo de reprocessamento.
2. No card do inquérito, abrir a tela do aparelho desejado. As telas próprias destes domínios são:

   | Tela | Domínios | Responde |
   |---|---|---|
   | **Redes sociais e buscas** (`social_buscas.html`) | atividade social, buscas | o que a pessoa publicou (post, comentário, check-in) e o que digitou nas buscas do navegador, do e-mail e dos aplicativos |
   | **Calendário** (`calendario.html`) | calendário | compromissos da agenda do aparelho, com período, local, convidados e recorrência |
    | **Dispositivos reconhecidos** (`dispositivos_reconhecidos.html`) | dispositivos reconhecidos | o que já pareou/foi visto pelo aparelho (Bluetooth, acessórios), com modelo, série e MAC; filtros por tipo e marcadores "só com MAC", "só com nº de série" e "só unidos de várias fontes" |
   | **Identidade do aparelho** (`identidade_aparelho.html`) | info do aparelho, donos, contas de usuário, antenas | de quem é este aparelho e onde ele esteve conectado |

3. As demais entram como **aba** em telas que já existiam: Navegação (cookies, favoritos,
   downloads, preenchimento automático, uso de rede), Arquivos (envios para a nuvem, gravações),
   Ligações (mensagens de voz), Contas bancárias (transferências, cartões de crédito, cartões da
   carteira), Localizações (trajetos) e Wi-Fi (conexões).

   O par **uso de apps / uso por aplicativo** é o caso à parte: não tem tela nem aba própria, e sim
   um **modal de uso por aplicativo** aberto a partir de uma linha da tela **Aplicativos instalados**
   (`js/aplicativos_uso.js`). O modal mostra KPIs do app escolhido — tempo em primeiro e segundo
   plano, aberturas, ativações, última abertura — e, embaixo, o log de eventos preso àquele pacote
   pelo filtro do servidor. Quando o aparelho não emite o resumo de tempo (vários iPhones), a tela
   diz isso e fica só com o log.
4. Em qualquer um: busca livre, **"só o que foi apagado"**, e — nos domínios que os declaram —
   período, faixa do dia (madrugada/manhã/tarde/noite), selects de categoria, faixa de valor e
   marcadores liga/desliga ("só com anexo", "só com coordenada"). Os selects só oferecem valor que
   **existe naquele aparelho**, com a contagem de cada um ao lado.
5. Registro relevante aceita **📌 (entidade da mesa)**, com a contagem de comentários já feitos na
   coluna "Entidade" — ver [mesa_inquerito.md](mesa_inquerito.md). O componente **não** oferece
   "marcar para o relatório" nem exportação para Excel: essas duas continuam só nas telas com JS
   próprio.

Aba que não aparece quer dizer que a extração não trouxe aquele domínio. **Contagem `NULL` é
diferente de zero**: significa "extração carregada antes deste domínio existir", e a tela não promete
que o aparelho não tem o dado — para isso, ver o backfill em [extração_ufdr.md](extração_ufdr.md).

## Como funciona

### O registro de domínios (`api/funcoes/ufdr_dominios_utils.py`)

Cada domínio é uma instância de `_Dominio` no dicionário `DOMINIOS`, declarando tabela, colunas
devolvidas, ordenação, onde a busca livre procura e quais filtros extras a tela ganha:

| Campo | Para que serve |
|---|---|
| `tabela`, `colunas`, `ordem` | a listagem em si |
| `fonte` | `SELECT` mesclado no lugar da tabela crua — hoje só `dispositivos-reconhecidos`, que junta no mesmo aparelho os registros repetidos que o iPhone grava por aplicativo (KnowledgeC, Biome, Find My), unidos por MAC (ou nome+tipo); `qt_registros` conta quantos registros a linha representa |
| `busca` | colunas varridas pelo termo livre (`ILIKE`) |
| `rotulo` | nome no singular, usado nos textos da tela ("nenhuma **conexão** encontrada") |
| `filtro` | condição SQL **fixa**, sempre aplicada — o trajeto sem nenhuma coordenada não diz de onde para onde, e a conexão sem dispositivo nem MAC não identifica nada |
| `pacote` | coluna do pacote do app, que permite abrir a lista já filtrada por um aplicativo |
| `data` / `hora` | filtro por período; com `hora`, também a faixa do dia (hora de Brasília) |
| `facetas` / `derivados` | colunas de valor repetido oferecidas como select; `derivados` cobre a faceta que não é coluna (o *host* do endereço, que agrupa publicações por site) |
| `marcadores` | recortes liga/desliga (`{chave: condição SQL}`) |
| `valor` | faixa numérica mínimo/máximo (valor da transferência) |
| `anexos` | coluna com nomes de anexo; declarada, cada item vem com o anexo já resolvido para o arquivo do aparelho e a tela abre o binário |

Três funções públicas:

- **`listar_dominio(...)`** — a listagem paginada, com todos os filtros acima. O `id_inquerito`
  entra sempre no `WHERE` (via `JOIN` com `INQ_TB02_EXTRACOES`), e `?extracao=ID` restringe a um
  aparelho.
- **`facetas_dominio(...)`** — os valores que **existem de fato** em cada coluna de filtro, com a
  contagem, mais os extremos de período e de valor e a contagem de cada marcador. Nada de lista fixa
  de "tipos possíveis": o que o Cellebrite grava em `TransferType` varia por aplicativo. A tela
  esconde o controle que daria lista vazia.
- **`contagens_dominios(id_extracao)`** — quantos registros de cada domínio esta extração tem, para
  a tela decidir quais abas exibir sem buscar a lista inteira.

Três decisões que valem registrar:

- **Lista branca é o que impede injeção.** O nome da faceta e a chave do marcador chegam pela query
  string; só valem se estiverem declarados em `cfg.facetas`/`cfg.marcadores`. Nome fora da lista é
  ignorado, não vira identificador SQL.
- **`ate` inclui o dia inteiro** (`< dia seguinte`): "até 10/03" tem que pegar o que aconteceu às
  23h do dia 10. Data malformada (o `<input type="date">` chega pela metade enquanto se digita) é
  ignorada em vez de virar erro 500, e o valor aceita a forma brasileira (`1.234,56`), que é como o
  investigador digita quando copia da tela.
- **Domínio com `filtro` fixo não usa o contador de `INQ_TB02`.** Aquele número conta a tabela
  inteira, e a aba prometeria linhas que a listagem não mostra; esses contam ao vivo, com o filtro
  aplicado. É barato porque a tabela é indexada por extração.

As faixas do dia e a conversão para hora de Brasília são reaproveitadas de
`investigacao_utils` (`_PERIODOS_ARQUIVO`, `_hora_local_sql`) — as mesmas das telas de Arquivos,
Ligações e Localizações, para o filtro querer dizer a mesma coisa em toda listagem.

### As rotas (`api/rotas/investigacao.py`)

| Rota | Devolve |
|---|---|
| `GET /api/investigacao/inqueritos/{id_inquerito}/ufdr/{dominio}` | a listagem paginada (`extracao`, `busca`, `apagados`, `pagina`, `limite`, `pacote`, `de`/`ate`, `faixa_dia`, facetas, `valor_min`/`valor_max`, `marcadores`) |
| `GET .../ufdr/{dominio}/facetas` | valores dos selects, extremos e contagem dos marcadores |
| `GET .../extracoes/{id_extracao}/dominios` | contagem por domínio, para montar as abas |

Domínio desconhecido responde `404`. O acesso passa por `exigir_acesso_inquerito`, como toda rota de
dado de inquérito — ver [permissoes_delegacia.md](permissoes_delegacia.md).

### O componente do front (`web/js/utils/ufdr_dominio.js`)

`UfdrDominio.montar({seletor, abas})` desenha a lista, a paginação, os filtros e o modal de anexo.
Os tipos de coluna disponíveis são `texto` (padrão), `longo`, `mono`, `data`, `numero`, `bytes`,
`duracao`, `bool`, `coord`, `url`, `tag` e `anexos`.

O que dispensa JS por tela é a **auto-montagem**: qualquer elemento com
`data-dominios="chave1,chave2"` vira a lista sozinho no `$(document).ready`. É por isso que
`social_buscas.html`, `calendario.html`, `dispositivos_reconhecidos.html` e
`identidade_aparelho.html` têm 65 linhas de HTML e nenhum arquivo `.js` próprio. As telas antigas
usam `UfdrDominio.abasDe([...])` para concatenar as abas novas às suas
(`navegacao.js`, `arquivos.js`, `ligacoes.js`, `contas_financeiras.js`, `aplicativos_uso.js`).

As colunas e os filtros de cada domínio são declarados **nos dois lados**: `COLUNAS` no JS (o que
desenhar) e `DOMINIOS` no Python (a lista branca do que é filtrável). Acrescentar um filtro na tela
sem declará-lo no back-end não funciona — de propósito.

## Modelo de dados

Todas no schema `INVESTIGACAO`, todas filhas de `INQ_TB02_EXTRACOES` por `ID_EXTRACAO`, todas com
`CD_STATUS_UFDR` (o `deleted_state` do `<model>`) — exceto `DON_TB01`, porque o
`<model type="User">` não traz o atributo, e por isso o filtro de apagados simplesmente não se
aplica a ele.

| Domínio (chave da rota) | Tabela | Rótulo | Tela |
|---|---|---|---|
| `uso-apps` | `USO_TB01_USO_APPS` | uso de aplicativo | modal de uso, em Aplicativos |
| `uso-aplicativo` | `APU_TB01_USO_APLICATIVO` | app com uso registrado | modal de uso, em Aplicativos |
| `dispositivos-reconhecidos` | `RCD_TB01_DISPOSITIVOS` | dispositivo reconhecido | Dispositivos reconhecidos |
| `conexoes` | `CNX_TB01_CONEXOES` | conexão | aba de Wi-Fi |
| `cookies` | `COO_TB01_COOKIES` | cookie | aba de Navegação |
| `favoritos` | `FAV_TB01_FAVORITOS` | favorito | aba de Navegação |
| `downloads` | `DWN_TB01_DOWNLOADS` | download | aba de Navegação |
| `autofill` | `AFL_TB01_AUTOFILL` | preenchimento automático | aba de Navegação |
| `uso-rede` | `RDE_TB01_USO_REDE` | uso de rede | aba de Navegação |
| `atividade-social` | `RDS_TB01_ATIVIDADE_SOCIAL` | atividade em rede social | Redes sociais e buscas |
| `buscas` | `BSC_TB01_BUSCAS` | busca | Redes sociais e buscas |
| `envios-nuvem` | `UPL_TB01_ENVIOS_NUVEM` | envio para a nuvem | aba de Arquivos |
| `gravacoes` | `GRV_TB01_GRAVACOES` | gravação | aba de Arquivos |
| `calendario` | `CAL_TB01_CALENDARIO` | compromisso | Calendário |
| `trajetos` | `JRN_TB01_TRAJETOS` | trajeto | aba de Localizações |
| `info-aparelho` | `DEV_TB01_INFO_APARELHO` | informação do aparelho | Identidade do aparelho |
| `donos` | `DON_TB01_DONOS` | dono | Identidade do aparelho |
| `contas-usuario` | `CTA_TB01_CONTAS_USUARIO` | conta de usuário | Identidade do aparelho |
| `antenas` | `ERB_TB01_ANTENAS` | antena | Identidade do aparelho |
| `voicemail` | `VMS_TB01_VOICEMAIL` | mensagem de voz | aba de Ligações |
| `transferencias` | `TRF_TB01_TRANSFERENCIAS` | transferência | aba de Contas bancárias |
| `cartoes-credito` | `CRT_TB01_CARTOES_CREDITO` | cartão de crédito | aba de Contas bancárias |
| `cartoes-carteira` | `MBC_TB01_CARTOES_CARTEIRA` | cartão da carteira | aba de Contas bancárias |

`DON_TB01` e `CTA_TB01` são anteriores a esta auditoria (dono do aparelho e contas de usuário já
existiam); entraram no registro para ganhar de graça a mesma listagem e os mesmos filtros das
demais, e é o que faz a tela **Identidade do aparelho** juntar as quatro fontes de identidade num
lugar só.

**O que aceita 📌** está em `entidades_utils._DOMINIOS_UFDR_ANOTAVEIS`: os 21 domínios da auditoria.
`dono` e `conta_usuario` ficam de fora dessa lista porque já tinham `tp_origem` próprio antes dela —
continuam anotáveis pelo caminho antigo, e por isso os 23 domínios do registro aceitam 📌.

**Contadores em `INQ_TB02_EXTRACOES`**: cada domínio tem uma coluna `QT_*` (`QT_USO_APPS`,
`QT_BUSCAS`, `QT_CALENDARIO`…), mapeada em `CONTADORES`. `contas-usuario` não tem coluna própria —
não valia uma migração só para isso — e é contado ao vivo. `NULL` significa "extração anterior ao
domínio", nunca "vazio".

Tipos que só existem num sistema operacional ficam com a tabela vazia num aparelho do outro, como
qualquer outro domínio ausente.

## Configuração

Nenhuma variável de `.env` própria. O teto de itens por página é o `LIMITE_MAX_LISTAGEM` comum às
listagens de investigação (`investigacao_utils.py`).

## Referências

- `api/funcoes/ufdr_dominios_utils.py` — `_Dominio`, `DOMINIOS`, `CONTADORES`, `listar_dominio`,
  `facetas_dominio`, `contagens_dominios`, `_sql_host`.
- `api/rotas/investigacao.py` — `ufdr_dominio`, `ufdr_dominio_facetas` e a rota de contagens.
- `api/funcoes/ufdr_parser.py` e `api/funcoes/investigacao_utils.py` — a gravação, no passe de
  extração.
- `web/js/utils/ufdr_dominio.js` — `montar`, `abasDe`, `COLUNAS`, auto-montagem por
  `data-dominios`.
- Telas próprias: `web/social_buscas.html`, `web/calendario.html`,
  `web/dispositivos_reconhecidos.html`, `web/identidade_aparelho.html`.
- Telas que ganharam abas: `web/navegacao.html` (`js/navegacao.js`), `web/arquivos.html`
  (`js/arquivos.js`), `web/ligacoes.html` (`js/ligacoes.js`), `web/contas_financeiras.html`
  (`js/contas_financeiras.js`), `web/localizacoes.html`, `web/wifi.html`, `web/aplicativos.html`
  (`js/aplicativos_uso.js`).
- Esquema: `db/init/18_credenciais_financeiro.sql` (o `CD_STATUS_UFDR` universal e
  `AFL_TB01_AUTOFILL`), `db/init/17_aparelho_redes.sql` (as tabelas),
  `db/init/18_credenciais_financeiro.sql` (campos que viraram `TEXT`; `DeviceEvent` e
  `ActivitySensorData` fora de escopo).
- Documentos relacionados: [extração_ufdr.md](extração_ufdr.md) (a extração e o backfill),
  [investigacao_celular.md](investigacao_celular.md) (as telas que ganharam abas),
  [mesa_inquerito.md](mesa_inquerito.md) (📌 e o grafo).
- `api/funcoes/entidades_utils.py` — `_DOMINIOS_UFDR_ANOTAVEIS` e `_VERIFICACAO_ORIGEM`.
