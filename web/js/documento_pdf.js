/* Visualizador de PDF da tela do documento (web/documento.html) — o DocPdf.

   Existe porque o nginx manda X-Frame-Options: DENY (nginx/default.conf): embutir o viewer
   nativo do navegador num <iframe> está morto por contrato de segurança, e não se abre mão do
   header por uma tela. O pdf.js (web/vendor/pdfjs, build legado 3.11.174, mesma origem — a CSP
   script-src 'self' continua de pé) desenha o PDF em <canvas> dentro da própria página, e é o
   Worker dele que pede o worker-src da CSP.

   O arquivo inteiro vai para o navegador de uma vez (fetch do /original, que já serve inline e
   é a mesma rota do botão "Original"; o FileResponse do backend não fala HTTP Range, então
   carregamento progressivo do pdf.js não tem de onde vir). Por isso o teto de 300 páginas que a
   tela impõe antes de oferecer o modo PDF: além dele o download de umavez pesa demais para a
   aba — o texto continua na tela e o inteiro fica no botão "Original".

   O desenho das páginas é sob demanda: um IntersectionObserver renderiza o <canvas> quando a
   folha entra na janela da tela e o apaga (liberando a memória do bitmap) quando ela sai — 50
   páginas A4 desenhadas de uma vez custariam centenas de MB de canvas. A moldura (div com o
   aspect-ratio real da página) fica no lugar para o scroll não pular sob os pés do leitor.

   Quem decide quando o painel aparece, e como ele acompanha o texto, é js/documento.js: daqui
   o que sai é DocPdf.abrir() (carrega e monta), DocPdf.irParaPagina() (navegação) e o callback
   aoPagina (scroll de quem olha o PDF chegou noutra página). */
