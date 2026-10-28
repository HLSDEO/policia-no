/* Painel "Gestão de usuários" da tela Auditoria — lista ações administrativas em usuários.
 * Mesmo padrão de auditoria_acesso.js / auditoria_ia.js. */
(function () {
  "use strict";

  var $card = $("#auditoria-usuarios-card");
  var $corpo = $("#audusr-corpo");
  var $vazio = $("#audusr-vazio");
  var $paginacao = $("#audusr-paginacao");
  var $pagInfo = $("#audusr-pag-info");
  var $pagAnterior = $("#audusr-pag-anterior");
  var $pagProximo = $("#audusr-pag-proximo");

  var $filtroAcao = $("#audusr-filtro-acao");
  var $filtroAutor = $("#audusr-filtro-autor");
  var $filtroAlvo = $("#audusr-filtro-alvo");
  var $filtroDataDe = $("#audusr-filtro-data-de");
  var $filtroDataAte = $("#audusr-filtro-data-ate");
  var $btnAtualizar = $("#audusr-atualizar-btn");

  var ROTULO_ACAO = {
    criar: "Criar usuário",
    editar: "Editar usuário",
    ativar: "Ativar usuário",
    desativar: "Desativar usuário",
    aprovar: "Aprovar usuário",
    revogar_aprovacao: "Revogar aprovação",
    redefinir_senha: "Redefinir senha",
    configurar_ia: "Configurar IA própria",
    apagar_ia: "Remover IA própria",
    alterar_delegacia: "Alterar delegacia",
    ativar_ia: "Reativar IA do usuário",
    desativar_ia: "Desativar IA do usuário",
  };

  var paginaAtual = 0;
  var TAMANHO_PAGINA = 50;
  var totalRegistros = 0;
  var autoresCache = [];
  var alvosCache = [];

  function formatarDataHora(iso) {
    return AnalyserUtils.formatarDataHora(iso);
  }

  function formatarDetalhes(detalhes) {
    if (!detalhes) return "—";
    try {
      var obj = typeof detalhes === "string" ? JSON.parse(detalhes) : detalhes;
      var partes = [];
      for (var k in obj) {
        if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
        var v = obj[k];
        if (v === null || v === undefined || v === "") continue;
        if (k === "api_key" || k === "ca_cert" || k === "senha") continue;
        var rotulo = k.replace(/_/g, " ");
        partes.push(rotulo + ": " + String(v));
      }
      return partes.length ? partes.join("; ") : "—";
    } catch (e) {
      return String(detalhes);
    }
  }

  function renderLinha(item) {
    var $tr = $("<tr>");
    $("<td>").text(formatarDataHora(item.dt_acao)).appendTo($tr);
    $("<td>").text(item.no_autor || item.login_autor || "—").appendTo($tr);
    $("<td>").append(
      $("<span>").addClass("badge badge-info").text(ROTULO_ACAO[item.tp_acao] || item.tp_acao)
    ).appendTo($tr);
    $("<td>").text(item.no_alvo || item.login_alvo || "—").appendTo($tr);
    $("<td>").text(formatarDetalhes(item.tx_detalhes)).appendTo($tr);
    $("<td>").text(item.co_ip || "—").appendTo($tr);
    return $tr;
  }

  function atualizarKPIs(totais) {
    $("#audusr-kpi-total").text(totais.qt_total || 0);
    $("#audusr-kpi-autores").text(totais.qt_autores || 0);
    $("#audusr-kpi-alvos").text(totais.qt_alvos || 0);
    $("#audusr-kpi-tipos").text(totais.qt_tipos_acao || 0);
  }

  function popularSelectAutores(itens) {
    var ids = [];
    itens.forEach(function (item) {
      if (item.id_usuario_autor && ids.indexOf(item.id_usuario_autor) === -1) {
        ids.push(item.id_usuario_autor);
        autoresCache.push({ id: item.id_usuario_autor, nome: item.no_autor, login: item.login_autor });
      }
    });
    var atual = $filtroAutor.val();
    $filtroAutor.empty().append($("<option>").val("").text("Todos os autores"));
    autoresCache.forEach(function (a) {
      var texto = (a.nome || a.login) + " (" + a.login + ")";
      $filtroAutor.append($("<option>").val(a.id).text(texto));
    });
    $filtroAutor.val(atual);
  }

  function popularSelectAlvos(itens) {
    var ids = [];
    itens.forEach(function (item) {
      if (item.id_usuario_alvo && ids.indexOf(item.id_usuario_alvo) === -1) {
        ids.push(item.id_usuario_alvo);
        alvosCache.push({ id: item.id_usuario_alvo, nome: item.no_alvo, login: item.login_alvo });
      }
    });
    var atual = $filtroAlvo.val();
    $filtroAlvo.empty().append($("<option>").val("").text("Todos os usuários alvo"));
    alvosCache.forEach(function (a) {
      var texto = (a.nome || a.login) + " (" + a.login + ")";
      $filtroAlvo.append($("<option>").val(a.id).text(texto));
    });
    $filtroAlvo.val(atual);
  }

  function carregar() {
    var params = {
      limite: TAMANHO_PAGINA,
      deslocamento: paginaAtual * TAMANHO_PAGINA,
    };

    var acao = $filtroAcao.val();
    if (acao) params.tp_acao = acao;

    var autor = $filtroAutor.val();
    if (autor) params.id_usuario_autor = parseInt(autor, 10);

    var alvo = $filtroAlvo.val();
    if (alvo) params.id_usuario_alvo = parseInt(alvo, 10);

    var de = $filtroDataDe.val();
    var ate = $filtroDataAte.val();
    if (de || ate) {
      var dias = null;
      if (de && ate) {
        var diff = (new Date(ate) - new Date(de)) / (1000 * 60 * 60 * 24);
        if (diff >= 0) dias = Math.ceil(diff) + 1;
      } else if (de) {
        var diff = (Date.now() - new Date(de)) / (1000 * 60 * 60 * 24);
        if (diff >= 0) dias = Math.ceil(diff) + 1;
      }
      if (dias) params.dias = dias;
    }

    $btnAtualizar.prop("disabled", true).text("Carregando…");

    $.getJSON("/api/admin/usuarios/auditoria", params)
      .done(function (resp) {
        totalRegistros = resp.qt_total || 0;
        $corpo.empty();
        if (resp.itens && resp.itens.length) {
          $vazio.addClass("hidden");
          resp.itens.forEach(function (item) { $corpo.append(renderLinha(item)); });
          popularSelectAutores(resp.itens);
          popularSelectAlvos(resp.itens);
        } else {
          $vazio.removeClass("hidden");
        }
        atualizarKPIs(resp.totais || {});
        atualizarPaginacao();
      })
      .fail(function () {
        $vazio.text("Erro ao carregar a auditoria de usuários.").removeClass("hidden");
      })
      .always(function () {
        $btnAtualizar.prop("disabled", false).text("Atualizar");
      });
  }

  function atualizarPaginacao() {
    var totalPaginas = Math.ceil(totalRegistros / TAMANHO_PAGINA);
    if (totalPaginas <= 1) {
      $paginacao.hide();
      return;
    }
    $paginacao.show();
    $pagInfo.text("Página " + (paginaAtual + 1) + " de " + totalPaginas);
    $pagAnterior.prop("disabled", paginaAtual === 0);
    $pagProximo.prop("disabled", paginaAtual >= totalPaginas - 1);
  }

  $pagAnterior.on("click", function () {
    if (paginaAtual > 0) {
      paginaAtual--;
      carregar();
    }
  });

  $pagProximo.on("click", function () {
    var totalPaginas = Math.ceil(totalRegistros / TAMANHO_PAGINA);
    if (paginaAtual < totalPaginas - 1) {
      paginaAtual++;
      carregar();
    }
  });

  $btnAtualizar.on("click", function () {
    paginaAtual = 0;
    carregar();
  });

  $filtroAcao.on("change", function () { paginaAtual = 0; carregar(); });
  $filtroAutor.on("change", function () { paginaAtual = 0; carregar(); });
  $filtroAlvo.on("change", function () { paginaAtual = 0; carregar(); });
  $filtroDataDe.on("change", function () { paginaAtual = 0; carregar(); });
  $filtroDataAte.on("change", function () { paginaAtual = 0; carregar(); });

  // Inicializa quando a aba "usuários" for ativada
  var $abaUsuarios = $('.extracao-tab[data-tab="usuarios"]');
  function aoAtivarUsuarios() {
    $card.removeClass("hidden");
    if ($corpo.children().length === 0 && $vazio.hasClass("hidden")) {
      paginaAtual = 0;
      autoresCache = [];
      alvosCache = [];
      carregar();
    }
  }

  // O auditoria.js gerencia as abas; escutamos a mudança
  $(document).on("click", '.extracao-tab[data-tab="usuarios"]', aoAtivarUsuarios);

  // Se já estiver ativa no carregamento inicial
  if ($abaUsuarios.hasClass("active")) {
    aoAtivarUsuarios();
  }
})();