/* Troca de abas da tela "IA - Mesa de inquérito" ("Prompts de entidade" / "Padrões de
 * crime" / "Entidades automatizadas") — mesmo padrão de .extracao-tabs/.extracao-tab-panel
 * usado em auditoria.js e configuracoes.html.
 *
 * As abas eram páginas separadas do menu Administração; o conteúdo de cada uma continua
 * em js/prompts_entidade.js, js/padroes_crime.js e js/entidades_auto.js, que carregam
 * sozinhos ao abrir a página (são listas curtas, não vale adiar a carga até clicar na
 * aba). Aqui só se cuida de qual painel fica visível. */
(function () {
  "use strict";

  var CHAVE_ABA = "analyser-ia-mesa-aba";
  var PADRAO = "prompts";

  var $abas = $(".extracao-tab");
  var $paineis = $(".extracao-tab-panel");

  function ativarAba(nome) {
    if (!$abas.filter('[data-tab="' + nome + '"]').length) nome = PADRAO;
    $abas.removeClass("active").attr("aria-selected", "false");
    $paineis.addClass("hidden");
    $abas.filter('[data-tab="' + nome + '"]').addClass("active").attr("aria-selected", "true");
    $("#panel-" + nome).removeClass("hidden");
    try { localStorage.setItem(CHAVE_ABA, nome); } catch (e) { /* sem persistência */ }
  }

  $abas.on("click", function () { ativarAba($(this).data("tab")); });

  // ?aba=padroes abre direto na aba certa (link de outra tela); senão, a última usada
  var abaInicial = PADRAO;
  try {
    var daUrl = new URLSearchParams(window.location.search).get("aba");
    var salva = daUrl || localStorage.getItem(CHAVE_ABA);
    if (salva && $abas.filter('[data-tab="' + salva + '"]').length) abaInicial = salva;
  } catch (e) { /* sem persistência */ }
  ativarAba(abaInicial);
})();
