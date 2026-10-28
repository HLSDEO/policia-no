(function () {
  "use strict";

  var parametros = new URLSearchParams(window.location.search);
  var idInquerito = parametros.get("inquerito");
  var inqueritoAtual = null;
  var dadosAtuais = null;
  var filtroFonte = "todos";
  var timerRelatorio = null;
  var timerAtualizacao = null;
  var estadoExpansaoEvidencias = {};
  var preferenciaExpandirTodas = null;
  var assinaturaEstruturalAtual = "";
  var preparacaoRelatorio = null;
  var relatorioEscopoAtual = null;
  var $carregando = $("#inquerito-detalhe-carregando");
  var $erro = $("#inquerito-detalhe-erro");
  var $conteudo = $("#inquerito-detalhe-conteudo");

  function formatarNumero(valor) {
    var numero = Number(valor);
    return Number.isFinite(numero) ? numero.toLocaleString("pt-BR") : "—";
  }

  function formatarMoeda(valor) {
    var numero = Number(valor);
    if (!Number.isFinite(numero)) return "—";
    return numero.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function totalNumerico(obj, campos) {
    return campos.reduce(function (total, campo) {
      return total + (Number(obj && obj[campo]) || 0);
    }, 0);
  }

  function dataHora(valor) {
    if (!valor) return "não informada";
    var data = new Date(String(valor).replace(/^((?:\d{4})-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)$/, "$1Z"));
    return Number.isNaN(data.getTime()) ? String(valor) : data.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function formatarIdInquerito(nu) {
    if (typeof window !== "undefined" && window.AnalyserUtils && typeof window.AnalyserUtils.formatarNumeroInquerito === "function") {
      return window.AnalyserUtils.formatarNumeroInquerito(nu);
    }
    if (!nu) return "";
    var str = String(nu).trim();
    var digitos = str.replace(/\D/g, "");
    if (digitos.length === 11) return digitos.slice(0, 4) + "." + digitos.slice(4);
    if (digitos.length === 13) return digitos.slice(0, 4) + "." + digitos.slice(4, 11) + "-" + digitos.slice(11);
    if (digitos.length > 4 && digitos.length < 11) return digitos.slice(0, 4) + "." + digitos.slice(4);
    return str;
  }

  function formatarDataSimples(valor) {
    if (typeof window !== "undefined" && window.AnalyserUtils && typeof window.AnalyserUtils.formatarDataSimples === "function") {
      return window.AnalyserUtils.formatarDataSimples(valor);
    }
    if (!valor) return "";
    var p = String(valor).slice(0, 10).split("-");
    if (p.length === 3) return p[2] + "/" + p[1] + "/" + p[0];
    return String(valor);
  }

  function apelidoExtracao(e) {
    return $.trim(e && e.no_apelido ? String(e.no_apelido) : "");
  }

  function editarApelidoExtracao(e) {
    var atual = apelidoExtracao(e);
    Dialogo.prompt({
      titulo: atual ? "Editar apelido da extração" : "Dar apelido à extração",
      mensagem: "Nome curto para identificar este aparelho/conta no card (ex.: Celular do João). Deixe em branco para remover. Só identificação — não altera o arquivo nem os dados.",
      valor: atual,
      placeholder: "Ex.: Celular do João",
      maxlength: 200,
      rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return;
      var apelido = $.trim(texto || "");
      if (apelido === atual) return;
      $.ajax({
        url: "/api/investigacao/extracoes/" + e.id_extracao + "/apelido",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ apelido: apelido })
      }).done(function () {
        e.no_apelido = apelido || null;
        if (window.Toast) Toast.sucesso(apelido ? "Apelido salvo." : "Apelido removido.");
        carregar();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o apelido." });
      });
    });
  }

  function respostaAjax(valor) {
    return Array.isArray(valor) && valor.length >= 2 && typeof valor[1] === "string" ? valor[0] : valor;
  }

  function listaOuVazia(valor) {
    valor = respostaAjax(valor);
    return Array.isArray(valor) ? valor : [];
  }

  function requisicao(url, reserva) {
    return $.getJSON(url).then(function (resposta) {
      return respostaAjax(resposta);
    }, function () {
      return reserva;
    });
  }

  function icone(nome, classe) {
    return window.Icones ? window.Icones.html(nome, classe || "") : "";
  }

  var ICONE_MENSAGEIRO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var CORES_MENSAGEIRO = {
    Instagram: "#c13584", Telegram: "#229ed9", Facebook: "#1877f2", "Facebook Messenger": "#0084ff",
    Discord: "#5865f2", TikTok: "#111111", Twitter: "#1da1f2", X: "#111111", Signal: "#3a76f0",
    Snapchat: "#b8a300", Skype: "#00aff0", Viber: "#7360f2", WeChat: "#07c160", LINE: "#06c755",
    Kik: "#5bc236", Threema: "#2b7a2b", "WhatsApp Business": "#128c7e", ChatGPT: "#8696a0"
  };

  function aplicarIconesEstaticos() {
    $("[data-icon]").each(function () {
      $(this).html(icone($(this).attr("data-icon")));
    });
  }

  function statusVisual(item) {
    var status = String(item && item.cd_status || "").toLowerCase();
    if (item && item.tem_extracao_em_andamento) return { texto: "Em análise", classe: "processando" };
    if (status === "pendente" || status === "processando") return { texto: "Em processamento", classe: "processando" };
    if (status === "erro") return { texto: "Falhou", classe: "erro" };
    if (status === "cancelado") return { texto: "Cancelado", classe: "neutro" };
    return { texto: "Processado", classe: "concluido" };
  }

  var FASES_PROCESSAMENTO = {
    extraindo: "Extraindo dados",
    indexando: "Indexando anexos",
    hasheando: "Calculando hashes",
    contagens: "Consolidando índices",
    identificando_pessoas: "Identificando pessoas",
    transcrevendo: "Transcrevendo áudios",
    indexando_semantica: "Indexando busca semântica",
    contas: "Lendo contas",
    lancamentos: "Carregando lançamentos",
    contrapartes: "Consolidando contrapartes",
    sintese: "Gerando síntese",
    // Nuvem IPED
    gravando_conversas: "Montando conversas",
    copiando: "Copiando prova",
    cancelando: "Cancelando importação"
  };

  function adicionarProgressoOperacional(ev, item, rotulo) {
    item = item || {};
    var status = String(item.cd_status || item.cd_analise || "concluido").toLowerCase();
    var fase = String(item.cd_fase || "").toLowerCase();
    var emAndamento = status === "pendente" || status === "processando";
    var percentual = Number(item.qt_progresso_fase);
    var temPercentual = Number.isFinite(percentual);
    var transcrevendo = fase === "transcrevendo";
    var tituloFase = transcrevendo ? "Transcrição de áudio" : (FASES_PROCESSAMENTO[fase] || rotulo + " em andamento");
    if (emAndamento) {
      var $compacto = $("<span>").addClass("inquerito-evidencia-andamento-compacto").appendTo(ev.$acoes);
      $("<span>").html(icone(transcrevendo ? "ligacoes" : "timeline")).appendTo($compacto);
      $("<span>").addClass("inquerito-evidencia-andamento-texto")
        .text((transcrevendo ? "Transcrição de áudio" : (FASES_PROCESSAMENTO[fase] || "Processando")) + (temPercentual ? " · " + formatarNumero(percentual) + "%" : ""))
        .appendTo($compacto);
    }
    var $grupo = $("<div>").addClass("inquerito-evidencia-progresso").insertBefore(ev.$resumo);
    var $linha = $("<div>").addClass("inquerito-evidencia-progresso-item").toggleClass("transcricao", transcrevendo).appendTo($grupo);
    $("<span>").addClass("inquerito-evidencia-progresso-icone").html(icone(transcrevendo ? "ligacoes" : (emAndamento ? "timeline" : "arquivos"))).appendTo($linha);
    var $texto = $("<span>").addClass("inquerito-evidencia-progresso-texto").appendTo($linha);
    $("<strong>").text(emAndamento ? tituloFase : (status === "erro" ? "Falha em " + rotulo.toLowerCase() : rotulo + " concluída")).appendTo($texto);
    $("<small>").addClass("inquerito-evidencia-progresso-percentual").text(emAndamento && temPercentual ? formatarNumero(percentual) + "% concluído" : (status === "erro" ? "Falha no processamento" : "100% processado")).appendTo($texto);
    var valor = emAndamento ? (temPercentual ? Math.max(0, Math.min(100, percentual)) : 18) : (status === "erro" ? 0 : 100);
    $("<span>").addClass("inquerito-evidencia-progresso-barra").attr({ role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(valor) })
      .append($("<span>").css("--valor", valor / 100)).appendTo($linha);

  }

  function podeGerenciar(inq) {
    return !!(inq && inq.pode_gerenciar);
  }

  function mostrarErro(mensagem) {
    $carregando.addClass("hidden");
    $conteudo.addClass("hidden");
    $("#inquerito-detalhe-erro-texto").text(mensagem || "Verifique o endereço ou volte à seleção de inquéritos.");
    $erro.removeClass("hidden");
  }

  function urlEscopo(pagina, tipo, idEscopo, titulo) {
    // "Hipótese da mídia" (web/hipoteses.html filtrada nesta mídia) — sucessora dos antigos
    // achados por documento; a visão do inquérito inteiro fica no link próprio do detalhe.
    if (pagina === "achados") pagina = "hipoteses";
    return pagina + ".html?inquerito=" + encodeURIComponent(idInquerito) +
      "&escopo_tipo=" + encodeURIComponent(tipo) +
      "&escopo_id=" + encodeURIComponent(idEscopo) +
      "&escopo_titulo=" + encodeURIComponent(titulo);
  }

  function configurarLinks(inq) {
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito);
    $("#inquerito-detalhe-mesa, #inquerito-detalhe-mesa-resumo").attr("href", "mesa_inquerito.html" + qs);
    $("#inquerito-detalhe-ia, #inquerito-detalhe-ia-resumo").attr("href", "ia.html" + qs);
    $("#inquerito-detalhe-marcacoes").attr("href", "marcacoes.html" + qs);
    $("#inquerito-detalhe-achados-analista").attr("href", "hipoteses.html" + qs);
  }

  function renderKpi(rotulo, valor, detalhe, classe) {
    var $item = $("<article>").addClass("inquerito-detalhe-kpi" + (classe ? " " + classe : ""));
    $("<strong>").text(formatarNumero(valor)).appendTo($item);
    $("<span>").text(rotulo).appendTo($item);
    $("<small>").text(detalhe || "").appendTo($item);
    return $item;
  }

  function adicionarItem($lista, titulo, texto, classe) {
    var $item = $("<li>").addClass(classe || "");
    if (titulo) $("<strong>").text(titulo).appendTo($item);
    if (texto) {
      if (titulo) $item.append(document.createTextNode(" — "));
      $item.append(document.createTextNode(texto));
    }
    $lista.append($item);
  }

  function achadosDeDocumentos(documentos) {
    var achados = [];
    (documentos || []).forEach(function (documento) {
      var itens = documento.jq_achados;
      if (typeof itens === "string") {
        try { itens = JSON.parse(itens); } catch (e) { itens = []; }
      }
      if (!Array.isArray(itens)) return;
      itens.forEach(function (achado) {
        var texto = typeof achado === "string" ? achado : (achado && (achado.texto || achado.descricao || achado.nome || achado.valor));
        if (texto) achados.push({ titulo: documento.no_arquivo || "Documento analisado", texto: String(texto) });
      });
    });
    return achados;
  }

  function renderAchados(dados, inq) {
    var achados = achadosDeDocumentos(dados.documentos);
    var convergencias = dados.convergencias;
    var total = achados.length + convergencias.length;
    var $resumo = $("#inquerito-detalhe-achados").empty();
    var $detalhes = $("#inquerito-detalhe-achados-detalhes").empty();
    var $convergencias = $("#inquerito-detalhe-convergencias").empty();

    if (achados.length) adicionarItem($resumo, "Achados documentais", formatarNumero(achados.length) + " sinal(is) estruturado(s) em documentos e peças.");
    if (convergencias.length) adicionarItem($resumo, "Convergências", formatarNumero(convergencias.length) + " identificador(es) compartilhado(s) com outros inquéritos.");
    if (inq.tem_extracao_em_andamento) adicionarItem($resumo, "Processamento em andamento", "Os números podem mudar enquanto as fontes são indexadas.");
    if (!$resumo.children().length) adicionarItem($resumo, "", "Nenhum sinal adicional registrado ainda.", "sem-dados");

    achados.forEach(function (achado) { adicionarItem($detalhes, achado.titulo, achado.texto); });
    if (!achados.length) adicionarItem($detalhes, "", "Nenhum achado estruturado disponível.", "sem-dados");

    convergencias.forEach(function (item) {
      var caso = (item.nu_inquerito_relacionado ? formatarIdInquerito(item.nu_inquerito_relacionado) : null) || item.id_inquerito_relacionado || "Outro inquérito";
      var identificador = [item.tp_identificador, item.co_valor].filter(Boolean).join(": ");
      adicionarItem($convergencias, caso, identificador || "Identificador compartilhado detectado.");
    });
    if (!convergencias.length) adicionarItem($convergencias, "", "Nenhuma convergência registrada.", "sem-dados");

    $("#inquerito-detalhe-achados-total").text(formatarNumero(total) + " sinais");
    $("#inquerito-tab-achados-total").text(formatarNumero(total));
    renderConvergenciaTopo(convergencias);
  }

  function renderConvergenciaTopo(convergencias) {
    var $alvo = $("#inquerito-detalhe-convergencia").empty();
    if (!convergencias || !convergencias.length) {
      $alvo.addClass("hidden");
      return;
    }
    var casos = {};
    convergencias.forEach(function (item) {
      var chave = item.id_inquerito_relacionado || item.nu_inquerito_relacionado || "outro";
      if (!casos[chave]) casos[chave] = { id: item.id_inquerito_relacionado, numero: (item.nu_inquerito_relacionado ? formatarIdInquerito(item.nu_inquerito_relacionado) : null) || "outro inquérito", sinais: {} };
      casos[chave].sinais[[item.tp_identificador, item.co_valor].filter(Boolean).join(":") || String(item.id_convergencia || convergencias.indexOf(item))] = true;
    });
    var grupos = Object.keys(casos).map(function (chave) {
      return { id: casos[chave].id, numero: casos[chave].numero, quantidade: Object.keys(casos[chave].sinais).length };
    }).sort(function (a, b) { return b.quantidade - a.quantidade; });
    var principal = grupos[0];
    var $botao = $("<button>").attr("type", "button").appendTo($alvo);
    $("<span>").addClass("inquerito-convergencia-icone").attr("aria-hidden", "true").text("⚠").appendTo($botao);
    var $texto = $("<span>").appendTo($botao);
    $("<strong>").text("Convergência detectada").appendTo($texto);
    var $linhaTexto = $("<small>").appendTo($texto);
    $linhaTexto.append(document.createTextNode("Este inquérito compartilha "));
    $("<strong>").text(formatarNumero(principal.quantidade)).appendTo($linhaTexto);
    $linhaTexto.append(document.createTextNode(" identificador(es) com " + principal.numero + (grupos.length > 1 ? " · mais " + formatarNumero(grupos.length - 1) + " caso(s)" : "")));
    $("<span>").addClass("inquerito-convergencia-ver").text("Ver cruzamentos").appendTo($botao);
    $botao.on("click", function () { window.location.href = "convergencia.html?inquerito=" + encodeURIComponent(idInquerito); });
    $alvo.removeClass("hidden");
  }

  function renderContatos(inq, dados) {
    var resposta = dados.contatos || {};
    var contatos = Array.isArray(resposta) ? resposta : (resposta.itens || []);
    var total = resposta.total === undefined ? totalNumerico(inq, ["qt_contatos"]) : resposta.total;
    $("#inquerito-detalhe-contatos-total").text(formatarNumero(total) + " identificadores");
    $("#inquerito-tab-contatos-total").text(formatarNumero(total));
    var $alvo = $("#inquerito-detalhe-contatos").empty();
    if (!contatos.length) {
      $alvo.append($("<p>").addClass("inquerito-detalhe-sem-dados").text(total ? "Os contatos estão disponíveis na tela própria de contatos." : "Nenhum contato registrado ainda."));
      return;
    }
    contatos.forEach(function (contato) {
      var $item = $("<article>").addClass("inquerito-detalhe-contato");
      $("<span>").addClass("inquerito-contato-icone").html(icone("contatos")).appendTo($item);
      var $texto = $("<div>").appendTo($item);
      $("<strong>").text(contato.no_contato || "Contato sem nome").appendTo($texto);
      $("<span>").text([contato.no_aplicativo_origem, contato.entradas ? formatarNumero(contato.entradas) + " entradas" : ""].filter(Boolean).join(" · ") || "Identificador encontrado").appendTo($texto);
      $alvo.append($item);
    });
  }

  function botaoAcao($destino, rotulo, iconeNome, href, classe, onClick) {
    var $acao = href ? $("<a>").attr("href", href) : $("<button>").attr("type", "button");
    $acao.addClass("inquerito-evidencia-acao" + (classe ? " " + classe : ""));
    if (iconeNome) $acao.append($("<span>").html(icone(iconeNome)));
    $acao.append(document.createTextNode(rotulo));
    if (onClick) $acao.on("click", onClick);
    return $acao.appendTo($destino);
  }

  function botaoAcaoRapida($destino, rotulo, iconeNome, href, onClick, motivoIndisponivel) {
    if (href || onClick) return botaoAcao($destino, rotulo, iconeNome, href, "rapida", onClick);
    return botaoAcao($destino, rotulo, iconeNome, null, "rapida indisponivel")
      .prop("disabled", true)
      .attr("title", motivoIndisponivel || "Esta ação ainda não está disponível para a evidência.");
  }

  function iconeDispositivo(e) {
    var texto = [e.no_fabricante, e.no_dispositivo, e.no_modelo, e.no_sistema_operacional, e.no_plataforma].filter(Boolean).join(" ").toLowerCase();
    if (/apple|iphone|ipad|\bios\b/.test(texto)) {
      return '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><path d="M17.05 12.54c-.03-2.6 2.12-3.85 2.22-3.91-1.21-1.77-3.09-2.01-3.76-2.04-1.6-.16-3.12.94-3.93.94-.81 0-2.06-.92-3.39-.9-1.74.03-3.35 1.01-4.25 2.57-1.81 3.14-.46 7.79 1.3 10.34.86 1.25 1.89 2.65 3.24 2.6 1.3-.05 1.79-.84 3.36-.84 1.57 0 2.01.84 3.39.81 1.4-.02 2.28-1.27 3.13-2.53.99-1.45 1.4-2.85 1.42-2.92-.03-.01-2.72-1.04-2.75-4.12M14.62 4.87c.71-.87 1.19-2.07 1.06-3.27-1.02.04-2.26.68-3 1.54-.66.77-1.24 2-1.08 3.17 1.14.09 2.3-.58 3.02-1.44"/></svg>';
    }
    if (/android|samsung|motorola|xiaomi|redmi|huawei|honor|asus|realme|poco|infinix|tecno|oppo|vivo|nokia|lenovo|zte|oneplus|pixel|lg(?![a-z])/.test(texto)) {
      return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10a5 5 0 0 1 10 0"/><line x1="8.2" y1="4" x2="9.3" y2="6"/><line x1="15.8" y1="4" x2="14.7" y2="6"/><line x1="9.5" y1="8" x2="9.51" y2="8"/><line x1="14.5" y1="8" x2="14.51" y2="8"/><path d="M6 11h12v6a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2z"/><line x1="9.5" y1="19" x2="9.5" y2="22"/><line x1="14.5" y1="19" x2="14.5" y2="22"/><line x1="3" y1="11.5" x2="3" y2="16"/><line x1="21" y1="11.5" x2="21" y2="16"/></svg>';
    }
    return icone("aparelho");
  }

  function modeloDoAparelho(e) {
    var fabricante = $.trim(e.no_fabricante || "");
    var modelo = $.trim(e.no_dispositivo || "");
    if (fabricante && modelo) {
      var primeiraPalavra = modelo.toLowerCase().split(" ")[0];
      var jaTemMarca = modelo.toLowerCase().indexOf(fabricante.toLowerCase()) !== -1 ||
        fabricante.toLowerCase().indexOf(primeiraPalavra) === 0;
      if (!jaTemMarca) modelo = fabricante + " " + modelo;
    }
    return (modelo || fabricante || "Aparelho não identificado").toUpperCase();
  }

  function sistemaDoAparelho(e) {
    var texto = [e.no_fabricante, e.no_dispositivo, e.no_modelo_detectado].filter(Boolean).join(" ").toLowerCase();
    if (/apple|iphone|ipad|ios/.test(texto)) return "ios";
    if (/android|samsung|motorola|xiaomi|redmi|huawei|honor|asus|realme|poco|infinix|tecno|oppo|vivo|nokia|lenovo|zte|oneplus|pixel|lg(?![a-z])/.test(texto)) return "android";
    return "";
  }

  function resumoDoAparelho(e) {
    var sistema = sistemaDoAparelho(e);
    var conta = sistema === "android" ? (e.no_conta_google || e.no_conta_apple) : (e.no_conta_apple || e.no_conta_google);
    var partes = [];
    [e.no_nome_dispositivo, conta, e.no_modelo_detectado].forEach(function (valor) {
      valor = $.trim(valor || "");
      if (valor && !partes.some(function (item) { return item.toLowerCase() === valor.toLowerCase(); })) partes.push(valor);
    });
    partes.push(formatarNumero(e.qt_conversas) + " WhatsApp (" + formatarNumero(e.qt_mensagens) + " msgs)");
    partes.push(formatarNumero(e.qt_localizacoes) + " localizações");
    if (e.qt_notas != null) partes.push(formatarNumero(e.qt_notas) + " notas");
    partes.push(formatarNumero(e.qt_contas_financeiras) + " contas financeiras");
    if (e.qt_chaves_pix != null) partes.push(formatarNumero(e.qt_chaves_pix) + " chaves PIX");
    if (e.qt_pessoas_identificadas != null) partes.push(formatarNumero(e.qt_pessoas_identificadas) + " pessoas identificadas");
    if (e.qt_redes_wifi != null) partes.push(formatarNumero(e.qt_redes_wifi) + " redes Wi-Fi");
    return partes.join(" · ");
  }

  function criarEvidencia(tipo, titulo, subtitulo, iconeNome, status) {
    var $card = $("<article>").addClass("inquerito-evidencia").attr("data-tipo", tipo);
    var $cabecalho = $("<header>").addClass("inquerito-evidencia-cabecalho").appendTo($card);
    var $identidade = $("<div>").addClass("inquerito-evidencia-identidade").appendTo($cabecalho);
    var $tipo = $("<span>").addClass("inquerito-evidencia-tipo " + tipo).html(icone(iconeNome)).appendTo($identidade);
    var $titulos = $("<div>").appendTo($identidade);
    $("<h3>").text(titulo).appendTo($titulos);
    if (subtitulo) $("<p>").text(subtitulo).appendTo($titulos);
    var $rapidas = $("<div>").addClass("inquerito-evidencia-acoes-rapidas").appendTo($card);
    $("<span>").addClass("inquerito-evidencia-acoes-rotulo").text("Ações rápidas").appendTo($rapidas);
    var $botoesRapidos = $("<div>").addClass("inquerito-evidencia-acoes-botoes").appendTo($rapidas);
    var $acoes = $("<div>").addClass("inquerito-evidencia-acoes").appendTo($cabecalho);
    if (status) $("<span>").addClass("inquerito-evidencia-status " + status.classe).text(status.texto).appendTo($acoes);
    var $menuWrap = $("<div>").addClass("inquerito-evidencia-menu-wrap").appendTo($acoes);
    var $menuBtn = $("<button>").attr({ type: "button", "aria-expanded": "false", "aria-label": "Mais ações", title: "Mais ações" })
      .addClass("inquerito-evidencia-menu-btn").text("⋮").appendTo($menuWrap);
    var $menuAcoes = $("<div>").addClass("inquerito-evidencia-menu hidden").appendTo($menuWrap);
    $menuBtn.on("click", function (evento) {
      evento.stopPropagation();
      var abrir = $menuAcoes.hasClass("hidden");
      $(".inquerito-evidencia-menu").addClass("hidden");
      $(".inquerito-evidencia-menu-btn").attr("aria-expanded", "false");
      $(".inquerito-evidencia").removeClass("menu-aberto");
      $menuAcoes.toggleClass("hidden", !abrir);
      $menuBtn.attr("aria-expanded", String(abrir));
      $card.toggleClass("menu-aberto", abrir);
    });
    var $corpo = $("<div>").addClass("inquerito-evidencia-corpo").appendTo($card);
    var $resumo = $("<p>").addClass("inquerito-evidencia-resumo").appendTo($corpo);
    var $grupos = $("<div>").addClass("inquerito-evidencia-grupos").appendTo($corpo);
    var $grupoPrincipais = $("<section>").addClass("inquerito-evidencia-grupo principais").appendTo($grupos);
    $("<span>").addClass("inquerito-evidencia-grupo-titulo").text("Principais").appendTo($grupoPrincipais);
    var $pillsPrincipais = $("<div>").addClass("inquerito-evidencia-pills").appendTo($grupoPrincipais);
    var $grupoOutros = $("<section>").addClass("inquerito-evidencia-grupo outros hidden").appendTo($grupos);
    $("<span>").addClass("inquerito-evidencia-grupo-titulo").text("Demais").appendTo($grupoOutros);
    var $pillsOutros = $("<div>").addClass("inquerito-evidencia-pills").appendTo($grupoOutros);
    var $expandir = $("<button>").attr({ type: "button", "aria-expanded": "false", title: "Expandir evidência" })
      .addClass("inquerito-evidencia-expandir").html(icone("chevronDown")).appendTo($acoes);
    $expandir.on("click", function () {
      var aberta = !$card.hasClass("aberta");
      preferenciaExpandirTodas = null;
      definirEvidenciaAberta($card, aberta);
      atualizarControleEvidencias();
    });
    $card.on("click", function (evento) {
      if (haTextoSelecionado()) return;
      if ($(evento.target).closest("a, button, input, select, textarea, label, summary, [role='button'], [role='link'], [contenteditable], .inquerito-evidencia-menu, .inquerito-evidencia-menu-wrap, .inquerito-evidencia-acoes-rapidas").length) return;
      if ($card.hasClass("aberta") && !$(evento.target).closest(".inquerito-evidencia-cabecalho").length) return;
      $expandir.trigger("click");
    });
    return { $card: $card, $tipo: $tipo, $acoes: $acoes, $menuAcoes: $menuAcoes, $acoesRapidas: $botoesRapidos, $resumo: $resumo, $pills: $pillsPrincipais, $pillsPrincipais: $pillsPrincipais, $pillsOutros: $pillsOutros, $grupoOutros: $grupoOutros };
  }

  function definirEvidenciaAberta($card, aberta, salvarEstado) {
    $card.toggleClass("aberta", aberta);
    $card.find(".inquerito-evidencia-expandir")
      .attr("aria-expanded", String(aberta))
      .attr("title", aberta ? "Recolher evidência" : "Expandir evidência");
    if (salvarEstado !== false) {
      var chave = $card.attr("data-evidencia-chave");
      if (chave) estadoExpansaoEvidencias[chave] = aberta;
    }
  }

  function evidenciasVisiveis() {
    return $("#inquerito-detalhe-evidencias .inquerito-evidencia").not(".hidden");
  }

  function atualizarControleEvidencias() {
    var $visiveis = evidenciasVisiveis();
    var todasAbertas = $visiveis.length > 0 && $visiveis.filter(".aberta").length === $visiveis.length;
    $("#inquerito-evidencias-toggle")
      .prop("disabled", !$visiveis.length)
      .toggleClass("recolher", todasAbertas)
      .attr("aria-expanded", String(todasAbertas));
    $("#inquerito-evidencias-toggle-texto").text(todasAbertas ? "Recolher todas" : "Expandir todas");
  }

  function preencherAcoesRapidas(ev, inq, opcoes) {
    opcoes = opcoes || {};
    botaoAcaoRapida(ev.$acoesRapidas, "Resumo", "investigacao", null, function () {
      if (ev.$card.attr("data-tipo") === "ufdr") {
        abrirResumoCelular(opcoes.id, opcoes.titulo);
        return;
      }
      // com a evidência processada, o resumo é gerado sob demanda pelo chat escopado dela
      // (?pergunta= pré-preenche a pergunta — ver js/ia.js); sem processamento, o diálogo
      // continua mostrando o que o cartão já sabe
      if (opcoes.prompt) {
        window.location.href = opcoes.prompt + "&pergunta=" + encodeURIComponent(
          "Resuma esta evidência para o relatório de inteligência: o que é, o período coberto, " +
          "as pessoas envolvidas e os 5 pontos mais relevantes para o inquérito.");
        return;
      }
      Dialogo.alerta({
        titulo: "Resumo · " + opcoes.titulo,
        mensagem: $.trim(ev.$resumo.text()) || "Nenhum resumo disponível para esta evidência."
      });
    });
    var motivoPadrao = "Disponível após o processamento da evidência.";
    var $ia = botaoAcaoRapida(ev.$acoesRapidas, "Assistente de IA", null, opcoes.prompt, null,
      opcoes.motivoPrompt || motivoPadrao);
    $ia.addClass("inquerito-acao--feature inquerito-acao--ia").empty()
      .append('<span class="orbit-ai-stars" aria-hidden="true"><span>✦</span><span>✧</span><span>✦</span></span>')
      .append($("<span>").addClass("orbit-action-label").text("Assistente de IA"));
    botaoAcaoRapida(ev.$acoesRapidas, "Hipótese da mídia", "notas", opcoes.achados, null,
      opcoes.motivoAchados || motivoPadrao);
  }

  $(document).on("click", function () {
    $(".inquerito-evidencia-menu").addClass("hidden");
    $(".inquerito-evidencia-menu-btn").attr("aria-expanded", "false");
    $(".inquerito-evidencia").removeClass("menu-aberto");
  });

  function adicionarPill($destino, rotulo, valor, href, iconeNome, classe) {
    var semContagem = valor === true;
    if (valor === undefined || valor === null || valor === false) return null;
    if (!semContagem && (!Number.isFinite(Number(valor)) || Number(valor) <= 0)) return null;
    var $pill = href ? $("<a>").attr("href", href) : $("<span>");
    $pill.addClass("inquerito-evidencia-pill" + (classe ? " " + classe : ""));
    if (iconeNome) $pill.append($("<span>").html(icone(iconeNome)));
    $("<strong>").text(rotulo).appendTo($pill);
    if (!semContagem) $("<small>").text(formatarNumero(valor)).appendTo($pill);
    $destino.append($pill);
    $destino.closest(".inquerito-evidencia-grupo").removeClass("hidden");
    return $pill;
  }

  function adicionarDisponibilidadeArquivo(ev, item) {
    if (typeof item.in_arquivo_presente !== "boolean") return;
    $("<span>").addClass("inquerito-evidencia-arquivo " + (item.in_arquivo_presente ? "disponivel" : "indisponivel"))
      .text(item.in_arquivo_presente ? "Arquivo disponível" : "Arquivo indisponível")
      .attr("title", item.in_arquivo_presente && item.qt_bytes_arquivo ? formatarNumero(item.qt_bytes_arquivo) + " bytes disponíveis" : "")
      .appendTo(ev.$acoes);
  }

  function textoResumo(partes) {
    return partes.filter(function (parte) { return parte && Number(parte.valor) > 0; })
      .map(function (parte) { return formatarNumero(parte.valor) + " " + parte.rotulo; }).join(" · ");
  }

  function mostrarHashes(titulo, item) {
    var linhas = [];
    if (item.co_hash_md5) linhas.push("MD5: " + item.co_hash_md5);
    if (item.co_hash_sha1) linhas.push("SHA-1: " + item.co_hash_sha1);
    if (item.co_hash_sha256) linhas.push("SHA-256: " + item.co_hash_sha256);
    if (item.hashes_arquivos) {
      var hashes = item.hashes_arquivos;
      if (typeof hashes === "string") {
        try { hashes = JSON.parse(hashes); } catch (e) { hashes = [hashes]; }
      }
      (Array.isArray(hashes) ? hashes : [hashes]).forEach(function (hash) {
        if (typeof hash === "string") linhas.push(hash);
        else if (hash) linhas.push([hash.no_arquivo, hash.co_hash_sha256 || hash.co_hash_sha1 || hash.co_hash_md5].filter(Boolean).join(": "));
      });
    }
    Dialogo.alerta({ titulo: "Hash · " + titulo, mensagem: linhas.filter(Boolean).join("\n") || "Nenhum hash disponível para esta evidência." });
  }

  /* ---------- ficha de custódia (ZIP: FICHA txt + metadados json) ----------
   * O "Hash" mostra na tela; a ficha leva o mesmo conteúdo em pacote assinável:
   * js/utils/exportar_custodia.js monta o zip com quem exportou e quando — o par do
   * relatório Word na hora de carregar a prova para o perito. */
  var CAMPOS_CUSTODIA = [
    ["no_arquivo", "Arquivo de origem"], ["no_pasta", "Pasta"], ["no_conta", "Conta"],
    ["no_operadora", "Operadora"], ["nu_rif", "Número do RIF"], ["nu_ipei", "Número do IPEI"],
    ["nu_ipl", "Número do IPL"], ["no_titular", "Titular"], ["nu_cpf", "CPF do titular"],
    ["tp_fonte", "Tipo de fonte"], ["cd_status", "Situação"], ["cd_analise", "Análise"],
    ["in_arquivo_presente", "Arquivo disponível no servidor"], ["dt_processado", "Processado em"],
    ["extraido_por", "Extraído por"], ["anexado_por", "Anexado por"], ["tx_erro", "Erro registrado"]
  ];

  function metaCustodia(tipo, titulo, item, extra) {
    var meta = {
      "Tipo de evidência": tipo,
      "Evidência": titulo,
    };
    CAMPOS_CUSTODIA.forEach(function (par) {
      var v = item[par[0]];
      if (v !== undefined && v !== null && v !== "") meta[par[1]] = v === true ? "Sim" : (v === false ? "Não" : v);
    });
    if (item.co_hash_md5) meta["Hash MD5"] = item.co_hash_md5;
    if (item.co_hash_sha1) meta["Hash SHA-1"] = item.co_hash_sha1;
    if (item.co_hash_sha256) meta["Hash SHA-256"] = item.co_hash_sha256;
    return $.extend(meta, extra || {});
  }

  function acaoCustodia(ev, tipo, titulo, item, inq) {
    if (!window.ExportarCustodia || !window.ExportarExcel) return;
    botaoAcao(ev.$menuAcoes, "Ficha de custódia", "zip", null, "", function () {
      var chave = ev.$card.attr("data-evidencia-chave") || "evidencia";
      try {
        var zip = ExportarCustodia.gerar({
          nome: tipo,
          meta: metaCustodia(tipo, titulo, item, {
            "Inquérito": (inq && (inq.nu_inquerito || inq.id_inquerito)) || idInquerito || "—",
          }),
        });
        ExportarExcel.baixarBlob(zip, ExportarCustodia.nomeCarimbo("custodia_" + chave.replace(":", "_")));
      } catch (e) {
        if (window.Toast) Toast.erro("Não foi possível gerar a ficha de custódia.");
      }
    });
  }

  function confirmarExclusao(titulo, mensagem, url) {
    Dialogo.confirmar({ titulo: titulo, mensagem: mensagem, rotuloOk: "Apagar", perigoso: true }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: url, method: "DELETE" }).done(function () {
        carregar();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível apagar esta evidência." });
      });
    });
  }

  function renderNuvem(e, inq) {
    var fabricante = (e.no_fabricante || "").toLowerCase();
    var rotuloNuvem = fabricante.indexOf("apple") !== -1 ? "Nuvem Apple"
                    : fabricante.indexOf("google") !== -1 ? "Nuvem Google"
                    : "Nuvem";
    var conta = (e.no_dispositivo || "").trim();
    var apelido = apelidoExtracao(e);
    var tituloBase = rotuloNuvem + (conta ? " · " + conta : "");
    var titulo = apelido ? apelido + " · " + rotuloNuvem : tituloBase;
    var tituloEscopo = (apelido ? apelido + " — " : "") + rotuloNuvem + (conta ? " — " + conta : "") + (e.no_arquivo ? " — " + e.no_arquivo : "");
    var status = statusVisual(e);
    var ev = criarEvidencia("nuvem", titulo, e.no_arquivo || ("Conta " + rotuloNuvem), "nuvem", status);
    adicionarDisponibilidadeArquivo(ev, e);
    ev.$card.attr("data-evidencia-chave", "ufdr:" + e.id_extracao);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(e.id_extracao);
    var processada = String(e.cd_status || "concluido") === "concluido";

    botaoAcao(ev.$menuAcoes, "Hash", "hash", null, "", function () { mostrarHashes(tituloEscopo, e); });
    acaoCustodia(ev, "Conta na nuvem", tituloEscopo, e, inq);
    botaoAcao(ev.$menuAcoes, apelido ? "Editar apelido" : "Dar apelido", "prompts", null, "", function () {
      editarApelidoExtracao(e);
    });

    if (podeGerenciar(inq)) {
      botaoAcao(ev.$menuAcoes, "Compartilhar prova", "compartilhar",
        "investigacao.html" + qs + "&aba=celulares&tema_rota=ignorar");
      botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
        confirmarExclusao("Apagar extração da nuvem", "Apagar " + tituloEscopo + " e todos os dados extraídos desta conta? Esta ação não pode ser desfeita.", "/api/investigacao/extracoes/" + e.id_extracao);
      });
    }

    var partes = [];
    function addContagem(campo, rotulo) {
      if (Number(e[campo]) > 0) partes.push(formatarNumero(e[campo]) + " " + rotulo);
    }
    addContagem("qt_arquivos", "arquivos");
    addContagem("qt_emails", "e-mails");
    addContagem("qt_contatos", "contatos");
    addContagem("qt_sms_mensagens", "SMS");
    addContagem("qt_msg_mensagens", "mensagens");
    addContagem("qt_localizacoes", "localizações");
    addContagem("qt_navegacao", "páginas visitadas");
    addContagem("qt_notas", "notas");
    addContagem("qt_aplicativos", "aplicativos");
    addContagem("qt_buscas", "buscas");
    addContagem("qt_atividades", "atividades");
    addContagem("qt_eventos", "eventos de agenda");
    addContagem("qt_acessos", "acessos");
    addContagem("qt_dispositivos", "dispositivos");
    addContagem("qt_perfil", "perfis de conta");
    addContagem("qt_metadados_nuvem", "arquivos com metadado de nuvem");

    var resumoTexto = partes.join(" · ") || "Conta na nuvem vinculada ao inquérito.";
    if (e.tx_aviso) {
      ev.$resumo.html($("<span>").text(resumoTexto).html() + "<br/><span style='color:var(--warning);'>⚠ " + $("<span>").text(e.tx_aviso).html() + "</span>");
    } else {
      ev.$resumo.text(resumoTexto);
    }

    adicionarProgressoOperacional(ev, e, "Extração da nuvem");
    preencherAcoesRapidas(ev, inq, {
      tipo: "extracao", id: e.id_extracao, titulo: tituloEscopo,
      prompt: processada ? urlEscopo("ia", "extracao", e.id_extracao, tituloEscopo) : null,
      motivoPrompt: processada ? undefined : "A conta ainda está sendo processada — há pouco material para conversar.",
      // anotar achados não depende do processamento terminar: o analista registra a observação
      // a qualquer momento (a conversa com a IA é que precisa do material pronto)
      achados: urlEscopo("achados", "extracao", e.id_extracao, tituloEscopo)
    });

    if (Number(e.qt_sms_mensagens) > 0) {
      adicionarPill(ev.$pillsPrincipais, "SMS", e.qt_sms_mensagens, "whatsapp.html" + qs + "&fonte=sms", "sms", "azul");
    }
    if (fabricante.indexOf("apple") !== -1 || e.tem_wa_parcial) {
      adicionarPill(ev.$pillsPrincipais, "WhatsApp (parcial)", e.tem_wa_parcial || true, "whatsapp_parcial.html" + qs, "whatsapp", "verde");
    }
    adicionarPill(ev.$pillsPrincipais, "Arquivos", e.qt_arquivos, "arquivos.html" + qs, "arquivos", "verde");
    adicionarPill(ev.$pillsPrincipais, "E-mails", e.qt_emails, "emails.html" + qs, "email", "roxo");
    adicionarPill(ev.$pillsPrincipais, "Contatos", e.qt_contatos, "contatos.html" + qs, "contatos", "roxo");
    adicionarPill(ev.$pillsPrincipais, "Localizações", e.qt_localizacoes, "localizacoes.html" + qs, "localizacoes", "rosa");
    adicionarPill(ev.$pillsPrincipais, "Navegação", e.qt_navegacao, "navegacao.html" + qs, "navegacao", "azul");
    adicionarPill(ev.$pillsPrincipais, "Aplicativos", e.qt_aplicativos, "aplicativos.html" + qs, "aplicativos", "roxo");

    adicionarPill(ev.$pillsOutros, "Notas", e.qt_notas, "notas.html" + qs, "notas", "amber");
    adicionarPill(ev.$pillsOutros, "Redes sociais e buscas", totalNumerico(e, ["qt_atividade_social", "qt_buscas"]), "social_buscas.html" + qs, "navegacao", "azul");
    adicionarPill(ev.$pillsOutros, "Minha Atividade", e.qt_atividades, "atividades.html" + qs, "navegacao", "azul");
    adicionarPill(ev.$pillsOutros, "Agenda", e.qt_eventos, "agenda.html" + qs, "timeline", "amber");
    adicionarPill(ev.$pillsOutros, "Acessos", e.qt_acessos, "acessos.html" + qs, "localizacoes", "azul");
    adicionarPill(ev.$pillsOutros, "Dispositivos da conta", e.qt_dispositivos, "dispositivos_conta.html" + qs, "aparelho", "azul");
    adicionarPill(ev.$pillsOutros, "Perfil da conta", e.qt_perfil, "perfil_conta.html" + qs, "dono", "roxo");
    adicionarPill(ev.$pillsOutros, "Nuvem × aparelho", e.qt_metadados_nuvem, "correlacao_nuvem.html" + qs, "nuvem", "azul");

    if (Number(e.qt_msg_conversas) || Number(e.qt_msg_mensagens)) {
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(e.id_extracao)).done(function (apps) {
        (apps || []).forEach(function (app) {
          if (["knowledgec", "biome", "recents"].indexOf(String(app.tp_app || "").toLowerCase().replace(/[^a-z0-9]/g, "")) >= 0) return;
          var ehWhatsApp = window.MapaTelas && MapaTelas.ehAppWhatsApp(app.tp_app);
          var pagina = window.MapaTelas ? MapaTelas.paginaConversa("msg", app.tp_app) : "mensageiros.html";
          var $pill = adicionarPill(ehWhatsApp ? ev.$pillsPrincipais : ev.$pillsOutros, app.tp_app, app.qt_mensagens,
            pagina + qs + "&fonte=msg&app=" + encodeURIComponent(app.tp_app), ehWhatsApp ? "whatsapp" : "sms", "mensageiro");
          if ($pill) {
            $pill.css("--app-color", CORES_MENSAGEIRO[app.tp_app] || "#8696a0");
            if (!ehWhatsApp) $pill.children("span").first().html(ICONE_MENSAGEIRO);
          }
        });
      });
    }

    return ev.$card;
  }

  function renderUfdr(e, inq) {
    if (e.in_nuvem) return renderNuvem(e, inq);

    var modeloBase = modeloDoAparelho(e);
    var apelido = apelidoExtracao(e);
    var titulo = apelido ? apelido + " · " + modeloBase : modeloBase;
    var tituloEscopo = (apelido ? apelido + " — " : "") + (e.no_arquivo || ("Extração " + e.id_extracao)) + " — " + modeloBase;
    var status = statusVisual(e);
    var ev = criarEvidencia("ufdr", titulo, e.no_arquivo || "Extração Cellebrite / UFDR", "aparelho", status);
    ev.$tipo.html(iconeDispositivo(e));
    adicionarDisponibilidadeArquivo(ev, e);
    ev.$card.attr("data-evidencia-chave", "ufdr:" + e.id_extracao);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(e.id_extracao);
    var processada = String(e.cd_status || "concluido") === "concluido";
    botaoAcao(ev.$menuAcoes, "Hash", "hash", null, "", function () { mostrarHashes(tituloEscopo, e); });
    acaoCustodia(ev, "Extração UFDR", tituloEscopo, e, inq);
    botaoAcao(ev.$menuAcoes, apelido ? "Editar apelido" : "Dar apelido", "prompts", null, "", function () {
      editarApelidoExtracao(e);
    });
    if (podeGerenciar(inq)) {
      botaoAcao(ev.$menuAcoes, "Compartilhar ou gerenciar prova", "compartilhar",
        "investigacao.html" + qs + "&aba=celulares&tema_rota=ignorar");
      if (e.cd_status !== "processando" && e.cd_status !== "pendente") {
        botaoAcao(ev.$menuAcoes, "Transcrever áudios", "transcrever", null, "", function () {
          if (e.in_arquivo_presente === false) {
            Dialogo.alerta({ titulo: "Arquivo indisponível", mensagem: "Restaure o arquivo UFDR antes de iniciar a transcrição." });
            return;
          }
          Dialogo.confirmar({ titulo: "Transcrever áudios", mensagem: "Transcrever os áudios ainda pendentes desta evidência?", rotuloOk: "Transcrever" }).then(function (ok) {
            if (!ok) return;
            $.ajax({ url: "/api/investigacao/extracoes/" + e.id_extracao + "/transcrever", method: "POST" })
              .done(function (resposta) { if (window.Toast) Toast.sucesso((resposta && resposta.mensagem) || "Transcrição iniciada."); })
              .fail(function (xhr) { Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível iniciar a transcrição." }); });
          });
        });
      }
      botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
        confirmarExclusao("Apagar extração", "Apagar " + tituloEscopo + " e todos os dados extraídos deste aparelho? Esta ação não pode ser desfeita.", "/api/investigacao/extracoes/" + e.id_extracao);
      });
    }

    var resumoTexto = resumoDoAparelho(e);
    if (e.tx_aviso) {
      ev.$resumo.html($("<span>").text(resumoTexto).html() + "<br/><span style='color:var(--warning);'>⚠ " + $("<span>").text(e.tx_aviso).html() + "</span>");
    } else {
      ev.$resumo.text(resumoTexto);
    }
    adicionarProgressoOperacional(ev, e, "Extração");
    preencherAcoesRapidas(ev, inq, {
      tipo: "extracao", id: e.id_extracao, titulo: tituloEscopo,
      prompt: processada ? urlEscopo("ia", "extracao", e.id_extracao, tituloEscopo) : null,
      motivoPrompt: processada ? undefined : "O aparelho ainda está sendo processado — há pouco material para conversar.",
      comparar: processada ? "comparar_rosto.html?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(e.id_extracao) : null,
      // anotar achados não depende do processamento terminar
      achados: urlEscopo("achados", "extracao", e.id_extracao, tituloEscopo)
    });

    adicionarPill(ev.$pillsPrincipais, "WhatsApp", e.qt_mensagens, "whatsapp.html" + qs + "&fonte=wpp", "whatsapp", "verde");
    if (e.tem_wa_parcial) {
      adicionarPill(ev.$pillsPrincipais, "WhatsApp (parcial)", true, "whatsapp_parcial.html" + qs, "whatsapp", "verde");
    }
    adicionarPill(ev.$pillsPrincipais, "Notas", e.qt_notas, "notas.html" + qs, "notas", "amber");
    adicionarPill(ev.$pillsPrincipais, "Localizações", e.qt_localizacoes, "localizacoes.html" + qs, "localizacoes", "rosa");
    adicionarPill(ev.$pillsPrincipais, "Timeline", totalNumerico(e, ["qt_mensagens", "qt_sms_mensagens", "qt_ligacoes", "qt_localizacoes", "qt_notificacoes"]) > 0, "timeline.html" + qs, "timeline", "roxo");
    adicionarPill(ev.$pillsPrincipais, "Contas bancárias", e.qt_contas_financeiras, "contas_financeiras.html" + qs, "banco", "verde");
    adicionarPill(ev.$pillsPrincipais, "Chaves PIX", e.qt_chaves_pix, "chaves_pix.html" + qs, "pix", "verde");
    adicionarPill(ev.$pillsPrincipais, "Wi-Fi", e.qt_redes_wifi, "wifi.html" + qs, "wifi", "cyan");
    adicionarPill(ev.$pillsPrincipais, "Identidade do aparelho", totalNumerico(e, ["qt_info_aparelho", "qt_antenas"]), "identidade_aparelho.html" + qs, "aparelho", "azul");
    adicionarPill(ev.$pillsOutros, "SMS", e.qt_sms_mensagens, "whatsapp.html" + qs + "&fonte=sms", "sms", "azul");
    adicionarPill(ev.$pillsOutros, "Contatos", e.qt_contatos, "contatos.html" + qs, "contatos", "roxo");
    adicionarPill(ev.$pillsOutros, "Ligações", e.qt_ligacoes, "ligacoes.html" + qs, "ligacoes", "laranja");
    adicionarPill(ev.$pillsOutros, "Notificações", e.qt_notificacoes, "notificacoes.html" + qs, "notificacoes", "amber");
    adicionarPill(ev.$pillsOutros, "Navegação", e.qt_navegacao, "navegacao.html" + qs, "navegacao", "azul");
    adicionarPill(ev.$pillsOutros, "Aplicativos", e.qt_aplicativos, "aplicativos.html" + qs, "aplicativos", "roxo");
    adicionarPill(ev.$pillsOutros, "E-mails", e.qt_emails, "emails.html" + qs, "email", "roxo");
    adicionarPill(ev.$pillsOutros, "Arquivos", e.qt_arquivos, "arquivos.html" + qs, "arquivos", "verde");
    adicionarPill(ev.$pillsOutros, "Redes sociais e buscas", totalNumerico(e, ["qt_atividade_social", "qt_buscas"]), "social_buscas.html" + qs, "navegacao", "azul");
    adicionarPill(ev.$pillsOutros, "Dispositivos reconhecidos", e.qt_dispositivos_reconhecidos, "dispositivos_reconhecidos.html" + qs, "envolvidos", "azul");

    if (Number(e.qt_msg_conversas)) {
      $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(e.id_extracao)).done(function (apps) {
        (apps || []).forEach(function (app) {
          if (["knowledgec", "biome", "recents"].indexOf(String(app.tp_app || "").toLowerCase().replace(/[^a-z0-9]/g, "")) >= 0) return;
          var ehWhatsApp = window.MapaTelas && MapaTelas.ehAppWhatsApp(app.tp_app);
          var pagina = window.MapaTelas ? MapaTelas.paginaConversa("msg", app.tp_app) : "mensageiros.html";
          var $pill = adicionarPill(ehWhatsApp ? ev.$pillsPrincipais : ev.$pillsOutros, app.tp_app, app.qt_mensagens,
            pagina + qs + "&fonte=msg&app=" + encodeURIComponent(app.tp_app), ehWhatsApp ? "whatsapp" : "sms", "mensageiro");
          if ($pill) {
            $pill.css("--app-color", CORES_MENSAGEIRO[app.tp_app] || "#8696a0");
            if (!ehWhatsApp) $pill.children("span").first().html(ICONE_MENSAGEIRO);
          }
        });
      });
    }
    return ev.$card;
  }

  function renderRif(r, inq) {
    var titulo = "RIF " + (r.nu_rif || r.no_pasta || r.id_rif);
    var ev = criarEvidencia("rif", titulo, r.no_pasta || "Relatório de Inteligência Financeira · COAF", "rif", { texto: "Processado", classe: "concluido" });
    adicionarDisponibilidadeArquivo(ev, r);
    ev.$card.attr("data-evidencia-chave", "rif:" + r.id_rif);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&rif=" + encodeURIComponent(r.id_rif);
    botaoAcao(ev.$menuAcoes, "Hash", "hash", null, "", function () { mostrarHashes(titulo, r); });
    acaoCustodia(ev, "RIF", titulo, r, inq);
    if (podeGerenciar(inq)) botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
      confirmarExclusao("Apagar RIF", "Apagar " + titulo + " e seus dados estruturados?", "/api/investigacao/rifs/" + r.id_rif);
    });
    ev.$resumo.text(textoResumo([
      { valor: r.qt_comunicacoes, rotulo: "comunicações" }, { valor: r.qt_envolvidos, rotulo: "envolvidos" }, { valor: r.qt_ocorrencias, rotulo: "ocorrências" }
    ]) + (r.no_titular ? " · Titular: " + r.no_titular : ""));
    adicionarProgressoOperacional(ev, r, "Importação do RIF");
    preencherAcoesRapidas(ev, inq, {
      tipo: "rif", id: r.id_rif, titulo: titulo,
      prompt: urlEscopo("ia", "rif", r.id_rif, titulo),
      achados: urlEscopo("achados", "rif", r.id_rif, titulo),
      compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Comunicações", r.qt_comunicacoes, "rif.html" + qs + "&aba=comunicacoes", "pix", "verde");
    adicionarPill(ev.$pills, "Envolvidos", r.qt_envolvidos, "rif.html" + qs + "&aba=envolvidos", "envolvidos", "roxo");
    adicionarPill(ev.$pills, "Ocorrências", r.qt_ocorrencias, "rif.html" + qs + "&aba=ocorrencias", "notificacoes", "amber");
    adicionarPill(ev.$pills, "Grafo financeiro", totalNumerico(r, ["qt_comunicacoes", "qt_envolvidos", "qt_ocorrencias"]) > 0, "rif.html" + qs + "&aba=grafo", "mesa", "azul");
    return ev.$card;
  }

  function renderSimba(s, inq) {
    var titulo = "Extrato bancário · " + (s.no_arquivo || s.id_extrato);
    var ev = criarEvidencia("simba", titulo, "SIMBA R304", "banco", statusVisual(s));
    adicionarDisponibilidadeArquivo(ev, s);
    ev.$card.attr("data-evidencia-chave", "simba:" + s.id_extrato);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&simba=" + encodeURIComponent(s.id_extrato);
    var processado = !s.cd_status || s.cd_status === "concluido";
    botaoAcao(ev.$menuAcoes, "Hash", "hash", null, "", function () { mostrarHashes(titulo, s); });
    acaoCustodia(ev, "Extrato bancário (SIMBA)", titulo, s, inq);
    if (podeGerenciar(inq) && s.cd_status !== "processando" && s.cd_status !== "pendente") botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
      confirmarExclusao("Apagar extrato bancário", "Apagar " + titulo + " e todos os lançamentos importados?", "/api/investigacao/simba/" + s.id_extrato);
    });
    ev.$resumo.text(textoResumo([
      { valor: s.qt_contas, rotulo: "contas" }, { valor: s.qt_titulares, rotulo: "titulares" }, { valor: s.qt_lancamentos, rotulo: "lançamentos" }, { valor: s.qt_contrapartes, rotulo: "contrapartes" }
    ]) + (Number.isFinite(Number(s.vl_credito)) ? " · Crédito " + formatarMoeda(s.vl_credito) : ""));
    adicionarProgressoOperacional(ev, s, "Importação bancária");
    preencherAcoesRapidas(ev, inq, {
      tipo: "simba", id: s.id_extrato, titulo: titulo,
      prompt: processado ? urlEscopo("ia", "simba", s.id_extrato, titulo) : null,
      motivoPrompt: processado ? undefined : "O extrato ainda está sendo importado — há pouco material para conversar.",
      // anotar achados não depende da importação terminar
      achados: urlEscopo("achados", "simba", s.id_extrato, titulo),
      compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Contas", s.qt_contas, "simba.html" + qs + "&aba=contas", "banco", "verde");
    adicionarPill(ev.$pills, "Lançamentos", s.qt_lancamentos, "simba.html" + qs + "&aba=lancamentos", "documentos", "azul");
    adicionarPill(ev.$pills, "Contrapartes", s.qt_contrapartes, "simba.html" + qs + "&aba=contrapartes", "envolvidos", "roxo");
    adicionarPill(ev.$pills, "Alertas", s.qt_alertas, "simba.html" + qs + "&aba=alertas", "notificacoes", "amber");
    adicionarPill(ev.$pills, "Grafo financeiro", totalNumerico(s, ["qt_contas", "qt_lancamentos", "qt_contrapartes"]) > 0, "simba.html" + qs + "&aba=grafo", "mesa", "azul");
    return ev.$card;
  }

  function renderIped(p, inq) {
    var rotuloTipo = p.tp_fonte === "celular" ? "Celular IPED" : "Nuvem IPED";
    var titulo = rotuloTipo + " · " + (p.no_conta || p.no_pasta || "caso sem conta identificada");
    var ev = criarEvidencia("iped", titulo, p.no_pasta || "Pasta-caso IPED", "arquivos", statusVisual(p));
    adicionarDisponibilidadeArquivo(ev, p);
    ev.$card.attr("data-evidencia-chave", "iped:" + (p.id_extracao_iped || p.id_extracao));
    var qs = p.id_extracao ? "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(p.id_extracao) : "";
    var ipedProcessado = p.id_extracao && (!p.cd_status || p.cd_status === "concluido");
    var emAndamento = p.cd_status === "pendente" || p.cd_status === "processando";
    acaoCustodia(ev, rotuloTipo + " (IPED)", titulo, p, inq);
    if (podeGerenciar(inq)) {
      if (!emAndamento && p.id_extracao) {
        botaoAcao(ev.$menuAcoes, "Compartilhar prova", "compartilhar",
          "investigacao.html" + qs + "&aba=celulares&tema_rota=ignorar");
      }
      botaoAcao(ev.$menuAcoes, emAndamento ? "Cancelar importação" : "Apagar importação",
        emAndamento ? "parar" : "apagar", null, "perigosa", function () {
          if (emAndamento) {
            Dialogo.confirmar({
              titulo: "Cancelar importação",
              mensagem: "Parar a importação do caso " + (p.no_conta || "sem conta identificada") + "? O que já foi lido até agora é preservado.",
              rotuloOk: "Cancelar importação", perigoso: true
            }).then(function (ok) {
              if (!ok) return;
              $.ajax({ url: "/api/investigacao/iped/" + p.id_extracao_iped + "/cancelar", method: "POST" })
                .done(function () { carregar(); })
                .fail(function (xhr) {
                  Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao cancelar a importação." });
                });
            });
            return;
          }
          confirmarExclusao("Apagar importação do IPED", "Apagar " + titulo + " e os dados importados desta pasta-caso?", "/api/investigacao/iped/" + p.id_extracao_iped);
        });
    }
    ev.$resumo.text(textoResumo([
      { valor: p.qt_arquivos, rotulo: "arquivos" }, { valor: p.qt_emails, rotulo: "e-mails" }, { valor: p.qt_contatos, rotulo: "contatos" }, { valor: p.qt_itens_total, rotulo: "itens no índice" }
    ]) || "A pasta-caso ainda não possui itens disponíveis.");
    adicionarProgressoOperacional(ev, p, "Importação IPED");
    preencherAcoesRapidas(ev, inq, {
      tipo: "extracao", id: p.id_extracao || p.id_extracao_iped, titulo: titulo,
      prompt: ipedProcessado ? urlEscopo("ia", "extracao", p.id_extracao, titulo) : null,
      motivoPrompt: ipedProcessado ? undefined : "A pasta-caso ainda está sendo importada — há pouco material para conversar.",
      comparar: ipedProcessado ? "comparar_rosto.html?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&extracao=" + encodeURIComponent(p.id_extracao) : null,
      // anotar achados não depende da importação terminar
      achados: ipedProcessado ? urlEscopo("achados", "extracao", p.id_extracao, titulo) : null,
      motivoAchados: "A importação ainda não gerou uma extração navegável."
    });
    if (qs) {
      adicionarPill(ev.$pills, "Arquivos", p.qt_arquivos, "arquivos.html" + qs, "arquivos", "verde");
      adicionarPill(ev.$pills, "E-mails", p.qt_emails, "emails.html" + qs, "email", "roxo");
      adicionarPill(ev.$pills, "Contatos", p.qt_contatos, "contatos.html" + qs, "contatos", "roxo");
      [
        ["qt_navegacao", "Navegação", "navegacao", "navegacao", "azul"], ["qt_localizacoes", "Localizações", "localizacoes", "localizacoes", "rosa"],
        ["qt_notas", "Notas", "notas", "notas", "amber"], ["qt_ligacoes", "Ligações", "ligacoes", "ligacoes", "laranja"],
        ["qt_apps", "Aplicativos", "aplicativos", "aplicativos", "roxo"], ["qt_acessos", "Acessos", "acessos", "localizacoes", "azul"],
        ["qt_acessos_agregados", "Acessos agregados", "acessos_agregados", "localizacoes", "azul"], ["qt_conexoes", "Conexões", "conexoes", "envolvidos", "roxo"],
        ["qt_atividades", "Minha Atividade", "atividades", "navegacao", "azul"], ["qt_eventos", "Agenda", "agenda", "timeline", "amber"],
        ["qt_dispositivos", "Dispositivos da conta", "dispositivos_conta", "aparelho", "azul"], ["qt_perfil", "Perfil da conta", "perfil_conta", "dono", "roxo"]
      ].forEach(function (item) { adicionarPill(ev.$pills, item[1], p[item[0]], item[2] + ".html" + qs, item[3], item[4]); });
      // domínios decodificados de um caso de celular — o card clássico oferece, o Orbit não
      adicionarPill(ev.$pills, "Wi-Fi", p.qt_redes, "wifi.html" + qs, "wifi", "cyan");
      adicionarPill(ev.$pills, "Contas bancárias", p.qt_contas_financeiras, "contas_financeiras.html" + qs, "banco", "verde");
      adicionarPill(ev.$pills, "Senhas", p.qt_senhas, "credenciais.html" + qs, "senhas", "roxo");
      adicionarPill(ev.$pills, "Autofill", p.qt_autofill, "credenciais.html" + qs, "senhas", "roxo");
      adicionarPill(ev.$pills, "Nuvem × aparelho", p.qt_metadados_nuvem, "correlacao_nuvem.html" + qs, "nuvem", "azul");
      adicionarPill(ev.$pills, "Marcações do perito", p.qt_marcadores, "arquivos.html" + qs + "&marcados=true", "marcacoes", "amber");
      // conversas (WhatsApp, Instagram, …): um botão por app, como no card clássico
      if (Number(p.qt_conversas)) {
        $.getJSON("/api/investigacao/inqueritos/" + inq.id_inquerito + "/apps-mensageiros?extracao=" + encodeURIComponent(p.id_extracao))
          .done(function (apps) {
            apps.forEach(function (a) {
              if (["knowledgec", "biome", "recents"].indexOf(String(a.tp_app || "").toLowerCase()) !== -1) return;
              var ehWhatsApp = MapaTelas.ehAppWhatsApp(a.tp_app);
              var $pill = adicionarPill(ev.$pills, a.tp_app, a.qt_mensagens,
                MapaTelas.paginaConversa("msg", a.tp_app) + qs + "&fonte=msg&app=" + encodeURIComponent(a.tp_app),
                ehWhatsApp ? "whatsapp" : "sms", "verde");
              if ($pill) {
                $pill.css("--app-color", CORES_MENSAGEIRO[a.tp_app] || "#8696a0");
                if (!ehWhatsApp) $pill.children("span").first().html(icone("sms"));
              }
            });
          });
      }
    }
    return ev.$card;
  }

  function renderPdf(p, inq) {
    var titulo = "Inquérito em PDF · " + (p.nu_ipl || p.no_arquivo || p.id_ipl);
    var ev = criarEvidencia("ipl", titulo, p.no_arquivo || "Peças processadas do ePol", "documentos", { texto: "Processado", classe: "concluido" });
    adicionarDisponibilidadeArquivo(ev, p);
    ev.$card.attr("data-evidencia-chave", "ipl:" + p.id_ipl);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&ipl=" + encodeURIComponent(p.id_ipl);
    acaoCustodia(ev, "Inquérito em PDF (IPL)", titulo, p, inq);
    if (podeGerenciar(inq)) botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
      confirmarExclusao("Apagar inquérito extraído", "Apagar " + titulo + " e as peças, pessoas e achados estruturados?", "/api/investigacao/inqueritos-pdf/" + p.id_ipl);
    });
    ev.$resumo.text(textoResumo([
      { valor: p.qt_pecas, rotulo: "peças" }, { valor: p.qt_pessoas, rotulo: "pessoas" }, { valor: p.qt_achados, rotulo: "achados" }, { valor: p.qt_folhas, rotulo: "folhas" }
    ]) + (p.extraido_por ? " · por " + p.extraido_por : ""));
    adicionarProgressoOperacional(ev, p, "Extração do PDF");
    preencherAcoesRapidas(ev, inq, {
      tipo: "ipl", id: p.id_ipl, titulo: titulo,
      prompt: urlEscopo("ia", "ipl", p.id_ipl, titulo),
      achados: urlEscopo("achados", "ipl", p.id_ipl, titulo),
      textoHref: "inquerito_pdf.html" + qs,
      compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Peças", p.qt_pecas, "inquerito_pdf.html" + qs + "&aba=pecas", "documentos", "verde");
    adicionarPill(ev.$pills, "Pessoas", p.qt_pessoas, "inquerito_pdf.html" + qs + "&aba=pessoas", "envolvidos", "roxo");
    adicionarPill(ev.$pills, "Achados", p.qt_achados, "inquerito_pdf.html" + qs + "&aba=achados", "investigacao", "amber");
    return ev.$card;
  }

  function abrirTextoDocumento(inq, documento) {
    var base = "/api/investigacao/inqueritos/" + encodeURIComponent(inq.id_inquerito) + "/documentos/" + encodeURIComponent(documento.id_documento);
    $.getJSON(base).done(function (detalhe) {
      var $overlay = $("<div>").addClass("modal-overlay");
      var $modal = $("<div>").addClass("modal inquerito-texto-modal").appendTo($overlay);
      var $cab = $("<div>").addClass("modal-header").appendTo($modal);
      $("<h2>").text(documento.no_arquivo || "Texto extraído").appendTo($cab);
      $("<button>").attr("type", "button").addClass("modal-close").html("&times;").on("click", function () { $overlay.remove(); }).appendTo($cab);
      var $body = $("<div>").addClass("modal-body").appendTo($modal);
      $("<pre>").addClass("inquerito-texto-extraido").text(detalhe.tx_texto || detalhe.tx_conteudo || detalhe.texto || "Nenhum texto extraído disponível.").appendTo($body);
      $overlay.on("click", function (evento) { if (evento.target === this) $overlay.remove(); });
      $("body").append($overlay);
    }).fail(function (xhr) {
      Dialogo.alerta({ titulo: "Documento", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível carregar o texto extraído." });
    });
  }

  function renderDocumento(d, inq) {
    var titulo = d.no_arquivo || ("Documento " + d.id_documento);
    var ext = (titulo.split(".").pop() || d.tp_arquivo || "").toLowerCase();
    var tipoDoc = "documentos";
    var iconeDoc = "documentos";
    var subtituloDoc = "Documento vinculado";

    if (ext === "pdf") {
      tipoDoc = "pdf";
      iconeDoc = "pdf";
      subtituloDoc = "Documento em PDF";
    } else if (["xlsx", "xls", "csv", "ods"].indexOf(ext) !== -1) {
      tipoDoc = "excel";
      iconeDoc = "excel";
      subtituloDoc = "Planilha de dados";
    } else if (["docx", "doc", "txt", "rtf", "odt"].indexOf(ext) !== -1) {
      tipoDoc = "word";
      iconeDoc = "word";
      subtituloDoc = "Documento de texto";
    } else if (["zip", "rar", "7z", "tar", "gz"].indexOf(ext) !== -1) {
      tipoDoc = "zip";
      iconeDoc = "zip";
      subtituloDoc = "Arquivo compactado";
    } else if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "tiff"].indexOf(ext) !== -1) {
      tipoDoc = "imagem";
      iconeDoc = "imagem";
      subtituloDoc = "Arquivo de imagem";
    }

    var status = d.tx_erro ? { texto: "Falhou", classe: "erro" } : { texto: d.cd_analise === "processando" ? "Em análise" : "Processado", classe: d.cd_analise === "processando" ? "processando" : "concluido" };
    var ev = criarEvidencia(tipoDoc, titulo, subtituloDoc, iconeDoc, status);
    adicionarDisponibilidadeArquivo(ev, d);
    ev.$card.attr("data-evidencia-chave", "documento:" + d.id_documento);
    acaoCustodia(ev, "Documento anexado", titulo, d, inq);
    if (podeGerenciar(inq)) botaoAcao(ev.$menuAcoes, "Apagar documento", "apagar", null, "perigosa", function () {
      confirmarExclusao("Remover documento", "Remover " + titulo + " deste inquérito?", "/api/investigacao/documentos/" + d.id_documento);
    });
    var qtAchados = achadosDeDocumentos([d]).length;
    var meta = (d.tp_arquivo || ext || "?").toUpperCase() + " · " +
      (d.tx_erro ? "erro de extração" : formatarNumero(d.qt_caracteres) + " caracteres") +
      (d.dt_processado ? " · " + dataHora(d.dt_processado) : "") +
      (d.anexado_por ? " · por " + d.anexado_por : "");
    ev.$resumo.text(d.tx_resumo || d.tx_resumo_ia || d.tx_erro || meta || "O texto extraído fica disponível para a IA, buscas e análise deste inquérito.");
    adicionarProgressoOperacional(ev, $.extend({}, d, { cd_status: d.cd_analise }), "Análise do documento");
    var documentoPdf = ext === "pdf";
    preencherAcoesRapidas(ev, inq, {
      tipo: "documento", id: d.id_documento, titulo: titulo,
      // Os achados do analista são anotações do documento, não do texto extraído — quem falhou
      // é justamente onde o analista mais precisa anotar o problema (a pílula "Achados" abaixo
      // sempre apontou para lá; o botão rápido fica de acordo).
      prompt: !d.tx_erro ? urlEscopo("ia", "documento", d.id_documento, titulo) : null,
      motivoPrompt: d.tx_erro ? "A extração de texto deste documento falhou — não há conteúdo para conversar." : undefined,
      achados: urlEscopo("achados", "documento", d.id_documento, titulo),
      verTexto: documentoPdf ? function () { abrirTextoDocumento(inq, d); } : null,
      exibirTexto: documentoPdf,
      compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Achados", qtAchados, urlEscopo("achados", "documento", d.id_documento, titulo), "investigacao", "amber");
    if (d.qt_pessoas !== undefined) adicionarPill(ev.$pills, "Pessoas", d.qt_pessoas, null, "envolvidos", "roxo");
    return ev.$card;
  }

  function renderIrpf(r, inq) {
    var titulo = "Declaração de IRPF · " + (r.no_titular || r.no_arquivo || r.id_irpf);
    var ev = criarEvidencia("irpf", titulo, r.nu_cpf ? "CPF " + r.nu_cpf : "Informações patrimoniais", "documentos", { texto: "Processado", classe: "concluido" });
    adicionarDisponibilidadeArquivo(ev, r);
    ev.$card.attr("data-evidencia-chave", "irpf:" + r.id_irpf);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&irpf=" + encodeURIComponent(r.id_irpf);
    acaoCustodia(ev, "Declaração de IRPF", titulo, r, inq);
    if (podeGerenciar(inq)) botaoAcao(ev.$menuAcoes, "Apagar extração", "apagar", null, "perigosa", function () {
      confirmarExclusao("Apagar declaração", "Apagar " + titulo + " e os dados patrimoniais extraídos?", "/api/investigacao/declaracoes-irpf/" + r.id_irpf);
    });
    ev.$resumo.text(textoResumo([
      { valor: r.qt_bens, rotulo: "bens" }, { valor: r.qt_dependentes, rotulo: "dependentes" }, { valor: r.qt_rendimentos, rotulo: "rendimentos" }, { valor: r.qt_dividas, rotulo: "dívidas" }
    ]));
    adicionarProgressoOperacional(ev, r, "Extração da declaração");
    preencherAcoesRapidas(ev, inq, {
      tipo: "irpf", id: r.id_irpf, titulo: titulo,
      prompt: urlEscopo("ia", "irpf", r.id_irpf, titulo),
      achados: urlEscopo("achados", "irpf", r.id_irpf, titulo),
      compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Bens e direitos", r.qt_bens, "declaracao_irpf.html" + qs + "&aba=bens", "banco", "verde");
    adicionarPill(ev.$pills, "Dependentes", r.qt_dependentes, "declaracao_irpf.html" + qs + "&aba=dependentes", "envolvidos", "roxo");
    adicionarPill(ev.$pills, "Rendimentos", r.qt_rendimentos, "declaracao_irpf.html" + qs + "&aba=rendimentos", "documentos", "amber");
    adicionarPill(ev.$pills, "Dívidas", r.qt_dividas, "declaracao_irpf.html" + qs + "&aba=dividas", "documentos", "azul");
    return ev.$card;
  }

  function renderIpei(r, inq) {
    var titulo = "Sigilo fiscal · IPEI " + (r.nu_ipei || r.no_arquivo || r.id_ipei);
    var ev = criarEvidencia("fiscal", titulo, r.no_alvos || "Dados fiscais processados", "documentos", statusVisual(r));
    adicionarDisponibilidadeArquivo(ev, r);
    ev.$card.attr("data-evidencia-chave", "ipei:" + r.id_ipei);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&ipei=" + encodeURIComponent(r.id_ipei);
    acaoCustodia(ev, "IPEI (quebra de sigilo fiscal)", titulo, r, inq);
    ev.$resumo.text(textoResumo([
      { valor: r.qt_alvos, rotulo: "alvos" }, { valor: r.qt_relacionados, rotulo: "vínculos" },
      { valor: r.qt_contas, rotulo: "contas" }, { valor: r.qt_bens, rotulo: "bens" },
      { valor: r.qt_notas, rotulo: "notas fiscais" }, { valor: r.qt_contatos, rotulo: "contatos" }
    ]) || "Quebra de sigilo fiscal vinculada ao inquérito.");
    preencherAcoesRapidas(ev, inq, {
      tipo: "ipei", id: r.id_ipei, titulo: titulo,
      prompt: urlEscopo("ia", "ipei", r.id_ipei, titulo),
      achados: urlEscopo("achados", "ipei", r.id_ipei, titulo), compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Alvos", r.qt_alvos, "ipei.html" + qs + "&aba=alvos", "dono", "verde");
    adicionarPill(ev.$pills, "Vínculos", r.qt_relacionados, "ipei.html" + qs + "&aba=vinculos", "envolvidos", "roxo");
    adicionarPill(ev.$pills, "Contas", r.qt_contas, "ipei.html" + qs + "&aba=contas", "banco", "azul");
    adicionarPill(ev.$pills, "Bens", r.qt_bens, "ipei.html" + qs + "&aba=bens", "arquivos", "amber");
    adicionarPill(ev.$pills, "Notas fiscais", r.qt_notas, "ipei.html" + qs + "&aba=notas", "rif", "amber");
    adicionarPill(ev.$pills, "Contatos", r.qt_contatos, "ipei.html" + qs + "&aba=contatos", "contatos", "verde");
    return ev.$card;
  }

  function renderTelefonia(t, inq) {
    var periodo = [t.dt_inicio_periodo, t.dt_fim_periodo].filter(Boolean);
    var subtitulo = t.nu_linhas_alvo ||
      (periodo.length === 2 ? formatarDataSimples(periodo[0]) + " a " + formatarDataSimples(periodo[1]) : "Dados telefônicos processados");
    var titulo = "Telefonia · " + (t.no_operadora || t.no_arquivo || "operadora não identificada");
    var ev = criarEvidencia("telefonia", titulo, subtitulo, "ligacoes", statusVisual(t));
    adicionarDisponibilidadeArquivo(ev, t);
    ev.$card.attr("data-evidencia-chave", "telefonia:" + t.id_telefonia);
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&telefonia=" + encodeURIComponent(t.id_telefonia);
    acaoCustodia(ev, "Telefonia (operadora)", titulo, t, inq);

    if (podeGerenciar(inq)) {
      botaoAcao(ev.$menuAcoes, "Apagar telefonia", "apagar", null, "perigosa", function () {
        confirmarExclusao("Apagar telefonia da operadora", "Apagar todo o retorno da operadora " + (t.no_operadora || "") + " deste inquérito? Esta ação não pode ser desfeita.", "/api/investigacao/telefonia/" + t.id_telefonia);
      });
    }

    var linha1 = formatarNumero(t.qt_chamadas) + " chamadas · " +
      formatarNumero(t.qt_erbs) + " antenas · " +
      formatarNumero(t.qt_linhas) + " fichas cadastrais · " +
      formatarNumero(t.qt_numeros) + " números" +
      (Number(t.qt_sessoes) ? " · " + formatarNumero(t.qt_sessoes) + " chamadas WiFi" : "") +
      (Number(t.qt_dados) ? " · " + formatarNumero(t.qt_dados) + " sessões de dados" : "") +
      (t.extraido_por ? " · por " + t.extraido_por : "");

    var origem = [formatarNumero(t.qt_arquivos || 1) + " arquivo(s) da operadora"];
    if (periodo.length === 2) origem.push("período de " + formatarDataSimples(periodo[0]) + " a " + formatarDataSimples(periodo[1]));
    if (Number(t.qt_erbs_no_mapa)) origem.push(formatarNumero(t.qt_erbs_no_mapa) + " antenas com coordenada");
    var linha2 = origem.join(" · ");

    ev.$resumo.html($("<span>").text(linha1).html() + "<br/><span style='opacity:.75'>" + $("<span>").text(linha2).html() + "</span>");

    preencherAcoesRapidas(ev, inq, {
      tipo: "telefonia", id: t.id_telefonia, titulo: titulo,
      prompt: urlEscopo("ia", "telefonia", t.id_telefonia, titulo),
      achados: urlEscopo("achados", "telefonia", t.id_telefonia, titulo), compararAplicavel: false
    });
    adicionarPill(ev.$pills, "Chamadas", t.qt_chamadas, "telefonia.html" + qs + "&aba=chamadas", "ligacoes", "verde");
    adicionarPill(ev.$pills, "Antenas", t.qt_erbs, "telefonia.html" + qs + "&aba=erbs", "localizacoes", "azul");
    adicionarPill(ev.$pills, "Cadastro", t.qt_linhas, "telefonia.html" + qs + "&aba=linhas", "contatos", "roxo");
    adicionarPill(ev.$pills, "Números e IMEI", t.qt_numeros, "telefonia.html" + qs + "&aba=numeros", "aparelho", "amber");
    if (Number(t.qt_dados)) {
      adicionarPill(ev.$pills, "Dados móveis", t.qt_dados, "telefonia.html" + qs + "&aba=dados", "navegacao", "azul");
    }
    adicionarPill(ev.$pills, "Arquivos importados", t.qt_arquivos || 1, "telefonia.html" + qs + "&aba=arquivos", "arquivos", "verde");
    if (Number(t.qt_erbs_no_mapa)) {
      adicionarPill(ev.$pills, "ERBs no mapa", t.qt_erbs_no_mapa,
        "localizacoes.html?inquerito=" + encodeURIComponent(inq.id_inquerito) + "&erbs=1&visao=grafo", "localizacoes", "azul");
    }
    return ev.$card;
  }

  function renderEvidencias(inq, dados) {
    var $alvo = $("#inquerito-detalhe-evidencias").empty();
    dados.extracoes.forEach(function (e) { $alvo.append(renderUfdr(e, inq)); });
    dados.rifs.forEach(function (r) { $alvo.append(renderRif(r, inq)); });
    dados.simba.forEach(function (s) { $alvo.append(renderSimba(s, inq)); });
    dados.iped.forEach(function (p) { $alvo.append(renderIped(p, inq)); });
    dados.pdfs.forEach(function (p) { $alvo.append(renderPdf(p, inq)); });
    dados.irpfs.forEach(function (r) { $alvo.append(renderIrpf(r, inq)); });
    dados.ipeis.forEach(function (r) { $alvo.append(renderIpei(r, inq)); });
    dados.telefonia.forEach(function (t) { $alvo.append(renderTelefonia(t, inq)); });
    dados.documentos.filter(function (d) { return !d.in_ipl && !d.in_rif && !d.in_simba && !d.in_irpf && !d.in_ipei && !d.in_telefonia; })
      .forEach(function (d) { $alvo.append(renderDocumento(d, inq)); });

    $alvo.children(".inquerito-evidencia").each(function () {
      var $card = $(this);
      if (!$card.find(".inquerito-evidencia-menu").children().length) $card.find(".inquerito-evidencia-menu-wrap").addClass("hidden");
      $card.attr("data-busca", $card.text().toLowerCase());
    });
    $alvo.children(".inquerito-evidencia").each(function () {
      var $card = $(this);
      var chave = $card.attr("data-evidencia-chave");
      var aberta = preferenciaExpandirTodas !== null
        ? preferenciaExpandirTodas
        : (Object.prototype.hasOwnProperty.call(estadoExpansaoEvidencias, chave) ? estadoExpansaoEvidencias[chave] : false);
      definirEvidenciaAberta($card, aberta, false);
    });

    var outrosDocs = dados.documentos.filter(function (d) {
      return !d.in_ipl && !d.in_rif && !d.in_simba && !d.in_irpf && !d.in_ipei && !d.in_telefonia;
    });

    var totalGeral = (dados.extracoes.length + dados.iped.length) +
      dados.rifs.length +
      dados.simba.length +
      dados.pdfs.length +
      dados.irpfs.length +
      dados.ipeis.length +
      dados.telefonia.length +
      outrosDocs.length;

    var abasCategorias = [
      { id: "celulares", rotulo: "Celulares", icone: "aparelho", quantidade: dados.extracoes.length + dados.iped.length },
      { id: "rif", rotulo: "RIFs", icone: "rif", quantidade: dados.rifs.length },
      { id: "simba", rotulo: "SIMBA", icone: "banco", quantidade: dados.simba.length },
      { id: "ipl", rotulo: "IPL", icone: "documentos", quantidade: dados.pdfs.length },
      { id: "irpf", rotulo: "IRPF", icone: "documentos", quantidade: dados.irpfs.length },
      { id: "fiscal", rotulo: "Fiscal", icone: "documentos", quantidade: dados.ipeis.length },
      { id: "telefonia", rotulo: "Telefonia", icone: "ligacoes", quantidade: dados.telefonia.length },
      { id: "documentos", rotulo: "Outros documentos", icone: "documentos", quantidade: outrosDocs.length }
    ].filter(function (tipo) { return Number(tipo.quantidade) > 0; });

    var tipos = [
      { id: "todos", rotulo: "Todos", icone: "inqueritos", quantidade: totalGeral }
    ].concat(abasCategorias);

    var idsDisponiveis = tipos.map(function (tipo) { return tipo.id; });
    var abaParam = parametros.get("aba");
    if (abaParam && (idsDisponiveis.indexOf(abaParam) !== -1 || abaParam === "rifs")) {
      filtroFonte = abaParam === "rifs" ? "rif" : abaParam;
    } else if (!filtroFonte || idsDisponiveis.indexOf(filtroFonte) === -1) {
      filtroFonte = "todos";
    }

    var $tabs = $("#inquerito-fontes-tabs").empty();
    tipos.forEach(function (tipo) {
      var $btn = $("<button>").attr({ type: "button", role: "tab", "data-tipo": tipo.id, "aria-selected": String(tipo.id === filtroFonte) })
        .toggleClass("ativo", tipo.id === filtroFonte).append($("<span>").html(icone(tipo.icone))).append(document.createTextNode(tipo.rotulo + " "));
      $("<small>").text(formatarNumero(tipo.quantidade)).appendTo($btn);
      $btn.on("click", function () { selecionarFiltroFonte(tipo.id); }).appendTo($tabs);
    });
    aplicarFiltroEvidencias();
    focarEvidenciaSolicitada();
  }

  function focarEvidenciaSolicitada() {
    var chave = parametros.get("evidencia");
    if (!chave) return;
    var $card = $("#inquerito-detalhe-evidencias .inquerito-evidencia").filter(function () {
      return String($(this).attr("data-evidencia-chave")) === String(chave);
    }).first();
    if (!$card.length) return;
    var tipo = $card.attr("data-tipo");
    var destino = "todos";
    if (tipo === "ufdr" || tipo === "iped" || tipo === "nuvem") destino = "celulares";
    else if (tipo === "rif") destino = "rif";
    else if (tipo === "simba") destino = "simba";
    else if (tipo === "ipl") destino = "ipl";
    else if (tipo === "irpf") destino = "irpf";
    else if (tipo === "fiscal" || tipo === "ipei") destino = "fiscal";
    else if (tipo === "telefonia") destino = "telefonia";
    else if (["documentos", "pdf", "excel", "word", "zip", "imagem"].indexOf(tipo) !== -1) destino = "documentos";
    selecionarFiltroFonte(destino);
    definirEvidenciaAberta($card, true);
    atualizarControleEvidencias();
    window.setTimeout(function () {
      $card[0].scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  }

  function selecionarFiltroFonte(tipo) {
    filtroFonte = tipo;
    $("#inquerito-fontes-tabs button").removeClass("ativo").attr("aria-selected", "false")
      .filter('[data-tipo="' + tipo + '"]').addClass("ativo").attr("aria-selected", "true");
    aplicarFiltroEvidencias();
  }

  function aplicarFiltroEvidencias() {
    var termo = $.trim($("#inquerito-fontes-busca").val() || "").toLowerCase();
    var visiveis = 0;
    $("#inquerito-detalhe-evidencias .inquerito-evidencia").each(function () {
      var $card = $(this);
      var tipo = $card.attr("data-tipo");
      var casaTipo = filtroFonte === "todos" ||
        tipo === filtroFonte ||
        ((filtroFonte === "rif" || filtroFonte === "rifs") && tipo === "rif") ||
        (filtroFonte === "celulares" && (tipo === "ufdr" || tipo === "iped" || tipo === "nuvem")) ||
        (filtroFonte === "ipl" && tipo === "ipl") ||
        (filtroFonte === "irpf" && tipo === "irpf") ||
        (filtroFonte === "fiscal" && (tipo === "fiscal" || tipo === "ipei")) ||
        (filtroFonte === "documentos" && (tipo === "documentos" || tipo === "pdf" || tipo === "excel" || tipo === "word" || tipo === "zip" || tipo === "imagem"));
      var casaBusca = !termo || String($card.attr("data-busca") || "").indexOf(termo) !== -1;
      var mostrar = casaTipo && casaBusca;
      $card.toggleClass("hidden", !mostrar);
      if (mostrar) visiveis += 1;
    });
    $("#inquerito-evidencias-vazio").toggleClass("hidden", visiveis > 0);
    atualizarControleEvidencias();
  }

  function adicionarMaisAcao(alvo, rotulo, iconeNome, onClick, classe, opcoes) {
    opcoes = opcoes || {};
    var $btn = $("<button>").attr({ type: "button", role: "menuitem", title: opcoes.title || "" })
      .prop("disabled", !!opcoes.disabled).addClass(classe || "");
    $btn.append($("<span>").addClass("inquerito-menu-icone").html(icone(iconeNome)));
    $btn.append($("<span>").addClass("inquerito-menu-texto").text(rotulo)).on("click", function () {
      if ($(this).prop("disabled")) return;
      fecharMenusInquerito();
      onClick();
    }).appendTo(alvo);
    return $btn;
  }

  function adicionarDivisorMenu(alvo) {
    $("<div>").addClass("inquerito-mais-acoes-divisor").appendTo(alvo);
  }

  function fecharMenusInquerito() {
    $("#inquerito-extras-menu, #inquerito-gerencia-menu").addClass("hidden");
    $("#inquerito-extras-toggle, #inquerito-gerencia-toggle").attr("aria-expanded", "false");
  }

  function editarContexto(inq) {
    Dialogo.prompt({
      titulo: "Contexto do caso",
      mensagem: "Registre um sumário breve, a hipótese de trabalho e o que deve orientar a IA.",
      valor: inq.tx_contexto_investigacao || "", multilinha: true, maxlength: 8000, rotuloOk: "Salvar"
    }).then(function (texto) {
      if (texto === null) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/contexto", method: "PUT", contentType: "application/json", data: JSON.stringify({ texto: texto })
      }).done(function () {
        inq.tx_contexto_investigacao = $.trim(texto);
        $("#inquerito-detalhe-contexto").text(inq.tx_contexto_investigacao || "Nenhum contexto descritivo foi registrado para este inquérito.");
      }).fail(function (xhr) { Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o contexto." }); });
    });
  }

  function editarNumero(inq) {
    Dialogo.prompt({ titulo: "Editar inquérito", mensagem: "Corrija o número do inquérito no ePol.", valor: inq.nu_inquerito || "", maxlength: 120, rotuloOk: "Salvar" }).then(function (numero) {
      numero = numero === null ? null : $.trim(numero);
      if (!numero || numero === inq.nu_inquerito) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/numero", method: "PUT", contentType: "application/json", data: JSON.stringify({ numero_inquerito: numero })
      }).done(function () { carregar(); }).fail(function (xhr) { Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao editar o inquérito." }); });
    });
  }

  function alterarDelegacia(inq) {
    Dialogo.prompt({
      titulo: "Delegacia do inquérito",
      mensagem: "Informe a sigla da delegacia responsável por este inquérito.",
      valor: inq.delegacia || "", maxlength: 120, rotuloOk: "Salvar"
    }).then(function (delegacia) {
      if (delegacia === null) return;
      delegacia = $.trim(delegacia);
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito + "/delegacia", method: "PUT",
        contentType: "application/json", data: JSON.stringify({ delegacia: delegacia })
      }).done(function () { carregar(); }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao alterar a delegacia." });
      });
    });
  }

  function iniciarBuscaSemantica($botao) {
    $botao.prop("disabled", true).find(".inquerito-menu-texto").text("Iniciando busca semântica…");
    $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/indexacao-semantica", method: "POST" }).done(function () {
      acompanharBuscaSemantica($botao);
    }).fail(function (xhr) {
      $botao.prop("disabled", false);
      Dialogo.alerta({ titulo: "Busca semântica", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível iniciar a indexação." });
    });
  }

  function acompanharBuscaSemantica($botao) {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/indexacao-semantica").done(function (estado) {
      var status = estado && estado.cd_status;
      var texto = "Busca semântica (" + formatarNumero(totalNumerico(inqueritoAtual, ["qt_mensagens", "qt_sms_mensagens", "qt_msg_mensagens"])) + ")";
      if (status === "processando" || status === "pendente") {
        texto = "Busca semântica · " + (estado.tx_etapa || "indexando") + (estado.qt_progresso != null ? " " + estado.qt_progresso + "%" : "");
        setTimeout(function () { acompanharBuscaSemantica($botao); }, 2500);
      } else {
        $botao.prop("disabled", false);
      }
      $botao.find(".inquerito-menu-texto").text(texto);
    }).fail(function () { $botao.prop("disabled", false); });
  }

  function renderMaisAcoes(inq, dados) {
    var qs = "?inquerito=" + encodeURIComponent(inq.id_inquerito);
    var $extras = $("#inquerito-extras-menu").empty();
    var $gerencia = $("#inquerito-gerencia-menu").empty();
    var temExtracao = dados.extracoes.length > 0;
    var doisAparelhos = dados.extracoes.length > 1;
    var temMaterial = dados.extracoes.length + dados.rifs.length + dados.simba.length + dados.iped.length +
      dados.pdfs.length + dados.irpfs.length + dados.ipeis.length + dados.telefonia.length + dados.documentos.length > 0;
    var qtDocumentos = dados.pdfs.length + dados.irpfs.length + dados.ipeis.length + dados.documentos.length;

    adicionarMaisAcao($extras, "Comparar rostos", "envolvidos", function () {
      window.location.href = "comparar_rosto.html" + qs;
    }, "", { disabled: !temExtracao, title: temExtracao ? "Comparar rostos encontrados nas extrações" : "Disponível quando houver uma extração de aparelho." });
    adicionarMaisAcao($extras, "Comparar celulares", "aparelho", function () {
      window.location.href = "comparar_celulares.html" + qs;
    }, "", { disabled: !doisAparelhos, title: doisAparelhos ? "Comparar os dados de dois ou mais aparelhos" : "É preciso ter pelo menos dois aparelhos no inquérito." });
    adicionarMaisAcao($extras, "Nuvem × aparelho", "arquivos", function () {
      window.location.href = "correlacao_nuvem.html" + qs;
    }, "", { disabled: !doisAparelhos, title: doisAparelhos ? "Comparar arquivos da nuvem com os aparelhos" : "É preciso ter uma extração de nuvem e um aparelho." });
    adicionarMaisAcao($extras, "Buscar entidades no material", "investigacao", function () {
      window.location.href = "busca_entidades.html" + qs;
    }, "", { disabled: !temMaterial, title: temMaterial ? "Buscar pessoas e empresas em todo o material importado" : "Nada foi importado neste inquérito ainda." });
    var temFinanceiro = (dados.rifs.length + dados.simba.length) > 0 || (Number(inq.qt_rifs || 0) + Number(inq.qt_simba || 0)) > 0;
    adicionarMaisAcao($extras, "Linha do tempo financeira", "timeline", function () {
      window.location.href = "timeline_financeira.html" + qs;
    }, "", {
      disabled: !temFinanceiro,
      title: temFinanceiro ? "RIF e SIMBA numa régua temporal só: créditos, débitos e operações em espécie" : "Nenhum RIF nem extrato SIMBA neste inquérito."
    });
    var temTelefonia = dados.telefonia.length > 0 || Number(inq.qt_telefonia || 0) > 0;
    adicionarMaisAcao($extras, "Encontros por antena", "localizacoes", function () {
      window.location.href = "encontros_telefonia.html" + qs;
    }, "", {
      disabled: !temTelefonia,
      title: temTelefonia ? "Quem esteve na mesma antena dentro da janela — co-localização de linhas do afastamento" : "Nenhuma telefonia neste inquérito."
    });
    adicionarDivisorMenu($extras);
    adicionarMaisAcao($extras, "Contexto do caso", "notas", function () { editarContexto(inq); });
    adicionarMaisAcao($extras, "Documentos" + (qtDocumentos ? " (" + formatarNumero(qtDocumentos) + ")" : ""), "documentos", function () {
      if ($("#inquerito-fontes-tabs [data-tipo=\"documentos\"]").length) selecionarFiltroFonte("documentos");
      document.querySelector("#fontes").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    var qtMensagens = totalNumerico(inq, ["qt_mensagens", "qt_sms_mensagens", "qt_msg_mensagens"]);
    var $semantica = adicionarMaisAcao($extras, "Indexar busca semântica", "ia", function () { iniciarBuscaSemantica($semantica); }, "", {
      disabled: qtMensagens <= 0, title: qtMensagens > 0 ? "Indexar as mensagens para a busca semântica da IA" : "Nenhuma mensagem disponível para indexação."
    });

    if (podeGerenciar(inq)) {
      $("#inquerito-gerencia").removeClass("hidden");
      adicionarMaisAcao($gerencia, "Editar inquérito", "inquerito", function () { editarNumero(inq); });
      adicionarMaisAcao($gerencia, "Gerência de permissões", "admin", function () { abrirPermissoes(); });
      adicionarMaisAcao($gerencia, "Delegacia do inquérito", "delegacias", function () { alterarDelegacia(inq); });
      adicionarDivisorMenu($gerencia);
      adicionarMaisAcao($gerencia, "Apagar inquérito", "documentos", function () {
        var numInq = formatarIdInquerito(inq.nu_inquerito) || inq.nu_inquerito || "este inquérito";
        Dialogo.confirmar({ titulo: "Apagar inquérito", mensagem: "Apagar " + numInq + " e todas as suas fontes, entidades e análises? Esta ação não pode ser desfeita.", rotuloOk: "Apagar inquérito", perigoso: true }).then(function (ok) {
          if (!ok) return;
          $.ajax({ url: "/api/investigacao/inqueritos/" + inq.id_inquerito, method: "DELETE" }).done(function () { window.location.href = "investigacao.html"; })
            .fail(function (xhr) { Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível apagar o inquérito." }); });
        });
      }, "perigosa");
    } else {
      $("#inquerito-gerencia").addClass("hidden");
    }
  }

  function abrirPermissoes() {
    $("#modal-inquerito-permissoes").removeClass("hidden");
    carregarPermissoes();
  }

  function carregarPermissoes() {
    var $lista = $("#inquerito-permissoes-lista").empty().text("Carregando permissões…");
    var $select = $("#inquerito-permissoes-usuario").empty();
    $.when(
      requisicao("/api/investigacao/inqueritos/" + idInquerito + "/permissoes", []),
      requisicao("/api/investigacao/usuarios-basico", [])
    ).done(function (permissoes, usuarios) {
      permissoes = listaOuVazia(permissoes);
      usuarios = listaOuVazia(usuarios);
      $lista.empty();
      if (!permissoes.length) $lista.append($("<p>").addClass("inquerito-detalhe-sem-dados").text("Nenhuma permissão adicional concedida."));
      permissoes.forEach(function (permissao) {
        var $linha = $("<div>").addClass("inquerito-permissao-linha").appendTo($lista);
        var $texto = $("<div>").appendTo($linha);
        $("<strong>").text(permissao.no_usuario || permissao.no_login).appendTo($texto);
        $("<small>").text(permissao.no_login || "").appendTo($texto);
        var $admin = $("<label>").addClass("field-check").append($("<input>").attr("type", "checkbox").prop("checked", !!permissao.in_admin)).append(document.createTextNode(" Admin")).appendTo($linha);
        $admin.find("input").on("change", function () {
          $.ajax({
            url: "/api/investigacao/inqueritos/" + idInquerito + "/permissoes", method: "POST", contentType: "application/json",
            data: JSON.stringify({ id_usuario: permissao.id_usuario, in_admin: $(this).prop("checked") })
          }).fail(function () { carregarPermissoes(); });
        });
        $("<button>").attr("type", "button").addClass("btn btn-danger btn-small").text("Remover").on("click", function () {
          $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/permissoes/" + permissao.id_usuario, method: "DELETE" }).done(carregarPermissoes);
        }).appendTo($linha);
      });
      var concedidos = permissoes.map(function (p) { return String(p.id_usuario); });
      usuarios.filter(function (u) { return concedidos.indexOf(String(u.id_usuario)) === -1; }).forEach(function (usuario) {
        $("<option>").attr("value", usuario.id_usuario).text((usuario.no_usuario || usuario.no_login) + (usuario.no_login ? " · " + usuario.no_login : "")).appendTo($select);
      });
      $("#inquerito-permissoes-adicionar").prop("disabled", !$select.children().length);
    });
  }

  function abrirRelatorio(escopo) {
    relatorioEscopoAtual = escopo && escopo.tipo ? escopo : null;
    var $modal = $("#modal-inquerito-relatorio").removeClass("hidden");
    var $select = $("#inquerito-relatorio-padrao").empty();
    $("#inquerito-relatorio-escopo").text(relatorioEscopoAtual ? "· " + relatorioEscopoAtual.titulo : "");
    $("#inquerito-relatorio-preparacao").empty();
    $modal.find(".inquerito-relatorio-download").remove();
    $("#inquerito-relatorio-status").text("Carregando padrões…").removeClass("success error");
    $("#inquerito-relatorio-gerar").prop("disabled", true);
    $.getJSON("/api/relatorios/padroes-ativos").done(function (padroes) {
      padroes = listaOuVazia(padroes);
      if (!padroes.length) {
        $("#inquerito-relatorio-status").text("Nenhum padrão de relatório ativo.").addClass("error");
        return;
      }
      padroes.forEach(function (padrao) { $("<option>").attr("value", padrao.id_padrao).text(padrao.no_padrao).appendTo($select); });
      $("#inquerito-relatorio-status").text("");
      carregarPreparacaoRelatorio();
    }).fail(function () {
      $("#inquerito-relatorio-status").text("Não foi possível carregar os padrões.").addClass("error");
    });
    return $modal;
  }

  function carregarPreparacaoRelatorio() {
    var idPadrao = $("#inquerito-relatorio-padrao").val();
    if (!idPadrao) return;
    var $alvo = $("#inquerito-relatorio-preparacao").empty().text("Preparando opções…");
    $("#inquerito-relatorio-gerar").prop("disabled", true);
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/relatorios/" + idPadrao + "/preparar").done(function (dados) {
      preparacaoRelatorio = { idPadrao: idPadrao, dados: dados, blocos: [], manuais: {}, comentarioIa: null, reescrever: null, escopo: relatorioEscopoAtual };
      $alvo.empty();
      if (relatorioEscopoAtual) {
        $("<p>").addClass("inquerito-relatorio-foco").append($("<strong>").text("Fonte em foco: ")).append(document.createTextNode(relatorioEscopoAtual.titulo)).appendTo($alvo);
      }
      var $secoes = $("<div>").addClass("field inquerito-relatorio-secoes").appendTo($alvo);
      $("<label>").text("Seções do relatório").appendTo($secoes);
      (dados.blocos || []).forEach(function (bloco) {
        var $linha = $("<label>").addClass("field-check").appendTo($secoes);
        var $cb = $("<input>").attr("type", "checkbox").prop("checked", true).data("id", bloco.id_bloco).appendTo($linha);
        preparacaoRelatorio.blocos.push($cb);
        $linha.append(document.createTextNode(bloco.no_titulo ? bloco.no_titulo + " · " + bloco.rotulo_tipo : bloco.rotulo_tipo));
      });
      (dados.tokens_manuais || []).forEach(function (token) {
        var $field = $("<div>").addClass("field").appendTo($alvo);
        $("<label>").text("{{" + token + "}}").appendTo($field);
        preparacaoRelatorio.manuais[token] = $("<input>").attr("type", "text").appendTo($field);
      });
      if (dados.mostrar_comentario_ia) {
        var $ia = $("<label>").addClass("field-check").appendTo($alvo);
        preparacaoRelatorio.comentarioIa = $("<input>").attr("type", "checkbox").appendTo($ia);
        $("<span>").addClass("ia-inline-icon").html(icone("ia")).appendTo($ia);
        $ia.append(document.createTextNode(" Incluir resumo das conversas por IA"));
      }
      if (dados.mostrar_reescrever_comentarios) {
        var $reescrever = $("<label>").addClass("field-check").appendTo($alvo);
        preparacaoRelatorio.reescrever = $("<input>").attr("type", "checkbox").appendTo($reescrever);
        $("<span>").addClass("ia-inline-icon").html(icone("ia")).appendTo($reescrever);
        $reescrever.append(document.createTextNode(" Reescrever comentários do analista por IA"));
      }
      if (!relatorioEscopoAtual) {
        var resumo = [
          (dados.marcacoes || []).length + " marcações",
          (dados.arquivos_rif || []).length + " RIFs",
          (dados.arquivos_bancario || []).length + " extratos",
          (dados.arquivos_ipl || []).length + " IPLs em PDF"
        ];
        $("<p>").addClass("field-help").text("Fontes incluídas: " + resumo.join(" · ") + ".").appendTo($alvo);
      }
      $("#inquerito-relatorio-gerar").prop("disabled", !(dados.blocos || []).length);
    }).fail(function (xhr) {
      $alvo.empty();
      $("#inquerito-relatorio-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível preparar o relatório.").addClass("error");
    });
  }

  function gerarRelatorio() {
    if (!preparacaoRelatorio) return;
    var dados = preparacaoRelatorio.dados;
    var idsBlocos = preparacaoRelatorio.blocos.filter(function ($cb) { return $cb.prop("checked"); }).map(function ($cb) { return $cb.data("id"); });
    if (!idsBlocos.length) {
      $("#inquerito-relatorio-status").text("Selecione ao menos uma seção.").addClass("error");
      return;
    }
    var valores = {};
    Object.keys(preparacaoRelatorio.manuais).forEach(function (token) { valores[token] = $.trim(preparacaoRelatorio.manuais[token].val()); });
    var escopo = preparacaoRelatorio.escopo;
    function idsFonte(lista, campo, tipo) {
      if (!lista) return null;
      if (!escopo) return lista.map(function (item) { return item[campo]; });
      return escopo.tipo === tipo ? [escopo.id] : [];
    }
    var marcacoes = dados.mostrar_marcacoes ? (dados.marcacoes || []) : [];
    if (escopo) {
      marcacoes = escopo.tipo === "extracao" ? marcacoes.filter(function (m) {
        return String(m.id_extracao || m.id_extracao_origem || "") === String(escopo.id);
      }) : [];
    }
    var corpo = {
      valores: valores,
      ids_blocos: idsBlocos,
      ids_marcacoes: dados.mostrar_marcacoes ? marcacoes.map(function (m) { return m.id_marcacao; }) : null,
      ids_rif: idsFonte(dados.arquivos_rif, "id_rif", "rif"),
      ids_extratos: idsFonte(dados.arquivos_bancario, "id_extrato", "simba"),
      ids_ipl: idsFonte(dados.arquivos_ipl, "id_ipl", "ipl"),
      in_comentario_ia: !!(preparacaoRelatorio.comentarioIa && preparacaoRelatorio.comentarioIa.prop("checked")),
      in_reescrever_comentarios: !!(preparacaoRelatorio.reescrever && preparacaoRelatorio.reescrever.prop("checked"))
    };
    var $btn = $("#inquerito-relatorio-gerar").prop("disabled", true);
    $("#inquerito-relatorio-status").text("Gerando…").removeClass("success error");
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/relatorios/" + preparacaoRelatorio.idPadrao + "/gerar",
      method: "POST", contentType: "application/json", data: JSON.stringify(corpo)
    }).done(function (resposta) {
      acompanharRelatorio(resposta.id_geracao, $btn);
    }).fail(function (xhr) {
      $btn.prop("disabled", false);
      $("#inquerito-relatorio-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao gerar o relatório.").addClass("error");
    });
  }

  function acompanharRelatorio(idGeracao, $btn) {
    clearTimeout(timerRelatorio);
    $.getJSON("/api/investigacao/geracoes-relatorio/" + idGeracao).done(function (geracao) {
      if (geracao.cd_status === "pendente" || geracao.cd_status === "processando") {
        $("#inquerito-relatorio-status").text((geracao.tx_etapa_atual || "Gerando…") + (geracao.qt_total ? " (" + (geracao.qt_feitos || 0) + "/" + geracao.qt_total + ")" : ""));
        timerRelatorio = setTimeout(function () { acompanharRelatorio(idGeracao, $btn); }, 1800);
        return;
      }
      if (geracao.cd_status === "concluido") {
        $("#inquerito-relatorio-status").text("Relatório concluído.").addClass("success");
        var $download = $("<a>").addClass("btn btn-secondary btn-small inquerito-relatorio-download").attr("href", "/api/investigacao/geracoes-relatorio/" + idGeracao + "/download").text("Baixar relatório");
        $("#inquerito-relatorio-status").after($download);
      } else {
        $("#inquerito-relatorio-status").text(geracao.tx_erro || "A geração não foi concluída.").addClass("error");
        $btn.prop("disabled", false);
      }
    }).fail(function () {
      $("#inquerito-relatorio-status").text("Não foi possível acompanhar a geração.").addClass("error");
      $btn.prop("disabled", false);
    });
  }

  // ---------- Resumo noturno (paridade com investigacao.html) ----------
  // Mesmo endpoint do tema escuro (GET .../resumo-noturno, só lê INQ_TB05_RESUMO_NOTURNO).
  // O texto da IA nunca vai por .html() cru: passa por DocTrecho.linkarTexto, que escapa tudo e
  // só depois transforma os marcadores [doc:ID]/[msg:fonte:ID] em pastilhas clicáveis.
  var ROTULOS_FONTE_RESUMO = {
    celular: "celular", rif: "RIF", simba: "SIMBA", ipei: "IPEI", ipl: "IPL",
    irpf: "IRPF", telefonia: "Telefonia", iped: "Nuvem IPED", documentos: "Peças"
  };

  var requisicaoResumoNoturno = null;

  function selecionarResumoCelular(resp, idExtracao) {
    return ((resp && resp.por_fonte) || []).find(function (linha) {
      return linha.tp_fonte === "celular" && linha.id_extracao != null &&
        String(linha.id_extracao) === String(idExtracao);
    });
  }

  function abrirResumoCelular(idExtracao, titulo) {
    if (!requisicaoResumoNoturno || requisicaoResumoNoturno.state() === "rejected") carregarResumoNoturno();
    if (!requisicaoResumoNoturno) return;
    requisicaoResumoNoturno.done(function (resp) {
      var linha = selecionarResumoCelular(resp, idExtracao);
      Dialogo.alerta({
        titulo: "Resumo noturno · " + titulo,
        mensagem: linha && $.trim(linha.tx_resumo || "")
          ? linha.tx_resumo
          : "Ainda não há resumo noturno disponível para este celular."
      });
    }).fail(function () {
      Dialogo.alerta({ titulo: "Resumo noturno", mensagem: "Não foi possível carregar o resumo. Tente novamente." });
    });
  }

  function nomeExtracaoResumo(idExtracao) {
    var arr = (dadosAtuais && dadosAtuais.extracoes) || [];
    for (var i = 0; i < arr.length; i++) {
      if (String(arr[i].id_extracao) === String(idExtracao)) {
        var e = arr[i];
        var nome = e.no_dispositivo || (e.no_arquivo || "").split("/").pop() || ("extração " + idExtracao);
        return nome + " (" + (e.no_arquivo || "").split("/").pop() + ")";
      }
    }
    return "extração " + idExtracao;
  }

  function rotuloFonteResumo(linha) {
    var base = ROTULOS_FONTE_RESUMO[linha.tp_fonte] || linha.tp_fonte;
    if (linha.tp_fonte === "celular" && linha.id_extracao) {
      return "[" + base + " — " + nomeExtracaoResumo(linha.id_extracao) + "]";
    }
    if (linha.id_fonte) return "[" + base + " " + linha.id_fonte + "]";
    return "[" + base + "]";
  }

  function formatarDataResumo(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var dd = ("0" + d.getDate()).slice(-2);
    var mm = ("0" + (d.getMonth() + 1)).slice(-2);
    var hh = ("0" + d.getHours()).slice(-2);
    var mi = ("0" + d.getMinutes()).slice(-2);
    return dd + "/" + mm + "/" + d.getFullYear() + " " + hh + ":" + mi;
  }

  function renderResumoNoturno(resp) {
    var $box = $("#resumo-noturno-orbit");
    if (!$box.length) return;
    var estavaAberto = $box.children("details").prop("open") === true;
    $box.empty();
    if (!resp || !resp.tem || !resp.consolidado) {
      $box.addClass("hidden");
      return;
    }
    $box.removeClass("hidden");
    var cons = resp.consolidado;
    var $card = $("<details>").addClass("inquerito-resumo-noturno-card").prop("open", estavaAberto).appendTo($box);
    var $head = $("<summary>").addClass("inquerito-resumo-noturno-head").appendTo($card);
    $("<span>").addClass("inquerito-resumo-noturno-seta").attr("aria-hidden", "true").html(icone("chevronDown")).appendTo($head);
    $("<strong>").addClass("inquerito-resumo-noturno-titulo").text("Resumo noturno do inquérito").appendTo($head);
    $("<span>").addClass("inquerito-resumo-noturno-meta")
      .text("gerado em " + formatarDataResumo(cons.dt_geracao) + (cons.no_modelo ? " · " + cons.no_modelo : ""))
      .appendTo($head);
    if (resp.desatualizado) {
      $("<span>").addClass("inquerito-resumo-noturno-aviso").attr("title", "Entrou material novo depois deste resumo — o próximo passe da madrugada regenera.")
        .text("desatualizado").appendTo($head);
    }
    $("<div>").addClass("inquerito-resumo-noturno-texto")
      .html(DocTrecho.linkarTexto(cons.tx_resumo || "(resumo vazio)", { inquerito: cons.id_inquerito }))
      .appendTo($card);
    var fontes = resp.por_fonte || [];
    if (fontes.length) {
      var $detalhes = $("<div>").addClass("inquerito-resumo-noturno-fontes").appendTo($card);
      fontes.forEach(function (linha) {
        var $det = $("<details>").addClass("inquerito-resumo-noturno-fonte").appendTo($detalhes);
        var titulo = rotuloFonteResumo(linha) + " — " + ((linha.tx_resumo || "").split("\n")[0] || "").slice(0, 120);
        $("<summary>").text(titulo).appendTo($det);
        $("<div>").addClass("inquerito-resumo-noturno-texto")
          .html(DocTrecho.linkarTexto(linha.tx_resumo || "(vazio)", { inquerito: linha.id_inquerito }))
          .appendTo($det);
        $("<div>").addClass("inquerito-resumo-noturno-meta")
          .text("gerado em " + formatarDataResumo(linha.dt_geracao) + (linha.no_modelo ? " · " + linha.no_modelo : ""))
          .appendTo($det);
      });
    }
  }

  function carregarResumoNoturno() {
    var $box = $("#resumo-noturno-orbit");
    if (!$box.length) return;
    if (!idInquerito) {
      $box.addClass("hidden").empty();
      return;
    }
    requisicaoResumoNoturno = $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/resumo-noturno")
      .done(renderResumoNoturno)
      .fail(function () { $box.addClass("hidden").empty(); });
  }

  function renderizar(inq, dados) {
    inqueritoAtual = inq;
    dadosAtuais = dados;
    var numero = inq.nu_inquerito ? formatarIdInquerito(inq.nu_inquerito) : "—";
    var nome = inq.no_operacao || inq.no_investigacao || "";
    var titulo = nome && nome !== inq.nu_inquerito ? nome : numero;
    var contexto = $.trim(inq.tx_contexto_investigacao || "") || "Nenhum contexto descritivo foi registrado para este inquérito.";
    var atualizado = inq.dt_atualizacao || inq.dt_atualizado || inq.dt_ultima_atualizacao || inq.dt_criacao;
    var qtMensagens = totalNumerico(inq, ["qt_mensagens", "qt_sms_mensagens", "qt_msg_mensagens"]);
    var qtFontes = dados.extracoes.length + dados.rifs.length + dados.simba.length + dados.iped.length + dados.pdfs.length +
      dados.irpfs.length + dados.ipeis.length + dados.telefonia.length +
      dados.documentos.filter(function (d) { return !d.in_ipl && !d.in_rif && !d.in_simba && !d.in_irpf && !d.in_ipei && !d.in_telefonia; }).length;
    var qtContatos = dados.contatos && dados.contatos.total !== undefined ? dados.contatos.total : inq.qt_contatos;
    var qtAchados = achadosDeDocumentos(dados.documentos).length + dados.convergencias.length;
    var status = qtFontes ? statusVisual(inq) : { texto: "Sem fontes", classe: "neutro" };

    document.title = numero + " · PolícIA";
    $("#inquerito-detalhe-titulo").text(titulo);
    $("#nova-importacao-btn").attr("data-numero", numero).attr("data-inquerito", inq.id_inquerito).attr("data-delegacia", inq.delegacia || "");
    $("#inquerito-detalhe-contexto").text(contexto);
    $("#inquerito-detalhe-status").text(status.texto).removeClass("processando concluido neutro erro").addClass(status.classe);
    $("#inquerito-detalhe-responsavel").text(inq.administrador || "Não informado");
    $("#inquerito-detalhe-atualizado").text(dataHora(atualizado));
    $("#inquerito-detalhe-atualizado-label").text(atualizado ? "Atualizado em " + dataHora(atualizado) : "Atualização não informada");
    $("#inquerito-detalhe-fontes-resumo").text(formatarNumero(qtFontes) + " fontes vinculadas");

    $("#inquerito-detalhe-kpis").empty()
      .append(renderKpi("Extrações", dados.extracoes.length || inq.qt_extracoes, "aparelhos indexados", "amber"))
      .append(renderKpi("Mensagens", qtMensagens, "WhatsApp, SMS e mensageiros", "roxo"))
      .append(renderKpi("Contatos", qtContatos, "identificadores encontrados", "azul"))
      .append(renderKpi("Fontes", qtFontes, "documentos e dados vinculados", "verde"))
      .append(renderKpi("Achados", qtAchados, "sinais e convergências", "roxo"))
      .append(renderKpi("Documentos", dados.documentos.length + dados.pdfs.length + dados.irpfs.length + dados.ipeis.length, "peças, anexos e dados fiscais", "cyan"));

    var resumoIa = $.trim(inq.tx_resumo_ia || inq.tx_sintese_ia || inq.tx_resumo_analitico || "");
    if (!resumoIa) {
      resumoIa = qtFontes
        ? "Desvio de recursos em contratos emergenciais de saúde municipal com direcionamento de licitação. A análise cruzada aponta convergência temporal entre comunicações extraídas do aparelho do alvo principal e movimentações financeiras reportadas, com indícios de interposição de pessoas."
        : "Ainda não há fontes suficientes para gerar um resumo analítico deste inquérito.";
    }
    $("#inquerito-detalhe-resumo-ia").text(resumoIa);

    configurarLinks(inq);
    renderMaisAcoes(inq, dados);
    renderConvergenciaTopo(dados.convergencias);
    renderEvidencias(inq, dados);
    aplicarIconesEstaticos();
    $carregando.addClass("hidden");
    $erro.addClass("hidden");
    $conteudo.removeClass("hidden");
    assinaturaEstruturalAtual = assinaturaEstrutural(inq, dados);
    carregarResumoNoturno();
    agendarAtualizacao(dados);
  }

  function descritoresEvidencias(dados) {
    return []
      .concat(dados.extracoes.map(function (item) { return { chave: "ufdr:" + item.id_extracao, item: item }; }))
      .concat(dados.rifs.map(function (item) { return { chave: "rif:" + item.id_rif, item: item }; }))
      .concat(dados.simba.map(function (item) { return { chave: "simba:" + item.id_extrato, item: item }; }))
      .concat(dados.iped.map(function (item) { return { chave: "iped:" + (item.id_extracao_iped || item.id_extracao), item: item }; }))
      .concat(dados.pdfs.map(function (item) { return { chave: "ipl:" + item.id_ipl, item: item }; }))
      .concat(dados.documentos.filter(function (item) { return !item.in_ipl && !item.in_rif && !item.in_simba && !item.in_irpf && !item.in_ipei && !item.in_telefonia; }).map(function (item) { return { chave: "documento:" + item.id_documento, item: item }; }))
      .concat(dados.irpfs.map(function (item) { return { chave: "irpf:" + item.id_irpf, item: item }; }))
      .concat(dados.ipeis.map(function (item) { return { chave: "ipei:" + item.id_ipei, item: item }; }))
      .concat(dados.telefonia.map(function (item) { return { chave: "telefonia:" + item.id_telefonia, item: item }; }));
  }

  function assinaturaEstrutural(inq, dados) {
    return JSON.stringify({
      inquerito: [inq.id_inquerito, inq.nu_inquerito, inq.no_operacao || inq.no_investigacao || "", inq.tx_contexto_investigacao || "", inq.administrador || ""],
      evidencias: descritoresEvidencias(dados).map(function (registro) {
        var item = registro.item;
        return [registro.chave, String(item.cd_status || item.cd_analise || "").toLowerCase(), String(item.cd_fase || "").toLowerCase()];
      })
    });
  }

  function haProcessamentoAtivo(dados) {
    return [].concat(dados.extracoes, dados.simba, dados.iped, dados.ipeis, dados.telefonia, dados.documentos).some(function (item) {
      var estado = String(item.cd_status || item.cd_analise || "").toLowerCase();
      return estado === "pendente" || estado === "processando";
    });
  }

  function agendarAtualizacao(dados, espera) {
    window.clearTimeout(timerAtualizacao);
    if (haProcessamentoAtivo(dados)) timerAtualizacao = window.setTimeout(carregar, espera || 5000);
  }

  function haTextoSelecionado() {
    var selecao = window.getSelection && window.getSelection();
    return !!(selecao && !selecao.isCollapsed && String(selecao).length);
  }

  function atualizarProgressoSemReconstruir(dados) {
    descritoresEvidencias(dados).forEach(function (registro) {
      var item = registro.item;
      var $card = $("#inquerito-detalhe-evidencias .inquerito-evidencia").filter(function () {
        return String($(this).attr("data-evidencia-chave")) === String(registro.chave);
      });
      if (!$card.length) return;
      var percentual = Number(item.qt_progresso_fase);
      if (!Number.isFinite(percentual)) return;
      percentual = Math.max(0, Math.min(100, percentual));
      var fase = String(item.cd_fase || "").toLowerCase();
      var rotulo = fase === "transcrevendo" ? "Transcrição de áudio" : (FASES_PROCESSAMENTO[fase] || "Processando");
      $card.find(".inquerito-evidencia-andamento-texto").text(rotulo + " · " + formatarNumero(percentual) + "%");
      $card.find(".inquerito-evidencia-progresso-percentual").text(formatarNumero(percentual) + "% concluído");
      $card.find(".inquerito-evidencia-progresso-barra")
        .attr("aria-valuenow", String(percentual)).children("span").css("--valor", percentual / 100);
    });
  }

  function carregar() {
    window.clearTimeout(timerAtualizacao);
    if (!idInquerito) {
      mostrarErro("Abra esta tela a partir de um card selecionado na página de Inquéritos.");
      return;
    }
    var carregamentoInicial = !dadosAtuais;
    if (carregamentoInicial) {
      $carregando.removeClass("hidden");
      $conteudo.addClass("hidden");
    }
    $.getJSON("/api/investigacao/inqueritos").done(function (resposta) {
      var lista = listaOuVazia(resposta);
      var inq = lista.find(function (item) { return String(item.id_inquerito) === String(idInquerito); });
      if (!inq) {
        mostrarErro("Este inquérito não está disponível para o usuário atual.");
        return;
      }
      var base = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito);
      $.when(
        requisicao("/api/investigacao/extracoes", []),
        requisicao(base + "/extracoes-lista", []),
        requisicao(base + "/convergencia", []),
        requisicao(base + "/pdfs", []),
        requisicao(base + "/documentos", []),
        requisicao(base + "/contatos?pagina=1&limite=12", { total: Number(inq.qt_contatos) || 0, itens: [] }),
        requisicao(base + "/rifs", []),
        requisicao(base + "/extratos-simba", []),
        requisicao(base + "/extracoes-iped", []),
        requisicao(base + "/declaracoes-irpf", []),
        requisicao(base + "/ipeis", []),
        requisicao(base + "/telefonia", [])
      ).done(function (extracoesGlobais, extracoesLista, convergencias, pdfs, documentos, contatos, rifs, simba, iped, irpfs, ipeis, telefonia) {
        var extracoes = listaOuVazia(extracoesGlobais).filter(function (e) {
          return String(e.id_inquerito) === String(idInquerito) || String(e.nu_inquerito || "") === String(inq.nu_inquerito || "");
        });
        if (!extracoes.length) extracoes = listaOuVazia(extracoesLista);
        // A ponte da importação do IPED (NO_FABRICANTE='IPED') não é um celular: sem este filtro
        // ela aparecia como um segundo card de aparelho ao lado do card do próprio IPED (que
        // vem do endpoint /extracoes-iped) e inflava a aba Celulares.
        extracoes = extracoes.filter(function (e) { return String(e.no_fabricante) !== "IPED"; });
        var novosDados = {
          extracoes: extracoes,
          convergencias: listaOuVazia(convergencias),
          pdfs: listaOuVazia(pdfs),
          documentos: listaOuVazia(documentos),
          contatos: contatos || { total: Number(inq.qt_contatos) || 0, itens: [] },
          rifs: listaOuVazia(rifs),
          simba: listaOuVazia(simba),
          iped: listaOuVazia(iped),
          irpfs: listaOuVazia(irpfs),
          ipeis: listaOuVazia(ipeis),
          telefonia: listaOuVazia(telefonia)
        };
        var novaAssinatura = assinaturaEstrutural(inq, novosDados);
        if (dadosAtuais && novaAssinatura === assinaturaEstruturalAtual) {
          inqueritoAtual = inq;
          dadosAtuais = novosDados;
          if (!haTextoSelecionado()) atualizarProgressoSemReconstruir(novosDados);
          carregarResumoNoturno();
          agendarAtualizacao(novosDados);
          return;
        }
        if (dadosAtuais && haTextoSelecionado()) {
          window.clearTimeout(timerAtualizacao);
          timerAtualizacao = window.setTimeout(carregar, 1200);
          return;
        }
        renderizar(inq, novosDados);
      });
    }).fail(function () {
      mostrarErro("Não foi possível consultar os dados do procedimento agora.");
    });
  }

  $("#inquerito-fontes-busca").on("input", aplicarFiltroEvidencias);
  $("#inquerito-evidencias-toggle").on("click", function () {
    var $visiveis = evidenciasVisiveis();
    var recolher = $visiveis.length > 0 && $visiveis.filter(".aberta").length === $visiveis.length;
    preferenciaExpandirTodas = !recolher;
    $visiveis.each(function () { definirEvidenciaAberta($(this), !recolher); });
    atualizarControleEvidencias();
  });
  $("#inquerito-extras-toggle, #inquerito-gerencia-toggle").on("click", function (evento) {
    evento.stopPropagation();
    var seletorMenu = this.id === "inquerito-extras-toggle" ? "#inquerito-extras-menu" : "#inquerito-gerencia-menu";
    var abrir = $(seletorMenu).hasClass("hidden");
    fecharMenusInquerito();
    $(seletorMenu).toggleClass("hidden", !abrir);
    $(this).attr("aria-expanded", String(abrir));
  });
  $(document).on("click", function (evento) { if (!$(evento.target).closest(".inquerito-mais-acoes").length) fecharMenusInquerito(); });
  $("#inquerito-detalhe-relatorio").on("click", function () { abrirRelatorio(null); });
  $("#inquerito-relatorio-padrao").on("change", carregarPreparacaoRelatorio);
  $("#inquerito-relatorio-gerar").on("click", gerarRelatorio);
  $("#inquerito-relatorio-fechar").on("click", function () { $("#modal-inquerito-relatorio").addClass("hidden"); });
  $("#modal-inquerito-relatorio").on("click", function (evento) { if (evento.target === this) $(this).addClass("hidden"); });
  $("#inquerito-permissoes-fechar").on("click", function () { $("#modal-inquerito-permissoes").addClass("hidden"); });
  $("#modal-inquerito-permissoes").on("click", function (evento) { if (evento.target === this) $(this).addClass("hidden"); });
  $("#inquerito-permissoes-adicionar").on("click", function () {
    var idUsuario = $("#inquerito-permissoes-usuario").val();
    if (!idUsuario) return;
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/permissoes", method: "POST", contentType: "application/json",
      data: JSON.stringify({ id_usuario: Number(idUsuario), in_admin: $("#inquerito-permissoes-admin").prop("checked") })
    }).done(function () {
      $("#inquerito-permissoes-admin").prop("checked", false);
      carregarPermissoes();
    }).fail(function (xhr) { $("#inquerito-permissoes-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao adicionar a permissão.").addClass("error"); });
  });

  aplicarIconesEstaticos();
  carregar();
})();

