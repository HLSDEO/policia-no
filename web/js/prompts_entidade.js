/* Aba "Prompts de entidade" de web/ia_mesa_inquerito.html (era a página prompts_entidade.html,
 * unificada com "Padrões de crime"). Toda a leitura/escrita é admin-only no backend
 * (api/rotas/prompts_entidade.py) — quando o GET falha, o card some e sobra só o aviso. */
(function () {
  "use strict";

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function truncar(texto, max) { return AnalyserUtils.truncar(texto, max); }

  var $adminCard = $("#prompts-card");
  var $semPermissao = $("#prompts-sem-permissao");
  var $lista = $("#prompts-lista");
  var $vazio = $("#prompts-vazio");

  var $modal = $("#modal-prompt");
  var $modalTitulo = $("#modal-prompt-titulo");
  var $form = $("#prompt-form");
  var $tituloInput = $("#prompt-titulo-input");
  var $escopoSelect = $("#prompt-escopo-select");
  var $textoInput = $("#prompt-texto-input");
  var $status = $("#prompt-status");
  var $salvarBtn = $("#prompt-salvar-btn");

  var idEditando = null; // null = criando um novo

  var ROTULO_ESCOPO = { investigacao: "Investigação", documento: "Documento diverso" };

  function badgeAtivo(ativo) {
    return $("<span>").addClass("badge " + (ativo ? "badge-ok" : "badge-erro")).text(ativo ? "Ativo" : "Inativo");
  }

  function badgeEscopo(escopo) {
    return $("<span>").addClass("badge " + (escopo === "documento" ? "badge-rodando" : "badge-neutro")).text(ROTULO_ESCOPO[escopo] || escopo);
  }

  function renderPrompt(p) {
    var $card = $("<div>").addClass("investigacao-card");

    var $info = $("<div>").addClass("investigacao-info").appendTo($card);
    var $tituloLinha = $("<div>").css({ display: "flex", alignItems: "center", gap: "10px" }).appendTo($info);
    $("<strong>").text(p.no_titulo).appendTo($tituloLinha);
    $tituloLinha.append(badgeEscopo(p.tp_escopo));
    $tituloLinha.append(badgeAtivo(p.in_ativo));
    $("<span>").text(truncar(p.tx_prompt, 160)).appendTo($info);
    $("<span>").text("Criado por " + (p.criado_por || "—") + " · " + formatarDataHora(p.dt_cadastro)).appendTo($info);

    var $acoes = $("<div>").addClass("investigacao-actions").appendTo($card);
    $("<button>").addClass("btn btn-secondary").text("Editar").on("click", function () { abrirModal(p); }).appendTo($acoes);
    $("<button>").addClass("btn btn-secondary").text(p.in_ativo ? "Desativar" : "Ativar")
      .on("click", function () { alterarAtivo(p.id_prompt, !p.in_ativo); }).appendTo($acoes);
    $("<button>").addClass("btn btn-secondary").text("Remover")
      .on("click", function () { removerPrompt(p.id_prompt); }).appendTo($acoes);

    return $card;
  }

  function carregarPrompts() {
    $.getJSON("/api/prompts-entidade")
      .done(function (lista) {
        $adminCard.removeClass("hidden");
        $semPermissao.addClass("hidden");
        $lista.empty();
        if (!lista.length) {
          $vazio.removeClass("hidden");
          return;
        }
        $vazio.addClass("hidden");
        lista.forEach(function (p) { $lista.append(renderPrompt(p)); });
      })
      .fail(function () {
        $adminCard.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  function alterarAtivo(idPrompt, ativo) {
    $.ajax({
      url: "/api/prompts-entidade/" + idPrompt + "/ativo",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ ativo: ativo })
    }).done(carregarPrompts);
  }

  function removerPrompt(idPrompt) {
    $.ajax({ url: "/api/prompts-entidade/" + idPrompt, method: "DELETE" }).done(carregarPrompts);
  }

  function abrirModal(prompt) {
    idEditando = prompt ? prompt.id_prompt : null;
    $modalTitulo.text(prompt ? "Editar prompt" : "Novo prompt");
    $tituloInput.val(prompt ? prompt.no_titulo : "");
    $escopoSelect.val(prompt ? prompt.tp_escopo : "investigacao");
    $textoInput.val(prompt ? prompt.tx_prompt : "");
    $status.text("").removeClass("success error");
    $modal.removeClass("hidden");
  }

  function fecharModal() {
    $modal.addClass("hidden");
  }

  $("#abrir-novo-prompt-btn").on("click", function () { abrirModal(null); });
  $("#prompt-fechar").on("click", fecharModal);
  $modal.on("click", function (e) { if (e.target === this) fecharModal(); });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("success error");
    $salvarBtn.prop("disabled", true);

    var corpo = JSON.stringify({
      titulo: $.trim($tituloInput.val()),
      texto: $.trim($textoInput.val()),
      tp_escopo: $escopoSelect.val()
    });
    var url = idEditando ? "/api/prompts-entidade/" + idEditando : "/api/prompts-entidade";

    $.ajax({ url: url, method: "POST", contentType: "application/json", data: corpo })
      .done(function () {
        carregarPrompts();
        setTimeout(fecharModal, 300);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o prompt.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $salvarBtn.prop("disabled", false);
      });
  });

  carregarPrompts();
})();
