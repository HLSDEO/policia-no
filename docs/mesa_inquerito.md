# Mesa de investigação, entidades e identificação de pessoas

Este documento reúne a **Mesa de Inquérito** (grafo, filtros, análise estrutural, navegação e
visões salvas) e o modelo de **entidades** que a sustenta (entidades-comentário, canônicas,
evidências, identificadores e revisão de sugestões) — mesmo formato dos demais documentos (`docs/`).

| Seção | Cobre | Rotas |
|---|---|---|
| [Mesa de investigação](#mesa-de-investigação) | grafo de duas camadas, filtros, análise, navegação, visões salvas | `api/rotas/entidades.py`, `api/rotas/busca_automatica.py` |
| [Entidades, relações e revisão](#entidades-relações-e-revisão-de-sugestões) | camadas, revisão, canônicas, identificadores | `api/rotas/entidades.py` |

## Mesa de investigação

### Visão geral

A **Mesa de Inquérito** (`web/mesa_inquerito.html`) é a tela central de análise: desenha o
grafo do inquérito em **duas camadas** — os nós de entidades-comentário (registros de origem,
`ENT_TB01`) e os nós canônicos (`ENT_TB05`, com evidências `ENT_TB06`) — e reúne as três
operações de enriquecimento: **identificar pessoas** (determinístico, sem IA), **buscar
entidades** (IA) e **detectar padrões** (determinístico, sem IA). Sobre o desenho entrega a
**análise estrutural do quadro** (grau, comunidades por propagação de rótulos, **elos críticos**
— pontos de articulação e pontes por Tarjan iterativo) e um punhado de ferramentas de navegação
para casos grandes (**seleção por área**, **minimapa** e **visões salvas** por usuário). Todo o
desenho — layout de força, zoom/pan, comunidades, centralidade — roda **no cliente, sem
biblioteca externa** (`js/mesa_inquerito.js`).

### Fluxo de uso

1. **Filtros da mesa** (`mesa-toolbar`): busca por nome de nó (`termoBusca`, casa contra
   rótulo/comentário/observação, sem distinguir maiúscula) com **lista clicável dos top 50**
   (`mesa-busca-resultados`: tipo + grau, clique centraliza e abre a gaveta, afrouxando o
   grau se o nó estava fora do desenho); alternar "Sugestões da IA",
   "Confirmadas" (`CD_REVISAO` das entidades/relações), "Canônicas c/ relação" e "Canônicas s/
   relação" (uma canônica é "com relação" se participa de qualquer relação canônica **ou**
   vínculo de evidência, independente de qual camada está visível no momento); a legenda
    (`mesa-legenda`) também filtra — clicar num tipo liga/desliga ele, com botões
    **Todos/Nenhum** para os ~40 tipos; a legenda de **comunidades** é clicável e isola o grupo
    escolhido no desenho (`comunidadeFocada`, ver "Análise estrutural" abaixo). Ao lado do botão "Filtros" há ainda **Relações**
   (`filtro-grau-min`: Todas, 1+, 2+, 3+, **5+, 10+, 20+, 50+** — esconde os nós com menos
   ligações que o mínimo, mantendo quem casa a busca) e **Agrupar +N a partir de**
   (`filtro-limiar-cluster`: 3/5/10/20/Desligado — vale para folhas canônicas e para a
    camada doc: pessoa/achado citado num documento só vira "+N citações" no documento).
    **Ponte doc ↔ canônica** (servida pelo `grafo_documentos_inquerito`,
    `api/funcoes/documentos_utils.py`): a camada de documentos não é mais uma ilha — um
    achado cujo valor é um **CPF/CNPJ válido** (dígito verificador confere, com ou sem
    pontuação; vale até para o tipo `documento`, que a IA usa para o "DOCUMENTO: 149.408.157-14"
    do cabeçalho de um dossiê) ganha aresta "é o CPF de"/"é o CNPJ de" para a canônica cuja
    `NU_CHAVE` é `cpf:`/`cnpj:` com os mesmos dígitos; pessoa citada em documento só liga
    ("mesmo CPF/CNPJ") quando **CPF e nome** batem com a canônica — a IA repete o CPF do
    filtro do documento em toda pessoa citada, e ligar só pelo documento amarraria gente
    errada. Telefone/conta com dígitos que por acaso formam CPF válido não ligam (o tipo do
    achado manda). Ferramenta: `api/tools/teste_ponte_mesa.py`.
    A detecção de comunidades (propagação de rótulos) aparece como bolinha no nó, legenda
    "Comunidades:" clicável (isolar o grupo no desenho) e layout radial em desenhos grandes.
   A legenda de **relações é clicável** (`naturezasDesligadas`): desliga a natureza de verdade
   (fora do grau e do desenho). **Agrupar entidades** (`agrupar-entidades`): junta **só na
   tela** nós do mesmo tipo com nomes parecidos (fuzzy por tokens, tolerante a erro
   de digitação e sobrenome a mais/menos — ex.: "MARCELLO DOS SANTOS COSTA" e "MARCELO DOS
   SANTOS" viram um nó "MARCELLO DOS SANTOS COSTA (+1)" com as relações somadas; vale também
   para achados/pessoas de documento, ex.: "Art. 121 CP (Homicídio)" e "Art. 121 do Código
   Penal (Homicídio)" — CP/CPB e "código penal" contam como o mesmo código, mas CPP/processo
   penal não. Achado só agrupa com achado do mesmo `tp_achado`). Nada muda
   no banco — a gaveta do grupo lista quem foi junto, mostra as relações somadas (só
   leitura) e tem "Desfazer este grupo"; fundir de verdade continua sendo uma entidade por
   vez, pelo "Fundir com..." de cada canônica (grupo de documento é só visual: achado/pessoa
   de documento nem tem linha no banco). O recorte é compartilhável na URL
   (`?busca=&grau=&docs=`, via `EstadoUrl`) e o chat de IA enxerga os campos
   (`docs`, `naturezas_ocultas` em `grafo_filtros.js`).
   **Painel 📄 Documentos** (`mesa-docs-painel`): lista os documentos com nº de citações,
   busca e paginação; cada linha isola o documento no desenho, abre o visualizador ou marca
   para filtrar — "Filtrar marcados" recorta na tela (documentos + vizinhança de 1 salto),
   "↻ Só marcados" recarrega do servidor já pequeno (`GET .../grafo?docs=`).
   Para grafos grandes, `GET .../grafo/resumo` (`resumo_grafo`) devolve totais, contagens por
   tipo, histograma de grau e a estimativa da camada doc (`qt_documentos`,
   `total_nos_doc_estimado`) antes de baixar tudo — com 1000+ nós a mesa sugere começar em
   "2 ou mais relações"; `GET .../grafo` aceita `tipos_canonica`, `tipos_entidade`, `busca`,
   `min_evidencias`, `limite` e, para documentos, `docs`, `tipos_doc`, `min_docs`, `busca_doc`
   (recorte server-side). Em caso com 50+ documentos, cada documento contribui com no máximo
   100 arestas (as citações mais fortes; o resto vira `qt_citacoes_omitidas`) e entram no
   máximo 2000 pessoas (as mais conectadas). Acima de 500 nós desenhados o layout
   troca o Fruchterman-Reingold O(n²) pelo radial por comunidade (instantâneo). O fuzzy
   ("Agrupar entidades") é pulado acima de 1500 elegíveis, pedindo um recorte em vez de travar.
2. **Chat da mesa** (bolha 🤖 no canto): o painel flutuante de IA
   (`web/js/utils/ia_flutuante.js`) roda aqui sem restrição de escopo — a mesa é a tela do
   inquérito inteiro (só `?inquerito=`), então o chat enxerga aparelhos, RIFs, extratos e IPLs.
   Além de responder, ele **propõe filtros da mesa**: "me mostra as relações do fulano", "só o
   período de março", "esconde as ligações" viram um botão "Aplicar no grafo" no fim da resposta,
   que escreve nos mesmos filtros do item 1 (busca, camadas, período e tipos da legenda) e traz um
   "Desfazer". Nada muda na tela sem o clique — ver "Filtrar o grafo pelo chat" em `chat_ia.md`.
3. **Criar relação**: o botão "Modo relação" destaca um nó e pede o segundo clique
   (`mesa-modo-ligacao` — "clique no segundo nó para ligar, ou pressione Esc para cancelar");
   o modal `modal-relacao` pede o tipo do vínculo (texto livre) — vira `POST
   /api/investigacao/relacoes` (entidade↔entidade) ou `POST .../canonicas/relacoes`
   (canônica↔canônica), conforme o tipo dos dois nós clicados.
4. **Nó**: modal `modal-no` mostra o corpo (`no-corpo`) e ações (`no-acoes`) — comentar,
   revisar (confirmar/descartar sugestão da IA), fixar posição (arrastar salva `NU_POS_X/Y`;
   entidade fixada não entra mais no recálculo automático do layout), remover. **Nova
   canônica**: modal `modal-canonica` com tipo (pessoa/telefone/local/veículo/organização/
   outro), nome e observação.
5. **Imagem no lugar do nó**: a gaveta do nó tem a linha **Imagem** — "Enviar imagem"
   (ou "Trocar"/"Remover") abre o seletor de arquivo e a foto passa a ser o nó no desenho,
   recortada na silhueta dele. Vale para as duas camadas (canônica e entidade-comentário) e é
   o que transforma um grafo de trinta pessoas numa parede de rostos. Ver "Foto do nó" abaixo.
6. **Foco e multisseleção**: clicar num nó põe ele e a vizinhança direta em foco (o resto do
   grafo desbota) e abre a gaveta com o detalhe. **Shift+clique acumula**: cada nó entra ou sai
   do conjunto `focados` (`selecionarNo`/`alternarSelecao` em `mesa_inquerito.js`), o foco passa
   a ser a **união** das vizinhanças (`vizinhancaDe`) e a gaveta troca o detalhe por um resumo
   conjunto — as relações **entre** os selecionados, os **vizinhos em comum** (ligados a dois ou
   mais deles, que é onde aparece o intermediário) e a contagem das relações em foco por
   natureza. A barra de isolamento (`mesa-isolamento-bar`) transforma o foco em recorte de
   desenho: **Isolar 1/2 saltos** (`vizinhancaNSaltos`) desenha só a vizinhança, **Só caminho
   entre elas** (`bfsCaminho`) desenha só o menor caminho entre os selecionados, e **Voltar a
   tudo** limpa. Um nó que sai do desenho (filtro/período/recarga) sai do foco sozinho, e a gaveta
    se fecha quando não sobra ninguém. Esc limpa tudo.

   **Gaveta de detalhes (cabeçalho e corpo)** — o cabeçalho (`mesa-drawer-header`) mostra o
   **nome** da entidade como título e o **tipo** como badge ao lado (a cor do tipo, a mesma do
   desenho/legenda); à direita ficam **⤢ expandir/recolher**, **↗ buscar entidade** (abre
   `busca_entidades.html?q=` em nova aba) e **× fechar**. A largura se ajusta de duas formas — o
   botão ⤢ alterna o estado "larga" (`mesa-drawer-larga`, mais larga no lugar, sem modal) e a
   **alça na borda direita** (`#no-resize`, setas ←/→ no teclado) define largura contínua; a
   preferência sobrevive à troca de nó e ao recarregar via `localStorage` (`mesa-drawer-larga` /
   `mesa-drawer-w`), e o corpo passa a grade de duas colunas quando larga. O corpo organiza os
   blocos longos em **seções colapsáveis** (`novaSecao`): Relações, Evidências e "Citado em N
   documento(s)" recolhem sozinhas quando são muitos; com mais de 8 relações a seção ganha um
   campo de filtro. Nome/Chave/CPF-CNPJ têm botão **copiar** (`mesa-copiar-btn`, `copiar()` com
   fallback). Tudo nos dois temas (claro e orbit/dossiê — ver `mesa_orbit.css`).

7. **IA** (`ia-dropdown`): "Buscar entidades" (prompts), "Relacionar manuais" e "Detectar
   padrões" — os dois primeiros disparam jobs em background com polling
   (`ENT_TB04_BUSCAS_AUTOMATICAS`, ver `busca_automatica_utils.py` abaixo); a detecção de
   padrões usa `PDR_TB02_EXECUCOES` (ver `padroes_crime.md`) e os resultados abrem o modal
   `modal-deteccoes`.
8. **Identificar pessoas** (`identificar-pessoas-btn`): refaz a identificação determinística
   de **todas as extrações do inquérito mais o RIF e o SIMBA anexados** ("a mesma etapa que
   roda sozinha ao final de cada extração"); o rodapé (`$statusLabel`) mostra o resumo
   ("N pessoa(s) na mesa (M nova(s)), E evidência(s) e R relação(ões) novas em X extração(ões)")
   ao concluir. É idempotente — pode rodar quantas vezes for preciso.
9. **Análise** (`mesa-analise-btn`): modal `modal-analise` com os nós de maior **grau**
   (mais conexões), os de maior **intermediação** (nós por onde mais caminhos entre outros
   pares passam — "pontes da rede") e a contagem/tamanho das **comunidades** detectadas.
10. Quando não há entidade alguma, a mesa mostra ações de partida (`mesa-vazio-acoes`): buscar
    com IA, identificar pessoas ou criar manualmente.

### Como funciona

#### Grafo de duas camadas

`db/init/21_entidades_padroes.sql`: entidades canônicas (`ENT_TB05`) com evidências (`ENT_TB06`)
formam a camada **curada**; as entidades-comentário (`ENT_TB01`) formam a camada **bruta**. A
rota `GET /api/investigacao/inqueritos/{id}/grafo` devolve as duas camadas de uma vez —
entidades, relações, canônicas, relações canônicas e `vinculos_evidencia`
(`listar_vinculos_evidencia`, que liga a canônica aos nós da mesa que marcam o **mesmo**
registro via `tp_origem`/`id_origem` e mesmo `id_inquerito`, ignorando entidade `descartada`) —
e o front monta um único modelo de nós/arestas a partir disso (`montarModelo` em
`mesa_inquerito.js`): entidade descartada nunca vira nó; relação descartada nunca vira aresta.

#### Renderização (client-side, sem biblioteca externa)

- **Layout**: Fruchterman-Reingold (atração pelas arestas, repulsão entre todo par de nós),
  300 iterações rodadas em fatias de 30 por frame via `requestAnimationFrame` (não trava a UI
  num grafo grande). Só refaz o layout quando o **conjunto** de nós visíveis muda (assinatura =
  ids ordenados) — buscar, colorir por comunidade ou arrastar um nó não disparam recálculo, e
  nó fixado (posição salva ou arrastado na sessão) não se move. Raio do nó cresce com o grau
  (`10 + 2*sqrt(grau)`, teto 24/20 conforme canônica/entidade; nó com foto tem piso de 22).
- **Cor da linha = natureza da relação** (`NATUREZAS_RELACAO` / `naturezaAresta` em
  `js/mesa_inquerito.js`): conversa (WhatsApp/SMS), ligação, SIMBA, RIF, IPEI, IRPF, telefonia,
  PIX/transferência, contato compartilhado, posse do aparelho, presença em local, titulares que
  conversam entre si, evidência e agrupamento — o que não casa nenhuma regra fica em "outra",
  cinza. **A ordem da lista importa**: a aresta do CDR se chama `"ligou para (telefonia)"` e
  casaria com a regra da ligação do aparelho, que é outra fonte — por isso a regra da telefonia
  vem antes dela. `ENT_TB07` não
  tem coluna de fonte, então a classificação é **textual**, sobre `TX_TIPO_RELACAO` (o mesmo
  texto que `identificacao_pessoas.py` grava), primeira regra que casa vence. O JS põe
  `--mesa-aresta-cor` na aresta e numa ponta de seta por natureza (marcador em `<defs>` não
  herda cor de quem o usa); `mesa.css` lê a variável com o cinza de antes como padrão, que é o
  que as outras telas que reaproveitam `.mesa-aresta` (caso, convergência, grafo de fluxo)
  continuam usando. A legenda `mesa-legenda-relacoes` lista só as naturezas presentes no
  desenho e é informativa — quem liga/desliga camadas é a legenda de tipos de nó.
- **Análise de grafo** (`js/mesa_inquerito.js`, sem lib) — tudo sobre o **desenho atual** (o que
  está na tela, não a rede inteira no banco): **comunidades** por propagação de rótulos (label
  propagation — cada nó adota o rótulo mais comum entre os vizinhos, até 20 iterações ou
  convergir), estáveis sobre o conjunto-base (a partição não renumera ao mexer no filtro de grau)
  e mostradas como bolinha no nó (`mesa-no-badge`) + legenda "Comunidades:" **clicável** — clicar
  num grupo isola só ele (`comunidadeFocada`); **elos críticos** por **Tarjan iterativo**
  (sem recursão — um inquérito de milhares de nós estouraria a pilha) sobre multigrafo
  não-dirigido: **ponto de articulação** (nó que, removido, desconecta o quadro → anel vermelho
  `no-articulacao`) e **ponte** (aresta que faz o mesmo → linha tracejada vermelha `ponte`); o
  Tarjan trata certo as arestas paralelas (conversa + PIX entre o mesmo par — uma não é ponte se
  há outra ligando os dois). Ligado pelo checkbox **"Elos críticos"** (`mesa-destacar-elos`,
  `destacarElos`) e recapitulados no **modal "Análise"** (`mesa-analise-btn` / `modal-analise`):
  nós mais conectados (grau), pontos de articulação, pontes e comunidades — cada item é clicável e
  age sobre o grafo (foca o nó, abre a gaveta da aresta ou isola o grupo). **Clique numa aresta**
  abre a **gaveta da relação** (`abrirGavetaAresta`), por uma trilha invisível larga e aparada nas
  pontas (`mesa-aresta-hit`) que não rouba o clique dos nós: mostra de/para, natureza, tipo,
  detalhe, justificativa da IA, origem e as ações de confirmar/descartar/apagar quando cabe.
- **Navegação em desenho grande**: **seleção por área** (`mesa-selecao-area`, `modoSelecaoArea`) —
  arrastar um retângulo sobre o fundo escolhe todos os nós dentro e os põe em foco (o mesmo
  `abrirGaveta` do Shift+clique); **minimapa** (`mesa-minimapa`, `<canvas>`) — nós amostrados (até
  ~800) + retângulo da área visível, clicar/arrastar centraliza o quadro, some sozinho em desenhos
  com menos de 25 nós.
- **Visões salvas, privadas por usuário** (`mesa_visoes_utils.py`, `ENT_TB10_MESA_VISOES`): o
  investigador guarda filtros + fontes + teto de nós + enquadramento num nome só e volta a ele num
  clique, ou abre direto por link (`?visao=`). A linha é DONO do usuário (`ID_USUARIO NOT NULL`) e
  toda leitura/apagamento filtra por ele — cada um vê só as suas, mesmo no mesmo inquérito. O
  estado é JSONB opaco (só a tela entende o formato, então ganha filtro novo sem migration). Rotas:
  `GET/POST .../mesa-visoes` e `GET/DELETE .../mesa-visoes/{id}` (basta acesso de leitura ao
  inquérito — é dado pessoal, não mexe no caso); salvar com o mesmo nome substitui (upsert pelo
  UNIQUE `(id_inquerito, id_usuario, no_visao)`).

#### Foto do nó (`api/funcoes/imagens_mesa.py`, `ENT_TB09_IMAGENS`)

A imagem enviada substitui o preenchimento do nó: entra **recortada na silhueta** dele
(`silhuetaNo` em `mesa_inquerito.js` — círculo na entidade-comentário, losango na canônica, de
modo que a forma continua dizendo de que camada o nó é), com `preserveAspectRatio="xMidYMid
slice"` para preencher sem deformar, e a cor do tipo volta num **anel** por fora — a legenda
promete "cor = tipo" e não pode deixar de valer justo nos nós que mais chamam atenção. O ícone
do tipo sai de cena, e o raio do nó ganha um piso maior (`RAIO_MINIMO_FOTO`), senão o rosto vira
mancha. Nó com foto continua clicável e arrastável: nem a imagem nem o anel recebem ponteiro
(`pointer-events: none`), o alvo segue sendo a forma por baixo.

Alvo **polimórfico** (`TP_NO`/`ID_NO`, mesmo padrão de `TP_ORIGEM`/`ID_ORIGEM`): `canonica`
aponta `ENT_TB05` e `entidade` aponta `ENT_TB01`, uma imagem por nó (`UNIQUE (TP_NO, ID_NO)` —
reenviar substitui). Sem FK para o nó, portanto a exclusão é **cascata manual**: em
`remover_canonica`/`remover_entidade`, em `_apagar_anotacoes_de_extracao` (as entidades daquela
extração e as canônicas automáticas que ficaram órfãs) e em `_apagar_dados_inquerito` — esta
última obrigatória, já que `ENT_TB09` **tem** FK para o inquérito. Na fusão de canônicas
(`fundir_canonicas`) a foto da origem passa ao destino se ele ainda não tiver uma.

O que vai para o banco não é o arquivo enviado: `normalizar_imagem` reduz a `LADO_MAX` (320 px),
aplica a **orientação do EXIF** (sem isso um retrato tirado deitado aparece de lado no grafo),
achata transparência sobre **branco** (PNG transparente convertido direto para RGB vira um
retângulo preto) e reencoda em JPEG — uma foto de celular de 4 MB fica em dezenas de KB, que é o
que torna razoável guardar o binário junto do inquérito em vez de em disco. HEIC entra pelo
mesmo `pillow_heif` da prévia de anexos.

No cliente, cada foto é buscada **uma vez** (`GET /api/investigacao/nos/{tp_no}/{id_no}/imagem`)
e guardada como **data URL**, não como `src` remoto, por duas razões: o grafo se recarrega a cada
ação da mesa e um `src` viraria uma enxurrada de requisições; e o **PNG exportado** é o SVG
rasterizado a partir de uma data URL, contexto em que o navegador não vai à rede — com `src`
remoto a foto sairia como um buraco branco no meio do relatório. O grafo só devolve os metadados
(`imagens`: `tp_no`, `id_no`, `dt_envio`), e é o carimbo de envio que faz a foto trocada aparecer
sem F5. O `fill="none"` do anel é gravado **também como atributo**, não só no CSS: quem lê o SVG
sem a folha de estilo pintaria o miolo do anel de preto por cima da foto.

#### Identificação de pessoas — determinística, sem IA (`api/funcoes/identificacao_pessoas.py`)

Roda **por INQUÉRITO**, não por extração — a identidade é do caso, não do aparelho: com dois
celulares apreendidos, a dona de um costuma ser contato salvo no outro, e o titular do extrato
SIMBA costuma ser o dono de um dos aparelhos; cruzar tudo num índice só faz dela **uma** pessoa
com evidência de todas as fontes. Roda automática ao final de cada extração (com a própria
extração ainda `processando`, via `incluir_extracao`) e sob demanda pelo botão da Mesa
(`identificar_pessoas_inquerito`). Idempotente — reencontra a pessoa por **qualquer**
identificador dela (`ENT_TB08`), não só pela chave do nó.

**Fontes** (varridas uma vez por extração, mais RIF/SIMBA/IPL/IPEI/IRPF/telefonia uma vez por
inquérito — documentos do inquérito, sem aparelho associado):

| Fonte | Grupo | Confiabilidade do nome |
|---|---|---|
| Contato de WhatsApp/SMS/mensageiro | `contatos` | `NOME_PERFIL`/`NOME_SECUNDARIO` |
| Agenda do aparelho | `contatos` | `NOME_CADASTRADO` |
| Ligações (agrupadas por número) | `contatos` | `NOME_SECUNDARIO` |
| Dono do aparelho (`DON_TB01_DONOS`) | `dono` | `NOME_DOCUMENTAL` |
| Contas de aplicativo (Apple ID, Google...) | `contas` | `NOME_CADASTRADO` |
| Contas bancárias do aparelho (titular) | `financeiro` | `NOME_DOCUMENTAL` |
| Chaves PIX citadas em conversas | `financeiro` | `NOME_BASE_REFERENCIA` (nome vem do CPF resolvido em `PES_TB01_PESSOAS`) |
| Titulares e contrapartes do **extrato SIMBA (R304)** | `financeiro` | `NOME_DOCUMENTAL` |
| Envolvidos e contrapartes do **RIF (COAF)** | `financeiro` | `NOME_DOCUMENTAL` |
| Alvos, vínculos, cotitulares de conta, contrapartes de NF-e e contatos da **IPEI (quebra de sigilo fiscal)** | `ipei` | `NOME_DOCUMENTAL` |
| Declarante, dependentes, fontes pagadoras e beneficiários de doação da **declaração de IRPF** | `irpf` | `NOME_DOCUMENTAL` |
| Titulares de linha da **telefonia (quebra de sigilo telefônico)** | `telefonia` | `NOME_DOCUMENTAL` |
| Participantes de **transferência** do aparelho (`TRF_TB01`) | `financeiro` | `NOME_DOCUMENTAL` |
| Titular impresso no **cartão de crédito** (`CRT_TB01`) | `financeiro` | `NOME_DOCUMENTAL` |
| **Convidados e organizador de compromisso** (`CAL_TB01`) | `contatos` | `NOME_CADASTRADO` |
| **MSISDN e ID da Apple** do `DeviceInfoEntry` (`DEV_TB01`) | `dono`/`contas` | — (identificador) |
| **Organizador e participantes de evento da nuvem/IPED** (`EVT_TB01_EVENTOS`) | `contatos` | `NOME_CADASTRADO` |
| **Identidade do aparelho da nuvem/IPED** (`DVC_TB01_DISPOSITIVOS`: IMEI, série) | — | — (vira nó `dispositivo`, não pessoa) |

As três primeiras vieram da auditoria de aproveitamento do UFDR. O MSISDN e o ID da Apple
trocam DEDUÇÃO por DECLARAÇÃO no nó mais importante da mesa: até então o titular de um iPhone
era inferido varrendo as ~200 contas de aplicativo e filtrando por domínio Apple.

As duas últimas linhas são a ponte do IPED/nuvem: o IPED não grava em `DEV_TB01_INFO_APARELHO`
nem em `CAL_TB01` — a identidade do aparelho e os eventos dele caem nas tabelas genéricas de
conta na nuvem (`DVC_TB01_DISPOSITIVOS` e `EVT_TB01_EVENTOS`), lidas por
`_coletar_info_aparelho_nuvem` e `_coletar_eventos_nuvem`. O resto das fontes (contatos,
ligações, contas, mensagens) já é table-driven: o IPED grava nas mesmas tabelas do UFDR, então
os mesmos coletores as enxergam — e a identificação roda sozinha ao final da importação do IPED
(ver `extracao_iped.md`), como no UFDR.

Duas adaptações tornam a identificação do IPED equivalente à do UFDR — onde ela difere é no
**volume de mensagens** e no **dono**:

- **Top de conversa vale para o mensageiro genérico.** O `_marcar_top_wpp` (que promove os
  `_TOP_CONVERSAS_WPP` interlocutores mais frequentes de cada aparelho a nó) antes só lia a
  família WhatsApp/SMS do UFDR; a Nuvem IPED grava TODOS os mensageiros (WhatsApp, Instagram,
  Telegram…) em `MSG_TB0x`, então ele também varre `contato_msg` — é o que devolve os nós
  `"conversou com"` para o IPED. O filtro de "pessoa de verdade" continua: telefone de JID/
  número, e `@handle` (Instagram numérico) é recusado por `_telefone_br`.
- **O dono é eleito pela conta dominante.** Num aparelho (UFDR) toda conta configurada é do
  dono; numa nuvem (IPED) a categoria "User Accounts" mistura contas de várias pessoas (e até
  "conta" cujo nome é um arquivo SQLite, hoje descartada já no import por `conta_de_item`).
  `_coletar_contas_usuario` elege o e-mail válido que aparece em mais contas como dono, e o
  resto entra como conta comum fundida por e-mail — sem isso o inquérito inteiro virava uma
  pessoa só.

Convidado de compromisso e participante de transferência chegam como texto no formato
`Nome <identificador>` (é o que `ufdr_parser._rotulo_parties` grava). A quebra é feita pelos
identificadores entre `< >`, **nunca por vírgula**: nome com vírgula existe ("SILVA, JOAO") e
partir por vírgula fundiria pessoas diferentes — o pior erro possível aqui (ver
`_partes_de_rotulos`).

Um perfil (`_Perfil`) acumula identificadores (`telefone`/`cpf`/`email`/`usuario_app`/`nome`),
nomes com procedência e evidências; o índice (`_Indice`) funde dois perfis assim que
compartilham **qualquer** identificador. Um segundo passe (`_fundir_por_nome_abreviado`) funde
por primeira+última parte do nome ("João Silva" ⊂ "João Pedro da Silva"), recusando quando os
dois lados têm CPF diferente (homônimos — o documento manda).

**Duas invariantes contra fusão em cascata** (o jeito disso dar errado, já visto em UFDR real):

1. **Identificador que não identifica ninguém não vira ponte** — rótulo de app do Android
   ("WhatsApp Business", `com.whatsapp.w4b`) não entra como `usuario_app`/e-mail de fusão;
   CNPJ nunca vira `cpf` (é empresa); chave montada com campo vazio é descartada
   (`_usuario_app`, `_email`, `_telefone_br`, `_cpf_de_documento`).
2. **Dois DONOS de aparelhos diferentes só fundem por CPF/telefone/e-mail em comum**
   (`_Indice.fundir`) — nunca por identificador fraco. Também **não** usa o `IsPhoneOwner`
   por conversa do Cellebrite (`IN_DONO_DISPOSITIVO`) como sinal de fusão — só
   `DON_TB01_DONOS` (a declaração explícita do relatório) marca alguém como dono; o flag por
   conversa já veio errado em UFDR real (marcando pessoas diferentes como dono do mesmo
   aparelho) e, sem esse guard, o inquérito inteiro vira uma pessoa só.

**Além da pessoa, cinco outros tipos de nó** (todos determinísticos, ver
`_materializar_dispositivos`, `_materializar_locais`, `_materializar_telefonia`,
`_materializar_ipei` e `_materializar_irpf`):

- **`dispositivo`** — um por aparelho, com IMEI, ICCID, IMSI, série e MAC vindos do
  `DeviceInfoEntry` (UFDR) **ou** de `DVC_TB01_DISPOSITIVOS` (IMEI e série, no IPED/nuvem),
  ligado ao dono pela relação `"é dono de"`. O aparelho é entidade própria
  porque tem identidade própria: pessoa troca de aparelho e aparelho troca de dono, e pendurar
  IMEI na pessoa misturaria as duas coisas. A chave de deduplicação segue a ordem IMEI → ICCID
  → série → MAC → IMSI. Identificador de caractere único repetido é recusado
  (`_identificador_util`): um iPhone real trouxe `ICCID 00000000000000000000`, e como
  identificador ele faria dois inquéritos quaisquer com o mesmo placeholder "convergirem".
  Como a evidência aponta a origem certa de cada identificador (`info_aparelho` no UFDR,
  `dispositivo_conta` no IPED/nuvem), um aparelho que aparece nos dois formatos não mistura
  IDs de tabelas diferentes.
- **`local`** — ponto onde o aparelho conhece **10 ou mais** redes Wi-Fi na mesma coordenada
  arredondada a ~11 m (`_MINIMO_REDES_LOCAL`), ligado ao dono por `"esteve em"`. O corte é o
  que separa lugar de passagem: nos oito extratos auditados, 10+ redes deixa 75 pontos e 3+
  deixaria 1.199. A coordenada do Wi-Fi identifica um roteador, e é bem mais precisa que
  endereço de texto livre.
- **`telefone`** — a linha da quebra de sigilo telefônico (`_materializar_telefonia`). É o
  único tipo de nó em que o vértice natural **não** é a pessoa: no CDR o número de quem ligou
  não tem nome, tem volume de chamada, IMEI e horário, e forçá-lo a virar "pessoa" afirmaria
  uma identidade que a operadora não deu. Então a linha vira nó, o titular da ficha cadastral
  se liga a ela (`"titular da linha (telefonia)"`), o aparelho se liga a ela
  (`"linha usada no aparelho (telefonia)"`) e o CDR vira aresta entre linhas
  (`"ligou para (telefonia)"`, com quantas chamadas, quantos minutos e em que período). A
  chave é `tel:<número normalizado>`.
- **`organizacao`** — a empresa que aparece com **CNPJ** em IPEI, IRPF ou telefonia (chave de
  deduplicação = o CNPJ). Sem o nó da empresa, metade dos vínculos dessas fontes não teria
  onde chegar: sócio de empresa, empregador, fonte pagadora, emitente de nota fiscal e titular
  de linha corporativa.
- **`veiculo`** — a placa levantada no RENAVAM pela IPEI (`IPE_TB07_BENS`, `TP_BEM` começando
  em `veiculo`), chave `placa:<placa>`, ligada ao alvo por `"proprietário de (IPEI)"`. Só
  entra com placa: a discriminação do bem é texto livre, e nó tirado de texto livre não se
  consegue afirmar.

**O corte de volume é o que mantém a mesa legível.** As fontes documentais têm cauda longa, e
despejá-la inteira afogaria o que interessa (o mesmo raciocínio de `_TOP_CONVERSAS_WPP`):
`_TOP_CONTRAPARTES_TELEFONIA` (8) contrapartes de CDR por linha investigada, com piso de
`_MIN_CHAMADAS_TELEFONIA` (3) chamadas aplicado **no `HAVING` da consulta**, para a cauda nem
chegar ao processo; `_MAX_CONTRAPARTES_NFE` (15) emitentes/destinatários de nota fiscal por
alvo da IPEI e `_MAX_CONTRAPARTES_IRPF` (20) fontes pagadoras por declaração, os dois cortados
pelo **maior valor** (dez notas de R$ 40 no posto dizem menos que uma de R$ 300 mil);
`_MAX_EVIDENCIAS_POR_EMPRESA` (20) evidências por nó de empresa.

**Materialização** (`_identificada`, régua do que vira nó): dono do aparelho, ou quem tem CPF,
ou quem tem nome **e** aparece em alguma fonte além de `GRUPO_CONTATOS` (chave PIX, conta
bancária/de app) — quem só existe na agenda **não** vira nó (isso já é a tela Contatos; encher
a mesa com a agenda inteira não ajuda). Perfil com mais de `_MAX_TELEFONES_POR_PESSOA` (8)
telefones é descartado — sinal de fusão que deu errado, melhor não afirmar nada. **A ficha
cadastral da operadora é a exceção**: linha que um documento declara no nome da pessoa entra em
`_Perfil.telefones_documentais` e fica fora desse teto — sete linhas no mesmo CPF não é fusão
errada, é a resposta do ofício, e sem a ressalva o alvo da quebra de sigilo telefônico sumiria
da mesa justamente no caso em que ele tem mais linhas. Cada pessoa
materializada grava: canônica `pessoa` (nunca sobrescreve nó manual — só atualiza o que a
própria identificação criou), identificadores em `ENT_TB08`, evidências em `ENT_TB06`, e
relações **só quando dá para afirmar sem achismo**: `"conversou com"` entre o dono do aparelho
e quem trocou mensagem individual com ele, `"chave PIX citada por"` entre a chave e quem a
mandou na conversa, `"recebeu de (SIMBA/RIF)"`/`"pagou a (SIMBA/RIF)"` entre o titular da conta
e a contraparte (direção do dinheiro do ponto de vista do titular), as da IPEI —
`"sociedade (IPEI)"`, `"vínculo empregatício (IPEI)"`, `"procuração/escritura (IPEI)"`,
`"procuração perante a RFB (IPEI)"`, `"declaração do mesmo computador (IPEI)"`,
`"cotitular de conta (IPEI)"`, `"comprou de (IPEI)"`, `"vendeu para (IPEI)"` e
`"proprietário de (IPEI)"` —, as do IRPF — `"dependente (IRPF)"`, `"recebeu de (IRPF)"` e
`"doou a (IRPF)"` — e as da telefonia — `"titular da linha (telefonia)"`,
`"usa a linha (telefonia)"`, `"ligou para (telefonia)"` e
`"linha usada no aparelho (telefonia)"`.

O sufixo entre parênteses não é enfeite: é ele que a mesa usa para colorir a aresta por
natureza (ver `NATUREZAS_RELACAO`), e é por isso que toda relação nova de uma fonte
documental o carrega.

**A IPEI é a única fonte que também cria nó de EMPRESA** (`TP_ENTIDADE = 'organizacao'`, chave de
deduplicação = CNPJ). Metade dos vínculos dela tem CNPJ de um lado — sócio de empresa, empregador,
outorgante — e sem o nó da empresa a aresta não teria onde chegar: a **sociedade entre dois
investigados na mesma empresa** ficaria invisível, e é justamente esse desenho que a investigação
procura (o IRPF e a telefonia passaram a criar nó de empresa pelo mesmo motivo: fonte pagadora
e titular de linha corporativa vêm com CNPJ).
Assim a mesa condensa a tabela da IPEI em "fulano —vínculo→ entidade": o alvo vira nó,
cada relacionado vira nó (pessoa pelo CPF, empresa pelo CNPJ) e o tipo do vínculo vira a aresta.
`"declaração do mesmo computador (IPEI)"` é gravada como **simétrica** — quem transmitiu do mesmo
MAC está no mesmo pé, e duas setas dizendo o mesmo só sujam o grafo. `"ligou para (telefonia)"` e
`"cotitular de conta (IPEI)"` são simétricas pela mesma razão: no CDR a ida e a volta entre as
duas linhas viram **uma** aresta, com a soma das duas.

**O telefone e o e-mail que a IPEI e o IRPF trazem não viram nó nem aresta: viram
IDENTIFICADOR da própria pessoa** (`IPE_TB09_CONTATOS`, e o cabeçalho da declaração). É o que
faz o alvo da quebra de sigilo fiscal, o contribuinte e o contato salvo no celular apreendido
virarem **uma** pessoa — a fusão do `_Indice` acontece por identificador em comum, e até então
essas fontes só traziam CPF. O mesmo vale para o cadastro da operadora, que dá nome + CPF +
**o número da linha** no mesmo registro.

**Diagnóstico** — a resposta de `identificar_pessoas_inquerito` (e portanto de `POST
.../identificar-pessoas`) inclui `donos_excluidos`: a lista dos perfis marcados como dono do
aparelho que **não** viraram pessoa identificada (sempre deveriam, por `_identificada`) — sinal
de que a fusão descartou o perfil por excesso de telefones. Existe para que essa falha não fique
invisível ("processo termina com sucesso e 0 pessoas identificadas", sem pista do motivo); hoje
é só um campo da resposta da API (não renderizado em `mesa_inquerito.js`), útil para inspeção
via requisição direta ou log ao investigar um inquérito com resultado zerado inesperado.

#### Busca automática (`api/funcoes/busca_automatica_utils.py`)

`TIPOS_BUSCA_VALIDOS = {"prompts", "relacionar_manuais"}`; roda em thread com status
`pendente|processando|concluido|erro` e o front consulta via polling
(`GET /api/investigacao/inqueritos/{id}/busca-automatica`). A IA recebe as ferramentas de
leitura normais do chat mais três de escrita
(`funcoes.ia_investigacao.ferramentas_para_busca_automatica`):
`listar_entidades_existentes` (evita duplicar), `criar_entidade` e `criar_relacao` — tudo que
ela cria nasce `CD_REVISAO = 'sugerida'`, com `TX_JUSTIFICATIVA` (a evidência citada) e
`ID_BUSCA` gravados. Dois modos, cada um com seu prompt de sistema:

- **`prompts`** (`SISTEMA_PROMPT_BUSCA`) — varre os prompts **ativos** da biblioteca
  (`ENT_TB02_PROMPTS_BUSCA`, mantida por admin em "Prompts de entidade"); levanta erro se não
  houver nenhum ativo.
- **`relacionar_manuais`** (`SISTEMA_PROMPT_RELACIONAR`) — foca nas entidades criadas
  **manualmente** pelo investigador (`in_automatico = false`, não descartadas), destacando na
  mensagem as que ainda não têm nenhuma relação; exige pelo menos 2 entidades manuais.

A execução é registrada na auditoria de IA (`registrar_auditoria("busca_automatica", ...)`,
ver `auditoria.md`) e a contagem final de criadas (`QT_ENTIDADES_CRIADAS`/
`QT_RELACOES_CRIADAS`) é exata por proveniência (`WHERE id_busca = ...`), imune a criações
manuais concorrentes durante a execução.

#### Padrões (`api/funcoes/padroes_utils.py`)

Determinísticos (léxico/regex/temporal), um clique converte detecção em entidade — ver
`padroes_crime.md` para o detalhe completo.

#### Rotas

- `api/rotas/entidades.py` — leitura de entidades/grafo usa `exigir_acesso_inquerito`;
  criação/revisão/canônicas passam por `_exigir_acesso` (404 se o inquérito não existe, 403 sem
  permissão) ou checagem inline com `usuario_tem_acesso_inquerito`. `POST
  /api/investigacao/inqueritos/{id}/identificar-pessoas` dispara a identificação (síncrono —
  a chamada só retorna quando termina, ao contrário dos jobs em background de busca/padrões).
- `api/rotas/busca_automatica.py` — `GET`/`POST /api/investigacao/inqueritos/{id}/busca-
  automatica`, ambos atrás de `exigir_acesso_inquerito`.
- `POST`/`GET`/`DELETE /api/investigacao/nos/{tp_no}/{id_no}/imagem` (em `entidades.py`) — a foto
  do nó, com `tp_no` em `canonica|entidade`; `_inquerito_do_no` resolve o inquérito dono seja
  qual for a camada, e daí vale o mesmo `_exigir_acesso` do resto.
- `GET`/`POST /api/investigacao/inqueritos/{id}/mesa-visoes` e `GET`/`DELETE .../mesa-visoes/{id}`
  (em `entidades.py`, atrás de `exigir_acesso_inquerito`; ver `funcoes/mesa_visoes_utils.py`) — as
  **visões salvas** do quadro, PRIVADAS por usuário: `listar_visoes`/`obter_visao`/`apagar_visao`
  filtram por `ID_USUARIO` na própria SQL, então um investigador nunca lista, abre nem apaga a
  visão de outro. Salvar é upsert pelo nome (`(id_inquerito, id_usuario, no_visao)`).

## Entidades, relações e revisão de sugestões

### Visão geral

A mesa de investigação trabalha com duas camadas de entidades:

1. **Entidades-comentário** (`ENT_TB01_ENTIDADES`) — um registro de origem ganha um
   comentário do investigador (ou da IA) e vira um nó do grafo. A entidade **não copia** o
   dado: aponta para ele pelo par `TP_ORIGEM`/`ID_ORIGEM`, validado em código
   (`_VERIFICACAO_ORIGEM`) contra a tabela correspondente. O mesmo padrão de referência (sem
   FK, tipo + id) vale para as marcações de relatório (`REL_TB03_MARCACOES`, ver
   `relatorio_word.md`) e para as detecções de padrão (`PDR_TB03_DETECCOES`).
2. **Entidades canônicas** (`ENT_TB05_CANONICAS`) — a pessoa/telefone/local consolidado,
   com **evidências** (`ENT_TB06_CANONICA_EVIDENCIAS`) apontando registros de origem e
   **identificadores** (`ENT_TB08_CANONICA_IDENTIFICADORES`, ex.: CPF, telefone, e-mail, nome)
   que sustentam a convergência entre inquéritos (`convergencia.md`).

Toda entidade e toda relação passam por **revisão**: manual nasce `confirmada`; criada por
busca automática (IA) nasce `sugerida` (fica tracejada no grafo até o investigador confirmar ou
descartar — `CD_REVISAO`). Canônicas **não** têm revisão — a identificação de pessoas e a
criação manual já materializam direto, sem etapa de "sugestão" (a curadoria delas é decidir
apagar ou não, não confirmar/descartar).

### Fluxo de uso

1. Na **Mesa de Inquérito** (`web/mesa_inquerito.html`), abrir o modal de um nó e:
   - editar o comentário (`POST /api/investigacao/entidades/{id}/comentario`);
   - revisar (`sugerida`/`confirmada`/`descartada` — `POST .../revisao`); descartar uma
     entidade também descarta as relações que a tocam (aresta órfã não faz sentido no grafo);
   - criar relação com outro nó (`POST /api/investigacao/relacoes`);
   - fixar a posição no grafo (`POST .../posicao`, colunas `NU_POS_X`/`NU_POS_Y`; enviar `null`
     limpa e devolve o nó ao layout automático);
   - remover (`DELETE /api/investigacao/entidades/{id}`, cascata manual das relações antes da
     entidade — a FK não tem `ON DELETE CASCADE` aqui).
2. **Buscar entidades (IA)** — dropdown "IA" da mesa: roda os prompts ativos da biblioteca
   (`ENT_TB02_PROMPTS_BUSCA`) contra os dados do inquérito em background; tudo nasce
   `sugerida`, com justificativa e evidência (ver `busca_automatica_utils.py` acima).
3. **Identificar pessoas** — botão "Identificar pessoas": cruza contatos, agenda, ligações,
   chaves PIX, contas bancárias, contas de app **e os documentos financeiros do inquérito
   (RIF, SIMBA)**, de forma **determinística, sem IA**, e materializa as pessoas identificadas
   como canônica `pessoa` com identificadores, evidências e relações (ver detalhe completo
   acima, em "Mesa de investigação").
4. **Entidade canônica manual** — botão "Entidade canônica" cria pessoa/telefone/local/
   veículo/organização/outro (`POST /api/investigacao/canonicas`), com observação livre; nasce
   sem `NU_CHAVE` (só o builder automático usa chave de deduplicação).
5. Evidência manual: no modal da canônica, `POST /api/investigacao/canonicas/{id}/evidencias`
   aponta um registro de origem com `TX_DETALHE` (é assim que o investigador liga uma
   entidade-comentário existente a uma canônica manualmente, criando o vínculo que
   `listar_vinculos_evidencia` depois desenha).
6. Relação canônica ↔ canônica: `POST /api/investigacao/canonicas/relacoes`
   (insere em `ENT_TB07_CANONICA_RELACOES`; a `UNIQUE (origem, destino, tipo)` faz o `POST`
   repetido devolver a relação já existente em vez de duplicar).

### Como funciona

- `api/funcoes/entidades_utils.py` — `TIPOS_ORIGEM_VALIDOS` (50 tipos hoje — os 27 antigos mais
  os 21 domínios da auditoria de aproveitamento do UFDR, registrados em bloco por
  `_DOMINIOS_UFDR_ANOTAVEIS` já que todos têm a mesma forma, mais `dispositivo_conta` e
  `evento`, das tabelas genéricas de conta na nuvem preenchidas pelo IPED; é o que permite marcar
  para o relatório uma busca, uma conexão de Bluetooth ou uma transferência. Todo tipo novo
  precisa também de `LEFT JOIN` em `listar_entidades_inquerito`, senão o nó nasce sem rótulo e
  sem data e some da linha do tempo da mesa. Os 27 antigos: mensagens e
  contatos de WhatsApp/SMS/mensageiro genérico, agenda, ligação, localização, notificação,
  nota, navegação, e-mail, wifi, conta financeira, conta de usuário, aplicativo, dono, arquivo,
  e os tipos das fontes documentais — `rif_comunicacao`, `rif_envolvido`, `rif_contraparte`,
  `simba_conta`, `simba_lancamento`, `simba_contraparte`, `ipl_pessoa`, `ipl_achado`,
  `irpf_declaracao`/`irpf_bem`/`irpf_dependente`/`irpf_rendimento`/`irpf_divida`/`irpf_doacao`,
  `ipei_alvo`/`ipei_relacionado`/`ipei_conta`/`ipei_bem`/`ipei_nota`/`ipei_contato`,
  `telefonia_linha`/`telefonia_chamada`/`telefonia_numero`) e a
  função `_VERIFICACAO_ORIGEM` validam o par `TP_ORIGEM`/`ID_ORIGEM` contra a tabela de origem
  correspondente **e** que ela pertence ao `id_inquerito` informado — mesmo isolamento por
  caso do resto do app. `listar_entidades_inquerito` resolve, via `COALESCE` de `LEFT JOIN`s
  por tipo, um `rotulo_origem` e uma `dt_origem` legíveis para cada entidade, qualquer que seja
  a origem — é o que popula o nó no grafo e a linha do tempo.
- `api/funcoes/identificacao_pessoas.py` — ver detalhamento completo em "Mesa de investigação"
  acima (fontes, invariantes anti-fusão, materialização, diagnóstico).
- `api/funcoes/canonicas_utils.py` — CRUD de canônicas/evidências/relações canônicas e as
  consultas de convergência (`listar_convergencia_inquerito`, `listar_convergencia` —
  ver `convergencia.md`). `listar_canonicas` **não** traz os identificadores (`ENT_TB08`)
  junto — o dono do aparelho acumula dezenas deles, e o resumo já cabe em `TX_OBSERVACAO`.
  `listar_vinculos_evidencia` faz `JOIN` de `ENT_TB06` com `ENT_TB01` pelo mesmo
  `tp_origem`/`id_origem` **e** mesmo `id_inquerito`, filtrando `cd_revisao <> 'descartada'` do
  lado da entidade.
- `api/rotas/entidades.py` — todas as rotas acima; leitura de entidades e grafo exige
  `exigir_acesso_inquerito` (permissão do inquérito); criação/revisão exigem usuário aprovado
  e passam por `_exigir_acesso` (404 se o inquérito não existe, 403 sem permissão) ou checagem
  inline (`usuario_tem_acesso_inquerito`) nas rotas que já recebem `id_inquerito` no corpo.

## Modelo de dados

Todas em `INVESTIGACAO` (criadas em `db/init/21_entidades_padroes.sql` ):

| Camada | Tabela | Colunas principais |
|---|---|---|
| Bruta | `ENT_TB01_ENTIDADES` | `ID_ENTIDADE`, `ID_INQUERITO` (FK `FK65`), `TP_ORIGEM` (30), `ID_ORIGEM`, `TX_COMENTARIO`, `DT_CRIACAO`, `IN_AUTOMATICO` (default `false`, de `21_entidades_padroes.sql`), `CD_REVISAO` (`sugerida`/`confirmada`/`descartada`, default `confirmada`), `ID_BUSCA` (FK `FK95` → `ENT_TB04`, nulo se manual), `TX_JUSTIFICATIVA`, `NU_POS_X`/`NU_POS_Y` — as quatro últimas de |
| Bruta | `ENT_TB03_RELACOES` | `ID_RELACAO`, `ID_INQUERITO` (FK `FK74`), `ID_ENTIDADE_ORIGEM`/`ID_ENTIDADE_DESTINO` (FK `FK75`/`FK76` → `ENT_TB01`), `TX_TIPO_RELACAO` (texto livre, ex.: "contatou", "chave PIX citada por"), `IN_AUTOMATICO`, `CD_REVISAO`, `ID_BUSCA` (FK `FK96`), `TX_JUSTIFICATIVA` |
| Curada | `ENT_TB05_CANONICAS` | `ID_CANONICA`, `ID_INQUERITO` (FK `FK97`), `TP_ENTIDADE` (`pessoa`/`telefone`/`local`/`veiculo`/`organizacao`/`dispositivo`/`outro`), `NO_ENTIDADE`, `NU_CHAVE` (chave de deduplicação do builder automático — nula em canônica manual), `TX_OBSERVACAO`, `IN_AUTOMATICO`, `ID_USUARIO_CRIADOR` (FK `FK98`); `UNIQUE (ID_INQUERITO, TP_ENTIDADE, NU_CHAVE)` |
| Curada | `ENT_TB06_CANONICA_EVIDENCIAS` | `ID_EVIDENCIA`, `ID_CANONICA` (FK `FK99`, `ON DELETE CASCADE`), `TP_ORIGEM`, `ID_ORIGEM`, `TX_DETALHE`; `UNIQUE (ID_CANONICA, TP_ORIGEM, ID_ORIGEM)` |
| Curada | `ENT_TB07_CANONICA_RELACOES` | `ID_RELACAO`, `ID_INQUERITO` (FK `FK100`), `ID_CANONICA_ORIGEM`/`ID_CANONICA_DESTINO` (FK `FK101`/`FK102`, `ON DELETE CASCADE`), `TX_TIPO_RELACAO`, `IN_AUTOMATICO`, `ID_USUARIO_CRIADOR` (FK `FK103`); `UNIQUE (ORIGEM, DESTINO, TIPO)` — idempotência do builder automático |
| Curada | `ENT_TB08_CANONICA_IDENTIFICADORES` | `ID_IDENTIFICADOR`, `ID_CANONICA` (FK `FK156`, `ON DELETE CASCADE`), `ID_INQUERITO` (FK `FK157`, desnormalizado — a busca por identificador é sempre dentro do inquérito), `TP_IDENTIFICADOR` (`telefone`/`cpf`/`cnpj`/`email`/`usuario_app`/`nome` na pessoa e na empresa; `imei`/`iccid`/`imsi`/`serial`/`mac` no dispositivo; `placa` no veículo), `CO_VALOR` (300, normalizado), `TX_ORIGEM`; `UNIQUE (ID_CANONICA, TP_IDENTIFICADOR, CO_VALOR)` |
| Curada | `ENT_TB09_IMAGENS` | `ID_IMAGEM`, `ID_INQUERITO` (FK `FK258`), `TP_NO` (`canonica`/`entidade`, `CK12`), `ID_NO` (sem FK — alvo polimórfico), `NO_ARQUIVO`, `TP_MIME`, `NU_BYTES`, `IM_CONTEUDO` (BYTEA, a imagem já reduzida a 320 px e reencodada em JPEG), `ID_USUARIO` (FK `FK259`), `DT_ENVIO`; `UNIQUE (TP_NO, ID_NO)` — uma foto por nó, reenviar substitui |
| Mesa | `ENT_TB10_MESA_VISOES` | Visões salvas do quadro, **privadas por usuário**: `ID_VISAO`, `ID_INQUERITO` (FK `FK427`, `ON DELETE CASCADE`), `ID_USUARIO` (FK `FK428`, `ON DELETE CASCADE`, `NOT NULL` — dono), `NO_VISAO` (120), `JT_ESTADO` (JSONB opaco: filtros + fontes + limite + câmera, só a tela entende), `DT_CRIACAO`, `DT_ATUALIZACAO`; UNIQUE `UX117 (ID_INQUERITO, ID_USUARIO, NO_VISAO)` — salvar com o mesmo nome substitui (`db/init/49_visoes_mesa.sql`, migração `0056`) |
| Execução | `ENT_TB02_PROMPTS_BUSCA` | Biblioteca global de prompts da busca automática: `ID_PROMPT`, `NO_TITULO`, `TX_PROMPT`, `IN_ATIVO`, `ID_USUARIO_CRIADOR` (FK `FK73`) |
| Execução | `ENT_TB04_BUSCAS_AUTOMATICAS` | `ID_BUSCA`, `ID_INQUERITO` (FK `FK78`), `ID_USUARIO` (FK `FK79`), `TP_BUSCA` (`prompts`/`relacionar_manuais`), `CD_STATUS` (`pendente`/`processando`/`concluido`/`erro`), `TX_ERRO`, `QT_ENTIDADES_CRIADAS`, `QT_RELACOES_CRIADAS`, `DT_INICIO`/`DT_FIM` |
| Fontes dos nós | tabelas de `TIPOS_ORIGEM_VALIDOS` (mensagens, contatos, ligação, localização, notificação, nota, navegação, e-mail, wifi, contas financeira/usuário, aplicativo, dono, arquivo, RIF/SIMBA/IPL/IPEI/IRPF/telefonia, e as da nuvem `dispositivo_conta`/`evento`) | resolvidos por `TP_ORIGEM`/`ID_ORIGEM`; a mesma origem pode ser evidenciada por entidade e por canônica ao mesmo tempo (é o que gera o vínculo de evidência) |
| Extração | `INQ_TB02_EXTRACOES.QT_PESSOAS_IDENTIFICADAS` | `NULL` = extração ainda não passou pela identificação; `>= 0` = quantas pessoas ela contribuiu na última rodada (coluna de `21_entidades_padroes.sql`) |

Índices: `IX28`/`IX29` (`ENT_TB01` por inquérito/origem), `IX32`–`IX35` (`ENT_TB03`/`ENT_TB04`),
`IX44`/`IX45` (`ID_BUSCA` em `ENT_TB01`/`ENT_TB03`), `IX46`–`IX49` (canônicas/evidências/
relações canônicas), `IX99` (`ENT_TB08` por inquérito+tipo+valor — a busca de identificador que
sustenta fusão e convergência), `IX249` (`ENT_TB09` por inquérito — "quais nós têm foto?", uma
vez por carga do grafo), e os de convergência aproximada em
`db/init/21_entidades_padroes.sql` (ver `convergencia.md`).

## Referências

- Telas: `web/mesa_inquerito.html` (+ `js/mesa_inquerito.js`, `js/utils/grafo_filtros.js` e
  `js/utils/ia_flutuante.js` para o chat que filtra a mesa), `web/ia_mesa_inquerito.html`
  (abas "Prompts de entidade" e "Padrões de crime"), `web/convergencia.html`.
- Rotas: `api/rotas/entidades.py`, `api/rotas/busca_automatica.py`.
- Funções: `api/funcoes/{entidades_utils,identificacao_pessoas,canonicas_utils,
  busca_automatica_utils,padroes_utils,imagens_mesa,mesa_visoes_utils}.py`;
  `api/funcoes/ia_investigacao.py` (`ferramentas_para_busca_automatica`,
  `listar_entidades_existentes`); `api/funcoes/prompts_entidade_utils.py`
  (`listar_prompts_ativos`, biblioteca de `ENT_TB02`); `api/funcoes/ia_auditoria.py`
  (`registrar_auditoria`, origem `busca_automatica`).
- Esquema: `db/init/21_entidades_padroes.sql`.
- Relacionados: `exportar_grafo.md` (os botões "Exportar grafo" da barra: CSV/XLSX/ANX para o i2
  Analyst's Notebook e GraphML para Gephi/yEd/NetworkX, sempre sobre o grafo filtrado inteiro e
  não sobre o recorte desenhado — o recorte da mesa sai de `montarRecorteI2`, que leva CPF/
  telefone, nº de evidências, origem e o `tx_detalhe` de cada relação),
  `padroes_crime.md`, `convergencia.md` (consome `ENT_TB08` povoada aqui),
  `busca_semantica.md`, `auditoria.md` (turnos da busca automática), `permissoes_delegacia.md`,
  `extração_ufdr.md` e `extracao_iped.md` (fontes do aparelho), `rif.md`/`simba.md` (fontes
  financeiras da identificação de pessoas), `investigacao_celular.md`.
