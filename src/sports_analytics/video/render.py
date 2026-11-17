"""Render del Short vertical (1080×1920, 30 fps) con Pillow + ffmpeg.

Capas por fotograma: fondo animado (degradado + partículas) → contenido de la escena
(con transición de empuje y franja lima entre escenas) → logo, barra de progreso y
subtítulos palabra a palabra. Cada escena dura lo que dura su narración (o una
estimación sin voz). El audio (voz + música + efectos) lo mezcla ``video.audio``.
"""

from __future__ import annotations

import math
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from sports_analytics.video.script import Scene, ShortScript
from sports_analytics.video.tts import wav_duration

W, H, FPS = 1080, 1920, 30
ASSETS = Path(__file__).parent / "assets"
NAVY, NAVY_2, CARD = (11, 31, 58), (4, 14, 30), (22, 52, 92)
LIME, WHITE, GREY, SLATE = (198, 244, 50), (255, 255, 255), (170, 184, 204), (96, 132, 182)
RED = (255, 107, 107)  # fallos en el resumen de ayer
CHARS_PER_SECOND = 15.0  # ritmo estimado sin voz
PAD = 0.35  # silencio al final de cada escena
TRANS = 0.42  # duración de la transición entre escenas
DISCLAIMER = "Probabilidades de un modelo estadístico, no recomendaciones. +18"


# ----------------------------------------------------------------- utilidades


@lru_cache(maxsize=64)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / f"Inter-{weight}.otf"), size)


def clamp01(t: float) -> float:
    return min(max(t, 0.0), 1.0)


def ease(t: float) -> float:
    """Salida cúbica."""
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_back(t: float, s: float = 1.9) -> float:
    """Con rebote (se pasa un poco y vuelve)."""
    t = clamp01(t) - 1
    return 1 + (s + 1) * t**3 + s * t**2


def ease_in_out(t: float) -> float:
    t = clamp01(t)
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


@lru_cache(maxsize=512)
def text_img(text: str, weight: str, size: int, color: tuple) -> Image.Image:
    f = font(weight, size)
    x0, y0, x1, y1 = f.getbbox(text)
    img = Image.new("RGBA", (max(1, x1 - x0 + 4), max(1, y1 - y0 + 4)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2 - x0, 2 - y0), text, font=f, fill=color)
    return img


def fit_size(text: str, weight: str, size: int, width: int) -> int:
    while size > 30 and font(weight, size).getlength(text) > width:
        size -= 4
    return size


