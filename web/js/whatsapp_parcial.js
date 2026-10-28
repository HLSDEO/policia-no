/* WhatsApp "parcial" — mídia da nuvem (iCloud) reconstruída como conversa.
 *
 * O texto das mensagens está criptografado (ChatStorage.sqlite.enc), mas a mídia vem organizada
 * por JID com o mtime original. Esta tela agrupa os arquivos por JID e mostra cada contato/grupo
 * como uma "conversa" de mídias em ordem cronológica — áudio, imagem, vídeo e documento.
 *
 * Marcar para o relatório: cada mídia é um arquivo do aparelho (arq_tb01_arquivos), então o
 * botão 🗒️ usa tp_origem "arquivo" com o id_arquivo — o mesmo que a tela de Arquivos usa
 * (ver js/arquivos.js). A "conversa" aqui não tem id_conversa (é só um JID reconstruído),
 * então não há marcação de conversa como no whatsapp.html (wpp_conversa): o 🗒️ da lista
 * marca em lote todas as mídias daquele contato, pelo mesmo modal de lote das mensagens.
 *
 * Paginação e filtro rodam NO SERVIDOR (ver funcoes/whatsapp_parcial_utils.py): a tela carrega
 * a janela das PAGE mídias mais recentes e busca as anteriores sob demanda — contato com
 * centenas de mídias montava <img>/<video> de tudo e travava o navegador.
 */
