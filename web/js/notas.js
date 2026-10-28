(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var TP_ORIGEM = "nota";

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $total = $("#total-registros");
  var $corpo = $("#dados-corpo");
  var $vazio = $("#dados-vazio");
  var $busca = $("#filtro-busca");
  var $status = $("#filtro-status");

  if (!idInquerito) {
    $vazio.text("Parâmetros inválidos na URL.").show();
    return;
  }

  var linhas = [];       // todas as notas carregadas — lista pequena, filtra no cliente
  var contagensEntidades = {};

  function renderLinha(r) {
    var $tr = $("<tr>").attr("data-id", r.id_nota);
    $("<td>").text(formatarDataHora(r.dt_modificacao || r.dt_criacao)).appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-tag").text(r.no_aplicativo || "—")).appendTo($tr);
    $("<td>").text(r.tx_titulo || "—").appendTo($tr);
    $("<td>").append(
      $("<span>").css({ whiteSpace: "pre-wrap", wordBreak: "break-word", display: "inline-block", maxWidth: "480px" })
        .text(r.tx_corpo || "—")
    ).appendTo($tr);
    var $tdStatus = $("<td>").appendTo($tr);
    if (r.cd_status && r.cd_status.toLowerCase() !== "intact") {
      $tdStatus.append($("<span>").addClass("arq-status-apagado").text(r.cd_status));
    } else {
      $tdStatus.text(r.cd_status || "—");
    }
    var $tdAnexos = $("<td>").appendTo($tr);
    var $spanAnexos = $("<span>").addClass("arq-caminho").css({ direction: "ltr", maxWidth: "220px" }).attr("title", r.tx_anexos || "").appendTo($tdAnexos);
    if (r.anexos && r.anexos.length) {
      r.anexos.forEach(function (a, i) {
        if (i > 0) $spanAnexos.append(", ");
        if (a.id_arquivo) {
          $spanAnexos.append($("<a>").attr("href", "/api/investigacao/arquivos/" + a.id_arquivo + "/conteudo").attr("target", "_blank").text(a.nome));
        } else {
          $spanAnexos.append(document.createTextNode(a.nome));
        }
      });
    } else {
      $spanAnexos.text(r.tx_anexos || "—");
    }
    $("<td>").append(EntidadesUI.botao({
      idInquerito: idInquerito,
      tpOrigem: TP_ORIGEM,
      idOrigem: r.id_nota,
      rotulo: "nota " + (r.tx_titulo || r.no_aplicativo || ""),
      temComentario: !!contagensEntidades[r.id_nota],
      aoMudar: function () {}
    })).appendTo($tr);
    return $tr;
  }

  function popularFiltroStatus() {
    var vistos = {};
    linhas.forEach(function (r) { if (r.cd_status) vistos[r.cd_status] = true; });
    $status.find("option:not(:first)").remove();
    Object.keys(vistos).sort().forEach(function (s) {
      $status.append($("<option>").val(s).text(s));
    });
  }

  function filtrar() {
    var termo = $busca.val().trim().toLowerCase();
    var status = $status.val();
    return linhas.filter(function (r) {
      var temConteudo = !!(r.tx_corpo && r.tx_corpo.trim());
      var temAnexo = !!(r.anexos && r.anexos.length) || !!(r.tx_anexos && r.tx_anexos.trim());
      if (!temConteudo && !temAnexo) return false;
      if (status && r.cd_status !== status) return false;
      if (termo) {
        var alvo = ((r.tx_titulo || "") + " " + (r.tx_corpo || "") + " " + (r.no_aplicativo || "")).toLowerCase();
        if (alvo.indexOf(termo) === -1) return false;
      }
      return true;
    });
  }

  var filtradas = [];

  // A lista continua toda em memória (o filtro por texto/status precisa dela inteira), mas só a
  // página atual entra no DOM — várias telas com milhares de notas travavam o navegador.
  var paginador = UfdrDominio.criarPaginador({
    corpo: "#dados-corpo",
    depoisDe: "#dados-tabela",
    unidade: "nota(s)",
    render: renderLinha,
    aoDesenhar: function (qt) {
      $vazio.toggle(qt === 0);
      if (window.DestaqueRegistro) DestaqueRegistro.aplicar($corpo, DestaqueRegistro.idDaUrl());
    }
  });

  function aplicarFiltros() {
    filtradas = filtrar();
    paginador.definir(filtradas);
  }

  // A exportação automática lê o DOM (só a página atual). Instalar antes do auto-init do
  // exportar_excel.js troca o botão por um que exporta o filtro inteiro, não a página visível.
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "notas",
      dados: function () {
        return {
          colunas: ["Modificada em", "Aplicativo", "Título", "Corpo", "Status", "Anexos"],
          linhas: filtrar().map(function (r) {
            var anexos = (r.anexos && r.anexos.length)
              ? r.anexos.map(function (a) { return a.nome; }).join(", ")
              : (r.tx_anexos || "");
            return [formatarDataHora(r.dt_modificacao || r.dt_criacao), r.no_aplicativo || "",
                    r.tx_titulo || "", r.tx_corpo || "", r.cd_status || "", anexos];
          }),
        };
      },
    });
  }

  var filtroExtracao = idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : "";
  var linhasReq = $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/notas" + filtroExtracao);
  var contagensReq = $.Deferred();
  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (contagens) { contagensReq.resolve(contagens); });

  $.when(linhasReq, contagensReq).done(function (linhasResp, contagens) {
    linhas = linhasResp[0] || [];
    contagensEntidades = contagens || {};
    popularFiltroStatus();
    aplicarFiltros();
  }).fail(function () {
    // o texto sozinho deixa a pessoa parada; o botão re-tenta na hora
    $vazio.empty().text("Não foi possível carregar as notas.");
    $("<button>").attr("type", "button").addClass("btn-link").text("Tentar novamente")
      .on("click", function () { location.reload(); }).appendTo($vazio);
    $vazio.show();
    if (window.Toast) Toast.erro("Falha ao carregar as notas.");
  });

  $busca.on("input", aplicarFiltros);
  $status.on("change", aplicarFiltros);
})();