(function () {
  "use strict";

  var LIMITE_PAGINAS = 300; // acima disto a tela não oferece PDF: vai para o navegador inteiro
  var JANELA_RENDER = "400px"; // margem do observador: desenha um pouco antes de a folha aparecer
  var FONTE_DPR_MAX = 2; // retina desenha em 2x; além disso é memória sem ganho visível

  if (window.pdfjsLib) {
    // worker e fontes padrão são da MESMA origem (web/vendor/pdfjs) — a CSP não abre externa, e o
    // worker direto 'self' é o caminho; blob: na CSP só cobre o reserva do próprio pdf.js
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdfjs/pdf.worker.min.js";
    // PDF de órgão público costuma citar Helvetica/Times sem embutir: sem as fontes padrão o
    // pdf.js desenha a página com substituta e o carimbo sai torto — numa tela que serve para
    // conferir prova, a página tem de sair como o papel
    window.pdfjsLib.GlobalWorkerOptions.standardFontDataUrl = "vendor/pdfjs/standard_fonts/";
  }

  var pdfDoc = null;        // o documento carregado (pdf.js), nulo até o primeiro modo PDF
  var carregando = null;    // a promise de carregamento em curso (ou já resolvida)
  var metas = [];           // por página: {nu, w, h, renderizada} na ordem física do PDF
  var $painel = $("#doc-painel-pdf");
  var $lista = $("#doc-pdf-folhas");
  var $aviso;
  var observador = null;    // render/descarte de canvases
  var spy = null;           // qual página o leitor está olhando
  var paginaAtual = 0;
  var callbacks = {};       // {aoPagina, aoPronto, aoErro}
  var seq = 0;              // invalida um montar cancelado por outro

  function larguraAlvo() {
    // a folha ocupa a largura do painel (menos o padding), com o mesmo tecto das folhas de texto
    var $janela = $painel;
    var caixa = $janela[0] ? $janela[0].clientWidth : 0;
    var pad = parseFloat(getComputedStyle($painel[0]).paddingLeft || 0) * 2 || 32;
    return Math.max(320, Math.min(900 - pad, caixa - pad));
  }

  /* Desenha a página se ela não está desenhada nem a caminho. A largura é lida na hora do
   * desenho (não na fila): entre a fila e o paint o painel pode ter mudado de tamanho — janela
   * redimensionada, coluna do lado a lado que apareceu. */
  function desenharPagina(meta) {
    if (meta.renderizada || meta.tarefa) return;
    var $slot = $lista.find('.doc-pdf-folha[data-nu-pagina="' + meta.nu + '"]');
    var canvas = $slot.find("canvas")[0];
    if (!canvas) return;
    pdfDoc.getPage(meta.nu).then(function (pagina) {
      if (meta.renderizada || meta.tarefa) return;
      var escala = (larguraAlvo() / meta.w) * Math.min(FONTE_DPR_MAX, window.devicePixelRatio || 1);
      var viewport = pagina.getViewport({scale: escala});
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      $slot.find(".doc-pdf-quadro").css("aspect-ratio", viewport.width + " / " + viewport.height);
      meta.tarefa = pagina.render({canvasContext: canvas.getContext("2d"), viewport: viewport});
      meta.tarefa.promise.then(function () {
        meta.tarefa = null;
        // canvas.width > 0: se a folha saiu da janela no meio do desenho, descartarPagina
        // zerou o bitmap e este aqui não tem mais para onde ir — não marcar como desenhada
        if (canvas.width > 0) meta.renderizada = true;
      }, function () { meta.tarefa = null; }); // cancelado: a próxima entrada na janela re-renderiza
    });
  }

  function descartarPagina(meta) {
    if (meta.tarefa) { meta.tarefa.cancel(); meta.tarefa = null; }
    if (!meta.renderizada) return;
    var $slot = $lista.find('.doc-pdf-folha[data-nu-pagina="' + meta.nu + '"]');
    var canvas = $slot.find("canvas")[0];
    if (canvas) { canvas.width = 0; canvas.height = 0; } // libera o bitmap; a moldura fica
    meta.renderizada = false;
  }

  /* Redimensionar a janela muda a largura-alvo de tudo: os bitmaps desenhados viram retratos
   * tortos. Descarta os desenhados e religa o observador — o callback inicial dele re-desenha
   * exatamente as folhas que estão na tela, na largura nova; as de fora ficam baratas assim. */
  var redimTimer = null;
  $(window).on("resize", function () {
    clearTimeout(redimTimer);
    redimTimer = setTimeout(function () {
      if (!pdfDoc || !observador) return; // painel pausado: o retomar() já redesenha tudo
      metas.forEach(function (m) { if (m.renderizada || m.tarefa) descartarPagina(m); });
      observar();
    }, 250);
  });

  /* O spy elege a folha mais acima entre as que cruzam a faixa superior do painel — a mesma
   * régua do scroll-spy das folhas de texto (documento.js), para as duas colunas concordarem
   * sobre "que página estou lendo". */
  function observar() {
    if (!("IntersectionObserver" in window)) return;
    // no celular (<=900px, css/documento.css) o painel não rola — rola a página toda: aí a raiz
    // dos observadores passa a ser a janela, senão o painel mede o conteúdo inteiro e todas as
    // folhas "intersectam" o tempo todo
    var raiz = getComputedStyle($painel[0]).overflowY === "auto" ? $painel[0] : null;
    if (observador) observador.disconnect();
    observador = new IntersectionObserver(function (entradas) {
      entradas.forEach(function (en) {
        var meta = metas[Number(en.target.getAttribute("data-nu-pagina")) - 1];
        if (!meta) return;
        if (en.isIntersecting) desenharPagina(meta);
        else descartarPagina(meta);
      });
    }, {root: raiz, rootMargin: JANELA_RENDER, threshold: 0});
    $lista.find(".doc-pdf-folha").each(function () { observador.observe(this); });

    if (spy) spy.disconnect();
    spy = new IntersectionObserver(function (entradas) {
      var melhor = null;
      entradas.forEach(function (en) {
        if (en.isIntersecting && (!melhor || en.boundingClientRect.top < melhor.boundingClientRect.top)) {
          melhor = en;
        }
      });
      if (!melhor) return;
      var nu = Number(melhor.target.getAttribute("data-nu-pagina"));
      if (!nu || nu === paginaAtual) return;
      paginaAtual = nu;
      if (callbacks.aoPagina) callbacks.aoPagina(nu);
    }, {root: raiz, rootMargin: "0px 0px -65% 0px", threshold: 0});
    $lista.find(".doc-pdf-folha").each(function () { spy.observe(this); });
  }

  function montarFolhas(total) {
    $lista.empty();
    metas = [];
    var seqLocal = seq;
    var cauda = Promise.resolve();
    for (var nu = 1; nu <= total; nu++) {
      var $folha = $("<section>").addClass("doc-pdf-folha")
        .attr("data-nu-pagina", nu)
        .attr("aria-label", "Página " + nu)
        .appendTo($lista);
      $("<div>").addClass("doc-pdf-rotulo").text("Página " + nu).appendTo($folha);
      $("<div>").addClass("doc-pdf-quadro").append($("<canvas>")).appendTo($folha);
      metas.push({nu: nu, w: 612, h: 792, renderizada: false, tarefa: null}); // A4 até a medida chegar
    }
    // as medidas reais chegam página a página (barato: só o dicionário da página, sem desenhar);
    // a moldura certa antes do primeiro paint é o que evita o pulo do scroll
    metas.forEach(function (meta) {
      cauda = cauda.then(function () {
        if (seqLocal !== seq) return;
        return pdfDoc.getPage(meta.nu).then(function (pagina) {
          var v = pagina.getViewport({scale: 1});
          meta.w = v.width;
          meta.h = v.height;
          $lista.find('.doc-pdf-folha[data-nu-pagina="' + meta.nu + '"] .doc-pdf-quadro')
            .css("aspect-ratio", v.width + " / " + v.height);
        });
      });
    });
    cauda.then(observar);
  }

  function aviso(texto) {
    if (!$aviso) {
      $aviso = $("<div>").addClass("doc-pdf-aviso").appendTo($painel);
    }
    $aviso.text(texto || "").toggleClass("hidden", !texto);
  }

  var DocPdf = {
    LIMITE_PAGINAS: LIMITE_PAGINAS,

    carregado: function () { return Boolean(pdfDoc); },

    /* Carrega o PDF do /original na primeira vez que o modo PDF é pedido (e só nessa).
     * `url` é a rota original já montada por documento.js (mesma do botão "Original").
     * O fetch leva o cookie de sessão (same-origin, credentials explícito) — a rota é
     * autenticada igualzinha às outras da API. */
    abrir: function (url, opts) {
      opts = opts || {};
      callbacks = opts;
      if (carregando) {
        return carregando.then(function (total) {
          if (opts.aoPronto) opts.aoPronto(total);
          return total;
        });
      }
      aviso("Carregando o PDF…");
      seq++;
      carregando = fetch(url, {credentials: "same-origin"}).then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.arrayBuffer();
      }).then(function (buff) {
        return window.pdfjsLib.getDocument({data: new Uint8Array(buff), isEvalSupported: false}).promise;
      }).then(function (doc) {
        pdfDoc = doc;
        if (doc.numPages > LIMITE_PAGINAS) {
          throw {limite: true, total: doc.numPages};
        }
        montarFolhas(doc.numPages);
        aviso("");
        if (opts.aoPronto) opts.aoPronto(doc.numPages);
        return doc.numPages;
      }).catch(function (erro) {
        carregando = null; // sem promise quebrada presa: a próxima tentativa pode ser um novo abrir()
        pdfDoc = null;
        aviso("");
        if (opts.aoErro) opts.aoErro(erro || {});
        else aviso("Não foi possível abrir o PDF.");
        throw erro;
      });
      return carregando;
    },

    /* Rolou o painel até a página física `nu` (1..total). Os dois numeram igual ao texto
     * extraído — a extração é página a página — então o documento.js só repassa o número. */
    irParaPagina: function (nu, comportamento) {
      if (!pdfDoc) return;
      var $folha = $lista && $lista.find('.doc-pdf-folha[data-nu-pagina="' + nu + '"]');
      if (!$folha || !$folha.length) return;
      $folha[0].scrollIntoView({block: "start",
        behavior: comportamento === "smooth" ? "smooth" : "auto"});
      paginaAtual = nu;
    },

    /* O painel foi escondido: devolve a memória dos bitmaps, guarda o documento carregado
     * (a volta ao modo PDF não baixa de novo — os metadados e o parse ficam). */
    pausar: function () {
      metas.forEach(descartarPagina);
      if (observador) { observador.disconnect(); observador = null; }
      if (spy) { spy.disconnect(); spy = null; }
    },

    /* O painel voltou para a tela: os observadores só valem com o painel visível (um
     * IntersectionObserver de root escondida nunca dispara), então eles são religados aqui e
     * as folhas visíveis de novo são desenhadas de novo. */
    retomar: function () {
      if (pdfDoc && $lista && $lista.children().length) observar();
    }
  };

  window.DocPdf = DocPdf;
})();
