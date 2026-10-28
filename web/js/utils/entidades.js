/* Widget compartilhado de ações do investigador em cima de qualquer registro extraído
 * (mensagem, contato, ligação, localização, notificação, navegação, app, dono). Injeta os
 * modais uma vez e expõe window.EntidadesUI. Usado por whatsapp.js e por
 * contatos.js/ligacoes.js/localizacoes.js/notificacoes.js/navegacao.js/aplicativos.js.
 *
 * Cada registro ganha um checkbox de seleção em lote e dois botões, lado a lado:
 *   ☐   seleção — junta o registro no lote da barra flutuante, para marcar vários de uma vez.
 *   📌  "Criar entidade / comentário"  — anotação livre (base do grafo da Mesa de Inquérito).
 *   🗒️  "Marcar para o relatório"       — inclui o registro (com comentário opcional) nos
 *                                          relatórios montados na página "Padrão de relatórios".
 */
(function () {
  "use strict";

  var $overlay, $rotulo, $lista, $comentario, $salvarBtn, $status;
  var alvo = null; // { idInquerito, tpOrigem, idOrigem, rotulo }

  // --- marcações "marcar para o relatório" ---
  var $relOverlay, $relRotulo, $relLista, $relComentario, $relSalvarBtn, $relStatus;
  var relEstado = null;   // seletor de marcadores do modal individual
  var relAlvo = null;

  var CORES = [
    "#E74C3C", "#E67E22", "#F1C40F", "#2ECC71", "#1ABC9C",
    "#3498DB", "#9B59B6", "#E91E63", "#795548", "#607D8B",
    "#00BCD4", "#FF9800",
  ];
  // cache interno de { count, cor, marcadores } por "tpOrigem:idOrigem", preenchido em
  // carregarContagens — deixa o botão de relatório se destacar sem mexer nos call sites.
  var marcacoesCache = {};
  var idMarcacaoPorChave = {};

  // --- marcadores nomeados (as "tags" do IPED, ver api/funcoes/marcadores_utils.py) ---
  // São PESSOAIS do usuário e presos ao inquérito. Carregados uma vez por página; o
  // idInquerito vem do primeiro botao() montado, que é sempre anterior a qualquer modal.
  var marcadoresUsuario = [];
  var marcadoresCarregados = false;
  var idInqueritoAtual = null;

  // --- marcação em lote (barra flutuante + modal) ---
  var $loteBarra, $loteContagem, $loteMarcarBtn, $loteLimparBtn;
  var $loteOverlay, $loteModalContagem, $loteComentario, $loteSalvarBtn, $loteRemoverBtn, $loteStatus;
  var $loteJaMarcados;
  var loteEstado = null;   // seletor de marcadores do modal em lote
  // registros marcados para o lote, por "tpOrigem:idOrigem" -> cfg (o mesmo objeto passado a botao())
  var selecionados = {};

  // ---------- marcadores nomeados ----------

  function carregarMarcadores(callback) {
    if (marcadoresCarregados || !idInqueritoAtual) { if (callback) callback(); return; }
    $.getJSON("/api/investigacao/inqueritos/" + idInqueritoAtual + "/marcadores")
      .done(function (lista) {
        marcadoresUsuario = lista || [];
        marcadoresCarregados = true;
      })
      .always(function () { if (callback) callback(); });
  }

  function marcadorPorId(id) {
    for (var i = 0; i < marcadoresUsuario.length; i++) {
      if (String(marcadoresUsuario[i].id_marcador) === String(id)) return marcadoresUsuario[i];
    }
    return null;
  }

  function htmlSeletorMarcadores(prefixo) {
    return '<div class="field">' +
        '<label>Marcadores</label>' +
        '<div class="mrc-lista" id="' + prefixo + '-mrc-lista"></div>' +
        '<div class="mrc-novo">' +
          '<input type="text" id="' + prefixo + '-mrc-nome" maxlength="120" placeholder="Novo marcador (ex.: TRÁFICO DE DROGAS)">' +
          '<div class="rel-cores mrc-novo-cores" id="' + prefixo + '-mrc-cores"></div>' +
          '<button type="button" class="btn btn-secondary" id="' + prefixo + '-mrc-criar">＋ Criar</button>' +
        '</div>' +
        '<p class="field-help hidden" id="' + prefixo + '-mrc-erro"></p>' +
      '</div>';
  }

  // Estado de um seletor: quais marcadores estão marcados e qual cor o "novo" vai receber.
  // Devolvido para o modal guardar; a lista de chips é redesenhada por redesenharChips().
  function ligarSeletorMarcadores(prefixo) {
    var estado = { selecionados: {}, corNovo: CORES[0], prefixo: prefixo };
    var $cores = $("#" + prefixo + "-mrc-cores");

    CORES.forEach(function (c, i) {
      $("<span>")
        .addClass("rel-cor-bola" + (i === 0 ? " selecionada" : ""))
        .css("background", c)
        .data("cor", c)
        .on("click", function () {
          $cores.find(".rel-cor-bola").removeClass("selecionada");
          $(this).addClass("selecionada");
          estado.corNovo = c;
        })
        .appendTo($cores);
    });

    $("#" + prefixo + "-mrc-criar").on("click", function () { criarMarcadorInline(estado); });
    $("#" + prefixo + "-mrc-nome").on("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); criarMarcadorInline(estado); }
    });
    return estado;
  }

  function redesenharChips(estado) {
    var $lista = $("#" + estado.prefixo + "-mrc-lista").empty();
    if (!marcadoresUsuario.length) {
      $("<p>").addClass("empty-hint").text("Nenhum marcador ainda. Crie o primeiro abaixo — ele é só seu, dentro deste inquérito.").appendTo($lista);
      return;
    }
    marcadoresUsuario.forEach(function (m) {
      var selecionado = !!estado.selecionados[m.id_marcador];
      var $chip = $("<button>")
        .attr("type", "button")
        .addClass("mrc-chip" + (selecionado ? " selecionado" : ""))
        .attr("title", m.tx_descricao || m.no_marcador)
        .on("click", function () {
          if (estado.selecionados[m.id_marcador]) delete estado.selecionados[m.id_marcador];
          else estado.selecionados[m.id_marcador] = true;
          $chip.toggleClass("selecionado");
        });
      $("<span>").addClass("mrc-bola").css("background", m.co_cor).appendTo($chip);
      $chip.append(document.createTextNode(m.no_marcador));
      if (m.qt_itens) $("<span>").addClass("mrc-chip-qt").text(m.qt_itens).appendTo($chip);
      $chip.appendTo($lista);
    });
  }

  function criarMarcadorInline(estado) {
    var $nome = $("#" + estado.prefixo + "-mrc-nome");
    var $erro = $("#" + estado.prefixo + "-mrc-erro");
    var nome = $.trim($nome.val());
    if (!nome) { $erro.removeClass("hidden").text("Dê um nome ao marcador."); return; }
    $erro.addClass("hidden").text("");
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInqueritoAtual + "/marcadores",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ nome: nome, cor: estado.corNovo })
    })
      .done(function (resp) {
        var novo = resp.marcador;
        // criar de novo o que já existe devolve o existente (ON CONFLICT), então só entra na
        // lista quem ainda não estava lá — senão o mesmo marcador apareceria duas vezes
        if (!marcadorPorId(novo.id_marcador)) {
          novo.qt_itens = novo.qt_itens || 0;
          marcadoresUsuario.push(novo);
        }
        estado.selecionados[novo.id_marcador] = true;
        $nome.val("");
        redesenharChips(estado);
      })
      .fail(function (xhr) {
        $erro.removeClass("hidden").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar o marcador.");
      });
  }

  // cor do botão 🗒️ e do indicador: a do primeiro marcador meu, caindo para a CO_COR antiga
  // (casos anteriores aos marcadores nomeados)
  function corDoItem(item) {
    if (item && item.marcadores && item.marcadores.length) return item.marcadores[0].co_cor;
    return item ? item.cor : null;
  }

  function avisarMudanca(tpOrigem, idOrigem) {
    $(document).trigger("analyser:marcacao-mudou", [{ tpOrigem: tpOrigem, idOrigem: idOrigem }]);
  }

  function montarModal() {
    var $html = $(
      '<div class="modal-overlay hidden" id="modal-entidade">' +
        '<div class="modal ent-modal">' +
          '<div class="modal-header"><h2>Nova entidade — <span id="ent-rotulo"></span></h2>' +
          '<button class="modal-close" id="ent-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<div class="ent-lista" id="ent-lista"></div>' +
            '<div class="field">' +
              '<label>Comentário</label>' +
              '<textarea id="ent-comentario" rows="4" placeholder="Descreva por que isso é relevante..."></textarea>' +
            '</div>' +
            '<div class="form-actions">' +
              '<button class="btn btn-primary" id="ent-salvar-btn">Salvar</button>' +
              '<span class="form-status" id="ent-status"></span>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($html);

    $overlay = $("#modal-entidade");
    $rotulo = $("#ent-rotulo");
    $lista = $("#ent-lista");
    $comentario = $("#ent-comentario");
    $salvarBtn = $("#ent-salvar-btn");
    $status = $("#ent-status");

    $("#ent-fechar").on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === this) fechar(); });
    $salvarBtn.on("click", salvar);
  }

  function montarModalRelatorio() {
    var $html = $(
      '<div class="modal-overlay hidden" id="modal-marcacao">' +
        '<div class="modal ent-modal">' +
          '<div class="modal-header"><h2>Marcar para o relatório — <span id="rel-rotulo"></span></h2>' +
          '<button class="modal-close" id="rel-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<div class="ent-lista" id="rel-lista"></div>' +
            htmlSeletorMarcadores("rel") +
            '<div class="field">' +
              '<label>Comentário (opcional)</label>' +
              '<textarea id="rel-comentario" class="rel-comentario-textarea" rows="4" placeholder="Observação que vai junto do trecho no relatório (opcional)..."></textarea>' +
            '</div>' +
            '<div class="form-actions">' +
              '<button class="btn btn-primary" id="rel-salvar-btn">Marcar para o relatório</button>' +
              '<span class="form-status" id="rel-status"></span>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($html);

    $relOverlay = $("#modal-marcacao");
    $relRotulo = $("#rel-rotulo");
    $relLista = $("#rel-lista");
    $relComentario = $("#rel-comentario");
    $relSalvarBtn = $("#rel-salvar-btn");
    $relStatus = $("#rel-status");
    relEstado = ligarSeletorMarcadores("rel");

    $("#rel-fechar").on("click", fecharRelatorio);
    $relOverlay.on("click", function (e) { if (e.target === this) fecharRelatorio(); });
    $(document).on("keydown", function (e) { if (e.key === "Escape") fecharRelatorio(); });
    $relSalvarBtn.on("click", salvarMarcacao);
  }

  function montarBarraLote() {
    var $html = $(
      '<div class="lote-barra hidden" id="lote-barra">' +
        '<span id="lote-contagem"></span>' +
        '<span class="lote-barra-acoes">' +
          '<button type="button" class="btn btn-secondary" id="lote-limpar-btn">Limpar seleção</button>' +
          '<button type="button" class="btn btn-primary" id="lote-marcar-btn">🗒️ Marcar para o relatório</button>' +
        '</span>' +
      '</div>'
    );
    $("body").append($html);

    $loteBarra = $("#lote-barra");
    $loteContagem = $("#lote-contagem");
    $loteMarcarBtn = $("#lote-marcar-btn");
    $loteLimparBtn = $("#lote-limpar-btn");

    $loteLimparBtn.on("click", limparSelecaoLote);
    $loteMarcarBtn.on("click", abrirModalLote);
  }

  function montarModalLote() {
    var $html = $(
      '<div class="modal-overlay hidden" id="modal-marcacao-lote">' +
        '<div class="modal ent-modal">' +
          '<div class="modal-header"><h2>Marcar para o relatório — <span id="lote-modal-contagem"></span></h2>' +
          '<button class="modal-close" id="lote-modal-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<p class="empty-hint rel-ja-marcado hidden" id="lote-ja-marcados"></p>' +
            htmlSeletorMarcadores("lote") +
            '<div class="field">' +
              '<label>Comentário (opcional, aplicado a todos os registros selecionados)</label>' +
              '<textarea id="lote-rel-comentario" class="rel-comentario-textarea" rows="4" placeholder="Observação que vai junto de cada item marcado (opcional)..."></textarea>' +
            '</div>' +
            '<div class="form-actions">' +
              '<button class="btn btn-primary" id="lote-rel-salvar-btn">Marcar para o relatório</button>' +
              '<button class="btn btn-secondary" id="lote-rel-remover-btn" title="Tira os marcadores escolhidos dos registros selecionados; a marcação em si continua">Remover marcadores</button>' +
              '<span class="form-status" id="lote-rel-status"></span>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($html);

    $loteOverlay = $("#modal-marcacao-lote");
    $loteModalContagem = $("#lote-modal-contagem");
    $loteJaMarcados = $("#lote-ja-marcados");
    $loteComentario = $("#lote-rel-comentario");
    $loteSalvarBtn = $("#lote-rel-salvar-btn");
    $loteRemoverBtn = $("#lote-rel-remover-btn");
    $loteStatus = $("#lote-rel-status");
    loteEstado = ligarSeletorMarcadores("lote");

    $("#lote-modal-fechar").on("click", fecharModalLote);
    $loteOverlay.on("click", function (e) { if (e.target === this) fecharModalLote(); });
    $(document).on("keydown", function (e) { if (e.key === "Escape" && !$loteOverlay.hasClass("hidden")) fecharModalLote(); });
    $loteSalvarBtn.on("click", function () { salvarMarcacaoLote("adicionar"); });
    $loteRemoverBtn.on("click", function () { salvarMarcacaoLote("remover"); });
  }

  function formatarData(iso) {
    if (!iso) return "";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z")).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function renderComentario(e) {
    var $item = $("<div>").addClass("ent-item");
    $("<div>").addClass("ent-item-texto").text(e.tx_comentario).appendTo($item);
    var $rodape = $("<div>").addClass("ent-item-rodape").appendTo($item);
    var meta = formatarData(e.dt_criacao) + (e.criado_por ? " · " + e.criado_por : "");
    $("<span>").text(meta).appendTo($rodape);
    $("<button>").text("Remover").on("click", function () { removerEntidade(e.id_entidade); }).appendTo($rodape);
    return $item;
  }

  // O 🗒️ também é .ent-botao e carrega o MESMO data-chave do 📌, então sem o :not(.rel-botao)
  // o indicador de comentário acenderia os dois botões do registro.
  function $botaoComentario(tpOrigem, idOrigem) {
    var chave = chaveMarcacao(tpOrigem, idOrigem);
    return $('.ent-botao[data-chave="' + chave.replace(/"/g, '\\"') + '"]').not(".rel-botao");
  }

  // Recarrega a lista do modal e, de quebra, acerta o indicador do 📌 na tela de origem no próprio
  // DOM. É o que dispensa o cfg.aoMudar depois de salvar/remover um comentário: recarregar a tela
  // inteira remontava a conversa do WhatsApp e jogava o investigador de volta para o último dia.
  function carregarLista() {
    var cfg = alvo;   // fechar o modal antes da resposta chegar não pode quebrar o callback
    $lista.empty().text("Carregando...");
    $.getJSON(
      "/api/investigacao/inqueritos/" + cfg.idInquerito + "/entidades?tp_origem=" + encodeURIComponent(cfg.tpOrigem)
    ).done(function (lista) {
      $lista.empty();
      var doItem = lista.filter(function (e) { return e.id_origem === cfg.idOrigem; });
      $botaoComentario(cfg.tpOrigem, cfg.idOrigem).toggleClass("tem-comentario", doItem.length > 0);
      if (!doItem.length) {
        $lista.append($("<p>").addClass("empty-hint").text("Nenhum comentário ainda neste item."));
        return;
      }
      doItem.forEach(function (e) { $lista.append(renderComentario(e)); });
    });
  }

  function removerEntidade(idEntidade) {
    $.ajax({ url: "/api/investigacao/entidades/" + idEntidade, method: "DELETE" }).done(function () {
      carregarLista();
    });
  }

  function salvar() {
    var comentario = $.trim($comentario.val());
    if (!comentario) {
      $status.text("Escreva um comentário.").removeClass("success").addClass("error");
      return;
    }
    $status.text("Salvando...").removeClass("success error");
    $salvarBtn.prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/entidades",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: alvo.idInquerito,
        tp_origem: alvo.tpOrigem,
        id_origem: alvo.idOrigem,
        comentario: comentario
      })
    })
      .done(function () {
        $comentario.val("");
        $status.text("Comentário salvo.").addClass("success");
        carregarLista();   // acende o 📌 do registro sem recarregar a tela de origem
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $salvarBtn.prop("disabled", false);
      });
  }

  function abrir(cfg) {
    alvo = cfg; // { idInquerito, tpOrigem, idOrigem, rotulo }
    $rotulo.text(cfg.rotulo || "");
    $comentario.val("");
    $status.text("").removeClass("success error");
    carregarLista();
    $overlay.removeClass("hidden");
  }

  function fechar() {
    $overlay.addClass("hidden");
    alvo = null;
  }

  // ---------- marcações "marcar para o relatório" ----------

  function chaveMarcacao(tpOrigem, idOrigem) {
    return tpOrigem + ":" + idOrigem;
  }

  function renderMarcacao(m) {
    var $item = $("<div>").addClass("ent-item");
    var $header = $("<div>").addClass("ent-item-header").appendTo($item);
    var marcadores = m.marcadores || [];
    if (marcadores.length) {
      marcadores.forEach(function (mk) {
        $("<span>").addClass("rel-cor-indicator").attr("title", mk.no_marcador)
          .css("background", mk.co_cor).appendTo($header);
      });
    } else if (m.co_cor) {
      $("<span>").addClass("rel-cor-indicator").css("background", m.co_cor).appendTo($header);
    }
    $("<div>").addClass("ent-item-texto").text(m.tx_comentario || "(sem comentário)").appendTo($header);
    var $rodape = $("<div>").addClass("ent-item-rodape").appendTo($item);
    var meta = formatarData(m.dt_criacao) + (m.criado_por ? " · " + m.criado_por : "");
    $("<span>").text(meta).appendTo($rodape);
    $("<button>").text("Remover").on("click", function () { removerMarcacao(m.id_marcacao); }).appendTo($rodape);
    return $item;
  }

  function carregarListaMarcacao() {
    $relLista.empty().text("Carregando...");
    $.getJSON(
      "/api/investigacao/inqueritos/" + relAlvo.idInquerito + "/marcacoes?tp_origem=" + encodeURIComponent(relAlvo.tpOrigem)
    ).done(function (lista) {
      $relLista.empty();
      var doItem = lista.filter(function (m) { return m.id_origem === relAlvo.idOrigem; });
      if (!doItem.length) {
        $relSalvarBtn.text("Marcar para o relatório");
        $relLista.append($("<p>").addClass("empty-hint").text("Este registro ainda não está marcado para o relatório."));
        return;
      }
      // já marcado: o servidor não vai criar uma segunda marcação (o trecho sairia duplicado no
      // .docx), vai atualizar esta — o aviso e o rótulo do botão dizem isso antes do clique
      $relSalvarBtn.text("Atualizar marcação");
      $relLista.append($("<p>").addClass("empty-hint rel-ja-marcado")
        .text("Este registro JÁ ESTÁ marcado para o relatório. Salvar atualiza a marcação abaixo (os marcadores passam a ser os escolhidos aqui e o comentário novo é acrescentado) — não cria uma segunda."));
      doItem.forEach(function (m) { $relLista.append(renderMarcacao(m)); });
      // pré-seleciona os marcadores que o registro JÁ tem: salvar substitui o conjunto, então
      // sem isso um "salvar" só para acrescentar comentário apagaria as etiquetas existentes
      doItem.forEach(function (m) {
        (m.marcadores || []).forEach(function (mk) { relEstado.selecionados[mk.id_marcador] = true; });
      });
      redesenharChips(relEstado);
    });
  }

  // marcar/desmarcar atualiza só o botão 🗒️ do registro (classe + contorno colorido), pelo
  // data-chave, via atualizarContagemMarcacao. Nada de recarregar a tela de origem: era o que
  // remontava a conversa do WhatsApp a partir das mensagens mais recentes e jogava o investigador
  // para o último dia. Mesmo raciocínio do 📌 em carregarLista().
  function removerMarcacao(idMarcacao) {
    $.ajax({ url: "/api/investigacao/marcacoes/" + idMarcacao, method: "DELETE" }).done(function () {
      atualizarContagemMarcacao(relAlvo.tpOrigem, relAlvo.idOrigem, -1);
      relEstado.selecionados = {};
      redesenharChips(relEstado);
      avisarMudanca(relAlvo.tpOrigem, relAlvo.idOrigem);
      carregarListaMarcacao();
    });
  }

  function idsSelecionados(estado) {
    return Object.keys(estado.selecionados).map(Number);
  }

  function salvarMarcacao() {
    var comentario = $.trim($relComentario.val());
    var ids = idsSelecionados(relEstado);
    // CO_COR continua sendo gravada (é o fallback dos casos anteriores aos marcadores e o que
    // o relatório usa quando o registro não tem etiqueta): fica a cor do 1º marcador escolhido
    var primeiro = ids.length ? marcadorPorId(ids[0]) : null;
    var cor = primeiro ? primeiro.co_cor : null;

    $relStatus.text("Salvando...").removeClass("success error");
    $relSalvarBtn.prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/marcacoes",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: relAlvo.idInquerito,
        tp_origem: relAlvo.tpOrigem,
        id_origem: relAlvo.idOrigem,
        comentario: comentario || null,
        cor: cor
      })
    })
      .done(function (resp) {
        var alvo = relAlvo;   // fechar o modal antes da 2ª resposta não pode quebrar o callback
        // ja_existia: a marcação foi atualizada, não criada — a contagem não pode subir, senão
        // o botão passa a "achar" que há duas marcações no mesmo registro
        var delta = (resp && resp.ja_existia) ? 0 : 1;
        var idMarcacao = resp && resp.id_marcacao;
        var marcadores = ids.map(marcadorPorId).filter(Boolean);
        var concluir = function () {
          atualizarContagemMarcacao(alvo.tpOrigem, alvo.idOrigem, delta, cor, marcadores);
          avisarMudanca(alvo.tpOrigem, alvo.idOrigem);
          fecharRelatorio();
          $relSalvarBtn.prop("disabled", false);
        };
        if (!idMarcacao) { concluir(); return; }
        // os marcadores são pendurados na marcação num 2º passo: a rota substitui o conjunto
        // DESTE usuário, sem tocar nas etiquetas que um colega tenha no mesmo registro
        $.ajax({
          url: "/api/investigacao/marcacoes/" + idMarcacao + "/marcadores",
          method: "POST",
          contentType: "application/json",
          data: JSON.stringify({ ids_marcadores: ids })
        })
          .done(concluir)
          .fail(function (xhr) {
            $relStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao aplicar os marcadores.").addClass("error");
            $relSalvarBtn.prop("disabled", false);
          });
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao marcar.";
        $relStatus.text(msg).addClass("error");
        $relSalvarBtn.prop("disabled", false);
      });
  }

  function abrirRelatorio(cfg) {
    relAlvo = cfg;
    idInqueritoAtual = idInqueritoAtual || cfg.idInquerito;
    $relRotulo.text(cfg.rotulo || "");
    $relComentario.val("");
    $relStatus.text("").removeClass("success error");
    relEstado.selecionados = {};
    carregarMarcadores(function () { redesenharChips(relEstado); });
    carregarListaMarcacao();
    $relOverlay.removeClass("hidden");
  }

  function fecharRelatorio() {
    $relOverlay.addClass("hidden");
    relAlvo = null;
  }

  // ---------- marcação em lote ----------

  function contarSelecionados() {
    return Object.keys(selecionados).length;
  }

  function atualizarBarraLote() {
    var n = contarSelecionados();
    if (n === 0) {
      $loteBarra.addClass("hidden");
      return;
    }
    $loteContagem.text(n === 1 ? "1 registro selecionado" : n + " registros selecionados");
    $loteBarra.removeClass("hidden");
  }

  function limparSelecaoLote() {
    selecionados = {};
    $(".lote-checkbox").prop("checked", false);
    atualizarBarraLote();
  }

  // quantos dos selecionados já estão marcados (pelo cache que destaca os botões) — marcar de
  // novo não duplica mais, mas o usuário precisa saber ANTES de salvar que vai só atualizar
  function contarJaMarcados() {
    return Object.keys(selecionados).filter(function (chave) {
      var item = marcacoesCache[chave];
      return item && item.count > 0;
    }).length;
  }

  function abrirModalLote() {
    var n = contarSelecionados();
    if (n === 0) return;
    $loteModalContagem.text(n === 1 ? "1 registro" : n + " registros");
    var jaMarcados = contarJaMarcados();
    $loteJaMarcados.toggleClass("hidden", jaMarcados === 0).text(
      jaMarcados === 1
        ? "1 dos registros selecionados JÁ ESTÁ marcado para o relatório: a marcação dele será atualizada, não duplicada."
        : jaMarcados + " dos registros selecionados JÁ ESTÃO marcados para o relatório: as marcações deles serão atualizadas, não duplicadas."
    );
    $loteComentario.val("");
    $loteStatus.text("").removeClass("success error");
    loteEstado.selecionados = {};
    carregarMarcadores(function () { redesenharChips(loteEstado); });
    $loteOverlay.removeClass("hidden");
  }

  function fecharModalLote() {
    $loteOverlay.addClass("hidden");
  }

  // Uma requisição para o lote inteiro (antes era uma por registro: marcar um dia de conversa
  // subia dezenas de POSTs). acao = "adicionar" garante a marcação e pendura os marcadores;
  // "remover" só tira as etiquetas — desmarcar do relatório é outra ação, com outro botão.
  function salvarMarcacaoLote(acao) {
    var chaves = Object.keys(selecionados);
    if (!chaves.length) return;
    var comentario = $.trim($loteComentario.val()) || null;
    var ids = idsSelecionados(loteEstado);
    if (acao === "remover" && !ids.length) {
      $loteStatus.text("Escolha quais marcadores remover.").addClass("error");
      return;
    }
    var primeiro = ids.length ? marcadorPorId(ids[0]) : null;
    var cor = primeiro ? primeiro.co_cor : null;
    var marcadores = ids.map(marcadorPorId).filter(Boolean);
    var idInquerito = selecionados[chaves[0]].idInquerito;

    $loteStatus.text(acao === "remover" ? "Removendo..." : "Marcando...").removeClass("success error");
    $loteSalvarBtn.prop("disabled", true);
    $loteRemoverBtn.prop("disabled", true);

    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/marcadores/lote",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        itens: chaves.map(function (chave) {
          var cfg = selecionados[chave];
          return { tp_origem: cfg.tpOrigem, id_origem: cfg.idOrigem };
        }),
        ids_marcadores: ids,
        acao: acao,
        comentario: comentario,
        cor: cor
      })
    })
      .done(function (resp) {
        // como no modal individual, o botão de cada registro é atualizado no lugar e a tela de
        // origem NÃO é recarregada — marcar um dia inteiro no chat mantém a rolagem onde está
        chaves.forEach(function (chave) {
          var cfg = selecionados[chave];
          if (acao === "remover") {
            atualizarContagemMarcacao(cfg.tpOrigem, cfg.idOrigem, 0, null, null, ids);
          } else {
            var jaMarcado = marcacoesCache[chave] && marcacoesCache[chave].count > 0;
            atualizarContagemMarcacao(cfg.tpOrigem, cfg.idOrigem, jaMarcado ? 0 : 1, cor, marcadores);
          }
          avisarMudanca(cfg.tpOrigem, cfg.idOrigem);
        });
        var falhas = (resp && resp.falhas) || [];
        if (falhas.length) {
          $loteStatus.text(falhas.length + " de " + chaves.length + " falharam.").addClass("error");
          return;
        }
        limparSelecaoLote();
        fecharModalLote();
      })
      .fail(function (xhr) {
        $loteStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao aplicar os marcadores.").addClass("error");
      })
      .always(function () {
        $loteSalvarBtn.prop("disabled", false);
        $loteRemoverBtn.prop("disabled", false);
      });
  }

  // mantém o cache e o destaque visual do botão de relatório em sincronia após marcar/remover.
  // marcadores = conjunto novo de etiquetas do registro; idsRemovidos = etiquetas tiradas dele.
  function atualizarContagemMarcacao(tpOrigem, idOrigem, delta, cor, marcadores, idsRemovidos) {
    var chave = chaveMarcacao(tpOrigem, idOrigem);
    var item = marcacoesCache[chave] || { count: 0, cor: null, marcadores: [] };
    item.count = Math.max(0, item.count + delta);
    if (cor) item.cor = cor;
    if (marcadores) item.marcadores = marcadores;
    if (idsRemovidos && idsRemovidos.length) {
      item.marcadores = (item.marcadores || []).filter(function (mk) {
        return idsRemovidos.indexOf(mk.id_marcador) === -1;
      });
    }
    if (item.count === 0) { item.cor = null; item.marcadores = []; }
    marcacoesCache[chave] = item;
    pintarBotaoMarcacao(chave, item);
  }

  function pintarBotaoMarcacao(chave, item) {
    var $btn = $('.rel-botao[data-chave="' + chave.replace(/"/g, '\\"') + '"]');
    $btn.toggleClass("tem-marcacao", item.count > 0);
    if (item.count > 0) {
      $btn.css("box-shadow", "0 0 0 2px " + (corDoItem(item) || "#000"));
      var nomes = (item.marcadores || []).map(function (mk) { return mk.no_marcador; });
      $btn.attr("title", nomes.length ? "Marcadores: " + nomes.join(", ") : "Marcar para o relatório");
    } else {
      $btn.css("box-shadow", "").attr("title", "Marcar para o relatório");
    }
  }

  function botao(cfg) {
    // cfg: { idInquerito, tpOrigem, idOrigem, rotulo, temComentario, somenteRelatorio }
    // somenteRelatorio: registros que ainda não viram nó do grafo da Mesa de Inquérito (arquivos
    // do aparelho) saem só com o 🗒️, sem o 📌 de entidade.
    var $grupo = $("<span>").addClass("acoes-registro");

    var chave = chaveMarcacao(cfg.tpOrigem, cfg.idOrigem);
    idInqueritoAtual = idInqueritoAtual || cfg.idInquerito;

    $("<input>")
      .attr("type", "checkbox")
      .addClass("lote-checkbox")
      .attr("title", "Selecionar para marcar em lote")
      .attr("data-chave", chave)
      .prop("checked", !!selecionados[chave])
      .on("click", function (e) { e.stopPropagation(); })
      .on("change", function () {
        if (this.checked) {
          selecionados[chave] = cfg;
        } else {
          delete selecionados[chave];
        }
        atualizarBarraLote();
      })
      .appendTo($grupo);

    if (!cfg.somenteRelatorio) {
      $("<button>")
        .addClass("ent-botao" + (cfg.temComentario ? " tem-comentario" : ""))
        .attr("title", "Criar entidade / comentário")
        .attr("data-chave", chave)
        .html("📌")
        .on("click", function (e) {
          e.stopPropagation();
          abrir(cfg);
        })
        .appendTo($grupo);
    }
    var cache = marcacoesCache[chave];
    var temMarcacao = cache && cache.count > 0;
    var $btnRel = $("<button>")
      .addClass("ent-botao rel-botao" + (temMarcacao ? " tem-marcacao" : ""))
      .attr("title", "Marcar para o relatório")
      .attr("data-chave", chave)
      .html("🗒️")
      .on("click", function (e) {
        e.stopPropagation();
        abrirRelatorio(cfg);
      })
      .appendTo($grupo);
    if (temMarcacao) pintarBotaoMarcacao(chave, cache);

    return $grupo;
  }

  // marcações de um tipo de origem -> cache interno + destaque dos botões já renderizados.
  // Chamar mais de uma vez para o mesmo tipo REPETE a contagem (o cache é aditivo), então cada
  // tela carrega isso uma vez só; o que muda depois passa por atualizarContagemMarcacao.
  function carregarMarcacoes(idInquerito, tpOrigem, callback) {
    idInqueritoAtual = idInqueritoAtual || idInquerito;
    return $.getJSON(
      "/api/investigacao/inqueritos/" + idInquerito + "/marcacoes?tp_origem=" + encodeURIComponent(tpOrigem)
    ).done(function (lista) {
      (lista || []).forEach(function (m) {
        var chave = chaveMarcacao(tpOrigem, m.id_origem);
        var item = marcacoesCache[chave] || { count: 0, cor: null, marcadores: [] };
        item.count++;
        if (m.co_cor && !item.cor) item.cor = m.co_cor;
        // marcadores vêm na mesma resposta (só os DESTE usuário — ver relatorios_utils.
        // listar_marcacoes), então o botão já nasce com a cor e o title certos
        if (m.marcadores && m.marcadores.length && !(item.marcadores || []).length) {
          item.marcadores = m.marcadores;
        }
        idMarcacaoPorChave[chave] = m.id_marcacao;
        marcacoesCache[chave] = item;
      });
      // atualiza os botões já renderizados com a cor do cache
      Object.keys(marcacoesCache).forEach(function (chave) {
        var item = marcacoesCache[chave];
        if (item.count > 0) pintarBotaoMarcacao(chave, item);
      });
      if (callback) callback();
    });
  }

  function carregarContagens(idInquerito, tpOrigem, callback) {
    // carrega as contagens de entidade (para o callback) e, em paralelo, as de marcação
    // (guardadas no cache interno para o botão de relatório se destacar)
    carregarMarcacoes(idInquerito, tpOrigem);

    $.getJSON(
      "/api/investigacao/inqueritos/" + idInquerito + "/entidades?tp_origem=" + encodeURIComponent(tpOrigem)
    ).done(function (lista) {
      var contagens = {};
      lista.forEach(function (e) {
        contagens[e.id_origem] = (contagens[e.id_origem] || 0) + 1;
        // destaca o botão de entidade (📌) se houver comentários — e só ele: o seletor sem o
        // :not(.rel-botao) de $botaoComentario() acendia junto o 🗒️ do mesmo registro
        $botaoComentario(tpOrigem, e.id_origem).addClass("tem-comentario");
      });
      callback(contagens);
    });
  }

  $(function () {
    montarModal();
    montarModalRelatorio();
    montarBarraLote();
    montarModalLote();
  });

  window.EntidadesUI = {
    botao: botao,
    carregarContagens: carregarContagens,
    carregarMarcacoes: carregarMarcacoes,
    // abrirRelatorio exposto para a lista de conversas do WhatsApp, que monta o botão 🗒️ dentro
    // de uma STRING de HTML (desempenho, ver htmlConversa em js/whatsapp.js) e por isso não pode
    // passar por botao(), que devolve nós jQuery
    abrirRelatorio: abrirRelatorio,
    recarregarMarcadores: function (callback) {
      marcadoresCarregados = false;
      carregarMarcadores(callback);
    },
  };
})();
