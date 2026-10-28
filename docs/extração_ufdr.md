# Extração de arquivo UFDR (Cellebrite)

## Visão geral

A extração UFDR é a entrada principal de dados do sistema: o investigador informa o número do inquérito (ePol) e escolhe um arquivo `.ufdr` (formato de exportação do Cellebrite) que esteja na pasta de dados do servidor, e o sistema lê o aparelho inteiro — WhatsApp, SMS/MMS, mensageiros diversos, contatos, ligações, localizações, notificações, notas, navegação, aplicativos instalados, e-mails, contas financeiras, credenciais salvas, redes Wi-Fi, o dono do aparelho, o uso de cada aplicativo, o que foi buscado e publicado, calendário, trajetos, antenas de telefonia, caixa postal, transferências e a identidade do próprio aparelho — e grava tudo no banco em segundo plano.

O arquivo `.ufdr` é um ZIP com um `report.xml` no namespace `pa.cellebrite.com/report/2.0`, onde cada `<model>` de topo é um registro (chat, contato, ligação, localização, notificação, nota, senha etc.) e pode conter `<model>` aninhados. O XML pode ter vários gigabytes, então a leitura é feita em streaming (ET `iterparse`), com limpeza seletiva dos elementos já processados — e o registro já lido é **tirado da árvore**, não só esvaziado (ver `_PilhaConteineres`) — e um **único passe** pelo ZIP para tudo — inclusive os binários, que **nunca são copiados para o banco** e são lidos sob demanda do próprio `.ufdr` (via `ufdr_cache`).

O fluxo é orquestrado por `api/funcoes/investigacao_utils.py` (gravação no banco, fases, progresso, cancelamento) e `api/funcoes/ufdr_parser.py` (leitura e interpretação do XML, por domínio). A tela é `web/investigacao.html`, que expõe a aba de "Extrair" e o card de cada extração com fases, progresso e contagens.

Antes de qualquer leitura, o arquivo é **hasheado** (MD5/SHA1/SHA256) — é a referência de integridade da cadeia de custódia, que também serve para conferir se a cópia para `data/` está íntegra (ver [Hash de arquivos](hash_arquivos.md)).

> **Onde o arquivo fica depois.** Terminada a importação, o `.ufdr` é movido de
> `data/<DELEGACIA>/` para `data/<DELEGACIA>/<INQUÉRITO>/CELULAR/` e o caminho novo passa a ser
> o valor gravado no banco — ver [organizacao_arquivos.md](organizacao_arquivos.md).


## Fluxo de uso

1. Colocar o(s) arquivo(s) `.ufdr` na pasta de dados (`PASTA_INVESTIGACAO`, por padrão `/app/data`), opcionalmente em uma subpasta de delegacia (ex. `data/<delegacia>/<pedido>/`). O sistema lista os arquivos encontrados (só os da delegacia do usuário — ver [Permissões e multidelegacia](permissoes_delegacia.md)) e mostra *badges* de disponibilidade ("arquivo disponível" ou "arquivo ausente"). Além da cópia à mão e da cópia de pasta de rede (ver [importacao.md](importacao.md)), o `.ufdr` pode ser **enviado pelo navegador** na caixa "Enviar do seu computador" da própria tela de importação (`POST /api/investigacao/ufdr-upload`): grava em `data/<DELEGACIA>/` em blocos (sem limite de tamanho — ver "Rotas" abaixo) e já deixa o arquivo escolhido no seletor. Fechar a página cancela o envio; não há retomada.
2. Na tela Investigação, informar o número do inquérito (máx. 50 caracteres, regex `^[\w./-]{1,50}$`), escolher o `.ufdr` e, opcionalmente, o modelo de transcrição de áudio e a opção "transcrever vídeo".
3. Disparar a extração (`POST /api/investigacao/extrair`). Se o inquérito ainda não existir, ele é criado na hora (com o número informado) e a pessoa que extraiu ganha acesso a ele automaticamente (`_obter_ou_criar_inquerito`); se já existir e for de outro usuário, ela ganha permissão de acesso (auto-grant, ver `permissoes_delegacia.md`).
4. Acompanhar pelo card: badge "Processando" com a fase atual e a porcentagem (barra com polling), até virar "Concluído". O botão de cancelar (só gestor do inquérito) pede a interrupção cooperativa; o modal "Todas as extrações" mostra status e tempos de processamento (`DT_INICIO_PROCESSAMENTO`/`DT_FIM_PROCESSAMENTO`).
5. Com a extração concluída, o card mostra as contagens por domínio (conversas, mensagens, contatos, ligações, localizações, arquivos, etc.), cada uma com um botão que abre a tela daquele domínio já filtrada pela extração (`?extracao=ID`). As contagens ficam visíveis já na fase `contagens`, antes da transcrição terminar.
6. Se o arquivo estiver ausente (removido da pasta), dá para restaurá-lo informando um caminho no servidor (confinado a `data/`; usuário não-admin restrito à própria delegacia) ou enviando por upload — os binários (áudios, fotos, arquivos) continuam sendo lidos do ZIP sob demanda. Ao restaurar, os hashes são recalculados.
7. Apagar a extração (DELETE, só gestor) remove os dados dela e as anotações ligadas a esses registros (entidades, evidências de canônicas, detecções de padrões) — mas **não** apaga o inquérito: o card do inquérito permanece (com o botão explícito "Apagar inquérito" para removê-lo por completo). A tela confirma o que será apagado.

## Como funciona

### Disparo

`iniciar_extracao(nome_arquivo, numero_inquerito, id_usuario, modelo_transcricao, subpasta, transcrever_video)`
(em `investigacao_utils.py`) resolve a subpasta da delegacia, garante o inquérito
(`_obter_ou_criar_inquerito`), cria a linha em `INQ_TB02_EXTRACOES` com status
`pendente` (gravando já `TX_MODELO_TRANSCRICAO`/`IN_TRANSCREVER_VIDEO`) e **devolve**. Quem tira a
extração da fila é o despachante (`_despachante_extracoes`, uma thread daemon por worker subida no
startup da API), respeitando `EXTRACOES_SIMULTANEAS`; só então `_processar_extracao(id_extracao,
caminho)` roda. O front acompanha por polling das listagens/status, e `pendente` aparece na tela
como **"Na fila"**.

