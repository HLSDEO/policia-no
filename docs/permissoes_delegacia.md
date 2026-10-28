# Permissões e multidelegacia

Este documento reúne os níveis de **isolamento de dados** do sistema: o isolamento por
**delegacia** (multidelegacia, antes mesmo de o inquérito existir), o isolamento por
**inquérito** (cada um com sua lista de permissões individuais) e os **perfis de usuário**
que liberam capacidades extras — mesmo formato dos demais documentos (`docs/`).

| Seção | Cobre | Rotas |
|---|---|---|
| [Multidelegacia](#multidelegacia) | delegacias, subpastas em `data/`, controle de uso de IA por unidade | `api/rotas/delegacias.py`, `delegacia_permitida`, `api/funcoes/ia_delegacia.py` (`bloqueio_ia`) |
| [Permissões por inquérito](#permissões-e-isolamento-por-inquérito) | criador, acesso a listagens e a dados, gestão de permissões por inquérito | `api/rotas/investigacao.py`, `investigacao_utils.py` |
| [Caso de inteligência](#caso-de-inteligência) | o "caso" como inquérito com flag — mesmas permissões do inquérito | `api/rotas/investigacao.py`, `investigacao_utils.py` |

## Multidelegacia

### Visão geral

O sistema separa os arquivos de investigação por **delegacia**: usuários são associados a uma
delegacia (`USR_TB01_USUARIOS.ID_DELEGACIA`) e os arquivos `.ufdr`, documentos, RIFs, extratos e
pastas-caso do IPED ficam em **subpastas** de `data/` com o nome da delegacia (ex.: `data/DELECOR/`). Além do
isolamento por inquérito (ver abaixo), é o isolamento **por unidade antes do inquérito
existir**: quem lista/extrai arquivos só enxerga a pasta da própria delegacia — a pasta é
compartilhada por todos os usuários no mesmo container.

`data/<delegacia>/` é a pasta de **entrega**: terminada a importação, o arquivo desce para
`data/<delegacia>/<inquérito>/<categoria>/` e some do seletor — ver
[organizacao_arquivos.md](organizacao_arquivos.md).

O segundo controle por delegacia é **quem pode usar a IA do sistema**: cada delegacia tem o flag
`IN_IA_ATIVA`, e a IA é bloqueada (403) pelos **dois lados** — para usuários cuja delegacia está com
a IA desativada **e** sobre inquéritos de uma delegacia nessa situação, independentemente de
terem ou não acesso ao inquérito. O escopo exato do que esse flag alcança (e do que ele **não**
alcança) está detalhado em [IA por delegacia](#ia-por-delegacia).

### Fluxo de uso

1. **Cadastro/edição de usuário** (`cadastro.html`, `usuarios.html`): o seletor de delegacia é
   populado por `GET /api/delegacias` (rota pública).
2. **Admin** gerencia delegacias na tela `web/delegacias.html` (CRUD) e controla se a IA está
   ativa para a unidade (checkbox "IA ativa (permite acesso ao Prompt de IA)").
3. Na tela **Investigação**, o investigador escolhe (ou tem travada) a subpasta da própria
   delegacia; os arquivos das demais delegacias não aparecem na listagem.
4. Usuário de delegacia com `IN_IA_ATIVA = false` — ou qualquer usuário abrindo um inquérito
   de uma delegacia assim — recebe `403` em todas as rotas de IA; o bloco/resposta diz "IA
   desativada para \<DELEGACIA\>." (e na front, os botões de IA aparecem desabilitados com essa
   mesma frase no tooltip).

### Como funciona

**Tabela e associação** (`db/init/07_seguranca_usuarios.sql`):

- `SEGURANCA.DELEGACIAS`: `ID_DELEGACIA` (identity), `NO_DELEGACIA` (único, 300), `IN_IA_ATIVA`
  (`BOOL` default `true`), `DT_CRIACAO`. O arquivo semeia 13 delegacias (DELECOR, DELEFAZ, DRE,
  DELECIBER, DELEPAT, DMA, DELEPREV, DELINST, FICCO/ES, DPF/CIT/ES, DPF/SMT/ES, SIP, DRPJ).
- `USR_TB01_USUARIOS.ID_DELEGACIA` ganha FK `FK73` para `DELEGACIAS`. `obter_usuario` e
  `validar_sessao` (em `auth_utils.py`) fazem `LEFT JOIN` e devolvem `no_delegacia` junto do
  usuário.
- Associação feita em `api/rotas/usuarios.py`: no `POST /api/usuarios` (criar), no
  `PUT /api/usuarios/{id}` (editar) e no `PUT /api/usuarios/{id}/delegacia`; `id_delegacia`
  `null` desassocia. O autocadastro também envia `id_delegacia` (`POST /api/auth/cadastro`).

**Rotas** (`api/rotas/delegacias.py`):

| Rota | Acesso | Ação |
|---|---|---|
| `GET /api/delegacias` | público | lista `id_delegacia, no_delegacia, in_ia_ativa, dt_criacao`, por nome — usada no cadastro, na edição de usuários e na front para saber se a IA da unidade está ativa |
| `POST /api/delegacias` | admin | cria; nome em maiúsculas; `409` se duplicado |
| `PUT /api/delegacias/{id}` | admin | renomeia e ajusta `IN_IA_ATIVA` |
| `DELETE /api/delegacias/{id}` | admin | desassocia os usuários (`ID_DELEGACIA = NULL`) e apaga a delegacia |

#### IA por delegacia

**O portão único** (`api/funcoes/ia_delegacia.py`): todo consumidor de IA passa por
`bloqueio_ia(usuario, id_inquerito=None)`, que devolve o **nome da delegacia que
barra** a chamada (ou `None`). Ele confere os **dois lados**, e basta um negar:

| Lado | O que olha | Como resolve |
|---|---|---|
| **quem pede** | delegacia do usuário logado | `no_delegacia` da sessão (`validar_sessao`) |
| **sobre o quê** | delegacia dona do inquérito | `delegacia_do_inquerito`: `COALESCE(INQ_TB01.NO_DELEGACIA, delegacia do criador, delegacia de algum usuário com permissão)` — a **mesma** ordem de resolução que `listar_inqueritos` usa no rótulo do card, para o botão nunca discordar do nome exibido |

`delegacias_sem_ia()` lê a tabela inteira (uma dezena de linhas) a cada checagem, em vez de manter
cache a invalidar no CRUD; a comparação é por nome **normalizado** (`strip().upper()`), porque o
nome também chega pela URL do front e fica gravado em `INQ_TB01.NO_DELEGACIA`. Usuário ou inquérito
**sem** delegacia definida passam — não há o que bloquear. O `403` diz
"IA desativada para \<DELEGACIA\>." (`erro_ia_desativada`).

**Por que os dois lados**: antes o backend só olhava o usuário e o front (`investigacao.js`) só
olhava o **seletor de delegacia da página** — que é filtro de pasta, não identidade. Um admin com
"DELECIBER" selecionado via o botão "Prompt de IA" cinza e todo o resto da tela (prompt específico,
bolha do WhatsApp, `inqueritos.html`) abrindo a IA normalmente. As duas respostas se
contradiziam porque respondiam a perguntas diferentes.

**Onde entra**:

| Consumidor | Rotas | Como |
|---|---|---|
| Chat de IA do inquérito | todas as `.../ia/...` de `api/rotas/ia.py` | `exigir_ia_ativa` como dependency do **router inteiro** (lado usuário) + `exigir_acesso_ia_inquerito` nas rotas com `id_inquerito` no path e `exigir_ia_inquerito` dentro de `_exigir_acesso_via_conversa_ia` e do download de relatório (lado inquérito) |
| **Análise de documento anexado** | `POST .../documentos/{id}/analisar` | `exigir_ia_inquerito`; no anexo (`POST /api/investigacao/extrair-documento`) a análise é efeito colateral, então é **pulada em silêncio** com `bloqueio_ia` — o documento fica anexado do mesmo jeito |
| **Busca automática de entidades** (Mesa) | `POST .../busca-automatica` | `exigir_acesso_ia_inquerito`. O `GET` do histórico fica livre: é polling de status, não chama IA |

**Dupla checagem (delegacia + inquérito)**: sobre o flag, cada rota ainda exige acesso ao
inquérito — as que recebem `id_inquerito` no path usam `exigir_acesso_ia_inquerito` (que chama
`exigir_acesso_inquerito`, com a auditoria que ela grava, e depois o flag); as que só recebem
`id_conversa_ia` (`_exigir_acesso_via_conversa_ia`, em `ia.py`) resolvem o inquérito dono da
conversa (`obter_id_inquerito_de_conversa_ia`, em `ia_utils.py`), chamam
`usuario_tem_acesso_inquerito` e então o flag. Os dois portões são independentes e ambos precisam
passar.

**O que o flag ainda NÃO alcança**:

- **indexação semântica** (embeddings via Ollama, `embed_utils.py` — roda na extração, sem
  consulta por usuário e sem provedor externo de chat);
- capacidades **locais** (OCR de imagens, classificação CLIP, comparação de rostos), que não
  usam o provedor externo.

Em resumo: o flag é o interruptor **"esta delegacia usa a IA do sistema"** — vale para as pessoas
da unidade e para os inquéritos dela.

**Front** (nenhuma tela repete a regra — ou recebe a decisão pronta, ou pergunta ao backend):

- `listar_inqueritos` devolve **`ia_ativa`** por linha, já combinando os dois
  lados. `web/js/investigacao.js` (`iaBloqueada`/`desabilitarBotaoIa`/`botaoPromptIa`) desabilita
  com isso o "Prompt de IA" do card e os sete "Prompt de IA específico" (IPL, IRPF, documento, RIF,
  SIMBA, Nuvem IPED, aparelho); `web/js/inqueritos.js` (`botaoIa`)
  faz o mesmo nos dois modos de visualização (carrossel e lista);
- `web/js/ia.js` — a tela sonda a listagem de conversas e, no `403`, troca o
  chat pelo bloco com a frase que veio do backend;
- `web/js/utils/ia_flutuante.js` — a bolha flutuante de IA (páginas de dados do inquérito) não
  aparece: o teste de abertura recebe `403` e o botão simplesmente não é exibido;
- `web/js/mesa_inquerito.js` (`conferirIaDisponivel`) — na Mesa, "Buscar entidades (IA)" e
  "Relacionar manuais (IA)" ficam travados no `403` (a flag `iaBloqueada` impede que o polling de
  status reabilite os botões).

**Subpastas em `data/`** (`api/funcoes/investigacao_utils.py`):

- `_sanitizar_delegacia(nome)` converte o nome da delegacia em nome de pasta: maiúsculas, `/` e
  `\` viram espaço, remove tudo que não for `A-Z0-9 _-`, espaços viram hífen — `'FICCO/ES'` vira
  `'FICCO'`.
- `delegacia_permitida(delegacia_pedida, usuario)` sanitiza e confere: **admin** enxerga qualquer
  delegacia; **usuário comum** só a própria (`no_delegacia` da sessão) — pedir outra, ou nenhuma
  quando tem uma cadastrada, é `403 "Sem permissão para acessar arquivos de outra delegacia."`.
  É usada em toda rota que abre o seletor de `data/` ou dispara uma importação: listar `.ufdr` e
  extrair, anexar documento, RIF, SIMBA e Nuvem IPED (`api/rotas/investigacao.py`) e o
  seletor de documentos (`api/rotas/documentos.py`: `GET /api/documentos/arquivos`).
- `_subpasta_delegacia(subpasta)` resolve `PASTA_DADOS / subpasta` (raiz `data/` se vazia).

**Como a subpasta afeta listar arquivos e documentos**:

- `listar_arquivos_ufdr(subpasta)` faz `glob("*.ufdr")` **só na subpasta** — o seletor da tela de
  Investigação mostra apenas os arquivos da delegacia do usuário (ou da raiz, para quem não tem
  delegacia).
- `listar_arquivos_diversos(subpasta)` (`api/funcoes/documentos_utils.py`) idem para documentos
  PDF/CSV/XLSX/DOCX; a rota `GET /api/documentos/arquivos` (`api/rotas/documentos.py`) passa o
  parâmetro por `delegacia_permitida`, igual às rotas de investigação — usuário comum não lista a
  pasta de outra delegacia.
- RIF: `rif_utils.listar_rifs_disponiveis` considera **subpastas com 'RIF' no nome** dentro da
  subpasta da delegacia.
- SIMBA (`simba_utils`), Nuvem IPED (`iped_utils`), IPL e relatórios seguem o mesmo padrão de
  `_subpasta_*`.
- **Leitura de binários não depende da subpasta**: `_localizar_ufdr(no_arquivo)` procura o `.ufdr`
  na raiz **e em todas as subpastas imediatas** (o banco nem sempre guarda a subpasta original),
  então anexos/mídias continuam sendo servidos mesmo se o arquivo estiver na pasta da delegacia.
  O badge "Arquivo disponível/ausente" (`_situacao_arquivo_ufdr`) confere na subpasta primeiro e
  só então varre as demais.
- Restaurar `.ufdr` de caminho externo (`restaurar_arquivo_ufdr`) é confinado a `data/` e, para
  não-admin, à subpasta da própria delegacia.

### Modelo de dados

| Tabela | Colunas | Observações |
|---|---|---|
| `SEGURANCA.DELEGACIAS` | `ID_DELEGACIA`, `NO_DELEGACIA` (único), `IN_IA_ATIVA`, `DT_CRIACAO` | seed em `07_seguranca_usuarios.sql` |
| `SEGURANCA.USR_TB01_USUARIOS` | `ID_DELEGACIA` (FK `FK73`) | associação do usuário à unidade |

## Permissões e isolamento por inquérito

### Visão geral

Cadeia de custódia: a IA e as telas **nunca** cruzam dado de um inquérito ao responder sobre
outro. Cada inquérito tem um **criador** (`INQ_TB01_INQUERITOS.ID_USUARIO_CRIADOR`) e uma **lista
de permissões adicionais** em `INVESTIGACAO.INQ_TB03_PERMISSOES` (nome exato conferido em
`db/init/08_inqueritos.sql`). O acesso é checado **no backend em toda rota de dados**
(`exigir_acesso_inquerito` / `exigir_gestor_inquerito`, em `api/funcoes/investigacao_utils.py`)
contra a identidade da **sessão** — não contra parâmetros da URL: trocar `inquerito`/`extracao` na
URL não dá acesso, e o servidor responde `403` para não-admin sem permissão. **Admin enxerga
tudo.** O mesmo filtro vale na convergência entre inquéritos, dos dois lados do par — e no chat
de IA, que não lista nem responde sobre conversas de inquérito a que o usuário não tem acesso.

Quem decide o acesso é uma única função (`usuario_tem_acesso_inquerito`): admin → sempre; senão,
ser o criador ou ter linha em `INQ_TB03_PERMISSOES`. "Gerenciar permissões" é o nível acima
(`eh_gestor_inquerito`): admin, criador ou usuário com `IN_ADMIN = true` concedido. Toda decisão
(permitida ou negada) é gravada em auditoria de acesso (ver `auditoria.md` e
`registrar_acesso` abaixo).

**Acesso a dado ≠ gestão de permissões**: os dois níveis protegem coisas diferentes. Apagar o
inquérito, renomear a ilustração e as três rotas de permissões exigem **gestor**
(`exigir_gestor_inquerito`). Mas apagar um RIF, um extrato SIMBA, um IPL ou uma conversa de IA —
`DELETE .../rifs/{id_rif}`, `DELETE .../simba/{id_extrato}`, `DELETE
.../inqueritos-pdf/{id_ipl}`, `DELETE .../ia/conversas/{id_conversa_ia}` — passa só pelas
checagens inline (`_exigir_acesso_via_rif`/`_exigir_acesso_via_simba`/`_exigir_acesso_via_ipl`/
`_exigir_acesso_via_conversa_ia`, todas chamando `usuario_tem_acesso_inquerito`), o mesmo nível
de quem só lê. O comentário da própria rota de RIF é explícito sobre isso: "remove um RIF e
todas as suas linhas + o PDF vinculado (**qualquer usuário com acesso ao inquérito**)". Ou seja:
qualquer investigador com acesso ao inquérito — não só o gestor — pode apagar um documento
inteiro dele.

### Fluxo de uso

1. Ao extrair um `.ufdr` para um número de inquérito que não existe, quem extrai vira o **criador**
   (`_obter_ou_criar_inquerito`).
2. Se o inquérito já existe e é de outro usuário, quem extrai **ganha acesso automaticamente**
   (`INSERT ... ON CONFLICT DO NOTHING` em `INQ_TB03_PERMISSOES`) — é assim que alguém "se junta"
   a um inquérito existente. (Sem revogação automática: o acesso concedido dessa forma só sai por
   gestão manual, como qualquer outro.)
3. O gestor (criador, admin ou usuário com `IN_ADMIN` concedido) abre **Gerenciar permissões** no
   card do inquérito (`web/investigacao.html`) — o botão só aparece para quem é gestor (o backend
   devolve `pode_gerenciar` na listagem) — e:
   - adiciona/remove usuários — `GET/POST/DELETE
     /api/investigacao/inqueritos/{id_inquerito}/permissoes`;
   - concede/revoga o papel de **Admin** do inquérito por linha (toggle na modal).
   A tela avisa: "O criador é automaticamente administrador."
4. Nas rotas de dados, o acesso é conferido rota a rota; sem permissão → `403` (e a tentativa
   fica registrada como negada na auditoria de acesso).

### Como funciona

**Modelo** (`db/init/08_inqueritos.sql`):

- `INQ_TB01_INQUERITOS` ganha `ID_USUARIO_CRIADOR` (FK `FK80` → `USR_TB01_USUARIOS`).
- `INQ_TB03_PERMISSOES`: `ID_PERMISSAO` (identity), `ID_INQUERITO` (FK `FK81`), `ID_USUARIO`
  (FK `FK82`), `IN_ADMIN` (default `false` — concede também a **gestão** de permissões),
  `DT_CONCESSAO`; `UNIQUE (ID_INQUERITO, ID_USUARIO)` (`UQ03`); índice `IX36`. Admin global é
  checado **em código**, não gravado aqui.

**A decisão única de acesso** (`investigacao_utils.py`):

- `usuario_tem_acesso_inquerito(id, usuario)` — `True` se admin, criador ou com
  permissão concedida; a consulta é um único `SELECT 1 ... WHERE i.id_inquerito = %s AND
  (i.id_usuario_criador = %s OR EXISTS(... INQ_TB03_PERMISSOES p ...))`. É por onde passa
  **toda** decisão de acesso; grava auditoria em `registrar_acesso` **nos dois caminhos** — o
  admin também é auditado (permitido=true), porque é justamente de quem tem acesso que o rastro
  precisa dar conta.
- `exigir_acesso_inquerito` — dependency das rotas com `id_inquerito` no path
  (dezenas de rotas em `investigacao.py`, `ia.py`, `entidades.py`, `relatorios.py`,
  `comparar_rosto.py`, `busca_automatica.py` etc.); `403 "Sem permissão para este inquérito."`.
- ~20 **checagens inline** (`usuario_tem_acesso_inquerito` chamado dentro da rota) nas que só
  recebem `id_conversa`/`id_anexo`/`id_contato`/`id_marcacao`/`id_status` e derivam o inquérito
  (`obter_id_inquerito_de_conversa`, `_exigir_acesso_via_conversa`, `_exigir_acesso_via_contato`
  etc. — abrir conversa, baixar mídia, rotas de entidades/relatórios com `id` no corpo passam
  por elas).

**Gestão do inquérito** (`eh_gestor_inquerito` e `exigir_gestor_inquerito`):

- admin global **ou** criador **ou** permissão com `in_admin = true` (dois `SELECT 1` unidos —
  criador OU linha de permissão com `in_admin`); também auditorado.
- Protegem as rotas mais graves, todas em `api/rotas/investigacao.py`: apagar o inquérito,
  renomear, definir a delegacia dele e as **três rotas de permissões**:
  - `GET .../permissoes` → lista `listar_permissoes_inquerito` (id_usuario, no_usuario,
    no_login, in_admin, dt_concessao; **não inclui o criador**);
  - `POST .../permissoes` → `conceder_permissao(id, id_usuario, in_admin)` — **idempotente**:
    `INSERT ... ON CONFLICT (id_inquerito, id_usuario) DO UPDATE SET in_admin = %s` (o toggle de
    Admin da modal é o mesmo POST reaproveitado);
  - `DELETE .../permissoes/{id_usuario}` → `revogar_permissao` (`DELETE`; `404` se não havia
    permissão).
- O `403` do gestor diz: "Apenas o criador do inquérito ou um administrador pode gerenciar
  permissões."

**Auto-grant na extração** (`_obter_ou_criar_inquerito`): número já existente + quem
extrai não é o criador → `INSERT INTO INQ_TB03_PERMISSOES ... ON CONFLICT DO NOTHING` — o usuário ganha acesso de investigador (sem `in_admin`), sem depender do
gestor.

**Listagens restritas** — `_filtro_acesso_sql(usuario, alias)` produz o fragmento SQL
das listagens (vazio para admin; senão `alias.id_usuario_criador = %s OR EXISTS(...)` em
`INQ_TB03_PERMISSOES`). Usado em:

- `listar_extracoes` — modal "todas as extrações";
- `listar_inqueritos` — seção "Investigações" (um card por inquérito);
- convergência (`canonicas_utils.py` — ver abaixo).
Além do filtro, ambas as listagens calculam `pode_gerenciar` no SQL (mesmo predicado de gestão:
criador ou permissão com `in_admin`) — é o que decide na front se o botão "Gerenciar permissões"
aparece no card.

**Isolamento na convergência** (`api/funcoes/canonicas_utils.py`): `listar_convergencia_inquerito`
aplica `_filtro_acesso_sql` no lado **do inquérito relacionado** (`i2`);
`listar_convergencia_global` aplica **nos dois lados** do par (`i_a`/`i_b`):
ninguém descobre a existência de um inquérito a que não tem acesso — dois investigadores podem
estar sentados sobre a mesma convergência sem vê-la; quem enxerga o cruzamento inteiro é o admin.

**IA do inquérito sob as mesmas regras**: o chat de IA herda os dois portões — a delegacia
(`bloqueio_ia`, ver acima) e o inquérito (`exigir_acesso_inquerito` / `usuario_tem_acesso_inquerito`).
Além disso, as fontes marcadas no painel "Fontes" e os escopos de conversa
(RIF/extrato/IPL/extração/documento) são validados contra o inquérito em `ia_utils.py`: itens de outro inquérito não entram nem no
contexto nem na resposta.

**Identidade da sessão, não da URL**: o usuário vem da dependency `exigir_aprovado` (sessão
válida + aprovado), não de parâmetros do path; a checagem usa `usuario["id_usuario"]` do banco.

**Auditoria de acesso** (`api/funcoes/auditoria_acesso.py`): `registrar_acesso(id_usuario,
id_inquerito, permitido)` grava (permitido **ou negado**) em
`SEGURANCA.USR_TB05_ACESSOS_INQUERITO`, agregando por **janela de hora** (primeira chamada da hora
insere; as demais somam `QT_ACESSOS` e atualizam `DT_ULTIMO_ACESSO` — uma tela de polling vira uma
linha com contador alto, não centenas). A falha da auditoria nunca derruba o acesso que ela
registra. Detalhes em `auditoria.md`.

### Modelo de dados

| Tabela | Colunas principais | Papel |
|---|---|---|
| `INVESTIGACAO.INQ_TB01_INQUERITOS` | `ID_INQUERITO`, `NU_INQUERITO` (único), `IN_CASO_INTELIGENCIA`, `ID_USUARIO_CRIADOR` | o inquérito e seu criador (`IN_CASO_INTELIGENCIA = true` marca um caso de inteligência — ver [abaixo](#caso-de-inteligência)) |
| `INVESTIGACAO.INQ_TB03_PERMISSOES` | `ID_PERMISSAO`, `ID_INQUERITO`, `ID_USUARIO`, `IN_ADMIN`, `DT_CONCESSAO` | permissões adicionais (além do criador); `UNIQUE` por par inquérito/usuário |
| `SEGURANCA.USR_TB05_ACESSOS_INQUERITO` | `ID_USUARIO`, `ID_INQUERITO`, `NO_RECURSO`, `TP_METODO`, `IN_PERMITIDO`, `CO_IP`, `DT_JANELA`, `QT_ACESSOS`, `DT_ULTIMO_ACESSO` | auditoria de acessos (tentados e negados), agregada por hora |


## Caso de inteligência

### Visão geral

O "caso" não tem mais desenho próprio de permissão: um **caso de inteligência é um inquérito com a flag `IN_CASO_INTELIGENCIA`** (`INQ_TB01_INQUERITOS`, migração `0033`), então valem para ele as mesmas regras do inquérito — criador, `INQ_TB03_PERMISSOES`, auto-grant na extração, auditoria de acesso e gestão (ver [acima](#permissões-e-isolamento-por-inquérito)).

### Fluxo de uso

1. Só os perfis de **inteligência** — chefia (`inteligencia`) e agente (`agente_inteligencia`), ambos com `pode_caso_inteligencia` em `api/funcoes/auth_utils.py` — veem o seletor "Importar como" em `web/importacao.html` e podem importar como caso — as 5 rotas de importação recusam `caso_inteligencia: true` com 403 para os demais perfis.
2. No modo caso, o número é gerado `CASO-AAAA.NNNN` e quem importa vira o **criador** do inquérito, como em qualquer extração (`_obter_ou_criar_inquerito`).
3. A contagem `qt_casos` do painel (`estatisticas_delegacia`) conta os inquéritos com a flag, no mesmo escopo de acesso e filtro de delegacia dos demais números.

### Modelo de dados

| Tabela | Colunas principais | Papel |
|---|---|---|
| `INVESTIGACAO.INQ_TB01_INQUERITOS` | `IN_CASO_INTELIGENCIA` (`BOOL` default `false`) | `true` marca um caso de inteligência (número `CASO-AAAA.NNNN` em `NU_INQUERITO`) |

O subsistema antigo de casos (`CAS_TB01`–`CAS_TB12`, `CAS_TB10_PERMISSOES`, páginas `caso_*.html`/`ia_caso.html`, rotas `/api/documentos/casos/*`) foi removido na migração `0033`; os dados antigos foram descartados.

## Referências

- `api/rotas/delegacias.py` (GET público + CRUD admin), `api/rotas/usuarios.py` (associação),
  `api/rotas/investigacao.py` (`delegacia_permitida` em arquivos/extração/RIF/SIMBA/Nuvem IPED,
  rotas de permissões, `pode_gerenciar` nas listagens, `DELETE .../rifs/{id_rif}`
  `/simba/{id_extrato}`/`inqueritos-pdf/{id_ipl}` protegidas só por acesso comum ao inquérito),
  `api/rotas/documentos.py` (`delegacia_permitida` no seletor de arquivos), `api/rotas/ia.py`
  (`exigir_ia_ativa` no router, `_exigir_acesso_via_conversa_ia`).
- `api/funcoes/ia_delegacia.py` — o portão único do `IN_IA_ATIVA`: `bloqueio_ia`,
  `delegacias_sem_ia`, `delegacia_do_inquerito`, `normalizar_delegacia`,
  `erro_ia_desativada` e as dependencies `exigir_ia_ativa` / `exigir_acesso_ia_inquerito` /
  `exigir_ia_inquerito`. `listar_inqueritos` usa as duas primeiras para devolver `ia_ativa` por linha.
- `api/funcoes/investigacao_utils.py` — `delegacia_permitida`, `_sanitizar_delegacia`,
  `listar_arquivos_ufdr`, `_localizar_ufdr`, `_situacao_arquivo_ufdr`,
  `usuario_tem_acesso_inquerito`, `eh_gestor_inquerito`, `exigir_acesso_inquerito`,
  `exigir_gestor_inquerito`, `_filtro_acesso_sql`, `listar_permissoes_inquerito`,
  `conceder_permissao`, `revogar_permissao`, `_obter_ou_criar_inquerito` (auto-grant + geração do
  número do caso de inteligência).
- `api/funcoes/auth_utils.py` — perfis e capacidades (`pode_caso_inteligencia` para os perfis de
  chefia de inteligência e de inteligência).
- `api/funcoes/canonicas_utils.py` — isolamento na convergência (`_filtro_acesso_sql` dos dois
  lados).
- `api/funcoes/auditoria_acesso.py` — `registrar_acesso` (inclui tentativas negadas).
- `api/funcoes/ia_utils.py` — `obter_id_inquerito_de_conversa_ia`; escopos validados contra o
  inquérito.
- Outros consumidores de IA **fora** do flag de delegacia: `api/rotas/busca_automatica.py`,
  `api/funcoes/embed_utils.py` (indexação semântica).
- `db/init/07_seguranca_usuarios.sql` (delegacias, usuários, auditoria de acesso),
  `08_inqueritos.sql` (`INQ_TB03_PERMISSOES`, `IN_CASO_INTELIGENCIA`), migração
  `0033_caso_inteligencia_inquerito.sql`.
- Front: `web/delegacias.html` (+ `js/delegacias.js`), `web/investigacao.html` (modal "Gerenciar
  permissões"; botões de IA desabilitados),
  `web/js/investigacao.js` (`iaBloqueada`, `desabilitarBotaoIa`, `botaoPromptIa`, toggle
  Admin/Remover, `abrirModalPermissoes` sobre `urlBasePermissoes`), `web/js/inqueritos.js` (`botaoIa`),
  `web/js/ia.js` (tela com IA desativada), `web/js/utils/ia_flutuante.js`
  (bolha oculta), `web/js/mesa_inquerito.js` (`conferirIaDisponivel`), `web/cadastro.html`,
  `web/usuarios.html`.
- Documentos relacionados: `documentos_diversos.md` (caso de inteligência),
  `autenticacao.md` (login/cadastro/administração do usuário com sua
  delegacia), `auditoria.md` (rastro de quem acessou), `mesa_inquerito.md` (a mesa da
  delegacia), `investigacao_celular.md` (extração para a subpasta), `chat_ia.md` (provedor e
  auditoria da IA).
