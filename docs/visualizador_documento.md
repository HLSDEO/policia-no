# Visualizador de documento

## Visão geral

Uma tela única (`web/documento.html`) para **ler** um documento anexado — o PDF do IPL, a
declaração de IRPF, o parecer, a planilha, o parecer em Word, o HTML exportado de sistema —, com o
texto fatiado nas páginas do arquivo, busca dentro do documento e realce do trecho que a IA citou.
Em PDF até **300 páginas**, o arquivo de verdade também é desenhado na própria tela (pdf.js
vendorizado), no modo **Lado a lado** — que é o padrão: a leitura nasce com o texto da IA e o papel
conferindo-se mutuamente. O **.docx** se lê no modo **Formatado** (títulos, listas e tabelas de
verdade, no lugar do `<pre>`) e o **.html** no modo **Renderizado** (o original num `<iframe>` sem
scripts) — os dois em volta do mesmo texto que a IA lê, nunca no lugar dele.

Antes dela, o texto extraído só aparecia num modal com um `<pre>` corrido: sem página, sem busca e
sem acesso ao arquivo original. E a citação da IA que dizia
"está na página 7" não tinha para onde levar — porque **não existia número de página**: a extração
emendava as páginas do PDF com uma linha em branco e jogava fora a quebra. O número que a IA
mencionava era o carimbo impresso no rodapé, que é a **folha do processo** e não corresponde à
página do arquivo.

Duas mudanças resolvem isso:

1. a extração passa a gravar o marcador `[[página N]]` no próprio texto — a IA lê e cita a página
   certa, e a tela sabe onde cada página começa;
2. a citação `[doc:ID:PÁGINA]` do chat vira link para a tela, já na página e no trecho citados.

## Fluxo de uso

- **Pela lista de importações** (`investigacao.html`, aba "Outros documentos"): o botão
  **Abrir documento** aparece no bloco do documento genérico, no do inquérito em PDF (IPL) e no da
  declaração de IRPF — os três saem do mesmo PDF anexado, e o botão volta ao papel de origem.
- **Pela citação da IA**: no chat do inquérito, a pastilha 📄 ao lado da citação
  abre a tela na página citada, com o trecho realçado e a
  navegação ‹ › entre as ocorrências. A ℹ️ colada nela abre o modal de cadeia de custódia
  do documento (apelido, arquivo, hashes — `js/utils/evidencia_info.js`).
- **Na tela**: o sumário à esquerda lista as páginas com prévia; a busca realça e conta as
  ocorrências, mostrando em quais páginas caíram; **Original** abre o arquivo como ele é numa aba
  nova; **Reextrair** relê o arquivo e regrava o texto. O seletor de modos troca o que se lê e só
  oferece o que o documento é: em PDF, **Texto | PDF | Lado a lado** (padrão: lado a lado); em
  .docx, **Texto | Formatado**; em .html, **Texto | Renderizado** (a preferência fica no
  `localStorage` e vale só para quem o formato aceita).

## Como funciona

### O marcador de página

`api/funcoes/documentos_utils.py`:

- `_juntar_paginas(paginas)` emenda as páginas colocando `[[página N]]` sozinho numa linha antes de
  cada uma. Documento de **uma página só sai sem marcador**: numerar a única página é ruído no
  texto que a IA lê.
- `paginas_do_texto(texto)` desfaz a emenda; `texto_paginado(texto)` diz se o texto tem marcador.
- Ficar sozinho na linha é o que torna o marcador seguro de fatiar de volta: um `[[página 3]]` no
  meio de uma frase do documento é conteúdo, não quebra.

O que cada formato considera "página":

| Formato | Página |
|---|---|
| PDF | a página do arquivo (`pypdf`) |
| XLSX | uma por aba — é a unidade que se navega numa planilha; o `### <aba>` logo abaixo do marcador diz qual é |
| DOCX | as quebras de página do XML (`w:br type="page"` e `w:lastRenderedPageBreak`) |
| CSV, TXT, HTML | nenhuma — saem sem marcador, e a tela mostra folha única |

DOCX gerado por script não tem nenhuma das duas marcas e sai sem paginação: inventar uma
contagem por linhas apontaria a página errada, que é pior do que não apontar nenhuma.

### O que isso custa

