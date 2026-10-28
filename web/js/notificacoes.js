(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var TP_ORIGEM = "notificacao";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $app = $("#filtro-app");
  var $periodo = $("#filtro-periodo");
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");

  if (!idInquerito) {
    Estados.erro($("#tabela"), { mensagem: "Parâmetros inválidos na URL." });
    return;
  }

  var contagens = {};

  function paramsFiltros() {
    var p = {};
    if (idExtracao) p.extracao = idExtracao;
    var busca = $busca.val().trim();
    if (busca) p.busca = busca;
    if ($app.val()) p.app = $app.val();
    if ($periodo.val()) p.periodo = $periodo.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/notificacoes",
    idDe: function (r) { return r.id_notificacao; },
    ordenarPadrao: "recentes",
    params: paramsFiltros,
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Data/hora",
        ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" },
        render: function (r) { return formatarDataHora(r.dt_notificacao); }
      },
      {
        rotulo: "App de origem",
        ordenar: { asc: "app", desc: "app_desc" },
        render: function (r) { return r.no_pacote_app || "—"; }
      },
      { rotulo: "Conteúdo", render: function (r) { return r.tx_conteudo || "—"; } },
      { rotulo: "Registrado por", render: function (r) { return r.no_aplicativo_log || "—"; } },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_notificacao,
            rotulo: "notificação " + (r.no_pacote_app || ""),
            temComentario: !!contagens[r.id_notificacao],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar as notificações.",
    exportar: {
      nome: "notificacoes",
      linha: function (r) {
        return [formatarDataHora(r.dt_notificacao), r.no_pacote_app || "", r.tx_conteudo || "", r.no_aplicativo_log || ""];
      }
    }
  });

  // popula o select de app (facetas do inquérito/aparelho)
  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/notificacoes/filtros" + filtrosUrl).done(function (f) {
    (f.apps || []).forEach(function (a) { $("<option>").attr("value", a).text(a).appendTo($app); });
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(tabela.recarregar, 300);
  });
  $app.on("change", tabela.recarregar);
  $periodo.on("change", tabela.recarregar);
  $dataDe.on("change", tabela.recarregar);
  $dataAte.on("change", tabela.recarregar);

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
  });
})();
