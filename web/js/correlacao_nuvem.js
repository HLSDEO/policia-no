/* Tela "Nuvem × aparelho" — o arquivo que sumiu do celular e continua na conta na nuvem.
 *
 * O caso de uso: o suspeito apagou a foto/vídeo/áudio do WhatsApp no aparelho antes da
 * apreensão. O backup automático da galeria já tinha subido as pastas do WhatsApp para o Google
 * Fotos, e essa cópia sobrevive ao apagamento — então a mídia que interessa está viva na extração
 * de nuvem e ausente na do celular. À mão isso significa abrir duas galerias de centenas de
 * milhares de arquivos e comparar de olho.
 *
 * A tela é irmã de "Comparar celulares" e reaproveita o desenho dela (css/comparar_celulares.css,
 * classes .cmp-*), mas responde à pergunta OPOSTA: aquela acha o que está em dois lugares, esta
 * acha o que está só num. Ver funcoes/correlacao_nuvem_utils.py e docs/correlacao_nuvem.md.
 *
 * A seleção de extrações e os filtros vão para a URL (EstadoUrl), para a visão filtrada poder ser
 * colada para outro investigador e sobreviver ao botão Voltar. */
(function () {
  "use strict";

  var U = window.AnalyserUtils;
  var esc = U.esc;

  var idInquerito = new URLSearchParams(window.location.search).get("inquerito") || "";

  var $fontesNuvem = $("#fontes-nuvem");
  var $fontesAparelhos = $("#fontes-aparelhos");
  var $fontesVazio = $("#fontes-vazio");
  var $avisoSidecar = $("#aviso-sidecar");
  var $cardResumo = $("#card-resumo");
  var $resumoCards = $("#resumo-cards");
  var $busca = $("#filtro-busca");
  var $criterio = $("#filtro-criterio");
  var $estado = $("#filtro-estado");
  var $categoria = $("#filtro-categoria");
  var $ordenar = $("#filtro-ordenar");
  var $soWhatsapp = $("#filtro-whatsapp");
  var $soDoAparelho = $("#filtro-do-aparelho");
  var $explicacao = $("#crn-explicacao");
  var $carregando = $("#crn-carregando");
  var $resultados = $("#crn-resultados");
  var $vazio = $("#crn-vazio");
  var $paginacao = $("#crn-paginacao");
  var $pagInfo = $("#pag-info");

  var LIMITE = 25;

  /* O texto muda com o eixo porque cada um tem uma ressalva diferente, e omiti-las faria o
   * investigador ler "não encontrado" como "não existe". A do hash é a mais importante: o Google
   * reencoda foto e vídeo no upload, então o MD5 da nuvem NÃO bate com o do celular para mídia
   * recomprimida — nesse eixo, ausência de par não prova nada sobre foto. */
  var EXPLICACOES = {
    nome: "Cruzamento pelo nome do arquivo. O WhatsApp nomeia mídia de forma determinística " +
      "(IMG-AAAAMMDD-WA####) e esse nome sobrevive ao backup para o Google Fotos, então o nome é " +
      "o que os dois lados compartilham mesmo quando o binário foi reencodado no upload. É o eixo " +
      "mais confiável para mídia de conversa.",
    hash: "Cruzamento pelo hash MD5 — o mesmo binário, byte a byte. Vale para PDF, documento e " +
      "áudio (o WhatsApp guarda .opus intacto). <strong>Não vale para foto e vídeo</strong>: o " +
      "Google reencoda a mídia no upload, então o arquivo da nuvem tem outro hash mesmo quando é " +
      "a mesma imagem — aqui, “sem par no aparelho” não prova ausência.",
    tamanho: "Cruzamento pelo tamanho exato em bytes, dentro da mesma categoria. É o eixo " +
      "circunstancial: serve para o anexo que o Cellebrite viu na conversa mas cujo binário não " +
      "recuperou, quando nome e hash não têm o que comparar. Espere falso positivo."
  };

  var ESTADOS = {
    apagado: {
      rotulo: "Apagado do aparelho",
      dica: "A mensagem que carregava este arquivo continua no banco do WhatsApp, mas o binário " +
        "não está mais no aparelho — a nuvem é a única cópia."
    },
    recuperado: {
      rotulo: "Recuperado pelo Cellebrite",
      dica: "O aparelho tem uma cópia marcada como apagada (recuperada do espaço não alocado). A " +
        "nuvem confirma o achado e entrega o binário íntegro."
    },
    so_nuvem: {
      rotulo: "Só na nuvem",
      dica: "Nem o arquivo nem uma referência a ele foram encontrados no aparelho. Pode ter sido " +
        "apagado antes do backup do banco do WhatsApp — ou nunca ter estado neste aparelho."
    }
  };

  var fontes = { nuvem: [], aparelhos: [] };
  var selecionados = {};       // id_extracao -> true
  var paginaAtual = 1;
  var requisicao = null;       // a busca em voo, para cancelar quando o filtro muda

  // ---------- fontes ----------

  function nomeFonte(f) {
    return f.no_dispositivo || f.no_fabricante || U.nomeArquivo(f.no_arquivo) ||
      ("Extração " + f.id_extracao);
  }

  function idsSelecionados() {
    return fontes.nuvem.concat(fontes.aparelhos)
      .filter(function (f) { return selecionados[f.id_extracao]; })
      .map(function (f) { return f.id_extracao; });
  }

  function selecionadosDe(lista) {
    return lista.filter(function (f) { return selecionados[f.id_extracao]; });
  }

  function chipFonte(f, in_nuvem) {
    var marcado = !!selecionados[f.id_extracao];
    var $chip = $("<label>")
      .addClass("cmp-aparelho" + (marcado ? " ativo" : ""))
      .attr("title", f.no_arquivo || "");
    $("<input>").attr("type", "checkbox").prop("checked", marcado)
      .on("change", function () {
        selecionados[f.id_extracao] = this.checked;
        $chip.toggleClass("ativo", this.checked);
        // trocar de extração muda o universo, então o panorama precisa ser recontado — os
        // demais filtros só recortam o que já foi contado e não mexem nos números
        buscarResumo();
        aoMudarFiltro();
      })
      .appendTo($chip);
    var $corpo = $("<div>").appendTo($chip);
    $("<span>").addClass("cmp-aparelho-tag").text(in_nuvem ? "☁" : "📱").appendTo($corpo);
    $("<span>").addClass("cmp-aparelho-nome").text(" " + nomeFonte(f)).appendTo($corpo);
    var detalhe = U.formatarNumero(f.qt_arquivos) + " arquivos";
    if (in_nuvem) {
      detalhe += " · " + U.formatarNumero(f.qt_com_sidecar) + " com metadado de nuvem";
    } else {
      detalhe += " · " + U.formatarNumero(f.qt_midia_whatsapp) + " com nome de mídia do WhatsApp";
    }
    $("<span>").addClass("cmp-aparelho-info").text(detalhe).appendTo($corpo);
    return $chip;
  }

  function renderFontes() {
    $fontesNuvem.empty();
    $fontesAparelhos.empty();
    fontes.nuvem.forEach(function (f) { $fontesNuvem.append(chipFonte(f, true)); });
    fontes.aparelhos.forEach(function (f) { $fontesAparelhos.append(chipFonte(f, false)); });
    var falta = !fontes.nuvem.length || !fontes.aparelhos.length;
    $fontesVazio.toggleClass("hidden", !falta);
  }

  /* Caso do IPED importado antes de a leitura de sidecar existir não tem nenhuma linha em
   * NUV_TB01_METADADOS_ARQUIVO: a origem do upload é desconhecida e o filtro "só o que subiu de um
   * aparelho" zeraria o resultado sem explicar por quê. Melhor desabilitá-lo e dizer o motivo do
   * que deixar o investigador concluir que não há nada. */
  function avisarSemSidecar() {
    var sem = selecionadosDe(fontes.nuvem).filter(function (f) {
      return !Number(f.qt_com_sidecar);
    });
    var faltaTudo = sem.length && sem.length === selecionadosDe(fontes.nuvem).length;
    $soDoAparelho.prop("disabled", !!faltaTudo);
    if (faltaTudo) $soDoAparelho.prop("checked", false);
    if (!sem.length) {
      $avisoSidecar.addClass("hidden").empty();
      return;
    }
    $avisoSidecar.removeClass("hidden").html(
      "<strong>Sem metadado de nuvem:</strong> " + esc(sem.map(nomeFonte).join(", ")) +
      (sem.length > 1 ? " não têm" : " não tem") + " os arquivos “-info.json”/“.json” do Takeout " +
      "interpretados — a importação é anterior a esse recurso. O cruzamento funciona normalmente, " +
      "mas não dá para saber de qual aparelho a mídia subiu nem quando a foto foi tirada. " +
      "Reimporte o caso para preencher."
    );
  }

  // ---------- panorama ----------

  function renderResumo(dados) {
    $resumoCards.empty();
    if (dados.sem_par) {
      $cardResumo.addClass("hidden");
      return;
    }
    $cardResumo.removeClass("hidden");
    ["apagado", "recuperado", "so_nuvem"].forEach(function (chave) {
      var meta = ESTADOS[chave];
      var $card = $("<div>")
        .addClass("crn-resumo-card" + ($estado.val() === chave ? " ativo" : ""))
        .attr("title", meta.dica)
        .on("click", function () {
          // segundo clique no mesmo quadro limpa o filtro
          $estado.val($estado.val() === chave ? "" : chave);
          aoMudarFiltro();
        });
      $("<span>").addClass("crn-resumo-numero").text(U.formatarNumero(dados[chave] || 0)).appendTo($card);
      $("<span>").addClass("crn-resumo-rotulo").text(meta.rotulo).appendTo($card);
      $resumoCards.append($card);
    });
  }

  // ---------- resultados ----------

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
  // (mesma razão de js/comparar_celulares.js:linkConversa)
  function linkConversa(chat) {
    var url = window.MapaTelas.paginaConversa(chat.fonte, chat.tp_app) +
      "?inquerito=" + encodeURIComponent(idInquerito) +
      "&fonte=" + encodeURIComponent(chat.fonte) +
      "&msg=" + encodeURIComponent(chat.id_mensagem);
    if (chat.fonte === "msg" && chat.tp_app) url += "&app=" + encodeURIComponent(chat.tp_app);
    return url;
  }

  /* A frase que dá sentido ao arquivo: quem mandou, para qual conversa, quando e em que sentido.
   * Sem ela o resultado é só "existe na nuvem e não no celular", que é metade do que o
   * investigador precisa — e a metade menos útil. */
  function transitoDoArquivo(chat) {
    if (!chat || !chat.id_mensagem) return null;
    var $p = $("<div>").addClass("cmp-transito");
    var enviada = chat.in_enviada === true;
    $("<span>")
      .addClass(enviada ? "cmp-seta-enviada" : "cmp-seta-recebida")
      .text(enviada ? "▲ enviou" : "▼ recebeu")
      .appendTo($p);
    var conversa = chat.no_conversa || chat.no_remetente || "conversa";
    $p.append(document.createTextNode(enviada ? " para " : " de "));
    $("<a>").attr("href", linkConversa(chat)).text(conversa).appendTo($p);
    if (!enviada && chat.no_remetente && chat.no_remetente !== conversa) {
      $p.append(document.createTextNode(" (" + chat.no_remetente + ")"));
    }
    if (chat.dt_mensagem) {
      $p.append(document.createTextNode(" em " + U.formatarDataHora(chat.dt_mensagem)));
    }
    if (chat.in_apagada_remetente) {
      $p.append($("<span>").addClass("crn-estado apagado").css("margin-left", "6px")
        .attr("title", "O remetente apagou a mensagem para todos; o arquivo continua na nuvem")
        .text("apagada para todos"));
    }
    return $p;
  }

  /* O painel do sidecar: o que só o servidor sabe. As datas de servidor são as que importam numa
   * contestação — mexer no relógio do aparelho não as altera. */
  function painelNuvem(item) {
    var linhas = [];
    // "capturado" e não "foto tirada": o photoTakenTime do sidecar vale igual para vídeo
    if (item.dt_captura) linhas.push(["Capturado em", U.formatarDataHora(item.dt_captura)]);
    if (item.dt_criacao_servidor) linhas.push(["Criado no servidor", U.formatarDataHora(item.dt_criacao_servidor)]);
    if (item.dt_modificacao_servidor) linhas.push(["Alterado no servidor", U.formatarDataHora(item.dt_modificacao_servidor)]);
    if (item.tp_origem_upload) linhas.push(["Origem do upload", item.tp_origem_upload]);
    if (item.no_dispositivo_upload) linhas.push(["Aparelho de origem", item.no_dispositivo_upload]);
    if (item.no_pasta_dispositivo) linhas.push(["Pasta no aparelho", item.no_pasta_dispositivo]);
    if (item.no_proprietario) linhas.push(["Dono na nuvem", item.no_proprietario]);
    if (item.tx_compartilhado_com) linhas.push(["Compartilhado com", item.tx_compartilhado_com]);
    if (item.in_lixeira) linhas.push(["Lixeira da nuvem", "sim"]);
    if (item.vl_latitude && item.vl_longitude) {
      linhas.push(["Onde foi tirada", item.vl_latitude + ", " + item.vl_longitude]);
    }
    if (!linhas.length) return null;
    var $dl = $("<dl>").addClass("crn-nuvem");
    linhas.forEach(function (par) {
      $("<dt>").text(par[0]).appendTo($dl);
      $("<dd>").text(par[1]).appendTo($dl);
    });
    return $dl;
  }

  function render(dados) {
    dados.itens.forEach(function (item) {
      var $card = $("<div>").addClass("cmp-item" + (item.cd_estado === "apagado" ? " destaque" : ""));

      var $cab = $("<div>").addClass("cmp-item-cab").appendTo($card);
      // miniatura só quando a nuvem tem mesmo o binário — o endpoint devolve 404 para item que
      // veio só como metadado, e uma imagem quebrada no cartão parece erro da tela
      if (item.no_categoria === "Imagem" && item.disponivel) {
        $("<img>").addClass("cmp-miniatura").attr("loading", "lazy").attr("alt", "")
          .attr("src", "/api/investigacao/arquivos/" + item.id_arquivo + "/conteudo?preview=true&thumb=true")
          .on("error", function () { $(this).remove(); })
          .appendTo($cab);
      }

      var $titulo = $("<div>").addClass("cmp-item-titulo").appendTo($cab);
      $("<div>").addClass("cmp-item-nome").text(item.no_arquivo || "(sem nome)").appendTo($titulo);

      var $meta = $("<div>").addClass("cmp-item-meta").appendTo($titulo);
      $("<span>").text(item.wa_rotulo || item.no_categoria || "—").appendTo($meta);
      $("<span>").text(formatarBytes(item.qt_bytes)).appendTo($meta);
      if (item.dt_referencia) {
        $("<span>").text(U.formatarDataHora(item.dt_referencia)).appendTo($meta);
      }
      if (item.co_hash_md5) {
        $("<span>").addClass("cmp-hash").attr("title", "MD5").text(item.co_hash_md5).appendTo($meta);
      }

      var meta = ESTADOS[item.cd_estado] || { rotulo: item.cd_estado, dica: "" };
      $("<span>").addClass("crn-estado " + item.cd_estado)
        .attr("title", meta.dica).text(meta.rotulo).appendTo($cab);

      if (item.in_lacuna) {
        $("<span>").addClass("crn-lacuna")
          .attr("title", "O aparelho tem mídia do mesmo dia e tipo com sequência antes e depois " +
                "desta, mas não esta — o buraco na sequência indica apagamento seletivo, não " +
                "sincronização incompleta.")
          .text("buraco na sequência").appendTo($cab);
      }

      var $corpo = $("<div>").addClass("cmp-ocorrencias").appendTo($card);
      var $linha = $("<div>").addClass("cmp-ocorrencia").appendTo($corpo);
      var $dentro = $("<div>").addClass("cmp-ocorrencia-corpo").appendTo($linha);
      $("<div>").addClass("cmp-ocorrencia-caminho").text(item.tx_caminho || "—").appendTo($dentro);

      var $transito = transitoDoArquivo(item.chat);
      if ($transito) $dentro.append($transito);

      if (item.par_tx_caminho) {
        $("<div>").addClass("cmp-ocorrencia-caminho")
          .text("No aparelho: " + item.par_tx_caminho +
                (item.par_status ? " (" + item.par_status + ")" : ""))
          .appendTo($dentro);
      }
      if (item.tx_ocr) {
        $("<div>").addClass("cmp-ocorrencia-caminho")
          .attr("title", "Texto reconhecido pelo IPED na imagem/documento")
          .text("Texto na imagem: " + item.tx_ocr).appendTo($dentro);
      }
      if (item.tx_transcricao) {
        $("<div>").addClass("cmp-ocorrencia-caminho")
          .attr("title", "Transcrição feita pelo IPED")
          .text("Áudio: " + item.tx_transcricao).appendTo($dentro);
      }

      var $painel = painelNuvem(item);
      if ($painel) $dentro.append($painel);

      var $acoes = $("<div>").addClass("cmp-acoes-item").appendTo($linha);
      if (item.disponivel) {
        // o ganho que fecha o caso: a nuvem TEM o binário, então dá para ver a foto que sumiu
        $("<a>").addClass("btn btn-secondary")
          .attr("href", "/api/investigacao/arquivos/" + item.id_arquivo + "/conteudo")
          .attr("target", "_blank").attr("rel", "noopener")
          .text("Abrir da nuvem").appendTo($acoes);
      }
      $("<a>").addClass("btn btn-secondary")
        .attr("href", "arquivos.html?inquerito=" + encodeURIComponent(idInquerito) +
              "&extracao=" + encodeURIComponent(item.id_extracao) +
              "&busca=" + encodeURIComponent(item.no_arquivo || ""))
        .text("Ver na galeria").appendTo($acoes);

      $resultados.append($card);
    });
  }

  // ---------- busca ----------

  function parametros() {
    return {
      extracoes: idsSelecionados().join(","),
      criterio: $criterio.val(),
      estado: $estado.val() || undefined,
      categoria: $categoria.val() || undefined,
      so_whatsapp: $soWhatsapp.is(":checked"),
      so_do_aparelho: $soDoAparelho.is(":checked"),
      busca: ($busca.val() || "").trim() || undefined,
      ordenar: $ordenar.val(),
      pagina: paginaAtual,
      limite: LIMITE
    };
  }

  function base() {
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/correlacao-nuvem/";
  }

  function buscarResumo() {
    $.getJSON(base() + "resumo", { extracoes: idsSelecionados().join(",") })
      .done(renderResumo)
      .fail(function () { $cardResumo.addClass("hidden"); });
  }

  function buscar() {
    $explicacao.html(EXPLICACOES[$criterio.val()] || "");

    if (!selecionadosDe(fontes.nuvem).length || !selecionadosDe(fontes.aparelhos).length) {
      $resultados.empty();
      $carregando.addClass("hidden");
      $paginacao.addClass("hidden");
      $vazio.removeClass("hidden")
        .text("Escolha pelo menos uma conta na nuvem e um aparelho para cruzar.");
      return;
    }

    if (requisicao) requisicao.abort();
    $vazio.addClass("hidden");
    $carregando.removeClass("hidden");
    $resultados.empty();

    requisicao = $.getJSON(base() + "arquivos", parametros());
    requisicao.done(function (dados) {
      $carregando.addClass("hidden");
      if (!dados.itens || !dados.itens.length) {
        $paginacao.addClass("hidden");
        $vazio.removeClass("hidden").text(
          $soWhatsapp.is(":checked")
            ? "Nenhuma mídia do WhatsApp da nuvem está ausente do aparelho com os filtros atuais. " +
              "Desmarque “Só mídia do WhatsApp” para incluir o resto dos arquivos da conta."
            : "Nenhum arquivo da nuvem está ausente do aparelho com os filtros atuais."
        );
        return;
      }
      render(dados);
      pintarPaginacao(dados);
    }).fail(function (xhr) {
      if (xhr.statusText === "abort") return;
      $carregando.addClass("hidden");
      $paginacao.addClass("hidden");
      $vazio.removeClass("hidden").text(
        (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao cruzar a nuvem com o aparelho."
      );
    }).always(function () { requisicao = null; });
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
    var todos = fontes.nuvem.concat(fontes.aparelhos).length;
    window.EstadoUrl.salvar({
      extracoes: idsSelecionados().length === todos ? "" : idsSelecionados().join(","),
      criterio: $criterio.val() === "nome" ? "" : $criterio.val(),
      estado: $estado.val(),
      categoria: $categoria.val(),
      whatsapp: $soWhatsapp.is(":checked") ? "" : "0",
      aparelho: $soDoAparelho.is(":checked") ? "1" : "",
      busca: ($busca.val() || "").trim(),
      ordenar: $ordenar.val() === "relevancia" ? "" : $ordenar.val(),
      pagina: paginaAtual > 1 ? paginaAtual : ""
    });
  }

  function aoMudarFiltro() {
    paginaAtual = 1;
    avisarSemSidecar();
    salvarEstado();
    renderResumoAtivo();
    buscar();
  }

  // o quadro do panorama e o <select> de estado são o mesmo filtro em dois lugares; mudar num
  // precisa repintar o outro, senão a tela mostra dois estados diferentes ao mesmo tempo
  function renderResumoAtivo() {
    $resumoCards.children().each(function (i) {
      var chave = ["apagado", "recuperado", "so_nuvem"][i];
      $(this).toggleClass("ativo", $estado.val() === chave);
    });
  }

  // ---------- eventos ----------

  var timerBusca = null;
  $busca.on("input", function () {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(aoMudarFiltro, 350);
  });
  $criterio.add($estado).add($categoria).add($ordenar).on("change", aoMudarFiltro);
  $soWhatsapp.add($soDoAparelho).on("change", aoMudarFiltro);

  $("#pag-anterior").on("click", function () {
    if (paginaAtual > 1) { paginaAtual--; salvarEstado(); buscar(); }
  });
  $("#pag-proxima").on("click", function () {
    paginaAtual++; salvarEstado(); buscar();
  });

  // ---------- início ----------

  function restaurarEstado() {
    var p = new URLSearchParams(window.location.search);
    if (p.get("criterio")) $criterio.val(p.get("criterio"));
    if (p.get("estado")) $estado.val(p.get("estado"));
    if (p.get("categoria")) $categoria.val(p.get("categoria"));
    if (p.get("ordenar")) $ordenar.val(p.get("ordenar"));
    if (p.get("busca")) $busca.val(p.get("busca"));
    $soWhatsapp.prop("checked", p.get("whatsapp") !== "0");
    $soDoAparelho.prop("checked", p.get("aparelho") === "1");
    paginaAtual = Math.max(1, parseInt(p.get("pagina"), 10) || 1);
    var escolhidas = (p.get("extracoes") || "").split(",").filter(Boolean);
    // deep-link padrão do sistema (?extracao=ID, o que os cards do IPED usam) — antes a tela
    // ignorava o parâmetro e pré-selecionava tudo
    if (!escolhidas.length && p.get("extracao")) escolhidas.push(p.get("extracao"));
    return escolhidas;
  }

  function iniciar() {
    if (!idInquerito) {
      $fontesVazio.removeClass("hidden").text("Nenhum inquérito informado na URL.");
      return;
    }
    var escolhidas = restaurarEstado();
    $.getJSON(base() + "fontes")
      .done(function (dados) {
        fontes = { nuvem: dados.nuvem || [], aparelhos: dados.aparelhos || [] };
        var todas = fontes.nuvem.concat(fontes.aparelhos);
        todas.forEach(function (f) {
          // sem recorte na URL, tudo entra: o cruzamento só faz sentido com os dois lados
          selecionados[f.id_extracao] = !escolhidas.length ||
            escolhidas.indexOf(String(f.id_extracao)) >= 0;
        });
        renderFontes();
        avisarSemSidecar();
        if (fontes.nuvem.length && fontes.aparelhos.length) {
          buscarResumo();
          buscar();
        } else {
          $vazio.addClass("hidden");
          $cardResumo.addClass("hidden");
          $("#card-resultados").addClass("hidden");
        }
      })
      .fail(function () {
        $fontesVazio.removeClass("hidden").text("Não foi possível carregar as extrações do inquérito.");
      });
  }

  iniciar();
})();
