/* Monta o menu vertical (.sidebar) em páginas hub e menu horizontal (.topnav) nas demais.
 * A lista de seções vem de window.MapaTelas.NAV_ITEMS (js/mapa_telas.js) — único lugar que a
 * conhece; as páginas não têm mais <aside> fixo no HTML, pra evitar 30 cópias divergentes da
 * mesma navegação. Os ícones vêm de window.Icones (js/icones.js).
 *
 * Nas páginas "hub" (listadas em MapaTelas.PAGINAS_MENU_COMPLETO) o menu vem completo (ícone +
 * texto) na vertical (.sidebar). Nas demais (telas de um caso específico: whatsapp, contatos,
 * timeline, mesa de inquérito etc.) o menu vira uma barra horizontal no topo (.topnav) com:
 * - Esquerda: Início (só ícone) | Inquéritos (ícone+texto) | [Inquérito 2025.00001] |
 *   Investigação (ícone+texto) | Nova importação (ícone) | Convergência (ícone) |
 *   Administração (ícone+texto) -> dropdown — esta última só para administradores (ver
 *   "seções de admin" mais abaixo). O item entre colchetes é o inquérito aberto: só aparece
 *   quando a URL traz ?inquerito= (ver "item do inquérito aberto", mais abaixo).
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

  /* ---------- item do inquérito aberto ----------
   *
   * O único item do menu que não é fixo: só existe quando a URL traz ?inquerito=, e fica logo
   * depois de "Inquéritos / casos" — é o mesmo assunto, um nível abaixo. Antes esse "em qual
   * inquérito eu estou" só aparecia na trilha do canto direito, longe do menu e espremido
   * junto do aparelho e do nome da tela (ver js/utils/navegacao_inquerito.js, que ficou só
   * com esses dois).
   *
   * O número vem de /api/investigacao/inqueritos, pelo cache de sessão compartilhado com o menu
   * "Navegar". Com o cache quente o item já nasce escrito; senão nasce escondido e aparece
   * quando a resposta chega — nunca fica um "Inquérito undefined" no meio do menu. Se a busca
   * falhar ou o inquérito não estiver na lista do usuário, ele simplesmente não aparece. */

  var CACHE_INQUERITOS = "nav:inqueritos";

  var idInquerito = new URLSearchParams(window.location.search).get("inquerito") || "";

  function numeroDoInquerito(lista) {
    var achado = null;
    (lista || []).forEach(function (i) {
      if (String(i.id_inquerito) === String(idInquerito)) achado = i;
    });
    return achado && achado.nu_inquerito ? String(achado.nu_inquerito) : "";
  }

  function tituloInquerito(numero) {
    return "Inquérito " + numero + " — abrir a página do inquérito";
  }

  /* O rótulo vem em duas partes porque a palavra "Inquérito" sai sozinha em tela estreita (regra
   * em style.css): o ícone e o número já dizem o que é, e esses ~70px decidem se a barra inteira
   * cabe numa linha só num monitor de 1280. */
  function chipInqueritoHtml(atual, numero) {
    var esc = window.AnalyserUtils.esc;
    var ativo = atual === "investigacao.html";
    return '<a href="investigacao.html?inquerito=' + encodeURIComponent(idInquerito) + '"' +
      ' class="topnav-item topnav-inquerito' + (ativo ? " active" : "") + (numero ? "" : " hidden") + '"' +
      (ativo ? ' aria-current="page"' : "") +
      ' title="' + (numero ? esc(tituloInquerito(numero)) : "") + '">' +
      window.Icones.html("inquerito") +
      '<span class="topnav-inquerito-rotulo">' +
        '<span class="topnav-inquerito-prefixo">Inquérito</span>' +
        '<span class="topnav-inquerito-numero">' + esc(numero) + '</span>' +
      '</span></a>';
  }

  function completarChipInquerito($topnav) {
    window.AnalyserUtils.buscarComCacheSessao(CACHE_INQUERITOS, "/api/investigacao/inqueritos")
      .done(function (lista) {
        var numero = numeroDoInquerito(lista);
        if (!numero) return;
        $topnav.find(".topnav-inquerito")
          .attr("title", tituloInquerito(numero))
          .removeClass("hidden")
          .find(".topnav-inquerito-numero").text(numero);
      });
  }

  function paginaAtual() {
    return window.location.pathname.split("/").pop() || "index.html";
  }

  function montarSidebar() {
    var atual = paginaAtual();
    // Sempre usa topnav horizontal (menu B) em todas as páginas
    return montarTopnav(atual);
  }

  function montarTopnav(atual) {
    // com o cache de sessão quente o item do inquérito já nasce escrito; senão, completarChipInquerito()
    var numeroInquerito = idInquerito ? numeroDoInquerito(window.AnalyserUtils.lerCacheSessao(CACHE_INQUERITOS)) : "";
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
          '<div class="topnav-group' + (item.admin ? " topnav-admin hidden" : "") + '">' +
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

      var ativo = item.href === atual;
      var mostrarTexto = ["inicio", "mais", "inqueritos", "investigacao", "convergencia", "auditoria", "administracao"].indexOf(item.icone) !== -1 || item.href === "inqueritos.html" || item.href === "investigacao.html" || item.href === "convergencia.html" || item.href === "auditoria.html";
      html +=
        '<a href="' + item.href + '" class="topnav-item' + (ativo ? " active" : "") + (item.auditoria ? " topnav-auditoria hidden" : "") + (item.naoAdmin ? " topnav-nao-admin" : "") + '"' +
          (ativo ? ' aria-current="page"' : "") +
          ' title="' + item.label + '" aria-label="' + item.label + '">' +
          window.Icones.html(item.icone) +
          (mostrarTexto ? '<span>' + item.label + '</span>' : '') +
        '</a>';

      // o inquérito aberto entra colado em "Inquéritos / casos", como um nível a mais dele
      if (item.href === "inqueritos.html" && idInquerito) {
        html += chipInqueritoHtml(atual, numeroInquerito);
      }
    });

    html +=
        '</div>' +
        '<div class="topnav-right">' +
          window.Tema.dropdownHtml() +
          '<div class="topnav-user" id="topnav-user">' +
            '<button type="button" class="topnav-user-toggle" aria-expanded="false" aria-haspopup="menu" aria-controls="topnav-user-dropdown" aria-label="Menu do usuário">' +
              '<svg class="topnav-user-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>' +
              '<span class="topnav-user-name">Usuário</span>' +
              '<svg class="topnav-user-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' +
            '</button>' +
            '<div class="topnav-user-dropdown" id="topnav-user-dropdown" role="menu" aria-label="Ações do usuário" hidden>' +
              '<div class="topnav-user-header" aria-hidden="false">' +
                '<div class="topnav-user-header-name">Usuário</div>' +
                '<div class="topnav-user-header-email" hidden></div>' +
                '<span class="topnav-user-badge" hidden></span>' +
              '</div>' +
              '<p class="topnav-user-group-label" aria-hidden="true">Conta</p>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-trocar-senha" role="menuitem">' +
                window.Icones.html("senhas") +
                '<span>Trocar senha</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-meu-email" role="menuitem">' +
                window.Icones.html("email") +
                '<span>Meu e-mail</span>' +
              '</a>' +
              '<p class="topnav-user-group-label" aria-hidden="true">Notificações</p>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-meu-webhook" role="menuitem">' +
                window.Icones.html("compartilhar") +
                '<span>Meu webhook IA</span>' +
              '</a>' +
              '<a href="#" class="topnav-user-dropdown-item" id="topnav-avisos-ia" role="menuitemcheckbox" aria-checked="false">' +
                window.Icones.html("notificacoes") +
                '<span>Avisos da IA</span>' +
                '<span class="topnav-user-item-state" id="topnav-avisos-ia-estado">desligado</span>' +
              '</a>' +
              '<hr class="topnav-user-separator" aria-hidden="true">' +
              '<a href="#" class="topnav-user-dropdown-item topnav-user-dropdown-item-danger" id="topnav-sair" role="menuitem">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>' +
                '<span>Sair</span>' +
              '</a>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</nav>';

    var $topnav = $(html);

    if (idInquerito && !numeroInquerito) completarChipInquerito($topnav);

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

    $(document).on("click", "#topnav-trocar-senha", function (e) {
      e.preventDefault();
      fecharUsuario();
      if (window.AuthUI && window.AuthUI.abrirModalSenha) {
        window.AuthUI.abrirModalSenha();
      }
    });

    $(document).on("click", "#topnav-meu-email", function (e) {
      e.preventDefault();
      fecharUsuario();
      if (window.AuthUI && window.AuthUI.abrirModalEmail) {
        window.AuthUI.abrirModalEmail();
      }
    });

    $(document).on("click", "#topnav-meu-webhook", function (e) {
      e.preventDefault();
      fecharUsuario();
      if (window.AuthUI && window.AuthUI.abrirModalWebhookIa) {
        window.AuthUI.abrirModalWebhookIa();
      }
    });

    /* "Avisos da IA": o toast dentro do sistema já funciona sozinho (js/utils/avisos_ia.js).
     * Este item cuida só da notificação DO NAVEGADOR, que precisa de permissão e de um clique
     * do usuário para ser pedida — e, uma vez concedida, vira um liga/desliga. O rótulo de
     * estado (ligado/desligado) é atualizado por atualizarEstadoAvisosIa() abaixo. */
    function atualizarEstadoAvisosIa() {
      var $item = $("#topnav-avisos-ia");
      var $estado = $("#topnav-avisos-ia-estado");
      if (!$item.length) return;
      var ligado = Boolean(window.AvisosIA && window.AvisosIA.permissao() === "granted" && window.AvisosIA.notificacaoLigada());
      $item.attr("aria-checked", String(ligado));
      if ($estado.length) $estado.text(ligado ? "ligado" : "desligado");
      $item.toggleClass("is-on", ligado);
    }

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
        atualizarEstadoAvisosIa();
        avisar(ligar
          ? "Avisos da IA ligados — você será notificado quando a resposta ficar pronta, mesmo em outra aba."
          : "Notificações do navegador desligadas. Os avisos continuam aparecendo dentro das telas.", "sucesso");
        return;
      }
      window.AvisosIA.pedirPermissao(function (perm) {
        atualizarEstadoAvisosIa();
        if (perm === "granted") {
          avisar("Avisos da IA ligados — você será notificado quando a resposta ficar pronta, mesmo em outra aba.", "sucesso");
        } else {
          avisar("Permissão não concedida. Os avisos continuam aparecendo dentro das telas do sistema.", "aviso");
        }
      });
    });

    $(document).on("click", "#topnav-sair", function (e) {
      e.preventDefault();
      if (window.AuthUI && window.AuthUI.sair) {
        window.AuthUI.sair();
      }
    });

    // ---------- Dropdown do usuário: abrir/fechar centralizados ----------
    // Usa o atributo hidden (em vez de display:none inline) para o estado valer também
    // com CSS/JS desligado de forma parcial e para leitores de tela ignorarem o painel
    // fechado. A exclusão mútua com o dropdown "Temas" (js/utils/theme.js) é por eventos:
    // quem abre dispara topnav:*-abrindo e o outro fecha.
    function usuarioAberto() {
      return !$("#topnav-user-dropdown").prop("hidden");
    }

    function posicionarUsuario($dropdown) {
      $dropdown.css({ left: "", right: "" });
      var rect = $dropdown[0].getBoundingClientRect();
      if (rect.right > window.innerWidth - 8) {
        $dropdown.css({ left: "auto", right: 0 });
      }
      if ($dropdown[0].getBoundingClientRect().left < 8) {
        $dropdown.css({ left: 0, right: "auto" });
      }
    }

    function fecharUsuario(devolverFoco) {
      var $dropdown = $("#topnav-user-dropdown");
      if ($dropdown.prop("hidden")) return;
      $dropdown.prop("hidden", true);
      $(".topnav-user-toggle").attr("aria-expanded", "false");
      if (devolverFoco) $(".topnav-user-toggle").trigger("focus");
    }

    function abrirUsuario() {
      if (window.Tema && window.Tema.fechar) window.Tema.fechar();
      atualizarEstadoAvisosIa();
      var $dropdown = $("#topnav-user-dropdown");
      $dropdown.prop("hidden", false);
      posicionarUsuario($dropdown);
      $(".topnav-user-toggle").attr("aria-expanded", "true");
      $(document).trigger("topnav:usuario-abrindo");
    }

    // User dropdown toggle
    $(document).on("click", ".topnav-user-toggle", function (e) {
      e.stopPropagation();
      if (usuarioAberto()) {
        fecharUsuario(false);
      } else {
        abrirUsuario();
      }
    });

    // Close dropdown on outside click
    $(document).on("click.topnavuser", function (e) {
      if (!$(e.target).closest(".topnav-user").length) {
        fecharUsuario(false);
      }
    });

    // Tema aberto por js/utils/theme.js fecha o menu do usuário.
    $(document).on("topnav:tema-abrindo", function () {
      fecharUsuario(false);
    });

    // Teclado: Escape fecha (devolvendo o foco); setas navegam entre as ações.
    $(document).on("keydown.topnavuser", function (e) {
      if (!usuarioAberto()) return;
      var $itens = $("#topnav-user-dropdown .topnav-user-dropdown-item");
      if (e.key === "Escape") {
        e.preventDefault();
        fecharUsuario(true);
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
      // Só assume a navegação quando o foco já está dentro do menu do usuário.
      if (!$(e.target).closest(".topnav-user").length) return;
      e.preventDefault();
      var pos = $itens.index($(document.activeElement).closest(".topnav-user-dropdown-item"));
      if (e.key === "Home") pos = 0;
      else if (e.key === "End") pos = $itens.length - 1;
      else if (e.key === "ArrowDown") pos = pos < $itens.length - 1 ? pos + 1 : 0;
      else if (e.key === "ArrowUp") pos = pos > 0 ? pos - 1 : $itens.length - 1;
      if ($itens[pos]) $itens[pos].focus();
    });

    // O dropdown "Temas" (markup, troca e fechamento) é todo de js/utils/theme.js

    // Nome do usuário e seções de admin, quando js/utils/auth.js confirma quem entrou.
    //
    // Seções de admin: os itens marcados com admin:true no MapaTelas nascem com .hidden e só
    // aparecem aqui, se /api/auth/eu devolver in_admin. Nascer escondido (em vez de montar e
    // depois tirar) evita o flash de "Administração" para quem não pode abri-la — o menu é
    // montado de forma síncrona, muito antes da resposta do /eu. Sem sessão o evento nunca
    // chega e a seção fica de fora, que é o lado seguro do erro. É só UX: as rotas destas
    // telas exigem admin no backend (exigir_admin, em api/funcoes/auth_utils.py).
    $(document).on("analyser:usuario", function (e, usuario) {
      if (usuario && usuario.no_usuario) {
        $(".topnav-user-name").text(usuario.no_usuario);
        $(".topnav-user-header-name").text(usuario.no_usuario).attr("title", usuario.no_usuario);
        $(".topnav-user-toggle").attr("aria-label", "Menu do usuário: " + usuario.no_usuario);
      }
      // Cabeçalho do dropdown: e-mail (recuperação de conta) + papel, quando houver.
      var $email = $(".topnav-user-header-email");
      if (usuario && usuario.no_email) {
        $email.text(usuario.no_email).attr("title", usuario.no_email).prop("hidden", false);
      } else {
        $email.empty().prop("hidden", true);
      }
      var $badge = $(".topnav-user-badge");
      var papel = usuario && usuario.in_admin ? "Admin" : (usuario && usuario.pode_auditoria ? "Auditoria" : "");
      if (papel) {
        $badge.text(papel).prop("hidden", false);
      } else {
        $badge.empty().prop("hidden", true);
      }
      atualizarEstadoAvisosIa();
      if (usuario && usuario.in_admin) {
        $topnav.find(".topnav-admin").removeClass("hidden");
        $topnav.find(".topnav-nao-admin").remove();
      } else {
        $topnav.find(".topnav-admin").remove();
        $topnav.find(".topnav-nao-admin").removeClass("hidden");
      }
      // Auditoria fica ao lado da Convergência e aparece para auditoria/administrador
      // (pode_auditoria), não só para administradores — ver MapaTelas.NAV_ITEMS.
      if (usuario && usuario.pode_auditoria) {
        $topnav.find(".topnav-auditoria").removeClass("hidden");
      } else {
        $topnav.find(".topnav-auditoria").remove();
      }
    });

    return $topnav;
  }

  /* ---------- Ctrl+K: palette de busca rápida (temas clássicos) ----------
   *
   * É a versão leve da palette da Orbit (js/utils/navigation_orbit.js) para as telas
   * clássicas: abre com Ctrl/Cmd+K (ou "/") e junta telas (MapaTelas.TELAS_BUSCA, com os
   * mesmos filtros de permissão do Início), inquéritos (cache de sessão compartilhado) e —
   * quando o texto não é nome de tela/inquérito — um atalho para a Busca de evidências
   * (busca_conteudo.html), que procura DENTRO do material do inquérito com o motor híbrido.
   * No Orbit a palette própria já cuida do Ctrl+K, então aqui o atalho fica de fora. */

  function montarPalette() {
    function temaOrbit() {
      return document.documentElement.getAttribute("data-theme") === "orbit";
    }

    function norm(v) {
      return String(v || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }

    var permissoes = { admin: false, auditoria: false };
    var inqueritos = null;

    var $dialog = $('<dialog class="palette-busca" aria-label="Busca rápida">' +
      '<div class="palette-cabecalho">' +
        '<input type="search" class="palette-campo" autocomplete="off" ' +
          'placeholder="Telas, inquéritos ou evidências…" aria-label="Buscar telas, inquéritos ou evidências">' +
        '<button type="button" class="palette-fechar" aria-label="Fechar">×</button>' +
      '</div>' +
      '<div class="palette-resultados" role="status" aria-live="polite"></div>' +
      '<p class="palette-dica">↑↓ navega · Enter abre · Esc fecha</p>' +
    '</dialog>').appendTo(document.body);

    var $campo = $dialog.find(".palette-campo");
    var $resultados = $dialog.find(".palette-resultados");
    var origemFoco = null;

    $(document).on("analyser:usuario.palette", function (e, usuario) {
      if (!usuario) return;
      permissoes.admin = !!usuario.in_admin;
      permissoes.auditoria = !!usuario.pode_auditoria;
      if ($dialog[0].open) render();
    });

    function carregarInqueritos() {
      if (inqueritos) return;
      window.AnalyserUtils.buscarComCacheSessao(CACHE_INQUERITOS, "/api/investigacao/inqueritos")
        .done(function (lista) {
          inqueritos = lista || [];
          if ($dialog[0].open) render();
        })
        .fail(function () { inqueritos = []; });
    }

    function linha(href, titulo, subtitulo) {
      var esc = window.AnalyserUtils.esc;
      return '<a class="palette-linha" href="' + esc(href) + '">' +
        '<strong>' + esc(titulo) + '</strong>' +
        (subtitulo ? '<small>' + esc(subtitulo) + '</small>' : "") + '</a>';
    }

    function render() {
      var termo = norm($campo.val().trim());
      var html = "";

      var telas = window.MapaTelas.TELAS_BUSCA.filter(function (tela) {
        if (tela.admin && !permissoes.admin) return false;
        if (tela.auditoria && !permissoes.auditoria) return false;
        return !termo || norm(tela.label + " " + (tela.termos || "") + " " + (tela.desc || "")).indexOf(termo) !== -1;
      });
      telas.slice(0, 8).forEach(function (tela) {
        html += linha(tela.href, tela.label, tela.desc);
      });

      if (inqueritos === null) carregarInqueritos();
      var achados = (inqueritos || []).filter(function (inq) {
        return !termo || norm(inq.nu_inquerito).indexOf(termo) !== -1;
      }).slice(0, 8);
      achados.forEach(function (inq) {
        html += linha("investigacao.html?inquerito=" + encodeURIComponent(inq.id_inquerito),
          "Inquérito " + (inq.nu_inquerito || inq.id_inquerito), "abrir o inquérito");
      });

      // texto livre vira busca de conteúdo — dentro do inquérito aberto, se houver
      var texto = $campo.val().trim();
      if (texto.length >= 3) {
        var qs = "q=" + encodeURIComponent(texto);
        if (idInquerito) qs += "&inquerito=" + encodeURIComponent(idInquerito);
        html += linha("busca_conteudo.html?" + qs,
          'Buscar evidências por "' + texto + '"', "mensagens, documentos e transcrições (busca híbrida)");
      }

      if (!html) html = '<p class="palette-vazio">Nada corresponde a "' + window.AnalyserUtils.esc(texto) + '".</p>';
      $resultados.html(html);
    }

    function abrir() {
      origemFoco = document.activeElement;
      $campo.val("");
      render();
      if (!$dialog[0].open) $dialog[0].showModal();
      $campo.trigger("focus");
    }

    $campo.on("input", render);

    $campo.on("keydown", function (e) {
      var $linhas = $resultados.find("a.palette-linha");
      if (e.key === "ArrowDown") { e.preventDefault(); $linhas.first().trigger("focus"); }
      else if (e.key === "ArrowUp") { e.preventDefault(); $linhas.last().trigger("focus"); }
      else if (e.key === "Enter") {
        // Enter no campo abre o primeiro resultado (a palette é de atalho, não de formulário)
        var primeiro = $linhas.first();
        if (primeiro.length) { e.preventDefault(); primeiro[0].click(); }
      }
    });

    $resultados.on("keydown", "a.palette-linha", function (e) {
      var $linhas = $resultados.find("a.palette-linha");
      var pos = $linhas.index(this);
      if (e.key === "ArrowDown") { e.preventDefault(); $linhas.eq(Math.min(pos + 1, $linhas.length - 1)).trigger("focus"); }
      else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (pos <= 0) $campo.trigger("focus"); else $linhas.eq(pos - 1).trigger("focus");
      }
    });

    $dialog.find(".palette-fechar").on("click", function () { $dialog[0].close(); });
    $dialog.on("close", function () {
      if (origemFoco && origemFoco.isConnected) { try { origemFoco.focus(); } catch (e) { /* sem foco */ } }
    });
    $dialog.on("click", function (e) {
      if (e.target !== $dialog[0]) return;
      var r = $dialog[0].getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) $dialog[0].close();
    });

    document.addEventListener("keydown", function (e) {
      if (temaOrbit()) return;   // a Orbit tem a própria palette (navigation_orbit.js)
      var tecla = (e.key || "").toLowerCase();
      var ehCtrlK = (e.ctrlKey || e.metaKey) && tecla === "k";
      var tag = (document.activeElement && document.activeElement.tagName) || "";
      var digitando = /^(INPUT|TEXTAREA|SELECT)$/.test(tag) ||
        (document.activeElement && document.activeElement.isContentEditable);
      var ehBarra = tecla === "/" && !digitando && !e.ctrlKey && !e.metaKey;
      if (!ehCtrlK && !ehBarra) return;
      // não roubar o atalho de quem já está num diálogo/modal aberto
      if (document.querySelector("dialog[open], .modal-overlay")) return;
      e.preventDefault();
      abrir();
    }, true);
  }

  montarPalette();

  function montarSkipLink() {
    return $('<a href="#conteudo-principal" class="skip-link">Pular para o conteúdo</a>');
  }

  function marcarConteudoPrincipal() {
    var $conteudo = $(".content").first();
    if ($conteudo.length) {
      if (!$conteudo.attr("id")) $conteudo.attr("id", "conteudo-principal");
      if (!$conteudo.attr("role")) $conteudo.attr("role", "main");
    }
  }

  function inserirSidebar($element) {
    var $app = $(".app");
    var $waApp = $(".wa-app");
    var $chatApp = $(".chat-app");
    var $docApp = $(".doc-app");
    var isCustomLayout = $waApp.length || $chatApp.length || $docApp.length;

    if ($app.length) {
      if ($element.hasClass("topnav")) {
        // (7) topnav como irmão de .app, não dentro de .main — landmark <nav> fora de <main>
        $app.before($element);
        $("body").addClass("has-topnav");
      } else {
        $app.prepend($element);
        $app.children(".main").css("width", "");
      }
    } else if (isCustomLayout) {
      // Páginas com layout próprio (whatsapp, ia, mensageiros) — injeta topnav DENTRO do container
      var $container = $waApp.length ? $waApp : ($chatApp.length ? $chatApp : $docApp);
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
    if (!$(".skip-link").length) $("body").prepend(montarSkipLink());
    marcarConteudoPrincipal();
  }

  inserirSidebar(montarSidebar());
})();
