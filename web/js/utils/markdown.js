/* Renderizador de Markdown — window.Markdown. Usado pela tela de Documentação de código
 * (js/documentacao.js) para exibir os .md da pasta docs/ do projeto.
 *
 * Por que não reaproveitar o markdown do chat (js/ia.js): aquele é um markdown *mínimo*,
 * desenhado para a resposta de um modelo — todo título vira o mesmo `.chat-md-titulo` (sem
 * nível), lista aninhada vira lista plana, citação e linha horizontal não existem. Isso basta
 * para uma bolha de conversa e não basta para um documento de 500 linhas com hierarquia de
 * seções, tabelas e listas dentro de citação. Aqui os níveis de título importam: são eles que
 * viram o sumário lateral ("Nesta página") e as âncoras dos links entre documentos.
 *
 *   var doc = Markdown.renderizar(texto, { linkDoc: function (nome, ancora) { ... } });
 *   doc.html     -> HTML pronto para .html()
 *   doc.titulos  -> [{ nivel: 2, texto: "Como funciona", id: "como-funciona" }, …]
 *
 * `linkDoc` é opcional e traduz um link para outro arquivo .md no href que a tela quer usar
 * (a tela de documentação aponta para si mesma com ?doc=). Sem ele, o link fica como está.
 *
 * Segurança: TODO o texto é escapado antes de qualquer marcação virar tag, e href só aceita
 * http(s), âncora e caminho relativo — nada de `javascript:`. Os .md são arquivos do próprio
 * repositório (conteúdo confiável), mas o renderizador é genérico e não deve depender disso.
 *
 * Suporta o que a pasta docs/ realmente usa: títulos (# a ######), parágrafo, ênfase, código
 * inline e em bloco (```), listas com e sem número e aninhadas, citação (>), tabela com
 * cabeçalho e linha horizontal. Fora do escopo (não aparece nos documentos): imagem, HTML
 * cru, nota de rodapé, lista de tarefas. */
