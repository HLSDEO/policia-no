/* Tela Administração → Jobs & Filas.

   O que roda no servidor AGORA e o que está esperando vaga — as duas filas que existem no
   sistema: a de jobs (investigacao.job_tb01_jobs, funcoes/jobs_utils.py) e a de extrações
   (.ufdr, INQ_TB02_EXTRACOES, despachante em funcoes/investigacao_utils.py). A API é
   /api/admin/jobs e /api/admin/filas/extracoes; cancelar/liberar reusam as rotas do próprio
   dono do job (/api/investigacao/jobs/{id}/...), que o admin também pode chamar.

   O laço de atualização é um setTimeout por vez (não setInterval): se o servidor atrasar, o
   próximo ciclo só começa depois da resposta, e um admin com duas abas abertas não sonda o
   dobro. A aba oculta não atualiza — quem não está olhando não precisa da sondagem. */
(function () {
  "use strict";

  var UTILS = window.AnalyserUtils;
  var INTERVALO_MS = 5000;

  var $adminCard = $("#admin-card");
  var $semPermissao = $("#sem-permissao-hint");
  var autorizado = false;

  // ---------------- utilidades ----------------

  function decorrido(desde, ate) {
    var inicio = Date.parse(desde);
    if (isNaN(inicio)) return "";
    var fim = ate ? Date.parse(ate) : Date.now();
    var s = Math.max(0, Math.round((fim - inicio) / 1000));
    if (s < 60) return s + " s";
    if (s < 3600) return Math.floor(s / 60) + " min";
    var h = Math.floor(s / 3600);
    var m = Math.round((s % 3600) / 60);
    return h + " h" + (m ? " " + m + " min" : "");
  }

  var CLASSE_STATUS = {
    pendente: "badge-neutro",
    aguardando_vaga: "badge-aviso",
    processando: "badge-rodando",
    concluido: "badge-ok",
    cancelado: "badge-neutro",
    erro: "badge-erro"
  };

  function badge(status, extra) {
    var rotulo = extra || String(status || "").replace(/_/g, " ");
    return $("<span>").addClass("badge " + (CLASSE_STATUS[status] || "badge-neutro")).text(rotulo);
  }

  function barraProgresso(processados, total) {
    var pct = total > 0 ? Math.min(100, Math.round((processados / total) * 100)) : 0;
    var $b = $("<span>").addClass("fila-progresso");
    var $trilha = $("<span>").addClass("fila-progresso-trilha").appendTo($b);
    $("<span>").addClass("fila-progresso-fill").css("width", pct + "%").appendTo($trilha);
    $b.append($("<span>").addClass("fila-progresso-texto")
      .text(processados + " / " + total + " (" + pct + "%)"));
    return $b;
  }

  function inquerritoTexto(job) {
    if (job.nu_inquerito) return UTILS.formatarNumeroInquerito(job.nu_inquerito);
    if (job.id_inquerito) return "Inquérito #" + job.id_inquerito;
    return job.no_escopo || "—";
  }

  // ---------------- jobs ----------------

  var $filtroStatus = $("#filtro-status");
  var $filtroTipo = $("#filtro-tipo");
  var $filtroBusca = $("#filtro-busca");
  var $jobsLista = $("#jobs-lista");
  var $jobsAtualizado = $("#jobs-atualizado");
  var tiposPreenchidos = false;

  function parametrosJobs() {
    var status = $filtroStatus.val();
    var p = { limite: 200 };
    if (status === "todos") p.ativos = false;
    else p.status = status;
    if ($filtroTipo.val()) p.tp_job = $filtroTipo.val();
    var busca = $.trim($filtroBusca.val());
    if (busca) p.busca = busca;
    return p;
  }

  function renderJobs(d) {
    var jobs = d.jobs || [];
    var vagas = d.vagas || {};

    $("#kpi-vagas").text((vagas.ocupadas || 0) + " / " + (vagas.limite || 0))
      .css("color", (vagas.ocupadas || 0) >= (vagas.limite || 1) ? "var(--warning)" : "");
    var resumo = d.resumo || {};
    $("#kpi-fila").text((resumo.pendente || 0) + (resumo.aguardando_vaga || 0));
    $("#kpi-processando").text(resumo.processando || 0);

    if (!tiposPreenchidos && d.tipos) {
      var valorAtual = $filtroTipo.val();
      $filtroTipo.find("option:not(:first)").remove();
      Object.keys(d.tipos).forEach(function (tp) {
        $("<option>").val(tp).text(d.tipos[tp]).appendTo($filtroTipo);
      });
      $filtroTipo.val(valorAtual || "");
      tiposPreenchidos = true;
    }

    $jobsLista.empty();
    if (!jobs.length) {
      $("<p>").addClass("empty-hint")
        .text($filtroStatus.val() === "fila" ? "Nenhum job em andamento." : "Nenhum job encontrado com estes filtros.")
        .appendTo($jobsLista);
      return;
    }

    var $tabela = $("<table>").addClass("grupo-tabela fila-tabela");
    $("<thead>").append($("<tr>")
      .append($("<th>").text("Job"))
      .append($("<th>").text("Inquérito / escopo"))
      .append($("<th>").text("Dono"))
      .append($("<th>").text("Status"))
      .append($("<th>").text("Progresso"))
      .append($("<th>").text("Tempo"))
      .append($("<th>").text("Worker"))
      .append($("<th>").text(""))
    ).appendTo($tabela);
    var $corpo = $("<tbody>").appendTo($tabela);

    jobs.forEach(function (j) {
      var $tr = $("<tr>").appendTo($corpo);

      $("<td>").attr("data-label", "Job")
        .append($("<strong>").text(j.rotulo || j.tp_job))
        .append($("<span>").addClass("fila-secundario").text("#" + j.id_job + " · " + j.tp_job))
        .appendTo($tr);

      $("<td>").attr("data-label", "Inquérito").text(inquerritoTexto(j)).appendTo($tr);
      $("<td>").attr("data-label", "Dono").text(j.no_usuario || "—").appendTo($tr);

      var $status = $("<td>").attr("data-label", "Status").appendTo($tr);
      $status.append(badge(j.cd_status));
      if (j.fl_cancelar && j.rodando) {
        $status.append($("<span>").addClass("fila-secundario").text("cancelamento pedido"));
      }
      if (j.tx_erro) {
        $("<span>").addClass("fila-erro-texto").attr("title", j.tx_erro)
          .text(j.tx_erro).appendTo($status);
      }

      var $prog = $("<td>").attr("data-label", "Progresso").appendTo($tr);
      if (j.qt_total > 0) {
        $prog.append(barraProgresso(j.qt_processados, j.qt_total));
        if (j.qt_erros) {
          $prog.append($("<span>").addClass("fila-secundario").text(j.qt_erros + " com erro"));
        }
      } else {
        $prog.text("—");
      }

      var $tempo = $("<td>").attr("data-label", "Tempo").appendTo($tr);
      $tempo.append(document.createTextNode(decorrido(j.dt_inicio, j.dt_fim)));
      if (j.qt_na_frente > 0) {
        $tempo.append($("<span>").addClass("fila-posicao")
          .text(j.qt_na_frente + " na fila à frente"));
      }
      $tempo.append($("<span>").addClass("fila-secundario")
        .text(UTILS.formatarDataHora(j.dt_inicio)));

      $("<td>").attr("data-label", "Worker").text(j.no_worker || "—").appendTo($tr);

      var $acoes = $("<td>").addClass("col-acoes").appendTo($tr);
      if (j.rodando) {
        if (!j.fl_cancelar) {
          $("<button>").addClass("btn btn-mini").text("Cancelar")
            .on("click", function () { cancelarJob(j); }).appendTo($acoes);
        }
        $("<button>").addClass("btn btn-mini").text("Liberar")
          .attr("title", "Encerra o job na mão — para quando o processo que o tocava morreu.")
          .on("click", function () { liberarJob(j); }).appendTo($acoes);
      }
    });

    $tabela.appendTo($jobsLista);
  }

  function cancelarJob(job) {
    Dialogo.confirmar({
      titulo: "Cancelar job",
      mensagem: "Pedir a parada do job #" + job.id_job + " (" + (job.rotulo || job.tp_job) +
        ", de " + (job.no_usuario || "usuário desconhecido") + ")? O worker ancora na próxima " +
        "etapa e o job vira \"cancelado\".",
      rotuloOk: "Cancelar job"
    }).then(function (ok) {
      if (!ok) return;
      Api.post("/api/investigacao/jobs/" + job.id_job + "/cancelar", {})
        .done(function (r) { Toast.sucesso(r.mensagem || "Cancelamento solicitado."); carregarJobs(); })
        .fail(function (xhr) { Toast.erro(Api.mensagemErro(xhr, "Não foi possível cancelar.")); });
    });
  }

  function liberarJob(job) {
    Dialogo.confirmar({
      titulo: "Liberar job",
      mensagem: "Encerrar o job #" + job.id_job + " (" + (job.rotulo || job.tp_job) +
        ")? Use quando o processo que o tocava morreu e ele não sai da tela. O job vira \"erro\" " +
        "e a vaga de CPU é liberada.",
      rotuloOk: "Liberar"
    }).then(function (ok) {
      if (!ok) return;
      Api.post("/api/investigacao/jobs/" + job.id_job + "/liberar", {})
        .done(function (r) { Toast.sucesso(r.mensagem || "Processamento liberado."); carregarJobs(); })
        .fail(function (xhr) { Toast.erro(Api.mensagemErro(xhr, "Não foi possível liberar.")); });
    });
  }

  function carregarJobs() {
    Api.get("/api/admin/jobs", parametrosJobs())
      .done(function (d) {
        autorizado = true;
        $adminCard.removeClass("hidden");
        $semPermissao.addClass("hidden");
        renderJobs(d);
        $jobsAtualizado.text("atualizado " + new Date().toLocaleTimeString("pt-BR"));
      })
      .fail(function (xhr) {
        if (xhr.status === 403) {
          $adminCard.addClass("hidden");
          $semPermissao.removeClass("hidden");
          return;
        }
        if (!autorizado) {
          Estados.erro($jobsLista, {
            mensagem: Api.mensagemErro(xhr, "Não foi possível ler os jobs."),
            aoTentarDeNovo: carregarJobs
          });
        } else {
          $jobsAtualizado.text("sem resposta do servidor");
        }
      });
  }

  // ---------------- extrações ----------------

  var $extFiltroStatus = $("#ext-filtro-status");
  var $extFiltroBusca = $("#ext-filtro-busca");
  var $extLista = $("#ext-lista");
  var $extAtualizado = $("#ext-atualizado");

  function renderExtracoes(d) {
    var lista = d.extracoes || [];
    var r = d.resumo || {};

    $("#kpi-extracoes").text((r.rodando_local || 0) + " / " + (r.limite_local || 0))
      .css("color", (r.rodando_local || 0) >= (r.limite_local || 1) ? "var(--warning)" : "");
    $("#kpi-extracoes-label").text("extrações locais em execução" +
      (r.rodando_remotas ? " · " + r.rodando_remotas + " em nós" : ""));
    $("#kpi-ext-fila").text(r.pendentes || 0);

    $extLista.empty();
    if (!lista.length) {
      $("<p>").addClass("empty-hint")
        .text($extFiltroStatus.val() === "fila" ? "Nenhuma extração em andamento." : "Nenhuma extração encontrada.")
        .appendTo($extLista);
      return;
    }

    var $tabela = $("<table>").addClass("grupo-tabela fila-tabela");
    $("<thead>").append($("<tr>")
      .append($("<th>").text("Extração"))
      .append($("<th>").text("Inquérito"))
      .append($("<th>").text("Dispositivo / nó"))
      .append($("<th>").text("Quem"))
      .append($("<th>").text("Status"))
      .append($("<th>").text("Fase"))
      .append($("<th>").text("Fila / tempo"))
    ).appendTo($tabela);
    var $corpo = $("<tbody>").appendTo($tabela);

    lista.forEach(function (e) {
      var $tr = $("<tr>").appendTo($corpo);

      var $arq = $("<td>").attr("data-label", "Arquivo").appendTo($tr);
      $("<strong>").text(UTILS.nomeArquivo(e.no_arquivo || "")).appendTo($arq);
      $("<span>").addClass("fila-secundario").attr("title", e.no_arquivo || "")
        .text("#" + e.id_extracao).appendTo($arq);

      $("<td>").attr("data-label", "Inquérito")
        .text(e.nu_inquerito ? UTILS.formatarNumeroInquerito(e.nu_inquerito) : "—")
        .appendTo($tr);

      var $disp = $("<td>").attr("data-label", "Dispositivo").appendTo($tr);
      $disp.append(document.createTextNode(e.no_dispositivo || "—"));
      if (e.no_servidor) {
        $disp.append($("<span>").addClass("fila-secundario").text("nó " + e.no_servidor));
      }

      $("<td>").attr("data-label", "Quem").text(e.no_usuario || "—").appendTo($tr);

      var $status = $("<td>").attr("data-label", "Status").appendTo($tr);
      $status.append(badge(e.cd_status));
      if (e.fl_cancelar && (e.cd_status === "pendente" || e.cd_status === "processando")) {
        $status.append($("<span>").addClass("fila-secundario").text("cancelamento pedido"));
      }
      if (e.tx_erro) {
        $("<span>").addClass("fila-erro-texto").attr("title", e.tx_erro)
          .text(e.tx_erro).appendTo($status);
      }

      var $fase = $("<td>").attr("data-label", "Fase").appendTo($tr);
      if (e.cd_status === "processando") {
        $fase.append(document.createTextNode(e.cd_fase || "processando"));
        if (e.qt_progresso_fase != null) {
          $fase.append(barraProgresso(e.qt_progresso_fase, 100));
        }
      } else if (e.cd_status === "pendente") {
        $fase.text("na fila");
      } else {
        $fase.text(e.cd_fase || "—");
      }

      var $fila = $("<td>").attr("data-label", "Fila / tempo").appendTo($tr);
      if (e.cd_status === "pendente" && e.qt_na_frente > 0) {
        $fila.append($("<span>").addClass("fila-posicao")
          .text(e.qt_na_frente + " na fila à frente"));
      }
      var desde = e.dt_inicio_processamento || e.dt_upload;
      $fila.append(document.createTextNode(decorrido(desde, e.dt_fim_processamento)));
      $fila.append($("<span>").addClass("fila-secundario")
        .text(UTILS.formatarDataHora(e.dt_upload)));
    });

    $tabela.appendTo($extLista);
  }

  function carregarExtracoes() {
    var p = { status: $extFiltroStatus.val(), limite: 100 };
    var busca = $.trim($extFiltroBusca.val());
    if (busca) p.busca = busca;
    Api.get("/api/admin/filas/extracoes", p)
      .done(function (d) {
        renderExtracoes(d);
        $extAtualizado.text("atualizado " + new Date().toLocaleTimeString("pt-BR"));
      })
      .fail(function (xhr) {
        if (xhr.status === 403) return; // o fail de carregarJobs já trocou a tela
        if (!autorizado) return;
        $extAtualizado.text("sem resposta do servidor");
      });
  }

  // ---------------- laço ----------------

  var timer = null;

  function ciclo() {
    timer = setTimeout(function () {
      if (!autorizado) return;
      if (!document.hidden) {
        carregarJobs();
        carregarExtracoes();
      }
      ciclo();
    }, INTERVALO_MS);
  }

  $("#jobs-atualizar-btn").on("click", function () { carregarJobs(); });
  $("#ext-atualizar-btn").on("click", function () { carregarExtracoes(); });
  $filtroStatus.on("change", carregarJobs);
  $filtroTipo.on("change", carregarJobs);
  $filtroBusca.on("input", UTILS.debounce(carregarJobs, 400));
  $extFiltroStatus.on("change", carregarExtracoes);
  $extFiltroBusca.on("input", UTILS.debounce(carregarExtracoes, 400));

  Carregando.emBloco($jobsLista);
  carregarJobs();
  carregarExtracoes();
  ciclo();
})();
