/* Progresso detalhado do turno de IA. Só produz interface quando o tema ativo é Orbit. */
(function () {
  "use strict";

  var inicios = new WeakMap();

  function orbitAtivo() {
    return document.documentElement.getAttribute("data-theme") === "orbit";
  }

  function requisicaoDeMensagem(settings) {
    return settings && String(settings.type || settings.method || "GET").toUpperCase() === "POST" &&
      /\/api\/investigacao\/ia\/conversas\/\d+\/mensagens(?:\?|$)/.test(String(settings.url || ""));
  }

  function numero(valor) { return Number(valor || 0); }

  function duracao(ms) {
    var segundos = Math.max(0, Math.floor(ms / 1000));
    var horas = Math.floor(segundos / 3600);
    var minutos = Math.floor((segundos % 3600) / 60);
    var resto = segundos % 60;
    if (horas) return horas + " h " + minutos + " min " + resto + " s";
    if (minutos) return minutos + " min " + resto + " s";
    return resto + " s";
  }

  function trilha(valor) {
    if (typeof valor === "string") {
      try { valor = JSON.parse(valor); } catch (e) { valor = []; }
    }
    return Array.isArray(valor) ? valor : [];
  }

  function nomeConsulta(nome) {
    return String(nome || "consulta").replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }

  function argumentos(valor) {
    if (!valor || typeof valor !== "object" || !Object.keys(valor).length) return "";
    try { return JSON.stringify(valor); } catch (e) { return ""; }
  }

  function criarPainel(inicio) {
    $(".orbit-ia-live").remove();
    var $painel = $("<section>").addClass("orbit-ia-live").attr({
      role: "status", "aria-live": "polite", "aria-label": "Progresso da resposta da IA"
    });
    var $tempo = $("<div>").addClass("orbit-ia-live-tempo").appendTo($painel);
    var $card = $("<div>").addClass("orbit-ia-live-card").appendTo($painel);
    var $atual = $("<div>").addClass("orbit-ia-live-atual").text("Preparando a análise…").appendTo($card);
    var $detalhes = $("<details>").addClass("orbit-ia-live-details").prop("open", true).appendTo($card);
    var $resumo = $("<summary>").text("Acompanhamento da IA").appendTo($detalhes);
    var $corpo = $("<div>").addClass("orbit-ia-live-corpo").appendTo($detalhes);
    $("#chat-mensagens").append($painel);

    var timer = window.setInterval(function () {
      $tempo.text("Processando há " + duracao(Date.now() - inicio));
    }, 1000);
    $tempo.text("Processando há 0 s");
    return { $painel: $painel, $tempo: $tempo, $atual: $atual, $detalhes: $detalhes, $resumo: $resumo, $corpo: $corpo, timer: timer };
  }

  function desenhar(painel, estado) {
    if (!painel.$painel[0] || !painel.$painel[0].isConnected) return;
    var rodadas = trilha(estado.tx_progresso);
    var consultas = rodadas.reduce(function (total, rodada) {
      return total + (Array.isArray(rodada.ferramentas) ? rodada.ferramentas.length : 0);
    }, 0);
    var partes = [];
    if (rodadas.length) partes.push(rodadas.length + (rodadas.length === 1 ? " etapa" : " etapas"));
    if (consultas) partes.push(consultas + (consultas === 1 ? " consulta" : " consultas"));
    if (numero(estado.qt_lotes_total) > 1) partes.push(numero(estado.qt_lotes_concluidos) + "/" + numero(estado.qt_lotes_total) + " partes");
    painel.$resumo.text("Raciocínio em andamento" + (partes.length ? " · " + partes.join(", ") : ""));
    painel.$atual.text(estado.tx_etapa_atual || (rodadas.length ? "Avaliando os resultados obtidos…" : "Preparando contexto e fontes…"));
    painel.$corpo.empty();
    if (!rodadas.length) {
      $("<div>").addClass("orbit-ia-live-vazio").text("As etapas e consultas aparecerão aqui à medida que forem concluídas.").appendTo(painel.$corpo);
      return;
    }
    rodadas.forEach(function (rodada, indice) {
      var $etapa = $("<section>").addClass("orbit-ia-live-etapa").appendTo(painel.$corpo);
      $("<div>").addClass("orbit-ia-live-etapa-titulo").text("Etapa " + (rodada.rodada || indice + 1)).appendTo($etapa);
      if (rodada.raciocinio) $("<div>").addClass("orbit-ia-live-raciocinio").text(rodada.raciocinio).appendTo($etapa);
      var ferramentas = Array.isArray(rodada.ferramentas) ? rodada.ferramentas : [];
      if (ferramentas.length) {
        var $lista = $("<ul>").addClass("orbit-ia-live-consultas").appendTo($etapa);
        ferramentas.forEach(function (ferramenta) {
          var $item = $("<li>").addClass("orbit-ia-live-consulta").appendTo($lista);
          $("<strong>").text(nomeConsulta(ferramenta.ferramenta)).appendTo($item);
          var args = argumentos(ferramenta.argumentos);
          if (args) $("<code>").text(args).appendTo($item);
        });
      } else if (!rodada.raciocinio) {
        $("<div>").addClass("orbit-ia-live-vazio").text("A IA está avaliando esta etapa.").appendTo($etapa);
      }
    });
    painel.$corpo.scrollTop(painel.$corpo[0].scrollHeight);
    var mensagens = $("#chat-mensagens")[0];
    if (mensagens) mensagens.scrollTop = mensagens.scrollHeight;
  }

  function aguardarResposta(painel, idMensagem) {
    painel.$atual.text("Organizando a resposta final…");
    var encerrar = function () {
      window.clearInterval(painel.timer);
      painel.$painel.remove();
    };
    var seletor = idMensagem ? '.chat-linha[data-id-mensagem="' + idMensagem + '"]' : ".chat-linha.assistant";
    if ($(seletor).length) return encerrar();
    var observador = new MutationObserver(function () {
      if ($(seletor).length) { observador.disconnect(); encerrar(); }
    });
    observador.observe(document.getElementById("chat-mensagens"), { childList: true, subtree: true });
    window.setTimeout(function () { observador.disconnect(); encerrar(); }, 15000);
  }

  function acompanhar(idProcessamento, inicio) {
    var base = "/api/investigacao/ia/processamentos/" + encodeURIComponent(idProcessamento);
    var painel = criarPainel(inicio);
    var fonte = null;
    var polling = null;
    var encerrado = false;

    function pararTransporte() {
      if (fonte) { fonte.close(); fonte = null; }
      if (polling) { window.clearInterval(polling); polling = null; }
    }

    function aplicar(estado) {
      if (encerrado || !estado) return;
      desenhar(painel, estado);
      if (estado.cd_status === "concluido") {
        encerrado = true; pararTransporte(); aguardarResposta(painel, estado.id_mensagem_resposta);
      } else if (estado.cd_status === "erro" || estado.cd_status === "cancelado") {
        encerrado = true; pararTransporte(); window.clearInterval(painel.timer); painel.$painel.remove();
      }
    }

    function iniciarPolling() {
      if (encerrado || polling) return;
      polling = window.setInterval(function () { $.getJSON(base).done(aplicar); }, 1800);
      $.getJSON(base).done(aplicar);
    }

    if (window.EventSource) {
      try {
        fonte = new EventSource(base + "/eventos");
        fonte.addEventListener("estado", function (evento) {
          var estado;
          try { estado = JSON.parse(evento.data); } catch (e) { return; }
          aplicar(estado);
        });
        fonte.addEventListener("fim", function () { pararTransporte(); iniciarPolling(); });
        fonte.onerror = function () {
          if (fonte && fonte.readyState === EventSource.CLOSED) { pararTransporte(); iniciarPolling(); }
        };
      } catch (e) { iniciarPolling(); }
    } else {
      iniciarPolling();
    }
  }

  $(document).ajaxSend(function (_evento, _xhr, settings) {
    if (requisicaoDeMensagem(settings)) inicios.set(settings, Date.now());
  });

  $(document).ajaxSuccess(function (_evento, xhr, settings, resposta) {
    if (!orbitAtivo() || !requisicaoDeMensagem(settings)) return;
    resposta = resposta || xhr.responseJSON;
    if (resposta && resposta.modo === "async" && resposta.id_processamento) {
      acompanhar(resposta.id_processamento, inicios.get(settings) || Date.now());
    }
  });
})();
