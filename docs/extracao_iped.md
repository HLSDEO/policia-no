# Extração Nuvem IPED

## Visão geral

A Nuvem IPED importa uma **pasta-caso inteira já processada pelo IPED** (ferramenta forense
usada pela polícia/perícia brasileira) — não um arquivo único como o `.ufdr`, e não uma pasta
de ZIPs como o Google Takeout, mas o resultado completo de um processamento do IPED: índice
Lucene, blobs de conteúdo comprimidos em SQLite (`storage/storage-N.db`), texto/OCR já
calculado, e o próprio JRE + jars do IPED embutidos (a pasta é auto-suficiente, dá pra abrir
em qualquer máquina sem instalar nada).

> **Onde a pasta fica depois.** Terminada a importação, a pasta-caso é movida de
> `data/<DELEGACIA>/` para `data/<DELEGACIA>/<INQUÉRITO>/NUVEM/` e o caminho novo passa a ser
> o valor gravado no banco — ver [organizacao_arquivos.md](organizacao_arquivos.md).


O formato foi verificado a fundo contra um exemplo real em `data/SIP/iped_google/` — ver
[extracao_java_iped.md](extracao_java_iped.md) para o relato completo da investigação
(inclusive dois caminhos que pareciam certos e não eram). Duas descobertas centrais:

1. **Não dá para ler o índice Lucene do IPED pela API "oficial" do IPED.** Abrir o caso via
   `iped.data.IIPEDSource`/`IItemReader` (as classes que a documentação do projeto IPED
   sugere) carrega todo o sistema de plugins/tasks do processamento (reconhecimento facial,
   Python via `jep`, etc.) mesmo para leitura pura, e trava/quebra fora do bootstrap completo
   da aplicação IPED. A solução foi ler o índice **diretamente com Apache Lucene puro**
   (`lucene-core`, já presente no `lib/` de qualquer caso) — mais simples, mais rápido, e sem
   nenhuma dependência do sistema de plugins.
2. **O primeiro exemplo real não era uma extração UFED/Cellebrite reprocessada — era uma
   pasta com os próprios ZIPs do Google Takeout, ainda não descompactados**, que o IPED
   expandiu como containers e processou (OCR, hash, categorização) por dentro.

> **A premissa do item 2 não vale como regra.** Uma pasta-caso do IPED pode ter vindo de um
> celular (UFDR decodificado), de uma quebra de sigilo de nuvem (Takeout, iCloud), de um HD
> de computador indexado, ou dos três juntos. A v1 desta extração foi desenhada em cima do
> único exemplo disponível e desviava DUAS das ~188 categorias que o IPED define (`Emails` e
> `Contacts`), mandando todo o resto para `ARQ_TB01_ARQUIVOS` como arquivo genérico. Só que o
> IPED **não indexa arquivo, ele decodifica**: cada mensagem de WhatsApp, cada ligação, cada
> senha e cada rede Wi-Fi vira um item próprio do índice com metadado semântico pronto — e
> tudo isso virava linha de arquivo chamada "chat_message_37". A v2 roteia por domínio
> (`funcoes/iped_dominios.py`), sempre com fallback: o que não casa com domínio nenhum segue
> exatamente pelo caminho de antes, e um caso de HD não paga nada pelo roteamento.

A implementação está em `api/funcoes/iped_utils.py` (orquestração) + `api/funcoes/iped_campos.py`
(camada de campos do índice) + `api/funcoes/iped_dominios.py` (roteamento por domínio) +
`api/funcoes/iped_cache.py` (binários e miniaturas) + `api/funcoes/iped_texto.py` (OCR e
transcrição prontos) + `api/funcoes/iped_marcadores.py` (marcadores do perito) +
`api/tools/iped_dumper/IpedDumper.java` (leitura do índice) e
`api/tools/iped_dumper/IpedBookmarks.java` (leitura dos marcadores); as rotas ficam em
`api/rotas/investigacao.py`; a tela é `web/investigacao.html` (aba "Nuvem IPED (pasta do
caso)" + card por caso importado).

## Fluxo de uso

1. Colocar a pasta do caso processado pelo IPED em `data/` (opcionalmente em uma subpasta de
   delegacia). A pasta precisa ter `IPEDConfig.txt` e `index/` direto dentro — ou conter uma
   subpasta `iped/` com isso (as duas estruturas são aceitas, ver "Como funciona").
2. Na tela Investigação, aba "Nuvem IPED (pasta do caso)", escolher a pasta no seletor. Ao
   escolher, a tela já sugere a conta (e-mail) detectada — o investigador confirma ou edita
   antes de extrair (campo aparece automaticamente, `POST /api/investigacao/iped-detectar-conta`).
3. Confirmar o inquérito e importar. A leitura do índice roda em background (subprocesso Java).
4. Acompanhar pelo card: badge "Processando" enquanto lê; ao concluir, as contagens de
   arquivos/e-mails/contatos aparecem, com botões para as telas compartilhadas com UFDR/Takeout
   (Arquivos, E-mails, Contatos).
5. **Ao concluir**, a importação dispara a **identificação de pessoas do inquérito** (o mesmo
   passo que o UFDR roda ao final da extração): contatos, ligações, contas, mensagens, a
   identidade do aparelho e o organizador/participantes de eventos lidos do caso viram pessoas,
   dispositivos e relações na **Mesa de Inquérito** (ver `mesa_inquerito.md`). Como a
   identificação é por inquérito, este caso é cruzado com os demais aparelhos e com os
   documentos financeiros anexados. O cancelamento preserva o parcial lido e também o identifica.
