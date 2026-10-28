# Marcadores nomeados

## Visão geral

Marcar um registro para o relatório (o 🗒️ das telas de dados) sempre existiu — ver
[relatorio_word.md](relatorio_word.md). O que não existia era **nome** na etiqueta: a única
segmentação era uma paleta de 12 cores anônimas, e o .docx saía rotulado "[Vermelho]". Quem
marcou é que tinha de lembrar o que o vermelho significava naquele caso, semanas depois.

Os **marcadores** são as *tags* do IPED trazidas para cá: etiquetas com **nome, cor e
contagem** ("TRÁFICO DE DROGAS", "PENDENTES DE ANÁLISE"), criadas pelo próprio investigador.
Com elas vieram três coisas na tela do WhatsApp/mensageiros:

- um **sumário flutuante** (🏷️, ao lado do botão da IA) com a árvore *marcador → conversa →
  mensagem*, que leva direto à bolha;
- a possibilidade de marcar a **conversa** (o contato) pela lista lateral — triagem do fio, para
  o analista controlar o que já leu e o que é relevante;
- a página **Marcações**, com o conteúdo do que foi etiquetado e exportação em planilha e .docx.

### Duas regras que explicam o resto

1. **O marcador é pessoal e preso ao inquérito.** Como no IPED, ele vive dentro do caso: cada
   inquérito começa com a lista vazia, e a etiqueta de um investigador não aparece na lista de
   outro. Toda função de `marcadores_utils.py` recebe `id_usuario` e filtra por ele.
2. **A marcação continua compartilhada.** Se o colega marcou uma mensagem, todo mundo com
   acesso ao caso vê que ela está marcada — isso não mudou (o índice único `UX95` segue valendo,
   ver [db/init/30_relatorios.sql](../db/init/30_relatorios.sql)). Só os **marcadores
   aplicados** são individuais.

É a regra 2 que impede o marcador de morar numa coluna de `REL_TB03_MARCACOES`: aquela linha é
uma só para o inquérito inteiro. Daí a tabela de aplicação N:N — cada usuário pendura os seus
marcadores na marcação compartilhada, e vários marcadores podem conviver no mesmo registro.

`REL_TB03_MARCACOES.CO_COR` **não foi removida**: continua sendo o *fallback* dos casos
anteriores aos marcadores, que seguem saindo no relatório exatamente como antes.

## Fluxo de uso

1. Na tela do WhatsApp (`whatsapp.html`) ou de mensageiros, clicar no 🗒️ de uma mensagem — ou
   no 🗒️ que aparece ao passar o mouse sobre uma **conversa** na lista lateral.
2. No modal, escolher marcadores existentes (chips) ou criar um novo ali mesmo: nome + cor.
   Salvar substitui o conjunto de marcadores **do usuário** naquele registro.
3. Para marcar em bloco: os checkboxes de cada registro alimentam a barra flutuante; o modal em
   lote tem **Marcar para o relatório** e **Remover marcadores** (o Adicionar/Remover do
   diálogo do IPED), numa requisição só para o lote inteiro.
4. O botão 🏷️ abre o sumário: árvore numerada por marcador, conversa e mensagem. Clicar numa
   mensagem rola até a bolha (ou abre a conversa centrada nela); clicar na conversa abre a
   conversa.
5. A página **Marcações** (botão 🏷️ no bloco do inquérito, ou pelo rodapé do sumário) mostra o
   conteúdo do que está etiquetado e permite renomear, mesclar e apagar marcadores, além de
   exportar.

### Marcar a conversa ≠ marcar as mensagens dela

São **duas marcações diferentes**, e essa é a distinção mais importante do módulo:

| O que é marcado | Para quê | O que sai no .docx |
|---|---|---|
| A **conversa** (o contato naquele mensageiro), pelo 🗒️ da lista lateral | Triagem do fio: "já li", "é relevante", "pendente" | Uma **linha de identificação** — com quem, aparelho, tamanho e período do fio, comentário e marcadores |
| A **mensagem**, pelo 🗒️ da bolha | O trecho que vira prova | A **bolha**, no desenho de tela de celular |

Marcar a conversa **não marca** as mensagens dela. Um fio de 2.000 mensagens etiquetado como
"LIDO" não pode despejar 2.000 bolhas num relatório que ninguém pediu — e, mais de fundo, "já
li esta conversa" é uma afirmação sobre a conversa, não sobre cada mensagem.

Quem quiser o fio completo no `.docx` marca a caixa **"Conteúdo das conversas marcadas"** na
página Marcações, na hora de exportar. É uma segunda decisão, explícita: no backend, o
parâmetro `conteudo_conversas` liga o `expandir_conversas` de `listar_chat_marcado`, e só aí o
`OR` do `JOIN` traz as mensagens do fio.

## Como funciona

### Backend

