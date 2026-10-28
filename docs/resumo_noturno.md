# Resumo noturno por inquérito (batch da madrugada)

## Visão geral

Todo dia de madrugada um passe gera, por inquérito, um **resumo do caso + conversas
suspeitas por celular**, exibido no topo de `investigacao.html?inquerito=ID` (tema escuro)
e de `inquerito_orbit.html?inquerito=ID` (tema Orbit). O passo a
passo dos prompts é fixo:

1. **por celular**: principais conversas suspeitas + prática delitiva suspeita de cada uma;
2. **junta os celulares** com os **demais arquivos** (RIF, SIMBA, IPEI, IPL, IRPF,
   telefonia, IPED — só o que tiver de suspeito, em agregados);
3. **peças genéricas**: reuso do resumo IA já existente (`tx_resumo_ia`), sem nova chamada;
4. **síntese INQUÉRITO**: `resumo` (xpto do topo) + `[celular 1] resumo X`, `[celular 2]`
   resumo Y, … + financeiro + peças.

Só reprocessa o que mudou (hash de entrada); o resto é pulado sem chamar a IA.

## Fluxo de uso

1. De madrugada (02:00, no fuso configurado): a **própria API** dispara o passe — uma thread que
   sobe junto com o container (`api/funcoes/agendador.py`). Não há nada para instalar no host, e
   Windows e Linux se comportam igual: `docker compose up -d` já deixa o passe agendado.
2. De manhã: abrir `http://localhost:8080/investigacao.html?inquerito=101` (ou
   `inquerito_orbit.html?inquerito=101` no tema Orbit) — o bloco
   **Resumo noturno do inquérito** aparece no topo, com o consolidado e um expansível
   por fonte (`[celular — ...]`, `[RIF 123]`, …). Badge **desatualizado** = entrou material
   novo depois do passe; o próximo passe regenera.
3. Teste manual: `docker compose exec -T api-1 python /app/tools/resumo_noturno.py --dry-run`
   (só lista), depois `--inquerito 101 --forcar` (gera um).

## Como funciona

- Pipeline: `api/funcoes/resumo_noturno.py` — `gerar_resumo_inquerito()` (1 chamada IA por
  fonte + 1 de síntese, sem function calling, resposta em JSON estrito) e
  `processar_todos()` (um inquérito nunca derruba o passe). CLI fino em
  `api/tools/resumo_noturno.py` (`--inquerito/--forcar/--dry-run/--usuario`).
- Entradas por fonte (agregados, nunca integrais): celular via
  `montar_conteudo_inquerito(... extracoes=[id])` (teto 60k chars); RIF via totais +
  top-15 comunicações/envolvidos; SIMBA via `obter_extrato` + contas + top-15 contrapartes;
  IPEI/IPL/IRPF/telefonia/IPED via totais + top linhas; documentos via
  `listar_documentos_inquerito` filtrando o que já tem bloco próprio.
- Leitura: `GET /api/investigacao/inqueritos/{id}/resumo-noturno`
  (`api/rotas/investigacao.py`, `exigir_acesso_inquerito`) → `obter_resumo_inquerito()`.
  Só lê a tabela; vale mesmo com IA desativada na delegacia.
- Tela: `web/investigacao.html#resumo-noturno` + `carregarResumoNoturno/renderResumoNoturno`
  em `web/js/investigacao.js` (chamado em `processarRespostaInvestigacoes`) e paridade Orbit em
  `web/inquerito_orbit.html#resumo-noturno-orbit` + `web/js/inquerito_orbit.js`
  (chamado em `renderizar`). Conteúdo da IA
  sempre por `.text()`, nunca `.html()`.
- Auditoria: cada chamada vira `IA_TB04_AUDITORIA` com `no_origem='resumo_noturno'`; a linha
  guarda ainda modelo/tokens/custo. Apagar o inquérito apaga os resumos
  (`_apagar_dados_inquerito` em `api/funcoes/investigacao_utils.py`).
- **Agendamento (`api/funcoes/agendador.py`)**: thread daemon subida no startup da API
  (`main.py`, junto do despachante de extrações). A cada 5 min ela calcula a *referência* — o dia
  a que pertence a última ocorrência do horário configurado que já passou — e roda se ainda não
  houver execução para aquele dia. Consequências dessa escolha:
  - **recupera atraso**: servidor desligado às 02:00 e ligado às 08:00 roda ao ligar, ainda como o
    passe daquele dia (é o `Persistent=true` do systemd, sem systemd);
  - **um só roda**: com `API_WORKERS > 1` há uma thread por worker, e a vaga do dia é a chave
    primária de `AGD_TB01_EXECUCOES` — quem insere a linha roda, o resto desiste. Execução em
    `processando` que parou de bater há mais de 30 min é retomável (processo morto); enquanto o
    passe roda, uma thread bate `DT_ULTIMA_ATIVIDADE` a cada minuto;
  - **estreia não dispara**: na primeira subida (ledger vazio) o dia corrente é marcado como feito
    **sem rodar** — atualizar o sistema às 15:00 não pode disparar um passe completo de IA na hora;
  - **sem IA configurada não queima o dia**: o tique sai sem reivindicar, então configurar a IA à
    tarde faz o passe rodar no tique seguinte.
  - O fuso é explícito (`RESUMO_NOTURNO_TZ`, padrão `America/Sao_Paulo`) porque o **container roda
    em UTC**: sem isso "02:00" cairia às 23:00. Nunca calcule a referência com o `now()` do banco.
  - **Segunda tarefa: `higiene_data`** (`api/funcoes/higiene_data.py`) — mesmo ledger e mesmo
    horário, mas roda por fora dos guards do resumo (não depende de IA configurada nem da
    estreia): move uploads soltos e sem registro da raiz de `data/` para `data/_lixeira/uploads/`
    (`UPLOAD_ORFAO_DIAS`, padrão 7) e expurga a lixeira vencida (`LIXEIRA_DIAS`, padrão 30).
    Ver [importacao.md](importacao.md).
