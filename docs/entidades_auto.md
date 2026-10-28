# Entidades automatizadas (extração determinística de documentos)

## Visão geral

A análise de um **documento anexado** (`INQ_TB04_DOCUMENTOS`) era 100% IA — e a produção mostrou o
custo disso: ~36% dos achados caíam no tipo genérico `documento`, com CPF/CNPJ/CNJ válidos perdidos
ali. As **entidades automatizadas** são a outra metade: uma biblioteca global de **regex com
validação** (dígito verificador de CPF/CNPJ, CNJ, telefone, placa…) e **normalização**
(só-dígitos, minúsculo, placa sem traço), auditável e sem custo de IA. O determinístico roda
**primeiro** (barato, total, determinístico) e a IA fica no **resíduo** — pessoas, vínculos,
tipificação, endereços, o que regex não alcança.

Não confundir com os **Padrões de crime** (`PDR_TB01`, ver `padroes_crime.md`) nem com a **busca
automática por IA** (`busca_automatica`, ver `mesa_inquerito.md`): aqui a biblioteca é só sobre
**documentos genéricos** e o motor é regex pura, sem IA.

## Fluxo de uso

1. Na tela **IA e mesa do inquérito** (`web/ia_mesa_inquerito.html`), aba **"Entidades
   automatizadas"**, o administrador vê a biblioteca de padrões (11 de fábrica: IPL, processo CNJ,
   CPF, CNPJ, telefone, e-mail, OAB, placa, agência, conta, valor em dinheiro) e pode criar,
   editar, ativar/desativar e excluir — escrita só para admin; leitura liberada a qualquer
   usuário aprovado.
2. Cada padrão tem: nome, **tipo de achado** (enum fechado — `cpf`, `cnpj`, `telefone`, `email`,
   `valor`, `conta`, `agencia`, `processo`, `procedimento`, `oab`, `placa_veiculo`, `documento`),
   categoria livre (identificação/contato/financeiro/judicial…), descrição e a **definição JSON**:
   `{"padrao": <regex>, "grupo": N, "validador": ..., "normalizacao": ...}` — validada na gravação
   (regex tem que compilar; `grupo` tem que existir; validador/normalização têm que ser conhecidos).
3. Por inquérito, o botão **"Rodar extração automática"** dispara um job em background que varre o
   texto de todos os documentos do inquérito e grava as detecções; o histórico com status
   (`pendente|processando|concluido|erro`) aparece abaixo, com polling — mesmo padrão das outras
   telas. As detecções são agrupadas por padrão, com o valor, nº de ocorrências e um trecho de
   contexto em volta.
4. **Transparente**: toda análise de documento por IA já roda com o pré-passe automático embutido
   (ver "Como funciona") — não precisa disparar nada para o caso a caso.

## Como funciona

- **Função pura no meio** — `extrair_automaticas_texto(texto, padroes)` em
  `api/funcoes/entidades_auto_utils.py` é o motor sem banco, usado pelas duas vias:
  - **Pré-passe da análise por IA** (`api/funcoes/documentos_utils.py`): antes de chamar o modelo,
    roda o determinístico sobre o texto; a mensagem enviada à IA lista o que já foi extraído e pede
    para **não repetir**; na volta, `_fundir_achados` junta os dois (automáticos primeiro, IA
    preenche o resto) usando a mesma chave normalizada (`chave_achado`). Diferente da IA, o
    determinístico **sempre persiste**: sem IA configurada ou com o modelo quebrado, o documento sai
    `concluido` só com os achados automáticos.
  - **Job por inquérito** (`iniciar_extracao` → thread daemon): cria a execução em `DOC_TB06`,
    varre `TX_CONTEUDO` dos documentos do inquérito (só os com texto e sem erro) e grava
    `DOC_TB07` com `ON CONFLICT DO NOTHING` (dedupe por execução+documento+padrão+chave).
- **Prioridade por ordem de id**: os seeds entram do específico para o genérico (procedimento →
  CNJ → CPF → CNPJ → telefone → … → conta → valor), e **trecho já ocupado por um padrão não é
  recontado por outro** — é assim que `Conta: 1643` não vira telefone e `(27) 99847-7194` não vira
  conta.
- **Validadores** (`nenhum|cpf|cnpj|telefone|email|placa|valor|cnj|conta`) confirmam o formato —
  CPF/CNPJ com os dois dígitos verificadores (módulo 11, rejeitando sequências triviais), CNJ com
  20 dígitos, telefone com 10/11, valor > 0 na convenção BR (ponto = milhar, vírgula = decimal).
- **Normalização** gera `CO_CHAVE` (só dígitos / minúsculo / placa sem traço / valor `1234.56` /
  CNJ / OAB / IPL) — é ela que deduplica e **casa o achado automático com o da IA**.
- **Tetos por segurança**: texto truncado a 200 000 caracteres, 500 matches por padrão, 100 valores
  distintos por (documento, padrão); padrão que não compila é pulado (o job segue).
- Variantes que a IA inventa para o tipo (`endereço` → `endereco`, `cpf_cnpj`) são dobradas no tipo
  canônico por `normalizar_tp_achado` na leitura do grafo e na fusão.

## Modelo de dados

`db/init/47_entidades_automaticas.sql` (delta: `db/migracoes/0041_entidades_automaticas.sql`),
schema `INVESTIGACAO`:

| Tabela | Papel |
|---|---|
| `DOC_TB05_AUTO_PADROES` | biblioteca global (`NO_PADRAO` único, `TP_ACHADO`, `TX_DEFINICAO` JSONB, `IN_ATIVO`, criador) |
| `DOC_TB06_AUTO_EXECUCOES` | uma linha por job por inquérito (`CD_STATUS`, `QT_DETECCOES`, `TX_ERRO`, início/fim) |
| `DOC_TB07_AUTO_DETECCOES` | achados (`ID_DOCUMENTO` → `INQ_TB04`, `TX_VALOR` como aparece no texto, `CO_CHAVE` normalizada, `TX_TRECHO` com ~120 caracteres de janela, `QT_OCORRENCIAS`); `UQ12` deduplica; cai junto com o padrão via `ON DELETE CASCADE` |

Índices: `IX421` (execuções por inquérito), `IX422/IX423` (detecções por execução/inquérito),
`IX424` (inquérito+tipo+chave — a consulta de fusão/conferência).

## Configuração

Nenhuma variável de `.env`. A biblioteca vive no banco e é editada pela própria aba; os 11 padrões
de fábrica entram como seed do `47_` (instalação nova) / da migração `0041` (banco existente).

## Referências

- Rota: `api/rotas/entidades_auto.py` — `GET/POST /api/entidades-auto[/{id}][/ativo]`,
  `DELETE /api/entidades-auto/{id}` (admin) e
  `POST/GET /api/investigacao/inqueritos/{id}/extracao-auto` + `.../deteccoes` (acesso ao
  inquérito).
- Lógica: `api/funcoes/entidades_auto_utils.py`; integração com a IA em
  `api/funcoes/documentos_utils.py` (pré-passe + `_fundir_achados`).
- Tela: `web/ia_mesa_inquerito.html`, aba "Entidades automatizadas" (JS: `js/entidades_auto.js`).
- `documentos_diversos.md` (o documento anexado e sua análise por IA), `visualizador_documento.md`
  (a leitura do documento), `padroes_crime.md` (biblioteca análoga, mas para mensagens),
  `banco_dados.md` (convenções de nome).
