(function () {
  "use strict";

  var $form = $("#cadastro-form");
  var $nome = $("#nome-input");
  var $login = $("#login-input");
  var $senha = $("#senha-input");
  var $email = $("#email-input");
  var $status = $("#cadastro-status");
  var $btn = $("#cadastro-btn");

  // já tem sessão válida? não faz sentido oferecer cadastro de novo
  $.ajax({ url: "/api/auth/eu", method: "GET" }).done(function () {
    window.location.href = "index.html";
  });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("error success");
    $btn.prop("disabled", true).text("Criando…");

    $.ajax({
      url: "/api/auth/cadastro",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        nome: $.trim($nome.val()),
        login: $.trim($login.val()),
        senha: $senha.val(),
        email: $.trim($email.val()) || null
      })
    })
      .done(function () {
        // já fica logado (a rota abre sessão), só que sem aprovação — auth.js mostra a tela de espera
        window.location.href = "index.html";
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível criar a conta.";
        $status.text(msg).addClass("error");
        $btn.prop("disabled", false).text("Criar conta");
      });
  });
})();
