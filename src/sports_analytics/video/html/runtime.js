// Motor de animación de los Shorts. Determinista: todo depende de renderAt(t), sin relojes.
// Python inyecta window.SHORT = {style, sport, total, trans, logo, scenes:[{kind,start,dur,lead,data,plan,words}]}
// Cada elemento animado lleva data-at (inicio dentro de la escena) y data-d (duración); el CSS
// de cada estilo decide cómo se ve la entrada leyendo la variable --k (0→1).
(() => {
  const D = window.SHORT;
  const STYLE = window.STYLES[D.style];
  const clamp = (x) => Math.min(1, Math.max(0, x));
  const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
  const inOut = (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
  const back = (x, s = 1.9) => { x = clamp(x) - 1; return 1 + (s + 1) * x * x * x + s * x * x; };
  const EASE = { out: easeOut, linear: clamp, back, inout: inOut };

  const h = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const rv = (e, at, d = 0.5, kind = "") => {
    e.classList.add("rv");
    if (kind) e.classList.add("rv-" + kind);
    e.dataset.at = at;
    e.dataset.d = d;
    return e;
  };
  const counter = (e, value, fmt) => { e.dataset.count = value; e.dataset.fmt = fmt; return e; };
  const fmtNum = (v, fmt) => {
    if (fmt === "pct") return Math.round(v * 100) + "%";
    if (fmt === "int") return String(Math.round(v));
    if (fmt === "dec") return v.toFixed(1).replace(".", ",");
    if (fmt === "dec2") return v.toFixed(2).replace(".", ",");
    return String(Math.round(v));
  };

  // ------------------------------------------------------------ escenas genéricas
  const BUILD = {
    intro(s, d, p) {
      const logo = rv(h("div", "logo-big"), p.logo, 0.5, "pop");
      logo.appendChild(Object.assign(h("img"), { src: D.logo }));
      s.append(logo, rv(h("div", "title fit", "JC Probabilidades"), p.title, 0.6),
        rv(h("div", "sub", d.sub), p.sub, 0.6));
      const tags = h("div", "tags");
      d.tags.forEach((t, i) => tags.appendChild(rv(h("div", "tag", t), p.tags[i], 0.4, "pop")));
      s.appendChild(tags);
    },
    recap(s, d, p) {
      s.appendChild(rv(h("div", "title mood-" + d.mood, d.title), p.title, 0.5));
      const sc = rv(h("div", "score"), p.score, p.score_d, "count");
      sc.append(counter(h("span", "num"), d.hits, "int"), h("span", "of", "/" + d.n));
      s.append(sc, rv(h("div", "score-label", "aciertos"), p.score, 0.5));
      const rows = h("div", "recap-rows");
      d.items.forEach(([label, pick, hit], i) => {
        if (i >= p.rows.length) return;
        const r = rv(h("div", "rrow " + (hit ? "hit" : "miss")), p.rows[i], 0.35);
        r.append(h("span", "mark", hit ? "✓" : "✗"), h("span", "rlabel fit", label), h("span", "rpick", "fav. " + pick));
        rows.appendChild(r);
      });
      s.appendChild(rows);
      if (d.note) s.appendChild(rv(h("div", "note-small", d.note), p.note, 0.4));
    },
    hook(s, d, p) {
      s.appendChild(rv(h("div", "kicker", d.kicker), p.kicker, 0.6));
      s.appendChild(rv(h("div", "team home fit", d.home), p.home, 0.55, "left"));
      s.appendChild(rv(h("div", "vs", "vs"), p.vs, 0.45, "pop"));
      s.appendChild(rv(h("div", "team away fit", d.away), p.away, 0.55, "right"));
      const note = rv(h("div", "note"), p.note, 0.5, "pop");
      note.append(h("div", "note-head", d.note_head), h("div", "note-big", d.note_big), h("div", "note-foot", d.note_foot));
      s.appendChild(note);
      s.appendChild(rv(h("div", "when", d.when), p.when, 0.5));
    },
    bars(s, d, p) {
      s.appendChild(rv(h("div", "title", d.title), p.title, 0.5));
      const rows = h("div", "rows rows-" + d.rows.length);
      const top = Math.max(...d.rows.map((r) => r[1]));
      d.rows.forEach(([label, v], i) => {
        // d.me: fila destacada (p. ej. el equipo del video); si no, se destaca el mayor valor
        const mark = d.me ? (label === d.me ? " me" : "") : (v === top ? " top" : "");
        const r = rv(h("div", "row" + mark), p.rows[i], 0.9);
        r.style.setProperty("--p", v / d.scale);
        const bar = h("div", "bar");
        bar.appendChild(h("div", "fill"));
        const lab = h("div", "label fit");
        lab.appendChild(h("span", "ltext", label));
        const tag = (d.tags || {})[label];
        if (tag) lab.appendChild(h("span", "rtag rtag-" + tag.kind, tag.text));
        r.append(lab, bar, counter(h("div", "val"), v, d.fmt || "pct"));
        rows.appendChild(r);
      });
      s.appendChild(rows);
      if (d.aside) s.appendChild(rv(h("div", "aside", d.aside), p.aside, 0.6));
      if (d.stamp) s.appendChild(rv(h("div", "stamp", d.stamp), p.stamp, 0.35, "stamp"));
    },
    why(s, d, p) {
      s.appendChild(rv(h("div", "title", d.title), p.title, 0.5));
      const box = h("div", "duels duels-" + d.duels.length);
      d.duels.forEach((du, i) => {
        const [ln, lv] = du.left, [rn, rvv] = du.right;
        const top = Math.max(lv, rvv) || 1;
        const e = rv(h("div", "duel"), p.duels[i], 0.9);
        e.style.setProperty("--lp", lv / top);
        e.style.setProperty("--rp", rvv / top);
        const names = h("div", "dnames");
        names.append(h("span", "dl", ln), h("span", "dr", rn));
        const track = h("div", "track");
        track.append(h("div", "lfill" + (lv >= rvv ? " win" : "")), h("div", "rfill" + (rvv > lv ? " win" : "")));
        const vals = h("div", "dvals");
        vals.append(counter(h("span", "dl" + (lv >= rvv ? " win" : "")), lv, du.fmt), h("span", "dvs", "vs"),
          counter(h("span", "dr" + (rvv > lv ? " win" : "")), rvv, du.fmt));
        e.append(h("div", "dtitle", du.title), names, track, vals);
        box.appendChild(e);
      });
      s.appendChild(box);
    },
    cta(s, d, p) {
      s.appendChild(rv(h("div", "q", d.question), p.q, 0.5));
      if (d.n_high) {
        s.appendChild(rv(counter(h("div", "num"), d.n_high, "int"), p.num, p.num_d, "count"));
        s.appendChild(rv(h("div", "numlabel", d.num_label), p.num, 0.6));
      } else {
        s.appendChild(rv(h("div", "numlabel big", "Cada noche, gratis"), p.num, 0.6));
      }
      s.appendChild(rv(h("div", "pill", "Telegram: " + d.handle), p.pill, 0.5, "pop"));
      s.appendChild(rv(h("div", "disc", d.disclaimer), p.pill, 0.5));
    },
  };
  BUILD.sets = BUILD.bars;


  // ------------------------------------------------------------ presentador ficticio
  // Leo: analista inventado, dibujado en SVG. Boca, parpadeo y cabeza se mueven en
  // updateHost() según las palabras narradas (sc.words), así queda sincronizado con la voz.
  const HOST_SVG = `
<svg class="host-svg" viewBox="0 0 1080 1120" aria-hidden="true">
  <defs>
    <radialGradient id="hSpot" cx="50%" cy="45%" r="50%">
      <stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="hSkin" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#C98C62"/><stop offset="1" stop-color="#B27650"/>
    </linearGradient>
    <linearGradient id="hJacket" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B3458"/><stop offset="1" stop-color="#0F2140"/>
    </linearGradient>
  </defs>
  <ellipse cx="540" cy="470" rx="470" ry="440" fill="url(#hSpot)"/>
  <g class="h-body">
    <path d="M150 1120 C170 960 250 880 400 840 L470 820 L540 900 L610 820 L680 840 C830 880 910 960 930 1120 Z" fill="url(#hJacket)"/>
    <path d="M470 820 L540 900 L610 820 L590 812 L540 860 L490 812 Z" fill="#F4F6FA"/>
    <path d="M520 870 L540 900 L560 870 L552 1000 L540 1020 L528 1000 Z" fill="#C6F432"/>
    <path d="M470 820 L430 900 L500 1060 L540 905 Z" fill="#0C1A33" opacity=".55"/>
    <path d="M610 820 L650 900 L580 1060 L540 905 Z" fill="#0C1A33" opacity=".55"/>
    <path d="M700 960 L770 960 L765 985 L705 985 Z" fill="#C6F432"/>
  </g>
  <g class="h-head">
    <rect x="490" y="700" width="100" height="140" rx="30" fill="#A96E48"/>
    <ellipse cx="392" cy="545" rx="30" ry="46" fill="#B9794F"/>
    <ellipse cx="688" cy="545" rx="30" ry="46" fill="#B9794F"/>
    <ellipse cx="540" cy="520" rx="150" ry="188" fill="url(#hSkin)"/>
    <path d="M410 600 C420 690 470 735 540 740 C610 735 660 690 670 600 C650 650 610 700 540 702 C470 700 430 650 410 600 Z" fill="#2A1A12" opacity=".16"/>
    <path d="M388 470 C380 360 440 300 540 298 C650 296 705 365 694 470 C676 420 640 395 600 392 C560 410 500 410 460 392 C420 400 398 430 388 470 Z" fill="#231915"/>
    <path d="M405 440 C415 380 460 340 520 335" stroke="#3B2A22" stroke-width="10" fill="none" stroke-linecap="round" opacity=".6"/>
    <g class="h-brows">
      <rect x="440" y="440" width="76" height="14" rx="7" fill="#231915" transform="rotate(-4 478 447)"/>
      <rect x="564" y="440" width="76" height="14" rx="7" fill="#231915" transform="rotate(4 602 447)"/>
    </g>
    <g class="h-eyes">
      <g class="h-eye" transform="translate(478 500)"><g class="h-lid"><ellipse rx="26" ry="20" fill="#fff"/><circle r="12" fill="#2B1C14"/><circle cx="-4" cy="-5" r="4" fill="#fff"/></g></g>
      <g class="h-eye" transform="translate(602 500)"><g class="h-lid"><ellipse rx="26" ry="20" fill="#fff"/><circle r="12" fill="#2B1C14"/><circle cx="-4" cy="-5" r="4" fill="#fff"/></g></g>
    </g>
    <g fill="none" stroke="#14294A" stroke-width="8">
      <rect x="434" y="465" width="88" height="70" rx="22"/>
      <rect x="558" y="465" width="88" height="70" rx="22"/>
      <path d="M522 492 Q540 482 558 492"/>
    </g>
    <path d="M540 520 C534 560 528 585 522 598 C532 606 548 606 558 598" stroke="#8E5A3A" stroke-width="7" fill="none" stroke-linecap="round"/>
    <g class="h-mouth" transform="translate(540 650)">
      <ellipse class="h-mouth-in" rx="46" ry="6" fill="#5B1D1D"/>
      <ellipse class="h-tongue" cx="0" cy="10" rx="26" ry="0" fill="#C24E4E"/>
      <path class="h-lip" d="M-48 0 Q0 14 48 0" stroke="#7A3524" stroke-width="7" fill="none" stroke-linecap="round"/>
    </g>
  </g>
</svg>`;
  BUILD.host = (el, d, p) => {
    const wrap = rv(h("div", "host"), p.host, 0.5);
    wrap.innerHTML = HOST_SVG;
    el.appendChild(wrap);
    const card = rv(h("div", "host-card"), p.card, 0.45);
    card.append(h("div", "host-name", d.name || "Leo, analista de datos"));
    if (d.top) card.append(h("div", "host-top", d.top));
    if (d.big) {
      const big = rv(h("div", "host-big rv-pop"), p.big, 0.5);
      big.textContent = d.big;
      card.append(big);
    }
    if (d.small) card.append(h("div", "host-small", d.small));
    el.appendChild(card);
  };
  // Apertura de boca (0..1) a partir de las palabras: sílabas aproximadas por palabra
  const mouthAt = (words, t) => {
    for (const w of words) {
      const a = w[0], b = w[1];
      if (t >= a && t < b) {
        const syl = Math.max(1, Math.round((w[3] || "").replace(/[^aeiouáéíóú]/gi, "").length));
        const ph = ((t - a) / Math.max(0.05, b - a)) * syl;
        return 0.25 + 0.75 * Math.abs(Math.sin(Math.PI * ph));
      }
    }
    return 0;
  };
  const updateHost = (el, t, words) => {
    const svg = el.querySelector(".host-svg");
    if (!svg) return;
    const m = mouthAt(words || [], t);
    svg.querySelector(".h-mouth-in").setAttribute("ry", (5 + 30 * m).toFixed(1));
    svg.querySelector(".h-tongue").setAttribute("ry", (m > 0.4 ? 10 * m : 0).toFixed(1));
    svg.querySelector(".h-lip").style.opacity = m > 0.15 ? 0 : 1;
    // parpadeo cada ~3.4 s (0.14 s), cabeza que acompaña la voz
    const cyc = t % 3.4, blink = cyc > 3.26 ? 0.1 : 1;
    svg.querySelectorAll(".h-lid").forEach((l) => l.setAttribute("transform", `scale(1 ${blink})`));
    const talk = m > 0 ? 1 : 0.4;
    const rot = Math.sin(t * 1.6) * 1.6 * talk, dy = Math.sin(t * 3.1) * 3 * talk;
    svg.querySelector(".h-head").setAttribute("transform", `rotate(${rot.toFixed(2)} 540 760) translate(0 ${dy.toFixed(1)})`);
    svg.querySelector(".h-brows").setAttribute("transform", `translate(0 ${(-6 * m * (Math.sin(t * 5) > 0.6 ? 1 : 0)).toFixed(1)})`);
  };
  // ------------------------------------------------------------ montaje
  const stage = document.getElementById("stage");
  const bg = document.getElementById("bg");
  (STYLE.decor || (() => {}))(bg, D);
  const frames = [];
  D.scenes.forEach((sc) => {
    const el = h("div", "scene scene-" + sc.kind + " sport-" + D.sport);
    (BUILD[sc.kind] || (() => {}))(el, sc.data, sc.plan);
    el.style.visibility = "hidden";
    stage.appendChild(el);
    frames.push({ sc, el, anim: [...el.querySelectorAll("[data-at]")] });
  });
  const wipe = h("div", "wipe");
  document.body.appendChild(wipe);
  const caps = h("div", "caps");
  document.body.appendChild(caps);
  const prog = h("div", "progress");
  prog.appendChild(h("div", "progress-fill"));
  document.body.appendChild(prog);
  const logo = h("div", "logo-small");
  logo.appendChild(Object.assign(h("img"), { src: D.logo }));
  document.body.appendChild(logo);

  // Ajusta el tamaño de los textos marcados con .fit para que quepan en su caja
  window.fitAll = () => {
    document.querySelectorAll(".fit").forEach((e) => {
      e.style.whiteSpace = "nowrap";
      const range = document.createRange();
      range.selectNodeContents(e);
      const limit = () => Math.min(e.getBoundingClientRect().right, 1000);
      let size = parseFloat(getComputedStyle(e).fontSize);
      let guard = 80;
      while (range.getBoundingClientRect().right > limit() + 1 && size > 24 && guard--) {
        size -= 3;
        e.style.fontSize = size + "px";
      }
    });
  };

  const ease = EASE[STYLE.ease || "out"];
  const animate = (f, t) => {
    for (const e of f.anim) {
      const at = +e.dataset.at, d = +e.dataset.d;
      let raw = clamp((t - at) / d);
      let k = e.classList.contains("rv-pop") || e.classList.contains("rv-count") ? back(raw) : ease(raw);
      if (e.classList.contains("rv-stamp")) k = easeOut(raw);
      if (STYLE.steps && raw > 0 && raw < 1) k = Math.round(raw * STYLE.steps) / STYLE.steps; // LED: a saltos
      e.style.setProperty("--k", k.toFixed(4));
      e.style.setProperty("--raw", raw.toFixed(4));
      e.classList.toggle("on", raw > 0);
      if (e.dataset.count !== undefined) e.textContent = fmtNum(+e.dataset.count * clamp(raw), e.dataset.fmt);
      e.querySelectorAll("[data-count]").forEach((c) => {
        c.textContent = fmtNum(+c.dataset.count * clamp(raw), c.dataset.fmt);
      });
    }
  };

  const drawCaps = (sc, t) => {
    const words = sc.words || [];
    if (!words.length) { caps.innerHTML = ""; return; }
    let cur = words.findIndex((w) => t < w[1]);
    if (cur < 0) cur = words.length - 1;
    const chunk = words[cur][2];
    const key = sc.start + ":" + chunk + ":" + cur;
    if (caps.dataset.key === key) return;
    caps.dataset.key = key;
    caps.innerHTML = "";
    const line = h("div", "caps-line");
    words.forEach((w, i) => {
      if (w[2] !== chunk) return;
      line.appendChild(h("span", i === cur ? "w now" : i < cur ? "w said" : "w next", w[3]));
      line.appendChild(document.createTextNode(" "));
    });
    caps.appendChild(line);
  };

  window.renderAt = (tg) => {
    let i = 0;
    frames.forEach((f, j) => { if (f.sc.start <= tg + 1e-6) i = j; });
    const f = frames[i];
    const t = tg - f.sc.start;
    frames.forEach((g) => (g.el.style.visibility = "hidden"));
    const tr = D.trans;
    let showPrev = false;
    if (i > 0 && t < tr) {
      const e = inOut(t / tr);
      wipe.style.visibility = "visible";
      wipe.style.setProperty("--e", e.toFixed(4));
      showPrev = e < 0.5;
    } else {
      wipe.style.visibility = "hidden";
    }
    if (showPrev) {
      const p = frames[i - 1];
      animate(p, p.sc.dur);
      p.el.style.visibility = "visible";
    } else {
      animate(f, t);
      f.el.style.visibility = "visible";
    }
    document.body.classList.toggle("is-intro", f.sc.kind === "intro");
    drawCaps(f.sc, t);
    if (f.sc.kind === "host" && !showPrev) updateHost(f.el, t, f.sc.words);
    prog.firstChild.style.width = (100 * clamp(tg / D.total)).toFixed(2) + "%";
    if (STYLE.tick) STYLE.tick(tg);
  };
  window.SHORT_READY = true;
})();
