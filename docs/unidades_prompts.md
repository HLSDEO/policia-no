# Unidades superiores e prompts de IA em cascata

## 1. Visão geral

Duas mudanças que trabalham juntas:

1. **Hierarquia de unidades** — as delegacias podem ser agrupadas numa **árvore de unidades
   superiores**. Na regra simples, o pai da delegacia é a UF (SR/PF-ES); em estados como o DF há um
   nível a mais entre a UF e a delegacia (DIP e DICOR abaixo da superintendência). A árvore é de
   profundidade variável (tabela `SEGURANCA.UNIDADES` com `ID_UNIDADE_PAI`), então casos novos não
   pedem migração — só cadastro na tela.
2. **Prompt de IA por nível** — além do prompt de sistema base (fixo em código), o administrador
   cadastra **instruções adicionais** em quatro níveis — geral do sistema, unidade superior,
   delegacia e usuário — que se **acumulam em cascata** no prompt de sistema do chat:

   ```
   SISTEMA_PROMPT (código)
     + contexto do inquérito/hipóteses
     + INSTRUÇÕES GERAIS (nível 'sistema')
     + INSTRUÇÕES DA UNIDADE  (ancestrais da delegacia, raiz → folha: SR/PF-DF → DIP)
     + INSTRUÇÕES DA DELEGACIA
     + INSTRUÇÕES DO USUÁRIO
   ```

   Cada nível **complementa**, nenhum substitui o método base; e a regra fixada no próprio bloco é
   que, em conflito, **a pergunta do investigador vence**.

Por enquanto a unidade superior é **organização + herança de prompt**: não dá visibilidade de
dados entre delegacias (o isolamento continua por inquérito/delegacia, ver
[permissoes_delegacia.md](permissoes_delegacia.md)).

## 2. Fluxo de uso

1. **Administração → Delegacias**, card **Unidades superiores**: criar a unidade (nome, sigla,
   nível "UF / superintendência" ou "Coordenação", pai opcional, UF). A API recusa ciclo (a própria
   ou uma descendente como pai) e exclui somente unidade sem filhos e sem delegacias vinculadas.
2. No mesmo card, editar a delegacia e escolher a **Unidade superior** (novo campo na tabela e nos
   dois modais). "Sem unidade" = comportamento antigo.
3. **Administração → Configurações → aba "Prompts de IA"**:
   - **1 · Geral do sistema** — vale para todo mundo;
   - **2 · Prompts por unidade, delegacia ou usuário** — seletor de nível + entidade + texto;
     salvar texto vazio **remove** o prompt ativo da entidade (a lista mostra também linhas
     inativas como histórico reativável, removíveis por "Remover");
   - **3 · Prévia da cascata** — mostra o bloco exato que o prompt de sistema daquele usuário ou
     daquela delegacia recebe hoje.
4. O chat de IA passa a receber o bloco montado — nenhuma ação do analista.

## 3. Como funciona

### Árvore (`SEGURANCA.UNIDADES`)

- `api/rotas/unidades.py` — CRUD admin-only (`GET/POST/PUT/DELETE /api/unidades`). Lista **plana**
  com `id_unidade_pai`; a árvore é montada no front (`web/js/delegacias.js`). Anti-ciclo:
  `descendente_de()` sobe o sub-árvore do candidato com teto de profundidade.
- `DELEGACIAS.ID_UNIDADE` (FK `ON DELETE SET NULL`): apagar a unidade nunca apaga a delegacia.
  O CRUD de delegacia (`api/rotas/delegacias.py`) aceita `id_unidade` (NULL = não mexe) e
  `limpar_unidade: true` para desvincular.

### Cascata (`SEGURANCA.PROMPTS_IA` + `api/funcoes/ia_prompt_niveis.py`)

- Uma linha **ativa** por nível+entidade (UNIQUE parcial `UX113`); `ID_ENTIDADE` é polimórfico
  (unidade/delegacia/usuário, NULL em `sistema`) — sem FK, com checagem de existência na rota de
  escrita e limpeza na exclusão da entidade (delegacia/unidade).
- `prompt_niveis(id_usuario=…)` monta o bloco: CTE recursiva sobe a árvore a partir da **delegacia
  do usuário** (com teto de profundidade — defesa contra ciclo gravado à mão), junta os prompts
  ativos e concatena com cabeçalhos (`--- DA UNIDADE DIP/PF (DIP) ---`). Teto de
  `LIMITE_CHARS_PROMPTS_NIVEIS` (8 mil caracteres) com aviso de corte dentro do bloco. Sem cache:
  duas consultas baratas por turno, mesmo raciocínio de `ia_delegacia.delegacias_sem_ia`.
- **Injeção** em `api/funcoes/ia_utils.py`: o bloco é resolvido em `enviar_mensagem_usuario` (já
  descontado do orçamento de conteúdo anexado) e entra no `contexto_caso` dentro de
  `_processar_mensagem` — ou seja, vale para o chat geral, as conversas com escopo
  (`SISTEMA_PROMPT_ESCOPO`: RIF/SIMBA/IPL/…), o modo conteúdo completo e o map-reduce, porque todos
  montam o sistema a partir do `contexto_caso`.
- **Fora do escopo por decisão**: resumo noturno, análise de documento anexado e comentário IA no
  relatório mantêm seus prompts fixos — podem aderir depois chamando `prompt_niveis()`.
- Auditoria: o bloco vai junto no `TX_CONTEXTO` registrado em `IA_TB04_AUDITORIA` (é parte do
  prompt de sistema).

