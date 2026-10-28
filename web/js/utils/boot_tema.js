/* Aplica o tema salvo ANTES da primeira pintura - era um <script> inline no <head> de cada
 * pagina, exatamente como js/utils/theme.js le o tema do localStorage e poe no atributo
 * data-theme de <html>. Sem isto, toda tela nascia escura e piscava claro para quem usa o tema
 * claro.
 *
 * Saiu do inline por causa da CSP (nginx/default.conf, `script-src 'self'`): com o cabecalho
 * valendo tambem para os .html (achado IV.3 do pentest - o location ~* \.(js|css|html)$
 * anulava os add_header do nivel `server` por causa da heranca do add_header no nginx), o
 * inline seria bloqueado no navegador e o anti-piscada morreria nas 73 telas. Script externo
 * no <head>, sem async/defer, roda do mesmo jeito antes do <body>.
 *
 * Aceita qualquer id de tema em cache (ver TEMAS em js/utils/theme.js) - o teste e so de
 * formato, pra um valor estranho no localStorage nao virar atributo arbitrario no <html>. */
(function () {
  try {
    var t = localStorage.getItem("analyser-theme");
    document.documentElement.setAttribute("data-theme", /^[a-z0-9-]{1,30}$/.test(t || "") ? t : "dark");
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();
