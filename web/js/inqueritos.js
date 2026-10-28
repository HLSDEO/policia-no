/* js/inqueritos.js */
(function () {
  "use strict";

  var ITENS_POR_PAGINA_PADRAO = 12;
  var ITENS_POR_PAGINA_OPCOES = [12, 24, 48];
  var CHAVE_POR_PAGINA = "analyser-inqueritos-por-pagina";
  var itensPorPagina = ITENS_POR_PAGINA_PADRAO;
  var DEBOUNCE_MS = 260;

  var inqueritosTodos = [];
  var inqueritosFiltrados = [];
  var paginaAtual = 0; // carrossel index
  var paginaLista = 1;

  var $busca = $("#inq-busca");
  var $filtroTipo = $("#inq-filtro-tipo");
  var $filtroStatus = $("#inq-filtro-status");
  var $filtroDelegacia = $("#inq-filtro-delegacia");
  var $filtroOrdem = $("#inq-ordem");
  var $filtroPorPagina = $("#inq-por-pagina");
  var $btnLimpar = $("#inq-limpar");
  var $btnLimparVazio = $("#lista-limpar-filtros");
  var $vazioTexto = $("#lista-vazio-texto");
  var $track = $("#carousel-track");
  var $indicators = $("#carousel-indicators");
  var $btnPrev = $("#carousel-prev");
  var $btnNext = $("#carousel-next");
  var $vazio = $("#carousel-vazio");
  var $statsCard = $("#stats-card");
  var $statsSkeleton = $("#stats-skeleton");
  var $statsErro = $("#stats-erro");
  var $statInqueritos = $("#stat-inqueritos");
  var $statImportacoes = $("#stat-importacoes");
  var $statMensagens = $("#stat-mensagens");
  var $statMarcadores = $("#stat-relatorios");
  var $statCasos = $("#stat-casos");
  var $modoBtns = $(".inv-modo-btn");
  var $carrosselView = $("#carrossel-view");
  var $listaView = $("#lista-view");
  var $lista = $("#inq-lista");
  var $listaVazio = $("#lista-vazio");
  var $listaCarregando = $("#lista-carregando");
  var $listaPaginacao = $("#lista-paginacao");
  var $listaPrev = $("#lista-prev");
  var $listaNext = $("#lista-next");
  var $listaFirst = $("#lista-first");
  var $listaLast = $("#lista-last");
  var $pagInfo = $("#lista-pag-info");
  var $total = $("#inq-total");
  var $totalTopo = $("#inq-total-topo");
  var $carregandoGeral = $("#inqueritos-carregando");
  var $erroGeral = $("#inqueritos-erro");

  var CHAVE_MODO = "analyser-inqueritos-modo";
  var modoVisualizacao = "lista";
  var estadoInicialURL = lerEstadoURL();

  var U = window.InqueritoUtils;

  // ---- Modo inicial: URL > localStorage > default lista (1.1) ----
  function lerModoSalvo() {
    if (estadoInicialURL.modo === "carrossel" || estadoInicialURL.modo === "lista") return estadoInicialURL.modo;
    try {
      var salvo = localStorage.getItem(CHAVE_MODO);
      if (salvo === "carrossel" || salvo === "lista") return salvo;
    } catch (e) {}
    return "lista";
  }
  modoVisualizacao = lerModoSalvo();

  function salvarModo(modo) {
    try { localStorage.setItem(CHAVE_MODO, modo); } catch (e) {}
  }

  function limparIdInquerito(nu) {
    if (!nu) return "";
    return nu.replace(/[\.\-]/g, "");
  }

  function lerPorPaginaSalvo() {
    var daURL = estadoInicialURL.porPagina;
    if (ITENS_POR_PAGINA_OPCOES.indexOf(daURL) !== -1) return daURL;
    try {
      var salvo = parseInt(localStorage.getItem(CHAVE_POR_PAGINA) || "", 10);
      if (ITENS_POR_PAGINA_OPCOES.indexOf(salvo) !== -1) return salvo;
    } catch (e) {}
    return ITENS_POR_PAGINA_PADRAO;
  }

  function salvarPorPagina(n) {
    try { localStorage.setItem(CHAVE_POR_PAGINA, String(n)); } catch (e) {}
  }

  // ---- URL sync (1.4) ----
  function lerEstadoURL() {
    try {
      var p = new URLSearchParams(window.location.search);
      var porPag = parseInt(p.get("por_pagina") || "", 10);
      return {
        q: p.get("q") || "",
        status: p.get("status") || "todos",
        delegacia: p.get("delegacia") || "",
        tipo: p.get("tipo") || "",
        ordem: p.get("ordem") || "recente",
        modo: p.get("modo") || null,
        pagina: Math.max(1, parseInt(p.get("pagina") || "1", 10) || 1),
        porPagina: ITENS_POR_PAGINA_OPCOES.indexOf(porPag) !== -1 ? porPag : ITENS_POR_PAGINA_PADRAO
      };
    } catch (e) {
      return { q: "", status: "todos", delegacia: "", tipo: "", ordem: "recente", modo: null, pagina: 1, porPagina: ITENS_POR_PAGINA_PADRAO };
    }
  }

  function sincronizarURL() {
    try {
      var params = new URLSearchParams();
      var q = ($busca.val() || "").trim();
      if (q) params.set("q", q);
      var status = $filtroStatus.val();
      if (status && status !== "todos") params.set("status", status);
      var delegacia = $filtroDelegacia.val();
      if (delegacia) params.set("delegacia", delegacia);
      var tipo = $filtroTipo.val();
      if (tipo) params.set("tipo", tipo);
      var ordem = $filtroOrdem.val();
      if (ordem && ordem !== "recente") params.set("ordem", ordem);
      if (modoVisualizacao !== "lista") params.set("modo", modoVisualizacao);
      if (modoVisualizacao === "lista" && paginaLista > 1) params.set("pagina", String(paginaLista));
      if (itensPorPagina !== ITENS_POR_PAGINA_PADRAO) params.set("por_pagina", String(itensPorPagina));
      var qs = params.toString();
      var novo = window.location.pathname + (qs ? "?" + qs : "") + (window.location.hash || "");
      history.replaceState(null, "", novo);
    } catch (e) {}
  }

  function aplicarEstadoURLNaUI() {
    if (estadoInicialURL.q) $busca.val(estadoInicialURL.q);
    // links antigos (?status=andamento|concluido) apontam para o filtro velho de processamento —
    // ignore em vez de deixar o select sem seleção nenhuma
    if (estadoInicialURL.status && ["todos", "ativo", "arquivado"].indexOf(estadoInicialURL.status) !== -1) {
      $filtroStatus.val(estadoInicialURL.status);
    }
    if (estadoInicialURL.tipo) $filtroTipo.val(estadoInicialURL.tipo);
    if (estadoInicialURL.ordem) $filtroOrdem.val(estadoInicialURL.ordem);
    paginaLista = estadoInicialURL.pagina || 1;
    itensPorPagina = lerPorPaginaSalvo();
    if ($filtroPorPagina && $filtroPorPagina.length) $filtroPorPagina.val(String(itensPorPagina));
  }

  function temFiltroAtivo() {
    return !!(
      ($busca.val() || "").trim() ||
      ($filtroTipo.val() || "") !== "" ||
      ($filtroStatus.val() || "") !== "todos" ||
      ($filtroDelegacia.val() || "") !== "" ||
      ($filtroOrdem.val() || "recente") !== "recente"
    );
  }

  function limparFiltros() {
    $busca.val("");
    $filtroTipo.val("");
    $filtroStatus.val("todos");
    $filtroDelegacia.val("");
    $filtroOrdem.val("recente");
    paginaLista = 1;
    paginaAtual = 0;
    aplicarFiltros();
    atualizarStatsAtivos();
    try { $busca.focus(); } catch (e) {}
  }

  function debounce(fn, wait) { return AnalyserUtils.debounce(fn, wait); }

  // ---- IA buttons ----
  function botaoIa(inq) {
    var $btn = $("<button>").addClass("btn btn-ia").html(U.ICONE_IA + " IA");
    if (inq.ia_ativa === false) {
      return $btn
        .css({ opacity: 0.4, cursor: "not-allowed" })
        .attr("disabled", true)
        .attr("title", "IA desativada para " + (inq.delegacia || "esta delegacia"));
    }
    return $btn.on("click", function () {
      window.location.href = "ia.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
    });
  }

  var ICONE_MAIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
  // pasta aberta: botão "Abrir Caso/IPL" (substitui o emoji 📂)
  var ICONE_PASTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  // ampulheta: selo de extração em andamento (substitui o emoji ⏳)
  var ICONE_ANDAMENTO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 2h12"/><path d="M6 22h12"/><path d="M6 2v3c0 3 6 4.5 6 7s-6 4-6 7v3"/><path d="M18 2v3c0 3-6 4.5-6 7s6 4 6 7v3"/></svg>';
  // visto: selo de inquérito processado (substitui o ✓)
  var ICONE_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
  // triângulo de alerta: estado de erro das estatísticas (substitui o ⚠)
  var ICONE_ALERTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  function botaoImportar(item) {
    var $btn = $("<button>").addClass("btn btn-importar").attr("type", "button").attr("title", "Importar dados para este inquérito").html(ICONE_MAIS + " Importar");
    return $btn.on("click", function (e) {
      e.stopPropagation();
      if (window.abrirModalImportar) window.abrirModalImportar(item);
    });
  }

  // ---- Carregamento com skeleton (1.5) ----
  function mostrarCarregandoInicial() {
    $statsSkeleton.removeClass("hidden").attr("aria-hidden", "false");
    $statsCard.addClass("hidden").attr("hidden", true).attr("aria-busy", "true");
    $statsErro.addClass("hidden").empty();
    $carregandoGeral.removeClass("hidden").removeAttr("hidden");
    $erroGeral.addClass("hidden").empty().attr("hidden", true);
    $listaCarregando.removeClass("hidden").removeAttr("hidden");
    $total.text("Carregando…");
    if ($totalTopo && $totalTopo.length) $totalTopo.text("Carregando…");
  }

  function esconderCarregandoInicial() {
    $statsSkeleton.addClass("hidden").attr("aria-hidden", "true");
    $carregandoGeral.addClass("hidden").attr("hidden", true);
    $listaCarregando.addClass("hidden").attr("hidden", true);
  }

  function carregarInqueritos() {
    mostrarCarregandoInicial();
    var deferredInq = $.getJSON("/api/investigacao/inqueritos");

    return deferredInq
      .done(function (listaInq) {
        var lista = listaInq || [];

        inqueritosTodos = lista.map(function (inq) {
          inq.tipo = "inquerito";
          inq.id_item = inq.id_inquerito;
          inq.nome_item = inq.nu_inquerito;
          inq.dt_criacao_ref = inq.dt_ultima_atualizacao || inq.dt_atualizacao || inq.dt_criacao;
          return inq;
        });

        $erroGeral.addClass("hidden").attr("hidden", true).empty();
        preencherDelegacias();
        // reaplica delegacia da URL se ainda não estava nas opções
        if (estadoInicialURL.delegacia && !$filtroDelegacia.val()) {
          // se valor da URL não existe nas opções, mantém vazio
        }
        aplicarFiltros();
        var delegacia = $filtroDelegacia.val();
        carregarEstatisticas(delegacia);
      })
      .fail(function (xhr) {
        esconderCarregandoInicial();
        var msg = xhr.responseJSON && xhr.responseJSON.detail ? xhr.responseJSON.detail : (xhr.statusText || "Erro desconhecido");
        $erroGeral.removeClass("hidden").removeAttr("hidden")
          .html('<div class="estado-erro" role="alert"><p class="estado-titulo">Não foi possível carregar as investigações</p><p class="estado-dica">' + $("<span>").text(msg).html() + '</p><button type="button" class="btn btn-secondary estado-acao" id="btn-tentar-novamente">Tentar de novo</button></div>');
        $erroGeral.find("#btn-tentar-novamente").on("click", function () { carregarInqueritos(); });
        $total.text("Erro ao carregar");
        if (window.Toast) Toast.erro("Erro ao carregar investigações: " + msg);
        esconderCarregandoInicial();
        $carrosselView.addClass("hidden").attr("hidden", true).attr("aria-hidden", "true");
        $listaView.addClass("hidden").attr("hidden", true).attr("aria-hidden", "true");
      })
      .always(function () {
        esconderCarregandoInicial();
      });
  }

  // ---- Delegacias com contagem (2.7) ----
  function preencherDelegacias() {
    var cont = {};
    inqueritosTodos.forEach(function (i) {
      var d = i.delegacia || "—";
      cont[d] = (cont[d] || 0) + 1;
    });
    var delegacias = Object.keys(cont).sort(function (a, b) { return a.localeCompare(b, "pt-BR"); });
    var atual = $filtroDelegacia.val();
    // se ainda não tem valor (primeira carga), tenta URL
    if (!atual && estadoInicialURL.delegacia) atual = estadoInicialURL.delegacia;
    $filtroDelegacia.empty().append('<option value="">Todas as delegacias (' + inqueritosTodos.length + ')</option>');
    delegacias.forEach(function (d) {
      var label = d + " (" + cont[d] + ")";
      // escapa valor para atributo
      var escVal = String(d).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
      var escLabel = String(label).replace(/</g, "&lt;");
      $filtroDelegacia.append('<option value="' + escVal + '">' + escLabel + '</option>');
    });
    if (atual !== null && atual !== undefined) {
      // se valor não existe mais, volta para todas
      var existe = atual === "" || delegacias.indexOf(atual) !== -1;
      $filtroDelegacia.val(existe ? atual : "");
    }
  }

  function carregarEstatisticas(delegacia) {
    $statsErro.addClass("hidden").empty();
    $statsSkeleton.removeClass("hidden");
    $statsCard.addClass("hidden").attr("hidden", true);
    var url = "/api/investigacao/estatisticas-delegacia";
    if (delegacia) {
      url += "?delegacia=" + encodeURIComponent(delegacia);
    }
    $.getJSON(url)
      .done(function (stats) {
        if (stats) {
          $statInqueritos.text(U.formatarNumero(stats.qt_inqueritos || 0));
          $statImportacoes.text(U.formatarNumero(stats.qt_importacoes_processadas || 0));
          $statMensagens.text(U.formatarNumero(stats.qt_mensagens_importadas || 0));
          $statMarcadores.text(U.formatarNumero(stats.qt_marcadores_realizados || 0));
          if ($statCasos && $statCasos.length) {
            $statCasos.text(U.formatarNumero(stats.qt_casos || 0));
          }
          $statsSkeleton.addClass("hidden");
          $statsCard.removeClass("hidden").removeAttr("hidden").attr("aria-busy", "false");
          $statsErro.addClass("hidden").empty();
        } else {
          $statsSkeleton.addClass("hidden");
        }
      })
      .fail(function (xhr) {
        $statsSkeleton.addClass("hidden");
        var msg = xhr.responseJSON && xhr.responseJSON.detail ? xhr.responseJSON.detail : "Não foi possível carregar as estatísticas.";
        $statsErro.removeClass("hidden").removeAttr("hidden")
          .html('<div class="estado-erro"><span class="estado-icone" aria-hidden="true">' + ICONE_ALERTA + '</span><p class="estado-titulo">' + $("<span>").text(msg).html() + '</p><button type="button" class="btn btn-secondary btn-small" id="stats-tentar">Tentar de novo</button></div>');
        $statsErro.find("#stats-tentar").on("click", function () { carregarEstatisticas($filtroDelegacia.val()); });
        $statsCard.addClass("hidden").attr("hidden", true);
      });
  }

  // ---- Fases / progresso real (2.2) ----
  function textoFase(fase) {
    if (!fase) return "";
    var map = {
      hasheando: "Calculando hash",
      indexando: "Indexando arquivos",
      extraindo: "Extraindo dados",
      contagens: "Consolidando",
      identificando_pessoas: "Identificando pessoas",
      transcrevendo: "Transcrevendo áudios",
      indexando_semantica: "Indexação semântica",
      cancelando: "Cancelando"
    };
    return map[fase] || String(fase).replace(/_/g, " ");
  }

  function progressoInfo(item) {
    var emAndamento = !!item.tem_extracao_em_andamento;
    if (!emAndamento) {
      return { texto: "Processado", pct: 100, mostrarBarra: true, indeterminado: false, concluido: true };
    }
    var fase = item.fase_em_andamento || item.cd_fase || null;
    var pct = item.progresso_em_andamento;
    if (pct === undefined) pct = item.qt_progresso_fase;
    if (pct != null) {
      pct = parseInt(pct, 10);
      if (isNaN(pct)) pct = null;
      else pct = Math.max(0, Math.min(99, pct));
    }
    var faseTxt = textoFase(fase);
    if (pct != null && faseTxt) {
      return { texto: pct + "% — " + faseTxt, pct: pct, mostrarBarra: true, indeterminado: false, concluido: false };
    }
    if (pct != null) {
      return { texto: pct + "% em processamento", pct: pct, mostrarBarra: true, indeterminado: false, concluido: false };
    }
    if (faseTxt) {
      return { texto: faseTxt + "…", pct: null, mostrarBarra: false, indeterminado: true, concluido: false };
    }
    return { texto: "Em andamento", pct: null, mostrarBarra: false, indeterminado: true, concluido: false };
  }

  // ---- Ordenação (2.4) ----
  function ordenarInvestigacoes(lista, ordem) {
    var copia = lista.slice();
    switch (ordem) {
      case "recente":
        copia.sort(function (a, b) { return new Date(b.dt_criacao_ref || 0) - new Date(a.dt_criacao_ref || 0); });
        break;
      case "antigo":
        copia.sort(function (a, b) { return new Date(a.dt_criacao_ref || 0) - new Date(b.dt_criacao_ref || 0); });
        break;
      case "nome_az":
        copia.sort(function (a, b) { return String(a.nome_item || "").localeCompare(String(b.nome_item || ""), "pt-BR"); });
        break;
      case "nome_za":
        copia.sort(function (a, b) { return String(b.nome_item || "").localeCompare(String(a.nome_item || ""), "pt-BR"); });
        break;
      case "mais_dados":
        function totalDados(x) {
          return (x.qt_extracoes || 0) + (x.qt_simba || 0) + (x.qt_rifs || 0) + (x.qt_ipls || 0) + (x.qt_irpfs || 0) + (x.qt_ipeis || 0) + (x.qt_documentos || 0);
        }
        copia.sort(function (a, b) { return totalDados(b) - totalDados(a); });
        break;
      default:
        break;
    }
    return copia;
  }

  function aplicarFiltros() {
    var termo = ($busca.val() || "").toLowerCase().trim();
    var status = $filtroStatus.val();
    var delegacia = $filtroDelegacia.val();
    var tipo = $filtroTipo.val();
    var ordem = $filtroOrdem.val() || "recente";

    var filtrados = inqueritosTodos.filter(function (item) {
      var eCaso = !!item.in_caso_inteligencia;
      if (tipo === "caso" && !eCaso) return false;
      if (tipo === "inquerito" && eCaso) return false;
      if (termo) {
        var busca = (item.nome_item || "") + " " + (item.delegacia || "") + " " + (item.administrador || "") + " " + (eCaso ? "caso caso de inteligencia" : "inquerito ipl");
        if (!busca.toLowerCase().includes(termo)) return false;
      }
      if (status === "ativo" && item.tp_situacao === "arquivado") return false;
      if (status === "arquivado" && item.tp_situacao !== "arquivado") return false;
      if (delegacia && item.delegacia !== delegacia) return false;
      return true;
    });

    filtrados = ordenarInvestigacoes(filtrados, ordem);
    inqueritosFiltrados = filtrados;

    // corrige paginaLista se saiu do range
    var totalPaginas = Math.max(1, Math.ceil(inqueritosFiltrados.length / itensPorPagina));
    if (paginaLista > totalPaginas) paginaLista = totalPaginas;
    if (paginaLista < 1) paginaLista = 1;

    render();
    atualizarTotal();
    atualizarStatsAtivos();
    sincronizarURL();
  }

  function render() {
    if (modoVisualizacao === "lista") renderLista();
    else renderCarousel();
  }

  function atualizarTotal() {
    var total = inqueritosFiltrados.length;
    var qCaso = inqueritosFiltrados.filter(function (x) { return !!x.in_caso_inteligencia; }).length;
    var qInq = total - qCaso;
    var ordemTxt = $filtroOrdem.find("option:selected").text() || "";
    var html;
    if (!total) {
      html = 'Nenhuma investigação encontrada';
    } else {
      var partes = [];
      partes.push('<strong>' + U.formatarNumero(total) + '</strong> investigaç' + (total !== 1 ? 'ões' : 'ão'));
      if (qInq || qCaso) {
        partes.push('(' + qInq + ' inquérito' + (qInq !== 1 ? 's' : '') + ', ' + qCaso + ' caso' + (qCaso !== 1 ? 's' : '') + ' de inteligência)');
      }
      if (modoVisualizacao === "lista") {
        var totalPag = Math.max(1, Math.ceil(total / itensPorPagina));
        partes.push('· página ' + paginaLista + ' de ' + totalPag);
        if (ordemTxt) partes.push('· ' + $("<span>").text(ordemTxt).html());
      } else {
        if (ordemTxt) partes.push('· ' + $("<span>").text(ordemTxt).html());
      }
      html = partes.join(" ");
    }
    $total.html(html);
    if ($totalTopo && $totalTopo.length) $totalTopo.html(html);
    if ($btnLimpar && $btnLimpar.length) $btnLimpar.prop("disabled", !temFiltroAtivo());
  }

  // Stats clicáveis: Inquéritos (todos) e Casos filtram por tipo.
  function atualizarStatsAtivos() {
    var tipo = $filtroTipo.val() || "";
    $('[data-stat-filtro]').each(function () {
      var filtro = $(this).attr("data-stat-filtro");
      var ativo = (filtro === "todos" && tipo === "") || (filtro === tipo);
      $(this).toggleClass("ativo", ativo).attr("aria-pressed", String(ativo));
    });
  }

  function renderCarousel() {
    $track.empty();
    $indicators.empty();

    $carrosselView.toggleClass("carrossel-unico", inqueritosFiltrados.length <= 1);

    if (!inqueritosFiltrados.length) {
      $vazio.removeClass("hidden").removeAttr("hidden").text($busca.val() || $filtroStatus.val() !== "todos" || $filtroDelegacia.val() || $filtroTipo.val() ? "Nenhum resultado para este filtro. Tente ajustar os filtros." : "Nenhuma investigação encontrada.");
      $btnPrev.addClass("hidden").prop("disabled", true).attr("hidden", true);
      $btnNext.addClass("hidden").prop("disabled", true).attr("hidden", true);
      $indicators.addClass("hidden").attr("hidden", true);
      return;
    }

    $vazio.addClass("hidden").attr("hidden", true);
    $btnPrev.removeClass("hidden").removeAttr("hidden");
    $btnNext.removeClass("hidden").removeAttr("hidden");

    // Com muitos itens, um dot por card vira centenas de botões — mostra texto "X de Y".
    var MAX_DOTS = 20;
    if (inqueritosFiltrados.length > MAX_DOTS) {
      $indicators.addClass("hidden").attr("hidden", true).empty();
    } else {
      $indicators.removeClass("hidden").removeAttr("hidden");
    }

    inqueritosFiltrados.forEach(function (inq, idx) {
      var $card = criarCard(inq);
      $card.attr("data-index", idx)
           .attr("role", "listitem")
           .attr("aria-label", (idx + 1) + " de " + inqueritosFiltrados.length + " — " + (inq.nome_item || "") + " — " + (inq.tem_extracao_em_andamento ? "em andamento" : "processado"))
           .attr("aria-roledescription", "slide");
      $track.append($card);

      if (inqueritosFiltrados.length <= MAX_DOTS) {
        var $dot = $("<button>")
          .addClass("carousel-indicator")
          .attr("type", "button")
          .attr("data-index", idx)
          .attr("role", "tab")
          .attr("aria-label", "Ir para " + (inq.in_caso_inteligencia ? "caso de inteligência " : "inquérito ") + (idx + 1) + " de " + inqueritosFiltrados.length)
          .attr("aria-selected", "false");
        $indicators.append($dot);
      }
    });

    paginaAtual = 0;
    irPara(0, true);
  }

  function criarCard(item) {
    // Caso de inteligência usa a mesma estrutura do inquérito (mesmas fontes e mesmos dados) —
    // só o rótulo e o selo mudam.
    var eCaso = !!item.in_caso_inteligencia;
    var $card = $("<article>").addClass("carousel-card" + (eCaso ? " caso-card" : "")).attr("data-id-item", item.id_item).attr("data-tipo", eCaso ? "caso" : "inquerito");

    // Header: tipo badge + delegacia + status
    var $header = $("<div>").addClass("inq-card-header").appendTo($card);
    var $idWrap = $("<span>").css({ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }).appendTo($header);
    $("<span>").addClass("inq-id").text("DELEGACIA " + (item.delegacia || "—")).appendTo($idWrap);
    $("<span>").addClass("inq-tipo-badge " + (eCaso ? "caso" : "inquerito")).text(eCaso ? "CASO DE INTELIGÊNCIA" : "INQUÉRITO").appendTo($idWrap);
    // Selo da situação do caso (o "status" do filtro): arquivado é visível, ativo é o silêncio.
    if (item.tp_situacao === "arquivado") {
      $("<span>").addClass("badge badge-neutro").attr("title", "Inquérito arquivado pelo gestor — o caso segue íntegro e navegável").text("ARQUIVADO").appendTo($idWrap);
    }

    var prog = progressoInfo(item);
    var statusClass = item.tem_extracao_em_andamento ? "andamento" : "processado";
    $("<span>").addClass("inq-status " + statusClass).attr("title", prog.texto)
      .html((item.tem_extracao_em_andamento ? ICONE_ANDAMENTO : ICONE_OK) + " " + $("<span>").text(prog.texto).html())
      .appendTo($header);

    // Body
    var $body = $("<div>").addClass("inq-body").appendTo($card);
    $("<div>").addClass("inq-title-full")
      .html((eCaso ? "CASO" : "IPL") + '<br><strong>' + $("<span>").text(limparIdInquerito(item.nu_inquerito) || "—").html() + '</strong>')
      .appendTo($body);

    // Meta
    var $metaGrade = $("<div>").addClass("inq-meta").appendTo($card);

    var $resp = $("<div>").addClass("inq-meta-item inq-meta-full").appendTo($metaGrade);
    $("<div>").addClass("inq-meta-label").text("RESPONSÁVEL").appendTo($resp);
    $("<div>").addClass("inq-meta-value").text(item.administrador || "—").appendTo($resp);

    // Resumo: quebrado em 6 para inquéritos (2.3)
    var $summary = $("<div>").addClass("inq-summary").appendTo($card);
    $("<h3>").addClass("inq-summary-title").text("RESUMO DOS DADOS PROCESSADOS").appendTo($summary);
    var $summaryGrade = $("<div>").addClass("inq-summary-grade").appendTo($summary);

    criarMiniCardSimples($summaryGrade, U.ICONE_DISPOSITIVO, "ufdr", "UFDR", item.qt_extracoes || 0, "extrações", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_SIMBA, "simba", "SIMBA", item.qt_simba || 0, "extratos", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_RIF, "rif", "RIF", item.qt_rifs || 0, "RIFs", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_DOCUMENTO, "ipl", "IPL", item.qt_ipls || 0, "docs", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_DOCUMENTO, "irpf", "IRPF", item.qt_irpfs || 0, "declarações", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_DOCUMENTO, "ipei", "Sigilo fiscal", item.qt_ipeis || 0, "IPEIs", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_TELEFONIA, "telefonia", "Telefonia", item.qt_telefonia || 0, "operadoras", item);
    criarMiniCardSimples($summaryGrade, U.ICONE_DOCUMENTO, "documentos", "Documentos", item.qt_documentos || 0, "arquivos", item);
    $summaryGrade.addClass("inq-summary-grade--quebrado");

    // Footer
    var $footer = $("<div>").addClass("inq-footer").appendTo($card);
    $("<button>").addClass("btn btn-primary")
      .html(ICONE_PASTA + (eCaso ? " Abrir Caso" : " Abrir IPL"))
      .on("click", function () { window.location.href = "investigacao.html?inquerito=" + encodeURIComponent(item.id_inquerito); })
      .appendTo($footer);
    botaoIa(item).appendTo($footer);
    $("<button>").addClass("btn btn-mesa")
      .attr("title", "Mesa de inquérito")
      .html(U.ICONE_MESA + ' <span>Mesa<span class="btn-rotulo-extra"> de inquérito</span></span>')
      .on("click", function () { window.location.href = "mesa_inquerito.html?inquerito=" + encodeURIComponent(item.id_inquerito); })
      .appendTo($footer);
    botaoImportar(item).appendTo($footer);

    return $card;
  }

  function renderLista() {
    $lista.empty();

    if (!inqueritosFiltrados.length) {
      var filtrado = temFiltroAtivo();
      if ($vazioTexto && $vazioTexto.length) {
        $vazioTexto.text(filtrado ? "Nenhum resultado para este filtro. Tente ajustar ou limpar os filtros." : "Nenhuma investigação encontrada.");
      } else {
        $listaVazio.text(filtrado ? "Nenhum resultado para este filtro. Tente ajustar ou limpar os filtros." : "Nenhuma investigação encontrada.");
      }
      $listaVazio.removeClass("hidden").removeAttr("hidden");
      if ($btnLimparVazio && $btnLimparVazio.length) $btnLimparVazio.toggleClass("hidden", !filtrado);
      $listaPaginacao.addClass("hidden").attr("hidden", true);
      return;
    }

    $listaVazio.addClass("hidden").attr("hidden", true);

    var totalPaginas = Math.max(1, Math.ceil(inqueritosFiltrados.length / itensPorPagina));
    if (paginaLista > totalPaginas) paginaLista = totalPaginas;
    var inicio = (paginaLista - 1) * itensPorPagina;
    var fim = Math.min(inicio + itensPorPagina, inqueritosFiltrados.length);
    var fatia = inqueritosFiltrados.slice(inicio, fim);

    var fragment = document.createDocumentFragment();
    fatia.forEach(function (inq) {
      var el = criarLinhaLista(inq)[0];
      fragment.appendChild(el);
    });
    $lista[0].appendChild(fragment);

    // paginação
    if (totalPaginas > 1) {
      $listaPaginacao.removeClass("hidden").removeAttr("hidden");
      $pagInfo.text("Mostrando " + (inicio + 1) + "–" + fim + " de " + inqueritosFiltrados.length + " · página " + paginaLista + " de " + totalPaginas);
      $listaPrev.prop("disabled", paginaLista <= 1);
      $listaNext.prop("disabled", paginaLista >= totalPaginas);
      if ($listaFirst && $listaFirst.length) $listaFirst.prop("disabled", paginaLista <= 1);
      if ($listaLast && $listaLast.length) $listaLast.prop("disabled", paginaLista >= totalPaginas);
    } else {
      $listaPaginacao.addClass("hidden").attr("hidden", true);
    }
  }

  function criarLinhaLista(item) {
    var eCaso = !!item.in_caso_inteligencia;
    var prog = progressoInfo(item);
    var $linha = $("<article>")
      .addClass("investigacao-card inq-lista-card" + (eCaso ? " caso-card" : ""))
      .attr("data-id-item", item.id_item)
      .attr("data-tipo", eCaso ? "caso" : "inquerito")
      .attr("role", "listitem")
      .attr("aria-label", (eCaso ? "Caso de inteligência " : "Inquérito ") + (item.nome_item || "") + " — " + prog.texto);

    var $principal = $("<div>").addClass("inq-lista-principal").appendTo($linha);

    var $topo = $("<div>").addClass("inq-lista-topo").appendTo($principal);
    $("<span>").addClass("inq-lista-numero")
      .html((eCaso ? '<small>CASO</small>' : '<small>IPL</small>') + $("<span>").text(limparIdInquerito(item.nu_inquerito) || "—").html())
      .appendTo($topo);
    $("<span>").addClass("inq-tipo-badge " + (eCaso ? "caso" : "inquerito")).text(eCaso ? "CASO DE INTELIGÊNCIA" : "INQUÉRITO").appendTo($topo);
    if (item.tp_situacao === "arquivado") {
      $("<span>").addClass("badge badge-neutro").attr("title", "Inquérito arquivado pelo gestor — o caso segue íntegro e navegável").text("ARQUIVADO").appendTo($topo);
    }
    var status = item.tem_extracao_em_andamento ? "andamento" : "processado";
    $("<span>").addClass("inq-status " + status).attr("title", prog.texto)
      .html((item.tem_extracao_em_andamento ? ICONE_ANDAMENTO : ICONE_OK) + " " + $("<span>").text(prog.texto).html())
      .appendTo($topo);

    var $meta = $("<div>").addClass("inq-lista-meta").appendTo($principal);
    metaItem($meta, "Delegacia", item.delegacia || "—");
    metaItem($meta, "Responsável", item.administrador || "—");
    metaItem($meta, "Tipo", eCaso ? "Caso de inteligência" : "Inquérito");
    metaItem($meta, "Última atualização", formatarDataHora(item.dt_criacao_ref));

    var $contadores = $("<div>").addClass("inq-lista-contadores").appendTo($principal);
    contador($contadores, U.ICONE_DISPOSITIVO, "ufdr", "UFDR", item.qt_extracoes || 0, item);
    contador($contadores, U.ICONE_SIMBA, "simba", "SIMBA", item.qt_simba || 0, item);
    contador($contadores, U.ICONE_RIF, "rif", "RIF", item.qt_rifs || 0, item);
    contador($contadores, U.ICONE_DOCUMENTO, "ipl", "IPL", item.qt_ipls || 0, item);
    contador($contadores, U.ICONE_DOCUMENTO, "irpf", "IRPF", item.qt_irpfs || 0, item);
    contador($contadores, U.ICONE_DOCUMENTO, "ipei", "Fiscal", item.qt_ipeis || 0, item);
    contador($contadores, U.ICONE_TELEFONIA, "telefonia", "Telefonia", item.qt_telefonia || 0, item);
    contador($contadores, U.ICONE_DOCUMENTO, "documentos", "Docs", item.qt_documentos || 0, item);

    if (prog.mostrarBarra || prog.indeterminado || prog.concluido) {
      var $progWrap = $("<div>").addClass("inq-lista-progresso").appendTo($principal);
      $("<div>").addClass("inq-meta-label").text("ANDAMENTO: " + prog.texto).appendTo($progWrap);
      if (prog.mostrarBarra && prog.pct != null) {
        var $bar = $("<div>").addClass("inq-progress").attr({ role: "progressbar", "aria-valuenow": prog.pct, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": prog.texto }).appendTo($progWrap);
        $("<div>").addClass("inq-progress-bar").css("width", prog.pct + "%").appendTo($bar);
      } else if (prog.indeterminado) {
        var $barInd = $("<div>").addClass("inq-progress inq-progress--indeterminado").attr("aria-hidden", "true").appendTo($progWrap);
        $("<div>").addClass("inq-progress-bar").appendTo($barInd);
      } else if (prog.concluido) {
        var $barOk = $("<div>").addClass("inq-progress").attr({ role: "progressbar", "aria-valuenow": 100, "aria-valuemin": 0, "aria-valuemax": 100 }).appendTo($progWrap);
        $("<div>").addClass("inq-progress-bar").css("width", "100%").appendTo($barOk);
      }
    }

    var $acoes = $("<div>").addClass("inq-lista-acoes").appendTo($linha);
    $("<button>").addClass("btn btn-primary")
      .html(ICONE_PASTA + (eCaso ? " Abrir Caso" : " Abrir IPL"))
      .on("click", function () { window.location.href = "investigacao.html?inquerito=" + encodeURIComponent(item.id_inquerito); })
      .appendTo($acoes);
    botaoIa(item).appendTo($acoes);
    $("<button>").addClass("btn btn-mesa")
      .html(U.ICONE_MESA + " Mesa de inquérito")
      .on("click", function () { window.location.href = "mesa_inquerito.html?inquerito=" + encodeURIComponent(item.id_inquerito); })
      .appendTo($acoes);
    botaoImportar(item).appendTo($acoes);

    return $linha;
  }

  function metaItem($container, label, valor) {
    var $item = $("<span>").addClass("inq-lista-meta-item").appendTo($container);
    $("<span>").addClass("inq-lista-meta-label").text(label + ": ").appendTo($item);
    $("<strong>").text(valor).appendTo($item);
  }

  function abaParaClasse(classe) {
    var m = { ufdr: "celulares", simba: "simba", rif: "rifs", ipl: "ipl", irpf: "irpf", ipei: "fiscal", telefonia: "telefonia", documentos: "documentos", iped: "celulares" };
    return m[classe] || "celulares";
  }

  function hrefBadgeInquerito(item, classe) {
    if (!item || !item.id_inquerito) return null;
    var aba = abaParaClasse(classe);
    return "investigacao.html?inquerito=" + encodeURIComponent(item.id_inquerito) + "&aba=" + encodeURIComponent(aba);
  }

  function contador($container, iconeHtml, classe, label, valor, item) {
    var href = null;
    if (item) {
      href = hrefBadgeInquerito(item, classe);
    }
    var zerado = !valor;
    var $c;
    if (href) {
      var titulo = "Ver " + label + " deste inquérito na aba " + abaParaClasse(classe);
      $c = $("<a>").addClass("inq-lista-cont" + (zerado ? " inq-lista-cont--zero" : "")).attr("href", href).attr("title", titulo).attr("aria-label", label + ": " + U.formatarNumero(valor)).appendTo($container);
    } else {
      $c = $("<span>").addClass("inq-lista-cont" + (zerado ? " inq-lista-cont--zero" : "")).appendTo($container);
    }
    $("<span>").addClass("inq-mini-icon " + classe).attr("aria-hidden", "true").html(iconeHtml).appendTo($c);
    $("<span>").attr("aria-hidden", "true").html(label + " <strong>" + U.formatarNumero(valor) + "</strong>").appendTo($c);
  }

  // Deixa visível só a view do modo atual e marca o botão correspondente. (3)
  function aplicarModoUI() {
    var ehLista = modoVisualizacao === "lista";
    $modoBtns.each(function () {
      var ativo = $(this).data("modo") === modoVisualizacao;
      $(this).toggleClass("ativo", ativo).attr("aria-pressed", String(ativo));
    });
    $carrosselView.toggleClass("hidden", ehLista);
    $listaView.toggleClass("hidden", !ehLista);
    if (ehLista) {
      $carrosselView.attr("hidden", true).attr("aria-hidden", "true");
      $listaView.removeAttr("hidden").attr("aria-hidden", "false");
    } else {
      $listaView.attr("hidden", true).attr("aria-hidden", "true");
      $carrosselView.removeAttr("hidden").attr("aria-hidden", "false");
    }
  }

  function definirModo(modo) {
    var novo = modo === "carrossel" ? "carrossel" : "lista";
    if (novo === modoVisualizacao) return;
    modoVisualizacao = novo;
    salvarModo(modoVisualizacao);
    paginaLista = 1;
    aplicarModoUI();
    render();
    atualizarTotal();
    sincronizarURL();
  }

  function criarMiniCardSimples($container, iconeHtml, classe, label, valor, unit, item) {
    var href = null;
    if (item) {
      href = hrefBadgeInquerito(item, classe);
    }
    var $m;
    if (href) {
      var tituloMini = "Ver " + label + " deste inquérito na aba " + abaParaClasse(classe);
      $m = $("<a>").addClass("inq-mini-card").attr("href", href).attr("title", tituloMini);
    } else {
      $m = $("<div>").addClass("inq-mini-card");
    }
    $("<div>").addClass("inq-mini-icon " + classe).html(iconeHtml).appendTo($m);
    var $info = $("<div>").addClass("inq-mini-info").appendTo($m);
    $("<div>").addClass("inq-mini-label").text(label).appendTo($info);
    $("<div>").addClass("inq-mini-value").text(U.formatarNumero(valor) + " " + unit).appendTo($info);
    $container.append($m);
  }

  function formatarDataHora(iso) {
    if (!iso) return "sem registro";
    try {
      return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"))
        .toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
    } catch (e) {
      return String(iso);
    }
  }

  function irPara(indice) {
    if (indice < 0 || indice >= inqueritosFiltrados.length) return;
    paginaAtual = indice;
    $track.find(".carousel-card").removeClass("active prev next");
    $track.find('[data-index="' + indice + '"]').first().addClass("active");
    var total = inqueritosFiltrados.length;
    var antIdx = (indice - 1 + total) % total;
    if (antIdx !== indice) {
      $track.find('[data-index="' + antIdx + '"]').first().addClass("prev");
    }
    var proxIdx = (indice + 1) % total;
    if (proxIdx !== indice && proxIdx !== antIdx) {
      $track.find('[data-index="' + proxIdx + '"]').first().addClass("next");
    }
    atualizarIndicadores();
    atualizarBotoesNavegacao();
  }

  function proximo() {
    var prox = (paginaAtual + 1) % inqueritosFiltrados.length;
    irPara(prox);
  }

  function anterior() {
    var ant = (paginaAtual - 1 + inqueritosFiltrados.length) % inqueritosFiltrados.length;
    irPara(ant);
  }

  function atualizarIndicadores() {
    var $dots = $indicators.find(".carousel-indicator");
    if (!$dots.length) {
      // Modo compacto (>20 itens): anuncia posição via aria-live do track.
      $track.attr("aria-label", "Lista de inquéritos — " + (paginaAtual + 1) + " de " + inqueritosFiltrados.length);
      return;
    }
    $dots
      .removeClass("active")
      .attr("aria-selected", "false")
      .filter('[data-index="' + paginaAtual + '"]')
      .addClass("active")
      .attr("aria-selected", "true")
      .attr("aria-current", "true");
    $indicators.find(".carousel-indicator:not(.active)").removeAttr("aria-current");
  }

  function atualizarBotoesNavegacao() {
    var disabled = inqueritosFiltrados.length <= 1;
    $btnPrev.prop("disabled", disabled).toggleClass("hidden", disabled);
    $btnNext.prop("disabled", disabled).toggleClass("hidden", disabled);
    if (disabled) {
      $btnPrev.attr("hidden", true);
      $btnNext.attr("hidden", true);
    } else {
      $btnPrev.removeAttr("hidden");
      $btnNext.removeAttr("hidden");
    }
  }

  function bindEventos() {
    var debouncedBusca = debounce(function () {
      paginaLista = 1;
      paginaAtual = 0;
      aplicarFiltros();
    }, DEBOUNCE_MS);

    $busca.on("input", debouncedBusca);
    $busca.on("search", function () {
      // limpar via X nativo dispara sem debounce
      paginaLista = 1;
      aplicarFiltros();
    });
    $filtroTipo.on("change", function () { paginaLista = 1; paginaAtual = 0; aplicarFiltros(); carregarEstatisticas($filtroDelegacia.val()); });
    $filtroStatus.on("change", function () { paginaLista = 1; paginaAtual = 0; aplicarFiltros(); });
    $filtroDelegacia.on("change", function () {
      paginaLista = 1;
      paginaAtual = 0;
      aplicarFiltros();
      carregarEstatisticas($(this).val());
    });
    $filtroOrdem.on("change", function () { paginaLista = 1; paginaAtual = 0; aplicarFiltros(); });
    if ($filtroPorPagina && $filtroPorPagina.length) {
      $filtroPorPagina.on("change", function () {
        var n = parseInt($(this).val(), 10);
        if (ITENS_POR_PAGINA_OPCOES.indexOf(n) === -1) n = ITENS_POR_PAGINA_PADRAO;
        itensPorPagina = n;
        salvarPorPagina(n);
        paginaLista = 1;
        aplicarFiltros();
      });
    }
    if ($btnLimpar && $btnLimpar.length) $btnLimpar.on("click", limparFiltros);
    if ($btnLimparVazio && $btnLimparVazio.length) $btnLimparVazio.on("click", limparFiltros);

    // Stats clicáveis: filtra por tipo sem recarregar estatísticas.
    $(document).on("click", "[data-stat-filtro]", function () {
      var filtro = $(this).attr("data-stat-filtro") || "";
      var atual = $filtroTipo.val() || "";
      var novo = filtro === "todos" ? "" : filtro;
      if (novo === atual && filtro === "todos") novo = "";
      // Clicar no stat já ativo limpa o filtro de tipo.
      if (novo === atual) novo = "";
      $filtroTipo.val(novo);
      paginaLista = 1;
      paginaAtual = 0;
      aplicarFiltros();
    });

    $modoBtns.on("click", function () {
      definirModo($(this).data("modo"));
    });

    $btnPrev.on("click", function () { anterior(); });
    $btnNext.on("click", function () { proximo(); });

    $track.on("click", ".carousel-card.prev", function () { anterior(); });
    $track.on("click", ".carousel-card.next", function () { proximo(); });

    // evita que clique no badge linkado dispare navegação do carrossel (prev/next)
    $track.on("click", "a.inq-mini-card, a.inq-lista-cont", function (e) { e.stopPropagation(); });

    $indicators.on("click", ".carousel-indicator", function () {
      irPara($(this).data("index"));
    });

    function irParaPagina(n) {
      var totalPag = Math.max(1, Math.ceil(inqueritosFiltrados.length / itensPorPagina));
      n = Math.max(1, Math.min(totalPag, n));
      if (n === paginaLista) return;
      paginaLista = n;
      renderLista();
      atualizarTotal();
      sincronizarURL();
      if ($lista && $lista.length && $lista[0].scrollIntoView) {
        try { $lista[0].scrollIntoView({ behavior: "smooth", block: "start" }); } catch (e) {}
      }
    }

    $listaPrev.on("click", function () { irParaPagina(paginaLista - 1); });
    $listaNext.on("click", function () { irParaPagina(paginaLista + 1); });
    if ($listaFirst && $listaFirst.length) $listaFirst.on("click", function () { irParaPagina(1); });
    if ($listaLast && $listaLast.length) $listaLast.on("click", function () { irParaPagina(Math.max(1, Math.ceil(inqueritosFiltrados.length / itensPorPagina))); });

    $(document).on("keydown", function (e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
      if (modoVisualizacao === "lista") return;
      if (e.key === "ArrowRight") { e.preventDefault(); proximo(); }
      if (e.key === "ArrowLeft") { e.preventDefault(); anterior(); }
    });
  }

  $(function () {
    aplicarEstadoURLNaUI();
    bindEventos();
    aplicarModoUI();
    carregarInqueritos();
  });

  window.carregarInqueritos = carregarInqueritos;
  window.atualizarTudo = function () { carregarInqueritos(); };
})();
