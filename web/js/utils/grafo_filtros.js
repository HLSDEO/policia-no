/* Filtros do grafo expostos ao chat de IA — window.GrafoFiltros.
 *
 * As telas de grafo (grafo_rif.html, grafo_simba.html, mesa_inquerito.html) têm cada uma o seu
 * painel de filtros, escrito à mão em HTML, com ids próprios e semântica própria. O painel
 * flutuante de IA (js/utils/ia_flutuante.js) precisava de duas coisas para poder FILTRAR o grafo
 * a pedido do investigador ("me mostra as principais relações do fulano"):
 *
 *   1. saber o que existe para filtrar naquela tela (quais campos, de que tipo, com que opções
 *      válidas — a lista de bancos, por exemplo, só existe depois que os dados carregam);
 *   2. um jeito único de ESCREVER esses filtros e remontar o grafo, sem que o chat conheça o id
 *      de cada input de cada página.
 *
 * Este módulo é esse contrato. Cada página de grafo se registra uma vez, descrevendo os campos
 * dela; o chat lê `esquema()` para montar o bloco de contexto que vai no prompt e chama
 * `aplicar()` quando o investigador clica no filtro que a IA propôs. Nada aqui conhece RIF,
 * SIMBA ou mesa — e nenhuma das três precisa conhecer o chat.
 *
 * Registro (na página do grafo, depois de montar o painel de filtros):
 *
 *   GrafoFiltros.registrar({
 *     pagina: "grafo_rif",
 *     titulo: "Grafo do RIF",
 *     descricao: "Fluxo financeiro do RIF: titulares no meio, quem creditou/debitou em volta.",
 *     campos: [
 *       { nome: "busca", id: "f-grafo-busca", tipo: "texto", rotulo: "Busca",
 *         descricao: "Nome ou CPF/CNPJ; deixa em foco só quem casa e os vizinhos diretos." },
 *       { nome: "banco", id: "f-grafo-banco", tipo: "select", rotulo: "Banco" },
 *       ...
 *     ],
 *     aoAplicar: aplicarGrafo,   // remonta o grafo com os campos já escritos
 *   });
 *
 * Tipos de campo: "texto", "select", "data" (yyyy-mm-dd), "moeda" (pt-BR mascarado no input),
 * "numero" e "bool" (checkbox). Um campo pode ser VIRTUAL — sem `id`, com `ler`/`escrever`
 * próprios — para o que não mora num input (ex.: os tipos de nó desligados na legenda da mesa).
 */
