/* Linha do tempo financeira do inquérito — SIMBA + RIF por mês, num gráfico só.
 *
 * Junta o que hoje vive separado: a série mensal do extrato (simba.html, por extrato) e as
 * comunicações do RIF (rif.html). O backend devolve os meses já fundidos
 * (GET /api/investigacao/inqueritos/<id>/financeiro-timeline); aqui é só desenhar: quatro
 * barras agrupadas por mês (SIMBA C/D + RIF C/D), linha de saldo líquido, coluna de
 * divergência entre as fontes, KPIs, tabela com totais e exportação CSV.
 *
 * Novidades: filtros por período/fonte/tipo/valor (persistidos na URL via EstadoUrl),
 * gaveta lateral paginada com os lançamentos do mês, clique/teclado nas barras abre a
 * gaveta, e flag para comunicações do RIF sem data de operação (caem na de recebimento).
 */
(function () {
  "use strict";

  var idInquerito = AnalyserUtils.paramUrl("inquerito") || "";
  var SVG_NS = "http://www.w3.org/2000/svg";
  var LIMITE_DETALHES = 200;
  var dados = null;
  var mesesOriginais = [];
  var drawerEstado = null;
  var ultimoFoco = null;
  // pessoa selecionada (chave vinda de dados.pessoas): sem ela a tela soma todo mundo,
  // que é exatamente o que confundia — "parece que está unificando tudo, mesmo sendo de
  // pessoas diferentes". O filtro é no backend (o mês já chega agregado por pessoa).
  var pessoaAtual = AnalyserUtils.paramUrl("fpessoa") || "";
  var comparando = false;
  var PALETA = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be185d", "#4d7c0f"];

  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function formatarValor(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function formatarReais(v) { return "R$ " + formatarValor(num(v)); }
  function formatarNumero(n) { return num(n).toLocaleString("pt-BR"); }
  function formatarCompacto(v) {
    var n = num(v), abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " bi";
    if (abs >= 1e6) return (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mi";
    if (abs >= 1e3) return (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + " mil";
    return n.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
  }
  function mesBR(mes) {
    var m = String(mes || "").match(/^(\d{4})-(\d\d)$/);
    return m ? m[2] + "/" + m[1] : String(mes || "—");
  }

  function elG(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function formatarDataBR(data) {
    if (!data) return "—";
    var d = new Date(data + "T00:00:00");
    return d.toLocaleDateString("pt-BR");
  }

  // ---------- divergência entre as fontes ----------
  // Saldo SIMBA menos saldo RIF no mês: quanto maior (em proporção ao movimento), mais as
  // duas fontes contam histórias diferentes no mesmo mês.
  function divergencia(m) {
    var netS = num(m.simba_credito) - num(m.simba_debito);
    var netR = num(m.rif_credito) - num(m.rif_debito);
    var mov = Math.abs(netS) + Math.abs(netR);
    var dif = netS - netR;
    var ratio = mov > 0 ? Math.abs(dif) / mov : 0;
    return { netS: netS, netR: netR, dif: dif, ratio: ratio, forte: ratio >= 0.4, media: ratio >= 0.2 && ratio < 0.4 };
  }
  function clsDivergencia(m) {
    var d = divergencia(m);
    return d.forte ? "fin-diverge-forte" : (d.media ? "fin-diverge" : "");
  }

  // ---------- filtros ----------

  function obterFiltros() {
    return {
      mesIni: $("#filtro-mes-ini").val() || null,
      mesFim: $("#filtro-mes-fim").val() || null,
      fonte: $("#filtro-fonte").val() || null,
      tipo: $("#filtro-tipo").val() || null,
      valorMin: num($("#filtro-valor-min").val())
    };
  }

  function filtrarMeses(f) {
    return mesesOriginais.filter(function (m) {
      if (f.mesIni && m.mes < f.mesIni) return false;
      if (f.mesFim && m.mes > f.mesFim) return false;
      if (f.fonte === "simba" && num(m.simba_credito) === 0 && num(m.simba_debito) === 0) return false;
      if (f.fonte === "rif" && num(m.rif_credito) === 0 && num(m.rif_debito) === 0) return false;
      if (f.tipo === "credito" && num(m.total_credito) === 0) return false;
      if (f.tipo === "debito" && num(m.total_debito) === 0) return false;
      if (f.valorMin > 0) {
        var maxCred = Math.max(num(m.simba_credito), num(m.rif_credito), num(m.total_credito));
        var maxDeb = Math.max(num(m.simba_debito), num(m.rif_debito), num(m.total_debito));
        if (maxCred < f.valorMin && maxDeb < f.valorMin) return false;
      }
      return true;
    });
  }

  function nomePessoaAtual() {
    var pessoas = (dados && dados.pessoas) || [];
    for (var i = 0; i < pessoas.length; i++) {
      if (pessoas[i].chave === pessoaAtual) return pessoas[i].nome || pessoas[i].chave;
    }
    return pessoaAtual;
  }

  function salvarFiltrosUrl(f) {
    if (!window.EstadoUrl) return;
    window.EstadoUrl.salvar({
      fini: f.mesIni || null,
      ffim: f.mesFim || null,
      ffonte: f.fonte || null,
      ftipo: f.tipo || null,
      fvalor: f.valorMin > 0 ? String(f.valorMin) : null,
      fpessoa: pessoaAtual || null
    });
  }

  function aplicarFiltros() {
    if (!dados) return;
    var f = obterFiltros();
    var filtrados = filtrarMeses(f);
    dados.meses = filtrados;
    salvarFiltrosUrl(f);
    renderGraficoAtual(f, filtrados);
    renderTabela(filtrados);
    renderResumo(filtrados, f);
    atualizarAtalhosFiltro(f);
  }

  function renderGraficoAtual(f, filtrados) {
    var podeComparar = ((dados && dados.por_pessoa) || []).length > 1;
    $("#btn-comparar").toggleClass("hidden", !podeComparar);
    if (!podeComparar) comparando = false;
    $("#btn-comparar").toggleClass("ativo", comparando)
      .text(comparando ? "Voltar às barras" : "Comparar pessoas");
    $("#fin-legenda-barras").toggleClass("hidden", comparando);
    $("#fin-legenda-comparacao").toggleClass("hidden", !comparando);
    if (comparando && podeComparar) renderGraficoComparacao(f);
    else renderGrafico(filtrados);
  }

  function atualizarAtalhosFiltro(f) {
    var $atalhos = $("#fin-filtro-atalhos").empty();
    var temFiltro = false;
    if (f.mesIni || f.mesFim) {
      temFiltro = true;
      var txtPer = "Período: " + (f.mesIni ? mesBR(f.mesIni) : "início") + " — " + (f.mesFim ? mesBR(f.mesFim) : "hoje");
      $("<span>").addClass("filtro-chip").text(txtPer).appendTo($atalhos);
    }
    if (f.fonte) {
      temFiltro = true;
      $("<span>").addClass("filtro-chip").text("Fonte: " + (f.fonte === "simba" ? "SIMBA" : "RIF")).appendTo($atalhos);
    }
    if (f.tipo) {
      temFiltro = true;
      $("<span>").addClass("filtro-chip").text("Tipo: " + (f.tipo === "credito" ? "Créditos" : "Débitos")).appendTo($atalhos);
    }
    if (f.valorMin > 0) {
      temFiltro = true;
      $("<span>").addClass("filtro-chip").text("Valor ≥ " + formatarReais(f.valorMin)).appendTo($atalhos);
    }
    if (pessoaAtual) {
      temFiltro = true;
      $("<span>").addClass("filtro-chip").text("Pessoa: " + nomePessoaAtual()).appendTo($atalhos);
    }
    $("#btn-limpar-filtros").toggleClass("hidden", !temFiltro);
  }

  function limparFiltros() {
    $("#filtro-mes-ini, #filtro-mes-fim, #filtro-fonte, #filtro-tipo, #filtro-valor-min").val("");
    if (pessoaAtual) {
      pessoaAtual = "";
      $("#filtro-pessoa").val("");
      carregar();
      return;
    }
    aplicarFiltros();
  }

  // ---------- resumo / KPIs ----------

  function renderResumo(meses, f) {
    var r = (dados && dados.resumo) || {};
    var somas = { sc: 0, sd: 0, rc: 0, rd: 0, tc: 0, td: 0 };
    var pico = null;
    (meses || []).forEach(function (m) {
      somas.sc += num(m.simba_credito);
      somas.sd += num(m.simba_debito);
      somas.rc += num(m.rif_credito);
      somas.rd += num(m.rif_debito);
      somas.tc += num(m.total_credito);
      somas.td += num(m.total_debito);
      var mov = num(m.total_credito) + num(m.total_debito);
      if (!pico || mov > pico.mov) pico = { mes: m.mes, mov: mov };
    });
    var saldo = somas.tc - somas.td;

    var kpis = [
      { rot: "Meses", val: formatarNumero((meses || []).length) },
      { rot: "Extratos SIMBA", val: formatarNumero(r.qt_extratos) },
      { rot: "RIFs", val: formatarNumero(r.qt_rifs) },
      { rot: "SIMBA entradas", val: formatarReais(somas.sc), cls: "credito" },
      { rot: "SIMBA saídas", val: formatarReais(somas.sd), cls: "debito" },
      { rot: "RIF créditos", val: formatarReais(somas.rc), cls: "credito" },
      { rot: "RIF débitos", val: formatarReais(somas.rd), cls: "debito" },
      { rot: "Saldo líquido", val: formatarReais(saldo), cls: saldo >= 0 ? "credito" : "debito" }
    ];
    var $r = $("#fin-resumo").empty();
    kpis.forEach(function (k) {
      var $c = $("<div>").addClass("fin-kpi" + (k.cls ? " " + k.cls : "")).appendTo($r);
      $("<div>").addClass("rot").text(k.rot).appendTo($c);
      $("<div>").addClass("val").text(k.val).appendTo($c);
    });
    if (pico) {
      var $p = $("<div>").addClass("fin-kpi fin-kpi-clicavel")
        .attr("role", "button").attr("tabindex", "0")
        .attr("title", "Rolar até o mês de maior movimentação (" + mesBR(pico.mes) + ")")
        .on("click keydown", function (e) {
          if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
          if (e.type === "keydown") e.preventDefault();
          rolarParaMes(pico.mes);
        })
        .appendTo($r);
      $("<div>").addClass("rot").text("Mês de pico").appendTo($p);
      $("<div>").addClass("val").text(mesBR(pico.mes)).appendTo($p);
    }
    var filtrado = f && (f.mesIni || f.mesFim || f.fonte || f.tipo || f.valorMin > 0 || pessoaAtual);
    if (filtrado) {
      $("<div>").addClass("fin-resumo-nota").text("KPIs refletem os filtros aplicados — " +
        formatarNumero((meses || []).length) + " de " + formatarNumero(mesesOriginais.length) + " meses.").appendTo($r);
    }
  }

  function rolarParaMes(mes) {
    var $linha = $("#mes-row-" + String(mes).replace(/[^0-9-]/g, ""));
    if (!$linha.length) return;
    $linha[0].scrollIntoView({ behavior: "smooth", block: "center" });
    $linha.addClass("linha-destaque");
    setTimeout(function () { $linha.removeClass("linha-destaque"); }, 1900);
  }

  function renderFontes() {
    var fontes = [];
    ((dados && dados.extratos) || []).forEach(function (e) {
      fontes.push("SIMBA " + (e.no_arquivo || "").split("/").pop());
    });
    ((dados && dados.rifs) || []).forEach(function (e) {
      fontes.push("RIF " + (e.nu_rif || e.id_rif));
    });
    $("#fin-fontes").text(fontes.length ? fontes.join(" · ") : "Nenhuma fonte financeira concluída.");
  }

  // ---------- gráfico ----------

  function renderGrafico(meses) {
    var svg = document.getElementById("fin-grafico");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!meses.length) {
      $("#fin-grafico-titulo").text(mesesOriginais.length
        ? "Movimentação por mês — nada passa nos filtros"
        : "Movimentação por mês — sem dados datados");
      return;
    }
    var margemX = 66;
    var largura = Math.max(640, meses.length * 56 + margemX);
    var altura = 240, areaTop = 18, areaBottom = altura - 18;
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("height", altura);
    svg.setAttribute("width", "100%");
    svg.style.minWidth = largura + "px";

    var maximo = meses.reduce(function (m, p) {
      return Math.max(m, num(p.simba_credito), num(p.simba_debito), num(p.rif_credito), num(p.rif_debito));
    }, 0) || 1;

    // saldo líquido mensal (eixo próprio): parte positiva acima da linha de zero, negativa abaixo
    var nets = meses.map(function (p) {
      return num(p.simba_credito) - num(p.simba_debito) + num(p.rif_credito) - num(p.rif_debito);
    });
    var maxPosNet = Math.max(0, Math.max.apply(null, nets));
    var maxNegNet = Math.max(0, -(Math.min.apply(null, nets)));
    var zeroY = maxNegNet > 0 ? areaBottom - 46 : areaBottom;

    var passo = (largura - margemX) / meses.length;
    var larguraBarra = Math.max(3, Math.min(9, passo / 4 - 2));

    function xMes(i) { return margemX + i * passo + passo / 2; }
    function yBarra(v) { return zeroY - (v / maximo) * (zeroY - areaTop); }
    function yNeto(v) {
      if (v >= 0) return zeroY - (v / (maxPosNet || 1)) * (zeroY - areaTop);
      return zeroY + (-v / (maxNegNet || 1)) * (areaBottom - zeroY);
    }

    [0, 0.25, 0.5, 0.75, 1].forEach(function (frac) {
      var y = yBarra(maximo * frac);
      svg.appendChild(elG("line", { x1: margemX, y1: y, x2: largura, y2: y, "class": "eixo", "stroke-dasharray": "4,4", opacity: 0.2 }));
      var rot = elG("text", { x: margemX - 6, y: y + 3, "text-anchor": "end", "class": "rotulo" });
      rot.textContent = "R$ " + formatarCompacto(maximo * frac);
      svg.appendChild(rot);
    });
    svg.appendChild(elG("line", { x1: margemX, y1: zeroY, x2: largura, y2: zeroY, "class": "eixo" }));

    var series = [
      ["simba_credito", "#16a34a", "SIMBA entradas"],
      ["simba_debito", "#dc2626", "SIMBA saídas"],
      ["rif_credito", "#4ade80", "RIF créditos"],
      ["rif_debito", "#f87171", "RIF débitos"]
    ];
    meses.forEach(function (p, i) {
      var x = xMes(i);
      series.forEach(function (s, j) {
        var v = num(p[s[0]]);
        if (v <= 0) return;
        var h = Math.max(1, (v / maximo) * (zeroY - areaTop));
        var bx = x + (j - 1.5) * (larguraBarra + 1);
        var rect = elG("rect", {
          x: bx, y: zeroY - h, width: larguraBarra, height: h, rx: 2, ry: 2,
          fill: s[1], "data-mes": p.mes, "data-tipo": s[2], "data-valor": formatarReais(v),
          "data-simba-c": formatarReais(p.simba_credito), "data-simba-d": formatarReais(p.simba_debito),
          "data-rif-c": formatarReais(p.rif_credito), "data-rif-d": formatarReais(p.rif_debito),
          tabindex: 0, role: "button",
          "aria-label": mesBR(p.mes) + " — " + s[2] + " " + formatarReais(v)
        });
        rect.appendChild(elG("title", {})).textContent =
          mesBR(p.mes) + " · " + s[2] + " " + formatarReais(v);
        svg.appendChild(rect);
      });
    });

    // linha do saldo líquido
    var pontos = nets.map(function (v, i) { return xMes(i) + "," + yNeto(v).toFixed(1); });
    svg.appendChild(elG("polyline", { points: pontos.join(" "), "class": "neto-linha" }));
    nets.forEach(function (v, i) {
      var c = elG("circle", {
        cx: xMes(i), cy: yNeto(v), r: 3, "class": "neto-ponto",
        "data-mes": meses[i].mes, "data-tipo": "Saldo líquido", "data-valor": formatarReais(v),
        tabindex: 0, role: "button",
        "aria-label": mesBR(meses[i].mes) + " — saldo líquido " + formatarReais(v)
      });
      c.appendChild(elG("title", {})).textContent =
        mesBR(meses[i].mes) + " · saldo líquido " + formatarReais(v);
      svg.appendChild(c);
    });

    var intervalo = meses.length > 24 ? 12 : 6;
    meses.forEach(function (p, i) {
      var mostrar = /-01$/.test(p.mes) || i === 0 || i % intervalo === 0;
      if (!mostrar) return;
      var t = elG("text", { x: xMes(i), y: altura - 10, "text-anchor": "middle", "class": "rotulo" });
      var semData = num(p.rif_sem_data_operacao);
      t.textContent = mesBR(p.mes) + (semData > 0 ? " *" : "");
      if (semData > 0) {
        t.appendChild(elG("title", {})).textContent =
          "RIF sem data de operação em " + formatarNumero(semData) + " comunicação(ões) — usa a data de recebimento";
      }
      svg.appendChild(t);
    });
    $("#fin-grafico-titulo").text("Movimentação por mês — pico de " + formatarReais(maximo) + " em um único mês");
  }

  // ---------- gráfico de comparação: uma linha por pessoa ----------
  // Volume total (entradas + saídas) por mês, cada pessoa em sua cor — é a resposta para
  // "quem movimentou quando", sem somar investigados diferentes no mesmo monte. A legenda é
  // clicável: clicar na pessoa seta o filtro e volta às barras dela.

  function renderGraficoComparacao(f) {
    var svg = document.getElementById("fin-grafico");
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var pessoas = ((dados && dados.por_pessoa) || []).map(function (p) {
      var meses = (p.meses || []).filter(function (m) {
        if (f.mesIni && m.mes < f.mesIni) return false;
        if (f.mesFim && m.mes > f.mesFim) return false;
        if (f.fonte === "simba" && num(m.simba_credito) === 0 && num(m.simba_debito) === 0) return false;
        if (f.fonte === "rif" && num(m.rif_credito) === 0 && num(m.rif_debito) === 0) return false;
        return true;
      });
      return { chave: p.chave, nome: p.nome, meses: meses };
    }).filter(function (p) { return p.meses.length; });

    var $leg = $("#fin-legenda-comparacao").empty();
    if (!pessoas.length) {
      $("#fin-grafico-titulo").text("Comparação por pessoa — nada passa nos filtros");
      return;
    }

    var mesesUniv = {};
    pessoas.forEach(function (p) { p.meses.forEach(function (m) { mesesUniv[m.mes] = true; }); });
    var meses = Object.keys(mesesUniv).sort();

    function volume(m) {
      return num(m.simba_credito) + num(m.simba_debito) + num(m.rif_credito) + num(m.rif_debito);
    }

    var margemX = 66;
    var largura = Math.max(640, meses.length * 56 + margemX);
    var altura = 240, areaTop = 18, areaBottom = altura - 18;
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("height", altura);
    svg.setAttribute("width", "100%");
    svg.style.minWidth = largura + "px";

    var maximo = 1;
    pessoas.forEach(function (p) {
      p.meses.forEach(function (m) { maximo = Math.max(maximo, volume(m)); });
    });

    var passo = (largura - margemX) / meses.length;
    var indice = {};
    meses.forEach(function (m, i) { indice[m] = i; });
    function xMes(i) { return margemX + i * passo + passo / 2; }
    function y(v) { return areaBottom - (v / maximo) * (areaBottom - areaTop); }

    [0, 0.25, 0.5, 0.75, 1].forEach(function (frac) {
      var yy = y(maximo * frac);
      svg.appendChild(elG("line", { x1: margemX, y1: yy, x2: largura, y2: yy, "class": "eixo", "stroke-dasharray": "4,4", opacity: 0.2 }));
      var rot = elG("text", { x: margemX - 6, y: yy + 3, "text-anchor": "end", "class": "rotulo" });
      rot.textContent = "R$ " + formatarCompacto(maximo * frac);
      svg.appendChild(rot);
    });
    svg.appendChild(elG("line", { x1: margemX, y1: areaBottom, x2: largura, y2: areaBottom, "class": "eixo" }));

    pessoas.forEach(function (p, pi) {
      var cor = PALETA[pi % PALETA.length];
      var pontos = p.meses.map(function (m) { return xMes(indice[m.mes]) + "," + y(volume(m)).toFixed(1); });
      svg.appendChild(elG("polyline", { points: pontos.join(" "), "class": "cmp-linha", stroke: cor }));
      p.meses.forEach(function (m) {
        var v = volume(m);
        var c = elG("circle", {
          cx: xMes(indice[m.mes]), cy: y(v), r: 3.5, "class": "neto-ponto", fill: cor,
          "data-mes": m.mes, "data-tipo": p.nome + " · volume", "data-valor": formatarReais(v),
          "data-simba-c": formatarReais(m.simba_credito), "data-simba-d": formatarReais(m.simba_debito),
          "data-rif-c": formatarReais(m.rif_credito), "data-rif-d": formatarReais(m.rif_debito),
          tabindex: 0, role: "button",
          "aria-label": mesBR(m.mes) + " — " + p.nome + " volume " + formatarReais(v)
        });
        c.appendChild(elG("title", {})).textContent =
          mesBR(m.mes) + " · " + p.nome + " volume " + formatarReais(v);
        svg.appendChild(c);
      });

      var $item = $("<span>").addClass("legenda-item")
        .attr("title", "Filtrar por " + p.nome)
        .on("click", function () {
          $("#filtro-pessoa").val(p.chave);
          pessoaAtual = p.chave;
          comparando = false;
          carregar();
        });
      $("<span>").addClass("legenda-cor").css("background", cor).appendTo($item);
      $item.append(document.createTextNode(" " + p.nome));
      $leg.append($item);
    });

    var intervalo = meses.length > 24 ? 12 : 6;
    meses.forEach(function (mes, i) {
      var mostrar = /-01$/.test(mes) || i === 0 || i % intervalo === 0;
      if (!mostrar) return;
      var t = elG("text", { x: xMes(i), y: altura - 10, "text-anchor": "middle", "class": "rotulo" });
      t.textContent = mesBR(mes);
      svg.appendChild(t);
    });
    $("#fin-grafico-titulo").text("Comparação por pessoa — volume total (entradas + saídas) por mês, eixo único");
  }

  // ---------- tabela ----------

  function renderTabela(meses) {
    var $corpo = $("#corpo-meses").empty();
    meses.forEach(function (p) {
      var div = divergencia(p);
      var difTxt = (div.dif > 0 ? "+" : "") + formatarValor(div.dif);
      var semData = num(p.rif_sem_data_operacao);
      var $tr = $("<tr>").attr("id", "mes-row-" + String(p.mes).replace(/[^0-9-]/g, ""));
      $("<td>").addClass("col-nome").text(mesBR(p.mes)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.simba_credito)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.simba_debito)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.rif_credito)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.rif_debito)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.total_credito)).appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(p.total_debito)).appendTo($tr);
      var cls = clsDivergencia(p);
      var $tdDif = $("<td>").addClass("col-nome").css("text-align", "right").text(difTxt)
        .attr("title", "Saldo SIMBA (" + formatarReais(div.netS) + ") − saldo RIF (" + formatarReais(div.netR) + ") no mês");
      if (cls) $tdDif.addClass(cls);
      $tdDif.appendTo($tr);
      var $tdQt = $("<td>").addClass("col-nome").css("text-align", "right")
        .text(formatarNumero(p.simba_qt) + " / " + formatarNumero(p.rif_qt));
      if (semData > 0) {
        $tdQt.attr("title", "RIF sem data de operação em " + formatarNumero(semData) + " comunicação(ões) — usa a data de recebimento");
        $("<span>").addClass("fin-flag-rif").text(" *").appendTo($tdQt);
      }
      $tdQt.appendTo($tr);
      var $btnDetalhes = $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "4px 8px", fontSize: "12px" })
        .attr("title", "Ver detalhes do mês")
        .attr("aria-label", "Ver detalhes de " + mesBR(p.mes))
        .html('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>')
        .on("click", function () { abrirDrawer(p.mes); });
      $("<td>").css("text-align", "center").append($btnDetalhes).appendTo($tr);
      $corpo.append($tr);
    });
    $("#fin-vazio").toggleClass("hidden", meses.length > 0);

    // rodapé com totais
    var $rod = $("#rodape-meses").empty();
    if (meses.length) {
      var t = { sc: 0, sd: 0, rc: 0, rd: 0, tc: 0, td: 0, sq: 0, rq: 0, div: 0 };
      meses.forEach(function (p) {
        t.sc += num(p.simba_credito); t.sd += num(p.simba_debito);
        t.rc += num(p.rif_credito); t.rd += num(p.rif_debito);
        t.tc += num(p.total_credito); t.td += num(p.total_debito);
        t.sq += num(p.simba_qt); t.rq += num(p.rif_qt);
        if (divergencia(p).media || divergencia(p).forte) t.div += 1;
      });
      var $tr = $("<tr>").addClass("rodape-totais");
      $("<td>").addClass("col-nome").text("Totais").appendTo($tr);
      [t.sc, t.sd, t.rc, t.rd, t.tc, t.td].forEach(function (v) {
        $("<td>").addClass("col-nome").css("text-align", "right").text(formatarValor(v)).appendTo($tr);
      });
      $("<td>").addClass("col-nome").css("text-align", "right")
        .text((t.tc - t.td > 0 ? "+" : "") + formatarValor(t.tc - t.td))
        .attr("title", "Saldo total do período: total de entradas menos total de saídas")
        .appendTo($tr);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarNumero(t.sq) + " / " + formatarNumero(t.rq)).appendTo($tr);
      var $tdDiv = $("<td>").addClass("col-nome").css("text-align", "right");
      if (t.div > 0) {
        $tdDiv.attr("title", "Meses em que os saldos SIMBA e RIF divergem mais de 20% do movimento")
          .text(formatarNumero(t.div) + " mês(es)");
      }
      $tdDiv.appendTo($tr);
      $("<td>").appendTo($tr);
      $rod.append($tr);
    }
  }

  // ---------- tooltip / interação do gráfico ----------

  function htmlTooltip($r) {
    if ($r.attr("data-tipo") === "Saldo líquido") {
      return "<strong>" + mesBR($r.attr("data-mes")) + "</strong> — saldo líquido <strong>" + $r.attr("data-valor") + "</strong>";
    }
    return "<strong>" + mesBR($r.attr("data-mes")) + "</strong> — " + $r.attr("data-tipo") +
      " <strong>" + $r.attr("data-valor") + "</strong><br>" +
      "SIMBA " + $r.attr("data-simba-c") + " / " + $r.attr("data-simba-d") +
      " · RIF " + $r.attr("data-rif-c") + " / " + $r.attr("data-rif-d");
  }

  $(document).on("mouseenter", "#fin-grafico rect, #fin-grafico .neto-ponto", function () {
    $("#fin-tooltip").html(htmlTooltip($(this))).show();
  }).on("mousemove", "#fin-grafico rect, #fin-grafico .neto-ponto", function (e) {
    var wrap = $("#fin-tooltip").parent();
    var off = wrap.offset();
    $("#fin-tooltip").css({
      left: Math.min(e.pageX - off.left + 14, wrap.width() - 260) + "px",
      top: (e.pageY - off.top - 48) + "px"
    });
  }).on("mouseleave", "#fin-grafico rect, #fin-grafico .neto-ponto", function () {
    $("#fin-tooltip").hide();
  });

  $(document).on("click", "#fin-grafico rect, #fin-grafico .neto-ponto", function () {
    abrirDrawer($(this).attr("data-mes"));
  });
  $(document).on("keydown", "#fin-grafico rect, #fin-grafico .neto-ponto", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      abrirDrawer($(this).attr("data-mes"));
    }
  });

  // ---------- exportação CSV ----------

  $("#btn-excel").on("click", function () {
    var meses = (dados && dados.meses) || [];
    if (!meses.length) return;
    var f = obterFiltros();
    var linhas = [];
    linhas.push("# Linha do tempo financeira — PolícIA");
    linhas.push("# Inquérito: " + (idInquerito || "—"));
    if (pessoaAtual) linhas.push("# Pessoa: " + nomePessoaAtual());
    linhas.push("# Filtros: " + (f.mesIni || f.mesFim ? "período " + (f.mesIni ? mesBR(f.mesIni) : "início") + " a " + (f.mesFim ? mesBR(f.mesFim) : "hoje") : "todos") +
      (f.fonte ? " · fonte " + (f.fonte === "simba" ? "SIMBA" : "RIF") : "") +
      (f.tipo ? " · tipo " + (f.tipo === "credito" ? "créditos" : "débitos") : "") +
      (f.valorMin > 0 ? " · valor ≥ " + formatarReais(f.valorMin) : "") +
      (pessoaAtual ? " · pessoa " + nomePessoaAtual() : " · todas as pessoas (soma)"));
    var t = { sc: 0, sd: 0, rc: 0, rd: 0, tc: 0, td: 0 };
    meses.forEach(function (p) {
      t.sc += num(p.simba_credito); t.sd += num(p.simba_debito);
      t.rc += num(p.rif_credito); t.rd += num(p.rif_debito);
      t.tc += num(p.total_credito); t.td += num(p.total_debito);
    });
    linhas.push("# SIMBA entradas " + formatarReais(t.sc) + " · SIMBA saídas " + formatarReais(t.sd) +
      " · RIF créditos " + formatarReais(t.rc) + " · RIF débitos " + formatarReais(t.rd) +
      " · saldo líquido " + formatarReais(t.tc - t.td));
    linhas.push("mes;simba_credito;simba_debito;rif_credito;rif_debito;total_credito;total_debito;divergencia;simba_qt;rif_qt");
    meses.forEach(function (p) {
      linhas.push([p.mes, p.simba_credito, p.simba_debito, p.rif_credito, p.rif_debito,
        p.total_credito, p.total_debito, divergencia(p).dif.toFixed(2), p.simba_qt, p.rif_qt].join(";"));
    });
    var blob = new Blob(["\ufeff" + linhas.join("\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "timeline_financeira_" + (idInquerito || "inquerito") + ".csv";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  });

  // ---------- gaveta de detalhes do mês ----------

  function urlDetalhes(simbaOff, rifOff) {
    var u = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/financeiro-timeline/detalhes?mes=" + encodeURIComponent(drawerEstado.mes) +
      "&limite=" + LIMITE_DETALHES +
      "&simba_offset=" + simbaOff + "&rif_offset=" + rifOff;
    if (pessoaAtual) u += "&pessoa=" + encodeURIComponent(pessoaAtual);
    return u;
  }

  function abrirDrawer(mes) {
    ultimoFoco = document.activeElement;
    drawerEstado = {
      mes: mes, simba: [], rif: [], simbaTotal: 0, rifTotal: 0,
      simbaOff: 0, rifOff: 0, carregando: false
    };
    var $drawer = $("#fin-drawer");
    var $overlay = $("#fin-drawer-overlay");
    var $body = $("#fin-drawer-body");

    $("#fin-drawer-titulo").text("Detalhes de " + mesBR(mes));
    $body.html('<div style="padding:20px;text-align:center;color:var(--text-muted);">Carregando detalhes...</div>');
    $drawer.removeClass("hidden");
    $overlay.removeClass("hidden");
    document.body.style.overflow = "hidden";
    $("#fin-drawer-fechar").focus();

    carregarDetalhes(false);
  }

  function carregarDetalhes(mais) {
    var est = drawerEstado;
    if (!est || est.carregando) return;
    est.carregando = true;
    var $body = $("#fin-drawer-body");
    if (mais) $body.append('<div class="fin-drawer-carregando" style="padding:10px;text-align:center;color:var(--text-muted);">Carregando mais...</div>');

    $.getJSON(urlDetalhes(mais ? est.simbaOff : 0, mais ? est.rifOff : 0))
      .done(function (resp) {
        resp = resp || {};
        if (mais) {
          est.simba = est.simba.concat(resp.simba || []);
          est.rif = est.rif.concat(resp.rif || []);
        } else {
          est.simba = resp.simba || [];
          est.rif = resp.rif || [];
        }
        est.simbaTotal = num(resp.simba_total);
        est.rifTotal = num(resp.rif_total);
        est.simbaOff = est.simba.length;
        est.rifOff = est.rif.length;
        est.carregando = false;
        if (drawerEstado === est) renderDrawerConteudo(est);
      })
      .fail(function () {
        est.carregando = false;
        $body.html('<div style="padding:20px;text-align:center;color:var(--danger);">Erro ao carregar detalhes.</div>');
      });
  }

  function linkFonteSimba(l) {
    return "simba.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&simba=" + encodeURIComponent(l.id_extrato);
  }
  function linkFonteRif(c) {
    return "rif.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&rif=" + encodeURIComponent(c.id_rif);
  }

  function renderDrawerConteudo(est) {
    var $body = $("#fin-drawer-body");
    var html = [];
    var parcialSimba = est.simbaTotal > 0 && est.simba.length < est.simbaTotal;
    var parcialRif = est.rifTotal > 0 && est.rif.length < est.rifTotal;

    if (est.simba.length) {
      var totC = 0, totD = 0;
      est.simba.forEach(function (l) {
        if (l.cd_natureza === "C") totC += num(l.vl_operacao); else totD += num(l.vl_operacao);
      });
      html.push('<h3 style="margin:0 0 8px;font-size:13px;color:var(--text-secondary);">SIMBA — Lançamentos (' +
        formatarNumero(est.simba.length) + (est.simbaTotal ? " de " + formatarNumero(est.simbaTotal) : "") + ')</h3>');
      html.push('<div class="fin-drawer-totais">Créditos <strong style="color:#16a34a">' + formatarReais(totC) + '</strong> · Débitos <strong style="color:#dc2626">' + formatarReais(totD) + '</strong>' +
        (parcialSimba ? " <em>(parcial)</em>" : "") + '</div>');
      html.push('<div style="max-height:300px;overflow:auto;">');
      html.push('<table class="grupo-tabela" style="font-size:12px;">');
      html.push('<thead><tr><td class="col-nome">Data</td><td class="col-nome" style="text-align:right;">Valor</td><td class="col-nome">Natureza</td><td class="col-nome">Pessoa</td><td class="col-nome">Conta</td><td class="col-nome">Descrição / Contraparte</td><td class="col-nome">Fonte</td></tr></thead>');
      html.push('<tbody>');
      est.simba.forEach(function (l) {
        var cor = l.cd_natureza === "C" ? "#16a34a" : "#dc2626";
        var nat = l.cd_natureza === "C" ? "Crédito" : "Débito";
        html.push('<tr>');
        html.push('<td class="col-nome">' + formatarDataBR(l.dt_lancamento) + '</td>');
        html.push('<td class="col-nome" style="text-align:right;color:' + cor + ';font-weight:600;">' + formatarReais(l.vl_operacao) + '</td>');
        html.push('<td class="col-nome"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + cor + ';margin-right:6px;vertical-align:middle;"></span>' + nat + '</td>');
        html.push('<td class="col-nome">' + txt(l.no_titular) +
          (l.nu_cpf_cnpj_titular ? '<br><span style="opacity:.65;font-size:11px;">' + txt(l.nu_cpf_cnpj_titular) + '</span>' : "") + '</td>');
        html.push('<td class="col-nome">' + txt(l.no_banco) + ' ' + txt(l.nu_conta) + '</td>');
        html.push('<td class="col-nome">' + txt(l.ds_historico || l.no_contraparte) +
          (l.nu_cpf_cnpj_contraparte ? '<br><span style="opacity:.65;font-size:11px;">' + txt(l.nu_cpf_cnpj_contraparte) + '</span>' : "") + '</td>');
        html.push('<td class="col-nome"><a href="' + linkFonteSimba(l) + '" title="Abrir o extrato no SIMBA">SIMBA ↗</a></td>');
        html.push('</tr>');
      });
      html.push('</tbody></table></div>');
      if (parcialSimba) {
        html.push('<div style="margin-top:8px;"><button type="button" class="btn btn-secondary fin-mais-simba" style="font-size:12px;">Carregar mais lançamentos (' +
          formatarNumero(est.simba.length) + ' de ' + formatarNumero(est.simbaTotal) + ')</button></div>');
      }
    }

    if (est.rif.length) {
      var rc = 0, rd = 0;
      est.rif.forEach(function (c) { rc += num(c.vl_campo_b); rd += num(c.vl_campo_c); });
      html.push('<h3 style="margin:16px 0 8px;font-size:13px;color:var(--text-secondary);">RIF — Comunicações (' +
        formatarNumero(est.rif.length) + (est.rifTotal ? " de " + formatarNumero(est.rifTotal) : "") + ')</h3>');
      html.push('<div class="fin-drawer-totais">Créditos <strong style="color:#16a34a">' + formatarReais(rc) + '</strong> · Débitos <strong style="color:#dc2626">' + formatarReais(rd) + '</strong>' +
        (parcialRif ? " <em>(parcial)</em>" : "") + '</div>');
      html.push('<div style="max-height:300px;overflow:auto;">');
      html.push('<table class="grupo-tabela" style="font-size:12px;">');
      html.push('<thead><tr><td class="col-nome">Data op.</td><td class="col-nome" style="text-align:right;">Crédito</td><td class="col-nome" style="text-align:right;">Débito</td><td class="col-nome">Pessoa</td><td class="col-nome">Tipo</td><td class="col-nome">Contraparte</td><td class="col-nome">Fonte</td></tr></thead>');
      html.push('<tbody>');
      est.rif.forEach(function (c) {
        html.push('<tr>');
        html.push('<td class="col-nome">' + formatarDataBR(c.dt_operacao) +
          (c.usa_recebimento ? ' <span class="fin-flag-rif" title="Sem data de operação — usa a data de recebimento">*</span>' : "") + '</td>');
        html.push('<td class="col-nome" style="text-align:right;color:#16a34a;">' + formatarReais(c.vl_campo_b) + '</td>');
        html.push('<td class="col-nome" style="text-align:right;color:#dc2626;">' + formatarReais(c.vl_campo_c) + '</td>');
        html.push('<td class="col-nome">' + txt(c.no_envolvido) +
          (c.nu_cpf_cnpj_envolvido ? '<br><span style="opacity:.65;font-size:11px;">' + txt(c.nu_cpf_cnpj_envolvido) + '</span>' : "") + '</td>');
        html.push('<td class="col-nome">' + txt(c.tp_comunicacao) + '</td>');
        html.push('<td class="col-nome">' + txt(c.no_contraparte) +
          (c.nu_cpf_cnpj_contraparte ? '<br><span style="opacity:.65;font-size:11px;">' + txt(c.nu_cpf_cnpj_contraparte) + '</span>' : "") + '</td>');
        html.push('<td class="col-nome"><a href="' + linkFonteRif(c) + '" title="Abrir o RIF">RIF ↗</a></td>');
        html.push('</tr>');
      });
      html.push('</tbody></table></div>');
      if (parcialRif) {
        html.push('<div style="margin-top:8px;"><button type="button" class="btn btn-secondary fin-mais-rif" style="font-size:12px;">Carregar mais comunicações (' +
          formatarNumero(est.rif.length) + ' de ' + formatarNumero(est.rifTotal) + ')</button></div>');
      }
    }

    if (!est.simba.length && !est.rif.length) {
      html.push('<div style="padding:20px;text-align:center;color:var(--text-muted);">Nenhum lançamento ou comunicação detalhada para este mês.</div>');
    }

    $body.html(html.join(""));
    $body.find(".fin-mais-simba, .fin-mais-rif").on("click", function () { carregarDetalhes(true); });
  }

  function fecharDrawer() {
    $("#fin-drawer").addClass("hidden");
    $("#fin-drawer-overlay").addClass("hidden");
    document.body.style.overflow = "";
    if (ultimoFoco && typeof ultimoFoco.focus === "function") ultimoFoco.focus();
    ultimoFoco = null;
  }

  $("#fin-drawer-fechar, #fin-drawer-overlay").on("click", fecharDrawer);
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && !$("#fin-drawer").hasClass("hidden")) fecharDrawer();
  });

  $("#filtro-mes-ini, #filtro-mes-fim, #filtro-fonte, #filtro-tipo, #filtro-valor-min").on("change input", function () {
    clearTimeout(window._filtroTimer);
    window._filtroTimer = setTimeout(aplicarFiltros, 150);
  });
  $("#btn-limpar-filtros").on("click", limparFiltros);

  // a pessoa filtra no backend (os meses chegam agregados por pessoa), então muda-la recarrega
  $("#filtro-pessoa").on("change", function () {
    pessoaAtual = $(this).val() || "";
    carregar();
  });
  $("#btn-comparar").on("click", function () {
    comparando = !comparando;
    if (dados) renderGraficoAtual(obterFiltros(), dados.meses || []);
  });

  // ---------- carga inicial ----------

  function definirPeriodoPadrao() {
    if (!mesesOriginais.length) return;
    var primeiro = mesesOriginais[0].mes;
    var ultimo = mesesOriginais[mesesOriginais.length - 1].mes;
    ["#filtro-mes-ini", "#filtro-mes-fim"].forEach(function (sel) {
      var $inp = $(sel);
      $inp.attr("min", primeiro).attr("max", ultimo);
    });
    if (!$("#filtro-mes-ini").val()) $("#filtro-mes-ini").val(primeiro);
    if (!$("#filtro-mes-fim").val()) $("#filtro-mes-fim").val(ultimo);
  }

  function restaurarFiltrosDaUrl() {
    if (!window.EstadoUrl) return;
    var e = window.EstadoUrl.ler(["fini", "ffim", "ffonte", "ftipo", "fvalor", "fpessoa"]);
    if (e.fini) $("#filtro-mes-ini").val(e.fini);
    if (e.ffim) $("#filtro-mes-fim").val(e.ffim);
    if (e.ffonte === "simba" || e.ffonte === "rif") $("#filtro-fonte").val(e.ffonte);
    if (e.ftipo === "credito" || e.ftipo === "debito") $("#filtro-tipo").val(e.ftipo);
    if (e.fvalor && num(e.fvalor) > 0) $("#filtro-valor-min").val(e.fvalor);
    if (e.fpessoa && !pessoaAtual) pessoaAtual = e.fpessoa;
  }

  function renderPessoas() {
    var pessoas = (dados && dados.pessoas) || [];
    var $sel = $("#filtro-pessoa").empty();
    $("<option>").val("").text(pessoas.length > 1 ? "Todas as pessoas (soma)" : "Todas").appendTo($sel);
    pessoas.forEach(function (p) {
      var rot = p.nome || p.chave;
      if (p.documento) rot += " — " + p.documento;
      rot += p.tem_simba && p.tem_rif ? " (SIMBA+RIF)" : (p.tem_simba ? " (SIMBA)" : " (RIF)");
      $("<option>").val(p.chave).text(rot).appendTo($sel);
    });
    var conhecida = pessoas.some(function (p) { return p.chave === pessoaAtual; });
    if (!conhecida) pessoaAtual = "";
    $sel.val(pessoaAtual);
    $("#filtro-pessoa-wrap").toggleClass("hidden", pessoas.length < 2);
  }

  function carregar() {
    if (!idInquerito) return;
    Carregando.emTabela($("#corpo-meses"), 10);
    Carregando.emBloco($("#fin-grafico-wrap"));
    $("#fin-vazio").addClass("hidden");
    var url = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/financeiro-timeline";
    if (pessoaAtual) url += "?pessoa=" + encodeURIComponent(pessoaAtual);
    $.getJSON(url)
      .done(function (resp) {
        dados = resp || { meses: [], extratos: [], rifs: [], pessoas: [], por_pessoa: [], resumo: {} };
        mesesOriginais = (dados.meses || []).map(function (m) { return $.extend({}, m); });
        definirPeriodoPadrao();
        Carregando.limpar($("#fin-grafico-wrap"));
        renderPessoas();
        renderFontes();
        aplicarFiltros();
      })
      .fail(function () {
        Carregando.limpar($("#fin-grafico-wrap"));
        $("#corpo-meses").empty();
        $("#fin-vazio").removeClass("hidden").text("Erro ao carregar a linha do tempo financeira.");
      });
  }

  restaurarFiltrosDaUrl();
  carregar();
})();
