/* Administração → API-PF: configuração da entrada pela API da Polícia Federal.
 *
 * O formulário não é fixo no HTML: os campos vêm de GET /api/config/api-pf, que devolve o
 * catálogo de api/funcoes/api_pf_utils.py (rótulo, tipo, ajuda, obrigatório) já com o valor
 * atual do .env. Campo novo lá aparece aqui sem editar este arquivo.
 *
 * O client_secret nunca chega em texto claro: vem só a máscara. Salvar com ele em branco mantém
 * o que já está gravado — senão salvar o formulário sem redigitar o segredo o apagaria.
 */
(function () {
  "use strict";

  var $card = $("#api-pf-card");
  var $semPermissao = $("#sem-permissao-hint");
  var $campos = $("#api-pf-campos");
  var $status = $("#api-pf-status");

  var catalogo = [];

  function idCampo(chave) {
    return "campo-" + chave.toLowerCase().replace(/_/g, "-");
  }

  function montarCampo(campo) {
    var id = idCampo(campo.chave);
    var $bloco = $("<div>").addClass("field");

    $("<label>").attr("for", id)
      .text(campo.rotulo + (campo.obrigatorio ? " *" : ""))
      .appendTo($bloco);

    if (campo.tipo === "booleano") {
      var $select = $("<select>").attr("id", id);
      $("<option>").val("sim").text("Sim").appendTo($select);
      $("<option>").val("nao").text("Não").appendTo($select);
      $select.val((campo.valor || "sim").toLowerCase() === "nao" ? "nao" : "sim");
      $select.appendTo($bloco);
    } else {
      var $input = $("<input>")
        .attr("id", id)
        .attr("type", campo.secreto ? "password" : (campo.tipo === "numero" ? "number" : "text"))
        .val(campo.secreto ? "" : (campo.valor || ""));
      if (campo.secreto && campo.preenchido) {
        // A máscara vai no placeholder, não no valor: assim o campo continua vazio de verdade e
        // salvar sem tocá-lo significa "mantenha o segredo atual".
        $input.attr("placeholder", campo.mascara + " (salvo)");
      } else if (campo.padrao && !campo.secreto) {
        $input.attr("placeholder", campo.padrao);
      }
      if (campo.tipo === "numero") $input.attr("min", 1).attr("max", 300);
      $input.appendTo($bloco);
    }

    var ajuda = campo.ajuda || "";
    if (campo.secreto && campo.preenchido) {
      ajuda = "Já existe um valor salvo. Deixe em branco para mantê-lo.";
    }
    if (ajuda) $("<p>").addClass("field-help").text(ajuda).appendTo($bloco);

    return $bloco;
  }

  function render(resposta) {
    catalogo = (resposta && resposta.campos) || [];
    $campos.empty();
    catalogo.forEach(function (campo) { $campos.append(montarCampo(campo)); });
    $("#api-pf-desligada").toggle(!(resposta && resposta.configurado));
    $card.removeClass("hidden");
    $semPermissao.addClass("hidden");
  }

  function carregar() {
    $.getJSON("/api/config/api-pf")
      .done(render)
      .fail(function () {
        // 403 (não-admin): a tela fica escondida mesmo, não é erro pro usuário comum.
        $card.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  function valoresDoFormulario() {
    var valores = {};
    catalogo.forEach(function (campo) {
      var valor = $("#" + idCampo(campo.chave)).val();
      valores[campo.chave] = valor == null ? "" : String(valor).trim();
    });
    return valores;
  }

  function salvar(e) {
    e.preventDefault();
    $status.removeClass("error success").text("");
    var $btn = $("#btn-salvar").prop("disabled", true);

    $.ajax({
      url: "/api/config/api-pf",
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({ valores: valoresDoFormulario() })
    })
      .done(function (resposta) {
        render(resposta);
        $status.addClass("success").text("Configuração salva.");
        if (window.Toast) window.Toast.sucesso("Configuração da API-PF salva.");
      })
      .fail(function (xhr) {
        $status.addClass("error").text(
          (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar."
        );
      })
      .always(function () {
        $btn.prop("disabled", false);
      });
  }

  // ---------- Testar conexão ----------

  function abrirTeste() {
    $("#testar-login").val("");
    $("#testar-senha").val("");
    $("#testar-status").removeClass("error success").text("");
    $("#testar-resultado").addClass("hidden").empty();
    $("#modal-testar").removeClass("hidden");
    $("#testar-login").trigger("focus");
  }

  function fecharTeste() {
    // A senha digitada aqui é de uma pessoa de verdade: não fica no DOM depois de fechar.
    $("#testar-senha").val("");
    $("#modal-testar").addClass("hidden");
  }

  function mostrarResultado(resposta) {
    var $res = $("#testar-resultado").empty().removeClass("hidden");
    if (!resposta.ok) {
      $("<p>").addClass("form-status error").text(resposta.detalhe || "A API-PF recusou o teste.").appendTo($res);
      return;
    }
    var id = resposta.identidade || {};
    $("<p>").addClass("form-status success").text("A API-PF respondeu com sucesso.").appendTo($res);
    var $lista = $("<ul>").css({ margin: "8px 0 0", paddingLeft: "18px", fontSize: "13px" }).appendTo($res);
    $("<li>").text("Login: " + (id.no_login || "—")).appendTo($lista);
    $("<li>").text("Nome: " + (id.no_usuario || "—")).appendTo($lista);
    $("<li>").text("E-mail: " + (id.no_email || "—")).appendTo($lista);
    $("<p>").addClass("field-help")
      .text("É com esses dados que o cadastro do PolícIA é criado no primeiro login desta pessoa.")
      .appendTo($res);
  }

  function testar(e) {
    e.preventDefault();
    var $st = $("#testar-status").removeClass("error success").text("");
    var $btn = $("#testar-btn").prop("disabled", true);
    $("#testar-resultado").addClass("hidden").empty();

    $.ajax({
      url: "/api/config/api-pf/testar",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ login: $.trim($("#testar-login").val()), senha: $("#testar-senha").val() })
    })
      .done(mostrarResultado)
      .fail(function (xhr) {
        $st.addClass("error").text(
          (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível testar."
        );
      })
      .always(function () {
        $btn.prop("disabled", false);
      });
  }

  $("#api-pf-form").on("submit", salvar);
  $("#btn-restaurar").on("click", carregar);
  $("#btn-testar").on("click", abrirTeste);
  $("#testar-fechar").on("click", fecharTeste);
  $("#modal-testar").on("click", function (e) { if (e.target === this) fecharTeste(); });
  $("#testar-form").on("submit", testar);

  carregar();
})();
