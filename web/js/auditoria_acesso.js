/* Auditoria de acesso a inquérito (tela Auditoria) — auditoria e administradores: lista as
 * janelas de acesso registradas em seguranca.usr_tb05_acessos_inquerito (quem leu o quê, de qual
 * inquérito, quando, e o que foi barrado por falta de permissão). O card fica escondido e só
 * aparece se o GET responder (perfil com pode_auditoria), igual ao de Auditoria de IA.
 *
 * Cada linha é uma JANELA DE UMA HORA, não uma requisição: ver o porquê da agregação em
 * db/init/07_seguranca_usuarios.sql. */
(function () {
  "use strict";

  var PAGINA = 100;

  var $card = $("#acessos-card");
  var $corpo = $("#acc-corpo");
  var $vazio = $("#acc-vazio");
  var $filtroPeriodo = $("#acc-filtro-periodo");
  var $filtroNegados = $("#acc-filtro-negados");
  var $atualizarBtn = $("#acc-atualizar-btn");
  var $paginacao = $("#acc-paginacao");
  var $pagInfo = $("#acc-pag-info");
  var $pagAnterior = $("#acc-pag-anterior");
  var $pagProximo = $("#acc-pag-proximo");

  var estado = { pagina: 1, total: 0 };

  function totalPaginas() { return Math.max(1, Math.ceil(estado.total / PAGINA)); }

  var TZ = "America/Sao_Paulo";

  /* O banco guarda TIMESTAMP sem fuso, gravado com now() em UTC. Sem o "Z" o browser lê o ISO
   * como hora local e a trilha aparece 3 h adiantada; e mesmo com o Z, sem timeZone fixo o
   * horário mudaria conforme a máquina de quem lê — numa auditoria isso não pode acontecer.
   * Mesmo padrão das demais telas (ver js/acessos.js). */
  function dataUtc(iso) {
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
  }

  function hora(iso) {
    if (!iso) return "—";
    return dataUtc(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  }

  function fmtPeriodo(item) {
    if (!item.dt_janela) return "—";
    var dia = dataUtc(item.dt_janela).toLocaleDateString("pt-BR", { timeZone: TZ });
    // a janela é de uma hora, mas o que importa ler é o intervalo real de uso dentro dela
    return dia + " " + hora(item.dt_primeiro_acesso) + "–" + hora(item.dt_ultimo_acesso);
  }

  function fmtRecurso(caminho) {
    if (!caminho || caminho === "-") return "—";
    // o prefixo /api/... é igual em todas as linhas e só rouba largura da coluna
    return caminho.replace(/^\/api\/(investigacao|entidades|padroes|relatorios|ia|documentos)\//, "");
  }

  function renderKpis(totais, qt) {
    $("#acc-kpi-total").text(qt.toLocaleString("pt-BR"));
    $("#acc-kpi-requisicoes").text(Number(totais.qt_requisicoes || 0).toLocaleString("pt-BR"));
    $("#acc-kpi-usuarios").text(Number(totais.qt_usuarios || 0).toLocaleString("pt-BR"));
    $("#acc-kpi-inqueritos").text(Number(totais.qt_inqueritos || 0).toLocaleString("pt-BR"));
    var negadas = Number(totais.qt_janelas_negadas || 0);
    var $kpiNegadas = $("#acc-kpi-negadas");
    $kpiNegadas.text(negadas.toLocaleString("pt-BR"));
    $kpiNegadas.css("color", negadas ? "var(--danger)" : "");
  }

  function renderLinha(item) {
    var $tr = $("<tr>");
    $("<td>").addClass("col-nome").text(fmtPeriodo(item)).appendTo($tr);
    $("<td>").addClass("col-nome").text(item.no_usuario || item.no_login || ("usuário " + item.id_usuario)).appendTo($tr);
    // inquérito apagado continua na trilha, mas sem número para mostrar
    $("<td>").addClass("col-nome")
      .text(item.nu_inquerito || ("#" + item.id_inquerito + " (apagado)"))
      .appendTo($tr);
    $("<td>").addClass("col-nome")
      .css({ maxWidth: "320px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" })
      .attr("title", item.no_recurso || "")
      .text(fmtRecurso(item.no_recurso)).appendTo($tr);
    $("<td>").addClass("col-nome").text(item.tp_metodo || "—").appendTo($tr);
    $("<td>").addClass("col-nome").text(Number(item.qt_acessos || 0).toLocaleString("pt-BR")).appendTo($tr);
    $("<td>").addClass("col-nome").text(item.co_ip || "—").appendTo($tr);
    $("<td>").addClass("col-nome").append(
      $("<span>").addClass("badge " + (item.in_permitido ? "badge-ok" : "badge-erro"))
        .text(item.in_permitido ? "permitido" : "negado")
    ).appendTo($tr);
    return $tr;
  }

  function renderPaginacao() {
    var tp = totalPaginas();
    $pagInfo.text("Página " + estado.pagina + " de " + tp.toLocaleString("pt-BR") +
      " · " + estado.total.toLocaleString("pt-BR") + " registro(s)");
    $pagAnterior.prop("disabled", estado.pagina <= 1);
    $pagProximo.prop("disabled", estado.pagina >= tp);
    $paginacao.css("display", estado.total > PAGINA ? "flex" : "none");
  }

  function carregar(resetarPagina) {
    if (resetarPagina) estado.pagina = 1;
    var params = { limite: PAGINA, deslocamento: (estado.pagina - 1) * PAGINA };
    var dias = $filtroPeriodo.val();
    if (dias) params.dias = dias;
    if ($filtroNegados.is(":checked")) params.somente_negados = true;

    $.ajax({ url: "/api/admin/acessos", method: "GET", data: params })
      .done(function (dados) {
        $card.removeClass("hidden");
        $corpo.empty();
        (dados.itens || []).forEach(function (item) {
          $corpo.append(renderLinha(item));
        });
        estado.total = dados.qt_total || 0;
        renderKpis(dados.totais || {}, estado.total);
        renderPaginacao();
        $vazio.toggleClass("hidden", estado.total > 0);
      });
    // .fail silencioso de propósito: usuário sem permissão de admin (403) só não vê o card
  }

  $atualizarBtn.on("click", function () { carregar(true); });
  $filtroPeriodo.on("change", function () { carregar(true); });
  $filtroNegados.on("change", function () { carregar(true); });
  $pagAnterior.on("click", function () {
    if (estado.pagina > 1) { estado.pagina--; carregar(false); }
  });
  $pagProximo.on("click", function () {
    if (estado.pagina < totalPaginas()) { estado.pagina++; carregar(false); }
  });

  carregar(true);
})();
