(function () {
  "use strict";

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var $adminCard = $("#admin-card");
  var $semPermissao = $("#sem-permissao-hint");
  var $corpo = $("#usuarios-corpo");
  var $vazio = $("#usuarios-vazio");
  var $erro = $("#usuarios-erro");
  var $contador = $("#usuarios-contador");
  var $filtroInfo = $("#filtro-info");

  var $busca = $("#filtro-busca");
  var $filtroPerfil = $("#filtro-perfil");
  var $filtroStatus = $("#filtro-status");
  var $filtroAprovacao = $("#filtro-aprovacao");
  var $filtroDelegacia = $("#filtro-delegacia");
  var $filtroIa = $("#filtro-ia");
  var $filtroOrdem = $("#filtro-ordem");
  var $filtroLimpar = $("#filtro-limpar");

  var $paginacao = $("#usuarios-paginacao");
  var $pagInfo = $("#usuarios-pag-info");

  var $modalNovo = $("#modal-novo-usuario");
  var $novoForm = $("#novo-usuario-form");
  var $novoStatus = $("#novo-usuario-status");
  var $novoBtn = $("#novo-usuario-btn");

  var listaCompleta = [];
  var listaFiltrada = [];
  var listaDelegacias = [];
  var idsIaPropria = [];
  var detalhesIa = {};
  var pagina = 1;
  var POR_PAGINA = 20;
  var carregando = false;
  var ultimoFocoModal = null;
  var eu = null;

  var PERFIL_ROTULO = {
    analista: "Analista",
    inteligencia: "Chefia de inteligência",
    agente_inteligencia: "Inteligência",
    auditoria: "Auditoria",
    administrador: "Administrador"
  };

  function rotuloPerfil(perfil) {
    return PERFIL_ROTULO[perfil] || "—";
  }

  function tempoRelativo(iso) {
    if (!iso) return "";
    var dt = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    if (isNaN(dt.getTime())) return "";
    var diffMs = Date.now() - dt.getTime();
    if (diffMs < 0) return "";
    var min = Math.floor(diffMs / 60000);
    if (min < 1) return "agora mesmo";
    if (min < 60) return "há " + min + " min";
    var h = Math.floor(min / 60);
    if (h < 24) return "há " + h + " h";
    var d = Math.floor(h / 24);
    if (d < 30) return d === 1 ? "há 1 dia" : "há " + d + " dias";
    var m = Math.floor(d / 30);
    if (m < 12) return m === 1 ? "há 1 mês" : "há " + m + " meses";
    var a = Math.floor(m / 12);
    return a === 1 ? "há 1 ano" : "há " + a + " anos";
  }

  // ---------- delegacias ----------

  function carregarDelegacias() {
    $.getJSON("/api/delegacias")
      .done(function (lista) {
        listaDelegacias = lista || [];
        popularSelectDelegacia($("#novo-delegacia-select"), null);
        popularSelectDelegacia($("#editar-delegacia-select"), null);
        popularFiltroDelegacia();
      })
      .fail(function () {
        listaDelegacias = [];
      });
  }

  function popularSelectDelegacia($select, selectedId) {
    var atual = $select.val();
    $select.empty().append($("<option>").val("").text("Selecione"));
    listaDelegacias.forEach(function (d) {
      var $opt = $("<option>").val(d.id_delegacia).text(d.no_delegacia);
      if (d.id_delegacia === selectedId) $opt.prop("selected", true);
      $select.append($opt);
    });
    if (selectedId === undefined || selectedId === null) $select.val(atual);
  }

  function popularFiltroDelegacia() {
    var atual = $filtroDelegacia.val();
    $filtroDelegacia.empty()
      .append($("<option>").val("").text("Todas as delegacias"))
      .append($("<option>").val("__sem__").text("Sem delegacia"));
    listaDelegacias.forEach(function (d) {
      $filtroDelegacia.append($("<option>").val(String(d.id_delegacia)).text(d.no_delegacia));
    });
    if (atual) $filtroDelegacia.val(atual);
  }

  function nomeDelegacia(id) {
    if (!id) return "—";
    var d = listaDelegacias.find(function (x) { return x.id_delegacia === id; });
    return d ? d.no_delegacia : "—";
  }

  // ---------- sessão atual (autoproteção) ----------

  function descobrirEu() {
    $(document).on("analyser:usuario", function (e, usuario) {
      eu = usuario || null;
      aplicarFiltros();
    });
    $.getJSON("/api/auth/eu")
      .done(function (usuario) {
        eu = usuario || null;
        aplicarFiltros();
      })
      .fail(function () { eu = null; });
  }

  function souEu(idUsuario) {
    return !!(eu && eu.id_usuario === idUsuario);
  }

  // ---------- filtros / ordenação / paginação ----------

  function temFiltroAtivo() {
    return !!($.trim($busca.val()) || $filtroPerfil.val() || $filtroStatus.val() ||
      $filtroAprovacao.val() || $filtroDelegacia.val() || $filtroIa.val() ||
      ($filtroOrdem.val() && $filtroOrdem.val() !== "nome"));
  }

  function aplicarFiltros() {
    var termo = $.trim($busca.val()).toLowerCase();
    var perfil = $filtroPerfil.val();
    var status = $filtroStatus.val();
    var aprov = $filtroAprovacao.val();
    var deleg = $filtroDelegacia.val();
    var ia = $filtroIa.val();
    var ordem = $filtroOrdem.val() || "nome";

    listaFiltrada = listaCompleta.filter(function (u) {
      if (termo) {
        var alvo = ((u.no_login || "") + " " + (u.no_usuario || "") + " " + (u.no_email || "")).toLowerCase();
        if (alvo.indexOf(termo) === -1) return false;
      }
      if (perfil && (u.tp_perfil || "analista") !== perfil) return false;
      if (status === "ativo" && !u.in_ativo) return false;
      if (status === "desativado" && u.in_ativo) return false;
      if (aprov === "aprovado" && !u.in_aprovado) return false;
      if (aprov === "pendente" && u.in_aprovado) return false;
      if (deleg === "__sem__" && u.id_delegacia) return false;
      if (deleg && deleg !== "__sem__" && String(u.id_delegacia) !== deleg) return false;
      if (ia === "desativada" && u.in_ia_ativa !== false) return false;
      if (ia === "propria" && !temIaPropria(u.id_usuario)) return false;
      if (ia === "padrao" && temIaPropria(u.id_usuario)) return false;
      return true;
    });

    listaFiltrada.sort(function (a, b) {
      if (ordem === "nome-desc") {
        return String(b.no_usuario || "").localeCompare(String(a.no_usuario || ""), "pt-BR");
      }
      if (ordem === "login") {
        return String(a.no_login || "").localeCompare(String(b.no_login || ""), "pt-BR");
      }
      if (ordem === "recentes" || ordem === "antigos") {
        var da = a.dt_ultimo_login ? new Date(a.dt_ultimo_login).getTime() : 0;
        var db = b.dt_ultimo_login ? new Date(b.dt_ultimo_login).getTime() : 0;
        return ordem === "recentes" ? db - da : da - db;
      }
      if (ordem === "cadastro-recente") {
        var ca = a.dt_cadastro ? new Date(a.dt_cadastro).getTime() : 0;
        var cb = b.dt_cadastro ? new Date(b.dt_cadastro).getTime() : 0;
        return cb - ca;
      }
      return String(a.no_usuario || "").localeCompare(String(b.no_usuario || ""), "pt-BR");
    });

    var totalPaginas = Math.max(1, Math.ceil(listaFiltrada.length / POR_PAGINA));
    if (pagina > totalPaginas) pagina = totalPaginas;
    if (pagina < 1) pagina = 1;

    renderTabela();
    renderKpis();
    atualizarContadores(totalPaginas);
  }

  function atualizarContadores(totalPaginas) {
    var total = listaCompleta.length;
    $contador.text(total === 1 ? "1 usuário" : total + " usuários");
    $filtroInfo.text(temFiltroAtivo()
      ? "Mostrando " + listaFiltrada.length + " de " + total
      : "");
    $filtroLimpar.toggleClass("hidden", !temFiltroAtivo());
    if (listaFiltrada.length > POR_PAGINA) {
      $paginacao.show();
      $pagInfo.text("Página " + pagina + " de " + totalPaginas);
      $("#usuarios-pag-anterior").prop("disabled", pagina <= 1);
      $("#usuarios-pag-proximo").prop("disabled", pagina >= totalPaginas);
    } else {
      $paginacao.hide();
    }
    var pendentes = listaFiltrada.filter(function (u) { return !u.in_aprovado; }).length;
    $("#aprovar-pendentes-btn").prop("disabled", !pendentes);
    $("#aprovar-pendentes-btn").text(pendentes ? "Aprovar pendentes (" + pendentes + ")" : "Aprovar pendentes");
  }

  function renderKpis() {
    $("#kpi-total").text(listaCompleta.length);
    $("#kpi-ativos").text(listaCompleta.filter(function (u) { return u.in_ativo; }).length);
    $("#kpi-pendentes").text(listaCompleta.filter(function (u) { return !u.in_aprovado; }).length);
    $("#kpi-desativados").text(listaCompleta.filter(function (u) { return !u.in_ativo; }).length);
    $("#kpi-ia").text(idsIaPropria.length);
  }

  function renderTabela() {
    $corpo.empty();
    if (!listaFiltrada.length) {
      $vazio.prop("hidden", false);
      $vazio.text(listaCompleta.length && temFiltroAtivo()
        ? "Nenhum usuário com esses filtros."
        : "Nenhum usuário cadastrado.");
    } else {
      $vazio.prop("hidden", true);
    }
    var inicio = (pagina - 1) * POR_PAGINA;
    listaFiltrada.slice(inicio, inicio + POR_PAGINA).forEach(function (u) {
      $corpo.append(renderLinhaUsuario(u));
    });
  }

  function mostrarCarregando() {
    $corpo.empty();
    for (var i = 0; i < 6; i++) {
      var $tr = $("<tr>").addClass("delegacia-skeleton").attr("aria-hidden", "true");
      for (var c = 0; c < 10; c++) {
        $tr.append($("<td>").append($("<span>").addClass("skeleton-bar")));
      }
      $corpo.append($tr);
    }
    $vazio.prop("hidden", true);
    $erro.prop("hidden", true);
  }

  // ---------- linha ----------

  function temIaPropria(idUsuario) {
    return idsIaPropria.indexOf(idUsuario) !== -1;
  }

  function buscarDetalheIa(idUsuario) {
    if (detalhesIa[idUsuario] !== undefined) return;
    detalhesIa[idUsuario] = null;
    $.getJSON("/api/usuarios/" + idUsuario + "/config-ia")
      .done(function (d) {
        if (d && d.definida && d.ativa) {
          detalhesIa[idUsuario] = (d.provider || "?") + " · " + (d.model || "?");
        } else {
          detalhesIa[idUsuario] = false;
        }
        $corpo.find('tr[data-usuario="' + idUsuario + '"] .ia-celula').each(function () {
          if (detalhesIa[idUsuario]) $(this).attr("title", String(detalhesIa[idUsuario]));
        });
      })
      .fail(function () { detalhesIa[idUsuario] = false; });
  }

  function botaoSm(rotulo, classe, titulo) {
    return $("<button>").attr("type", "button").addClass("btn btn-sm " + (classe || "btn-secondary"))
      .text(rotulo).attr("title", titulo || rotulo);
  }

  function renderLinhaUsuario(u) {
    var $tr = $("<tr>").attr("data-usuario", u.id_usuario);
    if (souEu(u.id_usuario)) $tr.addClass("usuarios-eu");
    $("<td>").text(u.no_login).appendTo($tr);
    var $tdNome = $("<td>").appendTo($tr);
    $("<span>").text(u.no_usuario).appendTo($tdNome);
    if (souEu(u.id_usuario)) {
      $("<span>").addClass("badge badge-neutro").css("margin-left", "6px").text("você").appendTo($tdNome);
    }
    $("<td>").text(u.no_email || "—").appendTo($tr);
    $("<td>").text(nomeDelegacia(u.id_delegacia)).appendTo($tr);
    $("<td>").text(rotuloPerfil(u.tp_perfil)).appendTo($tr);
    var $tdIa = $("<td>").addClass("ia-celula").appendTo($tr);
    if (u.in_ia_ativa === false) {
      $tdIa.append($("<span>").addClass("badge badge-erro").text("Desativada"))
        .attr("title", "A IA está desligada para este usuário — ele não usa IA nenhuma (nem a própria, nem a das Configurações)");
    } else if (temIaPropria(u.id_usuario)) {
      $tdIa.append($("<span>").addClass("badge badge-ok").text("Própria"));
      buscarDetalheIa(u.id_usuario);
      if (detalhesIa[u.id_usuario]) $tdIa.attr("title", String(detalhesIa[u.id_usuario]));
      else $tdIa.attr("title", "Este usuário usa provedor/modelo/chave próprios");
    } else {
      $tdIa.text("Padrão").attr("title", "Usa a IA das Configurações");
    }
    var $tdLogin = $("<td>").appendTo($tr);
    if (u.dt_ultimo_login) {
      var rel = tempoRelativo(u.dt_ultimo_login);
      $tdLogin.text(formatarDataHora(u.dt_ultimo_login) + (rel ? " (" + rel + ")" : ""));
      $tdLogin.attr("title", String(u.dt_ultimo_login));
    } else {
      $tdLogin.text("Nunca acessou");
    }
    $("<td>").append(
      $("<span>").addClass("badge " + (u.in_ativo ? "badge-ok" : "badge-erro")).text(u.in_ativo ? "Ativo" : "Desativado")
    ).appendTo($tr);
    $("<td>").append(
      $("<span>").addClass("badge " + (u.in_aprovado ? "badge-ok" : "badge-rodando")).text(u.in_aprovado ? "Aprovado" : "Pendente")
    ).appendTo($tr);

    var $acoes = $("<td>").addClass("usuarios-acoes").appendTo($tr);

    var $btnAtivo = botaoSm(u.in_ativo ? "Desativar" : "Reativar", u.in_ativo ? "btn-secondary" : "btn-primary",
      (u.in_ativo ? "Desativar " : "Reativar ") + u.no_login);
    $btnAtivo.on("click", function () { confirmarAlterarAtivo(u); });
    $btnAtivo.appendTo($acoes);

    var $btnAprov = botaoSm(u.in_aprovado ? "Revogar" : "Aprovar", u.in_aprovado ? "btn-secondary" : "btn-primary",
      (u.in_aprovado ? "Revogar aprovação de " : "Aprovar ") + u.no_login);
    $btnAprov.attr("title", (u.in_aprovado ? "Revogar aprovação de " : "Aprovar ") + u.no_login);
    $btnAprov.on("click", function () { confirmarAlterarAprovado(u); });
    $btnAprov.appendTo($acoes);

    var $btnEditar = botaoSm("Editar", "btn-secondary", "Editar " + u.no_login);
    $btnEditar.on("click", function () { abrirModalEditar(u); });
    $btnEditar.appendTo($acoes);

    var $menu = $("<details>").addClass("usuarios-menu").appendTo($acoes);
    $("<summary>").addClass("btn btn-sm btn-secondary").text("Mais ▾")
      .attr("title", "Mais ações para " + u.no_login).appendTo($menu);
    var $lista = $("<div>").addClass("usuarios-menu-lista").appendTo($menu);
    $("<button>").attr("type", "button").addClass("btn btn-sm btn-secondary").text("Redefinir senha")
      .on("click", function () { $menu.removeAttr("open"); abrirModalRedefinicao(u); }).appendTo($lista);
    $("<button>").attr("type", "button").addClass("btn btn-sm btn-secondary").text("IA do usuário")
      .on("click", function () { $menu.removeAttr("open"); abrirModalConfigIa(u); }).appendTo($lista);
    $("<button>").attr("type", "button").addClass("btn btn-sm btn-secondary")
      .text(u.in_ia_ativa === false ? "Reativar IA" : "Desativar IA")
      .on("click", function () { $menu.removeAttr("open"); confirmarAlterarIa(u); }).appendTo($lista);
    $("<a>").addClass("btn btn-sm btn-secondary").text("Histórico")
      .attr("href", "auditoria.html").attr("title", "Ver trilha de auditoria da gestão de usuários")
      .on("click", function () { $menu.removeAttr("open"); }).appendTo($lista);
    return $tr;
  }

  // ---------- carregamento ----------

  function carregarUsuarios() {
    if (carregando) return;
    carregando = true;
    mostrarCarregando();
    $.getJSON("/api/usuarios/config-ia")
      .done(function (resp) { idsIaPropria = (resp && resp.ids) || []; })
      .fail(function () { idsIaPropria = []; })
      .always(listarUsuarios);
  }

  function listarUsuarios() {
    $.getJSON("/api/usuarios")
      .done(function (lista) {
        listaCompleta = lista || [];
        detalhesIa = {};
        $adminCard.removeClass("hidden");
        $semPermissao.addClass("hidden");
        $erro.prop("hidden", true);
        pagina = 1;
        aplicarFiltros();
      })
      .fail(function (xhr) {
        if (xhr.status === 401 || xhr.status === 403) {
          // não-admin: a seção fica escondida mesmo, não é erro pro usuário comum
          $adminCard.addClass("hidden");
          $semPermissao.removeClass("hidden");
          return;
        }
        $corpo.empty();
        $vazio.prop("hidden", true);
        $erro.prop("hidden", false);
        $contador.text("");
        $filtroInfo.text("");
        if (window.Toast) Toast.erro("Não foi possível carregar os usuários. Tente de novo.");
      })
      .always(function () {
        carregando = false;
      });
  }

  // ---------- ações com confirmação ----------

  function confirmarAlterarAtivo(u) {
    var ativar = !u.in_ativo;
    if (!ativar && souEu(u.id_usuario)) {
      Toast.erro("Você não pode desativar a sua própria conta.");
      return;
    }
    var acao = ativar ? "Reativar" : "Desativar";
    Dialogo.confirmar({
      titulo: acao + " usuário",
      mensagem: acao + ' "' + u.no_usuario + " (" + u.no_login + ')"?' +
        (ativar ? "" : "\nEle perde o acesso imediatamente (sessões param de validar)."),
      rotuloOk: acao,
      perigoso: !ativar
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/usuarios/" + u.id_usuario + "/ativo",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ ativo: ativar })
      })
        .done(function () {
          u.in_ativo = ativar;
          Toast.sucesso(ativar ? "Usuário reativado." : "Usuário desativado.");
          aplicarFiltros();
        })
        .fail(function (xhr) {
          Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar. Tente de novo.");
        });
    });
  }

  function confirmarAlterarIa(u) {
    var ligar = u.in_ia_ativa === false;
    Dialogo.confirmar({
      titulo: ligar ? "Reativar IA" : "Desativar IA",
      mensagem: ligar
        ? 'Religar a IA de "' + u.no_usuario + '"? Ele volta a usar a IA própria (se tiver) ou a das Configurações.'
        : 'Desativar a IA de "' + u.no_usuario + " (" + u.no_login + ')"? Ele perde TODO o acesso à IA — chat, busca automática, análise de documento e relatório. O resto do sistema segue normal, e a IA própria dele fica guardada para quando religar.',
      rotuloOk: ligar ? "Religar IA" : "Desativar IA",
      perigoso: !ligar
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/usuarios/" + u.id_usuario + "/ia",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ ativa: ligar })
      })
        .done(function () {
          u.in_ia_ativa = ligar;
          Toast.sucesso(ligar ? "IA do usuário religada." : "IA do usuário desativada.");
          aplicarFiltros();
        })
        .fail(function (xhr) {
          Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar. Tente de novo.");
        });
    });
  }

  function confirmarAlterarAprovado(u) {
    var aprovar = !u.in_aprovado;
    if (!aprovar && souEu(u.id_usuario)) {
      Toast.erro("Você não pode revogar a sua própria aprovação.");
      return;
    }
    Dialogo.confirmar({
      titulo: aprovar ? "Aprovar usuário" : "Revogar aprovação",
      mensagem: aprovar
        ? 'Aprovar "' + u.no_usuario + " (" + u.no_login + ')"? Ele passa a acessar o sistema.'
        : 'Revogar a aprovação de "' + u.no_usuario + " (" + u.no_login + ')"? Ele perde o acesso às funcionalidades.',
      rotuloOk: aprovar ? "Aprovar" : "Revogar",
      perigoso: !aprovar
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({
        url: "/api/usuarios/" + u.id_usuario + "/aprovado",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ aprovado: aprovar })
      })
        .done(function () {
          u.in_aprovado = aprovar;
          Toast.sucesso(aprovar ? "Usuário aprovado." : "Aprovação revogada.");
          aplicarFiltros();
        })
        .fail(function (xhr) {
          Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível salvar. Tente de novo.");
        });
    });
  }

  function aprovarPendentes() {
    var pendentes = listaFiltrada.filter(function (u) { return !u.in_aprovado && !souEu(u.id_usuario); });
    if (!pendentes.length) {
      Toast.info("Nenhum usuário pendente no filtro atual.");
      return;
    }
    Dialogo.confirmar({
      titulo: "Aprovar pendentes",
      mensagem: "Aprovar " + pendentes.length + " usuário(s) do filtro atual?",
      rotuloOk: "Aprovar todos"
    }).then(function (ok) {
      if (!ok) return;
      var concluidos = 0, falhas = 0;
      var $btn = $("#aprovar-pendentes-btn").prop("disabled", true);
      var proximo = function (i) {
        if (i >= pendentes.length) {
          $btn.prop("disabled", false);
          listaCompleta.forEach(function (u) {
            if (pendentes.some(function (p) { return p.id_usuario === u.id_usuario; }) && falhas === 0) {
              u.in_aprovado = true;
            }
          });
          carregarUsuarios();
          if (falhas) Toast.erro(concluidos + " aprovado(s), " + falhas + " falharam.");
          else Toast.sucesso(concluidos + " usuário(s) aprovado(s).");
          return;
        }
        $.ajax({
          url: "/api/usuarios/" + pendentes[i].id_usuario + "/aprovado",
          method: "POST",
          contentType: "application/json",
          data: JSON.stringify({ aprovado: true })
        })
          .done(function () { concluidos++; })
          .fail(function () { falhas++; })
          .always(function () { proximo(i + 1); });
      };
      proximo(0);
    });
  }

  // ---------- Redefinição de senha pelo administrador ----------

  var $modalRedef = $("#modal-redefinicao");
  var usuarioRedef = null;

  function abrirModalRedefinicao(u) {
    usuarioRedef = u;
    $("#redefinicao-intro").text(
      "Gerar um link de uso único para " + u.no_usuario + " (" + u.no_login + ") criar uma senha nova. " +
      "A senha atual continua valendo até ele usar o link."
    );
    $("#redefinicao-resultado").addClass("hidden");
    $("#redefinicao-acoes").removeClass("hidden");
    $("#redefinicao-erro, #redefinicao-status").text("").removeClass("success error");
    $("#redefinicao-enviar").toggleClass("hidden", !u.no_email);
    abrirModal($modalRedef);
    $("#redefinicao-gerar").focus();
  }

  function fecharModalRedefinicao() {
    fecharModal($modalRedef);
    // o link é credencial: não deixa sobrar na tela depois de fechar
    $("#redefinicao-valor").val("");
    usuarioRedef = null;
  }

  function gerarRedefinicao(enviarEmail) {
    var $erro = $("#redefinicao-erro");
    var $botoes = $("#redefinicao-gerar, #redefinicao-enviar");
    $erro.text("").removeClass("success error");
    $botoes.prop("disabled", true);

    $.ajax({
      url: "/api/usuarios/" + usuarioRedef.id_usuario + "/redefinicao",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ enviar_email: !!enviarEmail })
    })
      .done(function (resp) {
        var temLink = !!resp.url;
        $("#redefinicao-label").text(temLink ? "Link para entregar ao usuário" : "Código para entregar ao usuário");
        $("#redefinicao-valor").val(temLink ? resp.url : resp.token);
        $("#redefinicao-validade").text(
          (temLink ? "" : "http://{SISTEMA}/redefinir_senha.html#token=... ") +
          "Vale até " + formatarDataHora(resp.dt_expiracao) + " e só pode ser usado uma vez."
        );
        $("#redefinicao-resultado").removeClass("hidden");
        $("#redefinicao-acoes").addClass("hidden");
        if (enviarEmail) {
          if (resp.enviado_para) {
            $("#redefinicao-status").text("Enviado para " + resp.enviado_para + ".").addClass("success");
          } else {
            $("#redefinicao-status").text("Não foi possível enviar o e-mail — entregue o link acima.").addClass("error");
          }
        }
        $("#redefinicao-copiar").focus();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Não foi possível gerar o link.";
        $erro.text(msg).addClass("error");
      })
      .always(function () {
        $botoes.prop("disabled", false);
      });
  }

  function copiarRedefinicao() {
    var valor = $("#redefinicao-valor").val();
    var $status = $("#redefinicao-status");
    function ok() { $status.text("Copiado.").addClass("success").removeClass("error"); }
    function falhou() { $status.text("Não foi possível copiar — selecione e copie à mão.").addClass("error").removeClass("success"); }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(valor).then(ok, falhou);
      return;
    }
    var campo = $("#redefinicao-valor")[0];
    campo.select();
    try {
      if (document.execCommand("copy")) ok(); else falhou();
    } catch (e) {
      falhou();
    }
  }

  $("#redefinicao-fechar").on("click", fecharModalRedefinicao);
  $modalRedef.on("click", function (e) { if (e.target === this) fecharModalRedefinicao(); });
  $("#redefinicao-gerar").on("click", function () { gerarRedefinicao(false); });
  $("#redefinicao-enviar").on("click", function () { gerarRedefinicao(true); });
  $("#redefinicao-copiar").on("click", copiarRedefinicao);

  // ---------- Edição / criação ----------

  var $modalEditar = $("#modal-editar-usuario");
  var $editarForm = $("#editar-usuario-form");
  var $editarStatus = $("#editar-usuario-status");
  var $editarBtn = $("#editar-usuario-btn");
  var editandoUsuario = null;

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

  function abrirModalEditar(u) {
    editandoUsuario = u;
    $("#editar-login-input").val(u.no_login);
    $("#editar-nome-input").val(u.no_usuario);
    $("#editar-email-input").val(u.no_email || "");
    $("#editar-perfil-select").val(u.tp_perfil || "analista");
    popularSelectDelegacia($("#editar-delegacia-select"), u.id_delegacia);
    $editarStatus.text("").removeClass("success error");
    abrirModal($modalEditar);
    $("#editar-nome-input").focus();
  }

  function fecharModalEditar() {
    fecharModal($modalEditar);
    editandoUsuario = null;
  }

  $("#editar-usuario-fechar").on("click", fecharModalEditar);
  $modalEditar.on("click", function (e) { if (e.target === this) fecharModalEditar(); });

  $editarForm.on("submit", function (e) {
    e.preventDefault();
    $editarStatus.text("").removeClass("success error");
    var nome = $.trim($("#editar-nome-input").val());
    if (nome.length < 2) {
      $editarStatus.text("Dê um nome com ao menos 2 letras.").addClass("error");
      $("#editar-nome-input").focus();
      return;
    }
    if (souEu(editandoUsuario.id_usuario) && $("#editar-perfil-select").val() !== "administrador") {
      $editarStatus.text("Você não pode remover seu próprio perfil de administrador.").addClass("error");
      return;
    }
    $editarBtn.prop("disabled", true);
    $.ajax({
      url: "/api/usuarios/" + editandoUsuario.id_usuario,
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({
        nome: nome,
        email: $.trim($("#editar-email-input").val()) || null,
        perfil: $("#editar-perfil-select").val() || "analista",
        id_delegacia: parseInt($("#editar-delegacia-select").val(), 10) || null
      })
    })
      .done(function () {
        Toast.sucesso("Usuário atualizado.");
        carregarUsuarios();
        setTimeout(fecharModalEditar, 400);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao editar usuário.";
        $editarStatus.text(msg).addClass("error");
      })
      .always(function () {
        $editarBtn.prop("disabled", false);
      });
  });

  var RE_LOGIN = /^[a-z0-9._-]{3,60}$/;

  function abrirModalNovo() {
    $novoForm[0].reset();
    $("#novo-perfil-select").val("analista");
    popularSelectDelegacia($("#novo-delegacia-select"), null);
    $novoStatus.text("").removeClass("success error");
    abrirModal($modalNovo);
    $("#novo-login-input").focus();
  }

  function fecharModalNovo() {
    fecharModal($modalNovo);
    $("#novo-senha-input").val("");
  }

  $("#abrir-novo-usuario-btn").on("click", abrirModalNovo);
  $("#novo-usuario-fechar").on("click", fecharModalNovo);
  $modalNovo.on("click", function (e) { if (e.target === this) fecharModalNovo(); });

  $novoForm.on("submit", function (e) {
    e.preventDefault();
    $novoStatus.text("").removeClass("success error");
    var login = $.trim($("#novo-login-input").val()).toLowerCase();
    var nome = $.trim($("#novo-nome-input").val());
    var senha = $("#novo-senha-input").val() || "";
    if (!RE_LOGIN.test(login)) {
      $novoStatus.text("Login inválido: 3–60 minúsculas, sem espaço (letras, números, ponto, hífen, sublinhado).").addClass("error");
      $("#novo-login-input").focus();
      return;
    }
    if (nome.length < 2) {
      $novoStatus.text("Dê um nome com ao menos 2 letras.").addClass("error");
      $("#novo-nome-input").focus();
      return;
    }
    if (senha.length < 8) {
      $novoStatus.text("A senha precisa ter pelo menos 8 caracteres.").addClass("error");
      $("#novo-senha-input").focus();
      return;
    }
    $novoBtn.prop("disabled", true);
    $.ajax({
      url: "/api/usuarios",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({
        login: login,
        nome: nome,
        senha: senha,
        perfil: $("#novo-perfil-select").val() || "analista",
        email: $.trim($("#novo-email-input").val()) || null,
        id_delegacia: parseInt($("#novo-delegacia-select").val(), 10) || null
      })
    })
      .done(function () {
        Toast.sucesso('Usuário "' + login + '" criado e já aprovado.');
        carregarUsuarios();
        setTimeout(fecharModalNovo, 400);
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar usuário.";
        $novoStatus.text(msg).addClass("error");
      })
      .always(function () {
        $novoBtn.prop("disabled", false);
      });
  });

  // ---------- IA do usuário ----------

  var $modalIa = $("#modal-config-ia");
  var $iaStatus = $("#config-ia-status");
  var usuarioIa = null;
  var iaDefinida = false;
  var iaRemoverCert = false;

  function statusIa(texto, tipo) {
    $iaStatus.text(texto || "").removeClass("success error").addClass(tipo || "");
  }

  function atualizarCamposIa(modelo, apiUrl) {
    var provedor = $("#config-ia-provider").val();
    var preset = IAPresets.CHAT_PRESETS[provedor] || IAPresets.CHAT_PRESETS.outro;
    IAPresets.popularSelect($("#config-ia-model"), $("#config-ia-model-custom"), preset.models, modelo);
    IAPresets.popularSelect($("#config-ia-api-url"), $("#config-ia-api-url-custom"), preset.urls, apiUrl);
    var comCertificado = provedor === "openai-cert";
    $("#config-ia-ca-cert-wrap").toggleClass("hidden", !comCertificado);
    $("#config-ia-reasoning-wrap").toggleClass("hidden", !comCertificado);
  }

  function alternarCamposIa() {
    $("#config-ia-campos").toggleClass("hidden", !$("#config-ia-ativa").is(":checked"));
  }

  function abrirModalConfigIa(u) {
    usuarioIa = u;
    iaDefinida = false;
    iaRemoverCert = false;
    statusIa("");
    $("#config-ia-intro").text(
      "Provedor, modelo e chave que " + u.no_usuario + " (" + u.no_login + ") vai usar. " +
      "Sem isso, ele usa a IA das Configurações, como a maioria dos usuários." +
      (u.in_ia_ativa === false ? " ATENÇÃO: a IA dele está DESATIVADA — nada disto vale até religar (Mais ▾ → Reativar IA)." : "")
    );
    IAPresets.montarSelectProvedores($("#config-ia-provider"), IAPresets.CHAT_PROVIDERS);
    $("#config-ia-form")[0].reset();
    $("#config-ia-api-key").attr("placeholder", "sk-...");
    $("#config-ia-api-key-help").text("A chave (ou a cota) deste usuário no provedor. Ollama e LM Studio locais não pedem chave.");
    $("#config-ia-ca-cert").val("");
    $("#config-ia-ca-cert-file").val("");
    $("#config-ia-ca-cert-remover").addClass("hidden");
    $("#config-ia-apagar").addClass("hidden");
    abrirModal($modalIa);
    $("#config-ia-ativa").focus();

    $.getJSON("/api/usuarios/" + u.id_usuario + "/config-ia")
      .done(function (dados) {
        dados = dados || {};
        iaDefinida = !!dados.definida;
        $("#config-ia-ativa").prop("checked", iaDefinida && !!dados.ativa);
        $("#config-ia-provider").val(dados.provider || "openai");
        $("#config-ia-reasoning").val(dados.reasoningEffort || "low");
        $("#config-ia-context-length").val(dados.contextLength || "");
        atualizarCamposIa(dados.model, dados.apiUrl);
        if (dados.apiKeyMasked) {
          $("#config-ia-api-key").attr("placeholder", dados.apiKeyMasked);
          $("#config-ia-api-key-help").text("Uma chave já está salva para este usuário. Deixe em branco para mantê-la.");
        }
        if (dados.caCertResumo) {
          $("#config-ia-ca-cert-help").text("Certificado salvo: " + dados.caCertResumo + ". Deixe em branco para mantê-lo.");
          $("#config-ia-ca-cert-remover").removeClass("hidden");
        }
        $("#config-ia-apagar").toggleClass("hidden", !iaDefinida);
        alternarCamposIa();
      })
      .fail(function (xhr) {
        atualizarCamposIa("", "");
        alternarCamposIa();
        statusIa(
          xhr.status === 500
            ? "Não foi possível ler a configuração — a migração 39 do banco já foi aplicada?"
            : "Não foi possível carregar a configuração atual.",
          "error"
        );
      });
  }

  function fecharModalConfigIa() {
    fecharModal($modalIa);
    $("#config-ia-api-key").val("");
    $("#config-ia-ca-cert").val("");
    usuarioIa = null;
  }

  $("#config-ia-fechar").on("click", fecharModalConfigIa);
  $modalIa.on("click", function (e) { if (e.target === this) fecharModalConfigIa(); });
  $("#config-ia-ativa").on("change", alternarCamposIa);
  $("#config-ia-provider").on("change", function () { atualizarCamposIa("", ""); });
  $("#config-ia-model").on("change", function () {
    $("#config-ia-model-custom").toggleClass("hidden", $(this).val() !== IAPresets.OUTRO);
  });
  $("#config-ia-api-url").on("change", function () {
    $("#config-ia-api-url-custom").toggleClass("hidden", $(this).val() !== IAPresets.OUTRO);
  });

  $("#config-ia-ca-cert-file").on("change", function () {
    var arquivo = this.files && this.files[0];
    if (!arquivo) return;
    var leitor = new FileReader();
    leitor.onload = function () {
      $("#config-ia-ca-cert").val(String(leitor.result || ""));
      iaRemoverCert = false;
    };
    leitor.onerror = function () { Dialogo.alerta("Não foi possível ler o arquivo do certificado."); };
    leitor.readAsText(arquivo);
  });

  $("#config-ia-ca-cert-remover").on("click", function () {
    iaRemoverCert = true;
    $("#config-ia-ca-cert").val("");
    $("#config-ia-ca-cert-file").val("");
    $("#config-ia-ca-cert-help").text("O certificado será apagado ao salvar.");
  });

  $("#config-ia-form").on("submit", function (e) {
    e.preventDefault();
    if (!usuarioIa) return;
    var ativa = $("#config-ia-ativa").is(":checked");
    if (!ativa && !iaDefinida) {
      statusIa("Este usuário já usa a IA das Configurações.", "success");
      return;
    }
    statusIa("");
    var $botao = $("#config-ia-salvar").prop("disabled", true);
    $.ajax({
      url: "/api/usuarios/" + usuarioIa.id_usuario + "/config-ia",
      method: "PUT",
      contentType: "application/json",
      data: JSON.stringify({
        ativa: ativa,
        provider: $("#config-ia-provider").val(),
        model: IAPresets.valorAtual($("#config-ia-model"), $("#config-ia-model-custom")),
        apiUrl: IAPresets.valorAtual($("#config-ia-api-url"), $("#config-ia-api-url-custom")),
        apiKey: $("#config-ia-api-key").val(),
        contextLength: parseInt($("#config-ia-context-length").val(), 10) || 0,
        reasoningEffort: $("#config-ia-reasoning").val(),
        caCert: $.trim($("#config-ia-ca-cert").val()),
        removerCaCert: iaRemoverCert
      })
    })
      .done(function () {
        Toast.sucesso("IA do usuário salva.");
        carregarUsuarios();
        setTimeout(fecharModalConfigIa, 400);
      })
      .fail(function (xhr) {
        statusIa((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar a IA do usuário.", "error");
      })
      .always(function () { $botao.prop("disabled", false); });
  });

  $("#config-ia-apagar").on("click", function () {
    if (!usuarioIa) return;
    var alvo = usuarioIa;
    Dialogo.confirmar({
      titulo: "Voltar ao padrão",
      mensagem: "Apagar a IA própria de " + alvo.no_usuario + "? Ele volta a usar a IA das Configurações.",
      rotuloOk: "Apagar",
      perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/usuarios/" + alvo.id_usuario + "/config-ia", method: "DELETE" })
        .done(function () {
          Toast.sucesso("IA própria removida.");
          carregarUsuarios();
          fecharModalConfigIa();
        })
        .fail(function (xhr) {
          statusIa((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao apagar.", "error");
        });
    });
  });

  // ---------- filtros / paginação / retry / ESC ----------

  var aplicarComDebounce = AnalyserUtils.debounce(function () {
    pagina = 1;
    aplicarFiltros();
  }, 150);

  $busca.on("input", aplicarComDebounce);
  $filtroPerfil.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroStatus.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroAprovacao.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroDelegacia.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroIa.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroOrdem.on("change", function () { pagina = 1; aplicarFiltros(); });
  $filtroLimpar.on("click", function () {
    $busca.val("");
    $filtroPerfil.val("");
    $filtroStatus.val("");
    $filtroAprovacao.val("");
    $filtroDelegacia.val("");
    $filtroIa.val("");
    $filtroOrdem.val("nome");
    pagina = 1;
    aplicarFiltros();
    $busca.focus();
  });

  $("#usuarios-pag-anterior").on("click", function () {
    if (pagina > 1) { pagina--; renderTabela(); atualizarContadores(Math.max(1, Math.ceil(listaFiltrada.length / POR_PAGINA))); }
  });
  $("#usuarios-pag-proximo").on("click", function () {
    var total = Math.max(1, Math.ceil(listaFiltrada.length / POR_PAGINA));
    if (pagina < total) { pagina++; renderTabela(); atualizarContadores(total); }
  });
  $("#usuarios-recarregar").on("click", carregarUsuarios);
  $("#aprovar-pendentes-btn").on("click", aprovarPendentes);

  $(document).on("keydown", function (e) {
    if (e.key !== "Escape") return;
    if ($(".toast").length) return; // o toast consome o ESC dele
    if (!$modalNovo.hasClass("hidden")) fecharModalNovo();
    else if (!$modalEditar.hasClass("hidden")) fecharModalEditar();
    else if (!$modalIa.hasClass("hidden")) fecharModalConfigIa();
    else if (!$modalRedef.hasClass("hidden")) fecharModalRedefinicao();
  });

  // Mostra/esconde senha (novo usuário + IA) — mesmo padrão do olho das Configurações.
  $(document).on("click", ".password-toggle", function () {
    var id = $(this).attr("data-toggle-key");
    var $campo = id ? $("#" + id) : $(this).siblings("input");
    if (!$campo.length) return;
    var mostrando = $campo.attr("type") === "text";
    $campo.attr("type", mostrando ? "password" : "text");
    $(this).attr("aria-label", mostrando ? "Mostrar senha" : "Ocultar senha");
  });

  // Exportação em Excel do conjunto filtrado (não só da página desenhada).
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "usuarios",
      dados: function () {
        if (!listaFiltrada.length) return null;
        return {
          colunas: ["Login", "Nome", "E-mail", "Delegacia", "Perfil", "IA", "Último login", "Status", "Aprovação"],
          linhas: listaFiltrada.map(function (u) {
            return [
              u.no_login || "",
              u.no_usuario || "",
              u.no_email || "",
              nomeDelegacia(u.id_delegacia),
              rotuloPerfil(u.tp_perfil),
              u.in_ia_ativa === false ? "Desativada" : (temIaPropria(u.id_usuario) ? "Própria" : "Padrão"),
              u.dt_ultimo_login ? formatarDataHora(u.dt_ultimo_login) : "Nunca acessou",
              u.in_ativo ? "Ativo" : "Desativado",
              u.in_aprovado ? "Aprovado" : "Pendente"
            ];
          })
        };
      }
    });
  }

  descobrirEu();
  carregarUsuarios();
  carregarDelegacias();
})();
