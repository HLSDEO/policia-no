/* Camada fina sobre $.ajax — window.Api — mais uma rede de segurança global para chamadas que
 * não tratam erro.
 *
 * Api.get(url, params) / Api.post(url, dados) / Api.enviar(opts) devolvem o mesmo Deferred do
 * jQuery que $.getJSON/$.ajax devolvem, então .done()/.fail()/.then() continuam funcionando —
 * a ideia é poder trocar $.getJSON(...) por Api.get(...) sem mudar o resto do código, ganhando
 * de graça a extração padronizada da mensagem de erro (xhr.responseJSON.detail, hoje repetida
 * em dezenas de arquivos) através de Api.mensagemErro(xhr).
 *
 * A rede de segurança em si é o listener global $(document).ajaxError(): das ~290 chamadas de
 * API espalhadas pelo projeto, boa parte não tem .fail(), então uma queda de rede ou erro 500
 * hoje deixa a tela sem nenhum feedback. O listener cobre isso sem exigir editar cada chamada:
 *
 *   - status 0 (rede caiu / servidor fora do ar)   -> toast "Sem conexão com o servidor"
 *   - status >= 500 (erro do servidor)             -> toast "Erro no servidor..."
 *   - status 401 (sessão expirou)                  -> toast + redireciona ao login
 *   - demais 4xx (404, 409, 413, 422...)           -> toast com a mensagem da API
 *
 * O 403 é a ÚNICA exceção que continua muda: é o caso esperado em servidor.html e em toda rota
 * de admin para quem não é admin, e a própria página já mostra o aviso de "sem permissão" —
 * um toast ali seria barulho em cima de tela que já explicou.
 *
 * Os outros 4xx eram mudos junto com ele, e isso escondia erro de verdade: 404 de extração
 * apagada, 409 de operação já em andamento, 422 de parâmetro inválido: o analista clicava e a
 * tela ficava exatamente como estava, sem erro e sem log visível. abort (usuário cancelou/nova
 * busca) segue ignorado. Chamadas que já tratam o próprio erro (com .fail()) podem pedir
 * `erroSilencioso: true` pra pular o toast global e não duplicar a mensagem. */
