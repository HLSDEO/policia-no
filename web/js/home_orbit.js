/* Tela inicial: saudação (nome vem do evento "analyser:usuario", disparado por js/auth.js depois
 * de validar a sessão) e busca global.
 *
 * A busca substituiu a grade de "acesso rápido": com ~35 telas, um atalho fixo por tela não cabia
 * mais, e o que o investigador procura ao entrar é um inquérito, não uma seção. Ela acha duas
 * coisas — inquéritos (pelo número) e telas do sistema (pelo nome, sem depender de acento).
 * Com o campo vazio mostra os inquéritos mais recentes, que é o atalho que de fato importa.
 *
 * Ícones e a lista de telas vêm de módulos compartilhados com js/sidebar.js e
 * js/navegacao_inquerito.js — ver js/icones.js e js/mapa_telas.js (window.MapaTelas.TELAS_BUSCA),
 * que precisam carregar antes deste arquivo.
 *
 * Revisão a11y (1) + estados de carga/erro (2) + empty-state acionável (6):
 * - Combobox segue padrão ARIA 1.2: input[role=combobox][aria-expanded][aria-controls][aria-haspopup=listbox][aria-autocomplete=list]
 *   + aria-activedescendant aponta para a option selecionada por ↑/↓ (mover). Cada linha tem id="home-opt-N" e role=option + aria-selected.
 * - Chips Mesa/IA são <button type="button"> fora do <a> principal (evita <a> dentro de <a> / interactive dentro de interactive).
 * - Enquanto GET /api/investigacao/inqueritos carrega, mostra skeleton e aria-busy="true" em vez de flash de "Nenhum inquérito".
 * - Erro usa window.Estados.erro com "Tentar de novo" (Api.enviar com erroSilencioso para não duplicar toast global).
 * - Vazio sem dado usa window.Estados.vazio com CTAs Investigação / Nova importação; vazio filtrado oferece "Limpar busca". */
