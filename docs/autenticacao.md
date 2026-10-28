# Autenticação, usuários e sessão

Este documento cobre o ciclo de vida da conta de usuário: **login/sessão**, **autocadastro**, **redefinição de senha** e **administração de usuários** — um arquivo só, com as quatro features no mesmo formato dos demais documentos (`docs/`).

| Seção | Tela | Rotas |
|---|---|---|
| [Login e sessão](#login-e-sessão) | `login.html` | `POST /api/auth/login`, `logout`, `GET /api/auth/eu` |
| [Autocadastro](#autocadastro) | `cadastro.html` | `POST /api/auth/cadastro` |
| [Entrada pela API-PF](#entrada-pela-api-pf) | `login.html` (aba API-PF), `api_pf.html` | `GET /api/auth/provedores`, `POST /api/auth/primeiro-acesso`, `/api/config/api-pf` |
| [Redefinição de senha](#redefinição-de-senha) | `redefinir_senha.html` | `/api/auth/esqueci`, `/api/auth/redefinir` |
| [Administração de usuários](#administração-de-usuários) | `usuarios.html` | `api/rotas/usuarios.py` |

## Login e sessão

### Visão geral

A autenticação é **server-side**: `POST /api/auth/login` gera um token opaco de 256 bits,
devolvido em cookie `httpOnly`/`samesite=lax`; no banco (`SEGURANCA.USR_TB02_SESSOES`) fica apenas o
**hash SHA-256** do token — diferente de JWT, é possível revogar uma sessão específica (logout) ou
todas de um usuário (desativação) sem blocklist. A senha é guardada com **bcrypt** em
`SEGURANCA.USR_TB01_USUARIOS.TX_SENHA_HASH` (`api/funcoes/auth_utils.py`). O login inicial é
`admin` / `admin1234` (semeadura em `db/init/07_seguranca_usuarios.sql` — a senha é pública no código-fonte e
deve ser trocada no primeiro acesso).

A tela de login tem **duas abas**, montadas a partir de `GET /api/auth/provedores`:
**PolícIA** (a senha é conferida aqui, é o padrão e funciona sem rede externa) e **API-PF** (a
senha vai para o OpenID Connect da PF). O cadastro é **um só** para as duas — ver
[Entrada pela API-PF](#entrada-pela-api-pf).

### Fluxo de uso

1. `web/login.html` → `web/js/login.js` envia `POST /api/auth/login` com
   `{login, senha, provedor}` (`provedor` ausente = `POLICIA`).
2. Resposta `{"ok": true, "no_usuario", "in_admin", "in_aprovado"}` + cookie `analyser_sessao`;
   o front redireciona para `index.html` (ou o destino de `?next=`).
3. Falha de credencial → `401 "Login ou senha inválidos."` (a mensagem não distingue login
   inexistente de senha errada, para não denunciar quem tem conta).
4. Limite de tentativas estourado → `429 "Muitas tentativas de login..."` com header `Retry-After`.
5. À saída, `POST /api/auth/logout` revoga o token no banco (`DELETE` em `USR_TB02_SESSOES`) e
   limpa o cookie (`web/js/utils/auth.js`).
6. `GET /api/auth/eu` devolve o usuário da sessão atual — é o que o front usa para saber se está
   logado; `web/js/login.js` pula o formulário se já houver sessão válida.

### Como funciona

**Sessão** (`api/funcoes/auth_utils.py`):

- `criar_sessao()` gera `secrets.token_urlsafe(32)` (~256 bits); o texto puro só existe naquele
  momento — no banco vai `SHA-256` em `ID_SESSAO_HASH` (PK), junto de `ID_USUARIO` (FK para
  `USR_TB01_USUARIOS`), `DT_CRIACAO` e `DT_EXPIRACAO` (`db/init/07_seguranca_usuarios.sql`).
- O cookie `analyser_sessao` é `httpOnly`, `samesite=lax`, `path=/` e `max_age` igual a
  `DURACAO_SESSAO` (12 h).
- A sessão é **deslizante**: `validar_sessao()` renova `DT_EXPIRACAO` para `now() + 12h` quando
  falta menos de `RENOVAR_SESSAO_A_CADA` (1 h) — no máximo ~1 escrita por hora por sessão, com o
  gate em SQL (`dt_expiracao < now() + DURACAO_SESSAO - RENOVAR_SESSAO_A_CADA`). A dependency
  `exigir_login` reemite o cookie a cada requisição autenticada para a validade no navegador
  acompanhar a renovação.
- `validar_sessao()` também filtra `u.in_ativo`: desativar um usuário não derruba as sessões
  abertas na hora, elas só param de validar na próxima checagem.
- Cadeia de dependências de autorização: `exigir_login` → `exigir_aprovado` → `exigir_admin`
  (em `auth_utils.py`; `exigir_aprovado` retorna `403` para cadastro pendente de aprovação).

**Limite de tentativas** (janela deslizante de 15 min sobre
`SEGURANCA.USR_TB04_TENTATIVAS_LOGIN`, `db/init/07_seguranca_usuarios.sql`):

| Escopo | Limite | Contra |
|---|---|---|
| por **login** | 5 falhas (`MAX_TENTATIVAS_LOGIN`) | força bruta contra uma conta específica |
| por **IP** | 30 falhas (`MAX_TENTATIVAS_IP`) | varredura que troca de login a cada palpite |

- O IP vem do header **`X-Real-IP`** posto pelo nginx (`_ip_cliente` em `api/rotas/auth.py`,
  truncado em 45 caracteres; sem o header, usa `request.client.host`). É seguro confiar no header
  porque a API não tem porta publicada no host — o único caminho até ela é o proxy, que o
  sobrescreve.
- A checagem (`segundos_bloqueio_login`, via consulta `_SQL_FALHAS_RECENTES`) acontece **antes**
  de conferir a senha — não gasta bcrypt com quem já estourou o limite, e o `Retry-After` devolve
  o tempo até a falha mais antiga da janela envelhecer.
- **Tentativa bloqueada não é gravada**: só entra em `USR_TB04` a tentativa que de fato conferiu a
  senha (`registrar_tentativa_login`). Senão, quem fica martelando empurraria a janela para frente
  sozinho e o bloqueio nunca expiraria.
- **O bloqueio é derivado, não gravado**: não existe estado de "conta bloqueada" para administrar;
  passada a janela sem falha nova, o acesso volta sozinho.
- **Login bem-sucedido zera a contagem daquele login**: a consulta ignora falhas anteriores ao
  último sucesso na janela (CTE `marco`); a contagem por **IP** não tem esse alívio (quem conhece
  uma credencial válida não pode zerar o contador do IP a cada acerto).
- **Expurgo de 90 dias** (`RETENCAO_TENTATIVAS`): `DELETE` oportunista a cada login bem-sucedido —
  a tabela serve também de trilha de auditoria ("tentaram entrar como fulano 40 vezes na
  madrugada"). O login digitado é gravado em `NO_LOGIN` (não o id do usuário), sem FK, porque a
  maior parte do ataque usa login inexistente.
- `autenticar()` atualiza `DT_ULTIMO_LOGIN` a cada sucesso e devolve o usuário sem o hash.

**Tema por usuário** (`db/init/07_seguranca_usuarios.sql`): `TP_TEMA` em `USR_TB01_USUARIOS`
(`'dark'` (default) ou `'light'`, com CHECK). `POST /api/auth/tema` grava a preferência do usuário
logado, `validar_sessao()` a devolve em `tp_tema`, e o front aplica com cache local em
`localStorage` (`web/js/utils/theme.js`) para não esperar a API.

### Modelo de dados

| Tabela | Colunas principais | Observações |
|---|---|---|
| `SEGURANCA.USR_TB01_USUARIOS` | `ID_USUARIO`, `NO_LOGIN` (único), `NO_USUARIO`, `TX_SENHA_HASH` (bcrypt, **anulável**), `IN_ADMIN`, `IN_ATIVO`, `IN_APROVADO`, `DT_CADASTRO`, `DT_ULTIMO_LOGIN`, `NO_EMAIL`, `TP_TEMA`, `ID_DELEGACIA`, `CO_PROVEDOR` | seed do admin em `07_seguranca_usuarios.sql`; `CO_PROVEDOR`/hash anulável em `35_autenticacao_api_pf.sql` |
| `SEGURANCA.USR_TB02_SESSOES` | `ID_SESSAO_HASH` (PK, SHA-256 do token), `ID_USUARIO` (FK), `DT_CRIACAO`, `DT_EXPIRACAO` | sem o texto puro do token |
| `SEGURANCA.USR_TB04_TENTATIVAS_LOGIN` | `ID_TENTATIVA`, `NO_LOGIN`, `CO_IP` (45), `IN_SUCESSO`, `DT_TENTATIVA` | índices `IX103` (login) e `IX104` (IP), ambos com `DT_TENTATIVA` |

## Autocadastro

### Visão geral

`web/cadastro.html` → `POST /api/auth/cadastro` cria um usuário **sem `IN_ADMIN` e sem
`IN_APROVADO`** e já abre sessão: a pessoa consegue logar, mas fica **sem acesso às telas** até um
administrador aprovar na tela **Usuários** (`web/usuarios.html`, rotas de `api/rotas/usuarios.py`,
que exigem admin). Senha mínima de 8 caracteres. O autocadastro existe porque a instalação pode ser
aberta (não há convite); a aprovação é o controle humano antes do acesso a qualquer dado.

### Fluxo de uso

1. `web/cadastro.html` exibe o formulário: **Nome**, **Login**, **E-mail** (opcional), **Delegacia**
   (seletor preenchido por `GET /api/delegacias`) e **Senha** (com `minlength="8"` no HTML e
   validação idêntica no backend).
2. `web/js/cadastro.js` envia `POST /api/auth/cadastro` com
   `{login, nome, senha, email, id_delegacia}`.
3. Sucesso: sessão aberta no mesmo fluxo do login (cookie `analyser_sessao`) e redirecionamento
   para a página inicial — onde o usuário vê o aviso de cadastro pendente de aprovação.
4. Um administrador abre **Usuários** → linha do usuário → aprova (ou revoga) via
   `POST /api/usuarios/{id_usuario}/aprovado`.
5. A partir daí, as dependencies de autorização passam a liberar o acesso às telas.

### Como funciona

- A rota (`api/rotas/auth.py`) chama `criar_usuario(login, nome, senha, admin=False,
  aprovado=False, email=..., id_delegacia=...)` de `api/funcoes/auth_utils.py`.
- Validações em `criar_usuario` (erros viram `400` com a mensagem da exceção):
  - login e nome obrigatórios; login normalizado (minúsculo, sem espaços nas pontas);
  - senha com pelo menos `TAMANHO_MINIMO_SENHA` (8) caracteres;
  - login já existente → `ValueError("Já existe um usuário com esse login.")`;
  - e-mail já existente (comparação por `lower`) → `ValueError("Já existe um usuário com esse
    e-mail.")`;
  - e-mail validado por `_normalizar_email` (regex deliberadamente frouxa — `RE_EMAIL` —, em
    minúsculo; o e-mail é a credencial de recuperação, ver a seção abaixo).
- O `INSERT` grava `IN_ADMIN = false` e `IN_APROVADO = false`; o campo `ID_DELEGACIA` é FK para
  `SEGURANCA.DELEGACIAS` (ver `permissoes_delegacia.md`).
- Pós-cadastro, `_abrir_sessao()` cria a sessão do mesmo jeito que o login (`criar_sessao` +
  cookie httpOnly); a resposta é `{"ok": true, "no_usuario", "in_admin": false,
  "in_aprovado": false}`.
- O acesso às funcionalidades é gateado pela dependency **`exigir_aprovado`**, que encadeia em
  `exigir_login`: `exigir_login` passa para o autocadastrado (ele precisa das rotas de status,
  troca de senha e logout), mas `exigir_aprovado` responde
  `403 "Cadastro pendente de aprovação do administrador."` em tudo o mais (`api/main.py` aplica
  `Depends(exigir_aprovado)` como dependency global de todos os routers de funcionalidade).
- Aprovação: `POST /api/usuarios/{id}/aprovado` (`definir_aprovado`) ajusta `IN_APROVADO` — pode
  ser reaproveitada para revogar o acesso de alguém aprovado. Usuário criado por um admin já
  nasce aprovado (`criar_usuario` padrão `aprovado=True`; coluna com `DEFAULT true` em
  `db/init/07_seguranca_usuarios.sql`).

### Modelo de dados

| Tabela | Colunas envolvidas | Observações |
|---|---|---|
| `SEGURANCA.USR_TB01_USUARIOS` | `ID_USUARIO`, `NO_LOGIN`, `NO_USUARIO`, `TX_SENHA_HASH`, `IN_ADMIN`, `IN_APROVADO`, `NO_EMAIL`, `ID_DELEGACIA`, `DT_CADASTRO` | `IN_APROVADO` adicionado em `07_seguranca_usuarios.sql` com `DEFAULT true` |
| `SEGURANCA.USR_TB02_SESSOES` | `ID_SESSAO_HASH`, `ID_USUARIO`, `DT_EXPIRACAO` | sessão aberta no cadastro |
| `SEGURANCA.DELEGACIAS` | `ID_DELEGACIA`, `NO_DELEGACIA` | lista pública `GET /api/delegacias` popula o seletor |

## Entrada pela API-PF

### Visão geral

A API-PF é uma **segunda forma de provar quem a pessoa é**, não um segundo cadastro. O login do
PolícIA continua sendo o principal: é o cadastro deste banco que decide quem entra, quem é
administrador, quem foi aprovado e de que delegacia a pessoa é. A senha digitada na aba **API-PF**
vai para o endpoint OpenID Connect (Keycloak) da PF, que responde com um **access token JWT**; o
login, o nome e o e-mail saem das claims desse token e viram (ou atualizam) uma linha em
`USR_TB01_USUARIOS`. **Nenhuma senha da PF é guardada aqui.**

Quem entra assim nasce **sem senha do PolícIA** (`TX_SENHA_HASH IS NULL`) e é obrigado a criar uma
no primeiro acesso — é ela que garante a entrada quando a rede da PF estiver fora, que é o ponto
de um sistema que roda dentro da delegacia. Junto da senha, a pessoa escolhe a **delegacia**.

O cadastro criado nasce **pendente de aprovação**, igual ao do autocadastro: a credencial da PF
prova *identidade*, não *autorização a ver inquérito* — quem libera continua sendo um
administrador, na tela Usuários.

### Fluxo de uso

1. `web/login.html` monta as abas a partir de `GET /api/auth/provedores`. Sem `API_PF_URL`
   configurada, a aba **API-PF** aparece desabilitada com o motivo; com uma forma de entrar só, a
   faixa de abas nem aparece.
2. A pessoa escolhe **API-PF** e envia `POST /api/auth/login` com
   `{login, senha, provedor: "API_PF"}`.
3. A API chama o endpoint da PF (`api/funcoes/api_pf_utils.py`). `400/401/403` viram o mesmo
   `401 "Login ou senha inválidos."` do caminho local; falha da integração (timeout, TLS, rede,
   HTTP inesperado) vira **`502` com o motivo** e **não** conta no limite de tentativas — uma
   indisponibilidade externa não pode bloquear contas legítimas.
4. Com o token em mãos, `sincronizar_usuario_api_pf()` cria ou atualiza o cadastro local e a
   sessão é aberta normalmente (mesmo cookie, mesma duração).
5. A resposta traz `primeiro_acesso: true` enquanto não houver senha local. `web/js/login.js`
   então abre a caixa **bloqueante** de `web/js/utils/primeiro_acesso.js` (senha + confirmação +
   delegacia) antes de entrar no sistema; ela não tem Cancelar, não fecha no Esc nem no clique
   fora — as saídas são criar a senha ou sair.
6. `POST /api/auth/primeiro-acesso` grava a senha e a delegacia. A partir daí a pessoa entra
   pelas **duas** abas.
7. Quem fechou o navegador no meio cai na mesma caixa em qualquer página interna:
   `GET /api/auth/eu` devolve `senha_local_pendente` e `web/js/utils/auth.js` carrega o script sob
   demanda.

### Como funciona

- **Provedores** (`PROVEDORES` em `api/rotas/auth.py`): a lista de abas é do servidor, não do
  front — assim nenhuma aba aparece oferecendo algo que a API não aceita.
  `GET /api/auth/provedores` é pública (é lida antes de existir sessão) e devolve só rótulo,
  descrição e `ds_indisponivel` — **nenhuma URL, client_id ou segredo**.
- **Requisição** (`api_pf_utils.autenticar`): `POST` `x-www-form-urlencoded` com `client_id`,
  `client_secret`, `grant_type`, o campo do login e o campo da senha (os dois últimos com o
  **nome** configurado na tela). `client_id`/`client_secret` vazios não são enviados — há
  instalação em que o cliente é público, e mandar o campo em branco faz o Keycloak recusar.
- **A assinatura do JWT não é conferida**, e isso é seguro *neste fluxo*: nós somos o cliente, o
  token acabou de chegar por TLS em resposta à nossa própria requisição, e a prova de que a senha
  estava certa é o próprio `200`. Se um dia o token passar a chegar de outro lugar (do navegador,
  de um header `Authorization`), a assinatura vira a única garantia e é preciso validar contra o
  JWKS do realm. O raciocínio completo está no cabeçalho de `api/funcoes/api_pf_utils.py`.
- **Sincronização** (`sincronizar_usuario_api_pf` em `auth_utils.py`): casa pelo **login**
  normalizado (`INSERT ... ON CONFLICT (no_login) DO UPDATE`), então quem já tinha conta aqui tem
  aquela linha atualizada, nunca uma segunda. O **nome** é regravado a cada login (a PF é a fonte
  da verdade sobre quem a pessoa é); o **e-mail** só entra se ainda não houver um (trocá-lo é
  trocar a credencial de recuperação, e isso continua sendo ato do próprio usuário); e-mail já
  usado por outro entra como `NULL`. **`IN_APROVADO`, `IN_ADMIN`, `IN_ATIVO`, `ID_DELEGACIA` e a
  senha local não são tocados** no reencontro — quem foi desativado ou rebaixado aqui não volta ao
  sistema por logar de novo na PF.
- **Senha local ausente não é senha em branco**: `_verificar_senha` devolve `False` para hash
  nulo, então o login pelo PolícIA recusa a conta enquanto a senha não for criada.
- **`definir_senha_inicial`** carrega `AND tx_senha_hash IS NULL` no próprio `UPDATE`: sem essa
  condição, a rota de primeiro acesso seria um caminho para trocar a senha de qualquer conta sem
  conhecer a anterior. Segunda chamada devolve `409`.
- **Configuração em tempo de execução**: os valores são lidos do `.env` a **cada** tentativa
  (`api_pf_utils.ler`), não de `os.environ` — o compose copia o `.env` para o ambiente do processo
  uma vez, na subida, então um valor salvo pela tela só valeria depois de um `docker compose up`.

### Administração → API-PF

`web/api_pf.html` + `web/js/api_pf.js` (rotas em `api/rotas/api_pf.py`, todas com
`Depends(exigir_admin)` no router). O formulário **não é fixo no HTML**: os campos vêm de
`GET /api/config/api-pf`, que devolve o catálogo `CAMPOS` de `api/funcoes/api_pf_utils.py` já com o
valor atual — campo novo lá aparece na tela sem tocar no front.

| Campo | Chave no `.env` | Padrão |
|---|---|---|
| URL do endpoint (POST) * | `API_PF_URL` | — (obrigatório) |
| client_id | `API_PF_CLIENT_ID` | vazio (não enviado) |
| client_secret | `API_PF_CLIENT_SECRET` | vazio (não enviado) |
| grant_type | `API_PF_GRANT_TYPE` | `password` |
| Campo do login no corpo | `API_PF_CAMPO_USUARIO` | `username` |
| Campo da senha no corpo | `API_PF_CAMPO_SENHA` | `password` |
| Timeout (segundos) | `API_PF_TIMEOUT` | `10` (1 a 300) |
| Conferir o certificado TLS | `API_PF_VERIFICAR_TLS` | `sim` |
| Claim do login | `API_PF_CLAIM_LOGIN` | `preferred_username` |
| Claim do nome | `API_PF_CLAIM_NOME` | `name` |
| Claim do e-mail | `API_PF_CLAIM_EMAIL` | `email` |

- O **client_secret nunca sai da API em texto claro**: a tela recebe só uma máscara no
  placeholder, e salvar com o campo em branco **mantém** o valor gravado — senão salvar o
  formulário sem redigitar o segredo o apagaria. Para apagá-lo de verdade, edite o `.env` do
  servidor.
- **Claim com caminho**: `claim_texto` aceita ponto (`resource_access.epol-server.roles`) e pega o
  primeiro item quando o valor é lista.
- **Sem a claim do nome o login falha** (`502` com o motivo): sem nome não dá para montar um
  cadastro utilizável, e usar o login no lugar encheria a tela Usuários de linhas
  indistinguíveis.
- **Conferir o certificado TLS**: desligar destrava a integração quando a cadeia da AC da PF não é
  reconhecida pelo container, mas a conexão deixa de provar que do outro lado está mesmo o
  servidor da PF — quem estiver no caminho da rede pode se passar por ele e ficar com o login e a
  senha de quem entrar. A saída correta é instalar a cadeia da AC no servidor.
- **Testar conexão**: `POST /api/config/api-pf/testar` com um login e uma senha de verdade mostra o
  que a API devolveu (login, nome, e-mail) **sem** abrir sessão nem criar usuário. A tentativa não
  entra em `USR_TB04_TENTATIVAS_LOGIN` — contá-la deixaria um administrador capaz de bloquear a
  conta de outra pessoa a partir desta tela.

### Modelo de dados

`db/init/35_autenticacao_api_pf.sql` (aplicar à mão em banco já existente — ver
`db/init/README.md`):

| Mudança em `SEGURANCA.USR_TB01_USUARIOS` | Por quê |
|---|---|
| `TX_SENHA_HASH` passa a aceitar `NULL` | quem chega pela API-PF ainda não tem senha daqui |
| `CO_PROVEDOR VARCHAR(20) NOT NULL DEFAULT 'POLICIA'` (`CK10`: `POLICIA`/`API_PF`) | **origem** do cadastro, não a forma de entrar: quem veio da API-PF e criou a senha local entra pelos dois caminhos |

## Redefinição de senha

### Visão geral

Senha esquecida se resolve por um **token de uso único** em
`SEGURANCA.USR_TB03_REDEFINICAO_SENHA` — mesmo desenho das sessões: no banco fica só o **hash
SHA-256** do token. Quem escolhe a senha nova é sempre o **próprio usuário**, na tela
`web/redefinir_senha.html`; nem o administrador fica sabendo qual é. Usar o token **encerra todas
as sessões** do usuário (senha esquecida pode significar conta comprometida). São dois caminhos
para emitir o token:

| Caminho | Como | Validade | Precisa de |
|---|---|---|---|
| **Administrador** | tela **Usuários** → **Redefinir senha** → gera o link e entrega ao usuário | 24 h (`VALIDADE_TOKEN_ADMIN`) | nada |
| **E-mail** | login → **Esqueci minha senha** → `POST /api/auth/esqueci` | 2 h (`VALIDADE_TOKEN_EMAIL`) | SMTP no `.env` + `NO_EMAIL` do usuário |

### Fluxo de uso

1. **Caminho do administrador** (`web/js/usuarios.js`): modal **Redefinir senha** →
   `POST /api/usuarios/{id_usuario}/redefinicao` (`{enviar_email: bool}`). A resposta traz
   `token`, `url`, `dt_expiracao`, `enviado_para` e `email_disponivel`. A tela mostra o **link**
   quando há `APP_URL` no `.env`; sem `APP_URL`, entrega o **código** (`resp.token`) para o
   usuário colar à mão.
2. **Caminho do e-mail**: em `redefinir_senha.html`, etapa "pedir" →
   `POST /api/auth/esqueci` com `{login_ou_email}` (aceita login **ou** e-mail). A resposta é
   **sempre a mesma**, com ou sem envio — o formulário público não pode virar um verificador de
   quem tem conta. Se a instalação não tiver SMTP (`GET /api/auth/recuperacao` →
   `{email: false}`), a tela mostra o aviso para procurar o administrador.
3. Com link/código em mãos: `GET /api/auth/redefinir/{token}` confere sem consumir (a tela avisa
   que expirou em vez de deixar digitar a senha em vão) e a etapa "senha" envia
   `POST /api/auth/redefinir` com `{token, senha_nova}`.
4. Sucesso: todas as sessões do usuário são encerradas; ele volta ao login e entra com a senha
   nova.

### Como funciona

- **Emissão** (`criar_token_redefinicao` em `api/funcoes/auth_utils.py`): `secrets.token_urlsafe(32)`;
  o token puro só existe no retorno — no banco vai `_hash_token()` (SHA-256, hex) como PK
  `ID_TOKEN_HASH`. Tokens **anteriores ainda não usados** do mesmo usuário são apagados na emissão
  (`DELETE ... WHERE id_usuario = %s AND dt_uso IS NULL`): cada novo pedido invalida o anterior.
- **Validação de uso único**: `redefinir_senha_com_token()` valida a senha nova **antes** de marcar
  o token (`UPDATE USR_TB03 SET dt_uso = now() WHERE ... dt_uso IS NULL AND dt_expiracao > now()
  AND usuário ativo ... RETURNING id_usuario`) — a senha é trocada e o `dt_uso` marcado na mesma
  transação; o `rowcount` do UPDATE é a garantia do uso único mesmo com dois cliques simultâneos.
  Ao final, `DELETE FROM USR_TB02_SESSOES WHERE id_usuario = ...` encerra todas as sessões.
- **Limite de pedidos**: `pedido_redefinicao_recente()` — 1 pedido do mesmo usuário a cada
  `INTERVALO_MINIMO_PEDIDO` (2 min), considerando só tokens de origem `'email'`. Evita que o
  formulário público inunde a caixa de outra pessoa.
- **Resposta idêntica**: `usuario_para_recuperacao()` devolve `None` para login/e-mail inexistente
  ou usuário inativo; contas sem `NO_EMAIL`, SMTP fora do ar e pedido repetido caem todos no mesmo
  `generico`. O envio em si (`enviar_email` em `api/funcoes/email_utils.py`) engole falhas e
  devolve `False` — a rota pública responde igual nos dois casos.
- **Sem `APP_URL`, entrega o código**: `link_redefinicao(token)` devolve `""` quando
  `APP_URL` não está no `.env`; tanto o e-mail (`enviar_redefinicao_senha`) quanto a tela de
  Usuários passam a mostrar o **código** em vez do link, para colar na mesma tela
  (`redefinir_senha.html` etapa "código").
- **Troca de e-mail exige senha atual**: `POST /api/auth/email` só grava (ou limpa) `NO_EMAIL`
  depois de `autenticar(login, senha_atual)` — o e-mail é a credencial de recuperação, e trocá-lo
  sem a senha atual transformaria uma sessão sequestrada em roubo permanente da conta. O e-mail é
  único por `lower()` (índice parcial `IX100_USR_TB01_EMAIL`).
- **Troca de senha logado** (sem token): `POST /api/auth/senha`, também exigindo a senha atual. A
  troca encerra **todas as sessões** do usuário na mesma transação (`definir_senha`) e o response
  já entrega um cookie de sessão **novo**: o identificador emitido antes da troca deixa de existir
  no servidor e não abre mais nada — inclusive nas mãos de quem o tenha interceptado.

### Modelo de dados

`SEGURANCA.USR_TB03_REDEFINICAO_SENHA` (`db/init/07_seguranca_usuarios.sql`):

| Coluna | Observações |
|---|---|
| `ID_TOKEN_HASH` | PK, SHA-256 do token (texto puro nunca fica no banco) |
| `ID_USUARIO` | FK `FK158` → `USR_TB01_USUARIOS` |
| `TP_ORIGEM` | `'admin'` ou `'email'` (CHECK `CK04`) |
| `DT_CRIACAO` | `DEFAULT now()` |
| `DT_EXPIRACAO` | validade conforme a origem (24 h / 2 h) |
| `DT_USO` | marcado no consumo; token não é apagado, para o histórico continuar auditável |

Índices: `IX101` (ID_USUARIO), `IX102` (DT_EXPIRACAO). `NO_EMAIL` fica em `USR_TB01_USUARIOS`
(coluna adicionada no mesmo arquivo, `VARCHAR(3000)`, única por `lower()` quando não nula).

## Administração de usuários

### Visão geral

A administração de usuários vive em `api/rotas/usuarios.py` e na tela `web/usuarios.html`: criar
usuário, aprovar autocadastro, ativar/desativar, editar nome/e-mail/admin/delegacia e gerar link
de redefinição de senha. **Todas as rotas exigem administrador** — o router declara
`APIRouter(dependencies=[Depends(exigir_admin)])`, ou seja, sessão válida + aprovada + `IN_ADMIN`.

### Fluxo de uso

1. `web/usuarios.html` carrega `web/js/utils/auth.js` + `web/js/utils/sidebar.js` e esconde a seção de
   administração se `GET /api/usuarios` responder `403` (não-admin vê só o aviso de tela
   restrita).
2. A tabela lista login, nome, e-mail, delegacia, admin, último login, status e aprovação, com
   exportação para Excel (`data-exportar="usuarios"`). Uma coluna **IA** marca com um selo quem
   tem provedor de IA próprio — ver [ia_por_usuario.md](ia_por_usuario.md).
3. **Cadastrar usuário** (modal): login, nome, e-mail (opcional), senha inicial (mín. 8), delegacia
   (seletor de `GET /api/delegacias`) e checkbox "É administrador".
4. **Aprovar** um autocadastrado: `POST /api/usuarios/{id}/aprovado` (`{aprovado: true}`); o botão
   também serve para revogar (`{aprovado: false}`).
5. **Ativar/desativar**: `POST /api/usuarios/{id}/ativo` — desativar bloqueia o login sem apagar o
   histórico; sessões abertas só param de validar na próxima checagem.
6. **Editar**: `PUT /api/usuarios/{id}` com nome, e-mail, admin e delegacia (login é fixo na tela).
7. **Redefinir senha**: modal → `POST /api/usuarios/{id}/redefinicao` — gera o link/código de
   recuperação e devolve ao admin para entregar ao usuário (ver seção acima); opcionalmente
   envia por e-mail (`enviar_email: true`), se houver SMTP e o usuário tiver e-mail.
8. **IA do usuário**: modal → `GET`/`PUT`/`DELETE /api/usuarios/{id}/config-ia` — define, só para
   aquele analista, outro provedor/modelo/chave de IA. Documentado à parte em
   [ia_por_usuario.md](ia_por_usuario.md).

### Como funciona

- `listar_usuarios()` (`api/funcoes/auth_utils.py`) devolve todos os usuários com `LEFT JOIN` em
  `seguranca.delegacias` (nome da delegacia), **sem o hash da senha**, ordenados por nome.
- `criar()` → `criar_usuario(...)`: valida login/nome obrigatórios, senha mínima de 8, login e
  e-mail únicos; usuário criado por admin **já nasce aprovado** (a criação é a aprovação) — é o
  mesmo `criar_usuario` do autocadastro, mas com `aprovado=True`. Erros de validação → `400`.
- `alterar_ativo` / `alterar_aprovado` → `definir_ativo` / `definir_aprovado` (simples UPDATE em
  `USR_TB01_USUARIOS`; `404` se o id não existir).
- `atualizar_usuario` (`PUT`) → `UPDATE ... SET no_usuario, no_email, in_admin, id_delegacia` com
  checagem de e-mail duplicado em `lower()` e `404` se nenhuma linha foi alterada.
- `alterar_delegacia` (`PUT .../delegacia`) → só o `id_delegacia` (desassociação com valor `null`).
- `gerar_redefinicao` → `criar_token_redefinicao(id_usuario, "admin")` (validade 24 h) e devolve
  `token`, `url` (link ou vazio sem `APP_URL` — a tela então exibe o código), `dt_expiracao`,
  `enviado_para` e `email_disponivel`. O e-mail só é mandado se o admin pedir (`enviar_email`) e
  houver SMTP + `NO_EMAIL`; o link é devolvido de qualquer jeito. O admin nunca chega a saber a
  senha nova — quem a escolhe é o usuário, em `redefinir_senha.html`.
- **O link/código é credencial**: `web/js/usuarios.js` limpa o campo (`#redefinicao-valor`) ao
  fechar a modal — o valor não fica na tela depois de resolvido, do mesmo jeito que uma senha não
  ficaria. Copiar usa `navigator.clipboard` quando disponível (contexto seguro: https ou
  localhost) e cai para `document.execCommand("copy")` numa instalação servida por http puro em IP
  de rede interna, onde a Clipboard API não existe.
- Proteção: `exigir_admin` encadeia `exigir_aprovado` → `exigir_login` (`auth_utils.py`); as telas
  que dependem de seletores de usuário usam `listar_usuarios_basico()` (só id/nome/login de ativos
  e aprovados), sem expor a tela de administração.
- **Limites de tamanho na entrada**: `UsuarioIn` (criação) capa `login` em 60 caracteres e `senha`
  em 200 — mais apertado que a coluna `NO_LOGIN VARCHAR(3000)` — e um valor além do limite é
  rejeitado com `422` do próprio FastAPI antes de chegar em `criar_usuario`. `UsuarioUpdateIn`
  (`PUT /api/usuarios/{id}`) nem recebe `login`: a tela mostra o campo desabilitado porque a rota
  simplesmente não aceita trocá-lo — para renomear o login é preciso apagar e recriar o usuário.

### Modelo de dados

| Tabela | Colunas envolvidas |
|---|---|
| `SEGURANCA.USR_TB01_USUARIOS` | `ID_USUARIO`, `NO_LOGIN` (único), `NO_USUARIO`, `TX_SENHA_HASH`, `IN_ADMIN`, `IN_ATIVO`, `IN_APROVADO`, `NO_EMAIL`, `DT_CADASTRO`, `DT_ULTIMO_LOGIN`, `ID_DELEGACIA` (FK → `SEGURANCA.DELEGACIAS`) |
| `SEGURANCA.USR_TB03_REDEFINICAO_SENHA` | tokens de redefinição gerados pelo admin (`TP_ORIGEM = 'admin'`) |
| `SEGURANCA.USR_TB04_TENTATIVAS_LOGIN` | trilha de falhas por login/IP (auditoria de ataque) |
| `SEGURANCA.DELEGACIAS` | opções do seletor de delegacia |

## Configuração

Constantes em `api/funcoes/auth_utils.py`:

| Constante | Valor | Papel |
|---|---|---|
| `TAMANHO_MINIMO_SENHA` | 8 | senha mínima no cadastro/alteração |
| `DURACAO_SESSAO` | 12 h | validade da sessão (e do cookie) |
| `RENOVAR_SESSAO_A_CADA` | 1 h | renovação deslizante da sessão |
| `JANELA_TENTATIVAS` | 15 min | janela do limite de tentativas |
| `MAX_TENTATIVAS_LOGIN` | 5 | falhas por login |
| `MAX_TENTATIVAS_IP` | 30 | falhas por IP |
| `RETENCAO_TENTATIVAS` | 90 dias | expurgo da tabela de tentativas |
| `VALIDADE_TOKEN_EMAIL` | 2 h | validade do token de redefinição pedido por e-mail |
| `VALIDADE_TOKEN_ADMIN` | 24 h | validade do token gerado pelo administrador |
| `INTERVALO_MINIMO_PEDIDO` | 2 min | intervalo mínimo entre pedidos de "esqueci minha senha" do mesmo usuário (só conta tokens de origem `email`) |

Variáveis de `.env` (lidas a cada uso por `api/funcoes/email_utils.py`):

| Variável | Papel |
|---|---|
| `SMTP_HOST` | host do relay — com `SMTP_REMETENTE`, é o mínimo para `smtp_configurado()` |
| `SMTP_PORT` | default `587` (starttls) ou `465` (ssl) |
| `SMTP_SEGURANCA` | `starttls` (default) / `ssl` / `nenhuma` |
| `SMTP_USUARIO` / `SMTP_SENHA` | autenticação (relay interno pode dispensar) |
| `SMTP_REMETENTE` | remetente; vazio cai no `SMTP_USUARIO` |
| `APP_URL` | base pública do sistema; sem ela, entrega-se o código em vez do link |

O nginx precisa enviar `X-Real-IP` ao proxy `/api/*` (ver `nginx/default.conf` e
`infraestrutura.md`), senão o limite por IP contaria sempre o container `web`.

## Referências

- `api/rotas/auth.py` — `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/eu`,
  `POST /api/auth/tema`, `POST /api/auth/senha`, `POST /api/auth/email`,
  `POST /api/auth/cadastro`, `POST /api/auth/esqueci`, `GET /api/auth/redefinir/{token}`,
  `POST /api/auth/redefinir`, `GET /api/auth/recuperacao`, `GET /api/auth/provedores`,
  `POST /api/auth/primeiro-acesso`; e as de aviso da IA (`GET /api/auth/avisos-ia`,
  `GET/PUT /api/auth/webhook-ia`, `POST /api/auth/webhook-ia/teste` — ver `avisos_ia.md`).
- `api/rotas/api_pf.py` — `GET/PUT /api/config/api-pf`, `POST /api/config/api-pf/testar`
  (todas exigem administrador).
- `api/funcoes/api_pf_utils.py` — cliente do OpenID Connect da PF, catálogo `CAMPOS` da tela e
  leitura da configuração do `.env` em tempo de execução.
- `api/rotas/usuarios.py` — `GET/POST /api/usuarios`, `POST /api/usuarios/{id}/ativo`,
  `POST /api/usuarios/{id}/aprovado`, `POST /api/usuarios/{id}/redefinicao`,
  `PUT /api/usuarios/{id}`, `PUT /api/usuarios/{id}/delegacia`, e as rotas de IA própria
  (`GET /api/usuarios/config-ia`, `GET/PUT/DELETE /api/usuarios/{id}/config-ia` — ver
  `ia_por_usuario.md`).
- `api/funcoes/auth_utils.py` — `criar_sessao`, `validar_sessao`, `autenticar`, `criar_usuario`,
  `definir_ativo`, `definir_aprovado`, `definir_tema`, `criar_token_redefinicao`,
  `redefinir_senha_com_token`, `sincronizar_usuario_api_pf`, `definir_senha_inicial` e as
  dependencies `exigir_login`/`exigir_aprovado`/`exigir_admin`.
- `api/funcoes/email_utils.py` — SMTP, link vs. código.
- `db/init/07_seguranca_usuarios.sql` (usuários, sessões, tokens de redefinição, tentativas de
  login, delegacias) e `db/init/35_autenticacao_api_pf.sql` (`CO_PROVEDOR`, hash de senha anulável).
- `web/login.html`, `web/cadastro.html`, `web/redefinir_senha.html`, `web/usuarios.html`,
  `web/api_pf.html` (JS: `login.js`, `cadastro.js`, `redefinir_senha.js`, `usuarios.js`,
  `api_pf.js`, `utils/auth.js`, `utils/primeiro_acesso.js`, `utils/theme.js`).
- Documentos relacionados: `permissoes_delegacia.md` (delegacia do usuário, permissões por
  inquérito), `auditoria.md` (rastro de acesso e de IA), `interface.md` (guarda de sessão no
  front).