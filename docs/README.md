# Documentação do Projeto — PolícIA

Este índice reúne a documentação detalhada de cada feature do projeto, um arquivo `.md` por feature, na pasta `docs/`.

O projeto — ver `README.md` na raiz — é uma plataforma de investigação criminal que reúne bases de dados de referência, extração forense de celular (`.ufdr`/Cellebrite), extração genérica de documentos (PDF/XLSX/CSV, RIF do COAF, extrato SIMBA, inquérito ePol, IRPF, IPEI fiscal e retorno de operadora), API FastAPI, interface web (temas Claro/Escuro/Orbit), chat de IA com *function calling*, classificação de imagens e geração de relatório final em Word.

## 1. Plataforma

| Documento | Feature |
|---|---|
| [infraestrutura.md](infraestrutura.md) | Docker Compose (8 containers: banco, API x2, nginx, Ollama, PgBouncer, backups, registry), nginx, `.env`, dados em `data/` e `db/data/`, arquitetura da API FastAPI |
| [backup.md](backup.md) | Dump diário do banco (sidecar) com verificação diária no agendador, teste de restauração e cópia offsite (restic); PgBouncer na frente do Postgres e liderança única dos laços de fundo — a base para réplicas da API |
| [filas_jobs.md](filas_jobs.md) | Tela **Jobs & Filas** (admin): o que roda no servidor agora — OCR, classificação, comparação facial, importações — com vagas de CPU compartilhadas, posição na fila, cancelar/liberar por job e a fila durável de extrações `.ufdr` |
| [atualizacao.md](atualizacao.md) | Versão instalada (`VERSION`), migração de schema rastreada e automática (`db/migracoes/`), atualização pela tela via agente `systemd` no host (`scripts/analyser-update`) |
| [importacao.md](importacao.md) | Tela **Nova importação**: a porta de entrada de todo material (UFDR, RIF, SIMBA, Nuvem IPED, documento avulso) — delegacia, tipo (inquérito/caso de inteligência) e número do inquérito compartilhados pelas abas, envio pelo navegador |
| [organizacao_arquivos.md](organizacao_arquivos.md) | Organização de `data/` por inquérito: o arquivo importado vai para `<DELEGACIA>/<INQUÉRITO>/<CELULAR\|NUVEM\|RIF\|BANCARIO\|OUTROS>/` |
| [interface.md](interface.md) | Interface web: barra de navegação injetada, busca global, diálogos próprios, tema; painel de saúde do servidor (CPU, memória, disco); tela de documentação de código (esta pasta `docs/` lida dentro do sistema) |
| [no_descentralizado.md](no_descentralizado.md) | Nós descentralizados: este repositório é o **central puro** (navegação + recebimento — importação, modelos e discos vivem no nó, outro repositório); registro/token/heartbeat na tela Administração → Servidores, hash, parse, extração, jobs e embeddings no nó via ponte |
| [no_midia_direta.md](no_midia_direta.md) | Mídia direta: o navegador na LAN da delegacia baixa fotos/vídeos do próprio nó (HTTPS 8443, ticket HMAC de 10 min assinado pelo central, Range para seek de vídeo); fora da LAN tudo segue pelo proxy do central sem ninguém notar |
| [instalacao_central.md](instalacao_central.md) | Instalação do **servidor principal (central) do zero**: pacotes + Docker, registry dos nós, `.env` (senha do banco, porta do web), primeira subida (Postgres+migrações), primeiro acesso/admin, agente de atualização, AD + Samba por delegacia e cadastro dos nós |
| [instalacao_no.md](instalacao_no.md) | Instalação de um **nó do zero** na delegacia: pacotes + Docker, clone e `.env` (`PAPEL=no`, token, porta), agente de atualização, ingresso no AD, Samba por delegacia, container `/no/*` e conferência final |
| [orbit.md](orbit.md) | Tema **Orbit** (ex-FRONTEND-V3): integrado à main como terceira opção de tema (Claro/Escuro/Orbit), páginas `_orbit`, navegação superior/lateral, busca e favoritos locais ao navegador |

## 2. Autenticação, usuários e segurança de usuário

