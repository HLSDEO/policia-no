# Exportação para Excel no navegador

## Visão geral

Geração de planilhas `.xlsx` **inteiramente no navegador**, sem dependências externas nem
servidor: `web/js/utils/exportar_excel.js` monta o arquivo OOXML (ZIP com método `store`) com código
próprio — sem libzip/JSZip nem qualquer biblioteca de planilha. Um botão "⬇ Excel" é injetado na
barra da tela (`.topbar-actions` por padrão) de forma automática (`data-exportar` na tabela) ou
programática (`ExportarExcel.instalar`). Todas as células são gravadas como **texto**
(`t="inlineStr"`) — CPF, telefone, chave PIX e códigos não viram número nem perdem zero à
esquerda ao abrir no Excel.

Usado por mais de 20 telas de listagem (aplicativos, contatos, ligações, chaves PIX, e-mails,
notas, notificações, redes Wi-Fi, contas financeiras, usuários, arquivos, agenda, acessos,
dispositivos de conta, localizações, navegação, SIMBA, inquérito ePol, RIF — ver Referências).

## Fluxo de uso

1. **Modo simples (DOM), auto-inicializado**: em qualquer `<table>` marcada com
   `data-exportar="nome-do-arquivo"`, o script (`autoInit`, rodando no `DOMContentLoaded` ou
   direto se o documento já carregou) injeta o botão sozinho e exporta apenas as **linhas
   visíveis** do DOM no momento do clique (`offsetParent !== null` ou `getClientRects().length`
   — respeita filtros/paginação aplicados no cliente) e as colunas que têm algum dado em pelo
   menos uma linha (descarta colunas de ação/botão, mesmo sem marcação explícita). Atributo
   opcional `data-exportar-alvo` troca o seletor onde o botão entra (default `.topbar-actions`).
2. **Modo programático**: `ExportarExcel.instalar({ nome, tabela })` aponta para uma tabela
   específica (seletor string ou elemento) fora do auto-init; `ExportarExcel.instalar({ nome,
   dados })` é o modo para telas com **paginação no servidor** — `dados` é uma função que devolve
   `{colunas, linhas}` ou uma `Promise` disso, chamada **na hora do clique**, para buscar o
   conjunto completo (não só a página carregada) antes de exportar. `instalar` não duplica o
   botão se um `.btn-exportar-excel` já existir no alvo.
3. O arquivo é baixado como `<base>-AAAAMMDD-HHMM.xlsx` (`nomeComData`, carimbo local do
   navegador) via `Blob` + `URL.createObjectURL`, com revogação da URL 1 s depois. Sem colunas ou
   sem linhas → aviso "Não há dados para exportar." (via `Dialogo.alerta` quando disponível — ver
   `interface.md` — com *fallback* para `alert()` nativo se o módulo de diálogo não estiver
   carregado na página).
   O provedor pode devolver **`null`** para desistir sem aviso nenhum — é o caso de quem já
   conversou com o usuário e ouviu "cancelar" (ver o teto de lançamentos do SIMBA, abaixo);
   emendar um "Não há dados para exportar" logo depois de um cancelamento só confundiria.
4. **Caso especial — grafo para o i2 Analyst's Notebook**: `web/js/utils/exportar_i2.js` chama
   `ExportarExcel.gerarXlsxMultiSheet` diretamente (sem passar por `instalar`/`exportar`) para
   gerar um `.xlsx` com duas abas — "Entidades" (`Entity ID`, `Label`, `Type`, `Username`,
   `Image URL`) e "Vinculos" (`Link ID`, `From/To Entity ID`, `Type`, `Label`, `Weight`,
   `Symmetric`, `Common Followers/Following`, `Summary`), mais as colunas de atributo do
   recorte — no layout que o assistente de importação do i2 espera. Ver `exportar_grafo.md`.

## Quem exporta o conjunto inteiro, e quem exporta o que está na tela

O modo DOM só enxerga o que está no DOM. Em tela **paginada** isso é uma armadilha séria: a
planilha abre, parece completa e está truncada, sem nenhum aviso. Por isso toda tela paginada usa
o provedor `dados`:

| Tela | Paginação | Como exporta |
|---|---|---|
| RIF (`js/rif.js`) | 50 por página, **no cliente** | Provedor: a aba aberta inteira, a partir da mesma lista filtrada que a tela renderiza (`filtrarComunicacoes`/`filtrarEnvolvidos`/`filtrarOcorrencias`), antes do fatiamento por página. As colunas vão **além das da tabela** — campos A/D/E, data da operação e do fim do fato, ocorrência do BC, CPF/CNPJ do comunicante e o texto da análise só aparecem na linha de detalhe da tela, e na planilha custam uma coluna |
| SIMBA — Contas e Contrapartes | sem paginação | Modo DOM (`lerTabela`): a tabela já tem tudo |
| SIMBA — Lançamentos | 200 por página, **no servidor** | Provedor `dadosLancamentos`: repete a chamada da tela com os **mesmos filtros**, de 1.000 em 1.000 (o teto da API), até fechar o total. Acima de **50.000** lançamentos pergunta antes se leva só os primeiros ou cancela — o que não pode é cortar calado |
| Convergência — ambas as abas | 10 pares por página, prévia de 10 sinais por card | Botão próprio por aba, montando **uma linha por sinal** a partir do conjunto filtrado inteiro |
| Demais listagens | sem paginação, ou filtro no cliente | Modo DOM |

Regra ao criar uma tela nova: **se a lista pagina, o `data-exportar` do modo DOM está errado** —
é preciso um provedor `dados`.

## Como funciona

`web/js/utils/exportar_excel.js` (módulo autoexecutável, expõe `window.ExportarExcel`):