> **Por que uma fila, e não uma thread por extração como antes.** Não havia teto nenhum: cinco
> `.ufdr` de dezenas de GB disparados juntos disputavam disco, memória (cada um sobe
> `TRANSCRICAO_THREADS` processos de whisper) e conexões de banco. E a fila é **durável**: uma
> extração enfileirada sobrevive a um restart da API, porque a fila é a própria
> `INQ_TB02_EXTRACOES` em `pendente`. Com a thread por extração, um restart matava a thread e a
> linha só virava `erro` 60 minutos depois, pelo watchdog — o analista perdia o trabalho.
>
> Consequência para o watchdog: `liberar_extracoes_travadas` **não** toca mais em `pendente`
> (agora é "esperando a vez", que pode durar horas de forma perfeitamente saudável) — só em
> `processando`, que tem worker e deve bater.

> **Conexão de banco durante o pipeline.** A função segurava **uma conexão do pool do começo ao
> fim** — atravessando o hash do `.ufdr` (leitura completa de dezenas de GB), a transcrição no
> faster-whisper e a indexação semântica, que juntas passam de horas. Poucas extrações simultâneas
> prendiam o pool inteiro e a aplicação respondia 500 para todo mundo. Hoje só o laço do
> `report.xml` (`_laco_report_xml`, que está gravando o tempo todo) usa conexão longa, com
> `get_connection(longa=True)`; fase, progresso e cancelamento passaram para a classe `_Batimento`,
> que abre uma conexão curta por operação. As fases `hasheando`, `transcrevendo` e
> `indexando_semantica` rodam com **zero** conexões retidas.

### `_processar_extracao` — o pipeline

1. **Status e fases**: marca `processando`, fase `extraindo`, progresso 0 e `DT_INICIO_PROCESSAMENTO`.
2. **Hashes**: calcula MD5, SHA1 e SHA256 do arquivo **antes de abrir o ZIP** (`calcular_hashes_arquivo`, em `hash_utils.py`) — é a cópia do arquivo "tal como chegou", a referência de integridade para conferência posterior (cadeia de custódia). Os hashes ficam em `CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256` e são exibidos no front.

   Logo depois, `_recusar_se_duplicada` interrompe a extração se **este mesmo arquivo já estiver no inquérito**: outra extração `concluido`/`processando` com o mesmo SHA-256. Nada impedia importar o mesmo aparelho duas vezes — o nome do arquivo pode mudar, e com vários analistas no mesmo inquérito cada um tem a sua cópia na pasta da delegacia —, e o resultado era o aparelho inteiro em dobro: contagens dobradas, a mesma conversa duas vezes na busca, a mesma pessoa identificada duas vezes na mesa, sem nada na tela denunciando. `erro`/`cancelado` **não** contam: reimportar depois de uma falha é justamente o caminho de recuperação. A mensagem em `TX_ERRO` diz qual extração é a original e que ela precisa ser apagada antes.
3. **Dispositivo**: `ler_info_dispositivo` (em `ufdr_parser.py`) lê o `report.xml` com `iterparse` **até o primeiro `<model>`** e extrai fabricante (`DeviceInfoSelectedManufacturer`), modelo (`DeviceInfoSelectedDeviceName`), data da extração (`DeviceInfoExtractionStartDateTime`) e telefone (`LastUsedMSISDN` — normalizado para só dígitos) — gravados em `NO_DISPOSITIVO`, `NO_FABRICANTE` e `DT_EXTRACAO`. O telefone vira o JID do dono para o WhatsApp (`<fone>@s.whatsapp.net`) e o identificador do dono para SMS.
4. **Índices de binários**: `indexar_indicadores` faz **um único passe pelo `namelist`** do ZIP e constrói os três índices:
   - `indice_binarios`: tamanho em bytes → lista de caminhos (áudios, imagens, vídeos, documentos e compactados, pela extensão — `_EXTENSOES_ANEXO`). Permite casar cada anexo da conversa com os bytes exatos dentro do ZIP por tamanho (ver `_casar_por_tamanho`, abaixo);
   - `contagem_tamanhos`: tamanho em bytes → quantos arquivos do ZIP têm **exatamente** esse tamanho, contando **todos** eles e não só os de extensão conhecida. É o que torna honesta a pergunta "esse tamanho é inequívoco?": o índice de candidatos enxerga só `_EXTENSOES_ANEXO`, então um arquivo sem extensão conhecida de mesmo tamanho não contava como colisão e o anexo recebia o binário errado **com toda a aparência de acerto**. Era o mesmo defeito que o `.thumb` já tinha revelado — lá resolvido caso a caso, aqui de vez;
   - `mapa_fotos`: telefone → caminho da foto de perfil do WhatsApp, extraído dos nomes de `files/Image` cujo nome do arquivo casa `_RE_FOTO_PERFIL = ^(\d{10,15})(?:@|-\d)` (telefone de 10 a 15 dígitos seguido de `@` ou de `-` + dígito — o padrão de nome que o WhatsApp usa para a foto salva localmente), escolhendo a melhor por prioridade de extensão quando há mais de um arquivo pro mesmo telefone (imagem cheia antes da miniatura: `.jpg`/`.jpeg` > `.png` > `.thumb`, extensão desconhecida fica por último — `_PRIORIDADE_FOTO`).
   Tudo isso reutilizando o mesmo `ZipFile` aberto uma única vez para toda a extração (o arquivo pode ter GBs — reabri-lo 3 vezes custa caro).
