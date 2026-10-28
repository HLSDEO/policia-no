# Convergência entre inquéritos


> **Atualização — sinais vindos da auditoria de aproveitamento do UFDR**
>
> - **Identificadores de aparelho** (`imei`, `iccid`, `imsi`, `serial`, `mac`) entraram nos
>   sinais EXATOS. Vêm da canônica `dispositivo`, materializada do `DeviceInfoEntry` pela
>   identificação de pessoas — antes o sinal `imei` só era alimentado por conta na nuvem
>   (Google Takeout) e ficava praticamente vazio. Na prática o **ICCID é o mais útil**: o chip
>   migra de celular, então ele reencontra a mesma LINHA em outro caso, coisa que o IMEI (que é
>   do aparelho) não pega.
> - **`co_presenca`** (aproximado): aparelhos de inquéritos diferentes que reconheceram o mesmo
>   dispositivo por perto. Liga dois investigados que estiveram no mesmo lugar sem nunca terem
>   se falado. O BSSID de roteador ficou **de fora** deste sinal: medido em produção, incluí-lo
>   fazia dois inquéritos "convergirem" por 1.256 endereços, e o primeiro exemplo era um hotspot
>   de operadora — equipamento público que qualquer um enxerga. Há ainda o corte estatístico
>   `_MAX_INQUERITOS_MAC`, que descarta endereço presente em inquéritos demais para ser encontro.
> **Atualização — CNPJ e placa (fontes documentais na mesa)**
>
> Quando a IPEI, o IRPF e a telefonia passaram a materializar **empresa** (`organizacao`,
> deduplicada pelo CNPJ) e **veículo** (deduplicado pela placa) como nó da mesa — ver
> `mesa_inquerito.md` —, `cnpj` e `placa` viraram identificadores de verdade em
> `ENT_TB08_CANONICA_IDENTIFICADORES`, e entraram nos sinais **EXATOS** da convergência, nos
> três caminhos (alerta do card, resumo da lista e visão global). A mesma empresa em dois
> inquéritos é sócio, empregador ou fonte pagadora em comum; o mesmo carro em dois inquéritos é
> o vínculo mais concreto que existe depois do documento da pessoa.
>
> **Corte de frequência só no CNPJ** (`_MAX_INQUERITOS_CNPJ`, hoje **5**): CNPJ presente em
> inquéritos demais é banco, operadora, prefeitura ou o grande empregador da região — a empresa
> que aparece na declaração de meio mundo. Mesma forma do `_MAX_INQUERITOS_MAC` da co-presença,
> mas mais folgado, porque CNPJ é documental e esparso e uma empresa de fachada compartilhada
> por quatro inquéritos relacionados é exatamente o desenho que se quer ver. **Placa não tem
> corte**: carro não é infraestrutura pública.

> - **`local_rede`** (aproximado): coordenada de roteador ou de antena em comum, com mínimo
>   próprio (`_MINIMO_REDES_LOCAL`). É pool SEPARADO do histórico de GPS de propósito — uma
>   varredura de Wi-Fi devolve dezenas de redes na mesma coordenada, e misturar as duas fontes
>   no mesmo limiar triplicava o sinal `local` (62 → 188 pares na medição).

> **Atualização — cache de 1 h + tela paginada no servidor (a tela "não carregava")**
>
> Em produção a consulta de convergência passou de **100 s** no Postgres (só o nome aproximado
> por trigrama sobre ~30 mil nomes leva ~96 s) e morria no `statement_timeout` de requisição
> (60 s) — a tela nunca abria, e o JSON com TODOS os sinais de TODOS os pares pesava dezenas de
> MB quando cabia. Duas mudanças, juntas:
>
> - **Cache `INVESTIGACAO.CONV_TB01_SINAIS`** (`db/init/50_cache_convergencia.sql`, migração
>   `0057`): o cruzamento completo (sem filtro de usuário) roda num **laço líder de hora em
>   hora** (`iniciar_cache_convergencia`, padrão `leader_utils`) na conexão longa
>   (`statement_timeout 0`) e grava os sinais prontos. A troca é `TRUNCATE + INSERT` numa
>   transação só — leitor nunca vê tabela pela metade. O botão **"Atualizar"** força o mesmo
>   recálculo sob advisory lock (`72313`): quem chega junto desiste, nada roda em dobro.
>   Medição com o limiar do `%%` fixado em `0.5` via `set_config`: ~96 s → ~42 s.
> - **Leituras paginadas no SQL**: as rotas viraram SELECT sobre o cache com os filtros
>   (tipo, confiança, busca, **"pelo menos X sinais"**, **"Score ≥ %"**) e a paginação de pares
>   aplicados no banco. O filtro de acesso (`_filtro_acesso_sql`) migrou da consulta grande para
>   a LEITURA, nos dois lados do par — a privacidade é a mesma, a tabela em si não tem filtro
>   nenhum. A tela baixa **uma página** (10 pares + 10 sinais de prévia cada); o modal, o grafo
>   (só arestas, `/arestas`) e o Excel/i2 (`/export`, o recorte filtrado inteiro) têm rota
>   própria.
>
> **Semântica do "Score ≥ %"**: quando ativo, ficam só os sinais **exatos** e os aproximados
> **com** score acima do corte; aproximados sem percentual (local, ponto de rede, co-presença)
> ficam de fora — é o filtro do "quero ver só o que é forte".

