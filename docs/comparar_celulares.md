# Comparar celulares (o que os aparelhos de um inquérito têm em comum)

## Visão geral

A tela cruza os **aparelhos de um mesmo inquérito** entre si e mostra o que eles têm em comum. O
caso que a motivou: A não recebe o dinheiro do ilícito e manda pagarem para B; o celular de A
envia o boleto para B, e B devolve o comprovante para A. Nenhuma mensagem diz nada incriminador —
o vínculo está no **arquivo** que existe nos dois aparelhos. Encontrar isso à mão significa abrir
duas galerias de dezenas ou centenas de milhares de arquivos e comparar de olho.

São quatro eixos, do mais forte ao mais circunstancial:

| Eixo | O que cruza | Força do sinal |
|---|---|---|
| **Arquivos** | mesmo `CO_HASH_MD5` em duas extrações | exato — é o mesmo binário, byte a byte |
| **Interlocutores** | mesmo telefone/usuário nas conversas (WhatsApp + SMS + mensageiros) | forte, e mais forte ainda quando o identificador é o **dono** de um dos aparelhos: os dois falaram entre si |
| **Agenda** | mesmo telefone/e-mail em `CTT_TB02_CONTATO_ENTRADAS` | forte, mas é o que o aparelho *guardou*, não o que ele *usou* |
| **Wi-Fi** | mesmo BSSID (MAC do roteador) | de **lugar**, não de vínculo — hotspot de operadora aparece em qualquer celular |

Implementação: `api/funcoes/comparar_celulares_utils.py` + `api/rotas/comparar_celulares.py`;
tela em `web/comparar_celulares.html` (JS `web/js/comparar_celulares.js`, CSS
`web/css/comparar_celulares.css`).

Onde fica: card do inquérito em `investigacao.html` → menu **Extras** → "Comparar celulares"
(desabilitado, com o motivo no `title`, quando o inquérito tem menos de dois aparelhos); e no menu
**Navegar** de qualquer tela do inquérito (`MapaTelas.ITENS_INQUERITO`).

## O eixo de arquivos

### Por que o hash funciona (e quando não funciona)

O Cellebrite grava MD5 e SHA-256 de praticamente todo `<file>` do UFDR — 1.700.636 de 1.703.877
nos oito extratos auditados — e o parser passa esses valores direto para `ARQ_TB01_ARQUIVOS`
(`investigacao_utils._drenar_arquivos`). A Nuvem IPED também traz hash por item. O cruzamento,
portanto, é **exato**: não há heurística de nome nem de tamanho.

O que casa e o que não casa:

- **Documento, áudio, vídeo e arquivo encaminhado casam.** O WhatsApp entrega o binário original
  ao destinatário — o PDF do boleto e o áudio gravado chegam idênticos.
- **Foto tirada na hora e enviada como imagem normalmente NÃO casa.** O app reencoda antes de
  subir, e o destinatário recebe outro binário. Quando o vínculo é uma foto, o caminho é a
  comparação por semelhança visual (`comparar_rosto.md`), não este eixo.

O agrupamento é por **MD5** e não por SHA-256, porque é o que tem cobertura nas duas fontes
(UFDR e IPED) — as duas colunas existem em `ARQ_TB01_ARQUIVOS`, mas misturá-las num `GROUP BY`
só juntaria espaços de hash diferentes. Colisão de MD5 é construível por quem quer forjar um
vínculo; para conferir um achado que vá para o relatório, o SHA-256 das duas ocorrências está no
banco e é o que se compara.

> **Extração antiga não tem hash.** Quem foi ingerido antes de o parser passar a ler MD5/SHA-256
> do `<file>` tem os arquivos no banco com as colunas de hash vazias, e some deste eixo sem que
> isso seja um erro. A tela detecta o caso (`qt_arquivos > 0` e `qt_arquivos_com_hash = 0`) e
> mostra o aviso em vez de um "nenhum resultado" enganoso — a correção é reprocessar a extração
> (ver `docs/extração_ufdr.md`, seção de backfill).

### O filtro de relevância

Dois aparelhos da mesma marca compartilham **dezenas de milhares** de arquivos idênticos: ícone de
app, som de notificação, fonte, papel de parede, cache. Sem filtro, o boleto fica soterrado. Daí
os três níveis do seletor (`_FILTRO_RELEVANCIA`):

