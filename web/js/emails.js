(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var TP_ORIGEM = "email";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $busca = $("#filtro-busca");
  var $fonte = $("#filtro-fonte");
  var $remetente = $("#filtro-remetente");
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");

  if (!idInquerito) {
    Estados.erro($("#tabela"), { mensagem: "Parâmetros inválidos na URL." });
    return;
  }

  var contagens = {};

  function htmlParaTexto(h) {
    if (!h) return "—";
    h = h.replace(/<\/?p[^>]*>/gi, "\n").replace(/<br\s*\/?>/gi, "\n");
    h = h.replace(/<[^>]+>/g, "");
    h = h.replace(/&nbsp;/gi, " ").replace(/&amp;/gi, "&").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
    h = h.replace(/\n{3,}/g, "\n\n").trim();
    return h;
  }

  var uid = 0;
  function modalHtml(html) {
    var id = "modal-html-" + (++uid);
    var $overlay = $(
      '<div class="modal-overlay hidden" id="' + id + '">' +
        '<div class="modal" style="width:min(900px,94vw);">' +
          '<div class="modal-header"><h2>Conteúdo original do e-mail</h2>' +
          '<button type="button" class="modal-close" data-fechar="' + id + '">&times;</button></div>' +
          '<div class="modal-body" style="font-size:13px;line-height:1.5;word-wrap:break-word;"></div>' +
        '</div>' +
      '</div>'
    ).appendTo("body");
    $overlay.find(".modal-body").append(html);
    $overlay.removeClass("hidden");
    $overlay.on("click", function (e) { if (e.target === this) $overlay.addClass("hidden"); });
    $overlay.find("[data-fechar=" + id + "]").on("click", function () { $overlay.addClass("hidden"); });
  }

  function paramsFiltros() {
    var p = {};
    if (idExtracao) p.extracao = idExtracao;
    var busca = $busca.val().trim();
    if (busca) p.busca = busca;
    if ($fonte.val()) p.fonte = $fonte.val();
    if ($remetente.val()) p.remetente = $remetente.val();
    if ($dataDe.val()) p.data_de = $dataDe.val();
    if ($dataAte.val()) p.data_ate = $dataAte.val();
    return p;
  }

  function celulaConteudo(r) {
    // a lista já traz só uma prévia do corpo (left(tx_corpo, 700)); "Ver HTML" busca o integral
    // sob demanda. Sem isso, um corpo de 200 mil caracteres esticava a linha inteira da tabela.
    var $container = $("<span>").addClass("email-conteudo");
    var texto = htmlParaTexto(r.tx_corpo);
    if (r.corpo_truncado) texto += " …";
    var $txt = $("<span>").addClass("email-conteudo-txt").text(texto);
    $container.append($txt);
    if (r.tx_corpo) {
      $("<button>")
        .text("Ver HTML")
        .css({ marginLeft: 8, padding: "2px 8px", fontSize: 12, cursor: "pointer", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)", color: "var(--text-primary)" })
        .on("click", function () {
          var url = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
                    "/emails/" + encodeURIComponent(r.id_email) + "/corpo";
          $.getJSON(url).done(function (d) { modalHtml((d && d.tx_corpo) || r.tx_corpo); })
            .fail(function () { modalHtml(r.tx_corpo); });
        })
        .appendTo($container);
    }
    return $container;
  }

  var tabela = TabelaPaginada.criar({
    alvo: "#tabela",
    url: "/api/investigacao/inqueritos/" + idInquerito + "/emails",
    idDe: function (r) { return r.id_email; },
    ordenarPadrao: "recentes",
    params: paramsFiltros,
    autoCarregar: false,
    colunas: [
      {
        rotulo: "Data",
        ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" },
        render: function (r) { return r.dt_email ? formatarDataHora(r.dt_email) : "—"; }
      },
      { rotulo: "Fonte", render: function (r) { return r.no_fonte || "—"; } },
      {
        rotulo: "Remetente",
        ordenar: { asc: "remetente", desc: "remetente_desc" },
        render: function (r) { return r.no_remetente || "—"; }
      },
      { rotulo: "Destinatários", render: function (r) { return r.tx_destinatarios || "—"; } },
      {
        rotulo: "Assunto",
        ordenar: { asc: "assunto", desc: "assunto_desc" },
        render: function (r) { return r.tx_assunto || "—"; }
      },
      { rotulo: "Conteúdo", render: celulaConteudo },
      {
        rotulo: "Entidade",
        exportavel: false,
        render: function (r) {
          return EntidadesUI.botao({
            idInquerito: idInquerito,
            tpOrigem: TP_ORIGEM,
            idOrigem: r.id_email,
            rotulo: "e-mail " + (r.tx_assunto || ""),
            temComentario: !!contagens[r.id_email],
            aoMudar: function () {}
          });
        }
      }
    ],
    erro: "Não foi possível carregar os e-mails.",
    exportar: {
      nome: "emails",
      linha: function (r) {
        return [
          r.dt_email ? formatarDataHora(r.dt_email) : "",
          r.no_fonte || "", r.no_remetente || "", r.tx_destinatarios || "", r.tx_assunto || "",
          htmlParaTexto(r.tx_corpo),
        ];
      }
    }
  });

  // popula os selects de fonte e remetente
  var filtrosUrl = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/emails/filtros" + filtrosUrl).done(function (f) {
    (f.fontes || []).forEach(function (v) { $("<option>").attr("value", v).text(v).appendTo($fonte); });
    (f.remetentes || []).forEach(function (v) { $("<option>").attr("value", v).text(v).appendTo($remetente); });
  });

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(tabela.recarregar, 300);
  });
  $fonte.on("change", tabela.recarregar);
  $remetente.on("change", tabela.recarregar);
  $dataDe.on("change", tabela.recarregar);
  $dataAte.on("change", tabela.recarregar);

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    tabela.carregar();
  });
})();
