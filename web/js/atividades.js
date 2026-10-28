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
  var $produto = $("#filtro-produto");
  var $periodo = $("#filtro-periodo");
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
    if ($produto.val()) p.produto = $produto.val();
    if ($periodo.val()) p.periodo = $periodo.val();
    if ($ordenar.val() && $ordenar.val() !== "recentes") p.ordenar = $ordenar.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  function celulaUrl(url) {
    if (!url) return "—";
    return $("<a>").attr("href", url).attr("target", "_blank").attr("rel", "noopener noreferrer").text(url);
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/atividades",
    idDe: function (r) { return r.id_atividade; },
    params: paramsFiltros,
    colunas: [
      { rotulo: "Quando", render: function (r) { return formatarDataHora(r.dt_atividade); } },
      { rotulo: "Produto", render: function (r) { return r.tx_produto || "—"; } },
      { rotulo: "Descrição", render: function (r) { return r.tx_descricao || "—"; } },
      { rotulo: "URL", render: function (r) { return celulaUrl(r.tx_url); } }
    ],
    erro: "Não foi possível carregar Minha Atividade.",
    exportar: {
      nome: "atividades",
      linha: function (r) {
        return [formatarDataHora(r.dt_atividade), r.tx_produto || "", r.tx_descricao || "", r.tx_url || ""];
      }
    }
  });

  function renderBarra($alvo, itens, chave, valor, rotulo) {
    $alvo.empty();
    var maximo = Math.max.apply(null, itens.map(function (i) { return Number(i[valor]) || 0; }).concat(0));
    itens.forEach(function (item) {
      var v = Number(item[valor]) || 0;
      var largura = maximo > 0 ? Math.max((v / maximo) * 100, v > 0 ? 2 : 0) : 0;
      var $linha = $("<div>").addClass("kpi-barra-linha").appendTo($alvo);
      $("<div>").addClass("kpi-barra-rotulo").attr("title", item[chave]).text(rotulo ? rotulo(item) : item[chave]).appendTo($linha);
      var $trilha = $("<div>").addClass("kpi-barra-trilha").appendTo($linha);
      $("<div>").addClass("kpi-barra-preenchimento").css("width", largura + "%").appendTo($trilha);
      $("<div>").addClass("kpi-barra-valor").text(formatarNumero(v)).appendTo($linha);
    });
  }

  var LABEL_PERIODO = { madrugada: "Madrugada (0h–6h)", manha: "Manhã (6h–12h)", tarde: "Tarde (12h–18h)", noite: "Noite (18h–0h)" };
  var ORDEM_PERIODO = ["madrugada", "manha", "tarde", "noite"];

  function carregarResumo() {
    var params = paramsFiltros();
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/atividades/resumo", params).done(function (r) {
      $("#kpi-total").text(formatarNumero(r.total));
      $("#kpi-periodo").text(r.primeira ? (formatarDataSimples(r.primeira) + " – " + formatarDataSimples(r.ultima)) : "—");

      var $produtos = $("#atv-top-produtos"), $produtosVazio = $("#atv-top-produtos-vazio");
      if (r.top_produtos && r.top_produtos.length) {
        $produtosVazio.addClass("hidden");
        renderBarra($produtos, r.top_produtos, "produto", "registros");
      } else {
        $produtos.empty();
        $produtosVazio.removeClass("hidden");
      }

      var $per = $("#atv-por-periodo"), $perVazio = $("#atv-por-periodo-vazio");
      var itensPeriodo = ORDEM_PERIODO.map(function (p) {
        return { periodo: p, registros: (r.por_periodo_dia && r.por_periodo_dia[p]) || 0 };
      });
      if (itensPeriodo.some(function (i) { return i.registros > 0; })) {
        $perVazio.addClass("hidden");
        renderBarra($per, itensPeriodo, "periodo", "registros", function (i) { return LABEL_PERIODO[i.periodo]; });
      } else {
        $per.empty();
        $perVazio.removeClass("hidden");
      }
    });
  }

  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/atividades/filtros" + filtrosUrl).done(function (f) {
    (f.produtos || []).forEach(function (p) { $("<option>").attr("value", p).text(p).appendTo($produto); });
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
  $produto.on("change", recarregarTudo);
  $periodo.on("change", recarregarTudo);
  $ordenar.on("change", recarregarTudo);
  $dataDe.on("change", recarregarTudo);
  $dataAte.on("change", recarregarTudo);

  carregarResumo();
})();
