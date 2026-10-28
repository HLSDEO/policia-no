(function () {
  "use strict";

  var INTERVALO_MS = 5000;

  var $adminCard = $("#admin-card");
  var $semPermissao = $("#sem-permissao-hint");
  var $discos = $("#kpi-discos");
  var $atualizado = $("#kpi-atualizado");

  function formatarBytes(n) {
    if (n === null || n === undefined) return "—";
    var unidades = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < unidades.length - 1) {
      n /= 1024;
      i++;
    }
    return n.toFixed(i === 0 ? 0 : 1) + " " + unidades[i];
  }

  function corPercentual(p) {
    if (p >= 90) return "var(--danger)";
    if (p >= 75) return "var(--warning)";
    return "var(--success)";
  }

  function renderBarraDisco(disco) {
    var $linha = $("<div>").addClass("kpi-barra-linha");
    $("<div>").addClass("kpi-barra-rotulo").attr("title", disco.caminho).text(disco.rotulo).appendTo($linha);
    var $trilha = $("<div>").addClass("kpi-barra-trilha").appendTo($linha);
    $("<div>").addClass("kpi-barra-preenchimento")
      .css({ width: disco.percentual + "%", background: corPercentual(disco.percentual) })
      .appendTo($trilha);
    $("<div>").addClass("kpi-barra-valor").text(disco.percentual.toFixed(1) + "%").appendTo($linha);
    $("<div>").addClass("kpi-barra-detalhe").text(formatarBytes(disco.usado) + " / " + formatarBytes(disco.total)).appendTo($linha);
    return $linha;
  }

  function carregarMetricas() {
    $.getJSON("/api/sistema/metricas")
      .done(function (m) {
        $adminCard.removeClass("hidden");
        $semPermissao.addClass("hidden");

        $("#kpi-cpu").text(m.cpu_percent.toFixed(1) + "%").css("color", corPercentual(m.cpu_percent));
        $("#kpi-cpu-label").text("uso de CPU · " + m.cpu_cores + " núcleo" + (m.cpu_cores > 1 ? "s" : ""));

        $("#kpi-memoria").text(m.memoria.percentual.toFixed(1) + "%").css("color", corPercentual(m.memoria.percentual));
        $("#kpi-memoria-label").text(formatarBytes(m.memoria.usado) + " / " + formatarBytes(m.memoria.total) + " de memória");

        $discos.empty();
        m.discos.forEach(function (d) { $discos.append(renderBarraDisco(d)); });

        $atualizado.text("Atualizado às " + new Date().toLocaleTimeString("pt-BR"));

        setTimeout(carregarMetricas, INTERVALO_MS);
      })
      .fail(function () {
        $adminCard.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  // ---------- Versão e atualização ----------

  var $versaoInstalada = $("#versao-instalada");
  var $migracoesEstado = $("#migracoes-estado");
  var $migracoesBtn = $("#migracoes-aplicar-btn");
  var $atualizacaoEstado = $("#atualizacao-estado");
  var $atualizacaoBtn = $("#atualizacao-verificar-btn");
  var $atualizacaoAplicarBtn = $("#atualizacao-aplicar-btn");
  var $atualizacaoNotas = $("#atualizacao-notas");
  var $progresso = $("#atualizacao-progresso");
  var $progressoTitulo = $("#atualizacao-progresso-titulo");
  var $progressoLog = $("#atualizacao-progresso-log");

  var versaoDisponivel = null;
  var pollProgressoTimer = null;

  function renderMigracoes(m) {
    var pendentes = (m && m.pendentes) || [];
    if (!pendentes.length) {
      $migracoesEstado.text("em dia").css("color", "var(--success)");
      $migracoesBtn.addClass("hidden");
    } else {
      $migracoesEstado
        .text(pendentes.length + " pendente" + (pendentes.length > 1 ? "s" : "") + " (" + pendentes.join(", ") + ")")
        .css("color", "var(--warning)");
      $migracoesBtn.removeClass("hidden");
    }
    if (m && m.alteradas_apos_aplicadas && m.alteradas_apos_aplicadas.length) {
      $migracoesEstado.append(
        $("<div>").css({ color: "var(--danger)", fontSize: "12px", marginTop: "4px" })
          .text("editada(s) após aplicada(s): " + m.alteradas_apos_aplicadas.join(", "))
      );
    }
    // Número repetido: o prefixo é a identidade da migração no ledger, então uma das duas nunca
    // seria aplicada. O runner recusa aplicar enquanto isso existir — a tela diz por quê.
    if (m && m.numeros_duplicados && m.numeros_duplicados.length) {
      $migracoesEstado.append(
        $("<div>").css({ color: "var(--danger)", fontSize: "12px", marginTop: "4px" })
          .text("número repetido (nenhuma migração é aplicada até corrigir): "
                + m.numeros_duplicados.join("; "))
      );
    }
  }

  function idadeVerificacao(ts) {
    if (!ts) return "";
    var s = Math.floor(Date.now() / 1000) - ts;
    if (s < 0) return "";
    if (s < 90) return "agora há pouco";
    if (s < 3600) return "há " + Math.round(s / 60) + " min";
    if (s < 86400) return "há " + Math.round(s / 3600) + " h";
    return "há " + Math.round(s / 86400) + " d";
  }

  function renderAtualizacao(v) {
    $atualizacaoNotas.addClass("hidden").empty();
    $atualizacaoAplicarBtn.addClass("hidden");
    versaoDisponivel = null;

    if (v.erro && !v.versao_disponivel) {
      $atualizacaoEstado.text(v.erro).css("color", "var(--text-secondary)");
      return;
    }
    if (v.disponivel) {
      versaoDisponivel = v.versao_disponivel;
      $atualizacaoEstado
        .text("versão " + v.versao_disponivel + " disponível (instalada: " + v.instalada + ")")
        .css("color", "var(--warning)");
      // O botão só serve onde há agente de host (systemd) para aplicar. Sem ele — Docker Desktop,
      // Windows, dev — mostra a instrução em vez de um botão que travaria.
      if (v.agente_host) {
        $atualizacaoAplicarBtn.removeClass("hidden").text("Atualizar para " + v.versao_disponivel);
      } else {
        $atualizacaoNotas.removeClass("hidden");
        $("<div>").css({ color: "var(--text-secondary)", fontSize: "13px" })
          .text("Atualização pelo servidor: git checkout " + v.versao_disponivel + " && docker compose up -d --build")
          .appendTo($atualizacaoNotas);
      }
      if (v.erro) {
        $atualizacaoEstado.append(
          $("<div>").css({ color: "var(--text-secondary)", fontSize: "12px", marginTop: "4px" }).text(v.erro)
        );
      }
      if (v.notas || v.url) {
        $atualizacaoNotas.removeClass("hidden");
        if (v.url) {
          $("<a>").attr({ href: v.url, target: "_blank", rel: "noopener" })
            .text("Ver release no GitHub").appendTo($atualizacaoNotas);
        }
        if (v.notas) {
          $("<pre>").addClass("versao-notas-corpo").text(v.notas).appendTo($atualizacaoNotas);
        }
      }
    } else {
      var idade = idadeVerificacao(v.verificado_em);
      $atualizacaoEstado
        .text("instalação em dia (" + v.instalada + ")" + (idade ? " · verificado " + idade : ""))
        .css("color", "var(--success)");
      // release malformado (VERSION da tag não bate) ou info do host velha — mostra o motivo
      // em vez de deixar o operador achando que a checagem não viu nada.
      if (v.erro) {
        $atualizacaoEstado.append(
          $("<div>").css({ color: "var(--warning)", fontSize: "12px", marginTop: "4px" }).text(v.erro)
        );
        if (v.versao_disponivel && v.url) {
          $atualizacaoNotas.removeClass("hidden");
          $("<a>").attr({ href: v.url, target: "_blank", rel: "noopener" })
            .text("Ver release " + v.versao_disponivel + " no GitHub").appendTo($atualizacaoNotas);
        }
      }
    }
  }

  // ---------- Aplicar atualização (via agente do host) ----------

  function renderProgresso(p) {
    if (!p.linhas.length && !p.resultado && !p.rodando) {
      $progresso.addClass("hidden");
      return;
    }
    $progresso.removeClass("hidden");
    $progressoLog.text(p.linhas.join("\n"));
    $progressoLog.scrollTop($progressoLog[0].scrollHeight);

    if (p.rodando) {
      $progressoTitulo.text("Atualizando…").css("color", "var(--warning)");
      $atualizacaoAplicarBtn.prop("disabled", true);
    } else if (p.resultado) {
      if (p.resultado.ok) {
        $progressoTitulo.text("Concluído — versão " + p.resultado.versao).css("color", "var(--success)");
      } else {
        $progressoTitulo.text("Falhou: " + (p.resultado.erro || "ver log")).css("color", "var(--danger)");
      }
      $atualizacaoAplicarBtn.prop("disabled", false);
    }
  }

  function pararPoll() {
    if (pollProgressoTimer) { clearTimeout(pollProgressoTimer); pollProgressoTimer = null; }
  }

  function pollProgresso() {
    Api.get("/api/sistema/atualizacao/progresso")
      .done(function (p) {
        renderProgresso(p);
        if (p.rodando) {
          pollProgressoTimer = setTimeout(pollProgresso, 2500);
        } else {
          pararPoll();
          if (p.resultado && p.resultado.ok) {
            Toast.sucesso("Atualizado para " + p.resultado.versao);
            carregarVersao();
            carregarMigracoes();
          } else if (p.resultado) {
            Toast.erro("Atualização falhou — veja o log na tela");
          }
        }
      })
      .fail(function () { pollProgressoTimer = setTimeout(pollProgresso, 5000); });
  }

  $atualizacaoAplicarBtn.on("click", function () {
    if (!versaoDisponivel) return;
    Dialogo.confirmar({
      titulo: "Atualizar para " + versaoDisponivel,
      mensagem: "O servidor vai baixar a versão " + versaoDisponivel + ", trocar os arquivos e reconstruir os "
        + "containers. A plataforma fica alguns minutos indisponível durante o build. Continuar?",
      rotuloOk: "Atualizar"
    }).then(function (ok) {
      if (!ok) return;
      $atualizacaoAplicarBtn.prop("disabled", true).text("Enviando pedido…");
      $progressoLog.text("");
      Api.post("/api/sistema/atualizacao/aplicar", { versao: versaoDisponivel })
        .done(function (r) {
          if (!r.aceito) {
            Toast.erro(r.erro || "pedido recusado");
            $atualizacaoAplicarBtn.prop("disabled", false).text("Atualizar para " + versaoDisponivel);
            return;
          }
          Toast.info("Atualização iniciada — acompanhe abaixo");
          $progresso.removeClass("hidden");
          $progressoTitulo.text("Atualizando…").css("color", "var(--warning)");
          pararPoll();
          pollProgresso();
        })
        .fail(function (xhr) {
          Toast.erro(Api.mensagemErro(xhr));
          $atualizacaoAplicarBtn.prop("disabled", false).text("Atualizar para " + versaoDisponivel);
        });
    });
  });

  function carregarMigracoes() {
    return Api.get("/api/sistema/migracoes").done(renderMigracoes);
  }

  function carregarEstadoAtualizacao(forcar) {
    return Api.get("/api/sistema/atualizacao", forcar ? { forcar: 1 } : null)
      .done(function (d) {
        renderMigracoes(d.migracoes);
        renderAtualizacao(d.versao);
      });
  }

  $migracoesBtn.on("click", function () {
    Dialogo.confirmar({
      titulo: "Aplicar migrações pendentes",
      mensagem: "Roda agora os deltas de schema ainda não aplicados neste banco. É o mesmo que a API faz ao reiniciar."
    }).then(function (ok) {
      if (!ok) return;
      $migracoesBtn.prop("disabled", true).text("Aplicando…");
      Api.post("/api/sistema/migracoes/aplicar")
        .done(function (r) {
          if (r.erro) {
            Toast.erro("Falhou: " + r.erro);
          } else if (r.aplicadas && r.aplicadas.length) {
            Toast.sucesso(r.aplicadas.length + " migração(ões) aplicada(s)");
          } else {
            Toast.info("Nada pendente");
          }
          carregarMigracoes();
        })
        .fail(function (xhr) { Toast.erro(Api.mensagemErro(xhr)); })
        .always(function () { $migracoesBtn.prop("disabled", false).text("Aplicar pendentes"); });
    });
  });

  $atualizacaoBtn.on("click", function () {
    $atualizacaoBtn.prop("disabled", true).text("Verificando…");
    $atualizacaoEstado.text("consultando o GitHub…").css("color", "var(--text-secondary)");
    carregarEstadoAtualizacao(true)
      .fail(function (xhr) { $atualizacaoEstado.text(Api.mensagemErro(xhr)); })
      .always(function () { $atualizacaoBtn.prop("disabled", false).text("Verificar"); });
  });

  function carregarVersao() {
    Api.get("/api/sistema/versao")
      .done(function (d) { $versaoInstalada.text(d.instalada || "—"); });
  }

  carregarVersao();
  // Estado das migrações não custa rede; já mostra ao abrir. A checagem de update (que bate no
  // GitHub) fica só no clique de "Verificar".
  Api.get("/api/sistema/migracoes")
    .done(function (m) { renderMigracoes(m); });

  // Retoma o acompanhamento se uma atualização já estiver em curso (aberta noutra aba, ou a
  // página foi recarregada no meio).
  Api.get("/api/sistema/atualizacao/progresso")
    .done(function (p) {
      renderProgresso(p);
      if (p.rodando) pollProgresso();
    });

  carregarMetricas();
})();
