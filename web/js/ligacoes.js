(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarDuracao(segundos) {
    if (segundos === null || segundos === undefined) return "—";
    var m = Math.floor(segundos / 60);
    var s = segundos % 60;
    return m + "min " + s + "s";
  }

  var TP_ORIGEM = "ligacao";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $direcao = $("#filtro-direcao");
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
    if ($direcao.val()) p.direcao = $direcao.val();
    if ($periodo.val()) p.periodo = $periodo.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/ligacoes",
    idDe: function (r) { return r.id_ligacao; },
    ordenarPadrao: "recentes",
    params: paramsFiltros,
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Data/hora",
        ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" },
        render: function (r) { return formatarDataHora(r.dt_ligacao); }
      },
      { rotulo: "Direção", render: function (r) { return r.tp_direcao || "—"; } },
      { rotulo: "Status", render: function (r) { return r.cd_status || "—"; } },
      {
        rotulo: "Duração",
        ordenar: { desc: "duracao", asc: "duracao_asc", padrao: "desc" },
        render: function (r) { return formatarDuracao(r.qt_duracao_segundos); }
      },
      {
        rotulo: "Contato",
        ordenar: { asc: "contato", desc: "contato_desc" },
        render: function (r) { return r.no_contato || "—"; }
      },
      { rotulo: "Telefone", render: function (r) { return r.nu_telefone_contato || "—"; } },
      { rotulo: "App", render: function (r) { return r.no_aplicativo || "—"; } },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_ligacao,
            rotulo: "ligação com " + (r.no_contato || r.nu_telefone_contato || "—"),
            temComentario: !!contagens[r.id_ligacao],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar as ligações.",
    aoContagem: function (total) { if (abas) abas.definirContagem("ligacoes", total); },
    exportar: {
      nome: "ligacoes",
      linha: function (r) {
        return [
          formatarDataHora(r.dt_ligacao), r.tp_direcao || "", r.cd_status || "",
          formatarDuracao(r.qt_duracao_segundos), r.no_contato || "", r.nu_telefone_contato || "", r.no_aplicativo || "",
        ];
      }
    }
  });

  // popula o select de direção (facetas do inquérito/aparelho)
  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/ligacoes/filtros" + filtrosUrl).done(function (f) {
    (f.direcoes || []).forEach(function (d) { $("<option>").attr("value", d).text(d).appendTo($direcao); });
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(tabela.recarregar, 300);
  });
  $direcao.on("change", tabela.recarregar);
  $periodo.on("change", tabela.recarregar);
  $dataDe.on("change", tabela.recarregar);
  $dataAte.on("change", tabela.recarregar);

  // A lista de ligações vira a primeira aba; a caixa postal (Voicemail do UFDR) vem do
  // componente compartilhado, na mesma barra. As duas já são paginadas no servidor.
  var abas = UfdrDominio.montar({
    seletor: "#abas-host",
    abas: [{ chave: "ligacoes", rotulo: "Ligações", externo: "#painel-ligacoes" }]
      .concat(UfdrDominio.abasDe(["voicemail"]))
  });

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
  });
})();
