/* Exportação de um grafo SVG para PNG — window.ExportarPNG. Usado pela mesa de inquérito, pelo
 * grafo do RIF e pelo grafo do extrato do SIMBA (as três telas que desenham grafo).
 *
 * Por que existe: os grafos já exportavam para o i2 (CSV/XLSX/ANX), que é o formato de quem vai
 * CONTINUAR trabalhando no desenho. Faltava o caso mais comum do dia a dia — colar a figura num
 * relatório, num despacho ou numa apresentação. Print de tela não serve: recorta o que passou da
 * moldura, sai na resolução do monitor e perde a legenda, que é o que explica as cores.
 *
 * O que a imagem leva: o grafo INTEIRO (o enquadramento é refeito pelo bounding box do conteúdo,
 * não pelo zoom/pan em que o usuário parou), com título, subtítulo e a legenda desenhada por
 * baixo, no fundo do tema atual — o mesmo fundo que o halo dos rótulos usa, senão o contorno
 * deles aparece como um borrão claro em cima de um PNG escuro.
 *
 * Como funciona (e as três armadilhas do caminho):
 *   1. O SVG da tela é clonado e recebe o estilo COMPUTADO de cada elemento como style inline.
 *      Sem isso a imagem sai preta e sem forma: o clone é rasterizado fora do documento, onde
 *      nenhuma folha de estilo da página se aplica e todo var(--cor) fica sem valor.
 *   2. O conteúdo do <symbol> (os ícones dos nós) fica de fora dessa cópia de propósito — ele
 *      herda cor e traço do <use> que o chama, e carimbar estilo ali congelaria o ícone no preto
 *      do padrão, sumindo dentro do nó.
 *   3. Só entra no arquivo a propriedade que DIFERE do valor inicial. Num grafo de mil nós,
 *      gravar "fill:rgb(0, 0, 0)" em cada linha inflaria o SVG (e a data URL) em megabytes sem
 *      mudar um pixel.
 *
 * Uso:
 *   ExportarPNG.doGrafo(document.getElementById("mesa-grafo"), {
 *     prefixo: "mesa-inquerito",
 *     titulo: "Mesa de Inquérito",
 *     subtitulo: ["142 nós · 310 ligações", "exportado em 24/08/2026 15:04"],
 *     legendas: ["#mesa-legenda", "#mesa-legenda-relacoes"],
 *     antes: function () { ...estado temporário...; return function () { ...desfaz... }; }
 *   });
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var XLINK_NS = "http://www.w3.org/1999/xlink";

  // Propriedades que o desenho usa, com o valor inicial de cada uma (ver armadilha 3 acima).
  // Padrão null = comparar com o que a raiz do SVG computa: é o caso da fonte, que só o <text>
  // muda e que precisa estar na raiz para o texto não sair em serifada no rasterizador.
  var PROPS = [
    ["fill", "rgb(0, 0, 0)"],
    ["fill-opacity", "1"],
    ["stroke", "none"],
    ["stroke-width", "1px"],
    ["stroke-opacity", "1"],
    ["stroke-dasharray", "none"],
    ["stroke-linecap", "butt"],
    ["stroke-linejoin", "miter"],
    ["opacity", "1"],
    ["paint-order", "normal"],
    ["text-anchor", "start"],
    ["dominant-baseline", "auto"],
    ["font-size", null],
    ["font-family", null],
    ["font-weight", "400"],
    ["font-style", "normal"],
    ["letter-spacing", "normal"],
    ["visibility", "visible"]
  ];

  var MARGEM = 28;        // moldura em volta de tudo
  var LARGURA_MIN = 720;  // grafo estreito não pode espremer título e legenda
  var ALT_LINHA_LEG = 22;
  var GAP_ITEM = 18;
  var LADO_MAX = 10000;   // teto do canvas: acima disso o navegador devolve imagem em branco

  function varCss(nome, alt) {
    var v = window.getComputedStyle(document.documentElement).getPropertyValue(nome);
    v = String(v || "").trim();
    return v || alt;
  }

  function transparente(cor) {
    return !cor || /^transparent$/i.test(cor) || /rgba\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(cor);
  }

  function avisar(txt) {
    if (window.Toast) window.Toast.aviso(txt);
    else if (window.Dialogo) window.Dialogo.alerta(txt);
    else alert(txt);
  }

  function nomeArquivo(prefixo) {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    var ts = d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes());
    return (prefixo || "grafo") + "-grafo-" + ts + ".png";
  }

  function baixar(blob, nome) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = nome;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // ------------------------------------------------------------- SVG autônomo

  function inlinearEstilos(orig, clone, raiz) {
    var tag = String(orig.nodeName || "").toLowerCase();
    if (tag === "symbol") return;   // ver armadilha 2 no cabeçalho
    if (tag !== "defs") {
      var cs = window.getComputedStyle(orig);
      if (cs) {
        for (var p = 0; p < PROPS.length; p++) {
          var nome = PROPS[p][0], padrao = PROPS[p][1];
          var v = cs.getPropertyValue(nome);
          if (!v) continue;
          if (padrao === null ? (raiz && v === raiz.getPropertyValue(nome)) : v === padrao) continue;
          clone.style.setProperty(nome, v);
        }
      }
    }
    // href sem prefixo é SVG 2; o xlink:href junto mantém a imagem legível em quem só lê SVG 1.1
    // (vale para o <use> dos ícones e para o <image> da foto que substitui o nó na mesa)
    if (tag === "use" || tag === "image") {
      var href = orig.getAttribute("href") || orig.getAttributeNS(XLINK_NS, "href");
      if (href) clone.setAttributeNS(XLINK_NS, "xlink:href", href);
    }
    var oc = orig.children, cc = clone.children;
    for (var i = 0; i < oc.length && i < cc.length; i++) inlinearEstilos(oc[i], cc[i], raiz);
  }

  function svgAutonomo(svg, caixa, fundo) {
    var raiz = window.getComputedStyle(svg);
    var clone = svg.cloneNode(true);
    inlinearEstilos(svg, clone, raiz);
    // a fonte tem de estar na raiz: o rasterizador não conhece o <body> da página e, sem ela,
    // todo rótulo sai na serifada padrão do renderizador
    clone.style.setProperty("font-family", raiz.getPropertyValue("font-family"));
    clone.style.setProperty("font-size", raiz.getPropertyValue("font-size"));
    clone.style.removeProperty("cursor");
    clone.removeAttribute("id");
    clone.setAttribute("xmlns", SVG_NS);
    clone.setAttribute("xmlns:xlink", XLINK_NS);
    clone.setAttribute("viewBox", caixa.x + " " + caixa.y + " " + caixa.w + " " + caixa.h);
    clone.setAttribute("width", caixa.w);
    clone.setAttribute("height", caixa.h);
    clone.setAttribute("preserveAspectRatio", "xMidYMid meet");

    if (fundo) {
      var r = document.createElementNS(SVG_NS, "rect");
      r.setAttribute("x", caixa.x); r.setAttribute("y", caixa.y);
      r.setAttribute("width", caixa.w); r.setAttribute("height", caixa.h);
      r.setAttribute("fill", fundo);
      clone.insertBefore(r, clone.firstChild);
    }
    return new XMLSerializer().serializeToString(clone);
  }

  /* Enquadramento pelo CONTEÚDO, não pelo viewBox: o usuário exporta depois de ter dado zoom e
     arrastado, e o que ele quer no relatório é o grafo inteiro, não o pedaço em que parou. */
  function caixaConteudo(svg, folga) {
    var b = null;
    try { b = svg.getBBox(); } catch (e) { b = null; }
    if (!b || !isFinite(b.width) || b.width <= 0 || b.height <= 0) {
      var vb = svg.viewBox && svg.viewBox.baseVal;
      if (vb && vb.width > 0) return { x: vb.x, y: vb.y, w: vb.width, h: vb.height };
      return null;
    }
    // getBBox ignora espessura de traço e ponta de seta; a folga cobre as duas
    folga = folga == null ? 26 : folga;
    return { x: b.x - folga, y: b.y - folga, w: b.width + 2 * folga, h: b.height + 2 * folga };
  }

  // ---------------------------------------------------------------- legenda

  /* A legenda mora no HTML, fora do SVG — e é ela que diz o que cada cor significa. Em vez de
     tentar embutir HTML no SVG (foreignObject não é rasterizado), lemos as pastilhas da tela e
     redesenhamos no canvas com a API 2D. Tipo desligado na legenda fica de fora: ele não está no
     desenho, e listá-lo seria apontar para uma cor que não aparece em lugar nenhum. */
  function coletarLegenda(alvos) {
    var itens = [];
    (alvos || []).forEach(function (alvo) {
      var cont = typeof alvo === "string" ? document.querySelector(alvo) : alvo;
      if (!cont) return;
      Array.prototype.forEach.call(cont.children, function (filho) {
        var texto = String(filho.textContent || "").replace(/\s+/g, " ").trim();
        if (!texto) return;
        if (filho.classList.contains("mesa-legenda-titulo")) {
          itens.push({ tipo: "titulo", texto: texto });
          return;
        }
        if (!filho.classList.contains("mesa-legenda-item")) return;
        if (filho.classList.contains("desligado")) return;
        itens.push({ tipo: "item", texto: texto, amostra: lerAmostra(filho.firstElementChild) });
      });
    });
    return itens;
  }

  function lerAmostra(el) {
    if (!el) return null;
    var proprio = el.classList.contains("mesa-legenda-bolinha") || el.classList.contains("mesa-legenda-linha");
    if (!proprio && String(el.textContent || "").trim() !== "") return null;   // é o rótulo, não a amostra
    var cs = window.getComputedStyle(el);
    var forma = el.classList.contains("mesa-legenda-linha") ? "linha" : "ponto";
    if ((parseFloat(cs.height) || 0) <= 4) forma = "linha";

    var cor = transparente(cs.backgroundColor) ? "" : cs.backgroundColor;
    if (!cor) {
      // legenda de linha tracejada: a cor vive dentro do repeating-linear-gradient
      var m = String(cs.backgroundImage || "").match(/rgba?\([^)]+\)|#[0-9a-fA-F]{3,8}/);
      if (m) { cor = m[0]; forma = "linha"; }
    }
    if (!cor) {
      var larguraBorda = parseFloat(cs.borderTopWidth) || 0;
      if (larguraBorda > 0 && !transparente(cs.borderTopColor)) {
        cor = cs.borderTopColor;
        // anel (o "titular" do grafo de fluxo) x traço solto ao lado do rótulo
        if (forma !== "linha") forma = "anel";
      }
    }
    return cor ? { cor: cor, forma: forma } : null;
  }

  function larguraAmostra(a) { return !a ? 0 : (a.forma === "linha" ? 22 : 14) + 6; }

  function fonteItem(it, familia) {
    return it.tipo === "titulo" ? "600 11px " + familia : "12px " + familia;
  }

  function larguraItem(ctx, it, familia) {
    ctx.font = fonteItem(it, familia);
    return larguraAmostra(it.amostra) + ctx.measureText(it.texto).width;
  }

  function quebrarLegenda(ctx, itens, largura, familia) {
    var linhas = [], atual = [], x = 0;
    itens.forEach(function (it) {
      var w = larguraItem(ctx, it, familia);
      if (atual.length && x + w > largura) { linhas.push(atual); atual = []; x = 0; }
      it._w = w;
      atual.push(it);
      x += w + GAP_ITEM;
    });
    if (atual.length) linhas.push(atual);
    return linhas;
  }

  function desenharLegenda(ctx, linhas, x0, y0, familia, cores) {
    linhas.forEach(function (linha, li) {
      var x = x0, y = y0 + li * ALT_LINHA_LEG + ALT_LINHA_LEG / 2;
      linha.forEach(function (it) {
        var a = it.amostra;
        if (a) {
          ctx.save();
          if (a.forma === "linha") {
            ctx.strokeStyle = a.cor; ctx.lineWidth = 3; ctx.lineCap = "round";
            ctx.beginPath(); ctx.moveTo(x + 1, y); ctx.lineTo(x + 21, y); ctx.stroke();
          } else if (a.forma === "anel") {
            ctx.strokeStyle = a.cor; ctx.lineWidth = 2.4;
            ctx.beginPath(); ctx.arc(x + 7, y, 4.5, 0, Math.PI * 2); ctx.stroke();
          } else {
            ctx.fillStyle = a.cor;
            ctx.beginPath(); ctx.arc(x + 7, y, 5, 0, Math.PI * 2); ctx.fill();
          }
          ctx.restore();
        }
        ctx.font = fonteItem(it, familia);
        ctx.fillStyle = it.tipo === "titulo" ? cores.fraca : cores.texto;
        ctx.textBaseline = "middle";
        ctx.fillText(it.texto, x + larguraAmostra(a), y);
        x += it._w + GAP_ITEM;
      });
    });
  }

  // ---------------------------------------------------------------- principal

  function tituloDaPagina() {
    var h = document.querySelector(".page-title");
    return h ? String(h.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function doGrafo(svg, opcoes) {
    opcoes = opcoes || {};
    if (typeof svg === "string") svg = document.querySelector(svg);
    if (!svg) return Promise.reject(new Error("SVG do grafo não encontrado"));

    var caixa, fundo, familia, cores, itens, titulo, subtitulos, svgTexto;
    var restaurar = null;
    try {
      // A tela pode estar num estado que não deve ir para o papel (na mesa os rótulos somem
      // quando o grafo está "de longe"). O gancho troca isso e devolve como desfazer — tudo
      // dentro do mesmo passo síncrono, antes de qualquer repintura.
      restaurar = typeof opcoes.antes === "function" ? opcoes.antes() : null;

      caixa = caixaConteudo(svg, opcoes.folga);
      if (!caixa) { avisar("Grafo vazio: não há o que exportar."); return Promise.resolve(false); }

      var csBody = window.getComputedStyle(document.body);
      familia = String(csBody.fontFamily || "sans-serif");
      var wrap = svg.parentElement;
      fundo = opcoes.fundo || (wrap ? window.getComputedStyle(wrap).backgroundColor : "");
      // o halo dos rótulos é pintado com --bg-page; outro fundo o transformaria num borrão
      if (transparente(fundo)) fundo = varCss("--bg-page", csBody.backgroundColor) || "#ffffff";
      cores = {
        texto: varCss("--text-primary", csBody.color || "#111"),
        fraca: varCss("--text-muted", "#888"),
        borda: varCss("--border", "#ccc")
      };

      itens = coletarLegenda(opcoes.legendas);
      titulo = opcoes.titulo != null ? opcoes.titulo : tituloDaPagina();
      subtitulos = (opcoes.subtitulo == null ? [] : [].concat(opcoes.subtitulo))
        .filter(function (s) { return String(s == null ? "" : s).trim(); });

      svgTexto = svgAutonomo(svg, caixa, fundo);
    } finally {
      if (restaurar) { try { restaurar(); } catch (e) { /* o estado da tela não derruba o download */ } }
    }

    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        try { resolve(montar(img)); } catch (e) { reject(e); }
      };
      img.onerror = function () {
        avisar("Não foi possível gerar a imagem do grafo.");
        reject(new Error("falha ao rasterizar o SVG"));
      };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgTexto);
    });

    function montar(img) {
      var medidor = document.createElement("canvas").getContext("2d");
      var larguraConteudo = Math.max(caixa.w, LARGURA_MIN);
      var linhasLeg = itens.length ? quebrarLegenda(medidor, itens, larguraConteudo, familia) : [];

      var topo = MARGEM;
      if (titulo) topo += 26;
      topo += subtitulos.length * 17;
      if (titulo || subtitulos.length) topo += 12;
      var alturaLeg = linhasLeg.length ? 16 + linhasLeg.length * ALT_LINHA_LEG : 0;

      var W = larguraConteudo + 2 * MARGEM;
      var H = topo + caixa.h + alturaLeg + MARGEM;
      // 2x para o texto não serrilhar quando ampliarem a figura no relatório; com teto, porque
      // canvas grande demais volta em branco
      var escala = Math.max(Math.min(opcoes.escala || 2, LADO_MAX / Math.max(W, H)), 0.25);

      var canvas = document.createElement("canvas");
      canvas.width = Math.round(W * escala);
      canvas.height = Math.round(H * escala);
      var ctx = canvas.getContext("2d");
      ctx.scale(escala, escala);
      ctx.fillStyle = fundo;
      ctx.fillRect(0, 0, W, H);

      var y = MARGEM;
      ctx.textBaseline = "top";
      if (titulo) {
        ctx.font = "600 18px " + familia;
        ctx.fillStyle = cores.texto;
        ctx.fillText(titulo, MARGEM, y);
        y += 26;
      }
      ctx.font = "12px " + familia;
      ctx.fillStyle = cores.fraca;
      subtitulos.forEach(function (s) {
        ctx.fillText(String(s), MARGEM, y + 2);
        y += 17;
      });

      ctx.drawImage(img, MARGEM + (larguraConteudo - caixa.w) / 2, topo, caixa.w, caixa.h);

      if (linhasLeg.length) {
        var yLeg = topo + caixa.h + 8;
        ctx.strokeStyle = cores.borda;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(MARGEM, yLeg + 0.5); ctx.lineTo(W - MARGEM, yLeg + 0.5); ctx.stroke();
        desenharLegenda(ctx, linhasLeg, MARGEM, yLeg + 8, familia, cores);
      }

      var nome = opcoes.nome || nomeArquivo(opcoes.prefixo);
      if (canvas.toBlob) {
        canvas.toBlob(function (blob) {
          if (!blob) { avisar("Não foi possível gerar o PNG."); return; }
          baixar(blob, nome);
          if (window.Toast) window.Toast.sucesso("PNG do grafo gerado.");
        }, "image/png");
      } else {
        var a = document.createElement("a");
        a.href = canvas.toDataURL("image/png");
        a.download = nome;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
      }
      return true;
    }
  }

  window.ExportarPNG = { doGrafo: doGrafo };
})();
