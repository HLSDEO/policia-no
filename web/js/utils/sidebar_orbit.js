/* Monta o menu vertical (.sidebar) em páginas hub e menu horizontal (.topnav) nas demais.
 * A lista de seções vem de window.MapaTelas.NAV_ITEMS (js/mapa_telas.js) — único lugar que a
 * conhece; as páginas não têm mais <aside> fixo no HTML, pra evitar 30 cópias divergentes da
 * mesma navegação. Os ícones vêm de window.Icones (js/icones.js).
 *
 * Nas páginas "hub" (listadas em MapaTelas.PAGINAS_MENU_COMPLETO) o menu vem completo (ícone +
 * texto) na vertical (.sidebar). Nas demais (telas de um caso específico: whatsapp, contatos,
 * timeline, mesa de inquérito etc.) o menu vira uma barra horizontal no topo (.topnav) com:
 * - Esquerda: Início (só ícone) | Inquéritos (ícone+texto) | Investigação (ícone+texto) |
 *   Nova importação (ícone) | Convergência (ícone) | Administração (ícone+texto) -> dropdown
 * - Direita: Temas (dropdown montado por js/utils/theme.js), depois o menu do usuário
 *   (Trocar senha, Meu e-mail, Sair)
 *
 * Também injeta o skip-link ("Pular para o conteúdo") como primeiro elemento focável da página,
 * e marca `.content` com id="conteudo-principal" + role="main" — landmark de acessibilidade,
 * sem exigir edição em cada HTML.
 *
 * Roda antes de js/auth.js, que completa o rodapé (.sidebar-footer) com o usuário logado. */
