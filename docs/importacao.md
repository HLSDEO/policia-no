# Nova importação — a porta de entrada de todo material

## Visão geral

**Nova importação** (`web/importacao.html`) é a tela por onde **todo** material entra no sistema:
o `.ufdr` do Cellebrite, a pasta-caso do IPED, a pasta do RIF, o R304 do SIMBA e o documento avulso
do inquérito. Cada uma dessas cinco importações tem a sua própria
documentação — o que esta tela faz é reuni-las num formulário só, sob duas decisões que valem para
todas:

1. **A delegacia é escolhida uma vez, na topbar**, e vale para a tela inteira: é ela que define de
   qual subpasta de `data/` os seletores listam o que ainda não foi importado (ver
   [permissoes_delegacia.md](permissoes_delegacia.md)).
2. **O número do inquérito é um campo só, fora das abas de fonte.** Ele vale para as cinco fontes
   — trocar de aba não faz redigitar o número, que é o erro que o desenho anterior
   (um formulário completo por fonte) convidava.

A tela **não mostra andamento**. Toda importação roda em segundo plano e é acompanhada na página
de **Investigação** (`web/investigacao.html`) — por isso cada formulário, ao disparar, revela um
botão "Ver andamento" em vez de tentar exibir progresso aqui.

> Esta tela é item de primeiro nível do menu (`NAV_ITEMS` em `web/js/utils/mapa_telas.js`). O
> **mesmo formulário** existe como modal na lista de inquéritos (`web/inqueritos.html`, botão
> "+ Importar", JS `web/js/inqueritos_importar.js`), aí com o número do inquérito
> já preenchido a partir do card de onde se clicou.

## Fluxo de uso

1. **Escolher a delegacia** no seletor da topbar. Ele é preenchido por `GET /api/delegacias` e
   inicia no valor guardado em `localStorage["analyser-delegacia"]` ou, na primeira visita, na
   delegacia do próprio usuário (`GET /api/auth/eu`). Trocar a delegacia **recarrega as cinco
   listas** de uma vez (ver "Carregamento das listas" abaixo).
2. **Escolher o tipo de importação** — o seletor "Importar como", antes do número, com as opções
    **Inquérito** e **Caso de inteligência**. O seletor só aparece para os perfis de **inteligência**
    — chefia e agente (`pode_caso_inteligencia`); os demais perfis importam sempre como inquérito.
   No modo caso, o campo de número some e o inquérito nasce com a flag `IN_CASO_INTELIGENCIA` e
   número gerado `CASO-AAAA.NNNN` (ver [documentos_diversos.md](documentos_diversos.md)).

3. **Digitar o número do ePol** (obrigatório no modo inquérito, vale para as cinco fontes) e
   escolher a **fonte**:

   | Aba de fonte | Entrada | Rota de disparo | Documento |
   |---|---|---|---|
   | Indexação Cellebrite (UFDR) | um `.ufdr` de `data/` (ou enviado pelo navegador — sem limite de tamanho) | `POST /api/investigacao/extrair` | [extração_ufdr.md](extração_ufdr.md) |
   | RIF - COAF (CSV) | pasta com `RIF` no nome (3 CSVs + PDF, envio pelo navegador ou `data/`) | `POST /api/investigacao/extrair-rif` | [rif.md](rif.md) |
   | Bancário SIMBA R304 (CSV) | arquivo `*R304*.csv` (envio pelo navegador ou `data/`) | `POST /api/investigacao/extrair-simba` | [simba.md](simba.md) |
   | Nuvem IPED (pasta do caso) | pasta-caso do IPED | `POST /api/investigacao/extrair-iped` | [extracao_iped.md](extracao_iped.md) |
    | Arquivo genérico | PDF, CSV, XLSX, DOCX, TXT ou HTML (vários de uma vez, síncrono) e ZIP (vai para a fila `import_zip` — o POST devolve `id_job`, o progresso vem por polling) | `POST /api/investigacao/extrair-documento` | [documentos_diversos.md](documentos_diversos.md), [irpf.md](irpf.md), [ipei.md](ipei.md), [telefonia.md](telefonia.md) |

4. **Importar.** O botão desabilita, a linha de status mostra o resultado e aparece o link **Ver
   andamento**, que leva a `investigacao.html`.