## Visão geral

Sinais que cruzam **mais de um inquérito** — do exato (CPF, CNPJ, telefone, e-mail, chave PIX,
identidade do aparelho, placa de veículo) ao aproximado (nome parecido, local visitado em
comum). É o tipo de cruzamento que só um
sistema faz: nenhum investigador lê dois inquéritos inteiros lado a lado atrás de um nome
parecido ou de um ponto de GPS repetido. Tela dedicada em `web/convergencia.html`, "Pares com
sinal em comum" — visão global entre **todos** os inquéritos visíveis ao usuário — mais um
**alerta automático** por inquérito, mostrado direto no card da investigação
(`web/js/investigacao.js`), com aviso explícito de que sinais **aproximados** são hipótese, não
confirmação. A restrição por permissão continua valendo dos **dois lados** do par: só entram
pares em que ambos os inquéritos são visíveis ao usuário (ver `permissoes_delegacia.md`).

Depende inteiramente da **identificação de pessoas** (`mesa_inquerito.md`) já ter populado
`ENT_TB08_CANONICA_IDENTIFICADORES` — sem identificador resolvido em canônica (CPF, CNPJ,
telefone, e-mail, nome, identidade de aparelho, placa), não há o que cruzar. Um inquérito sem nenhuma pessoa identificada nunca aparece em nenhum par.

## Fluxo de uso

1. **Alerta automático** (sem ação do usuário): ao abrir um inquérito em `web/investigacao.html`,
   o card consulta `GET /api/investigacao/inqueritos/{id}/convergencia` e, se houver
   identificador em comum com outro inquérito visível, mostra "⚠ Convergência detectada —
   Este inquérito compartilha N identificador(es) com `<número do outro inquérito>`" (um bloco
   por inquérito relacionado, com link direto). **Só sinais exatos**
   (`_IDENTIFICADORES_CONVERGENCIA`: CPF, CNPJ, telefone, e-mail, IMEI/ICCID/IMSI/série/MAC e
   placa) — nome aproximado, local e co-presença **não** entram aqui (ver "Como funciona").
