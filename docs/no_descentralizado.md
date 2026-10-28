# Nó descentralizado de extração

> Uma delegacia com servidor próprio: os `.ufdr` e documentos ficam na pasta `data/` **da
> delegacia**, e o central (web + banco) usa aquele servidor para o trabalho que depende do
> arquivo — hashear, parsear documento e ler o pacote. Todo o resto (banco, sessões, IA,
> relatórios, busca) continua exclusivamente no central.

## Por que

100+ delegacias, um servidor central. Os `.ufdr` têm dezenas de GB e a extração é CPU/disco.
Se os arquivos tivessem que subir para o central, a WAN seria o gargalo e o central, o único
ponto de processamento. O nó resolve as duas coisas: **o trabalho vai até o arquivo** (hash e
parse rodam na delegacia), e o que atravessa a rede é só o resultado — alguns MB de texto e
metadados, nunca os dezenas de GB do pacote.

## Um projeto, dois papéis

O mesmo repositório sobe como qualquer um dos dois, decidido por `PAPEL` no `.env`:

| | `PAPEL=central` (default) | `PAPEL=no` |
|---|---|---|
| Postgres, migrações, nginx/web | ✅ | ❌ (nem pool, nem pool aberto no startup) |
| Rotas de usuário (`/api/*`) | ✅ | ❌ |
| Rotas internas | `/api/no/*` (o nó fala com o central) | `/no/*` (o central fala com o nó) |
| Fila de extração / jobs / agendador | ✅ (processa o que é seu) | ❌ (só heartbeat de 60 s ao central) |
| `data/` | delegacias sem nó | a pasta da própria delegacia |

Sobe com um compose próprio, o **`docker-compose.no.yml`** (mesma imagem `./api`):

```
# na máquina da delegacia (NUNCA no home do usuário — ver docs/instalacao_no.md, passo 2)
sudo mkdir -p /mnt/dados/analyser && sudo chown "$USER:$USER" /mnt/dados/analyser
git clone <repo> /mnt/dados/analyser && cd /mnt/dados/analyser
# editar .env: PAPEL=no, CENTRAL_URL=http://<central>:<porta>, NO_TOKEN=<token da tela>
docker compose -f docker-compose.no.yml up -d --build
```

### Teste local: central e nó na MESMA máquina

Duas pastas-irmãs com o mesmo repositório (mesmo commit — o papel é o que muda):

```
C:\Git\analyser      -> central   (docker-compose.yml)       web em :8080
C:\Git\analyser-no   -> nó        (docker-compose.no.yml)    /no/* em :8090
```

1. **Central**: `docker compose up -d --build` (como sempre).
2. **Nó**: copie o `.env` do central para a pasta do nó e ajuste só o bloco do nó:
   `PAPEL=no`, `CENTRAL_URL=http://host.docker.internal:8080`, `NO_TOKEN=` (vazio por ora),
   `NO_PORT=8090`. Suba com `docker compose -f docker-compose.no.yml up -d --build`.
   `host.docker.internal` resolve para o próprio computador de dentro de qualquer container
   (Docker Desktop; no Linux o compose já declara o `host-gateway`) — é por ele que os dois se
   acham sem depender do IP da rede.
3. **Registro**: no central, Administração → Servidores → novo nó, delegacia = a sua,
   `TX_URL_BASE=http://host.docker.internal:8090`. Copie o token, cole no `NO_TOKEN` do `.env`
   do nó e `docker compose -f docker-compose.no.yml up -d` (recria com o token). **Testar** na
   tela = ponto verde.
4. **Botão da delegacia**: Administração → Delegacias → coluna **"Usa o Nó"**. Ligado = a
   delegacia extrai da pasta do nó (`C:\Git\analyser-no\data\<DELEGACIA>\...`). Desligado = a
   delegacia continua extraindo da pasta do **principal** (`C:\Git\analyser\data\...`) mesmo
   com o nó cadastrado e online — dá para comparar os dois caminhos com o mesmo registro.

