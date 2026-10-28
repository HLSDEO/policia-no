/* Auditoria de pensamento da IA: o bloco recolhido que fica na bolha da resposta.
 *
 * Mostra, rodada a rodada, o raciocínio que o modelo devolveu e as ferramentas que ele chamou
 * naquela rodada. Vem pronto do backend na coluna TX_RACIOCINIO da mensagem (montada em
 * funcoes/ia_provedores.py, AuditoriaIA.trilha) — aqui só se desenha.
 *
 * Nem todo provedor devolve o raciocínio: a OpenAI pública cobra os tokens de reasoning e não
 * expõe o texto, enquanto o gateway com certificado (LiteLLM), o DeepSeek e os modelos locais
 * devolvem. Por isso a trilha nunca é só o pensamento — a lista de ferramentas aparece em
 * qualquer provedor, e é ela que garante conteúdo no painel.
 *
 * Usado pelo chat do inquérito (js/ia.js).
 */
window.IARaciocinio = (function () {
  "use strict";

  // O conteúdo aqui é texto do MODELO sobre dados do aparelho: tudo entra por .text(), nunca por
  // .html(). O markdown das respostas não se aplica — raciocínio é rascunho, não documento.

  function normalizar(trilha) {
    if (!trilha) return [];
    if (typeof trilha === "string") {
      try { trilha = JSON.parse(trilha); } catch (e) { return []; }
    }
    if (!Array.isArray(trilha)) return [];
    return trilha.filter(function (r) {
      return r && (r.raciocinio || (r.ferramentas && r.ferramentas.length));
    });
  }

  function plural(n, singular, pluralForma) {
    return n + " " + (n === 1 ? singular : pluralForma);
  }

  function argumentosEmTexto(argumentos) {
    if (!argumentos || typeof argumentos !== "object") return "";
    var chaves = Object.keys(argumentos);
    if (!chaves.length) return "";
    try {
      return JSON.stringify(argumentos);
    } catch (e) {
      return chaves.join(", ");
    }
  }

  function renderFerramentas($destino, ferramentas) {
    if (!ferramentas || !ferramentas.length) return;
    var $ul = $("<ul>").addClass("chat-raciocinio-tools");
    ferramentas.forEach(function (f) {
      var $li = $("<li>");
      $("<code>").text(f.ferramenta || "?").appendTo($li);
      var args = argumentosEmTexto(f.argumentos);
      if (args) $("<span>").addClass("chat-raciocinio-args").text(args).appendTo($li);
      $li.appendTo($ul);
    });
    $ul.appendTo($destino);
  }

  function renderRodada(r, indice, total) {
    var $bloco = $("<div>").addClass("chat-raciocinio-rodada");
    // com uma rodada só o rótulo "Etapa 1 de 1" é ruído; some
    if (total > 1) {
      $("<div>").addClass("chat-raciocinio-rotulo")
        .text("Etapa " + (r.rodada || indice + 1) + " de " + total).appendTo($bloco);
    }
    if (r.raciocinio) {
      $("<div>").addClass("chat-raciocinio-texto").text(r.raciocinio).appendTo($bloco);
    }
    renderFerramentas($bloco, r.ferramentas);
    return $bloco;
  }

  function resumo(rodadas) {
    var comPensamento = rodadas.some(function (r) { return !!r.raciocinio; });
    var qtFerramentas = rodadas.reduce(function (soma, r) {
      return soma + ((r.ferramentas && r.ferramentas.length) || 0);
    }, 0);
    var partes = [];
    if (comPensamento) partes.push(plural(rodadas.length, "etapa", "etapas"));
    if (qtFerramentas) partes.push(plural(qtFerramentas, "consulta", "consultas"));
    var titulo = comPensamento ? "Raciocínio da IA" : "Consultas feitas pela IA";
    return titulo + (partes.length ? " · " + partes.join(", ") : "");
  }

  /* Devolve o <details> recolhido pronto para entrar na bolha, ou null quando o turno não tem
   * nada a mostrar (resposta direta, sem ferramenta e sem raciocínio — e toda mensagem anterior
   * à migração 0013, que tem tx_raciocinio nulo). */
  function render(trilha) {
    var rodadas = normalizar(trilha);
    if (!rodadas.length) return null;

    var $det = $("<details>").addClass("chat-raciocinio");
    var $sum = $("<summary>").appendTo($det);
    $("<span>").addClass("chat-raciocinio-seta").text("▸").appendTo($sum);
    $("<span>").text(resumo(rodadas)).appendTo($sum);

    var $corpo = $("<div>").addClass("chat-raciocinio-corpo").appendTo($det);
    rodadas.forEach(function (r, i) {
      $corpo.append(renderRodada(r, i, rodadas.length));
    });
    return $det;
  }

  return { render: render };
})();