(function () {
  "use strict";

  /* O `detail` do FastAPI nem sempre é string. Num 422 (validação do corpo/query) ele vem como
   * lista de objetos {loc, msg, type}, e devolver isso cru fazia o analista ler "[object Object]"
   * — Dialogo.alerta usa textContent, então o array vira literalmente essa palavra. */
  function textoDetalhe(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      var msgs = detail
        .map(function (item) {
          if (typeof item === "string") return item;
          return item && item.msg ? String(item.msg) : "";
        })
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    } else if (detail && typeof detail === "object" && detail.msg) {
      return String(detail.msg);
    }
    return "";
  }

  function mensagemErro(xhr, padrao) {
    if (xhr && xhr.responseJSON && xhr.responseJSON.detail) {
      var texto = textoDetalhe(xhr.responseJSON.detail);
      if (texto) return texto;
    }
    if (xhr && xhr.status === 0) return "Sem conexão com o servidor.";
    return padrao || "Ocorreu um erro. Tente novamente.";
  }

  function enviar(opts) {
    opts = opts || {};
    var config = $.extend({}, opts);
    delete config.erroSilencioso;
    var jqXHR = $.ajax(config);
    if (opts.erroSilencioso) jqXHR.__erroSilencioso = true;
    return jqXHR;
  }

  function get(url, params) {
    return enviar({ url: url, method: "GET", data: params });
  }

  function post(url, dados) {
    return enviar({
      url: url,
      method: "POST",
      contentType: "application/json",
      data: dados != null ? JSON.stringify(dados) : undefined
    });
  }

  window.Api = { get: get, post: post, enviar: enviar, mensagemErro: mensagemErro };

  // ---------- rede de segurança global ----------

  function irParaLogin() {
    var voltar = encodeURIComponent(window.location.pathname.split("/").pop() + window.location.search);
    window.location.href = "login.html?next=" + voltar;
  }

  var avisoSessaoExpirada = false;

  /* Marca as chamadas que tratam o próprio erro, para o toast global de 4xx não duplicar a
   * mensagem que a página já mostrou.
   *
   * Sem isto não dava para ligar o aviso de 4xx: são 272 .fail() no projeto contra 4 usos de
   * `erroSilencioso`, e um 404/409/422 passaria a render dois avisos na maioria das telas. O
   * jQuery dispara os callbacks locais ANTES do ajaxError global, mas o que interessa aqui nem é
   * a ordem: basta saber, no momento do global, se alguém chegou a se inscrever no rejeitado.
   *
   * O prefilter roda com o jqXHR já criado, então dá para instrumentar os quatro caminhos de
   * inscrição em falha antes que a página use qualquer um deles. `done` fica de fora de
   * propósito: quem só trata o sucesso é exatamente quem precisa da rede de segurança. */
  function algumEhFuncao(argumentos) {
    return Array.prototype.some.call(argumentos, function (a) { return typeof a === "function"; });
  }

  $.ajaxPrefilter(function (options, originais, jqXHR) {
    // A exigência de que o argumento SEJA uma função não é detalhe: logo depois do prefilter o
    // próprio jQuery roda `jqXHR.fail( s.error )` para instalar o callback de opção — com
    // `undefined` quando ninguém passou `error:`. Sem esta guarda toda chamada do sistema saía
    // marcada como "trata o próprio erro" e o toast de 4xx nunca apareceria.
    ["fail", "always", "catch"].forEach(function (nome) {
      var original = jqXHR[nome];
      if (typeof original !== "function") return;
      jqXHR[nome] = function () {
        if (algumEhFuncao(arguments)) jqXHR.__trataErro = true;
        return original.apply(this, arguments);
      };
    });
    var thenOriginal = jqXHR.then;
    if (typeof thenOriginal === "function") {
      jqXHR.then = function (aoResolver, aoRejeitar) {
        if (typeof aoRejeitar === "function") jqXHR.__trataErro = true;
        return thenOriginal.apply(this, arguments);
      };
    }
  });

  $(document).ajaxError(function (event, jqXHR, settings) {
    if (jqXHR.__erroSilencioso) return;
    if (jqXHR.statusText === "abort") return;
    if (!window.Toast) return;

    var status = jqXHR.status;

    if (status === 401) {
      // GET /api/auth/eu é a própria checagem de sessão de js/auth.js, chamada em toda página
      // protegida mesmo sem login algum ainda — ali um 401 não é "sessão expirou", é "nunca
      // houve sessão", e auth.js já redireciona sozinho, sem precisar de aviso. O toast aqui é
      // só para um 401 em outra chamada (sessão que era válida e caiu no meio da navegação).
      if (settings && /\/api\/auth\/eu(\?|$)/.test(settings.url)) return;
      // login, troca de senha e troca de e-mail DEVOLVEM 401 quando a credencial informada está
      // errada — não é sessão expirada. Quem fez a chamada já tratou o erro na tela (prefilter
      // acima marcou __trataErro); avisar "sessão expirou" e recarregar aqui apagaria a mensagem
      // real e jogaria o usuário de volta ao login por um simples dedo bobo.
      if (jqXHR.__trataErro) return;
      // evita toast duplicado se duas chamadas caírem em 401 ao mesmo tempo
      if (avisoSessaoExpirada) return;
      avisoSessaoExpirada = true;
      Toast.aviso("Sua sessão expirou. Faça login novamente.");
      setTimeout(irParaLogin, 1200);
      return;
    }

    if (status === 0) {
      Toast.erro("Sem conexão com o servidor. Verifique sua rede.");
      return;
    }

    if (status >= 500) {
      Toast.erro(mensagemErro(jqXHR, "Erro no servidor. Tente novamente."));
      return;
    }

    // 403 continua mudo (ver o cabeçalho); os demais 4xx passam a avisar, menos quando a própria
    // chamada já tem tratamento de erro — aí a mensagem é da página, não daqui.
    if (status >= 400 && status !== 403 && !jqXHR.__trataErro) {
      Toast.erro(mensagemErro(jqXHR, "Não foi possível concluir a operação."));
    }
  });
})();
