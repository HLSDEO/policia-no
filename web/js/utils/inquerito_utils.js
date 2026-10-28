/* Utilitários compartilhados para ações de inquérito.
 * Usado por inqueritos.js e investigacao.js para evitar duplicação. */
(function () {
  "use strict";

  var ICONE_MESA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>';
  var ICONE_IA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z"/><path d="M19 15l.7 2.1L22 18l-2.3.9L19 21l-.7-2.1L16 18l2.3-.9L19 15z"/></svg>';
  var ICONE_WHATSAPP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.4 8.4 0 0 1-4.15-1.1L3 20l1.15-5.15A8.4 8.4 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3h.5A8.5 8.5 0 0 1 21 11.5z"/></svg>';
  var ICONE_DISPOSITIVO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>';
  var ICONE_RIF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="13" x2="8" y2="17"/><line x1="12" y1="13" x2="12" y2="17"/><line x1="16" y1="13" x2="16" y2="17"/></svg>';
  var ICONE_SIMBA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-6 9 6"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"/></svg>';
  var ICONE_DOCUMENTO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
  var ICONE_EDITAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  var ICONE_PERMISSOES = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1l9 5v6c0 5.55-3.84 10.74-9 12c-5.16-1.26-9-6.45-9-12V6l9-5z"/><polyline points="10 17 14 13 18 17"/><line x1="12" y1="9" x2="12" y2="13"/></svg>';
  var ICONE_APAGAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';
  var ICONE_ENTIDADE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
  var ICONE_TELEFONIA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v9"/><path d="M9 12h6l2 9H7l2-9z"/><path d="M6.5 6.5a7 7 0 0 1 11 0"/><path d="M4 4a10.5 10.5 0 0 1 16 0"/></svg>';
  var ICONE_RELAO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';

  function formatarNumero(n) {
    if (n === null || n === undefined) return "0";
    return n.toLocaleString("pt-BR");
  }

  // O usuário da sessão chega pelo evento "analyser:usuario" (js/utils/auth.js) — não existe
  // window.Auth. Guardamos a foto aqui para o podeGerenciar ter com o que comparar.
  var usuarioSessao = null;
  $(document).on("analyser:usuario.inqueritoUtils", function (e, usuario) {
    usuarioSessao = usuario || null;
  });

  function podeGerenciar(inq) {
    if (!usuarioSessao) return false;
    if (usuarioSessao.in_admin) return true; // /api/auth/eu devolve in_admin (não eh_admin)
    return String(inq.id_usuario_criador) === String(usuarioSessao.id_usuario);
  }

  function criarMenuAcoes(itens) {
    if (!itens || !itens.length) return null;

    var $wrap = $("<div>").addClass("acoes-menu");
    var $btn = $("<button>")
      .addClass("btn btn-secondary")
      .attr("type", "button")
      .html('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>')
      .appendTo($wrap);

    var $lista = $("<div>").addClass("acoes-menu-lista hidden").appendTo($wrap);

    itens.forEach(function (item) {
      if (item === "divisor") {
        $("<div>").addClass("acoes-menu-divisor").appendTo($lista);
        return;
      }

      var $item;
      if (item.$el) {
        $item = item.$el;
        if (item.$el.is("button")) {
          $item.addClass("acoes-menu-item").css({ width: "100%", textAlign: "left", justifyContent: "flex-start" });
        }
      } else {
        $item = $("<button>")
          .addClass("acoes-menu-item" + (item.perigoso ? " perigoso" : ""))
          .attr("type", "button")
          .prop("disabled", !!item.disabled)
          .attr("title", item.title || "")
          .html(item.html)
          .on("click", function () {
            $lista.addClass("hidden");
            $btn.attr("aria-expanded", "false");
            if (item.onClick) item.onClick();
          });
      }
      $lista.append($item);
    });

    $btn.on("click", function (e) {
      e.stopPropagation();
      var aberto = $lista.hasClass("hidden");
      $(".acoes-menu-lista").addClass("hidden");
      $(".acoes-menu .btn").attr("aria-expanded", "false");
      $lista.toggleClass("hidden", !aberto);
      $btn.attr("aria-expanded", String(aberto));
    });

    $(document).on("click", function (e) {
      if (!$(e.target).closest(".acoes-menu").length) {
        $lista.addClass("hidden");
        $btn.attr("aria-expanded", "false");
      }
    });

    return $wrap;
  }

  function abrirModalDocumentos(idInquerito, numeroInquerito) {
    var $modal = $("#modal-documentos");
    var $numero = $("#documentos-inquerito-numero");
    var $lista = $("#documentos-lista");
    var $vazio = $("#documentos-vazio");

    if (!$modal.length) return;

    $numero.text(numeroInquerito);
    $lista.empty();
    $vazio.addClass("hidden");
    $modal.removeClass("hidden");

    Carregando.emBloco($lista);

    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/documentos")
      .done(function (docs) {
        Carregando.limpar($lista);
        if (!docs || !docs.length) {
          $vazio.removeClass("hidden");
          return;
        }
        docs.forEach(function (d) {
          var $linha = $("<div>").addClass("permissao-linha").css({ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", padding: "8px 12px", border: "1px solid var(--border)", borderRadius: "8px", marginBottom: "6px" });
          var $info = $("<div>").css({ display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 });
          $("<strong>").css({ fontSize: "13px", color: "var(--text-primary)" }).text(d.no_arquivo || "Documento").appendTo($info);
          $("<span>").css({ fontSize: "12px", color: "var(--text-secondary)" }).text(formatarNumero(d.qt_paginas) + " pág · " + formatarNumero(d.qt_entidades) + " ent. · " + (d.cd_analise === "concluido" ? "Analisado" : d.cd_analise)).appendTo($info);
          $linha.append($info);
          var $btnVer = $("<button>").addClass("btn btn-secondary btn-small").text("Ver texto").on("click", function () { abrirModalDocumentoTexto(idInquerito, d.id_documento, d.no_arquivo); });
          $linha.append($btnVer);
          $lista.append($linha);
        });
      })
      .fail(function (xhr) {
        Carregando.limpar($lista);
        Toast.erro("Erro ao carregar documentos: " + (xhr.responseJSON?.detail || xhr.statusText));
      });
  }

  function abrirModalDocumentoTexto(idInquerito, idDocumento, nomeArquivo) {
    var $modal = $("#modal-documento-texto");
    var $nome = $("#documento-texto-nome");
    var $conteudo = $("#documento-texto-conteudo");
    var $vazio = $("#documento-texto-vazio");
    var $status = $("#documento-texto-status");
    var $copiar = $("#documento-texto-copiar");

    if (!$modal.length) return;

    $nome.text(nomeArquivo);
    $conteudo.empty().text("Carregando…");
    $vazio.addClass("hidden");
    $status.text("");
    $modal.removeClass("hidden");

    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/documentos/" + encodeURIComponent(idDocumento))
      .done(function (resp) {
        if (resp?.erro) {
          $conteudo.empty();
          $vazio.removeClass("hidden").text(resp.erro);
          return;
        }
        var texto = resp?.conteudo || "";
        if (!texto) {
          $conteudo.empty();
          $vazio.removeClass("hidden").text("Nenhum texto extraído deste documento.");
          return;
        }
        $conteudo.text(texto);
        $copiar.off("click").on("click", function () {
          navigator.clipboard.writeText(texto).then(function () {
            $status.addClass("success").text("Copiado!").removeClass("error");
            setTimeout(function () { $status.text(""); }, 2000);
          }).catch(function () {
            $status.addClass("error").text("Falha ao copiar").removeClass("success");
          });
        });
      })
      .fail(function (xhr) {
        $conteudo.empty();
        $vazio.removeClass("hidden").text("Erro: " + (xhr.responseJSON?.detail || xhr.statusText));
      });
  }

  function renomearInquerito(inq) {
    Dialogo.prompt({
      titulo: "Editar inquérito",
      mensagem: "Corrigir o número do inquérito no ePol:",
      valor: inq.nu_inquerito,
      rotuloOk: "Salvar",
      rotuloCancelar: "Cancelar"
    }).then(function (novoNumero) {
      if (!novoNumero || novoNumero === inq.nu_inquerito) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + inq.id_inquerito,
        method: "PATCH",
        contentType: "application/json",
        data: JSON.stringify({ nu_inquerito: novoNumero })
      }).done(function () {
        Toast.sucesso("Inquérito renomeado");
        if (window.atualizarTudo) window.atualizarTudo();
        else if (window.carregarInqueritos) window.carregarInqueritos();
      }).fail(function (xhr) {
        Dialogo.alerta({ titulo: "Erro", mensagem: xhr.responseJSON?.detail || "Erro ao renomear" });
      });
    });
  }

  function abrirModalPermissoes(idInquerito, numeroInquerito) {
    var $modal = $("#modal-permissoes");
    var $numero = $("#permissoes-inquerito-numero");
    var $lista = $("#permissoes-lista");
    var $vazio = $("#permissoes-vazio");
    var $select = $("#permissao-select");
    var $btnAdd = $("#permissao-adicionar-btn");
    var $status = $("#permissoes-status");
    var $adminCheck = $("#permissao-admin-checkbox");

    if (!$modal.length) return;

    $numero.text(numeroInquerito);
    $lista.empty();
    $vazio.addClass("hidden");
    $status.text("");
    $adminCheck.prop("checked", false);
    $modal.removeClass("hidden");

    Carregando.emBloco($lista);

    $.when(
      $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/permissoes"),
      $.getJSON("/api/usuarios")
    ).done(function (permsResp, usersResp) {
      var perms = permsResp[0] || [];
      var users = usersResp[0] || [];
      Carregando.limpar($lista);

      if (!perms.length) $vazio.removeClass("hidden");

      perms.forEach(function (p) {
        var $linha = $("<div>").addClass("permissao-linha");
        var $info = $("<div>").css({ display: "flex", flexDirection: "column", gap: "2px" });
        $("<span>").addClass("permissao-nome").text(p.no_usuario || p.login).appendTo($info);
        $("<span>").addClass("permissao-login").text(p.login + (p.eh_admin ? " · Admin" : "")).appendTo($info);
        $linha.append($info);
        if (p.eh_admin) {
          $("<span>").addClass("badge badge-neutro").css({ fontSize: "11px" }).text("Admin").appendTo($linha);
        } else {
          var $btnRemover = $("<button>").addClass("btn btn-danger btn-small").text("Remover").on("click", function () { removerPermissao(idInquerito, p.id_usuario, $linha); });
          $linha.append($btnRemover);
        }
        $lista.append($linha);
      });

      $select.empty().append('<option value="">Selecione um usuário</option>');
      users.forEach(function (u) {
        var jaTem = perms.some(function (p) { return String(p.id_usuario) === String(u.id_usuario); });
        if (!jaTem) $select.append($("<option>").val(u.id_usuario).text((u.no_usuario || u.login) + " (" + u.login + ")"));
      });
    }).fail(function (xhr) {
      Carregando.limpar($lista);
      Toast.erro("Erro ao carregar: " + (xhr.responseJSON?.detail || xhr.statusText));
    });

    $btnAdd.off("click").on("click", function () {
      var idUsuario = $select.val();
      if (!idUsuario) return;
      var ehAdmin = $adminCheck.prop("checked");
      $btnAdd.prop("disabled", true);
      $.ajax({
        url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/permissoes",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ id_usuario: idUsuario, eh_admin: ehAdmin })
      }).done(function () {
        Toast.sucesso("Permissão adicionada");
        $select.val("");
        $adminCheck.prop("checked", false);
        abrirModalPermissoes(idInquerito, numeroInquerito);
      }).fail(function (xhr) {
        $status.removeClass("success").addClass("error").text(xhr.responseJSON?.detail || "Erro ao adicionar");
      }).always(function () { $btnAdd.prop("disabled", false); });
    });
  }

  function removerPermissao(idInquerito, idUsuario, $linha) {
    Dialogo.confirmar({
      titulo: "Remover permissão",
      mensagem: "Tem certeza que deseja remover este usuário do inquérito?",
      rotuloOk: "Remover", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/permissoes/" + encodeURIComponent(idUsuario), method: "DELETE" })
        .done(function () { $linha.remove(); Toast.sucesso("Permissão removida"); })
        .fail(function (xhr) { Toast.erro(xhr.responseJSON?.detail || "Erro ao remover"); });
    });
  }

  function apagarInquerito(idInquerito, numeroInquerito) {
    Dialogo.confirmar({
      titulo: "Apagar inquérito",
      mensagem: "Apagar o inquérito \"" + numeroInquerito + "\" por completo? Todas as extrações, RIFs, SIMBA, documentos, entidades e relações serão removidos. Essa ação não pode ser desfeita.",
      rotuloOk: "Apagar", perigoso: true
    }).then(function (ok) {
      if (!ok) return;
      $.ajax({ url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito), method: "DELETE" })
        .done(function () {
          Toast.sucesso("Inquérito apagado");
          if (window.atualizarTudo) window.atualizarTudo();
          else if (window.carregarInqueritos) window.carregarInqueritos();
        })
        .fail(function (xhr) {
          Dialogo.alerta({ titulo: "Erro", mensagem: xhr.responseJSON?.detail || "Erro ao apagar o inquérito." });
        });
    });
  }

  window.InqueritoUtils = {
    formatarNumero: formatarNumero,
    podeGerenciar: podeGerenciar,
    criarMenuAcoes: criarMenuAcoes,
    abrirModalDocumentos: abrirModalDocumentos,
    abrirModalDocumentoTexto: abrirModalDocumentoTexto,
    renomearInquerito: renomearInquerito,
    abrirModalPermissoes: abrirModalPermissoes,
    apagarInquerito: apagarInquerito,
    ICONE_MESA: ICONE_MESA,
    ICONE_IA: ICONE_IA,
    ICONE_WHATSAPP: ICONE_WHATSAPP,
    ICONE_DISPOSITIVO: ICONE_DISPOSITIVO,
    ICONE_RIF: ICONE_RIF,
    ICONE_SIMBA: ICONE_SIMBA,
    ICONE_TELEFONIA: ICONE_TELEFONIA,
    ICONE_DOCUMENTO: ICONE_DOCUMENTO,
    ICONE_EDITAR: ICONE_EDITAR,
    ICONE_PERMISSOES: ICONE_PERMISSOES,
    ICONE_APAGAR: ICONE_APAGAR,
    ICONE_ENTIDADE: ICONE_ENTIDADE,
    ICONE_RELAO: ICONE_RELAO
  };
})();