**Build não apaga dado nenhum — nunca.** Os dados não moram nas imagens: o banco é o volume
nomeado `postgres_data`, e os arquivos são o bind `./data` de cada pasta (o do nó é o `data`
DELE). `build`, `up -d --build`, `restart` e até `down` (sem flags) não tocam em nenhum deles.
O único comando que **apaga o banco** é `docker compose down -v` (o `-v` derruba os volumes
nomeados) — não use, e por isso o compose do nó nem tem volume nomeado: só bind de pasta.
Da mesma forma, **não** rode o `docker-compose.yml` completo dentro da pasta do nó: os
containers teriam o mesmo nome dos do central e um `up` derrubaria os dele.

## Registro e token (Administração → Servidores)

Tabela `INVESTIGACAO.NO_TB01_SERVIDORES` (uma linha por nó) +
`NO_TB02_SERVIDOR_DELEGACIAS` (quais delegacias o nó atende). **Um nó pode atender várias
delegacias; cada delegacia está em no máximo um nó** (índice único `UX110` no vínculo). Campos:
nome, delegacias (vínculo editável na tela — botão **Editar**), `TX_URL_BASE` (o endereço que o
*central* alcança no nó) e `TX_TOKEN`.

O token é compartilhado nos dois sentidos (`Authorization: Bearer <token>`): o nó o guarda no
`.env` (`NO_TOKEN`) e o central, na linha do registro. É em claro dos dois lados porque o
central precisa *apresentar* o token ao chamar o nó — revogar é **Trocar token** na tela: o nó
para de autenticar imediatamente e só volta quando o `.env` dele receber o valor novo.

A tela mostra o token **uma única vez** (na criação/troca), o ponto online/offline (batimento <
3 min), a versão que o nó reportou no último ping e um botão **Testar** (o central bate no
`/no/saude` do nó). Inativar um nó devolve a delegacia ao central na hora, sem mexer em dado
nenhum.

## O que muda no fluxo quando a delegacia tem nó

Tudo parte de uma pergunta — `no_utils.servidor_ativo_para(delegacia)` — e este repositório é o
**CENTRAL PURO**: ele não processa importação nenhuma. Sem nó ativo/online para a delegacia, cada
fluxo abaixo é **recusado** (409 com `no_utils.SEM_NO_ATIVO_MSG` nas rotas, `ValueError` nos
utils) — não existe mais caminho local de data/, Tesseract, faster-whisper, CLIP, JRE ou Ollama
na imagem do central. Os modelos e os discos vivem no nó (outro repositório, com o código de
`PAPEL=no`).

A delegacia também tem a coluna **`IN_NO_ATIVA`** (checkbox **"Usa o Nó"** na tela
Administração → Delegacias): com ela desligada o central finge que o nó não existe — e como o
central não processa nada, as importações da delegacia ficam **indisponíveis** até religar.
Extrações que já *nasceram* no nó continuam sendo lidas por ele (é o `ID_SERVIDOR` da linha que
manda, não a flag).

1. **Seletores de arquivo** (`GET /api/investigacao/arquivos`, `GET /api/documentos/arquivos`):
   a listagem vem da pasta do nó (`GET /no/listar`). Sem nó ativo a rota devolve **409** com o
   motivo, e o seletor da tela mostra o texto do servidor ("Indisponível no central").
