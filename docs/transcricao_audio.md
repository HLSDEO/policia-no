# Transcrição de áudio

## Visão geral

Os áudios (e, opcionalmente, os vídeos) das conversas extraídas de um arquivo UFDR são transcritos
**localmente**, com o **faster-whisper** (CTranslate2) rodando na CPU dos servidores — não há
chamada externa para nenhum serviço de nuvem. A transcrição acontece **durante a extração**, na
fase `transcrevendo`, como última etapa antes da indexação semântica: o investigador opta, no
formulário de extração, pelo modelo de transcrição (qualidade) e por transcrever também os vídeos,
e essas escolhas ficam gravadas **por extração**.

O resultado vai para a coluna `TX_TRANSCRICAO` do anexo (a coluna fica nula enquanto não transcrito
ou quando o áudio falha) e passa a alimentar todos os consumidores de texto do inquérito: a busca
de mensagens (inclusive a da IA), a detecção de padrões de interesse, a indexação semântica
(embeddings) e a linha do tempo de eventos; no visualizador de conversa a transcrição aparece junto
à mensagem (embaixo da bolha ou no modal do player) e nos relatórios DOCX ela entra nas tabelas de
anexos e na transcrição das conversas. O total da extração é contabilizado em
`QT_AUDIOS_TRANSCRITOS` (é ele que o card da extração mostra).

A implementação se divide em duas camadas:

- `api/funcoes/transcricao.py` (109 linhas) — `transcrever_lote`, o lote em paralelo: resolve a
  configuração, baixa o modelo, abre um pool de processos e transcreve com progresso e
  cancelamento cooperativo;
- `api/funcoes/investigacao_utils.py` — `_transcrever_audios_pendentes` seleciona os anexos de
  áudio/vídeo da extração (WhatsApp + SMS + demais mensageiros num só lote) e grava os textos de
  volta no banco.

A inferência usa `WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=2)` por
worker: quantização int8 para reduzir memória e `cpu_threads=2` por processo para não saturar a CPU.

## Fluxo de uso

1. No formulário de extração do UFDR (tela Investigação), escolher a **qualidade da transcrição**
   e, se quiser, marcar **"Transcrever também o áudio dos vídeos"**:
   - **Baixa** (`base`) — mais rápida;
   - **Média** (`small`, recomendado, padrão) — equilíbrio entre velocidade e precisão;
   - **Alta** (`large-v3`) — mais precisa, porém bem mais lenta e com mais uso de memória.
   As escolhas ficam registradas na extração (`TX_MODELO_TRANSCRICAO`, `IN_TRANSCREVER_VIDEO`).
2. Durante a extração, a fase vira `transcrevendo` e o badge do card mostra **"Transcrevendo
   áudios: N%"** — aqui o denominador é exato (a lista de áudios já está montada antes de começar,
   diferente de fases como `contagens`). O card já exibe as contagens desde a fase `contagens`: a
   transcrição é a última etapa justamente para não bloquear a navegação nas conversas/contatos,
   que já estão gravados.
3. Ao concluir, `QT_AUDIOS_TRANSCRITOS` guarda quantos áudios saíram com texto (0 ou nulo se nada
   deu certo; falhas individuais não derrubam a extração).
4. As transcrições passam a ser consultáveis:
   - na **busca de mensagens da IA** — os termos casam também com o texto transcrito e cada
     resultado traz a coluna agregada `transcricao_audio` (mensagem de voz não tem `tx_mensagem`:
     o conteúdo dela só existe na transcrição);
   - na **detecção de padrões** (léxico/regex) — as fontes `wpp_mensagem` e `sms_mensagem` incluem
     as transcrições dos anexos;
   - na **indexação semântica** — o trecho de uma mensagem de voz é a própria transcrição;
    - no **visualizador de conversa** — transcrição embaixo da bolha da mensagem de voz e no modal
      do player de áudio (e de **vídeo**, quando a extração transcreveu o áudio dos vídeos), onde
      também dá para **corrigir manualmente** o texto (botão "✎ corrigir"
      no modal), quando o investigador percebe que o faster-whisper errou o que foi dito; a correção
     substitui `TX_TRANSCRICAO` na hora, refletindo em todos os consumidores acima (busca, padrões,
     indexação semântica não é reprocessada automaticamente — ver abaixo);
   - nos **relatórios DOCX** — transcrições nas tabelas de anexos e na linha "Transcrição: …" das
     conversas.
5. É possível **cancelar a extração** durante a fase: o pedido de parada (`fl_cancelar`) é checado
   após cada áudio; os áudios já transcritos até o momento ficam salvos e a extração termina como
   `cancelado`.

