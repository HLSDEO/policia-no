(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function temCoord(lat, lon) {
    return lat !== null && lat !== undefined && lon !== null && lon !== undefined;
  }

  function linkMapa(lat, lon) {
    if (!temCoord(lat, lon)) return "—";
    var url = "https://www.google.com/maps?q=" + lat + "," + lon;
    return $("<a>").attr("href", url).attr("target", "_blank").attr("rel", "noopener").text(lat.toFixed(5) + ", " + lon.toFixed(5));
  }

  var TP_ORIGEM = "localizacao";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $app = $("#filtro-app");
  var $categoria = $("#filtro-categoria");
  var $periodo = $("#filtro-periodo");
  // #filtro-ordenar é lido e mantido pela TabelaPaginada (opção seletorOrdenar), não aqui
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");
  var $coordenadas = $("#filtro-coordenadas");
  var $area = $("#filtro-area");
  var $btnGeo = $("#btn-geocodificar");
  var $geoStatus = $("#geo-status");

  if (!idInquerito) {
    Estados.erro($("#tabela"), { mensagem: "Parâmetros inválidos na URL." });
    return;
  }

  // ---------- restaura os filtros salvos na URL (window.EstadoUrl) ----------
  // A TabelaPaginada grava cada filtro na query a cada carga; sem ler de volta, um link
  // compartilhado com ?busca=… abria sem filtro nenhum (só página e ordenação voltavam).
  if (window.EstadoUrl) {
    var f = EstadoUrl.ler(["busca", "app", "categoria", "periodo", "data_de", "data_ate", "so_coordenadas"]);
    if (f.busca) $busca.val(f.busca);
    if (f.periodo) $periodo.val(f.periodo);
    if (f.data_de) $dataDe.val(f.data_de);
    if (f.data_ate) $dataAte.val(f.data_ate);
    if (f.so_coordenadas === "true") $coordenadas.prop("checked", true);

    // os selects de app/categoria têm as opções vindo do endpoint /filtros, assíncrono demais
    // para a primeira consulta: cria-se a opção do valor vindo da URL na hora, e a lista real
    // (popularFiltro, abaixo) entra sem duplicar o que já está ali.
    function opcaoDeValor($sel, valor) {
      var achou = false;
      $sel.find("option").each(function () { if (this.value === valor) achou = true; });
      if (!achou) $("<option>").attr("value", valor).text(valor).appendTo($sel);
      $sel.val(valor);
    }
    if (f.app) opcaoDeValor($app, f.app);
    if (f.categoria) opcaoDeValor($categoria, f.categoria);

    // recorte por área de shapefile: a URL guarda ?camada=ID / ?feicao=ID (são os nomes que os
    // filtros ganham na query). A opção real chega com as camadas (js/localizacoes_camadas.js),
    // que pode demorar mais que a primeira carga — entra uma provisória, e o renderSelect das
    // camadas mantém o valor quando a real aparece (ou o descarta se a camada saiu).
    var fArea = EstadoUrl.ler(["camada", "feicao"]);
    var areaUrl = /^\d+$/.test(fArea.feicao || "") ? "feicao:" + fArea.feicao
      : (/^\d+$/.test(fArea.camada || "") ? "camada:" + fArea.camada : "");
    if (areaUrl) {
      if (!$area.find('option[value="' + areaUrl + '"]').length) {
        $("<option>").attr("value", areaUrl).text("Área importada…").appendTo($area);
      }
      $area.val(areaUrl);
    }
  }

  var contagens = {};            // id_localizacao -> nº de entidades (carregado uma vez)
  var coordsSemEndereco = [];    // [[lat, lon], ...] das linhas da página sem endereço, para o botão geocodificar

  // Filtros "base" (os mesmos que a aba Grafo consome via window.LocalizacoesFiltros — ver
  // abaixo). "Só com coordenadas" e "ordenar" só valem para a tabela, por isso entram à parte
  // em paramsFiltrosTabela, sem mudar o que a aba Grafo já pedia antes desta migração.
  function paramsFiltros() {
    var p = {};
    // Com o seletor de fontes montado (js/localizacoes_fontes.js), quem manda no recorte por
    // aparelho é ele — inclusive quando a URL trouxe ?extracao=, que vira só a marcação inicial.
    // Enquanto a lista de aparelhos não chega, vale o ?extracao= da URL, para a primeira consulta
    // já sair no recorte certo de quem veio pelo menu Navegar.
    if (window.LocalizacoesFontes && LocalizacoesFontes.ativo()) {
      var fontes = LocalizacoesFontes.param();
      if (fontes !== null) p.extracoes = fontes;
    } else if (idExtracao) {
      p.extracao = idExtracao;
    }
    var busca = $busca.val().trim();
    if (busca) p.busca = busca;
    if ($app.val()) p.app = $app.val();
    if ($categoria.val()) p.categoria = $categoria.val();
    if ($periodo.val()) p.periodo = $periodo.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    // recorte por área de shapefile: o <select> guarda "camada:ID" (a camada inteira) ou
    // "feicao:ID" (uma área dela) — ver js/localizacoes_camadas.js
    var area = ($area.val() || "").split(":");
    if (area.length === 2 && area[1]) p[area[0]] = area[1];
    return p;
  }

  // `ordenar` NÃO entra aqui: quem manda nele agora é a TabelaPaginada (cabeçalhos clicáveis),
  // que mantém o <select> #filtro-ordenar em sincronia pela opção seletorOrdenar.
  function paramsFiltrosTabela() {
    var p = paramsFiltros();
    if ($coordenadas.is(":checked")) p.so_coordenadas = "true";
    return p;
  }

  function atualizarBotaoGeo(total) {
    if (geoCtrl) return;              // a fila em andamento manda no rótulo/enabled do botão
    var n = coordsSemEndereco.length;
    $btnGeo.prop("disabled", n === 0);
    if (n === 0) {
      $geoStatus.text(total ? "Sem endereços a buscar nesta página." : "");
    } else {
      $geoStatus.text(n.toLocaleString("pt-BR") + " sem endereço nesta página.");
    }
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/localizacoes",
    idDe: function (r) { return r.id_localizacao; },
    ordenarPadrao: "recentes",
    params: paramsFiltrosTabela,
    seletorOrdenar: "#filtro-ordenar",
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Data/hora",
        ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" },
        render: function (r) { return formatarDataHora(r.dt_localizacao); }
      },
      { rotulo: "App", render: function (r) { return r.no_aplicativo || "—"; } },
      { rotulo: "Coordenadas", render: function (r) { return linkMapa(r.vl_latitude, r.vl_longitude); } },
      { rotulo: "Endereço", render: function (r) { return r.tx_endereco || "—"; } },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_localizacao,
            rotulo: "localização " + (r.tx_endereco || ""),
            temComentario: !!contagens[r.id_localizacao],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar as localizações.",
    aoRenderizar: function (itens, resp) {
      // sem endereço, com coordenada e ainda não geocodificada: entra na fila do botão
      // "Buscar endereços" (geocodificado=true sem endereço = já tentado, sem resultado)
      coordsSemEndereco = itens
        .filter(function (r) { return !r.tx_endereco && !r.geocodificado && temCoord(r.vl_latitude, r.vl_longitude); })
        .map(function (r) { return [r.vl_latitude, r.vl_longitude]; });
      atualizarBotaoGeo(resp.total);
    },
    exportar: {
      nome: "localizacoes",
      colunas: ["Data/hora", "App", "Latitude", "Longitude", "Endereço"],
      linha: function (r) {
        var temC = temCoord(r.vl_latitude, r.vl_longitude);
        return [
          formatarDataHora(r.dt_localizacao), r.no_aplicativo || "",
          temC ? String(r.vl_latitude) : "", temC ? String(r.vl_longitude) : "", r.tx_endereco || "",
        ];
      }
    }
  });

  var assinantesFiltro = [];

  function recarregarDoInicio() {
    // o recorte mudou: uma fila de geocodificação da página anterior perderia o sentido no meio
    if (geoCtrl) geoCtrl.cancelar();
    coordsSemEndereco = [];
    tabela.recarregar();
    assinantesFiltro.forEach(function (fn) { fn(); });
  }

  $("#limpar-filtros").on("click", function () {
    $busca.val("");
    $app.val("");
    $categoria.val("");
    $periodo.val("");
    $dataDe.val("");
    $dataAte.val("");
    $coordenadas.prop("checked", false);
    $area.val("");
    // as FONTES (aparelhos/antenas) não entram aqui: são a escolha de quem alimenta a tela, não
    // um filtro dela — desfazê-las num clique em "Limpar" pegaria o analista de surpresa.
    recarregarDoInicio();
  });

  // A aba Grafo (localizacoes_grafo.js) trabalha sobre exatamente o mesmo recorte desta tabela. Expor a
  // montagem dos filtros e o aviso de mudança evita duplicar a regra lá e as duas visões saírem do ar
  // uma com a outra.
  window.LocalizacoesFiltros = {
    idInquerito: idInquerito,
    params: paramsFiltros,
    aoMudar: function (fn) { assinantesFiltro.push(fn); },
    buscar: function (texto) { $busca.val(texto).trigger("input"); },
    // "camada:ID" / "feicao:ID" / "" — usado pelo popup do polígono no mapa
    area: function (valor) { $area.val(valor || "").trigger("change"); },
    areaAtual: function () { return $area.val() || ""; },
  };

  // ---------- geocodificação sob demanda (botão) ----------
  // O laço de lotes é do utilitário compartilhado (js/utils/geocodificacao.js). O botão vira
  // "Cancelar" enquanto a fila roda: trocar de página/filtro no meio não pode deixar a fila
  // antiga continuando sozinha em segundo plano.
  var ROTULO_GEO = "Buscar endereços (OpenStreetMap)";
  var geoCtrl = null;

  function geoTerminar(mensagem) {
    geoCtrl = null;
    $btnGeo.text(ROTULO_GEO);
    $btnGeo.prop("disabled", coordsSemEndereco.length === 0);
    $geoStatus.text(mensagem);
  }

  $btnGeo.on("click", function () {
    if (geoCtrl) { geoCtrl.cancelar(); return; }
    if (!coordsSemEndereco.length) return;
    var coordenadas = coordsSemEndereco.slice();   // fixa a lista desta página (a recarga a redefine)
    $btnGeo.text("Cancelar busca de endereços");
    $geoStatus.text("Buscando endereços no OpenStreetMap…");
    geoCtrl = Geocodificacao.emLotes({
      idInquerito: idInquerito,
      coordenadas: coordenadas,
      aoProgresso: function (restantes) {
        $geoStatus.text("Buscando endereços no OpenStreetMap… (faltam " + restantes.toLocaleString("pt-BR") + ")");
      },
      aoConcluir: function (feitas) {
        geoTerminar(feitas.toLocaleString("pt-BR") + " endereço(s) buscado(s). Atualizando…");
        tabela.carregar();   // recarrega: os endereços recém-cacheados aparecem pelo JOIN
      },
      aoCancelar: function () { geoTerminar("Busca de endereços cancelada."); },
      aoFalhar: function () { geoTerminar("Não foi possível buscar os endereços (serviço externo indisponível?)."); },
    });
  });

  // popula os selects de app/categoria (facetas do inquérito/aparelho). A restauração da URL pode
  // ter criado uma opção avulsa para o valor já filtrado — a lista real entra sem duplicar nada.
  function popularFiltro($sel, valores) {
    var existentes = {};
    $sel.find("option").each(function () { if (this.value) existentes[this.value] = true; });
    (valores || []).forEach(function (v) {
      if (!existentes[v]) $("<option>").attr("value", v).text(v).appendTo($sel);
    });
  }

  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/localizacoes/filtros" + filtrosUrl).done(function (f) {
    popularFiltro($app, f.apps);
    popularFiltro($categoria, f.categorias);
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(recarregarDoInicio, 300);
  });
  $app.on("change", recarregarDoInicio);
  $categoria.on("change", recarregarDoInicio);
  $periodo.on("change", recarregarDoInicio);
  // #filtro-ordenar não entra aqui: a TabelaPaginada já escuta o change dele (seletorOrdenar) e
  // recarrega. Um segundo handler dispararia duas requisições por troca de ordenação.
  $dataDe.on("change", recarregarDoInicio);
  $dataAte.on("change", recarregarDoInicio);
  $coordenadas.on("change", recarregarDoInicio);
  $area.on("change", recarregarDoInicio);
  // marcar/desmarcar um aparelho no seletor de fontes muda o conjunto das duas visões
  if (window.LocalizacoesFontes) LocalizacoesFontes.aoMudar(recarregarDoInicio);

  // carrega as contagens de entidade uma vez, depois a primeira página
  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    // Espera o seletor de fontes descobrir quais aparelhos existem: sem isso a primeira página
    // sairia com o recorte antigo (?extracao=) e teria de ser refeita logo em seguida.
    if (window.LocalizacoesFontes) LocalizacoesFontes.aoCarregar(function () { tabela.carregar(); });
    else tabela.carregar();
  });
})();
