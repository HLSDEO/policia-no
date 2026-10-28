/* Tela do documento (web/documento.html) — a interface única para ler um documento anexado.
 *
 * Antes dela o texto extraído só aparecia num modal de <pre> e a citação da IA que dizia
 * "está na página 7" não tinha para onde levar.
 *
 * Esta tela atende o acervo do inquérito. Qual inquérito é vem da URL:
 *   documento.html?inquerito=<id>&doc=<id>   -> /api/investigacao/inqueritos/<id>/documentos/<id>/...
 *
 * Parâmetros da citação, montados por js/utils/doc_trecho.js a partir do clique no chat:
 *   &pagina=<n>   a página que a IA citou ([doc:ID:N] — ver o marcador [[página N]] no texto)
 *   &trecho=<txt> o trecho literal entre aspas, que vira realce
 *
 * A tela mostra o TEXTO EXTRAÍDO, que é exatamente o que a IA leu — é nele que o realce da
 * citação faz sentido, e é ele que o investigador precisa conferir quando desconfia da resposta.
 * O arquivo original abre em aba nova, pelo botão "Original": embutir num <iframe> não funciona
 * porque o nginx manda X-Frame-Options: DENY (nginx/default.conf), que barra o enquadramento até
 * de mesma origem. Em aba nova também é onde a âncora #page=N passa a valer, o que o iframe não
 * conseguia entregar de todo jeito.
 *
 * O PDF, porém, ganhou leitura dentro da própria tela (js/documento_pdf.js, pdf.js vendorizado —
 * sem iframe, sem tocar nos headers de segurança): os modos Texto | PDF | Lado a lado, com o
 * scroll acompanhando a página entre as duas colunas (nu_pagina de um lado, página física do PDF
 * do outro — é a mesma numeração, a extração é página a página). Só até 300 páginas: o
 * FileResponse do /original não fala HTTP Range, então o arquivo inteiro vai para o navegador de
 * uma vez; PDF grande continua sendo texto aqui + original na aba nova, como sempre foi.
 *
 * Planilha (CSV/XLS/XLSX) tem folha própria em vez de <pre>: o texto extraído é a tabela markdown
 * que o backend montou (documentos_utils._linha_tabela) e volta a ser célula aqui, numa <table>
 * com cabeçalho fixo — ver parsearPlanilha/montarPlanilha. O realce da busca e da citação é
 * aplicado célula a célula, então a navegação ‹ › e o contador continuam valendo.
 *
 * O .docx também saiu do <pre> em dois níveis. No texto normal, a tabela markdown que o extrator
 * agora grava volta a ser <table> pelo mesmo caminho da planilha (parsearBlocos + montarBlocos).
 * E no modo "Formatado" a folha inteira é desenhada a partir dos BLOCOS que o backend devolve em
 * .../render (títulos, parágrafos com negrito/itálico, listas, tabelas) — as MESMAS páginas do
 * texto, então sumário, numeração e citação da IA valem igual nos dois modos. O Formatado
 * substitui o <pre>: a página formatada É a folha, e o realce continua bloco a bloco.
 *
 * O .html ganhou o modo "Renderizado": o arquivo original entra num <iframe sandbox srcdoc> —
 * srcdoc não passa pela rede (o X-Frame-Options: DENY do nginx não o alcança) e o sandbox vazio
 * executa nada do que o documento contiver. A busca e o sumário continuam no painel de texto,
 * que é o que a IA lê; o renderizado é leitura visual, sem paginação para prometer.
 *
 * Documentos de centenas de folhas são desenhados em lotes (um quadro por frame) e, durante uma
 * busca, só as páginas com ocorrência entram no DOM — a contagem por página sai de
 * DocTrecho.contarTermo/contarTrecho, que contam na string sem montar HTML, então o filtro custa
 * o que a leitura manda: nada de redesenhar o documento inteiro a cada tecla. O hash
 * (#pagina=&folha=&marca=) acompanha a navegação para o link da tela abrir exatamente onde o
 * investigador estava.
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome, ""); }

  var idInquerito = paramUrl("inquerito");
  var idDocumento = paramUrl("doc");
  var paginaAlvo = parseInt(paramUrl("pagina"), 10) || 0;
  var trechoCitado = paramUrl("trecho");

  var CHAVE_FONTE = "analyser-doc-fonte";
  var FONTE_MIN = 10, FONTE_MAX = 22, FONTE_PADRAO = 12.5;
  var LOTE_FOLHAS = 24; // folhas desenhadas por frame no render incremental
  var CHAVE_MODO = "analyser-doc-modo";
  // lado é o padrão de fábrica: a tela nasce para conferir o texto contra o papel. Quem salva
  // outro modo (Analyser apertar o botão) manda mais que o padrão (aplicarModo corta PDF para
  // quem não é PDF, e "lado" para quem não é paginado).
  var MODO_PADRAO = "lado";

  var documento = null;
  var paginas = [];
  var $marcas = $();          // realces da busca/citação atual, na ordem do documento
  var indiceMarca = 0;
  var termoBusca = "";
  var buscaTimer = null;
  var contagens = [];         // ocorrências por página com o alvo corrente (string, sem DOM)
  var soHits = true;          // com busca ativa: desenhar só as folhas com ocorrência
  var renderSeq = 0;          // invalida lotes de um render cancelado por outro
  var paginaVisivel = 0;      // atualizada pelo scroll-spy; é a régua do "salto longo"
  var spy = null;
  var modo = MODO_PADRAO;     // "texto" | "pdf" | "lado" | "formatado" (docx) | "renderizado" (html)
  var sincron = null;         // trava anti-ecôo da sincronização de scroll (ver sincronizar*)
  var renderBlocos = null;    // blocos do .../render (docx formatado), buscados uma vez
  var renderBloqueado = false; // o /render falhou ou não bate: oferecer de novo seria o mesmo erro
  var renderHtmlDoc = null;   // texto do original .html já baixado para o iframe
  var renderHtmlCarregando = false;
  var posicaoPousada = false; // posicionar() já fez a primeira viagem; re-renders só voltam a ela

  function lerHash() {
    var o = {};
    String(location.hash || "").replace(/^#/, "").split("&").forEach(function (par) {
      var kv = par.split("=");
      if (kv.length === 2) {
        try { o[kv[0]] = decodeURIComponent(kv[1]); } catch (e) { o[kv[0]] = kv[1]; }
      }
    });
    return o;
  }
  var hashInicial = lerHash();
  // o hash é o "onde eu estava" desta tela; a query é o "onde a IA me mandou" — na volta de um
  // link copiado da própria tela, é o hash que diz a verdade
  if (hashInicial.pagina) paginaAlvo = parseInt(hashInicial.pagina, 10) || paginaAlvo;

  /* Grava no hash a posição da tela (replaceState: histórico do navegador não se enche de
   * micro-navegações de ‹ ›). Sem isso, "copiar o link daqui" seria impossível. */
  function gravarHash(indiceFolha, marca) {
    var p = paginas[indiceFolha] || {};
    var partes = [];
    if (p.nu_pagina) partes.push("pagina=" + encodeURIComponent(p.nu_pagina));
    partes.push("folha=" + indiceFolha);
    if (marca != null) partes.push("marca=" + marca);
    try { history.replaceState(null, "", "#" + partes.join("&")); } catch (e) { /* file:// */ }
  }

  var $sumarioLista = $("#doc-sumario-lista");
  var $folhas = $("#doc-folhas");
  var $aviso = $("#doc-aviso");
  var $citacao = $("#doc-citacao");

  // ---------------------------------------------------------------- API

  /* Prefixo da API do inquérito a que este documento pertence. */
  function base() {
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/documentos/" + encodeURIComponent(idDocumento);
  }

  function escopoValido() {
    return Boolean(idDocumento && idInquerito);
  }

  // ---------------------------------------------------------------- helpers

  function formatarNumero(n) {
    var v = Number(n);
    return isFinite(v) ? v.toLocaleString("pt-BR") : "—";
  }

  // Guarda própria (string vazia, não "—", e data inválida não vira "Invalid Date" na tela),
  // por isso não delega direto. O que estava errado era o miolo: sem acrescentar o "Z" e sem
  // fixar o fuso, o timestamp UTC ingênuo do servidor era lido como horário LOCAL, e a data de
  // processamento aparecia 3 h fora da mesma informação em qualquer outra tela — num sistema
  // pericial, hora que não bate entre telas vira dúvida sobre a prova.
  function formatarDataHora(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return isNaN(d) ? "" : d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function avisar(html) {
    if (!html) { $aviso.addClass("hidden").empty(); return; }
    $aviso.removeClass("hidden").empty().append(html);
  }

  // ---------------------------------------------------------------- tamanho do texto

  function fonteAtual() {
    var v = NaN;
    try { v = parseFloat(localStorage.getItem(CHAVE_FONTE)); } catch (e) { /* sem persistência */ }
    return isFinite(v) && v >= FONTE_MIN && v <= FONTE_MAX ? v : FONTE_PADRAO;
  }

  function aplicarFonte(px) {
    px = Math.min(FONTE_MAX, Math.max(FONTE_MIN, px));
    document.documentElement.style.setProperty("--doc-fonte", px + "px");
    try { localStorage.setItem(CHAVE_FONTE, String(px)); } catch (e) { /* idem */ }
  }

  // ---------------------------------------------------------------- modos de leitura

  /* O PDF renderizado (DocPdf) só faz sentido quando há PDF para abrir e ele cabe no navegador:
   * o /original não serve HTTP Range, então o pdf.js recebe o arquivo inteiro — acima de
   * LIMITE_PAGINAS a tela continua sendo só texto (como sempre foi) + o botão "Original". */
  function podeVerPdf() {
    return Boolean(documento && documento.in_pdf && documento.in_original && !documento.tx_erro);
  }

  function pdfOferecido() {
    if (!podeVerPdf() || !window.DocPdf) return false;
    // paginado: qt_paginas é a contagem de verdade, e o corte acontece antes de baixar qualquer
    // byte; sem paginar a contagem do texto não conta página de PDF, então o corte fica para a
    // carga (DocPdf devolve o aviso de limite e a tela desliga o seletor)
    if (documento.in_paginado && documento.qt_paginas > DocPdf.LIMITE_PAGINAS) return false;
    return true;
  }

  /* Lado a lado é o modo que exige as duas numerações batendo: sem o marcador de página não há
   * como o texto dizer ao PDF "estou na folha 7" — aí só o texto ou só o PDF. */
  function podeLadoALado() {
    return pdfOferecido() && Boolean(documento.in_paginado);
  }

  // ---------------------------------------------------------------- docx formatado / html renderizado

  function tipoArquivo() {
    return String((documento && documento.tp_arquivo) || "").toLowerCase();
  }

  function ehDocx() {
    return Boolean(documento) && tipoArquivo() === "docx";
  }

  function ehHtml() {
    return Boolean(documento) && ["html", "htm"].indexOf(tipoArquivo()) !== -1;
  }

  /* O Formatado lê o arquivo original bloco a bloco — sem ele (ou sem texto, que é o que dá as
   * páginas do sumário) não há o que formatar; a tela continua o texto de sempre. */
  function podeFormatar() {
    return ehDocx() && Boolean(documento.in_original) && !documento.tx_erro && !renderBloqueado;
  }

  /* O Renderizado não depende da extração nenhuma — é o arquivo cru num sandbox. Falha só
   * quando o arquivo sumiu de data/ (aí não há mesmo o que ler). */
  function podeRenderizarHtml() {
    return ehHtml() && Boolean(documento.in_original) && !renderHtmlBloqueado();
  }

  function renderHtmlBloqueado() {
    return renderHtmlDoc === "__falhou";
  }

  /* Um botão por formato que a tela sabe abrir. Quem manda a visibilidade é este render — os
   * botões nascem no HTML e só mudam de cara quando o documento muda, e o formato de um
   * documento não muda nunca. */
  function renderSeletorModo() {
    var $m = $("#doc-modo");
    var temPdf = pdfOferecido(), temDocx = podeFormatar(), temHtml = podeRenderizarHtml();
    if (!temPdf && !temDocx && !temHtml) { $m.addClass("hidden"); return; }
    $m.removeClass("hidden");
    $m.find('[data-modo="pdf"]').toggleClass("hidden", !temPdf);
    $m.find('[data-modo="lado"]').toggleClass("hidden", !(temPdf && documento.in_paginado));
    $m.find('[data-modo="formatado"]').toggleClass("hidden", !temDocx);
    $m.find('[data-modo="renderizado"]').toggleClass("hidden", !temHtml);
    $m.find("button").each(function () {
      $(this).attr("aria-pressed", String($(this).attr("data-modo") === modo));
    });
  }

  function aplicarModo(novo, guardar) {
    var anterior = modo;
    if (["pdf", "lado", "formatado", "renderizado"].indexOf(novo) === -1) novo = "texto";
    if (["pdf", "lado"].indexOf(novo) !== -1 && !pdfOferecido()) novo = "texto";
    if (novo === "lado" && !podeLadoALado()) novo = "pdf";
    if (novo === "formatado" && !podeFormatar()) novo = "texto";
    if (novo === "renderizado" && !podeRenderizarHtml()) novo = "texto";
    modo = novo;
    if (guardar !== false) {
      try { localStorage.setItem(CHAVE_MODO, modo); } catch (e) { /* sem persistência */ }
    }
    $("#doc-painel-texto").toggleClass("ativo", modo !== "pdf" && modo !== "renderizado");
    $("#doc-painel-pdf").toggleClass("ativo", modo === "pdf" || modo === "lado");
    $("#doc-painel-render").toggleClass("ativo", modo === "renderizado");
    renderSeletorModo();

    if (modo === "renderizado") { abrirRenderHtml(); return; }
    if (modo === "formatado") { abrirRenderBlocos(); return; }
    // sair do Formatado para o Texto é trocar os blocos formatados pelo texto extraído — a folha
    // inteira é outra leitura, e é o mesmo renderFolhas de sempre que a refaz
    if (anterior === "formatado") { renderFolhas(posicionar); return; }

    if (!window.DocPdf) return; // texto puro como sempre foi: o DocPdf nem está no ar
    if (modo === "texto") { DocPdf.pausar(); return; }
    if (!DocPdf.carregado()) {
      abrirPdf();
      return;
    }
    DocPdf.retomar();
    DocPdf.irParaPagina(nuPaginaVisivel(), "auto");
  }

  /* A primeira entrada no Formatado é o único momento em que os blocos são buscados. Daqui para
   * frente o modo vai e volta sem tocar na rede. Se o backend recusa (não é docx, arquivo
   * sumiu) ou devolve outra contagem de páginas — o arquivo mudou depois da extração e as
   * folhas sairiam com o rótulo da página errada —, o formato desliga e a leitura segue no
   * texto, como sempre foi. */
  function abrirRenderBlocos() {
    function redesenhar() {
      if (modo === "formatado") renderFolhas(posicionar);
    }
    if (renderBlocos) { redesenhar(); return; }
    $.getJSON(base() + "/render")
      .done(function (resp) {
        var pag = (resp && resp.paginas) || [];
        if (!pag.length || pag.length !== paginas.length) {
          renderBloqueado = true;
          renderSeletorModo();
          Toast.aviso("A formatação deste arquivo não bate com o texto extraído — " +
            "reextrair alinha as duas leituras. A tela segue no texto.");
          aplicarModo("texto", false);
          return;
        }
        renderBlocos = pag;
        redesenhar();
      })
      .fail(function (xhr) {
        renderBloqueado = true;
        renderSeletorModo();
        var motivo = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        Toast.erro(motivo || "Não foi possível ler a formatação deste documento.");
        aplicarModo("texto", false);
      });
  }

  /* O HTML original entra no iframe por srcdoc: não é busca de rede (o X-Frame-Options DENY do
   * nginx não se aplica a ele) e o sandbox="" corta script, formulário e quadro de dentro do
   * próprio documento — HTML anexado é conteúdo sob investigação, não código para executar. */
  function abrirRenderHtml() {
    var $alvo = $("#doc-render-iframe");
    if (renderHtmlBloqueado()) return;
    if (renderHtmlDoc != null) {
      if (!$alvo.attr("srcdoc")) $alvo.attr("srcdoc", renderHtmlDoc);
      return;
    }
    if (renderHtmlCarregando) return;
    renderHtmlCarregando = true;
    $.ajax({ url: base() + "/original", dataType: "text" })
      .done(function (texto) {
        renderHtmlCarregando = false;
        renderHtmlDoc = String(texto || "");
        if (modo === "renderizado") $alvo.attr("srcdoc", renderHtmlDoc);
      })
      .fail(function () {
        renderHtmlCarregando = false;
        renderHtmlDoc = "__falhou";
        renderSeletorModo();
        Toast.erro("Não foi possível ler o HTML original deste documento.");
        aplicarModo("texto", false);
      });
  }

  /* A primeira entrada num modo PDF é o único momento em que o arquivo é buscado. O erro que
   * chega depois de baixar (limite de páginas — a contagem do texto não valia) desliga o
   * seletor: oferecer de novo seria deixar a porta aberta para o mesmo travamento. */
  function abrirPdf() {
    DocPdf.abrir(base() + "/original", {
      aoPronto: function () {
        if (modo === "texto") return; // voltou ao texto enquanto o arquivo descia
        DocPdf.retomar();
        DocPdf.irParaPagina(nuPaginaVisivel(), "auto");
      },
      aoPagina: sincronizarTextoDesdePdf,
      aoErro: function (erro) {
        if (erro && erro.limite) {
          documento.qt_paginas = erro.total; // pdfOferecido passa a dizer não
          aplicarModo("texto", false); // a preferência do usuário continua salva para o próximo PDF
          Toast.aviso("Este PDF tem " + erro.total + " páginas — acima das " +
            DocPdf.LIMITE_PAGINAS + " que a tela abre. A leitura segue no texto extraído; " +
            "o arquivo inteiro está no botão “Original”.");
          return;
        }
        Toast.erro("Não foi possível abrir o PDF. A leitura segue no texto extraído.");
        aplicarModo("texto", false);
      }
    });
  }

  /* A página do PDF que acompanha a folha de texto corrente. Os dois numeram do mesmo jeito
   * (a extração é página a página) — o que não tem nu_pagina (texto pré-marcador de um
   * documento velho) ainda tem um "lugar no PDF": a anterior que tinha. */
  function nuPaginaVisivel() {
    for (var i = Math.min(paginaVisivel, paginas.length - 1); i >= 0; i--) {
      var n = Number((paginas[i] || {}).nu_pagina);
      if (n) return n;
    }
    return 1;
  }

  /* Scroll texto → PDF: chamado de dentro do scroll-spy das folhas. A trava anti-ecôo segura a
   * janela em que o movimento de um painel não pode disparar o movimento de volta do outro.
   * `forcar` é para navegação explícita (sumário, ‹ › da citação): o pedido do investigador vale
   * mesmo se o spy do PDF acabou de mexer na trava. */
  function sincronizarPdfDesdeTexto(forcar) {
    if (modo === "texto" || !window.DocPdf || !DocPdf.carregado()) return;
    if (!forcar && sincron && sincron.de === "pdf") return;
    sincron = {de: "texto"};
    clearTimeout(sincron.t);
    sincron.t = setTimeout(function () { sincron = null; }, 150);
    DocPdf.irParaPagina(nuPaginaVisivel(), "auto");
  }

  /* PDF → texto: só o "lado" precisa; no modo PDF puro não há coluna de texto para acompanhar
   * (e o sumário continua acompanhando por fora, pelo scroll-spy de quem quiser). */
  function sincronizarTextoDesdePdf(nu) {
    if (modo !== "lado") return;
    if (sincron && sincron.de === "texto") return;
    sincron = {de: "pdf"};
    clearTimeout(sincron.t);
    sincron.t = setTimeout(function () { sincron = null; }, 150);
    var i = indiceDaPagina(nu);
    if (i < 0 || i === paginaVisivel) return;
    var $folha = $folhas.find('.doc-folha[data-indice="' + i + '"]');
    if (!$folha.length) return; // folha filtrada pela busca: o texto não está na tela, o PDF fica
    $folha[0].scrollIntoView({block: "start", behavior: "auto"});
    paginaVisivel = i;
    marcarItemAtual(i);
  }

  // ---------------------------------------------------------------- cabeçalho

  function renderCabecalho() {
    var nome = documento.no_curto || documento.no_arquivo || ("Documento " + idDocumento);
    $("#doc-nome").text(nome).attr("title", documento.no_arquivo || nome);
    document.title = nome + " · PolícIA";

    var partes = [(documento.tp_arquivo || "?").toUpperCase()];
    if (documento.tx_erro) {
      partes.push("erro de extração");
    } else {
      partes.push(formatarNumero(documento.qt_caracteres) + " caracteres");
      // só anuncia a contagem quando ela significa página de verdade: num documento sem
      // marcador "1 página" seria uma informação inventada
      if (documento.in_paginado) {
        partes.push(formatarNumero(documento.qt_paginas) + (ehPlanilha() ? " abas" : " páginas"));
      }
    }
    var quando = formatarDataHora(documento.dt_processado);
    if (quando) partes.push(quando);
    $("#doc-meta").text(partes.join(" · "));

    renderBotaoOriginal();
    renderSeletorModo();
    // sem arquivo em data/ não há o que reextrair
    $("#doc-reextrair").toggleClass("hidden", !documento.in_original);
  }

  /* O botão do arquivo original. Só o PDF o navegador de fato exibe; nos outros formatos o clique
   * baixa o arquivo, e o rótulo diz isso em vez de prometer uma visualização que não vem. */
  function renderBotaoOriginal() {
    var $a = $("#doc-original");
    if (!documento.in_original) {
      $a.addClass("hidden").removeAttr("href");
      return;
    }
    var url = base() + "/original";
    // numa aba nova a âncora é respeitada: o PDF abre direto na folha que a IA citou
    if (documento.in_pdf && paginaAlvo) url += "#page=" + encodeURIComponent(paginaAlvo);
    $a.removeClass("hidden")
      .attr("href", url)
      .text(documento.in_pdf ? "Original" : "Baixar original")
      .attr("title", documento.in_pdf
        ? ("Abre o PDF original numa aba nova" + (paginaAlvo ? ", na página " + paginaAlvo : ""))
        : "Baixa o arquivo original para abrir no programa dele");
    if (documento.in_pdf) $a.removeAttr("download");
    else $a.attr("download", "");
  }

  /* Recados que dependem do estado da extração — ficam na faixa acima das folhas porque são
   * acionáveis (o botão de reextrair está neles), não decoração. */
  function renderAviso() {
    if (documento.tx_erro) {
      avisar($("<span>").text(
        "A extração do texto falhou: " + documento.tx_erro +
        " — a IA não lê este documento. O arquivo original continua disponível na aba “Original”."
      ));
      return;
    }
    if (!documento.in_paginado && podeTerPagina()) {
      var $m = $("<span>").text(
        "Este documento foi extraído antes da marcação de páginas: o texto aparece numa folha só, " +
        "e a IA não consegue citar a página. Reextrair relê o arquivo original e corrige isso."
      );
      avisar($m);
      return;
    }
    avisar(null);
  }

  /* Formatos em que existe página para marcar. CSV e TXT não têm — avisar que "falta paginar" um
   * CSV seria prometer o que a reextração não vai entregar. */
  function podeTerPagina() {
    return ["pdf", "xls", "xlsx", "docx"].indexOf((documento.tp_arquivo || "").toLowerCase()) !== -1;
  }

  // ---------------------------------------------------------------- planilhas

  /* CSV, XLS e XLSX ganham visualização de planilha: o texto extraído vira tabela de verdade. */
  function ehPlanilha() {
    return ["csv", "xls", "xlsx"].indexOf((documento.tp_arquivo || "").toLowerCase()) !== -1;
  }

  /* O título da aba — a primeira linha "### <nome>" que o extrator grava na página de cada aba
   * (documentos_utils._abas_do_xlsx/_abas_de_linhas). É o rótulo da folha e do item do sumário:
   * "Página 3" não diz nada sobre uma planilha; "Antenas" diz tudo. O CSV não tem título (uma
   * aba só, anônima), então volta vazio. */
  function tituloPlanilha(texto) {
    var m = String(texto || "").match(/^###[ \t]+(.+)$/m);
    return m ? m[1].trim() : "";
  }

  /* Desfaz o formato de tabela markdown do texto extraído: linha "### <aba>", linhas "| a | b |"
   * com o separador "| --- | --- |" fechando o cabeçalho, e notas "_[planilha truncada…]_". As
   * células podem ser cortadas com segurança no "|": o extrator troca "|" por "/" dentro delas
   * (_linha_tabela), então nenhuma célula contém o separador. O cabeçalho só vale se o separador
   * vier logo depois da primeira linha — sem ele, tudo é corpo (planilha sem linha de título). */
  function parsearPlanilha(texto) {
    var titulo = "";
    var linhas = [];
    var notas = [];
    String(texto || "").split(/\r?\n/).forEach(function (l) {
      var t = l.trim();
      if (!t) return;
      var mTitulo = t.match(/^###[ \t]+(.+)$/);
      if (mTitulo) { titulo = mTitulo[1].trim(); return; }
      var mLinha = t.match(/^\|(.*)\|$/);
      if (mLinha) {
        linhas.push(mLinha[1].split("|").map(function (c) { return c.trim(); }));
        return;
      }
      notas.push(t);
    });
    var cabecalho = null;
    if (linhas.length >= 2 &&
        linhas[1].length &&
        linhas[1].every(function (c) { return /^-+$/.test(c); })) {
      cabecalho = linhas[0];
      linhas = linhas.slice(2);
    }
    return { titulo: titulo, cabecalho: cabecalho, linhas: linhas, notas: notas };
  }

  /* Data ISO da meia-noite ("2024-03-10T00:00:00", saída crua do isoformat do backend) é ruído
   * — a hora nasceu zero. A tela exibe só o dia, mas a busca e a contagem do sumário usam
   * exatamente ESTA forma, senão o selo de ocorrências da folha e os <mark> divergiriam.
   * Célula com hora de verdade (não-zero) fica inteira. */
  var RE_DATA_MEIA_NOITE = /^(\d{4}-\d{2}-\d{2})T00:00(?::00)?(?:\.0+)?$/;
  function celulaExibida(c) {
    var t = String(c == null ? "" : c);
    var m = t.match(RE_DATA_MEIA_NOITE);
    return m ? m[1] : t;
  }

  /* Alinhamento é leitura, não conteúdo: número vai para a direita (tabular-nums no CSS) sem
   * que um caractere mude — busca e IA seguem lendo a string extraída. CPF/CNPJ, datas com
   * barra e telefones ficam de fora de propósito: alinhados à direita virariam colunas de
   * "números" que não se somam. */
  var RE_NUMERO = /^-?(?:\d{1,3}(?:\.\d{3})+(?:[.,]\d{1,3})*|\d+(?:[.,]\d+)?)$/;
  function classeCelula(t) {
    if (RE_NUMERO.test(t)) return "doc-cel-num";
    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return "doc-cel-data";
    return "";
  }

  /* Monta a <table> de uma aba. O realce é o mesmo do <pre> (citação ou busca), só que aplicado
   * célula a célula — um trecho que atravessa duas células não casa, mas cada célula que casa
   * acende o <mark> e conta na navegação ‹ › do mesmo jeito. */
  function montarPlanilha(estrutura, realcarTexto) {
    var $caixa = $("<div>").addClass("doc-folha-tabela");
    var $tabela = $("<table>").appendTo($caixa);
    var qtd = 0;

    function celulasRealcadas(linha) {
      return linha.map(function (c) {
        var exibida = celulaExibida(c);
        var r = realcarTexto(exibida);
        qtd += r.qtd;
        return {
          html: r.html,
          classe: classeCelula(exibida),
          // a célula encurtada (data da meia-noite) entrega o valor cru no title: nada de
          // informação some da tela, só sai do caminho do olho
          titulo: exibida === String(c == null ? "" : c) ? "" : String(c)
        };
      });
    }

    function celula($el, cel) {
      if (cel.classe) $el.addClass(cel.classe);
      if (cel.titulo) $el.attr("title", cel.titulo);
      return $el.html(cel.html);
    }

    if (estrutura.cabecalho) {
      var $tr = $("<tr>").appendTo($("<thead>").appendTo($tabela));
      celulasRealcadas(estrutura.cabecalho).forEach(function (h) {
        celula($("<th>").attr("scope", "col"), h).appendTo($tr);
      });
    }
    if (estrutura.linhas.length) {
      var $tbody = $("<tbody>").appendTo($tabela);
      estrutura.linhas.forEach(function (linha) {
        var $linha = $("<tr>").appendTo($tbody);
        celulasRealcadas(linha).forEach(function (h) {
          celula($("<td>"), h).appendTo($linha);
        });
      });
    }
    estrutura.notas.forEach(function (nota) {
      $("<div>").addClass("doc-tabela-nota").text(nota).appendTo($caixa);
    });
    return { el: $caixa, qtd: qtd };
  }

  // ---------------------------------------------------------------- docx em blocos

  /* O texto extraído de um .docx (agora com as tabelas no mesmo markdown da planilha — ver
   * _extrair_texto_docx no backend) volta a ter forma: linha "| a | b |" é célula, o resto é
   * parágrafo. Diferente da planilha, uma folha de docx tem parágrafo E tabela na mesma página,
   * na ordem em que vieram — por isso o parseia aqui é por blocos, não a folha inteira. */
  function parsearBlocos(texto) {
    var blocos = [];
    var tabela = null;

    function fecharTabela() {
      if (!tabela) return;
      if (tabela.linhas.length >= 2 && tabela.linhas[1].length &&
          tabela.linhas[1].every(function (c) { return /^-+$/.test(c); })) {
        tabela.cabecalho = tabela.linhas[0];
        tabela.linhas = tabela.linhas.slice(2);
      }
      blocos.push(tabela);
      tabela = null;
    }

    String(texto || "").split(/\r?\n/).forEach(function (l) {
      var t = l.trim();
      var m = t.match(/^\|(.*)\|$/);
      if (m) {
        if (!tabela) tabela = { t: "t", cabecalho: null, linhas: [] };
        tabela.linhas.push(m[1].split("|").map(function (c) { return c.trim(); }));
        return;
      }
      fecharTabela();
      if (t) blocos.push({ t: "p", x: t });
    });
    fecharTabela();
    return blocos;
  }

  /* Os blocos da folha — os do texto (parsearBlocos) ou os ricos do /render (títulos, listas,
   * runs com negrito/itálico). Os dois têm a MESMA matéria-prima de busca: cada parágrafo,
   * item e célula é uma string que passa pelo realcarTexto exatamente como a célula da
   * planilha passa, então o mark.doc-trecho, o ‹ › e o contador do sumário não mudam de lei. */
  function montarBlocos(blocos, realcarTexto, $folha, $cab) {
    var kTabela = 0;
    $folha = $("<div>").addClass("doc-folha-blocos").appendTo($folha);
    blocos.forEach(function (b) {
      if (b.t === "h") {
        var nivel = Math.min(6, Math.max(1, Number(b.n) || 1));
        $("<h" + nivel + ">").addClass("doc-bloco-titulo")
          .html(realcarTexto(String(b.x || "")).html).appendTo($folha);
      } else if (b.t === "l") {
        var $lista = $("<" + (b.ordem ? "ol" : "ul") + ">")
          .addClass("doc-bloco-lista").appendTo($folha);
        (b.itens || []).forEach(function (item) {
          $("<li>").html(realcarTexto(String(item || "")).html).appendTo($lista);
        });
      } else if (b.t === "t") {
        var montada = montarPlanilha(
          { titulo: "", cabecalho: b.cabecalho || null, linhas: b.linhas || [], notas: [] },
          realcarTexto);
        montada.el.addClass("doc-bloco-tabela").appendTo($folha);
        // o cabeçalho da tabela gruda abaixo do cabeçalho da folha, como na planilha (o top é a
        // altura real dele — nome de página longo quebra linha e muda tudo)
        montada.el.find("th").css("top", $cab.outerHeight() + "px");
        // uma aba de dados por tabela da folha: o investigador leva o que está lendo, não a
        // página inteira (o texto completo já tem botão próprio no cabeçalho da folha)
        $("<button>").attr("type", "button")
          .addClass("doc-bloco-csv")
          .attr("data-k", kTabela)
          .attr("title", "Baixa os dados desta tabela como .csv (abre no Excel)")
          .attr("aria-label", "Baixar tabela " + (kTabela + 1) + " como CSV")
          .text("baixar .csv")
          .insertAfter(montada.el);
        kTabela++;
      } else if (b.runs && b.runs.length) {
        // parágrafo com negrito/itálico/sublinhado: run a run, cada um realçado por si — o
        // backend já fundiu os vizinhos de mesma formatação, então na prática o texto normal
        // inteiro é um run só e a busca casa como no <pre>
        var $p = $("<p>").addClass("doc-bloco-para").appendTo($folha);
        b.runs.forEach(function (r) {
          var html = realcarTexto(String(r.x || "")).html;
          if (r.u) html = "<u>" + html + "</u>";
          if (r.i) html = "<em>" + html + "</em>";
          if (r.b) html = "<strong>" + html + "</strong>";
          $("<span>").html(html).appendTo($p);
        });
      } else {
        $("<p>").addClass("doc-bloco-para")
          .html(realcarTexto(String(b.x || "")).html).appendTo($folha);
      }
    });
    return kTabela;
  }

  // ---------------------------------------------------------------- folhas e sumário

  function rotuloPagina(p) {
    if (ehPlanilha()) {
      var titulo = tituloPlanilha(p.tx_conteudo);
      if (titulo) return titulo;
      if (!documento.in_paginado) return "Planilha";
    }
    // página 0 é o texto que veio antes do primeiro marcador (ver paginas_do_texto no backend);
    // e documento sem marcador nenhum não tem número de página para mostrar
    if (!documento.in_paginado) return "Texto extraído";
    if (!p.nu_pagina) return "Início do documento";
    return "Página " + p.nu_pagina;
  }

  function previa(p) {
    if (ehPlanilha()) {
      // o nome da aba resume melhor que as primeiras células ("| 912345678 | 10/03 | ...")
      var titulo = tituloPlanilha(p.tx_conteudo);
      if (titulo) return titulo;
    }
    var limpo = String(p.tx_conteudo || "").replace(/\s+/g, " ").trim();
    return limpo ? limpo.slice(0, 160) : "(página sem texto)";
  }

  /* Ocorrências desta página com o alvo corrente, contadas na string (sem montar HTML) — é o que
   * permite filtrar as folhas e abastecer o selo do sumário antes de qualquer desenho. Na
   * planilha vale a mesma regra do realce: conta célula a célula. */
  function contarPagina(p, planilha) {
    function um(t) {
      if (termoBusca) return DocTrecho.contarTermo(t, termoBusca);
      if (trechoCitado) return DocTrecho.contarTrecho(t, trechoCitado);
      return 0;
    }
    if (!planilha && !ehDocx()) return um(p.tx_conteudo);
    if (ehDocx()) {
      // a folha de docx é desenhada em blocos — o bloco que não é desenhado inteiro não pode
      // contar inteiro: a contagem do sumário tem de bater com os <mark> que aparecem na tela
      var nb = 0;
      parsearBlocos(p.tx_conteudo).forEach(function (b) {
        if (b.t !== "t") { nb += um(b.x || ""); return; }
        if (b.cabecalho) b.cabecalho.forEach(function (c) { nb += um(celulaExibida(c)); });
        b.linhas.forEach(function (ln) {
          ln.forEach(function (c) { nb += um(celulaExibida(c)); });
        });
      });
      return nb;
    }
    // celulaExibida também aqui: a contagem do sumário tem de bater com o realce
    // encontra na folha (que mostra a célula encurtada, não o ISO cru)
    var e = parsearPlanilha(p.tx_conteudo), n = 0;
    if (e.cabecalho) e.cabecalho.forEach(function (c) { n += um(celulaExibida(c)); });
    e.linhas.forEach(function (linha) {
      linha.forEach(function (c) { n += um(celulaExibida(c)); });
    });
    return n;
  }

  /* Uma folha do documento (cabeçalho + <pre> ou <table>), com botão de copiar. É o miolo do
   * render, chamado em lotes por renderFolhas. */
  function montarFolha(p, i, realcarTexto, planilha) {
    var $folha = $("<section>").addClass("doc-folha").attr("data-indice", i)
      .attr("aria-label", rotuloPagina(p)).appendTo($folhas);
    var $cab = $("<div>").addClass("doc-folha-cabecalho").appendTo($folha);
    $("<span>").text(rotuloPagina(p)).appendTo($cab);

    var r;
    var estrutura = planilha ? parsearPlanilha(p.tx_conteudo) : null;
    var comTabela = Boolean(estrutura && (estrutura.cabecalho || estrutura.linhas.length));
    if (comTabela) {
      $folha.addClass("doc-folha-planilha"); // a folha que pode estourar a tela (CSS)
      // o tamanho da aba dito no cabeçalho: num CDR de milhares de linhas é a primeira
      // informação que o investigador quer, e ele ainda não rolou nada para saber
      var colunas = estrutura.cabecalho ? estrutura.cabecalho.length :
        estrutura.linhas.reduce(function (m, l) { return Math.max(m, l.length); }, 0);
      var nLinhas = estrutura.linhas.length;
      $("<span>").addClass("doc-folha-contagem")
        .attr("title", "Contagem das linhas com conteúdo desta aba")
        .text(nLinhas + (nLinhas === 1 ? " linha" : " linhas") + " × " +
              colunas + (colunas === 1 ? " coluna" : " colunas"))
        .appendTo($cab);
      var montada = montarPlanilha(estrutura, realcarTexto);
      $folha.append(montada.el);
      // o <th> gruda logo abaixo do cabeçalho da folha, que já é sticky: o top em px é a
      // altura real dele (medida aqui, e não fixa no CSS, porque um nome de aba longo quebra
      // linha e muda a altura)
      montada.el.find("th").css("top", $cab.outerHeight() + "px");
    } else if (ehDocx()) {
      // docx é sempre em blocos: no texto, parágrafos + tabelas do markdown extraído; no
      // Formatado, os blocos ricos do backend substituem o <pre> — as páginas são as mesmas
      var formatado = modo === "formatado" && renderBlocos;
      var blocos = formatado
        ? (((renderBlocos[i] || {}).blocos) || [])
        : parsearBlocos(p.tx_conteudo);
      if (!blocos.length) {
        $("<span>").addClass("doc-folha-vazia").text(
          "sem texto — provável folha sem conteúdo"
        ).appendTo($cab);
      }
      montarBlocos(blocos, realcarTexto, $folha, $cab);
    } else {
      r = realcarTexto(p.tx_conteudo);
      // "sem texto — sem OCR" é recado de folha de PDF digitalizado; planilha vazia é só
      // planilha vazia, não ganha o recado
      if (!planilha && !String(p.tx_conteudo || "").trim()) {
        $("<span>").addClass("doc-folha-vazia").text(
          "sem texto — provável folha digitalizada sem OCR"
        ).appendTo($cab);
      }
      $("<pre>").addClass("doc-folha-texto").html(r.html).appendTo($folha);
    }

    $("<button>").attr("type", "button")
      .addClass("doc-folha-copiar")
      .attr("title", "Copiar o texto extraído desta página")
      .attr("aria-label", "Copiar o texto: " + rotuloPagina(p))
      .text("copiar")
      .appendTo($cab);

    if (comTabela) {
      $("<button>").attr("type", "button")
        .addClass("doc-folha-csv")
        .attr("title", "Baixa os dados desta aba como .csv (abre no Excel)")
        .attr("aria-label", "Baixar CSV: " + rotuloPagina(p))
        .text("baixar .csv")
        .appendTo($cab);
    }
  }

  /* Redesenha as folhas com o realce corrente. `alvo` é o trecho citado ou o termo digitado — os
   * dois realçam do mesmo jeito, mas casam diferente (ver DocTrecho.realcar/realcarTermo).
   *
   * Dois expedientes contra o travamento em documentos grandes: as folhas são montadas em lotes
   * de um frame (o primeiro paint não espera o documento todo), e com busca ativa só entram no
   * DOM as páginas com ocorrência — o restante continua acessível pelo sumário, que lista tudo.
   * `depois` roda quando o último lote terminou (é ali que posicionar/irParaMarca encontram o
   * DOM completo). */
  function renderFolhas(depois) {
    renderSeq++;
    var seq = renderSeq;
    $folhas.empty();
    if (spy) { spy.disconnect(); spy = null; }
    if (!paginas.length) {
      $("<div>").addClass("doc-vazio").text(
        documento.tx_erro
          ? "Não há texto extraído deste documento."
          : "O documento foi anexado, mas nenhum texto foi extraído dele."
      ).appendTo($folhas);
      $sumarioLista.empty();
      $("#doc-sumario-contagem").text("");
      $marcas = $();
      contagens = [];
      renderFiltro();
      if (depois) depois();
      return;
    }

    var totalMarcas = 0;
    var realcarTexto = termoBusca
      ? function (t) { return DocTrecho.realcarTermo(t, termoBusca); }
      : function (t) { return DocTrecho.realcar(t, trechoCitado); };
    var planilha = ehPlanilha();

    contagens = paginas.map(function (p) {
      var n = contarPagina(p, planilha);
      totalMarcas += n;
      return n;
    });

    var filtrado = Boolean(termoBusca) && soHits;
    var indices = [];
    paginas.forEach(function (_p, i) {
      if (!filtrado || contagens[i] > 0) indices.push(i);
    });

    renderSumario(contagens);
    renderFaixaRealce(totalMarcas);
    renderFiltro();

    if (!indices.length) {
      $("<div>").addClass("doc-vazio").text(
        "Nenhuma página contém “" + termoBusca + "”."
      ).appendTo($folhas);
      $marcas = $();
      indiceMarca = 0;
      if (depois) depois();
      return;
    }

    var pos = 0;
    function quadro() {
      if (seq !== renderSeq) return; // um novo render assumiu a tela no meio do caminho
      var fim = Math.min(pos + LOTE_FOLHAS, indices.length);
      for (; pos < fim; pos++) montarFolha(paginas[indices[pos]], indices[pos], realcarTexto, planilha);
      if (pos < indices.length) { requestAnimationFrame(quadro); return; }
      $marcas = $folhas.find("mark.doc-trecho");
      indiceMarca = 0;
      observarFolhas();
      if (depois) depois();
    }
    requestAnimationFrame(quadro);
  }

  function renderSumario(porPagina) {
    $sumarioLista.empty();
    $("#doc-sumario-rotulo").text(
      ehPlanilha()
        ? (documento.in_paginado ? "Abas" : "Planilha")
        : (documento.in_paginado ? "Páginas" : "Documento")
    );
    $("#doc-sumario-contagem").text(
      documento.in_paginado ? formatarNumero(paginas.length) : ""
    );

    paginas.forEach(function (p, i) {
      var $item = $("<button>")
        .attr("type", "button")
        .addClass("doc-sumario-item")
        .attr("data-indice", i)
        .appendTo($sumarioLista);
      if (!String(p.tx_conteudo || "").trim()) $item.addClass("vazia");

      var $num = $("<div>").addClass("doc-sumario-item-num").appendTo($item);
      $("<span>").text(rotuloPagina(p)).appendTo($num);
      if (porPagina[i]) {
        $("<span>").addClass("doc-sumario-item-hits").text(porPagina[i]).appendTo($num);
      }
      var $prev = $("<div>").addClass("doc-sumario-item-previa").appendTo($item);
      // o termo buscado também acende na prévia: com o documento filtrado, é o sumário que diz
      // ONDE em cada página o caso aparece, sem precisar abrir a folha
      if (termoBusca) $prev.html(DocTrecho.realcarTermo(previa(p), termoBusca).html);
      else $prev.text(previa(p));
    });
  }

  /* O que o filtro de busca está fazendo, dito em voz alta e com um clique para desfazer:
   * esconder páginas sem aviso faria a tela parecer quebrada. */
  function renderFiltro() {
    var $f = $("#doc-busca-filtro");
    var comHits = contagens.filter(function (n) { return n > 0; }).length;
    if (!termoBusca || !paginas.length || !comHits) { $f.addClass("hidden").text(""); return; }
    $f.removeClass("hidden").text(
      soHits
        ? "Mostrando as " + comHits + " de " + formatarNumero(paginas.length) +
          " páginas com ocorrência — ver todas"
        : "Mostrando todas as " + formatarNumero(paginas.length) +
          " páginas — ver só as " + comHits + " com ocorrência"
    );
  }

  /* A faixa acima das folhas diz sempre O QUE ESTÁ REALÇADO agora — e só existe uma, porque só
   * existe um realce. A busca digitada tem precedência sobre a citação: foi o último pedido do
   * investigador, e deixar o rótulo dizendo "trecho citado pela IA" enquanto o amarelo já é o da
   * busca faria o contador ‹ › mentir sobre o que está contando. */
  function renderFaixaRealce(total) {
    $("#doc-busca-info").text(
      termoBusca
        ? (total ? total + (total === 1 ? " ocorrência" : " ocorrências") : "Nada encontrado.")
        : ""
    );
    $("#doc-citacao-nav").toggleClass("hidden", total < 2);
    $("#doc-trecho-posicao").text(total ? "1 de " + total : "");

    if (termoBusca) {
      $citacao.removeClass("hidden");
      $("#doc-citacao-rotulo").text("Busca no documento");
      var t = total
        ? "“" + termoBusca + "”"
        : "“" + termoBusca + "” não aparece no texto extraído deste documento.";
      $("#doc-citacao-texto").text(t).attr("title", t);
      return;
    }
    renderCitacao();
  }

  function marcarItemAtual(indice, naTela) {
    var $itens = $sumarioLista.find(".doc-sumario-item")
      .removeClass("atual").removeAttr("aria-current");
    var $alvo = $itens.filter('[data-indice="' + indice + '"]')
      .addClass("atual").attr("aria-current", "true");
    // acompanhado pelo scroll-spy, o sumário precisa acompanhar a leitura — mas só rola a lista
    // quando o item saiu da janela dela (senão cada quadro da tela sacudiria a lateral)
    if (naTela && $alvo.length) {
      var item = $alvo[0], caixa = $sumarioLista[0];
      if (item.offsetTop < caixa.scrollTop ||
          item.offsetTop + item.offsetHeight > caixa.scrollTop + caixa.clientHeight) {
        item.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function irParaPagina(indice, comportamento) {
    indice = Number(indice);
    // smooth por centenas de folhas é um slide show picotado (o navegador percorre o caminho
    // todo): salto curto anima, salto longo teleporta
    var comportamentoAlvo = comportamento || "auto";
    if (comportamentoAlvo === "smooth" && Math.abs(indice - paginaVisivel) > 10) {
      comportamentoAlvo = "auto";
    }
    if (modo === "pdf") {
      // sem coluna de texto na tela, o sumário comanda o PDF direto
      paginaVisivel = indice;
      marcarItemAtual(indice);
      gravarHash(indice, null);
      if (window.DocPdf && DocPdf.carregado()) {
        DocPdf.irParaPagina(nuPaginaVisivel(), comportamentoAlvo);
      }
      return;
    }
    if (modo === "renderizado") {
      // o iframe não pagina: clicar no sumário é pedido de leitura do TEXTO — o painel volta
      // para ele e só então salta para a folha pedida
      aplicarModo("texto", false);
      renderFolhas(function () { irParaPagina(indice, comportamentoAlvo); });
      return;
    }
    var $folha = $folhas.find('.doc-folha[data-indice="' + indice + '"]');
    if (!$folha.length) return;
    $folha[0].scrollIntoView({ block: "start", behavior: comportamentoAlvo });
    paginaVisivel = indice;
    marcarItemAtual(indice);
    gravarHash(indice, null);
    sincronizarPdfDesdeTexto(true); // clique no sumário é pedido explícito: o PDF acompanha
  }

  function irParaMarca(indice) {
    if (!$marcas.length) return;
    indiceMarca = (indice + $marcas.length) % $marcas.length;
    $marcas.removeClass("atual");
    var alvo = $marcas.eq(indiceMarca).addClass("atual")[0];
    if (alvo && alvo.scrollIntoView) alvo.scrollIntoView({ block: "center" });
    $("#doc-trecho-posicao").text(indiceMarca + 1 + " de " + $marcas.length);
    var $folha = $marcas.eq(indiceMarca).closest(".doc-folha");
    if ($folha.length) {
      paginaVisivel = Number($folha.attr("data-indice"));
      marcarItemAtual($folha.attr("data-indice"));
      gravarHash(paginaVisivel, indiceMarca);
      // ‹ › também comanda o PDF: no modo puro porque não há outra coluna, no lado a lado para
      // o par sair junto do realce (o scroll-spy até acompanharia, mas no ritmo dos eventos;
      // aqui o salto é o do clique)
      if (modo !== "texto" && window.DocPdf && DocPdf.carregado()) {
        sincronizarPdfDesdeTexto(true); // navegação explícita: manda mesmo com a trava do spy ativa
      }
    }
  }

  /* Scroll-spy: o item "atual" do sumário segue a leitura, não só o clique. A faixa superior do
   * rootMargin elege a folha que está de fato no topo da área visível. */
  function observarFolhas() {
    if (!("IntersectionObserver" in window)) return;
    var $painel = $("#doc-painel-texto"); // não ".ativo": no lado a lado os dois painéis estão ativos
    var raiz = $painel.length && getComputedStyle($painel[0]).overflowY === "auto" ? $painel[0] : null;
    if (spy) spy.disconnect();
    spy = new IntersectionObserver(function (entradas) {
      var melhor = null;
      entradas.forEach(function (en) {
        if (en.isIntersecting && (!melhor || en.boundingClientRect.top < melhor.boundingClientRect.top)) {
          melhor = en;
        }
      });
      if (!melhor) return;
      var i = Number(melhor.target.getAttribute("data-indice"));
      if (i === paginaVisivel) return;
      paginaVisivel = i;
      marcarItemAtual(i, true);
      sincronizarPdfDesdeTexto();
    }, { root: raiz, rootMargin: "0px 0px -65% 0px", threshold: 0 });
    $folhas.find(".doc-folha").each(function () { spy.observe(this); });
  }

  // ---------------------------------------------------------------- citação da IA

  /* Onde a tela pousa depois de carregar. A ordem é a da confiança: o hash (a última posição
   * desta própria tela, num link copiado) vem primeiro; depois o trecho literal, que é a
   * evidência que o investigador clicou e vale mais do que o número da página (a IA erra a
   * página com mais facilidade do que erra a transcrição); a página; e por fim o começo. */
  function posicionar() {
    if (!paginas.length) return;
    // quem já pousou (troca de modo, filtro da busca) volta de onde o investigador estava — a
    // viagem inicial (hash, citação da IA, página citada) é uma só
    if (posicaoPousada) { irParaPagina(paginaVisivel); return; }
    posicaoPousada = true;
    var hf = parseInt(hashInicial.folha, 10);
    if (isFinite(hf) && $folhas.find('.doc-folha[data-indice="' + hf + '"]').length) {
      var hm = parseInt(hashInicial.marca, 10);
      if (isFinite(hm) && $marcas.length) { irParaMarca(hm); return; }
      irParaPagina(hf);
      return;
    }
    if (trechoCitado && $marcas.length) { irParaMarca(0); return; }
    var indice = indiceDaPagina(paginaAlvo);
    if (indice >= 0) { irParaPagina(indice); return; }
    marcarItemAtual(0);
  }

  function indiceDaPagina(numero) {
    if (!numero) return -1;
    for (var i = 0; i < paginas.length; i++) {
      if (Number(paginas[i].nu_pagina) === Number(numero)) return i;
    }
    return -1;
  }

  /* A faixa da citação existe para o investigador saber POR QUE a tela abriu onde abriu — e para
   * dizer com todas as letras quando o trecho não foi encontrado. Silenciar isso faria parecer
   * que o clique no chat foi ignorado. */
  function renderCitacao() {
    if (!trechoCitado && !paginaAlvo) { $citacao.addClass("hidden"); return; }
    $citacao.removeClass("hidden");

    var rotulo = "Trecho citado pela IA";
    var texto = trechoCitado;

    if (trechoCitado && !$marcas.length) {
      rotulo = "Trecho não localizado";
      texto = "“" + trechoCitado + "” não foi encontrado neste texto" +
        (paginaAlvo && indiceDaPagina(paginaAlvo) >= 0
          ? " — a tela abriu na página " + paginaAlvo + ", que foi a citada."
          : " — o documento abriu no começo.");
    } else if (!trechoCitado && paginaAlvo) {
      rotulo = "Página citada pela IA";
      texto = indiceDaPagina(paginaAlvo) >= 0
        ? "Página " + paginaAlvo
        : "Página " + paginaAlvo + " — este documento não tem essa página.";
    }
    $("#doc-citacao-rotulo").text(rotulo);
    $("#doc-citacao-texto").text(texto).attr("title", texto);
  }

  /* Dispensar a faixa também apaga o realce: ela é a única explicação do porquê daquele
   * amarelo no texto, e realce sem explicação vira sujeira na leitura. */
  function fecharFaixa() {
    if (termoBusca) {
      termoBusca = "";
      $("#doc-busca").val("");
    } else {
      trechoCitado = "";
      paginaAlvo = 0;
      // o link do original levava #page= da citação dispensada; sem isto ele ficaria apontando
      // para uma página que a tela não diz mais de onde veio
      renderBotaoOriginal();
    }
    renderFolhas();
    $citacao.addClass("hidden");
  }

  // ---------------------------------------------------------------- copiar folha

  function copiarTexto(texto, $btn) {
    function ok() {
      Toast.sucesso("Texto da página copiado.");
      var rotulo = $btn.text();
      $btn.text("copiado").prop("disabled", true);
      setTimeout(function () { $btn.text(rotulo).prop("disabled", false); }, 1200);
    }
    // clipboard API exige contexto seguro; no HTTP interno o caminho é o reserva clássico
    function reserva() {
      var $ta = $("<textarea>").val(texto)
        .css({ position: "fixed", top: "-1000px" }).appendTo("body");
      $ta[0].select();
      var feito = false;
      try { feito = document.execCommand("copy"); } catch (e) { /* sem permissão */ }
      $ta.remove();
      if (feito) ok();
      else Toast.erro("Não foi possível copiar — selecione o texto e copie manualmente.");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(ok, reserva);
    } else reserva();
  }

  /* O .csv da aba: os dados como a tela os mostra (inclusive o "/" que o extrator pôs no lugar
   * do "|" — é isto que está aqui; o binário inteiro fica a um clique no "Original"). Ponto e
   * vírgula, aspas e BOM são a receita do Excel pt-BR abrir acento e colunas certas na primeira
   * tentativa — CSV com vírgula é para o Excel português uma coluna só. Serve também ao .xls
   * que o Excel de hoje não abre: daqui ele sai como o que se lê na tela. */
  function baixarCsv(i) {
    var e = parsearPlanilha(String((paginas[i] || {}).tx_conteudo || ""));
    var linhas = [];
    if (e.cabecalho) linhas.push(e.cabecalho);
    linhas = linhas.concat(e.linhas);
    var nome = String(documento.no_curto || "documento").replace(/\.[^.]+$/, "");
    var aba = tituloPlanilha((paginas[i] || {}).tx_conteudo);
    if (aba) nome += "-" + aba;
    baixarCsvLinhas(linhas, nome, "aba");
  }

  /* A k-ésima tabela desta folha (docx em bloco ou formatado) sai como o que se lê na tela. */
  function baixarCsvTabela(i, k) {
    var blocos = modo === "formatado" && renderBlocos
      ? (((renderBlocos[i] || {}).blocos) || [])
      : parsearBlocos(String((paginas[i] || {}).tx_conteudo || ""));
    var tabelas = blocos.filter(function (b) { return b.t === "t"; });
    var tab = tabelas[k];
    if (!tab) { Toast.erro("Esta tabela não tem linhas para baixar."); return; }
    var linhas = [];
    if (tab.cabecalho) linhas.push(tab.cabecalho);
    linhas = linhas.concat(tab.linhas);
    var nome = String(documento.no_curto || "documento").replace(/\.[^.]+$/, "") +
      (tabelas.length > 1 ? "-tabela-" + (k + 1) : "");
    baixarCsvLinhas(linhas, nome, "tabela");
  }

  function baixarCsvLinhas(linhas, nomeBase, rotulo) {
    if (!linhas.length) {
      Toast.erro("Esta " + rotulo + " não tem linhas para baixar.");
      return;
    }
    var corpo = linhas.map(function (ln) {
      return ln.map(function (c) {
        var s = String(c == null ? "" : c);
        return /[;"\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      }).join(";");
    }).join("\r\n");
    var nome = nomeBase.replace(/[\\/:*?"<>|\r\n]/g, "_") + ".csv";
    var url = URL.createObjectURL(new Blob(["\ufeff" + corpo],
      { type: "text/csv;charset=utf-8" }));
    var a = document.createElement("a");
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 2000);
  }

  // ---------------------------------------------------------------- eventos

  $sumarioLista.on("click", ".doc-sumario-item", function () {
    var i = Number($(this).attr("data-indice"));
    // com o filtro de busca ligado a folha pode nem estar no DOM: voltar a mostrar tudo e aí sim
    // pular para ela — clicar no sumário é pedido de leitura, não de filtro
    if (termoBusca && soHits && !contagens[i]) {
      soHits = false;
      renderFolhas(function () { irParaPagina(i, "smooth"); });
      return;
    }
    irParaPagina(i, "smooth");
  });

  $("#doc-trecho-anterior").on("click", function () { irParaMarca(indiceMarca - 1); });
  $("#doc-trecho-proximo").on("click", function () { irParaMarca(indiceMarca + 1); });

  $("#doc-citacao-fechar").on("click", fecharFaixa);

  $("#doc-modo").on("click", "button", function () {
    aplicarModo($(this).attr("data-modo"));
  });

  $("#doc-busca-filtro").on("click", function () {
    soHits = !soHits;
    renderFolhas();
  });

  $folhas.on("click", ".doc-folha-copiar", function () {
    var i = Number($(this).closest(".doc-folha").attr("data-indice"));
    copiarTexto(String((paginas[i] || {}).tx_conteudo || ""), $(this));
  });

  $folhas.on("click", ".doc-folha-csv", function () {
    baixarCsv(Number($(this).closest(".doc-folha").attr("data-indice")));
  });

  $folhas.on("click", ".doc-bloco-csv", function () {
    baixarCsvTabela(
      Number($(this).closest(".doc-folha").attr("data-indice")),
      Number($(this).attr("data-k")));
  });

  $("#doc-busca").on("input", function () {
    var valor = $(this).val();
    clearTimeout(buscaTimer);
    // documento de centenas de páginas é redesenhado em lotes a cada busca; o atraso evita
    // refazer isso a cada tecla (e o filtro mantém o lote pequeno)
    buscaTimer = setTimeout(function () {
      var novo = String(valor || "").trim();
      if (novo !== termoBusca) soHits = true; // termo novo, filtro de volta ao padrão
      termoBusca = novo;
      // buscar é pedido sobre o TEXTO: no modo PDF puro (ou no HTML renderizado, que não tem
      // realce nenhum) o realce nasceria escondido, então a digitação puxa o texto de volta
      // para a tela (lado a lado quando o documento permite)
      if (novo && (modo === "pdf" || modo === "renderizado")) {
        aplicarModo(podeLadoALado() ? "lado" : "texto", false);
      }
      renderFolhas(function () {
        if (termoBusca && $marcas.length) irParaMarca(0);
        else if (!termoBusca) posicionar();
      });
    }, 220);
  });

  $("#doc-fonte-menos").on("click", function () { aplicarFonte(fonteAtual() - 1); });
  $("#doc-fonte-mais").on("click", function () { aplicarFonte(fonteAtual() + 1); });

  // teclado: Enter/Shift+Enter dançam entre as ocorrências, Esc dispensa a faixa,
  // Ctrl+F (ou /) levam o foco para a busca — os gestos de quem vasculha documento grande
  $(document).on("keydown", function (e) {
    // com um modal (Dialogo, toast de erro) na frente, o teclado é dele — a tela não Intercepta
    if ($(".modal-overlay").length) return;
    var emCampo = $(e.target).is("input, textarea, select") || e.target.isContentEditable;
    if (e.key === "Escape") {
      // dentro do campo de busca o Esc é do próprio campo (limpa); na tela, é da faixa
      if (!emCampo && !$citacao.hasClass("hidden")) fecharFaixa();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "f" || e.key === "F")) {
      e.preventDefault();
      $("#doc-busca").trigger("focus").select();
      return;
    }
    if (emCampo) {
      if (e.key === "Enter" && e.target.id === "doc-busca" && $marcas.length) {
        e.preventDefault();
        irParaMarca(indiceMarca + (e.shiftKey ? -1 : 1));
      }
      return;
    }
    if (e.key === "/") {
      e.preventDefault();
      $("#doc-busca").trigger("focus").select();
      return;
    }
    if (e.key === "Enter" && $marcas.length > 1 && !$citacao.hasClass("hidden")) {
      e.preventDefault();
      irParaMarca(indiceMarca + (e.shiftKey ? -1 : 1));
    }
  });

  $("#doc-reextrair").on("click", function () {
    var $btn = $(this);
    Dialogo.confirmar({
      titulo: "Reextrair o texto",
      mensagem: "Reler o arquivo original e regravar o texto extraído deste documento? " +
        "É o que traz a marcação de páginas para documentos anexados antes dela. " +
        "As pessoas e achados já levantados pela IA não são refeitos.",
      rotuloOk: "Reextrair"
    }).then(function (ok) {
      if (!ok) return;
      $btn.prop("disabled", true).text("Reextraindo…");
      $.ajax({ url: base() + "/reextrair", method: "POST" })
        .done(function () { window.location.reload(); })
        .fail(function (xhr) {
          $btn.prop("disabled", false).text("Reextrair");
          Dialogo.alerta({
            titulo: "Erro",
            mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao reextrair o documento."
          });
        });
    });
  });

  // ---------------------------------------------------------------- carga

  function mensagemFalha(xhr) {
    if (xhr.status === 401) return "A sessão expirou. Recarregue a página e entre de novo.";
    if (xhr.status === 403) return "Você não tem acesso a este documento neste inquérito.";
    if (xhr.status === 404) return "Este documento não existe mais neste inquérito.";
    return (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao carregar o documento.";
  }

  function carregar() {
    if (!escopoValido()) {
      $folhas.empty().append($("<div>").addClass("doc-vazio").text(
        "Endereço incompleto: falta dizer qual documento abrir (?doc=) e de qual inquérito (?inquerito=)."
      ));
      return;
    }
    $folhas.empty().append($("<div>").addClass("doc-vazio").text("Carregando o documento…"));
    Carregando.emBloco($folhas);
    $.getJSON(base() + "/visualizacao")
      .done(function (resp) {
        Carregando.limpar($folhas);
        documento = resp;
        paginas = resp.paginas || [];
        renderCabecalho();
        renderAviso();
        // o modo salvo na última sessão só vale se este documento o aceita (aplicarModo corta
        // PDF para quem não é PDF, e "lado" para quem não é paginado) — e não reescreve a chave
        var modoSalvo = MODO_PADRAO;
        try { modoSalvo = localStorage.getItem(CHAVE_MODO) || MODO_PADRAO; } catch (e) { /* idem */ }
        aplicarModo(modoSalvo, false);
        // o callback do render incremental é o ponto em que o DOM está completo — é ali, e não
        // num quadro solto, que posicionar encontra as folhas e os realces todos
        renderFolhas(posicionar);
      })
      .fail(function (xhr) {
        Carregando.limpar($folhas);
        $folhas.empty().append($("<div>").addClass("doc-vazio").text(mensagemFalha(xhr)));
      });
  }

  aplicarFonte(fonteAtual());
  carregar();
})();
