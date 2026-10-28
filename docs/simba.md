# Extrato bancário do SIMBA (R304)

## Visão geral

Importação e análise do **extrato bancário do SIMBA (R304)** — o SIMBA (Sistema de
Investigação de Movimentações Bancárias, do MPF) exporta o afastamento de sigilo em vários
relatórios; o R304 é o dos **lançamentos** — um CSV por arquivo, na subpasta da conta em
`data/`, delimitado por `;`, codificado em **Windows-1252** (algumas instalações reexportam
em UTF-8, o sistema detecta), com o nome carregando "R304"
(ex.: `002-PF-002310-30_R304_20260727_171432.csv`).

> **Onde o arquivo fica depois.** Terminada a importação, o CSV é movido de
> `data/<DELEGACIA>/` para `data/<DELEGACIA>/<INQUÉRITO>/BANCARIO/` e o caminho novo passa a ser
> o valor gravado no banco — ver [organizacao_arquivos.md](organizacao_arquivos.md).


**Grão por perna (a regra que evita somar errado)**: o R304 traz **uma linha por perna da
operação** — os campos `od_*` ("outro documento") descrevem a contraparte, e um lançamento
com dois depositantes vira duas linhas com o mesmo `lancamento_codigo_simba` e
`od_valor_operacao` parcial. Logo: somar `od_valor_operacao` em todas as linhas dá o volume
**correto** (não duplica); contar lançamentos de verdade exige `COUNT(DISTINCT
cd_lancamento_simba)`.

Diferente do RIF, a importação roda em **background com progresso** — o extrator é um processo
externo que demora (minutos com um arquivo grande) e o proxy corta requisições em 60 s. A
página `web/simba.html` mostra Contas, Lançamentos (com paginação no servidor), Contrapartes,
**Alertas** (leituras determinísticas do extrato, sem IA) e o **grafo** do fluxo financeiro em
três colunas (banco → titular → contraparte).

## Fluxo de uso

1. Em `web/simba.html`, selecionar o arquivo/extrato (`smb-arquivo`; o seletor lista os
   `*R304*.csv` de `data/` com o tamanho) e o inquérito; a importação dispara o processamento
   em background e o front acompanha por **polling** do progresso
   (`CD_STATUS`/`CD_FASE`/`QT_PROGRESSO_FASE`). O **cabeçalho é validado na hora**, ainda
   dentro da requisição — arquivo errado vira mensagem no formulário, não um extrato em
   "erro" na tela.