@lru_cache(maxsize=16)
def _alpha_lut(alpha: int) -> list[int]:
    return [v * alpha // 255 for v in range(256)]


def compose(canvas: Image.Image, el: Image.Image, x: float, y: float) -> None:
    """``alpha_composite`` recortando lo que queda fuera del lienzo."""
    x, y = int(round(x)), int(round(y))
    w, h = el.size
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(w, canvas.width - x), min(h, canvas.height - y)
    if ex <= sx or ey <= sy:
        return
    canvas.alpha_composite(el, dest=(x + sx, y + sy), source=(sx, sy, ex, ey))


def put(
    canvas: Image.Image,
    el: Image.Image,
    cx: float,
    cy: float,
    scale: float = 1.0,
    alpha: float = 1.0,
    rotate: float = 0.0,
    anchor: str = "c",
) -> None:
    """Pega ``el`` centrado (o alineado a la izquierda/derecha) con escala, opacidad y giro."""
    if alpha <= 0.01 or scale <= 0.01:
        return
    if abs(scale - 1.0) > 1e-3:
        el = el.resize(
            (max(1, int(el.width * scale)), max(1, int(el.height * scale))), Image.BILINEAR
        )
    if rotate:
        el = el.rotate(rotate, resample=Image.BICUBIC, expand=True)
    if alpha < 0.99:
        el = el.copy()
        el.putalpha(el.getchannel("A").point(_alpha_lut(int(alpha * 255))))
    x = {"c": cx - el.width / 2, "l": cx, "r": cx - el.width}[anchor]
    compose(canvas, el, x, cy - el.height / 2)


def text(canvas, s: str, weight: str, size: int, color, cx, cy, **kw) -> None:
    put(canvas, text_img(s, weight, size, (*color[:3], 255)), cx, cy, **kw)


def layer() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


# --------------------------------------------------------------- fondo y marco


@lru_cache(maxsize=1)
def _gradient() -> Image.Image:
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(H):
        k = (y / H) ** 1.2
        rgb = tuple(int(NAVY[i] * (1 - k) + NAVY_2[i] * k) for i in range(3))
        d.line([(0, y), (W, y)], fill=(*rgb, 255))
    glow = Image.new("L", (W, H), 0)  # halo suave detrás del contenido
    ImageDraw.Draw(glow).ellipse((-200, 450, W + 200, 1350), fill=38)
    glow = glow.resize((W // 8, H // 8)).resize((W, H), Image.BILINEAR)
    halo = Image.new("RGBA", (W, H), (*CARD, 0))
    halo.putalpha(glow)
    img.alpha_composite(halo)
    return img


@lru_cache(maxsize=1)
def _particles() -> list[tuple[float, float, float, float, tuple]]:
    rnd = random.Random(7)
    return [
        (
            rnd.uniform(0, W),
            rnd.uniform(0, H),
            rnd.uniform(18, 70),  # velocidad (px/s)
            rnd.uniform(2, 6),  # radio
            (*(LIME if rnd.random() < 0.35 else WHITE), rnd.randint(25, 70)),
        )
        for _ in range(46)
    ]


def background(t: float) -> Image.Image:
    img = _gradient().copy()
    d = ImageDraw.Draw(img, "RGBA")
    for x, y, v, r, color in _particles():
        yy = (y - v * t) % H
        xx = x + 18 * math.sin(t * 0.7 + y)
        d.ellipse((xx - r, yy - r, xx + r, yy + r), fill=color)
    return img


@lru_cache(maxsize=1)
def _logo() -> Image.Image:
    size = 150
    logo = Image.open(ASSETS / "logo.png").convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    logo.putalpha(mask.resize((size, size), Image.LANCZOS))
    return logo


def chrome(img: Image.Image, t_global: float, total: float) -> None:
    """Logo, barra de progreso y usuario (fijos, no se desplazan con la escena)."""
    put(img, _logo(), W / 2, 150)
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, 0, W, 12), fill=(*CARD, 255))
    d.rectangle((0, 0, int(W * clamp01(t_global / total)), 12), fill=(*LIME, 255))
    text(img, "@JCProbabilidades", "Medium", 36, GREY, W / 2, 1850)


# ------------------------------------------------------------------ subtítulos


def _captions(scene: Scene) -> list[str]:
    """Frases de 6 palabras como máximo, sin mezclar dos oraciones."""
    out, cur = [], []
    for w in scene.narration.split():
        cur.append(w)
        if w.endswith((".", "?", ":")) or len(cur) >= 6 or (w.endswith(",") and len(cur) >= 3):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def word_times(scene: Scene, dur: float, lead: float = 0.0) -> list[tuple[float, float, int, str]]:
    """(inicio, fin, índice de frase, palabra), repartiendo el tiempo hablado por letras."""
    chunks = _captions(scene)
    words = [(i, w) for i, c in enumerate(chunks) for w in c.split()]
    speak = max(dur - PAD - lead, 0.1)
    total = sum(len(w) + 1 for _, w in words) or 1
    out, t = [], lead
    for i, w in words:
        step = (len(w) + 1) / total * speak
        out.append((t, t + step, i, w))
        t += step
    return out


def draw_captions(img: Image.Image, scene: Scene, t: float, dur: float, lead: float = 0.0) -> None:
    timed = word_times(scene, dur, lead)
    if not timed:
        return
    current = next((k for k, (_, b, _, _) in enumerate(timed) if t < b), len(timed) - 1)
    chunk = timed[current][2]
    words = [(k, w) for k, (_, _, c, w) in enumerate(timed) if c == chunk]
    gap, maxw = 16, 900
    f = font("ExtraBold", 58)
    lines, cur, cur_w = [], [], 0.0
    for k, w in words:
        ww = f.getlength(w)
        if cur and cur_w + gap + ww > maxw:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append((k, w, ww))
        cur_w += (gap if len(cur) > 1 else 0) + ww
    lines.append(cur)
    lh = 74
    box_h = lh * len(lines) + 36
    y0 = 1585 - box_h / 2
    d = ImageDraw.Draw(img, "RGBA")  # mezcla el recuadro semitransparente con el fondo
    d.rounded_rectangle((70, y0, W - 70, y0 + box_h), radius=30, fill=(0, 0, 0, 155))
    for li, line in enumerate(lines):
        width = sum(ww for _, _, ww in line) + gap * (len(line) - 1)
        x = (W - width) / 2
        y = y0 + 18 + li * lh
        for k, w, ww in line:
            if k == current:
                color = (*LIME, 255)
            elif k < current:
                color = (*WHITE, 255)
            else:
                color = (*WHITE, 120)
            d.text((x, y), w, font=f, fill=color)
            x += ww + gap


# --------------------------------------------------------------------- escenas


@lru_cache(maxsize=1)
def _vs_badge() -> Image.Image:
    size = 180
    img = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, size * 2 - 8, size * 2 - 8), fill=(*LIME, 255))
    img = img.resize((size, size), Image.LANCZOS)
    put(img, text_img("VS", "Black", 72, (*NAVY, 255)), size / 2, size / 2)
    return img


