# RIF do COAF/SISCOAF

## Visão geral

Importação de **Relatórios de Inteligência Financeira (RIF)** do COAF/SISCOAF: um conjunto de
arquivos por RIF dentro de uma subpasta em `data/` — os CSVs de Comunicações, Envolvidos e
Ocorrências (separados por `;`, codificados em **Windows-1252**) e o PDF do relatório. Cada
RIF pertence a um inquérito (criado na hora se o número ainda não existir).

> **Onde a pasta fica depois.** Terminada a importação, a pasta do RIF é movida de
> `data/<DELEGACIA>/` para `data/<DELEGACIA>/<INQUÉRITO>/RIF/`, renomeada para `RIF_<número>`, e
> o caminho novo passa a ser o valor gravado em `NO_PASTA` — ver [organizacao_arquivos.md](organizacao_arquivos.md).


O pipeline completo: a pasta vira uma linha em `RIF_TB01_RIFS` (cabeçalho com contagens), os
CSVs são **parseados e higienizados** em tabelas próprias (`RIF_TB02`/`TB03`/`TB04`), o PDF é
**anexado como documento** (`INQ_TB04`) para a IA ler, cada arquivo tem **hashes de custódia**
(`RIF_TB06_ARQUIVOS`) e — o coração do sistema — o **texto livre de cada comunicação é
estruturado em contrapartes** (remetentes/favorecidos com valores) por um parser
determinístico de 24 formatos de bancos/estilos de redação do COAF, com o fechamento pelo
total via linha "DESCONHECIDO". A página `web/rif.html` mostra o resumo (KPIs), a **série mensal crédito × débito** (clique no
mês filtra as comunicações), a **barra de cobertura** (quanto do movimentado tem dono: nomeado
no texto + identificado à mão × sem dono), as abas — incluindo **Contrapartes**, a consolidação
por pessoa do RIF inteiro com o cruzamento "também no SIMBA" —, a análise com as contrapartes
estruturadas e a **identificação manual** das fatias desconhecidas; o **grafo** dos envolvidos
tem tela própria (`web/grafo_rif.html`), com exportação do grafo para i2 (CSV/XLSX/ANX) e
GraphML.

## Fluxo de uso

1. **Importar** (tela de investigação): escolher a subpasta do RIF (nome com "RIF" + número,
   o seletor pré-lista o que cada pasta tem: CSVs e PDF) e o inquérito — a pasta pode ter sido
   **enviada pelo navegador** ("Enviar do seu computador", na aba do RIF: escolher ou arrastar a
   pasta inteira) ou copiada para `data/`. A importação é
   **síncrona** (os CSVs são pequenos) e grava `RIF_TB01_RIFS` com as contagens. RIFs sem CSV
   importam mesmo assim (só o PDF).
