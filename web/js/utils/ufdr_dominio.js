/* Componente compartilhado das telas dos domínios do UFDR importados na auditoria de
 * aproveitamento: uso de aplicativo, dispositivos reconhecidos, conexões, cookies, favoritos,
 * downloads, uso de rede, atividade em rede social, buscas, envios para a nuvem, gravações,
 * calendário, trajetos, identidade do aparelho, antenas, caixa postal, transferências,
 * cartões, dono do aparelho e contas de usuário.
 *
 * Todos são a mesma coisa do ponto de vista da tela — uma lista paginada, com busca livre e
 * com o filtro "só o que foi apagado" —, então em vez de 22 páginas com o mesmo código cada
 * página só declara suas abas e colunas e chama UfdrDominio.montar(). Os dados vêm de
 * GET /api/investigacao/inqueritos/{id}/ufdr/{dominio} (ver funcoes/ufdr_dominios_utils.py).
 *
 * Tipos de coluna: texto (padrão), longo, mono, data, numero, bytes, duracao, bool, coord,
 * url, tag, anexos.
 *
 * Um domínio pode declarar filtros além da busca (`filtros` no registro de COLUNAS): período
 * (`periodo`), faixa do dia (`horario`), selects de categoria (`selects`), faixa numérica
 * (`faixa`) e marcadores liga/desliga (`marcadores`). Os valores dos selects, a contagem de
 * cada marcador e os extremos de data/valor vêm de .../ufdr/{dominio}/facetas — só existe
 * controle para o que este aparelho tem de fato. As mesmas colunas precisam estar declaradas no
 * back-end (data=/hora=/facetas=/derivados=/marcadores=/valor= em ufdr_dominios_utils.DOMINIOS),
 * que é a lista branca. */