6. **Ao final da importação**, a pasta em `data/` é renomeada para a conta identificada (ou
   para o valor que o investigador confirmou/editou na tela) — facilita achar o caso certo
   depois só pelo nome. A renomeação só acontece ao final, não antes de iniciar (ver "Como
   funciona" sobre por quê).
7. Cancelar preserva o que já foi lido até a parada (mesma filosofia do UFDR — diferente do
   Google Takeout, que descarta a carga parcial). Apagar remove os dados importados (arquivos/
   e-mails/contatos) mas não a pasta em `data/`.

## Como funciona

### A pasta pode vir de duas formas

O construtor oficial do IPED espera `<raiz do caso>/iped/index`. Mas exemplos reais (inclusive
o deste repositório) chegam já **sem** esse wrapper — a pasta escolhida já É a pasta módulo,
com `index/`/`storage/`/`IPEDConfig.txt` direto dentro. `_pasta_modulo_iped()` (`iped_utils.py`)
aceita as duas formas: pasta com `IPEDConfig.txt`+`index/` direto, ou pasta que contém uma
subpasta `iped/` com isso.

### Leitura do índice (dumper Java)

Ver [extracao_java_iped.md](extracao_java_iped.md) para o relato completo. Resumo: um
subprocesso Java (`api/tools/iped_dumper/IpedDumper.java`, `_processar_importacao_iped` em
`iped_utils.py`) lê o índice Lucene do caso e imprime um JSON por linha em stdout — um por
item, com todos os campos armazenados daquele documento Lucene. O classpath usa os jars do
**próprio caso** (`lib/*.jar` — tem a versão certa do Lucene para aquele índice), não uma
cópia nossa; só o JRE vem da imagem da API (`default-jre-headless`, ver `api/Dockerfile`).

### Campos multivalorados

O Lucene permite o **mesmo nome de campo várias vezes** no mesmo documento, e o IPED usa isso a
rodo. A primeira versão do dumper emitia `"nome":"valor"` por ocorrência, o que produz **chave
JSON repetida** — e `json.loads`, do lado Python, fica só com a **última**. Medido contra o caso
de exemplo do repositório (2.765 itens):

| Campo | Valores que iam para o lixo | Campo | Valores que iam para o lixo |
|---|---|---|---|
| `Regex:URL` | 6.932 | `Regex:MONEY` | 111 |
| `Regex:EMAIL` | 5.272 | `category` | **60** |
| `Regex:PHONE` | 5.106 | `Regex:BR_CNPJ` | 8 |
| `phoneNumber` | 2.185 | `Regex:BR_CPF` | 3 |
| `userName` | 2.152 | `Communication:To` | 2 |

Dois danos além da perda de entidades: **60 itens ficavam com a categoria errada** (sobrava a
última) e **e-mail com vários destinatários perdia destinatário**.

Hoje o dumper agrupa por nome e emite **array** quando há mais de um valor (campo com um valor só
continua saindo como string, para não mexer em quem já consome). Do lado Python, nenhum
`campos.get(...)` é usado direto: `_valor`, `_valores` e `_juntar` (`iped_utils.py`) são a única
porta de entrada, e checagens como "este item é um e-mail?" viraram teste de pertinência
(`"Emails" in _valores(campos, "category")`).

### Documentos vazios do índice

O índice do IPED tem, além do documento de cada item, **um documento sem nenhum campo
armazenado** — no exemplo, 2.765 de 5.530 `maxDoc`, exatamente alternados. Não são documentos
deletados (o índice não tem nenhum arquivo `.liv`). Emiti-los fazia o Python parsear 50% de JSON
vazio à toa e, pior, `QT_ITENS_TOTAL` gravava `maxDoc` — **o dobro** do número real de itens.

O dumper agora os pula, e o protocolo do stderr separa as duas contagens:
`PROGRESS <docs lidos>/<maxDoc>` (o denominador do progresso, que é o único número conhecido de
antemão) e `DONE <itens emitidos>` (o que vai para `QT_ITENS_TOTAL`).

### Roteamento por item

Para cada item do stream, `_processar_importacao_iped` decide o destino pela categoria que o
próprio IPED já calculou (`category`):

| Categoria/MIME do IPED | Destino | Função |
|---|---|---|
| `Emails` | `EML_TB01_EMAILS` (mesma tabela do UFDR/Takeout) | `_email_de_item` — o IPED quebra o mbox numa mensagem por item, com campos semânticos prontos (`Communication:From/To/Date`, `Message-Subject`). **Corpo e anexo têm segunda via**: `Message-Body` não existe nos itens do `RFC822Parser` (conferido: nenhum dos 8 e-mails do caso iCloud tem o campo) e o texto extraído não é recuperável do índice (o `IndexTask` grava `content` com `Field.Store.NO`), então quando o campo falta o `.eml` bruto é lido do storage e parseado — ver `_corpo_e_anexos_do_eml` |
| `Chats`/`WhatsApp`/`Telegram`/… e `message/x-*-message` | `MSG_TB01..TB05` (as mesmas do UFDR) | `iped_dominios` — cada mensagem é um item próprio com `Message-From/To`, `Message-Body`, `Communication:Date`, `isGroupMessage`, `mediaName`, coordenada de mensagem de localização e a marca de apagada |
| `Calls`/`WhatsApp Calls`/… | `LIG_TB01_LIGACOES` | `ligacao_de_item` |
| `Contacts`/`Shared Contacts` | `CTT_TB01_CONTATOS`/`CTT_TB02` | `contato_de_item` pelos campos que o IPED já decodificou (`userName`, `phoneNumber`, `userOrganization`, `emailAddress`, `userAddress`…); só quando o item não tem nenhum campo semântico é que `_contatos_de_item` busca o blob e usa `takeout_parsers.parse_vcf` |
| `Locations`/`Journeys`/`Cell Towers` | `LOC_TB01_LOCALIZACOES` | `localizacao_de_item` |
| `Notes` · `Calendar`/`Appointments` | `NOT_TB01_NOTAS` · `EVT_TB01_EVENTOS` | `nota_de_item` · `evento_de_item` |
| `User Accounts` · `Financial Accounts`/`Credit Cards`/`Transfers of Funds` | `CTA_TB01_CONTAS_USUARIO` · `BNC_TB01_CONTAS_FINANCEIRAS` | `conta_de_item` · `financeiro_de_item` |
| `Passwords` · `Autofill` · `Wireless Networks` | `SNH_TB01_SENHAS` · `AFL_TB01_AUTOFILL` · `WIFI_TB01_REDES` | `senha_de_item` · `autofill_de_item` · `rede_de_item` |
| `Applications Usage`/`Android Applications` · `Internet History`/`Web Bookmarks`/`Searches` | `APP_TB01` · `NAV_TB01` | `aplicativo_de_item` · `navegacao_de_item` |
| `Folders`/`Empty Files` | pulado | — |
| tudo o mais (o caso comum: documentos, imagens, compactados) | `ARQ_TB01_ARQUIVOS` (idem) | `_item_para_arquivo` — hash (MD5/SHA1/SHA256), categoria normalizada pelo `contentType`, datas, identificadores e a triagem que o IPED já fez |

`conta_de_item` e `financeiro_de_item` têm guarda própria: item que cai na categoria "User
Accounts"/"Financial Accounts" mas **sem identificador real** (só o `name`, que é o nome do
arquivo — visto num caso iCloud com `graphstore_stash.db`, `storage.sqlitedb`…) devolve `None` e
segue como arquivo genérico, em vez de virar uma conta de usuário cujo titular é o nome de um
SQLite. É o que impede a identificação de pessoas de fundir o inquérito numa "pessoa" só (ver
`mesa_inquerito.md`).

Três regras que sustentam o roteamento:

- **Categoria não é traduzida.** O índice guarda sempre o nome canônico em inglês; a
  localização (`iped-categories_pt_BR.properties`) só é aplicada na exibição
  (`Category.getName()`, relatório HTML) e a busca do IPED desfaz a tradução antes de
  consultar (`QueryBuilder.getNonLocalizedCategory`). Conferido num caso brasileiro real, que
  grava `Contacts`/`Emails`/`PDF Documents`. Por isso casar em inglês é seguro.
- **O item traz a categoria FOLHA** (`WhatsApp`, `Phone Calls`), não a raiz da árvore
  (`Chats`, `Calls`) — daí as folhas listadas uma a uma em `iped_dominios`.
- **Ser de um domínio não tira o item da tela Arquivos.** Registro decodificado de dentro do
  banco de um aplicativo (chat, mensagem) não tem binário próprio — o `WhatsAppParser` cria o
  subitem com `EmptyInputStream` e `LENGTH` vazio — e não vira linha de arquivo. Mas um
  `Bookmarks.html` de um HD tem binário: alimenta `NAV_TB01` **e** continua aparecendo em
  Arquivos. É o que `_DOMINIOS_SEM_ARQUIVO` e `_tem_binario` decidem.

**Conversas exigem dois passes.** A mensagem é item FILHO do chat (`parentId` aponta para o
`id` do chat) e o dumper entrega os documentos na ordem do índice, que não garante o chat
antes das suas mensagens. Guardar tudo em memória não serve — um celular tem centenas de
milhares de mensagens —, então elas vão para um SQLite temporário indexado por `parentId`
(`_BufferMensagens`, criado só quando a primeira mensagem chega) e no fim, chat a chat, são
lidas de volta em ordem e gravadas por `_gravar_conversa_msg`, com commit a cada 50 conversas.
Mensagem cujo chat não apareceu cai numa conversa sintética por aplicativo, no mesmo espírito
do `CHAT_SEM_CONVERSA` do UFDR. Enquanto isso o card mostra a fase "Montando conversas".

