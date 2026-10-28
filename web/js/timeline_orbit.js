(function () {
  "use strict";

  if (document.documentElement.getAttribute("data-theme") !== "orbit") return;

  var idInquerito = window.AnalyserUtils.paramUrl("inquerito");
  if (!idInquerito) return;

  var ESPACO_NOMES_SVG = "http://www.w3.org/2000/svg";
  var DIA_EM_MS = 86400000;
  var LARGURA_GRAFICO = 1180;
  var ALTURA_GRAFICO_COMBINADO = 160;
  var modoAgrupamento = "combined";
  var destaque = "";
  var panorama = null;
  var carregando = false;
  var recarregarAoConcluir = false;
  var analisePendente = true;
  var janelaTemporal = null;
  var limitesTemporais = null;
  var pontoSelecionado = null;
  var cachePosicoes = {};
  var animacaoMapa = 0;
  var timer = null;
  var timerSincronizacaoLista = null;
  var $checkMapa = $("#tl-orbit-check-mapa");
  var arrastoIniciado = false;
  var arrastando = false;
  var arrastoOrigemX = 0;
  var arrastoOrigemY = 0;
  var arrastoJanela = null;
  var houveArrasto = false;

  var CONJUNTOS = [
    { id: "", rotulo: "Todos" },
    { id: "investigativo", rotulo: "Investigativo" },
    { id: "comunicacao", rotulo: "Comunicação" },
    { id: "localizacao", rotulo: "Localização" },
    { id: "internet", rotulo: "Internet" },
    { id: "financeiro", rotulo: "Financeiro" },
    { id: "aparelho", rotulo: "Aparelho" },
  ];

  var SEGMENTOS = {
    comunicacao: { rotulo: "Comunicação", cor: "#5f8cff" },
    localizacao: { rotulo: "Localização", cor: "#ff4f87" },
    internet: { rotulo: "Internet", cor: "#22c7dd" },
    financeiro: { rotulo: "Financeiro", cor: "#f2b84b" },
    aparelho: { rotulo: "Aparelho", cor: "#a87cf5" },
  };

  var ORDEM_SEGMENTOS = ["comunicacao", "localizacao", "internet", "financeiro", "aparelho"];
  var CATEGORIAS_INVESTIGATIVAS = {
    ligacao: true, mensagem_wpp: true, localizacao: true, trajeto: true,
    compromisso: true, transferencia: true,
  };

  var CATEGORIAS = {
    ligacao: { rotulo: "Ligações", segmento: "comunicacao" },
    mensagem_wpp: { rotulo: "WhatsApp", segmento: "comunicacao" },
    mensagem_sms: { rotulo: "SMS", segmento: "comunicacao" },
    email: { rotulo: "E-mails", segmento: "comunicacao" },
    localizacao: { rotulo: "Localizações", segmento: "localizacao" },
    compromisso: { rotulo: "Compromissos", segmento: "localizacao" },
    trajeto: { rotulo: "Trajetos", segmento: "localizacao" },
    antena: { rotulo: "Antenas", segmento: "localizacao" },
    navegacao: { rotulo: "Navegação", segmento: "internet" },
    busca: { rotulo: "Buscas", segmento: "internet" },
    social: { rotulo: "Redes sociais", segmento: "internet" },
    transferencia: { rotulo: "Transferências", segmento: "financeiro" },
    notificacao: { rotulo: "Notificações", segmento: "aparelho" },
    nota: { rotulo: "Notas", segmento: "aparelho" },
    conexao: { rotulo: "Conexões", segmento: "aparelho" },
  };

  var $painelAnalitico = $("#tl-orbit-analytics");
  var $grafico = $("#tl-orbit-chart");
  var $envoltorioGrafico = $("#tl-orbit-chart-wrap");
  var $tooltip = $("#tl-orbit-tooltip");
  var $vazio = $("#tl-orbit-chart-empty");

  function obterLarguraGrafico() {
    var larguraWrap = $envoltorioGrafico.width();
    return Math.max(760, Math.round(larguraWrap || LARGURA_GRAFICO));
  }

  function categoriaInfo(tipo) {
    if (CATEGORIAS[tipo]) return CATEGORIAS[tipo];
    if (String(tipo || "").indexOf("mensagem_") === 0) {
      return { rotulo: String(tipo).slice(9).replace(/_/g, " "), segmento: "comunicacao" };
    }
    return { rotulo: String(tipo || "Evento").replace(/_/g, " "), segmento: "aparelho" };
  }

  function numero(valor) {
    return Number(valor || 0).toLocaleString("pt-BR");
  }

  function quantidadeComRotulo(valor, singular, plural) {
    var quantidade = Number(valor || 0);
    return numero(quantidade) + " " + (quantidade === 1 ? singular : plural);
  }

  function dataCurta(valor) {
    if (!valor) return "—";
    var partes = String(valor).slice(0, 10).split("-");
    return partes.length === 3 ? partes[2] + "/" + partes[1] + "/" + partes[0] : valor;
  }

  function dataEixo(timestamp) {
    return new Date(timestamp).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "2-digit" }).replace(/ de /g, " ");
  }

  function isoData(timestamp) {
    var d = new Date(timestamp);
    return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0");
  }

  function criarElementoSvg(tag, atributos, texto) {
    var el = document.createElementNS(ESPACO_NOMES_SVG, tag);
    Object.keys(atributos || {}).forEach(function (nome) { el.setAttribute(nome, atributos[nome]); });
    if (texto !== undefined) el.textContent = texto;
    return el;
  }

  function parametros() {
    var p = { analitico: true };
    var extracaoDaUrl = window.AnalyserUtils.paramUrl("extracao");
    var mapa = [
      ["extracao", "#filtro-extracao"], ["busca", "#filtro-busca"],
      ["conjunto", "#filtro-conjunto"], ["categoria", "#filtro-categoria"],
      ["periodo", "#filtro-periodo"],
    ];
    mapa.forEach(function (item) {
      var valor = $(item[1]).val();
      // A lista de extrações é carregada em paralelo. Até ela chegar, preserva o recorte da URL.
      if (item[0] === "extracao" && !valor && $(item[1]).children("option").length <= 1) {
        valor = extracaoDaUrl;
      }
      if (valor) p[item[0]] = valor;
    });
    if ($("#filtro-geo").is(":checked")) p.somente_geo = true;
    return p;
  }

  function mostrarCarregando() {
    carregando = true;
    $painelAnalitico.addClass("loading");
    $("#tl-orbit-kpis strong").text("…");
    $vazio.addClass("hidden");
  }

  function atualizarKpis(resumo) {
    var $cards = $("#tl-orbit-kpis article");
    $cards.eq(0).find("strong").text(numero(resumo.total_eventos));
    $cards.eq(0).attr("title", "Eventos no recorte atual");
    $cards.eq(1).find("strong").text(numero(resumo.total_dias));
    $cards.eq(1).attr("title", "Dias com atividade registrada");
    $cards.eq(2).find("strong").text(numero(resumo.total_aplicativos));
    $cards.eq(2).attr("title", quantidadeComRotulo(resumo.total_categorias, "categoria", "categorias") + " · " + quantidadeComRotulo(resumo.total_extracoes, "extração", "extrações"));
    $cards.eq(3).find("strong").text(dataCurta(resumo.dia_pico));
    $cards.eq(3).find("small").text(quantidadeComRotulo(resumo.eventos_dia_pico, "evento", "eventos"));
    // 5º KPI: em qual faixa do dia o recorte concentra a atividade (qt_* chegam do analítico)
    var periodos = [
      ["Madrugada", resumo.qt_madrugada],
      ["Manhã", resumo.qt_manha],
      ["Tarde", resumo.qt_tarde],
      ["Noite", resumo.qt_noite],
    ];
    var totalPeriodos = periodos.reduce(function (soma, p) { return soma + Number(p[1] || 0); }, 0);
    if (totalPeriodos > 0) {
      var topo = periodos.reduce(function (a, b) { return Number(b[1] || 0) > Number(a[1] || 0) ? b : a; });
      var pct = Math.round(Number(topo[1] || 0) / totalPeriodos * 100);
      $cards.eq(4).find("strong").text(topo[0]);
      $cards.eq(4).find("small").text(pct + "% dos eventos");
      $cards.eq(4).attr("title", periodos.map(function (p) {
        return p[0] + ": " + numero(p[1]) + " (" + Math.round(Number(p[1] || 0) / totalPeriodos * 100) + "%)";
      }).join(" · "));
    } else {
      $cards.eq(4).find("strong").text("—");
      $cards.eq(4).find("small").text("");
      $cards.eq(4).removeAttr("title");
    }
    $("#total-registros").text(numero(resumo.total_eventos) + " eventos");
  }

  function compactarPontos(originais) {
    var prontos = (originais || []).map(function (p) {
      var info = categoriaInfo(p.tp_categoria);
      return {
        id: p.tp_categoria + "|" + String(p.dt_dia).slice(0, 10) + "|" + String(p.no_aplicativo || ""),
        data: String(p.dt_dia).slice(0, 10),
        inicio: String(p.dt_dia).slice(0, 10),
        fim: String(p.dt_dia).slice(0, 10),
        tempo: Date.parse(String(p.dt_dia).slice(0, 10) + "T12:00:00Z"),
        categoria: p.tp_categoria,
        rotulo: info.rotulo,
        segmento: info.segmento,
        aplicativo: p.no_aplicativo || "Sem aplicativo",
        quantidade: Number(p.quantidade || 0),
      };
    }).filter(function (p) { return Number.isFinite(p.tempo); });

    if (prontos.length <= 480) return prontos;

    var tempos = prontos.map(function (p) { return p.tempo; });
    var minimo = Math.min.apply(null, tempos);
    var maximo = Math.max.apply(null, tempos);
    var categorias = new Set(prontos.map(function (p) { return p.categoria; })).size || 1;
    var faixas = Math.max(12, Math.floor(440 / categorias));
    var intervalo = Math.max(DIA_EM_MS, Math.ceil((maximo - minimo + DIA_EM_MS) / faixas));
    var grupos = {};

    prontos.forEach(function (p) {
      var indice = Math.floor((p.tempo - minimo) / intervalo);
      var chave = p.categoria + "|" + indice;
      if (!grupos[chave]) {
        grupos[chave] = Object.assign({}, p, { tempoPeso: 0, aplicativos: {}, quantidade: 0 });
      }
      var g = grupos[chave];
      g.tempoPeso += p.tempo * p.quantidade;
      g.quantidade += p.quantidade;
      g.inicio = p.inicio < g.inicio ? p.inicio : g.inicio;
      g.fim = p.fim > g.fim ? p.fim : g.fim;
      g.aplicativos[p.aplicativo] = (g.aplicativos[p.aplicativo] || 0) + p.quantidade;
    });

    return Object.keys(grupos).map(function (chave) {
      var g = grupos[chave];
      g.id = chave;
      g.tempo = g.tempoPeso / Math.max(1, g.quantidade);
      g.data = isoData(g.tempo);
      g.aplicativo = Object.keys(g.aplicativos).sort(function (a, b) { return g.aplicativos[b] - g.aplicativos[a]; })[0];
      return g;
    });
  }

  function encerrarAnimacaoMapa() {
    if (animacaoMapa) cancelAnimationFrame(animacaoMapa);
    animacaoMapa = 0;
  }

  function limitar(valor, minimo, maximo) {
    return Math.max(minimo, Math.min(maximo, valor));
  }

  function resolverColisoes(nos) {
    var maiorRaio = nos.reduce(function (maior, no) { return Math.max(maior, no.raio); }, 4);
    var tamanhoCelula = maiorRaio * 2 + 3;

    for (var iteracao = 0; iteracao < 2; iteracao++) {
      var grade = {};
      nos.forEach(function (no) {
        var gx = Math.floor(no.x / tamanhoCelula);
        var gy = Math.floor(no.y / tamanhoCelula);
        for (var dxGrade = -1; dxGrade <= 1; dxGrade++) {
          for (var dyGrade = -1; dyGrade <= 1; dyGrade++) {
            var vizinhos = grade[(gx + dxGrade) + ":" + (gy + dyGrade)] || [];
            vizinhos.forEach(function (outro) {
              var dx = no.x - outro.x;
              var dy = no.y - outro.y;
              var distanciaMinima = no.raio + outro.raio + 1.2;
              var distancia = Math.sqrt(dx * dx + dy * dy);
              if (distancia >= distanciaMinima) return;
              if (distancia < .01) { dx = (Math.random() - .5) * .1; dy = (Math.random() - .5) * .1; distancia = Math.sqrt(dx * dx + dy * dy) || .01; }
              var ajuste = (distanciaMinima - distancia) / distancia * .52;
              var moverX = dx * ajuste;
              var moverY = dy * ajuste;
              no.x += moverX;
              no.y += moverY;
              outro.x -= moverX;
              outro.y -= moverY;
            });
          }
        }
        var chave = gx + ":" + gy;
        if (!grade[chave]) grade[chave] = [];
        grade[chave].push(no);
      });
      nos.forEach(function (no) {
        no.x = limitar(no.x, no.xMin + no.raio, no.xMax - no.raio);
        no.y = limitar(no.y, no.yMin + no.raio, no.yMax - no.raio);
      });
    }
  }

  function animarEnxame(nos, rapido) {
    encerrarAnimacaoMapa();
    var grafico = $grafico.get(0);
    var alpha = 1;
    var decaimento = rapido ? 0.84 : 0.945;

    function passo() {
      alpha *= decaimento;
      nos.forEach(function (no) {
        no.vx += (no.alvoX - no.x) * .9 * alpha;
        no.vy += (no.alvoY - no.y) * .3 * alpha;
        no.vx *= .66;
        no.vy *= .66;
        no.x += no.vx;
        no.y += no.vy;
      });
      resolverColisoes(nos);
      nos.forEach(function (no) {
        no.elemento.setAttribute("cx", no.x.toFixed(2));
        no.elemento.setAttribute("cy", no.y.toFixed(2));
        no.ponto.x = no.x;
        no.ponto.y = no.y;
        cachePosicoes[no.ponto.id] = { x: no.x, y: no.y };
      });

      if (alpha > .004 && document.documentElement.contains(grafico) && document.body.classList.contains("tl-orbit-com-mapa")) {
        animacaoMapa = requestAnimationFrame(passo);
      } else {
        animacaoMapa = 0;
        grafico.removeAttribute("data-animando");
      }
    }

    grafico.setAttribute("data-animando", "true");
    animacaoMapa = requestAnimationFrame(passo);
  }

  function prepararRecorteTemporal(todosPontos) {
    var tempos = todosPontos.map(function (ponto) { return ponto.tempo; });
    var inicio = Math.min.apply(null, tempos);
    var fim = Math.max.apply(null, tempos);
    if (inicio === fim) { inicio -= DIA_EM_MS / 2; fim += DIA_EM_MS / 2; }

    limitesTemporais = { inicio: inicio, fim: fim };
    if (!janelaTemporal) janelaTemporal = { inicio: inicio, fim: fim };
    janelaTemporal.inicio = Math.max(inicio, janelaTemporal.inicio);
    janelaTemporal.fim = Math.min(fim, janelaTemporal.fim);
    if (janelaTemporal.inicio >= janelaTemporal.fim) janelaTemporal = { inicio: inicio, fim: fim };

    atualizarZoom();
    return {
      inicio: janelaTemporal.inicio,
      fim: janelaTemporal.fim,
      pontos: todosPontos.filter(function (ponto) {
        return ponto.tempo >= janelaTemporal.inicio && ponto.tempo <= janelaTemporal.fim;
      })
    };
  }

  function configurarGrafico(pontos, recorte) {
    var separado = modoAgrupamento === "segment";
    var margem = { esquerda: separado ? 138 : 58, direita: 34, topo: separado ? 22 : 12, base: separado ? 32 : 24 };
    var segmentos = ORDEM_SEGMENTOS.filter(function (id) {
      return pontos.some(function (ponto) { return ponto.segmento === id; });
    });
    var larguraGrafico = obterLarguraGrafico();
    var altura = separado
      ? Math.max(ALTURA_GRAFICO_COMBINADO, margem.topo + segmentos.length * 64 + margem.base)
      : ALTURA_GRAFICO_COMBINADO;
    return {
      separado: separado,
      largura: larguraGrafico,
      margem: margem,
      segmentos: segmentos,
      altura: altura,
      x: function (tempo) {
        var areaUtil = larguraGrafico - margem.esquerda - margem.direita;
        return margem.esquerda + ((tempo - recorte.inicio) / (recorte.fim - recorte.inicio)) * areaUtil;
      }
    };
  }

  function dimensionarPontos(todosPontos, pontos) {
    var quantidades = todosPontos.map(function (ponto) { return ponto.quantidade; });
    var minimo = Math.min.apply(null, quantidades);
    var maximo = Math.max.apply(null, quantidades);
    pontos.forEach(function (ponto) {
      var proporcao = maximo === minimo ? .48
        : (Math.sqrt(ponto.quantidade) - Math.sqrt(minimo)) / (Math.sqrt(maximo) - Math.sqrt(minimo));
      ponto.raio = 3 + proporcao * 9;
      ponto.intensidade = maximo ? Math.max(3, Math.round(ponto.quantidade / maximo * 100)) : 0;
    });
  }

  function posicionarPontosPorSegmento(grafico, pontos, configuracao) {
    configuracao.segmentos.forEach(function (segmento, indice) {
      var centro = configuracao.margem.topo + indice * 64 + 26;
      var pontosDoSegmento = pontos.filter(function (ponto) { return ponto.segmento === segmento; });
      var total = pontosDoSegmento.reduce(function (soma, ponto) { return soma + ponto.quantidade; }, 0);
      grafico.appendChild(criarElementoSvg("text", { x: configuracao.margem.esquerda - 16, y: centro - 3, "text-anchor": "end", class: "tl-orbit-row-label" }, SEGMENTOS[segmento].rotulo));
      grafico.appendChild(criarElementoSvg("text", { x: configuracao.margem.esquerda - 16, y: centro + 14, "text-anchor": "end", class: "tl-orbit-row-meta" }, numero(total) + " eventos"));
      grafico.appendChild(criarElementoSvg("line", { x1: configuracao.margem.esquerda, x2: configuracao.largura - configuracao.margem.direita, y1: centro + 28, y2: centro + 28, class: "tl-orbit-row-line" }));
      pontosDoSegmento.forEach(function (ponto) {
        Object.assign(ponto, { alvoX: configuracao.x(ponto.tempo), alvoY: centro, yMin: centro - 24, yMax: centro + 24 });
      });
    });
  }

  function posicionarPontos(pontos, configuracao) {
    if (configuracao.separado) {
      posicionarPontosPorSegmento($grafico.get(0), pontos, configuracao);
      return;
    }
    pontos.forEach(function (ponto) {
      Object.assign(ponto, { alvoX: configuracao.x(ponto.tempo), alvoY: 68, yMin: 18, yMax: 118 });
    });
  }

  function desenharEixoTemporal(grafico, configuracao, recorte) {
    var eixoY = configuracao.altura - 18;
    var margem = configuracao.margem;
    grafico.appendChild(criarElementoSvg("line", { x1: margem.esquerda, x2: configuracao.largura - margem.direita, y1: eixoY - 10, y2: eixoY - 10, class: "tl-orbit-axis-line" }));
    for (var indice = 0; indice < 5; indice++) {
      var tempo = recorte.inicio + (recorte.fim - recorte.inicio) * indice / 4;
      var x = configuracao.x(tempo);
      grafico.appendChild(criarElementoSvg("line", { x1: x, x2: x, y1: eixoY - 14, y2: eixoY - 6, class: "tl-orbit-axis-line" }));
      grafico.appendChild(criarElementoSvg("text", { x: x, y: eixoY + 6, "text-anchor": "middle", class: "tl-orbit-axis-label" }, dataEixo(tempo)));
    }
  }

  function criarNoAnimado(grafico, ponto, configuracao) {
    var cor = SEGMENTOS[ponto.segmento] ? SEGMENTOS[ponto.segmento].cor : "#8b93a8";
    var anterior = cachePosicoes[ponto.id];
    var inicialX = anterior ? anterior.x : ponto.alvoX;
    var inicialY = anterior ? anterior.y : ponto.alvoY + (Math.random() - .5) * 56;
    var circulo = criarElementoSvg("circle", {
      cx: inicialX, cy: inicialY, r: ponto.raio, fill: cor,
      class: "tl-orbit-dot", tabindex: "0", "data-segmento": ponto.segmento,
      "data-categoria": ponto.categoria, "data-ponto": ponto.id,
      "aria-label": ponto.rotulo + ", " + numero(ponto.quantidade) + " eventos, " + dataCurta(ponto.inicio)
    });
    circulo.addEventListener("mouseenter", function (evento) { mostrarTooltip(evento, ponto); });
    circulo.addEventListener("mousemove", posicionarTooltip);
    circulo.addEventListener("mouseleave", esconderTooltip);
    circulo.addEventListener("focus", function (evento) { mostrarTooltip(evento, ponto); });
    circulo.addEventListener("blur", esconderTooltip);
    circulo.addEventListener("click", function (evento) {
      if (houveArrasto) {
        evento.stopPropagation();
        evento.preventDefault();
        return;
      }
      selecionarPonto(ponto);
    });
    circulo.addEventListener("keydown", function (evento) {
      if (evento.key !== "Enter" && evento.key !== " ") return;
      evento.preventDefault();
      selecionarPonto(ponto);
    });
    grafico.appendChild(circulo);
    return {
      ponto: ponto, elemento: circulo, raio: ponto.raio,
      alvoX: ponto.alvoX, alvoY: ponto.alvoY, x: inicialX, y: inicialY,
      xMin: configuracao.margem.esquerda, xMax: configuracao.largura - configuracao.margem.direita,
      yMin: ponto.yMin, yMax: ponto.yMax, vx: 0, vy: 0
    };
  }

  function desenhar(rapido) {
    if (!panorama) return;
    encerrarAnimacaoMapa();
    var grafico = $grafico.get(0);
    while (grafico.firstChild) grafico.removeChild(grafico.firstChild);
    esconderTooltip();

    var todosPontos = compactarPontos(panorama.pontos);
    var larguraGrafico = obterLarguraGrafico();
    if (!todosPontos.length) {
      $vazio.removeClass("hidden");
      grafico.setAttribute("viewBox", "0 0 " + larguraGrafico + " " + ALTURA_GRAFICO_COMBINADO);
      return;
    }

    var recorte = prepararRecorteTemporal(todosPontos);
    if (!recorte.pontos.length) {
      $vazio.text("Não há eventos neste intervalo de zoom.").removeClass("hidden");
      grafico.setAttribute("viewBox", "0 0 " + larguraGrafico + " 300");
      return;
    }

    $vazio.addClass("hidden");
    var configuracao = configurarGrafico(recorte.pontos, recorte);
    dimensionarPontos(todosPontos, recorte.pontos);
    grafico.setAttribute("viewBox", "0 0 " + configuracao.largura + " " + configuracao.altura);
    grafico.setAttribute("height", configuracao.altura);
    grafico.appendChild(criarElementoSvg("title", { id: "tl-orbit-chart-title" }, "Distribuição temporal dos eventos"));
    posicionarPontos(recorte.pontos, configuracao);
    desenharEixoTemporal(grafico, configuracao, recorte);
    var nosAnimados = recorte.pontos.map(function (ponto) { return criarNoAnimado(grafico, ponto, configuracao); });
    aplicarDestaque();
    animarEnxame(nosAnimados, rapido);
  }

  function mostrarTooltip(evento, ponto) {
    if (arrastando) return;
    $tooltip.empty();
    $("<strong>").text(ponto.rotulo).appendTo($tooltip);
    var periodo = ponto.inicio === ponto.fim ? dataCurta(ponto.inicio) : dataCurta(ponto.inicio) + " a " + dataCurta(ponto.fim);
    $("<span>").text(periodo + " · " + numero(ponto.quantidade) + " eventos").appendTo($tooltip);
    $("<span>").text("Aplicação predominante: " + ponto.aplicativo).appendTo($tooltip);
    $tooltip.removeClass("hidden");
    posicionarTooltip(evento);
  }

  function posicionarTooltip(evento) {
    if (arrastando) {
      esconderTooltip();
      return;
    }
    var rect = $envoltorioGrafico.get(0).getBoundingClientRect();
    var origem = evento.touches ? evento.touches[0] : evento;
    var clientX = origem.clientX;
    var clientY = origem.clientY;
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) {
      var alvo = evento.target.getBoundingClientRect();
      clientX = alvo.left + alvo.width / 2;
      clientY = alvo.top;
    }
    var esquerda = Math.min(rect.width - 200, Math.max(8, clientX - rect.left + 14));
    var topo = Math.max(8, clientY - rect.top - 76);
    $tooltip.css({ left: esquerda + "px", top: topo + "px" });
  }

  function esconderTooltip() { $tooltip.addClass("hidden"); }

  function pontoNoDestaque(categoria, segmento) {
    if (!destaque) return true;
    if (destaque === "investigativo") return !!CATEGORIAS_INVESTIGATIVAS[categoria];
    return segmento === destaque;
  }

  function aplicarDestaque() {
    $("#tl-orbit-chart .tl-orbit-dot").each(function () {
      var $ponto = $(this);
      var corresponde = pontoNoDestaque($ponto.attr("data-categoria"), $ponto.attr("data-segmento"));
      $ponto.toggleClass("dimmed", !corresponde);
      $ponto.toggleClass("selected", !!pontoSelecionado && $ponto.attr("data-ponto") === pontoSelecionado.id);
    });
  }

  function atualizarZoom() {
    if (!limitesTemporais || !janelaTemporal) return;
    var tolerancia = 3600000;
    var completo = Math.abs(janelaTemporal.inicio - limitesTemporais.inicio) < tolerancia &&
      Math.abs(janelaTemporal.fim - limitesTemporais.fim) < tolerancia;
    $("#tl-orbit-zoom-range").text(completo ? "Período completo" : dataCurta(isoData(janelaTemporal.inicio)) + " – " + dataCurta(isoData(janelaTemporal.fim)));
    $(".tl-orbit-zoom [data-zoom='out'], .tl-orbit-zoom [data-zoom='reset']").prop("disabled", completo);
    $(".tl-orbit-zoom [data-zoom='in']").prop("disabled", janelaTemporal.fim - janelaTemporal.inicio <= DIA_EM_MS + tolerancia);
  }

  function sincronizarListaComPeriodo() {
    clearTimeout(timerSincronizacaoLista);
    timerSincronizacaoLista = setTimeout(function () {
      if (!janelaTemporal || !limitesTemporais) return;
      var tolerancia = 3600000;
      var completo = Math.abs(janelaTemporal.inicio - limitesTemporais.inicio) < tolerancia &&
        Math.abs(janelaTemporal.fim - limitesTemporais.fim) < tolerancia;
      if (completo) {
        $("#filtro-data-de").val("");
        $("#filtro-data-ate").val("");
      } else {
        $("#filtro-data-de").val(isoData(janelaTemporal.inicio));
        $("#filtro-data-ate").val(isoData(janelaTemporal.fim));
      }
      if (window.TimelineLista) {
        window.TimelineLista.carregar(1);
      }
    }, 250);
  }

  function controlarZoom(acao) {
    if (!limitesTemporais || !janelaTemporal) return;
    var inicioTotal = limitesTemporais.inicio;
    var fimTotal = limitesTemporais.fim;
    var amplitudeTotal = fimTotal - inicioTotal;
    var amplitude = janelaTemporal.fim - janelaTemporal.inicio;
    var fimFixo = janelaTemporal.fim;

    if (acao === "reset") {
      janelaTemporal = { inicio: inicioTotal, fim: fimTotal };
      cachePosicoes = {};
      desenhar();
      sincronizarListaComPeriodo();
      return;
    }

    if (acao === "in") {
      amplitude = Math.max(DIA_EM_MS, amplitude / 2);
      janelaTemporal = { inicio: fimFixo - amplitude, fim: fimFixo };
    } else if (acao === "out") {
      amplitude = Math.min(amplitudeTotal, amplitude * 2);
      janelaTemporal = { inicio: fimFixo - amplitude, fim: fimFixo };
    }

    janelaTemporal.inicio = Math.max(inicioTotal, janelaTemporal.inicio);
    janelaTemporal.fim = Math.min(fimTotal, janelaTemporal.fim);
    desenhar(true);
    sincronizarListaComPeriodo();
  }

  function zoomPeloPonto(clientX, fator) {
    if (!limitesTemporais || !janelaTemporal) return;
    var svg = $grafico.get(0);
    if (!svg) return;

    var larguraGrafico = obterLarguraGrafico();
    var ctm = svg.getScreenCTM();
    var svgX;

    if (ctm) {
      var pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = svg.getBoundingClientRect().top + 20;
      var svgPt = pt.matrixTransform(ctm.inverse());
      svgX = svgPt.x;
    } else {
      var rect = svg.getBoundingClientRect();
      svgX = (clientX - rect.left) * (larguraGrafico / (rect.width || 1));
    }

    var separado = modoAgrupamento === "segment";
    var margemEsq = separado ? 138 : 58;
    var margemDir = 34;
    var areaUtil = larguraGrafico - margemEsq - margemDir;
    var ratio = Math.max(0, Math.min(1, (svgX - margemEsq) / Math.max(1, areaUtil)));

    var duracaoAtual = janelaTemporal.fim - janelaTemporal.inicio;
    var tempoCursor = janelaTemporal.inicio + ratio * duracaoAtual;
    var duracaoTotal = limitesTemporais.fim - limitesTemporais.inicio;
    var novaDuracao = Math.max(DIA_EM_MS, Math.min(duracaoTotal, duracaoAtual * fator));

    var novoInicio = tempoCursor - ratio * novaDuracao;
    var novoFim = novoInicio + novaDuracao;

    if (novoInicio < limitesTemporais.inicio) {
      novoInicio = limitesTemporais.inicio;
      novoFim = Math.min(limitesTemporais.fim, novoInicio + novaDuracao);
    } else if (novoFim > limitesTemporais.fim) {
      novoFim = limitesTemporais.fim;
      novoInicio = Math.max(limitesTemporais.inicio, novoFim - novaDuracao);
    }

    var completo = Math.abs(novaDuracao - duracaoTotal) < 3600000;
    if (completo) {
      janelaTemporal = { inicio: limitesTemporais.inicio, fim: limitesTemporais.fim };
      cachePosicoes = {};
      desenhar();
    } else {
      janelaTemporal = { inicio: Math.round(novoInicio), fim: Math.round(novoFim) };
      desenhar(true);
    }
    sincronizarListaComPeriodo();
  }

  function selecionarPonto(ponto) {
    pontoSelecionado = ponto;
    esconderTooltip();
    abrirPainel(ponto);

    // Reflete o período e eventos da bolha clicada diretamente na lista exibida abaixo
    $("#filtro-data-de").val(ponto.inicio);
    $("#filtro-data-ate").val(ponto.fim);
    if (window.TimelineLista) {
      window.TimelineLista.carregar(1);
    }
  }

  function abrirPainel(ponto) {
    pontoSelecionado = ponto;
    esconderTooltip();
    var segmento = SEGMENTOS[ponto.segmento] || { rotulo: "Evento" };
    var periodo = ponto.inicio === ponto.fim ? dataCurta(ponto.inicio) : dataCurta(ponto.inicio) + " a " + dataCurta(ponto.fim);
    var participacao = panorama && panorama.resumo && Number(panorama.resumo.total_eventos)
      ? ponto.quantidade / Number(panorama.resumo.total_eventos) * 100 : 0;
    var textoParticipacao = participacao > 0 && participacao < .1 ? "< 0,1%" : participacao.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%";

    $("#tl-orbit-detail-segment").text(String(segmento.rotulo).toUpperCase());
    $("#tl-orbit-detail-title").text(ponto.rotulo);
    $("#tl-orbit-detail-total").text(numero(ponto.quantidade));
    $("#tl-orbit-detail-period").text(periodo);
    $("#tl-orbit-detail-app").text(ponto.aplicativo);
    $("#tl-orbit-detail-share").text(textoParticipacao);
    $("#tl-orbit-detail-bar").css("width", ponto.intensidade + "%");
    $("#tl-orbit-detail-panel").removeClass("hidden").attr("aria-hidden", "false");
    document.body.classList.add("tl-orbit-panel-open");
    aplicarDestaque();
    $("#tl-orbit-detail-close").trigger("focus");
  }

  function fecharPainel() {
    if (!pontoSelecionado && $("#tl-orbit-detail-panel").hasClass("hidden")) return;
    pontoSelecionado = null;
    $("#tl-orbit-panel-scrim, #tl-orbit-detail-panel").addClass("hidden").attr("aria-hidden", "true");
    document.body.classList.remove("tl-orbit-panel-open");
    aplicarDestaque();
  }

  function alternarMapaVisual(ativo) {
    document.body.classList.toggle("tl-orbit-com-mapa", ativo);
    $checkMapa.prop("checked", ativo);
    if (ativo) {
      if (!panorama || analisePendente) carregar();
      else desenhar();
    } else {
      encerrarAnimacaoMapa();
      fecharPainel();
    }
  }

  function selecionarVisualizacao(view) {
    var lista = view === "lista";
    document.body.classList.toggle("tl-orbit-lista", true);
    alternarMapaVisual(!lista);
  }

  function abrirDia(ponto) {
    if (ponto) selecionarPonto(ponto);
    fecharPainel();
    selecionarVisualizacao("lista");
    var $lista = $("#tl-list-view");
    if ($lista.length) {
      window.scrollTo({ top: $lista.offset().top - 80, behavior: "smooth" });
    }
  }

  function atualizarSegmentos() {
    var $destino = $("#tl-orbit-segments").empty();
    $("<span>").addClass("tl-orbit-segment-label").text("Destaque").appendTo($destino);
    CONJUNTOS.forEach(function (item) {
      $("<button>", { type: "button", class: "tl-orbit-segment", text: item.rotulo })
        .toggleClass("active", item.id === destaque)
        .attr("aria-pressed", item.id === destaque ? "true" : "false")
        .on("click", function () {
          destaque = item.id;
          atualizarSegmentos();
          aplicarDestaque();
        })
        .appendTo($destino);
    });
  }

  function carregar() {
    if (carregando) { recarregarAoConcluir = true; return; }
    recarregarAoConcluir = false;
    mostrarCarregando();
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/timeline", parametros())
      .done(function (resposta) {
        if (!resposta || !resposta.resumo || !Array.isArray(resposta.pontos)) {
          panorama = null;
          $vazio.text("A visualização aguarda a atualização do serviço da API.").removeClass("hidden");
          return;
        }
        panorama = resposta;
        analisePendente = false;
        janelaTemporal = null;
        fecharPainel();
        atualizarKpis(resposta.resumo || {});
        // o servidor diz de onde aonde vai o recorte (data_inicial/data_final): segura as datas
        // da tela nos limites reais dos dados e diz isso na legenda do gráfico
        var resumo = resposta.resumo || {};
        if (resumo.data_inicial && resumo.data_final) {
          var inicio = String(resumo.data_inicial).slice(0, 10);
          var fim = String(resumo.data_final).slice(0, 10);
          $("#filtro-data-de").attr("min", inicio);
          $("#filtro-data-ate").attr("max", fim);
          $("#tl-orbit-chart-desc").text("O recorte cobre " + dataCurta(inicio) + " a " + dataCurta(fim) +
            ". Pontos maiores indicam mais eventos. Passe o mouse para ver o resumo e clique para abrir os detalhes.");
        }
        desenhar();
      })
      .fail(function () {
        panorama = { resumo: {}, pontos: [] };
        $vazio.text("Não foi possível carregar o panorama analítico.").removeClass("hidden");
      })
      .always(function () {
        carregando = false;
        $painelAnalitico.removeClass("loading");
        if (recarregarAoConcluir) carregar();
      });
  }

  $checkMapa.on("change", function () { alternarMapaVisual($(this).is(":checked")); });

  $envoltorioGrafico.on("wheel", function (evento) {
    if (!limitesTemporais || !janelaTemporal) return;
    evento.preventDefault();
    var e = evento.originalEvent;
    var fator = e.deltaY < 0 ? 0.8 : 1.25;
    zoomPeloPonto(e.clientX, fator);
  });

  $envoltorioGrafico.on("mousedown", function (evento) {
    if (evento.button !== 0) return;
    if (!limitesTemporais || !janelaTemporal) return;
    arrastoIniciado = true;
    arrastando = false;
    arrastoOrigemX = evento.clientX;
    arrastoJanela = { inicio: janelaTemporal.inicio, fim: janelaTemporal.fim };
  });

  // Pan horizontal: o mesmo cálculo serve ao mouse e ao toque de um dedo
  function deslocarPan(clientX, cancelarPadrao) {
    if (!arrastoIniciado || !arrastoJanela || !limitesTemporais) return;
    var deltaX = clientX - arrastoOrigemX;

    if (!arrastando) {
      if (Math.abs(deltaX) > 4) {
        arrastando = true;
        houveArrasto = true;
        $envoltorioGrafico.addClass("arrastando");
        esconderTooltip();
      } else {
        return;
      }
    }

    if (cancelarPadrao) cancelarPadrao();

    var larguraGrafico = obterLarguraGrafico();
    var separado = modoAgrupamento === "segment";
    var margemEsq = separado ? 138 : 58;
    var margemDir = 34;
    var areaUtil = Math.max(1, larguraGrafico - margemEsq - margemDir);

    var rect = $grafico.get(0) ? $grafico.get(0).getBoundingClientRect() : null;
    var ratioTelaParaSvg = (rect && rect.width > 0) ? (larguraGrafico / rect.width) : 1;
    var deltaSvgX = deltaX * ratioTelaParaSvg;

    var duracao = arrastoJanela.fim - arrastoJanela.inicio;
    var duracaoTotal = limitesTemporais.fim - limitesTemporais.inicio;
    if (duracao >= duracaoTotal) return;

    var deltaTempo = (deltaSvgX / areaUtil) * duracao;
    var novoInicio = Math.round(arrastoJanela.inicio - deltaTempo);
    var novoFim = Math.round(arrastoJanela.fim - deltaTempo);

    if (novoInicio < limitesTemporais.inicio) {
      novoInicio = limitesTemporais.inicio;
      novoFim = novoInicio + duracao;
    } else if (novoFim > limitesTemporais.fim) {
      novoFim = limitesTemporais.fim;
      novoInicio = novoFim - duracao;
    }

    janelaTemporal = { inicio: novoInicio, fim: novoFim };
    desenhar(true);
    sincronizarListaComPeriodo();
  }

  function encerrarArrasto() {
    if (!arrastoIniciado) return;
    arrastoIniciado = false;
    arrastoJanela = null;
    if (arrastando) {
      arrastando = false;
      $envoltorioGrafico.removeClass("arrastando");
      setTimeout(function () {
        houveArrasto = false;
      }, 120);
    }
  }

  $(document).on("mousemove.orbitPan", function (evento) {
    deslocarPan(evento.clientX, function () { evento.preventDefault(); });
  });

  $(document).on("mouseup.orbitPan", encerrarArrasto);

  // ---------- toque: pan de um dedo e pinch-zoom de dois ----------
  // O CSS põe touch-action: pan-y no contêiner: rolagem vertical segue no navegador, horizontal
  // e pinça são nossos. Sem isso o gráfico seria impossível de mexer no celular.
  var pinca = null;

  function distanciaTouch(t) {
    var dx = t[0].clientX - t[1].clientX;
    var dy = t[0].clientY - t[1].clientY;
    return Math.sqrt(dx * dx + dy * dy) || 1;
  }

  $envoltorioGrafico.on("touchstart", function (evento) {
    if (!limitesTemporais || !janelaTemporal) return;
    var toques = evento.originalEvent.touches;
    if (toques.length === 1) {
      arrastoIniciado = true;
      arrastando = false;
      arrastoOrigemX = toques[0].clientX;
      arrastoOrigemY = toques[0].clientY;
      arrastoJanela = { inicio: janelaTemporal.inicio, fim: janelaTemporal.fim };
      pinca = null;
    } else if (toques.length === 2) {
      arrastoIniciado = false;
      arrastoJanela = null;
      pinca = {
        distancia: distanciaTouch(toques),
        meio: (toques[0].clientX + toques[1].clientX) / 2,
      };
    }
  });

  $envoltorioGrafico.on("touchmove", function (evento) {
    var toques = evento.originalEvent.touches;
    if (pinca && toques.length === 2) {
      evento.preventDefault();
      var nova = distanciaTouch(toques);
      if (Math.abs(nova - pinca.distancia) > 6) {
        // afastar os dedos (nova > antiga) = ampliar: fator < 1 encurta a janela
        zoomPeloPonto(pinca.meio, Math.max(0.5, Math.min(2, pinca.distancia / nova)));
        pinca.distancia = nova;
      }
      return;
    }
    if (arrastoIniciado && toques.length === 1) {
      var deltaX = Math.abs(toques[0].clientX - arrastoOrigemX);
      var dy = Math.abs(toques[0].clientY - (arrastoOrigemY || toques[0].clientY));
      // arraste claramente horizontal: toma o gesto para o pan (o navegador cuida do vertical)
      if (arrastando || deltaX > dy) {
        evento.preventDefault();
        deslocarPan(toques[0].clientX);
      }
    }
  });

  $envoltorioGrafico.on("touchend touchcancel", function () {
    pinca = null;
    encerrarArrasto();
  });

  $(window).on("blur.orbitPan", function () {
    if (arrastoIniciado) {
      arrastoIniciado = false;
      arrastando = false;
      arrastoJanela = null;
      $envoltorioGrafico.removeClass("arrastando");
      houveArrasto = false;
    }
  });

  $(".tl-orbit-layout-toggle button").on("click", function () {
    modoAgrupamento = $(this).data("layout");
    $(".tl-orbit-layout-toggle button").each(function () {
      var ativo = $(this).data("layout") === modoAgrupamento;
      $(this).toggleClass("active", ativo).attr("aria-pressed", ativo ? "true" : "false");
    });
    desenhar();
  });
  $(".tl-orbit-zoom button").on("click", function () { controlarZoom($(this).data("zoom")); });
  $("#tl-orbit-clear-filters").on("click", function () {
    $("#filtro-busca, #filtro-conjunto, #filtro-categoria, #filtro-extracao, #filtro-periodo, #filtro-data-de, #filtro-data-ate").val("");
    $("#filtro-geo").prop("checked", false);
    destaque = "";
    janelaTemporal = null;
    atualizarSegmentos();
    fecharPainel();
    $("#filtro-conjunto").trigger("change");
  });
  $("#tl-orbit-detail-close, #tl-orbit-panel-scrim").on("click", fecharPainel);
  $("#tl-orbit-detail-open-list").on("click", function () {
    if (pontoSelecionado) abrirDia(pontoSelecionado);
    else fecharPainel();
  });
  $(document).on("keydown.timelineOrbit", function (evento) { if (evento.key === "Escape") fecharPainel(); });

  // Setas ← / → percorrem os nós do gráfico a partir do nó focado: sem isso o Tab percorre
  // centenas de bolhas na ordem do DOM, que não é a ordem temporal
  $grafico.on("keydown", function (evento) {
    if (evento.key !== "ArrowRight" && evento.key !== "ArrowLeft") return;
    var alvo = evento.target;
    if (!alvo || !alvo.classList || !alvo.classList.contains("tl-orbit-dot")) return;
    evento.preventDefault();
    var nos = $grafico.find(".tl-orbit-dot").toArray();
    nos.sort(function (a, b) {
      return (parseFloat(a.getAttribute("cx")) || 0) - (parseFloat(b.getAttribute("cx")) || 0);
    });
    var indice = nos.indexOf(alvo);
    var proximo = nos[indice + (evento.key === "ArrowRight" ? 1 : -1)];
    if (proximo) proximo.focus();
  });

  $("#filtro-conjunto, #filtro-categoria, #filtro-extracao, #filtro-periodo, #filtro-geo").on("change.timelineOrbit", function () {
    analisePendente = true;
    janelaTemporal = null;
    cachePosicoes = {};
    fecharPainel();
    clearTimeout(timer);
    if (document.body.classList.contains("tl-orbit-com-mapa")) timer = setTimeout(carregar, 60);
  });

  $("#filtro-data-de, #filtro-data-ate").on("change.timelineOrbit", function () {
    var de = $("#filtro-data-de").val();
    var ate = $("#filtro-data-ate").val();
    if (!limitesTemporais) return;
    if (!de && !ate) {
      janelaTemporal = { inicio: limitesTemporais.inicio, fim: limitesTemporais.fim };
      cachePosicoes = {};
      desenhar();
    } else {
      var tInicio = de ? Date.parse(de + "T00:00:00Z") : limitesTemporais.inicio;
      var tFim = ate ? Date.parse(ate + "T23:59:59Z") : limitesTemporais.fim;
      if (Number.isFinite(tInicio) && Number.isFinite(tFim) && tInicio < tFim) {
        janelaTemporal = { inicio: Math.max(limitesTemporais.inicio, tInicio), fim: Math.min(limitesTemporais.fim, tFim) };
        desenhar(true);
      }
    }
  });
  $("#filtro-busca").on("input.timelineOrbit", function () {
    analisePendente = true;
    janelaTemporal = null;
    fecharPainel();
    clearTimeout(timer);
    if (document.body.classList.contains("tl-orbit-com-mapa")) timer = setTimeout(carregar, 450);
  });
  $(window).on("resize.timelineOrbit", function () {
    clearTimeout(timer);
    timer = setTimeout(desenhar, 120);
  });

  atualizarSegmentos();
  selecionarVisualizacao("lista");
})();
