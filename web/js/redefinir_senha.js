/* Recuperação de senha — a mesma página cobre as três etapas, porque o usuário pode chegar aqui
 * por dois caminhos (link do e-mail, ou código que o administrador passou na mão):
 *
 *   1. pedir   — sem token: informa login/e-mail e recebe o link (só se houver SMTP).
 *   2. codigo  — tem o código mas não o link clicável (caso do token gerado pelo admin).
 *   3. senha   — token conferido no servidor: escolhe a senha nova.
 *
 * Com #token= na URL a etapa 1 é pulada e o token já vai direto para conferência.
 *
 * O token chega em FRAGMENTO (#token=), não em query string: fragmento não é enviado ao
 * servidor, então a credencial do link não fica no access_log do nginx nem no log da API.
 * A leitura em ?token= continua como fallback para links de e-mail antigos (pré-mudança),
 * ainda em caixas de entrada. A conferência é POST: o token nunca vai para a URL da request. */
(function () {
  "use strict";

  var tokenAtual = null;

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function tokenDaUrl() {
    var m = /(?:^|[#&])token=([^&]+)/.exec(window.location.hash || "");
    if (m) {
      try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
    }
    return paramUrl("token"); // links antigos (?token=) continuam valendo até expirarem
  }

  function mostrarEtapa(id) {
    $("#etapa-pedir, #etapa-sem-email, #etapa-codigo, #etapa-senha").addClass("hidden");
    $("#" + id).removeClass("hidden");
    // o atalho "já tenho um código" não faz sentido quando já se está usando um
    $("#link-codigo-wrap").toggleClass("hidden", id === "etapa-codigo" || id === "etapa-senha");
  }

  function mensagemErro(xhr, padrao) {
    return (xhr.responseJSON && xhr.responseJSON.detail) || padrao;
  }

  // ---------- Etapa 3: conferir o token e trocar a senha ----------

  function conferirToken(token, $status, $btn) {
    $.ajax({
      url: "/api/auth/redefinir/conferir",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ token: token })
    })
      .done(function (resp) {
        tokenAtual = token;
        $("#senha-hint").text("Escolha uma senha nova para " + resp.no_usuario + " (" + resp.no_login + ").");
        mostrarEtapa("etapa-senha");
        $("#senha-input").trigger("focus");
      })
      .fail(function (xhr) {
        // sem um formulário de origem (token veio da URL), joga o usuário na etapa do código
        // já com o aviso, em vez de deixar a tela vazia
        if (!$status || !$status.length) {
          mostrarEtapa("etapa-codigo");
          $("#codigo-status").text(mensagemErro(xhr, "Link inválido ou expirado.")).addClass("error");
          return;
        }
        $status.text(mensagemErro(xhr, "Código inválido ou expirado.")).addClass("error");
      })
      .always(function () {
        if ($btn && $btn.length) $btn.prop("disabled", false).text("Continuar");
      });
  }

  $("#senha-form").on("submit", function (e) {
    e.preventDefault();
    var $status = $("#senha-status");
    var $btn = $("#senha-btn");
    $status.text("").removeClass("error success");

    var senha = $("#senha-input").val();
    if (senha !== $("#senha2-input").val()) {
      $status.text("As duas senhas não são iguais.").addClass("error");
      return;
    }

    $btn.prop("disabled", true).text("Salvando…");
    $.ajax({
      url: "/api/auth/redefinir",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ token: tokenAtual, senha_nova: senha })
    })
      .done(function () {
        $("#senha-form").addClass("hidden");
        $("#senha-hint").text("Senha alterada. Você já pode entrar com a senha nova.").addClass("success");
      })
      .fail(function (xhr) {
        $status.text(mensagemErro(xhr, "Não foi possível salvar a senha.")).addClass("error");
        $btn.prop("disabled", false).text("Salvar senha");
      });
  });

  // ---------- Etapa 2: código colado à mão ----------

  $("#codigo-form").on("submit", function (e) {
    e.preventDefault();
    var $status = $("#codigo-status");
    var $btn = $("#codigo-btn");
    $status.text("").removeClass("error success");
    $btn.prop("disabled", true).text("Conferindo…");
    conferirToken($.trim($("#codigo-input").val()), $status, $btn);
  });

  $("#link-codigo").on("click", function (e) {
    e.preventDefault();
    mostrarEtapa("etapa-codigo");
    $("#codigo-input").trigger("focus");
  });

  // ---------- Etapa 1: pedir o link por e-mail ----------

  $("#pedir-form").on("submit", function (e) {
    e.preventDefault();
    var $status = $("#pedir-status");
    var $btn = $("#pedir-btn");
    $status.text("").removeClass("error success");
    $btn.prop("disabled", true).text("Enviando…");

    $.ajax({
      url: "/api/auth/esqueci",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ login_ou_email: $.trim($("#identificacao-input").val()) })
    })
      .done(function (resp) {
        // a resposta é a mesma exista ou não a conta (o servidor não conta qual é o caso) —
        // então a mensagem tem que ser redigida sem confirmar nem negar
        $status.text(resp.mensagem || "Pedido registrado.").addClass("success");
        $("#pedir-form").find("input, button").prop("disabled", true);
      })
      .fail(function (xhr) {
        $status.text(mensagemErro(xhr, "Não foi possível registrar o pedido.")).addClass("error");
        $btn.prop("disabled", false).text("Enviar link");
      });
  });

  // ---------- Entrada ----------

  var tokenUrl = tokenDaUrl();
  if (tokenUrl) {
    conferirToken(tokenUrl, null, null);
  } else {
    // sem SMTP não há o que pedir: mostra a orientação de falar com o administrador, mantendo
    // o atalho do código (o link gerado pelo admin continua valendo)
    $.ajax({ url: "/api/auth/recuperacao", method: "GET" })
      .done(function (resp) {
        mostrarEtapa(resp.email ? "etapa-pedir" : "etapa-sem-email");
      })
      .fail(function () {
        mostrarEtapa("etapa-pedir");
      });
  }
})();
