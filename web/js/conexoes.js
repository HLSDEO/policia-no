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
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  function celulaIdent(tipo, valor) {
    var $wrap = $("<span>");
    if (tipo) $("<span>").css({ display: "block", fontSize: "11px", opacity: ".7" }).text(tipo).appendTo($wrap);
    $("<span>").text(valor || "—").appendTo($wrap);
    return $wrap;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/conexoes",
    idDe: function (r) { return r.id_conexao; },
    params: paramsFiltros,
    colunas: [
      { rotulo: "Identificador 1", render: function (r) { return celulaIdent(r.tp_ident_1, r.tx_ident_1); } },
      { rotulo: "Identificador 2", render: function (r) { return celulaIdent(r.tp_ident_2, r.tx_ident_2); } },
      { rotulo: "Primeira vez", render: function (r) { return formatarDataHora(r.dt_primeira); } },
      { rotulo: "Última vez", render: function (r) { return formatarDataHora(r.dt_ultima); } }
    ],
    erro: "Não foi possível carregar as conexões.",
    exportar: {
      nome: "conexoes",
      colunas: ["Tipo 1", "Identificador 1", "Tipo 2", "Identificador 2", "Primeira vez", "Última vez"],
      linha: function (r) {
        return [
          r.tp_ident_1 || "", r.tx_ident_1 || "", r.tp_ident_2 || "", r.tx_ident_2 || "",
          formatarDataHora(r.dt_primeira), formatarDataHora(r.dt_ultima),
        ];
      }
    }
  });

  function carregarResumo() {
    var params = paramsFiltros();
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/conexoes/resumo", params).done(function (r) {
      $("#kpi-total").text(formatarNumero(r.total));
      $("#kpi-periodo").text(r.primeira ? (formatarDataSimples(r.primeira) + " – " + formatarDataSimples(r.ultima)) : "—");
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
  $dataDe.on("change", recarregarTudo);
  $dataAte.on("change", recarregarTudo);

  carregarResumo();
})();
