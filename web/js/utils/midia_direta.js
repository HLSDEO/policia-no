/* Mídia direta do nó (docs/no_midia_direta.md) — o atalho que o navegador tenta antes do central.

 * Quem está na LAN da delegacia baixa a mídia do nó que a gravou; todo o resto (VV, férias,
 * nó sem certificado) simplesmente não nota nada: o central responde a URL do nó quando pode,
 * o navegador SONDA o no-web uma vez por aba de navegador, e qualquer recusa (porta fechada,
 * certificado rejeitado, ticket vencido no meio da página) derruba aquela base por 10 minutos
 * e cai no proxy de sempre — a mesma URL que já era usada antes disto existir.

 * Uso nas telas:
 *   MidiaDireta.preparar([id1, id2, ...])   — na renderização da página: pergunta ao central
 *                                             as URLs diretas da leva (um POST, não N GETs)
 *   MidiaDireta.url(id, urlCentral)          — na hora de montar <img>/<video>/<a>: a direta
 *                                             se já conhecida e boa, a central caso contrário
 *   MidiaDireta.fallback(el, urlCentral)     — no onerror do elemento: se ele estava usando
 *                                             uma direta, marca a base como ruim e devolve o
 *                                             caminho central para re-setar o src            */
(function () {
  "use strict";

  var TTL_PROBA_MS = 10 * 60 * 1000;    // sonda por base: uma vez a cada 10 min por aba
  var TTL_URL_MS = 9 * 60 * 1000;       // o ticket vale 10 — 9 é usar com folga, não na beira
  var CHAVE_SESSAO = "mdBases";
  var SONDA_MS = 2000;                  // além disso a rede não está "lenta", está ausente

  var bases = {};   // "https://no-x:8443" -> { ok: bool, ts: epoch_ms }  (persiste na aba)
  var urls = {};    // "v12"/"d12" -> { url, ts } | { erro: true }

  try {
    var guardado = JSON.parse(sessionStorage.getItem(CHAVE_SESSAO) || "{}");
    var agora0 = Date.now();
    Object.keys(guardado).forEach(function (b) {
      if (guardado[b] && agora0 - (guardado[b].ts || 0) < TTL_PROBA_MS) bases[b] = guardado[b];
    });
  } catch (e) { /* sessionStorage privado/cheio: só perde a sonda da aba anterior */ }

  function gravarSessao() {
    try { sessionStorage.setItem(CHAVE_SESSAO, JSON.stringify(bases)); } catch (e) { /* idem */ }
  }

  function baseDe(url) {
    var m = /^(https?:\/\/[^\/]+)/i.exec(url || "");
    return m ? m[1] : null;
  }

  function chave(id, download) { return (download ? "d" : "v") + id; }

  /* A sonda é um GET /no/saude com mode:"no-cors": sem CORS não dá para LER a resposta, mas o
   * que importa aqui é o TCP+TLS+HTTP acontecer — promessa resolvida = a estação alcança o nó;
   * erro/rejeição/2 s de silêncio = não alcança (e o usuário nunca vai saber que tentou). */
  function sonda(base, aoDecidir) {
    var registro = bases[base];
    if (registro && Date.now() - registro.ts < TTL_PROBA_MS) { aoDecidir(registro.ok); return; }
    var decidido = false;
    function decide(ok) {
      if (decidido) return;
      decidido = true;
      bases[base] = { ok: ok, ts: Date.now() };
      gravarSessao();
      aoDecidir(ok);
    }
    if (!window.fetch) { decide(false); return; }
    var temporizador = setTimeout(function () { decide(false); }, SONDA_MS);
    fetch(base + "/no/saude", { cache: "no-store", mode: "no-cors" })
      .then(function () { clearTimeout(temporizador); decide(true); })
      .catch(function () { clearTimeout(temporizador); decide(false); });
  }

  /* Preparar é o único round-trip: o central responde a URL direta por arquivo (ou null para
   * "sirva por mim"). As URLs chegam mas só viram src depois da sonda da base — em VV a
   * resposta chega, a sonda falha uma vez, e o resto da aba inteira nem pergunta mais. */
  function preparar(ids, download) {
    if (!ids || !ids.length) return;
    var novos = [];
    ids.forEach(function (id) {
      var c = chave(id, download);
      var registro = urls[c];
      if (!registro || (registro.ts && Date.now() - registro.ts > TTL_URL_MS)) novos.push(id);
    });
    if (!novos.length) return;
    novos.forEach(function (id) { urls[chave(id, download)] = { pendente: true, ts: Date.now() }; });
    $.ajax({
      url: "/api/investigacao/arquivos/rotas-diretas",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ ids: novos, download: !!download })
    }).done(function (r) {
      var achados = (r && r.urls) || {};
      Object.keys(achados).forEach(function (id) {
        var c = chave(id, download);
        var u = achados[id];
        if (!u) { urls[c] = { url: null, ts: Date.now() }; return; }
        var base = baseDe(u);
        if (!base) { urls[c] = { url: null, ts: Date.now() }; return; }
        urls[c] = { url: u, base: base, ts: Date.now() };
        sonda(base, function (ok) { if (!ok) urls[c] = { url: null, ts: Date.now() }; });
      });
    }).fail(function () {
      novos.forEach(function (id) { urls[chave(id, download)] = { erro: true, ts: Date.now() }; });
    });
  }

  /* Sincrona de propósito: a tela monta <img src> em loop e não pode esperar promessa. Se a
   * resposta do central ainda veio pendente ou a sonda está em voo, devolve a URL central — o
   * ATALHO que chegou atrasado é simplesmente não usado, nunca uma tela em branco. */
  function url(idArquivo, urlCentral, download) {
    var registro = urls[chave(idArquivo, download)];
    if (!registro || registro.pendente || registro.erro || !registro.url) return urlCentral;
    if (registro.ts && Date.now() - registro.ts > TTL_URL_MS) return urlCentral;
    if (registro.base) {
      var b = bases[registro.base];
      if (!b || Date.now() - b.ts >= TTL_PROBA_MS) return urlCentral;  // sonda em voo/ausente
      if (!b.ok) return urlCentral;
    }
    return registro.url;
  }

  /* Errou com URL direta: pode ser ticket vencido na aba aberta há meia hora, Wi-Fi que caiu,
   * certificado que venceu de madrugada. A resposta certa é a mesma para todas: refaz pelo
   * central AGORA e para de oferecer a direta nesta aba. */
  function fallback(el, urlCentral) {
    var usado = el.getAttribute ? (el.getAttribute("src") || el.href || "") : "";
    var direto = null;
    Object.keys(urls).forEach(function (c) {
      var r = urls[c];
      if (r && r.url && usado.indexOf(r.url.slice(0, 64)) === 0) direto = r;
    });
    if (!direto) return urlCentral;  // já era o central: o erro é outro, não nosso
    if (direto.base) {
      bases[direto.base] = { ok: false, ts: Date.now() };
      gravarSessao();
    }
    return urlCentral;
  }

  window.MidiaDireta = { preparar: preparar, url: url, fallback: fallback };
})();