5. **Passe principal**: `iterar_registros` (em `ufdr_parser.py`) percorre o `report.xml` em streaming e produz `(tipo, dados)` para cada registro de topo, com a **limpeza seletiva de memória**:
   - a profundidade de aninhamento de `<model>` é contada; só registros na raiz (profundidade 1) contam — um mesmo `type` aninhado (ex. `User` dentro de `InstalledApplication`, `Contact` dentro de outro `Contact`) não vira registro próprio;
   - os elementos aninhados esperados (`Party`/`InstantMessage`/`Attachment` nos chats etc.) nunca são limpos individualmente — só junto com o `<model>` pai que os contém, senão a leitura do pai voltaria vazia; cada registro de topo é limpo (`elem.clear()`) assim que termina;
   - os `<file>` (arquivos do aparelho) são interceptados pela tag no mesmo passe (ficam fora dos `<model>`);
   - terminado o registro de topo, além do `elem.clear()` o **pai dele é limpo** (`_PilhaConteineres.descartar_processados`), tirando da árvore o que já foi lido. `clear()` esvazia o elemento mas não o remove da lista de filhos do pai, e o `iterparse` do ElementTree monta a árvore inteira enquanto lê: num iPhone com 1,7 milhão de `<file>` sobrava uma casca de `Element` por registro, pendurada até o fim do passe, e cada extração simultânea pagava a sua. Como o ElementTree não dá o pai de um elemento (não existe `getparent()`, como no lxml), a pilha dos contêineres abertos (`<project>`, `<decodedData>`, `<taggedFiles>`…) é mantida na mão pelos eventos `start`/`end` — só os de fora dos registros, meia dúzia. Medido em `tools/teste_parser_ufdr.py`: **89% menos** memória retida;
   - tipo de `<model>` que o parser não sabe interpretar é contado por tipo em `desconhecidos` e vira **um resumo no log ao fim do passe** (`_registrar_tipos_desconhecidos`), em `INFO` para os ignorados de propósito e em `WARNING` para o resto. Antes saía um `debug` por registro, ou seja: em produção, nada. O Cellebrite cria tipos novos a cada versão do Physical Analyzer, e tipo novo é dado do aparelho sendo descartado em silêncio;
   - o progresso em bytes do XML (`{'lidos','total'}`) é mantido no dict passado por quem chama: `total` vem do índice do ZIP (`report.xml.file_size`) e `lidos` de `f.tell()` medido a cada `_PASSO_PROGRESSO = 2000` eventos (chamar `tell()` a cada elemento num XML de milhões de elementos pesaria);
   - o despacho por tipo cobre `Chat`, `InstantMessage` de topo, `Contact`, `Call`, `Location`, `Notification`, `Note`, `VisitedPage`, `InstalledApplication`, `User`, `UserAccount`, tipos de Wi-Fi (`WifiDetails` e também `NetworkActivity`/`WifiNetworkActivity`/`ConnectionEvent`/`WifiConnectionEvent`), e-mails (`Email`/`EMail`/`EmailMessage`), contas financeiras (`FinancialAccount`/`BankAccount`/`Account`), `Password`, `Autofill`, `FormData` e os domínios da tabela abaixo (`AppsUsageLog`, `DeviceEvent`, `ActivitySensorData`, `RecognizedDevice`, `DeviceConnectivity`, `Cookie`, `SocialMediaActivity`, `SearchedItem`, `FileUpload`, `CalendarEntry`, `ApplicationUsage`, `Journey`, `DeviceInfoEntry`, `CellTower`, `Voicemail`, `WebBookmark`, `FileDownload`, `TransferOfFunds`, `CreditCard`, `MobileCard`, `Recording`, `NetworkUsage`) — tipos desconhecidos são ignorados com log `debug`;
   - **não há filtro por tipo aninhado**: a contagem de profundidade já garante que só o registro de topo é despachado e limpo. O filtro por tipo que existia antes descartava também os registros de topo cujo tipo ocorre aninhado — e `InstantMessage` é um deles, então mensagem sem `Chat` pai sumia em silêncio;
   - **contas financeiras derivadas**: aplicativos instalados de bancos, credenciais salvas e contas de app viram evidências de contas financeiras que só são **consolidadas ao final do passe** (as três evidências da MESMA conta chegam em pontos distintos do XML; `_consolidar_contas_derivadas` as junta e emite os registros `conta_financeira` de quebra).
6. **Gravação em lote** (em `_processar_extracao`): cada registro é gravado na hora com a função do domínio (`_gravar_conversa`, `_gravar_contato_agenda`, `_gravar_ligacao`, ...; os 22 domínios da auditoria de aproveitamento compartilham `_gravar_dominio_novo`, guiado pelo mapa `_DOMINIOS_NOVOS`); os `<file>` acumulam num buffer de **20 mil itens**, drenado via `COPY ... FROM STDIN` (`_drenar_arquivos` — essencial porque um iPhone tem centenas de milhares de arquivos, e INSERT por linha seria lento).

   **Mensagens e anexos também vão em lote** (`_gravar_mensagens_em_lote`, lotes de 500): era um `INSERT ... RETURNING` por mensagem e outro por anexo, cada um com a sua ida e volta ao Postgres — num aparelho de 300 mil mensagens, 300 mil round-trips só de mensagem, e o passe do `report.xml` passava a maior parte do tempo esperando a rede em vez de ler XML. O que segurava a mudança é que o anexo precisa do ID da mensagem de volta; `executemany(..., returning=True)` do psycopg 3 resolve isso enfileirando o lote em pipeline e devolvendo **um conjunto de resultados por entrada, na ordem das entradas** — o id sai casado com a sua mensagem *por construção*. Um `INSERT` multivalorado com `RETURNING` seria mais simples e estava errado: ali a ordem das linhas devolvidas é hábito do Postgres, não promessa, e um dia trocaria o anexo de dono em silêncio — na tela, a foto de uma pessoa dentro da conversa de outra. Medido em `tools/teste_ingestao_ufdr.py` (correção) e no banco local (desempenho): **4,4× mais rápido** mesmo com o banco no mesmo host; com o Postgres em outra máquina a diferença cresce, porque o gargalo é justamente o round-trip. A cada **200 registros processados** há `commit` + atualização do progresso (`_marcar_progresso`) + checagem de cancelamento (`_cancelamento_solicitado`). Contadores por domínio ficam em memória.
   Ao fim do passe, ainda dentro do mesmo cursor: as mensagens sem `Chat` pai são gravadas em conversas sintéticas por aplicativo, e `_resolver_anexos_por_file_id` liga por UPDATE os anexos que ficaram sem binário ao `<file>` correspondente.
