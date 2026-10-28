/* Painel flutuante de IA — acompanha o investigador na tela sem tirá-lo dela: bolha fixa no
 * canto que abre um chat cujo contexto já vem restrito ao que está aberto.
 *
 * Três famílias de modo, pela URL da página:
 *  - "de documento" (rif.html/grafo_rif.html com ?rif=, simba.html/grafo_simba.html com ?simba=,
 *    ipei.html com ?ipei= e documento.html com ?doc=): escopo fixo naquele RIF/extrato/quebra de
 *    sigilo fiscal/documento anexado. Todos saem da tabela DOCUMENTOS abaixo.
 *  - "mensageiro" (whatsapp.html/mensageiros.html, com ?fonte=): escopo no mensageiro
 *    (WhatsApp/SMS/outros) deste aparelho, ou na conversa específica quando uma estiver
 *    selecionada em whatsapp.js (evento "analyser:conversa-aberta").
 *  - "extracao" (demais páginas de dados extraídos — arquivos/e-mails/navegação/contatos/apps/
 *    timeline/páginas de nuvem —, sem ?rif=/?simba=/?doc=/?fonte=): escopo no aparelho/conta corrente
 *    (?extracao=, mesmo tp_escopo="extracao" que o botão "Prompt de IA específico" já usa), ou
 *    sem restrição quando a página está mostrando "todos os aparelhos" (sem ?extracao=).
 *    A mesa de inquérito cai aqui, e de propósito sem restrição: ela é a tela do inquérito
 *    inteiro (só ?inquerito=), então o chat dela enxerga tudo — aparelhos, RIFs, extratos e IPLs.
 *
 * Reaproveita as mesmas rotas de investigacao/ia usadas por js/ia.js e o mesmo mecanismo de
 * escopo do backend (funcoes/ia_investigacao.ferramentas_para_inquerito).
 *
 * Nas telas de GRAFO (grafo_rif/grafo_simba/mesa_inquerito) o chat também filtra o desenho: a
 * pergunta vai com o painel de filtros da tela (window.GrafoFiltros, em js/utils/grafo_filtros.js)
 * e a IA pode devolver um marcador [filtro:{...}], que vira o botão "Aplicar no grafo" no fim da
 * resposta. Ver funcoes/ia_contexto_tela.py do lado do prompt.
 *
 * Se a delegacia do usuário logado tiver a IA desativada, o backend responde 403 em qualquer
 * chamada de .../ia/... (ver rotas/ia.py, _exigir_ia_ativa) — o botão simplesmente não aparece
 * nesse caso (checagem feita uma vez, ao carregar a página).
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  if (!idInquerito) return; // nada a fazer fora do contexto de um inquérito

  var idRif = paramUrl("rif") || null;
  var idSimba = paramUrl("simba") || null;
  var idIpei = paramUrl("ipei") || null; // ipei.html?inquerito=<id>&ipei=<id>
  var idTelefonia = paramUrl("telefonia") || null; // telefonia.html?inquerito=<id>&telefonia=<id>
  var idDocumento = paramUrl("doc") || null; // documento.html?inquerito=<id>&doc=<id>
  var idExtracao = paramUrl("extracao") || null; // "Todos os aparelhos" = null
  var fonteParam = paramUrl("fonte");

  // Estilos das peças que este arquivo injeta e ainda não têm regra no chat.css (contador e o
  // botão de fechar aba). Injetado aqui — único dono — para não exigir mexer em CSS alheio.
  // Copiar/feedback/evidências/ir-para-o-fim reusam as classes do chat.css (chat-bolha-acoes,
  // chat-feedback-btn, chat-cobertura, chat-ir-fim, chat-erro-retry).
  $("<style>").text(
    ".iaflt-contador{font-size:10.5px;color:var(--text-muted);text-align:right;padding:0 14px 2px;flex-shrink:0}" +
    ".iaflt-contador.limite{color:var(--danger);font-weight:600}" +
    "button.iaflt-aba-fechar{border:none;background:none;font-family:inherit;color:inherit;padding:0;cursor:pointer}" +
    ".iaflt-aba-nova{width:7px;height:7px;border-radius:50%;background:var(--warning,#f59e0b);flex-shrink:0}" +
    // painel fechado fica fora da ordem de Tab (o CSS base só esconde com opacity)
    ".iaflt-painel{visibility:hidden;transition:transform .15s ease,opacity .15s ease,visibility 0s linear .15s}" +
    ".iaflt-painel.aberto{visibility:visible;transition:transform .15s ease,opacity .15s ease,visibility 0s}"
  ).appendTo("head");

  // Os modos "de documento" só diferem em três coisas: o parâmetro da URL que os liga, a rota
  // que dá o rótulo e o texto do cabeçalho. Ficam numa tabela em vez de numa cadeia de ternários
  // porque a cadeia já tinha quatro níveis — e cada novo tipo de documento (aqui, a IPEI) entrava
  // como mais um aninhamento em três lugares diferentes do arquivo.
  var DOCUMENTOS = {
    rif: { id: idRif, rota: "/rifs", chaveId: "id_rif",
           rotulo: function (r) { return "RIF " + (r.nu_rif || r.no_pasta || idRif); },
           generico: function () { return "RIF " + idRif; },
           subtitulo: "RIF", restrito: "este RIF" },
    simba: { id: idSimba, rota: "/extratos-simba", chaveId: "id_extrato",
             rotulo: function (s) { return "Extrato " + (s.no_arquivo || idSimba); },
             generico: function () { return "Extrato " + idSimba; },
             subtitulo: "Extrato bancário (SIMBA)", restrito: "este extrato" },
    ipei: { id: idIpei, rota: "/ipeis", chaveId: "id_ipei",
            rotulo: function (p) { return "IPEI " + (p.nu_ipei || p.no_arquivo || idIpei); },
            generico: function () { return "IPEI " + idIpei; },
            subtitulo: "Quebra de sigilo fiscal (IPEI)", restrito: "esta quebra de sigilo fiscal" },
    telefonia: { id: idTelefonia, rota: "/telefonia", chaveId: "id_telefonia",
                 rotulo: function (t) { return "Telefonia " + (t.no_operadora || idTelefonia); },
                 generico: function () { return "Telefonia " + idTelefonia; },
                 subtitulo: "Telefonia (operadora)", restrito: "o retorno desta operadora" },
    documento: { id: idDocumento, rota: "/documentos", chaveId: "id_documento",
                 rotulo: function (d) { return d.no_curto || d.no_arquivo || ("Documento " + idDocumento); },
                 generico: function () { return "Documento " + idDocumento; },
                 subtitulo: "Documento anexado", restrito: "este documento" }
  };

  var modoDocumento = null;
  ["rif", "simba", "ipei", "telefonia", "documento"].forEach(function (m) {
    if (!modoDocumento && DOCUMENTOS[m].id) modoDocumento = m;
  });
  var modo = modoDocumento || (fonteParam ? "mensageiro" : "extracao");

  // só usados no modo "mensageiro" (whatsapp.html/mensageiros.html)
  var fonte = fonteParam || "wpp";               // wpp | sms | msg
  var appMensageiro = paramUrl("app") || null;   // quando fonte=msg: Instagram/Telegram/…

  var NOME_FONTE = { wpp: "WhatsApp", sms: "SMS", msg: appMensageiro || "Mensageiros" };
  var nomeFonteAtual = NOME_FONTE[fonte] || "Mensagens";

  var idConversaAberta = null;  // id_conversa (thread) selecionada em whatsapp.js — null = nenhuma
  var nomeConversaAberta = null;

  var tituloDocumento = null;   // rótulo do RIF/extrato/documento (modos "rif"/"simba"/"documento"), pré-carregado uma vez

  var idConversaIA = null;      // id_conversa_ia da aba ativa agora no painel
  var painelAberto = false;

  // ---------- abas: cada escopo (Geral do mensageiro + uma por conversa aberta nesta página)
  // vira uma aba própria, para não perder o chat "Geral" quando o investigador abre uma
  // conversa específica (ex.: ao clicar num link 🔎 de referência) — ver ativarAba/renderAbas.
  var abas = [];             // [{chave, esc, idConversaIA, feedbacks, avisosPendentes, ultimoEnvio}]
  var abaAtivaChave = null;
  var enviandoChaves = {};   // chave -> true do POST até nascer o processamento (trava reenvio por aba)
  var processamentos = {};   // chave -> acompanhamento ativo (SSE com fallback em polling) naquela aba
  // As abas abertas sobrevivem a um F5 na mesma aba do navegador (sessionStorage): só o escopo
  // é gravado — a conversa de IA de cada uma é resolvida de novo sob demanda (resolverConversa).
  var CHAVE_ABAS = "iaflt-abas:" + idInquerito + ":" + modo + (modo === "mensageiro" ? ":" + fonte : "");

  // ---------- rótulo do RIF/extrato/documento (modos "rif"/"simba"/"documento") ----------

  function carregarTituloDocumento(callback) {
    var doc = DOCUMENTOS[modo];
    if (!doc) {
      if (callback) callback();
      return;
    }
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + doc.rota)
      .done(function (lista) {
        var achado = (lista || []).filter(function (x) {
          return String(x[doc.chaveId]) === String(doc.id);
        })[0];
        if (achado) tituloDocumento = doc.rotulo(achado);
      })
      .always(function () {
        tituloDocumento = tituloDocumento || doc.generico();
        if (callback) callback();
      });
  }

  // ---------- nome do aparelho, do cache que navegacao_inquerito.js já escreve em sessionStorage ----------
  function nomeExtracaoAtual() {
    if (!idExtracao) return "todo o inquérito";
    try {
      var bruto = sessionStorage.getItem("nav:extracoes:" + idInquerito);
      if (bruto) {
        var lista = (JSON.parse(bruto).dados || []);
        var alvo = lista.filter(function (x) { return String(x.id_extracao) === String(idExtracao); })[0];
        if (alvo) return alvo.no_dispositivo || alvo.no_fabricante || alvo.no_arquivo || ("extração " + idExtracao);
      }
    } catch (e) { /* cache ausente/corrompido — usa o genérico abaixo */ }
    return "este aparelho";
  }

  // ---------- rótulo da página (modo "extracao") — reaproveita o <title> em vez de duplicar
  // o nome de cada tela aqui; todas seguem o padrão "Nome · PolícIA" (ver <title> de cada html)
  function tituloPagina() {
    return (document.title || "").replace(/\s*·\s*PolícIA\s*$/i, "") || "Dados extraídos";
  }

  // ---------- markdown mínimo (mesmas marcações de js/ia.js, inclusive bloco ``` ) ----------
  function escaparHtml(t) {
    return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // link markdown só para http(s), absoluto ou âncora — nada de javascript:/data: no href
  function linkMarkdown(rotulo, url) {
    var limpa = String(url || "").trim().replace(/"/g, "%22");
    if (!/^(https?:\/\/|\/|#|\.\/|\.\.\/)/i.test(limpa)) return null;
    return '<a href="' + limpa + '" target="_blank" rel="noopener">' + rotulo + "</a>";
  }

  function markdownParaHtml(texto) {
    var blocos = escaparHtml(texto).split("```");
    var saida = "";
    blocos.forEach(function (bloco, i) {
      if (i % 2 === 1) {
        // dentro de ``` ``` — só remove a dica de linguagem da 1ª linha, sem formatar nada
        saida += "<pre><code>" + bloco.replace(/^[a-z]*\n/, "") + "</code></pre>";
        return;
      }
      saida += markdownLinhas(bloco);
    });
    return saida;
  }

  function markdownLinhas(bloco) {
    var html = "", listaAberta = false, linhasTabela = [];

    function fecharTabela() {
      if (!linhasTabela.length) return;
      html += "<div class='chat-md-tabela'><table>";
      linhasTabela.forEach(function (celulas, idx) {
        var tag = idx === 0 ? "th" : "td";
        html += "<tr>" + celulas.map(function (c) { return "<" + tag + ">" + c + "</" + tag + ">"; }).join("") + "</tr>";
      });
      html += "</table></div>";
      linhasTabela = [];
    }

    bloco.split("\n").forEach(function (linha) {
      var f = linha
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        // link markdown — é assim que chega o "Baixar este relatório em Word (.docx)" que o
        // backend anexa à resposta quando o investigador pede um relatório (ia_relatorio_docx.py)
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (todo, rotulo, url) {
          return linkMarkdown(rotulo, url) || todo;
        });

      // linha de tabela: | a | b | ... | — a separadora (|---|---|) é descartada
      if (/^\s*\|.*\|\s*$/.test(f)) {
        if (!/^[\s|:-]+$/.test(f)) {
          linhasTabela.push(f.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }));
        }
        return;
      }
      fecharTabela();

      var item = f.match(/^\s*[-*]\s+(.*)$/);
      if (item) {
        if (!listaAberta) { html += "<ul>"; listaAberta = true; }
        html += "<li>" + item[1] + "</li>";
        return;
      }
      if (listaAberta) { html += "</ul>"; listaAberta = false; }
      html += f.trim() === "" ? "<div class='chat-md-espaco'></div>" : "<div>" + f + "</div>";
    });
    fecharTabela();
    if (listaAberta) html += "</ul>";
    return html;
  }

  // Transforma os marcadores de evidência [msg:wpp:ID]/[msg:sms:ID]/[msg:msg:ID] que a IA cita em
  // links que abrem a mensagem original, e [doc:ID] / [doc:ID:PÁGINA] em link que abre a tela do
  // documento na página e no trecho citados (mesmo comportamento de js/ia.js — ver lá o porquê).
  function linkarMarcadores(html) {
    return html
      .replace(/\[doc:\d+(?::\d+(?:-\d+)?)?\]/g, function (marcador) {
        return DocTrecho.linkarMarcadores(marcador, { inquerito: idInquerito });
      })
      .replace(/\[msg:(wpp|sms|msg):(\d+)\]/g, function (_todo, fonteRef, id) {
        var pagina = MapaTelas.paginaConversa(fonteRef);
        var href = pagina + "?inquerito=" + encodeURIComponent(idInquerito) + "&fonte=" + fonteRef + "&msg=" + id;
        return '<a class="chat-ref" href="' + href + '" title="Abrir a mensagem original">🔎</a>' +
          (window.EvidenciaInfo ? EvidenciaInfo.pastilhaHtml(fonteRef, id) : "");
      });
  }

  // ---------- ações da bolha (copiar / 👍👎 / lastro de evidências) — paridade com js/ia.js ----------
  var RE_MSG_EVIDENCIA = /\[msg:(?:wpp|sms|msg):\d+\]/g;
  var RE_DOC_EVIDENCIA = /\[doc:\d+(?::\d+(?:-\d+)?)?\]/g;

  function contarEvidencias(texto) {
    var t = String(texto || "");
    var msgs = (t.match(RE_MSG_EVIDENCIA) || []).length;
    var docs = (t.match(RE_DOC_EVIDENCIA) || []).length;
    return { msgs: msgs, docs: docs, total: msgs + docs };
  }

  function textoCobertura(ev) {
    if (!ev.total) return "sem citação";
    var partes = [];
    if (ev.msgs) partes.push(ev.msgs + (ev.msgs === 1 ? " msg" : " msgs"));
    if (ev.docs) partes.push(ev.docs + (ev.docs === 1 ? " doc" : " docs"));
    return "📎 " + ev.total + (ev.total === 1 ? " evidência" : " evidências") + " · " + partes.join(" · ");
  }

  function copiarTexto(texto, okMsg) {
    function ok() { if (window.Toast) Toast.sucesso(okMsg || "Copiado."); }
    function fallback() {
      try {
        var $t = $("<textarea>").val(texto).css({ position: "fixed", opacity: "0" }).appendTo("body");
        $t[0].select();
        document.execCommand("copy");
        $t.remove();
        ok();
      } catch (e) {
        if (window.Toast) Toast.erro("Não foi possível copiar.");
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(ok, fallback);
    } else {
      fallback();
    }
  }

  function feedbackDe(aba, idMensagem) {
    return (aba && aba.feedbacks && aba.feedbacks[idMensagem]) || { positivo: 0, negativo: 0, meu: null };
  }

  function aplicarFeedbacks(aba) {
    $mensagens.find(".chat-linha.assistant").each(function () {
      var id = $(this).attr("data-id-mensagem");
      if (id == null || id === "") return;
      var fb = feedbackDe(aba, id);
      var $linha = $(this);
      $linha.find(".chat-feedback-btn[data-fb='positivo']")
        .toggleClass("ativo", fb.meu === "positivo")
        .find(".chat-feedback-conta").text(fb.positivo ? String(fb.positivo) : "");
      $linha.find(".chat-feedback-btn[data-fb='negativo']")
        .toggleClass("ativo", fb.meu === "negativo")
        .find(".chat-feedback-conta").text(fb.negativo ? String(fb.negativo) : "");
    });
  }

  function carregarFeedbacks(idConversa, aba) {
    if (!idConversa || !aba) return;
    $.getJSON("/api/investigacao/ia/conversas/" + idConversa + "/feedbacks").done(function (mapa) {
      if (idConversaIA !== idConversa) return; // trocou de aba no meio da leitura
      aba.feedbacks = mapa || {};
      aplicarFeedbacks(aba);
    });
  }

  function enviarFeedback(idMensagem, voto, $btn, aba) {
    if (idMensagem == null || !$btn) return;
    var atual = feedbackDe(aba, idMensagem).meu;
    var novo = (atual === voto) ? null : voto; // votar de novo no mesmo remove
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/ia/mensagens/" + idMensagem + "/feedback",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ tp_feedback: novo })
    }).done(function (resp) {
      if (!aba.feedbacks) aba.feedbacks = {};
      aba.feedbacks[idMensagem] = { positivo: resp.positivo || 0, negativo: resp.negativo || 0, meu: resp.meu || null };
      var $linha = $btn.closest(".chat-linha");
      $linha.find(".chat-feedback-btn[data-fb='positivo']")
        .toggleClass("ativo", resp.meu === "positivo")
        .find(".chat-feedback-conta").text(resp.positivo ? String(resp.positivo) : "");
      $linha.find(".chat-feedback-btn[data-fb='negativo']")
        .toggleClass("ativo", resp.meu === "negativo")
        .find(".chat-feedback-conta").text(resp.negativo ? String(resp.negativo) : "");
    }).fail(function (xhr) {
      if (window.Toast) Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao registrar avaliação.");
    }).always(function () {
      $btn.prop("disabled", false);
    });
  }

  // ---------- filtros do grafo propostos pela IA ----------
  // Nas telas de grafo (grafo_rif/grafo_simba/mesa_inquerito) a pergunta vai com o esquema de
  // filtros da tela (ver contextoTela) e a IA pode responder com um marcador
  // [filtro:{"campo":"valor",...}], que vira o botão "Aplicar no grafo" abaixo da resposta.
  // O filtro NUNCA é aplicado sozinho: quem manda no que está na tela é o investigador — ele lê
  // o que a IA propôs, clica se concordar, e tem "Desfazer" para voltar ao recorte anterior.
  var RE_FILTRO = /\[filtro:(\{[\s\S]*?\})\]/g;

  function temGrafo() {
    return !!(window.GrafoFiltros && GrafoFiltros.disponivel());
  }

  function contextoTela() {
    if (!temGrafo()) return null;
    try { return GrafoFiltros.esquema(); } catch (e) { return null; }
  }

  // Separa os marcadores do texto: o que sobra vira a resposta em markdown, e os objetos viram
  // botões. Marcador com JSON quebrado fica no texto mesmo — sumir com ele esconderia o problema.
  function extrairFiltros(texto) {
    var filtros = [];
    var limpo = String(texto || "").replace(RE_FILTRO, function (todo, json) {
      var obj;
      try { obj = JSON.parse(json); } catch (e) { return todo; }
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) return todo;
      filtros.push(obj);
      return "";
    });
    return { texto: limpo, filtros: filtros };
  }

  // O que o botão vai fazer, em português, ANTES do clique — o investigador precisa saber o que
  // muda na tela dele sem ter que confiar no rótulo genérico.
  function descreverFiltro(valores) {
    var esquema = contextoTela();
    if (!esquema) return "";
    var porNome = {};
    esquema.campos.forEach(function (c) { porNome[c.nome] = c; });
    var partes = [];
    Object.keys(valores).forEach(function (nome) {
      var campo = porNome[nome];
      var v = valores[nome];
      if (Array.isArray(v)) v = v.join(", ");
      if (v === true) v = "sim";
      if (v === false) v = "não";
      partes.push((campo ? campo.rotulo : nome) + ": " + v);
    });
    return partes.length ? partes.join(" · ") : "Limpar todos os filtros do grafo";
  }

  function botaoFiltro(valores) {
    var $wrap = $("<div>").addClass("iaflt-filtro");
    $("<div>").addClass("iaflt-filtro-desc").text(descreverFiltro(valores)).appendTo($wrap);
    var $acoes = $("<div>").addClass("iaflt-filtro-acoes").appendTo($wrap);
    var $aplicar = $("<button>").attr("type", "button").addClass("iaflt-filtro-btn")
      .text(Object.keys(valores).length ? "Aplicar no grafo" : "Limpar filtros do grafo")
      .appendTo($acoes);
    var $status = $("<div>").addClass("iaflt-filtro-status hidden").appendTo($wrap);

    if (!temGrafo()) {
      $aplicar.prop("disabled", true).attr("title", "Esta tela não tem grafo para filtrar.");
      return $wrap;
    }

    $aplicar.on("click", function () {
      var res = GrafoFiltros.aplicar(valores);
      if (!res) return;
      $acoes.empty();
      var texto = res.resumo ? "Grafo filtrado — " + res.resumo : "Filtros do grafo limpos.";
      if (res.ignorados.length) texto += " · Não aplicado: " + res.ignorados.join("; ");
      $status.removeClass("hidden").toggleClass("com-aviso", !!res.ignorados.length).text(texto);
      $("<button>").attr("type", "button").addClass("iaflt-filtro-btn secundario")
        .text("Desfazer")
        .on("click", function () {
          GrafoFiltros.restaurar(res.anterior);
          $status.addClass("hidden").text("");
          $acoes.empty().append($aplicar);
        })
        .appendTo($acoes);
    });
    return $wrap;
  }

  function renderMensagem(m, aba) {
    var $linha = $("<div>").addClass("chat-linha " + m.tp_papel);
    if (m.id_mensagem_ia != null) $linha.attr("data-id-mensagem", m.id_mensagem_ia);
    var $bolha = $("<div>").addClass("chat-bolha");
    if (m.tp_papel === "assistant") {
      var separado = extrairFiltros(m.tx_conteudo);
      $("<div>").addClass("chat-md").html(linkarMarcadores(markdownParaHtml(separado.texto))).appendTo($bolha);
      separado.filtros.forEach(function (f) { $bolha.append(botaoFiltro(f)); });
    } else {
      $("<div>").text(m.tx_conteudo).appendTo($bolha);
    }
    // ações na bolha: copiar (toda mensagem) + 👍/👎 e lastro (só resposta da IA já gravada)
    if (m.id_mensagem_ia != null || m.tp_papel === "user") {
      (function (msg, $b) {
        var $acoes = $("<div>").addClass("chat-bolha-acoes");
        $("<button>").addClass("chat-copiar-btn").attr("type", "button")
          .attr("title", "Copiar o texto desta mensagem").text("⧉ Copiar")
          .on("click", function () { copiarTexto(String(msg.tx_conteudo || ""), "Mensagem copiada."); })
          .appendTo($acoes);
        if (msg.tp_papel === "assistant" && msg.id_mensagem_ia != null && aba) {
          var fb = feedbackDe(aba, msg.id_mensagem_ia);
          [["positivo", "👍", "Avaliar como útil"], ["negativo", "👎", "Avaliar como não útil"]].forEach(function (cfg) {
            var tipo = cfg[0];
            var $fb = $("<button>").addClass("chat-feedback-btn").attr("type", "button")
              .attr("data-fb", tipo).attr("title", cfg[2])
              .toggleClass("ativo", fb.meu === tipo);
            $("<span>").text(cfg[1]).appendTo($fb);
            var n = tipo === "positivo" ? fb.positivo : fb.negativo;
            $("<span>").addClass("chat-feedback-conta").text(n ? String(n) : "").appendTo($fb);
            $fb.on("click", function () { enviarFeedback(msg.id_mensagem_ia, tipo, $fb, aba); });
            $fb.appendTo($acoes);
          });
          var ev = contarEvidencias(msg.tx_conteudo);
          $("<span>").addClass("chat-cobertura")
            .toggleClass("com-lastro", ev.total > 0).toggleClass("sem-lastro", !ev.total)
            .attr("title", ev.total ? "Citações clicáveis nesta resposta" : "Resposta sem citação clicável [msg:] ou [doc:]")
            .text(textoCobertura(ev)).appendTo($acoes);
        }
        $b.append($acoes);
      })(m, $bolha);
    }
    $linha.append($bolha);
    return $linha;
  }

  // ---------- DOM (injetado — nenhuma página precisa de markup próprio, só incluir este script) ----------

  var ICONE_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var ICONE_ENVIAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  var ICONE_MAXIMIZAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>';

  var $botao = $(
    '<button type="button" class="iaflt-botao hidden" id="iaflt-botao" title="Conversar com a IA sobre esta tela">' + ICONE_CHAT + "</button>"
  ).appendTo("body");

  var $painel = $(
    '<div class="iaflt-painel" id="iaflt-painel" role="dialog" aria-modal="false" aria-labelledby="iaflt-titulo" aria-hidden="true">' +
      '<div class="iaflt-cabecalho">' +
        '<div class="iaflt-cabecalho-titulos">' +
          '<div class="iaflt-titulo" id="iaflt-titulo">IA</div>' +
          '<div class="iaflt-subtitulo" id="iaflt-subtitulo"></div>' +
        "</div>" +
        '<button type="button" class="iaflt-maximizar" id="iaflt-maximizar" title="Abrir em tela cheia (ia.html)" aria-label="Abrir em tela cheia">' + ICONE_MAXIMIZAR + "</button>" +
        '<button type="button" class="iaflt-fechar" id="iaflt-fechar" title="Fechar" aria-label="Fechar">&times;</button>' +
      "</div>" +
      '<div class="iaflt-abas hidden" id="iaflt-abas" role="tablist" aria-label="Abas de conversa"></div>' +
      '<div class="iaflt-escopo-banner hidden" id="iaflt-escopo-banner" role="note"></div>' +
      '<div class="iaflt-mensagens" id="iaflt-mensagens" role="log" aria-live="polite" aria-label="Mensagens da conversa"></div>' +
      '<button type="button" class="chat-ir-fim hidden" id="iaflt-ir-fim">↓ Ir para o fim <span class="chat-ir-fim-novas hidden" id="iaflt-ir-fim-novas"></span></button>' +
      '<div class="iaflt-digitando hidden" id="iaflt-digitando" role="status">A IA está respondendo…</div>' +
      '<div class="iaflt-contador hidden" id="iaflt-contador" aria-live="polite"></div>' +
      '<form class="iaflt-form hidden" id="iaflt-form">' +
        '<textarea id="iaflt-input" rows="1" placeholder="Escreva uma mensagem…" maxlength="8000" aria-label="Mensagem para a IA"></textarea>' +
        '<button type="submit" class="iaflt-enviar" id="iaflt-enviar-btn" aria-label="Enviar mensagem">' + ICONE_ENVIAR + "</button>" +
      "</form>" +
      '<div class="iaflt-resize iaflt-resize-se" data-resize="se"></div>' +
      '<div class="iaflt-resize iaflt-resize-sw" data-resize="sw"></div>' +
      '<div class="iaflt-resize iaflt-resize-ne" data-resize="ne"></div>' +
      '<div class="iaflt-resize iaflt-resize-nw" data-resize="nw"></div>' +
      '<div class="iaflt-resize iaflt-resize-e" data-resize="e"></div>' +
      '<div class="iaflt-resize iaflt-resize-w" data-resize="w"></div>' +
      '<div class="iaflt-resize iaflt-resize-s" data-resize="s"></div>' +
      '<div class="iaflt-resize iaflt-resize-n" data-resize="n"></div>' +
    "</div>"
  ).appendTo("body");

  var $mensagens = $("#iaflt-mensagens");
  var $digitando = $("#iaflt-digitando");
  var $form = $("#iaflt-form");
  var $input = $("#iaflt-input");
  var $enviarBtn = $("#iaflt-enviar-btn");
  var $escopoBanner = $("#iaflt-escopo-banner");
  var $abasBar = $("#iaflt-abas");
  var $resizeHandles = $painel.find(".iaflt-resize");
  var $contador = $("#iaflt-contador");
  var $irFim = $("#iaflt-ir-fim");
  var $irFimNovas = $("#iaflt-ir-fim-novas");
  var MAX_CHARS = 8000;

  // ---------- resize do painel ----------
  var resizeState = null;

  function iniciarResize(e, direcao) {
    e.preventDefault(); e.stopPropagation();
    resizeState = {
      direcao: direcao,
      startX: e.clientX || (e.touches && e.touches[0].clientX),
      startY: e.clientY || (e.touches && e.touches[0].clientY),
      startWidth: $painel[0].offsetWidth,
      startHeight: $painel[0].offsetHeight
    };
    $(document).on("mousemove.iafltResize touchmove.iafltResize", moverResize);
    $(document).on("mouseup.iafltResize touchend.iafltResize", finalizarResize);
    $("body").addClass("iaflt-resizing");
  }

  function moverResize(e) {
    if (!resizeState) return;
    var clientX = e.clientX || (e.touches && e.touches[0].clientX);
    var clientY = e.clientY || (e.touches && e.touches[0].clientY);
    if (clientX == null || clientY == null) return;

    var dx = clientX - resizeState.startX;
    var dy = clientY - resizeState.startY;
    var dir = resizeState.direcao;

    var novaWidth = resizeState.startWidth;
    var novaHeight = resizeState.startHeight;

    var minW = 300, minH = 300;
    var maxW = Math.min(1200, $(window).width() - 28);
    var maxH = Math.min(900, $(window).height() - 116);

    if (dir.indexOf("e") !== -1 || dir.indexOf("w") !== -1) {
      // indexOf (e não dir === "w"): nas quinas a direção vem composta ("nw", "sw") — com
      // igualdade estrita o sinal saía invertido e a quina encolhia ao arrastar para fora
      var dW = dir.indexOf("w") !== -1 ? -dx : dx;
      novaWidth = Math.min(maxW, Math.max(minW, resizeState.startWidth + dW));
    }
    if (dir.indexOf("s") !== -1 || dir.indexOf("n") !== -1) {
      var dH = dir.indexOf("n") !== -1 ? -dy : dy;
      novaHeight = Math.min(maxH, Math.max(minH, resizeState.startHeight + dH));
    }

    $painel.css({ width: novaWidth + "px", height: novaHeight + "px" });
  }

  function finalizarResize() {
    if (!resizeState) return;
    resizeState = null;
    $(document).off(".iafltResize");
    $("body").removeClass("iaflt-resizing");
    try {
      sessionStorage.setItem("iaflt-geom", JSON.stringify({ w: $painel[0].offsetWidth, h: $painel[0].offsetHeight }));
    } catch (e) { /* sessionStorage cheio/bloqueado — só não lembra do próximo F5 */ }
  }

  // retoma o tamanho escolhido antes (mesma sessão), ajustado à janela atual
  try {
    var geomSalva = JSON.parse(sessionStorage.getItem("iaflt-geom") || "null");
    if (geomSalva && geomSalva.w && geomSalva.h) {
      $painel.css({
        width: Math.max(300, Math.min(geomSalva.w, $(window).width() - 28)) + "px",
        height: Math.max(300, Math.min(geomSalva.h, $(window).height() - 116)) + "px"
      });
    }
  } catch (e) { /* valor corrompido — segue com o tamanho padrão */ }

  $resizeHandles.on("mousedown touchstart", function (e) {
    var dir = $(this).data("resize");
    if (dir) iniciarResize(e, dir);
  });

  // ---------- escopo: o que está aberto agora determina a que a próxima conversa de IA se prende ----------

  // escopo "Geral" do mensageiro (todo o WhatsApp/SMS/etc. deste aparelho, sem conversa específica)
  function escopoGeral() {
    return {
      tp: fonte,
      id: idExtracao ? Number(idExtracao) : null,
      titulo: nomeFonteAtual + " — " + nomeExtracaoAtual(),
    };
  }

  function escopoAtual() {
    var doc = DOCUMENTOS[modo];
    if (doc) return { tp: modo, id: Number(doc.id), titulo: tituloDocumento || doc.generico() };
    if (modo === "extracao") {
      return idExtracao
        ? { tp: "extracao", id: Number(idExtracao), titulo: nomeExtracaoAtual() }
        : { tp: null, id: null, titulo: "todo o inquérito" };
    }
    if (idConversaAberta) {
      return { tp: "conversa_" + fonte, id: idConversaAberta, titulo: nomeConversaAberta || "Conversa" };
    }
    return escopoGeral();
  }

  function mesmoEscopo(a, b) {
    return a && b && a.tp === b.tp && String(a.id || "") === String(b.id || "");
  }

  function chaveEscopo(esc) { return esc.tp + ":" + (esc.id || ""); }

  function acharAba(chave) {
    return abas.filter(function (a) { return a.chave === chave; })[0];
  }

  // no modo "mensageiro" a aba "Geral" fica sempre disponível, mesmo que o investigador
  // chegue direto numa conversa específica (ex.: pelo link 🔎 de uma referência) — sem isso,
  // uma vez que idConversaAberta é fixado não há como voltar ao chat geral (ver whatsapp.js,
  // que não tem "desselecionar conversa").
  if (modo === "mensageiro") {
    var escGeralInicial = escopoGeral();
    abas.push({ chave: chaveEscopo(escGeralInicial), esc: escGeralInicial, idConversaIA: null });
  }

  // retoma as abas de conversa da mesma sessão (a "Geral" já foi criada acima; a do mensageiro
  // muda de identidade quando o aparelho na URL muda, então só recriamos as de conversa)
  try {
    var abasSalvas = JSON.parse(sessionStorage.getItem(CHAVE_ABAS) || "null");
    if (abasSalvas && Array.isArray(abasSalvas.escs)) {
      abasSalvas.escs.forEach(function (esc) {
        if (!esc || !esc.tp) return;
        if (modo === "mensageiro" && esc.tp.indexOf("conversa_") !== 0) return;
        var ch = chaveEscopo(esc);
        if (!acharAba(ch)) abas.push({ chave: ch, esc: esc, idConversaIA: null, feedbacks: {}, avisosPendentes: [] });
      });
      if (abasSalvas.ativa && acharAba(abasSalvas.ativa)) abaAtivaChave = abasSalvas.ativa;
    }
  } catch (e) { /* valor corrompido — começa sem abas lembradas */ }

  function rotuloAba(esc) {
    return esc.tp.indexOf("conversa_") === 0 ? esc.titulo : "Geral";
  }

  function salvarAbas() {
    try {
      sessionStorage.setItem(CHAVE_ABAS, JSON.stringify({
        escs: abas.map(function (a) { return a.esc; }),
        ativa: abaAtivaChave
      }));
    } catch (e) { /* idem geometria */ }
  }

  function renderAbas() {
    salvarAbas();
    $abasBar.empty();
    if (modo !== "mensageiro" || abas.length < 2) { $abasBar.addClass("hidden"); return; }
    $abasBar.removeClass("hidden");
    abas.forEach(function (a) {
      var rotulo = rotuloAba(a.esc);
      var $aba = $("<button>").addClass("iaflt-aba").attr({
        type: "button", role: "tab",
        "aria-selected": a.chave === abaAtivaChave ? "true" : "false",
        "aria-label": rotulo
      })
        .toggleClass("ativa", a.chave === abaAtivaChave)
        .on("click", function () {
          if (a.chave !== abaAtivaChave) ativarAba(a.esc);
        });
      $("<span>").addClass("iaflt-aba-rotulo").text(rotulo).appendTo($aba);
      // bolinha de aviso: chegou resposta/erro enquanto a aba estava desativada
      if (a.pendenteRecarga || (a.avisosPendentes && a.avisosPendentes.length)) {
        $("<span>").addClass("iaflt-aba-nova").attr("title", "Novidades nesta aba").appendTo($aba);
      }
      if (a.esc.tp.indexOf("conversa_") === 0) {
        $("<button>").addClass("iaflt-aba-fechar").attr({
          type: "button", title: "Fechar esta aba", "aria-label": "Fechar aba " + rotulo
        }).html("&times;")
          .on("click", function (e) { e.stopPropagation(); fecharAba(a.chave); })
          .appendTo($aba);
      }
      $abasBar.append($aba);
    });
  }

  function fecharAba(chave) {
    var idx = abas.map(function (a) { return a.chave; }).indexOf(chave);
    if (idx === -1) return;
    var eraAtiva = abaAtivaChave === chave;
    abas.splice(idx, 1);
    if (!eraAtiva) { renderAbas(); return; }
    if (abas.length) ativarAba(abas[0].esc); // volta pra "Geral", que nunca é removida
    else { abaAtivaChave = null; renderAbas(); }
  }

  function atualizarCabecalho(esc) {
    var doc = DOCUMENTOS[modo];
    if (doc) {
      $("#iaflt-titulo").text("🤖 " + esc.titulo);
      $("#iaflt-subtitulo").text(doc.subtitulo);
      $escopoBanner.text(
        "🔒 Restrito a " + doc.restrito +
        " — sem acesso a outros documentos ou ao conteúdo do aparelho deste inquérito."
      ).removeClass("hidden");
      return;
    }
    if (modo === "extracao") {
      $("#iaflt-titulo").text("🤖 " + tituloPagina());
      $("#iaflt-subtitulo").text(esc.titulo);
      $escopoBanner.text(
        esc.id
          ? "🔒 Restrito a " + esc.titulo + " — sem acesso a outros aparelhos, RIFs, extratos ou IPLs do inquérito."
          : "Conversa geral do inquérito — sem restrição de escopo."
      ).removeClass("hidden");
      return;
    }
    var deConversa = esc.tp.indexOf("conversa_") === 0;
    $("#iaflt-titulo").text(deConversa ? "💬 " + esc.titulo : "🤖 " + nomeFonteAtual);
    $("#iaflt-subtitulo").text(deConversa ? nomeFonteAtual + " — " + nomeExtracaoAtual() : nomeExtracaoAtual());
    $escopoBanner.text(
      deConversa
        ? "🔒 Restrito a esta conversa — a IA não vê outros contatos, ligações ou dados deste inquérito."
        : "🔒 Restrito a " + nomeFonteAtual + (esc.id ? " deste aparelho" : " (todo o inquérito)") + " — sem acesso a outros dados."
    ).removeClass("hidden");
  }

  // ---------- carregar/enviar mensagens ----------

  // Rolagem: só desce sozinho quando já estava no fim; senão, mostra o botão flutuante
  // "Ir para o fim" com contador de novidades (mesma regra de js/ia.js).
  var novasDesdeFim = 0;

  function estaNoFim() {
    var el = $mensagens[0];
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function rolarParaFim(forcar) {
    var el = $mensagens[0];
    if (!el) return;
    if (forcar || estaNoFim()) {
      el.scrollTop = el.scrollHeight;
      novasDesdeFim = 0;
    } else {
      novasDesdeFim += 1;
    }
    atualizarIrFim();
  }

  function atualizarIrFim() {
    if (!$irFim.length) return;
    var el = $mensagens[0];
    var longe = el ? (el.scrollHeight - el.scrollTop - el.clientHeight > 160) : false;
    $irFim.toggleClass("hidden", !longe && !novasDesdeFim);
    if ($irFimNovas.length) {
      $irFimNovas.toggleClass("hidden", !novasDesdeFim)
        .text(novasDesdeFim ? String(novasDesdeFim) : "");
    }
  }

  $mensagens.on("scroll", function () {
    if (estaNoFim()) novasDesdeFim = 0;
    atualizarIrFim();
  });
  $irFim.on("click", function () { rolarParaFim(true); });

  function carregarMensagens(idConversa, aba) {
    $mensagens.empty();
    $.getJSON("/api/investigacao/ia/conversas/" + idConversa + "/mensagens").done(function (msgs) {
      if (idConversaIA !== idConversa) return; // trocou de escopo enquanto isto carregava
      if (!msgs.length) {
        $mensagens.append($("<div>").addClass("iaflt-vazio").text("Envie uma mensagem para começar — a IA já sabe do que se trata."));
        despejarAvisos(aba);
        return;
      }
      msgs.forEach(function (m) { $mensagens.append(renderMensagem(m, aba)); });
      rolarParaFim(true);
      carregarFeedbacks(idConversa, aba);
      despejarAvisos(aba);
    }).fail(function (xhr) {
      // 404: esta aba apontava para uma conversa vazia que a limpeza automática do backend apagou
      // (o painel cria uma conversa por contato ao abrir — quem só navega acumula conversas vazias).
      // Recria uma no mesmo escopo, transparente para o investigador.
      if (xhr.status !== 404 || idConversaIA !== idConversa || !aba) return;
      if (aba.idConversaIA !== idConversa) return;
      aba.idConversaIA = null;
      resolverConversa(aba.esc, function (novoId) {
        aba.idConversaIA = novoId;
        if (abaAtivaChave !== aba.chave) return; // o usuário saiu da aba durante a recriação
        idConversaIA = novoId;
        carregarMensagens(novoId, aba);
      });
    });
  }

  function resolverConversa(esc, callback) {
    var urlLista = "/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas";
    // poupa a conversa aberta agora no painel da limpeza de conversas vazias do backend
    if (idConversaIA) urlLista += "?conversa_ativa=" + encodeURIComponent(idConversaIA);
    $.getJSON(urlLista).done(function (lista) {
      var existente = lista.filter(function (c) {
        return c.tp_escopo === esc.tp && String(c.id_escopo || "") === String(esc.id || "");
      })[0];
      if (existente) { callback(existente.id_conversa_ia); return; }
      $.ajax({
        url: "/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({ titulo: esc.titulo, tp_escopo: esc.tp, id_escopo: esc.id }),
      })
        .done(function (resp) { callback(resp.id_conversa_ia); })
        .fail(function (xhr) {
          mostrarErroChat((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a conversa.");
        });
    }).fail(function () {
      mostrarErroChat("Erro ao carregar as conversas de IA.");
    });
  }

  // Ativa a aba do escopo dado — cria a aba se for a primeira vez que este escopo aparece nesta
  // página (ex.: primeira conversa aberta), ou só troca pra ela se já existir (sem perder as
  // demais, ao contrário do comportamento antigo que substituía o chat carregado).
  // Acompanhar turnos NÃO para com a troca de aba: cada aba mantém seu acompanhamento próprio
  // (processamentos[chave]); o que a troca faz é tirar/pôr o botão de cancelar e o "digitando".
  function ativarAba(esc) {
    var chave = chaveEscopo(esc);
    var aba = acharAba(chave);
    if (!aba) {
      aba = { chave: chave, esc: esc, idConversaIA: null, feedbacks: {}, avisosPendentes: [] };
      abas.push(aba);
    } else {
      aba.esc = esc; // título pode ter chegado depois (ex.: nome do RIF carregado assíncrono)
    }
    abaAtivaChave = chave;
    aba.pendenteRecarga = false;
    renderAbas();
    atualizarCabecalho(esc);
    $form.removeClass("hidden");

    // botão de cancelar e "digitando" pertencem só à aba ativa
    $painel.find(".iaflt-cancelar-btn").detach();
    var proc = processamentos[chave];
    if (proc) {
      $form.before(proc.$cancelar);
      $digitando.removeClass("hidden").text(proc.etapa || TEXTO_DIGITANDO);
    } else {
      $digitando.addClass("hidden").text(TEXTO_DIGITANDO);
    }
    $enviarBtn.prop("disabled", !!enviandoChaves[chave]);
    $input.prop("disabled", !!enviandoChaves[chave]);

    if (aba.idConversaIA) {
      idConversaIA = aba.idConversaIA;
      carregarMensagens(aba.idConversaIA, aba);
      return;
    }
    $mensagens.empty().append($("<div>").addClass("iaflt-vazio").text("Carregando…"));
    resolverConversa(esc, function (idConversa) {
      aba.idConversaIA = idConversa;
      if (abaAtivaChave !== chave) return; // o usuário trocou de aba enquanto isto resolvia
      idConversaIA = idConversa;
      carregarMensagens(idConversa, aba);
    });
  }

  function mostrarErroChat(msg, chaveEnvio) {
    var $bolha = $("<div>").addClass("chat-bolha chat-erro").text(msg);
    var chave = chaveEnvio || abaAtivaChave;
    var aba = chave ? acharAba(chave) : null;
    if (chaveEnvio && chaveEnvio === abaAtivaChave && aba && aba.ultimoEnvio && !processamentos[chave]) {
      $("<button>").addClass("chat-erro-retry").attr("type", "button")
        .text("Tentar de novo")
        .on("click", function () {
          $(this).closest(".chat-linha").remove();
          if (!aba.ultimoEnvio) return;
          enviarTexto(aba.ultimoEnvio.texto, { idConversa: aba.idConversaIA, repetir: true });
        })
        .appendTo($bolha);
    }
    $mensagens.append($("<div>").addClass("chat-linha").append($bolha));
    rolarParaFim(true);
  }

  // aviso de turno (erro/cancelamento) que chegou com a aba desativada: guarda e mostra depois
  function registrarAvisoPendente(chave, texto) {
    var aba = acharAba(chave);
    if (!aba) return;
    (aba.avisosPendentes = aba.avisosPendentes || []).push(texto);
    renderAbas();
  }

  function despejarAvisos(aba) {
    if (!aba || !aba.avisosPendentes || !aba.avisosPendentes.length) return;
    while (aba.avisosPendentes.length) mostrarErroChat(aba.avisosPendentes.shift());
    renderAbas();
  }

  var TEXTO_DIGITANDO = "A IA está respondendo…";

  function finalizarEnvio(chave) {
    delete enviandoChaves[chave];
    if (chave !== abaAtivaChave) return; // aba em segundo plano: nada na tela é dela
    $enviarBtn.prop("disabled", false);
    $input.prop("disabled", false);
    $digitando.addClass("hidden").text(TEXTO_DIGITANDO);
  }

  // Acompanhamento do turno: SSE (etapas aparecem na hora), com reserva em polling de 2,5 s se o
  // EventSource não abrir/desistir — mesmo desenho de js/ia.js. Vive em processamentos[chave],
  // então continua rodando enquanto o investigador conversa em outra aba.
  function acompanharProcessamento(chave, idProcessamento, idConversa) {
    var URL_BASE = "/api/investigacao/ia/processamentos/" + idProcessamento;
    var proc = processamentos[chave] = { fonte: null, poll: null, timerAbertura: null, encerrado: false, etapa: "" };

    proc.$cancelar = $("<button>").addClass("iaflt-cancelar-btn").attr("type", "button").text("Cancelar resposta")
      .on("click", function () {
        proc.$cancelar.prop("disabled", true).text("Cancelando…");
        $.ajax({ url: URL_BASE + "/cancelar", method: "POST" });
        // o acompanhamento vê o status 'cancelado' e encerra
      });
    if (chave === abaAtivaChave) $form.before(proc.$cancelar);

    function encerrar() {
      proc.encerrado = true;
      if (proc.timerAbertura) { clearTimeout(proc.timerAbertura); proc.timerAbertura = null; }
      if (proc.fonte) { proc.fonte.close(); proc.fonte = null; }
      if (proc.poll) { clearInterval(proc.poll); proc.poll = null; }
      proc.$cancelar.remove();
      delete processamentos[chave];
      finalizarEnvio(chave);
    }

    function aplicarEstado(p) {
      if (proc.encerrado || !p) return;
      if (p.cd_status === "concluido") {
        encerrar();
        if (idConversaIA === idConversa) {
          carregarMensagens(idConversa, acharAba(chave));
        } else {
          var aba = acharAba(chave);
          if (aba) { aba.pendenteRecarga = true; renderAbas(); } // bolinha na aba; recarrega ao voltar
        }
      } else if (p.cd_status === "cancelado") {
        encerrar();
        if (chave === abaAtivaChave) mostrarErroChat("Resposta cancelada.");
        else registrarAvisoPendente(chave, "Resposta cancelada.");
      } else if (p.cd_status === "erro") {
        encerrar();
        var txt = p.tx_erro || "Erro ao gerar a resposta.";
        if (chave === abaAtivaChave) mostrarErroChat(txt, chave);
        else registrarAvisoPendente(chave, txt);
      } else {
        proc.etapa = p.tx_etapa_atual || "";
        if (chave === abaAtivaChave) $digitando.removeClass("hidden").text(proc.etapa || TEXTO_DIGITANDO);
      }
    }

    function iniciarPolling() {
      if (proc.encerrado || proc.poll) return;
      proc.poll = setInterval(function () {
        $.getJSON(URL_BASE).done(aplicarEstado);
        // falha transitória de rede: mantém o polling na próxima batida
      }, 2500);
    }

    function cairParaPolling() {
      if (proc.encerrado) return;
      if (proc.timerAbertura) { clearTimeout(proc.timerAbertura); proc.timerAbertura = null; }
      if (proc.fonte) { proc.fonte.close(); proc.fonte = null; }
      iniciarPolling();
    }

    function iniciarSse() {
      if (!window.EventSource) return false;
      try {
        proc.fonte = new EventSource(URL_BASE + "/eventos");
      } catch (e) {
        return false;
      }
      // se nada chegar na abertura (proxy que bufferiza o stream), o polling assume
      proc.timerAbertura = setTimeout(cairParaPolling, 8000);
      proc.fonte.addEventListener("estado", function (ev) {
        if (proc.timerAbertura) { clearTimeout(proc.timerAbertura); proc.timerAbertura = null; }
        var p = null;
        try { p = JSON.parse(ev.data); } catch (e) { return; } // evento truncado: o próximo corrige
        aplicarEstado(p);
      });
      proc.fonte.addEventListener("fim", cairParaPolling);
      proc.fonte.onerror = function () {
        if (proc.fonte && proc.fonte.readyState === EventSource.CLOSED) cairParaPolling();
      };
      return true;
    }

    if (!iniciarSse()) iniciarPolling();
  }

  function estaEnviando(chave) {
    return !!enviandoChaves[chave] || !!processamentos[chave];
  }

  function enviarTexto(texto, opts) {
    opts = opts || {};
    texto = $.trim(texto || "");
    var chave = abaAtivaChave;
    var aba = chave ? acharAba(chave) : null;
    var idConversa = opts.idConversa || (aba && aba.idConversaIA) || idConversaIA;
    if (!texto || !idConversa || !chave || estaEnviando(chave)) return;
    // gesto do usuário: momento certo de pedir a permissão de notificação (js/utils/avisos_ia.js)
    if (window.AvisosIA && !opts.repetir) window.AvisosIA.pedirPermissao();
    enviandoChaves[chave] = true;
    if (aba) aba.ultimoEnvio = { texto: texto };
    $mensagens.find(".iaflt-vazio").remove();
    if (!opts.repetir) {
      $mensagens.append(renderMensagem({ tp_papel: "user", tx_conteudo: texto }));
    }
    rolarParaFim(true);
    $input.val("").css("height", "auto");
    atualizarContador();
    $enviarBtn.prop("disabled", true);
    $input.prop("disabled", true);
    $digitando.removeClass("hidden").text(TEXTO_DIGITANDO);

    $.ajax({
      url: "/api/investigacao/ia/conversas/" + idConversa + "/mensagens",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({
        texto: texto, incluir_conteudo: false, exibir_prompt: false,
        // nas telas de grafo vai junto o que dá para filtrar e o que já está filtrado agora,
        // para a IA poder propor um recorte em cima dos campos que existem de verdade
        contexto_tela: contextoTela(),
      }),
    })
      .done(function (resp) {
        if (resp && resp.modo === "async") {
          acompanharProcessamento(chave, resp.id_processamento, idConversa);
          return;
        }
        finalizarEnvio(chave);
        if (idConversaIA === idConversa) carregarMensagens(idConversa, aba);
      })
      .fail(function (xhr) {
        mostrarErroChat((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao falar com a IA.", chave);
        finalizarEnvio(chave);
      });
  }

  $form.on("submit", function (e) {
    e.preventDefault();
    enviarTexto($input.val());
  });

  function atualizarContador() {
    if (!$contador.length) return;
    var len = ($input.val() || "").length;
    $contador
      .toggleClass("hidden", len < MAX_CHARS * 0.8)
      .toggleClass("limite", len >= MAX_CHARS * 0.9)
      .text(len + " / " + MAX_CHARS);
  }

  $input.on("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $form.trigger("submit"); }
  });
  $input.on("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 90) + "px";
    atualizarContador();
  });

  // ---------- abrir/fechar painel + reagir à troca de conversa em whatsapp.js ----------

  function abrirPainel() {
    painelAberto = true;
    $painel.addClass("aberto").attr("aria-hidden", "false");
    var esc = escopoAtual();
    var abaAtual = acharAba(abaAtivaChave);
    // !idConversaIA: depois de um F5 a aba ativa volta do sessionStorage só com o escopo, sem
    // conversa carregada — mesmo escopo da página não basta para pular o ativarAba, senão o
    // painel abre todo vazio (nem mensagens, nem o formulário, que só o ativarAba revela)
    if (!abaAtual || !mesmoEscopo(abaAtual.esc, esc) || !abaAtual.idConversaIA) ativarAba(esc);
    setTimeout(function () { $input.focus(); }, 50); // digitar direto ao abrir
  }

  function fecharPainel() {
    painelAberto = false;
    $painel.removeClass("aberto").attr("aria-hidden", "true");
    $botao.focus(); // devolve o foco a quem abriu (teclado não fica órfão)
  }

  // Maximizar: leva para a tela cheia (ia.html) mantendo o contexto — a conversa ativa vai
  // em ?conversa= para reabrir com o mesmo histórico; sem conversa resolvida ainda, o escopo
  // atual vai em ?escopo_tipo=/?escopo_id=/?escopo_titulo= (ver js/ia.js, abrirOuCriarConversaComEscopo).
  function urlIaCheia() {
    var url = "ia.html?inquerito=" + encodeURIComponent(idInquerito);
    if (idConversaIA) return url + "&conversa=" + encodeURIComponent(idConversaIA);
    try {
      var esc = escopoAtual();
      if (esc && esc.tp && esc.id != null) {
        url += "&escopo_tipo=" + encodeURIComponent(esc.tp) +
          "&escopo_id=" + encodeURIComponent(esc.id) +
          "&escopo_titulo=" + encodeURIComponent(esc.titulo || "");
      }
    } catch (e) { /* sem escopo — abre só com o inquérito */ }
    return url;
  }

  // Enquanto o painel está aberto na conversa X, o aviso global não repete em toast o que já
  // está aparecendo aqui dentro — mas com a aba em segundo plano a notificação continua saindo.
  if (window.AvisosIA) {
    window.AvisosIA.silenciarSe(function () {
      return (painelAberto && idConversaIA) ? "chat_inquerito:" + idConversaIA : null;
    });
  }

  $botao.on("click", function () { painelAberto ? fecharPainel() : abrirPainel(); });
  $("#iaflt-fechar").on("click", fecharPainel);
  $("#iaflt-maximizar").on("click", function () { window.open(urlIaCheia(), "_blank", "noopener"); });
  $(document).on("keydown", function (e) {
    if (e.key !== "Escape" || !painelAberto) return;
    // um diálogo (Dialogo) aberto na página tem prioridade sobre o painel
    if (document.querySelector(".modal-overlay")) return;
    fecharPainel();
  });

  // disparado por js/whatsapp.js (abrirConversa) toda vez que o investigador seleciona um contato
  // — só existe no modo "mensageiro" (whatsapp.js não roda em rif.html/simba.html)
  $(document).on("analyser:conversa-aberta", function (evt, dados) {
    if (modo !== "mensageiro" || dados.fonte !== fonte) return; // defensivo
    idConversaAberta = dados.idConversa;
    nomeConversaAberta = dados.nome;

    // Chegando por um achado da IA (?msg=ID), o link 🔎 não carrega o aparelho e whatsapp.js
    // adota o aparelho de onde veio a mensagem e o acrescenta à URL. O escopo "Geral" precisa
    // acompanhar (este script capturou a URL antes disso): sem a sincronia, a aba "Geral" passa
    // a procurar uma conversa "todo o inquérito" (id_escopo null) que não é a que já existia —
    // o histórico só reaparecia num F5, quando a URL já vinha com ?extracao=.
    if (dados.idExtracao != null && String(dados.idExtracao) !== String(idExtracao || "")) {
      idExtracao = String(dados.idExtracao);
      var geral = null;
      abas.forEach(function (a) { if (a.esc.tp.indexOf("conversa_") !== 0) geral = a; });
      if (geral) {
        var chaveAntiga = geral.chave;
        geral.esc = escopoGeral();
        geral.chave = chaveEscopo(geral.esc);
        if (abaAtivaChave === chaveAntiga) {
          abaAtivaChave = geral.chave;
          if (painelAberto) atualizarCabecalho(geral.esc);
        }
        renderAbas();
      }
    }

    if (painelAberto) {
      var esc = escopoAtual();
      var abaAtual = acharAba(abaAtivaChave);
      // mesmo motivo do abrirPainel: aba restored do F5 sem conversa carregada precisa do ativarAba
      if (!abaAtual || !mesmoEscopo(abaAtual.esc, esc) || !abaAtual.idConversaIA) ativarAba(esc);
    }
  });

  // ---------- disponibilidade da IA (gestão de delegacias) ----------
  // A mesma checagem que o backend já faz em toda rota .../ia/... (rotas/ia.py, _exigir_ia_ativa)
  // decide se o botão aparece: 403 = IA desativada para a delegacia do usuário logado, e o botão
  // simplesmente não é exibido — sem duplicar aqui a lista de delegacias/regra de negócio. Nos modos
  // de documento (ver DOCUMENTOS, no topo) aproveita a mesma chamada (que já ocorre) para
  // pré-carregar o rótulo do RIF/extrato/IPEI/documento.
  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas")
    .done(function () { carregarTituloDocumento(function () { $botao.removeClass("hidden"); }); })
    .fail(function () { /* IA desativada ou sem acesso — não mostra o botão */ });
})();
