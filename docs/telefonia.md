# Telefonia — o retorno da operadora na quebra de sigilo telefônico

Deferida a quebra de sigilo telefônico, a operadora não devolve **um** arquivo: devolve uma pasta.
Este documento descreve como esses arquivos são reconhecidos na importação de arquivo genérico,
por que todos eles viram **um card só** no inquérito, e o que fica disponível depois disso.

Código: `api/funcoes/telefonia_utils.py`, rotas em `api/rotas/investigacao.py`, schema em
`db/init/43_telefonia.sql`, tela em `web/telefonia.html` + `web/js/telefonia.js`, camada de mapa em
`web/js/localizacoes_erbs.js`.

## O que chega

O exemplo abaixo é de uma resposta da VIVO; o layout muda de operadora para operadora, e por isso
nada aqui é lido por posição de coluna (ver "Como o formato é reconhecido").

| Arquivo | O que é |
|---|---|
| `DadosCadastrais - FULANO VIVO.txt` | relatório de pesquisa por CPF ou por linha, em texto de largura fixa: uma ficha por linha telefônica, com titular, CPF/RG, endereço, modalidade (pré/pós), situação e datas de habilitação/rescisão |
| `consulta_<linha>_id<protocolo>_<uf>.xlsx` | a planilha do ofício, com uma aba por assunto: **Relatório de chamadas** (o CDR), **Endereço das ERBs**, **Resumo dos Números** (número + IMEI), **Histórico de Chamadas WiFi** e **Histórico de Chamadas TuGO** |
| `consulta_....pdf` | o gêmeo impresso da planilha — é ele que costuma ser juntado aos autos |

E, na prática, mais do que isso: o período pedido no ofício costuma vir **quebrado em várias
levas**, cada uma num arquivo, principalmente as ERBs.

## A regra central: um card por operadora

O inquérito em PDF, o IRPF e a IPEI têm um bloco **por arquivo**. Aqui não daria: a resposta de uma
operadora é um material só, partido em N arquivos, e nada garante um identificador comum entre
eles (o TXT de cadastro sequer diz qual operadora o emitiu — só o nome do arquivo diz).

Por isso a chave do card é o par **(inquérito, operadora)**:

- todo arquivo VIVO do inquérito 123 cai no card `Telefonia — VIVO`, **em qualquer ordem de
  importação**; outra operadora abre outro card;
- a operadora é procurada no **nome do arquivo** e no **conteúdo** (a razão social aparece no
  cabeçalho do ofício: `Ref. 405928/2024 - TBRA - VIVO SP`). Não achando nenhuma, o card nasce como
  `Não identificada` — ele existe do mesmo jeito;
- `TEL_TB02_ARQUIVOS` registra **cada arquivo** que entrou, com as seções que trouxe e o período
  que cobre. É a procedência que o agrupamento esconderia, e é a aba "Arquivos importados" da tela.

### Por que nada duplica

Toda tabela filha carrega `TX_CHAVE` — o sha256 do conteúdo que **identifica** aquela linha —, com
índice único por card. A consequência prática:

- importar a planilha **e** o PDF do mesmo ofício dá **uma** lista de chamadas, não duas;
- reimportar o mesmo arquivo não duplica nada;
- a segunda leva de ERBs do mesmo período se funde com a primeira.

A chave é a identidade do registro, e não o retrato de todas as colunas: uma chamada é
`(tipo, data/hora, chamador, chamado, duração)`; uma ERB é o `CGI`. Isso é de propósito — a
planilha traz o CGI da chamada e o PDF não, e as duas leituras precisam **colidir** para virarem
uma linha só.

Quando colidem, o `ON CONFLICT` **completa** em vez de sobrescrever
(`COALESCE(excluded.coluna, tabela.coluna)`): a ERB lida do PDF, que vem sem endereço, ganha o
endereço quando a planilha entra. E há uma regra de precedência — `TP_FONTE` marca cada linha como
`planilha`, `pdf` ou `cadastro`, e a planilha **ganha** do PDF. Sem isso, importar o PDF depois da
planilha rebaixaria linhas que já estavam melhores.

## Como o formato é reconhecido

Nada é lido por posição de coluna: a mesma seção chega com colunas a mais ou a menos conforme a
operadora e conforme o que o ofício pediu.