2. Para a visão completa, abrir "Convergência entre inquéritos" no menu.
3. Filtrar e buscar — tudo aplicado **no servidor**, sobre o cache de sinais:
    - tipo de sinal (`filtro-tipo`): CPF, CNPJ, Telefone, E-mail, Nome aproximado,
      IMEI/ICCID/IMSI/série/MAC, Placa de veículo, Local em comum, Ponto de rede em comum e
      Aparelho em comum por perto (ou "Todos os tipos"). A lista do `<select>` e o
      `ROTULO_TIPO`/`CONFIANCA_ORDEM` de `js/convergencia.js` espelham
      `_ROTULO_IDENTIFICADOR`/`_SINAIS_EXATOS` do back-end — **todo tipo aproximado precisa
      estar em `CONFIANCA_ORDEM`**, porque o `|| 0` da comparação trata ausente como exato;
    - confiança (`filtro-confianca`): exata e aproximada, só exata, só aproximada;
    - **mínimo de sinais** (`filtro-min-sinais`): "≥ 2 / 5 / 10 / 25 / 50 / 100 sinais" —
      corta os pares fracos (`HAVING count(*) >= X` já com os demais filtros aplicados; é o
      filtro dos "188 pares de ruído");
    - **score mínimo** (`filtro-min-score`): "Score ≥ N %" — mantém os **exatos** e os
      aproximados **com** `vl_score ≥ N%`; aproximados **sem** score (local, ponto de rede,
      co-presença) ficam de fora quando ativo (decisão do produto: é o corte do "só o forte";
      para voltar a vê-los, zere o campo). Chegada em %, o servidor divide por 100;
    - ordem (`filtro-ordem`): mais sinais primeiro (padrão) ou número do inquérito;
    - busca livre (`convergencia-busca`, sem caixa, com espera de 300 ms; `ILIKE`): casar
      com o **número do inquérito** mantém o par inteiro; casar com **valor, entidade ou tipo**
      mantém só os sinais que casam (mesma semântica de antes, agora no SQL).
    Os filtros vão como query string (`tipo`, `confianca`, `busca`, `min_sinais`, `min_score`,
    `ordem`, `pagina`) e são compartilhados por lista, arestas, modal e exportação — o que se
    exporta é exatamente o recorte que a tela mostra.
    O botão **"⬇ Excel"** (`conv-inq-excel`) exporta o conjunto filtrado **inteiro** com **uma
    linha por sinal** (`/convergencia/export`) — os dois inquéritos do par viram duas colunas da
    linha. Não é o que está desenhado: a lista pagina de 10 em 10 pares e cada card mostra só
    uma prévia dos sinais, então exportar o DOM daria uma fração do que o filtro selecionou.
4. Visualizar em **Lista** ou **Grafo** (`visao-lista-btn`/`visao-grafo-btn`):
   - **Lista** — um card por par de inquéritos, **10 pares por página**
     (`convergencia-paginacao`, mesma marcação de `investigacao.html`). Como um par de dois
     inquéritos grandes passa de mil sinais em produção, o card mostra só os **10 primeiros**
     sinais (exatos primeiro) e um rodapé "Mostrando 10 de N sinal(is) · Ver todos os sinais"
     que abre o modal com o resto — pôr tudo no DOM trava a tela;
   - **Grafo** — o quadro inteiro é o kit da Mesa de Inquérito transportado para cá (mesmas
      classes de `css/mesa.css`, mesmo worker `js/mesa_layout_worker.js`): cada **nó é um
      inquérito** (não uma pessoa) e cada **aresta é um par convergente** — verde sólida se o
      par tem algum sinal exato, laranja tracejada se só tem aproximado; espessura cresce com
      o número de sinais (`min(1 + qt*0.6, 5)`); raio do nó cresce com o grau. Clicar num nó
      lista os pares dele; clicar numa aresta (linha invisível larga por cima, fácil de mirar)
      abre o modal do par. **Layout**: Fruchterman-Reingold no **Web Worker** (não trava a UI;
      síncrono em chunk via `requestAnimationFrame` sem Worker, radial O(n) acima de 500 nós),
      área que cresce com o número de nós, semente reprodutível por comunidade e passe
      anti-colisão; o layout só refaz quando o CONJUNTO de nós muda, e arrastar um nó o fixa
      na posição escolhida (em memória, sobrevive a re-filtrar na sessão). **Gestos**: roda do
      mouse, pan, pinch de dois dedos, botões +/−/"Enquadrar tudo"/tela cheia
      (`conv-viewport-controls`) e minimapa (`mesa-minimapa`, some em redes < 25 nós).
      **Rótulos sob demanda**: acima de 30 nós os números somem quando se afasta e voltam no
      zoom ou no hover — de longe o que importa é a FORMA da rede. **Hover** acende o nó e os
      vizinhos direto e esmaece o resto (`mesa-grafo-com-foco`). **Comunidades** (label
      propagation sobre as arestas do recorte): nó ganha a cor do grupo, a legenda
      (`conv-legenda-comunidades`) é clicável e isola o grupo sem refazer a posição dos que
      ficam. **Elos críticos** (Tarjan — `conv-elos-criticos`): pontes em vermelho tracejado e
      pontos de articulação com anel vermelho — o par que é a única porta entre dois grupos de
      casos. **Análise** (`conv-analise-btn`): modal com hubs, articulação, pontes e
      comunidades, cada item clicável (abre o nó/par ou isola o grupo). **PNG**
      (`conv-exportar-png`) via `js/utils/exportar_png.js`: conteúdo inteiro, 2x, com título,
      filtros e legenda (rótulos entram à força — no papel nenhum número a menos ajuda).
