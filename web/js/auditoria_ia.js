/* Auditoria de IA (tela Auditoria) — auditoria e administradores: lista os turnos de IA
 * registrados em investigacao.ia_tb04_auditoria (prompt, contexto, ferramentas, documentos, tempo,
 * tokens e custo). O card fica escondido e só aparece se o GET responder (perfil com pode_auditoria);
 * clicar numa linha expande o detalhe completo, carregado sob demanda. */
(function () {
  "use strict";

  var PAGINA = 50;

  var ORIGENS = {
    chat_inquerito: "Chat do inquérito",
    chat_caso: "Chat do caso",
    busca_automatica: "Busca automática",
    busca_documentos: "Busca em documentos",
    documento_analise: "Análise de documento",
    rif_analise_ia: "Reanálise de RIF"
  };

  var $card = $("#auditoria-card");
  var $corpo = $("#aud-corpo");
  var $vazio = $("#aud-vazio");
  var $filtroOrigem = $("#aud-filtro-origem");
  var $filtroUsuario = $("#aud-filtro-usuario");
  var $filtroInquerito = $("#aud-filtro-inquerito");
  var $filtroDataDe = $("#aud-filtro-data-de");
  var $filtroDataAte = $("#aud-filtro-data-ate");
  var $filtroOrdem = $("#aud-filtro-ordem");
  var $filtroFeedback = $("#aud-filtro-feedback");
  var $atualizarBtn = $("#aud-atualizar-btn");
  var $paginacao = $("#aud-paginacao");
  var $pagInfo = $("#aud-pag-info");
  var $pagAnterior = $("#aud-pag-anterior");
  var $pagProximo = $("#aud-pag-proximo");

  var estado = { pagina: 1, total: 0 };

  function totalPaginas() { return Math.max(1, Math.ceil(estado.total / PAGINA)); }

  var TZ = "America/Sao_Paulo";

  /* DT_CHAMADA é TIMESTAMP sem fuso, gravado com now() em UTC. Sem o "Z" o browser lê o ISO
   * como hora local e a chamada aparece 3 h adiantada; e mesmo com o Z, sem timeZone fixo o
   * horário mudaria conforme a máquina de quem lê — numa auditoria isso não pode acontecer.
   * Mesmo padrão das demais telas (ver js/acessos.js). */
  function fmtData(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return d.toLocaleDateString("pt-BR", { timeZone: TZ }) + " " +
      d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  }

  function fmtTokens(item) {
    var prefixo = item.in_tokens_estimados ? "~" : "";
    var entrada = Number(item.qt_tokens_entrada || 0);
    var saida = Number(item.qt_tokens_saida || 0);
    if (!entrada && !saida) return "—";
    return prefixo + entrada.toLocaleString("pt-BR") + " / " + saida.toLocaleString("pt-BR");
  }

  function fmtCusto(valor) {
    if (valor === null || valor === undefined) return "—";
    return "US$ " + Number(valor).toFixed(4);
  }

  function fmtTempo(ms) {
    if (ms === null || ms === undefined) return "—";
    var s = Number(ms) / 1000;
    if (s < 60) return s.toFixed(1) + " s";
    return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  }

  // Votos 👍/👎 na resposta do turno (só o chat do inquérito tem voto): "—" quando ninguém
  // avaliou ou a origem nem admite voto — o filtro "Sem avaliação" só lista o 1º caso.
  function fmtFeedback(item) {
    var pos = Number(item.qt_feedback_pos || 0);
    var neg = Number(item.qt_feedback_neg || 0);
    if (!pos && !neg) return "—";
    return "👍 " + pos + " · 👎 " + neg;
  }

  function renderKpis(totais, qt) {
    $("#aud-kpi-total").text(qt.toLocaleString("pt-BR"));
    $("#aud-kpi-tokens-entrada").text(Number(totais.qt_tokens_entrada || 0).toLocaleString("pt-BR"));
    $("#aud-kpi-tokens-saida").text(Number(totais.qt_tokens_saida || 0).toLocaleString("pt-BR"));
    $("#aud-kpi-custo").text(fmtCusto(totais.vl_custo_usd));
    $("#aud-kpi-tempo").text(fmtTempo(totais.nu_tempo_ms));
    $("#aud-kpi-feedback-pos").text(Number(totais.qt_feedback_pos || 0).toLocaleString("pt-BR"));
    $("#aud-kpi-feedback-neg").text(Number(totais.qt_feedback_neg || 0).toLocaleString("pt-BR"));
  }

  function linhaDetalhe(item, colunas) {
    var $tr = $("<tr>").addClass("aud-detalhe");
    var $td = $("<td>").attr("colspan", colunas).css({ padding: "10px 14px" }).appendTo($tr);
    $td.append($("<p>").addClass("card-hint").text("Carregando detalhe…"));

    $.ajax({ url: "/api/admin/ia/auditoria/" + item.id_auditoria, method: "GET" })
      .done(function (det) {
        $td.empty();

        function bloco(titulo, conteudo, pre) {
          if (conteudo === null || conteudo === undefined || conteudo === "") return;
          $td.append($("<p>").css({ margin: "8px 0 2px", fontWeight: "600" }).text(titulo));
          var $c = $(pre ? "<pre>" : "<p>").css({
            margin: 0, maxHeight: "220px", overflow: "auto", whiteSpace: "pre-wrap",
            wordBreak: "break-word", fontSize: "12px"
          }).text(conteudo);
          $td.append($c);
        }

        var referencia = [];
        if (det.id_inquerito) referencia.push("inquérito " + det.id_inquerito);
        if (det.id_conversa_ia) referencia.push("conversa " + det.id_conversa_ia);
        bloco("Referência", referencia.join(" · ") || "—");
        bloco("Prompt enviado", det.tx_prompt, true);

        var rotuloContexto = "Contexto utilizado (prompt de sistema)";
        if (det.qt_chars_contexto > (det.tx_contexto || "").length) {
          rotuloContexto += " — truncado, tamanho real: " + Number(det.qt_chars_contexto).toLocaleString("pt-BR") + " caracteres";
        }
        bloco(rotuloContexto, det.tx_contexto, true);

        var ferramentas = det.tx_ferramentas || [];
        if (ferramentas.length) {
          bloco("Ferramentas chamadas (" + ferramentas.length + ")", ferramentas.map(function (f) {
            return f.ferramenta + " " + JSON.stringify(f.argumentos || {});
          }).join("\n"), true);
        }

        var documentos = det.tx_documentos || [];
        if (documentos.length) {
          bloco("Documentos consultados (" + documentos.length + ")", documentos.map(function (d) {
            return JSON.stringify(d);
          }).join("\n"), true);
        }

        // Avaliação da resposta deste turno (só chat do inquérito): contadores + quem votou.
        // Turno de outra origem, ou sem voto, não ganha o bloco — igual à bolha do chat.
        var fbPos = Number(det.qt_feedback_pos || 0);
        var fbNeg = Number(det.qt_feedback_neg || 0);
        if (fbPos || fbNeg) {
          $td.append($("<p>").css({ margin: "8px 0 2px", fontWeight: "600" })
            .text("Avaliação da resposta (👍 " + fbPos + " · 👎 " + fbNeg + ")"));
          var votos = det.feedbacks || [];
          if (votos.length) {
            var $ul = $("<ul>").css({ margin: "0", paddingLeft: "18px", fontSize: "12px" });
            votos.forEach(function (v) {
              var icone = v.tp_feedback === "positivo" ? "👍" : (v.tp_feedback === "negativo" ? "👎" : "·");
              var quando = v.dt_criacao ? (" — " + fmtData(v.dt_criacao)) : "";
              $ul.append($("<li>").text(icone + " " + (v.no_usuario || "usuário") + quando));
            });
            $td.append($ul);
          }
        }

        if (det.cd_status === "erro") bloco("Erro", det.tx_erro, true);
      })
      .fail(function () {
        $td.empty().append($("<p>").addClass("card-hint").text("Erro ao carregar o detalhe."));
      });

    return $tr;
  }

  function renderLinha(item) {
    var $tr = $("<tr>").css("cursor", "pointer").attr("title", "Clique para ver o detalhe");
    $("<td>").addClass("col-nome").text(fmtData(item.dt_chamada)).appendTo($tr);
    $("<td>").addClass("col-nome").text(ORIGENS[item.no_origem] || item.no_origem).appendTo($tr);
    $("<td>").addClass("col-nome").text(item.no_usuario || "—").appendTo($tr);
    $("<td>").addClass("col-nome").text(item.no_modelo || "—").appendTo($tr);
    $("<td>").addClass("col-nome").css({ maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" })
      .text(item.tx_prompt_resumo || "—").appendTo($tr);
    $("<td>").addClass("col-nome").text(item.qt_chamadas_api + " / " + item.qt_ferramentas).appendTo($tr);
    $("<td>").addClass("col-nome").text(fmtTokens(item)).appendTo($tr);
    $("<td>").addClass("col-nome").text(fmtCusto(item.vl_custo_usd)).appendTo($tr);
    $("<td>").addClass("col-nome").text(fmtTempo(item.nu_tempo_ms)).appendTo($tr);
    $("<td>").addClass("col-nome").attr("title", "Votos 👍/👎 de investigadores na resposta deste turno").text(fmtFeedback(item)).appendTo($tr);
    $("<td>").addClass("col-nome").append(
      $("<span>").addClass("badge " + (item.cd_status === "ok" ? "badge-ok" : "badge-erro")).text(item.cd_status)
    ).appendTo($tr);

    $tr.on("click", function () {
      var $prox = $tr.next(".aud-detalhe");
      if ($prox.length) {
        $prox.remove();
        return;
      }
      $tr.after(linhaDetalhe(item, $tr.children().length));
    });
    return $tr;
  }

  function renderPaginacao() {
    var tp = totalPaginas();
    $pagInfo.text("Página " + estado.pagina + " de " + tp.toLocaleString("pt-BR") +
      " · " + estado.total.toLocaleString("pt-BR") + " registro(s)");
    $pagAnterior.prop("disabled", estado.pagina <= 1);
    $pagProximo.prop("disabled", estado.pagina >= tp);
    $paginacao.css("display", estado.total > PAGINA ? "flex" : "none");
  }

  function carregar(resetarPagina) {
    if (resetarPagina) estado.pagina = 1;
    var params = { limite: PAGINA, deslocamento: (estado.pagina - 1) * PAGINA };
    var origem = $filtroOrigem.val();
    if (origem) params.origem = origem;
    var idUsuario = $filtroUsuario.val();
    if (idUsuario) params.id_usuario = idUsuario;
    var idInquerito = $filtroInquerito.val();
    if (idInquerito) params.id_inquerito = idInquerito;
    if ($filtroDataDe.val()) params.data_de = $filtroDataDe.val();
    if ($filtroDataAte.val()) params.data_ate = $filtroDataAte.val();
    var ordem = $filtroOrdem.val();
    if (ordem) params.ordenar = ordem;
    var feedback = $filtroFeedback.val();
    if (feedback) params.feedback = feedback;

    $.ajax({ url: "/api/admin/ia/auditoria", method: "GET", data: params })
      .done(function (dados) {
        $card.removeClass("hidden");
        $corpo.empty();
        (dados.itens || []).forEach(function (item) {
          $corpo.append(renderLinha(item));
        });
        estado.total = dados.qt_total || 0;
        renderKpis(dados.totais || {}, estado.total);
        renderPaginacao();
        $vazio.toggleClass("hidden", estado.total > 0);
      });
    // .fail silencioso de propósito: usuário sem permissão de admin (403) só não vê o card
  }

  // Seletor de usuário: só usuários ativos e aprovados aparecem (mesma origem do seletor de
  // permissões); registros órfãos continuam acessíveis no filtro "Todos os usuários".
  function popularUsuarios() {
    $.ajax({ url: "/api/investigacao/usuarios-basico", method: "GET" })
      .done(function (usuarios) {
        (usuarios || []).forEach(function (u) {
          $filtroUsuario.append($("<option>").val(u.id_usuario).text(u.no_usuario + (u.no_login ? " (" + u.no_login + ")" : "")));
        });
      });
    // .fail silencioso de propósito: 403/erro só deixa o dropdown com "Todos os usuários"
  }

  // Seletor de inquérito: admin vê todos os inquéritos existentes; os apagados não aparecem,
  // mas os registros deles continuam acessíveis no filtro "Todos os inquéritos".
  function popularInqueritos() {
    $.ajax({ url: "/api/investigacao/inqueritos", method: "GET" })
      .done(function (inqueritos) {
        (inqueritos || []).forEach(function (i) {
          $filtroInquerito.append($("<option>").val(i.id_inquerito).text(i.nu_inquerito || ("#" + i.id_inquerito)));
        });
      });
    // .fail silencioso de propósito: 403/erro só deixa o dropdown com "Todos os inquéritos"
  }

  $atualizarBtn.on("click", function () { carregar(true); });
  $filtroOrigem.on("change", function () { carregar(true); });
  $filtroUsuario.on("change", function () { carregar(true); });
  $filtroInquerito.on("change", function () { carregar(true); });
  $filtroDataDe.on("change", function () { carregar(true); });
  $filtroDataAte.on("change", function () { carregar(true); });
  $filtroOrdem.on("change", function () { carregar(true); });
  $filtroFeedback.on("change", function () { carregar(true); });
  $pagAnterior.on("click", function () {
    if (estado.pagina > 1) { estado.pagina--; carregar(false); }
  });
  $pagProximo.on("click", function () {
    if (estado.pagina < totalPaginas()) { estado.pagina++; carregar(false); }
  });

  popularUsuarios();
  popularInqueritos();
  carregar(true);
})();