2. **KPIs** (`smb-resumo`): Contas afastadas, Titulares, Lançamentos, Contrapartes, Crédito
   total, Débito total, **Saldo do fluxo** (crédito − débito) e Período — com tooltip no
   Crédito total avisando que ele soma tudo, inclusive o que não é recebimento de terceiro.
   Logo em seguida, **Recebido de terceiros**/**Pago a terceiros** (o número certo para "quanto
   X recebeu/pagou de outras pessoas", ver `_resumo_credito_extrato` abaixo). Condicionais,
   só quando o valor é maior que zero: **"Sem crédito/débito"** (`QT_SEM_NATUREZA` —
   lançamentos com `*` na natureza), **"Entre contas do próprio titular"**, **"Aplicação
   financeira / estorno"** e **"Outras sem contraparte"** (as duas últimas somadas é o antigo
   "Sem contraparte no extrato" — agora **separadas**, porque a primeira não é dinheiro de
   terceiro e a segunda é saque/tarifa/juros sem identificação). Se a importação ainda
   roda/falhou/cancelou, um banner de atenção ocupa o resumo inteiro ("Importação em andamento
   — o que aparece abaixo está incompleto…").
3. Abas (`smb-abas`) — o botão **"⬇ Excel"** da barra exporta a aba aberta (o nome do arquivo
   acompanha a aba). Nas abas sem paginação a planilha sai da própria tabela; na de
   **Lançamentos** ela é buscada no servidor, com os mesmos filtros, até fechar o total — antes
   saía só a página de 200 à vista, o que dava uma planilha truncada com cara de completa. Acima
   de 50 mil lançamentos a tela pergunta antes se leva só os primeiros. Ver `exportar_excel.md`:
   - **Contas** (`tab-contas`) — totais já apurados na importação, ordenadas por
     crédito+débito;
   - **Lançamentos** (`tab-lancamentos`) — paginação **no servidor**, 200 por página (R304 passa
     fácil de 100 mil linhas); filtros por conta, termo (descrição/contraparte/documento/observação
     com `ILIKE`), natureza (`C`/`D`/sem), período, valor mínimo; totais de C/D do filtro no
     rodapé;
   - **Contrapartes** (`tab-contrapartes`) — por conta/direção (`SMB_TB04`) e a visão
     **consolidada por pessoa** (somando todas as contas: "quem mais movimentou dinheiro com
     os investigados"), com `in_titular_no_extrato`;
   - **Alertas** (`tab-alertas`) — resumo + 5 sub-painéis (ver "Padrões e pesos" abaixo);
   - **Grafo** — nós `banco`/`titular`/`contraparte`/`outros` e arestas com
     `vl_entrada`/`vl_saida`; filtros por banco, só internalizados, PF/PJ, direção, valor
     mínimo e termo; limite de `_LIMITE_CONTRAPARTES_GRAFO` (máx. 150).
4. Cancelamento: botão marca `FL_CANCELAR` (fase vira `cancelando`); o worker encerra, os
   dados parciais são **descartados** e o status vira `cancelado` (ou `erro` com `TX_ERRO`).
5. Ao concluir, a **síntese** textual é gravada em `INQ_TB04_DOCUMENTOS` (documento do
   inquérito) para a IA responder sobre a movimentação sem precisar do CSV inteiro.

## Como funciona

### Descoberta e seleção (`simba_utils.py`)

- `_subpasta_simba(subpasta)` resolve `PASTA_DADOS/<subpasta>`; a subpasta da delegacia vem de
  `delegacia_permitida` nas rotas.
- `_RE_R304 = re.compile(r"r304", re.IGNORECASE)`; `listar_arquivos_simba` devolve cada
  `*.csv` com "r304" no nome (`{nome, tamanho_bytes}`) para o seletor.
- **`salvar_simba_enviado`** — o envio do R304 pelo navegador (abas "Bancário SIMBA R304" de
  `web/importacao.html` e do modal "+ Importar" de `web/inqueritos.html`, via
  `POST /api/investigacao/simba-upload`). Espelha `documentos_utils.salvar_documento_enviado`
  (gravação em blocos, sufixo `(2)` em colisão de nome, limite de `UPLOAD_DOCUMENTO_MB`), mas
  só aceita `.csv`, **confere o cabeçalho antes de dar o arquivo por salvo** (um CSV que não é
  R304 nem chega a ficar em `data/` — reusa `validar_cabecalho` acima) e **garante "R304" no
  nome**, prefixando quando falta, porque a listagem só enxerga `*R304*.csv`.
- `_validar_arquivo` barra path traversal (`/`, `\`, `.`, `..`) e exige `.csv` + "r304".
- `validar_cabecalho` lê **só o cabeçalho** e recusa na hora qualquer arquivo que não tenha as
  `_COLUNAS_OBRIGATORIAS = (conta_numero_conta, lancamento_data, od_valor_operacao,
  lancamento_natureza)` — com a importação em background, um erro descoberto depois viraria
  um extrato em "erro" na tela em vez de mensagem no formulário.
- `_detectar_encoding` lê 1 MB do começo: BOM UTF-8 → `utf-8-sig`; decodifica UTF-8 → `utf-8`;
  senão `cp1252` (o começo já traz acentuação suficiente para decidir).

### Engenharia de dados na leitura

- **`_iterar_csv`** — percorre linha a linha **sem carregar tudo na memória**
  (`io.TextIOWrapper` sobre o binário); a cada 2 mil linhas chama `ao_progredir(bruto.tell())`
  para a barra de progresso. A posição vem do buffer binário, que lê adiante — por isso o
  progresso trava em 99% até a fase acabar.
- **`_txt`** — remove o **apóstrofo que o SIMBA põe para o Excel não comer zero à esquerda**
  (`'2028` → `2028`); vazio, `-` e `0` isolado (banco/agência usam `0` como "sem informação")
  viram `None`. `_texto_livre` é igual mas mantém `0` (descrições podem ser `0`).
- **`_doc`** — CPF/CNPJ só com dígitos e **zero à esquerda recomposto**: o R304 traz o
  documento do TITULAR com zeros comidos (CNPJ `03.785.719/0001-73` vem como `3785719000173`,
  13 dígitos) enquanto o da CONTRAPARTE costuma vir completo (14) — sem repor, a mesma
  empresa nunca casaria dos dois lados e o cruzamento "contraparte também é titular" não
  acharia nada. Regra: 8–11 dígitos completam para 11 (CPF); 12–14 completam para 14 (CNPJ);
  abaixo de 8 não é documento (fica como veio, sem virar CPF inventado). Documento de dígito
  único repetido (`99.999.999/9999-99`) é o sentinela de "não informado" → `None` — senão
  milhares de contrapartes não identificadas se juntariam num só CNPJ de dezenas de milhões.
  **O caminho inverso também acontece**: o documento da CONTRAPARTE vem num campo largo, de
  CNPJ, e o banco preenche o CPF com zeros até 14 (`032.533.836-10` → `00003253383610`). Lido
  como CNPJ, vira `00.003.253/3836-10` na tela e a **mesma pessoa aparece duas vezes**, uma como
  CPF e outra como um CNPJ inexistente — inclusive "repassando dinheiro para si mesma" no alerta
  de repasses entre titulares. Por isso os 14 dígitos passam pelo **dígito verificador**
  (`funcoes/cpf_cnpj_utils.py`): se não fecham como CNPJ mas os últimos 11 fecham como CPF, é
  CPF. A checagem — e não um simples "tem zeros na frente" — é o que preserva os CNPJ legítimos
  começados em zero (`00.000.000/0001-91`, do Banco do Brasil). Extratos importados **antes**
  dessa correção têm o documento gravado nas duas formas; `reprocessar_titular_interno_simba`
  normaliza e reconsolida (ver abaixo).
- **`_valor_br`** — usa **`Decimal`**, não `float`: os totais por conta são somados aqui,
  linha a linha, ao longo de mais de cem mil lançamentos — em float o arredondamento binário
  faria o total fechar com centavos de diferença do extrato original, que é justamente o que
  o investigador confere. Parsing pt-BR (`281.500,00` → `281500.00`).
- **`_data_br`** — `dd/mm/aaaa`; **`31/12/9999` é o sentinela do SIMBA para "conta não
  encerrada"** (a data de encerramento da maioria das linhas) → vira `None`, senão a tela
  informaria toda conta aberta como encerrada no ano 9999.
- **`_natureza`** — `'C'`/`'D'`; qualquer outra coisa (o CSV traz `*` quando a instituição não
  classificou) vira `None` — esses lançamentos aparecem à parte ("Sem crédito/débito"), em
  vez de entrar em crédito ou débito por chute.
- **`_chave_conta`** — identidade da conta: `conta_codigo_simba` quando existe; senão
  `banco-agencia-conta` concatenados.

### Importação em background

`iniciar_importacao_simba` (o que fica NA REQUISIÇÃO, com resposta imediata): validar o
arquivo e o cabeçalho e garantir o inquérito (`_obter_ou_criar_inquerito`); cria a linha em
`SMB_TB01` com `CD_STATUS = 'pendente'` e dispara `threading.Thread(target=_processar_importacao)`.
O resto roda fora da requisição:

1. Marca `processando` + `DT_INICIO_PROCESSAMENTO`; calcula e grava os **hashes**
   (MD5/SHA1/SHA256, streaming em blocos de 4 MiB — ver `hash_arquivos.md`).
2. **`_carregar_extrato`** — duas passadas pelo CSV:
   - **Fase `contas`**: `_levantar_contas` monta em memória o cadastro das contas e seus
     totais (linhas, crédito, débito, `sem_natureza`, `dt_min`/`dt_max`), **sem tocar no
     banco**; linha sem conta identificável (rodapé/legenda) é descartada. Grava os totais no
     cabeçalho e insere as contas em `SMB_TB02` (`RETURNING id_conta_simba`). Extrato sem
     nenhuma conta levanta erro ("confira se é mesmo o relatório R304").
   - **Fase `lancamentos`**: **COPY** (`cur.copy`) das 23 colunas em `SMB_TB03` — é o que
     torna viável um arquivo de centenas de milhares de linhas; linhas cuja chave de conta
     não resolveu são puladas.
   - **Fase `contrapartes`**: `_consolidar_contrapartes` — um `INSERT … SELECT` que agrega
     `SMB_TB03` em `SMB_TB04` por `(conta, direção, coalesce(doc, 'NOME:'||upper(nome)))`; o
     **nome, banco e conta saem do lançamento de MAIOR valor do grupo** (`array_agg ORDER BY
     vl_operacao DESC`), não de um `max()` alfabético — o mesmo CNPJ costuma aparecer escrito
     de formas diferentes, e o rótulo da relação principal é o que descreve o vínculo. Só
     entram natureza C/D com alguma identificação. Em seguida marca
     **`in_titular_no_extrato = true`** nas contrapartes cujo CPF/CNPJ também é titular de
     conta afastada no mesmo arquivo (`EXISTS` contra `SMB_TB02`) — é o dinheiro circulando
     entre os próprios investigados. O casamento aceita **dois formatos**: `cp.nu_cpf_cnpj =
     c.nu_cpf_cnpj` (caso comum) **ou** `right(cp.nu_cpf_cnpj, 11) = c.nu_cpf_cnpj` quando o
     documento da contraparte veio com 14 dígitos e os últimos 11 são exatamente o CPF (11
     dígitos) de um titular daqui — o caso do CPF da contraparte gravado num campo largo de
     CNPJ (ver nota em `_doc`, acima). Extratos importados **antes** dessa correção têm o flag
     desatualizado para esse caso específico; reaplicar exige rodar de novo só esse `UPDATE`
     (idempotente, não mexe em mais nada) e regenerar a síntese.
   - **Fase `sintese`**: `_montar_sintese` gera o texto (abaixo) e insere em
     `INQ_TB04_DOCUMENTOS` (`tp_arquivo='csv'`, nome "Síntese do extrato SIMBA — <arquivo>"),
     ligando em `ID_DOCUMENTO_SINTESE`.
3. `_finalizar('concluido')` — em conexão própria e tolerante a falha (se nem isso funcionar,
   o extrato fica em `processando`, visível e investigável no banco). Qualquer exceção fecha
   com `erro` + `TX_ERRO[:2000]`; `_Cancelada` fecha com `cancelado`.

**Progresso e cancelamento**: as fases/progresso usam uma **conexão só deles**
(`conn_status`), nunca a da carga — durante o COPY a conexão de trabalho está em modo de
cópia e não aceita outro comando, e cada gravação de progresso precisa de COMMIT para a tela
enxergar (o que encerraria a transação da carga no meio). `_marcar_fase` zera o progresso a
cada fase (a que não sabe medir fica sem porcentagem); `_marcar_progresso` grava 0–99 só
quando o inteiro muda e trava em 99 até a fase acabar; o cancelamento é conferido a cada ~10
checkpoints (20 mil linhas), não a cada bloco. `solicitar_cancelamento_simba` marca
`FL_CANCELAR = true` + fase `cancelando` (só se `pendente`/`processando`).

**Descarte da carga parcial** (`_descartar_carga_parcial`): **extrato parcial é pior que
extrato nenhum** — os totais por conta foram gravados na 1ª passada e ficariam maiores que os
lançamentos de fato carregados, e ninguém perceberia olhando a tela. `rollback` na entrada é
obrigatório (se a interrupção veio de falha do próprio banco, a transação está abortada),
depois apaga TB04/TB03/TB02 e zera os totais do cabeçalho. A linha do extrato fica, com o
status, para o investigador ver o que houve.

**Por que background**: o extrato chega a ~117 mil lançamentos/48 MB e o processamento leva
~17 s — acima do limite de 60 s do proxy reverso na pior janela (um arquivo de centenas de MB
mostraria "erro" numa importação que na verdade terminou bem). O padrão é o mesmo de
extrações UFDR e buscas automáticas (thread + polling de status).

### Síntese textual (o que a IA lê)

`_montar_sintese` monta: cabeçalho com contagens/período/totais; logo abaixo, a **quebra de
`_resumo_credito_extrato`** em texto (externo/interno/financeiro próprio/outros sem
contraparte, cada um com um aviso explícito de que o crédito/débito total sozinho **não**
responde "quanto recebeu de terceiro"); **CONTAS** (até 500, ordenadas por crédito+débito, com
titular · doc · banco ag/cc · lançamentos · C · D · período); aviso de lançamentos **sem
contraparte** (nº e R$, "não entram nesta lista nem nos totais por contraparte", remetendo à
quebra do topo); **PRINCIPAIS CONTRAPARTES** consolidadas por pessoa (todas as contas, até
150, com "creditou R$ X · recebeu R$ Y" e "· TAMBÉM É TITULAR DE CONTA AFASTADA" quando for o
caso); **ALERTAS APURADOS AUTOMATICAMENTE** (espécie em R$/nº, fracionamento em nº, redondos em
nº/R$, internas em nº/R$, triangulação em nº — com os 25 primeiros detalhes de fracionamento e
internas e os 15 primeiros ciclos de triangulação, cada um com as três pernas). Os critérios são
escritos com os pesos em vigor, não com "R$ 10 mil" fixo. Anexada como documento do inquérito
para a IA responder sobre a movimentação sem precisar do CSV inteiro.

### Crédito de terceiro vs. crédito bruto (`_resumo_credito_extrato`)

**O erro mais fácil de cometer lendo um R304** (humano ou IA): tratar `SMB_TB01.VL_CREDITO`
como "quanto o titular recebeu de outras pessoas". Não é — é a soma de **tudo** que entrou na
conta, o que inclui três coisas que não são recebimento de terceiro:

1. dinheiro que só **circulou entre as contas do próprio titular** (ou de outro titular do
   mesmo afastamento) — `SMB_TB04.in_titular_no_extrato`;
2. **aplicação financeira automática** de fim de dia e o **resgate/rendimento**
   correspondente — o banco girando o saldo ocioso do titular sozinho;
3. **estorno/devolução** — a desfeita de uma operação anterior.

Os grupos 2 e 3 não têm nome nem CPF/CNPJ de contraparte no R304 (`coalesce(nu_cpf_cnpj_od,
no_od) IS NULL`), então **já ficam fora de `SMB_TB04_CONTRAPARTES`** — mas sem identificação
explícita eles somem dentro do genérico "sem contraparte", indistinguíveis de saque/tarifa/
depósito em espécie. Num extrato com conta de poupança/aplicação automática, isso pode ser
**mais da metade do crédito total** sendo o mesmo dinheiro entrando e saindo repetidas vezes —
foi exatamente o caso que motivou essa quebra: uma pergunta do tipo "quanto X recebeu de
contas de outros titulares" respondida com o crédito bruto errou por milhões de reais.

`_resumo_credito_extrato(cur, id_extrato, id_conta=None)` (wrapper público sem cursor:
`resumo_credito_extrato`) separa o crédito e o débito em quatro categorias que somadas fecham
o total:

| Categoria | Origem | O que significa |
|---|---|---|
| `externo` | `SMB_TB04` onde `NOT in_titular_no_extrato` | contraparte identificada que **não** é titular de conta afastada aqui — o único número que responde "recebido/pago de terceiro" |
| `interno` | `SMB_TB04` onde `in_titular_no_extrato` | contraparte identificada que **também** é titular — dinheiro entre os próprios investigados |
| `financeiro_proprio` | `SMB_TB03` sem contraparte, descrição casa `_RE_APLICACAO_AUTOMATICA` ou `_RE_ESTORNO_DEVOLUCAO` | aplicação automática/resgate/rendimento ou estorno/devolução |
| `outros_sem_contraparte` | `SMB_TB03` sem contraparte, resto | saque, depósito em espécie, tarifa, juros — o R304 não trouxe nome/CPF do outro lado |

Classificação por regex sobre `TX_DESCRICAO` (validada contra descrições reais de R304 —
Itaú/CAIXA/Banestes/MercadoPago — sem falso positivo encontrado):
`_RE_APLICACAO_AUTOMATICA = APLIC\w*\s*AUT|AUT\w*\s*APLIC|RESGATE\s*AUTOM|POUPAN[CÇ]A\s*AUT`
(cobre `"RES APLIC AUT MAIS"`, `"APL APLIC AUT MAIS"`, `"REND PAGO APLIC AUT MAIS"`) e
`_RE_ESTORNO_DEVOLUCAO = ^EST\s|\yESTORNO\y|\yDEVOLU` (cobre `"EST PIX TF <nome>"`,
`"EST ITAU SEG..."`, além da grafia por extenso).

> **`\y`, não `\b`.** Estas expressões são avaliadas pelo **Postgres** (operador `~*`), que usa
> ARE: ali `\b` é o caractere *backspace*, não a borda de palavra — a borda é `\y`. Escrito com
> `\b`, o trecho simplesmente nunca casa, sem erro nenhum. Era o caso de `\bESTORNO\b|\bDEVOLU`
> até esta correção: no extrato de teste, 614 lançamentos (R$ 2,6 mi) de estorno/devolução caíam
> em `outros_sem_contraparte` em vez de `financeiro_proprio`.

Aparece em três lugares:
- **`obter_extrato`** (cabeçalho da página) — os quatro pares C/D somados ao resultado, usados
  pelos KPIs "Recebido de terceiros"/"Pago a terceiros"/"Entre contas do próprio titular"/
  "Aplicação financeira / estorno"/"Outras sem contraparte" em `simba.js`;
- **`_montar_sintese`** — a quebra vira texto logo após "Crédito total/Débito total", com aviso
  explícito de qual número usar para "quanto recebeu/pagou de terceiros";
- **ferramenta de IA `resumo_credito_simba`** (`ia_investigacao.py`) — uma linha por extrato do
  inquérito (ou só o pedido em `id_extrato`); a descrição da ferramenta e a de
  `listar_contas_simba` se referenciam uma à outra para a IA não confundir crédito bruto com
  crédito de terceiro.

### Padrões e pesos dos alertas (`calcular_alertas`)

Tudo é **SQL determinístico — nada de IA** — para que cada número possa ser reconferido no
extrato original. Parâmetros:

- `_RE_SEM_TERCEIRO_REDONDO = _RE_APLICACAO_RESGATE | _RE_ESTORNO_DEVOLUCAO`, com
  `_RE_APLICACAO_RESGATE = APLICA[CÇ]|RESGATE|\yCDB\y|\yRDB\y|POUPAN[CÇ]|BB\s*FIX|FUNDO\s*DE\s*INVEST|INVESTIMENT`
  — o que sai da lista de valores redondos (ver abaixo).
- `_RE_ESPECIE = (SAQUE|SAQ\.|DEPOSITO|DEPÓSITO|DEP\.|DINHEIRO|ESPECIE|ESPÉCIE|NUMERARIO|NUMERÁRIO)`
  e `_RE_NAO_ESPECIE = (CHEQUE|CHQ|TARIFA|IOF|JUROS|RENDIMENTO|ESTORNO)` — filtro pela
  **descrição do lançamento** (é o que o R304 oferece): "operações que a instituição
  descreveu como saque/depósito em dinheiro"; cheques ficam fora **de propósito**. Não são
  configuráveis (são texto, não peso numérico).
- **Pesos numéricos, configuráveis** em **Administração → Alertas SIMBA**
  (`web/alertas_simba.html`, rota `GET`/`POST /api/config/simba-alertas`, admin-only) — gravados
  no `.env` como as demais configurações administráveis (`env_utils.py`), lidos a cada chamada
  por `pesos_alerta_simba()` (sem cache — o efeito aparece na consulta seguinte, sem reiniciar o
  servidor):

  | Peso | `.env` | Padrão de fábrica | Usado em |
  |---|---|---|---|
  | Valor de referência | `SIMBA_ALERTA_LIMITE_COMUNICACAO` | 10000 | valor mínimo de "redondo"; total mínimo do fracionamento |
  | Mínimo de lançamentos do fracionamento | `SIMBA_ALERTA_MIN_FRACIONAMENTO` | 3 | `HAVING count(*) >= `peso |
  | Múltiplo do valor redondo | `SIMBA_ALERTA_MULTIPLO_REDONDO` | 1000 | `mod(vl_operacao, `peso`) = 0` |
  | Giro minimo da passagem | `SIMBA_ALERTA_GIRO_PASSAGEM` | 50000 | soma C+D minima da conta |
  | Saldo residual maximo | `SIMBA_ALERTA_TOLERANCIA_PASSAGEM` | 10 | % sobre o maior lado |
  | Minimo de lancamentos da passagem | `SIMBA_ALERTA_MIN_LANC_PASSAGEM` | 10 | `qt_lancamentos >= peso` |

  Nenhum dos três é limite legal — são convenções de análise, por isso viraram ajustáveis em vez
  de fixos no código. `PESOS_ALERTA_SIMBA_PADRAO` em `simba_utils.py` é a fonte única do padrão
  de fábrica (usado tanto no fallback de `pesos_alerta_simba()` quanto no botão "Restaurar
  padrão de fábrica" da tela de administração).

Os 5 grupos (cada um com `LIMIT` próprio na lista, mas totais independentes no `resumo`):

| Alerta | Critério (SQL) | Colunas na aba |
|---|---|---|
| **Movimentação em espécie** | descrição casa `_RE_ESPECIE` e não casa `_RE_NAO_ESPECIE`, por conta (top 100 por valor) | Titular, CPF/CNPJ, Instituição, Agência/conta, Lanç., Depósitos, Saques, **Espécie**, **Movimentado na conta**, **% em espécie** |
| **Possível fracionamento** | `HAVING count(*) >= min_fracionamento AND sum(vl) >= limite AND max(vl) < limite` — N+ operações em espécie na **mesma conta, mesmo dia e mesmo sentido**, somando o valor de referência ou mais, cada uma isoladamente abaixo — o desenho clássico de quebra de valor (pode ter explicação legítima) | Titular, Conta, Data, Sentido (badge depósitos/saques), Operações, Total, Maior |
| **Repasses entre titulares** | `SMB_TB04.in_titular_no_extrato` (o próprio afastamento) | Titular, **Conta**, Sentido (recebeu de/repassou a), Contraparte (+ badge **"mesma pessoa"**), CPF/CNPJ, Lanç., Total, Período |
| **Triangulação** | `_triangulacoes` — ciclos **A → B → C → A** montados em Python sobre as mesmas linhas internas | Ciclo, Trechos (as três pernas), **Menor trecho**, Soma dos trechos, Lanç., Período |
| **Valores redondos** | `vl_operacao >= limite AND mod(vl_operacao, multiplo_redondo) = 0`, **fora** aplicação/resgate/estorno — valor combinado costuma vir redondo, ao contrário de nota/folha/tributo | Titular, **Conta**, Data, Descrição (+ "em espécie" e local), Contraparte, Valor, **Passo**, **Repete**, C/D |

Duas armadilhas de leitura, que a tela agora enuncia no texto do critério:

- **"% em espécie"** é `espécie ÷ (crédito + débito da conta)` — o denominador é TUDO que passou
  na conta, não a soma das colunas Depósitos e Saques ao lado. Sem o denominador na própria
  linha o percentual não fecha com nada visível e parece erro de cálculo; por isso `vl_especie`
  e `vl_movimentado_conta` vêm explícitos do SQL.
- **A lista de valores redondos deixa aplicação, resgate e estorno de fora**
  (`_RE_SEM_TERCEIRO_REDONDO`): esse dinheiro não muda de dono e é redondo por natureza
  (aplica-se R$ 1.300.000,00, não R$ 1.312.457,83). Num extrato real eles são os MAIORES valores
  redondos e, com a lista ordenada por valor e cortada em 200, ocupavam sozinhos o topo do alerta
  (no extrato de teste: 319 lançamentos, R$ 24,6 mi). Ficar de fora não é sumir — `qt_redondos`/
  `vl_redondos` seguem contando tudo, o que foi separado sai em `qt_redondos_internos`/
  `vl_redondos_internos` (dito no critério e no KPI) e a aba Lançamentos continua com todos.
  Essa expressão é **mais larga** que `_RE_MOVIMENTO_FINANCEIRO_PROPRIO`, que só quer a varredura
  automática de fim de dia: aqui a pergunta não é "o banco fez sozinho?", é "tem alguém do outro
  lado?".
- **Repasses entre titulares conta cada transferência duas vezes** quando as duas contas foram
  afastadas (é débito no extrato de quem pagou e crédito no de quem recebeu) — os totais não
  devem ser somados. E linhas com o **mesmo CPF/CNPJ dos dois lados** não são duas pessoas: é a
  mesma pessoa entre contas dela (o nome muda porque cada banco cadastrou de um jeito). Daí a
  coluna Conta dos dois lados e o `in_mesmo_titular`.

**Triangulação** (`_triangulacoes`, `LIMIT` 50, ordenado pelo menor trecho): o repasse interno
olhado como grafo. Cada linha de `SMB_TB04` interna vira uma aresta orientada entre CPF/CNPJ
(`right(doc, 11)` como chave, para casar a forma antiga preenchida até 14); como a mesma
transferência está gravada dos dois lados, cada aresta fica com a **visão de quem pagou** (o
débito), caindo para a de quem recebeu só quando o pagador não tem aquela conta no arquivo —
somar as duas dobraria o valor. Ciclos com `a == b` são descartados (contas da mesma pessoa não
formam triângulo) e cada ciclo é deduplicado pela rotação que começa no menor documento — a
ordem inversa é ciclo próprio, é dinheiro correndo no outro sentido. `vl_menor` é o gargalo: por
mais que um trecho movimente milhões, só pode ter dado a volta inteira o valor do menor trecho.
Acima de `_MAX_NOS_TRIANGULACAO` (300) titulares com repasse interno a apuração é pulada com
`logger.warning` — não por custo, mas porque o resultado deixaria de ser conferível um a um.

Resumo (independente dos LIMIT): `qt_especie`/`vl_especie`, `qt_redondos`/`vl_redondos`,
`vl_internas`, `qt_sem_natureza`, mais as quatro categorias de `_resumo_credito_extrato`
(acima). O payload traz ainda **`parametros`** (os pesos aplicados), para a tela, a síntese e o
relatório enunciarem o critério com o número em vigor em vez de repetir "R$ 10 mil" fixo. Na UI:
KPIs (espécie em R$, fracionamento em nº, redondos em nº, entre os investigados em R$,
triangulação em nº, aplicação financeira/estorno em R$) + 5 sub-painéis, cada um abrindo com
título e o **critério em texto** para o investigador saber exatamente o que o alerta significa.

### Consultas

- **`listar_extratos_inquerito`** (card da investigação) — LATERAL join do **titular com
  mais contas** (`array_agg ORDER BY qtd DESC`; "o alvo do pedido judicial; as demais contas
  costumam ser contrapartes trazidas para cruzamento"), hashes, status/fase/progresso/erro.
- **`obter_extrato`** (cabeçalho da página) — subqueries: `qt_lancamentos_distintos`
  (`COUNT(DISTINCT cd_lancamento_simba)` — o grão real), `qt_sem_natureza` e
  `qt_sem_contraparte`/`vl_sem_contraparte` (movimentação que NÃO aparece na aba de
  contrapartes), mais as oito colunas de `_resumo_credito_extrato` (`vl_credito_externo`,
  `vl_debito_externo`, `vl_credito_interno`, `vl_debito_interno`,
  `vl_credito_financeiro_proprio`, `vl_debito_financeiro_proprio`,
  `vl_credito_outros_sem_contraparte`, `vl_debito_outros_sem_contraparte` — ver seção acima).
- **`listar_lancamentos_simba`** — paginação do lado do servidor (mandar 100 mil linhas para
  o navegador, como o RIF faz com comunicações, não escala); contagem + soma de C/D do filtro
  na mesma query; `ORDER BY dt_lancamento DESC NULLS LAST, vl_operacao DESC`; tamanho
  clampado 200–1000.
- **`listar_contrapartes_simba`** (grão de TB04, por conta/direção, `ILIKE`, `so_internas`,
  limite 500–5000) e **`consolidar_contrapartes_simba`** (por pessoa, somando todas as
  contas: `qt_contas`, `qt_lancamentos`, `vl_creditou`/`vl_recebeu`, período,
  `bool_or(in_titular_no_extrato)`; mesmo agrupamento `coalesce(doc, 'NOME:'||upper(nome))`).
- **`serie_mensal_simba`** — crédito × débito por mês (`date_trunc`), "a curva que mostra
  quando a movimentação apareceu/parou".

### Grafo do fluxo financeiro (`grafo_simba`)

Montado **já agregado, em três colunas** (BANCO → TITULAR → CONTRAPARTE) porque a versão
anterior (um nó por pessoa, uma aresta por par, layout por força) virava um novelo — um R304
comum tem milhares de contrapartes. O que muda:

- **pessoa é UMA só**, somando todas as contas dela (a mesma empresa em cinco bancos era
  cinco nós antes);
- o par titular↔contraparte é **UMA aresta com os dois sentidos somados**
  (`vl_entrada` = a contraparte creditou o titular; `vl_saida` = o titular pagou), em vez de
  uma aresta por direção;
- o **banco entra como nó de origem**, ligado ao titular pelo total movimentado ali (com
  filtro de direção, a aresta do banco mostra só aquele sentido — senão contaria um movimento
  que o resto do desenho está escondendo);
- só as **N contrapartes de maior volume viram nó** (default `_LIMITE_CONTRAPARTES_GRAFO =
  25`, máximo 150) — todo o resto é somado num nó **"Demais contrapartes"**, para que o
  dinheiro que ficou de fora continue visível em vez de sumir da tela;
- devolve `{nos (tipo banco|titular|contraparte|outros), arestas, bancos, resumo}` com
  `qt_contrapartes_total/exibidas/agrupadas` e `vl_exibido`/`vl_agrupado` (o quanto foi
  engolido pelo nó agregado).
- Filtros: banco, só internalizadas, PF/PJ (`tp_pessoa`), direção, valor mínimo (no lado do
  servidor, sobre `vl_entrada+vl_saida`), termo. Na UI: cores por banco (hash da
  `CORES_BANCO`), layout por **baricentro** (pondera as posições pelo valor das arestas),
  zoom/pan.
- **Foco e multisseleção**: o desenho roda no motor compartilhado com o RIF
  (`js/utils/grafo_fluxo.js`), então vale o mesmo dos dois lados — clicar num nó abre a gaveta e
  põe em foco ele e as ligações dele, **Shift+clique acumula** e a gaveta passa a mostrar o
  resumo do conjunto (o que correu **entre** os selecionados, com o total em R$, e as
  contrapartes ligadas a mais de um deles). Ver `docs/rif.md` para o detalhe.
- **Exportação do grafo**: CSV, XLSX e ANX para o i2 Analyst's Notebook e **GraphML** para
  Gephi/yEd/NetworkX, sempre sobre o grafo filtrado inteiro — ver `exportar_grafo.md`.

### Operações administrativas

- **`solicitar_cancelamento_simba`** — botão na tela; `False` se não estiver
  `pendente`/`processando`.
- **`apagar_simba`** — cascata TB04 → TB03 → TB02 → TB01 + apaga o documento-síntese. Levanta
  **`ImportacaoEmAndamento`** se a importação ainda estiver rodando (apagar as linhas debaixo
  da thread deixaria o COPY gravando contra um extrato que não existe mais) — o caminho certo
  é cancelar primeiro e apagar depois.
- **`reprocessar_titular_interno_simba`/`_todos`** — aplica a extratos **já importados** as
  correções de CPF/CNPJ que vieram depois deles, sem reimportar o CSV: (1)
  `_normalizar_documentos_extrato` reaplica o `_doc()` atual aos documentos gravados em
  `SMB_TB02`/`SMB_TB03` — a conversão roda em Python, e não em SQL, para ser exatamente a mesma
  regra da importação, dígito verificador incluído; (2) apaga e refaz `SMB_TB04` a partir dos
  lançamentos já normalizados, o que junta as contrapartes que estavam partidas em duas linhas
  (CPF e o CNPJ inexistente) e remarca `IN_TITULAR_NO_EXTRATO`; (3) reescreve o texto da síntese
  existente. Devolve `{documentos_normalizados, contrapartes_corrigidas, sintese_atualizada}` e é
  idempotente (rodar sem nada para corrigir não muda nada). Sem parâmetro é para **todos** os
  extratos concluídos, de todos os inquéritos.
- **Rotas** (`api/rotas/investigacao.py`): `GET /api/investigacao/simba-disponiveis` (filtra
  pela delegacia), `POST /api/investigacao/extrair-simba` (payload `SimbaIn`: `arquivo`,
  `numero_inquerito`, `delegacia`), `GET .../inqueritos/{id}/extratos-simba`,
  `GET .../simba/{id}` e `/contas`, `/lancamentos`, `/contrapartes`,
  `/contrapartes-consolidadas`, `/serie-mensal`, `/grafo`, `/alertas`,
  `POST .../simba/{id}/cancelar`, `POST .../simba/{id}/reprocessar-titular-interno`,
  `DELETE .../simba/{id}`. Todas exigem `exigir_aprovado`; as que só recebem `id_extrato`
  passam por `_exigir_acesso_via_simba` (404/403). `POST
  .../simba/reprocessar-titular-interno-todos` é **admin-only** (`exigir_admin`) por cruzar
  todos os inquéritos — sem UI própria hoje, chamada direta (mesmo padrão de
  `POST .../rifs/{id_rif}/reprocessar-contrapartes`).
- A busca de entidades da Mesa consulta contas/lançamentos/contrapartes do SIMBA do inquérito
  (ver `mesa_inquerito.md`).

## Modelo de dados

Criado em `db/init/26_simba.sql` (status/progresso) e
`25_rif.sql` (hashes) — esquema `INVESTIGACAO`:

| Tabela | Papel |
|---|---|
| `SMB_TB01_EXTRATOS` | Cabeçalho: `ID_EXTRATO`, `ID_INQUERITO` (FK `INQ_TB01`), `NO_ARQUIVO`, `ID_DOCUMENTO_SINTESE` (FK `INQ_TB04`), `QT_CONTAS`, `QT_TITULARES`, `QT_LANCAMENTOS` (**pernas**, não lançamentos distintos), `QT_CONTRAPARTES`, `VL_CREDITO`/`VL_DEBITO`, `DT_PRIMEIRO`/`DT_ULTIMO_LANCAMENTO`, `ID_USUARIO`, `DT_IMPORTACAO` + progresso (`CD_STATUS` `pendente`/`processando`/`concluido`/`erro`/`cancelado`, default `concluido` para linhas antigas; `CD_FASE` `contas`→`lancamentos`→`contrapartes`→`sintese`/`cancelando`; `QT_PROGRESSO_FASE` SMALLINT 0–99; `TX_ERRO`, `FL_CANCELAR`, `DT_INICIO/FIM_PROCESSAMENTO`) + `CO_HASH_MD5`/`SHA1`/`SHA256` |
| `SMB_TB02_CONTAS` | Uma por `CD_CONTA_SIMBA` (chave do SIMBA; fallback banco-agência-conta): `TX_IDENTIFICADOR` (identificador com titular), `NO_TITULAR`, `NU_CPF_CNPJ` (com zeros recompletos), `TP_PESSOA` (`PF`/`PJ`), banco/agência/conta, `TP_CONTA`, `DT_ABERTURA`/`DT_ENCERRAMENTO` (9999 → NULL), `TX_ATENDIMENTO` (ofício/decisão), totais já apurados (`QT_LANCAMENTOS`, `VL_CREDITO`, `VL_DEBITO`, `DT_PRIMEIRO`/`DT_ULTIMO`) |
| `SMB_TB03_LANCAMENTOS` | **Uma linha por perna** `od_*`: `CD_LANCAMENTO_SIMBA` (repete nas pernas), `CD_OD_SIMBA` (único por linha), `DT_LANCAMENTO`, `TP_LANCAMENTO` (código numérico do SIMBA), `TX_DESCRICAO`, `NU_DOCUMENTO`, `TX_LOCAL`, `VL_OPERACAO` (valor da perna), `CD_NATUREZA` (`C`/`D`/NULL), `VL_SALDO`+`CD_NATUREZA_SALDO`, e os campos da contraparte (`NU_CPF_CNPJ_OD`, `NO_OD`, `TP_PESSOA_OD`, banco/agência/conta/tipo, `TX_OBSERVACAO_OD`, `TX_DOC_IDENTIFICACAO_OD`) + `NO_ENDOSSANTE_CHEQUE` |
| `SMB_TB04_CONTRAPARTES` | Consolidadas por conta/direção (`credito`/`debito`): `NO_CONTRAPARTE`, `NU_CPF_CNPJ`, banco/agência/conta, `QT_LANCAMENTOS`, `VL_TOTAL`, `DT_PRIMEIRO`/`DT_ULTIMO`, `IN_TITULAR_NO_EXTRATO` (contraparte que também é titular afastado aqui) |
| `INQ_TB04_DOCUMENTOS` | Recebe a **síntese** (`tp_arquivo='csv'`) ao concluir |

Índices: `IX108` (TB01 por inquérito), `IX109` (TB02 por extrato), `IX110` (TB03 por
extrato), `IX111` (TB03 por conta+data), `IX112` (TB03 por CPF/CNPJ da contraparte),
`IX113`/`IX114` (TB04 por extrato/conta).

## Contas de passagem e linha do tempo financeira

### Contas de passagem (F2)

Sexto alerta do extrato (`passagens` em `calcular_alertas`, `_contas_passagem` em
`api/funcoes/simba_utils.py`): conta com muito credito E muito debito, saldo residual pequeno
diante do giro e varios lancamentos. Usa os totais de `SMB_TB02`. Aparece na aba Alertas
(sub-aba "Contas de passagem"), como selo no grafo (`in_passagem`), na sintese da IA e no bloco
`bancario_alertas` do Word.

### Linha do tempo financeira (F3, Extras do inquerito)

Tela `web/timeline_financeira.html` (Extras): SIMBA + RIF por mes no mesmo eixo, KPIs, tabela e
CSV. Backend `timeline_financeira_inquerito`; ferramentas de IA `timeline_financeira` e
`listar_passagens_simba`.

A tela traz: filtros por período/fonte/tipo/valor persistidos na URL (EstadoUrl), KPIs do
período filtrado com saldo líquido e mês de pico clicável, linha de saldo líquido mensal no
gráfico, coluna `Δ SIMBA×RIF` (desalinhamento entre o saldo das duas fontes no mês), totais no
rodapé da tabela, flag `*` para meses com comunicações do RIF sem data de operação (caem na
data de recebimento) e gaveta lateral paginada (`GET .../financeiro-timeline/detalhes`, com
`limite`/`simba_offset`/`rif_offset` e `simba_total`/`rif_total`) com totais, CPF/CNPJ da
contraparte e link para a tela-fonte (SIMBA/RIF).

## Configuração

- Pasta de dados: `PASTA_DADOS`/`PASTA_INVESTIGACAO` (`api/funcoes/investigacao_utils.py`),
  com subpastas por delegacia quando multidelegacia (ver `permissoes_delegacia.md`).
- A importação em si não tem variáveis próprias de `.env`: roda em background com polling, no
  mesmo desenho de `INQ_TB02_EXTRACOES` (ver `09_extracoes.sql` ).
- **Pesos dos alertas** (opcionais — sem eles vale o padrão de fábrica): `SIMBA_ALERTA_LIMITE_COMUNICACAO`,
  `SIMBA_ALERTA_MIN_FRACIONAMENTO`, `SIMBA_ALERTA_MULTIPLO_REDONDO`, `SIMBA_ALERTA_GIRO_PASSAGEM`,
  `SIMBA_ALERTA_TOLERANCIA_PASSAGEM`, `SIMBA_ALERTA_MIN_LANC_PASSAGEM`. Editáveis em
  Administração → Alertas SIMBA; ver "Padrões e pesos dos alertas" acima.

## Referências

- Tela: `web/simba.html` (JS: `js/simba.js`).
- Administração dos pesos de alerta: `web/alertas_simba.html` (JS: `js/alertas_simba.js`),
  rota `api/rotas/simba_alertas_admin.py`.
- Rotas: `api/rotas/investigacao.py` (`SimbaIn`, `_exigir_acesso_via_simba`).
- Funções: `api/funcoes/simba_utils.py`; hashes em `api/funcoes/hash_utils.py`.
- Ferramenta de IA `resumo_credito_simba`: `api/funcoes/ia_investigacao.py`.
- Esquema: `db/init/26_simba.sql`, `25_rif.sql`
  `09_extracoes.sql` (padrão de cancelamento).
- Linha do tempo financeira (Extras): `web/timeline_financeira.html`, rota
  `GET /api/investigacao/inqueritos/{id}/financeiro-timeline` (SIMBA concluidos + RIF por mes).
- Relacionados: `rif.md` (mesmo desenho por-inquérito, semântica de direção e DESCONHECIDO),
  `hash_arquivos.md`, `permissoes_delegacia.md`, `investigacao_celular.md` (contas
  financeiras), `mesa_inquerito.md` (busca de entidades nos extratos),
  `relatorio_word.md` (blocos `bancario_*` usam só extratos `concluido`).
- Tela de disparo desta importação: [importacao.md](importacao.md) (aba correspondente de `web/importacao.html`).
