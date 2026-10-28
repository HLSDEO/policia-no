(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $tipo = $("#filtro-tipo");
  var $ordenar = $("#filtro-ordenar");
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");

  if (!idInquerito) {
    Estados.erro($("#tabela"), { mensagem: "Parâmetros inválidos na URL." });
    return;
  }

  function paramsFiltros() {
    var p = {};
    if (idExtracao) p.extracao = idExtracao;
    var busca = $busca.val().trim();
    if (busca) p.busca = busca;
    if ($tipo.val()) p.tipo = $tipo.val();
    if ($ordenar.val() && $ordenar.val() !== "recentes") p.ordenar = $ordenar.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  function celulaMultilogin(r) {
    if (!r.in_multilogin) return "—";
    return $("<span>").css({ color: "#d97706", fontWeight: 600 }).text("multilogin");
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/acessos-agregados",
    idDe: function (r) { return r.id_acesso_agr; },
    params: paramsFiltros,
    colunas: [
      { rotulo: "Identificador", render: function (r) { return r.tx_identificador || "—"; } },
      { rotulo: "Tipo", render: function (r) { return r.tp_identificador || "—"; } },
      { rotulo: "Canal de autenticação", render: function (r) { return r.tp_canal_autenticacao || "—"; } },
      { rotulo: "Primeira vez", render: function (r) { return formatarDataHora(r.dt_primeira); } },
      { rotulo: "Última vez", render: function (r) { return formatarDataHora(r.dt_ultima); } },
      { rotulo: "Dias ativos", render: function (r) { return formatarNumero(r.qt_dias_ativos); } },
      { rotulo: "Multilogin", render: celulaMultilogin }
    ],
    erro: "Não foi possível carregar os acessos agregados.",
    exportar: {
      nome: "acessos_agregados",
      linha: function (r) {
        return [
          r.tx_identificador || "", r.tp_identificador || "", r.tp_canal_autenticacao || "",
          formatarDataHora(r.dt_primeira), formatarDataHora(r.dt_ultima),
          r.qt_dias_ativos != null ? String(r.qt_dias_ativos) : "", r.in_multilogin ? "sim" : "não",
        ];
      }
    }
  });

  function renderBarra($alvo, itens, chave, valor) {
    $alvo.empty();
    var maximo = Math.max.apply(null, itens.map(function (i) { return Number(i[valor]) || 0; }).concat(0));
    itens.forEach(function (item) {
      var v = Number(item[valor]) || 0;
      var largura = maximo > 0 ? Math.max((v / maximo) * 100, v > 0 ? 2 : 0) : 0;
      var $linha = $("<div>").addClass("kpi-barra-linha").appendTo($alvo);
      $("<div>").addClass("kpi-barra-rotulo").attr("title", item[chave]).text(item[chave]).appendTo($linha);
      var $trilha = $("<div>").addClass("kpi-barra-trilha").appendTo($linha);
      $("<div>").addClass("kpi-barra-preenchimento").css("width", largura + "%").appendTo($trilha);
      $("<div>").addClass("kpi-barra-valor").text(formatarNumero(v)).appendTo($linha);
    });
  }

  function carregarResumo() {
    var params = paramsFiltros();
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/acessos-agregados/resumo", params).done(function (r) {
      $("#kpi-total").text(formatarNumero(r.total));
      $("#kpi-multilogin").text(formatarNumero(r.multilogin));

      var $top = $("#agr-top-identificadores"), $topVazio = $("#agr-top-identificadores-vazio");
      if (r.top_identificadores && r.top_identificadores.length) {
        $topVazio.addClass("hidden");
        renderBarra($top, r.top_identificadores, "identificador", "dias_ativos");
      } else {
        $top.empty();
        $topVazio.removeClass("hidden");
      }
    });
  }

  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/acessos-agregados/filtros" + filtrosUrl).done(function (f) {
    (f.tipos || []).forEach(function (t) { $("<option>").attr("value", t).text(t).appendTo($tipo); });
  });

  function recarregarTudo() {
    tabela.recarregar();
    carregarResumo();
  }

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(recarregarTudo, 300);
  });
  $tipo.on("change", recarregarTudo);
  $ordenar.on("change", recarregarTudo);
  $dataDe.on("change", recarregarTudo);
  $dataAte.on("change", recarregarTudo);

  carregarResumo();
})();