Segue a mesma extração-ponte (`INQ_TB02_EXTRACOES`, `NO_FABRICANTE = 'IPED'`) que o UFDR usa —
é o que faz os arquivos/e-mails/contatos aparecerem nas telas genéricas (`arquivos.html`,
`emails.html`, `contatos.html`) filtrados por essa extração, e o que dá à identificação de
pessoas (`identificacao_pessoas.py`) e à mesa de investigação acesso aos dados **sem nenhuma
mudança nos coletores de mensagem/contato/ligação/conta** — eles são table-driven, não
fonte-driven. A única ponte que faltava era a da identidade do aparelho e dos eventos, que o
IPED grava nas tabelas genéricas de conta na nuvem (`DVC_TB01_DISPOSITIVOS` e
`EVT_TB01_EVENTOS`) em vez das tabelas do UFDR — cobertas por `_coletar_info_aparelho_nuvem` e
`_coletar_eventos_nuvem` (ver `mesa_inquerito.md`). A identificação roda automaticamente ao
concluir (ou cancelar) a importação — `_identificar_pessoas_do_caso` em `iped_utils.py`.

### Roteamento por produto do Google

A categoria do IPED resolve e-mail/contato/arquivo, mas ela não distingue um `History.json` do
Chrome de um JSON qualquer — para o IPED os dois são "Other Texts". Quando o caso foi
processado em cima de uma pasta de **Google Takeout** (o cenário do exemplo real, e o motivo de
a extração se chamar "Nuvem"), o IPED expande os ZIPs do Takeout como containers e **preserva o
caminho interno original depois de `>>`**:

```
Dados/<conta>.873696194465.Chrome.History_001.zip>>Chrome/History.json
```

O que vem depois do `>>` é exatamente o caminho que o Takeout usava, então
`takeout_parsers.destino()` classifica com o mesmo mapa (`_ROTEIRO`) da antiga extração "Nuvem
Google (ZIPs)", e os parsers dela decompõem o conteúdo. Cada item **continua virando linha em
`ARQ_TB01_ARQUIVOS`** — o roteamento por produto é enriquecimento, não desvio:

| Caminho interno | Destino | Tabela |
|---|---|---|
| `Chrome/History.json`, `Chrome/Bookmarks.html` | navegação | `NAV_TB01_PAGINAS_VISITADAS` |
| `Chrome/…Addresses and more…`, `Keep/*.json` | notas | `NOT_TB01_NOTAS` |
| `Access Log Activity/Activities - *.csv` | acessos | `ACS_TB01_ACESSOS` |
| `Access Log Activity/Aggregated Activities - *.csv` | serviços acessados + conexões entre identificadores | `ACS_TB02_AGREGADOS` + `ACS_TB03_CONEXOES` |
| `My Activity/**.html` | Minha Atividade | `ATV_TB01_ATIVIDADES` |
| `Google Play Store/Installs\|Install History\|Library\|User Activity` | aplicativos | `APP_TB01_APLICATIVOS` |
| `Android Device Configuration Service/*.html` | dispositivos da conta (traz IMEI) | `DVC_TB01_DISPOSITIVOS` |
| `Google Account/*SubscriberInfo.html` | perfil da conta | `PRF_TB01_PERFIL` |
| `Calendar/**/*.ics` | agenda | `EVT_TB01_EVENTOS` |
| `Duo/…Call History…` | ligações | `LIG_TB01_LIGACOES` |
| `Maps/…Labeled places…`, `Saved/*.json`, `Timeline/*.json` | localizações | `LOC_TB01_LOCALIZACOES` |
| `Drive/*-info.json`, `Google Photos/**.json` | **sidecar** de nuvem | `NUV_TB01_METADADOS_ARQUIVO` (ver abaixo) |
| `Mail/*.mbox`, `Contacts/*.vcf` | **ignorados aqui de propósito** | o IPED já resolve por categoria (ver tabela acima); roteá-los de novo duplicaria o dado |

`_IGNORAR_PADRAO` descarta o ruído do Takeout (`ExportSummary`, `NoRecords`, `Deletion
Markers`, `.txt`). Produto sem parser (hoje `Google Chat`, `YouTube`, `Google Meet`) não é
perda: o arquivo continua navegável, baixável e pesquisável por texto em `arquivos.html`.

### O sidecar: o metadado que a nuvem guarda SOBRE o arquivo

No Takeout um arquivo do usuário nunca vem sozinho. O Drive entrega `<nome>-info.json` ao lado de
`<nome>`, e o Google Fotos entrega `<foto>.jpg.json` (nas exportações mais novas,
`<foto>.jpg.supplemental-metadata.json`). Esses JSON **não são arquivos do usuário: descrevem
outro arquivo**, e trazem o que só o servidor sabe.

Até aqui eles caíam em `_TABELAS_IGNORADAS` e viravam só mais uma linha de `.json` em
`ARQ_TB01_ARQUIVOS`, com o conteúdo descartado. O que se perdia (conferido contra um `-info.json`
real do caso de exemplo):

- **quatro tempos, não um** — `creation_date`/`modified` são o que o cliente informou;
  `server_creation_date`/`server_modified` são carimbo do Google. Numa contestação de data é
  neles que se confia, porque mexer no relógio do aparelho não os altera;
- **quem mais tinha acesso** — `owners`, `creator_id`, `readers`, `writers` e o `raw_acl`, cada um
  com `gaia_id` + `email_address` + `display_name`;
