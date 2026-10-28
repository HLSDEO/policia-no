(function () {
  "use strict";

  var PADRAO_EPOL = /^\d{4}\.\d{7}-\d{2}$/;
  // Limite vigente vem de GET /api/documentos/upload/limite (UPLOAD_DOCUMENTO_MB, ajustável
  // em Administração → Desempenho). Quem decide de verdade é o servidor. O .zip é a exceção,
  // como o .ufdr: sem limite (vai por POST /api/documentos/upload-zip).
  var MAX_UPLOAD = 50 * 1024 * 1024;
  var LIMITE_UPLOAD_MB = 50;
  var EXTENSOES_UPLOAD = ["pdf", "csv", "xlsx", "docx", "txt", "html", "htm", "zip"];

  function aplicarLimiteUploadOrbit(mb) {
    mb = parseInt(mb, 10);
    if (!mb || mb < 1 || mb > 500) return;
    LIMITE_UPLOAD_MB = mb;
    MAX_UPLOAD = mb * 1024 * 1024;
    var texto = "Até " + mb + " MB por arquivo";
    var $a = $("#nova-importacao-upload-status");
    if ($a.length && $a.text()) $a.text($a.text().replace(/Até \d+ MB por arquivo/, texto));
  }

  $.getJSON("/api/documentos/upload/limite")
    .done(function (r) { if (r && r.limite_mb) aplicarLimiteUploadOrbit(r.limite_mb); });
  var FONTES = {
    ufdr: { rotulo: "Indexação Cellebrite (UFDR)", curto: "UFDR Cellebrite", icone: "aparelho", endpointLista: "/api/investigacao/arquivos", endpointEnvio: "/api/investigacao/extrair", campo: "arquivo", label: "Arquivo .ufdr", hint: "Selecione uma extração Cellebrite. Áudios e vídeos podem ser transcritos durante a indexação." },
    rif: { rotulo: "RIF - COAF (CSV)", curto: "RIF · COAF", icone: "rif", endpointLista: "/api/investigacao/rifs-disponiveis", endpointEnvio: "/api/investigacao/extrair-rif", campo: "pasta", label: "Pasta do RIF (em data/)", hint: "Importa comunicações, envolvidos, ocorrências e o PDF do Relatório de Inteligência Financeira." },
    simba: { rotulo: "Bancário SIMBA R304 (CSV)", curto: "SIMBA R304", icone: "banco", endpointLista: "/api/investigacao/simba-disponiveis", endpointEnvio: "/api/investigacao/extrair-simba", campo: "arquivo", label: "Arquivo do SIMBA (R304, em data/)", hint: "Importa contas, lançamentos, contrapartes e alertas do afastamento de sigilo bancário." },
    iped: { rotulo: "Nuvem IPED (pasta do caso)", curto: "Nuvem IPED", icone: "arquivos", endpointLista: "/api/investigacao/iped-disponiveis", endpointEnvio: "/api/investigacao/extrair-iped", campo: "arquivo", label: "Pasta do caso do IPED (em data/)", hint: "Importa o índice e os arquivos de uma pasta-caso processada pelo IPED." },
    documento: { rotulo: "Arquivo genérico", curto: "Outros documentos", icone: "documentos", multiplo: true, endpointLista: "/api/documentos/arquivos", endpointEnvio: "/api/investigacao/extrair-documento", campo: "arquivo", label: "Documentos (PDF, CSV, XLSX, DOCX, TXT, HTML ou ZIP)", hint: "Inclui documentos, IRPF, IPEI de sigilo fiscal e arquivos de telefonia reconhecidos pelo sistema. Um .zip em data/ é expandido (subpastas achatadas) e tudo é importado de uma vez." }
  };

  var fonteAtual = "ufdr";
  var tipoAtual = "inquerito";
  var contexto = { existente: false, numero: "", inquerito: null, delegacia: "", caso: null };
  var $modal;
  var usuarioAtual = null;

  function aplicarPermissoesUsuario(u) {
    if (!u) return;
    usuarioAtual = u;
    var podeCaso = !!u.pode_caso_inteligencia;
    if ($modal && $modal.length) {
      var $btnCaso = $modal.find('.nova-importacao-tipos [data-tipo="caso"]');
      if (!podeCaso) {
        $btnCaso.remove();
        if (tipoAtual === "caso") ativarTipo("inquerito");
        var $botoes = $modal.find('.nova-importacao-tipos [data-tipo]');
        if ($botoes.length <= 1) $modal.find(".nova-importacao-tipos").addClass("hidden");
      }
    }
  }

  $(document).on("analyser:usuario", function (e, u) { aplicarPermissoesUsuario(u); });
  $.getJSON("/api/auth/eu").done(aplicarPermissoesUsuario);

  function icone(nome) { return window.Icones ? window.Icones.html(nome) : ""; }
  function delegaciaStorage() { try { return localStorage.getItem("analyser-delegacia") || ""; } catch (e) { return ""; } }
  // vinculado a uma delegacia: ela é o padrão do contexto — o storage do seletor não existe
  // para esse perfil (as telas travam o seletor na própria delegacia)
  function delegaciaAtual() {
    return contexto.delegacia || delegaciaStorage() ||
      (usuarioAtual && usuarioAtual.id_delegacia && usuarioAtual.no_delegacia) || null;
  }
  function escaparSeletor(valor) { return String(valor || "").replace(/(["\\])/g, "\\$1"); }

  function criarModal() {
    if ($("#modal-nova-importacao-orbit").length) { $modal = $("#modal-nova-importacao-orbit"); return; }
    var html = [
      '<div class="modal-overlay hidden" id="modal-nova-importacao-orbit" aria-hidden="true">',
      ' <div class="modal nova-importacao-modal" role="dialog" aria-modal="true" aria-labelledby="nova-importacao-titulo">',
      '  <div class="modal-header"><div><h2 id="nova-importacao-titulo">Nova Importação</h2><p>Crie um inquérito ou um caso de inteligência a partir da primeira fonte de dados.</p></div><button type="button" class="modal-close" id="nova-importacao-fechar" aria-label="Fechar">&times;</button></div>',
      '  <div class="modal-body">',
      '   <div class="nova-importacao-tipos" role="tablist" aria-label="Tipo de importação"><button type="button" class="ativo" data-tipo="inquerito" role="tab" aria-selected="true">Inquérito</button><button type="button" data-tipo="caso" role="tab" aria-selected="false">Caso de inteligência</button></div>',
      '   <div class="extracao-etapas nova-importacao-etapas" aria-label="Etapas da importação">',
      '    <div class="extracao-etapa ativo" data-etapa="1"><span class="extracao-etapa-numero">1</span><span class="extracao-etapa-texto"><strong id="nova-importacao-etapa-tipo">Inquérito</strong><small>Identifique o caso</small></span></div><span class="extracao-etapa-conector" aria-hidden="true"></span>',
      '    <div class="extracao-etapa" data-etapa="2"><span class="extracao-etapa-numero">2</span><span class="extracao-etapa-texto"><strong>Fonte</strong><small>Escolha os dados</small></span></div><span class="extracao-etapa-conector" aria-hidden="true"></span>',
      '    <div class="extracao-etapa" data-etapa="3"><span class="extracao-etapa-numero">3</span><span class="extracao-etapa-texto"><strong>Configuração</strong><small>Revise e importe</small></span></div>',
      '   </div>',
      '   <section id="nova-importacao-painel-inquerito">',
      '    <div class="field field-inquerito" id="nova-importacao-numero-campo"><label for="nova-importacao-inquerito">Número do inquérito (ePol) <span aria-hidden="true">*</span></label><input id="nova-importacao-inquerito" type="text" placeholder="ex.: 2023.0001234-56" inputmode="numeric" maxlength="15" autocomplete="off" required aria-required="true" pattern="[0-9]{4}\\.[0-9]{7}-[0-9]{2}" aria-describedby="nova-importacao-inquerito-ajuda"><p class="field-help" id="nova-importacao-inquerito-ajuda">Obrigatório. Use o formato completo AAAA.0000000-00.</p></div>',
      '    <p class="nova-importacao-hint hidden" id="nova-importacao-numero-caso-nota" hidden>Número gerado automaticamente (CASO-AAAA.NNNN) ao importar.</p>',
      '    <div class="nova-fonte-opcoes" role="tablist" aria-label="Fonte de dados"></div><p class="nova-importacao-hint" id="nova-importacao-fonte-hint"></p>',
       '    <div class="nova-importacao-upload hidden" id="nova-importacao-upload-bloco"><label class="nova-importacao-drop" for="nova-importacao-upload"><strong>Enviar do computador</strong><span>Arraste os arquivos aqui ou clique para escolher</span><input type="file" id="nova-importacao-upload" multiple accept=".pdf,.csv,.xlsx,.docx,.txt,.html,.htm,.zip" class="sr-only"></label><p class="field-help" id="nova-importacao-upload-status">Até 50 MB por arquivo (ZIP sem limite de tamanho).</p></div>',
      '    <div class="field"><label for="nova-importacao-arquivo" id="nova-importacao-arquivo-label">Arquivo ou pasta</label><select id="nova-importacao-arquivo" required></select><p class="field-help" id="nova-importacao-arquivo-ajuda"></p></div>',
      '    <div class="nova-fonte-configuracao" id="nova-importacao-config-ufdr"><div class="field"><label for="nova-importacao-transcricao">Qualidade da transcrição de áudio</label><select id="nova-importacao-transcricao"><option value="nenhuma">Nenhuma (não transcrever)</option><option value="base">Baixa (mais rápida)</option><option value="small" selected>Média (recomendado)</option><option value="large-v3">Alta (mais precisa)</option></select><p class="field-help">Modelos maiores são mais precisos, mas exigem mais tempo e memória.</p></div><label class="field-check"><input type="checkbox" id="nova-importacao-video"> Transcrever também o áudio dos vídeos</label></div>',
      '    <div class="field hidden" id="nova-importacao-conta-campo"><label for="nova-importacao-conta">Conta identificada no IPED</label><input id="nova-importacao-conta" type="text" placeholder="ex.: alvo@gmail.com"><p class="field-help">Confirme ou edite o identificador detectado antes da importação.</p></div>',
      '   </section>',
      '   <div class="nova-fonte-revisao"><span>' + icone("arquivos") + '</span><div><strong id="nova-importacao-revisao-titulo">Informe o número do inquérito</strong><small id="nova-importacao-revisao-texto">A barra acima acompanha o preenchimento.</small></div></div>',
      '   <div class="form-actions"><button class="btn btn-primary" type="button" id="nova-importacao-enviar">Importar</button><span class="form-status" id="nova-importacao-status" role="status" aria-live="polite"></span></div>',
      '  </div></div></div>'
    ].join("");
    $("body").append(html);
    $modal = $("#modal-nova-importacao-orbit");
    var $opcoes = $modal.find(".nova-fonte-opcoes");
    Object.keys(FONTES).forEach(function (chave) {
      var fonte = FONTES[chave];
      $("<button>").attr({ type: "button", role: "tab", "data-fonte": chave, "aria-selected": String(chave === fonteAtual), title: fonte.rotulo }).toggleClass("ativo", chave === fonteAtual).append($("<span>").html(icone(fonte.icone))).append(document.createTextNode(fonte.rotulo)).appendTo($opcoes);
    });
    if (usuarioAtual) aplicarPermissoesUsuario(usuarioAtual);
    vincularEventos();
  }

  function formatarEpol(valor) {
    var digitos = String(valor || "").replace(/\D/g, "").slice(0, 13);
    if (digitos.length <= 4) return digitos;
    if (digitos.length <= 11) return digitos.slice(0, 4) + "." + digitos.slice(4);
    return digitos.slice(0, 4) + "." + digitos.slice(4, 11) + "-" + digitos.slice(11);
  }

  function numeroValido(mostrarErro) {
    var $campo = $("#nova-importacao-inquerito");
    var valor = $.trim($campo.val());
    var valido = PADRAO_EPOL.test(valor) || (contexto.existente && valor === contexto.numero);
    if (!valido && (mostrarErro || valor)) $campo.attr("aria-invalid", "true");
    else $campo.removeAttr("aria-invalid");
    $("#nova-importacao-inquerito-ajuda").toggleClass("error", !valido && mostrarErro).text(!valido && mostrarErro ? "Informe o número completo no formato AAAA.0000000-00." : (contexto.existente ? "Inquérito atual. O número já está preenchido." : "Obrigatório. Use o formato completo AAAA.0000000-00."));
    return valido;
  }

  function valoresSelecionados(seletor) { var valor = $(seletor).val(); return Array.isArray(valor) ? valor.filter(Boolean) : (valor ? [valor] : []); }

  // Caso de inteligência usa as mesmas fontes do inquérito — só o número muda (gerado
  // automaticamente quando ainda não existe). Por isso a etapa 1 é igual, com o aviso do número.
  function atualizarEtapas(concluido) {
    var etapa = 1, pronto = false;
    var eCaso = tipoAtual === "caso";
    var numeroOk = eCaso || numeroValido(false);
    var arquivos = valoresSelecionados("#nova-importacao-arquivo");
    if (numeroOk) etapa = 2;
    if (numeroOk && arquivos.length) etapa = 3;
    pronto = numeroOk && arquivos.length > 0;
    var fonte = FONTES[fonteAtual];
    if (eCaso) {
      $("#nova-importacao-revisao-titulo").text(arquivos.length ? fonte.curto + " pronta para importação" : "Selecione " + fonte.curto);
      $("#nova-importacao-revisao-texto").text(arquivos.length ? arquivos.length + " item(ns) selecionado(s)." : "O número do caso será gerado ao importar.");
    } else {
      $("#nova-importacao-revisao-titulo").text(!numeroOk ? "Informe o número do inquérito" : (arquivos.length ? fonte.curto + " pronta para importação" : "Selecione " + fonte.curto));
      $("#nova-importacao-revisao-texto").text(arquivos.length ? arquivos.length + " item(ns) selecionado(s)." : "A fonte será vinculada ao número ePol informado.");
    }
    $(".nova-importacao-etapas .extracao-etapa").each(function () {
      var indice = Number($(this).attr("data-etapa"));
      var feito = concluido || indice < etapa;
      $(this).toggleClass("concluido", feito).toggleClass("ativo", !concluido && indice === etapa);
      $(this).find(".extracao-etapa-numero").text(feito ? "✓" : indice);
    });
    $("#nova-importacao-enviar").prop("disabled", !pronto);
  }

  function valorItem(item, tipo) { if (tipo === "rif") return item.pasta; if (tipo === "iped") return item.tx_pasta; return item.nome; }
  function textoItem(item, tipo) { if (tipo === "rif") return item.pasta + (item.nu_rif ? " · RIF " + item.nu_rif : ""); if (tipo === "iped") return item.tx_pasta; var rotulo = (item.nome || "") + (item.tamanho_bytes ? " · " + formatarBytes(item.tamanho_bytes) : ""); if (item.eh_zip) rotulo += " [ZIP — expande e importa tudo]"; return rotulo; }
  function formatarBytes(bytes) { var unidades = ["B", "KB", "MB", "GB"], i = 0, n = Number(bytes || 0); while (n >= 1024 && i < unidades.length - 1) { n /= 1024; i++; } return n ? n.toFixed(1) + " " + unidades[i] : ""; }

  function preencherSelect($select, itens, tipo, selecionados) {
    $select.empty();
    itens.forEach(function (item) { var valor = valorItem(item, tipo); if (valor) $("<option>").val(valor).text(textoItem(item, tipo)).appendTo($select); });
    if (!itens.length) $("<option>").val("").text("Nenhuma fonte disponível para importação").appendTo($select);
    if (selecionados && selecionados.length) $select.val(selecionados);
  }

  function carregarFonte(selecionados) {
    var fonte = FONTES[fonteAtual];
    var $select = $("#nova-importacao-arquivo").prop("multiple", !!fonte.multiplo);
    if (fonte.multiplo) $select.attr("size", "4"); else $select.removeAttr("size");
    $select.empty().append($("<option>").val("").text("Consultando fontes disponíveis…"));
    $("#nova-importacao-arquivo-ajuda").text("Consultando fontes disponíveis…");
    var params = delegaciaAtual() ? { delegacia: delegaciaAtual() } : {};
    $.getJSON(fonte.endpointLista, params).done(function (itens) {
      itens = Array.isArray(itens) ? itens : [];
      if (fonteAtual === "documento") itens = itens.filter(function (item) { return item.suportado && !/r304/i.test(item.nome || ""); });
      preencherSelect($select, itens, fonteAtual, selecionados || []);
      $("#nova-importacao-arquivo-ajuda").text(itens.length ? "Selecione o material que será importado." : "Nenhum arquivo ou pasta compatível foi encontrado.");
      atualizarEtapas();
    }).fail(function () { preencherSelect($select, [], fonteAtual); $("#nova-importacao-arquivo-ajuda").text("Não foi possível consultar as fontes disponíveis agora."); atualizarEtapas(); });
  }

  function ativarFonte(chave) {
    if (!FONTES[chave]) chave = "ufdr";
    fonteAtual = chave;
    var fonte = FONTES[chave];
    $(".nova-fonte-opcoes [data-fonte]").removeClass("ativo").attr("aria-selected", "false").filter('[data-fonte="' + escaparSeletor(chave) + '"]').addClass("ativo").attr("aria-selected", "true");
    $("#nova-importacao-fonte-hint").text(fonte.hint);
    $("#nova-importacao-arquivo-label").text(fonte.label);
    $("#nova-importacao-config-ufdr").toggleClass("hidden", chave !== "ufdr");
    $("#nova-importacao-conta-campo").toggleClass("hidden", chave !== "iped");
    $("#nova-importacao-upload-bloco").toggleClass("hidden", chave !== "documento");
    $("#nova-importacao-status").text("").removeClass("success error");
    carregarFonte();
  }

  function ativarTipo(tipo) {
    if (tipo === "caso" && usuarioAtual && !usuarioAtual.pode_caso_inteligencia) {
      tipo = "inquerito";
    }
    tipoAtual = tipo === "caso" ? "caso" : "inquerito";
    $(".nova-importacao-tipos [data-tipo]").removeClass("ativo").attr("aria-selected", "false").filter('[data-tipo="' + tipoAtual + '"]').addClass("ativo").attr("aria-selected", "true");
    var eCaso = tipoAtual === "caso";
    $("#nova-importacao-numero-campo").toggleClass("hidden", eCaso).prop("hidden", eCaso);
    $("#nova-importacao-numero-caso-nota").toggleClass("hidden", !eCaso).prop("hidden", !eCaso);
    $("#nova-importacao-etapa-tipo").text(eCaso ? "Caso de inteligência" : "Inquérito");
    $("#nova-importacao-status").text("").removeClass("success error");
    ativarFonte(fonteAtual);
    atualizarEtapas();
  }

  function extrairContexto(origem) {
    var item = origem && origem.jquery ? origem[0] : origem;
    var $origem = item && item.nodeType ? $(item) : $();
    var objeto = item && !item.nodeType ? item : {};
    var numero = $origem.attr("data-numero") || objeto.nu_inquerito || objeto.numero || "";
    var tipo = $origem.attr("data-tipo") || objeto.tipo || "inquerito";
    if (tipo !== "caso" && objeto.in_caso_inteligencia) tipo = "caso";
    if (tipo === "caso" && usuarioAtual && !usuarioAtual.pode_caso_inteligencia) {
      tipo = "inquerito";
    }
    return { existente: !!numero, numero: numero, inquerito: $origem.attr("data-inquerito") || objeto.id_inquerito || objeto.id_item || null, delegacia: $origem.attr("data-delegacia") || objeto.delegacia || "", tipo: tipo === "caso" ? "caso" : "inquerito" };
  }

  function abrir(origem) {
    criarModal();
    contexto = extrairContexto(origem || {});
    if (contexto.existente) contexto.numero = String(contexto.numero);
    $("#nova-importacao-inquerito").val(contexto.existente ? contexto.numero : (contexto.numero ? formatarEpol(contexto.numero) : "")).prop("readonly", contexto.existente);
    ativarTipo(contexto.tipo);
    $modal.removeClass("hidden").attr("aria-hidden", "false");
    $("body").css("overflow", "hidden");
    window.setTimeout(function () { $(tipoAtual === "caso" && !contexto.existente ? ".nova-fonte-opcoes button:first" : (contexto.existente ? ".nova-fonte-opcoes button:first" : "#nova-importacao-inquerito")).trigger("focus"); }, 0);
  }

  function fechar() { if ($modal) $modal.addClass("hidden").attr("aria-hidden", "true"); $("body").css("overflow", ""); }
  function status(texto, tipo) { $("#nova-importacao-status").text(texto || "").removeClass("success error").addClass(tipo || ""); if (tipo === "error" && window.Toast) Toast.erro(texto); }

  function concluirInquerito(numero, resposta) {
    atualizarEtapas(true);
    status("Importação iniciada. O progresso já pode ser acompanhado no inquérito.", "success");
    if (contexto.existente && location.pathname.indexOf("inquerito_orbit.html") !== -1) { window.setTimeout(function () { location.reload(); }, 900); return; }
    var id = (resposta && resposta.id_inquerito) || contexto.inquerito;
    if (id) { window.setTimeout(function () { location.href = "inquerito_orbit.html?inquerito=" + encodeURIComponent(id); }, 650); return; }
    window.setTimeout(function () { $.getJSON("/api/investigacao/inqueritos").done(function (lista) { var criado = (lista || []).find(function (item) { return String(item.nu_inquerito) === String(numero); }); location.href = criado ? "inquerito_orbit.html?inquerito=" + encodeURIComponent(criado.id_inquerito) : "inqueritos_orbit.html"; }).fail(function () { location.href = "inqueritos_orbit.html"; }); }, 800);
  }

  function enviarInquerito() {
    var eCaso = tipoAtual === "caso";
    if (!eCaso && !numeroValido(true)) { atualizarEtapas(); return; }
    var arquivos = valoresSelecionados("#nova-importacao-arquivo");
    if (!arquivos.length) { status("Selecione ao menos uma fonte de dados.", "error"); return; }
    var fonte = FONTES[fonteAtual], numero = $.trim($("#nova-importacao-inquerito").val());
    var $botao = $("#nova-importacao-enviar").prop("disabled", true).html('<span class="spinner"></span> Importando…');
    var indice = 0, respostaFinal = null;
    function proximo() {
      if (indice >= arquivos.length) { $botao.text("Importar"); concluirInquerito(numero, respostaFinal); return; }
      var payload = { numero_inquerito: numero, delegacia: delegaciaAtual(), caso_inteligencia: eCaso };
      payload[fonte.campo] = arquivos[indice];
      if (fonteAtual === "ufdr") { payload.modelo_transcricao = $("#nova-importacao-transcricao").val() || null; payload.transcrever_video = $("#nova-importacao-video").prop("checked"); }
      if (fonteAtual === "iped") payload.conta = $.trim($("#nova-importacao-conta").val()) || null;
      $.ajax({ url: fonte.endpointEnvio, method: "POST", contentType: "application/json", data: JSON.stringify(payload) }).done(function (resposta) { respostaFinal = resposta || respostaFinal; if (eCaso && !numero && resposta && resposta.nu_inquerito) numero = resposta.nu_inquerito; indice++; proximo(); }).fail(function (xhr) { $botao.text("Importar"); atualizarEtapas(); status((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível iniciar a importação.", "error"); });
    }
    proximo();
  }

  function extensao(nome) { var partes = String(nome || "").toLowerCase().split("."); return partes.length > 1 ? partes.pop() : ""; }
  function enviarUploads(arquivos) {
    arquivos = Array.prototype.slice.call(arquivos || []);
    var statusId = "#nova-importacao-upload-status";
    var validos = arquivos.filter(function (arquivo) { return EXTENSOES_UPLOAD.indexOf(extensao(arquivo.name)) !== -1 && arquivo.size > 0 && (extensao(arquivo.name) === "zip" || arquivo.size <= MAX_UPLOAD); });
    if (!validos.length) { $(statusId).text("Selecione arquivos PDF, CSV, XLSX, DOCX, TXT ou HTML de até " + LIMITE_UPLOAD_MB + " MB — ZIP não tem limite de tamanho.").addClass("error"); return; }
    var enviados = [], indice = 0;
    function proximo() {
      if (indice >= validos.length) { $(statusId).text(enviados.length + " arquivo(s) enviado(s) e selecionado(s).").removeClass("error").addClass("success"); carregarFonte(enviados); return; }
      var arquivo = validos[indice], dados = new FormData();
      dados.append("arquivos", arquivo, arquivo.name); if (delegaciaAtual()) dados.append("delegacia", delegaciaAtual());
      $(statusId).text("Enviando " + (indice + 1) + "/" + validos.length + ": " + arquivo.name + "…").removeClass("error success");
      // o .zip vai pela rota sem limite (como o .ufdr); os demais, pela rota comum
      var urlEnvio = extensao(arquivo.name) === "zip" ? "/api/documentos/upload-zip" : "/api/documentos/upload";
      $.ajax({ url: urlEnvio, method: "POST", data: dados, processData: false, contentType: false }).done(function (resposta) { (resposta.arquivos || []).forEach(function (item) { enviados.push(item.nome); }); indice++; proximo(); }).fail(function (xhr) { $(statusId).text((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível enviar " + arquivo.name + ".").addClass("error"); });
    }
    proximo();
  }

  function vincularEventos() {
    $("#nova-importacao-fechar").on("click", fechar);
    $modal.on("click", function (evento) { if (evento.target === this) fechar(); });
    $(".nova-importacao-tipos").on("click", "[data-tipo]", function () { ativarTipo($(this).attr("data-tipo")); });
    $(".nova-fonte-opcoes").on("click", "[data-fonte]", function () { ativarFonte($(this).attr("data-fonte")); });
    $("#nova-importacao-inquerito").on("input", function () { if (!$(this).prop("readonly")) $(this).val(formatarEpol($(this).val())); numeroValido(false); atualizarEtapas(); });
    $("#nova-importacao-arquivo").on("change", function () { if (fonteAtual !== "iped" || !$(this).val()) return; $("#nova-importacao-conta-campo").removeClass("hidden"); $.ajax({ url: "/api/investigacao/iped-detectar-conta", method: "POST", contentType: "application/json", data: JSON.stringify({ arquivo: $(this).val(), delegacia: delegaciaAtual() }) }).done(function (resposta) { $("#nova-importacao-conta").val(resposta.conta || resposta.email || ""); }); });
    $("#nova-importacao-upload").on("change", function () { enviarUploads(this.files); this.value = ""; });
    $(".nova-importacao-drop").on("dragover dragenter", function (evento) { evento.preventDefault(); $(this).addClass("arrastando"); }).on("dragleave", function () { $(this).removeClass("arrastando"); }).on("drop", function (evento) {
      evento.preventDefault();
      $(this).removeClass("arrastando");
      var arquivos = evento.originalEvent && evento.originalEvent.dataTransfer ? evento.originalEvent.dataTransfer.files : [];
      enviarUploads(arquivos);
    });
    $("#nova-importacao-enviar").on("click", function () { status(""); enviarInquerito(); });
  }

  window.abrirNovaImportacaoOrbit = abrir;
  window.abrirModalImportar = abrir;
  $(document).on("click", "#nova-importacao-btn, #nova-fonte-btn, #novo-inquerito-btn, #nova-extracao-btn, .orbit-nova-importacao", function (evento) { evento.preventDefault(); abrir(this); });
  $(document).on("keydown", function (evento) { if (evento.key === "Escape" && $modal && !$modal.hasClass("hidden")) fechar(); });
  $(function () { var params = new URLSearchParams(location.search); if (params.get("nova_importacao") === "1") abrir({}); });
})();
