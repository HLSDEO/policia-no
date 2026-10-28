/* Grafo do extrato bancário do SIMBA — página própria (grafo_simba.html).
 *
 * Substituiu a aba "Grafo" de simba.html, que era um diagrama em três colunas
 * (BANCO → TITULAR → CONTRAPARTE) com caixas empilhadas. O desenho em colunas lia bem uma conta
 * de cada vez, mas escondia o que o investigador procura num extrato com dezenas de titulares
 * afastados: QUEM aparece em mais de uma conta. Aqui é o mesmo modelo do grafo do RIF — entidade
 * deduplicada por documento, titular no meio, banco pendurado — desenhado pelo motor comum em
 * js/utils/grafo_fluxo.js.
 *
 * Os dados vêm prontos de /api/investigacao/simba/{id}/grafo, que já agrega por PAR de pessoas em
 * SQL. Isso importa: o R304 tem uma linha por PERNA do lançamento (ver simba_utils), então somar
 * no navegador daria número errado — quem faz a conta é o banco de dados.
 */
(function () {
  "use strict";

  var U = GrafoFluxo.util;
  var num = U.num, formatarReais = U.formatarReais, abrevReais = U.abrevReais;
  var soDigitos = U.soDigitos, chaves = U.chaves, truncar = U.truncar;

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idExtrato = paramUrl("simba");
  var idInquerito = paramUrl("inquerito");

  var resposta = null;        // resposta crua do endpoint (fonte da verdade)
  var timerBusca = null;

  // ---------------------------------------------------------------- tipos e cores
  var CORES_NO = { instituicao: "#2563eb", pf: "#16a34a", pj: "#d97706", agregado: "#6b7280" };
  var ROTULO_NO = {
    instituicao: "Instituição (banco da conta afastada)", pf: "Pessoa física", pj: "Pessoa jurídica",
    agregado: "Demais contrapartes (somadas)"
  };
  var ICONES_NO = {
    instituicao: '<rect x="3" y="9" width="18" height="11" rx="1"/><path d="M3 9l9-5 9 5"/><line x1="7" y1="12" x2="7" y2="17"/><line x1="12" y1="12" x2="12" y2="17"/><line x1="17" y1="12" x2="17" y2="17"/>',
    pf: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    pj: '<rect x="5" y="3" width="14" height="18" rx="1"/><line x1="9" y1="7" x2="9.01" y2="7"/><line x1="15" y1="7" x2="15.01" y2="7"/><line x1="9" y1="11" x2="9.01" y2="11"/><line x1="15" y1="11" x2="15.01" y2="11"/><line x1="10" y1="21" x2="14" y2="21"/>',
    agregado: '<circle cx="12" cy="12" r="9"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="12" y1="8" x2="12" y2="16"/>'
  };
  var COR_ARESTA = { credito: "#16a34a", debito: "#dc2626", interna: "#7c3aed", conta: "#8696a0" };
  var TRACEJADO_DIR = { conta: true };
  var MARCADOR_POR_DIR = { credito: "credito", debito: "debito", interna: "interna" };

  // ---------------------------------------------------------------- construção do grafo
  /* O endpoint devolve o titular como "T:<chave>" e a contraparte como "C:<chave>" — a MESMA
     pessoa aparece duas vezes quando ela é titular de uma conta afastada e contraparte de outra
     (é justamente o que `in_titular_no_extrato` marca, e o caso mais interessante do extrato).
     Aqui os dois viram UM nó, casando pela chave sem o prefixo. Sem isso o grafo repetiria a
     pessoa e esconderia exatamente o vínculo que se quer enxergar. */
  function chaveEntidade(id) { return String(id || "").replace(/^[TC]:/, ""); }

  function tipoPorDoc(doc, tpPessoa) {
    if (soDigitos(doc).length === 14) return "pj";
    if (tpPessoa === "PJ") return "pj";
    return "pf";
  }

  function construirGrafo() {
    if (!resposta || !resposta.nos) return { nos: [], arestas: [] };

    var tipoContraparte = $("#f-grafo-cp-tipo").val() || "";
    var creditoDe = U.parseValorBR($("#f-grafo-credito-de").val());
    var creditoAte = U.parseValorBR($("#f-grafo-credito-ate").val());
    var debitoDe = U.parseValorBR($("#f-grafo-debito-de").val());
    var debitoAte = U.parseValorBR($("#f-grafo-debito-ate").val());
    var semAgregado = $("#f-grafo-agregado").is(":checked");
    var temFiltroValor = isFinite(creditoDe) || isFinite(creditoAte) || isFinite(debitoDe) || isFinite(debitoAte);

    var nos = [], idxPorChave = {}, idxPorId = {};

    function novoNo(chave, base) {
      var idx = idxPorChave[chave];
      if (idx === undefined) {
        idx = nos.length; idxPorChave[chave] = idx;
        base.chave = chave;
        base.credito = 0;   // R$ que este nó creditou na conta de um titular afastado
        base.debito = 0;    // R$ que este nó recebeu da conta de um titular afastado
        base.entrada = 0;   // como titular: R$ que entraram na conta afastada dele
        base.saida = 0;     // como titular: R$ que saíram dela
        base.nLanc = 0;
        base.autoCredito = 0;  // a mesma entidade dos dois lados do movimento: transferência
        base.autoDebito = 0;   // entre contas dela mesma, não vira aresta (seria um laço)
        base.bancos = {};
        base.contas = 0;
        nos.push(base);
      }
      return idx;
    }

    resposta.nos.forEach(function (n) {
      var idx;
      if (n.tipo === "banco") {
        idx = novoNo("b:" + n.rotulo, {
          tipo: "instituicao", rotulo: n.rotulo, doc: "", titular: false,
          valor: num(n.vl_entrada) + num(n.vl_saida), nContas: num(n.qt_contas), titulares: {}
        });
      } else if (n.tipo === "outros") {
        idx = novoNo("agregado", {
          tipo: "agregado", rotulo: "Demais contrapartes", doc: "", titular: false,
          qtAgregados: num(n.qt_agregados)
        });
        // do ponto de vista do agregado, o endpoint já inverteu os sentidos
        nos[idx].credito += num(n.vl_saida);
        nos[idx].debito += num(n.vl_entrada);
        nos[idx].nLanc += num(n.qt_lancamentos);
      } else {
        idx = novoNo("e:" + chaveEntidade(n.id), {
          tipo: tipoPorDoc(n.cpf_cnpj, n.tp_pessoa), rotulo: n.rotulo || "—",
          doc: n.cpf_cnpj || "", titular: false
        });
        var no = nos[idx];
        if (!no.doc && n.cpf_cnpj) no.doc = n.cpf_cnpj;
        if (soDigitos(n.cpf_cnpj).length === 14) no.tipo = "pj";
        if (n.tipo === "titular") {
          no.titular = true;
          no.entrada += num(n.vl_entrada);
          no.saida += num(n.vl_saida);
          if (n.in_passagem) no.passagem = true;
          if (n.banco) no.bancos[n.banco] = true;
        } else {
          // contraparte: o endpoint já entrega os valores do ponto de vista dela
          no.credito += num(n.vl_saida);      // ela pagou o titular
          no.debito += num(n.vl_entrada);     // ela recebeu do titular
          if (n.interna) no.interna = true;
        }
        no.nLanc += num(n.qt_lancamentos);
      }
      idxPorId[n.id] = idx;
    });

    // ---- arestas
    /* Uma aresta de fluxo é sempre "R$ X foi de ORIGEM para DESTINO", e por isso a chave é o par
       ORDENADO — não o sentido do ponto de vista de quem reportou.
       O motivo: quando os DOIS lados têm conta afastada, o R304 traz a mesma transferência duas
       vezes (débito no extrato de quem pagou, crédito no extrato de quem recebeu), e o endpoint
       devolve as duas como pares distintos (T:A/C:B e T:B/C:A). Somar as duas contaria o mesmo
       dinheiro em dobro e desenharia duas setas no mesmo sentido, uma verde e uma vermelha.
       Daí o MÁXIMO em vez da soma: os dois relatos são a mesma quantia, e o maior é a leitura
       segura se algum extrato cobrir período menor que o outro. */
    var arestas = [], porChave = {};
    function ligar(i, j, dir, papel, valor, lanc, extra) {
      if (i === undefined || j === undefined || i === j) return;
      var chave = (dir === "conta" ? "conta|" : "fluxo|") + i + "|" + j;
      var a = porChave[chave];
      if (!a) {
        a = { i: i, j: j, dir: dir, papel: papel, valor: 0, nLanc: 0,
              semValor: true, contas: 0, dtPrimeiro: null, dtUltimo: null };
        porChave[chave] = a; arestas.push(a);
      }
      a.valor = Math.max(a.valor, num(valor));
      a.nLanc = Math.max(a.nLanc, num(lanc));
      if (a.valor > 0.005) a.semValor = false;
      if (extra) {
        a.contas += num(extra.contas);
        if (extra.dtPrimeiro && (!a.dtPrimeiro || extra.dtPrimeiro < a.dtPrimeiro)) a.dtPrimeiro = extra.dtPrimeiro;
        if (extra.dtUltimo && (!a.dtUltimo || extra.dtUltimo > a.dtUltimo)) a.dtUltimo = extra.dtUltimo;
      }
    }

    resposta.arestas.forEach(function (a) {
      var io = idxPorId[a.origem], id = idxPorId[a.destino];
      if (io === undefined || id === undefined) return;
      if (a.tipo === "conta") {
        // banco → titular: é onde a conta está, não caminho de dinheiro (tracejado, sem valor)
        ligar(io, id, "conta", "Conta afastada", 0, 0, { contas: a.qt_contas });
        nos[io].titulares[id] = true;
        nos[id].contas += num(a.qt_contas);
        if (nos[io].rotulo) nos[id].bancos[nos[io].rotulo] = true;
        return;
      }
      // movimento: a origem é o TITULAR e o destino a contraparte. O par costuma ter os dois
      // sentidos, e cada um vira sua própria aresta — o motor desloca as duas em paralelo para
      // não caírem uma sobre a outra.
      var extra = { dtPrimeiro: a.dt_primeiro, dtUltimo: a.dt_ultimo };
      // Depois da fusão T:/C:, um movimento entre a pessoa e ela mesma (dinheiro andando entre
      // contas do próprio titular, em bancos diferentes do extrato) vira um LAÇO, que este
      // desenho não traça. Não pode simplesmente sumir: no extrato de teste são R$ 137 mi de
      // entrada e R$ 70 mi de saída — mais que o total desenhado. Fica registrado no nó e entra
      // no resumo, como no grafo do RIF.
      if (io === id) {
        nos[io].autoCredito += num(a.vl_entrada);
        nos[io].autoDebito += num(a.vl_saida);
        nos[io].nLancAuto = num(nos[io].nLancAuto) + num(a.qt_lancamentos);
        return;
      }
      if (num(a.vl_entrada) > 0.005) ligar(id, io, "credito", "Creditou", a.vl_entrada, a.qt_lancamentos, extra);
      if (num(a.vl_saida) > 0.005) ligar(io, id, "debito", "Recebeu", a.vl_saida, a.qt_lancamentos, extra);
    });

    /* Cor e papel saem de quem está nas pontas, não de quem reportou: dinheiro que ENTRA numa
       conta afastada é verde, que SAI dela é vermelho, e entre dois afastados é roxo — este
       último é o achado do extrato (dinheiro circulando entre os próprios investigados) e antes
       se disfarçava de "entrada de um" e "saída do outro" ao mesmo tempo. */
    arestas.forEach(function (a) {
      if (a.dir === "conta") { a.cor = COR_ARESTA.conta; return; }
      var origem = nos[a.i], destino = nos[a.j];
      if (origem.titular && destino.titular) { a.dir = "interna"; a.papel = "Entre afastados"; }
      else if (destino.titular) { a.dir = "credito"; a.papel = "Creditou"; }
      else { a.dir = "debito"; a.papel = "Recebeu"; }
      a.cor = COR_ARESTA[a.dir];
    });

    // ---- escala e rótulos
    nos.forEach(function (n) {
      n.total = n.tipo === "instituicao" ? n.valor : (n.credito + n.debito + n.entrada + n.saida);
    });
    var maxCom = 0, maxEnt = 0, maxAresta = 0;
    nos.forEach(function (n) {
      if (n.tipo === "instituicao") { if (n.total > maxCom) maxCom = n.total; }
      else if (n.total > maxEnt) maxEnt = n.total;
    });
    arestas.forEach(function (a) { if (a.valor > maxAresta) maxAresta = a.valor; });
    nos.forEach(function (n) {
      if (n.tipo === "instituicao") n.r = 15 + 12 * (maxCom ? Math.sqrt(n.total / maxCom) : 0);
      else n.r = (n.titular ? 12 : 10) + 11 * (maxEnt ? Math.sqrt(n.total / maxEnt) : 0);
    });
    arestas.forEach(function (a) {
      a.largura = 1.2 + 5.5 * (maxAresta ? Math.sqrt(a.valor / maxAresta) : 0);
      a.rotulo = a.semValor ? a.papel : (a.papel + " · " + abrevReais(a.valor));
    });
    nos.forEach(function (n, i) { n._idx = i; });

    // ---- filtros de cliente (os do servidor já vieram aplicados na resposta)
    var vivas = arestas.filter(function (a) {
      if (a.semValor) return true;
      if (a.dir === "credito" || a.dir === "interna") {
        if (isFinite(creditoDe) && a.valor < creditoDe) return false;
        if (isFinite(creditoAte) && a.valor > creditoAte) return false;
      }
      if (a.dir === "debito" || a.dir === "interna") {
        if (isFinite(debitoDe) && a.valor < debitoDe) return false;
        if (isFinite(debitoAte) && a.valor > debitoAte) return false;
      }
      return true;
    });

    var vivo = nos.map(function () { return true; });
    if (semAgregado) nos.forEach(function (n, i) { if (n.tipo === "agregado") vivo[i] = false; });

    // "Tipo de pessoa" incide sobre a CONTRAPARTE de cada ligação (o lado que não é o titular) —
    // filtrar nó a nó deixaria o titular e o banco na tela mesmo sem nenhuma contraparte casando
    if (tipoContraparte) {
      vivas = vivas.filter(function (a) {
        if (a.dir === "conta") return true;
        var n = nos[a.dir === "debito" || a.dir === "interna" ? a.j : a.i];
        if (!n || n.tipo === "instituicao") return true;
        if (n.tipo === "agregado") return false;
        return n.tipo === tipoContraparte;
      });
    }

    var temFoco = !!tipoContraparte || temFiltroValor;
    vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });

    var grau = {}, grauFluxo = {};
    function contar() {
      grau = {}; grauFluxo = {};
      vivas.forEach(function (a) {
        grau[a.i] = (grau[a.i] || 0) + 1; grau[a.j] = (grau[a.j] || 0) + 1;
        if (!a.semValor) { grauFluxo[a.i] = (grauFluxo[a.i] || 0) + 1; grauFluxo[a.j] = (grauFluxo[a.j] || 0) + 1; }
      });
    }
    contar();
    if (temFoco) {
      nos.forEach(function (n, i) { if (vivo[i] && n.titular && !grauFluxo[i]) vivo[i] = false; });
      vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });
      contar();
    }
    nos.forEach(function (n, i) { if (vivo[i] && !grau[i]) vivo[i] = false; });
    vivas = vivas.filter(function (a) { return vivo[a.i] && vivo[a.j]; });

    var nosFiltrados = nos.filter(function (n, i) { return vivo[i]; });
    var mapa = {};
    nosFiltrados.forEach(function (n, i) { mapa[n._idx] = i; n._posGrafo = i; });
    var arestasFiltradas = vivas.map(function (a) {
      var c = {}; for (var k in a) c[k] = a[k];
      c.i = mapa[a.i]; c.j = mapa[a.j];
      return c;
    });
    return { nos: nosFiltrados, arestas: arestasFiltradas };
  }

  // ---------------------------------------------------------------- motor
  var grafo = GrafoFluxo.criar({
    cores: CORES_NO, rotulosTipo: ROTULO_NO, icones: ICONES_NO,
    corAresta: COR_ARESTA, tracejado: TRACEJADO_DIR, marcador: MARCADOR_POR_DIR,
    aneis: [
      { classe: "mesa-no-anel-titular", cor: "#6366f1", rotulo: "Titular de conta afastada",
        externo: true, quando: function (n) { return !!n.titular; } },
      // Depois da fusão T:/C: quem é titular numa conta E contraparte de outra vira um nó só, e
      // é o achado do extrato — o dinheiro andando entre os próprios investigados. O anel marca
      // esse caso (titular que também movimentou como contraparte) e também a contraparte que o
      // backend marcou como titular afastado mas cuja conta ficou fora deste recorte.
      { classe: "pep-no", cor: "#c026d3", rotulo: "Também é titular afastado (aparece nos dois papéis)",
        quando: function (n) { return n.titular ? !!(n.credito || n.debito) : !!n.interna; } },
      { classe: "mesa-no-anel-titular", cor: "#d97706", rotulo: "Conta de passagem (entra ~= sai)",
        quando: function (n) { return !!n.passagem; } }
    ],
    legendaLinhas: [
      ["#16a34a", "Entrada — creditou a conta do titular", false],
      ["#dc2626", "Saída — o titular pagou a contraparte", false],
      ["#7c3aed", "Entre dois titulares afastados (mesmo extrato)", false],
      ["#8696a0", "Conta afastada no banco", true]
    ],
    prioridade: function (n) { return n.titular ? 2 : (n.tipo === "instituicao" ? 1 : 0); },
    tamanhoRotulo: function (n) { return (n.titular || n.tipo === "instituicao") ? 22 : 16; },
    fracRotulo: function (a) { return a.tipo === "instituicao" ? 0.68 : 0.32; },
    temFiltros: filtrosAtivos,
    // ficha da entidade em nova aba (botão ↗ da gaveta); banco e "demais contrapartes" não têm ficha
    fichaURL: function (no) {
      if (no.tipo === "instituicao" || no.tipo === "agregado") return null;
      return "ficha_entidade.html?fonte=simba" +
             "&inquerito=" + encodeURIComponent(idInquerito || "") +
             "&simba=" + encodeURIComponent(idExtrato) +
             "&nome=" + encodeURIComponent(no.rotulo || "") +
             "&doc=" + encodeURIComponent(no.doc || "");
    },
    subtitulo: function (no, nLig, nDes) {
      var partes = [ROTULO_NO[no.tipo] || no.tipo];
      if (no.titular) partes.push("titular de conta afastada");
      if (no.passagem) partes.push("conta de passagem");
      if (no.interna && !no.titular) partes.push("também é titular afastado");
      partes.push(nLig + (nLig === 1 ? " ligação" : " ligações") +
                  (nDes < nLig ? " (" + nDes + " no desenho)" : ""));
      if (!no._desenhado) partes.push("fora dos nós desenhados");
      return partes.join(" · ");
    },
    rotuloLigacao: function (a, outro, souOrigem) {
      var seta = a.semValor ? " — " : (souOrigem ? " → " : " ← ");
      return (a.rotulo != null ? a.rotulo : "") + seta + truncar(outro.rotulo, 38) +
             (a.nLanc ? " (" + a.nLanc + " lanç.)" : "");
    },
    detalhes: function (no) {
      var linhas = [], bancos = chaves(no.bancos);
      function l(rot, val) { linhas.push([rot, val]); }
      if (no.tipo === "instituicao") {
        l("Tipo", "Instituição com conta afastada neste extrato");
        l("Contas afastadas", no.nContas);
        l("Titulares com conta aqui", chaves(no.titulares).length);
        l("Movimentação das contas (crédito + débito)", formatarReais(no.valor));
      } else if (no.tipo === "agregado") {
        l("Tipo", "Contrapartes fora do limite exibido, somadas num nó só");
        l("Quantas", no.qtAgregados);
        l("Creditaram os titulares", formatarReais(no.credito));
        l("Receberam dos titulares", formatarReais(no.debito));
        l("Lançamentos", no.nLanc);
        linhas.push({ nota: "Aumente “Contrapartes no desenho” nos filtros para abrir parte deste grupo em nós próprios." });
      } else {
        l("Tipo", (no.tipo === "pj" ? "Pessoa jurídica" : "Pessoa física") +
                  (no.titular ? " · titular de conta afastada" : "") +
                  (no.passagem ? " · conta de passagem" : "") +
                  (no.interna && !no.titular ? " · também é titular afastado" : ""));
        l("CPF/CNPJ", no.doc);
        if (no.titular) {
          l("Contas afastadas", no.contas || "—");
          l("Entrou na conta (crédito)", no.entrada ? formatarReais(no.entrada) : "—");
          l("Saiu da conta (débito)", no.saida ? formatarReais(no.saida) : "—");
        }
        if (no.autoCredito || no.autoDebito) {
          l("Entre contas da própria entidade",
            "entrada " + formatarReais(no.autoCredito) + " · saída " + formatarReais(no.autoDebito) +
            (no.nLancAuto ? " · " + no.nLancAuto + " lançamentos" : "") +
            " (sem aresta: origem e destino são a mesma pessoa)");
        }
        if (no.credito || !no.titular) l("Creditou titulares afastados", no.credito ? formatarReais(no.credito) : "—");
        if (no.debito || !no.titular) l("Recebeu de titulares afastados", no.debito ? formatarReais(no.debito) : "—");
        if (no.nLanc) l("Lançamentos", no.nLanc);
        if (bancos.length) l("Bancos", bancos.join(", "));
      }
      return linhas;
    },
    // atalho para o extrato: a página antiga levava o clique no nó direto para os lançamentos
    // daquela pessoa; com a gaveta isso vira um botão, sem perder o caminho
    acoes: function (no) {
      if (no.tipo === "instituicao" || no.tipo === "agregado") return [];
      var termo = no.doc || no.rotulo;
      if (!termo) return [];
      return [{
        texto: "Ver lançamentos",
        href: "simba.html?inquerito=" + encodeURIComponent(idInquerito || "") +
              "&simba=" + encodeURIComponent(idExtrato) +
              "&aba=lancamentos&busca=" + encodeURIComponent(termo)
      }];
    },
    aoRenderizar: function () { atualizarResumo(); atualizarTotal(); atualizarAchados(); }
  });

  /* Resumido (padrão) x Detalhado — mesma mecânica do RIF (GrafoFluxo.controleModo/resumir).
     gDetalhado guarda o último grafo completo: modo Resumido é só representação, e exportações,
     resumo financeiro e contadores continuam saindo do detalhado, sem perder dado nenhum. */
  var gDetalhado = null;
  var gCompleto = null;   // snapshot sem filtros (base dos achados), montado na 1ª carga
  // níveis de entorno da busca: 1 no campo manual; 2 num clique de achado — com contexto o
  // servidor traz também o que os TITULARES do match tocam (a entidade sozinha, com uma aresta,
  // não contextualiza nada)
  var nivelBusca = 1;
  var modoAtual = GrafoFluxo.controleModo(function () {
    atualizarDica();
    remontar();
  });

  /* Os atributos abaixo são os MESMOS campos da gaveta de detalhe (ver `detalhes` acima), mais o
     PERÍODO da movimentação, que a aresta já acumula (dtPrimeiro/dtUltimo, vindos do R304) e o
     desenho não tem onde mostrar. É o que faz a carta do i2 dizer "R$ 45.320,00 em 12
     lançamentos, entre 03/02/2026 e 28/07/2026" no lugar de um "Creditou" solto. */
  var CFG_I2 = {
    prefixo: "simba",
    tipos: { instituicao: "Instituicao", pf: "Pessoa Fisica", pj: "Pessoa Juridica", agregado: "Agrupado" },
    peso: function (no) { return no.total || 0; },
    atributosNo: function (no) {
      if (no.tipo === "instituicao") {
        return {
          "Contas afastadas": no.nContas,
          "Titulares com conta aqui": chaves(no.titulares).length,
          "Movimentação das contas": no.valor ? formatarReais(no.valor) : ""
        };
      }
      if (no.tipo === "agregado") {
        return {
          "Contrapartes somadas": no.qtAgregados,
          "Creditaram os titulares": formatarReais(no.credito),
          "Receberam dos titulares": formatarReais(no.debito),
          "Lançamentos": no.nLanc || "",
          "Observação": "Contrapartes fora do limite exibido, somadas num nó só"
        };
      }
      return {
        "CPF/CNPJ": no.doc,
        "Papel": (no.titular ? "Titular de conta afastada" : "Contraparte") +
                 (no.interna && !no.titular ? " · também é titular afastado" : ""),
        "Contas afastadas": no.titular ? (no.contas || "") : "",
        "Entrou na conta (crédito)": no.entrada ? formatarReais(no.entrada) : "",
        "Saiu da conta (débito)": no.saida ? formatarReais(no.saida) : "",
        "Creditou titulares afastados": no.credito ? formatarReais(no.credito) : "",
        "Recebeu de titulares afastados": no.debito ? formatarReais(no.debito) : "",
        "Entre contas da própria entidade": (no.autoCredito || no.autoDebito)
          ? "entrada " + formatarReais(no.autoCredito) + " · saída " + formatarReais(no.autoDebito) +
            (no.nLancAuto ? " · " + no.nLancAuto + " lançamentos" : "") : "",
        "Lançamentos": no.nLanc || "",
        "Bancos": chaves(no.bancos).join(", ")
      };
    },
    atributosVinculo: function (a) {
      return {
        "Sentido": a.dir === "credito" ? "Crédito — a contraparte pagou o titular"
                 : a.dir === "debito" ? "Débito — o titular pagou a contraparte"
                 : a.dir === "interna" ? "Entre dois titulares afastados do mesmo extrato"
                 : "Conta afastada na instituição",
        "Valor": a.semValor ? "— (sem valor apurado)" : formatarReais(a.valor),
        "Lançamentos": a.nLanc || "",
        "Período": U.periodo(a.dtPrimeiro, a.dtUltimo),
        "Contas envolvidas": a.contas || ""
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
    Promise.resolve(grafo.exportarPNG({ prefixo: "simba" }))
      .catch(function () { /* o aviso já foi dado por quem falhou */ })
      .then(function () { $b.prop("disabled", false); });
  });

  function renderGrafo() {
    gDetalhado = construirGrafo();
    var g = gDetalhado;
    if (modoAtual() === GrafoFluxo.MODO.RESUMIDO) {
      // grafo de investigação: o banco sai do desenho e as repetições do mesmo par viram uma
      // relação só (o detalhamento continua na gaveta)
      g = GrafoFluxo.resumir(g, { tecnicoTipos: ["instituicao"], ocultarDirs: ["conta"] });
    }
    grafo.definir(g);
    grafo.render();
  }

  // ---------------------------------------------------------------- filtros
  // Os de servidor mudam a CONSULTA (e o ranking das maiores contrapartes), então recarregam;
  // os de cliente só reduzem o que já veio, e remontam o desenho sem ir ao banco.
  var CAMPOS_SERVIDOR = ["f-grafo-busca", "f-grafo-banco", "f-grafo-tp", "f-grafo-direcao", "f-grafo-limite"];
  var CAMPOS_CLIENTE = ["f-grafo-cp-tipo", "f-grafo-credito-de", "f-grafo-credito-ate",
                        "f-grafo-debito-de", "f-grafo-debito-ate"];

  function filtrosAtivos() {
    if ($("#f-grafo-internas").is(":checked") || $("#f-grafo-agregado").is(":checked")) return true;
    return CAMPOS_SERVIDOR.concat(CAMPOS_CLIENTE).some(function (id) {
      if (id === "f-grafo-limite") return false;         // o limite tem valor padrão, não é filtro
      return $.trim($("#" + id).val() || "") !== "";
    });
  }

  var carregando = false;
  function carregar() {
    if (carregando) return;
    carregando = true;
    $("#f-grafo-btn-filtrar, #f-grafo-btn-limpar").prop("disabled", true);
    $("#f-grafo-info").text("Consultando o extrato…");

    var params = {};
    var termo = $.trim($("#f-grafo-busca").val() || "");
    if (termo) params.termo = termo;
    // recorte vindo de um ACHADO do painel: pede ao servidor 1 nível de entorno dos titulares
    // que o termo toca (senão a entidade vem sozinha, com uma aresta só)
    if (termo && nivelBusca > 1) params.contexto = true;
    if ($("#f-grafo-banco").val()) params.banco = $("#f-grafo-banco").val();
    if ($("#f-grafo-tp").val()) params.tp_pessoa = $("#f-grafo-tp").val();
    if ($("#f-grafo-direcao").val()) params.direcao = $("#f-grafo-direcao").val();
    if ($("#f-grafo-limite").val()) params.limite = $("#f-grafo-limite").val();
    if ($("#f-grafo-internas").is(":checked")) params.so_internas = true;

    $.getJSON("/api/investigacao/simba/" + idExtrato + "/grafo", params)
      .done(function (resp) {
        resposta = resp || { nos: [], arestas: [] };
        preencherBancos();
        // snapshot do extrato inteiro (1ª carga, sem filtro de servidor): base dos achados
        if (!gCompleto && !params.termo && !params.banco && !params.tp_pessoa &&
            !params.direcao && !params.so_internas) {
          gCompleto = construirGrafo();
        }
        remontar();
      })
      .fail(function () { $("#f-grafo-info").text("erro ao consultar o extrato"); })
      .always(function () {
        carregando = false;
        $("#f-grafo-btn-filtrar, #f-grafo-btn-limpar").prop("disabled", false);
      });
  }

  // O layout é O(n²) e leva alguns segundos num extrato grande, travando a aba enquanto roda —
  // avisa e deixa o navegador pintar o aviso antes de entrar no cálculo.
  var remontando = false;
  function remontar() {
    if (remontando) return;
    remontando = true;
    $("#f-grafo-info").text("Remontando o grafo…");
    setTimeout(function () {
      try {
        grafo.redefinirLimite();
        renderGrafo();
      } finally { remontando = false; }
    }, 30);
  }

  function preencherBancos() {
    var $sel = $("#f-grafo-banco");
    if ($sel.find("option").length > 1 || !resposta.bancos) return;
    resposta.bancos.forEach(function (b) { $("<option>").val(b).text(b).appendTo($sel); });
  }

  function atualizarResumo() {
    // sai do grafo DETALHADO: o resumo financeiro é o mesmo nos dois modos, e no Resumido os
    // bancos saem do desenho — contar "0 bancos" aqui passaria a ideia errada
    var g = gDetalhado || grafo.dados() || { nos: [], arestas: [] };
    var tit = 0, ent = 0, inst = 0, agreg = 0, credito = 0, debito = 0;
    g.nos.forEach(function (n) {
      if (n.tipo === "instituicao") inst++;
      else if (n.tipo === "agregado") agreg++;
      else { ent++; if (n.titular) tit++; }
    });
    var interno = 0;
    g.arestas.forEach(function (a) {
      if (a.dir === "credito") credito += a.valor;
      else if (a.dir === "debito") debito += a.valor;
      else if (a.dir === "interna") interno += a.valor;
    });
    // soma o que ficou sem aresta (entidade dos dois lados do movimento), senão o total do
    // resumo fica menor que o do extrato sem nenhuma explicação na tela
    g.nos.forEach(function (n) { credito += num(n.autoCredito); debito += num(n.autoDebito); });
    var partes = [
      tit + (tit === 1 ? " titular afastado" : " titulares afastados"),
      (ent - tit) + " contrapartes",
      inst + (inst === 1 ? " banco" : " bancos")
    ];
    if (agreg) partes.push("+ demais somadas");
    partes.push("entradas " + formatarReais(credito));
    partes.push("saídas " + formatarReais(debito));
    if (interno) partes.push("entre afastados " + formatarReais(interno));
    $("#f-grafo-info").text(partes.join(" · ") + (filtrosAtivos() ? " (filtrado)" : ""));

    // o que ficou de fora do desenho precisa aparecer escrito: um grafo truncado sem aviso faz
    // parecer que aquelas contrapartes são todas que existem
    var r = (resposta && resposta.resumo) || {};
    if (r.qt_contrapartes_agrupadas) {
      $("#gf-nota").text(
        "Mostrando as " + r.qt_contrapartes_exibidas + " maiores contrapartes de " +
        r.qt_contrapartes_total + " (" + formatarReais(r.vl_exibido) + "). As outras " +
        r.qt_contrapartes_agrupadas + ", que somam " + formatarReais(r.vl_agrupado) +
        ", estão reunidas no nó “Demais contrapartes”."
      ).show();
    } else if (r.qt_contrapartes_total) {
      $("#gf-nota").text("Todas as " + r.qt_contrapartes_total + " contrapartes destes filtros estão no desenho.").show();
    } else {
      $("#gf-nota").hide();
    }
  }

  function atualizarTotal() {
    var g = grafo.dados() || { nos: [], arestas: [] };
    $("#total-registros").text(g.nos.length.toLocaleString("pt-BR") + " nós · " +
      g.arestas.length.toLocaleString("pt-BR") + " ligações");
  }

  function limpar() {
    CAMPOS_SERVIDOR.concat(CAMPOS_CLIENTE).forEach(function (id) {
      if (id !== "f-grafo-limite") $("#" + id).val("");
    });
    $("#f-grafo-internas, #f-grafo-agregado").prop("checked", false);
    nivelBusca = 1;
    carregar();
  }

  $("#f-grafo-btn-filtrar").on("click", carregar);
  $("#f-grafo-btn-limpar").on("click", limpar);
  $("#f-grafo-banco, #f-grafo-tp, #f-grafo-direcao, #f-grafo-limite, #f-grafo-internas").on("change", carregar);
  $("#f-grafo-cp-tipo, #f-grafo-agregado").on("change", remontar);
  $("#f-grafo-busca").on("input", function () {
    nivelBusca = 1;   // digitar é a busca manual de sempre: 1 nível de entorno
    clearTimeout(timerBusca);
    timerBusca = setTimeout(carregar, 450);   // a busca vai ao banco: um pedido por tecla é demais
  });
  ["f-grafo-credito-de", "f-grafo-credito-ate", "f-grafo-debito-de", "f-grafo-debito-ate"].forEach(function (id) {
    U.aplicarMascaraMonetaria(document.getElementById(id));
  });

  // ---- filtros expostos ao chat de IA (js/utils/grafo_filtros.js)
  // aoAplicar é sempre `carregar` (não `remontar`): metade dos campos daqui é filtro de servidor,
  // e como a IA pode mexer nos dois grupos de uma vez, refazer a consulta cobre os dois casos —
  // renderGrafo, chamado no fim de carregar, relê os filtros de cliente ao montar o grafo.
  if (window.GrafoFiltros) {
    GrafoFiltros.registrar({
      pagina: "grafo_simba",
      titulo: "Grafo do extrato do SIMBA",
      descricao: "Fluxo financeiro do extrato bancário (R304): o titular da conta afastada no "
        + "meio, quem creditou apontando para ele, ele apontando para quem recebeu, e o banco da "
        + "conta pendurado no titular.",
      campos: [
        { nome: "busca", id: "f-grafo-busca", tipo: "texto", rotulo: "Buscar pessoa",
          descricao: "Nome ou CPF/CNPJ de UMA pessoa (titular ou contraparte). Deixa no desenho "
            + "só ela e quem se liga a ela — é o filtro para 'as relações de fulano'." },
        { nome: "banco", id: "f-grafo-banco", tipo: "select", rotulo: "Banco",
          descricao: "Instituição onde está a conta afastada." },
        { nome: "tipo_titular", id: "f-grafo-tp", tipo: "select", rotulo: "Tipo do titular" },
        { nome: "tipo_contraparte", id: "f-grafo-cp-tipo", tipo: "select", rotulo: "Tipo da contraparte" },
        { nome: "sentido", id: "f-grafo-direcao", tipo: "select", rotulo: "Sentido do dinheiro",
          descricao: "Só entradas (creditaram o titular) ou só saídas (o titular pagou)." },
        { nome: "credito_min", id: "f-grafo-credito-de", tipo: "moeda", rotulo: "Entrada mínima (R$)",
          descricao: "Só entra no desenho quem creditou ao titular pelo menos este total." },
        { nome: "credito_max", id: "f-grafo-credito-ate", tipo: "moeda", rotulo: "Entrada máxima (R$)" },
        { nome: "debito_min", id: "f-grafo-debito-de", tipo: "moeda", rotulo: "Saída mínima (R$)",
          descricao: "Só entra no desenho quem recebeu do titular pelo menos este total." },
        { nome: "debito_max", id: "f-grafo-debito-ate", tipo: "moeda", rotulo: "Saída máxima (R$)" },
        { nome: "contrapartes_no_desenho", id: "f-grafo-limite", tipo: "select", padrao: "25",
          rotulo: "Contrapartes no desenho",
          descricao: "Quantas contrapartes ganham nó próprio; o resto vai para “Demais contrapartes”." },
        { nome: "so_entre_titulares", id: "f-grafo-internas", tipo: "bool",
          rotulo: "Só entre titulares afastados",
          descricao: "Mostra só o dinheiro circulando entre as próprias contas afastadas do extrato." },
        { nome: "ocultar_demais_contrapartes", id: "f-grafo-agregado", tipo: "bool",
          rotulo: "Ocultar “Demais contrapartes”" },
      ],
      aoAplicar: function () { nivelBusca = 1; carregar(); },
    });
  }

  /* ------------------------------------------------------------- achados clicáveis
     Mesmo painel do RIF (GrafoFluxo.renderKPIs), calculado sobre o grafo detalhado já filtrado.
     Aqui o clique reconsulta o servidor (a busca do SIMBA é feita no banco): o card joga a
     entidade no campo "Buscar pessoa" e dispara o carregar. Só contrapartes concorrem. */
  function buscarNoGrafo(no) {
    var termo = soDigitos(no.doc) || no.rotulo;
    var $b = $("#f-grafo-busca");
    var igual = $.trim($b.val() || "") === termo;
    // clicar de novo na MESMA entidade solta o recorte (senão o clique no card repetido parece mudo)
    $b.val(igual ? "" : termo);
    nivelBusca = igual ? 1 : 2;
    carregar();
  }

  function atualizarAchados() {
    // sempre sobre o snapshot do extrato inteiro (sem filtros): os achados descrevem o extrato,
    // não o recorte atual — senão, depois do primeiro clique, todos os cards apontam para a
    // mesma entidade já filtrada e o painel para de dizer qualquer coisa nova
    var g = gCompleto || gDetalhado;
    if (!g) { GrafoFluxo.renderKPIs([]); return; }
    var util = function (n) {
      return (n.tipo === "pf" || n.tipo === "pj") && !n.titular;
    };
    var kpis = [];
    function melhor(fn) {
      var top = null, tv = -1;
      g.nos.forEach(function (n) {
        if (!util(n)) return;
        var v = fn(n);
        if (v > tv) { tv = v; top = n; }
      });
      return top;
    }
    function push(rotulo, no, nota) {
      if (!no) return;
      kpis.push({ rotulo: rotulo, valor: no.rotulo || "—", nota: nota,
                  aoClicar: function () { buscarNoGrafo(no); } });
    }
    function trunc2(t) { t = String(t || "—"); return t.length > 18 ? t.slice(0, 17) + "…" : t; }

    var n1 = melhor(function (n) { return n.total; });
    if (n1 && n1.total > 0.005) push("Maior fluxo total", n1, formatarReais(n1.total));

    var n2 = melhor(function (n) { return n.credito; });
    if (n2 && n2.credito > 0.005) push("Quem mais creditou nos titulares", n2,
      formatarReais(n2.credito) + " em " + (n2.nLanc || "?") + " lanç.");

    var n3 = melhor(function (n) { return n.debito; });
    if (n3 && n3.debito > 0.005) push("Quem mais recebeu dos titulares", n3,
      formatarReais(n3.debito) + " em " + (n3.nLanc || "?") + " lanç.");

    // ligada a mais de um titular afastado — a pessoa que reaparece em várias contas
    var mul = {};
    g.arestas.forEach(function (a) {
      if (a.dir === "conta") return;
      var ni = g.nos[a.i], nj = g.nos[a.j];
      var tit = ni.titular ? ni : (nj.titular ? nj : null);
      var cp = tit ? (tit === ni ? nj : ni) : null;
      if (!tit || !cp || !util(cp)) return;
      var m = mul[cp.chave] = mul[cp.chave] || { no: cp, tits: {}, valor: 0 };
      m.tits[tit.chave] = true;
      m.valor += num(a.valor);
    });
    var pior = null;
    chaves(mul).forEach(function (k) {
      var m = mul[k], qt = chaves(m.tits).length;
      if (qt < 2) return;
      if (!pior || qt > pior.qt || (qt === pior.qt && m.valor > pior.valor)) pior = { no: m.no, qt: qt, valor: m.valor };
    });
    if (pior) push("Ligada a mais de um titular", pior.no,
      pior.qt + " titulares · " + formatarReais(pior.valor));

    var aMax = null;
    g.arestas.forEach(function (a) {
      if (a.semValor || a.dir === "conta") return;
      if (!aMax || a.valor > aMax.valor) aMax = a;
    });
    if (aMax) {
      var di = g.nos[aMax.i], dj = g.nos[aMax.j];
      var alvo = di.titular ? dj : di;
      kpis.push({
        rotulo: "Maior relação em valor", valor: trunc2(di.rotulo) + " → " + trunc2(dj.rotulo),
        nota: formatarReais(aMax.valor) + (aMax.nLanc ? " em " + aMax.nLanc + " lanç." : ""),
        aoClicar: function () { buscarNoGrafo(alvo); }
      });
    }

    var n7 = melhor(function (n) { return n.nLanc; });
    if (n7 && n7.nLanc > 1) push("Mais lançamentos (fracionamento?)", n7,
      n7.nLanc + " lançamentos · " + formatarReais(n7.total));

    GrafoFluxo.renderKPIs(kpis);
  }

  function atualizarDica() {
    if (modoAtual() === GrafoFluxo.MODO.RESUMIDO) {
      $("#gf-dica").text(
        "Modo RESUMIDO (grafo de investigação): os bancos saem do desenho e as repetições do mesmo " +
        "par, na mesma direção, viram UMA relação com o total em R$ e a quantidade de operações. " +
        "Verde = entrada (creditou a conta afastada), vermelho = saída, roxo = entre dois titulares " +
        "afastados; espessura ∝ valor (escala logarítmica, para uma operação gigante não apagar o " +
        "resto). Titular continua com anel. Clicar num nó abre a gaveta com as relações agregadas " +
        "dele; clicar numa LINHA abre a relação e as operações que a compõem — nada foi apagado, só " +
        "agrupado (contas e bancos continuam na gaveta, e as exportações saem do grafo completo). " +
        "Arraste para mover, role para zoom."
      );
      return;
    }
    $("#gf-dica").text(
      "Quem creditou (verde) aponta para o TITULAR da conta afastada, e o titular aponta para quem recebeu " +
      "(vermelho); o banco (azul) fica ligado ao titular pela linha tracejada, porque é onde a conta está e não " +
      "caminho do dinheiro. Quando o mesmo par tem os dois sentidos, saem duas linhas paralelas. A mesma pessoa é " +
      "um nó só: quem é titular de uma conta afastada E contraparte de outra aparece uma vez, com anel — é o " +
      "vínculo entre contas do próprio extrato. Espessura e tamanho ∝ R$. Arraste para mover, role para dar zoom. " +
      "Clicar num nó abre a gaveta à esquerda e coloca ele e as ligações dele em foco; na gaveta dá para pular de " +
      "uma ligação para a próxima e ir direto aos lançamentos. Shift+clique acumula: com dois ou mais nós em foco " +
      "a gaveta mostra o que passou entre eles e as contrapartes em comum. Esc, ✕ ou um clique no vazio saem do foco."
    );
  }

  // ---------------------------------------------------------------- carga
  if (!idExtrato) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  $("#voltar-simba").attr("href", "simba.html?inquerito=" + encodeURIComponent(idInquerito || "") +
    "&simba=" + encodeURIComponent(idExtrato));

  $.getJSON("/api/investigacao/simba/" + idExtrato).done(function (e) {
    if (e && e.no_arquivo) $("#smb-arquivo").text("· " + window.AnalyserUtils.nomeArquivo(e.no_arquivo));
  });

  // deep link da ficha ("Ver no grafo"): chega com a busca da entidade já aplicada
  var buscaInicial = paramUrl("busca");
  if (buscaInicial) $("#f-grafo-busca").val(buscaInicial);

  atualizarDica();
  carregar();
})();