### Rotas (`api/rotas/configuracao_admin.py`, seção 10 — todas admin)

| Rota | Para quê |
|---|---|
| `GET /api/config/prompts-ia` | todos os prompts, com nome da entidade resolvido |
| `GET /api/config/prompts-ia/opcoes` | listas dos seletores (unidades, delegacias, usuários ativos) |
| `POST /api/config/prompts-ia` | upsert do prompt ativo do nível+entidade (vazio = remove) |
| `DELETE /api/config/prompts-ia/{id}` | remove uma linha (inclusive inativa) |
| `GET /api/config/prompts-ia/efetivo?tp_nivel=&id=` | prévia da cascata para um usuário/delegacia |

### IA própria de unidade/delegacia (seção 11 — conexão, não prompt)

Hierarquia de CONEXÃO de IA: `.env (sistema) → unidade superior (raiz→folha) → delegacia →
usuário`. A linha vencedora vale INTEIRA (nunca mistura provedor de um nível com chave de outro)
e é escolhida do mais específico para o mais geral — `config_para_ia` em
`api/funcoes/config_ia_usuario.py` (o usuário já era o `USR_TB06_CONFIG_IA`; o elo do meio é a
nova `CONFIG_IA_ENTIDADE`). Vence tudo que vem do `.env`, inclusive as sessões `REPORT_AI_*`/
`DOC_AI_*`, igual à IA por usuário de sempre. Tela: botão **"IA"** nas linhas de unidade e
delegacia em Gerenciar delegacias (mesmas regras de chave mascarada/certificado da "IA do
usuário").

| Rota | Para quê |
|---|---|
| `GET /api/config/ia-entidade/{nivel}/{id}` | linha atual (chave só mascarada, cert só resumo) |
| `POST /api/config/ia-entidade` | upsert (branco na chave/cert = mantém o gravado) |
| `DELETE /api/config/ia-entidade/{nivel}/{id}` | remove a exceção (volta ao nível de cima) |

### UF herdada da árvore

Com unidade superior escolhida, a **UF da delegacia é a da árvore** — a efetiva da unidade
(dela mesma ou do ancestral mais próximo com UF). `api/funcoes/unidades_utils.py`: ao salvar
unidade (ou mover a árvore) um único UPDATE recursivo replica a UF para as delegacias; no
criar/editar de delegacia a UF digitada é sobrescrita pela herdada quando existe. A tela
desabilita o campo UF quando a unidade escolhida já responde a UF. Delegacia sem unidade
continua com UF manual.

## 4. Modelo de dados

```
SEGURANCA.UNIDADES
  ID_UNIDADE (PK) · NO_UNIDADE (UNIQUE) · SG_UNIDADE · TP_NIVEL ('uf'|'coordenacao')
  ID_UNIDADE_PAI (FK → UNIDADES) · UF · DT_CRIACAO

SEGURANCA.DELEGACIAS
  + ID_UNIDADE (FK → UNIDADES, ON DELETE SET NULL)   -- 0050

SEGURANCA.PROMPTS_IA                                  -- 0051
  ID_PROMPT (PK) · TP_NIVEL ('sistema'|'unidade'|'delegacia'|'usuario') · ID_ENTIDADE
  TX_PROMPT · IN_ATIVA · ID_USUARIO_AUTOR · DT_CRIACAO · DT_ATUALIZACAO
  CK32 (nível) · CK33 (sistema ⇔ entidade NULL) · FK423 (autor)
  UX113 UNIQUE (tp_nivel, COALESCE(id_entidade,0)) WHERE in_ativa · IX431 (lookup)

SEGURANCA.CONFIG_IA_ENTIDADE                            -- 0052
  ID_CONFIG (PK) · TP_NIVEL ('unidade'|'delegacia') · ID_ENTIDADE (polimórfico, sem FK —
  limpeza na exclusão da entidade) · IN_ATIVA · CO_PROVEDOR · NO_MODELO · TX_API_URL ·
  TX_API_KEY · TX_CA_CERT · CO_ESFORCO_RACIOCINIO · QT_CONTEXTO · ID_USUARIO_AUTOR
  CK34 (nível) · FK424 (autor) · UX114 UNIQUE WHERE in_ativa · IX432 (lookup da cadeia)
```

Migrações: `db/migracoes/0050_unidades_superiores.sql`, `0051_prompts_ia.sql` e
`0052_config_ia_entidade.sql` (espelhos declarativos em `db/init/07_seguranca_usuarios.sql`
e `db/init/39_config_ia_usuario.sql`).

## 5. Configuração

Nenhuma variável de `.env`. Tudo vive no banco e é gerido pelas duas telas.

## 6. Referências

- Telas: `web/delegacias.html` (unidades + vínculo), `web/configuracoes.html` aba `prompts-ia`.
- JS: `web/js/delegacias.js`, `web/js/config-tabs.js` (seção `PromptsNiveis`).
- Backend: `api/rotas/unidades.py`, `api/rotas/delegacias.py`, `api/rotas/configuracao_admin.py`,
  `api/funcoes/ia_prompt_niveis.py`, `api/funcoes/ia_utils.py`.
- Docs relacionadas: [permissoes_delegacia.md](permissoes_delegacia.md) (isolamento atual,
  inalterado), [configuracoes_ia.md](configuracoes_ia.md) (abas de provedor),
  [chat_ia.md](chat_ia.md) (montagem do prompt), [ia_por_usuario.md](ia_por_usuario.md)
  (configuração de provedor por usuário — coisa diferente do prompt por usuário).
