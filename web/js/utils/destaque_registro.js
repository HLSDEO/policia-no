/* Realce de um registro vindo de deep-link — window.DestaqueRegistro.
 *
 * O link da evidência em busca_entidades aponta para a tela da fonte com ?destacar=<id> do
 * registro achado. As telas de listagem (TabelaPaginada já faz isto sozinha; as telas que renderizam
 * a própria tabela usam este utilitário) chamam `aplicar` depois de desenhar as linhas: a linha
 * marcada com data-id="<id>" rola para o centro e ganha a classe `.tp-destacada` (mesma do
 * TabelaPaginada). A chave sai da URL depois do primeiro realce para não reaparecer ao paginar. */
(function () {
  "use strict";

  function idDaUrl() {
    var p = new URLSearchParams(window.location.search);
    var v = p.get("destacar");
    return v === null || v === "" ? null : v;
  }

  function aplicar($alvo, id) {
    if (id == null || id === "") return;
    var $linha = $alvo.find('[data-id="' + id + '"]');
    if (!$linha.length) return;
    $linha.addClass("tp-destacada");
    $linha[0].scrollIntoView({ block: "center" });
    if (window.EstadoUrl) window.EstadoUrl.salvar({ destacar: null });
  }

  window.DestaqueRegistro = { idDaUrl: idDaUrl, aplicar: aplicar };
})();
