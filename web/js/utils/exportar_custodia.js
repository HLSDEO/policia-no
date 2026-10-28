/* Ficha de custódia — window.ExportarCustodia.

 * Gera, no navegador, um .zip com o pacote de custódia de uma evidência:
 *   - FICHA_DE_CUSTODIA.txt : leitura humana (inquérito, evidência, hashes, quem exportou e quando)
 *   - metadados.json        : os metadados crus da evidência, para conferência automática
 *   - <lista>.csv           : a lista de registros, quando a tela tiver uma (opcional)
 *
 * Reaproveita o ZIP "store" e o download por blob do js/exportar_excel.js (ExportarExcel.
 * montarZip/baixarBlob/gerarCsv) — o formato .xlsx de lá e o .csv daqui saem do mesmo ZIP
 * pronto para carregar numa ferramenta de perícia, sem nenhuma dependência externa.
 *
 *   ExportarCustodia.instalar({
 *     alvo: "#btn-custodia",
 *     nome: "custodia_rif_12",
 *     meta: function () { return { "Inquérito": "2024.0001", "Hash SHA-256": "abc…", ... }; },
 *     lista: function () { return { colunas: [...], linhas: [[...]] }; }   // opcional
 *   });
 *
 * `meta`/`lista` são funções para só calcular no clique (metadados pesados não pagam o preço
 * em toda renderização do card). Lista ausente gera só a ficha + o JSON. */
(function () {
  "use strict";

  var usuarioAtual = null;
  // js/auth.js dispara o evento quando confirma quem entrou; a ficha registra quem exportou.
  $(document).on("analyser:usuario.custodia", function (e, u) { usuarioAtual = u || null; });

  function carimbo() {
    var d = new Date();
    return d.toLocaleString("pt-BR");
  }

  function nomeCarimbo(base) {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    return (base || "custodia") + "-" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) +
      "-" + p(d.getHours()) + p(d.getMinutes()) + ".zip";
  }

  function fichaTexto(meta, listaQt) {
    var linhas = [];
    linhas.push("FICHA DE CUSTÓDIA DE EVIDÊNCIA DIGITAL");
    linhas.push("=====================================");
    linhas.push("");
    Object.keys(meta || {}).forEach(function (chave) {
      var valor = meta[chave];
      linhas.push(chave + ": " + (valor === null || valor === undefined || valor === "" ? "—" : valor));
    });
    linhas.push("");
    linhas.push("Registros incluídos no pacote: " + (listaQt != null ? listaQt : "lista não incluída"));
    linhas.push("");
    linhas.push("--- Exportação ---");
    linhas.push("Exportado por: " + (usuarioAtual ? (usuarioAtual.no_usuario || usuarioAtual.no_login) : "não identificado"));
    linhas.push("Login: " + (usuarioAtual ? (usuarioAtual.no_login || "—") : "—"));
    linhas.push("Data/hora da exportação: " + carimbo());
    linhas.push("");
    linhas.push("Os hashes acima identificam o arquivo original. Qualquer alteração no material");
    linhas.push("resulta em hashes diferentes — confira o SHA-256 ao reabrir este pacote.");
    return new TextEncoder().encode(linhas.join("\r\n"));
  }

  function gerar(opcoes) {
    var Excel = window.ExportarExcel;
    if (!Excel) throw new Error("js/utils/exportar_excel.js precisa carregar antes.");

    var meta = (typeof opcoes.meta === "function" ? opcoes.meta() : opcoes.meta) || {};
    var lista = typeof opcoes.lista === "function" ? opcoes.lista() : opcoes.lista;

    var arquivos = [
      { nome: "FICHA_DE_CUSTODIA.txt", dados: fichaTexto(meta, lista ? (lista.linhas || []).length : null) },
      { nome: "metadados.json", dados: new TextEncoder().encode(JSON.stringify(meta, null, 2)) },
    ];
    if (lista && lista.colunas && lista.colunas.length) {
      arquivos.push({
        nome: (opcoes.nomeLista || "registros") + ".csv",
        dados: Excel.gerarCsv(lista.colunas, lista.linhas || []),
      });
    }
    return Excel.montarZip(arquivos);
  }

  function instalar(opcoes) {
    opcoes = opcoes || {};
    var $alvo = opcoes.alvo ? $(opcoes.alvo) : $();
    if (!$alvo.length) return null;
    $alvo.on("click", function () {
      try {
        ExportarExcel.baixarBlob(gerar(opcoes), nomeCarimbo(opcoes.nome));
      } catch (e) {
        if (window.Toast) Toast.erro("Não foi possível gerar a ficha de custódia.");
      }
    });
    return $alvo;
  }

  window.ExportarCustodia = { instalar: instalar, gerar: gerar, nomeCarimbo: nomeCarimbo };
})();
