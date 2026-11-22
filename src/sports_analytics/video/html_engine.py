"""Motor de video con plantillas HTML (cuaderno, pizarra, marcador, cancha) dibujadas por Chrome.

Python arma la línea de tiempo (voz de Google TTS, duración de cada escena, subtítulos palabra a
palabra, momentos de cada animación) y el audio (voz + música + efectos). La página HTML recibe
esos datos y ``renderAt(t)`` dibuja cada fotograma; Playwright toma la captura y ffmpeg arma el
MP4. Los momentos de las animaciones salen de un único plan (``plan_for``), compartido por la
página y los efectos de sonido, para que todo quede sincronizado.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_analytics.video.render import PAD, TRANS, timeline, word_times
from sports_analytics.video.script import ShortScript

HTML_DIR = Path(__file__).parent / "html"
ASSETS = Path(__file__).parent / "assets"
FPS = 30
TAIL = 0.8
STYLES = ("cuaderno", "pizarra", "marcador", "cancha")
FONTS = [
    ("Kalam", 400, "kalam-latin-400-normal"),
    ("Kalam", 700, "kalam-latin-700-normal"),
    ("Caveat", 400, "caveat-latin-400-normal"),
    ("Caveat", 700, "caveat-latin-700-normal"),
    ("Barlow Condensed", 700, "barlow-condensed-latin-700-normal"),
    ("Barlow Condensed", 800, "barlow-condensed-latin-800-normal"),
    ("Oswald", 700, "oswald-latin-700-normal"),
    ("Archivo", 500, "archivo-latin-500-normal"),
    ("Archivo", 800, "archivo-latin-800-normal"),
    ("Doto", 700, "doto-latin-700-normal"),
    ("Doto", 900, "doto-latin-900-normal"),
]
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
SURFACE_ES = {
    "hard": "pista dura",
    "clay": "arcilla",
    "grass": "césped",
    "carpet": "pista cubierta",
}

SVG_FILTERS = """<svg width="0" height="0" style="position:absolute">
 <filter id="rough"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3"/>
  <feDisplacementMap in="SourceGraphic" scale="6"/></filter>
 <filter id="chalk"><feTurbulence type="fractalNoise" baseFrequency="1.1" numOctaves="2" seed="8" result="n"/>
  <feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 -0.9 1.45" result="m"/>
  <feComposite in="SourceGraphic" in2="m" operator="in"/></filter>
 <filter id="paint"><feTurbulence type="fractalNoise" baseFrequency="0.6" numOctaves="2" seed="5" result="n"/>
  <feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 -0.9 1.15" result="m"/>
  <feComposite in="SourceGraphic" in2="m" operator="in"/></filter>
