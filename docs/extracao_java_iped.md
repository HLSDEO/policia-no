# O dumper Java da Nuvem IPED (IpedDumper.java)

Documento dedicado só a essa peça, por pedido explícito de quem revisou o plano desta feature
— cobre por que ela existe, o caminho errado que foi tentado primeiro (e por quê), por que o
caminho certo funciona, como compilar/rodar, e o formato exato dos dados. Para o resto da
extração Nuvem IPED (roteamento, banco de dados, tela), ver
[extracao_iped.md](extracao_iped.md).

## Por que um subprocesso Java

Um caso do IPED guarda seus metadados num índice **Apache Lucene** (`<pasta do caso>/index/`)
— formato binário, com *compound files* e um codec específico da versão do Lucene usada no
processamento. Não existe biblioteca madura para ler isso em Python puro. A pasta do caso já
carrega tudo que é preciso para lê-lo de volta: um JRE embutido (`jre/`) e os `.jar` do IPED,
inclusive o Lucene na versão certa (`lib/lucene-core-*.jar` e afins) — então a leitura
acontece via um subprocesso Java, não via alguma biblioteca Python de terceiros.

## O caminho errado (e por que foi descartado)

A primeira tentativa seguiu a orientação mais óbvia: usar as classes que o próprio IPED expõe
para isso, `iped.data.IIPEDSource`/`IItemReader` (implementadas por `iped.engine.data.
IPEDSource`). Na teoria, é a "API pública" do projeto para ler um caso de fora.

Na prática, abrir um caso assim **não é leitura pura** — o construtor de `IPEDSource` chama
incondicionalmente `Configuration.getInstance().loadConfigurables(moduleDir, true)`, que
carrega **todas** as tasks configuradas no caso: reconhecimento facial, detecção de nudez,
carteiras de hardware — várias delas via Python embutido (`jep`). Isso aconteceu mesmo lendo
um caso já processado, só para consultar metadados.

Dois sintomas, nessa ordem, testando contra o exemplo real (`data/SIP/iped_google/`):

1. **Travamento indefinido.** `PythonTask.getJep()` tentava inicializar o `jep` (JVM ↔ Python
   embutido) e travava esperando uma lib nativa que não existe neste ambiente
   (`UnsatisfiedLinkError: no jep in java.library.path`, capturado numa thread de fundo — o
   processo principal simplesmente nunca avançava).
2. **Removendo o `.jar` do `jep` do classpath** para forçar uma falha rápida em vez de travar,
   o erro mudou para um `NullPointerException` dentro do inicializador estático de **outra**
   task, sem relação nenhuma com Python: `ExportFileTask.<clinit>` → `Messages.
   getExternalBundle()`. Ou seja, `Configuration.loadConfigurables()` depende de um bootstrap
   de localização/recursos que só a aplicação completa do IPED (GUI ou processamento via
   linha de comando) faz antes de tocar em `IPEDSource` — não documentado nas interfaces
   públicas, e não replicável sem entender o bootstrap inteiro do `iped.app.bootstrap.
   BootstrapUI`/`iped.app.processing.Main`.

Confirmado no próprio `iped-api/README.md` do projeto (repositório público, `github.com/
sepinf-inc/IPED`): "most interfaces were generated automatically from implementation classes"
e "interfaces will be refactored for 4.0 version of the software, breaking back compatibility
with 3.x" — ou seja, mesmo a documentação oficial não trata essa API como estável ou pensada
para consumo externo simples.

## O caminho certo: Lucene puro

`IpedDumper.java` não usa nenhuma classe do `iped-api`/`iped-engine`. Usa só Apache Lucene
diretamente, com as classes-padrão de leitura de índice:

```java
FSDirectory dir = FSDirectory.open(indexDir);
IndexReader reader = DirectoryReader.open(dir);
for (int docId = 0; docId < reader.maxDoc(); docId++) {
    Document doc = reader.document(docId);   // pode ser null, ou vir sem nenhum campo armazenado
    // doc.iterator() dá acesso a TODOS os campos armazenados, sem precisar saber os nomes
}
```

Isso evita completamente o sistema de plugins/tasks — só toca a API de leitura de índice do
Lucene, que não depende de nenhuma feature opcional do caso (Python, reconhecimento facial
etc.). Rodou em segundos contra o exemplo (5530 documentos Lucene → 2765 itens com campos — o
resto são documentos internos do índice, que vêm `null` ou sem campo armazenado nenhum e são
pulados; ver "O que a primeira versão errava" abaixo).

Os nomes dos campos usados pelo IPED (`id`, `parentId`, `category`, `hash`, `md5`, `sha-1`,
`sha-256`, `contentType`, `dataSourcePath`, etc.) são strings relativamente estáveis,
documentadas em `iped.properties.BasicProps`/`ExtraProperties` no código-fonte público — mas
o dumper não precisa importar essas classes para usá-los: como ele serializa **todo campo que
existir** no `Document`, não precisa declarar nomes de antemão.

## O que a primeira versão errava

