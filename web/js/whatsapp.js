(function () {
  "use strict";

  function paramUrl(nome, padrao) { return AnalyserUtils.paramUrl(nome, padrao); }

  var idInquerito = paramUrl("inquerito", null);
  var idExtracao = paramUrl("extracao", null); // opcional: restringe a um aparelho do inquérito
  var fonte = paramUrl("fonte", "wpp"); // "wpp" (WhatsApp), "sms" ou "msg" (mensageiros diversos)
  var app = paramUrl("app", null); // quando fonte=msg: o mensageiro específico (Instagram, Telegram, …)
  var idMsgDeepLink = paramUrl("msg", null); // deep-link vindo de um achado da IA: abre e destaca a mensagem
  var destacarMsgId = null; // mensagem a destacar quando a conversa alvo terminar de carregar
  var nomeFonte = fonte === "sms" ? "SMS" : fonte === "msg" ? (app || "Mensageiro") : "WhatsApp";
  var rotulos = { wpp: "conversa(s) de WhatsApp", sms: "conversa(s) de SMS", msg: "conversa(s) de " + nomeFonte };
  var tpOrigemMensagem = fonte === "sms" ? "sms_mensagem" : fonte === "msg" ? "msg_mensagem" : "wpp_mensagem";
  // marcar a CONVERSA inteira (lista lateral), e não só uma mensagem dela — na exportação é o
  // que separa "sai o fio completo" de "saem só as bolhas marcadas"
  var tpOrigemConversa = fonte === "sms" ? "sms_conversa" : fonte === "msg" ? "msg_conversa" : "wpp_conversa";

  var $app = $(".wa-app");
  var $lista = $("#wa-conversas");
  var $mensagens = $("#wa-mensagens");
  var $header = $("#wa-main-header");
  var $label = $("#wa-inquerito-label");
  var $participantes = $("#wa-participantes");
  var $participantesChips = null; // metade esquerda da barra (chips); a direita é a busca

  var $modalMidia = $("#modal-midia");
  var $midiaTitulo = $("#midia-titulo");
  var $midiaCorpo = $("#midia-corpo");

  var $modalAssoc = $("#modal-associacao");
  var $assocTitulo = $("#assoc-titulo");
  var $assocStatus = $("#assoc-status");
  var $assocPessoaInput = $("#assoc-pessoa-input");
  var $assocPessoaResultados = $("#assoc-pessoa-resultados");
  var $assocPessoaSelecionada = $("#assoc-pessoa-selecionada");
  var $assocMunicipioInput = $("#assoc-municipio-input");
  var $assocMunicipioResultados = $("#assoc-municipio-resultados");
  var $assocMunicipioSelecionado = $("#assoc-municipio-selecionado");
  var $assocUfSelect = $("#assoc-uf-select");

  var idConversaAtual = null;
  var nomeConversaAtual = null;
  var conversasPorId = {};      // id_conversa -> conversa, para o clique delegado achar os dados do item
  var $conversaAtiva = null;    // item marcado como .active; guardado para não varrer a lista inteira
  var contatoAssociando = null; // { id_contato, nome, ... }
  var pessoaSelecionada = null; // { id_pessoa, no_pessoa, nu_cpf }
  var municipioSelecionado = null; // { id_municipio, no_municipio, sg_uf }

  // Busca na conversa (mora na barra dos participantes, encostada à direita)
  var $buscaConversaWrap = null;
  var $buscaConversaInput = null;
  var $buscaConversaLimpar = null;
  var $buscaConversaResultados = null;
  var buscaConversaDebounce = null;
  var buscaConversaSeq = 0;     // descarta resposta atrasada de um termo já abandonado
  var buscaConversaAtivo = -1;  // índice do resultado destacado pelas setas do teclado
  var LIMITE_BUSCA_CONVERSA = 20;

  function debounce(fn, ms) { return AnalyserUtils.debounce(fn, ms); }

  // ---------- Busca na conversa (barra dos participantes) ----------
  // Fica à direita, na mesma linha dos chips de contato. No cabeçalho ela disputava a largura com
  // o nome da conversa de um lado e com a trilha + o botão "Navegar" do outro (flutuantes,
  // injetados por js/navegacao_inquerito.js) — sobrava pouco para os três.

  function iniciarBuscaConversa() {
    // Remove handler anterior se existir
    $(document).off("click.buscaConversa");

    // Se já existe, remove do DOM para recriar (a barra foi limpa)
    if ($buscaConversaWrap) {
      $buscaConversaWrap.remove();
      $buscaConversaWrap = null;
    }

    $buscaConversaInput = $("<input>")
      .attr({
        type: "text",
        placeholder: "Buscar nesta conversa…",
        autocomplete: "off",
        title: "Busca em todo o histórico da conversa, inclusive nas transcrições de áudio",
        "aria-label": "Buscar mensagem nesta conversa"
      })
      .addClass("wa-conversa-busca-input");

    $buscaConversaLimpar = $("<button>")
      .addClass("wa-conversa-busca-limpar hidden")
      .attr({ type: "button", title: "Limpar busca (Esc)", "aria-label": "Limpar busca" })
      .html("&times;")
      .on("click", function () {
        limparBuscaConversa();
        $buscaConversaInput.focus();
      });

    $buscaConversaResultados = $("<div>").addClass("wa-busca-resultados hidden");

    $buscaConversaWrap = $("<div>").addClass("wa-conversa-busca")
      .append($("<span>").addClass("wa-busca-icone").html("&#128269;"))
      .append($buscaConversaInput)
      .append($buscaConversaLimpar)
      .append($buscaConversaResultados);

    $buscaConversaWrap.appendTo($participantes);

    buscaConversaDebounce = debounce(executarBuscaConversa, 300);

    $buscaConversaInput.on("input", function () {
      $buscaConversaLimpar.toggleClass("hidden", !$buscaConversaInput.val());
      buscaConversaDebounce();
    });

    // teclado: ↓/↑ percorrem os resultados, Enter abre o destacado (ou o primeiro), Esc limpa —
    // dá para ir do termo à bolha sem tirar a mão do teclado
    $buscaConversaInput.on("keydown", function (e) {
      if (e.key === "Escape") {
        limparBuscaConversa();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        moverSelecaoBusca(e.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        var $itens = itensBusca();
        if (!$itens.length) { executarBuscaConversa(); return; }
        var $alvo = buscaConversaAtivo >= 0 ? $itens.eq(buscaConversaAtivo) : $itens.first();
        $alvo.trigger("click");
      }
    });

    // voltar ao campo já com uma busca feita reabre a lista, em vez de exigir digitar de novo
    $buscaConversaInput.on("focus", function () {
      if (itensBusca().length) $buscaConversaResultados.removeClass("hidden");
    });

    // Fecha resultados ao clicar fora
    $(document).on("click.buscaConversa", function (e) {
      if (!$buscaConversaWrap || !$buscaConversaWrap.length) return;
      if (!$buscaConversaWrap.is(e.target) && $buscaConversaWrap.has(e.target).length === 0) {
        $buscaConversaResultados.addClass("hidden");
      }
    });
  }

  function itensBusca() {
    return $buscaConversaResultados ? $buscaConversaResultados.find(".wa-busca-resultado-item") : $();
  }

  // estados intermediários (buscando / vazio / erro) no lugar de um painel que simplesmente
  // não aparece — sem eles não dava para saber se a busca falhou ou se não havia resultado
  function avisoBusca(texto) {
    $buscaConversaResultados.empty()
      .append($("<div>").addClass("wa-busca-aviso").text(texto))
      .removeClass("hidden");
    buscaConversaAtivo = -1;
  }

  function executarBuscaConversa() {
    var termo = $.trim($buscaConversaInput.val());
    buscaConversaAtivo = -1;
    if (termo.length < 2) {
      $buscaConversaResultados.empty().addClass("hidden");
      return;
    }
    if (!idConversaAtual) return;

    var seq = ++buscaConversaSeq;
    avisoBusca("Buscando…");

    $.getJSON("/api/investigacao/conversas/" + idConversaAtual + "/buscar-mensagens", {
      termo: termo,
      fonte: fonte,
      limite: LIMITE_BUSCA_CONVERSA
    }).done(function (resultados) {
      if (seq !== buscaConversaSeq) return; // resposta de um termo/conversa já abandonado
      if (!resultados || !resultados.length) {
        avisoBusca("Nenhuma mensagem com “" + termo + "”.");
        return;
      }
      $buscaConversaResultados.empty();
      var total = resultados.length;
      var resumo = total === 1 ? "1 mensagem encontrada" : total + " mensagens encontradas";
      if (total >= LIMITE_BUSCA_CONVERSA) resumo = "as " + total + " mais recentes"; // a API corta no limite
      $("<div>").addClass("wa-busca-resumo").text(resumo).appendTo($buscaConversaResultados);
      resultados.forEach(function (r) {
        $buscaConversaResultados.append(renderResultadoBusca(r, termo));
      });
      $buscaConversaResultados.removeClass("hidden");
    }).fail(function () {
      if (seq !== buscaConversaSeq) return;
      avisoBusca("Não foi possível buscar agora.");
    });
  }

  function moverSelecaoBusca(passo) {
    var $itens = itensBusca();
    if (!$itens.length) return;
    $buscaConversaResultados.removeClass("hidden");
    buscaConversaAtivo = (buscaConversaAtivo + passo + $itens.length) % $itens.length;
    $itens.removeClass("ativo");
    var alvo = $itens.eq(buscaConversaAtivo).addClass("ativo")[0];
    if (alvo && alvo.scrollIntoView) alvo.scrollIntoView({ block: "nearest" });
  }

  // mensagem longa vira um trecho em volta do termo: a lista mostrava o texto inteiro e um
  // resultado só já enchia o painel, escondendo os demais
  function trechoDoTermo(texto, termo) {
    var CONTEXTO = 70;
    if (texto.length <= 180) return texto;
    var pos = texto.toLowerCase().indexOf(termo.toLowerCase());
    if (pos < 0) return texto.slice(0, 180) + "…";
    var ini = Math.max(0, pos - CONTEXTO);
    var fim = Math.min(texto.length, pos + termo.length + CONTEXTO);
    return (ini > 0 ? "…" : "") + texto.slice(ini, fim) + (fim < texto.length ? "…" : "");
  }

  function renderResultadoBusca(msg, termo) {
    var texto = msg.tx_mensagem || "";
    var transcricao = msg.transcricao || "";
    var isTranscricao = !texto && transcricao;
    var exibirTexto = String(texto || transcricao);

    // escapa ANTES de destacar: o texto vem da extração e vai para .html() — sem isso um "<b>"
    // numa mensagem virava marcação de verdade
    var esc = window.AnalyserUtils.escaparHtml;
    var html = esc(trechoDoTermo(exibirTexto, termo));
    if (termo) {
      var regex = new RegExp("(" + esc(termo).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
      html = html.replace(regex, '<span class="destaque">$1</span>');
    }

    var $avatar = $("<div>").addClass("wa-avatar wa-avatar-sm wa-busca-resultado-avatar")
      .text((msg.remetente || "?").charAt(0).toUpperCase());

    var $topo = $("<div>").addClass("wa-busca-resultado-topo")
      .append($("<div>").addClass("wa-busca-resultado-remetente").text(msg.remetente || "Desconhecido"));
    if (msg.dt_mensagem) {
      $topo.append($("<div>").addClass("wa-busca-resultado-data").text(formatarDataHora(msg.dt_mensagem)));
    }

    var $conteudo = $("<div>").addClass("wa-busca-resultado-conteudo")
      .append($topo)
      .append($("<div>").addClass("wa-busca-resultado-texto").html(html));

    if (isTranscricao && transcricao) {
      $conteudo.append($("<div>").addClass("wa-busca-resultado-transcricao").text("transcrição de áudio/vídeo"));
    }

    var $item = $("<div>").addClass("wa-busca-resultado-item")
      .attr({ "data-id-mensagem": msg.id_mensagem, title: "Ir para esta mensagem" })
      .append($avatar)
      .append($conteudo)
      .on("mouseenter", function () {
        itensBusca().removeClass("ativo");
        buscaConversaAtivo = itensBusca().index(this);
        $(this).addClass("ativo");
      })
      .on("click", function () {
        var idMsg = $(this).attr("data-id-mensagem");
        if (!idConversaAtual || !idMsg) return;
        $buscaConversaResultados.addClass("hidden");
        // reaproveita o caminho do deep-link da IA (irParaMensagem, mais abaixo): se a bolha já
        // está carregada ele só rola até ela; senão reabre a conversa centrada na mensagem
        irParaMensagem(idConversaAtual, Number(idMsg));
      });

    return $item;
  }

  function limparBuscaConversa() {
    buscaConversaSeq++;      // ignora resposta em voo (evita repovoar a lista já limpa)
    buscaConversaAtivo = -1;
    if ($buscaConversaInput) {
      $buscaConversaInput.val("");
    }
    if ($buscaConversaLimpar) {
      $buscaConversaLimpar.addClass("hidden");
    }
    if ($buscaConversaResultados) {
      $buscaConversaResultados.empty().addClass("hidden");
    }
  }

  document.title = nomeFonte + " · PolícIA";
  $("#wa-sidebar-titulo").text("Conversas " + nomeFonte);

  // na página de mensageiros, tematiza tudo pela cor do app: avatar (inicial), bolhas enviadas,
  // acento (nome do remetente/links) e um filete no cabeçalho — cada aplicação com identidade própria
  var CORES_APP = {
    Instagram: "#c13584", Telegram: "#229ed9", Facebook: "#1877f2", "Facebook Messenger": "#0084ff",
    Discord: "#5865f2", TikTok: "#010101", Twitter: "#1da1f2", X: "#111111", Signal: "#3a76f0",
    Snapchat: "#b8a300", Skype: "#00aff0", Viber: "#7360f2", WeChat: "#07c160", LINE: "#06c755",
    Kik: "#5bc236", Threema: "#2b7a2b", Recents: "#8696a0", Desconhecido: "#8696a0"
  };
  // O WhatsApp Business também é fonte=msg, mas abre em whatsapp.html e deve ficar com a cara do
  // WhatsApp (verde do tema, abas de grupo/Status) — nada de repintar pela cor de app.
  if (fonte === "msg" && app && !MapaTelas.ehAppWhatsApp(app)) {
    var corApp = CORES_APP[app] || "#8696a0";
    var $avatar = $("#wa-avatar-app");
    if ($avatar.length) $avatar.css("background", corApp).text(app.charAt(0).toUpperCase());
    // sobrescreve as variáveis do tema WhatsApp pela cor do app (bolha enviada + acento). As
    // variáveis são definidas em .wa-app, então o override precisa ir nesse elemento (inline vence
    // a regra do stylesheet), não em :root.
    var appEl = document.querySelector(".wa-app");
    if (appEl) {
      appEl.style.setProperty("--wa-bubble-sent", corApp);
      appEl.style.setProperty("--wa-accent", corApp);
    }
    document.body.classList.add("tema-app");
  }

  function formatarDataHora(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // Formatadores reaproveitados. d.toLocaleDateString(..., {timeZone}) constrói um Intl.DateTimeFormat
  // NOVO a cada chamada — na lista de conversas isso rodava uma vez por conversa (~1,8 s em 20 mil).
  // Reutilizando a instância, o mesmo trabalho cai para ~0,3 s. A saída é idêntica (verificado).
  var TZ_BR = "America/Sao_Paulo";
  var FMT_DATA = new Intl.DateTimeFormat("pt-BR", { timeZone: TZ_BR });
  var FMT_HORA = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: TZ_BR });

  // carimbo completo do rodapé da bolha: "2022-12-08 21:27:09 -03:00". O fuso é explícito porque a
  // hora do UFDR vem sem indicação de origem e o relatório precisa deixar claro em que fuso ela está
  // sendo apresentada (Brasília); "longOffset" devolve "GMT-03:00", de onde tiramos só o deslocamento.
  var FMT_CARIMBO = new Intl.DateTimeFormat("pt-BR", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hourCycle: "h23", timeZone: TZ_BR, timeZoneName: "longOffset",
  });

  function formatarCarimbo(iso) {
    if (!iso) return "";
    var p = {};
    FMT_CARIMBO.formatToParts(_dataDe(iso)).forEach(function (parte) { p[parte.type] = parte.value; });
    var off = String(p.timeZoneName || "").replace(/^(GMT|UTC)/, "").replace("−", "-");
    if (/^[+-]\d$/.test(off)) off = off[0] + "0" + off[1];   // "-3" -> "-03"
    if (/^[+-]\d\d$/.test(off)) off += ":00";                 // "-03" -> "-03:00"
    if (!off) off = "+00:00";
    return p.year + "-" + p.month + "-" + p.day + " " +
           p.hour + ":" + p.minute + ":" + p.second + " " + off;
  }

  // "hoje" também era recalculado a cada chamada; cacheado por um minuto, o bastante para a virada
  // do dia ser percebida sem refazer new Date()+toDateString() por item da lista
  var _hojeStr = null, _hojeEm = 0;
  function hojeDateString() {
    var agora = Date.now();
    if (_hojeStr === null || agora - _hojeEm > 60000) {
      _hojeStr = new Date(agora).toDateString();
      _hojeEm = agora;
    }
    return _hojeStr;
  }

  // hora se for hoje, senão a data curta — igual ao WhatsApp Web na lista de conversas
  function formatarHoraLista(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    if (d.toDateString() === hojeDateString()) return FMT_HORA.format(d);
    return FMT_DATA.format(d);
  }

  function _dataDe(iso) {
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
  }

  // chave do dia (DD/MM/AAAA em horário de Brasília) — usada só para detectar a virada de dia
  function diaChaveDe(iso) {
    if (!iso) return "";
    return FMT_DATA.format(_dataDe(iso));
  }

  // rótulo do divisor de dia, estilo WhatsApp (ex.: "Sexta-feira, 25 de dezembro de 2024")
  function formatarDataDivisor(iso) {
    if (!iso) return "";
    var s = _dataDe(iso).toLocaleDateString("pt-BR", {
      weekday: "long", day: "2-digit", month: "long", year: "numeric", timeZone: "America/Sao_Paulo",
    });
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  var CORES_AVATAR = ["#f57c72", "#e0985a", "#6fcf97", "#4fb3d9", "#9b8afb", "#f2789f", "#54c7b8", "#c99342"];

  function corAvatar(nome) {
    var hash = 0;
    for (var i = 0; i < nome.length; i++) hash = (hash * 31 + nome.charCodeAt(i)) >>> 0;
    return CORES_AVATAR[hash % CORES_AVATAR.length];
  }

  var URL_REGEX = /(https?:\/\/[^\s]+)/g;

  function renderTextoComLinks(texto, $container) {
    texto.split(URL_REGEX).forEach(function (parte) {
      if (!parte) return;
      if (/^https?:\/\//.test(parte)) {
        $("<a>").addClass("wa-link").attr({ href: parte, target: "_blank", rel: "noopener noreferrer" }).text(parte).appendTo($container);
      } else {
        $container.append(document.createTextNode(parte));
      }
    });
  }

  var ICONES_ANEXO = { pdf: "📄", image: "🖼️", audio: "🎵", video: "🎞️", generico: "📎" };

  function categoriaAnexo(tipo) {
    tipo = (tipo || "").toLowerCase();
    if (tipo.indexOf("pdf") !== -1) return "pdf";
    if (tipo.indexOf("image") !== -1) return "image";
    if (tipo.indexOf("audio") !== -1) return "audio";
    if (tipo.indexOf("video") !== -1) return "video";
    return "generico";
  }

  // Reflete uma transcrição corrigida na bolha da conversa (embaixo do card do anexo), sem
  // precisar recarregar a janela de mensagens. O card e (se já existir) o texto de transcrição
  // carregam data-anexo-id para serem achados aqui.
  function sincronizarTranscricaoNaBolha(idAnexo, texto) {
    var $existente = $('.wa-bolha-transcricao[data-anexo-id="' + idAnexo + '"]');
    if (texto) {
      if ($existente.length) {
        $existente.text("Transcrição: " + texto);
      } else {
        var $card = $('.wa-anexo-card[data-anexo-id="' + idAnexo + '"]');
        if ($card.length) {
          $("<div>").addClass("wa-bolha-transcricao").attr("data-anexo-id", idAnexo)
            .text("Transcrição: " + texto).insertAfter($card);
        }
      }
    } else {
      $existente.remove();
    }
  }

  // Transcrição feita na extração (faster-whisper) logo abaixo do player, para acompanhar a
  // escuta — do áudio E do áudio do vídeo (a transcrever_video transcreve os dois, e a revisão
  // humana é o mesmo gesto: ouvir e ajustar o texto). Sempre mostrada quando dá para
  // identificar o anexo, com opção de corrigir o texto reconhecido automaticamente (ele erra,
  // sobretudo em áudio ruim). A correção é editada no próprio modal, e não num diálogo por
  // cima: o player precisa continuar à mão (e tocando) para reouvir o trecho enquanto se ajusta.
  function montarTranscricaoMidia(cat, transcricao, idAnexo) {
    var dito = cat === "video" ? "no vídeo" : "no áudio";
    if (!idAnexo) {
      if (!transcricao) return;
      $("<div>").addClass("wa-midia-transcricao")
        .append($("<div>").addClass("wa-midia-transcricao-rotulo").text("Transcrição"))
        .append($("<div>").addClass("wa-midia-transcricao-texto").text(transcricao))
        .appendTo($midiaCorpo);
      return;
    }
    var $wrap = $("<div>").addClass("wa-midia-transcricao").appendTo($midiaCorpo);
    var $cabecalho = $("<div>").addClass("wa-midia-transcricao-cabecalho").appendTo($wrap);
    $("<div>").addClass("wa-midia-transcricao-rotulo").text("Transcrição").appendTo($cabecalho);
    var $btnCorrigir = $("<button>").attr("type", "button").addClass("wa-midia-transcricao-corrigir")
      .text("✎ corrigir").appendTo($cabecalho);
    var $texto = $("<div>").addClass("wa-midia-transcricao-texto")
      .toggleClass("wa-midia-transcricao-vazia", !transcricao)
      .text(transcricao || "(sem transcrição)")
      .appendTo($wrap);
    var $editor = $("<div>").addClass("wa-midia-transcricao-editor hidden").appendTo($wrap);
    var $campo = $("<textarea>").addClass("dialogo-campo").attr("maxlength", 20000)
      .attr("placeholder", "Ajuste o texto para refletir o que foi dito " + dito + ".").appendTo($editor);
    var $acoes = $("<div>").addClass("wa-midia-transcricao-acoes").appendTo($editor);
    var $salvar = $("<button>").attr("type", "button").addClass("btn btn-primary").text("Salvar").appendTo($acoes);
    var $cancelar = $("<button>").attr("type", "button").addClass("btn btn-secondary").text("Cancelar").appendTo($acoes);

    var modoEdicao = function (ligado) {
      $editor.toggleClass("hidden", !ligado);
      $texto.toggleClass("hidden", ligado);
      $btnCorrigir.toggleClass("hidden", ligado);
    };

    $btnCorrigir.on("click", function () {
      $campo.val(transcricao || "");
      modoEdicao(true);
      $campo.trigger("focus");
    });
    $cancelar.on("click", function () { modoEdicao(false); });
    $campo.on("keydown", function (e) { if (e.key === "Escape") { e.stopPropagation(); modoEdicao(false); } });
    $salvar.on("click", function () {
      var novoTexto = $.trim($campo.val());
      $salvar.prop("disabled", true);
      $.ajax({
        url: "/api/investigacao/anexos/" + idAnexo + "/transcricao?fonte=" + fonte,
        method: "PUT",
        contentType: "application/json",
        data: JSON.stringify({ texto: novoTexto })
      }).done(function (resp) {
        transcricao = resp.transcricao || "";
        $texto.text(transcricao || "(sem transcrição)").toggleClass("wa-midia-transcricao-vazia", !transcricao);
        sincronizarTranscricaoNaBolha(idAnexo, transcricao);
        modoEdicao(false);
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar a correção da transcrição." });
      }).always(function () {
        $salvar.prop("disabled", false);
      });
    });
  }

  function abrirModalMidia(cat, url, nome, transcricao, idAnexo) {
    var tituloPadrao = cat === "image" ? "Imagem" : cat === "video" ? "Vídeo" : "Áudio";
    $midiaTitulo.text(nome || tituloPadrao);
    $midiaCorpo.empty();
    // o binário é lido do .ufdr sob demanda (pode demorar) — mostra "carregando…" até chegar
    var $carregando = $("<div>").addClass("wa-midia-carregando")
      .html('<span class="spinner"></span> <span>carregando…</span>').appendTo($midiaCorpo);
    if (cat === "image") {
      $("<img>").addClass("wa-midia-imagem").attr({ alt: nome || "imagem" })
        .on("load", function () { $carregando.remove(); })
        .on("error", function () { $carregando.html("⚠ não foi possível carregar este anexo."); })
        .attr("src", url)
        .appendTo($midiaCorpo);
    } else if (cat === "video") {
      var $video = $("<video>").addClass("wa-midia-video").attr({ controls: true, autoplay: true })
        .on("canplay loadeddata", function () { $carregando.remove(); })
        .on("error", function () { $carregando.html("⚠ não foi possível carregar este anexo."); })
        .attr("src", url)
        .appendTo($midiaCorpo);
      // 1×/1,5×/2× também para o vídeo: quando o que importa é a fala, a escuta em série ajuda
      VelocidadeMidia.controle($video).appendTo($midiaCorpo);
      // a transcrição do áudio do vídeo (transcrever_video na extração) com a mesma revisão
      // humana do áudio — o PUT de transcrição do backend vale para qualquer anexo de mídia
      montarTranscricaoMidia("video", transcricao, idAnexo);
    } else {
      var $audio = $("<audio>").addClass("wa-midia-audio").attr({ controls: true, autoplay: true })
        .on("canplay loadeddata", function () { $carregando.remove(); })
        .on("error", function () { $carregando.html("⚠ não foi possível carregar este anexo."); })
        .attr("src", url)
        .appendTo($midiaCorpo);
      // 1×/1,5×/2× logo abaixo do player: áudio de WhatsApp costuma ser ouvido em série
      VelocidadeMidia.controle($audio).appendTo($midiaCorpo);
      montarTranscricaoMidia("audio", transcricao, idAnexo);
    }
    $modalMidia.removeClass("hidden");
  }

  function fecharModalMidia() {
    $modalMidia.addClass("hidden");
    $midiaCorpo.empty(); // para o áudio/descarrega a imagem ao fechar
  }

  $("#midia-fechar").on("click", fecharModalMidia);
  $modalMidia.on("click", function (e) { if (e.target === this) fecharModalMidia(); });

  // miniatura da imagem com placeholder "carregando…" (o binário vem do .ufdr sob demanda) e
  // estados de carregada/erro — abre em tamanho cheio no clique
  function renderImagemAnexo(a) {
    var urlImagem = "/api/investigacao/anexos/" + a.id + "/imagem?fonte=" + fonte;
    var $wrap = $("<div>").addClass("wa-anexo-imagem-wrap");
    if (a.eh_figurinha) {
      $("<span>").addClass("wa-badge-figurinha").text("Figurinha").appendTo($wrap);
    }
    var $ph = $("<div>").addClass("wa-anexo-carregando")
      .html('<span class="spinner"></span> <span>carregando…</span>').appendTo($wrap);
    $("<img>")
      .addClass("wa-anexo-imagem" + (a.eh_figurinha ? " wa-anexo-imagem-sticker" : ""))
      .attr({ alt: a.nome || "imagem", loading: "lazy" })
      .on("load", function () { $ph.remove(); })
      .on("error", function () { $ph.html("⚠ imagem indisponível"); })
      .on("click", function () { abrirModalMidia("image", urlImagem, a.nome); })
      .attr("src", urlImagem)
      .appendTo($wrap);
    return $wrap;
  }

  // Rótulo do que a mídia ORIGINAL era, para a legenda da miniatura. Sai do tipo declarado
  // (que descreve o original), não do binário — que aqui é sempre a miniatura.
  var ROTULO_ORIGINAL = { video: "Vídeo", pdf: "Documento", audio: "Áudio", image: "Imagem", generico: "Arquivo" };

  // O Cellebrite grava a LEGENDA da mídia no Title do anexo — o texto que o WhatsApp mostra
  // embaixo da foto/vídeo, e que muitas vezes é a única coisa que sobrou de uma mídia não
  // recuperada. Em áudio e figurinha o mesmo campo não é legenda: vem "Duration: 00:00:16"
  // ou "Sticker", metadado que continua fazendo sentido como nome do card.
  var TITULO_METADADO = /^(duration:\s*\d{1,2}:\d{2}(:\d{2})?|sticker)$/i;

  function legendaAnexo(a) {
    var t = $.trim(a.titulo || "");
    if (!t || TITULO_METADADO.test(t)) return "";
    if (a.nome && t === a.nome) return "";
    return t;
  }

  // Nome do card: o arquivo. A legenda saiu do card (vai como texto da bolha, inteira — no
  // card ela era cortada em uma linha), então quando não há nome de arquivo — o caso da mídia
  // de visualização única, que o UFDR traz sem Filename — o card se identifica pelo tipo.
  function nomeCardAnexo(a, cat) {
    if (a.nome) return a.nome;
    if (a.titulo && !legendaAnexo(a)) return a.titulo;
    return ROTULO_ORIGINAL[cat] || "anexo";
  }

  // "Visualização única" — o ⓵ que o IPED mostra na mensagem. O UFDR não tem campo próprio
  // para isso: o Cellebrite marca a mensagem com o rótulo SelfDestruct (IN_TEMPORARIA) e não
  // traz binário nenhum do anexo, porque o WhatsApp apaga a mídia do aparelho assim que ela é
  // aberta. Conferido contra o ChatStorage.sqlite da própria extração NB2026.66391
  // (ZMESSAGETYPE 38/39/53 = imagem/vídeo/áudio de visualização única): a regra pega 874 das
  // 881 mensagens, sem falso positivo. O rótulo sozinho não serve — ele também marca as
  // mensagens temporárias comuns, inclusive texto, cujo arquivo veio na extração.
  function ehVisualizacaoUnica(m, a) {
    return !!m.in_temporaria && !a.disponivel;
  }

  function marcaVisualizacaoUnica() {
    return $("<div>").addClass("wa-visualizacao-unica")
      .attr("title", "Mídia de visualização única: o WhatsApp apaga o arquivo do aparelho assim " +
                     "que ele é aberto, por isso ele não vem na extração — só a legenda e os metadados")
      .append($("<span>").addClass("wa-visualizacao-unica-icone").text("1"))
      .append($("<span>").text("Visualização única"));
  }

  // Miniatura de uma mídia que NÃO veio na extração: o Cellebrite guarda só o quadro/preview
  // (JPEG de 1-4 KB) mas mantém o tipo do original — um vídeo do WhatsApp cujo binário não foi
  // recuperado. Antes isso virava um card "▶ assistir" que abria um player vazio; a miniatura,
  // que muitas vezes é a única coisa que se pode ver da mídia, ficava inacessível.
  function renderMiniatura(a) {
    var cat = categoriaAnexo(a.tipo);
    var $wrap = $("<div>").addClass("wa-anexo-miniatura");
    $wrap.append(renderImagemAnexo(a));
    // a legenda da mídia não entra aqui: ela vai inteira como texto da bolha (ver renderMensagem)
    var titulo = a.nome || (legendaAnexo(a) ? "" : a.titulo);
    $("<div>").addClass("wa-anexo-miniatura-legenda")
      .attr("title", "O UFDR trouxe apenas a miniatura; o arquivo original não foi recuperado na extração")
      .text(ICONES_ANEXO[cat] + " " + (ROTULO_ORIGINAL[cat] || "Arquivo") +
            (titulo ? " — " + titulo : "") + " · só a miniatura foi extraída")
      .appendTo($wrap);
    return $wrap;
  }

  // Traduz a categoria do binário REAL (vinda da API) para as categorias desta tela.
  // O `tipo` declarado no anexo descreve a mídia ORIGINAL e mente com frequência: áudio
  // gravado como video/mp4, miniatura gravada como application/pdf. Quem manda no player
  // é o arquivo que existe.
  var CAT_BINARIO = { imagem: "image", audio: "audio", video: "video", documento: "generico" };

  function renderAnexo(a) {
    if (a.so_miniatura) return renderMiniatura(a);
    var catDeclarada = categoriaAnexo(a.tipo);
    var cat = CAT_BINARIO[a.categoria_binario] || catDeclarada;
    // documento cai no genérico, mas PDF continua merecendo o ícone de PDF
    if (cat === "generico" && catDeclarada === "pdf") cat = "pdf";
    if (cat === "image" && a.imagem_disponivel) return renderImagemAnexo(a);

    // disponibilidade do binário no .ufdr, por categoria: imagem/áudio têm flags próprios
    // (compatibilidade), os demais tipos (vídeo, pdf, documentos, outros) usam o flag genérico
    var disp = cat === "image" ? a.imagem_disponivel
             : cat === "audio" ? a.audio_disponivel
             : a.disponivel;
    var indisponivel = !disp;

    var $card = $("<div>").addClass("wa-anexo-card" + (indisponivel ? " wa-anexo-indisponivel" : ""))
      .attr("data-anexo-id", a.id);
    $("<div>").addClass("wa-anexo-icone wa-anexo-icone-" + cat).text(ICONES_ANEXO[cat]).appendTo($card);
    var $info = $("<div>").addClass("wa-anexo-info").appendTo($card);
    $("<div>").addClass("wa-anexo-nome").text(nomeCardAnexo(a, cat)).appendTo($info);
    if (a.tipo) $("<div>").addClass("wa-anexo-tipo").text(a.tipo).appendTo($info);

    var urlAudio = "/api/investigacao/anexos/" + a.id + "/audio?fonte=" + fonte;
    var urlArquivo = "/api/investigacao/anexos/" + a.id + "/arquivo?fonte=" + fonte;

    if (cat === "audio" && disp) {
      $card.addClass("wa-anexo-clicavel");
      $("<div>").addClass("wa-anexo-acao").text("▶ ouvir").appendTo($info);
      $card.on("click", function () { abrirModalMidia("audio", urlAudio, a.nome, a.transcricao, a.id); });
    } else if (cat === "video" && disp) {
      $card.addClass("wa-anexo-clicavel");
      $("<div>").addClass("wa-anexo-acao").text("▶ assistir").appendTo($info);
      $card.on("click", function () { abrirModalMidia("video", urlArquivo, a.nome, a.transcricao, a.id); });
    } else if (disp) {
      // pdf/documento/outros disponíveis: abre no navegador (inline) ou baixa
      $card.addClass("wa-anexo-clicavel");
      $("<div>").addClass("wa-anexo-acao").text("⬇ abrir / baixar").appendTo($info);
      $card.on("click", function () { window.open(urlArquivo, "_blank", "noopener"); });
    } else {
      $("<div>").addClass("wa-anexo-acao").text("indisponível no arquivo").appendTo($info);
    }
    return $card;
  }

  // Mensagem de "Localização compartilhada" do WhatsApp: sem Body, só Latitude/Longitude —
  // mesma coordenada que o Cellebrite Reader/IPED mostram, aqui como link pro Google Maps.
  function renderLocalizacao(lat, lon) {
    var url = "https://www.google.com/maps?q=" + lat + "," + lon;
    var $card = $("<a>").addClass("wa-anexo-card wa-anexo-clicavel wa-bolha-localizacao")
      .attr({ href: url, target: "_blank", rel: "noopener" });
    $("<div>").addClass("wa-anexo-icone wa-anexo-icone-localizacao").text("📍").appendTo($card);
    var $info = $("<div>").addClass("wa-anexo-info").appendTo($card);
    $("<div>").addClass("wa-anexo-nome").text("Localização compartilhada").appendTo($info);
    $("<div>").addClass("wa-anexo-tipo").text(lat.toFixed(6) + ", " + lon.toFixed(6)).appendTo($info);
    return $card;
  }

  // Escapa para interpolação em string HTML. Inclui as aspas porque também usamos isto DENTRO de
  // atributos (data-id, style) — sem isso, um valor com " escaparia do atributo. Obrigatório em todo
  // valor interpolado: o texto das mensagens vem da extração, ou seja, é conteúdo não confiável.
  function esc(valor) {
    return String(valor == null ? "" : valor)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function iniciais(nome) {
    if (!nome) return "?";
    var partes = $.trim(nome).split(/\s+/);
    var ini = partes[0].charAt(0);
    if (partes.length > 1) ini += partes[partes.length - 1].charAt(0);
    return ini.toUpperCase();
  }

  function criarAvatar(nome, classeExtra) {
    return $("<div>")
      .addClass("wa-avatar" + (classeExtra ? " " + classeExtra : ""))
      .css("background", corAvatar(nome || "?"))
      .text(iniciais(nome));
  }

  // ✓ enviada/entregue, ✓✓ entregue, ✓✓ azul lida — igual ao WhatsApp, a partir do cd_status_mensagem real
  function iconeStatus(status) {
    if (status === "Read") return $("<span>").addClass("wa-check wa-check-lida").text("✓✓");
    if (status === "Delivered") return $("<span>").addClass("wa-check").text("✓✓");
    return $("<span>").addClass("wa-check").text("✓");
  }

  function nomeConversa(c) {
    // tp_lista é a classificação definitiva do backend: no WhatsApp ela usa o
    // JID @g.us, que continua confiável mesmo quando o UFDR marca tp_conversa
    // incorretamente como "individual".
    if (c.tp_lista === "grupo" || c.tp_conversa === "grupo") return c.no_grupo || "Grupo";
    // o número entra como rótulo quando o aparelho não tinha o contato na agenda: antes essas
    // conversas ficavam todas com o mesmo "(sem identificação)", indistinguíveis entre si —
    // e agora que dá para pesquisar pelo número, o resultado precisa mostrar qual número é.
    return c.participantes || c.no_grupo || telefonesConversa(c).join(", ") || "(sem identificação)";
  }

  // ---------- Pesquisa por número de telefone ----------

  // Só os dígitos. A extração grava o identificador do jeito que o aparelho guardou (o JID
  // "555196772307@s.whatsapp.net" no WhatsApp, "+55 51 99677-2307" no SMS) e o investigador
  // digita o número como ele aparece no relatório que tem em mãos. Comparar dígito a dígito é
  // o que faz os dois se encontrarem.
  function digitosDe(valor) {
    return String(valor == null ? "" : valor).replace(/\D/g, "");
  }

  // Só o que é telefone de verdade: o canal do WhatsApp entra como
  // "120363335391126963@newsletter" e o LID, como "…@lid" — identificadores internos de 18
  // dígitos, que não são o número de ninguém e casariam com qualquer pedaço de número digitado.
  // Telefone é o JID @s.whatsapp.net (e, no SMS/mensageiros, o identificador sem domínio), com
  // no máximo os 15 dígitos do E.164.
  function ehIdentificadorTelefone(ident) {
    return ident.indexOf("@") === -1 || /@s\.whatsapp\.net$/i.test(ident);
  }

  // Os números dos participantes da conversa, um por posição. NÃO junta tudo antes de tirar os
  // não-dígitos: colados, o fim de um número e o começo do seguinte formariam sequências que
  // não existem em ninguém, e a busca casaria com a conversa errada.
  function telefonesConversa(c) {
    return String(c.identificadores || "").split("|")
      .filter(ehIdentificadorTelefone)
      .map(function (ident) { return digitosDe(telefoneDe(ident)); })
      .filter(function (tel) { return tel.length >= 4 && tel.length <= 15; });
  }

  // Um mesmo celular aparece ora com o nono dígito, ora sem — "55 51 99677-2307" e
  // "55 51 9677-2307" são o mesmo aparelho, e qual das duas formas está na extração depende de
  // quem cadastrou o contato. Procura pelas duas: tira o 9 quando o termo o traz, e põe quando
  // não traz. A segunda forma é necessária porque o DDD gruda no bloco de oito — "27 9677-2307"
  // NÃO é pedaço de "5527996772307", o 9 do meio quebra a sequência. Só amplia a busca: o termo
  // como foi digitado continua sendo procurado.
  function variantesTelefone(d) {
    var lista = [d];
    if (d.length >= 9 && d.charAt(d.length - 9) === "9") {
      lista.push(d.slice(0, d.length - 9) + d.slice(d.length - 8));
    } else if (d.length >= 10) {
      // DDD + oito dígitos para cima; com menos que isso, o bloco de oito sozinho já casa
      lista.push(d.slice(0, d.length - 8) + "9" + d.slice(d.length - 8));
    }
    return lista;
  }

  // Devolve o item da lista como STRING de HTML, não como nó jQuery. Uma extração pode ter dezenas
  // de milhares de conversas, e montar isso com $("<div>")… (7 objetos jQuery por item) levava ~11 s
  // para 20 mil conversas; como string + um único innerHTML, o parser nativo faz a mesma árvore em
  // ~150 ms. O clique NÃO é registrado aqui — é delegado uma vez só em $lista (ver adiante).
  // O 🗒️ da conversa entra como STRING aqui, e não via EntidadesUI.botao() (que devolve nós
  // jQuery): montar o item com jQuery levava ~11 s em 20 mil conversas, contra ~150 ms como
  // string. As classes e o data-chave são os mesmos que o widget procura, então
  // EntidadesUI.carregarMarcacoes pinta o contorno das conversas já marcadas sem código extra.
  // Rótulo e classe do badge por tipo de conversa. Grupo não recebe badge: já é o caso comum e
  // marcá-lo só encheria a lista de etiqueta. Os três de mão única, sim — sem eles a listagem
  // mostrava um canal (CNN Brasil, g1…) com a mesma cara de uma conversa com o contato.
  var BADGE_TIPO = {
    status: ["Status", "Atualizações de Status (Stories) — registro de visualizações, não é a conversa com o contato"],
    canal: ["Canal", "Canal do WhatsApp — publicação de mão única, o titular apenas seguia"],
    transmissao: ["Transmissão", "Lista de transmissão — envio de mão única para vários destinatários"]
  };

  function htmlBadgeTipo(tp) {
    var b = BADGE_TIPO[tp];
    if (!b) return "";
    return '<span class="wa-badge-status ' + esc(tp) + '" title="' + esc(b[1]) + '">' + esc(b[0]) + '</span>';
  }

  function htmlConversa(c) {
    var nome = nomeConversa(c);
    var preview = c.ultima_mensagem || (c.eh_status ? "Atualização de Status (sem texto)" : "");
    var chave = tpOrigemConversa + ":" + c.id_conversa;
    // Os números vão no próprio item, já em dígitos: a busca roda a cada tecla sobre milhares de
    // itens e ler um atributo pronto é mais barato do que normalizar o JID de novo a cada letra.
    var tels = telefonesConversa(c);
    // e no title do nome, para o investigador ver POR QUE aquela conversa casou com o número
    // que ele digitou — sem isso o resultado é um nome que não tem nada a ver com o termo
    var titulo = tels.length && tels.join(", ") !== nome ? nome + " · " + tels.join(", ") : nome;
    // corAvatar só devolve valores de CORES_AVATAR (lista literal de hex), então não precisa de escape
    return '<div class="wa-conversa-item" data-id="' + esc(c.id_conversa) + '"' +
             ' data-tipo="' + esc(c.tp_lista || "conversa") + '"' +
             ' data-tel="' + esc(tels.join(" ")) + '">' +
             '<div class="wa-avatar" style="background:' + corAvatar(nome || "?") + '">' +
               esc(iniciais(nome)) +
             '</div>' +
             '<div class="wa-conversa-corpo">' +
               '<div class="wa-conversa-linha1">' +
                 '<span class="wa-conversa-nome" title="' + esc(titulo) + '">' + esc(nome) + '</span>' +
                 htmlBadgeTipo(c.tp_lista) +
                 '<span class="wa-conversa-data">' + esc(formatarHoraLista(c.dt_ultima_atividade)) + '</span>' +
               '</div>' +
               '<div class="wa-conversa-preview">' + esc(preview) + '</div>' +
             '</div>' +
             '<button type="button" class="ent-botao rel-botao wa-conversa-marcar" ' +
               'data-chave="' + esc(chave) + '" data-id-conversa="' + esc(c.id_conversa) + '" ' +
               'title="Marcar esta conversa (triagem do contato: já li, é relevante…)">🗒️</button>' +
           '</div>';
  }

  // ---------- Mensagem de referência (resposta que cita outra) ----------

  // Prévia de UMA linha do que foi citado: o texto, ou — quando a citada não tem texto — o que
  // ela era (áudio/foto/vídeo, com o título que o WhatsApp guarda: "ice runtz og"). Sem isso a
  // citação de uma mídia apareceria como uma tarja vazia.
  function resumoCitada(c) {
    if (c.texto && $.trim(c.texto)) return $.trim(c.texto);
    var a = c.anexo;
    if (a) {
      var cat = categoriaAnexo(a.tipo);
      var rotuloCat = cat === "image" ? "Foto" : cat === "audio" ? "Áudio"
                    : cat === "video" ? "Vídeo" : cat === "pdf" ? "Documento" : "Anexo";
      var titulo = a.titulo || a.nome;
      return ICONES_ANEXO[cat] + " " + rotuloCat + (titulo ? " — " + titulo : "");
    }
    if (c.tem_coordenada) return "📍 Localização compartilhada";
    if (c.apagada) return "Mensagem apagada pelo remetente";
    return "(sem conteúdo)";
  }

  // Rótulo de origem quando a citada NÃO está na conversa aberta. O caso comum é a resposta a um
  // Status (Stories), que o Cellebrite guarda num pseudo-chat à parte — o IPED escreve
  // "Status citado" nessa mesma situação.
  function origemCitada(c) {
    if (!c.outra_conversa) return "";
    if (c.tp_conversa === "status") return "Status citado";
    if (c.tp_conversa === "canal") return "Citada de um canal";
    if (c.tp_conversa === "transmissao") return "Citada de uma lista de transmissão";
    return "Citada de outra conversa" + (c.conversa_nome ? ": " + c.conversa_nome : "");
  }

  // A tarja de citação, no alto da bolha (mesmo lugar em que o WhatsApp e o IPED a mostram).
  // Clicar salta para a mensagem citada — inclusive quando ela está em outra conversa.
  function renderCitacao(m) {
    var c = m.citada;
    if (!c) {
      // o UFDR diz que esta mensagem responde a outra, mas a original não está na extração
      // (apagada antes da coleta, ou fora do recorte exportado): dizer isso é melhor do que
      // desenhar a resposta como se fosse mensagem solta
      return $("<div>").addClass("wa-bolha-citada wa-bolha-citada-ausente")
        .attr("title", "Identificador citado: " + m.nu_ident_citado)
        .append($("<div>").addClass("wa-citada-corpo")
          .text("↩ Resposta a uma mensagem que não está na extração"));
    }
    var $bloco = $("<div>").addClass("wa-bolha-citada")
      .attr("title", "Ir para a mensagem citada");
    var $texto = $("<div>").addClass("wa-citada-texto").appendTo($bloco);
    $("<div>").addClass("wa-citada-autor")
      .text(c.enviada ? "Você" : (c.remetente || "(sem identificação)")).appendTo($texto);
    var origem = origemCitada(c);
    if (origem) $("<div>").addClass("wa-citada-origem").text(origem).appendTo($texto);
    $("<div>").addClass("wa-citada-corpo").text(resumoCitada(c)).appendTo($texto);
    // quadro da mídia citada à direita, como no WhatsApp e no IPED — vale também quando o
    // arquivo original não veio na extração e só restou a miniatura (ver renderMiniatura)
    if (c.anexo && c.anexo.imagem_disponivel && c.anexo.id) {
      $("<img>").addClass("wa-citada-miniatura")
        .attr({ alt: "miniatura da mensagem citada", loading: "lazy" })
        .on("error", function () { $(this).remove(); })
        .attr("src", "/api/investigacao/anexos/" + c.anexo.id + "/imagem?fonte=" + fonte)
        .appendTo($bloco);
    }
    $bloco.on("click", function (e) {
      e.stopPropagation();
      if (!irParaMensagem(c.id_conversa, c.id_mensagem)) {
        Dialogo.alerta({
          titulo: "Mensagem citada",
          mensagem: "A mensagem citada está em outra conversa que não está nesta listagem."
        });
      }
    });
    return $bloco;
  }

  // Bolha sem texto, sem anexo e sem coordenada. Um "(sem conteúdo)" seco escondia dado que o
  // próprio UFDR traz: Subject (é como Facebook/Messenger gravam o conteúdo de algumas mensagens)
  // e DeletionReason/Recalled, que dizem que a mensagem foi apagada pelo remetente — fato
  // relevante para a investigação e bem diferente de "veio vazia". Os três campos chegam de
  // listar_mensagens_conversa e valem para as três famílias (wpp/sms/msg).
  function renderBolhaSemTexto(m, $bolha) {
    var $vazio = $("<div>").css("opacity", 0.6).appendTo($bolha);
    if (m.tx_assunto) {
      $vazio.text("Assunto: " + m.tx_assunto);
    } else if (m.in_apagada_remetente || m.tx_motivo_exclusao) {
      $vazio.text(m.tx_motivo_exclusao
        ? "Mensagem apagada pelo remetente (" + m.tx_motivo_exclusao + ")"
        : "Mensagem apagada pelo remetente");
    } else {
      $vazio.text("(sem conteúdo)");
    }
  }

  // Nem o 📌 (comentário/entidade) nem o 🗒️ (marcar para o relatório) recarregam a conversa:
  // entidades.js acerta o indicador de cada botão no próprio DOM. Antes esta tela passava um
  // callback aoMudar que chamava abrirConversa(), o que remontava a janela mais recente de
  // mensagens e jogava o investigador de volta para o último dia, perdendo onde ele estava.
  function renderMensagem(m, temComentario) {
    var $linha = $("<div>").addClass("wa-bolha-linha" + (m.in_enviada ? " enviada" : ""))
      .attr("data-msg-id", m.id_mensagem)
      .attr("data-msg-dia", diaChaveDe(m.dt_mensagem))          // p/ o divisor detectar a virada de dia
      .attr("data-msg-rotulo", formatarDataDivisor(m.dt_mensagem));
    var $bolha = $("<div>").addClass("wa-bolha");

    if (!m.in_enviada && m.remetente) {
      $("<div>").addClass("wa-bolha-remetente").text(m.remetente).appendTo($bolha);
    }
    // encaminhada e citação vêm antes do conteúdo, como no WhatsApp e no IPED
    if (m.in_encaminhada) {
      $("<div>").addClass("wa-bolha-encaminhada")
        .attr("title", "O UFDR marca esta mensagem como encaminhada").text("↪ Encaminhada")
        .appendTo($bolha);
    }
    if (m.citada || m.nu_ident_citado) {
      $bolha.append(renderCitacao(m));
    }
    if (m.tx_mensagem) {
      renderTextoComLinks(m.tx_mensagem, $("<div>").addClass("wa-bolha-texto").appendTo($bolha));
    }
    if (m.anexos && m.anexos.length) {
      m.anexos.forEach(function (a) {
        if (ehVisualizacaoUnica(m, a)) $bolha.append(marcaVisualizacaoUnica());
        $bolha.append(renderAnexo(a));
        // A legenda vem do Title do anexo e é a única parte da mídia de visualização única
        // que sobrevive na extração: entra como texto da bolha, inteira. Dentro do card ela
        // ocupava a linha do nome do arquivo e era cortada em ~30 caracteres.
        var legenda = legendaAnexo(a);
        if (legenda && legenda !== m.tx_mensagem) {
          renderTextoComLinks(legenda, $("<div>").addClass("wa-bolha-texto wa-anexo-legenda").appendTo($bolha));
        }
        if (a.transcricao) {
          $("<div>").addClass("wa-bolha-transcricao").attr("data-anexo-id", a.id)
            .text("Transcrição: " + a.transcricao).appendTo($bolha);
        }
      });
    }
    var temLocalizacao = m.vl_latitude != null && m.vl_longitude != null;
    if (temLocalizacao) {
      $bolha.append(renderLocalizacao(m.vl_latitude, m.vl_longitude));
    }
    if (!m.tx_mensagem && !temLocalizacao && (!m.anexos || !m.anexos.length)) {
      renderBolhaSemTexto(m, $bolha);
    }

    var $rodape = $("<div>").addClass("wa-bolha-rodape").appendTo($bolha);
    $("<span>").addClass("wa-bolha-hora").attr("title", formatarDataHora(m.dt_mensagem)).text(formatarCarimbo(m.dt_mensagem)).appendTo($rodape);
    if (m.in_enviada) $rodape.append(iconeStatus(m.cd_status_mensagem));
    $rodape.append(EntidadesUI.botao({
      idInquerito: idInquerito,
      tpOrigem: tpOrigemMensagem,
      idOrigem: m.id_mensagem,
      rotulo: "mensagem" + (m.remetente ? " de " + m.remetente : ""),
      temComentario: !!temComentario
    }));

    $linha.append($bolha);
    return $linha;
  }

  function renderCabecalhoConversa(idConversa, nome) {
    $header.empty();

    // Volta para a lista no modo painel único (tela < 860px). Em tela larga o CSS o esconde:
    // os dois painéis convivem e não há para onde voltar.
    $("<button>")
      .addClass("wa-voltar")
      .attr({ type: "button", "aria-label": "Voltar para a lista de conversas", title: "Voltar para a lista" })
      .html("&#8592;")
      .on("click", function () {
        limparBuscaConversa();
        $app.removeClass("conversa-aberta");
      })
      .appendTo($header);

    var $avatar = criarAvatar(nome);
    $header.append($avatar);
    var $info = $("<div>").addClass("wa-main-header-info").appendTo($header);
    // o mesmo badge da listagem também aqui: abertas, essas conversas de mão única não têm nada
    // que as distinga de uma conversa comum (Status vem só com visualizações, canal com posts)
    var tpLista = (conversasPorId[idConversa] || {}).tp_lista;
    var $linhaNome = $("<div>").addClass("wa-main-header-linha").appendTo($info);
    $("<div>").addClass("wa-main-header-nome").text(nome).appendTo($linhaNome);
    var badge = BADGE_TIPO[tpLista];
    if (badge) {
      $("<span>").addClass("wa-badge-status " + tpLista)
        .attr("title", badge[1]).text(badge[0]).appendTo($linhaNome);
      $("<div>").addClass("wa-main-header-sub").text(badge[1]).appendTo($info);
    }

    // Relatório da conversa inteira (modal adiante): fica no cabeçalho, ao alcance de quem lê o fio.
    // À esquerda (logo após o nome) de propósito — o canto direito é reserva da trilha flutuante.
    $("<button>")
      .addClass("wa-header-relatorio")
      .attr({ type: "button", title: "Gerar relatório (.docx) com as mensagens desta conversa" })
      .html("&#128196; Gerar relat\u00f3rio")
      .on("click", abrirModalRelatorioConversa)
      .appendTo($header);

    // foto de perfil do outro lado, lida sob demanda do .ufdr (só WhatsApp); ao carregar,
    // substitui o avatar de iniciais — se não houver foto (404) ou falhar, mantém as iniciais
    if (fonte === "wpp") {
      var urlFoto = "/api/investigacao/conversas/" + idConversa + "/foto?fonte=" + fonte;
      var $foto = $("<img>").addClass("wa-avatar wa-avatar-foto wa-avatar-foto-clicavel")
        .attr({ alt: nome, title: "Ver foto" })
        .on("load", function () { $avatar.remove(); })
        .on("error", function () { $foto.remove(); })
        .on("click", function () { abrirModalMidia("image", urlFoto, nome); })
        .attr("src", urlFoto);
      $foto.insertBefore($avatar);
    }
  }

  // A barra abaixo do cabeçalho: chips de contato à esquerda, busca à direita. É montada ao abrir
  // a conversa (e não quando os participantes chegam) porque a busca não depende deles — em
  // conversa sem participante identificado a barra continua existindo só com a busca.
  function prepararBarraConversa() {
    $participantes.empty().removeClass("hidden");
    $participantesChips = $("<div>").addClass("wa-participantes-chips").appendTo($participantes);
    iniciarBuscaConversa();
  }

  // ---------- Mensagens paginadas (janela recente + carregar anteriores/posteriores) ----------
  // Conversas grandes têm dezenas de milhares de mensagens; renderizar tudo travava o navegador.
  // Carregamos uma janela por vez e o investigador rola para carregar mais.
  var contagensEntidades = {};   // por id_mensagem (contagem de entidades), carregado 1x por conversa
  var primeiroIdCarregado = null; // menor id na tela (para "carregar anteriores")
  var ultimoIdCarregado = null;   // maior id na tela (para "carregar posteriores")

  // Filtro de conteúdo da conversa aberta (barra Tudo/Textos/Mídias): "tudo" não filtra,
  // "texto" mostra só bolhas de texto e "midia" só bolhas com anexo. O filtro vai na URL das
  // janelas de mensagens — o backend o aplica também à paginação (tem_anteriores/posteriores),
  // então "carregar mais" continua coerente com o que está na tela.
  var filtroConteudo = "tudo";

  function urlMensagens(params) {
    var qs = "?fonte=" + fonte;
    if (filtroConteudo !== "tudo") qs += "&tipo=" + filtroConteudo;
    for (var k in params) if (params[k] != null) qs += "&" + k + "=" + encodeURIComponent(params[k]);
    return "/api/investigacao/conversas/" + idConversaAtual + "/mensagens" + qs;
  }

  function fragmentoMensagens(msgs) {
    var frag = document.createDocumentFragment();
    msgs.forEach(function (m) { frag.appendChild(renderMensagem(m, contagensEntidades[m.id_mensagem])[0]); });
    return frag;
  }

  // Insere os divisores de dia (chip com a data, estilo WhatsApp) onde o dia muda. Refaz do zero a
  // cada mudança da lista — as mensagens chegam em janelas (anteriores/posteriores), então recalcular
  // é o jeito simples de manter os divisores corretos nas fronteiras entre lotes.
  function atualizarDivisoresDia() {
    $mensagens.find(".wa-divisor-dia").remove();
    var diaAnterior = null;
    $mensagens.find(".wa-bolha-linha").each(function () {
      var dia = this.getAttribute("data-msg-dia");
      if (dia && dia !== diaAnterior) {
        $("<div>").addClass("wa-divisor-dia")
          .append($("<span>").text(this.getAttribute("data-msg-rotulo") || dia))
          .append(
            $("<button>").addClass("wa-divisor-marcar").attr("type", "button")
              .attr("title", "Marcar todas as mensagens deste dia (já carregadas) para o relatório")
              .text("🗒️ Marcar dia")
              .on("click", function () { marcarDiaParaRelatorio(dia); })
          )
          .insertBefore(this);
        diaAnterior = dia;
      }
    });
  }

  // seleciona (via o mesmo checkbox de lote de cada mensagem) todas as mensagens do dia que já
  // estão carregadas na tela e abre o modal de marcação em lote — não alcança mensagens de dias
  // ainda não carregados (⬆ Carregar mensagens anteriores / ⬇ mais recentes).
  function marcarDiaParaRelatorio(dia) {
    $mensagens.find(".wa-bolha-linha").filter(function () {
      return this.getAttribute("data-msg-dia") === dia;
    }).find(".lote-checkbox").each(function () {
      if (!this.checked) {
        this.checked = true;
        $(this).trigger("change");
      }
    });
    $("#lote-marcar-btn").trigger("click");
  }

  function botaoCarregar(id, texto, direcao) {
    return $("<div>").attr("id", id).addClass("wa-carregar-mais-wrap").append(
      $("<button>").addClass("wa-carregar-mais").text(texto)
        .on("click", function () { carregarMais(direcao); })
    );
  }

  function botaoAntes(tem) {
    $("#wa-btn-antes").remove();
    if (tem) $mensagens.prepend(botaoCarregar("wa-btn-antes", "⬆ Carregar mensagens anteriores", "anteriores"));
  }

  function botaoDepois(tem) {
    $("#wa-btn-depois").remove();
    if (tem) $mensagens.append(botaoCarregar("wa-btn-depois", "⬇ Carregar mensagens mais recentes", "posteriores"));
  }

  function carregarMais(direcao) {
    var anterior = direcao === "anteriores";
    var $wrap = $(anterior ? "#wa-btn-antes" : "#wa-btn-depois");
    $wrap.find("button").prop("disabled", true).text("Carregando…");
    $.getJSON(urlMensagens(anterior ? { antes_id: primeiroIdCarregado } : { depois_id: ultimoIdCarregado }))
      .done(function (resp) {
        var msgs = resp.mensagens || [];
        if (anterior) {
          var alturaAntes = $mensagens[0].scrollHeight;
          botaoAntes(false);
          if (msgs.length) {
            $mensagens.prepend(fragmentoMensagens(msgs));
            primeiroIdCarregado = msgs[0].id_mensagem;
          }
          botaoAntes(resp.tem_anteriores);
          atualizarDivisoresDia();
          // preserva a posição visual ao inserir conteúdo acima (já com os divisores, p/ a conta bater)
          $mensagens[0].scrollTop += $mensagens[0].scrollHeight - alturaAntes;
        } else {
          botaoDepois(false);
          if (msgs.length) {
            $mensagens.append(fragmentoMensagens(msgs));
            ultimoIdCarregado = msgs[msgs.length - 1].id_mensagem;
          }
          botaoDepois(resp.tem_posteriores);
          atualizarDivisoresDia();
        }
      });
  }

  // Carrega (ou recarrega) a janela de mensagens da conversa aberta. É chamada ao abrir a
  // conversa e ao trocar o filtro Tudo/Textos/Mídias — nesse segundo caso sem tocar no cabeçalho,
  // nos participantes ou no painel flutuante de IA, só o conteúdo da janela muda.
  function carregarMensagensJanela(alvoDeepLink) {
    $mensagens.empty();
    contagensEntidades = {};
    primeiroIdCarregado = ultimoIdCarregado = null;

    var mensagensReq = $.getJSON(urlMensagens(alvoDeepLink ? { centro_id: alvoDeepLink } : {}));
    var contagensReq = $.Deferred();
    EntidadesUI.carregarContagens(idInquerito, tpOrigemMensagem, function (contagens) { contagensReq.resolve(contagens); });
    $.when(mensagensReq, contagensReq).done(function (mensagensResp, contagens) {
      contagensEntidades = contagens || {};
      var resp = mensagensResp[0];
      var msgs = resp.mensagens || [];
      if (!msgs.length) {
        // com filtro ativo, "não há nada nesta janela" quer dizer que a conversa inteira não
        // tem mensagem daquele tipo (a janela sem cursor já é a mais recente DENTRO do filtro)
        $mensagens.append($("<div>").addClass("wa-vazio").text(
          filtroConteudo !== "tudo"
            ? "Nenhuma mensagem com este filtro nesta conversa."
            : "Sem mensagens nesta conversa."));
        return;
      }
      primeiroIdCarregado = msgs[0].id_mensagem;
      ultimoIdCarregado = msgs[msgs.length - 1].id_mensagem;
      botaoAntes(resp.tem_anteriores);
      $mensagens.append(fragmentoMensagens(msgs));
      botaoDepois(resp.tem_posteriores);
      atualizarDivisoresDia();

      var $alvo = alvoDeepLink ? $mensagens.find('.wa-bolha-linha[data-msg-id="' + alvoDeepLink + '"]') : $();
      if ($alvo.length) {
        $alvo.addClass("wa-destaque");
        $alvo[0].scrollIntoView({ block: "center" });
      } else {
        $mensagens.scrollTop($mensagens[0].scrollHeight); // sem deep-link: começa no fim (mais recentes)
      }
    });
  }

  function abrirConversa(idConversa, nome) {
    limparBuscaConversa();
    // o modal de relatório fala da conversa aberta — não pode sobreviver à troca de conversa
    $("#modal-relatorio-conversa").addClass("hidden");

    idConversaAtual = idConversa;
    nomeConversaAtual = nome;
    // avisa o painel flutuante de IA (js/ia_flutuante.js, se presente na página) para que ele
    // restrinja a conversa à mensagem certa quando o investigador trocar de contato
    $(document).trigger("analyser:conversa-aberta", { fonte: fonte, idConversa: idConversa, nome: nome, idExtracao: idExtracao });
    renderCabecalhoConversa(idConversa, nome);
    prepararBarraConversa();
    $("#wa-filtros-conteudo").removeClass("hidden");

    var alvoDeepLink = destacarMsgId;  // se veio de um achado da IA, centra a janela nessa mensagem
    destacarMsgId = null;
    carregarMensagensJanela(alvoDeepLink);
    carregarParticipantes(idConversa);
  }

  // ---------- Porta de entrada para outras telas desta página (sumário flutuante) ----------
  // Sem isto, ir de um item do sumário até a bolha exigiria recarregar a página com ?msg=ID —
  // e recarregar joga fora a rolagem, a conversa aberta e o painel de IA em uso.

  function abrirConversaPorId(idConversa) {
    var $item = $lista.find('.wa-conversa-item[data-id="' + idConversa + '"]');
    if ($item.length) { $item.trigger("click"); return true; }
    var c = conversasPorId[idConversa];
    if (!c) return false;
    $app.addClass("conversa-aberta");
    abrirConversa(c.id_conversa, nomeConversa(c));
    return true;
  }

  function irParaMensagem(idConversa, idMensagem) {
    // conversa já aberta e bolha já na tela: só rola até ela, sem recarregar a janela de
    // mensagens (recarregar volta para o dia mais recente)
    if (String(idConversa) === String(idConversaAtual)) {
      var $alvo = $mensagens.find('.wa-bolha-linha[data-msg-id="' + idMensagem + '"]');
      if ($alvo.length) {
        $mensagens.find(".wa-destaque").removeClass("wa-destaque");
        $alvo.addClass("wa-destaque");
        $alvo[0].scrollIntoView({ block: "center" });
        return true;
      }
    }
    // a bolha alvo pode não casar com o filtro de conteúdo ativo (ex.: o resultado da busca é
    // uma mídia e o filtro está em "Textos"): volta para "Tudo" para o salto não cair numa
    // janela vazia
    if (filtroConteudo !== "tudo") definirFiltroConteudo("tudo");
    // senão, abre a conversa com a janela CENTRADA na mensagem (mesmo caminho do deep-link da IA)
    destacarMsgId = idMensagem;
    if (abrirConversaPorId(idConversa)) return true;
    destacarMsgId = null;
    return false;
  }

  window.WhatsAppUI = {
    irParaMensagem: irParaMensagem,
    abrirConversa: abrirConversaPorId,
    fonte: fonte,
    tpOrigemMensagem: tpOrigemMensagem,
    tpOrigemConversa: tpOrigemConversa,
  };

  // ---------- Participantes + associação a pessoa/local de referência ----------

  // limpa o identificador do WhatsApp (jid "5527...@s.whatsapp.net") pro telefone puro; SMS/msg já vêm limpos
  function telefoneDe(identificador) {
    if (!identificador) return "";
    return String(identificador).replace(/@.*/, "");
  }

  function renderParticipanteChip(p) {
    var $chip = $("<div>").addClass("wa-participante-chip");
    var $ident = $("<div>").addClass("wa-participante-ident").appendTo($chip);
    $("<span>").addClass("wa-participante-nome").text(p.nome || telefoneDe(p.identificador) || "(sem nome)").appendTo($ident);
    // telefone numa 2ª linha só quando há nome real (sem nome, o número já é o rótulo — não repete)
    var tel = telefoneDe(p.identificador);
    if (tel && p.nome && tel !== p.nome) {
      $("<span>").addClass("wa-participante-tel").text("📞 " + tel).appendTo($ident);
    }

    var tituloAssociacao = p.associado_por ? "Associado por " + p.associado_por : "";
    if (p.pessoa_nome) {
      $("<span>").addClass("wa-participante-badge").attr("title", tituloAssociacao).text("🔗 " + p.pessoa_nome).appendTo($chip);
    }
    var local = p.municipio_nome
      ? p.municipio_nome + (p.uf_sigla ? " - " + p.uf_sigla : "")
      : (p.uf_nome || "");
    if (local) {
      $("<span>").addClass("wa-participante-badge").attr("title", tituloAssociacao).text("📍 " + local).appendTo($chip);
    }

    $("<button>")
      .addClass("wa-participante-associar")
      .text(p.pessoa_nome || local ? "Editar" : "Associar")
      .on("click", function () { abrirModalAssociacao(p); })
      .appendTo($chip);

    return $chip;
  }

  // grupo (muitos membros): mostra um cabeçalho recolhível com o total, pra lista não dominar a tela.
  var _LIMITE_INLINE_PARTICIPANTES = 4;

  function carregarParticipantes(idConversa) {
    var $chips = $participantesChips;
    if (!$chips) return;
    $chips.empty();
    $.getJSON("/api/investigacao/conversas/" + idConversa + "/participantes?fonte=" + fonte).done(function (lista) {
      if ($chips !== $participantesChips) return; // outra conversa foi aberta antes da resposta
      if (!lista.length) return;

      if (lista.length <= _LIMITE_INLINE_PARTICIPANTES) {
        lista.forEach(function (p) { $chips.append(renderParticipanteChip(p)); });
        return;
      }

      // grupo: cabeçalho "N membros" que expande/recolhe a lista (recolhida por padrão)
      var $lista = $("<div>").addClass("wa-participantes-lista hidden");
      lista.forEach(function (p) { $lista.append(renderParticipanteChip(p)); });

      var $seta = $("<span>").addClass("wa-participantes-seta").text("▸");
      var $toggle = $("<button>")
        .addClass("wa-participantes-toggle")
        .append($("<span>").text("👥 " + lista.length + " membros"))
        .append($seta)
        .on("click", function () {
          var recolhido = $lista.toggleClass("hidden").hasClass("hidden");
          $seta.text(recolhido ? "▸" : "▾");
        });

      // o toggle fica na linha dos chips; a lista vai direto na barra para quebrar a linha
      // inteira (flex-basis:100%) sem empurrar a busca
      $chips.append($toggle);
      $participantes.append($lista);
    });
  }

  function limparResultados($container) {
    $container.empty().addClass("hidden");
  }

  function atualizarSelecaoPessoa() {
    if (pessoaSelecionada) {
      $assocPessoaSelecionada.removeClass("hidden").empty();
      $("<span>")
        .text(pessoaSelecionada.no_pessoa + (pessoaSelecionada.nu_cpf ? " · CPF " + pessoaSelecionada.nu_cpf : ""))
        .appendTo($assocPessoaSelecionada);
      $("<button>").html("&times;").attr("title", "Remover seleção").on("click", function () {
        pessoaSelecionada = null;
        atualizarSelecaoPessoa();
      }).appendTo($assocPessoaSelecionada);
      $assocPessoaInput.val("").hide();
    } else {
      $assocPessoaSelecionada.addClass("hidden");
      $assocPessoaInput.show();
    }
  }

  function atualizarSelecaoMunicipio() {
    if (municipioSelecionado) {
      $assocMunicipioSelecionado.removeClass("hidden").empty();
      $("<span>")
        .text(municipioSelecionado.no_municipio + (municipioSelecionado.sg_uf ? " - " + municipioSelecionado.sg_uf : ""))
        .appendTo($assocMunicipioSelecionado);
      $("<button>").html("&times;").attr("title", "Remover seleção").on("click", function () {
        municipioSelecionado = null;
        atualizarSelecaoMunicipio();
      }).appendTo($assocMunicipioSelecionado);
      $assocMunicipioInput.val("").hide();
    } else {
      $assocMunicipioSelecionado.addClass("hidden");
      $assocMunicipioInput.show();
    }
  }

  function abrirModalAssociacao(participante) {
    contatoAssociando = participante;
    pessoaSelecionada = participante.id_pessoa_associada
      ? { id_pessoa: participante.id_pessoa_associada, no_pessoa: participante.pessoa_nome, nu_cpf: participante.pessoa_cpf }
      : null;
    municipioSelecionado = participante.id_municipio_associado
      ? { id_municipio: participante.id_municipio_associado, no_municipio: participante.municipio_nome, sg_uf: participante.uf_sigla }
      : null;

    $assocTitulo.text(participante.nome || participante.identificador || "contato");
    $assocPessoaInput.val("").show();
    $assocMunicipioInput.val("").show();
    limparResultados($assocPessoaResultados);
    limparResultados($assocMunicipioResultados);
    atualizarSelecaoPessoa();
    atualizarSelecaoMunicipio();
    $assocUfSelect.val(participante.id_uf_associada || "");
    $assocStatus.text("").removeClass("success error");
    $modalAssoc.removeClass("hidden");
  }

  function fecharModalAssociacao() {
    $modalAssoc.addClass("hidden");
    contatoAssociando = null;
  }

  function renderResultadoPessoa(r) {
    var $item = $("<div>").addClass("wa-assoc-resultado-item");
    $("<div>").text(r.no_pessoa || "(sem nome)").appendTo($item);
    $("<div>").addClass("wa-assoc-resultado-sub").text(r.nu_cpf ? "CPF " + r.nu_cpf : "").appendTo($item);
    $item.on("click", function () {
      pessoaSelecionada = { id_pessoa: r.id_pessoa, no_pessoa: r.no_pessoa, nu_cpf: r.nu_cpf };
      atualizarSelecaoPessoa();
      limparResultados($assocPessoaResultados);
    });
    return $item;
  }

  function renderResultadoMunicipio(r) {
    var $item = $("<div>").addClass("wa-assoc-resultado-item");
    $("<div>").text(r.no_municipio + (r.sg_uf ? " - " + r.sg_uf : "")).appendTo($item);
    $item.on("click", function () {
      municipioSelecionado = { id_municipio: r.id_municipio, no_municipio: r.no_municipio, sg_uf: r.sg_uf };
      atualizarSelecaoMunicipio();
      limparResultados($assocMunicipioResultados);
    });
    return $item;
  }

  var buscarPessoas = debounce(function () {
    var termo = $.trim($assocPessoaInput.val());
    if (termo.length < 2) { limparResultados($assocPessoaResultados); return; }
    $.getJSON("/api/referencia/pessoas?termo=" + encodeURIComponent(termo)).done(function (lista) {
      $assocPessoaResultados.empty();
      if (!lista.length) { limparResultados($assocPessoaResultados); return; }
      lista.forEach(function (r) { $assocPessoaResultados.append(renderResultadoPessoa(r)); });
      $assocPessoaResultados.removeClass("hidden");
    });
  }, 300);

  var buscarMunicipios = debounce(function () {
    var termo = $.trim($assocMunicipioInput.val());
    if (termo.length < 2) { limparResultados($assocMunicipioResultados); return; }
    $.getJSON("/api/referencia/municipios?termo=" + encodeURIComponent(termo)).done(function (lista) {
      $assocMunicipioResultados.empty();
      if (!lista.length) { limparResultados($assocMunicipioResultados); return; }
      lista.forEach(function (r) { $assocMunicipioResultados.append(renderResultadoMunicipio(r)); });
      $assocMunicipioResultados.removeClass("hidden");
    });
  }, 300);

  function salvarAssociacao(limpar) {
    if (!contatoAssociando) return;
    var idUf = limpar ? "" : $assocUfSelect.val();
    var corpo = {
      id_pessoa: limpar ? null : (pessoaSelecionada ? pessoaSelecionada.id_pessoa : null),
      id_municipio: limpar ? null : (municipioSelecionado ? municipioSelecionado.id_municipio : null),
      id_uf: idUf ? Number(idUf) : null
    };
    $assocStatus.text("Salvando…").removeClass("success error");
    $.ajax({
      url: "/api/investigacao/contatos/" + contatoAssociando.id_contato + "/associacao?fonte=" + fonte,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(corpo)
    }).done(function () {
      $assocStatus.text("Associação salva.").addClass("success");
      if (idConversaAtual) carregarParticipantes(idConversaAtual);
      setTimeout(fecharModalAssociacao, 600);
    }).fail(function (xhr) {
      var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar associação.";
      $assocStatus.text(msg).addClass("error");
    });
  }

  function carregarUfs() {
    $.getJSON("/api/referencia/ufs").done(function (lista) {
      lista.forEach(function (uf) {
        $assocUfSelect.append($("<option>").val(uf.id_uf).text(uf.no_uf + " (" + uf.sg_uf + ")"));
      });
    });
  }

  $assocPessoaInput.on("input", buscarPessoas);
  $assocMunicipioInput.on("input", buscarMunicipios);
  $("#assoc-salvar-btn").on("click", function () { salvarAssociacao(false); });
  $("#assoc-limpar-btn").on("click", function () { salvarAssociacao(true); });
  $("#assoc-fechar").on("click", fecharModalAssociacao);
  $modalAssoc.on("click", function (e) { if (e.target === this) fecharModalAssociacao(); });

  carregarUfs();

  // ---------- Relatório da conversa aberta (botão no cabeçalho) ----------
  // Reaproveita o pipeline de relatórios (rotas/relatorios.py): marca a conversa INTEIRA
  // (tpOrigemConversa — nos blocos 'whatsapp'/'chat' do padrão isso imprime o fio completo,
  // não só o que está na tela), gera em segundo plano pelo padrão escolhido (o POST passava do
  // timeout do proxy em casos grandes — daí o job + polling), e baixa o .docx do servidor.
  // A marcação fica no registro (igual ao botão 🗒️ da lateral) para os próximos relatórios.
  var $modalRel = $("#modal-relatorio-conversa");
  var $rcNome = $("#rc-conversa-nome");
  var $rcPadrao = $("#rc-padrao-select");
  var $rcDetalhe = $("#rc-detalhe");
  var $rcGerar = $("#rc-gerar-btn");
  var $rcBaixar = $("#rc-baixar-btn");
  var $rcStatus = $("#rc-status");
  var rcPreparacao = null;      // { dados, inputsManuais, ia } do padrão selecionado (resposta do /preparar)
  var rcUltimaGeracao = null;   // última geração concluída, para o "Baixar novamente"
  var rcTimer = null;           // polling; sobrevive ao fechar do modal: a geração termina e baixa sozinha
  // tipos de bloco que imprimem mensagens/conversas marcadas no .docx
  var RC_BLOCOS_CONVERSA = { whatsapp: 1, chat: 1, marcacoes: 1 };

  function abrirModalRelatorioConversa() {
    if (!idInquerito || !idConversaAtual) return;
    $rcNome.text(nomeConversaAtual || "");
    $rcStatus.text("").removeClass("success error");
    $rcGerar.prop("disabled", true).text("Gerar relatório");
    $rcBaixar.toggleClass("hidden", !rcUltimaGeracao);
    $rcDetalhe.empty();
    rcPreparacao = null;
    $rcPadrao.empty().append($("<option>").val("").text("Carregando padrões…"));
    $modalRel.removeClass("hidden");
    $.getJSON("/api/relatorios/padroes-ativos").done(function (lista) {
      $rcPadrao.empty();
      if (!lista || !lista.length) {
        $rcPadrao.append($("<option>").val("").text("—"));
        $("<p>").addClass("form-status error")
          .text("Nenhum padrão de relatório ativo. Peça ao administrador para cadastrar um na página \"Padrão de relatórios\".")
          .appendTo($rcDetalhe);
        return;
      }
      $rcPadrao.append($("<option>").val("").text("Selecione o padrão…"));
      lista.forEach(function (p) {
        $("<option>").val(p.id_padrao).text(p.no_padrao).appendTo($rcPadrao);
      });
    }).fail(function () {
      $rcPadrao.empty().append($("<option>").val("").text("Erro ao carregar os padrões."));
    });
  }

  function fecharModalRelatorioConversa() {
    $modalRel.addClass("hidden"); // geração em andamento (se houver) continua e baixa sozinha
  }

  function rcPrepararPadrao() {
    var idPadrao = $rcPadrao.val();
    rcPreparacao = null;
    $rcGerar.prop("disabled", true).off("click");
    $rcDetalhe.empty();
    $rcStatus.text("").removeClass("success error");
    if (!idPadrao) return;
    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
              "/relatorios/" + encodeURIComponent(idPadrao) + "/preparar")
      .done(function (dados) {
        if (String($rcPadrao.val()) !== String(idPadrao)) return; // outro padrão escolhido no voo
        var temConversa = (dados.blocos || []).some(function (b) { return RC_BLOCOS_CONVERSA[b.tp_bloco]; });
        if (!temConversa) {
          $("<p>").addClass("form-status error")
            .text("Este padrão não tem bloco de mensagens/conversas marcadas — nenhuma conversa entraria nele. Escolha outro.")
            .appendTo($rcDetalhe);
          return;
        }
        var $campos = $("<div>");
        var inputsManuais = {};
        (dados.tokens_manuais || []).forEach(function (tok) {
          var $in = $("<input>").attr("type", "text");
          inputsManuais[tok] = $in;
          $("<div>").addClass("field")
            .append($("<label>").text("{{" + tok + "}}"))
            .append($in)
            .appendTo($campos);
        });
        var $ia = null;
        if (dados.mostrar_comentario_ia) {
          $ia = $("<input>").attr("type", "checkbox");
          $("<div>").addClass("field").append(
            $("<label>").addClass("wa-relatorio-ia")
              .append($ia)
              .append(document.createTextNode(" Adicionar resumo das conversas por IA"))
          ).appendTo($campos);
        }
        $rcDetalhe.append($campos);
        rcPreparacao = { dados: dados, inputsManuais: inputsManuais, ia: $ia };
        $rcGerar.prop("disabled", false).on("click", rcGerar);
      })
      .fail(function (xhr) {
        if (String($rcPadrao.val()) !== String(idPadrao)) return;
        $("<p>").addClass("form-status error")
          .text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao preparar a geração.")
          .appendTo($rcDetalhe);
      });
  }

  function rcGerar() {
    if (!rcPreparacao || !idConversaAtual) return;
    var valores = {};
    Object.keys(rcPreparacao.inputsManuais).forEach(function (tok) {
      valores[tok] = $.trim(rcPreparacao.inputsManuais[tok].val());
    });
    $rcGerar.prop("disabled", true);
    $rcStatus.html('<span class="spinner" aria-hidden="true"></span> Marcando a conversa…').removeClass("success error");
    // marca a conversa inteira; se já estava marcada o endpoint devolve a existente (ja_existia)
    $.ajax({
      url: "/api/investigacao/marcacoes",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: Number(idInquerito),
        tp_origem: tpOrigemConversa,
        id_origem: Number(idConversaAtual)
      })
    }).done(function (resp) {
      rcDispararGeracao([resp.id_marcacao], valores, rcPreparacao);
    }).fail(function (xhr) {
      $rcGerar.prop("disabled", false);
      $rcStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao marcar a conversa para o relatório.").addClass("error");
    });
  }

  function rcDispararGeracao(idsMarcacoes, valores, prep) {
    $rcStatus.html('<span class="spinner" aria-hidden="true"></span> Gerando…').removeClass("success error");
    $.ajax({
      url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
           "/relatorios/" + prep.dados.padrao.id_padrao + "/gerar",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        valores: valores,
        ids_marcacoes: idsMarcacoes,
        in_comentario_ia: !!(prep.ia && prep.ia.prop("checked")),
        // o botão é da conversa aberta: imprime o fio inteiro, não só bolhas marcadas
        // (sem isto o bloco 'chat' do padrão traria a conversa como item e sairia vazio)
        in_expandir_conversas: true
      })
    }).done(function (resp) {
      rcAcompanharGeracao(resp.id_geracao);
    }).fail(function (xhr) {
      $rcGerar.prop("disabled", false);
      $rcStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao gerar o relatório.").addClass("error");
    });
  }

  function rcAcompanharGeracao(idGeracao) {
    clearTimeout(rcTimer);
    $.getJSON("/api/investigacao/geracoes-relatorio/" + idGeracao)
      .done(function (g) {
        if (g.cd_status === "pendente" || g.cd_status === "processando") {
          var etapa = g.tx_etapa_atual || "Gerando…";
          var contagem = g.qt_total ? " (" + (g.qt_feitos || 0) + "/" + g.qt_total + ")" : "";
          $rcStatus.html('<span class="spinner" aria-hidden="true"></span> ')
            .append(document.createTextNode(etapa + contagem)).removeClass("success error");
          rcTimer = setTimeout(function () { rcAcompanharGeracao(idGeracao); }, 2000);
          return;
        }
        if (g.cd_status === "concluido") {
          rcUltimaGeracao = idGeracao;
          rcBaixarGeracao(idGeracao);
          $rcStatus.text("Relatório gerado (.docx baixado). Se o download não abriu, use \"Baixar novamente\".").addClass("success");
          $rcBaixar.removeClass("hidden");
          $rcGerar.prop("disabled", false).text("Gerar de novo");
          EntidadesUI.carregarMarcacoes(idInquerito, tpOrigemConversa); // acende o 🗒️ da conversa recém-marcada
        } else {
          $rcGerar.prop("disabled", false);
          $rcStatus.text(g.tx_erro || "Erro ao gerar o relatório.").addClass("error");
        }
      })
      .fail(function () {
        $rcGerar.prop("disabled", false);
        $rcStatus.text("Não foi possível acompanhar a geração do relatório.").addClass("error");
      });
  }

  function rcBaixarGeracao(idGeracao) {
    // âncora sintética: o Content-Disposition da rota faz o navegador baixar sem sair da página
    var a = document.createElement("a");
    a.href = "/api/investigacao/geracoes-relatorio/" + idGeracao + "/download";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  $rcPadrao.on("change", rcPrepararPadrao);
  $("#rc-fechar").on("click", fecharModalRelatorioConversa);
  $modalRel.on("click", function (e) { if (e.target === this) fecharModalRelatorioConversa(); });
  $rcBaixar.on("click", function () { if (rcUltimaGeracao) rcBaixarGeracao(rcUltimaGeracao); });

  // Clique nas conversas: um único handler delegado no container, em vez de um por item. Além do
  // custo de registrar dezenas de milhares de closures, o handler antigo fazia
  // $(".wa-conversa-item").removeClass("active") — varria a lista inteira a cada clique.
  $lista.on("click", ".wa-conversa-item", function () {
    var $item = $(this);
    if ($conversaAtiva) $conversaAtiva.removeClass("active");
    $conversaAtiva = $item.addClass("active");
    var c = conversasPorId[$item.attr("data-id")];
    if (c) {
      // no modo painel único (< 860px) troca a lista pela conversa; em tela larga a classe
      // não muda nada, os dois painéis já estão visíveis
      $app.addClass("conversa-aberta");
      abrirConversa(c.id_conversa, nomeConversa(c));
    }
  });

  // Marca a CONVERSA (o contato naquele mensageiro), não as mensagens dela: é a triagem do
  // analista — quais fios ele já leu, quais são relevantes. stopPropagation para o clique não
  // abrir a conversa junto.
  $lista.on("click", ".wa-conversa-marcar", function (e) {
    e.stopPropagation();
    var c = conversasPorId[$(this).attr("data-id-conversa")];
    if (!c) return;
    EntidadesUI.abrirRelatorio({
      idInquerito: idInquerito,
      tpOrigem: tpOrigemConversa,
      idOrigem: c.id_conversa,
      rotulo: "conversa com " + nomeConversa(c),
      somenteRelatorio: true
    });
  });

  // ---------- Lista de conversas: paginada e filtrada no SERVIDOR ----------
  // Um aparelho pode ter dezenas de milhares de conversas; carregar todas de uma vez virava um JSON
  // de megabytes (5 s para abrir, e travava com muita gente simultânea). Agora o servidor devolve
  // páginas de LIMITE_CONVERSAS e aplica busca + aba lá, então a busca e as contagens das abas
  // continuam corretos mesmo com só uma página na tela. Busca, aba e nº de páginas carregadas
  // ficam na URL (EstadoUrl): compartilhar ou recarregar mantém a mesma visão.
  var LIMITE_CONVERSAS = 1000;
  var estadoUrl = window.EstadoUrl ? EstadoUrl.ler(["busca", "tipo", "pagina"]) : {};
  var filtroTipo = estadoUrl.tipo || "todos";
  var filtroBusca = estadoUrl.busca || "";
  var offsetCarregado = 0;       // conversas já na tela
  var paginasAlvo = Math.max(1, parseInt(estadoUrl.pagina, 10) || 1); // recarga reabre até aqui
  var temMais = false;
  var carregando = false;
  var primeiraPaginaPronta = false;   // já pintou rótulo + contagens da 1ª página?
  var marcacoesCarregadas = false;
  var deepLinkTentado = false;

  $("#wa-busca-input").val(filtroBusca);
  $("#wa-filtros .wa-filtro").each(function () {
    $(this).toggleClass("ativo", $(this).attr("data-filtro") === filtroTipo);
  });

  function urlConversasPara(offset) {
    var qs = "?fonte=" + encodeURIComponent(fonte) +
      "&tipo=" + encodeURIComponent(filtroTipo) +
      "&limite=" + LIMITE_CONVERSAS + "&offset=" + offset;
    if (idExtracao) qs += "&extracao=" + encodeURIComponent(idExtracao);
    if (app) qs += "&app=" + encodeURIComponent(app);
    if (filtroBusca) qs += "&busca=" + encodeURIComponent(filtroBusca);
    return "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/conversas" + qs;
  }

  // Busca e aba disparam uma recarga do zero (offset 0): quem digita um nome e clica em "Grupos"
  // espera o nome DENTRO dos grupos — os dois são filtros no servidor e se combinam (E).
  function reiniciarLista() {
    $lista.empty();
    offsetCarregado = 0;
    paginasAlvo = 1;
    temMais = false;
    primeiraPaginaPronta = false;
    carregando = false;
    carregarConversas();
  }

  var buscaDebounce = debounce(function () {
    var novo = $.trim($("#wa-busca-input").val() || "");
    if (novo === filtroBusca) return;
    filtroBusca = novo;
    reiniciarLista();
  }, 300);
  $("#wa-busca-input").on("input", buscaDebounce);

  $("#wa-filtros").on("click", ".wa-filtro", function () {
    var novo = $(this).attr("data-filtro");
    if (novo === filtroTipo) return;
    filtroTipo = novo;
    $(this).addClass("ativo").siblings().removeClass("ativo");
    reiniciarLista();
  });


  // Filtro do conteúdo da conversa aberta (Tudo/Textos/Mídias). Trocar recarrega só a janela
  // de mensagens (com o novo filtro), mantendo cabeçalho, participantes e busca como estão.
  function definirFiltroConteudo(novo) {
    filtroConteudo = novo;
    $("#wa-filtros-conteudo .wa-filtro").each(function () {
      $(this).toggleClass("ativo", $(this).attr("data-conteudo") === novo);
    });
  }

  $("#wa-filtros-conteudo").on("click", ".wa-filtro", function () {
    var novo = $(this).attr("data-conteudo");
    if (novo === filtroConteudo) return;
    definirFiltroConteudo(novo);
    if (idConversaAtual) carregarMensagensJanela(null);
  });

  // Números ao lado de cada aba, vindos do servidor (total por categoria, respeitando a busca).
  // Vêm cheios mesmo com uma página só na tela — sem isso o investigador teria de clicar em cada
  // aba para descobrir, por exemplo, quantos canais há no meio de 2,6 mil conversas.
  function pintarContagens(contagens) {
    $("#wa-filtros .wa-filtro").each(function () {
      var $b = $(this);
      var n = (contagens || {})[$b.attr("data-filtro")];
      if (n === undefined) return;
      $b.find(".wa-filtro-conta").remove();
      $("<span>").addClass("wa-filtro-conta").text("(" + n + ")").appendTo($b);
    });
  }

  function botaoCarregarMais() {
    return $("<div>").attr("id", "wa-rodape-lista").addClass("wa-carregar-mais-wrap").append(
      $("<button>").addClass("wa-carregar-mais").text("Carregar mais conversas")
        .on("click", function () {
          $(this).prop("disabled", true).text("Carregando…");
          paginasAlvo = Math.ceil(offsetCarregado / LIMITE_CONVERSAS) + 1;
          carregarConversas();
        })
    );
  }

  // Acrescenta páginas de LIMITE_CONVERSAS seguidas até ter `paginasAlvo` páginas na tela (ou
  // esgotar). Na 1ª página pinta rótulo e contagens; no fim põe o "carregar mais" (ou o vazio) e
  // grava o estado na URL. É chamado tanto no carregamento inicial/quando muda busca-aba (via
  // reiniciarLista, paginasAlvo=1) quanto no "carregar mais" e na recarga que reaporta páginas.
  function carregarConversas() {
    if (carregando) return;
    carregando = true;
    var alvoItens = paginasAlvo * LIMITE_CONVERSAS;
    (function passo() {
      $.getJSON(urlConversasPara(offsetCarregado)).done(function (resp) {
        var conversas = resp.conversas || [];
        conversas.forEach(function (c) { conversasPorId[c.id_conversa] = c; });
        $("#wa-rodape-lista").remove();
        // monta como string e entrega ao parser nativo de uma vez (ver htmlConversa)
        if (conversas.length) {
          $lista[0].insertAdjacentHTML("beforeend", conversas.map(htmlConversa).join(""));
        }
        offsetCarregado += conversas.length;
        temMais = !!resp.tem_mais;
        if (!primeiraPaginaPronta) {
          primeiraPaginaPronta = true;
          $label.text((resp.total || 0) + " " + rotulos[fonte]);
          pintarContagens(resp.contagens);
        }
        if (offsetCarregado < alvoItens && temMais && conversas.length) { passo(); return; }
        finalizar();
      }).fail(function () {
        $("#wa-rodape-lista").remove();
        $lista.append($("<p>").attr("id", "wa-rodape-lista").addClass("empty-hint")
          .css("padding", "16px").text("Erro ao carregar as conversas."));
        carregando = false;
      });
    })();
  }

  function finalizar() {
    $("#wa-rodape-lista").remove();
    if (temMais) {
      $lista.append(botaoCarregarMais());
    } else if (!$lista.find(".wa-conversa-item").length) {
      $lista.append($("<p>").attr("id", "wa-rodape-lista").addClass("empty-hint")
        .css("padding", "16px").text("Nenhuma conversa neste filtro."));
    }
    var paginas = Math.max(1, Math.ceil(offsetCarregado / LIMITE_CONVERSAS));
    if (window.EstadoUrl) {
      EstadoUrl.salvar({
        busca: filtroBusca || "",
        tipo: filtroTipo !== "todos" ? filtroTipo : "",
        pagina: paginas > 1 ? paginas : "",
      });
    }
    carregando = false;
    if (!marcacoesCarregadas) {
      marcacoesCarregadas = true;
      EntidadesUI.carregarMarcacoes(idInquerito, tpOrigemConversa); // acende o 🗒️ das marcadas
    }
    if (!deepLinkTentado && idMsgDeepLink) { deepLinkTentado = true; resolverDeepLink(); }
  }

  // Veio de um achado da IA (?msg=ID): resolve a conversa da mensagem e a abre. A conversa pode não
  // estar na página carregada — nesse caso busca só ela (?conversa=ID) e a injeta no topo da lista.
  function resolverDeepLink() {
    $.getJSON("/api/investigacao/mensagens/" + encodeURIComponent(idMsgDeepLink) + "/conversa?fonte=" + fonte)
      .done(function (r) {
        destacarMsgId = idMsgDeepLink;
        // o marcador [msg:wpp:ID] que a IA cita não carrega o aparelho — sem ?extracao= na URL,
        // adota o aparelho de onde a mensagem veio (resolvido pelo backend), refletindo na URL para
        // o painel flutuante de IA tratar a aba "Geral" como o mesmo aparelho da conversa.
        if (!idExtracao && r.id_extracao) {
          idExtracao = String(r.id_extracao);
          if (window.EstadoUrl) EstadoUrl.salvar({ extracao: idExtracao });
          $(document).trigger("nav:extracao-resolvida", [idExtracao]);
        }
        var $item = $lista.find('.wa-conversa-item[data-id="' + r.id_conversa + '"]');
        if ($item.length) { $item.trigger("click"); return; }
        $.getJSON(urlConversasPara(0) + "&conversa=" + encodeURIComponent(r.id_conversa))
          .done(function (resp) {
            var alvo = (resp.conversas || [])[0];
            if (!alvo) {
              Dialogo.alerta({ titulo: "Mensagem não encontrada", mensagem: "Não foi possível localizar a mensagem citada." });
              return;
            }
            conversasPorId[alvo.id_conversa] = alvo;
            $lista[0].insertAdjacentHTML("afterbegin", htmlConversa(alvo));
            $lista.find('.wa-conversa-item[data-id="' + alvo.id_conversa + '"]').trigger("click");
          })
          .fail(function () {
            Dialogo.alerta({ titulo: "Mensagem não encontrada", mensagem: "Não foi possível localizar a mensagem citada." });
          });
      })
      .fail(function () {
        Dialogo.alerta({ titulo: "Mensagem não encontrada", mensagem: "Não foi possível localizar a mensagem citada." });
      });
  }

  if (!idInquerito) {
    $lista.append($("<p>").addClass("empty-hint").css("padding", "16px").text("Nenhum inquérito informado na URL."));
  } else {
    carregarConversas();
  }
})();
