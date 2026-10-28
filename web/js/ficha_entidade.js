/* Ficha da entidade — página aberta pelo botão ↗ da gaveta dos grafos (RIF e SIMBA).

 * O grafo responde "quem se liga a quem"; a ficha responde "o que se sabe desta pessoa/empresa
 * nesta fonte": identificação, totais e a lista de movimentações. Sem backend novo: o RIF relê
 * os mesmos quatro endpoints da tela de tabelas (comunicações, envolvidos, contrapartes,
 * identificações) e o SIMBA relê /simba/{id}/grafo recortado pelo documento/nome — a mesma
 * agregação que o grafo usa, só que em tabela em vez de desenho.
 *
 * Entradas por URL: fonte=rif|simba, inquerito, rif= ou simba=, nome=, doc=.
 * Casa por documento quando há documento (CPF/CNPJ só de dígitos); sem documento, casa pelo
 * nome completo normalizado — por isso a ficha pode trazer homônimos quando o RIF não informa
 * o documento, e diz isso no rodapé. */
(function () {
  "use strict";

  var U = GrafoFluxo.util;
  var num = U.num, soDigitos = U.soDigitos, norm = U.norm;
  function formatarReais(v) { return U.formatarReais(v); }
  function formatarData(iso) { return U.formatarData(iso); }
  function paramUrl(n) { return AnalyserUtils.paramUrl(n); }

  var fonte = paramUrl("fonte") || "rif";
  var idInq = paramUrl("inquerito") || "";
  var idRif = paramUrl("rif") || "";
  var idSimba = paramUrl("simba") || "";
  var doc = soDigitos(paramUrl("doc") || "");
  var nome = (paramUrl("nome") || "").trim();
  var nomeNorm = norm(nome);

  $("#ficha-voltar").attr("href", document.referrer || "javascript:history.back()");
  $("#ficha-fonte").text(fonte === "simba" ? "· extrato SIMBA" : "· RIF (COAF)");

  function casaDoc(d) { var sd = soDigitos(d); return !!doc && !!sd && sd === doc; }
  function casaNome(n) { return !doc && !!nomeNorm && norm(n) === nomeNorm; }
  function casa(ent, campoNome, campoDoc) {
    return casaDoc(ent[campoDoc]) || (!soDigitos(ent[campoDoc]) && casaNome(ent[campoNome]));
  }

  // ------------------------------------------------------------------ montagem da tela
  function setTitulo(texto) { $("#ficha-nome").text(texto || "—"); }
  function chip(texto, classe) {
    if (!texto) return;
    $("<span>").addClass("ficha-chip").addClass(classe || "").text(texto).appendTo("#ficha-chips");
  }
  function card(rotulo, valor, classe) {
    var $c = $("<div>").addClass("ficha-card");
    if (classe) $c.addClass(classe);
    $("<span>").text(rotulo).appendTo($c);
    $("<b>").text(valor).appendTo($c);
    $("#ficha-cards").append($c);
  }
  function montarTabela(colunas) {
    colunas.forEach(function (c) {
      $("<th>").text(c).appendTo("#ficha-thead-tr");
    });
    $("#ficha-tab-titulo, #ficha-tab").show();
  }
  // linhas: [{cells: [...], valor: n, cred: bool}] — ordenadas por data textual (ISO) desc
  function preencherTabela(linhas, limite) {
    linhas.sort(function (a, b) { return String(b.ordem || "").localeCompare(String(a.ordem || "")); });
    var $tb = $("#ficha-tab tbody");
    var mostra = linhas.slice(0, limite);
    mostra.forEach(function (ln) {
      var $tr = $("<tr>");
      ln.cells.forEach(function (cel) { $tr.append($("<td>").text(cel)); });
      if (ln.valor != null) {
        $tr.children("td").last()
          .addClass("ficha-v").addClass(ln.cred ? "cred" : "deb")
          .text(formatarReais(ln.valor));
      }
      $tb.append($tr);
    });
    if (linhas.length > limite) {
      var $mais = $("<tr>").addClass("mais");
      $("<td>").attr("colspan", $("#ficha-thead-tr").children().length)
        .text("… e mais " + (linhas.length - limite) + " movimentações (veja as tabelas da fonte).")
        .appendTo($mais);
      $tb.append($mais);
    }
  }
  function vazio(msg) {
    if (msg) $("#ficha-vazio").text(msg);
    $("#ficha-vazio").show();
  }
  function semParam() {
    vazio("Ficha sem parâmetros: abra pelo botão ↗ da gaveta de um grafo.");
  }

  // ------------------------------------------------------------------ RIF
  if (fonte === "simba") carregarSimba(); else carregarRif();
  if (!idRif && fonte !== "simba") semParam();

  function carregarRif() {
    if (!idRif) return;
    var d = { comunicacoes: [], envolvidos: [], contrapartes: [], identificacoes: [] };
    function get(aba) {
      return $.getJSON("/api/investigacao/rifs/" + idRif + "/" + aba)
        .done(function (lista) { d[aba] = lista || []; });
    }
    $.when(get("comunicacoes"), get("envolvidos"), get("contrapartes"), get("identificacoes"))
      .always(function () { montarRif(d); });
  }

  function montarRif(d) {
    var comPorId = {}, titularesPorIdx = {};
    d.comunicacoes.forEach(function (c) { comPorId[c.id_comunicacao_rif] = c; });
    d.envolvidos.forEach(function (e) {
      if (String(e.tp_envolvido || "").trim() === "Titular") {
        (titularesPorIdx[cIdx(e)] = titularesPorIdx[cIdx(e)] || []).push(e);
      }
    });
    function cIdx(e) { return e.nu_indexador; }

    var eu = d.envolvidos.filter(function (e) { return casa(e, "no_envolvido", "nu_cpf_cnpj"); });
    var cps = d.contrapartes.filter(function (cp) { return !ehVazia(cp) && casa(cp, "no_contraparte", "nu_cpf_cnpj"); });
    var idents = d.identificacoes.filter(function (i) { return casa(i, "no_contraparte", "nu_cpf_cnpj"); });
    if (!eu.length && !cps.length && !idents.length) {
      setTitulo(nome || doc);
      vazio("Esta entidade não aparece neste RIF (ou aparece só dentro do texto livre de alguma comunicação).");
      return;
    }

    var papeis = {}, pep = false, ehTitular = false, nomeMelhor = nome, docMelhor = doc;
    eu.forEach(function (e) {
      var t = String(e.tp_envolvido || "").trim();
      if (t) papeis[t] = true;
      if (t === "Titular") ehTitular = true;
      if (/^s/i.test(String(e.tx_pep || ""))) pep = true;
      if (!docMelhor && e.nu_cpf_cnpj) docMelhor = soDigitos(e.nu_cpf_cnpj);
    });
    cps.forEach(function (cp) { if (!docMelhor && cp.nu_cpf_cnpj) docMelhor = soDigitos(cp.nu_cpf_cnpj); });

    setTitulo(nomeMelhor || (eu[0] && eu[0].no_envolvido) || (cps[0] && cps[0].no_contraparte) || doc);
    if (docMelhor) chip("CPF/CNPJ " + fmtDoc(docMelhor));
    if (ehTitular) chip("Titular de conta", "titular");
    if (pep) chip("PEP", "pep");
    Object.keys(papeis).forEach(function (p) { if (p !== "Titular") chip(p); });
    if (idents.length) chip("identificada manualmente pelo investigador");

    var linhas = [], entrou = 0, saiu = 0, lanc = 0;
    var coms = {}, bancos = {}, titularesLigados = {};

    // como contraparte: cada fatia que a extração nomeou nas tabelas Entradas/Saídas
    cps.forEach(function (cp) {
      var c = comPorId[cp.id_comunicacao_rif];
      if (!c) return;
      var dt = String(c.dt_operacao || c.dt_recebimento || "").slice(0, 10);
      var tit = (titularesPorIdx[c.nu_indexador] || []).map(function (t) { return t.no_envolvido; }).join(" / ") || "titular";
      var credito = cp.cd_direcao === "credito";
      var v = num(cp.vl_total);
      if (credito) entrou += v; else saiu += v;
      lanc += num(cp.qt_lancamentos);
      coms[c.nu_indexador] = true;
      if (c.no_comunicante) bancos[c.no_comunicante] = true;
      titularesLigados[tit] = true;
      linhas.push({
        ordem: dt,
        cells: [formatarData(dt), "nº " + c.nu_indexador, c.no_comunicante || "—",
                credito ? "→ pagou " + tit : "← recebeu de " + tit, ""],
        valor: v, cred: credito
      });
    });

    // como titular: o total declarado em cada comunicação dela (campos B/C do SISCOAF)
    d.comunicacoes.forEach(function (c) {
      var meus = (titularesPorIdx[c.nu_indexador] || []).some(function (t) {
        return casa(t, "no_envolvido", "nu_cpf_cnpj");
      });
      if (!meus) return;
      coms[c.nu_indexador] = true;
      if (c.no_comunicante) bancos[c.no_comunicante] = true;
      var dt = String(c.dt_operacao || c.dt_recebimento || "").slice(0, 10);
      var b = num(c.vl_campo_b), cc = num(c.vl_campo_c);
      if (b > 0.005) {
        entrou += b;
        linhas.push({ ordem: dt, cells: [formatarData(dt), "nº " + c.nu_indexador,
                    c.no_comunicante || "—", "entrou na conta dela (campo B)", ""], valor: b, cred: true });
      }
      if (cc > 0.005) {
        saiu += cc;
        linhas.push({ ordem: dt, cells: [formatarData(dt), "nº " + c.nu_indexador,
                    c.no_comunicante || "—", "saiu da conta dela (campo C)", ""], valor: cc, cred: false });
      }
    });

    card("Creditaram na conta dela", formatarReais(entrou), "cred");
    card("Debitaram da conta dela", formatarReais(saiu), "deb");
    card("Lançamentos apurados", String(lanc || "—"));
    card("Comunicações", String(Object.keys(coms).length || "—"));
    card("Instituições", String(Object.keys(bancos).length || "—"));

    montarTabela(["Data", "Comunicação", "Banco", "Movimento", "Valor"]);
    if (!linhas.length) vazio("Aparece só como envolvida (papel), sem lançamento de valor apurado.");
    else preencherTabela(linhas, 300);

    var $x = $("#ficha-extra");
    if (Object.keys(titularesLigados).length) {
      $("<h3 class=\"ficha-secao\">").text("Titulares com quem se relaciona").appendTo($x);
      var $ul = $("<div class=\"ficha-chips\">").appendTo($x);
      Object.keys(titularesLigados).forEach(function (t) {
        $("<span class=\"ficha-chip titular\">").text(t).appendTo($ul);
      });
    }
    if (idents.length) {
      $("<h3 class=\"ficha-secao\">").text("Identificações manuais (RIF_TB06)").appendTo($x);
      var $t = $("<table class=\"ficha-tab\">").appendTo($x);
      var $tr = $("<tr>").appendTo($("<thead>").appendTo($t));
      ["Comunicação", "Direção", "Valor", "Observação"].forEach(function (h) { $("<th>").text(h).appendTo($tr); });
      var $tb = $("<tbody>").appendTo($t);
      idents.forEach(function (i) {
        var $r = $("<tr>").appendTo($tb);
        ["nº " + i.nu_indexador, i.cd_direcao === "credito" ? "entrada" : "saída",
         formatarReais(i.vl_total), i.tx_obs || "—"].forEach(function (cel) { $("<td>").text(cel).appendTo($r); });
      });
    }
    if (!doc) {
      $("<p class=\"field-help\">").text("Sem documento: a ficha casou pelo nome e pode trazer homônimos.")
        .appendTo("#ficha-extra");
    }

    $("#ficha-no-grafo").attr("href", "grafo_rif.html?inquerito=" + encodeURIComponent(idInq) +
      "&rif=" + encodeURIComponent(idRif) +
      "&busca=" + encodeURIComponent((doc || nome))).show();
  }
  function ehVazia(cp) {
    return !String(cp.no_contraparte || "").trim() && !String(cp.nu_cpf_cnpj || "").trim();
  }
  function fmtDoc(d) {
    if (d.length === 11) return d.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
    if (d.length === 14) return d.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, "$1.$2.$3/$4-$5");
    return d;
  }

  // ------------------------------------------------------------------ SIMBA
  function carregarSimba() {
    if (!idSimba) { semParam(); return; }
    var termo = doc || nome;
    if (!termo) { semParam(); return; }
    $.getJSON("/api/investigacao/simba/" + idSimba + "/grafo", { termo: termo, limite: "150" })
      .done(function (r) { montarSimba(r || { nos: [], arestas: [] }); })
      .fail(function () { vazio("Erro ao consultar o extrato."); });
  }

  function montarSimba(r) {
    var porId = {}, idsEu = {}, eu = null;
    (r.nos || []).forEach(function (n) {
      porId[n.id] = n;
      if (n.tipo === "banco" || n.tipo === "outros") return;
      var bate = casaDoc(n.cpf_cnpj) || (!soDigitos(n.cpf_cnpj) && casaNome(n.rotulo)) || casaNome(n.rotulo);
      if (bate) {
        idsEu[n.id] = true;
        if (!eu || n.tipo === "titular") eu = n;   // o "T:" é a melhor versão da ficha
      }
    });
    setTitulo((eu && eu.rotulo) || nome || doc);
    if (!eu) {
      vazio("Esta entidade não aparece neste recorte do extrato.");
      return;
    }

    var titular = eu.tipo === "titular";
    if (eu.cpf_cnpj) chip("CPF/CNPJ " + fmtDoc(soDigitos(eu.cpf_cnpj)));
    if (titular) chip("Titular de conta afastada", "titular");
    if (eu.in_passagem) chip("conta de passagem (entra ≈ sai)");
    // os dois papéis no mesmo extrato: a pessoa é titular de uma conta E contraparte de outra
    if (titular && Object.keys(idsEu).some(function (id) { return /^C:/.test(id); })) {
      chip("também aparece como contraparte neste extrato");
    }

    var linhas = [], entrou = 0, saiu = 0, lanc = 0, contas = 0;
    var bancos = {}, contrapartes = {};
    (r.arestas || []).forEach(function (a) {
      var de = porId[a.origem], para = porId[a.destino];
      if (a.tipo === "conta") {
        if (idsEu[a.destino]) {
          contas += num(a.qt_contas);
          if (de && de.rotulo) bancos[de.rotulo] = true;
        }
        return;
      }
      var euDeLado = idsEu[a.origem] ? "origem" : (idsEu[a.destino] ? "destino" : null);
      if (!euDeLado) return;
      var outro = euDeLado === "origem" ? para : de;
      // o extrato é visto da conta do titular (origem): entrada = entrou nele; para a
      // contraparte (destino) os papéis se invertem
      var minhaEntrada = euDeLado === "origem" ? num(a.vl_entrada) : num(a.vl_saida);
      var minhaSaida = euDeLado === "origem" ? num(a.vl_saida) : num(a.vl_entrada);
      entrou += minhaEntrada; saiu += minhaSaida; lanc += num(a.qt_lancamentos);
      if (outro) {
        if (outro.banco) bancos[outro.banco] = true;
        if (outro.rotulo && outro.tipo !== "banco") contrapartes[outro.rotulo] = true;
      }
      var per = [a.dt_primeiro, a.dt_ultimo].filter(Boolean)
        .map(function (x) { return formatarData(x); }).join(" a ");
      if (minhaEntrada > 0.005) {
        linhas.push({ ordem: a.dt_ultimo || a.dt_primeiro || "",
                      cells: [per || "—", (outro && outro.rotulo) || "?", "entrou (recebeu)", ""],
                      valor: minhaEntrada, cred: true });
      }
      if (minhaSaida > 0.005) {
        linhas.push({ ordem: a.dt_ultimo || a.dt_primeiro || "",
                      cells: [per || "—", (outro && outro.rotulo) || "?", "saiu (pagou)", ""],
                      valor: minhaSaida, cred: false });
      }
    });
    if (eu.banco) bancos[eu.banco] = true;

    card("Recebeu", formatarReais(entrou), "cred");
    card("Pagou", formatarReais(saiu), "deb");
    card("Lançamentos", String(eu.qt_lancamentos || lanc || "—"));
    if (titular) card("Contas afastadas", String(contas || eu.qt_contas || "—"));
    card("Bancos", String(Object.keys(bancos).length || "—"));
    card("Contrapartes", String(Object.keys(contrapartes).length || "—"));

    montarTabela(["Período", "Contraparte", "Movimento", "Valor"]);
    if (!linhas.length) vazio("Sem lançamentos desta entidade neste recorte (o limite de contrapartes pode ter agrupado as demais).");
    else preencherTabela(linhas, 300);

    if (!doc) {
      $("<p class=\"field-help\">").text("Sem documento: a ficha casou pelo nome e pode trazer homônimos.")
        .appendTo("#ficha-extra");
    }
    $("#ficha-no-grafo").attr("href", "grafo_simba.html?inquerito=" + encodeURIComponent(idInq) +
      "&simba=" + encodeURIComponent(idSimba) +
      "&busca=" + encodeURIComponent(termo)).show();
  }
})();
