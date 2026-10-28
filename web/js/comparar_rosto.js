/* Tela "Comparar rosto": envia uma foto de referência, acompanha o job de comparação facial
 * (JOB_TB01_JOBS, tipo `comparacao_facial`) e lista os arquivos mais parecidos.
 *
 * O job é sempre acompanhado por id, em GET /api/investigacao/jobs/{id_job}; ao abrir a tela a
 * pergunta é pelo ÚLTIMO job do inquérito (GET .../inqueritos/{id}/jobs/comparacao_facial), o que
 * serve para dois casos que antes não tinham saída: recarregar a página no meio da varredura
 * retoma o acompanhamento em vez de deixar o job rodando invisível, e o resultado da última
 * comparação volta a aparecer sem ter que subir a foto de novo.
 *
 * Backend: api/rotas/comparar_rosto.py + api/funcoes/comparar_rosto_utils.py (docs/comparar_rosto.md). */
(function () {
  "use strict";

  var U = window.AnalyserUtils;

  var $inquerito = $("#inquerito-select");
  var $ajudaInquerito = $("#inquerito-help");
  var $aparelho = $("#extracao-select");
  var $ajudaAparelho = $("#extracao-help");
  var $limite = $("#limite-select");
  var $dropZone = $("#drop-zone");
  var $arquivo = $("#file-input");
  var $previa = $("#preview-wrap");
  var $previaImg = $("#preview-img");
  var $trocar = $("#trocar-img-btn");
  var $ajudaArquivo = $("#file-help");
  var $form = $("#upload-form");
  var $btn = $("#comparar-btn");
  var $btnCancelar = $("#cancelar-btn");
  var $status = $("#form-status");

  var $resultados = $("#resultados-wrap");
  var $lista = $("#resultados-lista");
  var $contagem = $("#resultados-count");
  var $hint = $("#resultados-hint");
  var $hintTopo = $("#header-hint");

  var $filtros = $("#cmp-filtros");
  var $filtroBusca = $("#filtro-busca");
  var $filtroMin = $("#filtro-similaridade");
  var $filtroOrdem = $("#filtro-ordenar");
  var $filtroInfo = $("#filtro-info");

  var $modalOrigem = $("#modal-origem");
  var $origemConteudo = $("#origem-conteudo");
  var $origemFechar = $("#origem-fechar");
  var $modalMidia = $("#modal-midia");
  var $midiaConteudo = $("#midia-conteudo");
  var $midiaFechar = $("#midia-fechar");

  // mesmo teto de MAX_TAMANHO_UPLOAD (api/rotas/comparar_rosto.py): dito antes do upload, para o
  // analista não esperar 400 do servidor por uma foto de 25 MB
  var TAMANHO_MAXIMO = 10 * 1024 * 1024;

  var arquivoSelecionado = null;
  var urlPrevia = null;
  var idInquerito = null;
  var resultados = [];
  var idJob = null;
  var jobRodando = false;
  var pollTimer = null;
  // número da última troca de inquérito: uma resposta que chegue depois de o analista já ter
  // escolhido outro inquérito é descartada, em vez de aparecer no contexto errado
  var geracao = 0;
  var aparelhoDaUrl = U.paramUrl("extracao", "");

  function truncarCaminho(caminho, max) {
    if (!caminho) return "—";
    if (caminho.length <= max) return caminho;
    return "…" + caminho.slice(-(max - 1));
  }

  function rotuloAparelho(aparelho) {
    return aparelho.no_dispositivo || aparelho.no_fabricante
      || U.nomeArquivo(aparelho.no_arquivo) || ("Extração " + aparelho.id_extracao);
  }

  function atualizarBotao() {
    $btn.prop("disabled", !(arquivoSelecionado && idInquerito) || jobRodando);
  }

  function setStatus(texto, tipo) {
    $status.text(texto || "").removeClass("success error").addClass(tipo || "");
  }

  // ---------- modais acessíveis (mesmo padrão de investigacao.js: devolve o foco a quem abriu) ----------

  var focoRetorno = new WeakMap();

  function abrirModal($overlay, $foco) {
    var ativo = document.activeElement;
    if (ativo && !$overlay.has(ativo).length) focoRetorno.set($overlay[0], ativo);
    $overlay.removeClass("hidden");
    $("body").css("overflow", "hidden");
    var $alvo = ($foco && $foco.length) ? $foco
      : $overlay.find("button, [href], input, select").filter(":visible").first();
    if ($alvo.length) setTimeout(function () { $alvo.focus(); }, 50);
  }

  function fecharModal($overlay) {
    $overlay.addClass("hidden");
    if (!$(".modal-overlay:not(.hidden)").length) $("body").css("overflow", "");
    var anterior = focoRetorno.get($overlay[0]);
    focoRetorno.delete($overlay[0]);
    if (anterior && document.contains(anterior)) {
      try { anterior.focus(); } catch (e) { /* o card de origem pode ter sumido da tela */ }
    }
  }

  // Esc fecha o modal do topo: a imagem ampliada está por cima da origem, quando os dois estão abertos
  $(document).on("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!$modalMidia.hasClass("hidden")) fecharModal($modalMidia);
    else if (!$modalOrigem.hasClass("hidden")) fecharModal($modalOrigem);
  });

  // ---------- inquérito · aparelho ----------

  function carregarInqueritos() {
    Api.get("/api/investigacao/inqueritos").done(function (lista) {
      Estados.limpar($ajudaInquerito);
      $inquerito.empty();
      if (!lista || !lista.length) {
        $inquerito.append($("<option>").val("").text("Nenhum inquérito disponível"));
        $ajudaInquerito.text("É preciso ter um inquérito com extração concluída para comparar rostos.");
        atualizarBotao();
        return;
      }
      lista.forEach(function (inq) {
        $inquerito.append(
          $("<option>").val(inq.id_inquerito)
            .text(inq.nu_inquerito + " (" + inq.qt_extracoes + " extrações)")
        );
      });
      var sel = U.paramUrl("inquerito");
      var nuSel = U.paramUrl("nu_inquerito");
      if (sel && $inquerito.find('option[value="' + sel + '"]').length) {
        $inquerito.val(sel);
      } else if (nuSel) {
        var achado = lista.find(function (inq) { return inq.nu_inquerito === nuSel; });
        if (achado) $inquerito.val(String(achado.id_inquerito));
      }
      aoTrocarInquerito(true);
    }).fail(function (xhr) {
      $inquerito.empty().append($("<option>").val("").text("Erro ao carregar inquéritos"));
      Estados.erro($ajudaInquerito, {
        mensagem: Api.mensagemErro(xhr, "Não foi possível carregar os inquéritos."),
        aoTentarDeNovo: carregarInqueritos,
      });
      atualizarBotao();
    });
  }

  $inquerito.on("change", function () { aoTrocarInquerito(false); });

  function aoTrocarInquerito(primeiraVez) {
    var gerao = ++geracao;
    idInquerito = $inquerito.val() || null;
    $hintTopo.text(idInquerito ? "Inquérito: " + $inquerito.find("option:selected").text() : "");
    if (window.EstadoUrl) EstadoUrl.salvar({ inquerito: idInquerito });

    // o job pertence ao inquérito em que foi iniciado: continuar acompanhando o anterior depois de
    // trocar o select mostraria progresso e resultados de outro inquérito na tela
    pararPoll();
    idJob = null;
    definirJobRodando(false);
    limparResultados();

    carregarAparelhos(gerao, primeiraVez);
  }

  function carregarAparelhos(gerao, primeiraVez) {
    aparelhoDaUrl = primeiraVez ? aparelhoDaUrl : "";
    $aparelho.empty().append($("<option>").val("").text("Todos os aparelhos do inquérito"));
    $aparelho.prop("disabled", true);
    if (!idInquerito) {
      $ajudaAparelho.text("");
      return;
    }
    // mesma chave de cache de js/utils/navegacao_inquerito.js: a trilha da página já buscado esta lista
    U.buscarComCacheSessao(
      "nav:extracoes:" + idInquerito,
      "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/extracoes-lista"
    ).then(function (lista) {
      if (gerao !== geracao) return;
      var aparelhos = lista || [];
      aparelhos.forEach(function (a) {
        $aparelho.append($("<option>").val(a.id_extracao).text(rotuloAparelho(a)));
      });
      $aparelho.prop("disabled", aparelhos.length === 0);
      var alvo = String(aparelhoDaUrl || "");
      if (alvo && $aparelho.find('option[value="' + alvo + '"]').length) $aparelho.val(alvo);
      aparelhoDaUrl = "";
      atualizarAjudaAparelho();
      retomarUltimoJob(gerao);
    });
  }

  $aparelho.on("change", atualizarAjudaAparelho);

  function atualizarAjudaAparelho() {
    $ajudaAparelho.text($aparelho.val()
      ? "A varredura roda só neste aparelho — mais rápida e só compara com as imagens dele."
      : "A varredura roda em todas as imagens do inquérito.");
  }

  // ---------- job: retomar, acompanhar, cancelar ----------

  function retomarUltimoJob(gerao) {
    if (!idInquerito) return;
    Api.enviar({
      url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/jobs/comparacao_facial",
      method: "GET",
      erroSilencioso: true, // sem job nenhum é o caso normal de um inquérito novo
    }).done(function (job) {
      if (gerao !== geracao || !job) return;
      var escopo = job.tx_parametros && job.tx_parametros.extracao;
      if (escopo && $aparelho.find('option[value="' + escopo + '"]').length) {
        $aparelho.val(String(escopo));
        atualizarAjudaAparelho();
      }

      if (job.rodando) {
        idJob = job.id_job;
        iniciarPoll();
        return;
      }
      var matches = job.tx_resultado && job.tx_resultado.matches;
      if (!matches) return;
      mostrarResultados(matches, "Última comparação deste inquérito"
        + (job.dt_fim ? ", concluída em " + U.formatarDataHora(job.dt_fim) : "."));
    });
  }

  function definirJobRodando(rodando) {
    jobRodando = rodando;
    $btnCancelar.toggleClass("hidden", !rodando);
    $dropZone.toggleClass("disabled", rodando);
    if (!rodando) rotuloCancelar("Cancelar");
    atualizarBotao();
  }

  // o rótulo entra num <span> porque o ícone de parar fica no botão: .text() levaria o SVG junto
  var ICONE_PARAR = window.Icones ? window.Icones.html("parar") : "";

  function rotuloCancelar(texto) {
    $btnCancelar.prop("disabled", false).html(ICONE_PARAR + $("<span>").text(texto).html());
  }

  function pararPoll() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function formatarDuracao(segundos) {
    if (!isFinite(segundos) || segundos < 0) return "";
    if (segundos < 60) return Math.max(1, Math.round(segundos)) + "s";
    var min = Math.floor(segundos / 60);
    var seg = Math.round(segundos % 60);
    return min + "min" + (seg ? " " + seg + "s" : "");
  }

  function iniciarPoll() {
    var inicio = Date.now();
    definirJobRodando(true);

    function textoProgresso(p) {
      var total = p.total || 0;
      var processados = p.processados || 0;
      if (!total) return "Comparando…";

      var pct = Math.round((processados / total) * 100);
      var texto = "Comparando… " + pct + "% (" + processados + "/" + total + ")";

      var reaproveitados = p.reaproveitados || 0;
      var novos = processados - reaproveitados;
      if (novos > 0) {
        // ETA sobre as imagens que ainda não tinham vetor calculado — as reaproveitadas saem do
        // cache instantaneamente e distorceriam a estimativa
        var faltam = total - processados;
        if (faltam > 0) {
          var restante = ((Date.now() - inicio) / 1000 / novos) * faltam;
          var fmt = formatarDuracao(restante);
          if (fmt) texto += " · ~" + fmt + " restante";
        }
      }
      return texto;
    }

    function poll() {
      if (!idJob) {
        pararPoll();
        definirJobRodando(false);
        return;
      }
      Api.enviar({ url: "/api/investigacao/jobs/" + idJob, method: "GET", erroSilencioso: true })
        .done(function (j) {
          // as colunas da tabela de jobs, com os nomes que esta tela já usava
          var p = {
            rodando: j.rodando,
            cancelado: j.cancelado,
            total: j.qt_total,
            processados: j.qt_processados,
            reaproveitados: (j.tx_metricas && j.tx_metricas.reaproveitados) || 0,
            erro: j.tx_erro,
            aguardando: j.cd_status === "aguardando_vaga" || j.cd_status === "pendente",
            na_frente: j.qt_na_frente || 0,
            resultados: (j.tx_resultado && j.tx_resultado.matches) || []
          };
          if (!p.rodando) {
            pararPoll();
            Carregando.restaurar($btn);
            definirJobRodando(false);
            if (p.erro) {
              setStatus(p.erro, "error");
              return;
            }
            if (p.cancelado) {
              // um job cancelado fecha sem resultado (os embeddings já calculados ficam gravados
              // no banco, e é isso que acelera a próxima varredura — não há lista parcial)
              if (p.resultados.length) {
                mostrarResultados(p.resultados, "O que já estava calculado quando o cancelamento chegou.");
              } else {
                limparResultados();
              }
              setStatus("Comparação cancelada.", "");
              return;
            }
            mostrarResultados(p.resultados);
            var novos = (p.processados || 0) - (p.reaproveitados || 0);
            setStatus(
              "Comparação concluída em " + formatarDuracao((Date.now() - inicio) / 1000) +
              (novos > 0 ? " (" + novos + " imagens calculadas agora)." : "."),
              "success"
            );
            return;
          }
          if (p.aguardando) {
            // esperando vaga entre os jobs pesados do servidor — antes isso aparecia como
            // "Comparando…" parado, sem explicação
            var fila = p.na_frente > 0 ? " (" + p.na_frente + " na frente)" : "";
            setStatus("Na fila, aguardando vaga de processamento" + fila + "…", "");
          } else {
            setStatus(textoProgresso(p), "");
          }
          pollTimer = setTimeout(poll, 800);
        })
        .fail(function (xhr) {
          pararPoll();
          Carregando.restaurar($btn);
          definirJobRodando(false);
          setStatus(Api.mensagemErro(xhr, "Erro ao consultar o progresso."), "error");
        });
    }
    poll();
  }

  $btnCancelar.on("click", function () {
    if (!idJob) return;
    rotuloCancelar("Cancelando…");
    $btnCancelar.prop("disabled", true);
    setStatus("Cancelando…", "");
    Api.enviar({ url: "/api/investigacao/jobs/" + idJob + "/cancelar", method: "POST" })
      .done(function () {
        // o job continua ativo até o próximo lote (o cancelamento é checado na escrita de
        // progresso): quem manda parar quer saber que o próximo lote já é o último
        setStatus("Cancelamento solicitado — a varredura para no fim do lote atual.", "");
      })
      .fail(function (xhr) {
        rotuloCancelar("Cancelar");
        setStatus(Api.mensagemErro(xhr, "Não foi possível cancelar."), "error");
      });
  });

  // ---------- envio ----------

  $dropZone.on("dragover", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $dropZone.addClass("drop-zone-active");
  });

  $dropZone.on("dragleave", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $dropZone.removeClass("drop-zone-active");
  });

  $dropZone.on("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $dropZone.removeClass("drop-zone-active");
    var files = e.originalEvent.dataTransfer.files;
    if (files.length) processarArquivo(files[0]);
  });

  $arquivo.on("change", function () {
    if (this.files.length) processarArquivo(this.files[0]);
  });

  $trocar.on("click", function () {
    $arquivo.val("").click();
  });

  function processarArquivo(arquivo) {
    if (!arquivo.type || arquivo.type.indexOf("image/") !== 0) {
      recusarArquivo("Formato não suportado. Envie uma imagem.");
      return;
    }
    if (arquivo.size > TAMANHO_MAXIMO) {
      recusarArquivo("Imagem muito grande: " + U.formatarTamanho(arquivo.size) + ". O máximo é 10 MB.");
      return;
    }
    $ajudaArquivo.text("");
    arquivoSelecionado = arquivo;

    // createObjectURL em vez de FileReader/base64: a prévia aponta direto para o arquivo em
    // disco, sem carregar 10 MB de imagem como string base64 (uns 13 MB) na memória da aba
    if (urlPrevia) URL.revokeObjectURL(urlPrevia);
    urlPrevia = URL.createObjectURL(arquivo);
    $previaImg.attr("src", urlPrevia);
    $previa.removeClass("hidden");
    $dropZone.addClass("hidden");
    setStatus("");
    atualizarBotao();
  }

  // um arquivo recusado não pode deixar o anterior no lugar: sem isto o botão seguia habilitado e
  // o clique enviava a imagem que estava na memória havia dois minutos, não a que a pessoa acabou
  // de arrastar para a caixa
  function recusarArquivo(mensagem) {
    arquivoSelecionado = null;
    if (urlPrevia) {
      URL.revokeObjectURL(urlPrevia);
      urlPrevia = null;
    }
    $previaImg.removeAttr("src");
    $previa.addClass("hidden");
    $dropZone.removeClass("hidden");
    $ajudaArquivo.text(mensagem);
    atualizarBotao();
  }

  $form.on("submit", function (e) {
    e.preventDefault();
    if (!arquivoSelecionado || !idInquerito || jobRodando) return;

    pararPoll();
    limparResultados();
    Carregando.botao($btn, "Enviando…");
    setStatus("Iniciando…", "");
    enviarComparacao();
  });

  // um FormData novo por tentativa: o reenvio depois de cancelar a comparação do colega não pode
  // depender do objeto que já tinha subido numa requisição anterior
  function montarFormData() {
    var formData = new FormData();
    formData.append("arquivo", arquivoSelecionado, arquivoSelecionado.name || "referencia.jpg");
    formData.append("limite", $limite.val() || "50");
    var extracao = $aparelho.val();
    if (extracao) formData.append("extracao", extracao);
    return formData;
  }

  function enviarComparacao() {
    var ger = geracao;
    var url = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/comparar-rosto";
    Api.enviar({
      url: url,
      method: "POST",
      data: montarFormData(),
      processData: false,
      contentType: false,
      erroSilencioso: true, // a mensagem da API é mais específica que o toast genérico
    }).done(function (r) {
      if (ger !== geracao) {
        Carregando.restaurar($btn); // o inquérito mudou na subida: o botão volta ao estado normal
        return; // e o job, se o servidor aceitou, é do inquérito antigo — reaparece ao voltar nele
      }
      idJob = r.id_job;
      iniciarPoll();
    }).fail(function (xhr) {
      Carregando.restaurar($btn);
      definirJobRodando(false);
      if (xhr.status === 409) {
        oferecerCancelamento();
        return;
      }
      setStatus(Api.mensagemErro(xhr, "Erro ao iniciar comparação."), "error");
    });
  }

  /* O guard é um por inquérito (índice único da tabela de jobs). Em vez de só devolver o texto do
   * 409 — "Fulano começou uma comparação às 14h" —, a tela oferece cancelar o job do colega e
   * refazer o upload: na prática é quase sempre o que a pessoa quer, e sem isto ela teria que
   * descobrir sozinha qual botão aperta para liberar a vaga. */
  function oferecerCancelamento() {
    Api.enviar({
      url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/jobs/comparacao_facial",
      method: "GET",
      erroSilencioso: true,
    }).done(function (job) {
      if (!job || !job.rodando) {
        setStatus("Já existe uma comparação em andamento neste inquérito.", "error");
        return;
      }
      var quando = job.dt_inicio ? " desde " + U.formatarDataHora(job.dt_inicio) : "";
      window.Dialogo.confirmar({
        titulo: "Comparação já em andamento",
        mensagem: "Este inquérito já tem uma comparação facial rodando" + quando +
          ". Cancelar aquela e iniciar esta?",
        rotuloOk: "Cancelar e tentar de novo",
        perigoso: true,
      }).then(function (sim) {
        if (!sim) {
          setStatus("Comparação em andamento mantida.", "");
          return;
        }
        Api.enviar({ url: "/api/investigacao/jobs/" + job.id_job + "/cancelar", method: "POST" })
          .done(function () { aguardarLiberacao(job.id_job, 0); })
          .fail(function (xhr2) {
            setStatus(Api.mensagemErro(xhr2, "Não foi possível cancelar a comparação em andamento."), "error");
          });
      });
    });
  }

  // o cancelamento é obedecido no fim do lote em curso, e o índice único só libera quando o job
  // fecha: reenviar na hora seguinte ao "ok" levaria 409 de novo
  function aguardarLiberacao(idOutro, tentativa) {
    var ger = geracao;
    if (tentativa > 20) {
      setStatus("A outra comparação ainda não terminou de parar. Tente de novo daqui a instantes.", "error");
      return;
    }
    setStatus("Parando a comparação anterior…", "");
    Api.enviar({ url: "/api/investigacao/jobs/" + idOutro, method: "GET", erroSilencioso: true })
      .done(function (j) {
        if (ger !== geracao) return; // o analista trocou de inquérito enquanto esperava
        if (!j.rodando) {
          Carregando.botao($btn, "Enviando…");
          enviarComparacao();
          return;
        }
        pollTimer = setTimeout(function () { aguardarLiberacao(idOutro, tentativa + 1); }, 800);
      })
      .fail(function () {
        setStatus("Não foi possível confirmar a liberação.", "error");
      });
  }

  // ---------- resultados: filtros no que já foi calculado ----------

  function limparResultados() {
    resultados = [];
    $lista.empty();
    $contagem.text("");
    $hint.text("");
    $filtroInfo.text("");
    $filtros.addClass("hidden");
    $resultados.addClass("hidden");
  }

  function atualizarBadge(n) {
    $contagem.text(String(n)).removeClass("badge-ok badge-neutro").addClass(n ? "badge-ok" : "badge-neutro");
  }

  function mostrarResultados(matches, textoHint) {
    resultados = matches || [];
    $resultados.removeClass("hidden");
    if (!resultados.length) {
      atualizarBadge(0);
      $filtros.addClass("hidden");
      $filtroInfo.text("");
      $hint.text(textoHint || "");
      Estados.vazio($lista, {
        titulo: "Nenhuma imagem similar encontrada",
        dica: "O inquérito pode não ter a foto procurada, ou nenhuma das imagens passou no corte de "
          + "tamanho/formato da varredura (mínimo ~100 KB, sem WebP)."
      });
      return;
    }
    $hint.text(textoHint || "Imagens ordenadas por similaridade com a referência.");
    $filtros.removeClass("hidden");
    renderizarGrade();
  }

  function filtrarResultados() {
    var busca = $.trim($filtroBusca.val()).toLowerCase();
    var minimo = Number($filtroMin.val() || 0);
    var filtrados = resultados.filter(function (r) {
      if ((r.similaridade || 0) < minimo) return false;
      if (!busca) return true;
      return (String(r.no_arquivo || "") + " " + String(r.tx_caminho || "")).toLowerCase().indexOf(busca) !== -1;
    });
    var ordem = $filtroOrdem.val();
    if (ordem === "nome") {
      filtrados.sort(function (a, b) { return String(a.no_arquivo || "").localeCompare(String(b.no_arquivo || ""), "pt-BR"); });
    } else if (ordem === "arquivo") {
      filtrados.sort(function (a, b) { return String(a.tx_caminho || "").localeCompare(String(b.tx_caminho || ""), "pt-BR"); });
    } else {
      filtrados.sort(function (a, b) { return (b.similaridade || 0) - (a.similaridade || 0); });
    }
    return filtrados;
  }

  function renderizarGrade() {
    Estados.limpar($lista);
    $lista.empty();
    var filtrados = filtrarResultados();
    var filtrando = filtrados.length !== resultados.length;

    atualizarBadge(filtrados.length);
    $filtroInfo.text(filtrando ? "de " + resultados.length + " correspondências encontradas" : "");

    if (!filtrados.length) {
      Estados.vazio($lista, {
        filtrado: true,
        titulo: "Nenhuma correspondência com estes filtros",
        dica: "Reduza a similaridade mínima ou limpe a busca.",
        acao: { rotulo: "Limpar filtros", aoClicar: limparFiltros }
      });
      return;
    }

    var $grid = $("<div>").addClass("cmp-grid");
    filtrados.forEach(function (r) {
      $grid.append(montarCartao(r));
    });
    $lista.append($grid);
  }

  function limparFiltros() {
    $filtroBusca.val("");
    $filtroMin.val("0");
    $filtroOrdem.val("similaridade");
    renderizarGrade();
  }

  function montarCartao(r) {
    var classeSim = r.similaridade >= 80 ? "cmp-sim-alta" : r.similaridade >= 60 ? "cmp-sim-media" : "cmp-sim-baixa";

    var $card = $("<div>").addClass("cmp-card");

    // a miniatura é um botão: o arquivo inteiro está dentro do .ufdr e só sai por requisição, então
    // ampliar é sempre uma ação, não um hover
    var $ampliar = $("<button>").attr("type", "button").addClass("cmp-img-wrap")
      .attr("aria-label", "Ampliar a imagem de " + (r.no_arquivo || "arquivo"))
      .on("click", function () { abrirMidia(r); })
      .appendTo($card);
    $("<img>")
      .addClass("cmp-img")
      .attr("alt", "")
      .attr("loading", "lazy")
      .attr("src", "/api/investigacao/comparar-rosto/arquivo/" + encodeURIComponent(r.id_arquivo) + "/preview")
      .on("error", function () {
        $(this).remove();
        $ampliar.addClass("sem-previa");
      })
      .appendTo($ampliar);

    var $info = $("<div>").addClass("cmp-info").appendTo($card);
    $("<div>").addClass("cmp-similaridade").addClass(classeSim).text(r.similaridade + "%").appendTo($info);
    $("<div>").addClass("cmp-caminho").attr("title", r.tx_caminho || "")
      .text(truncarCaminho(r.tx_caminho, 50)).appendTo($info);
    $("<div>").addClass("cmp-arquivo").text(U.nomeArquivo(r.no_arquivo) || "—").appendTo($info);
    $("<div>").addClass("cmp-extracao")
      .text(r.no_dispositivo ? U.truncar(r.no_dispositivo, 40) : "Extração #" + r.id_extracao)
      .appendTo($info);

    var $acoes = $("<div>").addClass("cmp-acoes").appendTo($card);
    $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Ver origem")
      .attr("aria-label", "Ver origem de " + (r.no_arquivo || "arquivo"))
      .on("click", function () { abrirOrigem(r.id_arquivo); })
      .appendTo($acoes);
    $("<a>").addClass("btn btn-secondary").text("No aparelho")
      .attr("href", "arquivos.html?inquerito=" + encodeURIComponent(idInquerito) +
        "&extracao=" + encodeURIComponent(r.id_extracao) +
        "&busca=" + encodeURIComponent(U.nomeArquivo(r.no_arquivo) || ""))
      .attr("target", "_blank")
      .attr("rel", "noopener")
      .appendTo($acoes);

    return $card;
  }

  $filtroBusca.on("input", U.debounce(renderizarGrade, 200));
  $filtroMin.on("change", renderizarGrade);
  $filtroOrdem.on("change", renderizarGrade);

  // ---------- modal: imagem ampliada ----------

  function abrirMidia(r) {
    $("#midia-titulo").text(U.nomeArquivo(r.no_arquivo) || "Imagem encontrada");
    $midiaConteudo.empty();

    var $corpo = $("<div>").appendTo($midiaConteudo);
    Carregando.emBloco($corpo);

    // o preview de /conteudo reencoda em JPEG (abre até HEIC de iPhone), diferente da miniatura
    // quadrada de /comparar-rosto/arquivo/{id}/preview, que é cortada
    var url = "/api/investigacao/arquivos/" + encodeURIComponent(r.id_arquivo) + "/conteudo?preview=true";
    $("<img>").addClass("midia-img")
      .attr("alt", "Imagem encontrada: " + (r.no_arquivo || ""))
      .on("load", function () { Carregando.limpar($corpo); })
      .on("error", function () {
        Carregando.limpar($corpo);
        Estados.vazio($corpo, {
          titulo: "Não foi possível abrir esta imagem",
          dica: "O arquivo pode ter saído do .ufdr ou estar num formato que o navegador não decodifica."
        });
      })
      .attr("src", url)
      .appendTo($corpo);

    $("<div>").addClass("midia-acoes").append(
      $("<a>").addClass("btn btn-secondary").text("Baixar original")
        .attr("href", "/api/investigacao/arquivos/" + encodeURIComponent(r.id_arquivo) + "/conteudo?download=true"),
      $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Ver origem")
        .on("click", function () { abrirOrigem(r.id_arquivo); }),
      $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Fechar")
        .on("click", function () { fecharModal($modalMidia); })
    ).appendTo($midiaConteudo);

    abrirModal($modalMidia, $midiaFechar);
  }

  // ---------- modal: origem do arquivo ----------

  function abrirOrigem(idArquivo) {
    abrirModal($modalOrigem, $origemFechar);
    Carregando.emBloco($origemConteudo.empty());

    Api.get("/api/investigacao/comparar-rosto/arquivo/" + encodeURIComponent(idArquivo) + "/origem")
      .done(function (info) {
        Carregando.limpar($origemConteudo);
        var $campos = $("<div>").addClass("cmp-campos-modal");
        camposOrigem(info).forEach(function (campo) {
          if (campo[1] === null || campo[1] === undefined || campo[1] === "") return;
          var $f = $("<div>").addClass("field");
          $("<p>").addClass("campo-nome").text(campo[0]).appendTo($f);
          var $valor = $("<p>").addClass("campo-valor").text(campo[1]);
          if (campo[2]) $valor.addClass("campo-caminho");
          $f.append($valor).appendTo($campos);
        });

        $("<div>").addClass("midia-acoes").append(
          $("<a>").addClass("btn btn-primary")
            .attr("href", "arquivos.html?inquerito=" + encodeURIComponent(info.id_inquerito) +
              "&extracao=" + encodeURIComponent(info.id_extracao) +
              "&busca=" + encodeURIComponent(U.nomeArquivo(info.no_arquivo) || ""))
            .attr("target", "_blank")
            .attr("rel", "noopener")
            .text("Abrir nos arquivos"),
          $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Fechar")
            .on("click", function () { fecharModal($modalOrigem); })
        ).appendTo($campos);

        $origemConteudo.html($campos);
      })
      .fail(function (xhr) {
        Carregando.limpar($origemConteudo);
        Estados.erro($origemConteudo, {
          mensagem: Api.mensagemErro(xhr, "Erro ao carregar a origem."),
          aoTentarDeNovo: function () { abrirOrigem(idArquivo); }
        });
      });
  }

  // um array em vez de nove blocos `label + p` idênticos: acrescentar um campo da consulta é
  // acrescentar uma linha aqui
  function camposOrigem(info) {
    var ia = info.classificacao_ia
      ? info.classificacao_ia + (info.confianca_ia ? " (" + (info.confianca_ia * 100).toFixed(1) + "%)" : "")
      : null;
    return [
      ["Arquivo", U.nomeArquivo(info.no_arquivo), false],
      ["Caminho no dispositivo", info.tx_caminho, true],
      ["Categoria", info.no_categoria, false],
      ["Tipo MIME", info.tp_mime, false],
      ["Tamanho", U.formatarTamanho(info.qt_bytes), false],
      ["Data de modificação", U.formatarDataHora(info.dt_modificacao), false],
      ["Dispositivo", info.no_dispositivo, false],
      // NO_ARQUIVO da extração é o caminho relativo em data/ — aqui vale só o nome do .ufdr
      ["Arquivo UFDR", U.nomeArquivo(info.no_arquivo_ufdr), false],
      ["Inquérito", info.nu_inquerito, false],
      ["Classificação IA", ia, false]
    ];
  }

  $origemFechar.on("click", function () { fecharModal($modalOrigem); });
  $modalOrigem.on("click", function (e) { if (e.target === this) fecharModal($modalOrigem); });
  $midiaFechar.on("click", function () { fecharModal($modalMidia); });
  $modalMidia.on("click", function (e) { if (e.target === this) fecharModal($modalMidia); });

  // ---------- exportar o que está na tela ----------

  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "comparacao-rostos",
      dados: function () {
        var filtrados = filtrarResultados();
        if (!filtrados.length) {
          window.Dialogo.alerta("Rode uma comparação antes de exportar.");
          return null;
        }
        return {
          colunas: ["Similaridade (%)", "Arquivo", "Caminho no dispositivo", "Aparelho", "Extração", "Id do arquivo"],
          linhas: filtrados.map(function (r) {
            return [
              String(r.similaridade),
              r.no_arquivo || "",
              r.tx_caminho || "",
              r.no_dispositivo || "",
              String(r.id_extracao),
              String(r.id_arquivo)
            ];
          })
        };
      }
    });
  }

  // ---------- montar ----------

  $("#comparar-icone").html(window.Icones.html("investigacao"));
  rotuloCancelar("Cancelar");
  atualizarBotao();
  carregarInqueritos();

  // a URL.createObjectURL da prévia vive até a aba fechar; liberar na saída evita segurar o
  // buffer de uma foto de 10 MB sem motivo
  $(window).on("pagehide", function () {
    if (urlPrevia) URL.revokeObjectURL(urlPrevia);
  });
})();
