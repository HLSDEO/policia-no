# Exportação de grafo (i2 Analyst's Notebook e GraphML)

## Visão geral

Toda tela que desenha grafo exporta o grafo em **quatro formatos** — três para o **i2 Analyst's
Notebook** (CSV, XLSX, ANX) e um para as ferramentas de análise de rede de fora do i2
(**GraphML**: Gephi, yEd, Cytoscape, NetworkX). A escrita dos quatro mora num módulo só,
`web/js/utils/exportar_i2.js` (`window.ExportarI2`), e cada tela entrega apenas o **recorte**
dela — a lista de nós e de ligações já filtrada.

Por que exportar e não só desenhar: o i2 é a ferramenta em que a análise de vínculo continua
depois daqui, e quem monta a carta do processo precisa dos nós e das ligações como **dado**, não
como figura (para a figura existe o `ExportarPNG`). O GraphML atende a outra pergunta: no i2 se
monta a carta, no Gephi se **mede** a rede (centralidade, comunidade, caminho mínimo) em grafo
grande demais para ler no desenho.

## Telas que exportam

| Tela | Arquivo | Recorte |
|---|---|---|
| Grafo do RIF | `web/grafo_rif.html` / `js/grafo_rif.js` | via `GrafoFluxo` |
| Grafo do extrato SIMBA | `web/grafo_simba.html` / `js/grafo_simba.js` | via `GrafoFluxo` |
| Mesa de inquérito | `web/mesa_inquerito.html` / `js/mesa_inquerito.js` | `montarRecorteI2` |
| Grafo do caso | `web/caso_grafo.html` / `js/caso_grafo.js` | `montarRecorteI2` |
| Convergência entre inquéritos | `web/convergencia.html` / `js/convergencia.js` | `montarRecorteI2` |
| Grafo de localizações | `web/localizacoes.html` / `js/localizacoes_grafo.js` | `montarRecorteI2` |

RIF e SIMBA passam pelo motor compartilhado `js/utils/grafo_fluxo.js`, que monta o recorte a
partir do grafo de fluxo financeiro (`_recorteI2`) e expõe `exportarCSV` / `exportarXLSX` /
`exportarANX` / `exportarGraphML`. As outras quatro chamam `ExportarI2.ligarBotoes(montarRecorte)`,
que amarra os botões `#exportar-i2-csv|xlsx|anx|graphml` da barra da tela — botão que não existe
na página é ignorado, então uma tela pode oferecer só parte dos formatos.

**Regra de recorte, igual nas seis telas**: exporta-se o grafo **filtrado inteiro**, nunca o
recorte desenhado. O teto de nós das telas existe porque o layout por força trava o navegador; a
exportação não desenha nada, então não tem por que cortar — quem filtrou para 4 mil nós quer os
4 mil no i2, não os 100 que couberam no desenho. `montarRecorte` é chamado **no clique**, não na
carga, porque o grafo muda com filtro, busca e legenda.

## Os quatro formatos

| Formato | Para que serve | Como sai |
|---|---|---|
| **CSV** | Importação genérica do i2 e conferência em qualquer lugar | Uma linha por nó e por ligação na mesma tabela, com a coluna `registro` (`node`/`edge`) dizendo qual é qual; separador `;` e **BOM UTF-8** (sem o BOM o Excel abre em ANSI e todo acento vira caractere solto). As colunas de atributo são a **união** das de nó e de vínculo |
| **XLSX** | Assistente de importação do i2, mapeando entidade e vínculo separadamente | Duas abas — **Entidades** (`Entity ID`, `Label`, `Type`, `Username`, `Image URL` + atributos do nó) e **Vinculos** (`Link ID`, `From/To Entity ID`, `Type`, `Label`, `Weight`, `Symmetric`, `Common Followers/Following`, `Summary` + atributos do vínculo). Cada aba leva só as **suas** colunas. Gerado pelo `ExportarExcel.gerarXlsxMultiSheet` (ver `exportar_excel.md`) |
| **ANX** | A carta já montada, abre direto no Analyst's Notebook | XML com `EntityTypeCollection` (uma cor por tipo de entidade), `LinkTypeCollection` (uma cor por tipo de ligação) e `ChartItemCollection`. Ligação simétrica sai `ArrowOnBoth`, o resto `ArrowOnHead` |
| **GraphML** | Gephi, yEd, Cytoscape, NetworkX — medir a rede fora do i2 | Uma `<key>` declarada por atributo e um `<data>` por item. `label` e `weight` usam esses nomes exatos porque são os que o Gephi reconhece sozinho como rótulo do nó e peso da aresta |

Arquivo baixado: `<prefixo>-grafo-i2-AAAAMMDD-HHMM.<ext>` (CSV/XLSX/ANX) e
`<prefixo>-grafo-AAAAMMDD-HHMM.graphml` — o "i2" no meio do nome não valeria para o GraphML.

