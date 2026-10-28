/* Worker do layout da Mesa de Inquérito: roda o núcleo O(n²) do Fruchterman-Reingold fora da
 * UI thread, pra clique/scroll/zoom não travarem enquanto um grafo grande está se organizando
 * (ver LIMITE_NOS_PADRAO em mesa_inquerito.js — mesmo com o corte, um "Mostrar todos" em
 * inquérito com milhares de canônicas passa por aqui). Sem import: o projeto não usa bundler,
 * então este arquivo é autocontido e carregado via `new Worker("js/mesa_layout_worker.js")`. */
(function () {
  "use strict";

  self.onmessage = function (evt) {
    var dados = evt.data;
    var nos = dados.nos;            // [{id, x, y, fixo}]
    var arestas = dados.arestas;    // [{a, b}]
    var largura = dados.largura, altura = dados.altura, margem = dados.margem;
    var iteracoesTotal = dados.iteracoes;
    var porLote = dados.porLote;
    // Repulsão extra e comprimento mínimo de aresta (ver FATOR_REPULSAO/DISTANCIA_MINIMA_ARESTA
    // em mesa_inquerito.js): o Fruchterman-Reingold puro encosta vizinho em vizinho, e é o nó
    // encostado que faz o desenho virar borrão. Com padrão neutro, pra o worker seguir válido
    // se for chamado sem esses campos.
    var repulsao = dados.repulsao || 1;
    var distanciaMinima = dados.distanciaMinima || 0;
    // Gravidade para o centro no lugar da parede da caixa. Antes, quem era empurrado pra fora
    // era grampeado na borda (Math.max(margem, ...)) e ficava EMPILHADO ali: com repulsão
    // maior, dezenas de nós no mesmo pixel da margem. A gravidade segura o desenho junto sem
    // criar esse depósito de nós — e ainda mantém componentes desconexos por perto, que era o
    // outro serviço que a caixa prestava.
    var gravidade = dados.gravidade || 0;
    // Coesão de comunidade: cada nó sente um puxão fraco (quadrático, como a atração da aresta)
    // para o centróide da própria comunidade. Fraco de propósito — quem decide vizinhança são
    // as arestas; a coesão só desempata, fazendo a comunidade convergir JUNTA em vez de ficar
    // entremeada pelo grafo inteiro (a cor da legenda passava a não formar mancha nenhuma).
    var coesao = dados.coesao || 0;
    var comunidades = dados.comunidades || null; // array paralelo a nos: nº da comunidade ou -1

    var indice = {};
    nos.forEach(function (n, i) { indice[n.id] = i; });

    var K = Math.sqrt((largura * altura) / Math.max(nos.length, 1)) * 0.9;
    var temperatura = largura / 10;
    var dispX = new Array(nos.length);
    var dispY = new Array(nos.length);
    var iteracao = 0;

    function posicoesAtuais() {
      var posicoes = {};
      nos.forEach(function (n) { posicoes[n.id] = { x: n.x, y: n.y }; });
      return posicoes;
    }

    function passo() {
      var fim = Math.min(iteracao + porLote, iteracoesTotal);
      while (iteracao < fim) {
        for (var k = 0; k < nos.length; k++) { dispX[k] = 0; dispY[k] = 0; }

        for (var i = 0; i < nos.length; i++) {
          for (var j = i + 1; j < nos.length; j++) {
            var a = nos[i], b = nos[j];
            var dx = a.x - b.x, dy = a.y - b.y;
            var d = Math.max(Math.hypot(dx, dy), 0.01);
            var f = (K * K * repulsao) / d;
            dispX[i] += (dx / d) * f; dispY[i] += (dy / d) * f;
            dispX[j] -= (dx / d) * f; dispY[j] -= (dy / d) * f;
          }
        }

        for (var e = 0; e < arestas.length; e++) {
          var ia = indice[arestas[e].a], ib = indice[arestas[e].b];
          if (ia == null || ib == null) continue;
          var na = nos[ia], nb = nos[ib];
          var edx = na.x - nb.x, edy = na.y - nb.y;
          var ed = Math.max(Math.hypot(edx, edy), 0.01);
          var util = Math.max(ed - distanciaMinima, 0);  // só puxa além da distância mínima
          var ef = (util * util) / K;
          dispX[ia] -= (edx / ed) * ef; dispY[ia] -= (edy / ed) * ef;
          dispX[ib] += (edx / ed) * ef; dispY[ib] += (edy / ed) * ef;
        }

        if (coesao > 0 && comunidades) {
          var cxCom = {}, cyCom = {}, qtCom = {};
          for (var ci = 0; ci < nos.length; ci++) {
            var cg = comunidades[ci];
            if (cg == null || cg < 0) continue;
            cxCom[cg] = (cxCom[cg] || 0) + nos[ci].x;
            cyCom[cg] = (cyCom[cg] || 0) + nos[ci].y;
            qtCom[cg] = (qtCom[cg] || 0) + 1;
          }
          for (var c2 = 0; c2 < nos.length; c2++) {
            var gc = comunidades[c2];
            if (gc == null || gc < 0 || qtCom[gc] < 2) continue;
            var mdx = cxCom[gc] / qtCom[gc] - nos[c2].x;
            var mdy = cyCom[gc] / qtCom[gc] - nos[c2].y;
            var md = Math.max(Math.hypot(mdx, mdy), 0.01);
            var mf = ((md * md) / K) * coesao;
            dispX[c2] += (mdx / md) * mf;
            dispY[c2] += (mdy / md) * mf;
          }
        }

        var centroX = largura / 2, centroY = altura / 2;
        for (var g = 0; g < nos.length; g++) {
          var ng = nos[g];
          var gdx = centroX - ng.x, gdy = centroY - ng.y;
          var gd = Math.max(Math.hypot(gdx, gdy), 0.01);
          var gf = ((gd * gd) / K) * gravidade;   // mesma forma da atração da aresta
          dispX[g] += (gdx / gd) * gf; dispY[g] += (gdy / gd) * gf;
        }

        for (var m = 0; m < nos.length; m++) {
          var n = nos[m];
          if (n.fixo) continue;
          var dd = Math.max(Math.hypot(dispX[m], dispY[m]), 0.01);
          var lim = Math.min(dd, temperatura);
          n.x += (dispX[m] / dd) * lim;
          n.y += (dispY[m] / dd) * lim;
          // rede de segurança bem folgada (a gravidade é quem segura de verdade): só existe
          // pra uma configuração patológica não mandar o nó pro infinito
          n.x = Math.max(-largura, Math.min(largura * 2, n.x));
          n.y = Math.max(-altura, Math.min(altura * 2, n.y));
        }

        temperatura *= 0.98;
        iteracao++;
      }

      if (iteracao < iteracoesTotal) {
        postMessage({ tipo: "progresso", posicoes: posicoesAtuais(), iteracao: iteracao, total: iteracoesTotal });
        setTimeout(passo, 0); // workers não têm requestAnimationFrame — setTimeout(0) faz o mesmo papel de "próximo tick"
      } else {
        postMessage({ tipo: "fim", posicoes: posicoesAtuais() });
      }
    }

    passo();
  };
})();
