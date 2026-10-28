(function () {
  "use strict";

  var TP_ORIGEM = "conta_financeira";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $head = $("#dados-cabecalho");
  var $empty = $("#dados-vazio");

  var $busca = $("#filtro-busca");
  var $tipo = $("#filtro-tipo");
  var $instituicao = $("#filtro-instituicao");
  var $titular = $("#filtro-titular");
  var $fonte = $("#filtro-fonte");
  var $moeda = $("#filtro-moeda");
  var $saldo = $("#filtro-saldo");
  var $situacao = $("#filtro-situacao");
  var $marcadas = $("#filtro-marcadas");
  var $resumo = $("#filtro-resumo");
  var $limpar = $("#filtro-limpar");

  if (!idInquerito) {
    $empty.text("Parâmetros inválidos na URL.").show();
    return;
  }

  var contagens = {};
  var contas = [];

  // tp_conta agora guarda a CATEGORIA da instituição (o "como achamos" foi para a coluna Fonte).
  // Os dois últimos rótulos são de extrações antigas, gravadas antes dessa mudança.
  var ROTULO_TIPO = {
    banco: "Banco",
    pagamento: "Pagamento/carteira",
    cripto: "Carteira de cripto",
    aposta: "Casa de apostas",
    capitalizacao: "Capitalização/sorteio",
    credito: "Crédito/consulta",
    cartao: "Cartão",
    conta_corrente: "Conta corrente",
    senha_salva: "Credencial salva",
    app_instalado: "Aplicativo instalado"
  };

  // Titular vindo do gerenciador de senhas costuma ser o CPF cru (é o login de banco no Brasil).
  function formatarTitular(valor) {
    var so_digitos = (valor || "").replace(/\D/g, "");
    if (valor && so_digitos.length === 11 && so_digitos === valor) {
      return so_digitos.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
    }
    return valor;
  }

  /* VL_SALDO é texto no banco (vem como o aplicativo gravou: "1.234,56", "R$ 0,00", "0.0"),
   * então o filtro de saldo precisa interpretar as duas convenções decimais antes de decidir se
   * a conta tem dinheiro. Saldo que não vira número nenhum conta como "informado", não como
   * zero — é dado que existe e o investigador tem que ver. */
  function saldoNumero(valor) {
    var texto = String(valor == null ? "" : valor).replace(/[^\d,.-]/g, "");
    if (!texto) return null;
    if (texto.indexOf(",") >= 0) texto = texto.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(texto);
    return isNaN(n) ? null : n;
  }

  function temSaldo(r) {
    return String(r.vl_saldo == null ? "" : r.vl_saldo).trim() !== "";
  }

  ["Instituição", "Titular", "Conta", "Agência", "Tipo", "Saldo", "Moeda", "Fonte", "Situação",
   "Entidade"].forEach(function (x) {
    $("<td>").text(x).appendTo($head);
  });

  function renderLinha(r) {
    var $tr = $("<tr>").attr("data-id", r.id_conta_financeira);
    var tipo = ROTULO_TIPO[r.tp_conta] || r.tp_conta;
    [r.no_instituicao, formatarTitular(r.no_titular), r.nu_conta, r.nu_agencia, tipo, r.vl_saldo, r.no_moeda, r.no_fonte].forEach(function (v) {
      $("<td>").text(v || "—").appendTo($tr);
    });
    // conta apagada do aparelho é achado, não ruído: a marca é a mesma das demais telas do UFDR
    var marca = UfdrDominio.marcaEstado(r.cd_status_ufdr);
    $("<td>").append(marca || document.createTextNode("Íntegra")).appendTo($tr);
    $("<td>").append(EntidadesUI.botao({
      idInquerito: idInquerito,
      tpOrigem: TP_ORIGEM,
      idOrigem: r.id_conta_financeira,
      rotulo: "conta " + (r.no_instituicao || "") + (r.nu_conta ? " " + r.nu_conta : ""),
      temComentario: !!contagens[r.id_conta_financeira],
      aoMudar: function () {}
    })).appendTo($tr);
    return $tr;
  }

  // A exportação automática lê o DOM (só a página atual do paginador). Instalar antes do
  // auto-init do exportar_excel.js troca o botão por um que exporta o filtro inteiro.
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "contas-financeiras",
      dados: function () {
        return {
          colunas: ["Instituição", "Titular", "Conta", "Agência", "Tipo", "Saldo", "Moeda",
                    "Fonte", "Situação"],
          linhas: filtradasAtual.map(function (r) {
            return [r.no_instituicao || "", r.no_titular || "", r.nu_conta || "", r.nu_agencia || "",
                    ROTULO_TIPO[r.tp_conta] || r.tp_conta || "", r.vl_saldo != null ? r.vl_saldo : "",
                    r.no_moeda || "", r.no_fonte || "",
                    r.cd_status_ufdr && r.cd_status_ufdr !== "Intact" ? r.cd_status_ufdr : "Íntegra"];
          }),
        };
      },
    });
  }

  var paginador = UfdrDominio.criarPaginador({
    corpo: "#dados-corpo",
    depoisDe: "#dados-tabela",
    unidade: "conta(s)",
    render: renderLinha,
    aoDesenhar: function (qt) {
      $empty.toggle(qt === 0);
      if (qt === 0) {
        // "o aparelho não tem conta nenhuma" e "nenhuma bate com o filtro" são conclusões
        // diferentes para quem investiga; a mensagem tem que dizer qual das duas é
        $empty.text(contas.length === 0
          ? "Nenhuma conta financeira encontrada no UFDR."
          : "Nenhuma conta com esses filtros.");
      }
      if (abas) abas.definirContagem("contas", qt);
    }
  });

  /* ----- filtros da lista de contas -----
   * A lista vem inteira do servidor (é pequena: dezenas de contas por aparelho), então filtrar
   * no cliente evita uma ida ao banco a cada tecla. Não há filtro por período aqui porque o
   * modelo de conta do UFDR não tem data nenhuma — o que tem data são as transferências e os
   * cartões, nas outras abas, onde o filtro de período existe. */
  var CHAVES_URL = ["busca", "categoria", "instituicao", "titular", "fonte", "moeda", "saldo",
                    "situacao", "marcadas"];

  function preencherSelect($sel, valores) {
    valores.forEach(function (v) {
      $("<option>").attr("value", v.valor)
        .text(v.rotulo + "  (" + v.qt.toLocaleString("pt-BR") + ")").appendTo($sel);
    });
    // select com uma opção só não escolhe nada: some para não ocupar espaço na barra
    $sel.closest(".filtro-campo").toggle(valores.length > 1);
  }

  function opcoesDe(campo, rotular) {
    var qt = {};
    contas.forEach(function (r) {
      var v = (r[campo] == null ? "" : String(r[campo])).trim();
      if (v) qt[v] = (qt[v] || 0) + 1;
    });
    return Object.keys(qt).sort(function (a, b) {
      return qt[b] - qt[a] || a.localeCompare(b, "pt-BR");
    }).map(function (v) {
      return { valor: v, rotulo: rotular ? rotular(v) : v, qt: qt[v] };
    });
  }

  function montarOpcoes() {
    preencherSelect($tipo, opcoesDe("tp_conta", function (v) { return ROTULO_TIPO[v] || v; }));
    preencherSelect($instituicao, opcoesDe("no_instituicao"));
    preencherSelect($titular, opcoesDe("no_titular", formatarTitular));
    preencherSelect($fonte, opcoesDe("no_fonte"));
    preencherSelect($moeda, opcoesDe("no_moeda"));
    // saldo e situação são listas fixas: só some se o dado não existir em conta nenhuma
    $saldo.closest(".filtro-campo").toggle(contas.some(temSaldo));
    $situacao.closest(".filtro-campo").toggle(contas.some(function (r) {
      return r.cd_status_ufdr && r.cd_status_ufdr !== "Intact";
    }));
  }

  function combina(r) {
    var termo = $busca.val().trim().toLowerCase();
    if (termo) {
      var alvo = [r.no_instituicao, r.no_titular, formatarTitular(r.no_titular), r.nu_conta,
                  r.nu_agencia, r.no_fonte, r.no_moeda, ROTULO_TIPO[r.tp_conta] || r.tp_conta]
        .join(" ").toLowerCase();
      if (alvo.indexOf(termo) < 0) return false;
    }
    if ($tipo.val() && r.tp_conta !== $tipo.val()) return false;
    if ($instituicao.val() && r.no_instituicao !== $instituicao.val()) return false;
    if ($titular.val() && r.no_titular !== $titular.val()) return false;
    if ($fonte.val() && r.no_fonte !== $fonte.val()) return false;
    if ($moeda.val() && r.no_moeda !== $moeda.val()) return false;

    var saldo = $saldo.val();
    if (saldo === "informado" && !temSaldo(r)) return false;
    if (saldo === "sem" && temSaldo(r)) return false;
    if (saldo === "positivo" && (!temSaldo(r) || saldoNumero(r.vl_saldo) === 0)) return false;

    var apagada = r.cd_status_ufdr && r.cd_status_ufdr !== "Intact";
    if ($situacao.val() === "apagados" && !apagada) return false;
    if ($situacao.val() === "intactos" && apagada) return false;

    if ($marcadas.is(":checked") && !contagens[r.id_conta_financeira]) return false;
    return true;
  }

  function filtrosAtivos() {
    var qt = 0;
    [$busca, $tipo, $instituicao, $titular, $fonte, $moeda, $saldo, $situacao]
      .forEach(function ($c) { if ($c.val()) qt += 1; });
    if ($marcadas.is(":checked")) qt += 1;
    return qt;
  }

  var filtradasAtual = [];

  function aplicar() {
    var filtradas = contas.filter(combina);
    filtradasAtual = filtradas;
    paginador.definir(filtradas);
    var dest = EstadoUrl.ler(["destacar"]).destacar;
    if (dest) {
      var $linha = $("#dados-corpo").find('[data-id="' + dest + '"]');
      if ($linha.length) {
        $linha.addClass("tp-destacada");
        $linha[0].scrollIntoView({ block: "center" });
        EstadoUrl.salvar({ destacar: null });
      }
    }

    var qt = filtrosAtivos();
    $limpar.toggle(qt > 0).text(qt > 1 ? "Limpar filtros (" + qt + ")" : "Limpar filtros");
    $resumo.text(qt > 0
      ? filtradas.length.toLocaleString("pt-BR") + " de " +
        contas.length.toLocaleString("pt-BR") + " conta(s)"
      : "");

    EstadoUrl.salvar({
      busca: $busca.val().trim(), categoria: $tipo.val(), instituicao: $instituicao.val(),
      titular: $titular.val(), fonte: $fonte.val(), moeda: $moeda.val(), saldo: $saldo.val(),
      situacao: $situacao.val(), marcadas: $marcadas.is(":checked") ? "1" : ""
    });
  }

  function restaurarDaUrl() {
    var estado = EstadoUrl.ler(CHAVES_URL);
    $busca.val(estado.busca || "");
    $tipo.val(estado.categoria || "");
    $instituicao.val(estado.instituicao || "");
    $titular.val(estado.titular || "");
    $fonte.val(estado.fonte || "");
    $moeda.val(estado.moeda || "");
    $saldo.val(estado.saldo || "");
    $situacao.val(estado.situacao || "");
    $marcadas.prop("checked", estado.marcadas === "1");
  }

  var buscaTimer = null;
  $busca.on("input", function () {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(aplicar, 250);
  });
  $tipo.add($instituicao).add($titular).add($fonte).add($moeda).add($saldo).add($situacao)
    .add($marcadas).on("change", aplicar);
  $limpar.on("click", function () {
    [$busca, $tipo, $instituicao, $titular, $fonte, $moeda, $saldo, $situacao]
      .forEach(function ($c) { $c.val(""); });
    $marcadas.prop("checked", false);
    aplicar();
  });

  var carregou = false;

  function carregar() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/contas-financeiras" + (idExtracao ? "?extracao=" + encodeURIComponent(idExtracao) : ""))
      .done(function (linhas) {
        contas = linhas || [];
        carregou = true;
        montarOpcoes();
        restaurarDaUrl();   // depois das opções: o select precisa ter o valor para aceitá-lo
        aplicar();
      })
      .fail(function () { $empty.text("Não foi possível carregar as contas financeiras.").show(); });
  }

  // Esta lista vira a primeira aba; transferências e cartões vêm do componente compartilhado.
  var abas = UfdrDominio.montar({
    seletor: "#abas-host",
    abas: [{ chave: "contas", rotulo: "Contas", externo: "#painel-contas" }]
      .concat(UfdrDominio.abasDe(["transferencias", "cartoes-credito", "cartoes-carteira"])),
    // o contador da topbar é um só: ao voltar para cá ele ainda mostra o total dos cartões
    aoTrocarAba: function (chave) { if (chave === "contas" && carregou) aplicar(); }
  });

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    carregar();
  });
})();
