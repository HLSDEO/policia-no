# Organização dos arquivos em `data/` por inquérito

## Visão geral

Os arquivos de origem chegam soltos na pasta da delegacia (`data/<DELEGACIA>/`, ver
[permissoes_delegacia.md](permissoes_delegacia.md)) e, **terminada a importação**, são movidos
para a pasta do inquérito, separados por categoria:

```
data/
└── FICCO-ES/                          ← pasta da delegacia (entrega: é aqui que o arquivo é copiado)
    └── 2025.0079407/                  ← número do inquérito (ou `CASO-AAAA.NNNN` gerado, no caso de inteligência)
        ├── CELULAR/                   ← extrações .ufdr (Cellebrite)
        ├── NUVEM/                     ← pastas-caso do IPED (Google Takeout, iCloud…)
        ├── RIF/                       ← pastas de RIF do COAF/SISCOAF
        ├── BANCARIO/                  ← relatórios R304 do SIMBA
        └── OUTROS/                    ← documentos diversos (PDF, CSV, XLSX, DOCX, TXT)
```

Antes disso, todo arquivo importado ficava misturado na raiz da pasta da delegacia — dezenas de
`.ufdr`, CSVs e PDFs de inquéritos diferentes lado a lado, sem como saber a que inquérito cada um
pertencia sem consultar o banco.

O que fica gravado no banco passa a ser o **caminho relativo a `data/`**
(`FICCO-ES/2025.0079407/CELULAR/Relatorio.ufdr`), no lugar do nome puro do arquivo. É por esse
valor que os anexos são lidos sob demanda depois da importação (o `.ufdr` nunca é copiado para o
banco — ver [extração_ufdr.md](extração_ufdr.md)), então ele tem que acompanhar o arquivo.

## Fluxo de uso

1. O investigador copia o arquivo (ou a pasta) para `data/<DELEGACIA>/`, como sempre. Para
   **documento** (PDF/CSV/XLSX/DOCX/TXT) há um segundo caminho: a caixa "Enviar do seu
   computador", na aba **Arquivo genérico** da tela Nova importação, envia pelo navegador e grava
   nessa mesma pasta — quem não tem acesso ao disco do servidor não depende mais de alguém copiar
    por ele. Limite de 50 MB por arquivo por padrão, ajustável em Administração → Desempenho
    (`UPLOAD_DOCUMENTO_MB`, em `funcoes/documentos_utils.py`; o `client_max_body_size` de
    `nginx/default.conf` é 520 MB para caber o teto máximo de 500 MB) — **exceto o `.zip`,
    sem limite como o `.ufdr`** (`POST /api/documentos/upload-zip`, que o expande ali na
    hora). O **`.ufdr` também pode ser enviado pelo navegador** (aba **Indexação Cellebrite**,
    `POST /api/investigacao/ufdr-upload`), aí **sem limite de tamanho** — grava na mesma pasta
    de entrega, em blocos, e o seletor passa a listá-lo (ver [extração_ufdr.md](extração_ufdr.md)).
   Daí para frente o fluxo é idêntico — inclusive a mudança para `<INQUÉRITO>/OUTROS/` no fim.
2. Na tela **Nova importação** (`web/importacao.html` — ver [importacao.md](importacao.md)),
   escolhe o arquivo no seletor da aba correspondente e informa o número do inquérito.
3. Terminada a importação, o arquivo **some do seletor**: ele foi movido para
   `data/<DELEGACIA>/<INQUÉRITO>/<CATEGORIA>/`. O seletor lista só a raiz da pasta da delegacia,
   ou seja, o que ainda **não** foi importado — a lista deixa de crescer sem parar.
4. Para importar o **mesmo arquivo em outro inquérito**, copie-o de novo para
   `data/<DELEGACIA>/` (a cópia já importada continua onde está, servindo o inquérito dela).

Nada muda na tela: os cards, o relatório e a leitura de anexos continuam funcionando, agora
apontando para o caminho novo.

## Como funciona

`api/funcoes/organizacao_arquivos.py` concentra a regra; cada fluxo de importação chama
`organizar(...)` no fim do seu processamento e grava o caminho devolvido:

| Fluxo | Categoria | Quem chama | Coluna gravada |
|---|---|---|---|
| UFDR/Cellebrite | `CELULAR` | `investigacao_utils._organizar_arquivo_extracao` | `INQ_TB02_EXTRACOES.NO_ARQUIVO` |
| Nuvem IPED | `NUVEM` | `iped_utils._organizar_pasta_iped` | `IPD_TB01_EXTRACOES.NO_PASTA` + a extração-ponte em `INQ_TB02` |
| RIF | `RIF` | `rif_utils._organizar_pasta_rif` | `RIF_TB01_RIFS.NO_PASTA` |
| SIMBA (R304) | `BANCARIO` | `simba_utils._organizar_arquivo_extrato` | `SMB_TB01_EXTRATOS.NO_ARQUIVO` |
| Documento do inquérito | `OUTROS` | `documentos_utils._organizar_documento` | `INQ_TB04_DOCUMENTOS.NO_ARQUIVO` |

