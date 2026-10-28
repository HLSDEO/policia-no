# IA do usuário — provedor e cota por analista

## Visão geral

A tela **Configurações de IA** grava provedor, modelo, URL e chave no `.env`, e isso vale para
**todo mundo** na instalação (ver [configuracoes_ia.md](configuracoes_ia.md)). A **IA do usuário**
é a exceção a essa regra: o administrador abre a tela **Usuários**, escolhe um analista e define,
**só para ele**, outro provedor/modelo/chave.

O caso que motiva a feature é o gateway em que **cada analista tem a sua cota**: sem isso, todo o
gasto do sistema sai de uma credencial só, e não há como saber (nem limitar) quem consumiu o quê.

Duas propriedades definem o comportamento:

- **Quem não tem linha continua exatamente como antes.** A maioria dos usuários não tem exceção
  nenhuma, cai no `.env` e não nota diferença — o custo da feature para quem não a usa é zero.
- **Quem tem, tem em toda chamada de IA que fizer**: chat por inquérito, busca
  automática de entidades, análise de documento anexado e as opções de IA do relatório.

## Fluxo de uso

1. **Administração → Usuários**. Na linha do analista, o botão **"IA do usuário"**
   (`web/js/usuarios.js`). Um selo na coluna "IA" marca quem já tem configuração **ativa** — os ids
   vêm de `GET /api/usuarios/config-ia`.
2. O modal abre com o formulário de IA, no mesmo desenho das Configurações (montado por
   `web/js/utils/ia_presets.js`): **provedor**, **modelo**, **URL da API**, **chave**, e —
   quando o provedor pede — **certificado da CA** e **esforço de raciocínio**; mais o
   **tamanho de contexto** (`num_ctx` do Ollama).
3. O checkbox **"Ativa"** liga e desliga a exceção **sem apagar** o que está preenchido: desligado,
   o usuário volta ao `.env` mantendo os campos guardados para quando for religado.
4. **Salvar**. A partir daí, toda chamada de IA daquele analista usa a configuração dele.
5. **Apagar** remove a exceção de vez — o usuário volta ao padrão da instalação.

### O que a tela nunca mostra

- **A chave nunca volta ao navegador**: a rota devolve só a máscara (`mask_key`, os 4 primeiros e
  4 últimos caracteres), igual às Configurações.
- **O certificado volta como descrição** (nome, validade, impressão digital — `cert_utils.resumo`),
  não como PEM.
- Como a tela não recebe nenhum dos dois de volta, **campo em branco significa "mantém o que está
  gravado"** ao salvar. Para apagar o certificado existe o botão próprio (`removerCaCert`).

## Como funciona

### A precedência: três camadas

`enviar_mensagem` (`api/funcoes/ia_provedores.py`) resolve cada campo da mais específica para a
mais geral:

```
IA própria do usuário  >  configuração da seção (REPORT_AI_*, DOC_AI_*)  >  .env da instalação
```

Campo a campo (`_campo("provider", "AI_PROVIDER")` e irmãos), não bloco a bloco. Quase todo usuário
fica no `{}` da primeira camada, e aí nada muda em relação ao comportamento anterior.

**Vencer também a configuração de seção é deliberado.** A razão de a tela existir é "este analista
consome a cota **dele**"; uma exceção que valesse só no chat deixaria a maior parte do gasto — o
resumo das conversas do relatório, a análise de documento — na conta de todo mundo.

### De quem é a vez

`config_para_ia(id_usuario)` precisa saber de quem é a chamada, e há dois caminhos:

- **No caminho normal (uma requisição HTTP)**, o usuário vem do escopo publicado por
  `exigir_login` num `ContextVar` — `id_usuario_da_requisicao()`, em
  `api/funcoes/auditoria_acesso.py`, o mesmo mecanismo que a auditoria de acesso usa para saber a
  rota chamada. Nenhuma das dezenas de assinaturas de função precisou ganhar um parâmetro.
- **Em thread de job** — busca automática, análise de documento — **não há requisição**, e quem
  chama passa `id_usuario` na mão.

Fora de requisição e sem `id_usuario`, `id_usuario_da_requisicao()` devolve `None` e a chamada cai
no padrão do `.env`.

### Nunca derruba a IA

`config_para_ia` **não levanta**: uma consulta que falhe aqui derrubaria a IA do sistema inteiro
por causa de uma exceção que quase ninguém tem. O erro vai para o log e a chamada segue no `.env`.
Pelo mesmo motivo, `GET /api/usuarios/config-ia` (os ids do selo) é um endpoint **separado**, e não
uma coluna em `GET /api/usuarios`: numa instalação que ainda não aplicou `39_config_ia_usuario.sql`
a tela de usuários continua abrindo inteira, só sem o selo.

### Validação: aqui o campo em branco não passa

`config_ia_usuario.validar` exige provedor válido, modelo, URL começando com `http://`/`https://`,
esforço de raciocínio conhecido, chave (exceto em `ollama`/`lmstudio`, que rodam na rede interna
sem autenticação) e certificado quando o provedor o exige.

Isso é o **oposto** das Configurações, e de propósito: lá, campo vazio significa "mantém o que já
está no `.env`". Aqui, uma configuração pela metade viraria uma mistura silenciosa — o modelo deste
usuário com a URL de todo mundo —, que é exatamente o que é difícil de depurar quando a IA de um
analista para de responder.