## Como funciona

### Seleção dos anexos (investigacao_utils.py)

1. `_obter_config_transcricao(conn, id_extracao)` lê `TX_MODELO_TRANSCRICAO` e
   `IN_TRANSCREVER_VIDEO` da extração (valores por extração; se não informados, vale o padrão do
   ambiente, `TRANSCRICAO_MODELO`).
2. `_transcrever_audios_pendentes(conn, id_extracao, caminho, modelo, incluir_video)` monta a
   lista de tarefas consultando as **três** tabelas de anexos de conversa
   (`wpp_tb05_anexos`, `sms_tb05_anexos`, `msg_tb05_anexos`), com dois filtros:
   - `TP_CONTEUDO LIKE 'audio/%'` — ou `'audio/%' OR 'video/%'` quando `incluir_video` (o
     faster-whisper lê a trilha de áudio do container; vídeo mudo simplesmente não rende texto);
   - `CO_CAMINHO_ZIP IS NOT NULL` — o binário precisa ter sido localizado na ingestão.
   Cada tarefa carrega uma chave com o prefixo da tabela (`wpp:123`, `sms:123`, `msg:123`) para
   casar o resultado de volta com o anexo certo.
3. O binário **nunca é duplicado no banco**: os workers leem do próprio arquivo `.ufdr`. O
   `CO_CAMINHO_ZIP` foi localizado na ingestão por `ufdr_parser._extrair_anexos`, casando o anexo
   ao membro do ZIP **por tamanho exato em bytes** — só quando o casamento é inequívoco (um único
   candidato daquele tamanho); em caso de colisão o campo fica nulo e o áudio fica de fora.

### O lote em paralelo (transcricao.py)

1. `_configuracao(modelo_override)` resolve `TRANSCRICAO_THREADS` (padrão 4; se o valor do
   `.env` não for um inteiro válido, cai no padrão 4 em vez de derrubar a extração) e
   `TRANSCRICAO_MODELO` (padrão `small`); o override da extração substitui o modelo do `.env`
   (se o override vier vazio/`None`, também cai no padrão). O número de workers é
   `min(threads, total_de_tarefas)` — uma extração com só 2 áudios nunca sobe 4 processos à
   toa; `max(1, threads)` garante pelo menos 1 mesmo se `TRANSCRICAO_THREADS=0`.
2. `faster_whisper.utils.download_model(modelo)` baixa/verifica o modelo **uma única vez no
   processo pai**, antes de subir os workers. Sem isso, todos os N workers tentariam baixar o mesmo
   modelo (~500 MB) do HuggingFace Hub ao mesmo tempo na primeira execução (cache frio) — lento,
   desperdiça banda e pode travar na contenção de lock de arquivo.
3. `multiprocessing.Pool(spawn)` com `initializer=_inicializar_worker`: cada processo filho carrega
   o `WhisperModel` uma vez e o reutiliza em todos os áudios que processar (evita pagar a carga a
   cada arquivo). Processos separados (não threads) contornam o GIL para o trabalho pesado de
   inferência. O import do `faster_whisper` é tardio (dentro do worker): o processo pai da API
   nunca paga o custo de importação do modelo.
4. Cada worker (`_transcrever_um`) extrai o áudio do ZIP para um **arquivo temporário mantendo a
   extensão original** (`.opus` se não houver) — o decodificador precisa da extensão certa —,
   via `tempfile.NamedTemporaryFile(delete=True)`: o arquivo é apagado automaticamente ao sair
   do bloco `with`, mesmo em falha, então um lote de milhares de áudios não deixa lixo no disco
   do servidor. Transcreve com `transcribe(caminho, language="pt", vad_filter=..., beam_size=...)`
   (**idioma fixo**, sem autodetecção — um áudio majoritariamente em outro idioma não gera erro,
   só um texto incorreto/sem sentido, já que o decodificador força a gramática do português;
   `vad_filter`/`beam_size` vêm de `TRANSCRICAO_VAD`/`TRANSCRICAO_BEAM`, padrões ligados/guloso
   porque o servidor não tem GPU), concatenando os segmentos com espaço. Qualquer falha (membro ausente, ZIP corrompido, áudio sem fala
   reconhecível) devolve a chave sem texto: a tarefa fica de fora do resultado e
   `TX_TRANSCRICAO` permanece nulo, sem derrubar o lote.
