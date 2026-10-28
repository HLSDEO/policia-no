const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const theme = fs.readFileSync('web/js/utils/theme.js', 'utf8');
function setup(id, page, query = '', hash = '') {
  let current = id;
  const saved = {}, posts = [], redirects = [];
  const selection = { each() { return this; }, on() { return this; } };
  function $(arg) { if (typeof arg === 'function') arg(); return selection; }
  $.ajax = options => posts.push(JSON.parse(options.data));
  const root = { getAttribute: () => current, setAttribute: (_, value) => current = value,
    classList: { add() {}, remove() {} }, offsetHeight: 0 };
  const window = { location: { pathname: '/' + page, search: query, hash, replace: url => redirects.push(url) }, Icones: { html: () => '' } };
  vm.runInNewContext(theme, { window, document: { documentElement: root, querySelector: () => null }, $, URLSearchParams, setTimeout: fn => fn(), localStorage: { setItem: (k,v) => saved[k] = v } });
  return { api: window.Tema, saved, posts, redirects };
}
let t = setup('frontend-v3', 'index.html', '?x=1', '#busca');
assert.equal(t.api.atual(), 'orbit');
assert.equal(t.saved['analyser-theme'], 'orbit');
assert.equal(t.redirects[0], 'index_orbit.html?x=1#busca');
assert.match(t.api.dropdownHtml(), />Orbit</);
assert.doesNotMatch(t.api.dropdownHtml(), /FRONTEND-V3/);
for (const id of ['dark', 'light']) {
  for (const page of ['index.html', 'inqueritos.html', 'investigacao.html']) {
    t = setup(id, page, '?inquerito=23');
    assert.equal(t.redirects.length, 0);
  }
  t = setup('orbit', 'inquerito_orbit.html', '?inquerito=23', '#fontes');
  t.api.escolher(id);
  assert.equal(t.redirects.at(-1), 'investigacao.html?inquerito=23#fontes');
  assert.equal(t.posts[0].tema, id);
}
for (const query of ['?caso=4', '?inquerito=23&doc=9', '?inquerito=23&aba=documentos', '?inquerito=23&tema_rota=ignorar']) {
  t = setup('orbit', 'investigacao.html', query);
  assert.equal(t.redirects.length, 0, query);
}
t = setup('dark', 'index.html'); t.api.escolher('orbit');
assert.equal(t.posts.length, 0);
t.api.definirDoServidor('light'); assert.equal(t.api.atual(), 'orbit');
t = setup('frontend-v2', 'index.html');
assert.equal(t.api.atual(), 'dark');
assert.equal(t.saved['analyser-theme'], 'dark');
assert.doesNotMatch(t.api.dropdownHtml(), /FRONTEND-V2/);
for (const antigoArquivo of [
  'web/index_frontend_v2.html', 'web/inqueritos_frontend_v2.html', 'web/inquerito_frontend_v2.html',
  'web/css/style_frontend_v2.css', 'web/js/inqueritos_frontend_v2.js', 'web/js/utils/sidebar_frontend_v2.js',
]) assert.equal(fs.existsSync(antigoArquivo), false, antigoArquivo);
for (const page of ['index', 'inqueritos', 'inquerito']) {
  const orbit = fs.readFileSync(`web/${page}_orbit.html`, 'utf8');
  for (const match of orbit.matchAll(/(?:src|href)="((?:js|css|img)\/[^"?#]+)/g)) assert.ok(fs.existsSync('web/' + match[1]), match[1]);
  assert.match(orbit, /js\/utils\/mapa_telas.js/);
  assert.match(orbit, /js\/utils\/avisos_ia.js/);
  assert.match(orbit, /css\/nova_importacao_orbit.css/);
  assert.match(orbit, /js\/nova_fonte_orbit.js/);
}
const source = fs.readFileSync('web/js/inquerito_orbit.js', 'utf8');
const orbitDetail = fs.readFileSync('web/inquerito_orbit.html', 'utf8');
const orbitDetailCss = fs.readFileSync('web/css/inquerito_orbit.css', 'utf8');
assert.match(orbitDetail, /class="inquerito-orbit-shell hidden"/);
assert.doesNotMatch(orbitDetail, /data-view="resumo"|data-panel="resumo"/);
assert.doesNotMatch(orbitDetail, /inquerito-detalhe-navegacao|Fontes e documentos|Achados e convergências|Entidades e contatos/);
for (const action of ['inquerito-extras-toggle', 'inquerito-gerencia-toggle']) assert.match(orbitDetail, new RegExp(`id="${action}"`));
assert.match(source, /function modeloDoAparelho/);
assert.match(source, /function resumoDoAparelho/);
assert.match(source, /\.text\("Demais"\)/);
assert.match(source, /Compartilhar ou gerenciar prova/);
assert.match(source, /Transcrever áudios/);
assert.match(source, /Apagar extração/);
for (const action of ['Comparar rostos', 'Comparar celulares', 'Nuvem × aparelho', 'Buscar entidades no material', 'Contexto do caso', 'Indexar busca semântica', 'Gerência de permissões', 'Delegacia do inquérito']) assert.match(source, new RegExp(action));
for (const filter of ['Celulares', 'RIFs', 'SIMBA', 'Telefonia', 'Outros documentos']) assert.match(source, new RegExp(`rotulo: "${filter}"`));
assert.match(source, /\.filter\(function \(tipo\) \{ return Number\(tipo\.quantidade\) > 0; \}\)/);
for (const endpoint of ['/ipeis', '/telefonia']) assert.match(source, new RegExp(endpoint));
assert.doesNotMatch(orbitDetailCss, /inquerito-evidencia\.aberta\s*\{\s*grid-column:\s*1 \/ -1/);
assert.match(orbitDetailCss, /inquerito-detalhe-evidencias\s*\{[^}]*grid-template-columns:\s*repeat\(2/s);
assert.match(orbitDetailCss, /inquerito-evidencia\.aberta \.inquerito-evidencia-grupos\s*\{[^}]*grid-template-columns:\s*repeat\(2/s);
assert.match(orbitDetailCss, /inquerito-evidencia\s*\{[^}]*overflow:\s*visible/s);
assert.match(source, /removeClass\("menu-aberto"\)/);
assert.match(source, /toggleClass\("menu-aberto", abrir\)/);
assert.match(orbitDetailCss, /inquerito-evidencia\.menu-aberto\s*\{\s*z-index:\s*80/);
assert.match(orbitDetailCss, /inquerito-evidencia-pill > span\s*\{[^}]*border-radius:\s*50%[^}]*background:\s*var\(--pill-accent[^}]*color:\s*#fff/s);
assert.match(orbitDetailCss, /inquerito-evidencia-pill strong\s*\{\s*color:\s*#fff/);

const orbitList = fs.readFileSync('web/inqueritos_orbit.html', 'utf8');
const orbitListJs = fs.readFileSync('web/js/inqueritos_orbit.js', 'utf8');
assert.doesNotMatch(orbitList, /page-subtitle|inquerito-panorama|Panorama do inquérito/);
for (const id of ['stats-skeleton', 'stat-casos', 'inq-filtro-tipo', 'inq-filtro-status', 'inq-filtro-delegacia', 'inq-ordem', 'inq-total', 'nova-importacao-btn', 'orbit-selection-insights', 'orbit-selection-panel-title', 'orbit-selection-bars']) {
  assert.match(orbitList, new RegExp(`id="${id}"`), id);
}
assert.match(orbitList, /Nova Importação/);
assert.doesNotMatch(orbitList, /id="modal-importar"|js\/inqueritos_importar.js/);
assert.match(orbitList, /Use as setas do teclado ou os controles laterais para navegar/);
for (const evidence of ['"UFDR"', '"SIMBA"', '"RIF"', '"IPL"', '"IRPF"', '"Sigilo fiscal"', '"Telefonia"', '"Documentos"']) assert.match(orbitListJs, new RegExp(evidence));
for (const quantity of ['qt_ipeis', 'qt_telefonia']) assert.match(orbitListJs, new RegExp(quantity));
assert.match(orbitListJs, /ANDAMENTO: /);
assert.match(orbitListJs, /investigacao\.html\?inquerito=.*&aba=/);
assert.match(orbitListJs, /inquerito\.html\?inquerito=/);
assert.match(orbitListJs, /function renderSelection\(item\)/);
assert.match(orbitListJs, /PAINEL - /);
const orbitListCss = fs.readFileSync('web/css/inqueritos_orbit.css', 'utf8');
assert.doesNotMatch(orbitListCss, /backdrop-filter/);
assert.doesNotMatch(orbitListCss, /orbit-ia-(?:glint|pulse)/);
assert.match(orbitListCss, /carousel-card\.active\s*\{[^}]*filter:\s*none/s);
assert.match(orbitListCss, /carousel-card\.prev\s*\{[^}]*translateX\(-78%\)/s);
assert.match(orbitListCss, /orbit-selection-details\s*\{[^}]*grid-template-columns:\s*repeat\(2/s);
assert.match(orbitListCss, /#lista-view\s*\{[^}]*background:\s*transparent/s);
const orbitNavigation = fs.readFileSync('web/js/utils/navigation_orbit.js', 'utf8');
assert.match(orbitNavigation, /topnav-brand \.brand-mark.*clone\(\)/);
assert.match(orbitNavigation, /inqueritos_orbit\.html\?nova_importacao=1/);
const orbitImport = fs.readFileSync('web/js/nova_fonte_orbit.js', 'utf8');
for (const sourceLabel of ['Indexação Cellebrite (UFDR)', 'RIF - COAF (CSV)', 'Bancário SIMBA R304 (CSV)', 'Nuvem IPED (pasta do caso)', 'Arquivo genérico']) assert.ok(orbitImport.includes(sourceLabel), sourceLabel);
for (const endpoint of ['/api/investigacao/extrair', '/api/investigacao/extrair-rif', '/api/investigacao/extrair-simba', '/api/investigacao/extrair-iped', '/api/investigacao/extrair-documento', '/api/documentos/upload', '/api/documentos/upload-zip']) assert.ok(orbitImport.includes(endpoint), endpoint);
assert.match(orbitImport, /Caso de intelig.ncia/);
assert.doesNotMatch(orbitImport, /Caso gen.rico/);
assert.doesNotMatch(orbitImport, /\/api\/documentos\/casos/);
assert.match(orbitImport, /Inquérito[\s\S]*Fonte[\s\S]*Configuração/);
assert.match(orbitImport, /PADRAO_EPOL = \/\^\\d\{4\}\\\.\\d\{7\}-\\d\{2\}\$\//);
assert.match(orbitImport, /pattern="\[0-9\]\{4\}\\\\\.\[0-9\]\{7\}-\[0-9\]\{2\}"/);
assert.match(orbitImport, /\.prop\("readonly", contexto\.existente\)/);
for (const file of ['web/index_orbit.html', 'web/inquerito_orbit.html']) {
  const html = fs.readFileSync(file, 'utf8');
  assert.match(html, /Nova Importação/);
  assert.doesNotMatch(html, />Novo [Ii]nquérito|>Nova [Ff]onte de [Dd]ados/);
}
const orbitHomeCss = fs.readFileSync('web/css/home_orbit.css', 'utf8');
const login = fs.readFileSync('web/login.html', 'utf8');
assert.match(orbitHomeCss, /home-radar-background\s*\{[^}]*background:\s*transparent/s);
assert.doesNotMatch(orbitHomeCss, /home-radar-background::after|mask-image/);
assert.match(login, /class="home-radar-layer home-radar-network"/);
assert.match(login, /js\/home_radar_orbit\.js/);
const timeline = fs.readFileSync('web/timeline.html', 'utf8');
const timelineOrbit = fs.readFileSync('web/js/timeline_orbit.js', 'utf8');
const timelineOrbitCss = fs.readFileSync('web/css/timeline_orbit.css', 'utf8');
const timelineBase = fs.readFileSync('web/js/timeline.js', 'utf8');
const timelineRoute = fs.readFileSync('api/rotas/investigacao.py', 'utf8');
const timelineService = fs.readFileSync('api/funcoes/investigacao_utils.py', 'utf8');
for (const marker of [
  'class="tl-orbit-lista"', 'tl-orbit-modebar', 'tl-orbit-kpis', 'tl-orbit-chart',
  'data-view="visual"', 'data-view="lista"', 'Combinado', 'Por conjunto',
  'data-zoom="in"', 'data-zoom="out"', 'tl-orbit-detail-panel', 'tl-orbit-clear-filters',
]) assert.match(timeline, new RegExp(marker));
assert.doesNotMatch(timeline, /tl-orbit-filter-toggle/);
assert.match(timeline, /css\/timeline_orbit\.css/);
assert.match(timeline, /js\/timeline_orbit\.js/);
assert.match(timelineOrbit, /getAttribute\("data-theme"\) !== "orbit"/);
assert.match(timelineOrbit, /analitico: true/);
assert.match(timelineOrbit, /extracaoDaUrl/);
assert.match(timelineOrbit, /function compactarPontos/);
assert.match(timelineOrbit, /function abrirDia/);
assert.match(timelineOrbit, /function abrirPainel/);
assert.match(timelineOrbit, /function controlarZoom/);
assert.match(timelineOrbit, /function aplicarDestaque/);
assert.match(timelineOrbit, /function animarEnxame/);
assert.match(timelineOrbit, /fimFixo - amplitude/);
assert.match(timelineOrbit, /#tl-orbit-clear-filters/);
assert.match(timelineOrbit, /selecionarVisualizacao\("lista"\)/);
assert.match(timelineBase, /window\.TimelineListaOrbit/);
assert.match(timelineBase, /!document\.body\.classList\.contains\("tl-orbit-lista"\)/);
assert.match(timelineOrbitCss, /html:not\(\[data-theme="orbit"\]\)/);
assert.match(timelineOrbitCss, /body\.tl-orbit-lista \.tl-list-view/);
assert.match(timelineOrbitCss, /\.tl-orbit-dot\.dimmed/);
assert.match(timelineRoute, /analitico: bool = False/);
assert.match(timelineService, /estatisticas AS/);
assert.match(timelineService, /dia_pico/);
const mesaHtml = fs.readFileSync('web/mesa_inquerito.html', 'utf8');
const mesaJs = fs.readFileSync('web/js/mesa_inquerito.js', 'utf8');
const mesaOrbitCss = fs.readFileSync('web/css/mesa_orbit.css', 'utf8');
const whatsappCss = fs.readFileSync('web/css/whatsapp.css', 'utf8');
assert.match(mesaHtml, /css\/mesa_orbit\.css/);
for (const marker of ['mesa-no-pino-3d', 'mesa-no-volume-luz', 'criarPinoTridimensional', 'posicionarPinoTridimensional', 'MutationObserver', 'zoomPeloCentro', 'requestFullscreen']) {
  assert.match(mesaJs, new RegExp(marker));
}
assert.doesNotMatch(mesaJs, /mesa-no-cartao|posicionarCartao/);
for (const marker of ['mesa-realista-check', 'mesa-zoom-in', 'mesa-zoom-out', 'mesa-enquadrar', 'mesa-tela-cheia']) {
  assert.match(mesaHtml, new RegExp('id="' + marker + '"'));
}
assert.doesNotMatch(mesaHtml, /id="mesa-realista-check"[^>]*checked/);
assert.match(mesaJs, /getAttribute\("data-theme"\) === "orbit"/);
assert.match(mesaJs, /document\.body\.classList\.contains\("mesa-realista"\)/);
assert.match(mesaJs, /Quadro de Investigação/);
for (const marker of ['mesa-grafo-wrap', 'mesa-no-volume-luz', 'mesa-no-pino-3d', 'mesa-aresta', 'mesa-drawer']) {
  assert.match(mesaOrbitCss, new RegExp(':root\\[data-theme="orbit"\\][^}]*\\.' + marker));
}
assert.match(mesaOrbitCss, /body\.mesa-realista/);
assert.match(mesaOrbitCss, /@keyframes mesa-tachinha-perfurar/);
assert.match(mesaOrbitCss, /#mesa-grafo-wrap:fullscreen/);
assert.doesNotMatch(mesaOrbitCss, /mesa-no-cartao/);
assert.ok(fs.existsSync('web/img/orbit-cork-texture.jpg'));
for (const selector of [/data-theme="orbit"\] \.wa-app/, /data-theme="orbit"\] \.wa-busca input/,
  /data-theme="orbit"\] \.wa-conversa-busca-input/, /data-theme="orbit"\] \.wa-bolha/]) {
  assert.match(whatsappCss, selector);
}
assert.match(fs.readFileSync('web/inquerito_orbit.html', 'utf8'), /Quadro de Investigação/);
assert.match(fs.readFileSync('web/js/inqueritos_orbit.js', 'utf8'), /Quadro de Investigação/);
assert.match(fs.readFileSync('web/js/utils/mapa_telas_orbit.js', 'utf8'), /Quadro de Investigação/);
const convergence = fs.readFileSync('web/convergencia.html', 'utf8');
assert.match(convergence, /Pares com sinal em comum/);
assert.doesNotMatch(convergence, /Casos ↔ acervo/);
assert.doesNotMatch(convergence, /\/api\/investigacao\/convergencia-casos/);
assert.match(fs.readFileSync('web/ia.html', 'utf8'), /js\/utils\/ia_raciocinio\.js/);
assert.match(fs.readFileSync('web/ia.html', 'utf8'), /js\/ia_orbit_live\.js/);
const orbitLive = fs.readFileSync('web/js/ia_orbit_live.js', 'utf8');
for (const marker of ['tx_progresso', 'tx_etapa_atual', 'Processando há', 'Raciocínio em andamento', 'EventSource']) assert.match(orbitLive, new RegExp(marker));
assert.match(fs.readFileSync('db/migracoes/0023_ia_progresso_orbit.sql', 'utf8'), /TX_PROGRESSO JSONB/);
const numerosMigracoes = fs.readdirSync('db/migracoes')
  .map(nome => nome.match(/^(\d{4})_/))
  .filter(Boolean)
  .map(match => match[1]);
for (const page of ['ipei.html', 'telefonia.html', 'correlacao_nuvem.html']) assert.ok(fs.existsSync('web/' + page), page);
const sidebarOrbit = fs.readFileSync('web/js/utils/sidebar_orbit.js', 'utf8');
assert.match(sidebarOrbit, /topnav-nao-admin/);
assert.match(sidebarOrbit, /\$topnav\.find\("\.topnav-nao-admin"\)\.remove\(\)/);
const homeOrbitJs = fs.readFileSync('web/js/home_orbit.js', 'utf8');
assert.match(homeOrbitJs, /toggleBuscaAdmin/);
assert.match(homeOrbitJs, /home-admin/);
const homeOrbitCss = fs.readFileSync('web/css/home_orbit.css', 'utf8');
assert.match(homeOrbitCss, /body\.home-admin section\[aria-labelledby="busca-rotulo"\]/);
assert.match(homeOrbitCss, /body\.home-admin \.home-create-actions/);
const navOrbit = fs.readFileSync('web/js/utils/navigation_orbit.js', 'utf8');
assert.match(navOrbit, /user\.in_admin/);
assert.doesNotMatch(navOrbit, /\/api\/documentos\/casos/);
// Caso de inteligência: unificado no inquérito (sem tabelas/páginas próprias de caso)
const importacaoHtml = fs.readFileSync('web/importacao.html', 'utf8');
assert.match(importacaoHtml, /id="imp-tipo-select"/);
assert.match(importacaoHtml, /Caso de intelig.ncia/);
assert.doesNotMatch(importacaoHtml, /imp-tab-caso|imp-panel-caso|caso-form/);
const importacaoJs = fs.readFileSync('web/js/importacao.js', 'utf8');
assert.match(importacaoJs, /pode_caso_inteligencia/);
assert.match(importacaoJs, /caso_inteligencia/);
assert.doesNotMatch(importacaoJs, /\/api\/documentos\/casos/);
const investigacaoJs = fs.readFileSync('web/js/investigacao.js', 'utf8');
assert.match(investigacaoJs, /in_caso_inteligencia/);
assert.doesNotMatch(investigacaoJs, /\/api\/documentos\/casos/);
assert.doesNotMatch(investigacaoJs, /renderCardCaso/);
const inqueritosJs = fs.readFileSync('web/js/inqueritos.js', 'utf8');
assert.match(inqueritosJs, /in_caso_inteligencia/);
assert.doesNotMatch(inqueritosJs, /\/api\/documentos\/casos/);
assert.doesNotMatch(inqueritosJs, /ia_caso\.html/);
const rotasDocumentos = fs.readFileSync('web/js/documento.js', 'utf8');
assert.doesNotMatch(rotasDocumentos, /\/api\/documentos\/casos/);
for (const antigoCaso of [
  'web/caso_grafo.html', 'web/caso_analitico.html', 'web/caso_dados.html',
  'web/caso_identidades.html', 'web/caso_achados.html', 'web/caso_timeline.html',
  'web/ia_caso.html', 'web/js/ia_caso.js', 'web/js/utils/ia_caso_flutuante.js',
  'web/js/utils/doc_caso.js',
]) assert.equal(fs.existsSync(antigoCaso), false, antigoCaso);
const authUtils = fs.readFileSync('api/funcoes/auth_utils.py', 'utf8');
assert.match(authUtils, /"pode_caso_inteligencia": perfil in \("inteligencia", "agente_inteligencia"\)/);
assert.match(fs.readFileSync('db/init/08_inqueritos.sql', 'utf8'), /IN_CASO_INTELIGENCIA/);
assert.match(fs.readFileSync('db/migracoes/0033_caso_inteligencia_inquerito.sql', 'utf8'), /IN_CASO_INTELIGENCIA/);
assert.equal(fs.existsSync('db/init/23_casos.sql'), false);
console.log('PASS: routes, Orbit parity, new evidence sources, convergence, live AI reasoning, assets and source links.');
