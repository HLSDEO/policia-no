(function () {
  "use strict";

  /* NO_ARQUIVO/NO_PASTA chegam da API como caminho relativo dentro de data/
   * ("SIP/2025.0001/CELULAR/NB2026.ufdr", ver docs/organizacao_arquivos.md); no card interessa
   * só o nome do arquivo, então cada lista é encurtada assim que chega. */
  var encurtar = window.AnalyserUtils.encurtarCaminhos;

  var $corpo = $("#extracoes-corpo");
  var $investigacoesLista = $("#investigacoes-lista");
  var $busca = $("#inq-busca");
  var $filtroStatus = $("#inq-filtro-status");
  var $paginacao = $("#inq-paginacao");
  var $modal = $("#modal-extracoes");
  var $titulo = $("#investigacao-titulo");
  var $breadcrumb = $("#investigacao-breadcrumb");
  var $avisoDetalhe = $("#investigacao-detalhe-aviso");
  var $skeleton = $("#investigacao-skeleton");
  var $live = $("#investigacao-live");
  var $porPagina = $("#inq-por-pagina");
  var $toolbar = $("#investigacoes-toolbar");
  var $ordenacao = $("#inq-ordenacao");
  var $limpar = $("#inq-limpar");
  var $total = $("#inv-total");
  var $totalWrap = $("#inv-total-wrap");
  var $extracoesBusca = $("#extracoes-busca");
  var pollTimer = null;
  var usuarioLogado = null;
  // Estado renderizado no DOM, para o polling saber quando dá para só atualizar os badges de
  // status em lugar e quando é preciso re-renderizar a lista (transições de estado).
  var idsInqueritosRenderizados = "";
  var estadoExtracoes = {};

  function getDeepLinkParams() {
    var params = new URLSearchParams(window.location.search);
    return {
      inquerito: params.get("inquerito"),
      doc: params.get("doc"),
      aba: params.get("aba")
    };
  }

  function ehModoDetalheInquerito() {
    return !!getDeepLinkParams().inquerito;
  }

  function debounce(fn, delay) { return AnalyserUtils.debounce(fn, delay); }

  function configurarTrapFocus($overlay) {
    $overlay.on("keydown", function (e) {
      if (e.key !== "Tab" || $overlay.hasClass("hidden")) return;
      var $focusable = $overlay.find('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])').filter(":visible");
      if (!$focusable.length) return;
      var first = $focusable[0];
      var last = $focusable[$focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }

  // Guarda quem abriu cada modal para devolver o foco ao fechar (a11y).
  var focoRetornoModal = new WeakMap();

  function abrirModalAcessivel($overlay, $focoInicial) {
    if ($overlay && $overlay.length && !focoRetornoModal.has($overlay[0])) {
      var ativo = document.activeElement;
      if (ativo && $overlay.has(ativo).length === 0) focoRetornoModal.set($overlay[0], ativo);
    }
    $overlay.removeClass("hidden");
    // impede scroll do fundo
    $("body").css("overflow", "hidden");
    if ($focoInicial && $focoInicial.length) {
      setTimeout(function () { $focoInicial.focus(); }, 50);
    } else {
      var $first = $overlay.find('button, input, select, textarea, [tabindex]:not([tabindex="-1"])').filter(":visible").first();
      if ($first.length) setTimeout(function () { $first.focus(); }, 50);
    }
  }

  function fecharModalAcessivel($overlay) {
    if (!$overlay || !$overlay.length) return;
    $overlay.addClass("hidden");
    if ($(".modal-overlay:not(.hidden)").length === 0) {
      $("body").css("overflow", "");
    }
    var anterior = focoRetornoModal.get($overlay[0]);
    focoRetornoModal.delete($overlay[0]);
    if (anterior && document.contains(anterior)) {
      try { anterior.focus(); } catch (e) {}
    }
  }

  function atualizarTituloPagina() {
    var params = getDeepLinkParams();
    if (params.inquerito && inqueritosTodos.length === 1) {
      var inq = inqueritosTodos[0];
      var titulo = inq.nu_inquerito ? "Inquérito " + inq.nu_inquerito : "Inquérito " + params.inquerito;
      $titulo.text(titulo);
      document.title = titulo + " · PolícIA";
    } else {
      $titulo.text("Investigações");
      document.title = "Investigações · PolícIA";
    }
  }

  function renderBreadcrumb() {
    var params = getDeepLinkParams();
    var $lista = $breadcrumb.find(".breadcrumb-lista");
    if (!$lista.length) $lista = $("<ol>").addClass("breadcrumb-lista").appendTo($breadcrumb.empty());
    else $lista.empty();
    // remove botão voltar anterior (fica fora do <ol>)
    $breadcrumb.find(".breadcrumb-voltar").remove();
    if (params.inquerito) {
      var $li1 = $("<li>").appendTo($lista);
      $("<a>").attr("href", "investigacao.html").text("Investigações").appendTo($li1);
      var $li2 = $("<li>").appendTo($lista);
      $("<span>").addClass("breadcrumb-sep").attr("aria-hidden", "true").text("›").appendTo($li2);
      var rotuloAtual;
      if (inqueritosTodos.length === 1) {
        rotuloAtual = inqueritosTodos[0].nu_inquerito || ("Inquérito " + params.inquerito);
      } else if (inqueritosTodos.length === 0) {
        rotuloAtual = "Inquérito " + params.inquerito + " (não encontrado)";
      } else {
        rotuloAtual = "Inquérito " + params.inquerito;
      }
      $("<span>").addClass("breadcrumb-atual").attr("aria-current", "page").text(rotuloAtual).appendTo($li2);
      $("<a>").attr("href", "investigacao.html").addClass("btn btn-secondary breadcrumb-voltar").text("← Ver todas").appendTo($breadcrumb);
      $breadcrumb.removeClass("hidden");
      // anuncia para leitores de tela
      if ($live.length) $live.text("Visualizando inquérito " + (inqueritosTodos[0] ? inqueritosTodos[0].nu_inquerito : params.inquerito));
    } else {
      $breadcrumb.addClass("hidden");
    }
    // modo detalhe esconde toolbar de busca global para não confundir com filtro local
    if (ehModoDetalheInquerito()) {
      $toolbar.addClass("hidden");
      if ($totalWrap && $totalWrap.length) $totalWrap.addClass("hidden");
    } else {
      $toolbar.removeClass("hidden");
      if ($totalWrap && $totalWrap.length) $totalWrap.removeClass("hidden");
    }
  }

  function mostrarSkeleton() {
    $skeleton.removeClass("hidden");
    $investigacoesLista.addClass("hidden");
    $paginacao.addClass("hidden");
  }

  function esconderSkeleton() {
    $skeleton.addClass("hidden");
    $investigacoesLista.removeClass("hidden");
  }

  function mostrarAvisoDetalhe(msg, tipo) {
    if (!msg) {
      $avisoDetalhe.addClass("hidden").empty();
      return;
    }
    $avisoDetalhe.removeClass("hidden").empty();
    var $box = $("<div>").addClass("investigacao-aviso-box " + (tipo === "erro" ? "aviso-erro" : "aviso-alerta")).appendTo($avisoDetalhe);
    $("<span>").text(msg).appendTo($box);
    if (tipo === "erro") {
      $("<a>").attr("href", "investigacao.html").addClass("investigacao-aviso-link").text("Voltar para todas").appendTo($box);
    }
  }

  var POR_PAGINA_PADRAO = 10;
  var POR_PAGINA = (function () {
    try {
      var v = parseInt(localStorage.getItem("investigacao-por-pagina"), 10);
      if (v === 10 || v === 20 || v === 50) return v;
    } catch (e) {}
    return POR_PAGINA_PADRAO;
  })();
  var paginaAtual = 1;
  var inqueritosTodos = [];
  var extracoesPorInquerito = {};
  var delegaciaAtual = "";
  // usuário vinculado a uma delegacia não troca de delegacia no seletor (trava na dele)
  var delegaciaTravada = false;

  /* ?inquerito=<id> — deep-link vindo da trilha de navegação das telas do inquérito ("voltar pro
   * inquérito"). Filtra a lista por ele e destaca o card; some depois do primeiro render pra
   * não ficar rolando a tela a cada atualização automática. */
  var idInqueritoDestaque = getDeepLinkParams().inquerito;
  /* ?doc=<id> — endereço antigo das citações [doc:...] do chat de IA, que abriam um modal de
   * texto aqui dentro. Hoje o documento tem tela própria (documento.html): o link antigo (de uma
   * conversa gravada, de um favorito) é redirecionado na hora, antes de montar a lista, em vez de
   * esperar o card do inquérito carregar os documentos só para abrir um modal. */
  (function redirecionarDocumentoAntigo() {
    var idDoc = getDeepLinkParams().doc;
    var idInq = getDeepLinkParams().inquerito;
    if (idDoc && idInq) {
      window.location.replace(DocTrecho.url({ inquerito: idInq, doc: idDoc }));
    }
  })();

  var $modalPermissoes = $("#modal-permissoes");
  var $permissoesNumero = $("#permissoes-inquerito-numero");
  var $permissoesLista = $("#permissoes-lista");
  var $permissoesVazio = $("#permissoes-vazio");
  var $permissaoSelect = $("#permissao-select");
  var $permissaoAdicionarBtn = $("#permissao-adicionar-btn");
  var $permissoesStatus = $("#permissoes-status");
  var $permissaoAdminCheckbox = $("#permissao-admin-checkbox");
  /* O modal de permissões expõe o /permissoes do inquérito (GET lista, POST concede
   * com {id_usuario, in_admin}, DELETE /{id_usuario} revoga). Guardar a URL-base em vez do id do
   * inquérito é o que deixa a mesma tela atender sem duplicar nada. */
  var urlBasePermissoes = null;


  var $modalDocumentos = $("#modal-documentos");
  var $documentosNumero = $("#documentos-inquerito-numero");
  var $documentosLista = $("#documentos-lista");
  var $documentosVazio = $("#documentos-vazio");
  var idInqueritoDocumentos = null;

  var $modalDelegacia = $("#modal-delegacia");
  var $delegaciaSelect = $("#delegacia-select");
  var $delegaciaStatus = $("#delegacia-status");
  var idInqueritoDelegacia = null;
  var delegaciaAtualInquerito = "";


  var $modalDocumentoEntidades = $("#modal-documento-entidades");
  var $documentoEntidadesTitulo = $("#documento-entidades-titulo");
  var $documentoEntidadesResumo = $("#documento-entidades-resumo");
  var $documentoEntidadesSubtitulo = $("#documento-entidades-subtitulo");
  var $documentoEntidadesVazio = $("#documento-entidades-vazio");
  var $documentoEntidadesLista = $("#documento-entidades-lista");

  // configura trap focus para todos os modais estáticos
  configurarTrapFocus($modalPermissoes);
  configurarTrapFocus($modalDelegacia);
  configurarTrapFocus($modal);
  configurarTrapFocus($modalDocumentos);
  configurarTrapFocus($modalDocumentoEntidades);

  var ICONE_EXTRAIR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  var ICONE_MESA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>';
  var ICONE_IA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z"/><path d="M19 15l.7 2.1L22 18l-2.3.9L19 21l-.7-2.1L16 18l2.3-.9L19 15z"/></svg>';
  var ICONE_ACHADO_ANALISTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  var ICONE_WHATSAPP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.4 8.4 0 0 1-4.15-1.1L3 20l1.15-5.15A8.4 8.4 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3h.5A8.5 8.5 0 0 1 21 11.5z"/></svg>';
  var ICONE_SMS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v12H7l-3 3V4z"/></svg>';
  var ICONE_CONTATOS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
  var ICONE_LIGACOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
  var ICONE_LOCALIZACOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';
  var ICONE_NOTIFICACOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  var ICONE_NOTAS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>';
  var ICONE_NAVEGACAO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
  var ICONE_APLICATIVOS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>';
  var ICONE_ARQUIVOS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h5l2 2h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/></svg>';
  var ICONE_TIMELINE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="4" x2="3" y2="20"/><polyline points="7 4 21 4 18 8 21 12 18 16 21 20 7 20"/></svg>';
  var ICONE_PIX = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 6v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V6l-8-4z"/><path d="m9 12 2 2 4-4"/></svg>';
  var ICONE_EMAIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>';
  var ICONE_BANCO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-6 9 6"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/></svg>';
  var ICONE_RIF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="13" x2="8" y2="17"/><line x1="12" y1="13" x2="12" y2="17"/><line x1="16" y1="13" x2="16" y2="17"/></svg>';
  var ICONE_SIMBA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-6 9 6"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/></svg>';
  var ICONE_TELEFONIA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v9"/><path d="M9 12h6l2 9H7l2-9z"/><path d="M6.5 6.5a7 7 0 0 1 11 0"/><path d="M4 4a10.5 10.5 0 0 1 16 0"/></svg>';
  var ICONE_IPED = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 3v18M3 8h5M3 16h5"/></svg>';
  var ICONE_ALERTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  var ICONE_INQUERITO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/></svg>';
  var ICONE_IRPF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="12" y2="17"/><path d="M16.5 15.5 15 17l1.5 1.5"/></svg>';
  // nota fiscal recortada: a IPEI (quebra de sigilo fiscal) gira em torno de NFe, DIRPF e
  // e-Financeira, e o ícone precisa se distinguir do documento genérico e do IRPF ao lado
  var ICONE_IPEI = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h12v20l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5L6 22z"/><line x1="9" y1="7" x2="15" y2="7"/><line x1="9" y1="11" x2="15" y2="11"/><line x1="9" y1="15" x2="13" y2="15"/></svg>';
  var ICONE_PECAS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>';
  var ICONE_DOCUMENTO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
  var ICONE_ACHADOS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.7" y2="16.7"/></svg>';
  var ICONE_ENVOLVIDOS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
  var ICONE_OCORRENCIAS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  var ICONE_DONO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
  var ICONE_WIFI = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/></svg>';
  var ICONE_DISPOSITIVO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>';
  // Plataforma do aparelho no cabeçalho do bloco: robô do Android e maçã da Apple. A maçã é um
  // desenho preenchido — vazada em contorno ela não se lê —, por isso foge do padrão stroke dos demais.
  var ICONE_ANDROID = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10a5 5 0 0 1 10 0"/><line x1="8.2" y1="4" x2="9.3" y2="6"/><line x1="15.8" y1="4" x2="14.7" y2="6"/><line x1="9.5" y1="8" x2="9.51" y2="8"/><line x1="14.5" y1="8" x2="14.51" y2="8"/><path d="M6 11h12v6a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2z"/><line x1="9.5" y1="19" x2="9.5" y2="22"/><line x1="14.5" y1="19" x2="14.5" y2="22"/><line x1="3" y1="11.5" x2="3" y2="16"/><line x1="21" y1="11.5" x2="21" y2="16"/></svg>';
  var ICONE_APPLE = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M17.05 12.54c-.03-2.6 2.12-3.85 2.22-3.91-1.21-1.77-3.09-2.01-3.76-2.04-1.6-.16-3.12.94-3.93.94-.81 0-2.06-.92-3.39-.9-1.74.03-3.35 1.01-4.25 2.57-1.81 3.14-.46 7.79 1.3 10.34.86 1.25 1.89 2.65 3.24 2.6 1.3-.05 1.79-.84 3.36-.84 1.57 0 2.01.84 3.39.81 1.4-.02 2.28-1.27 3.13-2.53.99-1.45 1.4-2.85 1.42-2.92-.03-.01-2.72-1.04-2.75-4.12M14.62 4.87c.71-.87 1.19-2.07 1.06-3.27-1.02.04-2.26.68-3 1.54-.66.77-1.24 2-1.08 3.17 1.14.09 2.3-.58 3.02-1.44"/></svg>';
  var ICONE_ARQUIVO_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><polyline points="9 15 11 17 15 13"/></svg>';
  var ICONE_ARQUIVO_AUSENTE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9.5" y1="13.5" x2="14.5" y2="18.5"/><line x1="14.5" y1="13.5" x2="9.5" y2="18.5"/></svg>';
  var ICONE_PERMISSOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1l9 5v6c0 5.55-3.84 10.74-9 12c-5.16-1.26-9-6.45-9-12V6l9-5z"/><polyline points="10 17 14 13 18 17"/><line x1="12" y1="9" x2="12" y2="13"/></svg>';
  var ICONE_PARAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>';
  var ICONE_APAGAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';
  var ICONE_TRANSCREVER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
  var ICONE_COMPARTILHAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/></svg>';
  var ICONE_EDITAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  var ICONE_MENSAGEIRO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var ICONE_RELATORIO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/><line x1="9" y1="9" x2="10" y2="9"/></svg>';
  var ICONE_MARCACOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
  // chave: senhas do chaveiro (telas de credenciais do aparelho)
  var ICONE_CHAVE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3"/><path d="m15 8 3 3"/><path d="m17 6 2 2"/></svg>';
  var ICONE_NUVEM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19a4.5 4.5 0 0 0 .42-8.98 7 7 0 0 0-13.34 2.04A4 4 0 0 0 6 19z"/></svg>';
  // caixa de ferramentas: o menu "Extras" reúne as ANÁLISES do caso (comparar rostos,
  // comparar celulares, buscar entidades…), e o ⋮ genérico não dizia isso
  var ICONE_EXTRAS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0 5 5L21 12l-9 9-3-3 9-9 1.7-1.7z"/><path d="M9 15 4.5 19.5"/><circle cx="4" cy="20" r="1.5"/></svg>';
  var ICONE_MAIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>';
  // cadeado: item "Hash" do menu ⋮ (conferência de integridade do arquivo original)
  var ICONE_HASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
  // círculo com "i": item "Informações da prova" do menu ⋮
  var ICONE_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
  // linha ascendente: "Linha do tempo financeira" do menu Extras
  var ICONE_GRAFICO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>';
  // ondas de rádio: "Encontros por antena" do menu Extras
  var ICONE_ANTENA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M4.93 19.07a10 10 0 0 1 0-14.14"/></svg>';
  // folha com linhas: "Contexto da investigação" do menu Extras
  var ICONE_CONTEXTO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>';

  // caixa com tampa e seta: "Arquivar/Reativar inquérito" do menu Gerência (situação do caso,
  // não o andamento do processamento)
  var ICONE_ARQUIVAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';

  // Fonte única de ícones quando disponível: window.Icones (js/utils/icones.js) tem os mesmos
  // traços para os ícones comuns. Os específicos do caso (Mesa, IA, RIF/SIMBA/IPED/IPEI/IRPF,
  // telefonia, Android/Apple, etc.) continuam locais. O fallback garante a página mesmo se o
  // módulo compartilhado falhar.
  function iconeCompartilhado(nome, fallback) {
    try {
      if (window.Icones && typeof window.Icones.html === "function") {
        var s = window.Icones.html(nome);
        if (s) return s;
      }
    } catch (e) {}
    return fallback;
  }
  ICONE_WHATSAPP = iconeCompartilhado("whatsapp", ICONE_WHATSAPP);
  ICONE_SMS = iconeCompartilhado("sms", ICONE_SMS);
  ICONE_CONTATOS = iconeCompartilhado("contatos", ICONE_CONTATOS);
  ICONE_LIGACOES = iconeCompartilhado("ligacoes", ICONE_LIGACOES);
  ICONE_LOCALIZACOES = iconeCompartilhado("localizacoes", ICONE_LOCALIZACOES);
  ICONE_NOTIFICACOES = iconeCompartilhado("notificacoes", ICONE_NOTIFICACOES);
  ICONE_NOTAS = iconeCompartilhado("notas", ICONE_NOTAS);
  ICONE_NAVEGACAO = iconeCompartilhado("navegacao", ICONE_NAVEGACAO);
  ICONE_APLICATIVOS = iconeCompartilhado("aplicativos", ICONE_APLICATIVOS);
  ICONE_ARQUIVOS = iconeCompartilhado("arquivos", ICONE_ARQUIVOS);
  ICONE_TIMELINE = iconeCompartilhado("timeline", ICONE_TIMELINE);
  ICONE_EMAIL = iconeCompartilhado("email", ICONE_EMAIL);
  ICONE_CHAVE = iconeCompartilhado("senhas", ICONE_CHAVE);
  ICONE_NUVEM = iconeCompartilhado("nuvem", ICONE_NUVEM);
  ICONE_MARCACOES = iconeCompartilhado("marcacoes", ICONE_MARCACOES);
  ICONE_EXTRAS = iconeCompartilhado("extras", ICONE_EXTRAS);
  ICONE_COMPARTILHAR = iconeCompartilhado("compartilhar", ICONE_COMPARTILHAR);
  ICONE_TRANSCREVER = iconeCompartilhado("transcrever", ICONE_TRANSCREVER);
  ICONE_APAGAR = iconeCompartilhado("apagar", ICONE_APAGAR);
  ICONE_PARAR = iconeCompartilhado("parar", ICONE_PARAR);

  // cor de fundo do botão por mensageiro (aproxima a identidade de cada app; fallback cinza)
  var CORES_APP = {
    Instagram: "#c13584", Telegram: "#229ed9", Facebook: "#1877f2", "Facebook Messenger": "#0084ff",
    Discord: "#5865f2", TikTok: "#010101", Twitter: "#1da1f2", X: "#111111", Signal: "#3a76f0",
    Snapchat: "#b8a300", Skype: "#00aff0", Viber: "#7360f2", WeChat: "#07c160", LINE: "#06c755",
    Kik: "#5bc236", Threema: "#2b7a2b", "WhatsApp Business": "#128c7e"
  };

  // "Mensageiros" que o Cellebrite fabrica a partir de bases do próprio iOS (KnowledgeC, Biome,
  // Recents) e não de um app de conversa: viram botão no cartão do aparelho sem levar a lugar
  // nenhum útil, porque esse mesmo conteúdo já é mostrado em Notificações, Aplicativos e nas
  // demais telas de uso do aparelho. Some do cartão; os dados continuam no banco e na API.
  var APPS_NAO_MENSAGEIROS = ["knowledgec", "biome", "recents"];

  function formatarBytes(bytes) {
    if (!bytes) return "";
    var unidades = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (bytes >= 1024 && i < unidades.length - 1) {
      bytes /= 1024;
      i++;
    }
    return bytes.toFixed(1) + " " + unidades[i];
  }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  // data pura ("2026-02-23", sem hora) — não passa por Date pra não escorregar de fuso
  function formatarData(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(iso);
  }

  var STATUS_LABEL = {
    // 'pendente' agora quer dizer "na fila do despachante, esperando vaga" — antes era só o
    // instante entre criar a linha e a thread pegar. Ver _despachante_extracoes.
    pendente: "Na fila",
    processando: "Processando…",
    concluido: "Concluído",
    erro: "Falhou",
    cancelado: "Cancelado"
  };

  // rótulo detalhado por fase (só enquanto 'processando') — mais informativo que "Processando…"
  var FASE_LABEL = {
    extraindo: "Extraindo textos",
    indexando: "Indexando anexos",
    hasheando: "Calculando hashes",
    contagens: "Criando índices",
    identificando_pessoas: "Identificando pessoas",
    // Nuvem IPED: as conversas só podem ser gravadas depois de ler o índice inteiro (a
    // mensagem é item filho do chat e a ordem do índice não garante o chat primeiro), então
    // esta fase aparece no fim de um caso de celular — ver funcoes/iped_dominios.py
    gravando_conversas: "Montando conversas",
    transcrevendo: "Transcrevendo áudios",
    indexando_semantica: "Indexando busca semântica",
    // cópia de uma prova de outro inquérito (ver docs/compartilhar_prova.md)
    copiando: "Copiando prova",
    cancelando: "Parando extração…",
    // fases da importação do extrato do SIMBA (mesmo badge, ver simba_utils.py)
    contas: "Lendo contas",
    lancamentos: "Carregando lançamentos",
    contrapartes: "Consolidando contrapartes",
    sintese: "Gerando síntese"
  };

  var STATUS_BADGE = {
    pendente: "badge-rodando",
    processando: "badge-rodando",
    concluido: "badge-ok",
    erro: "badge-erro",
    cancelado: "badge-erro"
  };

  function badgeStatus(status, erro, fase, progresso) {
    // enquanto processando, mostra a fase corrente (Extraindo textos / Criando índices / …)
    var rotulo = (status === "processando" && fase && FASE_LABEL[fase]) ? FASE_LABEL[fase] : (STATUS_LABEL[status] || status);
    // ... e a porcentagem quando a fase sabe medir ("Extraindo textos: 50%"). Fases sem
    // denominador (contagens, identificação de pessoas) vêm com progresso nulo e ficam só com o
    // rótulo — em .ufdr de dezenas de GB o nome da fase sozinho não distingue lento de travado.
    // "cancelando" é exceção: o pedido de parada zera o progresso, mas o laço da extração ainda
    // pode gravar mais uma medição antes de enxergar o cancelamento — "Parando extração…: 51%"
    if (status === "processando" && fase !== "cancelando" && progresso != null && progresso !== "") {
      rotulo += ": " + Number(progresso) + "%";
    }
    // Na lista com vários cards o polling atualiza dezenas de badges a cada tick: todos com
    // aria-live="polite" deixavam o leitor de tela tagarela. Anuncia só no modo detalhe
    // (um inquérito); na lista o resumo vai para #investigacao-live via renderInvestigacoes.
    var isLive = (status === "processando" || status === "pendente") && ehModoDetalheInquerito();
    return $("<span>")
      .addClass("badge " + (STATUS_BADGE[status] || ""))
      .attr("title", erro || "")
      .attr("role", "status")
      .attr("aria-live", isLive ? "polite" : "off")
      .html((status === "processando" ? '<span class="spinner" aria-hidden="true"></span> ' : "") + rotulo);
  }

  // Cópia de uma prova de outro inquérito (menu "Compartilhar prova"): o aparelho não foi
  // apreendido NESTE inquérito, e quem olha o card precisa saber disso — e de onde ele veio.
  // O número do inquérito de origem fica gravado na cópia, então ele continua aparecendo mesmo
  // depois que a origem for apagada (ver docs/compartilhar_prova.md).
  function badgeProvaCompartilhada(e) {
    if (!e.id_extracao_origem) return null;
    var origem = e.nu_inquerito_origem || "outro inquérito";
    return $("<span>")
      .addClass("badge badge-neutro")
      .attr("title", "Prova copiada do inquérito " + origem +
        (e.dt_compartilhamento ? " em " + formatarDataHora(e.dt_compartilhamento) : "") +
        ". O arquivo original é o mesmo nos dois inquéritos — nada foi duplicado no disco.")
      .html(ICONE_COMPARTILHAR + " Prova de " + origem);
  }

  // Os anexos (imagens, áudios, vídeos, documentos) não ficam no banco: são lidos sob demanda do
  // .ufdr original. Se o arquivo for apagado ou renomeado na pasta de dados, esses conteúdos param
  // de abrir — este badge avisa antes de o investigador topar com a falha dentro da conversa.
  function badgeArquivoUfdr(e) {
    if (e.in_arquivo_presente) {
      return $("<span>")
        .addClass("badge badge-ok")
        .attr("title", "O .ufdr está na pasta de dados" +
          (e.qt_bytes_arquivo ? " (" + formatarBytes(e.qt_bytes_arquivo) + ")" : "") +
          ". Anexos, fotos e áudios podem ser abertos.")
        .html(ICONE_ARQUIVO_OK + " Arquivo disponível");
    }
    return $("<span>")
      .addClass("badge badge-aviso")
      .attr("title", "O arquivo \"" + (e.no_arquivo || "") + "\" não está mais na pasta de dados "
        + "(apagado ou renomeado). Os dados já extraídos continuam no sistema, mas anexos, fotos e "
        + "áudios não abrem até o arquivo voltar com o mesmo nome.")
      .html(ICONE_ARQUIVO_AUSENTE + " Arquivo ausente");
  }

  // Hash (MD5/SHA-1/SHA-256) do(s) arquivo(s) de origem, para conferência de integridade
  // (cadeia de custódia) — ver db/init/25_rif.sql.
  function textoHashesArquivo(md5, sha1, sha256) {
    if (!md5 && !sha1 && !sha256) {
      return "Hash ainda não calculado — o arquivo está em processamento ou foi importado antes deste recurso existir.";
    }
    return "MD5:     " + (md5 || "—") + "\nSHA-1:   " + (sha1 || "—") + "\nSHA-256: " + (sha256 || "—");
  }

  function textoHashesRif(hashesArquivos) {
    if (!hashesArquivos || !hashesArquivos.length) {
      return "Hash ainda não calculado — o RIF foi importado antes deste recurso existir.";
    }
    var rotulos = { comunicacoes: "Comunicações", envolvidos: "Envolvidos", ocorrencias: "Ocorrências", pdf: "PDF" };
    return hashesArquivos.map(function (a) {
      return (rotulos[a.tp_arquivo] || a.tp_arquivo) + " — " + a.no_arquivo +
        (a.qt_bytes ? " (" + formatarBytes(a.qt_bytes) + ")" : "") +
        "\n" + textoHashesArquivo(a.co_hash_md5, a.co_hash_sha1, a.co_hash_sha256);
    }).join("\n\n");
  }

  // Item do menu "Mais ações" do bloco: o hash é conferência pontual de cadeia de custódia, não
  // algo que se abre em toda análise — como botão solto no cabeçalho ele disputava atenção com o
  // que o investigador realmente clica ali (prompt de IA e achados).
  function itemHash(titulo, mensagem) {
    return {
      html: ICONE_HASH + " Hash",
      title: "Hash do arquivo original, para conferência de integridade",
      onClick: function () { Dialogo.alerta({ titulo: titulo, mensagem: mensagem }); }
    };
  }

  // ---------- itens comuns a TODOS os blocos de importação do card ----------

  // Título do cabeçalho com apelido em destaque ("RIF do alvo · RIF 123/2025"), o mesmo padrão
  // do bloco de celular (tituloModeloComApelido). Sem apelido, o título como sempre.
  function tituloComApelido(obj, titulo) {
    var apelido = obj && obj.no_apelido ? $.trim(String(obj.no_apelido)) : "";
    return apelido ? apelido + " · " + titulo : titulo;
  }

  // Apelido das provas que NÃO são extração .ufdr (rif, simba, documento, iped, ipl, irpf, ipei,
  // telefonia) — mora em INQ_TB06_APELIDOS e é gravado pelo PUT genérico
  // /api/investigacao/provas/{tipo}/{id}/apelido. Mesma política do apelido da extração: só
  // identificação no card, qualquer um com acesso ao inquérito pode dar.
  function editarApelidoProva(tpProva, idProva, atual, rotulo) {
    var atualTrim = $.trim(atual || "");
    Dialogo.prompt({
      titulo: atualTrim ? "Editar apelido do bloco" : "Dar apelido ao bloco",
      mensagem: "Nome curto para identificar este bloco no card (ex.: " + rotulo + " do alvo). "
        + "Deixe em branco para remover. Só identificação — não altera o arquivo nem os dados.",
      valor: atualTrim,
      placeholder: "Ex.: " + rotulo + " do alvo",
      maxlength: 200,
      rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return; // cancelou
      var apelido = $.trim(texto || "");
      if (apelido === atualTrim) return; // nada mudou
      $.ajax({
        url: "/api/investigacao/provas/" + tpProva + "/" + encodeURIComponent(idProva) + "/apelido",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ apelido: apelido })
      }).done(function () {
        atualizarTudo();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o apelido." });
      });
    });
  }

  function itemApelidoProva(tpProva, idProva, obj, rotulo) {
    var atual = obj && obj.no_apelido ? $.trim(String(obj.no_apelido)) : "";
    return {
      html: ICONE_EDITAR + (atual ? " Editar apelido" : " Dar apelido"),
      title: atual
        ? "Apelido atual: \"" + atual + "\" — clique para alterar ou remover"
        : "Nome curto para identificar este bloco no card (ex.: " + rotulo + " do alvo)",
      onClick: function () { editarApelidoProva(tpProva, idProva, atual, rotulo); }
    };
  }

  // Compartilhar prova hoje só copia extrações de aparelho/nuvem e casos IPED
  // (funcoes/compartilhar_prova.py). Nos demais blocos o item entra DESABILITADO com o motivo —
  // item ausente virava "cadê o compartilhar?" toda vez.
  function itemCompartilharIndisponivel() {
    return {
      html: ICONE_COMPARTILHAR + " Compartilhar prova",
      title: "Indisponível para este tipo de prova: a cópia entre inquéritos hoje só existe para "
        + "extrações de aparelho/nuvem e casos IPED (docs/compartilhar_prova.md).",
      disabled: true,
      onClick: function () {}
    };
  }

  // Modal "Informações da prova": arquivo original, data da importação e o mais que cada prova
  // guarda (linhas [rótulo, valor] — linhas com valor vazio são puladas).
  function itemInfoProva(titulo, linhas) {
    return {
      html: ICONE_INFO + " Informações da prova",
      title: "Nome do arquivo original, data da importação e demais dados desta prova",
      onClick: function () { abrirInfoProva(titulo, linhas); }
    };
  }

  function abrirInfoProva(titulo, linhas) {
    $("#info-prova-titulo").text("Informações da prova — " + titulo);
    var $lista = $("#info-prova-lista").empty();
    var temLinha = false;
    (linhas || []).forEach(function (par) {
      var valor = par[1];
      if (valor === null || valor === undefined || String(valor).trim() === "") return;
      temLinha = true;
      var $linha = $("<dl>").addClass("info-prova-linha").css("margin", 0);
      $("<dt>").text(par[0]).appendTo($linha);
      $("<dd>").text(String(valor)).appendTo($linha);
      $lista.append($linha);
    });
    if (!temLinha) {
      $("<p>").addClass("empty-hint").text("Nenhuma informação registrada para esta prova.").appendTo($lista);
    }
    abrirModalAcessivel($("#modal-info-prova"), $("#info-prova-fechar"));
  }

  $("#info-prova-fechar").on("click", function () { fecharModalAcessivel($("#modal-info-prova")); });
  $("#modal-info-prova").on("click", function (e) {
    if (e.target === this) fecharModalAcessivel($(this));
  });

  // Navega para a conversa de IA já pedindo que fique restrita a um único documento (RIF/extrato
  // do SIMBA/IPL) em vez do inquérito inteiro — ia.js cria (ou reaproveita) a conversa com esse
  // escopo ao ver os parâmetros escopo_tipo/escopo_id na URL.
  function abrirPromptEscopo(idInquerito, tpEscopo, idEscopo, titulo) {
    window.location.href = "ia.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=" + encodeURIComponent(tpEscopo) +
      "&escopo_id=" + encodeURIComponent(idEscopo) +
      "&escopo_titulo=" + encodeURIComponent(titulo);
  }

  /* Quem decide se a IA está liberada é o backend: a listagem já traz ia_ativa pronto por
   * inquérito, combinando o flag IN_IA_ATIVA da delegacia de quem está logado com o da
   * delegacia dona do item (ver api/funcoes/ia_delegacia.py). A tela não repete a regra — antes
   * ela olhava o seletor de delegacia da página, que é filtro de pasta, não identidade: o botão do
   * card ficava cinza enquanto todo o resto da tela seguia abrindo a IA normalmente. */
  function iaBloqueada(item) {
    return !!item && item.ia_ativa === false;
  }

  function desabilitarBotaoIa($btn, item) {
    return $btn
      .css({ opacity: 0.4, cursor: "not-allowed" })
      .attr("disabled", true)
      .attr("title", "IA desativada para " + ((item && item.delegacia) || "esta delegacia"));
  }

  // Botão "Prompt de IA específico" de um bloco do card (RIF/extrato/IPL/IRPF/documento/aparelho).
  function botaoPromptIa(inq, titulo, aoClicar) {
    var $btn = $("<button>").addClass("btn btn-ia").html(ICONE_IA + " Prompt de IA<br/>específico");
    if (iaBloqueada(inq)) return desabilitarBotaoIa($btn, inq);
    return $btn.attr("title", titulo).on("click", aoClicar);
  }

  // "Hipótese da mídia": abre web/hipoteses.html FILTRADA nesta mídia (mesmo par de URL do chat
  // de IA por escopo) — a visão do inquérito inteiro está no menu "Extras" do card.
  function abrirAchadosEscopo(idInquerito, tpEscopo, idEscopo, titulo) {
    window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=" + encodeURIComponent(tpEscopo) +
      "&escopo_id=" + encodeURIComponent(idEscopo) +
      "&escopo_titulo=" + encodeURIComponent(titulo);
  }

  function podeGerenciar(item) {
    // item pode ter pode_gerenciar (do backend) ou ser um id de criador (fallback)
    if (typeof item === "object" && item !== null) {
      return !!item.pode_gerenciar;
    }
    return !!usuarioLogado && (usuarioLogado.in_admin || item === usuarioLogado.id_usuario);
  }

  function renderLinhaExtracao(e) {
    var $tr = $("<tr>").attr("data-id-extracao", e.id_extracao);
    $("<td>").addClass("col-nome").attr("data-label", "Inquérito").text(e.nu_inquerito).appendTo($tr);
    var $colArquivo = $("<td>").addClass("col-nome").attr("data-label", "Arquivo").appendTo($tr);
    var apelidoLinha = apelidoExtracao(e);
    if (apelidoLinha) {
      $("<strong>").text("🏷️ " + apelidoLinha).appendTo($colArquivo);
      $colArquivo.append($("<br>"));
    }
    $colArquivo.append(document.createTextNode(e.no_arquivo || "—"));
    $colArquivo.append($("<br>"));
    $colArquivo.append(badgeArquivoUfdr(e));
    if (!e.in_arquivo_presente && podeGerenciar(e)) {
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "4px 10px", fontSize: "11px", marginTop: "4px" })
        .html('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;vertical-align:-1px;margin-right:3px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Re-localizar')
        .on("click", function () { restaurarArquivo(e.id_extracao, e.no_arquivo); })
        .appendTo($colArquivo);
    }
    $("<td>").addClass("col-nome").attr("data-label", "Dispositivo").text(e.no_dispositivo || "—").appendTo($tr);
    $("<td>").addClass("col-registros").attr("data-label", "Conversas").text(formatarNumero(e.qt_conversas)).appendTo($tr);
    $("<td>").addClass("col-registros").attr("data-label", "Mensagens").text(formatarNumero(e.qt_mensagens)).appendTo($tr);
    $("<td>").attr("data-label", "Status").append(badgeStatus(e.cd_status, e.tx_erro, e.cd_fase, e.qt_progresso_fase).addClass("badge-status-extra")).appendTo($tr);
    $("<td>").addClass("col-nome").attr("data-label", "Início").text(formatarDataHora(e.dt_inicio_processamento)).appendTo($tr);
    $("<td>").addClass("col-nome").attr("data-label", "Finalização").text(formatarDataHora(e.dt_fim_processamento)).appendTo($tr);
    $("<td>").addClass("col-nome").attr("data-label", "Registrado por").text(e.registrado_por || "—").appendTo($tr);
    var $acoes = $("<td>").addClass("col-nome").attr("data-label", "Ações").appendTo($tr);
    if (podeGerenciar(e) && e.cd_status !== "processando" && e.cd_status !== "pendente") {
      var arquivoDisponivel = !!e.in_arquivo_presente;
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "6px 12px", fontSize: "12px", marginRight: "4px" })
        .prop("disabled", !arquivoDisponivel)
        .attr("title", arquivoDisponivel
          ? "Transcrever os áudios ainda não transcritos (qualidade média)."
          : "Arquivo .ufdr ausente — restaure o arquivo antes de transcrever.")
        .html(ICONE_TRANSCREVER + " Transcrever")
        .on("click", function () { transcreverExtracao(e.id_extracao, $(this)); })
        .appendTo($acoes);
      $("<button>")
        .addClass("btn btn-danger")
        .css({ padding: "6px 12px", fontSize: "12px" })
        .text("Apagar")
        .on("click", function () { apagarExtracao(e.id_extracao, e.nu_inquerito, $(this)); })
        .appendTo($acoes);
    }
    return $tr;
  }

  function apagarExtracao(idExtracao, numeroInquerito, $btn) {
    Dialogo.confirmar({
      titulo: "Apagar extração",
      mensagem: "Apagar esta extração do inquérito " + numeroInquerito + "? Todas as conversas, mensagens, anexos, contatos, ligações, localizações, notificações, páginas visitadas, aplicativos e dono do aparelho dela serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      if ($btn) $btn.prop("disabled", true).html('<span class="spinner"></span> Apagando…');
      $.ajax({ url: "/api/investigacao/extracoes/" + idExtracao, method: "DELETE" })
        .done(function () {
          atualizarTudo();
        })
        .fail(function (xhr) {
          if ($btn) $btn.prop("disabled", false).html(ICONE_APAGAR + " Apagar extração");
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a extração." });
        });
    });
  }

  function transcreverExtracao(idExtracao, $btn) {
    Dialogo.confirmar({
      titulo: "Transcrever áudios",
      mensagem: "Transcrever os áudios ainda não transcritos desta extração? A transcrição usa a qualidade média (modelo 'small') e pode demorar conforme a quantidade de áudios.",
      rotuloOk: "Transcrever"
    }).then(function (ok) {
      if (!ok) return;
      if ($btn) $btn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');
      $.ajax({ url: "/api/investigacao/extracoes/" + idExtracao + "/transcrever", method: "POST" })
        .done(function (resp) {
          atualizarTudo();
          if (resp && resp.ok === false) {
            Toast.aviso(resp.mensagem || "Não há áudios pendentes de transcrição nesta extração.");
          } else {
            Toast.sucesso(resp && resp.mensagem ? resp.mensagem : "Transcrição iniciada — acompanhe o progresso no status da extração.");
          }
        })
        .fail(function (xhr) {
          if ($btn) $btn.prop("disabled", false).html(ICONE_TRANSCREVER + " Transcrever");
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a transcrição." });
        });
    });
  }

  function restaurarArquivo(idExtracao, noArquivo) {
    Dialogo.prompt({
      titulo: "Re-localizar arquivo .ufdr",
      mensagem: "O arquivo \"" + noArquivo + "\" não está mais na pasta de dados. Informe o caminho completo no servidor onde ele se encontra atualmente para copiá-lo de volta.\n\nEx.: /mnt/arquivos/extracoes/dispositivo.ufdr",
      rotuloOk: "Copiar",
      maxlength: 2000
    }).then(function (caminho) {
      if (caminho === null || !$.trim(caminho)) return;
      caminho = $.trim(caminho);
      $.ajax({
        url: "/api/investigacao/extracoes/" + idExtracao + "/restaurar-arquivo",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ caminho: caminho })
      }).done(function () {
        atualizarTudo();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao restaurar o arquivo." });
      });
    });
  }

  function cancelarExtracao(idExtracao, copiando) {
    Dialogo.confirmar({
      // A cópia de uma prova é tudo ou nada: parada no meio, ela é DESFEITA e o aparelho some
      // deste inquérito — metade de uma prova passaria por prova inteira. Diferente da extração,
      // em que o que já foi lido do .ufdr fica.
      titulo: copiando ? "Parar cópia da prova" : "Parar extração",
      mensagem: copiando
        ? "Parar a cópia em andamento? A cópia é desfeita e o aparelho não fica neste inquérito — para trazê-lo, compartilhe a prova de novo."
        : "Parar o processamento em andamento? Os dados já extraídos serão preservados.",
      rotuloOk: "Parar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/extracoes/" + idExtracao + "/cancelar", method: "POST" })
        .done(atualizarTudo)
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível parar a extração." });
        });
    });
  }

  // "COMPARTILHAR PROVA" — o mesmo aparelho apreendido costuma ser prova em mais de um
  // inquérito, e reimportar o .ufdr lá custa horas de parsing, hash, transcrição e indexação
  // para produzir exatamente o que já está no banco. A cópia leva minutos e o arquivo NÃO é
  // duplicado no disco: os dois inquéritos passam a ler os anexos do mesmo .ufdr.
  // Ver docs/compartilhar_prova.md.
  var $modalProva;
  var provaCtx = null; // { idExtracao }

  function montarModalProva() {
    var tpl = document.getElementById("tpl-compartilhar-prova");
    if (tpl && "content" in tpl) {
      $modalProva = $(tpl.content.firstElementChild.cloneNode(true));
    } else {
      // fallback para navegadores sem <template>: mesma estrutura do template no HTML
      $modalProva = $("#modal-compartilhar-prova");
    }
    if (!$modalProva || !$modalProva.length) return;
    $("body").append($modalProva);
    configurarTrapFocus($modalProva);
    $modalProva.find("#cp-fechar").on("click", function () { fecharModalAcessivel($modalProva); });
    $modalProva.on("click", function (e) { if (e.target === this) fecharModalAcessivel($modalProva); });
    $modalProva.find("#cp-copiar-btn").on("click", enviarCompartilhamento);
  }

  function compartilharProva(idExtracao, titulo) {
    if (!$modalProva) montarModalProva();
    provaCtx = { idExtracao: idExtracao };
    $("#cp-aparelho").text(titulo || "");
    $("#cp-status").text("").removeClass("success error");
    var $select = $("#cp-destino").empty().prop("disabled", true);
    var $btn = $("#cp-copiar-btn").prop("disabled", true).text("Copiar prova");
    abrirModalAcessivel($modalProva, $select);

    $.getJSON("/api/investigacao/extracoes/" + idExtracao + "/destinos-compartilhamento")
      .done(function (resp) {
        var destinos = (resp && resp.destinos) || [];
        if (!destinos.length) {
          $("#cp-status").addClass("error").text("Não há outro inquérito disponível para receber esta prova.");
          return;
        }
        var disponiveis = 0;
        destinos.forEach(function (d) {
          // o inquérito que já tem a prova aparece DESABILITADO, e não escondido: sumir com ele
          // deixaria o investigador procurando o que não some da lista
          var rotulo = d.nu_inquerito + (d.no_delegacia ? " — " + d.no_delegacia : "");
          if (d.ja_tem) rotulo += "  (já tem esta prova)";
          else disponiveis++;
          $("<option>").attr("value", d.id_inquerito).prop("disabled", !!d.ja_tem).text(rotulo).appendTo($select);
        });
        var $primeiro = $select.find("option:not(:disabled)").first();
        $select.val($primeiro.attr("value") || "").prop("disabled", false);
        $btn.prop("disabled", !disponiveis);
        if (!disponiveis) {
          $("#cp-status").addClass("error").text("Todos os inquéritos disponíveis já têm esta prova.");
        }
      })
      .fail(function (xhr) {
        $("#cp-status").addClass("error").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao listar os inquéritos.");
      });
  }

  function enviarCompartilhamento() {
    if (!provaCtx) return;
    var idDestino = Number($("#cp-destino").val());
    if (!idDestino) return;
    var $btn = $("#cp-copiar-btn").prop("disabled", true).html('<span class="spinner"></span> Copiando…');
    $("#cp-status").text("").removeClass("success error");
    $.ajax({
      url: "/api/investigacao/extracoes/" + provaCtx.idExtracao + "/compartilhar",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ id_inquerito_destino: idDestino })
    }).done(function (resp) {
      fecharModalAcessivel($modalProva);
      atualizarTudo();
      Toast.sucesso((resp && resp.mensagem) || "Cópia iniciada.");
    }).fail(function (xhr) {
      $btn.prop("disabled", false).text("Copiar prova");
      $("#cp-status").addClass("error").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao compartilhar a prova.");
    });
  }

  function carregarModalExtracoes() {
    $.getJSON("/api/investigacao/extracoes").done(function (lista) {
      encurtar(lista);
      $corpo.empty();
      lista.forEach(function (e) {
        if (e.no_fabricante === "IPED") return; // extração-ponte do caso IPED — ver agruparExtracoes
        $corpo.append(renderLinhaExtracao(e));
      });
      // reaplica o filtro de texto após recarregar (polling com modal aberto)
      if ($extracoesBusca && $extracoesBusca.length) $extracoesBusca.trigger("input");
    });
  }

  function renomearInquerito(inq) {
    Dialogo.prompt({
      titulo: "Editar número do inquérito",
      mensagem: "Novo número do inquérito no ePol. Vale para todas as extrações deste inquérito.",
      valor: inq.nu_inquerito || "",
      maxlength: 50,
      rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return; // cancelou
      var numero = $.trim(texto);
      if (!numero || numero === inq.nu_inquerito) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/numero",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ numero_inquerito: numero })
      }).done(function () {
        atualizarTudo(); // recarrega a lista com o novo número
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao renomear o inquérito." });
      });
    });
  }

  function alternarSituacaoInquerito(inq) {
    var arquivar = inq.tp_situacao !== "arquivado";
    Dialogo.confirmar({
      titulo: arquivar ? "Arquivar inquérito" : "Reativar inquérito",
      mensagem: arquivar
        ? "Arquivar o inquérito " + inq.nu_inquerito + "? Nada é apagado: o caso segue íntegro e navegável, "
          + "mas passa a constar como encerrado no filtro \"Arquivados\" das investigações."
        : "Reativar o inquérito " + inq.nu_inquerito + "? Ele volta a constar como caso ativo.",
      rotuloOk: arquivar ? "Arquivar" : "Reativar"
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/situacao",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ situacao: arquivar ? "arquivado" : "ativo" })
      }).done(function () {
        atualizarTudo(); // selo e menu refletem a situação nova
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao mudar a situação do inquérito." });
      });
    });
  }

  function editarContextoInquerito(inq) {
    Dialogo.prompt({
      titulo: "Contexto da investigação",
      mensagem: "Descreva a investigação para orientar a IA: tipo penal suspeito, alvo, apelidos, hipótese de "
        + "trabalho, o que procurar. Fica salvo no inquérito e entra em toda conversa de IA.",
      valor: inq.tx_contexto_investigacao || "",
      multilinha: true,
      maxlength: 8000,
      rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return; // cancelou
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/contexto",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ texto: texto })
      }).done(function () {
        inq.tx_contexto_investigacao = $.trim(texto); // reflete sem recarregar a lista
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o contexto." });
      });
    });
  }

  // Apelido livre da extração (.ufdr): nome curto dado pelo investigador só para
  // identificar o aparelho no card (ex.: "Celular do João"). Não altera arquivo, escopo
  // de IA nem achados — ver NO_APELIDO em INQ_TB02_EXTRACOES.
  function apelidoExtracao(e) {
    return $.trim(e && e.no_apelido ? String(e.no_apelido) : "");
  }

  // Título do cabeçalho com apelido em destaque, sem uppercar o que o usuário digitou:
  // "Celular do João · MOTOROLA MOTO G35". Sem apelido, só o modelo como antes.
  function tituloModeloComApelido(e, modelo) {
    var apelido = apelidoExtracao(e);
    return apelido ? apelido + " · " + modelo : modelo;
  }

  function editarApelidoExtracao(e) {
    var atual = apelidoExtracao(e);
    Dialogo.prompt({
      titulo: atual ? "Editar apelido da extração" : "Dar apelido à extração",
      mensagem: "Nome curto para identificar este aparelho no card (ex.: Celular do João). "
        + "Deixe em branco para remover. Só identificação — não altera o arquivo nem os dados.",
      valor: atual,
      placeholder: "Ex.: Celular do João",
      maxlength: 200,
      rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return; // cancelou
      var apelido = $.trim(texto || "");
      if (apelido === atual) return; // nada mudou
      $.ajax({
        url: "/api/investigacao/extracoes/" + e.id_extracao + "/apelido",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ apelido: apelido })
      }).done(function () {
        e.no_apelido = apelido || null; // reflete sem recarregar a lista quando dá
        atualizarTudo();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o apelido." });
      });
    });
  }

  // cache de delegacias para não refazer request a cada abertura
  var cacheDelegacias = null;
  var cacheDelegaciasTs = 0;
  function obterDelegacias(cacheMs) {
    var now = Date.now();
    if (cacheDelegacias && (now - cacheDelegaciasTs) < (cacheMs || 300000)) {
      return $.Deferred().resolve(cacheDelegacias).promise();
    }
    return $.getJSON("/api/delegacias").done(function (lista) {
      cacheDelegacias = lista;
      cacheDelegaciasTs = now;
    });
  }

  function abrirModalDelegacia(inq) {
    idInqueritoDelegacia = inq.id_inquerito;
    delegaciaAtualInquerito = inq.delegacia || "";
    $delegaciaStatus.text("").removeClass("success error");
    abrirModalAcessivel($modalDelegacia, $delegaciaSelect);
    $delegaciaSelect.empty().append('<option value="">(nenhuma)</option>');
    obterDelegacias().done(function (lista) {
      lista.forEach(function (d) {
        $delegaciaSelect.append($("<option>").val(d.no_delegacia).text(d.no_delegacia));
      });
      $delegaciaSelect.val(delegaciaAtualInquerito);
      // sincroniza seletor global da topbar se estiver em modo detalhe
      if (ehModoDetalheInquerito() && inq.delegacia) {
        $("#investigacao-delegacia-select").val(inq.delegacia);
      }
    });
  }

  $("#delegacia-salvar-btn").on("click", function () {
    var valor = $delegaciaSelect.val() || "";
    var url = "/api/investigacao/inqueritos/" + idInqueritoDelegacia + "/delegacia";
    $.ajax({
      url: url,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({ delegacia: valor })
    }).done(function () {
      $delegaciaStatus.text("Salvo!").addClass("success");
      atualizarTudo();
      setTimeout(function () { fecharModalAcessivel($modalDelegacia); }, 800);
    }).fail(function (xhr) {
      $delegaciaStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.").addClass("error");
    });
  });

  $modalDelegacia.on("click", function (e) {
    if (e.target === $modalDelegacia[0]) fecharModalAcessivel($modalDelegacia);
  });
  $("#delegacia-fechar").on("click", function () { fecharModalAcessivel($modalDelegacia); });

  // ---------- Gerar relatório (padrão + registros marcados) ----------

  var $modalRel; // modal construído sob demanda, reaproveitado entre inquéritos

  function truncarTexto(texto, max) {
    if (!texto) return "";
    return texto.length > max ? texto.slice(0, max - 1) + "…" : texto;
  }

  // Seleção do relatório salva por (inquérito, padrão): reabrir o modal não perde o que
  // já foi marcado. Guarda ids + valores manuais + flags de IA; na restauração, ids que não
  // existem mais (marcações desfeitas, blocos removidos do padrão) são ignorados.
  function grChaveSel() {
    if (!relCtx || !$("#gr-padrao-select").val()) return null;
    return "relatorio-sel:" + relCtx.idInquerito + ":" + $("#gr-padrao-select").val();
  }
  function grLerSel() {
    try {
      var chave = grChaveSel();
      if (!chave) return null;
      var raw = localStorage.getItem(chave);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function grSalvarSel(sel) {
    try {
      var chave = grChaveSel();
      if (!chave || !sel) return;
      var doc = {
        blocos: idsMarcados(sel.checksBlocos || []),
        marcacoes: (sel.checksMarcacoes || []).filter(function ($cb) { return $cb.prop("checked"); })
          .reduce(function (acc, $cb) { return acc.concat($cb.data("ids") || []); }, []),
        rif: idsMarcados(sel.checksRif || []),
        extratos: idsMarcados(sel.checksBancario || []),
        ipl: idsMarcados(sel.checksIpl || []),
        valores: {},
        ia: !!(sel.comentarioIa && sel.comentarioIa.prop("checked")),
        reescrever: !!(sel.reescreverComentarios && sel.reescreverComentarios.prop("checked"))
      };
      Object.keys(sel.inputsManuais || {}).forEach(function (tok) {
        try { doc.valores[tok] = sel.inputsManuais[tok].val(); } catch (e) {}
      });
      localStorage.setItem(chave, JSON.stringify(doc));
    } catch (e) {}
  }
  function grAplicarSel(sel) {
    var doc = grLerSel();
    if (!doc || !sel) return;
    var marcarPorId = function ($checks, ids) {
      if (!ids || !ids.length) return;
      var set = {};
      ids.forEach(function (id) { set[String(id)] = true; });
      $checks.forEach(function ($cb) {
        var id = $cb.data("id");
        if (id != null && set[String(id)]) $cb.prop("checked", true);
        else if (id != null) $cb.prop("checked", false);
      });
    };
    if (doc.blocos && doc.blocos.length) marcarPorId(sel.checksBlocos || [], doc.blocos);
    if (doc.rif) marcarPorId(sel.checksRif || [], doc.rif);
    if (doc.extratos) marcarPorId(sel.checksBancario || [], doc.extratos);
    if (doc.ipl) marcarPorId(sel.checksIpl || [], doc.ipl);
    if (doc.marcacoes && sel.checksMarcacoes && sel.checksMarcacoes.length) {
      var setM = {};
      doc.marcacoes.forEach(function (id) { setM[String(id)] = true; });
      sel.checksMarcacoes.forEach(function ($cb) {
        var temAlgum = ($cb.data("ids") || []).some(function (id) { return setM[String(id)]; });
        var temTodos = ($cb.data("ids") || []).every(function (id) { return setM[String(id)]; });
        // linha agrupada: marca se algum id dela estava salvo (filtro de criador pode ter
        // escondido o resto — nunca desmarca silenciosamente o que não estava na visão)
        if (($cb.data("ids") || []).length) $cb.prop("checked", temAlgum ? true : (doc.marcacoes.length ? false : $cb.prop("checked")));
        if (temTodos && ($cb.data("ids") || []).length > 1) $cb.prop("checked", true);
      });
    }
    Object.keys(doc.valores || {}).forEach(function (tok) {
      if (sel.inputsManuais && sel.inputsManuais[tok]) sel.inputsManuais[tok].val(doc.valores[tok]);
    });
    if (sel.comentarioIa) sel.comentarioIa.prop("checked", !!doc.ia);
    if (sel.reescreverComentarios) sel.reescreverComentarios.prop("checked", !!doc.reescrever);
  }

  function montarModalRelatorio() {
    var tpl = document.getElementById("tpl-gerar-relatorio");
    if (tpl && "content" in tpl) {
      $modalRel = $(tpl.content.firstElementChild.cloneNode(true));
    } else {
      $modalRel = $("#modal-gerar-relatorio");
    }
    if (!$modalRel || !$modalRel.length) return;
    $("body").append($modalRel);
    configurarTrapFocus($modalRel);
    $modalRel.find("#gr-fechar").on("click", function () { fecharModalAcessivel($modalRel); });
    $modalRel.on("click", function (e) { if (e.target === this) fecharModalAcessivel($modalRel); });
    $modalRel.on("keydown", function (e) {
      if (e.key === "Escape" && !$modalRel.hasClass("hidden")) fecharModalAcessivel($modalRel);
    });
    $modalRel.find("#gr-padrao-select").on("change", carregarPreparacaoRelatorio);
    $modalRel.find("#gr-baixar-btn").on("click", function () {
      if (relCtx && relCtx.ultimaGeracao) baixarGeracao(relCtx.ultimaGeracao);
    });
  }

  var relCtx = null; // { idInquerito, ultimaGeracao }
  var seqPreparacaoRel = 0; // corrida de padrão: resposta antiga não sobrescreve a nova

  function grErroPadroes(msg) {
    var $e = $("#gr-erro-padroes").empty().removeClass("hidden");
    $e.text(msg);
    var $btn = $("<button>").addClass("btn btn-secondary gr-filtros-limpar").css({ marginLeft: "8px" }).text("Tentar de novo");
    $btn.on("click", function () { $e.addClass("hidden").empty(); abrirGerarRelatorio({ id_inquerito: relCtx.idInquerito, nu_inquerito: $("#gr-inquerito").text() }); });
    $e.append($btn);
  }

  function abrirGerarRelatorio(inq) {
    if (!$modalRel) montarModalRelatorio();
    clearTimeout(timerGeracao);
    relCtx = { idInquerito: inq.id_inquerito, ultimaGeracao: relCtx && relCtx.idInquerito === inq.id_inquerito ? relCtx.ultimaGeracao : null };
    $("#gr-inquerito").text(inq.nu_inquerito);
    $("#gr-detalhe").empty();
    $("#gr-resumo").addClass("hidden").empty();
    $("#gr-status").text("").removeClass("success error");
    $("#gr-gerar-btn").prop("disabled", true).off("click");
    $("#gr-baixar-btn").addClass("hidden").off("click").on("click", function () {
      if (relCtx && relCtx.ultimaGeracao) baixarGeracao(relCtx.ultimaGeracao);
    });
    var $select = $("#gr-padrao-select").empty().prop("disabled", true);
    $("#gr-sem-padrao").hide();
    $("#gr-erro-padroes").addClass("hidden").empty();

    $.getJSON("/api/relatorios/padroes-ativos")
      .done(function (lista) {
        if (!relCtx || relCtx.idInquerito !== inq.id_inquerito) return;
        if (!lista.length) {
          $("#gr-sem-padrao").show();
          $select.hide();
          return;
        }
        $select.show().prop("disabled", false);
        lista.forEach(function (p) { $("<option>").attr("value", p.id_padrao).text(p.no_padrao).appendTo($select); });
        // mantém o último padrão usado neste inquérito, se ainda existir
        try {
          var ultimo = localStorage.getItem("relatorio-padrao:" + inq.id_inquerito);
          if (ultimo && $select.find("option[value='" + ultimo + "']").length) $select.val(ultimo);
        } catch (e) {}
        carregarPreparacaoRelatorio();
        carregarHistoricoRelatorio();
      })
      .fail(function (xhr) {
        if (!relCtx || relCtx.idInquerito !== inq.id_inquerito) return;
        $select.show().prop("disabled", false);
        grErroPadroes((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível carregar os padrões de relatório.");
      });
    abrirModalAcessivel($modalRel, $("#gr-padrao-select"));
  }

  function carregarHistoricoRelatorio() {
    if (!relCtx) return;
    var $hist = $("#gr-historico").addClass("hidden");
    var $lista = $("#gr-historico-lista").empty();
    $.getJSON("/api/investigacao/inqueritos/" + relCtx.idInquerito + "/geracoes-relatorio/recentes")
      .done(function (itens) {
        if (!relCtx || !itens || !itens.length) return;
        $hist.removeClass("hidden");
        itens.forEach(function (g) {
          var $linha = $("<div>").addClass("gr-historico-linha").appendTo($lista);
          var rotulo = (g.no_arquivo || ("Geração #" + g.id_geracao)) +
            " — " + (g.cd_status === "concluido" ? "concluído" : g.cd_status === "erro" ? ("falhou: " + (g.tx_erro || "erro")) : (g.tx_etapa_atual || g.cd_status));
          $("<span>").text(rotulo).appendTo($linha);
          if (g.cd_status === "concluido" && g.tem_arquivo) {
            $("<button>").addClass("btn btn-secondary gr-filtros-limpar").text("Baixar")
              .on("click", function () { baixarGeracao(g.id_geracao); }).appendTo($linha);
          }
          if (g.cd_status === "pendente" || g.cd_status === "processando") {
            $("<button>").addClass("btn btn-secondary gr-filtros-limpar").text("Acompanhar")
              .on("click", function () {
                $("#gr-status").text("Retomando acompanhamento…").removeClass("success error");
                acompanharGeracao(g.id_geracao, $("#gr-gerar-btn"));
              }).appendTo($linha);
          }
        });
      });
  }

  var COR_NOMES = {
    "#E74C3C": "Vermelho", "#E67E22": "Laranja", "#F1C40F": "Amarelo",
    "#2ECC71": "Verde", "#1ABC9C": "Verde-água", "#3498DB": "Azul",
    "#9B59B6": "Roxo", "#E91E63": "Rosa", "#795548": "Marrom",
    // "Âmbar" e não "Laranja": o #E67E22 já é o laranja, e dois nomes iguais na mesma paleta
    // deixavam a coluna "Marcador" ambígua (ver COR_NOMES em api/funcoes/relatorios_utils.py)
    "#607D8B": "Cinza", "#00BCD4": "Ciano", "#FF9800": "Âmbar",
  };

  // checklist simples (checkbox + rótulo) para os arquivos de RIF/extrato bancário — mesma ideia
  // do "Selecionar todos" da tabela de marcações, só que sem as colunas extras que não fazem
  // sentido pra esses dois casos (não há aplicativo/contato/cor, é um arquivo importado inteiro).
  function criarChecklistArquivos($detalhe, titulo, itens, idFn, labelFn, semItensTexto, aoMudar) {
    var $checks = [];
    var $wrap = $("<div>").addClass("field").appendTo($detalhe);
    var $titulo = $("<label>").text(titulo + " (" + itens.length + ")").appendTo($wrap);
    if (!itens.length) {
      $("<p>").addClass("field-help").text(semItensTexto).appendTo($wrap);
      return $checks;
    }
    var $box = $("<div>").css({ maxHeight: "200px", overflowY: "auto", border: "1px solid var(--border)", borderRadius: "8px" }).appendTo($wrap);
    // .field-check é o padrão já usado na página para checkbox+rótulo lado a lado (ver
    // "Transcrever também o áudio dos vídeos") — sem ela, o seletor genérico ".field input"
    // estica o checkbox pra 100% da largura da linha (só faz sentido pra <input type="text">).
    var $todosLbl = $("<label>").addClass("field-check").css({ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontWeight: "600" }).appendTo($box);
    var $todos = $("<input>").attr({ type: "checkbox", "aria-label": "Selecionar todos: " + titulo }).prop("checked", true).appendTo($todosLbl);
    $todosLbl.append(document.createTextNode("Selecionar todos"));
    var notificar = function () { if (typeof aoMudar === "function") aoMudar(); };
    itens.forEach(function (item) {
      var $linha = $("<label>").addClass("field-check").css({ padding: "6px 8px", borderBottom: "1px solid var(--border)" }).appendTo($box);
      var $cb = $("<input>").attr({ type: "checkbox", "aria-label": labelFn(item) }).prop("checked", true).data("id", idFn(item)).appendTo($linha);
      $cb.on("change", function () {
        var marcados = $checks.filter(function ($c) { return $c.prop("checked"); }).length;
        $todos.prop("checked", marcados === $checks.length);
        $todos.prop("indeterminate", marcados > 0 && marcados < $checks.length);
        $titulo.text(titulo + " (" + marcados + " de " + itens.length + " selecionados)");
        notificar();
      });
      $checks.push($cb);
      $linha.append(document.createTextNode(labelFn(item)));
    });
    $todos.on("change", function () {
      var marcado = $(this).prop("checked");
      $checks.forEach(function ($cb) { $cb.prop("checked", marcado); });
      $titulo.text(titulo + " (" + (marcado ? itens.length : 0) + " de " + itens.length + " selecionados)");
      notificar();
    });
    $titulo.text(titulo + " (" + itens.length + " de " + itens.length + " selecionados)");
    return $checks;
  }

  function carregarPreparacaoRelatorio() {
    var idPadrao = $("#gr-padrao-select").val();
    if (!idPadrao || !relCtx) return;
    var seq = ++seqPreparacaoRel;
    try { localStorage.setItem("relatorio-padrao:" + relCtx.idInquerito, String(idPadrao)); } catch (e) {}
    var $detalhe = $("#gr-detalhe").empty();
    $("#gr-resumo").addClass("hidden").empty();
    $("#gr-baixar-btn").addClass("hidden");
    $("<p>").addClass("field-help").html('<span class="spinner" aria-hidden="true"></span> Carregando opções do padrão…').appendTo($detalhe);
    $("#gr-padrao-select").prop("disabled", true);
    $("#gr-gerar-btn").prop("disabled", true).off("click");
    $("#gr-status").text("").removeClass("success error");

    $.getJSON("/api/investigacao/inqueritos/" + relCtx.idInquerito + "/relatorios/" + idPadrao + "/preparar")
      .done(function (dados) {
        if (seq !== seqPreparacaoRel || !relCtx) return;
        $("#gr-padrao-select").prop("disabled", false);
        encurtar(dados.arquivos_rif); encurtar(dados.arquivos_bancario);
        encurtar(dados.arquivos_ipl); encurtar(dados.marcacoes);
        $detalhe.empty();

        var selRef = { checksBlocos: [], checksRif: [], checksBancario: [], checksIpl: [], checksMarcacoes: [], inputsManuais: {}, comentarioIa: null, reescreverComentarios: null, dados: dados };
        var contaMarcados = function ($checks) {
          return ($checks || []).filter(function ($cb) { return $cb.prop("checked"); }).length;
        };
        var contaMarcacoesSel = function () {
          return (selRef.checksMarcacoes || []).filter(function ($cb) { return $cb.prop("checked"); })
            .reduce(function (acc, $cb) { return acc + (($cb.data("ids") || []).length); }, 0);
        };
        var atualizarResumo = function () {
          var partes = [];
          var nBlocos = contaMarcados(selRef.checksBlocos);
          var totalBlocos = (dados.blocos || []).length;
          partes.push("Seções " + nBlocos + "/" + totalBlocos);
          if (dados.mostrar_marcacoes) partes.push("Marcações " + contaMarcacoesSel() + "/" + (dados.marcacoes || []).length);
          if (dados.arquivos_rif) partes.push("RIF " + contaMarcados(selRef.checksRif) + "/" + dados.arquivos_rif.length);
          if (dados.arquivos_bancario) partes.push("Extratos " + contaMarcados(selRef.checksBancario) + "/" + dados.arquivos_bancario.length);
          if (dados.arquivos_ipl) partes.push("IPL " + contaMarcados(selRef.checksIpl) + "/" + dados.arquivos_ipl.length);
          var faltam = Object.keys(selRef.inputsManuais || {}).filter(function (tok) {
            try { return !$.trim(selRef.inputsManuais[tok].val()); } catch (e) { return false; }
          });
          if (faltam.length) partes.push("⚠ " + faltam.length + " variável(is) vazia(s): " + faltam.join(", "));
          var $r = $("#gr-resumo").removeClass("hidden");
          $r.text(partes.join(" · "));
          grSalvarSel(selRef);
        };
        selRef.contaMarcados = contaMarcados;
        selRef.contaMarcacoesSel = contaMarcacoesSel;
        selRef.atualizarResumo = atualizarResumo;

        // seções (blocos) do padrão — o padrão é global e só o admin edita; aqui quem gera tira
        // uma seção DESTE relatório sem mexer no modelo que os outros casos usam. O rótulo do
        // tipo vem pronto do servidor (rotulo_tipo), pra tela não manter uma cópia do mapa.
        var $checksBlocos = criarChecklistArquivos(
          $detalhe, "Seções deste relatório", dados.blocos || [],
          function (b) { return b.id_bloco; },
          function (b) { return b.no_titulo ? b.no_titulo + " — " + b.rotulo_tipo : b.rotulo_tipo; },
          "Este padrão não tem blocos. Cadastre-os em \"Padrão de relatórios\".",
          atualizarResumo
        );
        selRef.checksBlocos = $checksBlocos;

        // variáveis manuais (inputs) — vazias viram {{token}} literal no .docx, então o
        // resumo avisa e o gerar pede confirmação antes de sair com campo em branco.
        var manuais = dados.tokens_manuais || [];
        var $inputsManuais = {};
        selRef.inputsManuais = $inputsManuais;
        if (manuais.length) {
          var $wrap = $("<div>").appendTo($detalhe);
          $("<label>").css({ display: "block", marginBottom: "6px", fontWeight: "600" }).text("Variáveis a preencher").appendTo($wrap);
          $("<p>").addClass("field-help").text("Campos vazios saem como {{nome}} no documento, para você notar o que faltou.").appendTo($wrap);
          manuais.forEach(function (tok, i) {
            var $f = $("<div>").addClass("field").appendTo($wrap);
            var idInput = "gr-manual-" + i;
            $("<label>").attr("for", idInput).text("{{" + tok + "}}").appendTo($f);
            $inputsManuais[tok] = $("<input>").attr({ type: "text", id: idInput, autocomplete: "off" }).appendTo($f);
            $inputsManuais[tok].on("input", atualizarResumo);
          });
        }

        // opções de IA — cada uma só aparece quando o padrão tem bloco em que ela mexe: o resumo
        // nos blocos de conversa (chat/whatsapp), a reescrita onde o comentário do analista é
        // impresso (chat e a tabela de marcações). São coisas independentes e podem ir juntas.
        var $comentarioIa = null;
        var $reescreverComentarios = null;
        if (dados.mostrar_comentario_ia || dados.mostrar_reescrever_comentarios) {
          var $iaWrap = $("<div>").addClass("field").appendTo($detalhe);
          var $iaLinha = $("<div>").css({ display: "flex", gap: "18px", flexWrap: "wrap" }).appendTo($iaWrap);
          var opcaoIa = function (rotulo, ajuda) {
            var $col = $("<div>").css({ flex: "1 1 260px", minWidth: "220px" }).appendTo($iaLinha);
            var $lbl = $("<label>").addClass("field-check").appendTo($col);
            var $cb = $("<input>").attr("type", "checkbox").appendTo($lbl);
            $cb.on("change", function () { grSalvarSel(selRef); });
            $lbl.append(document.createTextNode(rotulo));
            $("<p>").addClass("field-help").text(ajuda).appendTo($col);
            return $cb;
          };
          if (dados.mostrar_comentario_ia) {
            $comentarioIa = opcaoIa(
              "Resumo das conversas por IA",
              "Gera um resumo por IA de cada conjunto de mensagens marcadas (por marcador) em cada conversa do relatório."
            );
          }
          if (dados.mostrar_reescrever_comentarios) {
            $reescreverComentarios = opcaoIa(
              "Reescrever comentários do analista por IA",
              "A IA só reescreve o que você anotou nas marcações — corrige e completa a frase, sem acrescentar fato novo. Só o texto da anotação vai ao modelo, e falhando a chamada sai o original. Cada anotação diferente é uma chamada, então a geração demora mais."
            );
          }
        }
        selRef.comentarioIa = $comentarioIa;
        selRef.reescreverComentarios = $reescreverComentarios;

        // RIF e extrato bancário — o padrão usa TODOS os arquivos importados no inquérito por
        // padrão; aqui o usuário escolhe quais entram, quando há mais de um
        var $checksRif = [];
        if (dados.arquivos_rif) {
          $checksRif = criarChecklistArquivos(
            $detalhe, "Arquivos de RIF para o relatório", dados.arquivos_rif,
            function (r) { return r.id_rif; },
            function (r) { return (r.nu_rif ? "RIF " + r.nu_rif : r.no_pasta) + " (" + (r.qt_comunicacoes || 0) + " comunicações)"; },
            "Nenhum RIF importado neste inquérito.",
            atualizarResumo
          );
        }
        var $checksBancario = [];
        if (dados.arquivos_bancario) {
          $checksBancario = criarChecklistArquivos(
            $detalhe, "Extratos bancários para o relatório", dados.arquivos_bancario,
            function (s) { return s.id_extrato; },
            function (s) { return s.no_arquivo + " (" + (s.qt_lancamentos || 0) + " lançamentos)"; },
            "Nenhum extrato bancário importado neste inquérito.",
            atualizarResumo
          );
        }
        var $checksIpl = [];
        if (dados.arquivos_ipl) {
          $checksIpl = criarChecklistArquivos(
            $detalhe, "Inquéritos em PDF (ePol) para o relatório", dados.arquivos_ipl,
            function (p) { return p.id_ipl; },
            function (p) { return (p.nu_ipl ? "IPL " + p.nu_ipl : p.no_arquivo) + " (" + (p.qt_folhas || 0) + " folhas)"; },
            "Nenhum inquérito em PDF importado neste inquérito.",
            atualizarResumo
          );
        }
        selRef.checksRif = $checksRif;
        selRef.checksBancario = $checksBancario;
        selRef.checksIpl = $checksIpl;

        // marcações — agrupadas por (dispositivo, aplicativo, contato, cor); só aparece em
        // padrões com bloco de marcações genéricas/chat/whatsapp (ver mostrar_marcacoes)
        var marcacoes = dados.marcacoes || [];
        var $checks = [];
        // "use strict": função declarada dentro do bloco else fica presa no bloco e a chamada
        // lá embaixo quebra com ReferenceError — por isso a função nasce aqui, no escopo do done()
        var aplicarFiltro = null;
        if (dados.mostrar_marcacoes) {
          var $mwrap = $("<div>").addClass("field").appendTo($detalhe);
          var $mlabel = $("<label>").text("Registros marcados para o relatório (" + marcacoes.length + ")").appendTo($mwrap);
          var nomeLogadoPrevio = (typeof usuarioLogado !== "undefined" && usuarioLogado && usuarioLogado.no_usuario) ? usuarioLogado.no_usuario : "";
          if (!marcacoes.length) {
            // Mesmo sem nenhum registro, o filtro de criador já nasce no usuário logado —
            // deixa claro de quem é a visão e mantém o padrão quando surgirem marcações.
            var $filtrosVazio = $("<div>").css({ display: "flex", gap: "8px", margin: "6px 0", flexWrap: "wrap" }).appendTo($mwrap);
            var $selCriadorVazio = $("<select>").css({ flex: "1 1 auto", minWidth: "180px" }).appendTo($filtrosVazio);
            $("<option>").attr("value", "").text("Todos os criadores").appendTo($selCriadorVazio);
            if (nomeLogadoPrevio) {
              $("<option>").attr("value", nomeLogadoPrevio).text(nomeLogadoPrevio).appendTo($selCriadorVazio);
              $selCriadorVazio.val(nomeLogadoPrevio);
            } else {
              $.getJSON("/api/auth/eu").done(function (eu) {
                var nome = eu && eu.no_usuario ? eu.no_usuario : "";
                if (!nome) return;
                if (!$selCriadorVazio.find("option").filter(function () { return $(this).attr("value") === nome; }).length) {
                  $("<option>").attr("value", nome).text(nome).appendTo($selCriadorVazio);
                }
                $selCriadorVazio.val(nome);
              });
            }
            $("<p>").addClass("field-help").text("Nenhum registro marcado neste inquérito. Use \"🗒️ Marcar para o relatório\" nas telas de dados.").appendTo($mwrap);
          } else {
            // agrupa por chave composta. O marcador entrou no lugar da cor: agrupar por "cor"
            // juntava numa linha só coisas que o investigador batizou de formas diferentes.
            // O criador entra na chave: filtrar por criador só esconde a linha, então dois
            // investigadores com o mesmo marcador no mesmo contato não podem morar na mesma
            // linha — senão o filtro mostraria o registro do outro junto.
            var nomeLogado = nomeLogadoPrevio;
            var grupos = {};
            marcacoes.forEach(function (m) {
              var marcadores = m.marcadores || [];
              var rotuloMarcador = marcadores.length
                ? marcadores.map(function (mk) { return mk.no_marcador; }).join(" + ")
                : (m.co_cor ? (COR_NOMES[m.co_cor] || m.co_cor) : "Sem marcador");
              var cor = marcadores.length ? marcadores[0].co_cor : (m.co_cor || null);
              var criador = m.criado_por || "—";
              var chave = (m.no_arquivo || "—") + "|" + (m.no_aplicativo || "—") + "|" + (m.no_contato || "") + "|" + rotuloMarcador + "|" + criador;
              if (!grupos[chave]) grupos[chave] = { ids: [], fonte: m.no_arquivo || "—", app: m.no_aplicativo || "—", contato: m.no_contato || "", cor: cor, marcador: rotuloMarcador, rotulo: m.rotulo_origem, criador: criador };
              grupos[chave].ids.push(m.id_marcacao);
            });
            var linhas = Object.keys(grupos).map(function (k) { return grupos[k]; });

            // busca + filtros por marcador e por criador: num caso grande a lista corrida com
            // scroll não permite achar o que incluir. Filtrar só ESCONDE a linha — nunca desmarca —
            // e por isso o rótulo passa a dizer quantos registros estão selecionados no total: um
            // item marcado fora do filtro não pode entrar no relatório sem que ninguém veja.
            // O filtro de criador nasce no usuário logado (mesmo que ele não tenha marcado nada —
            // nesse caso ele entra nas opções de todo jeito); "Todos os criadores" mostra tudo.
            var $filtros = $("<div>").css({ display: "flex", gap: "8px", margin: "6px 0", flexWrap: "wrap" }).appendTo($mwrap);
            var $busca = $("<input>").attr({ type: "search", placeholder: "Buscar por arquivo, aplicativo, marcador, criador ou conteúdo…", "aria-label": "Buscar marcações" })
              .css({ flex: "1 1 100%" }).appendTo($filtros);
            var $selMarcador = $("<select>").attr("aria-label", "Filtrar por marcador").css({ flex: "1 1 auto", minWidth: "180px" }).appendTo($filtros);
            $("<option>").attr("value", "").text("Todos os marcadores").appendTo($selMarcador);
            linhas.map(function (g) { return g.marcador; })
              .filter(function (m, i, arr) { return arr.indexOf(m) === i; })
              .sort()
              .forEach(function (m) { $("<option>").attr("value", m).text(m).appendTo($selMarcador); });
            var $selCriador = $("<select>").attr("aria-label", "Filtrar por criador").css({ flex: "1 1 auto", minWidth: "180px" }).appendTo($filtros);
            $("<option>").attr("value", "").text("Todos os criadores").appendTo($selCriador);
            var criadores = linhas.map(function (g) { return g.criador; })
              .filter(function (c, i, arr) { return arr.indexOf(c) === i; })
              .sort();
            if (nomeLogado && criadores.indexOf(nomeLogado) === -1) criadores.push(nomeLogado);
            criadores.sort().forEach(function (c) { $("<option>").attr("value", c).text(c).appendTo($selCriador); });
            if (nomeLogado) $selCriador.val(nomeLogado);
            var $limparFiltros = $("<button>").attr("type", "button").addClass("btn btn-secondary gr-filtros-limpar").text("Limpar filtros").appendTo($filtros);
            $limparFiltros.on("click", function () {
              $busca.val("");
              $selMarcador.val("");
              $selCriador.val("");
              aplicarFiltro();
              $busca.focus();
            });

            var $box = $("<div>").css({ maxHeight: "260px", overflowY: "auto", border: "1px solid var(--border)", borderRadius: "8px" }).appendTo($mwrap);
            var $tab = $("<table>").css({ width: "100%", borderCollapse: "collapse", fontSize: "13px" }).appendTo($box);

            var $thead = $("<thead>").appendTo($tab);
            var $trh = $("<tr>").appendTo($thead);
            var $todos = $("<input>").attr("type", "checkbox").prop("checked", true);
            [$todos, "Arquivo UFDR", "Aplicativo", "Marcador", "Criado por", "Conteúdo"].forEach(function (col, i) {
              var $th = $("<th>").css({
                textAlign: i === 0 ? "center" : "left", padding: "6px 8px", position: "sticky", top: 0,
                background: "var(--bg-card)", borderBottom: "1px solid var(--border)", fontWeight: "600", whiteSpace: "nowrap"
              }).appendTo($trh);
              if (i === 0) $th.append(col); else $th.text(col);
            });

            // "oculta" é dado da linha, não estilo consultado: :visible responderia "não" para
            // TODAS as linhas se o modal estivesse fechado, e o contador mentiria
            function visivel($cb) { return !$cb.closest("tr").data("oculta"); }

            var $vazioFiltro = $("<p>").addClass("gr-vazio-filtro hidden").text("Nenhum registro para estes filtros.").appendTo($mwrap);
            var $contVisiveis = $("<p>").addClass("field-help").appendTo($mwrap);

            function atualizarRotuloMarcacoes() {
              var selecionados = 0;
              $checks.forEach(function ($cb) {
                if ($cb.prop("checked")) selecionados += ($cb.data("ids") || []).length;
              });
              $mlabel.text(
                "Registros marcados para o relatório (" + selecionados + " de " + marcacoes.length + " selecionados)"
              );
              // "Selecionar todos" reflete só o que está visível — e fica indeterminado na seleção parcial
              var visiveis = $checks.filter(visivel);
              var marcadosVisiveis = visiveis.filter(function ($cb) { return $cb.prop("checked"); }).length;
              $todos.prop("checked", visiveis.length > 0 && marcadosVisiveis === visiveis.length);
              $todos.prop("indeterminate", marcadosVisiveis > 0 && marcadosVisiveis < visiveis.length);
              $vazioFiltro.toggleClass("hidden", visiveis.length > 0);
              $contVisiveis.text(visiveis.length + " de " + $checks.length + " grupos visíveis");
              atualizarResumo();
              grSalvarSel(selRef);
            }

            $todos.on("change", function () {
              var marcado = $(this).prop("checked");
              $checks.forEach(function ($cb) {
                if (visivel($cb)) $cb.prop("checked", marcado);
              });
              atualizarRotuloMarcacoes();
            });

            var $tbody = $("<tbody>").appendTo($tab);
            linhas.forEach(function (g) {
              var $tr = $("<tr>").appendTo($tbody);
              var celula = { padding: "6px 8px", borderBottom: "1px solid var(--border)", verticalAlign: "top" };

              var $cb = $("<input>").attr({ type: "checkbox", "aria-label": g.marcador + " — " + g.fonte }).prop("checked", true);
              $cb.data("ids", g.ids);
              $cb.on("change", atualizarRotuloMarcacoes);
              $checks.push($cb);
              selRef.checksMarcacoes.push($cb);
              $("<td>").css($.extend({}, celula, { textAlign: "center", width: "34px" })).append($cb).appendTo($tr);

              $("<td>").css($.extend({}, celula, { whiteSpace: "nowrap" })).text(g.fonte).appendTo($tr);
              $("<td>").css($.extend({}, celula, { whiteSpace: "nowrap" })).text(g.app).appendTo($tr);

              var $corTd = $("<td>").css($.extend({}, celula, { whiteSpace: "nowrap" })).appendTo($tr);
              if (g.cor) {
                $("<span>").addClass("rel-cor-indicator").css("background", g.cor).appendTo($corTd);
              }
              $corTd.append(" " + g.marcador);

              $("<td>").css($.extend({}, celula, { whiteSpace: "nowrap" })).text(g.criador).appendTo($tr);

              var texto = g.contato || truncarTexto(g.rotulo || "(sem conteúdo)", 120);
              if (g.ids.length > 1) texto += " (" + g.ids.length + " itens)";
              $("<td>").css(celula).text(texto).appendTo($tr);

              // o texto de busca é montado uma vez, na criação da linha, e não a cada tecla
              $tr.data("busca", (g.fonte + " " + g.app + " " + g.marcador + " " + g.criador + " " + texto).toLowerCase());
              $tr.data("marcador", g.marcador);
              $tr.data("criador", g.criador);
            });

            aplicarFiltro = function () {
              var termo = $.trim($busca.val()).toLowerCase();
              var marcador = $selMarcador.val();
              var criador = $selCriador.val();
              $tbody.children("tr").each(function () {
                var $tr = $(this);
                var casa = (!termo || $tr.data("busca").indexOf(termo) !== -1)
                  && (!marcador || $tr.data("marcador") === marcador)
                  && (!criador || $tr.data("criador") === criador);
                $tr.data("oculta", !casa).toggle(casa);
              });
              atualizarRotuloMarcacoes();
            }
            $busca.on("input", aplicarFiltro);
            $selMarcador.on("change", aplicarFiltro);
            $selCriador.on("change", aplicarFiltro);
            // usuarioLogado pode ainda não ter chegado quando o modal abre (é outro GET): se não
            // deu pra descobrir o nome, busca agora e aplica como padrão sem mexer no resto.
            if (!nomeLogado) {
              $.getJSON("/api/auth/eu").done(function (eu) {
                var nome = eu && eu.no_usuario ? eu.no_usuario : "";
                if (!nome) return;
                if (!$selCriador.find("option").filter(function () { return $(this).attr("value") === nome; }).length) {
                  $("<option>").attr("value", nome).text(nome).appendTo($selCriador);
                }
                $selCriador.val(nome);
                aplicarFiltro();
              });
            }
            aplicarFiltro();
          }
        }

        selRef.checksMarcacoes = $checks.length ? $checks : selRef.checksMarcacoes;
        selRef.inputsManuais = $inputsManuais;
        // restaura a seleção salva deste (inquérito, padrão) e recalcula rótulos/contadores
        grAplicarSel(selRef);
        if ($checks.length && aplicarFiltro) aplicarFiltro();
        else atualizarResumo();

        // botão "restaurar tudo" — limpa o salvamento e remarca 100%
        var $restaurar = $("<button>").attr("type", "button").addClass("btn btn-secondary gr-filtros-limpar").text("Marcar tudo de novo").css({ marginTop: "8px" });
        $restaurar.on("click", function () {
          try { localStorage.removeItem(grChaveSel()); } catch (e) {}
          ($checksBlocos || []).forEach(function ($cb) { $cb.prop("checked", true); });
          ($checksRif || []).forEach(function ($cb) { $cb.prop("checked", true); });
          ($checksBancario || []).forEach(function ($cb) { $cb.prop("checked", true); });
          ($checksIpl || []).forEach(function ($cb) { $cb.prop("checked", true); });
          ($checks || []).forEach(function ($cb) { $cb.prop("checked", true); });
          Object.keys($inputsManuais || {}).forEach(function (tok) { $inputsManuais[tok].val(""); });
          if ($checks.length && aplicarFiltro) aplicarFiltro();
          else atualizarResumo();
        });
        $("<div>").append($restaurar).appendTo($detalhe);

        $("#gr-gerar-btn").prop("disabled", false).off("click").on("click", function () {
          gerarRelatorio(idPadrao, {
            inputsManuais: $inputsManuais, checksMarcacoes: $checks, dados: dados,
            checksRif: $checksRif, checksBancario: $checksBancario, checksIpl: $checksIpl,
            checksBlocos: $checksBlocos, comentarioIa: $comentarioIa,
            reescreverComentarios: $reescreverComentarios
          });
        });
      })
      .fail(function (xhr) {
        if (seq !== seqPreparacaoRel) return;
        $("#gr-padrao-select").prop("disabled", false);
        $detalhe.empty();
        $("<p>").addClass("form-status error").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao preparar a geração.").appendTo($detalhe);
        var $retry = $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Tentar de novo");
        $retry.on("click", carregarPreparacaoRelatorio);
        $retry.appendTo($detalhe);
        $("#gr-status").text("").removeClass("success error");
      });
  }

  function idsMarcados($checks) {
    return $checks.filter(function ($cb) { return $cb.prop("checked"); }).map(function ($cb) { return $cb.data("id"); });
  }

  var timerGeracao = null; // um relatório por vez na tela; guardado pra não acumular cadeias

  function gerarRelatorio(idPadrao, sel) {
    var valores = {};
    Object.keys(sel.inputsManuais).forEach(function (tok) { valores[tok] = $.trim(sel.inputsManuais[tok].val()); });

    var dados = sel.dados;
    var corpo = { valores: valores };
    corpo.ids_marcacoes = dados.mostrar_marcacoes
      ? sel.checksMarcacoes.filter(function ($cb) { return $cb.prop("checked"); }).reduce(function (acc, $cb) { return acc.concat($cb.data("ids")); }, [])
      : null;
    corpo.ids_rif = dados.arquivos_rif ? idsMarcados(sel.checksRif) : null;
    corpo.ids_extratos = dados.arquivos_bancario ? idsMarcados(sel.checksBancario) : null;
    corpo.ids_ipl = dados.arquivos_ipl ? idsMarcados(sel.checksIpl) : null;
    corpo.ids_blocos = idsMarcados(sel.checksBlocos);
    corpo.in_comentario_ia = !!(sel.comentarioIa && sel.comentarioIa.prop("checked"));
    corpo.in_reescrever_comentarios = !!(sel.reescreverComentarios && sel.reescreverComentarios.prop("checked"));

    if (!corpo.ids_blocos.length) {
      $("#gr-status").text("Selecione ao menos uma seção para o relatório.").addClass("error");
      return;
    }
    // variáveis vazias viram {{token}} literal no .docx — confirma em vez de sair calado
    var vazios = Object.keys(sel.inputsManuais).filter(function (tok) { return !valores[tok]; });
    var seguir = function () { grSalvarSel(sel); dispararGeracao(idPadrao, corpo, $("#gr-gerar-btn")); };
    if (vazios.length) {
      Dialogo.confirmar({
        titulo: "Variáveis vazias",
        mensagem: "Estes campos vão sair como {{...}} no documento: " + vazios.join(", ") + ". Gerar assim mesmo?",
        rotuloOk: "Gerar assim mesmo"
      }).then(function (ok) { if (ok) seguir(); });
      return;
    }
    if (dados.mostrar_marcacoes && !corpo.ids_marcacoes.length) {
      Dialogo.confirmar({
        titulo: "Nenhuma marcação selecionada",
        mensagem: "Nenhum registro marcado vai entrar neste relatório. Gerar só com as outras seções?",
        rotuloOk: "Gerar assim mesmo"
      }).then(function (ok) { if (ok) seguir(); });
      return;
    }
    seguir();
  }

  function dispararGeracao(idPadrao, corpo, $btn) {
    $btn = $($btn).prop("disabled", true);
    $("#gr-baixar-btn").addClass("hidden");
    $("#gr-status").html('<span class="spinner" aria-hidden="true"></span> Gerando…').removeClass("success error");

    // a geração roda em segundo plano (passava do timeout do proxy em casos grandes com IA): o
    // POST devolve o id e o andamento vem por polling, como na indexação semântica
    $.ajax({
      url: "/api/investigacao/inqueritos/" + relCtx.idInquerito + "/relatorios/" + idPadrao + "/gerar",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(corpo)
    }).done(function (resp) {
      acompanharGeracao(resp.id_geracao, $btn);
    }).fail(function (xhr) {
      var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao gerar o relatório.";
      if (xhr.status === 409) {
        msg += " Use “Últimos relatórios → Acompanhar” para retomar a geração ativa.";
        carregarHistoricoRelatorio();
      }
      $("#gr-status").text(msg).addClass("error");
      $btn.prop("disabled", false);
    });
  }

  function acompanharGeracao(idGeracao, $btn) {
    clearTimeout(timerGeracao);
    $btn = $($btn);
    $.getJSON("/api/investigacao/geracoes-relatorio/" + idGeracao)
      .done(function (g) {
        if (g.cd_status === "pendente" || g.cd_status === "processando") {
          var etapa = g.tx_etapa_atual || "Gerando…";
          var contagem = g.qt_total ? " (" + (g.qt_feitos || 0) + "/" + g.qt_total + ")" : "";
          $("#gr-status").html('<span class="spinner" aria-hidden="true"></span> ').append(document.createTextNode(etapa + contagem)).removeClass("success error");
          // o timer não depende do DOM do modal: se o usuário fechar a janela no meio, a geração
          // termina e o download acontece do mesmo jeito, em vez de o arquivo ficar órfão
          timerGeracao = setTimeout(function () { acompanharGeracao(idGeracao, $btn); }, 2000);
          return;
        }
        $btn.prop("disabled", false);
        if (g.cd_status === "concluido") {
          if (relCtx) relCtx.ultimaGeracao = idGeracao;
          baixarGeracao(idGeracao);
          $("#gr-status").text("Relatório gerado (.docx baixado). Se o download não abriu, use “Baixar novamente”.").addClass("success");
          $("#gr-baixar-btn").removeClass("hidden");
          carregarHistoricoRelatorio();
        } else {
          $("#gr-status").text(g.tx_erro || "Erro ao gerar o relatório.").addClass("error");
        }
      })
      .fail(function () {
        $btn.prop("disabled", false);
        $("#gr-status").text("Não foi possível acompanhar a geração do relatório. Tente “Últimos relatórios → Acompanhar”.").addClass("error");
      });
  }

  function baixarGeracao(idGeracao) {
    // âncora sintética: o Content-Disposition da rota faz o navegador baixar sem sair da página
    // (não é mais um blob — o arquivo está no servidor)
    var a = document.createElement("a");
    a.href = "/api/investigacao/geracoes-relatorio/" + idGeracao + "/download";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function pintarBotaoIndexacao($btn, st) {
    if (!st) { $btn.html("🧠 Indexar busca semântica").prop("disabled", false); return; }
    if (st.cd_status === "processando" || st.cd_status === "pendente") {
      $btn.html("🧠 Indexando… " + (st.qt_feitos || 0) + "/" + (st.qt_total || 0)).prop("disabled", true);
    } else if (st.cd_status === "erro") {
      $btn.html("🧠 Reindexar (erro)").prop("disabled", false).attr("title", st.tx_erro || "Erro na indexação");
    } else { // concluido
      $btn.html("🧠 Busca semântica ✓ (" + (st.qt_indexado || 0) + ")").prop("disabled", false);
    }
  }

  // timers do polling da indexação semântica por inquérito — a cada re-render o botão renasce e
  // dispararia uma nova cadeia; limpar a anterior evita cadeias órfãs acumuladas.
  var timersIndexacao = {};

  function acompanharIndexacao(idInquerito, $btn) {
    clearTimeout(timersIndexacao[idInquerito]);
    if (!$btn || !$btn.length) {
      delete timersIndexacao[idInquerito];
      return;
    }
    // Na primeira chamada o botão ainda não entrou no DOM (o card está sendo montado):
    // não dá para exigir document.contains aqui, senão a cadeia morre antes de nascer.
    // Botão destacado no meio do voo é inofensivo — só pinta nó fora da tela.
    // Aba oculta: adia sem request para não gastar bateria/rede.
    if (document.hidden) {
      timersIndexacao[idInquerito] = setTimeout(function () { acompanharIndexacao(idInquerito, $btn); }, 5000);
      return;
    }
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/indexacao-semantica")
      .done(function (st) {
        // pode ter saído do DOM enquanto o GET voava
        if (!$btn[0] || !document.contains($btn[0])) { delete timersIndexacao[idInquerito]; return; }
        pintarBotaoIndexacao($btn, st);
        if (st && (st.cd_status === "processando" || st.cd_status === "pendente")) {
          timersIndexacao[idInquerito] = setTimeout(function () { acompanharIndexacao(idInquerito, $btn); }, 3000);
        } else {
          delete timersIndexacao[idInquerito];
        }
      })
      .fail(function () {
        timersIndexacao[idInquerito] = setTimeout(function () { acompanharIndexacao(idInquerito, $btn); }, 8000);
      });
  }

  function indexarSemantica(idInquerito, $btn) {
    $btn.prop("disabled", true);
    $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/indexacao-semantica", method: "POST" })
      .done(function () {
        acompanharIndexacao(idInquerito, $btn);
      })
      .fail(function (xhr) {
        $btn.prop("disabled", false);
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a indexação.";
        Dialogo.alerta({ titulo: "Busca semântica", mensagem: msg });
        acompanharIndexacao(idInquerito, $btn);
      });
  }

  // ---------- menu "Mais ações" (agrupa ações secundárias do card de investigação) ----------

  $(document).on("click", function () {
    $(".acoes-menu.aberto").removeClass("aberto").find(".acoes-menu-lista").addClass("hidden").end().find(".acoes-menu-toggle").attr("aria-expanded", "false");
  });
  $(document).on("keydown", function (e) {
    if (e.key === "Escape") {
      $(".acoes-menu.aberto").removeClass("aberto").find(".acoes-menu-lista").addClass("hidden").end().find(".acoes-menu-toggle").attr("aria-expanded", "false");
      // fecha o modal do topo com Esc, restaurando foco e scroll via fecharModalAcessivel
      var $visiveis = $(".modal-overlay:not(.hidden)");
      $visiveis.each(function () { fecharModalAcessivel($(this)); });
    }
  });

  // itens: array de "divisor" | {html, title, onClick, perigoso, disabled} | {$el: <botão já pronto>}
  // opts.compacto: só o ⋮, sem o rótulo — no cabeçalho dos blocos o espaço é disputado com o
  // nome do arquivo, e "Mais ações" por extenso empurrava os botões para uma segunda linha.
  // opts.rotulo/opts.icone: o card de investigação tem DOIS menus lado a lado ("Extras" e
  // "Gerência do inquérito"), e um "Mais ações" repetido não diria o que há dentro de cada um.
  // opts.alinharDireita: a lista abre para a esquerda — o menu mais à direita do card
  // transbordaria a borda com o alinhamento padrão.
  function criarMenuAcoes(itens, opts) {
    var compacto = !!(opts && opts.compacto);
    var rotulo = (opts && opts.rotulo) || "Mais ações";
    var icone = (opts && opts.icone) || ICONE_MAIS;
    var $menu = $("<div>").addClass("acoes-menu" + (compacto ? " acoes-menu-compacto" : "") +
                                    (opts && opts.alinharDireita ? " acoes-menu-direita" : ""));
    var $toggle = $("<button>")
      .addClass("btn btn-secondary acoes-menu-toggle")
      .attr("title", rotulo)
      .attr("aria-label", rotulo)
      .attr("aria-haspopup", "menu")
      .attr("aria-expanded", "false")
      .html(compacto ? icone : icone + " " + rotulo)
      .appendTo($menu);
    var $lista = $("<div>").addClass("acoes-menu-lista hidden").attr("role", "menu").on("click", function (evt) { evt.stopPropagation(); }).appendTo($menu);

    function fechar() {
      $menu.removeClass("aberto");
      $lista.addClass("hidden");
      $toggle.attr("aria-expanded", "false");
    }

    function abrir() {
      $(".acoes-menu.aberto").removeClass("aberto").find(".acoes-menu-lista").addClass("hidden").prev(".acoes-menu-toggle").attr("aria-expanded", "false");
      $menu.addClass("aberto");
      $lista.removeClass("hidden");
      $toggle.attr("aria-expanded", "true");
      // foca primeiro item para navegação por teclado
      var $first = $lista.find(".acoes-menu-item:not(:disabled)").first();
      if ($first.length) $first.focus();
    }

    itens.forEach(function (item) {
      if (item === "divisor") {
        $("<div>").addClass("acoes-menu-divisor").attr("role", "separator").appendTo($lista);
        return;
      }
      if (item.$el) {
        item.$el.addClass("acoes-menu-item").attr("role", "menuitem").on("click", fechar).appendTo($lista);
        return;
      }
      $("<button>")
        .addClass("acoes-menu-item" + (item.perigoso ? " perigoso" : ""))
        .attr("role", "menuitem")
        .prop("disabled", !!item.disabled)
        .attr("title", item.title || "")
        .html(item.html)
        .on("click", function () {
          fechar();
          item.onClick();
        })
        .appendTo($lista);
    });

    $toggle.on("click", function (evt) {
      evt.stopPropagation();
      var estavaAberto = $menu.hasClass("aberto");
      if (estavaAberto) fechar(); else abrir();
    });

    $toggle.on("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (!$menu.hasClass("aberto")) abrir();
      } else if (e.key === "Escape") {
        fechar();
      }
    });

    $lista.on("keydown", function (e) {
      var $itens = $lista.find(".acoes-menu-item:not(:disabled)");
      var idx = $itens.index(document.activeElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        $itens.eq((idx + 1) % $itens.length).focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        $itens.eq((idx - 1 + $itens.length) % $itens.length).focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        fechar();
        $toggle.focus();
      } else if (e.key === "Home") {
        e.preventDefault();
        $itens.first().focus();
      } else if (e.key === "End") {
        e.preventDefault();
        $itens.last().focus();
      }
    });

    return $menu;
  }

  function renderCardInvestigacao(inq, extracoes) {
    var $card = $("<div>").addClass("investigacao-card")
      .attr("data-nu-inquerito", inq.nu_inquerito)
      .attr("data-id-inquerito", inq.id_inquerito);

    var $info = $("<div>").addClass("investigacao-info").appendTo($card);
    $("<strong>").text(inq.nu_inquerito).appendTo($info);
    if (inq.in_caso_inteligencia) {
      $("<span>").addClass("badge badge-aviso").text("CASO DE INTELIGÊNCIA").appendTo($info);
    }
    if (inq.tp_situacao === "arquivado") {
      $("<span>").addClass("badge badge-neutro").attr("title", "Inquérito arquivado pelo gestor — o caso segue íntegro e navegável").text("ARQUIVADO").appendTo($info);
    }
    $("<span>").text(formatarNumero(inq.qt_extracoes) + " extração(ões)").appendTo($info);
    $("<span>")
      .addClass("investigacao-admin")
      .html(ICONE_DONO + " Administrador: ")
      .append($("<strong>").text(inq.administrador || "—"))
      .appendTo($info);
    if (inq.tem_extracao_em_andamento) {
      badgeStatus("processando", null, inq.fase_em_andamento, inq.progresso_em_andamento)
        .addClass("badge-status-inquerito em-andamento")
        .appendTo($info);
    }

    // O alerta de convergência entra depois, para a PÁGINA toda de uma vez — ver
    // carregarConvergenciasDaPagina.

    // Ações do caso inteiro: Mesa + IA + Relatório ficam sempre visíveis. Marcações e o
    // resto entram na gaveta "Extras" para não afogar o card em botões (eram 4 primários + 2
    // menus + 8 abas por card).
    var $acoesInq = $("<div>").addClass("investigacao-actions").appendTo($card);
    $("<button>")
      .addClass("btn btn-mesa")
      .html(ICONE_MESA + " Mesa de investigação")
      .on("click", function () {
        window.location.href = "mesa_inquerito.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
      })
      .appendTo($acoesInq);
    var iaHref = "ia.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
    var $btnIa = $("<button>").addClass("btn btn-ia").html(ICONE_IA + " Prompt de IA");
    if (iaBloqueada(inq)) {
      desabilitarBotaoIa($btnIa, inq);
    } else {
      $btnIa.on("click", function () { window.location.href = iaHref; });
    }
    $btnIa.appendTo($acoesInq);
    $("<button>")
      .addClass("btn btn-secondary")
      .attr("title", "Gera um relatório a partir de um padrão + os registros marcados para o relatório")
      .html(ICONE_RELATORIO + " Gerar relatório")
      .on("click", function () { abrirGerarRelatorio(inq); })
      .appendTo($acoesInq);
    var $btnSemantica = $("<button>")
      .attr("title", "Gera os vetores das mensagens para a busca semântica da IA (roda em segundo plano)")
      .html("🧠 Indexar busca semântica")
      .on("click", function () {
        indexarSemantica(inq.id_inquerito, $btnSemantica);
      });
    acompanharIndexacao(inq.id_inquerito, $btnSemantica); // reflete estado atual (se já rodando/concluído)

    // Duas gavetas em vez de uma "Mais ações" só: "Extras" são as ANÁLISES que o investigador
    // roda sobre o caso, "Gerência do inquérito" é a administração do próprio registro (quem vê,
    // de que delegacia é, apagar). Misturadas, "Apagar inquérito" morava a um item de distância
    // de "Documentos".
    //
    // Ferramenta que depende de dado ausente entra DESABILITADA com o motivo no title, e não
    // some: com o item simplesmente não desenhado, "cadê o Comparar rostos?" virava pergunta
    // toda vez que o inquérito ainda não tinha extração.
    var temExtracao = Number(inq.qt_extracoes) > 0;
    var doisAparelhos = Number(inq.qt_extracoes) > 1;
    var temFinanceiro = Number(inq.qt_rifs) > 0 || Number(inq.qt_simba) > 0;
    // A busca de entidades varre TUDO que foi importado (ver docs/busca_entidades.md), então
    // basta ter qualquer material — não só o financeiro, como quando ela só olhava RIF e SIMBA.
    var temMaterial = temFinanceiro || temExtracao || Number(inq.qt_iped) > 0 ||
      Number(inq.qt_ipls) > 0 ||
      Number(inq.qt_irpfs) > 0 || Number(inq.qt_ipeis) > 0 || Number(inq.qt_telefonia) > 0 ||
      Number(inq.qt_documentos) > 0;

    var itensMenu = [
      {
        html: ICONE_MARCACOES + " Marcações",
        title: "Seus marcadores neste inquérito: conversas e mensagens etiquetadas, com exportação",
        onClick: function () {
          window.location.href = "marcacoes.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      "divisor",
      {
        html: ICONE_DONO + " Comparar rostos",
        disabled: !temExtracao,
        title: temExtracao
          ? "Busca um rosto de referência nas imagens de todos os aparelhos do inquérito"
          : "Nenhuma extração de celular neste inquérito.",
        onClick: function () {
          window.location.href = "comparar_rosto.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_DISPOSITIVO + " Comparar celulares",
        disabled: !doisAparelhos,
        title: doisAparelhos
          ? "O que os aparelhos do inquérito têm em comum: o mesmo arquivo, interlocutores, agenda e Wi-Fi"
          : "É preciso ter pelo menos dois aparelhos no inquérito para comparar.",
        onClick: function () {
          window.location.href = "comparar_celulares.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_NUVEM + " Nuvem × aparelho",
        // mesma condição de "Comparar celulares" (duas extrações) e não "tem uma de nuvem e uma
        // de aparelho": a listagem de inquéritos não separa os dois tipos, e buscar esse número
        // aqui custaria uma agregação a mais em toda abertura da tela. A própria página diz que
        // falta um dos lados quando for o caso — e diz melhor, porque sabe qual dos dois falta.
        disabled: !doisAparelhos,
        title: doisAparelhos
          ? "Arquivos que estão na conta na nuvem e não estão mais no aparelho — a mídia do WhatsApp que foi apagada do celular"
          : "É preciso ter uma extração de nuvem e um aparelho no inquérito.",
        onClick: function () {
          window.location.href = "correlacao_nuvem.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_ACHADOS + " Buscar entidades no material",
        disabled: !temMaterial,
        title: temMaterial
          ? "Onde nomes, CPFs/CNPJs, telefones e hashes são citados em tudo que foi importado: "
            + "RIF, extrato, inquérito em PDF, IRPF, IPEI, telefonia, documentos, celular e nuvem"
          : "Nada importado neste inquérito ainda.",
        onClick: function () {
          window.location.href = "busca_entidades.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_GRAFICO + " Linha do tempo financeira",
        disabled: !temFinanceiro,
        title: temFinanceiro
          ? "SIMBA + RIF no mesmo eixo temporal: quando a movimentação apareceu, parou ou mudou de patamar"
          : "Nenhum RIF nem extrato SIMBA neste inquérito.",
        onClick: function () {
          window.location.href = "timeline_financeira.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_ANTENA + " Encontros por antena",
        disabled: !Number(inq.qt_telefonia),
        title: Number(inq.qt_telefonia)
          ? "Quem esteve na mesma antena dentro da janela — co-localização de linhas do afastamento"
          : "Nenhuma telefonia neste inquérito.",
        onClick: function () {
          window.location.href = "encontros_telefonia.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      "divisor",
      {
        html: ICONE_ACHADO_ANALISTA + " Hipóteses do inquérito",
        title: "Quadro do inquérito inteiro: hipóteses com responsável, evidências a favor/contra e pendências — o chat de IA usa o estado atual",
        onClick: function () {
          window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(inq.id_inquerito);
        }
      },
      {
        html: ICONE_CONTEXTO + " Contexto da investigação",
        title: "Descreva a investigação para orientar a IA (entra em toda conversa deste inquérito)",
        onClick: function () { editarContextoInquerito(inq); }
      },
      {
        html: ICONE_DOCUMENTO + " Documentos" + (Number(inq.qt_documentos) ? " (" + formatarNumero(inq.qt_documentos) + ")" : ""),
        title: "Documentos anexados via \"Extrair de PDF\" — o texto deles fica disponível para a IA da investigação",
        onClick: function () { abrirModalDocumentos(inq.id_inquerito, inq.nu_inquerito); }
      },
      { $el: $btnSemantica }
    ];
    $acoesInq.append(criarMenuAcoes(itensMenu, { rotulo: "Extras", icone: ICONE_EXTRAS }));

    if (podeGerenciar(inq)) {
      $acoesInq.append(criarMenuAcoes([
        {
          html: ICONE_EDITAR + " Editar inquérito",
          title: "Corrigir o número do inquérito no ePol",
          onClick: function () { renomearInquerito(inq); }
        },
        {
          html: ICONE_PERMISSOES + " Gerência de permissões",
          onClick: function () { abrirModalPermissoes(inq.id_inquerito, inq.nu_inquerito); }
        },
        {
          html: ICONE_EDITAR + " Delegacia do inquérito",
          title: "Definir ou alterar a delegacia associada a este inquérito",
          onClick: function () { abrirModalDelegacia(inq); }
        },
        {
          html: ICONE_ARQUIVAR + (inq.tp_situacao === "arquivado" ? " Reativar inquérito" : " Arquivar inquérito"),
          title: inq.tp_situacao === "arquivado"
            ? "O inquérito volta a constar como caso ativo nas investigações"
            : "Marca o caso como encerrado: sai do foco da lista (filtro \"Arquivados\"), mas nada é apagado",
          onClick: function () { alternarSituacaoInquerito(inq); }
        },
        "divisor",
        {
          html: ICONE_APAGAR + " Apagar inquérito",
          perigoso: true,
          disabled: !!inq.tem_extracao_em_andamento,
          title: inq.tem_extracao_em_andamento ? "Aguarde a extração terminar para apagar." : "",
          onClick: function () { apagarInquerito(inq.id_inquerito, inq.nu_inquerito); }
        }
      ], { rotulo: "Gerência do inquérito", icone: ICONE_PERMISSOES, alinharDireita: true }));
    }

    // Busca rápida do caso: um campo só — nome, CPF/CNPJ, telefone ou hash — que abre a busca
    // de entidades já neste inquérito com o termo digitado. A classificação por tipo é feita lá
    // (ver classificarTermo em js/busca_entidades.js); aqui o termo via inteiro no ?q=, que
    // também dispara a busca sozinho ao chegar.
    var $buscaWrap = $("<div>").addClass("inv-busca-entidades").appendTo($acoesInq);
    var $buscaCaixa = $('<div class="inv-busca">').appendTo($buscaWrap);
    $('<svg class="inv-busca-icone" aria-hidden="true" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>').appendTo($buscaCaixa);
    var $buscaInput = $('<input type="search" autocomplete="off" spellcheck="false">')
      .attr("placeholder", "Buscar nome, CPF/CNPJ, telefone ou hash no material deste inquérito…")
      .attr("aria-label", "Buscar entidade, documento, telefone ou hash no material deste inquérito")
      .appendTo($buscaCaixa);
    var $buscaBtn = $('<button type="button" class="btn btn-primary">')
      .html(ICONE_ACHADOS + " Buscar")
      .attr("title", "Onde isto aparece no inquérito? Abre a busca de entidades com o termo")
      .appendTo($buscaWrap);
    function irParaBuscaEntidades() {
      var termo = $.trim($buscaInput.val());
      if (!termo) { $buscaInput.trigger("focus"); return; }
      window.location.href = "busca_entidades.html?inquerito=" + encodeURIComponent(inq.id_inquerito) +
        "&q=" + encodeURIComponent(termo);
    }
    $buscaBtn.on("click", irParaBuscaEntidades);
    $buscaInput.on("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        irParaBuscaEntidades();
      }
    });

    // Abas para filtrar o conteúdo do inquérito
    var $abasInq = $("<div>").addClass("extracao-tabs inquerito-abas").attr("role", "tablist").attr("aria-label", "Categoria de conteúdo").appendTo($card);
    function criarAbaInq(id, label, icone, ativa, qt) {
      var $btn = $("<button>")
        .attr("type", "button")
        .addClass("extracao-tab" + (ativa ? " active" : ""))
        .attr("data-aba-inq", id)
        .attr("role", "tab")
        .attr("aria-selected", ativa ? "true" : "false")
        .html(icone + " " + label + (qt ? ' <span class="tab-contador">(' + formatarNumero(qt) + ")</span>" : ""))
        .appendTo($abasInq);
      return $btn;
    }
    var qtCelulares = extracoes.length + (Number(inq.qt_iped) || 0);
    var temCelulares = qtCelulares > 0;
    var temRifs = Number(inq.qt_rifs) > 0;
    var temSimba = Number(inq.qt_simba) > 0;
    var temDocumentos = Number(inq.qt_documentos) > 0;
    var temIpls = Number(inq.qt_ipls) > 0;
    var temIrpfs = Number(inq.qt_irpfs) > 0;
    var temFiscal = Number(inq.qt_ipeis) > 0;
    var temTelefonia = Number(inq.qt_telefonia) > 0;
    var abas = [
      { id: "celulares", label: "Celulares", icone: ICONE_DISPOSITIVO, tem: temCelulares, qt: qtCelulares },
      { id: "rifs", label: "RIFs", icone: ICONE_RIF, tem: temRifs, qt: Number(inq.qt_rifs) || 0 },
      { id: "simba", label: "SIMBA", icone: ICONE_SIMBA, tem: temSimba, qt: Number(inq.qt_simba) || 0 },
      { id: "ipl", label: "IPL", icone: ICONE_DOCUMENTO, tem: temIpls, qt: Number(inq.qt_ipls) || 0 },
      { id: "irpf", label: "IRPF", icone: ICONE_IRPF, tem: temIrpfs, qt: Number(inq.qt_irpfs) || 0 },
      { id: "fiscal", label: "Fiscal", icone: ICONE_IPEI, tem: temFiscal, qt: Number(inq.qt_ipeis) || 0 },
      { id: "telefonia", label: "Telefonia", icone: ICONE_TELEFONIA, tem: temTelefonia, qt: Number(inq.qt_telefonia) || 0 },
      { id: "documentos", label: "Outros documentos", icone: ICONE_DOCUMENTO, tem: temDocumentos, qt: Number(inq.qt_documentos) || 0 }
    ];

    function getTabStorageKey(idInquerito) {
      return "investigacao:aba:" + idInquerito;
    }

    function salvarAbaAtiva(idInquerito, cat) {
      try {
        sessionStorage.setItem(getTabStorageKey(idInquerito), cat);
      } catch (e) {}
    }

    function getAbaSalva(idInquerito) {
      try {
        return sessionStorage.getItem(getTabStorageKey(idInquerito));
      } catch (e) {
        return null;
      }
    }

    var abaUrl = getDeepLinkParams().aba;
    if (abaUrl && abas.every(function (a) { return a.id !== abaUrl; })) abaUrl = null;
    var abaSalva = getAbaSalva(inq.id_inquerito);
    var primeiraAtiva = (abaUrl && abas.find(function (a) { return a.tem && a.id === abaUrl; })) || abas.find(function (a) { return a.tem && a.id === abaSalva; }) || abas.find(function (a) { return a.tem; });
    if (abaUrl && primeiraAtiva && primeiraAtiva.id === abaUrl) {
      try { salvarAbaAtiva(inq.id_inquerito, abaUrl); } catch (e) {}
    }

    abas.forEach(function (a) {
      if (a.tem) criarAbaInq(a.id, a.label, a.icone, primeiraAtiva && primeiraAtiva.id === a.id, a.qt);
    });

    // Wrappers para cada categoria (para mostrar/esconder via abas)
    var $wrapCelulares = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "celulares", role: "tabpanel" }).toggle(!primeiraAtiva || primeiraAtiva.id === "celulares").attr("aria-hidden", !primeiraAtiva || primeiraAtiva.id === "celulares" ? "false" : "true").appendTo($card);
    var $wrapRifs = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "rifs", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "rifs").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "rifs" ? "false" : "true").appendTo($card);
    var $wrapSimba = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "simba", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "simba").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "simba" ? "false" : "true").appendTo($card);
    var $wrapDocumentos = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "documentos", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "documentos").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "documentos" ? "false" : "true").appendTo($card);
    var $wrapTelefonia = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "telefonia", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "telefonia").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "telefonia" ? "false" : "true").appendTo($card);
    var $wrapIpls = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "ipl", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "ipl").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "ipl" ? "false" : "true").appendTo($card);
    var $wrapIrpfs = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "irpf", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "irpf").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "irpf" ? "false" : "true").appendTo($card);
    var $wrapIpeis = $("<div>").addClass("inquerito-categoria-wrap").attr({ "data-categoria": "fiscal", role: "tabpanel" }).toggle(primeiraAtiva && primeiraAtiva.id === "fiscal").attr("aria-hidden", primeiraAtiva && primeiraAtiva.id === "fiscal" ? "false" : "true").appendTo($card);

    function mostrarCategoria(cat) {
      $abasInq.find(".extracao-tab").removeClass("active").attr("aria-selected", "false").attr("tabindex", "-1");
      var $ativo = $abasInq.find('[data-aba-inq="' + cat + '"]');
      $ativo.addClass("active").attr("aria-selected", "true").attr("tabindex", "0");
      $card.find(".inquerito-categoria-wrap").hide().attr("aria-hidden", "true");
      $card.find('.inquerito-categoria-wrap[data-categoria="' + cat + '"]').show().attr("aria-hidden", "false");
      salvarAbaAtiva(inq.id_inquerito, cat);
    }

    $abasInq.on("click", ".extracao-tab", function () {
      mostrarCategoria($(this).data("aba-inq"));
      $(this).focus();
    });

    $abasInq.on("keydown", ".extracao-tab", function (e) {
      var $tabs = $abasInq.find(".extracao-tab");
      var idx = $tabs.index(this);
      if (e.key === "ArrowRight") {
        e.preventDefault();
        var $next = $tabs.eq((idx + 1) % $tabs.length);
        mostrarCategoria($next.data("aba-inq"));
        $next.focus();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        var $prev = $tabs.eq((idx - 1 + $tabs.length) % $tabs.length);
        mostrarCategoria($prev.data("aba-inq"));
        $prev.focus();
      } else if (e.key === "Home") {
        e.preventDefault();
        var $first = $tabs.first();
        mostrarCategoria($first.data("aba-inq"));
        $first.focus();
      } else if (e.key === "End") {
        e.preventDefault();
        var $last = $tabs.last();
        mostrarCategoria($last.data("aba-inq"));
        $last.focus();
      }
    });
    // tabindex inicial
    $abasInq.find(".extracao-tab").attr("tabindex", "-1");
    if (primeiraAtiva) $abasInq.find('[data-aba-inq="' + primeiraAtiva.id + '"]').attr("tabindex", "0");

    // Uma seção por extração (cada aparelho), com seus próprios botões de dados.
    if (!extracoes.length) {
      $("<p>").addClass("empty-hint").css("margin", "10px 0 0").text("Nenhuma extração ainda.").appendTo($wrapCelulares);
    }
    extracoes.forEach(function (e) {
      $wrapCelulares.append(renderExtracaoBloco(inq, e));
    });

    // Bloco(s) de Nuvem IPED (um por pasta-caso importada) — entra na aba Celulares
    if (Number(inq.qt_iped)) {
      var $ipedWrap = $("<div>").addClass("blocos-async").appendTo($wrapCelulares);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/extracoes-iped").done(function (casos) {
        encurtar(casos).forEach(function (p) {
          $ipedWrap.append(renderIpedBloco(inq, p));
        });
      });
    }

    // Bloco(s) de inquérito em PDF — peças, pessoas e achados extraídos do IPL (um por PDF).
    if (Number(inq.qt_ipls)) {
      var $iplsWrap = $("<div>").addClass("blocos-async").appendTo($wrapIpls);
      var iplsData = [];
      var iplsExpandidos = {};
      var redesenharIpls = function () {
        desenharBlocosAgrupados($iplsWrap, iplsData, {
          agrupar: lerAgruparSessao("investigacao:agrupar:ipl"),
          expandidos: iplsExpandidos,
          chave: function (p) { return p.nu_ipl || "—"; },
          rotulo: function (chave) { return chave === "—" ? "Sem número de inquérito" : "Inquérito " + chave; },
          render: function (p) { return renderIplBloco(inq, p); }
        });
      };
      criarBarraAgrupar($wrapIpls, "investigacao:agrupar:ipl", redesenharIpls);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/pdfs").done(function (ipls) {
        iplsData = encurtar(ipls);
        redesenharIpls();
      });
    }

    // Bloco(s) de declaração de IRPF (Receita Federal) — identificação, bens, dependentes,
    // rendimentos, dívidas e doações extraídos (uma por PDF).
    if (Number(inq.qt_irpfs)) {
      var $irpfsWrap = $("<div>").addClass("blocos-async").appendTo($wrapIrpfs);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/declaracoes-irpf").done(function (irpfs) {
        encurtar(irpfs).forEach(function (r) {
          $irpfsWrap.append(renderIrpfBloco(inq, r));
        });
      });
    }

    // Bloco(s) de IPEI (quebra de sigilo fiscal da Receita Federal) — alvos, vínculos, consolidado
    // anual, contas, bens, notas fiscais e contatos. O PDF e a planilha de anexo que veio com ele
    // são a mesma importação, então dão um bloco só (ver api/funcoes/ipei_utils.py).
    if (Number(inq.qt_ipeis)) {
      var $ipeisWrap = $("<div>").addClass("blocos-async").appendTo($wrapIpeis);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/ipeis").done(function (ipeis) {
        encurtar(ipeis).forEach(function (p) {
          $ipeisWrap.append(renderIpeiBloco(inq, p));
        });
      });
    }

    // Bloco(s) de documentos anexados via "Extrair arquivo relacionado" — um por documento que
    // não virou bloco próprio (IPL reconhecido, PDF do RIF, síntese do SIMBA, declaração de IRPF)
    // para não duplicar.
    if (Number(inq.qt_documentos)) {
      var $docsWrap = $("<div>").addClass("blocos-async").appendTo($wrapDocumentos);
      var docsData = [];
      var docsExpandidos = {};
      var redesenharDocs = function () {
        desenharBlocosAgrupados($docsWrap, docsData, {
          agrupar: lerAgruparSessao("investigacao:agrupar:documentos"),
          expandidos: docsExpandidos,
          chave: function (d) { return categoriaDeDocumento(d); },
          rotulo: function (chave) { return chave; },
          render: function (d) { return renderDocumentoBloco(inq, d); }
        });
      };
      criarBarraAgrupar($wrapDocumentos, "investigacao:agrupar:documentos", redesenharDocs);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/documentos").done(function (docs) {
        docsData = encurtar(docs).filter(function (d) {
          return !(d.in_ipl || d.in_rif || d.in_simba || d.in_irpf || d.in_ipei || d.in_telefonia);
        });
        redesenharDocs();
      });
    }

    // Bloco(s) de RIF — movimentações bancárias importadas do COAF (um bloco por RIF).
    if (Number(inq.qt_rifs)) {
      var $rifsWrap = $("<div>").addClass("blocos-async").appendTo($wrapRifs);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/rifs").done(function (rifs) {
        encurtar(rifs).forEach(function (r) {
          $rifsWrap.append(renderRifBloco(inq, r));
        });
      });
    }

    // Bloco(s) de telefonia — UM POR OPERADORA, e não por arquivo: o cadastro, a planilha do
    // ofício, o PDF dela e as levas de ERB são o mesmo material partido em vários arquivos, e
    // todos caem no card da operadora (ver api/funcoes/telefonia_utils.py).
    if (Number(inq.qt_telefonia)) {
      var $telWrap = $("<div>").addClass("blocos-async").appendTo($wrapTelefonia);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/telefonia").done(function (cards) {
        encurtar(cards).forEach(function (t) {
          $telWrap.append(renderTelefoniaBloco(inq, t));
        });
      });
    }

    // Bloco(s) de extrato bancário do SIMBA (um por arquivo R304 importado).
    if (Number(inq.qt_simba)) {
      var $simbaWrap = $("<div>").addClass("blocos-async").appendTo($wrapSimba);
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/extratos-simba").done(function (extratos) {
        encurtar(extratos).forEach(function (s) {
          $simbaWrap.append(renderSimbaBloco(inq, s));
        });
      });
    }

    return $card;
  }

  /* ---------- cabeçalho e caixas de ação, comuns a todo bloco de extração/importação ---------- */

  // Não existe campo de sistema operacional na extração: o ícone do cabeçalho sai do texto que o
  // UFDR trouxe em <model>. Fabricante irreconhecível cai no ícone genérico de celular, e não em
  // "Android" — chutar a plataforma errada no cartão é pior do que não dizer nada.
  function sistemaDoAparelho(e) {
    var txt = ((e.no_fabricante || "") + " " + (e.no_dispositivo || "")).toLowerCase();
    if (/apple|iphone|ipad|ios/.test(txt)) return "ios";
    if (/android|samsung|motorola|xiaomi|redmi|huawei|honor|asus|realme|poco|infinix|tecno|oppo|vivo|nokia|lenovo|zte|lg(?![a-z])/.test(txt)) return "android";
    return "";
  }

  // Modelo do aparelho, que vira o título em destaque. Fabricante e modelo são campos separados,
  // mas o modelo quase sempre já carrega a marca ("Moto G35" para o fabricante "motorola") —
  // emendar os dois às cegas produz "MOTOROLA MOTO G35".
  function modeloDoAparelho(e) {
    var fab = (e.no_fabricante || "").trim();
    var mod = (e.no_dispositivo || "").trim();
    if (fab && mod) {
      var primeiraPalavra = mod.toLowerCase().split(" ")[0];
      var jaTemMarca = mod.toLowerCase().indexOf(fab.toLowerCase()) !== -1 ||
        fab.toLowerCase().indexOf(primeiraPalavra) === 0;
      if (!jaTemMarca) mod = fab + " " + mod;
    }
    return (mod || fab || "Aparelho não identificado").toUpperCase();
  }

  /* Monta o cabeçalho do bloco e devolve os dois contêineres que o chamador enche, porque badges e
   * ações mudam de um tipo de importação para outro:
   *  - $badges: estado do dado (status do processamento, arquivo presente/ausente). Fica na coluna
   *    da identidade, embaixo do nome do arquivo, porque descreve a extração — não é ação.
   *  - $acoes: uma fileira única alinhada à direita, só com o que se clica sempre (prompt de IA,
   *    achados). O secundário e o destrutivo vão para criarMenuAcoes, mesma convenção do cartão do
   *    inquérito. Antes eram quatro botões de tamanhos diferentes espalhados em três linhas.
   * A classe .extracao-cab segue no contêiner externo e .badge-status-extra/.em-andamento seguem
   * dentro dele, que é o que o polling procura (ver atualizarBadgesBlocosEmAndamento). */
  function montarCabecalho($bloco, opts) {
    var $cab = $("<div>").addClass("extracao-cab").appendTo($bloco);
    $("<div>")
      .addClass("extracao-avatar" + (opts.classeIcone ? " " + opts.classeIcone : ""))
      .html(opts.icone)
      .appendTo($cab);
    var $ident = $("<div>").addClass("extracao-identidade").appendTo($cab);
    $("<div>").addClass("extracao-modelo")
      .attr("title", opts.tituloAttr || "")
      .text(opts.titulo)
      .appendTo($ident);
    if (opts.subtitulo) {
      $("<div>").addClass("extracao-arquivo")
        .attr("title", opts.subtitulo)
        .text(opts.subtitulo)
        .appendTo($ident);
    }
    return {
      $badges: $("<div>").addClass("extracao-cab-badges").appendTo($ident),
      $acoes: $("<div>").addClass("extracao-cab-acoes").appendTo($cab)
    };
  }

  /* Uma caixa rotulada de botões de acesso. O celular usa duas — "Principais" e "Demais" —,
   * porque são uns vinte botões e, em fluxo único, o que se abre em toda análise (WhatsApp,
   * Localizações) fica com o mesmo peso visual do que se abre uma vez por caso (Calendário). Os
   * outros tipos de importação, com poucas ações, usam uma caixa só. */
  function criarGrupoAcoes($destino, titulo) {
    var $grupo = $("<div>").addClass("acoes-grupo").appendTo($destino);
    $("<div>").addClass("acoes-grupo-titulo").text(titulo).appendTo($grupo);
    return $("<div>").addClass("investigacao-actions").appendTo($grupo);
  }

  // Importação em andamento (ou que falhou) não tem botão nenhum: some com a caixa em vez de deixar
  // um retângulo rotulado e vazio no cartão.
  function podarVaziosBloco($bloco) {
    $bloco.find(".investigacao-actions").each(function () {
      if (!$(this).children().length) $(this).closest(".acoes-grupo").remove();
    });
    $bloco.find(".acoes-grupos, .extracao-cab-acoes, .extracao-cab-badges").each(function () {
      if (!$(this).children().length) $(this).remove();
    });
  }

  /* ---------- agrupar por categoria (abas IPL e Outros documentos) ----------
   * O estado do checkbox vive em sessionStorage (sobrevive à troca de aba e ao F5, mas não à
   * janela fechada) — ver o mesmo expediente da aba ativa em salvarAbaAtiva. */

  function lerAgruparSessao(chave) {
    try {
      return sessionStorage.getItem(chave) === "1";
    } catch (e) {
      return false;
    }
  }

  function salvarAgruparSessao(chave, ativo) {
    try {
      sessionStorage.setItem(chave, ativo ? "1" : "0");
    } catch (e) {}
  }

  // Barra com o checkbox, inserida no topo da aba (o wrapper é uma grade e a barra atravessa as
  // duas colunas — ver .agrupar-controle no style.css).
  function criarBarraAgrupar($wrap, chave, aoMudar) {
    var $chk = $("<input type='checkbox'>")
      .prop("checked", lerAgruparSessao(chave))
      .on("change", function () {
        salvarAgruparSessao(chave, this.checked);
        aoMudar();
      });
    var $barra = $("<label>").addClass("agrupar-controle")
      .append($chk)
      .append($("<span>").text(" Agrupar por categoria"));
    $wrap.prepend($barra);
  }

  // Redesenha a aba: achatada (padrão) ou com um grupo colapsável por categoria (ordenado
  // pela própria categoria). O contêiner é display:contents, então cada grupo cai como uma
  // linha da grade da aba e os blocos vão na grade interna do corpo do grupo. O que está
  // expandido fica em opts.expandidos para não recolher ao remarcar o checkbox.
  function desenharBlocosAgrupados($container, itens, opts) {
    $container.empty();
    if (!opts.agrupar || !itens.length) {
      itens.forEach(function (item) { $container.append(opts.render(item)); });
      return;
    }
    var grupos = [];
    var indice = {};
    itens.forEach(function (item) {
      var chave = String(opts.chave(item));
      if (!(chave in indice)) {
        indice[chave] = grupos.length;
        grupos.push({ chave: chave, itens: [] });
      }
      grupos[indice[chave]].itens.push(item);
    });
    grupos.sort(function (a, b) { return a.chave.localeCompare(b.chave, "pt-BR", { numeric: true }); });
    grupos.forEach(function (g) {
      var expandido = !!(opts.expandidos && opts.expandidos[g.chave]);
      var $grupo = $("<div>").addClass("agrupar-grupo");
      var $cab = $("<button type='button'>").addClass("agrupar-cabecalho")
        .attr({ "aria-expanded": expandido ? "true" : "false", title: "Expandir/recolher" });
      $("<span>").addClass("agrupar-seta").text(expandido ? "▾" : "▸").appendTo($cab);
      $("<span>").text(opts.rotulo(g.chave) + " (" + g.itens.length + ")").appendTo($cab);
      var $corpo = $("<div>").addClass("agrupar-corpo").toggle(expandido);
      g.itens.forEach(function (item) { $corpo.append(opts.render(item)); });
      $cab.on("click", function () {
        var aberto = $corpo.is(":visible");
        $corpo.toggle(!aberto);
        $cab.attr("aria-expanded", !aberto ? "true" : "false");
        $cab.find(".agrupar-seta").text(!aberto ? "▾" : "▸");
        if (opts.expandidos) opts.expandidos[g.chave] = !aberto;
      });
      $grupo.append($cab).append($corpo);
      $container.append($grupo);
    });
  }

  // Extensão do documento vira categoria amigável ("PDFs", "Planilhas"…) — implementação
  // única em window.AnalyserUtils.categoriaArquivo (também usada nos subgrupos de fontes da ia).
  function categoriaDeDocumento(d) {
    return window.AnalyserUtils.categoriaArquivo(d.tp_arquivo, d.no_arquivo);
  }

  function renderIplBloco(inq, p) {
    var $bloco = $("<div>").addClass("extracao-bloco");

    var titulo = "Inquérito " + (p.nu_ipl || p.no_arquivo);
    var cab = montarCabecalho($bloco, {
      icone: ICONE_INQUERITO,
      titulo: tituloComApelido(p, titulo),
      tituloAttr: p.no_arquivo,
      subtitulo: p.nu_ipl ? p.no_arquivo : ""
    });
    botaoPromptIa(
      inq,
      "Conversa de IA restrita só a este inquérito em PDF — não enxerga RIFs, extratos ou o conteúdo do aparelho",
      function () { abrirPromptEscopo(inq.id_inquerito, "ipl", p.id_ipl, titulo); }
    ).appendTo(cab.$acoes);
    $("<button>")
      .addClass("btn btn-achado")
      .attr("title", "Registrar informações já apuradas sobre este IPL, para a IA usar como contexto")
      .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
      .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "ipl", p.id_ipl, titulo); })
      .appendTo(cab.$acoes);
    // Menu sempre visível para qualquer um que veja o inquérito: Hash/Apelido/Informações são
    // conferência e identificação — não administração. Só o Apagar continua condicionado ao gestor.
    var hashesIpl = textoHashesArquivo(p.co_hash_md5, p.co_hash_sha1, p.co_hash_sha256);
    var menuIpl = [
      itemHash(titulo, hashesIpl),
      itemApelidoProva("ipl", p.id_ipl, p, "inquérito extraído"),
      itemCompartilharIndisponivel(),
      itemInfoProva(titulo, [
        ["Arquivo original", p.no_arquivo],
        ["Data da extração", p.dt_extracao ? formatarDataHora(p.dt_extracao) : ""],
        ["Extraído por", p.extraido_por],
        ["Número do IPL", p.nu_ipl],
        ["Conteúdo", formatarNumero(p.qt_pecas) + " peças · " + formatarNumero(p.qt_pessoas) + " pessoas · " +
         formatarNumero(p.qt_achados) + " achados · " + formatarNumero(p.qt_folhas) + " folhas"],
        ["Hash do arquivo", hashesIpl]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuIpl.push("divisor");
      menuIpl.push({
        html: ICONE_APAGAR + " Apagar inquérito extraído",
        perigoso: true,
        onClick: function () { apagarIpl(p.id_ipl, p.nu_ipl || p.no_arquivo, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuIpl, { compacto: true }));

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(p.qt_pecas) + " peças · " +
      formatarNumero(p.qt_pessoas) + " pessoas · " +
      formatarNumero(p.qt_achados) + " achados · " +
      formatarNumero(p.qt_folhas) + " folhas" +
      (p.extraido_por ? " · por " + p.extraido_por : "")
    ).appendTo($bloco);

    // linha de identificação do procedimento — o que o investigador confere antes de abrir
    var identificacao = [];
    if (p.no_autoridade) identificacao.push("Autoridade: " + p.no_autoridade);
    if (p.no_unidade) identificacao.push("Unidade: " + p.no_unidade);
    if (p.dt_instauracao) identificacao.push("Instaurado em " + formatarData(p.dt_instauracao));
    if (p.tx_tipificacao) identificacao.push("Tipificação: " + p.tx_tipificacao);
    if (p.nu_processo_judicial) identificacao.push("Processo: " + p.nu_processo_judicial);
    if (identificacao.length) {
      $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(identificacao.join(" · ")).appendTo($bloco);
    }

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&ipl=" + encodeURIComponent(p.id_ipl);
    function botaoIpl(icone, rotulo, aba, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = "inquerito_pdf.html" + qs + "&aba=" + aba;
        })
        .appendTo($acoes);
    }
    botaoIpl(ICONE_PECAS, "Peças", "pecas", "btn-pix");
    botaoIpl(ICONE_ENVOLVIDOS, "Pessoas", "pessoas", "btn-contatos");
    botaoIpl(ICONE_ACHADOS, "Achados", "achados", "btn-notificacoes");
    botaoAbrirDocumento(inq.id_inquerito, p.id_documento, $acoes);

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function apagarIpl(idIpl, rotulo, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar inquérito extraído",
      mensagem: "Apagar a extração de " + rotulo + " do inquérito " + numeroInquerito + "? As peças, pessoas e achados levantados do PDF, além do texto anexado para a IA, serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/inqueritos-pdf/" + idIpl, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar o inquérito extraído." });
        });
    });
  }

  function formatarMoeda(v) {
    if (v === null || v === undefined) return null;
    return "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function renderIrpfBloco(inq, r) {
    var $bloco = $("<div>").addClass("extracao-bloco");

    var titulo = "Declaração de IRPF " + (r.no_titular || r.no_arquivo);
    var cab = montarCabecalho($bloco, {
      icone: ICONE_IRPF,
      titulo: tituloComApelido(r, titulo),
      tituloAttr: r.no_arquivo,
      subtitulo: r.no_titular ? r.no_arquivo : ""
    });
    botaoPromptIa(
      inq,
      "Conversa de IA restrita só a esta declaração de IRPF — não enxerga outras declarações, RIFs, extratos ou o conteúdo do aparelho",
      function () { abrirPromptEscopo(inq.id_inquerito, "irpf", r.id_irpf, titulo); }
    ).appendTo(cab.$acoes);
    $("<button>")
      .addClass("btn btn-achado")
      .attr("title", "Registrar informações já apuradas sobre esta declaração, para a IA usar como contexto")
      .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
      .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "irpf", r.id_irpf, titulo); })
      .appendTo(cab.$acoes);
    // Menu sempre visível (ver comentário no bloco IPL): Hash/Apelido/Informações para todos,
    // Apagar só para quem gerencia.
    var hashesIrpf = textoHashesArquivo(r.co_hash_md5, r.co_hash_sha1, r.co_hash_sha256);
    var menuIrpf = [
      itemHash(titulo, hashesIrpf),
      itemApelidoProva("irpf", r.id_irpf, r, "declaração"),
      itemCompartilharIndisponivel(),
      itemInfoProva(titulo, [
        ["Arquivo original", r.no_arquivo],
        ["Data da extração", r.dt_extracao ? formatarDataHora(r.dt_extracao) : ""],
        ["Extraído por", r.extraido_por],
        ["Titular", r.no_titular],
        ["CPF", r.nu_cpf],
        ["Conteúdo", formatarNumero(r.qt_bens) + " bens · " + formatarNumero(r.qt_dependentes) + " dependentes · " +
         formatarNumero(r.qt_rendimentos) + " rendimentos · " + formatarNumero(r.qt_dividas) + " dívidas · " +
         formatarNumero(r.qt_doacoes) + " doações"],
        ["Hash do arquivo", hashesIrpf]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuIrpf.push("divisor");
      menuIrpf.push({
        html: ICONE_APAGAR + " Apagar declaração extraída",
        perigoso: true,
        onClick: function () { apagarIrpf(r.id_irpf, r.no_titular || r.no_arquivo, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuIrpf, { compacto: true }));

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(r.qt_bens) + " bens · " +
      formatarNumero(r.qt_dependentes) + " dependentes · " +
      formatarNumero(r.qt_rendimentos) + " rendimentos · " +
      formatarNumero(r.qt_dividas) + " dívidas · " +
      formatarNumero(r.qt_doacoes) + " doações" +
      (r.extraido_por ? " · por " + r.extraido_por : "")
    ).appendTo($bloco);

    // evolução patrimonial em destaque — o sinal mais forte pra apuração patrimonial
    var patrimonio = [];
    if (r.nu_cpf) patrimonio.push("CPF: " + r.nu_cpf);
    if (r.vl_rendimentos_tributaveis != null) patrimonio.push("Rendimentos tributáveis: " + formatarMoeda(r.vl_rendimentos_tributaveis));
    if (r.vl_bens_ano_atual != null) {
      var bensTxt = "Bens em 31/12: " + formatarMoeda(r.vl_bens_ano_atual);
      if (r.vl_bens_ano_anterior != null) bensTxt += " (era " + formatarMoeda(r.vl_bens_ano_anterior) + ")";
      patrimonio.push(bensTxt);
    }
    if (r.vl_dividas_ano_atual != null) {
      var dividasTxt = "Dívidas em 31/12: " + formatarMoeda(r.vl_dividas_ano_atual);
      if (r.vl_dividas_ano_anterior != null) dividasTxt += " (era " + formatarMoeda(r.vl_dividas_ano_anterior) + ")";
      patrimonio.push(dividasTxt);
    }
    if (patrimonio.length) {
      $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(patrimonio.join(" · ")).appendTo($bloco);
    }

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&irpf=" + encodeURIComponent(r.id_irpf);
    function botaoIrpf(icone, rotulo, aba, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = "declaracao_irpf.html" + qs + "&aba=" + aba;
        })
        .appendTo($acoes);
    }
    botaoIrpf(ICONE_BANCO, "Bens e Direitos", "bens", "btn-pix");
    botaoIrpf(ICONE_ENVOLVIDOS, "Dependentes", "dependentes", "btn-contatos");
    botaoIrpf(ICONE_NOTIFICACOES, "Rendimentos", "rendimentos", "btn-notificacoes");
    botaoIrpf(ICONE_ARQUIVOS, "Dívidas", "dividas", "btn-arquivos");
    botaoIrpf(ICONE_TIMELINE, "Doações", "doacoes", "btn-timeline");
    botaoAbrirDocumento(inq.id_inquerito, r.id_documento, $acoes);

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function apagarIrpf(idIrpf, rotulo, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar declaração extraída",
      mensagem: "Apagar a declaração de IRPF de " + rotulo + " do inquérito " + numeroInquerito + "? Os bens, dependentes, rendimentos, dívidas e doações levantados do PDF, além do texto anexado para a IA, serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/declaracoes-irpf/" + idIrpf, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a declaração extraída." });
        });
    });
  }

  function renderIpeiBloco(inq, r) {
    var $bloco = $("<div>").addClass("extracao-bloco");

    var alvos = r.no_alvos || r.no_arquivo;
    var titulo = "IPEI " + (r.nu_ipei || "") + (alvos ? " — " + alvos : "");
    var cab = montarCabecalho($bloco, {
      icone: ICONE_IPEI,
      titulo: tituloComApelido(r, r.nu_ipei ? "Sigilo fiscal · IPEI " + r.nu_ipei : r.no_arquivo),
      tituloAttr: titulo,
      subtitulo: alvos
    });
    botaoPromptIa(
      inq,
      "Conversa de IA restrita só a esta IPEI — não enxerga outras quebras de sigilo, RIFs, extratos ou o conteúdo do aparelho",
      function () { abrirPromptEscopo(inq.id_inquerito, "ipei", r.id_ipei, titulo); }
    ).appendTo(cab.$acoes);
    $("<button>")
      .addClass("btn btn-achado")
      .attr("title", "Registrar informações já apuradas sobre esta quebra de sigilo fiscal, para a IA usar como contexto")
      .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
      .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "ipei", r.id_ipei, titulo); })
      .appendTo(cab.$acoes);
    // Menu sempre visível (ver comentário no bloco IPL). A IPEI são DOIS arquivos da mesma
    // importação (PDF + planilha de anexo): o Hash mostra os dois, quando houver.
    var hashesIpei = (r.no_arquivo ? "PDF — " + r.no_arquivo + "\n" : "") +
      textoHashesArquivo(r.co_hash_md5, r.co_hash_sha1, r.co_hash_sha256);
    if (r.no_arquivo_anexo) {
      hashesIpei += "\n\nPlanilha de anexo — " + r.no_arquivo_anexo + "\n" +
        textoHashesArquivo(r.co_hash_md5_anexo, r.co_hash_sha1_anexo, r.co_hash_sha256_anexo);
    }
    var menuIpei = [
      itemHash(titulo, hashesIpei),
      itemApelidoProva("ipei", r.id_ipei, r, "IPEI"),
      itemCompartilharIndisponivel(),
      itemInfoProva(titulo, [
        ["Arquivo original", r.no_arquivo],
        ["Arquivo de anexo", r.no_arquivo_anexo],
        ["Data da extração", r.dt_extracao ? formatarDataHora(r.dt_extracao) : ""],
        ["Extraído por", r.extraido_por],
        ["Emitida em", r.dt_ipei ? formatarData(r.dt_ipei) : ""],
        ["Unidade", r.no_unidade],
        ["Processo judicial", r.nu_processo_judicial],
        ["Conteúdo", formatarNumero(r.qt_alvos) + " alvos · " + formatarNumero(r.qt_relacionados) + " vínculos · " +
         formatarNumero(r.qt_contas) + " linhas de conta · " + formatarNumero(r.qt_bens) + " bens · " +
         formatarNumero(r.qt_notas) + " notas fiscais · " + formatarNumero(r.qt_contatos) + " contatos"],
        ["Hash dos arquivos", hashesIpei]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuIpei.push("divisor");
      menuIpei.push({
        html: ICONE_APAGAR + " Apagar IPEI extraída",
        perigoso: true,
        onClick: function () { apagarIpei(r.id_ipei, r.nu_ipei || r.no_arquivo, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuIpei, { compacto: true }));

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(r.qt_alvos) + " alvos · " +
      formatarNumero(r.qt_relacionados) + " vínculos · " +
      formatarNumero(r.qt_contas) + " linhas de conta · " +
      formatarNumero(r.qt_bens) + " bens · " +
      formatarNumero(r.qt_notas) + " notas fiscais · " +
      formatarNumero(r.qt_contatos) + " contatos" +
      (r.extraido_por ? " · por " + r.extraido_por : "")
    ).appendTo($bloco);

    // a procedência do que está no bloco: o PDF assinado e a planilha que veio com ele. Os dois
    // arquivos são a MESMA importação (por isso um card só), e é isso que a linha diz.
    var origem = [];
    if (r.dt_ipei) origem.push("Emitida em " + formatarData(r.dt_ipei));
    if (r.no_unidade) origem.push(r.no_unidade);
    if (r.nu_processo_judicial) origem.push("Processo " + r.nu_processo_judicial);
    if (r.qt_folhas) origem.push(formatarNumero(r.qt_folhas) + " folhas");
    origem.push(r.no_arquivo_anexo ? "PDF + planilha de anexo" : "somente o PDF (sem anexo)");
    $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(origem.join(" · ")).appendTo($bloco);

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&ipei=" + encodeURIComponent(r.id_ipei);
    function botaoIpei(icone, rotulo, aba, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = "ipei.html" + qs + "&aba=" + aba;
        })
        .appendTo($acoes);
    }
    botaoIpei(ICONE_DONO, "Alvos", "alvos", "btn-contatos");
    botaoIpei(ICONE_ENVOLVIDOS, "Vínculos", "vinculos", "btn-notificacoes");
    botaoIpei(ICONE_TIMELINE, "Ano a ano", "anos", "btn-timeline");
    botaoIpei(ICONE_BANCO, "Contas", "contas", "btn-pix");
    botaoIpei(ICONE_ARQUIVOS, "Bens", "bens", "btn-arquivos");
    botaoIpei(ICONE_RIF, "Notas fiscais", "notas", "btn-notificacoes");
    botaoIpei(ICONE_EMAIL, "Contatos", "contatos", "btn-contatos");
    botaoAbrirDocumento(inq.id_inquerito, r.id_documento, $acoes);

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function apagarIpei(idIpei, rotulo, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar IPEI extraída",
      mensagem: "Apagar a quebra de sigilo fiscal " + rotulo + " do inquérito " + numeroInquerito + "? Os alvos, vínculos, contas, bens, notas fiscais e contatos levantados, além do texto do PDF e da planilha de anexo anexados para a IA, serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/ipeis/" + idIpei, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a IPEI extraída." });
        });
    });
  }

  function renderTelefoniaBloco(inq, t) {
    var $bloco = $("<div>").addClass("extracao-bloco");

    // as linhas que os ofícios pediram são o assunto do card; sem elas, o subtítulo diz o período
    var periodo = [t.dt_inicio_periodo, t.dt_fim_periodo].filter(Boolean);
    var subtitulo = t.nu_linhas_alvo ||
      (periodo.length === 2 ? formatarData(periodo[0]) + " a " + formatarData(periodo[1]) : "");
    var titulo = "Telefonia " + (t.no_operadora || "") + (subtitulo ? " — " + subtitulo : "");
    var cab = montarCabecalho($bloco, {
      icone: ICONE_TELEFONIA,
      titulo: tituloComApelido(t, "Telefonia · " + (t.no_operadora || "operadora não identificada")),
      tituloAttr: titulo,
      subtitulo: subtitulo
    });
    botaoPromptIa(
      inq,
      "Conversa de IA restrita só ao retorno desta operadora — não enxerga outras operadoras, RIFs, extratos ou o conteúdo do aparelho",
      function () { abrirPromptEscopo(inq.id_inquerito, "telefonia", t.id_telefonia, titulo); }
    ).appendTo(cab.$acoes);
    $("<button>")
      .addClass("btn btn-achado")
      .attr("title", "Registrar informações já apuradas sobre o retorno desta operadora, para a IA usar como contexto")
      .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
      .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "telefonia", t.id_telefonia, titulo); })
      .appendTo(cab.$acoes);
    // Menu sempre visível (ver comentário no bloco IPL). Telefonia não guarda hash: o card
    // agrega vários arquivos da operadora e nenhum deles tem digesto no banco — o item entra
    // desabilitado dizendo isso, em vez de sumir.
    var menuTelefonia = [
      {
        html: ICONE_HASH + " Hash",
        title: "Indisponível: os arquivos da operadora são agregados num card só e o sistema não "
          + "guarda o hash de nenhum deles.",
        disabled: true,
        onClick: function () {}
      },
      itemApelidoProva("telefonia", t.id_telefonia, t, "telefonia"),
      itemCompartilharIndisponivel(),
      itemInfoProva(titulo, [
        ["Operadora", t.no_operadora],
        ["Linhas investigadas", t.nu_linhas_alvo],
        ["Arquivos importados", formatarNumero(t.qt_arquivos) + " arquivo(s) da operadora"],
        ["Período coberto", periodo.length === 2 ? formatarData(periodo[0]) + " a " + formatarData(periodo[1]) : ""],
        ["Data da extração", t.dt_extracao ? formatarDataHora(t.dt_extracao) : ""],
        ["Última atualização", t.dt_atualizacao ? formatarDataHora(t.dt_atualizacao) : ""],
        ["Extraído por", t.extraido_por],
        ["Conteúdo", formatarNumero(t.qt_chamadas) + " chamadas · " + formatarNumero(t.qt_erbs) + " antenas · " +
         formatarNumero(t.qt_linhas) + " fichas cadastrais · " + formatarNumero(t.qt_numeros) + " números"]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuTelefonia.push("divisor");
      menuTelefonia.push({
        html: ICONE_APAGAR + " Apagar telefonia da operadora",
        perigoso: true,
        onClick: function () { apagarTelefonia(t.id_telefonia, t.no_operadora, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuTelefonia, { compacto: true }));

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(t.qt_chamadas) + " chamadas · " +
      formatarNumero(t.qt_erbs) + " antenas · " +
      formatarNumero(t.qt_linhas) + " fichas cadastrais · " +
      formatarNumero(t.qt_numeros) + " números" +
      (Number(t.qt_sessoes) ? " · " + formatarNumero(t.qt_sessoes) + " chamadas WiFi" : "") +
      (Number(t.qt_dados) ? " · " + formatarNumero(t.qt_dados) + " sessões de dados" : "") +
      (t.extraido_por ? " · por " + t.extraido_por : "")
    ).appendTo($bloco);

    // a procedência do card: quantos arquivos entraram nele e o que eles cobrem. Esta linha existe
    // porque o agrupamento por operadora ESCONDE os arquivos — sem ela, o investigador não teria
    // como saber que a planilha e o PDF do mesmo ofício já estão os dois aqui dentro.
    var origem = [formatarNumero(t.qt_arquivos) + " arquivo(s) da operadora"];
    if (periodo.length === 2) origem.push("período de " + formatarData(periodo[0]) + " a " + formatarData(periodo[1]));
    if (Number(t.qt_erbs_no_mapa)) origem.push(formatarNumero(t.qt_erbs_no_mapa) + " antenas com coordenada");
    $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(origem.join(" · ")).appendTo($bloco);

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&telefonia=" + encodeURIComponent(t.id_telefonia);
    function botaoTelefonia(icone, rotulo, aba, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = "telefonia.html" + qs + "&aba=" + aba;
        })
        .appendTo($acoes);
    }
    botaoTelefonia(ICONE_LIGACOES, "Chamadas", "chamadas", "btn-ligacoes");
    botaoTelefonia(ICONE_LOCALIZACOES, "Antenas", "erbs", "btn-localizacoes");
    botaoTelefonia(ICONE_CONTATOS, "Cadastro", "linhas", "btn-contatos");
    botaoTelefonia(ICONE_DISPOSITIVO, "Números e IMEI", "numeros", "btn-notificacoes");
    if (Number(t.qt_dados)) {
      botaoTelefonia(ICONE_NAVEGACAO, "Dados móveis", "dados", "btn-navegacao");
    }
    botaoTelefonia(ICONE_ARQUIVOS, "Arquivos importados", "arquivos", "btn-arquivos");
    // as antenas no mapa do inquérito, junto das localizações do aparelho — que é onde elas valem
    if (Number(t.qt_erbs_no_mapa)) {
      $("<button>")
        .addClass("btn btn-localizacoes")
        .html(ICONE_LOCALIZACOES + " ERBs no mapa")
        .attr("title", "Abre o mapa de localizações do inquérito com a camada de antenas ligada")
        .on("click", function () {
          window.location.href = "localizacoes.html?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&erbs=1&visao=grafo";
        })
        .appendTo($acoes);
    }

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function apagarTelefonia(idTelefonia, operadora, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar telefonia da operadora",
      mensagem: "Apagar TODO o retorno da operadora " + (operadora || "") + " do inquérito " +
        numeroInquerito + "? Como os arquivos dessa operadora foram reunidos num card só, isso " +
        "remove de uma vez as chamadas, antenas, fichas cadastrais e números de todos eles, além " +
        "do texto dos arquivos anexados para a IA. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/telefonia/" + idTelefonia, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a telefonia." });
        });
    });
  }

  function renderDocumentoBloco(inq, d) {
    var $bloco = $("<div>").addClass("extracao-bloco")
      .attr("data-id-documento", d.id_documento);

    var cab = montarCabecalho($bloco, {
      icone: ICONE_ARQUIVOS,
      titulo: tituloComApelido(d, d.no_arquivo),
      tituloAttr: d.no_arquivo
    });
    if (!d.tx_erro) {
      botaoPromptIa(
        inq,
        "Conversa de IA restrita só a este documento — não enxerga outros documentos, RIFs, extratos ou o conteúdo do aparelho",
        function () { abrirPromptEscopo(inq.id_inquerito, "documento", d.id_documento, "Documento " + d.no_arquivo); }
      ).appendTo(cab.$acoes);
      $("<button>")
        .addClass("btn btn-achado")
        .attr("title", "Registrar informações já apuradas sobre este documento, para a IA usar como contexto")
        .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
        .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "documento", d.id_documento, "Documento " + d.no_arquivo); })
        .appendTo(cab.$acoes);
    }
    // Menu sempre visível (ver comentário no bloco IPL). Hash do documento só existe para os
    // anexados depois do db/migracoes/0044_documento_hashes.sql — textoHashesArquivo diz isso.
    var hashesDoc = textoHashesArquivo(d.co_hash_md5, d.co_hash_sha1, d.co_hash_sha256);
    var menuDoc = [
      itemHash("Documento " + d.no_arquivo, hashesDoc),
      itemApelidoProva("documento", d.id_documento, d, "documento"),
      itemCompartilharIndisponivel(),
      itemInfoProva(d.no_arquivo, [
        ["Arquivo original", d.no_arquivo],
        ["Tipo", (d.tp_arquivo || "").toUpperCase()],
        ["Data da anexação", d.dt_processado ? formatarDataHora(d.dt_processado) : ""],
        ["Anexado por", d.anexado_por],
        ["Texto extraído", d.tx_erro ? "" : formatarNumero(d.qt_caracteres) + " caracteres"],
        ["Erro de extração", d.tx_erro],
        ["Hash do arquivo", hashesDoc]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuDoc.push("divisor");
      menuDoc.push({
        html: ICONE_APAGAR + " Remover documento",
        perigoso: true,
        onClick: function () { apagarDocumento(d.id_documento, d.no_arquivo); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuDoc, { compacto: true }));

    var meta = (d.tp_arquivo || "?").toUpperCase() + " · " +
      (d.tx_erro ? "erro de extração" : formatarNumero(d.qt_caracteres) + " caracteres") +
      " · " + formatarDataHora(d.dt_processado) +
      (d.anexado_por ? " · por " + d.anexado_por : "");
    $("<div>")
      .addClass("extracao-contagens")
      .attr("title", d.tx_erro || "")
      .text(meta)
      .appendTo($bloco);

    if (d.tx_erro) {
      $("<div>").addClass("extracao-contagens").css("opacity", ".85").text(
        "A extração do texto falhou: " + d.tx_erro + " — o documento está anexado, mas a IA não consegue ler o conteúdo."
      ).appendTo($bloco);
    } else {
      $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(
        "O texto extraído fica disponível para a IA nas conversas e buscas deste inquérito."
      ).appendTo($bloco);
    }

    // A caixa de ações fecha o bloco; as linhas de texto que renderAnaliseDocumento ainda escreve
    // entram acima dela (ver o helper linha() lá dentro).
    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    $("<button>")
      .addClass("btn btn-secondary")
      .attr("title", "Abre o documento na tela de leitura: páginas, busca e o arquivo original")
      .html(ICONE_PECAS + " Abrir documento")
      .on("click", function () { abrirDocumento(inq.id_inquerito, d); })
      .appendTo($acoes);
    if (!d.tx_erro) renderAnaliseDocumento(inq, d, $bloco, $acoes);

    podarVaziosBloco($bloco);
    return $bloco;
  }

  // Seção de análise de entidades do documento (resumo + pessoas + achados pela IA): badge de
  // andamento, resultado com botões de pessoas/achados, erro com retry, ou o botão inicial.
  function renderAnaliseDocumento(inq, d, $bloco, $acoes) {
    // As linhas de texto vão acima da caixa de ações, que já está montada no rodapé do bloco.
    function linha() { return $("<div>").insertBefore($bloco.find(".acoes-grupos")); }
    if (d.cd_analise === "processando" || d.cd_analise === "pendente") {
      linha().addClass("extracao-contagens em-andamento").css("opacity", ".8").html(
        ICONE_IA + " Analisando entidades do documento (resumo, pessoas e achados)…"
      );
      return;
    }
    if (d.cd_analise === "concluido") {
      var qtPessoas = (d.jq_pessoas || []).length;
      var qtAchados = (d.jq_achados || []).length;
      var $resumo = linha().addClass("extracao-contagens");
      $("<span>").html(ICONE_IA).appendTo($resumo);
      $("<span>").text(d.tx_resumo_ia || "Documento analisado pela IA.").appendTo($resumo);
      if (qtPessoas) {
        $("<button>").addClass("btn btn-secondary")
          .html(ICONE_ENVOLVIDOS + " Pessoas (" + qtPessoas + ")")
          .attr("title", "Pessoas citadas no documento")
          .on("click", function () { abrirEntidadesDocumento(inq.id_inquerito, d, "pessoas"); })
          .appendTo($acoes);
      }
      if (qtAchados) {
        $("<button>").addClass("btn btn-secondary")
          .html(ICONE_ACHADOS + " Achados (" + qtAchados + ")")
          .attr("title", "Dados de interesse encontrados no documento (telefones, valores, endereços…)")
          .on("click", function () { abrirEntidadesDocumento(inq.id_inquerito, d, "achados"); })
          .appendTo($acoes);
      }
      $("<button>").addClass("btn btn-secondary")
        .attr("title", "Refaz a análise com a IA")
        .html(ICONE_IA + " Reanalisar")
        .on("click", function () { analisarDocumento(inq.id_inquerito, d.id_documento); })
        .appendTo($acoes);
      return;
    }
    if (d.cd_analise === "erro") {
      linha().addClass("extracao-contagens").css("opacity", ".85").text(
        "A análise de entidades falhou: " + (d.tx_erro_analise || "erro desconhecido") + "."
      );
      $("<button>").addClass("btn btn-secondary")
        .html(ICONE_IA + " Tentar analisar novamente")
        .on("click", function () { analisarDocumento(inq.id_inquerito, d.id_documento); })
        .appendTo($acoes);
      return;
    }
    $("<button>").addClass("btn btn-ia")
      .attr("title", "A IA lê o texto extraído e lista as pessoas citadas e os dados de interesse (resumo, CPF/CNPJ, telefones, valores, endereços…)")
      .html(ICONE_IA + " Analisar entidades com IA")
      .on("click", function () { analisarDocumento(inq.id_inquerito, d.id_documento); })
      .appendTo($acoes);
  }

  function analisarDocumento(idInquerito, idDocumento) {
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/documentos/" + idDocumento + "/analisar",
      method: "POST"
    })
      .done(function () { carregarInvestigacoes(); })
      .fail(function (xhr) {
        Dialogo.alerta({
          titulo: "Erro",
          mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a análise do documento."
        });
      });
  }

  function abrirEntidadesDocumento(idInquerito, d, tp) {
    $documentoEntidadesTitulo.empty().append("Entidades do documento — ", $("<span>").text(d.no_arquivo));
    var pessoas = d.jq_pessoas || [];
    var achados = d.jq_achados || [];
    var ehPessoas = tp === "pessoas";
    $documentoEntidadesResumo.text(d.tx_resumo_ia || "");
    $documentoEntidadesResumo.toggleClass("hidden", !d.tx_resumo_ia);
    $documentoEntidadesSubtitulo.text(ehPessoas ? "Pessoas citadas (" + pessoas.length + ")" : "Dados de interesse (" + achados.length + ")");
    $documentoEntidadesLista.empty();
    var itens = ehPessoas ? pessoas : achados;
    if (!itens.length) {
      $documentoEntidadesVazio
        .text(ehPessoas ? "Nenhuma pessoa citada foi identificada no documento." : "Nenhum dado de interesse foi identificado no documento.")
        .removeClass("hidden");
    } else {
      $documentoEntidadesVazio.addClass("hidden");
      itens.forEach(function (p) {
        $documentoEntidadesLista.append(renderEntidadeDocumento(p, ehPessoas));
      });
    }
    abrirModalAcessivel($modalDocumentoEntidades, $("#documento-entidades-fechar"));
  }

  function renderEntidadeDocumento(p, ehPessoa) {
    var $linha = $("<div>").addClass("permissao-linha");
    var $info = $("<div>").appendTo($linha);
    if (ehPessoa) {
      var partes = [p.no_pessoa || "?"];
      if (p.no_vulgo) partes.push("vulgo \"" + p.no_vulgo + "\"");
      if (p.nu_cpf_cnpj) partes.push(p.nu_cpf_cnpj);
      $("<div>").addClass("permissao-nome").text(partes.join(" · ")).appendTo($info);
      var detalhe = [];
      if (p.tp_papel) detalhe.push(p.tp_papel);
      if (p.no_variantes) detalhe.push("também como: " + p.no_variantes);
      detalhe.push(Number(p.qt_mencoes) > 1 ? Number(p.qt_mencoes) + " menções" : "1 menção");
      $("<div>").addClass("permissao-login").text(detalhe.join(" · ")).appendTo($info);
    } else {
      $("<div>").addClass("permissao-nome").text(p.tx_valor || "?").appendTo($info);
      var detalheA = [p.tp_achado || "achado"];
      if (p.vl_numerico != null) detalheA.push("R$ " + formatarNumero(p.vl_numerico));
      if (Number(p.qt_ocorrencias) > 1) detalheA.push(Number(p.qt_ocorrencias) + " ocorrências");
      $("<div>").addClass("permissao-login").text(detalheA.join(" · ")).appendTo($info);
    }
    if (p.tx_contexto) {
      $("<div>").addClass("permissao-login").css({ opacity: ".8", fontStyle: "italic" })
        .text("\"" + p.tx_contexto + "\"")
        .appendTo($info);
    }
    return $linha;
  }

  $("#documento-entidades-fechar").on("click", function () {
    fecharModalAcessivel($modalDocumentoEntidades);
  });

  $modalDocumentoEntidades.on("click", function (e) {
    if (e.target === this) {
      fecharModalAcessivel($modalDocumentoEntidades);
    }
  });

  /* Abre a tela do documento (web/documento.html). Antes daqui saía um modal com o texto num
   * <pre>: sem página, sem busca e sem o arquivo original — e era ele que a citação da IA abria,
   * o que deixava "está na página 7" sem ter para onde ir. */
  function abrirDocumento(idInquerito, d, pagina, trecho) {
    window.location.href = DocTrecho.url({
      inquerito: idInquerito, doc: d.id_documento, pagina: pagina, trecho: trecho
    });
  }

  /* Botão "Abrir documento" para os blocos que NÃO são o documento genérico — inquérito em PDF e
   * declaração de IRPF. Os três saem do mesmo PDF anexado (uma linha de INQ_TB04): o bloco de IPL
   * e o de IRPF mostram o que foi levantado do papel (peças, bens, rendimentos), mas até aqui não
   * havia como voltar ao papel em si. Só aparece quando a extração guardou o id_documento —
   * importação antiga pode ter ID_DOCUMENTO nulo, e um botão que abre "documento null" é pior do
   * que botão nenhum. */
  function botaoAbrirDocumento(idInquerito, idDocumento, $acoes) {
    if (!idDocumento) return;
    $("<button>")
      .addClass("btn btn-secondary")
      .attr("title", "Abre o PDF de origem na tela de leitura: páginas, busca e o arquivo original")
      .html(ICONE_PECAS + " Abrir documento")
      .on("click", function () {
        abrirDocumento(idInquerito, { id_documento: idDocumento });
      })
      .appendTo($acoes);
  }


  function renderRifBloco(inq, r) {
    var $bloco = $("<div>").addClass("extracao-bloco");

    var titulo = "RIF " + (r.nu_rif || "") + " — " + r.no_pasta;
    var cab = montarCabecalho($bloco, {
      icone: ICONE_RIF,
      titulo: tituloComApelido(r, r.nu_rif ? "RIF " + r.nu_rif : r.no_pasta),
      tituloAttr: titulo,
      subtitulo: r.nu_rif ? r.no_pasta : ""
    });
    botaoPromptIa(
      inq,
      "Conversa de IA restrita só a este RIF — não enxerga outros RIFs, extratos ou o conteúdo do aparelho",
      function () { abrirPromptEscopo(inq.id_inquerito, "rif", r.id_rif, "RIF " + (r.nu_rif || r.no_pasta)); }
    ).appendTo(cab.$acoes);
    $("<button>")
      .addClass("btn btn-achado")
      .attr("title", "Registrar informações já apuradas sobre este RIF, para a IA usar como contexto")
      .html(ICONE_ACHADO_ANALISTA + " Hipótese<br>da mídia")
      .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "rif", r.id_rif, "RIF " + (r.nu_rif || r.no_pasta)); })
      .appendTo(cab.$acoes);
    var hashesRif = textoHashesRif(r.hashes_arquivos);
    var menuRif = [
      itemHash("RIF " + (r.nu_rif || r.no_pasta), hashesRif),
      itemApelidoProva("rif", r.id_rif, r, "RIF"),
      itemCompartilharIndisponivel(),
      itemInfoProva(titulo, [
        ["Pasta importada", r.no_pasta],
        ["Número do RIF", r.nu_rif],
        ["Data da importação", r.dt_importacao ? formatarDataHora(r.dt_importacao) : ""],
        ["Importado por", r.importado_por],
        ["Titular", r.no_titular ? r.no_titular + (r.nu_cpf_cnpj_titular ? " · " + r.nu_cpf_cnpj_titular : "") : ""],
        ["Conteúdo", formatarNumero(r.qt_comunicacoes) + " comunicações · " +
         formatarNumero(r.qt_envolvidos) + " envolvidos · " + formatarNumero(r.qt_ocorrencias) + " ocorrências"],
        ["PDF anexado", r.id_documento_pdf ? "sim" : ""],
        ["Hash dos arquivos", hashesRif]
      ])
    ];
    if (podeGerenciar(inq)) {
      menuRif.push("divisor");
      menuRif.push({
        html: ICONE_APAGAR + " Apagar RIF",
        perigoso: true,
        onClick: function () { apagarRif(r.id_rif, r.nu_rif, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuRif, { compacto: true }));

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(r.qt_comunicacoes) + " comunicações · " +
      formatarNumero(r.qt_envolvidos) + " envolvidos · " +
      formatarNumero(r.qt_ocorrencias) + " ocorrências" +
      (r.id_documento_pdf ? " · PDF anexado" : "") +
      (r.importado_por ? " · por " + r.importado_por : "")
    ).appendTo($bloco);

    if (r.no_titular) {
      var titularTxt = "Titular: " + r.no_titular + (r.nu_cpf_cnpj_titular ? " · " + r.nu_cpf_cnpj_titular : "") +
        (r.qt_titulares_distintos > 1 ? " (+" + (r.qt_titulares_distintos - 1) + " outro" + (r.qt_titulares_distintos > 2 ? "s" : "") + ")" : "");
      $("<div>").addClass("extracao-contagens").css("opacity", ".8").text(titularTxt).appendTo($bloco);
    }

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&rif=" + encodeURIComponent(r.id_rif);
    function botaoUrl(icone, rotulo, url, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () { window.location.href = url; })
        .appendTo($acoes);
    }
    function botaoRif(icone, rotulo, aba, corClasse) {
      botaoUrl(icone, rotulo, "rif.html" + qs + "&aba=" + aba, corClasse);
    }
    botaoRif(ICONE_PIX, "Comunicações", "comunicacoes", "btn-pix");
    botaoRif(ICONE_ENVOLVIDOS, "Envolvidos", "envolvidos", "btn-contatos");
    botaoRif(ICONE_OCORRENCIAS, "Ocorrências", "ocorrencias", "btn-notificacoes");
    // o grafo tem tela própria desde que saiu das abas de rif.html
    botaoUrl(ICONE_MESA, "Grafo", "grafo_rif.html" + qs, "btn-pix");

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function apagarRif(idRif, nuRif, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar RIF",
      mensagem: "Apagar o RIF " + (nuRif || "") + " do inquérito " + numeroInquerito + "? Todas as comunicações, envolvidos e ocorrências dele, além do PDF anexado, serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/rifs/" + idRif, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar o RIF." });
        });
    });
  }

  function formatarReais(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return "R$ " + n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function renderSimbaBloco(inq, s) {
    var $bloco = $("<div>").addClass("extracao-bloco")
      .attr("data-id-simba", s.id_extrato);
    // 'concluido' é o único estado em que os números do extrato valem alguma coisa: enquanto a
    // thread carrega, os totais por conta já estão gravados mas os lançamentos ainda não terminaram
    // de entrar, então o bloco mostra progresso em vez de contagens meia-boca.
    var emAndamento = s.cd_status === "pendente" || s.cd_status === "processando";
    var concluido = !s.cd_status || s.cd_status === "concluido";

    var cab = montarCabecalho($bloco, {
      icone: ICONE_SIMBA,
      titulo: tituloComApelido(s, "Extrato bancário"),
      subtitulo: s.no_arquivo
    });
    if (!concluido) {
      badgeStatus(s.cd_status, s.tx_erro, s.cd_fase, s.qt_progresso_fase)
        .addClass("badge-status-extra")
        .toggleClass("em-andamento", emAndamento)
        .appendTo(cab.$badges);
    }
    if (concluido) {
      botaoPromptIa(
        inq,
        "Conversa de IA restrita só a este extrato — não enxerga outros extratos, RIFs ou o conteúdo do aparelho",
        function () { abrirPromptEscopo(inq.id_inquerito, "simba", s.id_extrato, "Extrato " + s.no_arquivo); }
      ).appendTo(cab.$acoes);
      $("<button>")
        .addClass("btn btn-achado")
        .attr("title", "Registrar informações já apuradas sobre este extrato, para a IA usar como contexto")
        .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
        .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "simba", s.id_extrato, "Extrato " + s.no_arquivo); })
        .appendTo(cab.$acoes);
    }
    var hashesSimba = textoHashesArquivo(s.co_hash_md5, s.co_hash_sha1, s.co_hash_sha256);
    var menuSimba = [
      itemHash(s.no_arquivo, hashesSimba),
      itemApelidoProva("simba", s.id_extrato, s, "extrato"),
      itemCompartilharIndisponivel(),
      itemInfoProva("Extrato " + s.no_arquivo, [
        ["Arquivo original", s.no_arquivo],
        ["Data da importação", s.dt_importacao ? formatarDataHora(s.dt_importacao) : ""],
        ["Importado por", s.importado_por],
        ["Titular principal", s.no_titular ? s.no_titular + (s.nu_cpf_cnpj_titular ? " · " + s.nu_cpf_cnpj_titular : "") : ""],
        ["Conteúdo", formatarNumero(s.qt_contas) + " contas · " + formatarNumero(s.qt_titulares) + " titulares · " +
         formatarNumero(s.qt_lancamentos) + " lançamentos · " + formatarNumero(s.qt_contrapartes) + " contrapartes"],
        ["Hash do arquivo", hashesSimba]
      ])
    ];
    if (emAndamento) {
      menuSimba.push("divisor");
      menuSimba.push({
        html: ICONE_PARAR + " Cancelar importação",
        perigoso: true,
        onClick: function () { cancelarSimba(s.id_extrato, s.no_arquivo); }
      });
    } else if (podeGerenciar(inq)) {
      menuSimba.push("divisor");
      menuSimba.push({
        html: ICONE_APAGAR + " Apagar extrato",
        perigoso: true,
        onClick: function () { apagarSimba(s.id_extrato, s.no_arquivo, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuSimba, { compacto: true }));

    if (emAndamento) {
      $("<div>").addClass("extracao-contagens").text(
        "Importando o arquivo em segundo plano — pode fechar esta página, o andamento continua aqui."
      ).appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }

    if (s.cd_status === "erro") {
      $("<div>").addClass("extracao-contagens").css("opacity", ".85")
        .text("A importação falhou: " + (s.tx_erro || "erro não informado") +
              " · nada foi gravado; apague este registro e tente de novo.")
        .appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }
    if (s.cd_status === "cancelado") {
      $("<div>").addClass("extracao-contagens").css("opacity", ".85")
        .text("Importação cancelada — os dados parciais foram descartados. Apague este registro e importe de novo quando quiser.")
        .appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }

    $("<div>").addClass("extracao-contagens").text(
      formatarNumero(s.qt_contas) + " contas · " +
      formatarNumero(s.qt_titulares) + " titulares · " +
      formatarNumero(s.qt_lancamentos) + " lançamentos · " +
      formatarNumero(s.qt_contrapartes) + " contrapartes" +
      (s.importado_por ? " · por " + s.importado_por : "")
    ).appendTo($bloco);

    if (s.no_titular) {
      var titularSimbaTxt = "Titular principal: " + s.no_titular + (s.nu_cpf_cnpj_titular ? " · " + s.nu_cpf_cnpj_titular : "") +
        (s.qt_titulares > 1 ? " (de " + formatarNumero(s.qt_titulares) + " titulares no extrato)" : "");
      $("<div>").addClass("extracao-contagens").css("opacity", ".8").text(titularSimbaTxt).appendTo($bloco);
    }

    $("<div>").addClass("extracao-contagens").css("opacity", ".75").text(
      "Crédito " + formatarReais(s.vl_credito) + " · Débito " + formatarReais(s.vl_debito) +
      (s.dt_primeiro_lancamento ? " · " + formatarData(s.dt_primeiro_lancamento) +
        " a " + formatarData(s.dt_ultimo_lancamento) : "")
    ).appendTo($bloco);

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&simba=" + encodeURIComponent(s.id_extrato);
    function botaoSimba(icone, rotulo, aba, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () { window.location.href = "simba.html" + qs + "&aba=" + aba; })
        .appendTo($acoes);
    }
    botaoSimba(ICONE_BANCO, "Contas", "contas", "btn-contatos");
    botaoSimba(ICONE_PECAS, "Lançamentos", "lancamentos", "btn-pix");
    botaoSimba(ICONE_ENVOLVIDOS, "Contrapartes", "contrapartes", "btn-notificacoes");
    botaoSimba(ICONE_ALERTA, "Alertas", "alertas", "btn-danger");
    botaoSimba(ICONE_MESA, "Grafo", "grafo", "btn-pix");

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function cancelarSimba(idExtrato, noArquivo) {
    Dialogo.confirmar({
      titulo: "Cancelar importação",
      mensagem: "Parar a importação de " + noArquivo + "? O que já entrou é descartado — extrato pela metade traria totais maiores que os lançamentos de fato carregados. Você pode importar de novo depois.",
      rotuloOk: "Cancelar importação", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/simba/" + idExtrato + "/cancelar", method: "POST" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao cancelar a importação." });
        });
    });
  }

  function apagarSimba(idExtrato, noArquivo, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar extrato bancário",
      mensagem: "Apagar o extrato " + noArquivo + " do inquérito " + numeroInquerito + "? As contas, os lançamentos, as contrapartes consolidadas e a síntese anexada para a IA serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/simba/" + idExtrato, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar o extrato." });
        });
    });
  }

  function renderIpedBloco(inq, p) {
    var $bloco = $("<div>").addClass("extracao-bloco")
      .attr("data-id-iped", p.id_extracao_iped);
    var emAndamento = p.cd_status === "pendente" || p.cd_status === "processando";
    var concluido = !p.cd_status || p.cd_status === "concluido";

    // a fonte é deduzida no backend (tp_fonte): caso com conversa/senha/rede/autofill veio de um
    // celular (UFDR decodificado ou banco de app), caso sem isso é quebra de sigilo de nuvem.
    var rotuloTipo = p.tp_fonte === "celular" ? "Celular IPED" : "Nuvem IPED";
    var titulo = rotuloTipo + " — " + (p.no_conta || p.no_pasta || "caso sem conta identificada");
    var cab = montarCabecalho($bloco, {
      icone: ICONE_IPED,
      titulo: tituloComApelido(p, rotuloTipo),
      tituloAttr: p.no_pasta,
      subtitulo: p.no_conta || p.no_pasta || "caso sem conta identificada"
    });
    if (!concluido) {
      badgeStatus(p.cd_status, p.tx_erro, p.cd_fase, p.qt_progresso_fase)
        .addClass("badge-status-extra")
        .toggleClass("em-andamento", emAndamento)
        .appendTo(cab.$badges);
    }
    // Os binários do IPED não ficam no banco: são lidos sob demanda do storage do caso. Se a
    // pasta sair de data/, prévias, downloads e mídias de conversa param de abrir — este badge
    // avisa antes de o investigador topar com a falha dentro de uma tela.
    if (!emAndamento && p.cd_status !== "erro" && p.in_arquivo_presente === false) {
      $("<span>")
        .addClass("badge badge-aviso")
        .attr("title", "A pasta-caso não está mais em data/ (movida ou apagada). Os dados importados "
          + "continuam no sistema, mas prévias, downloads e mídias de conversa não abrem até a pasta voltar.")
        .html(ICONE_ARQUIVO_AUSENTE + " Caso ausente")
        .appendTo(cab.$badges);
    }
    var $ipedCompartilhada = badgeProvaCompartilhada(p);
    if ($ipedCompartilhada) $ipedCompartilhada.appendTo(cab.$badges);
    // Menu sempre visível (ver comentário no bloco IPL). O hash vem da extração-ponte (INQ_TB02)
    // criada na importação — é dela que os arquivos foram copiados. Compartilhar prova é real
    // aqui: a mesma conta na nuvem costuma ser prova em mais de um inquérito, e reprocessar a
    // pasta-caso do IPED custa o mesmo que reimportar um .ufdr (docs/compartilhar_prova.md).
    var hashesIped = textoHashesArquivo(p.co_hash_md5, p.co_hash_sha1, p.co_hash_sha256);
    var menuIped = [
      itemHash(titulo, hashesIped),
      itemApelidoProva("iped", p.id_extracao_iped, p, "caso IPED")
    ];
    if (!emAndamento && p.id_extracao && podeGerenciar(inq)) {
      menuIped.push({
        html: ICONE_COMPARTILHAR + " Compartilhar prova",
        title: "Copiar esta importação para outro inquérito, sem reprocessar a pasta do caso.",
        onClick: function () { compartilharProva(p.id_extracao, titulo); }
      });
    } else {
      menuIped.push(itemCompartilharIndisponivel());
    }
    menuIped.push(itemInfoProva(titulo, [
      ["Pasta do caso", p.no_pasta],
      ["Conta identificada", p.no_conta],
      ["Data da importação", p.dt_importacao ? formatarDataHora(p.dt_importacao) : ""],
      ["Importado por", p.importado_por],
      ["Versão do IPED", p.ds_versao_iped ? "IPED " + p.ds_versao_iped : ""],
      ["Itens no índice", p.qt_itens_total ? formatarNumero(p.qt_itens_total) : ""],
      ["Hash dos arquivos", hashesIped]
    ]));
    if (podeGerenciar(inq)) {
      menuIped.push("divisor");
      menuIped.push(emAndamento ? {
        html: ICONE_PARAR + " Cancelar importação",
        perigoso: true,
        onClick: function () { cancelarIped(p.id_extracao_iped, p.no_conta); }
      } : {
        html: ICONE_APAGAR + " Apagar importação",
        perigoso: true,
        onClick: function () { apagarIped(p.id_extracao_iped, p.no_conta, inq.nu_inquerito); }
      });
    }
    cab.$acoes.append(criarMenuAcoes(menuIped, { compacto: true }));

    if (emAndamento) {
      $("<div>").addClass("extracao-contagens").text(
        (p.cd_fase === "copiando"
          ? "Copiando a prova do inquérito " + (p.nu_inquerito_origem || "de origem")
          : "Lendo o caso em segundo plano" +
            (p.qt_itens_total ? " (" + formatarNumero(p.qt_itens_total) + " itens no índice)" : "")) +
        " — pode fechar esta página, o andamento continua aqui."
      ).appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }

    if (p.cd_status === "erro") {
      $("<div>").addClass("extracao-contagens").css("opacity", ".85")
        .text("A importação falhou: " + (p.tx_erro || "erro não informado") +
              " · apague este registro e tente de novo.")
        .appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }
    if (p.cd_status === "cancelado") {
      // cancelamento preserva o que já foi lido (o backend grava o parcial) — o card continua
      // com as contagens e os botões de navegação, com um aviso em cima
      $("<div>").addClass("extracao-contagens").css("opacity", ".85")
        .text("Importação cancelada — os dados lidos até a parada foram preservados.")
        .appendTo($bloco);
    }

    // contagens fixas + as dos produtos do Google que o caso trouxe (só aparecem quando > 0,
    // porque cada pasta-caso tem um conjunto diferente de produtos exportados)
    var partes = [
      formatarNumero(p.qt_arquivos) + " arquivos",
      formatarNumero(p.qt_emails) + " e-mails",
      formatarNumero(p.qt_contatos) + " contatos"
    ];
    [
      ["qt_navegacao", "páginas visitadas"], ["qt_localizacoes", "localizações"],
      ["qt_notas", "notas"], ["qt_ligacoes", "ligações"], ["qt_apps", "aplicativos"],
      ["qt_acessos", "acessos"], ["qt_acessos_agregados", "serviços acessados"],
      ["qt_conexoes", "conexões"], ["qt_atividades", "atividades"],
      ["qt_eventos", "eventos de agenda"], ["qt_dispositivos", "dispositivos"],
      ["qt_perfil", "perfis de conta"],
      // sidecar do Drive/Google Fotos: é o que habilita a tela "Nuvem × aparelho", então vale
      // aparecer no card — caso importado antes desse recurso mostra o chip zerado (ausente)
      ["qt_metadados_nuvem", "arquivos com metadado de nuvem"]
    ].forEach(function (par) {
      if (Number(p[par[0]])) partes.push(formatarNumero(p[par[0]]) + " " + par[1]);
    });
    // domínios decodificados (só um caso de celular preenche — nuvem e HD ficam NULL): o card
    // do IPED foi desenhado em cima de um caso de nuvem e nem os listava, então as conversas do
    // WhatsApp importadas de um celular não apareciam em lugar nenhum.
    [
      ["qt_conversas", "conversas"], ["qt_mensagens", "mensagens"],
      ["qt_contas_usuario", "contas de usuário"], ["qt_contas_financeiras", "contas financeiras"],
      ["qt_senhas", "senhas"], ["qt_autofill", "autofill"],
      ["qt_redes", "redes Wi-Fi"], ["qt_marcadores", "marcadores"]
    ].forEach(function (par) {
      if (Number(p[par[0]])) partes.push(formatarNumero(p[par[0]]) + " " + par[1]);
    });
    if (p.qt_itens_total) partes.push(formatarNumero(p.qt_itens_total) + " itens no índice");
    if (p.ds_versao_iped) partes.push("IPED " + p.ds_versao_iped);
    if (p.importado_por) partes.push("por " + p.importado_por);
    $("<div>").addClass("extracao-contagens").text(partes.join(" · ")).appendTo($bloco);

    var $acoes = criarGrupoAcoes($("<div>").addClass("acoes-grupos").appendTo($bloco), "Ações");
    if (p.id_extracao) {
      var qsExtracao = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(p.id_extracao);
      function botaoPaginaIped(icone, rotulo, pagina, corClasse) {
        $("<button>")
          .addClass("btn " + corClasse)
          .html(icone + " " + rotulo)
          .on("click", function () { window.location.href = pagina + qsExtracao; })
          .appendTo($acoes);
      }
      botaoPaginaIped(ICONE_ARQUIVOS, "Arquivos", "arquivos.html", "btn-pix");
      botaoPaginaIped(ICONE_EMAIL, "E-mails", "emails.html", "btn-contatos");
      botaoPaginaIped(ICONE_CONTATOS, "Contatos", "contatos.html", "btn-notificacoes");
      // mídia do WhatsApp que veio no backup da nuvem (Drive wa_<numero>/ ou tars do iCloud) sem
      // o texto das mensagens: linha do tempo na tela "WhatsApp (parcial)". Os botões de app de
      // mensageiro abaixo não a cobrem: num caso de nuvem o IPED não preenche QT_CONVERSAS.
      if (p.tem_wa_parcial) botaoPaginaIped(ICONE_WHATSAPP, "WhatsApp (parcial)", "whatsapp_parcial.html", "btn-whatsapp");
      // os produtos do Google variam de caso pra caso: só mostra o botão do que veio
      [
        ["qt_navegacao", ICONE_NAVEGACAO, "Navegação", "navegacao.html", "btn-navegacao"],
        ["qt_localizacoes", ICONE_LOCALIZACOES, "Localizações", "localizacoes.html", "btn-localizacoes"],
        ["qt_notas", ICONE_NOTAS, "Notas", "notas.html", "btn-notas"],
        ["qt_ligacoes", ICONE_LIGACOES, "Ligações", "ligacoes.html", "btn-ligacoes"],
        ["qt_apps", ICONE_APLICATIVOS, "Aplicativos", "aplicativos.html", "btn-aplicativos"],
        ["qt_acessos", ICONE_LOCALIZACOES, "Acessos", "acessos.html", "btn-localizacoes"],
        ["qt_acessos_agregados", ICONE_LOCALIZACOES, "Acessos agregados", "acessos_agregados.html", "btn-localizacoes"],
        ["qt_conexoes", ICONE_ENVOLVIDOS, "Conexões", "conexoes.html", "btn-contatos"],
        ["qt_atividades", ICONE_NAVEGACAO, "Minha Atividade", "atividades.html", "btn-navegacao"],
        ["qt_eventos", ICONE_TIMELINE, "Agenda", "agenda.html", "btn-notificacoes"],
        ["qt_dispositivos", ICONE_DISPOSITIVO, "Dispositivos da conta", "dispositivos_conta.html", "btn-pix"],
        ["qt_perfil", ICONE_DONO, "Perfil da conta", "perfil_conta.html", "btn-notas"]
      ].forEach(function (item) {
        if (Number(p[item[0]])) botaoPaginaIped(item[1], item[2], item[3], item[4]);
      });
      // domínios decodificados de um caso de celular — as telas que a extração "Nuvem" não tinha
      if (Number(p.qt_redes)) botaoPaginaIped(ICONE_WIFI, "Wi-Fi", "wifi.html", "btn-arquivos");
      if (Number(p.qt_contas_financeiras)) botaoPaginaIped(ICONE_BANCO, "Contas bancárias", "contas_financeiras.html", "btn-pix");
      if (Number(p.qt_senhas)) botaoPaginaIped(ICONE_CHAVE, "Senhas", "credenciais.html", "btn-notificacoes");
      if (Number(p.qt_autofill)) botaoPaginaIped(ICONE_CHAVE, "Autofill", "credenciais.html", "btn-notificacoes");
      // sidecar do Drive/Google Fotos: habilita a tela "Nuvem × aparelho"
      if (Number(p.qt_metadados_nuvem)) botaoPaginaIped(ICONE_NUVEM, "Nuvem × aparelho", "correlacao_nuvem.html", "btn-navegacao");
      // triagem do perito dentro do IPED: abre a tela de arquivos já filtrada nos itens marcados
      if (Number(p.qt_marcadores)) {
        $("<button>").addClass("btn btn-contatos")
          .html(ICONE_MARCACOES + " Marcações do perito")
          .on("click", function () {
            window.location.href = "arquivos.html" + qsExtracao + "&marcados=true";
          })
          .appendTo($acoes);
      }
      // Conversas (WhatsApp, Instagram, …): o IPED grava tudo nas tabelas genéricas de mensageiro
      // (fonte=msg), então um botão por app, no mesmo padrão do card do UFDR. Chegam por AJAX
      // depois do render — a quantidade de apps varia de caso pra caso.
      if (Number(p.qt_conversas)) {
        $.getJSON(
          "/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(p.id_extracao)
        ).done(function (apps) {
          apps.forEach(function (a) {
            if (APPS_NAO_MENSAGEIROS.indexOf(String(a.tp_app || "").toLowerCase()) !== -1) return;
            var ehWhatsApp = MapaTelas.ehAppWhatsApp(a.tp_app);
            var cor = CORES_APP[a.tp_app] || "#8696a0";
            $("<button>")
              .addClass("btn btn-mensageiro")
              .css("--btn-accent", cor)
              .html((ehWhatsApp ? ICONE_WHATSAPP : ICONE_MENSAGEIRO) + " " + a.tp_app +
                    " (" + formatarNumero(a.qt_mensagens) + ")")
              .on("click", function () {
                window.location.href = MapaTelas.paginaConversa("msg", a.tp_app) + qsExtracao +
                  "&fonte=msg&app=" + encodeURIComponent(a.tp_app);
              })
              .appendTo($acoes);
          });
        });
      }
      // concluído OU cancelado: o cancelamento preserva o parcial, e o prompt sobre ele vale
      if (p.cd_status === "concluido" || p.cd_status === "cancelado") {
        botaoPromptIa(
          inq,
          "Conversa de IA restrita só a este caso — não enxerga outros aparelhos, RIFs, extratos ou IPLs do inquérito",
          function () { abrirPromptEscopo(inq.id_inquerito, "extracao", p.id_extracao, titulo); }
        ).prependTo(cab.$acoes);
      }
    }

    podarVaziosBloco($bloco);
    return $bloco;
  }

  function cancelarIped(idExtracao, noConta) {
    Dialogo.confirmar({
      titulo: "Cancelar importação",
      mensagem: "Parar a importação do caso " + (noConta || "sem conta identificada") + "? O que já foi lido até agora é preservado.",
      rotuloOk: "Cancelar importação", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/iped/" + idExtracao + "/cancelar", method: "POST" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao cancelar a importação." });
        });
    });
  }

  function apagarIped(idExtracao, noConta, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar importação do IPED",
      mensagem: "Apagar a importação do caso " + (noConta || "sem conta identificada") + " do inquérito " + numeroInquerito + "? Arquivos, e-mails e contatos dessa importação serão removidos (a pasta em data/ não é apagada). Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/iped/" + idExtracao, method: "DELETE" })
        .done(function () { atualizarTudo(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a importação." });
        });
    });
  }

  function renderExtracaoBloco(inq, e) {
    // Extração de conta na nuvem (UFDR "Google Warrant Return"): não é um aparelho — o card
    // mostra os domínios de nuvem (arquivos, e-mails, atividades, agenda…) em vez de WhatsApp,
    // SMS e ligações, que não existem numa quebra de sigilo de conta Google.
    if (e.in_nuvem) return renderNuvemGoogleBloco(inq, e);

    var $bloco = $("<div>").addClass("extracao-bloco")
      .attr("data-id-extracao", e.id_extracao);

    var dispositivo = [e.no_dispositivo, e.no_fabricante].filter(Boolean).join(" · ");
    var apelidoCab = apelidoExtracao(e);
    // título composto: continua sendo o rótulo do escopo nas telas de IA e de achados —
    // com apelido na frente quando houver ("Celular do João — RELATORIO.ufdr — …").
    var titulo = (apelidoCab ? apelidoCab + " — " : "") + e.no_arquivo + (dispositivo ? " — " + dispositivo : "");
    var so = sistemaDoAparelho(e);
    var modeloBase = modeloDoAparelho(e);
    var cab = montarCabecalho($bloco, {
      icone: so === "ios" ? ICONE_APPLE : (so === "android" ? ICONE_ANDROID : ICONE_DISPOSITIVO),
      classeIcone: so ? "is-" + so : "",
      titulo: tituloModeloComApelido(e, modeloBase),
      tituloAttr: titulo,
      subtitulo: e.no_arquivo
    });
    if (e.cd_status !== "concluido") {
      badgeStatus(e.cd_status, e.tx_erro, e.cd_fase, e.qt_progresso_fase)
        .addClass("badge-status-extra")
        .toggleClass("em-andamento", e.cd_status === "pendente" || e.cd_status === "processando")
        .appendTo(cab.$badges);
    }
    // concluída, mas com algo pulado/avisado na importação (modelo não baixado, cópia truncada,
    // hash reaproveitado…) — o texto completo de TX_AVISO está no tooltip
    if (e.tx_aviso && (e.cd_status === "concluido" || e.cd_status === "cancelado")) {
      $("<span>")
        .addClass("badge badge-aviso")
        .attr("title", e.tx_aviso)
        .attr("role", "status")
        .text("⚠ Alerta na importação")
        .appendTo(cab.$badges);
    }
    badgeArquivoUfdr(e).appendTo(cab.$badges);
    var $provaCompartilhada = badgeProvaCompartilhada(e);
    if ($provaCompartilhada) $provaCompartilhada.appendTo(cab.$badges);
    // Extração cancelada preserva o que foi lido até a parada (e já mostra as contagens e os
    // botões de dados abaixo), então o prompt de IA e os achados valem para ela do mesmo jeito.
    // Fica de fora só quem ainda está rodando ou falhou sem dado nenhum.
    if (e.cd_status === "concluido" || e.cd_status === "cancelado") {
      botaoPromptIa(
        inq,
        "Conversa de IA restrita só a este aparelho — não enxerga outros aparelhos, RIFs, extratos ou IPLs do inquérito",
        function () { abrirPromptEscopo(inq.id_inquerito, "extracao", e.id_extracao, titulo); }
      ).appendTo(cab.$acoes);
      $("<button>")
        .addClass("btn btn-achado")
        .attr("title", "Registrar informações já apuradas sobre este aparelho, para a IA usar como contexto")
        .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
        .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "extracao", e.id_extracao, titulo); })
        .appendTo(cab.$acoes);
    }
    // Fica visível, e não no menu: é o conserto do aviso "Arquivo ausente" que está ali do lado,
    // e só aparece nesse caso.
    if (!e.in_arquivo_presente && podeGerenciar(inq)) {
      $("<button>")
        .addClass("btn btn-secondary")
        .html(ICONE_EXTRAIR + " Re-localizar arquivo")
        .on("click", function () { restaurarArquivo(e.id_extracao, e.no_arquivo); })
        .appendTo(cab.$acoes);
    }
    var hashesExtracao = textoHashesArquivo(e.co_hash_md5, e.co_hash_sha1, e.co_hash_sha256);
    var menuExtracao = [
      itemHash(e.no_arquivo, hashesExtracao),
      {
        html: ICONE_EDITAR + (apelidoCab ? " Editar apelido" : " Dar apelido"),
        title: apelidoCab
          ? "Apelido atual: \"" + apelidoCab + "\" — clique para alterar ou remover"
          : "Nome curto para identificar este aparelho no card (ex.: Celular do João)",
        onClick: function () { editarApelidoExtracao(e); }
      },
      itemInfoProva(titulo, [
        ["Arquivo original", e.no_arquivo],
        ["Aparelho", dispositivo],
        ["Modelo detectado", e.no_modelo_detectado],
        ["Nome do aparelho", e.no_nome_dispositivo],
        ["Data da importação", e.dt_upload ? formatarDataHora(e.dt_upload) : ""],
        ["Tamanho do arquivo", e.qt_bytes_arquivo ? formatarBytes(e.qt_bytes_arquivo) : ""],
        ["Hash do arquivo", hashesExtracao]
      ])
    ];
    if (podeGerenciar(inq)) {
      var emAndamento = e.cd_status === "processando" || e.cd_status === "pendente";
      menuExtracao.push("divisor");
      if (!emAndamento) {
        // Compartilhar a prova com outro inquérito: copia os dados deste aparelho para lá sem
        // refazer a importação e sem duplicar o .ufdr no disco (docs/compartilhar_prova.md).
        menuExtracao.push({
          html: ICONE_COMPARTILHAR + " Compartilhar prova",
          title: "Copiar esta prova para outro inquérito, sem reimportar o .ufdr.",
          onClick: function () { compartilharProva(e.id_extracao, titulo); }
        });
        // Mesma ação do botão "Transcrever" do modal "Todas as extrações" — aqui ela fica ao
        // alcance de quem já está olhando o aparelho, sem ter de abrir o modal e achar a linha certa.
        menuExtracao.push({
          html: ICONE_TRANSCREVER + " Transcrever áudios",
          disabled: !e.in_arquivo_presente,
          title: e.in_arquivo_presente
            ? "Transcrever os áudios ainda não transcritos (qualidade média)."
            : "Arquivo .ufdr ausente — restaure o arquivo antes de transcrever.",
          onClick: function () { transcreverExtracao(e.id_extracao); }
        });
      }
      if (e.cd_status === "processando") {
        var copiando = e.cd_fase === "copiando";
        menuExtracao.push({
          html: ICONE_PARAR + (copiando ? " Parar cópia" : " Parar extração"),
          perigoso: true,
          onClick: function () { cancelarExtracao(e.id_extracao, copiando); }
        });
      }
      menuExtracao.push({
        html: ICONE_APAGAR + " Apagar extração",
        perigoso: true,
        disabled: emAndamento,
        title: emAndamento ? "Aguarde a extração terminar para apagar." : "",
        onClick: function () { apagarExtracao(e.id_extracao, inq.nu_inquerito); }
      });
    } else {
      // quem não gerencia não compartilha nem apaga — o item aparece desabilitado com o motivo
      menuExtracao.push("divisor");
      menuExtracao.push(itemCompartilharIndisponivel());
    }
    cab.$acoes.append(criarMenuAcoes(menuExtracao, { compacto: true }));

    // Linha de resumo: primeiro QUEM é o aparelho, depois o pouco de contagem que o investigador
    // usa para decidir por onde começar. O nome do celular, a conta do sistema (Apple ID no
    // iPhone, Google no Android) e o modelo detectado vêm da identidade do próprio aparelho
    // (ver listar_extracoes) e só entram quando o UFDR os traz — extração antiga, aparelho sem
    // esses campos ou parada antes da leitura da identidade fica só com as contagens. O resto
    // das contagens saiu daqui de propósito: cada domínio já tem o seu botão logo abaixo.
    var contaDono = so === "android" ? (e.no_conta_google || e.no_conta_apple)
                                     : (e.no_conta_apple || e.no_conta_google);
    var resumo = [];
    [e.no_nome_dispositivo, contaDono, e.no_modelo_detectado].forEach(function (v) {
      v = $.trim(v || "");
      // no Android o nome do aparelho e o modelo detectado costumam ser o mesmo texto
      // ("Galaxy A06"): repetir só gasta a linha.
      var repetido = resumo.some(function (item) { return item.toLowerCase() === v.toLowerCase(); });
      if (v && !repetido) resumo.push(v);
    });
    resumo.push(formatarNumero(e.qt_conversas) + " WhatsApp (" + formatarNumero(e.qt_mensagens) + " msgs)");
    resumo.push(formatarNumero(e.qt_localizacoes) + " localizações");
    if (e.qt_notas != null) resumo.push(formatarNumero(e.qt_notas) + " notas");
    resumo.push(formatarNumero(e.qt_contas_financeiras) + " contas financeiras");
    if (e.qt_chaves_pix != null) resumo.push(formatarNumero(e.qt_chaves_pix) + " chaves PIX");
    if (e.qt_pessoas_identificadas != null) resumo.push(formatarNumero(e.qt_pessoas_identificadas) + " pessoas identificadas");
    if (e.qt_redes_wifi != null) resumo.push(formatarNumero(e.qt_redes_wifi) + " redes Wi-Fi");
    $("<div>").addClass("extracao-contagens").text(resumo.join(" · ")).appendTo($bloco);

    // Cópia de prova em andamento: as contagens acima são as que a cópia VAI ter (vieram da
    // origem junto com o resto do cabeçalho), mas os dados só ficam visíveis quando a cópia
    // termina — ela é gravada numa transação só, de propósito. Mostrar os botões de domínio aqui
    // levaria a telas vazias que parecem defeito, então eles só entram no fim.
    if (e.cd_status === "processando" && e.cd_fase === "copiando") {
      $("<div>").addClass("extracao-contagens").css("opacity", ".85").text(
        "Copiando a prova do inquérito " + (e.nu_inquerito_origem || "de origem") +
        " — os dados aparecem aqui quando a cópia terminar. Pode fechar esta página, o andamento continua."
      ).appendTo($bloco);
      podarVaziosBloco($bloco);
      return $bloco;
    }

    var $grupos = $("<div>").addClass("acoes-grupos").appendTo($bloco);
    var $principais = criarGrupoAcoes($grupos, "Principais");
    var $demais = criarGrupoAcoes($grupos, "Demais");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(e.id_extracao);

    function botaoConversa($destino, icone, rotulo, fonte, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = MapaTelas.paginaConversa(fonte) + qs + "&fonte=" + fonte;
        })
        .appendTo($destino);
    }

    function botaoDados($destino, icone, rotulo, pagina, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          window.location.href = pagina + ".html" + qs;
        })
        .appendTo($destino);
    }

    // Principais: as telas abertas em toda análise de aparelho. "Identidade do aparelho" entra aqui
    // mas segue condicional — nem todo UFDR traz info de aparelho ou antena de telefonia.
    botaoConversa($principais, ICONE_WHATSAPP, "WhatsApp", "wpp", "btn-whatsapp");
    botaoDados($principais, ICONE_NOTAS, "Notas", "notas", "btn-notificacoes");
    botaoDados($principais, ICONE_LOCALIZACOES, "Localizações", "localizacoes", "btn-localizacoes");
    botaoDados($principais, ICONE_TIMELINE, "Timeline", "timeline", "btn-timeline");
    botaoDados($principais, ICONE_BANCO, "Contas bancárias", "contas_financeiras", "btn-pix");
    botaoDados($principais, ICONE_PIX, "Chaves PIX", "chaves_pix", "btn-pix");
    botaoDados($principais, ICONE_WIFI, "Wi-Fi", "wifi", "btn-arquivos");
    if (Number(e.qt_info_aparelho) > 0 || Number(e.qt_antenas) > 0) {
      botaoDados($principais, ICONE_DONO, "Identidade do aparelho", "identidade_aparelho", "btn-navegacao");
    }

    // Demais: o resto do aparelho, na mesma ordem de antes.
    botaoConversa($demais, ICONE_SMS, "SMS", "sms", "btn-sms");
    botaoDados($demais, ICONE_CONTATOS, "Contatos", "contatos", "btn-contatos");
    botaoDados($demais, ICONE_LIGACOES, "Ligações", "ligacoes", "btn-ligacoes");
    botaoDados($demais, ICONE_NOTIFICACOES, "Notificações", "notificacoes", "btn-notificacoes");
    botaoDados($demais, ICONE_NAVEGACAO, "Navegação", "navegacao", "btn-navegacao");
    botaoDados($demais, ICONE_APLICATIVOS, "Aplicativos", "aplicativos", "btn-aplicativos");
    botaoDados($demais, ICONE_EMAIL, "E-mails", "emails", "btn-contatos");
    botaoDados($demais, ICONE_ARQUIVOS, "Arquivos", "arquivos", "btn-arquivos");

    // Domínios do UFDR importados na auditoria de aproveitamento. O botão só aparece quando a
    // extração tem o dado: o iPhone traz antena de telefonia e sensor de movimento que o
    // Android não tem, e vice-versa — botão que abre tela vazia faz procurar o que não existe.
    // Contagem nula = extração carregada antes destes domínios existirem, e também não mostra.
    [
      [ICONE_NAVEGACAO, "Redes sociais e buscas", "social_buscas",
       ["qt_atividade_social", "qt_buscas"]],
      [ICONE_TIMELINE, "Calendário", "calendario", ["qt_calendario"]],
      [ICONE_ENVOLVIDOS, "Dispositivos reconhecidos", "dispositivos_reconhecidos",
       ["qt_dispositivos_reconhecidos"]]
    ].forEach(function (item) {
      var temDado = item[3].some(function (campo) { return Number(e[campo]) > 0; });
      if (temDado) botaoDados($demais, item[0], item[1], item[2], "btn-navegacao");
    });

    // Mensageiros diversos (Instagram, Telegram, …): um botão por app presente nesta extração,
    // abrindo o mesmo visualizador de conversas com fonte=msg&app=<App>. Chegam depois do render,
    // direto na caixa "Demais" — a quantidade varia por aparelho e inflaria "Principais". A exceção
    // é o WhatsApp Business: ele só é "mensageiro" por um detalhe da extração (Source próprio no
    // UFDR), e quem abre o cartão procura por ele junto do WhatsApp — vai para "Principais",
    // encostado no botão do WhatsApp.
    if (Number(e.qt_msg_conversas)) {
      $.getJSON(
        "/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(e.id_extracao)
      ).done(function (apps) {
        apps.forEach(function (a) {
          if (APPS_NAO_MENSAGEIROS.indexOf(String(a.tp_app || "").toLowerCase()) !== -1) return;
          var cor = CORES_APP[a.tp_app] || "#8696a0";
          // o WhatsApp Business é WhatsApp: ícone, cor e tela do WhatsApp (ver MapaTelas.paginaConversa)
          var ehWhatsApp = MapaTelas.ehAppWhatsApp(a.tp_app);
          var $btnApp = $("<button>")
            .addClass("btn btn-mensageiro")
            .css("--btn-accent", cor)
            .html((ehWhatsApp ? ICONE_WHATSAPP : ICONE_MENSAGEIRO) +
                  " " + a.tp_app + " (" + formatarNumero(a.qt_mensagens) + ")")
            .on("click", function () {
              window.location.href = MapaTelas.paginaConversa("msg", a.tp_app) + qs +
                "&fonte=msg&app=" + encodeURIComponent(a.tp_app);
            });
          var $depoisDoWhatsApp = ehWhatsApp ? $principais.find(".btn-whatsapp").last() : $();
          if ($depoisDoWhatsApp.length) $btnApp.insertAfter($depoisDoWhatsApp);
          else if (ehWhatsApp) $btnApp.prependTo($principais);
          else $btnApp.appendTo($demais);
        });
      });
    }

    podarVaziosBloco($bloco);
    return $bloco;
  }

  // Card de uma extração de CONTA NA NUVEM (UFDR "Google Warrant Return" ou "ICLOUD_BACKUP"):
  // o conteúdo veio de files/Archives/*.zip (Minha Atividade/Drive/Google Fotos, ou iCloud), não
  // de um aparelho. Por isso não há WhatsApp/SMS/ligações/notificações — o card mostra os domínios
  // de nuvem e os que a nuvem compartilha com o celular (arquivos, e-mails, contatos, localizações…).
  function renderNuvemGoogleBloco(inq, e) {
    var $bloco = $("<div>").addClass("extracao-bloco")
      .attr("data-id-extracao", e.id_extracao);

    var fabricante = (e.no_fabricante || "").toLowerCase();
    var rotuloNuvem = fabricante.indexOf("apple") !== -1 ? "Nuvem Apple"
                    : fabricante.indexOf("google") !== -1 ? "Nuvem Google"
                    : "Nuvem";
    var conta = (e.no_dispositivo || "").trim();
    var apelidoNuvem = apelidoExtracao(e);
    var titulo = (apelidoNuvem ? apelidoNuvem + " — " : "") + rotuloNuvem + (conta ? " — " + conta : "");
    var cab = montarCabecalho($bloco, {
      icone: ICONE_NUVEM,
      titulo: apelidoNuvem ? apelidoNuvem + " · " + rotuloNuvem : titulo,
      tituloAttr: titulo + (e.no_arquivo ? " — " + e.no_arquivo : ""),
      subtitulo: e.no_arquivo
    });
    if (e.cd_status !== "concluido") {
      badgeStatus(e.cd_status, e.tx_erro, e.cd_fase, e.qt_progresso_fase)
        .addClass("badge-status-extra")
        .toggleClass("em-andamento", e.cd_status === "pendente" || e.cd_status === "processando")
        .appendTo(cab.$badges);
    }
    if (e.tx_aviso && (e.cd_status === "concluido" || e.cd_status === "cancelado")) {
      $("<span>")
        .addClass("badge badge-aviso")
        .attr("title", e.tx_aviso)
        .attr("role", "status")
        .text("⚠ Alerta na importação")
        .appendTo(cab.$badges);
    }
    badgeArquivoUfdr(e).appendTo(cab.$badges);
    var $provaCompartilhada = badgeProvaCompartilhada(e);
    if ($provaCompartilhada) $provaCompartilhada.appendTo(cab.$badges);

    if (e.cd_status === "concluido" || e.cd_status === "cancelado") {
      botaoPromptIa(
        inq,
        "Conversa de IA restrita só a esta conta na nuvem — não enxerga outros aparelhos, RIFs, extratos ou IPLs do inquérito",
        function () { abrirPromptEscopo(inq.id_inquerito, "extracao", e.id_extracao, titulo); }
      ).appendTo(cab.$acoes);
      $("<button>")
        .addClass("btn btn-achado")
        .attr("title", "Registrar informações já apuradas sobre esta conta na nuvem, para a IA usar como contexto")
        .html(ICONE_ACHADO_ANALISTA + " Hipótese<br/>da mídia")
        .on("click", function () { abrirAchadosEscopo(inq.id_inquerito, "extracao", e.id_extracao, titulo); })
        .appendTo(cab.$acoes);
    }
    if (!e.in_arquivo_presente && podeGerenciar(inq)) {
      $("<button>")
        .addClass("btn btn-secondary")
        .html(ICONE_EXTRAIR + " Re-localizar arquivo")
        .on("click", function () { restaurarArquivo(e.id_extracao, e.no_arquivo); })
        .appendTo(cab.$acoes);
    }
    var hashesNuvem = textoHashesArquivo(e.co_hash_md5, e.co_hash_sha1, e.co_hash_sha256);
    var menuExtracao = [
      itemHash(e.no_arquivo, hashesNuvem),
      {
        html: ICONE_EDITAR + (apelidoNuvem ? " Editar apelido" : " Dar apelido"),
        title: apelidoNuvem
          ? "Apelido atual: \"" + apelidoNuvem + "\" — clique para alterar ou remover"
          : "Nome curto para identificar esta conta no card (ex.: Conta do João)",
        onClick: function () { editarApelidoExtracao(e); }
      },
      itemInfoProva(titulo, [
        ["Arquivo original", e.no_arquivo],
        ["Conta na nuvem", conta],
        ["Tipo", rotuloNuvem],
        ["Data da importação", e.dt_upload ? formatarDataHora(e.dt_upload) : ""],
        ["Tamanho do arquivo", e.qt_bytes_arquivo ? formatarBytes(e.qt_bytes_arquivo) : ""],
        ["Hash do arquivo", hashesNuvem]
      ])
    ];
    if (podeGerenciar(inq)) {
      var emAndamento = e.cd_status === "processando" || e.cd_status === "pendente";
      menuExtracao.push("divisor");
      if (!emAndamento) {
        menuExtracao.push({
          html: ICONE_COMPARTILHAR + " Compartilhar prova",
          title: "Copiar esta prova para outro inquérito, sem reimportar o .ufdr.",
          onClick: function () { compartilharProva(e.id_extracao, titulo); }
        });
      }
      if (e.cd_status === "processando") {
        menuExtracao.push({
          html: ICONE_PARAR + " Parar extração",
          perigoso: true,
          onClick: function () { cancelarExtracao(e.id_extracao, false); }
        });
      }
      menuExtracao.push({
        html: ICONE_APAGAR + " Apagar extração",
        perigoso: true,
        disabled: emAndamento,
        title: emAndamento ? "Aguarde a extração terminar para apagar." : "",
        onClick: function () { apagarExtracao(e.id_extracao, inq.nu_inquerito); }
      });
    } else {
      // quem não gerencia não compartilha nem apaga — o item aparece desabilitado com o motivo
      menuExtracao.push("divisor");
      menuExtracao.push(itemCompartilharIndisponivel());
    }
    cab.$acoes.append(criarMenuAcoes(menuExtracao, { compacto: true }));

    // Resumo: o que a conta na nuvem trouxe. Tudo condicional — cada conta exporta um conjunto
    // diferente de produtos (um backup do iCloud tem SMS/iMessage e contatos; um warrant do
    // Google tem e-mails, localizações e Minha Atividade).
    var partes = [];
    function addContagem(campo, rotulo) {
      if (Number(e[campo]) > 0) partes.push(formatarNumero(e[campo]) + " " + rotulo);
    }
    addContagem("qt_arquivos", "arquivos");
    addContagem("qt_emails", "e-mails");
    addContagem("qt_contatos", "contatos");
    addContagem("qt_sms_mensagens", "SMS");
    // "iMessage" seria chute só para backup do iCloud; uma nuvem de rede social (extratos
    // "nuvem" do Cellebrite) traz o total em qt_msg_mensagens com apps Instagram/Messenger.
    // O nome real de cada app sai de apps-mensageiros, no botão por app logo abaixo.
    addContagem("qt_msg_mensagens", "mensagens");
    addContagem("qt_localizacoes", "localizações");
    addContagem("qt_navegacao", "páginas visitadas");
    addContagem("qt_notas", "notas");
    addContagem("qt_aplicativos", "aplicativos");
    addContagem("qt_buscas", "buscas");
    addContagem("qt_atividades", "atividades");
    addContagem("qt_eventos", "eventos de agenda");
    addContagem("qt_acessos", "acessos");
    addContagem("qt_dispositivos", "dispositivos");
    addContagem("qt_perfil", "perfis de conta");
    addContagem("qt_metadados_nuvem", "arquivos com metadado de nuvem");
    $("<div>").addClass("extracao-contagens").text(partes.join(" · ") || "—").appendTo($bloco);

    var $grupos = $("<div>").addClass("acoes-grupos").appendTo($bloco);
    var $principais = criarGrupoAcoes($grupos, "Principais");
    var $demais = criarGrupoAcoes($grupos, "Demais");
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(e.id_extracao);

    function botaoDados($destino, icone, rotulo, pagina, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () { window.location.href = pagina + ".html" + qs; })
        .appendTo($destino);
    }

    function botaoConversa($destino, icone, rotulo, fonte, app, corClasse) {
      $("<button>")
        .addClass("btn " + corClasse)
        .html(icone + " " + rotulo)
        .on("click", function () {
          var url = MapaTelas.paginaConversa(fonte) + qs + "&fonte=" + fonte;
          if (app) url += "&app=" + encodeURIComponent(app);
          window.location.href = url;
        })
        .appendTo($destino);
    }

    // iCloud backup: SMS e iMessage são o conteúdo principal (não existem no warrant Google).
    if (Number(e.qt_sms_mensagens) > 0) {
      botaoConversa($principais, ICONE_SMS, "SMS (" + formatarNumero(e.qt_sms_mensagens) + ")", "sms", null, "btn-sms");
    }
    // Um botão por app REAL de mensageiro da conta (iMessage no backup do iCloud,
    // Instagram/Messenger numa nuvem de rede social) — o mesmo fetch do card de aparelho.
    // Com app="iMessage" fixo, uma nuvem Instagram abria "iMessage (21.172)" em branco.
    if (Number(e.qt_msg_mensagens) > 0) {
      $.getJSON(
        "/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(e.id_extracao)
      ).done(function (apps) {
        apps.forEach(function (a) {
          if (APPS_NAO_MENSAGEIROS.indexOf(String(a.tp_app || "").toLowerCase()) !== -1) return;
          var cor = CORES_APP[a.tp_app] || "#8696a0";
          var ehWhatsApp = MapaTelas.ehAppWhatsApp(a.tp_app);
          $("<button>")
            .addClass("btn btn-mensageiro")
            .css("--btn-accent", cor)
            .html((ehWhatsApp ? ICONE_WHATSAPP : ICONE_MENSAGEIRO) +
                  " " + a.tp_app + " (" + formatarNumero(a.qt_mensagens) + ")")
            .on("click", function () {
              window.location.href = MapaTelas.paginaConversa("msg", a.tp_app) + qs +
                "&fonte=msg&app=" + encodeURIComponent(a.tp_app);
            })
            .appendTo($principais);
        });
      });
    }
    // Nuvem com mídia de WhatsApp sem texto: iCloud (tars por JID) ou backup do Google Drive
    // (wa_<numero>/, uma linha do tempo da conta). O flag vem da listagem (tem_wa_parcial); a
    // maçã mantém o botão sempre — comportamento histórico da tela, que tem estado vazio próprio.
    if (fabricante.indexOf("apple") !== -1 || e.tem_wa_parcial) {
      botaoDados($principais, ICONE_WHATSAPP, "WhatsApp (parcial)", "whatsapp_parcial", "btn-whatsapp");
    }

    if (Number(e.qt_arquivos) > 0) {
      botaoDados($principais, ICONE_ARQUIVOS, "Arquivos", "arquivos", "btn-arquivos");
    }
    if (Number(e.qt_emails) > 0) {
      botaoDados($principais, ICONE_EMAIL, "E-mails", "emails", "btn-contatos");
    }
    if (Number(e.qt_contatos) > 0) {
      botaoDados($principais, ICONE_CONTATOS, "Contatos", "contatos", "btn-notificacoes");
    }
    if (Number(e.qt_localizacoes) > 0) {
      botaoDados($principais, ICONE_LOCALIZACOES, "Localizações", "localizacoes", "btn-localizacoes");
    }
    if (Number(e.qt_navegacao) > 0) {
      botaoDados($principais, ICONE_NAVEGACAO, "Navegação", "navegacao", "btn-navegacao");
    }
    if (Number(e.qt_aplicativos) > 0) {
      botaoDados($principais, ICONE_APLICATIVOS, "Aplicativos", "aplicativos", "btn-aplicativos");
    }

    if (Number(e.qt_notas) > 0) {
      botaoDados($demais, ICONE_NOTAS, "Notas", "notas", "btn-notas");
    }
    if (Number(e.qt_buscas) > 0 || Number(e.qt_atividade_social) > 0) {
      botaoDados($demais, ICONE_NAVEGACAO, "Redes sociais e buscas", "social_buscas", "btn-navegacao");
    }
    if (Number(e.qt_atividades) > 0) {
      botaoDados($demais, ICONE_NAVEGACAO, "Minha Atividade", "atividades", "btn-navegacao");
    }
    if (Number(e.qt_eventos) > 0) {
      botaoDados($demais, ICONE_TIMELINE, "Agenda", "agenda", "btn-notificacoes");
    }
    if (Number(e.qt_acessos) > 0) {
      botaoDados($demais, ICONE_LOCALIZACOES, "Acessos", "acessos", "btn-localizacoes");
    }
    if (Number(e.qt_dispositivos) > 0) {
      botaoDados($demais, ICONE_DISPOSITIVO, "Dispositivos da conta", "dispositivos_conta", "btn-pix");
    }
    if (Number(e.qt_perfil) > 0) {
      botaoDados($demais, ICONE_DONO, "Perfil da conta", "perfil_conta", "btn-notas");
    }
    if (Number(e.qt_metadados_nuvem) > 0) {
      botaoDados($demais, ICONE_NUVEM, "Nuvem × aparelho", "correlacao_nuvem", "btn-navegacao");
    }

    podarVaziosBloco($bloco);
    return $bloco;
  }




  function apagarInquerito(idInquerito, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar inquérito",
      mensagem: "Apagar o inquérito " + numeroInquerito + " por completo? Todas as extrações, conversas, mensagens, anexos, contatos, ligações, localizações, notificações, entidades e permissões dele serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito, method: "DELETE" })
        .done(function () {
          atualizarTudo();
        })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar o inquérito." });
        });
    });
  }

  function construirUrlsInvestigacao() {
    var params = getDeepLinkParams();
    var inqUrl = "/api/investigacao/inqueritos";
    var extUrl = "/api/investigacao/extracoes";
    if (params.inquerito) {
      inqUrl += "?inquerito=" + encodeURIComponent(params.inquerito);
      extUrl += "?inquerito=" + encodeURIComponent(params.inquerito);
    }
    return { inqUrl: inqUrl, extUrl: extUrl };
  }

  function processarRespostaInvestigacoes(inqs, extracoesRaw) {
    var params = getDeepLinkParams();
    inqueritosTodos = inqs;
    var extracoes = encurtar(extracoesRaw);
    if (params.inquerito && inqueritosTodos.length === 1 && inqueritosTodos[0].delegacia) {
      var del = inqueritosTodos[0].delegacia;
      var $selDel = $("#investigacao-delegacia-select");
      if ($selDel.find('option[value="' + del + '"]').length === 0) {
        $selDel.append($("<option>").val(del).text(del));
      }
      $selDel.val(del);
    }
    agruparExtracoes(extracoes);
    registrarEstadoRender(inqueritosTodos, extracoes);
    esconderSkeleton();
    atualizarTituloPagina();
    renderBreadcrumb();
    renderInvestigacoes();
    carregarResumoNoturno();
    reagendarPoll();
  }

  // $.getJSON não tem timeout: se o servidor travar no meio da resposta, a página fica
  // no skeleton para sempre. Com timeout o fail roda e a tela mostra erro + retry.
  var POLL_TIMEOUT_MS = 30000;

  function getJSONComTimeout(url) {
    return $.ajax({ url: url, method: "GET", dataType: "json", timeout: POLL_TIMEOUT_MS });
  }

  function mostrarErroCarregamento() {
    esconderSkeleton();
    mostrarAvisoDetalhe("Erro ao carregar investigações. O servidor demorou demais ou a conexão falhou.", "erro");
    $("<button>").addClass("btn btn-secondary").attr("type", "button")
      .text("Tentar novamente")
      .on("click", function () { carregarInvestigacoes(); })
      .appendTo($avisoDetalhe);
  }

  function carregarInvestigacoes() {
    var params = getDeepLinkParams();
    mostrarSkeleton();
    mostrarAvisoDetalhe(null);
    var pollUrl = "/api/investigacao/poll";
    if (params.inquerito) pollUrl += "?inquerito=" + encodeURIComponent(params.inquerito);
    getJSONComTimeout(pollUrl).done(function (resp) {
      // poll retorna {inqueritos, extracoes}
      processarRespostaInvestigacoes(resp.inqueritos || [], resp.extracoes || []);
    }).fail(function () {
      // fallback para backends sem /poll (ou erro transitório)
      var urls = construirUrlsInvestigacao();
      var inqReq = getJSONComTimeout(urls.inqUrl);
      var extReq = getJSONComTimeout(urls.extUrl);
      $.when(inqReq, extReq).done(function (inqResp, extResp) {
        processarRespostaInvestigacoes(inqResp[0], extResp[0]);
      }).fail(function () {
        mostrarErroCarregamento();
      });
    });
  }

  // Aplica busca, delegacia, status e ordenação sobre a lista completa.
  // No modo detalhe (?inquerito=) o filtro de delegacia é ignorado: o link direto tem que
  // mostrar o inquérito mesmo que ele seja de outra delegacia.
  function filtrarItens() {
    var termo = $.trim($busca.val()).toLowerCase();
    var status = $filtroStatus.val();
    var ignoraDelegacia = ehModoDetalheInquerito();
    var lista = inqueritosTodos.filter(function (item) {
      if (!ignoraDelegacia && delegaciaAtual && (item.delegacia || "") !== delegaciaAtual) return false;
      if (termo && (item.nu_inquerito || "").toLowerCase().indexOf(termo) === -1) {
        return false;
      }
      var qtExtracoes = Number(item.qt_extracoes) || 0;
      if (status === "andamento") return !!item.tem_extracao_em_andamento;
      if (status === "concluido") return qtExtracoes > 0 && !item.tem_extracao_em_andamento;
      if (status === "sem") return qtExtracoes === 0;
      return true;
    });
    var ordem = $ordenacao && $ordenacao.length ? $ordenacao.val() : "recentes";
    lista.sort(function (a, b) {
      if (ordem === "az") return String(a.nu_inquerito || "").localeCompare(String(b.nu_inquerito || ""));
      if (ordem === "andamento") {
        var ea = a.tem_extracao_em_andamento ? 0 : 1;
        var eb = b.tem_extracao_em_andamento ? 0 : 1;
        if (ea !== eb) return ea - eb;
      }
      var ida = Number(a.id_inquerito) || 0;
      var idb = Number(b.id_inquerito) || 0;
      return idb - ida;
    });
    return lista;
  }

  function atualizarContadorTotal(filtrados) {
    if (!$total || !$total.length) return;
    var total = inqueritosTodos.length;
    var n = filtrados ? filtrados.length : total;
    $total.empty();
    if (!total) { $total.text(""); return; }
    $total.append(document.createTextNode(n === total
      ? total + (total === 1 ? " investigação" : " investigações")
      : n + " de " + total + " investigações"));
    var emAnd = inqueritosTodos.filter(function (i) { return i.tem_extracao_em_andamento; }).length;
    if (emAnd) $total.append(document.createTextNode(" · " + emAnd + " em andamento"));
  }

  // Zera busca/status/delegacia/ordenação de uma vez — saída do "0 resultados" quando
  // um filtro ficou preso (valor obsoleto no localStorage, texto restaurado pelo navegador…).
  function limparFiltrosInvestigacao() {
    $busca.val("");
    $filtroStatus.val("todos");
    delegaciaAtual = "";
    salvarDelegaciaStorage("");
    var $selDel = $("#investigacao-delegacia-select");
    if ($selDel.length) $selDel.val("");
    if ($ordenacao && $ordenacao.length) {
      $ordenacao.val("recentes");
      try { localStorage.setItem("investigacao-ordenacao", "recentes"); } catch (e) {}
    }
    paginaAtual = 1;
    renderInvestigacoes();
  }

  function renderInvestigacoes() {
    $investigacoesLista.empty();
    if ($limpar && $limpar.length) $limpar.toggleClass("hidden", !$.trim($busca.val()));

    var params = getDeepLinkParams();
    if (!inqueritosTodos.length) {
      if (params.inquerito) {
        mostrarAvisoDetalhe("Inquérito " + params.inquerito + " não encontrado ou sem permissão.", "erro");
        $investigacoesLista.append($("<p>").addClass("empty-hint").text("Nenhum inquérito encontrado para o ID informado."));
      } else {
        $investigacoesLista.append($("<p>").addClass("empty-hint").text("Nenhuma investigação ainda."));
      }
      atualizarContadorTotal([]);
      renderPaginacao(0, 0, 0, 1);
      return;
    } else {
      // limpa aviso de erro anterior se agora há dados
      if (params.inquerito) mostrarAvisoDetalhe(null);
    }

    var filtrados = filtrarItens();
    atualizarContadorTotal(filtrados);
    if (window.console && typeof console.debug === "function") {
      console.debug("[investigacao] total=" + inqueritosTodos.length +
        " filtrados=" + filtrados.length +
        " busca=" + JSON.stringify($.trim($busca.val())) +
        " status=" + $filtroStatus.val() +
        " delegacia=" + JSON.stringify(delegaciaAtual));
    }
    // anuncia contagem para leitor de tela sem entupir a cada tecla (só após render)
    if ($live && $live.length && !ehModoDetalheInquerito()) {
      $live.text(filtrados.length === 1 ? "1 investigação encontrada" : filtrados.length + " investigações encontradas");
    }
    var totalPaginas = Math.max(1, Math.ceil(filtrados.length / POR_PAGINA));
    if (paginaAtual > totalPaginas) paginaAtual = totalPaginas;
    if (paginaAtual < 1) paginaAtual = 1;

    if (!filtrados.length) {
      $investigacoesLista.append($("<p>").addClass("empty-hint").text("Nenhuma investigação encontrada para o filtro atual."));
      // Diz QUAL filtro está prendendo tudo — e oferece a saída num clique.
      var filtrosAtivos = [];
      var termoAtual = $.trim($busca.val());
      if (termoAtual) filtrosAtivos.push("busca “" + termoAtual + "”");
      var statusAtual = $filtroStatus.val();
      if (statusAtual && statusAtual !== "todos") {
        filtrosAtivos.push("status: " + $filtroStatus.find("option:selected").text());
      }
      if (delegaciaAtual) filtrosAtivos.push("delegacia: " + delegaciaAtual);
      if (filtrosAtivos.length) {
        $("<p>").addClass("empty-hint").text("Filtros ativos — " + filtrosAtivos.join(" · ")).appendTo($investigacoesLista);
        $("<button>").addClass("btn btn-secondary investigacao-limpar-filtros").attr("type", "button")
          .text("Limpar filtros")
          .on("click", limparFiltrosInvestigacao)
          .appendTo($investigacoesLista);
      }
      renderPaginacao(0, 0, 0, 1);
      return;
    }

    var inicio = (paginaAtual - 1) * POR_PAGINA;
    var pagina = filtrados.slice(inicio, inicio + POR_PAGINA);
    pagina.forEach(function (item) {
      $investigacoesLista.append(renderCardInvestigacao(item, extracoesPorInquerito[item.id_inquerito] || []));
    });

    renderPaginacao(inicio + 1, inicio + pagina.length, filtrados.length, totalPaginas);
    carregarConvergenciasDaPagina(pagina);
    destacarInqueritoDoLink();
  }

  /* Alerta de convergência de todos os cards da página, numa requisição só.
   *
   * Era uma chamada por card, dentro de renderCardInvestigacao: dez consultas de cruzamento entre
   * inquéritos para desenhar uma página, nenhuma com .fail() — então um 500 na rota rendia dez
   * toasts de "Erro no servidor" empilhados, um por card. O front ainda baixava todas as
   * ocorrências para contar os identificadores distintos no navegador; agora a contagem vem
   * pronta do banco (ver resumo_convergencia_inqueritos). */
  function carregarConvergenciasDaPagina(itens) {
    var ids = itens
      .filter(function (item) { return item.id_inquerito; })
      .map(function (item) { return item.id_inquerito; });
    if (!ids.length) return;

    Api.get("/api/investigacao/convergencia-resumo", { inqueritos: ids.join(",") })
      .done(function (porInquerito) {
        Object.keys(porInquerito || {}).forEach(function (idInquerito) {
          var relacionados = porInquerito[idInquerito];
          if (!relacionados || !relacionados.length) return;
          var $card = $investigacoesLista.children().filter(function () {
            return String($(this).attr("data-id-inquerito")) === String(idInquerito);
          });
          if (!$card.length) return;   // página trocou enquanto a resposta vinha
          $card.find(".investigacao-info").first().after(alertaConvergencia(relacionados));
        });
      });
    // sem .fail() próprio: a rede de segurança de utils/api.js já avisa, e agora uma vez só
  }

  /* O número do inquérito é texto digitado no cadastro, então nada aqui pode ser concatenado em
   * .html(): montado com .text()/.attr(), o valor nunca é lido como marcação. */
  function alertaConvergencia(relacionados) {
    // <details> sem "open": vem recolhido por padrão — com muitos inquéritos
    // relacionados a lista esticava o card. <details> nativo, sem JS de toggle,
    // mesmo padrão dos filtros da mesa e do raciocínio da IA.
    var $alerta = $("<details>").addClass("convergencia-alerta").attr("role", "note");
    $("<summary>")
      .addClass("convergencia-alerta-titulo")
      .text("⚠ Convergência detectada (" + relacionados.length + ")")
      .appendTo($alerta);
    relacionados.forEach(function (rel) {
      var $linha = $("<div>").addClass("convergencia-alerta-linha").appendTo($alerta);
      $linha.append(document.createTextNode("Este inquérito compartilha "));
      $("<strong>").text(formatarNumero(rel.qt_identificadores)).appendTo($linha);
      $linha.append(document.createTextNode(" identificador(es) com "));
      $("<a>")
        .attr("href", "investigacao.html?inquerito=" + encodeURIComponent(rel.id_inquerito_relacionado))
        .css("fontWeight", "500")
        .text(rel.nu_inquerito_relacionado)
        .appendTo($linha);
    });
    return $alerta;
  }

  function destacarInqueritoDoLink() {
    if (!idInqueritoDestaque) return;
    var alvo = String(idInqueritoDestaque);
    idInqueritoDestaque = null;
    var $card = $investigacoesLista.children().filter(function () {
      return String($(this).attr("data-id-inquerito")) === alvo;
    });
    if (!$card.length) return;
    $card.addClass("investigacao-card-destaque");
    $card[0].scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(function () { $card.removeClass("investigacao-card-destaque"); }, 2400);
  }

  function renderPaginacao(de, ate, total, totalPaginas) {
    $paginacao.empty();
    if (!total || ehModoDetalheInquerito()) {
      $paginacao.addClass("hidden");
      return;
    }
    $paginacao.removeClass("hidden");
    $paginacao.attr("role", "navigation").attr("aria-label", "Paginação das investigações");

    $("<span>")
      .addClass("paginacao-info")
      .attr("aria-live", "polite")
      .text("Mostrando " + de + "–" + ate + " de " + formatarNumero(total) + " registro(s)")
      .appendTo($paginacao);

    var $ctrls = $("<div>").addClass("paginacao-ctrls").appendTo($paginacao);
    $("<button>")
      .addClass("btn btn-secondary paginacao-btn")
      .attr("type", "button")
      .attr("aria-label", "Página anterior")
      .prop("disabled", paginaAtual <= 1)
      .html("‹ Anterior")
      .on("click", function () { if (paginaAtual > 1) { paginaAtual--; renderInvestigacoes(); } })
      .appendTo($ctrls);
    $("<span>")
      .addClass("paginacao-atual")
      .attr("aria-current", "page")
      .text("Página " + paginaAtual + " de " + totalPaginas)
      .appendTo($ctrls);
    $("<button>")
      .addClass("btn btn-secondary paginacao-btn")
      .attr("type", "button")
      .attr("aria-label", "Próxima página")
      .prop("disabled", paginaAtual >= totalPaginas)
      .html("Próxima ›")
      .on("click", function () { if (paginaAtual < totalPaginas) { paginaAtual++; renderInvestigacoes(); } })
      .appendTo($ctrls);
  }

  // debounce de 250ms para não re-renderizar a cada tecla em lista grande
  $busca.on("input", debounce(function () { paginaAtual = 1; renderInvestigacoes(); }, 250));
  $filtroStatus.on("change", function () { paginaAtual = 1; renderInvestigacoes(); });
  if ($ordenacao && $ordenacao.length) {
    try {
      var ordemSalva = localStorage.getItem("investigacao-ordenacao");
      if (ordemSalva === "az" || ordemSalva === "andamento" || ordemSalva === "recentes") $ordenacao.val(ordemSalva);
    } catch (e) {}
    $ordenacao.on("change", function () {
      try { localStorage.setItem("investigacao-ordenacao", String($(this).val())); } catch (e) {}
      paginaAtual = 1;
      renderInvestigacoes();
    });
  }
  if ($limpar && $limpar.length) {
    $limpar.on("click", function () {
      $busca.val("").focus();
      paginaAtual = 1;
      renderInvestigacoes();
    });
  }
  if ($extracoesBusca && $extracoesBusca.length) {
    $extracoesBusca.on("input", debounce(function () {
      var termo = $.trim($extracoesBusca.val()).toLowerCase();
      $corpo.find("tr").each(function () {
        var $tr = $(this);
        if (!termo) { $tr.show(); return; }
        $tr.toggle($tr.text().toLowerCase().indexOf(termo) !== -1);
      });
    }, 200));
  }
  // seletor de itens por página
  if ($porPagina.length) {
    $porPagina.val(String(POR_PAGINA));
    $porPagina.on("change", function () {
      var v = parseInt($(this).val(), 10);
      if (v === 10 || v === 20 || v === 50) {
        POR_PAGINA = v;
        try { localStorage.setItem("investigacao-por-pagina", String(v)); } catch (e) {}
        paginaAtual = 1;
        renderInvestigacoes();
      }
    });
  }


  function atualizarTudo() {
    carregarInvestigacoes();
    if (!$modal.hasClass("hidden")) {
      carregarModalExtracoes();
    }
  }

  // ---------- Polling de andamento ----------
  // Enquanto houver extração/importação/análise em andamento, a página repolava a cada 3s. Antes
  // isso re-renderizava a lista inteira (e o modal) do zero, jogando fora o DOM — menus abertos,
  // hover, seleção de texto e cliques em andamento sumiam a cada tick. Agora o polling só troca
  // os badges de status (fase + progresso) em lugar, e re-renderiza apenas quando algo muda de
  // estado de verdade (extração terminou/errou, inquérito novo/apagado, SIMBA/IPED/análise de
  // documento começou ou acabou) — raro se comparado com o tick de 3s.

  function agruparExtracoes(extracoes) {
    extracoesPorInquerito = {};
    extracoes.forEach(function (e) {
      // extrações-ponte de caso IPED (ver iped_utils.py) não aparecem como card próprio
      // aqui — só existem pra amarrar arquivos/e-mails/contatos às telas genéricas; a
      // extração já tem seu card específico ("Nuvem IPED —"), alimentado por
      // listar_extracoes_iped_inquerito.
      if (e.no_fabricante === "IPED") return;
      (extracoesPorInquerito[e.id_inquerito] = extracoesPorInquerito[e.id_inquerito] || []).push(e);
    });
  }

  // Registra o estado que o DOM reflete, para o próximo tick saber o que mudou.
  function registrarEstadoRender(inqueritos, extracoes) {
    idsInqueritosRenderizados = inqueritos.map(function (i) { return String(i.id_inquerito); }).sort().join("|");
    estadoExtracoes = {};
    extracoes.forEach(function (e) { estadoExtracoes[e.id_extracao] = { cd_status: e.cd_status, no_apelido: apelidoExtracao(e) }; });
  }

  function reagendarPoll() {
    clearTimeout(pollTimer);
    pollTimer = null;
    var temPendente = inqueritosTodos.some(function (inq) { return inq.tem_extracao_em_andamento; });
    if (temPendente) {
      // aba oculta: tick mais espaçado para não gastar rede/bateria
      pollTimer = setTimeout(atualizarStatusAoVivo, document.hidden ? 10000 : 3000);
    }
  }

  // volta da aba oculta: retoma o polling na hora em vez de esperar o tick espaçado
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && inqueritosTodos.some(function (inq) { return inq.tem_extracao_em_andamento; })) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(atualizarStatusAoVivo, 1000);
    }
  });

  // Substitui o badge no lugar, preservando as classes (badge-status-extra, em-andamento…).
  function atualizarBadgeAoVivo($badge, status, erro, fase, progresso) {
    if (!$badge || !$badge.length) return;
    var $novo = badgeStatus(status, erro, fase, progresso).addClass($badge.attr("class") || "");
    if ($badge.text() === $novo.text() && $badge.attr("title") === $novo.attr("title")) return;
    $badge.replaceWith($novo);
  }

  function atualizarBadgesInqueritos(inqueritos) {
    inqueritos.forEach(function (inq) {
      var $card = $investigacoesLista.children().filter('[data-id-inquerito="' + inq.id_inquerito + '"]');
      if (!$card.length) return;
      var $info = $card.find(".investigacao-info");
      var $badge = $info.find(".badge-status-inquerito");
      if (!inq.tem_extracao_em_andamento) return;
      if (!$badge.length) {
        badgeStatus("processando", null, inq.fase_em_andamento, inq.progresso_em_andamento)
          .addClass("badge-status-inquerito em-andamento")
          .appendTo($info);
      } else {
        atualizarBadgeAoVivo($badge, "processando", null, inq.fase_em_andamento, inq.progresso_em_andamento);
      }
    });
  }

  function atualizarBadgesExtracoes(extracoes) {
    extracoes.forEach(function (e) {
      var $bloco = $investigacoesLista.find('.extracao-bloco[data-id-extracao="' + e.id_extracao + '"]');
      if (!$bloco.length) return;
      var $badge = $bloco.find(".badge-status-extra").first();
      if ($badge.length) atualizarBadgeAoVivo($badge, e.cd_status, e.tx_erro, e.cd_fase, e.qt_progresso_fase);
    });
  }

  function atualizarBadgesModal(extracoes) {
    extracoes.forEach(function (e) {
      var $linha = $corpo.find('tr[data-id-extracao="' + e.id_extracao + '"]');
      if (!$linha.length) return;
      var $badge = $linha.find(".badge-status-extra").first();
      if ($badge.length) atualizarBadgeAoVivo($badge, e.cd_status, e.tx_erro, e.cd_fase, e.qt_progresso_fase);
    });
  }

  // Os dados de SIMBA, IPED e análise de documentos não vêm nos dois endpoints acima — vão um
  // GET por inquérito em andamento, só para atualizar os badges/blocos afetados em lugar.
  function atualizarBadgesBlocosEmAndamento(inqueritos) {
    var promessas = [];
    inqueritos.forEach(function (inq) {
      var id = inq.id_inquerito;
      var $card = $investigacoesLista.children().filter('[data-id-inquerito="' + id + '"]');
      if (!$card.length) return;

      promessas.push(
        $.getJSON("/api/investigacao/inqueritos/" + id + "/extratos-simba").done(function (lista) {
          lista.forEach(function (s) {
            var $bloco = $card.find('.extracao-bloco[data-id-simba="' + s.id_extrato + '"]');
            if (!$bloco.length) return;
            var emAndamentoNovo = s.cd_status === "pendente" || s.cd_status === "processando";
            if ($bloco.has(".em-andamento").length && !emAndamentoNovo) {
              // a importação terminou/falhou — o bloco renasce com contagens e botões
              $bloco.replaceWith(renderSimbaBloco(inq, s));
              return;
            }
            var $badge = $bloco.find(".badge-status-extra").first();
            if ($badge.length) atualizarBadgeAoVivo($badge, s.cd_status, s.tx_erro, s.cd_fase, s.qt_progresso_fase);
          });
        })
      );
      promessas.push(
        $.getJSON("/api/investigacao/inqueritos/" + id + "/extracoes-iped").done(function (lista) {
          lista.forEach(function (p) {
            var $bloco = $card.find('.extracao-bloco[data-id-iped="' + p.id_extracao_iped + '"]');
            if (!$bloco.length) return;
            var emAndamentoNovo = p.cd_status === "pendente" || p.cd_status === "processando";
            if ($bloco.has(".em-andamento").length && !emAndamentoNovo) {
              // a importação terminou/falhou — o bloco renasce com contagens e botões
              $bloco.replaceWith(renderIpedBloco(inq, p));
              return;
            }
            var $badge = $bloco.find(".badge-status-extra").first();
            if ($badge.length) atualizarBadgeAoVivo($badge, p.cd_status, p.tx_erro, p.cd_fase, p.qt_progresso_fase);
          });
        })
      );
      // Análise de entidades dos documentos: quando termina, o bloco vira resumo + botões.
      // Recria só o bloco do documento em lugar (renderDocumentoBloco não dispara requests).
      promessas.push(
        $.getJSON("/api/investigacao/inqueritos/" + id + "/documentos").done(function (lista) {
          lista.forEach(function (d) {
            var $bloco = $card.find('.extracao-bloco[data-id-documento="' + d.id_documento + '"]');
            if (!$bloco.length) return;
            var analiseAindaRodando = d.cd_analise === "pendente" || d.cd_analise === "processando";
            if ($bloco.has(".em-andamento").length && !analiseAindaRodando) {
              $bloco.replaceWith(renderDocumentoBloco(inq, d));
            }
          });
        })
      );
    });
    return $.when.apply($, promessas);
  }

  // O tick do polling: atualiza os status em lugar; re-renderiza só quando a composição da lista
  // ou o estado de algum processamento mudou (coisas que o badge em lugar não consegue refletir).
  // Respeita o mesmo filtro de deep-link do backend que carregarInvestigacoes usa.
  function processarPollAoVivo(inqueritos, extracoesRaw) {
    var extracoes = encurtar(extracoesRaw);
    inqueritosTodos = inqueritos;
    agruparExtracoes(extracoes);
    if (precisaRenderCompleto(inqueritos, extracoes)) {
      registrarEstadoRender(inqueritos, extracoes);
      renderInvestigacoes();
      if (!$modal.hasClass("hidden")) carregarModalExtracoes();
      reagendarPoll();
      return;
    }
    atualizarBadgesInqueritos(inqueritos);
    atualizarBadgesExtracoes(extracoes);
    if (!$modal.hasClass("hidden")) atualizarBadgesModal(extracoes);
    var emAndamento = inqueritos.filter(function (i) { return i.tem_extracao_em_andamento; });
    if (!emAndamento.length) {
      reagendarPoll();
      return;
    }
    atualizarBadgesBlocosEmAndamento(emAndamento).always(function () {
      registrarEstadoRender(inqueritos, extracoes);
      reagendarPoll();
    });
  }

  function atualizarStatusAoVivo() {
    var params = getDeepLinkParams();
    var pollUrl = "/api/investigacao/poll";
    if (params.inquerito) pollUrl += "?inquerito=" + encodeURIComponent(params.inquerito);
    getJSONComTimeout(pollUrl).done(function (resp) {
      processarPollAoVivo(resp.inqueritos || [], resp.extracoes || []);
    }).fail(function () {
      var urls = construirUrlsInvestigacao();
      var inqReq = getJSONComTimeout(urls.inqUrl);
      var extReq = getJSONComTimeout(urls.extUrl);
      $.when(inqReq, extReq).done(function (inqResp, extResp) {
        processarPollAoVivo(inqResp[0], extResp[0]);
      }).fail(function () {
        // tick perdido (timeout/rede): mantém o polling vivo em vez de morrer em silêncio
        reagendarPoll();
      });
    });
  }

  function precisaRenderCompleto(inqueritos, extracoes) {
    // composição da lista de inquéritos mudou (novo/apagado)
    var idsAtuais = inqueritos.map(function (i) { return String(i.id_inquerito); }).sort().join("|");
    if (idsAtuais !== idsInqueritosRenderizados) return true;

    // extração nova ou removida
    var idsExtAtuais = extracoes.map(function (e) { return String(e.id_extracao); }).sort().join("|");
    if (idsExtAtuais !== Object.keys(estadoExtracoes).sort().join("|")) return true;

    // status (não fase/progresso) de alguma extração mudou, ou o apelido (que o polling
    // em lugar não cobre — o título do bloco só renasce no render completo)
    for (var i = 0; i < extracoes.length; i++) {
      var e = extracoes[i];
      var ant = estadoExtracoes[e.id_extracao];
      if (ant && (ant.cd_status !== e.cd_status || ant.no_apelido !== apelidoExtracao(e))) return true;
    }

    // SIMBA/IPED/análise de documento também seguram tem_extracao_em_andamento: se um
    // processamento começou ou terminou no card renderizado, o bloco precisa renascer com a
    // cara certa (contagens, botões) — o badge em lugar não cobre essa virada.
    for (var j = 0; j < inqueritos.length; j++) {
      var inq = inqueritos[j];
      var $card = $investigacoesLista.children().filter('[data-id-inquerito="' + inq.id_inquerito + '"]');
      if (!$card.length) continue; // fora da página atual — não há como conferir pelo DOM
      var temEmAndamentoDom = !!$card.find(".em-andamento").length;
      if (!!inq.tem_extracao_em_andamento !== temEmAndamentoDom) return true;
    }
    return false;
  }

  function renderLinhaPermissao(p) {
    var $linha = $("<div>").addClass("permissao-linha");
    var $info = $("<div>").appendTo($linha);

    $("<div>").addClass("permissao-nome").text(p.no_usuario).appendTo($info);
    $("<div>").addClass("permissao-login").text(p.no_login).appendTo($info);

    var $acoes = $("<div>").css({ display: "flex", gap: "8px", alignItems: "center" }).appendTo($linha);

    // toggle administrador
    var $toggleLabel = $("<label>").addClass("switch-label").css({
      display: "flex", alignItems: "center", gap: "5px",
      fontSize: "12px", cursor: "pointer", whiteSpace: "nowrap"
    }).appendTo($acoes);
    var $toggle = $("<input>").attr("type", "checkbox")
      .prop("checked", !!p.in_admin)
      .on("change", function () {
        alterarAdminPermissao(p.id_usuario, $(this).is(":checked"));
      }).appendTo($toggleLabel);
    var $switch = $("<span>").addClass("switch").css({
      display: "inline-block", width: "28px", height: "16px",
      background: p.in_admin ? "#1a73e8" : "#ccc",
      borderRadius: "8px", position: "relative", transition: "background .15s",
      flexShrink: 0
    }).appendTo($toggleLabel);
    // bolinha do switch
    $("<span>").css({
      content: '""', display: "block", width: "12px", height: "12px",
      background: "#fff", borderRadius: "50%", position: "absolute",
      top: "2px", left: p.in_admin ? "14px" : "2px",
      transition: "left .15s"
    }).appendTo($switch);

    $toggleLabel.append("<span>Admin</span>");

    // atualiza visual do toggle ao mudar
    $toggle.on("change", function () {
      var on = $(this).is(":checked");
      $switch.css("background", on ? "#1a73e8" : "#ccc");
      $switch.children().css("left", on ? "14px" : "2px");
    });

    $("<button>")
      .addClass("btn btn-danger")
      .css({ padding: "4px 10px", fontSize: "12px", minWidth: "70px" })
      .text("Remover")
      .on("click", function () {
        revogarPermissao(p.id_usuario);
      })
      .appendTo($acoes);

    return $linha;
  }

  function alterarAdminPermissao(idUsuario, inAdmin) {
    $.ajax({
      url: urlBasePermissoes,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ id_usuario: idUsuario, in_admin: inAdmin })
    }).done(carregarPermissoes).fail(function (xhr) {
      var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao alterar permissão.";
      $permissoesStatus.text(msg).addClass("error");
    });
  }

  function carregarPermissoes() {
    $.getJSON(urlBasePermissoes).done(function (lista) {
      $permissoesLista.empty();
      $permissoesVazio.toggleClass("hidden", lista.length > 0);
      lista.forEach(function (p) {
        $permissoesLista.append(renderLinhaPermissao(p));
      });
      carregarSeletorPermissao(lista);
    });
  }

  function carregarSeletorPermissao(concedidos) {
    var idsConcedidos = concedidos.map(function (p) { return p.id_usuario; });
    $.getJSON("/api/investigacao/usuarios-basico").done(function (usuarios) {
      $permissaoSelect.empty();
      var disponiveis = usuarios.filter(function (u) {
        return idsConcedidos.indexOf(u.id_usuario) === -1 && (!usuarioLogado || u.id_usuario !== usuarioLogado.id_usuario);
      });
      if (!disponiveis.length) {
        $permissaoSelect.append($("<option>").val("").text("Nenhum usuário disponível"));
        $permissaoAdicionarBtn.prop("disabled", true);
        return;
      }
      $permissaoAdicionarBtn.prop("disabled", false);
      disponiveis.forEach(function (u) {
        $permissaoSelect.append($("<option>").val(u.id_usuario).text(u.no_usuario + " (" + u.no_login + ")"));
      });
    });
  }

  function abrirModalPermissoes(idInquerito, numeroInquerito) {
    abrirModalPermissoesUrl("/api/investigacao/inqueritos/" + idInquerito + "/permissoes", numeroInquerito);
  }

  function abrirModalPermissoesUrl(url, rotulo) {
    urlBasePermissoes = url;
    $permissoesNumero.text(rotulo);
    $permissoesStatus.text("").removeClass("success error");
    carregarPermissoes();
    abrirModalAcessivel($modalPermissoes, $permissaoSelect);
  }

  function revogarPermissao(idUsuario) {
    $.ajax({
      url: urlBasePermissoes + "/" + idUsuario,
      method: "DELETE"
    }).done(carregarPermissoes);
  }

  $("#permissoes-fechar").on("click", function () {
    fecharModalAcessivel($modalPermissoes);
  });

  $modalPermissoes.on("click", function (e) {
    if (e.target === this) {
      fecharModalAcessivel($modalPermissoes);
    }
  });

  $permissaoAdicionarBtn.on("click", function () {
    var idUsuario = $permissaoSelect.val();
    if (!idUsuario) return;
    $permissoesStatus.text("").removeClass("success error");
    $.ajax({
      url: urlBasePermissoes,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_usuario: parseInt(idUsuario, 10),
        in_admin: $permissaoAdminCheckbox.is(":checked")
      })
    })
      .done(function () {
        $permissaoAdminCheckbox.prop("checked", false);
        carregarPermissoes();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao conceder permissão.";
        $permissoesStatus.text(msg).addClass("error");
      });
  });

  $("#ver-extracoes-btn").on("click", function () {
    carregarModalExtracoes();
    abrirModalAcessivel($modal, $("#fechar-modal"));
  });

  $("#fechar-modal").on("click", function () {
    fecharModalAcessivel($modal);
  });

  $modal.on("click", function (e) {
    if (e.target === this) {
      fecharModalAcessivel($modal);
    }
  });

  function renderLinhaDocumento(d) {
    var $linha = $("<div>").addClass("permissao-linha");
    var $info = $("<div>").appendTo($linha);
    $("<div>").addClass("permissao-nome").text(d.no_arquivo).appendTo($info);
    var meta = (d.tp_arquivo || "?").toUpperCase() + " · " +
      (d.tx_erro ? "erro de extração" : formatarNumero(d.qt_caracteres) + " caracteres") +
      " · " + formatarDataHora(d.dt_processado) +
      (d.anexado_por ? " · por " + d.anexado_por : "");
    $("<div>").addClass("permissao-login").attr("title", d.tx_erro || "").text(meta).appendTo($info);
    var $acoes = $("<div>").css({ display: "flex", gap: "8px", alignItems: "center" }).appendTo($linha);
    if (!d.tx_erro) {
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "6px 12px", fontSize: "12px" })
        .text("Abrir")
        .on("click", function () { abrirDocumento(idInqueritoDocumentos, d); })
        .appendTo($acoes);
    }
    if (d.cd_analise === "concluido" && ((d.jq_pessoas || []).length || (d.jq_achados || []).length)) {
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "6px 12px", fontSize: "12px" })
        .text("Ver entidades")
        .on("click", function () {
          abrirEntidadesDocumento(idInqueritoDocumentos, d, (d.jq_pessoas || []).length ? "pessoas" : "achados");
        })
        .appendTo($acoes);
    }
    $("<button>")
      .addClass("btn btn-danger")
      .css({ padding: "6px 12px", fontSize: "12px" })
      .text("Remover")
      .on("click", function () { apagarDocumento(d.id_documento, d.no_arquivo); })
      .appendTo($acoes);
    return $linha;
  }

  function carregarDocumentosInquerito() {
    $.getJSON("/api/investigacao/inqueritos/" + idInqueritoDocumentos + "/documentos").done(function (lista) {
      encurtar(lista);
      $documentosLista.empty();
      $documentosVazio.toggleClass("hidden", lista.length > 0);
      lista.forEach(function (d) {
        $documentosLista.append(renderLinhaDocumento(d));
      });
    });
  }

  function apagarDocumento(idDocumento, nomeArquivo) {
    Dialogo.confirmar({
      titulo: "Remover documento",
      mensagem: "Remover o documento \"" + nomeArquivo + "\" deste inquérito? O texto extraído dele deixa de valer para a IA. Essa ação não pode ser desfeita.",
      rotuloOk: "Remover", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/documentos/" + idDocumento, method: "DELETE" })
        .done(function () {
          if (idInqueritoDocumentos) carregarDocumentosInquerito();
          carregarInvestigacoes(); // atualiza a contagem no botão do card
        })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao remover o documento." });
        });
    });
  }

  function abrirModalDocumentos(idInquerito, numeroInquerito) {
    idInqueritoDocumentos = idInquerito;
    $documentosNumero.text(numeroInquerito);
    carregarDocumentosInquerito();
    abrirModalAcessivel($modalDocumentos, $("#documentos-fechar"));
  }

  $("#documentos-fechar").on("click", function () {
    fecharModalAcessivel($modalDocumentos);
  });

  $modalDocumentos.on("click", function (e) {
    if (e.target === this) {
      fecharModalAcessivel($modalDocumentos);
    }
  });

  function salvarDelegaciaStorage(valor) {
    try { localStorage.setItem("analyser-delegacia", valor || ""); } catch (e) {}
  }

  // ---------- Resumo noturno (batch da madrugada) ----------
  // Bloco no topo do detalhe ?inquerito=ID: consolidado do caso + uma seção por fonte
  // (cada celular, RIF/SIMBA/..., documentos). Lê GET .../resumo-noturno, que só lê a
  // tabela INQ_TB05_RESUMO_NOTURNO — nunca chama a IA no request. O texto da IA nunca vai
  // por .html() cru: passa por DocTrecho.linkarTexto, que escapa tudo e só depois transforma
  // os marcadores [doc:ID]/[msg:fonte:ID] nas pastilhas que abrem a peça ou a mensagem citada.

  var ROTULOS_FONTE_RESUMO = {
    celular: "celular", rif: "RIF", simba: "SIMBA", ipei: "IPEI", ipl: "IPL",
    irpf: "IRPF", telefonia: "Telefonia", iped: "Nuvem IPED", documentos: "Peças"
  };

  function nomeExtracaoResumo(idExtracao) {
    var lista = extracoesPorInquerito;
    var ids = Object.keys(lista || {});
    for (var i = 0; i < ids.length; i++) {
      var arr = lista[ids[i]] || [];
      for (var j = 0; j < arr.length; j++) {
        if (String(arr[j].id_extracao) === String(idExtracao)) {
          var e = arr[j];
          var nome = e.no_dispositivo || (e.no_arquivo || "").split("/").pop() || ("extração " + idExtracao);
          return nome + " (" + (e.no_arquivo || "").split("/").pop() + ")";
        }
      }
    }
    return "extração " + idExtracao;
  }

  function rotuloFonteResumo(linha) {
    var base = ROTULOS_FONTE_RESUMO[linha.tp_fonte] || linha.tp_fonte;
    if (linha.tp_fonte === "celular" && linha.id_extracao) {
      return "[" + base + " — " + nomeExtracaoResumo(linha.id_extracao) + "]";
    }
    if (linha.id_fonte) return "[" + base + " " + linha.id_fonte + "]";
    return "[" + base + "]";
  }

  function formatarDataResumo(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var dd = ("0" + d.getDate()).slice(-2);
    var mm = ("0" + (d.getMonth() + 1)).slice(-2);
    var hh = ("0" + d.getHours()).slice(-2);
    var mi = ("0" + d.getMinutes()).slice(-2);
    return dd + "/" + mm + "/" + d.getFullYear() + " " + hh + ":" + mi;
  }

  function renderResumoNoturno(resp) {
    var $box = $("#resumo-noturno");
    $box.empty();
    if (!resp || !resp.tem || !resp.consolidado) {
      $box.addClass("hidden");
      return;
    }
    $box.removeClass("hidden");
    var cons = resp.consolidado;
    var $card = $("<div>").addClass("card card-fluido resumo-noturno-card").appendTo($box);
    // Vem fechado: o resumo é longo e empurra a lista/detalhe do inquérito pra baixo.
    var $painel = $("<details>").addClass("resumo-noturno-det").appendTo($card);
    var $head = $("<summary>").appendTo($painel);
    $($.parseHTML('<svg class="resumo-noturno-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>')).appendTo($head);
    $("<strong>").css({ fontSize: "15px" }).text("Resumo noturno do inquérito").appendTo($head);
    $("<span>").css({ fontSize: "12px", opacity: 0.75 })
      .text("gerado em " + formatarDataResumo(cons.dt_geracao) + (cons.no_modelo ? " · " + cons.no_modelo : ""))
      .appendTo($head);
    if (resp.desatualizado) {
      $("<span>").addClass("badge badge-aviso").attr("title", "Entrou material novo depois deste resumo — o próximo passe da madrugada regenera.")
        .text("desatualizado").appendTo($head);
    }
    var $corpo = $("<div>").addClass("resumo-noturno-corpo").appendTo($painel);
    $("<div>").css({ whiteSpace: "pre-wrap", fontSize: "13px", lineHeight: "1.55" })
      .html(DocTrecho.linkarTexto(cons.tx_resumo || "(resumo vazio)", { inquerito: cons.id_inquerito }))
      .appendTo($corpo);
    var fontes = resp.por_fonte || [];
    if (fontes.length) {
      var $detalhes = $("<div>").css({ marginTop: "10px", display: "flex", flexDirection: "column", gap: "6px" }).appendTo($corpo);
      fontes.forEach(function (linha) {
        var $det = $("<details>").css({ border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 10px" }).appendTo($detalhes);
        var titulo = rotuloFonteResumo(linha) + " — " + ((linha.tx_resumo || "").split("\n")[0] || "").slice(0, 120);
        $("<summary>").css({ cursor: "pointer", fontSize: "13px", fontWeight: "600" }).text(titulo).appendTo($det);
        $("<div>").css({ whiteSpace: "pre-wrap", fontSize: "13px", lineHeight: "1.55", marginTop: "6px" })
          .html(DocTrecho.linkarTexto(linha.tx_resumo || "(vazio)", { inquerito: linha.id_inquerito }))
          .appendTo($det);
        $("<div>").css({ fontSize: "11px", opacity: 0.65, marginTop: "4px" })
          .text("gerado em " + formatarDataResumo(linha.dt_geracao) + (linha.no_modelo ? " · " + linha.no_modelo : ""))
          .appendTo($det);
      });
    }
  }

  function carregarResumoNoturno() {
    var $box = $("#resumo-noturno");
    var params = getDeepLinkParams();
    if (!params.inquerito || inqueritosTodos.length !== 1) {
      $box.addClass("hidden").empty();
      return;
    }
    var id = inqueritosTodos[0].id_inquerito;
    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(id) + "/resumo-noturno")
      .done(renderResumoNoturno)
      .fail(function () { $box.addClass("hidden").empty(); });
  }

  function carregarDelegaciaStorage() {
    try { return localStorage.getItem("analyser-delegacia") || ""; } catch (e) { return ""; }
  }

  function carregarDelegacias() {
    $.getJSON("/api/delegacias").done(function (lista) {
      var $sel = $("#investigacao-delegacia-select");
      $sel.find("option:not(:first)").remove();
      if (delegaciaTravada && delegaciaAtual) {
        // vinculado a uma delegacia: o filtro mostra só a dela (o backend já recusaria
        // qualquer outra com 403 — a tela deixa de oferecer o que não funciona)
        $("<option>").val(delegaciaAtual).text(delegaciaAtual).appendTo($sel);
        $sel.val(delegaciaAtual).prop("disabled", true)
          .attr("title", "Seu usuário é vinculado a esta delegacia.");
        return;
      }
      // só popula o seletor: ele filtra a pasta de dados, e quem pode usar a IA não sai daqui
      // (vem em ia_ativa, por inquérito/caso — ver iaBloqueada)
      $.each(lista, function (_, d) {
        $("<option>").val(d.no_delegacia).text(d.no_delegacia).appendTo($sel);
      });
      if (delegaciaAtual) {
        $sel.val(delegaciaAtual);
        // valor salvo que não existe mais: volta para "Todas" em vez de filtrar tudo para fora
        if (!$sel.val()) {
          delegaciaAtual = "";
          salvarDelegaciaStorage("");
        }
      }
    });
  }

  $("#investigacao-delegacia-select").on("change", function () {
    delegaciaAtual = $(this).val() || "";
    salvarDelegaciaStorage(delegaciaAtual);
    paginaAtual = 1;
    renderInvestigacoes();
  });

  $.getJSON("/api/auth/eu")
    .done(function (usuario) {
      usuarioLogado = usuario;
      if (usuario.id_delegacia && usuario.no_delegacia) {
        // vinculado: o filtro é sempre a própria delegacia (ninguém troca por aí — o storage
        // velho de outra delegacia é descartado; o backend recusaria com 403 de qualquer jeito)
        delegaciaTravada = true;
        delegaciaAtual = usuario.no_delegacia;
        try { localStorage.removeItem("analyser-delegacia"); } catch (e) {}
      } else {
        // Só a escolha explícita salva filtra. Sem isso, a delegacia do usuário logado viraria
        // filtro padrão e esconderia todo o resto da lista no primeiro acesso.
        var storageVal = carregarDelegaciaStorage();
        if (storageVal) {
          delegaciaAtual = storageVal;
        }
      }
      carregarDelegacias();
    })
    .always(function () {
      atualizarTudo();
    });
})();
