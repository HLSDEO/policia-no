/* Exportação de grafo para o i2 Analyst's Notebook — window.ExportarI2. Usado por TODAS as telas
 * que desenham grafo: RIF, extrato do SIMBA (as duas via js/utils/grafo_fluxo.js), mesa de
 * inquérito, grafo do caso, convergência entre inquéritos e o grafo de localizações.
 *
 * Por que existe: o i2 é a ferramenta em que a análise de vínculo continua depois daqui — quem
 * monta a carta precisa dos nós e das ligações como DADO, não como figura (para isso existe o
 * ExportarPNG). O código nasceu dentro do grafo de fluxo financeiro e só o RIF e o SIMBA
 * exportavam; as outras quatro telas desenhavam grafo e não tinham saída nenhuma. Aqui a
 * escrita dos quatro formatos mora num lugar só e cada tela entrega apenas o recorte dela.
 *
 * Os quatro formatos, e por que são quatro:
 *   CSV     — uma linha por nó e por ligação, com a coluna `registro` dizendo qual é qual. É o
 *             formato de importação genérico do i2 e o que abre em qualquer lugar para conferência.
 *   XLSX    — duas abas (Entidades / Vínculos), que é como o assistente de importação do i2 espera
 *             quando se mapeia entidade e vínculo separadamente.
 *   ANX     — a carta já montada, com tipos de entidade e setas. Abre direto no Analyst's Notebook.
 *   GraphML — o mesmo grafo para fora do i2: Gephi, yEd, Cytoscape, NetworkX. É onde se MEDE a
 *             rede (centralidade, comunidade, caminho) em vez de montar a carta do processo.
 *
 * Contrato — a tela entrega um "recorte" já pronto:
 *   {
 *     prefixo: "mesa",                                       // começo do nome do arquivo
 *     entidades: [ { chave, rotulo, tipo, peso?, atributos? } ],
 *     vinculos:  [ { de, para, rotulo?, tipo?, peso?, simetrico?, resumo?, atributos? } ]
 *   }
 *
 * `atributos` é um objeto `{ "Rótulo na carta": valor }` com o que a tela sabe e o desenho não
 * cabe mostrar: CPF/CNPJ, valor movimentado, período, nº de lançamentos, banco, nº de evidências.
 * É a diferença entre a carta dizer "pagou a" e dizer "pagou R$ 45.320,00 em 12 lançamentos,
 * entre 03/02/2026 e 28/07/2026" — sem isso o i2 recebe a topologia e perde o caso. Valor vazio
 * (null, "" ou undefined) é omitido, para a coluna não existir só de enfeite.
 *
 * `chave` é o identificador do nó DENTRO da tela (id de string, índice do array, id_no do
 * servidor — o que for), e é por ela que `de`/`para` acham as pontas. O id que vai para o i2 é
 * derivado do RÓTULO, não da chave: na carta o id aparece, e "e1427" não diz nada a quem lê,
 * enquanto "joao_da_silva" diz. Homônimos são desambiguados com sufixo numérico.
 *
 * Vínculo com ponta fora do recorte é descartado em silêncio: a tela filtra nós, e uma ligação
 * para um nó que não foi exportado viraria uma entidade fantasma na carta.
 *
 * Regra de recorte (a mesma nas seis telas): exporta-se o grafo FILTRADO INTEIRO, nunca o
 * recorte desenhado. O teto de nós das telas existe porque o layout por força trava o navegador;
 * a exportação não desenha nada, então não tem por que cortar — e um analista que filtrou para
 * 4 mil nós quer os 4 mil no i2, não os 100 que couberam no desenho.
 */
