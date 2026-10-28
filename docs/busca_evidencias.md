# Busca de evidências na tela, palette Ctrl+K e ficha de custódia

## Visão geral

Três melhorias de uso entregues juntas, todas na camada de interface (sem tabela nova):

1. **Busca de evidências** (`busca_conteudo.html`) — a busca híbrida que até então só o chat de
   IA usava como ferramenta interna (`buscar_hibrida` em `funcoes/rag_hibrido.py`, ver
   `rag_hibrido.md`) ganhou tela: o investigador digita uma pergunta ou palavras-chave e recebe
   trechos de mensagens, transcrições e documentos, fundidos por RRF, cada um com link para o
   registro original.
2. **Palette Ctrl+K no tema clássico** — a busca rápida da Orbit (navigation_orbit) chegou às
   telas clássicas: `Ctrl/Cmd+K` (ou `/`) abre uma janela que junta telas, inquéritos e — para
   texto livre — um atalho direto para a Busca de evidências.
3. **Ficha de custódia** (`ExportarCustodia`) — cada cartão de evidência do Orbit (extração,
   nuvem, RIF, SIMBA, IPED, IPL, documento, IRPF, IPEI, telefonia) ganhou no menu "⋮" a ação
   "Ficha de custódia": um `.zip` com a ficha legível (`FICHA_DE_CUSTODIA.txt`), os metadados
   crus (`metadados.json`) e — quando a tela tem lista — os registros em CSV.

## Fluxo de uso

- **Busca de evidências**: menu "Navegar" do inquérito → "Busca de evidências" (ou Ctrl+K e
  digitar o texto). Escolhido o inquérito e digitada a consulta, os resultados listam a origem
  (sentido/literal/grafo), o trecho com o termo realçado e o link "Abrir a mensagem/documento
  original". A consulta fica na URL (`?q=`) e o link é compartilhável.
- **Ctrl+K**: em qualquer tela clássica, `Ctrl/Cmd+K` ou `/`; ↑↓ navegam, Enter abre, Esc fecha.
- **Ficha de custódia**: no cartão da evidência, "⋮" → "Ficha de custódia" — baixa
  `custodia_<tipo>_<id>-<data>.zip` com quem exportou e quando registrados na ficha.

## Como funciona

- **Backend**: `api/rotas/busca.py` expõe `GET /api/investigacao/inqueritos/{id}/busca-hibrida`
  (sessão aprovada + `exigir_acesso_inquerito`) e apenas repassa a `funcoes.rag_hibrido.buscar_hibrida`
  com a consulta limitada a 300 caracteres. O motor, os pesos e o cache não mudam.
- **Tela**: `web/busca_conteudo.html` + `web/js/busca_conteudo.js`. As referências dos
  resultados (`[msg:wpp:ID]`, `[doc:ID:PÁGINA]`) viram links pelas mesmas funções que as
  citações clicáveis do chat usam (`DocTrecho.url`, `MapaTelas.paginaConversa`), e o realce do
  termo é `DocTrecho.realcarTermo` (escapa tudo, injeta só os `<mark>`). Cada resultado com
  destino ganha também a pastilha ℹ️ de cadeia de custódia (`EvidenciaInfo.pastilhaEl` —
  mesmo modal de `web/js/utils/evidencia_info.js` do chat).
- **Palette**: implementada dentro de `web/js/utils/sidebar.js` (módulo global do tema
  clássico). Usa `MapaTelas.TELAS_BUSCA` com os mesmos filtros de permissão do Início
  (`admin`/`auditoria` revelados pelo evento `analyser:usuario`) e o cache de sessão de
  inquéritos. No Orbit o atalho é ignorado — lá já existe a palette de `navigation_orbit.js`.
- **Ficha de custódia**: `web/js/utils/exportar_custodia.js` monta o ZIP no navegador,
  reaproveitando o ZIP "store", o `gerarCsv` (BOM UTF-8, RFC 4180) e o download por blob que já
  viviam em `web/js/utils/exportar_excel.js` (agora expostos como `montarZip`/`baixarBlob`/
  `gerarCsv`). Os metadados vêm do objeto que já alimenta o cartão (hashes, arquivo, situação,
  quem extraiu) — `acaoCustodia()` em `web/js/inquerito_orbit.js`.
- **Ações rápidas destravadas no Orbit**: "Achados do Analista" deixou de exigir evidência
  processada (UFDR, nuvem, SIMBA) e passou a valer também para documento com extração falha —
  anotar não depende do dado estar pronto. "Resumo" dos cartões não-UFDR agora gera o resumo
  sob demanda pelo chat escopado (`?pergunta=` pré-preenche a pergunta em `ia.html`, ver
  `js/ia.js`); sem processamento, o diálogo continua mostrando o que o cartão sabe.
- **Exportações corrigidas**: as telas de Wi-Fi e Contas bancárias paginam o DOM no cliente e o
  botão de Excel automático lia só a página visível; agora instalam um provedor
  (`ExportarExcel.instalar({dados})`) que exporta o filtro inteiro. As mesmas telas de Notas e
  Chaves PIX ganharam paginação client-side (`UfdrDominio.criarPaginador`) com exportação completa.

## Modelo de dados

Nada novo: a busca lê o que o RAG híbrido já indexa (`rag_hibrido.md`), a ficha usa as colunas
de hash/custódia já existentes nas tabelas de origem (`hash_arquivos.md`), e as preferências da
palette são voláteis (nada é persistido).

## Arquivos

- `api/rotas/busca.py` (novo), `api/main.py`
- `web/busca_conteudo.html`, `web/js/busca_conteudo.js`, `web/css/busca_conteudo.css` (novos)
- `web/js/utils/sidebar.js` (palette), `web/js/utils/mapa_telas.js` (tela no menu/busca), `web/js/utils/icones.js` (ícone `busca`)
- `web/js/utils/exportar_custodia.js` (novo), `web/js/utils/exportar_excel.js` (CSV + blocos expostos)
- `web/js/inquerito_orbit.js` (ficha de custódia, ações rápidas), `web/js/ia.js` (`?pergunta=`)
- `web/js/wifi.js`, `web/js/contas_financeiras.js`, `web/js/notas.js`, `web/js/chaves_pix.js` (paginação/exportação), `web/js/servidores.js` (`Dialogo` no lugar de `window.confirm`)