O marcador entra em `TX_CONTEUDO` — ou seja, **muda o texto que a IA lê e o que já está indexado no
RAG**. Documento anexado antes desta feature não tem marcador nenhum: a tela cai para página única,
explica o porquê e oferece o botão **Reextrair**. `reextrair_texto_documento(escopo, id_pai,
id_documento)` relê o arquivo original em `data/`, regrava `TX_CONTEUDO`/`QT_CARACTERES`/`TX_ERRO`
e, no inquérito, apaga os embeddings daquele documento (`EMB_TB01`) — sem isso a busca semântica
continuaria respondendo com o texto antigo, porque a indexação só pega o que ainda não está lá.

### A tela

`web/documento.html` + `web/js/documento.js` + `web/css/documento.css`. O inquérito vem da URL:

    documento.html?inquerito=<id>&doc=<id>    -> /api/investigacao/inqueritos/<id>/documentos/<id>/...

Parâmetros da citação: `&pagina=<n>` e `&trecho=<texto>`.

O botão **Original** abre o arquivo em aba nova. O `<iframe>` com o viewer nativo continua morto
pelo `X-Frame-Options: DENY` (`nginx/default.conf`), que barra o enquadramento até de mesma
origem — e não se abre mão do header por uma tela. Em aba nova a âncora `#page=N` ainda passa a
valer, o que dentro de `<iframe>` o visualizador do Chrome ignorava de todo jeito. O PDF
**dentro** da tela resolve isso por outro caminho: sem iframe (ver abaixo).

### O PDF dentro da tela (pdf.js)

`web/js/documento_pdf.js` + `web/vendor/pdfjs/` (build legado 3.11.174 + worker + `standard_fonts/`,
tudo na mesma origem — a CSP `script-src 'self'` não abre externa; o worker do pdf.js é o que
pediu o `worker-src 'self' blob:` acrescentado à CSP nos dois `add_header` do nginx). O pdf.js
desenha o PDF em `<canvas>` dentro da própria página: nenhuma moldura, nenhum header relaxado.

- **Modos**: Texto | PDF | Lado a lado (`analyser-doc-modo` no `localStorage`; padrão `lado`).
  `aplicarModo` corta o que o documento não é: sem PDF → texto; PDF sem marcador de página → sem
  "lado" (não há como o texto dizer ao PDF "estou na folha 7"). Digitar na busca em modo PDF puro
  puxa o texto de volta para a tela — realce escondido não é realce.
- **Sincronia de scroll por página, não por pixel**: as duas colunas numeram do mesmo jeito (a
  extração é página a página), então o scroll-spy que já existia nas folhas de texto ganhou um
  par no painel do PDF, cada um religando o outro com uma trava anti-ecôo de 150 ms. Sumário,
  citação da IA e a navegação ‹ › comandam os dois.
- **O teto de 300 páginas**: o `/original` é um `FileResponse`, que não fala HTTP Range — o pdf.js
  recebe o arquivo inteiro de uma vez. Documento paginado é cortado pela contagem **antes de
  baixar qualquer byte**; sem paginação a contagem do texto não vale, e o corte acontece na carga
  (aviso + seletor desligado). Acima do teto a tela é o texto de sempre + o botão "Original".
- **Render sob demanda**: um `IntersectionObserver` desenha o `<canvas>` quando a folha entra na
  janela e devolve o bitmap quando sai (300 A4 desenhados de uma vez custariam GB de canvas); a
  moldura com o `aspect-ratio` real da página fica no lugar para o scroll não pular. As páginas
  sem OCR, que no texto são só o aviso "sem texto", ganham aqui a imagem real da folha.

### O DOCX formatado e o HTML renderizado

Dois formatos que antes só existiam como texto cru ganharam leitura própria — sempre **ao lado**
do texto extraído, nunca no lugar dele: é o texto (não o render) que a IA lê, que a busca casa e
que a citação realça.

- **`.docx` — modo Formatado.** `GET .../render` devolve o Word em **blocos** por página
  (`renderizar_blocos_docx`, em `documentos_utils.py`): título com nível, parágrafo com runs de
  negrito/itálico/sublinhado, lista ordenada/bulleted agrupada e tabela sem a repetição da
  mesclagem horizontal. Os blocos saem do **objeto python-docx**, nunca de HTML cru do arquivo, e
  o front (`parsearBlocos`/`montarBlocos` em `documento.js`) pinta o HTML e aplica o realce bloco
  a bloco — o mesmo expediente da célula de planilha, então `mark.doc-trecho`, o ‹ › e o selo de
  ocorrências do sumário valem igual. As quebras de página do render são as **mesmas** do extrator
  (`_RE_QUEBRA_PAGINA_DOCX`); se a contagem não bater com o texto gravado (arquivo mudou depois da
  extração), o formato desliga sozinho e a tela segue no texto. No modo Texto de um .docx, as
  tabelas — que o extrator agora grava no mesmo markdown da planilha (`_linha_tabela`, com `|`
  virando `/` dentro da célula) — também voltam a ser `<table>`, com botão de baixar cada uma em
  `.csv`. Isso vale só para texto **reextraído** depois da mudança; documento velho continua com a
  tabela achatada até o **Reextrair**.
