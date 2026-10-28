/* Geocodificação em lotes das localizações — window.Geocodificacao.

   O backend resolve no máximo 25 coordenadas novas por chamada (limite de 1 req/s do Nominatim +
   timeout do proxy), então a mesma lista é reenviada em sequência até `restantes` zerar — as já
   resolvidas voltam do cache, rápidas. A Tabela e o Grafo faziam esse laço cada um do seu jeito
   (duplicado e sem cancelamento): trocar de filtro no meio deixava a fila rodando sozinha. Aqui o
   laço existe uma vez só, devolve um controle com `cancelar()` (aborta o pedido em voo e para a
   fila) e avisa quem chamou no fim de cada caminho. */
(function () {
  "use strict";

  /* opts: { idInquerito, coordenadas, aoProgresso(restantes), aoConcluir(feitas),
             aoCancelar(feitas), aoFalhar() }
     Devolve { rodando(), cancelar() }. */
  function emLotes(opts) {
    var url = "/api/investigacao/inqueritos/" + opts.idInquerito + "/localizacoes/geocodificar";
    var xhr = null;
    var cancelado = false;
    var ativo = true;

    function lote(feitasAcum) {
      xhr = $.ajax({
        url: url,
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ coordenadas: opts.coordenadas }),
      }).done(function (resp) {
        xhr = null;
        var feitas = feitasAcum + (resp.geocodificadas || 0);
        if (!cancelado && resp.restantes > 0) {
          if (opts.aoProgresso) opts.aoProgresso(resp.restantes);
          lote(feitas);                       // continua até esvaziar a fila
          return;
        }
        ativo = false;
        if (cancelado) { if (opts.aoCancelar) opts.aoCancelar(feitas); }
        else if (opts.aoConcluir) opts.aoConcluir(feitas);
      }).fail(function (x, tipoErro) {
        xhr = null;
        ativo = false;
        if (cancelado || tipoErro === "abort") {
          if (opts.aoCancelar) opts.aoCancelar(feitasAcum);
          return;
        }
        if (opts.aoFalhar) opts.aoFalhar();
      });
    }

    lote(0);

    return {
      rodando: function () { return ativo; },
      cancelar: function () {
        if (!ativo) return;
        cancelado = true;
        if (xhr) xhr.abort();
      },
    };
  }

  window.Geocodificacao = { emLotes: emLotes };
})();