2. **Extração `.ufdr` (`POST /api/investigacao/extrair`) — o NÓ é o operador**: a linha nasce com
   `INQ_TB02_EXTRACOES.ID_SERVIDOR` preenchido (snapshot — trocar o nó da delegacia depois não
   reaponta extrações antigas). Na hora de processar o despachante **não abre o arquivo**: chama
   `POST /no/extrair` e sai da frente. O nó roda a pipeline REAL na CPU da estação (código do
   repositório do nó) e devolve
   ao central só o que ela produz:
   - **canal de entrega** (`funcoes/canal_extracao.py`): cada escrita de domínio da pipeline, em
     vez do INSERT, emite um registro JSON com os MESMOS dicts naturais que receberia (nada de id
      gerado no banco viaja). Os registros viram **lotes JSONL** no disco do nó (`data/.entrega/`),
      entregues em sequência por `POST /api/no/entrega/<ide>/lote/<n>` (se o `data/` do nó não for
      gravável pelo usuário do container — instalação que deixou a pasta como root —, o spool cai
      para `/tmp/analyser-entrega/` com aviso no log, e toda falha de preparação do spool vira
      callback `erro`, nunca uma extração presa em "processando"); o central reproduz cada
     registro chamando a MESMA função de gravação local — ON CONFLICT, dedup e gatilhos idênticos
     ao caminho local (paridade 3 conversas/120 mensagens verificada nos dois caminhos);
   - **estados** (fase, progresso, batida de vida, hashes, contagens, info do aparelho, erro,
     finalizar) são callbacks `POST /api/no/entrega/<ide>/estado` — é o que mantém card, órbita e
     watchdog funcionando sem o central tocar no arquivo; a resposta do callback carrega
     `cancelar`, por onde o "parar" da tela chega ao nó;
    - **transcrição**: o áudio já está no disco do nó — o faster-whisper roda LÁ e só o
      TEXTO volta nos lotes (`UPDATE` por `co_caminho_zip`, chave natural). O botão
      "Transcrever" de uma extração pronta também é remoto: `POST /no/transcrever` no nó, com
      progresso/textos/fecho voltando pelos mesmos callbacks de estado (`transcricao` /
      `transcricao_fim` em `canal_extracao.aplicar_estado`);
    - **quem fica no central**: a recusa por duplicidade interestações (só o banco do central vê os
      outros aparelhos; o nó manda os hashes, o central confere e manda cancelar se for gêmeo),
      PIX e identificação de pessoas — pós-entrega, porque exigem o banco. OCR/CLIP/facial/embeddings
      rodam TODOS no nó: o central só cria o job com `ID_SERVIDOR` (claim/plano/lote/estado em
      `jobs_no.py`) e reproduz os lotes de resultado (`jobs_no.aplicar_lote`);
    - **retomada**: o recibo `NO_TB03_LOTES_ENTREGA` (UX por `(id_extracao, nu_lote)`) torna a
      entrega idempotente — o nó reenvia do lote não confirmado; redespacho após queda apaga a
      tentativa parcial antes (`no_entrega.reiniciar_entrega`) para a passada nova nascer limpa;
    - **geração do despacho** (`INQ_TB02_EXTRACOES.NU_GERACAO`, migração 0059): cada claim sobe
      o número e o nó o carimba em cada lote/estado; o central devolve **409** à passada de uma
      geração anterior — o redespacho do watchdog (que é só temporal) nunca mais divide o banco
      com uma entrega VIVA de uma partição de rede recém-curada. No nó, redespacho com geração
      nova ordena a passada viva sair e assume o lugar (o flock da entrega espera ela desembalar);
    - **lote envenenado**: lote escrito de forma atômica no nó (tmp+fsync+replace — nunca linha
      truncada); lote que o central NUNCA vai aceitar (linha corrompida / tipo que ele não
      conhece) devolve **422**: o nó grava o arquivo como `.rejeitado` (quarentena para
      diagnóstico), marca a extração `erro` com a causa e segue — acabou o deadlock do reenvio
      infinito do mesmo lote;
    - **fechamento recuperável**: o `finalizar` marca `cd_fase='fechando'` (persiste a intenção)
      e o despachante do central retoma os fechamentos órfãos de um restart no meio da cauda;
      o `_finalizar_extracao` tenta 3× e, no limite, vira `erro` com causa — nunca `processando`
      para sempre com tudo entregue;
   - **limite**: `EXTRACOES_SIMULTANEAS` agora também é o teto LOCAL do nó — cada estação drena a
     sua fila na sua CPU (o central não é mais o gargalo de parse dos nós; 10 extrações pelo nó de
     teste fecharam 10/10 com o central ocioso).
  3. **Documento avulso** (`extrair-documento`: IPL, IRPF, IPEI, telefonia, texto genérico): o
     documento **mora só no nó** — o upload do navegador é repassado para lá e o arquivo nunca é
     espelhado para o central. A extração do texto roda no nó (`POST /no/parse`) e o organize também
     (`POST /no/organizar`: o nó move para `data/<delegacia>/<inquérito>/OUTROS/` na estação); o
     banco grava o texto + `INQ_TB04_DOCUMENTOS.ID_SERVIDOR` apontando a casa do arquivo. Quando o
      "Original", a reextração ou os blocos IPL/IRPF/IPEI/telefonia exigem o arquivo em disco, o
      `resolver()` materializa uma cópia **volátil e escondida** (`data/.no_cache/`, nunca listada,
      apagável — refaz sozinha por `GET /no/arquivo`). RIF e SIMBA (pasta/CSV) moram no nó da
      mesma forma: o upload cai na data() da estação, o `_validar_*` espelha a pasta/o CSV só
      enquanto o parser roda no central (pastas percorrem dezenas de CSV), e o organize é do nó
      (`POST /no/organizar` aceita pasta e `novo_nome`): concluída a importação o nó move para
      `data/<delegacia>/<inquérito>/RIF|BANCARIO/` e o central **apaga o espelho** — a cópia da
      prova não fica no principal. Nó fora do ar: o espelho fica onde está, e a reimportação do
      dia seguinte readquire do nó.
      O **ZIP em fila** (`import_zip`) segue o mesmo princípio: a expansão roda no nó
      (`POST /no/expandir-zip`) e cada arquivo de dentro é anexado pelo caminho de nó.
