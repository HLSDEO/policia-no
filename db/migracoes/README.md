# db/migracoes — deltas de schema para bancos que já existem

`db/init/*.sql` é o retrato **declarativo e completo** do schema, e só roda quando o volume do
Postgres está vazio (primeira subida). Um banco que já existe não recebe nada de lá.

Este diretório é o **diário incremental**: um arquivo por mudança de schema, aplicado uma única
vez, em ordem, pelo runner `api/funcoes/migrar_utils.py` — no startup da API, pelo
`analyser-update`, ou à mão com `docker compose exec api-1 python /app/tools/migrar.py`.

O que já rodou fica registrado em `SEGURANCA.SIS_TB01_MIGRACOES`.

## Como adicionar uma mudança de schema — os dois toques

Toda alteração entra em **dois lugares**:

1. **`db/init/NN_dominio.sql`** — a forma declarativa, para instalação nova. Coluna nova entra
   no `CREATE TABLE`; índice novo entra abaixo da tabela. (A regra do `db/init/README.md`
   continua: nada de `ALTER` avulso lá.)
2. **`db/migracoes/NNNN_descricao.sql`** — a mesma mudança na forma imperativa (`ALTER TABLE …`),
   para os bancos que já existem.

Esquecer um dos dois faz a instalação nova divergir da atualizada. O
`db/tools/conferir_paridade.sh` (CI) pega isso: cria um banco só do `init/` e outro do
`init/` da versão anterior + estes deltas, e compara os dois `pg_dump --schema-only`.

## Convenções

- **Nome:** `NNNN_descricao_curta.sql`, `NNNN` sequencial de 4 dígitos a partir de `0001`,
  independente da numeração de `db/init/`. O prefixo numérico é a identidade da migração no
  ledger (`0007_…` → `NU_MIGRACAO = 7`).
- **Número nunca se repete** — e isto é mais perigoso do que parece. Como o número é a identidade
  no ledger (`NU_MIGRACAO` é PRIMARY KEY), dois arquivos `0021_` fazem o runner aplicar um,
  registrar o número e **pular o outro pelo `continue` de "já registrada"**: sem erro, sem aviso, e
  com qual dos dois sobra dependendo da ordem em que cada instalação aplicou. Já aconteceu (dois
  `0021_` vindos de branches diferentes num merge — hoje renumerados para `0023_` e `0024_`); desde
  então `migrar_utils` **recusa aplicar qualquer migração** enquanto houver número repetido, e a
  tela Administração → Servidor mostra o conflito. **Ao integrar um branch, confira se o número que
  você escolheu continua livre na main** — o git aceita os dois arquivos sem conflito nenhum.
- **Renumerar um delta já publicado** (o conserto de uma colisão dessas) exige torná-lo
  **idempotente** — `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` —, porque parte das
  instalações já o aplicou sob o número antigo e vai recebê-lo de novo sob o novo. O número antigo
  fica no ledger como uma linha **órfã** (registrada, sem arquivo correspondente): é história de
  que algo rodou, o runner a ignora — só não estranhe `total_registradas` maior que
  `total_arquivos` em `--estado`.
- **Forward-only.** Não existe "down". Reverter é escrever um delta compensatório novo.
- **Uma transação por arquivo**, dada pelo runner — não coloque `BEGIN`/`COMMIT` no arquivo.
  Se falhar, nada é registrado e o próximo deploy retenta.
- **Constraints e índices** seguem a numeração global do projeto (`FK…`, `IX…`, `UX…`, `CK…`) —
  pegue o próximo livre do conjunto todo, igual em `db/init/`.
- **`CREATE INDEX CONCURRENTLY`, `VACUUM`, backfill longo** não rodam dentro de transação:
  a **primeira linha** do arquivo deve ser exatamente

  ```sql
  -- migracao: sem-transacao
  ```

  e aí o statement precisa ser idempotente (`CREATE INDEX CONCURRENTLY IF NOT EXISTS …`),
  porque sem transação uma falha no meio deixa trabalho parcial.
- **Nunca edite um arquivo já publicado** (que já saiu numa tag). O runner guarda o sha256 de
  cada um e avisa no log se o conteúdo mudou. Corrigir = novo delta.

## Exemplo

`db/migracoes/0008_arquivos_hash_md5_indice.sql` (resumido):

```sql
-- Índice do cruzamento da tela "Comparar celulares" (quais hashes aparecem em mais de uma
-- extração deste inquérito). Correspondente declarativo em db/init/20_arquivos.sql
-- (abaixo de ARQ_TB01_ARQUIVOS).
CREATE INDEX IF NOT EXISTS IX250_ARQ_TB01_HASH_MD5
    ON INVESTIGACAO.ARQ_TB01_ARQUIVOS (CO_HASH_MD5, ID_EXTRACAO)
    WHERE CO_HASH_MD5 IS NOT NULL;
```
