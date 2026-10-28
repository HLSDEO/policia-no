(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarDataSimples(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z")).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  var TP_ORIGEM = "navegacao";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $navegador = $("#filtro-navegador");
  var $periodo = $("#filtro-periodo");
  // #filtro-ordenar é lido e mantido pela TabelaPaginada (opção seletorOrdenar), não aqui
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");

  // O TabelaPaginada grava os filtros na URL a cada carga (e restaura página/ordenação), mas os
  // controles desta tela são da página — sem isto, o link compartilhado abre filtrado na tabela
  // e com a barra de filtros vazia, e a próxima digitação apagaria o recorte herdado.
  var estadoInicial = window.EstadoUrl ? EstadoUrl.ler(["busca", "navegador", "periodo",
                                                        "data_de", "data_ate"]) : {};
  $busca.val(estadoInicial.busca || "");
  $periodo.val(estadoInicial.periodo || "");
  $dataDe.val(estadoInicial.data_de || "");
  $dataAte.val(estadoInicial.data_ate || "");

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
    if ($navegador.val()) p.navegador = $navegador.val();
    if ($periodo.val()) p.periodo = $periodo.val();
    // `ordenar` fica com a TabelaPaginada (cabeçalhos clicáveis), que mantém o <select>
    // #filtro-ordenar em sincronia pela opção seletorOrdenar
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
    url: "/api/investigacao/inqueritos/" + idInquerito + "/navegacao",
    idDe: function (r) { return r.id_pagina; },
    ordenarPadrao: "recentes",
    params: paramsFiltros,
    seletorOrdenar: "#filtro-ordenar",
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Data/hora",
        ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" },
        render: function (r) { return formatarDataHora(r.dt_visita); }
      },
      { rotulo: "Navegador", render: function (r) { return r.no_aplicativo || "—"; } },
      {
        rotulo: "Título",
        ordenar: { asc: "titulo" },
        render: function (r) { return r.tx_titulo || "—"; }
      },
      { rotulo: "URL", render: function (r) { return celulaUrl(r.tx_url); } },
      { rotulo: "Conta", render: function (r) { return r.tx_conta || "—"; } },
      {
        rotulo: "Visitas",
        ordenar: { desc: "visitas", padrao: "desc" },
        render: function (r) { return r.qt_visitas != null ? r.qt_visitas.toLocaleString("pt-BR") : "—"; }
      },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_pagina,
            rotulo: "página " + (r.tx_titulo || r.tx_url || ""),
            temComentario: !!contagens[r.id_pagina],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar o histórico de navegação.",
    exportar: {
      nome: "navegacao",
      linha: function (r) {
        return [
          formatarDataHora(r.dt_visita), r.no_aplicativo || "", r.tx_titulo || "", r.tx_url || "",
          r.tx_conta || "", r.qt_visitas != null ? String(r.qt_visitas) : "",
        ];
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
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/navegacao/resumo", params).done(function (r) {
      $("#kpi-total-visitas").text(formatarNumero(r.total_visitas));
      $("#kpi-total-paginas").text(formatarNumero(r.total_registros));
      $("#kpi-sites-unicos").text(formatarNumero(r.sites_unicos));
      $("#kpi-navegadores").text(formatarNumero(r.total_navegadores));
      $("#kpi-contas").text(formatarNumero(r.total_contas));
      $("#kpi-periodo").text(r.primeira_visita ? (formatarDataSimples(r.primeira_visita) + " – " + formatarDataSimples(r.ultima_visita)) : "—");

      var $sites = $("#nav-top-sites"), $sitesVazio = $("#nav-top-sites-vazio");
      if (r.top_sites && r.top_sites.length) {
        $sitesVazio.addClass("hidden");
        renderBarra($sites, r.top_sites, "dominio", "visitas");
      } else {
        $sites.empty();
        $sitesVazio.removeClass("hidden");
      }

      var $nav = $("#nav-por-navegador"), $navVazio = $("#nav-por-navegador-vazio");
      if (r.por_navegador && r.por_navegador.length) {
        $navVazio.addClass("hidden");
        renderBarra($nav, r.por_navegador, "navegador", "registros");
      } else {
        $nav.empty();
        $navVazio.removeClass("hidden");
      }

      var $per = $("#nav-por-periodo"), $perVazio = $("#nav-por-periodo-vazio");
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

  // popula o select de navegadores (facetas do inquérito/aparelho)
  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/navegacao/filtros" + filtrosUrl).done(function (f) {
    (f.navegadores || []).forEach(function (nav) { $("<option>").attr("value", nav).text(nav).appendTo($navegador); });
    // o select só existe depois das facetas — a restauração da URL espera junto
    if (estadoInicial.navegador) $navegador.val(estadoInicial.navegador);
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
  $navegador.on("change", recarregarTudo);
  $periodo.on("change", recarregarTudo);
  // #filtro-ordenar fica com a TabelaPaginada (seletorOrdenar), que já recarrega a tabela ao
  // mudar. Não precisa de recarregarTudo: o resumo/KPI é agregado do MESMO recorte — a ordem
  // não muda nenhum número dele, só custaria uma requisição a mais por clique.
  $dataDe.on("change", recarregarTudo);
  $dataAte.on("change", recarregarTudo);

  // carrega as contagens de entidade uma vez, depois a primeira página + resumo
  // O histórico vira a primeira aba; cookies, favoritos, downloads, preenchimento automático
  // e uso de rede vêm do componente compartilhado, na mesma barra.
  var abas = UfdrDominio.montar({
    seletor: "#abas-host",
    // filtros/página das abas UFDR + a aba ativa vivem na URL (EstadoUrl), do mesmo jeito que
    // o TabelaPaginada já faz com o Histórico — o link colado reabre a mesma visão
    estadoUrl: true,
    abas: [{ chave: "historico", rotulo: "Histórico", externo: "#painel-historico" }]
      .concat(UfdrDominio.abasDe(["cookies", "favoritos", "downloads", "autofill", "uso-rede"]))
  });

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
    carregarResumo();
  });
})();