- **Seção**: de três lugares, nesta ordem — o **título da aba** (ou da página, no PDF: "Endereço das
  ERBs", "Resumo dos Números", "Histórico de Chamadas TuGO"…); uma **linha de marcador** dentro da
  aba, quando a operadora põe o nome do bloco numa célula mesclada ("Chamadas Efetuadas" da Oi,
  "Relatório de Antenas" da Claro); ou, não havendo nem título nem marcador, o **próprio cabeçalho**,
  adivinhado pelas colunas (a TIM exporta em `Sheet0`, sem nada que nomeie a seção).
- **Coluna**: pelo nome no cabeçalho da tabela, normalizado (sem acento, minúsculo). Nome repetido
  ganha sufixo: o cabeçalho das ERBs traz **duas** colunas `Sigla` — a primeira é a central
  (`CORE PS`) e a segunda é a sigla da antena (`TRGZ1`).
- **Cadastro**: pela ficha em si (`* RÓTULO:.......VALOR *`), e não pelo timbre. Uma ficha começa em
  `NÚMERO DA LINHA` e **não** termina no separador de pontos — a operadora quebra a ficha quando
  ela não cabe, e um `DATA RESCISÃO` sozinho num bloco seguinte pertence à linha anterior.
- **Coordenada**: a operadora manda grau-minuto-segundo com o sinal colado (`-24-30-44,73`), às
  vezes com ponto no lugar da vírgula (`-9-33-50.2`), às vezes já em decimal. As três formas viram
  grau decimal em `VL_LATITUDE`/`VL_LONGITUDE`; o texto original fica em `TX_LATITUDE`/`TX_LONGITUDE`
  para conferência.
- **IMEI zerado** (`000000000000000`) é o que a operadora grava quando não sabe o aparelho do outro
  lado: entra como nulo, senão trinta números diferentes apareceriam "no mesmo aparelho".
- **"Não há registro…"** encerra a seção — é como a operadora responde uma aba vazia.

### Formatos por operadora

Cada operadora exporta o mesmo material num leiaute próprio. Os quatro já mapeados:

| Operadora | Arquivo | Como é lido |
| --- | --- | --- |
| **VIVO** | `.xlsx` / `.pdf` do ofício | seção pelo título da aba; uma seção por assunto |
| **Oi (TNL PCS)** | `.xls` que na verdade é **HTML** | cada `<tr>` vira uma linha; o bloco "Chamadas Efetuadas/Recebidas" é marcador de seção e o sentido diz de que lado está o alvo (que vem do cabeçalho "Número do terminal:"). Uma linha rende a chamada + a ERB (com lat/long) + a ficha do **interlocutor** (o bloco "Dados do Cliente" varia a cada linha — é de quem o alvo falou, não do alvo) |
| **TIM** | `.xls` binário (BIFF/OLE2) | por tipo (`VOZ`, `SMS`, `GPRS`); sem título nem marcador, a seção sai do cabeçalho. Data `AAAAMMDD`, hora `HHMMSS`, duração em segundos. Duas ERBs por chamada (RE = recebeu, RS = saiu); como a TIM **não manda CGI**, o identificador que casa chamada e antena é sintetizado do `SiteID`+`SetorID` |
| **Claro (Vigia)** | `.xlsx` (template `vigia-*`) | `CHAMADAS` traz o alvo em "CD Mobile", o outro lado em "Telefone de Destino" e o sentido em "Tipo de ligação IC"; a antena é só o CGI, e as coordenadas chegam do arquivo `ANTENAS` e completam a mesma ERB pelo CGI. Alvo e interlocutor viram fichas |
| **TIM — extrato de antena** | `.pdf` ("Relatório de Eventos por (ERB)"), **um por antena** | CDR **invertido**: todas as chamadas que passaram por uma ERB no período. Três seções: **REGISTRO DA PESQUISA** (as chamadas — `MOC` originada / `MTC` recebida), **DADOS SUPLEMENTARES** (procedimentos de assinante, ex.: consulta de desvio de chamada — entram como chamada sem destino/duração, com o serviço em `TP_TRAFEGO`) e **DADOS DE ANTENA** (a tabela de antenas COM coordenada, azimute e endereço — é o que põe a ERB **no mapa**; sem ela o CDR só teria o CGI). O layout muda com a versão do `pypdf` (ver abaixo), então tudo é lido por FORMA, não por posição de linha. `analisar_eventos_erb_tim`, fora do caminho do ofício |
| **Dados móveis** | `.xlsx` com ou sem cabeçalho | o relatório de sessões de dados: cada linha é um intervalo (`DTAINI`/`HORINI` → `DTAFIM`/`HORFIM`) em que a linha (`NUMLIN`) ficou presa a uma antena (`LOCALI`, o CGI) com um IP (`CODIPS`, mais o prefixo `IPV6`), um IMEI (`CODIME`) e volumes de entrada/saída (`VOLENT`/`VOLSAI`, em `UNIVOL`). Com cabeçalho (`LOCALI/CODIPS/NUMLIN/…`, às vezes com as colunas `ERB_*` de endereço no fim), a leitura é por nome de coluna como no resto; sem cabeçalho (o extrato por CGI, só dados), é posicional (`_MAPA_POSICIONAL_DADOS`), e a forma da linha (CGI + linha + data + hora) é conferida por amostragem antes de valer. A antena da sessão entra em `TEL_TB05` pelo CGI (é o que põe no mapa antena que o ofício de voz não listou), e a operadora, quando nome e timbre calam, sai do MNC do CGI por maioria (`_MNC_OPERADORA`) — sem isso o relatório abriria um card "Não identificada" ao lado do card de voz |

O `.xls` é ambíguo: o magic byte separa o binário da TIM (`0xD0CF11E0`, lido com `xlrd`) do HTML da Oi
(um `<`). A **operadora** também sai desses três lugares — nome do arquivo, timbre do conteúdo e,
para quem não se nomeia (a TIM não se identifica em lugar nenhum), a **assinatura de colunas**
(`Número A`/`Número B`/`SiteID`). Data compacta, hora compacta, `datetime` já pronto (Claro) e
duração `hh:mm:ss` (Oi) passam pelos mesmos normalizadores das datas/horas do ofício.

> **GPRS da TIM não validado.** O único exemplo em mãos veio **corrompido na origem** (cabeçalho
> íntegro, dados embaralhados) — `VOZ` e `SMS` do mesmo lote leem perfeitos. O mapa GPRS→sessões
> existe para um arquivo são, mas o leitor valida o IP como IPv4 e a data antes de gravar, então o
> exemplo corrompido não entra com lixo: simplesmente não rende sessão.

### Duas armadilhas que custaram caro

**A planilha não declara a área usada.** O XLSX da operadora grava `<dimension ref="A1"/>`, e o
`openpyxl` em modo `read_only` acredita: a aba de 87 linhas é lida como **uma linha vazia**, sem
erro e sem aviso. A correção é chamar `aba.reset_dimensions()` antes de iterar — e é **método**,
não atributo: `aba.reset_dimensions = True` apenas apaga o método, em silêncio.

**O `pypdf` 4.3.1 cola colunas.** A versão fixada em `api/requirements.txt` devolve
`...10:39:41 3Nao Compl. Voz-3` e `003CORE PS TRGZ1...`; versões mais novas separam, então o
problema **não aparece** rodando o parser fora do container. `_normalizar_linha_pdf` reabre a
fronteira entre dígito e letra. A fronteira inversa (letra seguida de dígito) fica de fora de
propósito — senão a sigla `TRGZ1` viraria `TRGZ 1`.

**A mesma colagem derruba o extrato de antena — de um jeito pior.** No PDF de eventos por ERB, a
4.3.1 cola a central com a origem (`ZRJO055531984306619`) e o CGI com a data
(`724-...-5313605/02/2019 20:09:10`); a 6.x do host empilha uma coluna por linha. O parser foi
escrito e conferido contra a 6.x, passou em 648 arquivos **fora** do container e, **dentro**,
devolvia zero evento → o PDF caía como documento genérico. Duas defesas: (1) ler o evento por
FORMA e não por posição de linha (`analisar_eventos_erb_tim` junta as linhas do evento e pesca
data/CGI/telefone/IMEI por regex, na ordem); (2) o telefone sai por um padrão **estrito** de
número brasileiro (`55` + DDD + `9\d{8}` móvel ou `[2-5]\d{7}` fixo), preso ao fim da linha — sem
isso, quando a central termina em `5` e a origem é um fixo de 12 dígitos, o `5` da central entrava
no número. Lição que repete a de sempre: **validar parser de PDF com a `pypdf` do container**, não
com a do host.

### O que o PDF rende a menos

O PDF é lido pelo mesmo caminho da planilha, com as células reconhecidas por **formato** (15 dígitos
é IMEI, 8 a 13 é telefone, `hh:mm:ss` é hora, grau-minuto-segundo é coordenada) em vez de por
posição, porque as colunas vazias simplesmente não existem no texto extraído. Ainda assim ele rende
menos:

- o **CGI da chamada** quebra em três linhas no meio do registro e fica de fora (a planilha o traz);
- **cidade e bairro da ERB** são um palpite: sem cabeçalho de coluna, uma cidade de duas palavras
  ("SÃO PAULO") sairia como cidade "SÃO" e bairro "PAULO …". A planilha do mesmo ofício corrige
  isso ao ser importada, pela regra de precedência acima;
- **WiFi e TuGO** não são lidas do PDF: quase sempre voltam "não há registro", e o TuGO começa pelo
  chamador em vez da data, o que sem cabeçalho viraria adivinhação. Vindo registro, ele entra pela
  planilha.

Importando os dois arquivos, nada disso se perde. Importando **só** o PDF, o investigador fica com
o CDR sem as antenas — que é exatamente o que está impresso ali.

## O que fica disponível

### No card do inquérito

Aba **Telefonia**, um bloco por operadora, com as contagens, o período coberto, quantos arquivos
entraram, o botão de conversa de IA restrita e o de achados do analista.

### Na tela `telefonia.html`

| Aba | Conteúdo |
|---|---|
| Chamadas | o CDR, com filtro por número, período, tipo, só-completadas e só-com-antena; a antena já vem resolvida pelo CGI, com endereço |
| Antenas (ERBs) | CGI, sigla, setor/azimute, tecnologia, endereço, coordenada e **quantas chamadas passaram por cada uma** |
| Cadastro de linhas | as fichas: titular, CPF, RG, endereço, modalidade, situação, habilitação e rescisão |
| Números e IMEI | o ranking de interlocutores, com filtro de **IMEI compartilhado** (números diferentes no mesmo aparelho) |
| Chamadas por WiFi | as sessões IP, quando a operadora mandou |
| Dados móveis | as sessões de dados: em que intervalo cada linha ficou presa a cada antena (com endereço resolvido), com que IP/IPv6 e IMEI e com que volumes — o paradeiro por dados, complementar ao CDR de voz |
| Arquivos importados | a procedência: qual arquivo trouxe o quê, de qual ofício e de que período |

### Na mesa de inquérito

A telefonia é a **única fonte que cria nó de telefone** — e é onde esse tipo de nó sempre fez
sentido: no CDR o vértice natural é a linha, não a pessoa. O número de quem ligou não tem nome
(tem volume de chamada, IMEI e horário), e forçá-lo a virar "pessoa" afirmaria uma identidade
que a operadora não deu. Então, na identificação de pessoas (`funcoes/identificacao_pessoas.py`,
`_coletar_telefonia`/`_materializar_telefonia`):

- cada **linha** — do cadastro (`TEL_TB03`) ou nomeada como alvo no ofício (`TEL_TB02`) — vira
  nó `TP_ENTIDADE = 'telefone'`, chave `tel:<número normalizado>`;
- o **titular da ficha cadastral** vira pessoa (CPF) ou empresa (CNPJ) e ganha a aresta
  `titular da linha (telefonia)`. É a ponte com o resto da mesa: o perfil entra no índice com
  **nome + CPF + o número**, e é o número que funde o titular da linha com o contato salvo no
  celular apreendido, com o alvo da IPEI e com o envolvido do RIF;
- o **CDR** vira aresta `ligou para (telefonia)` entre duas linhas, **simétrica** (ida e volta
  são a mesma aresta, com a soma das duas), com quantas chamadas, quantos minutos e em que
  período. Entram as `_TOP_CONTRAPARTES_TELEFONIA` (8) contrapartes de cada linha investigada,
  com piso de `_MIN_CHAMADAS_TELEFONIA` (3) chamadas aplicado **no `HAVING` da consulta** —
  sem isso a mesa viraria a lista telefônica do investigado;
- o **IMEI** (`TEL_TB06`) vira nó `dispositivo`, com a aresta
  `linha usada no aparelho (telefonia)`. A chave é `imei:<dígitos>`, **a mesma** que o UFDR usa
  para o aparelho apreendido: quando os dois coincidem, "a linha rodou neste aparelho" e
  "fulano é dono deste aparelho" apontam para o mesmo nó — que é o vínculo que se quer
  enxergar. Por isso o nó do IMEI vindo da operadora **não** sobrescreve o rótulo do nó
  (`atualizar_nome=False`): ali o nome bom é o do UFDR ("Apple iPhone 13"), não "IMEI 3591…";
- a contraparte de CDR que outra fonte já identificou ganha `usa a linha (telefonia)`.

Ver `mesa_inquerito.md`.

### No mapa

As ERBs com coordenada viram uma **camada própria** no mapa de localizações do inquérito
(`localizacoes.html?inquerito=<id>&erbs=1`, que é para onde vai o botão "ERBs no mapa"). Quem liga e
desliga a camada é o seletor **Fontes** da barra de filtros daquela tela, onde as antenas aparecem
ao lado dos aparelhos do inquérito; a etiqueta acima do mapa continua dizendo quantas são, de que
operadoras, e enquadra o mapa nelas. Elas ficam
num pane entre os polígonos de shapefile e os pontos da extração: são contexto do que o aparelho
registrou. E são do **inquérito**, não de uma extração — a camada ignora o filtro de aparelho da
tela, e por isso também não entra na contagem por área.

O círculo é desenhado em metros e é generoso de propósito: ele representa **a região coberta pela
torre**, não a precisão da antena e muito menos a posição da pessoa.

**A antena não tem data — a chamada e a sessão de dados têm.** Quem dá tempo à torre é o CDR e
o relatório de dados: cada chamada guarda o CGI por onde passou e a data/hora em que passou, e
cada sessão guarda o intervalo em que ficou presa à antena. Por isso os filtros de **data** e de
**faixa do dia** da tela de localizações chegam ao endpoint das ERBs e são aplicados às CHAMADAS
E ÀS SESSÕES DE DADOS. Com recorte temporal, a camada deixa de ser "as antenas do ofício" e vira
**"as antenas por onde a linha passou neste período"**: antena sem passagem no recorte não é
desenhada, o popup mostra a **primeira e a última passagem** dentro dele (o mínimo e o máximo dos
dois) e conta voz e dados **à parte** ("N chamadas" + "M sessões de dados" — somar as duas
misturaria "ligou" com "trafegou"). Sem recorte, volta o cadastro inteiro, com a contagem cheia.
Os demais filtros da tela (app, categoria, busca, área, aparelho) **não** valem para a antena:
são o universo do aparelho, e cruzá-los seria filtrar um com o crivo do outro.

A sessão é um intervalo, não um instante: o recorte por data pega **sobreposição** (a sessão vale
enquanto está ativa — uma sessão de três dias aparece filtrando pelo segundo dia), e a faixa do
dia filtra pelo início da sessão, com a mesma conversão para Brasília das chamadas.

A etiqueta da camada tem ainda o filtro de **tipo de torre** (todas/chamadas/dados móveis): desenha
só as torres que levaram chamada ou só as que prenderam sessão de dados. É filtro só de desenho, no
navegador — o servidor já manda as duas contagens por antena — e só aparece quando há antena com
dado. Com recorte temporal, "só dados" é "só as que prenderam dado NESTE período".

### O CGI vem em duas grafias

O identificador da antena no cadastro e no CDR nem sempre é a mesma string. Na Vivo, a relação de
antenas manda `724-11-43321-03476` (MCC-MNC-LAC-CI) e o CDR manda `2571 347 2` — que é
`NU_CCC` + `NO_ERB` + `NU_SETOR` **da mesma torre**. Casar só pela string cheia deixava a antena
sem chamada nenhuma: contava "0 chamadas" e, com filtro de data, sumia do mapa inteiro. A consulta
da camada casa **as duas grafias**; num ofício de 4.449 antenas isso levou o casamento de 0 para
3.807 antenas.

> Ainda **não** casado: o `LEFT JOIN` de `listar_chamadas_telefonia`, que resolve a antena de cada
> linha do CDR na tela da operadora, continua comparando só a string cheia. Nesses ofícios a coluna
> de endereço da antena fica vazia.

A contagem por antena sai de **uma passada agregada** sobre o CDR. O subselect correlacionado
anterior custava ~77 ms por antena — quase 6 minutos num ofício grande, e a requisição não voltava:
o mapa ficava sem camada, sem erro visível.

### Para a IA

Escopo `telefonia`, preso a uma operadora, com seis ferramentas: `obter_telefonia_operadora`
(o que existe), `buscar_linhas_telefonia` (de quem é o número), `buscar_chamadas_telefonia`
(o CDR, com filtro de número/CGI/período/só-completadas — o filtro por CGI responde "qual linha
usou esta antena"), `buscar_erbs_telefonia` (antenas por movimento, cada uma com `linhas_alvo` =
as linhas pedidas no ofício que a usaram, e filtro por `numero`), `buscar_numeros_telefonia`
(interlocutores, com busca por IMEI) e `buscar_dados_moveis_telefonia` (as sessões de dados por
linha/período, com a antena resolvida). As mesmas ferramentas ficam disponíveis na conversa do
inquérito inteiro, aí sem a trava de operadora.

O prompt do escopo carrega os três cuidados de leitura de CDR que mudam a conclusão: tentativa não
é conversa, antena é região e não posição, e ausência de registro fora do período pedido não
significa ausência de chamada. A tela repete os três num aviso fixo, acima das abas.

## Encontros por antena e ancoras (T1)

Tela `web/encontros_telefonia.html` (Extras): pares de linhas na mesma ERB dentro da janela
(`GET .../telefonia/encontros`, `listar_encontros_inquerito`) + residencia/trabalho provavel
(`GET .../telefonia/ancoras`, `listar_ancoras_inquerito`).

- So entram as linhas do cadastro (os alvos); sem cadastro, as 20 de maior volume no CDR.
- `janela_min` (5-480, padrao 30); `min_encontros` (padrao 2). Antena e regiao, nao posicao.
- Numeros normalizados (so digitos, sem o 55) dos dois lados, para a mesma linha se encontrar
  entre operadoras com grafias diferentes.
- Cobertura (`qt_chamadas_com_cgi`, `qt_dados_com_cgi`, `qt_erbs_no_mapa`): a tela explica o vazio
  ("CDR com 26 de 81 chamadas com antena") em vez de mostrar so tracos; linhas sem nenhuma ancora
  saem da tabela, com a contagem "N de M linhas avaliadas".
- Coluna Localizacao (encontros) e endereco nas ancoras: endereco da ERB + coordenadas + link de
  mapa quando a antena tem lat/long; CGI sem cadastro resolvido sai so com o CGI.
- Ancoras: residencia = mais madrugadas distintas (22h-06h Brasilia); trabalho = mais dias uteis
  09h-18h; minimo 3 dias. Ferramentas de IA `buscar_encontros_telefonia` e `listar_ancoras_telefonia`.

## Tabelas

| Tabela | Conteúdo |
|---|---|
| `TEL_TB01_TELEFONIA` | o card: uma operadora dentro de um inquérito (`UX101` garante a unicidade) |
| `TEL_TB02_ARQUIVOS` | cada arquivo importado, com seções, ofício e período |
| `TEL_TB03_LINHAS` | fichas cadastrais das linhas |
| `TEL_TB04_CHAMADAS` | o CDR; TuGO entra aqui com `TP_REGISTRO = 'TUGO'` |
| `TEL_TB05_ERBS` | as antenas, com endereço e coordenada |
| `TEL_TB06_NUMEROS` | o resumo número + IMEI |
| `TEL_TB07_SESSOES_WIFI` | sessões de chamada por WiFi |
| `TEL_TB08_DADOS_MOVEIS` | sessões de dados móveis (linha + intervalo + CGI + IP/IPv6 + IMEI + volumes) |

## Desempenho e paginação

O card grande tem 815 mil chamadas, 200 mil números e 4 mil antenas em produção — nada disso
cabe no navegador de uma vez. Por isso cada aba filtra e pagina **no servidor**
(`funcoes/telefonia_utils.py`, contrato `{total, pagina, limite, itens}`) e a tela
(`telefonia.html` + `telefonia.js`) só desenha uma página de 100 linhas da aba ativa:

- a tela abre só com o cabeçalho (totais do card) e busca a primeira página da aba ativa; as
  outras abas carregam sob demanda, ao clicar;
- a busca e os filtros viram `WHERE` no Postgres (cada termo precisa casar em pelo menos uma
  coluna); o debounce de 300 ms evita uma consulta por letra;
- `qt_chamadas` por ERB/número é calculada só para os ~100 da página (um `LEFT JOIN` por lote
  contra o CDR indexado), e não por subselect correlacionado sobre as 200 mil linhas;
- sem filtro, o total sai de `TEL_TB01` (uma linha) em vez de `COUNT(*)` sobre o CDR;
- a aba Números ordena por telefone por padrão (barato); `ordenar=chamadas` agrega o CDR e traz
  o ranking de interlocutores — mais caro, e por isso opt-in no select da aba;
- a exportação para Excel busca o filtro atual no servidor até 20 mil linhas; acima disso a
  tela pede para refinar o filtro em vez de entregar um `.xlsx` silenciosamente incompleto.

Índices que sustentam isso em `db/init/43_telefonia.sql` (+ `db/migracoes/0021_…`):
`IX299` (tipo do CDR), `IX300/IX301` (CGI origem/destino, parciais), `IX302/IX303` (UF e
coordenada da ERB), `IX304` (telefone do resumo) e `IX305` (situação do cadastro).

## Limites conhecidos

- **Teto por arquivo**: 200 mil linhas (`LIMITE_LINHAS_ARQUIVO`). A importação é síncrona, feita no
  clique da tela, como a dos outros arquivos genéricos.
- **Teto da exportação**: 20 mil linhas por aba (`LIMITE_EXPORT` na tela, `_LIMITE_MAX_TEL` no
  backend). O CDR inteiro continua navegável por paginação; para análise em massa, use a IA
  (ferramentas `buscar_*_telefonia`) ou refine o filtro antes de exportar.
- **Sem backfill**: arquivo de operadora anexado como documento comum antes desta versão continua
  sendo só texto para a IA até ser importado de novo — a leitura pega o arquivo do disco, que
  continua lá (ver [ufdr_correcao_parser_exige_backfill](../db/migracoes/README.md) para o mesmo
  raciocínio no UFDR).
- **Formatos validados**: VIVO, Oi (TNL PCS, HTML-em-`.xls`), TIM (`.xls` binário VOZ/SMS + extrato
  de antena em `.pdf`) e Claro (template Vigia, `.xlsx`), cada um conferido contra arquivo real (o
  extrato de antena, contra os 648 PDFs do CD `203637_2020`); **dados móveis** conferido contra
  `hc_dados.xlsx` (9.985 sessões, com cabeçalho e colunas `ERB_*`), o extrato por CGI sem cabeçalho
  (199.824 sessões no teto de 200 mil por arquivo) e o reduzido de Linhares. **GPRS da TIM** só tem exemplo
  corrompido — o mapa existe, mas não foi verificado (ver acima). Os demais nomes em `OPERADORAS`
  são reconhecidos para nomear o card, mas o leiaute deles ainda não foi conferido.
- **Códigos do relatório de dados guardados como a operadora escreve**: `TECNOL` (5/6/7), `TIPLOC`
  (L/G), `TIPTRA`/`TIPSER`, `CODOPE`, `TIPIAC`/`ORICHA` — o significado desses códigos não veio
  documentado no material em mãos, então a tela e a IA mostram o valor cru em vez de traduzi-lo.
  `TIPASS`/`STAREQ` (constantes nos exemplos) não são guardados.
- **Titulares não viram pessoas identificadas**: o nome e o CPF do cadastro ficam na tabela de
  telefonia e não alimentam a identificação de pessoas do inquérito
  (`api/funcoes/identificacao_pessoas.py`).

## Prova

`api/tools/teste_telefonia.py` importa uma pasta inteira contra o Postgres de verdade, depois de
novo na ordem inversa, e confere o que importa aqui: que tudo cai num card só, que nada duplicou,
que a planilha completou o que o PDF não tinha e que o CGI da chamada casa com alguma antena. No
fim apaga o card que criou (`--manter` para inspecionar).

```
docker compose exec api-1 python /app/tools/teste_telefonia.py <id_inquerito> <pasta em data/>
```