(function () {
  "use strict";

  // O caractere NUL nunca aparece num .md — serve de marcador dos trechos de código já prontos,
  // que atravessam o escape e as substituições de ênfase sem serem tocados (senão `**` dentro de
  // um trecho de código viraria negrito).
  var MARCA = "\u0000";

  function escapar(texto) {
    return String(texto == null ? "" : texto)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ---------- inline ----------

  function hrefSeguro(url) {
    var limpa = String(url || "").trim();
    if (/^(https?:)?\/\//i.test(limpa)) return limpa;   // absoluto
    if (/^[#?]/.test(limpa)) return limpa;              // âncora/query na própria página
    if (/^[a-z][a-z0-9+.-]*:/i.test(limpa)) return "";  // qualquer outro esquema (javascript:, data:…)
    return limpa;                                        // caminho relativo
  }

  function montarLink(rotulo, url, opcoes) {
    var partes = String(url).split("#");
    var alvo = partes[0];
    var ancora = partes.length > 1 ? partes.slice(1).join("#") : "";

    // link para outro documento da pasta: quem decide o href é a tela
    if (/\.md$/i.test(alvo) && opcoes.linkDoc) {
      var nome = alvo.split("/").pop();
      return '<a href="' + escapar(opcoes.linkDoc(nome, ancora)) + '" class="md-link-doc"' +
        ' data-doc="' + escapar(nome) + '" data-ancora="' + escapar(ancora) + '">' + rotulo + "</a>";
    }

    var href = hrefSeguro(url);
    if (!href) return rotulo;
    var externo = /^(https?:)?\/\//i.test(href);
    return '<a href="' + escapar(href) + '"' +
      (externo ? ' target="_blank" rel="noopener"' : "") + ">" + rotulo + "</a>";
  }

  function inline(texto, opcoes) {
    var codigos = [];

    // 1. tira os trechos `código` da frente (guardados já escapados e prontos). A cerca pode ter
    // mais de uma crase — é assim que um documento mostra uma cerca de código dentro do texto
    // (```` ```json ```` em documentos_diversos.md); com uma crase só, o exemplo vazava cru.
    var s = String(texto == null ? "" : texto).replace(/(`+)([\s\S]+?)\1/g, function (_, cerca, codigo) {
      // uma crase de folga em cada ponta é separador, não conteúdo (``` `x` ``` -> `x`)
      var limpo = /^ [\s\S]*[^ ] $/.test(codigo) ? codigo.slice(1, -1) : codigo;
      codigos.push("<code>" + escapar(limpo) + "</code>");
      return MARCA + (codigos.length - 1) + MARCA;
    });

    // 2. escapa o resto — daqui pra frente só entra tag gerada por este arquivo
    s = escapar(s);

    // 3. links [rótulo](url) — o rótulo já está escapado, a url ainda não passou por hrefSeguro
    s = s.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, function (_, rotulo, url) {
      return montarLink(rotulo, url.replace(/&amp;/g, "&"), opcoes);
    });

    // 4. ênfase. Só com asterisco: `_` é caractere de nome em quase todo identificador citado
    // nos documentos (nm_arquivo, _processar_extracao) e tratá-lo como itálico transformaria
    // nome de função em texto torto.
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // 5. devolve os trechos de código ao lugar
    return s.replace(new RegExp(MARCA + "(\\d+)" + MARCA, "g"), function (_, i) {
      return codigos[Number(i)];
    });
  }

  // ---------- âncoras dos títulos ----------

  function gerarId(texto, usados) {
    // sem acento e sem pontuação: a âncora vai para a URL (#como-funciona) e para o sumário
    var base = String(texto).normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    base = base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "secao";
    var id = base;
    var n = 2;
    while (usados[id]) { id = base + "-" + n; n++; }
    usados[id] = true;
    return id;
  }

  // ---------- blocos ----------

  var RE_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  var RE_TITULO = /^(#{1,6})\s+(.*)$/;
  var RE_LINHA_TABELA = /^\s*\|.*\|\s*$/;
  var RE_SEPARADOR_TABELA = /^\s*\|[\s|:-]+\|\s*$/;
  var RE_REGUA = /^\s*([-*_])(\s*\1){2,}\s*$/;

  function celulas(linha) {
    return linha.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) {
      return c.trim();
    });
  }

  function dedent(linhas) {
    var minimo = null;
    linhas.forEach(function (l) {
      if (!l.trim()) return;
      var espacos = l.match(/^\s*/)[0].length;
      if (minimo === null || espacos < minimo) minimo = espacos;
    });
    if (!minimo) return linhas;
    return linhas.map(function (l) { return l.slice(minimo); });
  }

  /* Converte uma sequência de linhas em HTML. Chamada recursivamente para o conteúdo de item de
   * lista e de citação — é o que faz "lista dentro de citação" e "parágrafo dentro de item"
   * funcionarem sem caso especial. */
  function blocos(linhas, ctx) {
    var html = "";
    var i = 0;

    function paragrafo(buffer) {
      if (!buffer.length) return;
      // as linhas de um parágrafo são quebradas no meio da frase nos .md do projeto (limite de
      // coluna), então juntar com espaço é o que reconstrói o texto original
      html += "<p>" + inline(buffer.join(" ").trim(), ctx.opcoes) + "</p>";
    }

    var buffer = [];

    while (i < linhas.length) {
      var linha = linhas[i];

      // bloco de código ```
      var abertura = linha.match(/^\s*```+\s*([\w+-]*)\s*$/);
      if (abertura) {
        paragrafo(buffer); buffer = [];
        var corpo = [];
        i++;
        while (i < linhas.length && !/^\s*```+\s*$/.test(linhas[i])) { corpo.push(linhas[i]); i++; }
        i++; // fecha a cerca
        var lang = abertura[1] ? ' data-lang="' + escapar(abertura[1]) + '"' : "";
        html += "<pre" + lang + "><code>" + escapar(dedent(corpo).join("\n")) + "</code></pre>";
        continue;
      }

      // título
      var titulo = linha.match(RE_TITULO);
      if (titulo) {
        paragrafo(buffer); buffer = [];
        var nivel = titulo[1].length;
        var textoTitulo = titulo[2].replace(/\s*#+\s*$/, "").trim();
        var id = gerarId(textoTitulo, ctx.ids);
        ctx.titulos.push({ nivel: nivel, texto: textoTitulo.replace(/[*`]/g, ""), id: id });
        html += "<h" + nivel + ' id="' + id + '" class="md-h">' +
          inline(textoTitulo, ctx.opcoes) +
          '<a class="md-ancora" href="#' + id + '" aria-label="Link para esta seção">#</a>' +
          "</h" + nivel + ">";
        i++;
        continue;
      }

      // linha horizontal (antes da lista: "---" também casa com item de lista "-")
      if (RE_REGUA.test(linha)) {
        paragrafo(buffer); buffer = [];
        html += "<hr>";
        i++;
        continue;
      }

      // tabela
      if (RE_LINHA_TABELA.test(linha)) {
        paragrafo(buffer); buffer = [];
        var linhasTabela = [];
        while (i < linhas.length && RE_LINHA_TABELA.test(linhas[i])) { linhasTabela.push(linhas[i]); i++; }
        html += montarTabela(linhasTabela, ctx);
        continue;
      }

      // citação
      if (/^\s*>\s?/.test(linha)) {
        paragrafo(buffer); buffer = [];
        var citacao = [];
        while (i < linhas.length && (/^\s*>\s?/.test(linhas[i]) || (citacao.length && linhas[i].trim() && !linhas[i].trim().startsWith(">")))) {
          if (!/^\s*>/.test(linhas[i])) break;   // parágrafo solto depois da citação encerra o bloco
          citacao.push(linhas[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        html += "<blockquote>" + blocos(citacao, ctx) + "</blockquote>";
        continue;
      }

      // lista
      if (RE_ITEM.test(linha)) {
        paragrafo(buffer); buffer = [];
        var bloco = [];
        var recuoBase = linha.match(/^\s*/)[0].length;
        while (i < linhas.length) {
          var atual = linhas[i];
          if (RE_ITEM.test(atual) && atual.match(/^\s*/)[0].length >= recuoBase) { bloco.push(atual); i++; continue; }
          // continuação (linha recuada) ou linha em branco no meio da lista
          if (!atual.trim()) {
            var proxima = linhas[i + 1] || "";
            if (proxima.trim() && (RE_ITEM.test(proxima) || proxima.match(/^\s*/)[0].length > recuoBase)) {
              bloco.push(atual); i++; continue;
            }
            break;
          }
          if (atual.match(/^\s*/)[0].length > recuoBase) { bloco.push(atual); i++; continue; }
          break;
        }
        html += montarLista(bloco, ctx);
        continue;
      }

      if (!linha.trim()) {
        paragrafo(buffer); buffer = [];
        i++;
        continue;
      }

      buffer.push(linha);
      i++;
    }

    paragrafo(buffer);
    return html;
  }

  function montarTabela(linhasTabela, ctx) {
    var comCabecalho = linhasTabela.length > 1 && RE_SEPARADOR_TABELA.test(linhasTabela[1]);
    var html = '<div class="md-tabela-wrap"><table class="md-tabela">';
    linhasTabela.forEach(function (linha, idx) {
      if (RE_SEPARADOR_TABELA.test(linha)) return;
      var tag = comCabecalho && idx === 0 ? "th" : "td";
      html += "<tr>" + celulas(linha).map(function (c) {
        return "<" + tag + ">" + inline(c, ctx.opcoes) + "</" + tag + ">";
      }).join("") + "</tr>";
    });
    return html + "</table></div>";
  }

  /* Um bloco de lista já isolado por `blocos`: agrupa cada item com as suas linhas de
   * continuação (e sublistas, que são só linhas mais recuadas) e recorre no conteúdo. */
  function montarLista(bloco, ctx) {
    var primeiro = bloco[0].match(RE_ITEM);
    var recuo = primeiro[1].length;
    var numerada = /\d/.test(primeiro[2]);
    var itens = [];
    var atual = null;

    bloco.forEach(function (linha) {
      var item = linha.match(RE_ITEM);
      if (item && item[1].length <= recuo) {
        if (atual) itens.push(atual);
        atual = [item[3]];
        return;
      }
      if (!atual) { atual = [linha]; return; }
      atual.push(linha);
    });
    if (atual) itens.push(atual);

    var tag = numerada ? "ol" : "ul";
    var html = "<" + tag + ' class="md-lista">';
    itens.forEach(function (linhasItem) {
      var conteudo = [linhasItem[0]].concat(dedent(linhasItem.slice(1)));
      var interno = blocos(conteudo, ctx);
      // item de uma linha não precisa do <p> em volta — vira um bloco alto demais na lista
      interno = interno.replace(/^<p>([\s\S]*)<\/p>$/, function (todo, dentro) {
        return dentro.indexOf("<p>") === -1 ? dentro : todo;
      });
      html += "<li>" + interno + "</li>";
    });
    return html + "</" + tag + ">";
  }

  // ---------- API ----------

  function renderizar(texto, opcoes) {
    var ctx = { titulos: [], ids: {}, opcoes: opcoes || {} };
    // o BOM de um .md salvo no Windows gruda no primeiro `#` e o título vira parágrafo
    var linhas = String(texto == null ? "" : texto)
      .replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").split("\n");
    var html = blocos(linhas, ctx);
    return { html: html, titulos: ctx.titulos };
  }

  window.Markdown = { renderizar: renderizar, escapar: escapar, gerarId: gerarId };
})();