2. **Página `web/rif.html`** — cabeçalho com o **titular mais citado** (nome · CPF/CNPJ) e o
   botão **"Grafo →"** (abre `grafo_rif.html` no mesmo RIF). No topo, **KPIs** (`rif-resumo`): Comunicações, Instituições
   comunicantes, **Crédito total** (verde, `rif-kpi.credito`), **Débito total** (vermelho,
   `rif-kpi.debito`), Envolvidos, **PEP citados** (roxo, `rif-kpi.pep`), Ocorrências e
   Período (menor/maior `dt_operacao`/`dt_recebimento`).
 3. **Série mensal e cobertura** (sob os KPIs) — o gráfico `#rif-serie` soma crédito (campo B)
    e débito (campo C) por mês a partir das comunicações já carregadas (sem rota nova); clicar
    num mês filtra a aba Comunicações (chip removível em `#rif-filtros-ativos`, estado na URL
    `mes=`). A barra `#rif-cobertura` abre o movimentado: **nomeado** no texto (verde),
    **identificado à mão** (azul) e **sem dono** (hachura), com o % do total que tem dono.
 4. **Abas** (todas com busca + paginação de 50) — o botão **"⬇ Excel"** da barra exporta a aba
    aberta **inteira**, não a página de 50 desenhada, e com as colunas do detalhe junto (campos
    A/D/E, data da operação, ocorrência do BC, CPF/CNPJ do comunicante, texto da análise); o nome
    do arquivo acompanha a aba (`rif_comunicacoes-...xlsx`). Ver `exportar_excel.md`:
    - **Comunicações** — `#`, Recebimento, Titular, Instituição, Agência (cidade/UF), Crédito,
      Débito; filtros por instituição, **titular** (só aparece com 2+ titulares), UF e ordem
      (`nº`/recente/antigo/maior crédito/maior débito), além dos chips de **mês** (série) e de
      **pessoa** (vinda da aba Contrapartes; casa quem é envolvido do Indexador OU aparece como
      contraparte estruturada). "Ver análise" abre `rif_detalhe` com os metadados, o texto
      normalizado e os dois blocos de contrapartes (`rif-cp-bloco`, crédito verde/débito vermelho);
    - **Contrapartes** — a visão financeira do RIF inteiro
      (`GET .../rifs/{id}/contrapartes-consolidadas`, `consolidar_contrapartes_rif`): uma linha
      por pessoa — a MESMA chave da linha do tempo financeira (`simba_utils`: CPF/CNPJ, senão
      `NOME:`+maiúsculas) — com creditou/debitado/saldo, nº de comunicações, 1º/último movimento,
      badges PF/PJ · Titular · PEP · Servidor (casados por chave nos envolvidos) · **SIMBA R$**
      (o mesmo documento também movimenta no extrato do inquérito — como contraparte consolidada
      ou titular de conta: a mesma pessoa contada por duas fontes independentes) e o aviso "tem
      identificação à mão"; a última linha é o **Desconhecido** (subtraindo o que já foi
      identificado) e "Ver N comunicação(ões)" filtra a aba Comunicações pela pessoa;
    - **Envolvidos** — filtros por nome/CPF-CNPJ, tipo de papel, só PF / só PJ (14 dígitos ou
      com `/`), **Só PEP**, **Só servidor**; badges de marcadores;
    - **Ocorrências** — código, enquadramento/alerta e o **dinheiro da comunicação** a que
      pertence (casada pelo Indexador), com ordenação por maior valor.
 5. **Página `web/grafo_rif.html`** — o grafo do RIF em tela própria (mesmos `?inquerito=`/`?rif=`),
   com a barra de filtros (tipo de pessoa, período, faixa de crédito/débito), a legenda, o SVG,
   "Carregar mais" quando há limite de nós e os botões de exportação do grafo. Saiu das abas de
   `rif.html` porque o layout force-directed é caro e não precisa rodar quando só se quer ler as
   tabelas; links antigos (`rif.html?...&aba=grafo`) são desviados para cá.
 6. **Identificar o DESCONHECIDO**: na análise de uma comunicação, a linha "Desconhecido"
   (sem nome/doc) tem "Identificar"; o modal `modal-rif-ident` pede nome, CPF/CNPJ, valor e
   observação (origem da informação, ex.: "extrato SIMBA"). Salvar reparte o desconhecido; a
   fatia aparece aninhada (↳) logo abaixo; "Remover" devolve o valor ao desconhecido.
 7. **Reprocessar contrapartes** (botão no card do RIF na tela de investigação): reextrai as
   contrapartes do texto guardado no banco — para aplicar melhorias no parser sem reimportar.
 8. **Reanalisar com IA** (botão no fim do "Texto original da análise" de cada comunicação, em
   `rif.html`): a IA recebe **só o texto daquela comunicação** e identifica os lançamentos,
   **substituindo a extração por REGEX** só daquela comunicação — para textos que não seguem
   nenhum dos 24 formatos do parser e cujas tabelas de Entradas/Saídas saem vazias ou
   incompletas. As demais comunicações, as identificações manuais e o grafo (que relê a
   mesma `RIF_TB05`) não são tocados.

## Como funciona

### Descoberta e seleção de RIFs (`rif_utils.py`)

- `_subpasta_rif(subpasta)` resolve `PASTA_DADOS/<subpasta>` (padrão de `investigacao_utils`);
  a subpasta da delegacia vem de `delegacia_permitida` nas rotas.
- `_eh_pasta_rif` considera pasta de RIF **toda subpasta com "rif" no nome** — o critério é o
  nome e não a presença de CSV, porque há RIFs só com PDF/documentos.
- **`salvar_rif_enviado`** — o envio da pasta pelo navegador (abas "RIF - COAF" de
  `web/importacao.html` e do modal "+ Importar" de `web/inqueritos.html`, via
  `POST /api/investigacao/rif-upload`): o input `webkitdirectory` envia cada arquivo com o
  `webkitRelativePath`, e no arraste da pasta `web/js/utils/pasta_arraste.js` a desce via
  `webkitGetAsEntry()` e devolve o caminho relativo em `__caminho`; em ambos o primeiro
  componente do caminho é o nome da pasta em
  `data/`. Garante "RIF" no nome (prefixa quando falta, porque a listagem e a importação só
  reconhecem pastas com "RIF"), colisão ganha sufixo `(2)`, só **CSV e PDF** entram (o resto
  volta em `falhas`), limite por arquivo é o geral (`UPLOAD_DOCUMENTO_MB`, como no documento
  genérico), e qualquer erro de gravação remove a pasta inteira — nada de RIF pela metade.
- `_RE_NUM_RIF = re.compile(r"RIF[_\s]*0*(\d+)", re.IGNORECASE)` extrai o número do RIF do
  nome do CSV ou da pasta (`_numero_rif`).
- `_achar_csv(pasta, sufixo)` localiza o CSV pelo **sufixo** (`_Comunicacoes`, `_Envolvidos`,
  `_Ocorrencias`) **sem depender do número no nome**; `_achar_pdf` pega o primeiro `.pdf`.
- `listar_rifs_disponiveis` devolve, para cada pasta, `nu_rif` e flags `tem_*` — base do
  seletor.

### Engenharia de dados na leitura dos CSVs

