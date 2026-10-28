/* Tabela paginada de extração — window.TabelaPaginada.
 *
 * Encapsula o padrão que estava copiado em ~20 pares HTML/JS de telas de extração (contatos,
 * ligações, notificações, agenda, acessos...): busca a página atual da API, desenha o
 * cabeçalho e as linhas, mostra total/estado vazio/estado de erro/skeleton de carregamento e
 * cuida da paginação (botões, teclado ←/→, estado na URL). Cada tela reimplementava isso do
 * zero — o mesmo `carregarTabela`/`totalPaginas`/`recarregarDoInicio` com pequenas variações.
 *
 * O que a tela continua dona: a barra de filtros (`.arq-filtros`, com a busca e os selects/datas
 * específicos daquela tela) e, quando existir, o resumo de KPIs/gráficos acima da tabela — isso
 * varia demais entre telas para valer a pena generalizar. TabelaPaginada só cuida do que é
 * idêntico em todas: da tabela pra baixo.
 *
 *   var tabela = TabelaPaginada.criar({
 *     alvo: "#tabela",                       // <div> vazio onde a tabela/paginação são injetadas
 *     url: "/api/investigacao/inqueritos/" + idInquerito + "/contatos",
 *     params: function () { return { extracao: idExtracao, busca: $busca.val().trim() }; },
 *     colunas: [
 *       { rotulo: "Nome", render: function (r) { return r.no_contato || "—"; } },
 *       { rotulo: "Entidade", exportavel: false, render: function (r) { return EntidadesUI.botao({...}); } }
 *     ],
 *     vazio: { titulo: "Nenhum contato encontrado." },
 *     erro: "Não foi possível carregar os contatos.",
 *     exportar: { nome: "contatos", linha: function (r) { return [r.no_contato || "", ...]; } }
 *   });
 *
 *   // filtro específico da tela (fora do domínio da TabelaPaginada):
 *   $filtroTipo.on("change", function () { tabela.recarregar(); });
 *
 * Opções:
 *   alvo            seletor ou elemento — container vazio (obrigatório)
 *   url             endpoint que devolve { itens, total } (obrigatório)
 *   params          function() -> object; filtros da tela, SEM pagina/limite (default: {})
 *   limite          registros por página (default 100)
 *   colunas         [{ rotulo, render(item) -> texto|jQuery|Node, exportavel: false?, ordenar? }] (obrigatório)
 *                   ordenar: { desc: "<chave>", asc: "<chave>", padrao: "asc"|"desc" } torna a
 *                   coluna clicável; as chaves são as que o endpoint aceita na whitelist dele
 *                   (ver _ORDENACOES_* em funcoes/investigacao_utils.py) e vão como ?ordenar=
 *   ordenarPadrao   chave que corresponde à ordem PADRÃO do endpoint (ex.: "recentes"). Serve
 *                   para a seta já aparecer na coluna certa no primeiro carregamento sem mandar
 *                   ?ordenar= à toa — só entra na query quando o usuário troca a ordem
 *   seletorOrdenar  seletor de um <select> de ordenação que a tela já tenha (localizações,
 *                   navegação): fica sincronizado nos dois sentidos com os cabeçalhos
 *   vazio           { titulo, dica } — repassado a Estados.vazio
 *   erro            mensagem de Estados.erro (default genérica)
 *   exportar        { nome, linha(item) -> [valores], colunas: [rótulos]? } — se ausente, não
 *                   instala botão de exportação; `colunas` default = rótulos das colunas
 *                   exportáveis (exportavel !== false)
 *   autoCarregar    false para não buscar a 1ª página sozinho (ex.: espera contagens de
 *                   entidade chegar primeiro) — default true
 *   aoRenderizar    function(itens, resp) — chamada após cada render com sucesso, só quando há
 *                   itens (ex.: telas que precisam inspecionar as linhas da página atual, como
 *                   localizacoes.js)
 *   aoContagem      function(total) — chamada sempre, assim que o total é conhecido (ex.: a
 *                   contagem da aba quando esta tabela é uma das abas da página, ver
 *                   js/utils/ufdr_dominio.js). Independente de aoRenderizar: não confundir os dois,
 *                   um recebe o total (número), o outro os itens (array) — ver ligacoes.js
 *   alvoTotal       seletor do contador (default "#total-registros")
 *   rotuloTotal     function(n) -> string (default "N registros")
 *
 * Instância devolvida:
 *   .carregar()     busca a página atual de novo (sem resetar)
 *   .recarregar()   volta pra página 1 e busca
 *   .params()       último objeto de filtros usado na requisição (sem pagina/limite)
 *   .ordenar()      chave de ordenação atual (null = ordem padrão do endpoint)
 *   .ordenar(chave) troca a ordenação e recarrega
 *
 * Ganhos embutidos, de graça em toda tela que usar: cabeçalho como <th scope="col"> (era <td>,
 * sem semântica de tabela para leitor de tela), skeleton em vez de tabela em branco durante o
 * carregamento, estado vazio distinguindo "sem dado" de "sem resultado do filtro" (Estados.vazio,
 * ver js/estados.js), estado de erro com "Tentar de novo", `role="status"` no contador (o total
 * é anunciado ao mudar), navegação por `←`/`→` quando o foco não está em campo de texto, e
 * filtros + página refletidos na URL (window.EstadoUrl, ver js/estado_url.js) — assim voltar de
 * um detalhe devolve a lista no mesmo ponto e a URL pode ser compartilhada entre investigadores. */
