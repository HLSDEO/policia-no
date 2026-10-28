/* Primeiro acesso de quem entrou pela API-PF — window.PrimeiroAcesso.
 *
 * Quem entra pela API-PF tem o cadastro criado aqui na hora, mas nasce **sem senha do PolícIA**:
 * a PF valida a senha dela, e essa senha nunca passa por este servidor. Sem uma senha daqui, a
 * pessoa só consegue entrar enquanto houver rede da PF.
 *
 * Por isso esta caixa é **bloqueante**: não tem Cancelar, não fecha no Esc nem no clique fora. As
 * duas saídas são criar a senha ou sair. É a única caixa do sistema que se comporta assim, e é
 * deliberado — um "depois eu faço" aqui vira alguém sem acesso no dia em que a rede cair.
 *
 * Usada em dois momentos, com o mesmo código:
 *   - logo depois do login pela API-PF (js/login.js), antes de entrar no sistema;
 *   - em qualquer página interna (js/utils/auth.js), para quem fechou o navegador no meio.
 *
 * Carregar depois de jQuery. */
(function () {
  "use strict";

  var TAMANHO_MINIMO = 8;
  var $modal = null;

  function fechar() {
    if ($modal) $modal.remove();
    $modal = null;
  }

  function sair() {
    // .always: se o logout falhar, ainda leva à tela de login — deixar a pessoa presa numa caixa
    // que ela pediu para abandonar seria pior.
    $.ajax({ url: "/api/auth/logout", method: "POST" }).always(function () {
      window.location.href = "login.html";
    });
  }

  function salvar(aoConcluir) {
    var $status = $("#primeiro-acesso-status").removeClass("error success").text("");
    var nova = $("#primeiro-acesso-senha").val();

    // A conferência das duas digitações é do front mesmo: a API recebe uma senha só, e mandar as
    // duas para ela não acrescentaria nada além de trafegar a senha duas vezes.
    if (nova !== $("#primeiro-acesso-confirma").val()) {
      $status.text("As duas senhas não são iguais.").addClass("error");
      $("#primeiro-acesso-confirma").val("").trigger("focus");
      return;
    }
    if ((nova || "").length < TAMANHO_MINIMO) {
      $status.text("A senha precisa ter pelo menos " + TAMANHO_MINIMO + " caracteres.").addClass("error");
      return;
    }

    var $botao = $("#primeiro-acesso-salvar").prop("disabled", true);
    $.ajax({
      url: "/api/auth/primeiro-acesso",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ senha_nova: nova })
    })
      .done(function () {
        fechar();
        if (window.Toast) window.Toast.sucesso("Senha criada. Você já pode entrar pelo PolícIA.");
        if (aoConcluir) aoConcluir();
      })
      .fail(function (xhr) {
        $botao.prop("disabled", false);
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível criar a senha.";
        $status.text(msg).addClass("error");
      });
  }

  /* `aoConcluir` roda depois de a senha ser gravada. Em js/login.js é o redirecionamento para o
     sistema; nas páginas internas é só fechar e seguir. */
  function abrir(aoConcluir) {
    if ($modal) return;

    $modal = $(
      '<div class="modal-overlay" id="modal-primeiro-acesso">' +
        '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="primeiro-acesso-titulo" style="width:min(440px, 92vw);">' +
          '<div class="modal-header">' +
            '<h2 id="primeiro-acesso-titulo">Primeiro acesso</h2>' +
          "</div>" +
          '<div class="modal-body">' +
            '<p class="card-hint">Você entrou pela API-PF e seu cadastro foi criado no PolícIA. ' +
              "Crie agora uma senha deste sistema para conseguir entrar também quando não houver " +
              "acesso à rede da PF. Ela é sua, fica só neste servidor e não tem relação com a sua " +
              "senha da PF. A sua delegacia será vinculada por um administrador depois.</p>" +
            '<form id="form-primeiro-acesso" autocomplete="off">' +
              '<div class="field">' +
                '<label for="primeiro-acesso-senha">Senha do PolícIA</label>' +
                '<input type="password" id="primeiro-acesso-senha" autocomplete="new-password" minlength="' +
                  TAMANHO_MINIMO + '" required>' +
                '<p class="field-help">Pelo menos ' + TAMANHO_MINIMO + " caracteres.</p>" +
              "</div>" +
              '<div class="field">' +
                '<label for="primeiro-acesso-confirma">Repita a senha</label>' +
                '<input type="password" id="primeiro-acesso-confirma" autocomplete="new-password" required>' +
              "</div>" +
              '<div class="form-actions">' +
                '<button type="submit" class="btn btn-primary" id="primeiro-acesso-salvar">Criar senha</button>' +
                '<button type="button" class="btn btn-secondary" id="primeiro-acesso-sair">Sair</button>' +
              "</div>" +
              '<p class="form-status" id="primeiro-acesso-status"></p>' +
            "</form>" +
          "</div>" +
        "</div>" +
      "</div>"
    );

    $modal.on("click", "#primeiro-acesso-sair", sair);
    $modal.on("submit", "#form-primeiro-acesso", function (e) {
      e.preventDefault();
      salvar(aoConcluir);
    });

    $("body").append($modal);
    $("#primeiro-acesso-senha").trigger("focus");
  }

  window.PrimeiroAcesso = { abrir: abrir, TAMANHO_MINIMO: TAMANHO_MINIMO };
})();
