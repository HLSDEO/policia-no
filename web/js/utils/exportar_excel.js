/* Exportação de listas para Excel (.xlsx).
 *
 * Gera um arquivo .xlsx nativo (pacote OOXML) inteiramente no navegador, sem
 * dependências externas: monta o ZIP no método "store" (sem compressão) com CRC32
 * próprio. Todas as células saem como texto (inlineStr) — o mais seguro para dados
 * forenses (CPF, telefones, chaves PIX não devem virar número).
 *
 * Uso simples (páginas que carregam a lista inteira no cliente): basta marcar a
 * tabela com  data-exportar="nome-do-arquivo"  e incluir este script; o botão é
 * injetado sozinho na .topbar-actions e, ao clicar, exporta as linhas visíveis
 * (respeitando os filtros aplicados no DOM).
 *
 * Uso com provedor (páginas paginadas no servidor): chamar
 *   ExportarExcel.instalar({ nome, dados: function(){ return Promise|{colunas,linhas} } })
 * onde `dados` devolve o conjunto completo já filtrado — nunca só a página desenhada. Devolver
 * null cancela a exportação sem aviso (o provedor já falou com o usuário).
 */
(function () {
  "use strict";

  // ---- CRC32 -------------------------------------------------------------
  var TABELA_CRC = (function () {
    var t = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) c = TABELA_CRC[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  var utf8 = new TextEncoder();

  // ---- ZIP (método "store", sem compressão) ------------------------------
  function montarZip(arquivos) {
    // arquivos: [{ nome: string, dados: Uint8Array }]
    var registros = [];
    var offset = 0;
    var pedacosLocais = [];

    arquivos.forEach(function (a) {
      var nomeBytes = utf8.encode(a.nome);
      var crc = crc32(a.dados);
      var tam = a.dados.length;

      var cabecalho = new DataView(new ArrayBuffer(30));
      cabecalho.setUint32(0, 0x04034b50, true); // assinatura local file header
      cabecalho.setUint16(4, 20, true);         // versão necessária
      cabecalho.setUint16(6, 0x0800, true);     // flag: nome em UTF-8
      cabecalho.setUint16(8, 0, true);          // método: store
      cabecalho.setUint16(10, 0, true);         // hora
      cabecalho.setUint16(12, 0, true);         // data
      cabecalho.setUint32(14, crc, true);
      cabecalho.setUint32(18, tam, true);       // tamanho comprimido
      cabecalho.setUint32(22, tam, true);       // tamanho original
      cabecalho.setUint16(26, nomeBytes.length, true);
      cabecalho.setUint16(28, 0, true);         // extra len

      pedacosLocais.push(new Uint8Array(cabecalho.buffer), nomeBytes, a.dados);
      registros.push({ nomeBytes: nomeBytes, crc: crc, tam: tam, offset: offset });
      offset += 30 + nomeBytes.length + tam;
    });

    var inicioCentral = offset;
    var pedacosCentral = [];
    registros.forEach(function (r) {
      var c = new DataView(new ArrayBuffer(46));
      c.setUint32(0, 0x02014b50, true);         // assinatura central directory
      c.setUint16(4, 20, true);                 // versão criadora
      c.setUint16(6, 20, true);                 // versão necessária
      c.setUint16(8, 0x0800, true);             // flag UTF-8
      c.setUint16(10, 0, true);                 // método store
      c.setUint16(12, 0, true);
      c.setUint16(14, 0, true);
      c.setUint32(16, r.crc, true);
      c.setUint32(20, r.tam, true);
      c.setUint32(24, r.tam, true);
      c.setUint16(28, r.nomeBytes.length, true);
      c.setUint16(30, 0, true);                 // extra
      c.setUint16(32, 0, true);                 // comentário
      c.setUint16(34, 0, true);                 // disco
      c.setUint16(36, 0, true);                 // atributos internos
      c.setUint32(38, 0, true);                 // atributos externos
      c.setUint32(42, r.offset, true);          // offset do local header
      pedacosCentral.push(new Uint8Array(c.buffer), r.nomeBytes);
      offset += 46 + r.nomeBytes.length;
    });
    var tamCentral = offset - inicioCentral;

    var fim = new DataView(new ArrayBuffer(22));
    fim.setUint32(0, 0x06054b50, true);         // assinatura EOCD
    fim.setUint16(4, 0, true);
    fim.setUint16(6, 0, true);
    fim.setUint16(8, registros.length, true);
    fim.setUint16(10, registros.length, true);
    fim.setUint32(12, tamCentral, true);
    fim.setUint32(16, inicioCentral, true);
    fim.setUint16(20, 0, true);

    var partes = pedacosLocais.concat(pedacosCentral, [new Uint8Array(fim.buffer)]);
    var total = partes.reduce(function (s, p) { return s + p.length; }, 0);
    var saida = new Uint8Array(total);
    var pos = 0;
    partes.forEach(function (p) { saida.set(p, pos); pos += p.length; });
    return saida;
  }

  // ---- XLSX --------------------------------------------------------------
  function escaparXml(txt) {
    return String(txt == null ? "" : txt)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&apos;")
      // remove caracteres de controle inválidos em XML
      .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");
  }

  function colLetra(n) { // 1 -> A, 27 -> AA
    var s = "";
    while (n > 0) { var r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = (n - r - 1) / 26; }
    return s;
  }

  // Teto do Excel para uma célula. Estourar não dá erro de gravação aqui: dá arquivo que o
  // Excel diz estar corrompido na abertura. Campo de texto livre passa disso sem esforço (a
  // análise de uma comunicação de RIF, a transcrição de um áudio), então o corte mora no
  // escritor — nenhuma tela precisa lembrar dele.
  var MAX_CELULA = 32767;

  function planilhaXml(cabecalhos, linhas) {
    var out = [];
    out.push('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>');
    out.push('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>');

    var todas = [cabecalhos].concat(linhas);
    for (var i = 0; i < todas.length; i++) {
      var linha = todas[i];
      out.push('<row r="' + (i + 1) + '">');
      for (var j = 0; j < linha.length; j++) {
        var ref = colLetra(j + 1) + (i + 1);
        var val = linha[j] == null ? "" : String(linha[j]);
        if (val.length > MAX_CELULA) val = val.slice(0, MAX_CELULA - 1) + "…";
        out.push('<c r="' + ref + '" t="inlineStr"><is><t xml:space="preserve">' +
          escaparXml(val) + '</t></is></c>');
      }
      out.push('</row>');
    }
    out.push('</sheetData></worksheet>');
    return out.join("");
  }

  function gerarXlsx(cabecalhos, linhas) {
    var arquivos = [
      { nome: "[Content_Types].xml", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        '</Types>') },
      { nome: "_rels/.rels", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
        '</Relationships>') },
      { nome: "xl/workbook.xml", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
        '<sheets><sheet name="Dados" sheetId="1" r:id="rId1"/></sheets></workbook>') },
      { nome: "xl/_rels/workbook.xml.rels", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
        '</Relationships>') },
      { nome: "xl/worksheets/sheet1.xml", dados: utf8.encode(planilhaXml(cabecalhos, linhas)) },
    ];
    return montarZip(arquivos);
  }

  /* Nome de aba que o Excel aceita: no máximo 31 caracteres, sem  : \ / ? * [ ]  , sem
     apóstrofo nas pontas, não vazio e único na pasta de trabalho (a comparação do Excel ignora
     maiúscula/minúscula). Qualquer uma dessas regras violada faz o Excel RECUSAR o arquivo
     inteiro na abertura — não é a aba que abre com o nome errado, é a planilha que não abre.
     Antes o nome ia cru para o XML e só não quebrava porque as duas abas em uso ("Entidades" e
     "Vinculos") cabiam por acaso; qualquer aba nomeada a partir de dado da tela (nome do
     inquérito, da conta, do filtro) passava do limite sem aviso nenhum. */
  function nomeAba(nome, usados) {
    var base = String(nome == null ? "" : nome)
      .replace(/[:\\/?*\[\]]/g, " ")
      .replace(/\s+/g, " ").trim()
      .slice(0, 31)
      .replace(/^'+|'+$/g, "").trim();
    if (!base) base = "Planilha";

    // Colisão depois do corte ("Lançamentos de janeiro de 2026" e "…de fevereiro de 2026" viram
    // o mesmo nome): o sufixo entra dentro dos 31, não em cima deles.
    var escolhido = base, n = 2;
    while (usados[escolhido.toLowerCase()]) {
      var sufixo = " (" + n++ + ")";
      escolhido = base.slice(0, 31 - sufixo.length).trim() + sufixo;
    }
    usados[escolhido.toLowerCase()] = true;
    return escolhido;
  }

  function gerarXlsxMultiSheet(abas) {
    var sIds = abas.map(function (_, i) { return "sheet" + (i + 1); });
    var usados = {};
    var nomes = abas.map(function (a) { return nomeAba(a.nome, usados); });
    var contentTypes =
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      sIds.map(function (s) {
        return '<Override PartName="/xl/worksheets/' + s + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>';
      }).join("") +
      '</Types>';

    var sheets = abas.map(function (_, i) {
      return '<sheet name="' + escaparXml(nomes[i]) + '" sheetId="' + (i + 1) + '" r:id="rId' + (i + 1) + '"/>';
    }).join("");

    var rels = abas.map(function (_, i) {
      return '<Relationship Id="rId' + (i + 1) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/' + sIds[i] + '.xml"/>';
    }).join("");

    var arquivos = [
      { nome: "[Content_Types].xml", dados: utf8.encode(contentTypes) },
      { nome: "_rels/.rels", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
        '</Relationships>') },
      { nome: "xl/workbook.xml", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
        '<sheets>' + sheets + '</sheets></workbook>') },
      { nome: "xl/_rels/workbook.xml.rels", dados: utf8.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + '</Relationships>') },
    ];
    abas.forEach(function (a, i) {
      arquivos.push({ nome: "xl/worksheets/" + sIds[i] + ".xml", dados: utf8.encode(planilhaXml(a.cabecalhos, a.linhas)) });
    });
    return montarZip(arquivos);
  }

  function baixarBlob(bytes, nomeArquivo) {
    var blob = new Blob([bytes], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = nomeArquivo;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function nomeComData(base) {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    var carimbo = d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes());
    return (base || "lista") + "-" + carimbo + ".xlsx";
  }

  function avisar(msg) {
    if (window.Dialogo && Dialogo.alerta) Dialogo.alerta(msg);
    else alert(msg);
  }

  // ---- Leitura genérica de uma tabela do DOM -----------------------------
  // Devolve { colunas: [rótulos], linhas: [[valores]] } considerando só as linhas
  // presentes no DOM (portanto respeita os filtros feitos no cliente). Colunas
  // cujo conteúdo é apenas ação/controle (botões, links de ação) são descartadas.
  var SEL_IGNORAR = 'button, input, select, textarea, svg, .btn, .arq-acao, .arq-meta-baixar, .ent-botao, a[target="_blank"]';

  function textoLimpo(celula) {
    var clone = celula.cloneNode(true);
    clone.querySelectorAll(SEL_IGNORAR).forEach(function (el) { el.remove(); });
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  }

  function lerTabela(tabela) {
    var thead = tabela.tHead;
    var cabRow = thead ? thead.rows[thead.rows.length - 1] : null;
    var rotulos = cabRow ? Array.prototype.map.call(cabRow.cells, function (c) {
      return (c.textContent || "").replace(/\s+/g, " ").trim();
    }) : [];

    var corpo = tabela.tBodies[0];
    var trs = corpo ? Array.prototype.filter.call(corpo.rows, function (tr) {
      return tr.offsetParent !== null || tr.getClientRects().length > 0; // ignora linhas ocultas
    }) : [];

    var nCols = rotulos.length;
    trs.forEach(function (tr) { nCols = Math.max(nCols, tr.cells.length); });

    // valores limpos por célula
    var matriz = trs.map(function (tr) {
      var vals = [];
      for (var i = 0; i < nCols; i++) vals.push(tr.cells[i] ? textoLimpo(tr.cells[i]) : "");
      return vals;
    });

    // mantém apenas colunas que têm ao menos um valor (descarta colunas de ação)
    var manter = [];
    for (var c = 0; c < nCols; c++) {
      var temDado = matriz.some(function (linha) { return linha[c] !== ""; });
      if (temDado) manter.push(c);
    }
    // se a tabela ainda não tem linhas, mantém as colunas com rótulo
    if (!matriz.length) {
      manter = [];
      for (var k = 0; k < nCols; k++) if ((rotulos[k] || "").trim() !== "") manter.push(k);
    }

    return {
      colunas: manter.map(function (c) { return rotulos[c] || ""; }),
      linhas: matriz.map(function (linha) { return manter.map(function (c) { return linha[c]; }); }),
    };
  }

  // ---- Botão + orquestração ----------------------------------------------
  function criarBotao() {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-exportar-excel";
    btn.textContent = "⬇ Excel";
    btn.title = "Baixar a lista em formato Excel (.xlsx)";
    return btn;
  }

  function exportar(nome, colunas, linhas) {
    if (!colunas || !colunas.length || !linhas || !linhas.length) {
      avisar("Não há dados para exportar.");
      return false;
    }
    baixarBlob(gerarXlsx(colunas, linhas), nomeComData(nome));
    return true;
  }

  function instalar(opts) {
    opts = opts || {};
    var alvo = document.querySelector(opts.alvo || ".topbar-actions");
    if (!alvo) return;
    if (alvo.querySelector(".btn-exportar-excel")) return; // evita duplicar

    var btn = criarBotao();
    alvo.appendChild(btn);

    btn.addEventListener("click", function () {
      var original = btn.textContent;
      var finalizar = function () { btn.disabled = false; btn.textContent = original; };

      var fonte;
      if (typeof opts.dados === "function") {
        btn.disabled = true;
        btn.textContent = "Gerando…";
        fonte = Promise.resolve().then(opts.dados);
      } else {
        var tabela = typeof opts.tabela === "string" ? document.querySelector(opts.tabela) : opts.tabela;
        if (!tabela) { avisar("Tabela não encontrada para exportar."); return; }
        fonte = Promise.resolve(lerTabela(tabela));
      }

      fonte.then(function (d) {
        // `dados` devolvendo null é desistência do próprio provedor (ele já falou com o usuário —
        // ex.: o aviso de conjunto grande demais no SIMBA). Sair calado aqui evita emendar um
        // "Não há dados para exportar" logo depois de quem cancelou.
        if (d) exportar(opts.nome, d.colunas, d.linhas);
        finalizar();
      }).catch(function () {
        finalizar();
        avisar("Falha ao gerar o arquivo Excel.");
      });
    });

    return btn;
  }

  // ---- Auto-init: tabelas marcadas com data-exportar ---------------------
  function autoInit() {
    document.querySelectorAll("table[data-exportar]").forEach(function (tabela) {
      instalar({
        nome: tabela.getAttribute("data-exportar") || "lista",
        tabela: tabela,
        alvo: tabela.getAttribute("data-exportar-alvo") || ".topbar-actions",
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }

  // ---- CSV -----------------------------------------------------------------
  // RFC 4180 com BOM UTF-8 (o Excel brasileiro abre sem perguntar nada) e vírgula como
  // separador — o formato que qualquer ferramenta de análise aceita sem biblioteca.
  function celularCsv(v) {
    var s = String(v == null ? "" : v);
    return /[",\n\r;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function gerarCsv(cabecalhos, linhas) {
    var partes = ["\uFEFF"];
    [cabecalhos].concat(linhas || []).forEach(function (linha) {
      partes.push(linha.map(celularCsv).join(",") + "\r\n");
    });
    return new TextEncoder().encode(partes.join(""));
  }

  window.ExportarExcel = {
    instalar: instalar,
    exportar: exportar,
    gerarXlsx: gerarXlsx,
    gerarXlsxMultiSheet: gerarXlsxMultiSheet,
    gerarCsv: gerarCsv,
    lerTabela: lerTabela,
    // blocos de montagem para exportações próprias (ver js/utils/exportar_custodia.js):
    // o ZIP "store" e o download por blob que já vivem aqui não precisam ser reimplementados.
    montarZip: montarZip,
    baixarBlob: baixarBlob,
  };
})();