</svg>"""


def style_for(day: date, sport: str) -> str:
    """Rotación diaria; el tenis va desfasado para que los dos Shorts del día no se parezcan."""
    offset = 2 if sport == "tennis" else 0
    return STYLES[(day.toordinal() + offset) % len(STYLES)]


def when_es(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    hour = local.hour % 12 or 12
    suffix = "a. m." if local.hour < 12 else "p. m."
    return f"{_DAYS[local.weekday()]} {local.day}, {hour}:{local.minute:02d} {suffix}"


# ------------------------------------------------------------------ plan de animación


def duel_times(n: int, dur: float) -> list[float]:
    step = max((dur - PAD - 0.3) / max(n, 1), 0.9)
    return [0.3 + i * step for i in range(n)]


def plan_for(kind: str, data: dict, dur: float) -> dict:
    """Segundo (dentro de la escena) en que entra cada elemento. Lo usan la página y el audio."""
    if kind == "intro":
        return {"logo": 0.0, "title": 0.3, "sub": 0.6, "tags": [1.0 + 0.25 * i for i in range(3)]}
    if kind == "recap":
        rows = [0.9 + 0.22 * i for i in range(min(len(data["items"]), 5))]
        return {
            "title": 0.0,
            "score": 0.25,
            "score_d": 0.75,
            "rows": rows,
            "note": (rows[-1] if rows else 1.0) + 0.3,
        }
    if kind == "hook":
        return {"kicker": 0.0, "home": 0.15, "vs": 0.45, "away": 0.6, "note": 0.95, "when": 1.2}
    if kind in ("bars", "sets"):
        rows = [0.35 + 0.3 * i for i in range(len(data["rows"]))]
        plan = {"title": 0.0, "rows": rows, "aside": rows[-1] + 0.9}
        if data.get("stamp"):
            plan["stamp"] = max(2.0, 0.55 * dur)
        return plan
    if kind == "why":
        return {"title": 0.0, "duels": duel_times(len(data["duels"]), dur)}
    if kind == "cta":
        return {"q": 0.0, "num": 0.4, "num_d": 0.7, "pill": 1.3}
    return {}


def sfx_for(scenes: list[dict]) -> list[tuple[float, str]]:
    ev: list[tuple[float, str]] = []
    for i, s in enumerate(scenes):
        s0, p, kind = s["start"], s["plan"], s["kind"]
        ev.append((s0, "whoosh") if i else (0.0, "hit"))
        if kind == "hook":
            ev.append((s0 + p["vs"], "pop"))
        elif kind == "intro":
            ev += [(s0 + t, "pop") for t in p["tags"]]
        elif kind == "recap":
            ev.append((s0 + p["score"] + p["score_d"], "hit"))
            ev += [(s0 + t, "pop") for t in p["rows"]]
        elif kind in ("bars", "sets"):
            ev += [(s0 + t, "pop") for t in p["rows"]]
            if "stamp" in p:
                ev.append((s0 + p["stamp"], "hit"))
        elif kind == "why":
            ev += [(s0 + t, "pop") for t in p["duels"]]
        elif kind == "cta" and s["data"].get("n_high"):
            ev.append((s0 + p["num"] + p["num_d"], "hit"))
    return ev


# ------------------------------------------------------------------ datos de cada escena


def scene_data(kind: str, data: dict, tz: ZoneInfo) -> dict:
    """Convierte los datos del guion (pensados también para el motor anterior) al formato
    genérico de la página. Las escenas de tenis ya vienen en ese formato."""
    if data.get("generic"):
        return {k: v for k, v in data.items() if k != "generic"}
    if kind == "intro":
        return {"sub": "Un modelo estadístico analiza cada partido", "tags": data.get("tags", [])}
    if kind == "recap":
        titles = {
            "good": "¡Gran día ayer!",
            "ok": "Así nos fue ayer",
            "bad": "Ayer no fue nuestro día",
        }
        note = (
            "Publicados en el canal de Telegram"
            if data["source"] == "canal"
            else "Partidos de confianza Alta del modelo"
        )
        extra = len(data["items"]) - 5
        if extra > 0:
            note = f"y {extra} más. " + note
        return {
            "title": titles[data["mood"]],
            "mood": data["mood"],
            "hits": data["hits"],
            "n": data["n"],
            "items": [list(i) for i in data["items"]],
            "note": note,
        }
    if kind == "hook":
        return {
            "kicker": data.get("kicker", "Mañana, el partido más abierto"),
            "home": data["home"],
            "away": data["away"],
            "note_head": data.get("note_head", "el modelo dice:"),
            "note_big": data.get("note_big", ""),
            "note_foot": data.get("note_foot", "local, empate, visitante"),
            "when": f"{data['competition']}, {when_es(data['kickoff'], tz)}",
        }
    if kind == "bars":
        rows = [[label, float(p)] for label, p in data["rows"]]
        top = max(p for _, p in rows)
        low = min(p for _, p in rows)
        out = {"title": data.get("title", "¿Quién gana?"), "rows": rows, "scale": max(0.5, top)}
        if data.get("stamp"):
            out["stamp"] = data["stamp"]
        elif top < 0.5:  # el sello resume; la diferencia exacta la dice la voz
            out["stamp"] = f"Sin favorito claro: {round((top - low) * 100)} puntos"
        return out
    if kind == "why":
        duels = [
            {
                "title": d["title"],
                "left": list(d["left"]),
                "right": list(d["right"]),
                "fmt": d["fmt"],
            }
            for d in data.get("duels", [])
        ]
        return {"title": data.get("title", "¿Por qué tan parejo?"), "duels": duels}
    if kind == "cta":
        return {
            "question": data.get("question", "¿Quieres los partidos con favorito claro?"),
            "n_high": data.get("n_high", 0),
            "num_label": data.get("num_label", "con confianza Alta mañana"),
            "handle": data.get("handle", "@JCProbabilidades"),
            "disclaimer": "Probabilidades de un modelo estadístico, no recomendaciones. +18",
        }
    return dict(data)


def _font_css() -> str:
    return "".join(
        f"@font-face{{font-family:'{fam}';font-weight:{w};"
        f"src:url('{(HTML_DIR / 'fonts' / (f + '.woff2')).as_uri()}') format('woff2');}}"
        for fam, w, f in FONTS
    )


def _grain_uri() -> str:
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(4)
    noise = (rng.random((256, 256)) * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(noise, "L").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_page(payload: dict) -> str:
    style = payload["style"]
    css = (HTML_DIR / "base.css").read_text(encoding="utf-8") + (
        HTML_DIR / "styles" / f"{style}.css"
    ).read_text(encoding="utf-8")
    js = (HTML_DIR / "styles" / "styles.js").read_text(encoding="utf-8")
    runtime = (HTML_DIR / "runtime.js").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><style>
{_font_css()}:root{{--grain:url({_grain_uri()})}}
{css}</style></head><body>{SVG_FILTERS}<div id="bg"></div><div id="stage"></div>
<script>window.SHORT={data};</script><script>{js}</script><script>{runtime}</script></body></html>"""


