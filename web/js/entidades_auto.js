/* Aba "Entidades automatizadas" de web/ia_mesa_inquerito.html: biblioteca de padrões
 * determinísticos sobre documentos genéricos (api/rotas/entidades_auto.py). Ler a
 * biblioteca vale pra qualquer usuário aprovado; criar/editar/remover é só admin —
 * daí os botões condicionais. Inclui o executor por inquérito (job com polling). */
(function () {
  "use strict";

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function truncar(texto, max) { return AnalyserUtils.truncar(texto, max); }

  var $card = $("#auto-card");
  var $semPermissao = $("#auto-sem-permissao");
  var $lista = $("#auto-lista");
  var $vazio = $("#auto-vazio");
  var $novoBtn = $("#abrir-novo-auto-btn");
  var $execCard = $("#auto-exec-card");

  var $modal = $("#modal-auto");
  var $modalTitulo = $("#modal-auto-titulo");
  var $form = $("#auto-form");
  var $nome = $("#auto-nome-input");
  var $tpAchado = $("#auto-tp-achado-select");
  var $categoria = $("#auto-categoria-input");
  var $descricao = $("#auto-descricao-input");
  var $regex = $("#auto-regex-input");
  var $grupo = $("#auto-grupo-input");
  var $validador = $("#auto-validador-select");
  var $normalizacao = $("#auto-normalizacao-select");
  var $status = $("#auto-status");
  var $salvarBtn = $("#auto-salvar-btn");

  var $execInquerito = $("#auto-exec-inquerito");
  var $execBtn = $("#auto-exec-btn");
  var $execStatus = $("#auto-exec-status");
  var $execLista = $("#auto-exec-lista");

  var idEditando = null;
  var ehAdmin = false;
  var pollTimer = null;

  var ROTULO_TP = {
    cpf: "CPF", cnpj: "CNPJ", telefone: "Telefone", email: "E-mail", valor: "Valor",
    conta: "Conta", agencia: "Agência", processo: "Processo", procedimento: "Procedimento",
    oab: "OAB", placa_veiculo: "Placa", documento: "Documento"
  };

  function resumoDefinicao(p) {
    var d = p.tx_definicao || {};
    var partes = [];
    partes.push("Regex: " + truncar(d.padrao || "", 100));
    if (d.grupo) partes.push("grupo " + d.grupo);
    if (d.validador && d.validador !== "nenhum") partes.push("valida " + d.validador);
    return partes.join(" · ");
  }

  function renderPadrao(p) {
    var $item = $("<div>").addClass("investigacao-card");

    var $info = $("<div>").addClass("investigacao-info").appendTo($item);
    var $tituloLinha = $("<div>").css({ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }).appendTo($info);
    $("<strong>").text(p.no_padrao).appendTo($tituloLinha);
    $("<span>").addClass("badge badge-rodando").text(ROTULO_TP[p.tp_achado] || p.tp_achado).appendTo($tituloLinha);
    if (p.no_categoria) $("<span>").addClass("badge badge-neutro").text(p.no_categoria).appendTo($tituloLinha);
    $("<span>").addClass("badge " + (p.in_ativo ? "badge-ok" : "badge-erro")).text(p.in_ativo ? "Ativo" : "Inativo").appendTo($tituloLinha);

    if (p.ds_padrao) $("<span>").text(p.ds_padrao).appendTo($info);
    $("<span>").text(resumoDefinicao(p)).appendTo($info);
    $("<span>").text("Criado por " + (p.criado_por || "—") + " · " + formatarDataHora(p.dt_cadastro)).appendTo($info);

    if (ehAdmin) {
      var $acoes = $("<div>").addClass("investigacao-actions").appendTo($item);
      $("<button>").addClass("btn btn-secondary").text("Editar").on("click", function () { abrirModal(p); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").text(p.in_ativo ? "Desativar" : "Ativar")
        .on("click", function () { alterarAtivo(p.id_auto_padrao, !p.in_ativo); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").text("Remover")
        .on("click", function () { removerPadrao(p.id_auto_padrao); }).appendTo($acoes);
    }

    return $item;
  }

  function carregarPadroes() {
    $.getJSON("/api/entidades-auto")
      .done(function (padroes) {
        $card.removeClass("hidden");
        $execCard.removeClass("hidden");
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
        $execCard.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  function alterarAtivo(idPadrao, ativo) {
    $.ajax({
      url: "/api/entidades-auto/" + idPadrao + "/ativo",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ ativo: ativo })
    }).done(carregarPadroes);
  }

  function removerPadrao(idPadrao) {
    Dialogo.confirmar({
      titulo: "Remover padrão",
      mensagem: "Remover este padrão automático? As detecções já feitas com ele somem junto.",
      rotuloOk: "Remover", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/entidades-auto/" + idPadrao, method: "DELETE" }).done(carregarPadroes);
    });
  }

  function abrirModal(padrao) {
    idEditando = padrao ? padrao.id_auto_padrao : null;
    $modalTitulo.text(padrao ? "Editar padrão automático" : "Novo padrão automático");
    var d = (padrao && padrao.tx_definicao) || {};

    $nome.val(padrao ? padrao.no_padrao : "");
    $tpAchado.val(padrao ? padrao.tp_achado : "cpf");
    $categoria.val(padrao ? padrao.no_categoria || "" : "");
    $descricao.val(padrao ? padrao.ds_padrao || "" : "");
    $regex.val(d.padrao || "");
    $grupo.val(d.grupo !== undefined ? d.grupo : 0);
    $validador.val(d.validador || "nenhum");
    $normalizacao.val(d.normalizacao || "digitos");

    $status.text("").removeClass("success error");
    $modal.removeClass("hidden");
  }

  function fecharModal() {
    $modal.addClass("hidden");
  }

  $novoBtn.on("click", function () { abrirModal(null); });
  $("#auto-fechar").on("click", fecharModal);
  $modal.on("click", function (e) { if (e.target === this) fecharModal(); });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("success error");
    $salvarBtn.prop("disabled", true);

    var corpo = JSON.stringify({
      nome: $.trim($nome.val()),
      tp_achado: $tpAchado.val(),
      categoria: $.trim($categoria.val()) || null,
      descricao: $.trim($descricao.val()) || null,
      definicao: {
        padrao: $regex.val(),
        grupo: parseInt($grupo.val(), 10) || 0,
        validador: $validador.val(),
        normalizacao: $normalizacao.val()
      }
    });
    var url = idEditando ? "/api/entidades-auto/" + idEditando : "/api/entidades-auto";

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

  // ---------- executor por inquérito (job com polling) ----------

  function renderExecucao(x) {
    var $item = $("<div>").addClass("investigacao-card");
    var $info = $("<div>").addClass("investigacao-info").appendTo($item);
    var $linha = $("<div>").css({ display: "flex", alignItems: "center", gap: "10px" }).appendTo($info);
    var rotulo = { pendente: "Pendente", processando: "Processando", concluido: "Concluído", erro: "Erro" }[x.cd_status] || x.cd_status;
    var badge = x.cd_status === "concluido" ? "badge-ok" : (x.cd_status === "erro" ? "badge-erro" : "badge-rodando");
    $("<span>").addClass("badge " + badge).text(rotulo).appendTo($linha);
    $("<span>").text((x.qt_deteccoes !== null && x.qt_deteccoes !== undefined ? x.qt_deteccoes + " detecções · " : "") +
      "iniciada por " + (x.iniciado_por || "—") + " · " + formatarDataHora(x.dt_inicio)).appendTo($info);
    if (x.tx_erro) $("<span>").text(x.tx_erro).appendTo($info);
    return $item;
  }

  function pararPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function consultarExecucoes(idInquerito, continuar) {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/extracao-auto")
      .done(function (lista) {
        $execLista.empty();
        if (!lista.length) {
          $execStatus.text("Nenhuma execução ainda.").removeClass("error").addClass("success");
          return;
        }
        var primeira = lista[0];
        if (primeira.cd_status === "pendente" || primeira.cd_status === "processando") {
          $execStatus.text("Extraindo… (" + (primeira.cd_status === "pendente" ? "na fila" : "processando") + ")").removeClass("error").addClass("success");
        } else if (primeira.cd_status === "concluido") {
          $execStatus.text("Concluída: " + (primeira.qt_deteccoes || 0) + " detecções.").removeClass("error").addClass("success");
          pararPoll();
        } else {
          $execStatus.text(primeira.tx_erro || "Erro na extração.").removeClass("success").addClass("error");
          pararPoll();
        }
        lista.slice(0, 5).forEach(function (x) { $execLista.append(renderExecucao(x)); });
        if (continuar && (primeira.cd_status === "pendente" || primeira.cd_status === "processando")) {
          pararPoll();
          pollTimer = setInterval(function () { consultarExecucoes(idInquerito, true); }, 3000);
        }
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao consultar execuções.";
        $execStatus.text(msg).removeClass("success").addClass("error");
        pararPoll();
      });
  }

  $execBtn.on("click", function () {
    var idInquerito = parseInt($execInquerito.val(), 10);
    if (!idInquerito) {
      $execStatus.text("Informe o ID do inquérito.").removeClass("success").addClass("error");
      return;
    }
    $execStatus.text("").removeClass("success error");
    $execBtn.prop("disabled", true);
    $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/extracao-auto", method: "POST" })
      .done(function () { consultarExecucoes(idInquerito, true); })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a extração.";
        $execStatus.text(msg).removeClass("success").addClass("error");
      })
      .always(function () { $execBtn.prop("disabled", false); });
  });

  // botões de escrita só aparecem para admin — o backend também barra, isto é só UX
  $.getJSON("/api/auth/eu").done(function (usuario) {
    ehAdmin = !!usuario.in_admin;
    $novoBtn.toggleClass("hidden", !ehAdmin);
    carregarPadroes();
  });
})();