Dois detalhes do Lucene que só apareceram ao medir o dumper contra o caso de exemplo — ver
[extracao_iped.md](extracao_iped.md), seções "Campos multivalorados" e "Documentos vazios do
índice", para os números:

1. **Campo multivalorado virava chave JSON repetida.** O Lucene permite o mesmo nome de campo
   várias vezes no mesmo documento e o IPED usa isso a rodo (`Regex:URL`, `Regex:EMAIL`,
   `Communication:To`, até `category`). Escrever `"nome":"valor"` por ocorrência produz JSON com
   chave repetida, e `json.loads` fica só com a **última** — no exemplo, 6.932 valores de
   `Regex:URL` iam para o lixo e 60 itens ficavam com a categoria errada. Hoje `docToJson` agrupa
   os valores por nome (`LinkedHashMap`, preservando a ordem de aparição, que é significativa:
   `timeStamp` e `timeEvent` saem alinhados posicionalmente) e emite **array** quando há mais de
   um. Campo com um valor só continua saindo como string, para não quebrar quem já consome.
2. **Documento sem campo armazenado era emitido assim mesmo.** O índice tem, além do documento de
   cada item, um documento vazio — metade do `maxDoc`, exatamente alternado, e não são documentos
   deletados (o índice não tem nenhum arquivo `.liv`). Hoje o laço os pula, e o `stderr` separa
   `PROGRESS <docs lidos>/<maxDoc>` (o progresso, cujo denominador é o único número conhecido de
   antemão) de `DONE <itens emitidos>` (o que vira `QT_ITENS_TOTAL`, antes gravado como `maxDoc`
   — o dobro do real).

## Como compilar

```bash
javac --release 11 -cp "<pasta do caso>/lib/lucene-core-9.2.0.jar" -d . IpedDumper.java
```

- `--release 11`: o IPED 4.x roda em Java 11+ (confirmado pelo JRE embutido no caso de
  exemplo, `jre/release`: `JAVA_VERSION="11.0.13"`); compilar visando 11 garante que o
  `.class` roda em qualquer JRE 11 ou mais novo, mesmo que o `javac` usado para compilar seja
  mais recente (usado localmente: JDK 25 via `--release 11`, sem problema).
- Só precisa de `lucene-core` no classpath de compilação — nenhum outro `.jar` (nem os do
  próprio Lucene, como `lucene-queryparser`) é referenciado pelo código.
- O `.class` resultante (`IpedDumper.class`, ~5 KB) é versionado no repositório junto com o
  `.java`, em `api/tools/iped_dumper/` — não precisa recompilar a cada build da imagem. Se
  mexer no `.java`, recompile e commit o `.class` atualizado.

### O segundo programa: `IpedBookmarks`

Os marcadores do perito (`<caso>/iped/bookmarks.iped`) são serialização de objeto Java, e só o
`iped-engine` sabe desserializá-los — o oposto do `IpedDumper`, que foi escrito justamente
para **não** tocar nessas classes. Por isso são dois programas, e não um:

```bash
javac --release 11 -cp "<pasta do caso>/lib/*" -d . IpedBookmarks.java
```

Aqui o classpath precisa do `lib/*` inteiro (o `iped-engine`, e o `slf4j` que ele referencia).
Separados, uma falha na leitura dos marcadores — versão de IPED incompatível, classe faltando,
arquivo corrompido — não contamina a leitura do índice, que é a parte que não pode falhar; o
lado Python (`funcoes/iped_marcadores.py`) trata qualquer saída não-zero como "caso sem
marcador" e segue.

Em runtime precisa de `-Djava.awt.headless=true`: o objeto serializado carrega `java.awt.Color`
e `javax.swing.KeyStroke` (a cor e o atalho de teclado de cada marcador). Validado com um
`bookmarks.iped` sintético (gerado por um `Bookmarks` de verdade) lido de dentro da imagem da
API, que roda Java 21 sobre `.class` de Java 11.

## Como é invocado em runtime

`_classpath_dumper()` (`api/funcoes/iped_utils.py`) monta o classpath com **os jars do próprio
caso sendo importado** (`<pasta do caso>/lib/*`) + a pasta do dumper compilado — não uma cópia
nossa dos jars do Lucene. Isso importa para compatibilidade entre versões: cada caso carrega
a versão do Lucene compatível com o índice que ele mesmo gerou, então o dumper sempre lê com
a versão certa, não uma fixa que escolhêssemos nós.

```bash
java -cp "<pasta do caso>/lib/*:/app/tools/iped_dumper" IpedDumper <pasta do caso>
```

O JRE vem da própria imagem da API (`default-jre-headless`, ver `api/Dockerfile`) — **não** o
JRE embutido no caso (evita depender de bit de execução/permissões de um JRE que veio de um
host Windows, por exemplo).