4. **Upload pelo navegador**: o central recebe o corpo como sempre e o **repassa em streaming**
   para a pasta da delegacia (`PUT /no/enviar`, `POST /no/enviar-zip`) — um `.ufdr` de dezenas de
    GB não fica na memória nem no disco do central, e o nome final (sufixo "(2)") é decidido pelo
    dono da pasta. Vale para TODAS as abas com envio: documento, `.ufdr`, **pasta do RIF**
    (`salvar_rif_enviado` repassa arquivo por arquivo; o nome livre da pasta é reivindicado contra
    `GET /no/listar?tipo=rif`) e **R304 do SIMBA** (cabeçalho conferido num temporário de sistema
    ANTES de tocar o nó — CSV errado não deixa lixo na estante; o temporário morre na requisição).
    O progresso (`GET .../ufdr-upload/progresso`) atravessa a ponte: o `.parcial`
   existe no nó, com o nome do token do front. A **"Copiar de outra pasta de rede" (SMB,
   `rede_smb.py`)** segue o mesmo caminho: com nó ativo, cada arquivo sai do compartilhamento
   de origem e sobe direto para a estação (gerador bloco a bloco → `no_ponte.enviar_arquivo`);
   nada é gravado no `data/` do central, o cancelamento aborta o corpo no meio (o nó apaga o
   `.parcial`) e o progresso/bytes do job continuam os mesmos.
5. **Mídia e enriquecimento (o "passaporte")**: `investigacao_utils.fonte_ufdr(no_arquivo)` responde
   Path local **ou** `no_ponte.FonteRemota`, e `ufdr_zip.abrir`/`ufdr_cache` aceitam os dois — UMA
   decisão roteia todos os leitores: imagens/áudio/vídeo/anexos das telas, metadados EXIF/QuickTime
   e relatórios com imagens. O CPU pesado (Tesseract, CLIP, whisper, embeddings) **não existe mais
   no central**: roda no nó (jobs/`/no/embed`); pela WAN passam só os membros lidos (alguns KB/MB)
   para as telas, nunca o pacote.
6. **Nuvem embutida** (ZIP/TAR aninhado do Google/iCloud dentro do `.ufdr`): as janelas de bytes do
   `ufdr_cache` viram faixas HTTP sobre o `ArquivoRemoto` — o passe roda no nó sem mudar nada.
7. **Batimento**: qualquer chamada do nó ao central atualiza `DT_ULTIMO_HEARTBEAT`; o ping
   periódico (`POST /api/no/ping`, 60 s) cobre o nó ocioso e carrega a versão.

## Segurança

- as rotas do nó (`/no/*`) não fazem sentido sem token: comparação **constante-tempo** com
  `NO_TOKEN`; o nó não tem sessão nem usuário;
- as rotas do central para nós (`/api/no/*`) resolvem o nó pelo token, comparam
  constante-tempo, recusam nó inativado (403 ≠ 401) e batem o heartbeat;
- todo `caminho` que entra num `/no/*` é relativo a `data/` e passa pelo mesmo resolver que
  recusa `..` e caminho absoluto — o nó jamais lê fora da pasta de evidências;
- o token viaja no header; se a rede central↔nó não for confiável, termine TLS num proxy na
  frente da porta do nó (a URL base vira `https://`). Registrar nó com `http://` loga aviso
  explícito — o MESMO segredo autentica os dois sentidos, e em claro ele vale uma troca;
