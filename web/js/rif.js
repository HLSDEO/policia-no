(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idRif = paramUrl("rif");
  var idInquerito = paramUrl("inquerito");
  var abaInicial = paramUrl("aba") || "comunicacoes";
  var abaAtiva = "comunicacoes";

  // estado (aba/filtros/página) restaurado da URL via EstadoUrl (js/utils/estado_url.js): voltar
  // de um detalhe ou abrir um link compartilhado devolve a tela no mesmo ponto. A aba já chega
  // pelo abaInicial (paramUrl); os demais valores são aplicados depois de popular os selects.
  var estadoSalvo = window.EstadoUrl
    ? window.EstadoUrl.ler(["busca", "inst", "uf", "ordem", "etitular", "etipo", "epessoa", "epep", "eserv",
                            "obusca", "ocom", "cpbusca", "cpdir", "cpord", "cpimba", "cpessoa", "mes", "pag"])
    : {};

  if (!idRif) {
    $("#total-registros").text("Parâmetros inválidos na URL.");
    return;
  }

  // o grafo virou tela própria (grafo_rif.html); links e favoritos antigos ainda apontam para
  // rif.html?...&aba=grafo, então são desviados antes de carregar qualquer coisa aqui
  var urlGrafo = "grafo_rif.html?inquerito=" + encodeURIComponent(idInquerito || "") +
    "&rif=" + encodeURIComponent(idRif || "");
  if (abaInicial === "grafo" && idRif) {
    window.location.replace(urlGrafo);
    return;
  }
  $("#abrir-grafo").attr("href", urlGrafo);

  // Paginação: itens por página e página atual de cada aba
  var ITENS_POR_PAGINA = 50;
  var paginaAtual = { comunicacoes: 1, contrapartes: 1, envolvidos: 1, ocorrencias: 1 };

  // dados carregados por aba (a fonte da verdade; filtros/ordenação re-renderizam a partir daqui)
  var dados = { comunicacoes: [], envolvidos: [], ocorrencias: [] };

  // Visão financeira do RIF inteiro: contrapartes somadas por pessoa (rota própria), mais os
  // dois filtros que ela dispara sobre as comunicações — um mês da série e uma pessoa.
  var consolidadas = null;
  var filtroMes = null;      // "YYYY-MM" clicado na série mensal
  var filtroPessoa = null;   // { chave, nome, ids: {id_comunicacao_rif: true} }

  // contagens de "entidade/comentário" por id_origem (para destacar 📌); as marcações 🗒️ ficam
  // no cache interno de EntidadesUI (carregado junto em carregarContagens)
  var contagensEnt = { rif_comunicacao: {}, rif_envolvido: {}, rif_contraparte: {} };

  // contrapartes estruturadas (remetentes/favorecidos) agrupadas por id_comunicacao_rif
  var contrapartesPorCom = {};

  // Reanálise com IA: o botão "Reanalisar com IA" do "Texto original da análise" só aparece quando
  // a IA está liberada para a delegacia do usuário e para a do inquérito. A checagem é a mesma do
  // painel flutuante (ia_flutuante.js): GET .../ia/conversas responde 403 quando a IA está
  // desativada, e aí os botões simplesmente não são desenhados.
  var iaDisponivel = false;

  function checarIaDisponivel() {
    var d = $.Deferred();
    if (!idInquerito) { d.resolve(); return d; }
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas")
      .done(function () { iaDisponivel = true; })
      .fail(function () { iaDisponivel = false; })
      .always(function () { d.resolve(); });
    return d;
  }

  // identificações manuais (RIF_TB06): fatias do valor DESCONHECIDO que o investigador atribuiu a
  // alguém por outra fonte, também agrupadas por id_comunicacao_rif
  var identificacoesPorCom = {};

  // Envolvidos (RIF_TB03, já vem com papel Titular/Remetente/Beneficiário/etc. por Indexador — sem
  // precisar de regex) agrupados por nu_indexador, calculado uma vez após carregar tudo.
  var envolvidosPorIndexador = {};

  // Anexa o(s) titular(es) de cada comunicação (via Envolvidos, casando pelo Indexador) direto no
  // objeto da comunicação, para a coluna "Titular" e para os nós do grafo.
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

  // A linha sem nome E sem documento é o DESCONHECIDO: o resto entre o total da comunicação
  // (campos B/C) e o que o texto nomeia. É só nela que cabe identificar valor à mão.
  function ehDesconhecida(cp) {
    return !String(cp.no_contraparte || "").trim() && !String(cp.nu_cpf_cnpj || "").trim();
  }

  // chave da pessoa no client — a MESMA do backend (consolidar_contrapartes_rif / timeline):
  // CPF/CNPJ cru quando existe, senão "NOME:"+maiúsculas. É o que casa envolvido, contraparte
  // estruturada e identificação manual na mesma pessoa.
  function chaveDe(o) {
    var d = String(o.nu_cpf_cnpj || "").trim();
    if (d) return d;
    var n = String(o.no_envolvido || o.no_contraparte || "").trim();
    return n ? "NOME:" + n.toUpperCase() : null;
  }

  // comunicações em que a pessoa aparece: como envolvida do Indexador (titular, remetente…)
  // OU como contraparte estruturada do texto. É o que o filtro "Pessoa" aplica na aba del ali.
  function idsComPessoa(chave) {
    var ids = {};
    dados.comunicacoes.forEach(function (c) {
      var achou = (envolvidosPorIndexador[c.nu_indexador] || []).some(function (e) { return chaveDe(e) === chave; });
      if (!achou) {
        achou = (contrapartesPorCom[c.id_comunicacao_rif] || []).some(function (x) { return chaveDe(x) === chave; });
      }
      if (achou) ids[c.id_comunicacao_rif] = true;
    });
    return ids;
  }

  function aplicarFiltroPessoa(chave, nome) {
    filtroPessoa = { chave: chave, nome: nome || chave, ids: idsComPessoa(chave) };
    trocarAba("comunicacoes");
    aplicarComunicacoes();
  }
  function identsDe(idCom, direcao) {
    return (identificacoesPorCom[idCom] || []).filter(function (i) { return i.cd_direcao === direcao; });
  }
  function somaIdents(idents) {
    return idents.reduce(function (s, i) { return s + num(i.vl_total); }, 0);
  }

  // monta as duas tabelas (Entradas/créditos e Saídas/débitos) de uma comunicação
  function renderContrapartes(idCom) {
    var $wrap = $("<div>").addClass("rif-cp-wrap").attr("data-com", idCom);
    var lista = contrapartesPorCom[idCom] || [];
    if (!lista.length) {
      $("<p>").addClass("field-help").css("margin", "2px 0 8px").text("Sem contrapartes estruturadas nesta comunicação (ver texto original abaixo).").appendTo($wrap);
      return $wrap;
    }
    function tabela(direcao, titulo, quemRot, classe) {
      var itens = lista.filter(function (x) { return x.cd_direcao === direcao; });
      var idents = identsDe(idCom, direcao);
      if (!itens.length && !idents.length) return;
      // o total da direção não muda ao identificar: a identificação só reparte o desconhecido
      var total = itens.reduce(function (s, x) { return s + num(x.vl_total); }, 0);
      var $bloco = $("<div>").addClass("rif-cp-bloco").appendTo($wrap);
      $("<div>").addClass("rif-cp-titulo " + classe).html(titulo + " <span>(" + itens.length + " · R$ " + formatarValor(total) + ")</span>").appendTo($bloco);
      var $tab = $("<table>").addClass("rif-cp-tabela").appendTo($bloco);
      var $thead = $("<tr>").appendTo($("<thead>").appendTo($tab));
      [quemRot, "CPF/CNPJ", "Lanç.", "Valor (R$)", "Banco / observação", ""].forEach(function (h) { $("<th>").text(h).appendTo($thead); });
      var $tb = $("<tbody>").appendTo($tab);

      // uma fatia já identificada, logo abaixo do desconhecido de onde saiu
      function linhaIdent(ident) {
        var $r = $("<tr>").addClass("rif-cp-identificada").appendTo($tb);
        $("<td>").text(txt(ident.no_contraparte))
          .append($("<span>").addClass("rif-badge identificada").text("identificado")).appendTo($r);
        $("<td>").addClass("rif-cp-doc").text(txt(ident.nu_cpf_cnpj)).appendTo($r);
        $("<td>").addClass("rif-valor").text(ident.qt_lancamentos != null ? ident.qt_lancamentos : "—").appendTo($r);
        $("<td>").addClass("rif-valor " + classe).text(formatarValor(ident.vl_total)).appendTo($r);
        $("<td>").addClass("rif-cp-obs").text(txt(ident.tx_obs) + (ident.identificado_por ? " · por " + ident.identificado_por : "")).appendTo($r);
        var $acoes = $("<td>").addClass("rif-cp-acoes").appendTo($r);
        $("<button>").addClass("btn btn-secondary rif-btn-mini").text("Editar").attr("title", "Editar esta identificação")
          .on("click", function () { abrirModalIdent({ idCom: idCom, direcao: direcao, ident: ident }); })
          .appendTo($acoes);
        $acoes.append(" ");
        $("<button>").addClass("btn btn-secondary rif-btn-mini").text("Remover").attr("title", "Desfazer: o valor volta a ser desconhecido")
          .on("click", function () { removerIdent(ident, idCom); })
          .appendTo($acoes);
      }

      itens.forEach(function (x) {
        var desconhecida = ehDesconhecida(x);
        var jaIdent = desconhecida ? somaIdents(idents) : 0;
        var restante = num(x.vl_total) - jaIdent;
        var $r = $("<tr>").appendTo($tb);
        if (desconhecida) $r.addClass("rif-cp-desconhecido");
        $("<td>").text(desconhecida ? "Desconhecido" : txt(x.no_contraparte)).appendTo($r);
        $("<td>").addClass("rif-cp-doc").text(txt(x.nu_cpf_cnpj)).appendTo($r);
        $("<td>").addClass("rif-valor").text(x.qt_lancamentos != null ? x.qt_lancamentos : "—").appendTo($r);
        $("<td>").addClass("rif-valor " + classe).text(formatarValor(desconhecida ? restante : x.vl_total)).appendTo($r);
        var obs = desconhecida
          ? (jaIdent > 0.005 ? "resto de " + formatarReais(x.vl_total) + " após identificações" : "não nominado no texto")
          : txt(x.tx_obs);
        $("<td>").addClass("rif-cp-obs").text(obs).appendTo($r);
        var $acoes = $("<td>").addClass("rif-cp-acoes").appendTo($r);
        if (desconhecida) {
          $("<button>").addClass("btn btn-secondary rif-btn-mini").text("Identificar")
            .attr("title", "Atribuir parte deste valor a uma pessoa/empresa")
            .prop("disabled", restante <= 0.005)
            .on("click", function () {
              abrirModalIdent({ idCom: idCom, direcao: direcao, disponivel: restante, totalDesconhecido: num(x.vl_total) });
            })
            .appendTo($acoes);
          $acoes.append(" ");
        }
        if (idInquerito && window.EntidadesUI) {
          var rot = (direcao === "credito" ? "creditou" : "debitado") + " " + txt(x.no_contraparte) + " · " + formatarReais(x.vl_total);
          $acoes.append(EntidadesUI.botao({
            idInquerito: idInquerito, tpOrigem: "rif_contraparte", idOrigem: x.id_contraparte_rif,
            rotulo: rot, temComentario: !!contagensEnt.rif_contraparte[x.id_contraparte_rif], aoMudar: function () {}
          }));
        }
        if (desconhecida) idents.forEach(linhaIdent);
      });

      // identificações sem linha desconhecida correspondente (o reprocessamento mudou o resto):
      // aparecem no fim para o trabalho manual não sumir da tela
      if (idents.length && !itens.some(ehDesconhecida)) {
        idents.forEach(linhaIdent);
        $("<p>").addClass("rif-cp-nota").text(
          "Identificações sem valor desconhecido correspondente nesta direção — confira após reprocessar as contrapartes."
        ).appendTo($bloco);
      }
    }
    tabela("credito", "▼ Entradas — quem creditou (remetentes/depositantes)", "Remetente", "credito");
    tabela("debito", "▲ Saídas — quem foi debitado (favorecidos/destinatários)", "Favorecido", "debito");
    return $wrap;
  }

  // ------------------------------------------- identificação manual (modal)
  var identCtx = null;   // { idCom, direcao, ident?, disponivel, totalDesconhecido }

  // "100.000,00", "100000.00" e "100000" → 100000. Ponto sozinho em grupos de 3 é milhar (pt-BR).
  function parseValorBR(texto) {
    var s = String(texto == null ? "" : texto).trim().replace(/^R\$\s*/i, "").replace(/\s/g, "");
    if (!s) return NaN;
    if (s.indexOf(",") !== -1) s = s.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    var n = Number(s);
    return isFinite(n) ? n : NaN;
  }

  function aplicarMascaraMonetaria(input) {
    input.addEventListener("input", function (e) {
      var valor = e.target.value.replace(/\D/g, "");
      if (!valor) { e.target.value = ""; return; }
      var centavos = valor.slice(-2);
      var inteiros = valor.slice(0, -2);
      inteiros = inteiros.replace(/^0+/, "") || "0";
      inteiros = inteiros.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      e.target.value = inteiros + "," + centavos;
    });
    input.addEventListener("blur", function (e) {
      if (e.target.value === "," || e.target.value === "0,00") e.target.value = "";
    });
    input.addEventListener("focus", function (e) {
      if (!e.target.value) e.target.value = "0,00";
    });
  }

  function disponivelPara(idCom, direcao, identEditada) {
    var lista = (contrapartesPorCom[idCom] || []).filter(function (x) {
      return x.cd_direcao === direcao && ehDesconhecida(x);
    });
    var total = lista.reduce(function (s, x) { return s + num(x.vl_total); }, 0);
    var usado = identsDe(idCom, direcao).reduce(function (s, i) {
      return s + (identEditada && i.id_identificacao_rif === identEditada.id_identificacao_rif ? 0 : num(i.vl_total));
    }, 0);
    return total - usado;
  }

  function abrirModalIdent(ctx) {
    identCtx = ctx;
    var ident = ctx.ident || null;
    var disponivel = disponivelPara(ctx.idCom, ctx.direcao, ident);
    var rotDir = ctx.direcao === "credito" ? "entrada (quem creditou)" : "saída (quem foi debitado)";
    $("#rif-ident-titulo").text(ident ? "Editar identificação" : "Identificar valor desconhecido");
    $("#rif-ident-contexto").text(
      "Direção: " + rotDir + ". Disponível para identificar: " + formatarReais(disponivel) + "."
    );
    $("#rif-ident-nome").val(ident ? (ident.no_contraparte || "") : "");
    $("#rif-ident-doc").val(ident ? (ident.nu_cpf_cnpj || "") : "");
    $("#rif-ident-valor").val(ident ? formatarValor(ident.vl_total) : "");
    $("#rif-ident-lanc").val(ident && ident.qt_lancamentos != null ? ident.qt_lancamentos : "");
    $("#rif-ident-obs").val(ident ? (ident.tx_obs || "") : "");
    $("#rif-ident-erro").hide().text("");
    $("#modal-rif-ident").removeClass("hidden");
    $("#rif-ident-nome").focus();
  }

  function fecharModalIdent() {
    identCtx = null;
    $("#modal-rif-ident").addClass("hidden");
  }

  function erroIdent(msg) { $("#rif-ident-erro").text(msg).show(); }

  function salvarIdent() {
    if (!identCtx) return;
    var nome = $.trim($("#rif-ident-nome").val());
    var doc = $.trim($("#rif-ident-doc").val());
    var valor = parseValorBR($("#rif-ident-valor").val());
    var lanc = $.trim($("#rif-ident-lanc").val());
    if (!nome && !doc) { erroIdent("Informe ao menos o nome ou o CPF/CNPJ."); return; }
    if (!isFinite(valor) || valor <= 0) { erroIdent("Valor inválido."); return; }
    var disponivel = disponivelPara(identCtx.idCom, identCtx.direcao, identCtx.ident || null);
    if (valor > disponivel + 0.005) {
      erroIdent("Valor acima do que resta identificar (disponível: " + formatarReais(disponivel) + ").");
      return;
    }
    var corpo = {
      no_contraparte: nome || null, nu_cpf_cnpj: doc || null, vl_total: Number(valor.toFixed(2)),
      qt_lancamentos: lanc === "" ? null : parseInt(lanc, 10),
      tx_obs: $.trim($("#rif-ident-obs").val()) || null
    };
    var url, metodo;
    if (identCtx.ident) {
      url = "/api/investigacao/rifs/identificacoes/" + identCtx.ident.id_identificacao_rif;
      metodo = "PUT";
    } else {
      url = "/api/investigacao/rifs/" + idRif + "/identificacoes";
      metodo = "POST";
      corpo.id_comunicacao_rif = identCtx.idCom;
      corpo.cd_direcao = identCtx.direcao;
    }
    var idCom = identCtx.idCom;
    $("#rif-ident-salvar").prop("disabled", true);
    $.ajax({ url: url, method: metodo, contentType: "application/json", data: JSON.stringify(corpo) })
      .done(function () {
        fecharModalIdent();
        recarregarIdentificacoes(idCom);
      })
      .fail(function (xhr) {
        erroIdent((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar.");
      })
      .always(function () { $("#rif-ident-salvar").prop("disabled", false); });
  }

  function removerIdent(ident, idCom) {
    var quem = ident.no_contraparte || ident.nu_cpf_cnpj || "esta identificação";
    Dialogo.confirmar({
      titulo: "Remover identificação",
      mensagem: "Remover " + formatarReais(ident.vl_total) + " de " + quem + "? O valor volta a ser desconhecido.",
      perigoso: true, rotuloOk: "Remover"
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/rifs/identificacoes/" + ident.id_identificacao_rif, method: "DELETE" })
        .done(function () { recarregarIdentificacoes(idCom); })
        .fail(function (xhr) {
          Dialogo.alerta((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível remover.");
        });
    });
  }

  // recarrega as identificações e redesenha só o bloco daquela comunicação, mantendo o detalhe
  // aberto. O grafo (grafo_rif.html) monta a partir dos mesmos dados e já vai pegar a pessoa
  // identificada em vez do desconhecido na próxima vez que for aberto.
  // redesenha só o bloco de contrapartes de uma comunicação (identificar/reanalisar), sem
  // perder o detalhe aberto — as duas rotas que precisavam disso eram cópia uma da outra
  function substituirBlocoContrapartes(idCom) {
    var $antigo = $(".rif-cp-wrap[data-com='" + idCom + "']");
    if ($antigo.length) $antigo.replaceWith(renderContrapartes(idCom));
    atualizarTotal();
  }

  function recarregarIdentificacoes(idCom) {
    $.when(carregarIdentificacoes(), atualizarConsolidadas()).always(function () {
      substituirBlocoContrapartes(idCom);
    });
  }

  // Reanálise com IA de uma comunicação: o backend manda só o texto daquela comunicação para a IA
  // e regrava as contrapartes dela (RIF_TB05) a partir do que a IA identificou — a extração por
  // REGEX daquela comunicação é substituída. Aqui basta recarregar as contrapartes e redesenhar o
  // bloco, mantendo o detalhe aberto (o grafo pega os dados novos na próxima abertura).
  function reanalisarComIA(idCom, $btn) {
    Carregando.botao($btn, "Reanalisando com IA…");
    $.ajax({
      url: "/api/investigacao/rifs/" + idRif + "/comunicacoes/" + idCom + "/reanalisar-ia",
      method: "POST"
    })
      .done(function () {
        Toast.sucesso("Reanálise com IA concluída — confira as entradas e saídas.");
        carregarContrapartes().always(function () {
          substituirBlocoContrapartes(idCom);
          atualizarConsolidadas();
        });
      })
      .fail(function (xhr) {
        Toast.erro(Api.mensagemErro(xhr, "Não foi possível reanalisar com IA."));
      })
      .always(function () { Carregando.restaurar($btn); });
  }

  $("#rif-ident-salvar").on("click", salvarIdent);
  $("#rif-ident-cancelar, #rif-ident-fechar").on("click", fecharModalIdent);
  $("#modal-rif-ident").on("click", function (e) { if (e.target === this) fecharModalIdent(); });
  $("#modal-rif-ident").on("keydown", function (e) {
    if (e.key === "Escape") { fecharModalIdent(); return; }
    if (e.key === "Enter" && e.target.tagName === "INPUT") { salvarIdent(); return; }
    // focus trap simples: Tab/Super+Tab circulam dentro do modal aberto — o leitor de tela (e
    // quem navega só pelo teclado) não "vaza" para a página atrás do overlay
    if (e.key === "Tab") {
      var $f = $(this).find("button, input, select, textarea").filter(":visible").not(":disabled");
      if (!$f.length) return;
      var primeiro = $f[0], ultimo = $f[$f.length - 1];
      if (e.shiftKey && document.activeElement === primeiro) { e.preventDefault(); ultimo.focus(); }
      else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primeiro.focus(); }
    }
  });

  // ---------------------------------------------------------------- helpers
  function formatarData(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\d)(T[\d:.]+)?$/, "$1T00:00:00Z"));
    if (isNaN(d)) return "—";
    return d.toLocaleDateString("pt-BR", { timeZone: "UTC" });
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function formatarValor(v) {
    if (v === null || v === undefined || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function formatarReais(n) {
    return "R$ " + num(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function txt(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }
  function ehPep(e) { return /^s/i.test(String(e.tx_pep || "")); }          // "Sim"
  function ehServidor(e) { return /^s/i.test(String(e.tx_servidor || "")); } // "Sim"
  function ehPJ(e) { var d = String(e.nu_cpf_cnpj || "").replace(/\D/g, ""); return d.length === 14 || String(e.nu_cpf_cnpj || "").indexOf("/") !== -1; }

  // ---------------------------------------------------------------- resumo
  function renderResumo() {
    var com = dados.comunicacoes, env = dados.envolvidos, oco = dados.ocorrencias;
    var totCred = com.reduce(function (s, c) { return s + num(c.vl_campo_b); }, 0);
    var totDeb = com.reduce(function (s, c) { return s + num(c.vl_campo_c); }, 0);

    var datas = com.map(function (c) { return c.dt_operacao || c.dt_recebimento; }).filter(Boolean).sort();
    var periodo = datas.length ? (formatarData(datas[0]) + " – " + formatarData(datas[datas.length - 1])) : "—";

    var nPep = env.filter(ehPep).length;
    var bancos = {};
    com.forEach(function (c) { if (c.no_comunicante) bancos[c.no_comunicante] = true; });

    var kpis = [
      { rot: "Comunicações", val: com.length },
      { rot: "Instituições", val: Object.keys(bancos).length },
      { rot: "Crédito total", val: formatarReais(totCred), cls: "credito" },
      { rot: "Débito total", val: formatarReais(totDeb), cls: "debito" },
      { rot: "Envolvidos", val: env.length },
      { rot: "PEP citados", val: nPep, cls: nPep ? "pep" : "" },
      { rot: "Ocorrências", val: oco.length },
      { rot: "Período", val: periodo },
    ];
    var $r = $("#rif-resumo").empty();
    kpis.forEach(function (k) {
      var $c = $("<div>").addClass("rif-kpi" + (k.cls ? " " + k.cls : "")).appendTo($r);
      $("<div>").addClass("rot").text(k.rot).appendTo($c);
      $("<div>").addClass("val").text(typeof k.val === "number" ? k.val.toLocaleString("pt-BR") : k.val).appendTo($c);
    });
  }

  // ------------------------------------------------------- série mensal (SVG)
  // Crédito × débito por mês, direto das comunicações já carregadas (sem rota nova). Clique no
  // mês filtra a aba Comunicações — é como a tela responde "quando a movimentação começou/parou",
  // que antes cabia só no KPI "Período".
  function compacto(v) {
    var n = num(v), abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " bi";
    if (abs >= 1e6) return (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mi";
    if (abs >= 1e3) return (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + " mil";
    return n.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
  }

  function mesDe(c) {
    var d = c.dt_operacao || c.dt_recebimento;
    return d ? String(d).slice(0, 7) : null;
  }
  function mesBR(mes) {
    var m = String(mes || "").match(/^(\d{4})-(\d\d)$/);
    return m ? m[2] + "/" + m[1] : String(mes || "—");
  }

  function renderSerie() {
    var svg = document.getElementById("rif-serie");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var por = {};
    dados.comunicacoes.forEach(function (c) {
      var mes = mesDe(c);
      if (!mes) return;
      var m = por[mes] || (por[mes] = { mes: mes, cred: 0, deb: 0, qt: 0 });
      m.cred += num(c.vl_campo_b); m.deb += num(c.vl_campo_c); m.qt += 1;
    });
    var meses = Object.keys(por).sort().map(function (m) { return por[m]; });
    if (!meses.length) { $("#rif-serie-wrap").hide(); return; }
    $("#rif-serie-wrap").show();

    var SVG_NS = "http://www.w3.org/2000/svg";
    function elG(tag, attrs) {
      var e = document.createElementNS(SVG_NS, tag);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    }

    var margemX = 70;
    var largura = Math.max(560, meses.length * 46 + margemX);
    var altura = 150, areaTop = 10, areaBase = altura - 16;
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("height", altura);
    svg.setAttribute("width", "100%");
    svg.style.minWidth = largura + "px";

    var maximo = 1;
    meses.forEach(function (m) { maximo = Math.max(maximo, m.cred, m.deb); });
    var passo = (largura - margemX) / meses.length;
    var larguraBarra = Math.max(3, Math.min(12, passo / 2 - 3));
    function xMes(i) { return margemX + i * passo + passo / 2; }
    function y(v) { return areaBase - (v / maximo) * (areaBase - areaTop); }

    [0, 0.5, 1].forEach(function (frac) {
      var yy = y(maximo * frac);
      svg.appendChild(elG("line", { x1: margemX, y1: yy, x2: largura, y2: yy, "class": "eixo", "stroke-dasharray": "4,4", opacity: frac ? 0.25 : 1 }));
      var rot = elG("text", { x: margemX - 6, y: yy + 3, "text-anchor": "end", "class": "rotulo" });
      rot.textContent = "R$ " + compacto(maximo * frac);
      svg.appendChild(rot);
    });

    var intervalo = meses.length > 24 ? 12 : (meses.length > 12 ? 6 : 1);
    meses.forEach(function (m, i) {
      var x = xMes(i);
      if (filtroMes === m.mes) {
        svg.appendChild(elG("rect", { x: x - passo / 2 + 1, y: areaTop, width: passo - 2, height: areaBase - areaTop, "class": "mes-fundo", rx: 4 }));
      }
      if (m.cred > 0) {
        var hc = Math.max(1, (m.cred / maximo) * (areaBase - areaTop));
        var rc = elG("rect", { x: x - larguraBarra - 1, y: areaBase - hc, width: larguraBarra, height: hc, fill: "#16a34a", rx: 2 });
        rc.appendChild(elG("title", {})).textContent = mesBR(m.mes) + " · crédito " + formatarReais(m.cred);
        svg.appendChild(rc);
      }
      if (m.deb > 0) {
        var hd = Math.max(1, (m.deb / maximo) * (areaBase - areaTop));
        var rd = elG("rect", { x: x + 1, y: areaBase - hd, width: larguraBarra, height: hd, fill: "#dc2626", rx: 2 });
        rd.appendChild(elG("title", {})).textContent = mesBR(m.mes) + " · débito " + formatarReais(m.deb);
        svg.appendChild(rd);
      }
      var alvo = svg;
      var clicavel = elG("rect", {
        x: x - passo / 2, y: areaTop, width: passo, height: areaBase - areaTop,
        "class": "mes-clique", "data-mes": m.mes, tabindex: 0, role: "button",
        "aria-label": "Filtrar comunicações de " + mesBR(m.mes)
      });
      clicavel.appendChild(elG("title", {})).textContent =
        mesBR(m.mes) + " — " + m.qt + " comunicação(ões): crédito " + formatarReais(m.cred) + ", débito " + formatarReais(m.deb);
      svg.appendChild(clicavel);
      var mostrar = i === 0 || i % intervalo === 0 || filtroMes === m.mes;
      if (mostrar) {
        var t = elG("text", { x: x, y: altura - 4, "text-anchor": "middle", "class": "rotulo" });
        t.textContent = mesBR(m.mes);
        svg.appendChild(t);
      }
    });
    $("#rif-serie-info").text(filtroMes
      ? "Filtrando " + mesBR(filtroMes) + " — clique de novo para limpar"
      : meses.length + " meses com movimento");
  }

  // --------------------------------------------------- barra de cobertura
  // Quanto do movimentado tem dono: o que o texto nomeia (verde), o que a identificação manual
  // resgatou do desconhecido (azul) e o que segue sem dono (hachura). Sem isso o investigador
  // não sabe se a análise "fecha" ou se falta metade do dinheiro.
  function renderCobertura() {
    var c = consolidadas && consolidadas.cobertura;
    if (!c) { $("#rif-cobertura").hide(); return; }
    var total = c.vl_credito + c.vl_debito;
    if (total <= 0) { $("#rif-cobertura").hide(); return; }
    var nomeado = c.nomeado_credito + c.nomeado_debito;
    var ident = c.identificado_credito + c.identificado_debito;
    var resto = Math.max(0, total - nomeado - ident);
    function pct(v) { return (100 * v / total); }
    var $barra = $("#rif-cobertura-barra").empty();
    $("<span>").addClass("nomeado").css("width", pct(nomeado) + "%")
      .attr("title", "Nomeado no texto: " + formatarReais(nomeado)).appendTo($barra);
    if (ident > 0.005) {
      $("<span>").addClass("identificado").css("width", pct(ident) + "%")
        .attr("title", "Identificado à mão: " + formatarReais(ident)).appendTo($barra);
    }
    if (resto > 0.005) {
      $("<span>").addClass("sem-dono").css("width", pct(resto) + "%")
        .attr("title", "Sem dono no texto (desconhecido): " + formatarReais(resto)).appendTo($barra);
    }
    var $leg = $("#rif-cobertura-legenda").empty();
    $("<span>").text(c.pct.toLocaleString("pt-BR") + "% do movimentado tem dono (" +
      formatarReais(total) + " somando crédito e débito)").css("font-weight", "600").appendTo($leg);
    $("<span>").html('<span style="color:#16a34a">■</span> nomeado: ' + formatarReais(nomeado)).appendTo($leg);
    if (ident > 0.005) {
      $("<span>").html('<span style="color:#2563eb">■</span> identificado à mão: ' + formatarReais(ident)).appendTo($leg);
    }
    if (resto > 0.005) {
      $("<span>").html('<span style="opacity:.5">▨</span> sem dono: ' + formatarReais(resto)).appendTo($leg);
    }
    $("#rif-cobertura").show();
  }

  // ------------------------------------------------------- popular selects
  function popularSelect($sel, valores) {
    valores.sort(function (a, b) { return String(a).localeCompare(String(b), "pt-BR"); });
    valores.forEach(function (v) { $("<option>").val(v).text(v).appendTo($sel); });
  }
  function popularFiltros() {
    var bancos = {}, ufs = {}, tipos = {}, titulares = {};
    var qtTitulares = 0;
    dados.comunicacoes.forEach(function (c) {
      if (c.no_comunicante) bancos[c.no_comunicante] = true;
      if (c.sg_uf_agencia) ufs[c.sg_uf_agencia] = true;
      if (c.no_titular && !titulares[c.no_titular]) { titulares[c.no_titular] = true; qtTitulares++; }
    });
    dados.envolvidos.forEach(function (e) { if (e.tp_envolvido) tipos[e.tp_envolvido] = true; });
    popularSelect($("#f-com-inst"), Object.keys(bancos));
    popularSelect($("#f-com-uf"), Object.keys(ufs));
    popularSelect($("#f-env-tipo"), Object.keys(tipos));
    // o RIF pode citar mais de um titular (TB03 papel "Titular" por Indexador) — o filtro só
    // aparece quando há mais de um, senão o cabeçalho já diz tudo
    var $tit = $("#f-com-titular");
    $tit.find("option:not(:first)").remove();
    if (qtTitulares > 1) {
      popularSelect($tit, Object.keys(titulares));
      $tit.removeClass("hidden");
    } else {
      $tit.addClass("hidden");
    }
  }

  // ---------------------------------------------------- Paginação
  // APLICAR está definida mais abaixo; os botões chamam o aplicar da aba SEM resetar a página
  // (é a própria navegação de página) — antes apontavam para um aplicarFiltros() que não
  // existia, então trocar de página lançava ReferenceError.
  function renderPaginacao(aba, total) {
    var $pag = $("#pag-" + aba).empty();
    if (total <= ITENS_POR_PAGINA) {
      $pag.hide();
      return;
    }
    $pag.show();
    var totalPaginas = Math.ceil(total / ITENS_POR_PAGINA);
    var pag = paginaAtual[aba];

    var $anterior = $("<button>").text("← Anterior").attr("aria-label", "Página anterior")
      .prop("disabled", pag <= 1);
    $anterior.on("click", function () {
      if (pag > 1) {
        paginaAtual[aba] = pag - 1;
        APLICAR[aba](true);
      }
    });
    $pag.append($anterior);

    var $proxima = $("<button>").text("Próxima →").attr("aria-label", "Próxima página")
      .prop("disabled", pag >= totalPaginas);
    $proxima.on("click", function () {
      if (pag < totalPaginas) {
        paginaAtual[aba] = pag + 1;
        APLICAR[aba](true);
      }
    });
    $pag.append($proxima);

    var $info = $("<span>").addClass("pag-info").attr("role", "status")
      .text("Página " + pag + " de " + totalPaginas);
    $pag.append($info);
  }

  function obterItensPageinados(lista, aba) {
    // clamp: um filtro aplicado (ou a página vinda da URL) pode deixar a aba com menos páginas
    var totalPaginas = Math.max(1, Math.ceil(lista.length / ITENS_POR_PAGINA));
    var pag = Math.min(Math.max(1, paginaAtual[aba] || 1), totalPaginas);
    paginaAtual[aba] = pag;
    var inicio = (pag - 1) * ITENS_POR_PAGINA;
    var fim = inicio + ITENS_POR_PAGINA;
    return lista.slice(inicio, fim);
  }

  // ------------------------------------------- render Contrapartes (consolidadas)
  function badgeSimba(p) {
    if (!p.simba) return null;
    var $b = $("<span>").addClass("rif-badge simba")
      .text("SIMBA " + compacto(p.simba.vl_total))
      .attr("title", "O mesmo documento também movimenta no extrato SIMBA deste inquérito (" +
        formatarReais(p.simba.vl_total) + (p.simba.titular ? " — inclusive como TITULAR de conta" : "") +
        ") — a mesma pessoa contada por duas fontes independentes");
    return $b;
  }

  function linhaContraparte(p, desconhecida) {
    var $tr = $("<tr>");
    if (desconhecida) $tr.addClass("rif-cp-consol-desconhecido");
    var $nome = $("<td>").addClass("col-nome rif-cp-consol-nome").text(p.nome).appendTo($tr);
    if (p.tem_identificacao) {
      $nome.append(" ").append($("<span>").addClass("rif-badge identificada").text("tem identificação à mão"));
    }
    $("<td>").addClass("col-nome rif-cp-doc").text(txt(p.documento)).appendTo($tr);
    var $marcas = $("<td>").addClass("col-nome").appendTo($tr);
    if (p.tp_pessoa) $marcas.append($("<span>").addClass("rif-badge " + (p.tp_pessoa === "PJ" ? "pj" : "pf")).text(p.tp_pessoa));
    if (p.titular) $marcas.append(" ").append($("<span>").addClass("rif-badge titular").text("Titular").attr("title", "Aparece como Titular nos envolvidos do RIF"));
    if (p.pep) $marcas.append(" ").append($("<span>").addClass("rif-badge pep").text("PEP"));
    if (p.servidor) $marcas.append(" ").append($("<span>").addClass("rif-badge servidor").text("Servidor"));
    var sb = badgeSimba(p);
    if (sb) $marcas.append(" ").append(sb);
    var cred = num(p.vl_credito) + num(p.vl_identificado_credito);
    var deb = num(p.vl_debito) + num(p.vl_identificado_debito);
    $("<td>").addClass("col-nome rif-valor credito").text(formatarValor(cred)).appendTo($tr);
    $("<td>").addClass("col-nome rif-valor debito").text(formatarValor(deb)).appendTo($tr);
    var saldo = cred - deb;
    $("<td>").addClass("col-nome rif-valor").css("color", saldo >= 0 ? "#16a34a" : "#dc2626")
      .text((saldo > 0 ? "+" : "") + formatarValor(saldo)).appendTo($tr);
    $("<td>").addClass("col-nome rif-valor").text(num(p.qt_comunicacoes).toLocaleString("pt-BR")).appendTo($tr);
    var periodo = (p.dt_primeiro || p.dt_ultimo)
      ? formatarData(p.dt_primeiro) + " – " + formatarData(p.dt_ultimo) : "—";
    var $per = $("<td>").addClass("col-nome").text(desconhecida ? "—" : periodo);
    if (p.tx_obs) $per.attr("title", p.tx_obs);
    $per.appendTo($tr);
    var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
    if ((p.ids_comunicacoes || []).length) {
      var ids = p.ids_comunicacoes;
      var $btn = $("<button>").addClass("btn btn-secondary rif-detalhe-btn")
        .text("Ver " + ids.length + " comunicação(ões)")
        .attr("title", "Filtrar a aba Comunicações por esta pessoa (envolvida ou citada no texto)")
        .on("click", function () {
          if (desconhecida) {
            var idsMap = {};
            ids.forEach(function (i) { idsMap[i] = true; });
            filtroMes = null;
            filtroPessoa = { chave: "__desconhecido__", nome: "Desconhecido", ids: idsMap };
            trocarAba("comunicacoes");
            aplicarComunicacoes();
          } else {
            aplicarFiltroPessoa(p.chave, p.nome);
          }
        }).appendTo($acoes);
    }
    return $tr;
  }

  function filtrarContrapartes() {
    if (!consolidadas) return [];
    var termo = $.trim($("#f-cp-busca").val()).toLowerCase();
    var dir = $("#f-cp-dir").val();
    var soSimba = $("#f-cp-simba").is(":checked");
    var ordem = $("#f-cp-ordem").val();
    var lista = (consolidadas.pessoas || []).filter(function (p) {
      var cred = num(p.vl_credito) + num(p.vl_identificado_credito);
      var deb = num(p.vl_debito) + num(p.vl_identificado_debito);
      if (dir === "credito" && cred <= 0) return false;
      if (dir === "debito" && deb <= 0) return false;
      if (soSimba && !p.simba) return false;
      if (!termo) return true;
      return (String(p.nome || "") + " " + String(p.documento || "") + " " + String(p.chave || "")).toLowerCase().indexOf(termo) !== -1;
    });
    function volume(p) {
      return num(p.vl_credito) + num(p.vl_identificado_credito) + num(p.vl_debito) + num(p.vl_identificado_debito);
    }
    lista.sort(function (a, b) {
      if (ordem === "credito") return num(b.vl_credito) + num(b.vl_identificado_credito) - num(a.vl_credito) - num(a.vl_identificado_credito);
      if (ordem === "debito") return num(b.vl_debito) + num(b.vl_identificado_debito) - num(a.vl_debito) - num(a.vl_identificado_debito);
      if (ordem === "saldo") return num(b.saldo) - num(a.saldo);
      if (ordem === "comunicacoes") return num(b.qt_comunicacoes) - num(a.qt_comunicacoes);
      if (ordem === "recente") return String(b.dt_ultimo || "").localeCompare(String(a.dt_ultimo || ""));
      return volume(b) - volume(a);
    });
    return lista;
  }

  function renderContrapartesConsolidadas(lista) {
    var $corpo = $("#corpo-contrapartes").empty();
    var semRecorte = !$("#f-cp-busca").val() && !$("#f-cp-dir").val() && !$("#f-cp-simba").is(":checked");
    renderPaginacao("contrapartes", lista.length);
    obterItensPageinados(lista, "contrapartes").forEach(function (p) {
      $corpo.append(linhaContraparte(p, false));
    });
    // o Desconhecido fecha a tabela (sem filtros, para continuar visível como ressalva)
    var d = consolidadas && consolidadas.desconhecido;
    if (d && (d.credito > 0.005 || d.debito > 0.005) && semRecorte) {
      $corpo.append(linhaContraparte({
        nome: "Desconhecido (resto não nominado no texto)",
        documento: (d.identificado_credito + d.identificado_debito) > 0.005
          ? "identificado à mão: " + formatarValor(d.identificado_credito) + " / " + formatarValor(d.identificado_debito) : null,
        vl_credito: d.credito - d.identificado_credito, vl_debito: d.debito - d.identificado_debito,
        tem_identificacao: (d.identificado_credito + d.identificado_debito) > 0.005,
        qt_comunicacoes: d.qt_comunicacoes, ids_comunicacoes: d.ids_comunicacoes
      }, true));
    }
    var temDesconhecido = !!(d && semRecorte && (d.credito > 0.005 || d.debito > 0.005));
    $("#vazio-contrapartes").toggle(!lista.length && !temDesconhecido);
  }

  function aplicarContrapartes(manterPagina) {
    if (!manterPagina) paginaAtual.contrapartes = 1;
    var lista = filtrarContrapartes();
    renderContrapartesConsolidadas(lista);
    var total = (consolidadas && (consolidadas.pessoas || []).length) || 0;
    $("#f-cp-info").text(lista.length + " de " + total);
    atualizarTotal();
    salvarEstado();
  }

  // ---------------------------------------------------- render Comunicações
  function renderComunicacoes(lista) {
    var $corpo = $("#corpo-comunicacoes").empty();
    $("#vazio-comunicacoes").toggle(!lista.length);
    renderPaginacao("comunicacoes", lista.length);
    var listaPag = obterItensPageinados(lista, "comunicacoes");
    listaPag.forEach(function (c) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(c.nu_indexador != null ? c.nu_indexador : "—").appendTo($tr);
      $("<td>").addClass("col-nome").text(formatarData(c.dt_recebimento)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.no_titular)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(c.no_comunicante)).appendTo($tr);
      var ag = [c.no_cidade_agencia, c.sg_uf_agencia].filter(Boolean).join("/");
      $("<td>").addClass("col-nome").text(ag || "—").appendTo($tr);
      $("<td>").addClass("col-nome rif-valor credito").text(formatarValor(c.vl_campo_b)).appendTo($tr);
      $("<td>").addClass("col-nome rif-valor debito").text(formatarValor(c.vl_campo_c)).appendTo($tr);
      var $td = $("<td>").addClass("col-nome").appendTo($tr);

      // O detalhe (grade de campos + tabelas de contrapartes + texto) era montado para as 50
      // linhas da página antes de qualquer clique — com o texto longo da análise isso custava
      // caro por filtro/tecla. Agora monta na primeira abertura e fica em memória.
      var $det = $("<tr>").addClass("rif-detalhe").hide();
      var $cel = $("<td>").attr("colspan", 8).addClass("rif-detalhe-conteudo").appendTo($det);
      var detalheMontado = false;
      function montarDetalhe() {
        if (detalheMontado) return;
        detalheMontado = true;
        var $grid = $("<div>").addClass("rif-detalhe-grid").appendTo($cel);
        function meta(rot, val) {
          $("<span>").html("<b>" + rot + ":</b> ").append(document.createTextNode(val)).appendTo($grid);
        }
        meta("Titular", txt(c.no_titular));
        meta("CPF/CNPJ titular", txt(c.nu_cpf_cnpj_titular));
        meta("Operação", formatarData(c.dt_operacao));
        meta("Fim do fato", formatarData(c.dt_fim_fato));
        meta("Ocorrência BC", txt(c.nu_ocorrencia_bc));
        meta("Campo A", formatarValor(c.vl_campo_a));
        meta("Campo D", formatarValor(c.vl_campo_d));
        meta("Campo E", formatarValor(c.vl_campo_e));
        meta("CPF/CNPJ comunicante", txt(c.nu_cpf_cnpj_comunicante));

        // tabelas estruturadas de quem creditou / quem foi debitado (extraídas do texto)
        renderContrapartes(c.id_comunicacao_rif).appendTo($cel);

        $("<details>").addClass("rif-texto-wrap").append(
          $("<summary>").text("Texto original da análise")
        ).append(
          $("<div>").addClass("rif-texto").text(c.tx_informacoes_adicionais || "(sem texto de análise)")
        ).appendTo($cel);

        // Reanalisar com IA: a IA lê só o texto desta comunicação e identifica os lançamentos,
        // substituindo a extração por REGEX das contrapartes desta comunicação.
        if (iaDisponivel && (c.tx_informacoes_adicionais || "").trim()) {
          var $ia = $("<div>").addClass("rif-ia-acoes").appendTo($cel);
          var $btnIa = $("<button>").attr("type", "button")
            .addClass("btn btn-secondary rif-detalhe-btn")
            .text("Reanalisar com IA")
            .attr("title", "A IA lê o texto desta comunicação e identifica os lançamentos, substituindo a extração por REGEX")
            .on("click", function () { reanalisarComIA(c.id_comunicacao_rif, $btnIa); })
            .appendTo($ia);
          $("<span>").addClass("rif-ia-nota")
            .text("Substitui as contrapartes extraídas por REGEX desta comunicação.")
            .appendTo($ia);
        }
      }

      $("<button>")
        .addClass("btn btn-secondary rif-detalhe-btn")
        .text("Ver análise")
        .on("click", function () {
          var abrir = $det.is(":hidden");
          if (abrir) montarDetalhe();
          $det.toggle(abrir);
          $(this).text(abrir ? "Ocultar" : "Ver análise");
        })
        .appendTo($td);

      if (idInquerito && window.EntidadesUI) {
        var rot = "comunicação nº " + txt(c.nu_indexador) + " — " +
          (c.no_titular ? c.no_titular + " · " : "") + txt(c.no_comunicante);
        $td.append(" ").append(EntidadesUI.botao({
          idInquerito: idInquerito, tpOrigem: "rif_comunicacao", idOrigem: c.id_comunicacao_rif,
          rotulo: rot, temComentario: !!contagensEnt.rif_comunicacao[c.id_comunicacao_rif], aoMudar: function () {}
        }));
      }

      $corpo.append($tr).append($det);
    });
  }

  // ------------------------------------------------------ render Envolvidos
  function renderEnvolvidos(lista) {
    var $corpo = $("#corpo-envolvidos").empty();
    $("#vazio-envolvidos").toggle(!lista.length);
    renderPaginacao("envolvidos", lista.length);
    var listaPag = obterItensPageinados(lista, "envolvidos");
    listaPag.forEach(function (e) {
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(e.nu_indexador != null ? e.nu_indexador : "—").appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.no_envolvido)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.nu_cpf_cnpj)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.tp_envolvido)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.nu_agencia)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(e.nu_conta)).appendTo($tr);
      var $mark = $("<td>").addClass("col-nome").appendTo($tr);
      $("<span>").addClass("rif-badge " + (ehPJ(e) ? "pj" : "pf")).text(ehPJ(e) ? "PJ" : "PF").appendTo($mark);
      if (ehPep(e)) $mark.append(" ").append($("<span>").addClass("rif-badge pep").text("PEP"));
      if (ehServidor(e)) $mark.append(" ").append($("<span>").addClass("rif-badge pep").text("Servidor"));
      var $acoes = $("<td>").addClass("col-nome").appendTo($tr);
      if (idInquerito && window.EntidadesUI) {
        $acoes.append(EntidadesUI.botao({
          idInquerito: idInquerito, tpOrigem: "rif_envolvido", idOrigem: e.id_envolvido_rif,
          rotulo: "envolvido " + txt(e.no_envolvido), temComentario: !!contagensEnt.rif_envolvido[e.id_envolvido_rif],
          aoMudar: function () {}
        }));
      }
      $corpo.append($tr);
    });
  }

  // ----------------------------------------------------- render Ocorrências
  // O enquadramento do BC não existe no vácuo: cada linha mostra o dinheiro da comunicação a
  // que pertence (casada pelo Indexador) — o alerta de R$ 2 milhões merece outra atenção.
  function comunicacoesPorIndexador() {
    var mapa = {};
    dados.comunicacoes.forEach(function (c) {
      if (c.nu_indexador != null) mapa[c.nu_indexador] = c;
    });
    return mapa;
  }

  function renderOcorrencias(lista) {
    var $corpo = $("#corpo-ocorrencias").empty();
    $("#vazio-ocorrencias").toggle(!lista.length);
    renderPaginacao("ocorrencias", lista.length);
    var mapa = comunicacoesPorIndexador();
    var listaPag = obterItensPageinados(lista, "ocorrencias");
    listaPag.forEach(function (o) {
      var com = mapa[o.nu_indexador];
      var $tr = $("<tr>");
      $("<td>").addClass("col-nome").text(o.nu_indexador != null ? o.nu_indexador : "—").appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(o.id_ocorrencia)).appendTo($tr);
      $("<td>").addClass("col-nome").text(txt(o.tx_ocorrencia)).appendTo($tr);
      $("<td>").addClass("col-nome rif-valor credito").text(com ? formatarValor(com.vl_campo_b) : "—").appendTo($tr);
      $("<td>").addClass("col-nome rif-valor debito").text(com ? formatarValor(com.vl_campo_c) : "—").appendTo($tr);
      $corpo.append($tr);
    });
  }

  // ----------------------------------------------------------- filtragem
  /* Índice de busca pré-computado uma vez por registro (indexarBusca): o contemTexto antigo
     fazia JSON.stringify de cada linha a cada tecla — com milhares de comunicações e o texto
     longo da análise, isso travava a digitação. Agora é só um indexOf na string pronta. */
  function contemTexto(obj, termo) {
    if (!termo) return true;
    return (obj._busca || "").indexOf(termo) !== -1;
  }
  function indexarBusca() {
    ["comunicacoes", "envolvidos", "ocorrencias"].forEach(function (aba) {
      dados[aba].forEach(function (o) {
        delete o._busca;   // reindexar (retry da carga) não pode embutir o índice antigo
        o._busca = JSON.stringify(o).toLowerCase();
      });
    });
  }

  // grava aba/filtros/página na URL (EstadoUrl) — valores vazios/removidos somem da query
  function salvarEstado() {
    if (!window.EstadoUrl) return;
    var ordem = $("#f-com-ordem").val();
    var cpordem = $("#f-cp-ordem").val();
    EstadoUrl.salvar({
      aba: abaAtiva !== "comunicacoes" ? abaAtiva : null,
      busca: $.trim($("#f-com-busca").val()) || null,
      inst: $("#f-com-inst").val() || null,
      uf: $("#f-com-uf").val() || null,
      etitular: $("#f-com-titular").val() || null,
      ordem: ordem && ordem !== "indexador" ? ordem : null,
      mes: filtroMes || null,
      cpessoa: filtroPessoa ? filtroPessoa.chave : null,
      ebusca: $.trim($("#f-env-busca").val()) || null,
      etipo: $("#f-env-tipo").val() || null,
      epessoa: $("#f-env-pessoa").val() || null,
      epep: $("#f-env-pep").is(":checked") ? "1" : null,
      eserv: $("#f-env-servidor").is(":checked") ? "1" : null,
      obusca: $.trim($("#f-oco-busca").val()) || null,
      ocom: $("#f-oco-ordem").val() !== "indexador" ? $("#f-oco-ordem").val() : null,
      cpbusca: $.trim($("#f-cp-busca").val()) || null,
      cpdir: $("#f-cp-dir").val() || null,
      cpord: cpordem && cpordem !== "volume" ? cpordem : null,
      cpimba: $("#f-cp-simba").is(":checked") ? "1" : null,
      pag: paginaAtual[abaAtiva] > 1 ? paginaAtual[abaAtiva] : null
    });
  }

  // devolve os filtros salvos para os campos (chamado após popularFiltros, com as options prontas)
  function restaurarFiltros() {
    if (estadoSalvo.busca) $("#f-com-busca").val(estadoSalvo.busca);
    if (estadoSalvo.inst) $("#f-com-inst").val(estadoSalvo.inst);
    if (estadoSalvo.uf) $("#f-com-uf").val(estadoSalvo.uf);
    if (estadoSalvo.etitular) $("#f-com-titular").val(estadoSalvo.etitular);
    if (estadoSalvo.ordem) $("#f-com-ordem").val(estadoSalvo.ordem);
    if (estadoSalvo.ebusca) $("#f-env-busca").val(estadoSalvo.ebusca);
    if (estadoSalvo.etipo) $("#f-env-tipo").val(estadoSalvo.etipo);
    if (estadoSalvo.epessoa) $("#f-env-pessoa").val(estadoSalvo.epessoa);
    if (estadoSalvo.epep) $("#f-env-pep").prop("checked", true);
    if (estadoSalvo.eserv) $("#f-env-servidor").prop("checked", true);
    if (estadoSalvo.obusca) $("#f-oco-busca").val(estadoSalvo.obusca);
    if (estadoSalvo.ocom) $("#f-oco-ordem").val(estadoSalvo.ocom);
    if (estadoSalvo.cpbusca) $("#f-cp-busca").val(estadoSalvo.cpbusca);
    if (estadoSalvo.cpdir) $("#f-cp-dir").val(estadoSalvo.cpdir);
    if (estadoSalvo.cpord) $("#f-cp-ordem").val(estadoSalvo.cpord);
    if (estadoSalvo.cpimba) $("#f-cp-simba").prop("checked", true);
    if (estadoSalvo.mes) filtroMes = estadoSalvo.mes;
    // a pessoa vem por chave: os ids das comunicações dela só podem ser recomputados com
    // envolvidos + contrapartes carregados (é o que idsComPessoa consulta)
    if (estadoSalvo.cpessoa && !filtroPessoa) {
      var nome = estadoSalvo.cpessoa;
      var p = ((consolidadas && consolidadas.pessoas) || []).filter(function (x) { return x.chave === nome; })[0];
      if (p) nome = p.nome || p.chave;
      filtroPessoa = { chave: estadoSalvo.cpessoa, nome: nome, ids: idsComPessoa(estadoSalvo.cpessoa) };
    }
    var pag = parseInt(estadoSalvo.pag, 10);
    if (pag > 1) paginaAtual[abaAtiva] = pag;
  }

  function filtrarComunicacoes() {
    var termo = $.trim($("#f-com-busca").val()).toLowerCase();
    var inst = $("#f-com-inst").val();
    var uf = $("#f-com-uf").val();
    var titular = $("#f-com-titular").val();
    var ordem = $("#f-com-ordem").val();
    var lista = dados.comunicacoes.filter(function (c) {
      if (inst && c.no_comunicante !== inst) return false;
      if (uf && c.sg_uf_agencia !== uf) return false;
      if (titular && c.no_titular !== titular) return false;
      if (filtroMes && mesDe(c) !== filtroMes) return false;
      if (filtroPessoa && !filtroPessoa.ids[c.id_comunicacao_rif]) return false;
      return contemTexto(c, termo);
    });
    lista.sort(function (a, b) {
      if (ordem === "credito") return num(b.vl_campo_b) - num(a.vl_campo_b);
      if (ordem === "debito") return num(b.vl_campo_c) - num(a.vl_campo_c);
      if (ordem === "recente") return String(b.dt_recebimento || "").localeCompare(String(a.dt_recebimento || ""));
      if (ordem === "antigo") return String(a.dt_recebimento || "").localeCompare(String(b.dt_recebimento || ""));
      return num(a.nu_indexador) - num(b.nu_indexador);
    });
    return lista;
  }

  // chips dos filtros "financeiros" (mês da série e pessoa vinda das contrapartes consolidadas)
  function atualizarChipsCom() {
    var $c = $("#rif-filtros-ativos").empty();
    if (filtroMes) {
      $("<span>").addClass("rif-chip-filtro").text("Mês: " + mesBR(filtroMes) + " ✕")
        .attr("title", "Limpar o filtro de mês")
        .on("click", function () { filtroMes = null; renderSerie(); aplicarComunicacoes(); })
        .appendTo($c);
    }
    if (filtroPessoa) {
      $("<span>").addClass("rif-chip-filtro").text("Pessoa: " + filtroPessoa.nome + " ✕")
        .attr("title", "Limpar o filtro de pessoa")
        .on("click", function () { filtroPessoa = null; aplicarComunicacoes(); })
        .appendTo($c);
    }
  }

  function aplicarComunicacoes(manterPagina) {
    if (!manterPagina) paginaAtual.comunicacoes = 1; // filtros novos voltam à página 1
    var lista = filtrarComunicacoes();
    renderComunicacoes(lista);
    $("#f-com-info").text(lista.length + " de " + dados.comunicacoes.length);
    atualizarChipsCom();
    atualizarTotal();
    salvarEstado();
  }

  function filtrarEnvolvidos() {
    var termo = $.trim($("#f-env-busca").val()).toLowerCase();
    var tipo = $("#f-env-tipo").val();
    var pessoa = $("#f-env-pessoa").val();
    var soPep = $("#f-env-pep").is(":checked");
    var soServ = $("#f-env-servidor").is(":checked");
    var lista = dados.envolvidos.filter(function (e) {
      if (tipo && e.tp_envolvido !== tipo) return false;
      if (pessoa === "pf" && ehPJ(e)) return false;
      if (pessoa === "pj" && !ehPJ(e)) return false;
      if (soPep && !ehPep(e)) return false;
      if (soServ && !ehServidor(e)) return false;
      return contemTexto(e, termo);
    });
    return lista;
  }

  function aplicarEnvolvidos(manterPagina) {
    if (!manterPagina) paginaAtual.envolvidos = 1;
    var lista = filtrarEnvolvidos();
    renderEnvolvidos(lista);
    $("#f-env-info").text(lista.length + " de " + dados.envolvidos.length);
    atualizarTotal();
    salvarEstado();
  }

  function filtrarOcorrencias() {
    var termo = $.trim($("#f-oco-busca").val()).toLowerCase();
    var ordem = $("#f-oco-ordem").val();
    var mapa = comunicacoesPorIndexador();
    var lista = dados.ocorrencias.filter(function (o) { return contemTexto(o, termo); });
    if (ordem === "valor") {
      lista = lista.slice().sort(function (a, b) {
        var ca = mapa[a.nu_indexador], cb = mapa[b.nu_indexador];
        var va = ca ? num(ca.vl_campo_b) + num(ca.vl_campo_c) : 0;
        var vb = cb ? num(cb.vl_campo_b) + num(cb.vl_campo_c) : 0;
        return vb - va;
      });
    }
    return lista;
  }

  function aplicarOcorrencias(manterPagina) {
    if (!manterPagina) paginaAtual.ocorrencias = 1;
    var lista = filtrarOcorrencias();
    renderOcorrencias(lista);
    $("#f-oco-info").text(lista.length + " de " + dados.ocorrencias.length);
    atualizarTotal();
    salvarEstado();
  }

  var APLICAR = {
    comunicacoes: aplicarComunicacoes, contrapartes: aplicarContrapartes,
    envolvidos: aplicarEnvolvidos, ocorrencias: aplicarOcorrencias
  };

  // ------------------------------------------------------------------ abas
  function rotuloAba(aba) {
    var n = aba === "contrapartes" ? ((consolidadas && (consolidadas.pessoas || []).length) || 0)
                                   : (dados[aba] || []).length;
    return { comunicacoes: "Comunicações", contrapartes: "Contrapartes",
             envolvidos: "Envolvidos", ocorrencias: "Ocorrências" }[aba] + " (" + n + ")";
  }
  function atualizarRotulosAbas() {
    $(".rif-aba").each(function () { $(this).text(rotuloAba($(this).data("aba"))); });
  }
  function trocarAba(aba) {
    abaAtiva = aba;
    if (opcoesExcel) opcoesExcel.nome = "rif_" + aba;
    $(".rif-aba").removeClass("ativa").attr("aria-selected", "false")
      .filter('[data-aba="' + aba + '"]').addClass("ativa").attr("aria-selected", "true");
    $(".rif-painel").removeClass("ativo").filter('[data-painel="' + aba + '"]').addClass("ativo");
    // a página exibida na aba nova (pode ser diferente da antiga) também vai para a URL
    paginaAtual[aba] = Math.max(1, paginaAtual[aba] || 1);
    salvarEstado();
    atualizarTotal();
  }
  $(".rif-aba").on("click", function () { trocarAba($(this).data("aba")); });

  // ------------------------------------------------------------------ Excel
  /* Um botão só, que exporta a aba aberta INTEIRA — não a página desenhada. A tela pagina no
     cliente de 50 em 50, então o modo padrão do ExportarExcel (ler o DOM) devolveria 50 de 4 mil
     linhas sem avisar ninguém. Por isso o provedor `dados`: a planilha sai da mesma lista
     filtrada que a tela renderiza, antes do fatiamento por página.

     As colunas vão além das da tabela: campos A/D/E, data da operação e do fim do fato,
     ocorrência do BC, CPF/CNPJ do comunicante e o texto da análise estão no registro e só
     aparecem na linha de detalhe, que se abre uma a uma. Na planilha custam uma coluna e poupam
     a volta ao sistema — que é justamente para o que se exporta.

     Campo vazio sai vazio, e não com o "—" que a tela usa: na planilha o travessão é texto, e
     texto atrapalha filtro, ordenação e soma. */
  function cel(v) { return (v === null || v === undefined) ? "" : String(v); }
  function celData(v) { var d = formatarData(v); return d === "—" ? "" : d; }
  function celValor(v) { var x = formatarValor(v); return x === "—" ? "" : x; }
  function celSN(sim) { return sim ? "Sim" : "Não"; }

  var EXCEL_ABA = {
    comunicacoes: {
      colunas: ["#", "Recebimento", "Operação", "Fim do fato", "Titular", "CPF/CNPJ do titular",
                "Instituição comunicante", "CPF/CNPJ do comunicante", "Cidade da agência",
                "UF da agência", "Campo A (R$)", "Crédito — campo B (R$)", "Débito — campo C (R$)",
                "Campo D (R$)", "Campo E (R$)", "Ocorrência BC", "Texto da análise"],
      linha: function (c) {
        return [cel(c.nu_indexador), celData(c.dt_recebimento), celData(c.dt_operacao),
                celData(c.dt_fim_fato), cel(c.no_titular), cel(c.nu_cpf_cnpj_titular),
                cel(c.no_comunicante), cel(c.nu_cpf_cnpj_comunicante), cel(c.no_cidade_agencia),
                cel(c.sg_uf_agencia), celValor(c.vl_campo_a), celValor(c.vl_campo_b),
                celValor(c.vl_campo_c), celValor(c.vl_campo_d), celValor(c.vl_campo_e),
                cel(c.nu_ocorrencia_bc), cel(c.tx_informacoes_adicionais)];
      },
      filtrar: filtrarComunicacoes
    },
    envolvidos: {
      colunas: ["#", "Nome", "CPF/CNPJ", "Tipo", "Agência", "Conta", "Pessoa", "PEP", "Servidor"],
      linha: function (e) {
        return [cel(e.nu_indexador), cel(e.no_envolvido), cel(e.nu_cpf_cnpj), cel(e.tp_envolvido),
                cel(e.nu_agencia), cel(e.nu_conta), ehPJ(e) ? "PJ" : "PF",
                celSN(ehPep(e)), celSN(ehServidor(e))];
      },
      filtrar: filtrarEnvolvidos
    },
    ocorrencias: {
      colunas: ["#", "Código", "Enquadramento / alerta", "Crédito da comunicação (R$)", "Débito da comunicação (R$)"],
      linha: function (o) {
        var mapa = comunicacoesPorIndexador();
        var com = mapa[o.nu_indexador];
        return [cel(o.nu_indexador), cel(o.id_ocorrencia), cel(o.tx_ocorrencia),
                com ? celValor(com.vl_campo_b) : "", com ? celValor(com.vl_campo_c) : ""];
      },
      filtrar: filtrarOcorrencias
    },
    contrapartes: {
      colunas: ["Contraparte", "CPF/CNPJ", "PF/PJ", "PEP", "Servidor", "Titular no RIF",
                "Também no SIMBA (R$)", "Creditou (R$)", "Debitado (R$)", "Saldo (R$)",
                "Identificado à mão (R$)", "Comunicações", "1º movimento", "Último movimento", "Observação"],
      linha: function (p) {
        return [cel(p.nome), cel(p.documento), cel(p.tp_pessoa),
                p.pep ? "Sim" : "Não", p.servidor ? "Sim" : "Não", p.titular ? "Sim" : "Não",
                p.simba ? celValor(p.simba.vl_total) : "",
                celValor(num(p.vl_credito) + num(p.vl_identificado_credito)),
                celValor(num(p.vl_debito) + num(p.vl_identificado_debito)),
                celValor(p.saldo),
                celValor(num(p.vl_identificado_credito) + num(p.vl_identificado_debito)),
                cel(p.qt_comunicacoes), celData(p.dt_primeiro), celData(p.dt_ultimo), cel(p.tx_obs)];
      },
      filtrar: filtrarContrapartes
    }
  };

  var opcoesExcel = null;

  function instalarExcel() {
    if (!window.ExportarExcel) return;
    opcoesExcel = {
      nome: "rif_" + abaAtiva,
      dados: function () {
        var cfg = EXCEL_ABA[abaAtiva];
        if (!cfg) return { colunas: [], linhas: [] };
        return { colunas: cfg.colunas, linhas: cfg.filtrar().map(cfg.linha) };
      }
    };
    ExportarExcel.instalar(opcoesExcel);
  }

  function atualizarTotal() {
    var visiveis = $("#corpo-" + abaAtiva + " > tr").not(".rif-detalhe").length;
    var rot = { comunicacoes: "comunicações", contrapartes: "contrapartes",
                envolvidos: "envolvidos", ocorrencias: "ocorrências" }[abaAtiva];
    $("#total-registros").text(visiveis.toLocaleString("pt-BR") + " " + rot);
  }

  // binds de filtro — os handlers são sempre wrappers sem argumento: aplicar*(manterPagina) não
  // pode receber o objeto de evento (seria truthy e a filtragem nova esqueceria a página 1).
  // A busca tem debounce (AnalyserUtils.debounce, o padrão das outras telas): re-renderizar a
  // tabela inteira a cada tecla travava em RIFs com milhares de comunicações.
  var debounce = window.AnalyserUtils.debounce;
  $("#f-com-busca").on("input", debounce(function () { aplicarComunicacoes(); }, 250));
  $("#f-com-inst, #f-com-uf, #f-com-titular, #f-com-ordem").on("change", function () { aplicarComunicacoes(); });
  $("#f-env-busca").on("input", debounce(function () { aplicarEnvolvidos(); }, 250));
  $("#f-env-tipo, #f-env-pessoa").on("change", function () { aplicarEnvolvidos(); });
  $("#f-env-pep, #f-env-servidor").on("change", function () { aplicarEnvolvidos(); });
  $("#f-oco-busca").on("input", debounce(function () { aplicarOcorrencias(); }, 250));
  $("#f-oco-ordem").on("change", function () { aplicarOcorrencias(); });
  $("#f-cp-busca").on("input", debounce(function () { aplicarContrapartes(); }, 250));
  $("#f-cp-dir, #f-cp-ordem, #f-cp-simba").on("change", function () { aplicarContrapartes(); });

  // clique/teclado num mês da série alterna o filtro de mês (clicar de novo limpa)
  $(document).on("click keydown", "#rif-serie .mes-clique", function (e) {
    if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
    if (e.type === "keydown") e.preventDefault();
    var mes = $(this).attr("data-mes");
    filtroMes = filtroMes === mes ? null : mes;
    renderSerie();
    trocarAba("comunicacoes");
    aplicarComunicacoes();
  });

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

  // visão financeira do RIF inteiro (aba Contrapartes + barra de cobertura)
  function carregarConsolidadas() {
    return $.getJSON("/api/investigacao/rifs/" + idRif + "/contrapartes-consolidadas").done(function (resp) {
      consolidadas = resp || { pessoas: [], desconhecido: {}, cobertura: {} };
    });
  }

  // depois de identificar à mão / reanalisar com IA a TB05 mudou: recarrega a consolidação e
  // redesenha o que depende dela (cobertura + aba Contrapartes + rótulo da aba)
  function atualizarConsolidadas() {
    return carregarConsolidadas().always(function () {
      renderCobertura();
      atualizarRotulosAbas();
      aplicarContrapartes(true);
    });
  }

  // contagens de entidade + marcação (EntidadesUI) para destacar os botões 📌/🗒️ por linha
  function carregarContagens(tp) {
    var d = $.Deferred();
    if (!idInquerito || !window.EntidadesUI) { d.resolve(); return d; }
    EntidadesUI.carregarContagens(idInquerito, tp, function (c) { contagensEnt[tp] = c || {}; d.resolve(); });
    return d;
  }

  var inicializado = false;   // passos que só rodam uma vez (options de select, botão do Excel)

  /* Carga inicial com registro de falhas: um $.when(...).always() puro desenhava a tela "limpa"
     com 0 comunicações quando a API falhava — o investigador não tinha como saber que era erro.
     Agora cada requisição vigiada que falha vira um Estados.erro com "Tentar de novo" no topo do
     card, e o conteúdo parcial (se houver) continua visível abaixo. */
  function carregarTudo() {
    $("#rif-estado").empty();
    var falhas = [];
    function vigia(p, nome) { return p.fail(function () { falhas.push(nome); }); }

    return $.when(
      vigia(carregar("comunicacoes"), "comunicações"),
      vigia(carregar("envolvidos"), "envolvidos"),
      vigia(carregar("ocorrencias"), "ocorrências"),
      vigia(carregarContrapartes(), "contrapartes"),
      vigia(carregarIdentificacoes(), "identificações"),
      vigia(carregarConsolidadas(), "contrapartes consolidadas"),
      checarIaDisponivel(),
      carregarContagens("rif_comunicacao"), carregarContagens("rif_envolvido"), carregarContagens("rif_contraparte")
    ).always(function () {
      if (falhas.length) {
        Estados.erro($("#rif-estado"), {
          mensagem: "Não foi possível carregar: " + falhas.join(", ") + ". Os números abaixo podem estar incompletos.",
          aoTentarDeNovo: carregarTudo
        });
      }
      anexarTitulares();
      indexarBusca();
      if (!inicializado) {
        inicializado = true;
        atualizarTitularRIF();
        popularFiltros();
        restaurarFiltros();   // pode reconstituir filtroPessoa — precisa de envolvidos+contrapartes prontos
        instalarExcel();
      }
      renderResumo();
      renderSerie();
      renderCobertura();
      atualizarRotulosAbas();
      aplicarComunicacoes(true);
      aplicarContrapartes(true);
      aplicarEnvolvidos(true);
      aplicarOcorrencias(true);
      trocarAba(abaAtiva);
    });
  }

  abaAtiva = ["comunicacoes", "contrapartes", "envolvidos", "ocorrencias"].indexOf(abaInicial) !== -1 ? abaInicial : "comunicacoes";
  carregarTudo();

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
      $("#rif-titular-nome").text(titularMaisComum);
      $("#rif-titular-doc").text(txt(cpfMaisComum || "—"));
      $("#rif-titular").show();
    }
  }


})();
