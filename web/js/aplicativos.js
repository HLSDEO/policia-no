(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var TP_ORIGEM = "aplicativo";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");

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
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/aplicativos",
    idDe: function (r) { return r.id_aplicativo; },
    params: paramsFiltros,
    autoCarregar: false,
    colunas: [
      { rotulo: "Pacote", render: function (r) { return r.nu_pacote || "—"; } },
      { rotulo: "Nome", render: function (r) { return r.no_aplicativo || "—"; } },
      { rotulo: "Versão", render: function (r) { return r.nu_versao || "—"; } },
      { rotulo: "Instalação", render: function (r) { return formatarDataHora(r.dt_instalacao); } },
      { rotulo: "Último uso", render: function (r) { return formatarDataHora(r.dt_ultimo_uso); } },
      {
        // abre os números de uso só deste app; a lista de eventos é grande demais para
        // caber na tela junto com a de aplicativos instalados
        rotulo: "Uso",
        exportavel: false,
        render: function (r) {
          if (!r.nu_pacote) return "—";
          return $("<button>").addClass("btn arq-acao").text("Ver uso")
            .on("click", function () { UsoApp.abrir(r.nu_pacote, r.no_aplicativo); });
        }
      },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_aplicativo,
            rotulo: "app " + (r.nu_pacote || r.no_aplicativo || ""),
            temComentario: !!contagens[r.id_aplicativo],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar os aplicativos.",
    exportar: {
      nome: "aplicativos",
      linha: function (r) {
        return [r.nu_pacote || "", r.no_aplicativo || "", r.nu_versao || "", formatarDataHora(r.dt_instalacao), formatarDataHora(r.dt_ultimo_uso)];
      }
    }
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(tabela.recarregar, 300);
  });

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
  });
})();
