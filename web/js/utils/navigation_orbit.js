/* Orbit navigation. Preferences and item IDs are local and isolated per signed-in user.
 * All item lists come from the existing, permission-filtered API; no account API writes. */
(function () {
  "use strict";
  var user = null, items = [], favorites = [], recent = [], loaded = false, failed = false;
  var mode = "top", collapsed = false, view = "search", originFocus = null;
  var $top = $(".topnav").first(), $account = $top.find("#topnav-user");
  if (!$top.length) return;
  $top.find('.topnav-item[href="importacao.html"]').attr("href", "inqueritos_orbit.html?nova_importacao=1").addClass("orbit-nova-importacao").attr("title", "Nova Importação").attr("aria-label", "Nova Importação").find("span").last().text("Nova Importação");
  var $accountAnchor = $("<span hidden>").insertBefore($account);
  function active() { return document.documentElement.getAttribute("data-theme") === "orbit"; }
  function key(name) { return "orbit-" + name + "-" + user.id_usuario; }
  function read(name, fallback) { try { var v = JSON.parse(localStorage.getItem(key(name))); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function save(name, value) { if (user) { try { localStorage.setItem(key(name), JSON.stringify(value)); } catch (e) { /* private browsing */ } } }
  function norm(value) { return String(value || "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase(); }
  function icon(name) {
    if (name === "star") return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9Z"/></svg>';
    return window.Icones.html(name);
  }
  function menuIcon(href, fallback) {
    var item = (window.MapaTelas.NAV_ITEMS || []).find(function (i) { return i.href === href; });
    return item ? item.icone : fallback;
  }
  function nav(label, name, action, href) {
    var $el = $(href ? "<a>" : "<button type='button'>").addClass("orbit-nav-item").attr({title: label, "aria-label": label});
    if (href) $el.attr("href", href); else $el.on("click", function () { open(action); });
    return $el.html(icon(name)).append($("<span class='orbit-nav-label'>").text(label));
  }
  var $themes = $top.find("#topnav-theme");
  var $layoutControl = $("<div class='orbit-layout-control'>");
  var $layoutSwitch = $("<button type='button' class='orbit-layout-switch' role='switch' aria-label='Menu lateral' aria-checked='false'><span class='orbit-layout-track' aria-hidden='true'><span class='orbit-layout-thumb'></span></span></button>").appendTo($layoutControl);
  $layoutSwitch.before("<span class='orbit-layout-caption' data-mode='top'>Superior</span>");
  $layoutSwitch.after("<span class='orbit-layout-caption' data-mode='side'>Lateral</span>");
  $layoutControl.insertBefore($themes);
  var $layoutAnchor = $("<span hidden>").insertBefore($layoutControl);
  var $themesAnchor = $("<span hidden>").insertBefore($themes);
  var $side = $("<aside class='orbit-sidebar' aria-label='Navegação Orbit' hidden>");
  var $brandMark = $top.find(".topnav-brand .brand-mark").first().clone().addClass("orbit-brand-mark");
  var $brand = $("<button type='button' class='orbit-brand' aria-label='Recolher menu lateral' aria-expanded='true'>")
    .append($brandMark)
    .append("<span class='orbit-nav-label'>Políc<span class='orbit-brand-ia'>IA</span></span>");
  $side.append($brand);
  var detailParams = new URLSearchParams(location.search);
  var detailPage = location.pathname.split("/").pop();
  var activeInquiry = detailParams.get("inquerito");
  var isInquiryDetail = ["inquerito_orbit.html", "investigacao.html"].indexOf(detailPage) !== -1 && !!activeInquiry && !detailParams.get("caso");
  var hasActiveInquiry = false;
  var $inquiryControl = $("<div class='orbit-active-inquiry'>").hide()
    .append(icon("inquerito")).append("<span class='orbit-inquiry-label'>Inquérito</span>");
  var $inquirySelect = $("<select aria-label='Inquérito ativo'>").appendTo($inquiryControl);
  function formatarNumeroInq(nu) {
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
  function renderInquiry(list) {
    if (!isInquiryDetail) return;
    var current = (list || []).find(function (i) { return String(i.id_inquerito) === activeInquiry; });
    hasActiveInquiry = !!current;
    $inquirySelect.empty();
    if (current) {
      (list || []).forEach(function (i) {
        var numFmt = formatarNumeroInq(i.nu_inquerito) || ("Inquérito " + i.id_inquerito);
        $("<option>").val(String(i.id_inquerito)).text(numFmt).appendTo($inquirySelect);
      });
      $inquirySelect.val(activeInquiry);
      $inquiryControl.attr("title", "Inquérito ativo: " + (formatarNumeroInq(current.nu_inquerito) || activeInquiry));
    }
    apply();
  }
  $inquirySelect.on("change", function () {
    if (!this.value || this.value === activeInquiry) return;
    var params = new URLSearchParams();
    params.set("inquerito", this.value);
    if (detailPage === "investigacao.html") params.set("tema_rota", "ignorar");
    location.href = detailPage + "?" + params.toString();
  });

  var $scroll = $("<nav class='orbit-sidebar-scroll' aria-label='Principal'>").appendTo($side);
  $scroll.append(nav("Início", menuIcon("index.html", "inicio"), null, "index_orbit.html"));
  var $navPesquisa = nav("Pesquisa", "investigacao", "search").append("<kbd class='orbit-nav-label'>Ctrl K</kbd>");
  $scroll.append($navPesquisa);
  var $navConvergencia = nav("Convergência", menuIcon("convergencia.html", "convergencia"), null, "convergencia.html");
  $scroll.append($navConvergencia);
  var $auditoria = nav("Auditoria", menuIcon("auditoria.html", "auditoria"), null, "auditoria.html").hide().appendTo($scroll);
  var $headingInqueritos = $("<p class='orbit-nav-heading orbit-nav-label'>Inquéritos</p>");
  $scroll.append($headingInqueritos);
  var $all = nav("Todos os Inquéritos", menuIcon("inqueritos.html", "inqueritos"), null, "inqueritos_orbit.html").hide().appendTo($scroll);
  var $navNovaImportacao = nav("Nova Importação", menuIcon("importacao.html", "mais"), null, "inqueritos_orbit.html?nova_importacao=1").addClass("orbit-nova-importacao");
  $scroll.append($navNovaImportacao);
  var $navFavoritos = nav("Favoritos", "star", "favorites");
  $scroll.append($navFavoritos);
  var $navMeusInqueritos = nav("Meus Inquéritos", "dono", "mine");
  $scroll.append($navMeusInqueritos);
  // Use the same administration entries and permission flag as the upper menu.
  var $more = $("<details class='orbit-more orbit-admin'><summary title='Administração' aria-label='Administração'>" + icon("admin") + "<span class='orbit-nav-label'>Administração</span></summary></details>").hide();
  var $adminLinks = $("<div class='orbit-admin-links'>").appendTo($more);
  $top.find(".topnav-admin a").each(function () {
    $adminLinks.append($(this).clone().removeClass("topnav-dropdown-item").removeAttr("role").addClass("orbit-extra-link"));
  });
  $more.find("summary").on("click", function (e) {
    if (collapsed) {
      e.preventDefault();
      collapsed = false;
      save("collapsed", false);
      apply();
      $more.prop("open", true);
    }
  });
  var $headingRecentes = $("<p class='orbit-nav-heading orbit-nav-label'>Recentes</p>");
  var $recents = $("<div class='orbit-recents'>");
  $scroll.append($more).append($headingRecentes).append($recents);
  var $footer = $("<footer class='orbit-sidebar-footer'>").appendTo($side);
  $(document.body).append($side);
  var $dialog = $("<dialog class='orbit-search' aria-labelledby='orbit-search-title'><div class='orbit-search-head'><h2 id='orbit-search-title'>Pesquisa</h2><button type='button' class='orbit-search-close' aria-label='Fechar'>×</button></div><input type='search' class='orbit-search-input' aria-label='Pesquisar inquéritos e telas' placeholder='Pesquisar inquéritos e telas…'><div class='orbit-search-status' role='status' aria-live='polite'></div><div class='orbit-search-results'></div></dialog>").appendTo(document.body);
  var $input = $dialog.find("input"), $results = $dialog.find(".orbit-search-results"), $status = $dialog.find(".orbit-search-status");
  function apply() {
    var side = active() && mode === "side";
    $layoutControl.toggle(active()).attr("data-layout", mode);
    $layoutSwitch.attr({"aria-checked": String(mode === "side"), title: mode === "side" ? "Menu lateral — clique para usar o superior" : "Menu superior — clique para usar o lateral"});
    $(document.body).toggleClass("orbit-side", side).toggleClass("orbit-side-collapsed", side && collapsed);
    $side.prop("hidden", !side);
    $inquiryControl.toggle(active() && hasActiveInquiry && (!user || !user.in_admin));
    if (side) $brand.after($inquiryControl); else $top.find(".topnav-left").append($inquiryControl);
    $brand.attr({"aria-expanded": String(!collapsed), "aria-label": collapsed ? "Expandir menu lateral" : "Recolher menu lateral", title: collapsed ? "Expandir menu lateral" : "Recolher menu lateral"});
    if (side) {
      $footer.append($layoutControl, $themes, $account);
    } else {
      $layoutAnchor.after($layoutControl);
      $themesAnchor.after($themes);
      $accountAnchor.after($account);
    }
    $themes.find(".topnav-theme-dropdown").hide().css({left:"", right:""});
    $themes.find(".topnav-theme-toggle").attr("aria-expanded", "false");
    $account.find(".topnav-user-dropdown").hide();
    $account.find(".topnav-user-toggle").attr("aria-expanded", "false");
    if (!active() && $dialog[0].open) $dialog[0].close();
  }
  $layoutSwitch.on("click", function () {
    mode = mode === "side" ? "top" : "side";
    save("layout", mode); apply(); this.focus();
  });
  $brand.on("click", function () { collapsed = !collapsed; save("collapsed", collapsed); apply(); });
  new MutationObserver(apply).observe(document.documentElement, {attributes:true, attributeFilter:["data-theme"]});
  function renderRecent() {
    $recents.empty();
    recent.slice(0, 6).forEach(function (id) {
      var item = items.find(function (i) { return i.key === id; });
      if (item) $recents.append(nav(item.title, "inquerito", null, item.href));
    });
    if (!$recents.children().length) $recents.append($("<p class='orbit-nav-empty orbit-nav-label'>").text(loaded ? "Nenhum acesso recente." : "Carregando…"));
  }
  function recordCurrent() {
    var p = new URLSearchParams(location.search);
    var id = p.get("caso") ? "caso:" + p.get("caso") : p.get("inquerito") ? "inquerito:" + p.get("inquerito") : null;
    if (id && items.some(function (i) { return i.key === id; })) { recent = [id].concat(recent.filter(function (v) { return v !== id; })).slice(0, 12); save("recent", recent); }
    renderRecent();
  }
  function star(item) {
    var yes = favorites.indexOf(item.key) !== -1;
    return $("<button type='button' class='orbit-star'>").text(yes ? "★" : "☆")
      .attr({"aria-label": (yes ? "Remover dos favoritos: " : "Adicionar aos favoritos: ") + item.title, "aria-pressed": String(yes)})
      .on("click", function () {
        if (yes) favorites = favorites.filter(function (id) { return id !== item.key; }); else favorites.push(item.key);
        save("favorites", favorites); render();
        var $next = $results.find("button").filter(function () { return $(this).attr("data-key") === item.key; });
        if ($next.length) $next.trigger("focus"); else $input.trigger("focus");
      }).attr("data-key", item.key);
  }
  function render() {
    $results.empty();
    if (!loaded) { $status.text("Carregando inquéritos"); return; }
    if (failed) { $status.text("Não foi possível carregar todas as fontes."); $("<button type='button'>Tentar novamente</button>").on("click", load).appendTo($results); }
    var term = norm($input.val());
    var matches = items.filter(function (i) {
      return (view !== "favorites" || favorites.indexOf(i.key) !== -1) && (view !== "mine" || String(i.owner) === String(user.id_usuario)) && norm(i.title + " " + i.description).includes(term);
    });
    matches.slice(0, 80).forEach(function (item) {
      var $row = $("<div class='orbit-search-row'>").appendTo($results);
      $("<a>").attr("href", item.href).append($("<strong>").text(item.title)).append($("<small>").text(item.description)).appendTo($row);
      $row.append(star(item));
    });
    if (view === "search") (window.MapaTelas.TELAS_BUSCA || []).filter(function (screen) { return (!screen.admin || user.in_admin) && (!screen.auditoria || user.pode_auditoria) && norm((screen.rotulo || screen.label || screen.nome) + " " + (screen.termos || "") + " " + (screen.desc || "")).includes(term); }).forEach(function (screen) {
      var href = screen.href || (screen.pagina ? screen.pagina + ".html" : "");
      if (href) $("<a class='orbit-screen-result'>").attr("href", href).text(screen.rotulo || screen.label || screen.nome).appendTo($results);
    });
    if (!failed) $status.text($results.children().length ? matches.length + " inquérito(s)" + (matches.length > 80 ? " — mostrando 80; refine a pesquisa." : "") : view === "favorites" ? "Nenhum favorito. Use a estrela na Pesquisa para adicionar." : "Nenhum resultado encontrado.");
  }
  function open(kind) {
    if (!active()) return;
    if (user && user.in_admin) return;
    if (kind === "all" && (!user || !user.in_admin)) return;
    view = kind; originFocus = document.activeElement;
    $dialog.find("h2").text({search:"Pesquisa", favorites:"Favoritos", mine:"Meus Inquéritos", all:"Todos os Inquéritos"}[kind]);
    $input.val(""); render();
    if (!$dialog[0].open) $dialog[0].showModal();
    $input.trigger("focus");
  }
  $input.on("input", render);
  $dialog.find(".orbit-search-close").on("click", function () { $dialog[0].close(); });
  $dialog.on("close", function () { if (originFocus && originFocus.isConnected) originFocus.focus(); });
  $dialog.on("click", function (e) { if (e.target === this) { var r=this.getBoundingClientRect(); if (e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom) this.close(); } });
  $input.on("keydown", function (e) { if (e.key === "ArrowDown") { e.preventDefault(); $results.find("a").first().trigger("focus"); } if (e.key === "Enter") { var a=$results.find("a")[0]; if(a) a.click(); } });
  document.addEventListener("keydown", function (e) { if (active() && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { if (user && user.in_admin) return; e.preventDefault(); e.stopImmediatePropagation(); open("search"); } }, true);
  function load() {
    if (!user || user.in_admin) return;
    loaded=false; failed=false; render();
    function get(url) { return $.getJSON(url).then(function (data) { return data; }, function () { failed=true; return []; }); }
    $.when(get("/api/investigacao/inqueritos")).done(function (inqs) {
      items = (inqs || []).map(function(i){ var eCaso = !!i.in_caso_inteligencia; return {key:"inquerito:"+i.id_inquerito, title:(eCaso ? "Caso de inteligência " : "") + (formatarNumeroInq(i.nu_inquerito) || "Inquérito"), owner:i.id_usuario_criador, description:(eCaso ? "Caso de inteligência · " : "Inquérito · ") + (i.administrador || ""), href:"inquerito_orbit.html?inquerito="+encodeURIComponent(i.id_inquerito)}; });
      loaded=true; renderInquiry(inqs); recordCurrent(); render();
    });
  }
  function identify(u) {
    if (!u || !u.id_usuario || (user && user.id_usuario === u.id_usuario)) return;
    user=u; mode=read("layout", "top"); if (mode!=="side") mode="top";
    collapsed=read("collapsed", false) === true;
    favorites=read("favorites", []); if(!Array.isArray(favorites)) favorites=[];
    recent=read("recent", []); if(!Array.isArray(recent)) recent=[];
    var ehAdmin = !!user.in_admin;
    $navPesquisa.toggle(!ehAdmin);
    $navConvergencia.toggle(!ehAdmin);
    $auditoria.toggle(!ehAdmin && !!user.pode_auditoria);
    $headingInqueritos.toggle(!ehAdmin);
    $all.hide();
    $navNovaImportacao.toggle(!ehAdmin);
    $navFavoritos.toggle(!ehAdmin);
    $navMeusInqueritos.toggle(!ehAdmin);
    $headingRecentes.toggle(!ehAdmin);
    $recents.toggle(!ehAdmin);
    if (ehAdmin) {
      $adminLinks.empty();
      $top.find(".topnav-admin a").each(function () {
        $adminLinks.append($(this).clone().removeClass("topnav-dropdown-item").removeAttr("role").addClass("orbit-extra-link"));
      });
      $more.show();
    } else {
      $more.hide();
    }
    apply();
    if (!ehAdmin) load();
  }
  $(document).on("analyser:usuario.orbitNav", function(e,u){identify(u);});
  // The shared auth script may already have emitted its event before this lazy module loaded.
  $.getJSON("/api/auth/eu").done(identify);
  window.OrbitNavigation = {open:open};
  apply();
})();