- Agendador do host (opcional, e não mais necessário): `scripts/analyser-resumo` +
  `scripts/systemd/analyser-resumo@.service/.timer` (Ubuntu, `OnCalendar=*-*-* 02:00`,
  `Persistent=true`); `scripts/resumo-noturno.ps1/.bat` + `schtasks` (Windows). Nenhum dos dois é
  instalado pelo `scripts/instalar-host.sh` — era essa a pegadinha que a thread interna resolve.
  Quem já tem um deles pode manter: o CLI registra a execução no mesmo ledger
  (`registrar_passe_manual`), então a thread vê o dia como feito e não repete. Para desligar a
  thread e ficar só com o agendador do host: `RESUMO_NOTURNO_AGENDADOR=0`.

## Modelo de dados

`INVESTIGACAO.INQ_TB05_RESUMO_NOTURNO` (`db/init/44_resumo_noturno.sql`, delta
`db/migracoes/0020_resumo_noturno.sql`): uma linha por fonte
(`celular|rif|simba|ipei|ipl|irpf|telefonia|iped|documentos`, com `ID_EXTRACAO`/`ID_FONTE`)
+ uma `consolidado` por passe. `TX_RESUMO` (tela), `JQ_CONVERSAS` (estruturado do celular),
`NO_MODELO/QT_TOKENS_*/VL_CUSTO_USD`, `TX_HASH_ENTRADA` (pulo por igualdade),
`CD_STATUS concluir|erro`, `DT_GERACAO/DT_FIM`.

`INVESTIGACAO.AGD_TB01_EXECUCOES` (`db/init/45_agendador.sql`, delta
`db/migracoes/0022_agendador.sql`): o ledger do agendador — uma linha por
(`TP_TAREFA`, `DT_REFERENCIA`), que é ao mesmo tempo a **vaga do dia** (chave primária) e o
histórico. `CD_STATUS processando|concluido|erro`, `NO_ORIGEM agendador|cli|estreia`,
`NO_WORKER` (host:pid de quem pegou), `QT_PROCESSADOS`/`QT_ERROS`,
`DT_INICIO`/`DT_ULTIMA_ATIVIDADE` (batimento)/`DT_FIM`.

## Configuração

IA própria (`RESUMO_NOTURNO_*`), com queda por campo para `AI_*` (mesma herança de
`DOC_AI_*`), configurada na aba **Resumo noturno** de `configuracoes.html`
(provedor, modelo, URL, chave, certificado e esforço de raciocínio — endpoints
`GET/POST /api/config/ai/resumo-noturno` em `api/rotas/configuracao_admin.py`);
tetos opcionais `RESUMO_NOTURNO_MAX_CHARS_CELULAR` e `RESUMO_NOTURNO_MAX_CHARS_SINTESE`,
esses **sem campo na tela**. Sem nada em `RESUMO_NOTURNO_*`, usa a IA do prompt.

```
# Resumo noturno (vazio = usa a IA do prompt)
# RESUMO_NOTURNO_PROVIDER=
# RESUMO_NOTURNO_MODEL=
# RESUMO_NOTURNO_API_URL=
# RESUMO_NOTURNO_API_KEY=

# Agendamento do passe (campos na mesma aba). Ausente = ligado, 02:00, America/Sao_Paulo —
# é o que faz uma instalação nova rodar sozinha, sem agendador no host.
# RESUMO_NOTURNO_AGENDADOR=1     # 0 desliga a thread (para usar só o agendador do host)
# RESUMO_NOTURNO_HORA=02:00
# RESUMO_NOTURNO_TZ=America/Sao_Paulo
```

O agendamento também aparece na tela: **Configurações → Resumo noturno → Agendamento** mostra o
horário, o fuso, a próxima execução e como foi a última (`GET /api/config/ai/resumo-noturno`
devolve `agendadorProxima`/`agendadorUltima`, montados por `agendador.estado()`).

## Referências

- `api/funcoes/resumo_noturno.py`, `api/tools/resumo_noturno.py`
- `api/funcoes/agendador.py` (thread, ledger e estado), `api/main.py` (`iniciar_agendador`),
  `db/init/45_agendador.sql`, `db/migracoes/0022_agendador.sql`
- `api/rotas/investigacao.py` (`resumo-noturno`), `api/rotas/configuracao_admin.py`
- `web/investigacao.html`, `web/js/investigacao.js` (`carregarResumoNoturno`)
- `web/configuracoes.html#panel-resumo-noturno`, `web/js/config-tabs.js` (aba de configuração da IA)
- `docs/configuracoes_ia.md`, `docs/chat_ia.md`, `docs/auditoria.md`, `docs/banco_dados.md`
