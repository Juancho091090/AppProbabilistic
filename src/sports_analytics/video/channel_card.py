"""Imágenes del canal de Telegram (1080x1350): los partidos de confianza Alta de mañana
(noche) y cómo les fue (mañana siguiente).

Usa la plantilla del día (la misma del Short de fútbol) para que el canal y YouTube se vean
como una sola marca. Muestra exactamente los partidos que publica el mensaje del canal
(``select_channel_forecasts``), con la misma separación entre la regla del modelo y la del
mercado.
"""

from __future__ import annotations

import html
import json
import tempfile
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_analytics.reporting.channel import RULE_MARKET, ChannelPick
from sports_analytics.video import html_engine as E

W, H = 1080, 1350
MAX_ROWS = 8
_MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _league(name: str) -> str:
    tour, _, rest = name.partition(" · ")
    if not rest:
        return name
    return rest if rest.lower().startswith(tour.lower()) else f"{tour} {rest}"


def _time(dt, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'a. m.' if local.hour < 12 else 'p. m.'}"


def card_items(picks: list[ChannelPick], tz: ZoneInfo) -> list[dict]:
    """Una fila por partido publicado: favorito y su probabilidad según la regla que lo eligió."""
    out = []
    for pick in picks[:MAX_ROWS]:
        f = pick.forecast
        if f.sport == "football":
            names = (f.home_or_a, "Empate", f.away_or_b)
            if pick.rule == RULE_MARKET and pick.market_fair:
                probs = tuple(pick.market_fair)
            else:
                x = f.markets["1x2"]
                probs = (x["home"], x["draw"], x["away"])
            i = max(range(3), key=lambda k: probs[k])
            fav, p, sport = names[i], probs[i], "Fútbol"
        else:
            w = f.markets["winner"]
            fav, p = (f.home_or_a, w["A"]) if w["A"] >= w["B"] else (f.away_or_b, w["B"])
            sport = "Tenis"
        out.append(
            {
                "meta": f"{sport}, {_league(f.competition)}, {_time(f.kickoff_utc, tz)}",
                "match": f"{f.home_or_a} vs {f.away_or_b}",
                "fav": fav,
                "p": round(p * 100),
                "market": pick.rule == RULE_MARKET,
                "kickoff": f.kickoff_utc.isoformat(),
            }
        )
    return out


def card_html(items: list[dict], day: date, style: str, recap=None) -> str:
    def row(it: dict) -> str:
        cls = "cd-row market" if it["market"] else "cd-row"
        return (
            f'<div class="{cls}"><div><div class="cd-meta">{html.escape(it["meta"])}</div>'
            f'<div class="cd-match">{html.escape(it["match"])}</div></div>'
            f'<div class="cd-fav"><span class="who">{html.escape(it["fav"])}</span>'
            f'<span class="p">{it["p"]}%</span></div></div>'
        )

    model = sorted((i for i in items if not i["market"]), key=lambda i: i["kickoff"])
    market = sorted((i for i in items if i["market"]), key=lambda i: i["kickoff"])
    body = ""
    if model:
        body += f'<div class="cd-list">{"".join(row(i) for i in model)}</div>'
    if market:
        body += '<div class="cd-sub">Favoritos claros del mercado</div>'
        body += f'<div class="cd-list">{"".join(row(i) for i in market)}</div>'
    title = "Confianza Alta de mañana" if model else "Favoritos claros de mañana"
    recap_line = (
        f'<div class="cd-recap">Ayer: {recap.hits} de {recap.n} acertados</div>'
        if recap and recap.n
        else ""
    )
    return _page(style, len(items), _when(day), title, body, recap_line)


def _when(day: date) -> str:
    return f"{_DAYS[day.weekday()]} {day.day} de {_MONTHS[day.month - 1]}"


def _page(
    style: str, n_rows: int, date_line: str, title: str, body: str, foot: str, extra_class: str = ""
) -> str:
    density = " few" if n_rows <= 4 else (" dense" if n_rows >= 6 else "")
    css = (E.HTML_DIR / "styles" / f"{style}.css").read_text(encoding="utf-8") + (
        E.HTML_DIR / "card.css"
    ).read_text(encoding="utf-8")
    js = (E.HTML_DIR / "styles" / "styles.js").read_text(encoding="utf-8")
    data = json.dumps({"style": style, "sport": "football"})
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><style>
{E._font_css()}:root{{--grain:url({E._grain_uri()})}}
{css}</style></head><body class="st-{style}">{E.SVG_FILTERS}<div id="bg"></div>
<div class="card{density}{extra_class}">
 <div class="cd-head"><img src="{E._logo_uri()}" alt=""><div>
  <div class="cd-brand">JC Probabilidades</div><div class="cd-date">{date_line}</div></div></div>
 <div class="cd-title">{title}</div>
 <div class="cd-body">{body}</div>
 <div class="cd-foot">{foot}
  <div class="cd-legal">Probabilidades de un modelo estadístico, no recomendaciones. +18</div>
  <div class="cd-link">t.me/JCProbabilidades</div></div>
