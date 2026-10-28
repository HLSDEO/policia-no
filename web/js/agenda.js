(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarDataSimples(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z")).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
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
    if ($ordenar.val() && $ordenar.val() !== "antigos") p.ordenar = $ordenar.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/agenda",
    idDe: function (r) { return r.id_evento; },
    params: paramsFiltros,
    rotuloTotal: function (n) { return formatarNumero(n) + " eventos"; },
    colunas: [
      { rotulo: "Início", render: function (r) { return formatarDataHora(r.dt_inicio); } },
      { rotulo: "Fim", render: function (r) { return formatarDataHora(r.dt_fim); } },
      { rotulo: "Título", render: function (r) { return r.tx_titulo || "—"; } },
      { rotulo: "Local", render: function (r) { return r.tx_local || "—"; } },
      { rotulo: "Organizador", render: function (r) { return r.tx_organizador || "—"; } },
      { rotulo: "Participantes", render: function (r) { return r.tx_participantes || "—"; } }
    ],
    erro: "Não foi possível carregar a agenda.",
    exportar: {
      nome: "agenda",
      colunas: ["Início", "Fim", "Título", "Local", "Descrição", "Organizador", "Participantes"],
      linha: function (r) {
        return [
          formatarDataHora(r.dt_inicio), formatarDataHora(r.dt_fim), r.tx_titulo || "",
          r.tx_local || "", r.tx_descricao || "", r.tx_organizador || "", r.tx_participantes || "",
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
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/agenda/resumo", params).done(function (r) {
      $("#kpi-total").text(formatarNumero(r.total));
      $("#kpi-periodo").text(r.primeiro ? (formatarDataSimples(r.primeiro) + " – " + formatarDataSimples(r.ultimo)) : "—");

      var $mes = $("#evt-por-mes"), $mesVazio = $("#evt-por-mes-vazio");
      if (r.por_mes && r.por_mes.length) {
        $mesVazio.addClass("hidden");
        renderBarra($mes, r.por_mes, "mes", "eventos");
      } else {
        $mes.empty();
        $mesVazio.removeClass("hidden");
      }
    });
  }

  function recarregarTudo() {
    tabela.recarregar();
    carregarResumo();
  }

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(recarregarTudo, 300);
  });
  $ordenar.on("change", recarregarTudo);
  $dataDe.on("change", recarregarTudo);
  $dataAte.on("change", recarregarTudo);

  carregarResumo();
})();
