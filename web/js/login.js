/* Tela de login.
 *
 * As formas de entrar não são fixas no HTML: vêm de GET /api/auth/provedores, que já devolve os
 * rótulos e o motivo de indisponibilidade de cada uma. Com um provedor só, a faixa de abas nem
 * aparece — não faz sentido pedir escolha onde não há escolha.
 *
 * Um provedor pode vir com `ds_indisponivel` (API-PF sem URL configurada em Administração →
 * API-PF): a aba aparece desabilitada com o motivo abaixo, em vez de sumir e deixar o usuário
 * achando que a opção não existe.
 *
 * Depois do login pela API-PF, quem ainda não tem senha do PolícIA passa pela caixa bloqueante de
 * js/utils/primeiro_acesso.js antes de entrar no sistema. */
(function () {
  "use strict";

  var PROVEDOR_PADRAO = "POLICIA";

  var provedores = [];
  var escolhido = PROVEDOR_PADRAO;

  var $form = $("#login-form");
  var $login = $("#login-input");
  var $senha = $("#senha-input");
  var $status = $("#login-status");
  var $btn = $("#login-btn");

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function destinoAposLogin() {
    var next = paramUrl("next");
    // Só caminho relativo simples: um `next` com "//" ou "http:" viraria redirecionamento aberto
    // para fora do sistema, com a nossa tela de login como isca.
    if (next && /^[A-Za-z0-9_.-]+\.html(\?[^#]*)?$/.test(next)) return next;
    return "index.html";
  }

  function selecionar(codigo) {
    var provedor = provedores.filter(function (p) { return p.co_provedor === codigo; })[0];
    if (!provedor || provedor.ds_indisponivel) return;

    escolhido = codigo;
    $(".login-provedor").each(function () {
      $(this).toggleClass("active", $(this).attr("data-provedor") === codigo);
    });
    $status.text("").removeClass("error");
  }

  function montarProvedores(lista) {
    provedores = lista || [];
    if (!provedores.length) return;

    var disponiveis = provedores.filter(function (p) { return !p.ds_indisponivel; });
    // Escolha inicial: o primeiro que de fato funciona (o PolícIA vem primeiro da API). Se nenhum
    // funciona, o primeiro da lista — a tela fica com o aviso e o formulário desabilitado.
    escolhido = (disponiveis[0] || provedores[0]).co_provedor;

    if (provedores.length > 1) {
      var $faixa = $("#login-provedores").empty();
      provedores.forEach(function (p) {
        var $aba = $("<button>")
          .attr("type", "button")
          .addClass("login-provedor")
          .toggleClass("active", p.co_provedor === escolhido)
          .attr("data-provedor", p.co_provedor)
          .attr("title", p.ds_indisponivel || p.ds_provedor || "")
          .prop("disabled", !!p.ds_indisponivel)
          .text(p.no_provedor);
        $faixa.append($aba);
      });
      $faixa.removeClass("hidden");
    }

    var indisponiveis = provedores.filter(function (p) { return p.ds_indisponivel; });
    if (indisponiveis.length) {
      $("#login-aviso").removeClass("hidden").text(indisponiveis.map(function (p) {
        return p.no_provedor + ": " + p.ds_indisponivel;
      }).join(" "));
    }

    if (!disponiveis.length) {
      $("#login-form :input").prop("disabled", true);
    }
  }

  // já tem sessão válida? pula direto pro destino em vez de mostrar o formulário
  $.ajax({ url: "/api/auth/eu", method: "GET" }).done(function () {
    window.location.href = destinoAposLogin();
  });

  $.getJSON("/api/auth/provedores")
    .done(function (resposta) {
      montarProvedores(resposta && resposta.provedores);
    });

  $(document).on("click", ".login-provedor", function () {
    selecionar($(this).attr("data-provedor"));
  });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("error");
    $btn.prop("disabled", true).text("Entrando…");

    $.ajax({
      url: "/api/auth/login",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        login: $.trim($login.val()),
        senha: $senha.val(),
        provedor: escolhido || PROVEDOR_PADRAO
      })
    })
      .done(function (resposta) {
        // Primeiro acesso pela API-PF: a pessoa está autenticada, mas ainda não tem senha DESTE
        // sistema. Criar uma agora é o que garante o acesso quando a rede da PF estiver fora —
        // por isso a caixa vem antes de entrar, não depois.
        if (resposta && resposta.primeiro_acesso && window.PrimeiroAcesso) {
          window.PrimeiroAcesso.abrir(function () {
            window.location.href = destinoAposLogin();
          });
          return;
        }
        window.location.href = destinoAposLogin();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível entrar.";
        // O 429 traz Retry-After: dizer quanto falta é mais útil que "tente mais tarde".
        if (xhr.status === 429) {
          var segundos = parseInt(xhr.getResponseHeader("Retry-After") || "0", 10);
          if (segundos > 0) msg += " (cerca de " + Math.ceil(segundos / 60) + " min)";
        }
        $status.text(msg).addClass("error");
        $senha.val("").trigger("focus");
        $btn.prop("disabled", false).text("Entrar");
      });
  });
})();
