/* ============================================================================
   Camadas geoespaciais (shapefile) sobre o mapa da aba "Grafo" das localizações.

   O analista traz de fora um .shp com polígonos — perímetro de operação, limite de bairro, setor
   censitário, área de risco — e ele passa a valer como fundo do mapa. Além de desenhar, a camada
   responde duas perguntas em cima dos registros do aparelho: quantas localizações caíram dentro
   de cada área, e "me mostre só o que está dentro desta aqui".

   Shapefile não é um arquivo só: .shp (geometria) + .dbf (atributos) + .prj (projeção) + .shx
   andam juntos, e é comum circularem num .zip. A importação aceita as duas formas; quem lê,
   reprojeta para lat/lon e grava é a API (api/funcoes/geoespacial_utils.py) — daqui para cá tudo
   já chega em WGS 84, no mesmo referencial dos pontos da extração.

   O recorte por área não é um estado só do mapa: ele entra no <select> #filtro-area, que é lido
   por js/localizacoes.js junto com os demais filtros. Assim a tabela e o grafo continuam
   mostrando exatamente o mesmo conjunto, que é a regra da tela.
   ============================================================================ */
(function () {
  "use strict";

  var $botao = $("#camada-importar");
  var $barra = $("#loc-camadas");
  var $select = $("#filtro-area");

  // sem checagem de window.L: o Leaflet agora entra sob demanda na primeira abertura da aba
  // Grafo (js/localizacoes_grafo.js) e este módulo só toca em L depois que usarMapa() roda
  if (!$botao.length || !window.LocalizacoesFiltros) return;

  var idInquerito = LocalizacoesFiltros.idInquerito;
  var BASE = "/api/investigacao/inqueritos/" + idInquerito + "/localizacoes/camadas";

  // Pane próprio, abaixo do overlayPane (400): os polígonos são fundo de leitura e não podem
  // cobrir as arestas de deslocamento nem os círculos dos lugares.
  var PANE = "loc-camadas-shp";
  var Z_PANE = 350;

  var mapa = null;
  var camadas = [];        // metadados de cada camada, como vieram da API
  var desenhos = {};       // id_camada -> { layer, geojson, contagens, total, carregando }
  var visiveis = {};       // id_camada -> bool (o que está ligado agora)
  var timerContagem = null;

  function escapar(texto) {
    return AnalyserUtils.escaparHtml(texto);
  }

  function numero(n) {
    return AnalyserUtils.formatarNumero(n || 0);
  }

  function detalheErro(xhr, padrao) {
    return (xhr && xhr.responseJSON && xhr.responseJSON.detail) || padrao;
  }

  /* Os filtros que valem para a contagem por área são os mesmos da tela MENOS o próprio recorte
     por área — senão a resposta seria "dentro desta área, quantos estão dentro desta área". */
  function filtrosBase() {
    var p = LocalizacoesFiltros.params();
    delete p.camada;
    delete p.feicao;
    return p;
  }

  // ---------------------------------------------------------------- lista de camadas

  function carregar() {
    $.getJSON(BASE).done(function (lista) {
      camadas = lista || [];
      camadas.forEach(function (c) {
        // camada nova entra ligada; o que o usuário desligou continua desligado
        if (visiveis[c.id_camada] === undefined) visiveis[c.id_camada] = true;
      });
      renderBarra();
      renderSelect();
      sincronizarDesenho();
    }).fail(function () {
      // falha de rede não pode sumir com a barra em silêncio: quem tem camada importada e não a
      // vê precisa saber que foi o servidor, não que a camada deixou de existir
      $barra.removeClass("hidden");
      Estados.erro($barra, {
        mensagem: "Não foi possível listar as áreas importadas.",
        aoTentarDeNovo: carregar,
      });
    });
  }

  function porId(idCamada) {
    for (var i = 0; i < camadas.length; i++) {
      if (String(camadas[i].id_camada) === String(idCamada)) return camadas[i];
    }
    return null;
  }

  function renderBarra() {
    $barra.empty().toggleClass("hidden", camadas.length === 0);
    if (!camadas.length) return;

    $('<span class="loc-camadas-rotulo">').text("Áreas importadas:").appendTo($barra);

    camadas.forEach(function (camada) {
      var estado = desenhos[camada.id_camada] || {};
      var $chip = $('<div class="loc-camada">').attr("data-camada", camada.id_camada);

      var $rotulo = $('<label class="loc-camada-toggle">').appendTo($chip);
      $('<input type="checkbox">')
        .prop("checked", !!visiveis[camada.id_camada])
        .on("change", function () {
          visiveis[camada.id_camada] = $(this).is(":checked");
          sincronizarDesenho();
          renderBarra();
        })
        .appendTo($rotulo);
      $('<span class="loc-camada-cor">').css("background", camada.co_cor).appendTo($rotulo);
      $("<span>").text(camada.no_camada).appendTo($rotulo);

      var info = numero(camada.qt_feicoes) + " área(s)";
      if (estado.total !== undefined && estado.total !== null) {
        info += " · " + numero(estado.total) + " registro(s) dentro";
      } else if (visiveis[camada.id_camada]) {
        info += " · contando…";
      }
      $('<span class="loc-camada-info">').text(info).attr("title", tituloCamada(camada)).appendTo($chip);

      [
        { acao: "enquadrar", simbolo: "⤢", titulo: "Enquadrar o mapa nesta camada" },
        { acao: "renomear", simbolo: "✎", titulo: "Renomear a camada" },
        { acao: "apagar", simbolo: "×", titulo: "Apagar a camada" },
      ].forEach(function (item) {
        $('<button type="button" class="loc-camada-acao">')
          .attr("title", item.titulo)
          .attr("aria-label", item.titulo)
          .text(item.simbolo)
          .on("click", function () { acaoCamada(item.acao, camada); })
          .appendTo($chip);
      });

      $chip.appendTo($barra);
    });
  }

  function tituloCamada(camada) {
    var linhas = [
      camada.no_arquivo || "",
      "Projeção de origem: " + (camada.no_projecao || "não informada no .prj"),
      numero(camada.qt_vertices) + " vértices",
    ];
    // a simplificação é sempre menor que o pixel do zoom máximo, mas quem examina tem de saber
    // que a geometria desenhada não é a do arquivo vértice a vértice
    if (camada.vl_tolerancia) {
      linhas.push("Simplificada em ~" + Math.round(camada.vl_tolerancia * 111000) + " m");
    }
    if (camada.no_importada_por) linhas.push("Importada por " + camada.no_importada_por);
    return linhas.filter(Boolean).join("\n");
  }

  /* O <select> de área lista a camada inteira e, quando ela tem poucas áreas, cada uma delas.
     Acima do teto a lista viraria um paredão de opções — aí o caminho é clicar no polígono no
     mapa, que oferece o mesmo recorte no popup. */
  var MAX_FEICOES_NO_SELECT = 150;

  function renderSelect() {
    if (!$select.length) return;
    var atual = $select.val();
    $select.empty().append($("<option>").attr("value", "").text("Qualquer área"));

    camadas.forEach(function (camada) {
      $("<option>")
        .attr("value", "camada:" + camada.id_camada)
        .text("Dentro de: " + camada.no_camada)
        .appendTo($select);

      var estado = desenhos[camada.id_camada];
      if (!estado || !estado.geojson || camada.qt_feicoes > MAX_FEICOES_NO_SELECT) return;

      var $grupo = $("<optgroup>").attr("label", camada.no_camada).appendTo($select);
      estado.geojson.features.forEach(function (f) {
        $("<option>")
          .attr("value", "feicao:" + f.properties.id_feicao)
          .text("— " + (f.properties.no_feicao || "área " + f.properties.nu_ordem))
          .appendTo($grupo);
      });
    });

    $select.toggleClass("hidden", camadas.length === 0);
    // se a opção que estava escolhida ainda existe, mantém; senão volta para "qualquer área"
    if (atual && $select.find('option[value="' + atual.replace(/"/g, '\\"') + '"]').length) {
      $select.val(atual);
    } else if (atual) {
      LocalizacoesFiltros.area("");
    }
  }

  // ---------------------------------------------------------------- desenho no mapa

  function usarMapa(m) {
    mapa = m;
    if (!mapa.getPane(PANE)) {
      mapa.createPane(PANE);
      mapa.getPane(PANE).style.zIndex = Z_PANE;
    }
    sincronizarDesenho();
  }

  function sincronizarDesenho() {
    if (!mapa) return;
    camadas.forEach(function (camada) {
      if (visiveis[camada.id_camada]) desenhar(camada);
      else esconder(camada.id_camada);
    });
    Object.keys(desenhos).forEach(function (id) {
      if (!porId(id)) {                    // camada apagada: tira o desenho junto
        esconder(id);
        delete desenhos[id];
      }
    });
    agendarContagem();
  }

  function esconder(idCamada) {
    var estado = desenhos[idCamada];
    if (estado && estado.layer && mapa && mapa.hasLayer(estado.layer)) {
      mapa.removeLayer(estado.layer);
    }
  }

  function desenhar(camada) {
    var estado = desenhos[camada.id_camada];
    if (estado && estado.layer) {
      if (!mapa.hasLayer(estado.layer)) estado.layer.addTo(mapa);
      return;
    }
    if (estado && estado.carregando) return;

    desenhos[camada.id_camada] = { carregando: true };
    $.getJSON(BASE + "/" + camada.id_camada).done(function (geojson) {
      var layer = L.geoJSON(geojson, {
        pane: PANE,
        style: function () { return estiloFeicao(camada, false); },
        onEachFeature: function (feature, camadaFeicao) {
          camadaFeicao.bindTooltip(function () { return tooltipFeicao(camada, feature); }, { sticky: true });
          camadaFeicao.bindPopup(function () { return popupFeicao(camada, feature); }, { maxWidth: 320 });
        },
      });
      desenhos[camada.id_camada] = { layer: layer, geojson: geojson, carregando: false };
      if (visiveis[camada.id_camada] && mapa) layer.addTo(mapa);
      renderSelect();
      aplicarDestaque();
      agendarContagem();
    }).fail(function (xhr) {
      delete desenhos[camada.id_camada];
      Toast.erro(detalheErro(xhr, "Não foi possível desenhar a camada “" + camada.no_camada + "”."));
    });
  }

  function estiloFeicao(camada, destacada) {
    return {
      color: camada.co_cor,
      weight: destacada ? 3 : 1.5,
      opacity: destacada ? 1 : 0.85,
      fillColor: camada.co_cor,
      fillOpacity: destacada ? 0.28 : 0.12,
    };
  }

  /* O polígono que está servindo de recorte fica mais forte que os outros: sem isso, com o
     filtro ligado, não dá para ver no mapa qual área está mandando no que a tela mostra. */
  function aplicarDestaque() {
    var atual = LocalizacoesFiltros.areaAtual().split(":");
    var tipo = atual[0], id = atual[1];

    camadas.forEach(function (camada) {
      var estado = desenhos[camada.id_camada];
      if (!estado || !estado.layer) return;
      estado.layer.eachLayer(function (feicao) {
        var destacada =
          (tipo === "camada" && String(id) === String(camada.id_camada)) ||
          (tipo === "feicao" && String(id) === String(feicao.feature.properties.id_feicao));
        feicao.setStyle(estiloFeicao(camada, destacada));
      });
    });
  }

  function contagemDe(idCamada, idFeicao) {
    var estado = desenhos[idCamada];
    if (!estado || !estado.contagens) return null;
    var valor = estado.contagens[idFeicao];
    return valor === undefined ? 0 : valor;
  }

  function tooltipFeicao(camada, feature) {
    var qt = contagemDe(camada.id_camada, feature.properties.id_feicao);
    return "<strong>" + escapar(feature.properties.no_feicao || "Área sem nome") + "</strong>" +
      "<br>" + escapar(camada.no_camada) +
      (qt === null ? "" : "<br>" + numero(qt) + " registro(s) aqui dentro");
  }

  // no máximo 12 atributos no popup: malha oficial traz dezenas de colunas e o resto é ruído
  var MAX_ATRIBUTOS = 12;

  function popupFeicao(camada, feature) {
    var props = feature.properties;
    var qt = contagemDe(camada.id_camada, props.id_feicao);
    var html =
      '<div class="loc-poligono">' +
      "<h4>" + escapar(props.no_feicao || "Área " + props.nu_ordem) + "</h4>" +
      '<p class="loc-poligono-sub">' + escapar(camada.no_camada) +
      (qt === null ? "" : " · <strong>" + numero(qt) + "</strong> registro(s) dentro") + "</p>";

    var atributos = props.atributos || {};
    var chaves = Object.keys(atributos).filter(function (k) {
      var v = atributos[k];
      return v !== null && v !== undefined && String(v).trim() !== "";
    }).slice(0, MAX_ATRIBUTOS);

    if (chaves.length) {
      html += "<dl>";
      chaves.forEach(function (k) {
        html += "<dt>" + escapar(k) + "</dt><dd>" + escapar(atributos[k]) + "</dd>";
      });
      html += "</dl>";
    }

    var recorteAtivo = LocalizacoesFiltros.areaAtual() === "feicao:" + props.id_feicao;
    html +=
      '<div class="loc-poligono-acoes">' +
      '<button type="button" class="btn loc-poligono-acao" data-acao="' +
      (recorteAtivo ? "limpar" : "filtrar") + '" data-feicao="' + props.id_feicao + '">' +
      (recorteAtivo ? "Tirar o recorte" : "Só esta área") + "</button>" +
      "</div></div>";
    return html;
  }

  // o conteúdo do popup é HTML montado na hora, então o clique é capturado por delegação
  $(document).on("click", ".loc-poligono-acao", function () {
    var acao = $(this).data("acao");
    var idFeicao = $(this).data("feicao");
    LocalizacoesFiltros.area(acao === "limpar" ? "" : "feicao:" + idFeicao);
    if (mapa) mapa.closePopup();
  });

  // ---------------------------------------------------------------- contagem por área

  function agendarContagem() {
    clearTimeout(timerContagem);
    timerContagem = setTimeout(contar, 200);   // o filtro muda em rajada; conta uma vez só
  }

  // uma requisição de contagem por camada, abortada quando uma mais nova sai: sem o abort, uma
  // resposta atrasada do filtro anterior podia chegar depois e reescrever o número da tela
  var requisicoesContagem = {};

  function contar() {
    camadas.forEach(function (camada) {
      var estado = desenhos[camada.id_camada];
      if (!visiveis[camada.id_camada] || !estado || !estado.layer) return;

      if (requisicoesContagem[camada.id_camada]) requisicoesContagem[camada.id_camada].abort();
      requisicoesContagem[camada.id_camada] = $.getJSON(BASE + "/" + camada.id_camada + "/contagem", filtrosBase());
      requisicoesContagem[camada.id_camada].done(function (resp) {
        requisicoesContagem[camada.id_camada] = null;
        var atual = desenhos[camada.id_camada];
        if (!atual) return;                     // a camada saiu enquanto a contagem vinha
        atual.contagens = resp.contagens || {};
        atual.total = resp.total_dentro || 0;
        renderBarra();
      }).fail(function () {
        requisicoesContagem[camada.id_camada] = null;   // abort ou rede: a próxima contagem refaz
      });
    });
  }

  // ---------------------------------------------------------------- importar / renomear / apagar

  function acaoCamada(acao, camada) {
    if (acao === "enquadrar") {
      if (!mapa) return;
      mapa.fitBounds(
        [[camada.vl_lat_min, camada.vl_lon_min], [camada.vl_lat_max, camada.vl_lon_max]],
        { padding: [30, 30] }
      );
      return;
    }
    if (acao === "renomear") {
      Dialogo.prompt({
        titulo: "Renomear camada",
        mensagem: "Nome desta camada de áreas:",
        valor: camada.no_camada,
        maxlength: 200,
      }).then(function (nome) {
        if (nome === null || !nome.trim()) return;
        $.ajax({
          url: BASE + "/" + camada.id_camada,
          method: "PUT",
          contentType: "application/json",
          data: JSON.stringify({ nome: nome.trim() }),
        }).done(carregar).fail(function (xhr) {
          Toast.erro(detalheErro(xhr, "Não foi possível renomear a camada."));
        });
      });
      return;
    }
    if (acao === "apagar") {
      Dialogo.confirmar({
        titulo: "Apagar camada",
        mensagem: "Apagar a camada “" + camada.no_camada + "” e as suas " +
          numero(camada.qt_feicoes) + " área(s)?\n\nAs localizações do inquérito não são afetadas.",
        rotuloOk: "Apagar",
        perigoso: true,
      }).then(function (ok) {
        if (!ok) return;
        $.ajax({ url: BASE + "/" + camada.id_camada, method: "DELETE" }).done(function () {
          // o recorte que apontava para a camada apagada deixa de existir junto
          var atual = LocalizacoesFiltros.areaAtual();
          if (atual === "camada:" + camada.id_camada) LocalizacoesFiltros.area("");
          esconder(camada.id_camada);
          delete desenhos[camada.id_camada];
          delete visiveis[camada.id_camada];
          Toast.sucesso("Camada apagada.");
          carregar();
        }).fail(function (xhr) {
          Toast.erro(detalheErro(xhr, "Não foi possível apagar a camada."));
        });
      });
    }
  }

  var CORES_SUGERIDAS = ["#f59e0b", "#22d3ee", "#a855f7", "#22c55e", "#ef4444", "#6366f1"];

  // Limite geral de upload (UPLOAD_DOCUMENTO_MB, ajustável em Administração → Desempenho),
  // aplicado ao TOTAL somado dos arquivos do conjunto — não por arquivo. O servidor decide
  // (geoespacial_utils._limite_upload_bytes); aqui é só aviso prévio, sem esperar o upload subir.
  // Padrão igual ao geral (50); a checagem local só barra depois de ler o valor configurado —
  // sem ele, quem decide é o servidor, em vez de recusar com um número que pode estar errado.
  var LIMITE_GERAL_MB = 50;
  var LIMITE_GERAL_BYTES = 50 * 1024 * 1024;
  var limiteConfirmado = false;
  var dicaLimite = null;

  $.getJSON("/api/documentos/upload/limite").done(function (r) {
    var mb = r && parseInt(r.limite_mb, 10);
    if (!mb || mb < 1 || mb > 500) return;
    limiteConfirmado = true;
    LIMITE_GERAL_MB = mb;
    LIMITE_GERAL_BYTES = mb * 1024 * 1024;
    if (dicaLimite) dicaLimite.text("Até " + mb + " MB no total (limite geral de upload).");
  });

  $botao.on("click", abrirImportacao);

  function abrirImportacao() {
    var $overlay = $('<div class="modal-overlay">');
    var $modal = $('<div class="modal">').css("width", "min(520px, 94vw)")
      .attr("role", "dialog").attr("aria-modal", "true").attr("aria-labelledby", "camada-modal-titulo")
      .appendTo($overlay);

    var $cabecalho = $('<div class="modal-header">').appendTo($modal);
    $("<h2>").attr("id", "camada-modal-titulo").text("Importar shapefile").appendTo($cabecalho);
    var $fechar = $('<button type="button" class="modal-close" aria-label="Fechar">').html("&times;").appendTo($cabecalho);

    var $corpo = $('<div class="modal-body">').appendTo($modal);
    $('<p class="card-hint">').html(
      "Shapefile não é um arquivo só. Envie o <strong>.zip</strong> com o conjunto, ou selecione " +
      "de uma vez o <strong>.shp</strong>, o <strong>.dbf</strong>, o <strong>.prj</strong> e o " +
      "<strong>.shx</strong>.<br>Sem o <strong>.prj</strong> não há como saber em que projeção o " +
      "arquivo está, e o polígono pode cair no lugar errado."
    ).appendTo($corpo);

    var $arquivo = $('<input type="file" class="dialogo-campo" multiple>')
      .attr("accept", ".zip,.shp,.dbf,.prj,.shx,.cpg")
      .appendTo($corpo);

    dicaLimite = $('<p class="card-hint">')
      .text("Até " + LIMITE_GERAL_MB + " MB no total (limite geral de upload).")
      .appendTo($corpo);

    var $nome = $('<input type="text" class="dialogo-campo">')
      .attr("placeholder", "Nome da camada (padrão: nome do arquivo)")
      .attr("maxlength", 200)
      .appendTo($corpo);

    var $linhaCor = $('<div class="loc-camada-cores">').appendTo($corpo);
    $("<span>").text("Cor no mapa:").appendTo($linhaCor);
    var corEscolhida = CORES_SUGERIDAS[camadas.length % CORES_SUGERIDAS.length];
    CORES_SUGERIDAS.forEach(function (cor) {
      $('<button type="button" class="loc-camada-cor-opcao">')
        .css("background", cor)
        .toggleClass("ativa", cor === corEscolhida)
        .attr("title", cor)
        .on("click", function () {
          corEscolhida = cor;
          $linhaCor.find(".loc-camada-cor-opcao").removeClass("ativa");
          $(this).addClass("ativa");
        })
        .appendTo($linhaCor);
    });

    var $status = $('<p class="card-hint">').appendTo($corpo);

    var $acoes = $('<div class="form-actions">').appendTo($corpo);
    var $enviar = $('<button type="button" class="btn btn-primary">').text("Importar").appendTo($acoes);
    var $cancelar = $('<button type="button" class="btn btn-secondary">').text("Cancelar").appendTo($acoes);

    function fecharModal() {
      $(document).off("keydown", aoTeclar);
      $overlay.remove();
      dicaLimite = null;
    }
    // Escape fecha; Tab circula DENTRO do modal (focus trap): com a overlay aberta, o resto da
    // página não pode receber foco — é o mínimo de um dialog acessível.
    function aoTeclar(e) {
      if (e.key === "Escape") { fecharModal(); return; }
      if (e.key !== "Tab") return;
      var $foco = $modal.find("button, input, select, textarea, a[href]").filter(":visible");
      if (!$foco.length) return;
      var i = $foco.index(document.activeElement);
      if (i === -1) return;                       // foco fora do modal: o navegador entra no fluxo
      e.preventDefault();
      $foco.eq((i + (e.shiftKey ? $foco.length - 1 : 1)) % $foco.length).trigger("focus");
    }

    $fechar.on("click", fecharModal);
    $cancelar.on("click", fecharModal);
    $overlay.on("click", function (e) { if (e.target === $overlay[0]) fecharModal(); });
    $(document).on("keydown", aoTeclar);

    $enviar.on("click", function () {
      var arquivos = $arquivo[0].files;
      if (!arquivos || !arquivos.length) {
        $status.text("Escolha o .zip do shapefile ou os arquivos do conjunto.");
        return;
      }
      // O teto é o limite geral sobre a SOMA — um .shp sozinho pode passar do que seria
      // "por arquivo" e ainda caber no total, e é isso que vale.
      var total = 0;
      for (var j = 0; j < arquivos.length; j++) total += arquivos[j].size || 0;
      if (limiteConfirmado && total > LIMITE_GERAL_BYTES) {
        $status.text(
          "Arquivo grande demais (" + Math.round(total / 1048576) + " MB no total; o limite geral é " +
          LIMITE_GERAL_MB + " MB). Recorte a área de interesse antes de importar."
        );
        return;
      }
      var dados = new FormData();
      for (var i = 0; i < arquivos.length; i++) dados.append("arquivos", arquivos[i], arquivos[i].name);
      if ($nome.val().trim()) dados.append("nome", $nome.val().trim());
      dados.append("cor", corEscolhida);

      $enviar.prop("disabled", true);
      $status.text("Lendo o shapefile e reprojetando…");

      $.ajax({ url: BASE, method: "POST", data: dados, processData: false, contentType: false })
        .done(function (resp) {
          fecharModal();
          Toast.sucesso(
            "Camada “" + resp.no_camada + "” importada: " + numero(resp.qt_feicoes) + " área(s)."
          );
          if (resp.sem_prj) {
            Toast.aviso(
              "O shapefile veio sem .prj: as coordenadas foram usadas como lat/lon. " +
              "Confira no mapa se as áreas caíram no lugar certo."
            );
          }
          if (resp.ja_importado) {
            Toast.aviso(
              "Este mesmo arquivo já tinha sido importado como “" + resp.ja_importado +
              "”. Duas camadas iguais contam o mesmo registro duas vezes na leitura."
            );
          }
          visiveis[resp.id_camada] = true;
          $.getJSON(BASE).done(function (lista) {
            camadas = lista || [];
            renderBarra();
            renderSelect();
            sincronizarDesenho();
            var nova = porId(resp.id_camada);
            if (nova && mapa) {
              mapa.fitBounds(
                [[nova.vl_lat_min, nova.vl_lon_min], [nova.vl_lat_max, nova.vl_lon_max]],
                { padding: [30, 30] }
              );
            }
          });
        })
        .fail(function (xhr) {
          $enviar.prop("disabled", false);
          $status.text(detalheErro(xhr, "Não foi possível importar o shapefile."));
        });
    });

    $("body").append($overlay);
    $arquivo.trigger("focus");
  }

  // ---------------------------------------------------------------- ligações com o resto da tela

  // o mapa só existe depois que a aba Grafo é aberta pela primeira vez (js/localizacoes_grafo.js)
  window.LocalizacoesCamadas = { usarMapa: usarMapa };

  LocalizacoesFiltros.aoMudar(function () {
    aplicarDestaque();
    agendarContagem();
  });

  carregar();
})();
