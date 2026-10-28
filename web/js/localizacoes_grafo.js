/* ============================================================================
   Aba "Grafo" das localizações.

   As 30 mil e poucas localizações de uma extração não cabem na tela como pontos: a maioria cai em
   poucas quadras, e desenhar tudo vira uma mancha. Aqui elas são agregadas no servidor em células de
   grade — cada célula com registro vira um nó, dimensionado pelo volume — e ligadas por arestas de
   deslocamento (registros consecutivos no tempo que caíram em células diferentes).

   O nível da grade acompanha o zoom do mapa: quanto mais perto, menor a célula, e um nó grande se
   abre nos vários lugares que o compunham. É esse par (grade + recorte visível) que mantém o número
   de nós na tela estável por mais que o volume total cresça.
   ============================================================================ */
(function () {
  "use strict";

  var $visaoTabelaBtn = $("#visao-tabela-btn");
  var $visaoGrafoBtn = $("#visao-grafo-btn");
  var $visaoTabela = $("#visao-tabela");
  var $visaoGrafo = $("#visao-grafo");
  var $visaoTrajetos = $("#visao-trajetos");
  var $visaoTrajetosBtn = $("#visao-trajetos-btn");
  var $nivelInfo = $("#grafo-nivel");
  var $status = $("#grafo-status");
  var $minViagens = $("#grafo-min-viagens");
  var $enquadrar = $("#grafo-enquadrar");
  var $painel = $("#loc-painel");
  var $legenda = $("#loc-legenda");

  // o Leaflet não é pressuposto aqui: esta página só carrega o vendor na primeira abertura da aba
  // Grafo (carregarLeaflet, abaixo) — quem vai direto à Tabela não baixa nem interpreta o mapa.
  if (!$visaoGrafoBtn.length || !window.LocalizacoesFiltros) return;

  var idInquerito = LocalizacoesFiltros.idInquerito;

  // ---------- Leaflet sob demanda ----------
  // CSS + JS entram no documento uma única vez, no primeiro pedido do mapa. O CSS é um <link>
  // normal; o <script> é criado na mão (onload/onerror confiáveis) e a fila de callbacks roda na
  // ordem de chegada quando ele terminar.
  var leafletPromessa = null;
  function carregarLeaflet(depois) {
    if (window.L) { depois(); return; }
    if (leafletPromessa) { leafletPromessa.done(depois); return; }

    var d = $.Deferred();
    leafletPromessa = d.promise();

    var css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "vendor/leaflet.css";
    document.head.appendChild(css);

    var s = document.createElement("script");
    s.src = "vendor/leaflet.js";
    s.onload = function () { d.resolve(); };
    s.onerror = function () {
      leafletPromessa = null;                       // permite tentar de novo no próximo clique
      $status.text("Não foi possível carregar o mapa. Abra a aba Grafo de novo para tentar.");
    };
    document.body.appendChild(s);

    leafletPromessa.done(depois);
  }

  // Zoom do Leaflet -> nível da grade no servidor (índice de _NIVEIS_GRAFO_LOCALIZACAO). As faixas
  // foram escolhidas para caber ~30–50 células na largura da tela em cada zoom: menos que isso o mapa
  // fica vazio, mais que isso vira poeira de nós.
  var NIVEL_POR_ZOOM = [
    { zoom: 6, nivel: 0, rotulo: "país", escala: "~200 km" },
    { zoom: 8, nivel: 1, rotulo: "região", escala: "~55 km" },
    { zoom: 10, nivel: 2, rotulo: "cidade", escala: "~11 km" },
    { zoom: 12, nivel: 3, rotulo: "bairro", escala: "~2 km" },
    { zoom: 14, nivel: 4, rotulo: "quadra", escala: "~500 m" },
    { zoom: 16, nivel: 5, rotulo: "rua", escala: "~110 m" },
    { zoom: 99, nivel: 6, rotulo: "endereço", escala: "~20 m" },
  ];

  function nivelDoZoom(zoom) {
    for (var i = 0; i < NIVEL_POR_ZOOM.length; i++) {
      if (zoom < NIVEL_POR_ZOOM[i].zoom) return NIVEL_POR_ZOOM[i];
    }
    return NIVEL_POR_ZOOM[NIVEL_POR_ZOOM.length - 1];
  }

  var CORES_APP = {
    "Snapchat": "#eab308",
    "Google Photos": "#4285f4",
    "Google Location History": "#34a853",
    "Gmail": "#ea4335",
    "WhatsApp": "#25d366",
    "WhatsApp Business": "#128c7e",
    "Google Maps": "#a855f7",
    "Waze": "#22d3ee",
    "Facebook": "#6366f1",
    "Native WiFi": "#f97316",
  };
  var COR_SEM_APP = "#8696a0";

  // rampa perceptualmente ordenada (escuro -> claro) para a recência: lugares antigos ficam apagados
  // e os recentes saltam, que é o que interessa num exame
  var RAMPA_RECENCIA = ["#2b1a4d", "#6a2c70", "#b83b5e", "#f08a5d", "#ffd460"];

  function hexParaRgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function misturar(hexA, hexB, fracao) {
    var a = hexParaRgb(hexA), b = hexParaRgb(hexB);
    var c = a.map(function (v, i) { return Math.round(v + (b[i] - v) * fracao); });
    return "rgb(" + c.join(",") + ")";
  }

  function corRampa(t) {
    var n = RAMPA_RECENCIA.length - 1;
    var i = Math.max(0, Math.min(n - 1, Math.floor(t * n)));
    return misturar(RAMPA_RECENCIA[i], RAMPA_RECENCIA[i + 1], t * n - i);
  }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarData(iso) { return AnalyserUtils.formatarDataSimples(iso); }

  function numero(n) { return AnalyserUtils.formatarNumero(n || 0); }

  // rótulo curto dentro do círculo: 13495 não cabe, "13,5k" cabe
  function numeroCompacto(n) {
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(".", ",") + "k";
    return String(n);
  }

  function duracao(minutos) {
    if (minutos === null || minutos === undefined) return "—";
    if (minutos < 60) return minutos + " min";
    var h = Math.floor(minutos / 60);
    return h + "h" + (minutos % 60 ? String(minutos % 60).padStart(2, "0") : "");
  }

  var ANCORAS = {
    residencia: { selo: "🏠", rotulo: "Provável residência" },
    trabalho: { selo: "💼", rotulo: "Provável trabalho" },
  };

  // ---------------------------------------------------------------- estado

  var mapa = null;
  var camadaArestas = null;
  var camadaNos = null;
  var dados = { nos: [], arestas: [], nivel: 0 };
  var porId = {};
  var selecionado = null;
  var marcadores = {};     // id_no -> L.marker: permite destacar a seleção sem redesenhar o mapa
  var linhasArestas = [];  // [{ linha, origem, destino }]: idem, para as arestas
  var modoCor = "recencia";
  var enquadrado = false;      // o primeiro carregamento ajusta o mapa à área de interesse
  var precisaRecarregar = false;
  var timerMover = null;
  var requisicao = null;

  // ---------------------------------------------------------------- mapa

  function criarMapa() {
    mapa = L.map("loc-mapa", { zoomControl: true, worldCopyJump: true }).setView([-15.8, -47.9], 4);

    // tiles pelo proxy local (nginx /osm-tiles/ → OSM): o acesso direto do navegador era
    // bloqueado pelo OSM ("Access blocked" — política de uso dos tiles); ver nginx/default.conf
    L.tileLayer("/osm-tiles/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
    }).addTo(mapa);

    camadaArestas = L.layerGroup().addTo(mapa);
    camadaNos = L.layerGroup().addTo(mapa);

    // Os polígonos importados de shapefile (js/localizacoes_camadas.js) desenham no MESMO mapa e
    // entram antes dos nós, para ficarem por baixo — são fundo de leitura, não o dado.
    if (window.LocalizacoesCamadas) LocalizacoesCamadas.usarMapa(mapa);

    // As antenas da operadora (js/localizacoes_erbs.js) desenham no mesmo mapa, num pane entre os
    // polígonos e os lugares: são contexto do que o aparelho registrou, então ficam por trás dos
    // pontos da extração — e são do inquérito, não desta extração.
    if (window.LocalizacoesErbs) LocalizacoesErbs.usarMapa(mapa);

    mapa.on("moveend", function () {
      clearTimeout(timerMover);
      timerMover = setTimeout(carregar, 250);   // pan/zoom contínuo não deve disparar uma busca por quadro
    });
  }

  // A seta do sentido do deslocamento fica no meio da linha (marker-mid), não na ponta: na ponta ela
  // sumiria embaixo do círculo do destino. Precisa de um <defs> dentro do SVG que o Leaflet cria para
  // as camadas vetoriais — inserido uma vez só, na primeira renderização de aresta.
  function garantirDefs() {
    var svg = document.querySelector("#loc-mapa .leaflet-overlay-pane svg");
    if (!svg || svg.querySelector("#loc-seta")) return;
    var defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML =
      '<marker id="loc-seta" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto">' +
      '<path d="M0,0 L10,5 L0,10 z" class="loc-aresta-seta"/></marker>';
    svg.insertBefore(defs, svg.firstChild);
  }

  // ---------------------------------------------------------------- carregamento

  function carregar() {
    if ($visaoGrafo.hasClass("hidden")) { precisaRecarregar = true; return; }

    var faixa = nivelDoZoom(mapa.getZoom());
    var limites = mapa.getBounds();
    var params = LocalizacoesFiltros.params();
    params.nivel = faixa.nivel;
    // pede uma margem além da tela para o pan curto não abrir buraco enquanto a busca não volta
    var margem = limites.pad(0.25);
    params.lat_min = margem.getSouth();
    params.lat_max = margem.getNorth();
    params.lon_min = margem.getWest();
    params.lon_max = margem.getEast();

    if (requisicao) requisicao.abort();
    $status.text("Agregando…");

    requisicao = $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/localizacoes/grafo", params);
    requisicao.done(function (resp) {
      requisicao = null;
      dados = resp;
      if (!enquadrado && resp.foco) {
        enquadrado = true;
        mapa.fitBounds([[resp.foco.lat_min, resp.foco.lon_min], [resp.foco.lat_max, resp.foco.lon_max]], { padding: [30, 30] });
        return;   // o fitBounds dispara moveend e recarrega já no nível certo do novo enquadramento
      }
      enquadrado = true;
      render(faixa);
    }).fail(function (xhr, tipoErro) {
      if (tipoErro === "abort") return;    // substituída por uma busca mais nova, não é falha
      requisicao = null;
      $status.text("Não foi possível montar o grafo.");
    });
  }

  // ---------------------------------------------------------------- renderização

  function render(faixa) {
    camadaArestas.clearLayers();
    camadaNos.clearLayers();
    porId = {};
    marcadores = {};
    linhasArestas = [];

    var nos = dados.nos || [];
    nos.forEach(function (no) { porId[no.id_no] = no; });

    $nivelInfo.html(
      "Detalhe: <strong>" + faixa.rotulo + "</strong> (" + faixa.escala + ") · " +
      numero(nos.length) + " lugar(es) na tela" +
      (dados.total_celulas > nos.length ? " de " + numero(dados.total_celulas) : "")
    );
    $status.text("");

    if (!nos.length) {
      $legenda.empty();
      // mapa sem nós não é tela em branco sem explicação: o painel diz o que aconteceu e o que fazer
      Estados.vazio($painel, {
        titulo: "Nenhum lugar neste recorte",
        dica: "Ajuste os filtros ou o período, ou aproxime o mapa: os lugares só aparecem dentro da área visível.",
      });
      return;
    }
    if ($painel.find(".estado-vazio").length) {
      if (selecionado && porId[selecionado]) abrirPainel(porId[selecionado]);
      else fecharPainel();
    }

    calcularCores(nos);
    desenharArestas();
    desenharNos(nos);
    renderLegenda(nos);
    atualizarBotaoGeo();
    if (selecionado && !porId[selecionado]) fecharPainel();   // o nó selecionado sumiu ao mudar de nível
  }

  // A recência é colorida por posição na fila (e não pela data crua) porque o histórico cobre 20 anos
  // com quase tudo nos últimos: numa escala linear de tempo o mapa inteiro sairia da mesma cor.
  function calcularCores(nos) {
    if (modoCor === "app") {
      nos.forEach(function (no) { no._cor = CORES_APP[no.no_aplicativo] || COR_SEM_APP; });
      return;
    }
    var comData = nos.filter(function (no) { return no.dt_ultima; })
      .sort(function (a, b) { return a.dt_ultima < b.dt_ultima ? -1 : 1; });
    comData.forEach(function (no, i) {
      no._cor = corRampa(comData.length > 1 ? i / (comData.length - 1) : 1);
    });
    nos.forEach(function (no) { if (!no.dt_ultima) no._cor = COR_SEM_APP; });
  }

  function desenharNos(nos) {
    var maior = nos.reduce(function (m, no) { return Math.max(m, no.qt_registros); }, 1);

    nos.forEach(function (no) {
      // área ∝ registros (raiz do volume no diâmetro): é a leitura correta de tamanho num círculo
      var lado = Math.round(20 + 38 * Math.sqrt(no.qt_registros / maior));
      var ancora = ANCORAS[no.tp_ancora];
      var classes = "loc-cluster" + (ancora ? " ancora" : "") + (selecionado === no.id_no ? " selecionado" : "");

      var html =
        '<div class="' + classes + '" style="width:' + lado + "px;height:" + lado + "px;background:" + no._cor + '">' +
        numeroCompacto(no.qt_registros) +
        (ancora ? '<span class="loc-cluster-selo">' + ancora.selo + "</span>" : "") +
        "</div>";

      var marcador = L.marker([no.vl_latitude, no.vl_longitude], {
        icon: L.divIcon({ html: html, className: "", iconSize: [lado, lado] }),
        riseOnHover: true,
      });

      marcador.bindTooltip(
        "<strong>" + escapar(no.tx_endereco || "Sem endereço") + "</strong><br>" +
        numero(no.qt_registros) + " registros · " + numero(no.qt_dias) + " dia(s)" +
        (ancora ? "<br>" + ancora.rotulo : ""),
        { direction: "top", offset: [0, -lado / 2] }
      );

      marcador.on("click", function () { selecionar(no.id_no); });
      camadaNos.addLayer(marcador);
      marcadores[no.id_no] = marcador;
    });
  }

  function desenharArestas() {
    var minimo = parseInt($minViagens.val(), 10);
    if (!minimo) return;

    var arestas = (dados.arestas || []).filter(function (a) {
      return a.qt >= minimo && porId[a.origem] && porId[a.destino];
    });
    if (!arestas.length) return;

    var maior = arestas.reduce(function (m, a) { return Math.max(m, a.qt); }, 1);

    arestas.forEach(function (a) {
      var o = porId[a.origem], d = porId[a.destino];
      var destacada = String(selecionado) === String(a.origem) || String(selecionado) === String(a.destino);
      var meio = [(o.vl_latitude + d.vl_latitude) / 2, (o.vl_longitude + d.vl_longitude) / 2];

      var linha = L.polyline(
        [[o.vl_latitude, o.vl_longitude], meio, [d.vl_latitude, d.vl_longitude]],
        {
          color: destacada ? "#6366f1" : "#94a3b8",
          weight: 1 + 4 * Math.sqrt(a.qt / maior),
          opacity: destacada ? 0.95 : 0.4,
          interactive: false,
        }
      );
      camadaArestas.addLayer(linha);
      linhasArestas.push({ linha: linha, origem: a.origem, destino: a.destino });
      var elemento = linha.getElement();
      if (elemento) elemento.setAttribute("marker-mid", "url(#loc-seta)");
    });
    // uma inserção de <defs> por desenho, não por aresta: o querySelector varria o documento
    // inteiro a cada linha com a mesma resposta
    garantirDefs();
  }

  function renderLegenda(nos) {
    $legenda.empty();

    if (modoCor === "app") {
      var apps = [];
      nos.forEach(function (no) {
        var nome = no.no_aplicativo || "Sem app identificado";
        if (apps.indexOf(nome) === -1) apps.push(nome);
      });
      apps.sort().forEach(function (nome) {
        $('<div class="loc-legenda-item">')
          .append($('<span class="loc-legenda-bolinha">').css("background", CORES_APP[nome] || COR_SEM_APP))
          .append($("<span>").text(nome))
          .appendTo($legenda);
      });
    } else {
      var datas = nos.filter(function (no) { return no.dt_ultima; })
        .map(function (no) { return no.dt_ultima; }).sort();
      if (datas.length) {
        var gradiente = "linear-gradient(to right," + RAMPA_RECENCIA.join(",") + ")";
        $('<div class="loc-legenda-item">')
          .append($("<span>").text("Última vez aqui:"))
          .append($("<span>").text(formatarData(datas[0])))
          .append($("<span>").css({ width: "120px", height: "10px", borderRadius: "5px", background: gradiente }))
          .append($("<span>").text(formatarData(datas[datas.length - 1])))
          .appendTo($legenda);
      }
    }

    Object.keys(ANCORAS).forEach(function (tipo) {
      var alvo = null;
      nos.forEach(function (no) { if (!alvo && no.tp_ancora === tipo) alvo = no; });
      if (!alvo) return;
      $('<button type="button" class="loc-legenda-item loc-legenda-acao">')
        .attr("title", "Ir para " + ANCORAS[tipo].rotulo.toLowerCase() +
          (alvo.tx_endereco ? ": " + alvo.tx_endereco : ""))
        .append($("<span>").text(ANCORAS[tipo].selo))
        .append($("<span>").text(ANCORAS[tipo].rotulo))
        .on("click", function () { irParaNo(alvo); })
        .appendTo($legenda);
    });

    $('<div class="loc-legenda-item">')
      .append($("<span>").text("Tamanho do círculo = nº de registros · linha = deslocamento entre lugares"))
      .appendTo($legenda);
  }

  function escapar(texto) {
    return AnalyserUtils.escaparHtml(texto);
  }

  // ---------------------------------------------------------------- painel de detalhes

  /* Centraliza um lugar e abre o detalhe dele — usado pelos itens de âncora da legenda. Só desloca o
     mapa, sem mexer no zoom: mudar o zoom troca o nível da grade e, com ele, o id_no das células, e a
     seleção recém-feita seria descartada no render seguinte. */
  function irParaNo(no) {
    if (!no) return;
    mapa.panTo([no.vl_latitude, no.vl_longitude]);
    selecionar(no.id_no);
  }

  /* Clicar num nó destacava-o com um render() completo — clearLayers + recriação de todos os
     marcadores, linhas e cores, O(nós+arestas) por clique. Com o mapa carregado de perto são
     milhares de camadas recriadas para mudar a cor de uma linha. O destaque agora é incremental:
     só o marcador escolhido troca de classe e só as arestas mudam de estilo; o render() continua
     existindo para quando o DADO muda (nível, recorte, pan). */
  function destacarSelecao() {
    Object.keys(marcadores).forEach(function (id) {
      var el = marcadores[id].getElement();
      if (!el) return;
      $(el).find(".loc-cluster").first().toggleClass("selecionado", String(id) === String(selecionado));
    });
    linhasArestas.forEach(function (l) {
      var destaque = String(l.origem) === String(selecionado) || String(l.destino) === String(selecionado);
      l.linha.setStyle({ color: destaque ? "#6366f1" : "#94a3b8", opacity: destaque ? 0.95 : 0.4 });
    });
  }

  function selecionar(idNo) {
    selecionado = idNo;
    destacarSelecao();
    abrirPainel(porId[idNo]);
  }

  function fecharPainel() {
    selecionado = null;
    if (mapa) destacarSelecao();
    $painel.addClass("vazio").empty().append(
      $("<p>").text("Clique num lugar do mapa para ver o detalhe. Os círculos maiores concentram mais registros; ao aproximar, cada um se abre nos lugares que o compõem.")
    );
  }

  function abrirPainel(no) {
    if (!no) return fecharPainel();
    $painel.removeClass("vazio").empty();

    var ancora = ANCORAS[no.tp_ancora];
    $("<h3>").text(no.tx_endereco || "Sem endereço geocodificado").appendTo($painel);
    $('<p class="loc-painel-sub">')
      .text((ancora ? ancora.rotulo + " · " : "") + no.vl_latitude.toFixed(5) + ", " + no.vl_longitude.toFixed(5))
      .appendTo($painel);

    var $dl = $("<dl>").appendTo($painel);
    function linha(rotulo, valor) {
      $("<dt>").text(rotulo).appendTo($dl);
      $("<dd>").text(valor).appendTo($dl);
    }
    linha("Registros", numero(no.qt_registros));
    linha("Dias distintos", numero(no.qt_dias));
    linha("Primeira vez", formatarDataHora(no.dt_primeira));
    linha("Última vez", formatarDataHora(no.dt_ultima));
    linha("App dominante", (no.no_aplicativo || "sem app identificado") + (no.qt_apps > 1 ? " (+" + (no.qt_apps - 1) + ")" : ""));
    linha("Madrugadas (0h–6h)", numero(no.qt_noites) + " dia(s)");
    linha("Dias úteis 9h–17h", numero(no.qt_dias_uteis) + " dia(s)");

    var $acoes = $('<div class="loc-painel-acoes">').appendTo($painel);
    $('<button type="button" class="btn">').text("Aproximar aqui").on("click", function () {
      mapa.setView([no.vl_latitude, no.vl_longitude], Math.min(mapa.getZoom() + 3, 18));
    }).appendTo($acoes);
    if (no.tx_endereco) {
      $('<button type="button" class="btn">').text("Ver na tabela").on("click", function () {
        LocalizacoesFiltros.buscar(no.tx_endereco);
        mostrarTabela();
      }).appendTo($acoes);
    }
    $('<a class="btn" target="_blank" rel="noopener">')
      .attr("href", "https://www.google.com/maps?q=" + no.vl_latitude + "," + no.vl_longitude)
      .text("Google Maps").appendTo($acoes);

    var rotas = (dados.arestas || []).filter(function (a) {
      return a.origem === no.id_no || a.destino === no.id_no;
    }).sort(function (a, b) { return b.qt - a.qt; }).slice(0, 8);

    if (rotas.length) {
      $('<p class="loc-painel-titulo-secao">').text("Deslocamentos").appendTo($painel);
      rotas.forEach(function (a) {
        var saindo = a.origem === no.id_no;
        var outro = porId[saindo ? a.destino : a.origem];
        var nome = (outro && outro.tx_endereco) || "lugar sem endereço";
        $('<div class="loc-painel-rota">')
          .text((saindo ? "→ " : "← ") + nome)
          .append($("<span>").text(" · " + numero(a.qt) + "× · " + duracao(a.qt_minutos_mediana) + " em média"))
          .appendTo($painel);
      });
    }
  }

  // ---------------------------------------------------------------- nomear os lugares

  /* Sem endereço o grafo é um monte de bolha anônima, e a extração quase nunca traz endereço. Este
     botão geocodifica os lugares que estão na tela, do mais movimentado para o menos.

     O que se manda ao Nominatim é o vl_latitude_ref/vl_longitude_ref — o ponto exato mais frequente da
     célula, não o centroide: o cache casa por coordenada arredondada, e o centroide, sendo média, não
     corresponde a registro nenhum e voltaria sem endereço. */

  var TETO_GEO = 60;          // o Nominatim aceita ~1 req/s; acima disso a espera passa de um minuto
  var geoCtrl = null;         // fila em andamento (js/utils/geocodificacao.js) — cancelável
  var $btnGeo = $("#grafo-geocodificar");

  function pendentesGeo() {
    return (dados.nos || [])
      .filter(function (no) { return !no.tx_endereco && no.vl_latitude_ref !== null; })
      .sort(function (a, b) { return b.qt_registros - a.qt_registros; })
      .slice(0, TETO_GEO)
      .map(function (no) { return [no.vl_latitude_ref, no.vl_longitude_ref]; });
  }

  function atualizarBotaoGeo() {
    if (geoCtrl) return;
    var n = pendentesGeo().length;
    $btnGeo.prop("disabled", n === 0)
      .text(n ? "Nomear " + n + " lugar(es) visível(eis)" : "Lugares visíveis já nomeados");
  }

  // o laço de lotes é do utilitário compartilhado; o botão vira "Cancelar" enquanto a fila roda
  $btnGeo.on("click", function () {
    if (geoCtrl) { geoCtrl.cancelar(); return; }
    var coordenadas = pendentesGeo();
    if (!coordenadas.length) return;
    $btnGeo.text("Cancelar");
    geoCtrl = Geocodificacao.emLotes({
      idInquerito: idInquerito,
      coordenadas: coordenadas,
      aoProgresso: function (restantes) {
        $status.text("Buscando endereços… (faltam " + numero(restantes) + ")");
      },
      aoConcluir: function () {
        geoCtrl = null;
        $status.text("Endereços atualizados.");
        carregar();            // recarrega: os nós agora vêm com tx_endereco pelo JOIN do cache
      },
      aoCancelar: function () {
        geoCtrl = null;
        $status.text("Busca de endereços cancelada.");
        atualizarBotaoGeo();
      },
      aoFalhar: function () {
        geoCtrl = null;
        $status.text("Não foi possível buscar os endereços (serviço externo indisponível?).");
        atualizarBotaoGeo();
      },
    });
  });

  // ---------------------------------------------------------------- abas

  /* As três visões da tela — lista, mapa e trajetos — são exclusivas entre si. O toggle é um
     tablist ARIA de verdade (role=tab/aria-selected no HTML): aqui ele acompanha o estado e grava
     a visão na URL, para o link compartilhado abrir onde o analista deixou. */
  function alternar(qual) {
    $visaoTabela.toggleClass("hidden", qual !== "tabela");
    $visaoGrafo.toggleClass("hidden", qual !== "grafo");
    $visaoTrajetos.toggleClass("hidden", qual !== "trajetos");
    $visaoTabelaBtn.toggleClass("ativo", qual === "tabela").attr("aria-selected", String(qual === "tabela"));
    $visaoGrafoBtn.toggleClass("ativo", qual === "grafo").attr("aria-selected", String(qual === "grafo"));
    $visaoTrajetosBtn.toggleClass("ativo", qual === "trajetos").attr("aria-selected", String(qual === "trajetos"));
    if (window.EstadoUrl) EstadoUrl.salvar({ visao: qual === "tabela" ? null : qual });
  }

  function mostrarTabela() {
    alternar("tabela");
  }

  function mostrarTrajetos() {
    alternar("trajetos");
  }

  function mostrarGrafo() {
    alternar("grafo");

    if (!mapa) {
      // primeira abertura: o Leaflet ainda pode estar a caminho (carregarLeaflet)
      carregarLeaflet(function () {
        if ($visaoGrafo.hasClass("hidden")) { precisaRecarregar = true; return; }
        criarMapa();
        fecharPainel();
        carregar();
      });
      return;
    }
    mapa.invalidateSize();          // o container estava oculto e o Leaflet mediu zero
    if (precisaRecarregar) {
      precisaRecarregar = false;
      carregar();
    }
  }

  $visaoTabelaBtn.on("click", mostrarTabela);
  $visaoGrafoBtn.on("click", mostrarGrafo);
  $visaoTrajetosBtn.on("click", mostrarTrajetos);

  // setas ←/→ percorrem as abas com o teclado, como em qualquer tablist (importacao.html)
  $(".loc-visao-toggle").on("keydown", function (e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    var $bots = $visaoTabelaBtn.add($visaoGrafoBtn).add($visaoTrajetosBtn);
    var i = $bots.index(document.activeElement);
    if (i === -1) return;
    e.preventDefault();
    var $alvo = $bots.eq((i + (e.key === "ArrowRight" ? 1 : $bots.length - 1)) % $bots.length);
    $alvo.trigger("focus").trigger("click");
  });

  // os controles abaixo só mudam a aparência do que já está carregado — nada de nova busca
  function redesenhar() {
    if (mapa) render(nivelDoZoom(mapa.getZoom()));
  }

  $minViagens.on("change", redesenhar);

  $("#grafo-cor").on("change", function () {
    modoCor = $(this).val();
    redesenhar();
  });

  $enquadrar.on("click", function () {
    enquadrado = false;             // volta a usar o foco devolvido pelo servidor
    carregar();
  });

  /* Exportação para o i2 (CSV / XLSX / ANX — ver js/utils/exportar_i2.js).

     Aqui o nó é um LUGAR e o vínculo é um deslocamento entre dois lugares. Sai o recorte que
     está na tela: os lugares são células da grade do nível de zoom atual, então aproximar o mapa
     antes de exportar dá endereços, e afastar dá bairros ou cidades — é a mesma escolha que se
     faz ao olhar. O filtro de repetição dos trajetos vale também na exportação; em "Nenhum (só
     os lugares)" saem os lugares sem nenhuma ligação, que é o que a tela mostra.

     Lugar sem endereço vai pelas coordenadas: na carta, "-23,55012, -46,63331" ainda localiza,
     enquanto "sem endereço" repetido cem vezes viraria cem entidades com o mesmo nome. */
  function montarRecorteI2() {
    var nos = dados.nos || [];
    var porNo = {};
    nos.forEach(function (no) { porNo[no.id_no] = no; });

    var minimo = parseInt($minViagens.val(), 10);
    var vinculos = !minimo ? [] : (dados.arestas || []).filter(function (a) {
      return a.qt >= minimo && porNo[a.origem] && porNo[a.destino];
    }).map(function (a) {
      return {
        de: a.origem, para: a.destino,
        tipo: "Deslocamento",
        rotulo: a.qt + (a.qt > 1 ? " deslocamentos" : " deslocamento"),
        peso: a.qt,
        resumo: "Trajeto observado " + a.qt + (a.qt > 1 ? " vezes" : " vez"),
        atributos: {
          "Vezes observado": a.qt,
          "De": (porNo[a.origem].tx_endereco || "lugar sem endereço"),
          "Para": (porNo[a.destino].tx_endereco || "lugar sem endereço")
        }
      };
    });

    return {
      prefixo: "localizacoes",
      entidades: nos.map(function (no) {
        var ancora = ANCORAS[no.tp_ancora];
        return {
          chave: no.id_no,
          rotulo: no.tx_endereco || (no.vl_latitude.toFixed(5) + ", " + no.vl_longitude.toFixed(5)),
          tipo: ancora ? ancora.rotulo : "Local",
          peso: no.qt_registros,
          atributos: {
            "Endereço": no.tx_endereco || "",
            // Coordenada sempre, mesmo com endereço: é ela que localiza sem ambiguidade, e o
            // endereço é o representativo da célula da grade, não o de cada registro.
            "Coordenadas": no.vl_latitude.toFixed(5) + ", " + no.vl_longitude.toFixed(5),
            "Registros": no.qt_registros,
            "Dias distintos": no.qt_dias,
            "Primeira vez": no.dt_primeira ? formatarDataHora(no.dt_primeira) : "",
            "Última vez": no.dt_ultima ? formatarDataHora(no.dt_ultima) : "",
            "Madrugadas": no.qt_noites || "",
            "Dias úteis em horário comercial": no.qt_dias_uteis || "",
            "Aplicativo predominante": no.no_aplicativo || "",
            "Aplicativos distintos": no.qt_apps || "",
            "Âncora": ancora ? ancora.rotulo : ""
          }
        };
      }),
      vinculos: vinculos
    };
  }

  ExportarI2.ligarBotoes(montarRecorteI2);

  // filtros da tabela mudaram: o grafo tem de mostrar o mesmo recorte
  LocalizacoesFiltros.aoMudar(function () {
    if (geoCtrl) geoCtrl.cancelar();   // a fila de nomes era do recorte antigo
    enquadrado = false;
    if ($visaoGrafo.hasClass("hidden")) precisaRecarregar = true;
    else carregar();
  });

  // Deep-link: ?visao=grafo|tabela|trajetos abre direto na visão pedida; ?erbs=1
  // (botão "ERBs no mapa" da telefonia) implica Grafo, pois a camada de antenas
  // desenha no mapa do Grafo — na Tabela ela nem existe. Adiado para o ready para
  // que localizacoes_camadas.js / localizacoes_erbs.js (carregados DEPOIS deste
  // arquivo) já tenham registrado window.LocalizacoesCamadas/Erbs quando o mapa
  // for criado em mostrarGrafo().
  $(function () {
    try {
      var visaoUrl = String(AnalyserUtils.paramUrl("visao") || "").toLowerCase();
      if (!visaoUrl && AnalyserUtils.paramUrl("erbs")) visaoUrl = "grafo";
      if (visaoUrl === "grafo") mostrarGrafo();
      else if (visaoUrl === "trajetos") mostrarTrajetos();
    } catch (e) { /* mantém a visão padrão (Tabela) */ }
  });
})();
