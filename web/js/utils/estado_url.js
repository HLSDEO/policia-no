/* Estado de filtro/página na URL — window.EstadoUrl.
 *
 * Hoje o estado de uma tela de listagem (busca, filtros, página atual) vive só em memória: sair
 * da tela e voltar, ou usar o botão Voltar do navegador, perde tudo — e a URL não pode ser
 * compartilhada entre investigadores apontando pra a mesma visão filtrada. Este módulo é o
 * utilitário genérico para consertar isso, seguindo a mesma convenção de query string que o
 * projeto já usa (?inquerito=, ?extracao=, ?rif=): grava/lê pares chave-valor na URL atual via
 * history.replaceState (não mexe no histórico do navegador nem recarrega a página) e devolve
 * um objeto simples para restaurar o estado no load.
 *
 *   // ao aplicar um filtro/mudar de página:
 *   EstadoUrl.salvar({ busca: "joão", tipo: "whatsapp", pagina: 2 });
 *   // valor "" / null / undefined / false remove a chave da URL, em vez de gravar vazio
 *
 *   // ao montar a tela, pra restaurar o que estava salvo:
 *   var estado = EstadoUrl.ler(["busca", "tipo", "pagina"]);
 *   // -> { busca: "joão", tipo: "whatsapp", pagina: "2" } (só as chaves presentes na URL)
 *
 * `salvar` só mexe nas chaves passadas — ?inquerito=/?extracao=/etc. já presentes na URL não são
 * tocados. Consomem este módulo: `tabela_paginada.js` (filtros + página, em todas as telas que
 * ele monta) e `contas_financeiras.js` (os filtros da lista de contas). */
(function () {
  "use strict";

  function ler(chaves) {
    var params = new URLSearchParams(window.location.search);
    var resultado = {};
    (chaves || []).forEach(function (chave) {
      var v = params.get(chave);
      if (v !== null && v !== "") resultado[chave] = v;
    });
    return resultado;
  }

  function salvar(valores) {
    var params = new URLSearchParams(window.location.search);
    Object.keys(valores || {}).forEach(function (chave) {
      var v = valores[chave];
      if (v === null || v === undefined || v === "" || v === false) {
        params.delete(chave);
      } else {
        params.set(chave, v);
      }
    });
    var query = params.toString();
    var novaUrl = window.location.pathname + (query ? "?" + query : "") + window.location.hash;
    history.replaceState(null, "", novaUrl);
  }

  window.EstadoUrl = { ler: ler, salvar: salvar };
})();
