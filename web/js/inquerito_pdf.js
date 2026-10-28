(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idIpl = paramUrl("ipl");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "pecas";
  var abaAtiva = "pecas";
  var ABAS = ["pecas", "pessoas", "achados"];

  // dados por aba (fonte da verdade; filtros/ordenação re-renderizam a partir daqui)
  var dados = { pecas: [], pessoas: [], achados: [] };
  var cabecalho = null;

  // contagens de "entidade/comentário" por id_origem (para destacar 📌)
  var contagensEnt = { ipl_pessoa: {}, ipl_achado: {} };

  var ROTULO_ACHADO = {
    tipificacao: "Tipificação", procedimento: "Procedimento", valor: "Valor", conta: "Conta",
    agencia: "Agência", telefone: "Telefone", email: "E-mail", endereco: "Endereço",
    processo: "Processo", oab: "OAB"
  };

  // ---------------------------------------------------------------- helpers
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }

  // data pura, sem hora — formatada na mão pra não escorregar de fuso
  function formatarData(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(iso);
  }
  function faixaFolhas(p) {
    if (p.nu_folha_inicial == null) return "—";
    return p.nu_folha_final && p.nu_folha_final !== p.nu_folha_inicial
      ? p.nu_folha_inicial + "–" + p.nu_folha_final
      : String(p.nu_folha_inicial);
  }
  function ehPJ(p) {
    var d = String(p.nu_cpf_cnpj || "");
    return d.indexOf("/") !== -1 || d.replace(/\D/g, "").length === 14;
  }
  function rotuloAchado(tp) { return ROTULO_ACHADO[tp] || tp; }

  // ------------------------------------------------- identificação (topo)
  function renderCabecalho() {
    var $i = $("#ipl-ident").empty();
    if (!cabecalho) return;
    $("#ipl-numero").text(cabecalho.nu_ipl ? "nº " + cabecalho.nu_ipl : cabecalho.no_arquivo || "");

    var campos = [
      { rot: "Nº do IPL", val: cabecalho.nu_ipl },
      { rot: "Unidade", val: cabecalho.no_unidade },
      { rot: "Autoridade", val: cabecalho.no_autoridade },
      { rot: "Instaurado em", val: cabecalho.dt_instauracao ? formatarData(cabecalho.dt_instauracao) : null },
      { rot: "Tipificação", val: cabecalho.tx_tipificacao },
      { rot: "Processo judicial", val: cabecalho.nu_processo_judicial },
      { rot: "Folhas", val: cabecalho.qt_folhas ? num(cabecalho.qt_folhas).toLocaleString("pt-BR") : null },
      { rot: "Arquivo", val: cabecalho.no_arquivo }
    ];
    campos.forEach(function (c) {
      if (!c.val) return;
      var $c = $("<div>").addClass("ipl-campo").appendTo($i);
      $("<div>").addClass("rot").text(c.rot).appendTo($c);
      $("<div>").addClass("val").text(c.val).appendTo($c);
    });

    $("#ipl-resumo").toggleClass("hidden", !cabecalho.tx_resumo_fatos);
    $("#ipl-resumo-texto").text(cabecalho.tx_resumo_fatos || "");
  }

  // ------------------------------------------------------- popular selects
  function popularSelect($sel, valores, rotulo) {
    valores.sort(function (a, b) { return String(a).localeCompare(String(b), "pt-BR"); });
    valores.forEach(function (v) {
      $("<option>").val(v).text(rotulo ? rotulo(v) : v).appendTo($sel);
    });
  }
  function chavesDe(lista, campo) {
    var vistos = {};
    lista.forEach(function (x) { if (x[campo]) vistos[x[campo]] = true; });
    return Object.keys(vistos);
  }
  function popularFiltros() {
    popularSelect($("#f-pec-tipo"), chavesDe(dados.pecas, "tp_peca"));
    popularSelect($("#f-pes-papel"), chavesDe(dados.pessoas, "tp_papel"));
    popularSelect($("#f-ach-tipo"), chavesDe(dados.achados, "tp_achado"), rotuloAchado);
  }

  // botão 📌 de entidade/marcação, quando a página foi aberta a partir de um inquérito
  function botaoEntidade($td, tp, id, rotulo) {
    if (!idInquerito || !window.EntidadesUI) return;
    $td.append(EntidadesUI.botao({
      idInquerito: idInquerito, tpOrigem: tp, idOrigem: id, rotulo: rotulo,
      temComentario: !!contagensEnt[tp][id], aoMudar: function () {}
    }));
  }

  // --------------------------------------------------------- render Peças
  function renderPecas(lista) {
    var $corpo = $("#corpo-pecas").empty();
    $("#vazio-pecas").toggle(!lista.length);
    lista.forEach(function (p) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome ipl-folha").text(faixaFolhas(p)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(p.tp_peca)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(p.nu_peca)).appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarData(p.dt_peca)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(p.no_orgao)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(p.no_assinante)).appendTo($tr);
      $("<td>").addClass("col-nome ipl-resumo-peca").text(p.tx_resumo || "—").appendTo($tr);
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Pessoas
  function renderPessoas(lista) {
    var $corpo = $("#corpo-pessoas").empty();
    $("#vazio-pessoas").toggle(!lista.length);
    lista.forEach(function (p) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome ipl-folha").text(p.nu_folha != null ? p.nu_folha : "—").appendTo($tr);
      var $nome = $("<td>").addClass("col-nome").appendTo($tr);
      $("<div>").text(txt(p.no_pessoa)).appendTo($nome);
      // as outras grafias fundidas nesta linha ficam à vista — o agrupamento tem que ser auditável
      if (p.no_variantes) {
        $("<div>").addClass("ipl-variantes").text("também citado como: " + p.no_variantes).appendTo($nome);
      }
      var $doc = $("<td>").addClass("col-nome").appendTo($tr);
      if (p.nu_cpf_cnpj) {
        $doc.append(document.createTextNode(p.nu_cpf_cnpj + " "))
          .append($("<span>").addClass("ipl-badge " + (ehPJ(p) ? "pj" : "pf")).text(ehPJ(p) ? "PJ" : "PF"));
      } else {
        $doc.text("—");
      }
      var $vulgo = $("<td>").addClass("col-nome").appendTo($tr);
      if (p.no_vulgo) $vulgo.append($("<span>").addClass("ipl-badge vulgo").text(p.no_vulgo));
      else $vulgo.text("—");
      $("<td>").addClass("col-nome").text(txt(p.tp_papel)).appendTo($tr);
      $("<td>").addClass("col-nome").text(num(p.qt_mencoes).toLocaleString("pt-BR")).appendTo($tr);
      $("<td>").addClass("col-nome ipl-contexto").text(p.tx_contexto || "—").appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, "ipl_pessoa", p.id_pessoa_ipl,
        "pessoa " + (p.no_pessoa || p.nu_cpf_cnpj || p.no_vulgo || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Achados
  function renderAchados(lista) {
    var $corpo = $("#corpo-achados").empty();
    $("#vazio-achados").toggle(!lista.length);
    lista.forEach(function (a) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome ipl-folha").text(a.nu_folha != null ? a.nu_folha : "—").appendTo($tr);
      $("<td>").addClass("col-nome").append($("<span>").addClass("ipl-badge").text(rotuloAchado(a.tp_achado))).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(a.tx_valor)).appendTo($tr);
      $("<td>").addClass("col-nome").text(num(a.qt_ocorrencias).toLocaleString("pt-BR")).appendTo($tr);
      $("<td>").addClass("col-nome ipl-contexto").text(a.tx_contexto || "—").appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, "ipl_achado", a.id_achado_ipl,
        rotuloAchado(a.tp_achado) + " " + (a.tx_valor || ""));
      $corpo.append($tr);
    });
  }

  // ----------------------------------------------------------- filtragem
  function contemTexto(obj, termo) {
    if (!termo) return true;
    return JSON.stringify(obj).toLowerCase().indexOf(termo) !== -1;
  }
  function info(id, mostrados, total) {
    $(id).text(mostrados === total ? "" : mostrados.toLocaleString("pt-BR") + " de " + total.toLocaleString("pt-BR"));
  }

  function aplicarPecas() {
    var termo = $.trim($("#f-pec-busca").val()).toLowerCase();
    var tipo = $("#f-pec-tipo").val();
    var ordem = $("#f-pec-ordem").val();
    var lista = dados.pecas.filter(function (p) {
      if (tipo && p.tp_peca !== tipo) return false;
      return contemTexto(p, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "data") return String(a.dt_peca || "9").localeCompare(String(b.dt_peca || "9"));
      if (ordem === "data-desc") return String(b.dt_peca || "").localeCompare(String(a.dt_peca || ""));
      if (ordem === "tipo") return String(a.tp_peca || "").localeCompare(String(b.tp_peca || ""), "pt-BR");
      return num(a.nu_folha_inicial) - num(b.nu_folha_inicial);
    });
    renderPecas(lista);
    info("#f-pec-info", lista.length, dados.pecas.length);
    atualizarTotal();
  }

  function aplicarPessoas() {
    var termo = $.trim($("#f-pes-busca").val()).toLowerCase();
    var papel = $("#f-pes-papel").val();
    var doc = $("#f-pes-doc").val();
    var ordem = $("#f-pes-ordem").val();
    var lista = dados.pessoas.filter(function (p) {
      if (papel && p.tp_papel !== papel) return false;
      if (doc === "com" && !p.nu_cpf_cnpj) return false;
      if (doc === "pf" && (!p.nu_cpf_cnpj || ehPJ(p))) return false;
      if (doc === "pj" && (!p.nu_cpf_cnpj || !ehPJ(p))) return false;
      if (doc === "vulgo" && !p.no_vulgo) return false;
      return contemTexto(p, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "mencoes") return num(b.qt_mencoes) - num(a.qt_mencoes);
      if (ordem === "nome") return String(a.no_pessoa || "").localeCompare(String(b.no_pessoa || ""), "pt-BR");
      return num(a.nu_folha) - num(b.nu_folha);
    });
    renderPessoas(lista);
    info("#f-pes-info", lista.length, dados.pessoas.length);
    atualizarTotal();
  }

  function aplicarAchados() {
    var termo = $.trim($("#f-ach-busca").val()).toLowerCase();
    var tipo = $("#f-ach-tipo").val();
    var ordem = $("#f-ach-ordem").val();
    var lista = dados.achados.filter(function (a) {
      if (tipo && a.tp_achado !== tipo) return false;
      return contemTexto(a, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "folha") return num(a.nu_folha) - num(b.nu_folha);
      if (ordem === "ocorrencias") return num(b.qt_ocorrencias) - num(a.qt_ocorrencias);
      if (ordem === "valor") return num(b.vl_numerico) - num(a.vl_numerico);
      var t = String(a.tp_achado).localeCompare(String(b.tp_achado));
      return t !== 0 ? t : num(a.nu_folha) - num(b.nu_folha);
    });
    renderAchados(lista);
    info("#f-ach-info", lista.length, dados.achados.length);
    atualizarTotal();
  }

  // ------------------------------------------------------------------ abas
  function rotuloAba(aba) {
    return { pecas: "Peças", pessoas: "Pessoas", achados: "Achados" }[aba] +
      " (" + (dados[aba] || []).length + ")";
  }
  function atualizarRotulosAbas() {
    $(".ipl-aba").each(function () { $(this).text(rotuloAba($(this).data("aba"))); });
  }
  function trocarAba(aba) {
    abaAtiva = aba;
    $(".ipl-aba").removeClass("ativa").filter('[data-aba="' + aba + '"]').addClass("ativa");
    $(".ipl-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    atualizarTotal();
  }
  $(".ipl-aba").on("click", function () { trocarAba($(this).data("aba")); });

  function atualizarTotal() {
    var visiveis = $("#corpo-" + abaAtiva + " > tr").length;
    var rot = { pecas: "peças", pessoas: "pessoas", achados: "achados" }[abaAtiva];
    $("#total-registros").text(visiveis.toLocaleString("pt-BR") + " " + rot);
  }

  // binds de filtro
  $("#f-pec-busca, #f-pec-tipo, #f-pec-ordem").on("input change", aplicarPecas);
  $("#f-pes-busca, #f-pes-papel, #f-pes-doc, #f-pes-ordem").on("input change", aplicarPessoas);
  $("#f-ach-busca, #f-ach-tipo, #f-ach-ordem").on("input change", aplicarAchados);

  // ------------------------------------------------------------------ carga
  function carregar(aba) {
    return $.getJSON("/api/investigacao/inqueritos-pdf/" + idIpl + "/" + aba).done(function (lista) {
      dados[aba] = lista || [];
    });
  }

  function carregarContagens(tp) {
    var d = $.Deferred();
    if (!idInquerito || !window.EntidadesUI) { d.resolve(); return d; }
    EntidadesUI.carregarContagens(idInquerito, tp, function (c) { contagensEnt[tp] = c || {}; d.resolve(); });
    return d;
  }

  if (!idIpl) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  $.when(
    carregar("pecas"), carregar("pessoas"), carregar("achados"),
    // encurtarCaminhos: NO_ARQUIVO guarda o caminho relativo em data/ e na tela só o nome do
    // arquivo interessa (ver docs/organizacao_arquivos.md)
    $.getJSON("/api/investigacao/inqueritos-pdf/" + idIpl).done(function (c) { cabecalho = window.AnalyserUtils.encurtarCaminhos(c); }),
    carregarContagens("ipl_pessoa"), carregarContagens("ipl_achado")
  ).always(function () {
    renderCabecalho();
    popularFiltros();
    atualizarRotulosAbas();
    aplicarPecas();
    aplicarPessoas();
    aplicarAchados();
    trocarAba(ABAS.indexOf(abaInicial) !== -1 ? abaInicial : "pecas");
  });

  // exportar Excel (aba ativa, respeitando o filtro atual)
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "inquerito",
      dados: function () {
        return ExportarExcel.lerTabela(document.getElementById("tab-" + abaAtiva));
      },
    });
  }

  // Botão "Prompt de IA do Inquérito" — conversa restrita só a este IPL (ver js/ia.js e
  // funcoes/ia_investigacao.ferramentas_para_inquerito no backend)
  $("#prompt-ia-ipl-btn").on("click", function () {
    var titulo = "Inquérito " + ((cabecalho && (cabecalho.nu_ipl || cabecalho.no_arquivo)) || idIpl);
    window.location.href = "ia.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=ipl&escopo_id=" + encodeURIComponent(idIpl) +
      "&escopo_titulo=" + encodeURIComponent(titulo);
  });

  // Botão "Hipótese da mídia" — web/hipoteses.html filtrada neste IPL (mesmo par de URL do chat
  // de IA por escopo); a visão do inquérito inteiro está no menu "Extras" da Investigação.
  $("#achados-analista-ipl-btn").on("click", function () {
    window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=ipl&escopo_id=" + encodeURIComponent(idIpl) +
      "&escopo_titulo=" + encodeURIComponent("Inquérito " + ((cabecalho && (cabecalho.nu_ipl || cabecalho.no_arquivo)) || idIpl));
  });
})();
