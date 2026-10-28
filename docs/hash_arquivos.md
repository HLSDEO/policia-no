# Hash de arquivos de origem (cadeia de custódia)

## Visão geral

Cada arquivo de origem importado — `.ufdr` (Cellebrite), extrato SIMBA e os arquivos da pasta
do RIF — tem os hashes **MD5, SHA-1 e SHA-256** calculados na importação e gravados no banco. O
objetivo é a **cadeia de custódia**: permite conferir, depois, que o arquivo analisado é
byte-a-byte o mesmo que foi importado (`db/init/25_rif.sql`).

A Nuvem IPED segue um modelo diferente: não há "um arquivo" só (a evidência é uma pasta-caso
inteira com milhares de itens), mas o IPED já calcula hash **por item** durante o próprio
processamento — MD5/SHA-1/SHA-256 de cada arquivo, mais granular que os outros três fluxos.
Ver [extracao_iped.md](extracao_iped.md), seção "Hash por arquivo".

## Fluxo de uso

1. **UFDR, SIMBA e RIF** — no card do arquivo na tela de investigação, o botão de hash abre
   um popover com os três hashes em fonte monoespaçada (`web/js/investigacao.js`:
   `botaoHash`/`textoHashesArquivo`). No RIF o popover lista um bloco por arquivo da pasta
   (até 4: os 3 CSVs + PDF); se o RIF foi importado antes desse recurso existir,
   `textoHashesRif` mostra "Hash ainda não calculado — o RIF foi importado antes deste recurso
   existir." em vez de tentar exibir valores vazios.
2. **Relatório Word** — o bloco `extracoes` (tabela "Metadados dos arquivos de extração", nos
   dois padrões de investigação) traz o **SHA-256 pronto**, lido de `CO_HASH_SHA256` por
   `listar_extracoes_inquerito`. Extração importada **antes** de `25_rif.sql` não tem
   hash gravado; nesse caso a célula sai com "Não calculado na importação." em vez de vazia —
   célula em branco num relatório de cadeia de custódia não distingue "não calculado" de "o
   analista esqueceu de preencher". A coluna do hash é alargada (`EXTRACOES_FRACOES_COLUNAS`) e
   o hash sai em 8pt, senão os 64 caracteres quebram em quatro linhas. O texto padrão do
   relatório menciona a "função hash SHA-2 de 256 bits" (`db/init/30_relatorios.sql`, no bloco de cadeia de custódia dos dois padrões de investigação).

   > Até agosto/2026 essa célula saía **em branco de propósito**, para o analista preencher à
   > mão no Word. Não vale a pena voltar atrás: o hash já estava no banco desde que `RIF_TB06_ARQUIVOS` passou a existir,
   > e transcrever 64 caracteres à mão num documento assinado é onde o erro entra.

## Como funciona

- `api/funcoes/hash_utils.py` — `calcular_hashes_arquivo` lê o arquivo em **blocos de
  4 MiB** (`_TAMANHO_BLOCO`; `.ufdr` pode ter GBs, nunca carrega o arquivo inteiro na memória) e
  devolve `{"md5", "sha1", "sha256"}` em hexadecimal, atualizando os três `hashlib` em paralelo a
  cada bloco lido; `calcular_hashes_bytes` faz o mesmo para conteúdo já em memória (usado no
  upload/restauração de UFDR, quando o arquivo chega como bytes antes de ser gravado em disco).