(function () {
  "use strict";

  var LIMITE = 100;

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  /* Os campos de identificação do aparelho chegam num inglês que o investigador não percorre
   * ("DeviceInfoFactoryReset" não denuncia "restauração de fábrica") — os nomes mais comuns
   * ganham rótulo em português e o texto cru do UFDR fica no title, que é o que vai no laudo. */
  var TRADUCOES_CAMPO = {
    "Device name": "Nome do aparelho",
    "Model": "Modelo",
    "DeviceModel": "Modelo (código)",
    "OS": "Sistema (iOS)",
    "Last restore from backup": "Última restauração de backup",
    "DeviceInfoFactoryReset": "Última restauração de fábrica",
    "DeviceInfoIsESim": "Usa eSIM",
    "DeviceInfoiCloudAdvancedDataProtection": "Proteção avançada de dados do iCloud",
    "Advertising Id (IDFA) #1": "ID de publicidade (IDFA)",
    "Message Retention Duration": "Retenção de mensagens",
    "Randomized Mac Address": "MAC aleatório (Wi-Fi/Bluetooth)",
    "Battery Zero-Capacity Calibration Date": "Calibração da bateria (carga zero)"
  };

  function traduzirCampo(nome) {
    if (!nome) return nome;
    return TRADUCOES_CAMPO[String(nome).trim()] || String(nome);
  }

  /* MCC+MNC é o código oficial da operadora, mas ninguém decora que 724-05 é a Claro — a tela
   * mostra o nome e deixa o código no title. Código desconhecido mostra o próprio código. */
  var OPERADORAS = {
    "724-02": "TIM", "724-03": "TIM", "724-04": "TIM",
    "724-05": "Claro", "724-31": "Claro", "724-32": "Claro", "724-33": "Claro",
    "724-34": "Claro", "724-35": "Claro",
    "724-06": "Vivo", "724-10": "Vivo",
    "724-16": "Oi", "724-08": "CTBC", "724-09": "CTBC",
    "724-54": "Sercomtel", "724-30": "Truenko",
    "716-06": "Movistar", "716-10": "Claro", "716-17": "Entel",
    "730-02": "Movistar", "730-03": "Claro", "730-04": "Entel"
  };

  function nomeOperadora(mcc, mnc) {
    if (mcc == null || mcc === "" || mnc == null || mnc === "") return null;
    var s = String(mnc);
    if (s.length < 2) s = "0" + s;
    var chave = String(mcc).trim() + "-" + s;
    return { nome: OPERADORAS[chave] || ("rede " + chave), chave: chave };
  }

  /* Ficha-resumo da tela "Identidade do aparelho": o que o investigador veio procurar, num
   * relance. Cada linha procura o primeiro campo que existir (por isso a lista de nomes). */
  var FICHA_CAMPOS = [
    ["Modelo", ["Model", "Modelo detectado", "Modelo de telefone detectado", "DeviceModel"]],
    ["Sistema", ["OS", "OS Version"]],
    ["Nome do aparelho", ["Device name"]],
    ["Apple ID", ["ID da Apple", "Apple ID"]],
    ["Nº de série", ["ID exclusiva", "Número de série", "Serial"]],
    ["IMEI", ["IMEI"]],
    ["IMEI 2", ["IMEI2", "IMEI 2"]],
    ["ID do Android", ["Android ID", "ID do Android"]],
    ["Telefone (MSISDN)", ["MSISDN", "MSISDN usado pela última vez"]],
    ["ICCID (chips)", ["ICCID"]],
    ["ICCID do último usuário", ["ICCID do último usuário"]],
    ["IMSI", ["IMSI"]],
    ["MAC do aparelho", ["Endereço MAC"]],
    ["Bluetooth", ["Endereço do dispositivo Bluetooth"]],
    ["Fuso horário", ["Fuso horário"]],
    ["Conta no iCloud", ["Conta do iCloud presente"]],
    ["Operadora", ["Nome da operadora"]],
    ["Última restauração de backup", ["Last restore from backup"]],
    ["Última restauração de fábrica", ["DeviceInfoFactoryReset"]],
    ["Proteção avançada do iCloud", ["DeviceInfoiCloudAdvancedDataProtection"]],
    ["ID de publicidade (IDFA)", ["Advertising Id (IDFA) #1", "Advertising Id (IDFA)"]]
  ];

  function formatarDataHora(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function formatarNumero(v) { return AnalyserUtils.formatarNumero(v); }

  /* AAAA-MM-DD do dia local — toISOString() daria o dia em UTC, e no Brasil (UTC-3) isso
   * transforma 1º de março em 28 de fevereiro no campo de data. */
  function dataIso(d) {
    var mes = d.getMonth() + 1;
    var dia = d.getDate();
    return d.getFullYear() + "-" + (mes < 10 ? "0" : "") + mes + "-" + (dia < 10 ? "0" : "") + dia;
  }

  /* Data do dia (e hora) no fuso da tela — a mesma normalização de formatarDataHora: o UFDR
   * grava ingênuo em UTC, o "Z" que não está na string está na intenção. */
  function dataBrasilia(iso) {
    if (!iso) return null;
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return isNaN(d.getTime()) ? null : d;
  }

  function diaLocal(iso) {
    var d = dataBrasilia(iso);
    // en-CA devolve AAAA-MM-DD, que é a chave dos baldes da vista de mês
    return d ? d.toLocaleDateString("en-CA", { timeZone: "America/Sao_Paulo" }) : null;
  }

  function horaLocal(iso) {
    var d = dataBrasilia(iso);
    return d ? d.toLocaleTimeString("pt-BR", { timeZone: "America/Sao_Paulo",
                                                hour: "2-digit", minute: "2-digit" }) : "";
  }

  function formatarBytes(v) {
    if (v == null) return "—";
    var n = Number(v);
    var unidades = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < unidades.length - 1) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(1)) + " " + unidades[i];
  }

  function formatarDuracao(seg) {
    if (seg == null) return "—";
    var s = Math.max(0, Math.round(Number(seg)));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = s % 60;
    if (h) return h + "h " + m + "min";
    if (m) return m + "min " + r + "s";
    return r + "s";
  }

  /* Estado de exclusão do UFDR. "Trash" é a lixeira do próprio aplicativo e "Unknown" é
   * registro recuperado cujo estado o Physical Analyzer não afirmou — os dois são achado,
   * não ruído, então aparecem marcados (com peso visual menor que "Deleted"). */
  function marcaEstado(estado) {
    if (!estado || estado === "Intact") return null;
    var rotulos = { Deleted: "Apagado", Trash: "Lixeira", Unknown: "Recuperado" };
    var $s = $("<span>").addClass("ufdr-apagado").text(rotulos[estado] || estado);
    if (estado !== "Deleted") $s.addClass("ufdr-lixeira");
    return $s;
  }

  /* ----- anexos de publicação -----
   * O UFDR grava nesses modelos só o NOME do anexo (nada do file_id que o anexo de mensagem
   * tem), então o back-end procura esse nome entre os arquivos do MESMO aparelho e devolve
   * `anexos` = [{nome, id_arquivo, no_categoria, disponivel}] (ver resolver_anexos_por_nome).
   * Achou o binário, a tela abre; não achou, mostra o nome como está — que é tudo o que o
   * extrato permite afirmar, e some-lo do olhar do investigador seria pior. */
  var ICONE_ANEXO = { "Imagem": "🖼", "Vídeo": "🎥", "Áudio": "🔊", "Documento": "📄",
                      "Compactado": "🗜" };
  var MIDIA_ANEXO = { "Imagem": true, "Vídeo": true, "Áudio": true };

  function urlArquivo(idArquivo, sufixo) {
    return "/api/investigacao/arquivos/" + idArquivo + "/conteudo" + (sufixo || "");
  }

  // o nome vem às vezes com o caminho do aparelho junto; na célula cabe só o nome do arquivo
  function nomeCurtoAnexo(nome) {
    var partes = String(nome).replace(/\\/g, "/").split("/");
    return partes[partes.length - 1] || String(nome);
  }

  function chipAnexo(anexo) {
    var nome = nomeCurtoAnexo(anexo.nome);
    var icone = ICONE_ANEXO[anexo.no_categoria] || "📎";
    if (!anexo.id_arquivo || !anexo.disponivel) {
      return $("<span>").addClass("ufdr-anexo ufdr-anexo-ausente")
        .attr("title", anexo.nome + (anexo.id_arquivo
          ? " — o arquivo existe no aparelho, mas o .ufdr não trouxe o binário."
          : " — nenhum arquivo com este nome nesta extração; só o nome ficou registrado."))
        .append($("<span>").addClass("ufdr-anexo-icone").text(icone))
        .append(document.createTextNode(nome));
    }
    var $chip;
    if (MIDIA_ANEXO[anexo.no_categoria]) {
      $chip = $("<button>").attr({ type: "button", title: "Abrir " + anexo.nome })
        .addClass("ufdr-anexo ufdr-anexo-abre")
        .on("click", function () { abrirModalAnexo(anexo); });
      // imagem ganha miniatura na própria célula: é a resposta mais direta a "o que foi postado"
      if (anexo.no_categoria === "Imagem") {
        $("<img>").addClass("ufdr-anexo-thumb").attr({
          src: urlArquivo(anexo.id_arquivo, "?preview=true&thumb=true"),
          alt: nome, loading: "lazy"
        }).on("error", function () {
          $(this).replaceWith($("<span>").addClass("ufdr-anexo-icone").text(icone));
        }).appendTo($chip);
      } else {
        $("<span>").addClass("ufdr-anexo-icone").text(icone).appendTo($chip);
      }
    } else {
      // documento, compactado e afins não têm visualizador: o link baixa o binário original
      $chip = $("<a>").addClass("ufdr-anexo ufdr-anexo-abre")
        .attr({ href: urlArquivo(anexo.id_arquivo, "?download=true"),
                title: "Baixar " + anexo.nome })
        .append($("<span>").addClass("ufdr-anexo-icone").text(icone));
    }
    return $chip.append(document.createTextNode(nome));
  }

  function celulaAnexos(item, coluna) {
    var lista = item.anexos || [];
    if (!lista.length) {
      // sem a resolução do back-end (resposta antiga), ao menos o texto cru continua visível
      var texto = item[coluna.campo];
      return $("<span>").text(texto || "—");
    }
    var $env = $("<div>").addClass("ufdr-anexos");
    lista.forEach(function (anexo) { chipAnexo(anexo).appendTo($env); });
    return $env;
  }

  /* Visualizador do anexo: imagem, vídeo e áudio abrem aqui mesmo (a imagem pela prévia JPEG,
   * que é o que converte HEIC de iPhone); o binário original fica sempre a um clique, porque é
   * ele que vale como prova. Montado uma vez só, na primeira abertura. */
  var modal = null;

  function garantirModal() {
    if (modal) return modal;
    var $overlay = $("<div>").addClass("modal-overlay hidden").appendTo(document.body);
    var $caixa = $("<div>").addClass("modal ufdr-modal-anexo").appendTo($overlay);
    var $cabecalho = $("<div>").addClass("modal-header").appendTo($caixa);
    var $titulo = $("<h2>").appendTo($cabecalho);
    $("<button>").addClass("modal-close").attr("type", "button").html("&times;")
      .on("click", fecharModalAnexo).appendTo($cabecalho);
    var $corpo = $("<div>").addClass("modal-body").appendTo($caixa);
    $overlay.on("click", function (e) { if (e.target === this) fecharModalAnexo(); });
    $(document).on("keydown", function (e) {
      if (e.key === "Escape" && modal && !modal.overlay.hasClass("hidden")) fecharModalAnexo();
    });
    modal = { overlay: $overlay, titulo: $titulo, corpo: $corpo };
    return modal;
  }

  function abrirModalAnexo(anexo) {
    var m = garantirModal();
    var categoria = anexo.no_categoria;
    // vídeo e áudio tocam o binário original (streaming); imagem passa pela prévia
    var url = urlArquivo(anexo.id_arquivo, categoria === "Imagem" ? "?preview=true" : "");
    m.titulo.text(nomeCurtoAnexo(anexo.nome));
    m.corpo.empty();

    var $carregando = $("<div>").addClass("ufdr-midia-carregando")
      .append($("<span>").addClass("spinner"))
      .append($("<span>").text(" carregando…")).appendTo(m.corpo);
    var $midia = $(categoria === "Imagem" ? "<img>" : categoria === "Vídeo" ? "<video>" : "<audio>")
      .addClass("ufdr-midia")
      .on(categoria === "Imagem" ? "load" : "canplay loadeddata", function () { $carregando.remove(); })
      .on("error", function () { $carregando.text("⚠ não foi possível carregar este anexo."); });
    if (categoria === "Imagem") {
      $midia.attr("alt", nomeCurtoAnexo(anexo.nome));
    } else {
      $midia.attr({ controls: true, autoplay: true });
    }
    $midia.attr("src", url).appendTo(m.corpo);
    if (categoria === "Áudio") VelocidadeMidia.controle($midia).appendTo(m.corpo);

    $("<div>").addClass("ufdr-midia-acoes")
      .append($("<a>").addClass("arq-acao")
        .attr({ href: urlArquivo(anexo.id_arquivo, "?download=true") })
        .text("Baixar original"))
      .appendTo(m.corpo);

    m.overlay.removeClass("hidden");
  }

  function fecharModalAnexo() {
    if (!modal) return;
    modal.overlay.addClass("hidden");
    modal.corpo.empty();   // descarrega a imagem e para o áudio/vídeo que estava tocando
  }

  function celula(item, coluna) {
    var v = item[coluna.campo];
    var $td = $("<td>");
    switch (coluna.tipo) {
      case "anexos":
        return $td.append(celulaAnexos(item, coluna));
      case "data":
        return $td.text(formatarDataHora(v));
      case "numero":
        return $td.addClass("ufdr-num").text(formatarNumero(v));
      case "bytes":
        return $td.addClass("ufdr-num").text(formatarBytes(v));
      case "duracao":
        return $td.addClass("ufdr-num").text(formatarDuracao(v));
      case "bool":
        return $td.text(v == null ? "—" : (v ? "Sim" : "Não"));
      case "mono":
        return $td.append($("<span>").addClass("ufdr-mono").text(v || "—"));
      case "longo":
        return $td.addClass("ufdr-celula-longa").text(v || "—");
      case "tag":
        return v ? $td.append($("<span>").addClass("arq-tag").text(v)) : $td.text("—");
      case "situacao": {
        // situação do COMPROMISSIMO (Confirmado/Provisório/Cancelado) — não confundir com a
        // coluna "Situação" do fim da tabela, que é o estado de exclusão do UFDR
        if (!v) return $td.text("—");
        var mapa = { CONFIRMED: "Confirmado", TENTATIVE: "Provisório", CANCELLED: "Cancelado",
                     UNKNOWN: "Não informado" };
        var rotuloSit = mapa[String(v).toUpperCase()] || String(v);
        var $tag = $("<span>").addClass("arq-tag").text(rotuloSit);
        if (String(v).toUpperCase() === "CANCELLED") $tag.addClass("ufdr-situacao-cancelado");
        return $td.append($tag);
      }
      case "campo": {
        // nome de campo do aparelho traduzido; o texto cru do UFDR (o que vai no laudo) fica no title
        if (!v) return $td.text("—");
        return $td.text(traduzirCampo(v)).attr("title", String(v));
      }
      case "valores": {
        // o back-end agrupou os N valores de um mesmo campo (um ICCID por chip, ~60 MACs
        // aleatórios) — mostro o primeiro e expando o resto num clique, sem página de ruído
        var valores = String(v == null ? "" : v).split("\n")
          .filter(function (x) { return x !== ""; });
        if (!valores.length) return $td.text("—");
        if (valores.length === 1) {
          return $td.append($("<span>").addClass("ufdr-mono").text(valores[0]));
        }
        var $env = $("<div>").addClass("ufdr-valores");
        var $lista = $("<div>").addClass("ufdr-valores-lista hidden");
        valores.forEach(function (val) {
          $("<div>").addClass("ufdr-mono").text(val).appendTo($lista);
        });
        var $mais = $("<button>").attr({ type: "button", "aria-expanded": "false" })
          .addClass("ufdr-valores-mais")
          .text("+ " + (valores.length - 1) + " valor(es)")
          .on("click", function () {
            var aberto = $lista.hasClass("hidden");
            $lista.toggleClass("hidden", !aberto);
            $(this).attr("aria-expanded", String(aberto))
              .text(aberto ? "recolher" : "+ " + (valores.length - 1) + " valor(es)");
          });
        return $td.append($env.append(
          $("<span>").addClass("ufdr-mono").text(valores[0]), $mais, $lista));
      }
      case "ocorrencias": {
        // quantas vezes a MESMA célula (LAC/CID) foi regrava na extração — a antena frequente
        // é onde o aparelho ficou, e isso não pode depender de contar linha por linha
        var n2 = Number(v || 0);
        if (n2 < 2) return $td.text("—");
        return $td.append($("<span>").addClass("arq-tag")
          .attr("title", "esta mesma antena aparece " + n2 + " vezes na extração")
          .text(n2 + "×"));
      }
      case "operadora": {
        var op = nomeOperadora(item[coluna.campo], item[coluna.campoMnc]);
        if (!op) return $td.text("—");
        return $td.append($("<span>").addClass("arq-tag")
          .attr("title", "código MCC-MNC " + op.chave).text(op.nome));
      }
      case "unidos": {
        // o back-end mesclou N registros do UFDR que descrevem a mesma coisa (mesmo MAC/nome em
        // apps diferentes) — o selo diz quantos, para ninguém desconfiar da linha única
        var n = Number(v || 0);
        if (n < 2) return $td.text("—");
        return $td.append($("<span>").addClass("arq-tag")
          .attr("title", n + " registros do UFDR unidos nesta linha (mesmo aparelho, fontes diferentes)")
          .text(n + " unidos"));
      }
      case "repeticao": {        // o UFDR grava o texto cru do calendário ("daily", "weekly on Monday"…); o
        // investigador pensa em "Diária/Semanal" — e o ↻ denuncia recorrência de relance
        if (!v) return $td.text("—");
        var t = String(v).toLowerCase();
        var pares = [["weekday", "Dias de semana"], ["daily", "Diária"], ["monthly", "Mensal"],
                     ["yearly", "Anual"], ["weekly", "Semanal"]];
        var traduzida = null;
        for (var pi = 0; pi < pares.length; pi++) {
          if (t.indexOf(pares[pi][0]) !== -1) { traduzida = pares[pi][1]; break; }
        }
        return $td.text(traduzida ? "↻ " + traduzida : String(v));
      }
      case "url":
        if (!v) return $td.text("—");
        // link só de exibição: abre em aba nova e sem passar referrer para o site de origem
        return $td.append(
          $("<a>").attr({ href: v, target: "_blank", rel: "noopener noreferrer" })
            .addClass("ufdr-celula-longa").css("display", "inline-block").text(v)
        );
      case "coord":
        var lat = item[coluna.campo];
        var lon = item[coluna.campoLon];
        if (lat == null || lon == null) return $td.text("—");
        return $td.append(
          $("<a>").addClass("ufdr-coord")
            .attr({ href: "https://www.google.com/maps?q=" + lat + "," + lon,
                    target: "_blank", rel: "noopener noreferrer" })
            .text(Number(lat).toFixed(5) + ", " + Number(lon).toFixed(5))
        );
      default:
        return $td.text(v == null || v === "" ? "—" : String(v));
    }
  }

  /* Uma aba pode ser "externa": em vez de tabela montada aqui, ela apenas mostra um elemento
   * que a própria página já renderiza (a lista de redes Wi-Fi, a de contas financeiras). É o
   * que permite empilhar o conteúdo antigo e o novo em abas, em vez de uma região embaixo da
   * outra, sem reescrever a tela que já existia. `aoTrocarAba(chave, proprio)` avisa a página
   * quando a aba dela volta a aparecer — o contador da topbar é compartilhado, e sem isso ele
   * fica com o número da aba anterior. */
  function montar(config) {
    var idInquerito = paramUrl("inquerito");
    var idExtracao = paramUrl("extracao");
    var $raiz = $(config.seletor);
    // dentro de um modal o contador da topbar é da tela de trás, e sobrescrevê-lo confunde
    var $total = config.semTotal ? $() : $("#total-registros");
    var externas = {};
    config.abas.forEach(function (a) { if (a.externo) externas[a.chave] = $(a.externo); });

    if (!idInquerito || !idExtracao) {
      $raiz.append($("<p>").css("color", "var(--text-secondary)")
        .text("Parâmetros inválidos na URL (é necessário inquérito e extração)."));
      return;
    }

    var $abas = $("<div>").addClass("ufdr-abas").attr("role", "tablist").appendTo($raiz);

    /* Barra de filtros: uma parte fixa (busca livre e "só apagados", que valem em qualquer
     * domínio) e uma parte montada a cada troca de aba, a partir do que o domínio declara em
     * COLUNAS.filtros — período, faixa do dia, selects de categoria, faixa de valor e
     * marcadores. Os valores dos selects vêm do banco (rota /facetas), não de lista fixa: o que
     * o Cellebrite grava em TransferType ou em Status muda de aplicativo para aplicativo. */
    var $filtros = $("<div>").addClass("arq-filtros filtros-painel").appendTo($raiz);
    var $grade = $("<div>").addClass("filtros-grade").appendTo($filtros);
    var $rodape = $("<div>").addClass("filtros-rodape").appendTo($filtros);

    function campo(rotulo, classe) {
      var $c = $("<div>").addClass("filtro-campo").addClass(classe || "").appendTo($grade);
      $("<span>").addClass("filtro-rotulo").text(rotulo).appendTo($c);
      return $c;
    }

    var $busca = $("<input>").attr({ type: "search", placeholder: "Buscar…", autocomplete: "off" })
      .addClass("arq-busca").appendTo(campo("Buscar", "filtro-campo-busca"));

    /* Estado na URL (opt-in pela página: data-estado-url): busca, filtros, página e vista vivem
     * na query string via EstadoUrl — voltar não perde a visão, e a URL pode ser colada para um
     * colega abrir exatamente o recorte. A URL é a fonte de verdade: os controles restauram dela
     * a cada montagem (o que já estava digitado foi salvo nela, então nada se perde). */
    function paramEstado(nome) {
      return config.estadoUrl ? (paramUrl(nome) || "") : "";
    }
    /* Chaves que a última gravação deixou na URL (o padrão é do TabelaPaginada): os selects e
     * marcadores de UMA aba têm nomes próprios do domínio (f_no_bandeira, m_com_anexo…), e
     * EstadoUrl.salvar só toca no que recebe — uma chave apenas omitida ficaria presa na query,
     * e a próxima aba "restauraria" um valor que o usuário nunca escolheu nela. */
    var chavesSalvas = {};

    function salvarEstado(extra) {
      if (!config.estadoUrl || !window.EstadoUrl) return;
      var dados = {
        busca: $busca.val().trim(),
        apagados: $chkApagados.is(":checked") ? "1" : "",
        pagina: vista === "mes" ? "" : (pagina > 1 ? String(pagina) : ""),
        vista: vista === "mes" ? "mes" : "",
        ordenar: ordemAtual || ""
      };
      [["de", extras.de], ["ate", extras.ate], ["faixa_dia", extras.horario],
       ["valor_min", extras.valorMin], ["valor_max", extras.valorMax]].forEach(function (par) {
        // controle ainda não montado (as facetas estão a caminho) NÃO entra na lista:
        // salvar "" apagaria da URL o valor que o próprio montarFiltrosDaAba vai restaurar
        if (par[1]) dados[par[0]] = par[1].val() || "";
      });
      extras.selects.forEach(function ($s) { dados["f_" + $s.data("campo")] = $s.val() || ""; });
      extras.marcadores.forEach(function ($c) {
        var marcado = $c.is(":checked"), padrao = !!$c.data("padrao");
        // marcador padrão só precisa aparecer na URL quando o usuário o DESLIGA (o "0" é o
        // jeito de dizer "não apague o padrão", já que valor vazio some da URL)
        dados["m_" + $c.data("chave")] = marcado ? (padrao ? "" : "1") : (padrao ? "0" : "");
      });
      if (extra) Object.keys(extra).forEach(function (k) { dados[k] = extra[k]; });
      Object.keys(chavesSalvas).forEach(function (k) { if (!(k in dados)) dados[k] = ""; });
      chavesSalvas = {};
      Object.keys(dados).forEach(function (k) { if (dados[k]) chavesSalvas[k] = true; });
      EstadoUrl.salvar(dados);
    }

    if (config.estadoUrl) {
      $busca.val(paramEstado("busca"));
    }

    var $atalhos = $("<div>").addClass("filtro-atalhos").appendTo($rodape);
    // marcadores do domínio (só com anexo, só com coordenada): montados por aba, ao lado do
    // "só apagados", que é o marcador que vale em todo domínio
    var $marcadores = $("<div>").addClass("filtro-marcadores").appendTo($rodape);
    var $soApagados = $("<label>").addClass("arq-check").appendTo($rodape);
    var $chkApagados = $("<input>").attr("type", "checkbox").appendTo($soApagados);
    $soApagados.append(document.createTextNode(" Só apagados/recuperados"));
    var $limpar = $("<button>").addClass("btn btn-secondary filtro-limpar").attr("type", "button")
      .text("Limpar filtros").hide().appendTo($rodape);
    // restauração do estado fixo só depois de os controles existirem
    if (config.estadoUrl) $chkApagados.prop("checked", paramEstado("apagados") === "1");

    // controles montados por aba; ficam aqui para carregar() e limparFiltros() os lerem
    var extras = { selects: [], de: null, ate: null, valorMin: null, valorMax: null,
                   horario: null, marcadores: [] };

    var $envolucro = $("<div>").css("overflow-x", "auto").appendTo($raiz);
    var $tabela = $("<table>").addClass("grupo-tabela").appendTo($envolucro);
    var $cabecalho = $("<thead>").appendTo($tabela);
    var $corpo = $("<tbody>").appendTo($tabela);
    var $vazio = $("<p>").addClass("empty-hint").hide().appendTo($raiz);

    var $pag = $("<div>").addClass("arq-paginacao").appendTo($raiz);
    var $anterior = $("<button>").addClass("btn paginacao-btn").text("Anterior").appendTo($pag);
    var $info = $("<span>").addClass("arq-pag-info").appendTo($pag);
    var $proxima = $("<button>").addClass("btn paginacao-btn").text("Próxima").appendTo($pag);

    /* ---------- vista "Mês" (opt-in: só domínio com vistaMes, hoje o Calendário) ----------
       A página chama-se Calendário mas só conhecia a tabela. A grade desenha o MÊS VISÍVEL
       consultando a mesma rota com de/ate do mês (o filtro de período do domínio), e clique
       num dia devolve a lista filtrada naquele dia. Nada de fuso próprio: os baldes por dia
       usam diaLocal(), a mesma Brasília das datas da tabela. */
    var $barraVistas = $("<div>").addClass("ufdr-vistas").attr("role", "group")
      .attr("aria-label", "Modo de visualização").hide().insertAfter($filtros);
    var $btnLista = $("<button>").attr({ type: "button", "aria-pressed": "true" })
      .addClass("ufdr-vista-btn").text("Lista").appendTo($barraVistas);
    var $btnMes = $("<button>").attr({ type: "button", "aria-pressed": "false" })
      .addClass("ufdr-vista-btn").text("Mês").appendTo($barraVistas);
    var $mes = $("<div>").addClass("ufdr-mes").hide().insertAfter($barraVistas);

    /* Ficha-resumo (aba que declara `ficha`): uma chamada só, sem filtros, buscando tudo —
     * o painel mostra o aparelho inteiro, não a página/recorte da tabela, e por isso é buscado
     * e desenhado à parte, uma única vez por aba. */
    var $ficha = $("<div>").addClass("ufdr-ficha").hide().insertAfter($filtros);
    var fichaFeita = {};

    function desenharFicha(aba) {
      $ficha.toggle(abaAtual === aba.chave && $ficha.children().length > 0);
      if (fichaFeita[aba.chave]) return;
      fichaFeita[aba.chave] = true;
      var url = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
        "/ufdr/" + encodeURIComponent(aba.chave) +
        "?extracao=" + encodeURIComponent(idExtracao) + "&pagina=1&limite=5000";
      $.getJSON(url).done(function (dados) {
        var porCampo = {};
        (dados.itens || []).forEach(function (item) {
          porCampo[String(item.no_campo || "").trim()] = String(item.tx_valor || "");
        });
        var $gradeFicha = $("<div>").addClass("ufdr-ficha-grade");
        FICHA_CAMPOS.forEach(function (def) {
          var cru = null;
          for (var i = 0; i < def[1].length; i++) {
            var bruto = porCampo[def[1][i]];
            if (bruto && bruto.trim()) { cru = bruto; break; }
          }
          if (cru == null) return;
          var vs = cru.split("\n").filter(function (x) { return x !== ""; })
            .map(function (x) { return x === "True" ? "Sim" : x === "False" ? "Não" : x; });
          if (!vs.length) return;
          var $item = $("<div>").addClass("ufdr-ficha-item");
          $("<span>").addClass("ufdr-ficha-rotulo").text(def[0]).appendTo($item);
          $item.append($("<span>").addClass("ufdr-ficha-valor ufdr-mono")
            .text(vs.slice(0, 3).join(" · ") + (vs.length > 3 ? " (+" + (vs.length - 3) + ")" : ""))
            .attr("title", vs.join("\n")));
          $gradeFicha.append($item);
        });
        $ficha.empty().append($gradeFicha);
        $ficha.toggle(abaAtual === aba.chave && $gradeFicha.children().length > 0);
      }).fail(function () {
        delete fichaFeita[aba.chave];   // sem resposta, permite tentar de novo na próxima troca
      });
    }

    var vista = "lista";
    var mesData = new Date();       // qualquer dia do mês exibido
    var cacheMes = {};              // aba|de|ate|filtros -> baldes por dia
    var paginaRestaurada = false;   // ?pagina= da URL só vale para a aba inicial
    var vistaRestaurada = false;    // ?vista= idem

    function diasDoMesLabel() {
      var nome = mesData.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
      return nome.charAt(0).toUpperCase() + nome.slice(1);
    }

    function chaveFiltrosMes() {
      var partes = [$busca.val().trim(), $chkApagados.is(":checked") ? "1" : "0"];
      extras.selects.forEach(function ($s) { partes.push($s.val() || ""); });
      extras.marcadores.forEach(function ($c) { partes.push($c.is(":checked") ? "1" : "0"); });
      partes.push((extras.horario && extras.horario.val()) || "");
      return partes.join("|");
    }

    function desenharMes(porDia, de, ate, total) {
      $mes.empty();

      var $cab = $("<div>").addClass("ufdr-mes-cab").appendTo($mes);
      $("<button>").attr({ type: "button", "aria-label": "Mês anterior" }).addClass("ufdr-mes-nav")
        .text("‹").on("click", function () { mesData = new Date(mesData.getFullYear(), mesData.getMonth() - 1, 1); carregarMes(); })
        .appendTo($cab);
      $("<strong>").addClass("ufdr-mes-titulo").text(diasDoMesLabel()).appendTo($cab);
      $("<button>").attr({ type: "button", "aria-label": "Próximo mês" }).addClass("ufdr-mes-nav")
        .text("›").on("click", function () { mesData = new Date(mesData.getFullYear(), mesData.getMonth() + 1, 1); carregarMes(); })
        .appendTo($cab);
      $("<button>").attr("type", "button").addClass("btn btn-secondary ufdr-mes-hoje")
        .text("Hoje").on("click", function () { mesData = new Date(); carregarMes(); }).appendTo($cab);
      var hoje = diaLocal(new Date().toISOString());

      var $grade = $("<div>").addClass("ufdr-mes-grade").appendTo($mes);
      ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"].forEach(function (d) {
        $("<span>").addClass("ufdr-mes-dia-semana").text(d).appendTo($grade);
      });

      var ini = new Date(de + "T12:00:00");     // meio-dia local: o dia não vira ontem no fuso
      var dia = new Date(de + "T12:00:00");
      for (var vazio = 0; vazio < ini.getDay(); vazio++) {
        $("<span>").addClass("ufdr-mes-celula ufdr-mes-fora").appendTo($grade);
      }
      var limiteDias = Math.round((new Date(ate + "T12:00:00") - new Date(de + "T12:00:00")) / 86400000) + 1;
      for (var dnum = 0; dnum < limiteDias; dnum++) {
        (function (chaveDia, num) {
          var $cel = $("<button>").attr("type", "button")
            .addClass("ufdr-mes-celula ufdr-mes-dia")
            .toggleClass("ufdr-mes-hoje-cel", chaveDia === hoje)
            .appendTo($grade);
          var eventos = (porDia[chaveDia] || []).slice().sort(function (a, b) {
            return String(a.dt_inicio || "") < String(b.dt_inicio || "") ? -1 : 1;
          });
          if (eventos.length) $cel.addClass("com-evento");
          $cel.attr("title", eventos.length
            ? eventos.length + " compromisso(s) — clique para abrir a lista do dia"
            : (chaveDia === hoje ? "Hoje" : ""));
          $("<span>").addClass("ufdr-mes-num").text(num).appendTo($cel);
          eventos.slice(0, 3).forEach(function (ev) {
            var hora = horaLocal(ev.dt_inicio);
            var texto = (hora && hora !== "00:00" ? hora + " " : "") +
              (ev.tx_assunto || ev.tx_detalhes || "compromisso");
            if (ev.tx_repeticao) texto = "↻ " + texto;
            var $chip = $("<span>").addClass("ufdr-mes-chip").text(truncarChip(texto));
            if (String(ev.cd_situacao || "").toUpperCase() === "CANCELLED") $chip.addClass("cancelado");
            $cel.append($chip);
          });
          if (eventos.length > 3) {
            $("<span>").addClass("ufdr-mes-mais").text("+ " + (eventos.length - 3) + " mais").appendTo($cel);
          }
          $cel.on("click", function () { abrirDiaNaLista(chaveDia); });
        })(dataIso(dia), dia.getDate());
        dia.setDate(dia.getDate() + 1);
      }

      var resumo = (total != null ? total : itensDoMes(porDia)) + " compromisso(s) em " + diasDoMesLabel();
      $info.text(de === ate ? resumo : resumo + " · período filtrado");
      $total.text(String(total != null ? total : itensDoMes(porDia)).toLocaleString("pt-BR") + " compromisso(s)");
    }

    function truncarChip(t) { return t.length > 34 ? t.slice(0, 33) + "…" : t; }
    function itensDoMes(porDia) {
      var n = 0;
      Object.keys(porDia).forEach(function (k) { n += porDia[k].length; });
      return n;
    }

    // clique no dia: a lista de baixo, filtrada exatamente naquele dia (a grade é resumo;
    // a lista é o lugar de ler convidados, detalhes e marcar para o relatório)
    function abrirDiaNaLista(diaIso) {
      if (!extras.de || !extras.ate) return;   // domínio sem período não tem como fechar o dia
      extras.de.val(diaIso);
      extras.ate.val(diaIso);
      trocarVista("lista");
    }

    var seqMes = 0;            // só a busca do mês mais recente desenha (vira a folha rápido)
    var mesInicializado = false;

    function carregarMes() {
      var aba = abaPorChave(abaAtual);
      if (!aba) return;
      if (!mesInicializado) {
        // ordem de abertura: o mês da URL (alguém colou a visão) > mês do registro mais
        // recente > hoje. A extração costuma ser de meses atrás, e abrir em "hoje"
        // seria abrir no vazio
        mesInicializado = true;
        var mesUrl = paramEstado("mes");
        var maisRecente = extras.ate && extras.ate.attr("max");
        if (/^\d{4}-\d{2}$/.test(mesUrl)) mesData = new Date(mesUrl + "-15T12:00:00");
        else if (maisRecente) mesData = new Date(maisRecente + "T12:00:00");
      }
      var minhaBusca = ++seqMes;
      var ini = new Date(mesData.getFullYear(), mesData.getMonth(), 1);
      var fim = new Date(mesData.getFullYear(), mesData.getMonth() + 1, 0);
      salvarEstado({ mes: dataIso(ini).slice(0, 7) });
      var de = dataIso(ini), ate = dataIso(fim);
      // o período que o investigador escolheu continua valendo: a grade mostra a interseção
      if (extras.de && extras.de.val() && extras.de.val() > de) de = extras.de.val();
      if (extras.ate && extras.ate.val() && extras.ate.val() < ate) ate = extras.ate.val();

      var chaveCache = aba.chave + "|" + de + "|" + ate + "|" + chaveFiltrosMes();
      if (cacheMes[chaveCache]) { desenharMes(cacheMes[chaveCache], de, ate); return; }

      $mes.empty().append($("<p>").addClass("empty-hint").text("Carregando " + diasDoMesLabel() + "…"));
      var itens = [];
      var paginaAlvo = 1;
      var teto = 5;   // no máximo 5×200 por mês — folga enorme para agenda pessoal
      (function proxima() {
        $.getJSON(montarUrl(paginaAlvo, { de: de, ate: ate, limite: 200 })).done(function (dados) {
          if (minhaBusca !== seqMes) return;   // o usuário já virou a folha
          itens = itens.concat(dados.itens || []);
          var total = dados.total || 0;
          if (paginaAlvo < teto && itens.length < total) { paginaAlvo += 1; proxima(); return; }
          var porDia = {};
          itens.forEach(function (item) {
            var chave = diaLocal(item.dt_inicio) || "sem-data";
            (porDia[chave] = porDia[chave] || []).push(item);
          });
          cacheMes[chaveCache] = porDia;
          desenharMes(porDia, de, ate, total);
        }).fail(function () {
          if (minhaBusca !== seqMes) return;
          $mes.empty().append($("<p>").addClass("empty-hint")
            .text("Não foi possível carregar o mês."));
        });
      })();
    }

    function atualizarBotoesVista() {
      var ehMes = vista === "mes";
      $btnLista.attr("aria-pressed", String(!ehMes));
      $btnMes.attr("aria-pressed", String(ehMes));
    }

    function trocarVista(nova) {
      vista = nova;
      atualizarBotoesVista();
      var ehMes = nova === "mes";
      $mes.toggle(ehMes);
      $envolucro.toggle(!ehMes);
      $pag.toggle(!ehMes);
      if (ehMes) { $vazio.hide(); carregarMes(); }
      else { pagina = 1; carregar(); }
    }

    $btnLista.on("click", function () { if (vista !== "lista") trocarVista("lista"); });
    $btnMes.on("click", function () { if (vista !== "mes") trocarVista("mes"); });

    var abaAtual = null;
    // primeira aba visível: a URL não precisa de ?aba= quando é ela (link limpo)
    var primeiraAba = null;
    // ordenação do cabeçalho clicável (chave da whitelist do back-end) — pertence à aba,
    // e o ?ordenar= da URL só vale para a aba inicial (mesma regra da página)
    var ordemAtual = null;
    var ordemRestaurada = false;
    var pagina = 1;
    var buscaTimer = null;
    // Só a resposta da busca mais recente é desenhada. Duas cargas podem estar no ar ao mesmo
    // tempo (troca de aba enquanto as contagens de marcação chegam, ou digitação rápida no
    // filtro), e sem isso as duas respostas se somam na tabela.
    var seqCarga = 0;
    // deep-link ?destacar= (ver destacarRegistro/localizarDestacado)
    var idDestacar = window.EstadoUrl ? EstadoUrl.ler(["destacar"]).destacar : null;
    var procurandoDestacado = false;

    function abaPorChave(chave) {
      return config.abas.filter(function (a) { return a.chave === chave; })[0];
    }

    /* Cabeçalho como <th scope="col"> (igual ao TabelaPaginada): coluna declarada com
     * `ordenar` ganha botão clicável — as chaves batem com a whitelist `ordenacoes` do
     * back-end, e a direção do primeiro clique segue `padrao` da coluna (datas descem,
     * nomes sobem). Clicar de novo inverte; em coluna de uma direção só, o 2º clique
     * volta à ordem padrão do endpoint. */
    function chaveAtiva(chave, aba) {
      if (!chave || !aba) return null;
      for (var i = 0; i < aba.colunas.length; i++) {
        var o = aba.colunas[i].ordenar;
        if (!o) continue;
        if (o.asc === chave) return { indice: i, direcao: "asc" };
        if (o.desc === chave) return { indice: i, direcao: "desc" };
      }
      return null;
    }

    function atualizarSetas(aba) {
      var ativa = chaveAtiva(ordemAtual, aba);
      $cabecalho.find(".tp-ordenar").each(function () {
        var $btn = $(this);
        var eAtiva = !!ativa && Number($btn.attr("data-col")) === ativa.indice;
        $btn.toggleClass("tp-ordenar-ativo", eAtiva);
        $btn.find(".tp-seta").text(eAtiva ? (ativa.direcao === "asc" ? "▲" : "▼") : "↕");
      });
    }

    function clicarCabecalho(indice) {
      var aba = abaPorChave(abaAtual);
      var c = aba && aba.colunas[indice];
      if (!c || !c.ordenar) return;
      var ativa = chaveAtiva(ordemAtual, aba);
      var proxima;
      if (ativa && ativa.indice === indice) {
        proxima = c.ordenar[ativa.direcao === "asc" ? "desc" : "asc"] || null;
      } else {
        var padrao = c.ordenar.padrao || "asc";
        proxima = c.ordenar[padrao] || c.ordenar[padrao === "asc" ? "desc" : "asc"] || null;
      }
      ordemAtual = proxima;
      pagina = 1;
      atualizarSetas(aba);
      carregar();
    }

    function desenharCabecalho(aba) {
      $cabecalho.empty();
      var $tr = $("<tr>").appendTo($cabecalho);
      aba.colunas.forEach(function (c, i) {
        var $th = $("<th>").attr("scope", "col").addClass("col-nome").appendTo($tr);
        if (!c.ordenar) { $th.text(c.rotulo); return; }
        $("<button>").addClass("tp-ordenar").attr({ type: "button", "data-col": i })
          .attr("title", "Ordenar por " + c.rotulo)
          .append($("<span>").text(c.rotulo))
          .append($("<span>").addClass("tp-seta").attr("aria-hidden", "true").text("↕"))
          .on("click", function () { clicarCabecalho(i); })
          .appendTo($th);
      });
      $("<th>").attr("scope", "col").addClass("col-nome").text("Situação").appendTo($tr);
      if (aba.entidade) $("<th>").attr("scope", "col").addClass("col-nome").text("Entidade").appendTo($tr);
      atualizarSetas(aba);
    }

    /* Marcação para o relatório: o mesmo widget das demais telas. Cada domínio declara o
     * tp_origem e a coluna de id; o back-end valida esse tp_origem contra a lista de tipos
     * anotáveis (ver entidades_utils._DOMINIOS_UFDR_ANOTAVEIS). As contagens de comentário
     * são buscadas uma vez por domínio, na primeira vez que a aba é aberta. */
    var contagensEntidade = {};

    function celulaEntidade(aba, item) {
      var $td = $("<td>");
      var id = item[aba.entidade.id];
      if (id == null || !window.EntidadesUI) return $td.text("—");
      var contagens = contagensEntidade[aba.chave] || {};
      $td.append(EntidadesUI.botao({
        idInquerito: idInquerito,
        tpOrigem: aba.entidade.tp,
        idOrigem: id,
        rotulo: aba.entidade.rotulo ? aba.entidade.rotulo(item) : aba.rotulo,
        temComentario: !!contagens[id],
        aoMudar: function () {}
      }));
      return $td;
    }

    /* ----- filtros declarados pelo domínio (período, horário, categoria, faixa, marcadores) ---
     * Montados a cada troca de aba a partir de aba.filtros + do que a rota /facetas diz existir
     * neste aparelho: select sem valor nenhum não é criado, o período só aparece quando há
     * alguma data gravada e marcador que não casa com nada não é oferecido. Melhor não oferecer
     * filtro que só pode devolver lista vazia. */
    var facetasPorAba = {};

    // as mesmas faixas da tela de Arquivos, em hora de Brasília (ver FAIXAS_DIA no back-end)
    var FAIXAS_DIA = [
      { valor: "madrugada", rotulo: "Madrugada (00h–06h)" },
      { valor: "manha", rotulo: "Manhã (06h–12h)" },
      { valor: "tarde", rotulo: "Tarde (12h–18h)" },
      { valor: "noite", rotulo: "Noite (18h–00h)" }
    ];

    function inputData(rotulo, limites) {
      return $("<input>").attr({ type: "date", "aria-label": rotulo })
        .attr(limites || {}).addClass("arq-select")
        .on("change", function () { pagina = 1; carregar(); });
    }

    function inputValor(rotulo, dica) {
      return $("<input>").attr({ type: "number", step: "0.01", min: "0",
                                 "aria-label": rotulo, placeholder: dica })
        .addClass("arq-select filtro-numero")
        .on("change", function () { pagina = 1; carregar(); });
    }

    /* Atalho de período: os N dias que terminam no registro mais recente do aparelho — não em
     * hoje. A extração costuma ser de meses atrás, e "últimos 30 dias" contados de hoje devolve
     * lista vazia justo em quem tem dado antigo. */
    function atalhoPeriodo(rotulo, dias, fim) {
      $("<button>").addClass("filtro-chip").attr("type", "button").text(rotulo)
        .attr("title", "Os " + rotulo.toLowerCase() + " até o registro mais recente (" +
                       fim.split("-").reverse().join("/") + ")")
        .on("click", function () {
          var inicio = new Date(fim + "T00:00:00");   // local: a data do input não tem fuso
          inicio.setDate(inicio.getDate() - dias + 1);
          extras.de.val(dataIso(inicio));
          extras.ate.val(fim);
          pagina = 1;
          carregar();
        })
        .appendTo($atalhos);
    }

    function montarFiltrosDaAba(aba, dados) {
      $grade.find(".filtro-dinamico").remove();
      $atalhos.empty();
      $marcadores.empty();
      extras = { selects: [], de: null, ate: null, valorMin: null, valorMax: null,
                 horario: null, marcadores: [] };
      var cfg = aba.filtros;
      if (!cfg) { atualizarLimpar(); return; }
      dados = dados || {};

      (cfg.selects || []).forEach(function (declarado) {
        var valores = (dados.facetas || {})[declarado.campo] || [];
        if (!valores.length) return;
        var $sel = $("<select>").addClass("arq-select").data("campo", declarado.campo)
          .on("change", function () { pagina = 1; carregar(); });
        $("<option>").attr("value", "").text(declarado.todos || "Todos").appendTo($sel);
        valores.forEach(function (v) {
          $("<option>").attr("value", v.valor)
            .text(v.valor + "  (" + Number(v.qt).toLocaleString("pt-BR") + ")").appendTo($sel);
        });
        var salvo = paramEstado("f_" + declarado.campo);
        if (salvo) $sel.val(salvo);   // valor que não existe mais no aparelho cai de volta em "Todos"
        $sel.appendTo(campo(declarado.rotulo, "filtro-dinamico"));
        extras.selects.push($sel);
      });

      if (cfg.periodo && dados.periodo) {
        var limites = { min: dados.periodo.min, max: dados.periodo.max };
        var $c = campo(cfg.periodo.rotulo || "Período", "filtro-dinamico filtro-campo-intervalo");
        var $linha = $("<div>").addClass("filtro-intervalo").appendTo($c);
        extras.de = inputData("Data inicial", limites).appendTo($linha);
        $("<span>").addClass("filtro-ate").text("até").appendTo($linha);
        extras.ate = inputData("Data final", limites).appendTo($linha);
        extras.de.val(paramEstado("de"));
        extras.ate.val(paramEstado("ate"));
        ["7 dias", "30 dias", "12 meses"].forEach(function (rotulo, i) {
          atalhoPeriodo(rotulo, [7, 30, 365][i], dados.periodo.max);
        });
      }

      /* Faixa do dia: "o que ele buscou de madrugada" é pergunta de investigação, e o recorte
       * por data sozinho não responde. Só faz sentido junto de alguma data gravada. */
      if (cfg.horario && dados.periodo) {
        extras.horario = $("<select>").addClass("arq-select")
          .on("change", function () { pagina = 1; carregar(); });
        $("<option>").attr("value", "").text("Qualquer horário").appendTo(extras.horario);
        FAIXAS_DIA.forEach(function (f) {
          $("<option>").attr("value", f.valor).text(f.rotulo).appendTo(extras.horario);
        });
        extras.horario.val(paramEstado("faixa_dia"));
        extras.horario.appendTo(campo(cfg.horario.rotulo || "Faixa do dia", "filtro-dinamico"));
      }

      /* Marcadores: recorte liga/desliga com a contagem do que existe. Marcador que não casa com
       * nenhum registro deste aparelho não é montado. `padrao` nasce marcado (o "ocultar
       * feriados" do Calendário) — o investigador desliga quando quer ver a agenda inteira. */
      (cfg.marcadores || []).forEach(function (declarado) {
        var qt = ((dados.marcadores || {})[declarado.chave]) || 0;
        if (!qt) return;
        var naUrl = paramEstado("m_" + declarado.chave);
        var marcado = naUrl ? naUrl === "1" : !!declarado.padrao;
        var $rotulo = $("<label>").addClass("arq-check").attr("title", declarado.dica || "")
          .appendTo($marcadores);
        var $chk = $("<input>").attr("type", "checkbox").data("chave", declarado.chave)
          .data("padrao", !!declarado.padrao)
          .on("change", function () { pagina = 1; carregar(); })
          .appendTo($rotulo);
        $chk.prop("checked", marcado);
        $rotulo.append(document.createTextNode(
          " " + declarado.rotulo + " (" + Number(qt).toLocaleString("pt-BR") + ")"));
        extras.marcadores.push($chk);
      });

      if (cfg.faixa && dados.valor) {
        var $cv = campo(cfg.faixa.rotulo || "Valor", "filtro-dinamico filtro-campo-intervalo");
        var $lv = $("<div>").addClass("filtro-intervalo").appendTo($cv);
        extras.valorMin = inputValor("Valor mínimo", "mín").appendTo($lv);
        $("<span>").addClass("filtro-ate").text("a").appendTo($lv);
        extras.valorMax = inputValor("Valor máximo", "máx").appendTo($lv);
        extras.valorMin.val(paramEstado("valor_min"));
        extras.valorMax.val(paramEstado("valor_max"));
        $("<span>").addClass("filtro-dica")
          .text("de " + formatarNumero(dados.valor.min) + " a " + formatarNumero(dados.valor.max))
          .appendTo($cv);
      }
      atualizarLimpar();
    }

    function filtrosAtivos() {
      var qt = 0;
      if ($busca.val().trim()) qt += 1;
      if ($chkApagados.is(":checked")) qt += 1;
      extras.selects.forEach(function ($s) { if ($s.val()) qt += 1; });
      // marcador padrão (o "ocultar feriados") só conta como filtro quando sai do padrão —
      // senão toda tela limpa já abriria pedindo "limpe o filtro" que o usuário não tocou
      extras.marcadores.forEach(function ($c) {
        if ($c.is(":checked") !== !!$c.data("padrao")) qt += 1;
      });
      [extras.de, extras.ate, extras.valorMin, extras.valorMax, extras.horario]
        .forEach(function ($i) { if ($i && $i.val()) qt += 1; });
      return qt;
    }

    function atualizarLimpar() {
      var qt = filtrosAtivos();
      $limpar.toggle(qt > 0).text(qt > 1 ? "Limpar filtros (" + qt + ")" : "Limpar filtros");
    }

    function limparFiltros() {
      $busca.val("");
      $chkApagados.prop("checked", false);
      extras.selects.forEach(function ($s) { $s.val(""); });
      extras.marcadores.forEach(function ($c) { $c.prop("checked", !!$c.data("padrao")); });
      [extras.de, extras.ate, extras.valorMin, extras.valorMax, extras.horario]
        .forEach(function ($i) { if ($i) $i.val(""); });
      pagina = 1;
      carregar();
    }

    /* Monta a URL da aba atual com a página pedida — usada tanto pela carga normal quanto pela
     * varredura de deep-link, que passa outra página. `sobrescrita` ({de, ate, limite}) permite
     * à vista de mês pedir UM MÊS inteiro respeitando os demais filtros, sem mexer nos campos
     * de período que o investigador viu na tela. */
    function montarUrl(paginaAlvo, sobrescrita) {
      sobrescrita = sobrescrita || {};
      var aba = abaPorChave(abaAtual);
      var url = "/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
        "/ufdr/" + encodeURIComponent(aba.chave) +
        "?extracao=" + encodeURIComponent(idExtracao) +
        "&pagina=" + paginaAlvo + "&limite=" + (sobrescrita.limite || LIMITE);
      if (ordemAtual) url += "&ordenar=" + encodeURIComponent(ordemAtual);
      var termo = $busca.val().trim();
      if (termo) url += "&busca=" + encodeURIComponent(termo);
      if ($chkApagados.is(":checked")) url += "&apagados=true";
      extras.selects.forEach(function ($s) {
        if ($s.val()) url += "&f_" + $s.data("campo") + "=" + encodeURIComponent($s.val());
      });
      extras.marcadores.forEach(function ($c) {
        if ($c.is(":checked")) url += "&m_" + $c.data("chave") + "=true";
      });
      [["de", extras.de], ["ate", extras.ate], ["faixa_dia", extras.horario],
       ["valor_min", extras.valorMin], ["valor_max", extras.valorMax]].forEach(function (par) {
        var valor = sobrescrita[par[0]] != null ? sobrescrita[par[0]]
          : (par[1] && par[1].val()) || "";
        if (valor) url += "&" + par[0] + "=" + encodeURIComponent(valor);
      });
      // recorte fixo de quem montou (ex.: o modal de uso abre já preso a um aplicativo)
      Object.keys(config.filtroExtra || {}).forEach(function (k) {
        url += "&" + k + "=" + encodeURIComponent(config.filtroExtra[k]);
      });
      return url;
    }

    /* Deep-link ?destacar=<id> vindo do link da evidência (busca_entidades): realça a linha do
     * registro achado. Só age quando o id bate com alguma linha da página carregada. */
    function destacarRegistro() {
      if (!idDestacar) return;
      var $linha = $corpo.find('[data-id="' + idDestacar + '"]');
      if (!$linha.length) return;
      $linha.addClass("tp-destacada");
      $linha[0].scrollIntoView({ block: "center" });
      idDestacar = null;
      if (window.EstadoUrl) window.EstadoUrl.salvar({ destacar: null });
    }

    /* O registro pode não estar na primeira página (a lista ordena por data desc.) — varre as
     * páginas em segundo plano até achar a linha, então renderiza a página certa. */
    function localizarDestacado() {
      if (!idDestacar || procurandoDestacado) return;
      var aba = abaPorChave(abaAtual);
      if (!aba || !aba.entidade) return;
      procurandoDestacado = true;
      var paginaAlvo = 1;
      var teto = 50;

      (function proxima() {
        $.getJSON(montarUrl(paginaAlvo)).done(function (dados) {
          var itens = dados.itens || [];
          var achou = itens.some(function (item) {
            return item[aba.entidade.id] != null &&
              String(item[aba.entidade.id]) === String(idDestacar);
          });
          if (achou) {
            pagina = paginaAlvo;
            procurandoDestacado = false;
            carregar();
          } else if (paginaAlvo < teto && paginaAlvo * LIMITE < (dados.total || 0)) {
            paginaAlvo += 1;
            proxima();
          } else {
            procurandoDestacado = false;
          }
        }).fail(function () { procurandoDestacado = false; });
      })();
    }

    function carregar() {
      var aba = abaPorChave(abaAtual);
      if (!aba) return;
      salvarEstado();
      if (vista === "mes") { carregarMes(); return; }   // filtros valem nas duas vistas
      var url = montarUrl(pagina);
      atualizarLimpar();

      seqCarga += 1;
      var minhaCarga = seqCarga;
      $corpo.empty();
      $info.text("Carregando…");
      $.getJSON(url).done(function (dados) {
        if (minhaCarga !== seqCarga) return;   // chegou depois de outra carga mais nova
        var itens = dados.itens || [];
        itens.forEach(function (item) {
          var $tr = $("<tr>");
          if (aba.entidade && item[aba.entidade.id] != null) {
            $tr.attr("data-id", item[aba.entidade.id]);
          }
          aba.colunas.forEach(function (c) { celula(item, c).appendTo($tr); });
          var $situacao = $("<td>");
          var marca = marcaEstado(item.cd_status_ufdr);
          $situacao.append(marca || document.createTextNode("Íntegro"));
          $situacao.appendTo($tr);
          if (aba.entidade) celulaEntidade(aba, item).appendTo($tr);
          $corpo.append($tr);
        });
        destacarRegistro();
        if (idDestacar) localizarDestacado();
        $vazio.toggle(itens.length === 0);
        if (itens.length === 0) {
          // "não existe no aparelho" e "nada bate com o filtro" são conclusões diferentes
          $vazio.text(filtrosAtivos() > 0
            ? "Nenhum registro com esses filtros."
            : (aba.vazio || "Nada encontrado neste domínio."));
        }
        var ultimaPagina = Math.max(1, Math.ceil(dados.total / LIMITE));
        $info.text(dados.total.toLocaleString("pt-BR") + " registro(s) · página " +
          dados.pagina + " de " + ultimaPagina);
        $anterior.prop("disabled", dados.pagina <= 1);
        $proxima.prop("disabled", dados.pagina >= ultimaPagina);
        $total.text(dados.total.toLocaleString("pt-BR") + " " + (aba.unidade || "registro(s)"));
        if (aba.ficha) desenharFicha(aba);
      }).fail(function () {
        $info.text("");
        $vazio.text("Não foi possível carregar os dados.").show();
      });
    }

    function trocarAba(chave) {
      abaAtual = chave;
      // a aba ativa vive na URL (?aba=): reabrir o link — ou colá-lo para um colega — cai na
      // mesma aba com os mesmos filtros. A primeira aba não precisa da chave (URL limpa)
      if (config.estadoUrl && window.EstadoUrl && primeiraAba) {
        EstadoUrl.salvar({ aba: chave === primeiraAba ? "" : chave });
      }
      pagina = 1;
      // a página salva na URL é a da primeira aba aberta (quem navega por abas muda de
      // contexto — a página da aba anterior não significa nada na nova)
      if (config.estadoUrl && !paginaRestaurada) {
        paginaRestaurada = true;
        var p = parseInt(paramEstado("pagina"), 10);
        if (p > 0) pagina = p;
      }
      // ordenação é por aba: o ?ordenar= da URL só vale para a aba inicial (e só se alguma
      // coluna dela declara essa chave — a URL é editável); trocar de aba volta à ordem padrão
      if (!ordemRestaurada) {
        ordemRestaurada = true;
        var cfgIni = abaPorChave(chave);
        var o = config.estadoUrl && cfgIni && !cfgIni.externo ? paramEstado("ordenar") : "";
        ordemAtual = chaveAtiva(o, cfgIni) ? o : null;
      } else {
        ordemAtual = null;
      }
      $abas.find(".ufdr-aba").each(function () {
        $(this).attr("aria-selected", String($(this).data("chave") === chave));
      });
      Object.keys(externas).forEach(function (k) { externas[k].toggle(k === chave); });

      var externa = externas[chave];
      var proprio = !externa;
      $filtros.toggle(proprio);

      // a barra Lista|Mês só existe em aba própria que declara vistaMes; nas outras a volta
      // à tabela é automática
      var cfgAba = abaPorChave(chave);
      // a ficha é o painel de UMA aba — ao trocar, some até a dona dela carregar de novo
      $ficha.toggle(proprio && !!(cfgAba && cfgAba.ficha));
      var temVistas = proprio && !!(cfgAba && cfgAba.vistaMes);
      $barraVistas.toggle(temVistas);
      if (!temVistas) vista = "lista";
      if (temVistas && !vistaRestaurada) {
        vistaRestaurada = true;
        if (paramEstado("vista") === "mes") vista = "mes";   // a URL puxa direto pra grade
      }
      cacheMes = {};   // troca de aba pede mês buscado de novo (a chave já seria outra, mas
                       // o cache não precisa sobreviver a trocar de tela)
      mesInicializado = false;   // cada aba abre no mês do seu registro mais recente
      var ehMes = temVistas && vista === "mes";
      $mes.toggle(ehMes);
      $envolucro.toggle(proprio && !ehMes);
      $pag.toggle(proprio && !ehMes);
      atualizarBotoesVista();
      if (config.aoTrocarAba) config.aoTrocarAba(chave, proprio);
      if (!proprio) {
        $vazio.hide();
        return;   // a página cuida do conteúdo e do total desta aba
      }
      var aba = abaPorChave(chave);
      desenharCabecalho(aba);
      // filtros do domínio: as facetas são buscadas uma vez por aba e ficam em memória
      if (!aba.filtros) {
        montarFiltrosDaAba(aba, null);
      } else if (facetasPorAba[chave] !== undefined) {
        montarFiltrosDaAba(aba, facetasPorAba[chave]);
      } else {
        montarFiltrosDaAba(aba, null);   // sem os extras até a resposta chegar
        facetasPorAba[chave] = null;     // marca como pedida, para não repetir a busca
        $.getJSON("/api/investigacao/inqueritos/" + encodeURIComponent(idInquerito) +
                  "/ufdr/" + encodeURIComponent(chave) + "/facetas" +
                  "?extracao=" + encodeURIComponent(idExtracao))
          .done(function (dados) {
            facetasPorAba[chave] = dados || {};
            if (abaAtual === chave) montarFiltrosDaAba(aba, facetasPorAba[chave]);
          })
          .fail(function () { delete facetasPorAba[chave]; });   // permite tentar de novo
      }
      if (aba.entidade && window.EntidadesUI && contagensEntidade[chave] === undefined) {
        contagensEntidade[chave] = {};   // marca como pedida, para não repetir a busca
        EntidadesUI.carregarContagens(idInquerito, aba.entidade.tp, function (c) {
          contagensEntidade[chave] = c || {};
          if (abaAtual === chave) carregar();
        });
      }
      carregar();
    }

    $anterior.on("click", function () { if (pagina > 1) { pagina -= 1; carregar(); } });
    $proxima.on("click", function () { pagina += 1; carregar(); });
    $chkApagados.on("change", function () { pagina = 1; carregar(); });
    $limpar.on("click", limparFiltros);
    $busca.on("input", function () {
      clearTimeout(buscaTimer);
      buscaTimer = setTimeout(function () { pagina = 1; carregar(); }, 300);
    });

    // As contagens vêm de uma chamada só e decidem quais abas existem: mostrar aba vazia de um
    // domínio que este aparelho não tem (antena de telefonia só aparece em iPhone, dicionário
    // só em Android) faz o investigador procurar dado que não existe. Aba externa é sempre
    // mostrada — é o conteúdo principal da página, e quem conta é a própria página.
    $.getJSON("/api/investigacao/extracoes/" + encodeURIComponent(idExtracao) + "/ufdr-contagens")
      .done(function (resp) {
        var contagens = (resp && resp.contagens) || {};
        var visiveis = config.abas.filter(function (a) {
          return a.externo || Number(contagens[a.chave]) > 0;
        });
        var proprias = config.abas.filter(function (a) { return !a.externo; });
        var todasNulas = proprias.length > 0 &&
          proprias.every(function (a) { return contagens[a.chave] == null; });
        if (!visiveis.length) {
          $abas.hide(); $filtros.hide(); $envolucro.hide(); $pag.hide();
          $vazio.show().text(todasNulas
            ? "Esta extração foi carregada antes destes dados passarem a ser importados. " +
              "Refaça a extração do .ufdr para trazê-los."
            : "Este aparelho não tem nenhum registro destes tipos.");
          return;
        }
        visiveis.forEach(function (aba) {
          var $b = $("<button>")
            .addClass("ufdr-aba")
            .attr({ role: "tab", "aria-selected": "false", type: "button" })
            .data("chave", aba.chave)
            .append(document.createTextNode(aba.rotulo))
            .on("click", function () { trocarAba($(this).data("chave")); })
            .appendTo($abas);
          $("<span>").addClass("ufdr-aba-qt")
            .text(aba.externo ? "" : Number(contagens[aba.chave]).toLocaleString("pt-BR"))
            .appendTo($b);
        });
        // com uma aba só não há o que escolher; a barra some e sobra a tabela
        $abas.toggle(visiveis.length > 1);
        primeiraAba = visiveis[0].chave;
        // ?aba= da URL escolhe a aba inicial — só vale se a aba existe nesta extração
        // (um aparelho sem cookies não pode abrir na aba de cookies de um link antigo)
        var abaUrl = paramEstado("aba");
        var inicial = visiveis.filter(function (a) { return a.chave === abaUrl; })[0] || visiveis[0];
        trocarAba(inicial.chave);
      })
      .fail(function () {
        $vazio.show().text("Não foi possível carregar as contagens desta extração.");
      });

    return {
      /* A página informa quantos registros a aba externa tem, assim que carrega os dela. */
      definirContagem: function (chave, n) {
        $abas.find(".ufdr-aba").each(function () {
          if ($(this).data("chave") === chave) {
            $(this).find(".ufdr-aba-qt").text(n == null ? "" : Number(n).toLocaleString("pt-BR"));
          }
        });
      },
      abaAtiva: function () { return abaAtual; }
    };
  }

  /* Colunas de cada domínio, em um lugar só: as telas novas e as abas acrescentadas às telas
   * antigas (Wi-Fi, Navegação, Arquivos, Aplicativos, Localizações, Ligações, Contas) montam
   * daqui, então uma coluna acertada vale para todas. A chave é a mesma da rota da API. */
  var COLUNAS = {
    "uso-apps": {
      entidade: { tp: "uso_app", id: "id_uso_app", rotulo: function (i) { return i.nu_pacote || "uso de aplicativo"; } },
      rotulo: "Uso de aplicativos", unidade: "eventos de uso",
      vazio: "Nenhum registro de uso de aplicativo neste aparelho.",
      colunas: [
        { campo: "dt_inicio", rotulo: "Quando", tipo: "data" },
        { campo: "nu_pacote", rotulo: "Aplicativo", tipo: "mono" },
        { campo: "tp_acao", rotulo: "Ação", tipo: "tag" },
        { campo: "dt_fim", rotulo: "Fim", tipo: "data" },
        { campo: "no_submodulo", rotulo: "Parte do app" },
        { campo: "no_aplicativo", rotulo: "Registrado por" }
      ]
    },
    "dispositivos-reconhecidos": {
      entidade: { tp: "dispositivo_reconhecido", id: "id_dispositivo", rotulo: function (i) { return i.no_dispositivo || i.no_mac || "dispositivo reconhecido"; } },
      rotulo: "Dispositivos reconhecidos", unidade: "dispositivos",
      vazio: "Nenhum dispositivo reconhecido por este aparelho.",
      /* sem período para filtrar (o domínio não tem data): o que separa aqui é o tipo do
       * aparelho e o que a linha tem de identificável — MAC e série são o elo com outro
       * aparelho, e "unidos" mostra só o que a mesclagem formou de vários aplicativos. */
      filtros: {
        selects: [
          { campo: "tp_dispositivo", rotulo: "Tipo", todos: "Todos os tipos" }
        ],
        marcadores: [
          { chave: "com_mac", rotulo: "Só com MAC",
            dica: "Aparelhos identificáveis por MAC — os que ligam a outro aparelho no mesmo lugar" },
          { chave: "com_serie", rotulo: "Só com nº de série",
            dica: "Registros que trouxeram número de série, além do nome e do modelo" },
          { chave: "unidos", rotulo: "Só unidos de várias fontes",
            dica: "Linhas formadas de mais de um registro (KnowledgeC, Biome, Find My) mesclados no mesmo aparelho" }
        ]
      },
      colunas: [
        { campo: "no_dispositivo", rotulo: "Dispositivo" },
        { campo: "no_mac", rotulo: "MAC", tipo: "mono" },
        { campo: "tp_dispositivo", rotulo: "Tipo", tipo: "tag" },
        { campo: "no_modelo", rotulo: "Modelo" },
        { campo: "nu_serie", rotulo: "Nº de série", tipo: "mono" },
        { campo: "tx_identificadores", rotulo: "Outros identificadores", tipo: "longo" },
        { campo: "no_aplicativo", rotulo: "Fonte" },
        { campo: "qt_registros", rotulo: "Registros", tipo: "unidos" }
      ]
    },
    "conexoes": {
      entidade: { tp: "conexao", id: "id_conexao", rotulo: function (i) { return i.no_dispositivo || i.no_mac || "conexão"; } },
      rotulo: "Conexões", unidade: "conexões",
      vazio: "Nenhuma conexão de Wi-Fi ou Bluetooth registrada.",
      colunas: [
        { campo: "dt_conexao", rotulo: "Quando", tipo: "data" },
        { campo: "tp_metodo", rotulo: "Meio", tipo: "tag" },
        { campo: "no_dispositivo", rotulo: "Dispositivo" },
        { campo: "no_mac", rotulo: "MAC", tipo: "mono" },
        { campo: "tp_natureza", rotulo: "Natureza" },
        { campo: "tp_dispositivo", rotulo: "Tipo" },
        { campo: "no_aplicativo", rotulo: "Fonte" }
      ]
    },
    cookies: {
      entidade: { tp: "cookie", id: "id_cookie", rotulo: function (i) { return i.no_dominio || "cookie"; } },
      rotulo: "Cookies", unidade: "cookies",
      vazio: "Nenhum cookie extraído.",
      // a tabela quase sempre é a maior da extração — sem recorte por domínio/período a tela
      // só permite pescar por texto (as facetas e os marcadores batem com DOMINIOS no back-end)
      filtros: {
        periodo: { rotulo: "Último acesso" },
        horario: true,
        selects: [
          { campo: "no_dominio", rotulo: "Domínio", todos: "Todos os domínios" },
          { campo: "no_aplicativo", rotulo: "Navegador", todos: "Todos os navegadores" },
          { campo: "no_app_relacionado", rotulo: "App que gravou", todos: "Todos os apps" }
        ],
        marcadores: [
          { chave: "expirados", rotulo: "Expirados",
            dica: "Cookies cuja data de expiração já passou — o navegador não os usaria mais" },
          { chave: "expira_breve", rotulo: "Expiram em 30 dias",
            dica: "Ainda vivos, mas com expiração marcada para o próximo mês" },
          { chave: "sessao", rotulo: "Sem expiração (sessão)",
            dica: "Cookies sem data de expiração: existem só enquanto o navegador não fecha" }
        ]
      },
      colunas: [
        { campo: "no_dominio", rotulo: "Domínio", tipo: "mono", ordenar: { asc: "dominio" } },
        { campo: "no_cookie", rotulo: "Nome", ordenar: { asc: "nome" } },
        { campo: "tx_caminho", rotulo: "Caminho", tipo: "mono" },
        { campo: "tx_valor", rotulo: "Valor", tipo: "longo" },
        { campo: "dt_ultimo_acesso", rotulo: "Último acesso", tipo: "data",
          ordenar: { desc: "acesso_desc", asc: "acesso_asc", padrao: "desc" } },
        { campo: "dt_criacao", rotulo: "Criado em", tipo: "data",
          ordenar: { desc: "criacao_desc", asc: "criacao_asc", padrao: "desc" } },
        { campo: "dt_expiracao", rotulo: "Expira em", tipo: "data",
          ordenar: { desc: "expiracao_desc", asc: "expiracao_asc", padrao: "desc" } },
        { campo: "no_app_relacionado", rotulo: "Aplicativo", ordenar: { asc: "app" } }
      ]
    },
    favoritos: {
      entidade: { tp: "favorito", id: "id_favorito", rotulo: function (i) { return i.tx_titulo || i.tx_url || "favorito"; } },
      rotulo: "Favoritos", unidade: "favoritos",
      vazio: "Nenhum favorito de navegador.",
      filtros: {
        periodo: { rotulo: "Última visita" },
        horario: true,
        selects: [
          { campo: "no_aplicativo", rotulo: "Navegador", todos: "Todos os navegadores" },
          { campo: "tx_pasta", rotulo: "Pasta", todos: "Todas as pastas" }
        ]
      },
      colunas: [
        { campo: "tx_titulo", rotulo: "Título", ordenar: { asc: "titulo" } },
        { campo: "tx_url", rotulo: "Endereço", tipo: "url" },
        { campo: "tx_pasta", rotulo: "Pasta" },
        { campo: "qt_visitas", rotulo: "Visitas", tipo: "numero",
          ordenar: { desc: "visitas_desc", asc: "visitas_asc", padrao: "desc" } },
        { campo: "dt_ultima_visita", rotulo: "Última visita", tipo: "data",
          ordenar: { desc: "visita_desc", asc: "visita_asc", padrao: "desc" } },
        { campo: "no_aplicativo", rotulo: "Navegador", tipo: "tag" }
      ]
    },
    downloads: {
      entidade: { tp: "download", id: "id_download", rotulo: function (i) { return i.tx_url || "download"; } },
      rotulo: "Downloads", unidade: "downloads",
      vazio: "Nenhum download registrado.",
      filtros: {
        periodo: { rotulo: "Iniciado em" },
        horario: true,
        selects: [
          { campo: "no_aplicativo", rotulo: "Aplicativo", todos: "Todos os aplicativos" },
          { campo: "cd_situacao", rotulo: "Situação", todos: "Todas as situações" }
        ],
        marcadores: [
          { chave: "incompletos", rotulo: "Incompletos",
            dica: "Downloads que receberam menos bytes do que o tamanho anunciado (ou nada)" }
        ]
      },
      colunas: [
        { campo: "dt_inicio", rotulo: "Quando", tipo: "data",
          ordenar: { desc: "inicio_desc", asc: "inicio_asc", padrao: "desc" } },
        { campo: "tx_url", rotulo: "Origem", tipo: "url", ordenar: { asc: "url" } },
        { campo: "tx_destino", rotulo: "Salvo em", tipo: "longo" },
        { campo: "qt_bytes", rotulo: "Tamanho", tipo: "bytes",
          ordenar: { desc: "bytes_desc", asc: "bytes_asc", padrao: "desc" } },
        { campo: "qt_bytes_recebidos", rotulo: "Recebido", tipo: "bytes" },
        { campo: "cd_situacao", rotulo: "Situação", tipo: "tag" },
        { campo: "no_aplicativo", rotulo: "Aplicativo" }
      ]
    },
    autofill: {
      entidade: { tp: "autofill", id: "id_autofill", rotulo: function (i) { return i.no_chave || "preenchimento automático"; } },
      rotulo: "Preenchimento automático", unidade: "entradas",
      vazio: "Nenhum dado de preenchimento automático.",
      filtros: {
        periodo: { rotulo: "Último uso" },
        selects: [
          { campo: "no_fonte", rotulo: "Origem", todos: "Todas as origens" }
        ]
      },
      colunas: [
        { campo: "no_chave", rotulo: "Campo", ordenar: { desc: "campo_desc" } },
        { campo: "tx_valor", rotulo: "Valor", tipo: "longo" },
        { campo: "no_fonte", rotulo: "Origem", tipo: "tag" },
        { campo: "dt_ultimo_uso", rotulo: "Último uso", tipo: "data",
          ordenar: { desc: "uso_desc", asc: "uso_asc", padrao: "desc" } },
        { campo: "dt_registro", rotulo: "Registrado em", tipo: "data",
          ordenar: { desc: "registro_desc", padrao: "desc" } }
      ]
    },
    senhas: {
      rotulo: "Senhas salvas", unidade: "senhas",
      vazio: "Nenhuma senha ou segredo do chaveiro.",
      colunas: [
        { campo: "no_servico", rotulo: "Serviço", tipo: "tag" },
        { campo: "no_usuario", rotulo: "Usuário" },
        { campo: "tx_dados", rotulo: "Senha/dados", tipo: "mono" },
        { campo: "tp_senha", rotulo: "Tipo", tipo: "tag" },
        { campo: "no_grupo_acesso", rotulo: "Grupo de acesso (chaveiro)", tipo: "mono" },
        { campo: "no_fonte", rotulo: "Fonte" }
      ]
    },
    "uso-rede": {
      entidade: { tp: "uso_rede", id: "id_uso_rede", rotulo: function (i) { return i.no_ssid || "uso de rede"; } },
      rotulo: "Uso de rede", unidade: "registros",
      vazio: "Nenhum registro de consumo de rede.",
      filtros: {
        periodo: { rotulo: "Início" },
        horario: true,
        selects: [
          { campo: "tp_conexao", rotulo: "Tipo", todos: "Todos os tipos" }
        ],
        marcadores: [
          { chave: "roaming", rotulo: "Em roaming",
            dica: "Tráfego consumido fora da rede da operadora de casa — o recorte que liga a tela a uma viagem" }
        ]
      },
      colunas: [
        { campo: "dt_inicio", rotulo: "Início", tipo: "data",
          ordenar: { desc: "inicio_desc", asc: "inicio_asc", padrao: "desc" } },
        { campo: "dt_fim", rotulo: "Fim", tipo: "data" },
        { campo: "no_ssid", rotulo: "Rede" },
        { campo: "tp_conexao", rotulo: "Tipo", tipo: "tag" },
        { campo: "qt_bytes_recebidos", rotulo: "Recebido", tipo: "bytes",
          ordenar: { desc: "recv_desc", padrao: "desc" } },
        { campo: "qt_bytes_enviados", rotulo: "Enviado", tipo: "bytes",
          ordenar: { desc: "env_desc", padrao: "desc" } },
        { campo: "in_roaming", rotulo: "Roaming", tipo: "bool" }
      ]
    },
    "atividade-social": {
      entidade: { tp: "atividade_social", id: "id_atividade", rotulo: function (i) { return i.tx_corpo || i.tp_atividade || "publicação"; } },
      rotulo: "Atividade em redes sociais", unidade: "publicações",
      vazio: "Nenhuma publicação, comentário ou check-in extraído.",
      /* "Site" é o host do endereço da publicação (facebook.com, instagram.com) — o back-end
       * calcula a faceta a partir de TX_URL, então ela não é coluna da tabela. */
      filtros: {
        periodo: { rotulo: "Publicado em" },
        horario: true,
        selects: [
          { campo: "no_aplicativo", rotulo: "Aplicativo", todos: "Todos os aplicativos" },
          { campo: "dominio", rotulo: "Site", todos: "Todos os sites" },
          { campo: "tp_atividade", rotulo: "Tipo", todos: "Todos os tipos" },
          { campo: "no_autor", rotulo: "Autor", todos: "Todos os autores" }
        ],
        marcadores: [
          { chave: "com_anexo", rotulo: "Só com anexo",
            dica: "Publicações que trouxeram foto, vídeo ou arquivo junto" },
          { chave: "com_local", rotulo: "Só com localização",
            dica: "Publicações com coordenada gravada (check-in, foto geolocalizada)" }
        ]
      },
      colunas: [
        { campo: "dt_atividade", rotulo: "Quando", tipo: "data" },
        { campo: "tp_atividade", rotulo: "Tipo", tipo: "tag" },
        { campo: "no_autor", rotulo: "Autor" },
        { campo: "tx_corpo", rotulo: "Conteúdo", tipo: "longo" },
        { campo: "tx_url", rotulo: "Endereço", tipo: "url" },
        { campo: "vl_latitude", campoLon: "vl_longitude", rotulo: "Local", tipo: "coord" },
        { campo: "tx_marcados", rotulo: "Marcados", tipo: "longo" },
        { campo: "tx_anexos", rotulo: "Anexos", tipo: "anexos" },
        { campo: "qt_reacoes", rotulo: "Reações", tipo: "numero" },
        { campo: "qt_comentarios", rotulo: "Comentários", tipo: "numero" },
        { campo: "no_conta", rotulo: "Conta" }
      ]
    },
    buscas: {
      entidade: { tp: "busca", id: "id_busca", rotulo: function (i) { return i.tx_termo || "busca"; } },
      rotulo: "Buscas", unidade: "buscas",
      vazio: "Nenhum termo de busca extraído.",
      filtros: {
        periodo: { rotulo: "Buscado em" },
        horario: true,
        selects: [
          { campo: "no_aplicativo", rotulo: "Onde", todos: "Em qualquer app" },
          { campo: "no_origem", rotulo: "Perfil/mecanismo", todos: "Todos" },
          { campo: "no_conta", rotulo: "Conta", todos: "Todas as contas" }
        ],
        marcadores: [
          { chave: "com_local", rotulo: "Só com localização",
            dica: "Buscas com coordenada gravada (mapas, buscas locais)" }
        ]
      },
      colunas: [
        { campo: "dt_busca", rotulo: "Quando", tipo: "data" },
        { campo: "tx_termo", rotulo: "Termo buscado", tipo: "longo" },
        { campo: "no_aplicativo", rotulo: "Onde", tipo: "tag" },
        { campo: "no_origem", rotulo: "Perfil/mecanismo" },
        { campo: "vl_latitude", campoLon: "vl_longitude", rotulo: "Local", tipo: "coord" },
        { campo: "tx_resultados", rotulo: "Resultados", tipo: "longo" },
        { campo: "no_conta", rotulo: "Conta" }
      ]
    },
    "envios-nuvem": {
      entidade: { tp: "envio_nuvem", id: "id_envio", rotulo: function (i) { return i.no_arquivo || "envio para a nuvem"; } },
      rotulo: "Enviados para a nuvem", unidade: "envios",
      vazio: "Nenhum envio para a nuvem registrado.",
      colunas: [
        { campo: "dt_envio", rotulo: "Quando", tipo: "data" },
        { campo: "no_arquivo", rotulo: "Arquivo" },
        { campo: "no_pasta", rotulo: "Pasta" },
        { campo: "no_conta", rotulo: "Conta" },
        { campo: "no_dono", rotulo: "Dono" },
        { campo: "tx_compartilhamento", rotulo: "Compartilhamento", tipo: "longo" },
        { campo: "tp_arquivo", rotulo: "Tipo", tipo: "tag" },
        { campo: "tx_url", rotulo: "Endereço", tipo: "url" }
      ]
    },
    gravacoes: {
      entidade: { tp: "gravacao", id: "id_gravacao", rotulo: function (i) { return i.tx_titulo || "gravação"; } },
      rotulo: "Gravações", unidade: "gravações",
      vazio: "Nenhuma gravação de áudio registrada.",
      colunas: [
        { campo: "dt_gravacao", rotulo: "Quando", tipo: "data" },
        { campo: "tx_titulo", rotulo: "Título" },
        { campo: "qt_duracao_segundos", rotulo: "Duração", tipo: "duracao" },
        { campo: "tp_gravacao", rotulo: "Tipo", tipo: "tag" },
        { campo: "no_aplicativo", rotulo: "Aplicativo" }
      ]
    },
    calendario: {
      entidade: { tp: "compromisso", id: "id_compromisso", rotulo: function (i) { return i.tx_assunto || "compromisso"; } },
      rotulo: "Calendário", unidade: "compromissos",
      vazio: "Nenhum compromisso na agenda deste aparelho.",
      vistaMes: true,
      filtros: {
        periodo: { rotulo: "Início em" },
        selects: [
          { campo: "no_aplicativo", rotulo: "Aplicativo", todos: "Todos os apps" },
          { campo: "no_categoria", rotulo: "Categoria" },
          { campo: "no_conta", rotulo: "Conta" },
          { campo: "cd_situacao", rotulo: "Resposta", todos: "Qualquer" }
        ],
        marcadores: [
          { chave: "sem_feriados", rotulo: "Ocultar datas comemorativas",
            dica: "Esconde os feriados que o próprio calendário injeta (\"Feriados no Brasil\", " +
                  "\"Holidays in Brazil\", feeds #holiday@group.v.calendar.google.com)",
            padrao: true },
          { chave: "recorrente", rotulo: "Recorrentes", dica: "Só os que se repetem (diária, semanal, mensal…)" },
          { chave: "com_local", rotulo: "Com local", dica: "Só os com local escrito ou coordenada" },
          { chave: "com_convidados", rotulo: "Com convidados", dica: "Só os que listam convidados" }
        ]
      },
      colunas: [
        { campo: "dt_inicio", rotulo: "Início", tipo: "data" },
        { campo: "dt_fim", rotulo: "Fim", tipo: "data" },
        { campo: "tx_assunto", rotulo: "Assunto" },
        { campo: "cd_situacao", rotulo: "Resposta", tipo: "situacao" },
        { campo: "tx_local", rotulo: "Local" },
        { campo: "tx_convidados", rotulo: "Convidados", tipo: "longo" },
        { campo: "tx_detalhes", rotulo: "Detalhes", tipo: "longo" },
        { campo: "tx_repeticao", rotulo: "Repetição", tipo: "repeticao" },
        { campo: "tx_lembretes", rotulo: "Lembrete" },
        { campo: "no_organizador", rotulo: "Organizador" },
        { campo: "vl_latitude", campoLon: "vl_longitude", rotulo: "Coordenada", tipo: "coord" },
        { campo: "no_conta", rotulo: "Conta" }
      ]
    },
    "uso-aplicativo": {
      entidade: { tp: "uso_aplicativo", id: "id_uso", rotulo: function (i) { return i.nu_pacote || "uso de aplicativo"; } },
      rotulo: "Tempo de uso", unidade: "aplicativos",
      vazio: "Nenhum resumo de uso por aplicativo.",
      colunas: [
        { campo: "nu_pacote", rotulo: "Aplicativo", tipo: "mono" },
        { campo: "qt_tempo_ativo", rotulo: "Em primeiro plano", tipo: "duracao" },
        { campo: "qt_tempo_fundo", rotulo: "Em segundo plano", tipo: "duracao" },
        { campo: "qt_aberturas", rotulo: "Aberturas", tipo: "numero" },
        { campo: "qt_ativacoes", rotulo: "Ativações", tipo: "numero" },
        { campo: "dt_ultima_abertura", rotulo: "Última abertura", tipo: "data" },
        { campo: "qt_ultima_duracao", rotulo: "Último uso", tipo: "duracao" }
      ]
    },
    trajetos: {
      entidade: { tp: "trajeto", id: "id_trajeto", rotulo: function (i) { return i.tx_endereco_destino || "trajeto"; } },
      rotulo: "Trajetos", unidade: "trajetos",
      vazio: "Nenhum trajeto registrado.",
      colunas: [
        { campo: "dt_inicio", rotulo: "Início", tipo: "data" },
        { campo: "tx_endereco_origem", rotulo: "Origem", tipo: "longo" },
        { campo: "vl_lat_origem", campoLon: "vl_lon_origem", rotulo: "Coord. origem", tipo: "coord" },
        { campo: "tx_endereco_destino", rotulo: "Destino", tipo: "longo" },
        { campo: "vl_lat_destino", campoLon: "vl_lon_destino", rotulo: "Coord. destino", tipo: "coord" },
        { campo: "qt_pontos", rotulo: "Pontos", tipo: "numero" },
        { campo: "tp_trajeto", rotulo: "Tipo", tipo: "tag" },
        { campo: "no_aplicativo", rotulo: "Aplicativo" }
      ]
    },
    donos: {
      rotulo: "Dono do aparelho", unidade: "dono(s)",
      vazio: "Nenhum dono identificado nesta extração.",
      colunas: [
        { campo: "no_dono", rotulo: "Nome" },
        { campo: "tp_usuario", rotulo: "Tipo de usuário", tipo: "tag" },
        { campo: "nu_identificador", rotulo: "Identificador", tipo: "mono" },
        { campo: "dt_ultimo_login", rotulo: "Último login", tipo: "data" }
      ]
    },
    "contas-usuario": {
      rotulo: "Contas no aparelho", unidade: "contas",
      vazio: "Nenhuma conta de usuário encontrada.",
      colunas: [
        { campo: "no_usuario", rotulo: "Usuário", tipo: "longo" },
        { campo: "no_servico", rotulo: "Serviço", tipo: "tag" },
        { campo: "no_titular", rotulo: "Titular" },
        { campo: "no_fonte", rotulo: "Fonte" },
        { campo: "dt_criacao", rotulo: "Criada em", tipo: "data" }
      ]
    },
    "info-aparelho": {
      entidade: { tp: "info_aparelho", id: "id_info", rotulo: function (i) { return i.no_campo || "informação do aparelho"; } },
      rotulo: "Identificação", unidade: "informações",
      vazio: "Nenhuma informação de identificação do aparelho.",
      // ficha-resumo acima da tabela: IMEI, série, Apple ID, chips — o motivo da tela existir
      ficha: true,
      colunas: [
        { campo: "no_campo", rotulo: "Campo", tipo: "campo" },
        { campo: "tx_valor", rotulo: "Valor", tipo: "valores" },
        { campo: "no_categoria", rotulo: "Categoria", tipo: "tag" },
        { campo: "dt_registro", rotulo: "Primeiro registro", tipo: "data" }
      ]
    },
    antenas: {
      entidade: { tp: "antena", id: "id_antena", rotulo: function (i) { return "antena " + (i.nu_cid || ""); } },
      rotulo: "Antenas de telefonia", unidade: "antenas",
      vazio: "Nenhuma antena registrada (só aparece em iPhone).",
      colunas: [
        { campo: "dt_registro", rotulo: "Quando", tipo: "data" },
        // MCC+MNC são o código da operadora — o nome diz mais que dois números, e o código
        // continua acessível no title da tag
        { campo: "nu_mcc", campoMnc: "nu_mnc", rotulo: "Operadora", tipo: "operadora" },
        { campo: "nu_lac", rotulo: "LAC", tipo: "mono" },
        { campo: "nu_cid", rotulo: "CID", tipo: "mono" },
        { campo: "vl_latitude", campoLon: "vl_longitude", rotulo: "Coordenada", tipo: "coord" },
        { campo: "tp_rede", rotulo: "Rede", tipo: "tag" },
        { campo: "qt_ocorrencias", rotulo: "Vezes", tipo: "ocorrencias" }
      ]
    },
    voicemail: {
      entidade: { tp: "voicemail", id: "id_voicemail", rotulo: function (i) { return i.no_contato || i.nu_telefone || "mensagem de voz"; } },
      rotulo: "Caixa postal", unidade: "mensagens de voz",
      vazio: "Nenhuma mensagem de caixa postal.",
      colunas: [
        { campo: "dt_recebimento", rotulo: "Quando", tipo: "data" },
        { campo: "nu_telefone", rotulo: "De", tipo: "mono" },
        { campo: "no_contato", rotulo: "Contato" },
        { campo: "qt_duracao_segundos", rotulo: "Duração", tipo: "duracao" },
        { campo: "in_ouvida", rotulo: "Ouvida", tipo: "bool" },
        { campo: "tx_transcricao", rotulo: "Transcrição", tipo: "longo" },
        { campo: "no_aplicativo", rotulo: "Fonte" }
      ]
    },
    transferencias: {
      entidade: { tp: "transferencia", id: "id_transferencia", rotulo: function (i) { return i.tx_participantes || "transferência"; } },
      rotulo: "Transferências", unidade: "transferências",
      vazio: "Nenhuma transferência de valores registrada.",
      /* Filtros além da busca livre. As colunas têm que bater com o que o domínio declara em
       * ufdr_dominios_utils.py (data=, facetas=, valor=) — é lá que fica a lista branca. */
      filtros: {
        periodo: { rotulo: "Enviada em" },
        faixa: { rotulo: "Valor" },
        selects: [
          { campo: "tp_transferencia", rotulo: "Tipo", todos: "Todos os tipos" },
          { campo: "cd_situacao", rotulo: "Situação", todos: "Todas as situações" },
          { campo: "no_moeda", rotulo: "Moeda", todos: "Todas as moedas" },
          { campo: "no_aplicativo", rotulo: "Aplicativo", todos: "Todos os aplicativos" }
        ]
      },
      colunas: [
        { campo: "dt_envio", rotulo: "Enviada em", tipo: "data" },
        { campo: "vl_valor", rotulo: "Valor", tipo: "numero" },
        { campo: "no_moeda", rotulo: "Moeda", tipo: "tag" },
        { campo: "tx_participantes", rotulo: "Participantes", tipo: "longo" },
        { campo: "tx_descricao", rotulo: "Descrição", tipo: "longo" },
        { campo: "tp_transferencia", rotulo: "Tipo" },
        { campo: "cd_situacao", rotulo: "Situação", tipo: "tag" },
        { campo: "dt_processamento", rotulo: "Processada em", tipo: "data" },
        { campo: "no_aplicativo", rotulo: "Aplicativo" }
      ]
    },
    "cartoes-credito": {
      entidade: { tp: "cartao_credito", id: "id_cartao", rotulo: function (i) { return i.no_bandeira || "cartão"; } },
      rotulo: "Cartões de crédito", unidade: "cartões",
      vazio: "Nenhum cartão de crédito extraído.",
      filtros: {
        periodo: { rotulo: "Último uso" },
        selects: [
          { campo: "no_bandeira", rotulo: "Bandeira", todos: "Todas as bandeiras" },
          { campo: "no_aplicativo", rotulo: "Fonte", todos: "Todas as fontes" }
        ]
      },
      colunas: [
        { campo: "no_bandeira", rotulo: "Bandeira", tipo: "tag" },
        { campo: "nu_cartao", rotulo: "Número", tipo: "mono" },
        { campo: "no_titular", rotulo: "Titular" },
        { campo: "dt_validade", rotulo: "Validade" },
        { campo: "dt_ultimo_uso", rotulo: "Último uso", tipo: "data" },
        { campo: "no_aplicativo", rotulo: "Fonte" }
      ]
    },
    "cartoes-carteira": {
      entidade: { tp: "cartao_carteira", id: "id_cartao_carteira", rotulo: function (i) { return i.tx_descricao || "cartão da carteira"; } },
      rotulo: "Carteira", unidade: "cartões e passes",
      vazio: "Nenhum cartão ou passe na carteira do aparelho.",
      filtros: {
        periodo: { rotulo: "Comprado em" },
        selects: [
          { campo: "tp_cartao", rotulo: "Tipo", todos: "Todos os tipos" },
          { campo: "no_organizacao", rotulo: "Emissor", todos: "Todos os emissores" },
          { campo: "no_aplicativo", rotulo: "Fonte", todos: "Todas as fontes" }
        ]
      },
      colunas: [
        { campo: "tx_descricao", rotulo: "Descrição", tipo: "longo" },
        { campo: "no_organizacao", rotulo: "Emissor" },
        { campo: "tp_cartao", rotulo: "Tipo", tipo: "tag" },
        { campo: "dt_compra", rotulo: "Comprado em", tipo: "data" },
        { campo: "dt_expiracao", rotulo: "Expira em", tipo: "data" },
        { campo: "co_barras", rotulo: "Código de barras", tipo: "mono" },
        { campo: "vl_latitude", campoLon: "vl_longitude", rotulo: "Local", tipo: "coord" }
      ]
    }
  };

  /* Paginação no cliente, para as listas que a própria página já carrega inteiras (redes
   * Wi-Fi, contas financeiras). Elas vinham despejando todas as linhas no DOM de uma vez —
   * um aparelho com 12 mil redes travava a tela. Aqui o filtro continua sendo feito sobre a
   * lista completa em memória; só o desenho é recortado. */
  function criarPaginador(opcoes) {
    var porPagina = opcoes.porPagina || 100;
    var $corpo = $(opcoes.corpo);
    var $total = $(opcoes.total || "#total-registros");
    var itens = [];
    var pagina = 1;

    var $barra = $("<div>").addClass("arq-paginacao").insertAfter(opcoes.depoisDe || $corpo.closest("table"));
    var $anterior = $("<button>").addClass("btn paginacao-btn").text("Anterior").appendTo($barra);
    var $info = $("<span>").addClass("arq-pag-info").appendTo($barra);
    var $proxima = $("<button>").addClass("btn paginacao-btn").text("Próxima").appendTo($barra);

    function desenhar() {
      var ultima = Math.max(1, Math.ceil(itens.length / porPagina));
      pagina = Math.min(Math.max(1, pagina), ultima);
      $corpo.empty();
      itens.slice((pagina - 1) * porPagina, pagina * porPagina)
        .forEach(function (item, i) { $corpo.append(opcoes.render(item, (pagina - 1) * porPagina + i)); });
      $info.text(itens.length.toLocaleString("pt-BR") + " " + (opcoes.unidade || "registro(s)") +
        " · página " + pagina + " de " + ultima);
      $anterior.prop("disabled", pagina <= 1);
      $proxima.prop("disabled", pagina >= ultima);
      $barra.toggle(itens.length > porPagina);
      if ($total.length) {
        $total.text(itens.length.toLocaleString("pt-BR") + " " + (opcoes.unidade || "registro(s)"));
      }
      if (opcoes.aoDesenhar) opcoes.aoDesenhar(itens.length);
    }

    $anterior.on("click", function () { pagina -= 1; desenhar(); });
    $proxima.on("click", function () { pagina += 1; desenhar(); });

    return {
      definir: function (lista) { itens = lista || []; pagina = 1; desenhar(); },
      total: function () { return itens.length; }
    };
  }

  function abasDe(lista) {
    return lista.map(function (chave) {
      var cfg = COLUNAS[chave];
      if (!cfg) return null;
      return {
        chave: chave, rotulo: cfg.rotulo, unidade: cfg.unidade,
        vazio: cfg.vazio, colunas: cfg.colunas, entidade: cfg.entidade,
        filtros: cfg.filtros, vistaMes: cfg.vistaMes, ficha: cfg.ficha
      };
    }).filter(Boolean);
  }

  /* Auto-montagem: qualquer elemento com data-dominios="chave1,chave2" vira a lista.
   * É o que permite às telas novas não terem JS próprio e às telas antigas (Wi-Fi,
   * Navegação, Arquivos, Aplicativos, Localizações, Ligações, Contas) ganharem as abas
   * novas com uma linha de HTML. Com data-estado-url, filtros/página/vista vivem na URL. */
  $(function () {
    $("[data-dominios]").each(function () {
      var lista = String($(this).data("dominios")).split(",").map(function (s) { return s.trim(); });
      montar({ seletor: this, abas: abasDe(lista),
               estadoUrl: this.hasAttribute("data-estado-url") });
    });
  });

  window.UfdrDominio = { montar: montar, abasDe: abasDe, COLUNAS: COLUNAS,
                         criarPaginador: criarPaginador, celula: celula,
                         marcaEstado: marcaEstado, formatarDataHora: formatarDataHora,
                         formatarDuracao: formatarDuracao, formatarNumero: formatarNumero };
})();
