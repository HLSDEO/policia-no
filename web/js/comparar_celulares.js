/* Tela "Comparar celulares" — o que os aparelhos de um mesmo inquérito têm em comum.
 *
 * O caso de uso que a tela resolve: A manda B pagar um boleto e B devolve o comprovante. Nenhuma
 * mensagem diz nada; o vínculo está no ARQUIVO que existe nos dois celulares com o mesmo hash. À
 * mão isso significa abrir duas galerias de centenas de milhares de arquivos e comparar de olho.
 *
 * Quatro eixos, em abas (ver funcoes/comparar_celulares_utils.py para o que cada um cruza e por
 * quê): arquivos (o principal), interlocutores, agenda e Wi-Fi. Cada aba busca sob demanda no
 * primeiro clique — a de arquivos é a cara, e as outras três não têm por que esperar por ela.
 *
 * A seleção de aparelhos e os filtros vão para a URL (EstadoUrl), de modo que a visão filtrada
 * possa ser colada para outro investigador e sobreviva ao botão Voltar. */
(function () {
  "use strict";

  var U = window.AnalyserUtils;
  var esc = U.esc;

  var idInquerito = new URLSearchParams(window.location.search).get("inquerito") || "";

  var $aparelhos = $("#aparelhos-lista");
  var $aparelhosVazio = $("#aparelhos-vazio");
  var $avisoSemHash = $("#aviso-sem-hash");
  var $cardResultados = $("#card-resultados");
  var $abas = $("#cmp-abas");
  var $filtrosArquivos = $("#filtros-arquivos");
  var $busca = $("#filtro-busca");
  var $relevancia = $("#filtro-relevancia");
  var $categoria = $("#filtro-categoria");
  var $ordenar = $("#filtro-ordenar");
  var $explicacao = $("#cmp-explicacao");
  var $carregando = $("#cmp-carregando");
  var $resultados = $("#cmp-resultados");
  var $vazio = $("#cmp-vazio");
  var $paginacao = $("#cmp-paginacao");
  var $pagInfo = $("#pag-info");

  var LIMITE = { arquivos: 25, conversas: 50, contatos: 50, wifi: 50 };

  // Cada eixo diz o que buscar, o que explicar e como desenhar — assim a troca de aba é só
  // trocar de entrada nesta tabela, e não um if encadeado espalhado pelo arquivo.
  var EIXOS = {
    arquivos: {
      rota: "arquivos",
      vazio: "Nenhum arquivo em comum entre os aparelhos escolhidos com os filtros atuais.",
      explicacao: "Arquivos com o mesmo hash MD5 nos dois aparelhos — o mesmo binário, byte a byte. " +
        "Documento, áudio e vídeo enviados por mensageiro chegam idênticos ao destinatário e casam aqui; " +
        "foto que o app reencoda no envio, não. O selo “trafegou em conversa” marca o que foi anexo de " +
        "mensagem em pelo menos um dos aparelhos — é a prova de que o arquivo passou de um para o outro, " +
        "e não de que os dois por acaso o têm.",
      render: renderArquivos
    },
    conversas: {
      rota: "conversas",
      vazio: "Nenhum interlocutor em comum entre os aparelhos escolhidos.",
      explicacao: "Quem aparece nas conversas (WhatsApp, SMS e mensageiros) de mais de um aparelho. " +
        "Quando o identificador é o dono de um dos celulares comparados, os dois falaram entre si — " +
        "esses vêm marcados e em primeiro lugar.",
      render: renderConversas
    },
    contatos: {
      rota: "contatos",
      vazio: "Nenhum contato de agenda em comum entre os aparelhos escolhidos.",
      explicacao: "Telefones e e-mails salvos na agenda de mais de um aparelho. O nome que cada " +
        "celular deu ao mesmo número costuma ser informação em si.",
      render: renderContatos
    },
    wifi: {
      rota: "wifi",
      vazio: "Nenhuma rede Wi-Fi em comum entre os aparelhos escolhidos.",
      explicacao: "Redes com o mesmo BSSID (o MAC do roteador) conhecidas por mais de um aparelho: " +
        "os dois estiveram no mesmo ponto de rede. É sinal de lugar, não de vínculo — hotspot de " +
        "operadora e Wi-Fi de estabelecimento aparecem em qualquer celular que ande pela cidade.",
      render: renderWifi
    }
  };

  var aparelhos = [];          // extrações do inquérito, na ordem em que vieram
  var selecionados = {};       // id_extracao -> true
  var eixoAtual = "arquivos";
  var paginaAtual = 1;
  var requisicao = null;       // a busca em voo, para cancelar quando o filtro muda

  // ---------- aparelhos ----------

  function nomeAparelho(a) {
    return a.no_dispositivo || a.no_fabricante || U.nomeArquivo(a.no_arquivo) || ("Extração " + a.id_extracao);
  }

  function indiceAparelho(id) {
    for (var i = 0; i < aparelhos.length; i++) {
      if (String(aparelhos[i].id_extracao) === String(id)) return i;
    }
    return -1;
  }

  function idsSelecionados() {
    return aparelhos
      .filter(function (a) { return selecionados[a.id_extracao]; })
      .map(function (a) { return a.id_extracao; });
  }

  function renderAparelhos() {
    $aparelhos.empty();
    aparelhos.forEach(function (a, i) {
      var marcado = !!selecionados[a.id_extracao];
      var $chip = $("<label>")
        .addClass("cmp-aparelho" + (marcado ? " ativo" : ""))
        .attr("title", a.no_arquivo || "");
      $("<input>").attr("type", "checkbox").prop("checked", marcado)
        .on("change", function () {
          selecionados[a.id_extracao] = this.checked;
          $chip.toggleClass("ativo", this.checked);
          aoMudarFiltro();
        })
        .appendTo($chip);
      var $corpo = $("<div>").appendTo($chip);
      $("<span>").addClass("cmp-aparelho-tag").text(String(i + 1)).appendTo($corpo);
      $("<span>").addClass("cmp-aparelho-nome").text(" " + nomeAparelho(a)).appendTo($corpo);
      if (a.no_dono) {
        $("<span>").addClass("cmp-aparelho-info").text("Dono: " + a.no_dono).appendTo($corpo);
      }
      $("<span>").addClass("cmp-aparelho-info")
        .text(U.formatarNumero(a.qt_arquivos) + " arquivos · " +
              U.formatarNumero(a.qt_arquivos_com_hash) + " com hash")
        .appendTo($corpo);
      $aparelhos.append($chip);
    });
  }

  // Extração ingerida antes de o parser passar a ler MD5/SHA-256 do <file> tem os arquivos no
  // banco sem hash nenhum: o eixo de arquivos fica vazio para ela e isso NÃO é "não achou nada".
  // Sem este aviso, o investigador concluiria que os celulares não têm arquivo em comum.
  function avisarSemHash() {
    var sem = aparelhos.filter(function (a) {
      return selecionados[a.id_extracao] && Number(a.qt_arquivos) > 0 && !Number(a.qt_arquivos_com_hash);
    });
    if (!sem.length) {
      $avisoSemHash.addClass("hidden").empty();
      return;
    }
    var nomes = sem.map(nomeAparelho).join(", ");
    $avisoSemHash.removeClass("hidden").html(
      "<strong>Sem hash de arquivo:</strong> " + esc(nomes) +
      (sem.length > 1 ? " não têm" : " não tem") + " o hash dos arquivos gravado — " +
      "a extração foi importada antes de o sistema passar a lê-lo do UFDR. " +
      "Os outros eixos (interlocutores, agenda, Wi-Fi) funcionam normalmente; " +
      "para o cruzamento de arquivos, reprocesse a extração."
    );
  }

  // ---------- desenho dos eixos ----------

  function tagAparelho(idExtracao) {
    var i = indiceAparelho(idExtracao);
    var a = i >= 0 ? aparelhos[i] : null;
    return $("<span>").addClass("cmp-aparelho-tag")
      .attr("title", a ? nomeAparelho(a) : "")
      .text(i >= 0 ? String(i + 1) : "?");
  }

  function rotuloAparelho(idExtracao) {
    var i = indiceAparelho(idExtracao);
    return i >= 0 ? nomeAparelho(aparelhos[i]) : ("Extração " + idExtracao);
  }

  function formatarBytes(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n <= 0) return "—";
    if (n < 1024) return n + " B";
    var kb = n / 1024;
    if (kb < 1024) return kb.toFixed(1) + " KB";
    var mb = kb / 1024;
    if (mb < 1024) return mb.toFixed(1) + " MB";
    return (mb / 1024).toFixed(2) + " GB";
  }

  // deep-link para a mensagem que carregou o arquivo — a extração NÃO vai na URL de propósito,
  // para o filtro de aparelho da tela de conversa não esconder justamente a conversa alvo
  // (mesma razão de js/arquivos.js:linkConversa)
  function linkConversa(o) {
    var url = window.MapaTelas.paginaConversa(o.chat_fonte, o.chat_app) +
      "?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + encodeURIComponent(o.chat_fonte) +
      "&msg=" + encodeURIComponent(o.chat_id_mensagem);
    if (o.chat_fonte === "msg" && o.chat_app) url += "&app=" + encodeURIComponent(o.chat_app);
    return url;
  }

  function linkArquivos(idExtracao, nome) {
    return "arquivos.html?inquerito=" + encodeURIComponent(idInquerito) +
      "&extracao=" + encodeURIComponent(idExtracao) +
      "&busca=" + encodeURIComponent(nome || "");
  }

  /* A frase que dá sentido ao arquivo naquele aparelho: quem mandou, para qual conversa, quando
   * e em que sentido. Sem ela o resultado é só "os dois celulares têm este arquivo", que é
   * metade do que o investigador precisa. */
  function transitoDoArquivo(o) {
    if (!o.chat_id_mensagem) return null;
    var $p = $("<div>").addClass("cmp-transito");
    var enviada = o.chat_in_enviada === true;
    $("<span>")
      .addClass(enviada ? "cmp-seta-enviada" : "cmp-seta-recebida")
      .text(enviada ? "▲ enviou" : "▼ recebeu")
      .appendTo($p);
    var conversa = o.chat_no_conversa || o.chat_no_remetente || "conversa";
    $p.append(document.createTextNode(enviada ? " para " : " de "));
    $("<a>").attr("href", linkConversa(o)).text(conversa).appendTo($p);
    if (!enviada && o.chat_no_remetente && o.chat_no_remetente !== conversa) {
      $p.append(document.createTextNode(" (" + o.chat_no_remetente + ")"));
    }
    if (o.chat_dt_mensagem) {
      $p.append(document.createTextNode(" em " + U.formatarDataHora(o.chat_dt_mensagem)));
    }
    if (o.chat_app) $p.append(document.createTextNode(" · " + o.chat_app));
    return $p;
  }

  function renderArquivos(dados) {
    dados.itens.forEach(function (g) {
      var $item = $("<div>").addClass("cmp-item" + (g.in_anexo ? " destaque" : ""));

      var $cab = $("<div>").addClass("cmp-item-cab").appendTo($item);
      // miniatura só de imagem, e só quando algum dos aparelhos tem o binário: o endpoint de
      // conteúdo devolve 404 para arquivo que veio só como metadado no UFDR
      var comBinario = (g.ocorrencias || []).filter(function (o) { return o.disponivel; })[0];
      if (g.no_categoria === "Imagem" && comBinario) {
        $("<img>").addClass("cmp-miniatura").attr("loading", "lazy").attr("alt", "")
          .attr("src", "/api/investigacao/arquivos/" + comBinario.id_arquivo + "/conteudo?preview=true&thumb=true")
          .on("error", function () { $(this).remove(); })
          .appendTo($cab);
      }

      var $titulo = $("<div>").addClass("cmp-item-titulo").appendTo($cab);
      $("<div>").addClass("cmp-item-nome").text(g.no_arquivo || "(sem nome)").appendTo($titulo);
      var $meta = $("<div>").addClass("cmp-item-meta").appendTo($titulo);
      $("<span>").text(g.no_categoria || "—").appendTo($meta);
      $("<span>").text(formatarBytes(g.qt_bytes)).appendTo($meta);
      $("<span>").text("em " + g.qt_aparelhos + " aparelhos").appendTo($meta);
      $("<span>").addClass("cmp-hash").attr("title", "MD5").text(g.co_hash_md5).appendTo($meta);

      $("<span>")
        .addClass("cmp-selo" + (g.in_anexo ? "" : " neutro"))
        .attr("title", g.in_anexo
          ? "O arquivo foi anexo de mensagem em pelo menos um dos aparelhos"
          : "O arquivo está nos dois aparelhos, mas não foi encontrado como anexo de conversa")
        .text(g.in_anexo ? "trafegou em conversa" : "só presente")
        .appendTo($cab);

      var $ocs = $("<div>").addClass("cmp-ocorrencias").appendTo($item);
      (g.ocorrencias || []).forEach(function (o) {
        var $oc = $("<div>").addClass("cmp-ocorrencia").appendTo($ocs);
        tagAparelho(o.id_extracao).appendTo($oc);
        var $corpo = $("<div>").addClass("cmp-ocorrencia-corpo").appendTo($oc);
        $("<div>").text(rotuloAparelho(o.id_extracao)).appendTo($corpo);
        $("<div>").addClass("cmp-ocorrencia-caminho")
          .text(o.tx_caminho || o.no_arquivo || "—").appendTo($corpo);
        var $transito = transitoDoArquivo(o);
        if ($transito) $corpo.append($transito);
        var $acoes = $("<div>").addClass("cmp-acoes-item").appendTo($oc);
        if (o.disponivel) {
          $("<a>").addClass("btn btn-secondary")
            .attr("href", "/api/investigacao/arquivos/" + o.id_arquivo + "/conteudo")
            .attr("target", "_blank").attr("rel", "noopener")
            .text("Abrir").appendTo($acoes);
        }
        $("<a>").addClass("btn btn-secondary")
          .attr("href", linkArquivos(o.id_extracao, o.no_arquivo))
          .text("Ver na galeria").appendTo($acoes);
      });

      $resultados.append($item);
    });
  }

  function tabela(colunas) {
    var $tab = $("<table>").addClass("cmp-tabela");
    var $tr = $("<tr>").appendTo($("<thead>").appendTo($tab));
    colunas.forEach(function (c) { $("<th>").text(c).appendTo($tr); });
    $("<tbody>").appendTo($tab);
    return $tab;
  }

  function celulaPorAparelho(lista, montar) {
    var $td = $("<td>");
    var $wrap = $("<div>").addClass("cmp-por-aparelho").appendTo($td);
    (lista || []).forEach(function (a) {
      var $linha = $("<div>").appendTo($wrap);
      tagAparelho(a.id_extracao).appendTo($linha);
      $linha.append(document.createTextNode(" " + montar(a)));
    });
    return $td;
  }

  function renderConversas(dados) {
    var $tab = tabela(["Identificador", "Como cada aparelho conhece", "Conversas", "Última atividade", ""]);
    var $corpo = $tab.find("tbody");
    dados.itens.forEach(function (g) {
      var $tr = $("<tr>").appendTo($corpo);
      $("<td>").append($("<strong>").text(g.tx_exibicao || "—")).appendTo($tr);
      celulaPorAparelho(g.aparelhos, function (a) {
        return (a.nomes || "(sem nome)") + (a.apps ? " · " + a.apps : "");
      }).appendTo($tr);
      celulaPorAparelho(g.aparelhos, function (a) {
        return a.qt_conversas + (a.qt_conversas === 1 ? " conversa" : " conversas");
      }).appendTo($tr);
      $("<td>").text(U.formatarDataHora(g.dt_ultima_atividade)).appendTo($tr);
      $("<td>").append(
        g.in_direto
          ? $("<span>").addClass("cmp-selo")
              .attr("title", "Este identificador é o dono de um dos aparelhos comparados: os dois falaram entre si")
              .text("os aparelhos falaram entre si")
          : $("<span>").addClass("cmp-selo neutro").text("interlocutor em comum")
      ).appendTo($tr);
      if (g.in_direto) $tr.css("font-weight", "500");
    });
    $resultados.append($tab);
  }

  function renderContatos(dados) {
    var $tab = tabela(["Telefone / e-mail", "Nome em cada aparelho", "Em quantos"]);
    var $corpo = $tab.find("tbody");
    dados.itens.forEach(function (g) {
      var $tr = $("<tr>").appendTo($corpo);
      $("<td>").append($("<strong>").text(g.tx_exibicao || "—")).appendTo($tr);
      celulaPorAparelho(g.aparelhos, function (a) { return a.nomes || "(sem nome)"; }).appendTo($tr);
      $("<td>").text(g.qt_aparelhos).appendTo($tr);
    });
    $resultados.append($tab);
  }

  function renderWifi(dados) {
    var $tab = tabela(["BSSID (MAC do roteador)", "Rede", "Conexões por aparelho", "Última conexão"]);
    var $corpo = $tab.find("tbody");
    dados.itens.forEach(function (g) {
      var $tr = $("<tr>").appendTo($corpo);
      $("<td>").append($("<span>").addClass("cmp-hash").text(g.co_bssid)).appendTo($tr);
      $("<td>").text(g.ssids || "(sem nome)").appendTo($tr);
      celulaPorAparelho(g.aparelhos, function (a) {
        return U.formatarNumero(a.qt_conexoes) + "×";
      }).appendTo($tr);
      $("<td>").text(U.formatarDataHora(g.dt_ultima_conexao)).appendTo($tr);
    });
    $resultados.append($tab);
  }

  // ---------- busca ----------

  function parametros() {
    var p = {
      extracoes: idsSelecionados().join(","),
      busca: ($busca.val() || "").trim() || undefined,
      pagina: paginaAtual,
      limite: LIMITE[eixoAtual]
    };
    if (eixoAtual === "arquivos") {
      p.relevancia = $relevancia.val();
      p.categoria = $categoria.val() || undefined;
      p.ordenar = $ordenar.val();
    }
    return p;
  }

  function buscar() {
    var eixo = EIXOS[eixoAtual];
    $explicacao.text(eixo.explicacao);
    // o eixo de arquivos é o único com relevância/categoria/ordenação; nos outros os selects
    // seriam controles mortos em tela
    $filtrosArquivos.toggleClass("hidden", eixoAtual !== "arquivos");

    if (idsSelecionados().length < 2) {
      $resultados.empty();
      $carregando.addClass("hidden");
      $paginacao.addClass("hidden");
      $vazio.removeClass("hidden").text("Escolha pelo menos dois aparelhos para comparar.");
      return;
    }

    if (requisicao) requisicao.abort();
    $vazio.addClass("hidden");
    $carregando.removeClass("hidden");
    $resultados.empty();

    requisicao = $.getJSON(
      "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
      "/comparar-celulares/" + eixo.rota,
      parametros()
    );
    requisicao.done(function (dados) {
      $carregando.addClass("hidden");
      pintarBadge(eixoAtual, dados.total);
      if (!dados.itens || !dados.itens.length) {
        $paginacao.addClass("hidden");
        $vazio.removeClass("hidden").text(eixo.vazio);
        return;
      }
      eixo.render(dados);
      pintarPaginacao(dados);
    }).fail(function (xhr) {
      if (xhr.statusText === "abort") return;
      $carregando.addClass("hidden");
      $paginacao.addClass("hidden");
      $vazio.removeClass("hidden").text(
        (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao comparar os aparelhos."
      );
    }).always(function () { requisicao = null; });
  }

  function pintarBadge(eixo, total) {
    var $b = $abas.find('[data-badge="' + eixo + '"]');
    $b.toggleClass("hidden", !total).text(U.formatarNumero(total));
  }

  function pintarPaginacao(dados) {
    var paginas = Math.max(1, Math.ceil(dados.total / dados.limite));
    $paginacao.toggleClass("hidden", paginas <= 1);
    $pagInfo.text("Página " + dados.pagina + " de " + paginas + " · " +
                  U.formatarNumero(dados.total) + " resultados");
    $("#pag-anterior").prop("disabled", dados.pagina <= 1);
    $("#pag-proxima").prop("disabled", dados.pagina >= paginas);
  }

  function salvarEstado() {
    window.EstadoUrl.salvar({
      eixo: eixoAtual === "arquivos" ? "" : eixoAtual,
      extracoes: idsSelecionados().length === aparelhos.length ? "" : idsSelecionados().join(","),
      busca: ($busca.val() || "").trim(),
      relevancia: $relevancia.val() === "usuario" ? "" : $relevancia.val(),
      categoria: $categoria.val(),
      ordenar: $ordenar.val() === "relevancia" ? "" : $ordenar.val(),
      pagina: paginaAtual > 1 ? paginaAtual : ""
    });
  }

  function aoMudarFiltro() {
    paginaAtual = 1;
    avisarSemHash();
    salvarEstado();
    buscar();
  }

  // ---------- eventos ----------

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(aoMudarFiltro, 350);
  });
  $relevancia.add($categoria).add($ordenar).on("change", aoMudarFiltro);

  // roving tabindex + painel apontando para a aba ativa: sem isso os role="tab" do HTML
  // seriam uma promessa falsa para leitor de tela
  function ativarAba($tab) {
    eixoAtual = String($tab.data("eixo"));
    $abas.find(".extracao-tab")
      .removeClass("active")
      .attr({ "aria-selected": "false", "tabindex": "-1" });
    $tab.addClass("active").attr({ "aria-selected": "true", "tabindex": "0" });
    $("#cmp-painel").attr("aria-labelledby", "cmp-tab-" + eixoAtual);
  }

  $abas.on("click", ".extracao-tab", function () {
    var $tab = $(this);
    if ($tab.data("eixo") === eixoAtual) return;
    ativarAba($tab);
    paginaAtual = 1;
    salvarEstado();
    buscar();
  });

  $abas.on("keydown", ".extracao-tab", function (e) {
    var abas = $abas.find(".extracao-tab");
    var i = abas.index(this);
    var alvo = null;
    if (e.key === "ArrowRight") alvo = abas[(i + 1) % abas.length];
    else if (e.key === "ArrowLeft") alvo = abas[(i - 1 + abas.length) % abas.length];
    else if (e.key === "Home") alvo = abas.first();
    else if (e.key === "End") alvo = abas.last();
    if (!alvo) return;
    e.preventDefault();
    alvo.focus();
    $(alvo).trigger("click");
  });

  $("#pag-anterior").on("click", function () {
    if (paginaAtual > 1) { paginaAtual--; salvarEstado(); buscar(); }
  });
  $("#pag-proxima").on("click", function () {
    paginaAtual++; salvarEstado(); buscar();
  });

  // ---------- montagem ----------

  function inicializar() {
    if (!idInquerito) {
      $aparelhos.hide();
      $aparelhosVazio.removeClass("hidden")
        .text("Abra esta tela a partir de um inquérito (falta ?inquerito= na URL).");
      $cardResultados.hide();
      return;
    }

    var estado = window.EstadoUrl.ler(["eixo", "extracoes", "busca", "relevancia", "categoria", "ordenar", "pagina"]);

    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/comparar-celulares/aparelhos")
      .done(function (lista) {
        aparelhos = lista || [];
        if (aparelhos.length < 2) {
          $aparelhosVazio.removeClass("hidden");
          $cardResultados.hide();
          renderAparelhos();
          return;
        }
        var escolhidos = (estado.extracoes || "").split(",").filter(Boolean);
        aparelhos.forEach(function (a) {
          selecionados[a.id_extracao] = escolhidos.length
            ? escolhidos.indexOf(String(a.id_extracao)) !== -1
            : true;
        });
        renderAparelhos();
        avisarSemHash();

        if (estado.eixo && EIXOS[estado.eixo]) eixoAtual = estado.eixo;
        if (estado.busca) $busca.val(estado.busca);
        if (estado.relevancia) $relevancia.val(estado.relevancia);
        if (estado.categoria) $categoria.val(estado.categoria);
        if (estado.ordenar) $ordenar.val(estado.ordenar);
        paginaAtual = Math.max(1, parseInt(estado.pagina, 10) || 1);
        ativarAba($abas.find('[data-eixo="' + eixoAtual + '"]'));

        buscar();
      })
      .fail(function (xhr) {
        // o detalhe da API junto com a frase fixa: sem isso um 504 de timeout no banco
        // aparecia só como "Erro ao carregar os aparelhos", sem dizer o quê nem por quê
        var motivo = window.Api ? Api.mensagemErro(xhr, "") : "";
        $aparelhosVazio.removeClass("hidden").text(
          "Erro ao carregar os aparelhos deste inquérito." + (motivo ? " " + motivo : ""));
        $cardResultados.hide();
      });
  }

  $(inicializar);
})();
