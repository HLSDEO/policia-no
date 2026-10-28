/* ============================================================================
   Camada de antenas (ERBs) sobre o mapa da aba "Grafo" das localizações.

   As antenas não vêm do aparelho: vêm da resposta da operadora à quebra de sigilo telefônico
   (api/funcoes/telefonia_utils.py). Elas moram aqui, e não numa tela própria, porque o valor
   delas está em serem vistas JUNTO das localizações do celular — a antena diz por onde a linha
   passou mesmo quando o aparelho não guardou coordenada nenhuma, e onde as duas coisas se
   confirmam é onde a prova fica forte.

   Duas diferenças em relação às localizações do aparelho, e a camada é desenhada para deixar as
   duas visíveis em vez de escondidas:

     · a antena é a TORRE, não a pessoa. O raio do círculo aqui é fixo e generoso de propósito —
       ele não representa precisão, representa "região coberta". Por isso o rótulo do popup fala
       em área coberta, e nunca em posição.
     · a antena é do INQUÉRITO, não de um aparelho. A camada ignora o filtro de extração da tela,
       que é o que separa um celular do outro, e por isso ela também não entra na contagem por
       área nem no recorte de shapefile: seria misturar dois universos com a mesma etiqueta.

    O que ela NÃO ignora é o tempo. A torre não tem data — quem tem é a chamada que passou por ela
    e a sessão de dados que ficou presa nela. Então os filtros de data e de faixa do dia da tela vão
    para o servidor e são aplicados às CHAMADAS e às SESSÕES DE DADOS: com recorte temporal a camada
    deixa de ser "as antenas do ofício" e vira "as antenas por onde a linha passou neste período".
    Antena sem passagem no recorte não é desenhada, e o popup passa a dizer a primeira e a última
    passagem dentro dele (o mínimo e o máximo dos dois — voz e dados contam à parte).

    Quem liga e desliga a camada é o seletor de fontes da barra de filtros
    (js/localizacoes_fontes.js), onde as antenas aparecem ao lado dos aparelhos, UMA LINHA POR
    OPERADORA — o mesmo inquérito pode ter o retorno de várias quebras de sigilo, e desenhar todas
    juntas esconderia de qual ofício veio cada torre. A etiqueta desenhada aqui continua existindo
    para dizer quantas antenas são, de que operadoras, e para enquadrar o mapa nelas; o interruptor
    dela é um mestre (todas/nenhuma, indeterminado com seleção parcial), e a seleção fina fica no
    seletor. Entra ligada quando a URL traz ?erbs=1 ou ?erbs=<ids> (é assim que o card de telefonia
    e a tela da operadora chegam aqui); fora isso, começa desligada para não poluir a leitura de
    quem veio pelo aparelho.

    A etiqueta tem ainda o filtro de tipo de torre (todas/chamadas/dados móveis): ele só desenha
    as torres que levaram chamada ou as que prenderam sessão de dados — contadas à parte pelo
    servidor (qt_chamadas, qt_dados), então a troca é instantânea e não busca nada. Só aparece
    quando há antena com dado; sem isso ele não mudaria nada.
    ============================================================================ */
