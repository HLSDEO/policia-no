/* Coleta os arquivos soltos (drag and drop) numa drop-zone, INCLUINDO pastas inteiras.
 *
 * O clique já resolve pasta via input[webkitdirectory], que preenche webkitRelativePath; no
 * arraste, porém, dataTransfer.files devolve a pasta como um "arquivo" vazio sem caminho — por
 * isso soltar uma pasta numa drop-zone não funcionava. Aqui a pasta é lida por
 * DataTransferItem.webkitGetAsEntry() (precisa ser chamado SINCRONAMENTE dentro do evento drop),
 * descida recursivamente, e cada arquivo ganha a propriedade __caminho ("RIF_X/1_Comunicacoes.csv")
 * no papel do webkitRelativePath. Sem pastas (ou navegador sem suporte às APIs de filesystem do
 * drop), resolve com a própria lista de dataTransfer.files, como antes.
 *
 * Evita também a navegação nativa do navegador quando o arquivo/pasta é solto FORA de qualquer
 * drop-zone — ela recarregaria a página e jogaria fora o preenchimento do formulário.
 *
 * Expõe window.PastaArraste. Carregar após jquery, antes dos scripts de página. */
(function () {
  "use strict";

  function paraLista(files) {
    var lista = [];
    for (var i = 0; files && i < files.length; i++) lista.push(files[i]);
    return lista;
  }

  // Desce uma entrada (arquivo ou pasta) do filesystem do drop, acumlando os arquivos em `sobra`.
  // `base` é o caminho dos pais com a barra final ("", "RIF_X/", "RIF_X/anexo/").
  function coletarEntrada(entry, base, sobra) {
    var dfd = $.Deferred();
    if (entry.isFile) {
      entry.file(function (arquivo) {
        try { arquivo.__caminho = base + entry.name; } catch (e) {}
        sobra.push(arquivo);
        dfd.resolve();
      }, function () { dfd.resolve(); }); // entrada de arquivo que não abriu: ignora
      return dfd.promise();
    }
    if (!entry.isDirectory) {
      dfd.resolve();
      return dfd.promise();
    }
    var leitor = entry.createReader();
    var promessas = [];
    // readEntries devolve de 100 em 100 entradas: tem que chamar até voltar vazio
    function lerLote() {
      leitor.readEntries(function (lote) {
        if (!lote.length) {
          $.when.apply($, promessas).always(function () { dfd.resolve(); });
          return;
        }
        for (var i = 0; i < lote.length; i++) {
          promessas.push(coletarEntrada(lote[i], base + entry.name + "/", sobra));
        }
        lerLote();
      }, function () {
        $.when.apply($, promessas).always(function () { dfd.resolve(); });
      });
    }
    lerLote();
    return dfd.promise();
  }

  function coletar(dataTransfer) {
    var dfd = $.Deferred();
    var itens = dataTransfer && dataTransfer.items;
    var entradas = [];
    var podeEntradas = itens && itens.length &&
      typeof itens[0].webkitGetAsEntry === "function";
    if (podeEntradas) {
      for (var i = 0; i < itens.length; i++) {
        if (itens[i].kind !== "file") continue;
        var entrada = null;
        try { entrada = itens[i].webkitGetAsEntry(); } catch (e) {}
        if (!entrada) { entradas = null; break; }
        entradas.push(entrada);
      }
    }
    if (!entradas || !entradas.length) {
      dfd.resolve(paraLista(dataTransfer && dataTransfer.files));
      return dfd.promise();
    }
    var achados = [];
    var promessas = [];
    for (var j = 0; j < entradas.length; j++) promessas.push(coletarEntrada(entradas[j], "", achados));
    $.when.apply($, promessas).always(function () {
      dfd.resolve(achados.length ? achados : paraLista(dataTransfer.files));
    });
    return dfd.promise();
  }

  function temArquivos(e) {
    var tipos = e.originalEvent && e.originalEvent.dataTransfer && e.originalEvent.dataTransfer.types;
    if (!tipos) return false;
    for (var i = 0; i < tipos.length; i++) {
      if (tipos[i] === "Files") return true;
    }
    return false;
  }

  // Soltar arquivos/pastas fora de uma drop-zone faz o navegador ABRIR o arquivo e descartar a
  // página (perde formulário preenchido e uploads em andamento). Só bloqueia arraste de arquivos:
  // seleção de texto continua arrastável para inputs e textareas.
  function evitarNavegacao() {
    $(document).on("dragover.pasta-arraste", function (e) {
      if (temArquivos(e)) e.preventDefault();
    });
    $(document).on("drop.pasta-arraste", function (e) {
      if (temArquivos(e)) e.preventDefault();
    });
  }

  window.PastaArraste = { coletar: coletar, evitarNavegacao: evitarNavegacao };
})();