| Documento | Feature |
|---|---|
| [autenticacao.md](autenticacao.md) | Login/sessão, autocadastro e aprovação, recuperação de senha, administração de usuários, limite de tentativas |
| [permissoes_delegacia.md](permissoes_delegacia.md) | Multidelegacia (cadastro, IA por delegacia, subpastas) e permissões/isolamento por inquérito (inclui o caso de inteligência) |
| [unidades_prompts.md](unidades_prompts.md) | Árvore de unidades superiores das delegacias (UF, e níveis extras como DIP/DICOR no DF) e prompts de IA complementares em cascata: geral → unidade → delegacia → usuário |
| [auditoria.md](auditoria.md) | Auditoria de acesso a inquérito e de chamadas de IA |

## 3. Banco de dados

| Documento | Feature |
|---|---|
| [banco_dados.md](banco_dados.md) | Schemas, convenção de nomes, migrações |
| [ingestao.md](ingestao.md) | Importação das bases de referência (Detran, Serasa, TSE, IBGE) |
| [convergencia.md](convergencia.md) | Convergência entre dados disponíveis nos inquéritos em banco |

## 4. Extração forense

### 4.1. Arquivos de celular e nuvem

| Documento | Feature |
|---|---|
| [extração_ufdr.md](extração_ufdr.md) | Extração de UFDR/Cellebrite (16 domínios, fases, cancelamento) |
| [ufdr_dominios.md](ufdr_dominios.md) | Domínios que o parser descartava (auditoria de aproveitamento): registro genérico, telas de Redes sociais e buscas, Calendário, Dispositivos reconhecidos e Identidade do aparelho, e as abas novas |
| [extracao_iped.md](extracao_iped.md) | Extração Nuvem IPED (importa uma pasta-caso inteira processada pelo IPED) |
| [extracao_java_iped.md](extracao_java_iped.md) | O dumper Java da Nuvem IPED: por que existe, como funciona, como compilar |
| [investigacao_celular.md](investigacao_celular.md) | Telas de dados extraídos do aparelho e da nuvem: conversas (WhatsApp/SMS/mensageiros), agenda, ligações, localizações+grafo, timeline, notas, navegação, aplicativos, notificações, e-mails, contas bancárias, Wi-Fi, arquivos, chaves PIX, dados de conta Google sem equivalente em UFDR e o **WhatsApp parcial** (mídia de nuvem iCloud/Drive reconstruída como conversa) |
| [transcricao_audio.md](transcricao_audio.md) | Transcrição de áudios (faster-whisper) |
| [extracao_temporaria.md](extracao_temporaria.md) | Botão "Extrair temporariamente" da galeria: cópia temporária (4 h) dos vídeos/imagens da extração em `data/_midia_temp` — miniatura por ffmpeg nos vídeos sem quadro do IPED e modal servido direto do disco, com seek |
| [compartilhar_prova.md](compartilhar_prova.md) | Compartilhar prova: copiar uma extração já processada para outro inquérito sem reimportar o `.ufdr` (o arquivo não é duplicado no disco) |
| [classificacao_imagem.md](classificacao_imagem.md) | Classificação de imagens por CLIP |
| [ocr_imagem.md](ocr_imagem.md) | OCR de imagens (Tesseract) |
| [comparar_rosto.md](comparar_rosto.md) | Comparação facial |
| [comparar_celulares.md](comparar_celulares.md) | Comparar os aparelhos de um inquérito entre si: o mesmo arquivo (hash) nos dois celulares, interlocutores, agenda e Wi-Fi em comum |
| [correlacao_nuvem.md](correlacao_nuvem.md) | Nuvem × aparelho: o arquivo que está na conta na nuvem e **não está mais** no celular — a mídia do WhatsApp apagada do aparelho e preservada pelo backup da galeria |

### 4.2. Relatório financeiro e bancário