- **Só anexos de conversa** — a ocorrência é anexo de mensagem em pelo menos um dos aparelhos. É a
  prova de que o arquivo **trafegou**, e não de que os dois celulares por acaso o têm. É o nível a
  usar quando se procura a peça.
- **Conteúdo de usuário** (padrão) — categoria conhecida (`CATEGORIAS_USUARIO`: Imagem, Vídeo,
  Áudio, Documento, Compactado), fora das pastas de sistema/cache (`_CAMINHOS_SISTEMA`) e acima de
  8 KB (`TAMANHO_MINIMO_PADRAO`). Anexo de conversa entra sempre, mesmo que caia em alguma dessas
  exclusões.
- **Tudo** — sem filtro, inclusive o ruído do sistema.

Em qualquer nível a ordenação padrão põe o que trafegou em conversa primeiro
(`_ORDENACOES["relevancia"]`), então o ruído fica no fim mesmo quando está na lista.

O filtro é avaliado **por grupo, não por ocorrência** (`HAVING … bool_or(in_relevante)`): basta uma
das cópias passar para o arquivo aparecer, com todas as suas ocorrências. Sem isso, um arquivo que
é anexo num aparelho e está no cache no outro sumiria justamente por causa da cópia irrelevante.

`_CAMINHOS_SISTEMA` é curto de propósito. Filtrar demais esconde prova, e o que está lá é o que
aparece idêntico em qualquer aparelho da mesma marca/versão: `/system/`, `/apex/`, `/vendor/`,
`/product/`, `/data/app/`, `dalvik-cache`, `cache`, `thumbnails`, `Containers/Bundle` e
`/Applications/` do iOS. `Containers/Data/Application` **não** entra: é onde mora também a mídia
recebida pelos apps.

### O contexto de conversa

Achar o arquivo é metade. A outra metade é o **sentido do trânsito**: cada ocorrência traz, quando
o arquivo é anexo naquele aparelho, em que conversa apareceu, quando, se foi enviada ou recebida
(`IN_ENVIADA`) e por quem — o que a tela escreve como "▲ enviou para *João* em 12/03 14:02", com
link direto para a mensagem (`?msg=`, mesma convenção de `js/arquivos.js`).

`_sql_chat_do_arquivo()` é o parente rico de `investigacao_utils._SQL_CHAT_DO_ARQUIVO` (que
devolve só o id da mensagem para o deep-link). Roda como `LATERAL` **apenas sobre a página em
tela** — algumas dezenas de arquivos —, nunca sobre o inquérito inteiro. O nome da conversa
individual é o do outro lado (participante sem `IN_DONO_DISPOSITIVO`); em grupo, o nome do grupo.

### Desempenho

O cruzamento é feito em dois passes, numa consulta só:

1. **Candidatos** — `GROUP BY co_hash_md5 HAVING count(DISTINCT id_extracao) >= 2` lendo só
   `(CO_HASH_MD5, ID_EXTRACAO)`. O índice parcial `IX250_ARQ_TB01_HASH_MD5`
   (`db/init/20_arquivos.sql`, migração `0008`) cobre as duas colunas, então o passe é
   *index-only scan*: 26 ms para 87 mil arquivos na base de teste. Sem ele, seria varredura da
   tabela larga (caminho, OCR, MIME) para usar duas colunas.
2. **Detalhe** — junta os candidatos de volta à tabela, aplica relevância/categoria/busca, agrupa
   e pagina. Só a página resultante busca o contexto de conversa.

Medido em um iPhone de 89 mil arquivos: 0,25–0,42 s para qualquer nível de relevância.

## Os outros três eixos

Todos usam a mesma chave de cruzamento (`_sql_chave`): o valor vira `tel:<11 últimos dígitos>`
quando parece telefone (10 a 13 dígitos e sem `@`) e `id:<texto minúsculo>` caso contrário. É a
mesma régua de `identificacao_pessoas._telefone`, e é o que faz `+55 51 99677-2307`,
`5551996772307` e `51996772307` caírem no mesmo lugar — e o que permite reconhecer como a mesma
pessoa quem foi contatado por WhatsApp num aparelho e por SMS no outro. O teste do `@` vem antes do
de dígitos porque e-mail com número no nome tem dígito suficiente para passar por telefone.

- **Interlocutores** (`comparar_conversas`) — participantes de conversa em `WPP/SMS/MSG_TB03`. JID
  de grupo (`@g.us`) e oculto (`@lid`) viram `NULL` e caem fora: não são o número de ninguém. O
  campo `in_direto` marca o identificador que é **dono** de um dos aparelhos comparados — esses
  vêm primeiro, porque significam que os dois celulares falaram entre si.
