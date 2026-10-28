/* Sumário flutuante de marcações — a árvore de Marcadores do IPED na tela do WhatsApp.
 *
 * Um segundo botão no canto inferior direito (ao lado do da IA, ver js/ia_flutuante.js) abre
 * uma árvore numerada do que ESTE investigador etiquetou neste aparelho:
 *
 *   1. TRÁFICO DE DROGAS                       (12)
 *      1.1 Conversa com Beltrano        [conversa]
 *          · "mensagem xpto"                14/03
 *   2. TRÁFICO DE ARMAS                        (3)
 *
 * Clicar numa mensagem leva até a bolha; clicar na conversa abre a conversa. A navegação passa
 * por window.WhatsAppUI (js/whatsapp.js) e NÃO recarrega a página: recarregar perderia a
 * rolagem, a conversa aberta e o painel de IA em uso.
 *
 * Os marcadores são pessoais e presos ao inquérito (api/funcoes/marcadores_utils.py), então o
 * que aparece aqui é sempre só o do usuário logado.
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  if (!idInquerito) return;               // fora do contexto de um inquérito não há o que somar

  var fonte = paramUrl("fonte") || "wpp";
  var idExtracao = paramUrl("extracao") || null;
  var NOME_FONTE = { wpp: "WhatsApp", sms: "SMS", msg: paramUrl("app") || "Mensageiros" };

  var sumario = [];
  var aberto = false;
  var carregando = false;
  var recarregarTimer = null;

  // ---------- DOM (injetado: a página só precisa incluir este script) ----------

  var $botao = $(
    '<button type="button" class="mrcflt-botao" id="mrcflt-botao" title="Sumário de marcações">' +
      '<span>🏷️</span><span class="mrcflt-badge hidden" id="mrcflt-badge"></span>' +
    '</button>'
  ).appendTo("body");

  var $painel = $(
    '<div class="mrcflt-painel" id="mrcflt-painel">' +
      '<div class="mrcflt-cabecalho">' +
        '<div class="mrcflt-cabecalho-titulos">' +
          '<div class="mrcflt-titulo">Sumário de marcações</div>' +
          '<div class="mrcflt-subtitulo" id="mrcflt-subtitulo"></div>' +
        '</div>' +
        '<button type="button" class="mrcflt-fechar" id="mrcflt-fechar">&times;</button>' +
      '</div>' +
      '<div class="mrcflt-corpo" id="mrcflt-corpo"></div>' +
      '<div class="mrcflt-rodape">' +
        '<a class="btn btn-secondary" id="mrcflt-pagina">Abrir página de marcações</a>' +
        '<button type="button" class="btn btn-secondary" id="mrcflt-atualizar">Atualizar</button>' +
      '</div>' +
      '<div class="mrcflt-resize mrcflt-resize-se" data-resize="se"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-sw" data-resize="sw"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-ne" data-resize="ne"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-nw" data-resize="nw"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-e" data-resize="e"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-w" data-resize="w"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-s" data-resize="s"></div>' +
      '<div class="mrcflt-resize mrcflt-resize-n" data-resize="n"></div>' +
    '</div>'
  ).appendTo("body");

  var $corpo = $("#mrcflt-corpo");
  var $badge = $("#mrcflt-badge");
  var $subtitulo = $("#mrcflt-subtitulo");
  var $resizeHandles = $painel.find(".mrcflt-resize");

  // ---------- resize do painel ----------
  var resizeState = null;

  function iniciarResize(e, direcao) {
    e.preventDefault(); e.stopPropagation();
    resizeState = {
      direcao: direcao,
      startX: e.clientX || (e.touches && e.touches[0].clientX),
      startY: e.clientY || (e.touches && e.touches[0].clientY),
      startWidth: $painel[0].offsetWidth,
      startHeight: $painel[0].offsetHeight
    };
    $(document).on("mousemove.mrcfltResize touchmove.mrcfltResize", moverResize);
    $(document).on("mouseup.mrcfltResize touchend.mrcfltResize", finalizarResize);
    $("body").addClass("mrcflt-resizing");
  }

  function moverResize(e) {
    if (!resizeState) return;
    var clientX = e.clientX || (e.touches && e.touches[0].clientX);
    var clientY = e.clientY || (e.touches && e.touches[0].clientY);
    if (clientX == null || clientY == null) return;

    var dx = clientX - resizeState.startX;
    var dy = clientY - resizeState.startY;
    var dir = resizeState.direcao;

    var novaWidth = resizeState.startWidth;
    var novaHeight = resizeState.startHeight;

    var minW = 300, minH = 300;
    var maxW = Math.min(1200, $(window).width() - 28);
    var maxH = Math.min(900, $(window).height() - 116);

    if (dir.indexOf("e") !== -1 || dir.indexOf("w") !== -1) {
      var dW = dir === "w" ? -dx : dx;
      novaWidth = Math.min(maxW, Math.max(minW, resizeState.startWidth + dW));
    }
    if (dir.indexOf("s") !== -1 || dir.indexOf("n") !== -1) {
      var dH = dir === "n" ? -dy : dy;
      novaHeight = Math.min(maxH, Math.max(minH, resizeState.startHeight + dH));
    }

    $painel.css({ width: novaWidth + "px", height: novaHeight + "px" });
  }

  function finalizarResize() {
    if (!resizeState) return;
    resizeState = null;
    $(document).off(".mrcfltResize");
    $("body").removeClass("mrcflt-resizing");
  }

  $resizeHandles.on("mousedown touchstart", function (e) {
    var dir = $(this).data("resize");
    if (dir) iniciarResize(e, dir);
  });

  $subtitulo.text(NOME_FONTE[fonte] || "Mensagens");
  $("#mrcflt-pagina").attr(
    "href",
    "marcacoes.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + encodeURIComponent(fonte) +
      (idExtracao ? "&extracao=" + encodeURIComponent(idExtracao) : "")
  );

  // ---------- render ----------

  function formatarData(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: "America/Sao_Paulo" });
  }

  function irParaMensagem(idConversa, idMensagem) {
    if (window.WhatsAppUI && WhatsAppUI.irParaMensagem(idConversa, idMensagem)) return;
    // a conversa pode não estar na lista carregada (outro aparelho, por exemplo): aí não há
    // como saltar sem recarregar, e o deep-link ?msg= faz o resto. A página é a atual
    // (whatsapp.html ou mensageiros.html), que compartilham js/whatsapp.js.
    var url = new URL(window.location.href);
    url.searchParams.set("msg", idMensagem);
    window.location.href = url.toString();
  }

  function renderMensagem(msg, conversa) {
    var $item = $("<button>").attr("type", "button").addClass("mrcflt-mensagem")
      .attr("title", msg.tx_mensagem || msg.trecho)
      .on("click", function () { irParaMensagem(conversa.id_conversa, msg.id_mensagem); });
    $("<span>").addClass("mrcflt-mensagem-texto").text("· " + msg.trecho).appendTo($item);
    $("<span>").addClass("mrcflt-mensagem-data").text(formatarData(msg.dt_mensagem)).appendTo($item);
    return $item;
  }

  function renderConversa(conversa, numero) {
    var $wrap = $("<div>").addClass("mrcflt-conversa");
    var $cab = $("<button>").attr("type", "button").addClass("mrcflt-conversa-cabecalho")
      .attr("title", "Abrir esta conversa")
      .on("click", function () {
        if (window.WhatsAppUI) WhatsAppUI.abrirConversa(conversa.id_conversa);
      })
      .appendTo($wrap);
    $("<span>").addClass("mrcflt-num").text(numero).appendTo($cab);
    $("<span>").addClass("mrcflt-conversa-nome").text(conversa.titulo).appendTo($cab);
    // a conversa em si está marcada (triagem do contato), e não só mensagens dela — são duas
    // marcações diferentes e o analista precisa saber qual das duas está vendo
    if (conversa.marcada) {
      $("<span>").addClass("mrcflt-badge-conversa")
        .attr("title", "A conversa está marcada — triagem do contato, independente das mensagens")
        .text("marcada").appendTo($cab);
    }
    conversa.mensagens.forEach(function (msg) { $wrap.append(renderMensagem(msg, conversa)); });
    return $wrap;
  }

  function render() {
    $corpo.empty();
    var total = sumario.reduce(function (acc, m) { return acc + m.qt_mensagens + m.qt_conversas; }, 0);
    $badge.toggleClass("hidden", total === 0).text(total > 99 ? "99+" : total);

    if (!sumario.length) {
      $corpo.append($("<p>").addClass("mrcflt-vazio").text(
        "Nada marcado aqui ainda. Use o 🗒️ numa mensagem ou na conversa da lista para criar um marcador e agrupar o que interessa."
      ));
      return;
    }

    sumario.forEach(function (marcador, i) {
      var $bloco = $("<div>").addClass("mrcflt-marcador").appendTo($corpo);
      var $cab = $("<div>").addClass("mrcflt-marcador-cabecalho").appendTo($bloco);
      $("<span>").addClass("mrcflt-num").text((i + 1) + ".").appendTo($cab);
      $("<span>").addClass("mrcflt-bola").css("background", marcador.co_cor).appendTo($cab);
      $("<span>").addClass("mrcflt-marcador-nome").text(marcador.no_marcador).appendTo($cab);
      $("<span>").addClass("mrcflt-marcador-qt")
        .text("(" + (marcador.qt_mensagens + marcador.qt_conversas) + ")").appendTo($cab);

      marcador.conversas.forEach(function (conversa, j) {
        $bloco.append(renderConversa(conversa, (i + 1) + "." + (j + 1)));
      });
    });
  }

  // ---------- dados ----------

  function carregar(callback) {
    if (carregando) return;
    carregando = true;
    var url = "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/sumario?fonte=" +
      encodeURIComponent(fonte) + (idExtracao ? "&extracao=" + encodeURIComponent(idExtracao) : "");
    $.getJSON(url)
      .done(function (lista) {
        sumario = lista || [];
        render();
      })
      .fail(function () {
        $corpo.empty().append($("<p>").addClass("mrcflt-vazio").text("Não foi possível carregar o sumário."));
      })
      .always(function () {
        carregando = false;
        if (callback) callback();
      });
  }

  // marcar/desmarcar dispara analyser:marcacao-mudou (js/entidades.js). Marcar um dia inteiro
  // emite um evento por registro, então o recarregamento é adiado para juntar a rajada num só.
  $(document).on("analyser:marcacao-mudou", function () {
    clearTimeout(recarregarTimer);
    recarregarTimer = setTimeout(function () { carregar(); }, 400);
  });

  // ---------- abrir/fechar ----------

  function alternar() {
    aberto = !aberto;
    $painel.toggleClass("aberto", aberto);
    if (aberto) carregar();
  }

  $botao.on("click", alternar);
  $("#mrcflt-fechar").on("click", function () { aberto = false; $painel.removeClass("aberto"); });
  $("#mrcflt-atualizar").on("click", function () { carregar(); });
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && aberto) { aberto = false; $painel.removeClass("aberto"); }
  });

  // primeira carga em segundo plano, só para o badge já nascer com a contagem certa
  carregar();
})();