5. Clicar em um par abre o modal `modal-par` com os sinais detalhados
   (`par-titulo`/`par-corpo`): tipo do identificador, badge de confiança (exata/aproximada +
   score em % quando houver), valor e as entidades de cada lado (`no_entidade_a`/
   `no_entidade_b`), ordenados com os sinais exatos primeiro. O modal pagina de **50 em 50** e,
   quando o par passa de 50 sinais, ganha uma busca própria (valor, entidade ou tipo). Ele
   trabalha sobre o par **já filtrado** pela barra da página — se a busca da lista reduziu o
   par a 3 sinais, é isso que o modal mostra.
6. "Atualizar" (`atualizar-btn`) refaz a consulta; o total (`convergencia-totais`) mostra pares
   e soma de sinais do filtro atual.

## Como funciona

### Os seis sinais (`_QUERY_SINAIS_CONVERGENCIA` + cache, `api/funcoes/canonicas_utils.py`)

Um único `UNION ALL` de seis `SELECT`s (CTE `sinais`), do mais forte ao mais ruidoso. O
resultado bruto (par, sinal, score, valor, entidades dos dois lados) é o que vai para
`CONV_TB01_SINAIS`; o agrupamento por par, os filtros e a paginação são do lado da leitura
(`listar_convergencia` e cia.):

1. **Identificador exato** — `ENT_TB08_CANONICA_IDENTIFICADORES` unida consigo mesma por
   `CO_VALOR` igual e mesmo `TP_IDENTIFICADOR IN (_IDENTIFICADORES_CONVERGENCIA)` — hoje
   `cpf`, `cnpj`, `telefone`, `email`, `imei`, `iccid`, `imsi`, `serial`, `mac` e `placa` (o
   e-mail cobre também chave PIX de tipo e-mail, gravada com esse mesmo `TP_IDENTIFICADOR` pela
   identificação de pessoas). A lista é **uma constante só**, usada pelas três consultas, para
   elas não divergirem com o tempo — foi o que aconteceu antes, quando os identificadores de
   aparelho entraram na visão global e a documentação ficou falando de três tipos.
   `_sql_cnpj_nao_frequente()` recorta o CNPJ presente em mais de `_MAX_INQUERITOS_CNPJ` (5)
   inquéritos. Score `1.0`. `e8_b.id_inquerito > e8_a.id_inquerito` evita o par espelhado e a
   autoconvergência dentro do mesmo inquérito.
2. **Nome aproximado** — similaridade **trigrama** do `pg_trgm` (operador `%%`, que usa o
   índice `IX142` antes de calcular `similarity()`) sobre `TP_IDENTIFICADOR = 'nome'`, exigindo
   `similarity() >= _SIMILARIDADE_MINIMA_NOME` (**0.5**). O nome já vem normalizado pela
   identificação de pessoas (`_nome_completo` em `identificacao_pessoas.py`: maiúsculo, sem
   acento, sem conector, tokens ordenados) — a comparação é sobre essa forma canônica, não o
   nome bruto digitado em algum lugar. Valor exibido: o nome sozinho quando os dois lados são
   idênticos (caso raro, coincidência total após normalizar), ou `"nome_a ≈ nome_b"` quando
   diferem; score é o `similarity()` cru.
3. **Mesmo IMEI** — `DVC_TB01_DISPOSITIVOS` unida por `TX_IMEI` igual, exigindo
   `length(trim(tx_imei)) >= 14` (IMEI de verdade, não um campo vazio ou truncado). Aparelho
   físico repetido é sinal forte, mas de **cobertura baixa hoje**: `DVC_TB01` só é populada
   pela ingestão de contas na nuvem (hoje a Nuvem IPED, quando o caso traz o produto Android
   Device Configuration Service — ver `extracao_iped.md`); o UFDR não grava o IMEI do próprio
   aparelho numa tabela própria. Score `1.0`.
