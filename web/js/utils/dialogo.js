/* Diálogos em modal — substituem window.alert / confirm / prompt, que o navegador pode
 * suprimir ("impedir que esta página crie novas caixas de diálogo") deixando as ações sem
 * resposta. Injeta o próprio DOM, sem depender de HTML na página. Todos devolvem Promise:
 *
 *   Dialogo.alerta("Deu erro")                            -> Promise<void>
 *   Dialogo.confirmar({ mensagem, perigoso: true })       -> Promise<boolean>
 *   Dialogo.prompt({ mensagem, valor })                   -> Promise<string|null>  (null = cancelou)
 *
 * Cada opção aceita: titulo, mensagem, rotuloOk, rotuloCancelar. confirmar aceita `perigoso`
 * (botão vermelho). prompt aceita valor, placeholder, maxlength, multilinha. Passar uma string
 * no lugar do objeto é atalho para { mensagem }. */
(function () {
  "use strict";

  function normalizar(opts) {
    return typeof opts === "string" ? { mensagem: opts } : (opts || {});
  }

  function criarBase(titulo) {
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";

    var modal = document.createElement("div");
    modal.className = "modal";
    modal.style.width = "min(440px, 92vw)";

    var header = document.createElement("div");
    header.className = "modal-header";
    var h2 = document.createElement("h2");
    h2.textContent = titulo;
    var fechar = document.createElement("button");
    fechar.type = "button";
    fechar.className = "modal-close";
    fechar.innerHTML = "&times;";
    header.appendChild(h2);
    header.appendChild(fechar);

    var body = document.createElement("div");
    body.className = "modal-body";

    modal.appendChild(header);
    modal.appendChild(body);
    overlay.appendChild(modal);
    return { overlay: overlay, body: body, fechar: fechar };
  }

  function mensagemEl(texto) {
    var p = document.createElement("p");
    p.className = "card-hint";
    p.style.whiteSpace = "pre-wrap";
    p.textContent = texto || "";
    return p;
  }

  function acoesEl() {
    var d = document.createElement("div");
    d.className = "form-actions";
    return d;
  }

  function botao(rotulo, classe) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn " + classe;
    b.textContent = rotulo;
    return b;
  }

  // Fecha e resolve. onKey é removido para não vazar listeners no document.
  function fechador(overlay, onKey, resolve) {
    return function (valor) {
      document.removeEventListener("keydown", onKey);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(valor);
    };
  }

  function confirmar(opts) {
    opts = normalizar(opts);
    return new Promise(function (resolve) {
      var base = criarBase(opts.titulo || "Confirmar");
      base.body.appendChild(mensagemEl(opts.mensagem));
      var acoes = acoesEl();
      var ok = botao(opts.rotuloOk || "Confirmar", opts.perigoso ? "btn-danger" : "btn-primary");
      var cancelar = botao(opts.rotuloCancelar || "Cancelar", "btn-secondary");
      acoes.appendChild(ok);
      acoes.appendChild(cancelar);
      base.body.appendChild(acoes);

      var onKey = function (e) {
        if (e.key === "Escape") resolver(false);
        else if (e.key === "Enter") resolver(true);
      };
      var resolver = fechador(base.overlay, onKey, resolve);

      ok.addEventListener("click", function () { resolver(true); });
      cancelar.addEventListener("click", function () { resolver(false); });
      base.fechar.addEventListener("click", function () { resolver(false); });
      base.overlay.addEventListener("click", function (e) { if (e.target === base.overlay) resolver(false); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(base.overlay);
      ok.focus();
    });
  }

  function alerta(opts) {
    opts = normalizar(opts);
    return new Promise(function (resolve) {
      var base = criarBase(opts.titulo || "Aviso");
      base.body.appendChild(mensagemEl(opts.mensagem));
      var acoes = acoesEl();
      var ok = botao(opts.rotuloOk || "OK", "btn-primary");
      acoes.appendChild(ok);
      base.body.appendChild(acoes);

      var onKey = function (e) { if (e.key === "Escape" || e.key === "Enter") resolver(); };
      var resolver = fechador(base.overlay, onKey, resolve);

      ok.addEventListener("click", function () { resolver(); });
      base.fechar.addEventListener("click", function () { resolver(); });
      base.overlay.addEventListener("click", function (e) { if (e.target === base.overlay) resolver(); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(base.overlay);
      ok.focus();
    });
  }

  function prompt(opts) {
    opts = normalizar(opts);
    return new Promise(function (resolve) {
      var base = criarBase(opts.titulo || "");
      if (opts.mensagem) base.body.appendChild(mensagemEl(opts.mensagem));

      var campo = document.createElement(opts.multilinha ? "textarea" : "input");
      campo.className = "dialogo-campo";
      if (!opts.multilinha) campo.type = "text";
      campo.value = opts.valor != null ? opts.valor : "";
      if (opts.placeholder) campo.placeholder = opts.placeholder;
      if (opts.maxlength) campo.maxLength = opts.maxlength;
      base.body.appendChild(campo);

      var acoes = acoesEl();
      var ok = botao(opts.rotuloOk || "Salvar", "btn-primary");
      var cancelar = botao(opts.rotuloCancelar || "Cancelar", "btn-secondary");
      acoes.appendChild(ok);
      acoes.appendChild(cancelar);
      base.body.appendChild(acoes);

      var onKey = function (e) {
        if (e.key === "Escape") resolver(null);
        else if (e.key === "Enter" && !opts.multilinha) resolver(campo.value);
      };
      var resolver = fechador(base.overlay, onKey, resolve);

      ok.addEventListener("click", function () { resolver(campo.value); });
      cancelar.addEventListener("click", function () { resolver(null); });
      base.fechar.addEventListener("click", function () { resolver(null); });
      base.overlay.addEventListener("click", function (e) { if (e.target === base.overlay) resolver(null); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(base.overlay);
      campo.focus();
      if (campo.select) campo.select();
    });
  }

  window.Dialogo = { confirmar: confirmar, alerta: alerta, prompt: prompt };
})();
