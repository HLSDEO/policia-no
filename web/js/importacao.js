(function () {
  "use strict";

  // ---------- Fontes de extração (card "Nova importação") ----------
  // Uma aba por fonte (UFDR, documento, RIF, SIMBA, IPED). O número do inquérito é único,
  // fora das abas, e vale para todas. O andamento das importações aparece na página de
  // Investigação (investigacao.html), com a lista dos inquéritos.

  var $arquivoSelect = $("#arquivo-select");
  var $inquerito = $("#inquerito-input");
  var $form = $("#extracao-form");
  var $btn = $("#extrair-btn");
  var $status = $("#form-status");
  var $ufdrUploadInput = $("#ufdr-upload-input");
  var $ufdrDropZone = $("#ufdr-drop-zone");
  var $ufdrUploadStatus = $("#ufdr-upload-status");

  var $documentoForm = $("#documento-form");
  var $documentoSelect = $("#documento-select");
  var $documentoBtn = $("#extrair-documento-btn");
  var $documentoStatus = $("#documento-status");
  var $documentoUploadInput = $("#documento-upload-input");
  var $documentoDropZone = $("#documento-drop-zone");
  var $documentoUploadStatus = $("#documento-upload-status");
  var tsDocumento = new TomSelect("#documento-select", {
    plugins: ["remove_button"],
    placeholder: "Selecione um ou mais documentos…",
  });

  var $rifForm = $("#rif-form");
  var $rifSelect = $("#rif-select");
  var $rifBtn = $("#extrair-rif-btn");
  var $rifStatus = $("#rif-status");
  var $rifUploadInput = $("#rif-upload-input");
  var $rifDropZone = $("#rif-drop-zone");
  var $rifUploadStatus = $("#rif-upload-status");

  var $simbaForm = $("#simba-form");
  var $simbaSelect = $("#simba-select");
  var $simbaBtn = $("#extrair-simba-btn");
  var $simbaStatus = $("#simba-status");
  var $simbaUploadInput = $("#simba-upload-input");
  var $simbaDropZone = $("#simba-drop-zone");
  var $simbaUploadStatus = $("#simba-upload-status");

  var $ipedForm = $("#iped-form");
  var $ipedSelect = $("#iped-select");
  var $ipedBtn = $("#extrair-iped-btn");
  var $ipedStatus = $("#iped-status");
  var $ipedContaCampo = $("#iped-conta-campo");
  var $ipedContaInput = $("#iped-conta-input");

  var delegaciaAtual = "";
  // Usuário vinculado a uma delegacia NÃO troca de delegacia nesta tela — o seletor trava na
  // dele (a regra de verdade é o delegacia_permitida no backend; aqui a tela deixa de oferecer
  // o que o servidor ia recusar com 403). Só quem não tem delegacia vincula da (ex.: admin
  // sem pasta própria) recebe a lista completa.
  var delegaciaTravada = false;

  var $abasExtracao = $(".extracao-tab[data-tab]");
  var $painelsExtracao = $(".extracao-tab-panel");

  // soltar arquivo/pasta fora de qualquer drop-zone não pode navegar nem recarregar a tela
  PastaArraste.evitarNavegacao();

  // ---------- Tipo de importação (inquérito / caso de inteligência) ----------

  var $tipoCampo = $("#imp-tipo-campo");
  var $tipoSelect = $("#imp-tipo-select");
  var $numeroCampo = $("#imp-numero-campo");
  var $numeroCasoNota = $("#imp-numero-caso-nota");

  function eCasoInteligencia() {
    return $tipoCampo.is(":visible") && $tipoSelect.val() === "caso";
  }

  function aplicarTipoImportacao() {
    var eCaso = eCasoInteligencia();
    $numeroCampo.toggleClass("hidden", eCaso).prop("hidden", eCaso);
    $numeroCasoNota.toggleClass("hidden", !eCaso).prop("hidden", !eCaso);
    if (eCaso) $inquerito.removeAttr("aria-invalid");
  }

  $tipoSelect.on("change", function () {
    aplicarTipoImportacao();
    esconderLinksAndamento();
  });

  function ativarAbaFonte(nome) {
    if (!$abasExtracao.filter('[data-tab="' + nome + '"]').length) nome = "ufdr";
    $abasExtracao.removeClass("active").attr("aria-selected", "false").attr("tabindex", "-1");
    $painelsExtracao.addClass("hidden").attr("hidden", "").attr("aria-hidden", "true");
    var $abaAtiva = $abasExtracao.filter('[data-tab="' + nome + '"]');
    $abaAtiva.addClass("active").attr("aria-selected", "true").removeAttr("tabindex");
    var $painelAtivo = $("#panel-" + nome);
    $painelAtivo.removeClass("hidden").removeAttr("hidden").removeAttr("aria-hidden");
    try { localStorage.setItem("analyser-fonte-extracao", nome); } catch (e) {}
  }

  $abasExtracao.on("click", function () {
    ativarAbaFonte($(this).data("tab"));
  });

  $abasExtracao.on("keydown", function (e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    var idx = $abasExtracao.index(this);
    var total = $abasExtracao.length;
    if (e.key === "ArrowRight") idx = (idx + 1) % total;
    else if (e.key === "ArrowLeft") idx = (idx - 1 + total) % total;
    else if (e.key === "Home") idx = 0;
    else if (e.key === "End") idx = total - 1;
    $abasExtracao.eq(idx).focus().trigger("click");
  });

  // O seletor "Inquérito / Caso de inteligência" só existe para os perfis de inteligência
  // (chefia e agente).
  // O evento chega depois de /api/auth/eu confirmar o perfil; o backend também recusa o POST
  // quando o perfil não tem pode_caso_inteligencia — isto aqui é só a parte da tela.
  $(document).on("analyser:usuario", function (e, usuario) {
    if (!(usuario && usuario.pode_caso_inteligencia)) return;
    $tipoCampo.removeClass("hidden").removeAttr("hidden");
    aplicarTipoImportacao();
  });

  // Enter no campo compartilhado de inquérito dispara a fonte ativa
  $inquerito.on("keydown", function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    var aba = $abasExtracao.filter(".active").data("tab") || "ufdr";
    if (aba === "documento") $documentoForm.trigger("submit");
    else if (aba === "rif") $rifForm.trigger("submit");
    else if (aba === "simba") $simbaForm.trigger("submit");
    else if (aba === "iped") $ipedForm.trigger("submit");
    else $form.trigger("submit");
  });

  // Bloqueio em tempo de tela: só números, pontos e traços (vale para todas as fontes)
  $inquerito.on("beforeinput", function (e) {
    if (e.originalEvent && e.originalEvent.data && /[^0-9.\-]/.test(e.originalEvent.data)) {
      e.preventDefault();
    }
  });

  // Limpa aria-invalid ao digitar + remove na hora qualquer caractere inválido (cola, arrasta, autocomplete)
  $inquerito.on("input", function () {
    var v = $(this).val() || "";
    var filtrado = v.replace(/[^0-9.\-]/g, "");
    if (filtrado !== v) $(this).val(filtrado);
    if ($.trim(filtrado)) $(this).removeAttr("aria-invalid");
  });

  var abaInicial = "ufdr";
  try {
    var abaSalva = localStorage.getItem("analyser-fonte-extracao");
    if (abaSalva && $abasExtracao.filter('[data-tab="' + abaSalva + '"]').length) abaInicial = abaSalva;
  } catch (e) {}
  ativarAbaFonte(abaInicial);

  // ---------- Helpers de validação e feedback ----------

  function obterNumeroInquerito() {
    // No modo caso de inteligência o número é gerado no backend — devolve vazio.
    if (eCasoInteligencia()) return "";
    return $.trim($inquerito.val());
  }

  function validarNumeroInquerito($statusEl) {
    // No modo caso de inteligência não há número para validar — o backend gera.
    if (eCasoInteligencia()) {
      $inquerito.removeAttr("aria-invalid");
      return "";
    }
    var numero = obterNumeroInquerito();
    // pattern ".*\S.*" — não aceita só espaços
    if (!numero || !/\S/.test(numero)) {
      var msg = "Informe o número do inquérito.";
      if ($statusEl && $statusEl.length) $statusEl.text(msg).removeClass("success").addClass("error");
      if (window.Toast) Toast.erro(msg);
      $inquerito.attr("aria-invalid", "true").focus();
      return null;
    }
    // Bloqueio em tempo de tela já filtra, mas valida de novo no envio (cola via menu, autofill, JS desligado)
    if (!/^[0-9.\-]+$/.test(numero)) {
      var msgInvalido = "Use só números, pontos e traços no número do inquérito.";
      if ($statusEl && $statusEl.length) $statusEl.text(msgInvalido).removeClass("success").addClass("error");
      if (window.Toast) Toast.erro(msgInvalido);
      $inquerito.attr("aria-invalid", "true").focus();
      return null;
    }
    $inquerito.removeAttr("aria-invalid");
    return numero;
  }

  function revelarLinkVerAndamento(idLink, mensagemToast) {
    var $link = $(idLink);
    $link.removeClass("hidden").removeAttr("hidden");
    if (window.Toast && mensagemToast) {
      Toast.sucesso(mensagemToast, {
        acao: { rotulo: "Ver andamento", aoClicar: function () { window.location.href = "investigacao.html"; } },
        duracao: 8000
      });
    }
  }

  function esconderLinksAndamento() {
    $("#form-link-investigacao, #iped-link-investigacao, #documento-link-investigacao, #rif-link-investigacao, #simba-link-investigacao")
      .addClass("hidden").attr("hidden", "");
  }

  // Esconde links ao trocar de fonte ou ao iniciar nova submissão
  $abasExtracao.on("click", esconderLinksAndamento);


  var ICONE_EXTRAIR = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  var ICONE_DOCUMENTO = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
  var ICONE_RIF = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="13" x2="8" y2="17"/><line x1="12" y1="13" x2="12" y2="17"/><line x1="16" y1="13" x2="16" y2="17"/></svg>';
  var ICONE_SIMBA = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-6 9 6"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/></svg>';
  var ICONE_IPED = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 3v18M3 8h5M3 16h5"/></svg>';

  function formatarBytes(bytes) {
    if (!bytes) return "";
    var unidades = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (bytes >= 1024 && i < unidades.length - 1) {
      bytes /= 1024;
      i++;
    }
    return bytes.toFixed(1) + " " + unidades[i];
  }

  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }

  function setStatus(text, kind) {
    $status.text(text).removeClass("success error").addClass(kind || "");
    if (kind === "error" && text && window.Toast) Toast.erro(text);
  }

  // O central responde 409 com o motivo quando a delegacia não tem nó ativo (o processamento
  // é todo do nó) — o seletor mostra exatamente o texto do servidor, não um "erro" genérico.
  function detalheErro(xhr, padrao) {
    var d = xhr && xhr.responseJSON && xhr.responseJSON.detail;
    return (typeof d === "string" && d) ? d : padrao;
  }

  function carregarArquivos() {
    var params = delegaciaAtual ? { delegacia: delegaciaAtual } : {};
    return $.getJSON("/api/investigacao/arquivos", params).done(function (arquivos) {
      $arquivoSelect.empty();
      if (!arquivos.length) {
        $arquivoSelect.append($("<option>").val("").text("Nenhum .ufdr encontrado em data/"));
        $btn.prop("disabled", true);
        $("#arquivo-help").text("Copie o arquivo .ufdr para data/.");
        return;
      }
      $("#arquivo-help").text("");
      $btn.prop("disabled", false);
      arquivos.forEach(function (a) {
        $arquivoSelect.append(
          $("<option>").val(a.nome).text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")")
        );
      });
    }).fail(function (xhr) {
      $arquivoSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#arquivo-help").text(detalheErro(xhr, "Não foi possível listar os arquivos."));
    });
  }

  function carregarDocumentosDisponiveis() {
    var params = delegaciaAtual ? { delegacia: delegaciaAtual } : {};
    return $.getJSON("/api/documentos/arquivos", params).done(function (arquivos) {
      // R304 pertence à importação do SIMBA — não aparece entre os arquivos genéricos
      var suportados = arquivos.filter(function (a) { return a.suportado && !/r304/i.test(a.nome); });
      tsDocumento.clear();
      tsDocumento.clearOptions();
      if (!suportados.length) {
        tsDocumento.settings.placeholder = "Nenhum PDF/CSV/XLS/XLSX/DOCX/TXT/HTML/ZIP em data/";
        tsDocumento.inputState();
        $documentoBtn.prop("disabled", true);
        $("#documento-help").text("Copie os documentos (ou um .zip com eles) para data/.");
        return;
      }
      $documentoBtn.prop("disabled", false);
      $("#documento-help").text("");
      tsDocumento.settings.placeholder = "Selecione um ou mais documentos…";
      tsDocumento.inputState();
      tsDocumento.addOptions(suportados.map(function (a) {
        var rotulo = a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")";
        if (a.eh_zip) rotulo += " [ZIP — expande e importa tudo]";
        return { value: a.nome, text: rotulo };
      }));
    }).fail(function (xhr) {
      tsDocumento.clear();
      tsDocumento.clearOptions();
      tsDocumento.settings.placeholder = "Indisponível no central";
      tsDocumento.inputState();
      $("#documento-help").text(detalheErro(xhr, "Não foi possível listar os documentos."));
    });
  }

  function carregarRifsDisponiveis() {
    var params = delegaciaAtual ? { delegacia: delegaciaAtual } : {};
    return $.getJSON("/api/investigacao/rifs-disponiveis", params).done(function (rifs) {
      $rifSelect.empty();
      if (!rifs.length) {
        $rifSelect.append($("<option>").val("").text("Nenhuma pasta de RIF em data/"));
        $rifBtn.prop("disabled", true);
        $("#rif-help").text("Copie a pasta do RIF (com RIF no nome) para data/.");
        return;
      }
      $rifBtn.prop("disabled", false);
      $("#rif-help").text("");
      rifs.forEach(function (r) {
        var partes = [];
        if (r.tem_comunicacoes) partes.push("comunicações");
        if (r.tem_envolvidos) partes.push("envolvidos");
        if (r.tem_ocorrencias) partes.push("ocorrências");
        if (r.tem_pdf) partes.push("PDF");
        var extra = (r.nu_rif ? "RIF " + r.nu_rif : "");
        if (partes.length) extra += (extra ? " · " : "") + partes.join(", ");
        $rifSelect.append(
          $("<option>").val(r.pasta).text(r.pasta + (extra ? " (" + extra + ")" : ""))
        );
      });
    }).fail(function (xhr) {
      $rifSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#rif-help").text(detalheErro(xhr, "Não foi possível listar as pastas de RIF."));
    });
  }

  function carregarSimbaDisponiveis() {
    var params = delegaciaAtual ? { delegacia: delegaciaAtual } : {};
    return $.getJSON("/api/investigacao/simba-disponiveis", params).done(function (arquivos) {
      $simbaSelect.empty();
      if (!arquivos.length) {
        $simbaSelect.append($("<option>").val("").text("Nenhum arquivo R304 em data/"));
        $simbaBtn.prop("disabled", true);
        $("#simba-help").text("Copie o CSV do SIMBA (o nome contém R304) para data/.");
        return;
      }
      $simbaBtn.prop("disabled", false);
      $("#simba-help").text("");
      arquivos.forEach(function (a) {
        $simbaSelect.append(
          $("<option>").val(a.nome).text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")")
        );
      });
    }).fail(function (xhr) {
      $simbaSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#simba-help").text(detalheErro(xhr, "Não foi possível listar os arquivos R304."));
    });
  }

  function carregarIpedDisponiveis() {
    var params = delegaciaAtual ? { delegacia: delegaciaAtual } : {};
    var pDisponivel = $.getJSON("/api/investigacao/iped-disponivel").done(function (r) {
      if (r.disponivel) return;
      $ipedSelect.empty().append($("<option>").val("").text("Indisponível — rodará no nó"));
      $ipedBtn.prop("disabled", true);
      $("#iped-help").text(r.motivo || "Importação IPED indisponível neste servidor.");
    }).fail(function () {
      // silencioso: a listagem principal ainda tenta carregar
    });
    var pLista = $.getJSON("/api/investigacao/iped-disponiveis", params).done(function (pastas) {
      // Se o aviso "indisponível no central" já preencheu, não sobrescreve
      if ($("#iped-help").text().indexOf("central") !== -1) return;
      $ipedSelect.empty();
      if (!pastas.length) {
        $ipedSelect.append($("<option>").val("").text("Nenhuma pasta de caso do IPED em data/"));
        $ipedBtn.prop("disabled", true);
        $("#iped-help").text("Copie a pasta do caso processado pelo IPED (com IPEDConfig.txt/index dentro) para data/.");
        $ipedContaCampo.addClass("hidden").attr("hidden", "");
        return;
      }
      $ipedBtn.prop("disabled", false);
      $("#iped-help").text("");
      pastas.forEach(function (p) {
        $ipedSelect.append($("<option>").val(p.tx_pasta).text(p.tx_pasta));
      });
      $ipedSelect.trigger("change");
    }).fail(function () {
      $ipedSelect.empty().append($("<option>").val("").text("Erro ao carregar IPED"));
      $("#iped-help").text("Não foi possível listar as pastas do IPED.");
    });
    return $.when(pDisponivel, pLista);
  }

  // Ao escolher a pasta, pré-visualiza a conta que a importação vai detectar e usar para
  // renomear a pasta — o investigador confirma ou edita antes de disparar a extração de
  // verdade (ver funcoes/iped_utils.detectar_conta_iped). Best-effort: silencioso se falhar.
  var ipedDetectando = false;
  $ipedSelect.on("change", function () {
    var pasta = $ipedSelect.val();
    $ipedContaInput.val("");
    $ipedContaCampo.addClass("hidden").attr("hidden", "");
    if (!pasta) {
      $("#iped-help").text($("#iped-help").text().indexOf("central") !== -1 ? $("#iped-help").text() : "");
      $ipedBtn.prop("disabled", $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().val() === "");
      ipedDetectando = false;
      return;
    }
    // Verifica se lista está vazia (mensagem de "Nenhuma pasta") — não tenta detectar
    var pastasValida = !($ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().text().indexOf("Nenhuma") === 0);
    if (!pastasValida) return;
    ipedDetectando = true;
    // desabilita importação até a detecção terminar para evitar corrida
    $ipedBtn.prop("disabled", true);
    $("#iped-help").html('<span class="spinner" style="display:inline-block;vertical-align:middle;margin-right:6px;"></span> Detectando conta…');
    $.ajax({
      url: "/api/investigacao/iped-detectar-conta",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ arquivo: pasta, delegacia: delegaciaAtual || null })
    }).done(function (r) {
      if (r.conta) $ipedContaInput.val(r.conta);
      $ipedContaCampo.removeClass("hidden").removeAttr("hidden");
    }).fail(function () {
      // silencioso: campo continua oculto, usuário pode digitar manualmente se quiser
      $ipedContaCampo.removeClass("hidden").removeAttr("hidden");
    }).always(function () {
      ipedDetectando = false;
      var vazio = $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().val() === "";
      var semPastas = $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().text().indexOf("Nenhuma") === 0;
      $ipedBtn.prop("disabled", vazio || semPastas);
      if (!vazio && !semPastas) $("#iped-help").text("");
    });
  });

  // ---------- Envio de documento pelo navegador (aba "Arquivo genérico") ----------
  // Antes disto, o arquivo só entrava sendo copiado à mão para data/<DELEGACIA>/ no servidor — o
  // que a maioria dos investigadores não alcança. O envio grava naquela mesma pasta
  // (POST /api/documentos/upload) e recarrega a lista; a importação em si continua sendo o
  // formulário abaixo, com o arquivo já selecionado.

  // Limite vigente vem de GET /api/documentos/upload/limite (UPLOAD_DOCUMENTO_MB, ajustável
  // em Administração → Desempenho). A conferência aqui é só para o usuário saber na hora, sem
  // esperar o upload inteiro subir para levar 400 — quem decide é o servidor. O .zip é a
  // exceção, como o .ufdr: sem limite (vai por POST /api/documentos/upload-zip).
  var MAX_BYTES_UPLOAD = 50 * 1024 * 1024;
  var LIMITE_UPLOAD_MB = 50;
  // imagens entram como prova avulsa SEM camada de texto — espelha _IMAGENS_PROVA do back
  var EXTENSOES_UPLOAD = ["pdf", "csv", "xls", "xlsx", "docx", "txt", "html", "htm", "zip", "jpg", "jpeg", "png"];

  function textoAjudaUploadSimba() {
    return "O CSV vai para a pasta da delegacia selecionada acima e já aparece escolhido na lista abaixo. Até " + LIMITE_UPLOAD_MB + " MB por arquivo (ajustável em Administração → Desempenho). Só o relatório R304 é aceito — o cabeçalho é conferido na hora.";
  }

  function textoAjudaUploadRif() {
    return "A pasta inteira (os CSVs e o PDF) vai para a pasta da delegacia selecionada acima e já aparece escolhida na lista abaixo. Até " + LIMITE_UPLOAD_MB + " MB por arquivo (ajustável em Administração → Desempenho). Só CSV e PDF entram — os demais arquivos da pasta são ignorados. O import usa *_Comunicacoes, *_Envolvidos, *_Ocorrencias e o 1º PDF.";
  }

  function aplicarLimiteUpload(mb) {
    mb = parseInt(mb, 10);
    if (!mb || mb < 1 || mb > 500) return;
    LIMITE_UPLOAD_MB = mb;
    MAX_BYTES_UPLOAD = mb * 1024 * 1024;
    var $ajuda = $("#documento-upload-help");
    if ($ajuda.length) {
      var texto = $ajuda.text();
      if (/Até \d+ MB por arquivo/.test(texto)) {
        $ajuda.text(texto.replace(/Até \d+ MB por arquivo/, "Até " + mb + " MB por arquivo"));
      }
    }
    var $simba = $("#simba-upload-help");
    if ($simba.length) $simba.text(textoAjudaUploadSimba());
    var $rif = $("#rif-upload-help");
    if ($rif.length) $rif.text(textoAjudaUploadRif());
  }

  $.getJSON("/api/documentos/upload/limite")
    .done(function (r) { if (r && r.limite_mb) aplicarLimiteUpload(r.limite_mb); });

  var enviandoDocumentos = false;

  function statusUpload(text, kind) {
    $documentoUploadStatus.text(text || "").removeClass("success error").addClass(kind || "");
  }

  function extensaoDe(nome) {
    var ponto = (nome || "").lastIndexOf(".");
    return ponto === -1 ? "" : nome.slice(ponto + 1).toLowerCase();
  }

  function listarResultadoUpload(enviados, falhas) {
    $documentoUploadStatus.nextAll(".upload-lista").remove();
    if (!enviados.length && !falhas.length) return;
    var $lista = $('<ul class="upload-lista">');
    enviados.forEach(function (a) {
      var $li = $("<li>")
        .append($('<span class="upload-marca" aria-hidden="true">').text("✓"))
        .append($("<span>").text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"));
      // duplicado anunciado: o servidor nunca sobrescreve — avisa que o nome mudou
      if (a.renomeado) {
        $li.append($("<em>").addClass("upload-renomeado")
          .text(" — já existia um com este nome, entrou como está acima"));
        $li.addClass("upload-aviso");
      }
      $li.appendTo($lista);
    });
    falhas.forEach(function (msg) {
      $('<li class="upload-erro">')
        .append($('<span class="upload-marca" aria-hidden="true">').text("✗"))
        .append($("<span>").text(msg))
        .appendTo($lista);
    });
    $lista.insertAfter($documentoUploadStatus);
  }

  // Um arquivo por requisição, e não todos num FormData só: dá progresso ("2/5") e erro por
  // arquivo, e mantém cada corpo dentro do client_max_body_size do nginx (ver nginx/default.conf).
  // O .zip é a exceção: vai por POST /api/documentos/upload-zip, sem teto (como o .ufdr).
  function enviarDocumentos(arquivos) {
    if (enviandoDocumentos || !arquivos || !arquivos.length) return;

    var fila = [];
    var falhas = [];
    for (var i = 0; i < arquivos.length; i++) {
      var arquivo = arquivos[i];
      if (EXTENSOES_UPLOAD.indexOf(extensaoDe(arquivo.name)) === -1) {
        falhas.push(arquivo.name + ": tipo não suportado — envie PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG/PNG ou ZIP.");
      } else if (extensaoDe(arquivo.name) !== "zip" && arquivo.size > MAX_BYTES_UPLOAD) {
        falhas.push(arquivo.name + ": " + formatarBytes(arquivo.size) + " — o limite é " + LIMITE_UPLOAD_MB + " MB por arquivo.");
      } else if (!arquivo.size) {
        falhas.push(arquivo.name + ": arquivo vazio.");
      } else {
        fila.push(arquivo);
      }
    }

    if (!fila.length) {
      statusUpload("Nenhum arquivo pôde ser enviado.", "error");
      listarResultadoUpload([], falhas);
      if (window.Toast) Toast.erro(falhas[0]);
      return;
    }

    enviandoDocumentos = true;
    $documentoDropZone.addClass("disabled");
    $documentoBtn.prop("disabled", true);
    $documentoUploadStatus.nextAll(".upload-lista").remove();

    var enviados = [];
    var indice = 0;

    function enviarProximo() {
      if (indice >= fila.length) {
        enviandoDocumentos = false;
        $documentoDropZone.removeClass("disabled");
        listarResultadoUpload(enviados, falhas);
        if (!enviados.length) {
          statusUpload("Nenhum arquivo pôde ser enviado.", "error");
          $documentoBtn.prop("disabled", false);
          if (window.Toast) Toast.erro(falhas[0]);
          return;
        }
        var msg = enviados.length === 1
          ? "1 arquivo enviado para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + "."
          : enviados.length + " arquivos enviados para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + ".";
        if (falhas.length) msg += " " + falhas.length + " recusado(s).";
        statusUpload(msg, falhas.length ? "error" : "success");
        if (window.Toast) Toast.sucesso(msg);
        // recarrega a lista e já deixa marcado o que subiu — o investigador só confirma e importa
        carregarDocumentosDisponiveis().always(function () {
          enviados.forEach(function (a) { tsDocumento.addItem(a.nome, true); });
          tsDocumento.refreshItems();
          // reabilita mesmo se a listagem falhar: com o upload aceito, há documento para importar
          $documentoBtn.prop("disabled", false);
        });
        return;
      }

      var arquivo = fila[indice];
      statusUpload("Enviando " + (indice + 1) + "/" + fila.length + ": " + arquivo.name + "…");

      var dados = new FormData();
      dados.append("arquivos", arquivo, arquivo.name);
      if (delegaciaAtual) dados.append("delegacia", delegaciaAtual);

      $.ajax({
        url: extensaoDe(arquivo.name) === "zip" ? "/api/documentos/upload-zip" : "/api/documentos/upload",
        method: "POST",
        data: dados,
        processData: false,
        contentType: false,
      })
        .done(function (resp) {
          enviados = enviados.concat(resp.arquivos || []);
          falhas = falhas.concat(resp.falhas || []);
        })
        .fail(function (xhr) {
          var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
          falhas.push(arquivo.name + ": " + msg);
        })
        .always(function () {
          indice++;
          enviarProximo();
        });
    }

    enviarProximo();
  }

  $documentoUploadInput.on("change", function () {
    enviarDocumentos(this.files);
    // zera o input para que reenviar o MESMO arquivo dispare "change" de novo
    this.value = "";
  });

  $documentoDropZone.on("dragover", function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (!enviandoDocumentos) $documentoDropZone.addClass("drop-zone-active");
  });

  $documentoDropZone.on("dragleave", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $documentoDropZone.removeClass("drop-zone-active");
  });

  $documentoDropZone.on("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $documentoDropZone.removeClass("drop-zone-active");
    if (enviandoDocumentos) return;
    var dt = e.originalEvent.dataTransfer;
    if (!dt) return;
    // pasta solta inteira: recolhe os arquivos de dentro (inclusive de subpastas) e achatam
    PastaArraste.coletar(dt).always(function (arquivos) {
      if (arquivos && arquivos.length) enviarDocumentos(arquivos);
    });
  });

  // ---------- Envio do R304 pelo navegador (aba "Bancário SIMBA R304") ----------
  // Mesmo desenho do envio da aba "Arquivo genérico" logo acima, mas para o CSV do SIMBA:
  // grava em data/<DELEGACIA>/ via POST /api/investigacao/simba-upload (que confere o cabeçalho
  // do R304 na hora) e recarrega a lista do SIMBA já com o arquivo escolhido. O seletor é único,
  // então de uma leva de uploads quem fica escolhido é o último que subiu.

  var enviandoSimba = false;

  function statusSimbaUpload(text, kind) {
    $simbaUploadStatus.text(text || "").removeClass("success error").addClass(kind || "");
  }

  function listarResultadoSimbaUpload(enviados, falhas) {
    $simbaUploadStatus.nextAll(".upload-lista").remove();
    if (!enviados.length && !falhas.length) return;
    var $lista = $('<ul class="upload-lista">');
    enviados.forEach(function (a) {
      $("<li>")
        .append($('<span class="upload-marca" aria-hidden="true">').text("✓"))
        .append($("<span>").text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"))
        .appendTo($lista);
    });
    falhas.forEach(function (msg) {
      $('<li class="upload-erro">')
        .append($('<span class="upload-marca" aria-hidden="true">').text("✗"))
        .append($("<span>").text(msg))
        .appendTo($lista);
    });
    $lista.insertAfter($simbaUploadStatus);
  }

  function enviarSimba(arquivos) {
    if (enviandoSimba || !arquivos || !arquivos.length) return;

    var fila = [];
    var falhas = [];
    for (var i = 0; i < arquivos.length; i++) {
      var arquivo = arquivos[i];
      if (extensaoDe(arquivo.name) !== "csv") {
        falhas.push(arquivo.name + ": envie o CSV do relatório R304 do SIMBA.");
      } else if (arquivo.size > MAX_BYTES_UPLOAD) {
        falhas.push(arquivo.name + ": " + formatarBytes(arquivo.size) + " — o limite é " + LIMITE_UPLOAD_MB + " MB por arquivo.");
      } else if (!arquivo.size) {
        falhas.push(arquivo.name + ": arquivo vazio.");
      } else {
        fila.push(arquivo);
      }
    }

    if (!fila.length) {
      statusSimbaUpload("Nenhum arquivo pôde ser enviado.", "error");
      listarResultadoSimbaUpload([], falhas);
      if (window.Toast) Toast.erro(falhas[0]);
      return;
    }

    enviandoSimba = true;
    $simbaDropZone.addClass("disabled");
    $simbaBtn.prop("disabled", true);
    $simbaUploadStatus.nextAll(".upload-lista").remove();

    var enviados = [];
    var indice = 0;

    function enviarProximo() {
      if (indice >= fila.length) {
        enviandoSimba = false;
        $simbaDropZone.removeClass("disabled");
        listarResultadoSimbaUpload(enviados, falhas);
        if (!enviados.length) {
          statusSimbaUpload("Nenhum arquivo pôde ser enviado.", "error");
          $simbaBtn.prop("disabled", false);
          if (window.Toast) Toast.erro(falhas[0]);
          return;
        }
        var msg = enviados.length === 1
          ? "1 arquivo enviado para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + "."
          : enviados.length + " arquivos enviados para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + ".";
        if (falhas.length) msg += " " + falhas.length + " recusado(s).";
        statusSimbaUpload(msg, falhas.length ? "error" : "success");
        if (window.Toast) Toast.sucesso(msg);
        // recarrega a lista e já deixa escolhido o que subiu — o investigador só confirma e importa
        carregarSimbaDisponiveis().always(function () {
          var ultimo = enviados[enviados.length - 1];
          if (ultimo) $simbaSelect.val(ultimo.nome);
          // reabilita mesmo se a listagem falhar: com o upload aceito, há arquivo para importar
          $simbaBtn.prop("disabled", false);
        });
        return;
      }

      var arquivo = fila[indice];
      statusSimbaUpload("Enviando " + (indice + 1) + "/" + fila.length + ": " + arquivo.name + "…");

      var dados = new FormData();
      dados.append("arquivos", arquivo, arquivo.name);
      if (delegaciaAtual) dados.append("delegacia", delegaciaAtual);

      $.ajax({
        url: "/api/investigacao/simba-upload",
        method: "POST",
        data: dados,
        processData: false,
        contentType: false,
      })
        .done(function (resp) {
          enviados = enviados.concat(resp.arquivos || []);
          falhas = falhas.concat(resp.falhas || []);
        })
        .fail(function (xhr) {
          var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
          falhas.push(arquivo.name + ": " + msg);
        })
        .always(function () {
          indice++;
          enviarProximo();
        });
    }

    enviarProximo();
  }

  $simbaUploadInput.on("change", function () {
    enviarSimba(this.files);
    // zera o input para que reenviar o MESMO arquivo dispare "change" de novo
    this.value = "";
  });

  $simbaDropZone.on("dragover", function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (!enviandoSimba) $simbaDropZone.addClass("drop-zone-active");
  });

  $simbaDropZone.on("dragleave", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $simbaDropZone.removeClass("drop-zone-active");
  });

  $simbaDropZone.on("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $simbaDropZone.removeClass("drop-zone-active");
    if (enviandoSimba) return;
    var dt = e.originalEvent.dataTransfer;
    if (dt && dt.files && dt.files.length) enviarSimba(dt.files);
  });

  // ---------- Envio da pasta do RIF pelo navegador (aba "RIF - COAF") ----------
  // O RIF é uma PASTA, e não um arquivo: o envio manda a pasta inteira (input webkitdirectory
  // ou arrastar a pasta), com o caminho relativo de cada arquivo (webkitRelativePath). O servidor
  // (POST /api/investigacao/rif-upload, uma requisição por pasta) recria a pasta em
  // data/<DELEGACIA>/ e, ao concluir, a lista do RIF recarrega com a pasta já escolhida.

  var enviandoRif = false;

  function statusRifUpload(text, kind) {
    $rifUploadStatus.text(text || "").removeClass("success error").addClass(kind || "");
  }

  function listarResultadoRifUpload(pastas, falhas) {
    $rifUploadStatus.nextAll(".upload-lista").remove();
    if (!pastas.length && !falhas.length) return;
    var $lista = $('<ul class="upload-lista">');
    pastas.forEach(function (r) {
      $("<li>")
        .append($('<span class="upload-marca" aria-hidden="true">').text("✓"))
        .append($("<span>").text("Pasta " + r.pasta + " — " + r.arquivos.length + " arquivo(s)"))
        .appendTo($lista);
      r.arquivos.forEach(function (a) {
        $("<li>")
          .append($('<span class="upload-marca" aria-hidden="true">').text(""))
          .append($("<span>").text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"))
          .appendTo($lista);
      });
    });
    falhas.forEach(function (msg) {
      $('<li class="upload-erro">')
        .append($('<span class="upload-marca" aria-hidden="true">').text("✗"))
        .append($("<span>").text(msg))
        .appendTo($lista);
    });
    $lista.insertAfter($rifUploadStatus);
  }

  function enviarRif(arquivos) {
    if (enviandoRif || !arquivos || !arquivos.length) return;

    var falhas = [];
    var porPasta = {};
    for (var i = 0; i < arquivos.length; i++) {
      var arquivo = arquivos[i];
      // __caminho vem do PastaArraste: pasta solta por drag não tem webkitRelativePath
      var rel = arquivo.webkitRelativePath || arquivo.__caminho || arquivo.name || "";
      var partes = rel.replace(/\\/g, "/").split("/");
      if (partes.length < 2) {
        falhas.push((arquivo.name || "(sem nome)") + ": envie a pasta inteira do RIF — arraste a pasta ou use o botão de escolher pasta.");
        continue;
      }
      if (!/\.(csv|pdf)$/i.test(arquivo.name)) {
        falhas.push(arquivo.name + ": só CSV e PDF entram na pasta do RIF.");
        continue;
      }
      if (arquivo.size > MAX_BYTES_UPLOAD) {
        falhas.push(arquivo.name + ": " + formatarBytes(arquivo.size) + " — o limite é " + LIMITE_UPLOAD_MB + " MB por arquivo.");
        continue;
      }
      if (!arquivo.size) {
        falhas.push(arquivo.name + ": arquivo vazio.");
        continue;
      }
      var pasta = partes[0];
      (porPasta[pasta] = porPasta[pasta] || []).push(arquivo);
    }

    var nomesPastas = Object.keys(porPasta);
    if (!nomesPastas.length) {
      statusRifUpload("Nenhuma pasta pôde ser enviada.", "error");
      listarResultadoRifUpload([], falhas);
      if (window.Toast) Toast.erro(falhas[0]);
      return;
    }

    enviandoRif = true;
    $rifDropZone.addClass("disabled");
    $rifBtn.prop("disabled", true);
    $rifUploadStatus.nextAll(".upload-lista").remove();

    var enviadas = [];
    var indice = 0;

    function enviarProxima() {
      if (indice >= nomesPastas.length) {
        enviandoRif = false;
        $rifDropZone.removeClass("disabled");
        listarResultadoRifUpload(enviadas, falhas);
        if (!enviadas.length) {
          statusRifUpload("Nenhuma pasta pôde ser enviada.", "error");
          $rifBtn.prop("disabled", false);
          if (window.Toast) Toast.erro(falhas[0]);
          return;
        }
        var msg = enviadas.length === 1
          ? "Pasta " + enviadas[0].pasta + " enviada para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + "."
          : enviadas.length + " pastas enviadas para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + ".";
        if (falhas.length) msg += " " + falhas.length + " recusa(s).";
        statusRifUpload(msg, falhas.length ? "error" : "success");
        if (window.Toast) Toast.sucesso(msg);
        // recarrega a lista e já deixa escolhida a última pasta que subiu — só falta confirmar e importar
        carregarRifsDisponiveis().always(function () {
          var ultima = enviadas[enviadas.length - 1];
          if (ultima) $rifSelect.val(ultima.pasta);
          // reabilita mesmo se a listagem falhar: com o upload aceito, há pasta para importar
          $rifBtn.prop("disabled", false);
        });
        return;
      }

      var pasta = nomesPastas[indice];
      statusRifUpload("Enviando " + (indice + 1) + "/" + nomesPastas.length + ": pasta " + pasta + "…");

      var dados = new FormData();
      porPasta[pasta].forEach(function (arquivo) {
        dados.append("arquivos", arquivo, arquivo.webkitRelativePath || arquivo.__caminho || arquivo.name);
      });
      if (delegaciaAtual) dados.append("delegacia", delegaciaAtual);

      $.ajax({
        url: "/api/investigacao/rif-upload",
        method: "POST",
        data: dados,
        processData: false,
        contentType: false,
      })
        .done(function (resp) {
          enviadas.push({ pasta: resp.pasta, arquivos: resp.arquivos || [] });
          falhas = falhas.concat(resp.falhas || []);
        })
        .fail(function (xhr) {
          var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
          falhas.push(pasta + ": " + msg);
        })
        .always(function () {
          indice++;
          enviarProxima();
        });
    }

    enviarProxima();
  }

  $rifUploadInput.on("change", function () {
    enviarRif(this.files);
    // zera o input para que reenviar a MESMA pasta dispare "change" de novo
    this.value = "";
  });

  $rifDropZone.on("dragover", function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (!enviandoRif) $rifDropZone.addClass("drop-zone-active");
  });

  $rifDropZone.on("dragleave", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $rifDropZone.removeClass("drop-zone-active");
  });

  $rifDropZone.on("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $rifDropZone.removeClass("drop-zone-active");
    if (enviandoRif) return;
    var dt = e.originalEvent.dataTransfer;
    if (!dt) return;
    // o RIF é uma PASTA: dataTransfer.files não traz o conteúdo dela — PastaArraste desce a
    // pasta via webkitGetAsEntry e devolve os arquivos com o caminho relativo (__caminho)
    PastaArraste.coletar(dt).always(function (arquivos) {
      if (arquivos && arquivos.length) enviarRif(arquivos);
    });
  });

  // ---------- Envio de .ufdr pelo navegador (aba "Indexação Cellebrite") ----------
  // Mesmo desenho dos envios acima, mas é um dos dois uploads sem limite de tamanho (o outro
  // é o .zip, que vai por POST /api/documentos/upload-zip): um .ufdr passa
  // de dezenas de GB, então o teto é o location próprio do nginx (client_max_body_size 0) e não
  // UPLOAD_DOCUMENTO_MB — a rota POST /api/investigacao/ufdr-upload grava em blocos e o envio
  // mostra o progresso em bytes. Um arquivo por requisição, e fechar a página cancela o envio
  // (sem retomada — para arquivos muito grandes o texto de ajuda recomenda a cópia de rede).

  var enviandoUfdr = false;

  function statusUfdrUpload(text, kind) {
    $ufdrUploadStatus.text(text || "").removeClass("success error").addClass(kind || "");
  }

  function listarResultadoUfdrUpload(enviados, falhas) {
    $ufdrUploadStatus.nextAll(".upload-lista").remove();
    if (!enviados.length && !falhas.length) return;
    var $lista = $('<ul class="upload-lista">');
    enviados.forEach(function (a) {
      $("<li>")
        .append($('<span class="upload-marca" aria-hidden="true">').text("✓"))
        .append($("<span>").text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"))
        .appendTo($lista);
    });
    falhas.forEach(function (msg) {
      $('<li class="upload-erro">')
        .append($('<span class="upload-marca" aria-hidden="true">').text("✗"))
        .append($("<span>").text(msg))
        .appendTo($lista);
    });
    $lista.insertAfter($ufdrUploadStatus);
  }

  // Sorteia o token que dá nome ao .parcial no servidor e permite acompanhar a gravação por
  // GET /api/investigacao/ufdr-upload/progresso (sem estado em memória na API). randomUUID pode
  // faltar em contexto http (não-TLS) — aí cai no sorteio simples, que basta para não colidir.
  function gerarTokenUfdrUpload() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      try { return window.crypto.randomUUID().replace(/-/g, ""); } catch (e) {}
    }
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 10);
  }

  function enviarUfdr(arquivos) {
    if (enviandoUfdr || !arquivos || !arquivos.length) return;

    var falhas = [];
    var fila = [];
    for (var i = 0; i < arquivos.length; i++) {
      var arquivo = arquivos[i];
      if (extensaoDe(arquivo.name) !== "ufdr") {
        falhas.push(arquivo.name + ": envie o arquivo .ufdr.");
      } else if (!arquivo.size) {
        falhas.push(arquivo.name + ": arquivo vazio.");
      } else {
        fila.push(arquivo);
      }
    }

    if (!fila.length) {
      statusUfdrUpload("Nenhum arquivo pôde ser enviado.", "error");
      listarResultadoUfdrUpload([], falhas);
      if (window.Toast) Toast.erro(falhas[0]);
      return;
    }

    enviandoUfdr = true;
    $ufdrDropZone.addClass("disabled");
    $btn.prop("disabled", true);
    $ufdrUploadStatus.nextAll(".upload-lista").remove();

    var enviados = [];
    var indice = 0;

    function enviarProximo() {
      if (indice >= fila.length) {
        enviandoUfdr = false;
        $ufdrDropZone.removeClass("disabled");
        listarResultadoUfdrUpload(enviados, falhas);
        if (!enviados.length) {
          statusUfdrUpload("Nenhum arquivo pôde ser enviado.", "error");
          $btn.prop("disabled", false);
          if (window.Toast) Toast.erro(falhas[0]);
          return;
        }
        var msg = enviados.length === 1
          ? "1 arquivo enviado para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + "."
          : enviados.length + " arquivos enviados para data/" + (delegaciaAtual ? delegaciaAtual + "/" : "") + ".";
        if (falhas.length) msg += " " + falhas.length + " recusado(s).";
        statusUfdrUpload(msg, falhas.length ? "error" : "success");
        if (window.Toast) Toast.sucesso(msg);
        // recarrega a lista e já deixa escolhido o que subiu — o investigador só confirma e importa
        carregarArquivos().always(function () {
          var ultimo = enviados[enviados.length - 1];
          if (ultimo) $arquivoSelect.val(ultimo.nome);
          // reabilita mesmo se a listagem falhar: com o upload aceito, há .ufdr para importar
          $btn.prop("disabled", false);
        });
        return;
      }

      var arquivo = fila[indice];
      var token = gerarTokenUfdrUpload();
      var terminouEnvio = false;
      var finalizado = false;
      var pollTimer = null;
      statusUfdrUpload("Enviando " + (indice + 1) + "/" + fila.length + ": " + arquivo.name + "…");

      // O progresso do navegador (xhr.upload) cobre SÓ o envio dos bytes. Num .ufdr de dezenas de
      // GB, gravar o spool em data/ no servidor demora o mesmo tanto e acontece DEPOIS — sem o
      // polling abaixo, a tela ficaria parada em "Enviando 35.0 GB de 35.0 GB…" com o arquivo
      // ainda crescendo na pasta. GET .../progresso devolve o tamanho do .parcial em disco.
      function pararPollGravacao() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      }

      function mostrarGravacaoNoServidor() {
        if (finalizado || !terminouEnvio || pollTimer) return;
        statusUfdrUpload("Recebido — gravando no servidor…");
        pollTimer = setInterval(function () {
          $.getJSON("/api/investigacao/ufdr-upload/progresso", {
            token: token,
            delegacia: delegaciaAtual || undefined,
          })
            .done(function (r) {
              if (finalizado || !terminouEnvio) return;
              statusUfdrUpload("Gravando no servidor: " + formatarBytes(r.bytes) + " de " + formatarBytes(arquivo.size) + "…");
            })
            .fail(function () {
              // 404 (upload ainda não começou a gravar ou já terminou) e erros de rede:
              // silencioso — a linha de status continua no "Recebido — gravando…" até a resposta
            });
        }, 2000);
      }

      var dados = new FormData();
      dados.append("arquivo", arquivo, arquivo.name);
      dados.append("token", token);
      if (delegaciaAtual) dados.append("delegacia", delegaciaAtual);

      $.ajax({
        url: "/api/investigacao/ufdr-upload",
        method: "POST",
        data: dados,
        processData: false,
        contentType: false,
        xhr: function () {
          var xhr = $.ajaxSettings.xhr();
          if (xhr.upload) {
            xhr.upload.onprogress = function (e) {
              if (!e.lengthComputable) return;
              if (e.loaded >= e.total) {
                terminouEnvio = true;
                mostrarGravacaoNoServidor();
              } else {
                statusUfdrUpload("Enviando " + formatarBytes(e.loaded) + " de " + formatarBytes(e.total) + "…");
              }
            };
            // rede de segurança: se o evento final de progresso não vier, o onload dispara na
            // conclusão do envio do corpo e inicia a fase de gravação no servidor do mesmo jeito
            xhr.upload.onload = function () {
              terminouEnvio = true;
              mostrarGravacaoNoServidor();
            };
          }
          return xhr;
        },
      })
        .done(function (resp) {
          enviados = enviados.concat(resp.arquivos || []);
          falhas = falhas.concat(resp.falhas || []);
        })
        .fail(function (xhr) {
          var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
          falhas.push(arquivo.name + ": " + msg);
        })
        .always(function () {
          finalizado = true;
          terminouEnvio = true;
          pararPollGravacao();
          indice++;
          enviarProximo();
        });
    }

    enviarProximo();
  }

  $ufdrUploadInput.on("change", function () {
    enviarUfdr(this.files);
    // zera o input para que reenviar o MESMO arquivo dispare "change" de novo
    this.value = "";
  });

  $ufdrDropZone.on("dragover", function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (!enviandoUfdr) $ufdrDropZone.addClass("drop-zone-active");
  });

  $ufdrDropZone.on("dragleave", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $ufdrDropZone.removeClass("drop-zone-active");
  });

  $ufdrDropZone.on("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    $ufdrDropZone.removeClass("drop-zone-active");
    if (enviandoUfdr) return;
    var dt = e.originalEvent.dataTransfer;
    if (dt && dt.files && dt.files.length) enviarUfdr(dt.files);
  });

  $documentoForm.on("submit", function (e) {
    e.preventDefault();
    var arquivos = tsDocumento.getValue();
    var numero = validarNumeroInquerito($documentoStatus);
    if (numero === null) return;

    function statusDocumento(text, kind) {
      $documentoStatus.text(text).removeClass("success error").addClass(kind || "");
      if (kind === "error" && text && window.Toast) Toast.erro(text);
    }

    if (!arquivos.length) {
      statusDocumento("Selecione ao menos um documento.", "error");
      return;
    }

    statusDocumento("");
    esconderLinksAndamento();
    $documentoBtn.prop("disabled", true);

    var qtSucesso = 0;
    var qtReconhecidos = 0;
    var qtIpeis = 0;
    var qtAnexosIpei = 0;
    // a telefonia conta OPERADORAS reconhecidas, e não arquivos: três arquivos da VIVO viram um
    // card só, e dizer "3 importações" faria o investigador procurar três blocos que não existem
    var operadorasTelefonia = {};
    var falhas = [];
    var i = 0;

    // Polling do job de ZIP (GET /api/investigacao/jobs/{id} a cada 2 s): atualiza o botão com
    // o progresso por arquivo e, ao terminar, soma os contadores do lote e segue para o próximo
    // arquivo (aoTerminar). O cancelamento vai pela rota genérica de jobs.
    function acompanharJobZip(idJob, arquivoZip, aoTerminar) {
      var encerrado = false;
      var timer = null;
      var $cancelar = $('<button type="button" class="btn btn-secondary">Cancelar importação do ZIP</button>');
      $cancelar.on("click", function () {
        $cancelar.prop("disabled", true).text("Cancelando…");
        $.ajax({ url: "/api/investigacao/jobs/" + idJob + "/cancelar", method: "POST" })
          .fail(function (xhr) {
            $cancelar.prop("disabled", false).text("Cancelar importação do ZIP");
            if (window.Toast) Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível cancelar.");
          });
      });
      $documentoStatus.after($cancelar);
      statusDocumento("Importação do ZIP iniciada em background — pode fechar esta tela e acompanhar na Investigação.");
      function finalizar() {
        encerrado = true;
        if (timer) clearInterval(timer);
        $cancelar.remove();
      }
      timer = setInterval(function () {
        $.getJSON("/api/investigacao/jobs/" + idJob)
          .done(function (j) {
            if (encerrado) return;
            var total = j.qt_total || 0;
            var feitos = j.qt_processados || 0;
            var fase = (j.tx_metricas && j.tx_metricas.fase) || "";
            if (fase === "expandindo" || !total) {
              $documentoBtn.html('<span class="spinner"></span> Expandindo ZIP…');
            } else {
              $documentoBtn.html('<span class="spinner"></span> Importando ZIP ' + feitos + "/" + total + "…");
            }
            if (j.rodando) return;
            finalizar();
            var r = j.tx_resultado || {};
            if (j.cd_status === "concluido") {
              qtSucesso += (r.qt_anexados || 0);
              qtReconhecidos += (r.qt_reconhecidos || 0);
              qtIpeis += (r.qt_ipeis || 0);
              qtAnexosIpei += (r.qt_anexos_ipei || 0);
              (r.operadoras || []).forEach(function (op) { operadorasTelefonia[op] = true; });
              (r.falhas || []).forEach(function (f) { falhas.push(arquivoZip + " [zip]: " + f); });
              if (r.truncado) falhas.push(arquivoZip + ": ZIP truncado no limite (2000 docs/20GB) — resto ignorado.");
              if (window.Toast) Toast.sucesso(arquivoZip + ": " + (r.qt_anexados || 0) + " documento(s) anexado(s).");
            } else if (j.cd_status === "cancelado") {
              qtSucesso += (r.qt_anexados || 0);
              falhas.push(arquivoZip + ": importação cancelada (" + (r.qt_anexados || 0) + " anexado(s) antes).");
              (r.falhas || []).forEach(function (f) { falhas.push(arquivoZip + " [zip]: " + f); });
            } else {
              falhas.push(arquivoZip + ": " + (j.tx_erro || "falha na importação do ZIP."));
            }
            // o ZIP saiu da pasta de entrega: recarrega a lista para ele sumir do seletor
            carregarDocumentosDisponiveis().always(function () { aoTerminar(); });
          })
          .fail(function () { /* transitório: tenta de novo no próximo tick */ });
      }, 2000);
    }

    function extrairProximo() {
      if (i >= arquivos.length) {
        var msg;
        if (!falhas.length) {
          msg = qtSucesso === 1 ? "Documento anexado" : formatarNumero(qtSucesso) + " documentos anexados";
          var reconhecidos = [];
          if (qtReconhecidos) reconhecidos.push(formatarNumero(qtReconhecidos) + " como inquérito");
          // a planilha de anexo não conta como importação à parte: ela entrou na IPEI de mesmo
          // número, e é isso que a mensagem precisa dizer para o par não parecer perdido
          if (qtIpeis) reconhecidos.push(formatarNumero(qtIpeis) + " como quebra de sigilo fiscal (IPEI)");
          if (qtAnexosIpei) reconhecidos.push(formatarNumero(qtAnexosIpei) + " juntado(s) à IPEI como anexo");
          var operadoras = Object.keys(operadorasTelefonia);
          if (operadoras.length) {
            reconhecidos.push("telefonia de " + operadoras.join(", ") +
              " (todos no mesmo bloco por operadora)");
          }
          if (reconhecidos.length) msg += " (" + reconhecidos.join(", ") + ")";
          msg += " — já disponíveis para a IA. Veja o(s) bloco(s) na página de Investigação.";
          statusDocumento(msg, "success");
          revelarLinkVerAndamento("#documento-link-investigacao", msg);
        } else {
          msg = qtSucesso + " documento(s) anexado(s). Falhas: " + falhas.join("; ");
          statusDocumento(msg, "error");
        }
        $documentoBtn.prop("disabled", false).html(ICONE_DOCUMENTO + " Importar");
        return;
      }

      var arquivo = arquivos[i];
      $documentoBtn.html('<span class="spinner"></span> Extraindo ' + (i + 1) + "/" + arquivos.length + "…");

      // .zip em data/ vai para a FILA (job import_zip): o POST devolve o id_job na hora e o
      // lote é acompanhado por polling abaixo — sem isso, centenas de documentos estourariam
      // o proxy_read_timeout de 300 s do nginx. O .always só avança quando NÃO há job (o
      // polling chama extrairProximo ao terminar).
      var noJob = false;

      $.ajax({
        url: "/api/investigacao/extrair-documento",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaAtual || null, caso_inteligencia: eCasoInteligencia() })
      })
        .done(function (resp) {
          // caso de inteligência: o 1º POST criou o CASO-AAAA.NNNN e devolveu o número —
          // os arquivos seguintes do lote entram NO MESMO caso em vez de criar outro
          if (eCasoInteligencia() && !numero && resp.nu_inquerito) numero = resp.nu_inquerito;
          if (resp.job_queued) {
            noJob = true;
            acompanharJobZip(resp.id_job, arquivo, function () { i++; extrairProximo(); });
            return;
          }
          if (resp.tx_erro) {
            falhas.push(arquivo + ": anexado sem texto (" + resp.tx_erro + ")");
          } else {
            qtSucesso++;
            if (resp.inquerito) qtReconhecidos++;
            if (resp.ipei) { if (resp.ipei.anexo) qtAnexosIpei++; else qtIpeis++; }
            if (resp.telefonia) operadorasTelefonia[resp.telefonia.no_operadora] = true;
          }
        })
        .fail(function (xhr) {
          var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao extrair";
          falhas.push(arquivo + ": " + msg);
        })
        .always(function () {
          if (noJob) return;
          i++;
          extrairProximo();
        });
    }

    extrairProximo();
  });

  $rifForm.on("submit", function (e) {
    e.preventDefault();
    var pasta = $rifSelect.val();
    var numero = validarNumeroInquerito($rifStatus);
    if (numero === null) return;

    function statusRif(text, kind) {
      $rifStatus.text(text).removeClass("success error").addClass(kind || "");
      if (kind === "error" && text && window.Toast) Toast.erro(text);
    }

    if (!pasta) {
      statusRif("Selecione uma pasta de RIF.", "error");
      return;
    }

    statusRif("");
    esconderLinksAndamento();
    $rifBtn.prop("disabled", true).html('<span class="spinner"></span> Importando…');

    $.ajax({
      url: "/api/investigacao/extrair-rif",
      method: "POST",
      contentType: "application/json",
        data: JSON.stringify({ pasta: pasta, numero_inquerito: numero, delegacia: delegaciaAtual || null, caso_inteligencia: eCasoInteligencia() })
    })
      .done(function (resp) {
        var msg = "RIF importado — " + formatarNumero(resp.qt_comunicacoes) + " comunicações, " +
          formatarNumero(resp.qt_envolvidos) + " envolvidos, " + formatarNumero(resp.qt_ocorrencias) + " ocorrências.";
        if (resp.tem_pdf && resp.tx_erro_pdf) {
          msg += " PDF anexado, mas sem texto: " + resp.tx_erro_pdf;
          statusRif(msg, "error");
          if (window.Toast) Toast.erro(msg);
        } else {
          if (resp.tem_pdf) msg += " PDF anexado aos documentos.";
          statusRif(msg, "success");
          revelarLinkVerAndamento("#rif-link-investigacao", msg);
        }
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao importar o RIF.";
        statusRif(msg, "error");
      })
      .always(function () {
        $rifBtn.prop("disabled", false).html(ICONE_RIF + " Importar");
      });
  });

  $simbaForm.on("submit", function (e) {
    e.preventDefault();
    var arquivo = $simbaSelect.val();
    var numero = validarNumeroInquerito($simbaStatus);
    if (numero === null) return;

    function statusSimba(text, kind) {
      $simbaStatus.text(text).removeClass("success error").addClass(kind || "");
      if (kind === "error" && text && window.Toast) Toast.erro(text);
    }

    if (!arquivo) {
      statusSimba("Selecione um arquivo R304.", "error");
      return;
    }

    statusSimba("");
    esconderLinksAndamento();
    // a requisição só valida o arquivo e enfileira: a leitura do CSV inteiro roda em background
    // (ver simba_utils.py). Por isso ela volta na hora, e o andamento aparece no bloco do inquérito.
    $simbaBtn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');

    $.ajax({
      url: "/api/investigacao/extrair-simba",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaAtual || null })
    })
      .done(function () {
        var msg = "Importação iniciada — acompanhe o progresso no bloco do inquérito na página de Investigação. Pode fechar esta página que ela continua rodando.";
        statusSimba(msg, "success");
        revelarLinkVerAndamento("#simba-link-investigacao", msg);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a importação.";
        statusSimba(msg, "error");
      })
      .always(function () {
        $simbaBtn.prop("disabled", false).html(ICONE_SIMBA + " Importar");
      });
  });

  $ipedForm.on("submit", function (e) {
    e.preventDefault();
    if (ipedDetectando) {
      $ipedStatus.text("Aguarde a detecção da conta terminar…").removeClass("success").addClass("error");
      if (window.Toast) Toast.aviso("Aguarde a detecção da conta terminar…");
      return;
    }
    var arquivo = $ipedSelect.val();
    var numero = validarNumeroInquerito($ipedStatus);
    if (numero === null) return;
    var conta = $.trim($ipedContaInput.val());

    function statusIped(text, kind) {
      $ipedStatus.text(text).removeClass("success error").addClass(kind || "");
      if (kind === "error" && text && window.Toast) Toast.erro(text);
    }

    if (!arquivo) {
      statusIped("Selecione uma pasta de caso do IPED.", "error");
      return;
    }

    statusIped("");
    esconderLinksAndamento();
    // a requisição só valida a pasta e enfileira: a leitura do índice roda em background
    // (ver funcoes/iped_utils.py). O andamento aparece no bloco.
    $ipedBtn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');

    $.ajax({
      url: "/api/investigacao/extrair-iped",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaAtual || null,
        conta: conta || null, caso_inteligencia: eCasoInteligencia()
      })
    })
      .done(function () {
        var msg = "Importação iniciada — acompanhe o progresso no bloco do inquérito na página de Investigação. Pode fechar esta página que ela continua rodando.";
        statusIped(msg, "success");
        revelarLinkVerAndamento("#iped-link-investigacao", msg);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a importação.";
        statusIped(msg, "error");
      })
      .always(function () {
        $ipedBtn.prop("disabled", false).html(ICONE_EXTRAIR + " Importar");
      });
  });

  $form.on("submit", function (e) {
    e.preventDefault();
    var arquivo = $arquivoSelect.val();
    var numero = validarNumeroInquerito($status);
    if (numero === null) return;

    if (!arquivo) {
      setStatus("Selecione um arquivo .ufdr.", "error");
      return;
    }

    setStatus("");
    esconderLinksAndamento();
    $btn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');

    $.ajax({
      url: "/api/investigacao/extrair",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        arquivo: arquivo,
        numero_inquerito: numero,
        modelo_transcricao: $("#qualidade-select").val() || null,
        transcrever_video: $("#transcrever-video-check").is(":checked"),
        delegacia: delegaciaAtual || null,
        caso_inteligencia: eCasoInteligencia()
      })
    })
      .done(function () {
        var msg = "Extração iniciada — acompanhe o progresso na página de Investigação.";
        setStatus(msg, "success");
        revelarLinkVerAndamento("#form-link-investigacao", msg);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a extração.";
        setStatus(msg, "error");
      })
      .always(function () {
        $btn.prop("disabled", false).html(ICONE_EXTRAIR + " Importar");
      });
  });

  function salvarDelegaciaStorage(valor) {
    try { localStorage.setItem("analyser-delegacia", valor || ""); } catch (e) {}
  }

  function carregarDelegaciaStorage() {
    try { return localStorage.getItem("analyser-delegacia") || ""; } catch (e) { return ""; }
  }

  function carregarDelegacias() {
    return $.getJSON("/api/delegacias").done(function (lista) {
      var $sel = $("#importacao-delegacia-select");
      $sel.find("option:not(:first)").remove();
      if (delegaciaTravada && delegaciaAtual) {
        // vinculado: só a própria delegacia no ar, e o select nem abre
        $sel.append($("<option>").val(delegaciaAtual).text(delegaciaAtual));
        $sel.val(delegaciaAtual).prop("disabled", true)
          .attr("title", "Seu usuário é vinculado a esta delegacia — a importação só roda nela.");
        return;
      }
      $.each(lista, function (_, d) {
        $sel.append($("<option>").val(d.no_delegacia).text(d.no_delegacia));
      });
      if (delegaciaAtual) $sel.val(delegaciaAtual);
    });
  }

  // ---------- Carregamento unificado com debounce + Promise.all ----------

  var carregandoListas = false;
  var debounceListasTimer = null;

  function recarregarListas() {
    if (carregandoListas) return $.Deferred().resolve().promise();
    carregandoListas = true;
    // feedback visual: desabilita selects principais enquanto carrega
    $arquivoSelect.prop("disabled", true);
    $rifSelect.prop("disabled", true);
    $simbaSelect.prop("disabled", true);
    // TomSelect desabilita via disable()
    if (tsDocumento) tsDocumento.disable();

    var p1 = carregarArquivos();
    var p2 = carregarDocumentosDisponiveis();
    var p3 = carregarRifsDisponiveis();
    var p4 = carregarSimbaDisponiveis();
    var p5 = carregarIpedDisponiveis();

    return $.when(p1, p2, p3, p4, p5).always(function () {
      carregandoListas = false;
      $arquivoSelect.prop("disabled", false);
      $rifSelect.prop("disabled", false);
      $simbaSelect.prop("disabled", false);
      if (tsDocumento) tsDocumento.enable();
      // cada carregar* já ajusta o disabled do próprio botão quando vazio;
      // re-aplica regra de IPED detectando
      if (ipedDetectando) $ipedBtn.prop("disabled", true);
    });
  }

  function agendarRecarregarListas() {
    clearTimeout(debounceListasTimer);
    debounceListasTimer = setTimeout(function () { recarregarListas(); }, 150);
  }

  $("#importacao-delegacia-select").on("change", function () {
    delegaciaAtual = $(this).val() || "";
    salvarDelegaciaStorage(delegaciaAtual);
    agendarRecarregarListas();
  });

  // ---------- Copiar de outra pasta de rede (SMB) ----------
  // O material pode estar num compartilhamento Windows (\\servidor\pasta) de outra
  // máquina/unidade, com ou sem login de AD. O navegador não alcança `\\...` — quem conecta
  // é o SERVIDOR (POST /api/rede/listar + /api/rede/copiar, ver api/funcoes/rede_smb.py) e
  // grava direto em data/<DELEGACIA>/. A senha vive só na memória deste modal: nunca vai
  // para localStorage/URL, e o DOM do modal (com os <input>) é destruído ao fechar.

  var $copiarRedeBtn = $("#copiar-rede-btn");
  var redePollTimer = null;

  function redePararPoll() {
    if (redePollTimer) { clearInterval(redePollTimer); redePollTimer = null; }
  }

  // Cópias com modal de origem aberto: o próprio modal acompanha (polling de 2 s) e o
  // rastreador global abaixo as ignora, para não duplicar toast nem recarregamento.
  var redeIdsEmModal = {};

  function redeMsgErro(xhr, padrao) {
    if (window.Api && Api.mensagemErro) return Api.mensagemErro(xhr, padrao);
    if (xhr && xhr.responseJSON && xhr.responseJSON.detail) {
      var d = xhr.responseJSON.detail;
      if (typeof d === "string") return d;
    }
    return padrao || "Ocorreu um erro. Tente novamente.";
  }

  // Espelha _sanitizar_delegacia (api/funcoes/investigacao_utils.py): é a pasta sanitizada
  // que vira diretório em data/ — "FICCO/ES" vira "FICCO-ES". Só para exibir o destino.
  function sanitizarDelegacia(nome) {
    var s = (nome || "").trim().toUpperCase().replace(/[/\\]/g, " ").replace(/[^A-Z0-9 _-]/g, "");
    return s.replace(/\s+/g, " ").trim().replace(/ /g, "-");
  }

  function abrirModalRede() {
    if (!delegaciaAtual) {
      if (window.Toast) Toast.aviso("Escolha a delegacia de destino na topbar antes de copiar.");
      $("#importacao-delegacia-select").focus();
      return;
    }
    redePararPoll();

    var $overlay = $('<div class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="rede-titulo">');
    var $modal = $('<div class="modal" style="width:min(640px,94vw);">');
    var $header = $('<div class="modal-header">')
      .append($('<h2 id="rede-titulo">').text("Copiar de outra pasta de rede"))
      .append($('<button type="button" class="modal-close" aria-label="Fechar">&times;</button>'));
    var $body = $('<div class="modal-body">');

    $body.append($('<p class="card-hint">').text(
      "Conecta numa pasta compartilhada do Windows e copia o que você marcar para data/" +
      (sanitizarDelegacia(delegaciaAtual) || delegaciaAtual) + "/. O login só é preciso se a pasta pedir — pasta aberta conecta sem usuário nem senha."));
    $body.append($('<p class="card-hint">').html(
      "Destino: <strong>data/" + $("<span>").text(sanitizarDelegacia(delegaciaAtual) || delegaciaAtual).html() + "/</strong> — arquivos soltos na raiz, pastas (RIF/IPED) como subpastas."));

    var $campoCaminho = $('<input type="text" class="dialogo-campo" autocomplete="off" spellcheck="false" placeholder="\\\\SERVIDOR\\pasta">');
    var $campoUsuario = $('<input type="text" class="dialogo-campo" autocomplete="username" placeholder="Usuário (opcional — DOMINIO\\usuário ou usuário@dominio)">');
    var $campoDominio = $('<input type="text" class="dialogo-campo" autocomplete="off" placeholder="Domínio (opcional)">').val("pf.gov.br");
    var $campoSenha = $('<input type="password" class="dialogo-campo" autocomplete="current-password" placeholder="Senha (opcional)">');

    function linhaCampo(rotulo, $campo) {
      return $('<div class="field">')
        .append($("<label>").text(rotulo))
        .append($campo);
    }
    $body.append(linhaCampo("Caminho da pasta de rede", $campoCaminho));
    $body.find(".field").last().append(
      $('<p class="field-help">').text("Ex.: \\\\SERVIDOR\\pasta. Se der erro de conexão, tente o IP no lugar do nome (ex.: \\\\192.168.1.10\\pasta)."));
    $body.append(linhaCampo("Usuário", $campoUsuario));
    $body.append(linhaCampo("Domínio", $campoDominio));
    $body.append(linhaCampo("Senha", $campoSenha));

    var $acoesTopo = $('<div class="form-actions">');
    var $btnConectar = $('<button type="button" class="btn btn-secondary">').text("Conectar e listar");
    var $btnVoltar = $('<button type="button" class="btn btn-secondary" disabled>').text("← Voltar");
    $acoesTopo.append($btnConectar).append($btnVoltar);
    $body.append($acoesTopo);

    var $status = $('<p class="form-status" role="status" aria-live="polite">');
    $body.append($status);

    var $migalha = $('<p class="card-hint" style="margin:8px 0 0;">');
    $body.append($migalha);
    var $lista = $('<ul class="upload-lista rede-lista">');
    $body.append($lista);

    var $progresso = $('<div class="rede-progresso" hidden>').append(
      $('<div class="rede-progresso-barra">').append($('<div class="rede-progresso-preenchimento">')));
    var $progressoTexto = $('<p class="card-hint rede-progresso-texto">');
    $body.append($progresso).append($progressoTexto);

    var $acoesFim = $('<div class="form-actions">');
    var $btnCopiar = $('<button type="button" class="btn btn-primary" disabled>').text("Copiar selecionados");
    var $btnCancelarCopia = $('<button type="button" class="btn btn-secondary" disabled>').text("Cancelar cópia");
    var $btnFechar = $('<button type="button" class="btn btn-secondary">').text("Fechar");
    $acoesFim.append($btnCopiar).append($btnCancelarCopia).append($btnFechar);
    $body.append($acoesFim);

    $modal.append($header).append($body);
    $overlay.append($modal);
    $(document.body).append($overlay);

    var subpastaAtual = "";
    var itensAtuais = [];
    var idJobAtivo = null;
    var redeDestino = sanitizarDelegacia(delegaciaAtual) || delegaciaAtual;

    function statusRede(texto, kind) {
      $status.text(texto || "").removeClass("success error").addClass(kind || "");
      if (kind === "error" && texto && window.Toast) Toast.erro(texto);
    }

    function credenciais() {
      // Lê na hora de cada requisição e não guarda em variável de longa duração.
      return {
        usuario: $.trim($campoUsuario.val()) || null,
        senha: $campoSenha.val() || null,
        dominio: $.trim($campoDominio.val()) || null
      };
    }

    function caminhoBase() { return $.trim($campoCaminho.val()); }

    // Se o domínio está preenchido com um sufixo DNS (ex.: pf.gov.br) e o servidor no
    // caminho é um nome curto (ex.: ses0006), completa para o FQDN (ses0006.pf.gov.br) e
    // mostra o caminho final no campo — o container não resolve nome curto. Domínio vazio,
    // IP ou servidor que já tem ponto: não mexe em nada.
    function aplicarDominioNoCaminho() {
      var dominio = $.trim($campoDominio.val());
      if (!dominio || dominio.indexOf(".") === -1) return;
      var caminho = caminhoBase();
      var m = caminho.match(/^([\\/]{2})([^\\/]+)([\\/].*)?$/);
      if (!m) return;
      var servidor = m[2];
      if (servidor.indexOf(".") !== -1) return; // IP ou FQDN: já está completo
      if (/^\d{1,3}(\.\d{1,3}){3}$/.test(servidor)) return;
      var sufixo = dominio.replace(/^\.+/, "");
      if (!sufixo) return;
      if (servidor.toLowerCase().endsWith("." + sufixo.toLowerCase())) return;
      $campoCaminho.val("\\\\" + servidor + "." + sufixo + (m[3] || ""));
    }

    function renderizarLista(resposta) {
      subpastaAtual = resposta.subpasta || "";
      itensAtuais = resposta.itens || [];
      $btnVoltar.prop("disabled", !subpastaAtual);
      $migalha.text(subpastaAtual ? "Dentro de: " + subpastaAtual : "Raiz do compartilhamento");
      $lista.empty();
      if (!itensAtuais.length) {
        $lista.append($("<li>").append($("<span>").text("Pasta vazia.")));
      }
      itensAtuais.forEach(function (item, idx) {
        var $li = $("<li>").addClass(item.is_dir ? "rede-pasta" : "rede-arquivo");
        var $check = $('<input type="checkbox">')
          .attr("data-idx", idx)
          .attr("aria-label", "Selecionar " + item.nome);
        var rotulo = item.is_dir ? item.nome + "/" : item.nome + " (" + formatarBytes(item.tamanho_bytes) + ")";
        var $nome = $("<span>").text(rotulo);
        $li.append($check).append($nome);
        if (item.is_dir) {
          var $entrar = $('<button type="button" class="btn btn-secondary btn-compacto">').text("Abrir");
          $entrar.on("click", function () {
            listarNaSubpasta(item.caminho_rel);
          });
          $li.append($entrar);
        }
        $lista.append($li);
      });
      $btnCopiar.prop("disabled", true);
      $lista.find('input[type="checkbox"]').on("change", function () {
        $btnCopiar.prop("disabled", !$lista.find('input[type="checkbox"]:checked').length);
      });
    }

    function listarNaSubpasta(sub) {
      aplicarDominioNoCaminho();
      var caminho = caminhoBase();
      if (!caminho) { statusRede("Informe o caminho da pasta de rede.", "error"); $campoCaminho.focus(); return; }
      statusRede("Conectando…");
      $btnConectar.prop("disabled", true);
      var cred = credenciais();
      $.ajax({
        url: "/api/rede/listar",
        method: "POST",
        contentType: "application/json",
        timeout: 45000,
        data: JSON.stringify({ caminho: caminho, usuario: cred.usuario, senha: cred.senha, dominio: cred.dominio, subpasta: sub || "" })
      }).done(function (resposta) {
        statusRede("");
        renderizarLista(resposta);
      }).fail(function (xhr, statusTexto) {
        if (statusTexto === "timeout") {
          statusRede("O servidor demorou a responder (45 s). Ele pode estar tentando alcançar uma máquina inacessível — confira o nome/IP e tente de novo.", "error");
        } else {
          statusRede(redeMsgErro(xhr, "Não foi possível listar a pasta de rede."), "error");
        }
      }).always(function () {
        $btnConectar.prop("disabled", false);
      });
    }

    $btnConectar.on("click", function () { listarNaSubpasta(""); });
    $campoCaminho.on("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); listarNaSubpasta(""); } });
    $btnVoltar.on("click", function () {
      var partes = subpastaAtual.split("/").filter(Boolean);
      partes.pop();
      listarNaSubpasta(partes.join("/"));
    });

    function atualizarProgresso(resumo) {
      var pct = resumo.total ? Math.round((resumo.atual / resumo.total) * 100) : 0;
      $progresso.removeAttr("hidden");
      $progresso.find(".rede-progresso-preenchimento").css("width", pct + "%");
      if (resumo.cd_status === "concluido") {
        var destinoFinal = resumo.delegacia || redeDestino;
        var msg = "Cópia concluída: " + resumo.concluidos.length + " item(ns) em data/" + destinoFinal + "/.";
        if (resumo.falhas && resumo.falhas.length) msg += " Falhas: " + resumo.falhas.join("; ");
        $progressoTexto.text(msg);
        statusRede(msg, resumo.falhas.length ? "error" : "success");
        if (window.Toast) Toast.sucesso(msg);
        $btnCopiar.prop("disabled", false);
        $btnCancelarCopia.prop("disabled", true);
        delete redeIdsEmModal[idJobAtivo];
        idJobAtivo = null;
        redePararPoll();
        recarregarListas();
      } else if (resumo.cd_status === "erro" || resumo.cd_status === "cancelado") {
        var fim = resumo.cd_status === "cancelado" ? "Cópia cancelada." : ("Falha na cópia: " + (resumo.tx_erro || "erro desconhecido."));
        $progressoTexto.text(fim);
        statusRede(fim, "error");
        $btnCopiar.prop("disabled", false);
        $btnCancelarCopia.prop("disabled", true);
        delete redeIdsEmModal[idJobAtivo];
        idJobAtivo = null;
        redePararPoll();
        if (resumo.concluidos && resumo.concluidos.length) recarregarListas();
      } else {
        $progressoTexto.text("Copiando " + resumo.atual + "/" + resumo.total +
          " — " + formatarBytes(resumo.bytes_copiados) + " transferidos… pode fechar este modal que a cópia continua.");
      }
    }

    function pollJob() {
      if (!idJobAtivo) return;
      $.getJSON("/api/rede/copiar/" + encodeURIComponent(idJobAtivo))
        .done(atualizarProgresso)
        .fail(function (xhr) {
          if (xhr && xhr.status === 404) {
            redePararPoll();
            delete redeIdsEmModal[idJobAtivo];
            idJobAtivo = null;
            statusRede("A cópia não foi encontrada no servidor.", "error");
            $btnCopiar.prop("disabled", false);
            $btnCancelarCopia.prop("disabled", true);
          }
        });
    }

    $btnCopiar.on("click", function () {
      aplicarDominioNoCaminho();
      var marcados = $lista.find('input[type="checkbox"]:checked').map(function () {
        return itensAtuais[parseInt($(this).attr("data-idx"), 10)].caminho_rel;
      }).get();
      if (!marcados.length) { statusRede("Marque ao menos um arquivo ou pasta.", "error"); return; }
      if (!delegaciaAtual) { statusRede("Escolha a delegacia de destino na topbar.", "error"); return; }
      statusRede("");
      $btnCopiar.prop("disabled", true);
      $btnCancelarCopia.prop("disabled", false);
      $progresso.removeAttr("hidden");
      $progressoTexto.text("Iniciando cópia…");
      var cred = credenciais();
      $.ajax({
        url: "/api/rede/copiar",
        method: "POST",
        contentType: "application/json",
        timeout: 45000,
        data: JSON.stringify({ caminho: caminhoBase(), itens: marcados, delegacia: delegaciaAtual, usuario: cred.usuario, senha: cred.senha, dominio: cred.dominio })
      }).done(function (resposta) {
        idJobAtivo = resposta.id_job;
        if (resposta.delegacia) redeDestino = resposta.delegacia;
        redePararPoll();
        redeIdsEmModal[idJobAtivo] = true; // o rastreador global ignora este id
        redePollTimer = setInterval(pollJob, 2000);
        pollJob();
      }).fail(function (xhr, statusTexto) {
        if (statusTexto === "timeout") {
          statusRede("O servidor demorou a responder (45 s). A cópia pode não ter iniciado — confira o nome/IP e tente de novo.", "error");
        } else {
          statusRede(redeMsgErro(xhr, "Não foi possível iniciar a cópia."), "error");
        }
        $btnCopiar.prop("disabled", false);
        $btnCancelarCopia.prop("disabled", true);
      });
    });

    $btnCancelarCopia.on("click", function () {
      if (!idJobAtivo) return;
      $.ajax({ url: "/api/rede/copiar/" + encodeURIComponent(idJobAtivo) + "/cancelar", method: "POST" })
        .fail(function (xhr) { statusRede(redeMsgErro(xhr, "Não foi possível cancelar."), "error"); });
    });

    function fechar() {
      // Fecha o modal e destrói o DOM (com usuário/senha digitados). Se a cópia continua, o
      // rastreador global da página assume o aviso ao concluir — o progresso mora no banco.
      delete redeIdsEmModal[idJobAtivo];
      redePararPoll();
      idJobAtivo = null;
      $overlay.remove();
      $(document).off("keydown.rede");
      $copiarRedeBtn.focus();
    }

    $header.find(".modal-close").on("click", fechar);
    $btnFechar.on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === $overlay[0]) fechar(); });
    $(document).on("keydown.rede", function (e) { if (e.key === "Escape") fechar(); });

    $campoCaminho.focus();
  }

  $copiarRedeBtn.on("click", abrirModalRede);

  // ---------- Painel "Cópias" — acompanha mesmo depois de fechar a página ----------
  // O progresso mora em JOB_TB01_JOBS (ver api/funcoes/rede_smb.py): fechar a página, trocar
  // de tela ou reiniciar a API não perde nada. Ao voltar, o badge da topbar mostra quantas
  // cópias estão rodando e este painel lista cada uma, com cancelar.

  var $copiasBtn = $("#copias-btn");
  var $copiasBadge = $("#copias-badge");
  var copiasSnapshot = {}; // id_job -> cd_status, só do último rastreio

  function rotuloStatusCopia(s) {
    return { pendente: "Na fila", aguardando_vaga: "Aguardando", processando: "Copiando",
      concluido: "Concluída", erro: "Falhou", cancelado: "Cancelada" }[s] || s;
  }

  function classeStatusCopia(s) {
    return { pendente: "badge-aviso", aguardando_vaga: "badge-aviso", processando: "badge-rodando",
      concluido: "badge-ok", erro: "badge-erro", cancelado: "badge-neutro" }[s] || "badge-neutro";
  }

  function formatarDataHora(iso) {
    if (!iso) return "";
    var d = new Date(typeof iso === "string" && iso.indexOf(" ") !== -1 ? iso.replace(" ", "T") : iso);
    if (isNaN(d.getTime())) return "";
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getDate()) + "/" + p(d.getMonth() + 1) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function avisarCopiaTerminada(id) {
    // Busca o resumo final uma vez: toast + recarrega as listas se algo chegou.
    $.getJSON("/api/rede/copiar/" + encodeURIComponent(id)).done(function (resumo) {
      if (resumo.cd_status === "concluido") {
        var msg = "Cópia da pasta de rede concluída: " + resumo.concluidos.length +
          " item(ns) em data/" + (resumo.delegacia || "") + "/.";
        if (resumo.falhas && resumo.falhas.length) msg += " Falhas: " + resumo.falhas.join("; ");
        if (window.Toast) Toast.sucesso(msg);
        recarregarListas();
      } else if (resumo.cd_status === "erro") {
        if (window.Toast) Toast.erro("Cópia da pasta de rede falhou: " + (resumo.tx_erro || "erro desconhecido."));
        if (resumo.concluidos && resumo.concluidos.length) recarregarListas();
      } else if (resumo.cd_status === "cancelado") {
        if (window.Toast) Toast.aviso("Cópia da pasta de rede cancelada.");
      }
    });
  }

  function rastrearCopias() {
    if (document.hidden) return; // aba oculta: o servidor continua; o rastreio retoma ao voltar
    $.getJSON("/api/rede/copiar", { ativas: true, limite: 20 }).done(function (ativas) {
      var visiveis = {};
      var nRodando = 0;
      ativas.forEach(function (c) {
        visiveis[c.id_job] = c.cd_status;
        if (c.rodando) nRodando++;
      });
      $copiasBadge.text(nRodando).prop("hidden", !nRodando);
      Object.keys(copiasSnapshot).forEach(function (id) {
        if (visiveis[id] || redeIdsEmModal[id]) return; // ainda roda, ou o modal acompanha
        delete copiasSnapshot[id];
        avisarCopiaTerminada(id);
      });
      Object.keys(visiveis).forEach(function (id) { copiasSnapshot[id] = visiveis[id]; });
    });
  }

  setInterval(rastrearCopias, 5000);

  function abrirPainelCopias() {
    var $overlay = $('<div class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="copias-titulo">');
    var $modal = $('<div class="modal" style="width:min(680px,94vw);">');
    var $header = $('<div class="modal-header">')
      .append($('<h2 id="copias-titulo">').text("Cópias da pasta de rede"))
      .append($('<button type="button" class="modal-close" aria-label="Fechar">&times;</button>'));
    var $body = $('<div class="modal-body">');
    $body.append($('<p class="card-hint">').text("Suas cópias, da mais recente à mais antiga. Fechar a página não interrompe nada — volte aqui para acompanhar."));
    $body.append($('<div class="field">').append(
      $('<label class="field-check">').append($('<input type="checkbox" id="copias-so-ativas" checked>'))
        .append($("<span>").text(" Só em andamento"))));
    var $status = $('<p class="form-status" role="status" aria-live="polite">');
    $body.append($status);
    var $lista = $('<ul class="upload-lista rede-lista">');
    $body.append($lista);
    var $acoes = $('<div class="form-actions">');
    var $btnAtualizar = $('<button type="button" class="btn btn-secondary">').text("Atualizar");
    var $btnFechar = $('<button type="button" class="btn btn-secondary">').text("Fechar");
    $acoes.append($btnAtualizar).append($btnFechar);
    $body.append($acoes);
    $modal.append($header).append($body);
    $overlay.append($modal);
    $(document.body).append($overlay);

    function renderizar(copias) {
      $lista.empty();
      if (!copias.length) {
        $lista.append($("<li>").append($("<span>").text("Nenhuma cópia encontrada.")));
        return;
      }
      copias.forEach(function (c) {
        var pct = c.total ? Math.round((c.atual / c.total) * 100) : 0;
        var $li = $("<li>").addClass("copia-item");
        var $info = $("<div>").addClass("copia-info");
        $info.append($("<div>").addClass("copia-titulo")
          .text((c.origem || "pasta de rede") + " → data/" + (c.delegacia || "") + "/"));
        $info.append($('<div class="rede-progresso-barra">')
          .append($('<div class="rede-progresso-preenchimento">').css("width", pct + "%")));
        var detalhe = rotuloStatusCopia(c.cd_status) + " · " + c.atual + "/" + c.total + " item(ns)";
        if (c.bytes_copiados) detalhe += " · " + formatarBytes(c.bytes_copiados);
        if (c.dt_inicio) detalhe += " · início " + formatarDataHora(c.dt_inicio);
        if (c.falhas && c.falhas.length) detalhe += " · falhas: " + c.falhas.join("; ");
        $info.append($("<div>").addClass("copia-detalhe").text(detalhe));
        $li.append($info);
        $li.append($('<span class="badge">').addClass(classeStatusCopia(c.cd_status)).text(rotuloStatusCopia(c.cd_status)));
        if (c.rodando) {
          var $cancelar = $('<button type="button" class="btn btn-secondary btn-compacto">').text("Cancelar");
          $cancelar.on("click", function () {
            $cancelar.prop("disabled", true);
            $.ajax({ url: "/api/rede/copiar/" + encodeURIComponent(c.id_job) + "/cancelar", method: "POST" })
              .done(function () { carregar(); rastrearCopias(); })
              .fail(function (xhr) {
                $status.text(redeMsgErro(xhr, "Não foi possível cancelar.")).removeClass("success").addClass("error");
                $cancelar.prop("disabled", false);
              });
          });
          $li.append($cancelar);
        }
        $lista.append($li);
      });
    }

    function carregar() {
      var soAtivas = $body.find("#copias-so-ativas").is(":checked");
      $.getJSON("/api/rede/copiar", { ativas: soAtivas, limite: 20 })
        .done(renderizar)
        .fail(function (xhr) {
          $status.text(redeMsgErro(xhr, "Não foi possível listar as cópias.")).removeClass("success").addClass("error");
        });
    }

    $btnAtualizar.on("click", carregar);
    $body.find("#copias-so-ativas").on("change", carregar);
    var timer = setInterval(function () {
      if ($.contains(document.documentElement, $overlay[0])) carregar();
    }, 3000);

    function fechar() {
      clearInterval(timer);
      $overlay.remove();
      $(document).off("keydown.copias");
      $copiasBtn.focus();
    }
    $header.find(".modal-close").on("click", fechar);
    $btnFechar.on("click", fechar);
    $overlay.on("click", function (e) { if (e.target === $overlay[0]) fechar(); });
    $(document).on("keydown.copias", function (e) { if (e.key === "Escape") fechar(); });

    carregar();
  }

  $copiasBtn.on("click", abrirPainelCopias);
  rastrearCopias();

  $.getJSON("/api/auth/eu")
    .done(function (usuario) {
      if (usuario.id_delegacia && usuario.no_delegacia) {
        // vinculado a uma delegacia: ignora o localStorage (um valor velho de outra delegacia
        // não pode nem aparecer aqui — o backend recusaria com 403 de qualquer forma)
        delegaciaTravada = true;
        delegaciaAtual = usuario.no_delegacia;
        try { localStorage.removeItem("analyser-delegacia"); } catch (e) {}
      } else {
        var storageVal = carregarDelegaciaStorage();
        if (storageVal) delegaciaAtual = storageVal;
      }
      carregarDelegacias();
    })
    .always(function () {
      recarregarListas();
    });
})();
