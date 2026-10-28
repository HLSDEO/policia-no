/* Mapa único das telas do sistema — window.MapaTelas.
 *
 * Antes deste módulo, "quais telas existem e onde levam" vivia em dois lugares que precisavam
 * ser mantidos manualmente em sincronia: js/sidebar.js (NAV_ITEMS, o menu lateral) e
 * js/navegacao_inquerito.js (ITENS_INQUERITO/ITENS_EXTRACAO, o menu "Navegar" dentro de um
 * inquérito) — mais js/home.js com uma terceira lista (TELAS_BUSCA) para a busca global. Uma
 * tela nova (ou renomeada) exigia lembrar de editar até três arquivos.
 *
 * Este módulo é a fonte única; sidebar.js, home.js e navegacao_inquerito.js só leem daqui e
 * cuidam da parte que é deles (montar o menu, montar a trilha, filtrar a busca). Os ícones vêm
 * de window.Icones (js/icones.js) pela chave — carregar icones.js antes deste.
 *
 * Estrutura:
 *  - NAV_ITEMS / PAGINAS_MENU_COMPLETO: menu lateral (js/sidebar.js).
 *  - ITENS_INQUERITO / ITENS_EXTRACAO / ROTULO_PAGINA: menu "Navegar" e trilha
 *    (js/navegacao_inquerito.js).
 *  - TELAS_BUSCA: busca global da tela Início (js/home.js).
 *  - paginaConversa()/ehAppWhatsApp(): qual das duas telas de conversa abrir para uma fonte/app
 *    (js/investigacao.js, js/arquivos.js, js/chaves_pix.js, js/marcacoes.js, js/ia.js…). */
