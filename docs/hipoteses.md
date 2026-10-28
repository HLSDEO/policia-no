# Workspace de hipóteses do inquérito

Transforma o que era "achado do analista" (anotação de texto livre presa a UM documento
importado — `ACH_TB01_ACHADOS_ANALISTA`) em objeto estruturado e **do inquérito inteiro**:
hipótese com **status**, **responsável**, **evidências a favor**, **evidências contrárias** e
**pendências** de diligência. A evidência continua podendo apontar para um item importado
(RIF, extrato SIMBA, IPL, IRPF, IPEI, telefonia, extração ou documento) — mas agora vários
itens podem sustentar a mesma hipótese, não um achado por documento.

Substitui por completo o antigo CRUD de achados: a migração `db/migracoes/0046_hipoteses.sql`
converte cada achado em uma hipótese (confiança `verificada` → status `confirmada`,
`em_analise` → `em_analise`; natureza preservada em `TP_NATUREZA`) + uma evidência favorável
presa ao documento de origem, e derruba `ACH_TB01_ACHADOS_ANALISTA`.

## Tela

`web/hipoteses.html?inquerito=N` (JS: `js/hipoteses.js`, CSS: `css/hipoteses.css`) — entrada
pelo menu "Navegar" do inquérito ("Hipóteses do inquérito", `ITENS_INQUERITO` em
`js/utils/mapa_telas.js`/`mapa_telas_orbit.js`) e pelo item **"Hipóteses do inquérito"** do
menu **Extras** do card do inquérito em `investigacao.html` (`js/investigacao.js`).

### Visão "Hipótese da mídia" (página filtrada)

`web/hipoteses.html?inquerito=N&escopo_tipo=&escopo_id=&escopo_titulo=` (mesmo par de URL do
chat de IA por escopo) — sucessora direta dos antigos achados por documento. Os botões
**"Hipótese da mídia"** nos blocos da tela de Investigação, nas topbars de IPL/IRPF/IPEI/
telefonia e nas ações rápidas do Orbit abrem essa visão:

- o quadro mostra só as hipóteses **ligadas àquela mídia** — pelo vínculo próprio da hipótese
  (`TP_ESCOPO`/`ID_ESCOPO` em `HIP_TB01`) **ou** por uma evidência que aponte a ela
  (filtro no `listar_hipoteses` da API);
