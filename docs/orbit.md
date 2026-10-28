# Tema Orbit

Orbit substitui o nome FRONTEND-V3. A branch de desenvolvimento é `Orbit` e o identificador local do tema é `orbit`.

## Continuidade

- Evoluir o frontend tela por tela, conforme as escolhas do usuário, reunindo os elementos aprovados da main e da Orbit.
- A Orbit já está **integrada à main** como opção de tema (Claro/Escuro/Orbit) — os arquivos `_orbit` vivem no
  `web/` da main e evoluem junto com ela, sem contratos de backend exclusivos do tema.
- Claro e Escuro utilizam os arquivos originais da main. A extensão em `web/js/utils/theme.js` mantém as variantes no seletor e o encaminhamento de páginas.
- Funcionalidades, permissões e banco são compartilhados com a main. Atualizações da main devem ser integradas, sem criar contratos de backend exclusivos do tema.
- As páginas Orbit usam o mapa de navegação `mapa_telas.js` da main. O acervo e a gestão permanecem acessíveis no resumo do inquérito; recursos compartilhados abrem as mesmas telas da main.

## Arquivos e compatibilidade

As páginas são `index_orbit.html`, `inqueritos_orbit.html` e `inquerito_orbit.html`. Estilos, scripts e imagens próprios usam `orbit` no nome.

A preferência antiga `frontend-v3` migra automaticamente. Os antigos arquivos HTML com sufixo `_frontend_v3` foram removidos; os endereços atuais usam `_orbit`. O identificador Orbit não é enviado à API de preferência da conta; Claro/Escuro mantêm a persistência original.

## Atualizar outra instalação Docker

Faça backup do banco antes de atualizar. Após obter a branch Orbit, execute `docker compose up -d --build`. As migrações de `db/migracoes` são aplicadas pelo startup da API, exceto quando `MIGRAR_NO_STARTUP=0`. Consulte `docker compose exec -T api-1 python /app/tools/migrar.py --estado`; se necessário, aplique pendentes com `docker compose exec -T api-1 python /app/tools/migrar.py`. Não marque baseline para ignorar migrações pendentes.

Desde a integração, a Orbit acompanha a main: o `VERSION` da raiz (hoje `4.2.2`) é único para os dois, e todo o `db/init/` e os deltas de `db/migracoes/` (até `0042_`) são compartilhados. Como referência histórica da integração: as migrações 0010 a 0020 acrescentaram trabalhos em segundo plano, enriquecimento IPED, raciocínio e auditoria da IA, compartilhamento de extrações, Sigilo Fiscal/IPEI, Telefonia, feedback de respostas e o resumo noturno; a migração **0023** (que veio do Orbit numerada como 0021) sustenta o acompanhamento do raciocínio durante o processamento.

> **Atenção a quem atualizou entre o merge do Orbit e esta correção.** O delta do Orbit e o de paginação da telefonia foram publicados os dois como `0021_`, e o número é a identidade da migração no ledger: cada instalação aplicou um deles e **pulou o outro em silêncio** — sem a coluna `TX_PROGRESSO` (e aí o acompanhamento ao vivo do turno falha) ou sem os índices do CDR (e aí a telefonia fica lenta). Os dois viraram `0023_ia_progresso_orbit.sql` e `0024_telefonia_paginacao.sql`, ambos idempotentes: subir a API (ou rodar `tools/migrar.py`) aplica o que faltava e não repete o que já existe.

As telas compartilhadas de todos os temas receberam as notificações de respostas da IA, o raciocínio da IA, as novas visões de Convergência (`Entre inquéritos` e `Casos ↔ acervo`), correlação com o acervo e as páginas de Sigilo Fiscal e Telefonia. A lista e o detalhamento próprios da Orbit também apresentam essas duas novas fontes, com acesso direto aos respectivos dados.

