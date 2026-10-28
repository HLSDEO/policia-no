/* Busca de evidências (web/busca_conteudo.html).
 *
 * Expõe na tela o motor de busca híbrida do chat de IA (api/rotas/busca.py ->
 * funcoes/rag_hibrido.py): literal + sentido + grafo fundidos por RRF. A referência de
 * cada resultado é o mesmo marcador que o chat cita — [msg:wpp:ID] / [doc:ID:PÁGINA] —
 * então o link de destino reaproveita DocTrecho.url e MapaTelas.paginaConversa, as mesmas
 * rotas que js/ia.js usa nas citações clicáveis.
 *
 * A consulta e o inquérito vivem na URL (?q= &inquerito=) para o link ser compartilhável —
 * inclusive é o destino do Ctrl+K quando a busca da palette é texto livre e não nome de tela. */
(function () {
  "use strict";

  var $select = $("#inquerito-select");
  var $busca = $("#busca-input");
  var $btn = $("#buscar-btn");
  var $lista = $("#resultados-lista");
  var $vazio = $("#resultados-vazio");
  var $total = $("#total-registros");
  var $avisosCard = $("#avisos-card");
  var $avisos = $("#bc-avisos");

  var FONTES = {
    semantico: { rotulo: "sentido", classe: "bc-fonte" },
    textual: { rotulo: "literal", classe: "bc-fonte bc-fonte-textual" },
    grafo: { rotulo: "grafo", classe: "bc-fonte bc-fonte-grafo" },
  };

  var buscando = false;

  /* [msg:fonte:ID] e [doc:ID[:PÁGINA]] viram o link da tela de origem — as mesmas
   * montagens de URL que as citações clicáveis do chat usam. */
  function urlDestino(referencia, inquerito) {
    var m = /^\[msg:(wpp|sms|msg):(\d+)\]$/.exec(referencia || "");
    if (m) {
      return {
        href: window.MapaTelas.paginaConversa(m[1]) +
          "?inquerito=" + encodeURIComponent(inquerito) +
          "&fonte=" + m[1] + "&msg=" + encodeURIComponent(m[2]),
        rotulo: "Abrir a mensagem original",
      };
    }
    m = /^\[doc:(\d+)(?::(\d+(?:-\d+)?))?\]$/.exec(referencia || "");
    if (m) {
      return {
        href: window.DocTrecho.url({ inquerito: inquerito, doc: m[1], pagina: m[2] }),
        rotulo: "Abrir o documento" + (m[2] ? " na página " + m[2] : " inteiro"),
      };
    }
    return null;
  }

  function renderResultado(r, termo, inquerito) {
    var $item = $("<div>").addClass("bc-item");

    var $cab = $("<div>").addClass("bc-item-cabecalho");
    var f = FONTES[r.fonte] || { rotulo: r.fonte || "?", classe: "bc-fonte" };
    $cab.append($("<span>").addClass(f.classe).text(f.rotulo));
    $cab.append($("<span>").addClass("bc-ref").text(r.referencia || ""));
    $cab.append($("<span>").addClass("bc-origens").text("score " + Number(r.score || 0).toFixed(3)));
    $item.append($cab);

    // o trecho é conteúdo de documento/mensagem: nunca entra como HTML cru — realcarTermo
    // escapa tudo e devolve só os <mark> do termo procurado
    $item.append($("<p>").addClass("bc-trecho")
      .html(window.DocTrecho.realcarTermo(r.trecho || "", termo).html));

    var destino = urlDestino(r.referencia, inquerito);
    if (destino) {
      $item.append($("<a>").addClass("bc-destino").attr({ href: destino.href, target: "_blank", rel: "noopener" })
        .text(destino.rotulo + " ↗"));
      // ℹ️ cadeia de custódia (js/utils/evidencia_info.js), ao lado do link de texto — a
      // referência do resultado é exatamente o marcador que o chat cita, então a fonte é a mesma
      var mInfo = /^\[(?:msg:(wpp|sms|msg)|doc):(\d+)/.exec(r.referencia || "");
      if (mInfo && window.EvidenciaInfo) {
        $item.append($("<span>").text(" ")).append(window.EvidenciaInfo.pastilhaEl(mInfo[1] || "doc", mInfo[2]));
      }
    }
    return $item;
  }

  function mostrarAvisos(resp) {
    var partes = [];
    if (resp.aviso) partes.push(resp.aviso);
    var o = resp.origens || {};
    if (o.semantico != null || o.textual != null || o.grafo != null) {
      partes.push("Candidatos: " + (o.textual || 0) + " literais · " + (o.semantico || 0) +
        " por sentido · " + (o.grafo || 0) + " por grafo.");
    }
    if (partes.length) {
      $avisos.text(partes.join(" "));
      $avisosCard.removeClass("hidden");
    } else {
      $avisosCard.addClass("hidden");
    }
  }

  function buscar() {
    if (buscando) return;
    var inquerito = $select.val();
    var termo = $busca.val().trim();
    if (!inquerito) {
      if (window.Toast) Toast.erro("Escolha o inquérito onde procurar.");
      return;
    }
    if (!termo) {
      $busca.focus();
      return;
    }

    buscando = true;
    Carregando.botao($btn, "Buscando…");
    $vazio.hide();
    Carregando.emBloco($lista);
    if (window.EstadoUrl) EstadoUrl.salvar({ inquerito: inquerito, q: termo });

    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(inquerito) + "/busca-hibrida",
      { q: termo, limite: 30 })
      .done(function (resp) {
        Carregando.limpar($lista);
        $lista.empty();
        mostrarAvisos(resp);
        var resultados = (resp && resp.resultados) || [];
        $total.text(resultados.length.toLocaleString("pt-BR") + " resultado(s)");
        if (!resultados.length) {
          Estados.vazio($lista, {
            titulo: "Nada encontrado",
            dica: "Tente outras palavras, ou procure pelo nome de quem aparece no caso.",
          });
          return;
        }
        resultados.forEach(function (r) { $lista.append(renderResultado(r, termo, inquerito)); });
      })
      .fail(function () {
        Carregando.limpar($lista);
        $total.text("");
        Estados.erro($lista, {
          mensagem: "Não foi possível buscar agora.",
          aoTentarDeNovo: buscar,
        });
      })
      .always(function () {
        buscando = false;
        Carregando.restaurar($btn);
      });
  }

  // ---------- inquérito (pré-selecionado por ?inquerito=, como nas outras telas) ----------

  var prefills = {
    inquerito: window.AnalyserUtils.paramUrl("inquerito") ||
      (window.EstadoUrl ? window.EstadoUrl.ler(["inquerito"]).inquerito : ""),
    q: window.EstadoUrl ? window.EstadoUrl.ler(["q"]).q : window.AnalyserUtils.paramUrl("q"),
  };

  function carregarInqueritos() {
    $.getJSON("/api/investigacao/inqueritos").done(function (lista) {
      $select.empty();
      if (!lista.length) {
        $select.append($("<option>").val("").text("Nenhum inquérito disponível"));
        $btn.prop("disabled", true);
        return;
      }
      var selecionou = false;
      lista.forEach(function (inq) {
        var $opt = $("<option>").val(inq.id_inquerito).text(inq.nu_inquerito);
        if (prefills.inquerito && !selecionou &&
            (inq.nu_inquerito === prefills.inquerito || String(inq.id_inquerito) === prefills.inquerito)) {
          $opt.prop("selected", true);
          selecionou = true;
        }
        $opt.appendTo($select);
      });
    }).fail(function () {
      if (window.Toast) Toast.erro("Não foi possível listar os inquéritos.");
    });
  }

  carregarInqueritos();
  if (prefills.q) $busca.val(prefills.q);

  $btn.on("click", buscar);
  $busca.on("keydown", function (e) { if (e.key === "Enter") buscar(); });

  // a palette (Ctrl+K) manda texto livre para cá com ?q= — busca sozinha ao abrir
  if (prefills.q && prefills.inquerito) buscar();
})();