- **Codificação**: `_ler_csv` tenta `utf-8-sig` → `cp1252` → `latin-1`, com fallback
  `latin-1` + replace; separador `;` via `csv.DictReader`.
- **`_txt()`** — normalização de campo que resolve as armadilhas do formato:
  - `-`, vazio e espaços → `None`;
  - **notação científica** (ex.: `6,07012E+13`, como o SISCOAF exporta IDs) → convertida em
    inteiro real sem perder dígitos (`v.replace(',', '.')` → float → int, sem `E+`);
- **`_valor_br`** — `'281.500,00'` (pt-BR) → `281500.00` (remove `R$`, milhar `.`, troca a
  vírgula decimal por ponto); `None` se inválido.
- **`_data_br` / `_datahora_br`** — `dd/mm/aaaa` (com hora opcional `HH:MM[:SS]`) → `DATE`/
  `TIMESTAMP`; `None` se vazio/`-`/inválido.
- **O filtro contra lixo de rodapé** — `_indexador` exige **dígito puro** entre 1 e 9999. Os
  CSVs do SISCOAF trazem, DEPOIS das linhas de dados, uma legenda dos códigos de segmento
  (`Indexador = '45 - DPF - ...'`) e um rodapé com um hash; ambos têm Indexador não-numérico.
  A importação descarta qualquer linha cujo Indexador não seja um pequeno inteiro
  (`_dados()`), eliminando esse lixo em qualquer RIF.

### Importação (`importar_rif`) — uma transação

