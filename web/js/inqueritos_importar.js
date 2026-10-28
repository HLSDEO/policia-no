/* js/inqueritos_importar.js — Modal "+ Importar" na lista de inquéritos (inqueritos.html)
 * Reaproveita layout de importacao.html mas com o número já preenchido a partir do card.
 * Cobre UFDR, RIF, SIMBA, IPED e Documento genérico. Para um card de caso de inteligência,
 * o modal envia caso_inteligencia junto (o número do card é mantido).
 */
(function () {
  "use strict";

  var $overlay = $("#modal-importar");
  if (!$overlay.length) return;

  var $subtitulo = $("#modal-importar-subtitulo");
  var $fechar = $("#modal-importar-fechar");
  var $painelInq = $("#modal-imp-inquerito");

  // Inquérito — campos e forms
  var $inqueritoInput = $("#modal-inquerito-input");
  var $ufdrForm = $("#modal-ufdr-form");
  var $arquivoSelect = $("#modal-arquivo-select");
  var $qualidadeSelect = $("#modal-qualidade-select");
  var $transcreverVideo = $("#modal-transcrever-video-check");
  var $ufdrBtn = $("#modal-ufdr-btn");
  var $ufdrStatus = $("#modal-ufdr-status");
  var $ufdrLink = $("#modal-ufdr-link");

  var $ipedForm = $("#modal-iped-form");
  var $ipedSelect = $("#modal-iped-select");
  var $ipedBtn = $("#modal-iped-btn");
  var $ipedStatus = $("#modal-iped-status");
  var $ipedLink = $("#modal-iped-link");
  var $ipedContaCampo = $("#modal-iped-conta-campo");
  var $ipedContaInput = $("#modal-iped-conta-input");

  var $documentoForm = $("#modal-documento-form");
  var $documentoSelect = $("#modal-documento-select");
  var $documentoBtn = $("#modal-documento-btn");
  var $documentoStatus = $("#modal-documento-status");
  var $documentoLink = $("#modal-documento-link");
  var $documentoUploadInput = $("#modal-documento-upload-input");
  var $documentoDropZone = $("#modal-documento-drop-zone");
  var $documentoUploadStatus = $("#modal-documento-upload-status");
  var tsModalDocumento = null;

  var $rifForm = $("#modal-rif-form");
  var $rifSelect = $("#modal-rif-select");
  var $rifBtn = $("#modal-rif-btn");
  var $rifStatus = $("#modal-rif-status");
  var $rifLink = $("#modal-rif-link");
  var $rifUploadInput = $("#modal-rif-upload-input");
  var $rifDropZone = $("#modal-rif-drop-zone");
  var $rifUploadStatus = $("#modal-rif-upload-status");

  var $simbaForm = $("#modal-simba-form");
  var $simbaSelect = $("#modal-simba-select");
  var $simbaBtn = $("#modal-simba-btn");
  var $simbaStatus = $("#modal-simba-status");
  var $simbaLink = $("#modal-simba-link");
  var $simbaUploadInput = $("#modal-simba-upload-input");
  var $simbaDropZone = $("#modal-simba-drop-zone");
  var $simbaUploadStatus = $("#modal-simba-upload-status");

  var $tabs = $overlay.find(".extracao-tab[data-modal-tab]");
  var $panels = $overlay.find(".extracao-tab-panel");

  // soltar arquivo/pasta fora de qualquer drop-zone não pode navegar nem recarregar a tela
  PastaArraste.evitarNavegacao();

  var itemAtual = null;
  var delegaciaAtual = "";
  var ipedDetectando = false;
  var ultimoFocoAntesDoModal = null;

  var ICONE_EXTRAIR = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  var ICONE_DOCUMENTO = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
  var ICONE_RIF = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="13" x2="8" y2="17"/><line x1="12" y1="13" x2="12" y2="17"/><line x1="16" y1="13" x2="16" y2="17"/></svg>';
  var ICONE_SIMBA = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-6 9 6"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/></svg>';
  // marcas da lista de upload (substituem ✓/✗) — tamanho fixo inline porque .upload-marca
  // não dimensiona svg (só herda cor do <li>)
  var MARCA_OK = '<svg width="14" height="14" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  var MARCA_ERRO = '<svg width="14" height="14" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

  function formatarBytes(bytes) {
    if (!bytes) return "";
    var unidades = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (bytes >= 1024 && i < unidades.length - 1) { bytes /= 1024; i++; }
    return bytes.toFixed(1) + " " + unidades[i];
  }
  function formatarNumero(n) { return AnalyserUtils.formatarNumero(n); }
  function delegaciaParaApi() {
    if (!delegaciaAtual || delegaciaAtual === "—") return null;
    return delegaciaAtual;
  }
  function obterNumeroInquerito() { return $.trim($inqueritoInput.val()); }
  function validarNumeroInquerito($statusEl) {
    var numero = obterNumeroInquerito();
    if (!numero || !/\S/.test(numero)) {
      var msg = "Informe o número do inquérito.";
      if ($statusEl && $statusEl.length) $statusEl.text(msg).removeClass("success").addClass("error");
      if (window.Toast) Toast.erro(msg);
      $inqueritoInput.attr("aria-invalid", "true").focus();
      return null;
    }
    $inqueritoInput.removeAttr("aria-invalid");
    return numero;
  }
  function revelarLink($link) { if ($link && $link.length) $link.removeClass("hidden").removeAttr("hidden"); }
  function esconderLinks() {
    $ufdrLink.addClass("hidden").attr("hidden", "");
    $ipedLink.addClass("hidden").attr("hidden", "");
    $documentoLink.addClass("hidden").attr("hidden", "");
    $rifLink.addClass("hidden").attr("hidden", "");
    $simbaLink.addClass("hidden").attr("hidden", "");
  }
  function atualizarLinksInvestigacao() {
    if (!itemAtual) return;
    var href = "investigacao.html?inquerito=" + encodeURIComponent(itemAtual.id_inquerito || itemAtual.id_item);
    $ufdrLink.attr("href", href);
    $ipedLink.attr("href", href);
    $documentoLink.attr("href", href);
    $rifLink.attr("href", href);
    $simbaLink.attr("href", href);
  }

  // O modal sempre importa para o inquérito do card — quando o card é um caso de
  // inteligência, a flag acompanha o número (o backend a rejeita sem o perfil certo).
  function eCasoInteligencia() {
    return !!(itemAtual && itemAtual.in_caso_inteligencia);
  }

  function ativarAbaModal(nome) {
    if (!$tabs.filter('[data-modal-tab="' + nome + '"]').length) nome = "ufdr";
    $tabs.removeClass("active").attr("aria-selected", "false").attr("tabindex", "-1");
    $panels.addClass("hidden").attr("hidden", "").attr("aria-hidden", "true");
    var $abaAtiva = $tabs.filter('[data-modal-tab="' + nome + '"]');
    $abaAtiva.addClass("active").attr("aria-selected", "true").removeAttr("tabindex");
    var $painel = $("#modal-panel-" + nome);
    $painel.removeClass("hidden").removeAttr("hidden").removeAttr("aria-hidden");
  }

  $tabs.on("click", function () { ativarAbaModal($(this).data("modal-tab")); esconderLinks(); });
  $tabs.on("keydown", function (e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    var idx = $tabs.index(this), total = $tabs.length;
    if (e.key === "ArrowRight") idx = (idx + 1) % total;
    else if (e.key === "ArrowLeft") idx = (idx - 1 + total) % total;
    else if (e.key === "Home") idx = 0;
    else if (e.key === "End") idx = total - 1;
    $tabs.eq(idx).focus().trigger("click");
  });
  $inqueritoInput.on("keydown", function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    var aba = $tabs.filter(".active").data("modal-tab") || "ufdr";
    if (aba === "documento") $documentoForm.trigger("submit");
    else if (aba === "rif") $rifForm.trigger("submit");
    else if (aba === "simba") $simbaForm.trigger("submit");
    else if (aba === "iped") $ipedForm.trigger("submit");
    else $ufdrForm.trigger("submit");
  });
  $inqueritoInput.on("input", function () { if ($.trim($(this).val())) $(this).removeAttr("aria-invalid"); });

  // ---- Carregamento de listas filtradas por delegacia do item ----
  function carregarModalArquivos() {
    var params = delegaciaParaApi() ? { delegacia: delegaciaParaApi() } : {};
    return $.getJSON("/api/investigacao/arquivos", params).done(function (arquivos) {
      $arquivoSelect.empty();
      if (!arquivos.length) {
        $arquivoSelect.append($("<option>").val("").text("Nenhum .ufdr encontrado em data/" + (delegaciaParaApi() ? " (" + delegaciaAtual + ")" : "")));
        $ufdrBtn.prop("disabled", true);
        $("#modal-arquivo-help").text("Copie o arquivo .ufdr para data/" + (delegaciaParaApi() ? delegaciaAtual + "/" : "") + ".");
        return;
      }
      $("#modal-arquivo-help").text("");
      $ufdrBtn.prop("disabled", false);
      arquivos.forEach(function (a) {
        $arquivoSelect.append($("<option>").val(a.nome).text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"));
      });
    }).fail(function (xhr) {
      $arquivoSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#modal-arquivo-help").text((xhr && xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível listar os arquivos.");
    });
  }
  function carregarModalDocumentosDisponiveis() {
    var params = delegaciaParaApi() ? { delegacia: delegaciaParaApi() } : {};
    return $.getJSON("/api/documentos/arquivos", params).done(function (arquivos) {
      var suportados = arquivos.filter(function (a) { return a.suportado && !/r304/i.test(a.nome); });
      if (!tsModalDocumento) return;
      tsModalDocumento.clear(); tsModalDocumento.clearOptions();
      if (!suportados.length) {
        tsModalDocumento.settings.placeholder = "Nenhum PDF/CSV/XLS/XLSX/DOCX/TXT/HTML/ZIP em data/" + (delegaciaParaApi() ? " (" + delegaciaAtual + ")" : "");
        tsModalDocumento.inputState();
        $documentoBtn.prop("disabled", true);
        $("#modal-documento-help").text("Copie os documentos (ou um .zip com eles) para data/" + (delegaciaParaApi() ? delegaciaAtual + "/" : "") + ".");
        return;
      }
      $documentoBtn.prop("disabled", false);
      $("#modal-documento-help").text("");
      tsModalDocumento.settings.placeholder = "Selecione um ou mais documentos…";
      tsModalDocumento.inputState();
      tsModalDocumento.addOptions(suportados.map(function (a) {
        var rotulo = a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")";
        if (a.eh_zip) rotulo += " [ZIP — expande e importa tudo]";
        return { value: a.nome, text: rotulo };
      }));
    }).fail(function (xhr) {
      if (!tsModalDocumento) return;
      tsModalDocumento.clear(); tsModalDocumento.clearOptions();
      tsModalDocumento.settings.placeholder = "Indisponível no central";
      tsModalDocumento.inputState();
      $("#modal-documento-help").text((xhr && xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível listar os documentos.");
    });
  }
  function carregarModalRifsDisponiveis() {
    var params = delegaciaParaApi() ? { delegacia: delegaciaParaApi() } : {};
    return $.getJSON("/api/investigacao/rifs-disponiveis", params).done(function (rifs) {
      $rifSelect.empty();
      if (!rifs.length) {
        $rifSelect.append($("<option>").val("").text("Nenhuma pasta de RIF em data/" + (delegaciaParaApi() ? " (" + delegaciaAtual + ")" : "")));
        $rifBtn.prop("disabled", true);
        $("#modal-rif-help").text("Copie a pasta do RIF (com RIF no nome) para data/" + (delegaciaParaApi() ? delegaciaAtual + "/" : "") + ".");
        return;
      }
      $rifBtn.prop("disabled", false);
      $("#modal-rif-help").text("");
      rifs.forEach(function (r) {
        var partes = [];
        if (r.tem_comunicacoes) partes.push("comunicações");
        if (r.tem_envolvidos) partes.push("envolvidos");
        if (r.tem_ocorrencias) partes.push("ocorrências");
        if (r.tem_pdf) partes.push("PDF");
        var extra = (r.nu_rif ? "RIF " + r.nu_rif : "");
        if (partes.length) extra += (extra ? " · " : "") + partes.join(", ");
        $rifSelect.append($("<option>").val(r.pasta).text(r.pasta + (extra ? " (" + extra + ")" : "")));
      });
    }).fail(function (xhr) {
      $rifSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#modal-rif-help").text((xhr && xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível listar as pastas de RIF.");
    });
  }
  function carregarModalSimbaDisponiveis() {
    var params = delegaciaParaApi() ? { delegacia: delegaciaParaApi() } : {};
    return $.getJSON("/api/investigacao/simba-disponiveis", params).done(function (arquivos) {
      $simbaSelect.empty();
      if (!arquivos.length) {
        $simbaSelect.append($("<option>").val("").text("Nenhum arquivo R304 em data/" + (delegaciaParaApi() ? " (" + delegaciaAtual + ")" : "")));
        $simbaBtn.prop("disabled", true);
        $("#modal-simba-help").text("Copie o CSV do SIMBA (o nome contém R304) para data/" + (delegaciaParaApi() ? delegaciaAtual + "/" : "") + ".");
        return;
      }
      $simbaBtn.prop("disabled", false);
      $("#modal-simba-help").text("");
      arquivos.forEach(function (a) { $simbaSelect.append($("<option>").val(a.nome).text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")")); });
    }).fail(function (xhr) {
      $simbaSelect.empty().append($("<option>").val("").text("Indisponível no central"));
      $("#modal-simba-help").text((xhr && xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível listar os arquivos R304.");
    });
  }
  function carregarModalIpedDisponiveis() {
    var params = delegaciaParaApi() ? { delegacia: delegaciaParaApi() } : {};
    var pDisponivel = $.getJSON("/api/investigacao/iped-disponivel").done(function (r) {
      if (r.disponivel) return;
      $ipedSelect.empty().append($("<option>").val("").text("Indisponível — rodará no nó"));
      $ipedBtn.prop("disabled", true);
      $("#modal-iped-help").text(r.motivo || "Importação IPED indisponível neste servidor.");
    }).fail(function () {});
    var pLista = $.getJSON("/api/investigacao/iped-disponiveis", params).done(function (pastas) {
      if ($("#modal-iped-help").text().indexOf("central") !== -1) return;
      $ipedSelect.empty();
      if (!pastas.length) {
        $ipedSelect.append($("<option>").val("").text("Nenhuma pasta de caso do IPED em data/" + (delegaciaParaApi() ? " (" + delegaciaAtual + ")" : "")));
        $ipedBtn.prop("disabled", true);
        $("#modal-iped-help").text("Copie a pasta do caso processado pelo IPED para data/" + (delegaciaParaApi() ? delegaciaAtual + "/" : "") + ".");
        $ipedContaCampo.addClass("hidden").attr("hidden", "");
        return;
      }
      $ipedBtn.prop("disabled", false);
      $("#modal-iped-help").text("");
      pastas.forEach(function (p) { $ipedSelect.append($("<option>").val(p.tx_pasta).text(p.tx_pasta)); });
      $ipedSelect.trigger("change");
    }).fail(function () {
      $ipedSelect.empty().append($("<option>").val("").text("Erro ao carregar IPED"));
      $("#modal-iped-help").text("Não foi possível listar as pastas do IPED.");
    });
    return $.when(pDisponivel, pLista);
  }
  // ---- Envio de documento pelo navegador (aba "Arquivo genérico") ----
  // Espelha a aba "Arquivo genérico" de importacao.html (ver js/importacao.js): antes disto, o
  // arquivo só entrava sendo copiado à mão para data/<DELEGACIA>/ no servidor. O envio grava na
  // mesma pasta do inquérito (POST /api/documentos/upload, com a delegacia do card) e
  // recarrega a lista deixando o que subiu já selecionado; a importação em si continua sendo o
  // formulário abaixo.

  // Limite vigente vem de GET /api/documentos/upload/limite (UPLOAD_DOCUMENTO_MB, ajustável
  // em Administração → Desempenho). A conferência aqui é só para o usuário saber na hora —
  // quem decide é o servidor. O .zip é a exceção, como o .ufdr: sem limite (vai por
  // POST /api/documentos/upload-zip).
  var MAX_BYTES_UPLOAD = 50 * 1024 * 1024;
  var LIMITE_UPLOAD_MB = 50;
  // imagens entram como prova avulsa SEM camada de texto (o visualizador mostra o original) —
  // espelha _IMAGENS_PROVA do back; a extensão é só a primeira barreira, a assinatura é a segunda
  var EXTENSOES_UPLOAD = ["pdf", "csv", "xls", "xlsx", "docx", "txt", "html", "htm", "zip", "jpg", "jpeg", "png"];

  function textoAjudaUpload(destino) {
    return "Os arquivos vão para " + destino + " e já aparecem escolhidos na lista abaixo. Até " + LIMITE_UPLOAD_MB + " MB por arquivo (ajustável em Administração → Desempenho) — exceto o ZIP, sem limite de tamanho (como o .ufdr). ZIP também é aceito: os documentos de dentro (PDF, CSV, XLS, XLSX, DOCX, TXT, HTML) são extraídos e cada um vira um arquivo da lista. JPG/PNG entram como prova sem texto (abrem no original).";
  }

  $.getJSON("/api/documentos/upload/limite")
    .done(function (r) {
      var mb = r && parseInt(r.limite_mb, 10);
      if (!mb || mb < 1 || mb > 500) return;
      LIMITE_UPLOAD_MB = mb;
      MAX_BYTES_UPLOAD = mb * 1024 * 1024;
    });

  function extensaoDe(nome) {
    var ponto = (nome || "").lastIndexOf(".");
    return ponto === -1 ? "" : nome.slice(ponto + 1).toLowerCase();
  }

  function criarUploadModal(opts) {
    if (!opts.$input.length || !opts.$dropZone.length || !opts.$status.length) {
      return { limpar: function () {} };
    }
    var enviando = false;
    // O padrão é o envio de documento genérico (POST /api/documentos/upload); o upload do
    // SIMBA passa url/extensoes/msgTipo próprios (ver uploadSimbaModal abaixo).
    var urlUpload = opts.url || "/api/documentos/upload";
    var extensoesUpload = opts.extensoes || EXTENSOES_UPLOAD;
    var msgTipoUpload = opts.msgTipo || "tipo não suportado — envie PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG/PNG ou ZIP.";

    function statusUpload(text, kind) {
      opts.$status.text(text || "").removeClass("success error").addClass(kind || "");
    }

    function listarResultado(enviados, falhas) {
      opts.$status.nextAll(".upload-lista").remove();
      if (!enviados.length && !falhas.length) return;
      var $lista = $('<ul class="upload-lista">');
      enviados.forEach(function (a) {
        var $li = $("<li>")
          .append($('<span class="upload-marca" aria-hidden="true">').html(MARCA_OK))
          .append($("<span>").text(a.nome + " (" + formatarBytes(a.tamanho_bytes) + ")"));
        // duplicado anunciado: o servidor nunca sobrescreve — grava "nome (2).pdf" e a pessoa
        // precisa saber que o nome mudou (senão importa achando que é o outro arquivo)
        if (a.renomeado) {
          $li.append($("<em>").addClass("upload-renomeado")
            .text(" — já existia um com este nome, entrou como está acima"));
          $li.addClass("upload-aviso");
        }
        $li.appendTo($lista);
      });
      falhas.forEach(function (msg) {
        $('<li class="upload-erro">')
          .append($('<span class="upload-marca" aria-hidden="true">').html(MARCA_ERRO))
          .append($("<span>").text(msg))
          .appendTo($lista);
      });
      $lista.insertAfter(opts.$status);
    }

    // Um arquivo por requisição, e não todos num FormData só: dá progresso ("2/5") e erro por
    // arquivo, e mantém cada corpo dentro do client_max_body_size do nginx.
    function enviarArquivos(arquivos) {
      if (enviando || !arquivos || !arquivos.length) return;

      var fila = [];
      var falhas = [];
      for (var i = 0; i < arquivos.length; i++) {
        var arquivo = arquivos[i];
        if (extensoesUpload.indexOf(extensaoDe(arquivo.name)) === -1) {
          falhas.push(arquivo.name + ": " + msgTipoUpload);
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
        listarResultado([], falhas);
        if (window.Toast) Toast.erro(falhas[0]);
        return;
      }

      enviando = true;
      opts.$dropZone.addClass("disabled");
      opts.$btn.prop("disabled", true);
      opts.$status.nextAll(".upload-lista").remove();

      var enviados = [];

      // Pool de 3 envios simultâneos com barra de bytes agregada. Antes era um arquivo por
      // vez com status de texto ("Enviando 3/40…") — lote grande parecia travado, e o garfo
      // de rede ficava todo ocioso. A barra soma os bytes de todos (os em voo entram pela
      // contagem parcial do upload.onprogress de cada XHR).
      var SIMULTANEOS = 3;
      var totalBytes = 0;
      fila.forEach(function (a) { totalBytes += a.size || 0; });
      var feitosBytes = 0, feitosQt = 0, ativos = 0, proximoIdx = 0;
      var $barra = null, $cheio = null;
      if (totalBytes > 0) {
        $barra = $('<div class="upload-progresso" role="progressbar" aria-label="Progresso do upload">')
          .append('<div class="upload-progresso-cheio"></div>');
        $cheio = $barra.find(".upload-progresso-cheio");
        $barra.insertAfter(opts.$status);
      }
      function atualizarBarra(voo) {
        if (!$cheio) return;
        var pct = Math.min(100, Math.round(((feitosBytes + (voo || 0)) * 100) / totalBytes));
        $cheio.css("width", pct + "%");
      }

      function enviarUm(arquivo) {
        ativos++;
        statusUpload(feitosQt + "/" + fila.length + " enviados — último: " + arquivo.name + "…");

        var dados = new FormData();
        dados.append("arquivos", arquivo, arquivo.name);
        if (delegaciaParaApi()) dados.append("delegacia", delegaciaParaApi());

        function noProgresso(e) { if (e.lengthComputable) atualizarBarra(e.loaded); }

        $.ajax({
          // o .zip vai pela rota sem limite (como o .ufdr); os demais, pela rota comum.
          // (No modal do SIMBA o ZIP nem chega aqui: só .csv passa na validação.)
          url: extensaoDe(arquivo.name) === "zip" ? "/api/documentos/upload-zip" : urlUpload,
          method: "POST",
          data: dados,
          processData: false,
          contentType: false,
          xhr: function () {
            var x = $.ajaxSettings.xhr();
            if (x && x.upload) x.upload.addEventListener("progress", noProgresso);
            return x;
          },
        })
          .done(function (resp) {
            enviados = enviados.concat(resp.arquivos || []);
            falhas = falhas.concat(resp.falhas || []);
          })
          .fail(function (xhr) {
            var msgErro = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
            falhas.push(arquivo.name + ": " + msgErro);
          })
          .always(function () {
            feitosBytes += arquivo.size || 0;
            feitosQt++;
            ativos--;
            atualizarBarra(0);
            if (proximoIdx < fila.length) { enviarUm(fila[proximoIdx++]); return; }
            if (ativos > 0) return;

            // fila e voos todos encerrados: fecha a barra e mostra o balanço
            if ($barra) $barra.remove();
            enviando = false;
            opts.$dropZone.removeClass("disabled");
            listarResultado(enviados, falhas);
            if (!enviados.length) {
              statusUpload("Nenhum arquivo pôde ser enviado.", "error");
              opts.$btn.prop("disabled", false);
              if (window.Toast) Toast.erro(falhas[0]);
              return;
            }
            var destino = delegaciaParaApi() ? "data/" + delegaciaAtual + "/" : "data/";
            var msg = enviados.length === 1
              ? "1 arquivo enviado para " + destino + "."
              : enviados.length + " arquivos enviados para " + destino + ".";
            if (falhas.length) msg += " " + falhas.length + " recusado(s).";
            statusUpload(msg, falhas.length ? "error" : "success");
            if (window.Toast) Toast.sucesso(msg);
            // recarrega a lista e já deixa marcado o que subiu — só falta confirmar e importar
            opts.recarregar().always(function () {
              if (opts.aposEnviar) {
                opts.aposEnviar(enviados);
                return;
              }
              var ts = opts.obterTs ? opts.obterTs() : null;
              if (ts) {
                enviados.forEach(function (a) { try { ts.addItem(a.nome, true); } catch (e) {} });
                try { ts.refreshItems(); } catch (e) {}
              }
              // reabilita mesmo se a listagem falhar: com o upload aceito, há documento para importar
              opts.$btn.prop("disabled", false);
            });
          });
      }

      while (ativos < SIMULTANEOS && proximoIdx < fila.length) enviarUm(fila[proximoIdx++]);
    }

    opts.$input.on("change", function () {
      enviarArquivos(this.files);
      // zera o input para que reenviar o MESMO arquivo dispare "change" de novo
      this.value = "";
    });

    opts.$dropZone.on("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!enviando) opts.$dropZone.addClass("drop-zone-active");
    });

    opts.$dropZone.on("dragleave", function (e) {
      e.preventDefault();
      e.stopPropagation();
      opts.$dropZone.removeClass("drop-zone-active");
    });

    opts.$dropZone.on("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      opts.$dropZone.removeClass("drop-zone-active");
      if (enviando) return;
      var dt = e.originalEvent.dataTransfer;
      if (!dt) return;
      // pasta solta inteira: recolhe os arquivos de dentro (inclusive de subpastas) e achatam
      PastaArraste.coletar(dt).always(function (arquivos) {
        if (arquivos && arquivos.length) enviarArquivos(arquivos);
      });
    });

    return {
      limpar: function () {
        enviando = false;
        opts.$dropZone.removeClass("disabled drop-zone-active");
        opts.$status.text("").removeClass("success error");
        opts.$status.nextAll(".upload-lista").remove();
      }
    };
  }

  var uploadDocumentoModal = criarUploadModal({
    $input: $documentoUploadInput,
    $dropZone: $documentoDropZone,
    $status: $documentoUploadStatus,
    $btn: $documentoBtn,
    obterTs: function () { return tsModalDocumento; },
    recarregar: carregarModalDocumentosDisponiveis
  });

  // O seletor do SIMBA é único, então de uma leva de uploads quem fica escolhido é o último
  // que subiu; o servidor confere o cabeçalho do R304 antes de aceitar (ver simba-upload).
  var uploadSimbaModal = criarUploadModal({
    $input: $simbaUploadInput,
    $dropZone: $simbaDropZone,
    $status: $simbaUploadStatus,
    $btn: $simbaBtn,
    url: "/api/investigacao/simba-upload",
    extensoes: ["csv"],
    msgTipo: "envie o CSV do relatório R304 do SIMBA.",
    recarregar: carregarModalSimbaDisponiveis,
    aposEnviar: function (enviados) {
      var ultimo = enviados[enviados.length - 1];
      if (ultimo) $simbaSelect.val(ultimo.nome);
      // reabilita mesmo se a listagem falhar: com o upload aceito, há arquivo para importar
      $simbaBtn.prop("disabled", false);
    }
  });

  function textoAjudaUploadSimba(destino) {
    return "O CSV do relatório R304 vai para " + destino + " e já aparece escolhido na lista abaixo. Até " + LIMITE_UPLOAD_MB + " MB por arquivo (ajustável em Administração → Desempenho). Só o relatório R304 é aceito — o cabeçalho é conferido na hora.";
  }

  function textoAjudaUploadRif(destino) {
    return "A pasta inteira (os CSVs e o PDF) vai para " + destino + " e já aparece escolhida na lista abaixo. Até " + LIMITE_UPLOAD_MB + " MB por arquivo (ajustável em Administração → Desempenho). Só CSV e PDF entram — os demais arquivos da pasta são ignorados. O import usa *_Comunicacoes, *_Envolvidos, *_Ocorrencias e o 1º PDF.";
  }

  // ---- Envio da pasta do RIF pelo navegador (aba "RIF - COAF" do modal) ----
  // O RIF é uma PASTA, e não um arquivo: diferente do criarUploadModal acima (um arquivo por
  // requisição), aqui a pasta inteira vai num multipart só, com o caminho relativo de cada
  // arquivo (webkitRelativePath). Espelha js/importacao.js. Uma requisição por pasta, então
  // arrastar duas pastas vira dois envios em sequência; o seletor fica na última que subiu.
  function criarUploadRifModal(opts) {
    if (!opts.$input.length || !opts.$dropZone.length || !opts.$status.length) {
      return { limpar: function () {} };
    }
    var enviando = false;

    function statusUpload(text, kind) {
      opts.$status.text(text || "").removeClass("success error").addClass(kind || "");
    }

    function listarResultado(pastas, falhas) {
      opts.$status.nextAll(".upload-lista").remove();
      if (!pastas.length && !falhas.length) return;
      var $lista = $('<ul class="upload-lista">');
      pastas.forEach(function (r) {
        $("<li>")
          .append($('<span class="upload-marca" aria-hidden="true">').html(MARCA_OK))
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
          .append($('<span class="upload-marca" aria-hidden="true">').html(MARCA_ERRO))
          .append($("<span>").text(msg))
          .appendTo($lista);
      });
      $lista.insertAfter(opts.$status);
    }

    function enviarArquivos(arquivos) {
      if (enviando || !arquivos || !arquivos.length) return;

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
        statusUpload("Nenhuma pasta pôde ser enviada.", "error");
        listarResultado([], falhas);
        if (window.Toast) Toast.erro(falhas[0]);
        return;
      }

      enviando = true;
      opts.$dropZone.addClass("disabled");
      opts.$btn.prop("disabled", true);
      opts.$status.nextAll(".upload-lista").remove();

      var enviadas = [];
      var indice = 0;

      function enviarProxima() {
        if (indice >= nomesPastas.length) {
          enviando = false;
          opts.$dropZone.removeClass("disabled");
          listarResultado(enviadas, falhas);
          if (!enviadas.length) {
            statusUpload("Nenhuma pasta pôde ser enviada.", "error");
            opts.$btn.prop("disabled", false);
            if (window.Toast) Toast.erro(falhas[0]);
            return;
          }
          var destino = delegaciaParaApi() ? "data/" + delegaciaAtual + "/" : "data/";
          var msg = enviadas.length === 1
            ? "Pasta " + enviadas[0].pasta + " enviada para " + destino + "."
            : enviadas.length + " pastas enviadas para " + destino + ".";
          if (falhas.length) msg += " " + falhas.length + " recusa(s).";
          statusUpload(msg, falhas.length ? "error" : "success");
          if (window.Toast) Toast.sucesso(msg);
          // recarrega a lista e já deixa escolhida a última pasta que subiu — só falta confirmar
          opts.recarregar().always(function () {
            if (opts.aposEnviar) opts.aposEnviar(enviadas);
          });
          return;
        }

        var pasta = nomesPastas[indice];
        statusUpload("Enviando " + (indice + 1) + "/" + nomesPastas.length + ": pasta " + pasta + "…");

        var dados = new FormData();
        porPasta[pasta].forEach(function (arquivo) {
          dados.append("arquivos", arquivo, arquivo.webkitRelativePath || arquivo.__caminho || arquivo.name);
        });
        if (delegaciaParaApi()) dados.append("delegacia", delegaciaParaApi());

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
            var msgErro = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao enviar";
            falhas.push(pasta + ": " + msgErro);
          })
          .always(function () {
            indice++;
            enviarProxima();
          });
      }

      enviarProxima();
    }

    opts.$input.on("change", function () {
      enviarArquivos(this.files);
      // zera o input para que reenviar a MESMA pasta dispare "change" de novo
      this.value = "";
    });

    opts.$dropZone.on("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!enviando) opts.$dropZone.addClass("drop-zone-active");
    });

    opts.$dropZone.on("dragleave", function (e) {
      e.preventDefault();
      e.stopPropagation();
      opts.$dropZone.removeClass("drop-zone-active");
    });

    opts.$dropZone.on("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      opts.$dropZone.removeClass("drop-zone-active");
      if (enviando) return;
      var dt = e.originalEvent.dataTransfer;
      if (!dt) return;
      // o RIF é uma PASTA: dataTransfer.files não traz o conteúdo dela — PastaArraste desce a
      // pasta via webkitGetAsEntry e devolve os arquivos com o caminho relativo (__caminho)
      PastaArraste.coletar(dt).always(function (arquivos) {
        if (arquivos && arquivos.length) enviarArquivos(arquivos);
      });
    });

    return {
      limpar: function () {
        enviando = false;
        opts.$dropZone.removeClass("disabled drop-zone-active");
        opts.$status.text("").removeClass("success error");
        opts.$status.nextAll(".upload-lista").remove();
      }
    };
  }

  var uploadRifModal = criarUploadRifModal({
    $input: $rifUploadInput,
    $dropZone: $rifDropZone,
    $status: $rifUploadStatus,
    $btn: $rifBtn,
    recarregar: carregarModalRifsDisponiveis,
    aposEnviar: function (enviadas) {
      var ultima = enviadas[enviadas.length - 1];
      if (ultima) $rifSelect.val(ultima.pasta);
      // reabilita mesmo se a listagem falhar: com o upload aceito, há pasta para importar
      $rifBtn.prop("disabled", false);
    }
  });

  var carregandoModal = false;
  function recarregarModalListas() {
    if (carregandoModal) return $.Deferred().resolve().promise();
    carregandoModal = true;
    $arquivoSelect.prop("disabled", true); $rifSelect.prop("disabled", true); $simbaSelect.prop("disabled", true);
    if (tsModalDocumento) tsModalDocumento.disable();
    var p1 = carregarModalArquivos();
    var p2 = carregarModalDocumentosDisponiveis();
    var p3 = carregarModalRifsDisponiveis();
    var p4 = carregarModalSimbaDisponiveis();
    var p5 = carregarModalIpedDisponiveis();
    return $.when(p1, p2, p3, p4, p5).always(function () {
      carregandoModal = false;
      $arquivoSelect.prop("disabled", false); $rifSelect.prop("disabled", false); $simbaSelect.prop("disabled", false);
      if (tsModalDocumento) tsModalDocumento.enable();
      if (ipedDetectando) $ipedBtn.prop("disabled", true);
    });
  }

  // IPED conta detection (modal)
  $ipedSelect.on("change", function () {
    var pasta = $ipedSelect.val();
    $ipedContaInput.val(""); $ipedContaCampo.addClass("hidden").attr("hidden", "");
    if (!pasta) {
      $("#modal-iped-help").text($("#modal-iped-help").text().indexOf("central") !== -1 ? $("#modal-iped-help").text() : "");
      $ipedBtn.prop("disabled", $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().val() === "");
      ipedDetectando = false; return;
    }
    var pastasValida = !($ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().text().indexOf("Nenhuma") === 0);
    if (!pastasValida) return;
    ipedDetectando = true; $ipedBtn.prop("disabled", true);
    $("#modal-iped-help").html('<span class="spinner" style="display:inline-block;vertical-align:middle;margin-right:6px;"></span> Detectando conta…');
    $.ajax({ url: "/api/investigacao/iped-detectar-conta", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: pasta, delegacia: delegaciaParaApi() || null }) })
      .done(function (r) { if (r.conta) $ipedContaInput.val(r.conta); $ipedContaCampo.removeClass("hidden").removeAttr("hidden"); })
      .fail(function () { $ipedContaCampo.removeClass("hidden").removeAttr("hidden"); })
      .always(function () {
        ipedDetectando = false;
        var vazio = $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().val() === "";
        var semPastas = $ipedSelect.find("option").length === 1 && $ipedSelect.find("option").first().text().indexOf("Nenhuma") === 0;
        $ipedBtn.prop("disabled", vazio || semPastas);
        if (!vazio && !semPastas) $("#modal-iped-help").text("");
      });
  });

  // ---- Submit handlers ----
  $ufdrForm.on("submit", function (e) {
    e.preventDefault();
    var arquivo = $arquivoSelect.val();
    var numero = validarNumeroInquerito($ufdrStatus);
    if (numero === null) return;
    if (!arquivo) { $ufdrStatus.text("Selecione um arquivo .ufdr.").removeClass("success").addClass("error"); if (window.Toast) Toast.erro("Selecione um arquivo .ufdr."); return; }
    $ufdrStatus.text("").removeClass("success error"); esconderLinks();
    $ufdrBtn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');
    $.ajax({ url: "/api/investigacao/extrair", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, modelo_transcricao: $qualidadeSelect.val() || null, transcrever_video: $transcreverVideo.is(":checked"), delegacia: delegaciaParaApi() || null, caso_inteligencia: eCasoInteligencia() }) })
      .done(function () {
        var msg = "Extração iniciada — acompanhe o progresso na página de Investigação.";
        $ufdrStatus.text(msg).removeClass("error").addClass("success"); if (window.Toast) Toast.sucesso(msg); revelarLink($ufdrLink);
        if (window.carregarInqueritos) window.carregarInqueritos();
      })
      .fail(function (xhr) { var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a extração."; $ufdrStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg); })
      .always(function () { $ufdrBtn.prop("disabled", false).html(ICONE_EXTRAIR + " Importar"); });
  });

  $documentoForm.on("submit", function (e) {
    e.preventDefault();
    var arquivos = tsModalDocumento ? tsModalDocumento.getValue() : [];
    var numero = validarNumeroInquerito($documentoStatus);
    if (numero === null) return;
    if (!arquivos.length) { $documentoStatus.text("Selecione ao menos um documento.").removeClass("success").addClass("error"); if (window.Toast) Toast.erro("Selecione ao menos um documento."); return; }
    $documentoStatus.text("").removeClass("success error"); esconderLinks();
    $documentoBtn.prop("disabled", true);
    var qtSucesso = 0, qtReconhecidos = 0, falhas = [], i = 0;
    // Polling do job de ZIP (mesmo desenho de js/importacao.js): o POST devolve o id_job na
    // hora e o lote é acompanhado aqui — sem isso, centenas de documentos estourariam o
    // proxy_read_timeout de 300 s do nginx.
    // Também usado pela importação de documento PESADO enfileirada (job import_documento): o
    // resultado vem no mesmo formato e o rótulo do próprio job (j.rotulo) dá o texto.
    function acompanharJobZip(idJob, arquivoZip, aoTerminar) {
      var encerrado = false;
      var timer = null;
      var $cancelar = $('<button type="button" class="btn btn-secondary">Cancelar importação</button>');
      $cancelar.on("click", function () {
        $cancelar.prop("disabled", true).text("Cancelando…");
        $.ajax({ url: "/api/investigacao/jobs/" + idJob + "/cancelar", method: "POST" })
          .fail(function (xhr) {
            $cancelar.prop("disabled", false).text("Cancelar importação");
            if (window.Toast) Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível cancelar.");
          });
      });
      $documentoStatus.after($cancelar);
      $documentoStatus.text("Importação iniciada em background — pode fechar este modal e acompanhar na Investigação.");
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
            var rotulo = j.rotulo === "importação de documento" ? "documento" : "ZIP";
            if (fase === "expandindo" || (fase === "extraindo" && !total)) {
              $documentoBtn.html('<span class="spinner"></span> Extraindo texto do ' + rotulo + "…");
            } else {
              $documentoBtn.html('<span class="spinner"></span> Importando ' + rotulo + " " + feitos + "/" + (total || 1) + "…");
            }
            if (j.rodando) return;
            finalizar();
            var r = j.tx_resultado || {};
            if (j.cd_status === "concluido") {
              qtSucesso += (r.qt_anexados || 0);
              qtReconhecidos += (r.qt_reconhecidos || 0);
              (r.falhas || []).forEach(function (f) { falhas.push(arquivoZip + ": " + f); });
              if (r.truncado) falhas.push(arquivoZip + ": ZIP truncado no limite (2000 docs/20GB) — resto ignorado.");
              if (window.Toast) Toast.sucesso(arquivoZip + ": " + (r.qt_anexados || 0) + " documento(s) anexado(s).");
            } else if (j.cd_status === "cancelado") {
              qtSucesso += (r.qt_anexados || 0);
              falhas.push(arquivoZip + ": importação cancelada (" + (r.qt_anexados || 0) + " anexado(s) antes).");
              (r.falhas || []).forEach(function (f) { falhas.push(arquivoZip + ": " + f); });
            } else {
              falhas.push(arquivoZip + ": " + (j.tx_erro || "falha na importação."));
            }
            carregarModalDocumentosDisponiveis().always(function () { aoTerminar(); });
          })
          .fail(function () { /* transitório: tenta de novo no próximo tick */ });
      }, 2000);
    }
    function extrairProximo() {
      if (i >= arquivos.length) {
        var msg;
        if (!falhas.length) {
          msg = qtSucesso === 1 ? "Documento anexado" : formatarNumero(qtSucesso) + " documentos anexados";
          if (qtReconhecidos) msg += " (" + formatarNumero(qtReconhecidos) + " reconhecido(s) como inquérito)";
          msg += " — já disponíveis para a IA.";
          $documentoStatus.text(msg).removeClass("error").addClass("success"); if (window.Toast) Toast.sucesso(msg); revelarLink($documentoLink);
          if (window.carregarInqueritos) window.carregarInqueritos();
        } else {
          msg = qtSucesso + " documento(s) anexado(s). Falhas: " + falhas.join("; ");
          $documentoStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg);
        }
        $documentoBtn.prop("disabled", false).html(ICONE_DOCUMENTO + " Importar");
        return;
      }
      var arquivo = arquivos[i];
      $documentoBtn.html('<span class="spinner"></span> Extraindo ' + (i + 1) + "/" + arquivos.length + "…");
      var noJob = false;
      $.ajax({ url: "/api/investigacao/extrair-documento", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaParaApi() || null, caso_inteligencia: eCasoInteligencia() }) })
        .done(function (resp) {
          if (resp.nu_inquerito && !numero) numero = resp.nu_inquerito;
          if (resp.job_queued) {
            noJob = true;
            acompanharJobZip(resp.id_job, arquivo, function () { i++; extrairProximo(); });
            return;
          }
          if (resp.tx_erro) falhas.push(arquivo + ": anexado sem texto (" + resp.tx_erro + ")");
          else { qtSucesso++; if (resp.inquerito) qtReconhecidos++; }
        })
        .fail(function (xhr) { var msg2 = (xhr.responseJSON && xhr.responseJSON.detail) || "erro ao extrair"; falhas.push(arquivo + ": " + msg2); })
        .always(function () { if (noJob) return; i++; extrairProximo(); });
    }
    extrairProximo();
  });

  $rifForm.on("submit", function (e) {
    e.preventDefault();
    var pasta = $rifSelect.val();
    var numero = validarNumeroInquerito($rifStatus);
    if (numero === null) return;
    if (!pasta) { $rifStatus.text("Selecione uma pasta de RIF.").removeClass("success").addClass("error"); if (window.Toast) Toast.erro("Selecione uma pasta de RIF."); return; }
    $rifStatus.text("").removeClass("success error"); esconderLinks();
    $rifBtn.prop("disabled", true).html('<span class="spinner"></span> Importando…');
    $.ajax({ url: "/api/investigacao/extrair-rif", method: "POST", contentType: "application/json", data: JSON.stringify({ pasta: pasta, numero_inquerito: numero, delegacia: delegaciaParaApi() || null, caso_inteligencia: eCasoInteligencia() }) })
      .done(function (resp) {
        var msg = "RIF importado — " + formatarNumero(resp.qt_comunicacoes) + " comunicações, " + formatarNumero(resp.qt_envolvidos) + " envolvidos, " + formatarNumero(resp.qt_ocorrencias) + " ocorrências.";
        if (resp.tem_pdf && resp.tx_erro_pdf) { msg += " PDF anexado, mas sem texto: " + resp.tx_erro_pdf; $rifStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg); }
        else { if (resp.tem_pdf) msg += " PDF anexado aos documentos."; $rifStatus.text(msg).removeClass("error").addClass("success"); if (window.Toast) Toast.sucesso(msg); revelarLink($rifLink); if (window.carregarInqueritos) window.carregarInqueritos(); }
      })
      .fail(function (xhr) { var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao importar o RIF."; $rifStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg); })
      .always(function () { $rifBtn.prop("disabled", false).html(ICONE_RIF + " Importar"); });
  });

  $simbaForm.on("submit", function (e) {
    e.preventDefault();
    var arquivo = $simbaSelect.val();
    var numero = validarNumeroInquerito($simbaStatus);
    if (numero === null) return;
    if (!arquivo) { $simbaStatus.text("Selecione um arquivo R304.").removeClass("success").addClass("error"); if (window.Toast) Toast.erro("Selecione um arquivo R304."); return; }
    $simbaStatus.text("").removeClass("success error"); esconderLinks();
    $simbaBtn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');
    $.ajax({ url: "/api/investigacao/extrair-simba", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaParaApi() || null, caso_inteligencia: eCasoInteligencia() }) })
      .done(function () { var msg = "Importação iniciada — acompanhe o progresso no bloco do inquérito na página de Investigação."; $simbaStatus.text(msg).removeClass("error").addClass("success"); if (window.Toast) Toast.sucesso(msg); revelarLink($simbaLink); if (window.carregarInqueritos) window.carregarInqueritos(); })
      .fail(function (xhr) { var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a importação."; $simbaStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg); })
      .always(function () { $simbaBtn.prop("disabled", false).html(ICONE_SIMBA + " Importar"); });
  });

  $ipedForm.on("submit", function (e) {
    e.preventDefault();
    if (ipedDetectando) { $ipedStatus.text("Aguarde a detecção da conta terminar…").removeClass("success").addClass("error"); if (window.Toast) Toast.aviso("Aguarde a detecção da conta terminar…"); return; }
    var arquivo = $ipedSelect.val();
    var numero = validarNumeroInquerito($ipedStatus);
    if (numero === null) return;
    var conta = $.trim($ipedContaInput.val());
    if (!arquivo) { $ipedStatus.text("Selecione uma pasta de caso do IPED.").removeClass("success").addClass("error"); if (window.Toast) Toast.erro("Selecione uma pasta de caso do IPED."); return; }
    $ipedStatus.text("").removeClass("success error"); esconderLinks();
    $ipedBtn.prop("disabled", true).html('<span class="spinner"></span> Iniciando…');
    $.ajax({ url: "/api/investigacao/extrair-iped", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: arquivo, numero_inquerito: numero, delegacia: delegaciaParaApi() || null, conta: conta || null, caso_inteligencia: eCasoInteligencia() }) })
      .done(function () { var msg = "Importação iniciada — acompanhe o progresso no bloco do inquérito na página de Investigação."; $ipedStatus.text(msg).removeClass("error").addClass("success"); if (window.Toast) Toast.sucesso(msg); revelarLink($ipedLink); if (window.carregarInqueritos) window.carregarInqueritos(); })
      .fail(function (xhr) { var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a importação."; $ipedStatus.text(msg).removeClass("success").addClass("error"); if (window.Toast) Toast.erro(msg); })
      .always(function () { $ipedBtn.prop("disabled", false).html(ICONE_EXTRAIR + " Importar"); });
  });

  // ---- Modal open/close ----
  function fecharModal() {
    $overlay.addClass("hidden").attr("hidden", "").attr("aria-hidden", "true");
    $("body").css("overflow", "");
    // limpa status
    $ufdrStatus.text("").removeClass("success error");
    $ipedStatus.text("").removeClass("success error");
    $documentoStatus.text("").removeClass("success error");
    $rifStatus.text("").removeClass("success error");
    $simbaStatus.text("").removeClass("success error");
    if (typeof uploadDocumentoModal !== "undefined" && uploadDocumentoModal) uploadDocumentoModal.limpar();
    if (typeof uploadSimbaModal !== "undefined" && uploadSimbaModal) uploadSimbaModal.limpar();
    if (typeof uploadRifModal !== "undefined" && uploadRifModal) uploadRifModal.limpar();
    esconderLinks();
    // devolve o foco a quem abriu o modal (botão Importar do card)
    if (ultimoFocoAntesDoModal && ultimoFocoAntesDoModal.focus) {
      try { ultimoFocoAntesDoModal.focus(); } catch (e) {}
    }
    ultimoFocoAntesDoModal = null;
  }

  window.abrirModalImportar = function (item) {
    itemAtual = item || null;
    if (!itemAtual) return;
    try { ultimoFocoAntesDoModal = document.activeElement || null; } catch (e) { ultimoFocoAntesDoModal = null; }
    delegaciaAtual = (itemAtual.delegacia && itemAtual.delegacia !== "—") ? itemAtual.delegacia : "";

    if (typeof uploadDocumentoModal !== "undefined" && uploadDocumentoModal) uploadDocumentoModal.limpar();
    if (typeof uploadSimbaModal !== "undefined" && uploadSimbaModal) uploadSimbaModal.limpar();
    if (typeof uploadRifModal !== "undefined" && uploadRifModal) uploadRifModal.limpar();
    var destinoUpload = delegaciaAtual ? "data/" + delegaciaAtual + "/" : "data/";
    $("#modal-documento-upload-help").text(textoAjudaUpload(destinoUpload));
    $("#modal-simba-upload-help").text(textoAjudaUploadSimba(destinoUpload));
    $("#modal-rif-upload-help").text(textoAjudaUploadRif(destinoUpload));

    // título e painel (sempre o do inquérito — o número do card já vem preenchido)
    var nu = itemAtual.nu_inquerito || itemAtual.nome_item || itemAtual.id_item || "";
    $subtitulo.text((eCasoInteligencia() ? "Caso de inteligência " : "Inquérito ") + (nu || ""));
    $painelInq.removeClass("hidden").removeAttr("hidden").removeAttr("aria-hidden");
    $inqueritoInput.val(nu);
    ativarAbaModal("ufdr");

    atualizarLinksInvestigacao();
    esconderLinks();
    $overlay.removeClass("hidden").removeAttr("hidden").attr("aria-hidden", "false");
    $("body").css("overflow", "hidden");
    // foco inicial
    setTimeout(function () { $inqueritoInput.focus(); }, 80);
    // carrega listas filtradas
    recarregarModalListas();
  };
  window.fecharModalImportar = fecharModal;

  $fechar.on("click", fecharModal);
  $overlay.on("click", function (e) { if (e.target === $overlay[0]) fecharModal(); });
  // também fechar se clicar no overlay fora do modal (propaga do body)
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && !$overlay.hasClass("hidden")) fecharModal();
  });

  // ---- Init TomSelect (depois do DOM pronto) ----
  $(function () {
    try {
      if (window.TomSelect) {
        tsModalDocumento = new TomSelect("#modal-documento-select", { plugins: ["remove_button"], placeholder: "Selecione um ou mais documentos…" });
      }
    } catch (e) {}
    ativarAbaModal("ufdr");
  });

})();