- **`.html` — modo Renderizado.** O original é buscado por `fetch` e entra num
  `<iframe sandbox="" srcdoc>`: `srcdoc` não é busca de rede (o `X-Frame-Options: DENY` do nginx
  não o alcança) e o `sandbox` vazio executa **nada** do documento — HTML anexado é conteúdo sob
  investigação, não código para executar; servir o `.html` original `inline` na mesma origem seria
  um XSS entregue de bandeja, e é por isso que o botão continua sendo "Baixar original". O painel
  é leitura visual pura: sem paginação para prometer, sem realce para esconder — digitar na busca
  ou clicar no sumário puxa o painel de texto de volta.

### Casar a citação com o papel

`web/js/utils/doc_trecho.js` é o módulo compartilhado pelas telas que citam documento.

O trecho **não viaja no marcador**: sai do parágrafo clicado, pegando o que estiver entre aspas —
o formato que os prompts exigem da citação. Por isso só pode ser resolvido no clique, e por isso
existe um handler único para toda pastilha `.chat-ref-doc`.

Casar esse trecho com o texto do documento tem duas armadilhas:

- **quebra de linha**: o texto extraído quebra onde o PDF quebrava, então a citação quase nunca
  bate caractere a caractere — o casamento é palavra por palavra, com qualquer espaço entre elas;
- **acento**: PDF digitalizado com frequência perde o acento ("Sao Paulo" onde o papel diz "São
  Paulo"), enquanto a IA cita acentuado. O casamento roda sobre uma cópia **dobrada** (sem acento)
  do texto, e a dobra é feita caractere a caractere, um entra e um sai — todo o realce depende de o
  índice no texto dobrado valer no original. Mesmo raciocínio de `_montar_tabela_dobra` em
  `api/funcoes/anonimizacao.py`.

Reticências na citação (`…`, `...`) marcam onde a IA cortou o miolo: cada pedaço é procurado
sozinho, porque a frase inteira não acharia nada.

### Rotas

| Rota | O quê |
|---|---|
| `GET /api/investigacao/inqueritos/{id}/documentos/{id}/visualizacao` | metadados + páginas |
| `GET /api/investigacao/inqueritos/{id}/documentos/{id}/original` | o arquivo, `inline` |
| `GET /api/investigacao/inqueritos/{id}/documentos/{id}/render` | o .docx em blocos (modo Formatado) |
| `POST /api/investigacao/inqueritos/{id}/documentos/{id}/reextrair` | relê e regrava o texto |

## Modelo de dados

Nenhuma tabela ou coluna nova: a página vive dentro de `TX_CONTEUDO`, em
`INVESTIGACAO.INQ_TB04_DOCUMENTOS`. Não há
migração de schema — só a reextração dos documentos já anexados, sob demanda pelo botão.

O arquivo original é lido de `data/` pelo caminho relativo em `NO_ARQUIVO`, sempre via
`organizacao_arquivos.resolver`, que é quem barra `..` e caminho absoluto.

## Referências

- Telas: `web/documento.html`, `web/investigacao.html` (aba "Outros documentos"), `web/ia.html`
- Módulos: `web/js/documento.js`, `web/js/documento_pdf.js`, `web/js/utils/doc_trecho.js`,
  `web/vendor/pdfjs/` (pdf.js legado 3.11.174 + worker + `standard_fonts/`)
- CSS: `web/css/documento.css` (painéis, `.doc-painel-pdf`, `.doc-pdf-*`, `.doc-folha-blocos` /
  `.doc-bloco-*` do Formatado, `#doc-render-iframe` do Renderizado)
- CSP com `worker-src`: `nginx/default.conf` (nos dois `add_header`, server e `location` de assets)
- API: `api/funcoes/documentos_utils.py`, `api/rotas/investigacao.py`, `api/rotas/documentos.py`
- Teste do Formatado (sem banco): `python tools/teste_render_documento.py`, da pasta `api/`
- Documentos relacionados: [chat_ia.md](chat_ia.md), [documentos_diversos.md](documentos_diversos.md),
  [irpf.md](irpf.md), [busca_semantica.md](busca_semantica.md),
  [infraestrutura.md](infraestrutura.md) (CSP e o `worker-src` do pdf.js)
