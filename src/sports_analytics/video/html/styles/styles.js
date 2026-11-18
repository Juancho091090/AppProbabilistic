// Fondos y comportamiento de cada estilo. El aspecto de los textos está en <estilo>.css.
window.STYLES = (() => {
  const svg = (w, h, inner, attrs = "") =>
    `<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg" ${attrs}>${inner}</svg>`;
  const layer = (bg, html, cls = "") => {
    const d = document.createElement("div");
    d.className = "decor " + cls;
    d.innerHTML = html;
    bg.appendChild(d);
    return d;
  };

  const footballPitch = (stroke, sw, extra = "") =>
    `<g fill="none" stroke="${stroke}" stroke-width="${sw}" ${extra}>
      <rect x="70" y="140" width="940" height="1640"/><line x1="70" y1="960" x2="1010" y2="960"/>
      <circle cx="540" cy="960" r="150"/><circle cx="540" cy="960" r="8" fill="${stroke}"/>
      <rect x="290" y="140" width="500" height="250"/><rect x="290" y="1530" width="500" height="250"/>
      <rect x="420" y="140" width="240" height="90"/><rect x="420" y="1690" width="240" height="90"/></g>`;
  const tennisCourt = (stroke, sw, extra = "") =>
    `<g fill="none" stroke="${stroke}" stroke-width="${sw}" ${extra}>
      <rect x="190" y="260" width="700" height="1420"/><rect x="270" y="260" width="540" height="1420"/>
      <line x1="270" y1="610" x2="810" y2="610"/><line x1="270" y1="1330" x2="810" y2="1330"/>
      <line x1="540" y1="610" x2="540" y2="1330"/><line x1="120" y1="970" x2="960" y2="970" stroke-width="${sw * 1.6}"/></g>`;

  // Colores de superficie para la cancha de tenis
  const SURFACE = {
    hard: ["#2F62A8", "#2C7A5B"], clay: ["#C8642F", "#A9522A"], grass: ["#5E9E3E", "#4E8A33"],
    carpet: ["#2F62A8", "#2C7A5B"], unknown: ["#2F62A8", "#2C7A5B"],
  };

  return {
    cuaderno: {
      ease: "linear",
      decor(bg) {
        layer(bg, "", "paper");
        layer(bg, "", "margin");
      },
    },
    pizarra: {
      ease: "linear",
      decor(bg, D) {
        layer(bg, "", "board");
        const draw = D.sport === "tennis" ? tennisCourt("#EEF1EA", 7) : footballPitch("#EEF1EA", 7);
        layer(bg, svg(1080, 1920, draw, 'filter="url(#chalk)"'), "chalk-drawing");
      },
    },
    marcador: {
      ease: "out",
      steps: 4,
      decor(bg) {
        layer(bg, "", "housing");
        layer(bg, "", "panel");
      },
      tick(t) {
        // Parpadeo del sello y leve titileo de la matriz, como en un tablero real
        document.body.classList.toggle("blink", Math.floor(t * 2.5) % 2 === 0);
      },
    },
    cancha: {
      ease: "out",
      decor(bg, D) {
        if (D.sport === "tennis") {
          const surface = (D.surface || "unknown").toLowerCase();
          const [inner, outer] = SURFACE[surface] || SURFACE.unknown;
          layer(bg, "", "court-outer").style.background = outer;
          layer(bg, "", "court-inner").style.background = inner;
          layer(bg, svg(1080, 1920, tennisCourt("#fff", 9), 'filter="url(#paint)"'), "lines");
          layer(bg, "", "net");
        } else {
          layer(bg, "", "grass");
          layer(bg, svg(1080, 1920, footballPitch("#fff", 9), 'filter="url(#paint)"'), "lines");
        }
        layer(bg, "", "grain");
        layer(bg, "", "vignette");
      },
    },
  };
})();
