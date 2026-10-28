/* Modal de informação da evidência citada pela IA.

 * A citação [msg:wpp|sms|msg:ID] (🔎) e [doc:ID] (📄) ganham ao lado uma segunda pastilha — ℹ️ —
 * que NÃO navega: abre um modal com a cadeia de custódia por trás da prova (apelido da mídia,
 * nome do arquivo, dispositivo, data/hora + timestamp da mensagem e hashes MD5/SHA-1/SHA-256),
 * lida sob demanda de GET /api/investigacao/evidencias/{fonte}/{id}.
 *
 * Injeta o próprio DOM (mesma base CSS .modal-overlay/.modal de js/utils/dialogo.js) e amarra um
 * handler de clique só para toda pastilha .chat-ref-info do sistema — presente em qualquer tela
 * que carregue este arquivo junto de js/utils/doc_trecho.js. Expõe window.EvidenciaInfo. */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  var FONTES_MSG = { wpp: "WhatsApp", sms: "SMS", msg: "Mensagem" };

  /* A pastilha ℹ️, no mesmo estilo .chat-ref da pastilha de link que fica ao lado.
   * Devolve HTML (o mundo dos marcadores é de strings — ver doc_trecho.js). */
  function pastilhaHtml(fonte, id) {
    return '<a class="chat-ref chat-ref-info" role="button" tabindex="0"' +
      ' data-fonte="' + esc(fonte) + '" data-id="' + esc(id) + '"' +
      ' title="Informações da evidência (mídia, arquivo, data, hashes)">ℹ️</a>';
  }

  /* Versão elemento (jQuery) para telas que montam o link por DOM, não por string — é o caso da
   * Busca de evidências, onde o destino é um botão de texto e não um emoji. */
  function pastilhaEl(fonte, id) {
    return $("<a>").addClass("chat-ref chat-ref-info").attr({
      href: "#", tabindex: 0,
      "data-fonte": fonte, "data-id": id,
      title: "Informações da evidência (mídia, arquivo, data, hashes)"
    }).text("ℹ️");
  }

  function linha(rotulo, valor, extraClasse, copiar) {
    var $l = $("<div>").addClass("evidencia-linha");
    $l.append($("<span>").addClass("evidencia-rotulo").text(rotulo));
    var $v = $("<span>").addClass("evidencia-valor").addClass(extraClasse || "")
      .text(valor == null || valor === "" ? "—" : String(valor));
    if (copiar && valor) {
      $v.append($("<button>").attr("type", "button").addClass("evidencia-copiar")
        .text("copiar").on("click", function () {
          window.AnalyserUtils.copiarTexto(String(valor), "Copiado.");
        }));
    }
    $l.append($v);
    return $l;
  }

  function mostrar(dados) {
    var U = window.AnalyserUtils;
    var ehMsg = dados.tp_evidencia === "mensagem";
    var rotuloTipo = ehMsg ? (FONTES_MSG[dados.fonte] || "Mensagem") : "Documento";

    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    // acima do painel flutuante de IA (z 200) e das bolhas do chat (z 250–320): o ℹ️ é clicado
    // justamente de dentro delas
    overlay.style.zIndex = "1300";
    var modal = document.createElement("div");
    modal.className = "modal";
    modal.style.width = "min(560px, 92vw)";

    var header = document.createElement("div");
    header.className = "modal-header";
    var h2 = document.createElement("h2");
    h2.textContent = "ℹ️ " + rotuloTipo + " · " + (dados.no_apelido || dados.no_arquivo || "#" + dados.id_prova);
    var fechar = document.createElement("button");
    fechar.type = "button";
    fechar.className = "modal-close";
    fechar.innerHTML = "&times;";
    header.appendChild(h2);
    header.appendChild(fechar);

    var body = document.createElement("div");
    body.className = "modal-body";

    var $body = $(body);
    $body.append(linha("Apelido da mídia", dados.no_apelido));
    if (ehMsg) $body.append(linha("Dispositivo", dados.no_dispositivo));
    $body.append(linha("Nome do arquivo", dados.no_arquivo, "evidencia-arquivo", true));
    if (ehMsg && dados.dt_mensagem) {
      $body.append(linha("Data/hora", U ? U.formatarDataHora(dados.dt_mensagem) : dados.dt_mensagem));
      $body.append(linha("Timestamp (epoch)", dados.nu_epoch, "evidencia-hash", true));
    }
    $body.append(linha("MD5", dados.co_hash_md5, "evidencia-hash", true));
    $body.append(linha("SHA-1", dados.co_hash_sha1, "evidencia-hash", true));
    $body.append(linha("SHA-256", dados.co_hash_sha256, "evidencia-hash", true));
    $body.append($("<div>").addClass("card-hint").css("margin-top", "8px").text(
      "Referência citada pela IA: [" + (ehMsg ? dados.fonte + ":" : "doc:") + dados.id_prova + "]" +
      (ehMsg ? "" : " — hashes calculados na anexação; nulos em documento antigo ou que mora em nó.")
    ));

    modal.appendChild(header);
    modal.appendChild(body);
    overlay.appendChild(modal);

    function fecharModal() {
      document.removeEventListener("keydown", noEsc);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
    function noEsc(e) { if (e.key === "Escape") fecharModal(); }
    fechar.addEventListener("click", fecharModal);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) fecharModal(); });
    document.addEventListener("keydown", noEsc);
    document.body.appendChild(overlay);
    fechar.focus();
  }

  function abrir(fonte, id) {
    var $overlay = $("<div>").addClass("modal-overlay").css("z-index", 1300);
    var $modal = $("<div>").addClass("modal").css("width", "min(560px, 92vw)");
    $modal.append($("<div>").addClass("modal-header")
      .append($("<h2>").text("ℹ️ Evidência")));
    var $corpo = $("<div>").addClass("modal-body").text("Carregando…");
    $modal.append($corpo);
    $overlay.append($modal);
    function sair() { $(document).off("keydown.evidencia"); $overlay.remove(); }
    $overlay.on("click", function (e) { if (e.target === $overlay[0]) sair(); });
    $(document).on("keydown.evidencia", function (e) { if (e.key === "Escape") sair(); });
    $overlay.appendTo(document.body);

    $.getJSON("/api/investigacao/evidencias/" + encodeURIComponent(fonte) + "/" + encodeURIComponent(id))
      .done(function (dados) {
        sair();
        mostrar(dados);
      })
      .fail(function (xhr) {
        $corpo.text(xhr && xhr.status === 403
          ? "Sem permissão para o inquérito desta evidência."
          : xhr && xhr.status === 404
            ? "Evidência não encontrada (a IA pode ter inventado o id)."
            : "Não foi possível carregar as informações da evidência.");
      });
  }

  // Um handler só, para toda pastilha ℹ️ da aplicação (chat, painel flutuante, resumo noturno,
  // busca) — a pastilha carrega fonte e id em data-*, então não precisa de estado por tela.
  $(document).on("click", ".chat-ref-info", function (e) {
    e.preventDefault();
    var $a = $(this);
    abrir($a.attr("data-fonte"), $a.attr("data-id"));
  }).on("keydown", ".chat-ref-info", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      $(this).trigger("click");
    }
  });

  window.EvidenciaInfo = {
    pastilhaHtml: pastilhaHtml,
    pastilhaEl: pastilhaEl,
    abrir: abrir,
  };
})();
