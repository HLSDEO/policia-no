/* Tema da interface — registro dos temas disponíveis e a troca entre eles (window.Tema).
 *
 * O menu (js/utils/sidebar.js) monta o dropdown "Temas" a partir da lista TEMAS daqui, então
 * criar um tema novo exige duas alterações:
 *
 * 1. css/style.css — um bloco `:root[data-theme="<id>"] { ... }` com TODOS os tokens do bloco
 *    do tema escuro redefinidos (o `:root` base é o escuro, então token esquecido não fica
 *    sem valor, fica com a cor do escuro no meio do tema novo). Regras que dependem do tema
 *    fora do bloco de tokens — as de superfície do menu, por exemplo — também precisam de
 *    uma versão para o id novo.
 * 2. TEMAS aqui embaixo — { id, nome, icone }: o dropdown, o marcador de tema ativo e o
 *    <script> do <head> de cada página passam a conhecer o tema sozinhos.
 * O Orbit é local ao navegador: ele também troca o arquivo da tela principal e, por isso, não é
 * enviado ao backend. Claro/Escuro continuam sendo persistidos na conta exatamente como antes.
 *
 * O `icone` é uma chave de window.Icones (js/utils/icones.js), carregado antes deste arquivo. */
(function () {
  "use strict";

  var TEMAS = [
    { id: "dark", nome: "Escuro", icone: "lua" },
    { id: "light", nome: "Claro", icone: "sol" },
    { id: "orbit", nome: "Orbit", icone: "orbit" }
  ];

  var PADRAO = "dark";
  var CHAVE_CACHE = "analyser-theme";

  // Migra a preferência salva antes de FRONTEND-V3 receber o nome Orbit.
  if (document.documentElement.getAttribute("data-theme") === "frontend-v3") {
    document.documentElement.setAttribute("data-theme", "orbit");
    guardarCache("orbit");
  }

  function existe(id) {
    for (var i = 0; i < TEMAS.length; i++) {
      if (TEMAS[i].id === id) return true;
    }
    return false;
  }

  function atual() {
    var id = document.documentElement.getAttribute("data-theme");
    return existe(id) ? id : PADRAO;
  }

  function ehTemaLocal(id) {
    return id === "orbit";
  }

  var PAGINAS_POR_TELA = {
    inicio: { padrao: "index.html", orbit: "index_orbit.html" },
    inqueritos: { padrao: "inqueritos.html", orbit: "inqueritos_orbit.html" },
    inquerito: { padrao: "investigacao.html", orbit: "inquerito_orbit.html" }
  };

  var TELA_POR_PAGINA = {
    "index.html": "inicio",
    "index_orbit.html": "inicio",
    "inqueritos.html": "inqueritos",
    "inqueritos_orbit.html": "inqueritos",
    "inquerito.html": "inquerito",
    "inquerito_orbit.html": "inquerito"
  };

  function paginaAtual() {
    return window.location.pathname.split("/").pop() || "index.html";
  }

  function telaLogica() {
    var pagina = paginaAtual();
    if (TELA_POR_PAGINA[pagina]) return TELA_POR_PAGINA[pagina];
    if (pagina === "investigacao.html") {
      var params = new URLSearchParams(window.location.search);
      if (params.get("tema_rota") === "ignorar" || params.get("caso") || params.get("doc") || params.get("aba")) return null;
      if (params.get("inquerito")) return "inquerito";
      if (ehTemaLocal(atual())) return "inqueritos";
    }
    return null;
  }

  function redirecionarParaTema(id) {
    var tela = telaLogica();
    if (!tela) return false;
    var variante = ehTemaLocal(id) ? "orbit" : "padrao";
    var destino = PAGINAS_POR_TELA[tela][variante];
    if (!destino || paginaAtual() === destino) return false;
    var params = new URLSearchParams(window.location.search);
    params.delete("tema_rota");
    var sufixo = params.toString() ? "?" + params.toString() : "";
    window.location.replace(destino + sufixo + window.location.hash);
    return true;
  }

  // A classe .trocando-tema desliga as transitions durante a troca: sem isso o Chromium deixa
  // `background: var(--token)` preso no valor do tema anterior em tudo que tem
  // `transition: background-color` (botões, itens de menu) até o próximo reload.
  function aplicar(id) {
    var root = document.documentElement;
    root.classList.add("trocando-tema");
    root.setAttribute("data-theme", id);
    void root.offsetHeight; // força o recálculo já com as transitions desligadas
    // setTimeout, não requestAnimationFrame: em aba em segundo plano o rAF é suspenso e a
    // classe ficaria presa, deixando o app inteiro sem nenhuma transition.
    setTimeout(function () {
      root.classList.remove("trocando-tema");
    }, 0);
    marcarAtivo();
  }

  function guardarCache(id) {
    try {
      localStorage.setItem(CHAVE_CACHE, id);
    } catch (e) {
      /* localStorage indisponível (modo privado etc.) — só perde o cache que evita o flash de
         tema errado no próximo carregamento; o tema desta página já foi aplicado. */
    }
  }

  /* Ícone compacto da Orbit: usa o PNG aprovado, com o mesmo recorte/filtro da home.
   * Roda também nas telas compartilhadas; ao sair da Orbit restaura o markup original do menu. */
  function atualizarMarcaMenu() {
    carregarNavegacaoOrbit();
    $(".topnav-brand .brand-mark").each(function (indice) {
      var $marca = $(this);
      var original = $marca.data("marca-original-orbit");
      if (atual() !== "orbit") {
        if (original !== undefined) {
          $marca.html(original).removeData("marca-original-orbit");
        }
        return;
      }
      if (original !== undefined) return;
      $marca.data("marca-original-orbit", $marca.html());
      var filtro = "topnav-radar-suave-" + indice;
      $marca.html(
        '<svg class="topnav-logo-orbit" viewBox="207 90 1116 822" width="38" height="28" style="display:block;flex-shrink:0" aria-hidden="true" focusable="false">' +
          '<defs><filter id="' + filtro + '" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">' +
            '<feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  1 0 0 0 0" result="tom"/>' +
            '<feComponentTransfer in="tom" result="suavidade"><feFuncA type="table" tableValues="1 1 1 1 1 1 1 0.55 0.4 0.4 0.4"/></feComponentTransfer>' +
            '<feComposite in="SourceGraphic" in2="suavidade" operator="in"/>' +
          '</filter></defs>' +
          '<image href="img/logo-radar-sobrio-orbit.png" width="1536" height="1024" filter="url(#' + filtro + ')"/>' +
        '</svg>'
      );
    });
  }

  function carregarNavegacaoOrbit() {
    if (atual() !== "orbit" || !document.querySelector(".topnav") || document.getElementById("orbit-navigation-script")) return;
    var css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "css/navigation_orbit.css?v=10";
    document.head.appendChild(css);
    var script = document.createElement("script");
    script.id = "orbit-navigation-script";
    script.src = "js/utils/navigation_orbit.js?v=10";
    document.head.appendChild(script);
  }

  /* Marca no dropdown qual tema está valendo. Chamado depois de cada troca e quando o servidor
     manda um tema diferente do que estava em cache; nas páginas sem menu (login, cadastro,
     redefinir senha) não acha nada e não faz nada. */
  function marcarAtivo() {
    atualizarMarcaMenu();
    var id = atual();
    $(".topnav-theme-option").each(function () {
      var ativo = $(this).attr("data-tema") === id;
      $(this).toggleClass("active", ativo).attr("aria-checked", String(ativo));
    });
  }

  /* Troca escolhida pelo usuário: aplica na hora e persiste. */
  function escolher(id) {
    if (!existe(id) || id === atual()) return;
    aplicar(id);
    guardarCache(id);
    // localStorage é só cache pra aplicar o tema sem esperar a rede — quem manda é o banco
    // (ver api/funcoes/auth_utils.py:definir_tema), pra seguir o usuário entre navegadores/dispositivos.
    if (!ehTemaLocal(id)) {
      $.ajax({ url: "/api/auth/tema", method: "POST", contentType: "application/json", data: JSON.stringify({ tema: id }) });
    }
    redirecionarParaTema(id);
  }

  /* Tema que veio do banco no /api/auth/eu (js/utils/auth.js): só corrige o que já está na tela,
     sem POST de volta. Id desconhecido (tema removido do CSS) é ignorado, ficando o atual. */
  function definirDoServidor(id) {
    if (!existe(id)) return;
    if (ehTemaLocal(atual()) && !ehTemaLocal(id)) return;
    if (id !== atual()) aplicar(id);
    guardarCache(id);
    marcarAtivo();
  }

  /* HTML do dropdown "Temas" da topnav. Quem monta o menu chama isto; a lista de opções sai de
     TEMAS, então tema novo aparece aqui sem tocar em js/utils/sidebar.js. */
  function dropdownHtml() {
    var ativo = atual();
    var html =
      '<div class="topnav-theme" id="topnav-theme">' +
        '<button type="button" class="topnav-theme-toggle" aria-expanded="false"' +
          ' aria-haspopup="true" aria-controls="topnav-theme-dropdown" title="Temas">' +
          window.Icones.html("tema") +
          '<span class="topnav-theme-label">Temas</span>' +
          window.Icones.html("chevronDown", "topnav-theme-chevron") +
        '</button>' +
        '<div class="topnav-theme-dropdown" id="topnav-theme-dropdown" role="menu" style="display: none;">';
    TEMAS.forEach(function (tema) {
      var eAtivo = tema.id === ativo;
      html +=
        '<button type="button" class="topnav-theme-option' + (eAtivo ? " active" : "") + '"' +
          ' data-tema="' + tema.id + '" role="menuitemradio" aria-checked="' + (eAtivo ? "true" : "false") + '">' +
          window.Icones.html(tema.icone) +
          '<span>' + tema.nome + '</span>' +
          window.Icones.html("check", "topnav-theme-check") +
        '</button>';
    });
    html += '</div></div>';
    return html;
  }

  /* O dropdown é ancorado à direita do botão (right: 0). Quando a topnav quebra em duas linhas
     (telas estreitas), o bloco do tema vai parar na borda ESQUERDA da tela e essa ancoragem joga
     o painel pra fora do viewport — aí ele passa a abrir da esquerda pra direita. */
  function posicionar($dropdown) {
    $dropdown.css({ left: "", right: "" });
    if ($dropdown[0].getBoundingClientRect().left < 8) {
      $dropdown.css({ left: 0, right: "auto" });
    }
  }

  function fechar(devolverFoco) {
    var $dropdown = $("#topnav-theme-dropdown");
    if (!$dropdown.length || $dropdown.is(":hidden")) return;
    $dropdown.hide();
    $(".topnav-theme-toggle").attr("aria-expanded", "false");
    if (devolverFoco) $(".topnav-theme-toggle").trigger("focus");
  }

  // O <script> do <head> aplica o id que estava em cache sem conhecer a lista de temas. Se ele
  // não existe mais (tema tirado do CSS), volta pro padrão — senão a página ficaria com os
  // tokens do bloco `:root` base e um data-theme que nenhuma regra casa.
  if (!existe(document.documentElement.getAttribute("data-theme"))) {
    document.documentElement.setAttribute("data-theme", PADRAO);
    guardarCache(PADRAO); // senão o id morto voltaria a ser aplicado no próximo carregamento
  }

  redirecionarParaTema(atual());

  // Eventos delegados: o menu é injetado por js/utils/sidebar.js depois deste arquivo.
  $(function () {
    atualizarMarcaMenu();
    $(document).on("click", ".topnav-theme-toggle", function (e) {
      e.stopPropagation();
      var $dropdown = $("#topnav-theme-dropdown");
      var abrindo = $dropdown.is(":hidden");
      if (abrindo) {
        // Exclusão mútua com o menu do usuário (js/utils/sidebar.js escuta este evento).
        $(document).trigger("topnav:tema-abrindo");
        $dropdown.show();
        posicionar($dropdown);
      } else {
        $dropdown.hide();
      }
      $(this).attr("aria-expanded", String(abrindo));
    });

    $(document).on("click", ".topnav-theme-option", function () {
      escolher($(this).attr("data-tema"));
      fechar(false);
      $(".topnav-theme-toggle").trigger("focus");
    });

    $(document).on("click.topnavtema", function (e) {
      if (!$(e.target).closest(".topnav-theme").length) fechar(false);
    });

    // Menu do usuário aberto fecha o de Temas (exclusão mútua).
    $(document).on("topnav:usuario-abrindo", function () {
      fechar(false);
    });

    $(document).on("keydown.topnavtema", function (e) {
      var $dropdown = $("#topnav-theme-dropdown");
      if (!$dropdown.length || $dropdown.is(":hidden")) return;
      if (e.key === "Escape") {
        e.preventDefault();
        fechar(true);
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
      if (!$(e.target).closest(".topnav-theme").length) return;
      e.preventDefault();
      var $opcoes = $dropdown.find(".topnav-theme-option");
      var pos = $opcoes.index($(document.activeElement).closest(".topnav-theme-option"));
      if (e.key === "Home") pos = 0;
      else if (e.key === "End") pos = $opcoes.length - 1;
      else if (e.key === "ArrowDown") pos = pos < $opcoes.length - 1 ? pos + 1 : 0;
      else if (e.key === "ArrowUp") pos = pos > 0 ? pos - 1 : $opcoes.length - 1;
      if ($opcoes[pos]) $opcoes[pos].focus();
    });
  });

  window.Tema = {
    TEMAS: TEMAS,
    PADRAO: PADRAO,
    atual: atual,
    escolher: escolher,
    ehTemaLocal: ehTemaLocal,
    redirecionarParaTema: redirecionarParaTema,
    definirDoServidor: definirDoServidor,
    dropdownHtml: dropdownHtml,
    fechar: fechar
  };
})();
