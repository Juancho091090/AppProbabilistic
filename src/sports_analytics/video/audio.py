"""Banda sonora del Short: voz + música de fondo + efectos, mezclados con numpy.

La música se genera por código (sin derechos de autor): base electrónica suave a 100 BPM
en La menor (Am–F–C–G) con bombo, hi-hat, bajo, pad y arpegio. Se puede reemplazar por
una pista propia (``SHORT_MUSIC_FILE``, p. ej. de la Biblioteca de audio de YouTube), que
se repite hasta cubrir el video. La música baja automáticamente cuando habla la voz.
"""

from __future__ import annotations

import io
import subprocess
import wave
from collections.abc import Sequence

import numpy as np

RATE = 44100
BPM = 100
MUSIC_LEVEL = 0.20  # volumen de la música sin voz (la voz va a 1.0)
DUCK = 0.55  # cuánto baja la música mientras se habla
SFX_LEVEL = 0.45

# La menor: Am, F, C, G (frecuencias de las notas del acorde, octava 3)
_NOTE = {"A2": 110.0, "F2": 87.31, "C3": 130.81, "G2": 98.0}
CHORDS = [
    ("A2", (220.0, 261.63, 329.63)),
    ("F2", (174.61, 220.0, 261.63)),
    ("C3", (196.0, 261.63, 329.63)),
    ("G2", (196.0, 246.94, 293.66)),
]


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def _place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * RATE)
    if i >= len(buf) or i + len(sig) <= 0:
        return
    j0 = max(0, -i)
    n = min(len(sig) - j0, len(buf) - max(i, 0))
    buf[max(i, 0) : max(i, 0) + n] += gain * sig[j0 : j0 + n]


def _lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    """Filtro de un polo (vectorizado por bloques con lfilter manual)."""
    y = np.empty_like(x)
    acc = 0.0
    for k in range(0, len(x), 4096):  # bucle por bloques: suficiente para ~1 min de audio
        block = x[k : k + 4096]
        out = np.empty_like(block)
        for i, v in enumerate(block):
            acc += alpha * (v - acc)
            out[i] = acc
        y[k : k + 4096] = out
    return y


def kick() -> np.ndarray:
    t = _t(0.32)
    freq = 45 + 85 * np.exp(-t * 28)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return np.sin(phase) * np.exp(-t * 11)


def hat(rng: np.random.Generator) -> np.ndarray:
    t = _t(0.05)
    noise = rng.standard_normal(len(t))
    return np.diff(noise, prepend=0) * np.exp(-t * 90) * 0.25


def pluck(freq: float, length: float = 0.22) -> np.ndarray:
    t = _t(length)
    return (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)) * np.exp(-t * 14)


def pad(freqs: Sequence[float], length: float) -> np.ndarray:
    t = _t(length)
    sig = np.zeros_like(t)
    for f in freqs:
        for detune in (0.997, 1.003):
            for h in range(1, 5):  # sierra suave (pocas armónicas)
                sig += np.sin(2 * np.pi * f * detune * h * t) / (h * 1.6)
    env = np.minimum(1, t / 0.35) * np.minimum(1, (length - t) / 0.35)
    return sig * env / (len(freqs) * 6)


