(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idIpei = paramUrl("ipei");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "alvos";
  var abaAtiva = "alvos";
  var ABAS = ["alvos", "vinculos", "anos", "fontes", "contas", "bens", "notas", "contatos"];
  var TODAS_ABAS = ABAS.concat(["grafo"]);
  var LIMITE_PAGINA = 100; // a planilha de anexo passa de três mil notas; sem isto a tabela trava

  // dados por aba (fonte da verdade; filtros/ordenação re-renderizam a partir daqui)
  var dados = { alvos: [], vinculos: [], anos: [], fontes: [], contas: [], bens: [], notas: [], contatos: [] };
  var cabecalho = null;

  // estado de visualização
  var filtroAlvo = "";        // no_alvo do filtro global (vale em todas as abas)
  var ordenacoes = {};        // aba -> { chave, dir } (clique no cabeçalho)
  var paginas = {};           // aba -> página atual
  var ultimasListas = {};     // aba -> lista filtrada+ordenada COMPLETA (fonte da paginação/totais)

  // a aba do front tem nome de tela; a rota tem nome de recurso
  var ROTA_ABA = {
    alvos: "alvos", vinculos: "relacionados", anos: "anos", fontes: "fontes",
    contas: "contas", bens: "bens", notas: "notas", contatos: "contatos"
  };

  // contagens de "entidade/comentário" por id_origem (para destacar 📌). "anos" e "fontes" não
  // entram: são agregados calculados, não um registro que o analista anota.
  var TP_ORIGEM = {
    alvos: "ipei_alvo", vinculos: "ipei_relacionado", contas: "ipei_conta",
    bens: "ipei_bem", notas: "ipei_nota", contatos: "ipei_contato"
  };
  var CAMPO_ID = {
    alvos: "id_alvo", vinculos: "id_relacionado", contas: "id_conta",
    bens: "id_bem", notas: "id_nota", contatos: "id_contato"
  };
  var contagensEnt = {
    ipei_alvo: {}, ipei_relacionado: {}, ipei_conta: {}, ipei_bem: {}, ipei_nota: {}, ipei_contato: {}
  };

  // ---------------------------------------------------------------- helpers
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function temValor(v) { return v !== null && v !== undefined; }
  function moeda(v) {
    return temValor(v) ? "R$ " + num(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
  }
  function moedaCompacta(v) {
    if (v >= 1e9) return "R$ " + (v / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " bi";
    if (v >= 1e6) return "R$ " + (v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mi";
    if (v >= 1e3) return "R$ " + (v / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + " mil";
    return "R$ " + v.toLocaleString("pt-BR");
  }
  function numeroBR(v, casas) {
    return temValor(v) ? num(v).toLocaleString("pt-BR", { minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0 }) : "—";
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
  function periodo(item) {
    if (!item.nu_ano) return "—";
    return item.nu_mes ? (String(item.nu_mes).padStart(2, "0") + "/" + item.nu_ano) : String(item.nu_ano);
  }
  function rendimento(item) {
    return num(temValor(item.vl_rend_total) ? item.vl_rend_total : item.vl_receita_bruta);
  }

  function celulaDocumento($tr, doc) {
    var $td = $("<td>").addClass("col-nome").appendTo($tr);
    if (!doc) { $td.text("—"); return; }
    $td.append(document.createTextNode(doc + " "))
      .append($("<span>").addClass("ipei-badge " + (ehPJ(doc) ? "pj" : "pf")).text(ehPJ(doc) ? "PJ" : "PF"));
  }

  // botão 📌 de entidade/marcação
  function botaoEntidade($td, aba, id, rotulo) {
    if (!idInquerito || !window.EntidadesUI || !id || !TP_ORIGEM[aba]) return;
    var tp = TP_ORIGEM[aba];
    $td.append(EntidadesUI.botao({
      idInquerito: idInquerito, tpOrigem: tp, idOrigem: id, rotulo: rotulo,
      temComentario: !!contagensEnt[tp][id], aoMudar: function () {}
    }));
  }

  /* Botão 📄 "ver no PDF" — rastreabilidade probatória: cada linha aponta para o documento
   * original (PDF ou planilha de anexo), com o trecho que identifica o registro. O trecho viaja
   * como ?trecho= e o documento.html realça onde casar; se não casar, abre no começo com aviso
   * (comportamento do próprio documento.js, sem mudança aqui). */
  function botaoPdf(aba, item) {
    if (!idInquerito || !window.DocTrecho || !cabecalho) return null;
    var doc;
    if (aba === "notas" && item.tp_origem === "anexo") doc = cabecalho.id_documento_anexo;
    else doc = cabecalho.id_documento;
    if (!doc) return null;
    var trecho = "";
    if (aba === "vinculos") trecho = item.tx_contexto || item.nu_cpf_cnpj || item.no_relacionado || "";
    else if (aba === "alvos" || aba === "fontes") trecho = item.nu_cpf_cnpj || item.no_alvo || "";
    else if (aba === "contas") trecho = item.nu_conta || "";
    else if (aba === "bens") trecho = item.nu_identificador || "";
    else if (aba === "notas") trecho = item.no_emitente || "";
    else if (aba === "contatos") trecho = item.tx_valor || "";
    else trecho = item.no_alvo || "";
    var url = DocTrecho.url({ inquerito: idInquerito, doc: doc, trecho: trecho || undefined });
    return $("<a>").addClass("ent-botao ipei-pdf").attr("href", url)
      .attr("title", "Ver no documento original (PDF/texto extraído)").html("📄");
  }
  function comPdf($td, aba, item) {
    var $a = botaoPdf(aba, item);
    if ($a) $td.append($a);
  }

  // ------------------------------------------------- cabeçalho (topo)
  function renderCabecalho() {
    var $i = $("#ipei-ident").empty();
    if (!cabecalho) return;
    $("#ipei-numero").text(cabecalho.nu_ipei ? "· IPEI " + cabecalho.nu_ipei : (cabecalho.no_arquivo || ""));

    var campos = [
      { rot: "IPEI nº", val: cabecalho.nu_ipei },
      { rot: "Emitida em", val: cabecalho.dt_ipei ? formatarData(cabecalho.dt_ipei) : null },
      { rot: "Unidade emissora", val: cabecalho.no_unidade },
      { rot: "Processo judicial", val: cabecalho.nu_processo_judicial },
      { rot: "Decisão que afastou o sigilo", val: cabecalho.dt_decisao ? formatarData(cabecalho.dt_decisao) : null },
      { rot: "Folhas", val: cabecalho.qt_folhas ? numeroBR(cabecalho.qt_folhas) : null },
      { rot: "Auditores signatários", val: cabecalho.no_assinantes },
      { rot: "Arquivo (PDF)", val: cabecalho.no_arquivo },
      // o par PDF + planilha é uma importação só; quando não veio anexo, isso precisa ficar claro
      // (várias tabelas do relatório dizem "segue em anexo" e ficariam vazias sem explicação)
      { rot: "Planilha de anexo", val: cabecalho.no_arquivo_anexo || "não importada" }
    ];
    campos.forEach(function (c) {
      if (!c.val) return;
      var $c = $("<div>").addClass("ipei-campo").appendTo($i);
      $("<div>").addClass("rot").text(c.rot).appendTo($c);
      $("<div>").addClass("val").text(c.val).appendTo($c);
    });

    var conclusao = cabecalho.tx_conclusao || "";
    $("#ipei-conclusao").toggleClass("hidden", !conclusao);
    $("#ipei-conclusao-txt").text(conclusao);
  }

  // ------------------------------------------------ indicadores (KPIs)
  // Os indicadores, os indícios e a rede resumem o RELATÓRIO, então seguem só o filtro global
  // por alvo — nunca os filtros locais de cada aba (digitar na busca de Notas não pode mudar o
  // "Razão mov./rend." do topo, senão o investigador perde a referência do conjunto).
  function listaPorAlvo(aba) {
    return filtroDeAlvo(dados[aba] || []);
  }
  function renderKpis() {
    var alvos = listaPorAlvo("alvos");
    var anos = listaPorAlvo("anos");
    var contas = listaPorAlvo("contas");
    var mov = 0, rend = 0, decred = 0, bens = 0, nfe = 0, razaoMax = 0;
    anos.forEach(function (a) {
      mov += num(a.vl_mov_creditos);
      rend += rendimento(a);
      decred += num(a.vl_decred);
      bens += num(a.vl_bens_ano);
      nfe += num(a.vl_nfe_compras);
      razaoMax = Math.max(razaoMax, num(a.vl_razao_mov_rend));
    });
    var razaoGeral = rend > 0 ? mov / rend : 0;
    var tiles = [
      { rot: "Alvos", val: String(alvos.length) },
      { rot: "Contas (e-Fin.)", val: String(contas.length) },
      { rot: "Movimentação (créditos)", val: moeda(mov) },
      { rot: "Rendimento declarado", val: moeda(rend) },
      { rot: "Razão mov./rend.", val: numeroBR(razaoGeral, 2) + "x", classe: classeRazao(razaoGeral) },
      { rot: "Maior razão (ano)", val: razaoMax > 0 ? numeroBR(razaoMax, 2) + "x" : "—", classe: classeRazao(razaoMax) },
      { rot: "DECRED", val: moeda(decred) },
      { rot: "Bens em 31/12", val: moeda(bens) },
      { rot: "Compras em NFe", val: moeda(nfe) }
    ];
    var $k = $("#ipei-kpis").empty();
    tiles.forEach(function (t) {
      var $tile = $("<div>").addClass("ipei-kpi").appendTo($k);
      $("<div>").addClass("rot").text(t.rot).appendTo($tile);
      $("<div>").addClass("val " + (t.classe || "")).text(t.val).appendTo($tile);
    });
  }

  // ------------------------------------------------- indícios automáticos
  // Regras simples, transparentes e citáveis em relatório: o clique leva à aba certa já filtrada,
  // para o investigador conferir o dado de origem antes de usar o indício.
  function irParaAnosAlvo(nome) {
    $("#f-ano-busca").val(nome);
    paginas.anos = 1;
    aplicarAnos();
    trocarAba("anos");
    salvarEstadoUrl();
  }
  function irParaVinculosMac() {
    $("#f-vinc-busca").val("");
    $("#f-vinc-tipo").val("mac_coincidente");
    paginas.vinculos = 1;
    aplicarVinculos();
    trocarAba("vinculos");
    salvarEstadoUrl();
  }
  function irParaVinculosNome(nome) {
    $("#f-vinc-busca").val(nome);
    $("#f-vinc-tipo").val("");
    paginas.vinculos = 1;
    aplicarVinculos();
    trocarAba("vinculos");
    salvarEstadoUrl();
  }

  function renderIndicios() {
    var itens = [];
    var vistos = {};
    function push(nivel, texto, acao) {
      var chave = nivel + "|" + texto;
      if (vistos[chave]) return;
      vistos[chave] = true;
      itens.push({ nivel: nivel, texto: texto, acao: acao });
    }
    (listaPorAlvo("anos")).forEach(function (a) {
      var rend = rendimento(a);
      var r = num(a.vl_razao_mov_rend);
      if (r >= 10) push("alta", "Razão mov./rend. de " + numeroBR(r, 2) + "x — " + a.no_alvo + " em " + a.nu_ano, function () { irParaAnosAlvo(a.no_alvo); });
      else if (r >= 3) push("media", "Razão mov./rend. de " + numeroBR(r, 2) + "x — " + a.no_alvo + " em " + a.nu_ano, function () { irParaAnosAlvo(a.no_alvo); });
      if (rend > 0 && num(a.vl_nfe_compras) > rend) push("media", "Compras em NFe acima da renda — " + a.no_alvo + " em " + a.nu_ano, function () { irParaAnosAlvo(a.no_alvo); });
      if (rend > 0 && num(a.vl_bens_ano) > rend) push("media", "Bens em 31/12 acima da renda — " + a.no_alvo + " em " + a.nu_ano, function () { irParaAnosAlvo(a.no_alvo); });
    });
    listaPorAlvo("vinculos").forEach(function (v) {
      if (v.tp_relacao === "mac_coincidente") {
        push("alta", "Mesmo endereço MAC: " + v.no_alvo + " ↔ " + v.no_relacionado, irParaVinculosMac);
      }
    });
    var $it = $("#ipei-indicios-itens").empty();
    $("#ipei-indicios").toggleClass("hidden", !itens.length);
    itens.forEach(function (it) {
      $("<button>").attr("type", "button").addClass("ipei-indicio " + it.nivel)
        .attr("title", "Clique para abrir a aba correspondente já filtrada")
        .text(it.texto).on("click", it.acao).appendTo($it);
    });
  }

  // ------------------------------------------------------- popular selects
  function popularSelect($sel, valores, rotulo) {
    valores.sort(function (a, b) { return String(a).localeCompare(String(b), "pt-BR", { numeric: true }); });
    valores.forEach(function (v) {
      $("<option>").val(v).text(rotulo ? rotulo(v) : v).appendTo($sel);
    });
  }
  function definirSelect(sel, val) {
    if (!val) return;
    $(sel).find("option").each(function () {
      if ($(this).val() === val) $(sel).val(val);
    });
  }

  // ---------------------------------------------------------- render Alvos
  function renderAlvos(lista) {
    var $corpo = $("#corpo-alvos").empty();
    lista.forEach(function (a) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge " + (a.tp_pessoa === "PJ" ? "pj" : "pf")).text(a.tp_pessoa || "PF")
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(a.no_alvo)).appendTo($tr);
      celulaDocumento($tr, a.nu_cpf_cnpj);
      $("<td>").addClass("col-nome").text(formatarData(a.dt_nascimento)).appendTo($tr);
      var endereco = [a.tx_endereco, [a.no_municipio, a.sg_uf].filter(Boolean).join("/"), a.nu_cep]
        .filter(Boolean).join(" — ");
      $("<td>").addClass("col-nome ipei-texto").text(txt(endereco)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(a.tx_ocupacao)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(a.tx_situacao_cadastral)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "alvos", a);
      botaoEntidade($acoes, "alvos", a.id_alvo, "alvo " + (a.no_alvo || a.nu_cpf_cnpj || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Vínculos
  var ROTULO_VINCULO = {
    societario: "Societário", empregaticio: "Empregatício", procuracao: "Procuração",
    procuracao_rfb: "Procuração RFB", mac_coincidente: "Mesmo MAC"
  };
  function renderVinculos(lista) {
    var $corpo = $("#corpo-vinculos").empty();
    lista.forEach(function (v) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge " + (v.tp_relacao || "")).text(ROTULO_VINCULO[v.tp_relacao] || v.tp_relacao)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(v.no_alvo)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(v.no_relacionado)).appendTo($tr);
      celulaDocumento($tr, v.nu_cpf_cnpj);
      // a linha crua do PDF fica no title: a leitura da tabela é aproximada e é assim que o
      // investigador confere sem voltar ao documento
      $("<td>").addClass("col-nome ipei-texto").attr("title", v.tx_contexto || "").text(txt(v.tx_qualificacao)).appendTo($tr);
      var datas = [v.dt_inicio ? formatarData(v.dt_inicio) : null, v.dt_fim ? formatarData(v.dt_fim) : null]
        .filter(Boolean).join(" — ");
      $("<td>").addClass("col-nome").text(txt(datas)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(temValor(v.pc_participacao) ? numeroBR(v.pc_participacao, 2) + "%" : "—").appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "vinculos", v);
      botaoEntidade($acoes, "vinculos", v.id_relacionado, "vínculo " + (v.no_relacionado || v.nu_cpf_cnpj || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Ano a ano
  // limiares da razão movimentação/rendimento: acima de 3 já pede explicação, acima de 10 é o
  // tipo de salto que o próprio relatório destaca em texto
  function classeRazao(v) {
    if (!temValor(v)) return "";
    if (num(v) >= 10) return "alta";
    if (num(v) >= 3) return "media";
    return "";
  }
  function renderAnos(lista) {
    var $corpo = $("#corpo-anos").empty();
    lista.forEach(function (a) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(txt(a.no_alvo)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(a.nu_ano)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(temValor(a.vl_rend_total) ? a.vl_rend_total : a.vl_receita_bruta)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(a.vl_mov_creditos)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor ipei-razao " + classeRazao(a.vl_razao_mov_rend))
        .text(temValor(a.vl_razao_mov_rend) ? numeroBR(a.vl_razao_mov_rend, 2) + "x" : "—").appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(a.vl_decred)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(a.vl_nfe_compras)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(a.vl_bens_ano)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(a.vl_variacao_patrimonial)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "anos", a);
      $corpo.append($tr);
    });
  }

  // gráfico da aba Ano a ano — a incompatibilidade mov. × rend. salta aos olhos em barras
  function renderGraficoAnos(lista) {
    var svg = document.getElementById("ipei-grafico-anos");
    var $wrap = $("#ipei-grafico-anos-wrap");
    var $tit = $("#ipei-grafico-anos-titulo");
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!lista.length) { $wrap.addClass("hidden"); return; }
    var porAno = {};
    lista.forEach(function (a) {
      if (!a.nu_ano) return;
      var reg = porAno[a.nu_ano] || (porAno[a.nu_ano] = { rend: 0, mov: 0 });
      reg.rend += rendimento(a);
      reg.mov += num(a.vl_mov_creditos);
    });
    var anos = Object.keys(porAno).sort(function (a, b) { return Number(a) - Number(b); });
    if (!anos.length) { $wrap.addClass("hidden"); return; }
    $wrap.removeClass("hidden");

    var margemX = 66, altura = 230, areaTop = 14, areaBottom = altura - 26;
    var passo = 64;
    var largura = Math.max(560, margemX + anos.length * passo + 20);
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", altura);
    svg.style.minWidth = largura + "px";
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Gráfico de movimentação e rendimento por ano");

    var maximo = anos.reduce(function (m, y) { return Math.max(m, porAno[y].mov, porAno[y].rend); }, 0) || 1;
    var NS = "http://www.w3.org/2000/svg";
    function el(nome, attrs) {
      var e = document.createElementNS(NS, nome);
      Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      return e;
    }
    function yVal(v) { return areaBottom - (v / maximo) * (areaBottom - areaTop); }
    function xAno(i) { return margemX + i * passo + passo / 2; }

    [0, 0.25, 0.5, 0.75, 1].forEach(function (frac) {
      var y = yVal(maximo * frac);
      svg.appendChild(el("line", { x1: margemX, y1: y, x2: largura, y2: y, stroke: "currentColor", opacity: 0.15, "stroke-dasharray": "4,4" }));
      var rot = el("text", { x: margemX - 6, y: y + 3, "text-anchor": "end", "class": "ipei-grafico-rotulo" });
      rot.textContent = moedaCompacta(maximo * frac);
      svg.appendChild(rot);
    });
    svg.appendChild(el("line", { x1: margemX, y1: areaBottom, x2: largura, y2: areaBottom, stroke: "currentColor", opacity: 0.35 }));

    var largBarra = Math.max(8, Math.min(22, passo / 4));
    anos.forEach(function (y, i) {
      var x = xAno(i);
      [["rend", "#2563eb", "Rendimento / receita"], ["mov", "#dc2626", "Movimentação (créditos)"]].forEach(function (s, j) {
        var v = porAno[y][s[0]];
        if (v <= 0) return;
        var h = Math.max(1, (v / maximo) * (areaBottom - areaTop));
        var rect = el("rect", { x: x + (j - 0.5) * (largBarra + 2), y: areaBottom - h, width: largBarra, height: h, rx: 2, fill: s[1] });
        var t = el("title", {});
        t.textContent = y + " · " + s[2] + ": " + moeda(v);
        rect.appendChild(t);
        svg.appendChild(rect);
      });
      var rot = el("text", { x: x, y: altura - 10, "text-anchor": "middle", "class": "ipei-grafico-rotulo" });
      rot.textContent = y;
      svg.appendChild(rot);
    });
    $tit.text("Movimentação × rendimento por ano — pico de " + moeda(maximo) + " em um único ano");
  }

  // ------------------------------------------------------- render Fontes
  var ROTULO_FONTE = {
    pessoa_juridica: "Pessoa jurídica", isento: "Isento", exclusiva: "Exclusiva",
    pagamento: "Pagamento", servico_efd: "Serviço (EFD)"
  };
  function renderFontes(lista) {
    var $corpo = $("#corpo-fontes").empty();
    lista.forEach(function (f) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge").text(ROTULO_FONTE[f.tp_registro] || f.tp_registro)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(f.nu_ano)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(f.no_fonte)).appendTo($tr);
      celulaDocumento($tr, f.nu_cpf_cnpj);
      $("<td>").addClass("col-nome ipei-texto").text(txt(f.tx_natureza)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(f.vl_rendimento)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(f.vl_imposto)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "fontes", f);
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Contas
  function renderContas(lista) {
    var $corpo = $("#corpo-contas").empty();
    lista.forEach(function (c) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(txt(c.no_alvo)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(c.no_banco)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.nu_conta)).appendTo($tr);
      var tipo = c.tp_conta || "";
      if (c.no_titular) tipo += (tipo ? " · " : "") + c.no_titular + (c.tx_relacao ? " (" + c.tx_relacao + ")" : "");
      $("<td>").addClass("col-nome ipei-texto").text(txt(tipo)).appendTo($tr);
      $("<td>").addClass("col-nome").text(periodo(c)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(c.vl_creditos)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(c.vl_debitos)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(c.vl_saldo)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "contas", c);
      botaoEntidade($acoes, "contas", c.id_conta, "conta " + (c.nu_conta || "") + " " + periodo(c));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Bens
  var ROTULO_BEM = {
    declarado: "Declarado (IRPF)", divida: "Dívida", imovel: "Imóvel (DOI)",
    imovel_dimob: "Imóvel (Dimob)", criptoativo: "Criptoativo", especie: "Espécie (DME)",
    veiculo: "Veículo (RENAVAM)", veiculo_outras_bases: "Veículo (outras bases)", outro: "Outro"
  };
  function renderBens(lista) {
    var $corpo = $("#corpo-bens").empty();
    lista.forEach(function (b) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge").text(ROTULO_BEM[b.tp_bem] || b.tp_bem)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(b.no_alvo)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(b.nu_ano)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(b.nu_identificador)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(b.tx_descricao)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(b.vl_ano_anterior)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(b.vl_ano)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(b.tx_situacao)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "bens", b);
      botaoEntidade($acoes, "bens", b.id_bem, "bem " + (b.nu_identificador || b.tx_descricao || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Notas
  function renderNotas(lista) {
    var $corpo = $("#corpo-notas").empty();
    lista.forEach(function (n) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge " + (n.tp_origem === "anexo" ? "anexo" : "")).text(n.tp_origem === "anexo" ? "Anexo" : "PDF")
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarData(n.dt_emissao)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(n.no_emitente)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(n.no_destinatario)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(n.tx_descricao)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(temValor(n.qt_item) ? numeroBR(n.qt_item, 2) : "—").appendTo($tr);
      $("<td>").addClass("col-nome ipei-valor").text(moeda(n.vl_total)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "notas", n);
      botaoEntidade($acoes, "notas", n.id_nota, "nota " + (n.tx_descricao || n.no_emitente || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------- render Contatos
  var ROTULO_CONTATO = { endereco: "Endereço", telefone: "Telefone", email: "E-mail", mac: "MAC / IP" };
  function renderContatos(lista) {
    var $corpo = $("#corpo-contatos").empty();
    lista.forEach(function (c) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").append(
        $("<span>").addClass("ipei-badge").text(ROTULO_CONTATO[c.tp_contato] || c.tp_contato)
      ).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.no_alvo)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.nu_ano)).appendTo($tr);
      $("<td>").addClass("col-nome ipei-texto").text(txt(c.tx_valor)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      comPdf($acoes, "contatos", c);
      botaoEntidade($acoes, "contatos", c.id_contato, (ROTULO_CONTATO[c.tp_contato] || "") + " " + (c.tx_valor || ""));
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------ rede de vínculos
  var CORES_VINCULO = {
    societario: "#d97706", empregaticio: "#2563eb", procuracao: "#14b8a6",
    procuracao_rfb: "#14b8a6", mac_coincidente: "#dc2626"
  };
  function renderGrafo() {
    var alvos = listaPorAlvo("alvos");
    var vincs = listaPorAlvo("vinculos");
    var svg = $("#ipei-grafo").get(0);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    $("#ipei-grafo-legenda").empty();
    $("#ipei-grafo").off("click");
    if (!alvos.length || !vincs.length) {
      $("#vazio-grafo").show();
      $("#ipei-grafo").addClass("hidden");
      return;
    }
    $("#vazio-grafo").hide();
    $("#ipei-grafo").removeClass("hidden");

    var W = 940, H = 560, cx = W / 2, cy = H / 2;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", "100%");
    svg.style.minWidth = "700px";
    svg.style.maxHeight = "560px";
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Rede de vínculos da IPEI");

    // nós: alvos num anel interno (ou no centro, quando um só), relacionados no anel externo
    var nos = {};
    alvos.forEach(function (a, i) {
      var x = cx, y = cy;
      if (alvos.length > 1) {
        var ang = -Math.PI / 2 + (i / alvos.length) * Math.PI * 2;
        x = cx + Math.cos(ang) * 70;
        y = cy + Math.sin(ang) * 70;
      }
      nos["a" + a.id_alvo] = { nome: a.no_alvo, x: x, y: y, alvo: true };
    });
    var relMap = {};
    vincs.forEach(function (v) {
      if (!relMap[v.id_relacionado]) relMap[v.id_relacionado] = { nome: v.no_relacionado, doc: v.nu_cpf_cnpj };
    });
    var relIds = Object.keys(relMap);
    relIds.forEach(function (rid, i) {
      var ang = -Math.PI / 2 + (i / relIds.length) * Math.PI * 2;
      var r = Math.min(W, H) / 2 - 90;
      nos["r" + rid] = { nome: relMap[rid].nome, doc: relMap[rid].doc, x: cx + Math.cos(ang) * r, y: cy + Math.sin(ang) * r, alvo: false };
    });

    var NS = "http://www.w3.org/2000/svg";
    function el(nome, attrs) {
      var e = document.createElementNS(NS, nome);
      Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      return e;
    }
    function titulo(pai, texto) {
      var t = el("title", {});
      t.textContent = texto;
      pai.appendChild(t);
      return t;
    }

    vincs.forEach(function (v) {
      var origem = nos["a" + v.id_alvo];
      var destino = nos["r" + v.id_relacionado];
      if (!origem || !destino) return;
      var linha = el("line", {
        x1: origem.x, y1: origem.y, x2: destino.x, y2: destino.y,
        stroke: CORES_VINCULO[v.tp_relacao] || "#64748b", "stroke-opacity": 0.55,
        "stroke-width": v.tp_relacao === "mac_coincidente" ? 2.5 : 1.4
      });
      titulo(linha, origem.nome + " — " + destino.nome + " · " + (ROTULO_VINCULO[v.tp_relacao] || v.tp_relacao));
      svg.appendChild(linha);
    });

    Object.keys(nos).forEach(function (k) {
      var n = nos[k];
      var g = el("g", { "class": "ipei-grafo-no", "data-nome": n.nome || "", "data-alvo": n.alvo ? "1" : "" });
      var raio = n.alvo ? 13 : 8;
      g.appendChild(el("circle", {
        cx: n.x, cy: n.y, r: raio,
        fill: n.alvo ? "#2563eb" : "#475569",
        stroke: "rgba(255,255,255,.35)", "stroke-width": 1
      }));
      var nome = n.nome || "?";
      var rotulo = el("text", { x: n.x, y: n.y + raio + 13, "text-anchor": "middle", "class": "ipei-grafo-rotulo" });
      rotulo.textContent = nome.length > 22 ? nome.slice(0, 21) + "…" : nome;
      g.appendChild(rotulo);
      titulo(g, (n.alvo ? "Alvo: " : "Relacionado: ") + nome + (n.doc ? " · " + n.doc : "") +
        (n.alvo ? " — clique para filtrar a IPEI inteira" : " — clique para ver os vínculos"));
      svg.appendChild(g);
    });

    $("#ipei-grafo").on("click", ".ipei-grafo-no", function () {
      var nome = $(this).attr("data-nome");
      if (!nome) return;
      if ($(this).attr("data-alvo") === "1") {
        $("#f-alvo-global").val(nome).trigger("change");
      } else {
        irParaVinculosNome(nome);
      }
    });

    // legenda só com os tipos que aparecem
    var tipos = {};
    vincs.forEach(function (v) { tipos[v.tp_relacao] = true; });
    var $leg = $("#ipei-grafo-legenda");
    Object.keys(ROTULO_VINCULO).forEach(function (tp) {
      if (!tipos[tp]) return;
      $("<span>").addClass("ipei-badge " + tp).text(ROTULO_VINCULO[tp]).appendTo($leg);
    });
    $("<span>").addClass("ipei-chave").append($("<i>").css("background", "#2563eb")).append("Alvo").appendTo($leg);
    $("<span>").addClass("ipei-chave").append($("<i>").css("background", "#475569")).append("Relacionado").appendTo($leg);
  }

  // ----------------------------------------------------------- filtragem
  function contemTexto(obj, termo) {
    if (!termo) return true;
    return JSON.stringify(obj).toLowerCase().indexOf(termo) !== -1;
  }
  function info(id, mostrados, total) {
    $(id).text(mostrados === total ? "" : mostrados.toLocaleString("pt-BR") + " de " + total.toLocaleString("pt-BR"));
  }
  function termoDe(id) { return $.trim($(id).val() || "").toLowerCase(); }

  function filtroDeAlvo(lista) {
    if (!filtroAlvo) return lista;
    return lista.filter(function (i) { return i.no_alvo === filtroAlvo; });
  }

  // --------------------------- ordenação por clique no cabeçalho (cliente)
  var SORT_EXTRAI = {
    alvos: {
      no_alvo: function (i) { return i.no_alvo || ""; },
      nu_cpf_cnpj: function (i) { return i.nu_cpf_cnpj || ""; },
      dt_nascimento: function (i) { return i.dt_nascimento || ""; },
      tx_situacao_cadastral: function (i) { return i.tx_situacao_cadastral || ""; }
    },
    vinculos: {
      tp_relacao: function (i) { return ROTULO_VINCULO[i.tp_relacao] || i.tp_relacao || ""; },
      no_alvo: function (i) { return i.no_alvo || ""; },
      no_relacionado: function (i) { return i.no_relacionado || ""; },
      nu_cpf_cnpj: function (i) { return i.nu_cpf_cnpj || ""; },
      pc_participacao: function (i) { return num(i.pc_participacao); },
      dt_inicio: function (i) { return i.dt_inicio || ""; }
    },
    anos: {
      no_alvo: function (i) { return i.no_alvo || ""; },
      nu_ano: function (i) { return num(i.nu_ano); },
      rend: function (i) { return rendimento(i); },
      mov: function (i) { return num(i.vl_mov_creditos); },
      razao: function (i) { return num(i.vl_razao_mov_rend); },
      decred: function (i) { return num(i.vl_decred); },
      nfe: function (i) { return num(i.vl_nfe_compras); },
      bens: function (i) { return num(i.vl_bens_ano); },
      varia: function (i) { return num(i.vl_variacao_patrimonial); }
    },
    fontes: {
      tp_registro: function (i) { return ROTULO_FONTE[i.tp_registro] || i.tp_registro || ""; },
      nu_ano: function (i) { return num(i.nu_ano); },
      no_fonte: function (i) { return i.no_fonte || ""; },
      nu_cpf_cnpj: function (i) { return i.nu_cpf_cnpj || ""; },
      vl_rendimento: function (i) { return num(i.vl_rendimento); },
      vl_imposto: function (i) { return num(i.vl_imposto); }
    },
    contas: {
      no_alvo: function (i) { return i.no_alvo || ""; },
      no_banco: function (i) { return i.no_banco || ""; },
      nu_conta: function (i) { return i.nu_conta || ""; },
      periodo: function (i) { return num(i.nu_ano) * 100 + num(i.nu_mes); },
      vl_creditos: function (i) { return num(i.vl_creditos); },
      vl_debitos: function (i) { return num(i.vl_debitos); },
      vl_saldo: function (i) { return num(i.vl_saldo); }
    },
    bens: {
      tp_bem: function (i) { return ROTULO_BEM[i.tp_bem] || i.tp_bem || ""; },
      no_alvo: function (i) { return i.no_alvo || ""; },
      nu_ano: function (i) { return num(i.nu_ano); },
      nu_identificador: function (i) { return i.nu_identificador || ""; },
      vl_ano_anterior: function (i) { return num(i.vl_ano_anterior); },
      vl_ano: function (i) { return num(i.vl_ano); },
      tx_situacao: function (i) { return i.tx_situacao || ""; }
    },
    notas: {
      tp_origem: function (i) { return i.tp_origem || ""; },
      dt_emissao: function (i) { return i.dt_emissao || ""; },
      no_emitente: function (i) { return i.no_emitente || ""; },
      qt_item: function (i) { return num(i.qt_item); },
      vl_total: function (i) { return num(i.vl_total); }
    },
    contatos: {
      tp_contato: function (i) { return ROTULO_CONTATO[i.tp_contato] || i.tp_contato || ""; },
      no_alvo: function (i) { return i.no_alvo || ""; },
      nu_ano: function (i) { return num(i.nu_ano); },
      tx_valor: function (i) { return i.tx_valor || ""; }
    }
  };

  function compararValores(a, b) {
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a == null ? "" : a).localeCompare(String(b == null ? "" : b), "pt-BR", { numeric: true });
  }
  function ordenarLista(aba, lista) {
    var ord = ordenacoes[aba];
    if (!ord) return;
    var extrai = SORT_EXTRAI[aba] && SORT_EXTRAI[aba][ord.chave];
    if (!extrai) return;
    lista.forEach(function (item, i) { item.__ord = i; });
    lista.sort(function (a, b) {
      var r = compararValores(extrai(a), extrai(b));
      if (r) return ord.dir === "asc" ? r : -r;
      return (a.__ord || 0) - (b.__ord || 0);
    });
  }
  function atualizarCabecalho(aba) {
    var ord = ordenacoes[aba];
    $("#tab-" + aba + " th").each(function () {
      var $th = $(this);
      var $btn = $th.find(".tp-ordenar");
      if (!$btn.length) return;
      var ativo = !!ord && ord.chave === $btn.attr("data-ordenar");
      $th.attr("aria-sort", ativo ? (ord.dir === "asc" ? "ascending" : "descending") : "none");
      $btn.toggleClass("tp-ordenar-ativo", ativo);
      $btn.find(".tp-seta").text(ativo ? (ord.dir === "asc" ? "▲" : "▼") : "↕");
    });
  }

  // -------------------------------------------------- paginação (cliente)
  function paginaItens(aba) {
    var lista = ultimasListas[aba] || [];
    var maxPag = Math.max(1, Math.ceil(lista.length / LIMITE_PAGINA));
    if (!(aba in paginas) || paginas[aba] < 1 || paginas[aba] > maxPag) paginas[aba] = 1;
    var ini = (paginas[aba] - 1) * LIMITE_PAGINA;
    return { itens: lista.slice(ini, ini + LIMITE_PAGINA), total: lista.length, maxPag: maxPag };
  }
  function renderPaginacao(aba, total) {
    var $pag = $("#pag-" + aba);
    if (total <= LIMITE_PAGINA) { $pag.addClass("hidden"); return; }
    var maxPag = Math.max(1, Math.ceil(total / LIMITE_PAGINA));
    $pag.removeClass("hidden");
    $("#pag-" + aba + "-info").text("Página " + paginas[aba] + " de " + maxPag.toLocaleString("pt-BR") +
      " · " + total.toLocaleString("pt-BR") + " registros");
    $("#pag-" + aba + "-ant").prop("disabled", paginas[aba] <= 1);
    $("#pag-" + aba + "-prox").prop("disabled", paginas[aba] >= maxPag);
  }

  // ------------------------------------------------- totais (rodapé da tabela)
  var TOTAIS = {
    anos: {
      rotulo: "Totais do filtro",
      razao: { mov: SORT_EXTRAI.anos.mov, rend: SORT_EXTRAI.anos.rend },
      cols: [
        { i: 2, extrai: SORT_EXTRAI.anos.rend },
        { i: 3, extrai: SORT_EXTRAI.anos.mov },
        { i: 4, tipo: "razao" },
        { i: 5, extrai: SORT_EXTRAI.anos.decred },
        { i: 6, extrai: SORT_EXTRAI.anos.nfe },
        { i: 7, extrai: SORT_EXTRAI.anos.bens },
        { i: 8, extrai: SORT_EXTRAI.anos.varia }
      ]
    },
    fontes: {
      rotulo: "Totais do filtro",
      cols: [
        { i: 5, extrai: SORT_EXTRAI.fontes.vl_rendimento },
        { i: 6, extrai: SORT_EXTRAI.fontes.vl_imposto }
      ]
    },
    contas: {
      rotulo: "Totais do filtro",
      cols: [
        { i: 5, extrai: SORT_EXTRAI.contas.vl_creditos },
        { i: 6, extrai: SORT_EXTRAI.contas.vl_debitos },
        { i: 7, extrai: SORT_EXTRAI.contas.vl_saldo }
      ]
    },
    bens: {
      rotulo: "Totais do filtro",
      cols: [
        { i: 5, extrai: SORT_EXTRAI.bens.vl_ano_anterior },
        { i: 6, extrai: SORT_EXTRAI.bens.vl_ano }
      ]
    },
    notas: {
      rotulo: "Totais do filtro",
      cols: [{ i: 6, extrai: SORT_EXTRAI.notas.vl_total }]
    }
  };
  function renderTotais(aba, lista) {
    var cfg = TOTAIS[aba];
    var $tfoot = $("#tot-" + aba).empty();
    if (!cfg) return;
    var n = $("#tab-" + aba + " thead th").length;
    var $tr = $("<tr>");
    for (var i = 0; i < n; i++) {
      var col = null;
      cfg.cols.forEach(function (c) { if (c.i === i) col = c; });
      var $td = $("<td>").addClass("col-nome");
      if (i === 0) {
        $td.text(cfg.rotulo);
      } else if (!col) {
        // vazio
      } else if (col.tipo === "razao") {
        var mov = lista.reduce(function (s, it) { return s + cfg.razao.mov(it); }, 0);
        var rend = lista.reduce(function (s, it) { return s + cfg.razao.rend(it); }, 0);
        if (rend > 0) {
          var r = mov / rend;
          $td.addClass("ipei-valor ipei-razao " + classeRazao(r)).text(numeroBR(r, 2) + "x");
        } else {
          $td.addClass("ipei-valor").text("—");
        }
      } else {
        var soma = lista.reduce(function (s, it) { return s + col.extrai(it); }, 0);
        $td.addClass("ipei-valor").text(moeda(soma));
      }
      $tr.append($td);
    }
    $tfoot.append($tr);
  }

  // ---------------------------------------------------------- APLICAR por aba
  function aplicarAlvos() {
    var lista = dados.alvos.filter(function (a) { return contemTexto(a, termoDe("#f-alvo-busca")); });
    lista = filtroDeAlvo(lista);
    ordenarLista("alvos", lista);
    ultimasListas.alvos = lista;
    var pg = paginaItens("alvos");
    renderAlvos(pg.itens);
    $("#vazio-alvos").toggle(!lista.length);
    info("#f-alvo-info", lista.length, dados.alvos.length);
    renderTotais("alvos", lista);
    renderPaginacao("alvos", pg.total);
    atualizarTotal();
  }

  function aplicarVinculos() {
    var tipo = $("#f-vinc-tipo").val();
    var lista = dados.vinculos.filter(function (v) {
      if (tipo && v.tp_relacao !== tipo) return false;
      return contemTexto(v, termoDe("#f-vinc-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("vinculos", lista);
    ultimasListas.vinculos = lista;
    var pg = paginaItens("vinculos");
    renderVinculos(pg.itens);
    $("#vazio-vinculos").toggle(!lista.length);
    info("#f-vinc-info", lista.length, dados.vinculos.length);
    renderPaginacao("vinculos", pg.total);
    atualizarTotal();
  }

  function aplicarAnos() {
    var lista = dados.anos.filter(function (a) { return contemTexto(a, termoDe("#f-ano-busca")); });
    lista = filtroDeAlvo(lista);
    ordenarLista("anos", lista);
    ultimasListas.anos = lista;
    var pg = paginaItens("anos");
    renderAnos(pg.itens);
    $("#vazio-anos").toggle(!lista.length);
    info("#f-ano-info", lista.length, dados.anos.length);
    renderTotais("anos", lista);
    renderPaginacao("anos", pg.total);
    renderGraficoAnos(lista);
    atualizarTotal();
  }

  function aplicarFontes() {
    var tipo = $("#f-fonte-tipo").val();
    var lista = dados.fontes.filter(function (f) {
      if (tipo && f.tp_registro !== tipo) return false;
      return contemTexto(f, termoDe("#f-fonte-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("fontes", lista);
    ultimasListas.fontes = lista;
    var pg = paginaItens("fontes");
    renderFontes(pg.itens);
    $("#vazio-fontes").toggle(!lista.length);
    info("#f-fonte-info", lista.length, dados.fontes.length);
    renderTotais("fontes", lista);
    renderPaginacao("fontes", pg.total);
    atualizarTotal();
  }

  function aplicarContas() {
    var ano = $("#f-conta-ano").val();
    var lista = dados.contas.filter(function (c) {
      if (ano && String(c.nu_ano) !== ano) return false;
      return contemTexto(c, termoDe("#f-conta-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("contas", lista);
    ultimasListas.contas = lista;
    var pg = paginaItens("contas");
    renderContas(pg.itens);
    $("#vazio-contas").toggle(!lista.length);
    info("#f-conta-info", lista.length, dados.contas.length);
    renderTotais("contas", lista);
    renderPaginacao("contas", pg.total);
    atualizarTotal();
  }

  function aplicarBens() {
    var tipo = $("#f-bem-tipo").val();
    var lista = dados.bens.filter(function (b) {
      if (tipo && b.tp_bem !== tipo) return false;
      return contemTexto(b, termoDe("#f-bem-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("bens", lista);
    ultimasListas.bens = lista;
    var pg = paginaItens("bens");
    renderBens(pg.itens);
    $("#vazio-bens").toggle(!lista.length);
    info("#f-bem-info", lista.length, dados.bens.length);
    renderTotais("bens", lista);
    renderPaginacao("bens", pg.total);
    atualizarTotal();
  }

  function aplicarNotas() {
    var origem = $("#f-nota-origem").val();
    var lista = dados.notas.filter(function (n) {
      if (origem && n.tp_origem !== origem) return false;
      return contemTexto(n, termoDe("#f-nota-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("notas", lista);
    ultimasListas.notas = lista;
    var pg = paginaItens("notas");
    renderNotas(pg.itens);
    $("#vazio-notas").toggle(!lista.length);
    info("#f-nota-info", lista.length, dados.notas.length);
    renderTotais("notas", lista);
    renderPaginacao("notas", pg.total);
    atualizarTotal();
  }

  function aplicarContatos() {
    var tipo = $("#f-cont-tipo").val();
    var lista = dados.contatos.filter(function (c) {
      if (tipo && c.tp_contato !== tipo) return false;
      return contemTexto(c, termoDe("#f-cont-busca"));
    });
    lista = filtroDeAlvo(lista);
    ordenarLista("contatos", lista);
    ultimasListas.contatos = lista;
    var pg = paginaItens("contatos");
    renderContatos(pg.itens);
    $("#vazio-contatos").toggle(!lista.length);
    info("#f-cont-info", lista.length, dados.contatos.length);
    renderPaginacao("contatos", pg.total);
    atualizarTotal();
  }

  var APLICAR = {
    alvos: aplicarAlvos, vinculos: aplicarVinculos, anos: aplicarAnos, fontes: aplicarFontes,
    contas: aplicarContas, bens: aplicarBens, notas: aplicarNotas, contatos: aplicarContatos
  };

  // ------------------------------------------------------------------ abas
  var ROTULO_ABA = {
    alvos: "Alvos", vinculos: "Vínculos", grafo: "Rede de vínculos", anos: "Ano a ano",
    fontes: "Fontes pagadoras", contas: "Contas", bens: "Bens", notas: "Notas fiscais",
    contatos: "Contatos"
  };
  function atualizarRotulosAbas() {
    $(".ipei-aba").each(function () {
      var aba = $(this).data("aba");
      var n = aba === "grafo"
        ? listaPorAlvo("vinculos").length
        : ((ultimasListas[aba] || dados[aba] || [])).length;
      $(this).text(ROTULO_ABA[aba] + " (" + n + ")");
    });
  }
  function trocarAba(aba) {
    abaAtiva = aba;
    $(".ipei-aba").removeClass("ativa").filter('[data-aba="' + aba + '"]').addClass("ativa");
    $(".ipei-aba").each(function () {
      $(this).attr("aria-selected", $(this).data("aba") === aba ? "true" : "false");
    });
    $(".ipei-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    atualizarTotal();
    if (window.EstadoUrl) EstadoUrl.salvar({ aba: aba });
  }
  $(".ipei-aba").on("click", function () { trocarAba($(this).data("aba")); });

  function atualizarTotal() {
    if (abaAtiva === "grafo") {
      $("#total-registros").text(listaPorAlvo("vinculos").length.toLocaleString("pt-BR") + " vínculos");
      return;
    }
    var total = (ultimasListas[abaAtiva] || []).length;
    $("#total-registros").text(total.toLocaleString("pt-BR") + " " + ROTULO_ABA[abaAtiva].toLowerCase());
  }

  // ------------------------------------------------------- estado na URL
  function salvarEstadoUrl() {
    if (!window.EstadoUrl) return;
    EstadoUrl.salvar({
      alvo: filtroAlvo,
      f_alvos: $("#f-alvo-busca").val(),
      f_vinc: $("#f-vinc-busca").val(),
      f_vinc_tipo: $("#f-vinc-tipo").val(),
      f_anos: $("#f-ano-busca").val(),
      f_fontes: $("#f-fonte-busca").val(),
      f_fonte_tipo: $("#f-fonte-tipo").val(),
      f_contas: $("#f-conta-busca").val(),
      f_conta_ano: $("#f-conta-ano").val(),
      f_bens: $("#f-bem-busca").val(),
      f_bem_tipo: $("#f-bem-tipo").val(),
      f_notas: $("#f-nota-busca").val(),
      f_nota_origem: $("#f-nota-origem").val(),
      f_cont: $("#f-cont-busca").val(),
      f_cont_tipo: $("#f-cont-tipo").val()
    });
  }
  function restaurarEstadoUrl() {
    if (!window.EstadoUrl) return;
    var e = EstadoUrl.ler(["aba", "alvo", "f_alvos", "f_vinc", "f_vinc_tipo", "f_anos", "f_fontes",
      "f_fonte_tipo", "f_contas", "f_conta_ano", "f_bens", "f_bem_tipo", "f_notas", "f_nota_origem",
      "f_cont", "f_cont_tipo"]);
    if (e.aba && TODAS_ABAS.indexOf(e.aba) !== -1) abaInicial = e.aba;
    if (e.f_alvos) $("#f-alvo-busca").val(e.f_alvos);
    if (e.f_vinc) $("#f-vinc-busca").val(e.f_vinc);
    definirSelect("#f-vinc-tipo", e.f_vinc_tipo);
    if (e.f_anos) $("#f-ano-busca").val(e.f_anos);
    if (e.f_fontes) $("#f-fonte-busca").val(e.f_fontes);
    definirSelect("#f-fonte-tipo", e.f_fonte_tipo);
    if (e.f_contas) $("#f-conta-busca").val(e.f_contas);
    definirSelect("#f-conta-ano", e.f_conta_ano);
    if (e.f_bens) $("#f-bem-busca").val(e.f_bens);
    definirSelect("#f-bem-tipo", e.f_bem_tipo);
    if (e.f_notas) $("#f-nota-busca").val(e.f_notas);
    definirSelect("#f-nota-origem", e.f_nota_origem);
    if (e.f_cont) $("#f-cont-busca").val(e.f_cont);
    definirSelect("#f-cont-tipo", e.f_cont_tipo);
    if (e.alvo) filtroAlvo = e.alvo;
  }

  // --------------------------------------------------------- binds de filtro
  $("#f-alvo-busca").on("input", function () { paginas.alvos = 1; aplicarAlvos(); salvarEstadoUrl(); });
  $("#f-vinc-busca, #f-vinc-tipo").on("input change", function () { paginas.vinculos = 1; aplicarVinculos(); salvarEstadoUrl(); });
  $("#f-ano-busca").on("input", function () { paginas.anos = 1; aplicarAnos(); salvarEstadoUrl(); });
  $("#f-fonte-busca, #f-fonte-tipo").on("input change", function () { paginas.fontes = 1; aplicarFontes(); salvarEstadoUrl(); });
  $("#f-conta-busca, #f-conta-ano").on("input change", function () { paginas.contas = 1; aplicarContas(); salvarEstadoUrl(); });
  $("#f-bem-busca, #f-bem-tipo").on("input change", function () { paginas.bens = 1; aplicarBens(); salvarEstadoUrl(); });
  $("#f-nota-busca, #f-nota-origem").on("input change", function () { paginas.notas = 1; aplicarNotas(); salvarEstadoUrl(); });
  $("#f-cont-busca, #f-cont-tipo").on("input change", function () { paginas.contatos = 1; aplicarContatos(); salvarEstadoUrl(); });

  // filtro global por alvo: refaz todas as abas, indicadores, indícios e a rede
  $("#f-alvo-global").on("change", function () {
    filtroAlvo = $(this).val() || "";
    Object.keys(APLICAR).forEach(function (aba) { paginas[aba] = 1; APLICAR[aba](); });
    atualizarRotulosAbas();
    renderKpis();
    renderIndicios();
    renderGrafo();
    salvarEstadoUrl();
  });

  // ordenação por clique no cabeçalho (o <button> dentro do <th> já funciona com Tab/Enter)
  $(".ipei-painel").each(function () {
    var aba = $(this).data("painel");
    if (!APLICAR[aba]) return;
    $(this).find("th .tp-ordenar").on("click", function () {
      var chave = $(this).attr("data-ordenar");
      var atual = ordenacoes[aba];
      var dir = atual && atual.chave === chave && atual.dir === "asc" ? "desc" : "asc";
      ordenacoes[aba] = { chave: chave, dir: dir };
      paginas[aba] = 1;
      atualizarCabecalho(aba);
      APLICAR[aba]();
    });
  });

  // paginação: botões + teclado ←/→ (quando o foco não está em campo de texto)
  ABAS.forEach(function (aba) {
    $("#pag-" + aba + "-ant").on("click", function () {
      paginas[aba] = Math.max(1, (paginas[aba] || 1) - 1);
      APLICAR[aba]();
    });
    $("#pag-" + aba + "-prox").on("click", function () {
      var maxPag = Math.max(1, Math.ceil((ultimasListas[aba] || []).length / LIMITE_PAGINA));
      paginas[aba] = Math.min(maxPag, (paginas[aba] || 1) + 1);
      APLICAR[aba]();
    });
  });
  $(document).on("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (document.activeElement && document.activeElement.isContentEditable)) return;
    if (!APLICAR[abaAtiva]) return;
    var $pag = $("#pag-" + abaAtiva);
    if ($pag.hasClass("hidden")) return;
    if (e.key === "ArrowLeft" && !$("#pag-" + abaAtiva + "-ant").prop("disabled")) {
      $("#pag-" + abaAtiva + "-ant").trigger("click");
    } else if (e.key === "ArrowRight" && !$("#pag-" + abaAtiva + "-prox").prop("disabled")) {
      $("#pag-" + abaAtiva + "-prox").trigger("click");
    }
  });

  // ------------------------------------------------------------------ carga
  function carregar(aba) {
    return $.getJSON("/api/investigacao/ipeis/" + idIpei + "/" + ROTA_ABA[aba]).done(function (lista) {
      dados[aba] = lista || [];
    });
  }

  function carregarContagens(tp) {
    var d = $.Deferred();
    if (!idInquerito || !window.EntidadesUI) { d.resolve(); return d; }
    EntidadesUI.carregarContagens(idInquerito, tp, function (c) { contagensEnt[tp] = c || {}; d.resolve(); });
    return d;
  }

  if (!idIpei) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  var pedidos = ABAS.map(carregar);
  pedidos.push(
    // só o nome do arquivo, sem o caminho em data/ (ver docs/organizacao_arquivos.md)
    $.getJSON("/api/investigacao/ipeis/" + idIpei).done(function (c) { cabecalho = window.AnalyserUtils.encurtarCaminhos(c); })
  );
  Object.keys(contagensEnt).forEach(function (tp) { pedidos.push(carregarContagens(tp)); });

  $.when.apply($, pedidos).always(function () {
    renderCabecalho();
    // os selects de ano e de tipo de bem saem do que a IPEI realmente tem: fixar a lista faria a
    // tela oferecer filtro para "aeronave" num relatório que só fala de veículo
    popularSelect($("#f-conta-ano"), Array.from(new Set(dados.contas.map(function (c) { return c.nu_ano; }).filter(Boolean))).map(String));
    popularSelect($("#f-bem-tipo"), Array.from(new Set(dados.bens.map(function (b) { return b.tp_bem; }).filter(Boolean))),
      function (v) { return ROTULO_BEM[v] || v; });
    popularSelect($("#f-alvo-global"), Array.from(new Set(dados.alvos.map(function (a) { return a.no_alvo; }).filter(Boolean))));

    restaurarEstadoUrl();
    if (filtroAlvo) $("#f-alvo-global").val(filtroAlvo);

    ABAS.forEach(function (aba) { APLICAR[aba](); atualizarCabecalho(aba); });
    atualizarRotulosAbas();
    trocarAba(TODAS_ABAS.indexOf(abaInicial) !== -1 ? abaInicial : "alvos");
    renderKpis();
    renderIndicios();
    renderGrafo();
  });

  // ------------------------------------------------------------- exportar
  // Exporta a lista FILTRADA inteira (não só a página desenhada) — ver js/utils/exportar_excel.js
  var EXPORTAR = {
    alvos: {
      colunas: ["Tipo", "Nome", "CPF/CNPJ", "Nascimento / abertura", "Endereço", "Ocupação / CNAE", "Situação"],
      linha: function (a) {
        var endereco = [a.tx_endereco, [a.no_municipio, a.sg_uf].filter(Boolean).join("/"), a.nu_cep]
          .filter(Boolean).join(" — ");
        return [a.tp_pessoa || "PF", a.no_alvo || "", a.nu_cpf_cnpj || "", formatarData(a.dt_nascimento),
          endereco, a.tx_ocupacao || "", a.tx_situacao_cadastral || ""];
      }
    },
    vinculos: {
      colunas: ["Vínculo", "Alvo", "Relacionado", "CPF/CNPJ", "Qualificação", "Início", "Fim", "%"],
      linha: function (v) {
        return [ROTULO_VINCULO[v.tp_relacao] || v.tp_relacao || "", v.no_alvo || "", v.no_relacionado || "",
          v.nu_cpf_cnpj || "", v.tx_qualificacao || "", formatarData(v.dt_inicio), formatarData(v.dt_fim),
          temValor(v.pc_participacao) ? numeroBR(v.pc_participacao, 2) + "%" : ""];
      }
    },
    anos: {
      colunas: ["Alvo", "Ano", "Rendimento / receita", "Movimentação (créditos)", "Mov. / rendimento",
        "DECRED", "Compras em NFe", "Bens em 31/12", "Variação patrimonial"],
      linha: function (a) {
        return [a.no_alvo || "", a.nu_ano || "", moeda(temValor(a.vl_rend_total) ? a.vl_rend_total : a.vl_receita_bruta),
          moeda(a.vl_mov_creditos), temValor(a.vl_razao_mov_rend) ? numeroBR(a.vl_razao_mov_rend, 2) + "x" : "",
          moeda(a.vl_decred), moeda(a.vl_nfe_compras), moeda(a.vl_bens_ano), moeda(a.vl_variacao_patrimonial)];
      }
    },
    fontes: {
      colunas: ["Tipo", "Ano", "Fonte / beneficiário", "CNPJ/CPF", "Natureza", "Valor", "Imposto"],
      linha: function (f) {
        return [ROTULO_FONTE[f.tp_registro] || f.tp_registro || "", f.nu_ano || "", f.no_fonte || "",
          f.nu_cpf_cnpj || "", f.tx_natureza || "", moeda(f.vl_rendimento), moeda(f.vl_imposto)];
      }
    },
    contas: {
      colunas: ["Alvo", "Banco declarante", "Conta", "Tipo", "Período", "Créditos", "Débitos", "Saldo"],
      linha: function (c) {
        var tipo = c.tp_conta || "";
        if (c.no_titular) tipo += (tipo ? " · " : "") + c.no_titular + (c.tx_relacao ? " (" + c.tx_relacao + ")" : "");
        return [c.no_alvo || "", c.no_banco || "", c.nu_conta || "", tipo, periodo(c),
          moeda(c.vl_creditos), moeda(c.vl_debitos), moeda(c.vl_saldo)];
      }
    },
    bens: {
      colunas: ["Tipo", "Alvo", "Ano", "Identificador", "Descrição", "31/12 anterior", "31/12 atual", "Situação"],
      linha: function (b) {
        return [ROTULO_BEM[b.tp_bem] || b.tp_bem || "", b.no_alvo || "", b.nu_ano || "", b.nu_identificador || "",
          b.tx_descricao || "", moeda(b.vl_ano_anterior), moeda(b.vl_ano), b.tx_situacao || ""];
      }
    },
    notas: {
      colunas: ["Origem", "Emissão", "Emitente", "Destinatário", "Produto / serviço", "Qtde", "Valor total"],
      linha: function (n) {
        return [n.tp_origem === "anexo" ? "Anexo" : "PDF", formatarData(n.dt_emissao), n.no_emitente || "",
          n.no_destinatario || "", n.tx_descricao || "", temValor(n.qt_item) ? numeroBR(n.qt_item, 2) : "",
          moeda(n.vl_total)];
      }
    },
    contatos: {
      colunas: ["Tipo", "Alvo", "Ano", "Valor"],
      linha: function (c) {
        return [ROTULO_CONTATO[c.tp_contato] || c.tp_contato || "", c.no_alvo || "", c.nu_ano || "", c.tx_valor || ""];
      }
    }
  };
  function dadosExportacao() {
    var aba = abaAtiva === "grafo" ? "vinculos" : abaAtiva;
    var cfg = EXPORTAR[aba];
    if (!cfg) return { colunas: [], linhas: [] };
    return { colunas: cfg.colunas, linhas: (ultimasListas[aba] || []).map(cfg.linha) };
  }
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "ipei",
      dados: function () { return dadosExportacao(); },
    });
  }

  function tituloIpei() {
    return "IPEI " + ((cabecalho && (cabecalho.nu_ipei || cabecalho.no_arquivo)) || idIpei);
  }

  // Botão "Prompt de IA da IPEI" — conversa restrita só a esta quebra de sigilo fiscal (ver
  // js/ia.js e funcoes/ia_investigacao.ferramentas_para_inquerito no backend)
  $("#prompt-ia-ipei-btn").on("click", function () {
    window.location.href = "ia.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=ipei&escopo_id=" + encodeURIComponent(idIpei) +
      "&escopo_titulo=" + encodeURIComponent(tituloIpei());
  });

  // Botão "Hipótese da mídia" — web/hipoteses.html filtrada nesta IPEI (mesmo par de URL do
  // chat de IA por escopo); a visão do inquérito inteiro está no menu "Extras" da Investigação.
  $("#achados-analista-ipei-btn").on("click", function () {
    window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=ipei&escopo_id=" + encodeURIComponent(idIpei) +
      "&escopo_titulo=" + encodeURIComponent(tituloIpei());
  });
})();