5. Terminada a importação, o arquivo **some do seletor**: ele foi movido para
   `data/<DELEGACIA>/<INQUÉRITO>/<CATEGORIA>/` — ver
   [organizacao_arquivos.md](organizacao_arquivos.md). Os seletores listam só a raiz da pasta da
   delegacia, ou seja, o que ainda **não** foi importado.

### Copiar de outra pasta de rede

O botão **"Copiar de outra pasta de rede"**, na topbar ao lado do seletor de delegacia, cobre o
caso em que o material está num compartilhamento Windows (`\\SERVIDOR\pasta`) de outra
máquina/unidade: abre um modal onde se informa o caminho e, se a pasta pedir, usuário/senha
(funciona com conta `DOMINIO\usuário`, `usuário@dominio` ou pasta aberta sem login). O modal
lista o conteúdo para marcar um ou mais arquivos/pastas, e o **servidor** copia para
`data/<DELEGACIA>/` em segundo plano, com barra de progresso — pode fechar o modal que a cópia
continua, com aviso ao concluir. O campo **Domínio** vem preenchido com `pf.gov.br`: se o
servidor no caminho for um nome curto (`ses0006`), o modal completa para o FQDN
(`ses0006.pf.gov.br`, que é o que o container resolve) e mostra o caminho final no campo;
apague o domínio para usar o caminho exatamente como digitado (IP ou FQDN já completo nunca
são alterados). Arquivo marcado cai **solto na raiz** de `data/<DELEGACIA>/` (é só a raiz que
os seletores listam); pasta marcada (RIF, IPED) vira **uma subpasta da raiz**, com a estrutura
interna preservada.

O progresso mora em `JOB_TB01_JOBS` (`tp_job='copia_rede'`, sem inquérito — escopo
`"<delegacia>:<token>"`), não na memória do processo: fechar a página, trocar de tela ou
reiniciar a API não perde o acompanhamento. O botão **"Cópias"** da topbar mostra um badge
com quantas estão rodando e abre o painel com cada cópia (origem → destino, barra por item,
bytes, início, falhas) e botão de cancelar; ao concluir, a página avisa por toast e recarrega
as seis listas, e o arquivo aparece no seletor da sua fonte. É o terceiro caminho de entrada,
ao lado de copiar à mão e do envio pelo navegador (limitado a 50 MB para o documento avulso e
o R304 do SIMBA; **sem limite** para o `.ufdr` — ver "Particularidades de cada aba" abaixo).
Rotas em `api/rotas/rede.py` (`POST .../listar`, `POST .../copiar`,
`GET .../copiar` [minhas cópias], `GET .../copiar/{id}`, `POST .../cancelar`); lógica em
`api/funcoes/rede_smb.py` (dependência `smbprotocol`, rebuild da API).
A senha da rede nunca é salva — só trafega no corpo do POST e vive na memória durante a
operação. Usuário comum só copia para a própria delegacia (`delegacia_permitida`, como nas
listagens) e só vê as próprias cópias (admin vê todas em Administração → Jobs);
colisão de nome ganha sufixo `(2)`, como no upload.

### Particularidades de cada aba

- **UFDR** — além do arquivo, escolhe-se a **qualidade da transcrição de áudio**
  (`nenhuma` / `base` / `small` / `large-v3`, padrão "Média") e o checkbox **"Transcrever também o
  áudio dos vídeos"**. Ver [transcricao_audio.md](transcricao_audio.md). A aba também tem **envio
  pelo navegador**: a caixa "Enviar do seu computador" (arrastar ou clicar) manda o `.ufdr` por
  `POST /api/investigacao/ufdr-upload` para `data/<DELEGACIA>/` e já o deixa **escolhido** na
   lista abaixo. É uma das duas exceções de tamanho entre os uploads: **sem limite** (um `.ufdr` passa de
   dezenas de GB — o teto é o `location` próprio do nginx, não `UPLOAD_DOCUMENTO_MB`), gravação em
  blocos no servidor (`.parcial` + rename, upload interrompido não deixa arquivo truncado no
  seletor) e **progresso nas duas fases**: o envio dos bytes (barra do navegador) e a gravação no
  servidor (`GET /api/investigacao/ufdr-upload/progresso`, que mede o `.parcial` em disco — num
  `.ufdr` de dezenas de GB ela demora o mesmo tanto e acontece **depois** de o navegador terminar
  de mandar; sem esse segundo indicador a tela parecia travada em "Enviando X de X…"). Não tem
  retomada: fechar a página cancela o envio,
  e o texto de ajuda recomenda "Copiar de outra pasta de rede" para arquivos muito grandes. O
  upload não importa nada: a importação continua sendo o botão Importar da própria aba.
