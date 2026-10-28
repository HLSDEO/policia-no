/* Grafo do RIF — página própria (grafo_rif.html), separada de rif.html.
 *
 * Antes o grafo era a 4ª aba de rif.html e dividia o mesmo script com as tabelas de
 * Comunicações/Envolvidos/Ocorrências. Passou a ter tela só sua porque é o que mais ocupa espaço
 * (filtros + legenda + SVG em tela cheia) e o que mais custa a montar — o layout force-directed é
 * O(n²) por iteração e não faz sentido rodar toda vez que alguém abre o RIF só para ler as tabelas.
 *
 * Recebe ?inquerito= e ?rif= na URL, exatamente como rif.html, e recarrega os mesmos quatro
 * endpoints (comunicações, envolvidos, contrapartes, identificações) — a montagem do grafo depende
 * de todos eles. As identificações manuais (RIF_TB06) continuam sendo feitas em rif.html; aqui elas
 * só entram como nós já identificados, do jeito que estiverem na hora em que a página carrega.
 */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idRif = paramUrl("rif");
  var idInquerito = paramUrl("inquerito");

  // deep link da ficha ("Ver no grafo"): chega com a busca da entidade já aplicada
  var buscaInicial = paramUrl("busca");
  if (buscaInicial) $("#f-grafo-busca").val(buscaInicial);

  // dados carregados da API (fonte da verdade; os filtros re-renderizam a partir daqui)
  var dados = { comunicacoes: [], envolvidos: [] };

  // contrapartes estruturadas (remetentes/favorecidos) agrupadas por id_comunicacao_rif
  var contrapartesPorCom = {};

  // identificações manuais (RIF_TB06): fatias do valor DESCONHECIDO que o investigador atribuiu a
  // alguém por outra fonte, também agrupadas por id_comunicacao_rif
  var identificacoesPorCom = {};

  // Envolvidos (RIF_TB03, já vem com papel Titular/Remetente/Beneficiário/etc. por Indexador — sem
  // precisar de regex) agrupados por nu_indexador, calculado uma vez após carregar tudo.
  var envolvidosPorIndexador = {};

  // Anexa o(s) titular(es) de cada comunicação (via Envolvidos, casando pelo Indexador) direto no
  // objeto da comunicação, para o cabeçalho e para os nós do grafo.
  function anexarTitulares() {
    envolvidosPorIndexador = {};
    dados.envolvidos.forEach(function (e) {
      (envolvidosPorIndexador[e.nu_indexador] = envolvidosPorIndexador[e.nu_indexador] || []).push(e);
    });
    dados.comunicacoes.forEach(function (c) {
      var todos = envolvidosPorIndexador[c.nu_indexador] || [];
      var titulares = todos.filter(function (e) { return String(e.tp_envolvido || "").trim() === "Titular"; });
      c.no_titular = titulares.map(function (t) { return t.no_envolvido; }).filter(Boolean).join(" / ") || null;
      c.nu_cpf_cnpj_titular = titulares.length ? titulares[0].nu_cpf_cnpj : null;
      c._titulares = titulares;
    });
  }

  // ---------------------------------------------------------------- helpers
  // A linha sem nome E sem documento é o DESCONHECIDO: o resto entre o total da comunicação
  // (campos B/C) e o que o texto nomeia.
  function ehDesconhecida(cp) {
    return !String(cp.no_contraparte || "").trim() && !String(cp.nu_cpf_cnpj || "").trim();
  }
  function identsDe(idCom, direcao) {
    return (identificacoesPorCom[idCom] || []).filter(function (i) { return i.cd_direcao === direcao; });
  }
  function somaIdents(idents) {
    return idents.reduce(function (s, i) { return s + num(i.vl_total); }, 0);
  }

  // helpers de formatação vêm do motor do grafo (js/utils/grafo_fluxo.js) — ver o alias `U`
  // logo abaixo; aqui ficam só os que dependem do vocabulário do RIF
  var num = GrafoFluxo.util.num;
  var formatarReais = GrafoFluxo.util.formatarReais;
  var formatarData = GrafoFluxo.util.formatarData;
  var parseValorBR = GrafoFluxo.util.parseValorBR;
  var aplicarMascaraMonetaria = GrafoFluxo.util.aplicarMascaraMonetaria;
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function ehPep(e) { return /^s/i.test(String(e.tx_pep || "")); }          // "Sim"

  // ============================================================== GRAFO
  // O grafo é montado sobre as ENTRADAS e SAÍDAS de cada comunicação (as duas tabelas do "Ver
  // análise" em rif.html), com o TITULAR da conta no meio, não o banco. Quem creditou aponta para
  // o titular; o titular aponta para quem foi debitado; a instituição comunicante fica pendurada no
  // titular como "conta em". Entidades são deduplicadas por CPF/CNPJ em todo o RIF.
  //
  // Desenho, layout, gaveta e exportação ficam em js/utils/grafo_fluxo.js, compartilhados com o
  // grafo do SIMBA — aqui mora só o que é do RIF: de onde vêm os dados e como viram nós e arestas.
  var U = GrafoFluxo.util;
  var _soDigitos = U.soDigitos, _norm = U.norm, _chaves = U.chaves, _abrevReais = U.abrevReais;

  var CORES_NO = { instituicao: "#2563eb", pf: "#16a34a", pj: "#d97706", desconhecido: "#6b7280" };
  var ROTULO_NO = {
    instituicao: "Instituição comunicante (banco)", pf: "Pessoa física", pj: "Pessoa jurídica",
    desconhecido: "Contraparte não identificada no texto"
  };
  var ICONES_NO = {
    instituicao: '<rect x="3" y="9" width="18" height="11" rx="1"/><path d="M3 9l9-5 9 5"/><line x1="7" y1="12" x2="7" y2="17"/><line x1="12" y1="12" x2="12" y2="17"/><line x1="17" y1="12" x2="17" y2="17"/>',
    pf: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    pj: '<rect x="5" y="3" width="14" height="18" rx="1"/><line x1="9" y1="7" x2="9.01" y2="7"/><line x1="15" y1="7" x2="15.01" y2="7"/><line x1="9" y1="11" x2="9.01" y2="11"/><line x1="15" y1="11" x2="15.01" y2="11"/><line x1="10" y1="21" x2="14" y2="21"/>',
    desconhecido: '<circle cx="12" cy="12" r="9"/><path d="M9.3 9.4a2.8 2.8 0 0 1 5.4 1.1c0 1.9-2.7 2.2-2.7 4"/><line x1="12" y1="17.6" x2="12.01" y2="17.6"/>'
  };

  var COR_ARESTA = {
    credito: "#16a34a", "vinculo-credito": "#16a34a",
    debito: "#dc2626", "vinculo-debito": "#dc2626",
    conta: "#8696a0", cotitular: "#8696a0", vinculo: "#8696a0"
  };
  // tracejado = não é fluxo de caixa apurado (conta no banco, cotitularidade, papel só do CSV)
  var TRACEJADO_DIR = { conta: true, cotitular: true, vinculo: true, "vinculo-credito": true, "vinculo-debito": true };
  var MARCADOR_POR_DIR = { credito: "credito", debito: "debito", "vinculo-credito": "credito", "vinculo-debito": "debito" };

  // Papéis de Envolvidos (RIF_TB03) que indicam direção do fluxo, quando não há uma contraparte com
  // valor (RIF_TB05) cobrindo a mesma pessoa/comunicação — Depositante/Sacador são o equivalente das
  // COE (operações em espécie) a Remetente/Beneficiário nas COS.
  var _PAPEL_CREDITO = /^(Remetente|Depositante)$/i;
  var _PAPEL_DEBITO = /^(Benefici[aá]rio|Sacador|Favorecido)$/i;

  var COR_ARESTA = {
    credito: "#16a34a", "vinculo-credito": "#16a34a",
    debito: "#dc2626", "vinculo-debito": "#dc2626",
    conta: "#8696a0", cotitular: "#8696a0", vinculo: "#8696a0"
  };
  // tracejado = não é fluxo de caixa apurado (conta no banco, cotitularidade, papel só do CSV)
  var TRACEJADO_DIR = { conta: true, cotitular: true, vinculo: true, "vinculo-credito": true, "vinculo-debito": true };
  var MARCADOR_POR_DIR = { credito: "credito", debito: "debito", "vinculo-credito": "credito", "vinculo-debito": "debito" };

  // chave de deduplicação da instituição comunicante: o CNPJ do banco, com o nome como reserva —
  // as centenas de comunicações de um RIF costumam vir de uma dezena de bancos, e cada banco tem
  // que ser UM nó só (antes era um nó por comunicação, o que espalhava o mesmo banco pela tela)
  function chaveBanco(c) {
    return _soDigitos(c.nu_cpf_cnpj_comunicante) || _norm(c.no_comunicante) || "?";
  }

  // ------------------------------------------------------------------ construção
  // Modelo do grafo (um nó por entidade, deduplicada por CPF/CNPJ em todo o RIF):
  //
  //     remetente ──crédito──▶ TITULAR ──débito──▶ favorecido
  //                              │
  //                          (conta em)
  //                              │
  //                    instituição comunicante
  //
  // Fontes, por comunicação:
  //   1) contrapartes estruturadas (RIF_TB05, extraídas do texto livre) — são exatamente as linhas
  //      das tabelas "Entradas" e "Saídas" do "Ver análise"; têm valor e nº de lançamentos;
  //   2) identificações manuais (RIF_TB06) — fatias do "Desconhecido" atribuídas pelo investigador,
  //      entram como contraparte normal e saem do nó "Não identificado";
  //   3) Envolvidos (RIF_TB03) — trazem o papel pronto do CSV (Titular/Remetente/Beneficiário/Sócio/
  //      Procurador/...); dão o titular e reforçam, tracejado e sem valor, quem o regex não capturou.
  // O que o texto não nomeia vira um nó "Não identificado" por titular e direção — o dinheiro que
  // falta explicar não pode sumir do desenho.
  // Arestas do mesmo par são somadas: quem aparece em 20 comunicações do mesmo titular vira UMA
  // aresta, com o total em R$ e a soma dos lançamentos.
  function construirGrafo() {
    // ---- filtros
    var busca = $.trim($("#f-grafo-busca").val() || "").toLowerCase();
    var buscaDoc = _soDigitos(busca);
    var tipoPessoa = $("#f-grafo-pessoa").val() || "";
    var bancoSel = $("#f-grafo-banco").val() || "";
    var dataDe = $("#f-grafo-data-de").val();
    var dataAte = $("#f-grafo-data-ate").val();
    var creditoDe = parseValorBR($("#f-grafo-credito-de").val());
    var creditoAte = parseValorBR($("#f-grafo-credito-ate").val());
    var debitoDe = parseValorBR($("#f-grafo-debito-de").val());
    var debitoAte = parseValorBR($("#f-grafo-debito-ate").val());
    var semDesconhecido = $("#f-grafo-desconhecido").is(":checked");
    var temFiltroValor = isFinite(creditoDe) || isFinite(creditoAte) || isFinite(debitoDe) || isFinite(debitoAte);

    // ---- 1. comunicações que entram no grafo (período + banco)
    var comsFiltradas = dados.comunicacoes.filter(function (c) {
      if (bancoSel && chaveBanco(c) !== bancoSel) return false;
      var dt = c.dt_operacao || c.dt_recebimento;
      if (!dt) return !(dataDe || dataAte);   // sem data não dá para afirmar que está no período
      dt = String(dt).slice(0, 10);
      if (dataDe && dt < dataDe) return false;
      if (dataAte && dt > dataAte) return false;
      return true;
    });

    // PEP conhecido (por CPF e por nome), a partir dos envolvidos do RIF
    var pepDoc = {}, pepNome = {};
    dados.envolvidos.forEach(function (e) {
      if (ehPep(e)) { var d = _soDigitos(e.nu_cpf_cnpj); if (d) pepDoc[d] = true; if (e.no_envolvido) pepNome[_norm(e.no_envolvido)] = true; }
    });

    // ---- 2. registro de nós
    var nos = [], idxPorChave = {};

    function novoNo(chave, base) {
      var idx = idxPorChave[chave];
      if (idx === undefined) {
        idx = nos.length; idxPorChave[chave] = idx;
        base.chave = chave;
        base.credito = 0;   // R$ que este nó creditou na conta de um titular (entrada do RIF)
        base.debito = 0;    // R$ que este nó recebeu da conta de um titular (saída do RIF)
        base.entrada = 0;   // como titular: R$ que entraram na sua conta
        base.saida = 0;     // como titular: R$ que saíram da sua conta
        base.autoCredito = 0;  // o titular figurando como contraparte de si mesmo (entre contas
        base.autoDebito = 0;   // próprias): não vira aresta, mas o R$ não pode sumir da conta
        base.nLanc = 0;
        base.papeis = {};
        base.bancos = {};
        base.coms = {};
        nos.push(base);
      }
      return idx;
    }

    // pessoa/empresa deduplicada por CPF/CNPJ (reserva: nome normalizado)
    function noEntidade(nome, doc, opts) {
      opts = opts || {};
      var d = _soDigitos(doc);
      var idx = novoNo("e:" + (d || _norm(nome) || "?"), {
        tipo: d.length === 14 ? "pj" : "pf", rotulo: nome || doc || "?", doc: doc || "", pep: false, titular: false
      });
      var n = nos[idx];
      if (!n.doc && doc) n.doc = doc;              // completa o documento quando só o nome veio antes
      if (d.length === 14) n.tipo = "pj";
      if (opts.pep || pepDoc[d] || pepNome[_norm(nome)]) n.pep = true;
      if (opts.titular) n.titular = true;
      if (opts.manual) n.manual = true;
      if (opts.bancoObs) n.bancos[opts.bancoObs] = true;
      return idx;
    }

    function noInstituicao(c) {
      var idx = novoNo("b:" + chaveBanco(c), {
        tipo: "instituicao", rotulo: c.no_comunicante || ("Comunicação " + c.nu_indexador),
        doc: c.nu_cpf_cnpj_comunicante || "", valor: 0, nComs: 0, titulares: {}, ufs: {}
      });
      var n = nos[idx];
      n.valor += num(c.vl_campo_b) + num(c.vl_campo_c);
      n.nComs++;
      if (c.sg_uf_agencia) n.ufs[[c.no_cidade_agencia, c.sg_uf_agencia].filter(Boolean).join("/")] = true;
      return idx;
    }

    // o que o texto não nomeia, por titular e direção — um só nó em vez de um "?" por comunicação
    function noDesconhecido(iTit, direcao) {
      return novoNo("?:" + iTit + ":" + direcao, {
        tipo: "desconhecido", rotulo: "Não identificado (" + (direcao === "credito" ? "entradas" : "saídas") + ")",
        doc: "", pep: false, titular: false, direcao: direcao, iTitular: iTit
      });
    }

    // ---- 3. arestas (somadas por par + direção; vínculos sem valor separados por papel)
    var arestas = [], arestaPorChave = {};
    function ligar(i, j, dir, papel, valor, lanc, c) {
      if (i === undefined || j === undefined || i === j) return null;
      var semValor = !num(valor);
      var chave = dir + "|" + i + "|" + j + "|" + (semValor ? (papel || "") : "");
      var a = arestaPorChave[chave];
      if (!a) {
        a = { i: i, j: j, dir: dir, papel: papel, cor: COR_ARESTA[dir] || "#8696a0",
              valor: 0, nLanc: 0, semValor: true, coms: {}, bancos: {},
              dtPrimeiro: null, dtUltimo: null };
        arestaPorChave[chave] = a; arestas.push(a);
      }
      a.valor += num(valor);
      a.nLanc += (lanc || 0);
      if (a.valor > 0.005) a.semValor = false;
      if (c) {
        a.coms[c.nu_indexador] = true;
        if (c.no_comunicante) a.bancos[c.no_comunicante] = true;
        // Período da ligação: a data da OPERAÇÃO quando o RIF a traz, senão a do recebimento da
        // comunicação. É a mesma data pela qual o filtro de período da tela decide, então o
        // intervalo exportado bate com o que o investigador escolheu ver.
        var dtc = c.dt_operacao || c.dt_recebimento;
        if (dtc) {
          dtc = String(dtc).slice(0, 10);
          if (!a.dtPrimeiro || dtc < a.dtPrimeiro) a.dtPrimeiro = dtc;
          if (!a.dtUltimo || dtc > a.dtUltimo) a.dtUltimo = dtc;
        }
      }
      return a;
    }

    comsFiltradas.forEach(function (c) {
      var idCom = c.id_comunicacao_rif;
      var ib = noInstituicao(c);

      // titulares da comunicação (Envolvidos, papel "Titular") — o pivô do fluxo
      var titIdxs = (c._titulares || []).map(function (t) {
        var idx = noEntidade(t.no_envolvido, t.nu_cpf_cnpj, { titular: true });
        nos[idx].papeis["Titular"] = true;
        nos[idx].coms[c.nu_indexador] = true;
        if (c.no_comunicante) nos[idx].bancos[c.no_comunicante] = true;
        return idx;
      });
      // Comunicação sem Titular no CSV de Envolvidos: a instituição assume o papel de pivô, para a
      // movimentação não sumir do grafo (acontece quando o exportador do SISCOAF omite o papel).
      var iPivo = titIdxs.length ? titIdxs[0] : ib;
      if (!titIdxs.length) nos[ib].semTitular = true;

      titIdxs.forEach(function (it, k) {
        nos[ib].titulares[it] = true;
        ligar(it, ib, "conta", "Titular da conta", 0, 0, c);
        if (k > 0) ligar(titIdxs[0], it, "cotitular", "Cotitular", 0, 0, c);   // conta conjunta
      });

      var vistaChave = {};

      // as fatias identificadas à mão saem do desconhecido e viram nós próprios, com o mesmo peso
      // de uma contraparte extraída do texto (o que sobra continua no nó "Não identificado")
      var identificadas = (identificacoesPorCom[idCom] || []).map(function (i) {
        return { no_contraparte: i.no_contraparte, nu_cpf_cnpj: i.nu_cpf_cnpj, cd_direcao: i.cd_direcao,
                 qt_lancamentos: i.qt_lancamentos, vl_total: i.vl_total, tx_obs: i.tx_obs, _manual: true };
      });

      (contrapartesPorCom[idCom] || []).concat(identificadas).forEach(function (cp) {
        var v = num(cp.vl_total);
        var desconhecida = !cp._manual && ehDesconhecida(cp);
        if (desconhecida) {
          v -= somaIdents(identsDe(idCom, cp.cd_direcao));
          if (v <= 0.005) return;
        }
        var credito = cp.cd_direcao === "credito";
        var idx;
        if (desconhecida) {
          idx = noDesconhecido(iPivo, cp.cd_direcao);
        } else {
          idx = noEntidade(cp.no_contraparte, cp.nu_cpf_cnpj, {
            pep: /pep/i.test(cp.tx_obs || ""), manual: !!cp._manual, bancoObs: $.trim(cp.tx_obs || "") || null
          });
          vistaChave[_soDigitos(cp.nu_cpf_cnpj) || _norm(cp.no_contraparte)] = true;
        }
        var no = nos[idx];
        no.nLanc += (cp.qt_lancamentos || 0);
        no.coms[c.nu_indexador] = true;
        if (c.no_comunicante) no.bancos[c.no_comunicante] = true;
        if (!desconhecida) no.papeis[(credito ? "Remetente" : "Favorecido") + (cp._manual ? " (identificado)" : "")] = true;

        // o titular nomeado como contraparte da própria conta (transferência entre contas dele
        // mesmo) não desenha aresta — seria um laço — mas fica registrado no nó, senão o total do
        // grafo não fecha com o total do RIF
        if (idx === iPivo) {
          if (credito) nos[iPivo].autoCredito += v; else nos[iPivo].autoDebito += v;
          return;
        }
        if (credito) {
          no.credito += v; nos[iPivo].entrada += v;
          ligar(idx, iPivo, "credito", cp._manual ? "Remetente (id.)" : "Remetente", v, cp.qt_lancamentos, c);
        } else {
          no.debito += v; nos[iPivo].saida += v;
          ligar(iPivo, idx, "debito", cp._manual ? "Favorecido (id.)" : "Favorecido", v, cp.qt_lancamentos, c);
        }
      });

      // Envolvidos desta comunicação que a extração de texto não capturou: ainda mostra o vínculo COM
      // O TITULAR (sem valor), para não perder quem só aparece no CSV de Envolvidos — formatos de
      // texto não cobertos pelo parser, ou papéis societários (Sócio/Procurador) que não são fluxo.
      (envolvidosPorIndexador[c.nu_indexador] || []).forEach(function (e) {
        var tipo = $.trim(e.tp_envolvido || "");
        if (tipo === "Titular") return;
        var chave = _soDigitos(e.nu_cpf_cnpj) || _norm(e.no_envolvido);
        if (vistaChave[chave]) return;
        var idx = noEntidade(e.no_envolvido, e.nu_cpf_cnpj);
        if (idx === iPivo) return;
        var no = nos[idx];
        no.papeis[tipo || "Vínculo"] = true;
        no.coms[c.nu_indexador] = true;
        if (c.no_comunicante) no.bancos[c.no_comunicante] = true;
        if (_PAPEL_CREDITO.test(tipo)) ligar(idx, iPivo, "vinculo-credito", tipo, 0, 0, c);
        else if (_PAPEL_DEBITO.test(tipo)) ligar(iPivo, idx, "vinculo-debito", tipo, 0, 0, c);
        else ligar(idx, iPivo, "vinculo", tipo || "Vínculo", 0, 0, c);
      });
    });

    // ---- 4. escala (raio dos nós e espessura das arestas)
    var maxCom = 0, maxEnt = 0, maxAresta = 0;
    nos.forEach(function (n) {
      if (n.tipo === "instituicao") { if (n.valor > maxCom) maxCom = n.valor; }
      else {
        n.entrada += n.autoCredito; n.saida += n.autoDebito;
        n.total = n.credito + n.debito + n.entrada + n.saida;
        if (n.total > maxEnt) maxEnt = n.total;
      }
    });
    arestas.forEach(function (a) { if (a.valor > maxAresta) maxAresta = a.valor; });
    nos.forEach(function (n) {
      if (n.tipo === "instituicao") n.r = 15 + 12 * (maxCom ? Math.sqrt(n.valor / maxCom) : 0);
      else n.r = (n.titular ? 12 : 10) + 11 * (maxEnt ? Math.sqrt(n.total / maxEnt) : 0);
    });
    arestas.forEach(function (a) {
      a.largura = 1.2 + 5.5 * (maxAresta ? Math.sqrt((a.valor || 0) / maxAresta) : 0);
      a.nComs = _chaves(a.coms).length;
      a.rotulo = a.semValor ? (a.papel || "Vínculo") : (a.papel + " · " + _abrevReais(a.valor));
    });
    // índice estável (posição no grafo completo) — usado para localizar as ligações de um nó mesmo
    // quando só um subconjunto é desenhado (ver limitarNosGrafo)
    nos.forEach(function (n, i) { n._idx = i; });

    // ---- 5. filtros de valor: só valem para as arestas de fluxo (as que têm R$ apurado)
    var vivas = arestas.filter(function (a) {
      if (a.semValor) return true;             // conta/cotitular/vínculo não têm lançamento para filtrar
      var v = a.valor || 0;
      if (a.dir === "credito") {
        if (isFinite(creditoDe) && v < creditoDe) return false;
        if (isFinite(creditoAte) && v > creditoAte) return false;
      } else if (a.dir === "debito") {
        if (isFinite(debitoDe) && v < debitoDe) return false;
        if (isFinite(debitoAte) && v > debitoAte) return false;
      }
      return true;
    });

    // ---- 6. filtros de nó
    var vivo = nos.map(function () { return true; });
    if (semDesconhecido) nos.forEach(function (n, i) { if (n.tipo === "desconhecido") vivo[i] = false; });

    // "Tipo de pessoa" incide sobre as ENTIDADES QUE SE RELACIONARAM COM O TITULAR — por isso é
    // aplicado no lado CONTRAPARTE de cada ligação, não nó a nó. Filtrar nó a nó deixava o titular
    // (que é sempre poupado, senão o desenho se desmancha) e o banco na tela mesmo quando nenhuma
    // contraparte casava — pedir "PEP" num RIF sem PEP nenhum devolvia o grafo quase inteiro.
    function iContraparte(a) {
      return (a.dir === "debito" || a.dir === "vinculo-debito") ? a.j : a.i;
    }
    if (tipoPessoa) {
      vivas = vivas.filter(function (a) {
        if (a.dir === "conta" || a.dir === "cotitular") return true;   // esqueleto titular↔banco
        var n = nos[iContraparte(a)];
        if (!n || n.tipo === "instituicao") return true;
        if (n.tipo === "desconhecido") return false;                   // sem nome, sem tipo
        var doc = _soDigitos(n.doc);
        if (tipoPessoa === "pf") return doc.length !== 14;
        if (tipoPessoa === "pj") return doc.length === 14;
        return !!n.pep;
      });
    }

    // busca por nome/documento: mostra quem casa E o entorno, para o resultado continuar sendo um
    // grafo e não pontos soltos. Nível de entorno = nivelBusca (padrão 1: as relações diretas, o
    // "as relações de fulano" que o campo descreve). Clicar num ACHADO do painel sobe para 2: além
    // das relações diretas, o que elas tocam — o contexto que dá sentido ao achado.
    if (busca) {
      var casa = nos.map(function (n) {
        if (String(n.rotulo || "").toLowerCase().indexOf(busca) !== -1) return true;
        return !!(buscaDoc && _soDigitos(n.doc).indexOf(buscaDoc) !== -1);
      });
      var perto = casa.slice();
      for (var nivel = 0; nivel < nivelBusca; nivel++) {
        var alcance = perto.slice();
        vivas.forEach(function (a) {
          if (perto[a.i]) alcance[a.j] = true;
          if (perto[a.j]) alcance[a.i] = true;
        });
        perto = alcance;
      }
      nos.forEach(function (n, i) { if (!perto[i]) vivo[i] = false; });
    }

    // ---- 7. poda: aresta só existe se os dois lados existem; nó sem ligação sai do desenho
    var temFiltroFoco = !!busca || !!tipoPessoa || temFiltroValor;
    vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });

    var grau = {}, grauFluxo = {};
    function contarGraus() {
      grau = {}; grauFluxo = {};
      vivas.forEach(function (a) {
        grau[a.i] = (grau[a.i] || 0) + 1; grau[a.j] = (grau[a.j] || 0) + 1;
        if (!a.semValor) { grauFluxo[a.i] = (grauFluxo[a.i] || 0) + 1; grauFluxo[a.j] = (grauFluxo[a.j] || 0) + 1; }
      });
    }
    contarGraus();

    // com filtro de foco ativo, o titular que ficou só com o esqueleto (ligado ao banco e a mais
    // nada) vira ruído — sai junto com as contrapartes que o justificavam
    if (temFiltroFoco) {
      nos.forEach(function (n, i) {
        if (vivo[i] && n.titular && !grauFluxo[i]) vivo[i] = false;
      });
      vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });
      contarGraus();
    }
    nos.forEach(function (n, i) { if (vivo[i] && !grau[i]) vivo[i] = false; });
    vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });

    // ---- 8. reindexação
    var nosFiltrados = nos.filter(function (n, i) { return vivo[i]; });
    var idxMap = {};
    nosFiltrados.forEach(function (n, i) { idxMap[n._idx] = i; n._posGrafo = i; });
    var arestasFiltradas = vivas.map(function (a) {
      var copia = {}; for (var k in a) copia[k] = a[k];
      copia.i = idxMap[a.i]; copia.j = idxMap[a.j];
      return copia;
    });

    return { nos: nosFiltrados, arestas: arestasFiltradas, totalNos: nos.length };
  }

  // RIFs grandes chegam a milhares de nós; o layout force-directed abaixo é O(n²) por iteração e
  // trava a aba (e às vezes o navegador) se rodar sobre o grafo inteiro. Por padrão desenha só os
  // nós mais relevantes (titular sempre, depois por nº de ligações) e oferece "Carregar mais".

  // ------------------------------------------------------------------ modo de exibição
  /* O último grafo DETALHADO montado (com instituições, contas e ligações individuais). O modo
     Resumido é só representação visual — as exportações (CSV/XLSX/ANX/GraphML), o resumo financeiro
     e os contadores continuam saindo daqui, então nenhum dado se perde ao desenhar agregado. */
  var gDetalhado = null;
  // snapshot SEM filtros do grafo detalhado: base dos achados do painel (os dados brutos não
  // mudam durante a sessão, então montar uma vez no load é suficiente)
  var gCompleto = null;
  // quantos níveis de entorno a busca puxa: 1 no campo manual, 2 quando o recorte vem de um
  // achado do painel (a entidade sozinha com uma aresta não contextualiza nada)
  var nivelBusca = 1;

  // ------------------------------------------------------------------ motor de desenho
  var grafo = GrafoFluxo.criar({
    cores: CORES_NO, rotulosTipo: ROTULO_NO, icones: ICONES_NO,
    corAresta: COR_ARESTA, tracejado: TRACEJADO_DIR, marcador: MARCADOR_POR_DIR,
    aneis: [
      { classe: "mesa-no-anel-titular", cor: "#6366f1", rotulo: "Titular da conta (pivô do fluxo)",
        externo: true, quando: function (n) { return !!n.titular; } },
      { classe: "pep-no", cor: "#c026d3", rotulo: "PEP", quando: function (n) { return !!n.pep; } }
    ],
    legendaLinhas: [
      ["#16a34a", "Entrada — creditou a conta do titular", false],
      ["#dc2626", "Saída — o titular debitou para a contraparte", false],
      ["#8696a0", "Conta no banco / vínculo sem valor apurado", true]
    ],
    // ordem de corte quando o grafo não cabe: titular primeiro, depois banco, depois grau
    prioridade: function (n) { return n.titular ? 2 : (n.tipo === "instituicao" ? 1 : 0); },
    tamanhoRotulo: function (n) { return (n.titular || n.tipo === "instituicao") ? 22 : 16; },
    fracRotulo: function (a) { return a.tipo === "instituicao" ? 0.68 : 0.32; },
    temFiltros: filtrosGrafoAtivos,
    // ficha da entidade em nova aba (botão ↗ da gaveta); banco e "não identificado" não têm ficha
    fichaURL: function (no) {
      if (no.tipo === "instituicao" || no.tipo === "desconhecido") return null;
      return "ficha_entidade.html?fonte=rif" +
             "&inquerito=" + encodeURIComponent(idInquerito || "") +
             "&rif=" + encodeURIComponent(idRif) +
             "&nome=" + encodeURIComponent(no.rotulo || "") +
             "&doc=" + encodeURIComponent(no.doc || "");
    },
    subtitulo: function (no, nLig, nDes) {
      var partes = [ROTULO_NO[no.tipo] || no.tipo];
      if (no.titular) partes.push("titular da conta");
      if (no.pep) partes.push("PEP");
      partes.push(nLig + (nLig === 1 ? " ligação" : " ligações") +
                  (nDes < nLig ? " (" + nDes + " no desenho)" : ""));
      if (!no._desenhado) partes.push("fora dos nós desenhados");
      return partes.join(" · ");
    },
    rotuloLigacao: function (a, outro, souOrigem) {
      var seta = a.semValor ? " — " : (souOrigem ? " → " : " ← ");
      return (a.rotulo != null ? a.rotulo : "") + seta + U.truncar(outro.rotulo, 38) +
             (a.nLanc ? " (" + a.nLanc + " lanç.)" : "");
    },
    detalhes: function (no, g) {
      var linhas = [], coms = _chaves(no.coms), bancos = _chaves(no.bancos);
      function l(rot, val) { linhas.push([rot, val]); }

      if (no.tipo === "instituicao") {
        l("Tipo", "Instituição comunicante" + (no.semTitular ? " · alguma comunicação sem titular identificado" : ""));
        l("CNPJ", no.doc);
        l("Comunicações", no.nComs);
        l("Titulares com conta aqui", _chaves(no.titulares).length);
        l("Cidade/UF das agências", _chaves(no.ufs).join(", "));
        l("Movimentação comunicada (crédito + débito)", formatarReais(no.valor));
      } else if (no.tipo === "desconhecido") {
        var tit = no.iTitular != null ? g.nos.filter(function (n) { return n._idx === no.iTitular; })[0] : null;
        l("Tipo", "Contraparte não identificada no texto da comunicação");
        l("Direção", no.direcao === "credito" ? "Entrada — creditou a conta" : "Saída — debitado da conta");
        l("Titular", tit ? tit.rotulo : "—");
        l("Valor sem nome no texto", formatarReais(no.direcao === "credito" ? no.credito : no.debito));
        l("Comunicações", coms.length + (coms.length ? " (nº " + coms.join(", ") + ")" : ""));
        linhas.push({ nota: "É a diferença entre o total da comunicação (campos B/C) e o que o texto nomeia. " +
                            "Dá para atribuir parte desse valor a alguém em \"Identificar\", nas tabelas do RIF." });
      } else {
        l("Tipo", (no.tipo === "pj" ? "Pessoa jurídica" : "Pessoa física") +
                  (no.titular ? " · titular da conta" : "") + (no.pep ? " · PEP" : "") +
                  (no.manual ? " · identificado manualmente" : ""));
        l("CPF/CNPJ", no.doc);
        if (_chaves(no.papeis).length) l("Papéis", _chaves(no.papeis).join(", "));
        if (no.titular) {
          l("Entrou na conta (total creditado)", no.entrada ? formatarReais(no.entrada) : "— (sem valor apurado)");
          l("Saiu da conta (total debitado)", no.saida ? formatarReais(no.saida) : "— (sem valor apurado)");
        }
        if (no.autoCredito || no.autoDebito) {
          l("Entre contas do próprio titular",
            "entrada " + formatarReais(no.autoCredito) + " · saída " + formatarReais(no.autoDebito) +
            " (sem aresta: origem e destino são a mesma pessoa)");
        }
        if (no.credito || !no.titular) l("Creditou a conta do titular (entrada)", no.credito ? formatarReais(no.credito) : "— (sem valor apurado)");
        if (no.debito || !no.titular) l("Recebeu da conta do titular (saída)", no.debito ? formatarReais(no.debito) : "— (sem valor apurado)");
        if (no.nLanc) l("Lançamentos", no.nLanc);
        if (bancos.length) l("Bancos", bancos.join(", "));
        if (coms.length) l("Comunicações", coms.length + " (nº " + coms.slice(0, 20).join(", ") + (coms.length > 20 ? "…" : "") + ")");
      }
      return linhas;
    },
    aoRenderizar: function () { atualizarResumoFiltro(); atualizarTotal(); atualizarAchados(); }
  });

  /* Resumido (padrão) x Detalhado: o controle é compartilhado com o SIMBA (GrafoFluxo.controleModo).
     Trocar de modo apenas remontar o desenho a partir dos MESMOS filtros do DOM — nada se perde. */
  var modoAtual = GrafoFluxo.controleModo(function () {
    atualizarDicaGrafo();
    aplicarGrafo();
  });

  // ------------------------------------------------------------------ exportação i2
  /* Os atributos abaixo são os MESMOS campos da gaveta de detalhe do nó (ver `detalhes` acima).
     Sem eles a carta do i2 recebia só "Fulano —(pagou)→ Sicrano": a topologia, sem o CPF que
     identifica a pessoa, sem quanto foi, em quantos lançamentos, por qual banco e em que
     período — que é exatamente o que se leva para o relatório. */
  var CFG_I2 = {
    prefixo: "rif",
    tipos: { instituicao: "Instituicao", pf: "Pessoa Fisica", pj: "Pessoa Juridica", desconhecido: "Nao Identificado" },
    peso: function (no) { return no.tipo === "instituicao" ? no.valor : no.total; },
    // "Não identificado (entradas/saídas)" existe uma vez por titular e direção: no i2 todos
    // apareceriam com o mesmo rótulo. O rótulo exportado leva o titular junto (na tela o nó é
    // curto de propósito, para caber no desenho).
    rotulo: function (no, g) {
      if (no.tipo !== "desconhecido") return no.rotulo;
      var tit = g.nos.filter(function (n) { return n._idx === no.iTitular; })[0];
      return no.rotulo + (tit ? " · " + tit.rotulo : "");
    },
    atributosNo: function (no, g) {
      var coms = _chaves(no.coms), bancos = _chaves(no.bancos);

      if (no.tipo === "instituicao") {
        return {
          "CNPJ": no.doc,
          "Comunicações": no.nComs,
          "Titulares com conta aqui": _chaves(no.titulares).length,
          "Cidade/UF das agências": _chaves(no.ufs).join(", "),
          "Movimentação comunicada": no.valor ? formatarReais(no.valor) : ""
        };
      }
      if (no.tipo === "desconhecido") {
        var tit = no.iTitular != null ? g.nos.filter(function (n) { return n._idx === no.iTitular; })[0] : null;
        return {
          "Direção": no.direcao === "credito" ? "Entrada — creditou a conta" : "Saída — debitado da conta",
          "Titular": tit ? tit.rotulo : "",
          "Valor sem nome no texto": formatarReais(no.direcao === "credito" ? no.credito : no.debito),
          "Comunicações": coms.length ? coms.join(", ") : "",
          "Observação": "Diferença entre o total da comunicação (campos B/C) e o que o texto nomeia"
        };
      }
      return {
        "CPF/CNPJ": no.doc,
        "Papel": (no.titular ? "Titular da conta" : "Contraparte") +
                 (no.pep ? " · PEP" : "") + (no.manual ? " · identificado manualmente" : ""),
        "Papéis no RIF": _chaves(no.papeis).join(", "),
        "Entrou na conta (titular)": no.entrada ? formatarReais(no.entrada) : "",
        "Saiu da conta (titular)": no.saida ? formatarReais(no.saida) : "",
        "Creditou a conta do titular": no.credito ? formatarReais(no.credito) : "",
        "Recebeu da conta do titular": no.debito ? formatarReais(no.debito) : "",
        "Entre contas do próprio titular": (no.autoCredito || no.autoDebito)
          ? "entrada " + formatarReais(no.autoCredito) + " · saída " + formatarReais(no.autoDebito) : "",
        "Lançamentos": no.nLanc || "",
        "Bancos": bancos.join(", "),
        "Comunicações": coms.length ? coms.join(", ") : ""
      };
    },
    atributosVinculo: function (a) {
      var coms = _chaves(a.coms), bancos = _chaves(a.bancos);
      return {
        "Sentido": /credito$/i.test(a.dir || "") ? "Crédito — a contraparte pagou o titular"
                 : /debito$/i.test(a.dir || "") ? "Débito — o titular pagou a contraparte"
                 : (a.papel || "Vínculo sem fluxo de caixa apurado"),
        "Valor": a.semValor ? "— (sem valor apurado)" : formatarReais(a.valor),
        "Lançamentos": a.nLanc || "",
        "Período": U.periodo(a.dtPrimeiro, a.dtUltimo),
        "Bancos": bancos.join(", "),
        "Comunicações": coms.length ? coms.join(", ") : "",
        "Papel declarado": a.papel || ""
      };
    }
  };
  // as exportações saem sempre do grafo detalhado filtrado (o modo Resumido é só o desenho)
  $("#exportar-i2-csv").on("click", function () { GrafoFluxo.exportarCSV(gDetalhado || grafo.dados(), CFG_I2); });
  $("#exportar-i2-xlsx").on("click", function () { GrafoFluxo.exportarXLSX(gDetalhado || grafo.dados(), CFG_I2); });
  $("#exportar-i2-anx").on("click", function () { GrafoFluxo.exportarANX(gDetalhado || grafo.dados(), CFG_I2); });
  $("#exportar-i2-graphml").on("click", function () { GrafoFluxo.exportarGraphML(gDetalhado || grafo.dados(), CFG_I2); });
  $("#exportar-png").on("click", function () {
    var $b = $(this).prop("disabled", true);
    Promise.resolve(grafo.exportarPNG({ prefixo: "rif" }))
      .catch(function () { /* o aviso já foi dado por quem falhou */ })
      .then(function () { $b.prop("disabled", false); });
  });

  function renderGrafo() {
    gDetalhado = construirGrafo();
    var g = gDetalhado;
    if (modoAtual() === GrafoFluxo.MODO.RESUMIDO) {
      // grafo de investigação: banco/conta intermediários saem do desenho e as repetições do
      // mesmo par viram uma relação só (o detalhamento continua na gaveta)
      g = GrafoFluxo.resumir(g, { tecnicoTipos: ["instituicao"], ocultarDirs: ["conta", "cotitular"] });
    }
    grafo.definir(g);
    grafo.render();
  }

  /* ------------------------------------------------------------- achados clicáveis
     Painel à direita do grafo: quem merece o olhar do investigador primeiro, calculado sobre o
     grafo detalhado JÁ FILTRADO (o recorte do KPI respeita o que está na tela). Clicar num card
     joga a entidade no filtro de busca e remonta — é o atalho "x → mostrar só ele e a vizinhança".
     Só contrapartes (titular é o centro, não o achado; banco e "não identificado" têm seus
     próprios painéis — resumo e legenda). */
  function buscarNoGrafo(no) {
    var termo = _soDigitos(no.doc) || no.rotulo;
    var $b = $("#f-grafo-busca");
    var igual = $.trim($b.val() || "") === termo;
    // clicar de novo na MESMA entidade solta o recorte (sem isso o segundo clique no card
    // repetido parecia não fazer nada)
    $b.val(igual ? "" : termo);
    nivelBusca = igual ? 1 : 2;
    aplicarGrafo();
  }

  function atualizarAchados() {
    // sempre sobre o grafo COMPLETO (snapshot do load): os achados descrevem o RIF inteiro, não
    // o recorte atual — senão, depois de um clique, todos os cards passam a apontar para a mesma
    // entidade já filtrada e o painel para de dizer qualquer coisa nova
    var g = gCompleto || gDetalhado;
    if (!g) { GrafoFluxo.renderKPIs([]); return; }
    var util = function (n) {
      return n.tipo !== "instituicao" && n.tipo !== "desconhecido" && !n.titular;
    };
    var kpis = [];
    function melhor(fn, crit) {
      var top = null;
      g.nos.forEach(function (n) {
        if (!util(n)) return;
        var v = fn(n);
        if (top === null || v > crit(top)) top = n;
      });
      return top;
    }
    function push(rotulo, no, nota) {
      if (!no) return;
      kpis.push({ rotulo: rotulo, valor: no.rotulo || "—", nota: nota,
                  aoClicar: function () { buscarNoGrafo(no); } });
    }

    var n1 = melhor(function (n) { return n.total; }, function (t) { return t.total; });
    if (n1 && n1.total > 0.005) push("Maior fluxo total", n1, formatarReais(n1.total));

    var n2 = melhor(function (n) { return n.credito; }, function (t) { return t.credito; });
    if (n2 && n2.credito > 0.005) push("Quem mais creditou no titular", n2,
      formatarReais(n2.credito) + " em " + (n2.nLanc || "?") + " lanç.");

    var n3 = melhor(function (n) { return n.debito; }, function (t) { return t.debito; });
    if (n3 && n3.debito > 0.005) push("Quem mais recebeu do titular", n3,
      formatarReais(n3.debito) + " em " + (n3.nLanc || "?") + " lanç.");

    // ligada a mais de um titular — possível laranja/intermediário comum das contas
    var mul = {};
    g.arestas.forEach(function (a) {
      if (a.dir === "conta" || a.dir === "cotitular" || a.dir === "vinculo") return;
      var ni = g.nos[a.i], nj = g.nos[a.j];
      var tit = ni.titular ? ni : (nj.titular ? nj : null);
      var cp = tit ? (tit === ni ? nj : ni) : null;
      if (!tit || !cp || !util(cp)) return;
      var m = mul[cp.chave] = mul[cp.chave] || { no: cp, tits: {}, valor: 0 };
      m.tits[tit.chave] = true;
      m.valor += num(a.valor);
    });
    var pior = null;
    _chaves(mul).forEach(function (k) {
      var m = mul[k], qt = _chaves(m.tits).length;
      if (qt < 2) return;
      if (!pior || qt > pior.qt || (qt === pior.qt && m.valor > pior.valor)) pior = { no: m.no, qt: qt, valor: m.valor };
    });
    if (pior) push("Ligada a mais de um titular", pior.no,
      pior.qt + " titulares · " + formatarReais(pior.valor));

    var aMax = null;
    g.arestas.forEach(function (a) {
      if (a.semValor || (a.dir !== "credito" && a.dir !== "debito")) return;
      if (!aMax || a.valor > aMax.valor) aMax = a;
    });
    if (aMax) {
      var di = g.nos[aMax.i], dj = g.nos[aMax.j];
      var alvo = di.titular ? dj : di;      // a ficha que importa é a da contraparte
      kpis.push({
        rotulo: "Maior relação em valor", valor: truncar2(di.rotulo) + " → " + truncar2(dj.rotulo),
        nota: formatarReais(aMax.valor) + (aMax.nLanc ? " em " + aMax.nLanc + " lanç." : ""),
        aoClicar: function () { buscarNoGrafo(alvo); }
      });
    }

    var n7 = melhor(function (n) { return n.nLanc; }, function (t) { return t.nLanc; });
    if (n7 && n7.nLanc > 1) push("Mais lançamentos (fracionamento?)", n7,
      n7.nLanc + " lançamentos · " + formatarReais(n7.total));

    GrafoFluxo.renderKPIs(kpis);
  }
  function truncar2(t) { t = String(t || "—"); return t.length > 18 ? t.slice(0, 17) + "…" : t; }

  function atualizarDicaGrafo() {
    if (modoAtual() === GrafoFluxo.MODO.RESUMIDO) {
      $("#gf-dica").text(
        "Modo RESUMIDO (grafo de investigação): as instituições e as contas saem do desenho e as " +
        "repetições do mesmo par, na mesma direção, viram UMA relação com o total em R$ e a " +
        "quantidade de operações. Verde = entrada (creditou o titular), vermelho = saída (o titular " +
        "debitou); espessura ∝ valor (escala logarítmica, para uma operação gigante não apagar o " +
        "resto). Titular continua com anel. Clicar num nó abre a gaveta com as relações agregadas " +
        "dele; clicar numa LINHA abre a relação e as operações que a compõem — nada foi apagado, " +
        "só agrupado (o detalhamento de bancos, contas e comunicações está na gaveta, e as " +
        "exportações continuam saindo do grafo completo). Arraste para mover, role para zoom."
      );
      return;
    }
    $("#gf-dica").text(
      "O fluxo é lido do jeito que as tabelas Entradas/Saídas do RIF descrevem: quem creditou (verde) aponta " +
      "para o TITULAR da conta, e o titular aponta para quem foi debitado (vermelho). A instituição comunicante " +
      "(azul) fica ligada ao titular pela linha tracejada \"Titular da conta\" — ela é onde a conta está, não o " +
      "caminho do dinheiro. A mesma pessoa/empresa é um nó só em todo o RIF (deduplicada por CPF/CNPJ), e o par " +
      "que se repete em várias comunicações vira uma aresta só, com o total somado. Tracejado cinza = vínculo sem " +
      "valor apurado (sócio, procurador, ou papel que só aparece no CSV de Envolvidos). Espessura e tamanho ∝ R$. " +
      "Arraste para mover, role para dar zoom. Clicar num nó abre a gaveta à esquerda e coloca ele e as ligações " +
      "dele em foco (o resto desbota); na gaveta dá para pular de uma ligação para a próxima. Shift+clique acumula: " +
      "com dois ou mais nós em foco a gaveta mostra o que passou entre eles e as contrapartes em comum. Esc, ✕ ou " +
      "um clique no vazio saem do foco."
    );
  }

  // ------------------------------------------------------------- filtros
  var CAMPOS_FILTRO = ["f-grafo-busca", "f-grafo-banco", "f-grafo-pessoa", "f-grafo-data-de", "f-grafo-data-ate",
                       "f-grafo-credito-de", "f-grafo-credito-ate", "f-grafo-debito-de", "f-grafo-debito-ate"];

  function filtrosGrafoAtivos() {
    if ($("#f-grafo-desconhecido").is(":checked")) return true;
    return CAMPOS_FILTRO.some(function (id) { return $.trim($("#" + id).val() || "") !== ""; });
  }

  // Preenche o select de bancos com as instituições comunicantes do RIF (uma opção por banco, com a
  // quantidade de comunicações), ordenadas por quantidade.
  function preencherBancos() {
    var porChave = {};
    dados.comunicacoes.forEach(function (c) {
      var k = chaveBanco(c);
      if (!porChave[k]) porChave[k] = { chave: k, nome: c.no_comunicante || "(sem nome)", n: 0 };
      porChave[k].n++;
    });
    var lista = Object.keys(porChave).map(function (k) { return porChave[k]; })
      .sort(function (a, b) { return b.n - a.n || a.nome.localeCompare(b.nome, "pt-BR"); });
    var $sel = $("#f-grafo-banco");
    var atual = $sel.val();
    $sel.empty().append($("<option>").val("").text("Todos os bancos (" + lista.length + ")"));
    lista.forEach(function (b) {
      $sel.append($("<option>").val(b.chave).text(b.nome + " · " + b.n + (b.n === 1 ? " comunicação" : " comunicações")));
    });
    if (atual) $sel.val(atual);
  }

  // O layout force-directed é O(n²) por iteração e leva alguns segundos num RIF grande, travando a
  // aba enquanto roda. Como agora os selects reaplicam o filtro sozinhos, avisa e deixa o navegador
  // pintar o aviso (setTimeout) antes de entrar no cálculo — senão a tela só congela sem explicação.
  var remontando = false;
  function aplicarGrafo() {
    if (remontando) return;
    remontando = true;
    $("#f-grafo-btn-filtrar, #f-grafo-btn-limpar").prop("disabled", true);
    $("#f-grafo-info").text("Remontando o grafo…");
    setTimeout(function () {
      try {
        grafo.redefinirLimite();
        renderGrafo();
      } finally {
        remontando = false;
        $("#f-grafo-btn-filtrar, #f-grafo-btn-limpar").prop("disabled", false);
      }
    }, 30);
  }

  function atualizarResumoFiltro() {
    // sai do grafo DETALHADO: o resumo financeiro é o mesmo nos dois modos, e no Resumido os
    // bancos saem do desenho — contar "0 bancos" aqui passaria a ideia errada
    var g = gDetalhado || grafo.dados() || { nos: [], arestas: [], totalNos: 0 };
    var ent = 0, tit = 0, inst = 0, desc = 0, credito = 0, debito = 0;
    g.nos.forEach(function (n) {
      if (n.tipo === "instituicao") inst++;
      else if (n.tipo === "desconhecido") desc++;
      else { ent++; if (n.titular) tit++; }
    });
    g.arestas.forEach(function (a) {
      if (a.dir === "credito") credito += a.valor;
      else if (a.dir === "debito") debito += a.valor;
    });
    // soma o que ficou sem aresta (titular como contraparte de si mesmo), para o total bater com o RIF
    g.nos.forEach(function (n) { credito += (n.autoCredito || 0); debito += (n.autoDebito || 0); });
    var partes = [
      tit + (tit === 1 ? " titular" : " titulares"),
      (ent - tit) + " entidades relacionadas",
      inst + (inst === 1 ? " banco" : " bancos")
    ];
    if (desc) partes.push(desc + " não identificado" + (desc === 1 ? "" : "s"));
    partes.push("entradas " + formatarReais(credito));
    partes.push("saídas " + formatarReais(debito));
    $("#f-grafo-info").text(partes.join(" · ") + (filtrosGrafoAtivos() ? " (filtrado)" : ""));
  }

  function limparFiltrosGrafo() {
    CAMPOS_FILTRO.forEach(function (id) { $("#" + id).val(""); });
    $("#f-grafo-desconhecido").prop("checked", false);
    nivelBusca = 1;
    aplicarGrafo();
  }

  $("#f-grafo-btn-filtrar").on("click", aplicarGrafo);
  $("#f-grafo-btn-limpar").on("click", limparFiltrosGrafo);
  $("#f-grafo-banco, #f-grafo-pessoa").on("change", aplicarGrafo);
  $("#f-grafo-desconhecido").on("change", aplicarGrafo);
  // digitar no campo é a busca "manual" de sempre: volta o entorno para 1 nível
  $("#f-grafo-busca").on("keydown", function (e) { nivelBusca = 1; if (e.key === "Enter") aplicarGrafo(); });

  // Máscara monetária para os filtros do grafo
  ["f-grafo-credito-de", "f-grafo-credito-ate", "f-grafo-debito-de", "f-grafo-debito-ate"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) aplicarMascaraMonetaria(el);
  });

  // ---- filtros expostos ao chat de IA (js/utils/grafo_filtros.js)
  // O painel flutuante lê este descritor para saber o que dá para filtrar nesta tela e escreve
  // nos mesmos inputs quando o investigador aceita o recorte que a IA propôs. As opções do select
  // de bancos são lidas do DOM na hora, então o registro pode acontecer antes da carga.
  if (window.GrafoFiltros) {
    GrafoFiltros.registrar({
      pagina: "grafo_rif",
      titulo: "Grafo do RIF",
      descricao: "Fluxo financeiro deste RIF: o titular da conta no meio, quem creditou apontando "
        + "para ele, ele apontando para quem foi debitado, e a instituição comunicante pendurada "
        + "no titular.",
      campos: [
        { nome: "busca", id: "f-grafo-busca", tipo: "texto", rotulo: "Buscar entidade",
          descricao: "Nome ou CPF/CNPJ de UMA entidade. Deixa no desenho só ela e quem se liga a "
            + "ela — é o filtro para 'as relações de fulano'." },
        { nome: "banco", id: "f-grafo-banco", tipo: "select", rotulo: "Banco",
          descricao: "Instituição comunicante da comunicação (campo do RIF)." },
        { nome: "tipo_pessoa", id: "f-grafo-pessoa", tipo: "select", rotulo: "Tipo de pessoa",
          descricao: "Recorta as entidades ligadas ao titular por natureza." },
        { nome: "ocultar_nao_identificados", id: "f-grafo-desconhecido", tipo: "bool",
          rotulo: "Ocultar não identificados",
          descricao: "Esconde o nó que representa o valor que o texto da comunicação não nomeia." },
        { nome: "data_de", id: "f-grafo-data-de", tipo: "data", rotulo: "Operação a partir de" },
        { nome: "data_ate", id: "f-grafo-data-ate", tipo: "data", rotulo: "Operação até" },
        { nome: "credito_min", id: "f-grafo-credito-de", tipo: "moeda", rotulo: "Entrada mínima (R$)",
          descricao: "Só entra no desenho quem creditou ao titular pelo menos este total." },
        { nome: "credito_max", id: "f-grafo-credito-ate", tipo: "moeda", rotulo: "Entrada máxima (R$)" },
        { nome: "debito_min", id: "f-grafo-debito-de", tipo: "moeda", rotulo: "Saída mínima (R$)",
          descricao: "Só entra no desenho quem recebeu do titular pelo menos este total." },
        { nome: "debito_max", id: "f-grafo-debito-ate", tipo: "moeda", rotulo: "Saída máxima (R$)" },
      ],
      aoAplicar: function () { nivelBusca = 1; aplicarGrafo(); },
    });
  }


  // ------------------------------------------------------------- contadores
  function atualizarTotal() {
    var g = grafo.dados() || { nos: [], arestas: [] };
    $("#total-registros").text(g.nos.length.toLocaleString("pt-BR") + " nós · " +
      g.arestas.length.toLocaleString("pt-BR") + " ligações");
  }

  // ------------------------------------------------------------------ carga
  function carregar(aba) {
    return $.getJSON("/api/investigacao/rifs/" + idRif + "/" + aba).done(function (lista) {
      dados[aba] = lista || [];
    });
  }

  function carregarContrapartes() {
    return $.getJSON("/api/investigacao/rifs/" + idRif + "/contrapartes").done(function (lista) {
      contrapartesPorCom = {};
      (lista || []).forEach(function (x) {
        (contrapartesPorCom[x.id_comunicacao_rif] = contrapartesPorCom[x.id_comunicacao_rif] || []).push(x);
      });
    });
  }

  function carregarIdentificacoes() {
    return $.getJSON("/api/investigacao/rifs/" + idRif + "/identificacoes").done(function (lista) {
      identificacoesPorCom = {};
      (lista || []).forEach(function (x) {
        (identificacoesPorCom[x.id_comunicacao_rif] = identificacoesPorCom[x.id_comunicacao_rif] || []).push(x);
      });
    });
  }

  if (!idRif) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  // volta para as tabelas do mesmo RIF
  $("#voltar-rif").attr("href", "rif.html?inquerito=" + encodeURIComponent(idInquerito || "") +
    "&rif=" + encodeURIComponent(idRif));

  // monta o snapshot SEM filtros para os achados (os inputs voltam ao estado anterior — inclusive
  // a busca vinda de ?busca= da ficha — antes do primeiro render de verdade)
  function montarSnapshotAchados() {
    var guardados = CAMPOS_FILTRO.map(function (id) { return $("#" + id).val(); });
    var desc = $("#f-grafo-desconhecido").is(":checked");
    CAMPOS_FILTRO.forEach(function (id) { $("#" + id).val(""); });
    $("#f-grafo-desconhecido").prop("checked", false);
    try { gCompleto = construirGrafo(); } finally {
      CAMPOS_FILTRO.forEach(function (id, k) { $("#" + id).val(guardados[k]); });
      $("#f-grafo-desconhecido").prop("checked", desc);
    }
  }

  $.when(
    carregar("comunicacoes"), carregar("envolvidos"), carregarContrapartes(), carregarIdentificacoes()
  ).always(function () {
    anexarTitulares();
    atualizarTitularRIF();
    atualizarDicaGrafo();
    preencherBancos();
    montarSnapshotAchados();
    renderGrafo();
  });

  // número do RIF no título
  if (idInquerito) {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/rifs").done(function (rifs) {
      var r = (rifs || []).filter(function (x) { return String(x.id_rif) === String(idRif); })[0];
      if (r) $("#rif-numero").text((r.nu_rif ? "nº " + r.nu_rif : "") + (r.no_pasta ? " · " + window.AnalyserUtils.nomeArquivo(r.no_pasta) : ""));
    });
  }

  // exibe CPF/CNPJ e titular do RIF no header
  function atualizarTitularRIF() {
    if (!dados.comunicacoes.length) return;
    var contTitulares = {}, titularMaisComum = null, cpfMaisComum = null, contMax = 0;
    dados.comunicacoes.forEach(function (c) {
      if (c.no_titular) {
        contTitulares[c.no_titular] = (contTitulares[c.no_titular] || 0) + 1;
        if (contTitulares[c.no_titular] > contMax) {
          contMax = contTitulares[c.no_titular];
          titularMaisComum = c.no_titular;
          cpfMaisComum = c.nu_cpf_cnpj_titular;
        }
      }
    });
    if (titularMaisComum) {
      // um RIF costuma ter VÁRIOS titulares (um por comunicação); o cabeçalho mostra o mais
      // frequente e diz quantos outros existem, para não passar a ideia de que o grafo tem um só
      var outros = Object.keys(contTitulares).length - 1;
      $("#rif-titular-nome").text(titularMaisComum + (outros > 0 ? " + " + outros + " outro" + (outros === 1 ? "" : "s") : ""));
      $("#rif-titular-doc").text(txt(cpfMaisComum || "—"));
      $("#rif-titular").show();
    }
  }

})();
