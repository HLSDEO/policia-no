(function () {
  "use strict";

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var $adminCard = $("#admin-card");
  var $semPermissao = $("#sem-permissao-hint");
  var $lista = $("#padroes-lista");
  var $vazio = $("#padroes-vazio");

  // modal padrão (nome/descrição)
  var $modal = $("#modal-padrao");
  var $modalTitulo = $("#modal-padrao-titulo");
  var $form = $("#padrao-form");
  var $nomeInput = $("#padrao-nome-input");
  var $descricaoInput = $("#padrao-descricao-input");
  var $status = $("#padrao-status");
  var $salvarBtn = $("#padrao-salvar-btn");
  var idEditando = null;

  // modal blocos
  var $modalBlocos = $("#modal-blocos");
  var $blocosTitulo = $("#blocos-titulo");
  var $blocosLista = $("#blocos-lista");
  var $blocosVazio = $("#blocos-vazio");
  var $blocosStatus = $("#blocos-status");
  var $variaveisAjuda = $("#variaveis-ajuda");
  var padraoAtual = null; // { id_padrao, no_padrao }

  var TP_BLOCO_ROTULO = {
    capa: "Capa", sumario: "Sumário", texto: "Texto",
    tabela_campos: "Tabela de campos", extracoes: "Arquivos de extração",
    contas: "Contas de usuário", whatsapp: "WhatsApp", marcacoes: "Marcações",
    chat: "Chat (tela de celular)",
    bancario_contas: "Bancário — contas", bancario_contrapartes: "Bancário — contrapartes",
    bancario_alertas: "Bancário — pontos de atenção",
    bancario_arquivos: "Bancário — metadados dos arquivos",
    rif_resumo: "RIF — síntese", rif_comunicacoes: "RIF — comunicações",
    rif_contrapartes: "RIF — contrapartes", rif_envolvidos: "RIF — envolvidos",
    rif_ocorrencias: "RIF — ocorrências", rif_arquivos: "RIF — metadados dos arquivos",
    ipl_resumo: "Inquérito (ePol) — síntese", ipl_pecas: "Inquérito (ePol) — peças",
    ipl_pessoas: "Inquérito (ePol) — pessoas citadas", ipl_achados: "Inquérito (ePol) — achados",
    pix_chaves: "Chaves PIX",
    nuvem_perfil: "Nuvem — perfil da conta", nuvem_dispositivos: "Nuvem — dispositivos",
    nuvem_acessos: "Nuvem — acessos consolidados", iped_extracoes: "IPED — casos importados"
  };

  // por tipo: rótulo do campo de conteúdo, ajuda, e se tem/some com o campo
  var TP_BLOCO_CONFIG = {
    capa: { rotulo: "Linhas da capa (centralizadas) — formato \"Rótulo | valor\", uma por linha", rows: 3 },
    sumario: { semConteudo: true, nota: "Gera um sumário automático (o Word atualiza os números de página ao abrir)." },
    texto: { rotulo: "Conteúdo (use {{variaveis}})", rows: 6 },
    tabela_campos: { rotulo: "Campos — uma linha por campo no formato \"Rótulo | valor\"", rows: 6 },
    extracoes: { rotulo: "Introdução (opcional) — a tabela dos arquivos .ufdr (nome, tamanho, hash) entra abaixo automaticamente", rows: 3 },
    contas: { rotulo: "Introdução (opcional) — a tabela das contas dos aparelhos extraídos entra abaixo automaticamente", rows: 3 },
    whatsapp: { rotulo: "Introdução / observações — as conversas marcadas entram como tabelas coloridas abaixo (verde = investigado, azul = demais)", rows: 4 },
    chat: { rotulo: "Introdução / observações — as conversas marcadas (WhatsApp, SMS e demais mensageiros) entram abaixo como a tela de um celular (verde/direita = investigado, azul/esquerda = demais), com os comentários do investigador depois de cada trecho de conversa", rows: 4 },
    marcacoes: { rotulo: "Introdução (opcional) — os registros marcados entram como tabela abaixo", rows: 3 },
    bancario_contas: { rotulo: "Introdução (opcional) — a tabela das contas afastadas (titular, banco, período, crédito e débito) entra abaixo automaticamente", rows: 3 },
    bancario_contrapartes: { rotulo: "Introdução (opcional) — a tabela das principais contrapartes, somadas por pessoa, entra abaixo automaticamente", rows: 3 },
    bancario_alertas: { rotulo: "Introdução (opcional) — as tabelas de espécie, fracionamento e repasses entre titulares entram abaixo automaticamente", rows: 3 },
    bancario_arquivos: { rotulo: "Introdução (opcional) — a tabela dos arquivos de extrato selecionados (nome, tamanho, hashes MD5/SHA-1/SHA-256) entra abaixo automaticamente", rows: 3 },
    rif_resumo: { rotulo: "Introdução (opcional) — a síntese dos totais (RIFs, comunicações, envolvidos, ocorrências) entra abaixo automaticamente", rows: 3 },
    rif_comunicacoes: { rotulo: "Introdução (opcional) — a tabela das comunicações bancárias (comunicante, valores, agência) entra abaixo automaticamente", rows: 3 },
    rif_contrapartes: { rotulo: "Introdução (opcional) — a tabela das contrapartes consolidadas por CPF/CNPJ entra abaixo automaticamente", rows: 3 },
    rif_envolvidos: { rotulo: "Introdução (opcional) — a tabela dos envolvidos (nome, CPF, tipo, PEP, servidor) entra abaixo automaticamente", rows: 3 },
    rif_ocorrencias: { rotulo: "Introdução (opcional) — os enquadramentos/alertas dos RIFs entram abaixo automaticamente", rows: 3 },
    rif_arquivos: { rotulo: "Introdução (opcional) — a tabela dos arquivos dos RIFs selecionados (nome, tipo, tamanho, hashes MD5/SHA-1/SHA-256) entra abaixo automaticamente", rows: 3 },
    ipl_resumo: { rotulo: "Introdução (opcional) — a síntese dos autos (nº, unidade, autoridade, instauração, tipificação) entra abaixo automaticamente", rows: 3 },
    ipl_pecas: { rotulo: "Introdução (opcional) — a tabela das peças dos autos, com a numeração de folhas, entra abaixo automaticamente", rows: 3 },
    ipl_pessoas: { rotulo: "Introdução (opcional) — a tabela das pessoas citadas nos autos (nome, CPF, vulgo, papel, folha) entra abaixo automaticamente", rows: 3 },
    ipl_achados: { rotulo: "Introdução (opcional) — os achados extraídos do texto dos autos (telefones, contas, valores) entram abaixo em uma tabela por tipo", rows: 3 },
    pix_chaves: { rotulo: "Introdução (opcional) — a tabela das chaves PIX detectadas nas conversas, consolidadas por chave, entra abaixo automaticamente", rows: 3 },
    nuvem_perfil: { rotulo: "Introdução (opcional) — os dados de perfil da conta de nuvem entram abaixo como tabela de campos", rows: 3 },
    nuvem_dispositivos: { rotulo: "Introdução (opcional) — a tabela dos dispositivos conhecidos pela conta (com IMEI) entra abaixo automaticamente", rows: 3 },
    nuvem_acessos: { rotulo: "Introdução (opcional) — a tabela dos acessos consolidados por identificador (período e dias ativos) entra abaixo automaticamente", rows: 3 },
    iped_extracoes: { rotulo: "Introdução (opcional) — a tabela dos casos do IPED importados (pasta, conta, versão, contadores) entra abaixo automaticamente", rows: 3 }
  };

  // ---------- lista de padrões ----------

  function badgeAtivo(ativo) {
    return $("<span>").addClass("badge " + (ativo ? "badge-ok" : "badge-erro")).text(ativo ? "Ativo" : "Inativo");
  }

  function renderPadrao(p) {
    var $card = $("<div>").addClass("investigacao-card");

    var $info = $("<div>").addClass("investigacao-info").appendTo($card);
    var $tituloLinha = $("<div>").css({ display: "flex", alignItems: "center", gap: "10px" }).appendTo($info);
    $("<strong>").text(p.no_padrao).appendTo($tituloLinha);
    $tituloLinha.append(badgeAtivo(p.in_ativo));
    if (p.tx_descricao) $("<span>").text(p.tx_descricao).appendTo($info);
    $("<span>").text(
      (p.qt_blocos || 0) + " bloco(s) · criado por " + (p.criado_por || "—") + " · " + formatarDataHora(p.dt_cadastro)
    ).appendTo($info);

    var $acoes = $("<div>").addClass("investigacao-actions").appendTo($card);
    $("<button>").addClass("btn btn-secondary").text("Editar blocos").on("click", function () { abrirBlocos(p); }).appendTo($acoes);
    $("<button>").addClass("btn btn-secondary").text("Renomear").on("click", function () { abrirModal(p); }).appendTo($acoes);
    $("<button>").addClass("btn btn-secondary").text(p.in_ativo ? "Desativar" : "Ativar")
      .on("click", function () { alterarAtivo(p.id_padrao, !p.in_ativo); }).appendTo($acoes);
    $("<button>").addClass("btn btn-secondary").text("Remover")
      .on("click", function () { removerPadrao(p.id_padrao); }).appendTo($acoes);

    return $card;
  }

  function carregarPadroes() {
    $.getJSON("/api/relatorios/padroes")
      .done(function (lista) {
        $adminCard.removeClass("hidden");
        $semPermissao.addClass("hidden");
        $lista.empty();
        if (!lista.length) {
          $vazio.removeClass("hidden");
          return;
        }
        $vazio.addClass("hidden");
        lista.forEach(function (p) { $lista.append(renderPadrao(p)); });
      })
      .fail(function () {
        $adminCard.addClass("hidden");
        $semPermissao.removeClass("hidden");
      });
  }

  function alterarAtivo(idPadrao, ativo) {
    $.ajax({
      url: "/api/relatorios/padroes/" + idPadrao + "/ativo",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ ativo: ativo })
    }).done(carregarPadroes);
  }

  function removerPadrao(idPadrao) {
    Dialogo.confirmar({ mensagem: "Remover este padrão e todos os seus blocos?", perigoso: true, rotuloOk: "Remover" })
      .then(function (ok) {
        if (ok) $.ajax({ url: "/api/relatorios/padroes/" + idPadrao, method: "DELETE" }).done(carregarPadroes);
      });
  }

  // ---------- modal criar/renomear ----------

  function abrirModal(padrao) {
    idEditando = padrao ? padrao.id_padrao : null;
    $modalTitulo.text(padrao ? "Renomear padrão" : "Novo padrão");
    $nomeInput.val(padrao ? padrao.no_padrao : "");
    $descricaoInput.val(padrao ? (padrao.tx_descricao || "") : "");
    $status.text("").removeClass("success error");
    $modal.removeClass("hidden");
  }

  function fecharModal() { $modal.addClass("hidden"); }

  $("#abrir-novo-padrao-btn").on("click", function () { abrirModal(null); });
  $("#padrao-fechar").on("click", fecharModal);
  $modal.on("click", function (e) { if (e.target === this) fecharModal(); });

  $form.on("submit", function (e) {
    e.preventDefault();
    $status.text("").removeClass("success error");
    $salvarBtn.prop("disabled", true);
    var corpo = JSON.stringify({
      nome: $.trim($nomeInput.val()),
      descricao: $.trim($descricaoInput.val()) || null
    });
    var url = idEditando ? "/api/relatorios/padroes/" + idEditando : "/api/relatorios/padroes";
    $.ajax({ url: url, method: "POST", contentType: "application/json", data: corpo })
      .done(function () {
        carregarPadroes();
        setTimeout(fecharModal, 300);
      })
      .fail(function (xhr) {
        $status.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar o padrão.").addClass("error");
      })
      .always(function () { $salvarBtn.prop("disabled", false); });
  });

  // ---------- construtor de blocos ----------

  function abrirBlocos(padrao) {
    padraoAtual = padrao;
    $blocosTitulo.text(padrao.no_padrao);
    $blocosStatus.text("").removeClass("success error");
    carregarBlocos();
    $modalBlocos.removeClass("hidden");
  }

  function fecharBlocos() {
    $modalBlocos.addClass("hidden");
    padraoAtual = null;
    carregarPadroes(); // atualiza a contagem de blocos na lista
  }

  $("#blocos-fechar").on("click", fecharBlocos);
  $modalBlocos.on("click", function (e) { if (e.target === this) fecharBlocos(); });

  function carregarBlocos() {
    $.getJSON("/api/relatorios/padroes/" + padraoAtual.id_padrao).done(function (padrao) {
      $blocosLista.empty();
      var blocos = padrao.blocos || [];
      if (!blocos.length) {
        $blocosVazio.removeClass("hidden");
        return;
      }
      $blocosVazio.addClass("hidden");
      blocos.forEach(function (b, i) {
        $blocosLista.append(renderBloco(b, i === 0, i === blocos.length - 1));
      });
    });
  }

  function renderBloco(b, primeiro, ultimo) {
    var $card = $("<div>").addClass("card").css({ margin: "0 0 12px", padding: "14px", maxWidth: "none" });

    var $cab = $("<div>").css({ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }).appendTo($card);
    $("<span>").addClass("badge " + (b.tp_bloco === "marcacoes" ? "badge-rodando" : "badge-neutro")).text(TP_BLOCO_ROTULO[b.tp_bloco] || b.tp_bloco).appendTo($cab);
    var $espaco = $("<span>").css("flex", "1").appendTo($cab);
    $("<button>").addClass("btn btn-secondary").text("↑").attr("title", "Mover para cima").prop("disabled", primeiro)
      .on("click", function () { moverBloco(b.id_bloco, "cima"); }).appendTo($cab);
    $("<button>").addClass("btn btn-secondary").text("↓").attr("title", "Mover para baixo").prop("disabled", ultimo)
      .on("click", function () { moverBloco(b.id_bloco, "baixo"); }).appendTo($cab);
    $("<button>").addClass("btn btn-secondary").text("Remover")
      .on("click", function () { removerBloco(b.id_bloco); }).appendTo($cab);

    var cfg = TP_BLOCO_CONFIG[b.tp_bloco] || TP_BLOCO_CONFIG.texto;

    var $tituloField = $("<div>").addClass("field").appendTo($card);
    $("<label>").text(b.tp_bloco === "capa" ? "Título do relatório (centralizado)" : "Título da seção (opcional)").appendTo($tituloField);
    var $titulo = $("<input>").attr("type", "text").val(b.no_titulo || "").appendTo($tituloField);

    var $conteudo = $("<textarea>"); // referenciado no salvar mesmo quando escondido
    if (cfg.semConteudo) {
      $conteudo.val(b.tx_conteudo || "");
      $("<p>").addClass("field-help").text(cfg.nota).appendTo($card);
    } else {
      var $conteudoField = $("<div>").addClass("field").appendTo($card);
      $("<label>").text(cfg.rotulo).appendTo($conteudoField);
      $conteudo.attr("rows", cfg.rows)
        .css({ width: "100%", padding: "10px 12px", borderRadius: "8px", border: "1px solid var(--border)", background: "var(--bg-page)", color: "var(--text-primary)", fontSize: "14px", fontFamily: "inherit", resize: "vertical" })
        .val(b.tx_conteudo || "").appendTo($conteudoField);
    }

    var $acoes = $("<div>").addClass("form-actions").appendTo($card);
    var $salvarBloco = $("<button>").addClass("btn btn-primary").text("Salvar bloco").appendTo($acoes);
    var $st = $("<span>").addClass("form-status").appendTo($acoes);
    $salvarBloco.on("click", function () {
      $st.text("Salvando...").removeClass("success error");
      $salvarBloco.prop("disabled", true);
      $.ajax({
        url: "/api/relatorios/blocos/" + b.id_bloco,
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ tp_bloco: b.tp_bloco, titulo: $.trim($titulo.val()) || null, conteudo: $conteudo.val() || null })
      })
        .done(function () { $st.text("Salvo.").addClass("success"); })
        .fail(function (xhr) { $st.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.").addClass("error"); })
        .always(function () { $salvarBloco.prop("disabled", false); });
    });

    return $card;
  }

  function adicionarBloco(tpBloco) {
    $blocosStatus.text("").removeClass("success error");
    $.ajax({
      url: "/api/relatorios/padroes/" + padraoAtual.id_padrao + "/blocos",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ tp_bloco: tpBloco, titulo: null, conteudo: null })
    })
      .done(carregarBlocos)
      .fail(function (xhr) { $blocosStatus.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao adicionar bloco.").addClass("error"); });
  }

  function moverBloco(idBloco, direcao) {
    $.ajax({
      url: "/api/relatorios/blocos/" + idBloco + "/mover",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ direcao: direcao })
    }).done(carregarBlocos);
  }

  function removerBloco(idBloco) {
    $.ajax({ url: "/api/relatorios/blocos/" + idBloco, method: "DELETE" }).done(carregarBlocos);
  }

  $("[data-add-bloco]").on("click", function () { adicionarBloco($(this).attr("data-add-bloco")); });

  // painel de ajuda com as variáveis automáticas disponíveis
  $.getJSON("/api/relatorios/variaveis").done(function (vars) {
    var tokens = (vars || []).map(function (v) { return "{{" + v.token + "}}"; }).join(", ");
    $variaveisAjuda.html("Variáveis automáticas: <code>" + tokens + "</code>. Qualquer outra <code>{{variavel}}</code> que você usar será pedida na hora de gerar o relatório.");
  });

  carregarPadroes();
})();
