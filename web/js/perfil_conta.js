(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) {
    if (!iso) return null;
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z")).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  var idInquerito = paramUrl("inquerito");
  var idExtracao = paramUrl("extracao");

  var $total = $("#total-registros");
  var $perfis = $("#perfis");
  var $vazio = $("#vazio");

  if (!idInquerito) {
    $vazio.text("Parâmetros inválidos na URL.").show();
    return;
  }

  function campo($cartao, rotulo, valor) {
    if (!valor) return;
    var $l = $("<div>").addClass("prf-campo").appendTo($cartao);
    $("<div>").addClass("prf-rot").text(rotulo).appendTo($l);
    $("<div>").addClass("prf-val").text(valor).appendTo($l);
  }

  function renderPerfil(p) {
    var $cartao = $("<div>").addClass("card prf-cartao").attr("data-id", p.id_perfil);
    $("<h2>").text(p.no_nome || p.tx_email || "Perfil da conta").appendTo($cartao);
    campo($cartao, "Nome completo", p.no_nome);
    campo($cartao, "Nome", p.no_nome_dado);
    campo($cartao, "Sobrenome", p.no_nome_familia);
    campo($cartao, "E-mail", p.tx_email);
    campo($cartao, "E-mails alternativos", p.tx_email_alternativos);
    campo($cartao, "Conta criada em", formatarDataHora(p.dt_criacao));
    campo($cartao, "Nascimento", p.tx_nascimento);
    campo($cartao, "País", p.tx_pais);
    campo($cartao, "Idioma", p.tx_idioma);
    campo($cartao, "IP nos termos de serviço", p.tx_ip_tos);
    campo($cartao, "Serviços", p.tx_servicos);
    campo($cartao, "Última atualização", formatarDataHora(p.dt_ultima_atualizacao));
    campo($cartao, "Exclusão", formatarDataHora(p.dt_exclusao));
    campo($cartao, "Status", p.tx_status);
    campo($cartao, "Fonte", p.no_fonte);
    return $cartao;
  }

  var params = {};
  if (idExtracao) params.extracao = idExtracao;

  $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/perfil-conta", params).done(function (perfis) {
    $total.text((perfis || []).length.toLocaleString("pt-BR") + " perfil(is)");
    if (!perfis || !perfis.length) {
      $vazio.show();
      return;
    }
    perfis.forEach(function (p) { $perfis.append(renderPerfil(p)); });
    var dest = EstadoUrl.ler(["destacar"]).destacar;
    if (dest) {
      var $linha = $perfis.find('[data-id="' + dest + '"]');
      if ($linha.length) {
        $linha.addClass("tp-destacada");
        $linha[0].scrollIntoView({ block: "center" });
        EstadoUrl.salvar({ destacar: null });
      }
    }
  }).fail(function () {
    $vazio.text("Não foi possível carregar o perfil da conta.").show();
  });
})();
