(function () {
  "use strict";

  var TP_ORIGEM = "wifi";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  var SEGURANCA_CORES = {
    "WPA2": { bg: "#bbf7d0", fg: "#166534", label: "WPA2" },
    "WPA": { bg: "#fef08a", fg: "#854d0e", label: "WPA" },
    "WEP": { bg: "#fecaca", fg: "#991b1b", label: "WEP" },
    "WPA3": { bg: "#c7d2fe", fg: "#3730a3", label: "WPA3" },
    "Open": { bg: "#d1d5db", fg: "#374151", label: "Aberta" },
  };

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $total = $("#total-registros");
  var $estVazio = $("#estado-vazio");
  var $estCarregando = $("#estado-carregando");
  var $estLista = $("#estado-lista");
  var $corpo = $("#dados-corpo");
  var $vazio = $("#dados-vazio");
  var $busca = $("#filtro-busca");
  var $senha = $("#filtro-senha");
  var $ordenar = $("#filtro-ordenar");

  if (!idInquerito || !idExtracao) {
    $estVazio.text("Parâmetros inválidos na URL (é necessário inquérito e extração).").show();
    return;
  }

  var linhas = [];
  var contagens = {};

  /* O mesmo Wi-Fi chega em várias linhas e a tela parece quebrada: o iPhone grava a rede em
   * registros separados (um com o BSSID, outro com a segurança, outro com o computador que
   * conectou), e o Android repete a rede a cada perfil. O usuário vê "LAVANDARIO" cinco vezes
   * com informações soltas. A lista inteira já está em memória, então a mesclagem é aqui:
   * agrupa por SSID (na falta dele, pelo BSSID) e preenche cada coluna com o que houver.
   * O id da linha mesclada é o MENOR do grupo — estável entre recargas, que é o que o
   * marcador de entidade precisa para não perder o alvo. */
  function mesclarRedes(lista) {
    var grupos = {};
    var ordem = [];
    lista.forEach(function (r) {
      var chave = (r.no_ssid || "").trim().toUpperCase()
        || ((r.no_bssid || "").trim().toUpperCase() && "bssid:" + r.no_bssid.trim().toUpperCase())
        || "id:" + r.id_rede_wifi;
      if (!grupos[chave]) { grupos[chave] = []; ordem.push(chave); }
      grupos[chave].push(r);
    });
    return ordem.map(function (chave) {
      var grupo = grupos[chave];
      if (grupo.length === 1) return grupo[0];

      var base = grupo[0];
      grupo.forEach(function (r) { if (r.id_rede_wifi < base.id_rede_wifi) base = r; });

      function primeiro(campo) {
        var v = null;
        grupo.forEach(function (r) { if (v == null && r[campo] != null && r[campo] !== "") v = r[campo]; });
        return v;
      }
      function algum(campo) {
        var v = false;
        grupo.forEach(function (r) { if (r[campo]) v = true; });
        return v;
      }
      function unico(campo) {
        var vistos = [];
        grupo.forEach(function (r) {
          if (r[campo] && vistos.indexOf(String(r[campo])) === -1) vistos.push(String(r[campo]));
        });
        return vistos;
      }
      var minima = null, maxima = null, conexoes = null;
      grupo.forEach(function (r) {
        if (r.dt_primeira_conexao && (minima == null || r.dt_primeira_conexao < minima)) minima = r.dt_primeira_conexao;
        if (r.dt_ultima_conexao && (maxima == null || r.dt_ultima_conexao > maxima)) maxima = r.dt_ultima_conexao;
        if (r.qt_conexoes != null) conexoes = (conexoes || 0) + Number(r.qt_conexoes);
      });

      var m = {
        id_rede_wifi: base.id_rede_wifi,
        id_extracao: base.id_extracao,
        no_ssid: base.no_ssid || primeiro("no_ssid"),
        no_bssid: primeiro("no_bssid"),
        tp_seguranca: primeiro("tp_seguranca"),
        tp_autenticacao: primeiro("tp_autenticacao"),
        co_senha: primeiro("co_senha"),
        nr_canal: primeiro("nr_canal"),
        nr_sinal: primeiro("nr_sinal"),
        no_fabricante: unico("no_fabricante").join(", ") || null,
        in_rede_oculta: algum("in_rede_oculta"),
        in_favorita: algum("in_favorita"),
        dt_primeira_conexao: minima,
        dt_ultima_conexao: maxima,
        qt_conexoes: conexoes,
        no_fonte: unico("no_fonte").join(", ") || null,
        _registros: grupo.length,
        _bssids: unico("no_bssid"),
      };
      return m;
    });
  }

  function mostrarEstado(qual) {
    $estCarregando.toggle(qual === "carregando");
    $estLista.toggle(qual === "lista");
    $estVazio.toggle(qual === "vazio");
  }

  function tagSeguranca(tp) {
    var cor = SEGURANCA_CORES[tp] || { bg: "#f3f4f6", fg: "#6b7280", label: tp || "—" };
    return $("<span>").addClass("arq-tag").css({ background: cor.bg, color: cor.fg }).text(cor.label);
  }

  function rotuloSSID(r) {
    if (r.no_ssid) return r.no_ssid;
    if (r.no_bssid) return "(sem SSID, BSSID: " + r.no_bssid + ")";
    return "(rede sem identificação)";
  }

  function renderLinha(r) {
    var $tr = $("<tr>");
    var ssidTxt = rotuloSSID(r);
    var titulo = "Fonte: " + (r.no_fonte || "?");
    if (r.tp_autenticacao) titulo += " · Autenticação: " + r.tp_autenticacao;
    var $ssid = $("<span>").css({ fontFamily: "monospace", fontWeight: 600 }).attr("title", titulo).text(ssidTxt);
    var $tdSsid = $("<td>").append($ssid);
    // a rede veio em vários registros do UFDR e foi unida aqui — o selo conta quantos
    if (r._registros > 1) {
      $tdSsid.append($(" ").append(
        $("<span>").addClass("arq-tag").attr("title",
          r._registros + " registros do UFDR mesclados nesta linha (o aparelho grava a mesma rede em partes).").text(r._registros + " unidos")
      ));
    }
    $tdSsid.appendTo($tr);
    // mais de um BSSID sob o mesmo SSID (roteador mesh / vários access points)
    var $bssid = $("<code>").css({ fontSize: "12px" });
    if (r._bssids && r._bssids.length > 1) {
      $bssid.text(r._bssids[0] + " (+" + (r._bssids.length - 1) + ")")
        .attr("title", "Access points desta rede: " + r._bssids.join(", "));
    } else {
      $bssid.text(r.no_bssid || "—");
    }
    $("<td>").append($bssid).appendTo($tr);
    $("<td>").append(tagSeguranca(r.tp_seguranca)).appendTo($tr);
    $("<td>").text(r.co_senha ? r.co_senha : "—").appendTo($tr);
    $("<td>").css({ textAlign: "center" }).text(r.qt_conexoes != null ? Number(r.qt_conexoes).toLocaleString("pt-BR") : "—").appendTo($tr);
    $("<td>").text(formatarDataHora(r.dt_primeira_conexao)).appendTo($tr);
    $("<td>").text(formatarDataHora(r.dt_ultima_conexao)).appendTo($tr);
    $("<td>").css({ textAlign: "center" }).text(r.nr_canal != null ? r.nr_canal : "—").appendTo($tr);
    $("<td>").css({ textAlign: "center" }).text(r.nr_sinal != null ? r.nr_sinal + " dBm" : "—").appendTo($tr);
    $("<td>").text(r.no_fabricante || "—").appendTo($tr);
    $("<td>").css({ textAlign: "center" }).text(r.in_rede_oculta ? "Sim" : "Não").appendTo($tr);
    $("<td>").append($("<span>").addClass("arq-tag").text(r.no_fonte || "—")).appendTo($tr);
    $("<td>").append(EntidadesUI.botao({
      idInquerito: idInquerito,
      tpOrigem: TP_ORIGEM,
      idOrigem: r.id_rede_wifi,
      rotulo: "rede Wi-Fi " + (r.no_ssid || r.no_bssid || ""),
      temComentario: !!contagens[r.id_rede_wifi],
      aoMudar: function () {}
    })).appendTo($tr);
    return $tr;
  }

  // Um aparelho pode ter mais de 12 mil redes; despejar todas no DOM travava a tela. O filtro
  // continua sendo feito sobre a lista inteira em memória — só o desenho é paginado.
  var paginador = UfdrDominio.criarPaginador({
    corpo: "#dados-corpo",
    depoisDe: "#dados-tabela",
    unidade: "rede(s) Wi-Fi",
    render: renderLinha,
    aoDesenhar: function (qt) {
      $vazio.toggle(qt === 0);
      if (abas) abas.definirContagem("redes", qt);
    }
  });

  var filtradas = [];

  function aplicarFiltros() {
    var termo = $busca.val().trim().toLowerCase();
    var senha = $senha.val();
    var resultado = linhas.filter(function (r) {
      var temSenha = !!(r.co_senha && String(r.co_senha).trim());
      if (senha === "sim" && !temSenha) return false;
      if (senha === "nao" && temSenha) return false;
      if (termo) {
        var alvo = ((r.no_ssid || "") + " " + (r.no_bssid || "") + " " + (r.no_fabricante || "") + " " + (r.co_senha || "")).toLowerCase();
        if (alvo.indexOf(termo) === -1) return false;
      }
      return true;
    });

    // ordenação client-side (a lista já está toda em memória, inclusive mesclada)
    var ordem = $ordenar.val();
    if (ordem === "recentes") {
      resultado.sort(function (a, b) { return String(b.dt_ultima_conexao || "") < String(a.dt_ultima_conexao || "") ? -1 : 1; });
    } else if (ordem === "ssid") {
      resultado.sort(function (a, b) {
        return String(a.no_ssid || "\uffff").localeCompare(String(b.no_ssid || "\uffff"), "pt-BR");
      });
    } // "conexoes": já vem assim do banco (qt_conexoes DESC NULLS LAST)

    if (window.EstadoUrl) {
      // "sim" é o default da tela — não grava; "todos" (valor vazio) precisa de nome próprio,
      // já que valor vazio some da URL
      var senhaUrl = senha === "sim" ? "" : (senha === "" ? "todos" : senha);
      EstadoUrl.salvar({
        busca: $busca.val().trim(),
        senha: senhaUrl,
        ordenar: ordem !== "conexoes" ? ordem : "",
      });
    }
    filtradas = resultado;
    paginador.definir(resultado);
  }

  // A exportação automática lê o DOM (só a página atual do paginador). Instalar antes do
  // auto-init do exportar_excel.js troca o botão por um que exporta o filtro inteiro.
  if (window.ExportarExcel) {
    ExportarExcel.instalar({
      nome: "redes-wifi",
      dados: function () {
        return {
          colunas: ["SSID", "BSSID", "Segurança", "Senha", "Conexões", "Primeira conexão",
                    "Última conexão", "Canal", "Sinal (dBm)", "Fabricante", "Rede oculta", "Fonte"],
          linhas: filtradas.map(function (r) {
            return [r.no_ssid || (r.no_bssid ? "(sem SSID, BSSID: " + r.no_bssid + ")" : "(sem identificação)"),
                    r.no_bssid || "", r.tp_seguranca || "", r.co_senha || "",
                    r.qt_conexoes != null ? r.qt_conexoes : "", formatarDataHora(r.dt_primeira_conexao),
                    formatarDataHora(r.dt_ultima_conexao), r.nr_canal != null ? r.nr_canal : "",
                    r.nr_sinal != null ? r.nr_sinal : "", r.no_fabricante || "",
                    r.in_rede_oculta ? "Sim" : "Não", r.no_fonte || ""];
          }),
        };
      },
    });
  }

  function carregarLista() {
    mostrarEstado("carregando");
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/redes-wifi?extracao=" + encodeURIComponent(idExtracao))
      .done(function (dados) {
        linhas = mesclarRedes(dados || []);
        $vazio.empty().text("Nenhuma rede Wi-Fi encontrada.");   // limpa mensagem de falha anterior
        if (!linhas.length) {
          mostrarEstado("vazio");
          if (abas) abas.definirContagem("redes", 0);
          return;
        }
        mostrarEstado("lista");
        aplicarFiltros();
      })
      .fail(function () {
        mostrarEstado("lista");
        // o texto sozinho deixa a pessoa parada; o botão re-tenta na hora
        $corpo.empty();
        $vazio.empty().text("Não foi possível carregar as redes Wi-Fi.");
        $("<button>").attr("type", "button").addClass("btn-link").text("Tentar novamente")
          .on("click", carregarLista).appendTo($vazio);
        $vazio.show();
        if (window.Toast) Toast.erro("Falha ao carregar as redes Wi-Fi.");
      });
  }

  // Esta lista vira a primeira aba; as conexões de Wi-Fi/Bluetooth vêm do componente
  // compartilhado, na mesma barra de abas.
  var abas = UfdrDominio.montar({
    seletor: "#abas-host",
    abas: [{ chave: "redes", rotulo: "Redes Wi-Fi", externo: "#painel-redes" }]
      .concat(UfdrDominio.abasDe(["conexoes"]))
  });

  EntidadesUI.carregarContagens(idInquerito, TP_ORIGEM, function (c) {
    contagens = c || {};
    carregarLista();
  });

  $busca.on("input", aplicarFiltros);
  $senha.on("change", aplicarFiltros);
  $ordenar.on("change", aplicarFiltros);

  // filtros sobrevivem a voltar para a tela (e a URL pode ser compartilhada)
  if (window.EstadoUrl) {
    var u = EstadoUrl.ler(["busca", "senha", "ordenar"]);
    if (u.busca) $busca.val(u.busca);
    if (u.senha) $senha.val(u.senha === "todos" ? "" : u.senha);
    if (u.ordenar) $ordenar.val(u.ordenar);
  }
})();
