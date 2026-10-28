(function () {
  "use strict";

  var UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE",
    "SP", "TO"];

  var $corpo = $("#delegacias-corpo");
  var $vazio = $("#delegacias-vazio");
  var $erro = $("#delegacias-erro");
  var $contador = $("#delegacias-contador");
  var $filtroInfo = $("#filtro-info");

  var $busca = $("#filtro-busca");
  var $filtroUf = $("#filtro-uf");
  var $filtroUnidade = $("#filtro-unidade");
  var $filtroIa = $("#filtro-ia");
  var $filtroNo = $("#filtro-no");
  var $filtroSemUnidade = $("#filtro-sem-unidade");
  var $filtroOrdem = $("#filtro-ordem");
  var $filtroLimpar = $("#filtro-limpar");

  var $modalNovo = $("#modal-nova-delegacia");
  var $formNovo = $("#nova-delegacia-form");
  var $statusNovo = $("#nova-delegacia-status");
  var $btnNovo = $("#nova-delegacia-btn");

  var $modalEditar = $("#modal-editar-delegacia");
  var $formEditar = $("#editar-delegacia-form");
  var $statusEditar = $("#editar-delegacia-status");
  var $btnEditar = $("#editar-delegacia-btn");
  var $inputEditarNome = $("#editar-delegacia-nome");
  var $inputEditarUf = $("#editar-delegacia-uf");
  var $inputEditarUnidade = $("#editar-delegacia-unidade");
  var $inputEditarIaAtiva = $("#editar-delegacia-ia-ativa");
  var $inputEditarNoAtiva = $("#editar-delegacia-no-ativa");
  var editandoId = null;
  var ultimoFocoModal = null;

  // Estado local: espelha o servidor por id para os toggles não se sobrescreverem.
  // Antes cada toggle lia o objeto `d` capturado no render (valor antigo) e o PUT de um
  // campo mandava o outro campo desatualizado junto — alternar IA e Nó em sequência
  // sem recarregar fazia um desfazer o outro. Agora o PUT parcial manda só o campo
  // alterado (ver api/rotas/delegacias.py) e o estado é atualizado no sucesso.
  var estadoPorId = {};
  var listaCompleta = [];
  var carregando = false;

  function popularUfs($select, incluirTodas) {
    var atual = $select.val();
    $select.empty();
    if (incluirTodas) {
      $select.append($("<option>").val("").text($select.attr("id") === "filtro-uf" ? "Todas as UFs" : "Sem UF"));
    } else {
      $select.append($("<option>").val("").text("Sem UF"));
    }
    UFS.forEach(function (uf) {
      $select.append($("<option>").val(uf).text(uf));
    });
    if (atual) $select.val(atual);
  }

  popularUfs($filtroUf, true);
  popularUfs($("#nova-delegacia-uf"), false);
  popularUfs($inputEditarUf, false);
  popularUfs($("#nova-unidade-uf"), false);
  popularUfs($("#editar-unidade-uf"), false);

  // ---------- unidades superiores (árvore — ver docs/unidades_prompts.md) ----------
  // GET /api/unidades é de admin: para quem não é (a tela de delegacias abre para leitura em
  // alguns perfis), a fetch falha, o card fica vazio e os selects de unidade mostram só
  // "Sem unidade" — a API também não deixaria gravar, então nada se perde.
  var listaUnidades = [];
  var $corpoUnidades = $("#unidades-corpo");
  var $vazioUnidades = $("#unidades-vazio");
  var NIVEL_ROTULO = { uf: "UF / superintendência", coordenacao: "Coordenação" };

  function unidadePorId(id) {
    return listaUnidades.find(function (u) { return u.id_unidade === id; }) || null;
  }

  function nomeUnidade(id) {
    var u = unidadePorId(id);
    if (!u) return "—";
    return u.sg_unidade ? u.no_unidade + " (" + u.sg_unidade + ")" : u.no_unidade;
  }

  function popularSelectUnidade($select, atual) {
    var valor = atual !== undefined ? atual : $select.val();
    $select.find("option:not(:first)").remove();
    listaUnidades.forEach(function (u) {
      $select.append($("<option>").val(u.id_unidade).text(nomeUnidade(u.id_unidade)));
    });
    if (valor) $select.val(String(valor));
    if (!$select.val()) $select.val("");
  }

  // ---------- UF herdada: com unidade superior, quem define a UF é a árvore ----------
  var UF_AJUDA_PADRAO = "Escolha a UF — sem unidade escolhida, a delegacia entra na padrão do estado (SR/PF/<UF>).";

  function ufEfetivaDaUnidade(id_unidade) {
    var id = id_unidade, guarda = 0;
    while (id && guarda++ < 20) {
      var u = unidadePorId(id);
      if (!u) return "";
      if (u.uf) return String(u.uf).toUpperCase();
      id = u.id_unidade_pai;
    }
    return "";
  }

  function aplicarUfHerdada($unidade, $uf, $ajuda) {
    var id = Number($unidade.val() || 0);
    var uf = id ? ufEfetivaDaUnidade(id) : "";
    if (uf) {
      $uf.val(uf).prop("disabled", true);
      $ajuda.text("Herdada da unidade superior (" + nomeUnidade(id) + ").");
    } else {
      $uf.prop("disabled", false);
      $ajuda.text(UF_AJUDA_PADRAO);
    }
  }

  function descendentesDe(id_unidade) {
    // espelha o anti-ciclo da API: nem a própria nem nenhuma descendente pode virar pai
    var fora = {};
    fora[id_unidade] = true;
    var mudou = true;
    while (mudou) {
      mudou = false;
      listaUnidades.forEach(function (u) {
        if (!fora[u.id_unidade] && u.id_unidade_pai && fora[u.id_unidade_pai]) {
          fora[u.id_unidade] = true;
          mudou = true;
        }
      });
    }
    return fora;
  }

  function popularSelectPai($select, atual, excluirId) {
    var fora = excluirId ? descendentesDe(excluirId) : {};
    var valor = atual !== undefined ? atual : $select.val();
    $select.find("option:not(:first)").remove();
    listaUnidades.forEach(function (u) {
      if (!fora[u.id_unidade]) {
        $select.append($("<option>").val(u.id_unidade).text(nomeUnidade(u.id_unidade)));
      }
    });
    if (valor && !fora[valor]) $select.val(String(valor));
    if (!$select.val()) $select.val("");
  }

  // ---------- abas (mesma mecânica visual das Configurações) ----------
  $("#delegacias-tabs .extracao-tab").on("click", function () {
    var aba = $(this).data("tab");
    $("#delegacias-tabs .extracao-tab").removeClass("active").attr("aria-selected", "false");
    $(this).addClass("active").attr("aria-selected", "true");
    $("#painel-delegacias").toggleClass("hidden", aba !== "delegacias");
    $("#painel-unidades").toggleClass("hidden", aba !== "unidades");
    try { window.localStorage.setItem("delegacias_aba", aba); } catch (e) { /* sem storage */ }
  });
  (function () {
    var aba = "delegacias";
    try { aba = window.localStorage.getItem("delegacias_aba") || "delegacias"; } catch (e) { /* ignore */ }
    if (aba === "unidades") $("#delegacias-tabs .extracao-tab[data-tab='unidades']").trigger("click");
  })();

  // ---------- filtros da aba Unidades ----------
  function ufEfetivaLista(u) {
    var atual = u.id_unidade, guarda = 0;
    while (atual && guarda++ < 20) {
      var n = unidadePorId(atual);
      if (!n) return "";
      if (n.uf) return String(n.uf).toUpperCase();
      atual = n.id_unidade_pai;
    }
    return "";
  }

  function ordemArvore() {
    // pais antes dos filhos (ordem de leitura da árvore); filtros usam esta ordem
    var porPai = {}, raizes = [];
    listaUnidades.forEach(function (u) {
      if (u.id_unidade_pai && unidadePorId(u.id_unidade_pai)) {
        (porPai[u.id_unidade_pai] = porPai[u.id_unidade_pai] || []).push(u);
      } else {
        raizes.push(u);
      }
    });
    var porNome = function (a, b) { return String(a.no_unidade).localeCompare(String(b.no_unidade), "pt-BR"); };
    raizes.sort(porNome);
    var saida = [];
    (function descer(lista) {
      lista.slice().sort(porNome).forEach(function (u) {
        saida.push(u);
        if (porPai[u.id_unidade]) descer(porPai[u.id_unidade]);
      });
    })(raizes);
    return saida;
  }

  function popularFiltrosUnidade() {
    var $uf = $("#ufiltro-uf"), $pai = $("#ufiltro-pai");
    var atualUf = $uf.val(), atualPai = $pai.val();
    var ufs = {};
    listaUnidades.forEach(function (u) { if (u.uf) ufs[String(u.uf).toUpperCase()] = true; });
    $uf.find("option:not(:first)").remove();
    Object.keys(ufs).sort().forEach(function (uf) {
      $uf.append($("<option>").val(uf).text(uf));
    });
    $pai.find("option[value!='__raiz']").remove();
    listaUnidades.forEach(function (u) {
      $pai.append($("<option>").val(u.id_unidade).text(u.sg_unidade || u.no_unidade));
    });
    if (atualUf) $uf.val(atualUf);
    if (atualPai) $pai.val(atualPai);
  }

  function temFiltroUnidade() {
    return !!($("#ufiltro-busca").val().trim() || $("#ufiltro-uf").val() ||
      $("#ufiltro-pai").val() || $("#ufiltro-nivel").val());
  }

  function renderUnidades() {
    var termo = $("#ufiltro-busca").val().trim().toUpperCase();
    var uf = $("#ufiltro-uf").val();
    var pai = $("#ufiltro-pai").val();
    var nivel = $("#ufiltro-nivel").val();
    var lista = ordemArvore().filter(function (u) {
      if (termo && (String(u.no_unidade || "") + " " + String(u.sg_unidade || "")).toUpperCase().indexOf(termo) === -1) return false;
      if (uf && ufEfetivaLista(u) !== uf) return false;
      if (nivel && u.tp_nivel !== nivel) return false;
      if (pai === "__raiz" && u.id_unidade_pai) return false;
      if (pai && pai !== "__raiz" && Number(u.id_unidade_pai || 0) !== Number(pai)) return false;
      return true;
    });
    $corpoUnidades.empty();
    lista.forEach(function (u) { $corpoUnidades.append(renderLinhaUnidade(u)); });
    $vazioUnidades.prop("hidden", lista.length > 0);
    $vazioUnidades.text(listaUnidades.length
      ? "Nenhuma unidade com esses filtros."
      : "Nenhuma unidade cadastrada — as delegacias trabalham sem hierarquia, como antes.");
    $("#ufiltro-info").text(temFiltroUnidade()
      ? "Mostrando " + lista.length + " de " + listaUnidades.length
      : "");
  }

  $("#ufiltro-busca").on("input", AnalyserUtils.debounce(renderUnidades, 150));
  $("#ufiltro-uf, #ufiltro-pai, #ufiltro-nivel").on("change", renderUnidades);

  function renderLinhaUnidade(u) {
    var $tr = $("<tr>");
    var $nome = $("<span>").addClass("arq-tag").text(u.no_unidade).appendTo($tr);
    if (u.id_unidade_pai) {
      $nome.text("↳ " + u.no_unidade);
      $nome.attr("title", "Acima dela: " + nomeUnidade(u.id_unidade_pai));
    }
    $("<td>").text(u.sg_unidade || "—").appendTo($tr);
    $("<td>").text(NIVEL_ROTULO[u.tp_nivel] || u.tp_nivel).appendTo($tr);
    $("<td>").text(u.id_unidade_pai ? nomeUnidade(u.id_unidade_pai) : "—").appendTo($tr);
    var ufEf = u.uf ? String(u.uf).toUpperCase() : ufEfetivaLista(u);
    var $tdUf = $("<td>").text(ufEf || "—").appendTo($tr);
    if (!u.uf && ufEf) $tdUf.attr("title", "Herdada de uma unidade acima");
    var qt = Number(u.qt_delegacias || 0);
    $("<td>").text(qt === 1 ? "1 delegacia" : qt + " delegacias")
      .attr("title", "Inclui as unidades abaixo desta").appendTo($tr);
    var $acoes = $("<td>").addClass("delegacia-acoes").appendTo($tr);
    $("<button>").attr("type", "button").addClass("btn btn-secondary btn-sm")
      .text("IA").attr("aria-label", "IA própria da unidade " + u.no_unidade)
      .on("click", function () { abrirIaEntidade("unidade", u.id_unidade, u.sg_unidade || u.no_unidade); })
      .appendTo($acoes);
    $("<button>").attr("type", "button").addClass("btn btn-secondary btn-sm")
      .text("Editar").attr("aria-label", "Editar unidade " + u.no_unidade)
      .on("click", function () { abrirEditarUnidade(u); }).appendTo($acoes);
    $("<button>").attr("type", "button").addClass("btn btn-danger btn-sm")
      .text("Excluir").attr("aria-label", "Excluir unidade " + u.no_unidade)
      .on("click", function () { excluirUnidade(u); }).appendTo($acoes);
    return $tr;
  }

  function carregarUnidades() {
    return $.getJSON("/api/unidades")
      .done(function (lista) { listaUnidades = lista || []; })
      .fail(function () { listaUnidades = []; })
      .always(function () {
        popularFiltrosUnidade();
        renderUnidades();
        popularSelectUnidade($("#nova-delegacia-unidade"), "");
        popularSelectUnidade($("#editar-delegacia-unidade"), "");
        popularSelectUnidade($filtroUnidade, $filtroUnidade.val());
        aplicarFiltros();
      });
  }

  var editandoUnidadeId = null;

  function abrirEditarUnidade(u) {
    editandoUnidadeId = u.id_unidade;
    $("#editar-unidade-nome").val(u.no_unidade);
    $("#editar-unidade-sigla").val(u.sg_unidade || "");
    $("#editar-unidade-nivel").val(u.tp_nivel);
    popularSelectPai($("#editar-unidade-pai"), "", u.id_unidade);
    $("#editar-unidade-pai").val(u.id_unidade_pai ? String(u.id_unidade_pai) : "");
    $("#editar-unidade-uf").val(u.uf ? String(u.uf).toUpperCase() : "");
    $("#editar-unidade-status").text("").removeClass("success error");
    abrirModal($("#modal-editar-unidade"));
    $("#editar-unidade-nome").focus();
  }

  function excluirUnidade(u) {
    Dialogo.confirmar({
      titulo: "Excluir unidade",
      mensagem: "Excluir \"" + u.no_unidade + "\"? O prompt de IA dela (se houver) vai junto.",
      rotuloOk: "Excluir",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/unidades/" + u.id_unidade, method: "DELETE" })
        .done(function () {
          Toast.sucesso("Unidade \"" + u.no_unidade + "\" excluída.");
          carregarUnidades();
        })
        .fail(function (xhr) {
          Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível excluir.");
        });
    });
  }

  $("#abrir-nova-unidade-btn").on("click", function () {
    $("#nova-unidade-form")[0].reset();
    popularSelectPai($("#nova-unidade-pai"), "", null);
    $("#nova-unidade-status").text("").removeClass("success error");
    abrirModal($("#modal-nova-unidade"));
    $("#nova-unidade-nome").focus();
  });
  $("#nova-unidade-fechar").on("click", function () { fecharModal($("#modal-nova-unidade")); });
  $("#modal-nova-unidade").on("click", function (e) { if (e.target === this) fecharModal($("#modal-nova-unidade")); });
  $("#editar-unidade-fechar").on("click", function () { fecharModal($("#modal-editar-unidade")); });
  $("#modal-editar-unidade").on("click", function (e) { if (e.target === this) fecharModal($("#modal-editar-unidade")); });

  function payloadUnidade(prefixo) {
    var nome = nomeValido($("#" + prefixo + "-unidade-nome"), $("#" + prefixo + "-unidade-status"));
    if (!nome) return null;
    return {
      no_unidade: nome,
      sg_unidade: $.trim($("#" + prefixo + "-unidade-sigla").val()),
      tp_nivel: $("#" + prefixo + "-unidade-nivel").val(),
      id_unidade_pai: $("#" + prefixo + "-unidade-pai").val() ? Number($("#" + prefixo + "-unidade-pai").val()) : null,
      uf: $.trim($("#" + prefixo + "-unidade-uf").val())
    };
  }

  $("#nova-unidade-form").on("submit", function (e) {
    e.preventDefault();
    var dados = payloadUnidade("nova");
    if (!dados) return;
    $("#nova-unidade-btn").prop("disabled", true);
    $.ajax({ url: "/api/unidades", method: "POST", contentType: "application/json", data: JSON.stringify(dados) })
      .done(function () {
        Toast.sucesso("Unidade \"" + dados.no_unidade + "\" criada.");
        fecharModal($("#modal-nova-unidade"));
        carregarUnidades();
      })
      .fail(function (xhr) {
        $("#nova-unidade-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar unidade.").addClass("error");
      })
      .always(function () { $("#nova-unidade-btn").prop("disabled", false); });
  });

  $("#editar-unidade-form").on("submit", function (e) {
    e.preventDefault();
    if (!editandoUnidadeId) return;
    var dados = payloadUnidade("editar");
    if (!dados) return;
    $("#editar-unidade-btn").prop("disabled", true);
    $.ajax({ url: "/api/unidades/" + editandoUnidadeId, method: "PUT", contentType: "application/json", data: JSON.stringify(dados) })
      .done(function () {
        Toast.sucesso("Unidade atualizada.");
        fecharModal($("#modal-editar-unidade"));
        carregarUnidades();
      })
      .fail(function (xhr) {
        $("#editar-unidade-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao editar unidade.").addClass("error");
      })
      .always(function () { $("#editar-unidade-btn").prop("disabled", false); });
  });

  function formatarData(iso) {
    if (!iso) return "—";
    var dt = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    if (isNaN(dt.getTime())) return "—";
    return dt.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function rotuloDelegacia(d) {
    var nome = d.no_delegacia || ("#" + d.id_delegacia);
    return d.uf ? nome + " (" + String(d.uf).toUpperCase() + ")" : nome;
  }

  function temFiltroAtivo() {
    return !!($.trim($busca.val()) || $filtroUf.val() || $filtroUnidade.val() ||
      $filtroIa.val() || $filtroNo.val() || $filtroSemUnidade.val() ||
      ($filtroOrdem.val() && $filtroOrdem.val() !== "nome"));
  }

  // "debaxo da unidade X" inclui as que penduram em qualquer descendente dela
  function sobUnidade(id_unidade, alvo) {
    var atual = id_unidade, guarda = 0;
    while (atual && guarda++ < 20) {
      if (atual === alvo) return true;
      var u = unidadePorId(atual);
      if (!u) return false;
      atual = u.id_unidade_pai;
    }
    return false;
  }

  // centenas de linhas de uma vez travam a rolagem — paginação leve no cliente
  var POR_PAGINA = 50;
  var pagina = 1;

  function renderPaginacao(totalFiltrado, paginas) {
    // .delegacias-filtros define display:flex, que vence o atributo [hidden] — esconder é pela
    // classe .hidden (display:none !important), como no resto da tela
    var $pag = $("#delegacias-paginacao");
    if (totalFiltrado <= POR_PAGINA) {
      $pag.addClass("hidden");
      return;
    }
    $pag.removeClass("hidden");
    var inicio = (pagina - 1) * POR_PAGINA + 1;
    var fim = Math.min(pagina * POR_PAGINA, totalFiltrado);
    $("#pag-info").text(inicio + "–" + fim + " de " + totalFiltrado);
    $("#pag-anterior").prop("disabled", pagina <= 1);
    $("#pag-proxima").prop("disabled", pagina >= paginas);
  }

  function aplicarFiltros() {
    var termo = $.trim($busca.val()).toUpperCase();
    var uf = $filtroUf.val();
    var unidadeAlvo = $filtroUnidade.val() ? Number($filtroUnidade.val()) : 0;
    var ia = $filtroIa.val();
    var no = $filtroNo.val();
    var semUnidade = $filtroSemUnidade.val();
    var ordem = $filtroOrdem.val() || "nome";

    var filtrada = listaCompleta.filter(function (d) {
      if (termo && String(d.no_delegacia || "").toUpperCase().indexOf(termo) === -1) return false;
      if (uf && String(d.uf || "").toUpperCase() !== uf) return false;
      if (unidadeAlvo && !sobUnidade(d.id_unidade, unidadeAlvo)) return false;
      if (semUnidade === "sem" && d.id_unidade) return false;
      if (semUnidade === "com" && !d.id_unidade) return false;
      if (ia === "ativa" && !d.in_ia_ativa) return false;
      if (ia === "inativa" && d.in_ia_ativa) return false;
      if (no === "usa" && d.in_no_ativa === false) return false;
      if (no === "nao-usa" && d.in_no_ativa !== false) return false;
      return true;
    });

    function rotuloOrdemUnidade(d) {
      var u = d.id_unidade ? unidadePorId(d.id_unidade) : null;
      return (u ? (u.sg_unidade || u.no_unidade) : "\uffff");  // sem unidade por último
    }
    filtrada.sort(function (a, b) {
      if (ordem === "nome-desc") {
        return String(b.no_delegacia || "").localeCompare(String(a.no_delegacia || ""), "pt-BR");
      }
      if (ordem === "recentes" || ordem === "antigas") {
        var da = a.dt_criacao ? new Date(a.dt_criacao).getTime() : 0;
        var db = b.dt_criacao ? new Date(b.dt_criacao).getTime() : 0;
        return ordem === "recentes" ? db - da : da - db;
      }
      if (ordem === "unidade") {
        var porUn = rotuloOrdemUnidade(a).localeCompare(rotuloOrdemUnidade(b), "pt-BR");
        if (porUn) return porUn;
      }
      return String(a.no_delegacia || "").localeCompare(String(b.no_delegacia || ""), "pt-BR");
    });

    var totalFiltrado = filtrada.length;
    var paginas = Math.max(1, Math.ceil(totalFiltrado / POR_PAGINA));
    if (pagina > paginas) pagina = paginas;
    if (pagina < 1) pagina = 1;
    var visiveis = filtrada.slice((pagina - 1) * POR_PAGINA, pagina * POR_PAGINA);

    $corpo.empty();
    if (!totalFiltrado) {
      $vazio.prop("hidden", false);
      $vazio.text(listaCompleta.length && temFiltroAtivo()
        ? "Nenhuma delegacia com esses filtros."
        : "Nenhuma delegacia cadastrada.");
    } else {
      $vazio.prop("hidden", true);
    }
    visiveis.forEach(function (d) { $corpo.append(renderLinha(d)); });

    var total = listaCompleta.length;
    $contador.text(total === 1 ? "1 delegacia" : total + " delegacias");
    $filtroInfo.text(temFiltroAtivo()
      ? "Mostrando " + totalFiltrado + " de " + total
      : "");
    $filtroLimpar.toggleClass("hidden", !temFiltroAtivo());
    renderPaginacao(totalFiltrado, paginas);
  }

  $("#pag-anterior").on("click", function () {
    if (pagina > 1) { pagina--; aplicarFiltros(); $corpo.closest(".delegacias-tabela-wrap")[0].scrollIntoView(); }
  });
  $("#pag-proxima").on("click", function () {
    pagina++; aplicarFiltros(); $corpo.closest(".delegacias-tabela-wrap")[0].scrollIntoView();
  });

  function badgeStatus(ativo, textoAtivo, textoInativo) {
    return $("<span>")
      .addClass("badge " + (ativo ? "badge-ok" : "badge-neutro"))
      .text(ativo ? textoAtivo : textoInativo);
  }

  function alternarFlag(id, campo, valor, $toggle, $badge, textoAtivo, textoInativo) {
    var anterior = estadoPorId[id] ? estadoPorId[id][campo] : !valor;
    $toggle.prop("disabled", true);
    var payload = { no_delegacia: estadoPorId[id].no_delegacia };
    payload[campo] = valor;
    $.ajax({
      url: "/api/delegacias/" + id,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify(payload)
    })
      .done(function () {
        estadoPorId[id][campo] = valor;
        var d = listaCompleta.find(function (x) { return x.id_delegacia === id; });
        if (d) d[campo] = valor;
        $badge.replaceWith(badgeStatus(valor, textoAtivo, textoInativo).attr("data-badge", campo));
        // Mensagem específica por campo, sem tecnicês:
        Toast.sucesso(campo === "in_ia_ativa"
          ? (valor ? "IA ativada para " + estadoPorId[id].no_delegacia + "." : "IA desativada para " + estadoPorId[id].no_delegacia + ".")
          : (valor ? "Extração pelo nó ativada." : "Extração pelo central ativada."));
      })
      .fail(function (xhr) {
        $toggle.prop("checked", anterior);
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar. Tente de novo.";
        Toast.erro(msg);
      })
      .always(function () {
        $toggle.prop("disabled", false);
      });
  }

  // Remove o toast duplicado/genérico do .done acima: o fail/success reais estão aqui.
  function renderLinha(d) {
    var id = d.id_delegacia;
    estadoPorId[id] = {
      no_delegacia: d.no_delegacia,
      in_ia_ativa: !!d.in_ia_ativa,
      in_no_ativa: d.in_no_ativa !== false,
      id_unidade: d.id_unidade || null
    };

    var $tr = $("<tr>");
    $("<td>").append($("<span>").addClass("arq-tag").text(d.no_delegacia || "—")).appendTo($tr);
    $("<td>").append(
      d.uf
        ? $("<span>").addClass("badge badge-neutro delegacia-uf").text(String(d.uf).toUpperCase())
        : document.createTextNode("—")
    ).appendTo($tr);
    var $tdUnidade = $("<td>").appendTo($tr);
    if (d.id_unidade && unidadePorId(d.id_unidade)) {
      // com centenas de delegacias a coluna precisa ser estreita: sigla na tela, nome no title
      var u = unidadePorId(d.id_unidade);
      $("<span>").addClass("badge badge-neutro").text(u.sg_unidade || u.no_unidade)
        .attr("title", nomeUnidade(d.id_unidade)).appendTo($tdUnidade);
    } else if (d.id_unidade) {
      $tdUnidade.text("#" + d.id_unidade);  // unidade sumiu da lista (a API não deveria deixar)
    } else {
      $tdUnidade.text("—");
    }

    var iaAtiva = !!d.in_ia_ativa;
    var $tdIa = $("<td>").addClass("delegacia-flag").appendTo($tr);
    var $badgeIa = badgeStatus(iaAtiva, "Ativa", "Inativa").attr("data-badge", "in_ia_ativa");
    var $toggleIa = $("<input>").attr("type", "checkbox").prop("checked", iaAtiva)
      .attr("aria-label", "IA ativa para " + rotuloDelegacia(d));
    var $switchIa = $("<label>").addClass("delegacia-switch")
      .attr("title", iaAtiva ? "IA liberada para esta delegacia" : "IA bloqueada para esta delegacia")
      .append($toggleIa, $("<span>").addClass("delegacia-switch-trilho").attr("aria-hidden", "true"), $badgeIa);
    $toggleIa.on("change", function () {
      alternarFlag(id, "in_ia_ativa", this.checked, $toggleIa,
        $tr.find('[data-badge="in_ia_ativa"]'), "Ativa", "Inativa");
    });
    $tdIa.append($switchIa);

    // "Usa o Nó": desligado, o central finge que o nó não existe e a delegacia volta a extrair
    // pela pasta local — sem descadastrar o nó em Administração → Servidores.
    var noAtivo = d.in_no_ativa !== false;
    var $tdNo = $("<td>").addClass("delegacia-flag").appendTo($tr);
    var $badgeNo = badgeStatus(noAtivo, "Pelo nó", "Central").attr("data-badge", "in_no_ativa");
    var $toggleNo = $("<input>").attr("type", "checkbox").prop("checked", noAtivo)
      .attr("aria-label", "Extração usa o nó da delegacia " + rotuloDelegacia(d));
    var $switchNo = $("<label>").addClass("delegacia-switch")
      .attr("title", "Desligue para importar e extrair pela pasta do principal, mesmo com o nó cadastrado")
      .append($toggleNo, $("<span>").addClass("delegacia-switch-trilho").attr("aria-hidden", "true"), $badgeNo);
    $toggleNo.on("change", function () {
      alternarFlag(id, "in_no_ativa", this.checked, $toggleNo,
        $tr.find('[data-badge="in_no_ativa"]'), "Pelo nó", "Central");
    });
    $tdNo.append($switchNo);

    var $tdData = $("<td>").text(formatarData(d.dt_criacao)).appendTo($tr);
    if (d.dt_criacao) $tdData.attr("title", String(d.dt_criacao));

    var $acoes = $("<td>").addClass("delegacia-acoes").appendTo($tr);
    $("<button>").attr("type", "button").addClass("btn btn-secondary btn-sm")
      .text("Editar")
      .attr("aria-label", "Editar " + rotuloDelegacia(d))
      .on("click", function () { abrirEditar(d); })
      .appendTo($acoes);
    $("<button>").attr("type", "button").addClass("btn btn-secondary btn-sm")
      .text("IA")
      .attr("aria-label", "IA própria da delegacia " + rotuloDelegacia(d))
      .on("click", function () { abrirIaEntidade("delegacia", d.id_delegacia, rotuloDelegacia(d)); })
      .appendTo($acoes);
    $("<button>").attr("type", "button").addClass("btn btn-danger btn-sm")
      .text("Excluir")
      .attr("aria-label", "Excluir " + rotuloDelegacia(d))
      .on("click", function () { excluirDelegacia(d); })
      .appendTo($acoes);
    return $tr;
  }

  function mostrarCarregando() {
    $corpo.empty();
    for (var i = 0; i < 4; i++) {
      var $tr = $("<tr>").addClass("delegacia-skeleton").attr("aria-hidden", "true");
      for (var c = 0; c < 7; c++) {
        $tr.append($("<td>").append($("<span>").addClass("skeleton-bar")));
      }
      $corpo.append($tr);
    }
    $vazio.prop("hidden", true);
    $erro.prop("hidden", true);
  }

  function carregar() {
    if (carregando) return;
    carregando = true;
    mostrarCarregando();
    $.getJSON("/api/delegacias")
      .done(function (lista) {
        listaCompleta = lista || [];
        estadoPorId = {};
        $erro.prop("hidden", true);
        aplicarFiltros();
      })
      .fail(function () {
        $corpo.empty();
        $vazio.prop("hidden", true);
        $erro.prop("hidden", false);
        $contador.text("");
        $filtroInfo.text("");
      })
      .always(function () {
        carregando = false;
      });
  }

  function excluirDelegacia(d) {
    Dialogo.confirmar({
      titulo: "Excluir delegacia",
      mensagem: "Excluir \"" + rotuloDelegacia(d) + "\"?\nOs usuários dela ficam sem delegacia (não são apagados).",
      rotuloOk: "Excluir",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/delegacias/" + d.id_delegacia,
        method: "DELETE"
      })
        .done(function () {
          Toast.sucesso("Delegacia \"" + d.no_delegacia + "\" excluída.");
          carregar();
        })
        .fail(function (xhr) {
          Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível excluir.");
        });
    });
  }

  function abrirModal($modal) {
    ultimoFocoModal = document.activeElement;
    $modal.removeClass("hidden");
  }

  function fecharModal($modal) {
    $modal.addClass("hidden");
    if (ultimoFocoModal && ultimoFocoModal.focus) {
      try { ultimoFocoModal.focus(); } catch (e) { /* sem foco válido */ }
    }
  }

  function abrirEditar(d) {
    editandoId = d.id_delegacia;
    $inputEditarNome.val(d.no_delegacia);
    $inputEditarUf.val(d.uf ? String(d.uf).toUpperCase() : "");
    popularSelectUnidade($inputEditarUnidade, d.id_unidade || "");
    $inputEditarIaAtiva.prop("checked", !!d.in_ia_ativa);
    $inputEditarNoAtiva.prop("checked", d.in_no_ativa !== false);
    aplicarUfHerdada($inputEditarUnidade, $inputEditarUf, $("#editar-delegacia-uf-help"));
    $statusEditar.text("").removeClass("success error");
    abrirModal($modalEditar);
    $inputEditarNome.focus();
  }

  $("#nova-delegacia-unidade").on("change", function () {
    aplicarUfHerdada($(this), $("#nova-delegacia-uf"), $("#nova-delegacia-uf-help"));
  });
  $("#editar-delegacia-unidade").on("change", function () {
    aplicarUfHerdada($(this), $inputEditarUf, $("#editar-delegacia-uf-help"));
  });

  $("#abrir-nova-delegacia-btn").on("click", function () {
    $formNovo[0].reset();
    popularSelectUnidade($("#nova-delegacia-unidade"), "");
    aplicarUfHerdada($("#nova-delegacia-unidade"), $("#nova-delegacia-uf"), $("#nova-delegacia-uf-help"));
    $("#nova-delegacia-ia-ativa").prop("checked", true);
    $("#nova-delegacia-no-ativa").prop("checked", true);
    $statusNovo.text("").removeClass("success error");
    abrirModal($modalNovo);
    $("#nova-delegacia-nome").focus();
  });

  $("#nova-delegacia-fechar").on("click", function () {
    fecharModal($modalNovo);
  });
  $modalNovo.on("click", function (e) { if (e.target === this) fecharModal($modalNovo); });

  $("#editar-delegacia-fechar").on("click", function () {
    fecharModal($modalEditar);
  });
  $modalEditar.on("click", function (e) { if (e.target === this) fecharModal($modalEditar); });

  $(document).on("keydown", function (e) {
    if (e.key === "Escape") {
      if (!$modalNovo.hasClass("hidden")) fecharModal($modalNovo);
      else if (!$modalEditar.hasClass("hidden")) fecharModal($modalEditar);
      else if (!$("#modal-nova-unidade").hasClass("hidden")) fecharModal($("#modal-nova-unidade"));
      else if (!$("#modal-editar-unidade").hasClass("hidden")) fecharModal($("#modal-editar-unidade"));
      else if (!$("#modal-ia-entidade").hasClass("hidden")) fecharModal($("#modal-ia-entidade"));
    }
  });

  $("#delegacias-recarregar").on("click", carregar);

  var buscaTimer = null;
  $busca.on("input", function () {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(function () { pagina = 1; aplicarFiltros(); }, 150);
  });
  function filtroMudou() { pagina = 1; aplicarFiltros(); }
  $filtroUf.on("change", filtroMudou);
  $filtroUnidade.on("change", filtroMudou);
  $filtroIa.on("change", filtroMudou);
  $filtroNo.on("change", filtroMudou);
  $filtroSemUnidade.on("change", filtroMudou);
  $filtroOrdem.on("change", filtroMudou);
  $filtroLimpar.on("click", function () {
    $busca.val("");
    $filtroUf.val("");
    $filtroUnidade.val("");
    $filtroIa.val("");
    $filtroNo.val("");
    $filtroSemUnidade.val("");
    $filtroOrdem.val("nome");
    $("#ufiltro-busca, #ufiltro-uf, #ufiltro-pai, #ufiltro-nivel").val("");
    renderUnidades();
    filtroMudou();
    $busca.focus();
  });

  function nomeValido($input, $status) {
    var nome = $.trim($input.val());
    if (nome.length < 2) {
      $status.text("Dê um nome com ao menos 2 letras.").addClass("error");
      $input.focus();
      return null;
    }
    return nome.toUpperCase();
  }

  // Regra de negócio: nenhuma delegacia fica sem unidade superior. Escolhendo a unidade
  // resolve; sem escolha, ao menos a UF basta — a API cria/amarra a padrão SR/PF/<UF>
  // (ex.: SR/PF/ES para a UF ES). Sem os dois, o formulário nem envia.
  var SEM_UNIDADE_MSG = "Escolha a unidade superior (padrão SR/PF/UF, ex.: SR/PF/ES) ou " +
                        "informe a UF — nenhuma delegacia pode ficar sem unidade superior.";

  function unidadeConferida($unidade, $uf, $status) {
    if ($unidade.val() || $.trim($uf.val())) { $status.text(""); return true; }
    $status.text(SEM_UNIDADE_MSG).addClass("error");
    return false;
  }

  $formNovo.on("submit", function (e) {
    e.preventDefault();
    $statusNovo.text("").removeClass("success error");
    var nome = nomeValido($("#nova-delegacia-nome"), $statusNovo);
    if (!nome) return;
    if (!unidadeConferida($("#nova-delegacia-unidade"), $("#nova-delegacia-uf"), $statusNovo)) return;
    $btnNovo.prop("disabled", true);
    $.ajax({
      url: "/api/delegacias",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        no_delegacia: nome,
        uf: $.trim($("#nova-delegacia-uf").val()),
        id_unidade: $("#nova-delegacia-unidade").val() ? Number($("#nova-delegacia-unidade").val()) : null,
        in_ia_ativa: $("#nova-delegacia-ia-ativa").is(":checked"),
        in_no_ativa: $("#nova-delegacia-no-ativa").is(":checked")
      })
    })
      .done(function () {
        Toast.sucesso("Delegacia \"" + nome + "\" criada.");
        carregar();
        fecharModal($modalNovo);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar delegacia.";
        $statusNovo.text(msg).addClass("error");
      })
      .always(function () {
        $btnNovo.prop("disabled", false);
      });
  });

  $formEditar.on("submit", function (e) {
    e.preventDefault();
    $statusEditar.text("").removeClass("success error");
    var nome = nomeValido($inputEditarNome, $statusEditar);
    if (!nome || !editandoId) return;
    // legado sem unidade superior: o PUT não pode salvar sem resolver a unidade (a API
    // amarra a padrão SR/PF/UF — precisa ao menos da UF para isso)
    if (!(estadoPorId[editandoId] && estadoPorId[editandoId].id_unidade) &&
        !unidadeConferida($inputEditarUnidade, $inputEditarUf, $statusEditar)) return;
    $btnEditar.prop("disabled", true);
    $.ajax({
      url: "/api/delegacias/" + editandoId,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify((function () {
        var dado = {
          no_delegacia: nome,
          uf: $.trim($inputEditarUf.val()),
          in_ia_ativa: $inputEditarIaAtiva.is(":checked"),
          in_no_ativa: $inputEditarNoAtiva.is(":checked")
        };
        // a unidade não pode ser REMOVIDA (regra: nenhuma delegacia fica sem unidade
        // superior) — vem a escolha quando o administrador trocou; senão a API mantém a
        // atual e, se não havia nenhuma, amarra a padrão SR/PF/<UF> do UF informado
        var unidade = $inputEditarUnidade.val();
        if (unidade) dado.id_unidade = Number(unidade);
        return dado;
      })())
    })
      .done(function () {
        Toast.sucesso("Delegacia \"" + nome + "\" atualizada.");
        carregar();
        fecharModal($modalEditar);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao editar delegacia.";
        $statusEditar.text(msg).addClass("error");
      })
      .always(function () {
        $btnEditar.prop("disabled", false);
      });
  });

  // =====================================================================
  // IA própria de unidade/delegacia — .env → unidade → delegacia → usuário
  // (a linha vencedora vale inteira; ver funcoes/config_ia_usuario.py)
  // =====================================================================
  var $modalIaEnt = $("#modal-ia-entidade");
  var iaEntAlvo = null;      // {nivel, id, nome} da linha aberta no modal
  var iaEntCertTexto = "";   // PEM lido do arquivo, enviado no salvar
  var iaEntRemCert = false;

  IAPresets.montarSelectProvedores($("#ia-ent-provider"), IAPresets.CHAT_PROVIDERS);

  function montarCamposIaEntidade(modeloAtual, urlAtual) {
    var preset = IAPresets.CHAT_PRESETS[$("#ia-ent-provider").val()] || IAPresets.CHAT_PRESETS.outro;
    IAPresets.popularSelect($("#ia-ent-model"), $("#ia-ent-model-custom"), preset.models || [], modeloAtual || "");
    IAPresets.popularSelect($("#ia-ent-api-url"), $("#ia-ent-api-url-custom"), preset.urls || [], urlAtual || "");
    var comCertificado = $("#ia-ent-provider").val() === "openai-cert";
    $("#ia-ent-reasoning-wrap").toggleClass("hidden", !comCertificado);
    $("#ia-ent-ca-cert-wrap").toggleClass("hidden", !comCertificado);
  }
  $("#ia-ent-provider").on("change", function () { montarCamposIaEntidade(); });

  $("#ia-ent-ca-cert-file").on("change", function () {
    var arquivo = this.files && this.files[0];
    if (!arquivo) return;
    var leitor = new FileReader();
    leitor.onload = function () {
      iaEntCertTexto = String(leitor.result || "");
      iaEntRemCert = false;
      $("#ia-ent-ca-cert-remover").addClass("hidden");
      $("#ia-ent-ca-cert-help").text("Certificado escolhido: " + arquivo.name + " — vai junto ao salvar.");
    };
    leitor.onerror = function () { Toast.erro("Não foi possível ler o arquivo."); };
    leitor.readAsText(arquivo);
  });

  $("#ia-ent-ca-cert-remover").on("click", function () {
    iaEntRemCert = true;
    iaEntCertTexto = "";
    $(this).addClass("hidden");
    $("#ia-ent-ca-cert-help").text("O certificado gravado será apagado ao salvar.");
  });

  function abrirIaEntidade(nivel, id, nome) {
    iaEntAlvo = { nivel: nivel, id: id, nome: nome };
    iaEntCertTexto = "";
    iaEntRemCert = false;
    $("#ia-ent-ca-cert-file").val("");
    $("#ia-ent-status").text("").removeClass("success error");
    $("#ia-ent-titulo").text("IA própria — " + nome);
    $.getJSON("/api/config/ia-entidade/" + nivel + "/" + id)
      .done(function (d) {
        $("#ia-ent-ativa").prop("checked", !!d.ativa);
        $("#ia-ent-provider").val(d.provider || "openai");
        if (!$("#ia-ent-provider").val()) $("#ia-ent-provider").val("openai");
        montarCamposIaEntidade(d.model || "", d.apiUrl || "");
        $("#ia-ent-key").val("").attr("placeholder", d.apiKeyMasked || "");
        $("#ia-ent-key-help").text(d.apiKeyMasked
          ? "Chave já gravada (" + d.apiKeyMasked + "). Deixe em branco para mantê-la."
          : "Ollama e LM Studio locais não pedem chave.");
        $("#ia-ent-reasoning").val(d.reasoningEffort || "low");
        $("#ia-ent-contexto").val(d.contextLength ? d.contextLength : "");
        $("#ia-ent-remover").toggleClass("hidden", !d.definida);
        $("#ia-ent-ca-cert-remover").toggleClass("hidden", !d.caCertResumo);
        if (d.caCertResumo) {
          $("#ia-ent-ca-cert-help").text("Gravado: " + d.caCertResumo + ". Escolha um arquivo para substituir.");
        } else {
          $("#ia-ent-ca-cert-help").text("Necessário no provedor \"OpenAI — com certificado\".");
        }
        abrirModal($modalIaEnt);
      })
      .fail(function () {
        Toast.erro("Não foi possível carregar a IA desta entidade.");
      });
  }

  $("#ia-ent-fechar").on("click", function () { fecharModal($modalIaEnt); });
  $modalIaEnt.on("click", function (e) { if (e.target === this) fecharModal($modalIaEnt); });

  $("#ia-ent-salvar").on("click", function () {
    if (!iaEntAlvo) return;
    var $botao = $(this).prop("disabled", true);
    $("#ia-ent-status").text("").removeClass("success error");
    $.ajax({
      url: "/api/config/ia-entidade",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        tp_nivel: iaEntAlvo.nivel,
        id_entidade: iaEntAlvo.id,
        ativa: $("#ia-ent-ativa").is(":checked"),
        provider: $("#ia-ent-provider").val(),
        model: IAPresets.valorAtual($("#ia-ent-model"), $("#ia-ent-model-custom")),
        apiUrl: IAPresets.valorAtual($("#ia-ent-api-url"), $("#ia-ent-api-url-custom")),
        apiKey: $("#ia-ent-key").val(),
        contextLength: parseInt($("#ia-ent-contexto").val(), 10) || 0,
        reasoningEffort: $("#ia-ent-reasoning").val(),
        caCert: iaEntCertTexto,
        removerCaCert: iaEntRemCert
      })
    })
      .done(function () {
        Toast.sucesso("IA de \"" + iaEntAlvo.nome + "\" salva.");
        fecharModal($modalIaEnt);
      })
      .fail(function (xhr) {
        $("#ia-ent-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.")
          .addClass("error");
      })
      .always(function () { $botao.prop("disabled", false); });
  });

  $("#ia-ent-remover").on("click", function () {
    if (!iaEntAlvo) return;
    var alvo = iaEntAlvo;
    Dialogo.confirmar({
      titulo: "Remover IA própria",
      mensagem: "\"" + alvo.nome + "\" volta a usar a IA do nível de cima (unidade superior ou o padrão do sistema).",
      rotuloOk: "Remover",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/config/ia-entidade/" + alvo.nivel + "/" + alvo.id, method: "DELETE" })
        .done(function () {
          Toast.sucesso("IA própria de \"" + alvo.nome + "\" removida.");
          fecharModal($modalIaEnt);
        })
        .fail(function (xhr) {
          $("#ia-ent-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao remover.")
            .addClass("error");
        });
    });
  });

  // unidades primeiro: as linhas de delegacia mostram o nome da unidade, e dependem da lista
  carregarUnidades().always(carregar);
})();
