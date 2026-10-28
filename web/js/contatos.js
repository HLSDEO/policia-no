(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var TP_ORIGEM = "contato_agenda";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $tipo = $("#filtro-tipo");

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
    if ($tipo.val()) p.tipo = $tipo.val();
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/contatos",
    idDe: function (r) { return r.id_contato; },
    ordenarPadrao: "nome",
    params: paramsFiltros,
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Nome",
        ordenar: { asc: "nome", desc: "nome_desc" },
        render: function (r) { return r.no_contato || "—"; }
      },
      { rotulo: "Telefones / e-mails", render: function (r) { return r.entradas || "—"; } },
      {
        rotulo: "Origem",
        ordenar: { asc: "origem", desc: "origem_desc" },
        render: function (r) { return r.no_aplicativo_origem || "—"; }
      },
      {
        rotulo: "Grupo",
        ordenar: { asc: "grupo", desc: "grupo_desc" },
        render: function (r) { return r.no_grupo || "—"; }
      },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_contato,
            rotulo: "contato " + (r.no_contato || ""),
            temComentario: !!contagens[r.id_contato],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar os contatos.",
    exportar: {
      nome: "contatos",
      linha: function (r) {
        return [r.no_contato || "", r.entradas || "", r.no_aplicativo_origem || "", r.no_grupo || ""];
      }
    }
  });

  // popula o select de tipo (facetas do inquérito/aparelho)
  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/contatos/filtros" + filtrosUrl).done(function (f) {
    (f.tipos || []).forEach(function (t) { $("<option>").attr("value", t).text(t).appendTo($tipo); });
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(tabela.recarregar, 300);
  });
  $tipo.on("change", tabela.recarregar);

  // carrega as contagens de entidade uma vez, depois a primeira página
  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
  });
})();
