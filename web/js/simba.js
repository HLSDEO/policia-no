/* Extrato bancário do SIMBA (relatório R304) — contas, lançamentos, contrapartes e alertas.
 *
 * Diferença de fôlego para a página do RIF: um R304 passa fácil de 100 mil lançamentos, então
 * a aba "Lançamentos" é filtrada e paginada NO SERVIDOR (nada de baixar tudo e filtrar em JS).
 * Contas (centenas) e contrapartes (milhares, já consolidadas) cabem no navegador e são
 * filtradas localmente; alertas vêm prontos do backend, calculados em SQL.
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idExtrato = paramUrl("simba");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "contas";
  var abaAtiva = "contas";

  // o grafo virou tela própria (grafo_simba.html) — a aba em colunas daqui foi removida, mas
  // links e favoritos antigos ainda apontam para simba.html?...&aba=grafo, então são desviados
  // antes de carregar qualquer coisa
  var urlGrafo = "grafo_simba.html?inquerito=" + encodeURIComponent(idInquerito || "") +
    "&simba=" + encodeURIComponent(idExtrato || "");
  if (abaInicial === "grafo" && idExtrato) {
    window.location.replace(urlGrafo);
    return;
  }
  $("#abrir-grafo").attr("href", urlGrafo);

  // a gaveta do grafo manda o investigador para os lançamentos de uma pessoa (?busca=), que é o
  // que o clique no nó fazia na aba antiga
  var buscaInicial = paramUrl("busca");

  // estado restaurado da URL via EstadoUrl (js/utils/estado_url.js): filtros e página voltam
  // quando se volta de um detalhe ou se abre um link compartilhado. As chaves são prefixadas
  // por aba (cta*/l*/c*) para não brigar com ?inquerito=/?simba=/?busca=.
  var estadoSalvo = window.EstadoUrl ? window.EstadoUrl.ler([
    "ctabusca", "ctapes", "ctabanco", "ctaordem",
    "ltermo", "lconta", "lnat", "linicio", "lfim", "lvalor", "lpagina",
    "cbusca", "cconta", "cdirecao", "cinternas"
  ]) : {};

  var extrato = null;
  var contas = [];
  var contrapartes = [];       // o que está na tela agora (consolidado ou por conta)
  var cpCarregado = false;
  var alertas = null;
  var serie = [];

  var contagensEnt = { simba_conta: {}, simba_lancamento: {}, simba_contraparte: {} };

  // estado da aba Lançamentos (paginação server-side)
  var lanc = { pagina: 1, tamanho: 200, total: 0, linhas: [], carregado: false };
  var timerBuscaLanc = null;
  var timerBuscaCp = null;

  // ------------------------------------------------------------- helpers
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }

  function formatarData(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\d)(T[\d:.]+)?$/, "$1T00:00:00Z"));
    if (isNaN(d)) return "—";
    return d.toLocaleDateString("pt-BR", { timeZone: "UTC" });
  }
  function formatarValor(v) {
    if (v === null || v === undefined || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function formatarReais(v) { return "R$ " + formatarValor(num(v)); }
  function formatarNumero(n) { return num(n).toLocaleString("pt-BR"); }

  // CPF/CNPJ chega só com dígitos do banco — a pontuação é remontada aqui, para bater com o
  // que o investigador lê no extrato em papel
  function formatarDoc(d) {
    var s = String(d || "").replace(/\D/g, "");
    if (s.length === 11) return s.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
    if (s.length === 14) return s.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, "$1.$2.$3/$4-$5");
    return s || "—";
  }
  function ehPJ(doc) { return String(doc || "").replace(/\D/g, "").length === 14; }

  function periodo(ini, fim) {
    if (!ini && !fim) return "—";
    return formatarData(ini) + " – " + formatarData(fim);
  }

  function contaRotulo(c) {
    return (c.no_titular || "—") + " · " + (c.no_banco || "—") +
      " ag " + (c.nu_agencia || "—") + " cc " + (c.nu_conta || "—");
  }

  function botaoEntidade(tp, id, rotulo) {
    if (!idInquerito || !window.EntidadesUI) return $();
    return EntidadesUI.botao({
      idInquerito: idInquerito, tpOrigem: tp, idOrigem: id, rotulo: rotulo,
      temComentario: !!contagensEnt[tp][id], aoMudar: function () {}
    });
  }

  // ------------------------------------------------------------- resumo
  // A importação roda em background: dá para chegar aqui pela URL com o extrato ainda carregando
  // (ou depois de falhar/ser cancelado). Nesse estado os números são parciais ou zerados — avisar
  // é obrigatório, senão a página parece um extrato pronto que simplesmente movimentou pouco.
  function avisoImportacao() {
    var status = extrato && extrato.cd_status;
    if (!status || status === "concluido") return;
    var texto = {
      pendente: "Importação enfileirada — os números abaixo ainda não valem.",
      processando: "Importação em andamento — o que aparece abaixo está incompleto e muda enquanto o arquivo é lido.",
      erro: "A importação falhou e os dados parciais foram descartados: " + (extrato.tx_erro || "erro não informado"),
      cancelado: "Importação cancelada — os dados parciais foram descartados."
    }[status] || ("Importação em estado '" + status + "'.");
    $("<div>")
      .addClass("smb-kpi alerta")
      .css({ "grid-column": "1 / -1", "border-color": "#d97706" })
      .append($("<div>").addClass("rot").text("Atenção"))
      .append($("<div>").css({ "font-size": "13px", "font-weight": "600" }).text(texto))
      .prependTo($("#smb-resumo"));
  }

  function renderResumo() {
    if (!extrato) return;
    var liquido = num(extrato.vl_credito) - num(extrato.vl_debito);
    var kpis = [
      { rot: "Contas afastadas", val: formatarNumero(extrato.qt_contas) },
      { rot: "Titulares", val: formatarNumero(extrato.qt_titulares) },
      { rot: "Lançamentos", val: formatarNumero(extrato.qt_lancamentos) },
      { rot: "Contrapartes", val: formatarNumero(extrato.qt_contrapartes) },
      { rot: "Crédito total", val: formatarReais(extrato.vl_credito), cls: "credito",
        dica: "Soma TUDO — inclui transferência entre contas do próprio titular e aplicação " +
          "financeira automática/estorno. Para \"recebido de terceiros\" veja o KPI abaixo." },
      { rot: "Débito total", val: formatarReais(extrato.vl_debito), cls: "debito" },
      { rot: "Saldo do fluxo", val: formatarReais(liquido), cls: liquido >= 0 ? "credito" : "debito" },
      { rot: "Período", val: periodo(extrato.dt_primeiro_lancamento, extrato.dt_ultimo_lancamento) },
    ];
    // recebido/pago de contraparte externa identificada que NÃO é titular de conta afastada aqui —
    // é o número que responde "quanto recebeu/pagou de terceiros", diferente do crédito/débito bruto
    kpis.push({
      rot: "Recebido de terceiros",
      val: formatarReais(extrato.vl_credito_externo),
      cls: "credito",
      dica: "Crédito só de contraparte externa identificada, excluindo transferência entre contas " +
        "do próprio titular e aplicação financeira automática/estorno — é o número correto para " +
        "\"quanto recebeu de outras pessoas/outros titulares\"."
    });
    kpis.push({
      rot: "Pago a terceiros",
      val: formatarReais(extrato.vl_debito_externo),
      cls: "debito"
    });
    if (Number(extrato.qt_sem_natureza)) {
      kpis.push({ rot: "Sem crédito/débito", val: formatarNumero(extrato.qt_sem_natureza), cls: "alerta" });
    }
    var vlEntreContasTitular = num(extrato.vl_credito_interno) + num(extrato.vl_debito_interno);
    if (vlEntreContasTitular) {
      kpis.push({
        rot: "Entre contas do próprio titular",
        val: formatarReais(vlEntreContasTitular),
        cls: "alerta",
        dica: "C " + formatarReais(extrato.vl_credito_interno) + " · D " + formatarReais(extrato.vl_debito_interno) +
          " — dinheiro circulando entre contas do(s) mesmo(s) titular(es) afastado(s) neste extrato. " +
          "Não é recebimento de terceiro."
      });
    }
    var vlFinanceiroProprio = num(extrato.vl_credito_financeiro_proprio) + num(extrato.vl_debito_financeiro_proprio);
    if (vlFinanceiroProprio) {
      kpis.push({
        rot: "Aplicação financeira / estorno",
        val: formatarReais(vlFinanceiroProprio),
        cls: "alerta",
        dica: "C " + formatarReais(extrato.vl_credito_financeiro_proprio) + " · D " + formatarReais(extrato.vl_debito_financeiro_proprio) +
          " — aplicação financeira automática, resgate, rendimento, estorno ou devolução, sem " +
          "contraparte identificada. É o banco girando o saldo do próprio titular ou desfazendo " +
          "uma operação — não é dinheiro de terceiro. Não aparece na aba Contrapartes."
      });
    }
    var vlOutrosSemCp = num(extrato.vl_credito_outros_sem_contraparte) + num(extrato.vl_debito_outros_sem_contraparte);
    if (vlOutrosSemCp) {
      // esse valor NÃO está na aba de contrapartes — o extrato não diz quem está do outro lado
      kpis.push({
        rot: "Outras sem contraparte",
        val: formatarReais(vlOutrosSemCp),
        cls: "alerta",
        dica: "C " + formatarReais(extrato.vl_credito_outros_sem_contraparte) + " · D " + formatarReais(extrato.vl_debito_outros_sem_contraparte) +
          " — saque, depósito em espécie, tarifa, juros ou outra operação sem nome nem CPF/CNPJ da " +
          "contraparte. Esse valor não aparece na aba Contrapartes."
      });
    }
    var $r = $("#smb-resumo").empty();
    kpis.forEach(function (k) {
      var $c = $("<div>").addClass("smb-kpi" + (k.cls ? " " + k.cls : "")).appendTo($r);
      if (k.dica) $c.attr("title", k.dica);
      $("<div>").addClass("rot").text(k.rot).appendTo($c);
      $("<div>").addClass("val").text(k.val).appendTo($c);
    });
    avisoImportacao();
    // só o nome do arquivo: NO_ARQUIVO guarda o caminho em data/ (docs/organizacao_arquivos.md)
    $("#smb-arquivo").text(extrato.no_arquivo ? "· " + window.AnalyserUtils.nomeArquivo(extrato.no_arquivo) : "");
  }

  // --------------------------------------------------- gráfico mensal
  var SVG_NS = "http://www.w3.org/2000/svg";

  function elG(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  /* Barras crédito/débito lado a lado por mês com tooltip interativo e clique para filtrar. */
  function renderGrafico() {
    var svg = document.getElementById("smb-grafico");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!serie.length) {
      $("#smb-grafico-titulo").text("Movimentação por mês — sem lançamentos datados");
      return;
    }

    var largura = Math.max(640, serie.length * 32);
    var altura = 240, base = altura - 32, topo = 16;
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("height", altura);
    svg.setAttribute("width", "100%");
    svg.style.minWidth = largura + "px";

    var maximo = serie.reduce(function (m, p) {
      return Math.max(m, num(p.vl_credito), num(p.vl_debito));
    }, 0) || 1;
    var passo = largura / serie.length;
    var larguraBarra = Math.max(4, Math.min(14, passo / 2 - 2));

    // <title> dentro do SVG é o equivalente ao alt de uma imagem: leitor de tela anuncia o
    // resumo antes de descer pelas barras (que também ganham aria-label individual abaixo)
    var tituloGrafico = elG("title", {});
    tituloGrafico.textContent = "Movimentação de " + serie[0].mes + " a " + serie[serie.length - 1].mes +
      " — maior mês: " + formatarReais(maximo);
    svg.appendChild(tituloGrafico);

    // Linhas de grade horizontais
    [0, 0.25, 0.5, 0.75, 1].forEach(function (frac) {
      var y = base - (base - topo) * frac;
      svg.appendChild(elG("line", {
        x1: 0, y1: y, x2: largura, y2: y,
        "class": "eixo", "stroke-dasharray": "4,4", opacity: 0.2
      }));
    });
    svg.appendChild(elG("line", { x1: 0, y1: base, x2: largura, y2: base, "class": "eixo" }));

    // Barras
    serie.forEach(function (p, i) {
      var x = i * passo + passo / 2;
      [["vl_credito", "barra-credito", -1], ["vl_debito", "barra-debito", 1]].forEach(function (par) {
        var v = num(p[par[0]]);
        if (v <= 0) return;
        var h = Math.max(1, (v / maximo) * (base - topo));
        var bx = x + (par[2] < 0 ? -larguraBarra - 1 : 1);
        svg.appendChild(elG("rect", {
          x: bx, y: base - h, width: larguraBarra, height: h,
          rx: 2, ry: 2, "class": par[1],
          "data-mes": p.mes,
          "data-tipo": par[2] === -1 ? "crédito" : "débito",
          "data-valor": formatarReais(v),
          "data-credito": formatarReais(p.vl_credito),
          "data-debito": formatarReais(p.vl_debito),
          "data-qt": formatarNumero(p.qt_lancamentos),
          // barra alcançável por Tab e acionável por Enter/Espaço (handlers abaixo): sem isso o
          // gráfico era uma ilha só de mouse
          "tabindex": "0", "role": "button",
          "aria-label": p.mes + " — " + (par[2] === -1 ? "crédito " : "débito ") + formatarReais(v) +
            " (" + formatarNumero(p.qt_lancamentos) + " lançamentos no mês). Ativar para filtrar o mês."
        }));
      });
    });

    // Rótulos do eixo X (AAAA/MM)
    var intervalo = serie.length > 24 ? 12 : 6;
    serie.forEach(function (p, i) {
      var mostrar = /-01$/.test(p.mes) || i === 0 || i % intervalo === 0;
      if (!mostrar) return;
      var partes = p.mes.split("-");
      var t = elG("text", { x: i * passo + passo / 2, y: altura - 10, "text-anchor": "middle", "class": "rotulo" });
      t.textContent = partes[0] + "/" + partes[1];
      svg.appendChild(t);
    });

    $("#smb-grafico-titulo").text(
      "Movimentação por mês — pico de " + formatarReais(maximo) + " em um único mês"
    );
  }

  // Tooltip flutuante e clique para filtrar por mês. O tooltip é montado com nós de texto
  // (.text(), nunca .html() com concatenação): mesmo vindo do nosso backend, interpolar dado em
  // string de markup é o padrão que abre porta para injeção — texto é texto, e só se exibe.
  function conteudoTooltip($r) {
    var $tt = $("#smb-tooltip").empty();
    $("<strong>").text(String($r.data("mes")).replace("-", "/")).appendTo($tt);
    $tt.append(document.createTextNode(" — " + $r.data("tipo") + " "));
    $("<strong>").text(String($r.data("valor"))).appendTo($tt);
    $tt.append(document.createTextNode(
      " · crédito " + $r.data("credito") + " · débito " + $r.data("debito") +
      " · " + $r.data("qt") + " lançamentos"));
    return $tt;
  }
  function posicionarTooltip(pageX, pageY) {
    var wrap = $("#smb-tooltip").parent();
    var off = wrap.offset();
    $("#smb-tooltip").css({
      left: Math.min(pageX - off.left + 14, wrap.width() - 220) + "px",
      top: (pageY - off.top - 36) + "px"
    });
  }

  $(document).on("mouseenter", "#smb-grafico rect", function () {
    conteudoTooltip($(this)).show();
  }).on("mousemove", "#smb-grafico rect", function (e) {
    posicionarTooltip(e.pageX, e.pageY);
  }).on("mouseleave", "#smb-grafico rect", function () {
    $("#smb-tooltip").hide();
  // teclado: a barra focada mostra o tooltip ancorado nela mesma (não há cursor para seguir)
  }).on("focusin", "#smb-grafico rect", function () {
    var r = this.getBoundingClientRect();
    conteudoTooltip($(this)).show();
    posicionarTooltip(r.left + r.width / 2 + window.pageXOffset, r.top + window.pageYOffset);
  }).on("focusout", "#smb-grafico rect", function () {
    $("#smb-tooltip").hide();
  }).on("keydown", "#smb-grafico rect", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $(this).trigger("click"); }
  }).on("click", "#smb-grafico rect", function () {
    var mes = $(this).data("mes");
    var d = new Date(mes + "-01T00:00:00Z");
    d.setUTCMonth(d.getUTCMonth() + 1, 0);
    var fim = d.toISOString().slice(0, 10);
    $("#f-lan-inicio").val(mes + "-01");
    $("#f-lan-fim").val(fim);
    $("#f-lan-conta").val("");
    $("#f-lan-natureza").val("");
    lanc.carregado = true;
    trocarAba("lancamentos");
    carregarLancamentos(1);
  });

  // ------------------------------------------------------------- contas
  function popularSelectContas() {
    var opcoes = contas.slice().sort(function (a, b) {
      return String(a.no_titular || "").localeCompare(String(b.no_titular || ""), "pt-BR");
    });
    [$("#f-lan-conta"), $("#f-cp-conta")].forEach(function ($sel) {
      opcoes.forEach(function (c) {
        $("<option>").val(c.id_conta_simba).text(contaRotulo(c)).appendTo($sel);
      });
    });
    var bancos = {};
    contas.forEach(function (c) { if (c.no_banco) bancos[c.no_banco] = true; });
    Object.keys(bancos).sort(function (a, b) { return a.localeCompare(b, "pt-BR"); }).forEach(function (b) {
      $("<option>").val(b).text(b).appendTo($("#f-cta-banco"));
    });
  }

  function contasFiltradas() {
    var termo = $.trim($("#f-cta-busca").val()).toLowerCase();
    var pessoa = $("#f-cta-pessoa").val();
    var banco = $("#f-cta-banco").val();
    var ordem = $("#f-cta-ordem").val();

    var lista = contas.filter(function (c) {
      if (banco && c.no_banco !== banco) return false;
      if (pessoa && (c.tp_pessoa || (ehPJ(c.nu_cpf_cnpj) ? "PJ" : "PF")) !== pessoa) return false;
      if (!termo) return true;
      return [c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia, c.nu_conta, c.tx_identificador]
        .join(" ").toLowerCase().indexOf(termo) !== -1;
    });

    lista.sort(function (a, b) {
      if (ordem === "credito") return num(b.vl_credito) - num(a.vl_credito);
      if (ordem === "debito") return num(b.vl_debito) - num(a.vl_debito);
      if (ordem === "lancamentos") return num(b.qt_lancamentos) - num(a.qt_lancamentos);
      if (ordem === "titular") return String(a.no_titular || "").localeCompare(String(b.no_titular || ""), "pt-BR");
      return (num(b.vl_credito) + num(b.vl_debito)) - (num(a.vl_credito) + num(a.vl_debito));
    });
    return lista;
  }

  function renderContas() {
    var lista = contasFiltradas();
    var $corpo = $("#corpo-contas").empty();
    $("#vazio-contas").toggle(!lista.length);

    lista.forEach(function (c) {
      var $tr = $("<tr>").appendTo($corpo);
      var $titular = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(txt(c.no_titular)).appendTo($titular);
      $("<span>").addClass("smb-badge " + (ehPJ(c.nu_cpf_cnpj) ? "pj" : "pf"))
        .css("margin-left", "6px").text(ehPJ(c.nu_cpf_cnpj) ? "PJ" : "PF").appendTo($titular);
      $("<td>").addClass("col-nome smb-doc").text(formatarDoc(c.nu_cpf_cnpj)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.no_banco)).appendTo($tr);

      var $conta = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text("ag " + txt(c.nu_agencia) + " · cc " + txt(c.nu_conta)).appendTo($conta);
      $("<span>").addClass("smb-sub").text(txt(c.tp_conta)).appendTo($conta);

      var $periodo = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(periodo(c.dt_primeiro_lancamento, c.dt_ultimo_lancamento)).appendTo($periodo);
      $("<span>").addClass("smb-sub").text(
        "abertura " + formatarData(c.dt_abertura) +
        (c.dt_encerramento ? " · encerrada " + formatarData(c.dt_encerramento) : "")
      ).appendTo($periodo);

      $("<td>").addClass("col-nome smb-valor").text(formatarNumero(c.qt_lancamentos)).appendTo($tr);
      $("<td>").addClass("col-nome smb-valor credito").text(formatarValor(c.vl_credito)).appendTo($tr);
      $("<td>").addClass("col-nome smb-valor debito").text(formatarValor(c.vl_debito)).appendTo($tr);
      var saldo = num(c.vl_credito) - num(c.vl_debito);
      $("<td>").addClass("col-nome smb-valor " + (saldo >= 0 ? "credito" : "debito"))
        .text(formatarValor(saldo)).appendTo($tr);

      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "4px 10px", "font-size": "12px" })
        .text("Lançamentos")
        .attr("title", "Abrir a aba de lançamentos já filtrada nesta conta")
        .on("click", function () {
          $("#f-lan-conta").val(String(c.id_conta_simba));
          lanc.carregado = true;   // impede que trocarAba dispare uma 2ª carga
          trocarAba("lancamentos");
          carregarLancamentos(1);
        })
        .appendTo($acoes);
      $acoes.append(" ").append(botaoEntidade("simba_conta", c.id_conta_simba,
        "conta " + contaRotulo(c)));
    });

    $("#f-cta-info").text(lista.length + " de " + contas.length);
    atualizarTotal();
    salvarEstadoContas();
  }

  // -------------------------------------------------------- lançamentos
  function filtrosLancamentos() {
    var p = { pagina: lanc.pagina, tamanho: lanc.tamanho };
    var conta = $("#f-lan-conta").val();
    var termo = $.trim($("#f-lan-busca").val());
    var natureza = $("#f-lan-natureza").val();
    var inicio = $("#f-lan-inicio").val();
    var fim = $("#f-lan-fim").val();
    var valor = $("#f-lan-valor").val();
    if (conta) p.conta = conta;
    if (termo) p.termo = termo;
    if (natureza) p.natureza = natureza;
    if (inicio) p.dt_inicio = inicio;
    if (fim) p.dt_fim = fim;
    if (valor) p.valor_minimo = valor;
    return p;
  }

  function carregarLancamentos(pagina) {
    lanc.pagina = pagina || 1;
    lanc.carregado = true;
    $("#f-lan-info").text("carregando…");
    return $.getJSON("/api/investigacao/simba/" + idExtrato + "/lancamentos", filtrosLancamentos())
      .done(function (resp) {
        lanc.total = resp.total;
        lanc.linhas = resp.linhas || [];
        renderLancamentos(resp);
        salvarEstadoLancamentos();
      })
      .fail(function () {
        // o erro entra no lugar do hint vazio: "0 lançamentos" e "a consulta falhou" são
        // conclusões opostas e a tela não pode confundi-las — Estados traz o "Tentar de novo"
        Estados.erro($("#vazio-lancamentos").show(), {
          mensagem: "Não foi possível carregar os lançamentos.",
          aoTentarDeNovo: function () { carregarLancamentos(lanc.pagina); }
        });
        $("#f-lan-info").text("erro ao carregar");
      });
  }

  function renderLancamentos(resp) {
    var $corpo = $("#corpo-lancamentos").empty();
    Estados.limpar($("#vazio-lancamentos"));
    $("#vazio-lancamentos").toggle(!lanc.linhas.length);

    lanc.linhas.forEach(function (l) {
      var $tr = $("<tr>").appendTo($corpo);
      $("<td>").addClass("col-nome").text(formatarData(l.dt_lancamento)).appendTo($tr);

      var $conta = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(txt(l.no_titular)).appendTo($conta);
      $("<span>").addClass("smb-sub").text(
        txt(l.no_banco) + " ag " + txt(l.nu_agencia) + " cc " + txt(l.nu_conta)
      ).appendTo($conta);

      var $desc = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(txt(l.tx_descricao)).appendTo($desc);
      var detalhe = [l.nu_documento ? "doc " + l.nu_documento : "", l.tx_local || ""]
        .filter(Boolean).join(" · ");
      if (detalhe) $("<span>").addClass("smb-sub").text(detalhe).appendTo($desc);

      var $cp = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(txt(l.no_od)).appendTo($cp);
      var subCp = [];
      if (l.nu_cpf_cnpj_od) subCp.push(formatarDoc(l.nu_cpf_cnpj_od));
      if (l.nu_banco_od) subCp.push("bco " + l.nu_banco_od + " ag " + txt(l.nu_agencia_od) + " cc " + txt(l.nu_conta_od));
      if (l.tx_observacao_od) subCp.push(l.tx_observacao_od);
      if (subCp.length) $("<span>").addClass("smb-sub").text(subCp.join(" · ")).appendTo($cp);

      var classe = l.cd_natureza === "C" ? "credito" : (l.cd_natureza === "D" ? "debito" : "");
      $("<td>").addClass("col-nome smb-valor " + classe).text(formatarValor(l.vl_operacao)).appendTo($tr);
      $("<td>").addClass("col-nome").append(
        l.cd_natureza
          ? $("<span>").addClass("smb-badge " + classe).text(l.cd_natureza === "C" ? "entrada" : "saída")
          : $("<span>").addClass("smb-badge").text("—")
      ).appendTo($tr);
      $("<td>").addClass("col-nome smb-valor").text(
        formatarValor(l.vl_saldo) + (l.cd_natureza_saldo ? " " + l.cd_natureza_saldo : "")
      ).appendTo($tr);

      $("<td>").addClass("col-nome").append(botaoEntidade(
        "simba_lancamento", l.id_lancamento_simba,
        "lançamento " + formatarData(l.dt_lancamento) + " · " + txt(l.tx_descricao) + " · " +
        formatarReais(l.vl_operacao) + (l.no_od ? " · " + l.no_od : "")
      )).appendTo($tr);
    });

    var paginas = Math.max(1, Math.ceil(lanc.total / lanc.tamanho));
    var $pag = $("#smb-paginacao").empty();
    if (lanc.total) {
      $("<button>").addClass("btn btn-secondary").text("‹ Anterior")
        .attr("aria-label", "Página anterior de lançamentos")
        .prop("disabled", lanc.pagina <= 1)
        .on("click", function () { carregarLancamentos(lanc.pagina - 1); }).appendTo($pag);
      $("<span>").attr("role", "status").text("Página " + lanc.pagina + " de " + formatarNumero(paginas)).appendTo($pag);
      $("<button>").addClass("btn btn-secondary").text("Próxima ›")
        .attr("aria-label", "Próxima página de lançamentos")
        .prop("disabled", lanc.pagina >= paginas)
        .on("click", function () { carregarLancamentos(lanc.pagina + 1); }).appendTo($pag);
      $("<span>").css("opacity", ".7").text(
        "Nos filtros atuais: crédito " + formatarReais(resp.vl_credito) +
        " · débito " + formatarReais(resp.vl_debito)
      ).appendTo($pag);
    }

    $("#f-lan-info").text(formatarNumero(lanc.total) + " lançamentos no filtro");
    atualizarTotal();
  }

  // ------------------------------------------------------- contrapartes
  // Duas visões, com colunas diferentes: sem conta escolhida, mostra a contraparte consolidada
  // (somando todas as contas do extrato); com uma conta escolhida, mostra o par conta↔contraparte.
  function consolidado() { return !$("#f-cp-conta").val(); }

  function carregarContrapartes() {
    var base = "/api/investigacao/simba/" + idExtrato + "/contrapartes";
    var p = { limite: 1000 };
    var termo = $.trim($("#f-cp-busca").val());
    if (termo) p.termo = termo;
    if ($("#f-cp-internas").is(":checked")) p.so_internas = true;

    var url = base;
    if (consolidado()) {
      url = base + "-consolidadas";
    } else {
      p.conta = $("#f-cp-conta").val();
      var dir = $("#f-cp-direcao").val();
      if (dir) p.direcao = dir;
    }

    cpCarregado = true;
    $("#f-cp-info").text("carregando…");
    return $.getJSON(url, p).done(function (lista) {
      contrapartes = lista || [];
      renderContrapartes();
      salvarEstadoContrapartes();
    }).fail(function () {
      Estados.erro($("#vazio-contrapartes").show(), {
        mensagem: "Não foi possível carregar as contrapartes.",
        aoTentarDeNovo: carregarContrapartes
      });
      $("#f-cp-info").text("erro ao carregar");
    });
  }

  var cpModoCabecalho = null;   // o cabeçalho só muda entre consolidado/por-conta — não a cada render

  function renderContrapartes() {
    var $cab = $("#cab-contrapartes");
    var $corpo = $("#corpo-contrapartes").empty();
    var lista = contrapartes;
    var direcao = $("#f-cp-direcao").val();

    // no consolidado a direção é uma coluna (creditou/recebeu), então o filtro vira um descarte local
    if (consolidado() && direcao) {
      lista = lista.filter(function (c) {
        return direcao === "credito" ? num(c.vl_creditou) > 0 : num(c.vl_recebeu) > 0;
      });
    }
    Estados.limpar($("#vazio-contrapartes"));
    $("#vazio-contrapartes").toggle(!lista.length);

    var colunas = consolidado()
      ? ["Contraparte", "CPF/CNPJ", "Contas", "Lanç.", "Creditou (R$)", "Recebeu (R$)", "Período", "Ações"]
      : ["Contraparte", "CPF/CNPJ", "Conta do investigado", "Direção", "Lanç.", "Valor (R$)", "Período", "Ações"];
    var modo = colunas.join("|");
    if (cpModoCabecalho !== modo) {
      cpModoCabecalho = modo;
      $cab.empty();
      colunas.forEach(function (c) { $("<th>").attr("scope", "col").addClass("col-nome").text(c).appendTo($cab); });
    }

    lista.forEach(function (c) {
      var $tr = $("<tr>").appendTo($corpo);
      var $nome = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").text(txt(c.no_contraparte)).appendTo($nome);
      if (c.in_titular_no_extrato) {
        $("<span>").addClass("smb-badge interno").css("margin-left", "6px")
          .attr("title", "Esta contraparte também é titular de uma conta afastada no mesmo extrato")
          .text("titular no extrato").appendTo($nome);
      }
      $("<td>").addClass("col-nome smb-doc").text(formatarDoc(c.nu_cpf_cnpj)).appendTo($tr);

      if (consolidado()) {
        $("<td>").addClass("col-nome smb-valor").text(formatarNumero(c.qt_contas)).appendTo($tr);
        $("<td>").addClass("col-nome smb-valor").text(formatarNumero(c.qt_lancamentos)).appendTo($tr);
        $("<td>").addClass("col-nome smb-valor credito").text(formatarValor(c.vl_creditou)).appendTo($tr);
        $("<td>").addClass("col-nome smb-valor debito").text(formatarValor(c.vl_recebeu)).appendTo($tr);
      } else {
        var $contaInv = $("<td>").addClass("col-nome").appendTo($tr);
        $("<span>").text(txt(c.no_titular)).appendTo($contaInv);
        $("<span>").addClass("smb-sub").text(
          txt(c.no_banco) + " ag " + txt(c.nu_agencia) + " cc " + txt(c.nu_conta)
        ).appendTo($contaInv);
        $("<td>").addClass("col-nome").append(
          $("<span>").addClass("smb-badge " + c.cd_direcao)
            .text(c.cd_direcao === "credito" ? "creditou a conta" : "recebeu da conta")
        ).appendTo($tr);
        $("<td>").addClass("col-nome smb-valor").text(formatarNumero(c.qt_lancamentos)).appendTo($tr);
        $("<td>").addClass("col-nome smb-valor " + c.cd_direcao).text(formatarValor(c.vl_total)).appendTo($tr);
      }

      $("<td>").addClass("col-nome").text(periodo(c.dt_primeiro, c.dt_ultimo)).appendTo($tr);

      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      $("<button>")
        .addClass("btn btn-secondary")
        .css({ padding: "4px 10px", "font-size": "12px" })
        .text("Lançamentos")
        .attr("title", "Abrir os lançamentos desta contraparte")
        .on("click", function () {
          $("#f-lan-busca").val(c.nu_cpf_cnpj || c.no_contraparte || "");
          $("#f-lan-conta").val(consolidado() ? "" : String(c.id_conta_simba));
          $("#f-lan-natureza").val("");
          lanc.carregado = true;   // impede que trocarAba dispare uma 2ª carga
          trocarAba("lancamentos");
          carregarLancamentos(1);
        })
        .appendTo($acoes);
      if (!consolidado()) {
        $acoes.append(" ").append(botaoEntidade("simba_contraparte", c.id_contraparte_simba,
          (c.cd_direcao === "credito" ? "creditou " : "recebeu de ") + txt(c.no_titular) + ": " +
          txt(c.no_contraparte) + " · " + formatarReais(c.vl_total)));
      }
    });

    $("#f-cp-info").text(lista.length + (lista.length >= 1000 ? " (limite de 1.000 — refine a busca)" : ""));
    atualizarTotal();
  }

  // ------------------------------------------------------------ alertas

  // Os patamares dos alertas são configuráveis (Administração > Alertas SIMBA) e vêm junto com o
  // resultado, então o texto do critério é montado com o número realmente aplicado — escrever
  // "R$ 10 mil" fixo aqui mentiria para quem trabalha com outro corte.
  function peso(chave, padrao) {
    var p = (alertas && alertas.parametros) || {};
    return num(p[chave] || padrao);
  }
  function reaisCurto(v) {
    return "R$ " + num(v).toLocaleString("pt-BR");
  }

  // Quanto foi separado do alerta de valores redondos por ser aplicação/resgate/estorno — dito em
  // números, e não só "alguns ficam de fora", para o investigador saber o tamanho do que não vê.
  function redondosInternos() {
    var r = (alertas && alertas.resumo) || {};
    if (!num(r.qt_redondos_internos)) return ".";
    return " (" + formatarNumero(r.qt_redondos_internos) + " lançamentos, " +
      formatarReais(r.vl_redondos_internos) + ", ficaram de fora por esse motivo).";
  }

  // Passo redondo do valor: 250.000 é múltiplo de 50 mil, 15.000 só de mil. Serve para separar o
  // valor "combinado" do valor que só por acaso não tem centavos.
  var PASSOS_REDONDO = [1000000, 500000, 250000, 100000, 50000, 25000, 10000, 5000, 1000];
  function passoRedondo(valor) {
    var v = num(valor);
    for (var i = 0; i < PASSOS_REDONDO.length; i++) {
      if (v >= PASSOS_REDONDO[i] && v % PASSOS_REDONDO[i] === 0) return PASSOS_REDONDO[i];
    }
    return 0;
  }

  function contaCurta(l, sufixo) {
    var s = sufixo || "";
    return txt(l["no_banco" + s] || l["nu_banco" + s]) +
      " ag " + txt(l["nu_agencia" + s]) + " cc " + txt(l["nu_conta" + s]);
  }

  var SUB_ALERTAS = [
    {
      tipo: "especie", rotulo: "Movimentação em espécie",
      titulo: "Movimentação em espécie por conta",
      criterio: function () {
        return "Lançamentos cuja descrição informada pela instituição indica saque ou depósito em " +
          "dinheiro (cheques, tarifas, juros e estornos ficam de fora). A linha fecha da esquerda " +
          "para a direita: Depósitos + Saques + Sem C/D = Espécie, e o percentual é Espécie ÷ " +
          "Movimentado na conta. \"Sem C/D\" são os lançamentos em espécie que a instituição não " +
          "classificou como entrada nem saída (o R304 manda \"*\"), por isso não entram nas duas " +
          "primeiras colunas. \"Movimentado na conta\" é TUDO que passou na conta no período — " +
          "entradas e saídas somadas, de qualquer natureza (PIX, TED, boleto, aplicação) —, então o " +
          "percentual responde quanto da movimentação daquela conta foi em dinheiro vivo.";
      },
      colunas: ["Titular", "CPF/CNPJ", "Instituição", "Agência / conta", "Lanç.",
                "Depósitos (R$)", "Saques (R$)", "Sem C/D (R$)", "Espécie (R$)",
                "Movimentado na conta (R$)", "% em espécie"],
      vazio: "Nenhum lançamento em espécie identificado pela descrição.",
      dados: function () { return alertas.especie; },
      montar: function (l) {
        var totalEspecie = l.vl_especie !== undefined
          ? num(l.vl_especie) : num(l.vl_depositos) + num(l.vl_saques);
        var totalConta = l.vl_movimentado_conta !== undefined
          ? num(l.vl_movimentado_conta) : num(l.vl_credito) + num(l.vl_debito);
        var pct = totalConta > 0 ? (totalEspecie / totalConta) * 100 : 0;
        var $tr = $("<tr>");
        celula(txt(l.no_titular)).appendTo($tr);
        celula(formatarDoc(l.nu_cpf_cnpj), "smb-doc").appendTo($tr);
        celula(txt(l.no_banco)).appendTo($tr);
        celula("ag " + txt(l.nu_agencia) + " · cc " + txt(l.nu_conta)).appendTo($tr);
        celula(formatarNumero(l.qt_lancamentos), "smb-valor").appendTo($tr);
        celula(formatarValor(l.vl_depositos), "smb-valor credito").appendTo($tr);
        celula(formatarValor(l.vl_saques), "smb-valor debito").appendTo($tr);
        celula(num(l.vl_sem_natureza) ? formatarValor(l.vl_sem_natureza) : "—", "smb-valor")
          .attr("title", num(l.vl_sem_natureza)
            ? "Lançamentos em espécie que a instituição não classificou como crédito nem débito"
            : "")
          .appendTo($tr);
        celula(formatarValor(totalEspecie), "smb-valor").appendTo($tr);
        celula(formatarValor(totalConta), "smb-valor").appendTo($tr);
        celula(pct.toFixed(1).replace(".", ",") + "%", "smb-valor")
          .attr("title", formatarReais(totalEspecie) + " em espécie ÷ " + formatarReais(totalConta) +
                " movimentados na conta")
          .appendTo($tr);
        return $tr;
      }
    },
    {
      tipo: "fracionamento", rotulo: "Possível fracionamento",
      titulo: "Possível fracionamento",
      criterio: function () {
        return peso("min_fracionamento", 3) + " ou mais operações em espécie na mesma conta, no mesmo " +
          "dia e no mesmo sentido, somando " + reaisCurto(peso("limite_comunicacao", 10000)) +
          " ou mais — cada uma isoladamente abaixo desse patamar. É o desenho clássico de quebra de " +
          "valor; pode ter explicação legítima (movimento de comércio, por exemplo).";
      },
      colunas: ["Titular", "Conta", "Data", "Sentido", "Operações", "Total (R$)", "Maior (R$)"],
      vazio: "Nenhum dia com esse padrão.",
      dados: function () { return alertas.fracionamento; },
      montar: function (l) {
        var $tr = $("<tr>");
        celula(txt(l.no_titular)).appendTo($tr);
        celula(contaCurta(l)).appendTo($tr);
        celula(formatarData(l.dt_lancamento)).appendTo($tr);
        $("<td>").addClass("col-nome").append(
          $("<span>").addClass("smb-badge " + (l.cd_natureza === "C" ? "credito" : "debito"))
            .text(l.cd_natureza === "C" ? "depósitos" : "saques")
        ).appendTo($tr);
        celula(formatarNumero(l.qt_lancamentos), "smb-valor").appendTo($tr);
        celula(formatarValor(l.vl_total), "smb-valor").appendTo($tr);
        celula(formatarValor(l.vl_maior), "smb-valor").appendTo($tr);
        return $tr;
      }
    },
    {
      tipo: "internas", rotulo: "Repasses entre titulares",
      titulo: "Repasses entre os próprios titulares afastados",
      criterio: function () {
        return "Contrapartes cujo CPF/CNPJ também é titular de uma conta afastada neste extrato — " +
          "dinheiro circulando dentro do grupo investigado, que por isso NÃO é dinheiro de terceiros. " +
          "Cada linha é um par conta ⇄ contraparte visto do lado de UMA conta: a mesma transferência " +
          "aparece duas vezes quando as duas contas foram afastadas (como saída na conta de quem " +
          "pagou e como entrada na de quem recebeu), então não some os totais. Quando o CPF/CNPJ dos " +
          "dois lados é o mesmo, não são duas pessoas: é a mesma pessoa passando dinheiro entre " +
          "contas dela (a coluna Conta mostra de qual para qual) — o nome pode vir escrito diferente " +
          "porque cada banco cadastrou de um jeito.";
      },
      colunas: ["Titular da conta", "Conta", "Sentido", "Contraparte", "CPF/CNPJ", "Lanç.",
                "Total (R$)", "Período"],
      vazio: "Nenhuma contraparte é titular de outra conta do extrato.",
      dados: function () { return alertas.internas; },
      montar: function (l) {
        var $tr = $("<tr>");
        celula(txt(l.no_titular)).appendTo($tr);
        celula(contaCurta(l)).appendTo($tr);
        $("<td>").addClass("col-nome").append(
          $("<span>").addClass("smb-badge " + l.cd_direcao)
            .text(l.cd_direcao === "credito" ? "recebeu de" : "repassou a")
        ).appendTo($tr);

        var $cp = $("<td>").addClass("col-nome").appendTo($tr);
        $("<span>").text(txt(l.no_contraparte)).appendTo($cp);
        var subCp = [];
        if (l.nu_banco_contraparte || l.nu_conta_contraparte) subCp.push(contaCurta(l, "_contraparte"));
        if (l.in_mesmo_titular) {
          $("<span>").addClass("smb-badge neutro").css("margin-left", "6px")
            .attr("title", "Mesmo CPF/CNPJ do titular da conta — é a própria pessoa movendo dinheiro " +
                  "entre duas contas dela")
            .text("mesma pessoa").appendTo($cp);
        }
        if (subCp.length) $("<span>").addClass("smb-sub").text(subCp.join(" · ")).appendTo($cp);

        celula(formatarDoc(l.nu_cpf_cnpj), "smb-doc").appendTo($tr);
        celula(formatarNumero(l.qt_lancamentos), "smb-valor").appendTo($tr);
        celula(formatarValor(l.vl_total), "smb-valor " + l.cd_direcao).appendTo($tr);
        celula(periodo(l.dt_primeiro, l.dt_ultimo)).appendTo($tr);
        return $tr;
      }
    },
    {
      tipo: "triangulacao", rotulo: "Triangulação",
      titulo: "Triangulação de repasses entre titulares",
      criterio: function () {
        return "Ciclos fechados de três titulares afastados: A repassa a B, B repassa a C e C devolve " +
          "a A. É o repasse entre titulares olhado como circuito, e não como par — o vaivém direto " +
          "entre duas contas salta aos olhos, o desvio por uma terceira não. Só entram pessoas com " +
          "conta afastada aqui (para uma contraparte de fora não se tem o outro lado da conta) e três " +
          "CPF/CNPJ distintos — contas diferentes da mesma pessoa não formam triângulo. O valor que " +
          "pode ter dado a volta inteira é o do MENOR trecho: os demais podem incluir dinheiro de " +
          "outra origem. Circuito fechado não é prova de nada por si só (pagamentos cruzados entre " +
          "sócios e empresas do mesmo grupo desenham a mesma figura), é ponto a explicar.";
      },
      colunas: ["Ciclo", "Trechos", "Menor trecho (R$)", "Soma dos trechos (R$)", "Lanç.", "Período"],
      vazio: "Nenhum ciclo de três titulares identificado.",
      dados: function () { return alertas.triangulacoes || []; },
      montar: function (t) {
        var $tr = $("<tr>");
        var nomes = (t.nos || []).map(function (n) { return n.nome || "—"; });

        var $ciclo = $("<td>").addClass("col-nome").appendTo($tr);
        $("<span>").text(nomes.join(" → ") + " → " + nomes[0]).appendTo($ciclo);
        $("<span>").addClass("smb-sub").text(
          (t.nos || []).map(function (n) { return formatarDoc(n.doc); }).join(" · ")
        ).appendTo($ciclo);

        var $trechos = $("<td>").addClass("col-nome").appendTo($tr);
        (t.trechos || []).forEach(function (x) {
          $("<span>").addClass("smb-sub").text(
            (x.de_nome || "—") + " → " + (x.para_nome || "—") + ": " +
            formatarReais(x.vl_total) + " em " + formatarNumero(x.qt_lancamentos) + " lanç."
          ).appendTo($trechos);
        });

        celula(formatarValor(t.vl_menor), "smb-valor debito").appendTo($tr);
        celula(formatarValor(t.vl_total), "smb-valor").appendTo($tr);
        celula(formatarNumero(t.qt_lancamentos), "smb-valor").appendTo($tr);
        celula(periodo(t.dt_primeiro, t.dt_ultimo)).appendTo($tr);
        return $tr;
      }
    },
    {
      tipo: "passagens", rotulo: "Contas de passagem",
      titulo: "Contas de passagem (possível laranja)",
      criterio: function () {
        return "Contas em que entrou quase o mesmo que saiu: giro (entradas + saídas) de " +
          reaisCurto(peso("giro_passagem", 50000)) + " ou mais, saldo residual de até " +
          num(peso("tolerancia_passagem_pct", 10)) + "% do maior lado e " +
          formatarNumero(peso("min_lancamentos_passagem", 10)) + " ou mais lançamentos. É o desenho " +
          "da conta que só repassa — recebe e devolve, sem acumular. Conta operacional legítima " +
          "também gira, por isso o alerta exige os três juntos; ainda assim, pede explicação, " +
          "não é conclusão.";
      },
      colunas: ["Titular", "CPF/CNPJ", "Conta", "Entradas (R$)", "Saídas (R$)", "Giro (R$)",
                "Residual", "Lanç.", "Período"],
      vazio: "Nenhuma conta com esse padrão de repasse.",
      dados: function () { return alertas.passagens || []; },
      montar: function (l) {
        var giro = num(l.vl_giro) || (num(l.vl_credito) + num(l.vl_debito));
        var residual = (l.pc_saldo_residual !== null && l.pc_saldo_residual !== undefined)
          ? Number(l.pc_saldo_residual).toFixed(1).replace(".", ",") + "%" : "—";
        var $tr = $("<tr>");
        celula(txt(l.no_titular)).appendTo($tr);
        celula(formatarDoc(l.nu_cpf_cnpj), "smb-doc").appendTo($tr);
        celula(contaCurta(l)).appendTo($tr);
        celula(formatarValor(l.vl_credito), "smb-valor credito").appendTo($tr);
        celula(formatarValor(l.vl_debito), "smb-valor debito").appendTo($tr);
        celula(formatarValor(giro), "smb-valor").appendTo($tr);
        celula(residual, "smb-valor")
          .attr("title", "Saldo do fluxo " + formatarReais(num(l.vl_credito) - num(l.vl_debito)) +
            " ÷ maior lado — quanto ficou na conta diante do que passou por ela")
          .appendTo($tr);
        celula(formatarNumero(l.qt_lancamentos), "smb-valor").appendTo($tr);
        celula(periodo(l.dt_primeiro_lancamento, l.dt_ultimo_lancamento)).appendTo($tr);
        return $tr;
      }
    },
    {
      tipo: "redondos", rotulo: "Valores redondos",
      titulo: "Lançamentos de valor redondo",
      criterio: function () {
        return "Operações de " + reaisCurto(peso("limite_comunicacao", 10000)) + " ou mais em múltiplos " +
          "exatos de " + reaisCurto(peso("multiplo_redondo", 1000)) + ". A ideia é simples: pagamento " +
          "de nota, folha, tributo ou compra quase nunca fecha em número redondo — valor combinado " +
          "entre duas pessoas, sim. Quanto maior o passo (um múltiplo de 100 mil pesa mais que um de " +
          "mil) e quanto mais vezes o mesmo valor se repete, mais o lançamento pede explicação. " +
          "\"Repete\" conta quantas vezes esse mesmo valor aparece nesta lista; " +
          "\"em espécie\" marca os que a instituição descreveu como saque ou depósito em dinheiro, " +
          "que é onde o valor redondo mais costuma dizer alguma coisa. Saque não tem contraparte no " +
          "R304 (fica \"—\"): o que identifica o outro lado é o local da transação. " +
          "Aplicação, resgate e estorno ficam FORA desta lista — esse dinheiro não muda de dono e é " +
          "redondo por natureza (aplica-se R$ 1.300.000,00, não R$ 1.312.457,83), e sem separá-los " +
          "eles ocupariam sozinhos o topo do alerta" + redondosInternos() + " Eles continuam " +
          "na aba Lançamentos e no total de valores redondos do resumo.";
      },
      colunas: ["Titular", "Conta", "Data", "Descrição", "Contraparte", "Valor (R$)", "Passo",
                "Repete", "C/D"],
      vazio: "Nenhum lançamento redondo no patamar configurado.",
      dados: function () { return alertas.redondos; },
      montar: function (l) {
        var $tr = $("<tr>");
        celula(txt(l.no_titular)).appendTo($tr);
        celula(contaCurta(l)).appendTo($tr);
        celula(formatarData(l.dt_lancamento)).appendTo($tr);

        var $desc = $("<td>").addClass("col-nome").appendTo($tr);
        $("<span>").text(txt(l.tx_descricao)).appendTo($desc);
        var subDesc = [];
        if (l.in_especie) subDesc.push("em espécie");
        if (l.tx_local) subDesc.push(l.tx_local);
        if (subDesc.length) $("<span>").addClass("smb-sub").text(subDesc.join(" · ")).appendTo($desc);

        celula(txt(l.no_od) + (l.nu_cpf_cnpj_od ? " (" + formatarDoc(l.nu_cpf_cnpj_od) + ")" : ""))
          .appendTo($tr);
        celula(formatarValor(l.vl_operacao), "smb-valor " + (l.cd_natureza === "C" ? "credito" : "debito"))
          .appendTo($tr);

        var passo = passoRedondo(l.vl_operacao);
        celula(passo ? reaisCurto(passo) : "—", "smb-valor")
          .attr("title", passo ? "O valor é múltiplo exato de " + reaisCurto(passo) : "")
          .appendTo($tr);

        var repete = num(l.qt_mesmo_valor);
        celula(repete > 1 ? repete + "×" : "—", "smb-valor")
          .attr("title", repete > 1
            ? "Este mesmo valor aparece " + repete + " vezes entre os lançamentos deste alerta"
            : "")
          .appendTo($tr);

        celula(l.cd_natureza === "C" ? "entrada" : (l.cd_natureza === "D" ? "saída" : "—")).appendTo($tr);
        return $tr;
      }
    }
  ];

  function celula(valor, classe) {
    return $("<td>").addClass("col-nome" + (classe ? " " + classe : "")).text(valor);
  }

  function paginarArray(arr, pagina, tamanho) {
    var total = arr.length;
    var paginas = Math.max(1, Math.ceil(total / tamanho));
    pagina = Math.max(1, Math.min(pagina, paginas));
    return { dados: arr.slice((pagina - 1) * tamanho, pagina * tamanho), pagina: pagina, paginas: paginas, total: total };
  }

  function renderSubPainel(tipo) {
    var TAM = 20;
    var $painel = $("#sub-painel-" + tipo);
    var pagina = parseInt($painel.data("pagina") || 1, 10);
    var cfg;
    for (var i = 0; i < SUB_ALERTAS.length; i++) {
      if (SUB_ALERTAS[i].tipo === tipo) { cfg = SUB_ALERTAS[i]; break; }
    }
    if (!cfg) return;

    var lista = cfg.dados() || [];
    var p = paginarArray(lista, pagina, TAM);
    $painel.data("pagina", p.pagina).empty();

    // o critério vai SEMPRE na tela, antes da tabela (e mesmo quando não há nenhuma ocorrência):
    // um alerta que o investigador não sabe como foi apurado não serve para levar a lugar nenhum
    $("<h3>").text(cfg.titulo).appendTo($painel);
    $("<p>").addClass("criterio")
      .text(typeof cfg.criterio === "function" ? cfg.criterio() : cfg.criterio)
      .appendTo($painel);

    if (!p.dados.length) {
      $("<p>").addClass("smb-alerta-vazio").text(cfg.vazio).appendTo($painel);
      return;
    }

    var $wrap = $("<div>").css("overflow-x", "auto").appendTo($painel);
    var $tab = $("<table>").addClass("grupo-tabela").appendTo($wrap);
    var $cab = $("<tr>").appendTo($("<thead>").appendTo($tab));
    cfg.colunas.forEach(function (c) { $("<th>").attr("scope", "col").addClass("col-nome").text(c).appendTo($cab); });
    var $corpo = $("<tbody>").appendTo($tab);
    p.dados.forEach(function (l) { $corpo.append(cfg.montar(l)); });

    if (p.paginas > 1) {
      var $pag = $("<div>").addClass("smb-sub-paginacao").appendTo($painel);
      $("<button>").addClass("btn btn-secondary").text("‹ Anterior")
        .attr("aria-label", "Página anterior de " + cfg.rotulo)
        .prop("disabled", p.pagina <= 1)
        .on("click", function () {
          $painel.data("pagina", p.pagina - 1);
          renderSubPainel(tipo);
        }).appendTo($pag);
      $("<span>").attr("role", "status").text("Página " + formatarNumero(p.pagina) + " de " + formatarNumero(p.paginas)).appendTo($pag);
      $("<button>").addClass("btn btn-secondary").text("Próxima ›")
        .attr("aria-label", "Próxima página de " + cfg.rotulo)
        .prop("disabled", p.pagina >= p.paginas)
        .on("click", function () {
          $painel.data("pagina", p.pagina + 1);
          renderSubPainel(tipo);
        }).appendTo($pag);
    }

    atualizarTotal();
  }

  function subAlertasTrocar(tipo) {
    $(".smb-sub-aba").removeClass("ativa").attr("aria-selected", "false")
      .filter('[data-sub-aba="' + tipo + '"]').addClass("ativa").attr("aria-selected", "true");
    $(".smb-sub-painel").removeClass("ativo").filter("#sub-painel-" + tipo).addClass("ativo");
    atualizarTotal();
  }

  function renderAlertas() {
    if (!alertas) return;
    var r = alertas.resumo || {};

    $("#smb-alertas-resumo").empty();
    [
      { rot: "Movimentação em espécie", val: formatarReais(r.vl_especie), cls: "alerta" },
      { rot: "Lançamentos em espécie", val: formatarNumero(r.qt_especie) },
      { rot: "Possível fracionamento", val: formatarNumero(alertas.fracionamento.length), cls: "alerta",
        dica: "Dias/conta em que " + peso("min_fracionamento", 3) + " ou mais operações em espécie no " +
          "mesmo sentido somaram " + reaisCurto(peso("limite_comunicacao", 10000)) + " ou mais, todas " +
          "abaixo desse valor isoladamente." },
      { rot: "Valores redondos", val: formatarNumero(r.qt_redondos),
        dica: "Lançamentos de " + reaisCurto(peso("limite_comunicacao", 10000)) + " ou mais em " +
          "múltiplos exatos de " + reaisCurto(peso("multiplo_redondo", 1000)) + ", somando " +
          formatarReais(r.vl_redondos) + ". Destes, " + formatarNumero(r.qt_redondos_internos) +
          " (" + formatarReais(r.vl_redondos_internos) + ") são aplicação, resgate ou estorno e " +
          "não entram na lista do alerta." },
      { rot: "Entre os investigados", val: formatarReais(r.vl_internas), cls: "alerta",
        dica: "Soma dos repasses em que a contraparte também é titular de conta afastada aqui. Conta " +
          "cada transferência pelos dois lados quando as duas contas foram afastadas." },
      { rot: "Triangulação", val: formatarNumero((alertas.triangulacoes || []).length), cls: "alerta",
        dica: "Ciclos A → B → C → A entre titulares afastados." },
      { rot: "Contas de passagem", val: formatarNumero((alertas.passagens || []).length), cls: "alerta",
        dica: "Contas em que entrou quase o mesmo que saiu, com giro de " +
          reaisCurto(peso("giro_passagem", 50000)) + " ou mais — possível conta de repasse." },
      { rot: "Aplicação financeira / estorno", val: formatarReais(num(r.vl_credito_financeiro_proprio) + num(r.vl_debito_financeiro_proprio)),
        cls: "alerta", dica: "Aplicação automática, resgate, rendimento, estorno ou devolução, sem contraparte — não é dinheiro de terceiro." },
      { rot: "Sem crédito/débito", val: formatarNumero(r.qt_sem_natureza) },
    ].forEach(function (k) {
      var $c = $("<div>").addClass("smb-kpi" + (k.cls ? " " + k.cls : "")).appendTo($("#smb-alertas-resumo"));
      if (k.dica) $c.attr("title", k.dica);
      $("<div>").addClass("rot").text(k.rot).appendTo($c);
      $("<div>").addClass("val").text(k.val).appendTo($c);
    });

    $("#smb-sub-abas").empty();
    $("#smb-sub-paineis").empty();

    SUB_ALERTAS.forEach(function (s, i) {
      $("<button>").addClass("btn btn-secondary smb-sub-aba" + (i === 0 ? " ativa" : ""))
        .attr({ role: "tab", "aria-selected": i === 0 ? "true" : "false",
                "aria-controls": "sub-painel-" + s.tipo, id: "sub-aba-" + s.tipo })
        .text(s.rotulo).data("sub-aba", s.tipo)
        .on("click", function () { subAlertasTrocar(s.tipo); })
        .appendTo("#smb-sub-abas");

      $("<div>").addClass("smb-alerta-bloco smb-sub-painel" + (i === 0 ? " ativo" : ""))
        .attr({ id: "sub-painel-" + s.tipo, role: "tabpanel", "aria-labelledby": "sub-aba-" + s.tipo })
        .appendTo("#smb-sub-paineis");
    });

    SUB_ALERTAS.forEach(function (s) { renderSubPainel(s.tipo); });
    atualizarTotal();
  }

  // --------------------------------------------------------------- abas
  var ROTULO_ABA = { contas: "Contas", lancamentos: "Lançamentos", contrapartes: "Contrapartes", alertas: "Alertas" };

  function trocarAba(aba) {
    abaAtiva = aba;
    $(".smb-aba").removeClass("ativa").attr("aria-selected", "false")
      .filter('[data-aba="' + aba + '"]').addClass("ativa").attr("aria-selected", "true");
    $(".smb-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    // a 1ª entrada em Lançamentos respeita a página restaurada da URL (?lpagina=)
    if (aba === "lancamentos" && !lanc.carregado) carregarLancamentos(lanc.pagina || 1);
    if (aba === "contrapartes" && !cpCarregado) carregarContrapartes();
    if (aba === "alertas" && !alertas) carregarAlertas();
    if (opcoesExportacao) opcoesExportacao.nome = "simba_" + aba;
    if (window.EstadoUrl) EstadoUrl.salvar({ aba: aba !== "contas" ? aba : null });
    atualizarBotaoExcel();
    atualizarTotal();
  }

  /* O botão de Excel exporta a TABELA da aba aberta. Grafo e Alertas não têm tabela (um é SVG, o
     outro são blocos), então ali o botão só teria como resposta "não há dados para exportar" —
     some com ele em vez de oferecer uma ação que não existe. */
  function atualizarBotaoExcel() {
    $(".btn-exportar-excel").toggle(!!document.querySelector("#tab-" + abaAtiva));
  }
  $(".smb-aba").on("click", function () { trocarAba($(this).data("aba")); });

  function atualizarTotal() {
    var texto = "";
    if (abaAtiva === "contas") texto = $("#corpo-contas > tr").length + " contas";
    else if (abaAtiva === "lancamentos") texto = formatarNumero(lanc.total) + " lançamentos";
    else if (abaAtiva === "contrapartes") texto = $("#corpo-contrapartes > tr").length + " contrapartes";
    else if (abaAtiva === "alertas" && alertas) {
      var subAtiva = $(".smb-sub-aba.ativa").data("sub-aba");
      var cfgAtiva = null;
      SUB_ALERTAS.forEach(function (s) { if (s.tipo === subAtiva) cfgAtiva = s; });
      var qt = cfgAtiva ? (cfgAtiva.dados() || []).length : 0;
      texto = formatarNumero(qt) + " registros";
    }
    $("#total-registros").text(texto);
  }

  // ------------------------------------------------------------- filtros
  // a busca de contas re-renderiza a tabela inteira (com botão de entidade por linha) — o
  // debounce de 250ms é o padrão das outras telas para não reconstruir tudo a cada tecla
  var debounce = window.AnalyserUtils.debounce;
  $("#f-cta-busca").on("input", debounce(renderContas, 250));
  $("#f-cta-pessoa, #f-cta-banco, #f-cta-ordem").on("change", renderContas);

  $("#f-lan-conta, #f-lan-natureza, #f-lan-inicio, #f-lan-fim").on("change", function () {
    carregarLancamentos(1);
  });
  $("#f-lan-busca, #f-lan-valor").on("input", function () {
    // a busca vai ao banco a cada digitação — o atraso evita uma consulta por tecla
    clearTimeout(timerBuscaLanc);
    timerBuscaLanc = setTimeout(function () { carregarLancamentos(1); }, 400);
  });

  $("#f-cp-conta, #f-cp-internas").on("change", carregarContrapartes);
  $("#f-cp-direcao").on("change", function () {
    if (consolidado()) renderContrapartes(); else carregarContrapartes();
  });
  $("#f-cp-busca").on("input", function () {
    clearTimeout(timerBuscaCp);
    timerBuscaCp = setTimeout(carregarContrapartes, 400);
  });

  // ------------------------------------------- estado na URL (EstadoUrl)
  // Cada aba grava os próprios filtros na query ao aplicar; o load restaura (restaurarFiltros).
  // Valor vazio vai como null — EstadoUrl remove a chave, então desfiltrar limpa a URL também.
  function salvarEstadoContas() {
    if (!window.EstadoUrl) return;
    var ordem = $("#f-cta-ordem").val();
    EstadoUrl.salvar({
      ctabusca: $.trim($("#f-cta-busca").val()) || null,
      ctapes: $("#f-cta-pessoa").val() || null,
      ctabanco: $("#f-cta-banco").val() || null,
      ctaordem: ordem && ordem !== "movimento" ? ordem : null
    });
  }

  function salvarEstadoLancamentos() {
    if (!window.EstadoUrl) return;
    EstadoUrl.salvar({
      ltermo: $.trim($("#f-lan-busca").val()) || null,
      lconta: $("#f-lan-conta").val() || null,
      lnat: $("#f-lan-natureza").val() || null,
      linicio: $("#f-lan-inicio").val() || null,
      lfim: $("#f-lan-fim").val() || null,
      lvalor: $("#f-lan-valor").val() || null,
      lpagina: lanc.pagina > 1 ? lanc.pagina : null
    });
  }

  function salvarEstadoContrapartes() {
    if (!window.EstadoUrl) return;
    EstadoUrl.salvar({
      cbusca: $.trim($("#f-cp-busca").val()) || null,
      cconta: $("#f-cp-conta").val() || null,
      cdirecao: $("#f-cp-direcao").val() || null,
      cinternas: $("#f-cp-internas").is(":checked") ? "1" : null
    });
  }

  // chamado após popularSelectContas (as options de conta precisam existir para o .val() pegar)
  function restaurarFiltros() {
    if (estadoSalvo.ctabusca) $("#f-cta-busca").val(estadoSalvo.ctabusca);
    if (estadoSalvo.ctapes) $("#f-cta-pessoa").val(estadoSalvo.ctapes);
    if (estadoSalvo.ctabanco) $("#f-cta-banco").val(estadoSalvo.ctabanco);
    if (estadoSalvo.ctaordem) $("#f-cta-ordem").val(estadoSalvo.ctaordem);
    if (estadoSalvo.ltermo) $("#f-lan-busca").val(estadoSalvo.ltermo);
    if (estadoSalvo.lconta) $("#f-lan-conta").val(estadoSalvo.lconta);
    if (estadoSalvo.lnat) $("#f-lan-natureza").val(estadoSalvo.lnat);
    if (estadoSalvo.linicio) $("#f-lan-inicio").val(estadoSalvo.linicio);
    if (estadoSalvo.lfim) $("#f-lan-fim").val(estadoSalvo.lfim);
    if (estadoSalvo.lvalor) $("#f-lan-valor").val(estadoSalvo.lvalor);
    var lpagina = parseInt(estadoSalvo.lpagina, 10);
    if (lpagina > 1) lanc.pagina = lpagina;
    if (estadoSalvo.cbusca) $("#f-cp-busca").val(estadoSalvo.cbusca);
    if (estadoSalvo.cconta) $("#f-cp-conta").val(estadoSalvo.cconta);
    if (estadoSalvo.cdirecao) $("#f-cp-direcao").val(estadoSalvo.cdirecao);
    if (estadoSalvo.cinternas) $("#f-cp-internas").prop("checked", true);
  }

  // -------------------------------------------------------------- carga
  function carregarAlertas() {
    return $.getJSON("/api/investigacao/simba/" + idExtrato + "/alertas").done(function (a) {
      alertas = a;
      Estados.limpar($("#vazio-alertas").hide());
      renderAlertas();
    }).fail(function () {
      Estados.erro($("#vazio-alertas").show(), {
        mensagem: "Não foi possível calcular os alertas deste extrato.",
        aoTentarDeNovo: function () { $("#vazio-alertas").hide(); carregarAlertas(); }
      });
    });
  }

  function carregarContagens(tp) {
    var d = $.Deferred();
    if (!idInquerito || !window.EntidadesUI) { d.resolve(); return d; }
    EntidadesUI.carregarContagens(idInquerito, tp, function (c) { contagensEnt[tp] = c || {}; d.resolve(); });
    return d;
  }

  /* Exportação dos lançamentos: o conjunto INTEIRO, não a página aberta.

     A aba é paginada no servidor de 200 em 200, e o modo padrão do ExportarExcel lê o DOM — o
     que dava uma planilha com 200 linhas de um extrato de 60 mil, sem nenhum aviso de que o
     resto ficou para trás. É o pior tipo de erro num extrato bancário: o arquivo abre, parece
     completo e está truncado. Aqui a exportação repete a chamada da tela com os MESMOS filtros,
     de página em página, até fechar o total.

     O tamanho de página é o teto da API (listar_lancamentos_simba corta em 1.000), para fazer o
     menor número de idas ao servidor. */
  var EXCEL_LANC_TAMANHO = 1000;

  /* Teto do que a planilha aguenta montar no navegador. Passando disso, o usuário decide: leva
     os primeiros e refina o filtro depois, ou cancela e refina antes. O que não pode é cortar
     calado — que era exatamente o defeito anterior. */
  var EXCEL_LANC_MAX = 50000;

  var COLUNAS_LANC = ["Data", "Titular da conta", "Banco", "Agência", "Conta", "Descrição",
                      "Documento", "Local", "Contraparte", "CPF/CNPJ da contraparte",
                      "Banco da contraparte", "Agência da contraparte", "Conta da contraparte",
                      "Observação da contraparte", "Valor (R$)", "Natureza", "Saldo (R$)",
                      "Natureza do saldo"];

  /* Campo vazio sai vazio, e não com o "—" que a tela usa: na planilha o travessão é texto, e
     texto atrapalha filtro, ordenação e soma. Vale também para valor e data, que voltam "—" de
     `formatarValor`/`formatarData` tanto quando o campo está vazio quanto quando o conteúdo é
     inválido — e nos dois casos a célula certa é a vazia. */
  function celv(v) { return (v === null || v === undefined) ? "" : String(v); }
  function semTravessao(v) { return v === "—" ? "" : v; }

  function linhaLancExcel(l) {
    return [
      semTravessao(formatarData(l.dt_lancamento)),
      celv(l.no_titular), celv(l.no_banco), celv(l.nu_agencia), celv(l.nu_conta),
      celv(l.tx_descricao), celv(l.nu_documento), celv(l.tx_local),
      celv(l.no_od), celv(l.nu_cpf_cnpj_od), celv(l.nu_banco_od), celv(l.nu_agencia_od),
      celv(l.nu_conta_od), celv(l.tx_observacao_od),
      semTravessao(formatarValor(l.vl_operacao)),
      l.cd_natureza === "C" ? "entrada" : (l.cd_natureza === "D" ? "saída" : ""),
      semTravessao(formatarValor(l.vl_saldo)),
      celv(l.cd_natureza_saldo)
    ];
  }

  function dadosLancamentos() {
    var url = "/api/investigacao/simba/" + idExtrato + "/lancamentos";
    var filtros = filtrosLancamentos();
    delete filtros.pagina;
    delete filtros.tamanho;

    var linhas = [];

    function pagina(n, teto) {
      return $.getJSON(url, $.extend({}, filtros, { pagina: n, tamanho: EXCEL_LANC_TAMANHO }))
        .then(function (resp) {
          var recebidas = resp.linhas || [];
          recebidas.forEach(function (l) { linhas.push(linhaLancExcel(l)); });
          // `teto` já veio decidido pela 1ª página; sem linhas novas, para (evita laço infinito
          // se o total do servidor e as linhas devolvidas discordarem).
          if (recebidas.length && linhas.length < teto) return pagina(n + 1, teto);
          return { colunas: COLUNAS_LANC, linhas: linhas.slice(0, teto) };
        });
    }

    // A 1ª página serve para saber o total antes de decidir se pergunta alguma coisa.
    return $.getJSON(url, $.extend({}, filtros, { pagina: 1, tamanho: EXCEL_LANC_TAMANHO }))
      .then(function (resp) {
        var total = Number(resp.total) || 0;
        (resp.linhas || []).forEach(function (l) { linhas.push(linhaLancExcel(l)); });

        if (total <= linhas.length) return { colunas: COLUNAS_LANC, linhas: linhas };

        if (total <= EXCEL_LANC_MAX) return pagina(2, total);

        var msg = "Este filtro tem " + formatarNumero(total) + " lançamentos. A planilha sai com " +
          "os " + formatarNumero(EXCEL_LANC_MAX) + " primeiros (na ordem da tela) — para levar " +
          "tudo, restrinja por conta, período ou valor mínimo e exporte em partes.";
        return Promise.resolve(
          window.Dialogo
            ? Dialogo.confirmar({ titulo: "Exportar parte dos lançamentos", mensagem: msg,
                                  rotuloOk: "Exportar assim mesmo" })
            : confirm(msg)
        ).then(function (ok) {
          if (!ok) return null;   // desistiu: o ExportarExcel sai calado
          return pagina(2, EXCEL_LANC_MAX);
        });
      });
  }

  var opcoesExportacao = null;

  if (!idExtrato) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  var inicializado = false;   // passos que só rodam uma vez (options de select, botão do Excel)

  /* Carga inicial com registro de falhas: antes, um $.when(...).always() desenhava a página
     "limpa" (KPIs zerados, "sem lançamentos datados") mesmo quando a API respondia erro — o
     investigador não tinha como distinguir extrato vazio de consulta quebrada. Cada request
     vigiado que falha vira um Estados.erro com "Tentar de novo" no topo do card. */
  function carregarTudo() {
    $("#smb-estado").empty();
    var falhas = [];
    function vigia(p, nome) { return p.fail(function () { falhas.push(nome); }); }

    return $.when(
      vigia($.getJSON("/api/investigacao/simba/" + idExtrato).done(function (e) { extrato = e; }), "extrato"),
      vigia($.getJSON("/api/investigacao/simba/" + idExtrato + "/contas").done(function (l) { contas = l || []; }), "contas"),
      vigia($.getJSON("/api/investigacao/simba/" + idExtrato + "/serie-mensal").done(function (l) { serie = l || []; }), "série mensal"),
      carregarContagens("simba_conta"), carregarContagens("simba_lancamento"), carregarContagens("simba_contraparte")
    ).always(function () {
      if (falhas.length) {
        Estados.erro($("#smb-estado"), {
          mensagem: "Não foi possível carregar: " + falhas.join(", ") + ". O que aparece abaixo pode estar incompleto.",
          aoTentarDeNovo: carregarTudo
        });
      }
      renderResumo();
      renderGrafico();
      if (!inicializado) {
        inicializado = true;
        popularSelectContas();
        restaurarFiltros();

        // um botão de Excel só, que exporta a aba aberta (o nome do arquivo acompanha a aba)
        if (window.ExportarExcel) {
          opcoesExportacao = {
            nome: "simba_contas",
            dados: function () {
              // Contas e contrapartes cabem inteiras no DOM (filtro no cliente, sem paginação), então
              // ler a tabela devolve o conjunto todo. Lançamentos, não: ver dadosLancamentos.
              if (abaAtiva === "lancamentos") return dadosLancamentos();
              var tabela = document.querySelector("#tab-" + abaAtiva);
              return tabela ? ExportarExcel.lerTabela(tabela) : { colunas: [], linhas: [] };
            }
          };
          ExportarExcel.instalar(opcoesExportacao);
          atualizarBotaoExcel();
        }

        // a gaveta do grafo (?busca=) manda para os lançamentos de uma pessoa — vale mais que o
        // ltermo restaurado da URL, pois é sempre a intenção do último clique
        if (buscaInicial) {
          $("#f-lan-busca").val(buscaInicial);
          lanc.carregado = false;
        }
      }
      renderContas();
      trocarAba(abaAtiva);
    });
  }

  abaAtiva = ROTULO_ABA[abaInicial] ? abaInicial : "contas";
  carregarTudo();
})();
