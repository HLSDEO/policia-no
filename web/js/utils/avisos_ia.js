/* Avisos in-app de conclusão da IA — window.AvisosIA. Carregado em TODA página protegida.
 *
 * O problema que resolve: você pergunta no chat de IA, sai para outra tela do sistema e a
 * resposta fica pronta sem ninguém avisar. O webhook ("Meu webhook IA") só entrega FORA daqui
 * (Discord/Slack/n8n) e exige cadastro — não serve para o aviso dentro do próprio sistema.
 *
 * Como funciona: polling curto em GET /api/auth/avisos-ia, que devolve os turnos de IA
 * terminados (concluído/erro/cancelado) em que o usuário logado é o solicitante ou o criador da
 * conversa — nos dois chats (inquérito e caso). Cada aviso vira:
 *   - um toast com ação "Abrir" (leva à conversa exata);
 *   - uma notificação do navegador, se o usuário tiver autorizado (útil com a aba em segundo
 *     plano ou minimizada). A permissão só é pedida no gesto de enviar a pergunta ou pelo item
 *     "Avisos da IA" do menu do usuário — nunca de surpresa ao abrir uma página qualquer.
 *
 * A janela de tempo vem do servidor: a resposta traz "agora" (relógio do banco) e a chamada
 * seguinte devolve esse valor como "desde". Assim relógio do navegador atrasado/adiantado não
 * some com aviso nem repete. A lista dos já mostrados fica em localStorage (por usuário), para
 * o F5 não repetir o mesmo toast.
 *
 * A página do chat que está com a conversa aberta se registra por AvisosIA.silenciar(...): ela
 * já mostra a resposta na tela, então o toast seria ruído — mas se a aba estiver em segundo
 * plano a notificação do navegador ainda sai.
 */
