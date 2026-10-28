(function () {
  "use strict";

  var $container = $("#grupos-container");
  var pollTimer = null;

  var ICONE_IMPORTAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

  // Ordem e rótulos das seções dentro de um card: extraídas primeiro (o dado de
  // fato coletado), referência depois (tabelas de apoio/domínio).
  var SECOES = [
    { schema: "dados_extraidos", label: "Extraídas" },
    { schema: "dados_referencia", label: "Referência" },
  ];

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  function renderTabelaSecao(tabelas) {
    var $table = $("<table>").addClass("grupo-tabela");
    tabelas.forEach(function (t) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(t.label).appendTo($tr);
      $("<td>")
        .addClass("col-registros" + (t.registros === null ? " sem-dado" : ""))
        .text(t.registros === null ? "tabela não criada" : formatarNumero(t.registros))
        .appendTo($tr);
      $table.append($tr);
    });
    return $table;
  }

  function renderGrupo(grupo) {
    var total = grupo.tabelas.reduce(function (acc, t) { return acc + (t.registros || 0); }, 0);

    var $card = $("<div>").addClass("grupo-card").attr("data-grupo", grupo.grupo);

    var $header = $("<div>").addClass("grupo-header").appendTo($card);
    $("<h2>").text(grupo.label).appendTo($header);
    $("<span>").addClass("grupo-total").text(formatarNumero(total) + " registros").appendTo($header);

    // Separa as tabelas por schema em seções (extraídas x referência). O subcabeçalho
    // de cada seção só aparece quando o card tem mais de uma — cards de um tipo só
    // continuam com a lista corrida, sem rótulo redundante.
    var secoesPresentes = SECOES
      .map(function (s) {
        return { label: s.label, tabelas: grupo.tabelas.filter(function (t) { return (t.schema || "").toLowerCase() === s.schema; }) };
      })
      .filter(function (s) { return s.tabelas.length > 0; });

    if (secoesPresentes.length > 1) {
      var $secoes = $("<div>").addClass("grupo-secoes").appendTo($card);
      secoesPresentes.forEach(function (s) {
        var subtotal = s.tabelas.reduce(function (acc, t) { return acc + (t.registros || 0); }, 0);
        var $label = $("<div>").addClass("grupo-secao-label").appendTo($secoes);
        $("<span>").text(s.label).appendTo($label);
        $("<span>").addClass("grupo-secao-total").text(formatarNumero(subtotal)).appendTo($label);
        renderTabelaSecao(s.tabelas).appendTo($secoes);
      });
    } else {
      renderTabelaSecao(grupo.tabelas).appendTo($card);
    }

    var $footer = $("<div>").addClass("grupo-footer").appendTo($card);
    var $btn = $("<button>")
      .addClass("btn btn-primary btn-importar")
      .attr("type", "button")
      .attr("data-grupo", grupo.grupo);

    if (grupo.rodando) {
      $btn.prop("disabled", true).html('<span class="spinner"></span> Importando…');
      $("<span>").addClass("badge badge-rodando").html('<span class="spinner"></span> Em andamento').appendTo($footer.prepend($btn));
    } else {
      $btn.html(ICONE_IMPORTAR + " Importar").appendTo($footer);
      if (grupo.ultimo_resultado) {
        var ok = grupo.ultimo_resultado.ok;
        $("<span>")
          .addClass("badge " + (ok ? "badge-ok" : "badge-erro"))
          .attr("title", grupo.ultimo_resultado.mensagem || "")
          .text(ok ? "Última importação OK" : "Falhou")
          .appendTo($footer);
      }
    }

    return $card;
  }

  function temGrupoRodando(grupos) {
    return grupos.some(function (g) { return g.rodando; });
  }

  function carregarStatus() {
    return $.getJSON("/api/ingestao/status").done(function (grupos) {
      $container.empty();
      grupos.forEach(function (g) {
        $container.append(renderGrupo(g));
      });
      agendarPoll(temGrupoRodando(grupos));
    });
  }

  function agendarPoll(ativo) {
    clearTimeout(pollTimer);
    if (ativo) {
      pollTimer = setTimeout(carregarStatus, 3000);
    }
  }

  $container.on("click", ".btn-importar", function () {
    var grupo = $(this).attr("data-grupo");
    var $btn = $(this);
    $btn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');

    $.ajax({
      url: "/api/ingestao/importar/" + encodeURIComponent(grupo),
      method: "POST"
    })
      .done(function () {
        carregarStatus();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar o import.";
        Dialogo.alerta({ titulo: "Erro", mensagem: msg });
        carregarStatus();
      });
  });

  carregarStatus();
})();