(function () {
  "use strict";

  var NAV_ITEMS = window.MapaTelas.NAV_ITEMS;

  var ICON_KEY ='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>';
  var ICON_LOGOUT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
  var ICON_MAIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 7l10 6 10-6"/></svg>';

  function paginaAtual() {
    var pagina = window.location.pathname.split("/").pop() || "index.html";
    if (pagina === "index_orbit.html") return "index.html";
    if (pagina === "inqueritos_orbit.html") return "inqueritos.html";
    if (pagina === "inquerito_orbit.html") return "inquerito.html";
    return pagina;
  }

  function itemPrincipalAtivo(item, atual) {
    if (item.href === "inqueritos.html") {
      return ["inqueritos.html", "inquerito.html", "investigacao.html", "mesa_inquerito.html"].indexOf(atual) !== -1;
    }
    if (item.href === "ia.html") {
      return atual === "ia.html";
    }
    return item.href === atual;
  }

  function montarSidebar() {
    var atual = paginaAtual();
    // Sempre usa topnav horizontal (menu B) em todas as páginas
    return montarTopnav(atual);
  }

  function montarTopnav(atual) {
    var html =
      '<nav class="topnav" role="navigation" aria-label="Navegação principal">' +
        '<div class="topnav-left">' +
          '<a href="index.html" class="topnav-brand" title="Início" aria-label="Início">' +
            '<span class="brand-mark"><img src="img/logo-mark.png" alt="PolícIA"></span>' +
            '<span class="brand-name"><span class="brand-polic">Políc</span><span class="brand-ia">IA</span></span>' +
          '</a>';
    
    NAV_ITEMS.forEach(function (item) {
      if (item.grupo) {
        var temFilhoAtivo = item.itens.some(function (filho) { return filho.href === atual; });
        var idGrupo = "topnav-group-" + item.grupo.toLowerCase().replace(/\s+/g, "-");
        html +=
          '<div class="topnav-group' + (item.admin ? ' topnav-admin hidden' : '') + '">' +
            '<button type="button" class="topnav-group-toggle"' +
              ' aria-expanded="' + (temFilhoAtivo ? "true" : "false") + '"' +
              ' aria-controls="' + idGrupo + '"' +
              ' title="' + item.grupo + '">' +
              window.Icones.html(item.icone) +
              '<span>' + item.grupo + '</span>' +
              window.Icones.html("chevronDown", "topnav-group-chevron") +
            '</button>' +
            '<div class="topnav-group-dropdown" id="' + idGrupo + '" role="menu">';
        item.itens.forEach(function (filho) {
          var ativoFilho = filho.href === atual;
          html +=
            '<a href="' + filho.href + '" class="topnav-dropdown-item' + (ativoFilho ? " active" : "") + '"' +
              ' role="menuitem"' +
              (ativoFilho ? ' aria-current="page"' : "") +
              ' title="' + filho.label + '">' +
              window.Icones.html(filho.icone) +
              '<span>' + filho.label + '</span></a>';
        });
        html += '</div></div>';
        return;
      }

      var ativo = itemPrincipalAtivo(item, atual);
      var mostrarTexto = ["inicio", "mais", "inqueritos", "investigacao", "convergencia", "auditoria", "administracao", "ia"].indexOf(item.icone) !== -1 || item.href === "inqueritos.html" || item.href === "ia.html" || item.href === "convergencia.html" || item.href === "auditoria.html";
      html +=
        '<a href="' + item.href + '" class="topnav-item' + (ativo ? " active" : "") + (item.auditoria ? " topnav-auditoria hidden" : "") + (item.naoAdmin ? " topnav-nao-admin" : "") + '"' +
          (ativo ? ' aria-current="page"' : "") +
          ' title="' + item.label + '" aria-label="' + item.label + '">' +
          window.Icones.html(item.icone) +
          (mostrarTexto ? '<span>' + item.label + '</span>' : '') +
        '</a>';
    });

    html +=
        '</div>' +
        '<div class="topnav-right">' +
          window.Tema.dropdownHtml() +
          '<div class="topnav-user" id="topnav-user">' +
            '<button type="button" class="topnav-user-toggle" aria-expanded="false" aria-controls="topnav-user-dropdown" aria-label="Menu do usuário">' +
              '<svg class="topnav-user-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>' +
              '<span class="topnav-user-name">Usuário</span>' +
              '<svg class="topnav-user-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' +
            '</button>' +
            '<div class="topnav-user-dropdown" id="topnav-user-dropdown" role="menu" style="display: none;">' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-trocar-senha" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>' +
                '<span>Trocar senha</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-meu-email" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 7l10 6 10-6"/></svg>' +
                '<span>Meu e-mail</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-meu-webhook" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' +
                '<span>Meu webhook IA</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-avisos-ia" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>' +
                '<span>Avisos da IA</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-sair" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>' +
                '<span>Sair</span>' +
              '</a>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</nav>';

    var $topnav = $(html);

    $topnav.find(".topnav-group-toggle").on("click", function (e) {
      e.stopPropagation();
      var $grupo = $(this).closest(".topnav-group");
      var abrindo = !$grupo.hasClass("topnav-group-open");
      $grupo.toggleClass("topnav-group-open", abrindo);
      $(this).attr("aria-expanded", String(abrindo));
    });

    $(document).on("click.topnav", function (e) {
      if (!$(e.target).closest(".topnav-group").length) {
        $topnav.find(".topnav-group").removeClass("topnav-group-open");
        $topnav.find(".topnav-group-toggle").attr("aria-expanded", "false");
      }
    });

    $(document).on("click", "#topnav-trocar-senha", function () {
      if (window.AuthUI && window.AuthUI.abrirModalSenha) {
        window.AuthUI.abrirModalSenha();
      }
    });

    $(document).on("click", "#topnav-meu-email", function () {
      if (window.AuthUI && window.AuthUI.abrirModalEmail) {
        window.AuthUI.abrirModalEmail();
      }
    });

    $(document).on("click", "#topnav-meu-webhook", function (e) {
      e.preventDefault();
      if (window.AuthUI && window.AuthUI.abrirModalWebhookIa) {
        window.AuthUI.abrirModalWebhookIa();
      }
    });

    /* "Avisos da IA": o toast dentro do sistema já funciona sozinho (js/utils/avisos_ia.js).
     * Este item cuida só da notificação DO NAVEGADOR, que precisa de permissão e de um clique
     * do usuário para ser pedida — e, uma vez concedida, vira um liga/desliga. */
    $(document).on("click", "#topnav-avisos-ia", function (e) {
      e.preventDefault();
      if (!window.AvisosIA) return;
      var avisar = function (msg, tipo) {
        if (window.Toast) window.Toast[tipo || "info"](msg);
      };
      var permissao = window.AvisosIA.permissao();
      if (permissao === "indisponivel") {
        avisar("Este navegador não suporta notificações do sistema. Os avisos continuam aparecendo dentro das telas.", "aviso");
        return;
      }
      if (permissao === "denied") {
        avisar("As notificações estão bloqueadas para este site no navegador. Libere no cadeado da barra de endereço para recebê-las fora da aba.", "aviso");
        return;
      }
      if (permissao === "granted") {
        var ligar = !window.AvisosIA.notificacaoLigada();
        window.AvisosIA.definirNotificacao(ligar);
        avisar(ligar
          ? "Avisos da IA ligados — você será notificado quando a resposta ficar pronta, mesmo em outra aba."
          : "Notificações do navegador desligadas. Os avisos continuam aparecendo dentro das telas.", "sucesso");
        return;
      }
      window.AvisosIA.pedirPermissao(function (perm) {
        if (perm === "granted") {
          avisar("Avisos da IA ligados — você será notificado quando a resposta ficar pronta, mesmo em outra aba.", "sucesso");
        } else {
          avisar("Permissão não concedida. Os avisos continuam aparecendo dentro das telas do sistema.", "aviso");
        }
      });
    });

    $(document).on("click", "#topnav-sair", function () {
      if (window.AuthUI && window.AuthUI.sair) {
        window.AuthUI.sair();
      }
    });

    // User dropdown toggle
    $(document).on("click", ".topnav-user-toggle", function (e) {
      e.stopPropagation();
      var $dropdown = $("#topnav-user-dropdown");
      var abrindo = $dropdown.is(":hidden");
      $dropdown.toggle(abrindo);
      $(this).attr("aria-expanded", String(abrindo));
    });

    // Close dropdown on outside click
    $(document).on("click.topnavuser", function (e) {
      if (!$(e.target).closest(".topnav-user").length) {
        $("#topnav-user-dropdown").hide();
        $(".topnav-user-toggle").attr("aria-expanded", "false");
      }
    });

    // O dropdown "Temas" (markup, troca e fechamento) é todo de js/utils/theme.js

    // Update user name when auth completes
    $(document).on("analyser:usuario", function (e, usuario) {
      if (usuario && usuario.no_usuario) {
        $(".topnav-user-name").text(usuario.no_usuario);
      }
      // O backend continua sendo a autoridade das permissões. O menu apenas evita mostrar ações
      // administrativas antes de /api/auth/eu confirmar que a pessoa é administradora.
      if (usuario && usuario.in_admin) {
        $topnav.find(".topnav-admin").removeClass("hidden");
        $topnav.find(".topnav-nao-admin").remove();
      } else {
        $topnav.find(".topnav-admin").remove();
        $topnav.find(".topnav-nao-admin").removeClass("hidden");
      }
      // Auditoria fica ao lado da Convergência e aparece para auditoria/administrador.
      if (usuario && usuario.pode_auditoria) {
        $topnav.find(".topnav-auditoria").removeClass("hidden");
      } else {
        $topnav.find(".topnav-auditoria").remove();
      }
    });

    return $topnav;
  }

  function montarSkipLink() {
    return $('<a href="#conteudo-principal" class="skip-link">Pular para o conteúdo</a>');
  }

  function marcarConteudoPrincipal() {
    var $conteudo = $(".content").first();
    if ($conteudo.length) {
      $conteudo.attr("id", "conteudo-principal");
      if (!$conteudo.attr("role")) $conteudo.attr("role", "main");
    }
  }

  function inserirSidebar($element) {
    var $app = $(".app");
    var $waApp = $(".wa-app");
    var $chatApp = $(".chat-app");
    var isCustomLayout = $waApp.length || $chatApp.length;
    
    if ($app.length) {
      if ($element.hasClass("topnav")) {
        $app.find(".main").prepend($element);
        $app.children(".main").css("width", "");
        $("body").addClass("has-topnav");
      } else {
        $app.prepend($element);
        $app.children(".main").css("width", "");
      }
    } else if (isCustomLayout) {
      // Páginas com layout próprio (whatsapp, ia, mensageiros) — injeta topnav DENTRO do container
      var $container = $waApp.length ? $waApp : $chatApp;
      if ($element.hasClass("topnav")) {
        // Envolve os filhos existentes (sidebar + main) num wrapper para manter layout horizontal
        var $children = $container.children().not(".topnav");
        if ($children.length) {
          $children.wrapAll('<div class="wa-content-wrapper" style="display:flex;flex:1;min-height:0;"></div>');
        }
        $container.prepend($element);
        $container.addClass("has-topnav");
        $("body").addClass("has-topnav");
      } else {
        // sidebar vertical (fallback)
        $("body").addClass("has-injected-sidebar").prepend($element);
      }
    } else {
      if ($element.hasClass("topnav")) {
        $("body").addClass("has-injected-sidebar has-topnav").prepend($element);
      } else {
        $("body").addClass("has-injected-sidebar").prepend($element);
      }
    }
    $("body").prepend(montarSkipLink());
    marcarConteudoPrincipal();
  }

  inserirSidebar(montarSidebar());
})();

