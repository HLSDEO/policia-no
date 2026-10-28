(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var TIPO_ROTULO = {
    cpf: "CPF", cnpj: "CNPJ", email: "E-mail", telefone: "Telefone", aleatoria: "Aleatória (EVP)",
  };

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $total = $("#total-registros");
  var $btnRedetectar = $("#btn-redetectar");
  var $estDetectar = $("#estado-detectar");
  var $estProcessando = $("#estado-processando");
  var $estLista = $("#estado-lista");
  var $corpo = $("#dados-corpo");
  var $vazio = $("#dados-vazio");
  var $busca = $("#filtro-busca");
  var $tipo = $("#filtro-tipo");

  if (!idInquerito || !idExtracao) {
    $estDetectar.hide();
    $("#estado-lista").show();
    $vazio.text("Parâmetros inválidos na URL (é necessário inquérito e extração).").show();
    return;
  }

  var linhas = [];   // todas as chaves carregadas (a lista é pequena — filtra no cliente)

  function mostrarEstado(qual) {
    $estDetectar.toggle(qual === "detectar");
    $estProcessando.toggle(qual === "processando");
    $estLista.toggle(qual === "lista");
    $btnRedetectar.toggle(qual === "lista");
  }

  function linkMensagem(r) {
    var url = MapaTelas.paginaConversa(r.no_fonte, r.no_app) +
      "?inquerito=" + encodeURIComponent(idInquerito) +
      "&extracao=" + encodeURIComponent(idExtracao) +
      "&fonte=" + encodeURIComponent(r.no_fonte) +
      "&msg=" + encodeURIComponent(r.id_mensagem);
    if (r.no_fonte === "msg" && r.no_app) url += "&app=" + encodeURIComponent(r.no_app);
    return url;
  }

  function renderLinha(r) {
    var $tr = $("<tr>").attr("data-id", r.id_chave_pix);
    $("<td>").append($("<span>").css({ fontFamily: "monospace", fontWeight: 600 }).text(r.co_chave)).appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-tag").text(TIPO_ROTULO[r.tp_chave] || r.tp_chave)).appendTo($tr);
    var remetente = r.in_remetente_dono ? "Dono do aparelho" : (r.no_remetente || "—");
    $("<td>").text(remetente).appendTo($tr);
    $("<td>").text(r.no_conversa || "—").appendTo($tr);
    $("<td>").text(r.no_fonte === "wpp" ? "WhatsApp" : r.no_fonte === "sms" ? "SMS" : (r.no_app || "Mensageiro")).appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-caminho").css({ direction: "ltr", maxWidth: "360px" }).attr("title", r.tx_trecho || "").text(r.tx_trecho || "—")).appendTo($tr);
    $("<td>").text(formatarDataHora(r.dt_mensagem)).appendTo($tr);
    $("<td>").append($("<a>").addClass("arq-acao").attr({ href: linkMensagem(r) }).text("Ver mensagem")).appendTo($tr);
    return $tr;
  }

  function filtrar() {
    var termo = $busca.val().trim().toLowerCase();
    var tipo = $tipo.val();
    return linhas.filter(function (r) {
      if (tipo && r.tp_chave !== tipo) return false;
      if (termo) {
        var alvo = (r.co_chave + " " + (r.no_remetente || "") + " " + (r.no_conversa || "") + " " + (r.tx_trecho || "")).toLowerCase();
        if (alvo.indexOf(termo) === -1) return false;
      }
      return true;
    });
  }

  var filtradas = [];

  // As menções podem passar de mil (uma linha por mensagem); só a página atual entra no DOM.
  // O contador da tela é próprio (menções + chaves distintas), então o total do paginador é
  // desligado (seletor inexistente) e atualizado no aoDesenhar.
  var paginador = UfdrDominio.criarPaginador({
    corpo: "#dados-corpo",
    depoisDe: "#dados-tabela",
    total: "#total-registros-paginador",
    unidade: "menções",
    render: renderLinha,
    aoDesenhar: function (qt) {
      var distintas = {};
      filtradas.forEach(function (r) { distintas[r.tp_chave + ":" + r.co_chave] = true; });
      $total.text(qt.toLocaleString("pt-BR") + " menções · " + Object.keys(distintas).length + " chaves distintas");
      $vazio.toggle(qt === 0);
      var dest = EstadoUrl.ler(["destacar"]).destacar;
      if (dest) {
        var $linha = $corpo.find('[data-id="' + dest + '"]');
        if ($linha.length) {
          $linha.addClass("tp-destacada");
          $linha[0].scrollIntoView({ block: "center" });
          EstadoUrl.salvar({ destacar: null });
        }
      }
    }
  });

  function aplicarFiltros() {
    filtradas = filtrar();
    paginador.definir(filtradas);
  }

  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "chaves-pix",
      dados: function () {
        return {
          colunas: ["Chave", "Tipo", "Remetente", "Conversa", "Fonte", "Trecho", "Data da mensagem"],
          linhas: filtrar().map(function (r) {
            return [r.co_chave, TIPO_ROTULO[r.tp_chave] || r.tp_chave || "",
                    r.in_remetente_dono ? "Dono do aparelho" : (r.no_remetente || ""),
                    r.no_conversa || "",
                    r.no_fonte === "wpp" ? "WhatsApp" : r.no_fonte === "sms" ? "SMS" : (r.no_app || "Mensageiro"),
                    r.tx_trecho || "", formatarDataHora(r.dt_mensagem)];
          }),
        };
      },
    });
  }

  function carregarLista() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/chaves-pix?extracao=" + encodeURIComponent(idExtracao))
      .done(function (dados) {
        linhas = dados || [];
        mostrarEstado("lista");
        aplicarFiltros();
      })
      .fail(function () {
        mostrarEstado("lista");
        // o texto sozinho deixa a pessoa parada; o botão re-tenta na hora
        $vazio.empty().text("Não foi possível carregar as chaves PIX.");
        $("<button>").attr("type", "button").addClass("btn-link").text("Tentar novamente")
          .on("click", carregarLista).appendTo($vazio);
        $vazio.show();
        if (window.Toast) Toast.erro("Falha ao carregar as chaves PIX.");
      });
  }

  function detectar() {
    mostrarEstado("processando");
    $.ajax({ url: "/api/investigacao/extracoes/" + idExtracao + "/chaves-pix/detectar", method: "POST" })
      .done(function () { carregarLista(); })
      .fail(function () {
        mostrarEstado("detectar");
        $estDetectar.prepend($("<p>").css("color", "#f43f5e").text("Falha ao detectar. Tente novamente."));
      });
  }

  // fluxo de entrada: consulta o status e decide entre pedir detecção ou já mostrar a lista
  $.getJSON("/api/investigacao/extracoes/" + idExtracao + "/chaves-pix/status")
    .done(function (st) {
      if (st.escaneado) carregarLista();
      else mostrarEstado("detectar");
    })
    .fail(function () { mostrarEstado("detectar"); });

  $("#btn-detectar").on("click", detectar);
  $btnRedetectar.on("click", detectar);
  $busca.on("input", aplicarFiltros);
  $tipo.on("change", aplicarFiltros);
})();