(function () {
  "use strict";

  var NAV_ITEMS = [
    { href: "index.html", label: "Início", icone: "inicio" },
    { href: "inqueritos.html", label: "Inquéritos", icone: "inqueritos" },
    { href: "ia.html", label: "Assistente de IA", icone: "ia" },
    { href: "convergencia.html", label: "Convergência", icone: "convergencia" },
    {
      grupo: "Configurações",
      icone: "admin",
      adminOnly: true,
      itens: [
        { href: "ingestao.html", label: "Ingestão", icone: "ingestao" },
        { href: "usuarios.html", label: "Usuários", icone: "usuarios" },
        { href: "ia_mesa_inquerito.html", label: "IA - Quadro de investigação", icone: "prompts" },
        { href: "padroes_relatorio.html", label: "Padrão de relatórios", icone: "padroesRelatorio" },
        { href: "categorias_imagem.html", label: "Categorias de imagem", icone: "categoriasImagem" },
        { href: "delegacias.html", label: "Gerencia de delegacias", icone: "delegacias" },
        { href: "auditoria.html", label: "Auditoria", icone: "auditoria" },
        { href: "configuracoes.html", label: "Configurações de IA", icone: "configIa" },
        { href: "alertas_simba.html", label: "Alertas SIMBA", icone: "alertasSimba" },
        { href: "servidor.html", label: "Servidor", icone: "servidor" },
        { href: "documentacao.html", label: "Documentação de código", icone: "documentacao" }
      ]
    }
  ];

  var PAGINAS_MENU_COMPLETO = [
    "index.html", "inqueritos.html", "investigacao.html", "importacao.html",
    "ingestao.html", "usuarios.html", "ia_mesa_inquerito.html", "padroes_relatorio.html",
    "categorias_imagem.html", "delegacias.html", "auditoria.html", "convergencia.html", "configuracoes.html",
    "alertas_simba.html", "servidor.html", "documentacao.html"
  ];

  /* Telas do inquérito inteiro — não dependem de uma extração escolhida. */
  var ITENS_INQUERITO = [
    { icone: "inquerito", rotulo: "Resumo do Inquérito", pagina: "inquerito" },
    { icone: "mesa", rotulo: "Quadro de Investigação", pagina: "mesa_inquerito" },
    // quadro de hipóteses do inquérito inteiro (substitui os antigos "achados" por documento)
    { icone: "notas", rotulo: "Hipóteses do inquérito", pagina: "hipoteses" },
    { icone: "ia", rotulo: "Conversas de IA", pagina: "ia" }
  ];

  /* Qual das duas telas de conversa abrir. As duas rodam o mesmo js/whatsapp.js e diferem só na
   * lista lateral: whatsapp.html tem as abas Conversas/Grupos/Status, e mensageiros.html não tem —
   * ela tematiza a tela pela cor do app. Quem tem grupo e Status é o WhatsApp e o WhatsApp Business
   * (que o Cellebrite entrega como Source separado e por isso mora nas tabelas MSG_TB0x, ou seja,
   * chega aqui como fonte=msg&app=WhatsApp Business); SMS não tem nem um nem outro.
   *
   * Regra única de propósito: cada tela que linka para uma conversa repetia a sua, e o resultado era
   * SMS abrindo na tela do WhatsApp (com abas de grupo e Status que o SMS não tem) e o WhatsApp
   * Business abrindo na dos mensageiros (sem as abas, e pintado da cor de app desconhecido).
   *
   * A regra é CONTER "whatsapp" no nome, e não lista de nomes exatos (ver mapa_telas.js, a fonte
   * única — a nuvem já chegou com o modelo cru do Cellebrite no tp_app, "whatsapp_replaced_on_308",
   * e abriu na tela errada). Qualquer mensageiro com whatsapp no nome abre em whatsapp.html. */
  function ehAppWhatsApp(app) {
    return String(app || "").toLowerCase().indexOf("whatsapp") !== -1;
  }

  function paginaConversa(fonte, app) {
    if (fonte === "msg") return ehAppWhatsApp(app) ? "whatsapp.html" : "mensageiros.html";
    return fonte === "sms" ? "mensageiros.html" : "whatsapp.html";
  }

  /* Telas de uma extração (um aparelho). */
  var ITENS_EXTRACAO = [
    { icone: "whatsapp", rotulo: "WhatsApp", pagina: "whatsapp", fonte: "wpp" },
    { icone: "sms", rotulo: "SMS", pagina: "mensageiros", fonte: "sms" },
    { icone: "contatos", rotulo: "Contatos", pagina: "contatos" },
    { icone: "ligacoes", rotulo: "Ligações", pagina: "ligacoes" },
    { icone: "localizacoes", rotulo: "Localizações", pagina: "localizacoes" },
    { icone: "notificacoes", rotulo: "Notificações", pagina: "notificacoes" },
    { icone: "notas", rotulo: "Notas", pagina: "notas" },
    { icone: "timeline", rotulo: "Timeline", pagina: "timeline" },
    { icone: "navegacao", rotulo: "Navegação", pagina: "navegacao" },
    { icone: "aplicativos", rotulo: "Aplicativos", pagina: "aplicativos" },
    { icone: "email", rotulo: "E-mails", pagina: "emails" },
    { icone: "banco", rotulo: "Contas bancárias", pagina: "contas_financeiras" },
    { icone: "arquivos", rotulo: "Arquivos", pagina: "arquivos" },
    { icone: "pix", rotulo: "Chaves PIX", pagina: "chaves_pix" },
    { icone: "wifi", rotulo: "Wi-Fi", pagina: "wifi" },
    // domínios do UFDR importados na auditoria de aproveitamento (ver docs/extração_ufdr.md).
    // Alguns só existem em um sistema: antena de telefonia e sensor de movimento são de
    // iPhone. A tela mostra o aviso quando o aparelho não tem o dado.
    { icone: "navegacao", rotulo: "Redes sociais e buscas", pagina: "social_buscas" },
    { icone: "timeline", rotulo: "Calendário", pagina: "calendario" },
    { icone: "aparelho", rotulo: "Identidade do aparelho", pagina: "identidade_aparelho" },
    { icone: "envolvidos", rotulo: "Dispositivos reconhecidos", pagina: "dispositivos_reconhecidos" },
    // dados de conta na nuvem (Nuvem IPED hoje, Microsoft/Apple depois — ver
    // contas_nuvem_utils.py): ficam vazios pra um aparelho UFDR, como qualquer
    // outro item desta lista sem dado
    { icone: "navegacao", rotulo: "Minha Atividade", pagina: "atividades" },
    { icone: "localizacoes", rotulo: "Acessos", pagina: "acessos" },
    { icone: "localizacoes", rotulo: "Acessos agregados", pagina: "acessos_agregados" },
    { icone: "envolvidos", rotulo: "Conexões", pagina: "conexoes" },
    { icone: "aparelho", rotulo: "Dispositivos da conta", pagina: "dispositivos_conta" },
    { icone: "dono", rotulo: "Perfil da conta", pagina: "perfil_conta" },
    { icone: "timeline", rotulo: "Agenda", pagina: "agenda" }
  ];

  /* Rótulo de cada página, pra fechar a trilha quando o <h1> não serve (páginas sem topbar). */
  var ROTULO_PAGINA = {
    inquerito: "Resumo do Inquérito", mesa_inquerito: "Quadro de Investigação", hipoteses: "Hipóteses do inquérito", ia: "Conversas de IA", rif: "RIF",
    grafo_rif: "Grafo do RIF",
    inquerito_pdf: "Inquérito em PDF", mensageiros: "Mensageiros",
    simba: "Extrato bancário (SIMBA)"
  };

  /* Telas fixas da busca global (js/home.js). Os termos extras cobrem como cada tela é chamada
   * no dia a dia — quem procura "permissões" quer Usuários, quem procura "importar" quer
   * Ingestão. */
  var TELAS_BUSCA = [
    { href: "inqueritos.html", label: "Inquéritos", desc: "Selecione um IPL ou um caso de inteligência.", icone: "inqueritos", termos: "inqueritos ipl lista carrossel casos" },
    { href: "inqueritos_orbit.html?nova_importacao=1", label: "Nova Importação", desc: "Importar UFDR, RIF, SIMBA, IPED ou documento.", icone: "mais", termos: "importar fonte ufdr rif simba iped documento caso extrair" },
    { href: "ingestao.html", label: "Ingestão", desc: "Importar novos extratos e arquivos.", icone: "ingestao", termos: "importar upload extrair", adminOnly: true },
    { href: "usuarios.html", label: "Usuários", desc: "Contas do sistema e aprovações.", icone: "usuarios", termos: "contas aprovacao permissoes acesso senha", adminOnly: true },
    { href: "auditoria.html", label: "Auditoria", desc: "Quem acessou dado de inquérito e o que foi perguntado à IA.", icone: "auditoria", termos: "acessos log historico rastreio", adminOnly: true },
    // as duas abas de ia_mesa_inquerito.html entram separadas na busca: quem procura "padrões de
    // crime" não adivinha o nome da tela que passou a abrigá-las, e ?aba= já abre na certa
    { href: "ia_mesa_inquerito.html?aba=prompts", label: "IA - Quadro de investigação · Prompts de entidade", desc: "Modelos usados na extração por IA.", icone: "prompts", termos: "ia modelo entidades prompts", adminOnly: true },
    { href: "ia_mesa_inquerito.html?aba=padroes", label: "IA - Quadro de investigação · Padrões de crime", desc: "Regras de detecção de padrões.", icone: "padroesCrime", termos: "deteccao regras padroes crime", adminOnly: true },
    { href: "padroes_relatorio.html", label: "Padrão de relatórios", desc: "Modelos de relatório final.", icone: "padroesRelatorio", termos: "relatorio modelo docx", adminOnly: true },
    { href: "categorias_imagem.html", label: "Categorias de imagem", desc: "Classificação automática das imagens extraídas.", icone: "categoriasImagem", termos: "fotos classificacao imagens", adminOnly: true },
    { href: "configuracoes.html", label: "Configurações de IA", desc: "Provedor, modelo e parâmetros de IA.", icone: "configIa", termos: "ia provedor modelo ollama chave", adminOnly: true },
    { href: "documentacao.html", label: "Documentação de código", desc: "Os documentos da pasta docs/ — como cada feature funciona — lidos dentro do sistema.", icone: "documentacao", termos: "docs manual markdown tecnica ajuda como funciona", adminOnly: true }
  ];

  window.MapaTelas = {
    NAV_ITEMS: NAV_ITEMS,
    PAGINAS_MENU_COMPLETO: PAGINAS_MENU_COMPLETO,
    ITENS_INQUERITO: ITENS_INQUERITO,
    ITENS_EXTRACAO: ITENS_EXTRACAO,
    ROTULO_PAGINA: ROTULO_PAGINA,
    TELAS_BUSCA: TELAS_BUSCA,
    ehAppWhatsApp: ehAppWhatsApp,
    paginaConversa: paginaConversa
  };
})();


