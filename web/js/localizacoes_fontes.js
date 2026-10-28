/* ============================================================================
   Seleção de fontes da tela de Localizações.

   A tela mistura dois universos que só se parecem por caírem no mesmo mapa:

     · os APARELHOS do inquérito (cada extração — celular, Takeout), que registram onde o
       telefone esteve;
     · as ANTENAS da operadora (ERBs), que vêm da quebra de sigilo telefônico e dizem por onde a
       LINHA passou, mesmo quando o aparelho não guardou coordenada nenhuma.

   Antes só dava para ver um aparelho de cada vez (?extracao= na URL, escolhido no menu Navegar) e
   as antenas só existiam como uma etiqueta dentro da aba Grafo. Comparar dois celulares do mesmo
   inquérito — que é justamente o que mostra dois investigados no mesmo lugar na mesma hora — não
   tinha como. Este seletor junta as duas coisas num controle só: marque as fontes que entram na
   tela, e tabela, grafo e mapa passam a falar do mesmo conjunto.

   As antenas são uma fonte por OPERADORA, não um interruptor único: o mesmo inquérito pode ter a
   resposta de quebra de sigilo de várias operadoras (um card de telefonia por operadora), e
   desenhar todas juntas enterra a pergunta "de qual ofício veio esta torre?". Então o grupo
   Telefonia lista uma linha por operadora com antena no mapa, cada uma com o próprio interruptor.

   Estado na URL, para o recorte ser compartilhável: ?extracoes=1,2 (aparelhos marcados) e
   ?erbs=1 (todas as operadoras) ou ?erbs=63,66 (só essas operadoras, por id_telefonia).
   Desmarcar TODOS os aparelhos é uma escolha válida — "só as antenas" — e não volta a significar
   "todos".

   Carrega antes de js/localizacoes.js, que consulta este módulo ao montar os filtros.
   ============================================================================ */