- **ZIP próprio** (`montarZip`): monta local file headers + central directory + EOCD à mão,
  **sem compressão** (método `store`, campo método = 0 — simplicidade em vez de tamanho, já que
  são listas de texto, não arquivos grandes), nomes de entrada em UTF-8 (flag `0x0800`) e
  CRC-32 tabelado (`TABELA_CRC`, tabela de 256 entradas construída uma vez; `crc32()`).
- **XLSX OOXML** (`gerarXlsx`/`gerarXlsxMultiSheet`): gera os 4-5 arquivos mínimos que o Excel
  exige — `[Content_Types].xml`, `_rels/.rels`, `xl/workbook.xml`, `xl/_rels/workbook.xml.rels` e
  um `xl/worksheets/sheetN.xml` por aba. Células saem como
  `<c t="inlineStr"><is><t xml:space="preserve">…</t></is></c>` — texto inline em vez de shared
  strings (mais simples de gerar, ainda que repita bytes entre células iguais), com `escaparXml`
  fazendo o escape XML padrão (`&`, `<`, `>`, `"`, `'`) e removendo caracteres de controle
  inválidos em XML (`\x00-\x08`, `\x0B`, `\x0C`, `\x0E-\x1F` — dado bruto do OCR ou de campo livre
  pode conter esses bytes e quebraria o parser do Excel). Cada célula é cortada em **32.767**
  caracteres (`MAX_CELULA`, com "…" no fim): estourar o teto do Excel não dá erro na gravação, dá
  arquivo que o Excel diz estar corrompido na abertura — e campo de texto livre (a análise de uma
  comunicação de RIF, a transcrição de um áudio) passa disso sem esforço. O corte mora no
  escritor justamente para que nenhuma tela precise lembrar dele.
- **Nome de aba** (`nomeAba`, usado por `gerarXlsxMultiSheet`): o Excel exige no máximo **31
  caracteres**, sem `: \ / ? * [ ]`, sem apóstrofo nas pontas, não vazio e **único** na pasta de
  trabalho (comparando sem diferenciar maiúscula). Violar qualquer uma dessas regras faz o Excel
  **recusar o arquivo inteiro** — não é a aba que abre com o nome errado, é a planilha que não
  abre. `nomeAba` sanitiza, corta, dá nome ("Planilha") a aba sem rótulo e desempata colisão com
  sufixo `(2)` **dentro** dos 31 caracteres. Antes o nome ia cru para o XML, e só não quebrava
  porque as duas abas em uso ("Entidades" e "Vinculos") cabiam por acaso.
- `gerarXlsxMultiSheet(abas)` recebe `[{nome, cabecalhos, linhas}, ...]` e gera uma planilha por
  aba.
- **Leitura do DOM** (`lerTabela`): usa o **último** `<tr>` do `<thead>` como rótulos de coluna
  (tabelas com cabeçalho em duas linhas usam só a de baixo), varre `<tbody>` ignorando linhas
  ocultas, e limpa cada célula (`textoLimpo`) clonando o nó e removendo elementos de controle
  (`SEL_IGNORAR = 'button, input, select, textarea, svg, .btn, .arq-acao, .arq-meta-baixar,
  .ent-botao, a[target="_blank"]'`) antes de extrair o texto — um link de "abrir em nova aba" ou
  um botão de ação não vira lixo na célula exportada. Colunas sem nenhum dado em nenhuma linha são
  descartadas; se a tabela estiver vazia, mantém as colunas que têm rótulo não vazio no cabeçalho
  (para não exportar um arquivo sem coluna nenhuma quando a lista está momentaneamente vazia).
- API pública: `instalar`, `exportar(nome, colunas, linhas)`, `gerarXlsx(cabecalhos, linhas)`,
  `gerarXlsxMultiSheet(abas)`, `lerTabela(tabela)`.

## Referências

- Script: `web/js/utils/exportar_excel.js` (carregado pelas telas de listagem que precisam exportar).
- Uso via `data-exportar` (auto-init): `web/aplicativos.html`, `web/contas_financeiras.html`,
  `web/chaves_pix.html`, `web/contatos.html`, `web/emails.html`, `web/ligacoes.html`,
  `web/notificacoes.html`, `web/notas.html`, `web/usuarios.html`, `web/wifi.html`.
- Uso via `ExportarExcel.instalar(...)` programático (paginação no servidor ou tabela fora do
  auto-init): `web/js/acessos.js`, `web/js/acessos_agregados.js`, `web/js/aplicativos.js`,
  `web/js/agenda.js`, `web/js/arquivos.js`, `web/js/atividades.js`, `web/js/contatos.js`,
  `web/js/conexoes.js`, `web/js/dispositivos_conta.js`, `web/js/emails.js`,
  `web/js/inquerito_pdf.js`, `web/js/ligacoes.js`, `web/js/navegacao.js`,
  `web/js/notificacoes.js`, `web/js/localizacoes.js`, `web/js/simba.js` (aba ativa da tela SIMBA,
  com busca no servidor na aba Lançamentos), `web/js/rif.js` (aba ativa da tela do RIF).
- Uso direto de `ExportarExcel.exportar(...)` com linhas montadas à mão: `web/js/convergencia.js`
  (uma linha por sinal, nas duas abas).
- Uso de `gerarXlsxMultiSheet` fora do fluxo padrão: `web/js/utils/exportar_i2.js` (aba de
  entidades + aba de vínculos do grafo, para o i2 Analyst's Notebook).
- Relacionados: `exportar_grafo.md` (exportação de grafo para i2 e GraphML), `interface.md`
  (padrão `topbar-actions` e `window.Dialogo`, usado no aviso de "sem dados" e no aviso de
  conjunto grande demais), docs das telas com listagens (ver `investigacao_celular.md`),
  `rif.md`, `simba.md`, `convergencia.md`.