def generate_music(seconds: float, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60 / BPM
    bar = 4 * beat
    out = np.zeros(int(seconds * RATE) + RATE)
    k, h = kick(), hat(rng)
    n_bars = int(seconds / bar) + 1
    for b in range(n_bars):
        root, chord = CHORDS[b % len(CHORDS)]
        t0 = b * bar
        _place(out, pad(chord, bar + 0.3), t0, 0.55)
        for q in range(4):
            _place(out, k, t0 + q * beat, 0.9)
            _place(out, h, t0 + q * beat + beat / 2, 0.6)
        for e in range(8):  # bajo en corcheas
            _place(out, pluck(_NOTE[root], 0.26), t0 + e * beat / 2, 0.55 if e % 2 == 0 else 0.35)
        arp = [chord[0] * 2, chord[1] * 2, chord[2] * 2, chord[1] * 2]
        for s in range(16):  # arpegio en semicorcheas, suave
            _place(out, pluck(arp[s % 4], 0.18), t0 + s * beat / 4, 0.12)
    out = out[: int(seconds * RATE)]
    return out / (np.max(np.abs(out)) or 1)


def load_music(path: str, seconds: float) -> np.ndarray:
    """Pista propia (mp3/wav/…) decodificada con ffmpeg y repetida hasta cubrir el video."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
        capture_output=True,
        check=True,
    ).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768
    if not len(x):
        raise ValueError("La pista de música está vacía")
    reps = int(np.ceil(seconds * RATE / len(x)))
    x = np.tile(x, reps)[: int(seconds * RATE)]
    return x / (np.max(np.abs(x)) or 1)


# Nivel RMS de cada efecto (dBFS) tras aplicarle SFX_LEVEL. La voz de Google ronda −18 dB:
# los efectos quedan 14–20 dB por debajo para acompañar, no para tapar (queja del 17-nov:
# el "whoosh" de la transición sonaba tan fuerte como la voz).
SFX_TARGET_DB = {"whoosh": -38.0, "pop": -34.0, "hit": -33.0}


def _norm(x: np.ndarray, target_db: float) -> np.ndarray:
    rms = np.sqrt(np.mean(x**2)) or 1.0
    return x * (10 ** (target_db / 20) / rms)


def sfx(kind: str) -> np.ndarray:
    """Efecto ya nivelado (se suma tal cual a la mezcla)."""
    rng = np.random.default_rng({"whoosh": 1, "pop": 2, "hit": 3}.get(kind, 0))
    if kind == "whoosh":  # soplo suave y corto, sin agudos
        t = _t(0.32)
        env = np.sin(np.pi * t / 0.32) ** 3
        x = _lowpass(_lowpass(rng.standard_normal(len(t)) * env, 0.05), 0.08)
    elif kind == "pop":  # "tic" suave
        t = _t(0.09)
        freq = 700 + 300 * np.exp(-t * 50)
        x = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t * 45)
    elif kind == "hit":  # golpe grave apagado, sin chasquido
        t = _t(0.45)
        x = np.sin(2 * np.pi * (60 + 30 * np.exp(-t * 12)) * t) * np.exp(-t * 9)
    else:
        return np.zeros(1)
    return _norm(x, SFX_TARGET_DB[kind]) / SFX_LEVEL


def _wav_to_array(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        rate, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768
    if rate != RATE:  # remuestreo lineal (la voz de Google llega a 24 kHz)
        dur = len(x) / rate
        x = np.interp(np.arange(int(dur * RATE)) / RATE, np.arange(len(x)) / rate, x)
    return x


def _envelope(x: np.ndarray, attack: float = 0.03, release: float = 0.35) -> np.ndarray:
    """Envolvente de la voz para bajar la música (bloques de 10 ms)."""
    step = int(0.01 * RATE)
    n = max(1, -(-len(x) // step))
    padded = np.pad(x, (0, n * step - len(x)))
    level = np.sqrt(np.mean(padded.reshape(n, step) ** 2, axis=1) + 1e-12)
    active = (level > 0.02).astype(float)
    env = np.zeros(n)
    a, r = 0.01 / attack, 0.01 / release
    for i in range(n):
        prev = env[i - 1] if i else 0.0
        env[i] = prev + (a if active[i] > prev else r) * (active[i] - prev)
    return np.repeat(np.clip(env, 0, 1), step)[: len(x)]


def mix_soundtrack(
    voices: Sequence[tuple[float, bytes | None]],
    seconds: float,
    events: Sequence[tuple[float, str]] = (),
    music_file: str | None = None,
) -> bytes:
    """WAV 44,1 kHz mono: voz (1.0) + música (con ducking) + efectos, sin saturar."""
    n = int(seconds * RATE)
    voice = np.zeros(n)
    for at, data in voices:
        if data:
            _place(voice, _wav_to_array(data), at)
    music = load_music(music_file, seconds) if music_file else generate_music(seconds)
    music = np.pad(music, (0, max(0, n - len(music))))[:n]
    fade = np.ones(n)
    fi, fo = int(0.4 * RATE), int(1.2 * RATE)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-fo:] = np.linspace(1, 0, fo)
    duck = 1 - DUCK * _envelope(voice)
    effects = np.zeros(n)
    for at, kind in events:
        _place(effects, sfx(kind), at)
    out = voice + MUSIC_LEVEL * music * duck * fade + SFX_LEVEL * effects
    peak = np.max(np.abs(out)) or 1
    if peak > 0.97:
        out = out * 0.97 / peak
    pcm = (out * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