(function () {
  "use strict";

  var U = window.AnalyserUtils;
  var params = new URLSearchParams(window.location.search);
  var idInquerito = params.get("inquerito");
  var idExtracao = params.get("extracao");

  // tp_origem em REL_TB03_MARCACOES para as mídias (cada uma é um arquivo do aparelho)
  var TP_ORIGEM = "arquivo";

  // Tamanho da página do servidor (a janela das mídias mais recentes).
  var PAGE = 20;

  // Filtro por categoria da mídia (mesmas chaves devolvidas pela API em por_tipo).
  var TIPOS = [
    { chave: null, rotulo: "Todos" },
    { chave: "audio", rotulo: "Áudio", icone: "🎵" },
    { chave: "imagem", rotulo: "Imagem", icone: "🖼" },
    { chave: "video", rotulo: "Vídeo", icone: "🎞" },
    { chave: "documento", rotulo: "Documento", icone: "📄" }
  ];
  var ICONES_TIPO = { audio: "🎵", imagem: "🖼", video: "🎞", documento: "📄" };

  var conversas = [];
  var jidAtual = null;
  var identAtual = null;       // número/JID mostrado no subtítulo do cabeçalho
  var midiasTotais = [];       // páginas já carregadas, em ordem cronológica (mais antigas primeiro)
  var totalServidor = 0;       // total no servidor para o filtro ativo
  var totalGeralServidor = 0;  // total da conversa inteira (sem filtro), para os chips/header
  var tipoFiltro = null;       // null | audio | imagem | video | documento
  var seqConversa = 0;         // descarta resposta atrasada de uma conversa já abandonada
  var carregandoMais = false;  // evita duplo clique no "Carregar mais"

  // Escapa inclusive as aspas: entra em atributos (title, data-jid) interpolados em HTML.
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function baseUrl() {
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/whatsapp-parcial?extracao=" + encodeURIComponent(idExtracao);
  }

  function midiasUrl(jid, offset, tipo) {
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/whatsapp-parcial/midias?jid=" + encodeURIComponent(jid) +
      "&offset=" + offset + "&limite=" + PAGE +
      (tipo ? "&tipo=" + encodeURIComponent(tipo) : "") +
      "&extracao=" + encodeURIComponent(idExtracao);
  }

  function nomeConversa(c) {
    return c.nome || c.numero || c.jid || "desconhecido";
  }

  function avatar(c) {
    var nome = c.nome || "?";
    var partes = String(nome).trim().split(/\s+/);
    var iniciais = (partes[0] || "?").charAt(0) + (partes[1] ? partes[1].charAt(0) : "");
    var cor = "#25d366";
    if (c.tipo === "grupo") cor = "#6b7c93";
    else if (c.tipo === "status") cor = "#a5673f";
    return '<div class="wa-avatar" style="background:' + cor + '">' + esc(iniciais.toUpperCase()) + "</div>";
  }

  function temEntidades() {
    return !!(window.EntidadesUI && idInquerito);
  }

  // Subítulo da lista de conversas: contagem por categoria (só as presentes) + período coberto.
  function metaContato(c) {
    var qt = c.qt_por_tipo || {};
    var partes = [];
    TIPOS.forEach(function (t) {
      if (t.chave && qt[t.chave]) partes.push(ICONES_TIPO[t.chave] + " " + U.formatarNumero(qt[t.chave]));
    });
    var meta = partes.length ? partes.join(" · ") : U.formatarNumero(c.qt_midia) + " mídia(s)";
    if (c.ultima) meta += " · até " + U.formatarDataSimples(c.ultima);
    return meta;
  }

  // Marca todas as mídias de um dia já carregadas (mesma ideia do "Marcar dia" do
  // whatsapp.js): checa os checkboxes do lote e abre o modal de marcação em lote.
  function marcarDiaParaRelatorio(dia) {
    $("#wpp-mensagens").find('.wpp-msg[data-dia="' + dia + '"]').find(".lote-checkbox").each(function () {
      if (!this.checked) {
        this.checked = true;
        $(this).trigger("change");
      }
    });
    $("#lote-marcar-btn").trigger("click");
  }

  // O 🗒️ da lista de contatos: abre a conversa (se preciso) e marca em lote TODAS as
  // mídias dela — busca página por página no servidor antes de selecionar, senão só a janela
  // visível iria para o relatório. Não usa .rel-botao/data-chave porque a conversa parcial não
  // é um registro único — é o conjunto dos arquivos daquele JID.
  function marcarContatoParaRelatorio(jid) {
    function selecionarTudo() {
      $("#wpp-mensagens").find(".wpp-msg .lote-checkbox").each(function () {
        if (!this.checked) {
          this.checked = true;
          $(this).trigger("change");
        }
      });
      $("#lote-marcar-btn").trigger("click");
    }

    function carregarTudo(offset) {
      var seq = seqConversa;
      if (offset === 0) {
        midiasTotais = [];
        tipoFiltro = null;
        mostrarCarregandoMsgs();
        Toast.info("Carregando todas as mídias deste contato…");
      }
      $.getJSON(midiasUrl(jidAtual, offset, null)).done(function (resp) {
        if (seq !== seqConversa) return;
        totalServidor = resp.total || 0;
        totalGeralServidor = resp.total_geral || 0;
        midiasTotais = midiasTotais.concat(resp.midias || []);
        if (midiasTotais.length < totalServidor) {
          carregarTudo(midiasTotais.length);
          return;
        }
        renderFiltros(resp.por_tipo || {});
        renderJanela(true);
        selecionarTudo();
      }).fail(function () {
        if (seq !== seqConversa) return;
        Toast.erro("Não foi possível carregar todas as mídias.");
      });
    }

    var tudoCarregado = jid === jidAtual && !tipoFiltro &&
      midiasTotais.length && totalServidor <= midiasTotais.length;
    if (tudoCarregado) {
      selecionarTudo();
      return;
    }
    if (jid !== jidAtual) {
      abrirConversa(jid, function () { carregarTudo(0); });
      return;
    }
    carregarTudo(0);
  }

  function renderConversas(lista) {
    var $convs = $("#wpp-conversas").empty();
    if (!lista.length) {
      $convs.append('<div class="wpp-vazio">Nenhuma mídia de WhatsApp nesta nuvem.</div>');
      return;
    }
    lista.forEach(function (c) {
      var $item = $(
        '<div class="wa-conversa-item" data-jid="' + esc(c.jid) + '">' +
          avatar(c) +
          '<div class="wa-conversa-corpo">' +
            '<div class="wa-conversa-linha1">' +
              '<span class="wa-conversa-nome" title="' + esc(c.jid) + '">' + esc(nomeConversa(c)) + "</span>" +
              '<span class="wa-conversa-data">' + (c.ultima ? esc(U.formatarDataSimples(c.ultima)) : "—") + "</span>" +
            "</div>" +
            '<div class="wa-conversa-preview wpp-contato-meta">' + esc(metaContato(c)) + "</div>" +
          "</div>" +
        "</div>"
      );
      if (temEntidades()) {
        $("<button>")
          .attr("type", "button")
          .addClass("ent-botao wa-conversa-marcar")
          .attr("title", "Marcar todas as mídias deste contato para o relatório")
          .text("🗒️")
          .on("click", function (e) {
            e.stopPropagation();
            marcarContatoParaRelatorio(c.jid);
          })
          .appendTo($item);
      }
      $item.on("click", function () { abrirConversa(c.jid); });
      $convs.append($item);
    });
  }

  function abrirConversa(jid, depois) {
    var c = null;
    for (var i = 0; i < conversas.length; i++) {
      if (conversas[i].jid === jid) { c = conversas[i]; break; }
    }
    jidAtual = jid;
    identAtual = c && c.numero ? c.numero : jid;
    midiasTotais = [];
    totalServidor = 0;
    totalGeralServidor = 0;
    tipoFiltro = null;
    var seq = ++seqConversa;
    $(".wa-conversa-item").removeClass("active");
    $('.wa-conversa-item[data-jid="' + esc(jid) + '"]').addClass("active");
    $("#wpp-header").html(
      '<div class="wa-main-header-nome">' + esc(nomeConversa(c || { jid: jid })) + "</div>" +
      '<div class="wa-main-header-sub wpp-contato-meta">' + esc(identAtual) + "</div>"
    );
    $("#wpp-filtros").empty().addClass("hidden");
    mostrarCarregandoMsgs();
    carregarPagina(-PAGE, true, function () {
      if (seq !== seqConversa) return;
      if (depois) depois();
    });
  }

  // Página do servidor: offset negativo conta do fim (as mais recentes primeiro — o caso da
  // abertura da conversa); positivo, do início (o caso do "Carregar mais", que pede a janela
  // imediatamente anterior às já carregadas).
  function carregarPagina(offset, primeira, aoTerminar) {
    var seq = seqConversa;
    $.getJSON(midiasUrl(jidAtual, offset, tipoFiltro)).done(function (resp) {
      if (seq !== seqConversa) return;
      if (primeira) {
        midiasTotais = resp.midias || [];
      } else {
        midiasTotais = (resp.midias || []).concat(midiasTotais);
      }
      totalServidor = resp.total || 0;
      totalGeralServidor = resp.total_geral || 0;
      atualizarSubtotalHeader();
      renderFiltros(resp.por_tipo || {});
      renderJanela(primeira);
      if (aoTerminar) aoTerminar();
    }).fail(function () {
      if (seq !== seqConversa) return;
      $("#wpp-mensagens").html('<div class="wpp-vazio">Erro ao carregar a mídia.</div>');
      $("#wpp-filtros").empty().addClass("hidden");
    });
  }

  function atualizarSubtotalHeader() {
    var $sub = $("#wpp-header .wa-main-header-sub");
    if (!$sub.length) return;
    var texto = identAtual + " · " + U.formatarNumero(totalServidor) + " mídia(s)";
    if (tipoFiltro && totalGeralServidor !== totalServidor) {
      texto += " de " + U.formatarNumero(totalGeralServidor);
    }
    $sub.text(texto);
  }

  // Chips de filtro por categoria. As contagens saem do conjunto SEM filtro (por_tipo/
  // total_geral da API), então não mudam ao filtrar — e "Todos" continua visível mesmo
  // quando o filtro ativo não tem resultados.
  function renderFiltros(porTipo) {
    var $f = $("#wpp-filtros").empty();
    if (!totalGeralServidor) {
      $f.addClass("hidden");
      return;
    }
    $f.removeClass("hidden");
    TIPOS.forEach(function (t) {
      var qt = t.chave ? (porTipo ? porTipo[t.chave] : 0) : totalGeralServidor;
      $("<button>")
        .attr("type", "button")
        .addClass("wpp-filtro" + (tipoFiltro === t.chave ? " active" : ""))
        .attr("title", t.chave ? "Filtrar a conversa por " + t.rotulo.toLowerCase() : "Mostrar todas as mídias")
        .text((t.icone ? t.icone + " " : "") + t.rotulo + " · " + U.formatarNumero(qt))
        .on("click", function () { aplicarFiltro(t.chave); })
        .appendTo($f);
    });
  }

  function aplicarFiltro(tipo) {
    if (tipo === tipoFiltro) return;
    tipoFiltro = tipo;
    midiasTotais = [];
    totalServidor = 0;
    mostrarCarregandoMsgs();
    carregarPagina(-PAGE, true);
  }

  function mostrarCarregandoMsgs() {
    $("#wpp-mensagens").empty().append(
      $("<div>").addClass("wpp-carregando")
        .html('<span class="spinner"></span> <span>carregando mídias…</span>')
    );
  }

  function botaoCarregarMais(restantes) {
    return $("<div>").addClass("wpp-carregar-mais-wrap").append(
      $("<button>").addClass("wpp-carregar-mais").attr("type", "button")
        .text("⬆ Carregar mais (" + U.formatarNumero(restantes) + " restantes)")
        .on("click", carregarMais)
    );
  }

  function carregarMais() {
    if (carregandoMais) return;
    carregandoMais = true;
    var $msg = $("#wpp-mensagens");
    var alturaAntes = $msg[0].scrollHeight;
    var restantes = totalServidor - midiasTotais.length;
    var offset = Math.max(0, restantes - PAGE);
    var seq = seqConversa;
    $.getJSON(midiasUrl(jidAtual, offset, tipoFiltro)).done(function (resp) {
      if (seq !== seqConversa) return;
      midiasTotais = (resp.midias || []).concat(midiasTotais);
      renderJanela(false);
      // preserva a posição visual ao inserir conteúdo acima
      $msg[0].scrollTop += $msg[0].scrollHeight - alturaAntes;
    }).fail(function () {
      if (seq !== seqConversa) return;
      Toast.erro("Não foi possível carregar mídias anteriores.");
    }).always(function () {
      carregandoMais = false;
    });
  }

  function renderJanela(rolarParaFim) {
    var $msg = $("#wpp-mensagens").empty();
    if (!midiasTotais.length) {
      var texto = tipoFiltro
        ? "Nenhuma mídia deste tipo nesta conversa."
        : "Nenhuma mídia nesta conversa.";
      $msg.append('<div class="wpp-vazio">' + texto + "</div>");
      return;
    }
    var restantes = totalServidor - midiasTotais.length;
    if (restantes > 0) {
      $msg.append(botaoCarregarMais(restantes));
    }
    // remontar do zero mantém os divisores de dia corretos na fronteira entre lotes; a
    // seleção do lote sobrevive porque mora no EntidadesUI, não no DOM (o checkbox de
    // cada mídia já nasce checado de novo em renderMidia via botao()).
    var ultimoDia = null;
    midiasTotais.forEach(function (m) {
      var dia = m.dt_modificacao ? String(m.dt_modificacao).slice(0, 10) : null;
      if (dia && dia !== ultimoDia) {
        ultimoDia = dia;
        var $dia = $("<div>").addClass("wpp-dia")
          .append($("<span>").text(U.formatarData(m.dt_modificacao)));
        if (temEntidades()) {
          $dia.append(
            $("<button>").addClass("wpp-dia-marcar").attr("type", "button")
              .attr("title", "Marcar todas as mídias deste dia (já carregadas) para o relatório")
              .text("🗒️ Marcar dia")
              .on("click", (function (d) { return function () { marcarDiaParaRelatorio(d); }; })(dia))
          );
        }
        $msg.append($dia);
      }
      $msg.append(renderMidia(m, dia));
    });
    if (rolarParaFim) {
      $msg.scrollTop($msg[0].scrollHeight);
    }
    // mídia direta (docs/no_midia_direta.md): a janela carregada pergunta uma vez se pode
    // baixar do nó; o preview das miniaturas não entra (é JPEG gerado no central, não cópia)
    if (window.MidiaDireta) {
      MidiaDireta.preparar(midiasTotais.map(function (m) { return m.id_arquivo; })
        .filter(function (x) { return x != null; }), false);
      MidiaDireta.preparar(midiasTotais.map(function (m) { return m.id_arquivo; })
        .filter(function (x) { return x != null; }), true);
    }
  }

  // "0:07", "12:34" ou "1:02:03" a partir dos segundos do player (loadedmetadata).
  function formatarDuracao(segundos) {
    var total = Math.floor(segundos);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var p2 = function (n) { return (n < 10 ? "0" : "") + n; };
    return (h ? h + ":" + p2(m) + ":" + p2(s) : m + ":" + p2(s));
  }

  function definirDuracao($el, segundos) {
    if (!isFinite(segundos) || !segundos) return;
    $el.text("⏱ " + formatarDuracao(segundos));
  }

  function urlConteudo(m) {
    return "/api/investigacao/arquivos/" + m.id_arquivo + "/conteudo";
  }

  // ---------- Modal de imagem em tela cheia ----------

  var $modalImagem = $("#wpp-modal-imagem");
  var $modalTitulo = $("#wpp-modal-titulo");
  var $modalCorpo = $("#wpp-modal-corpo");

  function abrirModalImagem(m) {
    $modalTitulo.text(m.no_arquivo || "Imagem");
    $modalCorpo.empty().append(
      $("<div>").addClass("wa-midia-carregando")
        .html('<span class="spinner"></span> <span>carregando…</span>')
    );
    $("<img>").addClass("wa-midia-imagem wpp-modal-imagem-img")
      .attr("alt", m.no_arquivo || "imagem")
      .on("load", function () { $modalCorpo.find(".wa-midia-carregando").remove(); })
      .on("error", function () {
        $modalCorpo.empty().append(
          $("<div>").addClass("wa-midia-carregando").text("⚠ não foi possível carregar esta imagem.")
        );
      })
      .attr("src", urlConteudo(m) + "?preview=true")
      .appendTo($modalCorpo);
    $modalImagem.removeClass("hidden");
  }

  function fecharModalImagem() {
    $modalImagem.addClass("hidden");
    $modalCorpo.empty(); // descarrega a imagem ao fechar
  }

  $("#wpp-modal-fechar").on("click", fecharModalImagem);
  $modalImagem.on("click", function (e) { if (e.target === this) fecharModalImagem(); });
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && !$modalImagem.hasClass("hidden")) fecharModalImagem();
  });

  function renderMidia(m, dia) {
    var url = urlConteudo(m);
    // áudio/vídeo/documento são o binário cru → candidato à URL do nó (docs/no_midia_direta.md);
    // o ?preview=true da imagem é JPEG nosso, gerado no central — não tem equivalente lá
    var urlDireta = window.MidiaDireta ? MidiaDireta.url(m.id_arquivo, url, false) : url;
    var urlBaixar = window.MidiaDireta
      ? MidiaDireta.url(m.id_arquivo, url + "?download=true", true) : url + "?download=true";
    var mime = String(m.tp_mime || "").toLowerCase();
    var $b = $("<div>").addClass("wpp-msg");
    if (dia) $b.attr("data-dia", dia);
    var $media = $("<div>").addClass("wpp-msg-media");

    if (mime.indexOf("audio/") === 0) {
      var $durAudio = $("<span>").addClass("wpp-msg-duracao");
      $("<audio>").attr("controls", true).attr("preload", "none").attr("src", urlDireta)
        .on("loadedmetadata", function () { definirDuracao($durAudio, this.duration); })
        .on("error", function () {
          var c = window.MidiaDireta ? MidiaDireta.fallback(this, url) : url;
          if (this.getAttribute("src") !== c) this.setAttribute("src", c);
        })
        .appendTo($media);
      $media.append($durAudio);
    } else if (mime.indexOf("video/") === 0) {
      var $durVideo = $("<span>").addClass("wpp-msg-duracao");
      $("<video>").attr("controls", true).attr("preload", "metadata").attr("src", urlDireta)
        .on("loadedmetadata", function () { definirDuracao($durVideo, this.duration); })
        .on("error", function () {
          var c = window.MidiaDireta ? MidiaDireta.fallback(this, url) : url;
          if (this.getAttribute("src") !== c) this.setAttribute("src", c);
        })
        .appendTo($media);
      $media.append($durVideo);
    } else if (mime.indexOf("image/") === 0) {
      var $imgWrap = $("<div>").addClass("wpp-msg-imagem");
      var $ph = $("<div>").addClass("wpp-msg-imagem-carregando")
        .html('<span class="spinner"></span>');
      $("<img>").attr("loading", "lazy").attr("alt", m.no_arquivo || "imagem")
        .attr("title", "Abrir em tela cheia")
        .on("load", function () { $ph.remove(); })
        .on("error", function () { $ph.text("⚠ imagem indisponível"); })
        .on("click", function () { abrirModalImagem(m); })
        .attr("src", url + "?preview=true")
        .appendTo($imgWrap);
      $imgWrap.append($ph);
      $media.append($imgWrap);
    } else {
      var $a = $("<a>").addClass("wpp-msg-doc").attr("href", urlBaixar).attr("target", "_blank");
      $a.append($("<span>").text("📄 ")).append($("<span>").text(m.no_arquivo || "baixar documento"));
      $media.append($a);
    }

    $b.append($("<div>").addClass("wpp-msg-nome").text(m.no_arquivo || "mídia"));
    $b.append($media);
    var meta = U.formatarDataHora(m.dt_modificacao);
    if (m.qt_bytes != null) meta += " · " + U.formatarTamanho(m.qt_bytes);
    var $rodape = $("<div>").addClass("wpp-msg-rodape")
      .append($("<div>").addClass("wpp-msg-meta").text(meta));
    if (temEntidades() && m.id_arquivo != null) {
      $rodape.append(EntidadesUI.botao({
        idInquerito: idInquerito,
        tpOrigem: TP_ORIGEM,
        idOrigem: m.id_arquivo,
        rotulo: "arquivo " + (m.no_arquivo || m.tx_caminho || m.id_arquivo),
        somenteRelatorio: true
      }));
    }
    $b.append($rodape);
    return $b;
  }

  $("#wpp-busca").on("input", function () {
    var t = String($(this).val() || "").trim().toLowerCase();
    $("#wpp-conversas .wa-conversa-item").each(function () {
      var $it = $(this);
      var txt = ($it.find(".wa-conversa-nome").text() + " " + ($it.data("jid") || "")).toLowerCase();
      $it.toggle(!t || txt.indexOf(t) !== -1);
    });
  });

  if (!idInquerito) {
    $("#wpp-conversas").html('<div class="wpp-vazio">Sem inquérito na URL.</div>');
    return;
  }
  // Pinta os 🗒️ já marcados sem recarregar nada (o cache é aditivo: chamar uma vez só).
  // Carrega as marcações antes da lista para os botões já nascerem destacados.
  function carregarConversas() {
    $.getJSON(baseUrl()).done(function (lista) {
      conversas = lista || [];
      renderConversas(conversas);
    });
  }
  if (temEntidades()) {
    EntidadesUI.carregarMarcacoes(idInquerito, TP_ORIGEM, carregarConversas);
  } else {
    carregarConversas();
  }
})();
