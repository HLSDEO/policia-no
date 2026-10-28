/* Administração → Servidores (nós): CRUD dos nós descentralizados.

 O fluxo que esta tela fecha é o da instalação de uma delegacia: o admin cadastra o nó (nome,
 delegacia, URL), a API devolve o token UMA vez (modal "guarde agora"), e o instalador cola esse
 token no .env do nó. Daí em diante esta tela é observação: online/offline pelo batimento,
 versão reportada, botão Testar (o central bate no /no/saude do nó) e as duas operações que
 mudam o mundo do nó — inativar (delegacia volta a processar no central na hora) e trocar o
 token (revoga o nó: ele para de autenticar até ser reconfigurado). */
(function () {
  "use strict";

  var $corpo = $("#nos-corpo");
  var $vazio = $("#nos-vazio");

  function formatarData(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"))
      .toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // "há 2 min" na coluna, data completa no title — com a tela aberta 8h por dia, "14/10 19:40"
  // parado na tela vira ruído; o que importa é a idade do último batimento
  function tempoRelativo(iso) {
    if (!iso) return "nunca";
    var t = new Date(String(iso).replace(/^(\d{4}-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z")).getTime();
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return "há " + s + " s";
    if (s < 3600) return "há " + Math.floor(s / 60) + " min";
    if (s < 86400) return "há " + Math.floor(s / 3600) + " h";
    return "há " + Math.floor(s / 86400) + " d";
  }

  function versaoMenor(a, b) {  // "4.6.5" < "4.6.14" (numérica por segmento, não lexicográfica)
    var pa = String(a || "").split("."), pb = String(b || "").split(".");
    for (var i = 0; i < Math.max(pa.length, pb.length); i++) {
      var x = parseInt(pa[i] || "0", 10), y = parseInt(pb[i] || "0", 10);
      if (x !== y) return x < y;
    }
    return false;
  }

  function mostrarToken(titulo, aviso, token) {
    $("#token-titulo").text(titulo);
    $("#token-aviso").text(aviso);
    $("#token-valor").val(token);
    $("#modal-token").removeClass("hidden");
  }

  $("#token-fechar").on("click", function () { $("#modal-token").addClass("hidden"); });
  $("#token-copiar").on("click", function () {
    var campo = document.getElementById("token-valor");
    campo.select();
    try { document.execCommand("copy"); window.Toast.sucesso("Token copiado."); }
    catch (e) { window.Toast.aviso("Copie manualmente: o navegador bloqueou a cópia."); }
  });

  var nosCache = [];  // a lista do último carregar — alimenta o mapa "delegacia já está em outro nó"
  var versaoCentral = "";  // vem de /api/sistema/versao uma vez; colore a coluna Versão

  $.getJSON("/api/sistema/versao").done(function (r) {
    versaoCentral = String((r && r.instalada) || "").trim();
  });

  function carregar() {
    $.getJSON("/api/servidores").done(function (lista) {
      nosCache = lista;
      $corpo.empty();
      $vazio.toggle(!lista.length);
      lista.forEach(function (d) { $corpo.append(montarLinha(d)); });
      atualizarFilas(lista);
      aplicarBuscaNos();  // a listagem é recriada — o filtro em curso precisa ser reaplicado
    }).fail(function () {
      window.Toast.erro("Não foi possível listar os servidores.");
    });
  }

  /* ---------- "Verificar": release mais novo + nós atrás dele ---------- */

  $("#verificar-btn").on("click", function () {
    var $btn = $(this).prop("disabled", true);
    var $st = $("#verificar-status").show().text("Consultando o GitHub (~10 s)…");
    $.post("/api/servidores/verificar").done(function (r) {
      var txt = "Central em " + (r.central || "?") + " · ";
      if (r.erro && !r.versao_disponivel) {
        $st.text(txt + "não foi possível consultar o GitHub: " + r.erro);
      } else {
        txt += r.versao_disponivel ? "release mais novo: " + r.versao_disponivel : "GitHub indisponível (usei o último cache)";
        if (r.versao_disponivel && versaoMenor(r.central, r.versao_disponivel)) {
          txt += " — há release novo; atualize o central primeiro, depois os nós.";
        } else if ((r.nos_atrasados || []).length) {
          txt += " — nós atrás: " + r.nos_atrasados.join(", ") + ".";
        } else {
          txt += " — tudo em dia.";
        }
        if (r.erro) txt += " (" + r.erro + ")";
        $st.text(txt);
      }
    }).fail(function (xhr) {
      $st.text(((xhr.responseJSON || {}).detail) || "Falha ao verificar.");
    }).always(function () { $btn.prop("disabled", false); });
  });

  /* ---------- "Atualizar todos": rollout em levas (job global 'rollout_nos') ---------- */

  var rolloutTimer = null;

  function pintarRollout(job) {
    var $st = $("#verificar-status").show();
    if (!job || !job.cd_status) { $st.hide(); return; }
    var m = job.tx_metricas || {};
    var txt;
    if (job.cd_status === "processando" || job.cd_status === "pendente") {
      txt = "Atualizando os nós em levas: " + (job.qt_processados || 0) + "/" + (job.qt_total || "?") +
            " (ok: " + ((m.ok || []).length) + ", falhas: " + Object.keys(m.falhas || {}).length + ").";
    } else if (job.cd_status === "concluido") {
      var fk = Object.keys(m.falhas || {});
      txt = "Rollout concluído: " + ((m.ok || []).length) + " nó(s) atualizado(s)" +
            (fk.length ? "; falharam: " + fk.join(", ") : ".");
    } else if (job.cd_status === "cancelado") {
      txt = "Rollout cancelado.";
    } else {
      txt = "Rollout falhou: " + (job.tx_erro || "veja a fila de jobs");
    }
    $st.text(txt);
  }

  function acompanharRollout() {
    $.get("/api/servidores/atualizacao-todos").done(function (job) {
      pintarRollout(job);
      var vivo = job && (job.cd_status === "processando" || job.cd_status === "pendente");
      $("#atualizar-todos-btn").prop("disabled", !!vivo);
      if (vivo && !rolloutTimer) {
        rolloutTimer = setTimeout(function () {
          rolloutTimer = null;
          acompanharRollout();
        }, 10000);
      } else if (!vivo) {
        carregar();  // o rollout mexeu nas versões — a tabela volta a pintar do central
      }
    });
  }

  $("#atualizar-todos-btn").on("click", function () {
    var $btn = $(this).prop("disabled", true);
    $.ajax({
      url: "/api/servidores/atualizar-todos",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ max_parallel: 3 })
    }).done(function () { acompanharRollout(); })
      .fail(function (xhr) {
        $btn.prop("disabled", false);
        $("#verificar-status").show().text(((xhr.responseJSON || {}).detail) || "Falha ao iniciar o rollout.");
      });
  });
  acompanharRollout();  // reabrir a tela no meio de um rollout retoma o painel

  /* ---------- busca na tabela (nome, delegacia, URL) ---------- */

  function aplicarBuscaNos() {
    var termo = String($("#no-busca").val() || "").trim().toUpperCase();
    $corpo.children("tr").each(function () {
      var ok = !termo || String($(this).data("busca")).indexOf(termo) >= 0;
      $(this).toggle(ok);
    });
  }
  $("#no-busca").on("input", window.AnalyserUtils.debounce(aplicarBuscaNos, 150));

  /* ---------- auto-refresh: a tela fica aberta o dia todo; o ponto verde não pode congelar ---------- */

  setInterval(function () {
    // modal de ação aberto = alguém preenchendo formulário; re-render por cima perderia tudo
    if ($(".modal-overlay:not(.hidden)").length || document.hidden) return;
    carregar();
  }, 30000);

  /* ---------- badge da última atualização na linha (vem do /estado, arquivo do agente do nó) ---------- */

  function badgeAtualizacao(d, atualizacao) {
    var $b = $corpo.find('.no-fila[data-id="' + d.id_servidor + '"]').closest("tr").find(".no-atual");
    if (!$b.length || !atualizacao) return;
    if (atualizacao.rodando) {
      $b.text("⟳ atualizando…").css("color", "inherit").attr("title", "");
      return;
    }
    var r = atualizacao.resultado;
    if (!r) { $b.text("").attr("title", ""); return; }
    if (r.ok) {
      $b.text("✓ " + (r.versao || "?")).css("color", "#2e7d32")
        .attr("title", "atualizado com sucesso" + (r.ts ? " em " + formatarData(r.ts) : ""));
    } else {
      $b.text("✗ atualização falhou").css("color", "#c62828")
        .attr("title", String(r.erro || "veja o log no botão Atualizar"));
    }
  }

  /* ---------- coluna "Fila": ocupadas/limite do nó, preenchida sem bloquear a listagem ---------- */

  function atualizarFilas(lista) {
    (lista || []).forEach(function (d) {
      if (!d.in_ativa) return;
      var $cel = $corpo.find(".no-fila[data-id=" + d.id_servidor + "]");
      $.getJSON("/api/servidores/" + d.id_servidor + "/estado").done(function (e) {
        var f = e.fila || {};
        // o que vale é o teto do PROCESSO (limite_processo); "limite" é o do .env e pode esperar
        // um restart — quando os dois divergem, mostra a flechinha para o valor novo
        var teto = f.limite_processo || f.limite || "?";
        var txt = (f.ocupadas || 0) + "/" + teto;
        if (f.esperando) txt += " (+" + f.esperando + " na fila)";
        if (f.limite && teto !== f.limite) txt += " →" + f.limite;
        $cel.text(txt).css("color", f.ocupadas >= teto ? "#c62828" : "inherit");
        badgeAtualizacao(d, e.atualizacao);
      }).fail(function () { $cel.text("—"); });
    });
  }

  /* ---------- modal "Estado": barras de CPU/memória/disco + fila, atualizado a cada 5 s ---------- */

  var pollEstado = null;

  function pararEstado() { if (pollEstado) { clearInterval(pollEstado); pollEstado = null; } }

  function abrirEstado(d) {
    pararEstado();
    $("#estado-titulo").text("Estado do nó — " + d.no_servidor);
    $("#estado-status").text("Consultando…");
    $("#estado-barras").empty();
    $("#estado-fila").text("—");
    $("#estado-trabalhos").empty();
    $("#modal-estado").removeClass("hidden");
    var carregarUmaVez = function () {
      $.getJSON("/api/servidores/" + d.id_servidor + "/estado").done(function (e) {
        $("#estado-status").text("Versão " + (e.versao || "?") + " · ligado há " + formatarDuração(e.uptime_segundos));
        renderBarras(e);
        renderFilaEstado(e.fila || {});
      }).fail(function () {
        $("#estado-status").text("O nó não respondeu — offline?");
      });
    };
    carregarUmaVez();
    pollEstado = setInterval(carregarUmaVez, 5000);
  }

  $("#estado-fechar").on("click", function () { pararEstado(); $("#modal-estado").addClass("hidden"); });

  function formatarDuração(seg) {
    if (seg == null) return "—";
    var d = Math.floor(seg / 86400), h = Math.floor((seg % 86400) / 3600), m = Math.floor((seg % 3600) / 60);
    return (d ? d + "d " : "") + h + "h " + m + "min";
  }

  function formatarBytes(b) {
    if (b == null) return "—";
    var unidades = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (b >= 1024 && i < unidades.length - 1) { b /= 1024; i += 1; }
    return (i ? b.toFixed(1) : b) + " " + unidades[i];
  }

  function barra(rotulo, percentual, detalhe) {
    var cor = percentual >= 90 ? "#c62828" : percentual >= 75 ? "#ef6c00" : "#2e7d32";
    var $l = $("<div>");
    $("<div>").css({ fontSize: "12px", marginBottom: "2px" })
      .text(rotulo + " — " + Math.round(percentual) + "%  " + detalhe).appendTo($l);
    var $fundo = $("<div>").css({ background: "rgba(128,128,128,.2)", borderRadius: "6px", height: "10px" });
    $("<div>").css({ width: Math.max(2, Math.min(100, percentual)) + "%", background: cor,
      height: "10px", borderRadius: "6px" }).appendTo($fundo);
    return $l.append($fundo);
  }

  function renderBarras(e) {
    var $b = $("#estado-barras").empty();
    $b.append(barra("CPU (" + (e.cpu_cores || "?") + " núcleos)", e.cpu_percent || 0, ""));
    var mem = e.memoria || {};
    $b.append(barra("Memória", mem.percentual || 0, formatarBytes(mem.usado) + " de " + formatarBytes(mem.total)));
    (e.discos || []).forEach(function (d) {
      if (d.erro) { $b.append(barra(d.rotulo, 100, "inacessível")); return; }
      $b.append(barra(d.rotulo, d.percentual || 0,
        formatarBytes(d.usado) + " de " + formatarBytes(d.total)));
    });
    // TLS da mídia direta (docs/no_midia_direta.md): o certificado que vence em silêncio é a
    // pane que volta a "mídia abrindo pelo central" — melhor ver aqui do que deduzir na tela
    var m = e.midia;
    if (m) {
      var txt = m.certificado
        ? (m.dias_restantes == null ? "mídia direta: certificado presente (validade ilegível)"
           : m.dias_restantes < 0 ? "mídia direta: certificado VENCIDO há " + (-m.dias_restantes) + " dias"
           : "mídia direta: certificado válido por mais " + m.dias_restantes + " dias")
        : "mídia direta: sem certificado em nginx/ssl (o no-web está parado)";
      var cor = !m.certificado || (m.dias_restantes != null && m.dias_restantes < 30) ? "#ef6c00" : "#2e7d32";
      $("<div>").css({ "font-size": "12px", color: cor, "margin-top": "6px" }).text(txt).appendTo($b);
    }
  }

  function renderFilaEstado(f) {
    var txt = (f.ocupadas || 0) + " de " + (f.limite_processo || f.limite || "?") + " vagas em uso";
    if (f.esperando) txt += " — " + f.esperando + " aguardando vaga";
    if (f.limite && f.limite_processo && f.limite !== f.limite_processo) {
      txt += " (o .env pede " + f.limite + " — reinicie o nó para valer)";
    }
    $("#estado-fila").text(txt);
    var $t = $("#estado-trabalhos").empty();
    (f.trabalhos || []).forEach(function (t) {
      $("<div>").css("padding", "2px 0")
        .text("#" + t.id_extracao + "  " + (t.no_arquivo || "")).appendTo($t);
    });
  }

  function montarLinha(d) {
    var $tr = $("<tr>");
    $("<td>").append($("<span>").addClass("arq-tag").text(d.no_servidor)).appendTo($tr);
    var $deleg = $("<td>").appendTo($tr);
    var delegs = d.delegacias || [];
    if (!delegs.length) {
      $deleg.text("—");
    } else {
      delegs.forEach(function (x) {
        $("<div>").text(x.no_delegacia + (x.uf ? " (" + x.uf + ")" : "")).appendTo($deleg);
      });
    }
    $("<td>").append($("<code>").text(d.tx_url_base)).appendTo($tr);

    var $status = $("<td>").appendTo($tr);
    if (!d.in_ativa) {
      $("<span>").css({ color: "inherit", opacity: 0.6 }).text("inativo").appendTo($status);
    } else if (d.online) {
      $("<span>").css({ color: "#2e7d32", fontWeight: "600" }).text("● online").appendTo($status);
    } else {
      $("<span>").css({ color: "#c62828" }).text("○ offline").appendTo($status);
    }

    var $celVersao = $("<td>").css("font-family", "monospace").appendTo($tr);
    var $versao = $("<div>").text(d.tx_versao || "—").appendTo($celVersao);
    if (versaoCentral && d.tx_versao && versaoMenor(d.tx_versao, versaoCentral)) {
      $versao.css({ color: "#c62828", fontWeight: "600" })
        .attr("title", "o central está em " + versaoCentral + " — clique em Atualizar");
    }
    $("<div>").addClass("no-atual").css({ "font-size": "11px", opacity: 0.75 }).appendTo($celVersao);

    var $fila = $("<td>").text("…").addClass("no-fila").data("id", d.id_servidor)
      .css("font-family", "monospace").appendTo($tr);
    var $contato = $("<td>")
      .text(d.dt_ultimo_heartbeat ? tempoRelativo(d.dt_ultimo_heartbeat) : "nunca")
      .attr("title", d.dt_ultimo_heartbeat ? formatarData(d.dt_ultimo_heartbeat) : "")
      .appendTo($tr);

    var $acoes = $("<td>").appendTo($tr);
    [
      ["Testar", "btn-secondary", function () { testarNo(d); }],
      ["Estado", "btn-secondary", function () { abrirEstado(d); }],
      ["Desempenho", "btn-secondary", function () { abrirConfig(d); }],
      ["Atualizar", "btn-primary", function () { atualizarNo(d); }]
    ].forEach(function (b) {
      $("<button>").addClass("btn " + b[1])
        .css({ padding: "6px 10px", fontSize: "12px", marginRight: "6px" })
        .text(b[0]).on("click", b[2]).appendTo($acoes);
    });
    // as quatro ações raras moram num menu — com um nó por delegacia a linha não pode virar isso
    var $menu = $("<div>").addClass("acao-menu");
    [
      ["Editar", abrirEditar],
      [d.in_ativa ? "Inativar" : "Reativar", alternarAtiva],
      ["Trocar token", trocarToken],
      ["Excluir", excluirNo]
    ].forEach(function (b) {
      $("<button>").text(b[0]).css(b[0] === "Excluir" ? { color: "#c62828" } : {})
        .on("click", function () { $menu.removeClass("aberto"); b[1](d); }).appendTo($menu);
    });
    $("<button>").addClass("btn btn-secondary").text("⋯")
      .css({ padding: "6px 10px", fontSize: "12px" })
      .attr("title", "mais ações")
      .on("click", function (ev) {
        ev.stopPropagation();
        var aberto = $menu.hasClass("aberto");
        $(".acao-menu.aberto").removeClass("aberto");
        if (!aberto) posicionarAcaoMenu($menu, this);
      }).appendTo($acoes);
    $menu.appendTo($acoes);

    $tr.data("busca", [d.no_servidor, d.tx_url_base,
      (d.delegacias || []).map(function (x) { return x.no_delegacia + " " + (x.uf || ""); }).join(" ")
    ].join(" ").toUpperCase());
    return $tr;
  }

  /* menu com position:fixed ancorado no botão — a tabela mora num wrap rolável (overflow-x) e
     um menu absoluto dentro da linha nascia CORTADO na borda do wrap (a "caixa escondida").
     Inverte para cima / para a esquerda quando falta espaço na janela. */
  function posicionarAcaoMenu($menu, botao) {
    var r = botao.getBoundingClientRect();
    $menu.css({ top: "0px", left: "0px" }).addClass("aberto");
    var mh = $menu.outerHeight(), mw = $menu.outerWidth();
    var topo = r.bottom + 4;
    if (topo + mh > window.innerHeight - 8) topo = Math.max(8, r.top - mh - 4);
    var esq = r.right - mw;
    if (esq < 8) esq = 8;
    $menu.css({ top: topo + "px", left: esq + "px" });
  }

  $(document).on("click", function () { $(".acao-menu.aberto").removeClass("aberto"); });
  // o menu é fixo na tela: rolar (qualquer direção) desacostaria ele do botão — fecha
  $(window).on("scroll resize", function () { $(".acao-menu.aberto").removeClass("aberto"); });

  function testarNo(d) {
    $.post("/api/servidores/" + d.id_servidor + "/testar").done(function (r) {
      if (r.ok) {
        var extra = r.mesma_versao ? "" : " (versões diferentes: atualize o nó)";
        window.Toast.sucesso("Nó respondeu. Versão " + (r.versao_no || "?") + extra);
      } else {
        window.Toast.erro(r.detail || "Nó não respondeu.");
      }
      carregar();
    }).fail(function () { window.Toast.erro("Falha ao testar o nó."); });
  }

  /* ---------- "Atualizar": pedido via central → /no/atualizar do nó, log por polling ---------- */

  var pollAtualizacao = null;

  function pararAcompanhamento() {
    if (pollAtualizacao) { clearInterval(pollAtualizacao); pollAtualizacao = null; }
  }

  function atualizarNo(d) {
    Dialogo.confirmar({
      titulo: "Atualizar nó",
      mensagem: "Atualizar " + d.no_servidor + " (versão atual: " + (d.tx_versao || "?") +
                ") para o último release? O nó refaz o build e reinicia o container — fica " +
                "offline por 1–2 minutos.",
      rotuloOk: "Atualizar", perigoso: false
    }).then(function (ok) {
      if (!ok) return;
      pararAcompanhamento();
      $("#atualizacao-titulo").text("Atualizando " + d.no_servidor);
      $("#atualizacao-log").text("");
      $("#atualizacao-status").text("Enviando pedido…");
      $("#modal-atualizacao").removeClass("hidden");
      $.post("/api/servidores/" + d.id_servidor + "/atualizar").done(function (r) {
        if (!r.aceito) {
          $("#atualizacao-status").text("Pedido recusado: " + (r.erro || "motivo não informado"));
          return;
        }
        $("#atualizacao-status").text("Pedido aceito — aguardando o agente do nó…");
        acompanharAtualizacao(d.id_servidor);
      }).fail(function (xhr) {
        $("#atualizacao-status").text(((xhr.responseJSON || {}).detail) || "Falha ao falar com o nó.");
      });
    });
  }

  function acompanharAtualizacao(id) {
    var tentativasSemResposta = 0;
    pollAtualizacao = setInterval(function () {
      $.getJSON("/api/servidores/" + id + "/atualizacao").done(function (e) {
        tentativasSemResposta = 0;
        var $log = $("#atualizacao-log");
        $log.text((e.linhas || []).slice(-120).join("\n"));
        $log.scrollTop($log[0].scrollHeight);
        if (e.rodando) {
          $("#atualizacao-status").text("Atualizando… (versão instalada agora: " +
            (e.versao_instalada || "?") + ")");
        } else if (e.resultado) {
          pararAcompanhamento();
          if (e.resultado.ok) {
            $("#atualizacao-status").text("Concluído — nó na versão " + (e.resultado.versao || "?") + ".");
            window.Toast.sucesso("Nó atualizado para " + (e.resultado.versao || "?") + ".");
          } else {
            $("#atualizacao-status").text("Falhou: " + (e.resultado.erro || "verifique o log acima"));
            window.Toast.erro("A atualização do nó falhou.");
          }
          carregar();
        }
      }).fail(function () {
        // build/restart: o container do nó cai e volta — tolera algumas falhas antes de desistir
        tentativasSemResposta += 1;
        if (tentativasSemResposta >= 20) {
          pararAcompanhamento();
          $("#atualizacao-status").text("O nó parou de responder — confira 'docker compose -f " +
            "docker-compose.no.yml logs no' ou clique Testar.");
        } else {
          $("#atualizacao-status").text("Sem resposta do nó (build/restart em curso?) — " +
            tentativasSemResposta + " de 20 tentativas…");
        }
      });
    }, 3000);
  }

  $("#atualizacao-fechar").on("click", function () {
    pararAcompanhamento();
    $("#modal-atualizacao").addClass("hidden");
  });

  function alternarAtiva(d) {
    $.ajax({
      url: "/api/servidores/" + d.id_servidor,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({ in_ativa: !d.in_ativa })
    }).done(function () {
      window.Toast.sucesso(d.in_ativa
        ? d.no_servidor + " inativado — a delegacia volta a processar no central."
        : d.no_servidor + " reativado.");
      carregar();
    }).fail(function (xhr) { window.Toast.erro((xhr.responseJSON || {}).detail || "Falha."); });
  }

  function trocarToken(d) {
    Dialogo.confirmar({
      titulo: "Trocar token",
      mensagem: "Trocar o token revoga " + d.no_servidor + " na hora — ele só volta a " +
                "falar com o central depois de receber o token novo no .env. Continuar?",
      rotuloOk: "Trocar token", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.post("/api/servidores/" + d.id_servidor + "/token").done(function (r) {
        mostrarToken("Token novo de " + d.no_servidor, r.aviso, r.token);
        carregar();
      }).fail(function () { window.Toast.erro("Falha ao trocar o token."); });
    });
  }

  function excluirNo(d) {
    Dialogo.confirmar({
      titulo: "Excluir servidor",
      mensagem: "Excluir " + d.no_servidor + "? As extrações antigas da delegacia perdem o acesso ao arquivo (ele continua na pasta do nó).",
      rotuloOk: "Excluir", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/servidores/" + d.id_servidor, method: "DELETE" }).done(function () {
        window.Toast.sucesso("Servidor excluído.");
        carregar();
      }).fail(function (xhr) { window.Toast.erro((xhr.responseJSON || {}).detail || "Falha."); });
    });
  }

  /* ---------- "Desempenho": config lida AO VIVO do .env do nó, aplicada de volta por POST ---------- */

  var $modalConfig = $("#modal-config");
  var configNo = null;

  function abrirConfig(d) {
    configNo = d;
    $("#config-titulo").text("Desempenho — " + d.no_servidor);
    $("#config-status").text("Consultando o nó…");
    $("#config-campos").empty();
    $("#config-reiniciar").prop("disabled", false);
    $modalConfig.removeClass("hidden");
    $.getJSON("/api/servidores/" + d.id_servidor + "/config").done(function (r) {
      $("#config-status").text("Valores lidos do .env da estação. Vazio = automático (calculado " +
        "para o hardware do nó).");
      $("#config-banner").css("display", r.reinicio_pendente ? "flex" : "none");
      var $c = $("#config-campos").empty();
      (r.parametros || []).forEach(function (p) {
        var alvo = p.configurado || p.recomendado;
        var $label = $("<label>").css("display", "block");
        $("<div>").css({ fontWeight: "600", fontSize: "13px" })
          .text(p.rotulo + " (mín " + p.minimo + ", máx " + p.maximo + ")").appendTo($label);
        var $inp = $("<input>").attr({ type: "number", min: p.minimo, max: p.maximo, step: 1 })
          .attr("data-chave", p.chave).val(p.configurado || "")
          .css({ width: "140px", margin: "4px 8px 2px 0" }).appendTo($label);
        var dica = p.configurado ? "automático seria " + p.recomendado
                                 : "automático: " + p.recomendado;
        if (p.em_vigor && String(p.em_vigor) !== String(alvo)) {
          dica += " · EM VIGOR AGORA: " + p.em_vigor;
        }
        $("<span>").css({ "font-size": "12px", opacity: 0.75 }).text(dica).appendTo($label);
        $inp.data("antes", p.configurado || "");
        $c.append($label);
      });
    }).fail(function (xhr) {
      $("#config-status").text(((xhr.responseJSON || {}).detail) ||
        "O nó não respondeu (offline ou versão antiga).");
    });
  }
  $("#config-fechar").on("click", function () { $modalConfig.addClass("hidden"); });
  $modalConfig.on("click", function (e) { if (e.target === this) $modalConfig.addClass("hidden"); });

  $("#config-reiniciar-agora").on("click", function () {
    if (!configNo) return;
    var $btn = $(this).prop("disabled", true);
    $.post("/api/servidores/" + configNo.id_servidor + "/reiniciar").done(function (r) {
      if (r.aceito) {
        $("#config-banner").css("display", "none");
        window.Toast.sucesso("Reinício solicitado — o nó volta em ~30 s.");
      } else {
        $btn.prop("disabled", false);
        window.Toast.erro(r.erro || "O nó recusou o reinício.");
      }
    }).fail(function (xhr) {
      $btn.prop("disabled", false);
      window.Toast.erro(((xhr.responseJSON || {}).detail) || "Falha ao reiniciar o nó.");
    });
  });

  $("#config-salvar").on("click", function () {
    if (!configNo) return;
    var valores = {};
    $("#config-campos input").each(function () {
      var v = String($(this).val() || "").trim();
      if (v === "" && $(this).data("antes") === "") return;  // continuou automático: não mexe
      valores[$(this).data("chave")] = v === "" ? "" : v;
    });
    if (!Object.keys(valores).length) {
      window.Toast.sucesso("Nada para aplicar.");
      return;
    }
    $("#config-salvar").prop("disabled", true);
    $("#config-status").text("Aplicando no nó…");
    $.ajax({
      url: "/api/servidores/" + configNo.id_servidor + "/config",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ valores: valores, reiniciar: $("#config-reiniciar").is(":checked") })
    }).done(function (r) {
      $("#config-salvar").prop("disabled", false);
      if (r.reiniciando) {
        $("#config-banner").css("display", "none");
        $("#config-status").text("Configuração aplicada — o nó está reiniciando (~30 s) e volta " +
          "sozinho no próximo batimento.");
        window.Toast.sucesso("Config aplicada — reiniciando o nó.");
      } else if (r.precisa_reiniciar) {
        $("#config-banner").css("display", "flex");
        $("#config-status").text("Configuração gravada, mas NÃO reiniciou: " +
          (r.aviso || "use o botão laranja acima quando quiser."));
        window.Toast.erro("Aplicada, mas precisa reiniciar o nó.");
      } else {
        $("#config-status").text("Configuração aplicada (já vale sem reiniciar).");
        window.Toast.sucesso("Config aplicada no nó.");
      }
      carregar();
    }).fail(function (xhr) {
      $("#config-salvar").prop("disabled", false);
      $("#config-status").text(((xhr.responseJSON || {}).detail) || "Falha ao aplicar no nó.");
    });
  });

  /* ---------- delegacias em checkbox (novo e editar compartilham) ---------- */

  var delegaciasCache = null;

  function carregarDelegacias(cb) {
    if (delegaciasCache) { cb(delegaciasCache); return; }
    $.getJSON("/api/delegacias").done(function (lista) {
      delegaciasCache = lista;
      cb(lista);
    }).fail(function () { window.Toast.erro("Não foi possível listar as delegacias."); });
  }

  // Delegacia → nome do nó que já a atende (exceto o nó em edição, que passa vazio nesse id).
  // É o dado que falta para o modal AVISAR o conflito antes do save (a trava de verdade é a
  // UX110 no banco, vista só no POST).
  function mapaOcupadas(excluirId) {
    var mapa = {};
    nosCache.forEach(function (no) {
      if (no.id_servidor === excluirId) return;
      (no.delegacias || []).forEach(function (x) { mapa[x.id_delegacia] = no.no_servidor; });
    });
    return mapa;
  }

  function renderDelegacias($container, selecionadas, ocupadas) {
    $container.empty();
    ocupadas = ocupadas || {};
    delegaciasCache.forEach(function (d) {
      var dono = ocupadas[d.id_delegacia];
      var $label = $("<label>").css({
        display: "block", cursor: dono ? "not-allowed" : "pointer",
        padding: "3px 0", opacity: dono ? 0.55 : 1
      });
      $("<input>").attr("type", "checkbox").val(d.id_delegacia)
        .prop({
          checked: !dono && (selecionadas || []).indexOf(d.id_delegacia) >= 0,
          disabled: !!dono
        })
        .css("margin-right", "8px")
        .appendTo($label);
      $label.append(d.no_delegacia + (d.uf ? " (" + d.uf + ")" : "") +
                    (dono ? " — já no nó " + dono : ""));
      // busca pré-computada por rótulo+UF: o filtro só esconde/mostra (nunca re-renderiza —
      // re-render perderia as marcas feitas antes de filtrar)
      $label.data("busca", (d.no_delegacia + " " + (d.uf || "")).toUpperCase());
      $container.append($label);
    });
    atualizarContagem($container);
  }

  function atualizarContagem($container, visiveis) {
    var total = $container.children("label").length;
    var n = (visiveis === undefined) ? total : visiveis;
    $container.closest(".field").find(".del-contagem")
      .text(n === total ? total + " delegacias" : n + " de " + total + " delegacias");
  }

  function aplicarFiltroDelegacias($container, termo) {
    termo = (termo || "").trim().toUpperCase();
    var visiveis = 0;
    $container.children("label").each(function () {
      var ok = !termo || String($(this).data("busca")).indexOf(termo) >= 0;
      $(this).toggle(ok);
      if (ok) visiveis++;
    });
    atualizarContagem($container, visiveis);
  }

  $(".del-filtro").on("input", window.AnalyserUtils.debounce(function () {
    var $inp = $(this);
    aplicarFiltroDelegacias($("#" + $inp.data("alvo")), $inp.val());
  }, 150));

  function delegaciasMarcadas($container) {
    return $container.find("input:checked").map(function () { return Number(this.value); }).get();
  }

  /* ---------- modal "novo nó" ---------- */

  var $modalNovo = $("#modal-novo-no");
  $("#abrir-novo-no-btn").on("click", function () {
    carregarDelegacias(function () {
      $modalNovo.find(".del-filtro").val("");  // filtro sempre parte de "todas"
      renderDelegacias($("#novo-no-delegacias"), [], mapaOcupadas(null));
      $modalNovo.removeClass("hidden");
    });
  });
  $("#novo-no-fechar").on("click", function () { $modalNovo.addClass("hidden"); });

  $("#novo-no-form").on("submit", function (ev) {
    ev.preventDefault();
    var payload = {
      no_servidor: $("#novo-no-nome").val().trim(),
      ids_delegacia: delegaciasMarcadas($("#novo-no-delegacias")),
      tx_url_base: $("#novo-no-url").val().trim()
    };
    if (!payload.ids_delegacia.length) {
      $("#novo-no-status").text("Escolha pelo menos uma delegacia.");
      return;
    }
    $.ajax({
      url: "/api/servidores",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(payload)
    }).done(function (r) {
      $modalNovo.addClass("hidden");
      $("#novo-no-form")[0].reset();
      mostrarToken("Token de " + payload.no_servidor, r.aviso, r.token);
      carregar();
    }).fail(function (xhr) {
      $("#novo-no-status").text((xhr.responseJSON || {}).detail || "Falha ao criar.");
    });
  });

  /* ---------- modal "editar nó": delegacias atendidas + URL ---------- */

  var $modalEditar = $("#modal-editar-no");
  var editandoNo = null;

  function abrirEditar(d) {
    editandoNo = d;
    $("#editar-no-titulo").text("Editar nó — " + d.no_servidor);
    $("#editar-no-url").val(d.tx_url_base);
    $("#editar-no-midia-url").val(d.tx_midia_url || "");
    $("#editar-no-midia-ativa").prop("checked", !!d.in_midia_direta);
    $("#editar-no-midia-resultado").text("");
    $("#editar-no-status").text("");
    carregarDelegacias(function () {
      var ids = (d.delegacias || []).map(function (x) { return x.id_delegacia; });
      $modalEditar.find(".del-filtro").val("");  // filtro sempre parte de "todas"
      // as delegacias DESTE nó não contam como conflito: só as dos outros ficam bloqueadas
      renderDelegacias($("#editar-no-delegacias"), ids, mapaOcupadas(d.id_servidor));
      $modalEditar.removeClass("hidden");
    });
  }
  $("#editar-no-fechar").on("click", function () { $modalEditar.addClass("hidden"); });
  $modalEditar.on("click", function (e) { if (e.target === this) $modalEditar.addClass("hidden"); });

  $("#editar-no-form").on("submit", function (ev) {
    ev.preventDefault();
    var payload = {
      tx_url_base: $("#editar-no-url").val().trim(),
      ids_delegacia: delegaciasMarcadas($("#editar-no-delegacias")),
      tx_midia_url: $("#editar-no-midia-url").val().trim(),
      in_midia_direta: $("#editar-no-midia-ativa").is(":checked")
    };
    if (!payload.ids_delegacia.length) {
      $("#editar-no-status").text("O nó precisa continuar atendendo pelo menos uma delegacia.");
      return;
    }
    $.ajax({
      url: "/api/servidores/" + editandoNo.id_servidor,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify(payload)
    }).done(function () {
      $modalEditar.addClass("hidden");
      window.Toast.sucesso("Nó atualizado.");
      carregar();
    }).fail(function (xhr) {
      $("#editar-no-status").text((xhr.responseJSON || {}).detail || "Falha ao salvar.");
    });
  });

  // "Testar mídia": o central bate no /no/saude via HTTPS do no-web (o mesmo URL que o
  // navegador vai usar) — DNS, certificado e porta antes de entregar o endereço à delegacia
  $("#editar-no-midia-testar").on("click", function (ev) {
    ev.preventDefault();
    if (!editandoNo) return;
    var $r = $("#editar-no-midia-resultado").text(" testando…");
    $.post("/api/servidores/" + editandoNo.id_servidor + "/testar-midia").done(function (res) {
      $r.css("color", res.ok ? "#2e7d32" : "#c62828")
        .text(" " + (res.ok ? "✓ " + (res.detalhe || "OK")
                            : "✗ " + (res.detail || "sem resposta")));
    }).fail(function () {
      $r.css("color", "#c62828").text(" ✗ falha ao testar");
    });
  });

  carregar();
})();
