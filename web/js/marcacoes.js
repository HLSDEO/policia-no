/* Página "Marcações" — a árvore de Marcadores do IPED em tela cheia.
 *
 * À esquerda os marcadores DESTE investigador neste inquérito (nome, cor, contagem), com criar,
 * renomear, mesclar e apagar. À direita o que está dentro do marcador escolhido, com conteúdo:
 * conversas marcadas por inteiro e mensagens marcadas uma a uma, agrupadas por conversa.
 *
 * Duas exportações, e a diferença entre elas importa:
 *   · planilha (js/exportar_excel.js) — uma linha por item, para cruzar com outras listas;
 *   · .docx    — as bolhas do chat, pelo mesmo renderizador do bloco 'chat' do relatório
 *                (api/funcoes/relatorios_docx.gerar_marcadores_docx).
 *
 * Marcar a CONVERSA e marcar as MENSAGENS dela são coisas diferentes: a primeira é triagem do
 * contato ("já li", "é relevante") e sai como uma linha de identificação; a segunda é o trecho
 * que vira prova e sai como bolha. O fio completo de uma conversa marcada só entra no .docx com
 * a caixa "Conteúdo das conversas marcadas" ligada (dentro do diálogo de exportação).
 */
(function () {
  "use strict";

  function paramUrl(nome, padrao) { return AnalyserUtils.paramUrl(nome, padrao); }
  function salvarUrl(v) { if (window.EstadoUrl) EstadoUrl.salvar(v); }

  var idInquerito = paramUrl("inquerito", null);
  var idExtracao = paramUrl("extracao", null);
  var estadoInicial = window.EstadoUrl ? EstadoUrl.ler(["fonte", "marcador", "busca", "tipo", "ordem"]) : {};
  var fonte = estadoInicial.fonte || paramUrl("fonte", "todas");
  var CORES = [
    "#E74C3C", "#E67E22", "#F1C40F", "#2ECC71", "#1ABC9C",
    "#3498DB", "#9B59B6", "#E91E63", "#795548", "#607D8B",
    "#00BCD4", "#FF9800",
  ];

  var marcadores = [];        // do endpoint de marcadores (nome, cor, qt_itens, qt_conversas, qt_mensagens)
  var sumario = [];           // do endpoint de sumário (marcador → conversas → mensagens)
  var idSelecionado = estadoInicial.marcador || paramUrl("marcador", null);

  // Estado de visualização da coluna direita (vive na URL para ser compartilhável)
  var busca = estadoInicial.busca || "";
  var tipo = estadoInicial.tipo || "tudo";       // tudo | conversas | mensagens
  var ordem = estadoInicial.ordem || "conversa-az";
  var soAnexos = false;
  var soComentarios = false;
  var buscaMarcadores = "";
  var colapsadas = {};        // chaveConversa -> true
  var selecionados = {};      // chaveItem -> {kind, conversa, msg}
  var carregando = false;

  var $listaMarcadores = $("#lista-marcadores");
  var $itensCorpo = $("#itens-corpo");
  var $itensVazio = $("#itens-vazio");
  var $itensTitulo = $("#itens-titulo");
  var $itensTotal = $("#itens-total");
  var $itensAcoes = $("#itens-acoes");
  var $ferramentas = $("#itens-ferramentas");
  var $selecao = $("#itens-selecao");
  var $loading = $("#itens-loading");
  var $erro = $("#itens-erro");
  var $btnLimparFiltros = $("#btn-limpar-filtros");

  if (!$("#filtro-fonte option[value='todas']").length) {
    $("#filtro-fonte").prepend($("<option>").attr("value", "todas").text("Todas as fontes"));
  }
  $("#filtro-fonte").val(["todas", "wpp", "sms", "msg"].indexOf(fonte) >= 0 ? fonte : "todas");
  $("#busca-itens").val(busca);
  $("#filtro-tipo").val(tipo);
  $("#ordem-itens").val(ordem);

  if (!idInquerito) {
    $listaMarcadores.append($("<p>").addClass("empty-hint").text("Nenhum inquérito informado na URL."));
    return;
  }

  // ---------- utilidades ----------

  function formatarDataHora(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function fonteDaConversa(conversa) {
    return (conversa && conversa.fonte) || (fonte === "todas" ? "wpp" : fonte);
  }

  // A tela depende do app da conversa, não só da fonte: WhatsApp Business chega como fonte=msg
  // e abre em whatsapp.html, os demais mensageiros em mensageiros.html (ver MapaTelas.paginaConversa).
  function urlMensageiro(conversa, extra) {
    var f = fonteDaConversa(conversa);
    var app = conversa && conversa.tp_app;
    var url = MapaTelas.paginaConversa(f, app) + "?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + encodeURIComponent(f) +
      (f === "msg" && app ? "&app=" + encodeURIComponent(app) : "") +
      (idExtracao ? "&extracao=" + encodeURIComponent(idExtracao) : "");
    return extra ? url + extra : url;
  }

  function marcadorSelecionado() {
    for (var i = 0; i < sumario.length; i++) {
      if (String(sumario[i].id_marcador) === String(idSelecionado)) return sumario[i];
    }
    return null;
  }

  function dadosDoMarcador(id) {
    for (var i = 0; i < marcadores.length; i++) {
      if (String(marcadores[i].id_marcador) === String(id)) return marcadores[i];
    }
    return null;
  }

  function chaveConversa(c) { return "c:" + fonteDaConversa(c) + ":" + c.id_conversa; }
  function chaveMensagem(m) { return "m:" + (m.id_marcacao || m.id_mensagem); }

  function dataMaisRecente(conversa) {
    var max = 0;
    (conversa.mensagens || []).forEach(function (m) {
      var t = m.dt_mensagem ? new Date(m.dt_mensagem).getTime() : 0;
      if (t > max) max = t;
    });
    return max;
  }

  // ---------- coluna esquerda ----------

  function contagemMarcador(m) {
    if (m.qt_conversas != null || m.qt_mensagens != null) {
      return (m.qt_conversas || 0) + " conv · " + (m.qt_mensagens || 0) + " msg";
    }
    return String(m.qt_itens || 0);
  }

  function renderMarcadores() {
    $listaMarcadores.empty();
    var termo = (buscaMarcadores || "").toLowerCase();
    var visiveis = marcadores.filter(function (m) {
      return !termo || (m.no_marcador || "").toLowerCase().indexOf(termo) >= 0;
    });
    if (!visiveis.length) {
      $listaMarcadores.append($("<p>").addClass("empty-hint").text(
        marcadores.length ? "Nenhum marcador com esse nome." :
        "Nenhum marcador ainda. Crie um aqui, ou pelo 🗒️ de uma mensagem/conversa na tela do mensageiro."
      ));
      return;
    }
    visiveis.forEach(function (m) {
      var ativo = String(m.id_marcador) === String(idSelecionado);
      var $item = $("<button>").attr({ type: "button", role: "option", "aria-selected": ativo ? "true" : "false" })
        .addClass("mrc-item" + (ativo ? " ativo" : ""))
        .attr("title", (m.tx_descricao || m.no_marcador) + " — " + contagemMarcador(m))
        .on("click", function () { selecionar(m.id_marcador); });
      $("<span>").addClass("mrc-item-bola").css("background", m.co_cor).appendTo($item);
      $("<span>").addClass("mrc-item-nome").text(m.no_marcador).appendTo($item);
      $("<span>").addClass("mrc-item-qt").text(contagemMarcador(m)).appendTo($item);
      $listaMarcadores.append($item);
    });
  }

  function selecionar(id) {
    idSelecionado = id;
    selecionados = {};
    salvarUrl({ marcador: id });
    renderMarcadores();
    renderItens();
    if (window.innerWidth <= 900) irParaTab("itens");
  }

  // ---------- coluna direita: filtragem ----------

  function conversasFiltradas(marcador) {
    var lista = (marcador && marcador.conversas) || [];
    var t = (busca || "").trim().toLowerCase();
    var out = [];
    lista.forEach(function (c) {
      var msgs = (c.mensagens || []).filter(function (m) {
        if (soAnexos && !(m.qt_anexos > 0)) return false;
        if (soComentarios && !(m.tx_comentario || c.tx_comentario)) return false;
        if (!t) return true;
        return ((c.titulo || "").toLowerCase().indexOf(t) >= 0) ||
          ((m.tx_mensagem || m.trecho || "").toLowerCase().indexOf(t) >= 0) ||
          ((m.remetente || "").toLowerCase().indexOf(t) >= 0) ||
          ((m.tx_comentario || "").toLowerCase().indexOf(t) >= 0) ||
          ((c.tx_comentario || "").toLowerCase().indexOf(t) >= 0);
      });
      var conversaCasa = !t || (c.titulo || "").toLowerCase().indexOf(t) >= 0 ||
        (c.tx_comentario || "").toLowerCase().indexOf(t) >= 0;
      if (tipo === "conversas" && !c.marcada) return;
      if (tipo === "conversas" && t && !conversaCasa) return;
      if (tipo === "mensagens" && !msgs.length) return;
      if (tipo === "tudo" && !conversaCasa && !msgs.length) {
        if (t) return;
      }
      if (soAnexos && tipo !== "mensagens" && !msgs.length && !(c.marcada && false)) {
        // em "tudo", conversa marcada sem mensagem filtrada ainda aparece se o título casa
        if (!conversaCasa) return;
      }
      out.push({ conversa: c, mensagens: tipo === "conversas" ? [] : (conversaCasa && t ? c.mensagens.filter(function (m) {
        if (soAnexos && !(m.qt_anexos > 0)) return false;
        if (soComentarios && !m.tx_comentario) return false;
        return true;
      }) : msgs) });
    });
    if (ordem === "recentes") {
      out.sort(function (a, b) { return dataMaisRecente(b.conversa) - dataMaisRecente(a.conversa); });
    } else if (ordem === "mais-msgs") {
      out.sort(function (a, b) { return b.mensagens.length - a.mensagens.length; });
    } else {
      out.sort(function (a, b) {
        return (a.conversa.titulo || "").toLowerCase().localeCompare((b.conversa.titulo || "").toLowerCase());
      });
    }
    return out;
  }

  function haFiltroAtivo() {
    return !!(busca || soAnexos || soComentarios || tipo !== "tudo");
  }

  // ---------- planilha ----------

  function dadosPlanilha() {
    var colunas = ["Marcador", "Tipo", "Conversa", "Fonte", "Data", "Remetente", "Conteúdo", "Anexos", "Comentário"];
    var linhas = [];
    var alvos = idSelecionado ? [marcadorSelecionado()].filter(Boolean) :
      sumario.slice();
    alvos.forEach(function (marcador) {
      if (!marcador) return;
      conversasFiltradas(marcador).forEach(function (par) {
        var conversa = par.conversa;
        if (conversa.marcada && tipo !== "mensagens") {
          linhas.push([
            marcador.no_marcador, "Conversa marcada", conversa.titulo, fonteDaConversa(conversa), "",
            "", conversa.qt_mensagens_conversa + " mensagens no fio", "",
            conversa.tx_comentario || "",
          ]);
        }
        par.mensagens.forEach(function (msg) {
          linhas.push([
            marcador.no_marcador, "Mensagem", conversa.titulo, fonteDaConversa(conversa),
            formatarDataHora(msg.dt_mensagem),
            msg.remetente || (msg.in_enviada ? "(dono do aparelho)" : ""),
            msg.tx_mensagem || msg.trecho || "",
            msg.qt_anexos || 0,
            msg.tx_comentario || "",
          ]);
        });
      });
    });
    return { colunas: colunas, linhas: linhas };
  }

  // ---------- render da direita ----------

  function renderMensagem(conversa, msg) {
    var key = chaveMensagem(msg);
    var $linha = $("<div>").addClass("mrc-msg");
    var $chk = $("<input>").attr({ type: "checkbox", title: "Selecionar esta mensagem" })
      .addClass("mrc-check").prop("checked", !!selecionados[key])
      .on("change", function () {
        if ($(this).prop("checked")) selecionados[key] = { kind: "mensagem", conversa: conversa, msg: msg };
        else delete selecionados[key];
        atualizarBarraSelecao();
      });
    $linha.append($chk);
    var $meta = $("<div>").addClass("mrc-msg-meta").appendTo($linha);
    $("<div>").text(formatarDataHora(msg.dt_mensagem)).appendTo($meta);
    $("<div>").text(msg.remetente || (msg.in_enviada ? "(dono do aparelho)" : "—")).appendTo($meta);

    var $corpo = $("<div>").addClass("mrc-msg-corpo").appendTo($linha);
    $("<div>").addClass("mrc-msg-texto").text(msg.tx_mensagem || msg.trecho).appendTo($corpo);
    if (msg.qt_anexos) {
      $("<div>").addClass("mrc-msg-anexo").text("📎 " + msg.qt_anexos + " anexo(s)").appendTo($corpo);
    }
    if (msg.tx_comentario) {
      $("<div>").addClass("mrc-msg-comentario").text(msg.tx_comentario).appendTo($corpo);
    }

    var $acoes = $("<div>").addClass("mrc-msg-acoes").appendTo($linha);
    $("<a>").addClass("mrc-msg-abrir").text("abrir ↗")
      .attr({ href: urlMensageiro(conversa, "&msg=" + encodeURIComponent(msg.id_mensagem)),
              title: "Abre a conversa posicionada nesta mensagem" })
      .appendTo($acoes);
    $("<button>").attr("type", "button").addClass("mrc-mini-btn").text("×")
      .attr("title", "Remover esta mensagem do marcador")
      .on("click", function () { removerItens([{ kind: "mensagem", conversa: conversa, msg: msg }]); })
      .appendTo($acoes);
    return $linha;
  }

  function renderConversa(marcador, par) {
    var conversa = par.conversa;
    var key = chaveConversa(conversa);
    var fechada = !!colapsadas[key];
    var $bloco = $("<div>").addClass("mrc-conversa" + (fechada ? " fechada" : ""));
    var $cab = $("<div>").addClass("mrc-conversa-cabecalho").appendTo($bloco);
    var $chk = $("<input>").attr({ type: "checkbox", title: "Selecionar esta conversa" })
      .addClass("mrc-check").prop("checked", !!selecionados[key])
      .on("click", function (e) { e.stopPropagation(); })
      .on("change", function () {
        if ($(this).prop("checked")) selecionados[key] = { kind: "conversa", conversa: conversa };
        else delete selecionados[key];
        atualizarBarraSelecao();
      });
    $cab.append($chk);
    var $toggle = $("<button>").attr({
        type: "button", "aria-expanded": fechada ? "false" : "true",
        title: fechada ? "Expandir conversa" : "Recolher conversa"
      }).addClass("mrc-toggle").text(fechada ? "▸" : "▾")
      .on("click", function () {
        if (colapsadas[key]) delete colapsadas[key]; else colapsadas[key] = true;
        renderItens();
      });
    $cab.append($toggle);
    $("<a>").addClass("mrc-conversa-nome").text(conversa.titulo)
      .attr({ href: urlMensageiro(conversa, ""), title: "Abrir conversa (" + fonteDaConversa(conversa) + ")" })
      .appendTo($cab);
    if (fonte === "todas") {
      $("<span>").addClass("mrc-fonte").text(fonteDaConversa(conversa)).appendTo($cab);
    }
    if (conversa.marcada) {
      $("<span>").addClass("mrc-etiqueta")
        .attr("title", "A conversa está marcada (triagem do contato). As mensagens dela não ficam marcadas por isso.")
        .text("conversa marcada").appendTo($cab);
      $("<span>").addClass("mrc-item-qt").text(conversa.qt_mensagens_conversa + " mensagens no fio").appendTo($cab);
      $("<button>").attr("type", "button").addClass("mrc-mini-btn").text("×")
        .attr("title", "Remover a marcação da conversa (mantém as mensagens)")
        .on("click", function (e) {
          e.stopPropagation();
          removerItens([{ kind: "conversa", conversa: conversa }]);
        }).appendTo($cab);
    }

    if (fechada) {
      $("<span>").addClass("mrc-item-qt")
        .text(par.mensagens.length + " mensagem(ns)").appendTo($cab);
      return $bloco;
    }
    if (conversa.tx_comentario) {
      $("<div>").addClass("mrc-conversa-comentario").text(conversa.tx_comentario).appendTo($bloco);
    }
    if (!par.mensagens.length && conversa.marcada && tipo === "mensagens") {
      $("<div>").addClass("mrc-msg mrc-msg-vazia")
        .text("Só a conversa está marcada — nenhuma mensagem individual.").appendTo($bloco);
    }
    par.mensagens.forEach(function (msg) { $bloco.append(renderMensagem(conversa, msg)); });
    return $bloco;
  }

  function atualizarBarraSelecao() {
    var n = Object.keys(selecionados).length;
    $selecao.toggleClass("hidden", n === 0);
    $("#selecao-contagem").text(n + " selecionado(s)");
    // sincroniza checkboxes sem re-render completo
    $itensCorpo.find(".mrc-check").each(function () { /* estado já está no DOM */ });
  }

  function renderItens() {
    $itensCorpo.empty();
    var marcador = marcadorSelecionado();
    var dados = dadosDoMarcador(idSelecionado);

    if (!idSelecionado) {
      $itensTitulo.text("Selecione um marcador");
      $itensTotal.text("");
      $itensAcoes.addClass("hidden");
      $ferramentas.addClass("hidden");
      $itensVazio.hide();
      $btnLimparFiltros.addClass("hidden");
      return;
    }

    $itensTitulo.text(dados ? dados.no_marcador : (marcador ? marcador.no_marcador : "Marcador"));
    $itensAcoes.removeClass("hidden");
    $ferramentas.removeClass("hidden");

    if (!marcador || !marcador.conversas.length) {
      $itensTotal.text("");
      var nomeFonte = $("#filtro-fonte option:selected").text();
      $itensVazio.text(
        "Nada marcado com esta etiqueta em " + nomeFonte +
        ". Ela pode ter registros em outro mensageiro ou em outras telas do inquérito."
      ).show();
      $btnLimparFiltros.addClass("hidden");
      atualizarBarraSelecao();
      return;
    }

    var pares = conversasFiltradas(marcador);
    var nMsg = pares.reduce(function (a, p) { return a + p.mensagens.length; }, 0);
    var nConvMarcada = pares.filter(function (p) { return p.conversa.marcada; }).length;
    $itensTotal.text(pares.length + " conversa(s) · " + nMsg + " mensagem(ns)" +
      (nConvMarcada ? " · " + nConvMarcada + " fio(s) marcado(s)" : ""));

    if (!pares.length) {
      $itensVazio.text(
        haFiltroAtivo() ? "Nada bate com a busca/filtros neste marcador." :
        "Nada marcado com esta etiqueta."
      ).show();
      $btnLimparFiltros.toggleClass("hidden", !haFiltroAtivo());
      atualizarBarraSelecao();
      return;
    }
    $itensVazio.hide();
    $btnLimparFiltros.toggleClass("hidden", !haFiltroAtivo());
    pares.forEach(function (par) {
      $itensCorpo.append(renderConversa(marcador, par));
    });
    atualizarBarraSelecao();
  }

  function mostrarCarregando() {
    $loading.removeClass("hidden").empty();
    for (var i = 0; i < 4; i++) $("<div>").addClass("skeleton-bloco mrc-skel").appendTo($loading);
    $erro.addClass("hidden");
  }

  // ---------- carregamento ----------

  function carregar() {
    if (carregando) return;
    carregando = true;
    mostrarCarregando();
    var reqMarcadores = $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/marcadores");
    var reqSumario = $.getJSON(
      "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/sumario?fonte=" +
      encodeURIComponent(fonte) + (idExtracao ? "&extracao=" + encodeURIComponent(idExtracao) : "")
    );
    $.when(reqMarcadores, reqSumario).done(function (respMarcadores, respSumario) {
      marcadores = respMarcadores[0] || [];
      sumario = respSumario[0] || [];
      $erro.addClass("hidden");
      if (!dadosDoMarcador(idSelecionado)) idSelecionado = marcadores.length ? marcadores[0].id_marcador : null;
      salvarUrl({ marcador: idSelecionado });
      renderMarcadores();
      renderItens();
    }).fail(function (xhr) {
      var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível carregar os marcadores.";
      $erro.removeClass("hidden");
      $("#itens-erro-msg").text(msg);
      $listaMarcadores.empty().append($("<p>").addClass("empty-hint").text(msg));
    }).always(function () {
      carregando = false;
      $loading.addClass("hidden").empty();
    });
  }

  // ---------- remover / mover itens ----------

  function itensParaLote(itens) {
    return itens.map(function (it) {
      if (it.kind === "conversa") {
        var f = fonteDaConversa(it.conversa);
        var tp = (f === "wpp" ? "wpp_conversa" : f === "sms" ? "sms_conversa" : "msg_conversa");
        return { tp_origem: tp, id_origem: it.conversa.id_conversa };
      }
      var msg = it.msg;
      var fm = msg.fonte || fonteDaConversa(it.conversa);
      var tpm = (fm === "wpp" ? "wpp_mensagem" : fm === "sms" ? "sms_mensagem" : "msg_mensagem");
      return { tp_origem: tpm, id_origem: msg.id_mensagem };
    });
  }

  function removerItens(itens) {
    if (!itens.length) return;
    var rotulo = itens.length === 1
      ? (itens[0].kind === "conversa" ? 'a conversa "' + itens[0].conversa.titulo + '"' : "esta mensagem")
      : (itens.length + " itens");
    Dialogo.confirmar({
      titulo: "Remover do marcador",
      mensagem: "Tirar " + rotulo + ' do marcador "' +
        ((dadosDoMarcador(idSelecionado) || {}).no_marcador || "") +
        '"? O registro continua marcado para o relatório — só perde esta etiqueta.',
      rotuloOk: "Remover",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/lote",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ itens: itensParaLote(itens), ids_marcadores: [idSelecionado], acao: "remover" })
      }).done(function () {
        selecionados = {};
        carregar();
        if (window.Toast) Toast.sucesso("Removido do marcador.");
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao remover." });
      });
    });
  }

  function abrirModalMover() {
    var keys = Object.keys(selecionados);
    if (!keys.length) return;
    var outros = marcadores.filter(function (m) { return String(m.id_marcador) !== String(idSelecionado); });
    if (!outros.length) {
      Dialogo.alerta({ titulo: "Mover", mensagem: "Crie outro marcador para mover estes itens." });
      return;
    }
    var $overlay = $(
      '<div class="modal-overlay" id="modal-mover">' +
        '<div class="modal ent-modal"><div class="modal-header"><h2>Mover seleção</h2>' +
        '<button class="modal-close" data-fechar>&times;</button></div><div class="modal-body">' +
        '<p class="card-hint">Move ' + keys.length + ' item(ns) para outro marcador (adiciona lá e remove daqui).</p>' +
        '<div class="field"><label>Destino</label><select id="mv-destino" style="width:100%"></select></div>' +
        '<div class="form-actions"><button class="btn btn-primary" id="mv-ok">Mover</button>' +
        '<span class="form-status" id="mv-status"></span></div>' +
        "</div></div></div>"
    ).appendTo("body");
    outros.forEach(function (m) {
      $("<option>").attr("value", m.id_marcador).text(m.no_marcador).appendTo($overlay.find("#mv-destino"));
    });
    function fechar() { $overlay.remove(); }
    $overlay.find("[data-fechar]").on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === this) fechar(); });
    $(document).on("keydown.mover", function (e) { if (e.key === "Escape") { fechar(); $(document).off("keydown.mover"); } });
    $overlay.find("#mv-ok").on("click", function () {
      var dest = $overlay.find("#mv-destino").val();
      var itens = keys.map(function (k) { return selecionados[k]; });
      $overlay.find("#mv-status").text("Movendo…");
      $.ajax({
        url: "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/lote",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({ itens: itensParaLote(itens), ids_marcadores: [parseInt(dest, 10)], acao: "adicionar" })
      }).done(function () {
        $.ajax({
          url: "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/lote",
          method: "POST", contentType: "application/json",
          data: JSON.stringify({ itens: itensParaLote(itens), ids_marcadores: [idSelecionado], acao: "remover" })
        }).always(function () {
          $(document).off("keydown.mover");
          fechar();
          selecionados = {};
          idSelecionado = parseInt(dest, 10);
          carregar();
        });
      }).fail(function (xhr) {
        $overlay.find("#mv-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao mover.").addClass("error");
      });
    });
  }

  // ---------- modal de criar/editar marcador ----------

  var $modal = $(
    '<div class="modal-overlay hidden" id="modal-marcador">' +
      '<div class="modal ent-modal">' +
        '<div class="modal-header"><h2 id="mm-titulo">Novo marcador</h2>' +
        '<button class="modal-close" id="mm-fechar">&times;</button></div>' +
        '<div class="modal-body">' +
          '<div class="field">' +
            '<label for="mm-nome">Nome</label>' +
            '<input type="text" id="mm-nome" maxlength="120" placeholder="ex.: TRÁFICO DE DROGAS">' +
            '<p class="field-help" id="mm-nome-aviso"></p>' +
          '</div>' +
          '<div class="field">' +
            '<label>Cor</label>' +
            '<div class="rel-cores mrc-modal-cores" id="mm-cores"></div>' +
          '</div>' +
          '<div class="field">' +
            '<label for="mm-descricao">Descrição (opcional) <span id="mm-desc-cont" class="mrc-cont"></span></label>' +
            '<textarea id="mm-descricao" rows="2" maxlength="2000"></textarea>' +
          '</div>' +
          '<div class="form-actions">' +
            '<button class="btn btn-primary" id="mm-salvar">Salvar</button>' +
            '<span class="form-status" id="mm-status"></span>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>'
  ).appendTo("body");

  var mmCor = CORES[0];
  var mmEditando = null;

  CORES.forEach(function (c, i) {
    $("<span>").addClass("rel-cor-bola" + (i === 0 ? " selecionada" : ""))
      .css("background", c).data("cor", c)
      .attr({ role: "radio", tabindex: "0", title: c })
      .on("click", function () {
        $("#mm-cores").find(".rel-cor-bola").removeClass("selecionada");
        $(this).addClass("selecionada");
        mmCor = c;
      })
      .on("keydown", function (e) { if (e.key === "Enter" || e.key === " ") $(this).trigger("click"); })
      .appendTo($("#mm-cores", $modal));
  });

  function selecionarCorModal(cor) {
    mmCor = cor || CORES[0];
    $("#mm-cores").find(".rel-cor-bola").each(function () {
      $(this).toggleClass("selecionada", $(this).data("cor") === mmCor);
    });
  }

  function nomeDuplicado(nome, excetoId) {
    nome = (nome || "").trim().toLowerCase();
    return marcadores.some(function (m) {
      return (m.no_marcador || "").trim().toLowerCase() === nome &&
        String(m.id_marcador) !== String(excetoId);
    });
  }

  var ultimoFocoModal = null;
  function abrirModal(marcador) {
    ultimoFocoModal = document.activeElement;
    mmEditando = marcador || null;
    $("#mm-titulo").text(marcador ? "Editar marcador" : "Novo marcador");
    $("#mm-nome").val(marcador ? marcador.no_marcador : "");
    $("#mm-descricao").val(marcador ? (marcador.tx_descricao || "") : "");
    $("#mm-nome-aviso").text("");
    $("#mm-status").text("").removeClass("success error");
    atualizarContDesc();
    selecionarCorModal(marcador ? marcador.co_cor : CORES[0]);
    $modal.removeClass("hidden");
    $("#mm-nome").trigger("focus");
  }

  function fecharModal() {
    $modal.addClass("hidden");
    mmEditando = null;
    if (ultimoFocoModal && ultimoFocoModal.focus) ultimoFocoModal.focus();
  }

  function atualizarContDesc() {
    $("#mm-desc-cont").text(($("#mm-descricao").val() || "").length + "/2000");
  }
  $("#mm-descricao").on("input", atualizarContDesc);
  $("#mm-nome").on("input", function () {
    var v = $(this).val();
    $("#mm-nome-aviso").text(
      nomeDuplicado(v, mmEditando && mmEditando.id_marcador) ? "Já existe um marcador com esse nome — salvar vai atualizar a cor/descrição." : ""
    );
  });
  // Enter salva, ESC fecha
  $modal.on("keydown", function (e) {
    if (e.key === "Escape") fecharModal();
    else if (e.key === "Enter" && e.target.id !== "mm-descricao") $("#mm-salvar").trigger("click");
  });

  $("#mm-fechar").on("click", fecharModal);
  $modal.on("click", function (e) { if (e.target === this) fecharModal(); });

  $("#mm-salvar").on("click", function () {
    var nome = $.trim($("#mm-nome").val());
    if (!nome) { $("#mm-status").text("Dê um nome ao marcador.").addClass("error"); return; }
    var corpo = { nome: nome, cor: mmCor, descricao: $.trim($("#mm-descricao").val()) || null };
    var url = mmEditando
      ? "/api/investigacao/marcadores/" + mmEditando.id_marcador
      : "/api/investigacao/inqueritos/" + idInquerito + "/marcadores";
    $("#mm-status").text("Salvando...").removeClass("success error");
    $.ajax({ url: url, method: "POST", contentType: "application/json", data: JSON.stringify(corpo) })
      .done(function (resp) {
        if (!mmEditando && resp.marcador) idSelecionado = resp.marcador.id_marcador;
        fecharModal();
        carregar();
      })
      .fail(function (xhr) {
        $("#mm-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.").addClass("error");
      });
  });

  // ---------- modal mesclar com select ----------

  function abrirModalMesclar() {
    var origem = dadosDoMarcador(idSelecionado);
    if (!origem) return;
    var outros = marcadores.filter(function (m) { return String(m.id_marcador) !== String(origem.id_marcador); });
    if (!outros.length) {
      Dialogo.alerta({ titulo: "Mesclar", mensagem: "É preciso ter outro marcador para mesclar com este." });
      return;
    }
    var $overlay = $(
      '<div class="modal-overlay" id="modal-mesclar">' +
        '<div class="modal ent-modal"><div class="modal-header"><h2>Mesclar marcador</h2>' +
        '<button class="modal-close" data-fechar>&times;</button></div><div class="modal-body">' +
        '<p class="card-hint">Leva os registros de "' + $("<div>").text(origem.no_marcador).html() +
        '" para o destino e apaga a origem. Quem já estava nos dois continua uma vez só.</p>' +
        '<div class="field"><label>Destino</label><select id="mg-destino" style="width:100%"></select></div>' +
        '<p class="field-help" id="mg-info"></p>' +
        '<div class="form-actions"><button class="btn btn-danger" id="mg-ok">Mesclar e apagar origem</button>' +
        '<span class="form-status" id="mg-status"></span></div>' +
        "</div></div></div>"
    ).appendTo("body");
    outros.forEach(function (m) {
      $("<option>").attr("value", m.id_marcador)
        .text(m.no_marcador + " (" + contagemMarcador(m) + ")").appendTo($overlay.find("#mg-destino"));
    });
    function atualizarInfo() {
      var d = dadosDoMarcador($overlay.find("#mg-destino").val());
      $overlay.find("#mg-info").text(d ? ('Destino hoje: ' + contagemMarcador(d) + ".") : "");
    }
    $overlay.find("#mg-destino").on("change", atualizarInfo);
    atualizarInfo();
    function fechar() { $overlay.remove(); $(document).off("keydown.mesclar"); }
    $overlay.find("[data-fechar]").on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === this) fechar(); });
    $(document).on("keydown.mesclar", function (e) { if (e.key === "Escape") fechar(); });
    $overlay.find("#mg-ok").on("click", function () {
      var dest = $overlay.find("#mg-destino").val();
      $overlay.find("#mg-status").text("Mesclando…");
      $.ajax({
        url: "/api/investigacao/marcadores/" + origem.id_marcador + "/mesclar",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({ id_destino: parseInt(dest, 10) })
      }).done(function (resp) {
        fechar();
        idSelecionado = parseInt(dest, 10);
        carregar();
        if (window.Toast) Toast.sucesso("Mesclado (" + (resp.movidas || 0) + " aplicação(ões)).");
      }).fail(function (xhr) {
        $overlay.find("#mg-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao mesclar.").addClass("error");
      });
    });
  }

  // ---------- diálogo de exportação ----------

  function abrirModalExportar() {
    var $overlay = $(
      '<div class="modal-overlay" id="modal-export">' +
        '<div class="modal ent-modal"><div class="modal-header"><h2>Exportar</h2>' +
        '<button class="modal-close" data-fechar>&times;</button></div><div class="modal-body">' +
        '<div class="field"><label>Marcadores</label><div id="xp-lista" class="mrc-xp-lista"></div></div>' +
        '<div class="field"><label>Formato</label>' +
        '<label class="field-check"><input type="radio" name="xp-formato" value="docx" checked> .docx (bolhas de chat)</label>' +
        '<label class="field-check"><input type="radio" name="xp-formato" value="xlsx"> Excel (uma linha por item)</label></div>' +
        '<div class="field"><label class="field-check" title="Marcar uma conversa é triagem do contato; trazer o fio inteiro para o .docx é uma segunda decisão, feita aqui">' +
        '<input type="checkbox" id="xp-conteudo"> Conteúdo das conversas marcadas</label></div>' +
        '<p class="field-help" id="xp-previa"></p>' +
        '<div class="form-actions"><button class="btn btn-primary" id="xp-ok">Exportar</button>' +
        '<span class="form-status" id="xp-status"></span></div>' +
        "</div></div></div>"
    ).appendTo("body");
    var $lista = $overlay.find("#xp-lista");
    marcadores.forEach(function (m) {
      var $lab = $("<label>").addClass("field-check mrc-xp-item");
      var $chk = $("<input>").attr("type", "checkbox").val(m.id_marcador)
        .prop("checked", !idSelecionado || String(m.id_marcador) === String(idSelecionado));
      $lab.append($chk).append($("<span>").addClass("mrc-item-bola").css("background", m.co_cor))
        .append(document.createTextNode(" " + m.no_marcador + " (" + contagemMarcador(m) + ")"));
      $lista.append($lab);
    });
    function idsEscolhidos() {
      return $lista.find("input:checked").map(function () { return $(this).val(); }).get();
    }
    function atualizarPrevia() {
      var ids = idsEscolhidos();
      var nC = 0, nM = 0;
      sumario.forEach(function (s) {
        if (ids.length && ids.indexOf(String(s.id_marcador)) < 0) return;
        nC += s.qt_conversas || 0; nM += s.qt_mensagens || 0;
      });
      var fmt = $overlay.find("input[name=xp-formato]:checked").val();
      $overlay.find("#xp-previa").text(
        ids.length ? (ids.length + " marcador(es) · " + nC + " conversa(s) · " + nM + " mensagem(ns) em " + fmt) :
        "Escolha ao menos um marcador."
      );
    }
    $lista.on("change", atualizarPrevia);
    $overlay.find("input[name=xp-formato]").on("change", atualizarPrevia);
    atualizarPrevia();
    function fechar() { $overlay.remove(); $(document).off("keydown.export"); }
    $overlay.find("[data-fechar]").on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === this) fechar(); });
    $(document).on("keydown.export", function (e) { if (e.key === "Escape") fechar(); });
    $overlay.find("#xp-ok").on("click", function () {
      var ids = idsEscolhidos();
      if (!ids.length) { $overlay.find("#xp-status").text("Escolha ao menos um marcador.").addClass("error"); return; }
      var fmt = $overlay.find("input[name=xp-formato]:checked").val();
      var comConteudo = $overlay.find("#xp-conteudo").prop("checked");
      var $btn = $overlay.find("#xp-ok");
      if (window.Carregando) Carregando.botao($btn, "Exportando…");
      else $btn.prop("disabled", true);
      function done() {
        if (window.Carregando) Carregando.restaurar($btn);
        else $btn.prop("disabled", false);
        fechar();
      }
      if (fmt === "xlsx") {
        try {
          var dados = dadosPlanilhaPara(ids);
          if (!dados.linhas.length) {
            $overlay.find("#xp-status").text("Nada para exportar com esses filtros.").addClass("error");
            if (window.Carregando) Carregando.restaurar($btn);
            return;
          }
          window.ExportarExcel.exportar("marcacoes", dados.colunas, dados.linhas);
          done();
        } catch (e) {
          $overlay.find("#xp-status").text("Falha ao gerar o Excel.").addClass("error");
          if (window.Carregando) Carregando.restaurar($btn);
        }
        return;
      }
      var params = ["marcadores=" + encodeURIComponent(ids.join(","))];
      if (comConteudo) params.push("conteudo_conversas=true");
      fetch("/api/investigacao/inqueritos/" + idInquerito + "/marcadores/exportacao?" + params.join("&"), { credentials: "same-origin" })
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          var disp = resp.headers.get("Content-Disposition") || "";
          var nome = "marcacoes.docx";
          var m = disp.match(/filename\*=UTF-8''([^;]+)/);
          if (m) { try { nome = decodeURIComponent(m[1]); } catch (e) { /* mantém padrão */ } }
          return resp.blob().then(function (b) { return { blob: b, nome: nome }; });
        })
        .then(function (r) {
          var url = URL.createObjectURL(r.blob);
          var a = document.createElement("a");
          a.href = url; a.download = r.nome;
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
          done();
        })
        .catch(function () {
          $overlay.find("#xp-status").text("Falha ao gerar o .docx. Tente de novo.").addClass("error");
          if (window.Carregando) Carregando.restaurar($btn);
          else $btn.prop("disabled", false);
        });
    });
  }

  function dadosPlanilhaPara(ids) {
    var colunas = ["Marcador", "Tipo", "Conversa", "Fonte", "Data", "Remetente", "Conteúdo", "Anexos", "Comentário"];
    var linhas = [];
    sumario.forEach(function (marcador) {
      if (ids.indexOf(String(marcador.id_marcador)) < 0) return;
      conversasFiltradas(marcador).forEach(function (par) {
        var conversa = par.conversa;
        if (conversa.marcada && tipo !== "mensagens") {
          linhas.push([marcador.no_marcador, "Conversa marcada", conversa.titulo, fonteDaConversa(conversa),
            "", "", conversa.qt_mensagens_conversa + " mensagens no fio", "", conversa.tx_comentario || ""]);
        }
        par.mensagens.forEach(function (msg) {
          linhas.push([marcador.no_marcador, "Mensagem", conversa.titulo, fonteDaConversa(conversa),
            formatarDataHora(msg.dt_mensagem), msg.remetente || (msg.in_enviada ? "(dono do aparelho)" : ""),
            msg.tx_mensagem || msg.trecho || "", msg.qt_anexos || 0, msg.tx_comentario || ""]);
        });
      });
    });
    return { colunas: colunas, linhas: linhas };
  }

  // ---------- ações ----------

  $("#btn-novo-marcador").on("click", function () { abrirModal(null); });

  $("#btn-renomear").on("click", function () {
    var dados = dadosDoMarcador(idSelecionado);
    if (dados) abrirModal(dados);
  });

  $("#btn-apagar").on("click", function () {
    var dados = dadosDoMarcador(idSelecionado);
    if (!dados) return;
    Dialogo.confirmar({
      titulo: "Apagar marcador",
      mensagem: 'Apagar "' + dados.no_marcador + '"? Os registros continuam marcados para o ' +
                "relatório — eles só perdem esta etiqueta.",
      rotuloOk: "Apagar",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/marcadores/" + dados.id_marcador, method: "DELETE" })
        .done(function () { idSelecionado = null; salvarUrl({ marcador: "" }); carregar(); })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar." });
        });
    });
  });

  $("#btn-mesclar").on("click", abrirModalMesclar);
  $("#btn-exportar-menu").on("click", abrirModalExportar);
  $("#btn-tentar-de-novo").on("click", carregar);
  $("#btn-mover-selecao").on("click", abrirModalMover);
  $("#btn-limpar-selecao").on("click", function () { selecionados = {}; renderItens(); });
  $("#btn-remover-selecao").on("click", function () {
    var itens = Object.keys(selecionados).map(function (k) { return selecionados[k]; });
    removerItens(itens);
  });

  $("#busca-marcadores").on("input", function () {
    buscaMarcadores = $(this).val();
    renderMarcadores();
  });

  var buscaTimer = null;
  $("#busca-itens").on("input", function () {
    clearTimeout(buscaTimer);
    var v = $(this).val();
    buscaTimer = setTimeout(function () {
      busca = v;
      salvarUrl({ busca: busca });
      renderItens();
    }, 180);
  });
  $("#filtro-tipo").on("change", function () {
    tipo = $(this).val();
    salvarUrl({ tipo: tipo });
    renderItens();
  });
  $("#ordem-itens").on("change", function () {
    ordem = $(this).val();
    salvarUrl({ ordem: ordem });
    renderItens();
  });
  $("#filtro-anexos").on("change", function () {
    soAnexos = $(this).prop("checked");
    renderItens();
  });
  $("#filtro-comentarios").on("change", function () {
    soComentarios = $(this).prop("checked");
    renderItens();
  });
  $("#btn-expandir").on("click", function () { colapsadas = {}; renderItens(); });
  $("#btn-recolher").on("click", function () {
    var marcador = marcadorSelecionado();
    ((marcador && marcador.conversas) || []).forEach(function (c) { colapsadas[chaveConversa(c)] = true; });
    renderItens();
  });
  $btnLimparFiltros.on("click", function () {
    busca = ""; tipo = "tudo"; soAnexos = false; soComentarios = false;
    $("#busca-itens").val(""); $("#filtro-tipo").val("tudo");
    $("#filtro-anexos").prop("checked", false); $("#filtro-comentarios").prop("checked", false);
    salvarUrl({ busca: "", tipo: "" });
    renderItens();
  });

  $("#filtro-fonte").on("change", function () {
    fonte = $(this).val();
    salvarUrl({ fonte: fonte });
    carregar();
  });

  // Tabs mobile
  function irParaTab(qual) {
    $("#tab-marcadores").toggleClass("ativo", qual === "marcadores");
    $("#tab-itens").toggleClass("ativo", qual === "itens");
    $("#tab-marcadores").attr("aria-selected", qual === "marcadores" ? "true" : "false");
    $("#tab-itens").attr("aria-selected", qual === "itens" ? "true" : "false");
    $("#col-marcadores").toggleClass("mrc-ativa", qual === "marcadores");
    $("#col-itens").toggleClass("mrc-ativa", qual === "itens");
  }
  $("#tab-marcadores").on("click", function () { irParaTab("marcadores"); });
  $("#tab-itens").on("click", function () { irParaTab("itens"); });
  if (window.innerWidth <= 900) irParaTab(idSelecionado ? "itens" : "marcadores");

  carregar();
})();