5. `pool.imap_unordered` entrega os resultados na ordem de conclusão. A cada áudio finalizado
   (certo ou não):
   - `ao_progredir(feitos, total)` alimenta o badge — `_marcar_progresso` só grava no banco quando
     a **porcentagem inteira muda** (a fase grava `qt_progresso_fase` com commit próprio) e trava
     em 99 até a fase acabar de verdade;
   - `deve_cancelar` (lê `FL_CANCELAR` da extração) encerra o pool com `pool.terminate()` e
     interrompe — só os resultados já concluídos são aproveitados.
6. De volta em `investigacao_utils.py`, um `UPDATE ... SET tx_transcricao = %s` por anexo
   (mapeado pelo prefixo da chave), um `conn.commit()` único e o retorno
   `len(resultado)` → vira `QT_AUDIOS_TRANSCRITOS` no UPDATE final da extração.

### Consumo do texto transcrito

- **Busca de mensagens da IA** (`ia_investigacao.py`): `_select_mensagens_fonte` agrega as
  transcrições com `string_agg(a.tx_transcricao, ' | ')` na coluna `transcricao_audio`; o filtro de
  termos (`ILIKE ANY(%(patterns)s)`) casa em `tx_mensagem` **ou** em `tx_transcricao` (via
  `EXISTS`). O contexto do caso também informa `audios_transcritos` (total no inquérito), e a linha
  do tempo (`_SQL_EVENTOS`) usa `coalesce(m.tx_mensagem, <transcrições>)` para WhatsApp e
  mensageiros — voz só tem a transcrição.
- **Padrões de interesse** (`padroes_utils.py`): em `_FONTES_TEXTO`, as fontes `wpp_mensagem` e
  `sms_mensagem` são um UNION do texto das mensagens com as transcrições dos anexos (mesma
  `id_origem`, linhas separadas), varridas por léxico e regex.
- **Indexação semântica** (`embed_utils.py`): `_SQL_MSGS_INDEXAR` usa
  `coalesce(m.tx_mensagem, string_agg(a.tx_transcricao, ' | '))` para WhatsApp e mensageiros — o
  embedding de uma mensagem de voz é feito da transcrição.
- **Visualizador de conversa** (`investigacao_utils.py` + `web/js/whatsapp.js`): o payload de
  anexos da janela de mensagens traz `transcricao`; mensagens sem texto exibem
  "Transcrição: …" dentro da bolha, e o modal do player de áudio mostra a transcrição abaixo do
  player para acompanhar a escuta.
- **Relatórios DOCX** (`relatorios_utils.py`/`relatorios_docx.py`): transcrições entram nas
  tabelas de anexos do relatório e como linha itálica "Transcrição: …" nas conversas relatadas.

### Correção manual (revisão humana)

O faster-whisper erra, sobretudo em áudio ruim/gravado com ruído — a transcrição automática é um
ponto de partida, não a palavra final. No modal do player de áudio **ou de vídeo**
(`web/js/whatsapp.js`, `abrirModalMidia` + `montarTranscricaoMidia` — o mesmo bloco serve aos dois,
e o `PUT` do backend já aceitava qualquer anexo de mídia; só faltava o modal de vídeo receber a
transcrição e o id do anexo), o botão **"✎ corrigir"** troca o texto por um `textarea` pré-preenchido ali
mesmo, logo abaixo do player — a edição é inline, e não num diálogo por cima, para o áudio
continuar visível e tocando enquanto se reouve o trecho; ao salvar, `PUT /api/investigacao/anexos/{id_anexo}/transcricao?fonte=wpp|sms|msg`
(`api/rotas/investigacao.py`) grava o novo texto com `atualizar_transcricao_anexo`
(`api/funcoes/investigacao_utils.py`), sobrescrevendo `TX_TRANSCRICAO` — texto vazio grava `NULL`
(mesmo estado de "nunca transcrito"). Permissão: qualquer usuário com acesso ao inquérito (mesma
política de `salvar_contexto_investigacao`), verificada via `usuario_tem_acesso_inquerito` a partir
do `id_inquerito` resolvido pelo próprio anexo. A tela reflete a correção na hora, sem recarregar:
atualiza o texto no modal e (se existir) a linha "Transcrição: …" embaixo da bolha da mensagem.

