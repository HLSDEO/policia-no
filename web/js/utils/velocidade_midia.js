/* Velocidade de reprodução de áudio/vídeo — window.VelocidadeMidia. Os controles nativos do
 * <audio> só oferecem velocidade num menu escondido (e no Firefox nem isso), então quem ouve
 * dezenas de áudios de WhatsApp seguidos ficava preso em 1×. Botões 1× / 1,5× / 2× (as mesmas
 * do WhatsApp) logo abaixo do player, com a escolha lembrada entre um áudio e outro — o normal
 * é ouvir a fila inteira na mesma velocidade. O navegador preserva o tom (preservesPitch), então
 * a voz continua reconhecível em 2×.
 *
 *   var $audio = $("<audio>").attr({ controls: true, src: url }).appendTo($corpo);
 *   VelocidadeMidia.controle($audio).appendTo($corpo); */
(function () {
  "use strict";

  var VELOCIDADES = [1, 1.5, 2];
  var CHAVE = "analyser:velocidade-midia";

  function lida() {
    try {
      var v = parseFloat(window.localStorage.getItem(CHAVE));
      if (VELOCIDADES.indexOf(v) !== -1) return v;
    } catch (e) { /* localStorage bloqueado: cai no padrão */ }
    return 1;
  }

  function grava(v) {
    try { window.localStorage.setItem(CHAVE, String(v)); } catch (e) { /* idem */ }
  }

  function rotulo(v) {
    return String(v).replace(".", ",") + "×";
  }

  // Monta a barra de botões já ligada ao elemento de mídia e devolve para quem chama posicionar.
  function controle($midia) {
    var el = $midia && $midia.length ? $midia[0] : null;
    if (!el) return $();

    var atual = lida();
    var $barra = $("<div>").addClass("midia-velocidade")
      .attr({ role: "group", "aria-label": "Velocidade de reprodução" });
    var $botoes = $();

    function aplicar(v, persistir) {
      atual = v;
      // defaultPlaybackRate junto de playbackRate: sem ele o navegador volta para 1× quando a
      // mídia termina de carregar ou é rebobinada.
      try {
        el.defaultPlaybackRate = v;
        el.playbackRate = v;
      } catch (e) { /* mídia ainda sem src utilizável */ }
      $botoes.each(function () {
        var $b = $(this);
        var ativa = parseFloat($b.attr("data-velocidade")) === v;
        $b.toggleClass("ativa", ativa).attr("aria-pressed", ativa ? "true" : "false");
      });
      if (persistir) grava(v);
    }

    VELOCIDADES.forEach(function (v) {
      var $b = $("<button>").attr({ type: "button", "data-velocidade": v, title: "Ouvir em " + rotulo(v) })
        .addClass("midia-velocidade-btn").text(rotulo(v))
        .on("click", function () { aplicar(v, true); });
      $botoes = $botoes.add($b);
      $barra.append($b);
    });

    $midia.on("loadedmetadata play", function () {
      if (el.playbackRate !== atual) aplicar(atual, false);
    });
    aplicar(atual, false);
    return $barra;
  }

  window.VelocidadeMidia = { controle: controle, VELOCIDADES: VELOCIDADES };
})();
