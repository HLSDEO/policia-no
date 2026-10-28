/* Tela Administração → Documentação de código: lista os .md da pasta docs/ do projeto e mostra o
 * conteúdo renderizado (js/utils/markdown.js). Fonte: GET /api/documentacao (rotas/documentacao.py),
 * que exige administrador — o 403 aqui não é erro de tela, é a página avisando quem não pode ver.
 *
 * A documentação continua sendo editada no repositório; esta tela é só leitura. O que ela resolve é
 * o acesso: hoje só quem tem o código aberto no editor lê os documentos que explicam como cada
 * feature funciona.
 *
 * Estado na URL (?doc=arquivo.md#secao): o link de um documento é compartilhável, o botão Voltar do
 * navegador funciona, e um link entre documentos ([outro.md](outro.md), muito usado nos textos)
 * troca o documento na própria tela em vez de sair dela. */
(function () {
  "use strict";

  var $lista = $("#doc-lista");
  var $busca = $("#doc-busca");
  var $conteudo = $("#doc-conteudo");
  var $corpo = $("#doc-corpo");
  var $titulo = $("#doc-titulo");
  var $meta = $("#doc-meta");
  var $sumario = $("#doc-sumario");
  var $sumarioLista = $("#doc-sumario-lista");
  var $semPermissao = $("#sem-permissao-hint");
  var $layout = $("#doc-layout");

  var docAtual = null;
  var timerBusca = null;

  // ---------- utilidades ----------

  function formatarTamanho(bytes) {
    if (!bytes && bytes !== 0) return "—";
    if (bytes < 1024) return bytes + " B";
    return (bytes / 1024).toFixed(bytes < 102400 ? 1 : 0) + " KB";
  }

  function urlDoDoc(nome, ancora) {
    return "documentacao.html?doc=" + encodeURIComponent(nome) + (ancora ? "#" + ancora : "");
  }

  function semPermissao() {
    $layout.addClass("hidden");
    $semPermissao.removeClass("hidden");
  }

  // ---------- lista lateral ----------

  function renderLista(arquivos, filtrando) {
    $lista.empty();
    if (!arquivos.length) {
      Estados.vazio($lista, {
        filtrado: !!filtrando,
        titulo: filtrando ? "Nenhum documento com esse termo" : "Nenhum documento em docs/",
        dica: filtrando ? "A busca cobre nome, título e conteúdo dos arquivos." : "",
        acao: filtrando ? { rotulo: "Limpar busca", aoClicar: function () { $busca.val("").trigger("input"); } } : null
      });
      return;
    }

    var grupoAtual = null;
    arquivos.forEach(function (arq) {
      if (arq.grupo !== grupoAtual) {
        grupoAtual = arq.grupo;
        $("<div>").addClass("doc-lista-grupo").text(grupoAtual).appendTo($lista);
      }

      var $item = $("<button>")
        .attr({ type: "button", "data-doc": arq.nome, title: arq.nome })
        .addClass("doc-lista-item" + (arq.nome === docAtual ? " ativo" : ""));
      $("<span>").addClass("doc-lista-titulo").text(arq.titulo).appendTo($item);
      $("<span>").addClass("doc-lista-arquivo").text(arq.nome).appendTo($item);

      // na busca por conteúdo, mostra a linha que casou — sem isso o resultado é um nome de
      // arquivo sem explicação de por que ele está ali
      (arq.trechos || []).forEach(function (trecho) {
        $("<span>").addClass("doc-lista-trecho").text(trecho).appendTo($item);
      });

      $item.appendTo($lista);
    });
  }

  function carregarLista(termo, aoTerminar) {
    Api.get("/api/documentacao", termo ? { busca: termo } : {})
      .done(function (resp) {
        if (!resp.disponivel) {
          $lista.empty();
          Estados.erro($lista, { mensagem: "A pasta docs/ não está acessível pela API (" + resp.diretorio + ")." });
          return;
        }
        renderLista(resp.arquivos || [], !!termo);
        if (aoTerminar) aoTerminar(resp.arquivos || []);
      })
      .fail(function (xhr) {
        if (xhr.status === 403) { semPermissao(); return; }
        $lista.empty();
        Estados.erro($lista, {
          mensagem: Api.mensagemErro(xhr, "Não foi possível listar a documentação."),
          aoTentarDeNovo: function () { carregarLista(termo, aoTerminar); }
        });
      });
  }

  // ---------- documento ----------

  function renderSumario(titulos) {
    // só h2/h3: o h1 é o próprio título do documento (já está no cabeçalho) e h4+ deixaria o
    // sumário mais comprido que o texto em documentos bem subdivididos
    var uteis = titulos.filter(function (t) { return t.nivel === 2 || t.nivel === 3; });
    $sumarioLista.empty();
    if (uteis.length < 2) {
      $sumario.addClass("hidden");
      return;
    }
    uteis.forEach(function (t) {
      $("<a>")
        .addClass("doc-sumario-item nivel-" + t.nivel)
        .attr("href", "#" + t.id)
        .attr("data-ancora", t.id)
        .text(t.texto)
        .appendTo($sumarioLista);
    });
    $sumario.removeClass("hidden");
  }

  function irParaAncora(ancora) {
    if (!ancora) {
      window.scrollTo(0, 0);
      return;
    }
    var alvo = document.getElementById(ancora);
    if (!alvo) return;
    alvo.scrollIntoView({ block: "start" });
    $(".md-h").removeClass("md-h-alvo");
    $(alvo).addClass("md-h-alvo");
  }

  function marcarAtivo(nome) {
    $lista.find(".doc-lista-item").each(function () {
      $(this).toggleClass("ativo", $(this).attr("data-doc") === nome);
    });
  }

  function abrirDoc(nome, ancora, semHistorico) {
    if (!nome) return;
    docAtual = nome;
    marcarAtivo(nome);
    $conteudo.removeClass("hidden");
    $titulo.text(nome);
    $meta.text("");
    $sumario.addClass("hidden");
    Carregando.emBloco($corpo);

    Api.get("/api/documentacao/" + encodeURIComponent(nome))
      .done(function (doc) {
        Carregando.limpar($corpo);
        var render = Markdown.renderizar(doc.conteudo, { linkDoc: urlDoDoc });
        $titulo.text(doc.titulo);
        $meta.text(doc.nome + " · " + formatarTamanho(doc.bytes) +
          " · atualizado em " + AnalyserUtils.formatarDataHora(doc.atualizado_em));
        $corpo.html(render.html);
        renderSumario(render.titulos);
        window.scrollTo(0, 0);
        if (ancora) irParaAncora(ancora);
        if (!semHistorico) {
          history.pushState({ doc: nome, ancora: ancora || "" }, "", urlDoDoc(nome, ancora));
        }
        document.title = doc.titulo + " · Documentação · PolícIA";
      })
      .fail(function (xhr) {
        Carregando.limpar($corpo);
        if (xhr.status === 403) { semPermissao(); return; }
        $corpo.empty();
        Estados.erro($corpo, {
          mensagem: Api.mensagemErro(xhr, "Não foi possível abrir o documento."),
          aoTentarDeNovo: function () { abrirDoc(nome, ancora, true); }
        });
      });
  }

  // ---------- eventos ----------

  $lista.on("click", ".doc-lista-item", function () {
    abrirDoc($(this).attr("data-doc"), "");
  });

  // link para outro .md dentro do texto: troca o documento sem recarregar a página (o href
  // continua real, então "abrir em nova aba" e copiar o link seguem funcionando)
  $corpo.on("click", "a.md-link-doc", function (e) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    abrirDoc($(this).attr("data-doc"), $(this).attr("data-ancora") || "");
  });

  // âncora interna (#secao) — rola sem deixar o hash sujar o histórico a cada clique
  $corpo.on("click", 'a[href^="#"]', function (e) {
    e.preventDefault();
    irParaAncora($(this).attr("href").slice(1));
  });

  $sumarioLista.on("click", ".doc-sumario-item", function (e) {
    e.preventDefault();
    irParaAncora($(this).attr("data-ancora"));
  });

  $busca.on("input", function () {
    var termo = $.trim($busca.val());
    clearTimeout(timerBusca);
    timerBusca = setTimeout(function () {
      EstadoUrl.salvar({ busca: termo });
      carregarLista(termo);
    }, 250);
  });

  $busca.on("keydown", function (e) {
    if (e.key === "Escape") { $busca.val("").trigger("input"); }
  });

  window.addEventListener("popstate", function (e) {
    var estado = e.state || {};
    var nome = estado.doc || (EstadoUrl.ler(["doc"]).doc || "");
    if (nome && nome !== docAtual) abrirDoc(nome, estado.ancora || "", true);
    else irParaAncora(estado.ancora || "");
  });

  // ---------- init ----------

  var inicial = EstadoUrl.ler(["doc", "busca"]);
  var ancoraInicial = (window.location.hash || "").replace(/^#/, "");
  if (inicial.busca) $busca.val(inicial.busca);

  carregarLista(inicial.busca || "", function (arquivos) {
    if (!arquivos.length) return;
    var nome = inicial.doc && arquivos.some(function (a) { return a.nome === inicial.doc; })
      ? inicial.doc
      : arquivos[0].nome;   // README.md vem primeiro da API — é o índice da documentação
    abrirDoc(nome, ancoraInicial, true);
  });
})();