(function () {
  "use strict";

  var cfg = null;

  // ---------------------------------------------------------------- helpers

  function norm(s) {
    return String(s == null ? "" : s)
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .trim().toLowerCase();
  }

  function $el(campo) {
    return campo.id ? $("#" + campo.id) : $();
  }

  // "100.000,00", "100000.00", "100000" e 100000 → 100000. Mesma regra de GrafoFluxo.util
  // (ponto sozinho em grupos de 3 é milhar), reimplementada aqui porque a mesa de inquérito não
  // carrega o motor de fluxo.
  function parseValor(texto) {
    if (typeof texto === "number") return isFinite(texto) ? texto : NaN;
    var s = String(texto == null ? "" : texto).trim().replace(/^R\$\s*/i, "").replace(/\s/g, "");
    if (!s) return NaN;
    if (s.indexOf(",") !== -1) s = s.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    var n = Number(s);
    return isFinite(n) ? n : NaN;
  }

  function formatarValor(n) {
    return Number(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Aceita o que a IA mandar: "2025-01-31", "31/01/2025" ou "31-01-2025" → "2025-01-31".
  function normalizarData(v) {
    var s = String(v == null ? "" : v).trim();
    if (!s) return "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    var m = s.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/);   // dd/mm/aaaa (pt-BR)
    if (m) return m[3] + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[1]).slice(-2);
    return null; // não reconhecida — vira "ignorado", em vez de escrever lixo no input
  }

  function opcoesDe(campo) {
    if (typeof campo.opcoes === "function") return campo.opcoes() || [];
    var $sel = $el(campo);
    if (!$sel.is("select")) return [];
    return $sel.find("option").map(function () {
      return { valor: this.value, rotulo: $.trim(this.textContent || "") };
    }).get().filter(function (o) { return o.valor !== ""; });
  }

  // A IA escolhe pelo VALOR quando conhece (o esquema manda os dois), mas o normal é ela mandar
  // o rótulo humano ("BANCO ITAÚ") — então casa por valor, por rótulo exato e, por último, por
  // pedaço do rótulo. Sem casar nenhum, devolve null: o filtro é reportado como ignorado em vez
  // de silenciosamente aplicar "todos".
  function resolverOpcao(campo, valor) {
    var alvo = norm(valor);
    if (!alvo) return "";
    var opcoes = opcoesDe(campo);
    var achou = null;
    opcoes.some(function (o) { if (norm(o.valor) === alvo) { achou = o.valor; return true; } return false; });
    if (achou !== null) return achou;
    opcoes.some(function (o) { if (norm(o.rotulo) === alvo) { achou = o.valor; return true; } return false; });
    if (achou !== null) return achou;
    var parciais = opcoes.filter(function (o) { return norm(o.rotulo).indexOf(alvo) !== -1; });
    return parciais.length === 1 ? parciais[0].valor : null;
  }

  function padraoDe(campo) {
    if (campo.padrao !== undefined) return campo.padrao;
    if (campo.tipo === "bool") return false;
    if (campo.tipo === "lista") return [];
    return "";
  }

  // ---------------------------------------------------------------- ler

  function lerCampo(campo) {
    if (typeof campo.ler === "function") return campo.ler();
    var $e = $el(campo);
    if (!$e.length) return padraoDe(campo);
    if (campo.tipo === "bool") return $e.is(":checked");
    return $e.val() || "";
  }

  function ler() {
    var out = {};
    (cfg ? cfg.campos : []).forEach(function (c) { out[c.nome] = lerCampo(c); });
    return out;
  }

  function ehPadrao(campo, valor) {
    var p = padraoDe(campo);
    if (campo.tipo === "lista") return !(valor || []).length;
    if (campo.tipo === "bool") return !!valor === !!p;
    return String(valor == null ? "" : valor) === String(p == null ? "" : p);
  }

  // ---------------------------------------------------------------- escrever
  // Devolve o valor efetivamente escrito, ou null quando não deu para interpretar o que veio
  // (opção de select inexistente, data ilegível, valor não numérico).

  function escreverCampo(campo, valor) {
    if (typeof campo.escrever === "function") return campo.escrever(valor);
    var $e = $el(campo);
    if (!$e.length) return null;

    if (campo.tipo === "bool") {
      var b = valor === true || valor === "true" || valor === 1 || valor === "1" || valor === "sim";
      $e.prop("checked", b);
      return b;
    }
    if (campo.tipo === "select") {
      var v = resolverOpcao(campo, valor);
      if (v === null) return null;
      $e.val(v);
      return v;
    }
    if (campo.tipo === "data") {
      var d = normalizarData(valor);
      if (d === null) return null;
      $e.val(d);
      return d;
    }
    if (campo.tipo === "moeda") {
      if (valor === "" || valor == null) { $e.val(""); return ""; }
      var n = parseValor(valor);
      if (!isFinite(n)) return null;
      var txt = formatarValor(n);
      $e.val(txt);
      return txt;
    }
    if (campo.tipo === "numero") {
      if (valor === "" || valor == null) { $e.val(""); return ""; }
      var num = Number(valor);
      if (!isFinite(num)) return null;
      $e.val(String(num));
      return String(num);
    }
    var s = valor == null ? "" : String(valor);
    $e.val(s);
    return s;
  }

  // ---------------------------------------------------------------- resumo humano

  function rotuloValor(campo, valor) {
    if (campo.tipo === "bool") return valor ? "sim" : "não";
    if (campo.tipo === "lista") return (valor || []).join(", ");
    if (campo.tipo === "moeda") return "R$ " + valor;
    if (campo.tipo === "select") {
      var achou = null;
      opcoesDe(campo).some(function (o) {
        if (String(o.valor) === String(valor)) { achou = o.rotulo; return true; }
        return false;
      });
      return achou || String(valor);
    }
    if (campo.tipo === "data") {
      var p = String(valor).split("-");
      return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : String(valor);
    }
    return String(valor);
  }

  function resumo() {
    if (!cfg) return "";
    var partes = [];
    cfg.campos.forEach(function (c) {
      var v = lerCampo(c);
      if (ehPadrao(c, v)) return;
      partes.push(c.rotulo + ": " + rotuloValor(c, v));
    });
    return partes.join(" · ");
  }

  // ---------------------------------------------------------------- API pública

  function registrar(descritor) {
    cfg = descritor;
    cfg.campos = cfg.campos || [];
  }

  function disponivel() { return !!cfg; }

  var MAX_OPCOES = 80; // teto por campo, para a lista de bancos de um extrato grande não inchar o prompt

  /* Retrato da tela para o prompt: o que dá para filtrar, os valores válidos e o que já está
     filtrado agora. É isto que o chat manda junto da pergunta. */
  function esquema() {
    if (!cfg) return null;
    var atuais = {};
    var campos = cfg.campos.map(function (c) {
      var item = { nome: c.nome, rotulo: c.rotulo, tipo: c.tipo };
      if (c.descricao) item.descricao = c.descricao;
      if (c.tipo === "select") {
        var opcoes = opcoesDe(c);
        item.opcoes = opcoes.slice(0, MAX_OPCOES).map(function (o) {
          return { valor: o.valor, rotulo: o.rotulo };
        });
        if (opcoes.length > MAX_OPCOES) item.opcoes_truncadas = opcoes.length;
      }
      if (c.tipo === "lista" && typeof c.opcoes === "function") {
        item.opcoes = (c.opcoes() || []).slice(0, MAX_OPCOES);
      }
      var v = lerCampo(c);
      if (!ehPadrao(c, v)) atuais[c.nome] = v;
      return item;
    });
    return {
      pagina: cfg.pagina,
      titulo: cfg.titulo,
      descricao: cfg.descricao || "",
      campos: campos,
      valores: atuais,
      resumo: resumo(),
    };
  }

  /* Escreve os filtros e remonta o grafo.
     Por padrão SUBSTITUI a visão: campo não citado volta ao padrão — a IA propõe um recorte
     inteiro ("conversas com beltrano, no banco xpto, de X a Y"), e deixar sobra de um filtro
     anterior produziria um grafo que não é nem o de antes nem o proposto. `{ mesclar: true }`
     preserva o que não foi citado.
     Devolve { aplicados, ignorados, anterior, resumo } — `anterior` serve ao "Desfazer". */
  function aplicar(valores, opts) {
    if (!cfg) return null;
    valores = valores || {};
    var mesclar = !!(opts && opts.mesclar);
    var anterior = ler();
    var aplicados = {}, ignorados = [];
    var conhecidos = {};

    cfg.campos.forEach(function (c) {
      conhecidos[c.nome] = true;
      var citado = Object.prototype.hasOwnProperty.call(valores, c.nome);
      if (!citado) {
        if (!mesclar) escreverCampo(c, padraoDe(c));
        return;
      }
      var escrito = escreverCampo(c, valores[c.nome]);
      if (escrito === null) {
        ignorados.push(c.rotulo + ' ("' + valores[c.nome] + '" não existe nesta tela)');
        escreverCampo(c, padraoDe(c));
        return;
      }
      if (!ehPadrao(c, escrito)) aplicados[c.nome] = escrito;
    });

    Object.keys(valores).forEach(function (nome) {
      if (!conhecidos[nome]) ignorados.push(nome + " (filtro inexistente nesta tela)");
    });

    if (typeof cfg.aoAplicar === "function") cfg.aoAplicar();
    return { aplicados: aplicados, ignorados: ignorados, anterior: anterior, resumo: resumo() };
  }

  function limpar() { return aplicar({}); }

  /* Volta ao estado devolvido em `anterior` por uma chamada de aplicar(). Round-trip garantido:
     todo valor lido de um campo é aceito de volta por escreverCampo (select por valor exato,
     moeda já mascarada, data em yyyy-mm-dd). */
  function restaurar(valores) { return aplicar(valores || {}); }

  window.GrafoFiltros = {
    registrar: registrar,
    disponivel: disponivel,
    esquema: esquema,
    aplicar: aplicar,
    limpar: limpar,
    restaurar: restaurar,
    ler: ler,
    resumo: resumo,
  };
})();
