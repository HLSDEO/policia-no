# Padrões de crime (léxico, regex, temporal)

## Visão geral

Padrões que o botão **"Detectar padrões"** da Mesa de Inquérito roda sobre os dados de um
inquérito. Tudo é **determinístico, sem IA** — cobre 100% dos dados e o resultado é
reprodutível/auditável, ao contrário da busca automática por IA (`mesa_inquerito.md`). Três
tipos (`TIPOS_PADRAO_VALIDOS = {"lexico", "regex", "temporal"}`):

- **léxico** — lista de termos/gírias, casada por `ILIKE ANY` (contains, sem acentuação
  especial) nas fontes de texto;
- **regex** — expressão regular POSIX (`~*`, case-insensitive) nas mesmas fontes;
- **temporal** — comportamento no tempo, dois subtipos (`SUBTIPOS_TEMPORAIS = {"madrugada",
  "rajada"}`): atividade numa faixa de horário ou muitas mensagens em pouco tempo.

Os padrões são uma biblioteca **global** (cadastro por administrador, reaproveitada por todos
os inquéritos) e só os **ativos** entram na próxima detecção — a detecção roda **todos** os
ativos de uma vez, sem seleção por execução. Cada detecção aponta o registro de origem (mesmo
domínio `tp_origem`/`id_origem` das entidades) e pode virar entidade com um clique.

## Fluxo de uso

1. Na aba **Padrões de crime** de `web/ia_mesa_inquerito.html`, administrador vê a lista de
   padrões (`padroes-card`) e o botão "Novo padrão" (`abrir-novo-padrao-btn`); usuário sem
   permissão vê o aviso `padroes-sem-permissao` — leitura (`GET /api/padroes`) é
   liberada a qualquer usuário aprovado (a Mesa precisa mostrar o que vai rodar), só a escrita é
   admin-only.
2. O modal `modal-padrao` recebe: nome (máx. 150), tipo (`léxico`/`regex`/`temporal`),
   **categoria** (ex.: tráfico, financeiro, armas, comportamento — máx. 80), **descrição**
   ("o que este padrão indica quando dispara" — máx. 500) e a **definição** conforme o tipo:
   - léxico: campo `campo-lexico` — lista de termos, cada um vira `%termo%` na busca;
   - regex: campo `padrao-regex-input` (fonte monoespaçada, ex.: `\d{3}\.\d{3}\.\d{3}-\d{2}`) —
     validado com `re.compile` no servidor antes de gravar (expressão inválida vira erro 400
     com a mensagem do Python, não silenciosamente ignorada);
   - temporal: `campo-temporal` — madrugada (`hora_inicio`, `hora_fim`, `minimo` de mensagens)
     ou rajada (`janela_minutos`, `minimo_mensagens`).
3. Na Mesa, "IA → Detectar padrões" dispara `POST /api/investigacao/inqueritos/{id}/deteccao`
   em background (mesmo padrão de job/polling das demais extrações); o modal `deteccoes-corpo`
   lista os disparos da execução mais recente concluída, agrupados por padrão, cada um com o
   registro de origem e ação de "criar entidade" (`POST /api/investigacao/entidades` com
   `comentario = "[padrão: <nome>] <trecho>"`).

## Como funciona

### Validação da definição (`validar_definicao`)

Chamada tanto na criação quanto na atualização; levanta `ValueError` com mensagem legível
(vira `400` na rota) quando algo não fecha:

- **léxico** — pelo menos um termo não vazio depois do `strip()`; devolve `{"termos": [...]}`.
- **regex** — padrão não vazio e que compila (`re.compile`); devolve `{"padrao": ...}`.
- **temporal madrugada** — `hora_inicio`/`hora_fim` inteiros 0–23, `minimo` inteiro ≥ 1
  (default 0h–5h, mínimo 10). `hora_inicio > hora_fim` é aceito de propósito: é a faixa que
  cruza a meia-noite (ex.: 22h–5h), tratada na consulta com `OR` em vez de `BETWEEN` (ver
  `_detectar_madrugada`).
- **temporal rajada** — `janela_minutos` inteiro ≥ 1 (default 10), `minimo_mensagens` inteiro
  ≥ 2 (default 20); devolve `{"subtipo": "rajada", ...}`.

### Execução (`_processar_deteccao`, background com polling)

Roda em thread (`iniciar_deteccao` cria a linha `pendente` e dispara), como as demais
extrações/buscas do sistema. Para cada padrão **ativo** (`listar_padroes` filtrado por
`in_ativo`), chama o detector do tipo; **um padrão que falha (ex.: regex que o Postgres
rejeita, embora o Python já tenha aceitado) não derruba os demais** — o erro vai para
`erros[]`, juntado com `"; "` em `TX_ERRO` (até 2000 chars) ao fim, e a execução termina
`concluido` mesmo assim. Se **nenhum** padrão estiver ativo, levanta erro antes de começar
("Nenhum padrão ativo cadastrado na tela Padrões de crime.").