def page_payload(script: ShortScript, timed, tz: ZoneInfo, style: str, logo_uri: str) -> dict:
    scenes = []
    for ts in timed:
        data = scene_data(ts.scene.kind, ts.scene.data, tz)
        scenes.append(
            {
                "kind": ts.scene.kind,
                "start": round(ts.start, 4),
                "dur": round(ts.duration, 4),
                "data": data,
                "plan": plan_for(ts.scene.kind, data, ts.duration),
                "words": [
                    [round(a, 3), round(b, 3), c, w]
                    for a, b, c, w in word_times(ts.scene, ts.duration, ts.lead)
                ],
            }
        )
    last = timed[-1]
    return {
        "style": style,
        "sport": script.sport,
        "surface": script.surface,
        "trans": TRANS,
        "total": round(last.start + last.duration + TAIL, 4),
        "logo": logo_uri,
        "scenes": scenes,
    }


def _logo_uri() -> str:
    return "data:image/png;base64," + base64.b64encode((ASSETS / "logo.png").read_bytes()).decode()


def chrome_path() -> str | None:
    """Chrome del sistema si existe (GitHub Actions lo trae); si no, el de Playwright."""
    for cand in (
        os.environ.get("SHORT_CHROME"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ):
        if cand:
            return cand
    return None


def render_frames(html: str, total: float, out_video: Path, audio_wav: Path) -> None:
    from playwright.sync_api import sync_playwright

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg no está instalado")
    n = int(round(total * FPS))
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "short.html"
        page_file.write_text(html, encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-framerate",
            str(FPS),
            "-i",
            "-",
            "-i",
            str(audio_wav),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-movflags",
            "+faststart",
            str(out_video),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            with sync_playwright() as p:
                kw = {"args": ["--allow-file-access-from-files", "--disable-gpu-vsync"]}
                if chrome_path():
                    kw["executable_path"] = chrome_path()
                browser = p.chromium.launch(**kw)
                page = browser.new_page(viewport={"width": 1080, "height": 1920})
                page.goto(page_file.as_uri())
                page.wait_for_function("window.SHORT_READY === true")
                page.evaluate("document.fonts.ready.then(() => window.fitAll())")
                page.wait_for_timeout(150)
                for k in range(n):
                    page.evaluate("t => window.renderAt(t)", k / FPS)
                    proc.stdin.write(page.screenshot(type="jpeg", quality=90))
                browser.close()
        finally:
            proc.stdin.close()
            if proc.wait() != 0:
                raise RuntimeError("ffmpeg falló al generar el video")


def render_short_html(
    script: ShortScript,
    out_path: Path,
    tz: str = "America/Bogota",
    tts=None,
    music_file: str | None = None,
    style: str | None = None,
    day: date | None = None,
) -> Path:
    """MP4 del Short con la plantilla del día (o ``style``)."""
    from sports_analytics.video.audio import mix_soundtrack

    zone = ZoneInfo(tz)
    style = style or style_for(day or date.today(), script.sport)
    if style not in STYLES:
        raise ValueError(f"Plantilla desconocida: {style}. Opciones: {', '.join(STYLES)}")
    timed = timeline(script, tts)
    payload = page_payload(script, timed, zone, style, _logo_uri())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        voices = [(ts.start + ts.lead, ts.audio) for ts in timed]
        wav.write_bytes(
            mix_soundtrack(voices, payload["total"], sfx_for(payload["scenes"]), music_file)
        )
        render_frames(build_page(payload), payload["total"], out_path, wav)
    return out_path