(function () {
  "use strict";

  var $barra = $("#loc-erbs");

  // sem checagem de window.L: o Leaflet entra sob demanda na primeira abertura da aba Grafo
  // (js/localizacoes_grafo.js); este módulo só toca em L depois que usarMapa()/desenhar() rodam
  if (!$barra.length || !window.LocalizacoesFiltros) return;

  var idInquerito = LocalizacoesFiltros.idInquerito;
  var URL_ERBS = "/api/investigacao/inqueritos/" + idInquerito + "/telefonia/erbs";

  // Pane próprio entre os polígonos de shapefile (350) e o overlayPane dos lugares (400): a
  // antena é contexto do que o aparelho registrou, então fica atrás dos pontos da extração.
  var PANE = "loc-erbs";
  var Z_PANE = 380;

  // Raio em metros. Não é a precisão da antena — é um lembrete visual de que o que se sabe é a
  // região, e não o ponto. Uma célula urbana cobre algo nessa ordem de grandeza.
  var RAIO_METROS = 700;

  var COR = "#f59e0b";

  var mapa = null;
  var erbs = [];
  var camada = null;
  var visivel = false;
  // Tipo de torre que a camada desenha: "todas" (voz + dados), "voz" (só as que levaram chamada)
  // ou "dados" (só as que prenderam sessão de dados). É filtro SÓ de desenho, no navegador — o
  // servidor já manda qt_chamadas e qt_dados por antena, então trocar não busca nada de novo.
  var filtroTipo = "todas";
  // A camada costuma ficar pronta ANTES do mapa existir (a aba Grafo só monta o Leaflet quando
  // aparece). Sem isto, quem chega pelo botão "ERBs no mapa" tem as antenas desenhadas e o mapa
  // parado no Brasil inteiro — e jura que não veio nada.
  var enquadrarQuandoPuder = false;
  var recorteCarregado = null;   // assinatura do recorte temporal já buscado
  var recorteAtivo = {};         // o recorte que produziu os dados em `erbs`
  var requisicao = null;

  function numero(n) {
    return AnalyserUtils.formatarNumero(n || 0);
  }

  function formatarDataHora(iso) {
    return AnalyserUtils.formatarDataHora(iso);
  }

  function escapar(texto) {
    return AnalyserUtils.escaparHtml(texto);
  }

  /* Só os filtros TEMPORAIS da tela chegam aqui: app, categoria, busca e área são do aparelho, e
     aplicá-los à antena seria filtrar um universo com o crivo do outro. */
  function recorteTemporal() {
    var p = LocalizacoesFiltros.params();
    var r = {};
    if (p.data_de) r.data_de = p.data_de;
    if (p.data_ate) r.data_ate = p.data_ate;
    if (p.periodo) r.periodo = p.periodo;
    return r;
  }

  function assinatura(r) {
    return [r.data_de || "", r.data_ate || "", r.periodo || ""].join("|");
  }

  function temRecorte(r) {
    return !!(r.data_de || r.data_ate || r.periodo);
  }

  /* Recorta a lista pelas operadoras marcadas no seletor de fontes. O seletor é a fonte da verdade
     (js/localizacoes_fontes.js): com nenhuma operadora marcada, nada é desenhado; com todas, a
     lista passa inteira. */
  function porOperadora(lista) {
    var ativas = window.LocalizacoesFontes ? LocalizacoesFontes.operadorasAtivas() : null;
    if (!ativas || !ativas.size) return [];
    return lista.filter(function (e) { return ativas.has(String(e.id_telefonia)); });
  }

  /* As antenas que passam pelo filtro de tipo de torre. Com recorte temporal, qt_chamadas e
     qt_dados já vêm recortados do servidor — então "só dados" ali é "só as que prenderam dado
     NESTE período", e não no ofício inteiro. */
  function visiveis() {
    var lista = porOperadora(erbs);
    if (filtroTipo === "voz") return lista.filter(function (e) { return Number(e.qt_chamadas) > 0; });
    if (filtroTipo === "dados") return lista.filter(function (e) { return Number(e.qt_dados) > 0; });
    return lista;
  }

  // o filtro só é oferecido quando ele pode mudar alguma coisa: sem nenhuma antena com dado,
  // "só dados" seria um botão para o vazio (vale para as antenas das operadoras marcadas)
  function temDados() {
    return porOperadora(erbs).some(function (e) { return Number(e.qt_dados) > 0; });
  }

  // O ?erbs=1 é lido pelo seletor de fontes, que é quem guarda esse estado agora.
  function ligadaNoSeletor() {
    return !!(window.LocalizacoesFontes && LocalizacoesFontes.erbsAtivas());
  }

  /* O popup responde as três perguntas que o investigador faz olhando para uma antena: que
     antena é (para casar com o CGI do CDR), onde ela fica e quanto daquela linha passou por
     ali — a contagem é o que separa "mora aqui" de "passou uma vez". */
  function popup(erb) {
    var linhas = [];
    linhas.push("<strong>" + escapar(erb.no_operadora || "Operadora") + " · " + escapar(erb.nu_cgi) + "</strong>");
    if (erb.sg_erb) linhas.push("Antena " + escapar(erb.sg_erb) +
      (erb.nu_setor ? " · setor " + escapar(erb.nu_setor) : "") +
      (erb.nu_azimute !== null && erb.nu_azimute !== undefined ? " · " + escapar(erb.nu_azimute) + "°" : "") +
      (erb.tp_tecnologia ? " · " + escapar(erb.tp_tecnologia) : ""));
    if (erb.tx_endereco) linhas.push(escapar(erb.tx_endereco));
    var lugar = [erb.no_bairro, erb.no_municipio, erb.sg_uf].filter(Boolean).join(" — ");
    if (lugar) linhas.push(escapar(lugar));
    var recortado = temRecorte(recorteAtivo);
    linhas.push("<em>" + numero(erb.qt_chamadas) + " chamada(s) por esta antena" +
      (recortado ? " <u>no período filtrado</u>" : "") + "</em>");
    // as sessões de dados aparecem à parte: somá-las às chamadas misturaria "ligou" com
    // "trafegou", e a diferença importa — dado mostra paradeiro mesmo sem nenhuma ligação
    if (Number(erb.qt_dados)) {
      linhas.push("<em>" + numero(erb.qt_dados) + " sessão(ões) de dados por esta antena" +
        (recortado ? " <u>no período filtrado</u>" : "") + "</em>");
    }
    // a primeira e a última passagem são o que transforma a torre num fato datado: é isso que
    // responde "ele estava aqui QUANDO?", e é o que o filtro de data da tela recorta
    if (erb.dt_primeira) {
      linhas.push(erb.dt_primeira === erb.dt_ultima
        ? "Passagem em " + escapar(formatarDataHora(erb.dt_primeira))
        : "De " + escapar(formatarDataHora(erb.dt_primeira)) +
          "<br>até " + escapar(formatarDataHora(erb.dt_ultima)));
    }
    linhas.push('<span style="opacity:.7">Área coberta pela torre, não a posição da pessoa.</span>');
    return linhas.join("<br>");
  }

  /* Um ofício de verdade traz milhares de antenas, e cada uma vira dois vetores. Em SVG isso são
     ~9 mil nós no DOM e o mapa engasga a cada pan; no canvas o desenho é um só elemento. Abaixo do
     limiar fica o SVG, que dá popup/hover mais preciso e é o que a maioria dos casos usa. */
  var LIMIAR_CANVAS = 500;

  function desenhar() {
    if (!mapa || !visivel) return;
    if (camada) { camada.addTo(mapa); return; }
    var lista = visiveis();
    var renderizador = lista.length > LIMIAR_CANVAS ? L.canvas({ pane: PANE, padding: 0.3 }) : null;
    camada = L.layerGroup([], { pane: PANE });
    lista.forEach(function (erb) {
      var lat = Number(erb.vl_latitude);
      var lon = Number(erb.vl_longitude);
      if (!isFinite(lat) || !isFinite(lon)) return;
      // círculo em METROS (L.circle, não L.circleMarker): a área coberta é uma medida do mundo,
      // e tem de encolher quando o mapa se afasta — senão ela mentiria sobre a escala
      L.circle([lat, lon], {
        pane: PANE, renderer: renderizador, radius: RAIO_METROS,
        color: COR, weight: 1, opacity: .9, fillColor: COR, fillOpacity: .12,
      }).bindPopup(popup(erb)).addTo(camada);
      L.circleMarker([lat, lon], {
        pane: PANE, renderer: renderizador, radius: 4,
        color: COR, weight: 2, opacity: 1, fillColor: COR, fillOpacity: .9,
      }).bindPopup(popup(erb)).addTo(camada);
    });
    camada.addTo(mapa);
  }

  function esconder() {
    if (camada && mapa) mapa.removeLayer(camada);
  }

  function sincronizar() {
    if (visivel) desenhar();
    else esconder();
    renderBarra();
  }

  /* Só o mapa das antenas manda no enquadramento quando NÃO há aparelho marcado: com aparelho na
     tela, quem enquadra é o foco dos lugares (localizacoes_grafo.js), e disputar isso faria o mapa
     pular no meio da leitura. */
  function semAparelhoNaTela() {
    return !!(window.LocalizacoesFontes && LocalizacoesFontes.param() === "0");
  }

  function enquadrar() {
    var lista = visiveis();
    if (!lista.length) return;
    if (!mapa) { enquadrarQuandoPuder = true; return; }
    enquadrarQuandoPuder = false;
    var pontos = lista.map(function (e) { return [Number(e.vl_latitude), Number(e.vl_longitude)]; })
      .filter(function (p) { return isFinite(p[0]) && isFinite(p[1]); });
    if (pontos.length) mapa.fitBounds(L.latLngBounds(pontos), { padding: [40, 40] });
  }

  function renderBarra() {
    var recortado = temRecorte(recorteAtivo);
    $barra.empty().toggleClass("hidden", !erbs.length && !recortado);
    if (!erbs.length) {
      // com recorte, "nenhuma" é resposta: a linha não passou por antena alguma naquele período
      if (recortado) {
        $('<div class="loc-camada">').append(
          $('<span class="loc-camada-info">').text("Nenhuma antena no período filtrado.")
        ).appendTo($barra);
      }
      return;
    }

    var $chip = $('<div class="loc-camada">').appendTo($barra);
    var $rotulo = $('<label class="loc-camada-toggle">').appendTo($chip);
    // Interruptor-mestre da etiqueta: marcado com todas as operadoras ligadas, indeterminado com
    // uma parte, desmarcado com nenhuma. A seleção fina por operadora fica no seletor de fontes.
    var idsAtivas = window.LocalizacoesFontes ? LocalizacoesFontes.operadorasAtivas() : new Set();
    var presentes = [];
    erbs.forEach(function (e) {
      var id = String(e.id_telefonia);
      if (presentes.indexOf(id) === -1) presentes.push(id);
    });
    var ativas = presentes.filter(function (id) { return idsAtivas.has(id); });
    $('<input type="checkbox">')
      .prop("checked", presentes.length > 0 && ativas.length === presentes.length)
      .prop("indeterminate", ativas.length > 0 && ativas.length < presentes.length)
      .on("change", function () {
        // o seletor de fontes é a fonte da verdade: ele devolve o aviso, que redesenha a camada
        if (window.LocalizacoesFontes) LocalizacoesFontes.definirTodasErbs($(this).is(":checked"));
        else { visivel = $(this).is(":checked"); sincronizar(); }
      })
      .appendTo($rotulo);
    $('<span class="loc-camada-cor">').css("background", COR).appendTo($rotulo);
    $("<span>").text("Antenas da operadora").appendTo($rotulo);

    $('<span class="loc-camada-info">')
      .text(resumo())
      .attr("title", "Vêm da quebra de sigilo telefônico, não do aparelho. O círculo é a área coberta pela torre, não a posição da pessoa.")
      .appendTo($chip);

    // Tipo de torre: voz, dados ou as duas. Só aparece quando há antena com dado — sem isso o
    // filtro não mudaria nada e seria só ruído na barra.
    if (temDados()) {
      $('<select class="loc-camada-filtro" title="Mostrar só as torres que levaram chamada, só as que prenderam dado, ou todas">')
        .append($('<option value="todas">').text("Torres: todas"))
        .append($('<option value="voz">').text("Torres: chamadas"))
        .append($('<option value="dados">').text("Torres: dados móveis"))
        .val(filtroTipo)
        .on("change", function () {
          filtroTipo = $(this).val();
          // troca só o desenho: os dados já estão todos aqui, não há o que buscar
          if (camada && mapa) mapa.removeLayer(camada);
          camada = null;
          sincronizar();
          renderBarra();
        })
        .appendTo($chip);
    }

    $('<button type="button" class="loc-camada-acao">')
      .attr("title", "Enquadrar o mapa nas antenas")
      .attr("aria-label", "Enquadrar o mapa nas antenas")
      .text("⤢")
      .on("click", enquadrar)
      .appendTo($chip);
  }

  /* Uma busca por recorte temporal. `primeira` distingue a carga inicial (que decide se a camada
     existe e se o mapa vai até ela) de uma troca de filtro (que só repõe o desenho no lugar). */
  function carregar(primeira) {
    var recorte = recorteTemporal();
    var chave = assinatura(recorte);
    if (chave === recorteCarregado) return;      // o filtro que mudou não era temporal
    recorteCarregado = chave;

    if (requisicao) requisicao.abort();          // troca rápida de data não empilha resposta velha
    requisicao = $.getJSON(URL_ERBS, recorte);
    requisicao.done(function (lista) {
      requisicao = null;
      erbs = lista || [];
      recorteAtivo = recorte;
      // o desenho anterior é de outro período: some com ele antes de repor
      if (camada && mapa) mapa.removeLayer(camada);
      camada = null;

      // Sem antena NENHUMA no ofício a barra some inteira — uma etiqueta "0 antenas" só ocuparia
      // espaço na tela de quem nunca vai importar telefonia. Já "nenhuma antena NESTE período" é
      // informação, e a barra fica para dizer isso (renderBarra trata os dois casos).
      if (!erbs.length && !temRecorte(recorte)) { $barra.addClass("hidden"); return; }

      if (primeira) visivel = ligadaNoSeletor();
      sincronizar();
      // no arranque (?erbs=1) o enquadramento só é das antenas quando não há aparelho na tela
      if (primeira && visivel && semAparelhoNaTela()) enquadrar();
    }).fail(function (xhr) {
      if (xhr && xhr.statusText === "abort") return;   // foi a própria troca de filtro
      requisicao = null;
      recorteCarregado = null;                          // deixa tentar de novo na próxima mudança
      // "sumiu a etiqueta" não distingue falha de inquérito sem telefonia — sem aviso, o
      // analista conclui que não há antenas quando foi a rede que caiu
      $barra.removeClass("hidden");
      Estados.erro($barra, {
        mensagem: "Não foi possível carregar as antenas da operadora.",
        aoTentarDeNovo: function () { carregar(true); },
      });
    });
  }

  /* "12 antena(s) · Claro (7), Vivo (5)" — mesmo texto na etiqueta do mapa e no seletor de
     fontes: a quebra por operadora é o que diz de qual quebra de sigilo aquilo veio. Com o filtro
     de tipo ativo, diz quantas do total passam ("8 de 60") para o recorte não parecer ausência. */
  function resumo(lista) {
    lista = lista || visiveis();
    var operadoras = {};
    lista.forEach(function (e) { operadoras[e.no_operadora || "?"] = (operadoras[e.no_operadora || "?"] || 0) + 1; });
    var detalhe = Object.keys(operadoras).map(function (op) {
      return op + " (" + numero(operadoras[op]) + ")";
    }).join(", ");
    var total = numero(lista.length) + (lista.length !== erbs.length ? " de " + numero(erbs.length) : "");
    return total + " antena(s)" +
      (temRecorte(recorteAtivo) ? " no período" : "") +
      (detalhe ? " · " + detalhe : "");
  }

  function usarMapa(m) {
    mapa = m;
    if (!mapa.getPane(PANE)) {
      mapa.createPane(PANE);
      mapa.getPane(PANE).style.zIndex = Z_PANE;
    }
    carregar(true);
    sincronizar();
    // o mapa acabou de nascer: se as antenas já estavam pedindo enquadramento, é agora
    if (visivel && enquadrarQuandoPuder) enquadrar();
  }

  // As antenas são do inquérito e não dependem do mapa: buscá-las já na abertura da tela deixa a
  // camada pronta (e a etiqueta preenchida) antes de alguém chegar à aba Grafo. A lista de
  // operadoras do seletor de fontes vem do bloco de telefonia, em js/localizacoes_fontes.js.
  carregar(true);

  /* Os filtros da tela mudaram. Só os temporais interessam à antena — `carregar` compara a
     assinatura e não faz requisição nenhuma quando o que mudou foi app, busca ou aparelho. */
  LocalizacoesFiltros.aoMudar(function () { carregar(false); });

  if (window.LocalizacoesFontes) {
    LocalizacoesFontes.aoMudarErbs(function () {
      visivel = ligadaNoSeletor();
      sincronizar();
      // ligar a camada é um pedido explícito: leva o mapa até as antenas (mesmo que o mapa ainda
      // não exista — aí fica pendente para quando a aba Grafo montar)
      if (visivel) enquadrar();
    });
  }

  window.LocalizacoesErbs = { usarMapa: usarMapa };
})();