(function () {
  "use strict";

  var $barra = $(".arq-filtros").first();

  if (!$barra.length) return;

  var idInquerito = AnalyserUtils.paramUrl("inquerito");
  var idExtracao = AnalyserUtils.paramUrl("extracao");

  if (!idInquerito) return;

  var extracoes = [];          // [{id_extracao, no_dispositivo, ...}]
  var marcadas = null;         // Set de ids (string) — null enquanto a lista não chegou
  var operadoras = [];         // [{id_telefonia, no_operadora, qt}] — operadoras com antena no mapa
  var erbsMarcadas = null;     // Set de id_telefonia (string) — null enquanto a lista não chegou
  var erbsDaUrl = "";          // valor bruto de ?erbs= na URL, para resolver depois da carga
  var carregada = false;
  var operadorasCarregadas = false;

  var assinantes = [];         // mudou a seleção de aparelhos
  var assinantesErbs = [];     // mudou a seleção de operadoras
  var assinantesCarga = [];    // a lista de aparelhos chegou (ou falhou)

  function avisar(lista) {
    lista.forEach(function (fn) { fn(); });
  }

  function nomeExtracao(e) {
    return e.no_dispositivo || e.no_fabricante || e.no_arquivo || ("Extração " + e.id_extracao);
  }

  // ---------- estado inicial ----------

  function selecaoInicial() {
    var daUrl = window.EstadoUrl ? (window.EstadoUrl.ler(["extracoes"]).extracoes || "") : "";
    var todas = extracoes.map(function (e) { return String(e.id_extracao); });

    // ?extracoes= manda, quando traz algum id que existe neste inquérito (a URL é editável, e um
    // id de outro inquérito não pode virar recorte aqui)
    if (daUrl) {
      var ids = daUrl.split(",").filter(function (id) { return todas.indexOf(id) !== -1; });
      if (ids.length) return new Set(ids);
    }
    // veio pelo menu Navegar com um aparelho escolhido: começa nele, e os outros ficam a um
    // clique de distância em vez de exigirem trocar de URL
    if (idExtracao && todas.indexOf(String(idExtracao)) !== -1) return new Set([String(idExtracao)]);
    // veio do botão "ERBs no mapa" (?erbs=..., sem aparelho nenhum na URL): quem clicou ali pediu a
    // telefonia, não o aparelho — abrir com os dois ligados enterraria as antenas debaixo dos
    // pontos do celular. Os aparelhos ficam a um clique, no próprio seletor.
    if (erbsDaUrl) return new Set();
    return new Set(todas);
  }

  function todasMarcadas() {
    return !!marcadas && marcadas.size === extracoes.length;
  }

  /* CSV dos aparelhos marcados, para ?extracoes=. "0" quando nenhum está marcado: id impossível,
     que o backend lê como "nenhum registro de aparelho" — o oposto de omitir o parâmetro, que
     significa "todos". */
  function valorParam() {
    if (!marcadas || todasMarcadas()) return null;
    return marcadas.size ? Array.from(marcadas).join(",") : "0";
  }

  function todasOperadorasMarcadas() {
    return !!erbsMarcadas && erbsMarcadas.size === operadoras.length;
  }

  /* Valor de ?erbs=. "1" quando todas as operadoras estão marcadas — o mesmo sinal dos botões
     "ERBs no mapa" que já existiam, e que continua significando "todas as antenas". Subconjunto
     vira a lista de id_telefonia; nenhuma operadora marcada some da URL. */
  function valorParamErbs() {
    if (!erbsMarcadas || !erbsMarcadas.size) return null;
    if (todasOperadorasMarcadas()) return "1";
    return Array.from(erbsMarcadas).join(",");
  }

  function salvarUrl() {
    if (!window.EstadoUrl) return;
    var patch = {};
    // só mexe na chave quando a lista correspondente já chegou: salvar antes apagaria o valor
    // que ainda não foi resolvido (e a URL é o contrato de compartilhamento desta tela)
    if (marcadas) patch.extracoes = valorParam();
    if (erbsMarcadas) patch.erbs = valorParamErbs();
    if (Object.keys(patch).length) window.EstadoUrl.salvar(patch);
  }

  // ---------- desenho ----------

  var $wrap = $('<div class="loc-fontes">').appendTo($barra);
  var $btn = $('<button type="button" class="arq-select loc-fontes-btn">')
    .attr("aria-haspopup", "true")
    .attr("aria-expanded", "false")
    .attr("title", "Escolhe o que entra na tela: cada aparelho do inquérito e as antenas de cada operadora")
    .appendTo($wrap);
  var $painel = $('<div class="loc-fontes-painel hidden">').appendTo($wrap);

  /* Com zero aparelho marcado a tabela fica legitimamente vazia — e uma tela vazia sem explicação
     parece defeito. Este aviso, fora do painel, diz por que não há nada e onde o que sobrou (as
     antenas) está sendo desenhado. */
  var $aviso = $('<div class="loc-fontes-aviso hidden">').insertAfter($barra);

  function nomesOperadorasAtivas() {
    return operadoras
      .filter(function (op) { return erbsMarcadas.has(String(op.id_telefonia)); })
      .map(function (op) { return op.no_operadora; })
      .join(", ");
  }

  // buscarComCacheSessao resolve null quando a rede falhou: sem a bandeira, o seletor ficaria
  // mudo ("Nenhuma extração neste inquérito.") exatamente quando foi o servidor que não respondeu
  var falhaAparelhos = false;

  function rotuloBotao() {
    var partes = [];
    if (falhaAparelhos) partes.push("falha ao carregar aparelhos");
    else if (!marcadas) partes.push("aparelhos");
    else if (todasMarcadas()) partes.push(extracoes.length === 1 ? "1 aparelho" : "todos os aparelhos");
    else if (!marcadas.size) partes.push("nenhum aparelho");
    else partes.push(marcadas.size + " de " + extracoes.length + " aparelhos");
    if (erbsMarcadas && erbsMarcadas.size) {
      if (todasOperadorasMarcadas()) partes.push("antenas");
      else partes.push("antenas (" + nomesOperadorasAtivas() + ")");
    }
    return "Fontes: " + partes.join(" + ");
  }

  function item(rotulo, detalhe, marcado, aoMudar, titulo) {
    var $l = $('<label class="loc-fontes-item">');
    if (titulo) $l.attr("title", titulo);
    $('<input type="checkbox">').prop("checked", marcado).on("change", function () {
      aoMudar($(this).is(":checked"));
    }).appendTo($l);
    $('<span class="loc-fontes-nome">').text(rotulo).appendTo($l);
    if (detalhe) $('<span class="loc-fontes-detalhe">').text(detalhe).appendTo($l);
    return $l;
  }

  function renderAviso() {
    var nenhum = !!marcadas && !marcadas.size;
    var algumaErb = !!erbsMarcadas && !!erbsMarcadas.size;
    $aviso.toggleClass("hidden", !nenhum).text(!nenhum ? "" : (algumaErb
      ? "Nenhum aparelho selecionado em Fontes: a tela mostra só as antenas da operadora, no mapa da aba Grafo."
      : "Nenhuma fonte selecionada em Fontes: marque um aparelho (ou uma operadora) para ver algo aqui."));
  }

  function render() {
    $btn.text(rotuloBotao());
    renderAviso();
    $painel.empty();

    $('<div class="loc-fontes-grupo">').text("Aparelhos").appendTo($painel);
    if (carregada && !extracoes.length) {
      $('<div class="loc-fontes-vazio">')
        .text(falhaAparelhos ? "Não foi possível carregar os aparelhos. Recarregue a página."
                             : "Nenhuma extração neste inquérito.")
        .appendTo($painel);
    }
    extracoes.forEach(function (e) {
      var id = String(e.id_extracao);
      var fabricante = e.no_fabricante && e.no_fabricante !== e.no_dispositivo ? e.no_fabricante : "";
      $painel.append(item(nomeExtracao(e), fabricante, !!marcadas && marcadas.has(id), function (marcado) {
        if (marcado) marcadas.add(id);
        else marcadas.delete(id);
        aplicar();
      }, e.no_arquivo || ""));
    });

    if (extracoes.length > 1) {
      $('<button type="button" class="loc-fontes-todos">')
        .text(todasMarcadas() ? "Desmarcar todos" : "Marcar todos")
        .on("click", function () {
          var todos = todasMarcadas();
          marcadas = new Set(todos ? [] : extracoes.map(function (e) { return String(e.id_extracao); }));
          aplicar();
        })
        .appendTo($painel);
    }

    // As antenas são do INQUÉRITO, não de um aparelho: entram num grupo próprio, para não parecer
    // que o filtro de aparelho as recorta (ver js/localizacoes_erbs.js). Sem operadora com antena
    // no mapa, o grupo nem existe.
    if (operadorasCarregadas && operadoras.length) {
      $('<div class="loc-fontes-grupo">').text("Telefonia").appendTo($painel);
      operadoras.forEach(function (op) {
        var id = String(op.id_telefonia);
        $painel.append(item(
          op.no_operadora || "Operadora",
          AnalyserUtils.formatarNumero(op.qt) + (Number(op.qt) === 1 ? " antena" : " antenas"),
          !!erbsMarcadas && erbsMarcadas.has(id),
          function (marcado) {
            if (marcado) erbsMarcadas.add(id);
            else erbsMarcadas.delete(id);
            aplicarErbs();
          },
          "Antenas desta operadora no mapa (quebra de sigilo telefônico)."
        ));
      });
      if (operadoras.length > 1) {
        $('<button type="button" class="loc-fontes-todos">')
          .text(todasOperadorasMarcadas() ? "Desmarcar todas" : "Marcar todas")
          .on("click", function () {
            var todas = todasOperadorasMarcadas();
            erbsMarcadas = new Set(todas ? [] : operadoras.map(function (op) { return String(op.id_telefonia); }));
            aplicarErbs();
          })
          .appendTo($painel);
      }
      if (erbsMarcadas && erbsMarcadas.size) {
        $('<div class="loc-fontes-nota">')
          .text("As antenas aparecem no mapa da aba Grafo e não seguem o filtro de aparelho.")
          .appendTo($painel);
      }
    }
  }

  function aplicar() {
    render();
    salvarUrl();
    avisar(assinantes);
  }

  function aplicarErbs() {
    render();
    salvarUrl();
    avisar(assinantesErbs);
  }

  function mesmoConjunto(a, b) {
    if (!a || !b) return a === b;
    if (a.size !== b.size) return false;
    var igual = true;
    a.forEach(function (v) { if (!b.has(v)) igual = false; });
    return igual;
  }

  // Interruptor-mestre da etiqueta do mapa (todas/nenhuma): quem liga/desliga por operadora é o
  // seletor. Marcar aqui é "todas as operadoras"; desmarcar é "nenhuma".
  function definirTodasErbs(valor) {
    if (!operadoras.length) return;
    var alvo = valor
      ? new Set(operadoras.map(function (op) { return String(op.id_telefonia); }))
      : new Set();
    if (mesmoConjunto(alvo, erbsMarcadas)) return;
    erbsMarcadas = alvo;
    aplicarErbs();
  }

  // ---------- abrir/fechar ----------

  function fechar() {
    $painel.addClass("hidden");
    $btn.attr("aria-expanded", "false");
  }

  $btn.on("click", function (e) {
    e.stopPropagation();
    var fechado = $painel.hasClass("hidden");
    $painel.toggleClass("hidden", !fechado);
    $btn.attr("aria-expanded", String(fechado));
  });

  $(document).on("click", function (e) {
    if (!$(e.target).closest(".loc-fontes").length) fechar();
  });

  $(document).on("keydown", function (e) {
    if (e.key === "Escape") fechar();
  });

  // ---------- carga ----------

  erbsDaUrl = window.EstadoUrl ? (window.EstadoUrl.ler(["erbs"]).erbs || "") : "";
  render();

  function pronto() {
    carregada = true;
    render();
    avisar(assinantesCarga);
    assinantesCarga = [];
  }

  window.AnalyserUtils.buscarComCacheSessao(
    "nav:extracoes:" + idInquerito,
    "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/extracoes-lista"
  // buscarComCacheSessao nunca rejeita: falha vira `null`, e aí a lista fica vazia — nenhum
  // aparelho para marcar significa nenhum recorte, que é como a tela se comportava antes.
  ).done(function (lista) {
    falhaAparelhos = lista === null;
    extracoes = lista || [];
    marcadas = selecaoInicial();
    pronto();
  });

  // As operadoras vêm do bloco de telefonia do inquérito (um card por operadora), e não da camada
  // de antenas: o seletor fala do INQUÉRITO, então a lista e a contagem não mudam quando um filtro
  // temporal recorta a camada — senão a operadora sem antena no período sumiria da lista e não
  // daria para religá-la. Só entra operadora com antena georreferenciada, que é o que a camada
  // desenha.
  window.AnalyserUtils.buscarComCacheSessao(
    "nav:telefonia:" + idInquerito,
    "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) + "/telefonia"
  ).done(function (cards) {
    operadoras = (cards || [])
      .filter(function (t) { return Number(t.qt_erbs_no_mapa) > 0; })
      .map(function (t) {
        return { id_telefonia: t.id_telefonia, no_operadora: t.no_operadora, qt: t.qt_erbs_no_mapa };
      });
    operadorasCarregadas = true;
    erbsMarcadas = (erbsDaUrl === "1")
      ? new Set(operadoras.map(function (op) { return String(op.id_telefonia); }))
      : (erbsDaUrl
          ? new Set(erbsDaUrl.split(",").filter(function (id) {
              return operadoras.some(function (op) { return String(op.id_telefonia) === id; });
            }))
          : new Set());
    render();
    salvarUrl();
    avisar(assinantesErbs);
  });

  window.LocalizacoesFontes = {
    // CSV para ?extracoes= (null = sem recorte por aparelho)
    param: valorParam,
    // true quando este módulo já manda no recorte de aparelho (aí a tela não usa mais ?extracao=)
    ativo: function () { return !!marcadas; },
    aoMudar: function (fn) { assinantes.push(fn); },
    // roda assim que a lista de aparelhos chega (ou já, se ela chegou/falhou antes)
    aoCarregar: function (fn) { if (carregada) fn(); else assinantesCarga.push(fn); },

    // Set de id_telefonia das operadoras marcadas (vazio = nenhuma antena). A camada de antenas
    // filtra por ele (js/localizacoes_erbs.js).
    operadorasAtivas: function () { return erbsMarcadas || new Set(); },
    erbsAtivas: function () { return !!erbsMarcadas && erbsMarcadas.size > 0; },
    definirTodasErbs: definirTodasErbs,
    aoMudarErbs: function (fn) { assinantesErbs.push(fn); },
  };
})();
