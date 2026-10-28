/* Tela Administração → Desempenho.
 *
 * Mostra o hardware detectado, o valor que as fórmulas recomendam para ele, o que está gravado
 * no .env e o que o processo está de fato usando — e deixa fixar qualquer um deles.
 *
 * A lista de parâmetros NÃO está aqui: vem inteira da API (funcoes/desempenho_utils.PARAMETROS),
 * com rótulo, grupo, tipo e texto de ajuda. Acrescentar um parâmetro é mexer só no Python; esta
 * tela é um laço genérico sobre o que chegar.
 *
 * A distinção que a tela existe para deixar clara é "em vigor" x "fixar em": quase nada aqui vale
 * na hora (pool, threads e caches são lidos no startup da API; os PG_* pelo compose ao subir o
 * Postgres), e gravar sem avisar disso seria pior que não ter a tela. */
(function () {
  "use strict";

  var $hardware = $("#hardware-resumo");
  var $grupos = $("#grupos");
  var $perfil = $("#perfil");
  var $status = $("#status");
  var $aviso = $("#aviso-reinicio");
  var $salvar = $("#salvar-btn");
  var $reiniciar = $("#reiniciar-btn");

  // Sondagem do reinício: de quanto em quanto tempo pergunta se já voltou, e o teto antes de
  // desistir e pedir para recarregar na mão (um `up -d` com Postgres frio passa de 1 min).
  var POLL_REINICIO_MS = 3000;
  var TIMEOUT_REINICIO_MS = 240000;
  var reiniciando = false;

  var parametros = [];

  function formatarBytes(n) {
    var gb = Number(n) / (1024 * 1024 * 1024);
    if (!isFinite(gb) || gb <= 0) return "—";
    return (gb >= 10 ? gb.toFixed(0) : gb.toFixed(1)) + " GB";
  }

  // "cgroup" = o container tem limite configurado e é ele que manda; "host" = o container
  // enxerga a máquina inteira. A diferença importa: dimensionar cache pela RAM do host num
  // container limitado é pedir para o OOM killer resolver.
  function rotuloOrigem(origem) {
    return origem === "cgroup" ? "limite do container" : "máquina inteira";
  }

  function bloco(rotulo, valor, detalhe) {
    var $b = $("<div>").addClass("desemp-metrica");
    $("<span>").addClass("desemp-metrica-rotulo").text(rotulo).appendTo($b);
    $("<strong>").addClass("desemp-metrica-valor").text(valor).appendTo($b);
    if (detalhe) $("<span>").addClass("desemp-metrica-detalhe").text(detalhe).appendTo($b);
    return $b;
  }

  function renderHardware(d) {
    var hw = d.hardware || {};
    var base = d.base || {};
    $hardware.empty();
    $hardware.append(bloco("Núcleos", String(hw.cpus || "—"), rotuloOrigem(hw.origem_cpu)));
    $hardware.append(bloco("Memória", formatarBytes(hw.memoria_bytes), rotuloOrigem(hw.origem_memoria)));

    // Só vale mostrar a base do cálculo quando ela difere do detectado, isto é, quando um perfil
    // fixo está escolhido — senão é a mesma coisa dita duas vezes.
    if (base.cpus !== hw.cpus || base.memoria_bytes !== hw.memoria_bytes) {
      $hardware.append(bloco("Calculando como",
        base.cpus + " núcleos / " + formatarBytes(base.memoria_bytes), "perfil fixado"));
    }
  }

  function renderParametros(lista) {
    $grupos.empty();
    var ordem = [];
    var porGrupo = {};
    lista.forEach(function (p) {
      if (!porGrupo[p.grupo]) { porGrupo[p.grupo] = []; ordem.push(p.grupo); }
      porGrupo[p.grupo].push(p);
    });

    ordem.forEach(function (grupo) {
      $("<h3>").addClass("desemp-grupo-titulo").text(grupo).appendTo($grupos);

      var $tabela = $("<table>").addClass("grupo-tabela desemp-tabela");
      $("<thead>").append(
        $("<tr>")
          .append($("<td>").text("Parâmetro"))
          .append($("<td>").text("Recomendado"))
          .append($("<td>").text("Em vigor"))
          .append($("<td>").text("Fixar em"))
      ).appendTo($tabela);
      var $corpo = $("<tbody>").appendTo($tabela);

      porGrupo[grupo].forEach(function (p) {
        var $tr = $("<tr>").appendTo($corpo);

        var $nome = $("<td>").appendTo($tr);
        $("<div>").addClass("desemp-rotulo").text(p.rotulo).appendTo($nome);
        $("<div>").addClass("field-help").text(p.ajuda).appendTo($nome);

        // data-rotulo alimenta o ::before do layout estreito (css/desempenho.css), onde o
        // cabeçalho da tabela some e cada número precisa dizer sozinho o que é.
        $("<td>").addClass("desemp-num").attr("data-rotulo", "Recomendado").text(p.recomendado).appendTo($tr);
        $("<td>").addClass("desemp-num").attr("data-rotulo", "Em vigor").text(p.efetivo).appendTo($tr);

        var $campo = $("<td>").appendTo($tr);
        $("<input>")
          .attr({ type: "text", placeholder: "automático", "aria-label": p.rotulo })
          .addClass("desemp-input")
          .data("chave", p.chave)
          .val(p.configurado)
          .appendTo($campo);
        $("<div>").addClass("field-help").text(p.chave).appendTo($campo);
      });

      $tabela.appendTo($grupos);
    });
  }

  /* O que já foi salvo mas ainda não está valendo — a única informação desta tela que o usuário
   * não consegue deduzir sozinho. Compara o que VAI valer (o valor fixado, ou o recomendado
   * quando o campo está vazio) com o que o processo carregou no boot. */
  function renderAviso(lista) {
    var pendentes = lista.filter(function (p) {
      // "proximo" é o que valerá no próximo start (o fixado, ou o recomendado); "efetivo" é o
      // que o processo e o banco estão usando agora. Divergiu, falta reiniciar.
      return String(p.proximo) !== String(p.efetivo);
    });

    $aviso.empty();
    if (!pendentes.length) {
      $aviso.hide();
      return;
    }

    var alvos = [];
    if (pendentes.some(function (p) { return p.quando === "api"; })) alvos.push("a API");
    if (pendentes.some(function (p) { return p.quando === "banco"; })) alvos.push("o Postgres");
    // "container": entra no comando do uvicorn (API_WORKERS), então um restart do processo não
    // basta — o container precisa ser recriado. O comando mostrado embaixo já é o certo.
    var recriar = pendentes.some(function (p) { return p.quando === "container"; });
    if (recriar) alvos.push("o container da API (recriar, não só reiniciar)");

    $("<strong>").text("Falta reiniciar " + alvos.join(" e ") + ".").appendTo($aviso);
    $("<p>").text(
      "Estes valores já estão gravados, mas o processo continua com os antigos: " +
      pendentes.map(function (p) { return p.rotulo; }).join(", ") + "."
    ).appendTo($aviso);
    $("<code>").addClass("desemp-comando").text("docker compose up -d").appendTo($aviso);
    $aviso.show();
  }

  function aplicar(d) {
    parametros = d.parametros || [];
    $perfil.val(d.perfil || "auto");
    renderHardware(d);
    renderParametros(parametros);
    renderAviso(parametros);
  }

  function carregar() {
    Carregando.emBloco($grupos);
    Api.get("/api/config/desempenho")
      .done(aplicar)
      .fail(function (xhr) {
        Estados.erro($grupos, {
          mensagem: Api.mensagemErro(xhr, "Não foi possível ler a configuração."),
          aoTentarDeNovo: carregar
        });
      });
  }

  function salvar() {
    var valores = {};
    $(".desemp-input").each(function () {
      valores[$(this).data("chave")] = $.trim($(this).val());
    });

    $status.text("");
    Carregando.botao($salvar, "Salvando…");
    Api.post("/api/config/desempenho", { perfil: $perfil.val(), valores: valores })
      .done(function (d) {
        aplicar(d);
        Toast.sucesso("Configuração salva.");
      })
      .fail(function (xhr) {
        // 400 não dispara o toast global (ver js/utils/api.js), e a mensagem de validação diz
        // qual campo está errado — precisa aparecer aqui, ao lado do botão.
        $status.text(Api.mensagemErro(xhr, "Não foi possível salvar."));
      })
      .always(function () { Carregando.restaurar($salvar); });
  }

  $salvar.on("click", salvar);

  // ---------- Reiniciar servidor (`docker compose up -d`) ----------

  // GET silencioso: durante o restart o esperado é a API NÃO responder, e cada tentativa
  // falhada não pode virar um toast "Sem conexão" (ver js/utils/api.js — erroSilencioso).
  function getSilencioso(url) {
    return Api.enviar({ url: url, method: "GET", erroSilencioso: true });
  }

  // Espera o pedido ser consumido pelo host e a API nova responder com a config: só aí os
  // valores "em vigor" são os de verdade e faz sentido recarregar a tela.
  function esperarApiVoltar(inicio) {
    if (Date.now() - inicio > TIMEOUT_REINICIO_MS) {
      Carregando.restaurar($reiniciar);
      reiniciando = false;
      $status.text("");
      Toast.aviso("O servidor ainda não respondeu — aguarde mais um pouco e recarregue a página.");
      return;
    }
    getSilencioso("/api/sistema/reiniciar")
      .done(function (st) {
        // Resultado do host: o compose falhou lá (ex.: shared_buffers acima da memória
        // compartilhada) — mostra o motivo em vez de esperar um servidor que já voltou com
        // os valores antigos.
        if (st && st.resultado && !st.resultado.ok && !st.reiniciando) {
          Carregando.restaurar($reiniciar);
          reiniciando = false;
          $status.text("");
          Dialogo.alerta({
            titulo: "Reinício falhou",
            mensagem: (st.resultado.erro || "O host não conseguiu subir os containers.") +
              "\n\nRode no servidor: " + (st.comando || "docker compose up -d")
          });
          return;
        }
        if (st && st.reiniciando) {
          setTimeout(function () { esperarApiVoltar(inicio); }, POLL_REINICIO_MS);
          return;
        }
        getSilencioso("/api/config/desempenho")
          .done(function (d) {
            aplicar(d);
            Carregando.restaurar($reiniciar);
            reiniciando = false;
            $status.text("");
            Toast.sucesso("Servidor reiniciado — valores em vigor atualizados.");
          })
          .fail(function () {
            setTimeout(function () { esperarApiVoltar(inicio); }, POLL_REINICIO_MS);
          });
      })
      .fail(function () {
        // API ainda derrubada pelo compose: é o meio do caminho esperado, não um erro.
        setTimeout(function () { esperarApiVoltar(inicio); }, POLL_REINICIO_MS);
      });
  }

  function reiniciar() {
    if (reiniciando) return;
    Dialogo.confirmar({
      titulo: "Reiniciar servidor",
      mensagem: "Isto executa `docker compose up -d` no servidor: a plataforma fica indisponível " +
        "por 1–2 minutos e os valores já salvos passam a valer. " +
        "Alterações não salvas nesta tela serão perdidas. Continuar?",
      rotuloOk: "Reiniciar"
    }).then(function (ok) {
      if (!ok) return;
      reiniciando = true;
      $status.text("Reiniciando o servidor…");
      Carregando.botao($reiniciar, "Reiniciando…");
      Api.post("/api/sistema/reiniciar", {})
        .done(function (r) {
          if (!r.aceito) {
            Carregando.restaurar($reiniciar);
            reiniciando = false;
            // Sem agente e sem docker ao alcance, a tela não tem como reiniciar sozinha — o
            // comando para rodar no servidor é a saída útil.
            Dialogo.alerta({
              titulo: "Não foi possível reiniciar por aqui",
              mensagem: (r.erro || "Reinício não disparado.") +
                "\n\nRode no servidor: " + (r.comando || "docker compose up -d")
            });
            $status.text(r.erro || "");
            return;
          }
          Toast.info("Reinício iniciado — aguarde a plataforma voltar.");
          // Via direta o pedido não deixa rastro (não há .req): dá alguns segundos para o
          // compose começar a derrubar os containers antes de sondar, senão a primeira leitura
          // pega a API antiga e finge que já voltou.
          var esperaInicial = r.via === "direto" ? 8000 : 1500;
          setTimeout(function () { esperarApiVoltar(Date.now()); }, esperaInicial);
        })
        .fail(function (xhr) {
          Carregando.restaurar($reiniciar);
          reiniciando = false;
          $status.text(Api.mensagemErro(xhr, "Não foi possível pedir o reinício."));
        });
    });
  }

  $reiniciar.on("click", reiniciar);

  // Se a página foi recarregada no meio de um reinício (ou outro admin disparou), retoma o
  // acompanhamento em vez de deixar o botão parado com valores desatualizados.
  getSilencioso("/api/sistema/reiniciar")
    .done(function (st) {
      if (st && st.reiniciando && !reiniciando) {
        reiniciando = true;
        $status.text("Reinício em andamento…");
        Carregando.botao($reiniciar, "Reiniciando…");
        esperarApiVoltar(Date.now());
      }
    });

  $("#recomendados-btn").on("click", function () {
    $(".desemp-input").val("");
    $status.text("Campos limpos — salve para voltar ao dimensionamento automático.");
  });

  // Trocar o perfil muda o que as fórmulas recomendam. Recarrega a prévia sem gravar: dá para
  // ver com que valores o sistema subiria num servidor menor antes de decidir salvar.
  $perfil.on("change", function () {
    var escolhido = $perfil.val();
    Api.get("/api/config/desempenho", { perfil: escolhido }).done(function (d) {
      aplicar(d);
      $status.text(escolhido === "auto"
        ? "Prévia com o hardware detectado. Salve para aplicar."
        : "Prévia com o perfil " + escolhido + ". Salve para aplicar.");
    });
  });

  carregar();
})();
