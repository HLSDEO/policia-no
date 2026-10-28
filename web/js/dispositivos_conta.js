(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");

  if (!idInquerito) {
    Estados.erro($("#tabela"), { mensagem: "Parâmetros inválidos na URL." });
    return;
  }

  function paramsFiltros() {
    var p = {};
    if (idExtracao) p.extracao = idExtracao;
    var busca = $busca.val().trim();
    if (busca) p.busca = busca;
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/dispositivos-conta",
    idDe: function (r) { return r.id_dispositivo; },
    params: paramsFiltros,
    colunas: [
      { rotulo: "Dispositivo", render: function (r) { return r.no_dispositivo || "—"; } },
      { rotulo: "Fabricante", render: function (r) { return r.tx_fabricante || "—"; } },
      { rotulo: "Android ID", render: function (r) { return r.tx_android_id || "—"; } },
      { rotulo: "IMEI", render: function (r) { return r.tx_imei || "—"; } },
      { rotulo: "Versão", render: function (r) { return r.tx_versao || "—"; } },
      { rotulo: "Serial", render: function (r) { return r.tx_serial || "—"; } },
      { rotulo: "Origem", render: function (r) { return r.tp_origem || "—"; } }
    ],
    erro: "Não foi possível carregar os dispositivos.",
    exportar: {
      nome: "dispositivos_conta",
      linha: function (r) {
        return [
          r.no_dispositivo || "", r.tx_fabricante || "", r.tx_android_id || "", r.tx_imei || "",
          r.tx_versao || "", r.tx_serial || "", r.tp_origem || "",
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
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/dispositivos-conta/resumo", params).done(function (r) {
      $("#kpi-total").text(formatarNumero(r.total));
      var $fab = $("#dvc-por-fabricante"), $fabVazio = $("#dvc-por-fabricante-vazio");
      if (r.por_fabricante && r.por_fabricante.length) {
        $fabVazio.addClass("hidden");
        renderBarra($fab, r.por_fabricante, "fabricante", "registros");
      } else {
        $fab.empty();
        $fabVazio.removeClass("hidden");
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

  carregarResumo();
})();