- **Agenda** (`comparar_contatos`) — telefones/e-mails de `CTT_TB02_CONTATO_ENTRADAS`. Mostra o
  nome que **cada** aparelho deu ao mesmo número; nomes diferentes para o mesmo contato costumam
  ser informação em si.
- **Wi-Fi** (`comparar_wifi`) — `WIFI_TB01_REDES` agrupado por BSSID. O BSSID é o MAC do roteador,
  então rede com SSID repetido em endereços diferentes não casa por engano; mas equipamento
  público casa, e é a mesma razão por que o BSSID ficou **fora** da convergência entre inquéritos
  (ver o comentário em `canonicas_utils.py`). A tela mostra SSID e número de conexões por aparelho
  para o investigador separar um caso do outro.

## API

Todas em `/api/investigacao/inqueritos/{id_inquerito}/comparar-celulares/…`, todas passando por
`exigir_acesso_inquerito`:

| Rota | Parâmetros | Devolve |
|---|---|---|
| `GET …/aparelhos` | — | extrações concluídas com dono, `qt_arquivos` e `qt_arquivos_com_hash` |
| `GET …/arquivos` | `extracoes`, `relevancia`, `categoria`, `busca`, `tamanho_min`, `ordenar`, `pagina`, `limite` | `{total, pagina, limite, itens[]}`, um item por hash, com `ocorrencias[]` |
| `GET …/contatos` | `extracoes`, `busca`, `pagina`, `limite` | idem, um item por identificador |
| `GET …/conversas` | `extracoes`, `busca`, `pagina`, `limite` | idem, com `in_direto` |
| `GET …/wifi` | `extracoes`, `busca`, `pagina`, `limite` | idem, um item por BSSID |

`?extracoes=1,2,5` recorta quais aparelhos entram; ausente = todos. A lista é só recorte de tela:
quem decide o que vale é `_extracoes_do_inquerito`, que só devolve extração **concluída deste
inquérito** — id de fora mandado à mão não vaza dado de outro caso.

Um endpoint por eixo, e não um só com `?eixo=`, porque a tela carrega cada aba sob demanda: os
três eixos leves não têm por que esperar pelo cruzamento de arquivos.

## Tela

- Cartões de aparelho no topo (checkbox): quem entra na comparação. Cada aparelho ganha um número
  que reaparece nas ocorrências, onde não cabe o nome inteiro.
- Abas por eixo, com a contagem de resultados no *badge* depois da primeira busca.
- Seleção, filtros e página vão para a URL (`EstadoUrl`), de modo que a visão filtrada possa ser
  colada para outro investigador e sobreviva ao botão Voltar.
- Miniatura só para imagem e só quando algum aparelho tem o binário — arquivo que veio como
  metadado puro no UFDR devolve 404 em `/conteudo`.
- "Ver na galeria" abre `arquivos.html?…&busca=<nome>`; o suporte a `?busca=` foi adicionado em
  `js/arquivos.js` justamente para este link.

## Referências

- `api/funcoes/comparar_celulares_utils.py` — os quatro cruzamentos, o filtro de relevância e a
  chave de identificador.
- `api/rotas/comparar_celulares.py` — as cinco rotas.
- `db/init/20_arquivos.sql` / `db/migracoes/0008_arquivos_hash_md5_indice.sql` — índice
  `IX250_ARQ_TB01_HASH_MD5`.
- `web/comparar_celulares.html`, `web/js/comparar_celulares.js`, `web/css/comparar_celulares.css`.
- `web/js/investigacao.js` — menus "Extras" e "Gerência do inquérito" do card
  (`criarMenuAcoes`, opções `rotulo`/`icone`/`alinharDireita`).
- Relacionados: `hash_arquivos.md` (de onde vêm os hashes), `extração_ufdr.md` (o que o parser lê
  do `<file>`), `correlacao_nuvem.md` (**a pergunta oposta**: o que está na nuvem e *não* está
  mais no aparelho — esta tela acha o que está em dois lugares, aquela o que está só num),
  `convergencia.md` (o mesmo tipo de cruzamento, mas **entre** inquéritos),
  `comparar_rosto.md` (semelhança visual, para quando o hash não casa),
  `investigacao_celular.md`.