1. **Validação de caminho**: `_validar_pasta` barra path traversal (`/`, `\`, `.`, `..`) e
   exige nome com "RIF" (`ValueError`/`FileNotFoundError`).
2. **Inquérito**: `_obter_ou_criar_inquerito` — cria se o número ainda não existir.
3. **PDF → documento**: `extrair_texto_documento` extrai o texto e insere em
   `INQ_TB04_DOCUMENTOS` (`tp_arquivo='pdf'`, `tx_conteudo`, `tx_erro` se falhar) — é o que a
   IA consulta; `ID_DOCUMENTO_PDF` fica no cabeçalho.
4. **Cabeçalho**: `RIF_TB01_RIFS` com número, pasta, contagens e usuário.
5. **Custódia**: `calcular_hashes_arquivo` (MD5/SHA1/SHA256, streaming em blocos de 4 MiB)
   de cada um dos até 4 arquivos → `RIF_TB06_ARQUIVOS` (ver `hash_arquivos.md`).
6. **Comunicações**: uma linha em `RIF_TB02` por linha do CSV (20 colunas mapeadas campo a
   campo), e na sequência `_gravar_contrapartes` estrutura o texto livre → `RIF_TB05`.
7. **Envolvidos** (`RIF_TB03`) e **Ocorrências** (`RIF_TB04`) mapeadas por coluna SISCOAF.
8. `conn.commit()` — tudo ou nada.

### O coração: estruturar o texto livre em contrapartes (`extrair_contrapartes`)

Cada comunicação traz `informacoesAdicionais` — o texto do analista do COAF descrevendo "quem
creditou e quem foi debitado". O sistema o transforma em linhas estruturadas `{direcao,
nome, doc, valor, qtde, obs}` **100% determinístico, sem IA e auditável**. Texto narrativo
sem nenhuma estrutura conhecida devolve lista vazia — o front mostra o texto cru.

**Etapa A — desfazer a paginação** (`_remonta_quebra_pagina`): o texto chega formatado em
linhas de largura fixa do relatório, concatenadas com tiras de 200+ espaços que às vezes caem
**no meio de palavra** (`'FAB IOLA'`) ou de **CPF/CNPJ** (`'798.58 8.567-68'`). Tira ≥4
espaços com alfanumérico dos dois lados é **fechada sem espaço**; as demais colapsam para um
espaço. Depois `_repara_numeros` conserta contextos comprovadamente lixo (`R $ 32.000,00`,
`1.200.000 ,00`, `107. 985,24`, CPFs/CNPJs partidos) — nunca "dígito espaço dígito" solto,
que costuma separar dois números. `normalizar_texto_analise` aplica a mesma remontagem na
exibição; o cru fica no banco.

**Etapa B — corte crédito/débito** (`_pos_debito`): localiza o início da seção de débito
pelos marcadores (`principais favorecidos`, `Valor Enviado`, `lançamentos a débito`,
`Os débitos` — com heurística para não confundir resumo "totalizaram" —, `A DÉBITO TOTALIZOU`
etc.); antes do corte = crédito, depois = débito.

**Etapa C — os 24 formatos de banco**, cada um com regex própria, testados **nesta ordem
exata** dentro de `extrair_contrapartes` — a primeira combinação de formato que produzir ao
menos um item **vence e encerra a função** (`if itens: return ...`), então a ordem é
precedência, não é cosmética. A letra de cada formato é só um rótulo histórico (a ordem de
descoberta durante o desenvolvimento) e **não** corresponde à ordem alfabética nem à ordem de
tentativa — por isso a tabela abaixo já vem na ordem real de execução:

| # | Fmt | Banco/estilo | Padrão reconhecido |
|---|---|---|---|
| 1 | P | BB — depósito em espécie (segmento 42) | linha de terminal de largura fixa: `DEPOSITO EM DINHEIRO E CHEQUE <depositante>-<doc 14 dígitos><agência 9><conta 11> - R$ <valor sem centavos>`; sem texto analítico algum. Sempre crédito (quem deposita em espécie credita o titular) |
| 2 | B | Santander | `- NOME - CPF/CNPJ: doc - Valor Recebido/Enviado: R$` (+ variante "sendo: N PIX - R$X") |
| 3 | F | Itaú (seções) | `ORIGEM DOS RECURSOS`/`DESTINO DOS RECURSOS` com emitentes/favorecidos `NOME, CPF (R$ X)` |
| 4 | G | Itaú (amostra) | `R$X - N transações - NOME - DOCUMENTO: doc` |
| 5 | H | Mercado Pago | `R$X - NOME - [CPF/CNPJ] via N transferências` (doc opcional: cobre "Mesma titularidade") |
| 6 | D | Nu Pagamentos | `R$X em N transação(ões) via/para CPF/CNPJ doc (NOME)` — "via"=crédito, "para"=débito |
| 7 | I | "DESTACANDO" | `[XX,XX%] R$X NOME - CPF/CNPJ[:] doc (obs)` — direção pelo cabeçalho A CRÉDITO/A DÉBITO mais próximo |
| 8 | J | Caixa | `R$X - NOME, CPF/CNPJ doc (detalhe)` sob "principais remetentes/beneficiários foram" ou "Transferências Instantâneas..." |
| 9 | O | Banco Inter | `R$X - N PIX RECEBIDOS/ENVIADOS ... TITULAR É A EMPRESA NOME, CNPJ:` (ou "MESMA TITULARIDADE") |
| 10 | L | narrativo | `R$X, referente a N operações PIX, recebida de/enviada para NOME, CPF doc` (valor sem centavos) |
| 11 | N | tabela "em amostragem" | `NOME / DOC / R$ VALOR / PIX C\|D / QTDE /` — direção da coluna |
| 12 | M | Movimentação a Crédito/Débito | `R$X (XX%) – NOME – CPF/CNPJ: doc (Transações: N)` |
| 13 | K | Stark Bank/carteira digital | `NOME - doc \ Chave ... R$VALOR ... Qtde Transação(ões): N` — valores em formato US (`93,000.00`) |
| 14 | E | Sicoob | `R$X - origem/destino/depositante/sacador: NOME, doc` (depositante=crédito, sacador=débito) + variante sem papel (`Qtd. N totalizando R$ VALOR NOME, DOC`) |
| 15 | C | Banco do Brasil | `NOME - doc (qualif) - N lançamento(s) no total de: R$` |
| 16 | R | narrativa "da contraparte" (Nubank/Cora e outros) | `- R$ 22800 - 6 transações (PIX) recebidos da contraparte NOME, CPF DOC - BANCO`. Valores costumam vir **sem centavos**. A direção sai do particípio do próprio item (recebido=crédito, enviado/pago=débito), porque crédito e débito se alternam na mesma lista |
| 17 | S | bloco "INFORMACOES DE CONTRAPARTES" | cabeçalho declara as colunas (`Montante - Qtde De Transacoes - Nome - Documento`) e as linhas seguem `R$ 4.995,00 - 5 - NOME - DOC`; direção pelo subtítulo (`Remetentes (Principais)` = crédito, `Favorecidos/Destinatários` = débito) |
| 18 | T | "principais ordenantes" (itens marcados com `o`) | `R$ 293.887,00 em 13 operacoes NOME CPF:DOC` — documento colado ou separado do rótulo; direção pelo subtítulo (ordenantes = crédito) |
| 19 | U | Transferências Instantâneas detalhadas por conta | `R$ 102.789,00 NOME CPF 119.895.364-09 - ( 38 PIXs Crédito Recebidos - Conta …, 3 PIXs … )` — **sem vírgula** entre nome e documento (o que o distingue do J) e a quantidade vem dentro do parêntese, somada quando repartida em várias contas |
| 20 | V | Cora e afins | `NOME - DOC, sendo 5 transações, totalizando R$ 350.000,00`. O par nome-documento aparece o tempo todo no texto (sócio, titular, empresa citada), então o que qualifica a linha como contraparte é o `sendo N transações, totalizando R$` logo depois — é nele que a âncora fica |
| 21 | W | "R$ X enviados por NOME, DOC" | direção pelo particípio + preposição, inequívocos aqui: quem **envia** credita o titular, quem **recebe** o debita |
| 22 | X | "referem-se a recebimento via PIX de NOME DOC" | narrativa curta com o valor antes, no parêntese do percentual: `100% (R$ 4.500,00), referem-se a recebimento via PIX de NOME DOC`; sem vírgula entre nome e documento |
| 23 | Q | tabela com cabeçalho explícito (Itaú e outros) | `Valor CPF/CNPJ Nome/Razao social Qte Instituicoes de origem\|destino` seguido das linhas `R$VALOR doc NOME Qte [ISPB]`. **Documento e quantidade vêm trocados em relação ao formato A** — daí precisar de bloco próprio. Ancorado no cabeçalho literal (só entra em texto que o traz) e posicionado logo antes do A, para não tirar match de nenhum formato anterior; a direção vem do próprio cabeçalho (`de origem`=crédito, `de destino`=débito), não do corte global |
| 24 | A | Bradesco/clássico | tabela `VALOR QTDE NOME CPF/CNPJ BANCO` — testado **por último**: é o formato mais genérico (menos âncoras textuais), então vem só depois de todos os outros terem tido a chance de casar primeiro |

O formato P é sempre o primeiro testado, depois de `_remonta_quebra_pagina`/`_pos_debito`
normalizarem o texto: é um registro terminal do próprio SISCOAF (sem narrativa de analista),
então quando casa a função devolve na hora, sem passar pelos demais 23 formatos.

> **Por que o Q existe.** Sem ele, esse texto caía no A e saía inteiro errado: a `Qte` grudava
> no nome (`FULANO 2`), o ISPB da instituição virava o CPF, o CPF real virava a contagem (e o
> clamp de `qtde` o zerava para 1) e o resto da tabela era engolido em `TX_OBS` — só a primeira
> contraparte sobrevivia, torta, e o fechamento inventava um DESCONHECIDO pelo saldo que
> faltava. Corrigido em ago/2026; RIFs importados antes disso se acertam com
> `reprocessar_contrapartes_rif` (ver *Operações administrativas*), sem reimportar a pasta.

Técnicas de engenharia de dados embutidas nas regex:
- **`_frouxo()`** casa palavras partidas por espaços soltos ("TOTA LIZOU", "A CREDITOTOTALIZOU")
  — um espaço perdido num marcador de seção jogava a direção inteira da comunicação para o
  lado errado;
- **`_rastreador_direcao()`** devolve a direção do **último marcador de seção antes da
  ocorrência** para formatos em que crédito e débito se intercalam (I, J, K, M) — o corte
  global só vale como queda;
- **`_valor_flex()`** decide pt-BR vs formato US pela forma do número (vírgula de milhar ou
  `\d+\.\d{2}` = US) — carteiras digitais misturam os dois no mesmo texto;
- `_soma_qtde_clausula` soma as quantidades do detalhamento quando não há contagem explícita
  ("sendo: 7 PIX - R$43.000,00"); `_extrair_totais_texto` acha "Total a crédito/débito",
  "créditos somaram/totalizaram R$X" (fallback de totais);
- lookaheads negativos evitam dupla contagem de um mesmo lançamento entre variantes;
- `_limpa_contraparte` normaliza nomes; `_completar_contrapartes` ordena crédito antes de
  débito, maior valor primeiro, DESCONHECIDO ao final de cada direção.

**Etapa D — saneamento do documento e descarte de fantasma** (`_completar_contrapartes`, ponto
único por onde os 17 formatos passam):

- **`_parece_data()` descarta a linha inteira** quando o "documento" é na verdade uma data
  (`10.06.2024`, `23/01/2025`). `_DOC_RE` (`\d[\d./\-]{6,}\d`) casa qualquer sequência de
  dígitos com ponto/barra/traço, então uma data cabe nele: nos textos que trazem tabelas de
  datas e valores (parcelas de consórcio, movimentação por período) o formato A — o fallback
  frouxo — casava essas linhas e inventava contrapartes com a data no lugar do CPF e um pedaço
  da tabela no lugar do nome (`'24.06.2024 17.010,00 30'`). CPF/CNPJ nunca tem essa forma, e a
  linha toda é lixo, então é descartada em vez de só limpar o campo.
- **`_normaliza_doc()` confere o dígito verificador** (`funcoes/cpf_cnpj_utils.py`) e conserta
  os dois desvios de largura do SISCOAF, que grava o documento num campo de 14 posições:
  - zero **sobrando** à esquerda — `00003253383610` é o CPF `032.533.836-10`; `040.953.545/0001-37`
    é o CNPJ `40.953.545/0001-37`;
  - zero **perdido** à esquerda — `95.917.246-75` chegando sem o `0` inicial de `095.917.246-75`.

  Só encurta/recompõe quando os dígitos resultantes **fecham o DV**; se não fecharem, devolve o
  que veio (perder o dado seria pior que exibi-lo torto). Isso importa além da tela: o mesmo CPF
  gravado em duas formas vira duas pessoas diferentes no cruzamento entre inquéritos
  (`convergencia.md`).

### Fechamento com "DESCONHECIDO" (`_gravar_contrapartes`)

O total da movimentação vem dos **campos B (crédito) e C (débito)** do CSV para os segmentos
41/42 (fallback: totais extraídos do texto). Como o texto detalha só as principais
contrapartes, a diferença `total − soma das contrapartes` vira uma **linha sem nome e sem
documento** por direção (`NU_ORDEM` ao final, `resto > 0.01`). É ela que impede o grafo de
sugerir que os nomes citados cobrem a movimentação inteira — e é a base da identificação
manual. Antes de gravar: `qtde` é clampada para `1..999999999` (protege contra erro de
notação científica) e nome/doc/obs são truncados em 3000 chars (VARCHAR).

### Identificação manual do desconhecido (`RIF_TB06_IDENTIFICACOES`, `db/init/25_rif.sql`)

Quando o investigador descobre, por outra fonte (extrato, quebra de sigilo, oitiva), que uma
**fatia** do desconhecido é de alguém, registra nome/CPF-CNPJ/valor:
- `_validar_identificacao` recusa valor ≤ 0 e valor que **estoure o que resta identificar**
  (disponível = total desconhecido − soma das identificações, excluindo a própria linha na
  edição; mensagem "disponível: R$ 1.234,50" em pt-BR);
- exige ao menos nome ou documento; a comunicação precisa pertencer ao RIF;
- fica **fora de `RIF_TB05` de propósito**: "Reprocessar contrapartes" apaga e reextrai a
  TB05 inteira, e o trabalho manual não pode ir junto — por isso a ligação é por
  (comunicação + direção), que sobrevive ao reprocessamento;
- no front, a fatia aparece aninhada sob o desconhecido (↳) com badge; "Remover" devolve o
  valor ao desconhecido. Identificações sem desconhecido correspondente (após reprocessar)
  são sinalizadas com aviso na própria aba.

### Operações administrativas

- **`reprocessar_contrapartes_rif`**: relê o texto cru de `RIF_TB02.TX_INFORMACOES_ADICIONAIS`,
  apaga a `RIF_TB05` inteira do RIF e reextrai — sem tocar nas identificações manuais;
  devolve `{qt_comunicacoes, qt_contrapartes}`.
- **`analisar_contrapartes_comunicacao_ia`** (reanálise com IA de UMA comunicação): lê o texto
  **normalizado** (`normalizar_texto_analise`, o mesmo exibido na tela; truncado em 50 mil
  caracteres) e o manda para a IA com o `SISTEMA_PROMPT_ANALISE_RIF`, que pede um JSON
  `{"contrapartes": [{direcao, nome, doc, qtde, valor, obs}]}`. A resposta é validada item a
  item (direção, valor > 0, qtde clampada, doc normalizado com DV via `_normaliza_doc`) e
  passa pelo mesmo saneamento do parser (`_completar_contrapartes`: dedup, descarte de
  data-fantasma, ordenação); depois as linhas de `RIF_TB05` **só daquela comunicação** são
  apagadas e regravadas com o mesmo fechamento por "DESCONHECIDO" (campos B/C ou totais do
  texto). Se o JSON vier cortado pelo teto de saída do provedor, tenta uma segunda vez com
  `max_tokens_saida` maior (mesmo padrão da análise de documentos). A chamada é **síncrona** e
  fica registrada na auditoria com a origem `rif_analise_ia` (filtro próprio na tela
  Auditoria). Erros: `ValueError` → 400, `ErroProvedorIA` → 502.
- **`apagar_rif`**: cascata manual (TB06 identificações → TB05 → TB02 → TB03 → TB04 → TB01) +
  apaga o documento PDF vinculado (`INQ_TB04`).
- **Rotas** (`api/rotas/investigacao.py`): `GET /api/investigacao/rifs-disponiveis` (filtra
  pela delegacia do usuário), `POST /api/investigacao/extrair-rif` (payload `RifIn`:
  `pasta`, `numero_inquerito`, `delegacia`), `GET .../inqueritos/{id}/rifs`,
   `GET .../rifs/{id}/comunicacoes|envolvidos|contrapartes|contrapartes-consolidadas|ocorrencias`,
  `POST .../rifs/{id}/reprocessar-contrapartes`,
  `POST .../rifs/{id}/comunicacoes/{id_comunicacao}/reanalisar-ia` (passa por
  `exigir_ia_inquerito`: IA desativada para a delegacia do usuário ou do inquérito → 403),
  `GET|POST .../rifs/{id}/identificacoes`,
  `PUT|DELETE .../rifs/identificacoes/{id}`, `DELETE .../rifs/{id}`. Todas exigem
  `exigir_aprovado`; as que só recebem `id_rif` passam por `_exigir_acesso_via_rif`, que
  resolve o inquérito dono e confere permissão (404 se não existe, 403 sem acesso).

### Consultas e UI

- **`listar_rifs_inquerito`** (card da investigação) usa **LATERAL joins**:
  `json_agg` dos hashes dos arquivos (`arq.hashes`) e o **titular mais citado** — mesmo
  critério do cabeçalho de rif.html: `GROUP BY no_envolvido, nu_cpf_cnpj` dos envolvidos com
  `trim(tp_envolvido) = 'Titular'`, `array_agg(... ORDER BY qtd DESC)` para identificar o RIF
  de relance no card.
- **`listar_comunicacoes_rif`** devolve o texto da análise **normalizado** para leitura e
  para a IA (`normalizar_texto_analise`); o cru, com a paginação do SISCOAF, permanece no
  banco para fins de custódia. Consulta consolidada de envolvidos por
  `GROUP BY no_envolvido, nu_cpf_cnpj` filtrada por `id_inquerito`, `ORDER BY dt_importacao DESC`.
- **`consolidar_contrapartes_rif`** (aba Contrapartes): agrupa a `RIF_TB05` + as identificações
  manuais pela chave da pessoa (CPF/CNPJ, senão `NOME:`+maiúsculas — a mesma de `simba_utils`,
  o que permite o badge SIMBA), casa PEP/servidor/titular pela `RIF_TB03` (mesma chave) e devolve
  o desconhecido e a cobertura (nomeado + identificado ÷ campos B+C) à parte. `ids_comunicacoes`
  por pessoa é o que o front usa para filtrar a aba Comunicações. O cruzamento SIMBA normaliza
  o documento a dígitos (`length ≥ 11`) e soma `SMB_TB04` (contrapartes) com `SMB_TB02` (titulares)
  dos extratos **concluídos** do mesmo inquérito.
- **KPIs** calculados no client (`renderResumo`): soma de `vl_campo_b`/`vl_campo_c`, período
  de `dt_operacao`/`dt_recebimento`, PEP com `/^s/i.test(tx_pep)` ("Sim").
- **Grafo** (`web/js/grafo_rif.js`: `construirGrafo` + `calcularLayoutG`, Fruchterman-Reingold
  simplificado — mesma técnica de `caso_grafo.js`). A tela recarrega por conta própria os quatro
  endpoints do RIF (comunicações, envolvidos, contrapartes, identificações), porque a montagem
  do grafo depende de todos eles:
  - nós: **pessoa física/PJ** por envolvido/contraparte (CPF 11 dígitos = PF, 14 = PJ),
    **comunicação** (instituição) e nós de **contrapartes identificadas à mão** — que saem
    do desconhecido e viram nós próprios com o mesmo peso;
  - arestas: **titular** (cinza, sem valor), **crédito** (verde, com seta no sentido do
    recurso e valor), **débito** (vermelho, sentido inverso), e **vínculos por papel** sem
    valor (Remetente/Depositante puxam para o crédito; papéis de saída para o débito; demais
    neutros); o **DESCONHECIDO encolhe** pelo que já foi identificado;
  - **PEP** ganha contorno roxo (`pep-no`) — marcado tanto pelo CPF quanto pelo **nome
    normalizado** do envolvido (`pepDoc`/`pepNome` em `grafo_rif.js`), porque a mesma pessoa pode
    aparecer como contraparte estruturada (sem o `TX_PEP` de `RIF_TB03`, que só existe na linha
    de Envolvido) — casar por nome também garante que ela ainda saia marcada; rótulos com halo
    para legibilidade, zoom/pan, legenda;
  - **foco e multisseleção** (no motor compartilhado `js/utils/grafo_fluxo.js`, vale igual para
    o SIMBA): clicar num nó abre a gaveta com o detalhe e deixa nítidos ele e as ligações dele,
    desbotando o resto; **Shift+clique acumula** — a gaveta troca o detalhe por um resumo do
    conjunto (ligações entre os selecionados com o **total em R$**, contrapartes ligadas a mais
    de um deles, e quantos ficaram fora do recorte desenhado) e o foco passa a ser a união das
    vizinhanças. Esc, ✕ ou clique no vazio saem; refazer o desenho (filtro, "Carregar mais")
    limpa a seleção;
  - **limite de 1000 nós** (`LIMITE_NOS_GRAFO_PADRAO`) com "Carregar mais" (+1000) — o layout
    force-directed sobre o grafo inteiro trava a aba; com qualquer filtro ativo o limite cai
    (`limiteNosGrafo = Infinity`), porque aí o recorte já é o do investigador;
  - **exportação do grafo** — CSV, XLSX e ANX para o i2 Analyst's Notebook e **GraphML** para
    Gephi/yEd/NetworkX, sempre sobre o grafo **filtrado inteiro** e não sobre o recorte
    desenhado. O recorte sai do motor compartilhado (`GrafoFluxo._recorteI2`) e a escrita é a
    mesma das outras cinco telas de grafo — pesos = valores em reais, direção creditada no
    `summary`/rótulo. Detalhes de formato e contrato em `exportar_grafo.md`.

## Modelo de dados

Todas em `INVESTIGACAO` (criadas em `db/init/25_rif.sql` e ):

| Tabela | Papel |
|---|---|
| `RIF_TB01_RIFS` | Cabeçalho: `ID_RIF`, `ID_INQUERITO` (FK `INQ_TB01`), `NU_RIF` (derivado do nome), `NO_PASTA`, `ID_DOCUMENTO_PDF` (FK `INQ_TB04`, nulo sem PDF), `QT_COMUNICACOES`/`QT_ENVOLVIDOS`/`QT_OCORRENCIAS` (default 0), `ID_USUARIO`, `DT_IMPORTACAO` |
| `RIF_TB02_COMUNICACOES` | Uma linha por comunicação: `NU_INDEXADOR` (chave de ligação com envolvidos/ocorrências), `ID_COMUNICACAO`, `NU_OCORRENCIA_BC`, `DT_RECEBIMENTO` (timestamp), `DT_OPERACAO`/`DT_FIM_FATO` (date), `NU_CPF_CNPJ_COMUNICANTE`, `NO_COMUNICANTE`, `NO_CIDADE_AGENCIA`, `SG_UF_AGENCIA`, `NO_AGENCIA`, `NU_AGENCIA`, `TX_INFORMACOES_ADICIONAIS` (TEXT, o texto do analista — cru, com a paginação), `VL_CAMPO_A..E` (NUMERIC(18,2); B=crédito, C=débito), `CD_SEGMENTO` |
| `RIF_TB03_ENVOLVIDOS` | Envolvidos citados: `NU_INDEXADOR`, `NU_CPF_CNPJ`, `NO_ENVOLVIDO`, `TP_ENVOLVIDO` (papel: Titular/Remetente/Beneficiário…), `NU_AGENCIA`, `NU_CONTA`, `DT_ABERTURA_CONTA`/`DT_ATUALIZACAO_CONTA`, `TX_PEP` (`bitPepCitado`, "Sim"/"Não"), `TX_PESSOA_OBRIGADA`, `TX_SERVIDOR` |
| `RIF_TB04_OCORRENCIAS` | Enquadramentos/alertas: `NU_INDEXADOR`, `ID_OCORRENCIA`, `TX_OCORRENCIA` (TEXT) |
| `RIF_TB05_CONTRAPARTES` | Contrapartes estruturadas do texto: `ID_COMUNICACAO_RIF` (FK `TB02`), `CD_DIRECAO` (`credito`/`debito`), `NO_CONTRAPARTE`, `NU_CPF_CNPJ`, `QT_LANCAMENTOS`, `VL_TOTAL`, `TX_OBS` (banco de origem/destino ou qualificação: PEP, profissão), `NU_ORDEM` (ranking por valor na direção). Linhas sem nome/documento = o "DESCONHECIDO" |
| `RIF_TB06_ARQUIVOS` | Cadeia de custódia: um arquivo da pasta por linha — `NO_ARQUIVO`, `TP_ARQUIVO` (`comunicacoes`/`envolvidos`/`ocorrencias`/`pdf`), `QT_BYTES`, `CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256` (CHAR(32/40/64)) |
| `RIF_TB06_IDENTIFICACOES` | Identificação manual de fatia do desconhecido: `ID_COMUNICACAO_RIF`, `CD_DIRECAO`, `NO_CONTRAPARTE`, `NU_CPF_CNPJ`, `QT_LANCAMENTOS`, `VL_TOTAL`, `TX_OBS` (origem da informação), `ID_USUARIO` (quem identificou), `DT_CRIACAO`/`DT_ATUALIZACAO` |

Índices: `IX79` (TB01 por inquérito), `IX80`–`IX82` (TB02–TB04 por RIF), `IX83`/`IX84`
(TB05 por comunicação/RIF), `IX115`/`IX116` (identificações). O PDF vive em `INQ_TB04_DOCUMENTOS`.

## Configuração

- Pasta de dados: `PASTA_DADOS`/`PASTA_INVESTIGACAO` (`api/funcoes/investigacao_utils.py`),
  com subpastas por delegacia quando multidelegacia (ver `permissoes_delegacia.md`).
- Sem variáveis próprias de `.env`: a importação é síncrona e usa os utilitários globais.

## Referências

- Telas: `web/rif.html` (estilos em `css/entidades.css`, `css/chat.css`), `web/js/rif.js`;
  `web/grafo_rif.html` (estilos em `css/mesa.css`, `css/chat.css`), `web/js/grafo_rif.js`.
- Rotas: `api/rotas/investigacao.py` (RifIn/IdentificacaoRifIn, `_exigir_acesso_via_rif`).
- Funções: `api/funcoes/rif_utils.py`; pasta de dados via `PASTA_INVESTIGACAO`
  (`api/funcoes/investigacao_utils.py`); hashes em `api/funcoes/hash_utils.py`.
- Esquema: `db/init/25_rif.sql`
  `30_relatorios.sql` (padrão de relatório Word do RIF).
- Relacionados: `hash_arquivos.md` (cadeia de custódia), `simba.md` (mesmo desenho de
  "arquivos por subpasta + inquérito"), `mesa_inquerito.md` (consulta de RIF por entidades),
  `permissoes_delegacia.md`, `relatorio_word.md` (padrão seedado "Relatório de RIF" com os
  blocos `rif_resumo`/`rif_comunicacoes`/`rif_contrapartes`/`rif_envolvidos`/`rif_ocorrencias`,
  `db/init/30_relatorios.sql`; marcação "para o relatório" nos tipos de origem
  `rif_comunicacao`/`rif_envolvido`/`rif_contraparte`), `documentos_diversos.md` (inquérito
  policial em PDF — mesmo padrão "regex sem IA" que o parser de contrapartes do RIF).
- Tela de disparo desta importação: [importacao.md](importacao.md) (aba correspondente de `web/importacao.html`).