- **estado de exclusão na nuvem** — `trashed` e `is_explicitly_trashed` (o segundo distingue "o
  usuário mandou para a lixeira" de "caiu junto com a pasta");
- **de onde a foto subiu** — `googlePhotosOrigin.mobileUpload.deviceType`/`deviceFolder`. O
  `deviceFolder` é a pasta de origem no telefone, e é ele que diz "veio de `WhatsApp Images`";
- **quando a foto foi tirada e onde** — `photoTakenTime` e `geoData`;
- **o log de atividade do arquivo** — `activity_records` (quem fez o quê e quando).

`funcoes/nuvem_metadados.py` decide o parser **pelo conteúdo** e não pelo nome (o `-info.json` do
Drive se reconhece pela chave `lis`; o do Fotos por `title`/`photoTakenTime`), então um `.json`
que não seja sidecar (`Photo Features.json`, `Deletion Markers.json`, `user_info.json`) segue
sendo só um arquivo comum.

É este metadado que sustenta a tela **Nuvem × aparelho**
([correlacao_nuvem.md](correlacao_nuvem.md)): sem ele dá para dizer que um arquivo está na nuvem e
não no celular, mas não dá para dizer que ele subiu **daquele** aparelho, vindo da pasta do
WhatsApp, naquela data — que é o que transforma a ausência em prova.

### Identificadores que o IPED já extraiu

O índice traz campos `Regex:<TIPO>` por item, produzidos pelas regras do `RegexConfig.txt` do
próprio caso. `iped_campos.identificadores_do_item` grava o resultado em
`ARQ_TB01_ARQUIVOS.TX_IDENTIFICADORES` (JSONB, índice GIN `jsonb_path_ops`), na forma
`{"cpf": [...], "placa": [...], "telefone": [...]}`.

**O conjunto de tipos não é fixo — vem do caso.** Um caso do repo tem 34 tipos, outro tem 18,
e uma operação pode acrescentar regra própria. A v1 tinha uma lista fechada de 8 campos e
descartava semente e chave privada de carteira cripto, conta bancária, CNH, boleto, título de
eleitor, chave de DANFE, MAC e SWIFT. Pior: procurava `Regex:BITCOIN_ADDRESS`, **um nome que o
IPED nunca emitiu** — o real é `CRYPTOCOIN_BITCOIN_ADDRESS`, e era `CRIPTOCOIN_` no 4.2.2 que
processou o caso do repo (a grafia CRIPTO→CRYPTO mudou entre versões). Hoje a varredura pega
**todo** campo `Regex:*`, `_canonico()` uniformiza a grafia, e tipo sem normalizador dedicado
entra com a chave em minúsculas e validação conservadora. Efeito medido no caso iCloud: CNH,
boleto e QR code passaram a ser gravados, e nada se perde quando o caso traz regra nova.

Os QR codes que o IPED decodificou (`QRCodeText`) entram no mesmo JSONB, sob a chave `qrcode`,
em vez de coluna própria: a pergunta é a mesma ("quais arquivos citam isto") e o índice GIN já
responde. Num celular é onde apareceria um BR Code de PIX; nos casos do repo são chaves de NF-e.

**Mas o valor cru do IPED é ruidoso, e passá-lo direto para o banco seria pior do que não ter a
coluna.** Medido no caso de exemplo:

- `Regex:BR_CAR_PLATE` traz **1.462 ocorrências de `iso-8859`** (a declaração de charset de um
  cabeçalho de e-mail) e `gmt-0300` — os dois têm exatamente a forma de placa antiga (3 letras,
  hífen, 4 dígitos), então nenhuma regex os separa de uma placa de verdade;
- `Regex:PHONE` traz centenas de carimbos epoch de cabeçalho (`1753850450`), com 10 dígitos e DDD
  plausível — indistinguíveis de um fixo pela forma.

Por isso cada tipo passa por um normalizador que devolve a forma canônica ou `None`, e os
validadores são **os do próprio projeto** (`achados_texto.cpf_valido`/`cnpj_valido`, já lapidados
contra PDF real) — assim o CPF que entra aqui é o mesmo CPF que a tela de achados considera
válido. Telefone exige celular de 11 dígitos ou formatação explícita; cartão passa por Luhn;
placa tem uma lista curta de prefixos técnicos (`ISO`, `GMT`, `UTC`…) que é o único filtro
possível. Efeito no caso de exemplo: placa cai de 1.362 para 12 arquivos, telefone de 1.223 para
261.

`Regex:URL` e `Regex:IP` ficam **de fora de propósito**: num item de e-mail rendem milhares de
valores de rastreador e cabeçalho de trânsito, que enchem o JSONB sem dizer nada sobre quem usou a
conta. `Regex:MONEY` também — valor não é identificador, ninguém procura por ele.

### Texto e transcrição: aproveitar o que o IPED já calculou

O texto reconhecido e a transcrição de áudio **não são campos do índice Lucene** — é por isso que
a v1 não os encontrou. São dois SQLite ao lado do índice, na pasta `text/` do módulo do caso:

```
<caso>/text/ocr-results.db     ->  CREATE TABLE ocr(id TEXT PRIMARY KEY, text TEXT)
<caso>/text/transcriptions.db  ->  CREATE TABLE transcriptions(id TEXT PRIMARY KEY, text TEXT, score REAL)
```

A chave `id` é o **MD5 do item em maiúsculas** — exatamente o que o índice devolve em `hash`/`md5`
e o que vai para `ARQ_TB01_ARQUIVOS.CO_HASH_MD5`. Quando o item rende mais de um bloco (página de
PDF, região de imagem), o IPED grava uma linha por bloco com sufixo `-child-N`.

`funcoes/iped_texto.py` (`TextosDoCaso`) lê os dois em modo somente-leitura, junta os blocos na
ordem numérica do `N` e preenche `TX_OCR`/`DT_OCR` e `TX_TRANSCRICAO`. O ganho: o PolícIA tem OCR
(`ocr_utils.py`) e transcrição (`transcricao.py`) próprios, que custam GPU/CPU e minutos por
extração para produzir **o mesmo texto que já está gravado ali**. A busca por faixa
(`id >= hash AND id < hash || '~'`) sai pelo índice da chave primária em vez de um `LIKE`, que
varreria a tabela por item.

As conexões SQLite seguram a pasta no Windows e são fechadas (`textos.fechar()`) antes do
rename/move do fim da importação, pelo mesmo motivo de `iped_cache.fechar_conexoes`.

Falha de parser é engolida com `logger.warning` (`_rotear_produto_google`) — um produto com
formato inesperado não derruba a importação inteira. Os contadores por produto ficam em
`IPD_TB01_EXTRACOES` (colunas `QT_*`, migração `19_iped.sql`) e viram os
chips e os botões do card, que só aparecem para o que aquele caso realmente trouxe.

### Datas: a linha do tempo do item

`created`, `accessed` e `changed` do índice vêm **string vazia** na maioria dos casos — nos
1.088 itens do caso iCloud do repo, em todos. A data de verdade está no par `timeStamp` ×
`timeEvent`, dois campos multivalorados e **alinhados posicionalmente** (é por isso que o
`IpedDumper` preserva a ordem de aparição dos valores). No mesmo caso o par trazia 920
`modified`, 252 `common:dcterms:created`, 240 `pdf:docinfo:created`, 22 `created`, 22
`accessed`, 8 `communication:date` e 4 `image:exif:datetimeoriginal`.

`iped_campos.datas_do_item` desmonta esse par em `DT_CRIACAO`, `DT_ACESSO` e `DT_MODIFICACAO`
(cada uma com uma ordem de preferência: o sistema de arquivos ganha do metadado do documento,
que ganha do EXIF da foto) e guarda a lista inteira em `TX_LINHA_TEMPO` (JSONB
`{"evento": "ISO"}`). A lista importa tanto quanto a data: é ela que separa "criado pelo
usuário" de "gravado pelo Ghostscript ao converter o PDF".

### Sufixo de tipo: a armadilha do nome de campo

`IndexItem.addMetadataKeyToDoc` **renomeia** campo de metadado sem tipo declarado: valor
numérico vira `chave:number`, data vira `chave:date`, e valor que não casa com o tipo
declarado vira `chave:string`. No dump do caso isso aparece como `CarvedOffset` **e**
`CarvedOffset:number`, `image:Version` **e** `image:Version:number`. Qualquer leitura de campo
de metadado (`pdf:`, `image:`, `ufed:`, `Message-*`) tem que tentar as quatro formas — é o que
`iped_campos.valor_variantes` faz. Para campo básico (`name`, `size`, `path`) `valor()` basta.

### Triagem que o IPED já fez

Quando as tasks correspondentes estão ligadas no caso, o IPED grava por item:

- `nudityScore`/`nudityClass` — detecção de nudez → `VL_NUDEZ`/`TX_CLASSE_NUDEZ`;
- `face_count` — quantos rostos na imagem → `QT_FACES`;
- `hashDb:status`/`hashDb:set` — o hash bateu com hashset carregado no caso. Vale `"pedo"`
  quando é hashset de material de abuso infantil (ver `WhatsAppParser.extractMessages`) →
  `TX_ALERTA_HASH`. **É alerta para triagem, não conclusão**: quem decide é quem analisa.

Nenhum é obrigatório — caso processado sem essas tasks simplesmente não tem os campos e as
colunas ficam NULL (foi o que aconteceu no caso iCloud do repo).

### Marcadores do perito

Dentro do IPED, o perito seleciona itens e os agrupa em *bookmarks* com nome e comentário
("material de interesse", "conversas do alvo"). É a triagem que **já foi feita**, e o
investigador recebia a pasta-caso com centenas de milhares de itens sem nenhuma pista de quais
tinham sido separados.

Ficam em `<caso>/iped/bookmarks.iped` — serialização de objeto Java, não banco nem XML (ver
`Bookmarks.saveState`). Ler isso do lado Python significaria reimplementar o formato, então a
leitura roda num subprocesso Java próprio, `IpedBookmarks.java`, que usa o desserializador
oficial com os jars do próprio caso no classpath. Resultado em `ARQ_TB01_ARQUIVOS.TX_MARCADORES`
(nomes separados por vírgula) e `IN_SELECIONADO`, casados pelo `id` do item — por isso o
`NU_ID_ORIGEM` também é gravado.

> **Por que é um programa separado do `IpedDumper`.** O `IpedDumper` foi escrito de propósito
> para não tocar em nenhuma classe do `iped-engine` (ver o cabeçalho dele). Aqui o
> `iped-engine` é necessário, porque só ele sabe desserializar `Bookmarks`. Separados, uma
> falha na leitura dos marcadores — versão incompatível, classe faltando, arquivo corrompido —
> não contamina a leitura do índice, que é a parte que não pode falhar. Precisa de
> `-Djava.awt.headless=true`: o objeto serializado carrega `java.awt.Color` e
> `javax.swing.KeyStroke` (a cor e o atalho de teclado de cada marcador).

Os marcadores são lidos **antes** do laço principal (é um subprocesso curto sobre um arquivo,
não toca o índice), o que deixa `TX_MARCADORES` entrar já no `COPY` em vez de exigir um passe
de `UPDATE`. Caso nunca marcado devolve vazio e nada muda.

### Miniaturas e EXIF que o IPED já produziu

O IPED gera miniatura de imagem e de vídeo durante o processamento e guarda numa **segunda
tabela do mesmo `storage-N.db`**: `thumbs(id TEXT PRIMARY KEY, thumb BLOB)`, chaveada pelo MD5
em maiúsculas (`ThumbTask.java`); o campo `hasThumb` do índice diz quais itens têm. Qual
`storage-N.db`: `ExportFileTask.getStorageSuffix` usa `(hash[0] & 0xFF) >> 4` sobre os **bytes**
do MD5 — que é exatamente o primeiro dígito hexadecimal do hash (conferido: hash começando em
"DB…" está em `storage-13.db`). `iped_cache.ler_thumb` calcula assim, e não pelo
`dataSourcePath` do item, porque item cujo binário veio do arquivo original
(`FileInputStreamFactory`) não aponta para storage nenhum e mesmo assim tem miniatura.

Numa grade de 100 fotos isso troca 100 descompressões de JPEG cheio (mais o HEIC do iPhone, que
ainda passaria pelo `pillow-heif`) por 100 leituras de alguns KB já prontos. Vale só para a
grade: a prévia do modal precisa da resolução cheia, e a thumb do IPED tem ~160px. Miniatura
ausente não é erro — cai no caminho de sempre, gerando a partir do binário.

O EXIF também estava no índice o tempo todo (`image:Width`, `image:exif:DateTimeOriginal`,
`image:Exif IFD0:Software` — que num caso real trouxe `Android TP1A.220624.014.G780GXXSAEXF1`,
o modelo do aparelho). A rota `/arquivos/{id}/metadados` devolvia `{}` para origem IPED; hoje
lê o binário do storage e passa pelo **mesmo** extrator do UFDR (`extrair_metadados_imagem`/
`extrair_metadados_video`), com `iped_campos.metadados_de_midia` disponível para quem preferir
o caminho sem I/O.

### Identificação da conta e renomeação da pasta

Dois sinais, em ordem de confiança (`_DetectorConta` em `iped_utils.py`):

1. Um item `user_info.json` com campo `"email"` no JSON (`{"user": {"email": "...", ...}}`) —
   sinal de máxima confiança, visto no exemplo real.
2. O e-mail mais frequente no nome/caminho dos itens, no mesmo padrão que o Google Takeout já
   usa (`_RE_CONTA` — Takeout ZIPs ainda não expandidos trazem o e-mail no próprio nome do
   arquivo, e é exatamente isso que o exemplo real contém).

A detecção acontece **durante** o mesmo passe que já lê os itens para a Fase 1/3 (sem
subprocesso extra) — só a pré-visualização da tela (`POST /iped-detectar-conta`, antes de
confirmar a extração) roda um dumper à parte, limitado a poucos milhares de itens.

A renomeação em si só acontece **depois** que a importação inteira termina, por dois motivos:

- Evita um segundo subprocesso Java só para detectar.
- **Achado real, testando neste ambiente (Docker Desktop no Windows, bind mount de `data/`)**:
  tentar renomear logo após qualquer subprocesso Java que tocou os arquivos do caso falha com
  `PermissionError` persistente — não é questão de esperar mais tempo, é uma conexão SQLite
  **cacheada** (`iped_cache`, TTL de até 1h) ainda aberta para um `storage-N.db` dentro da
  pasta, que o bind mount do Windows trata como arquivo aberto (semântica NTFS, que o Linux
  dentro do container não teria sozinho). `iped_cache.fechar_conexoes(pasta)` fecha essas
  conexões antes da tentativa de rename, e algumas tentativas com espera curta cobrem qualquer
  atraso residual.

Como a pasta só é renomeada ao final, o ponteiro de binário gravado por item
(`CO_CAMINHO_ZIP`) usa o **ID da extração-ponte**, não o caminho da pasta — `_resolver_
arquivo_iped` busca o `NO_PASTA` atual em `IPD_TB01_EXTRACOES` na hora de servir o binário,
sempre correto mesmo depois (e de qualquer renomeação manual futura).

### Servir binários

`api/funcoes/iped_cache.py` — mesmo padrão de cache com TTL do `ufdr_cache.py`, mas em vez de
`zipfile.read()`, resolve por hash MD5 dentro de `storage/storage-N.db` (tabela `t1`, gzip).
Cada item, ao ser lido, já vem com a informação de qual `storage-N.db` procurar
(`dataSourcePath`), então não é preciso adivinhar/varrer todos os arquivos de storage. Um
segundo formato (`dataSourceDecoder = iped.utils.FileInputStreamFactory`) aponta direto pro
arquivo original da fonte de dados (ex. um ZIP do Takeout ainda não expandido) — só funciona
se essa pasta ainda estiver ao lado do caso processado; no exemplo deste repositório ela não
está, e o erro nesse caso é um `FileNotFoundError` limpo (mesmo espírito do ".ufdr ausente").

A rota `GET /api/investigacao/arquivos/{id}/conteudo` (compartilhada com UFDR/Takeout) já sabe
servir os dois formatos de ponteiro (`"<zip>::<caminho>"` do UFDR/Takeout, `"IPED::..."` desta
extração) — sem rota nova.

## Não-objetivos (registrados como backlog)

> **Resolvidos desde a v1.** Os antigos itens 1 e 5 ("transcrição de áudio" e "texto/OCR já
> calculado pelo IPED não é carregado") **saíram do backlog**: o texto não estava no índice
> Lucene, mas em `<caso>/text/ocr-results.db` e `<caso>/text/transcriptions.db`, chaveados pelo
> MD5 do item — ver "Texto e transcrição" acima. Da indexação semântica, só ela continua
> pendente (item 6 abaixo). `DS_VERSAO_IPED` também deixou de ser "reservado": `versao_iped()` lê
> a versão do nome dos jars em `lib/` (`iped-engine-4.2.2.jar` → `4.2.2`).

1. **Indexação semântica de itens vindos do IPED** — a extração não dispara essa fase (que hoje
   só existe para UFDR). Reaproveitar o que já existe é natural, mas fica para uma iteração
   seguinte. A transcrição e o OCR, que estavam neste item, hoje vêm prontos do próprio caso.
2. **Deriva de versão do IPED entre casos** — o dumper só depende do Lucene (não mais de
   `iped-api`/`iped-engine`), o que já reduz bastante o risco de quebra entre versões
   comparado à API de objetos do IPED — mas ainda depende de nomes de campo relativamente
   estáveis. Se uma versão futura do IPED mudar esses nomes ou a versão major do Lucene, o
   dumper pode falhar; hoje isso vira erro amigável, não traceback cru, mas não há
   compatibilidade formal garantida. Uma correção mais robusta (compilar o dumper sob demanda
   contra os jars do próprio caso, o que já é feito para o classpath — só falta o `javac`)
   fica registrada como melhoria futura.
3. **Roteamento de domínio validado só sinteticamente para chat/UFED.** Os dois casos reais
   disponíveis no repo são de nuvem e não têm um único item de conversa, ligação, senha ou
   rede — o roteamento desses domínios foi exercitado com itens montados à mão no formato que
   `WhatsAppParser` e `UfedXmlReader` produzem, e `api/tools/teste_iped_gravacao.py` cobre o
   caminho de gravação com itens reais do índice, mas nenhum dos dois passa por uma conversa
   de verdade. Os nomes de campo saíram do código-fonte do IPED, não de um caso observado: quando
   aparecer um caso de celular de verdade, vale conferir chat a chat antes de confiar nas
   contagens. O desenho já protege contra erro aqui — item que não casa segue como arquivo, e
   falha de conversão vira aviso no log —, mas "não quebrou" não é o mesmo que "está certo".
4. **Um caso com vários aparelhos ainda colapsa numa extração-ponte só.** `CO_EVIDENCIA`
   (o `evidenceUUID` do item) já é gravado por arquivo, então dá para *saber* de que fonte de
   dados cada item veio; o que falta é criar uma extração-ponte por `evidenceUUID`, que é o
   que faria a tela Comparar celulares tratar os três celulares de um caso como três
   aparelhos. É a mudança de modelagem mais relevante que sobrou.
5. **`view/` e o grafo do Neo4j não são usados.** O IPED gera prévia renderizada em
   `view/<H>/<A>/<HASH>.<ext>` (sinalizada por `hasPreview`/`previewExt`) — para caso vindo de
   UFDR isso inclui o HTML renderizado do chat — e monta um grafo de relacionamento em
   `<caso>/neo4j/csv/`. Os dois são matéria-prima direta para o visualizador de documento e
   para a mesa do inquérito, e nenhum é lido hoje.
6. **`thumbnail` e `imageFeatures` do índice saem como `null`.** São campos binários; o
   `IpedDumper` só chama `stringValue()`/`numericValue()`. A miniatura já vem da tabela
   `thumbs` do storage (ver acima), mas o `imageFeatures` — o vetor de similaridade de imagem
   que alimentaria busca por imagem parecida — precisaria de `binaryValue()` + base64 no
   dumper.

## Modelo de dados

- `IPD_TB01_EXTRACOES` (`db/init/19_iped.sql`): uma linha por pasta-caso importada —
  `ID_EXTRACAO_IPED`, `ID_INQUERITO`, `NO_PASTA` (pasta em `data/`, atualizada quando
  renomeada), `NO_CONTA` (e-mail identificado), `DS_VERSAO_IPED` (lida do nome dos jars em
  `lib/` por `versao_iped()`), `QT_ITENS_TOTAL` (itens **emitidos** pelo dumper, não `maxDoc` —
  ver "Documentos vazios do índice"), contadores por destino
  (`QT_ARQUIVOS`/`QT_EMAILS`/`QT_CONTATOS`/`QT_CONVERSAS`/`QT_MENSAGENS`/`QT_LOCALIZACOES`/
  `QT_CONTAS_USUARIO`/`QT_CONTAS_FINANCEIRAS`/`QT_SENHAS`/`QT_AUTOFILL`/`QT_REDES`/
  `QT_MARCADORES` — os de domínio decodificado só se preenchem com fonte de celular; num caso
  de HD ou de nuvem pura ficam NULL, e é assim que se lê "este caso não tinha conversa
  nenhuma"), mais um contador por produto do Google
  (`QT_NAVEGACAO`, `QT_ACESSOS`, `QT_ACESSOS_AGREGADOS`, `QT_CONEXOES`, `QT_ATIVIDADES`,
  `QT_APPS`, `QT_DISPOSITIVOS`, `QT_PERFIL`, `QT_EVENTOS`, `QT_LIGACOES`, `QT_NOTAS`,
  `QT_METADADOS_NUVEM` — `db/init/19_iped.sql`), status/fase/progresso/cancelamento no mesmo
  padrão de `INQ_TB02_EXTRACOES`, `ID_EXTRACAO` (ponte).
- Colunas da v2 em `ARQ_TB01_ARQUIVOS` (migração `db/migracoes/0012_iped_dominios.sql`):
  `DT_CRIACAO`/`DT_ACESSO`/`TX_LINHA_TEMPO` (o par `timeStamp`×`timeEvent`),
  `CO_TRACK_ID`/`NU_ID_ORIGEM`/`NU_ID_PAI`/`CO_EVIDENCIA` (identificadores estáveis do item e
  de qual fonte de dados ele veio), `VL_NUDEZ`/`TX_CLASSE_NUDEZ`/`QT_FACES`/`TX_ALERTA_HASH`
  (triagem) e `TX_MARCADORES`/`IN_SELECIONADO` (o que o perito marcou). Todas nullable:
  extração já importada continua válida, só sem os campos novos até ser reimportada.
- Apagar uma importação delega para `_apagar_dados_extracao` — a MESMA função que o UFDR usa —
  em vez de manter uma lista própria de tabelas. Enquanto a importação gravava em meia dúzia
  de tabelas a lista própria dava conta; agora que ela grava conversa, ligação, senha, rede,
  conta e financeiro, duas listas em paralelo garantiriam que uma ficasse para trás, e
  "apaguei a importação e as conversas continuaram lá" é o tipo de sobra que vira contaminação
  entre inquéritos. O sidecar de nuvem (`NUV_TB01_METADADOS_ARQUIVO`) era a última
  tabela que ainda saía por fora, num `DELETE` só do lado do IPED, e por isso apagar o
  **inquérito** inteiro falhava com `ForeignKeyViolation` quando o caso tinha metadado de nuvem —
  `apagar_inquerito` passa por `_apagar_dados_extracao`, não pelo caminho do card da nuvem.
  Hoje ele sai lá junto com o resto.
- Hash por item: `ARQ_TB01_ARQUIVOS` ganhou `CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256`
  (colunas genéricas, nullable — só a Nuvem IPED preenche hoje, mas UFDR/Takeout podem vir a
  preencher no futuro). Não existe um hash único "do arquivo inteiro" como no `.ufdr` (a
  evidência é uma pasta com milhares de itens, não um arquivo só) — mas o IPED já calcula
  hash de cada item durante o processamento, o que dá uma cadeia de custódia até mais
  granular.
- O que a Nuvem IPED aproveita do processamento do IPED, em `ARQ_TB01_ARQUIVOS` (migração
  `db/migracoes/0011_iped_enriquecimento.sql`): `TX_OCR`/`DT_OCR` e `TX_TRANSCRICAO` (dos SQLite
  em `<caso>/text/`), `TX_IDENTIFICADORES` (JSONB, índice GIN `jsonb_path_ops`),
  `IN_EXTENSAO_DIVERGENTE` (`extMismatch` — extensão que não bate com o conteúdo, sinal clássico
  de arquivo renomeado para se esconder) e `IN_RECUPERADO` (`carved` — recuperado do não alocado,
  diferente de `CD_STATUS_ARQUIVO = 'Deleted'`, em que o sistema de arquivos ainda tinha a
  entrada). Todas nullable: extração já importada continua válida, só sem os campos novos até ser
  reimportada.
- `NUV_TB01_METADADOS_ARQUIVO` (`db/init/29_nuvem.sql`): o sidecar de nuvem, uma linha por
  `<nome>-info.json`/`<foto>.jpg.json` — ver "O sidecar" acima. A ligação com
  `ARQ_TB01_ARQUIVOS` é por `(ID_EXTRACAO, LOWER(NO_ARQUIVO))` e não por chave estrangeira,
  porque os arquivos entram por `COPY` em lote e o `COPY` não devolve `ID_ARQUIVO`; o índice
  `IX203_ARQ_TB01_EXTRACAO_NOME_LOWER` é o outro lado dessa junção.
- `EML_TB01_EMAILS` ganhou `NU_THREAD` (o `X-GM-THRID` do Gmail, que reconstrói a conversa) e
  `NU_MENSAGEM` (o `Message-ID` do RFC 5322). Só o IPED preenche; o UFDR não expõe nenhum dos
  dois.
- Tabelas unificadas com o UFDR (ligadas pelo mesmo `ID_EXTRACAO` ponte):
  `ARQ_TB01_ARQUIVOS`, `EML_TB01_EMAILS`, `CTT_TB01_CONTATOS`/`CTT_TB02_CONTATO_ENTRADAS`, e —
  pelo roteamento por produto do Google — `NAV_TB01_PAGINAS_VISITADAS`, `LOC_TB01_LOCALIZACOES`,
  `NOT_TB01_NOTAS`, `LIG_TB01_LIGACOES`, `APP_TB01_APLICATIVOS`.
- Tabelas genéricas de conta na nuvem (`db/init/29_nuvem.sql`, consultadas por
  `funcoes/contas_nuvem_utils.py`): `ATV_TB01_ATIVIDADES`, `ACS_TB01_ACESSOS`,
  `ACS_TB02_AGREGADOS`, `ACS_TB03_CONEXOES`, `DVC_TB01_DISPOSITIVOS`, `PRF_TB01_PERFIL`,
  `EVT_TB01_EVENTOS`. Não são específicas do Google: qualquer nuvem (Microsoft/Apple) que
  grave nelas com outro `NO_FONTE` reaproveita as mesmas telas.

## Configuração

- Nenhuma variável de ambiente específica: usa a mesma `PASTA_INVESTIGACAO` do UFDR/Takeout.
- `default-jre-headless` precisa estar instalado na imagem da API (`api/Dockerfile`) — a rota
  `GET /api/investigacao/iped-disponivel` diz ao front se o Java está disponível.
- O dumper compilado (`IpedDumper.class`) vai junto na imagem (`COPY tools ./tools`), em
  `/app/tools/iped_dumper` — ver [extracao_java_iped.md](extracao_java_iped.md) para como
  recompilar.

## Referências

- `api/funcoes/iped_utils.py` — `listar_casos_iped_disponiveis`, `_pasta_modulo_iped`,
  `iniciar_importacao_iped`/`_processar_importacao_iped` (importação em background),
  `_valor`/`_valores`/`_juntar` (a única porta de entrada dos campos do dumper, ver "Campos
  multivalorados"), `_DetectorConta`/`detectar_conta_iped` (identificação da conta),
  `_renomear_pasta_para_conta`, `versao_iped`,
  `_item_para_arquivo`/`_email_de_item`/`_contatos_de_item` (roteamento por categoria),
  `identificadores_do_item` e os `_norm_*` (validação dos `Regex:*` do IPED),
  `_caminho_interno`/`_blob_do_item`/`_linhas_de_produto`/`_rotear_produto_google`
  (roteamento por produto do Google), `apagar_iped`/`solicitar_cancelamento_iped`,
  `_identificar_pessoas_do_caso` (dispara a identificação de pessoas da mesa ao concluir/
  cancelar a importação).
- `api/funcoes/iped_campos.py` — a única porta de entrada para ler um item: `valor`/`valores`/
  `juntar`/`valor_variantes` (campo multivalorado e sufixo de tipo), `datas_do_item`/
  `linha_do_tempo`, `identificadores_do_item` (varredura genérica dos `Regex:*`), `qrcodes`,
  `coordenada`, `metadados_de_midia`, `LinhaParcial`.
- `api/funcoes/iped_dominios.py` — `classificar` (categoria/MIME → domínio), `Roteador`
  (consome os itens de domínio e grava), `_BufferMensagens` (o SQLite temporário das
  mensagens) e um `*_de_item` por domínio.
- `api/funcoes/iped_marcadores.py` — `ler`: marcadores do perito via subprocesso Java,
  best-effort.
- `api/funcoes/iped_texto.py` — `TextosDoCaso`: OCR e transcrição prontos, lidos de
  `<caso>/text/ocr-results.db` e `<caso>/text/transcriptions.db`.
- `api/funcoes/nuvem_metadados.py` — `alvo_do_sidecar`, `parse_drive_info`,
  `parse_photos_sidecar`, `parse_sidecar`, `gravar_metadado`.
- `api/funcoes/correlacao_nuvem_utils.py` — a tela que consome o sidecar e o cruzamento com o
  aparelho; ver [correlacao_nuvem.md](correlacao_nuvem.md).
- `api/funcoes/takeout_parsers.py` — os parsers de conteúdo dos produtos do Google e o mapa
  `_ROTEIRO`/`destino()`. Vieram inteiros de `funcoes/google_takeout_utils.py`, removido junto
  com a extração "Nuvem Google (ZIPs)"; a única adaptação foi trocar o par (ZipFile, caminho)
  por bytes nos parsers de CSV, porque aqui o conteúdo vem do storage-N.db do caso.
- `api/funcoes/contas_nuvem_utils.py` — camada de consulta das tabelas genéricas de conta na
  nuvem (listagens, facetas, resumos) e as `_gravar_*` usadas pelo roteamento.
- `api/funcoes/iped_cache.py` — `ler_blob`, `ler_thumb` (miniatura pronta do IPED),
  `fechar_conexoes`.
- `api/tools/teste_iped_gravacao.py` — exercita o caminho de gravação contra o banco real com
  itens de verdade lidos do índice, **sem importar o caso** (nada é renomeado nem movido) e
  apagando a extração de teste no fim. É o caminho mais curto para validar uma mudança nesta
  extração sem rebuild, e o primeiro passo dele (`_regressao_ufdr`) grava uma tupla no formato
  exato do UFDR — a rede que impede uma coluna nova no `COPY` de quebrar a importação de
  celular, que é a que roda em produção.
- `api/funcoes/investigacao_utils.py` — `_resolver_arquivo_iped`; `_drenar_arquivos` (colunas
  de hash); `_gravar_email`/`_gravar_contato_agenda`/`_gravar_pagina_visitada`/
  `_gravar_localizacao`/`_gravar_nota`/`_gravar_ligacao`/`_gravar_aplicativo` (do UFDR).
- `api/tools/iped_dumper/IpedDumper.java` — ver [extracao_java_iped.md](extracao_java_iped.md).
- `api/rotas/investigacao.py` — `IpedIn`/`IpedDetectarContaIn`, rotas `iped-disponivel`/
  `iped-disponiveis`/`iped-detectar-conta`/`extrair-iped`/`iped/{id}/*`, e as rotas genéricas
  de conta na nuvem (`atividades`, `acessos`, `acessos-agregados`, `conexoes`,
  `dispositivos-conta`, `perfil-conta`, `agenda`).
- `db/init/19_iped.sql` — `IPD_TB01_EXTRACOES` e os contadores por produto;
  `db/init/20_arquivos.sql` — colunas de hash, OCR/transcrição, identificadores e flags em
  `ARQ_TB01_ARQUIVOS`; `db/init/29_nuvem.sql` — tabelas genéricas de conta na nuvem e
  `NUV_TB01_METADADOS_ARQUIVO`; `db/init/18_credenciais_financeiro.sql` — `NU_THREAD`/
  `NU_MENSAGEM` em `EML_TB01_EMAILS`; `db/migracoes/0011_iped_enriquecimento.sql` — os deltas
  correspondentes para banco que já existe.
- `web/investigacao.html` (aba "Nuvem IPED (pasta do caso)"), `web/js/investigacao.js` —
  `renderIpedBloco`, `cancelarIped`/`apagarIped`, `carregarIpedDisponiveis`.
- Telas alimentadas por esta extração: `arquivos.html`, `emails.html`, `contatos.html`,
  `navegacao.html`, `localizacoes.html`, `notas.html`, `ligacoes.html`, `aplicativos.html`,
  `atividades.html`, `acessos.html`, `acessos_agregados.html`, `conexoes.html`,
  `dispositivos_conta.html`, `perfil_conta.html`, `agenda.html`.
- Documentos relacionados: [extração_ufdr.md](extração_ufdr.md) (mesma extração-ponte, mesmas
  tabelas de arquivos/e-mails/contatos), [hash_arquivos.md](hash_arquivos.md)
  (cadeia de custódia — aqui aplicada por item, não por arquivo único),
  [investigacao_celular.md](investigacao_celular.md) (as telas de dados de conta — e o
  **WhatsApp parcial**, a mídia do WhatsApp do Drive/iCloud reconstruída como conversa, em
  `funcoes/whatsapp_parcial_utils.py` + `funcoes/iped_wa_drive.py`),
  [mesa_inquerito.md](mesa_inquerito.md) (a identificação de pessoas que esta extração
  dispara ao final).
- Tela de disparo desta importação: [importacao.md](importacao.md) (aba correspondente de `web/importacao.html`).

## Rodada 3 — correções validadas contra `laudo_bahia` e `nuvem_op_clean` (2026-09)

Auditoria contra dois casos reais novos (um laudo de celular — iPhone via UFDR, 1,7 milhão de
itens — e uma conta Google via Takeout, 3.256 itens), rodando o dumper e comparando o que o
índice tem com o que a importação esperava. Cada achado veio com a medição e a correção:

- **Mojibake no corpo de e-mail.** 152 de 1.342 e-mails do caso de nuvem tinham o `Message-Body`
  do índice corrompido ("OlÃ¡" em vez de "Olá") — o IPED decodificou o quoted-printable do mbox
  com charset errado, enquanto o `.eml` no storage decodifica certo com a biblioteca de e-mail
  do Python. `_email_de_item` agora lê o blob quando o corpo falta OU tem mojibake
  (`_parece_mojibake`), e só paga a leitura quando precisa (`Message:AttachmentCount == "0"`
  dispensa o blob só para descobrir anexos).
- **Marcadores do perito em BitmapBookmarks.** O `bookmarks.iped` do IPED 4.2.2 contém um
  `BitmapBookmarks`, e o cast de `Bookmarks.load()` estourava `ClassCastException` — os 2.558
  itens marcados do laudo eram descartados em silêncio. `IpedBookmarks.java` agora lê via
  `Util.readObject` + interface `IBookmarks`, com os métodos instáveis por reflexão, e
  `iped_marcadores.ler` tenta os backups `bkp/bookmarks.N.bkp.iped` quando o principal falha.
- **Anexo UFDR pendurado na mensagem.** O item `application/x-ufed-attachment` é filho da
  MENSAGEM (não do chat); tratado como mensagem, os 6.103 anexos do laudo viravam "mensagens"
  vazias em conversas sintéticas. Agora são incorporados como anexos da mensagem pai (com a
  legenda `ufed:Title`) em `_gravar_um`.
- **Mídia de conversa abre.** O anexo da mensagem ganha o ponteiro `IPED::...` do item de mídia
  do caso (por sha-256, a chave do `linkedItems`), e as rotas de anexo (`/anexos/{id}/imagem|audio|
  arquivo`) + `_classificar_anexos` aprenderam a resolver o ponteiro — áudio/vídeo/foto do
  WhatsApp tocam direto do storage, em streaming para binário grande.
- **SSID em `ufed:SSId`.** O UfedXmlReader grava com o "d" minúsculo no fim — 703 de 703 redes
  do laudo só tinham essa grafia, e a tela de Wi-Fi mostrava "WirelessNetwork_<uuid>".
- **Nome de app em `ufed:Identifier`.** Os 11.832 AppsUsageLog não têm `ufed:Name`; o fallback
  para o `name` do item enchia a tela de UUIDs. O pacote virou o nome.
- **Domínios UFDR restantes roteados:** SearchedItem (`ufed:Value` → BSC_TB01_BUSCAS),
  SocialMediaActivity (→ RDS_TB01), DeviceConnectivity (→ CNX_TB01), Cookie (→ COO_TB01),
  Voicemail (→ VMS_TB01), Recording (→ GRV_TB01), "Financial Account" singular (→ BNC_TB01) —
  todos pelas mesmas tabelas/telas do UFDR (`social_buscas.html`, conexões, navegação, agenda).
  Device Events seguem ignorados por decisão de escopo (mesma do UFDR) e não viram mais arquivo;
  localização decodificada sem coordenada nem endereço também sai de Arquivos.
- **Contato "Unknown".** O número do contato não decodificado está embutido no `name`
  (`Contact-Unknown-[…]`) e era descartado inteiro — agora vira entrada de telefone.
- **Exibição.** O card do IPED ganhou botões para Senhas/Autofill (nova tela
  `credenciais.html`, domínio "senhas" no componente `ufdr_dominio.js`), "Nuvem × aparelho"
  (sidecars) e "Marcações do perito" (filtro `?marcados=true` na tela de arquivos, com badge por
  item); o card mostra badge de "Caso ausente" quando a pasta sai de `data/`; cancelado exibe os
  dados parciais; o Orbit parou de desenhar a extração-ponte como segundo celular, ganhou os
  mesmos pills de domínio, cancelar/compartilhar e os rótulos de fase (`gravando_conversas`,
  `copiando`, `cancelando`); `correlacao_nuvem` aceita `?extracao=`; a timeline unificada ganhou a
  categoria `arquivo_iped` (eventos de `TX_LINHA_TEMPO` de arquivos de conteúdo, com índice
  parcial IX262); a miniatura de vídeo do IPED (`view/H0/H1/HASH.jpg`) aparece na galeria; o
  download/conteúdo de IPED é servido em streaming.
- **Contadores e organização.** `qt_extracoes` não conta mais a ponte do IPED como aparelho
  (caso IPED só entra quando é celular de fato); caso de celular é movido para `CELULAR/` e não
  `NUVEM/`.
- **Testes.** `api/tools/teste_iped_correcoes.py` exercita tudo acima contra o banco real
  (sem importar caso nenhum); `teste_iped_gravacao.py` continua como regressão.

