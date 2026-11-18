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
        const r = rv(h("div", "row" + (v === top ? " top" : "")), p.rows[i], 0.9);
        r.style.setProperty("--p", v / d.scale);
        const bar = h("div", "bar");
        bar.appendChild(h("div", "fill"));
        r.append(h("div", "label fit", label), bar, counter(h("div", "val"), v, "pct"));
        rows.appendChild(r);
      });
      s.appendChild(rows);
      if (d.aside) s.appendChild(rv(h("div", "aside", d.aside), p.aside, 0.6));
      if (d.stamp) s.appendChild(rv(h("div", "stamp", d.stamp), p.stamp, 0.35, "stamp"));
    },
    why(s, d, p) {
      s.appendChild(rv(h("div", "title", d.title), p.title, 0.5));
      const box = h("div", "duels");
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
    prog.firstChild.style.width = (100 * clamp(tg / D.total)).toFixed(2) + "%";
    if (STYLE.tick) STYLE.tick(tg);
  };
  window.SHORT_READY = true;
})();