No tema Orbit, a conversa de IA acompanha o turno em andamento com tempo decorrido, etapa operacional, raciocínio parcial disponibilizado pelo provedor e consultas executadas. A migração 0023 guarda essa trilha parcial no processamento para o SSE funcionar também com vários workers; ao final, o painel dá lugar à resposta e a trilha definitiva continua acessível dentro da bolha.

Na visão de inquérito do Orbit, as evidências UFDR usam marca/modelo como título, apresentam identidade e contagens relevantes em um resumo e oferecem um menu próprio para IA por evidência, achados, hashes, transcrição e gestão da prova, conforme a permissão do usuário.

O detalhamento do inquérito usa uma estrutura compacta, sem o submenu intermediário de resumo, fontes, achados e contatos. O cabeçalho oferece as quatro ações diretas e as gavetas `Extras` e `Gerência do inquérito`, com o mesmo conteúdo funcional da main. As evidências são o conteúdo principal do grande contêiner do inquérito; seus filtros usam os nomes da main e só aparecem quando há conteúdo naquela categoria.

Ao expandir uma evidência, o cartão permanece em sua coluna e separa os atalhos de dados internamente em `Principais` e `Demais`. Esses atalhos usam o padrão da main, com o pictograma branco dentro de um círculo da cor da categoria. Os cartões usam os mesmos ícones e cores de domínio da tela principal, com título mais destacado, marca/modelo, arquivo de origem, situação do processamento, disponibilidade do arquivo e síntese dos achados. O menu próprio de cada evidência continua disponível quando ela está recolhida e o cartão ativo sobe de camada para que a lista de ações não seja coberta pela evidência seguinte.

## Verificação

Execute `node tests/orbit.test.cjs` para conferir migração do tema, navegação, fontes de evidência e os recursos compartilhados recebidos da main.

## Navegação superior ou lateral

O seletor à esquerda de Temas alterna entre menu superior e lateral, apenas na Orbit. No modo lateral, ambos ficam no rodapé, acima da conta, e a barra de navegação superior desaparece. A conta segue o visual dos outros itens, sem separador ou seta. Clicar na marca PolícIA recolhe ou expande a lateral. O rodapé reutiliza o menu original da conta, incluindo senha, e-mail, webhook, avisos e saída. Nova importação é a segunda entrada da subseção Inquéritos/casos. Administração tem submenu próprio, visível somente para administradores, com os mesmos destinos do menu superior. Todos os Inquéritos/casos abre diretamente `inqueritos_orbit.html`.

Pesquisa e Ctrl/Cmd+K abrem a busca de inquéritos, casos e telas em qualquer página Orbit que use o menu compartilhado. Favoritos são adicionados ou removidos pelas estrelas dos resultados. Meus Inquéritos/casos filtra pelo identificador do criador; Todos é mostrado somente para administradores. Recentes registra os últimos inquéritos/casos abertos e só exibe os que continuam retornando na lista autorizada pela API.

O fundo animado da página inicial, inspirado no Particleground, também aparece na tela de login. A malha de partículas é exibida diretamente sobre o fundo-base do tema, sem máscara radial ou camada de degradê escuro, e respeita a preferência de redução de movimento do sistema e o controle de pausa.

Posição, recolhimento, favoritos e recentes são locais a este navegador, separados pelo ID do usuário. Não há sincronização entre dispositivos nem alteração de banco. As permissões continuam sendo impostas pelas APIs originais da main. A nova navegação é carregada por `theme.js`, com os arquivos exclusivos `navigation_orbit.js` e `navigation_orbit.css`.

O seletor de inquérito ativo aparece somente em `inquerito_orbit.html` ou no detalhamento de `investigacao.html`, com `inquerito` e sem `caso`. Usa a lista autorizada pela API, fica na navegação superior ou abaixo da marca lateral, e troca de inquérito sem reutilizar IDs de documento/extração. Teste: `node tests/orbit-inquiry.test.cjs`.