7. **Contagens**: já com os dados carregados, a fase vira `contagens` e o card mostra os números de todos os domínios (fase `contagens` em `investigacao_utils.py`) — o status segue `processando` até a transcrição terminar, mas o material já fica navegável. As contagens são gravadas **mesmo se o cancelamento foi pedido durante o passe principal** — o que foi lido até a parada fica preservado e contabilizado.
8. **Etapas adicionais** (só rodam se **não** houve cancelamento; todas *best-effort*, nenhuma derruba a extração em caso de falha):
   - **Chaves PIX** (`pix_utils.detectar_chaves_pix`): varre as mensagens citando "pix"/"chave" e liga cada chave ao contato; deixa `QT_CHAVES_PIX` como "já escaneado" mesmo em falha (ver [Chaves PIX](investigacao_celular.md#chaves-pix));
   - **Contas de apps iOS** (`_extrair_contas_de_apps_ios`): extrai dados de SQLite de `iCloud backup` encontrados nos arquivos do aparelho e soma em `QT_CONTAS_FINANCEIRAS`;
   - **Identificação de pessoas** (fase `identificando_pessoas`): cruza contatos, agenda, ligações, chaves PIX, contas bancárias e contas de aplicativo, no **inquérito inteiro** (o dono deste aparelho pode ser contato salvo no outro celular do caso);
   - **Transcrição de áudio** (fase `transcrevendo`), ver [Transcrição de áudio](transcricao_audio.md);
   - **Indexação semântica** (fase `indexando_semantica`), ver [Busca semântica](busca_semantica.md).
9. **Finalização**: status `concluido` (ou `cancelado`, se `FL_CANCELAR` foi pedido), fase e progresso zerados, `DT_FIM_PROCESSAMENTO` gravado e `QT_AUDIOS_TRANSCRITOS` salvo. Em erro, `CD_STATUS = 'erro'` e `TX_ERRO` (truncado em 2000 caracteres) guarda a mensagem para exibição na tela — um `.ufdr` truncado (cópia para `data/` interrompida) vira o aviso amigável de arquivo incompleto/corrompido ("o fim do arquivo ZIP não foi encontrado — confira se o tamanho em `data/` é igual ao do original") em vez de um traceback cru. **A falha também vai para o log** (`logger.error`/`logger.exception` em `_processar_extracao`, e `logger.error` no despachante quando o arquivo some da fila): o motivo fica em `logs/erros.log`/stdout mesmo que a linha da extração seja apagada da tela — a duplicidade (`ExtracaoDuplicada`) continua sendo só `warning`, é recusa deliberada, não falha.

### Cancelamento cooperativo

`POST /api/investigacao/extracoes/{id}/cancelar` (só gestor) chama `solicitar_cancelamento_extracao` (`investigacao_utils.py`): grava `FL_CANCELAR = true`, fase `cancelando`, zera o progresso e **sinaliza também a indexação semântica** (`solicitar_cancelamento_indexacao`). O worker enxerga o pedido nos checkpoints — a cada 200 registros no passe principal, a cada áudio na transcrição (e por um vigia a cada 30 s, para um worker preso não segurar a parada) e dentro da indexação — e encerra preservando o que já foi gravado. O badge mostra "Parando extração…" (exceção à regra de não exibir percentual na fase `cancelando`).

### Extração presa (watchdog)

Se o worker morrer (restart da API no meio do job) ou travar (ex.: pool de transcrição emperrado num áudio que o faster-whisper não termina), a extração ficaria para sempre em `processando`/`cancelando` — o badge preso em "Processando…"/"Parando extração…" e o DELETE recusando. O watchdog `liberar_extracoes_travadas` resolve: a linha em **`processando`** sem `DT_ULTIMA_ATIVIDADE` há `_MINUTOS_EXTRACAO_STALE` (60 min) vira `cancelado` (se havia `FL_CANCELAR`, preservando o que foi gravado) ou `erro` com mensagem legível.

Quem bate `DT_ULTIMA_ATIVIDADE` é uma **thread de batimento** por extração (`_Batimento.iniciar_vigia`, a cada 2 min), encerrada no `finally` de `_processar_extracao`. Antes o batimento era efeito colateral de fase/progresso/cancelamento — ou seja, só existia onde alguém tinha lembrado de medir alguma coisa, e as etapas do fim da extração não medem nada: PIX, contas de apps iOS, correção de direção e identificação de pessoas rodam inteiras entre duas trocas de fase, e a identificação cruza o **inquérito todo**. Passando dos 60 minutos, o watchdog dava a extração *viva* por travada: marcava `erro`, liberava a vaga da fila (outra extração entrava, furando `EXTRACOES_SIMULTANEAS`) e, no fim, o worker que nunca morreu gravava `concluido` por cima — com a mensagem de erro antiga ainda em `TX_ERRO`, daí `_finalizar_extracao` zerá-la. Com a thread, `DT_ULTIMA_ATIVIDADE` quer dizer o que o watchdog sempre supôs: **o processo está de pé**. Ela é daemon e morre junto com o processo, então o predicado temporal continua valendo para worker morto — que é o caso que o watchdog existe para resolver. O predicado é **puramente temporal**, e precisa continuar sendo: com `API_WORKERS > 1` não há como saber daqui quais linhas são de um processo morto e quais são de um processo vivo ao lado. O sweep roda no startup da API, na listagem de extrações (o polling do badge) e antes do apagar — a tela se recupera sozinha na próxima batida.

### Apagar extração / apagar inquérito

- `DELETE /api/investigacao/extracoes/{id}` (só gestor) chama `apagar_extracao`: recusa com `409` se a extração ainda estiver `processando` (na fila, em `pendente`, pode apagar: nenhum worker a tocou); então `_apagar_anotacoes_de_extracao` remove as anotações que apontam (por `tp_origem`/`id_origem`, sem FK) para registros desta extração — entidades/relações, evidências de canônicas e detecções de padrões; as **canônicas em si só somem** se forem automáticas e ficarem sem nenhuma evidência (nó que o investigador tocou na mesa é preservado); depois `_apagar_dados_extracao` apaga as tabelas de domínio da extração; por fim a linha da extração.
- O inquérito é apagado só por ação explícita (`DELETE /api/investigacao/inqueritos/{id}`, `apagar_inquerito`), que varre **todas** as tabelas filhas (entidades, canônicas, busca automática, padrões, conversas/processamentos/arquivos de prompt da IA, permissões, RIFs, IPLs, SIMBA, documentos, marcações, auditoria de IA, busca semântica) — e também recusa se houver extração ou importação Google em andamento.

### Rotas

| Rota | Acesso | Ação |
|---|---|---|
| `GET /api/investigacao/arquivos` | sessão + `delegacia_permitida` | lista `.ufdr` da subpasta da delegacia para o seletor |
| `POST /api/investigacao/ufdr-upload` | sessão + `delegacia_permitida` | recebe o `.ufdr` pelo navegador e grava em `data/<DELEGACIA>/` — sem limite de tamanho (teto no `location` próprio do nginx), gravação em blocos em `.parcial` + rename (`salvar_ufdr_enviado`); `.parcial` órfão é varrido no startup da API |
| `GET /api/investigacao/ufdr-upload/progresso` | sessão + `delegacia_permitida` | tamanho em disco do `.parcial` de um upload em andamento (identificado pelo token sorteado no front) — é o progresso da gravação no SERVIDOR, que continua depois de o navegador terminar de enviar os bytes |
| `POST /api/investigacao/extrair` | sessão (`ExtracaoIn`: `arquivo`, `numero_inquerito`, `modelo_transcricao`, `transcrever_video`, `delegacia`) | dispara a extração em background |
| `GET /api/investigacao/extracoes` | sessão + `_filtro_acesso_sql` | lista extrações visíveis com status (modal "Todas as extrações") |
| `DELETE /api/investigacao/extracoes/{id}` | gestor do inquérito | apaga a extração e os dados dela |
| `POST /api/investigacao/extracoes/{id}/cancelar` | gestor do inquérito | pede interrupção cooperativa (`409` se não estiver processando) |
| `POST /api/investigacao/extracoes/{id}/restaurar-arquivo` (+ `/upload`) | gestor do inquérito | recoloca o `.ufdr` na pasta de dados e recalcula os hashes |

### Domínios extraídos

| Domínio | Tabela(s) principal(is) | Origem no XML |
| --- | --- | --- |
| WhatsApp | `WPP_TB02_CONVERSAS`, `WPP_TB03_PARTICIPANTES`, `WPP_TB04_MENSAGENS`, `WPP_TB05_ANEXOS` | `<model>` tipo Chat com `Source` WhatsApp ("WhatsApp"/"WhatsApp Business") |
| SMS/MMS | `SMS_TB01_CONTATOS`, `SMS_TB02_CONVERSAS`, `SMS_TB03_PARTICIPANTES`, `SMS_TB04_MENSAGENS`, `SMS_TB05_ANEXOS` | Chat com `Source` SMS |
| Mensageiros diversos | `MSG_TB01_CONTATOS`, `MSG_TB02_CONVERSAS`, `MSG_TB03_PARTICIPANTES`, `MSG_TB04_MENSAGENS`, `MSG_TB05_ANEXOS` (coluna `TP_APP`) | Chats de qualquer outro aplicativo |
| Contatos | `CTT_TB01_CONTATOS`, `CTT_TB02_CONTATO_ENTRADAS` | `<model>` Contact |
| Ligações | `LIG_TB01_LIGACOES` | `<model>` Call |
| Localizações | `LOC_TB01_LOCALIZACOES` | `<model>` Location |
| Notificações | `NTF_TB01_NOTIFICACOES` | `<model>` Notification |
| Notas | `NOT_TB01_NOTAS` (nomes dos anexos na coluna `TX_ANEXOS`; sem binário) | `<model>` Note |
| Navegação | `NAV_TB01_PAGINAS_VISITADAS` | `<model>` VisitedPage |
| Aplicativos instalados | `APP_TB01_APLICATIVOS` | `<model>` InstalledApplication |
| E-mails | `EML_TB01_EMAILS` | `<model>` Email/EMail/EmailMessage |
| Contas financeiras | `BNC_TB01_CONTAS` (inclui instituição identificada, ver abaixo) | `FinancialAccount`/`BankAccount`/`Account` + derivadas de app instalado/credencial/conta de app |
| Dono do aparelho | `DON_TB01_DONOS` | `<model>` User (ou derivado no iOS, ver abaixo) |
| Contas de usuário | `CTA_TB01_CONTAS_USUARIO` | `<model>` UserAccount |
| Arquivos | `ARQ_TB01_ARQUIVOS` | `<file>` do ZIP (via COPY em lote) |
| Redes Wi-Fi | `WIFI_TB01_REDES` | `<model>` WifiDetails e afins |
| Senhas e autofill | `SNH_TB01_SENHAS`, `AFL_TB01_AUTOFILL` | `<model>` Password / Autofill / FormData |
| Uso de aplicativo | `USO_TB01_USO_APPS`, `APU_TB01_USO_APLICATIVO` | `AppsUsageLog` (evento a evento) e `ApplicationUsage` (agregado) |
| Dispositivos por perto | `RCD_TB01_DISPOSITIVOS` | `RecognizedDevice` |
| Conexões | `CNX_TB01_CONEXOES` | `DeviceConnectivity` |
| Cookies, favoritos, downloads, uso de rede | `COO_TB01_COOKIES`, `FAV_TB01_FAVORITOS`, `DWN_TB01_DOWNLOADS`, `RDE_TB01_USO_REDE` | `Cookie` / `WebBookmark` / `FileDownload` / `NetworkUsage` |
| Redes sociais e buscas | `RDS_TB01_ATIVIDADE_SOCIAL`, `BSC_TB01_BUSCAS` | `SocialMediaActivity` / `SearchedItem` |
| Envios para a nuvem e gravações | `UPL_TB01_ENVIOS_NUVEM`, `GRV_TB01_GRAVACOES` | `FileUpload` (só iPhone) / `Recording` |
| Calendário | `CAL_TB01_CALENDARIO` | `CalendarEntry` |
| Trajetos | `JRN_TB01_TRAJETOS` | `Journey` (origem/destino são `Location` completos) |
| Identidade do aparelho | `DEV_TB01_INFO_APARELHO` | `DeviceInfoEntry` (IMEI, IMSI, ICCID, MSISDN, série, MAC, Apple ID…) |
| Antenas de telefonia | `ERB_TB01_ANTENAS` | `CellTower` (só iPhone) |
| Caixa postal | `VMS_TB01_VOICEMAIL` | `Voicemail` (só iPhone; traz transcrição pronta) |
| Transferências e cartões | `TRF_TB01_TRANSFERENCIAS`, `CRT_TB01_CARTOES_CREDITO`, `MBC_TB01_CARTOES_CARTEIRA` | `TransferOfFunds` / `CreditCard` / `MobileCard` |

Ficam **deliberadamente de fora**, por decisão de escopo: `LogEntry` (log do sistema iOS),
`DictionaryWord` (dicionário do teclado), `RemoteServiceSession`, `DeviceEvent` (tela ligada,
bloqueio, digitação) e `ActivitySensorData` (passos e velocidade, só iPhone) — muito volume para
o valor probatório. Aparecem no log em nível `debug` como tipo desconhecido, junto com os demais.

### Onde cada domínio aparece na tela

As telas que já existiam ganharam os domínios novos **como abas**, não como uma segunda região
embaixo: Wi-Fi (redes · conexões), Ligações (ligações · caixa postal), Contas bancárias (contas ·
transferências · cartões de crédito · carteira), Navegação (cookies · favoritos · downloads ·
preenchimento automático · uso de rede) e Arquivos (envios para a nuvem · gravações). Em
Localizações, os trajetos entraram como terceira visão ao lado de Tabela e Grafo, que é o
alternador que a tela já usava. Telas próprias: Redes sociais e buscas, Calendário, Identidade do
aparelho (identificação · dono · contas no aparelho · antenas) e Dispositivos reconhecidos.

O uso de aplicativo não tem tela própria: na lista de Aplicativos, cada app tem o botão **Ver uso**,
que abre um modal com os números gerais daquele app (tempo em primeiro e segundo plano, aberturas,
última abertura) e o log de eventos dele, paginado e filtrado no servidor por `?pacote=`. São 413
mil eventos nos oito aparelhos auditados — em lista única não se acha nada.

O componente que monta essas abas é um só (`web/js/utils/ufdr_dominio.js`): a página declara
`data-dominios="chave1,chave2"` num elemento, ou chama `UfdrDominio.montar` passando também a
região que ela mesma desenha (`externo`), que vira a primeira aba. Aba de domínio que este
aparelho não tem não é exibida — antena de telefonia só existe em iPhone, dicionário só em
Android, e aba vazia faz procurar dado que não existe.

**Filtros por domínio.** Todos têm busca livre e "só apagados/recuperados". Além disso, um
domínio pode declarar (em `DOMINIOS`, no back-end, e em `COLUNAS[chave].filtros`, no front):
período (`data=`), selects de categoria (`facetas=`) e faixa numérica (`valor=`). Hoje declaram
os três domínios financeiros — transferências (enviada em, tipo, situação, moeda, aplicativo,
faixa de valor), cartões de crédito (último uso, bandeira, fonte) e carteira (comprado em, tipo,
emissor, fonte). Os valores dos selects vêm de `.../ufdr/{dominio}/facetas`, com a contagem de
cada um: são os que existem naquele aparelho, e não uma lista fixa — o que o Cellebrite grava em
`TransferType`/`Status` muda de aplicativo para aplicativo. A mesma rota devolve o intervalo de
datas e de valores existentes, que limita os campos de data e alimenta os atalhos "7 dias / 30
dias / 12 meses" — contados a partir do **registro mais recente**, não de hoje: a extração
costuma ser de meses atrás, e contar de hoje devolveria lista vazia justo em quem tem dado
antigo. Filtro que só poderia devolver vazio não é montado (coluna sem nenhum valor, período sem
nenhuma data). O nome da coluna chega pela query string (`?f_no_moeda=BRL`), então a lista
declarada no back-end é a lista branca que a valida.

Particularidades do parser:

- **Chat de Status (Stories)** do WhatsApp tem `Id` no formato `"Status: <jid>"` e é renderizado com o badge "Status".
- **Dono no iOS**: o iPhone não traz um `<model>` de usuário; `_derivar_dono_ios` (chamado ao final do passe, `investigacao_utils.py`) sintetiza o dono a partir das contas `UserAccount` + `IsPhoneOwner`, usando também os telefones coletados dos chats.
- **Contas financeiras**: `_identificar_instituicao` (em `ufdr_parser.py`) recebe vários
  textos candidatos (pacote do app, domínio de login, nome de exibição) e faz duas passadas:
  primeiro o catálogo de instituições conhecidas (bancos e casas de apostas cadastradas, ex.
  `br.betano.com` → "Betano"), depois — só se nada casou — a heurística de aposta
  `_RE_APOSTA = (?<![a-z])(bet|bets|aposta|apostas|cassino|casino|loteria|lottery|bingo)(?![a-z])`
  (case-insensitive; o *lookbehind*/*lookahead* de letra evita falso positivo em
  "alphabet"/"betterhelp", mas permite dígito colado, de propósito, porque metade dos domínios
  de aposta observados é numérica, ex. `79x16.com`, `8839i.com`) — o mercado tem centenas de
  marcas efêmeras que listar uma a uma no catálogo seria inviável. As três evidências do UFDR
  para a mesma conta (aplicativo instalado, credencial salva, conta de aplicativo — três
  `<model>` distintos) são acumuladas por `_acumular_conta_derivada` numa chave
  `(instituição, titular)` e fechadas por `_consolidar_contas_derivadas` ao final do passe:
  quando uma instituição tem **exatamente uma** linha com titular identificado, as linhas sem
  titular dela (o app instalado nunca traz usuário, por exemplo) são absorvidas nessa — assim
  o aplicativo do PicPay e a senha do PicPay do mesmo aparelho viram uma conta só, não duas.
- **Binários**: não são copiados para o banco — ficam no ZIP e são lidos sob demanda pelo `ufdr_cache` (ex. anexos de mensagem, fotos de perfil, arquivos do aparelho). Arquivos do aparelho guardam o "Local Path" do `<file>` em `CO_CAMINHO_ZIP`. Cada anexo de mensagem é resolvido por **três vias somadas, nessa ordem**: o `attachment_extracted_path` que o Cellebrite gravou; o casamento por tamanho (`_casar_por_tamanho`); e o atributo `file_id`, que aponta para o `<file id=…>` e é fechado por UPDATE ao final do passe (`_resolver_anexos_por_file_id`) porque `<file>` e `<model>` vêm intercalados no XML. As duas primeiras sozinhas localizavam 69% dos anexos com binário nos oito extratos auditados; com a terceira sobem para 94%. São complementares — em alguns iPhones a segunda acerta mais que a terceira —, por isso somam em vez de substituir.

  O casamento por tamanho tem duas regras, nessa ordem. **Nome do arquivo**: se um único candidato do mesmo tamanho tem o mesmo nome que o anexo declara em `Filename`, é ele — nome igual *e* tamanho igual não é coincidência, e é exatamente a evidência que `_resolver_anexos_por_file_id` já usava para **sobrescrever** um palpite errado depois do passe; usá-la antes de palpitar só antecipa a conclusão, e recupera o anexo que ficava "indisponível" só porque outro arquivo do aparelho tinha o mesmo tamanho. **Candidato único e tamanho inequívoco**: sem nome para desempatar, só aceita quando há um único candidato *e* nenhum outro arquivo do ZIP tem aquele tamanho (`contagem_tamanhos`). A segunda condição é a correção de fundo — antes o índice só enxergava as extensões conhecidas, então um `.bin` de mesmo tamanho não aparecia como colisão e o anexo recebia o binário errado. Em prova pericial, "não localizado" é um resultado; binário trocado é um erro.
- **Fotos de contato**: duas fontes complementares. O índice por nome de arquivo em `files/Image` (`indexar_fotos_perfil`) resolve a foto de perfil de WhatsApp; o campo `Name` do `<model type="ContactPhoto">`, que no iPhone é o caminho do arquivo dentro do ZIP, cobre a agenda inteira e vai para `CTT_TB01_CONTATOS.CO_CAMINHO_FOTO`. As `Url` do ContactPhoto são endereços remotos de CDN e não servem para achar binário.
- **Registros apagados**: o atributo `deleted_state` de cada `<model>` (`Intact`/`Deleted`/`Trash`/`Unknown`) é gravado em `CD_STATUS_UFDR` em **todos** os domínios. No iPhone é o único sinal de exclusão que existe — lá o Cellebrite não emite os rótulos de mensagem (`MessageLabel`) que o Android traz.
- **Rótulos de mensagem** (só Android): `MessageExtraData → MessageLabel` vira `IN_ENCAMINHADA`, `IN_APAGADA_REMETENTE` (o "apagada para todos"), `IN_TEMPORARIA` e `IN_EDITADA`; `QuotedMessageData` guarda em `NU_IDENT_CITADO` a mensagem que está sendo respondida.
- **Hashes de arquivo**: MD5 e SHA256 vêm prontos do próprio `<file>` e alimentam `CO_HASH_MD5`/`CO_HASH_SHA256` (SHA1 o UFDR não traz). É o que permite confronto com base de hashes conhecidos e convergência entre aparelhos do mesmo caso.
- **Transcrição já pronta**: quando o anexo traz `Transcript`, ele nasce em `TX_TRANSCRICAO` e a fase de transcrição local pula esse áudio — o faster-whisper é a etapa mais lenta da extração.
- **Mensagem sem conversa**: `InstantMessage` de topo (sem `Chat` pai) existe, sobretudo em iPhone, e entra numa conversa sintética por aplicativo chamada `(mensagens sem conversa)`.
- **Texto livre em coluna curta**: campo que o Cellebrite trata como texto livre mas cabe numa `VARCHAR(3000)` passa por `_curto()` no parser. O que é livre por natureza — corpo, assunto, URL, grupo de acesso do chaveiro — tem coluna `TEXT`. O caso que motivou isso apareceu em produção: um `Password/AccessGroup` de 3.255 caracteres abortava a extração inteira no INSERT.
- **Trajetos sem coordenada** ficam fora da listagem: um `Journey` sem origem nem destino não diz de onde para onde, e só ocupava a tela.
- **Timeline**: eventos idênticos no mesmo instante (mesma categoria, mesmo texto, mesmo aparelho) chegam agrupados com a contagem, em vez de virarem uma sequência de linhas iguais.
- **E-mails**: `_extrair_email` mapeia os campos com alias (`Source/SourceApplication`, `From/Sender/FromAddress`, `To/Recipients`, `Cc`, `Bcc`, `Subject/Title`, `Body/Text/Content/Snippet`).
- **Contatos de conversa**: `_upsert_contato` grava pela chave natural (JID/telefone) com `ON CONFLICT ... DO UPDATE` — contatos repetidos no UFDR são consolidados, não duplicados; o mesmo vale para participantes e para o caminho da foto de perfil (só o ponteiro, nunca o binário).

## Modelo de dados

- `INQ_TB01_INQUERITOS`: `ID_INQUERITO` (identidade), `NU_INQUERITO` (número único, o do ePol).
- `INQ_TB02_EXTRACOES`: uma linha por arquivo extraído. Colunas principais: `ID_EXTRACAO`, `ID_INQUERITO`, `NO_ARQUIVO`, `NO_DISPOSITIVO`, `NO_FABRICANTE`, `DT_EXTRACAO`, `DT_UPLOAD`, `CD_STATUS` (`pendente`/`processando`/`concluido`/`erro`/`cancelado`), `TX_ERRO`, hashes (`CO_HASH_MD5`/`CO_HASH_SHA1`/`CO_HASH_SHA256`), `DT_INICIO_PROCESSAMENTO`/`DT_FIM_PROCESSAMENTO`, `CD_FASE` (`extraindo`/`contagens`/`identificando_pessoas`/`transcrevendo`/`indexando_semantica`/`cancelando`), `QT_PROGRESSO_FASE` (0–99; 100 só quando a extração conclui), `FL_CANCELAR`, `TX_MODELO_TRANSCRICAO`, `IN_TRANSCREVER_VIDEO`, `QT_AUDIOS_TRANSCRITOS`, `QT_CHAVES_PIX` e os contadores por domínio (`QT_CONVERSAS`, `QT_MENSAGENS`, `QT_SMS_CONVERSAS`, `QT_SMS_MENSAGENS`, `QT_MSG_CONVERSAS`, `QT_MSG_MENSAGENS`, `QT_CONTATOS`, `QT_LIGACOES`, `QT_LOCALIZACOES`, `QT_NOTIFICACOES`, `QT_NOTAS`, `QT_NAVEGACAO`, `QT_APLICATIVOS`, `QT_DONOS`, `QT_ARQUIVOS`, `QT_EMAILS`, `QT_CONTAS_FINANCEIRAS`, `QT_REDES_WIFI`, `QT_SENHAS`, `QT_AUTOFILL`). O esquema é genérico: a Nuvem IPED usa a mesma tabela com `NO_FABRICANTE = 'IPED'` (ver [extracao_iped.md](extracao_iped.md)).
- Permissões: `INQ_TB03_PERMISSOES` (pessoa–inquérito, `IN_ADMIN` para gestor) — ver [Permissões e multidelegacia](permissoes_delegacia.md).

## Configuração

- `PASTA_INVESTIGACAO` (variável de ambiente, padrão `/app/data`): pasta onde ficam os `.ufdr` e onde o seletor de arquivos procura. Se vazia, o front desabilita a extração com aviso.
- Subpastas de delegacia (`data/<delegacia>/<pedido>/`) para organização; a rota `extrair` recebe `delegacia` e `delegacia_permitida` confina o acesso à unidade do usuário (admin enxerga todas).
- A transcrição e a indexação semântica têm configurações próprias (ver os respectivos documentos).

## Referências

- `README.md` na raiz (visão geral); índice em `docs/README.md`.
- `api/funcoes/ufdr_parser.py` — `ler_info_dispositivo`, `indexar_indicadores` (+ `indexar_fotos_perfil`, `indexar_por_nome_base`), `iterar_registros` (`_iterar_modelos_topo`, `_PilhaConteineres`, limpeza seletiva, despacho por tipo, tipos desconhecidos, consolidação de contas derivadas), `_casar_por_tamanho`, helpers `_valor_campo`, `_timestamp_iso`, `_estado_ufdr`, `_submodelo`/`_submodelos`/`_pares_chave_valor`/`_rotulo_party` (as estruturas compostas do UFDR, que não são `<field>`), `_extrair_*` por domínio, extensões/MIME por extensão.
- `api/funcoes/ufdr_dominios_utils.py` — registro dos domínios novos (tabela, colunas, ordenação, campos da busca, e os filtros opcionais `data`/`facetas`/`valor`) e as funções `listar_dominio`/`facetas_dominio`/`contagens_dominios` que atendem as rotas `/api/investigacao/inqueritos/{id}/ufdr/{dominio}` e `.../facetas`.
- `web/js/utils/ufdr_dominio.js` + `web/css/ufdr.css` — componente único de abas e tabela paginada dessas telas; uma página só precisa de um elemento com `data-dominios="chave1,chave2"`.
- `db/init/18_credenciais_financeiro.sql` e `db/init/17_aparelho_redes.sql` — colunas e tabelas da auditoria de aproveitamento. Ambas idempotentes (`IF NOT EXISTS`); em banco já existente quem aplica o delta correspondente é o runner de `db/migracoes/` (ver `atualizacao.md`).
- `api/funcoes/investigacao_utils.py` — `iniciar_extracao`, `_obter_ou_criar_inquerito`, `_processar_extracao` (pipeline, buffer de arquivos via COPY, contagens, etapas adicionais), `_marcar_fase`, `_marcar_progresso`, `_cancelamento_solicitado`, `solicitar_cancelamento_extracao`, `apagar_extracao`/`apagar_inquerito`, `restaurar_arquivo_ufdr` (+ upload), `_gravar_*` por domínio.
- `api/funcoes/ufdr_cache.py` — cache de zips/bytes para leitura sob demanda dos binários.
- `api/tools/teste_parser_ufdr.py` — teste do parser contra um `.ufdr` sintético (memória, cobertura, casamento de anexo). Não precisa de banco.
- `api/tools/teste_ingestao_ufdr.py` — testes de integração contra o Postgres de verdade: o lote de mensagens/anexos (confere que cada anexo ficou na sua mensagem, e desfaz tudo com `rollback`) e a recusa de `.ufdr` duplicado (com as três situações em que ela **não** deve barrar).
- `api/funcoes/hash_utils.py` — `calcular_hashes_arquivo`/`calcular_hashes_bytes`.
- `api/rotas/investigacao.py` — `ExtracaoIn` e rotas de extração (arquivos, extrair, extrações, apagar, cancelar, restaurar, chaves PIX).
- `db/init/10_whatsapp.sql` e demais arquivos `db/init/*.sql` — esquema.
- `web/investigacao.html` + `web/js/investigacao.js` — tela de extração, card de acompanhamento, modal "Todas as extrações", apagar/cancelar/restaurar.
- Documentos relacionados: `hash_arquivos.md` (cadeia de custódia), `transcricao_audio.md`, `busca_semantica.md`, `extracao_iped.md` (mesma tabela de extrações), `permissoes_delegacia.md` (subpastas e acesso), `ingestao.md` (API de ingestão em grupo).
- Tela de disparo desta importação: [importacao.md](importacao.md) (aba correspondente de `web/importacao.html`).
