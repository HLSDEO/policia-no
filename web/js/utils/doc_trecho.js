/* Trecho citado pela IA -> realce no texto do documento, e o link que abre a tela do documento.
 *
 * O chat de inquérito abre a mesma tela (web/documento.html) e este módulo é a parte
 * compartilhada: achar o trecho, casá-lo no texto e montar a URL.
 *
 * A dificuldade toda está em casar a citação com o papel. O trecho não viaja no marcador: sai do
 * próprio parágrafo clicado, pegando o que estiver entre aspas — o formato que os prompts exigem
 * da citação (funcoes/ia_utils.py). E o texto extraído quebra linha onde o PDF
 * quebrava, então a citação quase nunca bate caractere a caractere: casa palavra por palavra.
 *
 * Usado por js/documento.js (o realce), js/ia.js e, via linkarTexto, pelo resumo noturno do
 * inquérito (js/investigacao.js, js/inquerito_orbit.js), que é texto puro do banco em vez de
 * markdown de chat.
 */
window.DocTrecho = (function () {
  "use strict";

  var MAX_REALCES = 300;      // teto defensivo: trecho curto e genérico casaria o documento todo
  var MIN_FRAGMENTO = 12;     // pedaço menor que isto realça ruído, não a citação
  var MAX_TRECHO_URL = 400;   // o trecho viaja na query string; acima disto a URL fica impraticável

  // escapa também a aspa dupla: o mesmo escape serve ao texto do documento e a atributos
  function escaparHtml(texto) {
    return String(texto)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function escaparRegex(texto) {
    return String(texto).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /* O trecho sai do bloco clicado (item de lista, célula, parágrafo): é o que estiver entre aspas,
   * que é como o prompt manda a IA citar. Sem aspas por perto, o documento abre no começo. */
  function doContexto($link) {
    var $bloco = $link.closest("li, td, th, .chat-md-titulo, .chat-md > div");
    if (!$bloco.length) $bloco = $link.closest(".chat-md, .chat-bolha");
    var m = ($bloco.text() || "").match(/["“”«»]([^"“”«»]{8,600})["“”«»]/);
    return m ? m[1] : "";
  }

  /* Reticências marcam onde a IA cortou o meio da citação — cada pedaço é procurado sozinho.
   * Pedaço curto demais é descartado: casaria em qualquer lugar do documento. */
  function fragmentos(trecho) {
    var pedacos = String(trecho || "").split(/\s*(?:…|\.\.\.|\[\.\.\.\])\s*/);
    var uteis = pedacos.filter(function (p) { return p.trim().length >= MIN_FRAGMENTO; });
    if (uteis.length) return uteis;
    var inteiro = String(trecho || "").trim();
    return inteiro.length >= 8 ? [inteiro] : [];
  }

  /* O texto extraído quebra linha onde o PDF quebrava, então a citação da IA quase nunca bate
   * caractere a caractere: casa palavra por palavra, com qualquer espaço/quebra entre elas.
   * Pontuação, caixa e acento são ignorados dos dois lados (1 caractere entra, 1 sai, então o
   * índice no texto normalizado continua valendo no original — mesmo truque de dobrar()):
   * a IA costuma trocar vírgula/ponto ou citar acentuado o que o OCR trouxe sem acento, e exigir
   * igualdade nisso transformava paráfrase mínima em "Trecho não localizado" mesmo com o texto
   * visível no Ctrl+F. */
  var RE_PONTUACAO = /[.,;:!?()\[\]{}"“”«»'’`´^~|/\\@#$%*+=<>–—\-…]/g;
  var MIN_PALAVRAS_PARCIAL = 6; // prefixo menor que isto realça ruído, não a citação

  function normalizar(texto) {
    return dobrar(texto).toLowerCase().replace(RE_PONTUACAO, " ");
  }

  function palavrasNorm(fragmento) {
    return normalizar(fragmento).trim().split(/\s+/).filter(function (w) { return !!w; });
  }

  function regexFragmento(fragmento) {
    var palavras = palavrasNorm(fragmento).map(escaparRegex);
    if (!palavras.length) return null;
    return new RegExp(palavras.join("\\s+"), "gi");
  }

  /* Tenta o fragmento inteiro; se não casar, recua pelo fim até achar o maior prefixo que casa
   * (mínimo MIN_PALAVRAS_PARCIAL). A IA raramente erra o começo da transcrição — erra o fim
   * (troca palavra, resume, corta) — e sem este recuo um único vocábulo divergente no fim
   * anulava o realce inteiro. Devolve os intervalos achados (podem ser de um prefixo). */
  function casarComRecuo(alvoNorm, palavras) {
    var copia = palavras.slice();
    while (copia.length >= MIN_PALAVRAS_PARCIAL) {
      var re = new RegExp(copia.map(escaparRegex).join("\\s+"), "gi");
      var achados = [];
      var m;
      re.lastIndex = 0;
      while ((m = re.exec(alvoNorm)) !== null && achados.length < MAX_REALCES) {
        if (!m[0].length) { re.lastIndex++; continue; }
        achados.push([m.index, m.index + m[0].length]);
      }
      if (achados.length) return achados;
      copia.pop();
    }
    return [];
  }

  /* Dobra de acento, caractere a caractere. O texto extraído de PDF digitalizado com frequência
   * perde o acento ("Sao Paulo" onde o papel diz "São Paulo"), enquanto a IA cita acentuado — sem
   * isto a citação simplesmente não casaria nesses documentos, que são justamente os piores de
   * ler à mão.
   *
   * Um caractere entra, um caractere sai: todo o casamento depende de o índice no texto dobrado
   * valer no texto original, então uma decomposição que virasse duas letras é descartada e o
   * original fica no lugar. Mesmo raciocínio (e mesmo comentário) de _montar_tabela_dobra em
   * api/funcoes/anonimizacao.py. */
  function dobrar(texto) {
    texto = String(texto || "");
    if (!"".normalize) return texto;
    var saida = "";
    for (var i = 0; i < texto.length; i++) {
      var c = texto.charAt(i);
      var base = c.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      saida += base.length === 1 ? base : c;
    }
    return saida;
  }

  /* Os intervalos saem do texto NORMALIZADO, mas valem no original: normalizar() preserva o
   * índice de cada caractere (1 entra, 1 sai — ver dobrar() acima). Por isso o realce continua
   * mostrando o texto do papel, com acento e pontuação, mesmo tendo casado sem. */
  function intervalos(texto, pedacos, permitirParcial) {
    var achados = [];
    var alvo = normalizar(texto);
    pedacos.forEach(function (f) {
      var palavras = palavrasNorm(f);
      if (!palavras.length) return;
      var m;
      if (permitirParcial === false) {
        var re = regexFragmento(f);
        if (!re) return;
        m = [];
        var mm;
        re.lastIndex = 0;
        while ((mm = re.exec(alvo)) !== null && m.length < MAX_REALCES) {
          if (!mm[0].length) { re.lastIndex++; continue; }
          m.push([mm.index, mm.index + mm[0].length]);
        }
      } else {
        m = casarComRecuo(alvo, palavras);
      }
      for (var i = 0; i < m.length && achados.length < MAX_REALCES; i++) achados.push(m[i]);
    });
    achados.sort(function (a, b) { return a[0] - b[0]; });
    // fragmentos vizinhos podem casar sobrepostos; junta para não abrir <mark> dentro de <mark>
    var juntos = [];
    achados.forEach(function (iv) {
      var ultimo = juntos[juntos.length - 1];
      if (ultimo && iv[0] <= ultimo[1]) ultimo[1] = Math.max(ultimo[1], iv[1]);
      else juntos.push(iv.slice());
    });
    return juntos;
  }

  function aplicar(texto, ivs, classe) {
    if (!ivs.length) return { html: escaparHtml(texto), qtd: 0 };
    var html = "";
    var pos = 0;
    ivs.forEach(function (iv) {
      html += escaparHtml(texto.slice(pos, iv[0]));
      html += '<mark class="' + (classe || "doc-trecho") + '">' + escaparHtml(texto.slice(iv[0], iv[1])) + "</mark>";
      pos = iv[1];
    });
    html += escaparHtml(texto.slice(pos));
    return { html: html, qtd: ivs.length };
  }

  /* Texto escapado para HTML com o trecho citado dentro de <mark>. Devolve também quantos realces
   * saíram, porque a tela precisa dizer ao investigador quando não achou o trecho — abrir no
   * começo sem explicar parece a tela ter ignorado o clique. */
  function realcar(texto, trecho, classe) {
    texto = String(texto || "");
    return aplicar(texto, trecho ? intervalos(texto, fragmentos(trecho)) : [], classe);
  }

  /* Realce de uma busca digitada na tela. Diferente da citação, aqui o termo vale como está: não
   * se quebra em reticências (ninguém digita a elisão da IA) e não há mínimo de tamanho — quem
   * procura "CPF" quer ver "CPF", e descartar o termo por ser curto pareceria busca quebrada. */
  function realcarTermo(texto, termo, classe) {
    texto = String(texto || "");
    termo = String(termo || "").trim();
    return aplicar(texto, termo ? intervalos(texto, [termo], false) : [], classe);
  }

  /* Só a contagem de ocorrências, sem montar o HTML. É o que permite à tela de documento saber
   * QUAIS páginas têm ocorrência antes de desenhar qualquer uma — sem isso, filtrar as folhas de
   * um PDF de centenas de páginas custaria o mesmo que redesenhar o documento todo. */
  function contarTermo(texto, termo) {
    termo = String(termo || "").trim();
    return termo ? intervalos(String(texto || ""), [termo], false).length : 0;
  }

  function contarTrecho(texto, trecho) {
    return trecho ? intervalos(String(texto || ""), fragmentos(trecho)).length : 0;
  }

  /* URL da tela do documento. O `inquerito` diz de qual inquérito o documento é — é o que
   * escolhe a rota de API do outro lado. O corte em MAX_TRECHO_URL
   * nunca pode parar no meio de uma palavra: o fragmento final parcial jamais casaria no texto e
   * derrubaria o casamento do trecho inteiro (ver regexFragmento/intervalos abaixo). */
  function urlDocumento(o) {
    var qs = [];
    if (o.inquerito) qs.push("inquerito=" + encodeURIComponent(o.inquerito));
    qs.push("doc=" + encodeURIComponent(o.doc));
    if (o.pagina) qs.push("pagina=" + encodeURIComponent(o.pagina));
    if (o.trecho) {
      var t = String(o.trecho).slice(0, MAX_TRECHO_URL).replace(/\s+\S*$/, "");
      if (!t) t = String(o.trecho).slice(0, MAX_TRECHO_URL);
      qs.push("trecho=" + encodeURIComponent(t));
    }
    return "documento.html?" + qs.join("&");
  }


  // ---------- a citação do chat vira link ----------

  /* [doc:ID], [doc:ID:PÁGINA] e [doc:ID:INÍCIO-FIM] — o marcador que os prompts pedem depois de
   * cada citação. A forma de duas partes é a antiga (e continua chegando nas conversas já
   * gravadas); a de três traz a página lida do marcador [[página N]] do texto extraído, e a IA às
   * vezes cita uma faixa ("4-5") quando o trecho abrange páginas — o link abre na primeira. */
  var RE_MARCADOR_DOC = /\[doc:(\d+)(?::(\d+(?:-\d+)?))?\]/g;

  /* A pastilha ℹ️ da cadeia de custódia (js/utils/evidencia_info.js), colada na de link. Sem o
   * módulo na página a citação segue funcionando sozinha — a pastilha extra só desaparece. */
  function pastilhaInfo(fonte, id) {
    return window.EvidenciaInfo ? window.EvidenciaInfo.pastilhaHtml(fonte, id) : "";
  }

  /* A pastilha guarda o inquérito em data-*, e é isso que permite um handler de clique só:
   * o documento mora no inquérito. O href fica montado sem o trecho de propósito — é o que
   * abrir-em-nova-aba usa, e ali não há parágrafo de onde tirar a citação. */
  function pastilha(escopo, idDoc, pagina, rotulo, classe) {
    // faixa ("[doc:ID:4-5]", citação de trecho que corre páginas) abre na página inicial
    var pagUrl = pagina ? String(pagina).split("-")[0] : pagina;
    var url = urlDocumento({ inquerito: escopo.inquerito, doc: idDoc, pagina: pagUrl });
    var titulo = pagina
      ? "Abrir o documento na página " + pagina + ", no trecho citado"
      : "Abrir o documento no trecho citado";
    return '<a class="' + (classe || "chat-ref") + ' chat-ref-doc" href="' + escaparHtml(url) +
      '" data-doc="' + escaparHtml(idDoc) + '"' +
      (pagUrl ? ' data-pagina="' + escaparHtml(pagUrl) + '"' : "") +
      (escopo.inquerito ? ' data-inquerito="' + escaparHtml(escopo.inquerito) + '"' : "") +
      ' title="' + escaparHtml(titulo) + '">' + (rotulo || "📄") + "</a>" +
      pastilhaInfo("doc", idDoc);
  }

  /* Troca os marcadores [doc:…] de um HTML já renderizado pelas pastilhas clicáveis. Usada pelo
   * chat de inquérito (js/ia.js, js/utils/ia_flutuante.js). */
  function linkarMarcadores(html, escopo) {
    return String(html || "").replace(RE_MARCADOR_DOC, function (_todo, idDoc, pagina) {
      return pastilha(escopo, idDoc, pagina, "📄", "chat-ref");
    });
  }

  /* Texto CRU (não passou por markdown) com marcadores da IA -> HTML com as citações clicáveis.
   * É o caso do resumo noturno (js/investigacao.js, js/inquerito_orbit.js): ele não é chat, sai do
   * banco como texto puro e vai para um <div> com white-space: pre-wrap. Escapa tudo primeiro — o
   * resto é conteúdo da IA e nunca pode virar HTML — e só então troca os marcadores, que o escape
   * deixa intactos. Além de [doc:...], resolve o [msg:wpp|sms|msg:ID] que a síntese herda das
   * parciais de celular.
   */
  function linkarTexto(texto, escopo) {
    var esc = escopo || {};
    return escaparHtml(texto || "")
      .replace(RE_MARCADOR_DOC, function (_todo, idDoc, pagina) {
        // rótulo com o número, ao contrário do chat: aqui o mesmo parágrafo cita várias peças
        // ("[doc:21] referenciado mas não fornecido", "[doc:22] documenta...") e um 📄 solto em
        // cada uma não diria mais qual é qual
        return pastilha(esc, idDoc, pagina, "📄 " + idDoc, "chat-ref");
      })
      .replace(/\[msg:(wpp|sms|msg):(\d+)\]/g, function (todo, fonte, id) {
        // sem inquérito não há para onde ir (a tela da conversa exige ?inquerito=): fica o texto
        // cru, que é honesto
        if (!esc.inquerito || !window.MapaTelas) return todo;
        var href = MapaTelas.paginaConversa(fonte) + "?inquerito=" + encodeURIComponent(esc.inquerito) +
          "&fonte=" + fonte + "&msg=" + encodeURIComponent(id);
        return '<a class="chat-ref" href="' + escaparHtml(href) +
          '" title="Abrir a mensagem original">🔎</a>' + pastilhaInfo(fonte, id);
      });
  }

  /* Um handler só, para toda pastilha de documento da aplicação. O trecho não está no marcador:
   * sai do parágrafo clicado, e por isso só pode ser resolvido agora, no clique. */
  $(document).on("click", ".chat-ref-doc", function (e) {
    var $a = $(this);
    // ctrl/cmd/meio: deixa o navegador abrir em outra aba pelo href (sem o trecho, que é o que
    // dá para prometer aí) em vez de sequestrar o clique
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.which === 2) return;
    e.preventDefault();
    window.location.href = urlDocumento({
      inquerito: $a.attr("data-inquerito"),
      doc: $a.attr("data-doc"),
      pagina: $a.attr("data-pagina"),
      trecho: doContexto($a)
    });
  });

  return {
    escaparHtml: escaparHtml,
    doContexto: doContexto,
    realcar: realcar,
    realcarTermo: realcarTermo,
    contarTermo: contarTermo,
    contarTrecho: contarTrecho,
    pastilha: pastilha,
    linkarMarcadores: linkarMarcadores,
    linkarTexto: linkarTexto,
    url: urlDocumento
  };
})();