| Documento | Feature |
|---|---|
| [rif.md](rif.md) | RIF do COAF/SISCOAF |
| [simba.md](simba.md) | Extrato bancário do SIMBA (R304) |
| [irpf.md](irpf.md) | Declaração de IRPF (Receita Federal) em PDF |
| [ipei.md](ipei.md) | Quebra de sigilo fiscal: a IPEI da Receita Federal (PDF + planilha de anexo, num card só) |
| [telefonia.md](telefonia.md) | Quebra de sigilo telefônico: o retorno da operadora (cadastro, CDR, ERBs, números/IMEI) — vários arquivos num card só, por operadora |
| [documentos_diversos.md](documentos_diversos.md) | Caso de inteligência (inquérito com flag), documento anexado e inquérito policial em PDF (ePol) |
| [visualizador_documento.md](visualizador_documento.md) | Tela de leitura do documento anexado: páginas marcadas no texto extraído, busca, PDF renderizado na própria tela (Texto/PDF/Lado a lado), .docx em blocos formatados (Formatado), .html num iframe sem scripts (Renderizado), arquivo original e o realce do trecho que a IA citou |
| [busca_entidades.md](busca_entidades.md) | Buscar uma lista de pessoas/empresas de uma vez em tudo que foi importado no inquérito (exata e aproximada) |
| [busca_evidencias.md](busca_evidencias.md) | Busca de evidências na tela (o RAG híbrido exposto ao investigador), palette Ctrl+K e ficha de custódia em ZIP |
| [entidades_auto.md](entidades_auto.md) | Entidades automatizadas: biblioteca global de regex com validação (CPF/CNPJ/CNJ/telefone/…) sobre os documentos genéricos — pré-passe da análise por IA e job por inquérito com detecções deduplicadas |

### 4.3. Funções de apoio forense

| Documento | Feature |
|---|---|
| [hash_arquivos.md](hash_arquivos.md) | Hashes (MD5/SHA-1/SHA-256) dos arquivos de origem para cadeia de custódia |
| [busca_semantica.md](busca_semantica.md) | Busca semântica por embeddings |
| [relatorio_word.md](relatorio_word.md) | Relatório final em Word e marcações |
| [marcadores.md](marcadores.md) | Marcadores nomeados (as *tags* do IPED): sumário flutuante do WhatsApp, triagem das conversas, página de marcações e exportação |
| [exportar_excel.md](exportar_excel.md) | Exportação para Excel no navegador |
| [exportar_grafo.md](exportar_grafo.md) | Exportação de grafo para o i2 Analyst's Notebook (CSV/XLSX/ANX) e para GraphML (Gephi, yEd, NetworkX) |

## 5. Mesa de inquérito

| Documento | Feature |
|---|---|
| [mesa_inquerito.md](mesa_inquerito.md) | Mesa de investigação (grafo de duas camadas), entidades/relações/canônicas, identificação de pessoas e busca automática |
| [hipoteses.md](hipoteses.md) | Workspace de **hipóteses** do inquérito: quadro kanban por status, responsável, evidências a favor/contra e pendências — sucessor dos "achados do analista", com visão "Hipótese da mídia" e injeção do quadro no prompt do chat |
| [padroes_crime.md](padroes_crime.md) | Padrões de crime (léxico, regex, temporal) |

## 6. Inteligência artificial

| Documento | Feature |
|---|---|
| [chat_ia.md](chat_ia.md) | Chat de IA e *function calling* |
| [rag_hibrido.md](rag_hibrido.md) | Busca híbrida (literal + semântica + grafo) fundida por RRF, a ferramenta de primeira escolha do chat |
| [configuracoes_ia.md](configuracoes_ia.md) | Configurações do provedor de IA e busca semântica |
| [ia_por_usuario.md](ia_por_usuario.md) | **IA do usuário**: provedor/modelo/chave próprios de um analista, definidos pelo admin na tela Usuários — a exceção que vence o `.env` e a configuração de seção |
| [unidades_prompts.md](unidades_prompts.md) | **Prompts de IA por nível**: instruções adicionais geral/unidade/delegacia/usuário acumuladas no prompt de sistema do chat, com prévia da cascata na tela Configurações |
| [avisos_ia.md](avisos_ia.md) | Avisos de conclusão da IA: o toast/notificação **dentro** do sistema (`GET /api/auth/avisos-ia`) e o **webhook** por usuário para Discord/Slack/n8n |
| [resumo_noturno.md](resumo_noturno.md) | Resumo noturno por inquérito: batch da madrugada (por celular + RIF/SIMBA/… + peças) no topo de `investigacao.html?inquerito=ID` |

## Como os documentos são estruturados

Todo arquivo desta pasta segue o mesmo template:

1. **Visão geral** — o que a feature faz e para quem serve;
2. **Fluxo de uso** — passos na interface;
3. **Como funciona** — arquivos de `api/funcoes/` e `api/rotas/` envolvidos e pontos-chave;
4. **Modelo de dados** — tabelas e schemas do banco;
5. **Configuração** — variáveis de `.env` quando existem;
6. **Referências** — telas, rotas e documentos relacionados.