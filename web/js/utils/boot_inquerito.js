/* inquerito.html e so um despachante: redireciona para a versao da tela que combina com o tema
 * do navegador (Orbit abre inquerito_orbit.html, o resto abre investigacao.html), preservando a
 * query string e o hash. Era o <script> inline do <head> de inquerito.html; virou arquivo
 * externo por causa da CSP `script-src 'self'` (ver comentario de cabecalho em boot_tema.js). */
(function () {
  var tema = "dark";
  try { tema = localStorage.getItem("analyser-theme") || "dark"; } catch (e) { /* usa o padrao */ }
  var pagina = tema === "orbit" || tema === "frontend-v3"
    ? "inquerito_orbit.html"
    : "investigacao.html";
  window.location.replace(pagina + window.location.search + window.location.hash);
})();