| Arquivo | Papel |
|---|---|
| `api/funcoes/marcadores_utils.py` | CRUD dos marcadores, aplicação individual e em lote, sumário do mensageiro |
| `api/rotas/marcadores.py` | Rotas; confere acesso ao inquérito **e** que o marcador é do usuário logado |
| `api/funcoes/relatorios_utils.py` | `rotulo_marcador`/`cor_marcador`, `marcadores_das_marcacoes`, e as listagens de chat |
| `api/funcoes/relatorios_docx.py` | `gerar_marcadores_docx` (exportação avulsa) e o rótulo por marcador no relatório |
| `api/funcoes/entidades_utils.py` | `_VERIFICACAO_ORIGEM` ganhou `wpp_conversa`/`sms_conversa`/`msg_conversa` |

Rotas:

```
GET    /api/investigacao/inqueritos/{id}/marcadores            meus marcadores + contagem
POST   /api/investigacao/inqueritos/{id}/marcadores            criar
POST   /api/investigacao/marcadores/{id}                       renomear / trocar cor
DELETE /api/investigacao/marcadores/{id}                       apagar (as marcações continuam)
POST   /api/investigacao/marcadores/{id}/mesclar               {id_destino}
POST   /api/investigacao/marcacoes/{id}/marcadores             substitui o meu conjunto
POST   /api/investigacao/inqueritos/{id}/marcadores/lote       aplica/remove em N registros
GET    /api/investigacao/inqueritos/{id}/marcadores/sumario    ?fonte=todas|wpp|sms|msg&extracao=&q=&limit=&offset= (a árvore)
GET    /api/investigacao/inqueritos/{id}/marcadores/exportacao ?marcadores=1,2&conteudo_conversas= (.docx)
```

Pontos que valem conhecer antes de mexer:

- **A conversa como tipo de origem.** `wpp_conversa`/`sms_conversa`/`msg_conversa` entraram em
  `_VERIFICACAO_ORIGEM`, em `ROTULO_TP_ORIGEM` e nos dois `JOIN COALESCE` que resolvem o trecho
  legível de uma marcação. Sem os `JOIN`s, a conversa marcada apareceria em branco no sumário e
  no relatório.
- **Uma requisição por lote.** A rota `/lote` substituiu o laço que disparava um POST por
  registro — marcar um dia de conversa subia dezenas deles.
- **`listar_chat_marcado` traz só as MENSAGENS marcadas.** A segunda porta do `JOIN` (as
  mensagens de uma conversa marcada) fica atrás de `expandir_conversas`, desligado por padrão —
  ver "Marcar a conversa ≠ marcar as mensagens dela" acima. Quando ligado, a **cor/marcador** da
  conversa vale para todas as bolhas (é classificação), mas o **comentário** não: ele sai uma
  vez, em `tx_comentario_conversa` (uma linha antes da primeira tela). Copiado para cada
  mensagem, repetiria o mesmo texto uma vez por bolha nos "Comentários do investigador" de cada
  trecho.
- **As conversas marcadas saem por `listar_conversas_marcadas`**, como itens: título, aparelho,
  quantas mensagens o fio tem, período, comentário e marcadores. É uma consulta por fonte
  (WhatsApp/SMS/mensageiros), montada com o mesmo mapa `FONTES` de `marcadores_utils`. No
  relatório elas caem no bloco genérico `marcacoes`, que já resolve qualquer `TP_ORIGEM`.
- **A exportação em .docx reaproveita o bloco `chat`.** `gerar_marcadores_docx` monta o
  documento, escreve a tabela das conversas marcadas e chama `_bloco_chat`, o mesmo renderizador
  de bolhas do relatório — não há um segundo desenho de chat para manter.
- **Mensagens marcadas com buraco entre elas saem em telas separadas.** `listar_chat_marcado`
  devolve, além de `mensagens`, os `trechos` (`ru.dividir_em_trechos`): as marcadas quebradas
  onde o fio original foi pulado, com quantas mensagens ficaram de fora em cada salto. Sem
  isso, marcar três mensagens espalhadas por três dias sairia como um diálogo seguido de três
  bolhas. A contiguidade vem da `nu_ordem` — posição da mensagem dentro da conversa inteira,
  contada no SQL por `id_mensagem` (a ordem cronológica de inserção do UFDR, a mesma que o
  visualizador pagina).

### Frontend

| Arquivo | Papel |
|---|---|
| `web/js/utils/entidades.js` | Chips de marcador nos modais individual e em lote, criação inline, evento `analyser:marcacao-mudou` |
| `web/js/whatsapp.js` | 🗒️ da conversa na lista lateral e `window.WhatsAppUI` (navegação até a bolha) |
| `web/js/marcacoes_flutuante.js` | Botão 🏷️ e o painel da árvore |
| `web/js/marcacoes.js` + `web/marcacoes.html` | Página de gerência e exportação (busca, filtros, seleção em lote, mover/remover, export .docx/Excel com diálogo) |
| `web/css/entidades.css`, `marcacoes_flutuante.css`, `marcacoes.css` | Estilos |

