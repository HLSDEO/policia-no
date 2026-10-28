/* Aba "Padrões de crime" de web/ia_mesa_inquerito.html (era a página padroes_crime.html,
 * unificada com "Prompts de entidade"). Ler a biblioteca vale pra qualquer usuário aprovado;
 * criar/editar/remover é só admin (api/rotas/padroes.py) — daí os botões condicionais. */
(function () {
  "use strict";

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function truncar(texto, max) { return AnalyserUtils.truncar(texto, max); }

  var $card = $("#padroes-card");
  var $semPermissao = $("#padroes-sem-permissao");
  var $lista = $("#padroes-lista");
  var $vazio = $("#padroes-vazio");
  var $novoBtn = $("#abrir-novo-padrao-btn");

  var $modal = $("#modal-padrao");
  var $modalTitulo = $("#modal-padrao-titulo");
  var $form = $("#padrao-form");
  var $nome = $("#padrao-nome-input");
  var $categoria = $("#padrao-categoria-input");
  var $descricao = $("#padrao-descricao-input");
  var $tipo = $("#padrao-tipo-select");
  var $termos = $("#padrao-termos-input");
  var $regex = $("#padrao-regex-input");
  var $subtipo = $("#padrao-subtipo-select");
  var $status = $("#padrao-status");
  var $salvarBtn = $("#padrao-salvar-btn");

  var idEditando = null;
  var ehAdmin = false;

  var ROTULO_TIPO = { lexico: "Léxico", regex: "Regex", temporal: "Temporal", estrutural: "Estrutural" };
  var BADGE_TIPO = { lexico: "badge-neutro", regex: "badge-rodando", temporal: "badge-ok", estrutural: "badge-neutro" };
  var ROTULO_ESTRUTURAL = {
    imei_compartilhado: "Mesmo IMEI, donos diferentes",
    triangulacao_financeira: "Triangulação financeira (ciclo A→B→C→A)"
  };

  // ---------- resumo legível da definição, por tipo ----------

  function resumoDefinicao(p) {
    var d = p.tx_definicao || {};
    if (p.tp_padrao === "lexico") {
      var termos = d.termos || [];
      return termos.length + " termo(s): " + truncar(termos.join(", "), 140);
    }
    if (p.tp_padrao === "regex") {
      return "Expressão: " + truncar(d.padrao || "", 140);
    }
    if (p.tp_padrao === "estrutural") {
      return ROTULO_ESTRUTURAL[d.subtipo] || ("Subtipo: " + (d.subtipo || "—"));
    }
    if (d.subtipo === "rajada") {
      return "Rajada: " + d.minimo_mensagens + "+ mensagens em " + d.janela_minutos + " min na mesma conversa";
    }
    return "Faixa de horário: " + d.minimo + "+ mensagens entre " + d.hora_inicio + "h e " + d.hora_fim + "h";
  }

  function renderPadrao(p) {
    var $item = $("<div>").addClass("investigacao-card");

    var $info = $("<div>").addClass("investigacao-info").appendTo($item);
    var $tituloLinha = $("<div>").css({ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }).appendTo($info);
    $("<strong>").text(p.no_padrao).appendTo($tituloLinha);
    $("<span>").addClass("badge " + (BADGE_TIPO[p.tp_padrao] || "badge-neutro")).text(ROTULO_TIPO[p.tp_padrao] || p.tp_padrao).appendTo($tituloLinha);
    if (p.no_categoria) $("<span>").addClass("badge badge-neutro").text(p.no_categoria).appendTo($tituloLinha);
    $("<span>").addClass("badge " + (p.in_ativo ? "badge-ok" : "badge-erro")).text(p.in_ativo ? "Ativo" : "Inativo").appendTo($tituloLinha);

    if (p.ds_padrao) $("<span>").text(p.ds_padrao).appendTo($info);
    $("<span>").text(resumoDefinicao(p)).appendTo($info);
    $("<span>").text("Criado por " + (p.criado_por || "—") + " · " + formatarDataHora(p.dt_cadastro)).appendTo($info);

    if (ehAdmin) {
      var $acoes = $("<div>").addClass("investigacao-actions").appendTo($item);
      // padrão estrutural tem definição fixa (subtipo), sem campo no formulário — editar
      // pelo modal apagaria o subtipo sem o admin perceber. Ativar/desativar/remover continuam.
      if (p.tp_padrao !== "estrutural") {
        $("<button>").addClass("btn btn-secondary").text("Editar").on("click", function () { abrirModal(p); }).appendTo($acoes);
      }
      $("<button>").addClass("btn btn-secondary").text(p.in_ativo ? "Desativar" : "Ativar")
        .on("click", function () { alterarAtivo(p.id_padrao, !p.in_ativo); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").text("Remover")
        .on("click", function () { removerPadrao(p.id_padrao); }).appendTo($acoes);
    }

    return $item;
  }

  function carregarPadroes() {
    $.getJSON("/api/padroes")
      .done(function (padroes) {
        $card.removeClass("hidden");
        $semPermissao.addClass("hidden");
        $lista.empty();
        if (!padroes.length) {
          $vazio.removeClass("hidden");
          return;
        }
        $vazio.addClass("hidden");
        padroes.forEach(function (p) { $lista.append(renderPadrao(p)); });
      })
      .fail(function () {
        $card.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  function alterarAtivo(idPadrao, ativo) {
    $.ajax({
      url: "/api/padroes/" + idPadrao + "/ativo",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ ativo: ativo })
    }).done(carregarPadroes);
  }

  function removerPadrao(idPadrao) {
    Dialogo.confirmar({
      titulo: "Remover padrão",
      mensagem: "Remover este padrão? As detecções já feitas com ele somem junto.",
      rotuloOk: "Remover", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/padroes/" + idPadrao, method: "DELETE" }).done(carregarPadroes);
    });
  }

  // ---------- modal com campos por tipo ----------

  function atualizarCampos() {
    var tipo = $tipo.val();
    $("#campo-lexico").toggleClass("hidden", tipo !== "lexico");
    $("#campo-regex").toggleClass("hidden", tipo !== "regex");
    $("#campo-temporal").toggleClass("hidden", tipo !== "temporal");
    if (tipo === "temporal") {
      var sub = $subtipo.val();
      $("#campo-madrugada").css("display", sub === "madrugada" ? "flex" : "none");
      $("#campo-rajada").css("display", sub === "rajada" ? "flex" : "none");
    }
  }

  function abrirModal(padrao) {
    idEditando = padrao ? padrao.id_padrao : null;
    $modalTitulo.text(padrao ? "Editar padrão" : "Novo padrão");
    var d = (padrao && padrao.tx_definicao) || {};

    $nome.val(padrao ? padrao.no_padrao : "");
    $categoria.val(padrao ? padrao.no_categoria || "" : "");
    $descricao.val(padrao ? padrao.ds_padrao || "" : "");
    $tipo.val(padrao ? padrao.tp_padrao : "lexico");
    $termos.val(d.termos ? d.termos.join("\n") : "");
    $regex.val(d.padrao || "");
    $subtipo.val(d.subtipo || "madrugada");
    $("#padrao-hora-inicio").val(d.hora_inicio !== undefined ? d.hora_inicio : 0);
    $("#padrao-hora-fim").val(d.hora_fim !== undefined ? d.hora_fim : 5);
    $("#padrao-minimo").val(d.minimo !== undefined ? d.minimo : 10);
    $("#padrao-janela").val(d.janela_minutos !== undefined ? d.janela_minutos : 10);
    $("#padrao-minimo-rajada").val(d.minimo_mensagens !== undefined ? d.minimo_mensagens : 20);

    $status.text("").removeClass("success error");
    atualizarCampos();
    $modal.removeClass("hidden");
  }

  function fecharModal() {
    $modal.addClass("hidden");
  }

  function montarDefinicao() {
    var tipo = $tipo.val();
    if (tipo === "lexico") {
      var termos = $termos.val().split("\n").map($.trim).filter(Boolean);
      return { termos: termos };
    }
    if (tipo === "regex") {
      return { padrao: $.trim($regex.val()) };
    }
    if ($subtipo.val() === "madrugada") {
      return {
        subtipo: "madrugada",
        hora_inicio: parseInt($("#padrao-hora-inicio").val(), 10),
        hora_fim: parseInt($("#padrao-hora-fim").val(), 10),
        minimo: parseInt($("#padrao-minimo").val(), 10)
      };
    }
    return {
      subtipo: "rajada",
      janela_minutos: parseInt($("#padrao-janela").val(), 10),
      minimo_mensagens: parseInt($("#padrao-minimo-rajada").val(), 10)
    };
  }

  $tipo.on("change", atualizarCampos);
  $subtipo.on("change", atualizarCampos);
  $novoBtn.on("click", function () { abrirModal(null); });
  $("#padrao-fechar").on("click", fecharModal);
  $modal.on("click", function (e) { if (e.target === this) fecharModal(); });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("success error");
    $salvarBtn.prop("disabled", true);

    var corpo = JSON.stringify({
      nome: $.trim($nome.val()),
      tp_padrao: $tipo.val(),
      categoria: $.trim($categoria.val()) || null,
      descricao: $.trim($descricao.val()) || null,
      definicao: montarDefinicao()
    });
    var url = idEditando ? "/api/padroes/" + idEditando : "/api/padroes";

    $.ajax({ url: url, method: "POST", contentType: "application/json", data: corpo })
      .done(function () {
        carregarPadroes();
        setTimeout(fecharModal, 300);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o padrão.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $salvarBtn.prop("disabled", false);
      });
  });

  // botões de escrita só aparecem para admin — o backend também barra, isto é só UX
  $.getJSON("/api/auth/eu").done(function (usuario) {
    ehAdmin = !!usuario.in_admin;
    $novoBtn.toggleClass("hidden", !ehAdmin);
    carregarPadroes();
  });
})();