</div>
<script>{js}</script><script>
const D = {data}; (window.STYLES[D.style].decor || (() => {{}}))(document.getElementById('bg'), D);
</script></body></html>"""


# Marcas dibujadas a mano (trazo SVG): visto para acierto, equis para fallo.
_MARK = {
    True: '<svg class="cd-mark hit" viewBox="0 0 100 100" aria-label="Acierto">'
    '<path d="M14 56 C24 62 32 72 40 84 C52 58 68 34 88 14"/></svg>',
    False: '<svg class="cd-mark miss" viewBox="0 0 100 100" aria-label="Fallo">'
    '<path d="M20 18 C40 40 60 62 82 84"/><path d="M82 16 C60 40 42 60 18 86"/></svg>',
}


def results_html(recap, style: str, totals: tuple[int, int] | None = None) -> str:
    """Tarjeta de resultados del día ``recap.day``: cada partido publicado con su marca."""

    def row(it) -> str:
        state = {True: "hit", False: "miss", None: "pending"}[it.hit]
        cls = f"cd-row res {state}" + (" market" if it.market else "")
        sport = "Fútbol" if it.sport == "football" else "Tenis"
        meta = f"{sport}, {it.league}" if it.league else sport
        mark = _MARK.get(it.hit, '<span class="cd-wait">Sin resultado aún</span>')
        return (
            f'<div class="{cls}"><div><div class="cd-meta">{html.escape(meta)}</div>'
            f'<div class="cd-match">{html.escape(it.label)}</div></div>'
            f'<div class="cd-fav"><span class="who">{html.escape(it.pick)}</span>{mark}</div></div>'
        )

    items = recap.items[:MAX_ROWS]
    model = [i for i in items if not i.market]
    market = [i for i in items if i.market]
    body = ""
    if model:
        body += f'<div class="cd-list">{"".join(row(i) for i in model)}</div>'
    if market:
        body += '<div class="cd-sub">Favoritos claros del mercado</div>'
        body += f'<div class="cd-list">{"".join(row(i) for i in market)}</div>'
    title = f"{recap.hits} de {recap.n} acertados" if recap.n else "Aún sin resultados"
    foot = ""
    if totals and totals[1]:
        h, n = totals
        foot = (
            f'<div class="cd-recap">Acumulado del canal: {h} de {n} ({round(100 * h / n)} %)</div>'
        )
    return _page(
        style, len(items), f"Resultados del {_when(recap.day)}", title, body, foot, " results"
    )


# Si las filas no caben, encoge la lista (zoom) hasta que el pie quede dentro de la tarjeta.
_FIT_JS = """() => {
  const card = document.querySelector('.card'), list = document.querySelector('.cd-body'),
        foot = document.querySelector('.cd-foot');
  if (!card || !list || !foot) return 1;
  const limit = card.getBoundingClientRect().bottom
      - parseFloat(getComputedStyle(card).paddingBottom);
  let z = 1;
  const over = () => foot.getBoundingClientRect().bottom > limit + 1
      || list.getBoundingClientRect().bottom > foot.getBoundingClientRect().top;
  while (over() && z > 0.6) { z -= 0.02; list.style.zoom = z; }
  return z;
}"""


def render_card(
    picks: list[ChannelPick],
    day: date,
    out_path: Path,
    tz: str = "America/Bogota",
    style: str | None = None,
    recap=None,
) -> Path | None:
    """PNG de la tarjeta de la noche, o ``None`` si no hay partidos que mostrar."""
    items = card_items(picks, ZoneInfo(tz))
    if not items:
        return None
    style = style or E.style_for(day, "football")
    return _screenshot(card_html(items, day, style, recap), out_path)


def render_results_card(
    recap, out_path: Path, style: str | None = None, totals: tuple[int, int] | None = None
) -> Path | None:
    """PNG de los resultados de ``recap.day`` en la misma plantilla que se publicó esa noche
    (la del día del partido), o ``None`` si no hay partidos."""
    if recap is None or not recap.items:
        return None
    style = style or E.style_for(recap.day, "football")
    return _screenshot(results_html(recap, style, totals), out_path)


def _screenshot(page_html: str, out_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "card.html"
        page_file.write_text(page_html, encoding="utf-8")
        with sync_playwright() as p:
            kw = {"args": ["--allow-file-access-from-files"]}
            if E.chrome_path():
                kw["executable_path"] = E.chrome_path()
            browser = p.chromium.launch(**kw)
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto(page_file.as_uri())
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(150)
            page.evaluate(_FIT_JS)
            page.screenshot(path=str(out_path))
            browser.close()
    return out_path
