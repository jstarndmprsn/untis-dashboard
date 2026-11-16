// Optional – delete this file if your theme doesn't need JavaScript (most don't).
// It runs on every page the theme applies to, after the page's own script.
// Keep it light: the Pi drives a 1080p screen all day. Prefer CSS animations of transform/opacity,
// a few dozen elements at most, and nothing that runs every frame in JavaScript.
(() => {
  const page = document.body.classList.contains("page-heute") ? "heute"
             : document.body.classList.contains("page-woche") ? "woche" : "thermal";
  const front = document.querySelector(".theme-front");   // decoration layer in front of the content
  if (!front || page === "thermal") return;
  // Example: add elements to the layer and animate them with CSS from theme.css
  // for (let i = 0; i < 30; i++) { const s = document.createElement("i"); s.className = "flake"; front.append(s); }
})();
