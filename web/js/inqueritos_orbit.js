/* js/inqueritos_orbit.js — recursos da listagem principal com carrossel visual Orbit. */
(function () {
  "use strict";

  var ITENS_POR_PAGINA = 12;
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
  var $pagInfo = $("#lista-pag-info");
  var $total = $("#inq-total");
  var $carregandoGeral = $("#inqueritos-carregando");
  var $erroGeral = $("#inqueritos-erro");
  var $selectionInsights = $("#orbit-selection-insights");

  var cacheConvergencias = {};
  var CHAVE_MODO = "analyser-inqueritos-orbit-modo";
  var modoVisualizacao = "carrossel";
  var estadoInicialURL = lerEstadoURL();

  var U = window.InqueritoUtils;
  // Caso de inteligência usa as mesmas fontes do inquérito — só o selo muda.
  var FONTES_INQUERITO = [
    { campo: "qt_extracoes", icone: "ICONE_DISPOSITIVO", classe: "ufdr", rotulo: "UFDR", unidade: "extrações" },
    { campo: "qt_simba", icone: "ICONE_SIMBA", classe: "simba", rotulo: "SIMBA", unidade: "extratos" },
    { campo: "qt_rifs", icone: "ICONE_RIF", classe: "rif", rotulo: "RIF", unidade: "RIFs" },
    { campo: "qt_ipls", icone: "ICONE_DOCUMENTO", classe: "ipl", rotulo: "IPL", unidade: "docs" },
    { campo: "qt_irpfs", icone: "ICONE_DOCUMENTO", classe: "irpf", rotulo: "IRPF", unidade: "declarações" },
    { campo: "qt_ipeis", icone: "ICONE_DOCUMENTO", classe: "ipei", rotulo: "Sigilo fiscal", rotuloCurto: "Fiscal", unidade: "IPEIs" },
    { campo: "qt_telefonia", icone: "ICONE_TELEFONIA", classe: "telefonia", rotulo: "Telefonia", unidade: "operadoras" },
    { campo: "qt_documentos", icone: "ICONE_DOCUMENTO", classe: "documentos", rotulo: "Documentos", rotuloCurto: "Docs", unidade: "arquivos" }
  ];
  var CAMPOS_TOTAL_FONTES = ["qt_extracoes", "qt_simba", "qt_rifs", "qt_ipls", "qt_irpfs", "qt_ipeis", "qt_telefonia", "qt_iped", "qt_documentos"];

  function fontesDoItem(item) {
    return FONTES_INQUERITO.map(function (fonte) {
      return Object.assign({}, fonte, { quantidade: Number(item[fonte.campo] || 0) });
    });
  }

  function somarCampos(item, campos) {
    return campos.reduce(function (total, campo) { return total + Number(item[campo] || 0); }, 0);
  }

  // ---- Modo inicial: URL > localStorage > carrossel Orbit ----
  function lerModoSalvo() {
    if (estadoInicialURL.modo === "carrossel" || estadoInicialURL.modo === "lista") return estadoInicialURL.modo;
    try {
      var salvo = localStorage.getItem(CHAVE_MODO);
      if (salvo === "carrossel" || salvo === "lista") return salvo;
    } catch (e) {}
    return "carrossel";
  }
  modoVisualizacao = lerModoSalvo();

  function salvarModo(modo) {
    try { localStorage.setItem(CHAVE_MODO, modo); } catch (e) {}
  }

  function formatarIdInquerito(nu) {
    if (typeof window !== "undefined" && window.AnalyserUtils && typeof window.AnalyserUtils.formatarNumeroInquerito === "function") {
      return window.AnalyserUtils.formatarNumeroInquerito(nu);
    }
    if (!nu) return "";
    var str = String(nu).trim();
    var digitos = str.replace(/\D/g, "");
    if (digitos.length === 11) return digitos.slice(0, 4) + "." + digitos.slice(4);
    if (digitos.length === 13) return digitos.slice(0, 4) + "." + digitos.slice(4, 11) + "-" + digitos.slice(11);
    if (digitos.length > 4 && digitos.length < 11) return digitos.slice(0, 4) + "." + digitos.slice(4);
    return str;
  }

  // ---- URL sync (1.4) ----
  function lerEstadoURL() {
    try {
      var p = new URLSearchParams(window.location.search);
      return {
        q: p.get("q") || "",
        status: p.get("status") || "todos",
        delegacia: p.get("delegacia") || "",
        tipo: p.get("tipo") || "",
        ordem: p.get("ordem") || "recente",
        modo: p.get("modo") || null,
        pagina: Math.max(1, parseInt(p.get("pagina") || "1", 10) || 1)
      };
    } catch (e) {
      return { q: "", status: "todos", delegacia: "", tipo: "", ordem: "recente", modo: null, pagina: 1 };
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
      var qs = params.toString();
      var novo = window.location.pathname + (qs ? "?" + qs : "") + (window.location.hash || "");
      history.replaceState(null, "", novo);
    } catch (e) {}
  }

  function aplicarEstadoURLNaUI() {
    if (estadoInicialURL.q) $busca.val(estadoInicialURL.q);
    if (estadoInicialURL.status) $filtroStatus.val(estadoInicialURL.status);
    if (estadoInicialURL.tipo) $filtroTipo.val(estadoInicialURL.tipo);
    if (estadoInicialURL.ordem) $filtroOrdem.val(estadoInicialURL.ordem);
    paginaLista = estadoInicialURL.pagina || 1;
  }

  function debounce(fn, wait) {
    var t = null;
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }

  var HTML_ASSISTENTE_IA = '<span class="orbit-ai-stars" aria-hidden="true"><span>✦</span><span>✧</span><span>✦</span></span><span class="orbit-action-label">Assistente de IA</span>';
  var ICONE_MAIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

  function botaoIa(item) {
    var $btn = $("<button>").addClass("btn btn-ia orbit-action orbit-action--ia").html(HTML_ASSISTENTE_IA);
    if (item.ia_ativa === false) {
      return $btn
        .css({ opacity: 0.4, cursor: "not-allowed" })
        .attr("disabled", true)
        .attr("title", "IA desativada para " + (item.delegacia || "esta delegacia"));
    }
    return $btn.on("click", function () {
      window.location.href = "ia.html?inquerito=" + encodeURIComponent(item.id_inquerito);
    });
  }

  function botaoImportar(item) {
    var $btn = $("<button>").addClass("btn btn-importar orbit-action orbit-action--secondary").attr("type", "button").attr("title", "Importar dados para este inquérito").html(ICONE_MAIS + " <span>Importar</span>");
    return $btn.on("click", function (e) {
      e.stopPropagation();
      if (window.abrirNovaImportacaoOrbit) window.abrirNovaImportacaoOrbit(item);
    });
  }

  function botaoNavegacao(classes, conteudo, destino, titulo, usarHtml) {
    var $botao = $("<button>").addClass(classes).attr("type", "button");
    if (titulo) $botao.attr("title", titulo);
    $botao[usarHtml ? "html" : "text"](conteudo);
    return $botao.on("click", function () { window.location.href = destino; });
  }

  function preencherAcoes($destino, item, rotuloAdaptavel) {
    var eCaso = !!item.in_caso_inteligencia;
    var id = encodeURIComponent(item.id_inquerito);
    var paginaPrincipal = "inquerito.html?inquerito=";
    var paginaGrafo = "mesa_inquerito.html?inquerito=";
    var rotuloGrafo = "Quadro de Investigação";
    var conteudoGrafo = U.ICONE_MESA + " <span>" + (rotuloAdaptavel
      ? 'Quadro<span class="btn-rotulo-extra"> de Investigação</span>'
      : rotuloGrafo) + "</span>";

    botaoNavegacao("btn btn-primary orbit-action orbit-action--secondary", eCaso ? "📂 Abrir Caso" : "📂 Abrir IPL", paginaPrincipal + id).appendTo($destino);
    botaoIa(item).appendTo($destino);
    botaoNavegacao("btn btn-mesa orbit-action orbit-action--mesa", conteudoGrafo, paginaGrafo + id, rotuloGrafo, true).appendTo($destino);
    botaoImportar(item).appendTo($destino);
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
          .html('<div class="estado-erro"><span class="estado-icone" aria-hidden="true">⚠</span><p class="estado-titulo">' + $("<span>").text(msg).html() + '</p><button type="button" class="btn btn-secondary btn-small" id="stats-tentar">Tentar de novo</button></div>');
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
          return (x.qt_extracoes || 0) + (x.qt_simba || 0) + (x.qt_rifs || 0) + (x.qt_ipls || 0) +
            (x.qt_irpfs || 0) + (x.qt_ipeis || 0) + (x.qt_telefonia || 0) + (x.qt_documentos || 0);
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
      if (status === "andamento" && !item.tem_extracao_em_andamento) return false;
      if (status === "concluido" && item.tem_extracao_em_andamento) return false;
      if (delegacia && item.delegacia !== delegacia) return false;
      return true;
    });

    filtrados = ordenarInvestigacoes(filtrados, ordem);
    inqueritosFiltrados = filtrados;

    // corrige paginaLista se saiu do range
    var totalPaginas = Math.max(1, Math.ceil(inqueritosFiltrados.length / ITENS_POR_PAGINA));
    if (paginaLista > totalPaginas) paginaLista = totalPaginas;
    if (paginaLista < 1) paginaLista = 1;

    render();
    atualizarTotal();
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
    if (!total) {
      $total.html('Nenhuma investigação encontrada');
      return;
    }
    var partes = [];
    partes.push('<strong>' + U.formatarNumero(total) + '</strong> investigaç' + (total !== 1 ? 'ões' : 'ão'));
    if (qInq || qCaso) {
      partes.push('(' + qInq + ' inquérito' + (qInq !== 1 ? 's' : '') + ', ' + qCaso + ' caso' + (qCaso !== 1 ? 's' : '') + ' de inteligência)');
    }
    if (modoVisualizacao === "lista") {
      var totalPag = Math.max(1, Math.ceil(total / ITENS_POR_PAGINA));
      partes.push('· página ' + paginaLista + ' de ' + totalPag);
      if (ordemTxt) partes.push('· ' + $("<span>").text(ordemTxt).html());
    } else {
      if (ordemTxt) partes.push('· ' + $("<span>").text(ordemTxt).html());
    }
    $total.html(partes.join(" "));
  }

  function alertaConvergencia(relacionados) {
    var $alerta = $("<div>").addClass("convergencia-alerta");
    var $topo = $("<div>").addClass("convergencia-alerta-topo").appendTo($alerta);
    $("<span>").addClass("convergencia-alerta-icone").attr("aria-hidden", "true").text("⚠").appendTo($topo);
    $("<strong>").text("Convergência detectada").appendTo($topo);

    relacionados.forEach(function (rel) {
      var $linha = $("<div>").addClass("convergencia-alerta-linha").appendTo($alerta);
      $linha.append(document.createTextNode("Este inquérito compartilha "));
      $("<strong>").text(U.formatarNumero(rel.qt_identificadores)).appendTo($linha);
      $linha.append(document.createTextNode(" identificador(es) com "));
      $("<a>")
        .attr("href", "inquerito_orbit.html?inquerito=" + encodeURIComponent(rel.id_inquerito_relacionado))
        .text(rel.nu_inquerito_relacionado)
        .on("click", function (e) { e.stopPropagation(); })
        .appendTo($linha);
    });
    return $alerta;
  }

  function aplicarConvergencias(ids) {
    (ids || []).forEach(function (id) {
      var relacionados = cacheConvergencias[id];
      if (!relacionados || !relacionados.length) return;

      var $card = $track.find('.carousel-card[data-id-item="' + id + '"]');
      if ($card.length && !$card.find('.convergencia-alerta').length) {
        var $alertaCard = alertaConvergencia(relacionados);
        var $andamento = $card.find('.inq-card-andamento');
        if ($andamento.length) $alertaCard.insertBefore($andamento);
        else $card.find('.inq-meta').after($alertaCard);
      }

      var $linha = $lista.find('.inq-lista-card[data-id-item="' + id + '"]');
      if ($linha.length && !$linha.find('.convergencia-alerta').length) {
        var $alertaLinha = alertaConvergencia(relacionados);
        var $metaLinha = $linha.find('.inq-lista-meta');
        if ($metaLinha.length) $metaLinha.after($alertaLinha);
        else $linha.find('.inq-lista-principal').append($alertaLinha);
      }
    });
  }

  function carregarConvergencias(itens) {
    var ids = (itens || [])
      .filter(function (item) { return item && item.id_inquerito; })
      .map(function (item) { return item.id_inquerito; });
    if (!ids.length) return;

    var faltantes = ids.filter(function (id) { return cacheConvergencias[id] === undefined; });
    if (!faltantes.length) {
      aplicarConvergencias(ids);
      return;
    }

    $.getJSON("/api/investigacao/convergencia-resumo", { inqueritos: faltantes.join(",") })
      .done(function (porInquerito) {
        porInquerito = porInquerito || {};
        faltantes.forEach(function (id) {
          cacheConvergencias[id] = porInquerito[id] || [];
        });
        aplicarConvergencias(ids);
      })
      .fail(function () {});
  }

  function renderCarousel() {
    $track.empty();
    $indicators.empty();

    $carrosselView.toggleClass("carrossel-unico", inqueritosFiltrados.length <= 1);

    if (!inqueritosFiltrados.length) {
      $vazio.removeClass("hidden").removeAttr("hidden").text($busca.val() || $filtroStatus.val() !== "todos" || $filtroDelegacia.val() || $filtroTipo.val() ? "Nenhum resultado para este filtro. Tente ajustar os filtros." : "Nenhuma investigação encontrada.");
      $selectionInsights.addClass("hidden").attr("hidden", true);
      $btnPrev.addClass("hidden").prop("disabled", true).attr("hidden", true);
      $btnNext.addClass("hidden").prop("disabled", true).attr("hidden", true);
      $indicators.addClass("hidden").attr("hidden", true);
      return;
    }

    $vazio.addClass("hidden").attr("hidden", true);
    $btnPrev.removeClass("hidden").removeAttr("hidden");
    $btnNext.removeClass("hidden").removeAttr("hidden");
    $indicators.removeClass("hidden").removeAttr("hidden");

    inqueritosFiltrados.forEach(function (inq, idx) {
      var $card = criarCard(inq);
      $card.attr("data-index", idx)
           .attr("role", "listitem")
           .attr("aria-label", (idx + 1) + " de " + inqueritosFiltrados.length + " — " + (inq.nome_item || "") + " — " + (inq.tem_extracao_em_andamento ? "em andamento" : "processado"))
           .attr("aria-roledescription", "slide");
      $track.append($card);

      var $dot = $("<button>")
        .addClass("carousel-indicator")
        .attr("data-index", idx)
        .attr("role", "tab")
        .attr("aria-label", "Ir para " + (inq.in_caso_inteligencia ? "caso de inteligência " : "inquérito ") + (idx + 1) + " de " + inqueritosFiltrados.length)
        .attr("aria-selected", "false");
      $indicators.append($dot);
    });

    paginaAtual = 0;
    irPara(0, true);
    carregarConvergencias(inqueritosFiltrados);
  }

  function criarCard(item) {
    var ehCaso = !!item.in_caso_inteligencia;
    var $card = $("<article>").addClass("carousel-card" + (ehCaso ? " caso-card" : "")).attr("data-id-item", item.id_item).attr("data-tipo", ehCaso ? "caso" : "inquerito");

    // Header: tipo badge + delegacia + status
    var $header = $("<div>").addClass("inq-card-header").appendTo($card);
    var $idWrap = $("<span>").css({ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }).appendTo($header);
    $("<span>").addClass("inq-id").text("DELEGACIA " + (item.delegacia || "—")).appendTo($idWrap);
    $("<span>").addClass("inq-tipo-badge " + (ehCaso ? "caso" : "inquerito")).text(ehCaso ? "CASO DE INTELIGÊNCIA" : "INQUÉRITO").appendTo($idWrap);

    var prog = progressoInfo(item);
    var statusClass = item.tem_extracao_em_andamento ? "andamento" : "processado";
    var statusLabel = item.tem_extracao_em_andamento ? "⏳ " + prog.texto : "✓ " + prog.texto;
    $("<span>").addClass("inq-status " + statusClass).attr("title", prog.texto).text(statusLabel).appendTo($header);

    // Body
    var $body = $("<div>").addClass("inq-body").appendTo($card);
    $("<div>").addClass("inq-title-full")
      .html((ehCaso ? "CASO" : "IPL") + '<br><strong>' + $("<span>").text(formatarIdInquerito(item.nu_inquerito) || "—").html() + '</strong>')
      .appendTo($body);

    // Meta
    var $metaGrade = $("<div>").addClass("inq-meta").appendTo($card);

    var $resp = $("<div>").addClass("inq-meta-item inq-meta-full").appendTo($metaGrade);
    $("<div>").addClass("inq-meta-label").text("RESPONSÁVEL").appendTo($resp);
    $("<div>").addClass("inq-meta-value").text(item.administrador || "—").appendTo($resp);

    // Mantém o andamento visível no card, inclusive quando a importação já terminou.
    var $andamento = $("<div>").addClass("inq-card-andamento").appendTo($card);
    $("<div>").addClass("inq-meta-label").text("ANDAMENTO: " + prog.texto).appendTo($andamento);
    var $progress = $("<div>").addClass("inq-progress").attr({
      role: "progressbar",
      "aria-valuemin": 0,
      "aria-valuemax": 100,
      "aria-label": prog.texto
    }).appendTo($andamento);
    var $progressBar = $("<div>").addClass("inq-progress-bar").appendTo($progress);
    if (prog.indeterminado) {
      $progress.addClass("inq-progress--indeterminado").attr("aria-hidden", "true").removeAttr("role aria-valuemin aria-valuemax aria-label");
    } else {
      var percentual = prog.pct == null ? (prog.concluido ? 100 : 0) : prog.pct;
      $progress.attr("aria-valuenow", percentual);
      $progressBar.css("width", percentual + "%");
    }

    // Resumo: quebrado em 6 para inquéritos (2.3)
    var $summary = $("<div>").addClass("inq-summary").appendTo($card);
    $("<h3>").addClass("inq-summary-title").text("RESUMO DOS DADOS PROCESSADOS").appendTo($summary);
    var $summaryGrade = $("<div>").addClass("inq-summary-grade").appendTo($summary);

    fontesDoItem(item).forEach(function (fonte) {
      criarMiniCardSimples($summaryGrade, U[fonte.icone], fonte.classe, fonte.rotulo, fonte.quantidade, fonte.unidade, item);
    });
    $summaryGrade.addClass("inq-summary-grade--quebrado");

    // Footer
    var $footer = $("<div>").addClass("inq-footer").appendTo($card);
    preencherAcoes($footer, item, true);

    return $card;
  }

  function renderLista() {
    $lista.empty();

    if (!inqueritosFiltrados.length) {
      var filtrado = !!($busca.val() || $filtroStatus.val() !== "todos" || $filtroDelegacia.val() || $filtroTipo.val());
      $listaVazio.removeClass("hidden").removeAttr("hidden")
        .text(filtrado ? "Nenhum resultado para este filtro. Tente ajustar ou limpar os filtros." : "Nenhuma investigação encontrada.");
      $listaPaginacao.addClass("hidden").attr("hidden", true);
      return;
    }

    $listaVazio.addClass("hidden").attr("hidden", true);

    var totalPaginas = Math.max(1, Math.ceil(inqueritosFiltrados.length / ITENS_POR_PAGINA));
    if (paginaLista > totalPaginas) paginaLista = totalPaginas;
    var inicio = (paginaLista - 1) * ITENS_POR_PAGINA;
    var fim = Math.min(inicio + ITENS_POR_PAGINA, inqueritosFiltrados.length);
    var fatia = inqueritosFiltrados.slice(inicio, fim);

    var fragment = document.createDocumentFragment();
    fatia.forEach(function (inq) {
      var el = criarLinhaLista(inq)[0];
      fragment.appendChild(el);
    });
    $lista[0].appendChild(fragment);
    carregarConvergencias(fatia);

    // paginação
    if (totalPaginas > 1) {
      $listaPaginacao.removeClass("hidden").removeAttr("hidden");
      $pagInfo.text("Mostrando " + (inicio + 1) + "–" + fim + " de " + inqueritosFiltrados.length + " · página " + paginaLista + " de " + totalPaginas);
      $listaPrev.prop("disabled", paginaLista <= 1);
      $listaNext.prop("disabled", paginaLista >= totalPaginas);
    } else {
      $listaPaginacao.addClass("hidden").attr("hidden", true);
    }
  }

  function criarLinhaLista(item) {
    var ehCaso = !!item.in_caso_inteligencia;
    var prog = progressoInfo(item);
    var $linha = $("<article>")
      .addClass("investigacao-card inq-lista-card" + (ehCaso ? " caso-card" : ""))
      .attr("data-id-item", item.id_item)
      .attr("data-tipo", ehCaso ? "caso" : "inquerito")
      .attr("role", "listitem")
      .attr("aria-label", (ehCaso ? "Caso de inteligência " : "Inquérito ") + (item.nome_item || "") + " — " + prog.texto);

    var $principal = $("<div>").addClass("inq-lista-principal").appendTo($linha);

    var $topo = $("<div>").addClass("inq-lista-topo").appendTo($principal);
    $("<span>").addClass("inq-lista-numero")
      .html((ehCaso ? "<small>CASO</small>" : "<small>IPL</small>") + $("<span>").text(formatarIdInquerito(item.nu_inquerito) || "—").html())
      .appendTo($topo);
    $("<span>").addClass("inq-tipo-badge " + (ehCaso ? "caso" : "inquerito")).text(ehCaso ? "CASO DE INTELIGÊNCIA" : "INQUÉRITO").appendTo($topo);
    var status = item.tem_extracao_em_andamento ? "andamento" : "processado";
    var statusText = item.tem_extracao_em_andamento ? "⏳ " + prog.texto : "✓ " + prog.texto;
    $("<span>").addClass("inq-status " + status).attr("title", prog.texto).text(statusText).appendTo($topo);

    var $meta = $("<div>").addClass("inq-lista-meta").appendTo($principal);
    metaItem($meta, "Delegacia", item.delegacia || "—");
    metaItem($meta, "Responsável", item.administrador || "—");
    metaItem($meta, "Tipo", ehCaso ? "Caso de inteligência" : "Inquérito");
    metaItem($meta, "Última atualização", formatarDataHora(item.dt_criacao_ref));

    var $contadores = $("<div>").addClass("inq-lista-contadores").appendTo($principal);
    fontesDoItem(item).forEach(function (fonte) {
      contador($contadores, U[fonte.icone], fonte.classe, fonte.rotuloCurto || fonte.rotulo, fonte.quantidade, item);
    });

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
    preencherAcoes($acoes, item, false);

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
    var $c;
    if (href) {
      var titulo = "Ver " + label + " deste inquérito na aba " + abaParaClasse(classe);
      $c = $("<a>").addClass("inq-lista-cont").attr("href", href).attr("title", titulo).appendTo($container);
    } else {
      $c = $("<span>").addClass("inq-lista-cont").appendTo($container);
    }
    $("<span>").addClass("inq-mini-icon " + classe).html(iconeHtml).appendTo($c);
    $("<span>").html(label + " <strong>" + U.formatarNumero(valor) + "</strong>").appendTo($c);
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
    $selectionInsights.toggleClass("hidden", ehLista || !inqueritosFiltrados.length)
      .attr("hidden", ehLista || !inqueritosFiltrados.length);
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

  function renderSelection(item) {
    if (!item || modoVisualizacao !== "carrossel") {
      $selectionInsights.addClass("hidden").attr("hidden", true);
      return;
    }

    var ehCaso = !!item.in_caso_inteligencia;
    var prog = progressoInfo(item);
    var mensagens = (item.qt_mensagens || 0) + (item.qt_sms_mensagens || 0) + (item.qt_msg_mensagens || 0);
    var conversas = (item.qt_conversas || 0) + (item.qt_sms_conversas || 0) + (item.qt_msg_conversas || 0);
    var fontes = somarCampos(item, CAMPOS_TOTAL_FONTES);
    var kpis = [
      ["Mensagens", mensagens], ["Conversas", conversas],
      ["Contatos", item.qt_contatos || 0], ["Fontes", fontes]
    ];

    kpis.forEach(function (kpi, index) {
      $("#orbit-kpi-label-" + (index + 1)).text(kpi[0]);
      $("#orbit-kpi-" + (index + 1)).text(U.formatarNumero(kpi[1]));
    });

    $("#orbit-selection-kind").text(ehCaso ? "Caso de inteligência" : "Resumo da investigação");
    $("#orbit-selection-title").text(ehCaso ? "CASO " + (formatarIdInquerito(item.nu_inquerito) || "—") : "IPL " + (formatarIdInquerito(item.nu_inquerito) || "—"));
    $("#orbit-selection-panel-title").text("PAINEL - " + (ehCaso
      ? "CASO " + (formatarIdInquerito(item.nu_inquerito) || "—")
      : "IPL " + (formatarIdInquerito(item.nu_inquerito) || "—")));
    $("#orbit-selection-description").text($.trim(item.tx_contexto_investigacao || "") ||
      (ehCaso ? "Caso de inteligência sem contexto cadastrado." : "Inquérito sem contexto cadastrado."));

    var $tags = $("#orbit-selection-tags").empty();
    [ehCaso ? "Caso de inteligência" : "Inquérito", item.delegacia || "Delegacia não informada", item.administrador || "Responsável não informado"]
      .forEach(function (tag) { $("<span>").text(tag).appendTo($tags); });

    $("#orbit-selection-status").removeClass("andamento processado")
      .addClass(item.tem_extracao_em_andamento ? "andamento" : "processado")
      .text(item.tem_extracao_em_andamento ? prog.texto : "Processado");

    var $meta = $("#orbit-selection-meta").empty();
    [["Responsável", item.administrador || "—"], ["Delegacia", item.delegacia || "—"],
      ["Atualização", formatarDataHora(item.dt_criacao_ref)]].forEach(function (entry) {
      $("<div>").append($("<dt>").text(entry[0]), $("<dd>").text(entry[1])).appendTo($meta);
    });

    var fontesItem = fontesDoItem(item);
    var maximo = Math.max.apply(null, fontesItem.map(function (fonte) { return fonte.quantidade; }).concat([1]));
    var $bars = $("#orbit-selection-bars").empty();
    fontesItem.forEach(function (fonte) {
      var $row = $("<div>").addClass("orbit-selection-bar " + fonte.classe).appendTo($bars);
      $("<div>").addClass("orbit-selection-bar-label")
        .append($("<span>").text(fonte.rotulo), $("<strong>").text(U.formatarNumero(fonte.quantidade)))
        .appendTo($row);
      var $trackBar = $("<span>").addClass("orbit-selection-bar-track").attr("aria-hidden", "true").appendTo($row);
      $("<span>").addClass("orbit-selection-bar-value")
        .css("width", (fonte.quantidade ? Math.max(12, fonte.quantidade * 100 / maximo) : 0) + "%").appendTo($trackBar);
    });

    $selectionInsights.removeClass("hidden").removeAttr("hidden").removeClass("is-updating");
    void $selectionInsights[0].offsetWidth;
    $selectionInsights.addClass("is-updating");
  }

  function irPara(indice, immediate) {
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
    if (!immediate && total > 1) {
      $track.removeClass("orbit-nudge");
      void $track[0].offsetWidth;
      $track.addClass("orbit-nudge");
      setTimeout(function () { $track.removeClass("orbit-nudge"); }, 320);
    }
    atualizarIndicadores();
    atualizarBotoesNavegacao();
    renderSelection(inqueritosFiltrados[indice]);
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
    $indicators.find(".carousel-indicator")
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

    $listaPrev.on("click", function () {
      if (paginaLista > 1) {
        paginaLista--;
        renderLista();
        atualizarTotal();
        sincronizarURL();
        $lista[0].scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
    $listaNext.on("click", function () {
      var totalPag = Math.ceil(inqueritosFiltrados.length / ITENS_POR_PAGINA);
      if (paginaLista < totalPag) {
        paginaLista++;
        renderLista();
        atualizarTotal();
        sincronizarURL();
        $lista[0].scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });

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