- tentativas FRUSTRADAS de token são contabilizadas por IP nos dois lados (`/no/*` no nó e
  `/api/no/*` no central): 40 falhas em 5 min do mesmo endereço viram **429**. Quem acerta o
  token nunca é contabilizado — o freio não alcança nó legítimo;
- o túnel NÃO aceita mais o token na query string (iria para o log de acesso de qualquer
  proxy no caminho): `TUNEL_TOKEN_QUERY=1` no central reativa o trilho para o caso do proxy
  corporativo que come o header, ciente do risco;
- uploads recebidos pelo nó têm teto mesmo sem `max_bytes` do central: `NO_ENVIO_MAX_MB`
  (default 512 GiB, folga para .ufdr legítimo) e `NO_ZIP_MAX_MB` (default 4 GiB — o spool do
  ZIP mora em tmpfs, é RAM);
- o nó varre periodicamente (a cada 6 h, e no startup) os `.parcial` órfãos de upload —
  recursivamente, alcançando as pastas organizadas — e os spools `data/.entrega/<id>` /
  `/tmp/analyser-entrega/<id>` de extrações mortas (3 dias sem toque e sem flock vivo);

## Túnel NAT (WebSocket reverso)

O desenho exige que o central alcance o nó (`TX_URL_BASE`). Delegacia atrás de NAT não tem porta
aberta — mas o nó SEMPRE alcança o central (é assim que o heartbeat sobrevive). O túnel inverte o
cano com esse fato:

1. o nó abre um WebSocket persistente para `<CENTRAL_URL>/tunel/ws/node` (serviço **`tunel`** do
   `docker-compose.yml` — relay HTTP↔WS em processo ÚNICO, porta interna 3001, nada publicado;
   processo único é de propósito: o soquete vive no processo que o recebe, e a API tem 2×N
   processos);
2. o relay autentica o nó (`no_utils.no_autenticado`) e grava `NO_TB01_SERVIDORES.DT_TUNEL_ATIVO`
   no connect, a cada PING de 30 s e NULL no disconnect;
3. `no_ponte._url()` lê a coluna (fresca < 120 s = `tunel_ativo`) e manda toda chamada central→nó
   para `http://tunel:3001/t/<id>/no/...` — o relay desce o pedido pelo cano e devolve a resposta
   em streaming. O nó executa o pedido contra o PRÓPRIO uvicorn (`127.0.0.1:3000/no/*`): mesmo
   código, mesmos guards, mesma autenticação; o túnel é só um caminho de chegada a mais.

Protocolo (`api/funcoes/tunel_proto.py`): quadros binários multiplexados por `req_id`
(REQ/CHU/FIM/CAN/RESP/ERR/PING), corpo em streaming nos dois sentidos — o upload repassado de um
.ufdr de dezenas de GB nunca é materializado inteiro em lugar nenhum.

`TUNEL_NO_ATIVO=0` no .env do nó desliga o cano; com o serviço `tunel` fora do ar (central ainda
sem o compose novo) o nó loga uma vez, tenta em backoff e funciona 100% pela porta publicada.

## Jobs de imagem no nó (OCR / classificação CLIP / comparação facial)

No central puro os jobs de imagem **sempre** vão para a estação: a rota exige nó ativo/online
(`jobs_no.exigir_no_para_job` — 409 com o motivo quando não há) e a linha nasce marcada
(`JOB_TB01_JOBS.ID_SERVIDOR`); nenhuma thread de modelo sobe no central. A CPU da estação, que já
tem a prova no disco, faz o Tesseract/CLIP. O nó PERGUNTA (nada empurra para dentro do NAT):

```
POST /api/no/jobes/claim        → "o que é meu?" (SKIP LOCKED; facial: + foto de referência b64)
GET  /api/no/jobes/<id>/plano   → próxima janela de trabalho (cursor por id_arquivo — o avanço é
                                  garantido mesmo quando uma imagem é ilegível e não deixa carimbo)
POST /api/no/jobes/<id>/lote    → NDJSON de resultado (o central grava com as MESMAS funções do
                                  caminho local: _salvar_resultados, _gravar_embeddings)
POST /api/no/jobes/<id>/estado  → progresso/concluir/erro/finalizar/cancelado (resposta traz
                                  `cancelar` — é por aí que o "parar" da tela chega à estação)
```