Como a correção só troca a coluna, ela alimenta busca/padrões da IA na próxima consulta (leem
`TX_TRANSCRICAO` direto), mas **não** reprocessa embeddings já gerados — para uma correção grande
refletir na busca semântica é preciso rodar a indexação semântica de novo (botão "🧠 Indexar busca
semântica" do inquérito).

## Modelo de dados

- Anexos de conversa — `WPP_TB05_ANEXOS`, `SMS_TB05_ANEXOS` (desde a seção 12) e
  `MSG_TB05_ANEXOS` (criada na seção 34 já com o campo):
  - `TX_TRANSCRICAO` (TEXT) — texto transcrito (null enquanto não transcrito ou em caso de falha);
  - `CO_CAMINHO_ZIP` — caminho do binário dentro do `.ufdr` (originalmente `CO_CAMINHO_AUDIO`,
    renomeado na seção 19; vira TEXT na seção 33; índice `IX79/IX80/IX81` na seção 76). Null
    significa binário não localizado na ingestão (casamento por tamanho ambíguo).
- `INQ_TB02_EXTRACOES`:
  - `QT_AUDIOS_TRANSCRITOS` (BIGINT, seção 12) — total de áudios transcritos com sucesso;
  - `TX_MODELO_TRANSCRICAO` (VARCHAR(3000), seção 52) — modelo escolhido na extração (permite
    saber com qual modelo cada lote foi transcrito);
  - `IN_TRANSCREVER_VIDEO` (BOOLEAN, default `false`, seção 75) — flag de transcrever vídeos.

## Configuração

- `TRANSCRICAO_THREADS` — número de workers paralelos (padrão 4); é o teto de áudios transcritos
  simultaneamente e cada worker usa 2 threads de CPU (`cpu_threads`).
- `TRANSCRICAO_MODELO` — modelo padrão do faster-whisper (padrão `small`); pode ser sobrescrito
  por extração no formulário. Modelos maiores são mais precisos, porém mais lentos e com mais uso
  de memória; `large-v3` é o mais pesado.
- `TRANSCRICAO_VAD` — filtro de atividade de voz no `transcribe` (padrão **ligado**; `0`/`false`/
  `nao`/`off` desliga). Em CPU é o maior barateador: o Silero VAD (embutido no faster-whisper)
  pula os trechos de silêncio antes do modelo — e gravação policial é 30–70% silêncio.
- `TRANSCRICAO_BEAM` — tamanho do feixe de decodificação (padrão **1** = busca gulosa, ~2–3× mais
  rápida em CPU com perda mínima no `small`/português; `5` volta ao feixe largo do whisper padrão).
- Primeira execução baixa o modelo do **HuggingFace Hub** (o `small` tem ~500 MB): o servidor
  precisa de internet nesse primeiro uso e de espaço em disco para o cache. Depois, tudo é local.
- Idioma fixo em português (`language="pt"`); não há configuração para outros idiomas.
- Transcrever vídeos aumenta bastante o volume de trabalho (vídeos são bem maiores que áudios) —
  por isso é opcional e vem desligado por padrão.

## Referências

- `README.md` na raiz (visão geral); índice em `docs/README.md`.
- `api/funcoes/transcricao.py` — `transcrever_lote`, `_inicializar_worker`, `_transcrever_um`,
  `_configuracao`.
- `api/funcoes/investigacao_utils.py` — `_obter_config_transcricao`, `_transcrever_audios_pendentes`,
  fase `transcrevendo`, `_marcar_progresso`, `_cancelamento_solicitado`.
- `api/funcoes/ufdr_parser.py` — `_extrair_anexos` (casamento do binário por tamanho).
- Banco: `db/init/10_whatsapp.sql`, `11_sms.sql`, `12_mensageiros.sql`, `09_extracoes.sql`.
- Consumo: `api/funcoes/ia_investigacao.py` (`_select_mensagens_fonte`, `_SQL_EVENTOS`,
  contagem `audios_transcritos`), `api/funcoes/padroes_utils.py` (`_FONTES_TEXTO`),
  `api/funcoes/embed_utils.py` (`_SQL_MSGS_INDEXAR`), `api/funcoes/relatorios_utils.py` e
  `relatorios_docx.py`; front: `web/investigacao.html` (formulário), `web/js/investigacao.js`
  (`FASE_LABEL.transcrevendo`, envio do formulário), `web/js/whatsapp.js` (bolha e modal).
- Correção manual: `PUT /api/investigacao/anexos/{id_anexo}/transcricao` em
  `api/rotas/investigacao.py`, `atualizar_transcricao_anexo` em `api/funcoes/investigacao_utils.py`,
  botão "✎ corrigir" em `web/js/whatsapp.js` (`abrirModalMidia`,
  `sincronizarTranscricaoNaBolha`), estilos em `web/css/whatsapp.css`
  (`.wa-midia-transcricao-*`).