Detalhes que importam:

- **Sempre no fim.** O arquivo só se move depois de lido do começo ao fim: o `.ufdr` é hasheado,
  extraído e usado na transcrição de áudio; o R304 tem duas passadas; a pasta do IPED tem o índice
  Lucene mapeado em memória pelo subprocesso Java. Mover antes disso quebraria a leitura em
  andamento.
- **Só o que concluiu.** Importação com erro ou cancelada deixa o arquivo na pasta de entrega da
  delegacia, para o investigador conferir e reimportar.
- **Best-effort.** Falha ao mover (arquivo em uso, permissão, disco) vira aviso no log e o
  arquivo fica onde está — o valor no banco não muda e continua resolvendo. Uma importação que já
  terminou nunca é derrubada por causa da arrumação da pasta. Como o `rename` esbarra em handle
  ainda aberto pelo SO (bind mount de `data/` no Docker Desktop do Windows), `organizar` tenta 8
  vezes com 1s de espera — mesma manha de `_renomear_pasta_para_conta` em `iped_utils.py`.
- **A delegacia vem do disco**, não do cadastro: é o primeiro segmento do caminho atual do
  arquivo. Assim a pasta de destino é sempre a pasta de onde o arquivo veio, sem depender de o
  nome cadastrado da unidade bater com o nome da pasta (`FICCO/ES` × `FICCO-ES`).
- **O número do inquérito vira nome de pasta** por `sanitizar_inquerito`: `/`, `\` e os
  caracteres proibidos no Windows viram `-` (`064/2025` → `064-2025`).
- **Pasta de RIF é renomeada** para `RIF_<número>` quando o número foi reconhecido — as pastas do
  SISCOAF chegam como `RIF 128302`, `RIF_128302`, `RIF 128302 - análise`…
- **Colisão de nome** no destino ganha sufixo numérico (`Relatorio (2).ufdr`), e é o nome com
  sufixo que vai para o banco.
- **Idempotente**: `ja_organizado` reconhece um caminho que já passa por uma pasta de categoria e
  não mexe nele de novo.

### Na tela: só o nome do arquivo

O caminho relativo é o que a API devolve, mas o card não precisa mostrar onde o arquivo está no
servidor. `AnalyserUtils.nomeArquivo` (em `web/js/utils/utils.js`) devolve só a última parte do
caminho, e `AnalyserUtils.encurtarCaminhos` aplica isso em `no_arquivo`/`no_pasta`/
`no_arquivo_ufdr` de uma lista inteira — cada tela chama uma vez, no ponto em que a lista chega
da API, e todo o resto (título, tooltip, menu de hash, diálogo de apagar) já sai encurtado.

Cuidado ao mexer: `no_arquivo`/`no_pasta` de `ARQ_TB01_ARQUIVOS` são o nome e a pasta de um
arquivo **dentro** da extração (tela "Arquivos", `web/js/arquivos.js`) — nada a ver com a pasta
em `data/` e não devem ser encurtados. Por isso o encurtamento é aplicado tela a tela, e não num
interceptador global de AJAX.

### Resolução do arquivo depois da importação

`organizacao_arquivos.resolver(valor)` é o caminho único de volta ao arquivo em disco, usado por
`_localizar_ufdr` (anexos, OCR, comparação facial, relatório Word), pelo IPL/IRPF em PDF e pelo
tamanho em disco no relatório:

1. `data/<valor>` — o caso normal, com o caminho relativo gravado na importação;
2. se não achar, **busca pelo nome** em `data/` e abaixo (até 5 níveis, pulando `index/`,
   `storage/`, `lib/` e as pastas de saída do sistema). É o que cobre o que foi importado antes
   desta estrutura (nome puro no banco) e o que o investigador moveu à mão.

Path traversal (`..`) é barrado na entrada de `resolver`.

## Backfill do que já estava importado

`api/tools/backfill_organizar_arquivos.py` move o que foi importado antes e atualiza as seis
colunas da tabela acima. Rodar de dentro do contêiner da API:

```bash
docker compose exec api-1 python /app/tools/backfill_organizar_arquivos.py --dry-run
```

```bash
docker compose exec api-1 python /app/tools/backfill_organizar_arquivos.py
```

`--categoria ufdr` (repetível) restringe o passe. É idempotente: o que já está organizado é
pulado. Registro sem número de inquérito, ou cujo arquivo não está mais em `data/`, é só
reportado — nada é apagado nem sobrescrito.

Sem rodar o backfill nada quebra: o que ficou para trás continua com o nome puro no banco e
segue sendo encontrado pela busca por nome descrita acima.