A face do nó (o operário vive no repositório do nó) reusa as funções puras espelhadas lá
(`_executar_ocr`, `_classificar_lote`, `_embeddings_do_lote`) lendo o `.ufdr` LOCAL; o fechamento
da comparação facial é uma multiplicação de matriz no central contra os vetores devolvidos (o
vetor da referência volta no próprio callback `finalizar` — nenhum CLIP nem imagem no central).
Vaga local: `JOBS_NO_LIMITE` no .env do nó (teto da estação, flock elege um worker por container).
CLIP exige o modelo no cache HF do nó — volume `hf_cache` no `docker-compose.no.yml`; estações sem
saída para o Hugging Face pré-semeiam o volume (com `docker cp` do cache de outro nó).
`ocr_imagem_selecao` (seleção ≤200 imagens pedida pela IA no chat) desce como OCR comum: o plano
filtra pelos ids resolvidos no central (`tx_parametros.ids_arquivos`) e a estação reconhece.

## Rollout de update em levas

Botão "Atualizar todos" da tela Servidores: um job global (`tp_job='rollout_nos'`, escopo `nos`)
conduz levas de até `max_parallel` nós — dispara o `POST /no/atualizar` de cada um, confirma a
volta pela versão reportada (heartbeat + `GET /no/estado`), anota falha individual em
`tx_metricas.falhas` e segue: **um nó que falha não trava as levas seguintes**. Cancelar o job
para de disparar levas novas. O progresso vive na JOB_TB01 (mesma tela de filas) e o painel da
tela lê `GET /api/servidores/atualizacao-todos`. Toda a administração remota
(`/no/atualizar|atualizacao|config|reiniciar|estado`) passou pela ponte — funciona pelo túnel nos
nós atrás de NAT.

## Mídia direta (o navegador baixa do nó, não do central)

