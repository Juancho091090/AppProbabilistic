"""Render del Short vertical (1080×1920, 30 fps) con Pillow + ffmpeg.

Cada escena dura lo que dura su narración (o una estimación si no hay voz). Los
subtítulos se muestran por frases cortas, repartidas según su longitud.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from sports_analytics.video.script import Scene, ShortScript
from sports_analytics.video.tts import concat_wavs, wav_duration

W, H, FPS = 1080, 1920, 30
ASSETS = Path(__file__).parent / "assets"
NAVY, NAVY_2, CARD = (11, 31, 58), (5, 18, 38), (22, 52, 92)
LIME, WHITE, GREY = (198, 244, 50), (255, 255, 255), (170, 184, 204)
CHARS_PER_SECOND = 15.0  # ritmo estimado sin voz
PAD = 0.35  # silencio al final de cada escena
DISCLAIMER = "Probabilidades de un modelo estadístico, no recomendaciones. +18"


@lru_cache(maxsize=32)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / f"Inter-{weight}.otf"), size)


def ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


@dataclass
class TimedScene:
    scene: Scene
    start: float
    duration: float
    audio: bytes | None = None


def _wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if draw.textlength(cand, font=f) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    return [*lines, cur] if cur else lines


def _fit(draw, text: str, weight: str, size: int, width: int) -> ImageFont.FreeTypeFont:
    while size > 30 and draw.textlength(text, font=font(weight, size)) > width:
        size -= 4
    return font(weight, size)


def _center(draw, y: float, text: str, f, fill, alpha: float = 1.0) -> None:
    w = draw.textlength(text, font=f)
    color = (*fill, int(255 * alpha))
    draw.text(((W - w) / 2, y), text, font=f, fill=color)


@lru_cache(maxsize=1)
def _base() -> Image.Image:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(H):  # degradado vertical
        k = y / H
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(NAVY[i] * (1 - k) + NAVY_2[i] * k) for i in range(3)),
        )
    logo = Image.open(ASSETS / "logo.png").convert("RGB").resize((190, 190), Image.LANCZOS)
    mask = Image.new("L", (190, 190), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 189, 189), fill=255)
    img.paste(logo, ((W - 190) // 2, 90), mask)
    d.text((W / 2, 1855), "@JCProbabilidades", font=font("Medium", 36), fill=GREY, anchor="ms")
    return img


def _captions(scene: Scene) -> list[str]:
    """Frases de 6 palabras como máximo, para los subtítulos."""
    words, out = scene.narration.split(), []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if w.endswith((".", "?", ":")) or len(cur) >= 6 or (w.endswith(",") and len(cur) >= 3):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _draw_caption(img: Image.Image, scene: Scene, t: float, dur: float) -> None:
    chunks = _captions(scene)
    speak = max(dur - PAD, 0.1)
    total = sum(len(c) for c in chunks) or 1
    acc, current = 0.0, chunks[-1] if chunks else ""
    for c in chunks:
        acc += len(c) / total * speak
        if t < acc:
            current = c
            break
    if not current:
        return
    d = ImageDraw.Draw(img, "RGBA")
    f = font("ExtraBold", 54)
    lines = _wrap(d, current, f, 900)
    h = 70 * len(lines) + 40
    y0 = 1560 - h / 2
    d.rounded_rectangle((60, y0, W - 60, y0 + h), radius=28, fill=(0, 0, 0, 150))
    for i, line in enumerate(lines):
        d.text((W / 2, y0 + 20 + 70 * i), line, font=f, fill=WHITE, anchor="ma")


def _hook(img, s: Scene, t: float, tz: ZoneInfo) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    a1, a2, a3 = ease(t / 0.5), ease((t - 0.3) / 0.5), ease((t - 0.6) / 0.5)
    _center(d, 400 - 30 * (1 - a1), "EL PARTIDO MÁS ABIERTO", font("ExtraBold", 52), LIME, a1)
    _center(d, 462 - 30 * (1 - a1), "DE MAÑANA", font("ExtraBold", 52), LIME, a1)
    fh = _fit(d, s.data["home"], "Black", 110, 940)
    fa = _fit(d, s.data["away"], "Black", 110, 940)
    _center(d, 640 - 40 * (1 - a2), s.data["home"], fh, WHITE, a2)
    _center(d, 790, "vs", font("Bold", 64), LIME, a2)
    _center(d, 900 - 40 * (1 - a3), s.data["away"], fa, WHITE, a3)
    when = s.data["kickoff"].astimezone(tz).strftime("%H:%M")
    _center(d, 1080, f"{s.data['competition']} · {when}", font("Medium", 44), GREY, a3)


def _bars(img, s: Scene, t: float) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    _center(d, 380, "Probabilidades del modelo", font("Bold", 56), WHITE, ease(t / 0.4))
    rows = s.data["rows"]
    top = max(p for _, p in rows)
    for i, (label, p) in enumerate(rows):
        k = ease((t - 0.3 - 0.25 * i) / 1.1)
        y = 540 + i * 260
        color = LIME if p == top else WHITE
        lf = _fit(d, label, "Bold", 52, 640)
        d.text((80, y), label, font=lf, fill=(*WHITE, int(255 * min(1, k * 2))))
        d.rounded_rectangle((80, y + 80, W - 80, y + 150), radius=35, fill=(*CARD, 255))
        width = (W - 160) * p * k
        if width > 70:
            d.rounded_rectangle((80, y + 80, 80 + width, y + 150), radius=35, fill=(*color, 255))
        d.text((W - 80, y), f"{p * 100 * k:.0f}%", font=font("Black", 64), fill=color, anchor="ra")


def _why(img, s: Scene, t: float, dur: float) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    _center(d, 380, "¿Por qué tan parejo?", font("ExtraBold", 64), LIME, ease(t / 0.4))
    cards = s.data["cards"]
    step = max((dur - PAD) / max(len(cards), 1), 0.8)
    for i, (title, value) in enumerate(cards):
        k = ease((t - 0.2 - i * step) / 0.5)
        if k <= 0:
            continue
        y = 540 + i * 270 + 40 * (1 - k)
        d.rounded_rectangle((80, y, W - 80, y + 220), radius=36, fill=(*CARD, int(255 * k)))
        d.text((130, y + 40), title, font=font("Medium", 44), fill=(*GREY, int(255 * k)))
        vf = _fit(d, value, "Black", 76, 820)
        d.text((130, y + 105), value, font=vf, fill=(*WHITE, int(255 * k)))


def _cta(img, s: Scene, t: float) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    a = ease(t / 0.5)
    _center(d, 400, "¿Quieres los partidos", font("ExtraBold", 62), WHITE, a)
    _center(d, 478, "con favorito claro?", font("ExtraBold", 62), WHITE, a)
    n = s.data["n_high"]
    b = ease((t - 0.4) / 0.6)
    if n:
        _center(d, 600, str(n), font("Black", 230), LIME, b)
        _center(d, 860, "con confianza Alta mañana", font("Bold", 50), WHITE, b)
    else:
        _center(d, 700, "Cada noche, gratis", font("Bold", 56), LIME, b)
    c = ease((t - 0.9) / 0.5)
    pulse = 1 + 0.03 * math.sin(t * 5) * c
    pw, ph = 860 * pulse, 150 * pulse
    y = 1010
    d.rounded_rectangle(
        ((W - pw) / 2, y, (W + pw) / 2, y + ph), radius=75, fill=(*LIME, int(255 * c))
    )
    label = f"Telegram · {s.data['handle']}"
    d.text(
        (W / 2, y + ph / 2),
        label,
        font=_fit(d, label, "Black", 54, pw - 110),
        fill=(*NAVY, int(255 * c)),
        anchor="mm",
    )
    _center(d, 1230, DISCLAIMER, font("Medium", 30), GREY, c)


def frame(ts: TimedScene, t: float, tz: ZoneInfo) -> Image.Image:
    img = _base().copy()
    s = ts.scene
    if s.kind == "hook":
        _hook(img, s, t, tz)
    elif s.kind == "bars":
        _bars(img, s, t)
    elif s.kind == "why":
        _why(img, s, t, ts.duration)
    elif s.kind == "cta":
        _cta(img, s, t)
    _draw_caption(img, s, t, ts.duration)
    return img


def timeline(script: ShortScript, tts=None) -> list[TimedScene]:
    out, start = [], 0.0
    for s in script.scenes:
        audio = tts.synthesize(s.narration) if tts else None
        dur = (wav_duration(audio) if audio else len(s.narration) / CHARS_PER_SECOND) + PAD
        out.append(TimedScene(s, start, dur, audio))
        start += dur
    return out


def render_short(script: ShortScript, out_path: Path, tz: str = "America/Bogota", tts=None) -> Path:
    """Genera el MP4. Con ``tts`` (GoogleTTS) lleva voz; sin él, sale sin audio."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg no está instalado")
    zone = ZoneInfo(tz)
    scenes = timeline(script, tts)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24"]
        cmd += ["-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
        if any(ts.audio for ts in scenes):
            wav = Path(tmp) / "voz.wav"
            wav.write_bytes(concat_wavs([(ts.audio, ts.duration) for ts in scenes]))
            cmd += ["-i", str(wav), "-c:a", "aac", "-b:a", "160k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for ts in scenes:
                for i in range(int(round(ts.duration * FPS))):
                    proc.stdin.write(frame(ts, i / FPS, zone).tobytes())
        finally:
            proc.stdin.close()
            if proc.wait() != 0:
                raise RuntimeError("ffmpeg falló al generar el video")
    return out_path