## Contrato do recorte

A tela entrega um objeto já pronto:

```js
{
  prefixo: "mesa-inquerito",                                // começo do nome do arquivo
  entidades: [ { chave, rotulo, tipo, peso?, atributos? } ],
  vinculos:  [ { de, para, rotulo?, tipo?, peso?, simetrico?, resumo?, atributos? } ]
}
```

- **`atributos`** é `{ "Rótulo na carta": valor }` com o que a tela sabe e o desenho não cabe
  mostrar: CPF/CNPJ, valor movimentado, período, nº de lançamentos, banco, nº de evidências. É a
  diferença entre a carta dizer "pagou a" e dizer "pagou R$ 45.320,00 em 12 lançamentos, entre
  03/02/2026 e 28/07/2026" — sem isso o i2 recebe a topologia e perde o caso. Valor vazio
  (`null`, `""`, `undefined`) é **omitido**, para a coluna não existir só de enfeite; **zero e
  `false` ficam** ("0 lançamentos" é informação, e sumir com ela deixaria a célula parecendo "não
  apurado"). A ordem das colunas é a **de escrita**, não alfabética: quem monta o recorte escreve
  os campos na ordem em que se lê o nó, e alfabetar embaralharia a leitura.
- **`chave`** é o id do nó *dentro da tela* (string, índice, `id_no` do servidor — o que for), e é
  por ela que `de`/`para` acham as pontas. O id que vai para o arquivo é derivado do **rótulo**,
  não da chave: na carta o id aparece, e `e1427` não diz nada a quem lê, enquanto `joao_da_silva`
  diz. Homônimos ganham sufixo numérico (`joao_da_silva_1`).
- **Vínculo com ponta fora do recorte é descartado em silêncio** — a tela filtra nós, e uma
  ligação para um nó não exportado viraria entidade fantasma na carta.
- **`peso`** costuma ser o grau do nó dentro do recorte exportado: é o que o i2 usa para
  dimensionar a entidade, e o número de ligações é a leitura de importância que sobrevive à
  troca de ferramenta.

## Decisões que não são óbvias

- **Atributo no ANX vai na descrição, não como `Attribute` do i2.** O ANX tem atributo de verdade
  (`AttributeClassCollection`), mas o nome exato dos elementos varia entre versões do Analyst's
  Notebook, e errar ali faz o arquivo ser **recusado na abertura** — perder o formato que abre
  direto seria pior que a informação ficar num campo só. A descrição é visível no cartão do item
  em qualquer versão; para atributo mapeável campo a campo existem o CSV e o XLSX. A quebra de
  linha vai como `&#10;` porque o XML normaliza quebra literal dentro de valor de atributo para
  espaço, e a descrição chegaria numa linha só.
- **Id de `<key>` no GraphML é sequencial (`n0`, `e2`), não o nome do campo.** O nome vem de
  rótulo em português com acento e espaço ("Nº de lançamentos"), e id de GraphML é `NMTOKEN` — o
  arquivo seria recusado no parse. O nome legível vai em `attr.name`, que é o que aparece na
  ferramenta.
- **GraphML mistura aresta dirigida e não dirigida no mesmo grafo**: `edgedefault="directed"`, e a
  ligação sem sentido (evidência, "esteve junto de") sai marcada uma a uma com
  `directed="false"` — evita inventar seta onde não há remetente e destinatário.
- **Cor por tipo no ANX**: sem `Colour` por tipo de entidade e por tipo de ligação a carta abre
  com tudo da mesma cor, e o tipo, que é a informação mais rápida de ler, se perde.
- **O id sai em ASCII**: o slug passa por NFD e remove as marcas de combinação. A classe de
  diacríticos é montada com `String.fromCharCode` em vez de escrita literal porque no fonte esses
  caracteres são invisíveis, e o primeiro editor que reinterpretar a codificação do arquivo os
  corrompe sem ninguém notar.

## Referências

- Módulo: `web/js/utils/exportar_i2.js` (`ExportarI2.csv/xlsx/anx/graphml/ligarBotoes`).
- Motor compartilhado de RIF e SIMBA: `web/js/utils/grafo_fluxo.js` (`_recorteI2`,
  `exportarCSV`/`exportarXLSX`/`exportarANX`/`exportarGraphML`).
- Marcação dos botões: `<span class="i2-grupo">` com o rótulo "Exportar grafo:" nas seis páginas;
  CSS em `web/css/style.css` (`.i2-grupo`, `.i2-grupo-rotulo`, `.conv-grafo-barra`).
- Relacionados: `exportar_excel.md` (o `gerarXlsxMultiSheet` que a aba do XLSX usa), `rif.md`,
  `simba.md`, `mesa_inquerito.md`, `convergencia.md`, `documentos_diversos.md` (grafo do caso),
  `investigacao_celular.md` (grafo de localizações).
