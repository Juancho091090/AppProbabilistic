"""Vista previa de las plantillas del Short: captura fotogramas clave sin generar el video.

Uso: python scripts/short_preview.py salida.png [estilo ...]
Genera una hoja con los fotogramas de cada escena (fútbol y tenis) para revisar el diseño.
"""

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from sports_analytics.video import html_engine as E
from sports_analytics.video.render import timeline
from sports_analytics.video.sample import FAVORITE_FAIR, sample_payload, sample_recap
from sports_analytics.video.script import build_short_script, build_tennis_script


def shots_for(payload: dict) -> list[float]:
    """Un instante "asentado" por escena (casi al final de cada una)."""
    return [s["start"] + max(0.6, s["dur"] - 0.35) for s in payload["scenes"]]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview.png")
    styles = sys.argv[2:] or list(E.STYLES)
    p, fair, n = sample_payload()
    scripts = [
        build_short_script(
            p, {**fair, **FAVORITE_FAIR}, n, sample_recap(), True, ["ej2"], n_published=5
        ),
        build_tennis_script(p, 2),
    ]
    tz = ZoneInfo("America/Bogota")
    rows = []
    with sync_playwright() as pw:
        kw = {"args": ["--allow-file-access-from-files"]}
        if E.chrome_path():
            kw["executable_path"] = E.chrome_path()
        browser = pw.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        tmp = Path("/tmp/short_preview.html")
        for style in styles:
            for sc in scripts:
                payload = E.page_payload(sc, timeline(sc), tz, style, E._logo_uri())
                tmp.write_text(E.build_page(payload), encoding="utf-8")
                page.goto(tmp.as_uri())
                page.wait_for_function("window.SHORT_READY === true")
                page.evaluate("document.fonts.ready.then(() => window.fitAll())")
                page.wait_for_timeout(200)
                frames = []
                for t in shots_for(payload):
                    page.evaluate("t => window.renderAt(t)", t)
                    page.screenshot(path="/tmp/f.png")
                    frames.append(Image.open("/tmp/f.png").convert("RGB").resize((270, 480)))
                rows.append((f"{style} · {sc.sport}", frames))
        browser.close()
    width = max(len(f) for _, f in rows) * 280 + 20
    sheet = Image.new("RGB", (width, len(rows) * 530 + 10), "white")
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(E.HTML_DIR / "fonts" / "archivo-latin-800-normal.woff2"), 26)
    for r, (label, frames) in enumerate(rows):
        d.text((10, r * 530 + 8), label, font=font, fill=(20, 20, 20))
        for c, im in enumerate(frames):
            sheet.paste(im, (10 + c * 280, r * 530 + 42))
    sheet.save(out)
    print(out, sheet.size)


if __name__ == "__main__":
    main()
