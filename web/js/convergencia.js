/* Página de Convergência entre inquéritos: cruza sinais entre inquéritos visíveis ao
 * usuário e mostra os pares que convergem — do exato (CPF, telefone, e-mail, chave PIX,
 * IMEI) ao aproximado (nome parecido por trigrama, local de GPS em comum) — o tipo de
 * alerta que só um sistema faz. Duas visões dos mesmos dados: lista (para ler os detalhes)
 * e grafo (para ver a forma da rede entre os casos).
 *
 * Restrição por caso não muda: o usuário só vê inquéritos aos quais já tem acesso.
 *
 * Página server-side desde a medição que achou a causa da tela "pesada": a consulta de
 * cruzamento leva >100 s no banco e a base inteira no navegador travava a tela. O cálculo
 * pesado roda num laço de fundo (api/funcoes/canonicas_utils.py, cache de 1 h) e esta tela
 * pede ao servidor UMA página de pares / as arestas do grafo / as linhas do Excel, com os
 * filtros aplicados no SQL — nada aqui conhece a base inteira. */
(function () {
  "use strict";

  var $lista = $("#convergencia-lista");
  var $paginacao = $("#convergencia-paginacao");
  var $grafoArea = $("#convergencia-grafo-area");
  var $totais = $("#convergencia-totais");
  var $busca = $("#convergencia-busca");
  var $filtroTipo = $("#filtro-tipo");
  var $filtroConfianca = $("#filtro-confianca");
  var $filtroMinSinais = $("#filtro-min-sinais");
  var $filtroMinScore = $("#filtro-min-score");
  var $filtroOrdem = $("#filtro-ordem");
  var $filtroDelegacia = $("#convergencia-delegacia-select");
  var $atualizarBtn = $("#atualizar-btn");
  var $visaoListaBtn = $("#visao-lista-btn");
  var $visaoGrafoBtn = $("#visao-grafo-btn");
  var $legenda = $("#mesa-legenda");
  var $vazioGrafo = $("#mesa-vazio");
  var $modalPar = $("#modal-par");
  var $parTitulo = $("#par-titulo");
  var $parCorpo = $("#par-corpo");
  var svg = document.getElementById("mesa-grafo");

  var CONV_API = "/api/investigacao/convergencia";

  // PARES_POR_PAGINA: quantos CARDS de par cabem numa página da lista. SINAIS_POR_PAGINA_MODAL:
  // a mesma marca de antes, mas quem pagina agora é o servidor (/convergencia/par).
  var PARES_POR_PAGINA = 10;
  var SINAIS_POR_PAGINA_MODAL = 50;

  var esc = window.AnalyserUtils.escaparHtml;

  var visaoAtual = "lista";
  var paginaAtual = 1;
  var ultimaLista = null;      // última resposta da listagem (para o total não sumir no grafo)
  var esperaCacheTimer = null; // re-tentativa automática enquanto o cache nasce

  // Espelha _ROTULO_IDENTIFICADOR de api/funcoes/canonicas_utils.py — o que faltar aqui cai no
  // código cru do sinal na tela ("iccid", "co_presenca"), que é o que acontecia antes.
  var ROTULO_TIPO = {
    cpf: "CPF",
    cnpj: "CNPJ",
    telefone: "Telefone",
    email: "E-mail",
    nome: "Nome aproximado",
    imei: "IMEI",
    iccid: "ICCID do chip",
    imsi: "IMSI do chip",
    serial: "Nº de série do aparelho",
    mac: "MAC do aparelho",
    placa: "Placa de veículo",
    local: "Local em comum",
    local_rede: "Ponto de rede em comum",
    co_presenca: "Aparelho em comum por perto",
  };

  function formatarNumero(n) {
    return Number(n || 0).toLocaleString("pt-BR");
  }

  function formatarScore(v) {
    return v === null || v === undefined ? "" : Math.round(v * 100) + "%";
  }

  function badgeConfianca(conv) {
    if (conv.tp_confianca === "exata") {
      return '<span class="badge badge-ok">exata</span>';
    }
    var score = formatarScore(conv.vl_score);
    return '<span class="badge badge-aviso">aproximada' + (score ? " · " + score : "") + "</span>";
  }

  /* Um só lugar para montar os query params dos filtros — listagem, arestas do grafo, modal e
   * exportação saem exatamente do mesmo recorte, e "o que eu exportei ≠ o que eu vi" não tem
   * mais como acontecer (antes cada lado refiltrava os pares no navegador). */
  function filtrosQuery() {
    var q = {
      delegacia: $filtroDelegacia.val() || "",
      tipo: $filtroTipo.val() || "",
      confianca: $filtroConfianca.val() || "",
      busca: ($busca.val() || "").trim(),
      min_sinais: $filtroMinSinais.val() || "0",
      min_score: ($filtroMinScore.val() || "").trim(),
    };
    // "" num parâmetro numérico da API é 422 na validação do FastAPI — filtro vazio é filtro
    // ausente, e é assim que ele tem de chegar.
    if (q.min_score === "") delete q.min_score;
    return q;
  }

  function montarTabelaPar(par, sinais) {
    var $table = $("<table>").addClass("grupo-tabela").css("margin-top", "8px");
    var $thead = $("<thead>").appendTo($table);
    $("<tr>").append(
      "<td class='col-nome'>Tipo</td>",
      "<td class='col-nome'>Confiança</td>",
      "<td class='col-nome'>Valor</td>",
      "<td class='col-nome'>Entidade em " + esc(par.nu_inquerito_a) + "</td>",
      "<td class='col-nome'>Entidade em " + esc(par.nu_inquerito_b) + "</td>"
    ).appendTo($thead);

    var $tbody = $("<tbody>").appendTo($table);
    sinais.forEach(function (c) {
      $("<tr>").append(
        "<td>" + esc(ROTULO_TIPO[c.tp_identificador] || c.tp_identificador) + "</td>",
        "<td>" + badgeConfianca(c) + "</td>",
        "<td><code>" + esc(c.co_valor) + "</code></td>",
        "<td>" + esc(c.no_entidade_a || "—") + "</td>",
        "<td>" + esc(c.no_entidade_b || "—") + "</td>"
      ).appendTo($tbody);
    });
    return $table;
  }

  function montarCabecalhoPar(par) {
    return (
      '<a href="investigacao.html?inquerito=' + encodeURIComponent(par.id_inquerito_a) + '" style="color:var(--text-primary);">' +
      esc(par.nu_inquerito_a) + '</a>' +
      ' <span style="opacity:0.4;">⟷</span> ' +
      '<a href="investigacao.html?inquerito=' + encodeURIComponent(par.id_inquerito_b) + '" style="color:var(--text-primary);">' +
      esc(par.nu_inquerito_b) + '</a>'
    );
  }

  /* Paginação padrão da casa (mesma marcação de investigacao.html): "Mostrando x–y de N" à
   * esquerda, botões à direita. `aoTrocar` recebe a nova página. */
  function renderPaginacao($alvo, pagina, total, porPagina, rotulo, aoTrocar) {
    $alvo.empty();
    if (!total) {
      $alvo.addClass("hidden");
      return;
    }
    $alvo.removeClass("hidden");

    var totalPaginas = Math.max(1, Math.ceil(total / porPagina));
    var de = (pagina - 1) * porPagina + 1;
    var ate = Math.min(pagina * porPagina, total);

    $("<span>")
      .addClass("paginacao-info")
      .text("Mostrando " + formatarNumero(de) + "–" + formatarNumero(ate) + " de " + formatarNumero(total) + " " + rotulo)
      .appendTo($alvo);

    var $ctrls = $("<div>").addClass("paginacao-ctrls").appendTo($alvo);
    $("<button>")
      .addClass("btn btn-secondary paginacao-btn")
      .prop("disabled", pagina <= 1)
      .html("‹ Anterior")
      .on("click", function () { aoTrocar(pagina - 1); })
      .appendTo($ctrls);
    $("<span>")
      .addClass("paginacao-atual")
      .text("Página " + formatarNumero(pagina) + " de " + formatarNumero(totalPaginas))
      .appendTo($ctrls);
    $("<button>")
      .addClass("btn btn-secondary paginacao-btn")
      .prop("disabled", pagina >= totalPaginas)
      .html("Próxima ›")
      .on("click", function () { aoTrocar(pagina + 1); })
      .appendTo($ctrls);
  }

  // ---------------- modal de um par (todos os sinais, busca e página no servidor) ----------------

  var modalParAtual = null;
  var modalPagina = 1;
  var modalTermo = "";
  var $modalTabela = null;
  var $modalPaginacao = null;
  var modalBuscaTimer = null;

  function renderModalTabela() {
    var q = {
      id_a: modalParAtual.id_inquerito_a,
      id_b: modalParAtual.id_inquerito_b,
      busca: modalTermo.trim(),
      pagina: modalPagina,
      tamanho: SINAIS_POR_PAGINA_MODAL,
    };
    $modalTabela.html('<div style="display:flex; align-items:center; gap:8px; padding:12px 0;"><span class="spinner"></span> <span>Carregando…</span></div>');
    $.getJSON(CONV_API + "/par", q)
      .done(function (r) {
        $modalTabela.empty();
        if (!r.sinais.length) {
          $modalTabela.html('<p class="empty-hint">Nenhum sinal com esse termo.</p>');
        } else {
          montarTabelaPar(modalParAtual, r.sinais).appendTo($modalTabela);
        }
        renderPaginacao($modalPaginacao, r.pagina, r.total, SINAIS_POR_PAGINA_MODAL, "sinal(is)", function (nova) {
          modalPagina = nova;
          renderModalTabela();
          $modalTabela.closest(".modal-body").scrollTop(0);
        });
      })
      .fail(function () {
        $modalTabela.html('<p class="empty-hint" style="color:var(--danger);">Erro ao carregar os sinais do par.</p>');
      });
  }

  function abrirModalPar(par) {
    modalParAtual = {
      id_inquerito_a: par.id_inquerito_a != null ? par.id_inquerito_a : par.id_a,
      id_inquerito_b: par.id_inquerito_b != null ? par.id_inquerito_b : par.id_b,
      nu_inquerito_a: par.nu_inquerito_a != null ? par.nu_inquerito_a : par.nu_a,
      nu_inquerito_b: par.nu_inquerito_b != null ? par.nu_inquerito_b : par.nu_b,
    };
    modalPagina = 1;
    modalTermo = "";

    $parTitulo.html(montarCabecalhoPar(modalParAtual));
    $parCorpo.empty();

    // busca só aparece quando há sinal suficiente pra valer a pena procurar
    if ((par.qt_sinais || 0) > SINAIS_POR_PAGINA_MODAL) {
      var $barra = $("<div>").addClass("investigacoes-toolbar").css("margin", "0 0 10px").appendTo($parCorpo);
      var $wrap = $("<div>").addClass("inv-busca").appendTo($barra);
      $('<input type="search" autocomplete="off" placeholder="Buscar sinal por valor, entidade ou tipo…">')
        .on("input", function () {
          var v = $(this).val();
          clearTimeout(modalBuscaTimer);
          modalBuscaTimer = setTimeout(function () {
            modalTermo = v;
            modalPagina = 1;
            renderModalTabela();
          }, 250);
        })
        .appendTo($wrap);
    }

    $modalTabela = $("<div>").appendTo($parCorpo);
    $modalPaginacao = $("<div>").addClass("paginacao").appendTo($parCorpo);

    renderModalTabela();
    $modalPar.removeClass("hidden");
  }

  // ---------------- visão em lista ----------------

  function renderLista(resposta) {
    var pares = resposta.pares || [];
    if (!pares.length) {
      $lista.html(resposta.recalculando
        ? '<p class="empty-hint"><span class="spinner"></span> Primeiro cálculo dos sinais em andamento — a lista aparece sozinha em um minuto.</p>'
        : '<p class="empty-hint">Nenhum par com o filtro ou a busca selecionados.</p>');
      $paginacao.empty().addClass("hidden");
      return;
    }

    var $html = $("<div>");
    pares.forEach(function (par) {
      var $card = $("<div>").addClass("card").css({ "max-width": "none", "margin-bottom": "12px" });
      var $header = $("<div>").addClass("grupo-header").appendTo($card);
      $("<h3>").html(montarCabecalhoPar(par)).appendTo($header);
      $("<span>").addClass("badge").text(formatarNumero(par.qt_sinais) + " sinal(is)").appendTo($header);

      // O servidor já devolve a prévia ordenada (exatos primeiro) — pôr os ~1000 sinais de um
      // par grande no DOM trava a tela, o resto fica no modal, que pagina e tem busca.
      montarTabelaPar(par, par.convergencias).appendTo($card);

      if (par.qt_sinais > par.convergencias.length) {
        var $rodape = $("<div>")
          .css({ display: "flex", "align-items": "center", "justify-content": "space-between", gap: "12px", "flex-wrap": "wrap", "margin-top": "10px" })
          .appendTo($card);
        $("<span>").addClass("paginacao-info")
          .text("Mostrando " + par.convergencias.length + " de " + formatarNumero(par.qt_sinais) + " sinal(is)")
          .appendTo($rodape);
        $("<button>").attr("type", "button").addClass("btn btn-secondary paginacao-btn")
          .text("Ver todos os sinais")
          .on("click", function () { abrirModalPar(par); })
          .appendTo($rodape);
      }

      $html.append($card);
    });

    $lista.empty().append($html.children());
    renderPaginacao($paginacao, resposta.pagina, resposta.totais.pares, PARES_POR_PAGINA, "par(es)", function (nova) {
      paginaAtual = nova;
      carregarLista();
      $lista[0].scrollIntoView({ block: "start" });
    });
  }

  // ---------------- visão em grafo ----------------
  /* O quadro é a mesma rede da lista, desenhada com o kit da Mesa de Inquérito: layout
   * Fruchterman-Reingold no Web Worker (a página de convergência costuma passar de 300 nós e o
   * O(n²) síncrono travava a tela), arrastar nó para fixar, pinch-zoom, minimapa, rótulos que
   * somem de longe, comunidades (label propagation) e elos críticos (Tarjan). Os algoritmos e
   * as classes CSS vêm de js/mesa_inquerito.js / css/mesa.css — aqui só o modelo "nó =
   * inquérito, aresta = par com sinal em comum". */

  var SVG_NS = "http://www.w3.org/2000/svg";
  var LARGURA = 900, ALTURA = 560, MARGEM = 36;
  var LAYOUT_ITERACOES = 300, LAYOUT_POR_FRAME = 30;
  var NOS_POR_TELA_BASE = 24, AREA_MAXIMA = 4;
  var FATOR_REPULSAO = 1.6, DISTANCIA_MINIMA_ARESTA = 70;
  var GRAVIDADE = 0.35, COESAO_COMUNIDADE = 0.08;
  var LIMITE_ROTULOS = 30, ZOOM_ROTULOS = 0.55;

  var COR_NO = "#6366f1";
  var COR_ARESTA_EXATA = "#22c55e";
  var COR_ARESTA_APROXIMADA = "#f59e0b";
  var CORES_COMUNIDADE = [
    "#25d366", "#4fb3d9", "#f2789f", "#e0985a", "#9b8afb",
    "#54c7b8", "#c99342", "#d95f8a", "#5b9bd5", "#a97bd6"
  ];

  var $layoutStatus = $("#conv-layout-status");
  var $legendaComunidades = $("#conv-legenda-comunidades");

  var vb = { x: 0, y: 0, w: LARGURA, h: ALTURA };
  var areaLayoutAtual = { largura: LARGURA, altura: ALTURA };

  var nosG = [];              // nós do desenho atual (após recorte de comunidade)
  var arestasG = [];          // arestas do desenho atual
  var noPorId = {};
  var arestasFiltroAtual = []; // resposta crua de /arestas (o recorte todo, antes da comunidade)
  var comunidadePorNo = {};
  var cacheComunidades = { chave: null, valor: null };
  var cacheElos = { chave: null, valor: null };
  var comunidadeFocada = null;
  var chaveComunidadeFoco = null; // partição (hash das arestas) sobre a qual o foco foi escolhido
  var destacarElos = false;
  var assinaturaLayout = null;
  var posMemorizada = {};     // id -> {x, y, fixo}: o lugar escolhido sobrevive a re-filtrar
  var layoutHandle = null, layoutWorker = null;

  function aplicarViewBox() {
    svg.setAttribute("viewBox", vb.x + " " + vb.y + " " + vb.w + " " + vb.h);
    atualizarModoRotulos();
    agendarMinimapa();
  }

  function el(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function hash01(s) {
    var h = 2166136261;
    s = String(s);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }

  // hash O(n) dos ids — a "digital" do conjunto desenhado, para layout/comunidades saberem se
  // precisam mesmo recalcular (sort+join de milhares de id doía a cada render).
  function hashIds(ids) {
    var h1 = 0, h2 = 0, i, c, s;
    for (i = 0; i < ids.length; i++) {
      s = String(ids[i]);
      for (var j = 0; j < s.length; j++) {
        c = s.charCodeAt(j);
        h1 = (Math.imul(h1, 31) + c) | 0;
        h2 = (Math.imul(h2, 37) ^ c) | 0;
      }
    }
    return (h1 >>> 0) + "," + (h2 >>> 0);
  }

  // A tela é fixa, o mundo não: o layout cresce com o número de nós (até um teto) para o
  // Fruchterman-Reingold ter chão — senão 400 inquéritos se amassam nos mesmos 900×560.
  function areaLayout(qtdNos) {
    var fator = Math.min(Math.sqrt(Math.max(qtdNos, 1) / NOS_POR_TELA_BASE), AREA_MAXIMA);
    return { largura: Math.round(LARGURA * fator), altura: Math.round(ALTURA * fator) };
  }

  function raioNo(n) {
    return Math.min(10 + 2.4 * Math.sqrt(n.grau || 0), 26);
  }

  var colorePorComunidade = false; // definido em montarModelo sobre a base inteira do recorte

  function temComunidades() {
    return colorePorComunidade;
  }

  function contarComunidades(nosBase) {
    var vistos = {};
    nosBase.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c !== undefined && c !== null) vistos[c] = true;
    });
    return Object.keys(vistos).length;
  }

  function corNo(n) {
    var c = comunidadePorNo[n.id];
    if (c === undefined || c === null || !temComunidades()) return COR_NO;
    return CORES_COMUNIDADE[c % CORES_COMUNIDADE.length];
  }

  function chavePar(a, b) { return a < b ? a + "|" + b : b + "|" + a; }

  // ---------- análise estrutural (porta de mesa_inquerito.js:1833-1971) ----------

  function calcularComunidadesDeVerdade(nosBase, arestasBase) {
    // propagação de rótulos: cada nó adota o rótulo mais comum entre os vizinhos
    var viz = {};
    nosBase.forEach(function (n) { viz[n.id] = []; });
    arestasBase.forEach(function (a) {
      if (!viz[a.a] || !viz[a.b]) return;
      viz[a.a].push(a.b);
      viz[a.b].push(a.a);
    });
    var rotulo = {};
    var ids = nosBase.map(function (n) { return n.id; }).sort();
    ids.forEach(function (id, i) { rotulo[id] = i; });

    for (var it = 0; it < 20; it++) {
      var mudou = false;
      ids.forEach(function (id) {
        if (!viz[id].length) return;
        var contagem = {};
        viz[id].forEach(function (v) { contagem[rotulo[v]] = (contagem[rotulo[v]] || 0) + 1; });
        var melhor = rotulo[id], melhorQt = -1;
        Object.keys(contagem).sort().forEach(function (r) {
          if (contagem[r] > melhorQt) { melhorQt = contagem[r]; melhor = parseInt(r, 10); }
        });
        if (melhor !== rotulo[id]) { rotulo[id] = melhor; mudou = true; }
      });
      if (!mudou) break;
    }

    var mapa = {}, proximo = 0, resultado = {};
    ids.forEach(function (id) {
      if (mapa[rotulo[id]] === undefined) mapa[rotulo[id]] = proximo++;
      resultado[id] = mapa[rotulo[id]];
    });
    return resultado;
  }

  // Tarjan iterativo: pontos de articulação (inquérito que, removido, desconecta o quadro) e
  // pontes (par que é a única porta entre dois grupos de casos — o dado mais valioso daqui).
  function calcularElosCriticos(nosBase, arestasBase) {
    var idx = {};
    nosBase.forEach(function (n, i) { idx[n.id] = i; });
    var N = nosBase.length;
    var adj = new Array(N);
    for (var i = 0; i < N; i++) adj[i] = [];
    var edges = [];
    arestasBase.forEach(function (a) {
      var u = idx[a.a], v = idx[a.b];
      if (u == null || v == null || u === v) return;
      var e = edges.length;
      edges.push([u, v]);
      adj[u].push([v, e]); adj[v].push([u, e]);
    });

    var disc = new Array(N).fill(-1), low = new Array(N).fill(0),
        parentEdge = new Array(N).fill(-1),
        articulacoes = new Array(N).fill(false),
        ponteEdge = new Array(edges.length).fill(false);
    var tempo = 0;

    for (var raiz = 0; raiz < N; raiz++) {
      if (disc[raiz] !== -1) continue;
      disc[raiz] = low[raiz] = tempo++;
      var pilha = [{ u: raiz, pi: 0 }];
      while (pilha.length) {
        var topo = pilha[pilha.length - 1];
        var u = topo.u;
        if (topo.pi < adj[u].length) {
          var par = adj[u][topo.pi++];
          var v = par[0], e = par[1];
          if (e === parentEdge[u]) continue;
          if (disc[v] === -1) {
            parentEdge[v] = e;
            disc[v] = low[v] = tempo++;
            pilha.push({ u: v, pi: 0 });
          } else {
            low[u] = Math.min(low[u], disc[v]);
          }
          continue;
        }
        pilha.pop();
        if (parentEdge[u] !== -1) {
          var pe = parentEdge[u];
          var p = edges[pe][0] === u ? edges[pe][1] : edges[pe][0];
          if (low[u] > disc[p]) ponteEdge[pe] = true;
          if (parentEdge[p] !== -1 && low[u] >= disc[p]) articulacoes[p] = true;
          low[p] = Math.min(low[p], low[u]);
        } else {
          var filhos = 0;
          for (var k = 0; k < adj[u].length; k++) {
            var to = adj[u][k][0], ee = adj[u][k][1];
            if (parentEdge[to] === ee) filhos++;
          }
          if (filhos > 1) articulacoes[u] = true;
        }
      }
    }

    var out = { articulacoes: {}, pontes: {} };
    for (var a2 = 0; a2 < N; a2++) if (articulacoes[a2]) out.articulacoes[nosBase[a2].id] = true;
    for (var e2 = 0; e2 < edges.length; e2++) {
      if (ponteEdge[e2]) out.pontes[chavePar(nosBase[edges[e2][0]].id, nosBase[edges[e2][1]].id)] = true;
    }
    return out;
  }

  function obterComunidades() {
    var chave = arestasFiltroAtual.length + "e" + hashIds(arestasFiltroAtual.map(function (a) { return a.id_a + ":" + a.id_b; }));
    if (cacheComunidades.chave === chave && cacheComunidades.valor) return cacheComunidades.valor;
    cacheComunidades = { chave: chave, valor: calcularComunidadesDeVerdade(modeloBase().nos, modeloBase().arestas) };
    return cacheComunidades.valor;
  }

  function obterElosCriticos() {
    var chave = nosG.length + "n" + arestasG.length + "e" + hashIds(nosG.map(function (n) { return n.id; }));
    if (cacheElos.chave === chave && cacheElos.valor) return cacheElos.valor;
    cacheElos = { chave: chave, valor: calcularElosCriticos(nosG, arestasG) };
    return cacheElos.valor;
  }

  // ---------- modelo (nós/arestas a partir das arestas cruas, com o recorte de comunidade) ----------

  function modeloBase() {
    var indice = {}, nosB = [];
    arestasFiltroAtual.forEach(function (ar) {
      [[ar.id_a, ar.nu_a], [ar.id_b, ar.nu_b]].forEach(function (t) {
        if (indice[t[0]] === undefined) { indice[t[0]] = nosB.length; nosB.push({ id: t[0], nu: t[1] }); }
      });
    });
    var arestasB = arestasFiltroAtual.map(function (ar) {
      return { a: ar.id_a, b: ar.id_b, qt: ar.qt_sinais, temExata: ar.tem_exata, par: ar };
    });
    return { nos: nosB, arestas: arestasB };
  }

  function montarModelo() {
    var com = obterComunidades();
    comunidadePorNo = com;
    var base = modeloBase();
    // a cor vale pela PARTIÇÃO, não pelo recorte focado: ao isolar um grupo pela legenda os
    // nós continuam na cor da comunidade (senão a cor mudaria justo quando mais se olha)
    colorePorComunidade = contarComunidades(base.nos) >= 2;
    var selecionados = base.nos;
    if (comunidadeFocada !== null) {
      selecionados = base.nos.filter(function (n) { return com[n.id] === comunidadeFocada; });
    }
    var idsVisiveis = {};
    selecionados.forEach(function (n) { idsVisiveis[n.id] = true; });

    noPorId = {};
    nosG = selecionados.map(function (n) {
      var pos = posMemorizada[n.id];
      var no = {
        id: n.id, nu_inquerito: n.nu, grau: 0,
        x: pos ? pos.x : null, y: pos ? pos.y : null, fixo: pos ? pos.fixo : false,
      };
      noPorId[n.id] = no;
      return no;
    });
    arestasG = [];
    base.arestas.forEach(function (ar) {
      if (!idsVisiveis[ar.a] || !idsVisiveis[ar.b]) return;
      noPorId[ar.a].grau++;
      noPorId[ar.b].grau++;
      arestasG.push({ a: ar.a, b: ar.b, qt: ar.qt, temExata: ar.temExata, par: ar.par, chave: chavePar(ar.a, ar.b) });
    });
    // grau 0 dentro do recorte não é desenho — é resto de aresta cortada pelo filtro de comunidade
    nosG = nosG.filter(function (n) { return n.grau > 0; });
    Object.keys(noPorId).forEach(function (id) { if (!noPorId[id].grau) delete noPorId[id]; });
  }

  // ---------- layout (porta de mesa_inquerito.js:1522-1831) ----------

  function cancelarLayout() {
    if (layoutHandle) { cancelAnimationFrame(layoutHandle); layoutHandle = null; }
    if (layoutWorker) { layoutWorker.terminate(); layoutWorker = null; }
  }

  function calcularLayoutAsync(ok) {
    cancelarLayout();

    function terminar(recalculou) {
      if (recalculou) {
        afastarColisoes();
        nosG.forEach(function (n) {
          if (n.x != null) posMemorizada[n.id] = { x: n.x, y: n.y, fixo: !!n.fixo };
        });
      }
      $layoutStatus.addClass("hidden");
      if (ok) ok(recalculou);
    }

    if (!nosG.length) { $layoutStatus.addClass("hidden"); if (ok) ok(false); return; }

    // Só refaz o layout quando o CONJUNTO de nós muda (filtro/recorte). Ligar elos críticos ou
    // arrastar um nó não remexem nas posições — só nas cores e classes.
    var assinatura = nosG.length + "n" + hashIds(nosG.map(function (n) { return n.id; }));
    var algumSemPosicao = nosG.some(function (n) { return n.x == null || n.y == null; });
    if (assinatura === assinaturaLayout && !algumSemPosicao) {
      $layoutStatus.addClass("hidden"); // cancelamos um worker antigo — o overlay não pode ficar preso
      if (ok) ok(false);
      return;
    }
    assinaturaLayout = assinatura;

    nosG.forEach(function (n) { if (!n.fixo) { n.x = null; n.y = null; } });

    var area = areaLayout(nosG.length);
    areaLayoutAtual = area;
    var soltos = nosG.filter(function (n) { return n.x == null || n.y == null; });

    // Semente circular por COMUNIDADE (reprodutível pelo hash do id): parentes começam lado a
    // lado no anel e o FR converge com eles juntos em vez de embaralhar.
    var porCom = {};
    soltos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      c = (c === undefined || c === null) ? -1 : c;
      (porCom[c] = porCom[c] || []).push(n);
    });
    var chavesCom = Object.keys(porCom).sort(function (a, b) { return porCom[b].length - porCom[a].length; });
    var raioSemente = Math.min(area.largura, area.altura) / 3;
    var totalSoltos = Math.max(soltos.length, 1);
    var antesSoltos = 0;
    chavesCom.forEach(function (chaveCom) {
      var membros = porCom[chaveCom];
      var ang0 = (2 * Math.PI * antesSoltos) / totalSoltos;
      var arco = (2 * Math.PI * membros.length) / totalSoltos;
      membros.forEach(function (n, k) {
        var ang = ang0 + arco * ((k + 0.5 + (hash01(n.id) - 0.5) * 0.4) / Math.max(membros.length, 1));
        var rr = raioSemente * (0.72 + 0.28 * hash01(n.id + "|raio"));
        n.x = area.largura / 2 + Math.cos(ang) * rr;
        n.y = area.altura / 2 + Math.sin(ang) * rr;
      });
      antesSoltos += membros.length;
    });
    if (!soltos.length) { terminar(true); return; }

    // >500 nós não passa pelo FR O(n²) — radial por comunidade, O(n), instantâneo.
    if (nosG.length > 500) {
      layoutRadialPorComunidade(area);
      terminar(true);
      return;
    }

    $layoutStatus.removeClass("hidden");

    function aplicarPosicoes(posicoes) {
      Object.keys(posicoes).forEach(function (id) {
        var n = noPorId[id];
        if (n && !n.fixo) { n.x = posicoes[id].x; n.y = posicoes[id].y; }
      });
    }

    if (window.Worker) {
      var worker = new Worker("js/mesa_layout_worker.js");
      layoutWorker = worker;
      worker.onmessage = function (evt) {
        if (evt.data.tipo === "progresso") {
          aplicarPosicoes(evt.data.posicoes);
        } else if (evt.data.tipo === "fim") {
          aplicarPosicoes(evt.data.posicoes);
          worker.terminate();
          if (layoutWorker === worker) layoutWorker = null;
          terminar(true);
        }
      };
      worker.onerror = function () {
        worker.terminate();
        if (layoutWorker === worker) layoutWorker = null;
        calcularLayoutSincrono(terminar);
      };
      var iteracoes = nosG.length > 200 ? 80 : LAYOUT_ITERACOES;
      worker.postMessage({
        nos: nosG.map(function (n) { return { id: n.id, x: n.x, y: n.y, fixo: !!n.fixo }; }),
        arestas: arestasG.map(function (a) { return { a: a.a, b: a.b }; }),
        comunidades: nosG.map(function (n) {
          var c = comunidadePorNo[n.id];
          return (c === undefined || c === null) ? -1 : c;
        }),
        largura: area.largura, altura: area.altura, margem: MARGEM,
        iteracoes: iteracoes, porLote: LAYOUT_POR_FRAME,
        repulsao: FATOR_REPULSAO, distanciaMinima: DISTANCIA_MINIMA_ARESTA,
        gravidade: GRAVIDADE, coesao: COESAO_COMUNIDADE
      });
      return;
    }

    calcularLayoutSincrono(terminar);
  }

  function layoutRadialPorComunidade(area) {
    var grupos = {};
    nosG.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined || c === null) c = -1;
      (grupos[c] = grupos[c] || []).push(n);
    });
    var chaves = Object.keys(grupos).sort(function (a, b) { return grupos[b].length - grupos[a].length; });
    var cx = area.largura / 2, cy = area.altura / 2;
    var raioGeral = Math.min(area.largura, area.altura) * 0.38;
    chaves.forEach(function (chave, gi) {
      var membros = grupos[chave].sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); });
      var ang = (2 * Math.PI * gi) / Math.max(chaves.length, 1) - Math.PI / 2;
      var gx = cx + Math.cos(ang) * raioGeral * (chaves.length > 1 ? 1 : 0);
      var gy = cy + Math.sin(ang) * raioGeral * (chaves.length > 1 ? 1 : 0);
      var raioGrupo = Math.min(90 + Math.sqrt(membros.length) * 14, raioGeral * 0.9 + 40);
      membros.forEach(function (n, i) {
        if (n.fixo && n.x != null) return;
        if (i === 0) { n.x = gx; n.y = gy; return; }
        var a2 = (2 * Math.PI * (i - 1)) / Math.max(membros.length - 1, 1);
        var rr = raioGrupo * (0.35 + 0.65 * ((i % 5) / 4));
        n.x = gx + Math.cos(a2) * rr;
        n.y = gy + Math.sin(a2) * rr;
      });
    });
  }

  function calcularLayoutSincrono(ok) {
    var area = areaLayout(nosG.length);
    var K = Math.sqrt((area.largura * area.altura) / nosG.length) * 0.9;
    var temperatura = area.largura / 10;
    var indice = {};
    nosG.forEach(function (n, i) { indice[n.id] = i; });
    var iteracao = 0;

    function passo() {
      var fim = Math.min(iteracao + LAYOUT_POR_FRAME, LAYOUT_ITERACOES);
      while (iteracao < fim) {
        nosG.forEach(function (n) { n.dispX = 0; n.dispY = 0; });

        for (var i = 0; i < nosG.length; i++) {
          for (var j = i + 1; j < nosG.length; j++) {
            var a = nosG[i], b = nosG[j];
            var dx = a.x - b.x, dy = a.y - b.y;
            var d = Math.max(Math.hypot(dx, dy), 0.01);
            var f = (K * K * FATOR_REPULSAO) / d;
            a.dispX += (dx / d) * f; a.dispY += (dy / d) * f;
            b.dispX -= (dx / d) * f; b.dispY -= (dy / d) * f;
          }
        }

        arestasG.forEach(function (e) {
          var a = nosG[indice[e.a]], b = nosG[indice[e.b]];
          if (!a || !b) return;
          var dx = a.x - b.x, dy = a.y - b.y;
          var d = Math.max(Math.hypot(dx, dy), 0.01);
          var util = Math.max(d - DISTANCIA_MINIMA_ARESTA, 0);
          var f = (util * util) / K;
          a.dispX -= (dx / d) * f; a.dispY -= (dy / d) * f;
          b.dispX += (dx / d) * f; b.dispY += (dy / d) * f;
        });

        nosG.forEach(function (n) {
          if (n.fixo) return;
          var d = Math.max(Math.hypot(n.dispX, n.dispY), 0.01);
          var lim = Math.min(d, temperatura);
          n.x += (n.dispX / d) * lim;
          n.y += (n.dispY / d) * lim;
          n.x = Math.max(-area.largura, Math.min(area.largura * 2, n.x));
          n.y = Math.max(-area.altura, Math.min(area.altura * 2, n.y));
        });

        temperatura *= 0.98;
        iteracao++;
      }

      if (iteracao < LAYOUT_ITERACOES) {
        layoutHandle = requestAnimationFrame(passo);
      } else {
        layoutHandle = null;
        if (ok) ok(true);
      }
    }

    layoutHandle = requestAnimationFrame(passo);
  }

  // Passe anti-colisão (grade espacial O(n)): o FR puro encosta nó em nó, e nó encostado é o
  // rótulo brigando com rótulo. A folga de 18 é o espaço do número do inquérito embaixo do círculo.
  function afastarColisoes() {
    if (nosG.length < 2) return;
    var CELULA = 96;
    var grade = {};
    function chaveCelula(x, y) { return Math.floor(x / CELULA) + ":" + Math.floor(y / CELULA); }
    function reconstruir() {
      grade = {};
      nosG.forEach(function (n) {
        if (n.x == null) return;
        var k = chaveCelula(n.x, n.y);
        (grade[k] = grade[k] || []).push(n);
      });
    }
    reconstruir();
    for (var rodada = 0; rodada < 3; rodada++) {
      var moveu = false;
      nosG.forEach(function (n) {
        if (n.x == null) return;
        var cx = Math.floor(n.x / CELULA), cy = Math.floor(n.y / CELULA);
        for (var gx = cx - 1; gx <= cx + 1; gx++) {
          for (var gy = cy - 1; gy <= cy + 1; gy++) {
            var cel = grade[gx + ":" + gy];
            if (!cel) continue;
            for (var i = 0; i < cel.length; i++) {
              var m = cel[i];
              if (m === n || m.id <= n.id) continue;
              var min = raioNo(n) + raioNo(m) + 18;
              var dx = n.x - m.x, dy = n.y - m.y;
              var d = Math.max(Math.hypot(dx, dy), 0.01);
              if (d >= min) continue;
              var abre = min - d;
              var ux = dx / d, uy = dy / d;
              if (!n.fixo && !m.fixo) {
                n.x += ux * abre / 2; n.y += uy * abre / 2;
                m.x -= ux * abre / 2; m.y -= uy * abre / 2;
              } else if (!n.fixo) {
                n.x += ux * abre; n.y += uy * abre;
              } else if (!m.fixo) {
                m.x -= ux * abre; m.y -= uy * abre;
              } else {
                continue;
              }
              posMemorizada[n.id] = { x: n.x, y: n.y, fixo: !!n.fixo };
              posMemorizada[m.id] = { x: m.x, y: m.y, fixo: !!m.fixo };
              moveu = true;
            }
          }
        }
      });
      if (!moveu) break;
      reconstruir();
    }
  }

  // ---------- legendas ----------

  function renderLegendaGrafo() {
    $legenda.empty();
    var itens = [
      { cor: COR_ARESTA_EXATA, texto: "Tem sinal exato (CPF/telefone/e-mail/IMEI)" },
      { cor: COR_ARESTA_APROXIMADA, texto: "Só sinal aproximado (nome/local)" },
    ];
    itens.forEach(function (item) {
      var $i = $("<div>").addClass("mesa-legenda-item mesa-legenda-info");
      $("<span>").addClass("mesa-legenda-bolinha").css("background", item.cor).appendTo($i);
      $("<span>").text(item.texto).appendTo($i);
      $legenda.append($i);
    });
    var $n = $("<div>").addClass("mesa-legenda-item mesa-legenda-info");
    $("<span>").addClass("mesa-legenda-bolinha").css("background", CORES_COMUNIDADE[0]).appendTo($n);
    $("<span>").text(temComunidades()
      ? "Cor = comunidade de casos · tamanho = nº de pares (clique na legenda p/ isolar)"
      : "Nó = inquérito · tamanho = nº de pares de convergência").appendTo($n);
    $legenda.append($n);
    if (destacarElos) {
      var $p = $("<div>").addClass("mesa-legenda-item mesa-legenda-info");
      $("<span>").addClass("mesa-legenda-linha").css("background", "var(--danger, #e5534b)").appendTo($p);
      $("<span>").text("Elo crítico: ponte entre grupos ou inquérito que segura o grupo").appendTo($p);
      $legenda.append($p);
    }
  }

  function renderLegendaComunidades() {
    $legendaComunidades.empty();
    if (!nosG.length) return;
    var tamCom = {};
    nosG.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined || c === null) return;
      tamCom[c] = (tamCom[c] || 0) + 1;
    });
    var lista = Object.keys(tamCom).map(function (k) { return { indice: parseInt(k, 10), qt: tamCom[k] }; })
      .sort(function (a, b) { return b.qt - a.qt; });
    if (lista.length < 2 && comunidadeFocada === null) return;

    var $titulo = $("<span>").addClass("mesa-legenda-titulo").text("Comunidades:");
    $legendaComunidades.append($titulo);

    if (comunidadeFocada !== null) {
      var $voltar = $("<button>").attr("type", "button").addClass("mesa-legenda-item mesa-comunidade-btn")
        .attr("title", "Mostrar todas as comunidades de novo");
      $("<span>").text("× Mostrar todas").appendTo($voltar);
      $voltar.on("click", function () { comunidadeFocada = null; reRenderizar(); });
      $legendaComunidades.append($voltar);
    }

    var cabem = lista.slice(0, CORES_COMUNIDADE.length);
    var resto = lista.slice(CORES_COMUNIDADE.length);
    cabem.forEach(function (g) {
      var focoAqui = comunidadeFocada === g.indice;
      var $item = $("<button>").attr("type", "button")
        .addClass("mesa-legenda-item mesa-comunidade-btn")
        .toggleClass("desligado", comunidadeFocada !== null && !focoAqui)
        .toggleClass("ativo", focoAqui)
        .attr("title", focoAqui ? "Mostrar todas de novo" : "Isolar só esta comunidade no quadro");
      $("<span>").addClass("mesa-legenda-bolinha")
        .css("background", CORES_COMUNIDADE[g.indice % CORES_COMUNIDADE.length]).appendTo($item);
      $("<span>").text("Grupo " + (g.indice + 1) + " (" + g.qt + ")").appendTo($item);
      $item.on("click", function () {
        comunidadeFocada = focoAqui ? null : g.indice;
        chaveComunidadeFoco = comunidadeFocada === null ? null : cacheComunidades.chave;
        reRenderizar();
      });
      $legendaComunidades.append($item);
    });
    if (resto.length) {
      var $mais = $("<span>").addClass("mesa-legenda-item mesa-legenda-info")
        .text("+ " + resto.length + " menor(es)");
      $legendaComunidades.append($mais);
    }
  }

  // ---------- desenho ----------

  function vizinhosDe(id) {
    var viz = {};
    arestasG.forEach(function (ar) {
      if (ar.a === id) viz[ar.b] = true;
      if (ar.b === id) viz[ar.a] = true;
    });
    return viz;
  }

  function desenharGrafo() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!nosG.length) return;

    var elos = destacarElos ? obterElosCriticos() : { articulacoes: {}, pontes: {} };

    var defs = el("defs", {});
    defs.innerHTML =
      '<marker id="conv-seta-verde" markerWidth="7" markerHeight="7" refX="14" refY="3" orient="auto" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L6,3 L0,6 Z" fill="' + COR_ARESTA_EXATA + '"></path></marker>' +
      '<marker id="conv-seta-amarela" markerWidth="7" markerHeight="7" refX="14" refY="3" orient="auto" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L6,3 L0,6 Z" fill="' + COR_ARESTA_APROXIMADA + '"></path></marker>';
    svg.appendChild(defs);

    var gArestas = el("g", {});
    var gNos = el("g", {});
    svg.appendChild(gArestas);
    svg.appendChild(gNos);

    arestasG.forEach(function (ar) {
      var a = noPorId[ar.a], b = noPorId[ar.b];
      if (!a || !b || a.x == null || b.x == null) return;
      var cor = ar.temExata ? COR_ARESTA_EXATA : COR_ARESTA_APROXIMADA;
      var classe = "mesa-aresta";
      if (destacarElos && elos.pontes[ar.chave]) classe += " ponte";
      var linha = el("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: classe,
        "marker-end": ar.temExata ? "url(#conv-seta-verde)" : "url(#conv-seta-amarela)",
      });
      // a cor entra por variável CSS (não por stroke inline): é assim que a classe .ponte
      // consegue pintar a ponte de vermelho por cima — inline venceria qualquer regra
      linha.style.setProperty("--mesa-aresta-cor", cor);
      linha.style.strokeWidth = Math.min(1 + ar.qt * 0.6, 5);
      if (!ar.temExata && !(destacarElos && elos.pontes[ar.chave])) linha.style.strokeDasharray = "5 4";
      ar._el = linha;
      gArestas.appendChild(linha);

      var rotulo = el("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 3, class: "mesa-aresta-rotulo" });
      rotulo.textContent = ar.qt + (ar.qt > 1 ? " sinais" : " sinal");
      ar._rotulo = rotulo;
      gArestas.appendChild(rotulo);

      // linha invisível larga por cima: a 1,3 px seria quase impossível mirar o clique/toque
      var hit = el("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "mesa-aresta-hit",
      });
      hit.addEventListener("click", function (evt) {
        evt.stopPropagation();
        abrirModalPar(ar.par);
      });
      hit.addEventListener("mouseenter", function () {
        if (ar._rotulo) ar._rotulo.classList.add("rotulo-visivel");
      });
      hit.addEventListener("mouseleave", function () {
        if (ar._rotulo) ar._rotulo.classList.remove("rotulo-visivel");
      });
      var tt = el("title", {});
      tt.textContent = noPorId[ar.a].nu_inquerito + " ⟷ " + noPorId[ar.b].nu_inquerito +
        " — " + ar.qt + " sinal(is)" + (ar.temExata ? " (tem exato)" : " (só aproximado)");
      hit.appendChild(tt);
      ar._hit = hit;
      gArestas.appendChild(hit);
    });

    nosG.forEach(function (no) {
      if (no.x == null) return;
      var r = raioNo(no);
      var grupo = el("g", { class: "mesa-no-grupo" + (no.grau >= 10 ? " no-hub" : "") });
      if (destacarElos && elos.articulacoes[no.id]) grupo.classList.add("no-articulacao");
      var circulo = el("circle", { cx: no.x, cy: no.y, r: r, class: "mesa-no" });
      circulo.setAttribute("fill", corNo(no));
      circulo.__no = no;
      no._grupo = grupo;
      no._forma = circulo;

      var titulo = el("title", {});
      titulo.textContent = no.nu_inquerito + " — " + no.grau + " par(es) de convergência";
      circulo.appendChild(titulo);

      var rotulo = el("text", { x: no.x, y: no.y + r + 12, class: "mesa-no-rotulo" });
      rotulo.textContent = no.nu_inquerito;
      no._rotulo = rotulo;

      grupo.appendChild(circulo);
      grupo.appendChild(rotulo);

      grupo.addEventListener("mouseenter", function () { realcarVizinhaca(no, true); });
      grupo.addEventListener("mouseleave", function () { realcarVizinhaca(no, false); });
      gNos.appendChild(grupo);
    });

    atualizarModoRotulos();
    desenharMinimapa();
  }

  // hover = "onde isso toca": o nó e os vizinhos direto ficam acesos, o resto esmaece — as
  // classes de foco/opacidade são as mesmas da Mesa (css/mesa.css).
  function realcarVizinhaca(no, ligado) {
    if (ligado) {
      var viz = vizinhosDe(no.id);
      svg.classList.add("mesa-grafo-com-foco");
      nosG.forEach(function (n) {
        if (!n._grupo) return;
        var dentro = n === no || viz[n.id];
        n._grupo.classList.toggle("fora-do-foco", !dentro);
        n._grupo.classList.toggle("no-focado", n === no);
        if (n._rotulo) n._rotulo.classList.toggle("rotulo-visivel", dentro);
      });
      arestasG.forEach(function (ar) {
        var ligada = ar.a === no.id || ar.b === no.id;
        if (ar._el) ar._el.classList.toggle("fora-do-foco", !ligada);
        if (ar._rotulo) {
          ar._rotulo.classList.toggle("fora-do-foco", !ligada);
          ar._rotulo.classList.toggle("rotulo-visivel", ligada);
        }
      });
    } else {
      svg.classList.remove("mesa-grafo-com-foco");
      nosG.forEach(function (n) {
        if (!n._grupo) return;
        n._grupo.classList.remove("fora-do-foco", "no-focado");
        if (n._rotulo) n._rotulo.classList.remove("rotulo-visivel");
      });
      arestasG.forEach(function (ar) {
        if (ar._el) ar._el.classList.remove("fora-do-foco");
        if (ar._rotulo) ar._rotulo.classList.remove("fora-do-foco", "rotulo-visivel");
      });
    }
  }

  // ---------- rótulos sob demanda ----------
  /* Passando de LIMITE_ROTULOS inquéritos, os números só aparecem quando o analista se aproxima
   * ou aponta um nó — de longe o que importa é a FORMA da rede, não o texto. */
  function rotulosDevemSumir() {
    if (nosG.length <= LIMITE_ROTULOS) return false;
    return vb.w > areaLayoutAtual.largura * ZOOM_ROTULOS;
  }

  function atualizarModoRotulos() {
    svg.classList.toggle("mesa-grafo-rotulos-ocultos", rotulosDevemSumir());
  }

  function abrirModalNo(no, arestasDoNo) {
    $parTitulo.text(no.nu_inquerito);
    $parCorpo.empty();
    $("<dt>").text("Converge com (" + arestasDoNo.length + ")").appendTo($parCorpo);
    var $lista2 = $("<dd>").appendTo($parCorpo);
    arestasDoNo.forEach(function (ar) {
      var outro = ar.id_a === no.id ? ar.nu_b : ar.nu_a;
      var $linha = $("<div>").css({ cursor: "pointer", padding: "4px 0" }).appendTo($lista2);
      $("<span>").text(outro + " — " + ar.qt_sinais + " sinal(is)" + (ar.tem_exata ? "" : " (só aproximado)")).appendTo($linha);
      $linha.on("click", function () { abrirModalPar(ar); });
    });
    $modalPar.removeClass("hidden");
  }

  /* O grafo recebe SÓ as arestas ({id_a, nu_a, id_b, nu_b, qt_sinais, tem_exata}) da rota
   * /convergencia/arestas — a forma da rede inteira do filtro, sem o texto dos sinais, que era
   * metade do payload que travava a tela. Clicar numa aresta (ou num item da lista de um nó)
   * puxa os sinais daquele par no /convergencia/par, na hora. */
  function renderGrafo(arestasFiltro) {
    arestasFiltroAtual = arestasFiltro || [];
    // o índice da comunidade focada é da partição do recorte anterior — num recorte novo o
    // mesmo número pode significar outro grupo. O foco só sobrevive quando a partição é a mesma.
    if (comunidadeFocada !== null) {
      obterComunidades(); // garante cache/chave atuais
      if (cacheComunidades.chave !== chaveComunidadeFoco) comunidadeFocada = null;
    }
    if (!arestasFiltroAtual.length) {
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      nosG = []; arestasG = []; noPorId = {};
      $vazioGrafo.removeClass("hidden").text("Nenhuma convergência para desenhar.");
      $legenda.empty();
      $legendaComunidades.empty();
      aplicarViewBox();
      return;
    }
    $vazioGrafo.addClass("hidden");

    montarModelo();
    renderLegendaGrafo();
    renderLegendaComunidades();

    calcularLayoutAsync(function (recalculou) {
      desenharGrafo();
      if (recalculou) enquadrarConteudo(); else aplicarViewBox();
    });
  }

  // recorte/realce que não vêm do servidor (comunidade, elos): redesenha do modelo em memória
  function reRenderizar() {
    if (!arestasFiltroAtual.length) return;
    montarModelo();
    renderLegendaGrafo();
    renderLegendaComunidades();
    calcularLayoutAsync(function (recalculou) {
      desenharGrafo();
      if (recalculou) enquadrarConteudo(); else aplicarViewBox();
    });
  }

  function enquadrarConteudo() {
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nosG.forEach(function (n) {
      if (n.x == null || n.y == null) return;
      var r = raioNo(n) + 22; // folga pro rótulo embaixo do nó
      if (n.x - r < minX) minX = n.x - r;
      if (n.x + r > maxX) maxX = n.x + r;
      if (n.y - r < minY) minY = n.y - r;
      if (n.y + r > maxY) maxY = n.y + r;
    });
    if (minX === Infinity) return;
    var pad = 20;
    var w = Math.max(maxX - minX, 1) + pad * 2;
    var h = Math.max(maxY - minY, 1) + pad * 2;
    var razaoTela = svg.clientWidth && svg.clientHeight ? svg.clientWidth / svg.clientHeight : w / h;
    if (w / h < razaoTela) { w = h * razaoTela; } else { h = w / razaoTela; }
    vb.w = w;
    vb.h = h;
    vb.x = (minX + maxX) / 2 - w / 2;
    vb.y = (minY + maxY) / 2 - h / 2;
    aplicarViewBox();
  }

  // ---------------- minimapa (porta de mesa_inquerito.js:2185-2268) ----------------

  var minimapaRaf = null;
  var minimapaTela = null;

  function minimapaCanvas() { return document.getElementById("mesa-minimapa"); }

  function desenharMinimapa() {
    minimapaRaf = null;
    var cv = minimapaCanvas();
    if (!cv || cv.offsetParent === null) return;
    var ctx = cv.getContext("2d");
    if (!ctx) return;
    var W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    // desenho pequeno não tem para onde se perder — esconde a caixinha em vez de mostrar vazia
    if (nosG.length < 25) { cv.classList.add("mesa-minimapa-vazio"); minimapaTela = null; return; }
    cv.classList.remove("mesa-minimapa-vazio");
    if (!nosG.length) { minimapaTela = null; return; }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nosG.forEach(function (n) {
      if (n.x == null || n.y == null) return;
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    });
    if (minX === Infinity) { minimapaTela = null; return; }
    var pad = 6;
    var gw = Math.max(maxX - minX, 1), gh = Math.max(maxY - minY, 1);
    var escala = Math.min((W - pad * 2) / gw, (H - pad * 2) / gh);
    minimapaTela = { minX: minX, minY: minY, escala: escala, pad: pad };
    var passo = Math.max(1, Math.ceil(nosG.length / 800));
    for (var i = 0; i < nosG.length; i += passo) {
      var n = nosG[i];
      if (n.x == null || n.y == null) continue;
      ctx.fillStyle = corNo(n);
      ctx.beginPath();
      ctx.arc(pad + (n.x - minX) * escala, pad + (n.y - minY) * escala, 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
    var accent = (getComputedStyle(document.documentElement).getPropertyValue("--accent") || "").trim() || "#8285ff";
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(pad + (vb.x - minX) * escala, pad + (vb.y - minY) * escala, vb.w * escala, vb.h * escala);
  }

  function agendarMinimapa() {
    if (minimapaRaf) return;
    minimapaRaf = requestAnimationFrame(desenharMinimapa);
  }

  function pontoGrafoNoMinimapa(evt) {
    var cv = minimapaCanvas();
    if (!cv || !minimapaTela) return null;
    var r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var cx = (evt.clientX - r.left) * (cv.width / r.width);
    var cy = (evt.clientY - r.top) * (cv.height / r.height);
    return {
      x: (cx - minimapaTela.pad) / minimapaTela.escala + minimapaTela.minX,
      y: (cy - minimapaTela.pad) / minimapaTela.escala + minimapaTela.minY
    };
  }

  function iniciarMinimapa() {
    var cv = minimapaCanvas();
    if (!cv) return;
    var arrastando = false;
    function centralizar(evt) {
      var p = pontoGrafoNoMinimapa(evt);
      if (!p) return;
      vb.x = p.x - vb.w / 2;
      vb.y = p.y - vb.h / 2;
      aplicarViewBox();
    }
    cv.addEventListener("pointerdown", function (evt) {
      arrastando = true;
      if (cv.setPointerCapture) { try { cv.setPointerCapture(evt.pointerId); } catch (e) {} }
      centralizar(evt);
    });
    cv.addEventListener("pointermove", function (evt) { if (arrastando) centralizar(evt); });
    cv.addEventListener("pointerup", function () { arrastando = false; });
    cv.addEventListener("pointercancel", function () { arrastando = false; });
  }

  // ---------------- zoom / pan / arrastar nó / pinch (mesma caixa de gestos da Mesa) ----------------

  function pontoSvg(evt) {
    var pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    var ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    var p = pt.matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  }

  svg.addEventListener("wheel", function (evt) {
    evt.preventDefault();
    var fator = evt.deltaY < 0 ? 1 / 1.15 : 1.15;
    var novoW = Math.min(Math.max(vb.w * fator, LARGURA / 8), areaLayoutAtual.largura * 2);
    var escala = novoW / vb.w;
    var p = pontoSvg(evt);
    vb.x = p.x - (p.x - vb.x) * escala;
    vb.y = p.y - (p.y - vb.y) * escala;
    vb.w = novoW;
    vb.h = vb.h * escala;
    aplicarViewBox();
  }, { passive: false });

  function zoomPeloCentro(fator) {
    var centroX = vb.x + vb.w / 2, centroY = vb.y + vb.h / 2;
    var novoW = Math.min(Math.max(vb.w * fator, LARGURA / 8), areaLayoutAtual.largura * 2);
    var escala = novoW / vb.w;
    vb.w = novoW;
    vb.h *= escala;
    vb.x = centroX - vb.w / 2;
    vb.y = centroY - vb.h / 2;
    aplicarViewBox();
  }

  $("#conv-zoom-in").on("click", function () { zoomPeloCentro(1 / 1.22); });
  $("#conv-zoom-out").on("click", function () { zoomPeloCentro(1.22); });
  $("#conv-enquadrar").on("click", enquadrarConteudo);

  // ---------- tela cheia ----------

  var areaGrafo = document.getElementById("conv-grafo-wrap");
  var botaoTelaCheia = document.getElementById("conv-tela-cheia");

  function atualizarBotaoTelaCheia() {
    if (!botaoTelaCheia) return;
    var ativo = document.fullscreenElement === areaGrafo;
    botaoTelaCheia.setAttribute("aria-label", ativo ? "Sair da tela cheia" : "Exibir em tela cheia");
    botaoTelaCheia.setAttribute("title", ativo ? "Voltar à visualização normal" : "Exibir o quadro em tela cheia");
    var texto = botaoTelaCheia.querySelector("span");
    if (texto) texto.textContent = ativo ? "Sair da tela cheia" : "Tela cheia";
  }

  function alternarTelaCheia() {
    if (!areaGrafo || !document.exitFullscreen || !areaGrafo.requestFullscreen) {
      if (window.Toast) Toast.aviso("A visualização em tela cheia não está disponível neste navegador.");
      return;
    }
    var operacao = document.fullscreenElement ? document.exitFullscreen() : areaGrafo.requestFullscreen();
    if (operacao && typeof operacao.catch === "function") {
      operacao.catch(function () { if (window.Toast) Toast.aviso("Não foi possível abrir o quadro em tela cheia."); });
    }
  }

  if (botaoTelaCheia && areaGrafo) {
    botaoTelaCheia.addEventListener("click", alternarTelaCheia);
    document.addEventListener("fullscreenchange", function () {
      atualizarBotaoTelaCheia();
      requestAnimationFrame(function () { requestAnimationFrame(enquadrarConteudo); });
    });
    atualizarBotaoTelaCheia();
  }

  // ---------- gestos: pan, arrastar nó (fixa), pinch de dois dedos ----------

  var drag = null;      // {tipo:'pan'|'no', ...}
  var ponteiros = {};   // pointerId -> {x, y} em coords de tela
  var pinca = null;

  function idsPonteiros() { return Object.keys(ponteiros); }
  function fimPinca() { if (idsPonteiros().length < 2) pinca = null; }

  function moverNo(no, x, y) {
    no.x = x;
    no.y = y;
    posMemorizada[no.id] = { x: x, y: y, fixo: !!no.fixo };
    var r = raioNo(no);
    if (no._forma) {
      no._forma.setAttribute("cx", x);
      no._forma.setAttribute("cy", y);
    }
    if (no._rotulo) {
      no._rotulo.setAttribute("x", x);
      no._rotulo.setAttribute("y", y + r + 12);
    }
    arestasG.forEach(function (ar) {
      if (ar.a !== no.id && ar.b !== no.id) return;
      var a = noPorId[ar.a], b = noPorId[ar.b];
      if (!a || !b || a.x == null || b.x == null) return;
      if (ar._el) { ar._el.setAttribute("x1", a.x); ar._el.setAttribute("y1", a.y); ar._el.setAttribute("x2", b.x); ar._el.setAttribute("y2", b.y); }
      if (ar._hit) { ar._hit.setAttribute("x1", a.x); ar._hit.setAttribute("y1", a.y); ar._hit.setAttribute("x2", b.x); ar._hit.setAttribute("y2", b.y); }
      if (ar._rotulo) { ar._rotulo.setAttribute("x", (a.x + b.x) / 2); ar._rotulo.setAttribute("y", (a.y + b.y) / 2 - 3); }
    });
  }

  svg.addEventListener("pointerdown", function (evt) {
    ponteiros[evt.pointerId] = { x: evt.clientX, y: evt.clientY };
    svg.setPointerCapture(evt.pointerId);
    var ids = idsPonteiros();
    if (ids.length >= 2) {
      if (drag) {
        if (drag.raf) cancelAnimationFrame(drag.raf);
        drag = null;
      }
      var a = ponteiros[ids[0]], b = ponteiros[ids[1]];
      var c0 = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      pinca = {
        ids: [ids[0], ids[1]],
        d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        vb0: { x: vb.x, y: vb.y, w: vb.w, h: vb.h },
        p0: pontoSvg({ clientX: c0.x, clientY: c0.y }),
        c0: c0
      };
      return;
    }
    if (pinca) return;
    var alvo = evt.target;
    var p = pontoSvg(evt);
    if (alvo.__no) {
      drag = { tipo: "no", no: alvo.__no, x0: p.x, y0: p.y, moveu: false };
    } else if (alvo.classList && (alvo.classList.contains("mesa-aresta-hit") || alvo.classList.contains("mesa-aresta"))) {
      return; // clique de aresta não vira pan — deixa o click do hit trabalhar
    } else {
      drag = { tipo: "pan", x0: evt.clientX, y0: evt.clientY, vb0: { x: vb.x, y: vb.y }, moveu: false };
    }
  });

  svg.addEventListener("pointermove", function (evt) {
    if (ponteiros[evt.pointerId]) {
      ponteiros[evt.pointerId].x = evt.clientX;
      ponteiros[evt.pointerId].y = evt.clientY;
    }
    if (pinca) {
      var a = ponteiros[pinca.ids[0]], b = ponteiros[pinca.ids[1]];
      if (!a || !b) return;
      var d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      var fator = pinca.d0 / d;
      var novoW = Math.min(Math.max(pinca.vb0.w * fator, LARGURA / 8), areaLayoutAtual.largura * 2);
      var f = novoW / pinca.vb0.w;
      vb.w = novoW;
      vb.h = pinca.vb0.h * f;
      vb.x = pinca.p0.x - (pinca.p0.x - pinca.vb0.x) * f;
      vb.y = pinca.p0.y - (pinca.p0.y - pinca.vb0.y) * f;
      var cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      var unidade = vb.w / svg.clientWidth;
      vb.x -= (cx - pinca.c0.x) * unidade;
      vb.y -= (cy - pinca.c0.y) * unidade;
      aplicarViewBox();
      pinca.vb0 = { x: vb.x, y: vb.y, w: vb.w, h: vb.h };
      pinca.p0 = pontoSvg({ clientX: cx, clientY: cy });
      pinca.c0 = { x: cx, y: cy };
      pinca.d0 = d;
      return;
    }
    if (!drag) return;
    if (drag.tipo === "pan") {
      var escala = vb.w / svg.clientWidth;
      var dx = (evt.clientX - drag.x0) * escala;
      var dy = (evt.clientY - drag.y0) * escala;
      if (Math.abs(evt.clientX - drag.x0) + Math.abs(evt.clientY - drag.y0) > 3) drag.moveu = true;
      vb.x = drag.vb0.x - dx;
      vb.y = drag.vb0.y - dy;
      aplicarViewBox();
    } else {
      var p = pontoSvg(evt);
      if (Math.hypot(p.x - drag.x0, p.y - drag.y0) > 3) drag.moveu = true;
      if (!drag.moveu) return;
      drag.px = p.x;
      drag.py = p.y;
      if (!drag.raf) {
        drag.raf = requestAnimationFrame(function () {
          var d = drag;
          if (d) {
            d.raf = null;
            moverNo(d.no, d.px, d.py);
            agendarMinimapa();
          }
        });
      }
    }
  });

  svg.addEventListener("pointerup", function (evt) {
    delete ponteiros[evt.pointerId];
    var eraPinca = !!pinca;
    fimPinca();
    try { svg.releasePointerCapture(evt.pointerId); } catch (e) { /* sem captura — nada a soltar */ }
    if (eraPinca) return;
    if (!drag) return;
    var d = drag;
    drag = null;
    if (d.raf) { cancelAnimationFrame(d.raf); d.raf = null; }
    if (d.tipo === "no") {
      if (d.moveu) {
        d.no.fixo = true; // a escolha do investigador vale até recarregar a página
        posMemorizada[d.no.id] = { x: d.no.x, y: d.no.y, fixo: true };
      } else {
        var arestasDoNo = arestasFiltroAtual.filter(function (ar) { return ar.id_a === d.no.id || ar.id_b === d.no.id; });
        abrirModalNo(d.no, arestasDoNo);
      }
    }
  });

  svg.addEventListener("pointercancel", function (evt) {
    delete ponteiros[evt.pointerId];
    fimPinca();
    if (drag && drag.raf) cancelAnimationFrame(drag.raf);
    drag = null;
  });

  svg.addEventListener("lostpointercapture", function (evt) {
    delete ponteiros[evt.pointerId];
    fimPinca();
  });

  // ---------------- orquestração ----------------

  function mostrarTotais(resposta) {
    if (!resposta || !resposta.totais.pares) {
      $totais.hide();
      return;
    }
    var texto = formatarNumero(resposta.totais.pares) + " par(es) de inquéritos · " +
                formatarNumero(resposta.totais.sinais) + " sinal(is)";
    if (resposta.gerado_em) texto += " · sinais calculados em " + resposta.gerado_em;
    if (resposta.recalculando) texto += " · recálculo em andamento";
    $totais.show().text(texto);
  }

  /* Lista, arestas e exportação partem do MESMO estado de filtros; a página atual só importa na
   * lista (o grafo e o Excel não paginam). `aoConcluir` recebe a resposta — cada visão desenha
   * o que é seu. */
  function carregarLista(aoConcluir) {
    var q = filtrosQuery();
    q.ordem = $filtroOrdem.val() || "sinais";
    q.pagina = paginaAtual;
    q.tamanho = PARES_POR_PAGINA;

    $.getJSON(CONV_API, q)
      .done(function (r) {
        ultimaLista = r;
        mostrarTotais(r);
        if (visaoAtual === "lista") renderLista(r);
        reTentarSeCacheNascendo(r, aoConcluir);
      })
      .fail(function () {
        $lista.html('<p class="empty-hint" style="color:var(--danger);">Erro ao carregar convergência.</p>');
        $paginacao.empty().addClass("hidden");
      });
  }

  function carregarGrafo() {
    var q = filtrosQuery();
    $vazioGrafo.addClass("hidden");
    $.getJSON(CONV_API + "/arestas", q)
      .done(function (r) {
        renderGrafo(r.arestas || []);
        if (!(r.arestas || []).length && ultimaLista && ultimaLista.recalculando) {
          $vazioGrafo.removeClass("hidden").text("Primeiro cálculo dos sinais em andamento — o grafo aparece sozinho em um minuto.");
          esperaCacheTimer = setTimeout(renderView, 5000);
        }
      })
      .fail(function () {
        $vazioGrafo.removeClass("hidden").text("Erro ao carregar o grafo de convergência.");
      });
  }

  /* Cache nascendo de verdade (primeira subida pós-migração): a resposta vem com
   * `recalculando` e nada desenhado — em vez de deixar o analista olhando para o vazio, a tela
   * se recarrega sozinha até o primeiro resultado sair. O servidor já foi disparado pela rota;
   * aqui só espera. */
  function reTentarSeCacheNascendo(resposta, aoConcluir) {
    var vazio = !(resposta.pares || []).length;
    if (!(vazio && resposta.recalculando)) {
      clearTimeout(esperaCacheTimer);
      esperaCacheTimer = null;
      if (aoConcluir) aoConcluir();
      return;
    }
    clearTimeout(esperaCacheTimer);
    esperaCacheTimer = setTimeout(function () {
      if (visaoAtual === "lista") carregarLista(); else carregarGrafo();
    }, 5000);
  }

  function renderView() {
    if (visaoAtual === "lista") {
      $lista.removeClass("hidden");
      $grafoArea.addClass("hidden");
      $lista.html('<div style="display:flex; align-items:center; gap:8px; justify-content:center; padding:24px 0;"><span class="spinner"></span> <span>Carregando…</span></div>');
      $paginacao.empty().addClass("hidden");
      carregarLista();
    } else {
      $lista.addClass("hidden");
      $paginacao.empty().addClass("hidden");
      $grafoArea.removeClass("hidden");
      carregarGrafo();
      carregarLista(); // só para o total ("N pares · M sinais") bater com o filtro do grafo
    }
  }

  function refiltrar() {
    paginaAtual = 1;
    comunidadeFocada = null; // o recorte de comunidade é do desenho anterior — não atravessa o filtro novo
    renderView();
  }

  function carregarDelegacias() {
    $.getJSON("/api/delegacias")
      .done(function (lista) {
        var atual = $filtroDelegacia.val();
        $filtroDelegacia.empty().append('<option value="">Todas as delegacias</option>');
        lista.forEach(function (d) {
          $filtroDelegacia.append($("<option>").val(d.no_delegacia).text(d.no_delegacia));
        });
        $filtroDelegacia.val(atual);
      });
  }

  /* "Atualizar" = forçar o recálculo do cache no servidor (a conta leva ~1 minuto e roda em
   * segundo plano; a rota responde na hora). A tela mostra o aviso e volta a buscar sozinha
   * quando o status disser que o recálculo terminou. */
  $atualizarBtn.on("click", function () {
    var $btn = $(this);
    $btn.prop("disabled", true).text("Recalculando…");
    if (window.Toast) Toast.aviso("Recalculando os sinais de convergência — leva cerca de um minuto.");
    $.ajax({ url: CONV_API + "/recalcular", method: "POST" })
      .always(function () { vigiarRecalculo($btn); });
  });

  function vigiarRecalculo($btn) {
    $.getJSON(CONV_API + "/status")
      .done(function (s) {
        if (!s.recalculando) {
          $btn.prop("disabled", false).text("Atualizar");
          refiltrar();
          return;
        }
        setTimeout(function () { vigiarRecalculo($btn); }, 3000);
      })
      .fail(function () {
        $btn.prop("disabled", false).text("Atualizar");
      });
  }

  $filtroTipo.on("change", refiltrar);
  $filtroConfianca.on("change", refiltrar);
  $filtroMinSinais.on("change", refiltrar);
  $filtroOrdem.on("change", refiltrar);
  $filtroDelegacia.on("change", refiltrar);

  // score: número digitado, com espera — "8" no meio do "80" não deve recarregar a lista
  var scoreTimer = null;
  $filtroMinScore.on("input", function () {
    clearTimeout(scoreTimer);
    scoreTimer = setTimeout(refiltrar, 350);
  });

  // digitar filtra no servidor — espera a pausa pra não disparar uma consulta por tecla
  var buscaTimer = null;
  $busca.on("input", function () {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(refiltrar, 300);
  });

  $visaoListaBtn.on("click", function () {
    visaoAtual = "lista";
    $visaoListaBtn.addClass("ativo");
    $visaoGrafoBtn.removeClass("ativo");
    renderView();
  });
  $visaoGrafoBtn.on("click", function () {
    visaoAtual = "grafo";
    $visaoGrafoBtn.addClass("ativo");
    $visaoListaBtn.removeClass("ativo");
    renderView();
  });

  /* Exportações: com a lista paginada no servidor, o navegador não tem mais o recorte inteiro
   * em memória — ele PESA na rota /export, que devolve o conjunto filtrado inteiro, uma linha
   * por sinal. Daí saem tanto o Excel quanto os quatro formatos do i2 (nó = inquérito, vínculo
   * = par com sinais em comum, exatamente como antes). */

  function linhasExportadas() {
    return $.getJSON(CONV_API + "/export", filtrosQuery());
  }

  $("#conv-inq-excel").on("click", function () {
    var $btn = $(this);
    $btn.prop("disabled", true);
    linhasExportadas()
      .done(function (linhas) {
        if (!linhas.length) { window.Toast && Toast.aviso("Nada para exportar."); return; }
        ExportarExcel.exportar("convergencia-inqueritos",
          ["Inquérito A", "Inquérito B", "Tipo do sinal", "Confiança", "Score",
           "Valor", "Entidade no inquérito A", "Entidade no inquérito B"],
          linhas.map(function (l) {
            return [
              String(l.nu_a), String(l.nu_b),
              ROTULO_TIPO[l.tp_sinal] || l.tp_sinal,
              l.tp_confianca === "exata" ? "exata" : "aproximada",
              l.tp_confianca === "exata" ? "" : formatarScore(l.vl_score),
              String(l.ds_valor == null ? "" : l.ds_valor),
              l.no_entidade_a || "", l.no_entidade_b || "",
            ];
          }));
      })
      .fail(function () { if (window.Toast) Toast.erro("Erro ao montar a exportação."); })
      .always(function () { $btn.prop("disabled", false); });
  });

  /* Montagem do recorte i2 a partir das linhas planas do /export (uma por sinal).

     O nó é o INQUÉRITO e o vínculo é o sinal em comum. Vai o MESMO recorte que a tela está
     mostrando (todos os filtros aplicados no servidor), e não a base inteira: quem filtrou para
     "só exata" quer levar para a carta as convergências que ele considera firmes.

     O vínculo sai SIMÉTRICO de propósito: "os dois inquéritos citam o mesmo CPF" não tem origem
     nem destino, e uma seta na carta sugeriria um sentido que o dado não tem. O resumo leva os
     sinais um a um — na carta é ele que responde "em comum o quê", e sem isso o vínculo vira só
     um número. */
  function montarRecorteI2(linhas) {
    var porPar = {};
    linhas.forEach(function (l) {
      var chave = l.nu_a + "\u0001" + l.nu_b;
      var par = porPar[chave];
      if (!par) {
        par = porPar[chave] = { nu_a: l.nu_a, nu_b: l.nu_b, temExata: false, linhas: [] };
      }
      if (l.tp_confianca === "exata") par.temExata = true;
      par.linhas.push(l);
    });

    var inqueritos = {}, entidades = [];
    function registrar(numero) {
      var no = inqueritos[numero];
      if (!no) {
        no = {
          chave: numero, rotulo: String(numero), tipo: "Inquérito", peso: 0,
          atributos: { "Número do inquérito": String(numero), "Pares com sinal em comum": 0, "Sinais em comum": 0 }
        };
        inqueritos[numero] = no;
        entidades.push(no);
      }
      return no;
    }

    var vinculos = Object.keys(porPar).map(function (chave) {
      var par = porPar[chave];
      var noA = registrar(par.nu_a), noB = registrar(par.nu_b);
      noA.peso++; noB.peso++;
      noA.atributos["Pares com sinal em comum"] = noA.peso;
      noB.atributos["Pares com sinal em comum"] = noB.peso;
      noA.atributos["Sinais em comum"] += par.linhas.length;
      noB.atributos["Sinais em comum"] += par.linhas.length;

      var sinais = par.linhas.map(function (l) {
        return (ROTULO_TIPO[l.tp_sinal] || l.tp_sinal) + ": " + l.ds_valor +
               (l.tp_confianca === "exata" ? "" : " (aproximado)");
      });

      // Um atributo por TIPO de sinal, além da lista completa: na carta, "CPF" e "Nome
      // aproximado" no mesmo vínculo têm peso probatório muito diferente, e separá-los deixa o
      // analista filtrar por coluna em vez de ler o texto corrido.
      var atributos = {
        "Sinais em comum": par.linhas.length,
        "Confiança": par.temExata ? "Exata (ao menos um sinal exato)" : "Somente aproximada — hipótese",
        "Inquéritos": par.nu_a + " · " + par.nu_b
      };
      Object.keys(ROTULO_TIPO).forEach(function (tp) {
        var doTipo = par.linhas.filter(function (l) { return l.tp_sinal === tp; });
        if (!doTipo.length) return;
        atributos[ROTULO_TIPO[tp]] = doTipo.map(function (l) {
          var lados = [l.no_entidade_a, l.no_entidade_b].filter(Boolean).join(" ↔ ");
          return l.ds_valor + (lados ? " (" + lados + ")" : "") +
                 (l.tp_confianca === "exata" ? "" : " — aproximado");
        }).slice(0, 25).join(" · ") + (doTipo.length > 25 ? " · …" : "");
      });
      atributos["Todos os sinais"] = sinais.slice(0, 40).join(" · ") + (sinais.length > 40 ? " · …" : "");

      return {
        de: String(par.nu_a), para: String(par.nu_b),
        tipo: par.temExata ? "Convergência exata" : "Convergência aproximada",
        rotulo: par.linhas.length + (par.linhas.length > 1 ? " sinais" : " sinal"),
        peso: par.linhas.length,
        simetrico: true,
        resumo: sinais.slice(0, 40).join(" · ") + (sinais.length > 40 ? " · …" : ""),
        atributos: atributos
      };
    });

    return { prefixo: "convergencia", entidades: entidades, vinculos: vinculos };
  }

  [["#exportar-i2-csv", "csv"], ["#exportar-i2-xlsx", "xlsx"],
   ["#exportar-i2-anx", "anx"], ["#exportar-i2-graphml", "graphml"]].forEach(function (par) {
    var $b = $(par[0]);
    if (!$b.length) return;
    $b.on("click", function () {
      $b.prop("disabled", true);
      linhasExportadas()
        .done(function (linhas) {
          var recorte = montarRecorteI2(linhas);
          if (!recorte.entidades.length) { window.Toast && Toast.aviso("Nada para exportar."); return; }
          ExportarI2[par[1]](recorte);
        })
        .fail(function () { if (window.Toast) Toast.erro("Erro ao montar a exportação."); })
        .always(function () { $b.prop("disabled", false); });
    });
  });

  /* PNG do quadro: print de tela não serve para despacho — recorta o que passa da moldura e
   * deixa a legenda de fora, e é a legenda que explica as cores. O utilitário compartilhado
   * (js/utils/exportar_png.js) reenquadra no conteúdo inteiro, em 2x, com título e legenda.
   * Os rótulos entram à força: sumir de longe é regra de legibilidade DA TELA; no papel, um
   * grafo sem nenhum número não diz nada. */
  $("#conv-exportar-png").on("click", function () {
    if (!window.ExportarPNG) { if (window.Toast) Toast.aviso("Exportação de PNG não disponível nesta tela."); return; }
    if (!nosG.length) { if (window.Toast) Toast.aviso("Grafo vazio: não há o que exportar."); return; }
    var sub = [formatarNumero(nosG.length) + " inquérito(s) · " + formatarNumero(arestasG.length) + " par(es)"];
    var delegacia = $filtroDelegacia.val();
    if (delegacia) sub.push("Delegacia: " + delegacia);
    var busca = ($busca.val() || "").trim();
    if (busca) sub.push("Busca: “" + busca + "”");
    sub.push("Exportado em " + new Date().toLocaleString("pt-BR"));
    window.ExportarPNG.doGrafo(svg, {
      prefixo: "convergencia-grafo",
      titulo: "Convergência entre inquéritos",
      subtitulo: sub,
      legendas: ["#mesa-legenda", "#conv-legenda-comunidades"],
      antes: function () {
        var ocultos = svg.classList.contains("mesa-grafo-rotulos-ocultos");
        var comFoco = svg.classList.contains("mesa-grafo-com-foco");
        svg.classList.remove("mesa-grafo-rotulos-ocultos");
        svg.classList.remove("mesa-grafo-com-foco");
        var visiveis = [];
        if (ocultos) {
          Array.prototype.forEach.call(svg.querySelectorAll(".mesa-no-rotulo, .mesa-aresta-rotulo"), function (t) {
            t.classList.add("rotulo-visivel");
            visiveis.push(t);
          });
        }
        return function () {
          if (ocultos) svg.classList.add("mesa-grafo-rotulos-ocultos");
          if (comFoco) svg.classList.add("mesa-grafo-com-foco");
          visiveis.forEach(function (t) { t.classList.remove("rotulo-visivel"); });
        };
      }
    });
  });

  // ---------- elos críticos e modal de análise ----------

  $("#conv-elos-criticos").on("change", function () {
    destacarElos = this.checked;
    if (arestasFiltroAtual.length) {
      renderLegendaGrafo();
      desenharGrafo(); // só classes e cores: posições não mudam, o layout não é refeito
    }
  });

  function secaoAnalise(titulo, dica) {
    var $h = $("<h3>").addClass("mesa-analise-titulo").text(titulo);
    if (dica) $h.append($("<small>").text(" " + dica));
    return $h;
  }

  function itemNoAnalise($alvo, no, sufixo) {
    var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
      .attr("title", "Abrir os pares deste inquérito");
    $("<span>").addClass("mesa-legenda-bolinha").css("background", corNo(no)).appendTo($b);
    $("<span>").addClass("mesa-analise-nome").text(String(no.nu_inquerito)).appendTo($b);
    if (sufixo) $("<span>").addClass("mesa-analise-meta").text(sufixo).appendTo($b);
    $b.on("click", function () {
      fecharModalAnalise();
      abrirModalNo(no, arestasFiltroAtual.filter(function (ar) { return ar.id_a === no.id || ar.id_b === no.id; }));
    });
    $alvo.append($b);
  }

  function abrirModalAnalise() {
    var $corpo = $("#analise-corpo").empty();
    if (!nosG.length) {
      $corpo.append($("<p>").addClass("card-hint").text("Nenhum inquérito no desenho atual."));
      $("#modal-analise").removeClass("hidden");
      return;
    }
    var elos = obterElosCriticos();
    var artIds = Object.keys(elos.articulacoes);
    var ponteKeys = Object.keys(elos.pontes);

    var tamCom = {};
    nosG.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined || c === null) return;
      tamCom[c] = (tamCom[c] || 0) + 1;
    });
    var comLista = Object.keys(tamCom).map(function (k) { return { indice: parseInt(k, 10), qt: tamCom[k] }; })
      .sort(function (a, b) { return b.qt - a.qt; });

    var sinaisTotais = arestasG.reduce(function (s, a) { return s + a.qt; }, 0);
    var resumo = formatarNumero(nosG.length) + " inquérito(s) · " + formatarNumero(arestasG.length) +
      " par(es) · " + formatarNumero(sinaisTotais) + " sinal(is) · " +
      comLista.length + " comunidade" + (comLista.length === 1 ? "" : "s") + " · " +
      artIds.length + " ponto" + (artIds.length === 1 ? "" : "s") + " de articulação" + " · " +
      ponteKeys.length + " ponte" + (ponteKeys.length === 1 ? "" : "s");
    if (comunidadeFocada !== null) resumo += " · recorte só no grupo " + (comunidadeFocada + 1);
    $("<p>").addClass("mesa-analise-resumo").text(resumo).appendTo($corpo);

    $corpo.append(secaoAnalise("Inquéritos que mais convergem", "os hubs da rede"));
    var hubs = nosG.slice().sort(function (a, b) { return b.grau - a.grau; }).slice(0, 12);
    var $hubs = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
    hubs.forEach(function (n) { itemNoAnalise($hubs, n, n.grau + " par(es)"); });

    $corpo.append(secaoAnalise("Elos críticos · pontos de articulação", "remova e o quadro se parte"));
    if (!artIds.length) {
      $("<p>").addClass("mesa-analise-vazio").text("Nenhum ponto de articulação no desenho atual.").appendTo($corpo);
    } else {
      var $arts = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      artIds.map(function (id) { return noPorId[id]; }).filter(Boolean)
        .sort(function (a, b) { return b.grau - a.grau; }).slice(0, 24)
        .forEach(function (n) { itemNoAnalise($arts, n, n.grau + " par(es)"); });
    }

    $corpo.append(secaoAnalise("Elos críticos · pontes", "par que é a única porta entre dois grupos"));
    if (!ponteKeys.length) {
      $("<p>").addClass("mesa-analise-vazio").text("Nenhuma ponte no desenho atual.").appendTo($corpo);
    } else {
      var porChave = {};
      arestasG.forEach(function (ar) { if (!porChave[ar.chave]) porChave[ar.chave] = ar; });
      var $pontes = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      ponteKeys.forEach(function (k) {
        var ar = porChave[k];
        if (!ar) return;
        var na = noPorId[ar.a], nb = noPorId[ar.b];
        if (!na || !nb) return;
        var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
          .attr("title", "Abrir os sinais deste par");
        var $linha = $("<span>").addClass("mesa-legenda-linha")
          .css("background", ar.temExata ? COR_ARESTA_EXATA : COR_ARESTA_APROXIMADA);
        if (!ar.temExata) $linha.addClass("tracejada-longa");
        $linha.appendTo($b);
        $("<span>").addClass("mesa-analise-nome").text(na.nu_inquerito + " ⟷ " + nb.nu_inquerito).appendTo($b);
        $("<span>").addClass("mesa-analise-meta").text(ar.qt + " sinal(is)").appendTo($b);
        $b.on("click", function () { fecharModalAnalise(); abrirModalPar(ar.par); });
        $pontes.append($b);
      });
    }

    $corpo.append(secaoAnalise("Comunidades", "clique para isolar o grupo no quadro"));
    if (!comLista.length) {
      $("<p>").addClass("mesa-analise-vazio").text("Sem comunidades no desenho atual.").appendTo($corpo);
    } else {
      var $coms = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      comLista.forEach(function (g) {
        var focoAqui = comunidadeFocada === g.indice;
        var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
          .toggleClass("ativo", focoAqui)
          .attr("title", focoAqui ? "Mostrar todas de novo" : "Isolar este grupo");
        $("<span>").addClass("mesa-legenda-bolinha")
          .css("background", CORES_COMUNIDADE[g.indice % CORES_COMUNIDADE.length]).appendTo($b);
        $("<span>").addClass("mesa-analise-nome").text("Grupo " + (g.indice + 1)).appendTo($b);
        $("<span>").addClass("mesa-analise-meta").text(g.qt + " inquérito" + (g.qt === 1 ? "" : "s")).appendTo($b);
        $b.on("click", function () {
          comunidadeFocada = focoAqui ? null : g.indice;
          chaveComunidadeFoco = comunidadeFocada === null ? null : cacheComunidades.chave;
          fecharModalAnalise();
          reRenderizar();
        });
        $coms.append($b);
      });
    }

    $("#modal-analise").removeClass("hidden");
  }

  function fecharModalAnalise() { $("#modal-analise").addClass("hidden"); }
  $("#conv-analise-btn").on("click", abrirModalAnalise);
  $("#analise-fechar").on("click", fecharModalAnalise);
  $("#modal-analise").on("click", function (e) { if (e.target === this) fecharModalAnalise(); });

  $("#par-fechar").on("click", function () { $modalPar.addClass("hidden"); });
  $modalPar.on("click", function (e) { if (e.target === this) $modalPar.addClass("hidden"); });

  iniciarMinimapa();
  carregarDelegacias();
  renderView();
})();