4. **Local em comum** — CTE `locais_agrupados` arredonda `VL_LATITUDE`/`VL_LONGITUDE` a **4
   casas decimais (~11 m)** e agrupa por inquérito + coordenada arredondada, exigindo
   `count(*) >= _MINIMO_PONTOS_LOCAL` (**3**) registros de GPS no mesmo ponto — um único
   registro seria passagem/coincidência de rota, não local relevante. O `JOIN` entre CTEs
   iguala `(lat, lon)` de inquéritos diferentes. Score `NULL` (não há grau de "parecido" aqui:
   ou a coordenada arredondada bate, ou não bate) — a UI trata `vl_score` nulo como
   "aproximado" sem percentual. Endereço/descrição exibidos são o `max()` de cada lado (texto
   livre, pode variar entre registros do mesmo ponto).

`_SINAIS_EXATOS` (CPF, CNPJ, telefone, e-mail, IMEI/ICCID/IMSI/série/MAC, placa) decide
`tp_confianca` ("exata"/"aproximada"), resolvido já no `CASE` do `SELECT` final da consulta
pesada — **note que "nome" nunca é exato**, mesmo quando os dois valores batem byte a byte após
normalizar (homônimos existem; o investigador ainda precisa checar). O cache **não** guarda nada
por usuário: o filtro de acesso (`_filtro_acesso_sql`, aliases `i_a`/`i_b`) entra na **leitura**,
no `JOIN` de `CONV_TB01_SINAIS` com `INQ_TB01_INQUERITOS` dos dois lados (`_filtros_convergencia_sql`,
compartilhado por lista/arestas/exportação para nunca divergirem). Admin não tem filtro
(`ve_todas_delegacias` devolve `""`). A delegacia também é filtro de leitura, e só estreita.
Ordenações: lista `qt_sinais DESC, nu_a, nu_b` (ou `nu_a, nu_b`); sinais do par, exatos primeiro
e score descendente — a mesma marca visual de antes, agora no `ORDER BY` do SQL.

### Visão por inquérito (`listar_convergencia_inquerito`) — mais restrita de propósito

Alimenta o alerta automático do card da investigação. **Só cobre os sinais exatos**
(`WHERE tp_identificador IN (_IDENTIFICADORES_CONVERGENCIA)`, com o mesmo recorte de CNPJ
frequente) — nome aproximado, local, ponto de rede e co-presença **ficam de fora** deste
caminho. A razão: o alerta aparece sem o usuário pedir, toda vez que o
inquérito é aberto, então precisa de sinal confiável o bastante para não virar ruído recorrente;
sinal aproximado exige a curadoria da tela dedicada (badge, score, contexto lado a lado), não uma
notificação de canto de tela. Para ver convergência por nome/local/co-presença **envolvendo um
inquérito específico**, o caminho é abrir "Convergência entre inquéritos" e buscar pelo número
do inquérito na barra de busca — os pares dele ficam com todos os sinais.

### Suporte de índices (`db/init/21_entidades_padroes.sql`)

| Índice | Tabela/condição | Sinal |
|---|---|---|
| `IX142_ENT_TB08_NOME_TRGM` | GIN `gin_trgm_ops` em `CO_VALOR` `WHERE TP_IDENTIFICADOR = 'nome'` | 2 (nome aproximado) |
| `IX143_LOC_TB01_COORD_ARRED` | B-tree em `(round(VL_LATITUDE,4), round(VL_LONGITUDE,4))` `WHERE` latitude/longitude não nulas | 4 (local em comum) |
| `IX144_DVC_TB01_IMEI` | B-tree em `TX_IMEI` `WHERE TX_IMEI IS NOT NULL AND length(trim(TX_IMEI)) >= 14` | 3 (mesmo IMEI) |

A extensão `pg_trgm` já vinha habilitada por `db/init/01_schemas.sql` (usada pela busca
textual fuzzy do resto do sistema) — o que este recurso acrescentou foi só o índice específico
para `CO_VALOR` de nome.

## Aba "Casos ↔ acervo" (removida)

Esta aba cruzava os **Casos de documentos diversos** com o resto do acervo e foi removida junto
com o subsistema antigo de casos: o caso de inteligência agora é um inquérito com a flag
`IN_CASO_INTELIGENCIA` e já participa da convergência **entre inquéritos** normalmente (ver
[documentos_diversos.md](documentos_diversos.md)). Removidos: a aba `#aba-casos-btn` e o bloco
`abaCasos()` em `js/convergencia.js`, o card `#conv-casos-card` em `convergencia.html`,
`listar_convergencia_casos` em `canonicas_utils.py` e a rota
`GET /api/investigacao/convergencia-casos`.

## Modelo de dados

