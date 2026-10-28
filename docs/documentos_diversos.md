# Documentos diversos e inquérito policial em PDF

Este documento reúne as rotas de documentos que **não passam pelo extrator UFDR**: os **documentos diversos** (arquivos soltos em `data/`, listados em `GET /api/documentos/arquivos` e enviados pelo navegador em `POST /api/documentos/upload`), o **documento anexado ao inquérito** ("Arquivo genérico" na tela de importação, com análise de entidades por IA) e o **inquérito policial em PDF** (importação do IPL exportado do ePol, um caso particular do anexo anterior).

| Seção | Cobre | Rotas |
|---|---|---|
| [Caso de inteligência](#caso-de-inteligência) | o "caso" como inquérito com flag (`IN_CASO_INTELIGENCIA`), mesmas fontes e telas do inquérito | `api/rotas/investigacao.py` |
| [Documento anexado ao inquérito](#documento-anexado-ao-inquérito) | "Arquivo genérico": texto extraído preso ao inquérito para a IA + análise de entidades (resumo/pessoas/achados) | `api/rotas/investigacao.py` |
| [Inquérito policial em PDF](#inquérito-policial-em-pdf) | IPL do ePol, extração de peças/pessoas/achados por regex sem IA | `api/rotas/investigacao.py` |

> **Onde o arquivo fica depois.** Terminada a importação, o documento é movido de
> `data/<DELEGACIA>/` para `data/<DELEGACIA>/<INQUÉRITO>/OUTROS/` e o caminho novo passa a ser
> o valor gravado no banco. Ver [organizacao_arquivos.md](organizacao_arquivos.md).



## Caso de inteligência

### Visão geral

O "caso" não tem mais tabelas, páginas nem rotas próprias: um **caso de inteligência é um inquérito com a flag `IN_CASO_INTELIGENCIA`** (`INQ_TB01_INQUERITOS`, migração `0033`). Ele usa as **mesmas fontes e os mesmos dados do inquérito** — UFDR, RIF, SIMBA, IPED e documento genérico — e aparece nas mesmas telas (Investigação, Inquéritos, chat de IA, grafo), com o selo "CASO DE INTELIGÊNCIA".

Criação: em `web/importacao.html`, o seletor "Importar como" (visível só para os perfis de **inteligência** — chefia e agente) escolhe entre Inquérito e Caso de inteligência. No modo caso, o campo de número some e o backend gera `CASO-AAAA.NNNN` (sequência por ano, com retry em concorrência) em `_obter_ou_criar_inquerito`. As 5 rotas de importação aceitam `caso_inteligencia: true` e recusam com 403 quem não tem o perfil (`pode_caso_inteligencia`).

O subsistema antigo de casos (`CAS_TB01`–`CAS_TB12`, páginas `caso_*.html`/`ia_caso.html`, rotas `/api/documentos/casos/*`, prompts de entidade de caso) foi removido; os dados antigos foram descartados.

## Documento anexado ao inquérito

### Visão geral

"Extrair de PDF" na tela de Investigação: qualquer PDF/CSV/XLSX/DOCX/TXT/HTML de `data/` (inclusive o
PDF do próprio inquérito, quando não é um IPL do ePol) é anexado ao inquérito escolhido — um
`.zip` em `data/` (inclusive com subpastas, copiado à mão ou via rede) vai para a **fila de
importação** (job `import_zip`, ver abaixo) e cada documento de dentro é anexado no mesmo
inquérito (achatado: só o nome do arquivo vale, colisão ganha ` (2)`, mesmas travas
anti-zip-bomb do upload) — o
texto extraído fica em `INQ_TB04_DOCUMENTOS`, disponível para o chat de IA da investigação
(`ia_investigacao.listar_documentos_inquerito`/`ler_documento_inquerito`) e para a busca
semântica (fatiado em trechos por `embed_utils._fatiar_documento`, `TP_FONTE='doc'`). Ao
contrário da extração de Caso, aqui a extração de texto de um documento avulso é **síncrona**
(PDF/CSV/XLSX/DOCX típicos são rápidos de ler) — quem roda em background é a **importação do
ZIP** e a **análise de entidades por IA**, que gera um resumo + pessoas + achados estruturados,
disparada automaticamente depois de anexar.

### Fluxo de uso

1. Na tela de Investigação, escolher o arquivo (mesmo seletor de `data/` usado pela extração
   de UFDR/RIF/SIMBA) e o inquérito; **arquivos `.ufdr` são recusados** ("vai na extração de
   UFDR ao lado, não aqui") — evita o erro de anexar um `.ufdr` inteiro como "documento de
   texto".
     - O arquivo pode ser **enviado pelo navegador** ali mesmo, na caixa "Enviar do seu
       computador" (`POST /api/documentos/upload`): ele é gravado em `data/<DELEGACIA>/` —
        exatamente onde seria copiado à mão — e já aparece selecionado no seletor. Máximo de 50 MB
        por arquivo por padrão, ajustável em Administração → Desempenho (`UPLOAD_DOCUMENTO_MB`),
        PDF/CSV/XLSX/DOCX/TXT/HTML **e JPG/PNG** (imagem entra como prova sem camada de texto) —
        **o `.zip` é a exceção, sem limite como o `.ufdr`** (o front
        o manda por `POST /api/documentos/upload-zip`, que o expande ali na hora e guarda só os
        documentos de dentro). Gravado em blocos (`salvar_documento_enviado`, em
       `funcoes/documentos_utils.py`), com sufixo numérico quando o nome já existe — e a resposta
       marca `renomeado: true`, que a tela avisa em âmbar, para ninguém importar "o arquivo" sem
       saber que ele virou "arquivo (2)". Antes de dar por salvo, o conteúdo é conferido pelos
       **magic bytes** da extensão (`_ASSINATURAS`): `.pdf` que é executável vira falha nomeada
       na hora, em vez de "erro na extração" dias depois. A delegacia passa por
       `delegacia_permitida`, então ninguém grava na pasta de outra unidade.
2. Se o PDF **for reconhecido como um inquérito policial do ePol** (ver seção seguinte), o
   card ganha o bloco de IPL com peças/pessoas/achados, em vez da análise por IA — os dois
   caminhos são mutuamente exclusivos por documento.
3. Se **não** for um IPL — qualquer outro PDF/CSV/XLSX/DOCX/TXT — e houver provedor de IA
   configurado, a **análise de entidades** dispara sozinha: resumo do documento + até 30
   pessoas citadas + até 50 achados (telefone, e-mail, valor, conta, processo, tipificação
   etc.), no bloco do card, com o mesmo padrão de polling em background das demais extrações.
   Sem IA configurada, o documento fica só com o texto extraído (sem análise) — sem erro.
4. Botão "Reanalisar" no bloco do card dispara a análise de novo (útil depois de mudar o
   provedor de IA ou ajustar o prompt) — chamada ignorada em silêncio se já houver uma análise
   `processando` no documento.

### Como funciona

**`anexar_documento_inquerito`** (`documentos_utils.py`) — síncrona, dentro da requisição:
valida o nome (sem `/`/`\`, path traversal), recusa `.ufdr`, resolve o caminho em `data/`
(com subpasta de delegacia), extrai o texto (mesmos `_EXTRATORES_TEXTO` do Caso) e insere em
`INQ_TB04_DOCUMENTOS` (criando o inquérito se o número ainda não existir, como a extração de
UFDR). `TP_ARQUIVO` vem da extensão do arquivo (`pdf`/`csv`/`xlsx`/`docx`/`txt`/`html`/`htm`, ou `?` se vazia).
Imagem de prova (`_IMAGENS_PROVA`: `.jpg`/`.jpeg`/`.png`) extrai `(None, None)` — entra **sem
texto por design** (o visualizador abre a aba "Original"); não é erro, e o pós-anexo abaixo pula
a análise de IA nesses casos (`qt_caracteres = 0`), porque não há o que varrer.

**Documento pesado** (local, acima de `DOC_IMPORTE_SINCRONO_MB`, padrão 25 MB — 0 desliga): a
rota `extrair-documento` não extrai na requisição — cria o inquérito e abre o job
**`import_documento`** (`zip_import.iniciar_importacao_documento` — mesmo esqueleto do ZIP: o
worker anexa + roda o pós-anexo, e o resultado vem no mesmo formato
`qt_anexados`/`qt_reconhecidos`/`falhas` para o polling da tela ser um só). Um por vez por
inquérito (409 no segundo).

**Roteamento pós-anexo** (rota `POST /api/investigacao/extrair-documento`, em
`investigacao.py`, via `zip_import.pos_processar_documento`): depois de
`anexar_documento_inquerito`, tenta `ipl_utils.extrair_inquerito` — se o PDF **for**
identificado como inquérito (ver `parece_inquerito` na próxima seção), grava peças/pessoas/
achados em `IPL_TB01..04` e devolve o resumo do IPL; senão (`ipl is None`) e **só se não
houve erro de extração**, dispara `documentos_utils.iniciar_analise_documento` em thread —
mas só se `_ia_configurada()` (provedor + modelo + URL no `.env`) for verdadeiro, para não
abrir uma thread fadada a falhar sem provedor configurado.

**Importação de ZIP em fila** (`funcoes/zip_import.py`, job `import_zip` em `JOB_TB01_JOBS`):
um ZIP com centenas de documentos levaria minutos — além do `proxy_read_timeout` de 300 s do
nginx para rotas comuns — então o POST só valida o nome, cria o inquérito (gerando o
`CASO-AAAA.NNNN` uma vez, no modo caso) e abre o job, devolvendo `id_job` na hora. A thread
expande (`expandir_zip_para_importacao`, mesmo núcleo do upload), anexa um por um (cada um
com seu pós-anexo acima e sua análise de IA) com progresso por arquivo (`qt_total`/
`qt_processados`, fase `expandindo`/`importando`) e cancelamento por `fl_cancelar`; ao fim, o
ZIP vai para `OUTROS` (best-effort) e o resumo (anexados, reconhecidos, falhas até 50,
`truncado`) fica em `TX_RESULTADO`. Um ZIP por vez por inquérito (segundo POST concorrente
leva 409); `pesado=False` (I/O, não ocupa vaga de `jobs_pesados`). Acompanhamento nas rotas
genéricas (`GET /api/investigacao/jobs/{id_job}`, `.../inqueritos/{id}/jobs/import_zip`,
cancelar/liberar) e em Administração → Jobs.

**Análise de entidades por IA** (`iniciar_analise_documento`/`_processar_analise_documento`):
- Trava por documento: `UPDATE ... SET cd_analise='processando' ... WHERE id_documento = %s
  AND cd_analise <> 'processando'` — se `rowcount = 0` (já estava processando), a thread
  encerra sem fazer nada; é o mesmo truque de "um `UPDATE` condicional como mutex" usado em
  outros jobs do sistema, sem precisar de tabela de lock à parte.
- Corta o texto em `_LIMITE_ANALISE_CARACTERES = 100_000` (cabe folgado no contexto dos
  provedores) e chama a IA com `SISTEMA_PROMPT_ANALISE_DOCUMENTO`, que **exige JSON puro**
  (sem markdown) no formato `{resumo, pessoas: [...], achados: [...]}`;
  `_extrair_json_analise` tolera cercas ```` ```json ```` e texto em volta (pega do primeiro
  `{` ao último `}`).
- Se o JSON não parsear, tenta **uma segunda vez** com `max_tokens_saida =
  _TETO_SAIDA_ANALISE_DOCUMENTO` (16384): o gateway `openai-cert` corta a saída no teto do
  esforço configurado (o raciocínio gasta o mesmo orçamento), e documentos grandes — planilhas
  principalmente — geram JSON que passa do teto do `low` e chega pela metade (`finish_reason
  "length"`), como `Expecting ',' delimiter` no parse. Nos provedores que não enviam
  `max_tokens` o teto é ignorado e a 2ª chamada é só uma nova chance.
- Pessoas exigem `no_pessoa` não vazio; achados exigem `tx_valor` não vazio — o resto do
  objeto pode faltar. Corta em 30 pessoas e 50 achados (o prompt já pede isso à IA, o código
  reforça o limite do lado de cá).
- Grava `cd_analise='concluido'`, `tx_resumo_ia`, `jq_pessoas`/`jq_achados` (JSONB) e as datas
  de início/fim. Qualquer exceção grava `cd_analise='erro'` + `tx_erro_analise[:2000]` — nunca
  fica preso em "processando".
- A chamada é auditada (`registrar_auditoria("documento_analise", ...)`) como qualquer outra
  chamada de IA do sistema (ver `auditoria.md`).

**Apagar o documento** (`apagar_documento_inquerito`): a extração estruturada do IPL (se
houver) **sobrevive** — só o vínculo (`IPL_TB01.ID_DOCUMENTO`) é posto em `NULL`, as peças/
pessoas/achados continuam no banco. Os trechos vetorizados do documento saem da busca
semântica (`DELETE ... EMB_TB01_EMBEDDINGS WHERE tp_fonte = 'doc' AND id_mensagem =
id_documento`). O **arquivo não é apagado em seco**: sai da pasta do inquérito e vai para
`data/_lixeira/<inquérito>/<carimbo>__<nome>` (`mover_para_lixeira`) — volta-atrás de
`LIXEIRA_DIAS` (padrão 30 dias) para quem removeu por engano; o expurgo diário do agendador
(`higiene_data`) é o único que destrói, e só o que venceu. Documento que mora num **nó** nunca
veio para o central: lá só se limpa a cópia de cache (`.no_cache`), a prova fica na estação.

Rotas (`api/rotas/investigacao.py`): `POST /api/investigacao/extrair-documento` (payload
`ExtracaoIn`: `arquivo`, `numero_inquerito`, `delegacia`),
`GET .../inqueritos/{id}/documentos`, `GET .../inqueritos/{id}/documentos/{id_documento}`,
`POST .../inqueritos/{id}/documentos/{id_documento}/analisar`,
`DELETE /api/investigacao/documentos/{id_documento}`.

### Modelo de dados

As colunas de análise vivem na própria `INQ_TB04_DOCUMENTOS` (tabela de documentos do
inquérito, compartilhada com o anexo do PDF de RIF e do extrato SIMBA — ver `rif.md`/
`simba.md`), adicionadas em `db/init/22_embeddings.sql`:

| Coluna | Papel |
|---|---|
| `CD_ANALISE` | `''` (nunca analisado) / `processando` / `concluido` / `erro` |
| `DT_INICIO_ANALISE` / `DT_FIM_ANALISE` | Janela da análise, para o polling do card |
| `TX_ERRO_ANALISE` | Erro da chamada de IA, truncado em 2000 chars |
| `TX_RESUMO_IA` | Parágrafo curto (~200 palavras) do que o documento contém |
| `JQ_PESSOAS` / `JQ_ACHADOS` | JSONB com as listas estruturadas (até 30 pessoas / 50 achados) |
| `ID_USUARIO_ANALISE` | Quem disparou a análise (o próprio anexo ou o botão "Reanalisar") |

O mesmo arquivo amplia `EMB_TB01_EMBEDDINGS` com `NR_TRECHO` (documento anexado passa fácil de
200 mil caracteres, muito além do contexto de qualquer modelo de embeddings — indexado em
fatias de ~4 mil caracteres com sobreposição, `TP_FONTE='doc'`, `ID_MENSAGEM` = id do
documento) — ver `busca_semantica.md`.

## Inquérito policial em PDF

### Visão geral

Importação e leitura do **inquérito policial em PDF** exportado do ePol: o PDF único que
empilha **todas as peças do procedimento** (portaria, ofícios, despachos, informações
policiais, termos, petições…), cada folha carimbada com "Fl. N" no rodapé. Quando o PDF
anexado por "Extrair de PDF" (seção anterior) **parece** um inquérito de verdade
(`parece_inquerito`), o sistema levanta, **por regex, sem IA** — mesmo espírito do parser de
contrapartes do RIF —, o que o investigador procura primeiro:

- **peças** — o índice do inquérito: que documento está em qual folha;
- **pessoas** — quem é citado (CPF/CNPJ, vulgo, papel) e onde;
- **achados** — telefones, e-mails, valores, contas, processos, tipificações, endereços, OAB.

A página `web/inquerito_pdf.html` exibe três abas (Peças, Pessoas, Achados) a partir do
número do IP (`NU_IPL`, numeração do ePol).

### Fluxo de uso

1. Anexar o PDF do inquérito pelo mesmo fluxo de "Extrair de PDF" (seção anterior) — a
   detecção de IPL é automática, não há um seletor "isto é um IPL" separado.
2. As abas mostram:
   - **Peças** (`tab-pecas`) — faixa de folhas ("Fl. N–M"), tipo, número, data, órgão do
     timbre, quem assinou eletronicamente, e o primeiro parágrafo do corpo como resumo;
   - **Pessoas** (`tab-pessoas`) — nome (com as demais grafias fundidas visíveis, "também
     citado como: ..."), CPF/CNPJ com badge PF/PJ, vulgo, papel, nº de menções e o trecho de
     contexto da primeira citação;
   - **Achados** (`tab-achados`) — tipo (badge), valor normalizado, nº de ocorrências e
     contexto.
3. `ipl-numero` exibe "nº `<NU_IPL>`" (ou o nome do arquivo, se o número não foi lido);
   cabeçalho com unidade, autoridade, data de instauração, tipificação, processo judicial e o
   "RESUMO DO(s) FATO(s) INVESTIGADO(s)" da portaria, quando encontrado.
4. Botões de escopo: **"Prompt de IA do Inquérito"** abre o chat de IA restrito a este IPL
   (`ia.html?...&escopo_tipo=ipl&escopo_id=<id_ipl>`, ver `escopo-conversa-ia.md`) e
   **"Achados do analista"** abre `achados.html` no mesmo escopo — nos dois casos a IA só
   enxerga este inquérito em PDF, não o caso inteiro.
5. Exportação para Excel da aba ativa, respeitando os filtros (`ExportarExcel`, ver
   `exportar_excel.md`).

### Como funciona

Tudo em `api/funcoes/ipl_utils.py`, **100% determinístico por regex** — sem chamadas de IA.
As páginas digitalizadas (OCR) perdem acento e às vezes ganham espaço no meio das palavras,
então toda comparação de rótulo passa por `_chave()` (maiúscula, sem acento, espaços
colapsados) e, nos títulos de peça, também sem espaço nenhum (`_colado()`).

**Detecção — este PDF é mesmo um inquérito?** (`parece_inquerito`): heurística
**conservadora de propósito** — um PDF que não passe aqui vira só um documento de texto comum
para a IA (seção anterior), em vez de ser forçado num molde que não serve. Exige ao menos 3
carimbos `Fl. N` no texto inteiro **e** pelo menos 2 das marcas de vocabulário policial
(`_MARCAS_INQUERITO`: "INQUERITO POLICIAL", "IPL N", "AUTORIDADE POLICIAL", "DELEGADO DE
POLICIA", "POLICIA FEDERAL", "POLICIA CIVIL", "DELEGACIA", "CODIGO DE PROCESSO PENAL").

**Folhas** (`_folhas`): número de cada página lido do **último** carimbo `Fl. N` que aparecer
nela — os carimbos do meio da página costumam ser citação a outra folha ("conforme fl. 12"), o
do rodapé é sempre o último. Página sem carimbo nenhum cai para a posição na sequência
(`i + 1`).

**Remontagem de parágrafo** (`_reagrupar`, usada no resumo de peça e no "resumo dos fatos"):
o extrator de texto do PDF quebra a linha a cada mudança de fonte, e o inquérito tem negrito
no meio de frases inteiras — o texto cru sai picotado. Só duas coisas sobrevivem como quebra
real: linha em branco (fim de parágrafo) e **linha-rótulo** curta terminada em `:` (≤ 60
chars, ex.: "Valor a apurar:", "Assunto:" — `_MAX_ROTULO`); todo o resto é colado de volta,
com pontuação órfã (`,.;:)%` no início da próxima linha) grudando sem espaço.

**Peças — o índice** (`_extrair_pecas`): varre as primeiras `_LINHAS_CABECALHO = 18` linhas
de cada página atrás de um **título de peça** (`_titulo_peca`): a linha colada (`_colado`)
tem que casar uma das regex de `_TIPOS_PECA` — lista ordenada da mais específica para a mais
genérica (ex.: "Termo de depoimento" antes de "Termo", "Ofício" antes do genérico), porque o
primeiro padrão que casar decide o tipo. Título tem no máximo `_MAX_TITULO = 90` chars — um
parágrafo de corpo que só *cita* "portaria" não deve virar peça nova. "Petição" só conta em
caixa alta pura (o mesmo vocativo em caixa mista é fecho de ofício, não título). Página sem
título nenhum no cabeçalho é **continuação** da peça anterior. Duas páginas seguidas com o
**mesmo título colado e o mesmo número** se fundem numa peça só (uma peça de 2+ páginas não
vira duas linhas); sem essa checagem de número, duas peças distintas do mesmo tipo em folhas
consecutivas (ex.: Informação Policial 008 seguida da 011) colariam em uma. Para cada peça:
`_numero_peca` procura nas 2 linhas seguintes ao título (o ePol quebra "DESPACHO N°" do
número em linhas diferentes), descartando o que parecer o número do próprio IPL;
`_orgao_pagina` pega a 1ª linha do topo que pareça timbre (≥ 12 chars, ≥ 60% letras);
`_achar_data`/`_achar_assinante` rodam na folha de abertura primeiro, caindo para o corpo
inteiro só se não acharem nada ali.

**Data da peça** (`_achar_data`): por extenso ("24 de fevereiro de 2026") primeiro, depois a
data de "assinado eletronicamente em dd/mm/aaaa", depois `dd/mm/aaaa` solto — sempre
**ignorando datas dentro de citação legal** (`_em_citacao_legal`: janela de 90 chars antes do
match contendo "LEI", "DECRETO", "CÓDIGO", "CONSTITUIÇÃO" etc.), senão "Lei 11.419, de 19 de
dezembro de 2006" citada no meio de um despacho de 2026 viraria a data da peça.

**Assinante** (`_achar_assinante`): regex "assinad[oa] ... por NOME[, CARGO]" — a variante com
cargo (`_RE_ASSINANTE_CARGO`, lista `_CARGOS`: Delegado, Escrivão, Agente, Perito, Corregedor,
Promotor, Procurador, Investigador, Papiloscopista, Subsecretário, Superintendente,
Coordenador) tenta primeiro, a sem cargo é fallback.

**Resumo da peça** (`_resumo_peca`): primeiro parágrafo (já `_reagrupar`-ado) com ≥ 60 chars
que não seja título de peça, não comece pelo nome do órgão (repetição do timbre), não contenha
ruído de rodapé (`_RUIDO_RESUMO`: "ENDEREÇO:", "PÁGINA", "DOCUMENTO ELETRÔNICO", "ASSINADO
ELETRONICAMENTE", "CÓDIGO VERIFICADOR"...) e tenha **maioria de minúsculas** entre as letras —
o filtro que separa prosa de verdade (corpo da peça) de texto em caixa alta (timbre/título).

**Pessoas citadas** (`_extrair_pessoas`): quatro fontes por página, cada citação vira um
registro que depois se funde:
- **CPF/CNPJ** (`_RE_CPF`/`_RE_CNPJ`, tolerando o espaço que o OCR enfia antes do hífen —
  `'055.194.947 -38'`): o nome vem de `_nome_antes`, que anda **de trás para frente** pelos
  tokens antes do documento enquanto parecerem nome próprio, cortando antes rótulos como
  "portador(a) do"/"inscrito(a) no CPF"; exige ≥ 2 tokens próprios para aceitar (uma palavra
  solta não vira nome);
- **vulgo** (`_RE_VULGO`, `\bvulgo\s+NOME`);
- **nome com posto colado** (`_RE_NOME_COM_POSTO`: "APF ANDRÉ SIMONASSI NANTES", "Sr. CLERIO
  …") e **nome seguido de cargo** (`_RE_NOME_COM_CARGO`: "DIEGO MARQUES YAMASHITA\nCorregedor
  …") — filtrados por `_nome_de_pessoa` contra `_PALAVRAS_NAO_PESSOA` (POLICIA, DELEGACIA,
  AVENIDA, MINISTERIO...), para não capturar "Corregedor Geral da Polícia Civil do Estado"
  como se fosse um nome.

O **papel** de cada citação sai de duas fontes, por prioridade: o posto/cargo colado no nome
(`_papel_do_posto`, ex.: prefixo "DPF" → "Delegado de Polícia Federal") é mais confiável que
`_papel_por_perto`, que varre uma janela de ±250 chars ao redor da citação contra a lista
ordenada `_PAPEIS` (do mais específico — "Delegado de Polícia Federal" — ao mais genérico —
"Testemunha", "Vítima", "Familiar", "Liderança criminosa") — a ordem importa porque a
primeira ocorrência na janela vence.

**Fusão de grafias da mesma pessoa** (`_agrupar_pessoas`): o mesmo nome sai escrito de
jeitos diferentes no PDF, porque o extrator de texto quebra a palavra onde a fonte muda
("ALEXANDRE DE OLIVEIRA BOTELHO", "ALEXANDRE DE OL IVEIRA BOTELHO", às vezes truncado em
"ALEX ANDRE DE OLIVEIR") — além da citação abreviada ("ALEXANDRE BOTELHO"). Processa do nome
**mais completo** (mais letras) para o mais curto, então o primeiro de cada grupo vira o
canônico; três testes decidem se uma grafia funde com o grupo já aberto, do mais seguro para o
mais frouxo: (1) chave de letras puras idêntica; (2) chave do nome menor **contida** na do
maior, com ao menos `_MIN_CHAVE_CONTIDA = 12` letras (abaixo disso, "ANA" caber em "ANABEL" é
coincidência, não a mesma pessoa); (3) citação abreviada — mesmo primeiro **e** último token,
com os tokens do nome menor formando uma subsequência do maior na mesma ordem. Documento
diferente **nunca** funde — dois CPFs distintos são duas pessoas, por mais parecida que a
grafia esteja. As grafias fundidas ficam em `NO_VARIANTES`, visíveis na tela ("também citado
como: ..."), para o agrupamento continuar auditável.

**Achados** (`_extrair_achados`): um regex por tipo, deduplicado por `(tipo, valor
normalizado)` com contador de ocorrências — guarda só a 1ª folha onde apareceu. Casos de
borda com o porquê:
- **telefone** (`_telefone_valido`): exige DDD plausível (11–99) — sem essa checagem,
  protocolo e número de conta viravam "telefone" por acidente; rejeita 9 dígitos que não
  começam com "9" (celular sem o dígito adicional é suspeito de ser outra coisa) e sequência
  de ≤ 2 dígitos distintos (`0000-0000` e afins);
- **valor** (`_RE_VALOR` + `_MIN_VALOR = 100.0`): abaixo de R$ 100 é troco/taxa, não
  movimentação de interesse;
- **conta** (`_RE_CONTA`): só aceita 4–15 dígitos depois de limpar pontuação — fora dessa
  faixa não é conta bancária plausível;
- **processo**: número CNJ completo (`NNNNNNN-DD.AAAA.J.TR.OOOO`);
- **procedimento** (`_RE_PROCEDIMENTO`): siglas de procedimentos correlatos citados no corpo
  (RIF, IPL, IPJ, SEI-C, GAMPES, BO, RDO, PIC, NF, denúncia) — "o que puxa outra base para
  dentro da investigação": um IPL que cita um RIF por número é a pista para abrir aquele RIF
  também;
- **tipificação**: `Lei N/AAAA` ou "Código Penal" citados perto de "art./artigo";
- cada tipo é capado em `_MAX_ACHADOS_POR_TIPO = 400` — um PDF gigante não deve produzir
  milhares de linhas de "valor" repetidas.

**Cabeçalho do procedimento** (`_extrair_cabecalho`): `NU_IPL` de "IPL nº ..." nas 4
primeiras páginas (fallback: padrão de número CNJ interno do ePol,
`\d{4}\.\d{6,8}`); `NO_AUTORIDADE` de "NOME, Delegado(a) de Polícia Federal"; `NO_UNIDADE` é a
sigla (padrão `XX/XX/.../XX`) que **mais se repete** no rodapé de todas as folhas — não a
primeira encontrada, porque citações a outras unidades aparecem no meio do texto;
`TX_TIPIFICACAO` de "ocorrência(s) prevista(s) no(s) ..." da portaria; `TX_RESUMO_FATOS` do
bloco "RESUMO DO(s) FATO(s) INVESTIGADO(s)" até o próximo marcador de seção ("Diante disso",
"CUMPRA-SE", "determino que sejam"); `DT_INSTAURACAO` é a data da **primeira peça** (a
portaria, que abre o inquérito).

**Gravação** (`extrair_inquerito`, chamada logo depois de `anexar_documento_inquerito`):
resolve o caminho em `data/` (varrendo subpastas de delegacia se preciso), roda
`analisar_inquerito` (que já chama `parece_inquerito` e devolve `None` se não for um IPL) e,
se reconhecido, grava o cabeçalho em `IPL_TB01_INQUERITOS_PDF` e as listas em `IPL_TB02`
(peças) / `IPL_TB03` (pessoas) / `IPL_TB04` (achados) numa única transação.

Rotas (`api/rotas/investigacao.py`): a extração roda dentro de `POST
/api/investigacao/extrair-documento` (ver seção anterior — não há rota própria de
importação); leitura: `GET /api/investigacao/inqueritos/{id}/pdfs` (lista, exige
`exigir_acesso_inquerito`), `GET /api/investigacao/inqueritos-pdf/{id_ipl}` (cabeçalho),
`.../pecas`, `.../pessoas`, `.../achados`, `DELETE /api/investigacao/inqueritos-pdf/{id_ipl}`.
As quatro últimas exigem `exigir_aprovado` e passam por `_exigir_acesso_via_ipl` (resolve o
inquérito dono do IPL, 404/403).

### Modelo de dados

Criado em `db/init/27_inquerito_pdf.sql`, esquema `INVESTIGACAO`. Mesmo desenho por-inquérito
das extrações UFDR (`INQ_TB02`) e dos RIFs (`RIF_TB01`).

| Tabela | Papel |
|---|---|
| `IPL_TB01_INQUERITOS_PDF` | Cabeçalho: `ID_IPL`, `ID_INQUERITO` (FK), `ID_DOCUMENTO` (FK `INQ_TB04`, o texto que vai para a IA), `NO_ARQUIVO`, `NU_IPL`, `NO_UNIDADE`, `NO_AUTORIDADE`, `DT_INSTAURACAO`, `TX_TIPIFICACAO`, `TX_RESUMO_FATOS` (TEXT), `NU_PROCESSO_JUDICIAL`, `QT_FOLHAS`/`QT_PAGINAS`/`QT_PECAS`/`QT_PESSOAS`/`QT_ACHADOS`, `ID_USUARIO`, `DT_EXTRACAO` |
| `IPL_TB02_PECAS` | Uma por peça do índice: `TP_PECA` (Portaria/Ofício/Despacho/Termo/Auto/...), `NU_PECA`, `DT_PECA`, `NU_FOLHA_INICIAL`/`NU_FOLHA_FINAL`/`QT_FOLHAS`, `NO_ORGAO` (timbre), `NO_ASSINANTE`, `TX_RESUMO` (TEXT) |
| `IPL_TB03_PESSOAS` | Uma por pessoa (já fundida): `NO_PESSOA` (grafia mais completa), `NO_VARIANTES` (demais grafias fundidas), `NU_CPF_CNPJ`, `NO_VULGO`, `TP_PAPEL`, `NU_FOLHA` (da 1ª citação), `QT_MENCOES`, `TX_CONTEXTO` (TEXT) |
| `IPL_TB04_ACHADOS` | Uma por achado distinto: `TP_ACHADO` (`telefone`/`email`/`valor`/`conta`/`agencia`/`processo`/`procedimento`/`tipificacao`/`endereco`/`oab`), `TX_VALOR`, `VL_NUMERICO` (só em `valor`), `NU_FOLHA` (da 1ª ocorrência), `QT_OCORRENCIAS`, `TX_CONTEXTO` (TEXT) |

Índices: `IX83` (TB01 por inquérito), `IX84`–`IX86` (TB02–TB04 por IPL), `IX87` (TB04 por
IPL + tipo, usada pelo filtro de tipo da aba Achados). `apagar_ipl` remove a cascata completa
(peças → pessoas → achados → cabeçalho) e, por padrão, o documento de texto vinculado
(`INQ_TB04_DOCUMENTOS`) também — é o mesmo PDF, veio no mesmo clique.

## Configuração

- Pasta de dados: `PASTA_DADOS`/`PASTA_INVESTIGACAO` (`api/funcoes/investigacao_utils.py`),
  com subpastas por delegacia quando multidelegacia (ver `permissoes_delegacia.md`).
- A análise de entidades por IA (documento anexado ao inquérito, e a extração/identificação
  do Caso) usa a aba **Análise de documentos** de Configurações (`DOC_AI_*`); campo em branco
  ali cai no campo correspondente da IA do prompt (`AI_*`). Sem provedor em nenhuma das duas, o
  anexo do PDF segue funcionando (texto disponível para leitura e busca semântica), só a análise
  não roda. Ver `configuracoes_ia.md`.
- Além do bloco `DOC_AI_*`, a prova avulsa obedece a: `UPLOAD_DOCUMENTO_MB` (teto do upload,
  Administração → Desempenho), `DOC_IMPORTE_SINCRONO_MB` (padrão 25 — acima disto o documento
  importa pela fila `import_documento`; 0 desliga), `UPLOAD_ORFAO_DIAS` (padrão 7 — idade em que
  um upload solto e sem registro na raiz de `data/` é movido para `data/_lixeira/uploads/`;
  0 desliga) e `LIXEIRA_DIAS` (padrão 30 — prazo na `_lixeira` antes do expurgo definitivo).
  Os três últimos são lidos por `api/funcoes/higiene_data.py` (passe diário do
  [agendador](resumo_noturno.md), tarefa `higiene_data`) e por
  `api/funcoes/zip_import.limite_doc_sincrono_bytes`.

## Referências

- Telas: `web/importacao.html` (criação do Caso, JS: `js/importacao.js`), `web/investigacao.html` (lista dos Casos, JS: `js/investigacao.js`),
  `web/caso_grafo.html`, `web/caso_analitico.html` (JS: `js/caso_analitico.js`),
  `web/caso_dados.html` (JS: `js/caso_dados.js`), `web/caso_identidades.html`
  (JS: `js/caso_identidades.js`), `web/caso_achados.html` (JS: `js/caso_achados.js`),
  `web/caso_timeline.html` (JS: `js/caso_timeline.js`), `web/ia_caso.html`,
  `web/busca_entidades.html`, `web/inquerito_pdf.html` (JS: `js/inquerito_pdf.js`).
- Rotas: `api/rotas/documentos.py` (Casos + chat de IA do Caso),
  `api/rotas/investigacao.py` (documento anexado ao inquérito + IPL —
  `ExtracaoIn`, `_exigir_acesso_via_ipl`).
- Funções: `api/funcoes/documentos_utils.py` (extração de texto, Casos, análise de entidades
  por IA), `api/funcoes/achados_texto.py` (achados por regex, compartilha os padrões de
  `ipl_utils`), `api/funcoes/ia_caso.py` (chat de IA do Caso), `api/funcoes/ia_provedores.py`,
  `api/funcoes/ipl_utils.py` (parser do inquérito em PDF).
- Esquema: `db/init/23_casos.sql` (prompts por execução,
  resolução de identificações e trilha de auditoria),
  `db/init/27_inquerito_pdf.sql`, `db/init/22_embeddings.sql` (colunas de
  análise em `INQ_TB04_DOCUMENTOS` + fatiamento de embeddings).
- Relacionados: `chat_ia.md` (mesmo desenho de chat/function calling do Caso e do inquérito),
  `mesa_inquerito.md` (entidades/grafo, `TP_ORIGEM`/`ID_ORIGEM`), `investigacao_celular.md`
  (chaves PIX), `classificacao_imagem.md`, `rif.md` e `simba.md` (mesmo padrão "PDF anexado +
  parser determinístico sem IA" do IPL, e o mesmo `INQ_TB04_DOCUMENTOS` como destino do texto
  para a IA), `escopo-conversa-ia.md` (chat de IA restrito a um IPL), `busca_semantica.md`
  (fatiamento do documento anexado), `convergencia.md` (aba "Casos ↔ acervo" — cruza os achados
  de `CAS_TB11_ACHADOS` com inquéritos e outros casos), `exportar_excel.md`,
  `permissoes_delegacia.md`, `auditoria.md` (auditoria de IA dos Casos e da análise de documento).
- Tela de disparo desta importação: [importacao.md](importacao.md) (aba correspondente de `web/importacao.html`).