(function () {
  "use strict";

  // Marcas de combinação: os acentos que o NFD separou da letra, tirados para o id que vai ao i2
  // sair em ASCII. Montada com fromCharCode em vez de escrita como classe literal porque o
  // intervalo é feito dos próprios diacríticos — no fonte eles são invisíveis, e o primeiro
  // editor que reinterpretar a codificação do arquivo os corrompe sem ninguém notar.
  var RE_ACENTOS = new RegExp("[" + String.fromCharCode(0x0300) + "-" + String.fromCharCode(0x036F) + "]", "g");

  function _slug(text) {
    return String(text || "")
      .normalize("NFD").replace(RE_ACENTOS, "")
      .replace(/[^a-zA-Z0-9_-]/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "")
      .toLowerCase() || "no";
  }

  function _escaparCsv(v) {
    var s = String(v != null ? v : "");
    if (s.indexOf(";") !== -1 || s.indexOf("\n") !== -1 || s.indexOf("\r") !== -1 || s.indexOf('"') !== -1) {
      s = '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  function _escaparXml(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&apos;")
      .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");
  }

  function _nomeArquivo(prefixo, ext, meio) {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    var ts = d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes());
    return (prefixo || "grafo") + "-" + (meio || "grafo-i2") + "-" + ts + "." + ext;
  }

  function _baixar(conteudo, tipo, nome) {
    var blob = new Blob([conteudo], { type: tipo });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function _aviso(txt) { if (window.Dialogo) window.Dialogo.alerta(txt); else alert(txt); }

  /* Tira os campos sem conteúdo e passa tudo a texto. Zero e false FICAM: "0 lançamentos" é uma
     informação, e sumir com ela deixaria a célula vazia parecendo "não apurado". */
  function _limparAtributos(attrs) {
    var limpo = {};
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === "") return;
      limpo[k] = String(v);
    });
    return limpo;
  }

  /* Colunas de atributo na ordem em que apareceram — não em ordem alfabética. Quem monta o
     recorte escreve os campos na ordem em que se lê o nó (documento, depois valor, depois
     período), e alfabetar isso embaralharia a leitura na planilha. */
  function _colunasDe(itens) {
    var vistas = {}, colunas = [];
    itens.forEach(function (i) {
      Object.keys(i.attrs).forEach(function (k) {
        if (!vistas[k]) { vistas[k] = true; colunas.push(k); }
      });
    });
    return colunas;
  }

  function _valores(item, colunas) {
    return colunas.map(function (c) { return item.attrs[c] || ""; });
  }

  /* Recorte da tela -> { entities, edges, prefixo, colunas* } para os três escritores. */
  function _normalizar(recorte) {
    if (!recorte || !recorte.entidades || !recorte.entidades.length) return null;

    var idsUsados = {}, porChave = {}, entities = [];
    recorte.entidades.forEach(function (e, idx) {
      var rotulo = String(e.rotulo == null ? "" : e.rotulo).trim();
      var baseId = _slug(rotulo) || "entidade";
      var id = baseId, cont = 1;
      while (idsUsados[id]) { id = baseId + "_" + cont++; }
      idsUsados[id] = true;

      var registro = {
        id: id,
        label: rotulo || id,
        kind: String(e.tipo || "Entidade"),
        weight: Math.max(1, Math.round(Number(e.peso) || 0)),
        attrs: _limparAtributos(e.atributos)
      };
      porChave[e.chave != null ? String(e.chave) : String(idx)] = registro;
      entities.push(registro);
    });

    var edges = [];
    (recorte.vinculos || []).forEach(function (v) {
      var source = porChave[String(v.de)], target = porChave[String(v.para)];
      if (!source || !target) return;   // ponta fora do recorte exportado
      edges.push({
        source: source.id,
        target: target.id,
        kind: String(v.tipo || "direto"),
        label: String(v.rotulo || "vinculo"),
        weight: Math.max(1, Math.round(Number(v.peso) || 0)),
        symmetric: !!v.simetrico,
        summary: String(v.resumo == null ? "" : v.resumo),
        attrs: _limparAtributos(v.atributos)
      });
    });

    return {
      entities: entities,
      edges: edges,
      prefixo: recorte.prefixo || "grafo",
      colunasNo: _colunasDe(entities),
      colunasVinculo: _colunasDe(edges)
    };
  }

  function exportarCSV(recorte) {
    var dados = _normalizar(recorte);
    if (!dados) { _aviso("Grafo vazio. Nada para exportar."); return; }

    // Nós e vínculos dividem a mesma tabela (a coluna `registro` diz qual é qual), então as
    // colunas de atributo são a união das duas — cada linha preenche as suas e deixa o resto em
    // branco. Nome repetido nos dois lados (ex.: "Período") reaproveita a mesma coluna.
    var extras = dados.colunasNo.slice();
    dados.colunasVinculo.forEach(function (c) { if (extras.indexOf(c) === -1) extras.push(c); });

    var headers = ["registro","id","label","kind","source","target","weight","symmetric","common_followers_count","common_following_count","summary","image_url"].concat(extras);
    var linhas = [];

    dados.entities.forEach(function (e) {
      linhas.push(["node", e.id, e.label, e.kind, "", "", e.weight, "", "", "", "", ""].concat(_valores(e, extras)));
    });
    dados.edges.forEach(function (e, idx) {
      var eid = "edge-" + String(idx + 1).padStart(4, "0");
      linhas.push(["edge", eid, e.label, e.kind, e.source, e.target, e.weight, e.symmetric ? "sim" : "nao", "", "", e.summary, ""].concat(_valores(e, extras)));
    });

    // BOM: sem ele o Excel abre o CSV em ANSI e todo acento vira caractere solto.
    var bom = String.fromCharCode(0xFEFF);
    var csv = bom + headers.join(";") + "\n" + linhas.map(function (l) {
      return l.map(_escaparCsv).join(";");
    }).join("\n");

    _baixar(csv, "text/csv;charset=utf-8", _nomeArquivo(dados.prefixo, "csv"));
  }

  function exportarXLSX(recorte) {
    var dados = _normalizar(recorte);
    if (!dados) { _aviso("Grafo vazio. Nada para exportar."); return; }
    if (!window.ExportarExcel) { _aviso("ExportarExcel nao disponivel."); return; }

    // Aqui cada aba é de um tipo só, então cada uma leva exatamente as SUAS colunas de atributo —
    // é a forma que o assistente de importação do i2 espera para mapear atributo a atributo.
    var entCabecalhos = ["Entity ID","Label","Type","Username","Image URL"].concat(dados.colunasNo);
    var entLinhas = dados.entities.map(function (e) {
      return [e.id, e.label, e.kind, e.label.replace(/^@/, ""), ""].concat(_valores(e, dados.colunasNo));
    });

    var vinCabecalhos = ["Link ID","From Entity ID","To Entity ID","Type","Label","Weight","Symmetric","Common Followers","Common Following","Summary"].concat(dados.colunasVinculo);
    var vinLinhas = dados.edges.map(function (e, idx) {
      var eid = "link-" + String(idx + 1).padStart(4, "0");
      return [eid, e.source, e.target, e.kind, e.label, e.weight, e.symmetric ? "Sim" : "Nao", 0, 0, e.summary]
        .concat(_valores(e, dados.colunasVinculo));
    });

    var zip = window.ExportarExcel.gerarXlsxMultiSheet([
      { nome: "Entidades", cabecalhos: entCabecalhos, linhas: entLinhas },
      { nome: "Vinculos", cabecalhos: vinCabecalhos, linhas: vinLinhas }
    ]);

    _baixar(zip, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _nomeArquivo(dados.prefixo, "xlsx"));
  }

  /* Descrição do item na carta: a primeira linha é o resumo de sempre, e abaixo vem um
     "campo: valor" por atributo.

     Por que na descrição e não como Attribute do i2: o ANX tem atributo de verdade
     (AttributeClassCollection), mas o nome exato dos elementos varia entre versões do Analyst's
     Notebook, e errar ali faz o arquivo ser RECUSADO na abertura — perder o formato que abre
     direto seria pior que a informação ficar num campo só. A descrição é visível no cartão do
     item em qualquer versão. Para atributo mapeável campo a campo existem o CSV e o XLSX, que é
     onde o assistente de importação do i2 faz esse mapeamento.

     A quebra de linha vai como &#10; porque o XML normaliza quebra literal dentro de valor de
     atributo para espaço — a descrição chegaria numa linha só. */
  function _descricao(primeira, item) {
    var linhas = primeira ? [primeira] : [];
    Object.keys(item.attrs).forEach(function (k) { linhas.push(k + ": " + item.attrs[k]); });
    return _escaparXml(linhas.join("\n")).replace(/\n/g, "&#10;");
  }

  function exportarANX(recorte) {
    var dados = _normalizar(recorte);
    if (!dados) { _aviso("Grafo vazio. Nada para exportar."); return; }

    // Cada tipo de entidade ganha uma cor (o i2 usa o índice da paleta dele) — sem isso a carta
    // abre com tudo da mesma cor e o tipo, que é a informação mais rápida de ler, se perde.
    var tipos = {};
    dados.entities.forEach(function (e) { tipos[e.kind] = true; });
    var tipoIdx = 0, tipoMap = {};
    Object.keys(tipos).sort().forEach(function (t) { tipoMap[t] = tipoIdx++; });

    // O mesmo para os tipos de ligação: numa mesa de inquérito "conversou com" e "PIX para" são
    // linhas diferentes, e sem LinkType saem todas iguais.
    var tiposLink = {};
    dados.edges.forEach(function (e) { tiposLink[e.kind] = true; });
    var linkIdx = 0, linkMap = {};
    Object.keys(tiposLink).sort().forEach(function (t) { linkMap[t] = linkIdx++; });
    if (!Object.keys(linkMap).length) linkMap["direto"] = 0;

    var x = '<?xml version="1.0" encoding="utf-8"?>\n';
    x += '<Chart GridVisibleOnAllViews="true">\n';
    x += '  <EntityTypeCollection>\n';
    Object.keys(tipoMap).sort().forEach(function (t) {
      x += '    <EntityType PreferredRepresentation="RepresentAsIcon" IconFile="Person" Name="' + _escaparXml(t) + '" Colour="' + tipoMap[t] + '" />\n';
    });
    x += '  </EntityTypeCollection>\n';
    x += '  <LinkTypeCollection>\n';
    Object.keys(linkMap).sort().forEach(function (t) {
      x += '    <LinkType Colour="' + linkMap[t] + '" Name="' + _escaparXml(t) + '" />\n';
    });
    x += '  </LinkTypeCollection>\n';
    x += '  <ChartItemCollection>\n';

    dados.entities.forEach(function (e) {
      x += '    <ChartItem Label="' + _escaparXml(e.label) + '" Description="' + _descricao("ID: " + e.id, e) + '">\n';
      x += '      <End>\n';
      x += '        <Entity EntityId="' + _escaparXml(e.id) + '" LabelIsIdentity="false" Identity="' + _escaparXml(e.id) + '">\n';
      x += '          <Icon TextY="16" TextX="0">\n';
      x += '            <IconStyle Type="' + _escaparXml(e.kind) + '" />\n';
      x += '          </Icon>\n';
      x += '        </Entity>\n';
      x += '      </End>\n';
      x += '    </ChartItem>\n';
    });

    dados.edges.forEach(function (e) {
      var arrow = e.symmetric ? "ArrowOnBoth" : "ArrowOnHead";
      x += '    <ChartItem Label="' + _escaparXml(e.label) + '" Description="' + _descricao(e.summary || "", e) + '">\n';
      x += '      <Link End2Id="' + _escaparXml(e.target) + '" End1Id="' + _escaparXml(e.source) + '">\n';
      x += '        <LinkStyle Type="' + _escaparXml(e.kind) + '" ArrowStyle="' + arrow + '" />\n';
      x += '      </Link>\n';
      x += '    </ChartItem>\n';
    });

    x += '  </ChartItemCollection>\n';
    x += '</Chart>\n';

    _baixar(x, "application/xml;charset=utf-8", _nomeArquivo(dados.prefixo, "anx"));
  }

  /* GraphML — o grafo para FORA do i2: Gephi, yEd, Cytoscape, NetworkX. É o mesmo recorte dos
     outros três, mas serve a outra pergunta: no i2 se monta a carta para o processo, e no Gephi
     se mede a rede (centralidade, comunidade, caminho mínimo) em grafo grande demais para
     desenhar à mão. Nada disso o ANX faz, e refazer o recorte à mão para levar até lá era o que
     acontecia antes.

     Cada atributo do recorte vira um <key> declarado uma vez e um <data> por item — é assim que o
     Gephi mostra o campo na tabela de nós/arestas e deixa filtrar por ele. `label` e `weight`
     usam esses nomes exatos porque são os que o Gephi reconhece sozinho como rótulo do nó e peso
     da aresta; qualquer outro nome entraria como coluna solta.

     O id do <key> é sequencial (n0, n1, e0…) e não o nome do campo: o nome vem de rótulo em
     português com acento e espaço ("Nº de lançamentos"), e id de GraphML é NMTOKEN — o arquivo
     seria recusado no parse. O nome legível vai em attr.name, que é o que aparece na ferramenta. */
  function exportarGraphML(recorte) {
    var dados = _normalizar(recorte);
    if (!dados) { _aviso("Grafo vazio. Nada para exportar."); return; }

    var chavesNo = [
      { id: "n0", nome: "label", tipo: "string", ler: function (e) { return e.label; } },
      { id: "n1", nome: "tipo", tipo: "string", ler: function (e) { return e.kind; } },
      { id: "n2", nome: "grau", tipo: "int", ler: function (e) { return e.weight; } }
    ];
    dados.colunasNo.forEach(function (col, i) {
      chavesNo.push({ id: "n" + (i + 3), nome: col, tipo: "string",
                      ler: function (e) { return e.attrs[col]; } });
    });

    var chavesAresta = [
      { id: "e0", nome: "label", tipo: "string", ler: function (a) { return a.label; } },
      { id: "e1", nome: "tipo", tipo: "string", ler: function (a) { return a.kind; } },
      { id: "e2", nome: "weight", tipo: "double", ler: function (a) { return a.weight; } },
      { id: "e3", nome: "resumo", tipo: "string", ler: function (a) { return a.summary; } }
    ];
    dados.colunasVinculo.forEach(function (col, i) {
      chavesAresta.push({ id: "e" + (i + 4), nome: col, tipo: "string",
                          ler: function (a) { return a.attrs[col]; } });
    });

    function _datas(item, chaves) {
      var out = "";
      chaves.forEach(function (k) {
        var v = k.ler(item);
        if (v === null || v === undefined || v === "") return;
        out += '      <data key="' + k.id + '">' + _escaparXml(v) + "</data>\n";
      });
      return out;
    }

    var x = '<?xml version="1.0" encoding="UTF-8"?>\n';
    x += '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"' +
         ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' +
         ' xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns' +
         ' http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">\n';

    chavesNo.concat(chavesAresta).forEach(function (k) {
      var para = k.id.charAt(0) === "n" ? "node" : "edge";
      x += '  <key id="' + k.id + '" for="' + para + '" attr.name="' + _escaparXml(k.nome) +
           '" attr.type="' + k.tipo + '"/>\n';
    });

    // Grafo dirigido por padrão, e a ligação sem sentido (evidência, "esteve junto de") sai
    // marcada uma a uma com directed="false" — o GraphML deixa misturar os dois no mesmo grafo,
    // e é o que evita inventar seta onde não há remetente e destinatário.
    x += '  <graph id="G" edgedefault="directed">\n';

    dados.entities.forEach(function (e) {
      x += '    <node id="' + _escaparXml(e.id) + '">\n' + _datas(e, chavesNo) + "    </node>\n";
    });

    dados.edges.forEach(function (a, idx) {
      x += '    <edge id="e' + String(idx + 1).padStart(4, "0") + '"' +
           ' source="' + _escaparXml(a.source) + '" target="' + _escaparXml(a.target) + '"' +
           (a.symmetric ? ' directed="false"' : "") + ">\n" +
           _datas(a, chavesAresta) + "    </edge>\n";
    });

    x += "  </graph>\n</graphml>\n";

    _baixar(x, "application/xml;charset=utf-8", _nomeArquivo(dados.prefixo, "graphml", "grafo"));
  }

  /* Liga os três botões da barra da tela ao recorte dela. `montarRecorte` é chamado no CLIQUE,
   * não aqui: o grafo muda com filtro, busca e legenda, e o que vale é o estado do momento da
   * exportação. Botão que não existe na página é ignorado, então uma tela pode oferecer só
   * parte dos formatos sem precisar de outra chamada. */
  function ligarBotoes(montarRecorte, ids) {
    ids = ids || {};
    var pares = [
      [ids.csv || "#exportar-i2-csv", exportarCSV],
      [ids.xlsx || "#exportar-i2-xlsx", exportarXLSX],
      [ids.anx || "#exportar-i2-anx", exportarANX],
      [ids.graphml || "#exportar-i2-graphml", exportarGraphML]
    ];
    pares.forEach(function (par) {
      var $b = $(par[0]);
      if (!$b.length) return;
      $b.on("click", function () { par[1](montarRecorte()); });
    });
  }

  window.ExportarI2 = {
    csv: exportarCSV,
    xlsx: exportarXLSX,
    anx: exportarANX,
    graphml: exportarGraphML,
    ligarBotoes: ligarBotoes
  };
})();
