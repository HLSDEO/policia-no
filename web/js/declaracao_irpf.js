(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idIrpf = paramUrl("irpf");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "bens";
  var abaAtiva = "bens";
  var ABAS = ["bens", "dependentes", "rendimentos", "dividas", "doacoes"];

  // dados por aba (fonte da verdade; filtros/ordenação re-renderizam a partir daqui)
  var dados = { bens: [], dependentes: [], rendimentos: [], dividas: [], doacoes: [] };
  var cabecalho = null;

  // contagens de "entidade/comentário" por id_origem (para destacar 📌)
  var TP_ORIGEM = {
    bens: "irpf_bem", dependentes: "irpf_dependente", rendimentos: "irpf_rendimento",
    dividas: "irpf_divida", doacoes: "irpf_doacao"
  };
  var CAMPO_ID = {
    bens: "id_bem", dependentes: "id_dependente", rendimentos: "id_rendimento",
    dividas: "id_divida", doacoes: "id_doacao"
  };
  var contagensEnt = { irpf_bem: {}, irpf_dependente: {}, irpf_rendimento: {}, irpf_divida: {}, irpf_doacao: {} };

  // ---------------------------------------------------------------- helpers
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function temValor(v) { return v !== null && v !== undefined; }
  function moeda(v) {
    return temValor(v) ? "R$ " + num(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
  }

  // data pura, sem hora — formatada na mão pra não escorregar de fuso
  function formatarData(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(iso);
  }
  function ehPJ(doc) {
    var d = String(doc || "");
    return d.indexOf("/") !== -1 || d.replace(/\D/g, "").length === 14;
  }

  // botão 📌 de entidade/marcação
  function botaoEntidade($td, aba, id, rotulo) {
    if (!idInquerito || !window.EntidadesUI || !id) return;
    var tp = TP_ORIGEM[aba];
    $td.append(EntidadesUI.botao({
      idInquerito: idInquerito, tpOrigem: tp, idOrigem: id, rotulo: rotulo,
      temComentario: !!contagensEnt[tp][id], aoMudar: function () {}
    }));
  }

  // ------------------------------------------------- identificação (topo)
  function renderCabecalho() {
    var $i = $("#irpf-ident").empty();
    if (!cabecalho) return;
    $("#irpf-titular").text(cabecalho.no_titular || cabecalho.no_arquivo || "");

    var enderecoCompleto = [cabecalho.tx_endereco, [cabecalho.no_municipio, cabecalho.sg_uf].filter(Boolean).join("/"), cabecalho.nu_cep]
      .filter(Boolean).join(" — ");

    var campos = [
      { rot: "Titular", val: cabecalho.no_titular },
      { rot: "CPF", val: cabecalho.nu_cpf },
      { rot: "Nascimento", val: cabecalho.dt_nascimento ? formatarData(cabecalho.dt_nascimento) : null },
      { rot: "Título eleitoral", val: cabecalho.nu_titulo_eleitoral },
      { rot: "Endereço", val: enderecoCompleto },
      { rot: "Telefone", val: cabecalho.nu_telefone },
      { rot: "Celular", val: cabecalho.nu_celular },
      { rot: "E-mail", val: cabecalho.no_email },
      { rot: "Natureza da ocupação", val: cabecalho.tx_natureza_ocupacao },
      { rot: "Ocupação principal", val: cabecalho.tx_ocupacao_principal },
      { rot: "Tipo de declaração", val: cabecalho.tx_tipo_declaracao },
      { rot: "Exercício / Ano-calendário", val: (cabecalho.nu_exercicio && cabecalho.nu_ano_calendario) ? (cabecalho.nu_exercicio + " / " + cabecalho.nu_ano_calendario) : null },
      { rot: "Rendimentos tributáveis", val: temValor(cabecalho.vl_rendimentos_tributaveis) ? moeda(cabecalho.vl_rendimentos_tributaveis) : null },
      { rot: "Imposto devido", val: temValor(cabecalho.vl_imposto_devido) ? moeda(cabecalho.vl_imposto_devido) : null },
      { rot: "Imposto a restituir", val: (cabecalho.vl_imposto_restituir && num(cabecalho.vl_imposto_restituir) > 0) ? moeda(cabecalho.vl_imposto_restituir) : null },
      { rot: "Saldo a pagar", val: (cabecalho.vl_saldo_pagar && num(cabecalho.vl_saldo_pagar) > 0) ? moeda(cabecalho.vl_saldo_pagar) : null },
      { rot: "Arquivo", val: cabecalho.no_arquivo }
    ];
    campos.forEach(function (c) {
      if (!c.val) return;
      var $c = $("<div>").addClass("irpf-campo").appendTo($i);
      $("<div>").addClass("rot").text(c.rot).appendTo($c);
      $("<div>").addClass("val").text(c.val).appendTo($c);
    });

    renderPatrimonio();
  }

  // ---------------------------------------------- evolução patrimonial
  function itemPatrimonio($grid, rotulo, anterior, atual) {
    if (!temValor(anterior) && !temValor(atual)) return;
    var $item = $("<div>").addClass("irpf-patrimonio-item").appendTo($grid);
    $("<div>").addClass("rot2").text(rotulo).appendTo($item);
    $("<div>").addClass("val2").text(moeda(atual)).appendTo($item);
    if (temValor(anterior) && temValor(atual)) {
      var diff = num(atual) - num(anterior);
      var $val = $item.find(".val2");
      if (diff > 0) $val.addClass("up"); else if (diff < 0) $val.addClass("down");
      $("<div>").addClass("var2").text(
        "31/12 ano anterior: " + moeda(anterior) + " (" + (diff >= 0 ? "+" : "") + diff.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ")"
      ).appendTo($item);
    }
  }

  function renderPatrimonio() {
    var $grid = $("#irpf-patrimonio-grid").empty();
    var temAlgo = temValor(cabecalho.vl_bens_ano_atual) || temValor(cabecalho.vl_dividas_ano_atual);
    $("#irpf-patrimonio").toggleClass("hidden", !temAlgo);
    if (!temAlgo) return;
    itemPatrimonio($grid, "Bens e direitos", cabecalho.vl_bens_ano_anterior, cabecalho.vl_bens_ano_atual);
    itemPatrimonio($grid, "Dívidas e ônus reais", cabecalho.vl_dividas_ano_anterior, cabecalho.vl_dividas_ano_atual);
    if (temValor(cabecalho.vl_bens_ano_atual) && temValor(cabecalho.vl_dividas_ano_atual)) {
      itemPatrimonio(
        $grid, "Patrimônio líquido",
        temValor(cabecalho.vl_bens_ano_anterior) && temValor(cabecalho.vl_dividas_ano_anterior)
          ? num(cabecalho.vl_bens_ano_anterior) - num(cabecalho.vl_dividas_ano_anterior) : null,
        num(cabecalho.vl_bens_ano_atual) - num(cabecalho.vl_dividas_ano_atual)
      );
    }
  }

  // ------------------------------------------------------- popular selects
  function popularSelect($sel, valores, rotulo) {
    valores.sort(function (a, b) { return String(a).localeCompare(String(b), "pt-BR"); });
    valores.forEach(function (v) {
      $("<option>").val(v).text(rotulo ? rotulo(v) : v).appendTo($sel);
    });
  }

  // --------------------------------------------------------- render Bens
  function renderLinhasValor(corpo, vazio, lista, aba) {
    var $corpo = $(corpo).empty();
    $(vazio).toggle(!lista.length);
    lista.forEach(function (item) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(txt(item.no_codigo)).appendTo($tr);
      $("<td>").addClass("col-nome irpf-discriminacao").text(txt(item.tx_discriminacao)).appendTo($tr);
      $("<td>").addClass("col-nome irpf-valor").text(moeda(item.vl_ano_anterior)).appendTo($tr);
      $("<td>").addClass("col-nome irpf-valor").text(moeda(item.vl_ano_atual)).appendTo($tr);
      var variacao = (temValor(item.vl_ano_anterior) && temValor(item.vl_ano_atual))
        ? num(item.vl_ano_atual) - num(item.vl_ano_anterior) : null;
      $("<td>").addClass("col-nome irpf-valor").text(variacao === null ? "—" : (variacao >= 0 ? "+" : "") + moeda(variacao).replace("R$ -", "-R$ ")).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, aba, item[CAMPO_ID[aba]], (item.no_codigo || "") + " " + (item.tx_discriminacao || ""));
      $(corpo).append($tr);
    });
  }

  // ------------------------------------------------------- render Dependentes
  function renderDependentes(lista) {
    var $corpo = $("#corpo-dependentes").empty();
    $("#vazio-dependentes").toggle(!lista.length);
    lista.forEach(function (d) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(txt(d.no_dependente)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(d.nu_cpf)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(d.tx_relacao)).appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarData(d.dt_nascimento)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, "dependentes", d.id_dependente, "dependente " + (d.no_dependente || d.nu_cpf || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Rendimentos
  var ROTULO_ORIGEM = { titular: "Titular", dependente: "Dependente" };
  function renderRendimentos(lista) {
    var $corpo = $("#corpo-rendimentos").empty();
    $("#vazio-rendimentos").toggle(!lista.length);
    lista.forEach(function (r) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("irpf-badge " + (r.tp_origem || "")).text(ROTULO_ORIGEM[r.tp_origem] || r.tp_origem)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(r.no_fonte_pagadora)).appendTo($tr);
      var $doc = $("<td>").addClass("col-nome").appendTo($tr);
      if (r.nu_cnpj_cpf) {
        $doc.append(document.createTextNode(r.nu_cnpj_cpf + " "))
          .append($("<span>").addClass("irpf-badge " + (ehPJ(r.nu_cnpj_cpf) ? "pj" : "pf")).text(ehPJ(r.nu_cnpj_cpf) ? "PJ" : "PF"));
      } else {
        $doc.text("—");
      }
      $("<td>").addClass("col-nome irpf-valor").text(moeda(r.vl_rendimento)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, "rendimentos", r.id_rendimento, "rendimento " + (r.no_fonte_pagadora || r.nu_cnpj_cpf || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Doações
  var ROTULO_DOACAO = { efetuada: "Efetuada", partido: "Partido/candidato" };
  function renderDoacoes(lista) {
    var $corpo = $("#corpo-doacoes").empty();
    $("#vazio-doacoes").toggle(!lista.length);
    lista.forEach(function (d) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("irpf-badge " + (d.tp_doacao || "")).text(ROTULO_DOACAO[d.tp_doacao] || d.tp_doacao)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(d.no_beneficiario)).appendTo($tr);
      var $doc = $("<td>").addClass("col-nome").appendTo($tr);
      if (d.nu_cnpj_cpf) {
        $doc.append(document.createTextNode(d.nu_cnpj_cpf + " "))
          .append($("<span>").addClass("irpf-badge " + (ehPJ(d.nu_cnpj_cpf) ? "pj" : "pf")).text(ehPJ(d.nu_cnpj_cpf) ? "PJ" : "PF"));
      } else {
        $doc.text("—");
      }
      $("<td>").addClass("col-nome irpf-valor").text(moeda(d.vl_doacao)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      botaoEntidade($acoes, "doacoes", d.id_doacao, "doação " + (d.no_beneficiario || d.nu_cnpj_cpf || ""));
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

  function aplicarBens() {
    var termo = $.trim($("#f-bem-busca").val()).toLowerCase();
    var ordem = $("#f-bem-ordem").val();
    var lista = dados.bens.filter(function (b) { return contemTexto(b, termo); });
    lista.sort(function (a, b) {
      if (ordem === "variacao") return (num(b.vl_ano_atual) - num(b.vl_ano_anterior)) - (num(a.vl_ano_atual) - num(a.vl_ano_anterior));
      if (ordem === "codigo") return String(a.no_codigo || "").localeCompare(String(b.no_codigo || ""), "pt-BR");
      return num(b.vl_ano_atual) - num(a.vl_ano_atual);
    });
    renderLinhasValor("#corpo-bens", "#vazio-bens", lista, "bens");
    info("#f-bem-info", lista.length, dados.bens.length);
    atualizarTotal();
  }

  function aplicarDependentes() {
    var termo = $.trim($("#f-dep-busca").val()).toLowerCase();
    var ordem = $("#f-dep-ordem").val();
    var lista = dados.dependentes.filter(function (d) { return contemTexto(d, termo); });
    lista.sort(function (a, b) {
      if (ordem === "nascimento") return String(a.dt_nascimento || "9").localeCompare(String(b.dt_nascimento || "9"));
      return String(a.no_dependente || "").localeCompare(String(b.no_dependente || ""), "pt-BR");
    });
    renderDependentes(lista);
    info("#f-dep-info", lista.length, dados.dependentes.length);
    atualizarTotal();
  }

  function aplicarRendimentos() {
    var termo = $.trim($("#f-rend-busca").val()).toLowerCase();
    var origem = $("#f-rend-origem").val();
    var ordem = $("#f-rend-ordem").val();
    var lista = dados.rendimentos.filter(function (r) {
      if (origem && r.tp_origem !== origem) return false;
      return contemTexto(r, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "nome") return String(a.no_fonte_pagadora || "").localeCompare(String(b.no_fonte_pagadora || ""), "pt-BR");
      return num(b.vl_rendimento) - num(a.vl_rendimento);
    });
    renderRendimentos(lista);
    info("#f-rend-info", lista.length, dados.rendimentos.length);
    atualizarTotal();
  }

  function aplicarDividas() {
    var termo = $.trim($("#f-div-busca").val()).toLowerCase();
    var ordem = $("#f-div-ordem").val();
    var lista = dados.dividas.filter(function (d) { return contemTexto(d, termo); });
    lista.sort(function (a, b) {
      if (ordem === "variacao") return (num(b.vl_ano_atual) - num(b.vl_ano_anterior)) - (num(a.vl_ano_atual) - num(a.vl_ano_anterior));
      if (ordem === "codigo") return String(a.no_codigo || "").localeCompare(String(b.no_codigo || ""), "pt-BR");
      return num(b.vl_ano_atual) - num(a.vl_ano_atual);
    });
    renderLinhasValor("#corpo-dividas", "#vazio-dividas", lista, "dividas");
    info("#f-div-info", lista.length, dados.dividas.length);
    atualizarTotal();
  }

  function aplicarDoacoes() {
    var termo = $.trim($("#f-doa-busca").val()).toLowerCase();
    var tipo = $("#f-doa-tipo").val();
    var ordem = $("#f-doa-ordem").val();
    var lista = dados.doacoes.filter(function (d) {
      if (tipo && d.tp_doacao !== tipo) return false;
      return contemTexto(d, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "nome") return String(a.no_beneficiario || "").localeCompare(String(b.no_beneficiario || ""), "pt-BR");
      return num(b.vl_doacao) - num(a.vl_doacao);
    });
    renderDoacoes(lista);
    info("#f-doa-info", lista.length, dados.doacoes.length);
    atualizarTotal();
  }

  // ------------------------------------------------------------------ abas
  var ROTULO_ABA = { bens: "Bens e Direitos", dependentes: "Dependentes", rendimentos: "Rendimentos", dividas: "Dívidas", doacoes: "Doações" };
  function rotuloAba(aba) {
    return ROTULO_ABA[aba] + " (" + (dados[aba] || []).length + ")";
  }
  function atualizarRotulosAbas() {
    $(".irpf-aba").each(function () { $(this).text(rotuloAba($(this).data("aba"))); });
  }
  function trocarAba(aba) {
    abaAtiva = aba;
    $(".irpf-aba").removeClass("ativa").filter('[data-aba="' + aba + '"]').addClass("ativa");
    $(".irpf-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    atualizarTotal();
  }
  $(".irpf-aba").on("click", function () { trocarAba($(this).data("aba")); });

  function atualizarTotal() {
    var visiveis = $("#corpo-" + abaAtiva + " > tr").length;
    $("#total-registros").text(visiveis.toLocaleString("pt-BR") + " " + ROTULO_ABA[abaAtiva].toLowerCase());
  }

  // binds de filtro
  $("#f-bem-busca, #f-bem-ordem").on("input change", aplicarBens);
  $("#f-dep-busca, #f-dep-ordem").on("input change", aplicarDependentes);
  $("#f-rend-busca, #f-rend-origem, #f-rend-ordem").on("input change", aplicarRendimentos);
  $("#f-div-busca, #f-div-ordem").on("input change", aplicarDividas);
  $("#f-doa-busca, #f-doa-tipo, #f-doa-ordem").on("input change", aplicarDoacoes);

  // ------------------------------------------------------------------ carga
  function carregar(aba) {
    return $.getJSON("/api/investigacao/declaracoes-irpf/" + idIrpf + "/" + aba).done(function (lista) {
      dados[aba] = lista || [];
    });
  }

  function carregarContagens(tp) {
    var d = $.Deferred();
    if (!idInquerito || !window.EntidadesUI) { d.resolve(); return d; }
    EntidadesUI.carregarContagens(idInquerito, tp, function (c) { contagensEnt[tp] = c || {}; d.resolve(); });
    return d;
  }

  if (!idIrpf) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  $.when(
    carregar("bens"), carregar("dependentes"), carregar("rendimentos"), carregar("dividas"), carregar("doacoes"),
    // só o nome do arquivo, sem o caminho em data/ (ver docs/organizacao_arquivos.md)
    $.getJSON("/api/investigacao/declaracoes-irpf/" + idIrpf).done(function (c) { cabecalho = window.AnalyserUtils.encurtarCaminhos(c); }),
    carregarContagens("irpf_bem"), carregarContagens("irpf_dependente"), carregarContagens("irpf_rendimento"),
    carregarContagens("irpf_divida"), carregarContagens("irpf_doacao")
  ).always(function () {
    renderCabecalho();
    atualizarRotulosAbas();
    aplicarBens();
    aplicarDependentes();
    aplicarRendimentos();
    aplicarDividas();
    aplicarDoacoes();
    trocarAba(ABAS.indexOf(abaInicial) !== -1 ? abaInicial : "bens");
  });

  // exportar Excel (aba ativa, respeitando o filtro atual)
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "declaracao_irpf",
      dados: function () {
        return ExportarExcel.lerTabela(document.getElementById("tab-" + abaAtiva));
      },
    });
  }

  // Botão "Prompt de IA da Declaração" — conversa restrita só a esta declaração de IRPF (ver
  // js/ia.js e funcoes/ia_investigacao.ferramentas_para_inquerito no backend)
  $("#prompt-ia-irpf-btn").on("click", function () {
    var titulo = "Declaração de IRPF " + ((cabecalho && (cabecalho.no_titular || cabecalho.no_arquivo)) || idIrpf);
    window.location.href = "ia.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=irpf&escopo_id=" + encodeURIComponent(idIrpf) +
      "&escopo_titulo=" + encodeURIComponent(titulo);
  });

  // Botão "Hipótese da mídia" — web/hipoteses.html filtrada nesta declaração (mesmo par de URL
  // do chat de IA por escopo); a visão do inquérito inteiro está no menu "Extras" da Investigação.
  $("#achados-analista-irpf-btn").on("click", function () {
    window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=irpf&escopo_id=" + encodeURIComponent(idIrpf) +
      "&escopo_titulo=" + encodeURIComponent("Declaração de IRPF " + ((cabecalho && (cabecalho.no_titular || cabecalho.no_arquivo)) || idIrpf));
  });
})();
