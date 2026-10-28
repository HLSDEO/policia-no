/* Notificações "toast" — window.Toast. Feedback rápido e não bloqueante (import concluído,
 * item salvo, falha de rede) que não existia no projeto: sucesso era silencioso e erro
 * só tinha o modal bloqueante de Dialogo.alerta (bom para o que exige leitura antes de seguir,
 * ruim para "deu certo" ou "a rede caiu"). Injeta a própria pilha no <body>, sem depender de
 * HTML na página.
 *
 *   Toast.sucesso("Importação concluída")
 *   Toast.erro("Não foi possível salvar")
 *   Toast.aviso("Sessão expira em 5 minutos")
 *   Toast.info("3 novos registros")
 *   Toast.mostrar({ tipo: "sucesso", mensagem: "...", duracao: 6000, acao: { rotulo: "Desfazer", aoClicar: fn } })
 *
 * Todas retornam um handle { fechar() }. Erro/aviso ficam até serem fechados (clique ou Esc);
 * sucesso/info somem sozinhos (~5s), pausando enquanto o mouse ou o foco estiver em cima. */
(function () {
  "use strict";

  var DURACAO_PADRAO = { sucesso: 5000, info: 5000, aviso: 0, erro: 0 };
  var ROTULO_TIPO = { sucesso: "Sucesso", erro: "Erro", aviso: "Aviso", info: "Aviso" };

  var ICONES = {
    sucesso: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    erro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    aviso: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
  };

  var $pilha = null;

  function garantirPilha() {
    if ($pilha) return;
    $pilha = $('<div class="toast-pilha"></div>');
    $("body").append($pilha);
  }

  function mostrar(opts) {
    opts = typeof opts === "string" ? { mensagem: opts } : (opts || {});
    var tipo = opts.tipo && ICONES[opts.tipo] ? opts.tipo : "info";
    var duracao = opts.duracao != null ? opts.duracao : DURACAO_PADRAO[tipo];

    garantirPilha();
    // sucesso/info não devem interromper o leitor de tela (status/polite); erro/aviso devem
    // (alert/assertive) — cada toast carrega sua própria live region, não a pilha inteira,
    // pra não empilhar dois contêineres fixos na mesma posição da tela.
    var assertivo = tipo === "erro" || tipo === "aviso";
    var $toast = $('<div class="toast toast-' + tipo + '"></div>')
      .attr("role", assertivo ? "alert" : "status")
      .attr("aria-live", assertivo ? "assertive" : "polite")
      .attr("aria-atomic", "true");
    $("<span>").addClass("toast-icone").html(ICONES[tipo]).appendTo($toast);
    var $corpo = $("<div>").addClass("toast-corpo").appendTo($toast);
    $("<span>").addClass("sr-only").text(ROTULO_TIPO[tipo] + ": ").appendTo($corpo);
    $("<span>").addClass("toast-mensagem").text(opts.mensagem || "").appendTo($corpo);

    if (opts.acao && opts.acao.rotulo) {
      $("<button>").addClass("toast-acao").attr("type", "button").text(opts.acao.rotulo)
        .on("click", function () {
          if (opts.acao.aoClicar) opts.acao.aoClicar();
          fechar();
        })
        .appendTo($toast);
    }

    var $fechar = $("<button>").addClass("toast-fechar").attr("type", "button")
      .attr("aria-label", "Fechar notificação").html("&times;")
      .on("click", function () { fechar(); })
      .appendTo($toast);

    var timer = null;

    function agendar() {
      if (!duracao) return;
      timer = setTimeout(fechar, duracao);
    }

    function pausar() {
      clearTimeout(timer);
    }

    var fechado = false;
    function fechar() {
      if (fechado) return;
      fechado = true;
      clearTimeout(timer);
      $toast.addClass("toast-saindo");
      setTimeout(function () { $toast.remove(); }, 200);
    }

    $toast.on("mouseenter focusin", pausar);
    $toast.on("mouseleave focusout", agendar);

    $pilha.append($toast);
    agendar();

    return { fechar: fechar, elemento: $toast };
  }

  function sucesso(msg, opts) { return mostrar($.extend({ tipo: "sucesso", mensagem: msg }, opts)); }
  function erro(msg, opts) { return mostrar($.extend({ tipo: "erro", mensagem: msg }, opts)); }
  function aviso(msg, opts) { return mostrar($.extend({ tipo: "aviso", mensagem: msg }, opts)); }
  function info(msg, opts) { return mostrar($.extend({ tipo: "info", mensagem: msg }, opts)); }

  // Esc fecha só o toast mais recente (o de cima da pilha visual = último inserido).
  $(document).on("keydown", function (e) {
    if (e.key !== "Escape") return;
    var $ultimo = $(".toast").last();
    if ($ultimo.length) $ultimo.find(".toast-fechar").trigger("click");
  });

  window.Toast = { mostrar: mostrar, sucesso: sucesso, erro: erro, aviso: aviso, info: info };
})();