Pontos que valem conhecer:

- **O 🗒️ da conversa é montado como string HTML**, dentro de `htmlConversa`, e não por
  `EntidadesUI.botao()`. Com 20 mil conversas, montar o item com jQuery levava ~11 s contra
  ~150 ms como string. As classes (`ent-botao rel-botao`) e o `data-chave` são os mesmos que o
  widget procura, então `EntidadesUI.carregarMarcacoes` pinta o contorno das já marcadas sem
  código extra.
- **Nada recarrega a tela de origem.** Aplicar ou remover marcador atualiza o botão pelo
  `data-chave`; ir do sumário até uma bolha passa por `WhatsAppUI.irParaMensagem`. Recarregar
  remontaria a conversa a partir do dia mais recente e jogaria o investigador para longe de onde
  ele estava.
- **`analyser:marcacao-mudou`** é o que mantém o sumário fresco sem *polling*. Marcar um dia
  inteiro emite um evento por registro, então o painel adia o recarregamento para juntar a
  rajada.

## Modelo de dados

Migração: [db/init/31_marcadores_achados.sql](../db/init/31_marcadores_achados.sql), schema `INVESTIGACAO`.

**`MRC_TB01_MARCADORES`** — a etiqueta.

| Coluna | Observação |
|---|---|
| `ID_MARCADOR` | PK |
| `ID_INQUERITO` | FK202 → `INQ_TB01_INQUERITOS` |
| `ID_USUARIO` | FK203 → `SEGURANCA.USR_TB01_USUARIOS` — o dono |
| `NO_MARCADOR` | único por (inquérito, usuário) **ignorando maiúsculas** (`UX98_MRC_TB01_NOME`) |
| `CO_COR` | `#RRGGBB`, da mesma paleta de 12 |
| `TX_DESCRICAO`, `NU_ORDEM`, `DT_CADASTRO` | |

**`MRC_TB02_APLICACOES`** — liga um marcador a uma marcação.

| Coluna | Observação |
|---|---|
| `ID_MARCACAO` | FK204 → `REL_TB03_MARCACOES`, **ON DELETE CASCADE** |
| `ID_MARCADOR` | FK205 → `MRC_TB01_MARCADORES`, **ON DELETE CASCADE** |
| — | `UX98_MRC_TB02_PAR` impede a mesma etiqueta duas vezes no mesmo registro |

O `CASCADE` nos dois lados é o que mantém "desmarcar o registro" e "apagar o marcador" como um
`DELETE` simples, sem limpeza manual.

### Migração das cores já usadas

A própria `98_` converte o que já existia: cada `(inquérito, usuário, cor)` distinto vira um
marcador com o **nome da cor**, e as marcações daquele usuário passam a apontar para ele.
Marcação sem cor, ou sem usuário criador (as anteriores ao `ID_USUARIO_CRIADOR`), fica sem
marcador e segue pelo *fallback* de `CO_COR`.

Um detalhe corrigido de passagem: `#E67E22` e `#FF9800` eram **ambos "Laranja"** em `COR_NOMES`
— duas cores diferentes com a mesma etiqueta no .docx, e um empate que o único de nome não
aceitaria. O `#FF9800` passou a se chamar **"Âmbar"**, no SQL, em `relatorios_utils.py` e em
`investigacao.js`.

O script é idempotente: pode ser reaplicado com `psql -f` num banco existente.

## Efeito no relatório

O agrupamento por cor virou agrupamento por **marcador do usuário que está gerando**:

- `rotulo_marcador(registro)` devolve os nomes unidos por `" + "`, ou o nome da cor quando não
  há marcador. Um registro em duas etiquetas entra num grupo só — se cada marcador virasse um
  grupo, o mesmo trecho sairia duas vezes no .docx, exatamente o problema que a
  [30_relatorios.sql](../db/init/30_relatorios.sql) resolveu.
- O "Resumo das conversas por IA" deixou de resumir "o grupo vermelho" e passou a resumir "o grupo
  TRÁFICO DE DROGAS".
- Na tela de geração, a coluna **Cor** virou **Marcador**.

Caso antigo, sem nenhum marcador, sai igual ao de antes.

## Referências

- Telas: `web/whatsapp.html`, `web/mensageiros.html`, `web/marcacoes.html`
- [relatorio_word.md](relatorio_word.md) — padrões de relatório e o que as marcações alimentam
- [investigacao_celular.md](investigacao_celular.md) — as telas de dados extraídos
- [mesa_inquerito.md](mesa_inquerito.md) — as entidades (📌), que são outra coisa: anotação livre
  que vira nó do grafo
- [banco_dados.md](banco_dados.md) — convenção de nomes e como aplicar migrações