`_LIMITE_POR_FONTE = 200` — teto de achados por padrão em **cada fonte** (não um teto global):
um léxico genérico ("pó") num aparelho grande geraria dezenas de milhares de linhas sem ganho
investigativo; 200 já é mais do que um investigador lê.

### Léxico e regex (`_detectar_texto`, fontes em `_FONTES_TEXTO`)

Um `INSERT … SELECT` por fonte, com `LIMIT _LIMITE_POR_FONTE`, filtro `texto ILIKE ANY(%termos%)`
(léxico, cada termo vira `%termo%`) ou `texto ~* %regex%` (regex, POSIX case-insensitive). As
fontes:

| `tp_origem` | Texto varrido |
|---|---|
| `wpp_mensagem` | `WPP_TB04.TX_MENSAGEM` **+** `WPP_TB05.TX_TRANSCRICAO` (transcrição de áudio, via `UNION ALL` — mensagem de voz não tem texto próprio) |
| `sms_mensagem` | mesma ideia em `SMS_TB04`/`SMS_TB05` |
| `notificacao` | `NTF_TB01.TX_CONTEUDO` |
| `nota` | `concat_ws(': ', TX_TITULO, TX_CORPO)` de `NOT_TB01` |
| `navegacao` | `concat_ws(' · ', TX_TITULO, TX_URL)` de `NAV_TB01` |
| `arquivo` | `ARQ_TB01.TX_OCR` — texto reconhecido em imagens (print de comprovante PIX, boleto, foto de documento, conversa printada) só existe aqui; sem esta fonte nenhum padrão alcança o conteúdo de uma imagem. Reaproveita `tp_origem = 'arquivo'` (o mesmo da tela Arquivos/marcações), não um tipo novo — assim "criar entidade" a partir da detecção continua funcionando |

A data gravada em `DT_OCORRENCIA` é a de cada fonte (`dt_mensagem`, `dt_notificacao`,
`dt_modificacao`/`dt_criacao` da nota, `dt_visita`, `dt_modificacao` do arquivo — o carimbo que
o UFDR dá à imagem).

### Temporal — madrugada (`_detectar_madrugada`)

Uma detecção **por conversa** (WhatsApp e SMS, mesmo formato de tabelas em
`_FONTES_CONVERSA`) com `count(*) >= minimo` mensagens na faixa de horário, ancorada na
**última** mensagem da faixa (`max(id_mensagem)`, `max(dt_mensagem)`). Faixa que não cruza a
meia-noite usa `BETWEEN hora_inicio AND hora_fim`; faixa que cruza (`hora_inicio > hora_fim`,
ex.: 22h–5h) usa `hora >= hora_inicio OR hora <= hora_fim` — um `BETWEEN` simples nesse caso
excluiria a faixa inteira. O trecho gravado é
`"<n> mensagens entre <hi>h e <hf>h na conversa «<rótulo>»"`.

### Temporal — rajada (`_detectar_rajada`)

Uma detecção **por conversa/dia** em que alguma janela deslizante de `janela_minutos` teve
`>= minimo_mensagens`, usando `count(*) OVER (PARTITION BY id_conversa ORDER BY dt_mensagem
RANGE BETWEEN <janela> PRECEDING AND CURRENT ROW)` — janela de tempo, não de linhas. `DISTINCT
ON (id_conversa, dia)` mantém só a janela de maior contagem do dia, ancorada na mensagem que a
fechou.

### Rótulo de conversa (`_rotulo_conversa`)

Comum aos dois detectores temporais: nome do grupo (`c.no_grupo`) ou, sem grupo, o primeiro
participante que **não** é o dono do aparelho (`in_dono_dispositivo = false`) — mesma lógica de
rótulo usada em `investigacao_celular.md` para conversas individuais. Sem nenhum dos dois,
`"conversa " || id_conversa`.

### Rotas (`api/rotas/padroes.py`)

- `GET /api/padroes` — `exigir_aprovado`; lista ativos e inativos.
- `POST /api/padroes` / `POST /api/padroes/{id}` / `POST /api/padroes/{id}/ativo` /
  `DELETE /api/padroes/{id}` — `exigir_admin`. `AtivoIn` só tem `{ativo: bool}`.
- `POST /api/investigacao/inqueritos/{id}/deteccao` — `exigir_acesso_inquerito`; dispara a
  execução (dispara **todos** os padrões ativos, sem escolha).
