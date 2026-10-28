# Relatório final em Word e marcações

## Visão geral

Geração do relatório final da investigação em **Word (.docx)** a partir de um **padrão de
relatório** (`REL_TB01_PADROES`): uma sequência de **blocos** (`REL_TB02_BLOCOS`) — trechos de
texto com variáveis `{{...}}` e blocos que reúnem os registros **marcados para o relatório**
(`REL_TB03_MARCACOES`, o mesmo modelo `TP_ORIGEM`/`ID_ORIGEM` sem FK das entidades) ou que
consultam direto as tabelas do RIF/extrato bancário do inquérito. Há **quatro modelos
pré-carregados**, todos editáveis pelo admin: "relatório de análise - tabela simples" (padrão da Polícia
Federal, conversas em tabela), "relatório de análise - whatsapp" (mesmo modelo,
mas as conversas saem como bolhas de mensagem — a "tela do celular"), "Relatório de dados
bancários" (extrato SIMBA) e "Relatório de RIF" (COAF/SISCOAF). A interface é
`web/padroes_relatorio.html`; a geração em `api/funcoes/relatorios_docx.py` (python-docx).

## Fluxo de uso

1. **Marcar registros** nas telas de dados (mensagens, conversas de WhatsApp, RIF, extrato
   bancário, arquivos do aparelho etc.) — "marcar para o relatório" grava `REL_TB03_MARCACOES`
   com o registro de origem e um comentário opcional. A segmentação é feita por **marcadores
   nomeados** ("TRÁFICO DE DROGAS"), pessoais de cada investigador — ver
   [marcadores.md](marcadores.md); `CO_COR` continua na tabela como *fallback* dos casos
   anteriores a eles. **Uma marcação por registro**: marcar de novo o que já
   está marcado atualiza a marcação existente (a tela avisa antes), nunca cria uma segunda —
   ver "Marcação única" abaixo. RIF e extrato bancário **não** dependem de marcação: os
   blocos `rif_*`/`bancario_*` trazem tudo o que estiver importado no inquérito (ou o
   subconjunto de arquivos escolhido na tela de geração), como as próprias abas de `rif.html`/
   `simba.html`.
2. Em `web/padroes_relatorio.html` (admin): listar/editar padrões (`modal-padrao`, nome ex.:
   "relatório de análise - tabela simples"); editar blocos (`modal-blocos`, `variaveis-ajuda` lista as
   variáveis disponíveis); reordenar com `mover_bloco`; ativar/desativar (`definir_ativo`).
3. Gerar o relatório no inquérito (`GET .../relatorios/{id_padrao}/preparar` monta o
   formulário): informar `{{unidade_policial}}` e os demais tokens manuais do padrão
   (`{{numero_inquerito}}`, `{{data_geracao}}` e as variáveis automáticas da tabela abaixo são
   preenchidas sozinhas); campos livres não preenchidos viram `{{token}}` literal no `.docx`,
   para o autor notar o que faltou (não viram "____" silenciosamente).
   - **Seções deste relatório**: a lista dos blocos do padrão, todos marcados. Desmarcar tira a
     seção **desta** geração, sem mexer no padrão (que é global e só o admin edita — antes, para
     tirar uma seção de um caso era preciso alterar o modelo que todos os outros casos usam).
     Os tokens manuais continuam sendo pedidos com base no padrão inteiro: recalcular a cada
     clique custaria uma ida ao servidor, e um campo a mais preenchido é inofensivo.
   - Se o padrão tiver bloco `marcacoes`/`whatsapp`/`chat` (e **não** tiver bloco de RIF/
     bancário/IPL), a tela mostra a lista de marcações do inquérito para escolher quais entram,
     com **busca** (arquivo, aplicativo, marcador, conteúdo) e **filtros por marcador e criador**
     (o de criador nasce no usuário logado, por decisão de produto). Filtrar
     só esconde a linha — nunca desmarca —, e por isso o rótulo mostra sempre "N de M
     selecionados": um registro marcado fora do filtro não pode entrar no relatório sem que
     ninguém veja. "Selecionar todos" age só sobre as linhas visíveis. Filtro sem resultado mostra
     estado vazio + "Limpar filtros" + contador "X de Y grupos visíveis".
   - Se tiver bloco `rif_*`, mostra os RIFs importados para escolher (todos por padrão).
   - Se tiver bloco `bancario_*`, mostra os extratos **concluídos** para escolher.
   - Se tiver bloco `ipl_*`, mostra os inquéritos em PDF importados para escolher.
   - **Resumo da seleção** no rodapé do modal (`Seções x/y · Marcações n/m · RIF a/b · … · ⚠ variáveis vazias`),
     com confirmação antes de gerar com variável manual vazia (sai `{{token}}` literal) ou com zero
     marcações.
   - **Seleção persistida** em `localStorage` por (inquérito, padrão) — reabrir o modal restaura
     blocos/arquivos/marcações/valores/flags de IA; "Marcar tudo de novo" limpa o salvamento.
   - **Últimos relatórios**: `GET .../geracoes-relatorio/recentes` lista as 5 últimas gerações do
     usuário no inquérito, com Baixar (só se o `.docx` ainda existe) e Acompanhar (job ativo). Após
     concluir, o modal oferece "Baixar novamente" caso o download automático tenha sido bloqueado.
     `409` (já há geração ativa) orienta a retomar pelo histórico em vez de erro seco.
   - Duas opções de IA, independentes, lado a lado — cada uma só aparece quando o padrão tem
     bloco em que ela mexe:
     - **"Resumo das conversas por IA"** (blocos `chat`/`whatsapp`): resume cada grupo de
       mensagens marcadas (um resumo por **marcador** presente na conversa).
     - **"Reescrever comentários do analista por IA"** (blocos `chat`/`marcacoes`, os que
       imprimem o comentário): passa o texto de cada marcação pela IA, que só corrige e
       completa a frase — sem acrescentar fato. Chamada que falha devolve o texto original.
4. A geração roda **em segundo plano** (ver "Geração em background" abaixo): a tela mostra o
   andamento bloco a bloco e o download começa sozinho ao final.