- hipótese nova criada nessa visão **já nasce associada àquela mídia** (seletor "Mídia
  associada" pré-preenchido);
- o seletor "Mídia associada" do painel também existe na visão do inquérito inteiro — qualquer
  hipótese pode ser ligada (ou desligada) de uma mídia a qualquer momento.

- **Quadro kanban** com cinco painéis por status: proposta → em análise → confirmada /
  refutada / arquivada. Os botões ◀ ▶ do cartão movem a hipótese entre painéis (PUT com o novo
  `cd_status`); o clique no cartão abre o painel de detalhe.
- **Painel de detalhe** (modal): texto, status, responsável (seletor com quem tem relação com
  o inquérito — criador, permissionados, admins e colegas de delegacia do criador,
  `usuarios_para_responsavel`), natureza (hipótese/fato/normativa, legado da migração) e
  "A IA pode contestar". Em baixo, três colunas: **Evidências a favor**, **Evidências
  contrárias** (cada uma com texto livre e vínculo opcional a um item do inquérito, montado a
  partir dos mesmos endpoints do card de investigação) e **Pendências** (checklist com
  concluída/em aberto).
- **Deep-link** `#hipotese=ID` abre a hipótese direto (usado pela busca global de entidades,
  que indexa hipóteses e evidências como fontes do grupo "Análise").

## Banco (`db/init/48_hipoteses.sql`, migração `db/migracoes/0046_hipoteses.sql`)

| Tabela | O que é |
|---|---|
| `INVESTIGACAO.HIP_TB01_HIPOTESES` | `ID_HIPOTESE` (PK), `ID_INQUERITO` (FK→`INQ_TB01`, NOT NULL — a hipótese é do inquérito), `TX_HIPOTESE`, `CD_STATUS` (CK `proposta\|em_analise\|confirmada\|refutada\|arquivada`), `ID_USUARIO_RESPONSAVEL` (FK→`USR_TB01`), `ID_USUARIO_CRIADOR`, `TP_NATUREZA` (`hipotese\|normativa\|fato`, preservado da migração), `FL_CONTESTAVEL_IA`, par opcional `TP_ESCOPO`/`ID_ESCOPO` (CK `CK29`/`CK30` — a **mídia associada**; colunas adicionadas na migração `0047`), datas |
| `INVESTIGACAO.HIP_TB02_EVIDENCIAS` | FK→hipótese `ON DELETE CASCADE`; `TP_LADO` (`favoravel\|contraria`); `TX_EVIDENCIA`; par opcional `TP_ESCOPO`/`ID_ESCOPO` (os dois juntos — CK `CK28` — validado contra o inquérito por `ia_utils._validar_escopo_documento`, o mesmo checagem do escopo de IA) |
| `INVESTIGACAO.HIP_TB03_PENDENCIAS` | FK→hipótese `ON DELETE CASCADE`; `TX_PENDENCIA`, `IN_CONCLUIDA` (+ `DT_CONCLUSAO`) |

A exclusão do inquérito apaga as hipóteses (evidências/pendências via cascade) no mesmo ponto
do DELETE em cascata de `investigacao_utils.py`.

## API e prompt de IA

- Rotas (`api/rotas/hipoteses.py`, todas sob o guarda-chuva de acesso do inquérito —
  `exigir_acesso_inquerito`/`usuario_tem_acesso_inquerito`; as rotas por id resolvem o inquérito
  dono via `_exigir_acesso_via_hipotese`):
  - `GET/POST /api/investigacao/inqueritos/{id}/hipoteses` — lista (com contagens) e cria;
  - `GET /api/investigacao/inqueritos/{id}/hipoteses/usuarios` — seletor de responsável;
  - `GET/PUT/DELETE /api/investigacao/hipoteses/{id}` — detalhe (evidências + pendências),
    edição (inclusive mover de painel) e apagamento;
  - `POST .../hipoteses/{id}/evidencias`, `DELETE /api/investigacao/evidencias/{id}`;
  - `POST .../hipoteses/{id}/pendencias`, `PUT/DELETE /api/investigacao/pendencias/{id}`.
- Lógica e SQL em `api/funcoes/hipoteses_utils.py`.
- **Injeção no prompt**: `bloco_hipoteses_inquerito(id_inquerito, tp_escopo, id_escopo)` é
  concatenado ao contexto da investigação em **toda** conversa do inquérito — `ia_utils.py`.
  Conversa do inquérito inteiro vê o quadro completo; conversa **restrita a uma mídia** vê só
  as hipóteses daquela mídia (associadas a ela pelo vínculo próprio ou por evidência que
  aponte a ela) — é assim que a "hipótese da mídia" entra no chat IA específico dela
  (`web/ia.html?inquerito=&escopo_tipo=&escopo_id=`). Cada hipótese ativa (arquivadas fora)
  entra com status, responsável, natureza, evidências dos dois lados e pendências em aberto;
  as com `FL_CONTESTAVEL_IA = falso` são tratadas pela IA como verdade já estabelecida.
  Import tardio (dentro da função) evita o ciclo `hipoteses_utils → ia_utils`.

## Referências

- `api/rotas/hipoteses.py`, `api/funcoes/hipoteses_utils.py`, `api/funcoes/ia_utils.py`
  (montagem do prompt), `api/funcoes/busca_entidades_utils.py` (fontes `hipoteses` e
  `evidencias_hipotese`).
- `web/hipoteses.html`, `web/js/hipoteses.js`, `web/css/hipoteses.css`,
  `web/js/utils/mapa_telas.js` (+ variante orbit).
- Esquema: `db/init/48_hipoteses.sql`; deltas `db/migracoes/0046_hipoteses.sql` (cria as
  tabelas e migra `ACH_TB01`, removida de `db/init/31_marcadores_achados.sql`) e
  `db/migracoes/0047_hipotese_midia.sql` (par `TP_ESCOPO`/`ID_ESCOPO` em `HIP_TB01`).
- `chat_ia.md` (onde o bloco entra no prompt), `banco_dados.md` (prefixo `HIP_TB`).
