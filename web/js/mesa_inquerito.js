/* Mesa de Inquérito: grafo interativo (zoom/pan/arrastar, filtros, filtro de período),
 * duas camadas de nós — entidades-comentário (registros marcados) e entidades canônicas
 * (pessoa/telefone/local/...), estas povoadas pela identificação de pessoas da ingestão —
 * revisão das sugestões da IA, relações manuais, detecção de padrões de crime e análise
 * de grafo (centralidade/comunidades). Tudo sem lib externa, como o resto do front. */
(function () {
  "use strict";

  function paramUrl(nome) { return AnalyserUtils.paramUrl(nome); }

  function formatarDataHora(iso) { return AnalyserUtils.formatarDataHora(iso); }

  function truncar(texto, max) { return AnalyserUtils.truncar(texto, max); }

  // Meia-noite UTC (instante devolvido por diaDoRegistro/datasEmTexto) -> "DD/MM/AAAA"
  function formatarDiaInstantaneo(ms) {
    var d = new Date(ms);
    return ("0" + d.getUTCDate()).slice(-2) + "/" + ("0" + (d.getUTCMonth() + 1)).slice(-2) + "/" + d.getUTCFullYear();
  }

  var idInquerito = paramUrl("inquerito");
  var svg = document.getElementById("mesa-grafo");
  var $vazio = $("#mesa-vazio");
  var $statusLabel = $("#busca-status-label");
  var $legenda = $("#mesa-legenda");
  var $drawerNo = $("#mesa-drawer-no");

  function temaOrbitAtivo() {
    return document.documentElement.getAttribute("data-theme") === "orbit";
  }

  function modoRealistaAtivo() {
    return temaOrbitAtivo() && document.body.classList.contains("mesa-realista");
  }

  function sincronizarApresentacaoOrbit() {
    var orbit = temaOrbitAtivo();
    $("#mesa-page-title").text(orbit ? "Quadro de Investigação" : "Mesa de Inquérito");
    document.title = (orbit ? "Quadro de Investigação" : "Mesa de Inquérito") + " · PolícIA";
    document.body.classList.toggle("mesa-realista", orbit && $("#mesa-realista-check").prop("checked"));
  }

  sincronizarApresentacaoOrbit();

  if (!idInquerito) {
    $vazio.text("Nenhum inquérito informado na URL.").removeClass("hidden");
  }

  var SVG_NS = "http://www.w3.org/2000/svg";
  var LARGURA = 900, ALTURA = 560, MARGEM = 36;
  var LAYOUT_ITERACOES = 300, LAYOUT_POR_FRAME = 30;

  // A tela do layout CRESCE com o número de nós, em vez de espremer todo mundo nos mesmos
  // 900x560: com 100 nós no retângulo fixo, o grafo virava um bolo no meio da tela. A área
  // cresce com a raiz da contagem (é área, não comprimento), e o viewBox se ajusta ao
  // conteúdo depois do layout, então "espalhar mais" não deixa nada fora de vista.
  var NOS_POR_TELA_BASE = 30;
  var AREA_MAXIMA = 4;          // teto do fator, pra grafo enorme não virar um mapa vazio
  // Repulsão extra entre nós e comprimento mínimo de aresta: o Fruchterman-Reingold puro
  // encosta vizinho em vizinho, e nó encostado é o que faz rótulo brigar com rótulo.
  var FATOR_REPULSAO = 1.6;
  var DISTANCIA_MINIMA_ARESTA = 55;
  // Puxão para o centro que substitui a parede da caixa do layout — ver o worker.
  var GRAVIDADE = 0.35;
  // Puxão para o centróide da própria comunidade (ver worker): fraco, só para as comunidades
  // convergirem juntas em vez de ficarem entremeadas no grafo todo.
  var COESAO_COMUNIDADE = 0.06;

  // Hash determinístico id -> [0,1): dá o mesmo número para o mesmo nó em qualquer recarga,
  // então o ponto de partida do layout (e o layout final) é reprodutível.
  function hash01(s) {
    var h = 2166136261;
    s = String(s);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 16777619) | 0; }
    return (h >>> 0) / 4294967295;
  }

  function areaLayout(qtdNos) {
    var fator = Math.min(Math.sqrt(Math.max(qtdNos, 1) / NOS_POR_TELA_BASE), AREA_MAXIMA);
    fator = Math.max(fator, 1);
    return { largura: LARGURA * fator, altura: ALTURA * fator };
  }

  var ROTULOS_TIPO = {
    wpp_mensagem: "Mensagem WhatsApp",
    sms_mensagem: "Mensagem SMS",
    contato_wpp: ICONE_PESSOA,
    contato_sms: ICONE_PESSOA,
    contato_agenda: "Contato agenda",
    ligacao: ICONE_TELEFONE,
    localizacao: ICONE_LOCAL,
    notificacao: "Notificação",
    nota: "Nota (bloco de notas)",
    navegacao: "Página visitada",
    arquivo: "Arquivo do aparelho (texto por OCR)",
    aplicativo: ICONE_DISPOSITIVO,
    dono: "Dono do aparelho",
    conta_usuario: "Conta de aplicativo",
    conta_financeira: ICONE_BANCO,
    email: "E-mail",
    rif_comunicacao: ICONE_DOCUMENTO,
    rif_envolvido: ICONE_PESSOA,
    rif_contraparte: "Contraparte (RIF)",
    simba_conta: ICONE_BANCO,
    simba_lancamento: "Lançamento bancário (SIMBA)",
    simba_contraparte: "Contraparte (SIMBA)",
    ipl_pessoa: ICONE_PESSOA,
    ipl_achado: "Achado do inquérito (IPL)",
    irpf_declaracao: ICONE_DOCUMENTO,
    irpf_bem: ICONE_BEM,
    irpf_dependente: "Dependente (IRPF)",
    irpf_rendimento: "Rendimento (IRPF)",
    irpf_divida: "Dívida (IRPF)",
    irpf_doacao: "Doação (IRPF)",
    ipei_alvo: "Alvo da quebra de sigilo fiscal (IPEI)",
    ipei_relacionado: "Vínculo (IPEI)",
    ipei_conta: ICONE_BANCO,
    ipei_bem: ICONE_BEM,
    ipei_nota: "Nota fiscal (IPEI)",
    ipei_contato: "Contato do alvo (IPEI)",
    telefonia_linha: "Ficha cadastral da linha (telefonia)",
    telefonia_chamada: ICONE_TELEFONE,
    telefonia_numero: "Número + IMEI (telefonia)",
    pessoa: ICONE_PESSOA,
    telefone: ICONE_TELEFONE,
    local: ICONE_LOCAL,
    veiculo: "Veículo",
    organizacao: "Organização",
    dispositivo: ICONE_DISPOSITIVO,
    outro: "Outra canônica",
    doc_pessoa: "Pessoa citada em documento",
    doc_achado: "Achado de documento",
    doc_documento: "Documento anexado",
    cluster: "Grupo de contrapartes (clique para abrir)"
  };

  var CORES_TIPO = {
    wpp_mensagem: "#25d366",
    sms_mensagem: "#4fb3d9",
    contato_wpp: ICONE_PESSOA,
    contato_sms: ICONE_PESSOA,
    contato_agenda: "#9b8afb",
    ligacao: ICONE_TELEFONE,
    localizacao: ICONE_LOCAL,
    notificacao: "#c99342",
    nota: "#d4b23c",
    navegacao: "#5b9bd5",
    arquivo: "#64748b",
    aplicativo: ICONE_DISPOSITIVO,
    dono: "#d95f8a",
    conta_usuario: "#8b5cf6",
    conta_financeira: ICONE_BANCO,
    email: "#7c6ee6",
    rif_comunicacao: ICONE_DOCUMENTO,
    rif_envolvido: ICONE_PESSOA,
    rif_contraparte: "#b7791f",
    simba_conta: ICONE_BANCO,
    simba_lancamento: "#2ea8b8",
    simba_contraparte: "#278495",
    ipl_pessoa: ICONE_PESSOA,
    ipl_achado: "#dc6b45",
    irpf_declaracao: ICONE_DOCUMENTO,
    irpf_bem: ICONE_BEM,
    irpf_dependente: "#c06fa2",
    irpf_rendimento: "#43a56a",
    irpf_divida: "#c96161",
    irpf_doacao: "#d98b43",
    ipei_alvo: "#1e9a8a",
    ipei_relacionado: "#3aa497",
    ipei_conta: ICONE_BANCO,
    ipei_bem: ICONE_BEM,
    ipei_nota: "#b89538",
    ipei_contato: "#438ea4",
    telefonia_linha: "#4b8ecc",
    telefonia_chamada: ICONE_TELEFONE,
    telefonia_numero: "#437bb5",
    pessoa: ICONE_PESSOA,
    telefone: ICONE_TELEFONE,
    local: ICONE_LOCAL,
    veiculo: "#f0a860",
    organizacao: "#b78ef0",
    dispositivo: ICONE_DISPOSITIVO,
    outro: "#8696a0",
    doc_pessoa: "#e0985a",
    doc_achado: "#54c7b8",
    doc_documento: "#94a3b8",
    cluster: "#6b7280"
  };

  var CORES_COMUNIDADE = [
    "#25d366", "#4fb3d9", "#f2789f", "#e0985a", "#9b8afb",
    "#54c7b8", "#c99342", "#d95f8a", "#5b9bd5", "#a97bd6"
  ];

  var ICONE_PESSOA = '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>';
  var ICONE_TELEFONE = '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>';
  var ICONE_LOCAL = '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>';
  var ICONE_DISPOSITIVO = '<rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>';
  var ICONE_DOCUMENTO = '<path d="M14 2H6a2 2 0 0 0-2 2v16h16V8zM14 2v6h6M8 13h8M8 17h5"/>';
  var ICONE_BANCO = '<path d="M3 10h18M5 10v8M9 10v8M15 10v8M19 10v8M3 19h18M12 3l9 5H3z"/>';
  var ICONE_BEM = '<path d="m3 11 9-8 9 8M5 10v10h14V10M9 20v-6h6v6"/>';

  // ícone desenhado dentro de cada nó, por tipo (paths estilo feather, 24x24,
  // mesmos traços dos ícones do menu lateral)
  var ICONES_TIPO = {
    wpp_mensagem: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
    sms_mensagem: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    contato_wpp: ICONE_PESSOA,
    contato_sms: ICONE_PESSOA,
    contato_agenda: '<path d="M4 4h16v16H4z"/><path d="M4 9h16"/><circle cx="12" cy="15" r="2"/>',
    ligacao: ICONE_TELEFONE,
    localizacao: ICONE_LOCAL,
    notificacao: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    nota: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h8"/><path d="M8 9h2"/>',
    navegacao: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    aplicativo: ICONE_DISPOSITIVO,
    dono: '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M17 11l2 2 4-4"/>',
    pessoa: ICONE_PESSOA,
    telefone: ICONE_TELEFONE,
    local: ICONE_LOCAL,
    veiculo: '<rect x="1" y="3" width="15" height="13"/><path d="M16 8h4l3 3v5h-7z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
    organizacao: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    dispositivo: ICONE_DISPOSITIVO,
    outro: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>',
    cluster: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/><path d="M12 5v.01M12 19v.01M18.36 5.64l-.01.01M5.64 5.64l.01.01M18.36 18.36l-.01-.01M5.64 18.36l.01-.01"/>'
  };

  // Os tipos vindos de fontes fiscais, bancárias e documentais antes caíam sem ícone. Cada
  // família agora conserva um símbolo reconhecível mesmo quando o rótulo está oculto pelo zoom.
  Object.assign(ICONES_TIPO, {
    arquivo: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    conta_usuario: '<circle cx="12" cy="8" r="3"/><path d="M6 20c.5-4 2.5-6 6-6s5.5 2 6 6"/><path d="M19 7a3 3 0 1 1-1-2.2"/>',
    conta_financeira: ICONE_BANCO,
    email: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    rif_comunicacao: ICONE_DOCUMENTO,
    rif_envolvido: ICONE_PESSOA,
    rif_contraparte: '<circle cx="9" cy="8" r="3"/><path d="M3 20c.5-4 2.5-6 6-6s5.5 2 6 6M17 8h5M19.5 5.5v5"/>',
    simba_conta: ICONE_BANCO,
    simba_lancamento: '<path d="M4 7h15M15 3l4 4-4 4M20 17H5M9 13l-4 4 4 4"/>',
    simba_contraparte: '<circle cx="9" cy="8" r="3"/><path d="M3 20c.5-4 2.5-6 6-6s5.5 2 6 6M18 6v8M21 8.5c0-1.2-1.3-2-3-2s-3 .8-3 2 1.3 2 3 2 3 .8 3 2-1.3 2-3 2-3-.8-3-2"/>',
    ipl_pessoa: ICONE_PESSOA,
    ipl_achado: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/>',
    irpf_declaracao: ICONE_DOCUMENTO,
    irpf_bem: ICONE_BEM,
    irpf_dependente: '<circle cx="9" cy="8" r="3"/><path d="M3 20c.5-4 2.5-6 6-6s5.5 2 6 6M18 8v6M15 11h6"/>',
    irpf_rendimento: '<circle cx="12" cy="12" r="9"/><path d="M12 6v12M16 9c0-1.7-1.8-3-4-3s-4 1.3-4 3 1.8 3 4 3 4 1.3 4 3-1.8 3-4 3-4-1.3-4-3"/>',
    irpf_divida: '<path d="M4 6h16M4 12h10M4 18h6M15 15l3 3 3-5"/>',
    irpf_doacao: '<path d="M20 12v9H4v-9M2 7h20v5H2zM12 7v14M12 7H7.5a2.5 2.5 0 1 1 0-5C11 2 12 7 12 7zM12 7h4.5a2.5 2.5 0 1 0 0-5C13 2 12 7 12 7z"/>',
    ipei_alvo: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    ipei_relacionado: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1"/>',
    ipei_conta: ICONE_BANCO,
    ipei_bem: ICONE_BEM,
    ipei_nota: '<path d="M6 2h12v20l-3-2-3 2-3-2-3 2zM9 7h6M9 11h6M9 15h4"/>',
    ipei_contato: '<rect x="4" y="3" width="16" height="18" rx="2"/><circle cx="12" cy="9" r="3"/><path d="M7 18c.5-3 2.2-4.5 5-4.5s4.5 1.5 5 4.5"/>',
    telefonia_linha: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8" cy="11" r="2.5"/><path d="M5 17c.3-2.3 1.3-3.5 3-3.5s2.7 1.2 3 3.5M13 9h5M13 13h5"/>',
    telefonia_chamada: ICONE_TELEFONE,
    telefonia_numero: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M9 5h6M12 18h.01"/>',
    doc_pessoa: ICONE_PESSOA,
    doc_achado: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/>',
    doc_documento: ICONE_DOCUMENTO
  });

  function corTipo(tipo) {
    return CORES_TIPO[tipo] || "#8696a0";
  }

  function el(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  // Afastamento do ponto de controle entre duas arestas vizinhas do MESMO par de nós. Como a
  // curva passa na metade do controle, 44 aqui deixa 22 px entre as linhas no meio do vão —
  // com os 22 de antes sobrava 11 px e "conversou com" caía em cima de "ligou para" (duas
  // relações entre as mesmas duas pessoas é o normal, não a exceção).
  var ESPACO_PARALELAS = 44;
  // Empurrão extra do rótulo, pra fora do feixe, pra ele não deitar sobre a própria linha.
  var AFASTAMENTO_ROTULO = 7;
  // Escalonamento VERTICAL entre rótulos de arestas paralelas, COMPLEMENTAR à perpendicular.
  // O rótulo é uma caixa larga e baixa (~13 px de altura), então o que precisa ser garantido é
  // a separação vertical. Numa aresta horizontal a própria perpendicular já é vertical e faz
  // esse serviço; numa aresta vertical ela separa só na horizontal e não resolve nada. Por isso
  // o empilhamento entra na proporção de quanto a perpendicular NÃO é vertical (1 - |py|), e
  // NO MESMO SENTIDO dela (o sinal de py): somar os dois cegamente fazia um puxar contra o
  // outro — numa aresta quase horizontal com py negativo, os 17 px de empilhamento cancelavam
  // 17 dos 29 px da perpendicular e os rótulos voltavam a encostar (medido: 12 px de separação
  // onde devia haver 46). Com o sinal certo, a separação vertical entre rótulos vizinhos nunca
  // cai abaixo de EMPILHAMENTO_ROTULO, em qualquer ângulo de aresta.
  var EMPILHAMENTO_ROTULO = 17;

  // Relação que a identificação de pessoas cria entre os titulares de dois aparelhos
  // apreendidos que conversam entre si (ver funcoes/identificacao_pessoas.py). Ganha traço
  // próprio: é achado, não mais um contato.
  var RELACAO_DESTAQUE = "dono do outro aparelho";

  // ---------- natureza da relação (cor da linha) ----------
  // De onde o vínculo veio: conversa de WhatsApp, ligação, extrato do SIMBA, RIF, PIX... Sem
  // isso o desenho responde "quem se liga a quem" e esconde "por quê" — três linhas cinzentas
  // entre as mesmas duas pessoas podem ser um papo, um pagamento e uma ligação, e é justamente
  // essa diferença que o investigador procura. ENT_TB07 não guarda a fonte em coluna própria,
  // então a natureza sai do TEXTO do tipo da relação (o mesmo que a identificação de pessoas
  // escreve — ver funcoes/identificacao_pessoas.py): a primeira regra que casa vence, e o que
  // não casa nenhuma (relação manual, sugestão da IA com texto livre) cai em "outra", cinza,
  // sem fingir que sabe a origem.
  var NATUREZAS_RELACAO = [
    { chave: "evidencia", kind: "evidencia", cor: "#7d8b95", tracejada: true,
      rotulo: "Evidência do registro",
      dica: "Liga a entidade canônica ao registro marcado na mesa que serve de evidência dela." },
    { chave: "agrupamento", kind: "cluster_aresta", cor: "#6b7280",
      rotulo: "Grupo de contrapartes",
      dica: "Ligação do nó \"+N\", que junta as contrapartes com uma relação só." },
    { chave: "donos", padrao: /dono do outro aparelho/, cor: "#e5534b",
      rotulo: "Titulares que conversam",
      dica: "Donos de dois aparelhos apreendidos no mesmo inquérito, conversando entre si." },
    // As da telefonia vêm ANTES da regra de "ligou para": a aresta do CDR se chama "ligou para
    // (telefonia)" e casaria com a regra da ligação do aparelho, que é outra fonte e outra cor.
    { chave: "telefonia", padrao: /\(telefonia\)/, cor: "#a3e635",
      rotulo: "Telefonia (operadora)",
      dica: "Titular da linha, chamadas do CDR e aparelho (IMEI) vindos da quebra de sigilo telefônico." },
    { chave: "irpf", padrao: /\(irpf\)/, cor: "#e879f9",
      rotulo: "Declaração de IRPF",
      dica: "Dependente declarado, fonte pagadora e beneficiário de doação, com CPF/CNPJ dos dois lados." },
    { chave: "conversa", padrao: /conversou com/, cor: "#25d366",
      rotulo: "Conversa (WhatsApp/SMS)",
      dica: "Mensagens trocadas com o dono do aparelho — WhatsApp, SMS ou outro mensageiro." },
    { chave: "ligacao", padrao: /ligou para/, cor: "#f0a860",
      rotulo: "Ligação telefônica",
      dica: "Registro de chamadas do aparelho." },
    { chave: "simba", padrao: /\(simba\)/, cor: "#4fb3d9",
      rotulo: "Extrato bancário (SIMBA)",
      dica: "Crédito ou débito entre o titular da conta e a contraparte, vindo do extrato do SIMBA." },
    { chave: "rif", padrao: /\(rif\)/, cor: "#d4b23c",
      rotulo: "RIF (COAF)",
      dica: "Crédito ou débito comunicado no RIF." },
    { chave: "ipei", padrao: /\(ipei\)/, cor: "#5eead4",
      rotulo: "Quebra de sigilo fiscal (IPEI)",
      dica: "Sociedade, vínculo empregatício, procuração ou transmissão de declaração do mesmo computador, tudo com documento dos dois lados." },
    { chave: "pix", padrao: /pix|transfer/, cor: "#32bcad",
      rotulo: "PIX / transferência",
      dica: "Chave PIX citada em conversa, ou transferência achada no próprio aparelho." },
    { chave: "cartao", padrao: /contato compartilhado/, cor: "#f2789f",
      rotulo: "Contato compartilhado",
      dica: "Cartão de contato (vCard) que alguém mandou numa conversa." },
    { chave: "aparelho", padrao: /dono de/, cor: "#a97bd6",
      rotulo: "Posse do aparelho",
      dica: "Titular declarado do aparelho apreendido." },
    { chave: "local", padrao: /esteve em/, cor: "#6ea8fe",
      rotulo: "Presença em local",
      dica: "Localização ou rede Wi-Fi que põe a pessoa no lugar." },
    { chave: "documento", padrao: /\(documento\)/, cor: "#fb923c",
      rotulo: "Citação em documento",
      dica: "Pessoa ou achado que a IA citou num documento genérico anexado — a rede do caso de inteligência montada a partir dos documentos." },
    { chave: "outra", cor: "#9aa4ad",
      rotulo: "Outra relação (manual ou IA)",
      dica: "Vínculo criado à mão na mesa ou sugerido pela IA, sem fonte reconhecida." }
  ];

  // A última da lista ("outra") não tem regra: é o padrão de quem não casa nenhuma.
  function naturezaAresta(a) {
    if (a._natureza) return a._natureza;
    var alvo = String(a.tipo || "").toLowerCase();
    var escolhida = NATUREZAS_RELACAO[NATUREZAS_RELACAO.length - 1];
    for (var i = 0; i < NATUREZAS_RELACAO.length; i++) {
      var nat = NATUREZAS_RELACAO[i];
      if (nat.kind ? a.kind === nat.kind : (nat.padrao && nat.padrao.test(alvo))) {
        escolhida = nat;
        break;
      }
    }
    a._natureza = escolhida;
    return escolhida;
  }

  // Posiciona (ou reposiciona) a curva e o rótulo de uma aresta a partir dos nós dela.
  // Usada no desenho e a cada quadro do arrasto, pra as paralelas não colapsarem numa só
  // enquanto o nó é arrastado.
  function posicionarAresta(a) {
    var na = noPorId[a.a], nb = noPorId[a.b];
    if (!na || !nb || !a._el) return;
    var desloc = a._desloc || 0;
    var mx = (na.x + nb.x) / 2, my = (na.y + nb.y) / 2;

    // A perpendicular sai da ordem CANÔNICA do par (a mesma que agrupa as paralelas), não do
    // sentido desta aresta: entre as mesmas duas pessoas é comum haver uma relação de ida e
    // outra de volta ("A conversou com B" e "B chave PIX citada por A"), e aí o sentido inverte
    // px/py justamente na aresta cujo slot também é o oposto — as duas inversões se cancelavam
    // e as duas curvas (com os rótulos) caíam EXATAMENTE uma sobre a outra. O traçado continua
    // sendo desenhado de na para nb, então a seta segue apontando o lado certo.
    var primeiro = a.a < a.b ? na : nb, segundo = a.a < a.b ? nb : na;
    var dx = segundo.x - primeiro.x, dy = segundo.y - primeiro.y;
    var d = Math.max(Math.hypot(dx, dy), 0.01);
    var px = -dy / d, py = dx / d;   // perpendicular unitária

    // A linha nasce e morre na BORDA dos nós, não nos centros: com o fim no centro a ponta da
    // seta desaparecia debaixo do nó seguinte e a linha riscava fotos por dentro. O rumo de cada
    // ponta é medido em direção ao ponto de controle da curva — assim o corte sai perpendicular
    // à tangente real da aresta, e as paralelas curvadas continuam encostando certinho.
    var qx = mx + px * desloc, qy = my + py * desloc;
    var angA = Math.atan2(qy - na.y, qx - na.x);
    var angB = Math.atan2(qy - nb.y, qx - nb.x);
    var folgaA = raioNaDirecao(na, angA) + 1.5;
    // +8 no destino é o recuo da própria seta: o refX do marcador já atrasa a ponta ~8 unidades
    // do fim do traçado, então o traçado para 8 px ANTES da borda e a ponta encosta nela.
    var folgaB = raioNaDirecao(nb, angB) + (a.kind === "evidencia" ? 2.5 : 8);
    var ax = na.x + Math.cos(angA) * folgaA, ay = na.y + Math.sin(angA) * folgaA;
    var bx = nb.x + Math.cos(angB) * folgaB, by = nb.y + Math.sin(angB) * folgaB;

    var dPath = "M" + ax + "," + ay + " Q" + qx + "," + qy + " " + bx + "," + by;
    a._el.setAttribute("d", dPath);
    if (a._hit) a._hit.setAttribute("d", dPath);   // a trilha invisível segue exatamente a linha

    // o rótulo acompanha a SUA curva (que passa na metade do controle), afastado mais um
    // pouco pro lado de fora e empilhado na vertical
    var lado = desloc === 0 ? 0 : (desloc > 0 ? 1 : -1);
    var sobre = desloc / 2 + lado * AFASTAMENTO_ROTULO;
    var sentido = py < 0 ? -1 : 1;   // py = 0 (aresta vertical): qualquer sentido serve
    var empilha = a._paralelas > 1
      ? (a._ordem - (a._paralelas - 1) / 2) * EMPILHAMENTO_ROTULO * (1 - Math.abs(py)) * sentido
      : 0;
    a._rotulo.setAttribute("x", mx + px * sobre);
    a._rotulo.setAttribute("y", my + py * sobre + empilha - 3);
  }

  // ---------- estado ----------

  var dados = null;            // resposta crua de /grafo
  var nos = [];                // nós visíveis (após filtros)
  var arestas = [];            // arestas visíveis
  var todosNos = [];           // nós antes dos filtros
  var todasArestas = [];
  var noPorId = {};            // id ("e12"/"c3") -> nó
  var tiposDesligados = {};    // legenda clicada
  var naturezasDesligadas = {}; // legenda de relações clicada (P0-4: agora filtra de verdade)
  var termoBusca = "";
  var comunidadePorNo = {};
  var arestasBaseVisao = [];   // arestas do conjunto filtrado (pré-corte de limite) — legenda conta aqui
  // Filtro por documentos (campo "docs" do chat de IA, ver GrafoFiltros): null = desligado;
  // senão {ddId:true} e o desenho mostra só esses documentos + a vizinhança de 1 salto deles.
  // Vai na URL (?docs=).
  var filtroDocsIds = null;
  // Recorte server-side por documentos (?docs=) — legado de links antigos; o painel atual
  // usa o recorte por fontes abaixo.
  var recorteDocsServidor = null;
  // Recorte server-side por FONTES (painel "🗂 Fontes"): csv "tp:id,..." que o próximo
  // carregarGrafo manda como ?fontes= — o servidor devolve só o que nasce daquelas fontes.
  // null = sem recorte (inquérito inteiro).
  var recorteFontesServidor = null;
  // Painel "🗂 Fontes": cache da lista de documentos do grafo (dd + nº de citações).
  var cacheDocsLista = null;
  // Isolamento por vizinhança (P1-7): null = desligado; senão {raizes:[ids], saltos:1|2}
  var isolamento = null;
  // Caminho entre selecionados (P1-8): conjunto de ids que deve sobrar na tela, ou null.
  var soCaminhoIds = null;
  var posCanonica = {};        // posição em memória das canônicas arrastadas (não persiste)
  var assinaturaLayout = null; // ids visíveis do último layout automático (detecta mudança de conjunto)
  // Hash O(n) de conjunto de ids, sem sort+join (que num grafo de 6k nós monta ~40KB de
  // string a cada atualizar). Dois acumuladores independentes: colisão exigiria os dois
  // falharem juntos — e o layout ainda tem a guarda de posição nula (ver calcularLayoutAsync).
  function hashIds(ids) {
    var h1 = 0, h2 = 0, i, c, s;
    for (i = 0; i < ids.length; i++) {
      s = ids[i];
      for (c = 0; c < s.length; c++) {
        h1 = ((h1 * 31) + s.charCodeAt(c)) | 0;
        h2 = ((h2 * 131) + s.charCodeAt(c)) | 0;
      }
      h1 = (h1 + 0x9e3779b9) | 0;
      h2 = (h2 + 0x85ebca6b) | 0;
    }
    return (h1 >>> 0).toString(36) + "." + (h2 >>> 0).toString(36);
  }
  // Cache do cálculo de comunidades (label-prop 20×(N+E)): vale enquanto o conjunto de nós
  // e a quantidade de arestas não mudam — trocar só o zoom/busca não recalcula.
  var cacheComunidades = { chave: null, valor: null };
  // Assinatura da última legenda desenhada: contar tipos/naturezas é O(n+E) barato, mas
  // reconstruir ~40 botões com listeners a cada tecla digitada não — pula o DOM se igual.
  var assinaturaLegenda = null;
  var vb = { x: 0, y: 0, w: LARGURA, h: ALTURA };  // viewBox (zoom/pan)
  // Câmera a restaurar no próximo layout (aplicar uma visão salva): o vb volta para onde o
  // investigador deixou, em vez de reenquadrar do zero. Consumido e zerado em atualizar().
  var cameraPendente = null;
  var modoLigacao = null;      // nó de origem do modo "Relacionar"

  // ---------- análise estrutural (elos críticos) ----------
  // Ponto de articulação = nó que, removido, desconecta o desenho; ponte = aresta que faz o
  // mesmo. Tarjan iterativo (sem recursão — um inquérito de milhares de nós estouraria a
  // pilha), O(N+E), recalculado sobre o DESENHO atual e cacheado pela mesma técnica das
  // comunidades (ver calcularElosCriticos). Não é a rede inteira, é o que está na tela: "quem,
  // se sumir daqui, parte ESTE quadro ao meio".
  var cacheElos = { chave: null, valor: null };
  var destacarElos = false;    // checkbox "Destacar elos críticos"

  // ---------- recorte por comunidade (legenda clicável) ----------
  // Índice da comunidade isolada na tela (null = todas). Diferente do isolamento por raízes:
  // aqui o critério é o grupo detectado por propagação de rótulos.
  var comunidadeFocada = null;

  // ---------- seleção por área (marquee) ----------
  var modoSelecaoArea = false; // botão "Selecionar área" ligado

  // ---------- gaveta de aresta (clique na linha) ----------
  var gavetaArestaAtual = null;

  // ---------- minimapa ----------
  var minimapaAtivo = true;
  var minimapaTela = null;     // {w, h, bbox} do último desenho — converte clique→coord do grafo

  // Com RIF/SIMBA cruzados na identificação de pessoas, um inquérito grande facilmente passa de
  // mil canônicas — o layout por força é O(n²) por iteração (cada nó repele todo o resto),
  // então desenhar tudo de uma vez trava o navegador bem antes de virar um desenho legível. Por
  // padrão só entram os LIMITE_NOS_PADRAO nós mais conectados (+ quem casa a busca, sempre) — o
  // resto continua no banco, só não no desenho; "mostrar todos" (aviso na tela) tira o limite.
  var LIMITE_NOS_PADRAO = 100;
  var limiteNos = LIMITE_NOS_PADRAO;
  var $limiteAviso = $("#mesa-limite-aviso");

  // Filtro de período (barra abaixo do grafo). Guarda a meia-noite UTC do dia escolhido
  // (instante de comparação — ver diaDoRegistro) e o texto "AAAA-MM-DD" da caixa, pra
  // exibir o período no mesmo formato que o investigador digitou.
  var filtroTempoInicio = null;
  var filtroTempoFim = null;
  var filtroTempoInicioTxt = null;
  var filtroTempoFimTxt = null;

  // Agrupamento fuzzy ("Agrupar entidades", só tela): os supernós são recriados do zero a
  // cada atualizar(), então o que persiste aqui é só o conjunto de canônicas que o
  // investigador desfez à mão ("c12" -> true) — elas seguem soltas até o global desligar.
  var fuzzyIgnorados = {};
  // Último resultado do agrupamento (para o retorno visual ao lado do checkbox).
  var fuzzyInfo = { grupos: 0, juntos: 0, pulado: false, elegiveis: 0 };
  // Teto de elegíveis do fuzzy: a comparação é O(n² × Levenshtein) na thread principal —
  // com 6k nós, ligar o checkbox congelava a aba. Acima disso o agrupamento é pulado e o
  // status pede um recorte (documentos, grau, busca) em vez de travar.
  var FUZZY_TETO_ELEGIVEIS = 1500;

  // ---------- loading overlay ----------

  function mostrarLoading(texto) {
    $("#mesa-loading-text").text(texto || "Carregando…");
    $("#mesa-loading").removeClass("hidden");
  }

  function ocultarLoading() {
    $("#mesa-loading").addClass("hidden");
  }

  // ---------- dropdown IA ----------

  $(document).on("click", function (evt) {
    $("#ia-dropdown-menu, #export-dropdown-menu, #visoes-dropdown-menu").addClass("hidden");
    $("#ia-dropdown, #export-dropdown, #visoes-dropdown").removeClass("aberto");
    // o <details> dos filtros não fecha sozinho ao clicar fora
    if (!$(evt.target).closest("#mesa-filtros").length) {
      $("#mesa-filtros").removeAttr("open");
    }
  });

  $("#ia-dropdown-btn").on("click", function (evt) {
    evt.stopPropagation();
    $("#ia-dropdown-menu").toggleClass("hidden");
    $("#ia-dropdown").toggleClass("aberto");
  });

  $("#ia-dropdown-menu").on("click", "button", function () {
    $("#ia-dropdown-menu").addClass("hidden");
    $("#ia-dropdown").removeClass("aberto");
  });

  // Exportações i2 (CSV/XLSX/ANX/GraphML): quatro botões viram um dropdown para a barra de
  // ferramentas caber numa tela menor. Os ids seguem os mesmos — js/exportar_i2.js não muda.
  $("#export-dropdown-btn").on("click", function (evt) {
    evt.stopPropagation();
    $("#export-dropdown-menu").toggleClass("hidden");
    $("#export-dropdown").toggleClass("aberto");
  });

  $("#export-dropdown-menu").on("click", "button", function () {
    $("#export-dropdown-menu").addClass("hidden");
    $("#export-dropdown").removeClass("aberto");
  });

  // ---------- empty state actions ----------

  $("#mesa-vazio-buscar-ia").on("click", function () {
    dispararBusca("prompts");
  });
  $("#mesa-vazio-pessoas").on("click", function () {
    $("#identificar-pessoas-btn").trigger("click");
  });
  $("#mesa-vazio-criar").on("click", function () {
    $("#mesa-nova-canonica-btn").trigger("click");
  });

  // ---------- montagem do modelo ----------

  function montarModelo(resp) {
    dados = resp;
    todosNos = [];
    noPorId = {};
    assinaturaLayout = null; // grafo recarregado: força um layout limpo
    cacheComunidades = { chave: null, valor: null };
    cacheElos = { chave: null, valor: null };
    comunidadeFocada = null;   // dados novos → a partição de comunidades antiga não vale mais
    assinaturaLegenda = null;
    cacheDocsLista = null;

    // Quais nós têm foto. Do grafo vem só o carimbo do último envio, nunca o binário: ele é
    // buscado uma vez por nó (ver carregarFotosPendentes) e o carimbo é o que faz a foto
    // trocada aparecer sem precisar de F5.
    var versaoFoto = {};
    (resp.imagens || []).forEach(function (im) {
      versaoFoto[im.tp_no + ":" + im.id_no] = String(im.dt_envio || "1");
    });

    (resp.entidades || []).forEach(function (e) {
      if (e.cd_revisao === "descartada") return;
      var no = {
        id: "e" + e.id_entidade,
        kind: "entidade",
        tipo: e.tp_origem,
        rotulo: e.rotulo_origem || e.tx_comentario || "(sem rótulo)",
        dado: e,
        imagem: versaoFoto["entidade:" + e.id_entidade] || null,
        fixo: e.nu_pos_x != null && e.nu_pos_y != null,
        x: e.nu_pos_x != null ? e.nu_pos_x : null,
        y: e.nu_pos_y != null ? e.nu_pos_y : null
      };
      todosNos.push(no);
      noPorId[no.id] = no;
    });

    (resp.canonicas || []).forEach(function (c) {
      // posição salva no servidor tem prioridade sobre a de memória (sobrevive a um F5);
      // a de memória só cobre o intervalo entre soltar o nó e a resposta do POST de posição.
      var posServidor = c.nu_pos_x != null && c.nu_pos_y != null ? { x: c.nu_pos_x, y: c.nu_pos_y } : null;
      var pos = posServidor || posCanonica[c.id_canonica];
      var no = {
        id: "c" + c.id_canonica,
        kind: "canonica",
        tipo: c.tp_entidade,
        rotulo: c.no_entidade,
        dado: c,
        imagem: versaoFoto["canonica:" + c.id_canonica] || null,
        fixo: !!pos,
        x: pos ? pos.x : null,
        y: pos ? pos.y : null
      };
      todosNos.push(no);
      noPorId[no.id] = no;
    });

    todasArestas = [];
    (resp.relacoes || []).forEach(function (r) {
      if (r.cd_revisao === "descartada") return;
      if (!noPorId["e" + r.id_entidade_origem] || !noPorId["e" + r.id_entidade_destino]) return;
      todasArestas.push({
        kind: "relacao", a: "e" + r.id_entidade_origem, b: "e" + r.id_entidade_destino,
        tipo: r.tx_tipo_relacao, dado: r, sugerida: r.cd_revisao === "sugerida"
      });
    });
    (resp.relacoes_canonicas || []).forEach(function (r) {
      if (!noPorId["c" + r.id_canonica_origem] || !noPorId["c" + r.id_canonica_destino]) return;
      todasArestas.push({
        kind: "relacao_canonica", a: "c" + r.id_canonica_origem, b: "c" + r.id_canonica_destino,
        tipo: r.tx_tipo_relacao, dado: r, sugerida: false
      });
    });
    (resp.vinculos_evidencia || []).forEach(function (v) {
      if (!noPorId["c" + v.id_canonica] || !noPorId["e" + v.id_entidade]) return;
      todasArestas.push({
        kind: "evidencia", a: "c" + v.id_canonica, b: "e" + v.id_entidade,
        tipo: "evidência", dado: v, sugerida: false
      });
    });

    // Grafo derivado dos DOCUMENTOS GENÉRICOS: nós criados na hora pela análise de IA dos
    // documentos anexados (pessoa dp*, achado da*, documento dd*), pendurados no documento em
    // que aparecem. É o que dá rede a um caso de inteligência — ele é feito de PDFs jogados
    // dentro, não de extrações. Read-only: a fonte da verdade é o documento, não a mesa.
    var camadasDoc = resp.documentos || {};
    (camadasDoc.nos || []).forEach(function (d) {
      if (noPorId[d.id]) return;
      var no = {
        id: d.id, kind: "doc", tipo: d.tipo, rotulo: d.rotulo, dado: d.dado || {},
        imagem: null, fixo: false, x: null, y: null
      };
      todosNos.push(no);
      noPorId[no.id] = no;
    });
    (camadasDoc.arestas || []).forEach(function (a) {
      if (!noPorId[a.a] || !noPorId[a.b]) return;
      todasArestas.push({
        kind: "doc_rel", a: a.a, b: a.b, tipo: a.tipo, dado: a.dado || {}, sugerida: false
      });
    });

    // marca cada canônica como "com relação" se participa de qualquer aresta (relação canônica
    // ou evidência) — usado pelos filtros "canônicas c/ relação" e "s/ relação". Propriedade
    // estável do nó (independe de quais camadas estão ligadas no momento).
    var canonicaComAresta = {};
    todasArestas.forEach(function (a) {
      canonicaComAresta[a.a] = true;
      canonicaComAresta[a.b] = true;
    });
    todosNos.forEach(function (n) {
      if (n.kind === "canonica") n.temRelacao = !!canonicaComAresta[n.id];
    });
  }

  // ---------- foto no lugar do nó ----------
  // O investigador sobe um retrato (ou a foto do carro, do local) e ela passa a ser o nó no
  // desenho, no lugar da bolinha colorida — é o que faz um grafo de trinta pessoas virar uma
  // parede de rostos reconhecível de longe, como o quadro de investigação de parede.
  //
  // O binário é buscado UMA vez por nó e guardado como data URL, não como src apontando para a
  // API. Duas razões: o grafo se recarrega a cada ação da mesa (confirmar, relacionar, apagar) e
  // um src remoto seria uma enxurrada de requisições a cada refresh; e o PNG exportado é o SVG
  // rasterizado a partir de uma data URL, contexto em que o navegador NÃO vai à rede — a foto
  // sairia como um buraco branco no meio do relatório.

  var fotoPorNo = {};   // "canonica:12|2026-09-04T..." -> data URL (null enquanto está baixando)
  // A4: com 6k nós, baixar centenas de fotos na abertura trava a rede e força um renderGrafo
  // extra no fim do lote — acima de 500 nós no desenho, as fotos esperam um recorte menor.
  var fotosAdiadas = false;
  var $inputImagem = $("#no-imagem-input");
  // espelha TAMANHO_MAX_ENVIO de api/funcoes/imagens_mesa.py: recusar aqui poupa o upload
  var TAMANHO_MAX_IMAGEM = 20 * 1024 * 1024;

  function idNo(n) {
    return n.kind === "canonica" ? n.dado.id_canonica : n.dado.id_entidade;
  }

  function chaveFoto(n) {
    return n.kind + ":" + idNo(n) + "|" + n.imagem;
  }

  function urlImagemNo(n) {
    return "/api/investigacao/nos/" + n.kind + "/" + idNo(n) + "/imagem";
  }

  function fotoDoNo(n) {
    if (!n.imagem || n.kind === "cluster") return null;
    return fotoPorNo[chaveFoto(n)] || null;
  }

  // XHR cru em vez de $.ajax: o que vem é binário, e o pipeline de conversão do jQuery espera
  // texto. FileReader porque data URL é o formato que serve às duas telas (grafo e PNG).
  function baixarFoto(n, aoTerminar) {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", urlImagemNo(n) + "?v=" + encodeURIComponent(n.imagem));
    xhr.responseType = "blob";
    xhr.onload = function () {
      if (xhr.status !== 200 || !xhr.response) { aoTerminar(null); return; }
      var leitor = new FileReader();
      leitor.onload = function () { aoTerminar(String(leitor.result)); };
      leitor.onerror = function () { aoTerminar(null); };
      leitor.readAsDataURL(xhr.response);
    };
    xhr.onerror = function () { aoTerminar(null); };
    xhr.send();
  }

  function carregarFotosPendentes() {
    if (nos.length > 500) { fotosAdiadas = true; return; }
    fotosAdiadas = false;
    var faltando = todosNos.filter(function (n) {
      return n.imagem && !(chaveFoto(n) in fotoPorNo);
    });
    if (!faltando.length) return;
    var pendentes = faltando.length;
    faltando.forEach(function (n) {
      var chave = chaveFoto(n);
      fotoPorNo[chave] = null;   // marca o pedido em curso: dois refreshes não pedem duas vezes
      baixarFoto(n, function (url) {
        fotoPorNo[chave] = url;
        if (--pendentes > 0) return;
        // o lote chegou: redesenha com as fotos e refaz a gaveta aberta (que mostra a miniatura)
        renderGrafo();
        refazerGaveta();
      });
    });
  }

  function refazerGaveta() {
    if (focados.length) abrirGaveta();
  }

  function escolherImagemNo(n) {
    $inputImagem.data("no", n.id);
    $inputImagem.val("");          // escolher o MESMO arquivo de novo tem de disparar change
    $inputImagem[0].click();
  }

  $inputImagem.on("change", function () {
    var arquivo = this.files && this.files[0];
    var n = noPorId[$inputImagem.data("no")];
    if (arquivo && n) enviarImagemNo(n, arquivo);
  });

  function enviarImagemNo(n, arquivo) {
    if (arquivo.size > TAMANHO_MAX_IMAGEM) {
      Toast.erro("Imagem grande demais (máximo " + (TAMANHO_MAX_IMAGEM / (1024 * 1024)) + " MB).");
      return;
    }
    var dados = new FormData();
    dados.append("arquivo", arquivo);
    mostrarLoading("Enviando imagem…");
    $.ajax({ url: urlImagemNo(n), method: "POST", data: dados, processData: false, contentType: false })
      .done(function () {
        Toast.sucesso("Imagem aplicada ao nó.");
        carregarGrafo(refazerGaveta);
      })
      .fail(function (xhr) {
        ocultarLoading();
        mostrarErroAjax(xhr);
      });
  }

  function removerImagemNo(n) {
    confirmar("Remover a imagem deste nó? Ele volta a ser desenhado com a cor e o ícone do tipo.", function () {
      $.ajax({ url: urlImagemNo(n), method: "DELETE" })
        .done(function () {
          Toast.sucesso("Imagem removida.");
          carregarGrafo(refazerGaveta);
        })
        .fail(mostrarErroAjax);
    }, "Remover");
  }

  // Linha "Imagem" da gaveta: miniatura (quando há) e os botões de enviar/trocar/remover. Vale
  // para as duas camadas de nó — a foto é do NÓ, não da camada.
  function renderImagemDoNo(n, $corpo) {
    var foto = fotoDoNo(n);
    $("<dt>").text("Imagem").appendTo($corpo);
    var $dd = $("<dd>").addClass("mesa-no-imagem").appendTo($corpo);
    if (foto) $("<img>").attr({ src: foto, alt: "Imagem de " + (n.rotulo || "") }).appendTo($dd);
    else $("<span>").addClass("mesa-no-imagem-vazia").text("Sem imagem — o nó sai com a cor e o ícone do tipo.").appendTo($dd);
    var $botoes = $("<div>").addClass("mesa-no-imagem-acoes").appendTo($dd);
    $("<button>").addClass("btn btn-secondary btn-small").html(window.Icones.html("imagem") + (foto ? " Trocar" : " Enviar imagem"))
      .on("click", function () { escolherImagemNo(n); }).appendTo($botoes);
    if (foto) {
      $("<button>").addClass("btn btn-secondary btn-small").html(window.Icones.html("apagar") + " Remover")
        .on("click", function () { removerImagemNo(n); }).appendTo($botoes);
    }
  }

  // ---------- filtro de período ----------

  function parseDataFiltro(iso) {
    if (!iso) return null;
    var d = new Date(String(iso).replace(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?)$/, "$1Z"));
    return isNaN(d.getTime()) ? null : d;
  }

  // A data DE CALENDÁRIO do registro no fuso do Brasil — a mesma que o detalhe do nó mostra
  // (formatarDataHora). Comparar no fuso evita cortar errado o que aconteceu, em horário
  // local, na beira do dia escolhido (uma mensagem das 23:30 do dia 31 não pode sair do
  // período "janeiro"). Retorna o instante UTC da meia-noite daquele dia.
  function diaDoRegistro(iso) {
    var instante = parseDataFiltro(iso);
    if (!instante) return null;
    var partes = {};
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(instante).forEach(function (p) { partes[p.type] = p.value; });
    return Date.UTC(+partes.year, +partes.month - 1, +partes.day);
  }

  // Datas "DD/MM/AAAA" que aparecem num texto — ex.: o detalhe "1646 mensagem(ns) entre
  // 11/12/2025 e 15/01/2026" de uma relação "conversou com". O período REAL de atividade de
  // uma canônica identificada costuma estar só aqui: as evidências dela são contatos (sem
  // data), e sem isto o nó só teria a data de criação — a da extração, não a do fato.
  // Devolve meia-noite UTC de cada dia, como diaDoRegistro.
  function datasEmTexto(texto) {
    var dias = [];
    if (!texto) return dias;
    var re = /\b(\d{2})\/(\d{2})\/(\d{4})\b/g;
    var m;
    while ((m = re.exec(texto)) !== null) {
      dias.push(Date.UTC(+m[3], +m[2] - 1, +m[1]));
    }
    return dias;
  }

  // Datas de ATIVIDADE do nó (sem a criação): entidade usa a do registro de origem; canônica
  // usa as dos registros que a evidenciam (as entidades ligadas a ela por vínculo) e os
  // períodos escritos no detalhe das relações canônicas dela ("conversou com", "ligou para").
  function datasDeAtividade(n) {
    var dias = [];
    if (n.kind === "entidade") {
      var d = n.dado.dt_origem ? diaDoRegistro(n.dado.dt_origem) : null;
      if (d != null) dias.push(d);
    } else if (n.kind === "canonica") {
      todasArestas.forEach(function (a) {
        if (a.kind !== "evidencia") return;
        var outro = a.a === n.id ? a.b : (a.b === n.id ? a.a : null);
        if (!outro) return;
        var e = noPorId[outro];
        if (!e || e.kind !== "entidade") return;
        var de = e.dado.dt_origem ? diaDoRegistro(e.dado.dt_origem) : null;
        if (de != null) dias.push(de);
      });
      todasArestas.forEach(function (a) {
        if (a.kind !== "relacao_canonica") return;
        if (a.a !== n.id && a.b !== n.id) return;
        dias.push.apply(dias, datasEmTexto(a.dado.tx_detalhe));
      });
    }
    return dias;
  }

  // Datas de calendário do nó usadas pelo filtro de período — só as de ATIVIDADE. A criação
  // do nó NÃO entra: num nó automático ela é a data da extração, não a do fato (extraiu-se em
  // 18/08/2026 um papo de 11/12/2025 a 15/01/2026 — o nó não pode "caber" num filtro de
  // agosto só porque foi criado lá).
  function datasDoNo(n) {
    return datasDeAtividade(n);
  }

  // Entra no grafo se QUALQUER data do nó cair no período escolhido. Com o período ativo,
  // nó sem nenhuma atividade datada não passa — não dá pra dizer que ele é "do período".
  function passaFiltroTempo(n) {
    if (!filtroTempoInicio && !filtroTempoFim) return true;
    var dias = datasDoNo(n);
    if (!dias.length) return false;
    return dias.some(function (d) {
      if (filtroTempoInicio && d < filtroTempoInicio) return false;
      if (filtroTempoFim && d > filtroTempoFim) return false;
      return true;
    });
  }

  // Os nós que passam nos filtros da tela — ANTES do agrupamento de folhas e do teto de nós, que
  // são decisões de DESENHO. O desenho parte daqui e ainda corta; a exportação para o i2 para
  // aqui, porque lá não existe desenho a poupar.
  function nosFiltradosBase() {
    var mostrarSugeridas = $("#filtro-sugeridas").prop("checked");
    var mostrarConfirmadas = $("#filtro-confirmadas").prop("checked");
    var mostrarCanonComRel = $("#filtro-canonicas-com").prop("checked");
    var mostrarCanonSemRel = $("#filtro-canonicas-sem").prop("checked");
    var mostrarDocs = $("#filtro-documentos").prop("checked");

    return todosNos.filter(function (n) {
      if (tiposDesligados[n.tipo]) return false;
      if (n.kind === "doc") return mostrarDocs && passaFiltroTempo(n);
      if (!passaFiltroTempo(n)) return false;
      if (n.kind === "canonica") {
        return n.temRelacao ? mostrarCanonComRel : mostrarCanonSemRel;
      }
      if (n.dado.cd_revisao === "sugerida") return mostrarSugeridas;
      return mostrarConfirmadas;
    });
  }

  // ---------- agrupamento fuzzy de entidades ("Agrupar entidades") ----------
  // Junta NA TELA nós do mesmo tipo com nomes parecidos — ex.: "MARCELLO DOS SANTOS
  // COSTA" e "MARCELO DOS SANTOS" (erro de digitação + sobrenome a menos) viram um nó só
  // com as relações somadas, e o mesmo vale para achados/pessoas de documento ("Art. 121
  // CP (Homicídio)" x "Art. 121 do Código Penal (Homicídio)"). Nada muda no banco: fundir
  // de verdade continua sendo uma entidade por vez, pelo "Fundir com..." da gaveta (nó de
  // documento nem tem linha no banco — o grupo dele é só visual). Desligar o global desfaz tudo.
  //
  // A regra (nomesSimilares): quase todo o nome menor tem de estar contido no maior, token
  // a token com tolerância a erro de digitação — mas só em token de LETRAS (1 erro até 5
  // letras, 2 em mais longos); token com número (CPF, telefone, IMEI, coordenada, placa)
  // só casa exato, porque um dígito de diferença ali é outra entidade — e sobrando no
  // máximo 2 tokens no maior. E os dois lados precisam de 2+ tokens de conteúdo (preposição
  // fora): código/sobrenome solto nunca agrupa, e é isso que impede um sobrenome comum de
  // virar ponte juntando gente diferente no mesmo grupo.

  var FUZZY_STOPWORDS = { DE: 1, DA: 1, DO: 1, DAS: 1, DOS: 1, DU: 1, E: 1, DEL: 1, LA: 1, EL: 1 };

  // Abreviação jurídica: "CP"/"CPB" e "CÓDIGO PENAL" (opcionalmente + "BRASILEIRO") são o
  // mesmo código — sem dobrar, "Art. 121 CP (Homicídio)" nunca casa com "Art. 121 do Código
  // Penal (Homicídio)" (CP não casa com CODIGO/PENAL e a conta 3/4=0,75 fica abaixo do
  // corte 0,8). "CPP"/"CÓDIGO DE PROCESSO PENAL" NÃO dobram: outro código, outra entidade.
  function dobrarTokensJuridicos(tokens) {
    var saida = [];
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      if (t === "ARTIGO" || t === "ARTIGOS") t = "ART";
      if (t === "CP" || t === "CPB") { saida.push("CODIGOPENAL"); continue; }
      if (t === "CODIGO" && tokens[i + 1] === "PENAL") {
        saida.push("CODIGOPENAL");
        i++; // consome o PENAL
        if (tokens[i + 1] === "BRASILEIRO") i++; // consome o BRASILEIRO opcional
        continue;
      }
      // "CP BRASILEIRO" separado (raro) não vira dois códigos: o BRASILEIRO já está no dobrado
      if (t === "BRASILEIRO" && saida.length && saida[saida.length - 1] === "CODIGOPENAL") continue;
      saida.push(t);
    }
    return saida;
  }

  function normalizarTokensNome(rotulo) {
    var base = String(rotulo || "")
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toUpperCase().replace(/[^A-Z0-9]+/g, " ")
      .replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "");
    if (!base) return [];
    var tokens = base.split(" ").filter(function (t) { return t && !FUZZY_STOPWORDS[t]; });
    return dobrarTokensJuridicos(tokens);
  }

  function distanciaLevenshtein(a, b) {
    if (a === b) return 0;
    var n = a.length, m = b.length;
    if (!n) return m;
    if (!m) return n;
    var anterior = [], atual = [], i, j;
    for (j = 0; j <= m; j++) anterior[j] = j;
    for (i = 1; i <= n; i++) {
      atual[0] = i;
      for (j = 1; j <= m; j++) {
        var custo = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
        var del = anterior[j] + 1, ins = atual[j - 1] + 1, sub = anterior[j - 1] + custo;
        atual[j] = Math.min(del, ins, sub);
      }
      var tmp = anterior; anterior = atual; atual = tmp;
    }
    return anterior[m];
  }

  function tokensNomeCasam(a, b) {
    if (a === b) return true;
    // Token com número (CPF, telefone, IMEI, coordenada, placa, modelo) só casa exato: um
    // dígito de diferença ali é OUTRA entidade, não erro de digitação ("...3240" x "...3241",
    // CPFs vizinhos, coordenadas próximas). Erro de digitação se tolera em nome de gente.
    if (/[0-9]/.test(a) || /[0-9]/.test(b)) return false;
    // 1 erro em token curto (até 5 letras), 2 em longo: "ROOS" x "FOODS" (2 erros em 5
    // letras) são palavras diferentes; "MARCELLO" x "MARCELO" continua casando.
    var maior = Math.max(a.length, b.length);
    return distanciaLevenshtein(a, b) <= (maior <= 5 ? 1 : 2);
  }

  function nomesSimilares(rotA, rotB) {
    var ta = normalizarTokensNome(rotA), tb = normalizarTokensNome(rotB);
    if (ta.length < 2 || tb.length < 2) return false;
    var curto = ta.length <= tb.length ? ta : tb;
    var longo = ta.length <= tb.length ? tb : ta;
    // poda barata: sem nenhum par de tokens começando igual, nem calcula distância
    var prefixoEmComum = curto.some(function (c) {
      return longo.some(function (l) { return c.slice(0, 2) === l.slice(0, 2); });
    });
    if (!prefixoEmComum) return false;
    var usados = {};
    var casados = curto.filter(function (c) {
      for (var i = 0; i < longo.length; i++) {
        if (usados[i] || !tokensNomeCasam(c, longo[i])) continue;
        usados[i] = true;
        return true;
      }
      return false;
    }).length;
    return (casados / curto.length) >= 0.8 && (longo.length - casados) <= 2;
  }

  function agrupamentoFuzzyAtivo() {
    var $c = $("#agrupar-entidades");
    return $c.length > 0 && $c.prop("checked");
  }

  function lerMinRelacoes() {
    var v = parseInt($("#filtro-grau-min").val(), 10);
    return isFinite(v) && v > 0 ? v : 0;
  }

  // Une os nós elegíveis em supernós "fuzzy:<id do representante>" (um por grupo, só
  // grupos de 2+) e reaponta as arestas dos membros para o supernó. O representante é o mais
  // conectado (o hub que o investigador reconhece), desempate pelo nome mais completo.
  // Laço interno (membro↔membro do mesmo grupo) some; duplicata exata no mesmo sentido, com
  // o mesmo tipo e camada, vira uma aresta só — o desenho curva as paralelas que sobram.
  //
  // Elegível: canônica + achado/pessoa de documento (doc_achado/doc_pessoa). O achado entra
  // porque a IA grava cada variação literal ("Art. 121 CP (Homicídio)" x "Art. 121 do Código
  // Penal (Homicídio)") como um nó da* separado — sem o fuzzy, "agrupar entidades" dizia
  // "nenhum nome parecido" no meio de 11 Art. 121. Documento anexado (dd*) nunca agrupa:
  // "DISP 168.pdf" e "DISP 149.pdf" são documentos distintos, não o mesmo com outro nome.
  function fuzzyElegivel(n) {
    if (!n || fuzzyIgnorados[n.id]) return false;
    if (n.kind === "canonica") return true;
    if (n.kind === "doc" && (n.tipo === "doc_achado" || n.tipo === "doc_pessoa")) return true;
    return false;
  }

  // Achado só agrupa com achado do MESMO tp_achado (tipificação com tipificação, telefone
  // com telefone): sem isso um número "121" poderia encostar num "Art. 121" pelo radical.
  function mesmoGrupoFuzzy(a, b) {
    if (a.tipo !== b.tipo) return false;
    if (a.tipo === "doc_achado") {
      var ta = String((a.dado && a.dado.tp_achado) || "").toLowerCase();
      var tb = String((b.dado && b.dado.tp_achado) || "").toLowerCase();
      if (ta !== tb) return false;
    }
    return true;
  }

  function agruparEntidadesSimilares(candidatosBase, listaArestas) {
    Object.keys(noPorId).forEach(function (id) {
      if (id.indexOf("fuzzy:") === 0) delete noPorId[id];
    });

    var idsBase = {};
    candidatosBase.forEach(function (n) { idsBase[n.id] = true; });
    var grau = {};
    candidatosBase.forEach(function (n) { grau[n.id] = 0; });
    listaArestas.forEach(function (a) {
      if (!idsBase[a.a] || !idsBase[a.b]) return;
      grau[a.a]++;
      grau[a.b]++;
    });

    var elegiveis = candidatosBase
      .filter(fuzzyElegivel)
      .sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });

    // Teto anti-travamento (ver FUZZY_TETO_ELEGIVEIS): pula o O(n²) e avisa no status.
    if (elegiveis.length > FUZZY_TETO_ELEGIVEIS) {
      return { nos: candidatosBase, arestas: listaArestas, grupos: 0, juntos: 0,
               pulado: true, elegiveis: elegiveis.length };
    }

    // estrela pelo representante do grupo (sem encadeamento): entrar no grupo exige casar
    // com o primeiro membro, não com qualquer um — senão "A~B, B~C" juntava A com C.
    var grupos = [];
    elegiveis.forEach(function (n) {
      for (var i = 0; i < grupos.length; i++) {
        var g = grupos[i];
        if (!mesmoGrupoFuzzy(g.rep, n)) continue;
        if (nomesSimilares(g.rep.rotulo, n.rotulo)) { g.membros.push(n); return; }
      }
      grupos.push({ tipo: n.tipo, rep: n, membros: [n] });
    });

    var juntos = {};
    var supernos = [];
    grupos.forEach(function (g) {
      if (g.membros.length < 2) return;
      var rep = g.membros.slice().sort(function (a, b) {
        var dg = (grau[b.id] || 0) - (grau[a.id] || 0);
        if (dg) return dg;
        var dt = (b.rotulo || "").length - (a.rotulo || "").length;
        if (dt) return dt;
        return a.id < b.id ? -1 : 1;
      })[0];
      var no = {
        id: "fuzzy:" + rep.id, kind: "fuzzy", tipo: g.tipo,
        rotulo: rep.rotulo, dado: {},
        qtAgrupados: g.membros.length - 1,
        membros: g.membros.map(function (m) { return m.id; }),
        fixo: !!rep.fixo, x: rep.x, y: rep.y
      };
      noPorId[no.id] = no;
      g.membros.forEach(function (m) { juntos[m.id] = no; });
      supernos.push(no);
    });

    if (!supernos.length) return { nos: candidatosBase, arestas: listaArestas, grupos: 0, juntos: 0 };

    var nos2 = candidatosBase
      .filter(function (n) { return !juntos[n.id]; })
      .concat(supernos);
    var ids2 = {};
    nos2.forEach(function (n) { ids2[n.id] = true; });

    var vistos = {};
    var arestas2 = [];
    listaArestas.forEach(function (a) {
      var na = juntos[a.a] ? juntos[a.a].id : a.a;
      var nb = juntos[a.b] ? juntos[a.b].id : a.b;
      if (na === nb || !ids2[na] || !ids2[nb]) return;
      var chave = na + "→" + nb + "|" + a.kind + "|" + (a.tipo || "");
      if (vistos[chave]) return;
      vistos[chave] = true;
      arestas2.push({ kind: a.kind, a: na, b: nb, tipo: a.tipo, dado: a.dado || {}, sugerida: !!a.sugerida });
    });

    var juntosTotal = supernos.reduce(function (t, s) { return t + s.membros.length; }, 0);
    return { nos: nos2, arestas: arestas2, grupos: supernos.length, juntos: juntosTotal };
  }

  function aplicarFiltros() {
    var mostrarSugeridas = $("#filtro-sugeridas").prop("checked");

    var candidatosBase = nosFiltradosBase();

    // Agrupamento fuzzy (só tela): une os nós de nome parecido ANTES de todo o resto,
    // então o supernó já entra no cálculo de grau, no corte e nas comunidades com tudo somado.
    var etapaFuzzy = agrupamentoFuzzyAtivo()
      ? agruparEntidadesSimilares(candidatosBase, todasArestas)
      : { nos: candidatosBase, arestas: todasArestas, grupos: 0, juntos: 0 };
    fuzzyInfo = { grupos: etapaFuzzy.grupos || 0, juntos: etapaFuzzy.juntos || 0,
                  pulado: !!etapaFuzzy.pulado, elegiveis: etapaFuzzy.elegiveis || 0 };

    var agrupamento = agruparFolhas(etapaFuzzy.nos, etapaFuzzy.arestas);
    var candidatos = agrupamento.nos;
    var todasArestasComCluster = agrupamento.arestas;

    // P0-4: a legenda de relações agora filtra de verdade — aresta de natureza desligada
    // nem entra no cálculo de grau nem no desenho (antes era só informativa).
    var arestasBase = todasArestasComCluster.filter(function (a) {
      return !naturezasDesligadas[naturezaAresta(a).chave];
    });
    arestasBaseVisao = arestasBase;

    // grau "de verdade" (sobre TODAS as arestas do conjunto já agrupado, não só as que
    // sobrarem depois do corte) — é o critério de quem fica quando passa do limite: os mais
    // conectados contam a história melhor que uma amostra aleatória.
    var candidatosVisiveis = {};
    candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
    var grauReal = {};
    candidatos.forEach(function (n) { grauReal[n.id] = 0; });
    arestasBase.forEach(function (a) {
      if (candidatosVisiveis[a.a] && candidatosVisiveis[a.b]) {
        grauReal[a.a]++;
        grauReal[a.b]++;
      }
    });

    // Comunidades: partição estável sobre o CONJUNTO-BASE (antes de grau/isolamento/comunidade/
    // corte), então as cores e a legenda não trocam ao mexer no filtro de grau — e dá para isolar
    // um grupo pela legenda sem que o resto se reembaralhe (ver comunidadeFocada).
    comunidadePorNo = calcularComunidades(candidatos, arestasBase);

    // Filtro "Relações: N ou mais": esconde os nós com menos ligações que o mínimo no conjunto
    // atual — é o que tira a periferia (grau 1) e deixa os hubs. Quem casa a busca fica, mesmo
    // com pouca relação: ele é o motivo de o investigador estar olhando.
    var minRel = lerMinRelacoes();
    if (minRel > 0) {
      var salvoBusca = {};
      if (termoBusca) candidatos.forEach(function (n) { if (casaBusca(n)) salvoBusca[n.id] = true; });
      candidatos = candidatos.filter(function (n) {
        return (grauReal[n.id] || 0) >= minRel || salvoBusca[n.id];
      });
      candidatosVisiveis = {};
      candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
      // o grau exibido em cada sobrevivente é recalculado adiante, só sobre as arestas visíveis
    }

    // Recorte por comunidade (legenda clicável): só quem é do grupo escolhido. Vem antes dos
    // recortes explícitos (isolamento/caminho), que continuam por cima deste.
    if (comunidadeFocada !== null && comunidadeFocada !== undefined) {
      candidatos = candidatos.filter(function (n) { return comunidadePorNo[n.id] === comunidadeFocada; });
      candidatosVisiveis = {};
      candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
    }

    // P1-7/P1-8: isolamento por vizinhança e "só caminho" recortam os candidatos
    // antes do teto de nós — o recorte é o que o investigador pediu para ver.
    if (soCaminhoIds) {
      candidatos = candidatos.filter(function (n) { return soCaminhoIds[n.id]; });
      candidatosVisiveis = {};
      candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
    } else if (isolamento && isolamento.raizes && isolamento.raizes.length) {
      var alcance = vizinhancaNSaltos(isolamento.raizes, isolamento.saltos || 1, candidatosVisiveis, arestasBase);
      candidatos = candidatos.filter(function (n) { return alcance[n.id]; });
      candidatosVisiveis = {};
      candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
    }

    // Filtro por documentos (painel "📄 Documentos"): ficam os documentos marcados e a
    // vizinhança de 1 salto deles (quem é citado neles) — o recorte pedido antes do teto.
    if (filtroDocsIds) {
      var raizesDocs = Object.keys(filtroDocsIds).filter(function (id) { return candidatosVisiveis[id]; });
      if (raizesDocs.length) {
        var alcanceDocs = vizinhancaNSaltos(raizesDocs, 1, candidatosVisiveis, arestasBase);
        candidatos = candidatos.filter(function (n) { return alcanceDocs[n.id]; });
        candidatosVisiveis = {};
        candidatos.forEach(function (n) { candidatosVisiveis[n.id] = true; });
      }
    }

    if (candidatos.length > limiteNos) {
      var casam = termoBusca ? candidatos.filter(casaBusca) : [];
      var casamIds = {};
      casam.forEach(function (n) { casamIds[n.id] = true; });
      var resto = candidatos
        .filter(function (n) { return !casamIds[n.id]; })
        .sort(function (a, b) { return grauReal[b.id] - grauReal[a.id]; });
      nos = casam.concat(resto.slice(0, Math.max(0, limiteNos - casam.length)));
    } else {
      nos = candidatos;
    }

    var oculto = candidatos.length - nos.length;
    if (oculto > 0) {
      $limiteAviso.removeClass("hidden").empty()
        .append(document.createTextNode(
          "Mostrando " + nos.length.toLocaleString("pt-BR") + " de " + candidatos.length.toLocaleString("pt-BR") +
          " nós — os mais conectados" + (termoBusca ? " (mais quem casa a busca)" : "") + ". "
        ))
        .append(
          $("<button>").attr("type", "button").text("Mostrar todos")
            .attr("title", "Grafos muito grandes usam layout radial rápido em vez da simulação de forças.")
            .on("click", function () { limiteNos = Infinity; atualizar(); })
        );
    } else {
      $limiteAviso.addClass("hidden").empty();
    }

    var visiveis = {};
    nos.forEach(function (n) { visiveis[n.id] = true; });

    arestas = arestasBase.filter(function (a) {
      if (!visiveis[a.a] || !visiveis[a.b]) return false;
      if (a.sugerida && !mostrarSugeridas) return false;
      return true;
    });

    nos.forEach(function (n) { n.grau = 0; });
    arestas.forEach(function (a) {
      if (noPorId[a.a]) noPorId[a.a].grau++;
      if (noPorId[a.b]) noPorId[a.b].grau++;
    });

    // comunidadePorNo já veio calculado sobre a base (estável) lá em cima — não recalcula aqui,
    // senão ao isolar uma comunidade o resto se renumeraria e as cores mudariam sob os pés.

    if (window.EstadoUrl && idInquerito) {
      try {
        window.EstadoUrl.salvar({
          busca: termoBusca || "",
          grau: String(lerMinRelacoes() || ""),
          docs: filtroDocsIds ? Object.keys(filtroDocsIds).map(function (id) {
            return id.replace(/^dd/, "");
          }).join(",") : "",
          fontes: recorteFontesServidor || "",
        });
      } catch (e) { /* URL é conveniência — nunca pode quebrar o grafo */ }
    }
  }

  // BFS limitada a N saltos a partir das raízes, sobre um conjunto de arestas e restrita
  // aos ids permitidos (candidatos). Devolve o conjunto alcançado (raízes incluídas).
  function vizinhancaNSaltos(raizes, saltos, permitidos, listaArestas) {
    var adj = {};
    (listaArestas || arestas).forEach(function (a) {
      if (permitidos && (!permitidos[a.a] || !permitidos[a.b])) return;
      (adj[a.a] = adj[a.a] || []).push(a.b);
      (adj[a.b] = adj[a.b] || []).push(a.a);
    });
    var vistos = {};
    var borda = [];
    raizes.forEach(function (id) {
      if (!permitidos || permitidos[id]) { vistos[id] = true; borda.push(id); }
    });
    for (var s = 0; s < (saltos || 1); s++) {
      var prox = [];
      borda.forEach(function (id) {
        (adj[id] || []).forEach(function (viz) {
          if (!vistos[viz] && (!permitidos || permitidos[viz])) {
            vistos[viz] = true;
            prox.push(viz);
          }
        });
      });
      borda = prox;
      if (!borda.length) break;
    }
    return vistos;
  }

  // Menor caminho (BFS) entre dois nós sobre as arestas visíveis. Devolve a lista de ids
  // do caminho (inclui origem e destino) ou null se desconectados.
  function bfsCaminho(origem, destino, listaArestas) {
    if (origem === destino) return [origem];
    var adj = {};
    (listaArestas || arestas).forEach(function (a) {
      (adj[a.a] = adj[a.a] || []).push(a.b);
      (adj[a.b] = adj[a.b] || []).push(a.a);
    });
    var anterior = {};
    anterior[origem] = true;
    var fila = [origem];
    while (fila.length) {
      var atual = fila.shift();
      var vizinhos = adj[atual] || [];
      for (var i = 0; i < vizinhos.length; i++) {
        var v = vizinhos[i];
        if (anterior[v] !== undefined) continue;
        anterior[v] = atual;
        if (v === destino) {
          var caminho = [v];
          var p = atual;
          while (p !== true) { caminho.unshift(p); p = anterior[p]; }
          return caminho;
        }
        fila.push(v);
      }
    }
    return null;
  }

  // ---------- agrupamento de "folhas" (contraparte com uma relação só) ----------
  // Um titular com centenas de contrapartes do SIMBA/RIF vira uma estrela ilegível: cada uma
  // delas só tem UMA relação (com o titular) e nada mais a mostrar sozinha. Quando um nó tem
  // LIMIAR_AGRUPAMENTO ou mais "folhas" do mesmo tipo de relação, elas somem e viram um nó só
  // "+N <tipo>" — clicável, abre de volta os originais. Só olha relação canônica↔canônica
  // (RELACAO_CANONICA): entidade-comentário e vínculo de evidência não entram nessa conta.
  // P2-9: limiar configurável na tela (#filtro-limiar-cluster); 999999 = desligado.
  var LIMIAR_AGRUPAMENTO_PADRAO = 3;
  var clustersExpandidos = {}; // "hubId|tipoRelacao" -> true (usuário clicou pra abrir)

  function lerLimiarCluster() {
    var v = parseInt($("#filtro-limiar-cluster").val(), 10);
    return isFinite(v) && v >= 1 ? v : LIMIAR_AGRUPAMENTO_PADRAO;
  }

  function agruparFolhas(candidatosBase, listaArestas) {
    // Chamado com a lista já reescrita pelo fuzzy (arestas apontando para os supernós); sem
    // ela, cai na lista global — o comportamento de antes.
    listaArestas = listaArestas || todasArestas;
    var candIds = {};
    candidatosBase.forEach(function (n) { candIds[n.id] = true; });

    var grauCanonico = {};
    candidatosBase.forEach(function (n) { grauCanonico[n.id] = 0; });
    listaArestas.forEach(function (a) {
      if (a.kind !== "relacao_canonica") return;
      if (!candIds[a.a] || !candIds[a.b]) return;
      grauCanonico[a.a]++;
      grauCanonico[a.b]++;
    });

    // Grau na camada de documentos (arestas doc_rel): a cauda longa de um caso de
    // inteligência é a pessoa/achado citado num documento só — absorvidos em "+N citações"
    // no documento, o desenho inicial de 280 documentos deixa de carregar milhares de folhas.
    var grauDoc = {};
    candidatosBase.forEach(function (n) { grauDoc[n.id] = 0; });
    listaArestas.forEach(function (a) {
      if (a.kind !== "doc_rel") return;
      if (!candIds[a.a] || !candIds[a.b]) return;
      grauDoc[a.a]++;
      grauDoc[a.b]++;
    });

    // pra cada nó de grau 1, guarda quem é o vizinho (hub) e o tipo da relação que os liga.
    // O supernó fuzzy nunca é "folha": ele acabou de juntar gente justamente para ser visto,
    // então não pode ser absorvido num "+N" na sequência (vira hub normalmente, quando for o caso).
    // O mesmo vale para o supernó fuzzy da camada doc (kind fuzzy com tipo doc_*).
    var vizinhoUnico = {};
    listaArestas.forEach(function (a) {
      if (a.kind !== "relacao_canonica") return;
      if (!candIds[a.a] || !candIds[a.b]) return;
      var na = noPorId[a.a], nb = noPorId[a.b];
      if (grauCanonico[a.a] === 1 && na && na.kind === "canonica") vizinhoUnico[a.a] = { hub: a.b, tipo: a.tipo };
      if (grauCanonico[a.b] === 1 && nb && nb.kind === "canonica") vizinhoUnico[a.b] = { hub: a.a, tipo: a.tipo };
    });
    listaArestas.forEach(function (a) {
      if (a.kind !== "doc_rel") return;
      if (!candIds[a.a] || !candIds[a.b]) return;
      var na = noPorId[a.a], nb = noPorId[a.b];
      if (grauDoc[a.a] === 1 && na && na.kind === "doc" &&
          (na.tipo === "doc_pessoa" || na.tipo === "doc_achado") &&
          nb && nb.tipo === "doc_documento") {
        vizinhoUnico[a.a] = { hub: a.b, tipo: "citações" };
      }
      if (grauDoc[a.b] === 1 && nb && nb.kind === "doc" &&
          (nb.tipo === "doc_pessoa" || nb.tipo === "doc_achado") &&
          na && na.tipo === "doc_documento") {
        vizinhoUnico[a.b] = { hub: a.a, tipo: "citações" };
      }
    });

    var grupos = {}; // "hubId|tipo" -> [ids das folhas]
    Object.keys(vizinhoUnico).forEach(function (id) {
      // quem casa a busca fica sempre visível sozinho, mesmo que o resto do grupo agrupe
      if (termoBusca && casaBusca(noPorId[id])) return;
      var info = vizinhoUnico[id];
      var chave = info.hub + "|" + info.tipo;
      if (clustersExpandidos[chave]) return; // usuário pediu pra ver este grupo aberto
      (grupos[chave] = grupos[chave] || []).push(id);
    });

    var membrosAgrupados = {};
    var clusters = [];
    Object.keys(grupos).forEach(function (chave) {
      var membros = grupos[chave];
      if (membros.length < lerLimiarCluster()) return; // poucos — não compensa agrupar
      var separador = chave.indexOf("|");
      var hubId = chave.slice(0, separador), tipo = chave.slice(separador + 1);
      if (!candIds[hubId]) return;
      membros.forEach(function (id) { membrosAgrupados[id] = true; });
      var clusterId = "cluster:" + chave;
      // o cluster é reconstruído a cada atualizar(); herda a posição do anterior, senão nasce
      // com x/y nulos e, quando o CONJUNTO de nós não mudou, o layout nem roda pra preenchê-la
      // — o nó ia parar em <circle cx="null"> e sumia do desenho
      var anterior = noPorId[clusterId];
      var no = {
        id: clusterId, kind: "cluster", tipo: "cluster",
        rotulo: "+" + membros.length + " " + tipo,
        dado: {},
        fixo: !!(anterior && anterior.fixo),
        x: anterior ? anterior.x : null,
        y: anterior ? anterior.y : null,
        hub: hubId, tipoRelacao: tipo, membros: membros
      };
      noPorId[clusterId] = no;
      clusters.push(no);
    });

    var nos2 = candidatosBase
      .filter(function (n) { return !membrosAgrupados[n.id]; })
      .concat(clusters);

    var arestas2 = listaArestas
      .filter(function (a) { return !(membrosAgrupados[a.a] || membrosAgrupados[a.b]); })
      .concat(clusters.map(function (c) {
        return { kind: "cluster_aresta", a: c.hub, b: c.id, tipo: "", dado: {}, sugerida: false };
      }));

    return { nos: nos2, arestas: arestas2 };
  }

  function expandirCluster(n) {
    clustersExpandidos[n.hub + "|" + n.tipoRelacao] = true;
    atualizar();
  }

  // ---------- layout (Fruchterman-Reingold com nós fixados) ----------
  // O núcleo O(n²) roda num Web Worker (js/mesa_layout_worker.js) pra não travar clique/scroll/
  // zoom enquanto um grafo grande se organiza. Sem suporte a Worker (raro), cai no cálculo
  // síncrono chunked via requestAnimationFrame de sempre.

  var layoutHandle = null;   // requestAnimationFrame handle (fallback sem Worker)
  var layoutWorker = null;

  function cancelarLayout() {
    if (layoutHandle) { cancelAnimationFrame(layoutHandle); layoutHandle = null; }
    if (layoutWorker) { layoutWorker.terminate(); layoutWorker = null; }
  }

  function calcularLayoutAsync(ok) {
    cancelarLayout();

    // Toda saída de recálculo passa por aqui: o passe anti-colisão roda no fim do layout,
    // seja ele worker, síncrono ou radial, e só quando as posições mudaram de verdade.
    function terminar(recalculou) {
      if (recalculou) afastarColisoes();
      if (ok) ok(recalculou);
    }

    if (!nos.length) { if (ok) ok(false); return; }

    // Só refaz o layout automático quando o CONJUNTO de nós visíveis muda (filtro/legenda).
    // Buscar, colorir por comunidade ou arrastar um nó não mexem nas posições. Sem isso, ao
    // ocultar nós (ex.: desmarcar "canônicas s/ relação") os que sobram ficavam parados nos
    // lugares antigos, deixando buracos e um arranjo espalhado e difícil de ler.
    // A assinatura é um hash O(n) — sort+join de 6k ids montava ~40KB de string por atualizar.
    var assinatura = nos.length + "n" + hashIds(nos.map(function (n) { return n.id; }));
    var algumSemPosicao = nos.some(function (n) { return n.x == null || n.y == null; });
    if (assinatura === assinaturaLayout && !algumSemPosicao) { if (ok) ok(false); return; }
    assinaturaLayout = assinatura;

    // recomeça as posições automáticas do zero pra o subgrafo visível se reorganizar e ocupar
    // o espaço livre. Nós fixados pelo investigador (arrastados) mantêm o lugar escolhido.
    nos.forEach(function (n) { if (!n.fixo) { n.x = null; n.y = null; } });

    var area = areaLayout(nos.length);
    var soltos = nos.filter(function (n) { return n.x == null || n.y == null; });

    // Ponto de partida circular por COMUNIDADE: membros da mesma comunidade começam lado a
    // lado no anel (e em raio levemente variado pelo hash do id), então o FR converge com elas
    // juntas em vez de embaralhar parentes. O hash torna a semente reprodutível: o mesmo
    // conjunto de nós desenha igual entre recargas.
    var porCom = {};
    soltos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      c = (c === undefined || c === null) ? -1 : c;
      (porCom[c] = porCom[c] || []).push(n);
    });
    var chavesCom = Object.keys(porCom).sort(function (a, b) { return porCom[b].length - porCom[a].length; });
    var raioSemente = Math.min(area.largura, area.altura) / 3;
    var totalSoltos = Math.max(soltos.length, 1);
    var antesSoltos = 0;
    chavesCom.forEach(function (chaveCom) {
      var membros = porCom[chaveCom];
      var ang0 = (2 * Math.PI * antesSoltos) / totalSoltos;
      var arco = (2 * Math.PI * membros.length) / totalSoltos;
      membros.forEach(function (n, k) {
        var ang = ang0 + arco * ((k + 0.5 + (hash01(n.id) - 0.5) * 0.4) / Math.max(membros.length, 1));
        var rr = raioSemente * (0.72 + 0.28 * hash01(n.id + "|raio"));
        n.x = area.largura / 2 + Math.cos(ang) * rr;
        n.y = area.altura / 2 + Math.sin(ang) * rr;
      });
      antesSoltos += membros.length;
    });
    if (!soltos.length) { terminar(true); return; } // tudo fixado — nada a calcular

    // P2-10: desenho grande não passa pelo FR O(n²) — 6k nós = ~10B ops. Acima de 500
    // nós usa o radial por comunidade (O(n), síncrono, instantâneo); entre 200 e 500 o
    // worker roda com iterações reduzidas. O FR completo fica para recortes pequenos.
    if (nos.length > 500) {
      layoutRadialPorComunidade(area);
      $statusLabel.text("");
      terminar(true);
      return;
    }

    $statusLabel.text("Organizando grafo…");

    var indicePorId = {};
    nos.forEach(function (n) { indicePorId[n.id] = n; });

    // aplica posições vindas do worker/loop; confere n.fixo de novo (não só o que foi mandado
    // pro worker) pro caso do investigador ter arrastado um nó enquanto o cálculo ainda rodava
    function aplicarPosicoes(posicoes) {
      Object.keys(posicoes).forEach(function (id) {
        var n = indicePorId[id];
        if (n && !n.fixo) { n.x = posicoes[id].x; n.y = posicoes[id].y; }
      });
    }

    if (window.Worker) {
      var worker = new Worker("js/mesa_layout_worker.js");
      layoutWorker = worker;
      worker.onmessage = function (evt) {
        if (evt.data.tipo === "progresso") {
          aplicarPosicoes(evt.data.posicoes);
        } else if (evt.data.tipo === "fim") {
          aplicarPosicoes(evt.data.posicoes);
          worker.terminate();
          if (layoutWorker === worker) layoutWorker = null;
          $statusLabel.text("");
          terminar(true);
        }
      };
      worker.onerror = function () {
        // navegador bloqueou o worker (ex.: aberto via file://, sem servidor) — cai pro
        // cálculo síncrono em vez de deixar o layout parado pra sempre.
        worker.terminate();
        if (layoutWorker === worker) layoutWorker = null;
        calcularLayoutSincrono(terminar);
      };
      var iteracoes = nos.length > 200 ? 80 : LAYOUT_ITERACOES;
      worker.postMessage({
        nos: nos.map(function (n) { return { id: n.id, x: n.x, y: n.y, fixo: !!n.fixo }; }),
        arestas: arestas.map(function (a) { return { a: a.a, b: a.b }; }),
        comunidades: nos.map(function (n) {
          var c = comunidadePorNo[n.id];
          return (c === undefined || c === null) ? -1 : c;
        }),
        largura: area.largura, altura: area.altura, margem: MARGEM,
        iteracoes: iteracoes, porLote: LAYOUT_POR_FRAME,
        repulsao: FATOR_REPULSAO, distanciaMinima: DISTANCIA_MINIMA_ARESTA,
        gravidade: GRAVIDADE, coesao: COESAO_COMUNIDADE
      });
      return;
    }

    calcularLayoutSincrono(terminar);
  }

  // P2-10: layout radial por comunidade para desenhos grandes — O(n), sem simulação.
  // Cada comunidade ganha um setor do círculo maior e os membros se distribuem num
  // subcírculo; hubs (maior grau) ficam no centro do setor. Nós fixados mantêm o lugar.
  function layoutRadialPorComunidade(area) {
    var grupos = {};
    nos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined || c === null) c = -1;
      (grupos[c] = grupos[c] || []).push(n);
    });
    var chaves = Object.keys(grupos).sort(function (a, b) { return grupos[b].length - grupos[a].length; });
    var cx = area.largura / 2, cy = area.altura / 2;
    var raioGeral = Math.min(area.largura, area.altura) * 0.38;
    chaves.forEach(function (chave, gi) {
      var membros = grupos[chave].sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); });
      var ang = (2 * Math.PI * gi) / Math.max(chaves.length, 1) - Math.PI / 2;
      var gx = cx + Math.cos(ang) * raioGeral * (chaves.length > 1 ? 1 : 0);
      var gy = cy + Math.sin(ang) * raioGeral * (chaves.length > 1 ? 1 : 0);
      var raioGrupo = Math.min(90 + Math.sqrt(membros.length) * 14, raioGeral * 0.9 + 40);
      membros.forEach(function (n, i) {
        if (n.fixo && n.x != null) return;
        if (i === 0) { n.x = gx; n.y = gy; return; }
        var a2 = (2 * Math.PI * (i - 1)) / Math.max(membros.length - 1, 1);
        var rr = raioGrupo * (0.35 + 0.65 * ((i % 5) / 4));
        n.x = gx + Math.cos(a2) * rr;
        n.y = gy + Math.sin(a2) * rr;
      });
    });
  }

  function calcularLayoutSincrono(ok) {
    var area = areaLayout(nos.length);
    var K = Math.sqrt((area.largura * area.altura) / nos.length) * 0.9;
    var temperatura = area.largura / 10;

    var indice = {};
    nos.forEach(function (n, i) { indice[n.id] = i; });

    var iteracao = 0;

    function passo() {
      var fim = Math.min(iteracao + LAYOUT_POR_FRAME, LAYOUT_ITERACOES);
      while (iteracao < fim) {
        nos.forEach(function (n) { n.dispX = 0; n.dispY = 0; });

        for (var i = 0; i < nos.length; i++) {
          for (var j = i + 1; j < nos.length; j++) {
            var a = nos[i], b = nos[j];
            var dx = a.x - b.x, dy = a.y - b.y;
            var d = Math.max(Math.hypot(dx, dy), 0.01);
            var f = (K * K * FATOR_REPULSAO) / d;
            a.dispX += (dx / d) * f; a.dispY += (dy / d) * f;
            b.dispX -= (dx / d) * f; b.dispY -= (dy / d) * f;
          }
        }

        arestas.forEach(function (e) {
          var a = nos[indice[e.a]], b = nos[indice[e.b]];
          if (!a || !b) return;
          var dx = a.x - b.x, dy = a.y - b.y;
          var d = Math.max(Math.hypot(dx, dy), 0.01);
          // a aresta só puxa a partir de DISTANCIA_MINIMA_ARESTA: mais perto que isso ela
          // não encurta nada, e os dois nós param de se encostar
          var util = Math.max(d - DISTANCIA_MINIMA_ARESTA, 0);
          var f = (util * util) / K;
          a.dispX -= (dx / d) * f; a.dispY -= (dy / d) * f;
          b.dispX += (dx / d) * f; b.dispY += (dy / d) * f;
        });

        // coesão de comunidade — ver o worker (o caminho síncrono precisa do mesmo empurrão)
        if (COESAO_COMUNIDADE > 0) {
          var cxCom = {}, cyCom = {}, qtCom = {};
          nos.forEach(function (n) {
            var c = comunidadePorNo[n.id];
            if (c === undefined || c === null) return;
            cxCom[c] = (cxCom[c] || 0) + n.x; cyCom[c] = (cyCom[c] || 0) + n.y;
            qtCom[c] = (qtCom[c] || 0) + 1;
          });
          nos.forEach(function (n) {
            var c = comunidadePorNo[n.id];
            if (c === undefined || c === null || qtCom[c] < 2) return;
            var mdx = cxCom[c] / qtCom[c] - n.x, mdy = cyCom[c] / qtCom[c] - n.y;
            var md = Math.max(Math.hypot(mdx, mdy), 0.01);
            var mf = ((md * md) / K) * COESAO_COMUNIDADE;
            n.dispX += (mdx / md) * mf; n.dispY += (mdy / md) * mf;
          });
        }

        nos.forEach(function (n) {
          var gdx = area.largura / 2 - n.x, gdy = area.altura / 2 - n.y;
          var gd = Math.max(Math.hypot(gdx, gdy), 0.01);
          var gf = ((gd * gd) / K) * GRAVIDADE;
          n.dispX += (gdx / gd) * gf; n.dispY += (gdy / gd) * gf;
        });

        nos.forEach(function (n) {
          if (n.fixo) return; // posição escolhida pelo investigador não se move
          var d = Math.max(Math.hypot(n.dispX, n.dispY), 0.01);
          var lim = Math.min(d, temperatura);
          n.x += (n.dispX / d) * lim;
          n.y += (n.dispY / d) * lim;
          n.x = Math.max(-area.largura, Math.min(area.largura * 2, n.x));
          n.y = Math.max(-area.altura, Math.min(area.altura * 2, n.y));
        });

        temperatura *= 0.98;
        iteracao++;
      }

      if (iteracao < LAYOUT_ITERACOES) {
        layoutHandle = requestAnimationFrame(passo);
      } else {
        layoutHandle = null;
        $statusLabel.text("");
        if (ok) ok(true);
      }
    }

    layoutHandle = requestAnimationFrame(passo);
  }

  // Passe anti-colisão depois do layout: empurra para fora todo par de nós mais próximo que
  // raioA+raioB+folga (a folga é o espaço do rótulo, que vive embaixo do nó). O FR puro encosta
  // vizinho em vizinho — e nó encostado é o que faz rótulo brigar com rótulo. Grade espacial
  // O(n): cada nó só olha as 9 células ao redor, então roda até no radial de milhares de nós.
  function afastarColisoes() {
    if (nos.length < 2) return;
    var CELULA = 96;
    var grade = {};
    function chaveCelula(x, y) { return Math.floor(x / CELULA) + ":" + Math.floor(y / CELULA); }
    function reconstruir() {
      grade = {};
      nos.forEach(function (n) {
        if (n.x == null) return;
        var k = chaveCelula(n.x, n.y);
        (grade[k] = grade[k] || []).push(n);
      });
    }
    reconstruir();
    for (var rodada = 0; rodada < 3; rodada++) {
      var moveu = false;
      nos.forEach(function (n) {
        if (n.x == null) return;
        var cx = Math.floor(n.x / CELULA), cy = Math.floor(n.y / CELULA);
        for (var gx = cx - 1; gx <= cx + 1; gx++) {
          for (var gy = cy - 1; gy <= cy + 1; gy++) {
            var cel = grade[gx + ":" + gy];
            if (!cel) continue;
            for (var i = 0; i < cel.length; i++) {
              var m = cel[i];
              if (m === n || m.id <= n.id) continue; // cada par uma única vez
              var min = raioNo(n) + raioNo(m) + 18;
              var dx = n.x - m.x, dy = n.y - m.y;
              var d = Math.max(Math.hypot(dx, dy), 0.01);
              if (d >= min) continue;
              var abre = min - d;
              var ux = dx / d, uy = dy / d;
              if (!n.fixo && !m.fixo) {
                n.x += ux * abre / 2; n.y += uy * abre / 2;
                m.x -= ux * abre / 2; m.y -= uy * abre / 2;
              } else if (!n.fixo) {
                n.x += ux * abre; n.y += uy * abre;
              } else if (!m.fixo) {
                m.x -= ux * abre; m.y -= uy * abre;
              } else {
                continue; // os dois fixos: a escolha do investigador prevalece
              }
              moveu = true;
            }
          }
        }
      });
      if (!moveu) break;
      reconstruir(); // a grade acompanha as posições novas
    }
  }

  // ---------- análise: comunidades e centralidade ----------

  function vizinhanca(nosBase, arestasBase) {
    nosBase = nosBase || nos; arestasBase = arestasBase || arestas;
    var viz = {};
    nosBase.forEach(function (n) { viz[n.id] = []; });
    arestasBase.forEach(function (a) {
      if (!viz[a.a] || !viz[a.b]) return;   // aresta que toca nó fora do conjunto-base
      viz[a.a].push(a.b);
      viz[a.b].push(a.a);
    });
    return viz;
  }

  function calcularComunidades(nosBase, arestasBase) {
    // Cache (A1): a propagação é 20×(N+E) — com 6k nós, recalcular a cada tecla/busca trava.
    // Vale enquanto o conjunto de nós e a quantidade de arestas não mudam.
    nosBase = nosBase || nos; arestasBase = arestasBase || arestas;
    var chave = nosBase.length + "n" + arestasBase.length + "e" + hashIds(nosBase.map(function (n) { return n.id; }));
    if (cacheComunidades.chave === chave && cacheComunidades.valor) return cacheComunidades.valor;
    var resultado = calcularComunidadesDeVerdade(nosBase, arestasBase);
    cacheComunidades = { chave: chave, valor: resultado };
    return resultado;
  }

  function calcularComunidadesDeVerdade(nosBase, arestasBase) {
    // propagação de rótulos: cada nó adota o rótulo mais comum entre os vizinhos
    var viz = vizinhanca(nosBase, arestasBase);
    var rotulo = {};
    var ids = nosBase.map(function (n) { return n.id; }).sort();
    ids.forEach(function (id, i) { rotulo[id] = i; });

    for (var it = 0; it < 20; it++) {
      var mudou = false;
      ids.forEach(function (id) {
        if (!viz[id].length) return;
        var contagem = {};
        viz[id].forEach(function (v) { contagem[rotulo[v]] = (contagem[rotulo[v]] || 0) + 1; });
        var melhor = rotulo[id], melhorQt = -1;
        Object.keys(contagem).sort().forEach(function (r) {
          if (contagem[r] > melhorQt) { melhorQt = contagem[r]; melhor = parseInt(r, 10); }
        });
        if (melhor !== rotulo[id]) { rotulo[id] = melhor; mudou = true; }
      });
      if (!mudou) break;
    }

    // renumera comunidades para 0..N na ordem de aparição
    var mapa = {}, proximo = 0, resultado = {};
    ids.forEach(function (id) {
      if (mapa[rotulo[id]] === undefined) mapa[rotulo[id]] = proximo++;
      resultado[id] = mapa[rotulo[id]];
    });
    return resultado;
  }

  // Elos críticos do DESENHO atual: pontos de articulação (nós) e pontes (arestas). Tarjan
  // iterativo sobre multigrafo não-dirigido — a adjacência guarda o ÍNDICE da aresta e o DFS só
  // ignora a aresta pela qual chegou, não o pai. É isso que trata certo as arestas paralelas
  // (conversa + PIX entre o mesmo par): uma delas não pode ser ponte se há outra ligando os dois.
  function calcularElosCriticos(nosBase, arestasBase) {
    nosBase = nosBase || nos; arestasBase = arestasBase || arestas;
    var idx = {};
    nosBase.forEach(function (n, i) { idx[n.id] = i; });
    var N = nosBase.length;
    var adj = new Array(N);
    for (var i = 0; i < N; i++) adj[i] = [];
    var edges = [];
    arestasBase.forEach(function (a) {
      var u = idx[a.a], v = idx[a.b];
      if (u == null || v == null || u === v) return;   // nó fora do conjunto ou laço
      var e = edges.length;
      edges.push([u, v]);
      adj[u].push([v, e]); adj[v].push([u, e]);
    });

    var disc = new Array(N).fill(-1), low = new Array(N).fill(0),
        parentEdge = new Array(N).fill(-1),
        articulacoes = new Array(N).fill(false),
        ponteEdge = new Array(edges.length).fill(false);
    var tempo = 0;

    function chavePar(a, b) { return a < b ? a + "|" + b : b + "|" + a; }

    for (var raiz = 0; raiz < N; raiz++) {
      if (disc[raiz] !== -1) continue;
      disc[raiz] = low[raiz] = tempo++;
      var pilha = [{ u: raiz, pi: 0 }];
      while (pilha.length) {
        var topo = pilha[pilha.length - 1];
        var u = topo.u;
        if (topo.pi < adj[u].length) {
          var par = adj[u][topo.pi++];
          var v = par[0], e = par[1];
          if (e === parentEdge[u]) continue;           // só a aresta de chegada
          if (disc[v] === -1) {
            parentEdge[v] = e;
            disc[v] = low[v] = tempo++;
            pilha.push({ u: v, pi: 0 });
          } else {
            low[u] = Math.min(low[u], disc[v]);        // back edge
          }
          continue;
        }
        pilha.pop();
        if (parentEdge[u] !== -1) {
          var pe = parentEdge[u];
          var p = edges[pe][0] === u ? edges[pe][1] : edges[pe][0];
          if (low[u] > disc[p]) ponteEdge[pe] = true;              // ponte
          if (parentEdge[p] !== -1 && low[u] >= disc[p]) articulacoes[p] = true;
          low[p] = Math.min(low[p], low[u]);
        } else {
          var filhos = 0;                                            // raiz: articula com 2+ filhos
          for (var k = 0; k < adj[u].length; k++) {
            var to = adj[u][k][0], ee = adj[u][k][1];
            if (parentEdge[to] === ee) filhos++;
          }
          if (filhos > 1) articulacoes[u] = true;
        }
      }
    }

    var out = { articulacoes: {}, pontes: {} };
    for (var a2 = 0; a2 < N; a2++) if (articulacoes[a2]) out.articulacoes[nosBase[a2].id] = true;
    for (var e2 = 0; e2 < edges.length; e2++) {
      if (ponteEdge[e2]) out.pontes[chavePar(nosBase[edges[e2][0]].id, nosBase[edges[e2][1]].id)] = true;
    }
    return out;
  }

  // Elo crítico com cache pelo conjunto desenhado: o mesmo par nó/aresta que entra na chave das
  // comunidades. Recalculado só quando o desenho muda, não a cada render.
  function obterElosCriticos() {
    var chave = nos.length + "n" + arestas.length + "e" + hashIds(nos.map(function (n) { return n.id; }));
    if (cacheElos.chave === chave && cacheElos.valor) return cacheElos.valor;
    var valor = calcularElosCriticos(nos, arestas);
    cacheElos = { chave: chave, valor: valor };
    return valor;
  }

  // ---------- renderização ----------

  function renderLegenda() {
    var contagem = {};
    todosNos.forEach(function (n) { contagem[n.tipo] = (contagem[n.tipo] || 0) + 1; });
    // A1: contar é O(n+E) barato; reconstruir ~50 botões com listeners a cada atualizar
    // (cada tecla da busca!) não — pula o DOM quando nada mudou desde a última vez.
    var baseSig = (arestasBaseVisao && arestasBaseVisao.length) ? arestasBaseVisao : arestas;
    var contNatSig = {};
    baseSig.forEach(function (a) {
      var c = naturezaAresta(a).chave;
      contNatSig[c] = (contNatSig[c] || 0) + 1;
    });
    var tamanhosComSig = {};
    nos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c !== undefined) tamanhosComSig[c] = (tamanhosComSig[c] || 0) + 1;
    });
    function parteSig(obj) {
      return Object.keys(obj).sort().map(function (k) { return k + ":" + obj[k]; }).join(";");
    }
    var sig = parteSig(contagem) + "|" + parteSig(tiposDesligados) + "|" +
      parteSig(contNatSig) + "|" + parteSig(naturezasDesligadas) + "|" +
      Object.keys(tamanhosComSig).map(function (k) { return tamanhosComSig[k]; })
        .sort(function (a, b) { return b - a; }).join(",") +
      "|" + (comunidadeFocada === null || comunidadeFocada === undefined ? "" : "com" + comunidadeFocada);
    if (sig === assinaturaLegenda) return;
    assinaturaLegenda = sig;
    $legenda.empty();
    // O título separa esta linha da legenda das RELAÇÕES logo abaixo: são duas legendas de cor
    // seguidas, uma da bolinha do nó e outra da linha, e sem nome uma passa pela outra.
    if (Object.keys(contagem).length) {
      $("<span>").addClass("mesa-legenda-titulo").text("Nós:").appendTo($legenda);
      // P2-9: bulk todos/nenhum — com ~40 tipos, ligar/desligar um por um é inviável.
      $("<button>").attr("type", "button").addClass("mesa-legenda-item")
        .attr("title", "Mostra todos os tipos de nó")
        .text("Todos")
        .on("click", function () { tiposDesligados = {}; atualizar(); })
        .appendTo($legenda);
      $("<button>").attr("type", "button").addClass("mesa-legenda-item")
        .attr("title", "Esconde todos os tipos (depois religue só os que interessam)")
        .text("Nenhum")
        .on("click", function () {
          Object.keys(contagem).forEach(function (t) { tiposDesligados[t] = true; });
          atualizar();
        })
        .appendTo($legenda);
    }
    Object.keys(contagem).forEach(function (tipo) {
      var $item = $("<button>").attr("type", "button").addClass("mesa-legenda-item")
        .toggleClass("desligado", !!tiposDesligados[tipo])
        .attr("title", "Clique para mostrar/ocultar este tipo")
        .on("click", function () {
          tiposDesligados[tipo] = !tiposDesligados[tipo];
          atualizar();
        });
      var $bolinha = $("<span>").addClass("mesa-legenda-bolinha").css("background", corTipo(tipo)).appendTo($item);
      if (ICONES_TIPO[tipo]) {
        $bolinha.addClass("com-icone").html(
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
          ICONES_TIPO[tipo] + "</svg>"
        );
      }
      $("<span>").text((ROTULOS_TIPO[tipo] || tipo) + " (" + contagem[tipo] + ")").appendTo($item);
      $legenda.append($item);
    });
    renderLegendaRelacoes();
    renderLegendaComunidades();
  }

  // Legenda das linhas. Só entram as naturezas presentes no conjunto filtrado (não só no
  // desenho — senão, ao desligar uma natureza, ela sumia da legenda e não tinha como religar).
  // P0-4: clicável — desliga a natureza de verdade (não entra no grau nem no desenho).
  function renderLegendaRelacoes() {
    var $alvo = $("#mesa-legenda-relacoes").empty();
    var contagem = {};
    var temSugerida = false;
    // conta sobre as arestas do conjunto (pré-corte de limite), para a legenda não piscar
    // a cada "mostrar 100 de 6000"; cai para as visíveis quando aquele conjunto não existe.
    var base = (arestasBaseVisao && arestasBaseVisao.length) ? arestasBaseVisao : arestas;
    base.forEach(function (a) {
      var nat = naturezaAresta(a);
      contagem[nat.chave] = (contagem[nat.chave] || 0) + 1;
      if (a.sugerida) temSugerida = true;
    });

    var presentes = NATUREZAS_RELACAO.filter(function (nat) { return contagem[nat.chave]; });
    if (!presentes.length) return;

    $("<span>").addClass("mesa-legenda-titulo").text("Relações:").appendTo($alvo);
    presentes.forEach(function (nat) {
      var $item = $("<button>").attr("type", "button")
        .addClass("mesa-legenda-item")
        .toggleClass("desligado", !!naturezasDesligadas[nat.chave])
        .attr("title", nat.dica + " — clique para mostrar/ocultar")
        .on("click", function () {
          naturezasDesligadas[nat.chave] = !naturezasDesligadas[nat.chave];
          atualizar();
        });
      $("<span>").addClass("mesa-legenda-linha").css("background", nat.tracejada
        ? "repeating-linear-gradient(90deg, " + nat.cor + " 0 3px, transparent 3px 6px)"
        : nat.cor).appendTo($item);
      $("<span>").text(nat.rotulo + " (" + contagem[nat.chave] + ")").appendTo($item);
      $alvo.append($item);
    });

    if (temSugerida) {
      $("<span>").addClass("mesa-legenda-item mesa-legenda-info")
        .attr("title", "Relação proposta pela IA que ainda não foi confirmada nem descartada. A cor continua sendo a da natureza.")
        .append($("<span>").addClass("mesa-legenda-linha tracejada-longa"))
        .append($("<span>").text("linha tracejada = sugestão da IA"))
        .appendTo($alvo);
    }
  }

  // Legenda das comunidades: sem ela a bolinha colorida no canto do nó é um enigma — dá pra ver
  // que dois nós são do mesmo grupo, mas não que grupo é esse. Cada comunidade é nomeada pelo
  // membro MAIS CONECTADO dela, que é quem o investigador reconhece ("o grupo do HÉRCULES").
  function renderLegendaComunidades() {
    var $alvo = $("#mesa-legenda-comunidades").empty();
    var grupos = {};
    nos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined) return;
      (grupos[c] = grupos[c] || []).push(n);
    });

    var lista = Object.keys(grupos).map(function (c) {
      var membros = grupos[c];
      var chefe = membros.slice().sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); })[0];
      return { indice: parseInt(c, 10), membros: membros, chefe: chefe };
    }).sort(function (a, b) { return b.membros.length - a.membros.length; });

    // Um grupo só não é informação — a não ser que seja um grupo isolado a mando do usuário
    // (comunidadeFocada), caso em que a legenda precisa continuar visível para poder desfazer.
    if (lista.length < 2 && comunidadeFocada === null) return;

    // As cores se repetem de CORES_COMUNIDADE em CORES_COMUNIDADE — listar mais que isso
    // colocaria duas linhas da MESMA cor na legenda, que é justamente o que ela evita.
    var cabem = lista.slice(0, CORES_COMUNIDADE.length);
    var resto = lista.slice(CORES_COMUNIDADE.length);

    $("<span>").addClass("mesa-legenda-titulo").text("Comunidades:").appendTo($alvo);

    // Comunidade isolada na tela: um botão para voltar a todas, sempre visível enquanto ativa.
    if (comunidadeFocada !== null) {
      $("<button>").attr("type", "button").addClass("mesa-legenda-item mesa-comunidade-btn")
        .attr("title", "Mostra todas as comunidades de novo")
        .text("Todas as comunidades")
        .on("click", function () { comunidadeFocada = null; atualizar(); })
        .appendTo($alvo);
    }

    cabem.forEach(function (g) {
      var focoAqui = comunidadeFocada === g.indice;
      var $item = $("<button>").attr("type", "button")
        .addClass("mesa-legenda-item mesa-comunidade-btn")
        .toggleClass("ativo", focoAqui)
        .attr("aria-pressed", focoAqui ? "true" : "false")
        .attr("title", g.membros.length + " nó(s) neste grupo — ex.: "
          + g.membros.slice(0, 6).map(function (n) { return n.rotulo; }).join(", ")
          + " · clique para isolar este grupo no desenho")
        .on("click", function () {
          comunidadeFocada = focoAqui ? null : g.indice;
          atualizar();
        });
      $("<span>").addClass("mesa-legenda-bolinha")
        .css("background", CORES_COMUNIDADE[g.indice % CORES_COMUNIDADE.length]).appendTo($item);
      $("<span>").text(truncar(g.chefe ? g.chefe.rotulo : "grupo " + (g.indice + 1), 28)
        + " (" + g.membros.length + ")").appendTo($item);
      $alvo.append($item);
    });

    if (resto.length) {
      var soltos = resto.reduce(function (t, g) { return t + g.membros.length; }, 0);
      $("<span>").addClass("mesa-legenda-item mesa-legenda-info")
        .attr("title", "Grupos pequenos demais para caber na legenda; a cor da bolinha deles se repete.")
        .text("+" + resto.length + " grupos menores (" + soltos + " nós)").appendTo($alvo);
    }
  }

  function aplicarViewBox() {
    svg.setAttribute("viewBox", vb.x + " " + vb.y + " " + vb.w + " " + vb.h);
    atualizarModoRotulos();
    agendarMinimapa();
  }

  // Enquadra o desenho inteiro depois de um layout novo. Sem isso, espalhar mais os nós
  // (ver areaLayout) jogaria metade do grafo pra fora da moldura de 900x560.
  function enquadrarConteudo() {
    if (!nos.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nos.forEach(function (n) {
      if (n.x == null || n.y == null) return;
      var r = raioNo(n) + 26;   // folga pro rótulo, que fica embaixo do nó
      minX = Math.min(minX, n.x - r); maxX = Math.max(maxX, n.x + r);
      minY = Math.min(minY, n.y - r); maxY = Math.max(maxY, n.y + r);
    });
    if (minX === Infinity) return;
    var largura = Math.max(maxX - minX, 1), altura = Math.max(maxY - minY, 1);
    // mantém a proporção da moldura pra o preserveAspectRatio não cortar nada
    var proporcao = LARGURA / ALTURA;
    if (largura / altura < proporcao) largura = altura * proporcao;
    else altura = largura / proporcao;
    vb = {
      x: (minX + maxX) / 2 - largura / 2,
      y: (minY + maxY) / 2 - altura / 2,
      w: largura, h: altura
    };
    aplicarViewBox();
  }

  // ---------- minimapa ----------
  // Num desenho de milhares de nós é fácil se perder ao panear. O minimapa mostra os nós
  // amostrados (até ~800 bolinhas) e o retângulo da área visível; clicar/arrastar nele move o
  // quadro. Fica escondido em desenhos pequenos (não agrega nada) — ver #mesa-minimapa no CSS.
  function minimapaCanvas() { return document.getElementById("mesa-minimapa"); }

  function desenharMinimapa() {
    minimapaRaf = null;
    var cv = minimapaCanvas();
    if (!cv || !minimapaAtivo || cv.offsetParent === null) { return; }
    var ctx = cv.getContext("2d");
    if (!ctx) return;
    var W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    // desenho pequeno não tem para onde se perder — esconde a caixinha em vez de mostrar vazia
    if (nos.length < 25) { cv.classList.add("mesa-minimapa-vazio"); minimapaTela = null; return; }
    cv.classList.remove("mesa-minimapa-vazio");
    if (!nos.length) { minimapaTela = null; return; }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nos.forEach(function (n) {
      if (n.x == null || n.y == null) return;
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    });
    if (minX === Infinity) { minimapaTela = null; return; }
    var pad = 6;
    var gw = Math.max(maxX - minX, 1), gh = Math.max(maxY - minY, 1);
    var escala = Math.min((W - pad * 2) / gw, (H - pad * 2) / gh);
    minimapaTela = { minX: minX, minY: minY, escala: escala, pad: pad };
    var passo = Math.max(1, Math.ceil(nos.length / 800));
    for (var i = 0; i < nos.length; i += passo) {
      var n = nos[i];
      if (n.x == null || n.y == null) continue;
      ctx.fillStyle = corTipo(n.tipo);
      ctx.beginPath();
      ctx.arc(pad + (n.x - minX) * escala, pad + (n.y - minY) * escala, 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
    var accent = (getComputedStyle(document.documentElement).getPropertyValue("--accent") || "").trim() || "#8285ff";
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(pad + (vb.x - minX) * escala, pad + (vb.y - minY) * escala, vb.w * escala, vb.h * escala);
  }

  var minimapaRaf = null;
  function agendarMinimapa() {
    if (minimapaRaf || !minimapaAtivo) return;
    minimapaRaf = requestAnimationFrame(desenharMinimapa);
  }

  // Converte um ponto da tela (dentro do canvas) na coordenada do grafo que ele representa.
  function pontoGrafoNoMinimapa(evt) {
    var cv = minimapaCanvas();
    if (!cv || !minimapaTela) return null;
    var r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var cx = (evt.clientX - r.left) * (cv.width / r.width);
    var cy = (evt.clientY - r.top) * (cv.height / r.height);
    return {
      x: (cx - minimapaTela.pad) / minimapaTela.escala + minimapaTela.minX,
      y: (cy - minimapaTela.pad) / minimapaTela.escala + minimapaTela.minY
    };
  }

  function iniciarMinimapa() {
    var cv = minimapaCanvas();
    if (!cv) return;
    var arrastando = false;
    function centralizar(evt) {
      var p = pontoGrafoNoMinimapa(evt);
      if (!p) return;
      vb.x = p.x - vb.w / 2;
      vb.y = p.y - vb.h / 2;
      aplicarViewBox();
    }
    cv.addEventListener("pointerdown", function (evt) {
      arrastando = true;
      if (cv.setPointerCapture) { try { cv.setPointerCapture(evt.pointerId); } catch (e) {} }
      centralizar(evt);
    });
    cv.addEventListener("pointermove", function (evt) { if (arrastando) centralizar(evt); });
    cv.addEventListener("pointerup", function () { arrastando = false; });
    cv.addEventListener("pointercancel", function () { arrastando = false; });
  }

  // O supernó do agrupamento fuzzy se desenha com a silhueta da camada que ele soma:
  // grupo de canônicas vira losango (classe de canônica); grupo de achado/pessoa de
  // documento vira círculo como os nós doc — senão um grupo verde de "Art. 121" pareceria
  // canônica no desenho e a forma mentiria a camada.
  function ehNoCanonico(n) {
    if (n.kind === "canonica") return true;
    if (n.kind !== "fuzzy") return false;
    return !(n.tipo === "doc_achado" || n.tipo === "doc_pessoa" || n.tipo === "doc_documento");
  }

  function raioNo(n) {
    if (n.kind === "cluster") {
      // tamanho reflete quanta gente está agrupada ali dentro, não só o grau (que é sempre 1 —
      // o cluster só se liga ao hub) — senão um "+80" ficaria do mesmo tamanho que um "+3".
      return Math.min(12 + 2.5 * Math.sqrt(n.membros.length), 26);
    }
    var r = 10 + 2 * Math.sqrt(n.grau || 0); // base 10 pra caber o ícone do tipo
    r = Math.min(r, ehNoCanonico(n) ? 24 : 20);
    // nó com foto tem um piso maior: abaixo disso o rosto vira mancha e não identifica ninguém
    return n.imagem ? Math.max(r, RAIO_MINIMO_FOTO) : r;
  }

  var RAIO_MINIMO_FOTO = 22;

  // Raio efetivo da silhueta numa direção: círculo é r sempre; no losango (o retângulo girado
  // 45° das canônicas) a borda obedece |x|+|y| = r, o que dá r/(|cos|+|sin|) — mais perto nos
  // eixos, mais longe nas diagonais. É o que permite aparar a aresta NA BORDA do nó em vez de
  // no centro, deixando a ponta da seta visível e a linha por fora da foto.
  function raioNaDirecao(n, ang) {
    var r = raioNo(n);
    if (!ehNoCanonico(n)) return r;
    return r / (Math.abs(Math.cos(ang)) + Math.abs(Math.sin(ang)));
  }

  // A silhueta do nó: círculo na entidade-comentário, losango (quadrado girado) na canônica — a
  // forma é o que distingue as duas camadas no desenho. Virou função porque o nó com foto precisa
  // dela três vezes: o preenchimento por baixo, o recorte da imagem e o anel da cor do tipo.
  function silhuetaNo(n, r) {
    var forma = ehNoCanonico(n)
      ? el("rect", { width: r * 2, height: r * 2, rx: 5 })
      : el("circle", { r: r });
    posicionarSilhueta(forma, n, r);
    return forma;
  }

  function posicionarSilhueta(forma, n, r) {
    if (ehNoCanonico(n)) {
      forma.setAttribute("x", n.x - r);
      forma.setAttribute("y", n.y - r);
      forma.setAttribute("transform", "rotate(45 " + n.x + " " + n.y + ")");
    } else {
      forma.setAttribute("cx", n.x);
      forma.setAttribute("cy", n.y);
    }
  }

  // A cor do nó é SEMPRE a do tipo — é o que a legenda promete, e trocar o preenchimento pela
  // cor da comunidade fazia a legenda mentir (um nó rosa de "Pessoa" virava verde sem que nada
  // na tela explicasse). Comunidade é outra dimensão do mesmo nó, então vira marca própria:
  // uma bolinha no canto superior direito (ver corComunidade/badgeComunidade).
  function corNo(n) {
    return corTipo(n.tipo);
  }

  function corComunidade(n) {
    if (comunidadePorNo[n.id] === undefined) return null;
    return CORES_COMUNIDADE[comunidadePorNo[n.id] % CORES_COMUNIDADE.length];
  }

  // Posição da bolinha: a 45° do centro e a uma distância de um raio. Serve para as duas
  // formas — no círculo isso cai na borda, e no losango (retângulo girado 45°) cai no meio da
  // aresta superior direita, que é o mesmo ponto visual.
  function posBadge(n) {
    var r = raioNo(n);
    return { x: n.x + r * 0.707, y: n.y - r * 0.707, r: Math.max(3.5, Math.min(r * 0.32, 6)) };
  }

  function calcularPosicaoPino(n, r) {
    return { x: n.x, y: n.y - r - 1, r: Math.max(3.4, Math.min(4.8, r * 0.25)) };
  }

  function criarPinoTridimensional(n, r) {
    var posicaoPino = calcularPosicaoPino(n, r);
    var grupoPosicao = el("g", {
      class: "mesa-no-pino-posicao",
      transform: "translate(" + posicaoPino.x + " " + posicaoPino.y + ")"
    });
    var grupoPino = el("g", { class: "mesa-no-pino-3d" });
    grupoPino.appendChild(el("ellipse", { cx: 1.1, cy: 4.8, rx: posicaoPino.r * 0.72, ry: 1.5, class: "mesa-no-pino-impacto" }));
    grupoPino.appendChild(el("line", { x1: 0.8, y1: 0.5, x2: 0.8, y2: 6.2, class: "mesa-no-pino-agulha" }));
    grupoPino.appendChild(el("ellipse", { cx: 0.8, cy: 0.8, rx: posicaoPino.r * 0.92, ry: posicaoPino.r * 0.4, class: "mesa-no-pino-base" }));
    grupoPino.appendChild(el("path", {
      d: "M" + (-posicaoPino.r * 0.42) + ",0.5 L" + (-posicaoPino.r * 0.28) + "," + (-posicaoPino.r * 1.65) +
        " Q0," + (-posicaoPino.r * 2) + " " + (posicaoPino.r * 0.32) + "," + (-posicaoPino.r * 1.62) +
        " L" + (posicaoPino.r * 0.46) + ",0.5 Z",
      class: "mesa-no-pino-corpo"
    }));
    grupoPino.appendChild(el("ellipse", { cx: 0, cy: -posicaoPino.r * 1.72, rx: posicaoPino.r * 1.2, ry: posicaoPino.r * 0.62, class: "mesa-no-pino-cabeca" }));
    grupoPino.appendChild(el("ellipse", { cx: -posicaoPino.r * 0.32, cy: -posicaoPino.r * 1.92, rx: posicaoPino.r * 0.34, ry: posicaoPino.r * 0.16, class: "mesa-no-pino-brilho" }));
    grupoPosicao.appendChild(grupoPino);
    return grupoPosicao;
  }

  function posicionarPinoTridimensional(pino, n, r) {
    if (!pino) return;
    var posicaoPino = calcularPosicaoPino(n, r);
    pino.setAttribute("transform", "translate(" + posicaoPino.x + " " + posicaoPino.y + ")");
  }

  // ---------- rótulos sob demanda ----------
  // Passando de LIMITE_ROTULOS nós, os rótulos só aparecem quando o investigador se aproxima
  // (zoom além de ZOOM_ROTULOS) ou aponta um nó. O desenho de longe fica limpo e serve pra
  // enxergar a FORMA da rede; o nome é coisa de perto.

  var LIMITE_ROTULOS = 30;
  var ZOOM_ROTULOS = 0.55;    // fração da área do layout visível na moldura
  var mostrarTodosRotulos = false;   // caixa "Mostrar todos os rótulos"

  function rotulosDevemSumir() {
    if (mostrarTodosRotulos || termoBusca) return false;   // buscando, o nome é o que importa
    if (nos.length <= LIMITE_ROTULOS) return false;
    return vb.w > areaLayout(nos.length).largura * ZOOM_ROTULOS;
  }

  function atualizarModoRotulos() {
    svg.classList.toggle("mesa-grafo-rotulos-ocultos", rotulosDevemSumir());
  }

  /* ---------- PNG do grafo ----------
     Print de tela não serve para despacho nem relatório: corta o que passou da moldura, sai na
     resolução do monitor e deixa a legenda de fora — e é a legenda que diz o que cada cor
     significa. A figura sai enquadrada no conteúdo INTEIRO, em 2x, com título, contagem e a
     legenda redesenhada por baixo (ver js/utils/exportar_png.js).

     Os rótulos entram à força: eles somem sozinhos quando o desenho está "de longe" (ver
     rotulosDevemSumir), que é uma regra de legibilidade da TELA — no papel, um grafo sem nome
     nenhum não diz nada. O resto do estado vai como está: se há foco aceso ou busca destacando
     alguém, a imagem sai igual ao que se vê. */
  function exportarPng() {
    if (!window.ExportarPNG) { Toast.aviso("Exportação de PNG não disponível nesta tela."); return; }
    if (!nos.length) { Toast.aviso("Grafo vazio: não há o que exportar."); return; }

    var numero = $.trim($(".crumbs a[href*='investigacao.html?inquerito=']").last().text() || "");
    var sub = [nos.length.toLocaleString("pt-BR") + " nós · " +
               arestas.length.toLocaleString("pt-BR") + " ligações"];
    var periodo = $.trim($("#mesa-tempo-filtro-status").not(".hidden").text() || "");
    if (periodo) sub.push(periodo);
    if (termoBusca) sub.push("Busca: “" + termoBusca + "”");
    sub.push("Exportado em " + new Date().toLocaleString("pt-BR"));

    return window.ExportarPNG.doGrafo(svg, {
      prefixo: "mesa-inquerito",
      titulo: (temaOrbitAtivo() ? "Quadro de Investigação" : "Mesa de Inquérito") + (numero ? " — " + numero : ""),
      subtitulo: sub,
      legendas: ["#mesa-legenda", "#mesa-legenda-relacoes", "#mesa-legenda-comunidades"],
      antes: function () {
        var ocultos = svg.classList.contains("mesa-grafo-rotulos-ocultos");
        svg.classList.remove("mesa-grafo-rotulos-ocultos");
        // a deduplicação de rótulos sobrepostos é regra da TELA: no papel o espaço é o do
        // desenho inteiro, e um nome a menos nunca ajudou um relatório
        var sobrepostos = Array.prototype.slice.call(svg.querySelectorAll(".rotulo-sobreposto"));
        sobrepostos.forEach(function (el) { el.classList.remove("rotulo-sobreposto"); });
        return function () {
          if (ocultos) svg.classList.add("mesa-grafo-rotulos-ocultos");
          sobrepostos.forEach(function (el) { el.classList.add("rotulo-sobreposto"); });
        };
      }
    });
  }

  $("#mesa-exportar-png").on("click", function () {
    var $b = $(this).prop("disabled", true);
    Promise.resolve(exportarPng())
      .catch(function () { /* quem falhou já avisou */ })
      .then(function () { $b.prop("disabled", false); });
  });

  /* Exportação para o i2 (CSV / XLSX / ANX — ver js/utils/exportar_i2.js).

     Vai o grafo FILTRADO INTEIRO, não o desenhado. O teto de nós e o agrupamento de folhas
     ("+N contrapartes") existem porque o layout por força trava o navegador e porque uma estrela
     de 300 pontas é ilegível — problemas de DESENHO, que o i2 não tem. Exportar o nó "+N" seria
     pior que cortá-lo: viraria uma entidade sem nome e sem documento na carta, no lugar das 300
     pessoas de verdade que ele esconde.

     O tipo do nó vira o tipo da entidade no i2, e a NATUREZA da relação (conversa, ligação,
     SIMBA, RIF, PIX…) vira o tipo do vínculo — é ela que faz a carta responder "por que estão
     ligados", e não só "estão ligados". */
  function montarRecorteI2() {
    var mostrarSugeridas = $("#filtro-sugeridas").prop("checked");
    var candidatos = nosFiltradosBase();

    var exportados = {};
    candidatos.forEach(function (n) { exportados[n.id] = true; });

    var vinculos = todasArestas.filter(function (a) {
      if (!exportados[a.a] || !exportados[a.b]) return false;
      return !(a.sugerida && !mostrarSugeridas);
    }).map(function (a) {
      var natureza = naturezaAresta(a);
      var d = a.dado || {};
      return {
        de: a.a, para: a.b,
        tipo: natureza.rotulo,
        rotulo: a.tipo || natureza.rotulo,
        // Evidência liga a canônica ao registro que a comprova: não há "de quem para quem" nisso,
        // então a carta sai sem seta em vez de com uma seta inventada.
        simetrico: a.kind === "evidencia",
        resumo: natureza.rotulo + (a.sugerida ? " (sugestão da IA, ainda não confirmada)" : ""),
        atributos: {
          // TX_DETALHE é o que responde "pagou o QUÊ / conversou QUANTO": a identificação de
          // pessoas grava aqui a contagem e o período ("42 mensagens entre 01/01 e 20/03",
          // "R$ 45.320,00 em 12 lançamentos, entre ..."). Sem ele a carta ficava com o rótulo
          // seco — "pagou a (RIF)" — e o investigador tinha de voltar ao sistema para saber
          // quanto. Relação antiga, gravada antes de a origem preencher o campo, sai sem ele:
          // é preciso rodar a identificação de pessoas de novo para o detalhe aparecer.
          "Detalhe": d.tx_detalhe || "",
          "Natureza": natureza.rotulo,
          "Tipo da relação": a.tipo || "",
          "Origem do vínculo": a.sugerida ? "Sugestão da IA (não confirmada)"
                             : d.in_automatico ? "Automática (identificação de pessoas)"
                             : a.kind === "evidencia" ? "Evidência do registro marcado"
                             : "Manual",
          "Justificativa": d.tx_justificativa || "",
          "Criado por": d.criado_por || "",
          "Criado em": d.dt_criacao ? formatarDataHora(d.dt_criacao) : ""
        }
      };
    });

    // Peso = grau dentro do recorte exportado. É o que o i2 usa para dimensionar a entidade, e o
    // número de ligações é a leitura de importância que sobrevive à mudança de ferramenta.
    var grau = {};
    vinculos.forEach(function (v) {
      grau[v.de] = (grau[v.de] || 0) + 1;
      grau[v.para] = (grau[v.para] || 0) + 1;
    });

    return {
      prefixo: "mesa-inquerito",
      entidades: candidatos.map(function (n) {
        var d = n.dado || {};
        return {
          chave: n.id,
          rotulo: n.rotulo,
          tipo: ROTULOS_TIPO[n.tipo] || n.tipo || "Entidade",
          peso: grau[n.id] || 0,
          // A canônica é a PESSOA (com CPF/telefone em nu_chave); a entidade-comentário é o
          // REGISTRO marcado na mesa. Os dois viram nó no i2, e sem estes campos ficavam
          // indistinguíveis um do outro na carta.
          atributos: n.kind === "doc" ? {
            "Camada": "Documento genérico (análise por IA)",
            "Tipo": ROTULOS_TIPO[n.tipo] || n.tipo || "",
            "Papel": d.tp_papel || "",
            "Vulgo": d.no_vulgo || "",
            "CPF/CNPJ": d.nu_cpf_cnpj || "",
            "Tipo do achado": d.tp_achado || "",
            "Valor": d.tx_valor || "",
            "Menções": d.qt_mencoes || "",
            "Ocorrências": d.qt_ocorrencias || "",
            "Documentos": d.qt_documentos || "",
            "Resumo do documento": d.tx_resumo || "",
            "Ligações no recorte": grau[n.id] || 0
          } : n.kind === "canonica" ? {
            "Camada": "Entidade canônica",
            "Tipo": ROTULOS_TIPO[n.tipo] || n.tipo || "",
            "Identificador": d.nu_chave || "",
            "Observação": d.tx_observacao || "",
            "Evidências": d.qt_evidencias != null ? d.qt_evidencias : "",
            "Ligações no recorte": grau[n.id] || 0,
            "Origem": d.in_automatico ? "Identificada automaticamente" : "Criada à mão",
            "Criado por": d.criado_por || "",
            "Criado em": d.dt_criacao ? formatarDataHora(d.dt_criacao) : ""
          } : {
            "Camada": "Registro marcado na mesa",
            "Tipo do registro": ROTULOS_TIPO[n.tipo] || n.tipo || "",
            "Comentário": d.tx_comentario || "",
            "Revisão": d.cd_revisao || "",
            "Justificativa": d.tx_justificativa || "",
            "Ligações no recorte": grau[n.id] || 0,
            "Origem": d.in_automatico ? "Marcada pela IA" : "Marcada à mão",
            "Criado por": d.criado_por || "",
            "Criado em": d.dt_criacao ? formatarDataHora(d.dt_criacao) : ""
          }
        };
      }),
      vinculos: vinculos
    };
  }

  ExportarI2.ligarBotoes(montarRecorteI2);

  // Num nó-cabeça a vizinhança é o grafo quase inteiro (o dono do aparelho conversa com todo
  // mundo): revelar os nomes dela recriaria exatamente a poluição que se quis tirar — 200
  // rótulos de uma vez, medido no inquérito 6. Passando deste tamanho, mostra só o nome do
  // próprio nó, que é o que o investigador precisa para saber onde está.
  var LIMITE_ROTULOS_VIZINHANCA = 12;

  // União das vizinhanças de um conjunto de nós (por id): eles próprios, os vizinhos diretos e
  // as arestas que os tocam. Com um id é a vizinhança de sempre; com vários é o que a
  // multisseleção (Shift+clique) precisa deixar em foco. `tamanho` conta só os vizinhos, sem os
  // escolhidos — é o número que decide se cabe mostrar os nomes (LIMITE_ROTULOS_VIZINHANCA).
  function vizinhancaDe(ids) {
    var alvo = {}, vizinhos = {}, ligacoes = [];
    ids.forEach(function (id) { alvo[id] = true; vizinhos[id] = true; });
    arestas.forEach(function (a) {
      if (!alvo[a.a] && !alvo[a.b]) return;
      vizinhos[a.a] = true;
      vizinhos[a.b] = true;
      ligacoes.push(a);
    });
    return {
      nos: vizinhos, arestas: ligacoes, alvo: alvo,
      tamanho: Object.keys(vizinhos).length - Object.keys(alvo).length
    };
  }

  function vizinhosDe(n) {
    return vizinhancaDe([n.id]);
  }

  // Revela (ou esconde) o rótulo do nó apontado, e o dos vizinhos quando são poucos — ler um
  // nome solto no meio do grafo diz pouco; o que informa é "quem é este e com quem ele fala".
  function realcarRotulosDoNo(n, ligado) {
    // Tirar o mouse de cima de um nó não pode apagar os nomes que o FOCO acendeu: refaz o foco
    // em vez de desligar tudo (sem isto, passar o mouse pela seleção ia apagando-a aos poucos).
    if (!ligado && focados.length) { aplicarFoco(); return; }
    var viz = vizinhosDe(n);
    var comVizinhanca = ligado && viz.tamanho <= LIMITE_ROTULOS_VIZINHANCA;
    if (n._rotulo) n._rotulo.classList.toggle("rotulo-visivel", ligado);
    viz.arestas.forEach(function (a) {
      if (a._rotulo) a._rotulo.classList.toggle("rotulo-visivel", comVizinhanca);
    });
    nos.forEach(function (outro) {
      if (outro !== n && viz.nos[outro.id] && outro._rotulo) {
        outro._rotulo.classList.toggle("rotulo-visivel", comVizinhanca);
      }
    });
  }

  function casaBusca(n) {
    if (!termoBusca) return true;
    var alvo = (n.rotulo + " " + (n.dado.tx_comentario || "") + " " + (n.dado.tx_observacao || "") +
      " " + (n.dado.tx_valor || "") + " " + (n.dado.tx_contexto || "") +
      " " + (n.dado.no_vulgo || "") + " " + (n.dado.tx_variantes || "")).toLowerCase();
    return alvo.indexOf(termoBusca) !== -1;
  }

  function definicoesVisuaisRealistas() {
    if (!modoRealistaAtivo()) return "";
    return '<linearGradient id="mesa-no-brilho" x1="0" y1="0" x2="1" y2="1">' +
        '<stop offset="0" stop-color="#ffffff" stop-opacity="0.64"></stop>' +
        '<stop offset="0.34" stop-color="#ffffff" stop-opacity="0.08"></stop>' +
        '<stop offset="0.7" stop-color="#020817" stop-opacity="0.04"></stop>' +
        '<stop offset="1" stop-color="#020817" stop-opacity="0.42"></stop>' +
      '</linearGradient>' +
      '<linearGradient id="mesa-pino-corpo-volume" x1="0" y1="0" x2="1" y2="1">' +
        '<stop offset="0" stop-color="#ff8790"></stop>' +
        '<stop offset="0.42" stop-color="#e73143"></stop>' +
        '<stop offset="1" stop-color="#7c0a1d"></stop>' +
      '</linearGradient>' +
      '<radialGradient id="mesa-pino-cabeca-volume" cx="31%" cy="23%" r="75%">' +
        '<stop offset="0" stop-color="#ffe0db"></stop>' +
        '<stop offset="0.24" stop-color="#ff6872"></stop>' +
        '<stop offset="0.68" stop-color="#c3152f"></stop>' +
        '<stop offset="1" stop-color="#620817"></stop>' +
      '</radialGradient>' +
      '<linearGradient id="mesa-agulha-volume" x1="0" y1="0" x2="1" y2="0">' +
        '<stop offset="0" stop-color="#4e4e53"></stop>' +
        '<stop offset="0.48" stop-color="#f4f6f8"></stop>' +
        '<stop offset="1" stop-color="#777980"></stop>' +
      '</linearGradient>';
  }

  function renderGrafo() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    aplicarViewBox();

    if (!todosNos.length) {
      $vazio.removeClass("hidden");
      return;
    }
    $vazio.addClass("hidden");

    var desenhoRealista = modoRealistaAtivo();
    var defs = el("defs", {});
    var simbolos = Object.keys(ICONES_TIPO).map(function (tipo) {
      return '<symbol id="mesa-icone-' + tipo + '" viewBox="0 0 24 24">' + ICONES_TIPO[tipo] + "</symbol>";
    }).join("");
    // Uma ponta de seta por natureza: o marcador vive no <defs> e NÃO herda nada da aresta que
    // o usa, então a cor tem de estar nele — uma seta cinza no fim de uma linha verde é
    // exatamente o tipo de detalhe que faz a legenda parecer mentira.
    var setas = NATUREZAS_RELACAO.map(function (nat) {
      return '<marker id="mesa-seta-' + nat.chave + '" markerWidth="7" markerHeight="7" refX="14" refY="3" ' +
        'orient="auto" markerUnits="userSpaceOnUse">' +
        '<path d="M0,0 L6,3 L0,6 Z" class="mesa-aresta-ponta-path" style="--mesa-aresta-cor:' + nat.cor + '"></path>' +
        "</marker>";
    }).join("");
    defs.innerHTML = setas + simbolos + definicoesVisuaisRealistas();
    // P0-5: monta tudo num fragmento e insere de uma vez — N appends no SVG vivo
    // forçavam N reflows; com 6k nós isso é a diferença entre abrir e travar.
    var frag = document.createDocumentFragment();
    frag.appendChild(defs);

    // A3: em desenho grande, cada <title> é um nó DOM a mais (6k nós = 6k títulos que
    // ninguém lê de longe) e 2 listeners por nó são 12k handlers — o hover some acima de
    // 800 e os títulos acima de 500; clique, busca e gaveta continuam funcionando.
    var detalhar = nos.length <= 500;
    var comHover = nos.length <= 800;

    // Elos críticos do desenho (só quando o destaque está ligado — o Tarjan é O(N+E) e não
    // precisa rodar num grafo em que ninguém pediu os elos). Ver calcularElosCriticos.
    var elosVis = destacarElos ? obterElosCriticos() : null;

    // Arestas paralelas (mesmo par de nós) se afastam numa curva pra não se sobreporem. O
    // afastamento é do PONTO DE CONTROLE, e a curva quadrática passa na metade dele: por isso
    // ESPACO_PARALELAS é o dobro da distância que as linhas realmente ganham no meio.
    var porPar = {};
    arestas.forEach(function (a) {
      var chave = a.a < a.b ? a.a + "|" + a.b : a.b + "|" + a.a;
      (porPar[chave] = porPar[chave] || []).push(a);
    });

    arestas.forEach(function (a) {
      var chave = a.a < a.b ? a.a + "|" + a.b : a.b + "|" + a.a;
      var grupo = porPar[chave];
      var idx = grupo.indexOf(a);
      a._desloc = (idx - (grupo.length - 1) / 2) * ESPACO_PARALELAS;
      a._ordem = idx;
      a._paralelas = grupo.length;

      var nat = naturezaAresta(a);
      var caminho = el("path", {
        class: "mesa-aresta" + (a.sugerida ? " sugerida" : "") + (a.kind === "evidencia" ? " evidencia" : "")
          + (a.tipo === RELACAO_DESTAQUE ? " destaque" : "")
          + (elosVis && elosVis.pontes[chave] ? " ponte" : ""),
        fill: "none",
        style: "--mesa-aresta-cor:" + nat.cor,
        "marker-end": a.kind === "evidencia" ? "" : "url(#mesa-seta-" + nat.chave + ")"
      });
      // Par com várias relações (conversa + PIX + SIMBA entre os mesmos dois) ganha traço um
      // pouco mais grosso: de longe, onde o rótulo some, a espessura é o que diz "aqui tem
      // muito vínculo" sem precisar contar curvas.
      if (grupo.length > 1) {
        caminho.style.strokeWidth = Math.min(1.3 + 0.4 * (grupo.length - 1), 3) + "px";
      }
      caminho.__aresta = a;
      // com o rótulo escondido (grafo grande, de longe) a cor é a única pista do que é a linha;
      // o title devolve o nome por extenso sem precisar aproximar (só em desenho pequeno: A3)
      if (detalhar) {
        var tituloAresta = document.createElementNS(SVG_NS, "title");
        tituloAresta.textContent = (a.tipo ? a.tipo + " · " : "") + nat.rotulo;
        caminho.appendChild(tituloAresta);
      }
      frag.appendChild(caminho);

      var rotulo = el("text", { class: "mesa-aresta-rotulo" + (a.tipo === RELACAO_DESTAQUE ? " destaque" : "") });
      rotulo.textContent = truncar(a.tipo, 24);
      frag.appendChild(rotulo);

      // Trilha de clique da aresta: um path largo e invisível, APARADO nas bordas dos nós
      // (mesmo `d` da linha), para que passar perto de uma ligação abra o detalhe dela sem roubar
      // o clique dos nós por cima. Entra antes dos nós → os nós ficam por cima, mandam no clique.
      var hit = el("path", { class: "mesa-aresta-hit", fill: "none" });
      hit.__aresta = a;
      hit.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
      hit.addEventListener("click", function (ev) { ev.stopPropagation(); abrirGavetaAresta(a); });
      frag.appendChild(hit);

      a._el = caminho;
      a._hit = hit;
      a._rotulo = rotulo;
      posicionarAresta(a);
    });

    nos.forEach(function (n, indiceNo) {
      var grupo = el("g", { class: "mesa-no-grupo" });
      // hub: os nós mais conectados do desenho ganham anel mais firme — são os "protagonistas"
      // da rede, e é neles que o olho tem de cair primeiro, sem rótulo e sem zoom
      if ((n.grau || 0) >= 10) grupo.classList.add("no-hub");
      // elo crítico (ponto de articulação): removido, parte o desenho ao meio — anel próprio.
      if (elosVis && elosVis.articulacoes[n.id]) grupo.classList.add("no-articulacao");
      if (desenhoRealista) grupo.style.setProperty("--mesa-pin-delay", Math.min(indiceNo * 24, 420) + "ms");
      var destaque = casaBusca(n);
      if (!destaque) grupo.setAttribute("opacity", "0.15");

      var sugerida = n.kind === "entidade" && n.dado.cd_revisao === "sugerida";
      var r = raioNo(n);
      var forma = silhuetaNo(n, r);
      forma.setAttribute("class", ehNoCanonico(n)
        ? "mesa-no canonica"
        : "mesa-no" + (sugerida ? " sugerida" : "") + (n.dado.in_automatico ? " automatico" : ""));
      forma.setAttribute("fill", corNo(n));
      if (termoBusca && destaque) forma.classList.add("achado");
      forma.__no = n;

      if (detalhar) {
        var titulo = document.createElementNS(SVG_NS, "title");
        titulo.textContent = (n.rotulo || "").slice(0, 200) +
          (n.kind === "fuzzy" ? " — grupo de " + (n.membros || []).length + " itens de nome parecido (só na tela)" : "");
        forma.appendChild(titulo);
      }

      var rotulo = el("text", { x: n.x, y: n.y + r + 12, class: "mesa-no-rotulo" });
      rotulo.textContent = truncar(n.rotulo, 22) + (n.kind === "fuzzy" && n.qtAgrupados ? " (+" + n.qtAgrupados + ")" : "");

      grupo.appendChild(forma);

      // No modo realista, o volume pertence à própria entidade: um brilho discreto preserva a
      // silhueta e o tamanho do nó, sem criar a moldura quadrada que ocupava espaço no quadro.
      var luz = null;
      if (desenhoRealista) {
        luz = silhuetaNo(n, r);
        luz.setAttribute("class", "mesa-no-volume-luz");
        luz.setAttribute("fill", "url(#mesa-no-brilho)");
        grupo.appendChild(luz);
      }

      // Foto do nó: a imagem entra recortada na silhueta (círculo/losango), por cima do
      // preenchimento, e a cor do tipo volta num anel em volta — a legenda promete "cor = tipo" e
      // não pode deixar de valer justo nos nós que mais chamam atenção. O ícone do tipo sai de
      // cena: quem olha um rosto não precisa do bonequinho ao lado dele.
      var foto = fotoDoNo(n);
      var imagem = null, anel = null, recorte = null;
      if (foto) {
        recorte = silhuetaNo(n, r);
        var clip = el("clipPath", { id: "mesa-recorte-" + n.id });
        clip.appendChild(recorte);
        defs.appendChild(clip);
        imagem = el("image", {
          href: foto, x: n.x - r, y: n.y - r, width: r * 2, height: r * 2,
          // "slice" preenche a silhueta e corta a sobra; sem isso um retrato em pé sai deformado
          // para caber no quadrado do nó
          preserveAspectRatio: "xMidYMid slice",
          "clip-path": "url(#mesa-recorte-" + n.id + ")",
          class: "mesa-no-foto"
        });
        grupo.appendChild(imagem);
        anel = silhuetaNo(n, r);
        anel.setAttribute("class", "mesa-no-anel" + (sugerida ? " sugerida" : "")
          + (termoBusca && destaque ? " achado" : ""));
        anel.setAttribute("stroke", corNo(n));
        // fill=none também como atributo, não só no CSS: quem lê o SVG sem a folha de estilo
        // (um rasterizador, o i2) pintaria o miolo do anel de preto por cima da foto
        anel.setAttribute("fill", "none");
        grupo.appendChild(anel);
      }

      var icone = null;
      if (!foto && ICONES_TIPO[n.tipo]) {
        var lado = r * 1.15;
        icone = el("use", {
          href: "#mesa-icone-" + n.tipo,
          x: n.x - lado / 2, y: n.y - lado / 2, width: lado, height: lado,
          class: "mesa-no-icone"
        });
        grupo.appendChild(icone);
      }

      grupo.appendChild(rotulo);

      // marca da comunidade: bolinha no canto superior direito, por cima do nó
      var badge = null;
      var corCom = corComunidade(n);
      if (corCom) {
        var pb = posBadge(n);
        badge = el("circle", { cx: pb.x, cy: pb.y, r: pb.r, class: "mesa-no-badge", fill: corCom });
        var tituloBadge = document.createElementNS(SVG_NS, "title");
        tituloBadge.textContent = "Comunidade " + (comunidadePorNo[n.id] + 1);
        badge.appendChild(tituloBadge);
        grupo.appendChild(badge);
      }

      // A tachinha é formada por cabeça, haste e agulha. O grupo externo guarda a posição e o
      // interno recebe a animação de perfuração, para que arrastar um nó não conflite com ela.
      var pino3d = desenhoRealista ? criarPinoTridimensional(n, r) : null;
      if (pino3d) grupo.appendChild(pino3d);

      frag.appendChild(grupo);

      if (comHover) {
        grupo.addEventListener("mouseenter", function () { realcarRotulosDoNo(n, true); });
        grupo.addEventListener("mouseleave", function () { realcarRotulosDoNo(n, false); });
      }

      n._forma = forma;
      n._icone = icone;
      n._rotulo = rotulo;
      n._badge = badge;
      n._grupo = grupo;
      n._foto = imagem;
      n._pino3d = pino3d;
      // as cópias da silhueta que têm de andar juntas quando o nó é arrastado
      n._silhuetas = [forma, luz, anel, recorte].filter(Boolean);
    });

    svg.appendChild(frag);
    aplicarFoco();
    atualizarModoRotulos();
    resolverColisoesRotulos();
    desenharMinimapa();
  }

  // Rótulos visíveis sem sobreposição: guloso por grau — o rótulo do nó mais conectado é
  // desenhado primeiro, e o que colide com um já aceito ganha .rotulo-sobreposto (opacity 0,
  // mas volta pelo mecanismo de revelação que já existe: hover do nó, busca, foco). A caixa é
  // aproximada (fonte de tamanho fixo); acima de 600 nós o custo não paga o benefício.
  function resolverColisoesRotulos() {
    nos.forEach(function (n) { if (n._rotulo) n._rotulo.classList.remove("rotulo-sobreposto"); });
    if (nos.length > 600) return;
    var CEL = 88;
    var aceitos = {};
    var ordem = nos.slice().sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); });
    ordem.forEach(function (n) {
      if (!n._rotulo || n.x == null) return;
      var r = raioNo(n);
      var w = Math.min((n.rotulo || "").length, 24) * 5.6 + 8;
      var x0 = n.x - w / 2, x1 = n.x + w / 2;
      var y0 = n.y + r + 2, y1 = y0 + 12;
      var cx0 = Math.floor(x0 / CEL), cx1 = Math.floor(x1 / CEL);
      var cy0 = Math.floor(y0 / CEL), cy1 = Math.floor(y1 / CEL);
      var bate = false;
      for (var gx = cx0; gx <= cx1 && !bate; gx++) {
        for (var gy = cy0; gy <= cy1 && !bate; gy++) {
          var cel = aceitos[gx + ":" + gy];
          if (!cel) continue;
          for (var i = 0; i < cel.length; i++) {
            var o = cel[i];
            if (x0 < o.x1 && x1 > o.x0 && y0 < o.y1 && y1 > o.y0) { bate = true; break; }
          }
        }
      }
      if (bate) { n._rotulo.classList.add("rotulo-sobreposto"); return; }
      for (var gx2 = cx0; gx2 <= cx1; gx2++) {
        for (var gy2 = cy0; gy2 <= cy1; gy2++) {
          var k = gx2 + ":" + gy2;
          (aceitos[k] = aceitos[k] || []).push({ x0: x0, y0: y0, x1: x1, y1: y1 });
        }
      }
    });
  }

  function atualizarStatusFuzzy() {
    var $st = $("#fuzzy-status");
    if (!$st.length) return;
    // Retorno visual do "Agrupar entidades": sem ele, marcar e não ver mudança parece
    // "não aplicou" — quando na verdade pode só não haver nome parecido no desenho atual.
    if (!agrupamentoFuzzyAtivo()) { $st.addClass("hidden").text(""); return; }
    if (fuzzyInfo.pulado) {
      $st.removeClass("hidden").text(
        "muitos nós (" + Number(fuzzyInfo.elegiveis || 0).toLocaleString("pt-BR") +
        ") — aplique um recorte para agrupar");
      return;
    }
    $st.removeClass("hidden").text(
      fuzzyInfo.grupos > 0
        ? fuzzyInfo.juntos + " entidades em " + fuzzyInfo.grupos + " grupo(s)"
        : "nenhum nome parecido no desenho atual"
    );
  }

  function atualizar() {
    aplicarFiltros();
    renderLegenda();
    calcularLayoutAsync(function (recalculou) {
      if (cameraPendente) { vb = cameraPendente; cameraPendente = null; aplicarViewBox(); }
      else if (recalculou) enquadrarConteudo();
      renderGrafo();
      atualizarStatusFiltroTempo();
      atualizarStatusFuzzy();
      atualizarBarraIsolamento();
      atualizarListaBusca();
      if (fontesInv) atualizarMetaFontes();
      // A4: as fotos adiadas na abertura descem quando o desenho fica pequeno (recorte)
      if (fotosAdiadas && nos.length <= 500) carregarFotosPendentes();
      // se algum nó em foco saiu do desenho, a gaveta se refaz com quem sobrou (e se fecha
      // quando não sobra ninguém) — senão ela continuaria descrevendo um grafo que não existe
      var visivel = idsVisiveis();
      if (focados.length && focados.some(function (id) { return !visivel[id]; })) abrirGaveta();
    });
  }

  // P1-7: barra de isolamento (breadcrumb do recorte + ações). Aparece quando há foco,
  // isolamento ou caminho ativo — é o que permite voltar ao tudo sem caçar o filtro.
  function atualizarBarraIsolamento() {
    var $bar = $("#mesa-isolamento-bar");
    if (!$bar.length) return;
    var temFoco = focados.length > 0;
    var temRecorte = !!isolamento || !!soCaminhoIds;
    if (!temFoco && !temRecorte) { $bar.addClass("hidden"); return; }
    $bar.removeClass("hidden");
    var partes = [];
    if (soCaminhoIds) {
      partes.push("Caminho entre " + focados.length + " nó(s) (" + nos.length + " no desenho)");
    } else if (isolamento) {
      partes.push("Vizinhança de " + isolamento.raizes.length + " nó(s), " +
        isolamento.saltos + " salto(s) (" + nos.length + " no desenho)");
    } else {
      partes.push(focados.length + " em foco · " + nos.length + " no desenho");
    }
    $("#mesa-isolamento-texto").text(partes.join(" · "));
  }

  function isolarFoco(saltos) {
    if (!focados.length) { Toast.aviso("Clique num nó primeiro para isolar a vizinhança dele."); return; }
    soCaminhoIds = null;
    isolamento = { raizes: focados.slice(), saltos: saltos || 1 };
    atualizar();
  }

  function mostrarSoCaminho() {
    if (focados.length < 2) { Toast.aviso("Selecione 2+ nós (Shift+clique) para ver o caminho entre eles."); return; }
    var ids = {};
    var base = (arestasBaseVisao && arestasBaseVisao.length) ? arestasBaseVisao : arestas;
    for (var i = 0; i < focados.length; i++) {
      for (var j = i + 1; j < focados.length; j++) {
        var cam = bfsCaminho(focados[i], focados[j], base);
        if (cam) cam.forEach(function (id) { ids[id] = true; });
      }
    }
    focados.forEach(function (id) { ids[id] = true; });
    if (!Object.keys(ids).length) { Toast.aviso("Sem caminho entre os nós em foco no desenho atual."); return; }
    isolamento = null;
    soCaminhoIds = ids;
    atualizar();
  }

  function limparRecorte() {
    isolamento = null;
    soCaminhoIds = null;
    filtroDocsIds = null;
    comunidadeFocada = null;
    atualizar();
  }

  // ---------- zoom / pan / arrastar nó ----------

  function pontoSvg(evt) {
    var pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    var ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    var p = pt.matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  }

  // ---------- seleção por área (marquee) ----------
  // Num grafo de milhares de nós, Shift+clique nó a nó para escolher um conjunto é inviável.
  // O botão "Selecionar área" liga um modo em que arrastar no FUNDO desenha um retângulo e, ao
  // soltar, tudo que estiver dentro entra em foco (a mesma gaveta de multisseleção do Shift).
  function criarRetanguloArea(x, y) {
    var r = el("rect", { class: "mesa-marquee", x: x, y: y, width: 0, height: 0 });
    svg.appendChild(r);
    return r;
  }
  function posicionarRetanguloArea(d, x, y) {
    if (!d.el) return;
    d.el.setAttribute("x", Math.min(d.x0, x));
    d.el.setAttribute("y", Math.min(d.y0, y));
    d.el.setAttribute("width", Math.abs(x - d.x0));
    d.el.setAttribute("height", Math.abs(y - d.y0));
  }
  function selecionarNosNaArea(x0, y0, x1, y1) {
    var minx = Math.min(x0, x1), maxx = Math.max(x0, x1);
    var miny = Math.min(y0, y1), maxy = Math.max(y0, y1);
    var vis = idsVisiveis();
    var novos = [];
    nos.forEach(function (n) {
      if (n.x == null || n.y == null || !vis[n.id]) return;
      if (n.x >= minx && n.x <= maxx && n.y >= miny && n.y <= maxy) novos.push(n.id);
    });
    if (!novos.length) { Toast.aviso("Nenhum nó dentro da área."); return; }
    novos.forEach(function (id) { if (focados.indexOf(id) === -1) focados.push(id); });
    abrirGaveta();
  }
  function atualizarBotaoSelecaoArea() {
    var $b = $("#mesa-selecao-area");
    $b.toggleClass("ativo", modoSelecaoArea)
      .attr("aria-pressed", modoSelecaoArea ? "true" : "false")
      .attr("title", modoSelecaoArea
        ? "Seleção por área LIGADA: arraste no fundo para escolher nós (clique de novo para desligar)"
        : "Selecionar vários nós de uma vez: arraste um retângulo sobre o desenho");
    svg.classList.toggle("mesa-selecao-area-on", modoSelecaoArea);
  }
  function desligarSelecaoArea() {
    if (!modoSelecaoArea) return;
    modoSelecaoArea = false;
    atualizarBotaoSelecaoArea();
  }
  $("#mesa-selecao-area").on("click", function () {
    modoSelecaoArea = !modoSelecaoArea;
    atualizarBotaoSelecaoArea();
  });

  svg.addEventListener("wheel", function (evt) {
    evt.preventDefault();
    var fator = evt.deltaY < 0 ? 1 / 1.15 : 1.15;
    var novoW = Math.min(Math.max(vb.w * fator, LARGURA / 8), areaLayout(nos.length).largura * 2);
    var escala = novoW / vb.w;
    var p = pontoSvg(evt);
    vb.x = p.x - (p.x - vb.x) * escala;
    vb.y = p.y - (p.y - vb.y) * escala;
    vb.w = novoW;
    vb.h = vb.h * escala;
    aplicarViewBox();
  }, { passive: false });

  var drag = null; // {tipo:'pan'|'no', ...}

  function zoomPeloCentro(fator) {
    var centroX = vb.x + vb.w / 2, centroY = vb.y + vb.h / 2;
    var novoW = Math.min(Math.max(vb.w * fator, LARGURA / 8), areaLayout(nos.length).largura * 2);
    var escala = novoW / vb.w;
    vb.w = novoW;
    vb.h *= escala;
    vb.x = centroX - vb.w / 2;
    vb.y = centroY - vb.h / 2;
    aplicarViewBox();
  }

  $("#mesa-zoom-in").on("click", function () { zoomPeloCentro(1 / 1.22); });
  $("#mesa-zoom-out").on("click", function () { zoomPeloCentro(1.22); });
  $("#mesa-enquadrar").on("click", enquadrarConteudo);

  var areaGrafo = document.getElementById("mesa-grafo-wrap");
  var botaoTelaCheia = document.getElementById("mesa-tela-cheia");

  function atualizarBotaoTelaCheia() {
    if (!botaoTelaCheia) return;
    var ativo = document.fullscreenElement === areaGrafo;
    botaoTelaCheia.setAttribute("aria-label", ativo ? "Sair da tela cheia" : "Exibir em tela cheia");
    botaoTelaCheia.setAttribute("title", ativo ? "Voltar à visualização normal" : "Exibir o quadro em tela cheia");
    var texto = botaoTelaCheia.querySelector("span");
    if (texto) texto.textContent = ativo ? "Sair da tela cheia" : "Tela cheia";
  }

  function alternarTelaCheia() {
    if (!areaGrafo || !document.exitFullscreen || !areaGrafo.requestFullscreen) {
      Toast.aviso("A visualização em tela cheia não está disponível neste navegador.");
      return;
    }
    var operacao = document.fullscreenElement
      ? document.exitFullscreen()
      : areaGrafo.requestFullscreen();
    if (operacao && typeof operacao.catch === "function") {
      operacao.catch(function () { Toast.aviso("Não foi possível abrir o quadro em tela cheia."); });
    }
  }

  if (botaoTelaCheia && areaGrafo) {
    botaoTelaCheia.addEventListener("click", alternarTelaCheia);

    document.addEventListener("fullscreenchange", function () {
      atualizarBotaoTelaCheia();
      requestAnimationFrame(function () { requestAnimationFrame(enquadrarConteudo); });
    });
    atualizarBotaoTelaCheia();
  }

  $("#mesa-realista-check").on("change", function () {
    var ativar = temaOrbitAtivo() && this.checked;
    document.body.classList.toggle("mesa-realista", ativar);
    document.body.classList.toggle("mesa-realista-entrando", ativar);
    renderGrafo();
    clearTimeout(this._timerEntradaRealista);
    this._timerEntradaRealista = setTimeout(function () {
      document.body.classList.remove("mesa-realista-entrando");
    }, 1100);
  });

  // Pinch-zoom (touch): dois ponteiro ativos viram zoom+pan de dedos — sem isto o celular
  // só tinha pan (Pointer Events) e o zoom dependia da roda do mouse ou dos botões +/−.
  var ponteiros = {};        // pointerId -> { x, y } em coordenadas de tela
  var pinca = null;          // { ids, d0, vb0, p0, c0 } enquanto há dois dedos na tela

  function idsPonteiros() { return Object.keys(ponteiros); }

  function fimPinca() {
    if (idsPonteiros().length < 2) pinca = null;
  }

  svg.addEventListener("pointerdown", function (evt) {
    ponteiros[evt.pointerId] = { x: evt.clientX, y: evt.clientY };
    svg.setPointerCapture(evt.pointerId);
    var ids = idsPonteiros();
    if (ids.length >= 2) {
      // o segundo dedo chegou: larga pan/arrasto para não brigarem com o pinch pela viewBox
      if (drag) {
        if (drag.raf) cancelAnimationFrame(drag.raf);
        if (drag.tipo === "no" && drag.no._grupo) drag.no._grupo.classList.remove("mesa-no-solto");
        drag = null;
      }
      var a = ponteiros[ids[0]], b = ponteiros[ids[1]];
      var c0 = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      pinca = {
        ids: [ids[0], ids[1]],
        d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        vb0: { x: vb.x, y: vb.y, w: vb.w, h: vb.h },
        p0: pontoSvg({ clientX: c0.x, clientY: c0.y }),
        c0: c0
      };
      return;
    }
    if (pinca) return; // dedo que sobrou de um pinch — não vira pan
    var alvo = evt.target;
    var p = pontoSvg(evt);
    if (alvo.__no) {
      drag = { tipo: "no", no: alvo.__no, x0: p.x, y0: p.y, moveu: false };
      if (modoRealistaAtivo() && alvo.__no._grupo) alvo.__no._grupo.classList.add("mesa-no-solto");
    } else if (modoSelecaoArea) {
      // modo "Selecionar área": arrastar no fundo vira retângulo de seleção, não pan
      drag = { tipo: "area", x0: p.x, y0: p.y, moveu: false };
      drag.el = criarRetanguloArea(p.x, p.y);
    } else {
      drag = { tipo: "pan", x0: evt.clientX, y0: evt.clientY, vb0: { x: vb.x, y: vb.y }, moveu: false };
    }
  });

  svg.addEventListener("pointermove", function (evt) {
    if (ponteiros[evt.pointerId]) {
      ponteiros[evt.pointerId].x = evt.clientX;
      ponteiros[evt.pointerId].y = evt.clientY;
    }
    if (pinca) {
      var a = ponteiros[pinca.ids[0]], b = ponteiros[pinca.ids[1]];
      if (!a || !b) return;
      var d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      var fator = pinca.d0 / d; // dedos abrem → viewBox encolhe → zoom dentro
      var novoW = Math.min(Math.max(pinca.vb0.w * fator, LARGURA / 8), areaLayout(nos.length).largura * 2);
      var f = novoW / pinca.vb0.w;
      vb.w = novoW;
      vb.h = pinca.vb0.h * f;
      vb.x = pinca.p0.x - (pinca.p0.x - pinca.vb0.x) * f;
      vb.y = pinca.p0.y - (pinca.p0.y - pinca.vb0.y) * f;
      // mover o centro dos dois dedos também desloca o desenho (pan de polegar)
      var cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      var unidade = vb.w / svg.clientWidth;
      vb.x -= (cx - pinca.c0.x) * unidade;
      vb.y -= (cy - pinca.c0.y) * unidade;
      aplicarViewBox();
      // re-baseline: o próximo movimento parte da viewBox e do centro já aplicados
      pinca.vb0 = { x: vb.x, y: vb.y, w: vb.w, h: vb.h };
      pinca.p0 = pontoSvg({ clientX: cx, clientY: cy });
      pinca.c0 = { x: cx, y: cy };
      pinca.d0 = d;
      return;
    }
    if (!drag) return;
    if (drag.tipo === "pan") {
      var escala = vb.w / svg.clientWidth;
      var dx = (evt.clientX - drag.x0) * escala;
      var dy = (evt.clientY - drag.y0) * escala;
      if (Math.abs(evt.clientX - drag.x0) + Math.abs(evt.clientY - drag.y0) > 3) drag.moveu = true;
      vb.x = drag.vb0.x - dx;
      vb.y = drag.vb0.y - dy;
      aplicarViewBox();
    } else if (drag.tipo === "area") {
      var pa = pontoSvg(evt);
      if (Math.hypot(pa.x - drag.x0, pa.y - drag.y0) > 3) drag.moveu = true;
      posicionarRetanguloArea(drag, pa.x, pa.y);
    } else {
      var p = pontoSvg(evt);
      if (Math.hypot(p.x - drag.x0, p.y - drag.y0) > 3) drag.moveu = true;
      if (!drag.moveu) return;
      // P0-5: pointermove dispara dezenas de vezes por segundo e cada moverNo varre as
      // arestas do nó — coalesce num rAF para não enfileirar reposicionamentos.
      drag.px = p.x;
      drag.py = p.y;
      if (!drag.raf) {
        drag.raf = requestAnimationFrame(function () {
          var d = drag;
          if (d) {
            d.raf = null;
            moverNo(d.no, d.px, d.py);
          }
        });
      }
    }
  });

  function assentarPino(n) {
    if (!modoRealistaAtivo() || !n || !n._grupo) return;
    n._grupo.classList.remove("mesa-no-solto", "mesa-no-repinando");
    // Reinicia a perfuração mesmo quando o mesmo nó é movido várias vezes.
    void n._grupo.getBoundingClientRect();
    n._grupo.classList.add("mesa-no-repinando");
    clearTimeout(n._timerRepinar);
    n._timerRepinar = setTimeout(function () {
      if (n._grupo) n._grupo.classList.remove("mesa-no-repinando");
    }, 720);
  }

  svg.addEventListener("pointerup", function (evt) {
    delete ponteiros[evt.pointerId];
    var eraPinca = !!pinca;
    fimPinca();
    try { svg.releasePointerCapture(evt.pointerId); } catch (e) { /* sem captura — nada a soltar */ }
    if (eraPinca) return; // fim de pinch não é clique: o nó sob o dedo não abre a gaveta
    if (!drag) return;
    var d = drag;
    drag = null;
    if (d.raf) { cancelAnimationFrame(d.raf); d.raf = null; }

    if (d.tipo === "area") {
      if (d.el && d.el.parentNode) d.el.parentNode.removeChild(d.el);
      if (d.moveu) {
        var pf = pontoSvg(evt);
        selecionarNosNaArea(d.x0, d.y0, pf.x, pf.y);
      }
      desligarSelecaoArea();   // um retângulo por clique: o modo se desliga sozinho depois
      return;
    }

    if (d.tipo === "no") {
      if (d.no._grupo) d.no._grupo.classList.remove("mesa-no-solto");
      if (!d.moveu) {
        cliqueNo(d.no, evt.shiftKey);
        assentarPino(d.no);
      } else {
        d.no.fixo = true;
        if (d.no.kind === "entidade") {
          $.ajax({
            url: "/api/investigacao/entidades/" + d.no.dado.id_entidade + "/posicao",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ pos_x: d.no.x, pos_y: d.no.y })
          }).fail(function () { Toast.erro("Não foi possível salvar a posição desta entidade."); });
        } else if (d.no.kind === "canonica") {
          posCanonica[d.no.dado.id_canonica] = { x: d.no.x, y: d.no.y };
          $.ajax({
            url: "/api/investigacao/canonicas/" + d.no.dado.id_canonica + "/posicao",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ pos_x: d.no.x, pos_y: d.no.y })
          }).fail(function () { Toast.erro("Não foi possível salvar a posição desta entidade."); });
        }
        // nó "cluster" (agrupamento de folhas) é recriado a cada atualizar() — arrastar só
        // acomoda visualmente até o próximo refresh, sem posição salva (não tem onde persistir).
        renderGrafo(); // reagrupa clusters e refaz a legenda com o conjunto final
        assentarPino(d.no);
      }
    }
  });

  svg.addEventListener("pointercancel", function (evt) {
    delete ponteiros[evt.pointerId];
    fimPinca();
    if (drag && drag.tipo === "no") assentarPino(drag.no);
    if (drag && drag.tipo === "area" && drag.el && drag.el.parentNode) drag.el.parentNode.removeChild(drag.el);
    drag = null;
  });

  // captura perdida (navegador roubou o gesto, dedo saiu da tela): tira do mapa para o
  // contador de ponteiros não ficar sujo e o pinch nunca mais montar
  svg.addEventListener("lostpointercapture", function (evt) {
    delete ponteiros[evt.pointerId];
    fimPinca();
  });

  function moverNo(n, x, y) {
    n.x = x;
    n.y = y;
    var r = raioNo(n);
    (n._silhuetas || [n._forma]).forEach(function (s) { posicionarSilhueta(s, n, r); });
    if (n._foto) {
      n._foto.setAttribute("x", x - r);
      n._foto.setAttribute("y", y - r);
    }
    if (n._icone) {
      var lado = r * 1.15;
      n._icone.setAttribute("x", x - lado / 2);
      n._icone.setAttribute("y", y - lado / 2);
    }
    n._rotulo.setAttribute("x", x);
    n._rotulo.setAttribute("y", y + r + 12);
    if (n._badge) {
      var pb = posBadge(n);
      n._badge.setAttribute("cx", pb.x);
      n._badge.setAttribute("cy", pb.y);
    }
    posicionarPinoTridimensional(n._pino3d, n, r);

    arestas.forEach(function (a) {
      if (a.a !== n.id && a.b !== n.id) return;
      posicionarAresta(a);
    });
  }

  // ---------- clique no nó: modo ligação ou modal de detalhes ----------

  function cliqueNo(n, comShift) {
    if (n.kind === "cluster") {
      expandirCluster(n);
    } else if (modoLigacao) {
      concluirLigacao(n);   // no modo "Relacionar" o clique já tem dono; Shift não muda nada
    } else if (comShift) {
      alternarSelecao(n);
    } else {
      selecionarNo(n);
    }
  }

  // ---------- gaveta de detalhes ----------
  // O nó aberto na gaveta vira o FOCO: ele e os vizinhos diretos ficam nítidos, o resto do
  // grafo desbota. É o que torna o detalhe útil num grafo grande — antes o modal tapava o
  // desenho, e ao fechá-lo o nó se perdia de novo no meio de cem outros.
  //
  // Com Shift o clique ACUMULA: dá pra pôr duas, três pessoas em foco ao mesmo tempo e ver as
  // relações de todas juntas — que é o que responde "o que estes dois têm a ver um com o
  // outro?", pergunta que o foco de um nó só não respondia (era preciso decorar o desenho de
  // um, clicar no outro e comparar de memória).

  // ids dos nós em foco, na ordem em que foram escolhidos. Guarda ID e não o objeto: recarregar
  // o grafo troca os objetos de nó, e a seleção tem de sobreviver a isso.
  var focados = [];

  function aplicarFoco() {
    svg.classList.toggle("mesa-grafo-com-foco", focados.length > 0);
    if (!focados.length) {
      nos.forEach(function (n) {
        if (n._grupo) n._grupo.classList.remove("fora-do-foco", "no-focado");
        if (n._rotulo) n._rotulo.classList.remove("rotulo-visivel");
      });

    arestas.forEach(function (a) {
        if (a._el) a._el.classList.remove("fora-do-foco");
        if (a._rotulo) a._rotulo.classList.remove("fora-do-foco", "rotulo-visivel");
      });
      return;
    }

    var viz = vizinhancaDe(focados);
    var vizinhos = viz.nos;
    // nomes da vizinhança só quando ela é pequena — ver LIMITE_ROTULOS_VIZINHANCA. Com vários
    // nós em foco a conta é sobre a vizinhança SOMADA: dois nós de dez vizinhos cada já passam
    // do limite juntos, e é aí que os nomes começariam a se atropelar.
    var nomearVizinhos = viz.tamanho <= LIMITE_ROTULOS_VIZINHANCA;

    nos.forEach(function (n) {
      if (!n._grupo) return;
      var dentro = !!vizinhos[n.id];
      var proprio = !!viz.alvo[n.id];
      n._grupo.classList.toggle("fora-do-foco", !dentro);
      n._grupo.classList.toggle("no-focado", proprio);
      // o nome dos nós escolhidos aparece sempre, mesmo com os rótulos desligados
      if (n._rotulo) n._rotulo.classList.toggle("rotulo-visivel", proprio || (dentro && nomearVizinhos));
    });
    arestas.forEach(function (a) {
      var dentro = !!(viz.alvo[a.a] || viz.alvo[a.b]);
      if (a._el) a._el.classList.toggle("fora-do-foco", !dentro);
      if (a._rotulo) {
        a._rotulo.classList.toggle("fora-do-foco", !dentro);
        a._rotulo.classList.toggle("rotulo-visivel", dentro && nomearVizinhos);
      }
    });
  }

  // ---------- seleção ----------

  // Clique simples troca a seleção; Shift+clique põe/tira o nó do conjunto. Tirar o último
  // fecha a gaveta — seleção vazia é o mesmo que nenhuma.
  function selecionarNo(n) {
    focados = [n.id];
    abrirGaveta();
  }

  function alternarSelecao(n) {
    var i = focados.indexOf(n.id);
    if (i !== -1) focados.splice(i, 1);
    else focados.push(n.id);
    if (!focados.length) fecharModalNo();
    else abrirGaveta();
  }

  function idsVisiveis() {
    var visivel = {};
    nos.forEach(function (n) { visivel[n.id] = true; });
    return visivel;
  }

  // Elemento focado antes de a gaveta abrir (um botão da lista de busca, por exemplo) — ao
  // fechar, o foco volta pra ele em vez de se perder no body.
  var focoAntesGaveta = null;

  function abrirGaveta() {
    // um nó escolhido pode ter saído do desenho no meio do caminho (filtro, período, recarga):
    // sem esta limpeza a gaveta ficaria falando de gente que não está mais na tela
    var visivel = idsVisiveis();
    focados = focados.filter(function (id) { return visivel[id]; });
    if (!focados.length) { fecharModalNo(); return; }
    var estavaAberta = !$drawerNo.hasClass("hidden");
    if (focados.length === 1) abrirDetalhesNo(noPorId[focados[0]]);
    else abrirResumoSelecao();
    aplicarFoco();
    atualizarBarraIsolamento();
    $drawerNo.removeClass("hidden");
    if (!estavaAberta) {
      // teclado: o foco entra na gaveta (Esc/Tab andam por dentro dela) e o fechar é o alvo
      focoAntesGaveta = document.activeElement;
      $("#no-fechar").trigger("focus");
    }
  }

  function linhaDetalhe($corpo, rotulo, valor) {
    $("<dt>").text(rotulo).appendTo($corpo);
    $("<dd>").text(valor || "—").appendTo($corpo);
  }

  // ---------- cabecalho da gaveta (nome + tipo + botões) e corpo ----------

  // Cabeçalho da gaveta de um nó: o NOME (o rótulo do nó) é o título — é o que o investigador
  // procura; o tipo vira uma badge ao lado (a mesma cor do desenho e da legenda). Sem isto o
  // cabeçalho dizia só "Pessoa", e o nome ficava escondido lá embaixo, no corpo.
  function setTituloGaveta(n) {
    var nome = n.rotulo || "";
    var tipo = ROTULOS_TIPO[n.tipo] || n.tipo || "";
    $("#no-titulo").text(nome || tipo || "Entidade").attr("title", nome || "");
    var $tipo = $("#no-tipo").empty();
    if (tipo) {
      $("<span>").addClass("mesa-selecao-cor").css("background", corTipo(n.tipo))
        .attr("title", tipo).appendTo($tipo);
      $("<span>").text(tipo).appendTo($tipo);
      $tipo.show();
    } else {
      $tipo.hide();
    }
    // "Buscar entidade" só para o que tem nome de gente/empresa de verdade (não vale para grupo
    // fuzzy, nó de documento genérico nem o próprio arquivo).
    var podeBuscar = n.kind === "entidade" || n.kind === "canonica" || (n.kind === "doc" && n.tipo === "doc_pessoa");
    $("#no-buscar").attr("data-nome", podeBuscar ? (nome || "") : "").prop("hidden", !(podeBuscar && nome));
  }

  // Cabeçalho sem nome (relação, seleção de várias): só o título, sem badge nem busca.
  function tituloGavetaSimples(texto) {
    $("#no-titulo").text(texto).attr("title", "");
    $("#no-tipo").empty().hide();
    $("#no-buscar").attr("data-nome", "").prop("hidden", true);
  }

  // Linha de campo copiável (nome, chave, CPF/CNPJ): valor + botãozinho de copiar.
  function linhaDetalheCopiavel($corpo, rotulo, valor) {
    $("<dt>").text(rotulo).appendTo($corpo);
    var $dd = $("<dd>").text(valor || "—");
    if (valor) botaoCopiar($dd, valor);
    $dd.appendTo($corpo);
  }

  function botaoCopiar($dd, texto) {
    var $b = $("<button>").attr({ type: "button" }).addClass("mesa-copiar-btn")
      .attr({ title: "Copiar", "aria-label": "Copiar " + texto })
      .html(window.Icones.html("copiar"));
    $b.on("click", function (ev) { ev.preventDefault(); copiarTexto(String(texto)); });
    $dd.append($b);
  }

  function copiarTexto(txt) {
    var ok = function () { Toast.sucesso("Copiado."); };
    var falhou = function () { Toast.erro("Não foi possível copiar."); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(ok).catch(function () { copiarLegado(txt) ? ok() : falhou(); });
    } else {
      copiarLegado(txt) ? ok() : falhou();
    }
  }

  function copiarLegado(txt) {
    try {
      var ta = document.createElement("textarea");
      ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var feito = document.execCommand("copy");
      document.body.removeChild(ta);
      return feito;
    } catch (e) { return false; }
  }

  // Seção colapsável do corpo (Relações, Evidências, Documentos). Vira um <div> dentro do <dl>
  // (agrupar dt/dd em div é válido no HTML5) com cabeçalho clicável e contagem. Recolhida por
  // padrão quando a lista é longa, para a gaveta não virar uma pilha que exige rolagem.
  function novaSecao($corpo, titulo, contagem, recolherPorPadrao) {
    var $sec = $("<div>").addClass("mesa-gaveta-secao").toggleClass("recolhida", !!recolherPorPadrao);
    var $head = $("<button>").attr({ type: "button" }).addClass("mesa-gaveta-secao-head")
      .attr("aria-expanded", recolherPorPadrao ? "false" : "true");
    $("<span>").addClass("mesa-gaveta-seta").html(window.Icones.html("chevronDown")).appendTo($head);
    $("<span>").text(titulo).appendTo($head);
    if (contagem != null) $("<span>").addClass("mesa-gaveta-cont").text(" (" + contagem + ")").appendTo($head);
    var $corpoSecao = $("<div>").addClass("mesa-gaveta-secao-corpo");
    $head.on("click", function () {
      var recolhida = $sec.toggleClass("recolhida").hasClass("recolhida");
      $head.attr("aria-expanded", recolhida ? "false" : "true");
    });
    $sec.append($head).append($corpoSecao).appendTo($corpo);
    return $corpoSecao;
  }

  // ---------- expandir / arrastar a gaveta ----------

  var LARGURA_MIN_GAVERA = 300;

  function larguraDoQuadro() { return $("#mesa-grafo-wrap").width() || 900; }

  function atualizarBotaoExpandir() {
    var larga = $drawerNo.hasClass("mesa-drawer-larga");
    $("#no-expandir").attr("aria-pressed", larga ? "true" : "false")
      .attr("title", larga ? "Recolher a gaveta" : "Expandir a gaveta");
  }

  // Restaura a preferência salva (larga OU uma largura em px definida ao arrastar). Chamado no
  // boot e sempre que a gaveta é reaberta, para sobreviver à troca de nó e ao recarregar.
  function restaurarLarguraGaveta() {
    var larga = false, px = null;
    try {
      larga = localStorage.getItem("mesa-drawer-larga") === "1";
      px = parseInt(localStorage.getItem("mesa-drawer-w"), 10) || null;
    } catch (e) { /* modo privado: segue com o padrão */ }
    if (px) {
      var max = Math.max(LARGURA_MIN_GAVERA, larguraDoQuadro() - 16);
      $drawerNo.removeClass("mesa-drawer-larga")[0].style.width = Math.min(px, max) + "px";
    } else {
      $drawerNo[0].style.width = "";
      $drawerNo.toggleClass("mesa-drawer-larga", larga);
    }
    atualizarBotaoExpandir();
  }

  function definirLarguraGaveta(px) {
    var max = Math.max(LARGURA_MIN_GAVERA, larguraDoQuadro() - 16);
    px = Math.max(LARGURA_MIN_GAVERA, Math.min(px, max));
    $drawerNo[0].style.width = px + "px";
    $drawerNo.removeClass("mesa-drawer-larga"); // largura manual substitui o estado "larga"
    atualizarBotaoExpandir();
    try {
      localStorage.setItem("mesa-drawer-w", String(Math.round(px)));
      localStorage.setItem("mesa-drawer-larga", "0");
    } catch (e) { /* sem persistência — segue funcionando na sessão */ }
  }

  function iniciarCabecalhoGaveta() {
    restaurarLarguraGaveta();

    $("#no-expandir").on("click", function () {
      $drawerNo[0].style.width = ""; // limpa a largura manual para o estado largo valer
      var larga = $drawerNo.toggleClass("mesa-drawer-larga").hasClass("mesa-drawer-larga");
      try {
        localStorage.setItem("mesa-drawer-larga", larga ? "1" : "0");
        localStorage.removeItem("mesa-drawer-w");
      } catch (e) { /* ignore */ }
      atualizarBotaoExpandir();
    });

    $("#no-buscar").on("click", function () {
      var nome = $(this).attr("data-nome") || "";
      if (!nome) return;
      window.open("busca_entidades.html?q=" + encodeURIComponent(nome), "_blank", "noopener");
    });

    // Alça: pointer events (funciona com mouse e toque), ajustando a largura continuamente.
    var redimensionando = false;
    $("#no-resize").on("pointerdown", function (e) {
      redimensionando = true;
      $drawerNo.addClass("mesa-drawer-redimensionando");
      if (this.setPointerCapture && e.pointerId != null) { try { this.setPointerCapture(e.pointerId); } catch (err) {} }
      e.preventDefault();
    });
    $(document).on("pointermove", function (e) {
      if (!redimensionando) return;
      var esquerda = $drawerNo.offset().left;
      definirLarguraGaveta(e.pageX - esquerda);
    });
    $(document).on("pointerup pointercancel", function () {
      if (!redimensionando) return;
      redimensionando = false;
      $drawerNo.removeClass("mesa-drawer-redimensionando");
    });
    // teclado na alça: ← / → ajustam em passos finos
    $("#no-resize").on("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      var atual = parseInt($drawerNo[0].style.width, 10) || $drawerNo.outerWidth();
      definirLarguraGaveta(atual + (e.key === "ArrowRight" ? 24 : -24));
    });

    // ao redimensionar a janela, garante que a largura salva não estoura o quadro
    $(window).on("resize", function () {
      if ($drawerNo.hasClass("hidden")) return;
      if ($drawerNo[0].style.width) definirLarguraGaveta(parseInt($drawerNo[0].style.width, 10));
    });
  }

  function abrirDetalhesNo(n) {
    gavetaArestaAtual = null;
    var $corpo = $("#no-corpo").empty();
    var $acoes = $("#no-acoes").empty();
    setTituloGaveta(n);

    if (n.kind === "doc") {
      // Nó derivado do documento: a fonte da verdade é o texto do PDF/planilha, não a mesa.
      // A gaveta é leitura — com a ponte para abrir o documento e para promover a pessoa a
      // canônica (aí sim ela entra nas camadas do banco e pode ser relacionada a tudo).
      renderDetalheDoc(n, $corpo, $acoes);
      dicaSelecao("Nó montado pela análise dos documentos — o link abre o documento na página dele.");
      return;
    }

    if (n.kind === "fuzzy") {
      renderDetalheFuzzy(n, $corpo, $acoes);
      dicaSelecao("Grupo visual: nada foi fundido no banco. Desfaça o grupo para mexer em cada entidade.");
      return;
    }

    if (n.kind === "entidade") {
      var e = n.dado;
      linhaDetalhe($corpo, "Origem", e.rotulo_origem);
      linhaDetalhe($corpo, "Comentário", e.tx_comentario);
      if (e.tx_justificativa) linhaDetalhe($corpo, "Justificativa da IA", e.tx_justificativa);
      linhaDetalhe($corpo, "Criado por", (e.criado_por || "—") + (e.in_automatico ? " (busca automática)" : ""));
      linhaDetalhe($corpo, "Data", formatarDataHora(e.dt_criacao));
      if (e.dt_origem) linhaDetalhe($corpo, "Data do registro", formatarDataHora(e.dt_origem));
      if (e.cd_revisao === "sugerida") {
        $("<dt>").text("Status").appendTo($corpo);
        $("<dd>").append($("<span>").addClass("badge badge-rodando").text("Sugestão da IA — aguardando revisão")).appendTo($corpo);
      }

      renderImagemDoNo(n, $corpo);
      renderRelacoesDoNo(n, $corpo);

      if (e.cd_revisao === "sugerida") {
        $("<button>").addClass("btn btn-primary").html(window.Icones.html("check") + " Confirmar")
          .on("click", function () { revisarEntidade(e.id_entidade, "confirmada"); }).appendTo($acoes);
        $("<button>").addClass("btn btn-secondary").html(window.Icones.html("fechar") + " Descartar")
          .on("click", function () { revisarEntidade(e.id_entidade, "descartada"); }).appendTo($acoes);
      }
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("link") + " Relacionar")
        .on("click", function () { iniciarLigacao(n); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("editar") + " Editar comentário")
        .on("click", function () { editarComentario(e); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("apagar") + " Apagar")
        .on("click", function () { apagarEntidade(e.id_entidade); }).appendTo($acoes);
    } else {
      var c = n.dado;
      linhaDetalheCopiavel($corpo, "Nome", c.no_entidade);
      if (c.nu_chave) linhaDetalheCopiavel($corpo, "Chave", c.nu_chave);
      if (c.tx_observacao) linhaDetalhe($corpo, "Observação", c.tx_observacao);
      linhaDetalhe($corpo, "Criado por", (c.criado_por || "—") + (c.in_automatico ? " (identificação automática)" : ""));
      linhaDetalhe($corpo, "Data", formatarDataHora(c.dt_criacao));
      // O período REAL de atividade (datas dos registros que a evidenciam e das relações
      // dela) — a "Data" acima é quando o nó foi criado, que num nó automático é a data da
      // extração, não a do fato que o investigador procura.
      var atividades = datasDeAtividade(n);
      if (atividades.length) {
        var minA = Math.min.apply(null, atividades);
        var maxA = Math.max.apply(null, atividades);
        linhaDetalhe($corpo, "Período de atividade",
          formatarDiaInstantaneo(minA) + (maxA !== minA ? " a " + formatarDiaInstantaneo(maxA) : ""));
      }

      renderImagemDoNo(n, $corpo);
      renderRelacoesDoNo(n, $corpo);

      var $ev = novaSecao($corpo, "Evidências", c.qt_evidencias, (c.qt_evidencias || 0) > 12);
      $ev.text("Carregando…");
      $.getJSON("/api/investigacao/canonicas/" + c.id_canonica + "/evidencias").done(function (lista) {
        $ev.empty();
        if (!lista.length) $ev.text("—");
        lista.forEach(function (evd) {
          $("<div>").text((ROTULOS_TIPO[evd.tp_origem] || evd.tp_origem) + " #" + evd.id_origem +
            (evd.tx_detalhe ? " — " + evd.tx_detalhe : "")).appendTo($ev);
        });
      });

      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("link") + " Relacionar")
        .on("click", function () { iniciarLigacao(n); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("fundir") + " Fundir com...")
        .on("click", function () { abrirFusaoCanonica(c); }).appendTo($acoes);
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("apagar") + " Apagar")
        .on("click", function () { apagarCanonica(c.id_canonica); }).appendTo($acoes);
    }

    dicaSelecao("Shift+clique em outro nó para pôr os dois em foco ao mesmo tempo.");
  }

  // Gaveta do supernó fuzzy: quem foi junto, as relações somadas (só leitura) e como desfazer.
  // Não há edição nem "Relacionar" aqui — o grupo só existe na tela, e a relação precisa sair
  // de um nó de verdade (canônica para o banco; achado/pessoa de documento nem tem linha no
  // banco — o grupo dele é só visual). Fundir de verdade continua sendo no banco, uma entidade
  // por vez, pelo "Fundir com..." da gaveta de cada canônica.
  function renderDetalheFuzzy(n, $corpo, $acoes) {
    var membros = (n.membros || []).map(function (id) { return noPorId[id]; }).filter(Boolean);
    var rotuloGrupo = n.tipo === "doc_achado" ? "Achados neste grupo"
      : n.tipo === "doc_pessoa" ? "Pessoas neste grupo"
      : "Entidades neste grupo";
    linhaDetalhe($corpo, "Tipo", (ROTULOS_TIPO[n.tipo] || n.tipo) + " (grupo de nome parecido)");
    linhaDetalhe($corpo, "Nome exibido", n.rotulo);
    $("<dt>").text(rotuloGrupo + " (" + membros.length + ")").appendTo($corpo);
    var $lista = $("<dd>").appendTo($corpo);
    membros.forEach(function (m) {
      $("<div>").addClass("mesa-relacao-item").text("• " + (m.rotulo || m.id)).appendTo($lista);
    });

    var doNo = arestas.filter(function (a) { return a.a === n.id || a.b === n.id; });
    if (doNo.length) {
      $("<dt>").text("Relações somadas (" + doNo.length + ")").appendTo($corpo);
      var $rel = $("<dd>").appendTo($corpo);
      doNo.forEach(function (a) {
        var outro = noPorId[a.a === n.id ? a.b : a.a];
        if (!outro) return;
        var direcao = a.a === n.id ? "→" : "←";
        var texto = direcao + " " + (a.tipo || naturezaAresta(a).rotulo) + " " + direcao + " " + truncar(outro.rotulo, 50);
        $("<div>").addClass("mesa-relacao-item").append($("<span>").text(texto)).appendTo($rel);
      });
    }

    $("<button>").addClass("btn btn-secondary").html(window.Icones.html("desfazer") + " Desfazer este grupo")
      .attr("title", "Separa estes itens de novo na tela (o global continua valendo para o resto).")
      .on("click", function () {
        (n.membros || []).forEach(function (id) { fuzzyIgnorados[id] = true; });
        atualizar();
      }).appendTo($acoes);
  }

  // Gaveta de um nó derivado de documento genérico (pessoa/achado/documento). Read-only por
  // natureza: o que a IA achou no PDF só muda reanalisando o documento — por isso não há aqui
  // os botões de editar/apagar das outras camadas. O botão "Abrir documento" leva ao
  // visualizador (web/documento.html) com o trecho onde a pessoa aparece em evidência.
  function linkDocumento(idDoc, texto) {
    return $("<a>").attr({
      href: "documento.html?inquerito=" + encodeURIComponent(idInquerito) + "&doc=" + encodeURIComponent(idDoc),
      target: "_blank", rel: "noopener"
    }).text(texto);
  }

  function renderDetalheDoc(n, $corpo, $acoes) {
    var d = n.dado || {};

    if (n.tipo === "doc_documento") {
      linhaDetalhe($corpo, "Arquivo", d.no_arquivo);
      linhaDetalhe($corpo, "Tipo", (d.tp_arquivo || "").toUpperCase());
      if (d.tx_resumo) {
        $("<dt>").text("Resumo da IA").appendTo($corpo);
        $("<dd>").css("white-space", "pre-wrap").text(d.tx_resumo).appendTo($corpo);
      }
      $("<button>").addClass("btn btn-primary").html(window.Icones.html("documentos") + " Abrir documento")
        .on("click", function () {
          window.open("documento.html?inquerito=" + encodeURIComponent(idInquerito) +
            "&doc=" + encodeURIComponent(d.id_documento), "_blank", "noopener");
        }).appendTo($acoes);
      return;
    }

    var docs = d.documentos || [];
    if (n.tipo === "doc_pessoa") {
      linhaDetalheCopiavel($corpo, "Nome", d.no_pessoa);
      if (d.no_vulgo) linhaDetalhe($corpo, "Vulgo", d.no_vulgo);
      if (d.nu_cpf_cnpj) linhaDetalheCopiavel($corpo, "CPF/CNPJ", d.nu_cpf_cnpj);
      if (d.tx_variantes) linhaDetalhe($corpo, "Outras grafias", d.tx_variantes);
      if (d.tp_papel) linhaDetalhe($corpo, "Papel", d.tp_papel);
      linhaDetalhe($corpo, "Menções", String(d.qt_mencoes || 0));

      // Ponte com as camadas do banco: promover a pessoa a canônica a torna relacionável na
      // mesa (e visível ao chat de IA, que lê as canônicas do inquerito).
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("estrela") + " Criar entidade canônica")
        .attr("title", "Cria esta pessoa como entidade canônica da mesa — ela passa a poder ser relacionada e fundida com as demais.")
        .on("click", function () {
          var $b = $(this).prop("disabled", true);
          $.ajax({
            url: "/api/investigacao/canonicas", method: "POST", contentType: "application/json",
            data: JSON.stringify({
              id_inquerito: parseInt(idInquerito, 10), tp_entidade: "pessoa",
              nome: d.no_pessoa,
              observacao: ["Citada em " + (d.qt_documentos || docs.length) + " documento(s) do caso."]
                .concat(d.no_vulgo ? ["Vulgo: " + d.no_vulgo] : [])
                .concat(d.nu_cpf_cnpj ? ["CPF/CNPJ: " + d.nu_cpf_cnpj] : []).join(" ")
            })
          })
            .done(function () {
              Toast.sucesso("«" + d.no_pessoa + "» virou entidade canônica na mesa.");
              fecharModalNo(); carregarGrafo();
            })
            .fail(function (xhr) {
              $b.prop("disabled", false);
              mostrarErroAjax(xhr);
            });
        }).appendTo($acoes);
    } else {
      linhaDetalhe($corpo, "Tipo de achado", (d.tp_achado || "").toUpperCase());
      linhaDetalhe($corpo, "Valor", d.tx_valor);
      linhaDetalhe($corpo, "Ocorrências", String(d.qt_ocorrencias || 0));
      if (d.tx_contexto) linhaDetalhe($corpo, "Contexto", d.tx_contexto);
    }

    var total = d.qt_documentos || docs.length;
    var $lista = novaSecao($corpo, "Citado em " + total + " documento(s)", null, total > 12);
    docs.forEach(function (doc) {
      var $item = $("<div>").addClass("mesa-relacao-item");
      linkDocumento(doc.id_documento, doc.no_arquivo +
        (doc.qt_mencoes ? " (" + doc.qt_mencoes + "×)" : "")).appendTo($item);
      if (doc.tx_contexto) $("<small>").text(" — " + doc.tx_contexto).appendTo($item);
      $lista.append($item);
    });
    if (d.qt_documentos_omitidos) {
      $("<div>").text("… e mais " + d.qt_documentos_omitidos + " documento(s).").appendTo($lista);
    }
  }

  // A multisseleção não tem botão nem menu: sem uma linha dizendo que ela existe, ninguém
  // descobre. Fica no rodapé da gaveta, que é onde o investigador já está quando clicou um nó.
  // Fora do <dl> de propriedades — texto solto ali dentro não é dt nem dd.
  function dicaSelecao(texto) {
    $("#no-dica").remove();
    $("<p>").attr("id", "no-dica").addClass("mesa-drawer-dica").text(texto).insertAfter("#no-corpo");
  }

  // Gaveta com dois ou mais nós escolhidos. Não existe "o detalhe" de vários, e repetir o
  // detalhe de cada um seria só uma pilha de fichas: o que a seleção conjunta responde é o que
  // eles têm ENTRE si (as relações diretas) e o que têm EM COMUM (o vizinho que aparece nas
  // duas pontas) — o triângulo que denuncia intermediário costuma sair daí.
  function abrirResumoSelecao() {
    gavetaArestaAtual = null;
    var $corpo = $("#no-corpo").empty();
    var $acoes = $("#no-acoes").empty();
    var escolhidos = focados.map(function (id) { return noPorId[id]; }).filter(Boolean);
    tituloGavetaSimples(escolhidos.length + " entidades em foco");

    $("<dt>").text("Selecionadas").appendTo($corpo);
    var $sel = $("<dd>").appendTo($corpo);
    escolhidos.forEach(function (n) {
      var $item = $("<div>").addClass("mesa-relacao-item");
      $("<span>").addClass("mesa-selecao-cor").css("background", corTipo(n.tipo))
        .attr("title", ROTULOS_TIPO[n.tipo] || n.tipo).appendTo($item);
      $("<span>").text(truncar(n.rotulo, 42)).appendTo($item);
      $("<button>").addClass("mesa-relacao-btn").attr({ title: "Ver só esta", "aria-label": "Ver só esta" }).html(window.Icones.html("alvo"))
        .on("click", function () { selecionarNo(n); }).appendTo($item);
      $("<button>").addClass("mesa-relacao-btn").attr({ title: "Tirar do foco", "aria-label": "Tirar do foco" }).html(window.Icones.html("fechar"))
        .on("click", function () { alternarSelecao(n); }).appendTo($item);
      $sel.append($item);
    });

    var viz = vizinhancaDe(focados);

    // 1) o que liga os escolhidos uns aos outros
    var entre = viz.arestas.filter(function (a) { return viz.alvo[a.a] && viz.alvo[a.b]; });
    $("<dt>").text("Relações entre as selecionadas (" + entre.length + ")").appendTo($corpo);
    var $entre = $("<dd>").appendTo($corpo);
    if (!entre.length) {
      $entre.text("Nenhuma ligação direta entre elas.");
    } else {
      entre.forEach(function (a) {
        var $item = $("<div>").addClass("mesa-relacao-item");
        $("<span>").addClass("mesa-legenda-linha").css("background", naturezaAresta(a).cor)
          .attr("title", naturezaAresta(a).rotulo).appendTo($item);
        var texto = truncar(noPorId[a.a].rotulo, 24) + " → " + a.tipo + " → " + truncar(noPorId[a.b].rotulo, 24);
        if (a.dado.tx_detalhe) texto += " (" + a.dado.tx_detalhe + ")";
        $("<span>").text(texto).appendTo($item);
        $entre.append($item);
      });
    }

    // 2) quem está ligado a mais de uma das escolhidas — o contato compartilhado
    var quantasTocam = {};
    viz.arestas.forEach(function (a) {
      [[a.a, a.b], [a.b, a.a]].forEach(function (par) {
        if (!viz.alvo[par[0]] || viz.alvo[par[1]]) return;
        (quantasTocam[par[1]] = quantasTocam[par[1]] || {})[par[0]] = true;
      });
    });
    var comuns = Object.keys(quantasTocam)
      .filter(function (id) { return Object.keys(quantasTocam[id]).length > 1 && noPorId[id]; })
      .sort(function (x, y) { return Object.keys(quantasTocam[y]).length - Object.keys(quantasTocam[x]).length; });
    $("<dt>").text("Vizinhos em comum (" + comuns.length + ")").appendTo($corpo);
    var $comuns = $("<dd>").appendTo($corpo);
    if (!comuns.length) {
      $comuns.text("Nenhum nó ligado a mais de uma das selecionadas.");
    } else {
      comuns.slice(0, 40).forEach(function (id) {
        var n = noPorId[id];
        var $item = $("<div>").addClass("mesa-relacao-item");
        $("<span>").addClass("mesa-selecao-cor").css("background", corTipo(n.tipo)).appendTo($item);
        $("<span>").text(truncar(n.rotulo, 42) + " — em " + Object.keys(quantasTocam[id]).length + " delas").appendTo($item);
        $("<button>").addClass("mesa-relacao-btn").attr("title", "Pôr em foco também").text("+")
          .on("click", function () { alternarSelecao(n); }).appendTo($item);
        $comuns.append($item);
      });
      if (comuns.length > 40) $("<div>").text("… e mais " + (comuns.length - 40) + ".").appendTo($comuns);
    }

    // 3) tudo que sai da seleção, resumido por natureza — o detalhe aresta a aresta pode ser
    // centenas de linhas num nó-cabeça, e a contagem já diz de que o vínculo é feito
    var porNatureza = {};
    viz.arestas.forEach(function (a) {
      var nat = naturezaAresta(a);
      porNatureza[nat.chave] = (porNatureza[nat.chave] || 0) + 1;
    });
    $("<dt>").text("Relações em foco (" + viz.arestas.length + ")").appendTo($corpo);
    var $nat = $("<dd>").addClass("mesa-selecao-naturezas").appendTo($corpo);
    NATUREZAS_RELACAO.forEach(function (nat) {
      if (!porNatureza[nat.chave]) return;
      var $item = $("<span>").addClass("mesa-legenda-item mesa-legenda-info").attr("title", nat.dica);
      $("<span>").addClass("mesa-legenda-linha").css("background", nat.cor).appendTo($item);
      $("<span>").text(nat.rotulo + " (" + porNatureza[nat.chave] + ")").appendTo($item);
      $nat.append($item);
    });

    // Com exatamente duas entidades em foco a seleção vira par de trabalho: os mesmos
    // "Relacionar" e "Fundir" que a gaveta individual oferece, mas aplicáveis direto
    // entre as duas — sem o modo "clique no segundo nó" nem procurar o destino no select.
    if (escolhidos.length === 2) {
      var parA = escolhidos[0], parB = escolhidos[1];
      if (parA.kind !== "doc" && parB.kind !== "doc" && parA.kind !== "fuzzy" && parB.kind !== "fuzzy") {
        $("<button>").addClass("btn btn-primary").html(window.Icones.html("link") + " Relacionar")
          .attr("title", "Cria um vínculo entre as duas entidades em foco")
          .on("click", function () { modoLigacao = parA; concluirLigacao(parB); }).appendTo($acoes);
      }
      if (parA.kind === "canonica" && parB.kind === "canonica" &&
          parA.dado.tp_entidade === parB.dado.tp_entidade) {
        $("<button>").addClass("btn btn-secondary").html(window.Icones.html("fundir") + " Fundir")
          .attr("title", "Funde «" + truncar(parB.dado.no_entidade, 40) + "» em «" + truncar(parA.dado.no_entidade, 40) +
            "»: evidências, identificadores e relações passam para a primeira (que foi clicada antes)")
          .on("click", function () { abrirFusaoPar(parB, parA); }).appendTo($acoes);
      }
    }

    $("<button>").addClass("btn btn-secondary").html(window.Icones.html("fechar") + " Limpar foco")
      .on("click", fecharModalNo).appendTo($acoes);

    dicaSelecao("Shift+clique põe ou tira um nó do foco; clique sem Shift recomeça a seleção.");
  }

  function renderRelacoesDoNo(n, $corpo) {
    var doNo = arestas.filter(function (a) { return a.a === n.id || a.b === n.id; });
    if (!doNo.length) return;
    // Seção colapsável; com muitas relações (nó-cabeça) ela abre recolhida e ganha um campo de
    // filtro, para não virar uma parede de linhas que empurra as ações para fora da tela.
    var muitas = doNo.length > 8;
    var $sec = novaSecao($corpo, "Relações", doNo.length, doNo.length > 12);
    var $lista = $("<div>").appendTo($sec);
    if (muitas) {
      var $busca = $("<input>").attr({ type: "search", placeholder: "Filtrar relações…" })
        .addClass("mesa-gaveta-busca").prependTo($sec);
      $busca.on("input", function () {
        var q = this.value.trim().toLowerCase();
        if (q) { $sec.closest(".mesa-gaveta-secao").removeClass("recolhida"); }
        $lista.children().each(function () {
          this.style.display = ($(this).text().toLowerCase().indexOf(q) !== -1) ? "" : "none";
        });
      });
    }
    doNo.forEach(function (a) {
      var outro = noPorId[a.a === n.id ? a.b : a.a];
      var direcao = a.a === n.id ? "→" : "←";
      var $item = $("<div>").addClass("mesa-relacao-item");
      var texto = direcao + " " + a.tipo + " " + direcao + " " + truncar(outro.rotulo, 50);
      if (a.dado.tx_detalhe) texto += " (" + a.dado.tx_detalhe + ")";
      $("<span>").text(texto).appendTo($item);
      if (a.sugerida) $("<span>").addClass("badge badge-rodando").text("sugestão").appendTo($item);
      if (a.kind === "relacao") {
        if (a.sugerida) {
          $("<button>").addClass("mesa-relacao-btn").attr({ title: "Confirmar relação", "aria-label": "Confirmar relação" }).html(window.Icones.html("check"))
            .on("click", function () { revisarRelacao(a.dado.id_relacao, "confirmada"); }).appendTo($item);
          $("<button>").addClass("mesa-relacao-btn").attr({ title: "Descartar relação", "aria-label": "Descartar relação" }).html(window.Icones.html("fechar"))
            .on("click", function () { revisarRelacao(a.dado.id_relacao, "descartada"); }).appendTo($item);
        }
        $("<button>").addClass("mesa-relacao-btn").attr({ title: "Apagar relação", "aria-label": "Apagar relação" }).html(window.Icones.html("apagar"))
          .on("click", function () { apagarRelacao("/api/investigacao/relacoes/" + a.dado.id_relacao); }).appendTo($item);
      } else if (a.kind === "relacao_canonica") {
        $("<button>").addClass("mesa-relacao-btn").attr({ title: "Apagar relação", "aria-label": "Apagar relação" }).html(window.Icones.html("apagar"))
          .on("click", function () { apagarRelacao("/api/investigacao/canonicas/relacoes/" + a.dado.id_relacao); }).appendTo($item);
      }
      $lista.append($item);
    });
  }

  // Gaveta de uma aresta (clique na linha). Hoje a aresta só tinha o <title> no hover; o dado
  // completo (detalhe, justificativa, quanto/quantos, origem) já vem no payload do /grafo e só
  // aparecia no i2. Aqui ele chega na tela.
  function linhaPontaNo($corpo, rotulo, n) {
    $("<dt>").text(rotulo).appendTo($corpo);
    var $dd = $("<dd>").appendTo($corpo);
    var $item = $("<span>").addClass("mesa-relacao-item");
    $("<span>").addClass("mesa-selecao-cor").css("background", corTipo(n.tipo))
      .attr("title", ROTULOS_TIPO[n.tipo] || n.tipo).appendTo($item);
    var $link = $("<a>").attr("href", "#").text(truncar(n.rotulo, 44));
    $link.on("click", function (ev) { ev.preventDefault(); selecionarNo(n); });
    $dd.append($item.append($link));
  }

  // Destaca só os dois nós-ponta e a própria aresta (mais fechado que o foco de vizinhança).
  function focarAresta(a) {
    svg.classList.add("mesa-grafo-com-foco");
    var ponta = {}; ponta[a.a] = true; ponta[a.b] = true;
    nos.forEach(function (n) {
      if (!n._grupo) return;
      var dentro = !!ponta[n.id];
      n._grupo.classList.toggle("fora-do-foco", !dentro);
      n._grupo.classList.toggle("no-focado", dentro);
      if (n._rotulo) n._rotulo.classList.toggle("rotulo-visivel", dentro);
    });
    arestas.forEach(function (e) {
      var ligada = (e.a === a.a && e.b === a.b) || (e.a === a.b && e.b === a.a);
      if (e._el) e._el.classList.toggle("fora-do-foco", !ligada);
      if (e._rotulo) e._rotulo.classList.toggle("fora-do-foco", !ligada);
    });
  }

  function abrirGavetaAresta(a) {
    var na = noPorId[a.a], nb = noPorId[a.b];
    if (!na || !nb) return;
    gavetaArestaAtual = a;
    var $corpo = $("#no-corpo").empty();
    var $acoes = $("#no-acoes").empty();
    tituloGavetaSimples("Relação");

    linhaPontaNo($corpo, "De", na);
    linhaPontaNo($corpo, "Para", nb);
    var nat = naturezaAresta(a);
    var d = a.dado || {};
    linhaDetalhe($corpo, "Natureza", nat.rotulo);
    linhaDetalhe($corpo, "Tipo do vínculo", a.tipo);
    if (d.tx_detalhe) linhaDetalhe($corpo, "Detalhe", d.tx_detalhe);
    if (d.tx_justificativa) linhaDetalhe($corpo, "Justificativa da IA", d.tx_justificativa);
    var origem = a.sugerida ? "Sugestão da IA (não confirmada)"
      : a.kind === "evidencia" ? "Evidência do registro marcado"
      : a.kind === "doc_rel" ? "Análise de documento (IA)"
      : d.in_automatico ? "Automática (identificação de pessoas)"
      : "Manual";
    linhaDetalhe($corpo, "Origem do vínculo", origem);
    if (d.criado_por) linhaDetalhe($corpo, "Criado por", d.criado_por);
    if (d.dt_criacao) linhaDetalhe($corpo, "Criado em", formatarDataHora(d.dt_criacao));
    if (a.sugerida) {
      $("<dt>").text("Status").appendTo($corpo);
      $("<dd>").append($("<span>").addClass("badge badge-rodando").text("Sugestão da IA — aguardando revisão")).appendTo($corpo);
    }

    if (a.kind === "relacao") {
      if (a.sugerida) {
        $("<button>").addClass("btn btn-primary").html(window.Icones.html("check") + " Confirmar")
          .on("click", function () { revisarRelacao(a.dado.id_relacao, "confirmada"); }).appendTo($acoes);
        $("<button>").addClass("btn btn-secondary").html(window.Icones.html("fechar") + " Descartar")
          .on("click", function () { revisarRelacao(a.dado.id_relacao, "descartada"); }).appendTo($acoes);
      } else {
        $("<button>").addClass("btn btn-secondary").html(window.Icones.html("apagar") + " Apagar")
          .on("click", function () { apagarRelacao("/api/investigacao/relacoes/" + a.dado.id_relacao); }).appendTo($acoes);
      }
    } else if (a.kind === "relacao_canonica") {
      $("<button>").addClass("btn btn-secondary").html(window.Icones.html("apagar") + " Apagar")
        .on("click", function () { apagarRelacao("/api/investigacao/canonicas/relacoes/" + a.dado.id_relacao); }).appendTo($acoes);
    } else {
      dicaSelecao(a.kind === "evidencia"
        ? "Vínculo de evidência: a canônica é comprovada por este registro. Remova pela gaveta da canônica."
        : "Aresta da análise de documentos — a fonte da verdade é o documento, não a mesa.");
    }

    $drawerNo.removeClass("hidden");
    focarAresta(a);
    atualizarBarraIsolamento();
    $("#no-fechar").trigger("focus");
  }

  function fecharModalNo() {
    var estavaAberta = !$drawerNo.hasClass("hidden");
    $drawerNo.addClass("hidden");
    gavetaArestaAtual = null;
    focados = [];
    aplicarFoco();
    atualizarBarraIsolamento();
    atualizarModoRotulos();
    if (estavaAberta) {
      var alvo = focoAntesGaveta;
      focoAntesGaveta = null;
      if (alvo && alvo.focus && document.contains(alvo)) {
        try { alvo.focus(); } catch (e) { /* elemento saiu da página — foco vai pro body mesmo */ }
      }
    }
  }

  // Confirmação em modal (js/dialogo.js) — não usa os diálogos nativos do navegador, que
  // podem ser suprimidos ("impedir novas caixas de diálogo") e travar as ações sem aviso.
  function confirmar(mensagem, aoConfirmar, rotuloOk) {
    Dialogo.confirmar({ titulo: "Confirmar", mensagem: mensagem, rotuloOk: rotuloOk || "Apagar", perigoso: true })
      .then(function (ok) { if (ok && aoConfirmar) aoConfirmar(); });
  }

  function mostrarErroAjax(xhr) {
    Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao completar a ação.");
  }

  var ROTULO_REVISAO = { confirmada: "Confirmada.", descartada: "Descartada." };

  function revisarEntidade(idEntidade, revisao) {
    var $botoes = $("#no-acoes button").prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/entidades/" + idEntidade + "/revisao",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({ revisao: revisao })
    })
      .done(function () {
        Toast.sucesso(ROTULO_REVISAO[revisao] || "Sugestão revisada.");
        fecharModalNo(); carregarGrafo();
      })
      .fail(function (xhr) {
        $botoes.prop("disabled", false);
        mostrarErroAjax(xhr);
      });
  }

  function revisarRelacao(idRelacao, revisao) {
    $.ajax({
      url: "/api/investigacao/relacoes/" + idRelacao + "/revisao",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({ revisao: revisao })
    })
      .done(function () {
        Toast.sucesso(ROTULO_REVISAO[revisao] || "Sugestão revisada.");
        fecharModalNo(); carregarGrafo();
      })
      .fail(mostrarErroAjax);
  }

  function apagarRelacao(url) {
    confirmar("Apagar esta relação?", function () {
      $.ajax({ url: url, method: "DELETE" })
        .done(function () { fecharModalNo(); carregarGrafo(); })
        .fail(mostrarErroAjax);
    });
  }

  function editarComentario(e) {
    Dialogo.prompt({
      titulo: "Editar comentário",
      mensagem: "Comentário da entidade:",
      valor: e.tx_comentario || "",
      multilinha: true, maxlength: 2000, rotuloOk: "Salvar"
    }).then(function (novo) {
      if (novo === null || !$.trim(novo)) return;
      $.ajax({
        url: "/api/investigacao/entidades/" + e.id_entidade + "/comentario",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({ comentario: $.trim(novo) })
      })
        .done(function () {
          Toast.sucesso("Comentário atualizado.");
          fecharModalNo(); carregarGrafo();
        })
        .fail(mostrarErroAjax);
    });
  }

  function apagarEntidade(idEntidade) {
    confirmar("Apagar esta entidade e as relações dela?", function () {
      $.ajax({ url: "/api/investigacao/entidades/" + idEntidade, method: "DELETE" })
        .done(function () { fecharModalNo(); carregarGrafo(); })
        .fail(mostrarErroAjax);
    });
  }

  function apagarCanonica(idCanonica) {
    confirmar("Apagar esta entidade canônica, as evidências e as relações dela?", function () {
      $.ajax({ url: "/api/investigacao/canonicas/" + idCanonica, method: "DELETE" })
        .done(function () { fecharModalNo(); carregarGrafo(); })
        .fail(mostrarErroAjax);
    });
  }

  // ---------- fusão de canônicas ----------

  var fusaoPendente = null;

  function abrirFusaoCanonica(c) {
    var $select = $("#fusao-destino-select").empty();
    (dados.canonicas || [])
      .filter(function (o) { return o.tp_entidade === c.tp_entidade && o.id_canonica !== c.id_canonica; })
      .sort(function (a, b) { return a.no_entidade.localeCompare(b.no_entidade); })
      .forEach(function (o) {
        $("<option>").val(o.id_canonica).text(o.no_entidade + (o.nu_chave ? " (" + o.nu_chave + ")" : "")).appendTo($select);
      });
    if (!$select.children().length) {
      Toast.aviso("Não há outra entidade do tipo \"" + (ROTULOS_TIPO[c.tp_entidade] || c.tp_entidade) + "\" para fundir.");
      return;
    }
    $("#fusao-origem-nome").text(c.no_entidade);
    $("#fusao-status").text("").removeClass("error");
    fusaoPendente = c;
    fecharModalNo();
    $("#modal-fusao").removeClass("hidden");
  }

  // Fusão do par em foco (gaveta de multisseleção): mesmo modal, mas o destino é fixo —
  // o outro nó do par, já escolhido — então o select recebe uma opção só, pré-marcada.
  function abrirFusaoPar(origem, destino) {
    var d = destino.dado;
    $("#fusao-destino-select").empty();
    $("<option>").val(d.id_canonica).text(d.no_entidade + (d.nu_chave ? " (" + d.nu_chave + ")" : ""))
      .appendTo("#fusao-destino-select");
    $("#fusao-origem-nome").text(origem.dado.no_entidade);
    $("#fusao-status").text("").removeClass("error");
    fusaoPendente = origem.dado;
    fecharModalNo();
    $("#modal-fusao").removeClass("hidden");
  }

  $("#fusao-confirmar-btn").on("click", function () {
    if (!fusaoPendente) return;
    var destino = parseInt($("#fusao-destino-select").val(), 10);
    if (!destino) return;
    var nomeDestino = $("#fusao-destino-select option:selected").text();
    var origem = fusaoPendente;
    confirmar(
      "Fundir «" + origem.no_entidade + "» em «" + nomeDestino + "»? " +
      "As evidências, identificadores e relações passam para o destino, e a origem some. Não pode ser desfeito.",
      function () {
        var $btn = $("#fusao-confirmar-btn").prop("disabled", true);
        $.ajax({
          url: "/api/investigacao/canonicas/" + origem.id_canonica + "/fundir",
          method: "POST", contentType: "application/json",
          data: JSON.stringify({ id_canonica_destino: destino })
        })
          .done(function (r) {
            Toast.sucesso(
              "«" + origem.no_entidade + "» fundida em «" + nomeDestino + "» (" +
              r.evidencias + " evidência(s), " + r.identificadores + " identificador(es), " +
              r.relacoes + " relação(ões))."
            );
            fusaoPendente = null;
            $("#modal-fusao").addClass("hidden");
            carregarGrafo();
          })
          .fail(function (xhr) {
            Toast.erro((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao fundir as entidades.");
          })
          .always(function () { $btn.prop("disabled", false); });
      },
      "Fundir"
    );
  });

  // ---------- modo "Relacionar" (clicar em dois nós) ----------

  function iniciarLigacao(n) {
    modoLigacao = n;
    fecharModalNo();
    $("#mesa-modo-ligacao").removeClass("hidden")
      .text("Modo relação: «" + truncar(n.rotulo, 40) + "» → clique no segundo nó, ou pressione Esc para cancelar.");
  }

  function cancelarLigacao() {
    modoLigacao = null;
    $("#mesa-modo-ligacao").addClass("hidden");
  }

  function concluirLigacao(destino) {
    var origem = modoLigacao;
    cancelarLigacao();
    if (!origem || origem.id === destino.id) return;

    // Nó de documento não tem linha no banco — a relação nasceria órfã e a mesa não tem onde
    // gravá-la. Quem quer ligar uma pessoa citada em documento deve promovê-la a canônica (gaveta).
    if (origem.kind === "doc" || destino.kind === "doc") {
      Toast.aviso("Nó de documento não recebe relação manual. Abra a pessoa citada e crie a "
        + "entidade canônica dela — aí sim dá para relacioná-la.");
      return;
    }

    // O grupo fuzzy é soma visual de nós de nome parecido: a relação precisa sair de um nó
    // de verdade, com linha no banco. Desfaz o grupo (só na tela) e liga quem precisa.
    if (origem.kind === "fuzzy" || destino.kind === "fuzzy") {
      Toast.aviso("Isso é um grupo visual, não um nó de verdade. Desfaça o grupo na tela e relacione o nó de verdade.");
      return;
    }

    // entidade → canônica (ou vice-versa) vira EVIDÊNCIA: o registro da entidade passa a
    // evidenciar a canônica — semanticamente é isso que liga as duas camadas
    if (origem.kind !== destino.kind) {
      var canonica = origem.kind === "canonica" ? origem : destino;
      var entidade = origem.kind === "entidade" ? origem : destino;
      $.ajax({
        url: "/api/investigacao/canonicas/" + canonica.dado.id_canonica + "/evidencias",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({
          tp_origem: entidade.dado.tp_origem,
          id_origem: entidade.dado.id_origem,
          detalhe: "ligado manualmente na mesa"
        })
      }).done(carregarGrafo)
        .fail(function (xhr) {
          $statusLabel.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao ligar os nós.");
        });
      return;
    }

    ligacaoPendente = { origem: origem, destino: destino };
    $("#relacao-resumo").text("«" + truncar(origem.rotulo, 40) + "» → «" + truncar(destino.rotulo, 40) + "»");
    $("#relacao-tipo-input").val("");
    $("#relacao-status").text("").removeClass("error");
    $("#modal-relacao").removeClass("hidden");
    $("#relacao-tipo-input").trigger("focus");
  }

  var ligacaoPendente = null;

  $("#relacao-salvar-btn").on("click", function () {
    if (!ligacaoPendente) return;
    var tipo = $.trim($("#relacao-tipo-input").val());
    if (!tipo) {
      $("#relacao-status").text("Informe o tipo do vínculo.").addClass("error");
      return;
    }
    var ehCanonica = ligacaoPendente.origem.kind === "canonica";
    var corpo = ehCanonica
      ? {
          id_inquerito: parseInt(idInquerito, 10),
          id_canonica_origem: ligacaoPendente.origem.dado.id_canonica,
          id_canonica_destino: ligacaoPendente.destino.dado.id_canonica,
          tipo_relacao: tipo
        }
      : {
          id_inquerito: parseInt(idInquerito, 10),
          id_entidade_origem: ligacaoPendente.origem.dado.id_entidade,
          id_entidade_destino: ligacaoPendente.destino.dado.id_entidade,
          tipo_relacao: tipo
        };
    $.ajax({
      url: ehCanonica ? "/api/investigacao/canonicas/relacoes" : "/api/investigacao/relacoes",
      method: "POST", contentType: "application/json", data: JSON.stringify(corpo)
    })
      .done(function () {
        ligacaoPendente = null;
        $("#modal-relacao").addClass("hidden");
        carregarGrafo();
      })
      .fail(function (xhr) {
        $("#relacao-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar a relação.").addClass("error");
      });
  });

  $(document).on("keydown", function (evt) {
    if (evt.key === "Escape") {
      cancelarLigacao();
      fecharModalNo();   // a gaveta não tem overlay pra clicar fora: Esc é a saída
      $("#mesa-filtros").removeAttr("open");
      $("#ia-dropdown-menu, #export-dropdown-menu, #visoes-dropdown-menu").addClass("hidden");
      $("#ia-dropdown, #export-dropdown, #visoes-dropdown").removeClass("aberto");
      $("#modal-analise").addClass("hidden");
      desligarSelecaoArea();
    }
  });

  // ---------- canônica manual ----------

  $("#mesa-nova-canonica-btn").on("click", function () {
    $("#canonica-nome-input").val("");
    $("#canonica-obs-input").val("");
    $("#canonica-status").text("").removeClass("error");
    $("#modal-canonica").removeClass("hidden");
  });

  $("#canonica-salvar-btn").on("click", function () {
    var nome = $.trim($("#canonica-nome-input").val());
    if (!nome) {
      $("#canonica-status").text("Informe o nome.").addClass("error");
      return;
    }
    $.ajax({
      url: "/api/investigacao/canonicas",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: parseInt(idInquerito, 10),
        tp_entidade: $("#canonica-tipo-select").val(),
        nome: nome,
        observacao: $.trim($("#canonica-obs-input").val()) || null
      })
    })
      .done(function () {
        $("#modal-canonica").addClass("hidden");
        carregarGrafo();
      })
      .fail(function (xhr) {
        $("#canonica-status").text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar.").addClass("error");
      });
  });

  // ---------- identificação de pessoas (determinística, sem IA) ----------
  // Roda sozinha ao final de cada extração; o botão só refaz para inquéritos cujas
  // extrações são anteriores a essa etapa (ou depois de uma nova detecção de PIX).

  $("#identificar-pessoas-btn").on("click", function () {
    var $btn = $(this).prop("disabled", true).html('<span class="spinner"></span> Identificando…');
    $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/identificar-pessoas", method: "POST" })
      .done(function (r) {
        // Os outros tipos de nó só entram na frase quando existem: num inquérito só de celular
        // a mensagem não deve falar de linha telefônica nem de veículo.
        var outros = [
          [r.telefones_criados, "linha(s) telefônica(s)"],
          [r.organizacoes_criadas, "empresa(s)"],
          [r.dispositivos_criados, "aparelho(s)"],
          [r.veiculos_criados, "veículo(s)"],
          [r.locais_criados, "local(is)"]
        ].filter(function (par) { return Number(par[0]) > 0; })
         .map(function (par) { return par[0] + " " + par[1]; });
        var frase = "Identificação de pessoas: " + r.pessoas + " pessoa(s) na mesa (" + r.pessoas_criadas +
          " nova(s)), " + r.evidencias + " evidência(s) e " + r.relacoes + " relação(ões) novas em " +
          r.extracoes + " extração(ões)." +
          (outros.length ? " Também entraram: " + outros.join(", ") + "." : "");
        $statusLabel.text(frase);
        Toast.sucesso("Pessoas identificadas: " + r.pessoas + " na mesa, " + r.relacoes + " relação(ões) novas.");
        carregarGrafo();
      })
      .fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao identificar as pessoas.";
        $statusLabel.text(msg);
        Toast.erro(msg);
      })
      .always(function () {
        $btn.prop("disabled", false).html(window.Icones.html("pessoa") + " Identificar pessoas");
      });
  });

  // ---------- detecção de padrões ----------

  var pollDeteccao = null;

  $("#detectar-padroes-btn").on("click", function () {
    var $btn = $(this).prop("disabled", true).text("Detectando…");
    $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/deteccao", method: "POST" })
      .done(function () { monitorarDeteccao(); })
      .fail(function (xhr) {
        $statusLabel.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a detecção.");
        $btn.prop("disabled", false).html(window.Icones.html("alerta") + " Detectar padrões");
      });
  });

  function monitorarDeteccao() {
    clearTimeout(pollDeteccao);
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/deteccao").done(function (lista) {
      var ultima = lista[0];
      var $btn = $("#detectar-padroes-btn");
      if (!ultima || ultima.cd_status === "concluido" || ultima.cd_status === "erro") {
        $btn.prop("disabled", false).html(window.Icones.html("alerta") + " Detectar padrões");
      }
      if (!ultima) return;
      if (ultima.cd_status === "pendente" || ultima.cd_status === "processando") {
        $btn.prop("disabled", true).text("Detectando…");
        pollDeteccao = setTimeout(monitorarDeteccao, 2500);
      } else if (ultima.cd_status === "concluido") {
        // a linha de status é fácil de perder com o usuário em outra aba — o toast avisa
        Toast.sucesso("Detecção de padrões concluída.");
        abrirDeteccoes(ultima);
      } else {
        $statusLabel.text("Erro na detecção: " + (ultima.tx_erro || "desconhecido"));
        Toast.erro("Erro na detecção de padrões: " + (ultima.tx_erro || "desconhecido"));
      }
    }).fail(function () {
      // sem isto o botão ficava travado em "Detectando…" para sempre se o polling falhasse
      $("#detectar-padroes-btn").prop("disabled", false).html(window.Icones.html("alerta") + " Detectar padrões");
      Toast.erro("Não foi possível acompanhar a detecção de padrões.");
    });
  }

  function abrirDeteccoes(execucao) {
    var $corpo = $("#deteccoes-corpo").empty().text("Carregando…");
    $("#modal-deteccoes").removeClass("hidden");
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/deteccoes?execucao=" + execucao.id_execucao)
      .done(function (deteccoes) {
        $corpo.empty();
        if (execucao.tx_erro) {
          $("<p>").addClass("form-status error").text("Alguns padrões falharam: " + execucao.tx_erro).appendTo($corpo);
        }
        if (!deteccoes.length) {
          $("<p>").addClass("empty-hint").text("Nenhum padrão disparou neste inquérito.").appendTo($corpo);
          return;
        }
        var porPadrao = {};
        deteccoes.forEach(function (d) {
          (porPadrao[d.no_padrao] = porPadrao[d.no_padrao] || []).push(d);
        });
        Object.keys(porPadrao).forEach(function (nome) {
          var grupo = porPadrao[nome];
          var $grupo = $("<div>").addClass("mesa-deteccao-grupo").appendTo($corpo);
          var $cabeca = $("<div>").addClass("mesa-deteccao-cabeca").appendTo($grupo);
          $("<strong>").text(nome).appendTo($cabeca);
          if (grupo[0].no_categoria) $("<span>").addClass("badge badge-neutro").text(grupo[0].no_categoria).appendTo($cabeca);
          $("<span>").addClass("badge badge-ok").text(grupo.length + " achado(s)").appendTo($cabeca);

          grupo.forEach(function (d) {
            var $item = $("<div>").addClass("mesa-deteccao-item").appendTo($grupo);
            var $texto = $("<div>").addClass("mesa-deteccao-texto").appendTo($item);
            $("<span>").text(truncar(d.tx_trecho, 220)).appendTo($texto);
            $("<small>").text((ROTULOS_TIPO[d.tp_origem] || d.tp_origem) + " · " + formatarDataHora(d.dt_ocorrencia)).appendTo($texto);
            $("<button>").addClass("btn btn-secondary").html(window.Icones.html("pino") + " Criar entidade")
              .on("click", function () { criarEntidadeDeDeteccao(d, $(this)); }).appendTo($item);
          });
        });
      })
      .fail(function (xhr) {
        // sem este fail o modal ficava em "Carregando…" para sempre se a lista falhasse
        $corpo.empty();
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao buscar os padrões detectados.";
        $("<p>").addClass("form-status error").text(msg).appendTo($corpo);
        $("<button>").attr("type", "button").addClass("btn btn-secondary").html(window.Icones.html("recarregar") + " Tentar de novo")
          .on("click", function () { abrirDeteccoes(execucao); }).appendTo($corpo);
      });
  }

  function criarEntidadeDeDeteccao(d, $btn) {
    $btn.prop("disabled", true);
    $.ajax({
      url: "/api/investigacao/entidades",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({
        id_inquerito: parseInt(idInquerito, 10),
        tp_origem: d.tp_origem,
        id_origem: d.id_origem,
        comentario: "[padrão: " + d.no_padrao + "] " + (d.tx_trecho || "")
      })
    })
      .done(function () {
        $btn.html(window.Icones.html("check") + " Criada");
        carregarGrafo();
      })
      .fail(function (xhr) {
        $btn.prop("disabled", false);
        $statusLabel.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao criar a entidade.");
      });
  }

  // ---------- busca automática (IA) ----------

  var POLL_INTERVALO = 3000;
  var pollTimer = null;
  var ROTULO_BUSCA = { prompts: "busca por prompts", relacionar_manuais: "relacionar manuais" };

  // travado = tem IA rodando (desabilita os dois botões). tpAtivo diz qual busca está rodando,
  // pra pôr o spinner + texto de "carregando" no botão certo — sem isso, clicar em "Relacionar
  // manuais" não dava nenhum retorno visual e parecia que nada tinha acontecido.
  // iaBloqueada é o flag da delegacia (ver conferirIaDisponivel): o polling não pode reabilitar os
  // botões ao ver que nada está rodando, senão desfaz o bloqueio que chegou antes dele.
  var iaBloqueada = false;

  function botoesBusca(travado, tpAtivo) {
    var $buscar = $("#buscar-automatico-btn").prop("disabled", travado || iaBloqueada);
    var $relac = $("#relacionar-manuais-btn").prop("disabled", travado || iaBloqueada);
    $buscar.html(travado && tpAtivo !== "relacionar_manuais"
      ? '<span class="spinner"></span> Buscando…' : window.Icones.html("busca") + " Buscar entidades (IA)");
    $relac.html(travado && tpAtivo === "relacionar_manuais"
      ? '<span class="spinner"></span> Relacionando…' : window.Icones.html("link") + " Relacionar manuais (IA)");
  }

  // A IA só merece toast de conclusão quando NÓS a vimos rodar: no primeiro acesso da página o
  // polling também encontra a última execução "concluída", e notificar isso a cada F5 seria ruído.
  var iaVistaRodando = false;
  var pollIaFalhou = false;

  function monitorarBusca() {
    clearTimeout(pollTimer);
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/busca-automatica").done(function (lista) {
      pollIaFalhou = false;
      var ultima = lista[0];
      if (!ultima || ultima.cd_status === "concluido" || ultima.cd_status === "erro") {
        botoesBusca(false);
      }
      if (!ultima) { iaVistaRodando = false; return; }

      if (ultima.cd_status === "pendente" || ultima.cd_status === "processando") {
        iaVistaRodando = true;
        $statusLabel.text("IA rodando (" + (ROTULO_BUSCA[ultima.tp_busca] || ultima.tp_busca) +
          ", iniciada por " + (ultima.iniciado_por || "—") + ")…");
        botoesBusca(true, ultima.tp_busca);
        pollTimer = setTimeout(monitorarBusca, POLL_INTERVALO);
      } else if (ultima.cd_status === "concluido") {
        var frase = "Última IA (" + (ROTULO_BUSCA[ultima.tp_busca] || ultima.tp_busca) + "): " +
          (ultima.qt_entidades_criadas || 0) + " entidade(s), " +
          (ultima.qt_relacoes_criadas || 0) + " relação(ões) sugeridas — " + formatarDataHora(ultima.dt_fim);
        $statusLabel.text(frase);
        if (iaVistaRodando) {
          Toast.sucesso("IA concluída: " + (ultima.qt_entidades_criadas || 0) + " entidade(s), " +
            (ultima.qt_relacoes_criadas || 0) + " relação(ões) sugeridas.");
        }
        iaVistaRodando = false;
        carregarGrafo();
      } else if (ultima.cd_status === "erro") {
        $statusLabel.text("Erro na última busca: " + (ultima.tx_erro || "desconhecido"));
        if (iaVistaRodando) Toast.erro("A busca da IA terminou com erro: " + (ultima.tx_erro || "desconhecido"));
        iaVistaRodando = false;
      }
    }).fail(function () {
      // rede caiu um instante não pode travar os botões nem silenciar o acompanhamento —
      // avisa uma vez e tenta de novo mais devagar
      if (!pollIaFalhou) {
        pollIaFalhou = true;
        Toast.erro("Sem conexão com o servidor para acompanhar a IA — tentando de novo…");
      }
      pollTimer = setTimeout(monitorarBusca, 10000);
    });
  }

  function dispararBusca(tpBusca) {
    botoesBusca(true, tpBusca);
    $statusLabel.text("Iniciando " + (ROTULO_BUSCA[tpBusca] || tpBusca) + "…");
    $.ajax({
      url: "/api/investigacao/inqueritos/" + idInquerito + "/busca-automatica",
      method: "POST", contentType: "application/json",
      data: JSON.stringify({ tp_busca: tpBusca })
    })
      .done(monitorarBusca)
      .fail(function (xhr) {
        $statusLabel.text((xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao iniciar a busca.");
        botoesBusca(false);
      });
  }

  $("#buscar-automatico-btn").on("click", function () { dispararBusca("prompts"); });
  $("#relacionar-manuais-btn").on("click", function () { dispararBusca("relacionar_manuais"); });

  /* As duas buscas chamam o provedor de IA, então passam pelo flag IN_IA_ATIVA da delegacia — de
   * quem está logado e do inquérito aberto (api/funcoes/ia_delegacia.py). Quem decide é o backend:
   * a tela só sonda uma rota .../ia/... e, no 403, trava os botões em vez de deixar o investigador
   * clicar para receber o erro. Mesmo padrão da bolha flutuante (js/utils/ia_flutuante.js). */
  function conferirIaDisponivel() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/ia/conversas")
      .fail(function (xhr) {
        if (!xhr || xhr.status !== 403) return;
        iaBloqueada = true;
        var motivo = (xhr.responseJSON && xhr.responseJSON.detail) || "IA desativada para esta delegacia.";
        $("#buscar-automatico-btn, #relacionar-manuais-btn")
          .prop("disabled", true)
          .css({ opacity: 0.4, cursor: "not-allowed" })
          .attr({ title: motivo, "aria-disabled": "true" });
        $statusLabel.text(motivo);
        // opacity+title sozinho é fácil de perder (e o title não existe no touch): o toast avisa
        Toast.aviso(motivo);
      });
  }

  // ---------- filtros, busca e período ----------

  $("#filtro-sugeridas, #filtro-confirmadas, #filtro-canonicas-com, #filtro-canonicas-sem, #filtro-documentos").on("change", atualizar);

  $("#filtro-grau-min").on("change", atualizar);

  $("#agrupar-entidades").on("change", function () {
    // Desligar o global limpa os "desfeitos à mão": na próxima vez que ligar, tudo reagrupa
    // do zero — sem isso um grupo desfeito uma vez nunca mais juntava.
    if (!this.checked) fuzzyIgnorados = {};
    atualizar();
  });

  $("#mesa-destacar-elos").on("change", function () {
    destacarElos = this.checked;
    // Só liga/desliga o destaque dos elos — o recorte não muda, então redesenha direto, sem
    // refazer filtros/layout (o Tarjan é cacheado pelo conjunto desenhado).
    renderGrafo();
  });

  $("#mesa-rotulos-check").on("change", function () {
    mostrarTodosRotulos = this.checked;
    atualizarModoRotulos();
  });

  var timerBusca = null;
  $("#mesa-busca-input").on("input", function () {
    termoBusca = $.trim($(this).val()).toLowerCase();
    // P0-5: realce imediato sem reconstruir o SVG (só classes/opacidade nos grupos já
    // desenhados) + lista clicável; o recorte com layout espera o debounce.
    realceBuscaRapida();
    atualizarListaBusca();
    clearTimeout(timerBusca);
    timerBusca = setTimeout(atualizar, 350);
  });

  // Teclado na busca: setas percorrem a lista de resultados, Enter abre o marcado (ou o
  // primeiro), Esc limpa o campo sem fechar a gaveta por trás (mesmo padrão de js/home.js).
  $("#mesa-busca-input").on("keydown", function (evt) {
    if (evt.key === "Escape") {
      if ($(this).val()) {
        $(this).val("");
        $(this).trigger("input");
        evt.stopPropagation();
      }
      return;
    }
    var $itens = $("#mesa-busca-resultados .mesa-busca-resultado");
    if (!$itens.length) return;
    if (evt.key === "ArrowDown" || evt.key === "ArrowUp") {
      evt.preventDefault();
      var atual = $itens.index($itens.filter(".kbd-ativo"));
      var prox = evt.key === "ArrowDown"
        ? Math.min(atual + 1, $itens.length - 1)
        : Math.max((atual === -1 ? 0 : atual) - 1, 0);
      $itens.removeClass("kbd-ativo").eq(prox).addClass("kbd-ativo")
        .each(function () { this.scrollIntoView({ block: "nearest" }); });
    } else if (evt.key === "Enter") {
      evt.preventDefault();
      var $marcado = $itens.filter(".kbd-ativo").first();
      ($marcado.length ? $marcado : $itens.first()).trigger("click");
    }
  });

  // P0-5: destaca quem casa a busca só mexendo em classe/opacidade — sem renderGrafo().
  function realceBuscaRapida() {
    if (!nos.length) return;
    nos.forEach(function (n) {
      if (!n._grupo || !n._forma) return;
      var ok = casaBusca(n);
      n._grupo.setAttribute("opacity", ok ? "1" : "0.15");
      n._forma.classList.toggle("achado", !!(termoBusca && ok));
      if (n._rotulo && termoBusca) n._rotulo.classList.toggle("rotulo-visivel", ok);
    });
  }

  // P1-6: lista os top 50 que casam a busca (no inquérito inteiro, não só no desenho),
  // com tipo e grau — clicar centra e abre a gaveta. Sem isto, achar "fulano" em 6k nós
  // era caçar um ponto semitransparente no meio do desenho.
  // A1: uma passada só de filter+sort (antes eram dois filters por tecla, fora do debounce).
  function atualizarListaBusca() {
    var $box = $("#mesa-busca-resultados");
    if (!$box.length) return;
    if (!termoBusca) { $box.addClass("hidden").empty(); return; }
    var todos = todosNos.filter(casaBusca)
      .sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); });
    var achados = todos.slice(0, 50);
    $box.empty();
    if (!achados.length) {
      $box.removeClass("hidden").append($("<div>").text("Nenhum nó casa a busca."));
      return;
    }
    $box.removeClass("hidden");
    $("<div>").addClass("mesa-busca-meta").text(
      achados.length.toLocaleString("pt-BR") + " resultado(s)" +
      (todos.length > 50 ? " — top 50 por ligações" : "") +
      " · clique para centralizar"
    ).appendTo($box);
    achados.forEach(function (n) {
      var $b = $("<button>").attr("type", "button").addClass("mesa-busca-resultado")
        .attr("title", (n.rotulo || n.id) + " — clique para centralizar e abrir")
        .on("click", function () { assegurarNoVisivel(n.id); });
      $("<span>").addClass("mesa-busca-cor").css("background", corTipo(n.tipo)).appendTo($b);
      $("<span>").text(truncar(n.rotulo || n.id, 44)).appendTo($b);
      $("<span>").addClass("mesa-busca-meta").text(
        (ROTULOS_TIPO[n.tipo] || n.tipo) + " · " + (n.grau || 0) + " rel."
      ).appendTo($b);
      $box.append($b);
    });
  }

  // Centraliza a viewport no nó (mantém o zoom) e abre a gaveta dele.
  function centralizarNo(n) {
    if (!n || n.x == null) return;
    vb.x = n.x - vb.w / 2;
    vb.y = n.y - vb.h / 2;
    aplicarViewBox();
    selecionarNo(n);
  }

  // Garante que o nó da lista de busca apareça: limpa isolamento/caminho e, se ele
  // estava fora do desenho por filtro de grau/tipo, afrouxa o grau e refaz — depois
  // centra e abre. Sem isto o clique na lista às vezes "não fazia nada".
  function assegurarNoVisivel(id) {
    var n = noPorId[id];
    if (!n) return;
    var visivel = idsVisiveis()[id];
    if (visivel) { centralizarNo(n); return; }
    isolamento = null;
    soCaminhoIds = null;
    filtroDocsIds = null;
    atualizarBarraIsolamento();
    var minRelAntes = lerMinRelacoes();
    if (minRelAntes > 0) $("#filtro-grau-min").val("0");
    atualizar();
    var tentativas = 0;
    (function esperar() {
      var alvo = noPorId[id];
      if ((idsVisiveis()[id] && alvo && alvo.x != null) || ++tentativas > 40) {
        if (idsVisiveis()[id]) centralizarNo(noPorId[id]);
        else Toast.aviso("O nó continua fora dos filtros atuais (tipo/período).");
        return;
      }
      setTimeout(esperar, 150);
    })();
  }

  // ---------- painel de fontes (as mídias do inquérito no desenho) ----------
  // Inventário do inquérito — aparelhos (extrações), RIFs, extratos do SIMBA, IPLs, IRPF,
  // IPEI, telefonia e documentos anexados — com uma caixa por item, no mesmo molde do painel
  // "Fontes" do chat de IA (js/ia.js). Tudo marcado (o padrão) — ou nada marcado — é o
  // inquérito inteiro; seleção parcial recarrega o grafo com ?fontes= e o servidor devolve
  // só o que nasce das fontes marcadas (entidades, evidências de canônicas e a camada de
  // documentos). Canônica criada à mão, sem evidência, nunca some do recorte.
  // apelido do card em destaque ("Celular do alvo · Aparelho Samsung"), o mesmo padrão do
  // título dos blocos em investigacao.js (tituloComApelido). Sem apelido, o rótulo de sempre.
  function rotuloFonteComApelido(x, rotulo) {
    var ap = x && x.no_apelido ? $.trim(String(x.no_apelido)) : "";
    return ap ? ap + " · " + rotulo : rotulo;
  }

  var FONTES_GRUPOS = [
    {
      tp: "extracao", rotulo: "Aparelhos (extrações)", icone: "aparelho",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/extracoes-lista"; },
      id: function (x) { return x.id_extracao; },
      rotuloItem: function (x) {
        return rotuloFonteComApelido(x, "Aparelho " + (x.no_dispositivo || x.no_fabricante || x.no_arquivo || x.id_extracao));
      }
    },
    {
      tp: "rif", rotulo: "RIFs", icone: "rif",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/rifs"; },
      id: function (x) { return x.id_rif; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "RIF " + (x.nu_rif || x.no_pasta || x.id_rif)); }
    },
    {
      tp: "simba", rotulo: "Extratos SIMBA", icone: "banco",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/extratos-simba"; },
      id: function (x) { return x.id_extrato; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "Extrato SIMBA " + (x.no_arquivo || x.id_extrato)); }
    },
    {
      tp: "ipl", rotulo: "IPLs (inquéritos em PDF)", icone: "ipl",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/pdfs"; },
      id: function (x) { return x.id_ipl; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "IPL " + (x.nu_ipl || x.no_arquivo || x.id_ipl)); }
    },
    {
      tp: "irpf", rotulo: "Declarações de IRPF", icone: "pix",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/declaracoes-irpf"; },
      id: function (x) { return x.id_irpf; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "IRPF " + (x.no_titular || x.no_arquivo || x.id_irpf)); }
    },
    {
      tp: "ipei", rotulo: "IPEIs (sigilo fiscal)", icone: "pdf",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/ipeis"; },
      id: function (x) { return x.id_ipei; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "IPEI " + (x.nu_ipei || x.no_arquivo || x.id_ipei)); }
    },
    {
      tp: "telefonia", rotulo: "Telefonia (operadoras)", icone: "ligacoes",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/telefonia"; },
      id: function (x) { return x.id_telefonia; },
      rotuloItem: function (x) { return rotuloFonteComApelido(x, "Telefonia " + (x.no_operadora || x.id_telefonia)); }
    },
    {
      tp: "documento", rotulo: "Documentos anexados", icone: "documentos",
      url: function (id) { return "/api/investigacao/inqueritos/" + id + "/documentos"; },
      id: function (x) { return x.id_documento; },
      rotuloItem: function (x) {
        return rotuloFonteComApelido(x, x.no_arquivo || ("Documento " + x.id_documento));
      },
      filtro: function (x) { return !x.in_ipl && !x.in_rif && !x.in_simba; },
      subgrupo: function (x) { return window.AnalyserUtils.categoriaArquivo(x.tp_arquivo, x.no_arquivo); }
    }
  ];
  var FONTES_LIMIAR_SUBGRUPO = 50;   // grupo com item demais ganha subgrupos recolhidos (docs por categoria)
  var fontesInv = null;              // [{valor:"tp:id", tp, rotulo, sub, idDoc}] — carregado uma vez
  var fontesCarregando = false;
  var fontesMarcadas = {};           // "tp:id" -> true
  var fontesBuscaFiltro = "";
  var fontesSubExpandidos = {};      // subgrupo -> true (só a categoria de documento tem sub)

  function iconeDoGrupoFontes(tp) {
    for (var i = 0; i < FONTES_GRUPOS.length; i++) if (FONTES_GRUPOS[i].tp === tp) return FONTES_GRUPOS[i].icone;
    return "documentos";
  }

  function recorteFontesCsv() {
    // Os dois extremos querem dizer "sem restrição": tudo marcado (o padrão) e nada marcado
    // devolvem null — igual ao painel de fontes do chat, que nesse caso considera o inquérito
    // inteiro (ver api funcoes/ia_utils.py).
    var total = fontesInv ? fontesInv.length : 0;
    var marcadas = Object.keys(fontesMarcadas);
    if (!total || !marcadas.length || marcadas.length === total) return null;
    return marcadas.join(",");
  }

  function carregarFontesInventario(aoTerminar) {
    if (fontesInv) { if (aoTerminar) aoTerminar(); return; }
    if (fontesCarregando) return;
    fontesCarregando = true;
    var pendentes = FONTES_GRUPOS.map(function (g) {
      // lista vazia no lugar da rejeição: um endpoint com erro não pode deixar o painel
      // inteiro sem fonte (mesma regra do chat de IA).
      return $.getJSON(g.url(idInquerito)).then(
        function (lista) { return { grupo: g, lista: window.AnalyserUtils.encurtarCaminhos(lista || []) }; },
        function () { return { grupo: g, lista: [] }; }
      );
    });
    $.when.apply($, pendentes).done(function () {
      var inv = [];
      var vistos = {};
      Array.prototype.slice.call(arguments).forEach(function (r) {
        var lista = r.grupo.filtro ? r.lista.filter(r.grupo.filtro) : r.lista;
        lista.forEach(function (x) {
          var valor = r.grupo.tp + ":" + r.grupo.id(x);
          if (vistos[valor] || r.grupo.id(x) == null) return;
          vistos[valor] = true;
          inv.push({
            valor: valor, tp: r.grupo.tp,
            sub: r.grupo.subgrupo ? r.grupo.subgrupo(x) : null,
            rotulo: r.grupo.rotuloItem(x),
            idDoc: r.grupo.tp === "documento" ? r.grupo.id(x) : null
          });
        });
      });
      fontesInv = inv;
      fontesCarregando = false;
      // Começa com tudo marcado (= inquérito inteiro). Com um recorte vindo da URL, só ele
      // fica marcado — a tela mostra o que o servidor realmente desenhou.
      fontesMarcadas = {};
      var ativo = recorteFontesServidor;
      if (ativo) {
        String(ativo).split(/\s*,\s*/).forEach(function (v) { if (vistos[v]) fontesMarcadas[v] = true; });
      } else {
        inv.forEach(function (f) { fontesMarcadas[f.valor] = true; });
      }
      if (aoTerminar) aoTerminar();
    }).fail(function () {
      fontesCarregando = false;
    });
  }

  function linhaFonte(f) {
    var $item = $("<div>").addClass("mesa-docs-item");
    $("<input>").attr("type", "checkbox").addClass("mesa-fonte-check")
      .attr("data-valor", f.valor)
      .prop("checked", !!fontesMarcadas[f.valor])
      .attr("title", "Marcar esta fonte para entrar no desenho")
      .on("change", function () {
        if (this.checked) fontesMarcadas[f.valor] = true;
        else delete fontesMarcadas[f.valor];
        sincronizarFontesPainel();
      }).appendTo($item);
    if (window.Icones) {
      try {
        $("<span>").addClass("mesa-fonte-icone").attr("aria-hidden", "true")
          .html(window.Icones.html(iconeDoGrupoFontes(f.tp))).appendTo($item);
      } catch (e) { /* ícone é enfeite — sem ele a linha continua legível */ }
    }
    $("<span>").addClass("mesa-docs-nome").text(f.rotulo).attr("title", f.rotulo).appendTo($item);
    if (f.tp === "documento" && f.idDoc != null) {
      var dd = "dd" + f.idDoc;
      if (noPorId[dd]) {
        $("<button>").attr("type", "button").addClass("btn btn-secondary btn-small").text("Isolar")
          .attr("title", "Desenha só este documento e a vizinhança dele")
          .on("click", function () {
            filtroDocsIds = null;
            isolamento = { raizes: [dd], saltos: 1 };
            soCaminhoIds = null;
            atualizar();
          }).appendTo($item);
        linkDocumento(f.idDoc, "Abrir").addClass("btn btn-secondary btn-small")
          .attr("title", "Abre o visualizador do documento").appendTo($item);
      }
    }
    return $item;
  }

  function marcarFontes($checks, marcar) {
    $checks.each(function () {
      this.checked = marcar;
      var valor = $(this).attr("data-valor");
      if (marcar) fontesMarcadas[valor] = true;
      else delete fontesMarcadas[valor];
    });
    sincronizarFontesPainel();
  }

  function renderFontesSubgrupos($box, itens, termo) {
    var ordem = [];
    var mapa = {};
    itens.forEach(function (f) {
      var sub = f.sub || "Outros";
      if (!mapa[sub]) { mapa[sub] = []; ordem.push(sub); }
      mapa[sub].push(f);
    });
    ordem.sort(function (a, b) { return a.localeCompare(b, "pt-BR", { numeric: true }); });
    ordem.forEach(function (sub) {
      var expandido = !!termo || !!fontesSubExpandidos[sub];
      var $sub = $("<div>").addClass("mesa-fonte-subgrupo").appendTo($box);
      var $cab = $("<div>").addClass("mesa-fonte-subgrupo-cab").appendTo($sub);
      var $toggle = $("<button>").attr("type", "button").addClass("mesa-fonte-subgrupo-toggle")
        .attr({ "aria-expanded": expandido ? "true" : "false",
                "aria-label": (expandido ? "Recolher " : "Expandir ") + sub })
        .text(expandido ? "▾" : "▸").appendTo($cab);
      var $check = $("<input>").attr("type", "checkbox").addClass("mesa-fonte-subgrupo-check")
        .attr("aria-label", "Marcar ou desmarcar: " + sub).appendTo($cab);
      $("<span>").text(sub).appendTo($cab);
      var $qt = $("<span>").addClass("mesa-docs-qt").appendTo($cab);
      var $itens = $("<div>").addClass("mesa-fonte-subgrupo-itens").toggle(expandido).appendTo($sub);
      mapa[sub].forEach(function (f) { $itens.append(linhaFonte(f)); });
      $check.on("change", function () { marcarFontes($itens.find(".mesa-fonte-check"), this.checked); });
      $toggle.on("click", function () {
        var abrir = !$itens.is(":visible");
        $itens.toggle(abrir);
        $toggle.text(abrir ? "▾" : "▸")
          .attr({ "aria-expanded": abrir ? "true" : "false",
                  "aria-label": (abrir ? "Recolher " : "Expandir ") + sub });
        fontesSubExpandidos[sub] = abrir;
      });
      $sub.data("qt-fontes", $qt);
    });
  }

  function renderFontesPainel() {
    var $painel = $("#mesa-fontes-painel");
    $("#mesa-fontes-total").text(String(fontesInv ? fontesInv.length : 0));
    if (!$painel.length || $painel.hasClass("hidden")) return;
    var $box = $("#mesa-fontes-lista").empty();
    if (!fontesInv) {
      $box.append($("<div>").addClass("mesa-docs-meta").text(
        fontesCarregando ? "Carregando fontes…" : "Não foi possível carregar as fontes."));
      atualizarMetaFontes();
      return;
    }
    var termo = (fontesBuscaFiltro || "").toLowerCase();
    var visiveis = 0;
    FONTES_GRUPOS.forEach(function (g) {
      var itens = fontesInv.filter(function (f) {
        return f.tp === g.tp && (!termo || f.rotulo.toLowerCase().indexOf(termo) !== -1);
      });
      if (!itens.length) return;
      visiveis += itens.length;
      var $grupo = $("<div>").addClass("mesa-fonte-grupo").appendTo($box);
      var $cab = $("<div>").addClass("mesa-fonte-grupo-cab").appendTo($grupo);
      var $check = $("<input>").attr("type", "checkbox").addClass("mesa-fonte-grupo-check")
        .attr("aria-label", "Marcar ou desmarcar: " + g.rotulo).appendTo($cab);
      $("<span>").addClass("mesa-fonte-grupo-nome").text(g.rotulo).appendTo($cab);
      var $qt = $("<span>").addClass("mesa-docs-qt").appendTo($cab);
      var $itensBox = $("<div>").appendTo($grupo);
      var usarSub = itens.length > FONTES_LIMIAR_SUBGRUPO
        && itens.some(function (f) { return f.sub; });
      if (usarSub) renderFontesSubgrupos($itensBox, itens, termo);
      else itens.forEach(function (f) { $itensBox.append(linhaFonte(f)); });
      $check.on("change", function () {
        marcarFontes($grupo.find(".mesa-fonte-check"), this.checked);
      });
      $grupo.data("qt-fontes", $qt);
    });
    if (!visiveis) {
      $box.append($("<div>").addClass("mesa-docs-meta").text(
        fontesInv.length ? "Nenhuma fonte com esse texto." : "Nenhuma fonte neste inquérito."));
    }
    sincronizarFontesPainel();
  }

  // As caixas de grupo/subgrupo/todas refletem (e mexem em) o que está à vista — mesma regra
  // do painel de fontes do chat: com filtro de texto ativo, marcar "Todas" marca o filtrado.
  function sincronizarFontesPainel() {
    $("#mesa-fontes-lista .mesa-fonte-subgrupo").each(function () {
      var $s = $(this);
      var $itens = $s.find(".mesa-fonte-check");
      var n = $itens.filter(":checked").length;
      $s.find(".mesa-fonte-subgrupo-check")
        .prop("checked", $itens.length > 0 && n === $itens.length)
        .prop("indeterminate", n > 0 && n < $itens.length);
      var $qt = $s.data("qt-fontes");
      if ($qt) $qt.text(n + "/" + $itens.length);
    });
    $("#mesa-fontes-lista .mesa-fonte-grupo").each(function () {
      var $g = $(this);
      var $itens = $g.find(".mesa-fonte-check");
      var n = $itens.filter(":checked").length;
      $g.find(".mesa-fonte-grupo-check")
        .prop("checked", $itens.length > 0 && n === $itens.length)
        .prop("indeterminate", n > 0 && n < $itens.length);
      var $qt = $g.data("qt-fontes");
      if ($qt) $qt.text(n + "/" + $itens.length);
    });
    var $visiveis = $("#mesa-fontes-lista .mesa-fonte-check");
    var marcadosVisiveis = $visiveis.filter(":checked").length;
    $("#mesa-fontes-todas")
      .prop("checked", $visiveis.length > 0 && marcadosVisiveis === $visiveis.length)
      .prop("indeterminate", marcadosVisiveis > 0 && marcadosVisiveis < $visiveis.length);
    atualizarMetaFontes();
  }

  function atualizarMetaFontes() {
    var total = fontesInv ? fontesInv.length : 0;
    var n = Object.keys(fontesMarcadas).length;
    var txt = "";
    if (total) {
      txt = (!n || n === total)
        ? "Todas as " + total + " fontes — o quadro mostra o inquérito inteiro."
        : "Recorte parcial: " + n + " de " + total + " fontes. Clique em “Aplicar seleção”.";
      if (recorteFontesServidor || recorteDocsServidor) txt += " (recorte ativo no servidor)";
    }
    $("#mesa-fontes-meta").text(txt);
  }

  $("#mesa-fontes-toggle").on("click", function () {
    $("#mesa-fontes-painel").toggleClass("hidden");
    renderFontesPainel();
    if (!fontesInv) carregarFontesInventario(function () { renderFontesPainel(); });
  });
  $("#mesa-fontes-busca").on("input", function () {
    fontesBuscaFiltro = $.trim($(this).val() || "");
    renderFontesPainel();
  });
  $("#mesa-fontes-todas").on("change", function () {
    marcarFontes($("#mesa-fontes-lista").find(".mesa-fonte-check"), this.checked);
  });
  $("#mesa-fontes-aplicar").on("click", function () {
    if (!fontesInv) { Toast.aviso("As fontes ainda estão carregando — aguarde um instante."); return; }
    var csv = recorteFontesCsv();
    if (csv === (recorteFontesServidor || null)) { renderFontesPainel(); return; }
    recorteFontesServidor = csv;
    // o recorte por fontes substitui o recorte antigo por documentos na URL
    if (recorteDocsServidor) recorteDocsServidor = null;
    filtroDocsIds = null;
    isolamento = null;
    soCaminhoIds = null;
    carregarGrafo();
  });
  $("#mesa-fontes-limpar").on("click", function () {
    if (fontesInv) {
      fontesMarcadas = {};
      fontesInv.forEach(function (f) { fontesMarcadas[f.valor] = true; });
    }
    var tinhaRecorte = !!(recorteFontesServidor || recorteDocsServidor);
    recorteFontesServidor = null;
    recorteDocsServidor = null;
    filtroDocsIds = null;
    isolamento = null;
    soCaminhoIds = null;
    if (tinhaRecorte) carregarGrafo();
    else { renderFontesPainel(); atualizar(); }
  });

  // ---------- lista de documentos do grafo (campo "docs" do chat de IA) ----------
  // Os dd* com nº de citações (vizinhos em todasArestas). O painel da tela é o de fontes
  // acima; esta lista só alimenta o GrafoFiltros ("só estes documentos").
  function listaDocumentos() {
    if (cacheDocsLista) return cacheDocsLista;
    var vizinhos = {};
    todasArestas.forEach(function (a) {
      if (a.a.indexOf("dd") === 0 || a.b.indexOf("dd") === 0) {
        var dd = a.a.indexOf("dd") === 0 ? a.a : a.b;
        var outro = a.a.indexOf("dd") === 0 ? a.b : a.a;
        (vizinhos[dd] = vizinhos[dd] || {});
        vizinhos[dd][outro] = true;
      }
    });
    var lista = todosNos.filter(function (n) { return n.tipo === "doc_documento"; })
      .map(function (n) {
        var qt = vizinhos[n.id] ? Object.keys(vizinhos[n.id]).length : 0;
        var omit = (n.dado && n.dado.qt_citacoes_omitidas) || 0;
        return { id: n.id, idDoc: (n.dado && n.dado.id_documento),
                 nome: n.rotulo || n.id, qt: qt, omitidos: omit };
      })
      .sort(function (a, b) { return b.qt - a.qt; });
    cacheDocsLista = lista;
    return lista;
  }

  // ---------- filtro de período ----------

  function formatarDiaInput(valor) {
    if (!valor) return null;
    var partes = valor.split("-");
    return partes.length === 3 ? partes[2] + "/" + partes[1] + "/" + partes[0] : valor;
  }

  function atualizarStatusFiltroTempo() {
    var $status = $("#mesa-tempo-filtro-status");
    var ini = formatarDiaInput(filtroTempoInicioTxt);
    var fim = formatarDiaInput(filtroTempoFimTxt);
    var texto = null;
    if (ini && fim) texto = "Filtrando de " + ini + " a " + fim;
    else if (ini) texto = "Filtrando desde " + ini;
    else if (fim) texto = "Filtrando até " + fim;
    if (texto) {
      if (todosNos.length && !nos.length) texto += " — nenhuma atividade no período";
      $status.text(texto).removeClass("hidden");
    } else {
      $status.addClass("hidden").text("");
    }
  }

  function aplicarFiltroTempoDaUi() {
    filtroTempoInicioTxt = $("#filtro-tempo-inicio").val() || null;
    filtroTempoFimTxt = $("#filtro-tempo-fim").val() || null;
    filtroTempoInicio = filtroTempoInicioTxt ? Date.parse(filtroTempoInicioTxt + "T00:00:00Z") : null;
    filtroTempoFim = filtroTempoFimTxt ? Date.parse(filtroTempoFimTxt + "T23:59:59.999Z") : null;
    atualizar();
  }

  $("#filtro-tempo-inicio, #filtro-tempo-fim").on("change", aplicarFiltroTempoDaUi);

  $("#filtro-tempo-limpar").on("click", function () {
    $("#filtro-tempo-inicio, #filtro-tempo-fim").val("");
    filtroTempoInicio = null;
    filtroTempoFim = null;
    filtroTempoInicioTxt = null;
    filtroTempoFimTxt = null;
    atualizar();
  });

  // ---------- novos filtros (grafos grandes) ----------
  $("#filtro-limiar-cluster").on("change", atualizar);
  $("#mesa-isolar-1").on("click", function () { isolarFoco(1); });
  $("#mesa-isolar-2").on("click", function () { isolarFoco(2); });
  $("#mesa-mostrar-caminho").on("click", mostrarSoCaminho);
  $("#mesa-limpar-isolamento").on("click", limparRecorte);

  // ---------- filtros expostos ao chat de IA (js/utils/grafo_filtros.js) ----------
  // Diferente das telas de RIF/SIMBA, aqui os filtros têm estado DERIVADO em variáveis do módulo
  // (termoBusca, filtroTempo*, tiposDesligados) que só é atualizado pelos handlers de evento —
  // escrever no input direto não bastaria. Por isso aoAplicar ressincroniza tudo a partir do DOM
  // antes de remontar, e os tipos de nó (que não moram em input nenhum) entram como campo virtual.
  if (window.GrafoFiltros) {
    GrafoFiltros.registrar({
      pagina: "mesa_inquerito",
      titulo: temaOrbitAtivo() ? "Quadro de investigação" : "Mesa de inquérito",
      descricao: "Grafo de entidades do inquérito: pessoas, contatos, mensagens, ligações, "
        + "localizações, contas e arquivos, ligados por relações e evidências. Nós canônicos são "
        + "as pessoas/lugares já identificados; os demais são os registros de origem. Entram "
        + "também as pessoas e achados citados nos documentos genéricos anexados (caso de "
        + "inteligência), pendurados no documento em que aparecem.",
      campos: [
        { nome: "busca", id: "mesa-busca-input", tipo: "texto", rotulo: "Buscar nó",
          descricao: "Realça os nós cujo rótulo casa com o termo e garante que eles apareçam "
            + "mesmo quando o grafo passa do limite de nós desenhados — é o filtro para "
            + "'me mostra o fulano'." },
        { nome: "mostrar_sugeridas", id: "filtro-sugeridas", tipo: "bool", padrao: true,
          rotulo: "Sugestões da IA",
          descricao: "Entidades/relações que a busca automática propôs e ninguém confirmou ainda." },
        { nome: "mostrar_confirmadas", id: "filtro-confirmadas", tipo: "bool", padrao: true,
          rotulo: "Confirmadas" },
        { nome: "mostrar_canonicas_com_relacao", id: "filtro-canonicas-com", tipo: "bool", padrao: true,
          rotulo: "Canônicas c/ relação" },
        { nome: "mostrar_canonicas_sem_relacao", id: "filtro-canonicas-sem", tipo: "bool", padrao: false,
          rotulo: "Canônicas s/ relação",
          descricao: "Pessoas/lugares identificados que ainda não foram ligados a nada." },
        { nome: "mostrar_documentos", id: "filtro-documentos", tipo: "bool", padrao: true,
          rotulo: "Documentos genéricos (IA)",
          descricao: "Pessoas e achados que a IA extraiu dos documentos anexados ao inquérito "
            + "(PDF, planilha, texto), ligados ao documento onde aparecem. É a rede do caso de "
            + "inteligência; desligar deixa só as camadas do banco." },
        { nome: "min_relacoes", id: "filtro-grau-min", tipo: "select", padrao: "0",
          rotulo: "Mínimo de relações",
          descricao: "Esconde os nós com menos ligações que o mínimo (0 = todas; há 5/10/20/50 para grafos grandes). Quem casa a "
            + "busca continua aparecendo." },
        {
          nome: "docs", tipo: "lista", rotulo: "Só estes documentos",
          descricao: "Desenha só os documentos listados e a vizinhança de 1 salto deles "
            + "(o mesmo que marcar no painel de Documentos e filtrar). Valores: id_documento.",
          opcoes: function () {
            return listaDocumentos().map(function (d) {
              return d.idDoc != null ? String(d.idDoc) : d.id;
            });
          },
          ler: function () {
            if (!filtroDocsIds) return [];
            return Object.keys(filtroDocsIds).map(function (id) { return id.replace(/^dd/, ""); });
          },
          escrever: function (valor) {
            var lista = valor == null || valor === "" ? []
              : (Array.isArray(valor) ? valor : String(valor).split(/\s*,\s*/));
            var ids = {};
            lista.forEach(function (v) {
              if (!v) return;
              var id = String(v).indexOf("dd") === 0 ? String(v) : "dd" + String(v);
              if (noPorId[id] && noPorId[id].tipo === "doc_documento") ids[id] = true;
            });
            filtroDocsIds = Object.keys(ids).length ? ids : null;
            isolamento = null;
            soCaminhoIds = null;
            return Object.keys(ids).map(function (id) { return id.replace(/^dd/, ""); });
          },
        },
        {
          nome: "naturezas_ocultas", tipo: "lista", rotulo: "Naturezas de relação ocultas",
          descricao: "Naturezas que somem do desenho (o mesmo que desligar na legenda de relações).",
          opcoes: function () { return NATUREZAS_RELACAO.map(function (n) { return n.chave; }); },
          ler: function () {
            return Object.keys(naturezasDesligadas).filter(function (t) { return naturezasDesligadas[t]; });
          },
          escrever: function (valor) {
            var lista = valor == null || valor === "" ? []
              : (Array.isArray(valor) ? valor : String(valor).split(/\s*,\s*/));
            var validas = {};
            NATUREZAS_RELACAO.forEach(function (n) { validas[n.chave] = true; });
            if (lista.some(function (t) { return t && !validas[t]; })) return null;
            naturezasDesligadas = {};
            lista.forEach(function (t) { if (t) naturezasDesligadas[t] = true; });
            return lista.filter(Boolean);
          },
        },
        { nome: "agrupar_entidades", id: "agrupar-entidades", tipo: "bool", padrao: false,
          rotulo: "Agrupar entidades",
          descricao: "Junta na tela canônicas do mesmo tipo com nomes parecidos (só visual, "
            + "nada muda no banco)." },
        { nome: "periodo_de", id: "filtro-tempo-inicio", tipo: "data", rotulo: "Atividade a partir de",
          descricao: "Com período ativo, nó sem atividade datada dentro dele sai do desenho." },
        { nome: "periodo_ate", id: "filtro-tempo-fim", tipo: "data", rotulo: "Atividade até" },
        {
          nome: "tipos_ocultos", tipo: "lista", rotulo: "Tipos de nó ocultos",
          descricao: "Tipos que somem do desenho (o mesmo que desligar na legenda). Valores "
            + "válidos: os tipos existentes no grafo desta tela.",
          opcoes: function () {
            var vistos = {};
            todosNos.forEach(function (n) { vistos[n.tipo] = true; });
            return Object.keys(vistos);
          },
          ler: function () {
            return Object.keys(tiposDesligados).filter(function (t) { return tiposDesligados[t]; });
          },
          escrever: function (valor) {
            var lista = valor == null || valor === "" ? []
              : (Array.isArray(valor) ? valor : String(valor).split(/\s*,\s*/));
            var existentes = {};
            todosNos.forEach(function (n) { existentes[n.tipo] = true; });
            var desconhecido = lista.filter(function (t) { return t && !existentes[t]; });
            if (desconhecido.length) return null;   // vira "ignorado" em vez de esconder nada
            tiposDesligados = {};
            lista.forEach(function (t) { if (t) tiposDesligados[t] = true; });
            return lista.filter(Boolean);
          },
        },
      ],
      aoAplicar: function () {
        termoBusca = $.trim($("#mesa-busca-input").val() || "").toLowerCase();
        filtroTempoInicioTxt = $("#filtro-tempo-inicio").val() || null;
        filtroTempoFimTxt = $("#filtro-tempo-fim").val() || null;
        filtroTempoInicio = filtroTempoInicioTxt ? Date.parse(filtroTempoInicioTxt + "T00:00:00Z") : null;
        filtroTempoFim = filtroTempoFimTxt ? Date.parse(filtroTempoFimTxt + "T23:59:59.999Z") : null;
        atualizar();
      },
    });
  }

  // ---------- modal de análise estrutural ----------
  // Um retrato do DESENHO atual (não da rede inteira no banco): os hubs, os elos que se sumirem
  // partem o quadro ao meio (pontos de articulação e pontes, ver calcularElosCriticos) e as
  // comunidades detectadas. Cada item é clicável — leva ao nó / abre a aresta / isola o grupo —
  // então a análise não fica num rótulo morto: clicar já é a ação de investigar.
  function secaoAnalise(titulo, dica) {
    var $h = $("<h3>").addClass("mesa-analise-titulo").text(titulo);
    if (dica) $h.append($("<small>").text(" " + dica));
    return $h;
  }
  function itemNoClicavel($alvo, n, sufixo) {
    var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
      .attr("title", "Focar no nó e abrir a gaveta");
    $("<span>").addClass("mesa-selecao-cor").css("background", corTipo(n.tipo))
      .attr("title", ROTULOS_TIPO[n.tipo] || n.tipo).appendTo($b);
    $("<span>").addClass("mesa-analise-nome").text(truncar(n.rotulo, 40)).appendTo($b);
    if (sufixo) $("<span>").addClass("mesa-analise-meta").text(sufixo).appendTo($b);
    $b.on("click", function () { fecharModalAnalise(); selecionarNo(n); });
    $alvo.append($b);
  }
  function abrirModalAnalise() {
    var $corpo = $("#analise-corpo").empty();
    if (!nos.length) {
      $corpo.append($("<p>").addClass("card-hint").text("Nenhum nó no desenho atual."));
      $("#modal-analise").removeClass("hidden");
      return;
    }
    var elos = obterElosCriticos();
    var artIds = Object.keys(elos.articulacoes);
    var ponteKeys = Object.keys(elos.pontes);

    // tamanho das comunidades sobre o desenho atual (comunidadePorNo vem da base, estável)
    var tamCom = {};
    nos.forEach(function (n) {
      var c = comunidadePorNo[n.id];
      if (c === undefined) return;
      tamCom[c] = (tamCom[c] || 0) + 1;
    });
    var comLista = Object.keys(tamCom).map(function (k) { return { indice: parseInt(k, 10), qt: tamCom[k] }; })
      .sort(function (a, b) { return b.qt - a.qt; });

    var resumo = nos.length.toLocaleString("pt-BR") + " nós · " + arestas.length.toLocaleString("pt-BR")
      + " ligações · " + comLista.length + " comunidade" + (comLista.length === 1 ? "" : "s")
      + " · " + artIds.length + " ponto" + (artIds.length === 1 ? "" : "s") + " de articulação"
      + " · " + ponteKeys.length + " ponte" + (ponteKeys.length === 1 ? "" : "s");
    $("<p>").addClass("mesa-analise-resumo").text(resumo).appendTo($corpo);

    // hubs: os mais conectados do desenho, na ordem do grau
    $corpo.append(secaoAnalise("Nós mais conectados", "os que mais ligam gente"));
    var hubs = nos.slice().sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); }).slice(0, 12);
    var $hubs = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
    hubs.forEach(function (n) { itemNoClicavel($hubs, n, (n.grau || 0) + " lig."); });

    // pontos de articulação: removê-los parte o desenho em componentes separados
    $corpo.append(secaoAnalise("Elos críticos · pontos de articulação", "remova e o quadro se parte"));
    if (!artIds.length) {
      $("<p>").addClass("mesa-analise-vazio").text("Nenhum ponto de articulação no desenho atual.").appendTo($corpo);
    } else {
      var $arts = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      artIds.map(function (id) { return noPorId[id]; }).filter(Boolean)
        .sort(function (a, b) { return (b.grau || 0) - (a.grau || 0); }).slice(0, 24)
        .forEach(function (n) { itemNoClicavel($arts, n, (n.grau || 0) + " lig."); });
    }

    // pontes: ligações cuja remoção separa duas metades — a trilha que denuncia o atravessador
    $corpo.append(secaoAnalise("Elos críticos · pontes", "ligação que, cortada, separa o quadro"));
    if (!ponteKeys.length) {
      $("<p>").addClass("mesa-analise-vazio").text("Nenhuma ponte no desenho atual.").appendTo($corpo);
    } else {
      var porPar = {};
      arestas.forEach(function (a) {
        var k = a.a < a.b ? a.a + "|" + a.b : a.b + "|" + a.a;
        if (!porPar[k]) porPar[k] = a;
      });
      var $pontes = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      ponteKeys.forEach(function (k) {
        var a = porPar[k];
        if (!a) return;
        var na = noPorId[a.a], nb = noPorId[a.b];
        if (!na || !nb) return;
        var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
          .attr("title", "Abrir a ligação");
        $("<span>").addClass("mesa-selecao-cor").css("background", naturezaAresta(a).cor).appendTo($b);
        $("<span>").addClass("mesa-analise-nome").text(truncar(na.rotulo, 22) + " ↔ " + truncar(nb.rotulo, 22)).appendTo($b);
        $b.on("click", function () { fecharModalAnalise(); abrirGavetaAresta(a); });
        $pontes.append($b);
      });
    }

    // comunidades: clique isola o grupo (mesma ação da legenda clicável)
    $corpo.append(secaoAnalise("Comunidades", "clique para isolar o grupo"));
    if (comLista.length < 1) {
      $("<p>").addClass("mesa-analise-vazio").text("Sem comunidades no desenho atual.").appendTo($corpo);
    } else {
      var $coms = $("<div>").addClass("mesa-analise-grade").appendTo($corpo);
      comLista.forEach(function (g) {
        var focoAqui = comunidadeFocada === g.indice;
        var $b = $("<button>").attr("type", "button").addClass("mesa-analise-item")
          .toggleClass("ativo", focoAqui)
          .attr("title", focoAqui ? "Mostrar todas de novo" : "Isolar este grupo");
        $("<span>").addClass("mesa-legenda-bolinha")
          .css("background", CORES_COMUNIDADE[g.indice % CORES_COMUNIDADE.length]).appendTo($b);
        $("<span>").addClass("mesa-analise-nome").text("Comunidade " + (g.indice + 1)).appendTo($b);
        $("<span>").addClass("mesa-analise-meta").text(g.qt + " nó" + (g.qt === 1 ? "" : "s")).appendTo($b);
        $b.on("click", function () {
          comunidadeFocada = focoAqui ? null : g.indice;
          fecharModalAnalise();
          atualizar();
        });
        $coms.append($b);
      });
    }

    $("#modal-analise").removeClass("hidden");
    $("#analise-fechar").trigger("focus");
  }
  function fecharModalAnalise() { $("#modal-analise").addClass("hidden"); }
  $("#mesa-analise-btn").on("click", abrirModalAnalise);
  $("#analise-fechar").on("click", fecharModalAnalise);
  $("#modal-analise").on("click", function (e) { if (e.target === this) fecharModalAnalise(); });

  // ---------- visões salvas (privadas por usuário) ----------
  // O investigador ajusta filtros, fontes, limite e enquadramento e guarda TUDO num nome só, para
  // voltar àquele recorte num clique — ou abrir direto por link (?visao=). O estado mora no banco,
  // amarrado ao usuário (ID_USUARIO) e ao inquérito: cada um vê só as suas, nunca as dos colegas.
  var visoesCache = [];

  function estadoDaVisao() {
    return {
      filtros: (window.GrafoFiltros && GrafoFiltros.disponivel()) ? GrafoFiltros.ler() : {},
      fontes: recorteFontesServidor || null,
      camera: { x: vb.x, y: vb.y, w: vb.w, h: vb.h },
      limite: (limiteNos === Infinity) ? "todos" : limiteNos,
    };
  }

  // Reescreve as caixinhas do painel "🗂 Fontes" a partir do csv da visão — mesma regra do load
  // (csv vazio = todas; csv presente = só as listadas). Sem inventário carregado, não faz nada.
  function refluidirFontesMarcadasDaCsv(csv) {
    if (!fontesInv) return;
    fontesMarcadas = {};
    if (csv) {
      var vistos = {};
      fontesInv.forEach(function (f) { vistos[f.valor] = true; });
      String(csv).split(/\s*,\s*/).forEach(function (v) { if (v) fontesMarcadas[v] = true; });
    } else {
      fontesInv.forEach(function (f) { fontesMarcadas[f.valor] = true; });
    }
  }

  function aplicarVisao(estado) {
    if (!estado || typeof estado !== "object") { Toast.erro("Não foi possível aplicar esta visão."); return; }
    var f = estado.filtros || {};
    var csv = estado.fontes || null;
    limiteNos = estado.limite === "todos" ? Infinity : (parseInt(estado.limite, 10) || LIMITE_NOS_PADRAO);

    function aplicarFiltrosECamera() {
      // a câmera é restaurada no callback do layout disparado por este atualizar (ver atualizar)
      cameraPendente = (estado.camera && estado.camera.w) ? estado.camera : null;
      if (window.GrafoFiltros && GrafoFiltros.disponivel()) GrafoFiltros.aplicar(f, { mesclar: false });
      else atualizar();
    }

    if (csv !== (recorteFontesServidor || null)) {
      // o recorte por fontes vem do servidor: recarrega o grafo só com aquelas fontes e,
      // quando chegar, escreve os filtros e restaura a câmera sobre o desenho novo.
      recorteFontesServidor = csv;
      recorteDocsServidor = null;
      filtroDocsIds = null;
      isolamento = null;
      soCaminhoIds = null;
      refluidirFontesMarcadasDaCsv(csv);
      carregarGrafo(function () { renderFontesPainel(); aplicarFiltrosECamera(); });
    } else {
      aplicarFiltrosECamera();
    }
  }

  function carregarVisoes(aoTerminar) {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/mesa-visoes")
      .done(function (r) { visoesCache = r || []; })
      .fail(function () { visoesCache = []; })
      .always(function () { if (aoTerminar) aoTerminar(); });
  }

  function fecharDropdownVisoes() {
    $("#visoes-dropdown-menu").addClass("hidden");
    $("#visoes-dropdown").removeClass("aberto");
  }

  function renderVisoesDropdown() {
    var $m = $("#visoes-dropdown-menu").empty();
    $("<button>").attr("type", "button").addClass("dropdown-item mesa-visoes-salvar")
      .text("Salvar visão atual…")
      .on("click", salvarVisaoAtual).appendTo($m);
    if (!visoesCache.length) {
      $("<div>").addClass("mesa-visoes-vazio").text("Nenhuma visão salva ainda.").appendTo($m);
      return;
    }
    visoesCache.forEach(function (v) {
      var $item = $("<div>").addClass("mesa-visoes-item").appendTo($m);
      $("<button>").attr("type", "button").addClass("mesa-visoes-usar")
        .text(v.no_visao).attr("title", "Carregar esta visão")
        .on("click", function () { aplicarVisaoPorId(v.id_visao); }).appendTo($item);
      $("<button>").attr("type", "button").addClass("mesa-visoes-apagar")
        .attr("title", "Apagar esta visão").attr("aria-label", "Apagar esta visão").html("&times;")
        .on("click", function () { apagarVisao(v.id_visao, v.no_visao); }).appendTo($item);
    });
  }

  function salvarVisaoAtual() {
    fecharDropdownVisoes();
    Dialogo.prompt({
      titulo: "Salvar visão",
      mensagem: "Guarda filtros, fontes, limite e enquadramento atuais — visível só para você.",
      rotuloOk: "Salvar", maxlength: 120, placeholder: "Nome da visão",
    }).then(function (nome) {
      nome = (nome || "").trim();
      if (!nome) return;
      $.ajax({
        url: "/api/investigacao/inqueritos/" + idInquerito + "/mesa-visoes",
        method: "POST", contentType: "application/json",
        data: JSON.stringify({ nome: nome, estado: estadoDaVisao() }),
      })
        .done(function () { Toast.sucesso("Visão “" + nome + "” salva."); carregarVisoes(); })
        .fail(mostrarErroAjax);
    });
  }

  function aplicarVisaoPorId(id) {
    fecharDropdownVisoes();
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/mesa-visoes/" + id)
      .done(function (r) {
        if (window.EstadoUrl) { try { window.EstadoUrl.salvar({ visao: id }); } catch (e) {} }
        aplicarVisao(r && r.estado);
      })
      .fail(mostrarErroAjax);
  }

  function apagarVisao(id, nome) {
    confirmar("Apagar a visão “" + nome + "”?", function () {
      $.ajax({ url: "/api/investigacao/inqueritos/" + idInquerito + "/mesa-visoes/" + id, method: "DELETE" })
        .done(function () { Toast.sucesso("Visão apagada."); carregarVisoes(renderVisoesDropdown); })
        .fail(mostrarErroAjax);
    }, "Apagar");
  }

  $("#visoes-dropdown-btn").on("click", function (evt) {
    evt.stopPropagation();
    if ($("#visoes-dropdown-menu").hasClass("hidden")) {
      // abre já (mostra o menu vazio/"carregando") e preenche quando o GET voltar — sem o
      // removeClass o menu era montado mas continuava invisível, e o clique "não fazia nada".
      $("#visoes-dropdown-menu").removeClass("hidden");
      $("#visoes-dropdown").addClass("aberto");
      carregarVisoes(renderVisoesDropdown);
    } else {
      fecharDropdownVisoes();
    }
  });
  // clicar dentro do menu não o fecha (o handler global de documento fecharia a cada item);
  // quem fecha é o próprio handler de cada ação.
  $("#visoes-dropdown-menu").on("click", function (e) { e.stopPropagation(); });

  // ---------- fechar modais ----------

  $("#no-fechar").on("click", fecharModalNo);
  ["#modal-relacao", "#modal-canonica", "#modal-fusao", "#modal-deteccoes"].forEach(function (sel) {
    $(sel).on("click", function (e) { if (e.target === this) $(this).addClass("hidden"); });
  });
  $("#relacao-fechar").on("click", function () { $("#modal-relacao").addClass("hidden"); });
  $("#canonica-fechar").on("click", function () { $("#modal-canonica").addClass("hidden"); });
  $("#fusao-fechar").on("click", function () { $("#modal-fusao").addClass("hidden"); });
  $("#deteccoes-fechar").on("click", function () { $("#modal-deteccoes").addClass("hidden"); });

  // ---------- carga ----------

  function carregarGrafo(aoTerminar) {
    mostrarLoading("Carregando grafo…");
    var url = "/api/investigacao/inqueritos/" + idInquerito + "/grafo";
    // B8/C9 + painel "🗂 Fontes": recorte server-side — o payload já vem só com o que nasce
    // das fontes marcadas (?fontes=); ?docs= segue vivo para links antigos.
    if (recorteDocsServidor) url += "?docs=" + encodeURIComponent(recorteDocsServidor);
    if (recorteFontesServidor) {
      url += (recorteDocsServidor ? "&" : "?") + "fontes=" + encodeURIComponent(recorteFontesServidor);
    }
    $.getJSON(url)
      .done(function (resp) {
        ocultarLoading();
        // volta o empty state ao texto normal: uma recarga depois de um erro não deve
        // deixar a frase de erro nem o botão "tentar de novo" pendurados na tela vazia
        $vazio.find(".mesa-erro-retry").remove();
        $vazio.find(".empty-hint").first().text("Nenhuma entidade neste inquérito ainda.");
        montarModelo(resp);
        atualizar();
        carregarFotosPendentes();
        if (aoTerminar) aoTerminar();
      })
      .fail(function (xhr) {
        ocultarLoading();
        var msg = (xhr.responseJSON && xhr.responseJSON.detail) || "Erro ao carregar o grafo.";
        // Sem $vazio.text(): ele apagava os CTAs do empty state e o erro ficava uma frase
        // morta sem nenhuma ação. Aqui a mensagem entra no lugar da dica e o retry na linha
        // de botões, que continuam funcionando (buscar IA, identificar pessoas, criar).
        $vazio.find(".empty-hint").first().text("Não foi possível carregar o grafo: " + msg);
        if (!$vazio.find(".mesa-erro-retry").length) {
          $("<button>").attr("type", "button").addClass("btn btn-primary mesa-erro-retry")
            .html(window.Icones.html("recarregar") + " Tentar de novo")
            .on("click", function () { carregarGrafo(); })
            .insertBefore($vazio.find(".mesa-vazio-acoes").children().first());
        }
        $vazio.removeClass("hidden");
      });
  }

  // A escolha de tema acontece sem recarregar esta página. Refazer somente a camada SVG inclui
  // ou retira cartões e pinos imediatamente, mantendo posições, filtros e seleção atuais.
  new MutationObserver(function (mudancas) {
    if (!mudancas.some(function (m) { return m.attributeName === "data-theme"; })) return;
    sincronizarApresentacaoOrbit();
    if (dados) renderGrafo();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // P0-1: resumo antes do grafo — em inquérito grande sugere começar nos hubs e avisa
  // o tamanho real antes de baixar tudo. A conta inclui a camada de documentos estimada
  // (C10): um caso "só de PDFs" com 280 docs também é grafo grande, mesmo sem canônicas.
  function carregarResumo() {
    $.getJSON("/api/investigacao/inqueritos/" + idInquerito + "/grafo/resumo")
      .done(function (r) {
        if (!r) return;
        var total = (r.total_nos || 0) + (r.total_nos_doc_estimado || 0);
        if (!total) return;
        if (total >= 1000 && !termoBusca && String(lerMinRelacoes() || "0") === "0") {
          $("#filtro-grau-min").val("2");
          var partes = ["Grafo grande: " + Number(total).toLocaleString("pt-BR") + " nós"];
          if (r.qt_documentos) partes.push(Number(r.qt_documentos).toLocaleString("pt-BR") + " documentos");
            partes.push("começando em “2 ou mais relações”. Afrouxe o filtro, use a busca ou o painel “Fontes” para ver a periferia.");
          $statusLabel.text(partes.join(" · "));
        }
      })
      .fail(function () { /* resumo é conveniência — o grafo carrega mesmo sem ele */ });
  }

  var visaoInicial = null;
  if (idInquerito) {
    vb = { x: 0, y: 0, w: LARGURA, h: ALTURA };
    // P1-7: recorte compartilhável (?busca=&grau=&docs=) — restaurado antes de carregar.
    // ?docs= recorta já no servidor (payload pequeno); o filtro na tela é refeito junto.
    if (window.EstadoUrl) {
      try {
        var est = window.EstadoUrl.ler(["busca", "grau", "docs", "fontes", "visao"]);
        if (est.visao) visaoInicial = est.visao;
        if (est.busca) { $("#mesa-busca-input").val(est.busca); termoBusca = String(est.busca).toLowerCase(); }
        if (est.grau && $("#filtro-grau-min option[value='" + est.grau + "']").length) {
          $("#filtro-grau-min").val(est.grau);
        }
        // ?fontes= (painel "🗂 Fontes"): csv tp:id devolvido cru ao servidor no primeiro load.
        if (est.fontes) recorteFontesServidor = String(est.fontes);
        if (est.docs) {
          var idsDocs = {};
          String(est.docs).split(/\s*,\s*/).forEach(function (v) {
            if (v) idsDocs["dd" + v.replace(/^dd/, "")] = true;
          });
          if (Object.keys(idsDocs).length) {
            filtroDocsIds = idsDocs;
            recorteDocsServidor = Object.keys(idsDocs).map(function (id) {
              return id.replace(/^dd/, "");
            }).join(",");
          }
        }
      } catch (e) { /* URL inválida — segue com os padrões */ }
    }
    carregarResumo();
    carregarGrafo();
    monitorarBusca();
    conferirIaDisponivel();
    iniciarMinimapa();
    iniciarCabecalhoGaveta();
    // O inventário de fontes é carregado junto (são 8 GETs leves, uma vez só) para o botão
    // 🗂 Fontes já chegar com a contagem e o recorte ?fontes= da URL se refletir nas caixas.
    // Com ?visao= na URL, a visão salva é aplicada depois que o inventário chega — é ela que
    // decide as fontes, então precisa do inventário pronto para refletir nas caixinhas.
    carregarFontesInventario(function () {
      renderFontesPainel();
      if (visaoInicial) { aplicarVisaoPorId(visaoInicial); visaoInicial = null; }
    });
  }
})();
