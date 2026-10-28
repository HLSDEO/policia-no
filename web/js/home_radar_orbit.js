/* Fundo decorativo: malha animada a 30 fps, sem bibliotecas ou acesso a dados. */
(function () {
  "use strict";
  var network = document.querySelector(".home-radar-network");
  if (!network) return;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  var pointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  var toggle = document.querySelector(".home-motion-toggle");
  var ns = "http://www.w3.org/2000/svg", points = [], seed = 37;
  var x = 0, y = 0, targetX = 0, targetY = 0, frame = 0, last = null;
  var suspended = false, paused = false;
  function random() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }
  function path(attributes) {
    var node = document.createElementNS(ns, "path");
    Object.keys(attributes).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    network.appendChild(node);
    return node;
  }
  // Cinco caminhos SVG reutilizados; nenhum elemento criado durante a animação.
  var connections = [.12, .24, .38].map(function (opacity) {
    return path({ fill: "none", stroke: "#71899d", "stroke-width": .8, opacity: opacity });
  });
  var dots = ["#7895ae", "#719e90"].map(function (color) {
    return path({ fill: color, opacity: .78 });
  });
  for (var row = 0; row < 7; row++) {
    for (var col = 0; col < 11; col++) {
      var angle = random() * Math.PI * 2, speed = 7 + random() * 9;
      points.push({
        x: col * 120 + (random() - .5) * 90,
        y: row * 120 + (random() - .5) * 90,
        vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        depth: .4 + random() * .6
      });
    }
  }
  function enabled() { return !reduced.matches && !paused && !document.hidden && !suspended; }
  function paint(dt) {
    var lineData = ["", "", ""], dotData = ["", ""], positions = [];
    points.forEach(function (point, i) {
      point.x += point.vx * dt;
      point.y += point.vy * dt;
      if (point.x < -45 || point.x > 1245) point.vx = Math.abs(point.vx) * (point.x < -45 ? 1 : -1);
      if (point.y < -45 || point.y > 745) point.vy = Math.abs(point.vy) * (point.y < -45 ? 1 : -1);
      var px = point.x + x * 32 * point.depth;
      var py = point.y + y * 25 * point.depth;
      positions.push({ x: px, y: py });
      var radius = i % 5 === 0 ? 2 : 1.4;
      dotData[i % 3 === 0 ? 1 : 0] += "M" + (px - radius).toFixed(1) + "," + py.toFixed(1) +
        "a" + radius + "," + radius + " 0 1,0 " + radius * 2 + ",0a" + radius + "," + radius + " 0 1,0 " + -radius * 2 + ",0 ";
    });
    for (var i = 0; i < positions.length; i++) {
      for (var j = i + 1; j < positions.length; j++) {
        var a = positions[i], b = positions[j];
        var distanceSquared = (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y);
        if (distanceSquared < 175 * 175) {
          var band = distanceSquared < 85 * 85 ? 2 : distanceSquared < 130 * 130 ? 1 : 0;
          lineData[band] += "M" + a.x.toFixed(1) + "," + a.y.toFixed(1) + "L" + b.x.toFixed(1) + "," + b.y.toFixed(1);
        }
      }
    }
    connections.forEach(function (node, index) { node.setAttribute("d", lineData[index]); });
    dots.forEach(function (node, index) { node.setAttribute("d", dotData[index]); });
  }
  function tick(time) {
    frame = 0;
    if (!enabled()) return;
    if (last === null || time - last >= 1000 / 30) {
      var dt = last === null ? 0 : Math.min((time - last) / 1000, .064);
      last = time;
      var ease = 1 - Math.exp(-dt / .2);
      x += (targetX - x) * ease;
      y += (targetY - y) * ease;
      paint(dt);
    }
    frame = window.requestAnimationFrame(tick);
  }
  function start() { if (!frame && enabled()) frame = window.requestAnimationFrame(tick); }
  function sync() {
    window.cancelAnimationFrame(frame);
    frame = 0; last = null;
    if (reduced.matches || !pointer.matches) { x = y = targetX = targetY = 0; paint(0); }
    if (toggle) toggle.hidden = reduced.matches;
    start();
  }
  window.addEventListener("pointermove", function (event) {
    if (!enabled() || !pointer.matches || event.pointerType !== "mouse") return;
    targetX = Math.max(-1, Math.min(1, event.clientX / window.innerWidth * 2 - 1));
    targetY = Math.max(-1, Math.min(1, event.clientY / window.innerHeight * 2 - 1));
  }, { passive: true });
  function center() { targetX = targetY = 0; }
  document.documentElement.addEventListener("pointerleave", center);
  window.addEventListener("blur", center);
  document.addEventListener("visibilitychange", sync);
  reduced.addEventListener("change", sync);
  pointer.addEventListener("change", sync);
  window.addEventListener("pagehide", function () { suspended = true; sync(); });
  window.addEventListener("pageshow", function () { suspended = false; sync(); });
  if (toggle) toggle.addEventListener("click", function () {
    paused = !paused;
    toggle.textContent = paused ? "Retomar fundo" : "Pausar fundo";
    toggle.setAttribute("aria-pressed", String(paused));
    sync();
  });
  paint(0);
  sync();
})();