Com DNS + certificado provisioning pela TI, o serviço `no-web` do nó sobe HTTPS na porta 8443
e o analista da LAN baixa a mídia do disco onde ela nasceu; o central só assina um passe de
~10 minutos por arquivo (`funcoes/no_ticket.py`, segredo = o próprio token do nó) depois de
conferir usuário e inquérito. Interruptor por nó na tela Servidores (URL de mídia + "Testar
mídia"); qualquer recusa derruba o navegador no proxy de sempre, silenciosamente. Tudo —
arquitetura, ticket, checklist da TI, runbook de certificado — em
[no_midia_direta.md](no_midia_direta.md).

## O que ainda NÃO roda no nó (bloqueado no central até o nó suportar)
- **Nuvem IPED**: o dumper Java exige a árvore do caso (storage SQLite) no disco de quem processa.
  A rota `extrair-iped` devolve **409** ("será liberada quando o nó suportar o dumper") e a aba da
  tela mostra o aviso — `GET /api/investigacao/iped-disponivel` responde `disponivel: false`;
- **Parser RIF/SIMBA**: o `_validar_*` + parse hoje percorre dezenas de CSVs no disco de quem
  processa. O upload já cai na `data/` do nó, mas as rotas `extrair-rif`/`extrair-simba` devolvem
  **409** até o nó expor o parse da pasta/CSV;
- **`POST /no/embed`** (geração de embeddings na estação): protocolo novo — nós antigos respondem
  404 e o central traduz para "atualize o nó"; a busca semântica degrada para aviso e a indexação
  da cauda pós-entrega é pulada;
- **`POST /no/transcrever`** (botão "Transcrever" de uma extração pronta): idem — nós antigos não
  têm o endpoint e a rota devolve mensagem clara.

## Arquivos desta feature

| Arquivo | Papel |
|---|---|
| `db/init/46_nos.sql`, `db/migracoes/0035_no_servidores.sql` | `NO_TB01_SERVIDORES` + colunas `ID_SERVIDOR` (09, 41) |
| `db/init/46_nos.sql (NO_TB02)`, `db/migracoes/0038_no_multi_delegacias.sql` | vínculo nó ↔ N delegacias (UX110: cada delegacia em um só nó) |
| `db/migracoes/0037_delegacia_uf.sql` | `DELEGACIAS.UF` na gerência |
| `db/init/07_seguranca_usuarios.sql`, `db/migracoes/0036_delegacia_no_ativa.sql` | `DELEGACIAS.IN_NO_ATIVA` — o "usa o nó" por delegacia |
| `db/init/46_nos.sql (NO_TB03)`, `db/migracoes/0039_no_lotes_entrega.sql` | recibo por lote entregue (`UX111`) — a idempotência da retomada |
| `api/funcoes/no_utils.py` | registro, token, batimento, `servidor_ativo_para`, `servidor_por_arquivo`, `listagem_remota`, `SEM_NO_ATIVO_MSG` (metade nó apagada daqui — vive no repositório do nó) |
| `api/funcoes/no_ponte.py` | cliente central→nó: `ArquivoRemoto` (Range), `FonteRemota` (passaporte), hash, parse, stream, upload (`enviar_arquivo`/`enviar_zip`), `embed` (`/no/embed`), `iniciar_transcricao_remota` (`/no/transcrever`) |
| `api/funcoes/ufdr_zip.py`, `ufdr_cache.py` | `abrir()`/caches aceitam `FonteRemota` — a costura que dá passaporte a ~25 leitores sem tocá-los |
| (repositório do nó) `rotas/no_servico.py`, `funcoes/no_extracao.py`, `funcoes/no_jobs.py`, `funcoes/tunel_no.py` | a face do nó: `/no/*` (saude, hash, parse, listar, arquivo, enviar, extrair, transcrever, embed, atualizar, config), fila de extração, operário de jobs, cliente do túnel — NÃO existem mais neste repositório |
| `api/funcoes/canal_extracao.py` | o REPLAY do central: `reproduzir_lote`/`aplicar_estado` com as mesmas funções de gravação (emissor/`CanalEntrega` moram no nó) |
| `api/funcoes/no_entrega.py` | recebedor do central: idempotência por lote, callbacks de estado, redespacho limpo, cauda pós-entrega (PIX/identação/embeddings) |
| `api/rotas/no_central.py` | o que o central atende do nó: `/api/no/ping`, `/api/no/entrega/<ide>/lote/<n>`, `/api/no/entrega/<ide>/estado`, `/api/no/entrega/<ide>`, **`/api/no/jobes/claim|plano|lote|estado`** |
| `api/tunel_app.py`, `api/funcoes/tunel_proto.py` | túnel NAT: o relay HTTP↔WS (serviço `tunel`) e o protocolo de quadros (o cliente que roda no nó vive no repositório do nó) |
| `api/funcoes/jobs_no.py` | jobs no nó, metade central: claim/plano/lote/estado + rollout em levas + `exigir_no_para_job` |
| `db/migracoes/0058_tunel_jobs_no.sql` | `NO_TB01.DT_TUNEL_ATIVO` + índice do claim (`IX435`) |
| `db/init/09_extracoes.sql`, `db/migracoes/0059_geracao_entrega.sql` | `INQ_TB02.NU_GERACAO` — a geração que aposenta a passada substituída |
| `api/rotas/servidores.py` | Administração → Servidores (CRUD + trocar token + testar + proxies `/atualizar`, `/estado`, `/config` + **`/atualizar-todos`**/**`/atualizacao-todos`** do rollout) |
| `web/servidores.html`, `web/js/servidores.js` | a tela |
| `api/funcoes/no_ticket.py`, `api/funcoes/midia_direta_utils.py`, `nginx/no-midia.conf.template`, `web/js/utils/midia_direta.js`, `db/migracoes/0061_midia_direta.sql` | mídia direta (docs/no_midia_direta.md): passe assinado, decisão do central, TLS 8443 do no-web, atalho no navegador, colunas do registro |
| `api/rotas/delegacias.py`, `web/delegacias.html`, `web/js/delegacias.js` | o checkbox "Usa o Nó" na configuração da delegacia |
| `docker-compose.no.yml` | o container do nó |
| `investigacao_utils.py` (`fonte_ufdr`, extrair/despachante/processar, uploads), `documentos_utils.py` (anexar, upload, espelho de pasta), `transcricao.py`, `ufdr_nuvem/ufdr_icloud` | os desvios central→nó nos fluxos |
