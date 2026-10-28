/* Tela "Buscar entidades no material do inquérito".
 *
 * Cada linha é um termo único — nome, CPF/CNPJ, telefone ou hash — tipificado por regex antes
 * de virar o campo certo no corpo da requisição (ver classificarTermo/entidadeDeTermo).
 *
 * Monta a lista de pessoas/empresas procuradas, chama
 * POST /api/investigacao/inqueritos/{id}/buscar-entidades e apresenta o que voltou agrupado por
 * entidade e por fonte. A tela não sabe quais são as fontes: quem decide é o registro do
 * back-end (api/funcoes/busca_entidades_utils.py), e os grupos disponíveis vêm da rota
 * .../buscar-entidades/fontes a cada troca de inquérito. */
(function () {
  "use strict";

  var $inqueritoSelect = $("#inquerito-select");
  var $entidadesLista = $("#entidades-lista");
  var $gruposLista = $("#grupos-lista");
  var $gruposVazio = $("#grupos-vazio");
  var $adicionarBtn = $("#adicionar-entidade-btn");
  var $colarBtn = $("#colar-lista-btn");
  var $buscarBtn = $("#buscar-btn");
  var $aproximada = $("#aproximada-check");
  var $soFortes = $("#so-fortes-check");
  var $status = $("#buscar-status");
  var $resultadosCard = $("#resultados-card");
  var $resultadosConteudo = $("#resultados-conteudo");

  var esc = window.AnalyserUtils.esc;
  var formatarNumero = window.AnalyserUtils.formatarNumero;
  var nomeArquivo = window.AnalyserUtils.nomeArquivo;

  // Última resposta do servidor, guardada para o filtro "só correspondências fortes" poder
  // redesenhar sem repetir a busca.
  var ultimaResposta = null;

  // Rótulo e peso de cada critério. Documento e telefone são identificadores — quando batem, o
  // achado é certo; nome parecido e menção no texto são pistas a conferir.
  var CRITERIOS = {
    hash: { rotulo: "hash", classe: "forte", forte: true },
    documento: { rotulo: "CPF/CNPJ", classe: "forte", forte: true },
    telefone: { rotulo: "telefone", classe: "forte", forte: true },
    nome: { rotulo: "nome exato", classe: "forte", forte: true },
    nome_aproximado: { rotulo: "nome parecido", classe: "media", forte: false },
    texto: { rotulo: "citado no texto", classe: "", forte: false }
  };

  // ------------------------------------------------- termo único: classificação por regex
  //
  // Um campo só por linha; o tipo é deduzido do que foi digitado. A ordem importa:
  // hash antes de contar dígitos (40/64 dígitos são antes de tudo hex), CPF/CNPJ (11/14
  // dígitos) antes de telefone — o mesmo "12345678901" nu é mais provável CPF que celular,
  // e quem digita telefone quase sempre usa máscara, "+55" ou espaços.

  var RE_HASH = /^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$/i;
  var RE_DOC_MASCARA = /^\d{3}\.\d{3}\.\d{3}[.-]?\d{2}$|^\d{2}\.\d{3}\.\d{3}\/\d{4}[.-]?\d{2}$/;
  var RE_SO_SEPARADORES = /^[\d()+\-\s.]+$/;

  var ROTULOS_TIPO = {
    hash: "hash", documento: "CPF/CNPJ", telefone: "telefone", nome: "nome", varios: "lista"
  };

  function classificarTermo(termo) {
    var t = $.trim(termo || "");
    if (!t) return null;
    if (RE_HASH.test(t)) return "hash";
    if (RE_DOC_MASCARA.test(t)) return "documento";
    if (RE_SO_SEPARADORES.test(t)) {
      var digitos = t.replace(/\D/g, "");
      if (digitos.length === 11 || digitos.length === 14) return "documento";
      if (digitos.length >= 8 && digitos.length <= 13) return "telefone";
    }
    return "nome";
  }

  // Tipo detectado → campo do corpo da requisição que o motor lê ("documento" é o nome interno
  // do critério; no payload o campo se chama cpf_cnpj). Cada tipo preenche só o seu campo: o
  // motor casa documento por dígitos, telefone por sufixo e hash por igualdade em hex.
  var CAMPO_DO_TIPO = { hash: "hash", documento: "cpf_cnpj", telefone: "telefone", nome: "nome" };

  /* Vira o item do corpo da requisição. */
  function entidadeDeTermo(termo) {
    var t = $.trim(termo || "");
    var tipo = classificarTermo(t);
    if (!tipo) return null;
    var entidade = { nome: "", cpf_cnpj: null, telefone: null, hash: null };
    entidade[CAMPO_DO_TIPO[tipo]] = t;
    return entidade;
  }

  function atualizarBadge($linha) {
    var tipo = $linha.data("varios") ? "varios" : classificarTermo($linha.find(".ent-termo").val());
    var $badge = $linha.find(".ent-tipo");
    $badge.toggleClass("hidden", !tipo)
      .removeClass("tipo-hash tipo-documento tipo-telefone tipo-nome tipo-varios")
      .addClass("tipo-" + tipo)
      .text(ROTULOS_TIPO[tipo] || "");
  }

  function criarLinhaEntidade(valores) {
    valores = valores || {};
    var $linha = $(
      '<div class="entidade-linha" style="display:flex; gap:8px; align-items:end; margin-bottom:8px;">' +
        '<div class="field" style="flex:1;">' +
          '<label>Termo de busca</label>' +
          '<input type="text" class="ent-termo" autocomplete="off" spellcheck="false" ' +
            'placeholder="Nome, CPF/CNPJ, telefone ou hash (MD5/SHA-1/SHA-256)">' +
        '</div>' +
        '<span class="be-tipo-badge ent-tipo hidden" aria-live="polite"></span>' +
        '<input type="hidden" class="ent-extra-nome"><input type="hidden" class="ent-extra-doc">' +
        '<input type="hidden" class="ent-extra-fone"><input type="hidden" class="ent-extra-hash">' +
        '<button type="button" class="btn btn-danger ent-remover" style="margin-bottom:8px; padding:6px 10px;" title="Remover esta linha">×</button>' +
      '</div>'
    );
    $linha.find(".ent-termo").val(valores.termo || "");
    if (valores.nome) $linha.find(".ent-extra-nome").val(valores.nome);
    if (valores.cpf_cnpj) $linha.find(".ent-extra-doc").val(valores.cpf_cnpj);
    if (valores.telefone) $linha.find(".ent-extra-fone").val(valores.telefone);
    if (valores.hash) $linha.find(".ent-extra-hash").val(valores.hash);
    if (valores.nome || valores.cpf_cnpj || valores.telefone || valores.hash) $linha.data("varios", true);
    // Quem digita de novo abandona os campos extras da lista colada: a linha volta a ser um
    // termo único, classificado pelo que está escrito.
    $linha.find(".ent-termo").on("input", function () {
      $linha.removeData("varios");
      $linha.find("input[type=hidden]").val("");
      atualizarBadge($linha);
    });
    $linha.find(".ent-remover").on("click", function () {
      $linha.remove();
      if (!$entidadesLista.children().length) $entidadesLista.append(criarLinhaEntidade());
    });
    atualizarBadge($linha);
    return $linha;
  }

  function coletarEntidades() {
    var entidades = [];
    $entidadesLista.find(".entidade-linha").each(function () {
      var $linha = $(this);
      var termo = $.trim($linha.find(".ent-termo").val());
      // Linha de lista colada ("nome;CPF;telefone"): os campos vêm dos escondidos, e o que está
      // escrito é só a representação da linha original.
      var entidade = null;
      if ($linha.data("varios")) {
        var nome = $.trim($linha.find(".ent-extra-nome").val());
        var doc = $.trim($linha.find(".ent-extra-doc").val());
        var fone = $.trim($linha.find(".ent-extra-fone").val());
        var hash = $.trim($linha.find(".ent-extra-hash").val());
        if (nome || doc || fone || hash) {
          entidade = { nome: nome, cpf_cnpj: doc || null, telefone: fone || null, hash: hash || null };
        }
      }
      if (!entidade) entidade = entidadeDeTermo(termo);
      // Linha vazia é só uma linha ainda não preenchida, não um erro.
      if (entidade) entidades.push(entidade);
    });
    return entidades;
  }

  /* Um termo por linha (nome, CPF/CNPJ, telefone ou hash). A grafia antiga de planilha continua
   * valendo — "nome; CPF; telefone" na mesma linha vira uma entidade só, com cada parte
   * reclassificada por regex; o separador pode ser ";" ou tabulação. */
  function interpretarLista(texto) {
    return String(texto || "").split(/\r?\n/).map(function (linha) {
      var partes = linha.split(/[;\t]/);
      var item = { termo: $.trim(linha) };
      partes.forEach(function (parte) {
        var entidade = entidadeDeTermo(parte);
        if (!entidade) return;
        if (entidade.nome) item.nome = entidade.nome;
        else if (entidade.cpf_cnpj) item.cpf_cnpj = entidade.cpf_cnpj;
        else if (entidade.telefone) item.telefone = entidade.telefone;
        else if (entidade.hash) item.hash = entidade.hash;
      });
      return item;
    }).filter(function (item) {
      return item.nome || item.cpf_cnpj || item.telefone || item.hash;
    });
  }

  function colarLista() {
    window.Dialogo.prompt({
      titulo: "Colar lista de termos de busca",
      mensagem: "Um termo por linha — nome, CPF/CNPJ, telefone ou hash, reconhecidos " +
                "automaticamente. Para juntar nome, documento e telefone na MESMA entidade, " +
                "separe com \";\" — por exemplo: JOSE DA SILVA;123.456.789-09;(27) 99999-1234",
      multilinha: true,
      placeholder: "JOSE DA SILVA\nMARIA SOUZA;123.456.789-09\n(27) 99999-1234\n8f4a1c… (MD5/SHA-256)",
      rotuloOk: "Adicionar"
    }).then(function (texto) {
      if (texto == null) return;
      var itens = interpretarLista(texto);
      if (!itens.length) return;
      // Linhas em branco do formulário saem: quem colou uma lista quer ver a lista, não os
      // campos vazios que estavam ali antes.
      $entidadesLista.find(".entidade-linha").each(function () {
        var $linha = $(this);
        if (!$.trim($linha.find(".ent-termo").val()) && !$linha.data("varios")) $linha.remove();
      });
      itens.forEach(function (item) { $entidadesLista.append(criarLinhaEntidade(item)); });
    });
  }

  // ---------------------------------------------------------------- inquérito e fontes

  var inqueritoPrefill = window.AnalyserUtils.paramUrl("inquerito");
  // ?q= (busca rápida do card em investigacao.html): o termo já entra na primeira linha e a
  // busca dispara sozinha assim que as fontes do inquérito carregarem.
  var termoPrefill = window.AnalyserUtils.paramUrl("q");
  var autoBuscar = !!termoPrefill;

  function carregarInqueritos() {
    $.getJSON("/api/investigacao/inqueritos").done(function (lista) {
      $inqueritoSelect.empty();
      if (!lista.length) {
        $inqueritoSelect.append($("<option>").val("").text("Nenhum inquérito disponível"));
        $buscarBtn.prop("disabled", true);
        return;
      }
      $buscarBtn.prop("disabled", false);
      var selecionou = false;
      lista.forEach(function (inq) {
        var $opt = $("<option>").val(inq.id_inquerito).text(inq.nu_inquerito);
        if (inqueritoPrefill && !selecionou &&
            (inq.nu_inquerito === inqueritoPrefill || String(inq.id_inquerito) === inqueritoPrefill)) {
          $opt.prop("selected", true);
          selecionou = true;
        }
        $opt.appendTo($inqueritoSelect);
      });
      carregarGrupos();
    });
  }

  function carregarGrupos() {
    var idInquerito = $inqueritoSelect.val();
    $gruposLista.empty();
    if (!idInquerito) return;
    $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/buscar-entidades/fontes")
      .done(function (resp) {
        var algum = false;
        (resp.grupos || []).forEach(function (grupo) {
          ROTULOS_GRUPO[grupo.chave] = grupo.rotulo;
          if (grupo.disponivel) algum = true;
          var fontes = (grupo.fontes || []).map(function (f) { return f.rotulo; }).join("\n");
          var $pilula = $('<label class="be-grupo">')
            .attr("title", grupo.disponivel
              ? "Procura em:\n" + fontes
              : "Nada deste tipo foi importado neste inquérito.")
            .toggleClass("indisponivel", !grupo.disponivel);
          $('<input type="checkbox" class="grupo-check">')
            .val(grupo.chave)
            .prop("checked", grupo.disponivel)
            .prop("disabled", !grupo.disponivel)
            .appendTo($pilula);
          $("<span>").text(grupo.rotulo).appendTo($pilula);
          $pilula.appendTo($gruposLista);
        });
        $gruposVazio.toggle(!algum);
        $buscarBtn.prop("disabled", !algum);
        if (autoBuscar && algum) {
          autoBuscar = false;
          fazerBusca();
        }
      })
      .fail(function () {
        $gruposVazio.text("Não foi possível carregar as fontes deste inquérito.").show();
      });
  }

  function gruposSelecionados() {
    return $gruposLista.find(".grupo-check:checked").map(function () {
      return $(this).val();
    }).get();
  }

  // ---------------------------------------------------------------- busca

  function setStatus(texto, tipo) {
    $status.text(texto).removeClass("success error").addClass(tipo || "");
  }

  function fazerBusca() {
    var idInquerito = $inqueritoSelect.val();
    if (!idInquerito) {
      setStatus("Selecione um inquérito.", "error");
      return;
    }

    var entidades = coletarEntidades();
    if (!entidades.length) {
      setStatus("Informe ao menos um termo: nome, CPF/CNPJ, telefone ou hash.", "error");
      return;
    }

    var grupos = gruposSelecionados();
    if (!grupos.length) {
      setStatus("Escolha ao menos um lugar onde procurar.", "error");
      return;
    }

    var aproximada = $aproximada.is(":checked");
    setStatus(aproximada
      ? "Buscando… a busca aproximada varre o texto inteiro e pode levar alguns minutos."
      : "Buscando…", "");
    $buscarBtn.prop("disabled", true);

    $.ajax({
      url: "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/buscar-entidades",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ entidades: entidades, aproximada: aproximada, grupos: grupos })
    })
      .done(function (resp) {
        ultimaResposta = resp;
        renderResultados();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao buscar entidades.";
        setStatus(msg, "error");
      })
      .always(function () {
        $buscarBtn.prop("disabled", false);
      });
  }

  // ---------------------------------------------------------------- resultado

  function fortes(achado) {
    return (achado.criterios || []).some(function (c) {
      return CRITERIOS[c] && CRITERIOS[c].forte;
    });
  }

  function $criterios(achado) {
    var $caixa = $("<div>");
    (achado.criterios || []).forEach(function (chave) {
      var info = CRITERIOS[chave] || { rotulo: chave, classe: "" };
      $("<span>").addClass("be-criterio " + info.classe).text(info.rotulo).appendTo($caixa);
    });
    return $caixa;
  }

  /* O trecho vem do servidor com a menção já localizada (`marca`); aqui ele só é escapado e a
   * ocorrência destacada. Sem escapar antes, um "<script>" dentro de uma mensagem do WhatsApp
   * entraria na página. */
  function htmlTrecho(trecho) {
    if (!trecho || !trecho.texto) return "";
    var texto = esc(trecho.texto);
    var marca = esc(trecho.marca || "");
    if (marca) {
      var padrao = new RegExp(marca.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      texto = texto.replace(padrao, function (achado) { return "<mark>" + achado + "</mark>"; });
    }
    return texto;
  }

  function $tabelaFonte(bloco, soFortes) {
    var achados = soFortes ? bloco.achados.filter(fortes) : bloco.achados;
    if (!achados.length) return null;

    var temTrecho = achados.some(function (a) { return a.trecho && a.trecho.texto; });
    var temLink = achados.some(function (a) { return a.link; });
    var $caixa = $('<div class="be-tabela-caixa">');
    var $tabela = $('<table class="be-tabela">').appendTo($caixa);
    var $cab = $("<tr>").appendTo($("<thead>").appendTo($tabela));
    ["Registro", "Onde", "Casou por"].forEach(function (titulo) {
      $("<th>").text(titulo).appendTo($cab);
    });
    if (temLink) $("<th>").text("Abrir").appendTo($cab);
    if (temTrecho) $("<th>").text("Trecho").appendTo($cab);

    var $corpo = $("<tbody>").appendTo($tabela);
    achados.forEach(function (achado) {
      var $linha = $("<tr>").appendTo($corpo);

      var $registro = $("<td>").appendTo($linha);
      $('<div class="be-registro">').text(achado.titulo || "—").appendTo($registro);
      if (achado.detalhe) $('<div class="be-detalhe">').text(achado.detalhe).appendTo($registro);

      var onde = [];
      // NO_PASTA (RIF) e NO_ARQUIVO (SIMBA/documentos) são caminhos relativos a data/ — na
      // tabela só o nome interessa (ver docs/organizacao_arquivos.md).
      if (achado.local) onde.push(nomeArquivo(String(achado.local)));
      if (achado.data) onde.push(window.AnalyserUtils.formatarData(achado.data));
      $('<td class="be-onde">').text(onde.join(" · ") || "—").appendTo($linha);

      $("<td>").append($criterios(achado)).appendTo($linha);

      if (temLink) {
        $('<td class="be-abrir">').append(
          achado.link
            ? $("<a>").addClass("be-link")
              .attr("href", achado.link)
              .attr("title", "Abrir a evidência onde este registro está")
              .html('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>')
            : null
        ).appendTo($linha);
      }

      if (temTrecho) {
        $('<td class="be-trecho">').html(htmlTrecho(achado.trecho)).appendTo($linha);
      }
    });
    return $caixa;
  }

  function renderResultados() {
    var resp = ultimaResposta;
    $resultadosConteudo.empty();
    if (!resp) {
      $resultadosCard.hide();
      return;
    }
    $resultadosCard.show();

    var soFortes = $soFortes.is(":checked");
    var totalGeral = 0;

    (resp.resultados || []).forEach(function (resultado) {
      var $bloco = $('<div class="be-entidade">').appendTo($resultadosConteudo);
      var identidade = [resultado.cpf_cnpj, resultado.telefone, resultado.hash]
        .filter(Boolean).join(" · ");

      $('<div class="be-entidade-titulo">')
        .text(resultado.nome || identidade || "(entidade sem nome)")
        .appendTo($bloco);

      var fontesComAchado = (resultado.fontes || []).filter(function (bloco) {
        return !soFortes || bloco.achados.some(fortes);
      });
      var exibidos = fontesComAchado.reduce(function (soma, bloco) {
        return soma + (soFortes ? bloco.achados.filter(fortes).length : bloco.achados.length);
      }, 0);
      totalGeral += exibidos;

      var sub = [];
      if (resultado.nome && identidade) sub.push(identidade);
      sub.push(exibidos
        ? formatarNumero(soFortes ? exibidos : resultado.qt_achados) + " menção(ões) em " +
          formatarNumero(fontesComAchado.length) + " fonte(s)"
        : "nenhuma menção encontrada");
      $('<div class="be-entidade-sub">').text(sub.join(" · ")).appendTo($bloco);

      if (!fontesComAchado.length) {
        $('<div class="be-vazio">')
          .text(soFortes
            ? "Nada com correspondência forte. Desmarque o filtro para ver as pistas."
            : "Nada encontrado nas fontes consultadas.")
          .appendTo($bloco);
        return;
      }

      // Um resumo por grupo antes das tabelas: é o que responde "apareceu no RIF?" sem abrir nada.
      var porGrupo = {};
      fontesComAchado.forEach(function (item) {
        var qt = soFortes ? item.achados.filter(fortes).length : item.total;
        porGrupo[item.grupo] = (porGrupo[item.grupo] || 0) + qt;
      });
      var $resumo = $('<div class="be-resumo">').appendTo($bloco);
      Object.keys(porGrupo).forEach(function (grupo) {
        $('<span class="be-chip">')
          .text(rotuloGrupo(grupo) + ": " + formatarNumero(porGrupo[grupo]))
          .appendTo($resumo);
      });

      fontesComAchado.forEach(function (item, indice) {
        var $tabela = $tabelaFonte(item, soFortes);
        if (!$tabela) return;
        var mostrados = soFortes ? item.achados.filter(fortes).length : item.achados.length;
        var $detalhes = $('<details class="be-fonte">')
          // As três primeiras já vêm abertas: com dez fontes, abrir uma a uma para descobrir
          // onde está o achado seria a mesma varredura manual que a tela veio evitar.
          .prop("open", indice < 3)
          .appendTo($bloco);
        var contagem = item.total > mostrados
          ? " — " + formatarNumero(mostrados) + " de " + formatarNumero(item.total)
          : " — " + formatarNumero(mostrados);
        $("<summary>")
          .append($("<strong>").text(item.rotulo))
          .append($('<span class="be-fonte-contagem">').text(contagem))
          .appendTo($detalhes);
        $detalhes.append($tabela);
      });
    });

    var avisos = [];
    if (!resp.aproximada) avisos.push("busca exata (aproximadas desligadas)");
    if ((resp.fontes_com_erro || []).length) {
      avisos.push("não foi possível consultar: " + resp.fontes_com_erro.join(", "));
    }
    setStatus(
      formatarNumero(totalGeral) + " menção(ões) exibida(s) para " +
      formatarNumero((resp.resultados || []).length) + " entidade(s) pesquisada(s)" +
      (avisos.length ? " · " + avisos.join(" · ") : ""),
      (resp.fontes_com_erro || []).length ? "error" : "success"
    );
  }

  // Os rótulos vêm da rota de fontes; este é o mapa de fallback para quando o resultado chega
  // antes de a lista de grupos ter sido carregada (troca de inquérito com busca em andamento).
  var ROTULOS_GRUPO = {
    rif: "RIF (COAF)", simba: "Extrato bancário (SIMBA)", ipl: "Inquérito em PDF",
    irpf: "Declaração de IRPF", ipei: "IPEI (patrimonial)", telefonia: "Telefonia (operadora)",
    documentos: "Documentos anexados", celular: "Celular / extração",
    nuvem: "Contas na nuvem", analise: "Análise do caso"
  };

  function rotuloGrupo(chave) {
    return ROTULOS_GRUPO[chave] || chave;
  }

  // ---------------------------------------------------------------- eventos

  $adicionarBtn.on("click", function () { $entidadesLista.append(criarLinhaEntidade()); });
  $colarBtn.on("click", colarLista);
  $buscarBtn.on("click", fazerBusca);
  $inqueritoSelect.on("change", carregarGrupos);
  $soFortes.on("change", renderResultados);

  $entidadesLista.on("keydown", "input", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      fazerBusca();
    }
  });

  $entidadesLista.append(criarLinhaEntidade(termoPrefill ? { termo: termoPrefill } : null));
  carregarInqueritos();
})();
