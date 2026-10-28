(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function formatarTamanho(bytes) { return AnalyserUtils.formatarTamanho(bytes); }

  var LIMITE = 100;
  var TP_ORIGEM = "arquivo";   // código da origem em REL_TB03_MARCACOES
  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  // Lazy loading com IntersectionObserver para otimizar carregamento de thumbnails
  var lazyLoadObserver = null;
  if (window.IntersectionObserver) {
    lazyLoadObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var $img = $(entry.target);
          var src = $img.data("src");
          if (src) {
            $img.attr("src", src).removeData("src");
            lazyLoadObserver.unobserve(entry.target);
          }
        }
      });
    }, { rootMargin: "50px" });
  }

  var $total = $("#total-registros");
  var $corpo = $("#dados-corpo");
  var $vazio = $("#dados-vazio");
  var $paginacao = $("#paginacao");
  var $pagInfo = $("#pag-info");
  var $pagAnterior = $("#pag-anterior");
  var $pagProximo = $("#pag-proximo");

  var $visaoLista = $("#visao-lista");
  var $visaoGaleria = $("#visao-galeria");
  var $galeriaGrid = $("#galeria-grid");
  var $galeriaVazio = $("#galeria-vazio");

  var $busca = $("#filtro-busca");
  var $categoria = $("#filtro-categoria");
  var $status = $("#filtro-status");
  var $tamanho = $("#filtro-tamanho");
  var $periodo = $("#filtro-periodo");
  var $ocrFiltro = $("#filtro-ocr");
  var $classifIaFiltro = $("#filtro-classif-ia");
  var $classeIa = $("#filtro-classe-ia");
  var $ordenar = $("#filtro-ordenar");
  var $dataDe = $("#filtro-data-de");
  var $dataAte = $("#filtro-data-ate");
  var $disponivel = $("#filtro-disponivel");
  var $marcados = $("#filtro-marcados");

  var $modalMidia = $("#modal-midia");
  var $midiaTitulo = $("#midia-titulo");
  var $midiaCorpo = $("#midia-corpo");

  var $visoes = $(".arq-visao");
  var $pastasNav = $("#pastas-navegacao");
  var $pastasTrilha = $("#pastas-trilha");
  var $pastasLista = $("#pastas-lista");
  var $pastasVazio = $("#pastas-vazio");

  if (!idInquerito) {
    $vazio.text("Parâmetros inválidos na URL.").show();
    return;
  }

  // visao: "lista" = tabela plana com todos os arquivos; "pastas" = navegação pelo filesystem do
  // aparelho (pasta = caminho da pasta aberta, "" = raiz)
  var estado = { pagina: 1, total: 0, visao: "lista", pasta: "" };
  // itens da página carregada — é sobre eles que o ‹ › do modal de mídia navega
  var itensPagina = [];
  var idDestacar = EstadoUrl.ler(["destacar"]).destacar;   // deep-link ?destacar= da evidência
  var procurandoDestacado = false;

  // ---- estado na URL ----
  // Filtros, visão, pasta e página persistem na URL (EstadoUrl.salvar usa replaceState, não mexe
  // no histórico nem recarrega): voltar de um detalhe devolve a lista no mesmo ponto e a URL pode
  // ser compartilhada entre investigadores. Chaves curtas para a URL não virar um tratado.
  var CHAVES_URL = ["busca", "categoria", "status", "tamanho", "periodo", "ocr", "classif",
    "classe", "ordenar", "de", "ate", "disponivel", "marcados", "pagina", "visao", "pasta"];

  // "disponivel" só entra na URL quando DESMARCADO ("nao"): marcado é o default, e gravar um
  // default faria toda URL carregar chave redundante
  function salvarEstadoUrl() {
    EstadoUrl.salvar({
      busca: $busca.val().trim(),
      categoria: $categoria.val(),
      status: $status.val(),
      tamanho: $tamanho.val(),
      periodo: $periodo.val(),
      ocr: $ocrFiltro.val(),
      classif: $classifIaFiltro.val(),
      classe: $classeIa.val(),
      ordenar: $ordenar.val() && $ordenar.val() !== "categoria" ? $ordenar.val() : "",
      de: $dataDe.val(),
      ate: $dataAte.val(),
      disponivel: $disponivel.is(":checked") ? "" : "nao",
      marcados: $marcados.is(":checked") ? "true" : "",
      pagina: estado.pagina > 1 ? estado.pagina : "",
      visao: estado.visao,
      pasta: estado.pasta,   // a pasta vale em qualquer visão — a URL tem que poder reabri-la
    });
  }

  function restaurarEstadoUrl() {
    var u = EstadoUrl.ler(CHAVES_URL);
    if (u.busca !== undefined) $busca.val(u.busca);
    if (u.categoria) $categoria.val(u.categoria);
    if (u.status) $status.val(u.status);
    if (u.tamanho) $tamanho.val(u.tamanho);
    if (u.periodo) $periodo.val(u.periodo);
    if (u.ocr) $ocrFiltro.val(u.ocr);
    if (u.classif) $classifIaFiltro.val(u.classif);
    if (u.classe) classeSalva = u.classe;   // as opções ainda não existem; aplicada no load
    sincronizarFiltroIa();
    if (u.ordenar) $ordenar.val(u.ordenar);
    if (u.de) $dataDe.val(u.de);
    if (u.ate) $dataAte.val(u.ate);
    if (u.disponivel === "nao") $disponivel.prop("checked", false);
    if (u.marcados === "true") $marcados.prop("checked", true);
    estado.pagina = Math.max(1, parseInt(u.pagina, 10) || 1);
    if (u.visao === "pastas" || u.visao === "galeria") estado.visao = u.visao;
    if (u.pasta !== undefined && u.pasta !== "") estado.pasta = normalizarPasta(u.pasta);
  }

  // ---- filtro "Classif. IA": situação + classe ----
  // O select de classes vem de /api/categorias-imagem (o vocabulário que a própria IA usa,
  // administrável na tela de Administração). Escolher uma classe deixa o "situação" sem
  // sentido — o back-end filtra o termo exato na mesma chave, então ele é desabilitado.
  function sincronizarFiltroIa() {
    var temClasse = !!$classeIa.val();
    $classifIaFiltro.prop("disabled", temClasse)
      .toggleClass("arq-select-inativo", temClasse);
    if (temClasse) $classifIaFiltro.val("");
  }

  var classeSalva = null;   // ?classe= da URL, aplicada quando o vocabulário chegar
  var categoriasIa = [];    // vocabulário de imagens — alimenta o filtro e o editor do modal

  $classeIa.on("change", function () { sincronizarFiltroIa(); recarregarDoInicio(); });
  $classifIaFiltro.on("change", recarregarDoInicio);

  if (idInquerito) {
    $.getJSON("/api/categorias-imagem").done(function (cats) {
      var ativos = (cats || []).filter(function (c) { return c.in_ativo; });
      categoriasIa = ativos;
      if (!ativos.length) { $classeIa.prop("disabled", true); return; }
      ativos.forEach(function (c) {
        $("<option>").attr("value", c.termo)
          .text(c.rotulo_exibicao || c.termo).appendTo($classeIa);
      });
      // a URL pode ter chegado com uma classe salva (restaurarEstadoUrl guardou em classeSalva)
      if (classeSalva) $classeIa.val(classeSalva);
      // termo salvo na URL que deixou de existir (categoria desativada): volta a "Todas"
      if (!$classeIa.val()) $classeIa.prop("selectedIndex", 0);
      sincronizarFiltroIa();
    }).fail(function () {
      $classeIa.prop("disabled", true);   // sem vocabulário, não se oferece o filtro
    });
  }

  // ---- modal de mídia (imagem / vídeo / áudio) ----
  // monta uma linha "rótulo: valor" no painel de metadados (só quando há valor)
  function linhaMeta($dl, rotulo, valor) {
    if (valor === null || valor === undefined || valor === "") return;
    $("<div>").addClass("arq-meta-linha")
      .append($("<dt>").text(rotulo))
      .append($("<dd>").append(valor))
      .appendTo($dl);
  }

  // navegador.clipboard só existe em contexto seguro (https ou localhost); em instalação servida
  // por http em IP da rede interna não está disponível, daí o fallback (mesmo padrão de usuarios.js)
  function copiarTexto(texto, okMsg) {
    function ok() { Toast.sucesso(okMsg); }
    function falhou() { Toast.erro("Não foi possível copiar — selecione e copie à mão."); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(ok, falhou);
      return;
    }
    var campo = document.createElement("textarea");
    campo.value = texto;
    campo.style.position = "fixed";
    campo.style.opacity = "0";
    document.body.appendChild(campo);
    campo.select();
    try {
      if (document.execCommand("copy")) ok(); else falhou();
    } catch (e) {
      falhou();
    }
    document.body.removeChild(campo);
  }

  // valor + botão de copiar para os hashes no painel de metadados do modal
  function hashComCopiar(hash) {
    if (!hash) return null;
    return $("<span>").addClass("arq-hash")
      .append($("<code>").text(hash))
      .append($("<button>").attr("type", "button").addClass("arq-acao arq-hash-copiar")
        .text("Copiar").on("click", function () { copiarTexto(hash, "Hash copiado."); }));
  }

  /* Revisão humana dentro do modal: classificação e texto (OCR). O mesmo espírito da correção
   * de transcrição no modal do WhatsApp — editar com a mídia na frente, sem diálogo por cima,
   * porque é OLHANDO a imagem que se sabe o que está escrito / qual classe cabe. Grava pelas
   * rotas PUT por id (escrita=True no inquérito) e sincroniza a célula da lista/galeria atrás
   * sem recarregar a página inteira. */
  function blocoEdicaoMidia(rotulo) {
    // reusa os estilos do bloco de transcrição (whatsapp.css) — o modal de arquivos já é "wa"
    var $wrap = $("<div>").addClass("wa-midia-transcricao").appendTo($midiaCorpo);
    var $cab = $("<div>").addClass("wa-midia-transcricao-cabecalho").appendTo($wrap);
    $("<div>").addClass("wa-midia-transcricao-rotulo").text(rotulo).appendTo($cab);
    return $wrap;
  }

  function montarEditorClassificacao(r) {
    if (!categoriasIa.length) return;   // sem vocabulário na tela (GET falhou): não se oferece edição
    var $wrap = blocoEdicaoMidia("Classificação (IA)");
    var $linha = $("<div>").addClass("arq-edit-linha").appendTo($wrap);
    var $sel = $("<select>").addClass("arq-edit-select").appendTo($linha);
    var $salvar = $("<button>").attr("type", "button").addClass("btn btn-primary")
      .text("Salvar").prop("disabled", true).appendTo($linha);
    $("<option>").attr("value", "").text("— sem classificação —").appendTo($sel);
    categoriasIa.forEach(function (c) {
      $("<option>").attr("value", c.termo).text(c.rotulo_exibicao || c.termo).appendTo($sel);
    });
    var atual = r.classificacao_ia || "";
    if (atual && !categoriasIa.some(function (c) { return c.termo === atual; })) {
      // a classe salva saiu do vocabulário (desativada/renomeada): aparece como desatualizada
      // em vez de sumir — o valor real continua gravado até alguém escolher outro
      $("<option>").attr("value", atual).text(atual + " (desatualizada)").appendTo($sel);
    }
    $sel.val(atual);
    $sel.on("change", function () { $salvar.prop("disabled", false); });
    $salvar.on("click", function () {
      $salvar.prop("disabled", true);
      $.ajax({
        url: "/api/investigacao/arquivos/" + r.id_arquivo + "/classificacao",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ classificacao: $sel.val() || "" })
      }).done(function (resp) {
        r.classificacao_ia = resp.classificacao || null;
        r.confianca_ia = resp.confianca;
        sincronizarItemNaTela(r);
        Toast.sucesso("Classificação salva.");
      }).fail(function (xhr) {
        $salvar.prop("disabled", false);
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar a classificação." });
      });
    });
  }

  function montarEditorOcr(r, textoInicial) {
    var texto = textoInicial || "";
    var $wrap = blocoEdicaoMidia(r.no_categoria === "Imagem" ? "Texto na imagem (OCR)" : "Texto (OCR)");
    var $btnCorrigir = $("<button>").attr("type", "button").addClass("wa-midia-transcricao-corrigir")
      .text("✎ corrigir").appendTo($wrap.find(".wa-midia-transcricao-cabecalho"));
    var $texto = $("<div>").addClass("wa-midia-transcricao-texto")
      .toggleClass("wa-midia-transcricao-vazia", !texto)
      .text(texto || "(sem texto)")
      .appendTo($wrap);
    var $editor = $("<div>").addClass("wa-midia-transcricao-editor hidden").appendTo($wrap);
    var $campo = $("<textarea>").addClass("dialogo-campo").attr({
      maxlength: 20000,
      placeholder: "Digite o que está escrito na imagem. Vazio = esta imagem não tem texto."
    }).appendTo($editor);
    var $acoes = $("<div>").addClass("wa-midia-transcricao-acoes").appendTo($editor);
    var $salvar = $("<button>").attr("type", "button").addClass("btn btn-primary").text("Salvar").appendTo($acoes);
    var $cancelar = $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Cancelar").appendTo($acoes);

    var modoEdicao = function (ligado) {
      $editor.toggleClass("hidden", !ligado);
      $texto.toggleClass("hidden", ligado);
      $btnCorrigir.toggleClass("hidden", ligado);
    };
    $btnCorrigir.on("click", function () {
      $campo.val(texto);
      modoEdicao(true);
      $campo.trigger("focus");
    });
    $cancelar.on("click", function () { modoEdicao(false); });
    $campo.on("keydown", function (e) { if (e.key === "Escape") { e.stopPropagation(); modoEdicao(false); } });
    $salvar.on("click", function () {
      var novoTexto = $.trim($campo.val());
      $salvar.prop("disabled", true);
      $.ajax({
        url: "/api/investigacao/arquivos/" + r.id_arquivo + "/ocr",
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ texto: novoTexto })
      }).done(function (resp) {
        texto = resp.texto || "";
        $texto.text(texto || "(sem texto)").toggleClass("wa-midia-transcricao-vazia", !texto);
        // a linha atrás mostra o trecho (os mesmos 400 caracteres que a listagem devolve)
        r.tx_ocr = texto ? texto.slice(0, 400) : null;
        r.ocr_feito = true;
        sincronizarItemNaTela(r);
        modoEdicao(false);
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o texto." });
      }).always(function () {
        $salvar.prop("disabled", false);
      });
    });
  }

  // depois de salvar no modal, a célula da lista (ou o badge da miniatura) atrás tem de acompanhar
  function sincronizarItemNaTela(r) {
    var $tr = $corpo.find('tr[data-id="' + r.id_arquivo + '"]');
    if ($tr.length) {
      $tr.children().eq(3).empty().append(renderClassifIa(r));
      $tr.children().eq(4).empty().append(renderOcr(r));
    }
    var $thumb = $galeriaGrid.find('.arq-thumb[data-id="' + r.id_arquivo + '"]');
    if ($thumb.length) porBadgesNaThumb($thumb, r);
  }

  /* Navegação ‹ › dentro do modal: percorre as MÍDIAS da página atual (a mesma lista que está
   * na tabela/galeria atrás), por botões ou pelas setas do teclado — ver 30 prints de um
   * comprovante não pode significar fechar e reabrir o modal 30 vezes. A URL acompanha o item
   * exibido (?destacar=), então a foto aberta é compartilhável por link. */
  var navegaveisMidia = [];
  var modalIdx = -1;

  function midiasDaPagina() {
    return itensPagina.filter(function (r) {
      return r.disponivel && (r.no_categoria === "Imagem" || r.no_categoria === "Vídeo"
        || r.no_categoria === "Áudio");
    });
  }

  var $contMiContador = $("<span>").addClass("arq-midia-contador");
  var $contMiPrev = $("<button>").attr({ type: "button", "aria-label": "Mídia anterior", title: "Anterior (←)" })
    .addClass("arq-midia-nav").text("‹").on("click", function () { irNaMidia(-1); });
  var $contMiProx = $("<button>").attr({ type: "button", "aria-label": "Próxima mídia", title: "Próxima (→)" })
    .addClass("arq-midia-nav").text("›").on("click", function () { irNaMidia(1); });
  $modalMidia.find(".modal-header").first().find(".modal-close").first()
    .before($contMiContador, $contMiPrev, $contMiProx);

  function atualizarNavMidia() {
    var navegavel = navegaveisMidia.length > 1;
    $contMiPrev.toggleClass("hidden", !navegavel);
    $contMiProx.toggleClass("hidden", !navegavel);
    $contMiContador.toggleClass("hidden", !navegavel)
      .text(navegavel ? (modalIdx + 1) + " de " + navegaveisMidia.length : "");
  }

  function irNaMidia(delta) {
    if (navegaveisMidia.length < 2 || modalIdx < 0) return;
    abrirModalMidia(navegaveisMidia[(modalIdx + delta + navegaveisMidia.length) % navegaveisMidia.length]);
  }

  function abrirModalMidia(r) {
    navegaveisMidia = midiasDaPagina();
    modalIdx = -1;
    navegaveisMidia.forEach(function (m, i) {
      if (String(m.id_arquivo) === String(r.id_arquivo)) modalIdx = i;
    });
    var cat = r.no_categoria;
    var urlConteudo = "/api/investigacao/arquivos/" + r.id_arquivo + "/conteudo";
    // imagem abre a prévia JPEG (converte HEIC do iPhone); vídeo/áudio tocam o binário original
    // — e o binário original é o que a mídia direta serve (a prévia continua sendo do central:
    // quem decodifica HEIC e desenha o thumbnail é o Pillow daqui). Formato que o navegador decodifica
    // sozinho (jpg/png/webp/...) pode vir direto do nó: o central só existe na frente para os que
    // ele precisa converter (heic/tiff) — no link fino isso é a diferença entre segundos e um 503.
    var imagemNativa = /\.(jpe?g|png|gif|webp|bmp|avif)$/i.test(r.no_arquivo || "");
    var url = cat === "Imagem"
      ? (imagemNativa && window.MidiaDireta
          ? MidiaDireta.url(r.id_arquivo, urlConteudo, false)
          : urlConteudo + "?preview=true")
      : (window.MidiaDireta ? MidiaDireta.url(r.id_arquivo, urlConteudo, false) : urlConteudo);

    $midiaTitulo.text(r.no_arquivo || "Arquivo");
    $midiaCorpo.empty();
    var $carregando = $("<div>").addClass("wa-midia-carregando")
      .html('<span class="spinner"></span> <span>carregando…</span>').appendTo($midiaCorpo);
    var tag = cat === "Imagem" ? "<img>" : cat === "Vídeo" ? "<video>" : "<audio>";
    var $el = $(tag)
      .on(cat === "Imagem" ? "load" : "canplay loadeddata", function () { $carregando.remove(); })
      .on("error", function () {
        // errou vindo do nó? o central ainda tem o arquivo inteiro — tenta antes de reclamar
        var central = cat === "Imagem" ? urlConteudo + "?preview=true" : urlConteudo;
        var c = window.MidiaDireta ? MidiaDireta.fallback(this, central) : central;
        if (this.getAttribute("src") !== c) { this.setAttribute("src", c); return; }
        $carregando.html("⚠ não foi possível carregar este arquivo.");
      });
    if (cat === "Imagem") {
      // clique alterna "ajustar na tela ↔ tamanho real" (o natural só existe depois do load)
      $el.addClass("wa-midia-imagem").attr("alt", r.no_arquivo || "imagem")
        .on("click", function () { $el.toggleClass("arq-zoom"); });
    } else if (cat === "Vídeo") {
      $el.addClass("wa-midia-video").attr({ controls: true, autoplay: true });
    } else {
      $el.addClass("wa-midia-audio").attr({ controls: true, autoplay: true });
    }
    $el.attr("src", url).appendTo($midiaCorpo);
    if (cat === "Áudio") VelocidadeMidia.controle($el).appendTo($midiaCorpo);

    // painel de metadados: dados do arquivo (já temos da linha) + EXIF da imagem (buscado)
    var $meta = $("<dl>").addClass("arq-meta").appendTo($midiaCorpo);
    linhaMeta($meta, "Caminho", r.tx_caminho);
    linhaMeta($meta, "Tamanho", formatarTamanho(r.qt_bytes));
    linhaMeta($meta, "Modificado", formatarDataHora(r.dt_modificacao));
    linhaMeta($meta, "Status", r.cd_status_arquivo);
    // hash: identificador forense do binário — o mesmo arquivo em dois aparelhos tem o mesmo hash
    linhaMeta($meta, "Hash MD5", hashComCopiar(r.co_hash_md5));
    linhaMeta($meta, "Hash SHA-256", hashComCopiar(r.co_hash_sha256));
    var $acoesMeta = $("<div>").addClass("arq-acoes arq-meta-acoes").appendTo($meta);
    var urlBaixar = urlConteudo + "?download=true";
    if (window.MidiaDireta) urlBaixar = MidiaDireta.url(r.id_arquivo, urlBaixar, true);
    $("<a>").addClass("arq-acao")
      .attr({ href: urlBaixar }).text("Baixar original").appendTo($acoesMeta);
    if (r.chat_id_mensagem) {
      $("<a>").addClass("arq-acao")
        .attr({ href: linkConversa(r), title: "Abrir a mensagem em que este arquivo aparece" })
        .text(rotuloConversa(r)).appendTo($acoesMeta);
    }
    if (window.EntidadesUI) {
      EntidadesUI.botao({
        idInquerito: idInquerito,
        tpOrigem: TP_ORIGEM,
        idOrigem: r.id_arquivo,
        rotulo: r.no_arquivo || r.tx_caminho || "Arquivo",
        somenteRelatorio: true,
      }).appendTo($acoesMeta);
    }

    if (cat === "Imagem" || cat === "Vídeo") {
      // revisão da classificação com a imagem/vídeo na tela (o vocabulário é o da própria IA)
      montarEditorClassificacao(r);
      // o texto completo e o EXIF vêm juntos do banco — o editor de OCR usa o OCR integral,
      // nunca o trecho de 400 caracteres que acompanha a listagem
      $.getJSON("/api/investigacao/arquivos/" + r.id_arquivo + "/metadados").done(function (ex) {
        ex = ex || {};
        if (ex.largura && ex.altura) linhaMeta($meta, "Dimensões", ex.largura + " × " + ex.altura + " px");
        linhaMeta($meta, "Câmera", ex.camera);
        linhaMeta($meta, "Lente", ex.lente);
        linhaMeta($meta, "Data da captura", ex.data_captura);
        linhaMeta($meta, "Software", ex.software);
        if (ex.gps_lat != null && ex.gps_lon != null) {
          var coord = ex.gps_lat + ", " + ex.gps_lon;
          var $mapa = $("<a>").attr({
            href: "https://www.google.com/maps?q=" + ex.gps_lat + "," + ex.gps_lon,
            target: "_blank", rel: "noopener"
          }).text(coord + "  (ver no mapa)");
          linhaMeta($meta, "Localização (GPS)", $mapa);
        }
        montarEditorOcr(r, ex.ocr || "");
      });
    }

    $modalMidia.removeClass("hidden");
    atualizarNavMidia();
    // a URL passa a apontar para ESTA mídia: o link colado num pedido reabre a foto exata
    EstadoUrl.salvar({ destacar: String(r.id_arquivo) });
  }

  function fecharModalMidia() {
    $modalMidia.addClass("hidden");
    $midiaCorpo.empty(); // descarrega imagem / para o áudio ou vídeo ao fechar
    // a foto que estava aberta deixa de ser o estado da tela
    EstadoUrl.salvar({ destacar: null });
    modalIdx = -1;
  }

  $("#midia-fechar").on("click", fecharModalMidia);
  $modalMidia.on("click", function (e) { if (e.target === this) fecharModalMidia(); });
  $(document).on("keydown", function (e) { if (e.key === "Escape") fecharModalMidia(); });

  // ---- linha da tabela ----
  // deep-link para a mensagem que carregou este binário (whatsapp.js abre a conversa e destaca a
  // mensagem com ?msg=); a extração NÃO vai na URL para o filtro não esconder a conversa alvo
  function linkConversa(r) {
    var url = MapaTelas.paginaConversa(r.chat_fonte, r.chat_app) +
      "?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + encodeURIComponent(r.chat_fonte) +
      "&msg=" + encodeURIComponent(r.chat_id_mensagem);
    if (r.chat_fonte === "msg" && r.chat_app) url += "&app=" + encodeURIComponent(r.chat_app);
    return url;
  }

  function rotuloConversa(r) {
    if (r.chat_fonte === "wpp") return "Ver no WhatsApp";
    if (r.chat_fonte === "sms") return "Ver no SMS";
    return "Ver em " + (r.chat_app || "conversa");
  }

  function renderClassifIa(r) {
    // classificação manual vale para qualquer categoria (o editor do modal oferece a vídeo
    // também); para o resto, "N/A" continua sendo a resposta honesta quando não há valor
    if (r.classificacao_ia && r.confianca_ia != null) {
      var pct = (r.confianca_ia * 100).toFixed(0);
      return $("<span>").addClass("arq-ia-badge")
        .append($("<span>").addClass("arq-ia-tag").text(r.classificacao_ia))
        .append(" ")
        .append($("<span>").addClass("arq-ia-conf").text(pct + "%"));
    }
    if (r.no_categoria !== "Imagem") {
      return $("<span>").addClass("arq-ia-na").text("N/A");
    }
    return $("<span>").addClass("arq-ia-na").text("—");
  }

  // coluna "Texto (OCR)": trecho do que o OCR reconheceu na imagem. Clicar abre o arquivo (o texto
  // inteiro fica no modal, junto do próprio print). Distingue "sem texto" de "ainda não processada".
  function renderOcr(r) {
    if (r.tx_ocr) {
      return $("<div>").addClass("arq-ocr-trecho")
        .attr("title", "Clique para abrir e ver o texto completo")
        .text(r.tx_ocr)
        .on("click", function () { if (r.disponivel) abrirModalMidia(r); });
    }
    if (r.no_categoria !== "Imagem") {
      return $("<span>").addClass("arq-ia-na").text("N/A");
    }
    return $("<span>").addClass("arq-ia-na").text(r.ocr_feito ? "sem texto" : "—");
  }

  // caminho normalizado como o servidor faz (barras '/' simples, sem barra no início/fim)
  function normalizarPasta(caminho) {
    if (!caminho) return "";
    return String(caminho).replace(/\\/g, "/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
  }

  // pasta que contém o arquivo (o caminho gravado no UFDR inclui o nome dele no fim)
  function pastaDoCaminho(caminho) {
    var norm = normalizarPasta(caminho);
    var corte = norm.lastIndexOf("/");
    return corte < 0 ? "" : norm.slice(0, corte);
  }

  // abre o arquivo: mídia no modal, o resto baixa direto (mesma regra da coluna Ações)
  function abrirArquivo(r) {
    if (!r.disponivel) return;
    var cat = r.no_categoria;
    if (cat === "Imagem" || cat === "Vídeo" || cat === "Áudio") {
      abrirModalMidia(r);
    } else {
      var urlDown = "/api/investigacao/arquivos/" + r.id_arquivo + "/conteudo?download=true";
      if (window.MidiaDireta) urlDown = MidiaDireta.url(r.id_arquivo, urlDown, true);
      window.location = urlDown;
    }
  }

  function renderLinha(r) {
    var $tr = $("<tr>").attr("data-id", r.id_arquivo);
    var $nome = $("<td>").addClass("arq-nome");
    if (r.disponivel) {
      $("<a>").attr("href", "#").text(r.no_arquivo || "—").on("click", function (e) {
        e.preventDefault();
        abrirArquivo(r);
      }).appendTo($nome);
    } else {
      $nome.text(r.no_arquivo || "—");
    }
    // triagem do PERITO dentro do IPED (bookmarks.iped): o item foi marcado ou selecionado
    if (r.marcadores_perito || r.selecionado_perito) {
      $("<span>").addClass("arq-tag arq-tag-marcador")
        .attr("title", "Marcação do perito no IPED: " + (r.marcadores_perito || "selecionado"))
        .text(r.selecionado_perito ? "✳ perito" : "✳ " + r.marcadores_perito.split(",")[0])
        .appendTo($nome);
    }
    $nome.appendTo($tr);
    // o caminho é clicável: leva à visão de pastas já aberta na pasta do arquivo
    var $caminho = $("<span>").addClass("arq-caminho").attr("title", r.tx_caminho || "");
    if (r.tx_caminho) {
      $("<a>").attr("href", "#").text(r.tx_caminho).on("click", function (e) {
        e.preventDefault();
        trocarVisao("pastas", pastaDoCaminho(r.tx_caminho));
      }).appendTo($caminho);
    } else {
      $caminho.text("—");
    }
    $("<td>").append($caminho).appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-tag").text(r.no_categoria || "—")).appendTo($tr);
    $("<td>").append(renderClassifIa(r)).appendTo($tr);
    $("<td>").append(renderOcr(r)).appendTo($tr);
    $("<td>").text(formatarTamanho(r.qt_bytes)).appendTo($tr);
    $("<td>").text(formatarDataHora(r.dt_modificacao)).appendTo($tr);
    var status = r.cd_status_arquivo || "—";
    var apagado = /delet|apag/i.test(status);
    $("<td>").append($("<span>").toggleClass("arq-status-apagado", apagado).text(status)).appendTo($tr);

    var $acoes = $("<td>").addClass("arq-acoes");
    if (!r.disponivel) {
      $("<span>").addClass("arq-indisponivel").text("sem binário").appendTo($acoes);
    } else {
      var cat = r.no_categoria;
      if (cat === "Imagem" || cat === "Vídeo" || cat === "Áudio") {
        // mídia abre no modal; o resto (documento, PDF, compactado, outro) só baixa
        $("<button>").addClass("arq-acao").text("Abrir").on("click", function () {
          abrirModalMidia(r);
        }).appendTo($acoes);
      } else {
        var urlDown = "/api/investigacao/arquivos/" + r.id_arquivo + "/conteudo?download=true";
        if (window.MidiaDireta) urlDown = MidiaDireta.url(r.id_arquivo, urlDown, true);
        $("<a>").addClass("arq-acao").attr({ href: urlDown }).text("Baixar").appendTo($acoes);
      }
    }
    // o mesmo binário circulou numa conversa: leva ao ponto exato dela (a mensagem fica destacada)
    if (r.chat_id_mensagem) {
      $("<a>").addClass("arq-acao")
        .attr({ href: linkConversa(r), title: "Abrir a mensagem em que este arquivo aparece" })
        .text(rotuloConversa(r)).appendTo($acoes);
    }
    // documento/compactado não abre modal (só baixa) — o hash fica a um clique aqui; em mídia ele
    // já aparece no painel de metadados do modal
    if (cat !== "Imagem" && cat !== "Vídeo" && cat !== "Áudio" && (r.co_hash_md5 || r.co_hash_sha256)) {
      var hashLinha = r.co_hash_sha256 || r.co_hash_md5;
      $("<button>").attr("type", "button").addClass("arq-acao")
        .attr("title", "Copiar " + (r.co_hash_sha256 ? "SHA-256" : "MD5") + ": " + hashLinha)
        .text("Copiar hash")
        .on("click", function () { copiarTexto(hashLinha, "Hash copiado."); })
        .appendTo($acoes);
    }
    if (window.EntidadesUI) {
      EntidadesUI.botao({
        idInquerito: idInquerito,
        tpOrigem: TP_ORIGEM,
        idOrigem: r.id_arquivo,
        rotulo: r.no_arquivo || r.tx_caminho || "Arquivo",
        somenteRelatorio: true,
      }).appendTo($acoes);
    }
    $acoes.appendTo($tr);
    return $tr;
  }

  /* Miniatura da grade: um <button> de verdade — Tab percorre a galeria e Enter abre, como o
   * investigador espera. Os badges trazem para a miniatura a triagem que já vem na resposta da
   * lista (Classif. IA, texto OCR, tamanho), e o vídeo ganha o ▶ — clicar abre o modal, o
   * documento baixa (abrirArquivo decide por categoria). */
  // badges de triagem sobre a miniatura — separados da montagem para poderem ser recolocados
  // depois de uma edição no modal (sincronizarItemNaTela) sem reconstruir a grade
  function porBadgesNaThumb($thumb, r) {
    $thumb.find(".arq-thumb-badge").remove();
    if (r.classificacao_ia) {
      var pct = r.confianca_ia != null ? " " + (r.confianca_ia * 100).toFixed(0) + "%" : "";
      $("<span>").addClass("arq-thumb-badge arq-thumb-badge-ia")
        .attr("title", "Classificação: " + r.classificacao_ia + pct)
        .text(r.classificacao_ia + pct).appendTo($thumb);
    }
    if (r.tx_ocr) {
      $("<span>").addClass("arq-thumb-badge arq-thumb-badge-ocr")
        .attr("title", "Tem texto extraído — o conteúdo dele vale para a busca")
        .text("OCR ✓").appendTo($thumb);
    }
  }

  function renderThumb(r) {
    var cat = r.no_categoria;
    var $thumb = $("<button>").attr({
      type: "button",
      "data-id": r.id_arquivo,
      "aria-label": (r.no_arquivo || cat || "arquivo") + " (" + formatarTamanho(r.qt_bytes) + ")"
    }).addClass("arq-thumb").toggleClass("arq-thumb-nao", !r.disponivel);

    // miniaturas de imagens (e de vídeos com o quadro que o IPED já extraiu)
    if ((cat === "Imagem" || cat === "Vídeo") && r.disponivel) {
      var urlPreview = "/api/investigacao/arquivos/" + r.id_arquivo + "/conteudo?preview=true&thumb=true";
      var $placeholder = $("<div>").addClass("arq-thumb-placeholder")
        .html('<span class="spinner" style="width:20px;height:20px;"></span>')
        .appendTo($thumb);
      var $img = $("<img>").addClass("arq-thumb-img")
        .attr("alt", r.no_arquivo || (cat === "Vídeo" ? "vídeo" : "imagem"))
        .attr("loading", "lazy")
        .on("load", function () {
          $placeholder.remove();
        })
        .on("error", function () {
          // vídeo sem quadro pronto (ou binário ausente): volta para o ícone de mídia
          $placeholder.html(cat === "Vídeo" ? "🎥" : "⚠");
        })
        .appendTo($thumb);

      // usa data-src para lazy loading com IntersectionObserver (fallback para navegadores antigos)
      if (lazyLoadObserver) {
        $img.attr("data-src", urlPreview);
        lazyLoadObserver.observe($img[0]);
      } else {
        // fallback: carrega normalmente se IntersectionObserver não disponível
        $img.attr("src", urlPreview);
      }
      if (cat === "Vídeo") {
        $thumb.append($("<span>").addClass("arq-thumb-play").attr("aria-hidden", "true").text("▶"));
      }
    } else {
      // placeholder para não-imagens ou sem binário disponível
      var icone = cat === "Vídeo" ? "🎥" : cat === "Áudio" ? "🔊" : cat === "Documento" ? "📄" : "📎";
      if (!r.disponivel) icone = "🚫";
      $("<div>").addClass("arq-thumb-placeholder").text(icone).appendTo($thumb);
    }

    // triagem sobre a miniatura: só o que ajuda a decidir se vale abrir
    porBadgesNaThumb($thumb, r);
    $("<span>").addClass("arq-thumb-tam").text(formatarTamanho(r.qt_bytes)).appendTo($thumb);

    // nome do arquivo no hover
    $("<div>").addClass("arq-thumb-info").text(r.no_arquivo || "arquivo").appendTo($thumb);

    // mídia abre no modal, documento baixa; sem binário o botão não faz nada (arq-thumb-nao)
    $thumb.on("click", function () { abrirArquivo(r); });

    return $thumb;
  }

  function totalPaginas() {
    return Math.max(1, Math.ceil(estado.total / LIMITE));
  }

  // filtros atuais (sem paginação) — reutilizado no carregamento e na exportação
  function montarFiltros() {
    var params = {};
    if (idExtracao) params.extracao = idExtracao;
    // a pasta aberta vale em QUALQUER visão: na tabela, na galeria e na navegação — mudar de
    // visão dentro do DCIM/Camera não pode devolver o aparelho inteiro
    if (estado.pasta) params.pasta = estado.pasta;
    var cat = $categoria.val();
    if (cat) params.categoria = cat;
    var busca = $busca.val().trim();
    if (busca) params.busca = busca;
    if ($disponivel.is(":checked")) params.disponivel = "true";
    if ($marcados.is(":checked")) params.marcados = "true";
    if ($status.val()) params.status = $status.val();
    if ($tamanho.val()) params.tamanho_min = $tamanho.val();
    if ($periodo.val()) params.periodo = $periodo.val();
    if ($ocrFiltro.val()) params.ocr = $ocrFiltro.val();
    // classe da IA é o refinamento do "Classif. IA": escolher "documento" já implica
    // "classificadas" — o back-end aceita o termo exato na mesma chave
    var classe = $classeIa.val();
    if (classe) params.classificacao_ia = classe;
    else if ($classifIaFiltro.val()) params.classificacao_ia = $classifIaFiltro.val();
    if ($ordenar.val() && $ordenar.val() !== "categoria") params.ordenar = $ordenar.val();
    if ($dataDe.val()) params.data_de = $dataDe.val();
    if ($dataAte.val()) params.data_ate = $dataAte.val();
    return params;
  }

  function destacarLinha() {
    if (!idDestacar) return;
    var $linha = $corpo.find('[data-id="' + idDestacar + '"]');
    if (!$linha.length) return;
    $linha.addClass("tp-destacada");
    $linha[0].scrollIntoView({ block: "center" });
    idDestacar = null;
    EstadoUrl.salvar({ destacar: null });
  }

  /* O arquivo pode não estar na primeira página (a lista ordena por categoria/data) — varre as
   * páginas em segundo plano até achar a linha, então renderiza a página certa. */
  function localizarDestacado() {
    if (!idDestacar || procurandoDestacado || estado.visao !== "lista") return;
    procurandoDestacado = true;
    var paginaAlvo = 1;
    var teto = 50;

    (function proxima() {
      var params = montarFiltros();
      params.pagina = paginaAlvo;
      params.limite = LIMITE;
      $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/arquivos", params)
        .done(function (resp) {
          var achou = (resp.itens || []).some(function (r) {
            return String(r.id_arquivo) === String(idDestacar);
          });
          if (achou) {
            estado.pagina = paginaAlvo;
            procurandoDestacado = false;
            salvarEstadoUrl();
            carregar();
          } else if (paginaAlvo < teto && paginaAlvo * LIMITE < (resp.total || 0)) {
            paginaAlvo += 1;
            proxima();
          } else {
            procurandoDestacado = false;
          }
        })
        .fail(function () { procurandoDestacado = false; });
    })();
  }

  function carregar() {
    var params = montarFiltros();
    params.pagina = estado.pagina;
    params.limite = LIMITE;

    // limpa observers de lazy loading antes de limpar o grid
    if (lazyLoadObserver) {
      $galeriaGrid.find("img").each(function () {
        lazyLoadObserver.unobserve(this);
      });
    }

    $corpo.empty();
    $vazio.hide();
    $galeriaGrid.empty();
    $galeriaVazio.hide();
    // a consulta pode demorar (busca em OCR sobre milhares de linhas): skeleton enquanto isso
    if (window.Carregando) {
      if (estado.visao === "galeria") Carregando.emBloco($galeriaGrid.css("min-height", "180px"));
      else Carregando.emTabela($corpo, 9);
    }

    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/arquivos", params).done(function (resp) {
      if (window.Carregando) Carregando.limpar($galeriaGrid.css("min-height", ""));
      estado.total = resp.total;
      itensPagina = resp.itens || [];
      $total.text(resp.total.toLocaleString("pt-BR") + " arquivos"
        + (estado.visao === "pastas" ? " nesta pasta" : ""));
      if (!resp.itens.length) {
        $corpo.empty();   // tira o skeleton antes da mensagem
        var msg = estado.pasta
          ? "Nenhum arquivo solto nesta pasta."
          : "Nenhum arquivo encontrado.";
        var $alvo = estado.visao === "galeria" ? $galeriaVazio : $vazio;
        $alvo.empty().append(document.createTextNode(msg));
        // o binário de arquivos do Google Takeout nunca é copiado (fica só catalogado) — com o
        // filtro padrão marcado a lista sempre vem vazia pra essas contas; oferece desmarcar
        // direto em vez de deixar a pessoa achar que não tem nada
        if ($disponivel.is(":checked")) {
          $alvo.append(" ");
          $("<button>").attr("type", "button").addClass("btn-link")
            .text('Mostrar também os sem binário disponível')
            .on("click", function () { $disponivel.prop("checked", false).trigger("change"); })
            .appendTo($alvo);
        }
        $alvo.show();
        $paginacao.hide();
        return;
      }

      if (estado.visao === "galeria") {
        resp.itens.forEach(function (r) { $galeriaGrid.append(renderThumb(r)); });
      } else {
        $corpo.empty();   // troca o skeleton pelas linhas
        resp.itens.forEach(function (r) { $corpo.append(renderLinha(r)); });
        destacarLinha();
        if (idDestacar) localizarDestacado();
      }

      // mídia direta (docs/no_midia_direta.md): a página inteira pergunta de uma vez se pode
      // apontar para o nó; quem chegou atrasado para a resposta usa o central, sem drama
      if (window.MidiaDireta) {
        var idsMidia = resp.itens
          .filter(function (r) { return r.disponivel; })
          .map(function (r) { return r.id_arquivo; });
        MidiaDireta.preparar(idsMidia, false);
        MidiaDireta.preparar(idsMidia, true);
      }

      var tp = totalPaginas();
      $pagInfo.text("Página " + estado.pagina + " de " + tp.toLocaleString("pt-BR"));
      $pagAnterior.prop("disabled", estado.pagina <= 1);
      $pagProximo.prop("disabled", estado.pagina >= tp);
      $paginacao.css("display", tp > 1 ? "flex" : "none");
    }).fail(function () {
      if (window.Carregando) Carregando.limpar($galeriaGrid.css("min-height", ""));
      $corpo.empty();
      // o mesmo texto sozinho deixa a pessoa parada; o botão re-tenta na hora
      var $alvo = estado.visao === "galeria" ? $galeriaVazio : $vazio;
      $alvo.empty().text(estado.visao === "galeria"
        ? "Não foi possível carregar as imagens."
        : "Não foi possível carregar os arquivos.");
      $("<button>").attr("type", "button").addClass("btn-link").text("Tentar novamente")
        .on("click", carregar).appendTo($alvo);
      $alvo.show();
      $paginacao.hide();
      if (window.Toast) Toast.erro("Falha ao carregar a listagem de arquivos.");
    });
  }

  function recarregarDoInicio() {
    estado.pagina = 1;
    salvarEstadoUrl();
    atualizarOrdenacao();
    atualizarLimpar();
    carregar();
    if (estado.visao === "pastas") carregarPastas();
  }

  // ---- visualização por navegação em pastas ----
  var ICONE_PASTA =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    + 'stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';

  // trilha clicável: Raiz do aparelho › DCIM › Camera (o último segmento é a pasta aberta)
  function renderTrilha() {
    $pastasTrilha.empty();
    var segmentos = estado.pasta ? estado.pasta.split("/") : [];

    function item(rotulo, destino, atual) {
      if (atual) {
        $("<span>").addClass("arq-trilha-atual").text(rotulo).appendTo($pastasTrilha);
      } else {
        $("<button>").attr("type", "button").text(rotulo).on("click", function () {
          abrirPasta(destino);
        }).appendTo($pastasTrilha);
      }
    }

    item("Raiz do aparelho", "", segmentos.length === 0);
    segmentos.forEach(function (seg, i) {
      $("<span>").addClass("arq-trilha-sep").text("›").appendTo($pastasTrilha);
      item(seg, segmentos.slice(0, i + 1).join("/"), i === segmentos.length - 1);
    });

    // fora da visão de pastas a trilha é o lembrete de que a lista está recortada — e a saída
    if (estado.visao !== "pastas" && estado.pasta) {
      $("<button>").attr("type", "button").addClass("arq-trilha-sair")
        .attr("title", "Mostrar o aparelho inteiro nesta visão")
        .text("✕ sair da pasta")
        .on("click", function () { trocarVisao(estado.visao, ""); })
        .appendTo($pastasTrilha);
    }
  }

  function renderPasta(p) {
    var caminho = estado.pasta ? estado.pasta + "/" + p.no_pasta : p.no_pasta;
    var $botao = $("<button>").attr({ type: "button", title: caminho }).addClass("arq-pasta")
      .on("click", function () { abrirPasta(caminho); });
    $botao.append(ICONE_PASTA);
    $("<span>").addClass("arq-pasta-nome")
      .append($("<div>").text(p.no_pasta))
      .append($("<div>").addClass("arq-pasta-info").text(
        p.qt_arquivos.toLocaleString("pt-BR") + (p.qt_arquivos === 1 ? " arquivo" : " arquivos")
        + " · " + formatarTamanho(p.qt_bytes)
      ))
      .appendTo($botao);
    return $botao;
  }

  function carregarPastas() {
    var params = montarFiltros();
    $pastasLista.empty();
    $pastasVazio.hide();
    renderTrilha();

    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/arquivos/pastas", params)
      .done(function (resp) {
        var pastas = (resp && resp.pastas) || [];
        if (!pastas.length) {
          $pastasVazio.show();
          return;
        }
        pastas.forEach(function (p) { $pastasLista.append(renderPasta(p)); });
      })
      .fail(function () {
        $pastasVazio.text("Não foi possível carregar as pastas.").show();
      });
  }

  function abrirPasta(caminho) {
    estado.pasta = normalizarPasta(caminho);
    estado.pagina = 1;
    salvarEstadoUrl();
    if (estado.visao === "pastas") carregarPastas();
    else renderTrilha();
    carregar();
    window.scrollTo(0, 0);
  }

  function trocarVisao(visao, pasta, manterPagina) {
    if (visao === "pastas" || visao === "galeria") {
      estado.visao = visao;
    } else {
      estado.visao = "lista";
    }
    estado.pasta = normalizarPasta(pasta);
    if (!manterPagina) estado.pagina = 1;
    $visoes.each(function () {
      $(this).toggleClass("ativa", $(this).data("visao") === estado.visao);
    });

    var emPastas = estado.visao === "pastas";
    var emGaleria = estado.visao === "galeria";

    $pastasNav.toggle(emPastas);
    // a tabela serve às DUAS visões de lista: a plana e a "Arquivos nesta pasta" sob o grid de
    // pastas — só a galeria dispensa tabela (esconder daqui matava a listagem dentro da pasta)
    $visaoLista.toggle(!emGaleria);
    $visaoGaleria.toggle(emGaleria);
    atualizarBotaoMidiaTemp();   // "Extrair temporariamente" só faz sentido na galeria
    // a pasta aberta recorta qualquer visão — a trilha avisa (e sai) em todas
    $pastasTrilha.toggle(emPastas || !!estado.pasta);
    renderTrilha();

    salvarEstadoUrl();
    if (emPastas) carregarPastas();
    carregar();
    if (!manterPagina) window.scrollTo(0, 0);
  }

  $visoes.on("click", function () {
    var visao = $(this).data("visao");
    // trocar de visão NÃO abandona a pasta: quem está no DCIM/Camera quer ver aquele lugar
    // em grid, não o aparelho inteiro
    if (visao !== estado.visao) trocarVisao(visao, estado.pasta);
  });

  // ---- ordenação pelo cabeçalho da tabela ----
  // O <select> "Ordenar" continua sendo a fonte da verdade (montarFiltros lê dele); o clique no
  // cabeçalho só escolhe a chave certa e dispara o change. Cada coluna declara a direção
  // preferida (data-ordenar) e o par asc/desc — Nome alterna nome/nome_desc, Tamanho
  // maiores/menores, Modificado recentes/antigos.
  function atualizarOrdenacao() {
    var chave = $ordenar.val();
    if (chave === "categoria") chave = "";
    $(".tp-ordenar").each(function () {
      var $btn = $(this);
      var asc = $btn.data("ordenar-asc");
      var desc = $btn.data("ordenar-desc");
      var eAtivo = chave === asc || chave === desc;
      // aria-sort no <th> é o que o leitor de tela anuncia; a seta é só o reforço visual
      $btn.closest("th").attr("aria-sort", eAtivo ? (chave === asc ? "ascending" : "descending") : "none");
      $btn.toggleClass("tp-ordenar-ativo", eAtivo);
      $btn.find(".tp-seta").text(eAtivo ? (chave === asc ? "▲" : "▼") : "↕");
    });
  }

  $(".tp-ordenar").on("click", function () {
    var $btn = $(this);
    var asc = $btn.data("ordenar-asc");
    var desc = $btn.data("ordenar-desc");
    var padrao = $btn.data("ordenar") || asc;
    var atual = $ordenar.val();
    var proxima;
    if (atual === asc) proxima = desc;
    else if (atual === desc) proxima = asc;
    else proxima = padrao;
    $ordenar.val(proxima).trigger("change");
  });

  // ---- painel de filtros: limpar e atalhos de período ----
  var $limpar = $("#limpar-filtros");
  var $atalhos = $("#atalhos-periodo");

  function filtrosAtivos() {
    var qt = 0;
    if ($busca.val().trim()) qt += 1;
    if ($categoria.val()) qt += 1;
    if ($status.val()) qt += 1;
    if ($tamanho.val()) qt += 1;
    if ($periodo.val()) qt += 1;
    if ($ocrFiltro.val()) qt += 1;
    if ($classifIaFiltro.val() || $classeIa.val()) qt += 1;
    if ($ordenar.val() && $ordenar.val() !== "categoria") qt += 1;
    if ($dataDe.val() || $dataAte.val()) qt += 1;
    if (!$disponivel.is(":checked")) qt += 1;
    if ($marcados.is(":checked")) qt += 1;
    return qt;
  }

  function atualizarLimpar() {
    var qt = filtrosAtivos();
    $limpar.toggle(qt > 0).text(qt > 1 ? "Limpar filtros (" + qt + ")" : "Limpar filtros");
  }

  $limpar.on("click", function () {
    $busca.val("");
    $categoria.val("");
    $status.val("");
    $tamanho.val("");
    $periodo.val("");
    $ocrFiltro.val("");
    $classifIaFiltro.val("");
    $classeIa.val("");
    sincronizarFiltroIa();
    $ordenar.val("categoria");
    $dataDe.val("");
    $dataAte.val("");
    $disponivel.prop("checked", true);
    $marcados.prop("checked", false);
    recarregarDoInicio();
  });

  // AAAA-MM-DD do dia local — toISOString() daria o dia em UTC, e no Brasil (UTC-3) isso
  // transforma 1º de março em 28 de fevereiro no campo de data
  function dataIso(d) {
    var mes = d.getMonth() + 1;
    var dia = d.getDate();
    return d.getFullYear() + "-" + (mes < 10 ? "0" : "") + mes + "-" + (dia < 10 ? "0" : "") + dia;
  }

  // Atalhos de período: os N dias que terminam no registro mais recente do aparelho — não em
  // hoje (a extração costuma ser de meses atrás, e "últimos 30 dias" contados de hoje devolve
  // lista vazia justo em quem tem dado antigo). O fim da faixa vem do novo /arquivos/periodo.
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/arquivos/periodo"
    + (idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : ""))
    .done(function (p) {
      if (!p || !p.max) return;
      [["7 dias", 7], ["30 dias", 30], ["12 meses", 365]].forEach(function (par) {
        var rotulo = par[0], dias = par[1];
        $("<button>").addClass("filtro-chip").attr("type", "button").text(rotulo)
          .attr("title", "Os " + rotulo.toLowerCase() + " até o registro mais recente ("
            + p.max.split("-").reverse().join("/") + ")")
          .on("click", function () {
            var inicio = new Date(p.max + "T00:00:00");   // local: a data do input não tem fuso
            inicio.setDate(inicio.getDate() - dias + 1);
            $dataDe.val(dataIso(inicio));
            $dataAte.val(p.max);
            recarregarDoInicio();
          })
          .appendTo($atalhos);
      });
    });

  // ---- eventos de filtro/paginação ----
  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(recarregarDoInicio, 300);
  });
  $categoria.on("change", recarregarDoInicio);
  $status.on("change", recarregarDoInicio);
  $tamanho.on("change", recarregarDoInicio);
  $periodo.on("change", recarregarDoInicio);
  $ocrFiltro.on("change", recarregarDoInicio);
  // ($classifIaFiltro e $classeIa já têm handler próprio junto do filtro)
  $ordenar.on("change", recarregarDoInicio);
  $dataDe.on("change", recarregarDoInicio);
  $dataAte.on("change", recarregarDoInicio);
  $disponivel.on("change", recarregarDoInicio);
  $marcados.on("change", recarregarDoInicio);
  $pagAnterior.on("click", function () {
    if (estado.pagina > 1) { estado.pagina -= 1; salvarEstadoUrl(); carregar(); window.scrollTo(0, 0); }
  });
  $pagProximo.on("click", function () {
    if (estado.pagina < totalPaginas()) { estado.pagina += 1; salvarEstadoUrl(); carregar(); window.scrollTo(0, 0); }
  });

  // ←/→ navegam entre páginas quando o foco não está em campo de texto (mesmo padrão do
  // tabela_paginada.js); com algum modal aberto as setas não paginam a lista atrás dele
  $(document).on("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var alvo = e.target;
    if (alvo && (alvo.tagName === "INPUT" || alvo.tagName === "SELECT" || alvo.tagName === "TEXTAREA"
      || alvo.isContentEditable)) return;
    // modal de mídia na frente: as setas folheiam a pasta de mídia, não a lista atrás dele
    if (!$modalMidia.hasClass("hidden")) {
      if (navegaveisMidia.length > 1) {
        e.preventDefault();
        irNaMidia(e.key === "ArrowLeft" ? -1 : 1);
      }
      return;
    }
    if ($(".modal-overlay:not(.hidden)").length) return;
    var $botao = e.key === "ArrowLeft" ? $pagAnterior : $pagProximo;
    if ($botao.prop("disabled")) return;
    e.preventDefault();
    $botao.trigger("click");
  });

  // exportação Excel — busca o conjunto completo já filtrado (todas as páginas)
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "arquivos",
      dados: function () {
        var params = montarFiltros();
        params.pagina = 1;
        params.limite = 1000000;
        return $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/arquivos", params)
          .then(function (resp) {
            var itens = (resp && resp.itens) || [];
            return {
              colunas: ["Nome", "Caminho no aparelho", "Categoria", "Classificação IA", "Texto (OCR)", "Tamanho", "Modificado", "Status"],
              linhas: itens.map(function (r) {
                var classif = "";
                if (r.no_categoria === "Imagem" && r.classificacao_ia) {
                  classif = r.classificacao_ia + (r.confianca_ia != null ? " " + (r.confianca_ia * 100).toFixed(0) + "%" : "");
                } else if (r.no_categoria !== "Imagem") {
                  classif = "N/A";
                }
                return [
                  r.no_arquivo || "",
                  r.tx_caminho || "",
                  r.no_categoria || "",
                  classif,
                  r.tx_ocr || "",
                  formatarTamanho(r.qt_bytes),
                  formatarDataHora(r.dt_modificacao),
                  r.cd_status_arquivo || "",
                ];
              }),
            };
          });
      },
    });
  }

  // ---- Classificação por IA ----
  var $btnClassificar = $("#btn-classificar-ia");
  var $btnCancelarClassif = $("#btn-cancelar-classificacao");
  var $classificarStatus = $("#classificar-status");
  var $modalClassif = $("#modal-classificar-ia");
  var $classifMensagem = $("#classificar-mensagem");
  var $classifProgressoTexto = $("#classificar-progresso-texto");
  var $classifProgressoPct = $("#classificar-progresso-pct");
  var $classifBarra = $("#classificar-barra");
  var $classifAcoes = $("#classificar-acoes");
  var $classifConfirmar = $("#classificar-confirmar");
  var $classifFecharBtn = $("#classificar-cancelar");
  var $classifStale = $("#classificar-stale");
  var $classifReset = $("#classificar-reset");

  var classifTimer = null;
  var classifRodando = false;

  function atualizarBotoesToolbar(rodando) {
    classifRodando = rodando;
    $btnClassificar.toggleClass("hidden", rodando);
    $btnCancelarClassif.toggleClass("hidden", !rodando);
  }

  function resetarEstadoModal() {
    $classifAcoes.css("display", "flex");
    $classifConfirmar.prop("disabled", false).show();
    $classifFecharBtn.prop("disabled", false).text("Cancelar");
    $classifStale.hide();
    $btnClassificar.prop("disabled", false);
  }

  function fecharModalClassif() {
    $modalClassif.addClass("hidden");
    // a classificação roda no servidor: fechar o modal não pode parar o polling, senão a
    // porcentagem na toolbar congela no último valor lido
    if (!classifRodando && classifTimer) { clearInterval(classifTimer); classifTimer = null; }
    resetarEstadoModal();
  }

  function finalizarClassificacao(p) {
    if (classifTimer) { clearInterval(classifTimer); classifTimer = null; }
    atualizarBotoesToolbar(false);
    $classifStale.hide();

    var msg;
    if (p.cancelado) {
      msg = "Classificação cancelada. " + p.processados + " imagens processadas.";
    } else if (p.total > 0) {
      msg = "Classificação concluída. " + p.processados + " imagens (" + p.erros + " erros).";
      $classifProgressoPct.text("100%");
      $classifBarra.css("width", "100%");
      $classifProgressoTexto.text("Concluído");
    } else {
      msg = "Nenhuma imagem pendente para classificar.";
      $classifProgressoTexto.text("—");
      $classifProgressoPct.text("—");
      $classifBarra.css("width", "0%");
    }
    $classifMensagem.text(msg || "Classificação finalizada.");
    // com o modal fechado a toolbar é o único aviso de que terminou — não deixa a última
    // porcentagem lida no lugar
    $classificarStatus.text(p.cancelado ? "Classificação cancelada." : "Classificação concluída.");
    $classifAcoes.hide();
    if (p.total > 0) setTimeout(fecharModalClassif, 2000);
    if (p.total > 0 && !p.cancelado) carregar();
  }

  // O progresso agora vem da linha do job (id guardado ao disparar), e não de um endpoint
  // global do servidor: com vários analistas, aquele devolvia o job de quem estivesse rodando.
  var classifIdJob = null;

  // Nomes das colunas da tabela de jobs, traduzidos para o que a tela já usava.
  function normalizarJob(j) {
    return {
      rodando: j.rodando,
      cancelado: j.cancelado,
      total: j.qt_total,
      processados: j.qt_processados,
      erros: j.qt_erros,
      com_texto: (j.tx_metricas && j.tx_metricas.com_texto) || 0,
      na_frente: j.qt_na_frente || 0,
      aguardando: j.cd_status === "aguardando_vaga" || j.cd_status === "pendente",
      erro: j.tx_erro
    };
  }

  function pollProgresso() {
    if (!classifIdJob) return;
    $.getJSON("/api/investigacao/jobs/" + classifIdJob)
      .done(function (bruto) {
        var p = normalizarJob(bruto);
        if (!p.rodando) {
          finalizarClassificacao(p);
          return;
        }
        atualizarBotoesToolbar(true);
        if (p.aguardando) {
          // antes disto a barra ficava parada em 0% sem explicação enquanto o job esperava
          // vaga de CPU; agora a tela diz que é fila, e quantos estão na frente
          var fila = p.na_frente > 0 ? " (" + p.na_frente + " na frente)" : "";
          $classifMensagem.text("Na fila, aguardando vaga de processamento" + fila + "…");
          $classifProgressoTexto.text("Na fila" + fila);
          $classificarStatus.text("Classificação na fila" + fila);
          return;
        }
        var pct = p.total > 0 ? Math.round((p.processados / p.total) * 100) : 0;
        $classifMensagem.text("Classificando imagens…");
        $classifProgressoTexto.text(p.processados + " de " + p.total + " imagens" + (p.erros ? " (" + p.erros + " erros)" : ""));
        $classifProgressoPct.text(pct + "%");
        $classifBarra.css("width", pct + "%");
        $classificarStatus.text(p.processados + "/" + p.total + " (" + pct + "%)");
      })
      .fail(function () {
        // job apagado, sessão expirada ou aba antiga contra API nova: para de bater em vez de
        // martelar o servidor a cada 2 s
        if (classifTimer) { clearInterval(classifTimer); classifTimer = null; }
      });
  }

  function iniciarPolling() {
    classifTimer = setInterval(pollProgresso, 2000);
    pollProgresso();
  }

  // Botão "Cancelar classificação" na toolbar
  $btnCancelarClassif.on("click", function () {
    if (!classifIdJob) return;
    $btnCancelarClassif.prop("disabled", true).text("Cancelando…");
    $classificarStatus.text("Cancelando…");
    $.ajax({
      url: "/api/investigacao/jobs/" + classifIdJob + "/cancelar",
      method: "POST"
    }).fail(function (xhr) {
      $btnCancelarClassif.prop("disabled", false).text("Cancelar classificação");
      $classificarStatus.text("Erro ao cancelar.");
      if (xhr.status === 409) {
        // travou — mostra o reset no modal
        $classifMensagem.text("A classificação parece ter travado. Use o botão abaixo para resetar.");
        $classifStale.show();
        $classifAcoes.hide();
        $modalClassif.removeClass("hidden");
      }
    });
  });

  // Botão "Classificar imagens por IA" na toolbar → abre modal de confirmação
  $btnClassificar.on("click", function () {
    resetarEstadoModal();
    $classifMensagem.text("Verificando imagens pendentes…");
    $classifProgressoTexto.text("Aguardando…");
    $classifProgressoPct.text("0%");
    $classifBarra.css("width", "0%");
    $classifStale.hide();
    $modalClassif.removeClass("hidden");
  });

  // Confirmar no modal → dispara a classificação
  $classifConfirmar.on("click", function () {
    $classifConfirmar.prop("disabled", true).hide();
    $classifFecharBtn.prop("disabled", false).text("Fechar");
    $classifStale.hide();
    $classifMensagem.text("Iniciando classificação…");
    $classifProgressoTexto.text("Iniciando…");

    $.ajax({
      url: "/api/arquivos/classificar-imagens",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: parseInt(idInquerito, 10),
        id_extracao: idExtracao ? parseInt(idExtracao, 10) : null,
        batch_size: 16
      })
    })
      .done(function (r) {
        classifIdJob = r.id_job;
        atualizarBotoesToolbar(true);
        $classificarStatus.text("Iniciando…");
        iniciarPolling();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar classificação.";
        $classifMensagem.text(msg);
        $classifProgressoTexto.text("Erro");
        $classifAcoes.hide();
        if (xhr.status === 409) {
          $classifStale.show();
        } else {
          $btnClassificar.prop("disabled", false);
        }
      });
  });

  // Botão "Fechar" / "Cancelar" dentro do modal
  $classifFecharBtn.on("click", function () {
    fecharModalClassif();
  });

  // Botão de reset no modal
  $classifReset.on("click", function () {
    if (!classifIdJob) return;
    $.ajax({ url: "/api/investigacao/jobs/" + classifIdJob + "/liberar", method: "POST" })
      .done(function () {
        $modalClassif.addClass("hidden");
        resetarEstadoModal();
        atualizarBotoesToolbar(false);
        $classificarStatus.text("");
        $classifMensagem.text("Estado resetado.");
      });
  });

  // Fechar modal pelo X ou overlay
  $("#classificar-fechar").on("click", fecharModalClassif);
  $modalClassif.on("click", function (e) { if (e.target === this) fecharModalClassif(); });

  // a classificação roda no servidor, não na aba: se já havia uma em curso (outra aba, F5), a
  // toolbar volta a mostrar o andamento em vez de oferecer um botão que daria 409
  // ...mas só a DESTE inquérito: o endpoint antigo era global e mostrava o job de outra delegacia
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/jobs/classificacao_imagem")
    .done(function (j) {
      if (j && j.rodando) {
        classifIdJob = j.id_job;
        atualizarBotoesToolbar(true);
        iniciarPolling();
      }
    });

  // ---- OCR das imagens ----
  // Mesma mecânica da classificação por IA (uma varredura por vez no processo, com progresso,
  // cancelamento e reset), sobre um recurso diferente: /api/arquivos/ocr-imagens.
  var $btnOcr = $("#btn-ocr");
  var $btnCancelarOcr = $("#btn-cancelar-ocr");
  var $ocrStatus = $("#ocr-status");
  var $modalOcr = $("#modal-ocr");
  var $ocrMensagem = $("#ocr-mensagem");
  var $ocrProgressoTexto = $("#ocr-progresso-texto");
  var $ocrProgressoPct = $("#ocr-progresso-pct");
  var $ocrBarra = $("#ocr-barra");
  var $ocrAcoes = $("#ocr-acoes");
  var $ocrConfirmar = $("#ocr-confirmar");
  var $ocrFecharBtn = $("#ocr-cancelar");
  var $ocrStale = $("#ocr-stale");
  var $ocrReset = $("#ocr-reset");

  var TEXTO_OCR_INICIAL = $ocrMensagem.text();
  var ocrTimer = null;
  var ocrRodando = false;

  function atualizarBotoesOcr(rodando) {
    ocrRodando = rodando;
    $btnOcr.toggleClass("hidden", rodando);
    $btnCancelarOcr.toggleClass("hidden", !rodando);
  }

  function resetarEstadoModalOcr() {
    $ocrAcoes.css("display", "flex");
    $ocrConfirmar.prop("disabled", false).show();
    $ocrFecharBtn.prop("disabled", false).text("Cancelar");
    $ocrStale.hide();
    $btnOcr.prop("disabled", false);
  }

  function fecharModalOcr() {
    $modalOcr.addClass("hidden");
    // mesmo motivo da classificação: o OCR segue no servidor com o modal fechado
    if (!ocrRodando && ocrTimer) { clearInterval(ocrTimer); ocrTimer = null; }
    resetarEstadoModalOcr();
  }

  function finalizarOcr(p) {
    if (ocrTimer) { clearInterval(ocrTimer); ocrTimer = null; }
    atualizarBotoesOcr(false);
    $ocrStale.hide();

    var msg;
    if (p.cancelado) {
      msg = "OCR cancelado. " + p.processados + " imagens processadas ("
        + p.com_texto + " com texto).";
    } else if (p.total > 0) {
      msg = "OCR concluído. " + p.processados + " imagens, " + p.com_texto
        + " com texto reconhecido (" + p.erros + " erros).";
      $ocrProgressoPct.text("100%");
      $ocrBarra.css("width", "100%");
      $ocrProgressoTexto.text("Concluído");
    } else {
      msg = "Nenhuma imagem pendente de OCR.";
      $ocrProgressoTexto.text("—");
      $ocrProgressoPct.text("—");
      $ocrBarra.css("width", "0%");
    }
    $ocrMensagem.text(msg);
    $ocrAcoes.hide();
    $ocrStatus.text(p.cancelado ? "OCR cancelado." : "OCR concluído.");
    if (p.total > 0) setTimeout(fecharModalOcr, 2500);
    if (p.total > 0 && p.com_texto) carregar();
    // Atualiza o texto do botão após finalizar (vai de "Extrair" para "Refazer")
    if (p.total > 0 && !p.cancelado) atualizarTextoBotaoOcr();
  }

  var ocrIdJob = null;

  function pollProgressoOcr() {
    if (!ocrIdJob) return;
    $.getJSON("/api/investigacao/jobs/" + ocrIdJob)
      .done(function (bruto) {
        var p = normalizarJob(bruto);
        if (!p.rodando) {
          finalizarOcr(p);
          return;
        }
        atualizarBotoesOcr(true);
        if (p.aguardando) {
          var fila = p.na_frente > 0 ? " (" + p.na_frente + " na frente)" : "";
          $ocrMensagem.text("Na fila, aguardando vaga de processamento" + fila + "…");
          $ocrProgressoTexto.text("Na fila" + fila);
          $ocrStatus.text("OCR na fila" + fila);
          return;
        }
        var pct = p.total > 0 ? Math.round((p.processados / p.total) * 100) : 0;
        $ocrMensagem.text("Extraindo texto das imagens…");
        $ocrProgressoTexto.text(p.processados + " de " + p.total + " imagens · "
          + p.com_texto + " com texto" + (p.erros ? " (" + p.erros + " erros)" : ""));
        $ocrProgressoPct.text(pct + "%");
        $ocrBarra.css("width", pct + "%");
        $ocrStatus.text("OCR: " + p.processados + "/" + p.total + " (" + pct + "%)");
      })
      .fail(function () {
        if (ocrTimer) { clearInterval(ocrTimer); ocrTimer = null; }
      });
  }

  function iniciarPollingOcr() {
    ocrTimer = setInterval(pollProgressoOcr, 2000);
    pollProgressoOcr();
  }

  $btnOcr.on("click", function () {
    resetarEstadoModalOcr();
    $ocrMensagem.text(TEXTO_OCR_INICIAL);
    $ocrProgressoTexto.text("Aguardando…");
    $ocrProgressoPct.text("0%");
    $ocrBarra.css("width", "0%");
    $modalOcr.removeClass("hidden");
  });

  $ocrConfirmar.on("click", function () {
    $ocrConfirmar.prop("disabled", true).hide();
    $ocrFecharBtn.prop("disabled", false).text("Fechar");
    $ocrStale.hide();
    $ocrMensagem.text("Iniciando OCR…");
    $ocrProgressoTexto.text("Iniciando…");

    $.ajax({
      url: "/api/arquivos/ocr-imagens",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: parseInt(idInquerito, 10),
        id_extracao: idExtracao ? parseInt(idExtracao, 10) : null,
        lote: 20
      })
    })
      .done(function (r) {
        ocrIdJob = r.id_job;
        atualizarBotoesOcr(true);
        $ocrStatus.text("OCR: iniciando…");
        iniciarPollingOcr();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar o OCR.";
        $ocrMensagem.text(msg);
        $ocrProgressoTexto.text("Erro");
        $ocrAcoes.hide();
        if (xhr.status === 409) {
          $ocrStale.show();
        } else {
          $btnOcr.prop("disabled", false);
        }
      });
  });

  $btnCancelarOcr.on("click", function () {
    $btnCancelarOcr.prop("disabled", true).text("Cancelando…");
    $ocrStatus.text("Cancelando OCR…");
    $.ajax({ url: "/api/investigacao/jobs/" + ocrIdJob + "/cancelar", method: "POST" })
      .always(function () {
        $btnCancelarOcr.prop("disabled", false).text("Cancelar OCR");
      })
      .fail(function (xhr) {
        $ocrStatus.text("Erro ao cancelar o OCR.");
        if (xhr.status === 409) {
          // travou — mostra o reset no modal
          $ocrMensagem.text("O OCR parece ter travado. Use o botão abaixo para resetar.");
          $ocrStale.show();
          $ocrAcoes.hide();
          $modalOcr.removeClass("hidden");
        }
      });
  });

  $ocrReset.on("click", function () {
    if (!ocrIdJob) return;
    $.ajax({ url: "/api/investigacao/jobs/" + ocrIdJob + "/liberar", method: "POST" }).done(function () {
      fecharModalOcr();
      atualizarBotoesOcr(false);
      $ocrStatus.text("");
      $ocrMensagem.text(TEXTO_OCR_INICIAL);
    });
  });

  $ocrFecharBtn.on("click", fecharModalOcr);
  $("#ocr-fechar").on("click", fecharModalOcr);
  $modalOcr.on("click", function (e) { if (e.target === this) fecharModalOcr(); });

  // OCR roda no servidor, não na aba: se já havia uma varredura em curso (outra aba, F5), a
  // toolbar volta a mostrar o andamento em vez de oferecer um "Extrair texto" que daria 409.
  // Dois tipos de job: ocr_imagem (varredura desta tela) e ocr_imagem_selecao (recorte disparado
  // pela IA no chat) — ambos aparecem aqui, para o investigador acompanhar de onde vier.
  (function retomarOcr() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/jobs/ocr_imagem")
      .done(function (j) {
        if (j && j.rodando) {
          ocrIdJob = j.id_job;
          atualizarBotoesOcr(true);
          iniciarPollingOcr();
          return;
        }
        // nenhuma varredura desta tela rodando: pode ser um OCR pedido pela IA, ainda em curso
        $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/jobs/ocr_imagem_selecao")
          .done(function (js) {
            if (js && js.rodando) {
              ocrIdJob = js.id_job;
              atualizarBotoesOcr(true);
              iniciarPollingOcr();
            }
          });
      });
  })();

  // Atualiza o texto do botão de OCR dependendo do status da extração selecionada
  function atualizarTextoBotaoOcr() {
    var extracao = paramUrl("extracao");
    if (!extracao) {
      $btnOcr.html(
        '<svg class="icone-btn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="13" y2="14"/></svg>'
        + 'Extrair texto (OCR)'
      );
      return;
    }

    $.getJSON("/api/arquivos/ocr-extracao/" + extracao + "/status").done(function (status) {
      if (status.status === "completo") {
        $btnOcr.html(
          '<svg class="icone-btn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg>'
          + 'Refazer OCR'
        );
      } else {
        $btnOcr.html(
          '<svg class="icone-btn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="13" y2="14"/></svg>'
          + 'Extrair texto (OCR)'
        );
      }
    }).fail(function (xhr) {
      console.warn("Erro ao verificar status de OCR:", xhr.status, xhr.statusText);
      $btnOcr.html(
        '<svg class="icone-btn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="13" y2="14"/></svg>'
        + 'Extrair texto (OCR)'
      );
    });
  }

  // Atualiza o botão ao carregar
  atualizarTextoBotaoOcr();

  // Monitora mudanças na URL (quando user navega para outra extração)
  window.addEventListener("popstate", atualizarTextoBotaoOcr);
  // Também atualiza quando a página é recarregada com F5 ou navegação direta
  $(window).on("hashchange", atualizarTextoBotaoOcr);

  // ---- "Extrair temporariamente" (só na galeria) ----
  // Copia os vídeos e imagens da extração para uma pasta temporária do servidor (data/_midia_temp,
  // que expira sozinha em 4 h): vídeos sem quadro do IPED ganham miniatura e o modal toca direto
  // do disco, com seek. O trabalho pesado corre no job em background (funcoes/midia_temp.py) —
  // daqui só se dispara e acompanha. O original do .ufdr/caso IPED nunca é tocado.
  var $btnMidiaTemp = $("#btn-midia-temp");
  var $midiaTempStatus = $("#midia-temp-status");
  var midiaTempTimer = null;
  var midiaTempIdJob = null;

  function atualizarBotaoMidiaTemp() {
    // só se oferece na visão galeria e com uma extração aberta (a pasta de cópia é por extração).
    // toggleClass e não .toggle(): o .hidden da casa é display:none !important (style.css), que
    // o estilo inline do jQuery .show() não vence.
    $btnMidiaTemp.toggleClass("hidden", !(estado.visao === "galeria" && !!idExtracao));
  }

  function midiaTempRodando(rodando) {
    $btnMidiaTemp.prop("disabled", rodando);
    $btnMidiaTemp.find(".midia-temp-rotulo").text(rodando ? "Extraindo temporariamente…"
      : "Extrair temporariamente");
  }

  function pollingMidiaTemp() {
    if (!midiaTempIdJob) return;
    $.getJSON("/api/investigacao/jobs/" + midiaTempIdJob).done(function (bruto) {
      var p = normalizarJob(bruto);
      if (p.rodando) {
        var pct = p.total > 0 ? Math.round((p.processados / p.total) * 100) : 0;
        midiaTempRodando(true);
        $midiaTempStatus.text("Temporário: " + p.processados + "/" + p.total + " (" + pct + "%)");
        return;
      }
      clearInterval(midiaTempTimer); midiaTempTimer = null;
      midiaTempRodando(false);
      if (bruto.cd_status === "concluido") {
        var m = bruto.tx_metricas || {};
        $midiaTempStatus.text("Temporário pronto" + (m.thumbs ? ", " + m.thumbs + " miniaturas" : "")
          + " — vale por " + (m.expira_em_horas || 4) + " h");
        if (window.Toast) Toast.sucesso("Extração temporária concluída.");
        if (estado.visao === "galeria") carregar();   // recarrega as thumbs: vídeos agora têm quadro
      } else if (bruto.cd_status === "cancelado") {
        $midiaTempStatus.text("Extração temporária cancelada.");
      } else {
        $midiaTempStatus.text("Erro no temporário.");
        if (window.Toast) Toast.erro(p.erro || "Erro na extração temporária.");
      }
    }).fail(function () {
      // job sumiu ou sessão expirou: para de bater, como nos outros polls da tela
      if (midiaTempTimer) { clearInterval(midiaTempTimer); midiaTempTimer = null; }
      midiaTempRodando(false);
    });
  }

  function iniciarPollingMidiaTemp() {
    midiaTempTimer = setInterval(pollingMidiaTemp, 2000);
    pollingMidiaTemp();
  }

  $btnMidiaTemp.on("click", function () {
    $btnMidiaTemp.prop("disabled", true);
    $midiaTempStatus.text("Iniciando…");
    $.ajax({
      url: "/api/investigacao/extracoes/" + idExtracao + "/midia-temporaria",
      method: "POST"
    }).done(function (r) {
      midiaTempIdJob = r.id_job;
      iniciarPollingMidiaTemp();
    }).fail(function (xhr) {
      midiaTempRodando(false);
      $midiaTempStatus.text("");
      if (window.Toast) {
        Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a extração temporária.");
      }
    });
  });

  // A cópia roda no servidor, não na aba: se já havia job em curso (F5, outra aba), retoma o
  // acompanhamento em vez de oferecer um botão que daria 409.
  if (idExtracao) {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/jobs/midia_temporaria")
      .done(function (j) {
        if (j && j.rodando && String(j.id_extracao) === String(idExtracao)) {
          midiaTempIdJob = j.id_job;
          iniciarPollingMidiaTemp();
        }
      });
  }

  // marcações do inquérito: carregadas UMA vez (o cache do widget é aditivo) — as páginas
  // seguintes já leem o cache ao montar o botão 🗒️
  if (window.EntidadesUI) EntidadesUI.carregarMarcacoes(idInquerito, TP_ORIGEM);

  // A lista de arquivos do aparelho vira a primeira aba; envios para a nuvem e gravações
  // vêm do componente compartilhado, na mesma barra.
  if (window.UfdrDominio) {
    UfdrDominio.montar({
      seletor: "#abas-host",
      abas: [{ chave: "arquivos", rotulo: "Arquivos do aparelho", externo: "#painel-arquivos" }]
        .concat(UfdrDominio.abasDe(["envios-nuvem", "gravacoes"]))
    });
  }

  // ?busca= na URL abre a galeria já filtrada. Quem manda são as telas "Comparar celulares" e
  // "Comparar rostos", que acham o mesmo arquivo em dois aparelhos e precisam levar o investigador
  // direto a ele na galeria do aparelho certo (js/comparar_celulares.js: linkArquivos). Só vale
  // quando a URL NÃO traz ?visao=: se trouxer, é um estado salvo pelo próprio usuário, e a
  // visão dele manda.
  var buscaUrl = paramUrl("busca");
  var visaoSalvaUrl = paramUrl("visao");

  restaurarEstadoUrl();
  if (buscaUrl && !visaoSalvaUrl) {
    // deep-link de outra tela: galeria já filtrada pelo nome do arquivo
    $busca.val(buscaUrl);
    trocarVisao("galeria", "");
  } else if (estado.visao !== "lista") {
    // visão salva na URL (pastas/galeria): trocarVisao cuida do painel, e a página restaurada
    // acima vale porque manterPagina evita o reset
    trocarVisao(estado.visao, estado.pasta, true);
  } else {
    if (estado.pasta) { $pastasTrilha.show(); renderTrilha(); }   // lista já recortada na pasta
    carregar();
  }
  atualizarOrdenacao();
  atualizarLimpar();
})();