- **Momentos do cálculo** (sempre antes de qualquer processamento, sobre a cópia "tal como
  chegou", nunca sobre o conteúdo já interpretado):
  - UFDR: `investigacao_utils.py` — `_gravar_hash_extracao` chama `calcular_hashes_arquivo` no
    upload/restauração e `calcular_hashes_bytes` quando o conteúdo já está em memória; e de
    novo, redundante, o "passe de hash" em `_processar_extracao`, antes de abrir o ZIP para
    extrair;
  - SIMBA: `simba_utils.py._processar_importacao`, logo após marcar `processando` e antes de ler
    o CSV;
  - RIF: `rif_utils.py.importar_rif` — hash de cada um dos até 4 arquivos da pasta
    (`TP_ARQUIVO` `comunicacoes|envolvidos|ocorrencias|pdf`), gravado por arquivo em
    `RIF_TB06_ARQUIVOS`;
  - Nuvem IPED: não calcula hash própria — usa o hash que o IPED já gravou no índice Lucene de
    cada item (`_valor(campos, "md5")`/`"sha-1"`/`"sha-256"` em `iped_utils._item_para_arquivo`),
    gravado em `ARQ_TB01_ARQUIVOS.CO_HASH_MD5`/`SHA1`/`SHA256` (colunas genéricas, ver Modelo de
    dados abaixo). O MD5 é também a **chave de leitura** do OCR e da transcrição que o IPED já
    calculou (`<caso>/text/ocr-results.db` e `transcriptions.db`, chaveados pelo MD5 em
    maiúsculas — ver [extracao_iped.md](extracao_iped.md)), então ele serve à cadeia de custódia e
    ao aproveitamento do texto ao mesmo tempo.
- **Exibição** — `web/js/investigacao.js` monta o popover dos cards (UFDR: `e.co_hash_*` das
  extrações; SIMBA: `s.co_hash_*`; RIF: `r.hashes_arquivos`, um JSON agregado por
  `json_agg` no SQL, uma entrada por arquivo).

## Modelo de dados

Schema `INVESTIGACAO`; as colunas `CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256` moram no arquivo
de cada domínio, não num só:

| Tabela | Observação |
|---|---|
| `INQ_TB02_EXTRACOES` (`db/init/09_extracoes.sql`) | um `.ufdr` por linha; `CHAR(32/40/64)` direto na tabela (não há tabela filha, ao contrário do RIF) |
| `SMB_TB01_EXTRATOS` (`db/init/26_simba.sql`) | um extrato por linha; mesmo padrão |
| `RIF_TB06_ARQUIVOS` (`db/init/25_rif.sql`) | pasta com até 4 arquivos → tabela filha, uma linha por arquivo: `ID_ARQUIVO_RIF` (PK), `ID_RIF` (FK `FK172` → `RIF_TB01_RIFS`), `NO_ARQUIVO VARCHAR(3000)`, `TP_ARQUIVO VARCHAR(30)` (`comunicacoes`/`envolvidos`/`ocorrencias`/`pdf`), `QT_BYTES`; índice `IX117` por `ID_RIF` |
| `ARQ_TB01_ARQUIVOS` (`db/init/20_arquivos.sql`) | `CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256` — colunas genéricas (nullable), só a Nuvem IPED preenche hoje; uma linha por item, não por fonte inteira |

## Referências

- Funções: `api/funcoes/hash_utils.py`, `investigacao_utils.py` (`_gravar_hash_extracao`,
  `_processar_extracao`), `rif_utils.py` (`importar_rif`), `simba_utils.py`
  (`_processar_importacao`), `iped_utils.py` (`_item_para_arquivo`); `relatorios_utils.py`
  (`listar_extracoes_inquerito`) e `relatorios_docx.py` (`_bloco_extracoes`) para o hash no
  relatório Word.
- Esquema: `db/init/09_extracoes.sql` (`INQ_TB02`), `db/init/25_rif.sql` (`RIF_TB06_ARQUIVOS`), `db/init/26_simba.sql` (`SMB_TB01`), `db/init/20_arquivos.sql` (`ARQ_TB01`).
- Telas: `web/js/investigacao.js` (`botaoHash`, `textoHashesArquivo`, `textoHashesRif`).
- Relacionados: `extração_ufdr.md`, `extracao_iped.md`, `rif.md`, `simba.md`,
  `relatorio_word.md`, `banco_dados.md`.
