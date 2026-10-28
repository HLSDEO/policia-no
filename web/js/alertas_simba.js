(function () {
  "use strict";

  var ICONE_SALVAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';

  var $form = $("#simba-alertas-form");
  var $limite = $("#sa-limite-comunicacao");
  var $minFrac = $("#sa-min-fracionamento");
  var $multiplo = $("#sa-multiplo-redondo");
  var $giroPass = $("#sa-giro-passagem");
  var $tolPass = $("#sa-tolerancia-passagem");
  var $minLancPass = $("#sa-min-lanc-passagem");
  var $saveBtn = $("#sa-save-btn");
  var $status = $("#sa-form-status");
  var padraoFabrica = null;

  function setStatus(text, kind) {
    $status.text(text).removeClass("success error").addClass(kind || "");
  }

  function preencher(pesos) {
    $limite.val(pesos.limiteComunicacao);
    $minFrac.val(pesos.minFracionamento);
    $multiplo.val(pesos.multiploRedondo);
    if ($giroPass.length) $giroPass.val(pesos.giroPassagem);
    if ($tolPass.length) $tolPass.val(pesos.toleranciaPassagemPct);
    if ($minLancPass.length) $minLancPass.val(pesos.minLancamentosPassagem);
  }

  function carregar() {
    setStatus("");
    $.getJSON("/api/config/simba-alertas")
      .done(function (resp) {
        padraoFabrica = resp.padrao || null;
        preencher(resp);
      })
      .fail(function () {
        setStatus("Erro ao carregar a configuração atual.", "error");
      });
  }

  $("#sa-restaurar-btn").on("click", function () {
    if (!padraoFabrica) return;
    preencher({
      limiteComunicacao: padraoFabrica.limite_comunicacao,
      minFracionamento: padraoFabrica.min_fracionamento,
      multiploRedondo: padraoFabrica.multiplo_redondo,
      giroPassagem: padraoFabrica.giro_passagem,
      toleranciaPassagemPct: padraoFabrica.tolerancia_passagem_pct,
      minLancamentosPassagem: padraoFabrica.min_lancamentos_passagem
    });
  });

  /* Lê um campo numérico e exige inteiro positivo. O `$c.val && $c.val() || padrão` antigo não
   * colava (val() sempre existe em jQuery) e, pior, engolia campo vazio OU zero como o padrão do
   * fabricante — o administrador salvava "50000" achando que tinha posto outra coisa. Agora campo
   * inválido barra o submit com mensagem em vez de virar número silencioso. */
  function lerPositivo($c, padrao) {
    if (!$c.length) return padrao;
    var bruto = $.trim(String($c.val() == null ? "" : $c.val()));
    if (!bruto) return padrao;
    var n = parseInt(bruto, 10);
    return isFinite(n) && n > 0 ? n : NaN;
  }

  $form.on("submit", function (e) {
    e.preventDefault();
    setStatus("");

    var giro = lerPositivo($giroPass, 50000);
    var tol = lerPositivo($tolPass, 10);
    var minLanc = lerPositivo($minLancPass, 10);
    if (isNaN(giro) || isNaN(tol) || isNaN(minLanc)) {
      setStatus("Informe números inteiros positivos em giro, tolerância e lançamentos mínimos (ou deixe em branco para usar o padrão).", "error");
      return;
    }

    $saveBtn.prop("disabled", true).html('<span class="spinner"></span> Salvando…');

    var payload = {
      limiteComunicacao: parseInt($limite.val(), 10) || 0,
      minFracionamento: parseInt($minFrac.val(), 10) || 0,
      multiploRedondo: parseInt($multiplo.val(), 10) || 0,
      giroPassagem: giro,
      toleranciaPassagemPct: tol,
      minLancamentosPassagem: minLanc
    };

    $.ajax({
      url: "/api/config/simba-alertas",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(payload)
    })
      .done(function () {
        setStatus("Configuração salva.", "success");
        carregar();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao salvar.";
        setStatus(typeof msg === "string" ? msg : "Erro ao salvar.", "error");
      })
      .always(function () {
        $saveBtn.prop("disabled", false).html(ICONE_SALVAR + " Salvar");
      });
  });

  carregar();
})();
