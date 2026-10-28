# Dados extraídos do celular (telas de investigação)

Este documento cobre as telas que apresentam os dados extraídos do aparelho (UFDR/Cellebrite e
Nuvem IPED) — uma seção por domínio, no mesmo formato dos demais documentos (`docs/`): visão
geral, fluxo de uso, funcionamento e modelo de dados de cada um.

| Seção | Tela | Tabela principal |
|---|---|---|
| [Visualizador de conversas](#visualizador-de-conversas-whatsapp-sms-e-mensageiros-diversos) | `whatsapp.html` (+ `mensageiros.html`) | `WPP_TB0*`, `SMS_TB0*`, `MSG_TB0*` |
| [Agenda de contatos e associação a identidades](#agenda-de-contatos-e-associação-a-identidades) | `contatos.html` | `CTT_TB01/02` |
| [Ligações](#ligações) | `ligacoes.html` | `LIG_TB01` |
| [Localizações, geocodificação e grafo de deslocamento](#localizações-geocodificação-e-grafo-de-deslocamento) | `localizacoes.html` | `LOC_TB01`, `GEO_TB01`, `GEO_TB02..04` |
| [Linha do tempo unificada](#linha-do-tempo-unificada) | `timeline.html` | — (consulta os demais domínios) |
| [Notas de bloco de notas do aparelho](#notas-de-bloco-de-notas-do-aparelho) | `notas.html` | `NOT_TB01` |
| [Histórico de navegação](#histórico-de-navegação) | `navegacao.html` | `NAV_TB01` |
| [Aplicativos instalados](#aplicativos-instalados) | `aplicativos.html` | `APP_TB01` |
| [Notificações](#notificações) | `notificacoes.html` | `NTF_TB01` |
| [E-mails](#e-mails) | `emails.html` | `EML_TB01` |
| [Contas bancárias e carteiras de criptomoeda](#contas-bancárias-e-carteiras-de-criptomoeda) | `contas_financeiras.html` | `BNC_TB01` |
| [Redes Wi-Fi conhecidas](#redes-wi-fi-conhecidas) | `wifi.html` | `WIFI_TB01` |
| [Inventário do filesystem do aparelho](#inventário-do-filesystem-do-aparelho) | `arquivos.html` | `ARQ_TB01` |
| [Chaves PIX](#chaves-pix) | `chaves_pix.html` | `PIX_TB01` |
| [Dados de conta Google](#dados-de-conta-google-sem-equivalente-em-ufdr) | `atividades.html`, `acessos.html`, `acessos_agregados.html`, `conexoes.html`, `dispositivos_conta.html`, `perfil_conta.html`, `agenda.html` | `ATV/ACS/DVC/PRF/EVT_TB0*` |
| [WhatsApp parcial (nuvem)](#whatsapp-parcial-a-mídia-do-whatsapp-reconstruída-como-conversa) | `whatsapp_parcial.html` | `ARQ_TB01` (+ `CTT_TB01/02` para nomes) |

## Visualizador de conversas (WhatsApp, SMS e mensageiros diversos)

### Visão geral

O visualizador de conversas é a tela base para ler as mensagens extraídas de um inquérito. Ele
existe em duas páginas — `web/whatsapp.html` e `web/mensageiros.html` — que rodam o **mesmo**
`web/js/whatsapp.js` e diferem só na lista lateral: `whatsapp.html` tem as abas
Conversas/Grupos/Status, `mensageiros.html` não tem e tematiza a tela pela cor do app. As duas
servem três fontes, controladas pelo parâmetro `?fonte=`:

| fonte | Origem no UFDR | Tabelas |
|---|---|---|
| `wpp` | `Chat` com Source = "WhatsApp" | `WPP_TB01..05` |
| `sms` | `Chat` com Source = "Native Messages" | `SMS_TB01..05` |
| `msg` | demais Sources | `MSG_TB01..05`, separadas por `TP_APP` |

Qual das duas páginas abrir é decidido num lugar só, `MapaTelas.paginaConversa(fonte, app)`
(`web/js/utils/mapa_telas.js`), usado por toda tela que linka para uma conversa
(investigação, arquivos, chaves PIX, marcações, IA e timeline — esta remonta o link da conversa na
tela por essa regra; o `tx_link` do seu SQL ficou só como URL da navegação):

| fonte / app | Página | Por quê |
|---|---|---|
| `wpp` | `whatsapp.html` | tem grupo e Atualizações de Status |
| `msg` + `WhatsApp Business` | `whatsapp.html` | é WhatsApp: Source próprio no UFDR, mas com grupo e Status |
| `sms` | `mensageiros.html` | não tem grupo nem Status — as abas ficariam vazias |
| `msg` (demais apps) | `mensageiros.html` | tela tematizada pela cor do app |

O cartão do aparelho (`web/investigacao.html`) lista os mensageiros encontrados (Instagram,
Telegram, Facebook, Discord etc., um botão por app, com contagens), cada um abrindo o visualizador
com `?fonte=msg&app=<app>` na página que a regra acima indicar.

Além da leitura das mensagens, é daqui que se faz a **associação de contato → identidade** (pessoa
da base de referência e/ou município/UF) e que se acessam a foto de perfil do contato, os anexos
(imagem/áudio/vídeo/documento) e a transcrição dos áudios — tudo servido sob demanda do arquivo
`.ufdr` original, sem copiar binário para o banco.

### Fluxo de uso

1. Na tela de Investigação, no bloco da extração, abrir **WhatsApp**, **SMS** ou **Mensageiros**.
2. A lista de conversas mostra nome/grupo, participantes (todos menos o dono do aparelho) e a última
   mensagem de cada uma. O pseudo-chat de **Atualizações de Status** (`wpp` e WhatsApp Business, ver abaixo)
   mantém o nome do contato mas ganha um badge **"Status"** ao lado e, sem mensagem de verdade para
   mostrar, o preview fixo "Atualização de Status (sem texto)".
3. Abrir uma conversa: as mensagens são carregadas em janelas (as mais recentes primeiro, "mais
   antigas"/"mais novas" navegam pela história). O deep-link `?msg=ID` (citado pela IA como
   `[msg:wpp:ID]`, `[msg:sms:ID]`) abre a conversa já posicionada na mensagem citada e a destaca.
4. Clicar num anexo abre imagem/áudio/vídeo/PDF (servido do `.ufdr`); figurinhas (`image/webp`) são
   sinalizadas à parte (`eh_figurinha`) para o front não tratá-las como foto enviada; a transcrição do
   áudio aparece sob a bolha quando disponível. A foto de perfil do outro lado da conversa é exibida
   (só WhatsApp — ver resolução abaixo).
5. Em **Participantes**, cada contato pode ser associado a uma pessoa (busca fuzzy) e/ou município/UF
   — envio de todos os campos nulos limpa a associação.
6. **Marcadores** (ver [marcadores.md](marcadores.md)): o 🗒️ de cada bolha marca a mensagem, e o 🗒️
   que aparece ao passar o mouse sobre um item da lista lateral marca a **conversa** — o contato
   naquele mensageiro. São coisas diferentes: marcar a conversa é triagem do fio ("já li", "é
   relevante") e **não** marca as mensagens dela. O botão flutuante 🏷️, ao lado do da IA, abre o **sumário**: a árvore *marcador →
   conversa → mensagem*, que leva direto à bolha sem recarregar a página.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/conversas` | `fonte`, `extracao`, `app` | lista de conversas |
| `GET /api/investigacao/inqueritos/{id}/apps-mensageiros` | `extracao` | apps com contagens (tela mensageiros) |
| `GET /api/investigacao/conversas/{id}/mensagens` | `fonte`, `limite`, `antes_id`, `depois_id`, `centro_id` | janela paginada cronológica |
| `GET /api/investigacao/conversas/{id}/participantes` | `fonte` | participantes com associação atual |
| `GET /api/investigacao/conversas/{id}/foto` | `fonte=wpp` | foto de perfil, sob demanda do `.ufdr` |
| `GET /api/investigacao/anexos/{id}/imagem`, `/audio`, `/arquivo` | `fonte` | mídia/anexo, sob demanda do `.ufdr` |
| `POST /api/investigacao/contatos/{id}/associacao` | `fonte`, corpo `{id_pessoa, id_municipio, id_uf}` | associa ou limpa |
| `GET /api/investigacao/mensagens/{id}/conversa` | `fonte` | resolve conversa/inquérito do deep-link |
| `GET /api/referencia/pessoas`, `/municipios`, `/ufs` | `termo`, `limite` | autocomplete da associação |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_conversas` distingue o pseudo-chat de **Atualizações de Status** (`nu_whatsapp_chat_id
  LIKE 'Status:%'`), agrega os participantes em `string_agg` (excluindo o dono do aparelho) e traz a
  última mensagem (`dt_mensagem DESC NULLS LAST LIMIT 1`). `_TABELAS_CONVERSA` mapeia cada `fonte`
  para suas tabelas e coluna de identificador.
- **Atualizações de Status (Stories) como pseudo-chat**: o Cellebrite extrai as Stories do WhatsApp
  como um `<model type="Chat">` à parte, com `Source=WhatsApp` igual a uma conversa normal mas `Id`
  prefixado `"Status: <jid>"` (ex. `"Status: 5527997608633@s.whatsapp.net"`, enquanto a conversa real
  do mesmo contato é só `"5527997608633@s.whatsapp.net"`) — não é bug de duplicação, é um tipo de
  dado diferente que o Cellebrite mistura na tabela `Chat`; as mensagens desse pseudo-chat são só
  visualizações (`tx_mensagem` vazio, sempre `in_enviada=false`). O campo `eh_status` sai da mesma
  classificação (`tp_lista`), calculada onde há JID de chat: `fonte='wpp'` (`nu_whatsapp_chat_id`) e
  `fonte='msg'` (`nu_chat_id` — é por ali que passa o **WhatsApp Business**, que o Cellebrite entrega
  como Source separado mas grava com os mesmos JIDs); `sms` não tem JID e devolve sempre `false`. É
  consumido em `web/js/whatsapp.js` (`renderConversa`) para o badge. Cuidado ao alterar essa query:
  o `LIKE 'Status:%'` precisa de `%%` no f-string Python (psycopg interpreta `%` sozinho como
  placeholder).
- `listar_mensagens_conversa` devolve `{mensagens, tem_anteriores, tem_posteriores}`; cada mensagem
  traz `anexos` via `json_agg` com `disponivel` (`co_caminho_zip IS NOT NULL`), `imagem_disponivel`/
  `audio_disponivel` (mesma condição + `tp_conteudo LIKE 'image/%'`/`'audio/%'`) e `eh_figurinha`
  (`tp_conteudo = 'image/webp'`) — o front usa essas flags para decidir se mostra o botão de abrir
  mídia sem precisar de uma segunda ida ao servidor.
- `associar_contato` grava `ID_PESSOA_ASSOCIADA`/`ID_MUNICIPIO_ASSOCIADO`/`ID_UF_ASSOCIADA` +
  `ID_USUARIO_ASSOCIOU` no contato da fonte correspondente.
- Acesso: as rotas que só recebem `id_conversa` passam por `_exigir_acesso_via_conversa`, que resolve
  o inquérito (`obter_id_inquerito_de_conversa`) e checa `usuario_tem_acesso_inquerito` (403 caso o
  usuário não tenha permissão).
- Anexos/foto: `obter_binario_anexo` devolve o caminho dentro do ZIP; `ufdr_cache.ler_membro` lê sob
  demanda e `redimensionar_imagem` reencoda a prévia em JPEG; o cache em memória (`ufdr_cache.py`) é
  dois LRUs independentes com TTL de 1h renovado a cada acesso — até 8 `.ufdr` com handle aberto
  (`ZIPS_MAX`) e até 200 MB/500 itens de conteúdo já lido (imagens redimensionadas etc.), com item
  individual acima de 5 MB (vídeo, por exemplo) nunca entrando no cache de conteúdo.

**Foto de perfil do WhatsApp (`_foto_de_jid`, `ufdr_parser.indexar_fotos_perfil`)**: resolvida pela
**listagem do ZIP**, não pelo `report.xml` nem pelos `<model type="ContactPhoto">` (existem, mas
exigiriam parsear o XML inteiro — inviável num `.ufdr` de dezenas de GB). O parser varre os nomes de
`files/Image/` e casa pela regex `_RE_FOTO_PERFIL = ^(\d{10,15})(?:@|-\d)` sobre o basename:

- **Android (Samsung)**: `files/Image/<jid>.j` (ex. `5527999516013@s.whatsapp.net.j`) — casar por
  tamanho seria ambíguo (vários candidatos do mesmo size); o nome é que é determinístico.
- **iPhone**: `files/Image/<telefone>-<ts>.thumb` (ou `.jpg`) — telefone antes do `-`.

Quando há mais de um arquivo para o mesmo número, `_PRIORIDADE_FOTO` prefere a imagem cheia (`.jpg`)
à miniatura (`.thumb`). O mapa telefone→caminho é indexado uma vez por ingestão e gravado em
`WPP_TB01_CONTATOS.CO_CAMINHO_FOTO` (só o ponteiro; o binário nunca vai para o banco) via
`_upsert_contato`; `_foto_de_jid` resolve o telefone a partir do JID (`\d{10,15}` antes do `@`) para
casar contra esse mapa. Servida sob demanda por `GET /api/investigacao/conversas/{id}/foto`
(`obter_foto_conversa` resolve o contraparte não-dono da conversa). Só existe para WhatsApp — a
agenda de contatos (`CTT_TB01`, ver seção seguinte) e SMS/mensageiros não têm foto.

**Dono do aparelho**: a exclusão do dono na lista de participantes (`NOT
coalesce(ct.in_dono_dispositivo, false)`) usa a coluna `IN_DONO_DISPOSITIVO` de cada contato,
gravada por participante a partir do campo `IsPhoneOwner` do `Party` (funciona igual em Android e
iOS, direto do XML). Já o registro "oficial" do dono do aparelho (usado no modal "Dono do aparelho"
do card da extração, em `web/js/investigacao.js` — não uma tela deste documento, ver
`investigacao-blocos-vs-topbar`) depende de plataforma: Android tem `<model type="User">`, que
`ufdr_parser.iterar_donos` lê direto; **o iPhone não tem nenhum `<model type="User">` no
report.xml**. Nesse caso a ingestão sintetiza o dono em `investigacao_utils._derivar_dono_ios`, a
partir de duas fontes coletadas no mesmo passe de `iterar_registros` (sem reabrir o XML):
`_coletar_conta_ios` (a(s) conta(s) `<model type="UserAccount">` cujo usuário termina em
`@icloud.com`/`@me.com`/`@mac.com`/`@apple.com` ou cujo serviço cita "apple"/"icloud" — dos ~200
`UserAccount` de um iPhone a maioria são contas de apps, dedup por e-mail) e `_coletar_fone_dono`
(o telefone que aparece como `IsPhoneOwner=true` no maior número de conversas WhatsApp/SMS, com
fallback para o metadado `LastUsedMSISDN` quando nenhum `Party` tem o flag). Sem essa derivação,
toda extração de iPhone ficava com `qt_donos = 0`.

### Modelo de dados

Schema `INVESTIGACAO`. WhatsApp (de `db/init/10_whatsapp.sql`):

| Tabela | Colunas relevantes |
|---|---|
| `WPP_TB01_CONTATOS` | `ID_CONTATO`, `ID_EXTRACAO`, `NU_WHATSAPP_ID` (JID), `NO_CONTATO`, `IN_DONO_DISPOSITIVO`; único por `(ID_EXTRACAO, NU_WHATSAPP_ID)` |
| `WPP_TB02_CONVERSAS` | `NU_WHATSAPP_CHAT_ID`, `NO_GRUPO`, `TP_CONVERSA` (individual/grupo), `DT_INICIO`, `DT_ULTIMA_ATIVIDADE` |
| `WPP_TB03_PARTICIPANTES` | N:N conversa × contato, `IN_ADMIN_GRUPO` |
| `WPP_TB04_MENSAGENS` | `TX_MENSAGEM`, `DT_MENSAGEM`, `TP_MENSAGEM`, `CD_STATUS_MENSAGEM`, `IN_ENVIADA`, `NU_IDENTIFICADOR_ORIGINAL` |
| `WPP_TB05_ANEXOS` | `NO_ARQUIVO`, `TP_CONTEUDO`, `QT_BYTES`, `CO_URL_ORIGINAL` (CDN, normalmente expirada) |

SMS espelha o mesmo formato (`SMS_TB01..05`); mensageiros diversos usam uma família única
(`MSG_TB01..05`) com a coluna `TP_APP` separando cada app e chave natural
`(ID_EXTRACAO, TP_APP, NU_IDENTIFICADOR)`. Migrações posteriores acrescentaram:

- `10_whatsapp.sql` — colunas de associação em `WPP_TB01` e `SMS_TB01` com FKs para
  `DADOS_EXTRAIDOS.PES_TB01_PESSOAS`, `DADOS_REFERENCIA.IBGE_TB08_MUNICIPIO`,
  `DADOS_REFERENCIA.IBGE_TB03_UF` e `SEGURANCA.USR_TB01_USUARIOS`; em `MSG_TB01` já nascem criadas.
- `10_whatsapp.sql` — `WPP_TB01_CONTATOS.CO_CAMINHO_FOTO` (caminho no `.ufdr`, sem binário no banco).
- `10_whatsapp.sql` — `CO_CAMINHO_AUDIO` e `TX_TRANSCRICAO` em `WPP_TB05_ANEXOS` e
  `SMS_TB05_ANEXOS` (`MSG_TB05` já tem essas colunas de construção).

Contadores por extração em `INQ_TB02_EXTRACOES`: `QT_CONVERSAS`, `QT_MENSAGENS`, `QT_SMS_CONVERSAS`,
`QT_SMS_MENSAGENS`, `QT_MSG_CONVERSAS`, `QT_MSG_MENSAGENS`, `QT_AUDIOS_TRANSCRITOS`.

## Agenda de contatos e associação a identidades

### Visão geral

A tela `web/contatos.html` mostra a **agenda de contatos** extraída do aparelho (elemento `Contact`
do UFDR), com telefones e e-mails agregados por contato. Ela é mais rica que a lista de contatos de
conversa: para cada contato a agenda traz o **app de origem** (`no_aplicativo_origem`, ex. WhatsApp,
Contacts), grupo e tipo. `CTT_TB01` não tem foto de perfil — a única foto de contato do sistema é a
do WhatsApp, resolvida no visualizador de conversas (ver
[Visualizador de conversas](#visualizador-de-conversas-whatsapp-sms-e-mensageiros-diversos)), porque
só ali há um jeito barato de casar telefone→imagem pelo nome do arquivo dentro do `.ufdr`.

Além da leitura, é desta tela que o investigador **liga um contato a uma identidade**: pessoa da base
de referência (busca fuzzy por nome) e/ou município/UF. É julgamento humano, não dado extraído — os
campos são opcionais, nulos limpam a associação, e fica gravado qual usuário associou.

### Fluxo de uso

1. Na tela de Investigação, abrir **Contatos** no bloco da extração (ou pelo bloco do inquérito
   inteiro, cruzando aparelhos).
2. Filtrar por busca (nome, grupo, app de origem, telefone ou e-mail), tipo de contato
   (`tp_contato`) e extração; a paginação é no servidor.
3. Clicar em associar num contato: o modal usa autocomplete de pessoas (`/api/referencia/pessoas`),
   municípios (`/api/referencia/municipios`) e seletor de UF (`/api/referencia/ufs`).
4. **Exportar para Excel** gera o `.xlsx` com a listagem filtrada (botão instalado via
   `data-exportar="contatos"`).

### Como funciona

Rotas em `api/rotas/investigacao.py` e `api/rotas/referencia.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/contatos` | `extracao`, `busca`, `tipo`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/contatos/filtros` | `extracao` | tipos distintos para o select |
| `POST /api/investigacao/contatos/{id_contato}/associacao` | `fonte` (wpp/sms/msg), corpo `{id_pessoa, id_municipio, id_uf}` | grava ou limpa a associação |
| `GET /api/referencia/pessoas` | `termo`, `limite` | busca fuzzy de pessoas (autocomplete) |
| `GET /api/referencia/municipios` | `termo`, `limite` | municípios por nome |
| `GET /api/referencia/ufs` | — | lista de UFs |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_contatos` faz `string_agg` das entradas do contato (telefones/e-mails separados por vírgula)
  e devolve `{total, pagina, limite, itens}`.
- `_where_contato` monta o filtro de busca: `no_contato`, `no_aplicativo_origem`, `no_grupo` ou
  `tx_valor` de `CTT_TB02` (`EXISTS` na subconsulta), tudo `ILIKE`.
- `facetas_contato` devolve os `tp_contato` distintos.
- `associar_contato` grava nas colunas de associação da tabela da fonte: `WPP_TB01_CONTATOS`,
  `SMS_TB01_CONTATOS` ou `MSG_TB01_CONTATOS`, sempre com o `id_usuario` da sessão
  (`ID_USUARIO_ASSOCIOU`). A rota exige acesso via `_exigir_acesso_via_contato`, que resolve o
  inquérito dono do contato antes de permitir.

A associação alimenta a **mesa de investigação** (pessoa com evidência) e é refletida na listagem de
participantes do visualizador de conversas.

### Modelo de dados

`db/init/11_sms.sql`:

| Tabela | Colunas relevantes |
|---|---|
| `CTT_TB01_CONTATOS` | `ID_CONTATO`, `ID_EXTRACAO`, `NO_CONTATO`, `NO_APLICATIVO_ORIGEM` (Source), `NO_GRUPO` (Group), `TP_CONTATO` (Type) |
| `CTT_TB02_CONTATO_ENTRADAS` | `ID_ENTRADA`, `ID_CONTATO`, `TP_ENTRADA` (PhoneNumber/EmailAddress), `NO_CATEGORIA` (Phone/Mobile/Home/Work...), `TX_VALOR` |

As colunas de associação à identidade ficam nas tabelas de contatos de conversa (`10_whatsapp.sql`
e `12_mensageiros.sql`): `ID_PESSOA_ASSOCIADA`, `ID_MUNICIPIO_ASSOCIADO`, `ID_UF_ASSOCIADA` e
`ID_USUARIO_ASSOCIOU`, com FKs para `DADOS_EXTRAIDOS.PES_TB01_PESSOAS`,
`DADOS_REFERENCIA.IBGE_TB08_MUNICIPIO`, `DADOS_REFERENCIA.IBGE_TB03_UF` e
`SEGURANCA.USR_TB01_USUARIOS`. A agenda em si (`CTT_TB01`) não tem essas colunas — a associação é
feita no contato de conversa correspondente.

## Ligações

### Visão geral

A tela `web/ligacoes.html` lista as **ligações** do aparelho (elemento `Call` do UFDR): chamadas de
voz e vídeo com direção, status, duração e o "outro lado" já resolvido em nome e telefone.

O modelo não tem tabela de participantes N:N — cada registro é sempre a ligação do dono do aparelho
com um único interlocutor, nas colunas `NU_TELEFONE_CONTATO`/`NO_CONTATO`. Filtros de direção,
período do dia e intervalo de datas ajudam a reconstituir rotina e contatos mais frequentes.

### Fluxo de uso

1. Na tela de Investigação, abrir **Ligações** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos).
2. Filtrar por extração, direção (`Incoming`/`Outgoing`), período do dia (madrugada 0–6h, manhã
   6–12h, tarde 12–18h, noite 18–24h, em horário local) e intervalo de datas; busca livre por nome,
   telefone ou app.
3. A listagem é paginada no servidor, mais recente primeiro. Há exportação para Excel da listagem
   filtrada.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/ligacoes` | `extracao`, `busca`, `direcao`, `data_de`, `data_ate`, `periodo`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/ligacoes/filtros` | `extracao` | direções distintas (select) |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_ligacoes` devolve `{total, pagina, limite, itens}` ordenado por `dt_ligacao DESC NULLS LAST`.
- `_where_ligacao` monta os filtros: `tp_direcao` exato, `data_de`/`data_ate` em `AAAA-MM-DD` e
  `periodo` aplicado com `_hora_local_sql(l.dt_ligacao) BETWEEN ...` — o mesmo padrão
  (`_PERIODOS_ARQUIVO`) usado nas demais telas com faixa de horário.
- `facetas_ligacao` devolve os `tp_direcao` distintos.

As ligações entram na **linha do tempo unificada** com categoria `ligacao`, descrição montada como
"Recebida/Realizada de <nome> (<duração>s)" quando o horário é conhecido.

### Modelo de dados

`db/init/11_sms.sql`:

| Coluna | Origem no UFDR | Observação |
|---|---|---|
| `LIG_TB01_LIGACOES.ID_LIGACAO` | — | PK |
| `ID_EXTRACAO` | — | FK para `INQ_TB02_EXTRACOES` |
| `NO_APLICATIVO` | Source | app que registrou a chamada |
| `TP_DIRECAO` | Direction | Incoming/Outgoing |
| `CD_STATUS` | Status | Missed/Established/... |
| `DT_LIGACAO` | — | timestamp |
| `QT_DURACAO_SEGUNDOS` | — | inteiro |
| `IN_VIDEOCHAMADA` | — | booleano |
| `NU_TELEFONE_CONTATO` | Identifier da Party | o outro lado |
| `NO_CONTATO` | Name da Party | quando há nome |

Índice `IX19_LIG_TB01_DT_LIGACAO (DT_LIGACAO)`; contador `INQ_TB02_EXTRACOES.QT_LIGACOES` mostra na
tela de Investigação quantas ligações cada aparelho trouxe.

## Localizações, geocodificação e grafo de deslocamento

### Visão geral

A tela `web/localizacoes.html` mostra as **localizações** registradas no aparelho (elemento
`Location` do UFDR: Waze, Google Maps, etc.) com coordenadas, endereço quando o UFDR trouxe, app de
origem e categoria. Tem duas visões do mesmo conjunto filtrado:

- **Tabela** — listagem paginada com filtros de app, categoria, período do dia, datas e "só com
  coordenadas", além do botão **Buscar endereços (OpenStreetMap)**, que faz **geocodificação
  reversa** sob demanda via Nominatim (nunca automática).
- **Grafo** — para quando os pontos não cabem na tela: as localizações são agregadas **no servidor**
  em células de grade viradas em nós, ligados por **arestas de deslocamento** (registros consecutivos
  no tempo em células diferentes), com o nível da grade acompanhando o zoom do mapa.

Acima das duas visões, o seletor **Fontes** diz o que entra na tela: quais **aparelhos** do
inquérito (uma extração cada) e se as **antenas da operadora** (ERBs, da quebra de sigilo
telefônico) aparecem junto no mapa. Marcar dois celulares de uma vez é o que mostra dois
investigados no mesmo lugar na mesma hora; desmarcar todos deixa só as antenas. A escolha vai para
a URL (`?extracoes=11,12` e `?erbs=1`), então o recorte é compartilhável, e quem chega pelo menu
Navegar com `?extracao=` começa só naquele aparelho. Quem chega pelo botão **ERBs no mapa** começa
sem aparelho nenhum — pediu telefonia, recebe telefonia.

Os filtros de **data** e **faixa do dia** valem também para as antenas, que são recortadas pelas
chamadas que passaram por elas (ver `telefonia.md`); os demais filtros (app, categoria, busca,
área) são do aparelho e não as tocam.

Sobre o mapa do grafo é possível ainda importar **camadas geoespaciais de shapefile** (`.shp`) —
perímetro de operação, limite de bairro, setor censitário, área de risco. Além de desenhar os
polígonos, a camada responde duas perguntas em cima dos registros do aparelho: **quantas
localizações caíram dentro de cada área** e **"me mostre só o que está dentro desta área"** (o
recorte entra no mesmo conjunto de filtros, então vale para a tabela e para o grafo).

### Fluxo de uso

1. Na tela de Investigação, abrir **Localizações** no bloco da extração.
2. Alternar entre as abas **Tabela** e **Grafo** (Leaflet): o grafo deriva `?nivel=` (0..6, do país
   ao endereço) do zoom e recorta à área visível (`lat_min/lat_max/lon_min/lon_max`).
3. Na tabela, clicar em **Buscar endereços (OpenStreetMap)**: a cada chamada o servidor processa no
   máximo 25 coordenadas novas (respeitando 1 requisição/segundo ao Nominatim); as coordenadas já
   cacheadas voltam na hora.
4. No grafo, **Nomear lugares visíveis** geocodifica só os nós que estão na tela; o filtro de
   **trajetos pouco repetidos** destaca a rotina (esconde deslocamentos raros).
5. No grafo, **Importar SHP** abre o envio da camada geoespacial. Shapefile não é um arquivo só —
   aceita o `.zip` do conjunto ou os arquivos soltos (`.shp` + `.dbf` + `.prj` + `.shx`). Cada
   camada importada vira uma etiqueta acima do mapa: liga/desliga o desenho, mostra quantos
   registros caíram dentro, enquadra, renomeia e apaga.
6. No seletor **Fontes**, marcar/desmarcar aparelhos e as antenas: tabela, grafo e contagem por
   área recarregam no mesmo conjunto.
7. Clicar num polígono abre os atributos do `.dbf`, a contagem daquela área e o botão **Só esta
   área**; o mesmo recorte também está no `<select>` **Qualquer área**, na barra de filtros.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/localizacoes` | `extracao`, `extracoes`, `busca`, `app`, `categoria`, `so_coordenadas`, `data_de`, `data_ate`, `periodo`, `camada`, `feicao`, `ordenar`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/localizacoes/grafo` | `extracao`, `extracoes`, `busca`, `app`, `categoria`, `data_de`, `data_ate`, `periodo`, `nivel` (0..6), `lat_min/lat_max/lon_min/lon_max`, `camada`, `feicao` | nós + arestas |
| `GET /api/investigacao/inqueritos/{id}/localizacoes/filtros` | `extracao` | apps e categorias distintos |
| `POST /api/investigacao/inqueritos/{id}/localizacoes/geocodificar` | corpo `{coordenadas: [[lat, lon], ...]}` | devolve `{enderecos, cacheadas, geocodificadas, restantes}` |
| `GET /api/investigacao/inqueritos/{id}/localizacoes/camadas` | — | camadas de shapefile do inquérito (metadados) |
| `POST /api/investigacao/inqueritos/{id}/localizacoes/camadas` | multipart `arquivos[]` (.zip ou o conjunto), `nome`, `cor` | importa uma camada |
| `GET /api/investigacao/inqueritos/{id}/localizacoes/camadas/{id_camada}` | — | a camada como FeatureCollection GeoJSON |
| `GET …/camadas/{id_camada}/contagem` | mesmos filtros da tabela | `{contagens: {id_feicao: n}, total_dentro}` |
| `PUT` / `DELETE` `…/camadas/{id_camada}` | corpo `{nome, cor}` no PUT | renomeia / apaga |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `grafo_localizacoes` agrega por grade: `_NIVEIS_GRAFO_LOCALIZACAO = [2.0, 0.5, 0.1, 0.02, 0.005,
  0.001, 0.0002]` graus por célula (índice = `nivel`). Uma CTE `base` converte o timestamp para hora
  local (`America/Sao_Paulo`), calcula `floor(lat/passo)`/`floor(lon/passo)` e o recorte de bbox é
  aplicado aos nós; há join com o cache de endereços. Devolve `{nivel, passo, nos, arestas,
  total_registros, total_celulas, foco}`, com arestas ponderadas pela repetição do trajeto.
- `facetas_localizacao` devolve apps e categorias distintos.

Geocodificação em `api/funcoes/geocodificacao_utils.py`:

- `geocodificar_coordenadas` consulta `https://nominatim.openstreetmap.org/reverse` com User-Agent
  `analyser-forense/1.0 (geocodificacao reversa de localizacoes)`, timeout de 10 s e intervalo de
  1,1 s entre chamadas; processa no máximo `LIMITE_POR_CHAMADA = 25` coordenadas novas por chamada.
- Resultado grava no cache **global** `GEO_TB01_ENDERECOS` — cada coordenada é consultada uma única
  vez, para sempre.

Camadas de shapefile em `api/funcoes/geoespacial_utils.py` (front em `web/js/localizacoes_camadas.js`):

- **Leitura** com `pyshp`: `.shp` (geometria) + `.dbf` (atributos) + `.prj` (projeção) + `.shx`.
  Só polígonos — shapefile de ponto ou linha é recusado com mensagem.
- **Reprojeção** com `pyproj`, na importação: shapefile brasileiro raramente vem em lat/lon (o comum
  é SIRGAS 2000 / UTM, em metros na casa dos milhões). O `.prj` é lido e a geometria convertida para
  WGS 84, o mesmo referencial de `LOC_TB01`. **Sem `.prj`** as coordenadas só são aceitas se já
  estiverem na faixa lat/lon; fora dela a feição é descartada, em vez de virar polígono no oceano.
- **Simplificação** Douglas-Peucker a `TOLERANCIAS[0] = 0,000002°` (~0,2 m, abaixo do pixel do zoom
  máximo do Leaflet), escalando só se a camada passar de `MAX_VERTICES = 400.000` vértices. A
  tolerância usada fica em `VL_TOLERANCIA` e aparece no *tooltip* da etiqueta da camada — a
  geometria desenhada não é a do arquivo vértice a vértice, e quem examina precisa saber disso.
- **Contenção sem PostGIS**: o Postgres do projeto é o `pgvector/pgvector:pg16` — Postgres 16 com
  a extensão `vector` da busca semântica, mas sem PostGIS. Cada anel do
  polígono é gravado também no tipo nativo `POLYGON` (`GEO_TB04_ANEIS`), e "está dentro?" é
  `pg_anel @> point(lon, lat)`, com a caixa envolvente do anel (`VL_LAT_MIN`…) rejeitando barato
  antes de percorrer os vértices. Dentro = em algum contorno externo **e** em nenhum buraco.
- O recorte por área entra em `_where_localizacao` (`_filtro_area_localizacao`), o mesmo construtor
  de WHERE da tabela e do grafo — por isso as duas visões continuam mostrando o mesmo conjunto. O
  `JOIN` com `GEO_TB02_CAMADAS` carrega o `id_inquerito`: sem ele bastaria trocar o número na URL
  para recortar por uma área de outro inquérito.
- Tetos da importação: limite geral de upload (`UPLOAD_DOCUMENTO_MB`, sobre o TOTAL somado do
  conjunto — não por arquivo), 5.000 feições, 400 mil vértices depois de simplificar.

### Modelo de dados

| Tabela | Colunas relevantes |
|---|---|
| `LOC_TB01_LOCALIZACOES` (`11_sms.sql`) | `ID_LOCALIZACAO`, `ID_EXTRACAO`, `NO_APLICATIVO` (Source), `DT_LOCALIZACAO`, `VL_LATITUDE`/`VL_LONGITUDE` NUMERIC(10,6), `TX_ENDERECO` (PositionAddress), `NO_CATEGORIA`, `TX_DESCRICAO` (Description/Name) |
| `GEO_TB01_ENDERECOS` (`15_localizacao.sql`) | `ID_ENDERECO`, `VL_LATITUDE`/`VL_LONGITUDE` NUMERIC(10,5) (arredondadas), `TX_ENDERECO`, `NO_FONTE` ('nominatim'), `DT_GEOCODIFICACAO`; UNIQUE(lat, lon) |
| `GEO_TB02_CAMADAS` (`15_localizacao.sql`) | `ID_CAMADA`, `ID_INQUERITO`, `NO_CAMADA`, `NO_ARQUIVO`, `CO_HASH`, `NO_PROJECAO`/`TX_PROJECAO` (o `.prj` cru, para auditar a reprojeção), `QT_FEICOES`, `QT_VERTICES`, `VL_TOLERANCIA`, caixa `VL_LAT_MIN`…, `CO_COR`, `DT_IMPORTACAO`, `NO_IMPORTADA_POR` |
| `GEO_TB03_FEICOES` (`15_localizacao.sql`) | `ID_FEICAO`, `ID_CAMADA`, `NU_ORDEM`, `NO_FEICAO` (rótulo tirado do `.dbf`), `TX_ATRIBUTOS` JSONB (o registro do `.dbf` inteiro), `TX_GEOMETRIA` JSONB (GeoJSON em WGS 84), caixa |
| `GEO_TB04_ANEIS` (`15_localizacao.sql`) | `ID_ANEL`, `ID_FEICAO`, `ID_CAMADA`, `IN_BURACO`, caixa, `PG_ANEL` POLYGON (vértices em x=lon, y=lat) |

Índices: `IX20_LOC_TB01_DT_LOCALIZACAO`; contador `INQ_TB02_EXTRACOES.QT_LOCALIZACOES`. O cache de
endereços é **global ao sistema** (não por inquérito), e o arredondamento a 5 casas é feito no SQL
dos dois lados do join. `LOC_TB01` é compartilhada com qualquer fonte que grave localizações pela
mesma extração-ponte: além do UFDR, os locais do Google Maps/Saved/Timeline importados pela Nuvem
IPED (ver [Dados de conta Google](#dados-de-conta-google-sem-equivalente-em-ufdr)).

A geometria de uma camada é guardada **duas vezes**, de propósito: `GEO_TB03_FEICOES.TX_GEOMETRIA`
(GeoJSON) é o que vai inteiro para o Leaflet desenhar, e `GEO_TB04_ANEIS.PG_ANEL` é quem responde
"este ponto está dentro?". JSONB não tem teste de contenção, e o `POLYGON` do Postgres não
representa buraco nem multipolígono — nenhum dos dois sozinho serve para os dois usos. As três
tabelas saem em cascata: apagar a camada leva feições e anéis, e apagar o inquérito leva as camadas
(`_apagar_dados_inquerito`).

## Linha do tempo unificada

### Visão geral

A tela `web/timeline.html` reúne numa única sequência cronológica **todos os domínios com data/hora**
de um inquérito: ligações, mensagens de WhatsApp/SMS/mensageiros, localizações, notificações, notas,
navegação e e-mails. Em vez de abrir tela por tela, o investigador vê o dia do alvo inteiro de uma
vez, e cada evento tem um link que abre o **registro original** na tela de origem.

### Fluxo de uso

1. Na tela de Investigação, abrir **Timeline** (bloco do inquérito como um todo ou de uma extração).
2. Filtrar por texto livre, **conjunto** (recorte temático — ver abaixo), aplicações (multi-seletor
   com checkboxes — a API aceita várias categorias separadas por vírgula: `ligacao`, `mensagem_wpp`,
   `mensagem_sms`, `localizacao`, `notificacao`, `nota`, `navegacao`, `email` e as demais por
   mensageiro, ex. `mensagem_instagram`), **aparelhos** (multi-seletor — mais de um celular no
    mesmo recorte; a API lê `extracao=1,2`), faixa de horário (`madrugada`/`manha`/`tarde`/
   `noite`), intervalo de datas e **"só com localização"** (`somente_geo=1`, a base do mapa da
   página). Filtros, tamanho de página (50/100/200/500) e página atual vivem na query string
   (`EstadoUrl`): voltar não perde o recorte e a URL é compartilhável.
3. Paginação no servidor. No evento, o botão 🔎 abre o original — a conversa do WhatsApp/SMS/
   mensageiro posicionada na mensagem, montada no front pela regra única `MapaTelas.paginaConversa`
   (o SQL ainda devolve `tx_link`, usado como URL da navegação) — e o botão ⓘ abre o **painel de
   detalhe** com o `tx_dados` estruturado da origem (duração da ligação, valor/participantes da
   transferência, convidados do compromisso…) e mini-mapa Leaflet quando há coordenada.
4. Checkbox por evento + barra de seleção marca N eventos de uma vez "para o relatório" pelo lote
   (`POST /api/investigacao/inqueritos/{id}/marcadores/lote`), com marcador opcional — as marcações
   saem no .docx e na tela Marcações. O botão **Excel** exporta o recorte inteiro (todas as páginas
   do filtro, teto de 20 mil eventos), não só a página desenhada. O botão **🗺️ Mapa da página** desenha
   os eventos com coordenada da página atual como trilha temporal numerada (Leaflet sob demanda).

### Como funciona

Rota em `api/rotas/investigacao.py`:

`GET /api/investigacao/inqueritos/{id}/timeline` — parâmetros `extracao` (um id **ou vários em
vírgula — mais de um aparelho no mesmo recorte**), `busca`, `categoria` (vírgula), `conjunto`,
`periodo`, `data_de`, `data_ate`, `somente_geo`, `pagina`, `limite`, `sem_total`/`apenas_total`
(ver desempenho). O seletor de aparelhos (multi-checkbox) usa
`GET /api/investigacao/inqueritos/{id}/extracoes-lista`.

Pontos-chave em `api/funcoes/investigacao_utils.py` (`listar_timeline`):

- Um `UNION ALL` de dezessete fontes: `LIG_TB01_LIGACOES` (`ligacao`), `WPP_TB04` (`mensagem_wpp`),
  `SMS_TB04` (`mensagem_sms`), `MSG_TB04` (`mensagem_<app>` — a categoria usa o `TP_APP` do
  mensageiro), `LOC_TB01` (`localizacao`), `NTF_TB01` (`notificacao`), `NOT_TB01` (`nota`),
  `NAV_TB01` (`navegacao`), `EML_TB01` (`email`), `BSC_TB01` (`busca`), `RDS_TB01` (`social`),
  `CAL_TB01` (`compromisso`), `ERB_TB01` (`antena`), `CNX_TB01` (`conexao`), `JRN_TB01` (`trajeto`),
  `TRF_TB01` (`transferencia`) e `ARQ_TB01` com `TX_LINHA_TEMPO` (`arquivo_iped`).
- **No conjunto investigativo, os feriados importados automaticamente pelo Google Calendar
  (`no_categoria` 'Feriados…'/'Holidays…') ficam de fora do ramo de compromissos** — na agenda do
  investigador eles eram ruído puro.
- Cada linha traz `dt_evento`, `tp_categoria`, app, uma `tx_descricao` montada (ex. "Recebida de
  Fulano (34s)", "Enviou de Beltrano: ...", título da página com nº de visitas), o `tx_link`, a
  coordenada (`vl_lat`/`vl_lon` — localizações, trajetos pela origem, compromissos, buscas, sociais,
  mensagens e antenas com posição) e o `tx_dados`: um JSONB com os campos ricos da origem que a
  `tx_descricao` achata (`jsonb_strip_nulls(jsonb_build_object(...))` por ramo — duração da ligação,
  valor/moeda/participantes da transferência, convidados do compromisso, corpo da nota, etc.), que
   o painel de detalhe da tela desfaz em `dl` (compromissos trazem o `calendario` de origem —
   o nome do Google Calendar onde o evento nasceu).
- Links: o SQL ainda monta `tx_link` para mensagens (`whatsapp.html?inquerito=...&fonte=wpp&msg=<id>`,
  `mensageiros.html?...&fonte=sms&msg=<id>`, `...?fonte=msg&app=<app>&msg=<id>`) e para navegação
  (a URL da página). A tela usa apenas o `tx_link` da navegação; o link de conversa é remontado no
  front por `MapaTelas.paginaConversa` — a regra vive num lugar só.
- `somente_geo=1` restringe aos eventos com `vl_lat` — é o filtro "Só com localização" da tela e a
  base do mapa da página (trilha temporal Leaflet).
- Filtros de texto/datas/horário/geo aplicados no recorte; ordenação por `dt_evento DESC NULLS
  LAST`; devolve `{eventos, total, pagina, limite}` (`total` pode vir `null` com `sem_total=1`).
- **Desempenho** (a página 1 de um caso de 860 mil eventos levava 6s; hoje pinta em ~1s):
  1. os ramos do `UNION` são montados **só com o que o filtro pode responder** — categoria,
     conjunto e `somente_geo` descartam tabelas inteiras em Python antes do SQL (`_timeline_casa_categoria`);
  2. nenhum ramo joina `INQ_TB02_EXTRACOES`: os ids de extração são resolvidos antes e chegam como
     `id_extracao = ANY(%(ids)s)`, índice próprio de cada tabela; `no_arquivo`/`no_dispositivo`
     são acrescidos no fim, em Python;
  3. a deduplicação de eventos idênticos saiu do `GROUP BY` global (era o que obrigava a ler e
     agrupar o caso todo a cada página) — o LIMIT desce para dentro de cada ramo (`ORDER BY ...
     LIMIT ofs+limite` por fonte + merge fora) e a tela conta as repetições no crachá ×N
     (`agruparDuplicados` em `web/js/timeline.js`);
  4. a contagem do recorte (necessária só para a paginação) corre em paralelo à lista
     (`sem_total=1` na lista, `apenas_total=1` no contador) e fica em cache por recorte por
     5 minutos (`_TOTAIS_TIMELINE`); primeira página menor que o limite prova o total sozinha.
- **Conjuntos** (`_CONJUNTOS_TIMELINE`): recortes temáticos que agrupam categorias, para responder a
  uma pergunta de investigação sem marcar categoria por categoria. Um item terminado em `*` casa por
  prefixo — `mensagem_*` pega todos os mensageiros, cuja categoria carrega o nome do app.

  | Conjunto | Categorias |
  |---|---|
  | `investigativo` | `ligacao`, `mensagem_wpp`, `localizacao`, `trajeto`, `compromisso`, `transferencia` |
  | `comunicacao` | `ligacao`, `mensagem_*`, `email` |
  | `localizacao` | `localizacao`, `compromisso`, `trajeto`, `antena` |
  | `internet` | `navegacao`, `busca`, `social` |
  | `financeiro` | `transferencia` |
  | `aparelho` | `notificacao`, `nota`, `conexao` |

  Fora `investigativo` — que atravessa os temas e é definido pelo uso —, os demais **particionam**
  todas as categorias: somados dão exatamente o total sem filtro. A tela também usa o conjunto para
  enxugar o seletor de aplicações ao recorte escolhido (e volta a aplicação para "Todas" se a que
  estava marcada ficou de fora, combinação que daria lista vazia); quem filtra de fato é o servidor.
- **Faixa de horário** (`periodo`): reaproveita `_PERIODOS_ARQUIVO` e `_hora_local_sql`, os mesmos de
  arquivos/localizações/navegação. A hora sai convertida de UTC para `America/Sao_Paulo` antes do
  `EXTRACT`, senão a faixa discordaria do horário que a própria linha mostra. Valor fora da lista é
  ignorado (não filtra), nunca entra cru no SQL.
- **Modo analítico** (`analitico=true`, o mapa visual do tema Orbit): além dos pontos diários por
  categoria, o `resumo` traz `data_inicial`/`data_final` (a tela segura os `min`/`max` dos campos de
  data e anuncia o período na legenda) e `qt_madrugada`/`qt_manha`/`qt_tarde`/`qt_noite` — o KPI
  **PERÍODO DO DIA** mostra a faixa dominante com o percentual de cada uma no `title`). No Orbit o
  gráfico aceita toque (pan de um dedo, pinça com `touch-action: pan-y`), e as setas ← / →
  percorrem as bolhas em ordem temporal a partir da focada.
- Mensagens de mensageiro citadas pela IA em `[msg:msg:ID]` resolvem pela rota
  `GET /api/investigacao/mensagens/{id}/conversa` (fonte `msg`).

### Modelo de dados

A timeline **não tem tabela própria** — consulta as tabelas de cada domínio:

| Categoria | Tabela-fonte |
|---|---|
| `ligacao` | `LIG_TB01_LIGACOES` |
| `mensagem_wpp` | `WPP_TB04_MENSAGENS` (+ `WPP_TB01_CONTATOS`, `WPP_TB02_CONVERSAS`) |
| `mensagem_sms` | `SMS_TB04_MENSAGENS` |
| `mensagem_<app>` | `MSG_TB04_MENSAGENS` (+ `MSG_TB02_CONVERSAS.TP_APP`) |
| `localizacao` | `LOC_TB01_LOCALIZACOES` |
| `notificacao` | `NTF_TB01_NOTIFICACOES` |
| `nota` | `NOT_TB01_NOTAS` (usa `dt_modificacao`, senão `dt_criacao`) |
| `navegacao` | `NAV_TB01_PAGINAS_VISITADAS` |
| `email` | `EML_TB01_EMAILS` |

O filtro de extração sempre passa por `INQ_TB02_EXTRACOES` (o `id_extracao` de cada domínio).

## Notas de bloco de notas do aparelho

### Visão geral

A tela `web/notas.html` lista as **notas** dos blocos de notas do aparelho (elemento `Note` do
UFDR): Samsung Notes, Apple iCloud Backup, Notes, Google Keep etc., identificadas pelo app de
origem em `no_aplicativo`.

Cada nota traz título (frequentemente ausente no iOS, onde a primeira linha do corpo serve de
título), corpo, datas de criação/modificação, status (`Intact`/`Trash`/`Deleted`) e os nomes dos
anexos quando houver. É uma fonte importante de prova documental: em casos de tráfico o bloco de
notas costuma funcionar como "caderno de vendas" (quantidade, preço, apelido do comprador).

### Fluxo de uso

1. Na tela de Investigação, abrir **Notas** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos).
2. Filtrar por **busca** (título, corpo ou app, trigrama), **status** e **app**; a listagem é única
   (sem paginação no servidor).
3. Abrir uma nota para ver o corpo completo e a lista de anexos resolvidos (nome + link para o
   arquivo correspondente no inventário, quando existir). Cada linha aceita o pin de entidade e a
   marcação para o relatório.

### Como funciona

Rota em `api/rotas/investigacao.py`:

`GET /api/investigacao/inqueritos/{id}/notas` — parâmetros `extracao` (restringe a um aparelho).

Pontos-chave em `api/funcoes/investigacao_utils.py` (`listar_notas`):

- Lê `NOT_TB01_NOTAS` do inquérito (com os mesmos filtros básicos das demais telas).
- O campo `tx_anexos` guarda os nomes dos anexos separados por vírgula; a função resolve cada nome
  contra `ARQ_TB01_ARQUIVOS` da mesma extração e devolve uma lista `anexos` de `{nome, id_arquivo}`,
  que vira link para a tela de arquivos.
- O filtro de busca usa os índices de trigrama (IX88/IX89) para aguentar `ILIKE '%termo%'` sobre
  corpo de notas.

As notas entram na **linha do tempo unificada** (categoria `nota`), usando `dt_modificacao` quando
existir, senão `dt_criacao`.

### Modelo de dados

`db/init/17_aparelho_redes.sql`, tabela `NOT_TB01_NOTAS`:

| Coluna | Observação |
|---|---|
| `ID_NOTA`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_APLICATIVO` | Samsung Notes, Apple iCloud Backup, Notes, Google Keep... |
| `TX_TITULO` | normalmente vazio no iOS |
| `TX_CORPO` | texto livre |
| `DT_CRIACAO`, `DT_MODIFICACAO` | timestamps |
| `CD_STATUS` | deleted_state: Intact / Trash / Deleted |
| `TX_ANEXOS` | nomes dos anexos, separados por vírgula |

Índices de trigrama (`gin_trgm_ops`) em título e corpo para a busca; contador
`INQ_TB02_EXTRACOES.QT_NOTAS` na tela de Investigação (NULL = domínio ainda não varrido).

## Histórico de navegação

### Visão geral

A tela `web/navegacao.html` mostra o **histórico de páginas visitadas** do aparelho (elemento
`VisitedPage` do UFDR): em qual navegador ocorreu (Chrome, Samsung Internet...), título, URL, data
da última visita e quantas vezes a página foi aberta, além da conta logada no navegador quando o
UFDR trouxe. O topo da tela mostra KPIs (total de visitas, sites mais acessados, navegadores,
distribuição por período do dia) que acompanham os mesmos filtros da listagem.

### Fluxo de uso

1. Na tela de Investigação, abrir **Navegação** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos).
2. Filtrar por extração, busca (título/URL/conta), navegador, datas, período do dia (madrugada,
   manhã, tarde, noite — horário local) e ordenação (`recentes`, `antigos`, `visitas` ou `titulo`).
3. Ver os KPIs no topo, a listagem paginada no servidor e exportar para Excel quando necessário.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/navegacao` | `extracao`, `busca`, `navegador`, `data_de`, `data_ate`, `periodo`, `ordenar`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/navegacao/filtros` | `extracao` | navegadores distintos (select) |
| `GET /api/investigacao/inqueritos/{id}/navegacao/resumo` | mesmos filtros da listagem (sem paginação) | KPIs |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_paginas_visitadas` devolve `{total, pagina, limite, itens}` respeitando a ordenação pedida.
- `facetas_navegacao` devolve os `no_aplicativo` distintos (navegadores).
- `resumo_navegacao` agrega os KPIs com o mesmo `WHERE` da listagem — o recorte em tela e os números
  do topo nunca divergem.

As páginas entram na **linha do tempo unificada** (categoria `navegacao`), e o evento da timeline
leva link direto para a URL navegada.

### Modelo de dados

`db/init/16_navegacao_apps.sql`, tabela `NAV_TB01_PAGINAS_VISITADAS`:

| Coluna | Origem no UFDR | Observação |
|---|---|---|
| `ID_PAGINA`, `ID_EXTRACAO` | — | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_APLICATIVO` | Source | Chrome, Samsung Internet, etc. |
| `TX_TITULO` | Title | — |
| `TX_URL` | Url | texto livre |
| `DT_VISITA` | LastVisited | data da última visita |
| `QT_VISITAS` | VisitCount | quantas vezes a página foi aberta |
| `TX_CONTA` | Account | conta logada no navegador, quando houver |

Índice `IX41_NAV_TB01_DT_VISITA`; contador `INQ_TB02_EXTRACOES.QT_NAVEGACAO`. O histórico do Chrome
importado pela Nuvem IPED cai na mesma tabela (ver
[Dados de conta Google](#dados-de-conta-google-sem-equivalente-em-ufdr)).

## Aplicativos instalados

### Visão geral

A tela `web/aplicativos.html` lista os **aplicativos instalados** do aparelho (elemento
`InstalledApplication` do UFDR): pacote Android (ex. `com.whatsapp`), nome, versão, descrição, data
de instalação/compra e último uso, quando o UFDR trouxe.

O inventário de apps é também matéria-prima para outras partes do sistema: o app financeiro
instalado entra como evidência na tela de **contas financeiras**, e as contas de usuário logadas
(`UserAccount`) aparecem na tela de contas de usuário da investigação.

### Fluxo de uso

1. Na tela de Investigação, abrir **Aplicativos** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos).
2. Filtrar por extração e busca (nome do app ou pacote).
3. Listagem paginada no servidor, com dados de versão, datas de instalação e último uso. Cada linha
   aceita o pin de entidade e a marcação para o relatório.

### Como funciona

Rota em `api/rotas/investigacao.py`:

`GET /api/investigacao/inqueritos/{id}/aplicativos` — parâmetros `extracao`, `busca`, `pagina`,
`limite`.

Pontos-chave em `api/funcoes/investigacao_utils.py` (`listar_aplicativos`):

- Lê `APP_TB01_APLICATIVOS` do inquérito (filtro opcional por extração), com busca `ILIKE` sobre
  pacote e nome, e devolve `{total, pagina, limite, itens}`.

### Modelo de dados

`db/init/16_navegacao_apps.sql`, tabela `APP_TB01_APLICATIVOS`:

| Coluna | Origem no UFDR | Observação |
|---|---|---|
| `ID_APLICATIVO`, `ID_EXTRACAO` | — | PK e FK para `INQ_TB02_EXTRACOES` |
| `NU_PACOTE` | Identifier | pacote Android, ex. `com.whatsapp` |
| `NO_APLICATIVO` | Name | rótulo do app; costuma vir vazio no Android |
| `NU_VERSAO` | Version | — |
| `TX_DESCRICAO` | Description | — |
| `DT_INSTALACAO` | PurchaseDate | instalação/compra, quando disponível |
| `DT_ULTIMO_USO` | LastLaunched | último uso, quando disponível |

Índice `IX42_APP_TB01_ID_EXTRACAO`; contador `INQ_TB02_EXTRACOES.QT_APLICATIVOS`.

## Notificações

### Visão geral

A tela `web/notificacoes.html` lista as **notificações** registradas no aparelho (elemento
`Notification` do UFDR). O corpo (`Body`) costuma vir vazio; o app que de fato gerou a notificação
normalmente só aparece no texto livre de "Notes" — por isso a extração isola esse valor na coluna
`no_pacote_app`, que é o filtro principal da tela.

Notificações ajudam a entender a rotina de uso do aparelho (horários em que cada app é usado) e a
casar atividades com mensagens, localizações e redes Wi-Fi.

### Fluxo de uso

1. Na tela de Investigação, abrir **Notificações** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos).
2. Filtrar por extração, busca de texto (conteúdo/pacote), app (select com os pacotes distintos), e
   intervalo de datas/período do dia.
3. Listagem paginada no servidor, com o pacote do app, o conteúdo quando houver e a data/hora local.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/notificacoes` | `extracao`, `busca`, `app`, `data_de`, `data_ate`, `periodo`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/notificacoes/filtros` | `extracao` | apps distintos (select) |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_notificacoes` devolve `{total, pagina, limite, itens}`; os filtros de data/período usam o
  mesmo padrão `_PERIODOS_ARQUIVO` + `_hora_local_sql` das demais telas.
- `facetas_notificacao` devolve `{"apps": [...]}` com os `no_pacote_app` distintos da extração.

As notificações entram na **linha do tempo unificada** (categoria `notificacao`, com o app exibido
como `no_pacote_app` quando houver, senão `no_aplicativo_log`).

### Modelo de dados

`db/init/11_sms.sql`, tabela `NTF_TB01_NOTIFICACOES`:

| Coluna | Origem no UFDR | Observação |
|---|---|---|
| `ID_NOTIFICACAO`, `ID_EXTRACAO` | — | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_APLICATIVO_LOG` | Source | quem registrou o evento (ex. Digital Wellbeing) |
| `NO_PACOTE_APP` | extraído do texto "Notes" | pacote do app que gerou a notificação |
| `TX_CONTEUDO` | Body | texto, quando disponível (costuma vir vazio) |
| `DT_NOTIFICACAO` | — | timestamp |

Índice `IX21_NTF_TB01_DT_NOTIFICACAO`; contador `INQ_TB02_EXTRACOES.QT_NOTIFICACOES`.

## E-mails

### Visão geral

A tela `web/emails.html` lista os **e-mails** do inquérito: os extraídos do aparelho pelo UFDR e os
importados por um caso da Nuvem IPED (o IPED já quebra o mbox em uma mensagem por item — ver
`extracao_iped.md`) — ambos caem na mesma tabela `EML_TB01_EMAILS`, e a tela mostra a fonte de cada
mensagem (`no_fonte`). Cada registro traz remetente, destinatários/CC/CCO, assunto, corpo e data.

### Fluxo de uso

1. Na tela de Investigação, abrir **E-mails** no bloco da extração (ou sem `extracao`, cruzando
   aparelhos e fontes).
2. Filtrar por extração, busca (assunto/remetente/destinatários/corpo), fonte e remetente (selects
   alimentados por `.../filtros`), além de intervalo de datas.
3. Listagem paginada no servidor (mais recentes primeiro) e exportação para Excel do conjunto
   filtrado (`ExportarExcel.instalar`).

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/emails` | `extracao`, `busca`, `fonte`, `remetente`, `data_de`, `data_ate`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/emails/filtros` | `extracao` | `{fontes, remetentes}` para os selects |

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_emails` devolve `{total, pagina, limite, itens}` com `ORDER BY dt_email DESC NULLS LAST,
  id_email DESC`; `limite` respeita `LIMITE_MAX_LISTAGEM`.
- `facetas_emails` devolve as fontes e remetentes distintos (`no_fonte`/`no_remetente` não vazios).

Os e-mails entram na **linha do tempo unificada** (categoria `email`).

### Modelo de dados

`db/init/18_credenciais_financeiro.sql`, tabela `EML_TB01_EMAILS`:

| Coluna | Observação |
|---|---|
| `ID_EMAIL`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_FONTE` | origem do dado (app do UFDR, "IPED" num item da Nuvem IPED...) |
| `NO_REMETENTE` | remetente |
| `TX_DESTINATARIOS`, `TX_CC`, `TX_CCO` | destinatários, com cópia e cópia oculta |
| `TX_ASSUNTO`, `TX_CORPO` | assunto e texto |
| `DT_EMAIL` | data/hora |
| `TP_MODELO` | modelo Cellebrite de origem |

Contador `INQ_TB02_EXTRACOES.QT_EMAILS` (NULL = domínio ainda não varrido).

## Contas bancárias e carteiras de criptomoeda

### Visão geral

A tela `web/contas_financeiras.html` lista contas bancárias/financeiras encontradas no aparelho. O
UFDR raramente modela conta bancária de verdade, então a tela junta **três origens** de evidência:

1. **Conta modelada** — `FinancialAccount`/`BankAccount`/`Account` do UFDR, incluindo as carteiras de
   criptomoeda que o CryptoTracer do Cellebrite acha varrendo nomes de arquivo e mensagens
   (com a procedência do achado ao lado — `FoundInField`/`FoundInModelType`);
2. **App financeiro instalado** — deduzido do inventário de `InstalledApplication`;
3. **Credencial salva** — deduzida de `Password`/`UserAccount` (gerenciador de senhas / conta de
   aplicativo) cujo serviço bate no catálogo de instituições — bancos, carteiras de pagamento,
   exchanges, casas de aposta e serviços de crédito.

Evidências da mesma instituição viram **uma linha só**, com todas as fontes listadas na coluna de
fonte (isso acontece **na ingestão**, não a cada consulta — `listar_contas_financeiras` só lê
`BNC_TB01` já consolidada). Há também uma extração suplementar de SQLite de apps financeiros iOS
(best-effort, hoje Sicoob).

### Fluxo de uso

1. Na tela de Investigação, abrir **Contas bancárias** no bloco da extração (ou sem `extracao`).
2. A lista mostra instituição, titular, conta/agência (ou endereço da carteira de cripto),
   **categoria** (banco, pagamento, cripto, aposta, capitalização, crédito), moeda/ativo, saldo e a
   fonte de cada evidência.
3. Exportar para Excel quando necessário; cada linha aceita o pin de entidade e a marcação para o
   relatório.

### Como funciona

Rota em `api/rotas/investigacao.py`:

`GET /api/investigacao/inqueritos/{id}/contas-financeiras` — parâmetro `extracao`.

Pontos-chave em `api/funcoes/investigacao_utils.py`:

- `listar_contas_financeiras` lê `BNC_TB01_CONTAS_FINANCEIRAS` e ordena por categoria no padrão da
  análise financeira: `banco`/`conta_corrente` primeiro, depois `pagamento`, `cripto`, `aposta`,
  `capitalizacao`, `credito` e demais.
- `_extrair_contas_de_apps_ios` abre (em memória, via temp) o `CoreDataContas.sqlite` do Sicoob
  dentro do `.ufdr`, valida o cabeçalho SQLite e insere contas com `no_fonte = "Sicoob (app
  database)"` e `tp_modelo = "AppDatabase"`, evitando duplicatas por
  `(extração, conta, agência, instituição)`.

**Reconhecimento e fusão das três origens (`ufdr_parser.py`, na ingestão)**: `_identificar_instituicao`
recebe vários textos candidatos (pacote do app, domínio da URL de login, nome de exibição) e faz
**duas passadas**: primeiro contra `_INSTITUICOES_FIN` — o catálogo fixo de bancos/fintechs/
exchanges/casas de aposta/serviços de crédito, onde **a ordem do catálogo importa** (a marca
derivada tem que vir antes da marca-mãe, ex. `"com.itau.iti"` antes de `"com.itau"`, senão o Itaú
engoliria o iti); só se nada casar tenta a heurística de aposta por regex (`_RE_APOSTA`) sobre a
URL/domínio, rotulando com `_rotulo_servico` (de `"https://m.7games.bet/"` extrai `"m.7games.bet"`,
tira `www.` e query string). Cada evidência (app instalado, credencial, conta de app) vira uma
entrada em `_acumular_conta_derivada`, agrupada por `(instituição normalizada, titular normalizado)`
— sem isso o mesmo banco apareceria três vezes na tela, uma por modelo UFDR de origem.
`_consolidar_contas_derivadas` fecha o agrupamento: quando uma instituição tem **exatamente uma**
linha com titular (normalmente vinda da credencial, que traz usuário) e outras sem titular
(normalmente o app instalado, que nunca traz usuário), as sem-titular são **absorvidas** na
com-titular — o app do PicPay e a senha salva do PicPay do mesmo aparelho viram uma linha só, com
`NO_FONTE` = fontes concatenadas por `" + "` e `TP_MODELO` = modelos concatenados por `"+"`
(ambos truncados). Carteiras cripto trazem `NO_MOEDA` de `_ativos_financeiros`, que lê
`<multiModelField name='Assets'>` (modelos `FinancialAsset` aninhados) — é onde o CryptoTracer põe
o ativo, não num `<field>` simples, por isso um `_valor_campo` direto não o enxergaria.

### Modelo de dados

`db/init/18_credenciais_financeiro.sql`, tabela `BNC_TB01_CONTAS_FINANCEIRAS`:

| Coluna | Observação |
|---|---|
| `ID_CONTA_FINANCEIRA`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_FONTE` | origem da evidência (modelo Cellebrite, "Sicoob (app database)"...) |
| `NO_INSTITUICAO` | banco/carteira/plataforma |
| `NO_TITULAR` | titular quando o UFDR traz |
| `NU_CONTA`, `NU_AGENCIA` | número da conta/agência |
| `TP_CONTA` | banco, conta_corrente, pagamento, cripto, aposta, capitalizacao, credito... |
| `NO_MOEDA` | moeda ou ativo (cripto) |
| `VL_SALDO` | saldo declarado |
| `TP_MODELO` | modelo do UFDR de origem (FinancialAccount/BankAccount/AppDatabase...) |

Fontes de dedução em tabelas vizinhas: `APP_TB01_APLICATIVOS` (apps instalados),
`CTA_TB01_CONTAS_USUARIO` (`db/init/18_credenciais_financeiro.sql`) para credenciais/contas de app e o
catálogo de instituições da base de referência. Contador `INQ_TB02_EXTRACOES.QT_CONTAS_FINANCEIRAS`.

## Redes Wi-Fi conhecidas

### Visão geral

A tela `web/wifi.html` lista as **redes Wi-Fi** conhecidas/salvas pelo aparelho (elementos
`WirelessNetwork`/`NetworkActivity` do UFDR): SSID, BSSID, tipo de segurança, senha (quando
disponível), quantidade de conexões, primeira/última conexão, canal, sinal, rede oculta e fabricante.

O cruzamento com localizações e notificações ajuda a **estabelecer presença física**: a rede salva no
aparelho é indício do endereço em que o alvo esteve, mesmo sem GPS.

### Fluxo de uso

1. Na tela de Investigação, abrir **Wi-Fi** no bloco da extração.
2. A tela consulta o status da extração: se `QT_REDES_WIFI` for NULL (domínio nunca escaneado), mostra
   o estado vazio; caso contrário, lista as redes.
3. Busca (SSID/BSSID/fabricante/senha) e filtro de segurança, e exportação para Excel da listagem —
   **diferente de quase todas as outras telas deste documento, esses filtros rodam no cliente**
   (`aplicarFiltros` em `web/js/wifi.js`), não em parâmetros de query: a rota devolve todas as redes
   da extração de uma vez, sem paginação. O volume de redes Wi-Fi por aparelho é pequeno o bastante
   (dezenas a poucas centenas) para não justificar o custo de paginação no servidor.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/redes-wifi` | `extracao` | lista **todas** as redes (sem paginação nem filtro no servidor) |
| `GET /api/investigacao/extracoes/{id_extracao}/redes-wifi/status` | — | `{escaneado, qt_redes}`; `qt_redes` NULL = nunca escaneada |

Pontos-chave em `api/funcoes/wifi_utils.py`:

- `listar_redes_wifi` lê `WIFI_TB01_REDES` via `INQ_TB02_EXTRACOES` e ordena por
  `qt_conexoes DESC NULLS LAST, no_ssid NULLS LAST` — as redes mais usadas primeiro. Sem parâmetros
  de busca/paginação (ao contrário de `listar_ligacoes`/`listar_notificacoes`/etc.) — só filtra por
  `id_extracao` opcional.
- `status_wifi_extracao` devolve `{id_inquerito, qt_redes}`, com `qt_redes = None` quando a extração
  não trouxe/varreu redes (a tela distingue "não escaneada" de "zero redes").

### Modelo de dados

`db/init/17_aparelho_redes.sql`, tabela `WIFI_TB01_REDES`:

| Coluna | Observação |
|---|---|
| `ID_REDE_WIFI`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_SSID`, `NO_BSSID` | nome da rede e MAC do roteador |
| `TP_SEGURANCA`, `CO_SENHA` | segurança e senha (quando o UFDR traz) |
| `QT_CONEXOES` | nº de conexões registradas |
| `DT_PRIMEIRA_CONEXAO`, `DT_ULTIMA_CONEXAO` | período de uso |
| `NR_CANAL`, `NR_SINAL` | canal e sinal |
| `IN_REDE_OCULTA`, `TP_AUTENTICACAO` | rede oculta e autenticação |
| `NO_FABRICANTE`, `IN_FAVORITA`, `NO_FONTE` | fabricante, favorita e origem do dado |

Contador `INQ_TB02_EXTRACOES.QT_REDES_WIFI` (NULL = nunca escaneada).

## Inventário do filesystem do aparelho

### Visão geral

A tela `web/arquivos.html` mostra o **inventário do filesystem** do aparelho (elementos `<file>` do
`report.xml` do UFDR): câmera, downloads, mídia de apps e arquivos de sistema — não apenas anexos de
conversa. Uma extração chega a ter centenas de milhares de registros, então tudo é filtrado e
paginado no servidor.

A tela oferece navegação por pastas (trilha + subpastas, como um explorador), filtros por categoria,
status, tamanho, período e situação de OCR/classificação, um modal de mídia com metadados (EXIF com
GPS, incluindo HEIC do iPhone), download do binário original e os processamentos em lote:
**classificação de imagens** (CLIP local) e **OCR** (Tesseract local).

### Fluxo de uso

1. Na tela de Investigação, abrir **Arquivos** no bloco da extração.
2. Escolher a visão: listagem por categoria/status/tamanho/período + busca (nome, caminho ou texto de
   OCR) ou navegação por pastas (`?pasta=DCIM/Camera`, `''` = raiz — a árvore acompanha o recorte dos
   filtros).
3. Clicar numa imagem/vídeo: abre modal com prévia, painel de metadados (EXIF de imagem: dimensões,
   câmera, data da captura, GPS com link pro mapa; vídeo: GPS e data do metadado QuickTime) e, para
   imagens com OCR, o texto reconhecido ao lado. PDF abre no navegador; outros tipos baixam.
4. **Classificar imagens** e **Extrair texto (OCR)** rodam em background com progresso e
   cancelamento no modal; a etiqueta da classificação e o trecho de OCR aparecem nas linhas.

### Como funciona

Rotas em `api/rotas/investigacao.py`:

| Rota | Parâmetros | Comentário |
|---|---|---|
| `GET /api/investigacao/inqueritos/{id}/arquivos` | `extracao`, `categoria`, `busca`, `disponivel`, `status`, `tamanho_min`, `data_de`, `data_ate`, `periodo`, `ocr` (com/sem/pendente), `classificacao_ia` (com/sem/valor), `ordenar` (categoria/nome/maiores/menores/recentes/antigos), `pasta`, `pagina`, `limite` | listagem paginada |
| `GET /api/investigacao/inqueritos/{id}/arquivos/pastas` | mesmos filtros + `pasta` | subpastas imediatas com contagem e volume recursivos |
| `GET /api/investigacao/arquivos/{id}/metadados` | — | EXIF (imagem) com OCR do banco; QuickTime (vídeo) |
| `GET /api/investigacao/arquivos/{id}/conteudo` | `download`, `preview` | binário sob demanda do `.ufdr` |

Binários: nunca copiados para o banco — `obter_binario_arquivo` resolve o "Local Path" e o arquivo é
lido do ZIP de origem, com streaming em pedaços de 256 KB (`_stream_membro_zip`) para vídeos grandes
e reencodificação JPEG (`redimensionar_imagem`, máximo 1600 px — via Pillow/pillow-heif) para a
prévia de imagens, que é o que exibe HEIC do iPhone. A rota de metadados devolve `{}` para não-mídia.
`CO_CAMINHO_ZIP` nulo (sem Local Path — comum no iPhone, que tem centenas de milhares de `<file>` de
sistema) significa "só metadado": a linha aparece na listagem, mas não abre.

**"Ver na conversa"**: quando o mesmo binário do arquivo também foi enviado/recebido como anexo de
mensagem, a linha ganha um link direto para aquela conversa. `_SQL_CHAT_DO_ARQUIVO` casa
`ARQ_TB01.CO_CAMINHO_ZIP` (vem do "Local Path" do `<file>`) contra `WPP_TB05`/`SMS_TB05`/
`MSG_TB05.CO_CAMINHO_ZIP` (vem do casamento por tamanho feito na ingestão) **dentro da mesma
extração** — o caminho dentro do zip só é único dentro de um `.ufdr`, então dois aparelhos podem ter
`files/Image/1.jpg` sem ser o mesmo arquivo. Roda como `LATERAL` só sobre a página já paginada (não
o inquérito inteiro) e devolve `{fonte, id_mensagem, tp_app}` para montar o deep-link
`whatsapp.html?...&msg=ID` / `mensageiros.html?...&msg=ID`.

Lote (rotas dedicadas): `POST /api/arquivos/classificar-imagens` (+ `/progresso`, `/cancelar`,
`/reset`) e `POST /api/arquivos/ocr-imagens` (+ `/progresso`, `/cancelar`, `/reset`) em
`api/rotas/classificacao_imagem.py` e `api/rotas/ocr_imagem.py`; há ainda
`/api/arquivos/ocr-imagem-especifica`, `/ocr-imagens/resetar` e
`GET /api/arquivos/ocr-extracao/{id}/status`.

### Modelo de dados

`db/init/20_arquivos.sql`, tabela `ARQ_TB01_ARQUIVOS`:

| Coluna | Observação |
|---|---|
| `ID_ARQUIVO`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_ARQUIVO` | nome (último trecho do caminho; truncado em 2000) |
| `TX_CAMINHO` | caminho completo no aparelho |
| `NO_CATEGORIA` | Imagem/Vídeo/Áudio/Documento/Compactado/Outro — **pela extensão do nome**, não pelo campo `Tags` do UFDR (`_categoria_arquivo` em `ufdr_parser.py`; `Tags` vem inconsistente, quase sempre vazio no iPhone). Listas fechadas de extensão por categoria (`_EXTENSOES_IMAGEM` inclui `.heic`/`.heif`, `_EXTENSOES_COMPACTADO` é checada antes de "documento" para não classificar `.zip` como documento) |
| `TP_MIME` | MIME derivado da mesma extensão (`_MIME_POR_EXTENSAO`; `application/octet-stream` se desconhecida) — usado para servir/abrir |
| `QT_BYTES`, `DT_MODIFICACAO` | tamanho e data de modificação |
| `CD_STATUS_ARQUIVO` | Intact/Deleted (atributo `deleted`) |
| `CO_CAMINHO_ZIP` | "Local Path" no ZIP; NULL = só metadado (sem binário) |

Migrações posteriores: `20_arquivos.sql` adiciona `TX_OCR`/`DT_OCR` (DT_OCR NULL = não processado,
índices de trigrama e de pendentes IX77/IX78); `20_arquivos.sql` adiciona
`CLASSIFICACAO_IA`/`CONFIANCA_IA` (resultado do CLIP). Índices base: `IX71` (extração+categoria),
`IX72`/`IX73` (trigrama de nome e caminho). Contador `INQ_TB02_EXTRACOES.QT_ARQUIVOS`.

Heurísticas fixas no código: classificação processa imagens com binário no ZIP e acima de 51200
bytes/50 KB (`qt_bytes >= 51200`), **exceto `image/webp`** — figurinha do WhatsApp, sem valor pra
classificar, e um `.webp` também pode ser na verdade um ZIP de pacote de figurinhas com extensão
trocada; OCR processa imagens acima de 102400 bytes/100 KB (`_BYTES_MIN`, abaixo disso costuma ser
ícone/thumbnail de app sem texto útil, e infla a fila à toa) — mesma exclusão de `webp` pelo mesmo
motivo —, abre o ZIP uma vez por lote, reduz o lado maior a 2500 px (`_LADO_MAX`, acima disso o
Tesseract fica lento sem ganho de acerto) e marca `DT_OCR` ao terminar (impede reprocesso mesmo sem
texto reconhecido). Um job por vez no processo inteiro (409 se já tem um rodando) com *stale
detection* de 300 s/5 min (`_TIMEOUT_STALE`) — se o processo caiu no meio, o próximo pedido destrava
sozinho em vez de ficar preso pra sempre no "rodando".

Os anexos de notas são resolvidos contra esta tabela (ver [Notas](#notas-de-bloco-de-notas-do-aparelho)).

## Chaves PIX

### Visão geral

A detecção de chaves PIX varre as conversas da extração (WhatsApp, SMS/MMS e mensageiros diversos)
atrás de mensagens que citam "pix" ou "chave" e extrai a chave mencionada, classificada por tipo
(CPF, CNPJ, e-mail, telefone ou aleatória/EVP — formato UUID), com validação de formato e de dígito
verificador (CPF/CNPJ). Cada chave é **ligada ao contato** (remetente) e **à conversa** onde
apareceu — na mesma mensagem é gravada a referência para o sistema abrir o trecho original.

A detecção roda automaticamente ao final de cada extração (etapa *best-effort*, não derruba a
extração) e pode ser repetida pela tela de Chaves PIX ("Detectar chaves PIX" / "Re-detectar"). O
card da extração mostra a contagem e a tela lista as chaves com opções de filtro/exportação.

Implementação: `api/funcoes/pix_utils.py` (249 linhas, estratégia + extração + validação) e rotas em
`api/rotas/investigacao.py`; tela em `web/chaves_pix.html`.

### Fluxo de uso

1. Com uma extração concluída, abrir a tela Chaves PIX e escolher o inquérito (e a extração, opcional).
2. O sistema consulta o status da extração (`GET /api/investigacao/extracoes/{id}/chaves-pix/status`):
   se **nunca escaneada** mostra o botão "Detectar chaves PIX"; se já escaneada, mostra "Re-detectar"
   (e o resultado da última varredura). `QT_CHAVES_PIX` nulo na extração indica "nunca escaneada".
3. A varredura (`POST /api/investigacao/extracoes/{id}/chaves-pix/detectar`) é rápida — só mensagens
   que citam "pix"/"chave" — e idempotente: ela **re-popula** as chaves da extração, então repetir não
   duplica.
4. A tabela mostra a chave, o tipo, o contato, a conversa e a data; clicar na chave abre a mensagem
   original. Dá para exportar a tabela (botão de exportação `chaves-pix`).
5. A IA do caso também consulta chaves via ferramenta `buscar_chaves_pix` (com filtros por tipo/termo
   e opção de restringir a uma extração).

### Como funciona

1. **Pré-filtro SQL**: apenas mensagens cujo texto contenha `pix` ou `chave` (`ILIKE '%pix%' OR
   ILIKE '%chave%'`, case-insensitive) nas três fontes de conversa (`wpp`/`sms`/`msg`, resolvendo
   remetente e o outro lado via a mesma `string_agg` de participantes de `listar_conversas`) — evita
   rodar regex em todas as mensagens do inquérito.
2. **Extração (`extrair_chaves_pix`)**: para cada mensagem, um `bytearray` do tamanho do texto marca
   as **posições já ocupadas** e as chaves são tentadas nessa ordem de precedência: **UUID > e-mail >
   CNPJ > CPF > telefone** — cada regex só aceita um trecho se `livre()` disser que nenhuma posição
   dele já foi `marcar()`ada por um tipo de precedência maior (senão os 11 dígitos finais de um CNPJ
   virariam também um "CPF" solto). Regex exatas:
   - `_RE_UUID = \b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b` —
     chave aleatória (EVP); gravada com `tp_chave = "aleatoria"` (não "uuid" — a nomenclatura do
     BACEN para esse tipo de chave), em minúsculas;
   - `_RE_EMAIL = \b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`;
   - `_RE_CNPJ = \b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b` — aceita com ou sem pontuação; só vira
     achado se `_valida_cnpj` (os dois dígitos verificadores, pesos `[5,4,3,2,9,8,7,6,5,4,3,2]` e
     `[6]+pesos1`) bater e a sequência não for um único dígito repetido (`00000000000000` etc.);
   - `_RE_CPF = \b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b` — mesma ideia, `_valida_cpf` com pesos
     decrescentes a partir de 10/11; se o dígito verificador não bate, a posição continua **livre**
     e pode ainda virar telefone (número solto de 11 dígitos que falha como CPF, ex. errado de
     digitação, não é descartado à toa);
   - `_RE_TEL = (?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}` — `_telefone_normalizado`
     remove o `55` de país quando presente, exige 10 ou 11 dígitos restantes e DDD entre 11 e 99;
     resultado é só dígitos, sem `+55` (diferente do que uma leitura rápida do regex sugere).
3. **Gravação**: uma linha por **ocorrência (chave × mensagem)** — a mesma chave PIX citada em
   mensagens diferentes (ou por remetentes diferentes) gera uma linha por mensagem, porque cada
   ocorrência tem seu próprio trecho/conversa/data como evidência; só dentro da MESMA mensagem
   `(tipo, chave)` não se repete (dedup local em `extrair_chaves_pix`, não há `UNIQUE` na tabela).
   `_trecho` colapsa espaços/quebras e corta em até 300 caracteres; a referência `no_fonte` +
   `id_mensagem` alimenta o deep-link (`[msg:wpp:ID]`).
 4. **Ligação ao contato**: `remetente_dono` (`IN_REMETENTE_DONO`) vem de `IN_DONO_DISPOSITIVO` do
    contato remetente; `no_conversa` é `no_grupo` quando `tp_conversa='grupo'`, senão o `outro_lado`
    (participantes exceto o dono, mesma lógica de `listar_conversas`). `no_app`, `no_conversa` e
    `no_remetente` passam por `_cap()` antes do INSERT: as colunas são `varchar(3000)` e o
    `string_agg` do `outro_lado` estoura a largura em grupo grande — um único registro longo
    derrubava a varredura inteira (erro best-effort no fim da entrega).
5. **Idempotência**: `detectar_chaves_pix` primeiro `DELETE FROM pix_tb01_chaves WHERE id_extracao =
   %s`, depois insere de novo por fonte (`wpp`, `sms`, `msg`, nessa ordem) — rodar de novo recalcula
   tudo do zero, sem acumular duplicata nem deixar registro órfão de uma versão anterior do regex.
6. **Leitura em blocos**: a varredura usa um cursor **nomeado** (server-side) e `fetchmany`, em
   blocos de `_LOTE_LEITURA`. Era `fetchall()`: todas as mensagens da extração — com o corpo
   inteiro — na memória de uma vez, logo depois do passe do `report.xml` e com as outras extrações
   simultâneas fazendo o mesmo. O cursor comum do psycopg já traz o resultado inteiro para o
   cliente no `execute()`, então `fetchmany` sozinho não pouparia nada: ele só fatiaria o que já
   está em RAM. Com o cursor no servidor, cada `FETCH` traz um bloco de verdade e o pico fica
   limitado ao bloco — a detecção é linha a linha e nunca precisou de mais que isso.

### Modelo de dados

`db/init/18_credenciais_financeiro.sql`, tabela `PIX_TB01_CHAVES` (**sem** `UNIQUE` — o grão é por ocorrência):

| Coluna | Observação |
|---|---|
| `ID_CHAVE_PIX`, `ID_EXTRACAO` | PK e FK para `INQ_TB02_EXTRACOES` |
| `NO_FONTE` | `wpp`/`sms`/`msg` |
| `NO_APP` | mensageiro, quando `no_fonte='msg'` |
| `CO_CHAVE` | chave normalizada (só dígitos para CPF/CNPJ/telefone; e-mail e UUID em minúsculas) |
| `TP_CHAVE` | `cpf`/`cnpj`/`email`/`telefone`/`aleatoria` |
| `TX_CHAVE_ORIGINAL` | como apareceu no texto (até 160 caracteres) |
| `ID_MENSAGEM`, `ID_CONVERSA`, `NO_CONVERSA` | mensagem/conversa de origem (deep-link) |
| `ID_CONTATO_REMETENTE`, `NO_REMETENTE`, `IN_REMETENTE_DONO` | quem enviou a mensagem |
| `TX_TRECHO`, `DT_MENSAGEM` | evidência (até 300 caracteres) e data |

Índices `IX74` (extração) e `IX75` (trigrama de `CO_CHAVE`, `gin_trgm_ops` — `search_path` inclui
`public` na migração pelo mesmo motivo de `01_schemas.sql`).
`INQ_TB02_EXTRACOES.QT_CHAVES_PIX` — nulo = nunca escaneada; senão, quantas ocorrências foram
encontradas na última varredura.

## Dados de conta Google sem equivalente em UFDR

### Visão geral

Enquanto a extração de UFDR descreve o **aparelho**, os dados de conta descrevem a **conta na nuvem**.
Hoje quem os traz é a [Nuvem IPED](extracao_iped.md), que decompõe os produtos do Google que o IPED
indexou dentro da pasta-caso. O que tem equivalente no aparelho vai para as próprias tabelas do UFDR — Gmail
em `EML_TB01`, histórico do Chrome em `NAV_TB01`, contatos em `CTT_TB01`, apps em `APP_TB01`, Drive/
Google Photos/backups em `ARQ_TB01`, **Google Duo (`Duo/Call History.csv`) em `LIG_TB01`**,
**Google Maps/Saved/Timeline (`Maps/My labeled places`, `Saved/*.json`, `Timeline/*.json`) em
`LOC_TB01`**, **Chrome Autofill (`Chrome/Addresses and more.json`) e Google Keep (`Keep/*.json`) em
`NOT_TB01`** — e o que **não tem** equivalente ganha sete telas dedicadas, todas no padrão listagem
paginada + KPIs (`.../resumo`). Ver "Lacunas e cobertura conhecida" abaixo para o que ainda falta:

| Tela | Conteúdo |
|---|---|
| `web/atividades.html` | Minha Atividade: registros por produto (Image Search, YouTube, Chrome...), descrição e horário |
| `web/acessos.html` | Eventos de acesso à conta: IP (com proxy/não-routável), user-agent, produto e os identificadores vistos na sessão |
| `web/acessos_agregados.html` | Por identificador: dias ativos, multilogin e canal de autenticação |
| `web/conexoes.html` | Pares de identificadores vistos juntos numa sessão autenticada (IP ↔ cookie, Android ID ↔ OAuth login...) |
| `web/dispositivos_conta.html` | Dispositivos conhecidos pela conta (modelo, fabricante, Android ID, IMEI...) |
| `web/perfil_conta.html` | Perfil da conta em cartões (nome, e-mails, criação, idioma, país, status) |
| `web/agenda.html` | Eventos de calendário (título, local, descrição, organizador, participantes) |

As tabelas e rotas são **genéricas** — pensadas para servir também um futuro import de nuvem
Microsoft/Apple, bastando gravar nas mesmas tabelas com outro `NO_FONTE`.

### Fluxo de uso

1. Colocar a pasta-caso do IPED em `data/` e importar pela aba **Nuvem IPED** da tela de
   Investigação — uma extração por caso, identificada pelo e-mail da conta (bloco no card, com
   fases, progresso e cancelamento).
2. Abrir cada tela pelos botões do card da extração (só aparecem os produtos que aquele caso
   trouxe) ou pelo menu lateral; sem `extracao` na URL, a tela cruza todas as contas do inquérito.
3. Filtrar (busca, produto, datas, período do dia, ordenação) e usar os KPIs do topo, que respeitam
   os mesmos filtros da listagem. Nas conexões, cada linha liga os dois identificadores
   vistos juntos, com primeira/última ocorrência.

### Como funciona

Rotas em `api/rotas/investigacao.py`, sempre com os parâmetros comuns `extracao`, `busca`, `data_de`,
`data_ate`, `periodo` (madrugada/manha/tarde/noite, em horário local), `pagina`/`limite`, e os
específicos de cada tela (`produto`, `tipo`, `ordenar`):

| Rota | Detalhes |
|---|---|
| `GET .../inqueritos/{id}/atividades` (+ `/filtros`, `/resumo`) | `produto` exato; `ordenar` recentes/antigos |
| `GET .../inqueritos/{id}/acessos` (+ `/filtros`, `/resumo`) | busca varre IP/user-agent/produto/identificadores |
| `GET .../inqueritos/{id}/acessos-agregados` (+ `/filtros`, `/resumo`) | `tipo` de identificador; datas sobre a última atividade; `ordenar` recentes/dias_ativos |
| `GET .../inqueritos/{id}/conexoes` (+ `/resumo`) | datas sobre a última ocorrência |
| `GET .../inqueritos/{id}/dispositivos-conta` (+ `/resumo`) | busca por nome/fabricante/Android ID/IMEI |
| `GET .../inqueritos/{id}/perfil-conta` | normalmente 0–1 registro por conta |
| `GET .../inqueritos/{id}/agenda` (+ `/resumo`) | datas sobre o início; `ordenar` recentes/antigos (padrão antigos) |

Tudo em `api/funcoes/contas_nuvem_utils.py`, seguindo o padrão de
`lista_paginas_visitadas`/`resumo_navegacao` de `investigacao_utils.py` (filtros `_filtro_periodo_data`,
paginação com `LIMITE_MAX_LISTAGEM`, KPIs `resumo_*`). A importação em si fica em
`api/funcoes/iped_utils.py` (`_rotear_produto_google`), e os parsers de conteúdo em
`api/funcoes/takeout_parsers.py`.

**Roteamento por caminho interno** (`_ROTEIRO` em `takeout_parsers.py`, uma lista ordenada de `(prefixo_de_pasta,
sufixo_no_nome, tabela, fase)`; `_destino(nome)` percorre em ordem e usa o primeiro que casar — a
ordem importa quando um arquivo poderia casar duas regras — o caminho testado é o que sobra depois
do `>>` no caminho do item do IPED, que preserva o caminho original de dentro do ZIP do Takeout):
descobrir esse roteamento por tentativa
e erro tem histórico de bugs silenciosos, porque um nome que não casa nenhuma regra é **descartado
sem erro nenhum** (o Takeout não é um formato versionado/documentado oficialmente — cada exportação
observada é que dita a regra). Casos conhecidos, corrigidos numa auditoria que comparou o código
contra ZIPs reais de 8 contas (na época em que a importação lia os ZIPs direto):
- `"Chrome/Addresses and more.json"` caía na regra genérica de `Chrome/*.json` (histórico de
  navegação), que não reconhece a chave `"Autofill"` do JSON e produzia 0 linhas — por isso a regra
  de Autofill precisa vir **antes** da regra genérica de `Chrome/` no `_ROTEIRO` (comentário no
  código avisa disso). O conteúdo (nome, CPF, endereço, telefone, data de nascimento, e até valores
  de senha/PIN digitados em formulário) vira nota em `NOT_TB01` — achado relevante para identificação
  de pessoas.
- `"Google Play Store/Install History.csv"` e `"User Activity.html"` — o roteiro antigo tinha os
  sufixos sem espaço (`"installhistory"`), mas os nomes reais do Takeout têm espaço
  (`"install history"`/`"user activity"`); os dois produtos eram 100% perdidos até a correção.
- **Coordenadas negativas vindas como inteiro unsigned de 32 bits**: `Maps`/`Saved` trazem
  `latE7`/`lngE7` (grau × 10⁷); `_e7_para_grau` primeiro tenta o valor direto, mas quando ele vem
  maior que 2³¹ (ex. `4091633210`) subtrai `2**32` antes de dividir — sem essa correção uma
  localização real virava algo como "409° de latitude", visivelmente absurdo mas fácil de não notar
  numa tabela com milhares de linhas.

**Lacunas e cobertura conhecida** (decisão consciente, não bug — registrado para a próxima auditoria
não perder tempo redescobrindo o mesmo):
- **`GoogleChat/` (Messages/GroupInfo/GroupTasks)** — sem parser. Na amostra auditada (8 contas
  reais) só havia `UserInfo`, sem conteúdo de mensagem — mas isso pode não valer para todo caso.
- **`Duo/ContactInfo`, `DuoCustomerInfo`, `Groups`, `Blocked`** — têm dados reais (agenda
  sincronizada com nomes) mas só `Duo/Call History.csv` foi ligado a `LIG_TB01`; os contatos do Duo
  ficaram de fora.
- **`Timeline/*.json` (antigo "Histórico de localização")** — `parse_timeline_json` foi escrito por
  melhor esforço a partir do formato documentado do Takeout (cobre `semanticSegments`/`rawSignals`
  do Timeline on-device, com coordenadas em `"geo:lat,lng"`), mas **nunca foi validado contra dados
  reais** — as 8 contas da amostra original vieram todas `NoRecords`, e o caso IPED de validação só
  tinha `Timeline/Settings.json` (preferências, sem trajeto). Conferir com atenção no primeiro caso
  real que tiver histórico de localização do Timeline.
- **`My Activity/Deletion Markers/{PRIMARY,SECONDARY}.csv`** — ignorado **de propósito**
  (`_IGNORAR_PADRAO` inclui `"Deletion Markers"`), até 13,6 MB por conta. Potencialmente relevante
  (rastro do que o usuário tentou apagar), mas o schema desse CSV nunca foi inspecionado.
- Não cobertos: `GoogleMeet/MeetingHistory`, a família `YoutubeAndYoutubeMusic/*`, e o resto do
  Google Play Store (`Devices/IntegratedBilling/Loyalty/OrderHistory/PromotionHistory/
  PurchaseHistory/RefundRecords/Subscription/UserPreferences/UserReviews`).

### Modelo de dados

`db/init/29_nuvem.sql` — tabelas genéricas, todas com `ID_EXTRACAO` (FK para
`INQ_TB02_EXTRACOES`) e `NO_FONTE` rotulando a origem (ex. "Google (MyActivity)"):

| Tabela | Conteúdo |
|---|---|
| `ATV_TB01_ATIVIDADES` | `DT_ATIVIDADE`, `TX_PRODUTO`, `TX_DESCRICAO`, `TX_URL` |
| `ACS_TB01_ACESSOS` | `DT_ACESSO`, `TX_IP`, `TX_IP_PROXY`, `IN_IP_NAO_ROTEAVEL`, `TX_USER_AGENT`, `TX_PRODUTO`/`TX_SUBPRODUTO`, `TP_ACESSO`, `TX_CANAL` (ex. Gmail Access Channel), `TX_ANDROID_ID`, `TX_IOS_ID`, `TX_OUTRO_DISPOSITIVO`, `TX_IMAP_ID`, `TX_ACTIVESYNC_ID`, `TX_RESOLUCAO`, `TX_OAUTH_LOGIN_ID`, `TX_STATEFUL_TOKEN_ID`, `TX_COOKIE_AUTH_TIME` |
| `ACS_TB02_AGREGADOS` | `DT_PRIMEIRA`/`DT_ULTIMA`, `QT_DIAS_ATIVOS`, `TP_IDENTIFICADOR`/`TX_IDENTIFICADOR`, `IN_MULTILOGIN`, `TP_CANAL_AUTENTICACAO` |
| `ACS_TB03_CONEXOES` | `DT_PRIMEIRA`/`DT_ULTIMA`, pares `TP_IDENT_1`/`TX_IDENT_1` ↔ `TP_IDENT_2`/`TX_IDENT_2` |
| `DVC_TB01_DISPOSITIVOS` | `NO_DISPOSITIVO`, `TX_FABRICANTE`, `TX_ANDROID_ID`, `TX_IMEI`, `TX_VERSAO`, `TX_SERIAL`, `TX_OUTROS` (JSON), `TP_ORIGEM` |
| `PRF_TB01_PERFIL` | `NO_NOME`, `NO_NOME_DADO`/`NO_NOME_FAMILIA`, `TX_EMAIL`, `TX_EMAIL_ALTERNATIVOS`, `DT_CRIACAO`, `TX_IP_TOS`, `TX_IDIOMA`, `TX_PAIS`, `TX_NASCIMENTO`, `TX_SERVICOS`, `DT_ULTIMA_ATUALIZACAO`/`DT_EXCLUSAO`, `TX_STATUS` |
| `EVT_TB01_EVENTOS` | `DT_INICIO`/`DT_FIM`/`DT_CRIACAO`/`DT_ALTERACAO`, `TX_TITULO`, `TX_LOCAL`, `TX_DESCRICAO`, `TX_ORGANIZADOR`, `TX_PARTICIPANTES` (um por linha) |

Índices `IX134..141` (extração + data). O bookkeeping da importação (status, fase, progresso e os
contadores por produto) fica em `IPD_TB01_EXTRACOES` (`db/init/19_iped.sql` e ).


## WhatsApp parcial (a mídia do WhatsApp reconstruída como conversa)

### Visão geral

Dois pacotes de **nuvem** trazem mídia do WhatsApp **sem o texto das mensagens** — e a tela
`web/whatsapp_parcial.html` renasce com o que existe:

- **iCloud** — o banco de conversas vem criptografado (`ChatStorage.sqlite.enc`), mas a mídia
  **não**: vem organizada por JID dentro dos tars (`/Media/<jid>/<dir>/<uuid>.<ext>`), e o `mtime`
  do tar carrega a data/hora da mensagem original. A "conversa" de cada contato/grupo renasce em
  linha do tempo, uma entrada por JID.
- **Google Drive** (backup do Takeout lido pelo IPED) — os arquivos moram em
  `Drive Mobile Backups/wa_<numero>/` com o **nome canônico** do WhatsApp
  (`IMG-AAAAMMDD-WAnnnn.jpg`), que carrega a data da mensagem. Aqui o pacote **não** tem o contato
  de cada mídia (o banco `.crypt14` é criptografado e o metadado do Drive só tem md5/tamanho/data),
  então a reconstrução é **uma única conversa da CONTA**, datada pelo nome do arquivo — a data de
  sincronização do Drive (`dt_modificacao` do índice) é deixada de propósito fora da tela porque
  embaralharia a linha do tempo.

Só faz sentido em **extração de nuvem** (`INQ_TB02_EXTRACOES.IN_NUVEM`): no aparelho a mídia vem
junto do texto no próprio WhatsApp.

### Fluxo de uso

1. Na tela de Investigação, o card de uma extração de nuvem com mídia do WhatsApp mostra o botão
  **WhatsApp (parcial)** (`tem_wa_parcial` no resumo da extração) — mesmo ícone do WhatsApp.
2. A lista mostra uma "conversa" por contato/grupo (iCloud) ou a conversa da conta (Drive), com
  nome resolvido na agenda, tipo (individual/grupo/status), contagem total, por categoria e o
  período coberto.
3. Abrindo a conversa: janela cronológica paginada **no servidor** (`?offset/?limite`), filtro por
  `?tipo=audio|imagem|video|documento`; as mídias abrem pelo mesmo mecanismo de anexo sob demanda
  do `.ufdr` (cache em memória).

### Como funciona

- `api/funcoes/whatsapp_parcial_utils.py` (rotas em `api/rotas/investigacao.py`:
  `GET .../whatsapp-parcial` e `.../whatsapp-parcial/midias`):
  - `_arquivos_whatsapp` varre `ARQ_TB01` da extração em busca dos dois formatos — caminho interno
    `files/Archives/Media*::<tar>` com `/Media/<jid>@...` (iCloud) ou `Drive Mobile Backups/wa_`
    no caminho (Drive; o parser dos nomes é `api/funcoes/iped_wa_drive.py`). Sem `CO_CAMINHO_ZIP`
    não é mídia de dentro de tar.
  - **Drive**: a data exibida vem de `nome_midia()` (nome canônico), não do índice; o JID sintético
    é `<numero-da-conta>@s.whatsapp.net`.
  - **Nomes**: `_mapa_nomes_contatos` casa o número do JID com a agenda da extração
    (`CTT_TB01/02`, entrada `phonenumber`), tolerando DDI 55 e o 9 do celular por sufixo.
  - `resolver_extracao_nuvem` escolhe a extração de nuvem do inquérito quando o front não passa
    `?extracao=`; sem nuvem, a rota responde 404 e o botão nem aparece.

### Modelo de dados

Nenhuma tabela nova — a tela é uma **leitura** de `ARQ_TB01_ARQUIVOS` (colunas `TX_CAMINHO`,
`CO_CAMINHO_ZIP`, `TP_MIME`, `DT_MODIFICACAO`, `NO_CATEGORIA`) da extração de nuvem, com nomes da
agenda (`CTT_TB01_CONTATOS` + `CTT_TB02_CONTATO_ENTRADAS`). As mídias em si continuam dentro dos
tars no `.ufdr`, lidas sob demanda (ver o cache de conteúdo em `extração_ufdr.md`/`extracao_iped.md`).

## Configuração

A maior parte das telas deste documento não tem configuração própria. As exceções, todas no `.env`:

| Variável | Padrão | Efeito |
|---|---|---|
| `CLIP_NUM_THREADS` | — | threads do torch por classificação facial/de imagem |
| `OCR_IDIOMA` | `por+eng` | idiomas do Tesseract |
| `OCR_PSM` | `3` | modo de segmentação do Tesseract |
| `JOBS_PESADOS_LIMITE` | `2` | quantos jobs pesados (OCR/classificação/facial) rodam juntos |
| `TRANSCRICAO_THREADS` / `TRANSCRICAO_MODELO` | — | transcrição de áudio dos anexos (ver `transcricao_audio.md`); o modelo usado fica em `INQ_TB02_EXTRACOES.TX_MODELO_TRANSCRICAO` |

A geocodificação (Localizações) não tem variável de ambiente: os limites (intervalo de 1,1 s, teto de
25 coordenadas por chamada) são constantes no código, cuidando de respeitar a política de uso do
serviço público do OpenStreetMap.

## Referências

- Telas: `web/whatsapp.html`, `web/mensageiros.html`, `web/contatos.html`, `web/ligacoes.html`,
  `web/localizacoes.html`, `web/timeline.html`, `web/notas.html`, `web/navegacao.html`,
  `web/aplicativos.html`, `web/notificacoes.html`, `web/emails.html`,
  `web/contas_financeiras.html`, `web/wifi.html`, `web/arquivos.html`, `web/chaves_pix.html`,
  `web/atividades.html`, `web/acessos.html`, `web/acessos_agregados.html`, `web/conexoes.html`,
  `web/dispositivos_conta.html`, `web/perfil_conta.html`, `web/agenda.html`,
  `web/whatsapp_parcial.html`
- Pontos de entrada: `api/rotas/investigacao.py`, `api/rotas/referencia.py`,
  `api/rotas/classificacao_imagem.py`, `api/rotas/ocr_imagem.py`,
  `api/funcoes/investigacao_utils.py`, `api/funcoes/iped_utils.py`, `api/funcoes/iped_cache.py`,
  `api/funcoes/takeout_parsers.py`, `api/funcoes/contas_nuvem_utils.py`,
  `api/funcoes/ufdr_parser.py`, `api/funcoes/ufdr_cache.py`,
  `api/funcoes/geocodificacao_utils.py`, `api/funcoes/wifi_utils.py`,
  `api/funcoes/pix_utils.py`, `api/funcoes/jobs_pesados.py`,
  `api/funcoes/classificacao_imagem_utils.py`, `api/funcoes/ocr_utils.py`
- Arquivos de schema envolvidos: `10_whatsapp.sql`, `11_sms.sql`, `16_navegacao_apps.sql`
  `15_localizacao.sql`, `10_whatsapp.sql`, `20_arquivos.sql`,
  `18_credenciais_financeiro.sql`
  `17_aparelho_redes.sql`, `20_arquivos.sql`, `29_nuvem.sql`, `19_iped.sql`,
- Documentos relacionados: `extração_ufdr.md` (origem dos dados), `extracao_iped.md` (segunda fonte
  das mesmas tabelas), `classificacao_imagem.md` e `ocr_imagem.md` (processamentos em lote),
  `transcricao_audio.md`, `mesa_inquerito.md` (entidades e identificação de pessoas),
  `exportar_excel.md` (exportação das listagens), `permissoes_delegacia.md` (isolamento por
  inquérito), `banco_dados.md` (convenção de nomes)
