/* Estados vazio/erro — window.Estados. Substitui o `<p class="empty-hint">` solto (presente em
 * dezenas de HTML) e o padrão de sobrescrever esse mesmo texto quando a chamada falha (ex.:
 * "Não foi possível carregar os contatos." por cima de "Nenhum registro encontrado.").
 *
 * Distingue duas situações que uma ferramenta forense não deveria confundir: "não há esse dado
 * no aparelho" e "nenhum resultado bate com este filtro" são conclusões diferentes para um
 * investigador — a primeira é sobre o aparelho, a segunda é sobre a busca. Por isso
 * Estados.vazio aceita `filtrado` para trocar a mensagem e oferecer "Limpar filtros".
 *
 *   Estados.vazio($alvo, { titulo: "Nenhum contato encontrado" })
 *   Estados.vazio($alvo, { filtrado: true, acao: { rotulo: "Limpar filtros", aoClicar: fn } })
 *   Estados.erro($alvo, { mensagem: "Não foi possível carregar.", aoTentarDeNovo: fn })
 *   Estados.limpar($alvo)   // tira o estado antes de renderizar dado de verdade
 *
 * $alvo deve ser o contêiner onde o estado substitui o conteúdo (não precisa ser uma tabela). */
(function () {
  "use strict";

  var ICONE_VAZIO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 12h8M8 8h8M8 16h5"/></svg>';
  var ICONE_ERRO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

  function montarBotaoAcao(acao) {
    if (!acao || !acao.rotulo) return null;
    return $("<button>").addClass("btn btn-secondary estado-acao").attr("type", "button")
      .text(acao.rotulo).on("click", acao.aoClicar || function () {});
  }

  function vazio($alvo, opts) {
    opts = opts || {};
    var titulo = opts.titulo || (opts.filtrado ? "Nenhum resultado para este filtro" : "Nenhum registro encontrado");
    var dica = opts.dica || (opts.filtrado ? "Tente ajustar ou limpar os filtros." : "");

    var $bloco = $("<div>").addClass("estado-vazio");
    $("<span>").addClass("estado-icone").html(ICONE_VAZIO).appendTo($bloco);
    $("<p>").addClass("estado-titulo").text(titulo).appendTo($bloco);
    if (dica) $("<p>").addClass("estado-dica").text(dica).appendTo($bloco);
    var $btn = montarBotaoAcao(opts.acao);
    if ($btn) $bloco.append($btn);

    $alvo.empty().append($bloco);
    return $bloco;
  }

  function erro($alvo, opts) {
    opts = opts || {};
    var $bloco = $("<div>").addClass("estado-erro").attr("role", "alert");
    $("<span>").addClass("estado-icone").html(ICONE_ERRO).appendTo($bloco);
    $("<p>").addClass("estado-titulo").text(opts.mensagem || "Não foi possível carregar os dados.").appendTo($bloco);
    if (opts.aoTentarDeNovo) {
      $("<button>").addClass("btn btn-secondary estado-acao").attr("type", "button")
        .text("Tentar de novo").on("click", opts.aoTentarDeNovo).appendTo($bloco);
    }
    $alvo.empty().append($bloco);
    return $bloco;
  }

  function limpar($alvo) {
    $alvo.find("> .estado-vazio, > .estado-erro").remove();
    return $alvo;
  }

  window.Estados = { vazio: vazio, erro: erro, limpar: limpar };
})();