@lru_cache(maxsize=16)
def _chip(label: str) -> Image.Image:
    f = font("Bold", 42)
    w = int(f.getlength(label)) + 80
    img = Image.new("RGBA", (w, 84), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, 83), radius=42, fill=(*CARD, 255))
    put(img, text_img(label, "Bold", 42, (*WHITE, 255)), w / 2, 42)
    return img


def _hook(s: Scene, t: float, dur: float, tz: ZoneInfo, first: bool = True) -> Image.Image:
    img = layer()
    d = ImageDraw.Draw(img, "RGBA")
    if first and t < 0.35:  # destello inicial
        d.rectangle((0, 0, W, H), fill=(*LIME, int(200 * (1 - t / 0.35))))
    k = ease_back(t / 0.45)
    text(img, "EL PARTIDO MÁS ABIERTO", "Black", 56, LIME, W / 2, 395, scale=k)
    text(img, "DE MAÑANA", "Black", 56, LIME, W / 2, 465, scale=k)
    home, away = s.data["home"], s.data["away"]
    sh, sa = fit_size(home, "Black", 122, 960), fit_size(away, "Black", 122, 960)
    a = ease((t - 0.2) / 0.5)
    text(img, home, "Black", sh, WHITE, W / 2 - (1 - a) * W, 680, alpha=a)
    b = ease((t - 0.35) / 0.5)
    text(img, away, "Black", sa, WHITE, W / 2 + (1 - b) * W, 1030, alpha=b)
    v = (t - 0.7) / 0.4
    if v > 0:  # insignia VS con onda expansiva
        burst = clamp01((t - 0.8) / 0.6)
        if 0 < burst < 1:  # en capa aparte: en RGBA, dibujar reemplaza píxeles
            fx = layer()
            r = 90 + 160 * ease(burst)
            ImageDraw.Draw(fx).ellipse(
                (W / 2 - r, 855 - r, W / 2 + r, 855 + r),
                outline=(*LIME, int(200 * (1 - burst))),
                width=6,
            )
            img.alpha_composite(fx)
        wobble = 10 * math.sin(t * 9) * (1 - clamp01(v))
        put(img, _vs_badge(), W / 2, 855, scale=ease_back(v, 2.6), rotate=wobble)
    when = s.data["kickoff"].astimezone(tz).strftime("%H:%M")
    c = ease((t - 1.0) / 0.5)
    put(img, _chip(f"{s.data['competition']} · {when}"), W / 2, 1200 + 40 * (1 - c), alpha=c)
    return img