- **Nuvem IPED** — ao escolher a pasta, a tela chama
  `POST /api/investigacao/iped-detectar-conta` e mostra o campo **"Conta identificada (confirme ou
  edite)"** já preenchido com o e-mail que a importação usaria para **renomear a pasta** ao final.
  É pré-visualização: o investigador confirma, corrige ou apaga (em branco = não renomear), em vez
  de descobrir o nome errado só depois de processar o caso inteiro.
- **Arquivo genérico** — é uma das três abas com **envio pelo navegador** (as outras são o SIMBA
  e o RIF, abaixo): a caixa "Enviar do seu
   computador" (arrastar ou clicar) manda os arquivos por `POST /api/documentos/upload` para
   `data/<DELEGACIA>/` e já os deixa **escolhidos** na lista abaixo. Pasta arrastada até a caixa
   também vale: `web/js/utils/pasta_arraste.js` (`window.PastaArraste`) desce a pasta pelo
   `webkitGetAsEntry()` do drop e achatou os arquivos de dentro (inclusive de subpastas) na fila
   de envio. Quem não tem acesso ao disco do
     servidor deixa de depender de alguém copiar por ele. Limite de **50 MB por arquivo por padrão,
     ajustável em Administração → Desempenho** (`UPLOAD_DOCUMENTO_MB`, em
     `api/funcoes/documentos_utils.py`; o `client_max_body_size` do nginx é 520 MB — ver
     [infraestrutura.md](infraestrutura.md)) — **exceto o `.zip`, sem limite como o `.ufdr`**:
      o front manda o ZIP por `POST /api/documentos/upload-zip` (teto no `location` próprio do
      nginx, gravação/expansão em blocos) e o servidor o expande ali na hora, guardando só os
       documentos de dentro (PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG, PNG). Um `.zip` que já
       está em `data/` (copiado à mão, via rede ou expandido do upload) **não importa na
       requisição**:
       o botão Importar o manda para a **fila `import_zip`** (o POST devolve `id_job` na hora e
       a tela acompanha por polling em `GET /api/investigacao/jobs/{id_job}` — ver
       [documentos_diversos.md](documentos_diversos.md)). O upload **não importa nada**: ele
   só grava na pasta de entrega, e a importação continua sendo o botão Importar da própria aba —
   separar os dois é o que permite mandar cinco arquivos e importar três. Num envio múltiplo, o que
   falhou (tipo não suportado, vazio, grande demais) volta em `falhas` e o que deu certo em
   `arquivos`: um PDF recusado no meio da leva não derruba os outros quatro. A leitura do corpo é
   feita em blocos por `api/funcoes/upload_utils.py`, que corta no limite em vez de deixar o
   processo alocar os 52 MB que o nginx aceita antes de conferir o tamanho.
   O envio roda em **pool de 3 requisições simultâneas com barra de bytes agregada** (os bytes em
   voo saem do `upload.onprogress` de cada XHR) — lote grande para de parecer travado.
   **JPG/PNG são aceitos como prova sem texto**: a imagem entra no inquérito com `TX_CONTEUDO`
   vazio por design (o visualizador mostra a aba "Original", e a análise de IA/entidades pula
   documento sem texto — OCR é a tela de Arquivos, não o importador).
   Duas verdades o upload diz antes de gravar: **extensão que mente morre na porta** — PDF/DOCX/
   XLSX/ZIP/JPG/PNG são conferidos pelos magic bytes (`_ASSINATURAS` em
   `api/funcoes/documentos_utils.py`) e a divergência volta em `falhas` nomeada, em vez de virar
   "erro na extração" dias depois — e **nome em uso nunca é sobrescrito**: quem chega depois ganha
   sufixo `(2)` e a resposta marca `renomeado: true`, que a tela mostra em âmbar ("já existia um
   com este nome"), porque descobrir o renomeio sozinho é azar.
   Documento **pesado** (acima de `DOC_IMPORTE_SINCRONO_MB`, padrão 25 MB — 0 desliga) também não
   importa na requisição: o `POST /api/investigacao/extrair-documento` devolve `job_queued` com um
   job **`import_documento`** (mesma fila e mesmo polling do ZIP), e o worker faz o que a rota
   faria — extrair, anexar e reconhecer IPL/IRPF/IPEI/telefonia — sem relógio contra o proxy.
   O que o upload largou em `data/` e **ninguém importou** não apodrece lá: o agendador roda a
   higiene (`api/funcoes/higiene_data.py`) que move soltos velhos (`UPLOAD_ORFAO_DIAS`, padrão 7),
   de extensão de upload e sem registro, para `data/_lixeira/uploads/` — nunca apaga em seco.
  A aba **reconhece o formato** do que foi escolhido e decide o tratamento: o PDF de um inquérito
  do ePol vira bloco de IPL, a declaração de IRPF vira bloco de IRPF, e o PDF de uma quebra de
  sigilo fiscal vira bloco de IPEI — este último **junto com a planilha de anexo que veio com
  ele**, num card só, achada pelo número da IPEI no nome dos dois arquivos e em qualquer ordem de
  importação (ver [ipei.md](ipei.md)).
  Os arquivos de **operadora de telefonia** (o `.txt` de cadastro de linhas, a planilha do ofício
  com o CDR/ERBs/números e o PDF dela) são o caso extremo desse agrupamento: eles chegam em
  quantidade aberta — o período costuma vir quebrado em várias levas, sobretudo as ERBs — e **todos**
  caem num bloco só **por operadora**, sem depender de nome de arquivo nem de ordem de importação.
  Reimportar o mesmo arquivo, ou importar a planilha e o PDF do mesmo ofício, não duplica linha
  nenhuma (ver [telefonia.md](telefonia.md)). O que não é reconhecido segue como documento de texto
   para a IA, como antes.
- **Bancário SIMBA R304** — também tem **envio pelo navegador**: a caixa "Enviar do seu
   computador" (arrastar ou clicar) manda o CSV por `POST /api/investigacao/simba-upload` para
   `data/<DELEGACIA>/` e já o deixa **escolhido** na lista abaixo (mesmo contrato de resposta do
   upload de documento genérico: `arquivos`/`falhas`). Só `.csv` passa; o servidor **confere o
   cabeçalho do R304 na hora** (um CSV que não é o relatório certo nem chega a ficar em `data/` —
   ver `salvar_simba_enviado` em [simba.md](simba.md)) e **prefixa "R304_"** quando o nome não o
    tem, porque o seletor do SIMBA só lista `*R304*.csv`. Vale o mesmo limite de upload
   (`UPLOAD_DOCUMENTO_MB`). O upload não importa nada: a importação continua sendo o botão
   Importar da própria aba (`POST /api/investigacao/extrair-simba`).
- **RIF - COAF** — a terceira aba com **envio pelo navegador**, mas de **pasta inteira** (o RIF
   é uma pasta, não um arquivo): o botão "Enviar do seu computador" escolhe a pasta no diálogo do
   navegador (input `webkitdirectory`) ou aceita **arrastar a pasta inteira** — no arraste, o
   `dataTransfer.files` não traz o conteúdo da pasta, então `web/js/utils/pasta_arraste.js`
   (`window.PastaArraste`) a desce via `webkitGetAsEntry()` e cada arquivo sobe com o caminho
   relativo (`webkitRelativePath` no clique, `__caminho` no arraste) por
   `POST /api/investigacao/rif-upload` — uma
   requisição por pasta — e o servidor recria a pasta em `data/<DELEGACIA>/` (`salvar_rif_enviado`
   em [rif.md](rif.md)): **só CSV e PDF** entram (os demais voltam em `falhas`), o nome ganha
   prefixo "RIF_" quando falta "RIF" (a listagem e a importação só reconhecem pastas com "RIF"),
   colisão ganha sufixo `(2)`, vale o mesmo limite de upload (`UPLOAD_DOCUMENTO_MB`), e qualquer
   erro de gravação remove a pasta inteira — nada de RIF pela metade. Ao concluir, a lista
   recarrega com a pasta já **escolhida**; a importação em si continua sendo o botão Importar da
   própria aba (`POST /api/investigacao/extrair-rif`), que usa só os `*_Comunicacoes`,
   `*_Envolvidos`, `*_Ocorrencias` e o 1º PDF — arquivos extras da pasta (um 4º CSV, documentos
   de trabalho) seguem junto mas não são lidos.

## Como funciona

Tudo em `web/js/importacao.js` (+ `web/importacao.html`; o estilo vem de `css/style.css`,
`css/entidades.css` e do tema do `tom-select`, sem CSS próprio da tela). Não há módulo
próprio em `api/funcoes/` nem rota exclusiva desta tela: ela é uma **casca** sobre as rotas que
cada feature já expõe.

### As abas

Um nível de aba, com `role="tablist"` / `role="tab"` / `role="tabpanel"`,
`aria-selected` e navegação por seta, `Home` e `End`:

- **fonte** (`ativarAbaFonte`) — as cinco fontes, lembradas em
  `localStorage["analyser-fonte-extracao"]`.

### O seletor de tipo

O seletor "Importar como" (`#imp-tipo-select`) só é revelado quando `GET /api/auth/eu`
   devolve `pode_caso_inteligencia` (perfis de chefia de inteligência e de inteligência); o backend recusa o POST
com 403 para os demais. No modo caso, o campo de número é escondido, a validação
(`validarNumeroInquerito`) é pulada e cada disparo leva `caso_inteligencia: true` junto do
número vazio — o backend gera o número e marca a flag (ver
[documentos_diversos.md](documentos_diversos.md)).

Lembrar a fonte importa porque a maioria dos analistas importa sempre o mesmo tipo de material; a
tela abre onde a pessoa parou.

`Enter` no campo do número do inquérito dispara **o formulário da aba ativa** — não há um submit
global, porque cada fonte tem validação e payload próprios.

### Carregamento das listas

Os cinco seletores são preenchidos por rotas diferentes, todas passando a delegacia escolhida:

| Seletor | Rota |
|---|---|
| `.ufdr` | `GET /api/investigacao/arquivos?delegacia=` |
| Documentos do inquérito | `GET /api/documentos/arquivos?delegacia=` |
| Pasta do RIF | `GET /api/investigacao/rifs-disponiveis?delegacia=` |
| Arquivo do SIMBA | `GET /api/investigacao/simba-disponiveis?delegacia=` |
| Pasta do IPED | `GET /api/investigacao/iped-disponivel?delegacia=` |

Trocar a delegacia dispararia cinco requisições em sequência a cada clique. `recarregarListas()`
resolve isso: **um `$.when` sobre as cinco**, com *debounce* de 150 ms (`agendarRecarregarListas`) e
uma trava `carregandoListas` que ignora um segundo disparo enquanto o primeiro não terminou.
Enquanto carrega, os selects ficam desabilitados (os de multisseleção via `TomSelect.disable()`) —
em vez de mostrar uma lista da delegacia antiga que ainda parece clicável.

Todas essas rotas passam por `delegacia_permitida`: pedir a pasta de outra unidade é `403`, não uma
lista vazia (ver [permissoes_delegacia.md](permissoes_delegacia.md)).

### Validação e feedback

- `validarNumeroInquerito` é comum às cinco fontes: número em branco marca o campo com
  `aria-invalid`, escreve o erro na linha de status da aba e dispara um `Toast.erro`.
- `revelarLinkVerAndamento` mostra o "Ver andamento" da aba que disparou e emite o toast de
  sucesso; `esconderLinksAndamento` limpa os das outras, para não sobrar o link de uma importação
  anterior.
- Toda rota devolve o erro em `{detail}`, que a tela mostra na linha de status da própria aba — o
  padrão descrito em [interface.md](interface.md).

### O mesmo formulário como modal

`web/js/inqueritos_importar.js` monta o **modal "+ Importar"** de `web/inqueritos.html` com as
mesmas abas, os mesmos seletores e as mesmas rotas — a diferença é que o número do inquérito
vem do card de onde se clicou, já preenchido (e, quando o card é um caso de inteligência, o
modal envia `caso_inteligencia: true` junto). É código separado, e não o mesmo
arquivo reaproveitado, porque os ids do DOM são outros (prefixo `modal-`); a duplicação é conhecida
e vale para quem for mexer nas duas telas.

## Modelo de dados

Nenhuma tabela própria. Cada disparo grava na tabela da sua feature — `INQ_TB02_EXTRACOES`,
`RIF_TB01_RIFS`, `SMB_TB01_EXTRATOS`, `IPD_TB01_EXTRACOES`, `INQ_TB04_DOCUMENTOS` —, e o inquérito é criado (ou reaproveitado) por `_obter_ou_criar_inquerito`, que
também concede acesso a quem importou (ver [permissoes_delegacia.md](permissoes_delegacia.md)).
No modo caso, a mesma função gera o número `CASO-AAAA.NNNN` e marca `IN_CASO_INTELIGENCIA`.

O que a tela guarda é só preferência de interface, em `localStorage`: `analyser-delegacia`
e `analyser-fonte-extracao`.

## Configuração

O limite de upload por arquivo (padrão 50 MB) é ajustável em Administração → Desempenho
(`UPLOAD_DOCUMENTO_MB` no `.env`, lido via `limite_upload_bytes()` em
`api/funcoes/documentos_utils.py`; vale na hora, sem reiniciar) e o front o consulta em
`GET /api/documentos/upload/limite`. O `client_max_body_size` de `nginx/default.conf` (520 MB)
fica acima do teto máximo da tela (500 MB) de propósito, para quem recusar ser a API. **O
upload de `.ufdr` e o de `.zip` furam esse teto**: têm `location` exato próprio no nginx
(`client_max_body_size 0`, `proxy_request_buffering off`, timeouts longos — ver
[infraestrutura.md](infraestrutura.md)) e não consultam `UPLOAD_DOCUMENTO_MB`.

Outros ajustes da aba genérica, todos no `.env` (relidos a cada uso):
`DOC_IMPORTE_SINCRONO_MB` (padrão 25) — acima disto o documento importa pela fila
`import_documento` em vez da requisição; `UPLOAD_ORFAO_DIAS` (padrão 7, 0 desliga) — idade em
que um upload solto e sem registro vira lixo para a `_lixeira`; `LIXEIRA_DIAS` (padrão 30) —
prazo de recuperação na `_lixeira` antes do expurgo definitivo (ver
[organizacao_arquivos.md](organizacao_arquivos.md) e `api/funcoes/higiene_data.py`).

## Referências

- Telas: `web/importacao.html` (JS `web/js/importacao.js`);
  `web/inqueritos.html` (modal "+ Importar", JS `web/js/inqueritos_importar.js`);
  `web/investigacao.html` (onde o andamento aparece).
- Rotas de disparo: `api/rotas/investigacao.py` (`extrair`, `ufdr-upload`, `extrair-rif`,
  `rif-upload`, `extrair-simba`, `simba-upload`, `extrair-iped`, `extrair-documento`,
  `iped-detectar-conta`) e `api/rotas/documentos.py` (`upload`, `upload-zip`).
- Cópia de rede: `api/rotas/rede.py` (`listar`, `copiar`, `copiar` [minhas cópias], `copiar/{id}`, `.../cancelar`) e
  `api/funcoes/rede_smb.py`.
- Rotas dos seletores: `GET /api/investigacao/arquivos`, `/rifs-disponiveis`,
  `/simba-disponiveis`, `/iped-disponivel`; `GET /api/documentos/arquivos`;
  `GET /api/delegacias`.
- Documentos relacionados: [extração_ufdr.md](extração_ufdr.md), [rif.md](rif.md),
  [simba.md](simba.md), [extracao_iped.md](extracao_iped.md),
  [documentos_diversos.md](documentos_diversos.md),
  [organizacao_arquivos.md](organizacao_arquivos.md) (para onde o arquivo vai depois),
  [permissoes_delegacia.md](permissoes_delegacia.md) (a subpasta da delegacia),
  [interface.md](interface.md) (abas, toasts e padrão de erro).
