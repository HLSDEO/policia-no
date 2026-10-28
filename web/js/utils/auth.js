/* Guarda de autenticação, incluído em toda página protegida (todas exceto login.html/cadastro.html).
 * Confere a sessão via /api/auth/eu:
 *  - sem sessão: manda pro login.
 *  - sessão sem aprovação (autocadastro pendente): cobre a página com um aviso — as chamadas
 *    de API da própria página continuam acontecendo, mas exigem aprovação (403), então não
 *    haveria nada útil pra mostrar mesmo.
 *  - sessão de quem entrou pela API-PF e ainda não criou a senha do PolícIA: abre a caixa
 *    bloqueante de primeiro acesso (js/utils/primeiro_acesso.js), carregada sob demanda para
 *    não custar uma requisição em toda página só por um caso que acontece uma vez por usuário.
 *  - sessão aprovada: completa o rodapé do menu vertical (.sidebar-footer, montado por
 *    js/sidebar.js antes deste script rodar) com nome do usuário, Trocar senha e Sair. */
(function () {
  "use strict";

  function irParaLogin() {
    var voltar = encodeURIComponent(window.location.pathname.split("/").pop() + window.location.search);
    window.location.href = "login.html?next=" + voltar;
  }

  function sair() {
    $.ajax({ url: "/api/auth/logout", method: "POST" }).always(function () {
      window.location.href = "login.html";
    });
  }

  // ---------- Modal de trocar senha (compartilhado — usado pelo botão do rodapé e pela tela de pendente) ----------

  var $modalSenha;

  function montarModalSenha() {
    $modalSenha = $(
      '<div class="modal-overlay hidden" id="modal-trocar-senha">' +
        '<div class="modal" style="width:min(400px, 92vw);">' +
          '<div class="modal-header"><h2>Trocar senha</h2><button type="button" class="modal-close" id="senha-modal-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<div class="field"><label>Senha atual</label><input type="password" id="senha-modal-atual" autocomplete="current-password"></div>' +
            '<div class="field"><label>Senha nova</label><input type="password" id="senha-modal-nova" autocomplete="new-password" minlength="8"></div>' +
            '<div class="form-actions"><button type="button" class="btn btn-primary" id="senha-modal-salvar">Salvar</button><span class="form-status" id="senha-modal-status"></span></div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($modalSenha);

    $("#senha-modal-fechar").on("click", fecharModalSenha);
    $modalSenha.on("click", function (e) { if (e.target === this) fecharModalSenha(); });
    $("#senha-modal-salvar").on("click", salvarSenha);
  }

  function abrirModalSenha() {
    $("#senha-modal-atual").val("");
    $("#senha-modal-nova").val("");
    $("#senha-modal-status").text("").removeClass("success error");
    $modalSenha.removeClass("hidden");
  }

  function fecharModalSenha() {
    $modalSenha.addClass("hidden");
  }

  function salvarSenha() {
    var $status = $("#senha-modal-status");
    var $btn = $("#senha-modal-salvar");
    $status.text("").removeClass("success error");
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/auth/senha",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ senha_atual: $("#senha-modal-atual").val(), senha_nova: $("#senha-modal-nova").val() })
    })
      .done(function () {
        $status.text("Senha trocada.").addClass("success");
        setTimeout(fecharModalSenha, 700);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao trocar a senha.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $btn.prop("disabled", false);
      });
  }

  // ---------- Modal do e-mail de recuperação ----------
  // O e-mail é o que permite recuperar a conta sem depender de um administrador; guardá-lo é
  // opcional e fica no próprio usuário. Trocar exige a senha atual pelo mesmo motivo da troca de
  // senha — é uma credencial de recuperação, não um dado de perfil.

  var $modalEmail;
  var emailAtual = null;

  function montarModalEmail() {
    $modalEmail = $(
      '<div class="modal-overlay hidden" id="modal-meu-email">' +
        '<div class="modal" style="width:min(420px, 92vw);">' +
          '<div class="modal-header"><h2>Meu e-mail</h2><button type="button" class="modal-close" id="email-modal-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<p class="card-hint">Usado só para recuperar a senha, caso você esqueça. Deixe em branco para remover.</p>' +
            '<div class="field"><label>E-mail</label><input type="email" id="email-modal-valor" autocomplete="email"></div>' +
            '<div class="field"><label>Sua senha atual</label><input type="password" id="email-modal-senha" autocomplete="current-password"></div>' +
            '<div class="form-actions"><button type="button" class="btn btn-primary" id="email-modal-salvar">Salvar</button><span class="form-status" id="email-modal-status"></span></div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($modalEmail);

    $("#email-modal-fechar").on("click", fecharModalEmail);
    $modalEmail.on("click", function (e) { if (e.target === this) fecharModalEmail(); });
    $("#email-modal-salvar").on("click", salvarEmail);
  }

  function abrirModalEmail() {
    $("#email-modal-valor").val(emailAtual || "");
    $("#email-modal-senha").val("");
    $("#email-modal-status").text("").removeClass("success error");
    $modalEmail.removeClass("hidden");
  }

  function fecharModalEmail() {
    $modalEmail.addClass("hidden");
  }

  function salvarEmail() {
    var $status = $("#email-modal-status");
    var $btn = $("#email-modal-salvar");
    var email = $.trim($("#email-modal-valor").val());
    $status.text("").removeClass("success error");
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/auth/email",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ senha_atual: $("#email-modal-senha").val(), email: email || null })
    })
      .done(function () {
        emailAtual = email || null;
        $status.text(email ? "E-mail salvo." : "E-mail removido.").addClass("success");
        setTimeout(fecharModalEmail, 700);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o e-mail.";
        $status.text(msg).addClass("error");
      })
      .always(function () {
        $btn.prop("disabled", false);
      });
  }

  // ---------- Modal do webhook de conclusão da IA ----------
  // Cada usuário cadastra a SUA url (Discord/Slack/Teams/n8n/...) e passa a receber um POST de
  // aviso simples quando um turno da IA termina (concluído/erro/cancelado) nos chats que lhe
  // dizem respeito: pergunta que ele enviou ou conversa que ele criou. Vale para todos os chats
  // (ia.html, painel flutuante e IA do caso — mesmo backend). Sem URL, nada é enviado.

  var $modalWebhook;

  function montarModalWebhook() {
    $modalWebhook = $(
      '<div class="modal-overlay hidden" id="modal-meu-webhook">' +
        '<div class="modal" style="width:min(480px, 94vw);">' +
          '<div class="modal-header"><h2>Meu webhook IA</h2><button type="button" class="modal-close" id="webhook-modal-fechar">&times;</button></div>' +
          '<div class="modal-body">' +
            '<p class="card-hint">Avise-me quando a IA terminar de responder — mesmo com a aba fechada. Cadastre a URL do seu webhook (Discord, Slack, Teams, n8n, Make, endpoint próprio) e cada resposta, erro ou cancelamento nos seus chats de IA dispara um POST JSON simples para lá.</p>' +
            '<div class="field"><label>URL do webhook</label><input type="url" id="webhook-modal-url" placeholder="https://..." autocomplete="url"></div>' +
            '<div class="field"><label class="field-check" for="webhook-modal-ativo" style="display:flex;align-items:center;gap:8px;font-weight:400;margin:0;"><input type="checkbox" id="webhook-modal-ativo" style="width:auto;"> Notificações ativas</label></div>' +
            '<div class="form-actions"><button type="button" class="btn btn-primary" id="webhook-modal-salvar">Salvar</button><button type="button" class="btn btn-secondary" id="webhook-modal-testar">Salvar e testar</button><span class="form-status" id="webhook-modal-status"></span></div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    $("body").append($modalWebhook);

    $("#webhook-modal-fechar").on("click", fecharModalWebhook);
    $modalWebhook.on("click", function (e) { if (e.target === this) fecharModalWebhook(); });
    $("#webhook-modal-salvar").on("click", function () { salvarWebhook(false); });
    $("#webhook-modal-testar").on("click", function () { salvarWebhook(true); });
  }

  function abrirModalWebhookIa() {
    var $status = $("#webhook-modal-status");
    $status.text("").removeClass("success error");
    $("#webhook-modal-url").val("");
    $("#webhook-modal-ativo").prop("checked", true);
    $modalWebhook.removeClass("hidden");
    $.getJSON("/api/auth/webhook-ia")
      .done(function (cfg) {
        $("#webhook-modal-url").val(cfg.url || "");
        $("#webhook-modal-ativo").prop("checked", cfg.ativo !== false);
      })
      .fail(function () {
        // sem cadastro ainda ou tabela pendente — mantém o form vazio para cadastrar
      });
  }

  function fecharModalWebhook() {
    $modalWebhook.addClass("hidden");
  }

  function salvarWebhook(testar) {
    var $status = $("#webhook-modal-status");
    var $btns = $("#webhook-modal-salvar, #webhook-modal-testar");
    var url = $.trim($("#webhook-modal-url").val());
    var ativo = $("#webhook-modal-ativo").is(":checked");
    $status.text("").removeClass("success error");
    $btns.prop("disabled", true);
    $.ajax({
      url: "/api/auth/webhook-ia",
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({ url: url, ativo: ativo })
    })
      .done(function () {
        if (!url) {
          $status.text("Webhook removido — você não será mais notificado.").addClass("success");
          $btns.prop("disabled", false);
          return;
        }
        if (!testar) {
          $status.text(ativo ? "Webhook salvo." : "Webhook salvo (pausado).").addClass("success");
          $btns.prop("disabled", false);
          setTimeout(fecharModalWebhook, 700);
          return;
        }
        $status.text("Salvo. Testando…");
        $.ajax({ url: "/api/auth/webhook-ia/teste", method: "POST" })
          .done(function (r) {
            if (r && r.ok) {
              $status.text("Teste entregue (HTTP " + r.http_status + ").").addClass("success");
            } else {
              $status.text("Salvo, mas o teste falhou: " + ((r && (r.erro || ("HTTP " + r.http_status))) || "destino não respondeu.")).addClass("error");
            }
          })
          .fail(function (xhr) {
            var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao testar o webhook.";
            $status.text("Salvo, mas o teste falhou: " + msg).addClass("error");
          })
          .always(function () {
            $btns.prop("disabled", false);
          });
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o webhook.";
        $status.text(msg).addClass("error");
        $btns.prop("disabled", false);
      });
  }

  // ---------- Widget de usuário logado (rodapé do menu vertical) ----------

  var ICON_KEY ='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>';
  var ICON_LOGOUT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
  var ICON_MAIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 7l10 6 10-6"/></svg>';

  function criarBotaoRodape(iconSvg, label, handler) {
    var $btn = $("<button>").addClass("auth-footer-btn").attr("type", "button").attr("title", label).on("click", handler);
    $btn.append(iconSvg);
    $("<span>").text(label).appendTo($btn);
    return $btn;
  }

  function montarWidgetUsuario(usuario) {
    var $footer = $(".sidebar-footer");
    if (!$footer.length) return;

    var $bloco = $("<div>").addClass("auth-usuario");
    var $info = $("<div>").addClass("auth-usuario-info").appendTo($bloco);
    $("<span>").addClass("auth-usuario-nome").text(usuario.no_usuario).attr("title", usuario.no_usuario).appendTo($info);

    var $acoes = $("<div>").addClass("auth-usuario-acoes").appendTo($info);
    criarBotaoRodape(ICON_KEY, "Trocar senha", abrirModalSenha).appendTo($acoes);
    criarBotaoRodape(ICON_MAIL, "Meu e-mail", abrirModalEmail).appendTo($acoes);
    criarBotaoRodape(ICON_LOGOUT, "Sair", sair).appendTo($acoes);

    $footer.append($bloco);
  }

  function montarWidgets(usuario) {
    emailAtual = usuario.no_email || null;
    montarModalSenha();
    montarModalEmail();
    montarModalWebhook();
    montarWidgetUsuario(usuario);
  }

  function mostrarPendente() {
    var $overlay = $("<div>").addClass("auth-pendente-overlay");
    var $caixa = $("<div>").addClass("auth-pendente-caixa").appendTo($overlay);
    $("<h2>").text("Cadastro pendente de aprovação").appendTo($caixa);
    $("<p>").text("Sua conta foi criada, mas ainda precisa ser aprovada por um administrador antes de você acessar o sistema.").appendTo($caixa);
    var $acoes = $("<div>").addClass("auth-pendente-acoes").appendTo($caixa);
    $("<button>").addClass("btn btn-secondary").attr("type", "button").text("Trocar minha senha").on("click", abrirModalSenha).appendTo($acoes);
    $("<button>").addClass("btn btn-primary").attr("type", "button").text("Sair").on("click", sair).appendTo($acoes);
    $("body").append($overlay);
  }

  function sincronizarTema(usuario) {
    // localStorage (lido no <script> inline do <head>, antes do jQuery carregar) evita o
    // flash de tema errado; aqui só corrige se o banco (fonte de verdade) discordar — ex.:
    // usuário trocou o tema em outro navegador/dispositivo. Quem aplica é js/utils/theme.js,
    // que também acerta o tema marcado no dropdown "Temas" do menu.
    if (!usuario.tp_tema || !window.Tema) return;
    window.Tema.definirDoServidor(usuario.tp_tema);
  }

  // Primeiro acesso pendente: quem entrou pela API-PF e fechou o navegador antes de criar a
  // senha daqui cai nesta caixa em qualquer página interna. O script é buscado só agora — em
  // 99% das sessões ele nunca é necessário.
  function exigirPrimeiroAcesso(aoConcluir) {
    if (window.PrimeiroAcesso) {
      window.PrimeiroAcesso.abrir(aoConcluir);
      return;
    }
    $.getScript("js/utils/primeiro_acesso.js")
      .done(function () { window.PrimeiroAcesso.abrir(aoConcluir); })
      .fail(function () {
        // Sem a caixa não há como criar a senha; melhor mandar de volta ao login do que deixar a
        // pessoa navegando achando que o cadastro está completo.
        if (window.Toast) window.Toast.erro("Não foi possível abrir o primeiro acesso. Entre de novo.");
      });
  }

  $.ajax({ url: "/api/auth/eu", method: "GET" })
    .done(function (usuario) {
      sincronizarTema(usuario);
      montarWidgets(usuario);
      if (usuario.senha_local_pendente) {
        exigirPrimeiroAcesso(function () {
          if (!usuario.in_aprovado) mostrarPendente();
        });
      } else if (!usuario.in_aprovado) {
        mostrarPendente();
      }
      $(document).trigger("analyser:usuario", [usuario]);
    })
    .fail(irParaLogin);

  window.AuthUI = {
    sair: sair,
    abrirModalSenha: function () { abrirModalSenha(); },
    abrirModalEmail: function () { abrirModalEmail(); },
    abrirModalWebhookIa: function () { abrirModalWebhookIa(); }
  };
})();