(function () {
  "use strict";

  function criar(opts) {
    opts = opts || {};
    var $alvo = $(opts.alvo);
    var limite = opts.limite || 100;
    var colunas = opts.colunas || [];
    var alvoTotal = opts.alvoTotal || "#total-registros";
    var $total = $(alvoTotal).attr("role", "status");
    var rotuloTotal = opts.rotuloTotal || function (n) { return n.toLocaleString("pt-BR") + " registros"; };

    var ordenarPadrao = opts.ordenarPadrao || null;
    var estado = { pagina: 1, total: 0, ordenar: ordenarPadrao };
    var ultimosParams = {};
    var $seletorOrdenar = opts.seletorOrdenar ? $(opts.seletorOrdenar) : $();

    // Deep-link vindo de outra tela (ex.: o link da evidência em busca_entidades): ?destacar=<id>
    // rola até a linha cujo `idDe` casa e a realça. Só vale para o carregamento em que aparece —
    // depois a URL volta a ser só o estado da lista (EstadoUrl.salvar remove a chave).
    var idDestacar = window.EstadoUrl ? EstadoUrl.ler(["destacar"]).destacar : null;
    var procurandoDestacado = false;   // varredura em segundo plano para achar a página do registro

    // ---------- ordenação por coluna ----------
    // A coluna declara as chaves que o endpoint aceita (whitelist no servidor — o valor cru nunca
    // entra no SQL; ver _ORDENACOES_* em funcoes/investigacao_utils.py):
    //
    //   { rotulo: "Data", ordenar: { desc: "recentes", asc: "antigos", padrao: "desc" } }
    //
    // Só `ordenar` já torna a coluna clicável. Uma direção sozinha também vale (ex.: "mais
    // visitadas" só faz sentido decrescente) — nesse caso o clique alterna entre ordenar por ela
    // e voltar à ordem padrão do endpoint.

    function chaveDe(coluna, direcao) {
      return coluna.ordenar ? coluna.ordenar[direcao] || null : null;
    }

    // Qual (coluna, direção) corresponde à chave ativa — é o que decide a seta e o aria-sort.
    function ativoDaChave(chave) {
      if (!chave) return null;
      for (var i = 0; i < colunas.length; i++) {
        if (chaveDe(colunas[i], "asc") === chave) return { indice: i, direcao: "asc" };
        if (chaveDe(colunas[i], "desc") === chave) return { indice: i, direcao: "desc" };
      }
      return null;
    }

    function aoClicarCabecalho(indice) {
      var coluna = colunas[indice];
      var ativo = ativoDaChave(estado.ordenar);
      var proxima;
      if (ativo && ativo.indice === indice) {
        // mesma coluna: inverte. Se a coluna só tem uma direção declarada, chaveDe devolve null
        // e definirOrdenar cai em ordenarPadrao — o 2º clique desfaz a ordenação em vez de não
        // fazer nada (ex.: "Visitas", que só ordena decrescente)
        proxima = chaveDe(coluna, ativo.direcao === "asc" ? "desc" : "asc");
      } else {
        var padrao = (coluna.ordenar && coluna.ordenar.padrao) || "asc";
        proxima = chaveDe(coluna, padrao) || chaveDe(coluna, padrao === "asc" ? "desc" : "asc");
      }
      definirOrdenar(proxima);
    }

    function definirOrdenar(chave) {
      estado.ordenar = chave || ordenarPadrao;
      if ($seletorOrdenar.length) $seletorOrdenar.val(estado.ordenar || $seletorOrdenar.find("option").first().val());
      atualizarCabecalhos();
      recarregar();
    }

    function atualizarCabecalhos() {
      var ativo = ativoDaChave(estado.ordenar);
      $trHead.find("th").each(function (i) {
        var $th = $(this);
        var $btn = $th.find(".tp-ordenar");
        if (!$btn.length) return;
        var eAtivo = !!ativo && ativo.indice === i;
        // aria-sort no <th> é o que o leitor de tela anuncia; a seta é só o reforço visual
        $th.attr("aria-sort", eAtivo ? (ativo.direcao === "asc" ? "ascending" : "descending") : "none");
        $btn.toggleClass("tp-ordenar-ativo", eAtivo);
        $btn.find(".tp-seta").text(eAtivo ? (ativo.direcao === "asc" ? "▲" : "▼") : "↕");
      });
    }

    // ---------- estrutura fixa, montada uma vez ----------

    var $wrap = $("<div>").addClass("tp-wrap").css("overflow-x", "auto");
    var $tabela = $("<table>").addClass("grupo-tabela").appendTo($wrap);
    var $thead = $("<thead>").appendTo($tabela);
    var $trHead = $("<tr>").appendTo($thead);
    colunas.forEach(function (c, i) {
      var $th = $("<th>").attr("scope", "col").appendTo($trHead);
      if (!c.ordenar) {
        $th.text(c.rotulo);
        return;
      }
      // <button> dentro do <th> (e não onclick no <th>) para a coluna ser alcançável por Tab e
      // acionável por Enter/Espaço sem nenhum handler de teclado extra
      $("<button>").addClass("tp-ordenar").attr("type", "button")
        .attr("title", "Ordenar por " + c.rotulo)
        .append($("<span>").text(c.rotulo))
        .append($("<span>").addClass("tp-seta").attr("aria-hidden", "true").text("↕"))
        .on("click", function () { aoClicarCabecalho(i); })
        .appendTo($th);
      $th.attr("aria-sort", "none");
    });
    var $tbody = $("<tbody>").appendTo($tabela);

    var $estadoBloco = $("<div>").addClass("tp-estado hidden");

    var $paginacao = $("<div>").addClass("arq-paginacao hidden");
    var $pagAnterior = $("<button>").addClass("btn").attr("type", "button").text("◀ Anterior").appendTo($paginacao);
    var $pagInfo = $("<span>").addClass("arq-pag-info").appendTo($paginacao);
    var $pagProximo = $("<button>").addClass("btn").attr("type", "button").text("Próximo ▶").appendTo($paginacao);

    $alvo.empty().append($wrap).append($estadoBloco).append($paginacao);

    // ---------- render ----------

    function totalPaginas() {
      return Math.max(1, Math.ceil(estado.total / limite));
    }

    function celula(item, coluna) {
      var $td = $("<td>");
      var valor = coluna.render(item);
      if (valor && valor.jquery) {
        $td.append(valor);
      } else if (valor instanceof Node) {
        $td.append(valor);
      } else {
        $td.text(valor === null || valor === undefined || valor === "" ? "—" : valor);
      }
      return $td;
    }

    function atualizarPaginacao() {
      var tp = totalPaginas();
      if (tp <= 1) {
        $paginacao.addClass("hidden");
        return;
      }
      $pagInfo.text("Página " + estado.pagina + " de " + tp.toLocaleString("pt-BR"));
      $pagAnterior.prop("disabled", estado.pagina <= 1);
      $pagProximo.prop("disabled", estado.pagina >= tp);
      $paginacao.removeClass("hidden");
    }

    function temFiltroAtivo() {
      // qualquer filtro além de extracao/pagina/limite conta como filtro ativo pra distinguir
      // "sem dado" de "sem resultado deste filtro" no estado vazio. `ordenar` fica de fora: muda
      // a ordem, não o conjunto — contá-lo faria a tela sem nenhum dado dizer "nenhum resultado
      // para este filtro" só porque o investigador clicou num cabeçalho.
      return Object.keys(ultimosParams).some(function (k) {
        return k !== "extracao" && k !== "ordenar" && ultimosParams[k] !== "" && ultimosParams[k] != null;
      });
    }

    function renderizar(resp) {
      estado.total = resp.total || 0;
      $total.text(rotuloTotal(estado.total));
      // total isolado, para quem só precisa dele (ex.: a contagem da aba — ver
      // js/utils/ufdr_dominio.js). aoRenderizar(itens, resp), mais abaixo, é outra callback —
      // confundir as duas quebrou localizacoes.js (itens.filter em cima de um número)
      if (opts.aoContagem) opts.aoContagem(estado.total);

      if (!resp.itens || !resp.itens.length) {
        $wrap.addClass("hidden");
        $paginacao.addClass("hidden");
        Estados.vazio($estadoBloco.removeClass("hidden"), $.extend({
          filtrado: temFiltroAtivo(),
        }, opts.vazio || {}));
        return;
      }

      Estados.limpar($estadoBloco);
      $estadoBloco.addClass("hidden");
      $wrap.removeClass("hidden");

      $tbody.empty();
      resp.itens.forEach(function (item) {
        var $tr = $("<tr>");
        if (opts.idDe) $tr.attr("data-id", String(opts.idDe(item)));
        colunas.forEach(function (c) { $tr.append(celula(item, c)); });
        $tbody.append($tr);
      });

      destacarLinha();
      if (idDestacar) localizarDestacado();

      atualizarPaginacao();

      if (opts.aoRenderizar) opts.aoRenderizar(resp.itens, resp);
    }

    function destacarLinha() {
      if (!idDestacar || !opts.idDe) return;
      var $linha = $tbody.find('tr[data-id="' + idDestacar + '"]');
      if (!$linha.length) return;
      $linha.addClass("tp-destacada");
      $linha[0].scrollIntoView({ block: "center" });
      idDestacar = null;
      if (window.EstadoUrl) window.EstadoUrl.salvar({ destacar: null });
    }

    /* O registro achado pode não estar na primeira página (a lista ordena por data desc.) — a
     * varredura percorre as páginas em segundo plano até achar a linha, então renderiza a página
     * certa e o destacarLinha() da carga rola até ela. Teto de páginas para nunca varrer tudo. */
    function localizarDestacado() {
      if (!idDestacar || !opts.idDe || procurandoDestacado) return;
      procurandoDestacado = true;
      var base = $.extend({}, opts.params ? opts.params() : {});
      if (estado.ordenar && estado.ordenar !== ordenarPadrao) base.ordenar = estado.ordenar;
      var pagina = 1;
      var teto = 50;

      (function proxima() {
        var reqParams = $.extend({}, base, { pagina: pagina, limite: limite });
        Api.enviar({ url: opts.url, method: "GET", data: reqParams, erroSilencioso: true })
          .done(function (resp) {
            var itens = (resp && resp.itens) || [];
            var total = resp ? (resp.total || 0) : 0;
            var achou = itens.some(function (item) {
              return String(opts.idDe(item)) === String(idDestacar);
            });
            if (achou) {
              estado.pagina = pagina;
              procurandoDestacado = false;
              carregar();
            } else if (pagina < teto && pagina * limite < total) {
              pagina += 1;
              proxima();
            } else {
              procurandoDestacado = false;
            }
          })
          .fail(function () { procurandoDestacado = false; });
      })();
    }

    function mostrarErro() {
      $wrap.addClass("hidden");
      $paginacao.addClass("hidden");
      Estados.erro($estadoBloco.removeClass("hidden"), {
        mensagem: opts.erro || "Não foi possível carregar os dados.",
        aoTentarDeNovo: carregar,
      });
    }

    // ---------- carga ----------

    function carregar() {
      var params = $.extend({}, opts.params ? opts.params() : {});
      // a ordem padrão do endpoint não vai na query: manter a URL limpa é o que faz um link
      // compartilhado só carregar `ordenar` quando o investigador realmente trocou a ordem
      if (estado.ordenar && estado.ordenar !== ordenarPadrao) params.ordenar = estado.ordenar;
      ultimosParams = params;
      var reqParams = $.extend({}, params, { pagina: estado.pagina, limite: limite });

      Carregando.emTabela($tbody, colunas.length);
      $estadoBloco.addClass("hidden");
      $wrap.removeClass("hidden");
      $paginacao.addClass("hidden");

      salvarEstadoUrl(params);

      // erroSilencioso: o próprio Estados.erro já mostra a mensagem + "Tentar de novo" no lugar
      // da tabela; sem isso, o erro duplicaria como toast global (js/api.js) também.
      Api.enviar({ url: opts.url, method: "GET", data: reqParams, erroSilencioso: true })
        .done(renderizar)
        .fail(mostrarErro);
    }

    function recarregar() {
      estado.pagina = 1;
      carregar();
    }

    // ---------- estado na URL (window.EstadoUrl) ----------
    // Persiste os filtros da tela + a página atual na query string, sem tocar em ?inquerito=/
    // ?extracao=/etc. Restaura só a página no load (os filtros específicos de cada tela — selects,
    // datas — continuam por conta da própria tela, que já lê o valor deles do DOM).

    // Chaves que a última gravação deixou na URL. EstadoUrl.salvar só toca no que recebe, então
    // um filtro DESFEITO (some do objeto de params) deixaria o valor antigo preso na query — o
    // "Limpar filtros" esvaziaria a tela e a URL compartilhável continuaria filtrada.
    var chavesSalvas = {};

    function salvarEstadoUrl(params) {
      if (!window.EstadoUrl) return;
      // `ordenar` precisa ir explicitamente como null quando está no padrão (mesmo motivo de
      // `pagina`): EstadoUrl.salvar só mexe nas chaves que recebe, então OMITIR a chave deixaria
      // a ordenação anterior presa na URL depois de desfeita.
      var valores = $.extend({}, params, {
        pagina: estado.pagina > 1 ? estado.pagina : null,
        ordenar: params.ordenar || null,
      });
      var agora = {};
      Object.keys(valores).forEach(function (k) { if (valores[k] !== null) agora[k] = true; });
      var patch = valores;
      Object.keys(chavesSalvas).forEach(function (k) { if (!(k in agora)) patch[k] = null; });
      chavesSalvas = agora;
      window.EstadoUrl.salvar(patch);
    }

    if (window.EstadoUrl) {
      var salvo = window.EstadoUrl.ler(["pagina", "ordenar"]);
      var paginaSalva = parseInt(salvo.pagina, 10);
      if (paginaSalva > 1) estado.pagina = paginaSalva;
      // só aceita de volta uma chave que alguma coluna declara — a URL é editável/compartilhável,
      // e o endpoint ignoraria um valor fora da whitelist dele de qualquer jeito
      if (salvo.ordenar && ativoDaChave(salvo.ordenar)) estado.ordenar = salvo.ordenar;
    }
    // a tela pode já ter uma ordenação escolhida no <select> dela (localizações, navegação)
    if (estado.ordenar === ordenarPadrao && $seletorOrdenar.length && $seletorOrdenar.val()) {
      if (ativoDaChave($seletorOrdenar.val())) estado.ordenar = $seletorOrdenar.val();
    }
    if ($seletorOrdenar.length && estado.ordenar) $seletorOrdenar.val(estado.ordenar);
    if ($seletorOrdenar.length) {
      $seletorOrdenar.on("change", function () { definirOrdenar($(this).val()); });
    }
    atualizarCabecalhos();

    // ---------- paginação: botões e teclado ----------

    $pagAnterior.on("click", function () {
      if (estado.pagina > 1) { estado.pagina -= 1; carregar(); window.scrollTo(0, 0); }
    });
    $pagProximo.on("click", function () {
      if (estado.pagina < totalPaginas()) { estado.pagina += 1; carregar(); window.scrollTo(0, 0); }
    });

    // ←/→ mudam de página quando o foco não está em campo de texto/select — não interfere na
    // digitação da busca nem nos filtros da tela.
    $(document).on("keydown.tabelaPaginada", function (e) {
      var tag = (document.activeElement && document.activeElement.tagName) || "";
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (document.activeElement && document.activeElement.isContentEditable)) return;
      if (e.key === "ArrowLeft" && !$pagAnterior.prop("disabled") && !$paginacao.hasClass("hidden")) {
        $pagAnterior.trigger("click");
      } else if (e.key === "ArrowRight" && !$pagProximo.prop("disabled") && !$paginacao.hasClass("hidden")) {
        $pagProximo.trigger("click");
      }
    });

    // ---------- exportação (opcional) ----------

    if (opts.exportar && window.ExportarExcel) {
      var colunasExportar = opts.exportar.colunas || colunas
        .filter(function (c) { return c.exportavel !== false; })
        .map(function (c) { return c.rotulo; });
      ExportarExcel.instalar({
        nome: opts.exportar.nome,
        dados: function () {
          // repassa a ordenação atual: o .xlsx sai na mesma ordem que está na tela
          var params = $.extend({}, opts.params ? opts.params() : {}, { pagina: 1, limite: 1000000 });
          if (estado.ordenar) params.ordenar = estado.ordenar;
          return $.getJSON(opts.url, params).then(function (resp) {
            var itens = (resp && resp.itens) || [];
            return {
              colunas: colunasExportar,
              linhas: itens.map(opts.exportar.linha),
            };
          });
        },
      });
    }

    if (opts.autoCarregar !== false) carregar();

    return {
      carregar: carregar,
      recarregar: recarregar,
      params: function () { return ultimosParams; },
      ordenar: function (chave) {
        if (chave === undefined) return estado.ordenar;
        definirOrdenar(chave);
      },
    };
  }

  window.TabelaPaginada = { criar: criar };
})();