- `GET /api/investigacao/inqueritos/{id}/deteccao` — histórico de execuções (polling de
  status: `pendente`/`processando`/`concluido`/`erro`).
- `GET /api/investigacao/inqueritos/{id}/deteccoes?execucao=` — detecções de uma execução (a
  mais recente `concluido`, se `execucao` não vier).
- Modelos: `PadraoIn` (`tp_padrao` com `pattern="^(lexico|regex|temporal)$"`, `categoria` ≤ 80,
  `descricao` ≤ 500, `definicao` como `dict` — validado de novo em `validar_definicao` antes de
  gravar) e `AtivoIn`.

### Padrões de partida

A migração insere 7 padrões de exemplo (editáveis/desativáveis na tela, não fixos no código):
"Gíria de tráfico" e "Menção a arma de fogo" (léxico), "CPF citado em mensagem", "Placa de
veículo" e "Valor em dinheiro" (regex), "Atividade na madrugada" (temporal, 0h–5h, mínimo 10) e
"Rajada de mensagens" (temporal, 10 min, mínimo 20 mensagens).

## Modelo de dados

Criado na parte 4 de `db/init/21_entidades_padroes.sql` (esquema `INVESTIGACAO`):

| Tabela | Colunas | Papel |
|---|---|---|
| `PDR_TB01_PADROES` | `ID_PADRAO`, `NO_PADRAO` (150), `TP_PADRAO` (`lexico`/`regex`/`temporal`), `NO_CATEGORIA` (80), `DS_PADRAO` (500), `TX_DEFINICAO` (JSONB — formato varia por tipo, ver acima), `IN_ATIVO` (default `true`), `DT_CADASTRO`, `ID_USUARIO_CRIADOR` (FK `FK104` → `USR_TB01`) | Biblioteca global do padrão |
| `PDR_TB02_EXECUCOES` | `ID_EXECUCAO`, `ID_INQUERITO` (FK `FK105`), `ID_USUARIO` (FK `FK106`), `CD_STATUS` (`pendente`/`processando`/`concluido`/`erro`), `TX_ERRO`, `QT_DETECCOES`, `DT_INICIO`, `DT_FIM` | Uma linha por clique em "Detectar padrões" |
| `PDR_TB03_DETECCOES` | `ID_DETECCAO`, `ID_EXECUCAO` (FK `FK107`, `ON DELETE CASCADE`), `ID_INQUERITO` (FK `FK108`), `ID_PADRAO` (FK `FK109`, `ON DELETE CASCADE`), `TP_ORIGEM` (30), `ID_ORIGEM`, `TX_TRECHO`, `DT_OCORRENCIA` | Um achado, apontando o registro de origem (mesmo domínio `TP_ORIGEM`/`ID_ORIGEM` de `ENT_TB01_ENTIDADES`) |

Apagar um padrão ou uma execução leva as detecções junto (`ON DELETE CASCADE`); apagar o
inquérito **não** tem FK cascata configurada nessas três tabelas — mesmo padrão de outras
tabelas de execução do sistema (a limpeza de inquérito passa por rotina própria).

Índices: `IX50` (`PDR_TB02` por inquérito), `IX51` (`PDR_TB03` por execução), `IX52` (`PDR_TB03`
por inquérito).

## Configuração

Sem variáveis de `.env`: a biblioteca de padrões é 100% administrável pela tela (não há padrão
de fábrica reconfigurável fora dela, ao contrário dos pesos de alerta do SIMBA — ver
`simba.md`). O único limite não configurável é `_LIMITE_POR_FONTE = 200` (constante em
`padroes_utils.py`) — é proteção contra léxico genérico gerar ruído, não uma convenção de
análise que faça sentido expor por inquérito.

## Referências

- Telas: `web/ia_mesa_inquerito.html`, aba "Padrões de crime" (JS: `js/padroes_crime.js`;
  troca de abas em `js/ia_mesa_inquerito.js`), Mesa de Inquérito
  (`web/mesa_inquerito.html` — modal `modal-deteccoes`, JS: `js/mesa_inquerito.js`).
- Rotas: `api/rotas/padroes.py`.
- Funções: `api/funcoes/padroes_utils.py` (`validar_definicao`, `_processar_deteccao`,
  `_detectar_texto`, `_detectar_madrugada`, `_detectar_rajada`).
- Esquema: `db/init/21_entidades_padroes.sql` (parte 4).
- Relacionados: `mesa_inquerito.md` (entidades criadas a partir de uma detecção, mesmo domínio
  `tp_origem`/`id_origem`), `investigacao_celular.md` (fontes de texto: conversas, notas,
  navegação, arquivos do aparelho e o OCR em `ARQ_TB01.TX_OCR`), `permissoes_delegacia.md`.