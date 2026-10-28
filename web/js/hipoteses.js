(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  // Visão "Hipótese da mídia": com escopo_tipo/escopo_id na URL a página filtra o quadro pelas
  // hipóteses ligadas àquela mídia (vínculo próprio ou evidência apontando a ela) e novas
  // hipóteses já nascem associadas a ela.
  var escopoTipo = paramUrl("escopo_tipo");
  var escopoId = paramUrl("escopo_id");
  var escopoTitulo = paramUrl("escopo_titulo") || "";
  var midiaFiltro = escopoTipo && escopoId ? escopoTipo + ":" + escopoId : "";

  var STATUSOrdem = ["proposta", "em_analise", "confirmada", "refutada", "arquivada"];
  var ROTULO_STATUS = {
    proposta: "Proposta", em_analise: "Em análise", confirmada: "Confirmada",
    refutada: "Refutada", arquivada: "Arquivada",
  };
  var ROTULO_NATUREZA = { hipotese: "Hipótese de trabalho", fato: "Fato apurado", normativa: "Disposição normativa" };
  var ROTULO_ESCOPO = {
    rif: "RIF", simba: "extrato do SIMBA", ipl: "inquérito em PDF (IPL)",
    irpf: "declaração de IRPF", ipei: "IPEI (quebra de sigilo fiscal)",
    telefonia: "telefonia (retorno da operadora)",
    extracao: "aparelho (extração)", documento: "documento anexado",
  };

  // Fontes de item importado para o vínculo de evidência: os mesmos endpoints que o card de
  // investigação usa; label() segue os títulos daquela tela. O seletor é montado uma vez por
  // tela (cache em itensCache) — abrir o painel de uma hipótese não torna a buscar.
  var FONTES_ITENS = [
    { tp: "extracao", rotulo: "Aparelhos (extração)", url: function () { return "/api/investigacao/extracoes?inquerito=" + encodeURIComponent(idInquerito); }, id: "id_extracao", label: function (x) { return x.no_dispositivo || x.no_arquivo || ("Extração " + x.id_extracao); } },
    { tp: "rif", rotulo: "RIF (COAF)", url: function () { return urlInq("rifs"); }, id: "id_rif", label: function (x) { return "RIF " + (x.nu_rif || x.no_pasta || x.id_rif); } },
    { tp: "simba", rotulo: "Extratos bancários (SIMBA)", url: function () { return urlInq("extratos-simba"); }, id: "id_extrato", label: function (x) { return "SIMBA · " + (x.no_arquivo || x.id_extrato); } },
    { tp: "ipl", rotulo: "Inquéritos em PDF", url: function () { return urlInq("pdfs"); }, id: "id_ipl", label: function (x) { return "IPL · " + (x.nu_ipl || x.no_arquivo || x.id_ipl); } },
    { tp: "irpf", rotulo: "Declarações de IRPF", url: function () { return urlInq("declaracoes-irpf"); }, id: "id_irpf", label: function (x) { return "IRPF · " + (x.no_titular || x.no_arquivo || x.id_irpf); } },
    { tp: "ipei", rotulo: "Sigilos fiscais (IPEI)", url: function () { return urlInq("ipeis"); }, id: "id_ipei", label: function (x) { return "IPEI " + (x.nu_ipei || x.no_arquivo || x.id_ipei); } },
    { tp: "telefonia", rotulo: "Telefonia (operadoras)", url: function () { return urlInq("telefonia"); }, id: "id_telefonia", label: function (x) { return "Telefonia · " + (x.no_operadora || x.no_arquivo || "operadora"); } },
    { tp: "documento", rotulo: "Documentos anexados", url: function () { return urlInq("documentos"); }, id: "id_documento", label: function (x) { return x.no_arquivo || ("Documento " + x.id_documento); } },
  ];

  function urlInq(sufixo) {
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/" + sufixo;
  }

  function urlLista() {
    var url = urlInq("hipoteses");
    if (midiaFiltro) {
      url += "?tp_escopo=" + encodeURIComponent(escopoTipo) + "&id_escopo=" + encodeURIComponent(escopoId);
    }
    return url;
  }

  var $titulo = $("#hipoteses-titulo-inquerito");
  var $quadro = $("#hipoteses-quadro");
  var $toolbar = $("#hipoteses-toolbar");
  var $busca = $("#hipoteses-busca");
  var $filtroResponsavel = $("#hipoteses-filtro-responsavel");
  var $filtroNatureza = $("#hipoteses-filtro-natureza");
  var $contagem = $("#hipoteses-contagem");
  var $estado = $("#hipoteses-estado");
  var $estadoTexto = $("#hipoteses-estado-texto");
  var $estadoAcoes = $("#hipoteses-estado-acoes");
  var $spinner = $("#hipoteses-spinner");

  var $modal = $("#modal-hipotese");
  var $modalTitulo = $("#modal-hipotese-titulo");
  var $form = $("#hipotese-form");
  var $texto = $("#hipotese-texto-input");
  var $contador = $("#hipotese-contador");
  var $statusSelect = $("#hipotese-status-select");
  var $responsavelSelect = $("#hipotese-responsavel-select");
  var $naturezaSelect = $("#hipotese-natureza-select");
  var $contestavel = $("#hipotese-contestavel-input");
  var $statusMsg = $("#hipotese-status");
  var $salvarBtn = $("#hipotese-salvar-btn");
  var $apagarBtn = $("#hipotese-apagar-btn");
  var $estrutura = $("#hipotese-estrutura");

  var hipoteses = [];
  var usuarios = [];
  var itensCache = null;       // [{tp, rotulo, itens:[{id,label}]}] — montado na 1ª abertura do painel
  var idEditando = null;       // null = criando; senão, id da hipótese aberta no painel
  var snapshotModal = "";

  if (!idInquerito) {
    $titulo.text("Inquérito não informado.");
    $("#nova-hipotese-btn").prop("disabled", true);
  }

  function msgErro(xhr, padrao) {
    return (xhr.responseJSON && xhr.responseJSON.detail) || padrao;
  }

  function mostrarEstado(texto, acoes) {
    $spinner.addClass("hidden");
    $estadoTexto.text(texto);
    $estadoAcoes.empty().addClass("hidden");
    if (acoes && acoes.length) {
      acoes.forEach(function (acao) {
        $("<button>").attr("type", "button").addClass("btn " + (acao.primario ? "btn-primary" : "btn-secondary"))
          .text(acao.rotulo).on("click", acao.aoClicar).appendTo($estadoAcoes);
      });
      $estadoAcoes.removeClass("hidden");
    }
    $estado.removeClass("hidden");
  }

  function esconderEstado() {
    $estado.addClass("hidden");
  }

  // ---------- quadro ----------

  function filtradas() {
    var termo = $.trim($busca.val() || "").toLowerCase();
    var responsavel = $filtroResponsavel.val() || "";
    var natureza = $filtroNatureza.val() || "";
    return hipoteses.filter(function (h) {
      if (responsavel && String(h.id_usuario_responsavel || "") !== responsavel) return false;
      if (natureza && h.tp_natureza !== natureza) return false;
      if (termo && String(h.tx_hipotese || "").toLowerCase().indexOf(termo) === -1) return false;
      return true;
    });
  }

  function renderCartao(h) {
    var $card = $("<div>").addClass("investigacao-card hipotese-card").attr("data-id", h.id_hipotese);
    $card.addClass("hipotese-" + h.tp_natureza);
    var $info = $("<div>").addClass("investigacao-info").appendTo($card);

    var $badges = $("<div>").css({ display: "flex", gap: "6px", "flex-wrap": "wrap", "margin-bottom": "4px" }).appendTo($info);
    var classeNatureza = h.tp_natureza === "fato" ? "badge-ok" : h.tp_natureza === "hipotese" ? "badge-aviso" : "badge-neutro";
    $("<span>").addClass("badge " + classeNatureza).text(ROTULO_NATUREZA[h.tp_natureza] || h.tp_natureza).appendTo($badges);
    if (!h.fl_contestavel_ia) {
      $("<span>").addClass("badge badge-neutro").attr("title", "A IA trata esta hipótese como verdade já estabelecida")
        .text("Não contestável pela IA").appendTo($badges);
    }
    if (h.tp_escopo) {
      var midia = (itensCache || []).filter(function (f) { return f.tp === h.tp_escopo; })[0];
      var item = midia && midia.itens.filter(function (i) { return String(i.id) === String(h.id_escopo); })[0];
      $("<span>").addClass("badge badge-rodando")
        .attr("title", "Hipótese da mídia — entra no chat de IA específico dela")
        .text("Mídia: " + (item ? item.label : (ROTULO_ESCOPO[h.tp_escopo] || h.tp_escopo)))
        .appendTo($badges);
    }

    var texto = String(h.tx_hipotese || "");
    var $textoCard = $("<span>").addClass("hipotese-texto").text(texto).appendTo($info);
    if (texto.length > 260 || texto.split("\n").length > 4) $textoCard.addClass("com-clamp");

    var meta = "Resp.: " + (h.responsavel || "—");
    meta += " · " + (h.qt_favoraveis || 0) + " a favor · " + (h.qt_contrarias || 0) + " contra";
    if (h.qt_pendencias) meta += " · " + h.qt_pendencias + " pendência(s)";
    meta += " · " + (h.criado_por || "—") + " em " + AnalyserUtils.formatarDataHora(h.dt_criacao);
    $("<span>").addClass("hipotese-meta").text(meta).appendTo($info);

    var $acoes = $("<div>").addClass("investigacao-actions").appendTo($card);
    var idx = STATUSOrdem.indexOf(h.cd_status);
    if (idx > 0) {
      $("<button>").addClass("btn btn-secondary").attr("title", "Mover para " + ROTULO_STATUS[STATUSOrdem[idx - 1]])
        .text("◀").on("click", function () { moverStatus(h, STATUSOrdem[idx - 1]); }).appendTo($acoes);
    }
    if (idx >= 0 && idx < STATUSOrdem.length - 1) {
      $("<button>").addClass("btn btn-secondary").attr("title", "Mover para " + ROTULO_STATUS[STATUSOrdem[idx + 1]])
        .text("▶").on("click", function () { moverStatus(h, STATUSOrdem[idx + 1]); }).appendTo($acoes);
    }
    $("<button>").addClass("btn btn-secondary").text("Abrir").on("click", function () { abrirPainel(h.id_hipotese); }).appendTo($acoes);

    $card.on("click", function (evt) {
      if ($(evt.target).closest("button").length) return;
      abrirPainel(h.id_hipotese);
    });
    return $card;
  }

  function renderQuadro() {
    $quadro.empty();
    if (!hipoteses.length) {
      $toolbar.addClass("hidden");
      mostrarEstado("Nenhuma hipótese registrada neste inquérito.",
        [{ rotulo: "➕ Nova hipótese", primario: true, aoClicar: function () { abrirPainel(null); } }]);
      return;
    }
    esconderEstado();
    $toolbar.removeClass("hidden");
    var visiveis = filtradas();
    STATUSOrdem.forEach(function (status) {
      var $col = $("<section>").addClass("hipoteses-painel").attr("data-status", status);
      var doStatus = visiveis.filter(function (h) { return h.cd_status === status; });
      var $cab = $("<header>").addClass("hipoteses-painel-cab").appendTo($col);
      $("<span>").text(ROTULO_STATUS[status]).appendTo($cab);
      $("<span>").addClass("hipoteses-painel-contagem").text(doStatus.length).appendTo($cab);
      var $corpo = $("<div>").addClass("hipoteses-painel-corpo").appendTo($col);
      if (!doStatus.length) {
        $("<p>").addClass("empty-hint").text("vazio").appendTo($corpo);
      }
      doStatus.forEach(function (h) { $corpo.append(renderCartao(h)); });
      $quadro.append($col);
    });
    $contagem.text(visiveis.length === hipoteses.length
      ? hipoteses.length + (hipoteses.length === 1 ? " hipótese" : " hipóteses")
      : visiveis.length + " de " + hipoteses.length + " hipóteses");
  }

  function carregar() {
    if (!idInquerito) return;
    $quadro.empty();
    $toolbar.addClass("hidden");
    $estadoTexto.text("Carregando hipóteses…");
    $estadoAcoes.empty().addClass("hidden");
    $spinner.removeClass("hidden");
    $estado.removeClass("hidden");
    $.getJSON(urlLista())
      .done(function (lista) {
        hipoteses = lista || [];
        renderQuadro();
        abrirDaHash();
      })
      .fail(function (xhr) {
        mostrarEstado(msgErro(xhr, "Erro ao carregar as hipóteses."),
          [{ rotulo: "↻ Tentar de novo", primario: true, aoClicar: carregar }]);
      });
  }

  function carregarUsuarios() {
    return $.getJSON(urlInq("hipoteses/usuarios")).done(function (lista) {
      usuarios = lista || [];
      var atualFiltro = $filtroResponsavel.val() || "";
      var atualPainel = $responsavelSelect.val() || "";
      $filtroResponsavel.find("option:not(:first)").remove();
      $responsavelSelect.find("option:not(:first)").remove();
      usuarios.forEach(function (u) {
        $filtroResponsavel.append($("<option>").val(String(u.id_usuario)).text(u.no_usuario));
        $responsavelSelect.append($("<option>").val(String(u.id_usuario)).text(u.no_usuario));
      });
      $filtroResponsavel.val(atualFiltro);
      $responsavelSelect.val(atualPainel);
    });
  }

  // ---------- vínculo de evidência com item importado ----------

  function carregarItens() {
    if (itensCache) return $.Deferred().resolve(itensCache).promise();
    return $.when.apply($, FONTES_ITENS.map(function (f) {
      return $.getJSON(f.url()).then(function (lista) { return { f: f, lista: lista || [] }; },
        function () { return { f: f, lista: [] }; });
    })).done(function () {
      var fontes = arguments.length === 1 ? [arguments[0]] : Array.prototype.slice.call(arguments);
      itensCache = fontes.map(function (r) {
        return {
          tp: r.f.tp, rotulo: r.f.rotulo,
          itens: r.lista.map(function (x) { return { id: x[r.f.id], label: r.f.label(x) }; }),
        };
      });
      preencherSelectsItem();
    });
  }

  function preencherSelectsItem() {
    $(".hipotese-item-select, #hipotese-midia-select").each(function () {
      var atual = $(this).val() || "";
      var $s = $(this);
      $s.find("optgroup").remove();
      (itensCache || []).forEach(function (fonte) {
        if (!fonte.itens.length) return;
        var $g = $("<optgroup>").attr("label", fonte.rotulo).appendTo($s);
        fonte.itens.forEach(function (it) {
          $("<option>").val(fonte.tp + ":" + it.id).text(it.label).appendTo($g);
        });
      });
      $s.val(atual);
    });
  }

  function rotuloItemVinculado(tpEscopo, idEscopo) {
    var fonte = itensCache && itensCache.filter(function (f) { return f.tp === tpEscopo; })[0];
    var item = fonte && fonte.itens.filter(function (i) { return String(i.id) === String(idEscopo); })[0];
    return (ROTULO_ESCOPO[tpEscopo] || tpEscopo) + " · " + (item ? item.label : "#" + idEscopo);
  }

  // ---------- painel da hipótese ----------

  function snapshotAtual() {
    return JSON.stringify([$texto.val(), $statusSelect.val(), $responsavelSelect.val(),
      $naturezaSelect.val(), $contestavel.is(":checked"), $("#hipotese-midia-select").val() || "", idEditando]);
  }

  function abrirPainel(idHipotese) {
    idEditando = idHipotese;
    var pronto = carregarUsuarios();
    if (itensCache) { pronto = pronto.then(function () { return itensCache; }); }
    else { pronto = pronto.then(function () { return carregarItens(); }); }
    if (idHipotese) {
      pronto.then(function () {
        $.getJSON("/api/investigacao/hipoteses/" + idHipotese).done(function (h) {
          preencherPainel(h);
        }).fail(function (xhr) {
          Toast.erro(msgErro(xhr, "Erro ao carregar a hipótese."));
        });
      });
    } else {
      pronto.then(function () { preencherPainel(null); });
    }
  }

  function preencherPainel(h) {
    $modalTitulo.text(h ? "Hipótese" : "Nova hipótese");
    $texto.val(h ? h.tx_hipotese : "");
    $statusSelect.val(h ? h.cd_status : "proposta");
    $naturezaSelect.val(h ? h.tp_natureza : "hipotese");
    $contestavel.prop("checked", h ? !!h.fl_contestavel_ia : false);
    $responsavelSelect.val(h && h.id_usuario_responsavel ? String(h.id_usuario_responsavel) : "");
    // hipótese nova na visão de mídia já nasce ligada a ela; fora dali, só liga se escolherem
    $("#hipotese-midia-select").val(h ? (h.tp_escopo ? h.tp_escopo + ":" + h.id_escopo : "") : midiaFiltro);
    $statusMsg.text("").removeClass("success error");
    $apagarBtn.toggle(!!h);
    atualizarContador();
    if (h) {
      $estrutura.removeClass("hidden");
      renderEvidencias(h);
      renderPendencias(h);
    } else {
      $estrutura.addClass("hidden");
    }
    snapshotModal = snapshotAtual();
    $modal.removeClass("hidden");
    $texto.trigger("focus");
  }

  function renderEvidencias(h) {
    ["favoravel", "contraria"].forEach(function (lado) {
      var $alvo = $(lado === "favoravel" ? "#evidencias-favoraveis" : "#evidencias-contrarias").empty();
      (h.evidencias || []).filter(function (e) { return e.tp_lado === lado; }).forEach(function (e) {
        var $item = $("<div>").addClass("hipoteses-item").appendTo($alvo);
        var $txt = $("<span>").addClass("hipoteses-item-texto").text(e.tx_evidencia).appendTo($item);
        var partes = [(e.criado_por || "—") + " em " + AnalyserUtils.formatarDataHora(e.dt_criacao)];
        if (e.tp_escopo) partes.push(rotuloItemVinculado(e.tp_escopo, e.id_escopo));
        $txt.after($("<span>").addClass("hipoteses-item-meta").text(partes.join(" · ")));
        $("<button>").attr("type", "button").addClass("hipoteses-item-remover")
          .attr("title", "Remover evidência").html("&times;")
          .on("click", function () {
            $.ajax({ url: "/api/investigacao/evidencias/" + e.id_evidencia, method: "DELETE" })
              .done(function () { recarregarPainel(); })
              .fail(function (xhr) { Toast.erro(msgErro(xhr, "Erro ao remover a evidência.")); });
          }).appendTo($item);
      });
    });
  }

  function renderPendencias(h) {
    var $alvo = $("#lista-pendencias").empty();
    (h.pendencias || []).forEach(function (p) {
      var $item = $("<div>").addClass("hipoteses-item").addClass(p.in_concluida ? "concluida" : "").appendTo($alvo);
      var $chk = $("<input>").attr("type", "checkbox").prop("checked", !!p.in_concluida)
        .on("change", function () {
          $.ajax({
            url: "/api/investigacao/pendencias/" + p.id_pendencia, method: "PUT",
            contentType: "application/json",
            data: JSON.stringify({ texto: p.tx_pendencia, in_concluida: this.checked })
          }).done(recarregarPainel).fail(function (xhr) { Toast.erro(msgErro(xhr, "Erro ao atualizar a pendência.")); });
        }).appendTo($item);
      $("<span>").addClass("hipoteses-item-texto").text(p.tx_pendencia).appendTo($item);
      var partes = [(p.criado_por || "—")];
      partes.push(p.in_concluida
        ? "concluída em " + AnalyserUtils.formatarDataHora(p.dt_conclusao || p.dt_criacao)
        : "em aberto desde " + AnalyserUtils.formatarDataHora(p.dt_criacao));
      $item.append($("<span>").addClass("hipoteses-item-meta").text(partes.join(" · ")));
      $("<button>").attr("type", "button").addClass("hipoteses-item-remover")
        .attr("title", "Remover pendência").html("&times;")
        .on("click", function () {
          $.ajax({ url: "/api/investigacao/pendencias/" + p.id_pendencia, method: "DELETE" })
            .done(recarregarPainel)
            .fail(function (xhr) { Toast.erro(msgErro(xhr, "Erro ao remover a pendência.")); });
        }).appendTo($item);
      if (p.in_concluida) $chk.closest(".hipoteses-item").addClass("concluida");
    });
  }

  function recarregarPainel() {
    if (!idEditando) return;
    $.getJSON("/api/investigacao/hipoteses/" + idEditando).done(function (h) {
      renderEvidencias(h);
      renderPendencias(h);
      var i = hipoteses.map(function (x) { return x.id_hipotese; }).indexOf(h.id_hipotese);
      if (i >= 0) hipoteses.splice(i, 1, $.extend({}, hipoteses[i], {
        qt_favoraveis: (h.evidencias || []).filter(function (e) { return e.tp_lado === "favoravel"; }).length,
        qt_contrarias: (h.evidencias || []).filter(function (e) { return e.tp_lado === "contraria"; }).length,
        qt_pendencias: (h.pendencias || []).filter(function (p) { return !p.in_concluida; }).length,
      }));
      renderQuadro();
    });
  }

  function fecharPainel() {
    $modal.addClass("hidden");
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }

  function tentarFecharPainel() {
    if ($modal.hasClass("hidden")) return;
    if (snapshotAtual() === snapshotModal || !$.trim($texto.val())) { fecharPainel(); return; }
    Dialogo.confirmar({
      titulo: "Descartar alterações?",
      mensagem: "A hipótese em edição ainda não foi salva. Descartar?",
      rotuloOk: "Descartar", perigoso: true
    }).then(function (ok) { if (ok) fecharPainel(); });
  }

  function atualizarContador() {
    var n = ($texto.val() || "").length;
    $contador.text(n + " / 4000").toggleClass("perto-do-limite", n > 3800);
  }

  function corpoHipotese() {
    var corpo = {
      texto: $.trim($texto.val()),
      cd_status: $statusSelect.val(),
      id_usuario_responsavel: $responsavelSelect.val() ? Number($responsavelSelect.val()) : null,
      tp_natureza: $naturezaSelect.val(),
      fl_contestavel_ia: $contestavel.is(":checked"),
      tp_escopo: null,
      id_escopo: null,
    };
    var midia = $("#hipotese-midia-select").val();
    if (midia) {
      var partes = String(midia).split(":");
      corpo.tp_escopo = partes[0];
      corpo.id_escopo = Number(partes[1]);
    }
    return corpo;
  }

  function moverStatus(h, novoStatus) {
    $.ajax({
      url: "/api/investigacao/hipoteses/" + h.id_hipotese, method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({
        texto: h.tx_hipotese, cd_status: novoStatus,
        id_usuario_responsavel: h.id_usuario_responsavel || null,
        tp_natureza: h.tp_natureza, fl_contestavel_ia: !!h.fl_contestavel_ia,
        tp_escopo: h.tp_escopo || null, id_escopo: h.id_escopo || null,
      })
    }).done(carregar).fail(function (xhr) {
      Toast.erro(msgErro(xhr, "Erro ao mover a hipótese no quadro."));
    });
  }

  // ---------- eventos ----------

  $("#nova-hipotese-btn").on("click", function () { abrirPainel(null); });
  $("#hipotese-fechar").on("click", tentarFecharPainel);
  $modal.on("click", function (e) { if (e.target === this) tentarFecharPainel(); });
  $(document).on("keydown", function (evt) {
    if (evt.key === "Escape" && !$modal.hasClass("hidden")) tentarFecharPainel();
  });
  $texto.on("input", atualizarContador);
  $texto.on("keydown", function (evt) {
    if ((evt.ctrlKey || evt.metaKey) && evt.key === "Enter") {
      evt.preventDefault();
      $form.trigger("submit");
    }
  });

  $busca.on("input", renderQuadro);
  $filtroResponsavel.on("change", renderQuadro);
  $filtroNatureza.on("change", renderQuadro);

  $form.on("submit", function (e) {
    e.preventDefault();
    $statusMsg.text("").removeClass("success error");
    $salvarBtn.prop("disabled", true);
    var corpo = corpoHipotese();
    if (!corpo.texto) {
      $statusMsg.text("Escreva a hipótese antes de salvar.").addClass("error");
      $salvarBtn.prop("disabled", false);
      return;
    }
    var pedido;
    if (idEditando) {
      pedido = $.ajax({
        url: "/api/investigacao/hipoteses/" + idEditando, method: "PUT",
        contentType: "application/json", data: JSON.stringify(corpo),
      });
    } else {
      pedido = $.ajax({
        url: urlInq("hipoteses"), method: "POST",
        contentType: "application/json", data: JSON.stringify(corpo),
      });
    }
    pedido.done(function (res) {
      Toast.sucesso(idEditando ? "Hipótese atualizada." : "Hipótese registrada — o chat de IA já a considera no inquérito.");
      if (idEditando) { fecharPainel(); }
      else {
        idEditando = res.id_hipotese;
        $estrutura.removeClass("hidden");
        $apagarBtn.removeClass("hidden");
        $modalTitulo.text("Hipótese");
        renderEvidencias({ evidencias: [], pendencias: [] });
        renderPendencias({ pendencias: [] });
      }
      snapshotModal = snapshotAtual();
      carregar();
    }).fail(function (xhr) {
      $statusMsg.text(msgErro(xhr, "Erro ao salvar a hipótese.")).addClass("error");
    }).always(function () {
      $salvarBtn.prop("disabled", false);
    });
  });

  $apagarBtn.on("click", function () {
    if (!idEditando) return;
    Dialogo.confirmar({
      titulo: "Apagar hipótese",
      mensagem: "Apagar esta hipótese com todas as evidências e pendências? A IA deixará de considerá-la. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/hipoteses/" + idEditando, method: "DELETE" })
        .done(function () {
          Toast.sucesso("Hipótese apagada.");
          fecharPainel();
          carregar();
        })
        .fail(function (xhr) { Toast.erro(msgErro(xhr, "Erro ao apagar a hipótese.")); });
    });
  });

  ["favoravel", "contraria"].forEach(function (lado) {
    $("#form-evidencia-" + lado).on("submit", function (e) {
      e.preventDefault();
      if (!idEditando) { Toast.erro("Salve a hipótese antes de registrar evidências."); return; }
      var $ta = $(this).find("textarea");
      var $sel = $(this).find(".hipotese-item-select");
      var texto = $.trim($ta.val());
      if (!texto) return;
      var corpo = { tp_lado: lado, texto: texto, tp_escopo: null, id_escopo: null };
      if ($sel.val()) {
        var partes = String($sel.val()).split(":");
        corpo.tp_escopo = partes[0];
        corpo.id_escopo = Number(partes[1]);
      }
      $.ajax({
        url: "/api/investigacao/hipoteses/" + idEditando + "/evidencias", method: "POST",
        contentType: "application/json", data: JSON.stringify(corpo),
      }).done(function () {
        $ta.val(""); $sel.val("");
        recarregarPainel();
      }).fail(function (xhr) {
        Toast.erro(msgErro(xhr, "Erro ao registrar a evidência."));
      });
    });
  });

  $("#form-pendencia").on("submit", function (e) {
    e.preventDefault();
    if (!idEditando) { Toast.erro("Salve a hipótese antes de registrar pendências."); return; }
    var $ta = $(this).find("textarea");
    var texto = $.trim($ta.val());
    if (!texto) return;
    $.ajax({
      url: "/api/investigacao/hipoteses/" + idEditando + "/pendencias", method: "POST",
      contentType: "application/json", data: JSON.stringify({ texto: texto }),
    }).done(function () { $ta.val(""); recarregarPainel(); })
      .fail(function (xhr) { Toast.erro(msgErro(xhr, "Erro ao registrar a pendência.")); });
  });

  // Deep-link de fora (busca global, mesa): hipoteses.html?inquerito=N#hipotese=ID abre direto.
  function abrirDaHash() {
    var m = /hipotese=(\d+)/.exec(location.hash || "");
    if (!m) return;
    var id = Number(m[1]);
    if (hipoteses.some(function (h) { return h.id_hipotese === id; })) abrirPainel(id);
  }
  $(window).on("hashchange", abrirDaHash);

  if (idInquerito) {
    carregarUsuarios();
    carregar();
    if (midiaFiltro) {
      $titulo.text("Hipótese da mídia — " + (escopoTitulo || ROTULO_ESCOPO[escopoTipo] || escopoTipo));
      document.title = "Hipótese da mídia — " + (escopoTitulo || escopoTipo) + " · PolícIA";
    }
    $.getJSON("/api/investigacao/inqueritos").done(function (lista) {
      var inq = (lista || []).filter(function (i) { return String(i.id_inquerito) === String(idInquerito); })[0];
      if (inq && !midiaFiltro) {
        $titulo.text("Inquérito " + (inq.nu_inquerito || inq.id_inquerito));
        document.title = "Hipóteses — " + (inq.nu_inquerito || "inquérito") + " · PolícIA";
      } else if (inq && midiaFiltro) {
        document.title = "Hipótese da mídia — " + (escopoTitulo || escopoTipo) + " · PolícIA";
      }
    });
  }
})();
