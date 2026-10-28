/* Navegação dentro de um inquérito — substitui o antigo js/navegacao_extracao.js.
 *
 * Duas coisas, montadas em qualquer página que traga ?inquerito= na URL:
 *
 *  1. Trilha (breadcrumb) na topbar: "Investigação › <nº do inquérito> › <aparelho>", pra sempre
 *     dar pra saber onde se está e voltar pro caso sem depender do botão do navegador.
 *  2. Menu "Navegar", com as telas do inquérito (Mesa, IA, RIF, PDFs) e as da extração
 *     (WhatsApp, contatos, ligações…). Antes o menu só existia nas 15 telas de extração e
 *     exigia ?extracao= — agora basta ?inquerito=, e as telas-hub também o têm.
 *
 * Ponto de montagem: <span class="extracao-nav-container"></span> (nome mantido porque já está
 * no HTML de 16 páginas). Em páginas com .topbar a trilha vai pra cima do <h1 class="page-title">;
 * nas de layout próprio (whatsapp/mensageiros/ia, sem topbar) ela entra ao lado do botão.
 *
 * Requisições: nº do inquérito e lista de extrações vêm de endpoints já existentes e ficam em
 * sessionStorage (esses dados praticamente não mudam durante a sessão); RIFs e PDFs só são
 * buscados quando o menu é aberto pela primeira vez.
 *
 * Ícones (window.Icones) e a lista de telas (ITENS_INQUERITO/ITENS_EXTRACAO/ROTULO_PAGINA, em
 * window.MapaTelas) vêm de módulos compartilhados com js/sidebar.js e js/home.js — ver
 * js/icones.js e js/mapa_telas.js, que precisam carregar antes deste arquivo. */
