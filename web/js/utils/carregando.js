/* Indicadores de carregamento — window.Carregando. Formaliza o padrão que já existia
 * improvisado em alguns lugares (ex.: o botão "Importar" de js/ingestao.js, que troca o texto
 * por um spinner enquanto a chamada está no ar) e cobre os dois casos que não tinham nada: uma
 * tabela em branco enquanto a primeira página carrega, e um bloco (card, mapa, grafo) tomado
 * por um overlay durante uma operação mais longa. Usa a classe .spinner que já existe em
 * css/style.css.
 *
 *   var estadoBtn = Carregando.botao($btn, "Salvando…");
 *   ...
 *   Carregando.restaurar($btn, estadoBtn);   // desfaz, reabilita
 *
 *   Carregando.emTabela($tbody, 5);   // 5 colunas de skeleton
 *   Carregando.emBloco($card);
 *   Carregando.limpar($card);          // tira o overlay de emBloco */
(function () {
  "use strict";

  var SPINNER = '<span class="spinner" aria-hidden="true"></span>';

  // Guarda o HTML/disabled originais no próprio elemento (data-*), pra não exigir que quem
  // chama carregue o estado anterior manualmente.
  function botao($btn, textoCarregando) {
    $btn.each(function () {
      var $b = $(this);
      if ($b.data("carregando-original") != null) return; // já em estado de carregando
      $b.data("carregando-original", $b.html());
      $b.data("carregando-disabled", $b.prop("disabled"));
    });
    $btn.prop("disabled", true).attr("aria-busy", "true")
      .html(SPINNER + (textoCarregando ? " " + $("<span>").text(textoCarregando).html() : ""));
    return $btn;
  }

  function restaurar($btn) {
    $btn.each(function () {
      var $b = $(this);
      var original = $b.data("carregando-original");
      if (original == null) return;
      $b.html(original).prop("disabled", !!$b.data("carregando-disabled")).removeAttr("aria-busy");
      $b.removeData("carregando-original");
      $b.removeData("carregando-disabled");
    });
    return $btn;
  }

  function emTabela($tbody, nColunas) {
    nColunas = nColunas || 4;
    $tbody.empty();
    for (var i = 0; i < 6; i++) {
      var $tr = $("<tr>").addClass("skeleton-linha");
      for (var c = 0; c < nColunas; c++) {
        $("<td>").append($("<span>").addClass("skeleton-bloco")).appendTo($tr);
      }
      $tbody.append($tr);
    }
    return $tbody;
  }

  function emBloco($el) {
    $el.each(function () {
      var $alvo = $(this);
      if ($alvo.find("> .carregando-overlay").length) return;
      $alvo.css("position", function (i, v) { return v === "static" || !v ? "relative" : v; });
      $("<div>").addClass("carregando-overlay").attr("aria-hidden", "true")
        .append($("<span>").addClass("spinner spinner-grande"))
        .appendTo($alvo);
    });
    return $el;
  }

  function limpar($el) {
    $el.find("> .carregando-overlay").remove();
    return $el;
  }

  window.Carregando = { botao: botao, restaurar: restaurar, emTabela: emTabela, emBloco: emBloco, limpar: limpar };
})();
