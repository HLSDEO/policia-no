(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarDataCompacta(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return d.toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // popup do Leaflet recebe texto cru como HTML — o dado forense tem aspas e < o tempo todo
  function esc(texto) {
    return String(texto == null ? "" : texto)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  var CATEGORIA_INFO = {
    ligacao:       { icone: "\uD83D\uDCDE", cor: "#f97316", rotulo: "Ligação" },
    mensagem_wpp:  { icone: "\uD83D\uDCAC", cor: "#22c55e", rotulo: "WhatsApp" },
    mensagem_sms:  { icone: "\u2709\uFE0F", cor: "#3b82f6", rotulo: "SMS" },
    localizacao:   { icone: "\uD83D\uDCCD", cor: "#f43f5e", rotulo: "Localização" },
    notificacao:   { icone: "\uD83D\uDD14", cor: "#f59e0b", rotulo: "Notificação" },
    nota:          { icone: "\uD83D\uDCDD", cor: "#d4b23c", rotulo: "Nota" },
    navegacao:     { icone: "\uD83C\uDF10", cor: "#0ea5e9", rotulo: "Navegação" },
    email:         { icone: "\uD83D\uDCE7", cor: "#8b5cf6", rotulo: "E-mail" },
    // domínios do UFDR importados na auditoria de aproveitamento
    busca:         { icone: "\uD83D\uDD0E", cor: "#06b6d4", rotulo: "Busca" },
    social:        { icone: "\uD83D\uDCE3", cor: "#ec4899", rotulo: "Rede social" },
    compromisso:   { icone: "\uD83D\uDCC5", cor: "#14b8a6", rotulo: "Compromisso" },
    antena:        { icone: "\uD83D\uDCE1", cor: "#a855f7", rotulo: "Antena" },
    conexao:       { icone: "\uD83D\uDD17", cor: "#64748b", rotulo: "Conexão" },
    trajeto:       { icone: "\uD83E\uEDDF", cor: "#84cc16", rotulo: "Trajeto" },
    transferencia: { icone: "\uD83D\uDCB8", cor: "#eab308", rotulo: "Transferência" },
    // arquivo da Nuvem IPED com linha do tempo própria (timeStamp × timeEvent do índice)
    arquivo_iped:  { icone: "\uD83D\uDDC2\uFE0F", cor: "#64748b", rotulo: "Arquivo (IPED)" },
  };

  // Aplicações do multisseletor (a API aceita múltiplas categorias separadas por vírgula —
  // ver listar_timeline). O value é o tp_categoria cru; o rótulo é o que a tela mostra.
  var APLICACOES = [
    { valor: "ligacao", rotulo: "Ligações" },
    { valor: "mensagem_wpp", rotulo: "WhatsApp" },
    { valor: "mensagem_sms", rotulo: "SMS" },
    { valor: "localizacao", rotulo: "Localizações" },
    { valor: "notificacao", rotulo: "Notificações" },
    { valor: "nota", rotulo: "Notas" },
    { valor: "navegacao", rotulo: "Navegação" },
    { valor: "email", rotulo: "E-mails" },
    { valor: "busca", rotulo: "Buscas" },
    { valor: "social", rotulo: "Redes sociais" },
    { valor: "compromisso", rotulo: "Compromissos" },
    { valor: "antena", rotulo: "Antenas" },
    { valor: "conexao", rotulo: "Conexões" },
    { valor: "trajeto", rotulo: "Trajetos" },
    { valor: "transferencia", rotulo: "Transferências" },
    { valor: "arquivo_iped", rotulo: "Arquivos (IPED)" },
  ];

  // Rótulos amigáveis das chaves de tx_dados (o JSON estruturado que listar_timeline emite
  // por origem) — é o vocabulário do painel de detalhe, não o nome de coluna do banco.
  var DADOS_ROTULOS = {
    direcao: "Direção", contato: "Contato", telefone: "Telefone", duracao_segundos: "Duração (s)",
    situacao: "Situação", videochamada: "Videochamada", enviada: "Envio", tipo: "Tipo",
    encaminhada: "Encaminhada", apagada: "Apagada pelo remetente", temporaria: "Temporária",
    editada: "Editada", destinatarios: "Destinatários", assunto: "Assunto",
    endereco: "Endereço", categoria: "Categoria", descricao: "Descrição", origem: "Origem",
    precisao_metros: "Precisão (m)", elevacao_metros: "Elevação (m)",
    fim_permanencia: "Fim da permanência", app: "App", registrada_por: "Registrada por",
    titulo: "Título", corpo: "Corpo", criacao: "Criação", modificacao: "Modificação",
    pasta: "Pasta", anexos: "Anexos", url: "URL", visitas: "Visitas", conta: "Conta",
    remetente: "Remetente", cc: "Cc", termo: "Termo", resultados: "Resultados",
    plataforma: "Plataforma", autor: "Autor", reacoes: "Reações",
    compartilhamentos: "Compartilhamentos", comentarios: "Comentários", marcados: "Marcados",
    privacidade: "Privacidade", local: "Local", detalhes: "Detalhes", convidados: "Convidados",
    organizador: "Organizador", fim: "Fim", repeticao: "Repetição", rede: "Rede", cid: "Célula (CID)",
    lac: "Área (LAC)", mcc: "País (MCC)", operadora: "Operadora (MNC)", dispositivo: "Dispositivo",
    mac: "MAC", natureza: "Natureza", valor: "Valor", moeda: "Moeda", participantes: "Participantes",
    processamento: "Processamento", lat_origem: "Origem (lat)", lon_origem: "Origem (lon)",
    lat_destino: "Destino (lat)", lon_destino: "Destino (lon)", pontos: "Pontos do trajeto",
    nome: "Nome", evento: "Evento", arquivo: "Arquivo",
  };

  // Categorias que começam com "mensagem_" (mensageiros diversos)
  // Coordenadas no fim da descrição (legado: eventos antigos sem vl_lat/vl_lon no payload).
  function coordenadasTexto(txDescricao) {
    if (!txDescricao) return null;
    var m = String(txDescricao).match(/(\(-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?\))\s*$/);
    if (!m) return null;
    var partes = m[1].replace(/[()]/g, "").split(",").map(function (s) { return s.trim(); });
    return { lat: partes[0], lon: partes[1], texto: m[1] };
  }

  function infoCategoria(tp) {
    if (CATEGORIA_INFO[tp]) return CATEGORIA_INFO[tp];
    // mensageiros diversos: tp = "mensagem_instagram", "mensagem_telegram", etc.
    var m = tp.match(/^mensagem_(.+)$/);
    if (m) {
      var nomeApp = m[1].charAt(0).toUpperCase() + m[1].slice(1);
      return { icone: "\uD83D\uDCAC", cor: "#8696a0", rotulo: nomeApp };
    }
    return { icone: "\uD83D\uDCCC", cor: "#6366f1", rotulo: tp };
  }

  // Conjuntos: recortes temáticos de categorias (espelham _CONJUNTOS_TIMELINE no servidor, que é
  // quem de fato filtra). Aqui servem para enxugar o multisseletor ao conjunto escolhido.
  // Item terminado em '*' casa por prefixo: 'mensagem_*' pega todos os mensageiros.
  var CONJUNTOS = {
    investigativo: ["ligacao", "mensagem_wpp", "localizacao", "trajeto", "compromisso", "transferencia"],
    comunicacao:   ["ligacao", "mensagem_*", "email"],
    localizacao:   ["localizacao", "compromisso", "trajeto", "antena"],
    internet:      ["navegacao", "busca", "social"],
    financeiro:    ["transferencia"],
    aparelho:      ["notificacao", "nota", "conexao", "arquivo_iped"],
  };

  function categoriaNoConjunto(tp, lista) {
    return lista.some(function (c) {
      return c.charAt(c.length - 1) === "*" ? tp.indexOf(c.slice(0, -1)) === 0 : c === tp;
    });
  }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");
  var temaOrbit = document.documentElement.getAttribute("data-theme") === "orbit";
  var listaCarregada = false;
  var filtrosPendentes = false;
  var carregando = false;
  var xhrAtual = null;
  var xhrTotal = null;
  var totalCache = {};     // chave do recorte -> total — virar página não reconta nada
  var qtdPaginaAtual = 0;
  var eventosPagina = [];      // eventos desenhados na página atual (mapa da página e seleção)
  var selecionados = {};       // "tp_origem|id_referencia" -> {tp_origem, id_origem}

  // Estado de filtros/página na URL — a mesma convenção das telas de listagem do sistema
  // (timeline_financeira, tabela_paginada): sair e voltar não perde o recorte, e a URL vira
  // link compartilhável.
  var CHAVES_URL = ["busca", "conjunto", "categoria", "periodo", "data_de", "data_ate",
                    "pagina", "limite", "geo"];
  var estadoInicial = window.EstadoUrl ? window.EstadoUrl.ler(CHAVES_URL) : {};

  var $eventos = $("#tl-eventos");
  var $vazio = $("#tl-vazio");
  var $total = $("#total-registros");
  var $pag = $("#paginacao");
  var $pagAnt = $("#pag-anterior");
  var $pagProx = $("#pag-proximo");
  var $pagInfo = $("#pag-info");
  var $filtroBusca = $("#filtro-busca");
  var $filtroConjunto = $("#filtro-conjunto");
  var $filtroCategoria = $("#filtro-categoria");   // hidden input com as categorias em CSV
  var $filtroExtracao = $("#filtro-extracao");
  var $filtroPeriodo = $("#filtro-periodo");
  var $filtroDataDe = $("#filtro-data-de");
  var $filtroDataAte = $("#filtro-data-ate");
  var $filtroLimite = $("#filtro-limite");
  var $filtroGeo = $("#filtro-geo");

  var paginaAtual = Math.max(1, parseInt(estadoInicial.pagina, 10) || 1);
  var totalEventos = 0;

  if (!idInquerito) {
    $vazio.text("Parâmetros inválidos na URL.").removeClass("hidden").show();
    return;
  }

  // ---------- multisseletor de aplicações ----------
  var $multBotao = $("#tl-mult-botao");
  var $multPainel = $("#tl-mult-painel");
  var mudouPorDentro = false;

  function categoriasSelecionadas() {
    return String($filtroCategoria.val() || "").split(",").filter(Boolean);
  }

  function desenharAplicacoes() {
    var lista = CONJUNTOS[$filtroConjunto.val()];
    var selecionadas = categoriasSelecionadas();
    $multPainel.empty();
    APLICACOES.forEach(function (ap) {
      if (lista && !categoriaNoConjunto(ap.valor, lista)) return;
      var $item = $("<label>").addClass("tl-mult-item");
      var $cb = $("<input type='checkbox'>").val(ap.valor)
        .prop("checked", selecionadas.indexOf(ap.valor) >= 0);
      $cb.on("change", function () {
        var marcadas = $multPainel.find("input:checked").map(function () { return this.value; }).get();
        mudouPorDentro = true;
        $filtroCategoria.val(marcadas.join(","));
        $filtroCategoria.trigger("change");
      });
      $item.append($cb).append(document.createTextNode(" " + ap.rotulo));
      $multPainel.append($item);
    });
    $multPainel.append(
      $("<button type='button'>").addClass("tl-mult-limpar").text("Limpar aplicações")
        .on("click", function () {
          mudouPorDentro = true;
          $filtroCategoria.val("");
          sincronizarMultiseletor();
          $filtroCategoria.trigger("change");
        })
    );
  }

  // Sincroniza os checkboxes com o valor do hidden input (mudanças vindas de fora: restauração
  // da URL, "Limpar filtros") e atualiza o rótulo do botão.
  function sincronizarMultiseletor() {
    var selecionadas = categoriasSelecionadas();
    desenharAplicacoes();
    var rotulo = "Todas as aplicações";
    if (selecionadas.length === 1) {
      var ap = APLICACOES.filter(function (a) { return a.valor === selecionadas[0]; })[0];
      rotulo = (ap && ap.rotulo) || selecionadas[0];
    } else if (selecionadas.length > 1) {
      rotulo = selecionadas.length + " aplicações";
    }
    $multBotao.contents().first().replaceWith(document.createTextNode(rotulo + " "));
  }

  $multBotao.on("click", function (evento) {
    evento.stopPropagation();
    var aberto = !$multPainel.hasClass("hidden");
    $multPainel.toggleClass("hidden", aberto);
    $multBotao.attr("aria-expanded", aberto ? "false" : "true");
  });
  $multPainel.on("click", function (evento) { evento.stopPropagation(); });
  $(document).on("click", function () {
    $multPainel.addClass("hidden");
    $multBotao.attr("aria-expanded", "false");
  });

  // ---------- multisseletor de aparelhos (extrações) ----------
  // Mais de um celular no mesmo recorte: o hidden input guarda os ids em CSV e a rota os lê
  // daqui (?extracao=1,2). Painel idêntico ao das aplicações, mesma fiação de change.
  var $extBotao = $("#tl-ext-botao");
  var $extPainel = $("#tl-ext-painel");
  var listaAparelhos = [];
  var mudouAparelhoPorDentro = false;

  function aparelhosSelecionados() {
    return String($filtroExtracao.val() || "").split(",").filter(Boolean);
  }

  function rotuloAparelho(a) {
    return (a.no_arquivo || ("Extração " + a.id_extracao)) +
      (a.no_dispositivo ? " - " + a.no_dispositivo : "");
  }

  function desenharAparelhos() {
    var selecionados = aparelhosSelecionados();
    $extPainel.empty();
    listaAparelhos.forEach(function (a) {
      var $item = $("<label>").addClass("tl-mult-item");
      var $cb = $("<input type='checkbox'>").val(a.id_extracao)
        .prop("checked", selecionados.indexOf(String(a.id_extracao)) >= 0);
      $cb.on("change", function () {
        var marcados = $extPainel.find("input:checked").map(function () { return this.value; }).get();
        mudouAparelhoPorDentro = true;
        $filtroExtracao.val(marcados.join(","));
        $filtroExtracao.trigger("change");
      });
      $item.append($cb).append(document.createTextNode(" " + rotuloAparelho(a)));
      $extPainel.append($item);
    });
    if (listaAparelhos.length > 1) {
      $extPainel.append(
        $("<button type='button'>").addClass("tl-mult-limpar").text("Limpar aparelhos")
          .on("click", function () {
            mudouAparelhoPorDentro = true;
            $filtroExtracao.val("");
            sincronizarAparelhos();
            $filtroExtracao.trigger("change");
          })
      );
    }
  }

  function sincronizarAparelhos() {
    var selecionados = aparelhosSelecionados();
    desenharAparelhos();
    var rotulo = "Todos os aparelhos";
    if (selecionados.length === 1) {
      var a = listaAparelhos.filter(function (x) {
        return String(x.id_extracao) === selecionados[0];
      })[0];
      rotulo = a ? rotuloAparelho(a) : "Aparelho " + selecionados[0];
    } else if (selecionados.length > 1) {
      rotulo = selecionados.length + " aparelhos";
    }
    $extBotao.contents().first().replaceWith(document.createTextNode(rotulo + " "));
  }

  $extBotao.on("click", function (evento) {
    evento.stopPropagation();
    var aberto = !$extPainel.hasClass("hidden");
    $extPainel.toggleClass("hidden", aberto);
    $extBotao.attr("aria-expanded", aberto ? "false" : "true");
  });
  $extPainel.on("click", function (evento) { evento.stopPropagation(); });
  $(document).on("click", function () {
    $extPainel.addClass("hidden");
    $extBotao.attr("aria-expanded", "false");
  });

  // Carrega extracoes disponiveis para o filtro
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/extracoes-lista").done(function (extracoes) {
    // NO_ARQUIVO vem como caminho relativo em data/ — no filtro e no rodapé do evento só o nome
    // do arquivo interessa (ver docs/organizacao_arquivos.md)
    window.AnalyserUtils.encurtarCaminhos(extracoes || []);
    listaAparelhos = extracoes || [];
    if (idExtracao && !$filtroExtracao.val()) {
      // deep-link/URL: ?extracao=ID aceita CSV (vários aparelhos)
      $filtroExtracao.val(idExtracao);
    }
    sincronizarAparelhos();
  });

  // ---------- restauração do estado salvo na URL ----------
  $filtroBusca.val(estadoInicial.busca || "");
  $filtroConjunto.val(estadoInicial.conjunto || "");
  $filtroCategoria.val(estadoInicial.categoria || "");
  $filtroPeriodo.val(estadoInicial.periodo || "");
  $filtroDataDe.val(estadoInicial.data_de || "");
  $filtroDataAte.val(estadoInicial.data_ate || "");
  if (estadoInicial.geo === "1") $filtroGeo.prop("checked", true);
  $filtroLimite.val(estadoInicial.limite || "200");
  sincronizarMultiseletor();

  function obterParametros() {
    var params = { pagina: paginaAtual, limite: parseInt($filtroLimite.val(), 10) || 200 };
    if ($filtroExtracao.val()) params.extracao = $filtroExtracao.val();
    if ($filtroBusca.val()) params.busca = $filtroBusca.val();
    if ($filtroConjunto.val()) params.conjunto = $filtroConjunto.val();
    if ($filtroCategoria.val()) params.categoria = $filtroCategoria.val();
    if ($filtroPeriodo.val()) params.periodo = $filtroPeriodo.val();
    if ($filtroGeo.is(":checked")) params.somente_geo = true;
    if ($filtroDataDe.val()) params.data_de = $filtroDataDe.val();
    if ($filtroDataAte.val()) params.data_ate = $filtroDataAte.val();
    return params;
  }

  function paramsLimite() {
    return String(parseInt($filtroLimite.val(), 10) || 200);
  }

  function salvarEstadoUrl() {
    if (!window.EstadoUrl) return;
    EstadoUrl.salvar({
      busca: $filtroBusca.val() || "",
      conjunto: $filtroConjunto.val() || "",
      categoria: $filtroCategoria.val() || "",
      extracao: $filtroExtracao.val() || "",
      periodo: $filtroPeriodo.val() || "",
      data_de: $filtroDataDe.val() || "",
      data_ate: $filtroDataAte.val() || "",
      limite: paramsLimite(),
      geo: $filtroGeo.is(":checked") ? "1" : "",
      pagina: paginaAtual > 1 ? paginaAtual : "",
    });
  }

  function filtrosAtivos() {
    var p = obterParametros();
    return !!(p.busca || p.conjunto || p.categoria || p.periodo || p.somente_geo ||
      p.data_de || p.data_ate);
  }

  function mensagemErro(jqXHR) {
    var detalhe = jqXHR && jqXHR.responseJSON && jqXHR.responseJSON.detail;
    if (Array.isArray(detalhe)) detalhe = (detalhe[0] && detalhe[0].msg) || String(detalhe);
    return detalhe ? "Erro ao carregar a timeline: " + detalhe : "Erro ao carregar a timeline.";
  }

  function carregarEventos() {
    // a requisição anterior ao novo filtro/página não serve mais — corta em vez de deixar
    // a resposta atrasada pintar por cima da nova (corrida visível com rede lenta)
    if (xhrAtual) xhrAtual.abort();
    if (xhrTotal) xhrTotal.abort();
    carregando = true;
    filtrosPendentes = false;
    salvarEstadoUrl();
    $eventos.empty().html("<p class='empty-hint' style='text-align:center;padding:40px;'>Carregando\u2026</p>");

    // sem_total: a lista pinta assim que as linhas chegam (~1s); o total do recorte — que
    // relê o caso inteiro no banco — corre em paralelo e atualiza o contador quando chega
    var pLista = obterParametros();
    pLista.sem_total = true;
    xhrAtual = $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/timeline",
      data: pLista,
      dataType: "json",
    });
    xhrAtual
      .done(function (resp) {
        listaCarregada = true;
        renderizarEventos(resp);
      })
      .fail(function (jqXHR, textStatus) {
        if (textStatus === "abort") return;   // corte provocado por uma chamada mais nova
        totalEventos = 0;
        eventosPagina = [];
        $pag.hide();
        $total.text("");
        if (window.Estados) {
          $vazio.addClass("hidden");
          Estados.erro($eventos, { mensagem: mensagemErro(jqXHR), aoTentarDeNovo: carregarEventos });
        } else {
          $vazio.text(mensagemErro(jqXHR)).removeClass("hidden");
        }
      })
      .always(function () {
        carregando = false;
        xhrAtual = null;
      });
  }

  function coordenadaEvento(e) {
    var lat = e.vl_lat != null ? Number(e.vl_lat) : null;
    var lon = e.vl_lon != null ? Number(e.vl_lon) : null;
    if (Number.isFinite(lat) && Number.isFinite(lon) && (lat !== 0 || lon !== 0)) {
      return { lat: lat, lon: lon, texto: "(" + e.vl_lat + ", " + e.vl_lon + ")" };
    }
    // legado: payload de API desatualizada ainda traz a coordenada apenas no texto
    if (e.tp_categoria === "localizacao") {
      var coords = coordenadasTexto(e.tx_descricao);
      if (coords) return { lat: Number(coords.lat), lon: Number(coords.lon), texto: coords.texto };
    }
    return null;
  }

  // Link para a conversa original montado pela regra única do sistema (MapaTelas.paginaConversa)
  // — o link que vinha pronto do SQL duplicava essa regra e podia divergir da tela.
  function linkConversa(e) {
    if (!window.MapaTelas) return null;
    var fonte = e.tp_origem === "wpp_mensagem" ? "wpp"
      : e.tp_origem === "sms_mensagem" ? "sms"
      : e.tp_origem === "msg_mensagem" ? "msg" : null;
    if (!fonte) return null;
    var app = e.no_aplicativo || "";
    return MapaTelas.paginaConversa(fonte, app) + "?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + fonte +
      (fonte === "msg" && app ? "&app=" + encodeURIComponent(app) : "") +
      "&msg=" + encodeURIComponent(e.id_referencia);
  }

  function formatarValorDado(chave, valor) {
    if (valor === true) return "Sim";
    if (valor === false) return "Não";
    if (valor == null) return "";
    var texto = String(valor);
    if (/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(texto)) return formatarDataHora(texto);
    if (chave === "duracao_segundos") {
      var seg = Number(valor);
      if (!Number.isFinite(seg)) return texto;
      var m = Math.floor(seg / 60), s = seg % 60;
      return (m ? m + " min " : "") + s + " s";
    }
    return texto;
  }

  // ---------- Leaflet sob demanda (mesmo padrão de localizacoes_grafo.js) ----------
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
      leafletPromessa = null;
      if (window.Toast) Toast.aviso("Não foi possível carregar o mapa.");
    };
    document.body.appendChild(s);
    leafletPromessa.done(depois);
  }

  // tiles pelo proxy local (nginx /osm-tiles/ → OSM) — o acesso direto do navegador era
  // bloqueado pelo OSM ("Access blocked" — política de uso dos tiles); ver nginx/default.conf
  var AZULEJO_OSM = "/osm-tiles/{z}/{x}/{y}.png";

  function desenharMiniMapa($contenedor, evento) {
    carregarLeaflet(function () {
      if (!$contenedor.children().length) {
        var mapa = L.map($contenedor.get(0), { zoomControl: true, worldCopyJump: true }).setView([-15.8, -47.9], 4);
        L.tileLayer(AZULEJO_OSM, { attribution: "\u00A9 OpenStreetMap", maxZoom: 19 }).addTo(mapa);
        $contenedor.data("mapa", mapa);
      }
      var mapa = $contenedor.data("mapa");
      if (mapa._tlCamada) mapa.removeLayer(mapa._tlCamada);
      var camada = L.layerGroup().addTo(mapa);
      var coords = coordenadaEvento(evento);
      var dados = evento.tx_dados || {};
      var hora = evento.dt_evento ? formatarDataHora(evento.dt_evento) : "";
      if (coords) {
        L.circleMarker([coords.lat, coords.lon], { radius: 8, color: "#f43f5e" })
          .bindPopup(esc(hora + " - " + (evento.tx_descricao || "")))
          .addTo(camada);
      }
      // trajeto: desenha a corrida origem → destino
      if (coords && dados.lat_destino != null && dados.lon_destino != null) {
        var destino = [Number(dados.lat_destino), Number(dados.lon_destino)];
        L.circleMarker(destino, { radius: 8, color: "#84cc16" })
          .bindPopup(esc("Destino - " + (dados.destino || dados.endereco || "")))
          .addTo(camada);
        L.polyline([[coords.lat, coords.lon], destino], { color: "#84cc16", dashArray: "6 6" }).addTo(camada);
        mapa.fitBounds([[coords.lat, coords.lon], destino], { padding: [30, 30] });
      } else if (coords) {
        mapa.setView([coords.lat, coords.lon], 15);
      }
      mapa._tlCamada = camada;
      setTimeout(function () { mapa.invalidateSize(); }, 60);
    });
  }

  // ---------- mapa da página (trilha temporal dos eventos com coordenada) ----------
  var $painelMapa = $("#tl-mapa");
  var $corpoMapa = $("#tl-mapa-corpo");
  var $botaoMapa = $("#tl-mapa-btn");

  function desenharTrilha() {
    carregarLeaflet(function () {
      if (!$corpoMapa.children().length) {
        var mapa = L.map($corpoMapa.get(0), { worldCopyJump: true }).setView([-15.8, -47.9], 4);
        L.tileLayer(AZULEJO_OSM, { attribution: "\u00A9 OpenStreetMap", maxZoom: 19 }).addTo(mapa);
        $corpoMapa.data("mapa", mapa);
      }
      var mapa = $corpoMapa.data("mapa");
      if (mapa._tlCamada) mapa.removeLayer(mapa._tlCamada);
      var camada = L.layerGroup().addTo(mapa);
      var geo = eventosPagina.map(function (e) { return { e: e, c: coordenadaEvento(e) }; })
        .filter(function (x) { return x.c; })
        .sort(function (a, b) { return String(a.e.dt_evento) < String(b.e.dt_evento) ? -1 : 1; });
      $("#tl-mapa-hint").toggleClass("hidden", geo.length > 0)
        .text("Nenhum evento com coordenada nesta página.");
      if (!geo.length) { mapa._tlCamada = camada; return; }
      var pontos = geo.map(function (x) { return [x.c.lat, x.c.lon]; });
      L.polyline(pontos, { color: "#5f8cff", weight: 2, dashArray: "4 6" }).addTo(camada);
      geo.forEach(function (x, i) {
        L.circleMarker([x.c.lat, x.c.lon], { radius: 7, color: "#5f8cff", fillOpacity: 0.85 })
          .bindPopup("<strong>" + (i + 1) + "\u00BA</strong> - " + esc(formatarDataHora(x.e.dt_evento) +
            " - " + (x.e.tx_descricao || "")))
          .addTo(camada);
      });
      mapa.fitBounds(pontos, { padding: [30, 30] });
      mapa._tlCamada = camada;
      setTimeout(function () { mapa.invalidateSize(); }, 60);
    });
  }

  $botaoMapa.on("click", function () {
    var abrindo = $painelMapa.hasClass("hidden");
    $painelMapa.toggleClass("hidden", !abrindo);
    $botaoMapa.attr("aria-expanded", abrindo ? "true" : "false");
    if (abrindo) desenharTrilha();
  });

  // ---------- seleção de eventos → "marcar para o relatório" (em lote) ----------
  var $barraSelecao = $("#tl-selecao-bar");
  var $selecaoTotal = $("#selecao-total");
  var $selecaoMarcador = $("#selecao-marcador");

  function totalizarSelecao() {
    var n = Object.keys(selecionados).length;
    $selecaoTotal.text(n ? n + " selecionado(s)" : "");
    $barraSelecao.toggleClass("hidden", n === 0);
  }

  function limparSelecao() {
    selecionados = {};
    $eventos.find(".tl-item-check").prop("checked", false);
    totalizarSelecao();
  }

  $("#selecao-ver").attr("href", "marcacoes.html?inquerito=" + encodeURIComponent(idInquerito));
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/marcadores").done(function (lista) {
    (lista || []).forEach(function (m) {
      $selecaoMarcador.append($("<option>").val(m.id_marcador).text(m.no_marcador));
    });
  });

  $("#selecao-limpar").on("click", limparSelecao);
  $("#selecao-marcar").on("click", function () {
    var itens = Object.keys(selecionados).map(function (chave) {
      return { tp_origem: selecionados[chave].tp_origem, id_origem: selecionados[chave].id_origem };
    });
    if (!itens.length) return;
    var $btn = $(this).prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/lote",
      method: "POST",
      contentType: "application/json",
      dataType: "json",
      data: JSON.stringify({
        itens: itens,
        ids_marcadores: $selecaoMarcador.val() ? [Number($selecaoMarcador.val())] : [],
        acao: "adicionar",
      }),
    })
      .done(function () {
        if (window.Toast) Toast.sucesso(itens.length + " evento(s) marcados para o relatório.");
        limparSelecao();
      })
      .fail(function (jqXHR) {
        if (window.Toast) {
          Toast.erro((jqXHR.responseJSON && jqXHR.responseJSON.detail) || "Não foi possível marcar.");
        }
      })
      .always(function () { $btn.prop("disabled", false); });
  });

  // O servidor parou de deduplicar a timeline inteira com GROUP BY (era o que impedia o
  // ORDER BY + LIMIT de descer direto às fontes) — eventos idênticos no mesmo instante chegam
  // como linhas separadas e o crachá ×N é contado aqui, o que dá exatamente no mesmo desenho.
  function agruparDuplicados(eventos) {
    var porChave = {};
    var saida = [];
    eventos.forEach(function (ev) {
      var chave = [ev.dt_evento, ev.tp_categoria, ev.no_aplicativo || "", ev.tx_descricao || "",
                   ev.id_extracao, ev.tx_link || ""].join("\u0001");
      var existente = porChave[chave];
      if (existente) {
        existente.qt_repeticoes = Number(existente.qt_repeticoes || 1) + 1;
        return;
      }
      var copia = Object.assign({}, ev, { qt_repeticoes: 1 });
      porChave[chave] = copia;
      saida.push(copia);
    });
    return saida;
  }

  // ---------- total do recorte (chega em paralelo à lista) ----------
  function chaveTotal() {
    var p = obterParametros();
    delete p.pagina;
    delete p.limite;
    return JSON.stringify(p);
  }

  function pedirTotal(chave) {
    if (xhrTotal) xhrTotal.abort();
    var p = JSON.parse(chave);
    p.apenas_total = true;
    xhrTotal = $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/timeline", p)
      .done(function (resp) {
        var t = Number(resp.total || 0);
        totalCache[chave] = t;
        if (chave === chaveTotal()) {   // o recorte não mudou enquanto a contagem rodava
          totalEventos = t;
          atualizarControlesTotal();
        }
      })
      .always(function () { xhrTotal = null; });
  }

  function atualizarControlesTotal() {
    var limite = parseInt($filtroLimite.val(), 10) || 200;
    if (totalEventos == null) {
      $total.text("contando\u2026");
      // sem o total não dá para saber o número de páginas — oferece só o "próxima",
      // e apenas se a página atual encheu (sinal de que existe continuação)
      if (paginaAtual > 1 || qtdPaginaAtual >= limite) {
        $pag.show();
        $pagInfo.text("Página " + paginaAtual);
        $pagAnt.prop("disabled", paginaAtual <= 1);
        $pagProx.prop("disabled", qtdPaginaAtual < limite);
      } else {
        $pag.hide();
      }
      return;
    }
    $total.text(Number(totalEventos).toLocaleString("pt-BR") + " eventos");
    var totalPaginas = Math.ceil(totalEventos / limite);
    if (totalPaginas > 1) {
      $pag.show();
      $pagInfo.text("Página " + paginaAtual + " de " + totalPaginas + " (" +
        Number(totalEventos).toLocaleString("pt-BR") + " eventos)");
      $pagAnt.prop("disabled", paginaAtual <= 1);
      $pagProx.prop("disabled", paginaAtual >= totalPaginas);
    } else {
      $pag.hide();
    }
  }

  function renderizarEventos(resp) {
    var eventos = agruparDuplicados(window.AnalyserUtils.encurtarCaminhos(resp.eventos || []));
    var limite = parseInt($filtroLimite.val(), 10) || 200;
    var chave = chaveTotal();
    if (resp.total != null) {
      totalEventos = Number(resp.total);
      totalCache[chave] = totalEventos;
    } else if (totalCache[chave] != null) {
      totalEventos = totalCache[chave];
    } else if (paginaAtual === 1 && eventos.length < limite) {
      totalEventos = eventos.length;   // a página 1 coube inteira: ela mesma é a contagem
    } else {
      totalEventos = null;
    }
    qtdPaginaAtual = eventos.length;
    eventosPagina = eventos;
    $eventos.empty();
    $vazio.addClass("hidden");

    atualizarControlesTotal();
    if (totalEventos == null) pedirTotal(chave);

    if (!eventos.length) {
      $eventos.empty();
      if (window.Estados) {
        Estados.vazio($eventos, {
          filtrado: filtrosAtivos(),
          titulo: filtrosAtivos() ? "Nenhum evento para este recorte" : "Nenhum evento no inquérito",
          acao: filtrosAtivos() ? { rotulo: "Limpar filtros", aoClicar: limparFiltros } : null,
        });
      } else {
        $vazio.removeClass("hidden");
      }
      return;
    }

    // Agrupa por data
    var grupos = {};
    eventos.forEach(function (e) {
      var chave = formatarDataCompacta(e.dt_evento) || "sem_data";
      if (!grupos[chave]) grupos[chave] = [];
      grupos[chave].push(e);
    });

    var chaves = Object.keys(grupos).sort(function (a, b) {
      if (a === "sem_data") return 1;
      if (b === "sem_data") return -1;
      var da = a.split("/"), db = b.split("/");
      return db[2] - da[2] || db[1] - da[1] || db[0] - da[0];
    });

    chaves.forEach(function (chave, indiceGrupo) {
      var idAncora = "tl-dia-" + indiceGrupo;
      var $grupo = $("<section>").addClass("tl-dia").attr("aria-labelledby", idAncora);
      var $header = $("<h2>").addClass("tl-dia-header").attr("id", idAncora)
        .html("<span class='tl-dia-rotulo'>" + chave + "</span> <span class='tl-dia-qtd'>" + grupos[chave].length + " eventos</span>")
        .appendTo($grupo);
      var $lista = $("<ul>").addClass("tl-dia-lista").attr("role", "list").appendTo($grupo);

      grupos[chave].forEach(function (e) {
        var info = infoCategoria(e.tp_categoria);
        var $item = $("<li>").addClass("tl-item");
        var coords = coordenadaEvento(e);
        var temDados = e.tx_dados && Object.keys(e.tx_dados).length;
        var chaveSelecao = (e.tp_origem || e.tp_categoria) + "|" + e.id_referencia;

        // Checkbox de seleção para marcação em lote (a marcação vira bloco do relatório .docx)
        var $check = $("<input type='checkbox'>").addClass("tl-item-check")
          .attr("aria-label", "Selecionar evento: " + (e.tx_descricao || info.rotulo));
        if (selecionados[chaveSelecao]) $check.prop("checked", true);
        $check.on("change", function () {
          if (this.checked) selecionados[chaveSelecao] = { tp_origem: e.tp_origem || e.tp_categoria, id_origem: e.id_referencia };
          else delete selecionados[chaveSelecao];
          totalizarSelecao();
        });
        $item.append($check);

        // Indicador visual de categoria
        $("<div>").addClass("tl-item-marca").css("background", info.cor).appendTo($item);
        $("<span>").addClass("tl-item-icone").attr("aria-hidden", "true").text(info.icone).appendTo($item);

        var $corpo = $("<div>").addClass("tl-item-corpo").appendTo($item);

        // Hora
        var partes = e.dt_evento ? formatarDataHora(e.dt_evento).split(" ") : ["\u2014"];
        var hora = partes.length > 1 ? partes[1] : "\u2014";
        $("<span>").addClass("tl-item-hora").text(hora).appendTo($corpo);

        // Descrição + app. Evento idêntico repetido no mesmo instante chega agrupado do
        // servidor (ver listar_timeline): mostra "×N" em vez de N linhas iguais seguidas.
        var $desc = $("<div>").addClass("tl-item-desc").appendTo($corpo);
        var textoDescricao = e.tx_descricao || "";
        var legado = !e.vl_lat && e.tp_categoria === "localizacao" ? coordenadasTexto(textoDescricao) : null;
        var textoBase = legado ? textoDescricao.slice(0, -legado.texto.length).replace(/\s+$/, "") : textoDescricao;
        $("<span>").addClass("tl-item-texto").text(textoBase).appendTo($desc);
        if (coords && !temDados) {
          // sem painel de detalhe (API antiga) — mantém o atalho direto para o mapa externo
          $("<a>").addClass("tl-item-coordenadas")
            .attr({ href: "https://www.google.com/maps?q=" + coords.lat + "," + coords.lon, target: "_blank", rel: "noopener" })
            .attr("title", "Ver no Google Maps")
            .text(coords.texto)
            .appendTo($desc);
        }
        if (Number(e.qt_repeticoes) > 1) {
          $("<span>").addClass("tl-item-repeticoes")
            .attr("title", e.qt_repeticoes + " registros idênticos neste mesmo horário")
            .text("\u00D7" + e.qt_repeticoes).appendTo($desc);
        }
        $("<span>").addClass("tl-item-app").text(e.no_aplicativo || "").appendTo($desc);

        // Rodapé: dispositivo + nome do arquivo
        var dispositivo = [e.no_dispositivo, e.no_arquivo].filter(Boolean).join(" \u00B7 ");
        if (dispositivo) {
          $("<div>").addClass("tl-item-origem").text(dispositivo).appendTo($corpo);
        }

        // Botões de ação
        var $acoes = $("<div>").addClass("tl-item-acoes").appendTo($item);
        $acoes.append(EntidadesUI.botao({
          idInquerito: idInquerito,
          tpOrigem: e.tp_origem || e.tp_categoria,
          idOrigem: e.id_referencia,
          rotulo: (info.rotulo || e.tp_categoria) + " #" + e.id_referencia,
          temComentario: false,
          aoMudar: function () {}
        }));

        // Painel de detalhe (acordeão): o JSON estruturado que a tx_descricao achata + mini-mapa
        if (temDados || coords) {
          var $btnDetalhe = $("<button type='button'>").addClass("btn tl-item-detalhe-btn")
            .attr({ "aria-expanded": "false", "aria-label": "Ver detalhes do evento" })
            .text("\u24D8")
            .attr("title", "Detalhes do registro")
            .appendTo($acoes);
          var $detalhe = $("<div>").addClass("tl-item-detalhe hidden").attr("role", "region").appendTo($item);
          $btnDetalhe.on("click", function () {
            var abrindo = $detalhe.hasClass("hidden");
            $detalhe.toggleClass("hidden", !abrindo);
            $btnDetalhe.attr("aria-expanded", abrindo ? "true" : "false");
            if (!abrindo) return;
            if (!$detalhe.data("montado")) {
              $detalhe.data("montado", true);
              var $dl = $("<dl>").addClass("tl-detalhe-dl").appendTo($detalhe);
              Object.keys(e.tx_dados || {}).forEach(function (chave2) {
                var valor = formatarValorDado(chave2, e.tx_dados[chave2]);
                if (!valor) return;
                $dl.append($("<div>").append($("<dt>").text(DADOS_ROTULOS[chave2] || chave2))
                  .append($("<dd>").text(valor)));
              });
              if (coords) {
                $("<a>").addClass("tl-item-coordenadas tl-detalhe-gmaps")
                  .attr({ href: "https://www.google.com/maps?q=" + coords.lat + "," + coords.lon, target: "_blank", rel: "noopener" })
                  .text("Abrir no Google Maps")
                  .appendTo($detalhe);
                $("<div>").addClass("tl-mini-mapa").appendTo($detalhe);
                desenharMiniMapa($detalhe.find(".tl-mini-mapa"), e);
              }
              if (!$dl.children().length && !coords) $detalhe.remove();
            }
          });
        }

        // Link direto para a mensagem original (regra única do MapaTelas; abre na mesma aba)
        // ou pra URL de navegação (site externo — aí sim abre em nova aba, senão sai do analyser)
        var urlConversa = linkConversa(e);
        var urlExterna = e.tp_categoria === "navegacao" ? e.tx_link : null;
        var destino = urlConversa || urlExterna;
        if (destino) {
          var $link = $("<a>")
            .attr("href", destino)
            .attr("title", "Abrir original")
            .attr("aria-label", "Abrir registro original")
            .addClass("btn btn-link-original")
            .html("\uD83D\uDD0E")
            .appendTo($acoes);
          if (urlExterna) $link.attr("target", "_blank").attr("rel", "noopener");
        }

        $item.appendTo($lista);
      });

      $grupo.appendTo($eventos);
    });

    // Carrega contagens de entidades e marcações para cada tipo de origem
    var origens = {};
    eventos.forEach(function (e) {
      origens[e.tp_origem || e.tp_categoria] = true;
    });
    Object.keys(origens).forEach(function (tp) {
      EntidadesUI.carregarContagens(idInquerito, tp, function () {});
    });
    totalizarSelecao();
    // a trilha do mapa reflete a página desenhada
    if (!$painelMapa.hasClass("hidden")) desenharTrilha();
  }

  // Navegação entre páginas
  $pagAnt.on("click", function () {
    if (paginaAtual > 1) { paginaAtual--; carregarEventos(); }
  });
  $pagProx.on("click", function () {
    paginaAtual++; carregarEventos();
  });

  // Filtros: reagem a mudanças
  function salvarEcarregar() {
    paginaAtual = 1;
    carregarEventos();
  }

  function aplicarFiltros() {
    paginaAtual = 1;
    if (temaOrbit && !document.body.classList.contains("tl-orbit-lista")) {
      filtrosPendentes = true;
      return;
    }
    carregarEventos();
  }

  $filtroConjunto.on("change", function () {
    sincronizarMultiseletor();
    aplicarFiltros();
  });
  $filtroCategoria.on("change", function () {
    if (!mudouPorDentro) sincronizarMultiseletor();
    mudouPorDentro = false;
    aplicarFiltros();
  });
  $filtroExtracao.on("change", function () {
    // mudança vinda de fora (restauração, "Limpar filtros" do Orbit) redessena o painel
    if (!mudouAparelhoPorDentro) sincronizarAparelhos();
    mudouAparelhoPorDentro = false;
    salvarEstadoUrl();
    aplicarFiltros();
  });
  $filtroPeriodo.on("change", aplicarFiltros);
  $filtroDataDe.on("change", aplicarFiltros);
  $filtroDataAte.on("change", aplicarFiltros);
  $filtroLimite.on("change", salvarEcarregar);
  $filtroGeo.on("change", aplicarFiltros);
  $filtroBusca.on("input", AnalyserUtils.debounce(aplicarFiltros, 400));

  function limparFiltros() {
    $filtroBusca.val("");
    $filtroConjunto.val("");
    $filtroCategoria.val("");
    $filtroPeriodo.val("");
    $filtroDataDe.val("");
    $filtroDataAte.val("");
    $filtroGeo.prop("checked", false);
    $filtroExtracao.val("");
    sincronizarMultiseletor();
    sincronizarAparelhos();
    aplicarFiltros();
  }

  // "Limpar filtros" no tema clássico (no Orbit o botão é tratado por timeline_orbit.js, que
  // além dos filtros zera destaque/zoom do mapa e dispara o change que recarrega a lista).
  if (!temaOrbit) {
    $("#tl-orbit-clear-filters").on("click", limparFiltros);
  }

  // ---------- exportação .xlsx do recorte inteiro (não só a página desenhada) ----------
  var TETO_EXPORTACAO = 20000;
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "timeline",
      dados: function () {
        var base = obterParametros();
        delete base.pagina;
        base.limite = 500;
        base.sem_total = true;   // a exportação para na página incompleta — não precisa da contagem
        var todas = [];
        function coletar(pagina) {
          return $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/timeline",
            Object.assign({}, base, { pagina: pagina })).then(function (resp) {
              var evs = resp.eventos || [];
              todas = todas.concat(evs);
              if (todas.length > TETO_EXPORTACAO) {
                if (window.Toast) Toast.aviso("Exportação limitada a " + TETO_EXPORTACAO.toLocaleString("pt-BR") + " eventos — estreite os filtros.");
                return true;
              }
              if (evs.length >= base.limite) return coletar(pagina + 1);
              return true;
            });
        }
        return coletar(1).then(function () {
          if (!todas.length) return null;
          todas = agruparDuplicados(window.AnalyserUtils.encurtarCaminhos(todas));
          return {
            colunas: ["Data/hora", "Categoria", "Aplicação", "Descrição", "Repetições",
                      "Dispositivo", "Arquivo", "Latitude", "Longitude"],
            linhas: todas.map(function (e) {
              var c = coordenadaEvento(e);
              return [formatarDataHora(e.dt_evento), infoCategoria(e.tp_categoria).rotulo,
                      e.no_aplicativo || "", e.tx_descricao || "", e.qt_repeticoes || 1,
                      e.no_dispositivo || "", e.no_arquivo || "",
                      c ? c.lat : "", c ? c.lon : ""];
            }),
          };
        });
      },
    });
  }

  // Expõe controle da lista para integração com o mapa analítico (tema Orbit)
  window.TimelineLista = {
    carregar: function (pagina) {
      if (typeof pagina === "number") paginaAtual = pagina;
      carregarEventos();
    },
    obterParametros: obterParametros,
  };
  window.TimelineListaOrbit = {
    ativar: function () {
      if (!listaCarregada || filtrosPendentes) carregarEventos();
    },
  };

  // Carrega os eventos da lista imediatamente por padrão em todos os temas
  carregarEventos();
})();
