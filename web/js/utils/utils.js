/* Utilitários compartilhados de formatação/escape e um cache curto de sessão (sessionStorage),
 * para não duplicar em cada página. Expõe window.AnalyserUtils. Carregar logo após
 * jquery/dialogo.js, antes dos scripts de página. */
(function () {
  "use strict";

  // datetime completo ("2026-02-23T14:05:00") — string sem timezone é tratada como UTC (Z) e
  // convertida para o horário de Brasília na exibição.
  function formatarDataHora(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"))
      .toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // mesma regra de formatarDataHora, só a data (sem hora).
  function formatarDataSimples(iso) {
    if (!iso) return "—";
    return new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"))
      .toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  // data pura ("2026-02-23", sem hora) — string, não passa por Date/timezone pra não escorregar de dia.
  function formatarData(iso) {
    if (!iso) return "—";
    var m = String(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(iso);
  }

  function formatarNumero(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("pt-BR");
  }

  function formatarReais(v) {
    var n = Number(v);
    if (!isFinite(n)) return "R$ 0,00";
    return "R$ " + n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Formata o número do inquérito (ex.: 20200011431 -> 2020.0011431; 13 dígitos com DV -> 2020.0011431-00)
  function formatarNumeroInquerito(nu) {
    if (!nu) return "";
    var str = String(nu).trim();
    var digitos = str.replace(/\D/g, "");
    if (digitos.length === 11) {
      return digitos.slice(0, 4) + "." + digitos.slice(4);
    }
    if (digitos.length === 13) {
      return digitos.slice(0, 4) + "." + digitos.slice(4, 11) + "-" + digitos.slice(11);
    }
    if (digitos.length > 4 && digitos.length < 11) {
      return digitos.slice(0, 4) + "." + digitos.slice(4);
    }
    return str;
  }

  // Só o nome do arquivo, sem a estrutura de pastas. Desde a organização por inquérito
  // (docs/organizacao_arquivos.md), o back-end guarda em NO_ARQUIVO/NO_PASTA o caminho relativo
  // dentro de data/ — "SIP/2025.0001/CELULAR/NB2026.ufdr" —, e é isso que chega nas listagens.
  // Na tela interessa só a folha: onde o arquivo está no servidor é ruído no card.
  function nomeArquivo(caminho) {
    if (caminho == null || caminho === "") return caminho;
    var partes = String(caminho).replace(/\\/g, "/").replace(/\/+$/, "").split("/");
    return partes[partes.length - 1] || String(caminho);
  }

  // Aplica nomeArquivo nos campos de caminho de um objeto — ou de cada item de uma lista — assim
  // que ela chega da API, para não ter que lembrar de encurtar em cada ponto que exibe. Devolve o
  // que recebeu (altera no lugar).
  function encurtarCaminhos(dados) {
    var campos = ["no_arquivo", "no_pasta", "no_arquivo_ufdr"];
    (Array.isArray(dados) ? dados : [dados]).forEach(function (item) {
      if (!item || typeof item !== "object") return;
      campos.forEach(function (campo) {
        if (typeof item[campo] === "string") item[campo] = nomeArquivo(item[campo]);
      });
    });
    return dados;
  }

  /* Um parâmetro da query string da página. É o helper mais copiado do projeto — 48 definições
   * locais idênticas, e nenhuma aqui, que é o motivo de terem sido escritas de novo a cada tela.
   * `padrao` cobre a variante de duas assinaturas que circulava: sem ele o retorno é null quando
   * o parâmetro não veio, como no URLSearchParams cru. */
  function paramUrl(nome, padrao) {
    var valor = new URLSearchParams(window.location.search).get(nome);
    return valor !== null ? valor : (padrao !== undefined ? padrao : null);
  }

  // tamanho de arquivo legível. B e KB/MB com uma casa, GB com duas — a escala em que o
  // investigador compara .ufdr inteiros.
  function formatarTamanho(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    var n = Number(bytes);
    if (!isFinite(n)) return "—";
    if (n < 1024) return n + " B";
    var kb = n / 1024;
    if (kb < 1024) return kb.toFixed(1) + " KB";
    var mb = kb / 1024;
    if (mb < 1024) return mb.toFixed(1) + " MB";
    return (mb / 1024).toFixed(2) + " GB";
  }

  // corta no limite e marca com reticências (conta como 1 caractere, então o resultado nunca
  // passa de `max`).
  function truncar(texto, max) {
    if (!texto) return "";
    var s = String(texto);
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  }

  // adia a chamada até `ms` sem novo disparo — usado nos campos de busca de todas as telas de
  // filtro, para não mandar uma requisição por tecla.
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  // escapa dado não confiável (vindo de extração) antes de entrar em .html()/innerHTML.
  function escaparHtml(valor) {
    return String(valor == null ? "" : valor)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* Cache curto em sessionStorage para o que praticamente não muda enquanto se navega dentro de
   * um inquérito (o número dele, a lista de aparelhos) — sem isso cada página repetiria as mesmas
   * requisições só pra escrever um rótulo. Quem usa: js/utils/sidebar.js (o item "Inquérito
   * 2025.00001" do menu) e js/utils/navegacao_inquerito.js (trilha e menu "Navegar"), pelas mesmas
   * chaves: o primeiro a rodar busca, o segundo lê do cache.
   *
   * Falha de rede resolve `null` em vez de rejeitar — quem chamou decide o que mostrar sem
   * precisar de .fail() (e nada é guardado, então a próxima página tenta de novo). */
  var TTL_CACHE_SESSAO = 5 * 60 * 1000;

  // devolve `undefined` quando não há nada válido guardado (e não `null`, que pode ser o dado)
  function lerCacheSessao(chave, ttlMs) {
    var bruto = null;
    try {
      bruto = sessionStorage.getItem(chave);
    } catch (e) {
      return undefined; // sessionStorage indisponível (navegador em modo restrito)
    }
    if (!bruto) return undefined;
    try {
      var guardado = JSON.parse(bruto);
      if (guardado && Date.now() - guardado.em < (ttlMs || TTL_CACHE_SESSAO)) return guardado.dados;
    } catch (e) {
      /* cache corrompido — trata como ausente */
    }
    return undefined;
  }

  function buscarComCacheSessao(chave, url, ttlMs) {
    var d = $.Deferred();
    var guardado = lerCacheSessao(chave, ttlMs);
    if (guardado !== undefined) return d.resolve(guardado).promise();
    $.getJSON(url)
      .done(function (dados) {
        try {
          sessionStorage.setItem(chave, JSON.stringify({ em: Date.now(), dados: dados }));
        } catch (e) {
          /* sem espaço/sem sessionStorage — só não guarda */
        }
        d.resolve(dados);
      })
      .fail(function () { d.resolve(null); });
    return d.promise();
  }

  // Extensão vira categoria amigável ("PDFs", "Planilhas"...) para agrupar documentos —
  // usada no "Agrupar por categoria" do investigacao e nos subgrupos de fontes da ia.
  // Aceita a extensão direta ou o nome do arquivo (a extensão sai do final dele).
  function categoriaArquivo(tpArquivo, noArquivo) {
    var ext = String(tpArquivo || "").toLowerCase();
    if (!ext && noArquivo) {
      ext = (String(noArquivo).split(".").pop() || "").toLowerCase();
    }
    var mapa = {
      pdf: "PDFs",
      xls: "Planilhas", xlsx: "Planilhas", xlsm: "Planilhas", csv: "Planilhas", ods: "Planilhas",
      doc: "Documentos de texto", docx: "Documentos de texto", odt: "Documentos de texto", rtf: "Documentos de texto",
      txt: "Arquivos de texto", md: "Arquivos de texto", log: "Arquivos de texto",
      jpg: "Imagens", jpeg: "Imagens", png: "Imagens", gif: "Imagens", bmp: "Imagens",
      webp: "Imagens", tif: "Imagens", tiff: "Imagens", heic: "Imagens",
      mp3: "Áudios", wav: "Áudios", ogg: "Áudios", m4a: "Áudios", amr: "Áudios", aac: "Áudios",
      mp4: "Vídeos", mov: "Vídeos", avi: "Vídeos", mkv: "Vídeos", "3gp": "Vídeos", wmv: "Vídeos",
      zip: "Compactados", rar: "Compactados", "7z": "Compactados", tar: "Compactados", gz: "Compactados",
      html: "Páginas e dados", htm: "Páginas e dados", xml: "Páginas e dados", json: "Páginas e dados"
    };
    return mapa[ext] || (ext ? ext.toUpperCase() : "Outros");
  }

  // Copiar para a área de transferência com fallback para contextos sem navigator.clipboard
  // (HTTP interno da delegacia: o API só existe em origem segura, i.e. HTTPS/localhost).
  // `okMsg` é o texto do Toast de confirmação; retorna true quando o texto foi copiado.
  function copiarTexto(texto, okMsg) {
    var temToast = typeof window.Toast !== "undefined" && window.Toast;
    function ok() {
      if (temToast) window.Toast.sucesso(okMsg || "Copiado.");
      return true;
    }
    function fallback() {
      try {
        var t = document.createElement("textarea");
        t.value = String(texto);
        t.setAttribute("readonly", "");
        t.style.position = "fixed";
        t.style.top = "-1000px";
        t.style.opacity = "0";
        document.body.appendChild(t);
        t.select();
        var copiou = document.execCommand("copy");
        t.remove();
        if (!copiou) throw new Error("execCommand negou");
        return ok();
      } catch (e) {
        if (temToast) window.Toast.erro("Não foi possível copiar.");
        return false;
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(texto)).then(ok, fallback);
      return true;
    }
    return fallback();
  }

  window.AnalyserUtils = {
    formatarDataHora: formatarDataHora,
    formatarDataSimples: formatarDataSimples,
    formatarData: formatarData,
    formatarNumero: formatarNumero,
    formatarNumeroInquerito: formatarNumeroInquerito,
    formatarReais: formatarReais,
    formatarTamanho: formatarTamanho,
    paramUrl: paramUrl,
    truncar: truncar,
    debounce: debounce,
    nomeArquivo: nomeArquivo,
    encurtarCaminhos: encurtarCaminhos,
    categoriaArquivo: categoriaArquivo,
    escaparHtml: escaparHtml,
    esc: escaparHtml,
    lerCacheSessao: lerCacheSessao,
    buscarComCacheSessao: buscarComCacheSessao,
    copiarTexto: copiarTexto,
  };
})();
