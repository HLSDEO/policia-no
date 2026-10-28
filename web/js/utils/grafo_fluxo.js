/* Motor do grafo de fluxo financeiro — compartilhado por grafo_rif.html e grafo_simba.html.
 *
 * As duas telas desenham a mesma coisa: entidades (pessoa/empresa) deduplicadas por documento,
 * com o TITULAR da conta no meio, quem creditou apontando para ele e ele apontando para quem foi
 * debitado, mais a instituição pendurada no titular. Só a ORIGEM dos dados muda (RIF do COAF x
 * extrato R304 do SIMBA), então aqui mora tudo o que é desenho: layout force-directed, limite de
 * nós, legenda, zoom/pan, arraste, gaveta de detalhe com foco (um nó por clique, vários por
 * Shift+clique) e PNG do desenho. A exportação para i2 mora em js/utils/exportar_i2.js,
 * compartilhada com as outras telas de grafo; aqui só se monta o recorte que vai para lá.
 *
 * O que a página entrega: um grafo já montado e filtrado — `{ nos: [...], arestas: [...] }` — em
 * que cada nó tem `tipo`, `rotulo` e `r` (raio), e cada aresta tem `i`/`j` (índices em `nos`),
 * `dir`, `rotulo`, `largura` e `semValor`. O que a página configura: cores/ícones por tipo, cores
 * e traço por direção, os campos da gaveta e o rótulo de cada ligação.
 *
 * Motivo de existir: as duas telas nasceram como cópias uma da outra e a cópia já cobrou caro —
 * ao renomear as direções das arestas numa delas, TODAS as linhas passaram a sair cinza porque o
 * mapa de cores ficou para trás. Com um motor só, a correção vale para as duas.
 *
 * Contrato de DOM (os ids são os mesmos nas duas páginas):
 *   #gf-grafo          <svg> do desenho
 *   #gf-legenda        div da legenda
 *   #gf-dica           parágrafo da dica de uso
 *   #gf-vazio          aviso de "sem dados"
 *   #gf-info-limite    faixa "mostrando N de M" + #gf-carregar-mais
 *   #gf-drawer         gaveta (+ #gf-drawer-titulo, #gf-drawer-sub, #gf-drawer-corpo,
 *                      #gf-drawer-acoes, #gf-drawer-fechar)
 *   #gf-modo           controle Resumido/Detalhado (opcional; ver controleModo e resumir)
 *   #gf-kpis           painel de achados clicáveis (opcional; ver renderKPIs)
 *   #gf-drawer-expandir / #gf-drawer-nova  botões do cabeçalho da gaveta (⤢ expande, ↗ ficha)
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var G_W = 1280, G_H = 820, G_RAIO = 12, G_RAIO_GRANDE = 18;

  // A tela do layout CRESCE com o número de nós, em vez de espremer todo mundo nos mesmos
  // 1280x820 (mesma ideia da mesa de inquérito, ver mesa_inquerito.js/areaLayout). Espremidos, os
  // nós não cabem: o passo anti-sobreposição, que não conhece a caixa, empurra todo mundo para
  // fora e o desenho termina com milhares de unidades de altura e arestas atravessando a tela —
  // cada contraparte largada longe do titular dela, parecendo "solta, sem relação".
  var NOS_POR_TELA_BASE = 45;
  var FATOR_AREA_MAX = 4;      // teto, para grafo enorme não virar um mapa vazio
  // Puxão para o centro que substitui o grampo na borda da caixa: grampear empilha nós na margem,
  // e sem grampo nenhum os componentes desconexos saem voando.
  var GRAVIDADE = 0.35;

  // RIFs e extratos grandes chegam a milhares de nós; o layout é O(n²) por iteração e trava a aba
  // se rodar sobre o grafo inteiro. Por padrão desenha só os nós mais relevantes e oferece
  // "Carregar mais" — quando há filtro ativo o limite sai de cena (quem filtrou quer ver tudo).
  var LIMITE_PADRAO = 1000;

  function el(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    return e;
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function soDigitos(s) { return String(s || "").replace(/\D/g, ""); }
  function norm(s) { return String(s || "").trim().toUpperCase(); }
  function chaves(o) { return Object.keys(o || {}); }
  function truncar(t, m) { return AnalyserUtils.truncar(t, m); }
  function formatarReais(n) {
    return "R$ " + num(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function abrevReais(v) {
    v = num(v);
    var sinal = v < 0 ? "-" : ""; v = Math.abs(v);
    if (v >= 1e9) return sinal + "R$ " + (v / 1e9).toFixed(1).replace(".", ",") + "bi";
    if (v >= 1e6) return sinal + "R$ " + (v / 1e6).toFixed(1).replace(".", ",") + "mi";
    if (v >= 1e3) return sinal + "R$ " + Math.round(v / 1e3) + "k";
    return sinal + "R$ " + v.toFixed(0);
  }
  // "100.000,00", "100000.00" e "100000" → 100000. Ponto sozinho em grupos de 3 é milhar (pt-BR).
  function parseValorBR(texto) {
    var s = String(texto == null ? "" : texto).trim().replace(/^R\$\s*/i, "").replace(/\s/g, "");
    if (!s) return NaN;
    if (s.indexOf(",") !== -1) s = s.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    var n = Number(s);
    return isFinite(n) ? n : NaN;
  }
  function aplicarMascaraMonetaria(input) {
    if (!input) return;
    input.addEventListener("input", function (e) {
      var valor = e.target.value.replace(/\D/g, "");
      if (!valor) { e.target.value = ""; return; }
      var centavos = valor.slice(-2);
      var inteiros = (valor.slice(0, -2).replace(/^0+/, "") || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      e.target.value = inteiros + "," + centavos;
    });
    input.addEventListener("blur", function (e) {
      if (e.target.value === "," || e.target.value === "0,00") e.target.value = "";
    });
    input.addEventListener("focus", function (e) { if (!e.target.value) e.target.value = "0,00"; });
  }
  function formatarData(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\d)(T[\d:.]+)?$/, "$1T00:00:00Z"));
    if (isNaN(d)) return "—";
    return d.toLocaleDateString("pt-BR", { timeZone: "UTC" });
  }

  /* Intervalo legível de uma aresta, para o atributo "Período" do i2. Devolve "" quando não há
     data nenhuma (o exportador omite atributo vazio) e uma data só quando as duas coincidem ou
     falta uma — "— a 03/02/2026" seria pior que não dizer nada. */
  function periodo(dtDe, dtAte) {
    var de = dtDe ? formatarData(dtDe) : "";
    var ate = dtAte ? formatarData(dtAte) : "";
    if (de === "\u2014") de = "";
    if (ate === "\u2014") ate = "";
    if (!de && !ate) return "";
    if (!de || !ate || de === ate) return de || ate;
    return de + " a " + ate;
  }

  function areaLayout(qtd) {
    var fator = Math.min(Math.max(Math.sqrt(Math.max(qtd, 1) / NOS_POR_TELA_BASE), 1), FATOR_AREA_MAX);
    return { w: G_W * fator, h: G_H * fator };
  }

  /* Raio dos nós e espessura das arestas proporcionais ao R$ (raiz quadrada, para o círculo
     crescer em ÁREA e não em raio — senão um nó 100x maior fica ilegível). A página diz de onde
     sai o valor de cada nó/aresta; o resto é igual nas duas. */
  function escalar(g, cfg) {
    var maxNo = 0, maxAresta = 0;
    g.nos.forEach(function (n) { n._valor = num(cfg.valorNo(n)); if (n._valor > maxNo) maxNo = n._valor; });
    g.arestas.forEach(function (a) { if (num(a.valor) > maxAresta) maxAresta = num(a.valor); });
    g.nos.forEach(function (n) {
      var base = cfg.raioBase ? cfg.raioBase(n) : 10;
      n.r = base + 11 * (maxNo ? Math.sqrt(n._valor / maxNo) : 0);
    });
    g.arestas.forEach(function (a) {
      a.largura = 1.2 + 5.5 * (maxAresta ? Math.sqrt(num(a.valor) / maxAresta) : 0);
    });
  }

  // ==========================================================================================
  // MODO DE EXIBIÇÃO — Resumido (padrão) x Detalhado (o comportamento histórico)
  //
  // O modo é SÓ representação: os dados, os filtros e as exportações não mudam. A página monta o
  // grafo detalhado como sempre e, no Resumido, passa por `resumir()` antes de entregar ao motor.
  var MODO = { RESUMIDO: "resumido", DETALHADO: "detalhado" };

  /* Liga o controle segmentado #gf-modo (Detalhado | Resumido) e devolve um getter do modo atual.
     Os dois botoes compartilham a mesma mecânica nas duas telas; o que muda (o que cada modo faz
     com os dados) fica na página, no callback `aoTrocar`. */
  function controleModo(aoTrocar) {
    var atual = MODO.RESUMIDO;
    var $c = $("#gf-modo");
    if (!$c.length) return function () { return atual; };
    function pintar() {
      $c.find(".gf-modo-opcao").each(function () {
        $(this).toggleClass("ativo", String($(this).attr("data-modo")) === atual);
      });
    }
    pintar();
    $c.on("click", ".gf-modo-opcao", function () {
      var m = String($(this).attr("data-modo") || "");
      if (m !== MODO.RESUMIDO && m !== MODO.DETALHADO) return;
      if (m === atual) return;
      atual = m;
      pintar();
      aoTrocar();
    });
    return function () { return atual; };
  }

  /* Grafo detalhado -> grafo de investigação.
   *
   * Duas transformações, nenhuma das duas apaga informação:
   *  1) nós TÉCNICOS (instituição, conta, comunicação) que são só intermediários saem do desenho —
   *     mas um nó técnico que é PONTA de algum fluxo (comunicação sem titular, por exemplo) fica,
   *     senão dinheiro sumiria da tela;
   *  2) arestas repetidas entre o mesmo par e na mesma direção viram UMA relação agregada, com
   *     valor somado, contagem de operações e período. Os originais ficam em `_originais` (com as
   *     pontas resolvidas para os OBJETOS de nó, que sobrevivem à reindexação) para a gaveta
   *     mostrar de que a soma é feita.
   *
   * Espessura ∝ log(1+R$): linear deixaria uma única operação gigante apagar o resto do grafo, e
   * até a raiz quadrada do modo detalhado ainda exagera demais quando uma relação vale 1000x outra.
   * Direção (crédito/débito) é preservada: o que se funde são repetições do MESMO sentido. */
  function resumir(g, opts) {
    if (!g || !g.nos || !g.nos.length) return g;
    opts = opts || {};
    var tec = {}, mata = {};
    (opts.tecnicoTipos || []).forEach(function (t) { tec[t] = true; });
    (opts.ocultarDirs || []).forEach(function (d) { mata[d] = true; });

    var fluxo = {};
    g.arestas.forEach(function (a) {
      if (mata[a.dir]) return;
      fluxo[a.i] = (fluxo[a.i] || 0) + 1;
      fluxo[a.j] = (fluxo[a.j] || 0) + 1;
    });
    var mapa = {}, nos = [];
    g.nos.forEach(function (n, i) {
      if (tec[n.tipo] && !fluxo[i]) return;
      mapa[i] = nos.length;
      nos.push(n);
    });

    var grupos = {}, arestas = [];
    g.arestas.forEach(function (a) {
      if (mata[a.dir]) return;
      if (mapa[a.i] === undefined || mapa[a.j] === undefined) return;
      var ii = mapa[a.i], jj = mapa[a.j];
      if (ii === jj) return;
      var chave = Math.min(ii, jj) + "|" + Math.max(ii, jj) + "|" + a.dir;
      var e = grupos[chave];
      if (!e) {
        e = grupos[chave] = { i: ii, j: jj, dir: a.dir, papel: a.papel, cor: a.cor,
                              valor: 0, nLanc: 0, semValor: true, dtPrimeiro: null, dtUltimo: null,
                              contas: 0, coms: {}, bancos: {}, _originais: [] };
        arestas.push(e);
      }
      e.valor += num(a.valor);
      e.nLanc += num(a.nLanc);
      if (e.valor > 0.005) e.semValor = false;
      e.contas += num(a.contas);
      if (a.dtPrimeiro && (!e.dtPrimeiro || a.dtPrimeiro < e.dtPrimeiro)) e.dtPrimeiro = a.dtPrimeiro;
      if (a.dtUltimo && (!e.dtUltimo || a.dtUltimo > e.dtUltimo)) e.dtUltimo = a.dtUltimo;
      ["coms", "bancos"].forEach(function (campo) {
        var origem = a[campo];
        if (!origem) return;
        Object.keys(origem).forEach(function (k) { e[campo][k] = true; });
      });
      e._originais.push({ aresta: a, de: g.nos[a.i], para: g.nos[a.j] });
    });

    // nó sem ligação agregada não tem mais para onde apontar: sai do desenho
    var grau = {};
    arestas.forEach(function (a) { grau[a.i] = (grau[a.i] || 0) + 1; grau[a.j] = (grau[a.j] || 0) + 1; });
    var finais = [], mapa2 = {};
    nos.forEach(function (n, i) { if (grau[i]) { mapa2[i] = finais.length; finais.push(n); } });
    arestas.forEach(function (a) { a.i = mapa2[a.i]; a.j = mapa2[a.j]; });
    finais.forEach(function (n, i) { n._posGrafo = i; });

    var maxA = 0;
    arestas.forEach(function (a) { if (a.valor > maxA) maxA = a.valor; });
    arestas.forEach(function (a) {
      a.largura = 1.5 + 8 * (maxA && !a.semValor ? Math.log(1 + a.valor) / Math.log(1 + maxA) : 0);
      if (_chaves(a.coms).length) a.nComs = _chaves(a.coms).length;
      a.rotulo = opts.rotulo ? opts.rotulo(a, abrevReais)
        : a.semValor ? (a.papel || "Vínculo")
        : (a.papel || "Fluxo") + " · " + abrevReais(a.valor) +
          (a.nLanc ? " · " + a.nLanc + (a.nLanc === 1 ? " operação" : " operações") : "");
    });

    return { nos: finais, arestas: arestas, totalNos: g.totalNos,
             resumido: true, arestasClicaveis: true };
  }
  function _chaves(o) { return Object.keys(o || {}); }

  /* Painel lateral de ACHADOS DE INVESTIGAÇÃO (#gf-kpis). Cada card é
       { rotulo: o que o achado é, valor: a entidade, nota: o número que a destaca, aoClicar }
     e o clique faz a página recortar o grafo para aquela entidade (filtro aplicado pela página).
     Quem calcula os achados é a página (só ela conhece os dados da fonte); o motor desenha,
     lembra se o painel está recolhido e dá o ponto único de estilo das duas telas. */
  function renderKPIs(lista) {
    var $c = $("#gf-kpis");
    if (!$c.length) return;
    var recolhido = false;
    try { recolhido = localStorage.getItem("gf-kpis-recolhido") === "1"; } catch (e) {}
    $c.empty().toggleClass("recolhido", recolhido);

    var $head = $("<div>").addClass("gf-kpis-head");
    $("<span>").addClass("gf-kpis-titulo").text("Achados de investigação").appendTo($head);
    var $t = $("<button>").attr({ type: "button", class: "gf-kpis-toggle" })
      .attr("title", recolhido ? "Mostrar achados" : "Ocultar achados")
      .text(recolhido ? "◀" : "▶")
      .on("click", function () {
        var agora = !$c.hasClass("recolhido");
        $c.toggleClass("recolhido", agora);
        try { localStorage.setItem("gf-kpis-recolhido", agora ? "1" : "0"); } catch (e) {}
        $t.text(agora ? "◀" : "▶").attr("title", agora ? "Mostrar achados" : "Ocultar achados");
      }).appendTo($head);
    $c.append($head);

    var $body = $("<div>").addClass("gf-kpis-body");
    if (!lista || !lista.length) {
      $("<p>").addClass("gf-kpis-vazio").text("Nada relevante com os filtros atuais.").appendTo($body);
    }
    (lista || []).forEach(function (k) {
      var $b = $("<button>").attr("type", "button").addClass("gf-kpi");
      if (k.aoClicar) {
        $b.attr("title", "Recortar o grafo para esta entidade").on("click", k.aoClicar);
      } else $b.prop("disabled", true);
      $("<span>").addClass("gf-kpi-rotulo").text(k.rotulo).appendTo($b);
      $("<span>").addClass("gf-kpi-valor").text(k.valor).appendTo($b);
      if (k.nota) $("<span>").addClass("gf-kpi-nota").text(k.nota).appendTo($b);
      $body.append($b);
    });
    $c.append($body);
  }

  // ==========================================================================================
  function criar(cfg) {
    var svg = document.getElementById("gf-grafo");
    var $drawer = $("#gf-drawer");
    var vb = { x: 0, y: 0, w: G_W, h: G_H };
    var grafo = null;            // grafo filtrado inteiro (o que a página entregou)
    var desenhado = null;        // subconjunto efetivamente na tela (ver limitarNos)
    var limiteNos = LIMITE_PADRAO;
    // Nós em foco, na ordem em que foram escolhidos. Guarda os OBJETOS de nó: eles sobrevivem a
    // um render (é `_posDesenho` que muda), e todo caminho que troca o grafo passa por
    // fecharDrawer, que zera isto. Um só = o detalhe de sempre; vários = Shift+clique.
    var focados = [];
    // Relação agregada aberta na gaveta (modo Resumido): foco nos dois nós-ponta e nela mesma.
    var arestaFocada = null;
    // Nó da ficha aberta na gaveta (nó direto, ou a origem de uma relação agregada) — é dele que
    // sai o link do botão "abrir ficha em nova aba".
    var noGaveta = null;

    var CORES = cfg.cores || {};
    var ROTULOS = cfg.rotulosTipo || {};
    var ICONES = cfg.icones || {};
    var COR_ARESTA = cfg.corAresta || {};
    var TRACEJADO = cfg.tracejado || {};
    var MARCADOR = cfg.marcador || {};

    // ---------------------------------------------------------------- limite de nós
    /* Três índices convivem no mesmo objeto de nó, e confundi-los já custou caro:
         _idx        posição no grafo BRUTO (antes dos filtros) — uso interno de quem monta;
         _posGrafo   posição em grafo.nos (grafo filtrado inteiro) — índice das arestas de
                     `grafo`, usado pela gaveta;
         _posDesenho posição no subconjunto desenhado — índice das arestas de `desenhado`,
                     usado pelo arraste.
       Reescrever _posGrafo com a posição no subconjunto (são os MESMOS objetos) fazia a lista de
       "Ligações" da gaveta mostrar as arestas de outro nó em todo grafo grande o bastante para
       ser cortado pelo limite. */
    function limitarNos(g, limite) {
      var nos;
      if (g.nos.length <= limite) {
        nos = g.nos.slice();
      } else {
        var grau = new Array(g.nos.length).fill(0);
        g.arestas.forEach(function (a) { grau[a.i]++; grau[a.j]++; });
        var ordem = g.nos.map(function (n, i) { return i; });
        ordem.sort(function (i, j) {
          var ni = g.nos[i], nj = g.nos[j];
          var pi = cfg.prioridade ? cfg.prioridade(ni) : 0;
          var pj = cfg.prioridade ? cfg.prioridade(nj) : 0;
          if (pi !== pj) return pj - pi;
          return grau[j] - grau[i];
        });
        nos = ordem.slice(0, limite).map(function (i) { return g.nos[i]; });
      }
      var pos = {};
      nos.forEach(function (n, k) { n._posDesenho = k; pos[n._posGrafo] = k; });
      var arestas = [];
      g.arestas.forEach(function (a) {
        if (pos[a.i] === undefined || pos[a.j] === undefined) return;
        var c = {}; for (var k in a) c[k] = a[k];
        c.i = pos[a.i]; c.j = pos[a.j];
        arestas.push(c);
      });
      return { nos: nos, arestas: arestas };
    }

    function mostrarInfoLimite(total, mostrados) {
      var $info = $("#gf-info-limite");
      if (mostrados >= total || (cfg.temFiltros && cfg.temFiltros())) { $info.hide(); return; }
      $info.find("span").first().text(
        "Mostrando " + mostrados.toLocaleString("pt-BR") + " de " + total.toLocaleString("pt-BR") +
        " nós (priorizando titulares, instituições e nós mais conectados) — o layout completo travaria o navegador."
      );
      $info.show();
    }

    // ---------------------------------------------------------------- layout
    function calcularLayout(nos, arestas) {
      if (!nos.length) return;
      // Cada iteração é O(n²). Com poucas centenas de nós as 500 iterações originais custam
      // pouco, mas no teto de 1.000 nós passavam de 20s de aba congelada — e os filtros
      // remontam o grafo sozinhos. O nº de iterações acompanha o tamanho: grafo pequeno/médio
      // mantém exatamente a qualidade de antes.
      var ITER = nos.length <= 300 ? 500 : nos.length <= 600 ? 320 : 200;
      var PASSES = nos.length <= 300 ? 500 : nos.length <= 600 ? 300 : 180;
      var area = areaLayout(nos.length);
      var LW = area.w, LH = area.h, cx = LW / 2, cy = LH / 2;
      var K = Math.sqrt((LW * LH) / nos.length) * 1.25;
      nos.forEach(function (n, i) {
        var ang = (2 * Math.PI * i) / nos.length, raio = Math.min(LW, LH) / 3;
        n.x = cx + Math.cos(ang) * raio; n.y = cy + Math.sin(ang) * raio;
      });
      var temp = LW / 10;
      for (var it = 0; it < ITER; it++) {
        nos.forEach(function (n) { n.dx = 0; n.dy = 0; });
        for (var i = 0; i < nos.length; i++) for (var j = i + 1; j < nos.length; j++) {
          // Math.hypot é ~10x mais lento que sqrt em V8 e as coordenadas aqui são limitadas,
          // então não há overflow que justifique o hypot
          var a = nos[i], b = nos[j], dx = a.x - b.x, dy = a.y - b.y;
          var d = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
          var f = (K * K) / d, ux = dx / d, uy = dy / d;
          a.dx += ux * f; a.dy += uy * f; b.dx -= ux * f; b.dy -= uy * f;
        }
        arestas.forEach(function (e) {
          var a = nos[e.i], b = nos[e.j], dx = a.x - b.x, dy = a.y - b.y;
          var d = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
          var f = (d * d) / K, ux = dx / d, uy = dy / d;
          a.dx -= ux * f; a.dy -= uy * f; b.dx += ux * f; b.dy += uy * f;
        });
        nos.forEach(function (n) {                       // gravidade no lugar do grampo na borda
          var gdx = cx - n.x, gdy = cy - n.y;
          var gd = Math.max(Math.sqrt(gdx * gdx + gdy * gdy), 0.01);
          var gf = ((gd * gd) / K) * GRAVIDADE;
          n.dx += (gdx / gd) * gf; n.dy += (gdy / gd) * gf;
        });
        nos.forEach(function (n) {
          var d = Math.max(Math.sqrt(n.dx * n.dx + n.dy * n.dy), 0.01), lim = Math.min(d, temp);
          n.x += (n.dx / d) * lim;
          n.y += (n.dy / d) * lim;
        });
        temp *= Math.pow(0.985, 500 / ITER);
      }

      // Passo anti-sobreposição por CAIXA (nó + rótulo): cada nó ocupa um retângulo cuja largura
      // considera o texto do rótulo, e pares que se sobrepõem são afastados pelo eixo de menor
      // penetração — lado a lado com rótulos largos isso os escalona no vertical, evitando que as
      // descrições fiquem uma sobre a outra.
      nos.forEach(function (n) {
        var chars = Math.min(String(n.rotulo || "").length, 14);
        n.hw = Math.max(n.r || G_RAIO, chars * 2.7) + 7;
        n.hh = (n.r || G_RAIO) + 13;
      });
      separarCaixas(nos, PASSES, function (a, b) {
        var dx = a.x - b.x, dy = a.y - b.y;
        var ox = (a.hw + b.hw) - Math.abs(dx), oy = (a.hh + b.hh) - Math.abs(dy);
        if (!(ox > 0 && oy > 0)) return false;
        if (ox < oy) { var sx = (dx === 0 ? 1 : (dx < 0 ? -1 : 1)) * ox / 2; a.x += sx; b.x -= sx; }
        else { var sy = (dy === 0 ? 1 : (dy < 0 ? -1 : 1)) * oy / 2; a.y += sy; b.y -= sy; }
        return true;
      });
    }

    /* Comparar todos os pares por passe (O(n²)) é o que domina o tempo do desenho, e é
       desperdício: a caixa de um item é minúscula perto da tela, então quase todo par está longe
       demais para se tocar. A grade espacial tem célula do tamanho da MAIOR caixa — assim dois
       itens que se sobrepõem estão obrigatoriamente na mesma célula ou numa vizinha, e basta
       olhar as 9. (No RIF de 1.700 arestas, o laço dos rótulos sozinho respondia por ~12s.) */
    function separarCaixas(itens, passes, separar) {
      var celW = 1, celH = 1;
      itens.forEach(function (n) {
        if (n.hw * 2 > celW) celW = n.hw * 2;
        if (n.hh * 2 > celH) celH = n.hh * 2;
      });
      for (var pass = 0; pass < passes; pass++) {
        var mexeu = false, grade = {};
        for (var p = 0; p < itens.length; p++) {
          var ck = Math.floor(itens[p].x / celW) + ":" + Math.floor(itens[p].y / celH);
          (grade[ck] = grade[ck] || []).push(p);
        }
        for (var i = 0; i < itens.length; i++) {
          var gx = Math.floor(itens[i].x / celW), gy = Math.floor(itens[i].y / celH);
          for (var ox = -1; ox <= 1; ox++) for (var oy = -1; oy <= 1; oy++) {
            var lista = grade[(gx + ox) + ":" + (gy + oy)];
            if (!lista) continue;
            for (var k = 0; k < lista.length; k++) {
              if (lista[k] <= i) continue;                 // cada par uma vez só
              if (separar(itens[i], itens[lista[k]])) mexeu = true;
            }
          }
        }
        if (!mexeu) break;
      }
    }

    function aplicarViewBox() { svg.setAttribute("viewBox", vb.x + " " + vb.y + " " + vb.w + " " + vb.h); }

    // enquadra o viewBox no conteúdo (com folga para os rótulos), independentemente do canvas
    function ajustarViewBox(nos) {
      var rmax = 0, minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
      nos.forEach(function (n) {
        rmax = Math.max(rmax, n.r || G_RAIO);
        minx = Math.min(minx, n.x); maxx = Math.max(maxx, n.x);
        miny = Math.min(miny, n.y); maxy = Math.max(maxy, n.y);
      });
      var padX = rmax + 95, padY = rmax + 55;
      vb = { x: minx - padX, y: miny - padY,
             w: Math.max((maxx - minx) + 2 * padX, 60), h: Math.max((maxy - miny) + 2 * padY, 60) };
      aplicarViewBox();
    }

    // ---------------------------------------------------------------- legenda
    function renderLegenda(tipos) {
      var $l = $("#gf-legenda").empty();
      tipos.forEach(function (tp) {
        var $i = $("<div>").addClass("mesa-legenda-item");
        $("<span>").addClass("mesa-legenda-bolinha com-icone").css("background", CORES[tp])
          .html('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
                'stroke-linecap="round" stroke-linejoin="round">' + (ICONES[tp] || "") + "</svg>")
          .appendTo($i);
        $("<span>").text(ROTULOS[tp] || tp).appendTo($i);
        $l.append($i);
      });
      (cfg.aneis || []).forEach(function (anel) {
        var $i = $("<div>").addClass("mesa-legenda-item");
        $("<span>").addClass("mesa-legenda-bolinha")
          .css({ background: "transparent", border: "3px solid " + anel.cor }).appendTo($i);
        $("<span>").text(anel.rotulo).appendTo($i);
        $l.append($i);
      });
      (cfg.legendaLinhas || []).forEach(function (par) {
        var $i = $("<div>").addClass("mesa-legenda-item");
        $("<span>").css({
          display: "inline-block", width: "26px", height: "0", marginRight: "6px", flex: "0 0 auto",
          borderTop: "3px " + (par[2] ? "dashed" : "solid") + " " + par[0]
        }).appendTo($i);
        $("<span>").text(par[1]).appendTo($i);
        $l.append($i);
      });
    }

    // ---------------------------------------------------------------- gaveta + foco
    /* O nó aberto na gaveta vira o FOCO: ele e os vizinhos diretos ficam nítidos, o resto do
       grafo desbota. Sem isso a gaveta seria só um modal encostado na lateral — num grafo com
       centenas de nós o que importa é enxergar QUAIS deles se ligam ao nó aberto.

       Com Shift o clique ACUMULA (ver alternarSelecao) e o foco vira a UNIÃO das vizinhanças:
       num RIF ou num extrato a pergunta que não se respondia com um nó de cada vez era "o que
       estes dois têm a ver um com o outro?" — quanto correu entre eles e quem eles pagam em
       comum. Comparar de memória, clicando num e depois no outro, não responde isso. */
    function aplicarFoco() {
      svg.classList.toggle("mesa-grafo-com-foco", !!((focados.length || arestaFocada) && desenhado));
      if (!desenhado) return;
      // Nó escolhido que ficou FORA do desenho (acima do limite) não tem o que acender; se
      // nenhum dos escolhidos está desenhado, é o mesmo que não haver foco.
      var alvo = {}, temAlvo = false;
      focados.forEach(function (n) {
        if (n._posDesenho === undefined) return;
        alvo[n._posDesenho] = true;
        temAlvo = true;
      });
      if (arestaFocada) {
        // relação agregada clicada: as duas pontas ficam nítidas junto com ela
        alvo[arestaFocada.i] = true;
        alvo[arestaFocada.j] = true;
        temAlvo = true;
      }
      if (!temAlvo) {
        desenhado.nos.forEach(function (n) { if (n._grupo) n._grupo.classList.remove("fora-do-foco", "no-focado"); });
        desenhado.arestas.forEach(function (a) {
          if (a._line) a._line.classList.remove("fora-do-foco");
          if (a._label) a._label.classList.remove("fora-do-foco");
        });
        svg.classList.remove("mesa-grafo-com-foco");
        return;
      }
      // vizinhança = UNIÃO das vizinhanças dos escolhidos
      var vizinhos = {};
      chaves(alvo).forEach(function (k) { vizinhos[k] = true; });
      desenhado.arestas.forEach(function (a) {
        if (alvo[a.i]) vizinhos[a.j] = true;
        if (alvo[a.j]) vizinhos[a.i] = true;
      });
      desenhado.nos.forEach(function (n) {
        if (!n._grupo) return;
        n._grupo.classList.toggle("fora-do-foco", !vizinhos[n._posDesenho]);
        n._grupo.classList.toggle("no-focado", !!alvo[n._posDesenho]);
      });
      desenhado.arestas.forEach(function (a) {
        var dentro = !!(alvo[a.i] || alvo[a.j]) || a === arestaFocada;
        if (a._line) a._line.classList.toggle("fora-do-foco", !dentro);
        if (a._label) a._label.classList.toggle("fora-do-foco", !dentro);
      });
    }

    // Clique simples troca a seleção; Shift+clique põe/tira o nó do conjunto (ver o pointerup).
    // `abrirNo` continua sendo a porta de entrada de fora (API e botões de ligação da gaveta).
    function abrirNo(no) {
      if (!grafo) return;
      arestaFocada = null;
      focados = [no];
      atualizarGaveta();
    }

    /* Clique numa aresta do modo Resumido: a gaveta mostra a relação agregada (total, operações,
       período e as ligações originais que a compõem). É o "clicar na relação" da especificação —
       o detalhamento continua a um clique de distância, não some com a agregação. */
    function abrirAresta(a) {
      if (!grafo || !a) return;
      focados = [];
      arestaFocada = a;
      atualizarGaveta();
    }

    function alternarSelecao(no) {
      if (!grafo) return;
      arestaFocada = null;
      var i = focados.indexOf(no);
      if (i !== -1) focados.splice(i, 1);
      else focados.push(no);
      if (!focados.length) fecharDrawer();
      else atualizarGaveta();
    }

    function atualizarGaveta() {
      if (arestaFocada) detalharAresta(arestaFocada);
      else if (focados.length === 1) detalharNo(focados[0]);
      else resumirSelecao();
      $drawer.removeClass("hidden");
      aplicarFoco();
      // o botão da ficha só existe para quem tem ficha (instituição/agregado não têm; a página
      // decide o que tem ficha devolvendo URL nula em cfg.fichaURL)
      $("#gf-drawer-nova").toggle(!!(noGaveta && cfg.fichaURL && cfg.fichaURL(noGaveta)));
    }

    // Linha de rodapé da gaveta: o Shift+clique não tem botão nem menu que o anuncie. Vai como
    // <dd> porque o corpo da gaveta é um <dl> — texto solto ali dentro não seria nem dt nem dd.
    function dicaSelecao($c, texto) {
      $("<dd>").addClass("field-help").text(texto).appendTo($c);
    }

    function detalharNo(no) {
      noGaveta = no;
      $("#gf-drawer-titulo").text(no.rotulo || "Nó");
      var $c = $("#gf-drawer-corpo").empty();
      (cfg.detalhes(no, grafo) || []).forEach(function (linha) {
        if (linha.nota) { $("<dd>").addClass("field-help").text(linha.nota).appendTo($c); return; }
        $("<dt>").text(linha[0]).appendTo($c);
        $("<dd>").text(linha[1] == null || linha[1] === "" ? "—" : linha[1]).appendTo($c);
      });

      // ligações usam a posição no grafo filtrado (o mesmo índice que as arestas carregam)
      var ligacoes = grafo.arestas.filter(function (a) { return a.i === no._posGrafo || a.j === no._posGrafo; });
      if (ligacoes.length) {
        $("<dt>").text("Ligações (" + ligacoes.length + ")").appendTo($c);
        var $d = $("<dd>").appendTo($c);
        ligacoes.slice(0, 60).forEach(function (a) {
          var souOrigem = a.i === no._posGrafo;
          var outro = grafo.nos[souOrigem ? a.j : a.i];
          // cada ligação leva o foco para o vizinho: é assim que se anda pelo grafo sem caçar o
          // nó no desenho (só vale para quem está desenhado — acima do limite o nó não está lá)
          var $b = $("<button>").attr("type", "button").addClass("gf-lig")
            .text(cfg.rotuloLigacao(a, outro, souOrigem));
          if (outro._desenhado) {
            $b.attr("title", "Focar " + outro.rotulo).on("click", function () { abrirNo(outro); });
          } else {
            $b.prop("disabled", true).attr("title", outro.rotulo + " está fora dos nós desenhados (ver “Carregar mais”)");
          }
          $d.append($b);
        });
        if (ligacoes.length > 60) {
          $("<div>").addClass("field-help").text("… e mais " + (ligacoes.length - 60) + ".").appendTo($d);
        }
      }

      // quantas dessas ligações estão de fato desenhadas: acima do limite de nós o foco não
      // consegue acender as que ficaram de fora, e dizer só "374 ligações" com 330 acesas mente
      var desenhadas = ligacoes.filter(function (a) {
        return grafo.nos[a.i]._desenhado && grafo.nos[a.j]._desenhado;
      }).length;
      $("#gf-drawer-sub").text(cfg.subtitulo(no, ligacoes.length, desenhadas));

      var $acoes = $("#gf-drawer-acoes").empty();
      (cfg.acoes ? cfg.acoes(no) : []).forEach(function (acao) {
        var $b = $("<a>").addClass("btn btn-secondary btn-small").text(acao.texto);
        if (acao.href) $b.attr({ href: acao.href, target: acao.alvo || "_self" });
        else $b.attr("href", "#").on("click", function (e) { e.preventDefault(); acao.aoClicar(); });
        $acoes.append($b);
      });

      dicaSelecao($c, "Shift+clique em outro nó para pôr os dois em foco ao mesmo tempo.");
    }

    /* Gaveta com dois ou mais nós escolhidos. Não existe "o detalhe" de vários, e empilhar a
       ficha de cada um não diria nada novo: o que a seleção conjunta responde é o que eles têm
       ENTRE si (quanto correu de um para o outro) e o que têm EM COMUM (a contraparte que
       aparece nos dois) — no RIF e no SIMBA é justamente aí que aparece o intermediário. */
    function resumirSelecao() {
      noGaveta = null;
      $("#gf-drawer-titulo").text(focados.length + " nós em foco");
      var $c = $("#gf-drawer-corpo").empty();
      var posFocada = {};
      focados.forEach(function (n) { posFocada[n._posGrafo] = true; });

      var emFoco = grafo.arestas.filter(function (a) { return posFocada[a.i] || posFocada[a.j]; });
      var entre = emFoco.filter(function (a) { return posFocada[a.i] && posFocada[a.j]; });
      var totalEntre = entre.reduce(function (t, a) { return t + (a.semValor ? 0 : num(a.valor)); }, 0);

      var sub = [emFoco.length + (emFoco.length === 1 ? " ligação em foco" : " ligações em foco")];
      if (totalEntre) sub.push(formatarReais(totalEntre) + " entre os selecionados");
      var foraDoDesenho = focados.filter(function (n) { return !n._desenhado; }).length;
      if (foraDoDesenho) sub.push(foraDoDesenho + " fora dos nós desenhados");
      $("#gf-drawer-sub").text(sub.join(" · "));

      $("<dt>").text("Selecionados").appendTo($c);
      var $sel = $("<dd>").appendTo($c);
      focados.slice().forEach(function (n) {
        var $item = $("<div>").addClass("mesa-relacao-item");
        $("<span>").addClass("gf-selecao-cor").css("background", CORES[n.tipo] || "#8696a0")
          .attr("title", ROTULOS[n.tipo] || n.tipo).appendTo($item);
        $("<span>").text(truncar(n.rotulo, 34) + (n._desenhado ? "" : " (fora do desenho)")).appendTo($item);
        $("<button>").attr("type", "button").addClass("mesa-relacao-btn").attr("title", "Ver só este").text("⦿")
          .on("click", function () { abrirNo(n); }).appendTo($item);
        $("<button>").attr("type", "button").addClass("mesa-relacao-btn").attr("title", "Tirar do foco").text("✕")
          .on("click", function () { alternarSelecao(n); }).appendTo($item);
        $sel.append($item);
      });

      $("<dt>").text("Ligações entre os selecionados (" + entre.length + ")").appendTo($c);
      var $entre = $("<dd>").appendTo($c);
      if (!entre.length) {
        $entre.addClass("field-help").text("Nenhuma ligação direta entre eles.");
      } else {
        entre.slice(0, 60).forEach(function (a) {
          $("<div>").addClass("gf-lig gf-lig-estatico")
            .text(truncar(grafo.nos[a.i].rotulo, 26) + " " + cfg.rotuloLigacao(a, grafo.nos[a.j], true))
            .appendTo($entre);
        });
        if (entre.length > 60) {
          $("<div>").addClass("field-help").text("… e mais " + (entre.length - 60) + ".").appendTo($entre);
        }
      }

      // quem se liga a mais de um dos escolhidos
      var tocadoPor = {};
      emFoco.forEach(function (a) {
        [[a.i, a.j], [a.j, a.i]].forEach(function (par) {
          if (!posFocada[par[0]] || posFocada[par[1]]) return;
          (tocadoPor[par[1]] = tocadoPor[par[1]] || {})[par[0]] = true;
        });
      });
      var comuns = chaves(tocadoPor)
        .filter(function (p) { return chaves(tocadoPor[p]).length > 1; })
        .sort(function (x, y) { return chaves(tocadoPor[y]).length - chaves(tocadoPor[x]).length; });
      $("<dt>").text("Contrapartes em comum (" + comuns.length + ")").appendTo($c);
      var $comuns = $("<dd>").appendTo($c);
      if (!comuns.length) {
        $comuns.addClass("field-help").text("Nenhum nó ligado a mais de um dos selecionados.");
      } else {
        comuns.slice(0, 60).forEach(function (p) {
          var n = grafo.nos[p];
          var $b = $("<button>").attr("type", "button").addClass("gf-lig")
            .text(truncar(n.rotulo, 30) + " — ligado a " + chaves(tocadoPor[p]).length + " deles");
          if (n._desenhado) {
            $b.attr("title", "Pôr " + n.rotulo + " em foco também")
              .on("click", function () { alternarSelecao(n); });
          } else {
            $b.prop("disabled", true).attr("title", n.rotulo + " está fora dos nós desenhados (ver “Carregar mais”)");
          }
          $comuns.append($b);
        });
        if (comuns.length > 60) {
          $("<div>").addClass("field-help").text("… e mais " + (comuns.length - 60) + ".").appendTo($comuns);
        }
      }

      var $acoes = $("#gf-drawer-acoes").empty();
      $("<a>").addClass("btn btn-secondary btn-small").attr("href", "#").text("✕ Limpar foco")
        .on("click", function (e) { e.preventDefault(); fecharDrawer(); }).appendTo($acoes);

      dicaSelecao($c, "Shift+clique põe ou tira um nó do foco; clique sem Shift recomeça a seleção.");
    }

    /* Gaveta de uma RELAÇÃO agregada (modo Resumido). O que a aresta mostra no desenho é só o
       total; aqui aparece a composição: cada ligação original que entrou na soma, com papel,
       valor, lançamentos, período e as evidências (bancos/comunicações/contas) — nada foi perdido
       na agregação, só dobrou de tamanho visual. */
    function detalharAresta(a) {
      var de = desenhado ? desenhado.nos[a.i] : grafo.nos[a.i];
      var para = desenhado ? desenhado.nos[a.j] : grafo.nos[a.j];
      // a ficha de uma relação é a ficha de quem origina o fluxo
      noGaveta = de || null;
      $("#gf-drawer-titulo").text("Relação — " + (de ? de.rotulo : "?") + " → " + (para ? para.rotulo : "?"));
      var $c = $("#gf-drawer-corpo").empty();
      function l(rot, val) {
        if (val == null || val === "") return;
        $("<dt>").text(rot).appendTo($c);
        $("<dd>").text(val).appendTo($c);
      }

      var orig = a._originais || [];
      l("Direção", cfg.rotuloRelacao ? cfg.rotuloRelacao(a) : (a.papel || "Fluxo"));
      l("Valor total", a.semValor ? "— (sem valor apurado)" : formatarReais(a.valor));
      l("Operações", a.nLanc ? String(a.nLanc) : null);
      l("Período", periodo(a.dtPrimeiro, a.dtUltimo));
      if (orig.length > 1) l("Ligações agregadas", String(orig.length));
      var bancos = chaves(a.bancos);
      if (bancos.length) l("Bancos", bancos.join(", "));
      var coms = chaves(a.coms);
      if (coms.length) l("Comunicações", coms.length + (coms.length ? " (nº " + coms.slice(0, 20).join(", ") + (coms.length > 20 ? "…" : "") + ")" : ""));
      if (a.contas) l("Contas envolvidas", String(a.contas));

      $("<dt>").text("Pontas da relação").appendTo($c);
      var $p = $("<dd>").appendTo($c);
      [de, para].forEach(function (n) {
        if (!n) return;
        var $item = $("<div>").addClass("mesa-relacao-item");
        $("<span>").addClass("gf-selecao-cor").css("background", CORES[n.tipo] || "#8696a0")
          .attr("title", ROTULOS[n.tipo] || n.tipo).appendTo($item);
        $("<span>").text(truncar(n.rotulo, 34)).appendTo($item);
        $("<button>").attr("type", "button").addClass("mesa-relacao-btn").attr("title", "Focar " + n.rotulo).text("⦿")
          .on("click", function () { abrirNo(n); }).appendTo($item);
        $p.append($item);
      });

      if (orig.length > 1) {
        $("<dt>").text("Operações que compõem a relação (" + orig.length + ")").appendTo($c);
        var $o = $("<dd>").appendTo($c);
        orig.slice(0, 60).forEach(function (o) {
          var e = o.aresta;
          var partes = [];
          if (e.rotulo) partes.push(e.rotulo);
          else if (e.papel) partes.push(e.papel);
          if (!e.semValor && (!e.rotulo || e.rotulo.indexOf(e.papel) === -1)) partes.push(formatarReais(e.valor));
          if (e.nLanc) partes.push(e.nLanc + " lanç.");
          var per = periodo(e.dtPrimeiro, e.dtUltimo);
          if (per) partes.push("de " + per);
          var eb = chaves(e.bancos);
          if (eb.length) partes.push(eb.join(", "));
          var ec = chaves(e.coms);
          if (ec.length) partes.push(ec.length + (ec.length === 1 ? " comunicação" : " comunicações"));
          var $item = $("<div>").addClass("mesa-relacao-item");
          $("<span>").addClass("gf-lig gf-lig-estatico").css("flex", "1 1 auto")
            .text(truncar(o.de.rotulo, 20) + " → " + truncar(o.para.rotulo, 20) +
                  (partes.length ? " — " + partes.join(" · ") : "")).appendTo($item);
          $o.append($item);
        });
        if (orig.length > 60) {
          $("<div>").addClass("field-help").text("… e mais " + (orig.length - 60) + ".").appendTo($o);
        }
      } else {
        $("<dd>").addClass("field-help").text(
          "Clique numa ponta para ver as evidências completas (comunicações, instituições, contas)."
        ).appendTo($c);
      }

      $("#gf-drawer-sub").text([
        (orig.length || 1) + (orig.length === 1 ? " ligação de origem" : " ligações de origem"),
        a.semValor ? "sem valor apurado" : formatarReais(a.valor)
      ].join(" · "));

      var $acoes = $("#gf-drawer-acoes").empty();
      $("<a>").addClass("btn btn-secondary btn-small").attr("href", "#").text("✕ Limpar foco")
        .on("click", function (e) { e.preventDefault(); fecharDrawer(); }).appendTo($acoes);
    }

    function fecharDrawer() {
      $drawer.addClass("hidden");
      focados = [];
      arestaFocada = null;
      noGaveta = null;
      aplicarFoco();
    }

    // ---------------------------------------------------------------- desenho
    function render() {
      var g = grafo;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      aplicarViewBox();
      var vazio = !g || !g.nos.length;
      $("#gf-vazio").toggle(!!vazio);
      if (vazio) { desenhado = null; $("#gf-info-limite").hide(); fecharDrawer(); return; }

      var sel = desenhado = limitarNos(g, limiteNos);
      // o desenho é refeito do zero: nenhum elemento do foco anterior sobrevive
      fecharDrawer();
      g.nos.forEach(function (n) { n._grupo = null; n._desenhado = false; });
      sel.nos.forEach(function (n) { n._desenhado = true; });
      mostrarInfoLimite(g.nos.length, sel.nos.length);
      calcularLayout(sel.nos, sel.arestas);
      ajustarViewBox(sel.nos);

      renderLegenda(chaves(CORES).filter(function (tp) {
        return g.nos.some(function (n) { return n.tipo === tp; });
      }));

      var defs = el("defs", {});
      var simbolos = chaves(ICONES).map(function (tp) {
        return '<symbol id="gf-icone-' + tp + '" viewBox="0 0 24 24">' + ICONES[tp] + "</symbol>";
      }).join("");
      function marcador(id, cor) {
        return '<marker id="' + id + '" markerWidth="9" markerHeight="9" refX="8" refY="4" ' +
          'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" fill="' + cor + '"></path></marker>';
      }
      // uma seta por marcador declarado, na cor da direção que a usa — antes eram duas setas
      // fixas (verde e vermelha) no código, e qualquer direção nova da página ficava sem ponta
      var setas = {};
      chaves(MARCADOR).forEach(function (dir) { setas[MARCADOR[dir]] = COR_ARESTA[dir] || "#8696a0"; });
      defs.innerHTML = chaves(setas).map(function (nome) {
        return marcador("gf-seta-" + nome, setas[nome]);
      }).join("") + simbolos;
      svg.appendChild(defs);

      // Fase 1: linhas (encurtadas até a borda do nó, com a seta visível) e coleta dos rótulos.
      // Pares com ida E volta (comum no extrato: A pagou B e B pagou A) recebem um deslocamento
      // perpendicular tirado da ordem CANÔNICA do par — usar o sentido da própria aresta faz as
      // duas inversões se cancelarem e as linhas caem exatamente uma sobre a outra.
      var multiplos = {};
      sel.arestas.forEach(function (e) {
        var k = Math.min(e.i, e.j) + "-" + Math.max(e.i, e.j);
        (multiplos[k] = multiplos[k] || []).push(e);
      });
      chaves(multiplos).forEach(function (k) {
        var lista = multiplos[k];
        if (lista.length < 2) { lista[0]._desloc = 0; return; }
        lista.forEach(function (e, n) { e._desloc = (n - (lista.length - 1) / 2) * 7; });
      });

      var rotulos = [];
      sel.arestas.forEach(function (e) {
        var a = sel.nos[e.i], b = sel.nos[e.j];
        var geo = geometriaAresta(a, b, e);
        var cor = e.cor || COR_ARESTA[e.dir] || "#8696a0";
        // Sem opacity inline: ela venceria o CSS e o foco (classe fora-do-foco) não
        // conseguiria apagar as arestas dos nós ocultos — ver #gf-grafo em grafo_fluxo.css.
        var attrs = {
          x1: geo.x1, y1: geo.y1, x2: geo.x2, y2: geo.y2, class: "mesa-aresta", stroke: cor,
          "stroke-width": (e.largura || 1.3).toFixed(2)
        };
        if (MARCADOR[e.dir]) attrs["marker-end"] = "url(#gf-seta-" + MARCADOR[e.dir] + ")";
        if (TRACEJADO[e.dir]) attrs["stroke-dasharray"] = "5,4";
        var linha = el("line", attrs);
        e._line = linha;
        svg.appendChild(linha);
        var texto = truncar(e.rotulo != null ? e.rotulo : "", 26);
        rotulos.push({ x: geo.rx, y: geo.ry, texto: texto, hw: texto.length * 2.7 + 3, hh: 7 });
      });

      // Fase 2: afasta rótulos sobrepostos, mas só até MAX_DESLOC da posição original — solto
      // demais, o rótulo deixa de pertencer visualmente à aresta dele.
      var MAX_DESLOC = 18;
      rotulos.forEach(function (l) { l.origX = l.x; l.origY = l.y; });
      separarCaixas(rotulos, 300, function (la, lb) {
        var rdy = la.y - lb.y;
        var ox = (la.hw + lb.hw) - Math.abs(la.x - lb.x), oy = (la.hh + lb.hh) - Math.abs(rdy);
        if (!(ox > 0 && oy > 0)) return false;
        var sy = (rdy === 0 ? 1 : (rdy < 0 ? -1 : 1)) * (oy / 2 + 0.5);
        var ya = la.y + sy, yb = lb.y - sy;
        if (Math.abs(ya - la.origY) > MAX_DESLOC || Math.abs(yb - lb.origY) > MAX_DESLOC) return false;
        la.y = ya; lb.y = yb;
        return true;
      });

      // Fase 3: rótulos já separados.
      rotulos.forEach(function (l, idx) {
        var t = el("text", { x: l.x, y: l.y, class: "mesa-aresta-rotulo" });
        t.textContent = l.texto;
        svg.appendChild(t);
        sel.arestas[idx]._label = t;
      });

      // Fase 3b (só no modo Resumido): uma linha invisível e grossa por cima de cada aresta, para
      // a relação agregada ter alvo de clique — a linha visível de 2px é difícil de acertar.
      if (g.arestasClicaveis) {
        sel.arestas.forEach(function (e) {
          var geo = geometriaAresta(sel.nos[e.i], sel.nos[e.j], e);
          var hit = el("line", {
            x1: geo.x1, y1: geo.y1, x2: geo.x2, y2: geo.y2, class: "mesa-aresta-hit",
            stroke: "transparent", "stroke-width": Math.max(14, (e.largura || 1.3) + 12).toFixed(1)
          });
          hit.__aresta = e;
          e._hit = hit;
          svg.appendChild(hit);
        });
      }

      sel.nos.forEach(function (no) {
        var grupo = el("g", { class: "mesa-no-grupo" });
        var r = no.r || G_RAIO;
        // anéis (titular, PEP…) — o de fora é um círculo próprio, para poder coexistir com o
        // stroke do círculo principal
        (cfg.aneis || []).forEach(function (anel, k) {
          if (!anel.quando(no) || !anel.externo) return;
          var c = el("circle", { cx: no.x, cy: no.y, r: (r + 4).toFixed(1), class: anel.classe });
          no._anel = c;
          grupo.appendChild(c);
        });
        var classes = "mesa-no";
        (cfg.aneis || []).forEach(function (anel) {
          if (!anel.externo && anel.quando(no)) classes += " " + anel.classe;
        });
        var circ = el("circle", {
          cx: no.x, cy: no.y, r: r.toFixed(1), class: classes, fill: CORES[no.tipo] || "#8696a0"
        });
        circ.__no = no;
        no._circ = circ;
        var titulo = document.createElementNS(SVG_NS, "title");
        titulo.textContent = (no.rotulo || "") + (no.doc ? " · " + no.doc : "");
        circ.appendChild(titulo);
        grupo.appendChild(circ);
        var lado = r * 1.15;
        var icone = el("use", {
          href: "#gf-icone-" + no.tipo, x: no.x - lado / 2, y: no.y - lado / 2,
          width: lado, height: lado, class: "mesa-no-icone"
        });
        no._icone = icone;
        grupo.appendChild(icone);
        var rot = el("text", { x: no.x, y: no.y + r + 12, class: "mesa-no-rotulo" });
        rot.textContent = truncar(no.rotulo, cfg.tamanhoRotulo ? cfg.tamanhoRotulo(no) : 16);
        no._rotulo = rot;
        grupo.appendChild(rot);
        no._grupo = grupo;
        svg.appendChild(grupo);
      });

      if (cfg.aoRenderizar) cfg.aoRenderizar(g, sel);
    }

    function geometriaAresta(a, b, e) {
      var dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      var ux = dx / len, uy = dy / len;
      // perpendicular pela ordem canônica do par (ver comentário do _desloc)
      var sinal = e.i < e.j ? 1 : -1;
      var px = -uy * (e._desloc || 0) * sinal, py = ux * (e._desloc || 0) * sinal;
      var ra = a.r || G_RAIO, rb = b.r || G_RAIO;
      var frac = cfg.fracRotulo ? cfg.fracRotulo(a) : 0.4;
      return {
        x1: a.x + ux * ra + px, y1: a.y + uy * ra + py,
        x2: b.x - ux * (rb + 6) + px, y2: b.y - uy * (rb + 6) + py,
        rx: a.x + dx * frac + px, ry: a.y + dy * frac + py - 4
      };
    }

    // ---------------------------------------------------------------- zoom / pan / arraste
    function pontoSvg(evt) {
      var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      var ctm = svg.getScreenCTM(); if (!ctm) return { x: 0, y: 0 };
      var p = pt.matrixTransform(ctm.inverse()); return { x: p.x, y: p.y };
    }
    svg.addEventListener("wheel", function (evt) {
      evt.preventDefault();
      var fator = evt.deltaY < 0 ? 1 / 1.15 : 1.15;
      var novoW = Math.min(Math.max(vb.w * fator, G_W / 8), G_W * 8), escala = novoW / vb.w, p = pontoSvg(evt);
      vb.x = p.x - (p.x - vb.x) * escala; vb.y = p.y - (p.y - vb.y) * escala;
      vb.w = novoW; vb.h = vb.h * escala; aplicarViewBox();
    }, { passive: false });

    var drag = null;
    svg.addEventListener("pointerdown", function (evt) {
      var p = pontoSvg(evt);
      if (evt.target.__no) drag = { tipo: "no", no: evt.target.__no, x0: p.x, y0: p.y, moveu: false };
      else if (evt.target.__aresta)
        // arrasta como pan, mas um clique sem mover abre a relação agregada
        drag = { tipo: "pan", x0: evt.clientX, y0: evt.clientY, vb0: { x: vb.x, y: vb.y },
                 aresta: evt.target.__aresta, moveu: false };
      else drag = { tipo: "pan", x0: evt.clientX, y0: evt.clientY, vb0: { x: vb.x, y: vb.y }, moveu: false };
      svg.setPointerCapture(evt.pointerId);
    });
    svg.addEventListener("pointermove", function (evt) {
      if (!drag) return;
      if (drag.tipo === "pan") {
        var escala = vb.w / (svg.clientWidth || G_W);
        if (Math.abs(evt.clientX - drag.x0) + Math.abs(evt.clientY - drag.y0) > 3) drag.moveu = true;
        vb.x = drag.vb0.x - (evt.clientX - drag.x0) * escala;
        vb.y = drag.vb0.y - (evt.clientY - drag.y0) * escala;
        aplicarViewBox();
      } else {
        var p = pontoSvg(evt);
        if (Math.hypot(p.x - drag.x0, p.y - drag.y0) > 3) drag.moveu = true;
        if (drag.moveu) moverNo(drag.no, p.x, p.y);
      }
    });
    svg.addEventListener("pointerup", function (evt) {
      if (!drag) return;
      var d = drag; drag = null;
      svg.releasePointerCapture(evt.pointerId);
      if (d.tipo === "no" && !d.moveu) {
        if (evt.shiftKey) alternarSelecao(d.no);
        else abrirNo(d.no);
      }
      // clicar no vazio sai do foco (a gaveta não tem overlay para clicar fora);
      // clicar numa aresta clicável (modo Resumido) abre a relação agregada
      else if (d.tipo === "pan" && !d.moveu) {
        if (d.aresta) abrirAresta(d.aresta);
        else fecharDrawer();
      }
    });

    function moverNo(no, x, y) {
      no.x = x; no.y = y;
      var r = no.r || G_RAIO;
      no._circ.setAttribute("cx", x); no._circ.setAttribute("cy", y);
      if (no._anel) { no._anel.setAttribute("cx", x); no._anel.setAttribute("cy", y); }
      var lado = r * 1.15;
      no._icone.setAttribute("x", x - lado / 2); no._icone.setAttribute("y", y - lado / 2);
      no._rotulo.setAttribute("x", x); no._rotulo.setAttribute("y", y + r + 12);
      if (!desenhado) return;
      for (var k = 0; k < desenhado.arestas.length; k++) {
        var e = desenhado.arestas[k];
        if (e.i !== no._posDesenho && e.j !== no._posDesenho) continue;
        var geo = geometriaAresta(desenhado.nos[e.i], desenhado.nos[e.j], e);
        e._line.setAttribute("x1", geo.x1); e._line.setAttribute("y1", geo.y1);
        e._line.setAttribute("x2", geo.x2); e._line.setAttribute("y2", geo.y2);
        if (e._hit) {
          e._hit.setAttribute("x1", geo.x1); e._hit.setAttribute("y1", geo.y1);
          e._hit.setAttribute("x2", geo.x2); e._hit.setAttribute("y2", geo.y2);
        }
        if (e._label) { e._label.setAttribute("x", geo.rx); e._label.setAttribute("y", geo.ry); }
      }
    }

    $("#gf-drawer-fechar").on("click", fecharDrawer);

    /* Cabeçalho da gaveta: ⤢ expande/recolhe no lugar (a preferência sobrevive à troca de nó e
       de recarregamento) e ↗ abre a FICHA da entidade em nova aba — a URL quem monta é a página
       (cfg.fichaURL), porque só ela sabe onde buscam-se os detalhes daquela fonte. */
    try {
      if (localStorage.getItem("gf-drawer-larga") === "1") $drawer.addClass("gf-drawer-larga");
    } catch (e) { /* modo privado: só não lembra da próxima vez */ }
    $("#gf-drawer-expandir").on("click", function () {
      var larga = $drawer.toggleClass("gf-drawer-larga").hasClass("gf-drawer-larga");
      try { localStorage.setItem("gf-drawer-larga", larga ? "1" : "0"); } catch (e) {}
    });
    $("#gf-drawer-nova").on("click", function () {
      if (!noGaveta || !cfg.fichaURL) return;
      var url = cfg.fichaURL(noGaveta);
      if (url) window.open(url, "_blank");
    });
    $(document).on("keydown", function (e) { if (e.key === "Escape") fecharDrawer(); });
    $("#gf-carregar-mais").on("click", function () {
      if (cfg.temFiltros && cfg.temFiltros()) return;
      limiteNos += LIMITE_PADRAO;
      render();
    });

    /* PNG do desenho — ao contrário da exportação para i2, esta sai do que está NA TELA (é uma
       figura, não uma base de dados): o recorte de nós, o foco aceso e a posição dos nós que o
       usuário arrastou vão todos para a imagem. O subtítulo diz o tamanho do recorte justamente
       para a figura não passar por "o grafo inteiro" quando o limite cortou nós. */
    function exportarPNG(extra) {
      if (!window.ExportarPNG) { _aviso("Exportação de PNG não disponível nesta tela."); return; }
      if (!grafo || !grafo.nos.length || !desenhado) { _aviso("Grafo vazio. Nada para exportar."); return; }
      var n = function (v) { return v.toLocaleString("pt-BR"); };
      var sub = [
        n(desenhado.nos.length) + " nós · " + n(desenhado.arestas.length) + " ligações" +
        (desenhado.nos.length < grafo.nos.length
          ? " — recorte de " + n(grafo.nos.length) + " nós (ver “Carregar mais”)" : "")
      ];
      var resumo = $.trim($("#f-grafo-info").text() || "");
      if (resumo) sub.push(resumo);
      sub.push("Exportado em " + new Date().toLocaleString("pt-BR"));
      return window.ExportarPNG.doGrafo(svg, $.extend({
        prefixo: "grafo",
        legendas: ["#gf-legenda"],
        subtitulo: sub
      }, extra || {}));
    }

    // ---------------------------------------------------------------- API
    return {
      definir: function (g) { grafo = g; },
      dados: function () { return grafo; },
      desenhado: function () { return desenhado; },
      render: render,
      exportarPNG: exportarPNG,
      fecharDrawer: fecharDrawer,
      abrirNo: abrirNo,
      redefinirLimite: function () {
        limiteNos = (cfg.temFiltros && cfg.temFiltros()) ? Infinity : LIMITE_PADRAO;
      }
    };
  }

  // ============================================================== EXPORTAÇÃO i2
  // O recorte que as duas telas mandam para o i2 fica em js/utils/exportar_i2.js, compartilhado
  // com a mesa de inquérito, o grafo do caso, a convergência e as localizações. Aqui só se monta
  // o recorte: o grafo FILTRADO INTEIRO, nunca o desenhado. O teto de nós existe só porque o
  // layout trava o navegador; a exportação não desenha nada, então não tem por que cortar.
  //
  // A chave de cada entidade é o ÍNDICE dela em `g.nos` — que é exatamente como as arestas
  // apontam as pontas (`e.i` / `e.j`).
  //
  // `cfg.atributosNo` / `cfg.atributosVinculo` são o que cada tela sabe e o desenho não mostra:
  // documento, valores por sentido, período, lançamentos, bancos. Ficam na página, e não aqui,
  // porque RIF e SIMBA guardam coisas diferentes no nó (comunicação x conta afastada).
  // Tipo do vínculo no i2 = a DIREÇÃO do fluxo, que na tela é justamente o que a cor da linha
  // diz. Sem isso a carta sai com um tipo de ligação só e "recebeu de" e "pagou para" — a
  // leitura que importa num grafo financeiro — ficam indistinguíveis.
  var TIPO_I2_POR_DIR = {
    credito: "Crédito",
    debito: "Débito",
    "vinculo-credito": "Crédito (pelo papel declarado)",
    "vinculo-debito": "Débito (pelo papel declarado)",
    interna: "Transferência entre contas do titular",
    conta: "Conta na instituição",
    cotitular: "Cotitularidade",
    vinculo: "Vínculo declarado"
  };

  function _recorteI2(g, cfg) {
    if (!g || !g.nos || !g.nos.length) return null;

    function rotuloI2(no) { return (cfg.rotulo ? cfg.rotulo(no, g) : no.rotulo) || ""; }

    var entidades = g.nos.map(function (no, idx) {
      return {
        chave: idx,
        rotulo: rotuloI2(no),
        tipo: (cfg.tipos || {})[no.tipo] || no.tipo,
        peso: cfg.peso ? cfg.peso(no) : (no._valor || 0),
        atributos: cfg.atributosNo ? cfg.atributosNo(no, g) : null
      };
    });

    var vinculos = g.arestas.map(function (e) {
      var valor = num(e.valor);
      var sufixo = /credito$/i.test(e.dir || "") ? " (credito)"
                 : /debito$/i.test(e.dir || "") ? " (debito)" : "";
      return {
        de: e.i, para: e.j,
        tipo: TIPO_I2_POR_DIR[e.dir] || "Vínculo",
        rotulo: e.papel || "vinculo",
        peso: valor,
        simetrico: false,
        resumo: (e.papel || "Vinculo") + (e.semValor ? "" : " · " + formatarReais(valor)) + sufixo,
        atributos: cfg.atributosVinculo ? cfg.atributosVinculo(e, g) : null
      };
    });

    return { prefixo: cfg.prefixo || "grafo", entidades: entidades, vinculos: vinculos };
  }

  window.GrafoFluxo = {
    criar: criar,
    escalar: escalar,
    resumir: resumir,
    controleModo: controleModo,
    renderKPIs: renderKPIs,
    MODO: MODO,
    LIMITE_PADRAO: LIMITE_PADRAO,
    // exportação para i2 — sempre sobre o grafo filtrado inteiro, nunca sobre o recorte desenhado
    exportarCSV: function (g, cfg) { ExportarI2.csv(_recorteI2(g, cfg)); },
    exportarXLSX: function (g, cfg) { ExportarI2.xlsx(_recorteI2(g, cfg)); },
    exportarANX: function (g, cfg) { ExportarI2.anx(_recorteI2(g, cfg)); },
    exportarGraphML: function (g, cfg) { ExportarI2.graphml(_recorteI2(g, cfg)); },
    util: {
      num: num, soDigitos: soDigitos, norm: norm, chaves: chaves, truncar: truncar,
      formatarReais: formatarReais, abrevReais: abrevReais, formatarData: formatarData,
      periodo: periodo,
      parseValorBR: parseValorBR, aplicarMascaraMonetaria: aplicarMascaraMonetaria
    }
  };
})();