(function () {
  "use strict";

  var URL_AVISOS = "/api/auth/avisos-ia";
  var INTERVALO_VISIVEL = 15000;
  var INTERVALO_OCULTO = 30000;
  var MAX_VISTOS = 80;
  var MAX_TOASTS_POR_RODADA = 3;

  var idUsuario = null;
  var chaveEstado = null;
  var estado = { desde: null, vistos: [] };
  var timer = null;
  var emVoo = false;
  var ligado = false;
  // conversas que a página atual já está exibindo — { "chat_inquerito:12": true }
  var silenciadas = {};
  // funções que respondem "chat_inquerito:12" ou null (painéis flutuantes, que abrem e fecham)
  var provedores = [];

  // ---------- persistência (por usuário, tolerante a localStorage bloqueado) ----------

  function carregarEstado() {
    try {
      var bruto = localStorage.getItem(chaveEstado);
      if (!bruto) return;
      var lido = JSON.parse(bruto);
      if (lido && typeof lido === "object") {
        estado.desde = lido.desde || null;
        estado.vistos = Array.isArray(lido.vistos) ? lido.vistos.slice(-MAX_VISTOS) : [];
      }
    } catch (e) { /* sem persistência: no máximo repete um aviso depois do F5 */ }
  }

  function gravarEstado() {
    try {
      localStorage.setItem(chaveEstado, JSON.stringify({
        desde: estado.desde,
        vistos: estado.vistos.slice(-MAX_VISTOS)
      }));
    } catch (e) { /* idem */ }
  }

  function chaveAviso(a) {
    return a.origem + ":" + a.id_processamento + ":" + a.status;
  }

  function jaMostrado(a) {
    return estado.vistos.indexOf(chaveAviso(a)) !== -1;
  }

  function marcarMostrado(a) {
    estado.vistos.push(chaveAviso(a));
    if (estado.vistos.length > MAX_VISTOS) estado.vistos = estado.vistos.slice(-MAX_VISTOS);
  }

  // ---------- preferência de notificação do navegador ----------

  function chavePref() {
    return "analyser:avisos-ia-navegador:" + idUsuario;
  }

  function notificacaoLigada() {
    if (!("Notification" in window) || Notification.permission !== "granted") return false;
    try {
      return localStorage.getItem(chavePref()) !== "0";
    } catch (e) {
      return true;
    }
  }

  function definirNotificacao(ligar) {
    try { localStorage.setItem(chavePref(), ligar ? "1" : "0"); } catch (e) {}
  }

  /* Pede a permissão do navegador. Só faz sentido dentro de um gesto do usuário (enviar a
   * pergunta, clicar no item do menu) — por isso não é chamada na carga da página. */
  function pedirPermissao(aoDecidir) {
    if (!("Notification" in window)) {
      if (aoDecidir) aoDecidir("indisponivel");
      return;
    }
    if (Notification.permission === "granted") {
      definirNotificacao(true);
      if (aoDecidir) aoDecidir("granted");
      return;
    }
    if (Notification.permission === "denied") {
      if (aoDecidir) aoDecidir("denied");
      return;
    }
    try {
      var p = Notification.requestPermission(function (perm) {
        if (perm === "granted") definirNotificacao(true);
        if (aoDecidir) aoDecidir(perm);
      });
      // navegadores modernos devolvem Promise em vez de usar o callback
      if (p && typeof p.then === "function") {
        p.then(function (perm) {
          if (perm === "granted") definirNotificacao(true);
          if (aoDecidir) aoDecidir(perm);
        });
      }
    } catch (e) {
      if (aoDecidir) aoDecidir("erro");
    }
  }

  // ---------- exibição ----------

  function textoAviso(a) {
    if (a.origem === "planilha_ia") {
      // consolidação de planilha pedida no chat: o job rodou minutos e acabou de terminar
      if (a.status === "erro") return "A planilha consolidada da IA falhou" + (a.erro ? ": " + a.erro : ".");
      if (a.status === "cancelado") return "Consolidação de planilha cancelada.";
      return "A planilha consolidada da IA está pronta" + (a.titulo ? " (" + a.titulo + ")" : "")
        + " — abra a conversa para baixar.";
    }
    var alvo = a.titulo || "conversa de IA";
    if (a.status === "erro") return "A IA falhou em \"" + alvo + "\"" + (a.erro ? ": " + a.erro : ".");
    if (a.status === "cancelado") return "Resposta cancelada em \"" + alvo + "\".";
    return "A IA terminou de responder em \"" + alvo + "\".";
  }

  function abrir(a) {
    window.location.href = a.link;
  }

  function mostrarNotificacaoNavegador(a) {
    if (!notificacaoLigada()) return;
    try {
      var n = new Notification("Resposta da IA", {
        body: textoAviso(a),
        // a tag colapsa avisos repetidos da mesma conversa no lugar de empilhar
        tag: "avisos-ia:" + a.origem + ":" + a.id_conversa_ia,
        icon: "img/logo-mark.png"
      });
      n.onclick = function () {
        try { window.focus(); } catch (e) {}
        abrir(a);
        n.close();
      };
    } catch (e) { /* navegador bloqueou a criação — o toast já cobre o caso */ }
  }

  function mostrarToast(a) {
    if (!window.Toast) return;
    var mensagem = textoAviso(a);
    var opts = {
      duracao: a.status === "concluido" ? 12000 : 0,
      acao: { rotulo: "Abrir", aoClicar: function () { abrir(a); } }
    };
    if (a.status === "erro") window.Toast.erro(mensagem, opts);
    else if (a.status === "cancelado") window.Toast.aviso(mensagem, opts);
    else window.Toast.sucesso(mensagem, opts);
  }

  function estaSilenciada(a) {
    var chave = a.origem + ":" + a.id_conversa_ia;
    if (silenciadas[chave]) return true;
    // painéis flutuantes: em vez de avisar a cada troca de aba/abre-fecha, eles registram uma
    // função que diz, na hora, qual conversa está à vista (ou nada, se o painel está fechado)
    for (var i = 0; i < provedores.length; i++) {
      try {
        if (provedores[i]() === chave) return true;
      } catch (e) { /* provedor quebrado não pode calar o aviso */ }
    }
    return false;
  }

  function processar(avisos) {
    var mostrados = 0;
    for (var i = 0; i < avisos.length; i++) {
      var a = avisos[i];
      if (jaMostrado(a)) continue;
      marcarMostrado(a);

      // a própria tela do chat já está exibindo essa conversa e o usuário está olhando:
      // o toast seria ruído. Em segundo plano, a notificação do navegador ainda vale.
      if (estaSilenciada(a) && !document.hidden) continue;

      if (document.hidden) {
        mostrarNotificacaoNavegador(a);
      } else if (mostrados < MAX_TOASTS_POR_RODADA) {
        mostrarToast(a);
        mostrados++;
      }
    }
  }

  // ---------- polling ----------

  function bater() {
    if (emVoo || !ligado) return;
    emVoo = true;
    $.ajax({
      url: URL_AVISOS + (estado.desde ? "?desde=" + encodeURIComponent(estado.desde) : ""),
      method: "GET",
      cache: false
    })
      .done(function (r) {
        if (!r) return;
        if (r.agora) estado.desde = r.agora;
        if (r.avisos && r.avisos.length) processar(r.avisos);
        gravarEstado();
      })
      .fail(function (xhr) {
        // sessão caiu: parar de bater (o guarda do auth.js já manda para o login)
        if (xhr && xhr.status === 401) parar();
      })
      .always(function () { emVoo = false; });
  }

  function reagendar() {
    clearInterval(timer);
    timer = setInterval(bater, document.hidden ? INTERVALO_OCULTO : INTERVALO_VISIVEL);
  }

  function iniciar(usuario) {
    if (ligado || !usuario || !usuario.id_usuario || usuario.in_aprovado === false) return;
    idUsuario = usuario.id_usuario;
    chaveEstado = "analyser:avisos-ia:" + idUsuario;
    carregarEstado();
    ligado = true;
    reagendar();
    bater();
  }

  function parar() {
    ligado = false;
    clearInterval(timer);
  }

  document.addEventListener("visibilitychange", function () {
    if (!ligado) return;
    reagendar();
    // voltando para a aba, confere na hora em vez de esperar o próximo tique
    if (!document.hidden) bater();
  });

  $(document).on("analyser:usuario", function (e, usuario) { iniciar(usuario); });

  window.AvisosIA = {
    pedirPermissao: pedirPermissao,
    notificacaoLigada: notificacaoLigada,
    definirNotificacao: definirNotificacao,
    permissao: function () { return ("Notification" in window) ? Notification.permission : "indisponivel"; },
    /* A tela do chat avisa qual conversa está aberta: AvisosIA.silenciar("chat_inquerito", 12).
     * A troca de conversa limpa só as chaves daquela origem — a mesma tela pode silenciar mais de
     * uma origem para a mesma conversa (turno do chat + job de planilha disparado nela). */
    silenciar: function (origem, idConversa) {
      Object.keys(silenciadas).forEach(function (k) {
        if (k.indexOf(origem + ":") === 0) delete silenciadas[k];
      });
      if (idConversa) silenciadas[origem + ":" + idConversa] = true;
    },
    /* Painéis flutuantes: registram uma função consultada na hora do aviso, que devolve
     * "chat_inquerito:12" quando o painel está aberto naquela conversa, ou null/"" quando não. */
    silenciarSe: function (fn) {
      if (typeof fn === "function") provedores.push(fn);
    },
    verificarAgora: bater
  };
})();