SEG_COLORS = (LIME, SLATE, WHITE)  # local, empate, visitante


@lru_cache(maxsize=64)
def _donut(probs: tuple[float, float, float], progress: float) -> Image.Image:
    """Dona del 1X2 con antialias (dibujada al doble y reducida)."""
    radius, thick, ss = 300, 86, 2
    size = (radius * 2 + 20) * ss
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    box = (10 * ss, 10 * ss, size - 10 * ss, size - 10 * ss)
    d.arc(box, 0, 360, fill=(*CARD, 255), width=thick * ss)
    start, sweep_end, gap = -90.0, -90.0 + 360 * progress, 1.6
    for p, color in zip(probs, SEG_COLORS, strict=True):
        a0, a1 = start + gap, min(start + 360 * p - gap, sweep_end)
        if a1 > a0:
            d.arc(box, a0, a1, fill=(*color, 255), width=thick * ss)
        start += 360 * p
    return img.resize((size // ss, size // ss), Image.LANCZOS)


@lru_cache(maxsize=16)
def _legend_row(label: str, p: float, color: tuple) -> Image.Image:
    img = Image.new("RGBA", (860, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((0, 0, 859, 79), radius=40, fill=(*CARD, 235))
    d.ellipse((24, 22, 60, 58), fill=(*color, 255))
    size = fit_size(label, "Bold", 44, 560)
    put(img, text_img(label, "Bold", size, (*WHITE, 255)), 84, 40, anchor="l")
    put(img, text_img(f"{p * 100:.0f}%", "Black", 50, (*color, 255)), 830, 40, anchor="r")
    return img


@lru_cache(maxsize=4)
def _stamp(label: str) -> Image.Image:
    f = font("Black", 64)
    w = int(f.getlength(label)) + 90
    img = Image.new("RGBA", (w, 130), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        (4, 4, w - 5, 125), radius=24, fill=(*NAVY_2, 235), outline=(*LIME, 255), width=8
    )
    put(img, text_img(label, "Black", 64, (*LIME, 255)), w / 2, 65)
    return img


def stamp_time(dur: float) -> float:
    return max(2.0, 0.55 * dur)


def _probs(s: Scene, t: float, dur: float) -> Image.Image:
    img = layer()
    text(img, "¿Quién gana mañana?", "ExtraBold", 62, WHITE, W / 2, 360, scale=ease_back(t / 0.45))
    rows = s.data["rows"]
    probs = tuple(round(p, 3) for _, p in rows)
    prog = round(ease_in_out((t - 0.3) / 1.5), 2)
    put(img, _donut(probs, prog), W / 2, 780)
    top = max(probs)
    text(img, f"{top * 100 * prog:.0f}%", "Black", 130, WHITE, W / 2, 760)
    text(img, "máximo", "Medium", 38, GREY, W / 2, 850, alpha=prog)
    acc = 0.0  # cada fila de la leyenda aparece cuando su tramo termina de dibujarse
    for i, ((label, p), color) in enumerate(zip(rows, SEG_COLORS, strict=True)):
        acc += p
        if prog <= 0:
            continue
        k = ease_back((prog - acc + 0.08) / 0.15)
        if k > 0:
            put(img, _legend_row(label, p, color), W / 2, 1170 + i * 92, scale=0.6 + 0.4 * k)
    if top < 0.5:  # sello
        st = (t - stamp_time(dur)) / 0.35
        if st > 0:
            put(
                img,
                _stamp("SIN FAVORITO CLARO"),
                W / 2,
                1000,
                scale=1 + 0.8 * (1 - ease(st)),
                alpha=clamp01(st * 1.6),
                rotate=-7,
            )
    return img


def _fmt(v: float, kind: str) -> str:
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "dec":
        return f"{v:.1f}".replace(".", ",")
    return f"{v:.0f}"


def duel_times(n: int, dur: float) -> list[float]:
    """Momento en que aparece cada duelo (alineado, aprox., con su frase narrada)."""
    step = max((dur - PAD - 0.3) / max(n, 1), 0.9)
    return [0.3 + i * step for i in range(n)]


def _duel_card(duel: dict, g: float) -> Image.Image:
    card = Image.new("RGBA", (920, 270), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, 919, 269), radius=36, fill=(*CARD, 245))
    title = duel["title"]
    put(
        card,
        text_img(title, "Bold", fit_size(title, "Bold", 40, 840), (*GREY, 255)),
        40,
        46,
        anchor="l",
    )
    (ll, lv), (rl, rv) = duel["left"], duel["right"]
    top = max(lv, rv) or 1
    half, cx, by = 380, 460, 150
    lc = LIME if lv >= rv else WHITE
    rc = LIME if rv > lv else WHITE
    d.rounded_rectangle((cx - half, by - 22, cx + half, by + 22), radius=22, fill=(*NAVY_2, 255))
    lw, rw = half * lv / top * g, half * rv / top * g
    if lw > 24:
        d.rounded_rectangle((cx - lw, by - 22, cx - 3, by + 22), radius=22, fill=(*lc, 255))
    if rw > 24:
        d.rounded_rectangle((cx + 3, by - 22, cx + rw, by + 22), radius=22, fill=(*rc, 255))
    for label, anchor, x in ((ll, "l", 40), (rl, "r", 880)):
        size = fit_size(label, "Medium", 36, 380)
        put(card, text_img(label, "Medium", size, (*WHITE, 255)), x, 98, anchor=anchor)
    put(card, text_img(_fmt(lv * g, duel["fmt"]), "Black", 56, (*lc, 255)), 40, 222, anchor="l")
    put(card, text_img(_fmt(rv * g, duel["fmt"]), "Black", 56, (*rc, 255)), 880, 222, anchor="r")
    put(card, text_img("vs", "Bold", 40, (*GREY, 255)), cx, 222)
    return card


def _why(s: Scene, t: float, dur: float) -> Image.Image:
    img = layer()
    text(img, "¿Por qué tan parejo?", "Black", 66, LIME, W / 2, 360, scale=ease_back(t / 0.45))
    duels = s.data.get("duels") or []
    for i, (duel, t0) in enumerate(zip(duels, duel_times(len(duels), dur), strict=True)):
        lt = t - t0
        if lt < 0:
            continue
        card = _duel_card(duel, ease((lt - 0.25) / 0.8))
        put(
            img,
            card,
            W / 2,
            590 + i * 300 + 60 * (1 - ease(lt / 0.45)),
            scale=0.85 + 0.15 * ease_back(lt / 0.45),
            alpha=clamp01(lt / 0.25),
        )
    return img


@lru_cache(maxsize=1)
def _confetti() -> list[tuple[float, float, float, tuple]]:
    """(ángulo, velocidad, tamaño, color): estallido radial desde detrás del número."""
    rnd = random.Random(3)
    return [
        (
            rnd.uniform(0, 2 * math.pi),
            rnd.uniform(650, 1150),
            rnd.uniform(10, 18),
            LIME if rnd.random() < 0.6 else WHITE,
        )
        for _ in range(40)
    ]


@lru_cache(maxsize=2)
def _pill(handle: str) -> Image.Image:
    label = f"Telegram · {handle}"
    size = fit_size(label, "Black", 56, 780)
    img = Image.new("RGBA", (900, 150), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, 899, 149), radius=75, fill=(*LIME, 255))
    put(img, text_img(label, "Black", size, (*NAVY, 255)), 450, 75)
    return img


CTA_LAND = 1.1  # momento en que el número llega a su valor


def _cta(s: Scene, t: float, dur: float) -> Image.Image:
    img = layer()
    d = ImageDraw.Draw(img, "RGBA")
    k = ease_back(t / 0.45)
    text(img, "¿Quieres los partidos", "ExtraBold", 64, WHITE, W / 2, 370, scale=k)
    text(img, "con favorito claro?", "ExtraBold", 64, WHITE, W / 2, 450, scale=k)
    n = s.data["n_high"]
    if n:
        c = ease((t - 0.4) / (CTA_LAND - 0.4))
        bounce = ease_back((t - CTA_LAND) / 0.35, 3.0) if t > CTA_LAND else c
        ct = t - CTA_LAND
        if ct > 0:  # onda y confeti en capa aparte (dibujar en RGBA reemplaza píxeles)
            fx = layer()
            dfx = ImageDraw.Draw(fx)
            b = clamp01(ct / 0.6)
            r = 120 + 220 * ease(b)
            dfx.ellipse(
                (W / 2 - r, 680 - r, W / 2 + r, 680 + r),
                outline=(*LIME, int(180 * (1 - b))),
                width=8,
            )
            if ct < 1.4:
                for ang, speed, size, color in _confetti():
                    dist = 150 + speed * ct * (1 - 0.35 * ct)
                    x = W / 2 + math.cos(ang) * dist
                    y = 680 + math.sin(ang) * dist + 500 * ct * ct
                    a = int(255 * (1 - ct / 1.4))
                    dfx.rectangle((x, y, x + size, y + size * 0.6), fill=(*color, a))
            img.alpha_composite(fx)
        text(img, str(round(n * c)), "Black", 260, LIME, W / 2, 680, scale=0.7 + 0.3 * bounce)
        text(img, "con confianza Alta mañana", "Bold", 52, WHITE, W / 2, 880, alpha=c)
    else:
        text(
            img,
            "Cada noche, gratis",
            "Black",
            70,
            LIME,
            W / 2,
            700,
            scale=ease_back((t - 0.4) / 0.45),
        )
    p = (t - 1.3) / 0.5
    if p > 0:
        ay = 990 + 14 * math.sin(t * 7)
        d.polygon(
            [(W / 2 - 34, ay - 20), (W / 2 + 34, ay - 20), (W / 2, ay + 22)],
            fill=(*LIME, int(255 * clamp01(p))),
        )
        pulse = 1 + 0.035 * math.sin(t * 6) * clamp01(p)
        put(
            img,
            _pill(s.data["handle"]),
            W / 2,
            1110 + 120 * (1 - ease(p)),
            scale=ease_back(p) * pulse,
        )
        text(img, DISCLAIMER, "Medium", 30, GREY, W / 2, 1245, alpha=clamp01(p))
    return img


# ------------------------------------------------------------ resumen de ayer

RECAP_ROWS = 5
RECAP_SCORE_AT = 1.0  # momento en que el marcador llega a su valor


@lru_cache(maxsize=64)
def _ring(rate: float, progress: float) -> Image.Image:
    radius, thick, ss = 205, 40, 2
    size = (radius * 2 + 20) * ss
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = (10 * ss, 10 * ss, size - 10 * ss, size - 10 * ss)
    d.arc(box, 0, 360, fill=(*CARD, 255), width=thick * ss)
    sweep = 360 * rate * progress
    if sweep > 1:
        d.arc(box, -90, -90 + sweep, fill=(*LIME, 255), width=thick * ss)
    return img.resize((size // ss, size // ss), Image.LANCZOS)


@lru_cache(maxsize=32)
def _recap_row(label: str, pick: str, hit: bool) -> Image.Image:
    img = Image.new("RGBA", (920, 84), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 919, 83), radius=42, fill=(*CARD, 240))
    color = LIME if hit else RED
    d.ellipse((20, 14, 76, 70), fill=(*color, 255))
    if hit:
        d.line([(34, 43), (45, 55), (63, 31)], fill=(*NAVY, 255), width=8, joint="curve")
    else:
        d.line([(36, 30), (60, 54)], fill=(*NAVY, 255), width=8)
        d.line([(60, 30), (36, 54)], fill=(*NAVY, 255), width=8)
    size = fit_size(label, "Bold", 40, 540)
    put(img, text_img(label, "Bold", size, (*WHITE, 255)), 98, 42, anchor="l")
    fav = f"Fav.: {pick}"
    put(
        img,
        text_img(fav, "Medium", fit_size(fav, "Medium", 32, 250), (*GREY, 255)),
        895,
        42,
        anchor="r",
    )
    return img


def recap_row_times(n_rows: int) -> list[float]:
    return [0.9 + 0.22 * i for i in range(n_rows)]


def _recap(s: Scene, t: float, dur: float, first: bool = True) -> Image.Image:
    img = layer()
    d = ImageDraw.Draw(img, "RGBA")
    if first and t < 0.35:
        d.rectangle((0, 0, W, H), fill=(*LIME, int(200 * (1 - t / 0.35))))
    hits, n, mood = s.data["hits"], s.data["n"], s.data["mood"]
    title, color = {
        "good": ("¡GRAN DÍA AYER!", LIME),
        "ok": ("ASÍ NOS FUE AYER", WHITE),
        "bad": ("AYER NO FUE NUESTRO DÍA", RED),
    }[mood]
    text(
        img,
        title,
        "Black",
        fit_size(title, "Black", 66, 960),
        color,
        W / 2,
        360,
        scale=ease_back(t / 0.45),
    )
    prog = round(ease((t - 0.25) / (RECAP_SCORE_AT - 0.25)), 2)
    put(img, _ring(round(hits / n, 3), prog), W / 2, 640)
    land = ease_back((t - RECAP_SCORE_AT) / 0.35, 3.0) if t > RECAP_SCORE_AT else prog
    score = f"{round(hits * prog)}/{n}"
    text(img, score, "Black", 150, WHITE, W / 2, 615, scale=0.75 + 0.25 * land)
    text(img, "aciertos", "Medium", 38, GREY, W / 2, 712, alpha=prog)
    items = s.data["items"]
    shown = items[:RECAP_ROWS]
    for i, ((label, pick, hit), t0) in enumerate(
        zip(shown, recap_row_times(len(shown)), strict=True)
    ):
        k = (t - t0) / 0.35
        if k > 0:
            put(
                img,
                _recap_row(label, pick, hit),
                W / 2 + 120 * (1 - ease(k)),
                910 + i * 96,
                alpha=clamp01(k * 2),
            )
    extra = len(items) - len(shown)
    tail_t = (t - (recap_row_times(len(shown))[-1] + 0.3)) / 0.4 if shown else 0
    note = (
        "Publicados en Telegram · confianza Alta"
        if s.data["source"] == "canal"
        else "Confianza Alta del modelo"
    )
    if extra > 0:
        note = f"+{extra} más · " + note
    text(img, note, "Medium", 34, GREY, W / 2, 910 + len(shown) * 96 + 10, alpha=clamp01(tail_t))
    return img


# ------------------------------------------------------------------ línea de tiempo


@dataclass
class TimedScene:
    scene: Scene
    start: float
    duration: float
    audio: bytes | None = None
    lead: float = 0.0  # la voz empieza tras este margen (transición)


def content(ts: TimedScene, t: float, tz: ZoneInfo) -> Image.Image:
    s, dur, first = ts.scene, ts.duration, ts.start == 0
    if s.kind == "recap":
        return _recap(s, t, dur, first)
    if s.kind == "hook":
        return _hook(s, t, dur, tz, first)
    if s.kind == "bars":
        return _probs(s, t, dur)
    if s.kind == "why":
        return _why(s, t, dur)
    if s.kind == "cta":
        return _cta(s, t, dur)
    return layer()


def frame(
    ts: TimedScene,
    t: float,
    tz: ZoneInfo,
    prev: TimedScene | None = None,
    total: float | None = None,
    cache: dict | None = None,
) -> Image.Image:
    """Fotograma completo en el instante ``t`` de la escena ``ts``."""
    total = total or ts.start + ts.duration
    img = background(ts.start + t)
    cur = content(ts, t, tz)
    if prev is not None and t < TRANS:
        e = ease_in_out(t / TRANS)
        cache = cache if cache is not None else {}
        key = (prev.scene.kind, prev.start)
        if key not in cache:
            cache[key] = content(prev, prev.duration, tz)
        compose(img, cache[key], -W * e, 0)
        x = W * (1 - e)
        compose(img, cur, x, 0)
        ImageDraw.Draw(img).rectangle((x - 70, 0, x, H), fill=(*LIME, 255))
    else:
        img.alpha_composite(cur)
    chrome(img, ts.start + t, total)
    draw_captions(img, ts.scene, t, ts.duration, ts.lead)
    return img


def timeline(script: ShortScript, tts=None) -> list[TimedScene]:
    out, start = [], 0.0
    for s in script.scenes:
        audio = tts.synthesize(s.narration) if tts else None
        speech = wav_duration(audio) if audio else len(s.narration) / CHARS_PER_SECOND
        lead = TRANS * 0.5 if out else 0.0  # la voz entra cuando la escena ya casi llegó
        out.append(TimedScene(s, start, lead + speech + PAD, audio, lead))
        start += lead + speech + PAD
    return out


def sfx_events(scenes: list[TimedScene]) -> list[tuple[float, str]]:
    """Efectos sincronizados con la animación (mismos tiempos que el render)."""
    ev = []
    for i, ts in enumerate(scenes):
        s0, kind = ts.start, ts.scene.kind
        if i:
            ev.append((s0, "whoosh"))
        if i == 0:
            ev.append((0.0, "hit"))
        if kind == "hook":
            ev.append((s0 + 0.75, "pop"))
        elif kind == "recap":
            ev.append((s0 + RECAP_SCORE_AT, "hit"))
            n_rows = min(len(ts.scene.data["items"]), RECAP_ROWS)
            ev += [(s0 + t0, "pop") for t0 in recap_row_times(n_rows)]
        elif kind == "bars" and max(p for _, p in ts.scene.data["rows"]) < 0.5:
            ev.append((s0 + stamp_time(ts.duration), "hit"))
        elif kind == "why":
            n = len(ts.scene.data.get("duels") or [])
            ev += [(s0 + t0, "pop") for t0 in duel_times(n, ts.duration)]
        elif kind == "cta" and ts.scene.data.get("n_high"):
            ev.append((s0 + CTA_LAND, "hit"))
    return ev


TAIL = 0.8  # segundos finales (último fotograma) mientras se apaga la música


def render_short(
    script: ShortScript,
    out_path: Path,
    tz: str = "America/Bogota",
    tts=None,
    music_file: str | None = None,
) -> Path:
    """Genera el MP4 con voz (si hay ``tts``), música de fondo y efectos."""
    from sports_analytics.video.audio import mix_soundtrack

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg no está instalado")
    zone = ZoneInfo(tz)
    scenes = timeline(script, tts)
    last = scenes[-1]
    total = last.start + last.duration + TAIL
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        voices = [(ts.start + ts.lead, ts.audio) for ts in scenes]
        wav.write_bytes(mix_soundtrack(voices, total, sfx_events(scenes), music_file))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24"]
        cmd += ["-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(wav)]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        cache: dict = {}
        try:
            for k in range(int(round(total * FPS))):
                tg = k / FPS
                i = max(j for j, ts in enumerate(scenes) if ts.start <= tg + 1e-9)
                ts = scenes[i]
                img = frame(ts, tg - ts.start, zone, scenes[i - 1] if i else None, total, cache)
                proc.stdin.write(img.convert("RGB").tobytes())
        finally:
            proc.stdin.close()
            if proc.wait() != 0:
                raise RuntimeError("ffmpeg falló al generar el video")
    return out_path
