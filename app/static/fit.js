// Passt die Seiten an jeden Bildschirm an. Entworfen sind sie für 1920×1080 ("Design-Pixel");
// dieses Skript skaliert die Seite (body) so, dass sie den Bildschirm ausfüllt:
//   16:9 (720p, 1080p, 4K …)    genau 1920×1080, nur größer oder kleiner
//   höher (16:10, 5:4, 4:3)     1920 breit, mehr Höhe für die Stunden (bis 4:3)
//   breiter (21:9, 32:9)        1080 hoch, mehr Breite für die mittlere Spalte (bis 32:9)
//   noch schmaler/breiter       zentriert mit Rand (z. B. Hochkant-Bildschirm)
// DISPLAY_MARGIN (config.env, in %) lässt rundherum Rand – gegen Overscan bei Fernsehern.
// Läuft im <head>, bevor etwas gezeichnet wird, und bei jeder Größenänderung (z. B. Drehen).
(function () {
  const W = 1920, H = 1080;
  const MAX_H = W * 3 / 4, MAX_W = H * 32 / 9;
  const root = document.documentElement;

  function fit() {
    const m = Math.min(20, Math.max(0, parseFloat(root.dataset.margin) || 0)) / 100;
    const vw = window.innerWidth, vh = window.innerHeight;
    if (!vw || !vh) return;
    const aw = vw * (1 - 2 * m), ah = vh * (1 - 2 * m);       // verfügbare Fläche
    const s = Math.min(aw / W, ah / H);                       // Maßstab: Design-Pixel -> Bildschirm
    const w = Math.min(aw / s, MAX_W), h = Math.min(ah / s, MAX_H);
    const set = (k, v) => root.style.setProperty(k, v);
    set("--fit-s", s);
    set("--fit-w", w + "px");
    set("--fit-h", h + "px");
    set("--fit-x", (vw - w * s) / 2 + "px");
    set("--fit-y", (vh - h * s) / 2 + "px");
  }

  fit();
  window.addEventListener("resize", fit);
})();