(function () {
  "use strict";

  var ITENS_INQUERITO = window.MapaTelas.ITENS_INQUERITO;
  var ITENS_EXTRACAO = window.MapaTelas.ITENS_EXTRACAO;
  var ROTULO_PAGINA = window.MapaTelas.ROTULO_PAGINA;

  var CACHE_TTL_MS = 5 * 60 * 1000;

  function paramUrl(nome) {
    return new URLSearchParams(window.location.search).get(nome) || "";
  }

  function paginaAtual() {
    var pagina = window.location.pathname.split("/").pop().replace(".html", "") || "index";
    if (pagina === "inquerito_orbit") return "inquerito";
    return pagina;
  }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");
  var fonteAtual = paramUrl("fonte");
  var atual = paginaAtual();

  // ---------- cache de sessão ----------
  // nº do inquérito e nomes de aparelho não mudam enquanto se navega; guardar evita duas
  // requisições por página só pra desenhar a trilha.

  function buscarComCache(chave, url) {
    var d = $.Deferred();
    var bruto = null;
    try {
      bruto = sessionStorage.getItem(chave);
    } catch (e) {
      /* sessionStorage indisponível — segue direto pra rede */
    }
    if (bruto) {
      try {
        var guardado = JSON.parse(bruto);
        if (guardado && Date.now() - guardado.em < CACHE_TTL_MS) {
          return d.resolve(guardado.dados).promise();
        }
      } catch (e) {
        /* cache corrompido — ignora e busca de novo */
      }
    }
    $.getJSON(url)
      .done(function (dados) {
        try {
          sessionStorage.setItem(chave, JSON.stringify({ em: Date.now(), dados: dados }));
        } catch (e) {
          /* sem espaço/sem sessionStorage — só não guarda */
        }
        d.resolve(dados);
      })
      .fail(function () { d.resolve(null); });
    return d.promise();
  }

  function carregarNumeroInquerito() {
    return buscarComCache("nav:inqueritos", "/api/investigacao/inqueritos").then(function (lista) {
      if (!lista) return null;
      var achado = null;
      lista.forEach(function (i) {
        if (String(i.id_inquerito) === String(idInquerito)) achado = i;
      });
      return achado ? achado.nu_inquerito : null;
    });
  }

  function carregarExtracoes() {
    return buscarComCache(
      "nav:extracoes:" + idInquerito,
      "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/extracoes-lista"
    ).then(function (lista) { return lista || []; });
  }

  function nomeExtracao(e) {
    return e.no_dispositivo || e.no_fabricante || e.no_arquivo || ("Extração " + e.id_extracao);
  }

  // nome da mídia para a trilha: o apelido que o investigador deu, ou o nome do arquivo
  // da mídia quando não há apelido (ver extracoes-lista, que traz no_apelido)
  function nomeMidia(e) {
    var apelido = $.trim((e && e.no_apelido) || "");
    return apelido || (e && e.no_arquivo) || "";
  }

  // ---------- trilha ----------

  function montarTrilha(numero, extracoes) {
    var $trilha = $("<nav>").addClass("crumbs crumbs-completa").attr("aria-label", "Trilha de navegação");

    $("<a>").addClass("crumb").attr("href", "inqueritos_orbit.html").text("Inquérito").appendTo($trilha);
    $trilha.append(window.Icones.html("chevronRight", "crumb-sep"));
    var numeroFormatado = (typeof window !== "undefined" && window.AnalyserUtils && typeof window.AnalyserUtils.formatarNumeroInquerito === "function")
      ? window.AnalyserUtils.formatarNumeroInquerito(numero) : numero;
    $("<a>")
      .addClass("crumb")
      .attr("href", "inquerito_orbit.html?inquerito=" + encodeURIComponent(idInquerito))
      .attr("title", "Abrir a visão geral do inquérito")
      .text(numeroFormatado || "Inquérito selecionado")
      .appendTo($trilha);

    if (idExtracao) {
      var atualExtracao = null;
      extracoes.forEach(function (e) {
        if (String(e.id_extracao) === String(idExtracao)) atualExtracao = e;
      });
      if (!atualExtracao) atualExtracao = { id_extracao: idExtracao };
      if (atualExtracao) {
        $trilha.append(window.Icones.html("chevronRight", "crumb-sep"));
        $("<span>")
          .addClass("crumb crumb-aparelho")
          .attr("title", atualExtracao.no_arquivo || "")
          .html(window.Icones.html("aparelho"))
          .append($("<span>").text(nomeMidia(atualExtracao) || nomeExtracao(atualExtracao)))
          .appendTo($trilha);
      }
    }

    return $trilha;
  }

  function inserirTrilha($trilha) {
    var $titulo = $(".topbar .page-title").first();
    if ($titulo.length) {
      // a trilha fica acima do título, e os dois viram um bloco só pra .topbar continuar
      // distribuindo espaço entre "contexto" e .topbar-actions
      var $bloco = $("<div>").addClass("topbar-context").insertBefore($titulo);
      $bloco.append($trilha).append($titulo);
      return;
    }
    // páginas sem topbar (whatsapp/mensageiros/ia): a trilha entra ao lado do botão Navegar,
    // fechada com o nome da própria tela — não há <h1> visível pra fazer esse papel
    var rotulo = ROTULO_PAGINA[atual];
    // as duas telas de conversa servem várias fontes (WhatsApp, SMS, um mensageiro cada): o nome
    // da tela é o que está sendo listado, e não o arquivo .html que a fonte calhou de abrir
    if (atual === "whatsapp" || atual === "mensageiros") {
      rotulo = fonteAtual === "sms" ? "SMS"
             : fonteAtual === "msg" ? (paramUrl("app") || "Mensageiros")
             : "WhatsApp";
    }
    if (rotulo) {
      $trilha.append(window.Icones.html("chevronRight", "crumb-sep"));
      $("<span>").addClass("crumb crumb-atual").text(rotulo).appendTo($trilha);
    }
    // entra dentro do container (inline-flex, ancorado no canto), à esquerda do botão
    $(".extracao-nav-container").first().prepend($trilha.addClass("crumbs-flutuante"));
  }

  // ---------- menu ----------

  function urlItem(item, extracaoEscolhida) {
    var qs = "?inquerito=" + encodeURIComponent(idInquerito);
    if (item.escopo === "extracao" && extracaoEscolhida) {
      qs += "&extracao=" + encodeURIComponent(extracaoEscolhida);
    }
    if (item.fonte) qs += "&fonte=" + encodeURIComponent(item.fonte);
    if (item.extra) qs += item.extra;
    return item.pagina + ".html" + qs;
  }

  function ehAtivo(item) {
    if (atual !== item.pagina) return false;
    if (item.fonte) return fonteAtual === item.fonte || (!fonteAtual && item.fonte === "wpp");
    if (item.idParam) return paramUrl(item.idParam) === String(item.idValor);
    return true;
  }

  function montarItem(item, extracaoEscolhida) {
    var ativo = ehAtivo(item);
    var $a = $("<a>")
      .addClass("extracao-nav-item" + (ativo ? " active" : ""))
      .attr("href", urlItem(item, extracaoEscolhida))
      .attr("title", item.titulo || item.rotulo)
      .html(window.Icones.html(item.icone));
    $("<span>").text(item.rotulo).appendTo($a);
    if (ativo) $a.attr("aria-current", "page");
    return $a;
  }

  function montarGrupo(titulo, itens, extracaoEscolhida) {
    var $grupo = $("<div>").addClass("extracao-nav-grupo");
    $("<div>").addClass("extracao-nav-header").text(titulo).appendTo($grupo);
    itens.forEach(function (item) {
      $grupo.append(montarItem(item, extracaoEscolhida));
    });
    return $grupo;
  }

  /* Troca de aparelho mantendo a tela atual — só faz sentido com mais de uma extração. */
  function montarSeletorExtracao(extracoes) {
    var $bloco = $("<div>").addClass("extracao-nav-grupo");
    $("<div>").addClass("extracao-nav-header").text("Aparelho").appendTo($bloco);

    var $chips = $("<div>").addClass("extracao-nav-chips").appendTo($bloco);

    function chip(rotulo, valor, titulo) {
      var ativo = String(idExtracao || "") === String(valor);
      var params = new URLSearchParams(window.location.search);
      if (valor) params.set("extracao", valor);
      else params.delete("extracao");
      $("<a>")
        .addClass("extracao-nav-chip" + (ativo ? " active" : ""))
        .attr("href", atual + ".html?" + params.toString())
        .attr("title", titulo || rotulo)
        .text(rotulo)
        .appendTo($chips);
    }

    chip("Todos", "", "Ver os dados de todos os aparelhos do inquérito");
    extracoes.forEach(function (e) {
      chip(nomeExtracao(e), e.id_extracao, e.no_arquivo || "");
    });

    return $bloco;
  }

  function montarMenu(extracoes) {
    var extracaoEscolhida = idExtracao || (extracoes.length === 1 ? extracoes[0].id_extracao : "");

    var $dropdown = $("<div>").addClass("extracao-nav-dropdown hidden");

    var itensInquerito = ITENS_INQUERITO.map(function (i) {
      return $.extend({ escopo: "inquerito" }, i);
    });
    $dropdown.append(montarGrupo("Inquérito", itensInquerito, extracaoEscolhida));

    // RIFs e PDFs de inquérito são um por registro importado — entram no grupo do inquérito
    // depois que a busca (preguiçosa, no 1º clique) responde.
    $dropdown.append($("<div>").addClass("extracao-nav-documentos"));

    if (extracoes.length) {
      if (extracoes.length > 1) $dropdown.append(montarSeletorExtracao(extracoes));
      var itensExtracao = ITENS_EXTRACAO.map(function (i) {
        return $.extend({ escopo: "extracao" }, i);
      });
      $dropdown.append(montarGrupo("Extração", itensExtracao, extracaoEscolhida));
    }

    return $dropdown;
  }

  var documentosCarregados = false;

  function carregarDocumentos($dropdown) {
    if (documentosCarregados) return;
    documentosCarregados = true;

    var $destino = $dropdown.find(".extracao-nav-documentos");
    var base = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito);

    $.getJSON(base + "/rifs").done(function (rifs) {
      (rifs || []).forEach(function (r) {
        $destino.append(montarItem({
          icone: "rif",
          rotulo: "RIF " + (r.nu_rif || r.no_pasta || r.id_rif),
          titulo: "RIF " + (r.nu_rif || "") + (r.no_pasta ? " — " + r.no_pasta : ""),
          pagina: "rif",
          escopo: "inquerito",
          idParam: "rif",
          idValor: r.id_rif,
          extra: "&rif=" + encodeURIComponent(r.id_rif)
        }, ""));
      });
    });

    $.getJSON(base + "/extratos-simba").done(function (extratos) {
      (extratos || []).forEach(function (s) {
        $destino.append(montarItem({
          icone: "banco",
          rotulo: "Extrato " + (s.no_arquivo || s.id_extrato),
          titulo: "Extrato bancário do SIMBA — " + (s.no_arquivo || ""),
          pagina: "simba",
          escopo: "inquerito",
          idParam: "simba",
          idValor: s.id_extrato,
          extra: "&simba=" + encodeURIComponent(s.id_extrato)
        }, ""));
      });
    });

    $.getJSON(base + "/pdfs").done(function (pdfs) {
      (pdfs || []).forEach(function (p) {
        $destino.append(montarItem({
          icone: "ipl",
          rotulo: "Inquérito " + (p.nu_ipl || p.no_arquivo),
          titulo: p.no_arquivo || "",
          pagina: "inquerito_pdf",
          escopo: "inquerito",
          idParam: "ipl",
          idValor: p.id_ipl,
          extra: "&ipl=" + encodeURIComponent(p.id_ipl)
        }, ""));
      });
    });
  }

  function montarBotao() {
    var $btn = $("<button>")
      .addClass("extracao-nav-btn")
      .attr("type", "button")
      .attr("aria-haspopup", "true")
      .attr("aria-expanded", "false")
      .attr("title", "Navegar neste inquérito (Alt+N)")
      .html(window.Icones.html("menu"));
    $("<span>").text("Navegar").appendTo($btn);
    $btn.append(window.Icones.html("chevronDown", "extracao-nav-chevron"));
    return $btn;
  }

  // ---------- montagem ----------

  function abrir($container) {
    var $dropdown = $container.find(".extracao-nav-dropdown");
    carregarDocumentos($dropdown);
    $(".extracao-nav-dropdown").not($dropdown).addClass("hidden")
      .siblings(".extracao-nav-btn").attr("aria-expanded", "false");
    var fechado = $dropdown.hasClass("hidden");
    $dropdown.toggleClass("hidden", !fechado);
    $container.find(".extracao-nav-btn").attr("aria-expanded", String(fechado));
  }

  function fecharTodos() {
    $(".extracao-nav-dropdown").addClass("hidden");
    $(".extracao-nav-btn").attr("aria-expanded", "false");
  }

  $(document).on("click", ".extracao-nav-btn", function (e) {
    e.stopPropagation();
    abrir($(this).closest(".extracao-nav-container"));
  });

  $(document).on("click", function (e) {
    if (!$(e.target).closest(".extracao-nav-container").length) fecharTodos();
  });

  $(document).on("keydown", function (e) {
    if (e.key === "Escape") fecharTodos();
    if (e.altKey && (e.key === "n" || e.key === "N")) {
      var $container = $(".extracao-nav-container").first();
      if ($container.length) {
        e.preventDefault();
        abrir($container);
      }
    }
  });

  var extracoesCache = [];
  var ultimoNumero = null;

  function inicializar() {
    if (!idInquerito) return;
    var $containers = $(".extracao-nav-container");
    if (!$containers.length) return;

    $.when(carregarNumeroInquerito(), carregarExtracoes()).done(function (numero, extracoes) {
      ultimoNumero = numero;
      extracoesCache = extracoes || [];
      $containers.each(function () {
        $(this).empty().append(montarBotao()).append(montarMenu(extracoes));
      });
      inserirTrilha(montarTrilha(numero, extracoes));
    });
  }

  /* Deep-link de mensagem (?msg=): o whatsapp.js resolve o aparelho da mensagem depois que a
   * trilha já foi montada e reflete na URL — sem remontar, o nível do aparelho só aparecia no
   * F5. O evento avisa aqui; refaz a trilha com o id novo (dados já estão no cache de sessão). */
  $(document).on("nav:extracao-resolvida", function (e, nova) {
    if (!idInquerito || String(idExtracao) === String(nova || "")) return;
    idExtracao = String(nova);
    $("nav.crumbs").remove();
    inserirTrilha(montarTrilha(ultimoNumero, extracoesCache));
  });

  $(inicializar);
})();