O que **não** dá para validar aqui é se a chave está certa: quem responde isso é o provedor, na
primeira pergunta.

### Rotas (`api/rotas/usuarios.py`)

| Rota | Acesso | O quê |
|---|---|---|
| `GET /api/usuarios/config-ia` | admin (router) | `{ids: [...]}` — só quem tem configuração **ativa**, para o selo da listagem |
| `GET /api/usuarios/{id}/config-ia` | admin (router) | a configuração, com `apiKeyMasked` e `caCertResumo`; `definida: false` quando não há |
| `PUT /api/usuarios/{id}/config-ia` | admin | grava (`ConfigIaIn`); `400` com a mensagem de `validar`, `404` se o usuário não existe |
| `DELETE /api/usuarios/{id}/config-ia` | admin | apaga a exceção; `404` se não havia |

Todas ficam no router de `usuarios.py`, que declara `Depends(exigir_admin)` — **o próprio usuário
não configura a sua IA**, quem configura é um administrador. É o contrário do webhook de IA, que é
autosserviço (ver [avisos_ia.md](avisos_ia.md)).

## Modelo de dados

`SEGURANCA.USR_TB06_CONFIG_IA` (`db/init/39_config_ia_usuario.sql`):

| Coluna | Observações |
|---|---|
| `ID_USUARIO` | **PK = FK** (`FK248` → `USR_TB01_USUARIOS`) — no máximo **uma** configuração por usuário |
| `IN_ATIVA` | `true` por padrão; `false` desliga a exceção sem apagar os campos |
| `CO_PROVEDOR` | `VARCHAR(30) NOT NULL` — um dos `PROVEDORES_CHAT` |
| `NO_MODELO` | `VARCHAR(200) NOT NULL` |
| `TX_API_URL` | `VARCHAR(500) NOT NULL` |
| `TX_API_KEY` | `TEXT` anulável — vazio é legítimo: Ollama e LM Studio locais não pedem chave |
| `TX_CA_CERT` | PEM em base64, mesmo formato de `AI_CA_CERT` no `.env` (ver `api/funcoes/cert_utils.py`); só o provedor `openai-cert` usa |
| `CO_ESFORCO_RACIOCINIO` | `VARCHAR(10)`, um dos `ESFORCOS_RACIOCINIO` |
| `QT_CONTEXTO` | `num_ctx` do Ollama; `NULL` = padrão do servidor |
| `ID_USUARIO_AUTOR` | FK `FK249` → `USR_TB01_USUARIOS` — **qual administrador configurou**; é onde começa a trilha de "por que este usuário usa outro modelo" |
| `DT_CRIACAO` / `DT_ATUALIZACAO` | `now()`; a gravação é `INSERT … ON CONFLICT (id_usuario) DO UPDATE` |

Índice `IX239_USR_TB06_AUTOR` por `ID_USUARIO_AUTOR`.

> **A chave fica em texto claro na coluna**, do mesmo jeito que já fica no `.env` — quem tem o
> banco tem as chaves de qualquer forma. O que o sistema garante é que ela nunca sai pela API: a
> tela recebe só a máscara.

## Configuração

Nenhuma variável de `.env` própria — esta feature é justamente a **exceção** ao `.env`. As
variáveis que ela sobrepõe são `AI_PROVIDER`, `AI_MODEL`, `AI_API_URL`, `AI_API_KEY`,
`AI_CA_CERT` e `AI_CONTEXT_LENGTH` (ver [configuracoes_ia.md](configuracoes_ia.md)).

O consumo de cada usuário continua rastreado em `IA_TB04_AUDITORIA`, com o `NO_PROVEDOR`/`NO_MODELO`
efetivamente usados no turno e o custo estimado — é lá que se confere se a exceção está valendo
(ver [auditoria.md](auditoria.md)).

## Referências

- Funções: `api/funcoes/config_ia_usuario.py` (`obter`, `ids_configurados`, `validar`, `salvar`,
  `remover`, `config_para_ia`, `PROVEDORES_SEM_CHAVE`).
- Consumo: `api/funcoes/ia_provedores.py` (`enviar_mensagem`, a precedência de três camadas),
  `api/funcoes/documentos_utils.py` (análise de documento), `api/funcoes/auditoria_acesso.py`
  (`id_usuario_da_requisicao`).
- Rotas: `api/rotas/usuarios.py` — `GET/PUT/DELETE /api/usuarios/{id}/config-ia` e
  `GET /api/usuarios/config-ia` (modelos `ConfigIaIn` / `ConfigIaOut`).
- Front: `web/usuarios.html` (modal `#modal-config-ia`), `web/js/usuarios.js`,
  `web/js/utils/ia_presets.js` (os selects de provedor/modelo/URL, compartilhados com as
  Configurações).
- Esquema: `db/init/39_config_ia_usuario.sql`.
- Documentos relacionados: [configuracoes_ia.md](configuracoes_ia.md) (a configuração global que
  esta sobrepõe), [chat_ia.md](chat_ia.md), [auditoria.md](auditoria.md) (onde o consumo aparece),
  [autenticacao.md](autenticacao.md) (a tela Usuários), [avisos_ia.md](avisos_ia.md) (a outra
  configuração de IA por pessoa, essa autosserviço).
