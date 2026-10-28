(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  var delegaciaParam = paramUrl("delegacia");
  var idConversaAtual = null;

  // Conversa a abrir direto, do link do aviso "a IA terminou de responder" (js/utils/avisos_ia.js).
  var conversaParam = parseInt(paramUrl("conversa"), 10) || null;

  // Vindo de um botão "Prompt de IA específico" (RIF/extrato do SIMBA/IPL, ver investigacao.js e
  // rif.js/simba.js/inquerito_pdf.js): cria (ou reaproveita) uma conversa restrita a esse único
  // documento, em vez de deixar o investigador escolher/criar uma conversa normal.
  var escopoTipoParam = paramUrl("escopo_tipo");
  var escopoIdParam = paramUrl("escopo_id");
  var escopoTituloParam = paramUrl("escopo_titulo");
  var escopoTratado = false;

  // ?pergunta= — o "Resumo" dos cartões da Orbit abre a conversa escopada já com a pergunta
  // escrita (sem enviar: o investigador revisa e aperta Enter). Só a 1ª conversa aberta recebe
  // o prefill, para não sobrescrever o rascunho de quem troca de conversa depois.
  var perguntaParam = paramUrl("pergunta");
  var prefillPendente = perguntaParam ? String(perguntaParam).slice(0, 8000) : "";
  var conversasCache = [];

  var ROTULO_ESCOPO_TIPO = {
    rif: "RIF", simba: "extrato do SIMBA", ipl: "inquérito em PDF (IPL)",
    irpf: "declaração de IRPF", ipei: "IPEI (quebra de sigilo fiscal)",
    telefonia: "telefonia (retorno da operadora)",
    extracao: "aparelho (extração)", documento: "documento anexado"
  };

  var $lista = $("#chat-conversas");
  var $busca = $("#chat-busca");
  var $filtroCriador = $("#chat-filtro-criador");
  var $header = $("#chat-main-header");
  var $escopoBanner = $("#chat-escopo-banner");
  var $mensagens = $("#chat-mensagens");
  var $irFim = $("#chat-ir-fim");
  var $irFimNovas = $("#chat-ir-fim-novas");
  var $form = $("#chat-form");
  var $input = $("#chat-input");
  var $enviarBtn = $("#chat-enviar-btn");
  var $digitando = $("#chat-digitando");
  var $exibirPrompt = $("#chat-exibir-prompt");
  var $fontesLista = $("#chat-fontes-lista");
  var $fontesResumo = $("#chat-fontes-resumo");
  var $fontesTodas = $("#chat-fontes-todas");
  var $fontesBusca = $("#chat-fontes-busca");
  var $fontesFiltro = $("#chat-fontes-filtro");
  var $fontesToggle = $("#chat-fontes-toggle");
  var $fontesToggleTxt = $("#chat-fontes-toggle-txt");
  var $fontesFechar = $("#chat-fontes-fechar");
  var $fontesFundo = $("#chat-fontes-fundo");
  var $app = $(".chat-app");
  var $toolbar = $("#chat-toolbar");
  var $coberturaResumo = $("#chat-cobertura-resumo");
  var $exportarMdBtn = $("#chat-exportar-md");
  var $imprimirBtn = $("#chat-imprimir");
  var $duplicarBtn = $("#chat-duplicar");
  var $copiarLinkBtn = $("#chat-copiar-link");

  // Cache da conversa aberta: alimenta exportar .md, cobertura de fontes e feedback sem
  // releitura do DOM. Preenchido em abrirConversa a cada listagem do backend.
  var msgsCache = [];
  var feedbacksCache = {};

  // Painel de fontes ("considerar apenas"): o inventário do inquérito — aparelhos, RIFs, extratos
  // do SIMBA, IPLs e documentos anexados — numa lista com caixa de seleção por item, no lugar do
  // <select multiple> (TomSelect) que ficava dentro do compositor. Tudo marcado, que é o estado
  // inicial, = todo o inquérito; desmarcar é que restringe. As opções carregam do backend uma vez.
  var GRUPOS_FONTES = [
    {
      tp: "extracao", rotulo: "Aparelhos (extrações)", icone: "aparelho",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/extracoes-lista"; },
      id: function (x) { return x.id_extracao; },
      rotuloItem: function (x) { return "Aparelho " + (x.no_dispositivo || x.no_fabricante || x.no_arquivo || x.id_extracao); }
    },
    {
      tp: "rif", rotulo: "RIFs", icone: "rif",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/rifs"; },
      id: function (x) { return x.id_rif; },
      rotuloItem: function (x) { return "RIF " + (x.nu_rif || x.no_pasta || x.id_rif); }
    },
    {
      tp: "simba", rotulo: "Extratos SIMBA", icone: "banco",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/extratos-simba"; },
      id: function (x) { return x.id_extrato; },
      rotuloItem: function (x) { return "Extrato SIMBA " + (x.no_arquivo || x.id_extrato); }
    },
    {
      tp: "ipl", rotulo: "IPLs (inquéritos em PDF)", icone: "ipl",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/pdfs"; },
      id: function (x) { return x.id_ipl; },
      rotuloItem: function (x) { return "IPL " + (x.nu_ipl || x.no_arquivo || x.id_ipl); }
    },
    {
      tp: "documento", rotulo: "Documentos anexados", icone: "documentos",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/documentos"; },
      id: function (x) { return x.id_documento; },
      rotuloItem: function (x) { return "Documento " + (x.no_arquivo || x.id_documento); },
      filtro: function (x) { return !x.in_ipl && !x.in_rif && !x.in_simba; },
      subgrupo: function (x) { return window.AnalyserUtils.categoriaArquivo(x.tp_arquivo, x.no_arquivo); }
    }
  ];

  // Grupo com item demais ganha um nível: os itens são partidos pelo subgrupo da fonte (hoje
  // só "Documentos anexados" tem, por categoria de arquivo) em blocos que nascem recolhidos —
  // com 500 documentos, a lista aberta é rolagem infinita. Com filtro de texto ativo os blocos
  // abrem sozinhos, para o achado ficar à vista.
  var LIMIAR_SUBGRUPOS_FONTES = 50;
  var subExpandidos = {}; // "tp\nsub" -> true; nasce tudo recolhido

  function chaveSub(tp, sub) { return tp + "\n" + sub; }

  var CHAVE_PAINEL_FONTES = "analyser-ia-fontes-abertas";
  var fontes = [];          // [{valor: "tp:id", tp, rotulo}], na ordem dos grupos
  var marcadas = new Set(); // valores marcados

  function carregarFontes() {
    if (!idInquerito || escopoTipoParam) return; // conversa já restrita a um único documento — não se aplica
    var pendentes = GRUPOS_FONTES.map(function (g) {
      // o 2º callback do .then devolve lista vazia em vez de propagar a rejeição: com .fail() o
      // $.when abaixo rejeitava, e um único endpoint com erro deixava o painel inteiro sem fonte
      return $.getJSON(g.url(idInquerito)).then(
        // NO_ARQUIVO/NO_PASTA vêm como caminho relativo em data/ — no rótulo só o nome interessa
        // (ver docs/organizacao_arquivos.md)
        function (lista) { return { grupo: g, lista: window.AnalyserUtils.encurtarCaminhos(lista || []) }; },
        function () { return { grupo: g, lista: [] }; }
      );
    });
    $.when.apply($, pendentes).done(function () {
      Array.prototype.slice.call(arguments).forEach(function (r) {
        var lista = (r.grupo.filtro ? r.lista.filter(r.grupo.filtro) : r.lista);
        lista.forEach(function (x) {
          fontes.push({
            valor: r.grupo.tp + ":" + r.grupo.id(x),
            tp: r.grupo.tp,
            sub: r.grupo.subgrupo ? r.grupo.subgrupo(x) : null,
            rotulo: r.grupo.rotuloItem(x)
          });
        });
      });
      if (!fontes.length) return;
      fontes.forEach(function (f) { marcadas.add(f.valor); }); // começa com tudo = todo o inquérito
      $fontesBusca.toggleClass("hidden", fontes.length <= 8);  // filtro só quando a lista cansa de rolar
      renderFontes();
      atualizarEscopoUi(conversaAberta());
    });
  }

  function renderFontes() {
    var termo = $.trim($fontesFiltro.val() || "").toLowerCase();
    $fontesLista.empty();
    var visiveis = 0;
    GRUPOS_FONTES.forEach(function (g) {
      var itens = fontes.filter(function (f) {
        return f.tp === g.tp && (!termo || f.rotulo.toLowerCase().indexOf(termo) !== -1);
      });
      if (!itens.length) return;
      visiveis += itens.length;
      var $grupo = $("<div>").addClass("chat-fontes-grupo");
      var $cab = $("<label>").addClass("chat-fontes-grupo-cab").appendTo($grupo);
      $("<span>").addClass("chat-fontes-grupo-nome").text(g.rotulo).appendTo($cab);
      $("<span>").addClass("chat-fontes-grupo-qtd").appendTo($cab);
      $("<input>").attr("type", "checkbox").addClass("chat-fontes-grupo-check")
        .attr("aria-label", "Marcar ou desmarcar: " + g.rotulo).appendTo($cab);
      if (itens.length > LIMIAR_SUBGRUPOS_FONTES && itens.some(function (f) { return f.sub; })) {
        renderSubgrupos($grupo, g, itens, termo);
      } else {
        itens.forEach(function (f) { $grupo.append(linhaFonte(g, f)); });
      }
      $fontesLista.append($grupo);
    });
    if (!visiveis) {
      $fontesLista.append($("<div>").addClass("chat-fontes-vazio").text("Nenhuma fonte com esse texto."));
    }
    sincronizarFontes();
  }

  function linhaFonte(g, f) {
    var $linha = $("<label>").addClass("chat-fonte").attr("title", f.rotulo);
    $("<span>").addClass("chat-fonte-icone").html(window.Icones.html(g.icone)).appendTo($linha);
    $("<span>").addClass("chat-fonte-nome").text(f.rotulo).appendTo($linha);
    $("<input>").attr("type", "checkbox").addClass("chat-fonte-check")
      .attr("data-valor", f.valor).prop("checked", marcadas.has(f.valor)).appendTo($linha);
    return $linha;
  }

  function renderSubgrupos($grupo, g, itens, termo) {
    var ordem = [];
    var mapa = {};
    itens.forEach(function (f) {
      var sub = f.sub || "Outros";
      if (!mapa[sub]) { mapa[sub] = []; ordem.push(sub); }
      mapa[sub].push(f);
    });
    ordem.sort(function (a, b) { return a.localeCompare(b, "pt-BR", { numeric: true }); });
    ordem.forEach(function (sub) {
      var chave = chaveSub(g.tp, sub);
      var expandido = !!termo || !!subExpandidos[chave];
      var $sub = $("<div>").addClass("chat-fontes-subgrupo").appendTo($grupo);
      var $cabSub = $("<div>").addClass("chat-fontes-subgrupo-cab").appendTo($sub);
      $("<button type='button'>").addClass("chat-fontes-subgrupo-toggle")
        .attr({
          "aria-expanded": expandido ? "true" : "false",
          "aria-label": (expandido ? "Recolher " : "Expandir ") + sub
        })
        .text(expandido ? "▾" : "▸").appendTo($cabSub);
      $("<span>").addClass("chat-fontes-subgrupo-nome").text(sub).appendTo($cabSub);
      $("<span>").addClass("chat-fontes-subgrupo-qtd").appendTo($cabSub);
      $("<input>").attr("type", "checkbox").addClass("chat-fontes-subgrupo-check")
        .attr({ "aria-label": "Marcar ou desmarcar: " + sub, "data-sub": chave }).appendTo($cabSub);
      var $itens = $("<div>").addClass("chat-fontes-subgrupo-itens").toggle(expandido).appendTo($sub);
      mapa[sub].forEach(function (f) { $itens.append(linhaFonte(g, f)); });
    });
  }

  // As caixas de "todas" e de grupo refletem (e mexem em) o que está à vista: com filtro ativo,
  // marcar "todas" marca o que foi filtrado, não o inventário inteiro — mesma regra da seleção de
  // marcações em js/investigacao.js. O rodapé, esse, continua contando o inventário inteiro.
  // A caixa do subgrupo segue a mesma regra, restrita aos itens do bloco.
  function sincronizarFontes() {
    $fontesLista.find(".chat-fontes-grupo").each(function () {
      var $g = $(this);
      var $itens = $g.find(".chat-fonte-check");
      var n = $itens.filter(":checked").length;
      $g.find(".chat-fontes-grupo-qtd").text(n + "/" + $itens.length);
      $g.find(".chat-fontes-grupo-check")
        .prop("checked", $itens.length > 0 && n === $itens.length)
        .prop("indeterminate", n > 0 && n < $itens.length);
    });
    $fontesLista.find(".chat-fontes-subgrupo").each(function () {
      var $s = $(this);
      var $itens = $s.find(".chat-fonte-check");
      var n = $itens.filter(":checked").length;
      $s.find(".chat-fontes-subgrupo-qtd").text(n + "/" + $itens.length);
      $s.find(".chat-fontes-subgrupo-check")
        .prop("checked", $itens.length > 0 && n === $itens.length)
        .prop("indeterminate", n > 0 && n < $itens.length);
    });
    var $visiveis = $fontesLista.find(".chat-fonte-check");
    var marcadosVisiveis = $visiveis.filter(":checked").length;
    $fontesTodas
      .prop("checked", $visiveis.length > 0 && marcadosVisiveis === $visiveis.length)
      .prop("indeterminate", marcadosVisiveis > 0 && marcadosVisiveis < $visiveis.length);
    atualizarResumoFontes();
  }

  function marcarLote($checks, marcar) {
    $checks.each(function () {
      this.checked = marcar;
      var valor = $(this).attr("data-valor");
      if (marcar) marcadas.add(valor); else marcadas["delete"](valor);
    });
    sincronizarFontes();
  }

  $fontesLista.on("change", ".chat-fonte-check", function () {
    var valor = $(this).attr("data-valor");
    if (this.checked) marcadas.add(valor); else marcadas["delete"](valor);
    sincronizarFontes();
  });

  $fontesLista.on("change", ".chat-fontes-grupo-check", function () {
    marcarLote($(this).closest(".chat-fontes-grupo").find(".chat-fonte-check"), this.checked);
  });

  $fontesLista.on("change", ".chat-fontes-subgrupo-check", function () {
    marcarLote($(this).closest(".chat-fontes-subgrupo").find(".chat-fonte-check"), this.checked);
  });

  $fontesLista.on("click", ".chat-fontes-subgrupo-toggle", function () {
    var $btn = $(this);
    var $sub = $btn.closest(".chat-fontes-subgrupo");
    var $itens = $sub.find(".chat-fontes-subgrupo-itens");
    var abrir = !$itens.is(":visible");
    $itens.toggle(abrir);
    var nome = $sub.find(".chat-fontes-subgrupo-nome").text();
    $btn.text(abrir ? "▾" : "▸")
      .attr({
        "aria-expanded": abrir ? "true" : "false",
        "aria-label": (abrir ? "Recolher " : "Expandir ") + nome
      });
    subExpandidos[$sub.find(".chat-fontes-subgrupo-check").attr("data-sub")] = abrir;
  });

  $fontesTodas.on("change", function () {
    marcarLote($fontesLista.find(".chat-fonte-check"), this.checked);
  });

  $fontesFiltro.on("input", renderFontes);

  function escoposSelecionados() {
    // Os dois extremos querem dizer "sem restrição": tudo marcado (o padrão) e nada marcado
    // devolvem lista vazia, e o backend considera o inquérito inteiro (ver ia_utils.py).
    if (!fontes.length || !marcadas.size || marcadas.size === fontes.length) return [];
    var escopos = [];
    marcadas.forEach(function (valor) {
      var partes = valor.split(":");
      escopos.push({ tp_escopo: partes[0], id_escopo: Number(partes[1]) });
    });
    return escopos;
  }

  function atualizarResumoFontes() {
    var total = fontes.length;
    if (!total) return;
    var n = marcadas.size;
    $fontesResumo.removeClass("alerta");
    if (n === total) {
      $fontesResumo.text("Todas as " + total + " fontes marcadas — a IA considera todo o inquérito.");
      $fontesToggleTxt.text("Fontes: todas (" + total + ")");
    } else if (!n) {
      $fontesResumo.addClass("alerta")
        .text("Nenhuma fonte marcada — a IA volta a considerar todo o inquérito.");
      $fontesToggleTxt.text("Fontes: todas (" + total + ")");
    } else {
      $fontesResumo.text("A IA vai consultar só " + n + " de " + total + " fontes deste inquérito.");
      $fontesToggleTxt.text("Fontes: " + n + " de " + total);
    }
  }

  function conversaAberta() {
    return conversasCache.find(function (c) { return c.id_conversa_ia === idConversaAtual; }) || null;
  }

  // Aberto/fechado do painel: em tela larga começa aberto (as três colunas cabem); em tela estreita
  // começa fechado, porque lá ele vira gaveta sobre a conversa. A escolha do usuário fica gravada.
  function preferenciaPainelFontes() {
    try {
      var v = localStorage.getItem(CHAVE_PAINEL_FONTES);
      // o "1" salvo vem de uma tela larga: no celular o painel é gaveta e abri-la na chegada
      // taparia a conversa inteira — lá a preferência só vale para fechar.
      if (v === "1") return window.innerWidth > 1000;
      if (v === "0") return false;
    } catch (e) { /* localStorage bloqueado — vale o padrão por largura */ }
    return window.innerWidth > 1000;
  }

  function mostrarPainelFontes(abrir) {
    $app.toggleClass("fontes-abertas", !!abrir);
    $fontesToggle.attr("aria-expanded", abrir ? "true" : "false");
  }

  function alternarPainelFontes(abrir, porUsuario) {
    mostrarPainelFontes(abrir);
    try { localStorage.setItem(CHAVE_PAINEL_FONTES, abrir ? "1" : "0"); } catch (e) { /* idem */ }
    // na gaveta (tela estreita) o painel é o único conteúdo relevante: o foco entra com ele e
    // volta ao botão que o abriu, senão quem usa teclado fica navegando às cegas
    if (!porUsuario || window.innerWidth > 1000) return;
    if (abrir) {
      setTimeout(function () {
        ($fontesBusca.hasClass("hidden") ? $fontesFechar : $fontesFiltro).trigger("focus");
      }, 50);
    } else {
      $fontesToggle.trigger("focus");
    }
  }

  $fontesToggle.on("click", function () { alternarPainelFontes(!$app.hasClass("fontes-abertas"), true); });
  $fontesFechar.on("click", function () { alternarPainelFontes(false, true); });
  $fontesFundo.on("click", function () { alternarPainelFontes(false, true); });
  $(document).on("keydown", function (e) {
    // só na gaveta: em tela larga o painel é coluna fixa, fechar no Esc seria surpresa
    if (e.key === "Escape" && $app.hasClass("fontes-abertas") && window.innerWidth <= 1000) {
      alternarPainelFontes(false, true);
    }
  });

  carregarFontes();

  // Mostra a faixa de aviso quando a conversa aberta tiver escopo (restrita a um único
  // RIF/extrato/IPL) e esconde o painel de fontes, que não se aplica nesse caso.
  function atualizarEscopoUi(conversa) {
    var presa = !!(conversa && conversa.tp_escopo);
    if (presa) {
      var rotulo = ROTULO_ESCOPO_TIPO[conversa.tp_escopo] || conversa.tp_escopo;
      // "a: rotulo" em vez de "a um único rotulo" pra não depender do gênero gramatical do
      // rótulo (RIF/extrato são masculinos, declaração é feminino — ver ROTULO_ESCOPO_TIPO).
      $escopoBanner.text("🔒 Conversa restrita a: " + rotulo + " — a IA não enxerga outros documentos nem o conteúdo do aparelho deste inquérito.").removeClass("hidden");
    } else {
      $escopoBanner.addClass("hidden");
    }
    // o painel só faz sentido com uma conversa aberta, sem escopo próprio e com inventário carregado
    var disponivel = !presa && !!idConversaAtual && fontes.length > 0;
    $fontesToggle.toggleClass("hidden", !disponivel);
    mostrarPainelFontes(disponivel && preferenciaPainelFontes());
  }

  function formatarDataHora(iso) {
    if (!iso) return "";
    // os timestamps do servidor vêm em UTC sem sufixo de fuso ("2026-07-13T19:11:35"); acrescenta
    // 'Z' para o navegador interpretar como UTC e converter para o horário local. Valores que já
    // trazem fuso (ex.: a bolha otimista, com toISOString) passam intactos, evitando dobrar o offset.
    var s = String(iso);
    if (s.indexOf("T") !== -1 && !/[Zz]$|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
    return new Date(s).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // ---------- Markdown mínimo e seguro ----------
  // Escapa TODO o HTML primeiro e só então aplica as marcações — assim conteúdo vindo
  // da IA (ou citado por ela a partir das mensagens do aparelho) nunca vira tag de verdade.
  // Cobre o que os modelos realmente usam: título, negrito/itálico, código, lista e parágrafo.

  // Escapa as aspas também: o markdown abaixo monta href="..." a partir do texto, e uma aspa
  // solta na URL escapava do atributo e virava atributo injetado.
  function escaparHtml(texto) {
    return texto.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Só link "de verdade" vira link: http(s) com // e endereço de e-mail (com ou sem mailto:).
  // O texto da resposta vem da IA, que transcreve documentos do inquérito — um
  // [clique aqui](javascript:...) escrito dentro de um arquivo apensado viraria script rodando
  // como o investigador. Protocolo relativo (//evilsite) e data: também ficam de fora.
  function urlSegura(url) {
    var limpa = String(url).replace(/[\u0000-\u0020\u007f]/g, "").toLowerCase();
    if (/^https?:\/\//.test(limpa)) return true;
    return /^(mailto:)?[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(limpa);
  }

  // A URL já veio por escaparHtml (sem aspa crua, então não escapa do href="") e o regex chamador
  // não aceita espaço. Aqui só (a) derrubam os caracteres de controle que enganam o teste de
  // esquema ("java\nscript:") e (b) ganha o mailto: que o autor do markdown omitiu — sem ele o
  // e-mail viraria um href relativo, que abre para lugar nenhum.
  function normalizarUrlLink(url) {
    var limpa = String(url).replace(/[\u0000-\u001f]/g, "");
    return /^[a-z][a-z0-9+.-]*:/i.test(limpa) ? limpa : "mailto:" + limpa;
  }

  function markdownParaHtml(texto) {
    var blocos = escaparHtml(texto).split(/```/);
    var html = "";
    blocos.forEach(function (bloco, i) {
      if (i % 2 === 1) {
        // dentro de ``` ``` — só remove a dica de linguagem da primeira linha, sem formatar nada
        html += "<pre><code>" + bloco.replace(/^[a-z]*\n/, "") + "</code></pre>";
        return;
      }
      var linhas = bloco.split("\n");
      var listaAberta = false;
      var linhasTabela = [];

      function fecharTabela() {
        if (!linhasTabela.length) return;
        html += "<div class='chat-md-tabela'><table>";
        linhasTabela.forEach(function (celulas, idx) {
          var tag = idx === 0 ? "th" : "td";
          html += "<tr>" + celulas.map(function (c) { return "<" + tag + ">" + c + "</" + tag + ">"; }).join("") + "</tr>";
        });
        html += "</table></div>";
        linhasTabela = [];
      }

      linhas.forEach(function (linha) {
        var formatada = linha
          .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
          .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
          .replace(/`([^`\n]+)`/g, "<code>$1</code>")
          .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_todo, rotulo, url) {
            if (!urlSegura(url)) return _todo; // mostra o marcador cru em vez de criar link perigoso
            return '<a href="' + normalizarUrlLink(url) + '" target="_blank" rel="noopener noreferrer">' +
              rotulo + "</a>";
          });

        // linha de tabela: | a | b | ... | — a separadora (|---|---|) é descartada
        if (/^\s*\|.*\|\s*$/.test(formatada)) {
          if (!/^[\s|:-]+$/.test(formatada)) {
            linhasTabela.push(formatada.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }));
          }
          return;
        }
        fecharTabela();

        var itemLista = formatada.match(/^\s*[-*]\s+(.*)$/);
        var itemNumerado = formatada.match(/^\s*(\d+)[.)]\s+(.*)$/);
        var titulo = formatada.match(/^(#{1,4})\s+(.*)$/);

        if (itemLista || itemNumerado) {
          if (!listaAberta) { html += "<ul>"; listaAberta = true; }
          html += "<li>" + (itemLista ? itemLista[1] : itemNumerado[1] + ". " + itemNumerado[2]) + "</li>";
          return;
        }
        if (listaAberta) { html += "</ul>"; listaAberta = false; }

        if (titulo) {
          html += "<div class='chat-md-titulo'>" + titulo[2] + "</div>";
        } else if (formatada.trim() === "") {
          html += "<div class='chat-md-espaco'></div>";
        } else {
          html += "<div>" + formatada + "</div>";
        }
      });
      fecharTabela();
      if (listaAberta) html += "</ul>";
    });
    return html;
  }

  // Transforma os marcadores de evidência [msg:wpp:ID]/[msg:sms:ID] que a IA cita em links que abrem
  // a mensagem original na tela do WhatsApp/SMS (deep-link), e [doc:ID] / [doc:ID:PÁGINA] em link
  // que abre a tela do documento (documento.html) na página e no trecho citados. Aplicado após o
  // markdown (o escape de HTML não toca nos colchetes, então o marcador chega intacto aqui).
  function linkarMarcadores(html) {
    return html
      // [filtro:{...}] é a proposta de recorte do grafo que o painel flutuante transforma em botão
      // nas telas de grafo (ver js/utils/ia_flutuante.js). Esta tela não tem grafo nenhum para
      // filtrar — e como o marcador viaja no texto gravado da mensagem, uma conversa começada lá e
      // reaberta aqui mostraria o JSON cru no meio da resposta. Some.
      .replace(/\[filtro:\{[\s\S]*?\}\]/g, "")
      .replace(/\[doc:\d+(?::\d+(?:-\d+)?)?\]/g, function (marcador) {
        // o trecho citado não está no marcador: DocTrecho o resolve no clique, a partir do
        // parágrafo em que a pastilha caiu (ver js/utils/doc_trecho.js)
        return DocTrecho.linkarMarcadores(marcador, { inquerito: idInquerito });
      })
      .replace(/\[msg:(wpp|sms|msg):(\d+)\]/g, function (_todo, fonte, id) {
        // o marcador não carrega o app, então [msg:msg:ID] cai sempre na tela dos mensageiros
        // (lista os de todos os apps, e o ?msg= abre a conversa certa) — ver MapaTelas.paginaConversa
        var pagina = MapaTelas.paginaConversa(fonte);
        var href = pagina + "?inquerito=" + encodeURIComponent(idInquerito) + "&fonte=" + fonte + "&msg=" + id;
        return '<a class="chat-ref" href="' + href + '" title="Abrir a mensagem original">🔎</a>' +
          (window.EvidenciaInfo ? EvidenciaInfo.pastilhaHtml(fonte, id) : "");
      });
  }

  // ---------- Gasto do turno (o que cada resposta custou) ----------
  // A auditoria grava um registro por turno e, desde a migração 0018, ele aponta para a mensagem
  // do assistente — então a bolha mostra o gasto DELA (tokens de entrada/saída, tempo e custo
  // estimado), e não só o agregado da conversa que já aparecia no topo. Resposta antiga sem
  // registro casado, e resposta do modo "não enviar+exibir prompt" (nada foi ao provedor),
  // simplesmente não ganham esses campos.
  var parametrosIA = { qt_janela_contexto: 0, vl_cotacao_brl: null };

  // Fração da janela do modelo a partir da qual a bolha avisa que o contexto está enchendo. Daí
  // para cima o histórico começa a ser compactado/podado antes de ir ao provedor (ver
  // funcoes/ia_utils.py), o que custa detalhe — e a próxima pergunta pode não caber.
  var AVISO_CONTEXTO = 0.8;

  function carregarParametrosIA() {
    // sem estes parâmetros a bolha só perde o aviso de contexto e o valor em reais: o resto do
    // gasto (tokens, tempo, US$) vem junto com as mensagens
    $.getJSON("/api/investigacao/ia/parametros").done(function (p) {
      if (p) parametrosIA = p;
    });
  }

  function fmtNumero(n) { return Number(n || 0).toLocaleString("pt-BR"); }

  function fmtTempo(ms) {
    var seg = Number(ms || 0) / 1000;
    if (seg < 60) return seg.toFixed(1).replace(".", ",") + " s";
    var min = Math.floor(seg / 60);
    return min + " min " + Math.round(seg - min * 60) + " s";
  }

  // A auditoria guarda o custo em dólar (é o preço tabelado dos provedores). O valor em reais só
  // aparece quando há cotação configurada (AI_COTACAO_BRL no .env) — inventar uma taxa de câmbio
  // seria pior do que não mostrar.
  function fmtCusto(usd) {
    if (usd === null || usd === undefined) return "";  // modelo sem preço tabelado
    var n = Number(usd);
    var texto = "US$ " + n.toFixed(4).replace(".", ",");
    if (parametrosIA.vl_cotacao_brl) {
      texto += " · R$ " + (n * Number(parametrosIA.vl_cotacao_brl)).toFixed(2).replace(".", ",");
    }
    return texto;
  }

  function partesDoGasto(m) {
    var entrada = Number(m.qt_tokens_entrada || 0);
    var saida = Number(m.qt_tokens_saida || 0);
    var partes = [];
    if (entrada || saida) {
      // ~ = o provedor não informou o uso e os tokens foram estimados pelo tamanho do texto
      var til = m.in_tokens_estimados ? "~" : "";
      partes.push("↑ " + til + fmtNumero(entrada) + " / ↓ " + til + fmtNumero(saida) + " tokens");
    }
    if (m.nu_tempo_ms) partes.push(fmtTempo(m.nu_tempo_ms));
    var custo = fmtCusto(m.vl_custo_usd);
    if (custo) partes.push(custo);
    return partes;
  }

  function avisoContexto(m) {
    var janela = Number(parametrosIA.qt_janela_contexto || 0);
    var entrada = Number(m.qt_tokens_entrada || 0);
    if (!janela || !entrada || entrada / janela < AVISO_CONTEXTO) return null;
    return $("<div>").addClass("chat-bolha-aviso").text(
      "⚠️ Contexto em " + Math.round((entrada / janela) * 100) + "% da janela do modelo (" +
      fmtNumero(entrada) + " de " + fmtNumero(janela) + " tokens). Comece uma conversa nova: daqui " +
      "para cima o histórico é resumido antes de ir para a IA, e detalhe se perde."
    );
  }

  // ---------- Cobertura de fontes (lastro de evidências) ----------
  // Conta no texto BRUTO os marcadores que viram link clicável: [msg:wpp:ID]/[msg:sms:ID]/
  // [msg:msg:ID] e [doc:ID]/[doc:ID:PÁGINA]. O badge por bolha mostra o desdobramento por
  // fonte; o resumo da toolbar mostra o % da conversa com ao menos uma citação.
  var RE_MSG_EVIDENCIA = /\[msg:(?:wpp|sms|msg):\d+\]/g;
  var RE_DOC_EVIDENCIA = /\[doc:\d+(?::\d+(?:-\d+)?)?\]/g;

  function contarEvidencias(texto) {
    var t = String(texto || "");
    var msgs = (t.match(RE_MSG_EVIDENCIA) || []).length;
    var docs = (t.match(RE_DOC_EVIDENCIA) || []).length;
    return { msgs: msgs, docs: docs, total: msgs + docs };
  }

  function textoCobertura(ev) {
    if (!ev.total) return "sem citação";
    var partes = [];
    if (ev.msgs) partes.push(ev.msgs + (ev.msgs === 1 ? " msg" : " msgs"));
    if (ev.docs) partes.push(ev.docs + (ev.docs === 1 ? " doc" : " docs"));
    return "📎 " + ev.total + (ev.total === 1 ? " evidência" : " evidências") + " · " + partes.join(" · ");
  }

  function atualizarCoberturaResumo() {
    if (!$coberturaResumo.length) return;
    var respostas = msgsCache.filter(function (m) { return m.tp_papel === "assistant"; });
    if (!idConversaAtual || !respostas.length) {
      $coberturaResumo.addClass("hidden").removeClass("com-lastro sem-lastro").text("");
      return;
    }
    var comLastro = respostas.filter(function (m) { return contarEvidencias(m.tx_conteudo).total > 0; }).length;
    var pct = Math.round((comLastro / respostas.length) * 100);
    $coberturaResumo.removeClass("hidden")
      .toggleClass("com-lastro", comLastro > 0)
      .toggleClass("sem-lastro", !comLastro)
      .text("📎 " + comLastro + " de " + respostas.length + " respostas com citação (" + pct + "%)");
  }

  // ---------- Feedback 👍/👎 ----------
  function feedbackDe(idMensagem) {
    return (idMensagem != null && feedbacksCache[idMensagem]) || { positivo: 0, negativo: 0, meu: null };
  }

  function aplicarFeedbacksNaTela() {
    // aplica o cache aos botões já desenhados, sem recarregar a conversa inteira
    $mensagens.find(".chat-linha.assistant").each(function () {
      var id = $(this).attr("data-id-mensagem");
      if (id == null || id === "") return;
      var fb = feedbackDe(id);
      var $linha = $(this);
      $linha.find(".chat-feedback-btn[data-fb='positivo']")
        .toggleClass("ativo", fb.meu === "positivo")
        .find(".chat-feedback-conta").text(fb.positivo ? String(fb.positivo) : "");
      $linha.find(".chat-feedback-btn[data-fb='negativo']")
        .toggleClass("ativo", fb.meu === "negativo")
        .find(".chat-feedback-conta").text(fb.negativo ? String(fb.negativo) : "");
    });
  }

  function carregarFeedbacks(idConversa) {
    if (!idConversa) return;
    $.getJSON("/api/investigacao/ia/conversas/" + idConversa + "/feedbacks").done(function (mapa) {
      if (idConversa !== idConversaAtual) return; // trocou de conversa no meio da leitura
      feedbacksCache = mapa || {};
      aplicarFeedbacksNaTela();
    });
  }

  function enviarFeedback(idMensagem, voto, $btn) {
    if (idMensagem == null || !$btn) return;
    var atual = feedbackDe(idMensagem).meu;
    var novo = (atual === voto) ? null : voto; // votar de novo no mesmo remove
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/ia/mensagens/" + idMensagem + "/feedback",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ tp_feedback: novo })
    }).done(function (resp) {
      feedbacksCache[idMensagem] = { positivo: resp.positivo || 0, negativo: resp.negativo || 0, meu: resp.meu || null };
      var $linha = $btn.closest(".chat-linha");
      $linha.find(".chat-feedback-btn[data-fb='positivo']")
        .toggleClass("ativo", resp.meu === "positivo")
        .find(".chat-feedback-conta").text(resp.positivo ? String(resp.positivo) : "");
      $linha.find(".chat-feedback-btn[data-fb='negativo']")
        .toggleClass("ativo", resp.meu === "negativo")
        .find(".chat-feedback-conta").text(resp.negativo ? String(resp.negativo) : "");
    }).fail(function (xhr) {
      if (window.Toast) Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao registrar avaliação.");
    }).always(function () {
      $btn.prop("disabled", false);
    });
  }

  // copia com fallback para HTTP interno (navigator.clipboard só existe em origem segura)
  function copiarTexto(texto, okMsg) {
    return window.AnalyserUtils.copiarTexto(texto, okMsg);
  }

  // ---- cartão de progresso de job pesado (OCR) disparado pela IA na resposta ---------------
  // A ferramenta processar_texto_em_imagens roda o OCR em background e o turno encerra com uma
  // mensagem só. CO_JOBS da resposta (migração 0045) traz os jobs que aquele turno disparou; este
  // cartão acompanha cada um por polling de /api/investigacao/jobs/{id_job}, continua depois de o
  // turno acabar e reaparece ao reabrir a conversa. Só um polling por id_job (jobPollers), mesmo
  // que a conversa seja re-renderizada.
  var jobPollers = {};  // id_job -> setInterval

  function normalizarJobOcr(j) {
    return {
      rodando: !!j.rodando,
      status: j.cd_status,
      total: j.qt_total || 0,
      processados: j.qt_processados || 0,
      erros: j.qt_erros || 0,
      com_texto: (j.tx_metricas && j.tx_metricas.com_texto) || 0,
      naFrente: j.qt_na_frente || 0,
      erro: j.tx_erro || "",
      podeCancelar: j.cancelado !== true,
      // o job resolveu a extração? aproveita para apontar o link direto pro aparelho
      extracao: (j.tx_parametros && j.tx_parametros.id_extracao) || null,
      // consolidação de planilha: o job concluído traz {link, no_arquivo, qt_linhas} aqui
      resultado: j.tx_resultado || null
    };
  }

  function textoFinalOcr(p) {
    if (p.status === "cancelado") return "OCR cancelado — " + p.processados + " de " + p.total + " imagens.";
    if (p.status === "erro") return "Falha no OCR: " + (p.erro || "erro desconhecido.");
    if (p.total > 0) {
      return "OCR concluído — " + p.processados + " imagens, " + p.com_texto
        + " com texto reconhecido" + (p.erros ? (" (" + p.erros + " com erro)") : "") + ".";
    }
    return "OCR concluído — nenhuma imagem processada.";
  }

  function textoFinalPlanilha(p) {
    if (p.status === "cancelado") return "Consolidação cancelada — " + p.processados + " de " + p.total + " documentos lidos.";
    if (p.status === "erro") return "Falha na consolidação: " + (p.erro || "erro desconhecido.");
    var r = p.resultado || {};
    if (p.status === "concluido" && r.link) {
      return "Planilha pronta — " + r.qt_linhas + " linha(s) de " + r.qt_documentos
        + " documento(s). Baixe ao lado, ela fica disponível por 30 dias.";
    }
    return "Consolidação concluída.";
  }

  function pintarCartaoJobOcr($cartao, p) {
    var $barra = $cartao.find(".job-ocr-barra-preench");
    var $txt = $cartao.find(".job-ocr-texto");
    var $pct = $cartao.find(".job-ocr-pct");
    var $cancelar = $cartao.find(".job-ocr-cancelar");
    var planilha = $cartao.attr("data-tp-job") === "consolidacao_ia";
    $cartao.removeClass("rodando concluido erro cancelado aguardando");

    // o cartão nasceu sem extração conhecida (link no inquérito); se o job resolveu um único
    // aparelho, refina o link para abrir o Arquivos direto nele
    if (p.extracao != null) {
      var $link = $cartao.find(".job-ocr-link");
      var atual = $link.attr("href") || "";
      if (atual.indexOf("/investigacao.html") === 0) {
        var destino = linkArquivosOcr(p.extracao);
        $link.attr("href", destino.href).text(destino.rotulo);
      }
    }

    if (p.rodando) {
      $cartao.addClass(p.aguardando || p.status === "pendente" || p.status === "aguardando_vaga" ? "aguardando" : "rodando");
      var pct = p.total > 0 ? Math.round((p.processados / p.total) * 100) : 0;
      $barra.css("width", pct + "%");
      $pct.text(pct + "%");
      if (p.status === "aguardando_vaga" || p.status === "pendente") {
        $txt.text(planilha ? "Na fila da IA, começando em instantes…"
          : ("Na fila" + (p.naFrente ? (" · " + p.naFrente + " na frente") : "") + " — aguardando vaga de CPU."));
      } else {
        $txt.text((planilha ? "Lendo documentos com IA… " : "Reconhecendo texto… ")
          + p.processados + " de " + p.total);
      }
      if (p.podeCancelar) $cancelar.show(); else $cancelar.hide();
      return;
    }
    // terminou: para o polling deste job
    if (jobPollers[p.id_job]) { clearInterval(jobPollers[p.id_job]); delete jobPollers[p.id_job]; }
    $cancelar.hide();
    if (p.status === "erro") {
      $cartao.addClass("erro");
      $barra.css("width", "100%");
      $pct.text("!");
    } else if (p.status === "cancelado") {
      $cartao.addClass("cancelado");
      $pct.text("—");
    } else {
      $cartao.addClass("concluido");
      $barra.css("width", "100%");
      $pct.text("✓");
    }
    $txt.text(planilha ? textoFinalPlanilha(p) : textoFinalOcr(p));
    // planilha pronta: o link vira o download do .xlsx gerado pelo job (rota reconfere o acesso)
    if (planilha) {
      var $link = $cartao.find(".job-ocr-link");
      var r = p.resultado || {};
      if (p.status === "concluido" && r.link) {
        $link.attr("href", r.link).attr("target", "_blank").show()
          .text("⬇ Baixar planilha (.xlsx)");
      } else {
        $link.hide();
      }
    }
  }

  function acompanharJobOcr(idJob, $cartao) {
    function umaBatida() {
      $.getJSON("/api/investigacao/jobs/" + idJob)
        .done(function (j) {
          var p = normalizarJobOcr(j);
          p.id_job = idJob;
          pintarCartaoJobOcr($cartao, p);
        })
        .fail(function () { /* falha transitória: mantém o polling na próxima batida */ });
    }
    umaBatida();
    if (jobPollers[idJob]) clearInterval(jobPollers[idJob]);
    jobPollers[idJob] = setInterval(umaBatida, 2500);
  }

  // arquivos.html exige inquérito E extração (ver js/utils/ufdr_dominio.js) — sem extração a tela
  // abre com "parâmetros inválidos". Quando o recorte caiu num único aparelho, co_jobs traz o
  // id_extracao e o link abre o aparelho certo; senão cai no cartão do inquérito (aceita só inquérito).
  function linkArquivosOcr(idExtracao) {
    if (idExtracao != null && idExtracao !== "") {
      return { href: "/arquivos.html?inquerito=" + encodeURIComponent(idInquerito)
        + "&extracao=" + encodeURIComponent(idExtracao), rotulo: "Ver na tela Arquivos" };
    }
    return { href: "/investigacao.html?inquerito=" + encodeURIComponent(idInquerito),
      rotulo: "Ver no inquérito" };
  }

  function montarCartaoJobOcr(idJob, idExtracao, tpJob) {
    var planilha = tpJob === "consolidacao_ia";
    var $c = $("<div>").addClass("chat-job-ocr").attr("data-id-job", idJob);
    if (planilha) $c.attr("data-tp-job", tpJob);
    var $topo = $("<div>").addClass("job-ocr-topo");
    $("<span>").addClass("job-ocr-icone").text(planilha ? "📊" : "🔎").appendTo($topo);
    var $titulo = $("<div>").addClass("job-ocr-rotulo")
      .text(planilha ? "Planilha consolidada — agente de IA trabalhando em segundo plano"
                     : "OCR de imagens — processamento em segundo plano");
    $titulo.appendTo($topo);
    var $cancelar = $("<button>").addClass("job-ocr-cancelar").attr("type", "button")
      .attr("title", planilha ? "Parar esta consolidação" : "Parar este OCR")
      .text("Cancelar").hide()
      .on("click", function () {
        $cancelar.prop("disabled", true).text("Cancelando…");
        $.ajax({ url: "/api/investigacao/jobs/" + idJob + "/cancelar", method: "POST" });
      });
    $cancelar.appendTo($topo);
    $topo.appendTo($c);
    var $barra = $("<div>").addClass("job-ocr-barra");
    $("<div>").addClass("job-ocr-barra-preench").appendTo($barra);
    $barra.appendTo($c);
    var $rodape = $("<div>").addClass("job-ocr-rodape");
    $("<span>").addClass("job-ocr-texto").text("Preparando…").appendTo($rodape);
    var $link = $("<a>").addClass("job-ocr-link").attr("target", "_blank");
    if (planilha) {
      // sem destino até o job concluir — o pintarCartaoJobOcr revela o link de download no fim
      $link.attr("href", "#").hide();
    } else {
      var destino = linkArquivosOcr(idExtracao);
      $link.attr("href", destino.href).text(destino.rotulo);
    }
    $link.appendTo($rodape);
    $("<span>").addClass("job-ocr-pct").text("").appendTo($rodape);
    $rodape.appendTo($c);
    acompanharJobOcr(idJob, $c);
    return $c;
  }

  function cartoesJobs(m) {
    if (!m || !Array.isArray(m.co_jobs) || !m.co_jobs.length) return null;
    var $wrap = $("<div>").addClass("chat-jobs");
    m.co_jobs.forEach(function (jobe) {
      if (jobe && jobe.id_job) $wrap.append(montarCartaoJobOcr(jobe.id_job, jobe.id_extracao, jobe.tp_job));
    });
    return $wrap.children().length ? $wrap : null;
  }

  function renderMensagem(m) {
    var $linha = $("<div>").addClass("chat-linha " + m.tp_papel);
    if (m.id_mensagem_ia != null) $linha.attr("data-id-mensagem", m.id_mensagem_ia);
    var $bolha = $("<div>").addClass("chat-bolha");
    if (m.tp_papel === "assistant") {
      $("<div>").addClass("chat-md").html(linkarMarcadores(markdownParaHtml(m.tx_conteudo))).appendTo($bolha);
    } else {
      $("<div>").text(m.tx_conteudo).appendTo($bolha);
    }
    // auditoria de pensamento: recolhida por padrão, some quando o turno não tem trilha
    // (resposta direta, ou mensagem anterior à migração 0013) — ver js/utils/ia_raciocinio.js
    var $raciocinio = window.IARaciocinio && IARaciocinio.render(m.tx_raciocinio);
    if ($raciocinio) $bolha.append($raciocinio);
    // cartão de progresso do OCR que este turno disparou (vindo de co_jobs → migração 0045)
    if (m.tp_papel === "assistant") {
      var $jobs = cartoesJobs(m);
      if ($jobs) $bolha.append($jobs);
    }
    var meta = formatarDataHora(m.dt_mensagem);
    if (m.enviado_por) meta += " · " + m.enviado_por;
    if (m.no_modelo) meta += " · " + m.no_modelo;
    if (m.tp_papel === "assistant") {
      var gasto = partesDoGasto(m);
      if (gasto.length) meta += " · " + gasto.join(" · ");
      var $aviso = avisoContexto(m);
      if ($aviso) $bolha.append($aviso);
    }
    $("<div>").addClass("chat-bolha-meta").text(meta).appendTo($bolha);
    // ações da bolha: copiar (toda mensagem gravada) + 👍/👎 e lastro (só resposta da IA)
    if (m.id_mensagem_ia != null || m.tp_papel === "user") {
      (function (msg) {
        var $acoes = $("<div>").addClass("chat-bolha-acoes");
        $("<button>").addClass("chat-copiar-btn").attr("type", "button")
          .attr("title", "Copiar o texto desta mensagem").text("⧉ Copiar")
          .on("click", function () { copiarTexto(String(msg.tx_conteudo || ""), "Mensagem copiada."); })
          .appendTo($acoes);
        if (msg.tp_papel === "assistant" && msg.id_mensagem_ia != null) {
          var fb = feedbackDe(msg.id_mensagem_ia);
          [["positivo", "👍", "Avaliar como útil"], ["negativo", "👎", "Avaliar como não útil"]].forEach(function (cfg) {
            var tipo = cfg[0];
            var $fb = $("<button>").addClass("chat-feedback-btn").attr("type", "button")
              .attr("data-fb", tipo).attr("title", cfg[2])
              .toggleClass("ativo", fb.meu === tipo);
            $("<span>").text(cfg[1]).appendTo($fb);
            $("<span>").addClass("chat-feedback-conta")
              .text((tipo === "positivo" ? fb.positivo : fb.negativo) ? String(tipo === "positivo" ? fb.positivo : fb.negativo) : "")
              .appendTo($fb);
            $fb.on("click", function () { enviarFeedback(msg.id_mensagem_ia, tipo, $fb); });
            $fb.appendTo($acoes);
          });
          var ev = contarEvidencias(msg.tx_conteudo);
          $("<span>").addClass("chat-cobertura")
            .toggleClass("com-lastro", ev.total > 0).toggleClass("sem-lastro", !ev.total)
            .attr("title", ev.total ? "Citações clicáveis nesta resposta (mensagens e documentos)" : "Resposta sem citação clicável [msg:] ou [doc:]")
            .text(textoCobertura(ev)).appendTo($acoes);
        }
        $bolha.append($acoes);
      })(m);
    }
    $linha.append($bolha);
    return $linha;
  }

  // Título exibido de uma conversa: presa a um chat específico (tp_escopo "conversa_*"),
  // ganha o prefixo do aplicativo ("WhatsApp - João", "SMS - Maria", "<app> - Grupo X");
  // o app do mensageiro vem de no_app (backend, msg_tb02_conversas.tp_app).
  function tituloConversa(c) {
    var nome = (c && c.no_titulo) || "Nova conversa";
    if (!c || !c.tp_escopo || c.tp_escopo.indexOf("conversa_") !== 0) return nome;
    var app = c.tp_escopo === "conversa_wpp" ? "WhatsApp"
      : c.tp_escopo === "conversa_sms" ? "SMS"
      : (c.no_app || "Mensageiro");
    return app + " - " + nome;
  }

  // Cada conversa é um <button> de verdade (foco, Enter e Espaço de graça) dentro do grupo
  // rotulado da barra lateral: as ações renomear/apagar são botões, e botão dentro de botão não
  // existe em HTML — por isso a lista não usa role="listbox"/option (que também não permitiria
  // o aviso de "nenhuma conversa" entre as opções).
  function renderConversaItem(c) {
    var $item = $("<div>").addClass("chat-conversa-item")
      .attr("data-id", c.id_conversa_ia);
    var titulo = tituloConversa(c);
    var $abrir = $("<button>").attr({ type: "button", "class": "chat-conversa-abrir" })
      .attr("title", "Abrir a conversa " + titulo)
      .on("click", function () {
        abrirConversa(c.id_conversa_ia, titulo);
      })
      .appendTo($item);
    $("<div>").addClass("chat-conversa-nome").text(titulo).appendTo($abrir);
    // a lista é ordenada pela última atividade (backend), então a data mostrada é a da última
    // mensagem — cai para a data de criação enquanto a conversa não tem nenhuma
    var meta = c.qt_mensagens + " mensagens · " + formatarDataHora(c.dt_ultima_mensagem || c.dt_criacao);
    if (c.criado_por) meta += " · criada por " + c.criado_por;
    $("<div>").addClass("chat-conversa-meta").text(meta).appendTo($abrir);
    var $acoes = $("<div>").addClass("chat-conversa-acoes").appendTo($item);
    $("<button>")
      .addClass("chat-conversa-acao")
      .attr({ type: "button", title: "Renomear conversa", "aria-label": "Renomear conversa " + titulo })
      .text("✎")
      .on("click", function (e) {
        e.stopPropagation(); // não abrir a conversa ao renomear
        renomearConversa(c.id_conversa_ia, titulo);
      })
      .appendTo($acoes);
    $("<button>")
      .addClass("chat-conversa-acao chat-conversa-acao-perigo")
      .attr({ type: "button", title: "Apagar conversa", "aria-label": "Apagar conversa " + titulo })
      .html("&times;")
      .on("click", function (e) {
        e.stopPropagation(); // não abrir a conversa que está sendo apagada
        apagarConversa(c.id_conversa_ia, titulo);
      })
      .appendTo($acoes);
    return $item;
  }

  function renomearConversa(idConversa, tituloAtual) {
    Dialogo.prompt({
      titulo: "Renomear conversa",
      mensagem: "Novo nome da conversa:",
      valor: tituloAtual === "Nova conversa" ? "" : tituloAtual,
      placeholder: "Nome da conversa",
      maxlength: 200,
      rotuloOk: "Renomear"
    }).then(function (novo) {
      if (novo == null) return; // cancelou
      novo = $.trim(novo);
      if (!novo || novo === tituloAtual) return;
      $.ajax({
        url: "/api/investigacao/ia/conversas/" + idConversa,
        method: "PATCH",
        contentType: "application/json",
        data: JSON.stringify({ titulo: novo })
      })
        .done(function () {
          carregarConversas();
          if (idConversaAtual === idConversa) { $header.text(novo); sincronizarUrlConversa(); }
          if (window.Toast) Toast.sucesso("Conversa renomeada.");
        })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao renomear a conversa." });
        });
    });
  }

  function apagarConversa(idConversa, titulo) {
    Dialogo.confirmar({
      titulo: "Apagar conversa",
      mensagem: "Apagar a conversa \"" + titulo + "\"? As mensagens dela serão removidas. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/ia/conversas/" + idConversa, method: "DELETE" })
        .done(function () {
          if (idConversaAtual === idConversa) {
            limparConversaAberta("Selecione uma conversa à esquerda, ou crie uma nova para começar.");
          }
          carregarConversas();
        })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar a conversa." });
        });
    });
  }

  // Filtro "usuário que criou a conversa" (barra lateral, abaixo de "Nova conversa"). Só o
  // recorte da lista é feito aqui — a ordenação (última atividade primeiro) vem do backend.
  var filtroCriador = ""; // id_usuario_criador selecionado; "" = todos
  var filtroBusca = "";   // texto da busca por título

  function popularFiltroCriador() {
    var vistos = {};
    var criadores = [];
    conversasCache.forEach(function (c) {
      var id = c.id_usuario_criador;
      if (id == null || vistos[id]) return;
      vistos[id] = true;
      criadores.push({ id: String(id), nome: c.criado_por || ("Usuário " + id) });
    });
    criadores.sort(function (a, b) { return a.nome.localeCompare(b.nome, "pt-BR"); });
    if (filtroCriador && !vistos[filtroCriador]) filtroCriador = ""; // criador sumiu da lista
    $filtroCriador.empty();
    $("<option>").val("").text("Todos os usuários").appendTo($filtroCriador);
    criadores.forEach(function (c) {
      $("<option>").val(c.id).text(c.nome).appendTo($filtroCriador);
    });
    $filtroCriador.val(filtroCriador);
    $filtroCriador.toggleClass("hidden", criadores.length < 2);
  }

  function marcarConversaAtiva() {
    var $itens = $(".chat-conversa-item");
    $itens.removeClass("active").find("> .chat-conversa-abrir").removeAttr("aria-current");
    if (!idConversaAtual) return;
    $itens.filter("[data-id='" + idConversaAtual + "']").addClass("active")
      .find("> .chat-conversa-abrir").attr("aria-current", "true");
  }

  function renderListaConversas() {
    $lista.empty();
    var termo = $.trim(filtroBusca).toLowerCase();
    var visiveis = conversasCache.filter(function (c) {
      if (filtroCriador && String(c.id_usuario_criador) !== filtroCriador) return false;
      if (termo && tituloConversa(c).toLowerCase().indexOf(termo) === -1) return false;
      return true;
    });
    if (!visiveis.length) {
      $lista.append($("<p>").addClass("chat-conversas-vazio").text(
        (filtroCriador || termo) ? "Nenhuma conversa com esse filtro." : "Nenhuma conversa ainda."
      ));
      return;
    }
    visiveis.forEach(function (c) { $lista.append(renderConversaItem(c)); });
    marcarConversaAtiva();
  }

  $filtroCriador.on("change", function () {
    filtroCriador = $(this).val() || "";
    renderListaConversas();
  });

  if ($busca.length) {
    // a lista é filtrada no cliente, mas com centenas de conversas redesenhar por tecla travava
    // o teclado — mesmo debounce das outras buscas do sistema (AnalyserUtils.debounce)
    var republicarBusca = window.AnalyserUtils.debounce(renderListaConversas, 150);
    $busca.on("input", function () {
      filtroBusca = $(this).val() || "";
      republicarBusca();
    });
    $busca.on("keydown", function (e) {
      if (e.key === "Escape" && $(this).val()) {
        e.stopPropagation(); // limpa a busca em vez de fechar a gaveta de fontes
        $(this).val("");
        filtroBusca = "";
        renderListaConversas();
      }
    });
  }

  // ↑/↓ (e Home/End) percorrem as conversas a partir do item em foco — o Tab também chega a
  // todas, mas numa lista de dezenas de conversas seta é o jeito de andar sem perder o contexto.
  if ($lista.length) {
    $lista.on("keydown", ".chat-conversa-abrir", function (e) {
      var passo = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
      var saltar = e.key === "Home" || e.key === "End";
      if (!passo && !saltar) return;
      var $abrir = $lista.find(".chat-conversa-abrir");
      var i = $abrir.index(this);
      var alvo = saltar ? (e.key === "Home" ? 0 : $abrir.length - 1)
        : Math.max(0, Math.min($abrir.length - 1, i + passo));
      e.preventDefault();
      $abrir.eq(alvo).trigger("focus");
    });
  }

  function carregarConversas(selecionarId) {
    var url = "/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas";
    // a conversa aberta agora não pode ser apagada pela limpeza de conversas vazias do backend
    if (idConversaAtual) url += "?conversa_ativa=" + encodeURIComponent(idConversaAtual);
    $.getJSON(url).done(function (lista) {
      conversasCache = lista;
      if (selecionarId) {
        // não deixa o filtro esconder a conversa que estamos prestes a abrir
        var alvo = lista.find(function (c) { return c.id_conversa_ia === selecionarId; });
        if (alvo && filtroCriador && String(alvo.id_usuario_criador) !== filtroCriador) {
          filtroCriador = "";
        }
      }
      popularFiltroCriador();
      renderListaConversas();
      if (selecionarId) {
        var alvoAbrir = lista.find(function (c) { return c.id_conversa_ia === selecionarId; });
        abrirConversa(selecionarId, alvoAbrir ? tituloConversa(alvoAbrir) : "Nova conversa");
      }
    });
  }

  // Resolve o botão "Prompt de IA específico": reaproveita a conversa já restrita a este mesmo
  // documento se existir, senão cria uma nova já com o escopo gravado (ver POST .../ia/conversas
  // e funcoes/ia_utils.criar_conversa no backend).
  function abrirOuCriarConversaComEscopo() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas").done(function (lista) {
      conversasCache = lista;
      var existente = lista.find(function (c) {
        return c.tp_escopo === escopoTipoParam && String(c.id_escopo) === String(escopoIdParam);
      });
      if (existente) {
        carregarConversas(existente.id_conversa_ia);
        return;
      }
      $.ajax({
        url: "/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({
          titulo: escopoTituloParam || "",
          tp_escopo: escopoTipoParam,
          id_escopo: Number(escopoIdParam),
        }),
      })
        .done(function (resp) {
          carregarConversas(resp.id_conversa_ia);
        })
        .fail(function (xhr) {
          carregarConversas();
          Dialogo.alerta({
            titulo: "Erro",
            mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao abrir a conversa restrita a este documento.",
          });
        });
    });
  }

  var SUGESTOES = [
    "Faça um resumo geral do caso: volumes, período e com quem o dono do aparelho mais falava.",
    "Procure nas mensagens e áudios menções a dinheiro, depósito, transferência ou cartão.",
    "Quais são as conversas mais suspeitas? Justifique com trechos reais.",
    "Liste as ligações mais frequentes e cruze com os contatos das conversas.",
    "Que lugares aparecem nas localizações do aparelho?",
    "Que apps de banco aparecem nas notificações do aparelho?"
  ];

  function renderSugestoes() {
    var $wrap = $("<div>").addClass("chat-sugestoes");
    var $vazio = $("<div>").addClass("chat-vazio").appendTo($wrap);
    $("<p>").addClass("chat-vazio-titulo")
      .text("Envie a primeira mensagem, ou comece por uma destas:").appendTo($vazio);
    $("<p>").addClass("chat-vazio-dica")
      .text("Dica: peça citações [msg:] e [doc:] para cada afirmação — elas viram links para a prova.").appendTo($vazio);
    SUGESTOES.forEach(function (s) {
      $("<button>")
        .addClass("chat-sugestao")
        .attr("type", "button")
        .text(s)
        .on("click", function () {
          enviarTexto(s, { exibirPrompt: $exibirPrompt.is(":checked") });
        })
        .appendTo($wrap);
    });
    return $wrap;
  }

  // ---------- Toolbar da conversa: exportar / duplicar / compartilhar ----------
  // Exportar .md e imprimir são 100% locais (a conversa já está em msgsCache); duplicar cria
  // uma cópia no backend; copiar link monta o deep-link ?inquerito=&conversa= que esta mesma
  // tela já sabe abrir (conversaParam) — qualquer investigador com acesso ao inquérito abre.
  function atualizarToolbar() {
    var aberta = !!idConversaAtual;
    if ($toolbar.length) $toolbar.toggleClass("hidden", !aberta);
    if (!aberta && $coberturaResumo.length) {
      $coberturaResumo.addClass("hidden").text("");
    }
  }

  function limparConversaAberta(msgVazio) {
    idConversaAtual = null;
    msgsCache = [];
    feedbacksCache = {};
    ultimoEnvio = null;
    $header.text("Selecione ou crie uma conversa");
    sincronizarUrlConversa();
    marcarConversaAtiva();
    $form.hide().addClass("hidden");
    atualizarEscopoUi(null);
    atualizarToolbar();
    atualizarCoberturaResumo();
    novasDesdeFim = 0;
    atualizarIrFim();
    var $vazio = $("<div>").addClass("chat-vazio");
    $("<p>").addClass("chat-vazio-titulo").text(msgVazio).appendTo($vazio);
    $mensagens.empty().append($vazio);
  }

  function nomeArquivoConversa() {
    var c = conversaAberta();
    var base = (c && c.no_titulo) || "conversa-ia";
    return String(base).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "conversa-ia";
  }

  function conversaParaMarkdown() {
    var c = conversaAberta();
    var titulo = tituloConversa(c);
    var linhas = ["# " + titulo, ""];
    linhas.push("- Inquérito: " + idInquerito);
    linhas.push("- Conversa: " + idConversaAtual);
    linhas.push("- Exportado em: " + new Date().toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" }));
    linhas.push("");
    msgsCache.forEach(function (m) {
      var quem = m.tp_papel === "assistant" ? ("IA" + (m.no_modelo ? " (" + m.no_modelo + ")" : "")) : ("Investigador" + (m.enviado_por ? " (" + m.enviado_por + ")" : ""));
      linhas.push("## " + quem + " — " + (formatarDataHora(m.dt_mensagem) || ""));
      linhas.push("");
      linhas.push(String(m.tx_conteudo || ""));
      linhas.push("");
      if (m.tp_papel === "assistant") {
        var ev = contarEvidencias(m.tx_conteudo);
        var gasto = partesDoGasto(m);
        var extras = [];
        if (ev.total) extras.push(textoCobertura(ev));
        if (gasto.length) extras.push(gasto.join(" · "));
        var fb = (m.id_mensagem_ia != null && feedbacksCache[m.id_mensagem_ia]) || null;
        if (fb && (fb.positivo || fb.negativo)) extras.push("Avaliação: 👍 " + fb.positivo + " / 👎 " + fb.negativo);
        if (extras.length) { linhas.push("*" + extras.join(" — ") + "*"); linhas.push(""); }
      }
      linhas.push("---");
      linhas.push("");
    });
    return linhas.join("\n");
  }

  function exportarConversaMd() {
    if (!idConversaAtual) return;
    if (!msgsCache.length) {
      if (window.Toast) Toast.aviso("Nada para exportar: a conversa ainda não tem mensagens.");
      return;
    }
    var blob = new Blob([conversaParaMarkdown()], { type: "text/markdown;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = nomeArquivoConversa() + "-inquerito-" + idInquerito + "-conversa-" + idConversaAtual + ".md";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function duplicarConversaAtual() {
    if (!idConversaAtual || $duplicarBtn.prop("disabled")) return;
    $duplicarBtn.prop("disabled", true);
    $.ajax({ url: "/api/investigacao/ia/conversas/" + idConversaAtual + "/duplicar", method: "POST" })
      .done(function (resp) {
        if (window.Toast) Toast.sucesso("Conversa duplicada.");
        carregarConversas(resp && resp.id_conversa_ia);
      })
      .fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao duplicar a conversa." });
      })
      .always(function () { $duplicarBtn.prop("disabled", false); });
  }

  function copiarLinkConversa() {
    if (!idConversaAtual) return;
    var url = window.location.origin + window.location.pathname +
      "?inquerito=" + encodeURIComponent(idInquerito) + "&conversa=" + encodeURIComponent(idConversaAtual);
    copiarTexto(url, "Link da conversa copiado.");
  }

  if ($exportarMdBtn.length) $exportarMdBtn.on("click", exportarConversaMd);
  if ($imprimirBtn.length) $imprimirBtn.on("click", function () { window.print(); });
  if ($duplicarBtn.length) $duplicarBtn.on("click", duplicarConversaAtual);
  if ($copiarLinkBtn.length) $copiarLinkBtn.on("click", copiarLinkConversa);

  // A conversa aberta vira URL e título da aba: o botão "Copiar link" promete exatamente a tela
  // que está na frente do investigador, e um F5 não joga ele de volta na lista. O estado vai pela
  // mesma mão das outras telas (window.EstadoUrl, de js/utils/estado_url.js).
  function sincronizarUrlConversa() {
    if (!idInquerito || !window.EstadoUrl) return;
    // os escopos são o gesto de entrada ("Prompt de IA específico" de RIF/SIMBA/IPL), não o estado
    // da tela: limpos aqui, um F5 reabre a conversa sem passar de novo pela criação dela
    window.EstadoUrl.salvar({
      conversa: idConversaAtual || null,
      escopo_tipo: null,
      escopo_id: null,
      escopo_titulo: null
    });
    document.title = (idConversaAtual ? (($header.text() || "Conversa") + " · ") : "") +
      "Conversa com IA do Inquérito · PolícIA";
  }

  function abrirConversa(idConversa, titulo) {
    idConversaAtual = idConversa;
    msgsCache = [];
    feedbacksCache = {};
    // esta conversa está à vista: o aviso global não precisa repetir em toast (js/utils/avisos_ia.js)
    if (window.AvisosIA) {
      window.AvisosIA.silenciar("chat_inquerito", idConversa);
      // o job de consolidação desta conversa também: o cartão na bolha já mostra progresso/download
      window.AvisosIA.silenciar("planilha_ia", idConversa);
    }
    marcarConversaAtiva();
    // no modo painel único (< 860px) troca a lista pela conversa; em tela larga não muda nada
    $(".chat-app").addClass("conversa-aberta");
    $header.text(titulo);
    sincronizarUrlConversa();
    $form.removeClass("hidden").show();
    atualizarToolbar();
    atualizarCoberturaResumo();
    if (typeof sincronizarInput === "function") sincronizarInput();
    if (prefillPendente) {
      $input.val(prefillPendente);
      prefillPendente = "";
      if (typeof sincronizarInput === "function") sincronizarInput();
    }
    // foca o input para digitar direto (sem roubar foco em mobile)
    if (window.innerWidth > 860) setTimeout(function () { $input.focus(); }, 50);
    $mensagens.empty();
    atualizarEscopoUi(conversaAberta());
    carregarFeedbacks(idConversa);

    // Carrega auditoria para mostrar tokens/contexto
    $.getJSON("/api/investigacao/ia/conversas/" + idConversa + "/auditoria").done(function (resp) {
      var auditoria = Array.isArray(resp) ? resp : (resp.itens || []);
      console.log("Auditoria carregada:", auditoria.length, "itens");
      if (auditoria && auditoria.length > 0) {
        var totalTokensEntrada = 0, totalTokensSaida = 0, totalTempo = 0, totalCusto = 0;
        var maxContexto = 0;
        var temCusto = false;
        auditoria.forEach(function (a) {
          totalTokensEntrada += a.qt_tokens_entrada || 0;
          totalTokensSaida += a.qt_tokens_saida || 0;
          totalTempo += a.nu_tempo_ms || 0;
          maxContexto = Math.max(maxContexto, a.qt_chars_contexto || 0);
          // custo nulo = modelo sem preço tabelado; só soma o que tem preço, e some se nenhum tiver
          if (a.vl_custo_usd !== null && a.vl_custo_usd !== undefined) {
            totalCusto += Number(a.vl_custo_usd);
            temCusto = true;
          }
        });

        var $info = $("<div>").addClass("chat-resumo").attr("role", "note");

        // Formata tamanho do contexto (B → KB → MB → GB)
        var contexto_formato = "0 B";
        if (maxContexto > 0) {
          if (maxContexto >= 1_000_000_000) {
            contexto_formato = (maxContexto / 1_000_000_000).toFixed(2) + " GB";
          } else if (maxContexto >= 1_000_000) {
            contexto_formato = (maxContexto / 1_000_000).toFixed(2) + " MB";
          } else if (maxContexto >= 1_000) {
            contexto_formato = (maxContexto / 1_000).toFixed(2) + " KB";
          } else {
            contexto_formato = maxContexto + " B";
          }
        }
        var tempo_sec = (totalTempo / 1000).toFixed(1);

        $info.empty();
        $("<strong>").addClass("chat-resumo-titulo").text("📊 Resumo da Conversa").appendTo($info);
        $("<div>").html(
          "🔤 <strong>Tokens:</strong> " + $("<span>").text((totalTokensEntrada + totalTokensSaida).toLocaleString()).html() +
          " (" + $("<span>").text(totalTokensEntrada.toLocaleString()).html() + " entrada, " + $("<span>").text(totalTokensSaida.toLocaleString()).html() + " saída)<br>" +
          "📝 <strong>Contexto:</strong> " + $("<span>").text(contexto_formato).html() + " máximo | " +
          "⏱️ <strong>Tempo:</strong> " + $("<span>").text(tempo_sec + "s em " + auditoria.length + " turno(s)").html() +
          (temCusto ? "<br>💵 <strong>Custo estimado:</strong> " + $("<span>").text(fmtCusto(totalCusto)).html() : "")
        ).appendTo($info);
        $mensagens.prepend($info);
      }
    }).fail(function (xhr) {
      // Silenciosamente ignora erros de auditoria (a conversa funciona mesmo sem isso)
      console.log("Auditoria não disponível:", xhr.status);
    });

    $.getJSON("/api/investigacao/ia/conversas/" + idConversa + "/mensagens").done(function (msgs) {
      msgsCache = Array.isArray(msgs) ? msgs : [];
      if (!msgsCache.length) {
        atualizarCoberturaResumo();
        $mensagens.append(renderSugestoes());
        return;
      }
      msgsCache.forEach(function (m) {
        $mensagens.append(renderMensagem(m));
      });
      // os votos chegam numa chamada à parte e podem ter voltado antes das bolhas existirem
      aplicarFeedbacksNaTela();
      atualizarCoberturaResumo();
      rolarParaFim(true);
    }).fail(function (xhr) {
      // 404: a conversa foi apagada (ex.: ficou vazia por mais de 5 min e a limpeza automática
      // do backend a removeu, ou outro usuário/aba a apagou). Volta ao estado inicial.
      if (xhr.status !== 404 || idConversaAtual !== idConversa) return;
      limparConversaAberta("Esta conversa foi encerrada porque ficou vazia. Crie uma nova para começar.");
      carregarConversas();
    });
  }

  // painel único (tela < 860px): volta da conversa para a lista. Em tela larga o CSS esconde
  // o botão — os dois painéis convivem.
  $("#chat-voltar-btn").on("click", function () {
    $(".chat-app").removeClass("conversa-aberta");
  });

  var $novaBtn = $("#nova-conversa-btn");
  var criandoConversa = false; // trava: o POST não é instantâneo e dois cliques criavam duas conversas

  $novaBtn.on("click", function () {
    if (!idInquerito) {
      if (window.Toast) Toast.aviso("Abra esta tela por um inquérito (falta o ?inquerito= na URL).");
      return;
    }
    if (criandoConversa) return;
    criandoConversa = true;
    $novaBtn.prop("disabled", true);
    filtroCriador = ""; // a conversa nova é de quem clicou — não deixa o filtro escondê-la
    filtroBusca = "";
    if ($busca.length) $busca.val("");
    $.ajax({
      url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/ia/conversas",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({})
    }).done(function (resp) {
      carregarConversas(resp.id_conversa_ia);
    }).fail(function (xhr) {
      if (window.Toast) {
        Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não deu para criar a conversa. Tente de novo.");
      }
    }).always(function () {
      criandoConversa = false;
      $novaBtn.prop("disabled", false);
    });
  });

  // ---------- Autosize + contador + Shift+Enter ----------
  // A fonte do limite é o maxlength do <textarea> (ia.html): dois números soltos aqui e lá
  // desandam a contar histórias diferentes.
  var MAX_CHARS = Number($input.attr("maxlength")) || 8000;
  var $contador = $("#chat-contador");
  var $contadorLive = $("#chat-contador-live");
  var avisoContador = ""; // o que já foi anunciado — o leitor de tela só fala quando muda

  function ajustarAltura() {
    var el = $input[0];
    if (!el) return;
    el.style.height = "auto";
    var nova = Math.min(el.scrollHeight, 160);
    el.style.height = nova + "px";
    el.style.overflowY = el.scrollHeight > 160 ? "auto" : "hidden";
  }

  function atualizarContador() {
    if (!$contador.length) return;
    var len = ($input.val() || "").length;
    $contador.text(len + " / " + MAX_CHARS);
    $contador.toggleClass("alerta", len > MAX_CHARS * 0.8 && len < MAX_CHARS * 0.9);
    $contador.toggleClass("limite", len >= MAX_CHARS * 0.9);
    var aviso = "";
    if (len >= MAX_CHARS) {
      $contador.attr("title", "Limite de " + MAX_CHARS + " caracteres atingido");
      aviso = "Limite de " + MAX_CHARS + " caracteres atingido.";
    } else if (len >= MAX_CHARS * 0.9) {
      $contador.attr("title", "Quase no limite de " + MAX_CHARS + " caracteres");
      aviso = "Quase no limite de " + MAX_CHARS + " caracteres.";
    } else {
      $contador.removeAttr("title");
    }
    // o contador a cada tecla é ruído para quem usa leitor de tela: só a travessia de limite
    // vira anúncio, e ela vai para a região viva separada (ver o footer em ia.html)
    if ($contadorLive.length && aviso !== avisoContador) {
      avisoContador = aviso;
      $contadorLive.text(aviso);
    }
  }

  function sincronizarInput() {
    ajustarAltura();
    atualizarContador();
  }

  $input.on("input", sincronizarInput);

  $input.on("keydown", function (e) {
    // Enter no meio de uma composição (acentuação por dead key, IME chinês/japonês, corretor
    // de celular) é o teclado confirmando a letra, não o investigador confirmando a mensagem.
    var compondo = e.originalEvent && (e.originalEvent.isComposing || e.originalEvent.keyCode === 229);
    if (compondo) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $form.trigger("submit");
    }
    // Shift+Enter deixa o comportamento nativo (nova linha) — o autosize atualiza no input seguinte.
    // Para feedback imediato, agenda um ajuste no próximo tick caso o navegador ainda não tenha
    // inserido a quebra.
    if (e.key === "Enter" && e.shiftKey) {
      setTimeout(sincronizarInput, 0);
    }
  });

  // estado inicial (placeholder 1 linha, contador 0)
  sincronizarInput();

  var TEXTO_DIGITANDO = "A IA está respondendo…";
  var TEXTO_MONTANDO_PROMPT = "Montando o prompt completo…";
  var enviando = false; // trava reenvio enquanto uma mensagem está em andamento (Enter, sugestões, clique)
  var ultimoEnvio = null; // {texto, exibirPrompt, escopos, idConversa} — alimenta o "Tentar de novo"

  function finalizarEnvio() {
    enviando = false;
    $enviarBtn.prop("disabled", false);
    $input.prop("disabled", false);
    $digitando.hide().addClass("hidden").text(TEXTO_DIGITANDO);
    if (typeof sincronizarInput === "function") sincronizarInput();
    if (window.innerWidth > 860 && $form.is(":visible")) setTimeout(function () { $input.focus(); }, 50);
  }

  function mostrarErro(msg, podeRepetir) {
    var $bolha = $("<div>").addClass("chat-bolha chat-erro").text(msg);
    if (podeRepetir !== false && ultimoEnvio && ultimoEnvio.idConversa === idConversaAtual) {
      $("<button>").addClass("chat-erro-retry").attr("type", "button")
        .text("Tentar de novo")
        .on("click", function () {
          $(this).closest(".chat-linha").remove();
          reenviarUltima();
        })
        .appendTo($bolha);
    }
    $mensagens.append($("<div>").addClass("chat-linha").append($bolha));
    rolarParaFim(true);
  }

  function reenviarUltima() {
    if (!ultimoEnvio || enviando) return;
    enviarTexto(ultimoEnvio.texto, {
      exibirPrompt: ultimoEnvio.exibirPrompt,
      escopos: ultimoEnvio.escopos,
      idConversa: ultimoEnvio.idConversa,
      repetir: true
    });
  }

  // Rolagem: respeita quem subiu para ler o histórico — só desce sozinho quando já
  // estava no fim; senão mostra o botão flutuante "Ir para o fim" com contador.
  var novasDesdeFim = 0;

  function estaNoFim() {
    var el = $mensagens[0];
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function rolarParaFim(forcar) {
    var el = $mensagens[0];
    if (!el) return;
    if (forcar || estaNoFim()) {
      el.scrollTop = el.scrollHeight;
      novasDesdeFim = 0;
      atualizarIrFim();
    } else {
      novasDesdeFim += 1;
      atualizarIrFim();
    }
  }

  function atualizarIrFim() {
    if (!$irFim.length) return;
    var el = $mensagens[0];
    var longe = el ? (el.scrollHeight - el.scrollTop - el.clientHeight > 160) : false;
    $irFim.toggleClass("hidden", !longe && !novasDesdeFim);
    if ($irFimNovas.length) {
      $irFimNovas.toggleClass("hidden", !novasDesdeFim)
        .text(novasDesdeFim ? String(novasDesdeFim) : "");
    }
  }

  if ($mensagens.length) {
    $mensagens.on("scroll", function () {
      if (estaNoFim()) { novasDesdeFim = 0; }
      atualizarIrFim();
    });
  }

  if ($irFim.length) {
    $irFim.on("click", function () { rolarParaFim(true); });
  }

  // Todo envio é processado em background (evita timeout do proxy em turnos longos). O
  // acompanhamento é por SSE: uma conexão só, e cada etapa ("Lendo as conversas do WhatsApp…")
  // aparece assim que o worker a grava, em vez de esperar a próxima batida. O polling de 2,5 s
  // continua aqui como reserva — se o EventSource não abrir (proxy que segura text/event-stream,
  // navegador sem suporte) ou desistir no meio, ele assume de onde o SSE parou.
  function acompanharProcessamento(idProcessamento, idConversa, exibirPrompt) {
    var URL_BASE = "/api/investigacao/ia/processamentos/" + idProcessamento;
    var encerrado = false;
    var fonte = null;   // EventSource, enquanto o SSE estiver de pé
    var poll = null;    // setInterval do polling de reserva
    var timerAbertura = null;

    // botão de cancelar acima do campo — o turno pode ser longo (map-reduce de dezenas de partes)
    var $cancelar = $("<button>")
      .addClass("chat-cancelar-btn")
      .attr("type", "button")
      .text("Cancelar resposta")
      .on("click", function () {
        $cancelar.prop("disabled", true).text("Cancelando…");
        $.ajax({ url: URL_BASE + "/cancelar", method: "POST" });
        // o acompanhamento vê o status 'cancelado' e encerra; não precisa tratar a resposta aqui
      });
    $form.before($cancelar);

    function encerrar() {
      encerrado = true;
      if (timerAbertura) { clearTimeout(timerAbertura); timerAbertura = null; }
      if (fonte) { fonte.close(); fonte = null; }
      if (poll) { clearInterval(poll); poll = null; }
      finalizarEnvio();
      $cancelar.remove();
    }

    // Um estado do processamento, no mesmo formato pelos dois caminhos (evento 'estado' do SSE ou
    // resposta do polling) — por isso o tratamento é um só.
    function aplicarEstado(p) {
      if (encerrado || !p) return;
      if (p.cd_status === "concluido") {
        encerrar();
        // recarrega a conversa do banco para trazer a resposta final gravada
        if (idConversaAtual === idConversa) abrirConversa(idConversa, $header.text());
        carregarConversas();
      } else if (p.cd_status === "cancelado") {
        encerrar();
        $mensagens.append($("<div>").addClass("chat-cancelado").text("Resposta cancelada."));
        rolarParaFim(true);
        carregarConversas();
      } else if (p.cd_status === "erro") {
        encerrar();
        mostrarErro(p.tx_erro || "Erro ao gerar a resposta.");
      } else if (p.tx_etapa_atual) {
        $digitando.text(p.tx_etapa_atual);
      } else if (exibirPrompt) {
        $digitando.text(TEXTO_MONTANDO_PROMPT);
      } else {
        $digitando.text(TEXTO_DIGITANDO);
      }
    }

    function iniciarPolling() {
      if (encerrado || poll) return;
      poll = setInterval(function () {
        $.getJSON(URL_BASE).done(aplicarEstado);
        // falha transitória de rede: mantém o polling na próxima batida
      }, 2500);
    }

    function cairParaPolling() {
      if (encerrado) return;
      if (timerAbertura) { clearTimeout(timerAbertura); timerAbertura = null; }
      if (fonte) { fonte.close(); fonte = null; }
      iniciarPolling();
    }

    function iniciarSse() {
      if (!window.EventSource) return false;
      try {
        fonte = new EventSource(URL_BASE + "/eventos");
      } catch (e) {
        return false;
      }
      // O servidor manda o estado atual assim que a conexão abre. Se nada chegar (proxy que
      // bufferiza a resposta em vez de repassá-la), não dá para esperar para sempre: o polling
      // assume e o SSE é fechado.
      timerAbertura = setTimeout(cairParaPolling, 8000);
      fonte.addEventListener("estado", function (ev) {
        if (timerAbertura) { clearTimeout(timerAbertura); timerAbertura = null; }
        var p = null;
        try { p = JSON.parse(ev.data); } catch (e) { return; }  // evento truncado: o próximo corrige
        aplicarEstado(p);
      });
      // o servidor desistiu de acompanhar (processamento sumiu, ou teto de tempo) sem status final
      fonte.addEventListener("fim", cairParaPolling);
      fonte.onerror = function () {
        // queda de rede o próprio EventSource refaz (readyState CONNECTING) e o servidor reenvia o
        // estado atual; só quando ele desiste de vez (CLOSED — 401/403/404, proxy que recusa) é que
        // o polling precisa entrar
        if (fonte && fonte.readyState === EventSource.CLOSED) cairParaPolling();
      };
      return true;
    }

    if (!iniciarSse()) iniciarPolling();
  }

  function enviarTexto(texto, opts) {
    opts = opts || {};
    texto = $.trim(texto || "");
    var idConversa = opts.idConversa || idConversaAtual;
    if (!texto || !idConversa || enviando) return;
    var exibirPrompt = !!opts.exibirPrompt;
    var escopos = opts.escopos || escoposSelecionados();
    ultimoEnvio = { texto: texto, exibirPrompt: exibirPrompt, escopos: escopos, idConversa: idConversa };
    // gesto do usuário: único momento em que dá pra pedir a permissão de notificação sem
    // atrapalhar (o navegador exige interação; ver js/utils/avisos_ia.js)
    if (window.AvisosIA && !opts.repetir) window.AvisosIA.pedirPermissao();
    enviando = true;
    $mensagens.find(".chat-sugestoes, .chat-vazio").remove();
    if (!opts.repetir) {
      $mensagens.append(renderMensagem({ tp_papel: "user", tx_conteudo: texto, dt_mensagem: new Date().toISOString() }));
    }
    rolarParaFim(true);
    $input.val("");
    if (typeof sincronizarInput === "function") sincronizarInput();
    else $input.css("height", "auto");
    $enviarBtn.prop("disabled", true);
    $input.prop("disabled", true);
    $digitando.removeClass("hidden").show();

    $.ajax({
      url: "/api/investigacao/ia/conversas/" + idConversa + "/mensagens",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ texto: texto, exibir_prompt: exibirPrompt, escopos: escopos })
    })
      .done(function (resp) {
        if (resp && resp.modo === "async") {
          // sempre em background; acompanhamos por polling (botão/entrada seguem travados)
          $digitando.text(exibirPrompt ? TEXTO_MONTANDO_PROMPT : TEXTO_DIGITANDO);
          acompanharProcessamento(resp.id_processamento, idConversa, exibirPrompt);
          return;
        }
        // fallback defensivo (hoje o backend sempre responde async)
        $mensagens.append(renderMensagem(resp));
        rolarParaFim(false);
        carregarConversas();
        finalizarEnvio();
      })
      .fail(function (xhr) {
        mostrarErro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao falar com a IA.");
        finalizarEnvio();
      });
  }

  // "Não enviar + exibir prompt" é modo de trabalho, não escolha de uma mensagem só: quem liga
  // costuma usar a conversa inteira assim, então a preferência atravessa a sessão.
  var CHAVE_EXIBIR_PROMPT = "analyser-ia-exibir-prompt";
  try {
    if (localStorage.getItem(CHAVE_EXIBIR_PROMPT) === "1") $exibirPrompt.prop("checked", true);
    $exibirPrompt.on("change", function () {
      try { localStorage.setItem(CHAVE_EXIBIR_PROMPT, this.checked ? "1" : "0"); } catch (e) { /* bloqueado */ }
    });
  } catch (e) { /* localStorage indisponível — segue sendo escolha por mensagem */ }

  // Ctrl/Cmd+K leva o cursor para a busca de conversas (o mesmo atalho de VS Code/Slack), e o
  // Esc fecha o menu "⋯ Ações" — um <details> não fecha sozinho fora do clique.
  $(document).on("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      if (!idInquerito || !$busca.length) return;
      if ($busca.is(":hidden")) {
        // no painel único (< 860px) a busca mora na lista, escondida sob a conversa aberta
        $(".chat-app").removeClass("conversa-aberta");
        if ($busca.is(":hidden")) return; // ainda escondida (IA bloqueada, painel fora da tela)
      }
      e.preventDefault();
      $busca.trigger("focus").trigger("select");
    }
  });

  var $menuAcoes = $(".chat-toolbar-menu");
  if ($menuAcoes.length) {
    $(document).on("click", function (e) {
      if ($menuAcoes.prop("open") && !$(e.target).closest(".chat-toolbar-menu").length) {
        $menuAcoes.prop("open", false);
      }
    });
    $menuAcoes.on("keydown", function (e) {
      if (e.key === "Escape" && $menuAcoes.prop("open")) {
        $menuAcoes.prop("open", false).find("summary").trigger("focus");
      }
    });
  }

  $form.on("submit", function (e) {
    e.preventDefault();
    if (enviando) return; // já há uma mensagem em andamento nesta aba — ignora reenvio
    enviarTexto($input.val(), { exibirPrompt: $exibirPrompt.is(":checked") });
  });

  /* Quem decide se a IA está liberada é o backend, que confere o flag IN_IA_ATIVA dos dois lados —
   * a delegacia de quem está logado e a do inquérito aberto (api/funcoes/ia_delegacia.py) — e
   * responde 403 em qualquer rota .../ia/.... Antes a tela decidia sozinha, olhando um ?delegacia=
   * da URL que só o botão do card do inquérito mandava: entrando por qualquer outro caminho (link
   * direto, "Prompt de IA específico", inqueritos.html) o bloqueio não acontecia. Por isso a
   * primeira chamada é a listagem de conversas: ou ela responde, ou diz por que não. */
  function verificarIaDelegacia() {
    return $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/ia/conversas");
  }

  function mostrarIaBloqueada(xhr) {
    var $block = $("<div>").css({
      gridColumn: "1 / -1", display: "flex", alignItems: "center", justifyContent: "center",
      flexDirection: "column", gap: "16px", padding: "48px", color: "var(--text-muted)"
    });
    var texto = (xhr && xhr.responseJSON && xhr.responseJSON.detail)
      || (delegaciaParam ? "IA desativada para " + delegaciaParam + "." : "IA desativada para esta delegacia.");
    $block.append($("<div>").css("fontSize", "48px").text("\u26A0\uFE0F"));
    $block.append($("<p>").css("fontSize", "18px", "fontWeight", 600).text(texto));
    $block.attr("role", "alert");
    $(".chat-app").empty().append($block);
  }

  if (!idInquerito) {
    // sem inquérito não há o que perguntar: o aviso explica e os botões que dependem dele param
    $lista.append($("<p>").addClass("empty-hint").css("padding", "16px")
      .attr("role", "alert").text("Nenhum inquérito informado na URL. Abra esta tela pelo card de um inquérito."));
    $novaBtn.prop("disabled", true).attr("title", "Abra por um inquérito (falta o ?inquerito= na URL)");
    $toolbar.addClass("hidden");
  } else {
    verificarIaDelegacia().done(function () {
      carregarParametrosIA();
      if (escopoTipoParam && escopoIdParam && !escopoTratado) {
        escopoTratado = true;
        abrirOuCriarConversaComEscopo();
      } else {
        carregarConversas(conversaParam);
      }
    }).fail(function (xhr) {
      // 403 aqui é o flag da delegacia ou a falta de acesso ao inquérito; nos dois casos não há
      // chat a mostrar, e a mensagem do backend já diz qual dos dois foi
      mostrarIaBloqueada(xhr);
    });
  }
})();
