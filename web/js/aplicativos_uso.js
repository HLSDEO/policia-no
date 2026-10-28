/* Modal de uso de um aplicativo, aberto pela lista da tela de Aplicativos.
 *
 * O UFDR traz duas coisas sobre uso: o resumo por app (ApplicationUsage — tempo em primeiro
 * plano, aberturas) e o log evento a evento (AppsUsageLog — abriu, mandou pro fundo, fechou,
 * copiou). São 413 mil eventos nos oito aparelhos auditados, então não cabem numa tela só:
 * aqui o analista escolhe o app e vê os números gerais dele em cima e o detalhe paginado
 * embaixo, filtrado por esse app no servidor (?pacote=). */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $modal = $("#modal-uso-app");
  var $titulo = $("#uso-app-titulo");
  var $kpis = $("#uso-app-kpis");
  var $lista = $("#uso-app-lista");

  function fechar() {
    $modal.addClass("hidden");
    $lista.empty();
    $kpis.empty();
  }

  $("#uso-app-fechar").on("click", fechar);
  $modal.on("click", function (e) { if (e.target === this) fechar(); });
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && !$modal.hasClass("hidden")) fechar();
  });

  function kpi(rotulo, valor, dica) {
    var $c = $("<div>").addClass("uso-kpi");
    $("<span>").addClass("uso-kpi-valor").text(valor).appendTo($c);
    $("<span>").addClass("uso-kpi-rotulo").text(rotulo).appendTo($c);
    if (dica) $c.attr("title", dica);
    return $c;
  }

  /* Os números gerais vêm do resumo por app. Quando o aparelho não emite esse resumo (é o
   * caso de vários iPhones), o total de eventos do log ainda diz quanto o app foi usado. */
  function carregarKpis(pacote) {
    $kpis.empty().append($("<span>").addClass("arq-pag-info").text("Carregando…"));

    var resumo = $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/ufdr/uso-aplicativo?extracao=" + encodeURIComponent(idExtracao) +
      "&pacote=" + encodeURIComponent(pacote) + "&limite=1");
    var eventos = $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/ufdr/uso-apps?extracao=" + encodeURIComponent(idExtracao) +
      "&pacote=" + encodeURIComponent(pacote) + "&limite=1");

    $.when(resumo, eventos).done(function (respResumo, respEventos) {
      var r = (respResumo[0].itens || [])[0];
      var totalEventos = respEventos[0].total || 0;
      $kpis.empty();
      $kpis.append(kpi("Eventos de uso", UfdrDominio.formatarNumero(totalEventos),
        "Cada abertura, ida para segundo plano ou fechamento registrado pelo aparelho"));
      if (r) {
        $kpis.append(kpi("Em primeiro plano", UfdrDominio.formatarDuracao(r.qt_tempo_ativo)));
        $kpis.append(kpi("Em segundo plano", UfdrDominio.formatarDuracao(r.qt_tempo_fundo)));
        $kpis.append(kpi("Aberturas", UfdrDominio.formatarNumero(r.qt_aberturas)));
        $kpis.append(kpi("Ativações", UfdrDominio.formatarNumero(r.qt_ativacoes)));
        $kpis.append(kpi("Última abertura", UfdrDominio.formatarDataHora(r.dt_ultima_abertura)));
        $kpis.append(kpi("Duração do último uso", UfdrDominio.formatarDuracao(r.qt_ultima_duracao)));
      } else if (!totalEventos) {
        $kpis.append($("<p>").addClass("empty-hint")
          .text("Este aparelho não registrou uso deste aplicativo."));
      } else {
        $kpis.append($("<p>").addClass("empty-hint").css("grid-column", "1 / -1")
          .text("O aparelho não traz o resumo de tempo de uso — só o log de eventos abaixo."));
      }
    }).fail(function () {
      $kpis.empty().append($("<p>").addClass("empty-hint")
        .text("Não foi possível carregar os números de uso."));
    });
  }

  function abrir(pacote, nome) {
    if (!idInquerito || !idExtracao) return;
    $titulo.text(nome && nome !== pacote ? nome + " (" + pacote + ")" : pacote);
    $modal.removeClass("hidden");
    carregarKpis(pacote);
    $lista.empty();
    // a lista é o mesmo componente das demais telas, preso a este app pelo filtro do servidor
    UfdrDominio.montar({
      seletor: "#uso-app-lista",
      abas: UfdrDominio.abasDe(["uso-apps"]),
      filtroExtra: { pacote: pacote },
      semTotal: true
    });
  }

  window.UsoApp = { abrir: abrir };
})();