5. Abrir o `.docx` gerado: cabeçalho (brasão + "SERVIÇO PÚBLICO FEDERAL" / "MJSP - POLÍCIA
    FEDERAL") e rodapé (nº do procedimento + número de página) em todas as páginas,
   **sumário** (campo TOC do Word, "clique com o botão direito → Atualizar campo" para gerar) e
   seções por tipo de bloco. O nome do arquivo baixado é `relatorio_<inquérito>.docx`, ou
   `relatorio_rif_<inquérito>.docx` / `relatorio_ipl_<inquérito>.docx` quando o padrão tem bloco
   `rif_comunicacoes` / `ipl_pessoas`.

## Como funciona

### Padrões, blocos e tipos de bloco (`relatorios_utils.py`)

- Padrões e blocos são **globais** (rotas de escrita exigem admin — `exigir_admin`); as rotas
  de geração/marcação de um caso checam acesso ao inquérito
  (`exigir_acesso_inquerito`/`usuario_tem_acesso_inquerito`).
- `TIPOS_BLOCO_VALIDOS` (precisa bater com o `CHECK CK03_REL_TB02_TP_BLOCO`, mantido em
  sincronia por cada migração que acrescenta um tipo):

  | Grupo | Tipos | Conteúdo montado por |
  |---|---|---|
  | Texto livre do autor | `capa`, `sumario`, `texto`, `tabela_campos` | o próprio admin, com `{{variáveis}}` |
  | Registros marcados | `marcacoes` (tabela genérica agrupada por tipo de origem), `whatsapp` (tabela de conversa), `chat` (bolhas de mensagem, WhatsApp+SMS+outros mensageiros), `contas`, `extracoes` | `listar_marcacoes_inquerito`/`listar_whatsapp_marcado`/`listar_chat_marcado`/`listar_contas_inquerito`/`listar_extracoes_inquerito` |
  | Extrato bancário (SIMBA) | `bancario_contas`, `bancario_contrapartes`, `bancario_alertas` | `listar_bancario_*`/`resumo_bancario` — só extratos `concluido` |
  | RIF (SISCOAF) | `rif_resumo`, `rif_comunicacoes`, `rif_contrapartes`, `rif_envolvidos`, `rif_ocorrencias` | `listar_rif_*`/`resumo_rif` |
  | Inquérito em PDF (ePol) | `ipl_resumo`, `ipl_pecas`, `ipl_pessoas`, `ipl_achados` | `listar_ipl_*` — filtram por inquérito (as de `ipl_utils.py` filtram por `ID_IPL`, que é o que a página precisa) |
  | Chaves PIX | `pix_chaves` | `listar_pix_chaves` — **consolidado por chave**, não por menção |
  | Nuvem e IPED | `nuvem_perfil`, `nuvem_dispositivos`, `nuvem_acessos`, `iped_extracoes` | `listar_nuvem_*`/`listar_iped_extracoes` |

  Migrações, na ordem em que ampliaram o `CHECK`: `30_relatorios.sql` (conjunto original), (+`bancario_*`) (+`rif_*`) (+`chat`) (+`ipl_*`, `pix_chaves`
  `nuvem_*`, `iped_extracoes`).
- `ROTULO_TP_BLOCO` (`relatorios_utils.py`) é o rótulo curto de cada tipo, usado no servidor em
  dois lugares: a etapa que a geração em background informa ao polling e a lista de blocos que a
  tela de geração oferece para marcar/desmarcar (`preparar` devolve `rotulo_tipo` pronto, para a
  tela não manter uma segunda cópia do mapa). O construtor de padrões tem o seu próprio, junto
  com o texto de ajuda de cada tipo (`TP_BLOCO_CONFIG` em `padroes_relatorio.js`).
- `criar_bloco`/`atualizar_bloco` validam o tipo contra `TIPOS_BLOCO_VALIDOS`
  (`_validar_tipo_bloco`); `mover_bloco` troca `NU_ORDEM` com o vizinho acima/abaixo (`False`
  se o bloco já é o primeiro/último naquele sentido).
- `_TOKEN_RE = \{\{\s*([a-zA-Z0-9_]+)\s*\}\}` extrai os tokens usados no título/conteúdo de
  cada bloco; `tokens_manuais` devolve os que **não** estão em `CATALOGO_VARIAVEIS_AUTO`
  (ordenados, para a tela de geração pedir só o que falta preencher).

### Variáveis automáticas (`variaveis_auto`)

Calculadas por inquérito a cada geração (sem cache — refletem o estado atual do banco):

| Token | Origem | Quando fica em "—" |
|---|---|---|
| `numero_inquerito`, `data_geracao`, `data_hora_geracao`, `investigador` | `INQ_TB01`, relógio, usuário logado | nunca |
| `dispositivos` | `DISTINCT no_dispositivo` de `INQ_TB02_EXTRACOES` | sem extração de UFDR |
| `total_marcacoes` | `count(*)` de `REL_TB03_MARCACOES` do inquérito | nunca (fica "0") |
| `bancario_contas`/`bancario_titulares`/`bancario_credito`/`bancario_debito`/`bancario_periodo` | `SMB_TB01/02_*` **só `cd_status='concluido'`** | sem extrato concluído — um em andamento tem total parcial, e número parcial num relatório assinado é pior que a seção vir vazia |
| `rif_quantidade`/`rif_numeros`/`rif_comunicacoes`/`rif_envolvidos`/`rif_ocorrencias`/`rif_credito_total`/`rif_debito_total` | `RIF_TB01/02_*` do inquérito | sem RIF importado |
| `ipl_quantidade`/`ipl_numeros`/`ipl_folhas`/`ipl_pecas`/`ipl_pessoas`/`ipl_achados` | `IPL_TB01_INQUERITOS_PDF` (os contadores já vêm somados na linha do IPL) | sem inquérito em PDF importado |

`formatar_reais`/`formatar_documento`/`formatar_data`/`formatar_periodo`/`formatar_bytes` são
os helpers de apresentação pt-BR reaproveitados em todos os blocos (documento com 11 dígitos
vira CPF pontuado, 14 vira CNPJ; qualquer outro tamanho sai só com os dígitos).

### Marcação "marcar para o relatório" e resolução em texto legível

`listar_marcacoes_inquerito` resolve **cada** `TP_ORIGEM`/`ID_ORIGEM` num trecho legível com
um único `JOIN COALESCE` gigante — a mesma técnica de `listar_entidades_inquerito`
(`mesa_inquerito.md`) — cobrindo mensagens (WhatsApp/SMS/demais mensageiros), contatos,
ligação, localização (endereço da geocodificação reversa ou coordenadas —
`_conteudo_localizacao`), notificação, nota, navegação, e-mail, Wi-Fi, conta financeira,
aplicativo, dono do aparelho, **RIF** (`rif_comunicacao`/`rif_envolvido`/`rif_contraparte`,
saem sem extração associada — não vêm de UFDR), **SIMBA** (`simba_conta`/`simba_lancamento`/
`simba_contraparte`), **IPL** (`ipl_pessoa`/`ipl_achado`) e **arquivo do aparelho**
(`_conteudo_arquivo`: nome + caminho completo — o caminho é o que localiza a mídia na perícia,
DCIM/Camera, pasta do WhatsApp etc.). `no_aplicativo` rotula a origem (WhatsApp/SMS/RIF
(SISCOAF)/Extrato bancário (SIMBA)/...) para o agrupamento do bloco `marcacoes`.

`anexos_de_marcacoes` traz, para as marcações que são mensagem ou arquivo do aparelho, **onde
o binário está** (nome, MIME, caminho dentro de qual `.ufdr`) e o **MD5** dele — sem ler o
conteúdo ainda; a leitura acontece sob demanda na montagem do `.docx` (ver abaixo), igual à
prévia do chat.

### Marcação única por registro

Índice `UX95_REL_TB03_REGISTRO` (`30_relatorios.sql`) sobre `ID_INQUERITO, TP_ORIGEM,
ID_ORIGEM`: o mesmo registro não pode estar marcado duas vezes no mesmo inquérito. Antes dele
cada `INSERT` repetido virava uma linha/bolha a mais no `.docx` — o mesmo trecho saindo
duplicado num relatório assinado, sem que o autor tivesse como perceber (quem marcou uma
mensagem semanas atrás não lembra disso ao passar de novo pela conversa).

- `criar_marcacao` é um **upsert** (`ON CONFLICT ... DO UPDATE`) e devolve
  `(id_marcacao, ja_estava_marcado)`; a rota `POST /api/investigacao/marcacoes` repassa isso
  como `ja_existia`. Na atualização a cor nova substitui a antiga (escolha explícita do usuário
  agora) e o comentário novo é **acrescentado numa nova linha**, a menos que já esteja lá —
  comentário é registro de trabalho do analista, não se descarta em silêncio.
- Na tela (`js/utils/entidades.js`): o modal individual avisa "este registro JÁ ESTÁ marcado", troca
  o botão para "Atualizar marcação" e pré-seleciona a cor atual; o modal de lote diz quantos
  dos selecionados já estão marcados (pelo cache que destaca os botões 🗒️), **antes** de salvar.
- `deduplicar_por_registro` (`relatorios_utils.py`) repete a garantia na geração —
  `listar_marcacoes_inquerito`, `listar_whatsapp_marcado` e `listar_chat_marcado` passam por
  ela. É proposital: num banco onde a migração ainda não foi aplicada à mão (ver
  `banco_dados.md`), o relatório continua saindo sem duplicata.

### Geração do `.docx` (`relatorios_docx.py`)

`gerar_relatorio_docx` busca **só o que os blocos desta geração usam** — a query de alertas
bancários (`listar_bancario_alertas`) varre os lançamentos e é cara, e a resolução das marcações
é o `JOIN COALESCE` que cobre todas as tabelas de origem; um relatório sem essas seções não paga
por elas. O filtro de `ids_blocos` (as seções desmarcadas na tela) é aplicado **antes** do cálculo
de `tipos`, para que um bloco desmarcado também não pague pela consulta dele.
`_preparar_documento` monta o cabeçalho (brasão + "SERVIÇO PÚBLICO FEDERAL" / "MJSP - POLÍCIA
FEDERAL"); `_montar_rodape` grava o rodapé com campo `PAGE` do Word.

**Renderizador por tipo de bloco** (`_RENDER` para os tipos de texto livre + um `elif` por
tipo montado; tipo desconhecido cai em `_bloco_texto`):
- **`capa`**: linhas com `|` viram "Chave: Valor" (chave em negrito), centralizadas.
- **`sumario`**: insere o campo `TOC \o "1-3" \h \z \u` — o Word gera o índice de verdade só
  quando o usuário atualiza o campo; o texto provisório do placeholder já avisa disso.
- **`tabela_campos`**: cada linha `chave | valor` do conteúdo vira uma linha de tabela 2
  colunas.
- **`marcacoes`**: tabela por tipo de origem (`ROTULO_TP_ORIGEM`), com a extração de onde veio,
  data, conteúdo (+ imagem do anexo, se houver) e o comentário do investigador. **Exclui** os
  tipos já cobertos por um bloco `whatsapp`/`chat` do mesmo padrão (`wpp_mensagem` e também
  `sms_mensagem`/`msg_mensagem` quando há bloco `chat`) — para o mesmo registro não aparecer
  duas vezes no relatório.
- **`whatsapp`**: tabela `COLUNAS_WPP = ["De", "Marcação de tempo-Hora", "Corpo", "Anexo",
  "Transcrição/observação"]`, linha inteira colorida por remetente — **VERDE** `D9EAD3` (dono
  do aparelho/investigado), **AZUL** `CFE2F3` (demais), **CINZA** `EFEFEF` (cabeçalho); anexo
  de imagem embutido abaixo do nome do arquivo, redimensionado para caber num quadrado de
  `IMG_ANEXO_WPP_CM = 4.0` cm sem distorcer.
- **`chat`**: as conversas marcadas (WhatsApp **e** SMS **e** os demais mensageiros de
  `MSG_TB0x`, numa mesma seção) como se fosse a **tela de um celular** — barra verde-escura
  (`075E54`) no topo com o nome da conversa, o aplicativo e a extração de origem (marcada como
  linha de cabeçalho, `w:tblHeader`, então **reaparece no alto de cada página** quando a
  conversa é longa), bolhas à direita/verde (`DCF8C6`) para o dono do aparelho, à
  esquerda/branca para os demais, divisor de data e eventos de sistema do Cellebrite
  ("System Message" — ligação perdida, criação de grupo) como **selo cinza centralizado** do
  tamanho do texto, e os comentários do investigador numa **caixa logo depois do trecho de
  conversa a que se referem** (`_comentarios_do_trecho`) — não numa lista única ao final da
  conversa, que obrigava o leitor a voltar páginas e casar comentário e bolha pela data. O
  comentário da marcação da *conversa* (não de uma bolha) é o único que sai fora dos trechos:
  a mesma caixa, sem citação, antes da primeira tela (`_comentario_da_conversa`).
  - **a caixa e a citação da mensagem comentada.** Antes eram parágrafos soltos, iguais ao
    corpo do relatório, e a mensagem referida aparecia só como carimbo de hora dentro da
    frase: num trecho com várias marcações as linhas ficavam idênticas umas às outras e o
    leitor tinha que caçar a bolha na tela acima. Agora cada anotação é precedida da
    **citação** da mensagem — filete cinza à esquerda (`_borda_esquerda_paragrafo`, `w:pBdr`),
    remetente na cor que ele tem nas bolhas (`_cor_remetente`), carimbo completo e um trecho
    do conteúdo (`_texto_citado`, cortado em `COMENT_MAX_CHARS_CITACAO`) —, no mesmo desenho
    que o WhatsApp usa para resposta; a entrada passa a ser legível sozinha. A caixa é uma
    **tabela de célula única** (`_caixa_comentario`): papel creme `COMENT_FUNDO`, barra de
    acento à esquerda no verde da barra do app (`_bordas_celula`, `w:tcBorders`) e título em
    versalete. Célula, e não parágrafos sombreados um a um, porque é o sombreado da célula
    que envolve várias anotações num bloco só — parágrafo a parágrafo o Word deixa um risco
    branco entre eles e a caixa deixa de parecer uma caixa.
  - **a citação nunca sai vazia.** Mensagem só de mídia não tem corpo — a bolha se resolve
    exibindo a foto e o nome do anexo logo abaixo, a citação não exibe nada disso. Então
    `_texto_citado` descreve: `Imagem (IMG-0007.jpg)`, `Áudio (PTT-0003.opus) — transcrição:
    …`, e acrescenta `arquivo indisponível` quando nenhum anexo teve binário recuperado (a
    bolha diz isso por extenso; a citação não pode calar). Só a anotação em si sai na fonte
    do relatório (Times 10pt): quem a escreveu foi o analista, e ela tem que ler como o resto
    do documento assinado, não como o mockup do app.
  Detalhes que fazem o mockup parecer o app de verdade, e por que estão assim:
  - **uma tela por trecho contíguo, não uma tela com tudo emendado.** Marcar "mensagem,
    resposta, mensagem" quase nunca é marcar três bolhas seguidas: entre uma e outra costuma
    haver dezenas de mensagens não marcadas. Emendadas, elas viram um diálogo contínuo que não
    existiu — leitura errada de prova. `ru.dividir_em_trechos` quebra a conversa nos saltos
    (pela `nu_ordem`, a posição da mensagem dentro do fio inteiro, calculada no SQL), e
    `_bloco_chat` desenha uma tela por trecho, com a barra de título repetida e, entre elas, o
    aviso `_aviso_salto_chat` dizendo **quantas mensagens ficaram de fora**. O bloco `whatsapp`
    faz o mesmo com uma linha cinza mesclada no meio da tabela (`_linha_salto_tabela`).
    Conversa marcada por inteiro traz todas as mensagens e sai num trecho só.
  - **a bolha tem o tamanho do texto**, como no WhatsApp. Para isso a tabela que sustenta a
    conversa é uma **grade de `CHAT_COLUNAS = 24` colunas iguais** e cada linha mescla um trecho
    dela (`_faixa_chat`): a bolha ocupa de `CHAT_MIN_COLUNAS` a `CHAT_MAX_COLUNAS` colunas,
    `CHAT_MARGEM_COLUNAS` fica de respiro na borda da "tela" e o resto vira papel de parede.
    Mesclagem de colunas de uma grade é a única largura variável por linha que o Word respeita
    sem discussão — largura de célula solta, linha a linha, ele reinterpreta. Quantas colunas
    cabem sai de `_colunas_da_bolha`, que estima a largura do texto por
    `_CM_POR_CARACTERE_PT` (~0,17 cm a 10pt, medido no PDF gerado pelo Word); errar **para
    mais** só alarga a bolha, errar para menos quebra o nome do remetente em duas linhas.
  - todas as larguras saem da **largura útil real da página** (`_largura_util_cm`, calculada a
    partir da seção do `.docx`) — não de um valor fixo em cm: a área útil real do documento é
    ~15,24 cm, e um valor fixo baseado numa receita genérica de internet estoura a margem
    direita.
  - o **nome do remetente com o telefone/JID ao lado encabeça TODAS as bolhas**, dos dois
    lados. O app nomeia só a primeira de uma sequência (o resto o usuário deduz pela posição na
    tela) e nunca nomeia o "eu", mas no relatório uma bolha citada em separado — ou lida numa
    página em que a sequência já começou — tem que dizer sozinha de quem é. Mensagem enviada
    sem remetente gravado pela extração cai em "Dono do aparelho". Cor do nome é determinística
    por hash (`_PALETA_REMETENTES`, 8 cores) — o mesmo remetente sai sempre com a mesma cor no
    relatório inteiro.
  - o rodapé da bolha traz o **carimbo completo** (`formatar_data_hora`: data, hora e fuso), não
    a hora curta do app — cada mensagem tem que ser datável sozinha, sem depender do divisor de
    data que ficou páginas atrás. É um texto largo que não pode quebrar em duas linhas, então
    entra também na conta de `_colunas_da_bolha`: numa mensagem curta é o carimbo, e não o
    texto, que define a largura da bolha.
  - a bolha indica **resposta e visualização única**, como a tela de análise. Resposta abre a
    bolha com a tarja de citação (`_texto_citacao_bolha`: autor, origem e trecho da original,
    ou a declaração de que ela não foi recuperada) — sem isso a resposta saía como mensagem
    solta. Mídia de visualização única sai com o aviso `TEXTO_VISUALIZACAO_UNICA`
    (`_eh_visualizacao_unica`: rótulo SelfDestruct + anexo sem binário, mesma regra do
    `whatsapp.js`) — sem ele a bolha parecia só mais uma mídia indisponível.
  - o espaço entre bolhas é menor dentro de uma sequência do mesmo remetente
    (`CHAT_ESPACO_MESMO_PT`) do que entre remetentes diferentes (`CHAT_ESPACO_OUTRO_PT`).
  - a bolha tem margem interna própria (`_margens_celula`) e, no fim, `_bolha_mensagem`
    **uniformiza todos os parágrafos** dela (espaçamento zero, fonte `CHAT_FONTE`): o nome do
    anexo é escrito por `_anexos_na_celula`, que é compartilhado com o bloco `whatsapp` e sai
    no estilo Normal do relatório (Times New Roman, 8pt de espaço depois) — dentro da bolha
    isso destoava e abria buracos entre as linhas.
  - a **transcrição** do áudio sai com rótulo próprio, em corpo menor, depois do nome do
    arquivo: não é conteúdo da mensagem, é leitura feita depois, e não pode se confundir com o
    que a pessoa digitou.
  - imagem embutida na bolha usa `CHAT_IMG_CM = 6.5` (bem maior que a coluna estreita da tabela
    do bloco `whatsapp`), e a bolha é dimensionada por esse valor em vez de ir ao teto — senão
    sobra um vão branco à direita da foto.
  - `_shade`, `_margens_celula` e `_bordas_tabela` **inserem antes dos sucessores** em vez de
    dar `append`: `w:tcPr`/`w:tblPr` têm ordem fixa no schema e o Word recusa o arquivo
    ("conteúdo ilegível") se um filho aparecer fora de ordem.
- **`extracoes`**: tabela com nome do arquivo, tamanho e o **SHA-256 calculado na importação**
  (`CO_HASH_SHA256`); extração anterior à migração `25_rif.sql` não tem hash gravado
  e a célula declara "Não calculado na importação." em vez de sair vazia. A coluna do hash é
  alargada (`EXTRACOES_FRACOES_COLUNAS`) e o valor sai em 8pt, senão os 64 caracteres quebram
  em quatro linhas — ver `hash_arquivos.md`.
- **`contas`**: contas de usuário dos aparelhos (`DON_TB01_DONOS`).
- **`bancario_contas`/`bancario_contrapartes`/`bancario_alertas`**: espelham as abas de
  `simba.html`; o parágrafo de síntese acima da tabela existe porque, sem ele, o leitor teria
  que somar 200 linhas para saber de quanto se está falando; quando a tabela de contrapartes
  corta no limite (`listar_bancario_contrapartes(limite=40)`), um parágrafo declara quantas
  ficaram de fora e quanto elas somam — a tabela nunca deve passar a impressão de ser a lista
  completa quando não é.
- **`rif_resumo`/`rif_comunicacoes`/`rif_contrapartes`/`rif_envolvidos`/`rif_ocorrencias`**:
  espelham as abas de `rif.html`; `rif_comunicacoes` numera cada comunicação como subseção
  (4.1, 4.2...) e, sob cada uma, duas tabelinhas — Entradas (crédito) e Saídas (débito) — com
  as contrapartes estruturadas daquela comunicação (`listar_contrapartes_por_comunicacao_rif`),
  contraparte sem nome sai como "DESCONHECIDO" (mesma semântica de `rif.md`).
- **`ipl_resumo`/`ipl_pecas`/`ipl_pessoas`/`ipl_achados`**: espelham as abas de
  `inquerito_pdf.html`. `ipl_resumo` traz o parágrafo de síntese, a tabela de cabeçalho e o
  **resumo dos fatos** como parágrafo corrido (é texto de despacho, não cabe espremido em
  célula). `ipl_pecas` mostra a faixa de folhas (`1–3`, ou só a inicial quando não há final) —
  é a folha que localiza a peça nos autos. `ipl_achados` monta **uma tabela por tipo de achado**
  (`ROTULO_TP_ACHADO_IPL`): telefone, conta e valor não se comparam entre si, e numa tabela única
  o tipo teria que se repetir em cada linha; tipo novo que a extração passe a reconhecer sai com
  o próprio código capitalizado, em vez de sumir da tabela.
- **`pix_chaves`**: uma linha **por chave**, não por menção — a mesma chave costuma aparecer
  dezenas de vezes na mesma conversa, e o relatório quer a chave, onde ela apareceu e quando.
- **`nuvem_perfil`/`nuvem_dispositivos`/`nuvem_acessos`/`iped_extracoes`**: `nuvem_perfil` sai
  como tabela chave/valor (é um punhado de campos de uma conta só, e uma tabela larga com uma
  linha lê pior); `nuvem_dispositivos` destaca o **IMEI**, que é o elo entre a conta e um aparelho
  apreendido; `nuvem_acessos` usa `ACS_TB02_AGREGADOS` e não os acessos crus de `ACS_TB01` —
  são dezenas de milhares de linhas que não cabem num relatório, e o agregado é a mesma
  informação na granularidade que se lê; `iped_extracoes` é a cadeia de custódia do material de
  nuvem, no papel que o bloco `extracoes` cumpre para os `.ufdr`, e só lista importações
  `concluido` (mesmo critério dos extratos do SIMBA).
- **Tabelas que cortam num limite** (`pix_chaves`, `nuvem_dispositivos`, `nuvem_acessos`, além das
  contrapartes bancárias e de RIF): `_aviso_corte` declara quantas linhas ficaram de fora. Uma
  tabela num relatório assinado nunca pode passar a impressão de ser a lista completa quando não é.

**Registro sem conteúdo** (`_texto_sem_conteudo`, `TEXTO_SEM_CONTEUDO`): mensagem apagada ou
mídia que a extração não recuperou (`co_caminho_zip` nulo — mesmo critério de "indisponível no
arquivo" da tela de análise) deixava a célula "Corpo"/a bolha **em branco**; célula vazia num
relatório assinado é pior que uma frase, porque o leitor não distingue "o analista esqueceu de
preencher" de "esse dado não existe mais". Agora sai a frase do tipo de mídia — "Arquivo de
mensagem de áudio/imagem/vídeo indisponível." — ou "Conteúdo não disponível." quando não há nem
anexo. Quando o binário **existe**, o corpo continua vazio de propósito: a imagem/nome do
arquivo ao lado já é o conteúdo. Na coluna do próprio anexo, onde o nome do arquivo já está
escrito, a frase inteira seria repetição e sai só `(arquivo indisponível na extração)`. Em
marcações de **arquivo do aparelho** (`tp_origem = 'arquivo'`) a frase perde o "de mensagem".

**Hash do anexo** (`_linha_hash_md5`, ligado por `com_hash=True` nos blocos `marcacoes`,
`whatsapp` e `chat`): logo abaixo do nome do arquivo sai `HASH - MD5: <hash>`, um ponto menor
que o nome. É o que liga o arquivo **citado** no relatório ao binário **periciado** — dois
arquivos de mesmo nome não provam nada, o mesmo MD5 sim, e é por ele que o achado se confronta
com base de hashes conhecidos ou com o outro aparelho do caso (`comparar_celulares.md`).

O valor vem pronto do `<file>` do UFDR (`ufdr_parser`), gravado em `ARQ_TB01_ARQUIVOS.CO_HASH_MD5`.
As tabelas de anexo (`WPP`/`SMS`/`MSG_TB05_ANEXOS`) **não guardam hash** — só o ponteiro para o
binário no ZIP —, então as consultas casam anexo × arquivo do aparelho por
`(ID_EXTRACAO, CO_CAMINHO_ZIP)`, com o índice `IX251_ARQ_TB01_CAMINHO_ZIP` sustentando a busca
(sem ele seria uma varredura da tabela de arquivos por anexo do relatório).

Só sai em anexo **com** binário na extração: sem binário não há o que hashear, e a célula já
declarou a indisponibilidade. Com binário e sem hash no banco — extração ingerida antes de o
parser passar a ler MD5/SHA-256 do `<file>`, ou binário que não casou com nenhuma linha de
arquivo — a linha sai como `HASH - MD5: não disponível nesta extração.`, em itálico: num
relatório de cadeia de custódia o silêncio não distingue "não temos o hash" de "esqueceram de
escrever". Some reprocessando a extração.

**Imagem do anexo** (`_imagem_do_anexo`): lida **sob demanda** direto do `.ufdr` original — o
binário nunca é copiado para o banco nem para o disco do servidor, mesma técnica (inclusive o
cache em memória, mesma chave) da prévia de imagem no chat de investigação
(`investigacao_celular.md`). `.ufdr` ausente, membro corrompido ou Pillow incapaz de decodificar
→ `None`, e o relatório sai só com o nome do anexo, sem travar a geração inteira.

**O que conta como imagem** (`_eh_imagem`): o **binário**, não o `TP_CONTEUDO` declarado —
`categoria_binario_anexo`, a mesma regra da tela de análise. Vídeo do WhatsApp cujo arquivo não
foi recuperado chega como `video/mp4` com um JPEG de 1 KB no lugar (o `.thumb` do Cellebrite);
pelo tipo declarado ele é vídeo e o relatório não mostrava nada, enquanto a tela de análise
exibia a miniatura. São 50 anexos `.thumb` declarados `video/mp4` só na extração de teste, mais
212 com o tipo em branco e 12 de preview de link. Quando o binário é miniatura de outra coisa
(`_so_miniatura`), sai embaixo da imagem, em itálico, `(só a miniatura do vídeo foi extraída)` —
espelho do aviso que o chat dá (`renderMiniatura` em `whatsapp.js`): o relatório não pode
apresentar um quadro de vídeo como se fosse a foto enviada.

**Imagem menor que a caixa não é ampliada** (`_CM_POR_PIXEL`, 1 px = 1/96 pol, a equivalência do
navegador): a miniatura de vídeo tem 54×96 px e, esticada para os 6,5 cm da bolha, virava um
borrão de pixels num documento assinado. Foto de verdade sai sempre maior que a caixa — a prévia
já vem com 800 px no lado maior —, então para ela o teto nunca pega.

### Geração em background (`relatorios_geracao.py`)

Antes o `.docx` era montado dentro da própria requisição e devolvido como binário. Num caso grande
— sobretudo com as opções de IA ligadas ("Resumo das conversas por IA", uma chamada ao provedor
**por marcador de cada conversa**; "Reescrever comentários do analista por IA", **uma por
comentário**) — a montagem passa dos **60 s em que o nginx corta a requisição** (não há
`proxy_read_timeout` em `nginx/default.conf`), e o usuário via erro num relatório que ia terminar
bem. É o mesmo problema já resolvido na importação do SIMBA (`26_simba.sql`) e no chat.

O desenho é copiado de `embed_utils.py` (indexação semântica), o job mais próximo deste:

- `iniciar_geracao` cria a linha em `REL_TB04_GERACOES` e dispara uma `threading.Thread(daemon=True)`
  — não há fila nem `BackgroundTasks` no projeto, e a API roda **um worker uvicorn só**.
- **Trava por (inquérito, usuário)**, não só por inquérito (`UX99_REL_TB04_ATIVA`): dois
  investigadores no mesmo caso geram relatórios diferentes ao mesmo tempo e não podem se bloquear.
  O `UniqueViolation` do índice vira `GeracaoOcupada` → **409** na rota.
- **Watchdog** `_liberar_geracoes_travadas` (30 min): sem ele, um restart da API no meio deixa a
  linha em `processando` para sempre e o índice único tranca o usuário fora do recurso.
- `_atualizar_geracao` usa **conexão própria por update** — se o worker segurasse uma transação
  até o fim, o polling não enxergaria progresso nenhum.
- `gerar_relatorio_docx` recebe um callback `progresso(indice, rótulo)` e continua devolvendo
  `bytes`: a exportação de marcadores (`rotas/marcadores.py`) é outro endpoint, segue síncrona e
  não foi afetada. `QT_TOTAL` conta só os blocos **selecionados**, senão a barra pararia antes do fim.
- Falha **nunca sobe**: vira `cd_status='erro'` + `tx_erro` (truncado em 2000 caracteres), que o
  polling mostra na tela. Quem chamou é uma thread solta — um traceback morreria no log.
- O `.docx` é gravado em `PASTA_INVESTIGACAO/relatorios` (não no banco: são megabytes que o usuário
  baixa uma vez) e servido por `FileResponse` numa rota que **confere o acesso de novo** — entre
  disparar e baixar pode ter passado tempo e a permissão pode ter mudado. Limpeza preguiçosa dos
  arquivos com mais de 7 dias na hora de gravar um novo (o projeto não tem agendador; é a mesma
  solução do `.md` de prompt do chat), mais a remoção em cascata em `apagar_inquerito`.

No front (`investigacao.js`), `acompanharGeracao` é um `setTimeout` recursivo de 2 s com o timer
guardado em variável — o mesmo `acompanharIndexacao` da busca semântica. O timer **não depende do
DOM do modal**: se o usuário fechar a janela no meio, a geração termina e o download acontece do
mesmo jeito, em vez de o arquivo ficar órfão no disco.

**Resumo das conversas por IA** (`comentario_ia=True`, blocos `chat`/`whatsapp`):
`resumir_mensagens_ia` agrupa as mensagens marcadas **por marcador** dentro de cada conversa
(`ru.rotulo_marcador`, que cai para o nome da cor quando o registro não tem etiqueta) e pede à IA
configurada um parágrafo curto (≤ 4 frases) por grupo — cada marcador vira um resumo separado, com
a etiqueta do nome quando a conversa mistura mais de um. **Falha em silêncio**: sem provedor de
IA configurado, ou erro de rede/API (`ErroProvedorIA`), a função devolve `None` e a seção sai
sem aquele resumo — um recurso opcional não deve travar a geração do relatório inteiro.

**Reescrever comentários do analista por IA** (`reescrever_comentarios=True`): reescreve os
comentários das marcações — os que saem depois de cada trecho no bloco `chat`
(`_comentarios_do_trecho`), o comentário da conversa inteira e a coluna "Observação" do bloco
`marcacoes`. A opção só aparece quando o padrão tem bloco `chat` ou `marcacoes`, os que imprimem
comentário (a tabela do bloco `whatsapp` não tem essa coluna).

O prompt é de **redação, não de análise**: corrigir grafia e fechar a frase, preservando a
afirmação do analista. **Só o texto da anotação vai ao modelo** — nem a mensagem marcada, nem a
conversa: sem material novo à frente dele, não há de onde trazer fato que o analista não escreveu.
A anotação reescrita continua sendo palavra do analista, que assina o relatório. Também **falha em
silêncio**, mas ao contrário do resumo o texto **original volta ao documento**
(`_texto_do_comentario`): IA fora do ar não apaga a anotação de ninguém.

**Tudo antes de desenhar os blocos, uma anotação por chamada, em paralelo**
(`ru.reescrever_comentarios_ia`, que devolve `{original: reescrito}`). A primeira versão chamava a IA
dentro do renderizador, uma vez por comentário, **em série** — num caso real de **471 comentários
marcados** a geração ficou mais de meia hora parada no bloco "6. Conversas" e o usuário leu como
travamento. Duas correções, no mesmo lugar: os **textos repetidos** são reescritos uma vez só
(aqueles 471 comentários eram **37 textos distintos** — marcar um bloco de mensagens com a mesma
anotação é o normal) e as chamadas saem em `ThreadPoolExecutor` de `_WORKERS_REESCRITA = 6`. O
`progresso` recebe `"Reescrevendo comentários do analista por IA (n/N)…"` a cada uma concluída, com
índice de bloco 0 — a etapa aparece na tela antes de o primeiro bloco existir. É também por isso que
o prompt não leva a mensagem como contexto: sem ela, a mesma anotação sempre gera a mesma frase, e o
cache por texto é exato.

**Mandar várias anotações numa chamada só (lote) foi tentado e revertido** (commits `0b09bd5` e
`b8be961`, 17/08/2026). Parecia a economia óbvia — menos requisições —, mas o
`deepseek-v4-flash` é **superlinear no tamanho do lote**, medido em produção com as anotações reais
do caso: **1 anotação em 4,5 s; 5 em 34,2 s; 10 em 152,6 s** (15,3 s por anotação, contra 4,5 s
sozinha). Com dedupe + 6 frentes, 37 anotações individuais levam ~30 s; em lotes de 10, ~2,5 min. E
um lote grande estoura o timeout de 90 s do provedor (`TIMEOUTS_POR_PROVEDOR`), caso em que o lote
inteiro volta vazio e as 20 anotações saem sem reescrita. A cobrança do DeepSeek é por token, não
por requisição, então o lote também não economizava dinheiro — só o prompt de sistema repetido, uns
5 mil tokens no caso inteiro. Se um dia o provedor mudar, o número a refazer é esse: tempo por
anotação em lote de 1, 5 e 10.

### Rotas (`api/rotas/relatorios.py`, registrado com `exigir_aprovado` no `main.py`)

- **Padrões/blocos** (admin): `GET /api/relatorios/variaveis` (catálogo de variáveis
  automáticas para o painel de ajuda), `GET|POST /api/relatorios/padroes`,
  `GET|POST /api/relatorios/padroes/{id}`, `POST .../ativo`, `DELETE .../{id}`,
  `POST /api/relatorios/padroes/{id}/blocos`, `POST /api/relatorios/blocos/{id}`,
  `POST .../mover`, `DELETE /api/relatorios/blocos/{id}`.
- **Marcações** (acesso ao inquérito via `_exigir_acesso`, 404/403):
  `GET /api/investigacao/inqueritos/{id}/marcacoes?tp_origem=...`,
  `POST /api/investigacao/marcacoes`, `POST .../marcacoes/{id}/comentario`,
  `DELETE /api/investigacao/marcacoes/{id}`.
- **Geração**: `GET /api/relatorios/padroes-ativos` (o que a tela de geração oferece),
  `GET /api/investigacao/inqueritos/{id}/relatorios/{id_padrao}/preparar` — monta o
  formulário: os blocos do padrão (com `rotulo_tipo` pronto), tokens manuais, prévia das
  automáticas, e **conforme os tipos de bloco do padrão**
  (`TIPOS_BLOCO_MARCACOES`/`TIPOS_BLOCO_RIF`/`TIPOS_BLOCO_BANCARIO`/`TIPOS_BLOCO_IPL`) o que o
  usuário escolhe incluir. Um padrão de RIF/dados bancários/IPL pode ter um bloco `marcacoes`
  junto (ex.: seção "Registros marcados pela equipe"), mas a tela **não** oferece o seletor de
  marcações nesse caso — o foco ali é escolher os arquivos, e o bloco de marcações sai
  com tudo que já estiver marcado (`mostrar_marcacoes = bool(tipos & TIPOS_BLOCO_MARCACOES)
  and not tem_documento_proprio`) — decisão tomada depois de a tela antiga mostrar um seletor de
  marcações vazio e confuso para esses padrões.
  `POST /api/investigacao/inqueritos/{id}/relatorios/{id_padrao}/gerar` — **dispara o job** e
  devolve `{"ok": true, "id_geracao": N}` (409 se o usuário já tiver uma geração em andamento
  naquele inquérito). O payload aceita `ids_marcacoes`, `ids_rif`, `ids_extratos`, `ids_ipl`,
  `ids_blocos` e `in_comentario_ia`.
  `GET /api/investigacao/inqueritos/{id}/geracoes-relatorio/recentes` — últimas 5 gerações do
  usuário no inquérito (sem caminho em disco, com `tem_arquivo`), para o histórico do modal.
  `GET /api/investigacao/geracoes-relatorio/{id_geracao}` — andamento, para o polling (o caminho
  do arquivo em disco não trafega: é detalhe do servidor).
  `GET /api/investigacao/geracoes-relatorio/{id_geracao}/download` — o `.docx` como anexo
  (`Content-Disposition: attachment`, nome via `nome_arquivo`), conferindo o acesso ao inquérito
  de novo; 409 se ainda não estiver pronto e 404 se o arquivo já tiver sido limpo por antiguidade.
  O número do inquérito do nome do arquivo vem do **banco** (`obter_numero_inquerito`), nunca do
  payload — `numero_inquerito` é variável automática e nunca é enviada pela tela.

## Modelo de dados

Criado em `db/init/30_relatorios.sql` (esquema
`INVESTIGACAO`). Os blocos `bancario_*`, `rif_*`, `ipl_*`, `pix_chaves`, `nuvem_*` e
`iped_extracoes` **não têm tabela própria** — na geração, consultam direto
`SMB_TB0*`/`RIF_TB0*`/`IPL_TB0*`/`PIX_TB01`/`PRF_TB01`/`DVC_TB01`/`ACS_TB02`/`IPD_TB01` do
inquérito (ver `simba.md`, `rif.md`, `documentos_diversos.md`, `investigacao_celular.md` para as
chaves PIX, `extracao_iped.md`).

| Tabela | Papel |
|---|---|
| `REL_TB01_PADROES` | Padrão: `ID_PADRAO`, `NO_PADRAO`, `TX_DESCRICAO`, `IN_ATIVO`, `ID_USUARIO_CRIADOR`, `DT_CADASTRO` |
| `REL_TB02_BLOCOS` | Bloco: `ID_BLOCO`, `ID_PADRAO` (FK), `NU_ORDEM`, `TP_BLOCO` (`CHECK CK03_REL_TB02_TP_BLOCO` — ver tabela de tipos acima), `NO_TITULO`, `TX_CONTEUDO` (texto com `{{variáveis}}`; em `marcacoes`/`whatsapp`/`chat`/`bancario_*`/`rif_*` é a introdução opcional, não o conteúdo em si) |
| `REL_TB03_MARCACOES` | Marcação: `ID_INQUERITO` (FK), `TP_ORIGEM` (mesmos códigos de `ENT_TB01_ENTIDADES` — sem FK, `TP_ORIGEM` diz em qual tabela procurar; inclui `wpp_conversa`/`sms_conversa`/`msg_conversa`, a conversa marcada inteira), `ID_ORIGEM` (PK da linha na tabela correspondente), `TX_COMENTARIO`, `CO_COR` (ex.: `#E74C3C` — *fallback* anterior aos marcadores nomeados), `ID_USUARIO_CRIADOR`, `DT_CRIACAO` |
| `MRC_TB01_MARCADORES` / `MRC_TB02_APLICACOES` | Marcadores nomeados pendurados na marcação, um conjunto por usuário — ver [marcadores.md](marcadores.md) |
| `REL_TB04_GERACOES` | Job de geração: `ID_GERACAO`, `ID_INQUERITO` (FK), `ID_PADRAO` (FK, `ON DELETE CASCADE`), `ID_USUARIO`, `CD_STATUS` (`pendente`\|`processando`\|`concluido`\|`erro`), `QT_TOTAL`/`QT_FEITOS` (blocos), `TX_ETAPA_ATUAL`, `TX_ERRO`, `NO_ARQUIVO`, `CAMINHO_ARQUIVO`, `DT_INICIO`, `DT_FIM` — ver "Geração em background" |

Índices: `IX76` (TB02 por padrão+ordem), `IX77` (TB03 por inquérito), `IX78` (TB03 por
origem) e o **único** `UX95_REL_TB03_REGISTRO` (TB03 por inquérito+origem — uma marcação por
registro; `30_relatorios.sql` funde as repetições que já existiam antes de criá-lo,
juntando os comentários e preservando a cor, e é reaplicável). Os quatro padrões seedados (`30_relatorios.sql` "relatório de análise - tabela simples", "Relatório de dados bancários" "Relatório
de RIF", `30_relatorios.sql` "relatório de análise - whatsapp"; a renomeação dos nomes
antigos em bancos já existentes fica em `30_relatorios.sql`) são
**idempotentes** (`IF EXISTS ... RETURN` antes do `INSERT`) — reaplicáveis num banco já
existente via `psql`, sem duplicar o padrão a cada deploy.

## Configuração

- `PASTA_INVESTIGACAO` (env, default `/app/data`) — onde os `.ufdr` ficam para a leitura sob
  demanda das imagens de anexo, e onde o `.docx` gerado é gravado, em
  `PASTA_INVESTIGACAO/relatorios` (a geração passou a ser em background e o arquivo é baixado
  depois, por rota própria; ver "Geração em background"). Arquivos com mais de 7 dias
  (`_DIAS_MANTER_ARQUIVO`) são apagados na geração seguinte.
- Nenhuma chave de provedor é **exigida**: a geração em si é offline (python-docx). Os dois
  checkboxes de IA ("Resumo das conversas por IA" e "Reescrever comentários do analista por IA")
  são a única parte que chama um provedor — sem ele configurado o relatório sai igual, só sem os
  parágrafos de resumo e com os comentários do jeito que foram digitados (ver as duas seções
  acima). Ver `configuracoes_ia.md`.

## Referências

- Tela: `web/padroes_relatorio.html` (JS: `js/padroes_relatorio.js`).
- Rotas: `api/rotas/relatorios.py`.
- Funções: `api/funcoes/relatorios_utils.py` (padrões, marcações, variáveis, consultas de
  RIF/bancário/IPL/PIX/nuvem), `api/funcoes/relatorios_docx.py` (montagem do `.docx`),
  `api/funcoes/relatorios_geracao.py` (job em background e arquivo em disco).
- Esquema: `db/init/30_relatorios.sql`.
- Widget de marcação nas telas de dados: `web/js/utils/entidades.js` (modal individual, modal de
  lote e o cache que destaca os botões já marcados).
- Relacionados: `mesa_inquerito.md` (domínio `TP_ORIGEM`/`ID_ORIGEM`, mesmo `JOIN COALESCE` de
  resolução), `simba.md` (blocos `bancario_*` consideram só extratos `concluido`), `rif.md`
  (blocos `rif_*`, "DESCONHECIDO"), `investigacao_celular.md` (conversas WhatsApp/SMS/
  mensageiros, leitura de imagem sob demanda do `.ufdr`), `hash_arquivos.md` (hash em branco
  no bloco `extracoes`), `documentos_diversos.md` (origens `ipl_pessoa`/`ipl_achado` nas
  marcações), `configuracoes_ia.md` (provedor usado pelas duas opções de IA da geração).