`resolveIndexDir()` dentro do `IpedDumper` aceita tanto a pasta módulo do caso direto (com
`index/` dentro) quanto a raiz do caso que contém isso numa subpasta `iped/` — os dois formatos
foram vistos na prática (ver [extracao_iped.md](extracao_iped.md), seção "A pasta pode vir de
duas formas").

## Formato de saída

**stdout**: um JSON por linha, um por documento do índice:

```json
{"luceneDocId": 3, "fields": {"id": "33", "name": "arthurdimas18@gmail.com.873696194465.GooglePlayStore.OrderHistory_001.zip", "path": "Dados/arthurdimas18@gmail.com.873696194465.GooglePlayStore.OrderHistory_001.zip", "category": "Compressed Archives", "contentType": "application/zip", "hash": "7D5BA15176C76E5D308CC9F904D1CFFC", "md5": "7D5BA15176C76E5D308CC9F904D1CFFC", "sha-1": "...", "sha-256": "...", "size": "631", "parentId": "0", "deleted": "false", "isDir": "false", "dataSourcePath": "../Dados", "dataSourceDecoder": "iped.utils.FileInputStreamFactory", "idInDataSource": "arthurdimas18@gmail.com.873696194465.GooglePlayStore.OrderHistory_001.zip", ...}}
```

Documentos internos do índice (sem item associado) viram `{"luceneDocId": N, "fields": {}}` —
o lado Python (`iped_utils.py`) pula qualquer linha com `fields` vazio.

**Limitação conhecida**: campos com múltiplos valores no mesmo documento Lucene (ex. o par
`timeStamp`/`timeEvent`, repetido uma vez por tipo de data — criação/modificação/acesso) saem
como chaves JSON duplicadas no texto gerado. Isso é JSON tecnicamente inválido pro conceito de
objeto (chaves não deveriam repetir), e o parser do lado Python (`json.loads`) fica só com o
**último** valor de cada chave duplicada — os anteriores são silenciosamente descartados. Não
afeta os campos usados hoje (nenhum dos destinos atuais depende de campos multivalorados), mas
é uma limitação real para qualquer uso futuro que precise deles — corrigir exigiria agrupar
valores repetidos em uma lista por nome de campo antes de serializar.

**stderr**: só progresso e erros — nunca dados, para o lado Python poder separar por stream:

```
INDEX_DIR /app/data/SIP/iped_google/index
PROGRESS 0/5530
PROGRESS 500/5530
...
PROGRESS 5530/5530
DONE 5530
```

`_processar_importacao_iped` lê stdout e stderr em paralelo (uma thread dedicada para stderr)
— um subprocesso com muita saída em ambos os streams pode travar se só um lado for lido por
vez (o pipe do lado não lido enche e o processo filho bloqueia esperando alguém drenar).

## Compatibilidade entre versões do IPED

Menor risco do que dependeria se o dumper usasse a API de objetos do IPED, porque agora ele só
depende de:

1. **Qual versão do Lucene aquele caso usa** — visível pelo nome do jar (`lucene-core-9.2.0.jar`
   no exemplo). Como o classpath em runtime sempre usa os jars do próprio caso (ver acima), o
   dumper linka contra a versão certa automaticamente — não precisa saber de antemão.
2. **Nomes de campo do IPED** (`id`, `category`, `hash`, etc.) — convenções internas
   relativamente estáveis (conferidas contra `BasicProps`/`ExtraProperties` no código-fonte
   público), mas sem garantia formal entre versões majors do IPED.

Risco residual, sem solução implementada ainda (registrado como backlog em
[extracao_iped.md](extracao_iped.md)): se uma versão futura do IPED mudar a versão major do
Lucene de um jeito que quebre compatibilidade de leitura, ou renomear campos usados pelo
roteamento, o dumper falha — hoje isso vira um erro amigável na extração (capturado e
truncado, não um traceback cru), mas não há teste automatizado contra múltiplas versões do
IPED. Uma correção mais robusta seria compilar o dumper sob demanda contra os jars do próprio
caso (`javac` já resolveria pela mesma classpath usada em runtime) em vez de usar o `.class`
pré-compilado fixo — eliminaria esse risco por completo, ao custo de alguns segundos de
compilação por importação.

## Referências

- `api/tools/iped_dumper/IpedDumper.java` — o código-fonte, com o relato do caminho
  descartado no comentário de topo do arquivo.
- `api/funcoes/iped_utils.py` — `_classpath_dumper`, `_processar_importacao_iped` (invocação
  e leitura do subprocesso), `detectar_conta_iped`/`_DetectorConta` (segundo consumidor do
  mesmo dumper, para a pré-visualização da conta).
- [extracao_iped.md](extracao_iped.md) — o resto da extração (roteamento por item, banco de
  dados, tela, identificação de conta).
- Repositório público do IPED: `github.com/sepinf-inc/IPED` — `iped-api/README.md` (nota
  sobre estabilidade das interfaces), `iped-api/src/main/java/iped/data/IIPEDSource.java` e
  `IItemReader.java` (a API descartada), `iped-engine/src/main/java/iped/engine/data/
  IPEDSource.java` (o construtor que carrega `Configuration.loadConfigurables`).
