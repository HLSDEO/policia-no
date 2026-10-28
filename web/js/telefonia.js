/* Telefonia — o retorno de UMA operadora na quebra de sigilo telefônico.
 *
 * A tela é um card por operadora, e não por arquivo: cadastro, planilha do ofício e o PDF dela
 * caem todos aqui, porque são o mesmo material partido em vários arquivos (ver
 * api/funcoes/telefonia_utils.py e docs/telefonia.md). A aba "Arquivos importados" é o que
 * devolve a procedência que esse agrupamento esconde.
 *
 * Paginação no SERVIDOR (uma página de 100 por vez): o card grande tem 815 mil chamadas e
 * 200 mil números em produção, e carregar tudo de uma vez para filtrar em memória travava o
 * navegador. Cada aba busca só a página atual em /api/investigacao/telefonia/<id>/<recurso> com
 * os filtros da tela, e só a aba ativa carrega (lazy) — trocar de aba busca a primeira página
 * dela. O contrato é {total, pagina, limite, itens}, igual ao das listagens de extração.
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idTelefonia = paramUrl("telefonia");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "chamadas";
  var abaAtiva = "chamadas";
  var ABAS = ["chamadas", "erbs", "linhas", "numeros", "sessoes", "dados", "arquivos"];

  // estado (aba/filtros/página) restaurado da URL via EstadoUrl (js/utils/estado_url.js): voltar
  // de um detalhe ou abrir um link compartilhado devolve a tela no mesmo ponto. As chaves são
  // prefixadas por aba (c*/e*/l*/n*/s*/d*) para não brigar com ?inquerito=/?telefonia=/?aba=.
  var FILTROS_URL = {
    chamadas: { cbusca: "#f-cham-busca", cde: "#f-cham-de", cate: "#f-cham-ate", ctipo: "#f-cham-tipo",
                ccompl: "#f-cham-completadas", cantena: "#f-cham-com-antena" },
    erbs: { ebusca: "#f-erb-busca", euf: "#f-erb-uf", ecoord: "#f-erb-com-coord" },
    linhas: { lbusca: "#f-linha-busca", lsituacao: "#f-linha-situacao" },
    numeros: { nbusca: "#f-num-busca", nimei: "#f-num-com-imei", nrep: "#f-num-imei-repetido", nordem: "#f-num-ordem" },
    sessoes: { sbusca: "#f-ses-busca" },
    dados: { dbusca: "#f-dad-busca", dde: "#f-dad-de", date: "#f-dad-ate" }
  };
  var estadoSalvo = window.EstadoUrl
    ? window.EstadoUrl.ler(["pagina"].concat(Object.keys(FILTROS_URL.chamadas), Object.keys(FILTROS_URL.erbs),
        Object.keys(FILTROS_URL.linhas), Object.keys(FILTROS_URL.numeros),
        Object.keys(FILTROS_URL.sessoes), Object.keys(FILTROS_URL.dados)))
    : {};

  // Uma página por vez no DOM: 100 linhas desenham instantâneo, 5 mil já prendem a rolagem.
  var POR_PAGINA = 100;
  // Teto da exportação: o backend corta em 20 mil, então acima disso a tela pede para refinar
  // o filtro em vez de entregar um .xlsx silenciosamente incompleto.
  var LIMITE_EXPORT = 20000;

  var cabecalho = null;
  // total por aba (do cabeçalho do card), para o rótulo da aba não depender de carregar a lista
  var totaisCard = { chamadas: 0, erbs: 0, linhas: 0, numeros: 0, sessoes: 0, dados: 0, arquivos: 0 };
  // estado de paginação por aba + sequência anti-corrida (resposta velha não sobrescreve a nova)
  var estado = {};
  ABAS.forEach(function (aba) {
    estado[aba] = { pagina: 1, total: 0, carregada: false, seq: 0, carregando: false };
  });
  // arquivos é pequeno (um por arquivo importado) e vem inteiro de uma vez, com paginação só
  // no cliente para a tela ficar uniforme
  var arquivosLista = [];

  // ---------------------------------------------------------------- helpers
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function temValor(v) { return v !== null && v !== undefined && v !== ""; }
  function numeroBR(v, casas) {
    return temValor(v) ? num(v).toLocaleString("pt-BR", { minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0 }) : "—";
  }

  // data pura, sem hora — formatada na mão pra não escorregar de fuso
  function formatarData(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(iso);
  }

  function formatarDataHora(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?/);
    if (!m) return formatarData(iso);
    return m[3] + "/" + m[2] + "/" + m[1] + " " + m[4] + ":" + m[5] + (m[6] ? ":" + m[6] : "");
  }

  /* A operadora manda a duração em segundos, e é assim que ela precisa ser lida: "34" é meio
     minuto de conversa, "0" é chamada que nem tocou. Acima de um minuto o segundo continua
     visível — a diferença entre 1min02 e 1min58 importa numa transcrição. */
  function duracao(segundos) {
    if (!temValor(segundos)) return "—";
    var s = num(segundos);
    if (s < 60) return s + "s";
    var min = Math.floor(s / 60);
    var resto = s % 60;
    if (min < 60) return min + "min" + (resto ? " " + resto + "s" : "");
    return Math.floor(min / 60) + "h " + (min % 60) + "min";
  }

  /* "Completada" é conversa; qualquer outra coisa é tentativa. O badge existe para essa leitura
     não depender de o investigador conhecer o vocabulário da operadora ("Nao Compl."). */
  function completada(status) {
    return /^complet/i.test(String(status || ""));
  }

  function celulaBadge($tr, classe, rotulo) {
    $("<td>").addClass("col-nome").append(
      $("<span>").addClass("tel-badge " + classe).text(rotulo)
    ).appendTo($tr);
  }

  /* Antena resolvida pelo CGI: o CGI sozinho não diz nada a quem lê, e o endereço sozinho perde a
     referência que casa com o resto do CDR — então a célula mostra os dois. */
  function celulaAntena($tr, cgi, endereco, municipio, uf, tecnologia) {
    var $td = $("<td>").addClass("col-nome tel-texto").appendTo($tr);
    if (!temValor(cgi)) { $td.text("—"); return; }
    var lugar = [endereco, [municipio, uf].filter(Boolean).join("/")].filter(Boolean).join(" — ");
    $td.append($("<div>").addClass("tel-mono").text(cgi + (tecnologia ? " · " + tecnologia : "")));
    if (lugar) $td.append($("<div>").css("opacity", ".8").text(lugar));
  }

  /* Coordenada só é coordenada se for número dentro do mundo real: o link do Google Maps é o
     único href montado com dado da importação nesta tela, então lat/lon garbage (texto, >90°,
     >180°) vira "—" em vez de um href estranho. */
  function coordenadaValida(lat, lon) {
    if (!temValor(lat) || !temValor(lon)) return null;
    var la = Number(lat), lo = Number(lon);
    if (!isFinite(la) || !isFinite(lo) || Math.abs(la) > 90 || Math.abs(lo) > 180) return null;
    return { texto: la.toFixed(5) + ", " + lo.toFixed(5), lat: la, lon: lo };
  }

  function popularSelect($sel, valores, rotulo) {
    $sel.find("option[value!='']").remove();
    valores.sort(function (a, b) { return String(a).localeCompare(String(b), "pt-BR", { numeric: true }); });
    valores.forEach(function (v) {
      $("<option>").val(v).text(rotulo ? rotulo(v) : v).appendTo($sel);
    });
  }

  // contrato das listagens paginadas (documentado no topo): {total, pagina, limite, itens}
  function normalizarResposta(resp) {
    return { total: (resp && resp.total) || 0, itens: (resp && resp.itens) || [] };
  }

  function totalPaginas(aba) {
    return Math.max(1, Math.ceil((estado[aba].total || 0) / POR_PAGINA));
  }

  function infoPagina(aba) {
    var e = estado[aba];
    if (!e.total) return "nenhum registro";
    var de = (e.pagina - 1) * POR_PAGINA + 1;
    var ate = Math.min(e.pagina * POR_PAGINA, e.total);
    return "Mostrando " + numeroBR(de) + "–" + numeroBR(ate) + " de " + numeroBR(e.total) +
      " · página " + numeroBR(e.pagina) + " de " + numeroBR(totalPaginas(aba));
  }

  function renderPaginacao(aba) {
    var $pag = $("#pag-" + aba);
    if (!$pag.length) return;
    var e = estado[aba];
    var tp = totalPaginas(aba);
    $pag.toggle(e.total > POR_PAGINA);
    $pag.find(".tel-pag-info").text(infoPagina(aba));
    $pag.find(".tel-pag-ant").prop("disabled", e.pagina <= 1 || e.carregando);
    $pag.find(".tel-pag-prox").prop("disabled", e.pagina >= tp || e.carregando);
    var $info = $("#f-" + ({
      chamadas: "cham", erbs: "erb", linhas: "linha", numeros: "num", sessoes: "ses", dados: "dad"
    }[aba] || aba) + "-info");
    if ($info.length && aba !== "arquivos") $info.text(e.carregando ? "Carregando…" : infoPagina(aba));
    if (aba === abaAtiva) {
      $("#total-registros").text(numeroBR(e.total) + " registro(s)" + (e.carregando ? " · carregando…" : ""));
    }
  }

  function mostrarCarregando(aba, corpoSel) {
    var $corpo = $(corpoSel).empty();
    $("<tr>").append($("<td>").addClass("col-nome").attr("colspan", 12)
      .css("opacity", ".6").text("Carregando…")).appendTo($corpo);
  }

  // ------------------------------------------------- cabeçalho (topo)
  function renderCabecalho() {
    var $i = $("#tel-ident").empty();
    if (!cabecalho) return;
    $("#tel-operadora").text("· " + (cabecalho.no_operadora || ""));

    var periodo = [cabecalho.dt_inicio_periodo, cabecalho.dt_fim_periodo].filter(Boolean);
    var campos = [
      { rot: "Operadora", val: cabecalho.no_operadora },
      { rot: "Inquérito", val: cabecalho.nu_inquerito },
      { rot: "Arquivos importados", val: numeroBR(cabecalho.qt_arquivos) },
      // o período é a soma do que os ofícios cobrem: fora dele, "não há registro" não quer dizer
      // "não houve chamada", e é essa a leitura que a linha protege
      { rot: "Período coberto", val: periodo.length === 2 ? formatarData(periodo[0]) + " a " + formatarData(periodo[1]) : null },
      { rot: "Linhas pedidas no ofício", val: cabecalho.nu_linhas_alvo },
      { rot: "Fichas cadastrais", val: numeroBR(cabecalho.qt_linhas) },
      { rot: "Chamadas", val: numeroBR(cabecalho.qt_chamadas) },
      { rot: "Antenas", val: numeroBR(cabecalho.qt_erbs) },
      { rot: "Números no resumo", val: numeroBR(cabecalho.qt_numeros) },
      { rot: "Sessões de dados", val: numeroBR(cabecalho.qt_dados) },
      { rot: "Importado por", val: cabecalho.extraido_por }
    ];
    campos.forEach(function (c) {
      if (!temValor(c.val) || c.val === "—") return;
      var $c = $("<div>").addClass("tel-campo").appendTo($i);
      $("<div>").addClass("rot").text(c.rot).appendTo($c);
      $("<div>").addClass("val").text(c.val).appendTo($c);
    });

    totaisCard = {
      chamadas: num(cabecalho.qt_chamadas), erbs: num(cabecalho.qt_erbs),
      linhas: num(cabecalho.qt_linhas), numeros: num(cabecalho.qt_numeros),
      sessoes: num(cabecalho.qt_sessoes), dados: num(cabecalho.qt_dados),
      arquivos: num(cabecalho.qt_arquivos)
    };
    // o rótulo da aba vem do card, não da lista carregada: a lista agora é só uma página
    atualizarRotulosAbas();
  }

  // ------------------------------------------------------- Chamadas
  // params* devolvem só os filtros da tela; pagina/limite entram no carregador genérico (abaixo)
  function paramsChamadas() {
    var p = {};
    var busca = $("#f-cham-busca").val().trim();
    if (busca) p.busca = busca;
    var de = $("#f-cham-de").val(), ate = $("#f-cham-ate").val();
    if (de) p.data_de = de;
    if (ate) p.data_ate = ate;
    var tipo = $("#f-cham-tipo").val();
    if (tipo) p.tipo = tipo;
    if ($("#f-cham-completadas").is(":checked")) p.so_completadas = true;
    if ($("#f-cham-com-antena").is(":checked")) p.so_com_antena = true;
    return p;
  }

  function renderChamadas(lista) {
    var $corpo = $("#corpo-chamadas").empty();
    $("#vazio-chamadas").toggle(!lista.length && !estado.chamadas.carregando);
    lista.forEach(function (c) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(formatarDataHora(c.dt_chamada)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(c.nu_chamador)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(c.nu_imei_chamador)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(c.nu_chamado)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(c.nu_imei_chamado)).appendTo($tr);
      $("<td>").addClass("col-nome tel-num").text(duracao(c.qt_duracao_segundos)).appendTo($tr);
      celulaBadge($tr, completada(c.cd_status) ? "completada" : "tentativa", txt(c.cd_status));
      var $traf = $("<td>").addClass("col-nome").appendTo($tr);
      $traf.append(document.createTextNode(txt(c.tp_trafego)));
      if (c.tp_registro === "TUGO") {
        $traf.append(document.createTextNode(" "))
          .append($("<span>").addClass("tel-badge tugo").text("TuGO"));
      }
      celulaAntena($tr, c.nu_cgi_origem, c.tx_endereco_origem, c.no_municipio_origem,
        c.sg_uf_origem, c.tp_tecnologia_origem);
      celulaAntena($tr, c.nu_cgi_destino, c.tx_endereco_destino, c.no_municipio_destino,
        c.sg_uf_destino, c.tp_tecnologia_destino);
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------- carregador genérico
  /* As sete abas repetiam o mesmo esqueleto (seq anti-corrida + getJSON + done/fail/always)
     sete vezes — ~170 linhas de duplicação. O que muda de verdade é só o recurso da rota, onde
     estão os filtros, como a linha é desenhada e (em arquivos) o fato de a lista vir inteira e
     paginar no cliente. Um carregarAba(aba) faz o serviço das sete. */
  var ROTAS = {
    chamadas: { recurso: "chamadas", params: paramsChamadas, render: renderChamadas, rotulo: "chamadas" },
    erbs:     { recurso: "erbs",     params: paramsErbs,     render: renderErbs,     rotulo: "antenas" },
    linhas:   { recurso: "linhas",   params: paramsLinhas,   render: renderLinhas,   rotulo: "fichas cadastrais" },
    numeros:  { recurso: "numeros",  params: paramsNumeros,  render: renderNumeros,  rotulo: "números" },
    sessoes:  { recurso: "sessoes",  params: paramsSessoes,  render: renderSessoes,  rotulo: "chamadas por WiFi" },
    dados:    { recurso: "dados",    params: paramsDados,    render: renderDados,    rotulo: "sessões de dados" },
    arquivos: { recurso: "arquivos", cliente: true,                                    rotulo: "arquivos importados" }
  };

  function valorFiltro($el) {
    if ($el.attr("type") === "checkbox") return $el.is(":checked") ? "1" : null;
    return $.trim($el.val() == null ? "" : String($el.val())) || null;
  }

  // grava os filtros da aba + a página na URL; valor vazio/removido some da query
  function salvarEstadoAba(aba) {
    if (!window.EstadoUrl) return;
    var valores = {};
    var mapa = FILTROS_URL[aba] || {};
    Object.keys(mapa).forEach(function (chave) { valores[chave] = valorFiltro($(mapa[chave])); });
    valores.pagina = estado[aba].pagina > 1 ? estado[aba].pagina : null;
    EstadoUrl.salvar(valores);
  }

  // devolve os filtros salvos para os campos (chamado após popular os selects, antes da 1ª carga)
  function restaurarFiltros() {
    if (!window.EstadoUrl) return;
    ABAS.forEach(function (aba) {
      var mapa = FILTROS_URL[aba] || {};
      Object.keys(mapa).forEach(function (chave) {
        var v = estadoSalvo[chave];
        if (v == null) return;
        var $el = $(mapa[chave]);
        if ($el.attr("type") === "checkbox") $el.prop("checked", true);
        else $el.val(v);
      });
    });
    var pag = parseInt(estadoSalvo.pagina, 10);
    if (pag > 1) estado[abaAtiva].pagina = pag;
  }

  function carregarAba(aba) {
    var cfg = ROTAS[aba], e = estado[aba];
    e.carregando = true; e.seq += 1;
    var minha = e.seq;
    var atual = function () { return minha === e.seq; };
    renderPaginacao(aba);
    mostrarCarregando(aba, "#corpo-" + aba);
    var $vazio = $("#vazio-" + aba);
    Estados.limpar($vazio.hide());

    var url = "/api/investigacao/telefonia/" + idTelefonia + "/" + cfg.recurso;
    var params = cfg.cliente ? undefined : $.extend(cfg.params(), { pagina: e.pagina, limite: POR_PAGINA });
    $.getJSON(url, params)
      .done(function (resp) {
        if (!atual()) return;
        if (cfg.cliente) {
          arquivosLista = (resp && resp.itens) || resp || [];
          e.total = arquivosLista.length;
          e.pagina = Math.min(e.pagina, totalPaginas(aba));
          renderArquivos();
        } else {
          var n = normalizarResposta(resp);
          e.total = n.total;
          cfg.render(n.itens);
        }
        e.carregada = true;
      })
      .fail(function () {
        if (!atual()) return;
        // o erro entra no lugar do hint vazio, com "Tentar de novo" (Estados): "nenhum registro"
        // e "a consulta falhou" são conclusões opostas para quem investiga
        $("#corpo-" + aba).empty();
        Estados.erro($vazio.show(), {
          mensagem: "Não foi possível carregar as " + cfg.rotulo + " desta operadora.",
          aoTentarDeNovo: function () { carregarAba(aba); }
        });
      })
      .always(function () {
        if (!atual()) return;
        e.carregando = false;
        renderPaginacao(aba);
        if (aba === abaAtiva) salvarEstadoAba(aba);
      });
  }

  // ----------------------------------------------------------- ERBs
  function paramsErbs() {
    var p = {};
    var busca = $("#f-erb-busca").val().trim();
    if (busca) p.busca = busca;
    var uf = $("#f-erb-uf").val();
    if (uf) p.uf = uf;
    if ($("#f-erb-com-coord").is(":checked")) p.so_com_coord = true;
    return p;
  }

  function renderErbs(lista) {
    var $corpo = $("#corpo-erbs").empty();
    $("#vazio-erbs").toggle(!lista.length && !estado.erbs.carregando);
    lista.forEach(function (e) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(txt(e.nu_cgi)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.sg_erb || e.no_erb)).appendTo($tr);
      var setor = [temValor(e.nu_setor) ? "setor " + e.nu_setor : null,
        temValor(e.nu_azimute) ? e.nu_azimute + "°" : null].filter(Boolean).join(" · ");
      $("<td>").addClass("col-nome").text(txt(setor)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.tp_tecnologia)).appendTo($tr);
      $("<td>").addClass("col-nome tel-texto").text(txt(e.tx_endereco)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt([e.no_municipio, e.sg_uf].filter(Boolean).join("/"))).appendTo($tr);
      var coord = coordenadaValida(e.vl_latitude, e.vl_longitude);
      var $coord = $("<td>").addClass("col-nome tel-mono").appendTo($tr);
      if (coord) {
        // link externo para conferência rápida — a camada do mapa do inquérito é o botão do topo
        $coord.append($("<a>")
          .attr("href", "https://www.google.com/maps?q=" + coord.lat + "," + coord.lon)
          .attr("target", "_blank").attr("rel", "noopener").text(coord.texto));
      } else {
        $coord.text("—");
      }
      $("<td>").addClass("col-nome tel-num").text(numeroBR(e.qt_chamadas)).appendTo($tr);
      $corpo.append($tr);
    });
  }

  // --------------------------------------------------------- Linhas
  function paramsLinhas() {
    var p = {};
    var busca = $("#f-linha-busca").val().trim();
    if (busca) p.busca = busca;
    var situacao = $("#f-linha-situacao").val();
    if (situacao) p.situacao = situacao;
    return p;
  }

  function renderLinhas(lista) {
    var $corpo = $("#corpo-linhas").empty();
    $("#vazio-linhas").toggle(!lista.length && !estado.linhas.carregando);
    lista.forEach(function (l) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(txt(l.nu_linha)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(l.no_cliente)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(l.nu_cpf_cnpj)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(l.nu_rg)).appendTo($tr);
      var endereco = [l.tx_endereco, l.no_bairro, l.nu_cep].filter(Boolean).join(" — ");
      $("<td>").addClass("col-nome tel-texto").text(txt(endereco)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt([l.no_municipio, l.sg_uf].filter(Boolean).join("/"))).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(l.tp_modalidade)).appendTo($tr);
      celulaBadge($tr, /inativ|cancel/i.test(String(l.tp_situacao || "")) ? "inativo" : "ativo", txt(l.tp_situacao));
      $("<td>").addClass("col-nome tel-mono").text(formatarData(l.dt_habilitacao)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(formatarData(l.dt_rescisao)).appendTo($tr);
      $corpo.append($tr);
    });
  }

  // -------------------------------------------------------- Números
  function paramsNumeros() {
    var p = {};
    var busca = $("#f-num-busca").val().trim();
    if (busca) p.busca = busca;
    if ($("#f-num-com-imei").is(":checked")) p.so_com_imei = true;
    if ($("#f-num-imei-repetido").is(":checked")) p.so_compartilhado = true;
    var ordenar = $("#f-num-ordem").val();
    if (ordenar === "chamadas") p.ordenar = "chamadas";
    return p;
  }

  function renderNumeros(lista) {
    var $corpo = $("#corpo-numeros").empty();
    $("#vazio-numeros").toggle(!lista.length && !estado.numeros.carregando);
    lista.forEach(function (n) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(txt(n.nu_telefone)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(n.no_cliente)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(n.nu_imei)).appendTo($tr);
      var irmaos = n.nu_outros_telefones || [];
      $("<td>").addClass("col-nome tel-mono").text(irmaos.length ? irmaos.join(", ") : "—").appendTo($tr);
      $("<td>").addClass("col-nome tel-num").text(numeroBR(n.qt_chamadas)).appendTo($tr);
      $corpo.append($tr);
    });
  }

  // -------------------------------------------------------- Sessões
  function paramsSessoes() {
    var p = {};
    var busca = $("#f-ses-busca").val().trim();
    if (busca) p.busca = busca;
    return p;
  }

  function renderSessoes(lista) {
    var $corpo = $("#corpo-sessoes").empty();
    $("#vazio-sessoes").toggle(!lista.length && !estado.sessoes.carregando);
    lista.forEach(function (s) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(txt(s.nu_linha)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(s.tx_ip)).appendTo($tr);
      $("<td>").addClass("col-nome tel-num").text(txt(s.nu_porta)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(formatarDataHora(s.dt_inicio)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(formatarDataHora(s.dt_fim)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(s.tp_trafego)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(s.no_servico)).appendTo($tr);
      $corpo.append($tr);
    });
  }

  // -------------------------------------------------------- Dados móveis
  function paramsDados() {
    var p = {};
    var busca = $("#f-dad-busca").val().trim();
    if (busca) p.busca = busca;
    var de = $("#f-dad-de").val(), ate = $("#f-dad-ate").val();
    if (de) p.data_de = de;
    if (ate) p.data_ate = ate;
    return p;
  }

  // volume com a unidade que a operadora mandou (quase sempre K): "3342 K" lê direto, sem
  // conta — a ordem de grandeza é o que importa na triagem
  function volume(qtd, unidade) {
    if (!temValor(qtd)) return "—";
    return numeroBR(qtd) + (temValor(unidade) ? " " + unidade : "");
  }

  function renderDados(lista) {
    var $corpo = $("#corpo-dados").empty();
    $("#vazio-dados").toggle(!lista.length && !estado.dados.carregando);
    lista.forEach(function (d) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-mono").text(formatarDataHora(d.dt_inicio)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(formatarDataHora(d.dt_fim)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(d.nu_linha)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(d.tx_ip)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(d.tx_ipv6)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(d.nu_imei)).appendTo($tr);
      celulaAntena($tr, d.nu_cgi, d.tx_endereco_erb, d.no_municipio_erb,
        d.sg_uf_erb, d.tp_tecnologia);
      $("<td>").addClass("col-nome tel-num").text(volume(d.qt_vol_entrada, d.tp_unidade)).appendTo($tr);
      $("<td>").addClass("col-nome tel-num").text(volume(d.qt_vol_saida, d.tp_unidade)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(d.tp_tecnologia)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(d.tp_rede)).appendTo($tr);
      $corpo.append($tr);
    });
  }

  // ------------------------------------------------------ Arquivos
  var ROTULO_CONTEUDO = { cadastro: "Cadastro (texto)", planilha: "Planilha do ofício", pdf: "PDF do ofício" };
  var ROTULO_SECAO = {
    cadastro: "cadastro de linhas", chamadas: "chamadas", tugo: "chamadas TuGO",
    wifi: "chamadas WiFi", erbs: "antenas", numeros: "números/IMEI", dados: "dados móveis"
  };

  function renderArquivos() {
    var e = estado.arquivos;
    var $corpo = $("#corpo-arquivos").empty();
    $("#vazio-arquivos").toggle(!arquivosLista.length);
    var inicio = (e.pagina - 1) * POR_PAGINA;
    arquivosLista.slice(inicio, inicio + POR_PAGINA).forEach(function (a) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome tel-texto").text(txt(a.no_arquivo)).appendTo($tr);
      celulaBadge($tr, a.tp_conteudo || "", ROTULO_CONTEUDO[a.tp_conteudo] || txt(a.tp_conteudo));
      var secoes = String(a.tx_secoes || "").split(",").map(function (s) { return s.trim(); })
        .filter(Boolean).map(function (s) { return ROTULO_SECAO[s] || s; });
      $("<td>").addClass("col-nome tel-texto").text(secoes.length ? secoes.join(", ") : "—").appendTo($tr);
      $("<td>").addClass("col-nome tel-texto").text(txt(a.nu_referencia)).appendTo($tr);
      $("<td>").addClass("col-nome tel-mono").text(txt(a.nu_linha_alvo)).appendTo($tr);
      var periodo = [a.dt_inicio_periodo, a.dt_fim_periodo].filter(Boolean);
      $("<td>").addClass("col-nome tel-mono")
        .text(periodo.length === 2 ? formatarData(periodo[0]) + " a " + formatarData(periodo[1]) : "—").appendTo($tr);
      $("<td>").addClass("col-nome tel-num").text(numeroBR(a.qt_registros)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(a.importado_por)).appendTo($tr);
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      if (a.id_documento && idInquerito) {
        $("<a>").addClass("btn btn-secondary")
          .attr("href", "documento.html?inquerito=" + encodeURIComponent(idInquerito) +
            "&doc=" + encodeURIComponent(a.id_documento))
          .text("Abrir").appendTo($acoes);
      } else {
        $acoes.text("—");
      }
      $corpo.append($tr);
    });
    e.total = arquivosLista.length;
    renderPaginacao("arquivos");
  }

  function recarregar(aba) {
    estado[aba].pagina = 1;
    carregarAba(aba);
  }

  // ------------------------------------------------------------------ abas
  function atualizarRotulosAbas() {
    $(".tel-aba").each(function () {
      var aba = $(this).data("aba");
      var base = $(this).data("rotulo-base");
      if (!base) { base = $(this).text().replace(/\s*\(.*\)\s*$/, ""); $(this).data("rotulo-base", base); }
      var n = totaisCard[aba] || estado[aba].total || 0;
      $(this).text(base + (n ? " (" + numeroBR(n) + ")" : ""));
    });
  }

  function trocarAba(aba) {
    abaAtiva = aba;
    $(".tel-aba").removeClass("ativa").attr("aria-selected", "false")
      .filter('[data-aba="' + aba + '"]').addClass("ativa").attr("aria-selected", "true");
    $(".tel-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    // lazy: a aba só busca quando é aberta pela primeira vez — abrir a tela não dispara 6
    // consultas pesadas em paralelo
    if (!estado[aba].carregada && !estado[aba].carregando) {
      carregarAba(aba);
    } else {
      renderPaginacao(aba);
      salvarEstadoAba(aba);   // a página desta aba passa a ser a da URL
    }
    if (window.EstadoUrl) EstadoUrl.salvar({ aba: aba !== "chamadas" ? aba : null });
  }

  $(".tel-aba").on("click", function () { trocarAba($(this).data("aba")); });

  // paginação: anterior/próxima de cada aba
  ABAS.forEach(function (aba) {
    function irPara(pagina) {
      estado[aba].pagina = pagina;
      if (aba === "arquivos") { renderArquivos(); salvarEstadoAba(aba); } // paginação local
      else carregarAba(aba);                                              // salva no always
      window.scrollTo(0, 0);
    }
    $("#pag-" + aba + " .tel-pag-ant").on("click", function () {
      if (estado[aba].pagina > 1 && !estado[aba].carregando) irPara(estado[aba].pagina - 1);
    });
    $("#pag-" + aba + " .tel-pag-prox").on("click", function () {
      if (estado[aba].pagina < totalPaginas(aba) && !estado[aba].carregando) irPara(estado[aba].pagina + 1);
    });
  });

  // filtros: a busca espera 300ms para não disparar uma consulta por letra num CDR de 815 mil
  // linhas; o resto recarrega na hora e volta para a página 1. O debounce é o utilitário comum
  // (AnalyserUtils.debounce) — não vale reinventar por tela.
  var debounce = AnalyserUtils.debounce;
  $("#f-cham-busca").on("input", debounce(function () { recarregar("chamadas"); }, 300));
  $("#f-cham-de, #f-cham-ate, #f-cham-tipo, #f-cham-completadas, #f-cham-com-antena")
    .on("input change", function () { recarregar("chamadas"); });
  $("#f-erb-busca").on("input", debounce(function () { recarregar("erbs"); }, 300));
  $("#f-erb-uf, #f-erb-com-coord").on("input change", function () { recarregar("erbs"); });
  $("#f-linha-busca").on("input", debounce(function () { recarregar("linhas"); }, 300));
  $("#f-linha-situacao").on("input change", function () { recarregar("linhas"); });
  $("#f-num-busca").on("input", debounce(function () { recarregar("numeros"); }, 300));
  $("#f-num-com-imei, #f-num-imei-repetido, #f-num-ordem")
    .on("input change", function () { recarregar("numeros"); });
  $("#f-ses-busca").on("input", debounce(function () { recarregar("sessoes"); }, 300));
  $("#f-dad-busca").on("input", debounce(function () { recarregar("dados"); }, 300));
  $("#f-dad-de, #f-dad-ate").on("input change", function () { recarregar("dados"); });

  // ------------------------------------------------------------------ carga
  if (!idTelefonia) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  var abaValida = ABAS.indexOf(abaInicial) !== -1 ? abaInicial : "chamadas";
  abaAtiva = abaValida;
  var umavez = false;   // restaurar os filtros da URL só na 1ª passada (o retry não os reaplica)

  /* Cabeçalho primeiro (traz os totais dos rótulos das abas), depois o merge com os ofícios do
     inquérito e por último as opções de filtro — a ordem importa porque restaurar a URL exige os
     selects de UF/situação povoados. Antes a cadeia inteira não tinha .fail: se o card falhasse,
     a tela abria sem cabeçalho nem totais, sem nenhuma explicação; agora o erro vai para
     #tel-estado com "Tentar de novo". */
  function carregarCabecalho() {
    $("#tel-estado").empty();
    var falhouCabecalho = false;

    function depoisMerge() {
      renderCabecalho();
      // os selects saem do que a operadora realmente mandou: fixar a lista ofereceria filtro de
      // UF que não existe neste ofício. Restaurar a URL só depois de povoados: .val() em option
      // que ainda não existe não pega.
      $.getJSON("/api/investigacao/telefonia/" + idTelefonia + "/filtros")
        .done(function (f) {
          popularSelect($("#f-erb-uf"), (f && f.ufs) || []);
          popularSelect($("#f-linha-situacao"), (f && f.situacoes) || []);
        })
        .always(function () {
          if (falhouCabecalho) {
            Estados.erro($("#tel-estado"), {
              mensagem: "Não foi possível carregar o cabeçalho deste retorno.",
              aoTentarDeNovo: carregarCabecalho
            });
          }
          if (!umavez) { umavez = true; restaurarFiltros(); }
          trocarAba(abaValida);
        });
    }

    $.getJSON("/api/investigacao/telefonia/" + idTelefonia)
      .done(function (c) { cabecalho = c; })
      .fail(function () { falhouCabecalho = true; })
      .always(function () {
        if (idInquerito) {
          $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/telefonia")
            .done(function (lista) {
              var meu = (lista || []).filter(function (t) { return String(t.id_telefonia) === String(idTelefonia); })[0];
              if (meu) cabecalho = $.extend({}, meu, cabecalho || {}, {
                nu_linhas_alvo: meu.nu_linhas_alvo,
                dt_inicio_periodo: meu.dt_inicio_periodo,
                dt_fim_periodo: meu.dt_fim_periodo
              });
            })
            .fail(function () {
              // o merge só acrescenta contexto de ofício ao cabeçalho: o aviso avisa, não trava
              if (window.Toast && Toast.erro) Toast.erro("Não foi possível cruzar com os ofícios do inquérito — o período coberto pode ficar incompleto.");
            })
            .always(depoisMerge);
        } else {
          depoisMerge();
        }
      });
  }
  carregarCabecalho();

  // exportar Excel da aba ativa, respeitando o filtro atual — buscando no servidor (até o teto),
  // e não lendo o DOM (que agora tem só 100 linhas da página atual)
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "telefonia",
      dados: function () {
        var total = estado[abaAtiva].total || 0;
        if (total > LIMITE_EXPORT) {
          var msg = "O filtro atual tem " + numeroBR(total) + " registros — a exportação vai até " +
            numeroBR(LIMITE_EXPORT) + ". Refine o filtro para levar tudo.";
          if (window.Dialogo && Dialogo.alerta) Dialogo.alerta(msg);
          else alert(msg);
          return null;
        }
        if (abaAtiva === "arquivos") {
          return ExportarExcel.lerTabela(document.getElementById("tab-arquivos"));
        }
        var p = ROTAS[abaAtiva].params();   // params* já devolvem só os filtros da tela
        p.pagina = 1;
        p.limite = LIMITE_EXPORT;
        return $.getJSON("/api/investigacao/telefonia/" + idTelefonia + "/" + ROTAS[abaAtiva].recurso, p)
          .then(function (resp) {
            var n = normalizarResposta(resp);
            return linhasExport(abaAtiva, n.itens);
          });
      },
    });
  }

  function linhasExport(aba, itens) {
    if (aba === "chamadas") {
      return {
        colunas: ["Data e hora", "Chamador", "IMEI chamador", "Chamado", "IMEI chamado",
          "Duração", "Status", "Tráfego", "Antena (origem)", "Antena (destino)"],
        linhas: itens.map(function (c) {
          return [formatarDataHora(c.dt_chamada), c.nu_chamador || "", c.nu_imei_chamador || "",
            c.nu_chamado || "", c.nu_imei_chamado || "", duracao(c.qt_duracao_segundos),
            c.cd_status || "", [c.tp_trafego, c.tp_registro === "TUGO" ? "TuGO" : null].filter(Boolean).join(" "),
            [c.nu_cgi_origem, c.tx_endereco_origem, [c.no_municipio_origem, c.sg_uf_origem].filter(Boolean).join("/")].filter(Boolean).join(" — "),
            [c.nu_cgi_destino, c.tx_endereco_destino, [c.no_municipio_destino, c.sg_uf_destino].filter(Boolean).join("/")].filter(Boolean).join(" — ")];
        })
      };
    }
    if (aba === "erbs") {
      return {
        colunas: ["CGI", "Sigla", "Setor", "Azimute", "Tecnologia", "Endereço", "Cidade/UF",
          "Latitude", "Longitude", "Chamadas"],
        linhas: itens.map(function (e) {
          return [e.nu_cgi || "", e.sg_erb || e.no_erb || "", e.nu_setor != null ? String(e.nu_setor) : "",
            e.nu_azimute != null ? String(e.nu_azimute) : "", e.tp_tecnologia || "", e.tx_endereco || "",
            [e.no_municipio, e.sg_uf].filter(Boolean).join("/"),
            e.vl_latitude != null ? String(e.vl_latitude) : "",
            e.vl_longitude != null ? String(e.vl_longitude) : "",
            e.qt_chamadas != null ? String(e.qt_chamadas) : ""];
        })
      };
    }
    if (aba === "linhas") {
      return {
        colunas: ["Linha", "Titular", "CPF/CNPJ", "RG", "Endereço", "Cidade/UF", "Modalidade",
          "Situação", "Habilitação", "Rescisão"],
        linhas: itens.map(function (l) {
          return [l.nu_linha || "", l.no_cliente || "", l.nu_cpf_cnpj || "", l.nu_rg || "",
            [l.tx_endereco, l.no_bairro, l.nu_cep].filter(Boolean).join(" — "),
            [l.no_municipio, l.sg_uf].filter(Boolean).join("/"), l.tp_modalidade || "",
            l.tp_situacao || "", formatarData(l.dt_habilitacao), formatarData(l.dt_rescisao)];
        })
      };
    }
    if (aba === "numeros") {
      return {
        colunas: ["Número", "Titular", "IMEI", "Outros números no mesmo IMEI", "Chamadas"],
        linhas: itens.map(function (n) {
          return [n.nu_telefone || "", n.no_cliente || "", n.nu_imei || "",
            (n.nu_outros_telefones || []).join(", "), n.qt_chamadas != null ? String(n.qt_chamadas) : ""];
        })
      };
    }
    if (aba === "dados") {
      return {
        colunas: ["Início", "Fim", "Linha", "IP", "IPv6", "IMEI", "Antena (CGI)",
          "Vol. entrada", "Vol. saída", "Tecnologia", "Rede"],
        linhas: itens.map(function (d) {
          return [formatarDataHora(d.dt_inicio), formatarDataHora(d.dt_fim),
            d.nu_linha || "", d.tx_ip || "", d.tx_ipv6 || "", d.nu_imei || "",
            [d.nu_cgi, d.tx_endereco_erb,
              [d.no_municipio_erb, d.sg_uf_erb].filter(Boolean).join("/")].filter(Boolean).join(" — "),
            temValor(d.qt_vol_entrada) ? String(d.qt_vol_entrada) + (d.tp_unidade ? " " + d.tp_unidade : "") : "",
            temValor(d.qt_vol_saida) ? String(d.qt_vol_saida) + (d.tp_unidade ? " " + d.tp_unidade : "") : "",
            d.tp_tecnologia || "", d.tp_rede || ""];
        })
      };
    }
    return {
      colunas: ["Linha", "IP", "Porta", "Início", "Fim", "Tráfego", "Serviço"],
      linhas: itens.map(function (s) {
        return [s.nu_linha || "", s.tx_ip || "", s.nu_porta != null ? String(s.nu_porta) : "",
          formatarDataHora(s.dt_inicio), formatarDataHora(s.dt_fim),
          s.tp_trafego || "", s.no_servico || ""];
      })
    };
  }

  function tituloTelefonia() {
    return "Telefonia " + ((cabecalho && cabecalho.no_operadora) || idTelefonia);
  }

  // O mapa das antenas é o mapa de localizações do inquérito, com a camada de ERBs ligada — e não
  // um mapa próprio desta tela: o valor está em ver a antena JUNTO das localizações do aparelho.
  $("#ver-mapa-btn").on("click", function () {
    if (!idInquerito) return;
    window.location.href = "localizacoes.html?inquerito=" + encodeURIComponent(idInquerito) + "&erbs=1&visao=grafo";
  });

  // Botão "Prompt de IA da operadora" — conversa restrita a este retorno (ver js/ia.js e
  // funcoes/ia_investigacao.ferramentas_para_inquerito no backend)
  $("#prompt-ia-tel-btn").on("click", function () {
    window.location.href = "ia.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=telefonia&escopo_id=" + encodeURIComponent(idTelefonia) +
      "&escopo_titulo=" + encodeURIComponent(tituloTelefonia());
  });

  // Botão "Hipótese da mídia" — web/hipoteses.html filtrada nesta operadora (mesmo par de URL
  // do chat de IA por escopo); a visão do inquérito inteiro está no menu "Extras" da Investigação.
  $("#achados-analista-tel-btn").on("click", function () {
    window.location.href = "hipoteses.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=telefonia&escopo_id=" + encodeURIComponent(idTelefonia) +
      "&escopo_titulo=" + encodeURIComponent(tituloTelefonia());
  });
})();