Uma tabela própria — o **cache** `CONV_TB01_SINAIS` (`db/init/50_cache_convergencia.sql`) — mais
as tabelas já existentes (`INVESTIGACAO`). O cache guarda o resultado bruto da consulta, **sem**
restrição de usuário (quem restringe é a leitura); um novo identificador materializado pela
identificação de pessoas entra na convergência no próximo recálculo (laço de hora em hora ou
"Atualizar"):

| Tabela | Papel |
|---|---|
| `CONV_TB01_SINAIS` | Cache dos sinais calculados (par, tipo, confiança, score, valor, entidades, `DT_GERACAO`) — fonte exclusiva das leituras da tela |
| `ENT_TB08_CANONICA_IDENTIFICADORES` | `CO_VALOR` (normalizado) + `TP_IDENTIFICADOR` (`cpf`/`telefone`/`email`/`nome`/`usuario_app`) por canônica — fonte dos sinais 1 e 2; povoada pela identificação de pessoas (`identificacao_pessoas.py`) e pela criação manual de identificador |
| `ENT_TB05_CANONICAS` | `NO_ENTIDADE` de cada lado do par (rótulo mostrado na tabela/modal) |
| `DVC_TB01_DISPOSITIVOS` | `TX_IMEI`, `NO_DISPOSITIVO` por extração — sinal 3 |
| `LOC_TB01_LOCALIZACOES` | `VL_LATITUDE`/`VL_LONGITUDE`, `TX_ENDERECO`, `TX_DESCRICAO` por extração — sinal 4 |
| `INQ_TB01_INQUERITOS` / `INQ_TB02_EXTRACOES` | Números (`NU_INQUERITO`) e vínculo extração→inquérito, usados para o filtro de acesso e o rótulo dos nós |

## Configuração

Sem variáveis de `.env`. Os limiares que controlam ruído são constantes em
`canonicas_utils.py`, não expostos como configuração administrável (ao contrário dos pesos de
alerta do SIMBA — ver `simba.md`): `_SIMILARIDADE_MINIMA_NOME = 0.5`,
`_MINIMO_PONTOS_LOCAL = 3`, `CACHE_CONVERGENCIA_INTERVALO_S = 3600`. Mudar exige editar o código
e reiniciar a API. As chaves de advisory lock do laço/refresh são `72312`/`72313` (lista no
cabeçalho de `leader_utils.py`).

## Referências

- Telas: `web/convergencia.html` (JS: `js/convergencia.js`); alerta automático em
  `web/investigacao.html` (JS: `web/js/investigacao.js`).
- Rotas: `api/rotas/investigacao.py` — `GET /api/investigacao/convergencia` (pares paginados,
  filtros no servidor), `/convergencia/par` (sinais de um par), `/convergencia/arestas` (grafo),
  `/convergencia/export` (Excel/i2), `/convergencia/status` + `POST /convergencia/recalcular`
  (cache), todas `exigir_aprovado`; e
  `GET /api/investigacao/inqueritos/{id}/convergencia` (por inquérito, `exigir_acesso_inquerito`).
- Funções: `api/funcoes/canonicas_utils.py` (`atualizar_cache_convergencia`,
  `iniciar_cache_convergencia`, `listar_convergencia`, `listar_convergencia_par`,
  `listar_convergencia_arestas`, `exportar_convergencia`, `status_cache_convergencia`,
  `listar_convergencia_inquerito`, `_SINAIS_EXATOS`, `_SIMILARIDADE_MINIMA_NOME`,
  `_MINIMO_PONTOS_LOCAL`), `api/funcoes/leader_utils.py` (laço líder),
  `api/funcoes/investigacao_utils.py` (`_filtro_acesso_sql`). O laço sobe no startup da API
  (`main.py`).
- Esquema: `db/init/50_cache_convergencia.sql` (cache), `db/init/21_entidades_padroes.sql`,
  `db/init/01_schemas.sql` (extensão `pg_trgm`).
- Relacionados: `exportar_grafo.md` (os quatro formatos do botão de exportação sobre o grafo),
  `exportar_excel.md` (o "⬇ Excel" das duas abas), `mesa_inquerito.md` (identificação de pessoas
  povoa `ENT_TB08`; layout de grafo compartilhado), `extracao_iped.md` (única fonte de `DVC_TB01_DISPOSITIVOS` hoje),
  `permissoes_delegacia.md` (`_filtro_acesso_sql`).