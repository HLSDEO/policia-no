(function () {
  "use strict";

  var $corpo = $("#categorias-corpo");
  var $vazio = $("#categorias-vazio");

  var $modal = $("#modal-nova-categoria");
  var $form = $("#nova-categoria-form");
  var $status = $("#nova-categoria-status");
  var $btn = $("#nova-categoria-btn");

  function formatarData(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"))
      .toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function renderLinha(c) {
    var $tr = $("<tr>");
    $("<td>").text(c.termo || "—").appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-tag").text(c.rotulo_exibicao || "—")).appendTo($tr);
    $("<td>").append(
      $("<span>").addClass("badge " + (c.in_ativo ? "badge-ok" : "badge-erro"))
        .text(c.in_ativo ? "Ativo" : "Inativo")
    ).appendTo($tr);
    $("<td>").text(formatarData(c.criado_em)).appendTo($tr);

    var $acoes = $("<td>").appendTo($tr);
    $("<button>").addClass("btn btn-secondary").css({ padding: "6px 12px", fontSize: "12px", marginRight: "6px" })
      .text(c.in_ativo ? "Desativar" : "Ativar")
      .on("click", function () { alternarAtivo(c.id_categoria); })
      .appendTo($acoes);
    $("<button>").addClass("btn btn-danger").css({ padding: "6px 12px", fontSize: "12px" })
      .text("Excluir")
      .on("click", function () { excluirCategoria(c.id_categoria); })
      .appendTo($acoes);
    return $tr;
  }

  function carregar() {
    $.getJSON("/api/categorias-imagem")
      .done(function (lista) {
        $corpo.empty();
        if (!lista.length) {
          $vazio.show();
          return;
        }
        $vazio.hide();
        lista.forEach(function (c) { $corpo.append(renderLinha(c)); });
      })
      .fail(function () {
        $vazio.text("Erro ao carregar categorias.").show();
      });
  }

  function alternarAtivo(id) {
    $.ajax({
      url: "/api/categorias-imagem/" + id + "/alternar-ativo",
      method: "POST"
    }).done(carregar);
  }

  function excluirCategoria(id) {
    Dialogo.confirmar({ mensagem: "Excluir esta categoria?", perigoso: true }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/categorias-imagem/" + id,
        method: "DELETE"
      }).done(carregar);
    });
  }

  $("#abrir-nova-categoria-btn").on("click", function () {
    $form[0].reset();
    $status.text("").removeClass("success error");
    $modal.removeClass("hidden");
    $("#nova-categoria-termo").focus();
  });

  $("#nova-categoria-fechar").on("click", function () {
    $modal.addClass("hidden");
  });
  $modal.on("click", function (e) { if (e.target === this) $modal.addClass("hidden"); });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("success error");
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/categorias-imagem",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        termo: $.trim($("#nova-categoria-termo").val()),
        rotulo_exibicao: $.trim($("#nova-categoria-rotulo").val())
      })
    })
      .done(function () {
        carregar();
        $modal.addClass("hidden");
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar categoria.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $btn.prop("disabled", false);
      });
  });

  carregar();
})();