(function () {
  "use strict";

  var TELAS = window.MapaTelas.TELAS_BUSCA;
  var ICON_INQUERITO = window.Icones.html("inquerito");

  var MAX_INQUERITOS = 8;

  var $busca = $("#busca-global");
  var $resultados = $("#busca-resultados");
  var $rotulo = $("#busca-rotulo");
  var $live = $("#busca-live");

  var inqueritos = [];
  var indiceSelecionado = -1;
  var carregando = true;
  // Telas de administração (admin:true no MapaTelas) só entram na busca de quem é admin —
  // o menu esconde a seção "Administração" pela mesma regra (js/utils/sidebar.js), e sem
  // isto bastaria digitar "usuários" aqui para reencontrá-la. Começa false: a resposta de
  // /api/auth/eu chega depois, pelo evento "analyser:usuario".
  var ehAdmin = false;
  var podeAuditoria = false;

  function toggleBuscaAdmin() {
    if (ehAdmin) {
      $("body").addClass("home-admin");
    } else {
      $("body").removeClass("home-admin");
    }
  }

  /* Compara sem acento e sem caixa — "investigacao" acha "Investigação". NFD separa a letra
   * do acento e \p{Diacritic} joga o acento fora. */
  function normalizar(texto) {
    return String(texto || "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
  }

  function formatarNumero(n) {
    return Number(n || 0).toLocaleString("pt-BR");
  }

  function resumoInquerito(inq) {
    var partes = [];
    var qtExtracoes = Number(inq.qt_extracoes) || 0;
    if (qtExtracoes) partes.push(formatarNumero(qtExtracoes) + " extração(ões)");
    if (Number(inq.qt_mensagens)) partes.push(formatarNumero(inq.qt_mensagens) + " mensagens");
    if (Number(inq.qt_contatos)) partes.push(formatarNumero(inq.qt_contatos) + " contatos");
    if (Number(inq.qt_rifs)) partes.push(formatarNumero(inq.qt_rifs) + " RIF(s)");
    if (Number(inq.qt_ipls)) partes.push(formatarNumero(inq.qt_ipls) + " inquérito(s) em PDF");
    if (Number(inq.qt_simba)) partes.push(formatarNumero(inq.qt_simba) + " SIMBA");
    if (Number(inq.qt_iped)) partes.push(formatarNumero(inq.qt_iped) + " IPED");
    if (Number(inq.qt_irpfs)) partes.push(formatarNumero(inq.qt_irpfs) + " IRPF(s)");
    if (Number(inq.qt_documentos)) partes.push(formatarNumero(inq.qt_documentos) + " documento(s)");
    if (!partes.length) partes.push("Sem fontes de dados ainda");
    if (inq.administrador) partes.push("Administrador: " + inq.administrador);
    return partes.join(" · ");
  }

  function atualizarLive(texto) {
    if ($live && $live.length) $live.text(texto);
  }

  function skeletonItem() {
    var $item = $("<div>").addClass("home-resultado is-skeleton").attr("aria-hidden", "true");
    $("<span>").addClass("home-resultado-icone").html('<span class="skeleton-bloco" style="width:32px;height:32px;border-radius:8px"></span>').appendTo($item);
    var $linha = $("<span>").addClass("home-skeleton-linha").appendTo($item);
    $("<span>").addClass("skeleton-bloco").appendTo($linha);
    $("<span>").addClass("skeleton-bloco").appendTo($linha);
    return $item;
  }

  function mostrarSkeleton() {
    $resultados.empty().attr("aria-busy", "true");
    $rotulo.text("Inquéritos recentes");
    for (var i = 0; i < 4; i++) $resultados.append(skeletonItem());
    $busca.attr("aria-expanded", "false").removeAttr("aria-activedescendant");
    atualizarLive("Carregando inquéritos…");
  }

  function linhaResultado(icone, titulo, descricao, href, atalhos, idx) {
    var id = "home-opt-" + idx;
    var $item = $("<div>")
      .addClass("home-resultado")
      .attr({ role: "option", id: id, "data-href": href, "aria-selected": "false", tabindex: "-1" });

    var $link = $("<a>").addClass("home-resultado-link").attr("href", href).appendTo($item);
    // ícone + texto ficam dentro do link principal — o card inteiro continua clicável sem aninhar <button> dentro de <a>
    $("<span>").addClass("home-resultado-icone").html(icone).appendTo($link);
    var $texto = $("<span>").addClass("home-resultado-texto").appendTo($link);
    $("<span>").addClass("home-resultado-titulo").text(titulo).appendTo($texto);
    $("<span>").addClass("home-resultado-desc").text(descricao).appendTo($texto);

    if (atalhos && atalhos.length) {
      var $acoes = $("<span>").addClass("home-resultado-acoes").appendTo($item);
      atalhos.forEach(function (a) {
        $("<button>")
          .addClass("home-resultado-chip")
          .attr({ type: "button", "data-href": a.href, title: a.titulo || a.rotulo, "aria-label": a.titulo || a.rotulo })
          .text(a.rotulo)
          .appendTo($acoes);
      });
    }

    return $item;
  }

  function itemInquerito(inq, idx) {
    var id = encodeURIComponent(inq.id_inquerito);
    return linhaResultado(
      ICON_INQUERITO,
      inq.nu_inquerito || "Inquérito sem número",
      resumoInquerito(inq),
      "investigacao.html?inquerito=" + id,
      [
        { rotulo: "Quadro", href: "mesa_inquerito.html?inquerito=" + id, titulo: "Abrir o Quadro de Investigação" },
        { rotulo: "IA", href: "ia.html?inquerito=" + id, titulo: "Abrir as conversas de IA" }
      ],
      idx
    ).addClass("home-recente");
  }

  function itemTela(tela, idx) {
    return linhaResultado(window.Icones.html(tela.icone), tela.label, tela.desc, tela.href, null, idx);
  }

  function render() {
    if (carregando) {
      mostrarSkeleton();
      return;
    }

    var digitado = $.trim($busca.val());
    var termo = normalizar(digitado);
    $resultados.empty().attr("aria-busy", "false");
    indiceSelecionado = -1;
    $busca.removeAttr("aria-activedescendant");

    var inqueritosAchados = inqueritos.filter(function (inq) {
      return !termo || normalizar(inq.nu_inquerito).indexOf(termo) !== -1;
    }).slice(0, MAX_INQUERITOS);

    var telasAchadas = termo
      ? TELAS.filter(function (t) {
          if (t.admin && !ehAdmin) return false;
          if (t.auditoria && !podeAuditoria) return false;
          return normalizar(t.label + " " + t.desc + " " + t.termos).indexOf(termo) !== -1;
        })
      : [];

    $rotulo.text(termo ? "Resultados" : "Inquéritos recentes");

    var temResultados = false;

    if (!termo) {
      if (!inqueritos.length) {
        // (6) empty-state acionável — vazio real (sem dado no banco), não filtro
        if (window.Estados && window.Estados.vazio) {
          window.Estados.vazio($resultados, {
            titulo: "Nenhum inquérito ainda",
            dica: "Comece pela tela de Investigação ou faça uma nova importação.",
            acao: { rotulo: "Ir para Investigação", aoClicar: function () { window.location.href = "investigacao.html"; } }
          });
          var $vazio = $resultados.find(".estado-vazio");
          // segundo CTA — Estados só expõe uma ação
          if ($vazio.length) {
            var $wrap = $vazio.find(".home-empty-acoes");
            if (!$wrap.length) $wrap = $("<div>").addClass("home-empty-acoes").appendTo($vazio);
            // move o botão criado por Estados para dentro do wrap e adiciona o segundo
            var $primeiro = $vazio.find(".estado-acao").first();
            if ($primeiro.length && !$primeiro.parent().hasClass("home-empty-acoes")) {
              $primeiro.appendTo($wrap);
            }
            $("<button>").addClass("btn btn-secondary").attr("type", "button").text("Nova importação")
              .on("click", function () { window.location.href = "importacao.html"; }).appendTo($wrap);
          }
        } else {
          $resultados.append($("<p>").addClass("empty-hint").text("Nenhum inquérito ainda — comece pela tela de Investigação."));
        }
        $busca.attr("aria-expanded", "false");
        atualizarLive("Nenhum inquérito cadastrado");
        return;
      }
      var baseIdx = 0;
      inqueritosAchados.forEach(function (inq) { $resultados.append(itemInquerito(inq, baseIdx++)); });
      temResultados = inqueritosAchados.length > 0;
      $busca.attr("aria-expanded", temResultados ? "true" : "false");
      atualizarLive(temResultados ? formatarNumero(inqueritosAchados.length) + " inquéritos recentes" : "Nenhum inquérito");
      return;
    }

    if (!inqueritosAchados.length && !telasAchadas.length) {
      if (window.Estados && window.Estados.vazio) {
        window.Estados.vazio($resultados, {
          titulo: 'Nada encontrado para "' + digitado + '"',
          dica: "Tente outro número de inquérito ou nome de tela.",
          acao: { rotulo: "Limpar busca", aoClicar: function () { $busca.val(""); render(); $busca.trigger("focus"); } }
        });
      } else {
        $resultados.append($("<p>").addClass("empty-hint").text("Nada encontrado para “" + digitado + "”."));
      }
      $busca.attr("aria-expanded", "false");
      atualizarLive("Nenhum resultado para " + digitado);
      return;
    }

    var idxCounter = 0;
    if (inqueritosAchados.length) {
      $resultados.append($("<p>").addClass("home-grupo-label").text("Inquéritos"));
      inqueritosAchados.forEach(function (inq) { $resultados.append(itemInquerito(inq, idxCounter++)); });
    }
    if (telasAchadas.length) {
      $resultados.append($("<p>").addClass("home-grupo-label").text("Telas do sistema"));
      telasAchadas.forEach(function (t) { $resultados.append(itemTela(t, idxCounter++)); });
    }
    temResultados = idxCounter > 0;
    $busca.attr("aria-expanded", temResultados ? "true" : "false");
    var totalTxt = [];
    if (inqueritosAchados.length) totalTxt.push(inqueritosAchados.length + " inquérito(s)");
    if (telasAchadas.length) totalTxt.push(telasAchadas.length + " tela(s)");
    atualizarLive(totalTxt.join(", ") + " encontrados para " + (digitado || "todos"));
  }

  // ---------- teclado: ↑/↓ percorrem, Enter abre ----------

  function mover(passo) {
    var $itens = $resultados.find(".home-resultado");
    if (!$itens.length) return;
    indiceSelecionado += passo;
    if (indiceSelecionado < 0) indiceSelecionado = $itens.length - 1;
    if (indiceSelecionado >= $itens.length) indiceSelecionado = 0;
    $itens.removeClass("selecionado").attr("aria-selected", "false");
    var $alvo = $itens.eq(indiceSelecionado).addClass("selecionado").attr("aria-selected", "true");
    var idAlvo = $alvo.attr("id");
    if (idAlvo) $busca.attr("aria-activedescendant", idAlvo);
    $alvo[0].scrollIntoView({ block: "nearest" });
  }

  $(document).on("keydown.homeBusca", function (e) {
    if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === "k") {
      e.preventDefault();
      $busca.trigger("focus").select();
    }
  });

  $busca.on("input", render);

  $busca.on("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); mover(1); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); mover(-1); return; }
    if (e.key === "Escape") { $busca.val(""); render(); return; }
    if (e.key === "Enter") {
      var $itens = $resultados.find(".home-resultado");
      var $alvo = indiceSelecionado >= 0 ? $itens.eq(indiceSelecionado) : $itens.first();
      if ($alvo.length) {
        e.preventDefault();
        var href = $alvo.attr("data-href") || $alvo.find(".home-resultado-link").attr("href");
        if (href) window.location.href = href;
      }
    }
  });

  $resultados.on("mouseenter", ".home-resultado", function () {
    var $itens = $resultados.find(".home-resultado");
    indiceSelecionado = $itens.index(this);
    $itens.removeClass("selecionado").attr("aria-selected", "false");
    $(this).addClass("selecionado").attr("aria-selected", "true");
    var id = $(this).attr("id");
    if (id) $busca.attr("aria-activedescendant", id);
  });

  $resultados.on("mouseleave", ".home-resultado", function () {
    $(this).removeClass("selecionado").attr("aria-selected", "false");
    // mantém activedescendant no input até mover de novo — não limpa aqui
  });

  // Card clicável (fora dos chips): navegar para o href do item
  $resultados.on("click", ".home-resultado", function (e) {
    if ($(e.target).closest(".home-resultado-chip, .home-resultado-link").length) return;
    var href = $(this).attr("data-href") || $(this).find(".home-resultado-link").attr("href");
    if (href) window.location.href = href;
  });

  /* Chips são <button type="button"> fora do <a> — não aninha interativo dentro de interativo (1) */
  $resultados.on("click", ".home-resultado-chip", function (e) {
    e.preventDefault();
    e.stopPropagation();
    var href = $(this).attr("data-href");
    if (href) window.location.href = href;
  });

  // ---------- carga ----------

  $(document).on("analyser:usuario", function (e, usuario) {
    if (!usuario || !usuario.no_usuario) {
      $("#home-greeting").text("");
      return;
    }
    $("#home-greeting").html("Bem-vindo(a) de volta, <strong>" + $("<span>").text(usuario.no_usuario).html() + "</strong>.");
    var mudou = false;
    if (usuario.in_admin && !ehAdmin) { ehAdmin = true; mudou = true; }
    if (usuario.pode_auditoria && !podeAuditoria) { podeAuditoria = true; mudou = true; }
    if (mudou) {
      toggleBuscaAdmin();
      // só refaz a lista se já houver busca digitada — no caso comum (campo vazio) a
      // resposta chega antes de qualquer tecla e o render normal já sai completo
      if ($.trim($busca.val())) render();
    } else if (ehAdmin) {
      toggleBuscaAdmin();
    }
  });

  function carregarInqueritos() {
    carregando = true;
    mostrarSkeleton();
    var req;
    if (window.Api && window.Api.enviar) {
      req = window.Api.enviar({ url: "/api/investigacao/inqueritos", method: "GET", erroSilencioso: true });
    } else {
      req = $.getJSON("/api/investigacao/inqueritos");
    }
    req.done(function (lista) {
        inqueritos = lista || [];
        carregando = false;
        $resultados.attr("aria-busy", "false");
        render();
      })
      .fail(function (xhr) {
        carregando = false;
        $resultados.attr("aria-busy", "false");
        $rotulo.text("Inquéritos recentes");
        var msg = (window.Api && window.Api.mensagemErro) ? window.Api.mensagemErro(xhr, "Não foi possível carregar os inquéritos.") : "Não foi possível carregar os inquéritos.";
        if (xhr && xhr.status === 401) return; // guarda de auth.js redireciona
        if (window.Estados && window.Estados.erro) {
          window.Estados.erro($resultados, { mensagem: msg, aoTentarDeNovo: carregarInqueritos });
        } else {
          $resultados.empty().append($("<p>").addClass("empty-hint").text(msg));
        }
        $busca.attr("aria-expanded", "false");
        atualizarLive(msg);
      });
  }

  render();
  $busca.trigger("focus");
  carregarInqueritos();
  if (ehAdmin) toggleBuscaAdmin();
})();


