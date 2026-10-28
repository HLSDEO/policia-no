# Busca híbrida (RAG híbrido)

## Visão geral

O chat de IA já tinha três formas de achar evidência, e cada uma sozinha erra de um jeito diferente:

- a **literal** (`buscar_mensagens`) acha a palavra exata, e perde tudo que foi dito com gíria ou em
  código;
- a **semântica** (`buscar_semelhantes`, embeddings) acha o sentido, e às vezes passa longe da sigla,
  do apelido ou do número que a pergunta citava;
- o **grafo** de entidades canônicas sabe que dois registros falam da mesma pessoa, mas ninguém o
  consultava a partir de uma busca por texto.

A **busca híbrida** (`buscar_hibrida`) roda os três **numa chamada só**, funde os resultados por
relevância e devolve uma lista deduplicada. É a ferramenta que o prompt de sistema manda usar como
**primeira opção em pergunta aberta ou de conceito** ("o que se sabe sobre a venda de drogas", "quem
fala com quem", "padrão financeiro de X"), no lugar de disparar as buscas avulsas em paralelo. As
ferramentas avulsas continuam existindo para o que é específico
de um pilar — listar tudo de um contato, por exemplo.

O ganho concreto é o terceiro pilar: a **evidência irmã**. Achada uma mensagem, o grafo devolve as
*outras* mensagens ligadas à mesma pessoa/telefone/local — em outra conversa, em outro app, em outro
aparelho —, que nenhuma busca por texto conectaria.

## Fluxo de uso

Não há tela de busca híbrida: ela é uma **ferramenta do chat de IA**. O investigador pergunta em
linguagem natural na tela de IA do inquérito e a IA decide chamá-la; o indicador de progresso mostra
"Buscando evidências (literal + sentido + grafo)…".

Cada resultado devolvido traz:

| Campo | Para que serve |
|---|---|
| `referencia` | `[msg:wpp:123]` / `[msg:sms:123]` / `[msg:msg:123]` — o marcador que a IA cita e o investigador clica para abrir a mensagem original |
| `trecho` | o texto (ou a transcrição do áudio), cortado em 1200 caracteres |
| `fonte` | `semantico` \| `textual` \| `grafo` — de onde veio |
| `score` | a pontuação da fusão |

Mais três campos no envelope: `origens` (quantos candidatos cada pilar achou — transparência),
`aviso` (quando o inquérito não está indexado e o pilar semântico não roda) e `contexto_grafo`
(vínculos que não têm marcador clicável, como texto: "FULANO (pessoa) — mesmo número nas ligações").

O ajuste de pesos é do **administrador**, em **Configurações → RAG híbrido**.

## Como funciona

Tudo em `api/funcoes/rag_hibrido.py`.

### Os três pilares

**Semântico** — delega para `embed_utils.buscar_semelhantes` (cosseno em numpy sobre
`EMB_TB01_EMBEDDINGS`; ver [busca_semantica.md](busca_semantica.md)). Se o inquérito não estiver
indexado, o aviso é propagado para a IA e **o híbrido segue valendo** com os outros dois.

**Textual** — `ILIKE` sobre as mensagens e as transcrições de áudio das três fontes
(WhatsApp/SMS/mensageiros), unidas por `UNION ALL`, **ranqueado por `similarity()` do pg_trgm**
contra a consulta inteira. O ranking por relevância não é detalhe: o RRF funde por **posição**, então
um pilar ordenado por data envenenaria a fusão. A consulta é limpa de *stopwords* do português
(`_extrair_termos`); consulta que só tem stopwords cai de volta para todas as palavras.

O texto buscado é `tx_mensagem` **concatenado com** as transcrições dos anexos — mensagem de voz não
tem texto, o conteúdo dela só existe na transcrição.

**Grafo** — três passos a partir das referências que os outros dois pilares já acharam:

1. quais **entidades canônicas** têm alguma dessas referências como evidência (`ENT_TB06`, índice
   `IX48_ENT_TB06_ORIGEM`);
2. as **outras** evidências dessas canônicas **que forem mensagens** — as irmãs — viram resultados
   clicáveis, com o texto buscado;
3. os demais tipos de vínculo (ligação, contato, localização…) não têm marcador clicável no chat e
   entram só como **contexto textual**.

Referência de documento (`[doc:...]`) não tem evidência no grafo e é ignorada nesse pilar.

### A fusão (RRF ponderado)

`_rrf_fusion` implementa *Reciprocal Rank Fusion* ponderado, com a constante `K = 60` da literatura:

```
score(referencia) = Σ  peso_do_pilar / (60 + posição_no_pilar + 1)
```

Cada pilar contribui com **3 × limite** candidatos antes da fusão (a fusão estreita). A dedup é por
`referencia`: mantém o trecho do primeiro pilar que a trouxe, registra a `fonte`, e um registro que
aparece em mais de um pilar **acumula pontuação** — é o que empurra para o topo o que a busca literal
e a semântica acharam junto. Pilar com peso zero é pulado inteiro.

### Cache

O resultado fundido fica em cache por consulta — chave `(id_inquerito, consulta em minúsculas,
limite)`, TTL de 5 minutos, teto de 64 entradas (LRU). O motivo: um turno de IA pode chamar a mesma
ferramenta mais de uma vez, ou disparar chamadas em paralelo no mesmo inquérito, e sem cache a busca
semântica reembeddaria a consulta e o pilar textual revarreria as mensagens. O resultado é entregue
em cópia profunda, para quem consome não alterar o que está cacheado.

## Modelo de dados

Não cria tabela nenhuma — lê as que já existem: `WPP_TB04`/`SMS_TB04`/`MSG_TB04` (mensagens),
`WPP_TB05`/`SMS_TB05`/`MSG_TB05` (anexos, pela transcrição), `EMB_TB01_EMBEDDINGS` (pilar semântico)
e `ENT_TB05`/`ENT_TB06` (canônicas e evidências).

O que os scripts de schema acrescentam para esta busca são **índices GIN de trigrama**
(`gin_trgm_ops`) sobre `TX_MENSAGEM` das três tabelas de mensagem e `TX_TRANSCRICAO` das três de
anexo — `IX186`–`IX191`, em `db/init/10_whatsapp.sql`, `11_sms.sql` e `12_mensageiros.sql`. Sem eles a consulta varre a tabela de mensagens inteira: `ILIKE` com `%` nas
duas pontas não usa B-tree, e o ranking por `similarity()` precisa do mesmo índice. A extensão
`pg_trgm` vem de `db/init/01_schemas.sql`, instalada no schema `public` — por isso os scripts
mantêm `public` no fim do `search_path`.

## Configuração

Aba **RAG híbrido** da tela **Configurações** (só admin), gravada no `.env`:

| Variável | Padrão | O que é |
|---|---|---|
| `RAG_PESO_SEMANTICO` | `0.5` | peso do pilar semântico na fusão |
| `RAG_PESO_TEXTUAL` | `0.3` | peso do pilar literal |
| `RAG_PESO_GRAFO` | `0.2` | peso da expansão pelo grafo |
| `RAG_LIMITE` | `15` | máximo de resultados fundidos |

Os pesos são **únicos e globais** — não há ajuste por delegacia. Cada um é lido a cada busca e
grampeado em `[0, 1]`; valor inválido cai no padrão. O semântico tem o maior peso porque é o que
pega o que a busca literal não pega (gíria, linguagem cifrada), e o grafo entra como expansão: é
complemento, nunca a fonte principal.

Rotas: `GET`/`POST /api/config/ai/rag` (`api/rotas/configuracao_admin.py`), que grampeia `RAG_LIMITE`
em `[5, 50]` ao ler.

### Limite conhecido

**`RAG_LIMITE` não chega à busca.** `_config_rag()` calcula o limite, mas `buscar_hibrida` só
consome `["pesos"]` — o número efetivo de resultados é o argumento `limite` que o modelo passa na
chamada da ferramenta, ou o padrão de 15 do próprio Python (grampeado em `[1, 100]`). Mexer no campo
da tela hoje muda o `.env` e não muda o comportamento; os três pesos, esses, valem. Corrigir é
usar `config["limite"]` como padrão quando a ferramenta não receber `limite` explícito.

## Referências

- `api/funcoes/rag_hibrido.py` — `buscar_hibrida` (orquestração e cache), `_config_rag`,
  `_pilar_textual`/`_extrair_termos`/`_select_textual_fonte`, `_pilar_semantico`, `_pilar_grafo`/
  `_resolver_referencias`, `_rrf_fusion`, `_STOPWORDS`, `_RRF_K`, `_TAMANHO_TRECHO`.
- `api/funcoes/ia_investigacao.py` — o schema da ferramenta `buscar_hibrida` e o despacho.
- `api/funcoes/ia_utils.py` — o `SISTEMA_PROMPT` que manda preferir a híbrida em pergunta aberta, e o
  rótulo de progresso da tela.
- `api/funcoes/embed_utils.py` — `buscar_semelhantes` (pilar semântico).
- `api/rotas/configuracao_admin.py` — `GET`/`POST /api/config/ai/rag`;
  `web/configuracoes.html` (aba "RAG híbrido").
- Esquema: `db/init/10_whatsapp.sql`, `11_sms.sql`, `12_mensageiros.sql` (índices trigrama
  `IX186`–`IX191`); `db/init/01_schemas.sql` (extensão `pg_trgm`).
- Documentos relacionados: [chat_ia.md](chat_ia.md) (o loop de ferramentas e as demais buscas),
  [busca_semantica.md](busca_semantica.md) (embeddings e o cache da matriz),
  [mesa_inquerito.md](mesa_inquerito.md) (as canônicas e evidências que o pilar do grafo percorre),
  [configuracoes_ia.md](configuracoes_ia.md) (a tela de Configurações).
