/* Encontros por antena — quem estava com quem, onde e quando.
 *
 * Backend: GET .../telefonia/encontros (pares na mesma ERB dentro da janela) e
 * GET .../telefonia/ancoras (residência/trabalho provável por linha). Aqui é só filtros,
 * busca local e desenho das duas tabelas. Antena é região, não posição — o aviso fixo da
 * página vale para as duas tabelas.
 */
(function () {
  "use strict";

  var idInquerito = AnalyserUtils.paramUrl("inquerito") || "";
  var encontros = [];
  var ancoras = [];

  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function formatarNumero(n) { return num(n).toLocaleString("pt-BR"); }
  function formatarDataHora(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?/);
    if (!m) return String(iso);
    return m[3] + "/" + m[2] + "/" + m[1] + " " + m[4] + ":" + m[5];
  }
  function formatarLinha(nu) {
    var s = String(nu || "").replace(/\D/g, "");
    if (s.length === 13 && s.indexOf("55") === 0) s = s.slice(2);
    if (s.length === 11) return "(" + s.slice(0, 2) + ") " + s.slice(2, 7) + "-" + s.slice(7);
    if (s.length === 10) return "(" + s.slice(0, 2) + ") " + s.slice(2, 6) + "-" + s.slice(6);
    return s || "—";
  }
  function antenaCurta(l) {
    var lugar = [l.tx_endereco, [l.no_municipio, l.sg_uf].filter(Boolean).join("/")].filter(Boolean).join(" — ");
    return { cgi: txt(l.cgi), lugar: lugar };
  }

  function temCoord(lat, lon) {
    return lat !== null && lat !== undefined && lat !== "" &&
           lon !== null && lon !== undefined && lon !== "";
  }

  function coordCurta(lat, lon) {
    return Number(lat).toFixed(5) + ", " + Number(lon).toFixed(5);
  }

  /* Endereço + coordenadas + link de mapa. Sem coordenada não há link: a ERB sem
     lat/long é cadastro da operadora ("CADASTRO NAO ENCONTRADO"), não ponto no mapa. */
  function celulaLocal($tr, endereco, municipio, uf, lat, lon) {
    var $td = $("<td>").addClass("col-nome").appendTo($tr);
    var lugar = [endereco, [municipio, uf].filter(Boolean).join("/")].filter(Boolean).join(" — ");
    if (lugar) $("<div>").text(lugar).appendTo($td);
    if (temCoord(lat, lon)) {
      $("<div>").addClass("enc-mono").css("opacity", ".8").text(coordCurta(lat, lon)).appendTo($td);
      $("<a>")
        .attr({ href: "https://www.google.com/maps?q=" + encodeURIComponent(Number(lat) + "," + Number(lon)),
                target: "_blank", rel: "noopener" })
        .text("🗺 Ver no mapa")
        .appendTo($td);
    } else if (!lugar) {
      $td.text("—");
    }
    return $td;
  }

  function renderCobertura(cobertura) {
    var c = cobertura || {};
    var tem = (c.qt_chamadas_com_cgi !== undefined);
    $("#enc-cobertura").toggleClass("hidden", !tem);
    if (!tem) return;
    var partes = [
      "CDR: " + formatarNumero(c.qt_chamadas_com_cgi) + " de " + formatarNumero(c.qt_chamadas) +
        " chamadas com antena",
      "dados móveis: " + formatarNumero(c.qt_dados_com_cgi) + " de " + formatarNumero(c.qt_dados) +
        " sessões com antena",
      formatarNumero(c.qt_erbs_no_mapa) + " ERBs com coordenada"
    ];
    var semAntena = !num(c.qt_chamadas_com_cgi) && !num(c.qt_dados_com_cgi);
    $("#enc-cobertura")
      .text("Cobertura do material — " + partes.join(" · ") + "." +
        (semAntena ? " Sem antena no CDR não há encontro nem âncora que se apure." : ""));
  }

  function renderEncontros() {
    var busca = ($("#f-busca").val() || "").toLowerCase().replace(/[\s()-]/g, "");
    var $corpo = $("#corpo-encontros").empty();
    var lista = encontros.filter(function (l) {
      if (!busca) return true;
      return String(l.linha_a || "").replace(/\D/g, "").indexOf(busca) >= 0 ||
        String(l.linha_b || "").replace(/\D/g, "").indexOf(busca) >= 0 ||
        String(l.cgi || "").toLowerCase().indexOf(busca) >= 0;
    });
    lista.forEach(function (l) {
      var a = antenaCurta(l);
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome enc-mono").text(formatarLinha(l.linha_a)).appendTo($tr);
      $("<td>").addClass("col-nome enc-mono").text(formatarLinha(l.linha_b)).appendTo($tr);
      var $ant = $("<td>").addClass("col-nome").appendTo($tr);
      $("<div>").addClass("enc-mono").text(a.cgi).appendTo($ant);
      if (a.lugar) $("<div>").css("opacity", ".8").text(a.lugar).appendTo($ant);
      celulaLocal($tr, l.tx_endereco, l.no_municipio, l.sg_uf, l.vl_latitude, l.vl_longitude);
      $("<td>").addClass("col-nome").css("text-align", "right").text(formatarNumero(l.qt_encontros)).appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarDataHora(l.dt_primeiro)).appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarDataHora(l.dt_ultimo)).appendTo($tr);
      $corpo.append($tr);
    });
    $("#enc-vazio").toggleClass("hidden", lista.length > 0);
    $("#total-registros").text(formatarNumero(lista.length) + " pares");
  }

  /* Linhas sem nenhuma âncora saem da tabela (viraria um mar de "—"): a contagem
     abaixo diz quantas foram avaliadas, e a cobertura no topo explica o vazio. */
  function renderAncoras() {
    var $corpo = $("#corpo-ancoras").empty();
    var comAncora = ancoras.filter(function (l) { return l.cgi_residencia || l.cgi_trabalho; });
    comAncora.forEach(function (l) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome enc-mono").text(formatarLinha(l.linha)).appendTo($tr);
      var $res = $("<td>").addClass("col-nome").appendTo($tr);
      if (l.cgi_residencia) {
        $("<div>").addClass("enc-mono").text(l.cgi_residencia).appendTo($res);
        var lugar = [l.endereco_residencia, [l.municipio_residencia, l.uf_residencia].filter(Boolean).join("/")].filter(Boolean).join(" — ");
        if (lugar) $("<div>").css("opacity", ".8").text(lugar).appendTo($res);
        if (temCoord(l.lat_residencia, l.lon_residencia)) {
          $("<div>").addClass("enc-mono").css("opacity", ".8")
            .text(coordCurta(l.lat_residencia, l.lon_residencia)).appendTo($res);
          $("<a>")
            .attr({ href: "https://www.google.com/maps?q=" +
                    encodeURIComponent(Number(l.lat_residencia) + "," + Number(l.lon_residencia)),
                    target: "_blank", rel: "noopener" })
            .text("🗺 Ver no mapa")
            .appendTo($res);
        }
      } else { $res.text("—"); }
      $("<td>").addClass("col-nome").css("text-align", "right")
        .text(l.qt_noites ? formatarNumero(l.qt_noites) + " noites" : "—").appendTo($tr);
      var $tra = $("<td>").addClass("col-nome").appendTo($tr);
      if (l.cgi_trabalho) {
        $("<div>").addClass("enc-mono").text(l.cgi_trabalho).appendTo($tra);
        var lugarT = [l.endereco_trabalho, [l.municipio_trabalho, l.uf_trabalho].filter(Boolean).join("/")].filter(Boolean).join(" — ");
        if (lugarT) $("<div>").css("opacity", ".8").text(lugarT).appendTo($tra);
        if (temCoord(l.lat_trabalho, l.lon_trabalho)) {
          $("<div>").addClass("enc-mono").css("opacity", ".8")
            .text(coordCurta(l.lat_trabalho, l.lon_trabalho)).appendTo($tra);
          $("<a>")
            .attr({ href: "https://www.google.com/maps?q=" +
                    encodeURIComponent(Number(l.lat_trabalho) + "," + Number(l.lon_trabalho)),
                    target: "_blank", rel: "noopener" })
            .text("🗺 Ver no mapa")
            .appendTo($tra);
        }
      } else { $tra.text("—"); }
      $("<td>").addClass("col-nome").css("text-align", "right")
        .text(l.qt_dias_uteis ? formatarNumero(l.qt_dias_uteis) + " dias" : "—").appendTo($tr);
      $corpo.append($tr);
    });
    $("#ancoras-contagem").text(
      ancoras.length
        ? comAncora.length + " de " + ancoras.length + " linhas avaliadas com rotina detectada."
        : "");
    if (!comAncora.length) {
      $("<tr>").append(
        $("<td>").addClass("col-nome").attr("colspan", 5)
          .css("opacity", ".7")
          .text("Nenhuma linha com 3+ dias de rotina na mesma antena. Confira a cobertura do material no topo da página.")
      ).appendTo($corpo);
    }
  }

  function carregar() {
    if (!idInquerito) return;
    var params = {
      janela_min: $("#f-janela").val() || 30,
      min_encontros: $("#f-min").val() || 2,
      data_de: $("#f-de").val() || undefined,
      data_ate: $("#f-ate").val() || undefined
    };
    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/telefonia/encontros", params)
      .done(function (resp) {
        encontros = (resp && resp.encontros) || [];
        renderEncontros();
        renderCobertura(resp && resp.cobertura);
      })
      .fail(function () {
        encontros = [];
        renderEncontros();
        $("#enc-vazio").removeClass("hidden").text("Erro ao carregar os encontros.");
      });
    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/telefonia/ancoras")
      .done(function (resp) {
        ancoras = (resp && resp.ancoras) || [];
        renderAncoras();
      });
  }

  $("#btn-aplicar").on("click", carregar);
  $("#f-busca").on("input", renderEncontros);

  carregar();
})();
