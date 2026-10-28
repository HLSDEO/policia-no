/* Troca de abas da tela Auditoria ("Acesso a dado de inquérito" / "Chamadas de IA") — mesmo
 * padrão de .extracao-tabs/.extracao-tab-panel usado em investigacao.js. Cada aba mantém seu
 * próprio card com dados, carregado por auditoria_acesso.js / auditoria_ia.js. */
(function () {
  "use strict";

  var $abas = $(".extracao-tab");
  var $paineis = $(".extracao-tab-panel");

  function ativarAba(nome) {
    if (!$abas.filter('[data-tab="' + nome + '"]').length) nome = "acessos";
    $abas.removeClass("active").attr("aria-selected", "false");
    $paineis.addClass("hidden");
    $abas.filter('[data-tab="' + nome + '"]').addClass("active").attr("aria-selected", "true");
    $("#panel-" + nome).removeClass("hidden");
    try { localStorage.setItem("analyser-auditoria-aba", nome); } catch (e) {}
  }

  $abas.on("click", function () { ativarAba($(this).data("tab")); });

  var abaInicial = "acessos";
  try {
    var salva = localStorage.getItem("analyser-auditoria-aba");
    if (salva && $abas.filter('[data-tab="' + salva + '"]').length) abaInicial = salva;
  } catch (e) {}
  ativarAba(abaInicial);
})();
