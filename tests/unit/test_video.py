"""Short diario: guion, voz (Google TTS simulado), audio, render y envío."""

import base64
import io
import json
import shutil
import subprocess
import wave
from types import SimpleNamespace

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.reporting.telegram import TelegramService
from sports_analytics.video.audio import RATE, generate_music, mix_soundtrack, sfx
from sports_analytics.video.render import _captions, render_short, sfx_events, timeline, word_times
from sports_analytics.video.sample import sample_payload
from sports_analytics.video.script import build_short_script, pick_open_match
from sports_analytics.video.tts import GoogleTTS, concat_wavs, wav_duration


def _wav(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def test_picks_most_open_non_high_match():
    payload, fair, n = sample_payload()
    f = pick_open_match(payload)
    assert f.event_id == "ej1" and f.confidence != "high"


def test_script_numbers_come_from_forecast():
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n)
    assert [x.kind for x in s.scenes] == ["hook", "bars", "why", "cta"]
    bars = s.scenes[1].narration
    assert "36 por ciento" in bars and "29 al empate" in bars and "35 a Barcelona" in bars
    assert "1,3" in s.scenes[2].narration  # decimales con coma para la voz
    assert "Mañana hay 4" in s.scenes[3].narration
    assert "t.me/JCProbabilidades" in s.description and len(s.title) <= 100
    text = " ".join(x.narration for x in s.scenes).lower() + s.description.lower()
    for term in ("apuesta", "pick", "stake", "cuota"):
        assert term not in text


def test_no_script_without_open_matches():
    payload, fair, n = sample_payload()
    for sec in payload.football:
        for f in sec.forecasts:
            f.confidence = "high"
    assert build_short_script(payload, fair, n) is None


def test_captions_split_by_sentence_and_length():
    payload, fair, n = sample_payload()
    chunks = _captions(build_short_script(payload, fair, n).scenes[1])
    assert all(len(c.split()) <= 6 for c in chunks)
    assert all(not c[:-1].count(".") for c in chunks)  # no mezcla dos frases


def test_google_tts_request_and_hidden_key():
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["voice"] == {"languageCode": "es-US", "name": "es-US-Chirp3-HD-Puck"}
        return httpx.Response(200, json={"audioContent": base64.b64encode(_wav(1.5)).decode()})

    tts = GoogleTTS("clave-secreta-123", transport=httpx.MockTransport(handler))
    audio = tts.synthesize("Hola")
    assert abs(wav_duration(audio) - 1.5) < 0.01 and tts.characters == 4
    err = GoogleTTS(
        "clave-secreta-123", transport=httpx.MockTransport(lambda r: httpx.Response(403))
    )
    with pytest.raises(RuntimeError) as exc:
        err.synthesize("x")
    assert "clave-secreta" not in str(exc.value)


def test_concat_wavs_respects_scene_durations():
    out = concat_wavs([(_wav(1.0), 1.4), (None, 0.5), (_wav(2.0), 1.0)])
    assert abs(wav_duration(out) - 2.9) < 0.01


def test_timeline_uses_voice_durations():
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n)
    fake = SimpleNamespace(synthesize=lambda text: _wav(2.0))
    tl = timeline(s, fake)
    assert [round(t.duration, 2) for t in tl] == [2.35, 2.56, 2.56, 2.56]
    assert tl[1].start == pytest.approx(2.35) and tl[1].lead == pytest.approx(0.21)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_render_short_with_voice(tmp_path):
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n)
    fake = SimpleNamespace(synthesize=lambda text: _wav(1.0))
    path = render_short(s, tmp_path / "s.mp4", tts=fake)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(x for x in streams if x["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(x["codec_type"] == "audio" for x in streams)


def test_all_font_weights_are_bundled():
    """Pillow busca en las fuentes del sistema si falta el archivo: en CI no las hay."""
    import re
    from pathlib import Path

    from sports_analytics.video import render

    src = Path(render.__file__).read_text(encoding="utf-8")
    weights = set(re.findall(r'font\("(\w+)"', src)) | set(
        re.findall(r'_fit\([^)]*?"(\w+)", \d+', src)
    )
    assert weights
    for w in weights:
        assert (render.ASSETS / f"Inter-{w}.otf").is_file(), w


def _arr(wav_bytes: bytes):
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert w.getframerate() == RATE
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768


def test_music_is_generated_and_normalized():
    m = generate_music(5.0)
    assert len(m) == 5 * RATE and 0.99 <= abs(m).max() <= 1.0
    for kind in ("whoosh", "pop", "hit"):
        assert abs(sfx(kind)).max() > 0.1


def test_mix_ducks_music_under_voice_and_never_clips():
    import numpy as np

    loud = io.BytesIO()
    with wave.open(loud, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        tt = np.arange(24000 * 2) / 24000
        w.writeframes((np.sin(2 * np.pi * 220 * tt) * 0.5 * 32767).astype(np.int16).tobytes())
    x = _arr(mix_soundtrack([(3.0, loud.getvalue())], 8.0, [(1.0, "hit")]))
    assert len(x) == 8 * RATE and abs(x).max() <= 0.98
    # sin voz: solo música (más baja que la voz)
    assert np.sqrt(np.mean(x[int(6.5 * RATE) : int(6.9 * RATE)] ** 2)) < 0.2


def test_sfx_events_follow_scenes():
    payload, fair, n = sample_payload()
    tl = timeline(build_short_script(payload, fair, n))
    ev = sfx_events(tl)
    whooshes = [t for t, k in ev if k == "whoosh"]
    assert whooshes == [ts.start for ts in tl[1:]]
    assert sum(1 for _, k in ev if k == "pop") == 1 + len(tl[2].scene.data["duels"])


def test_word_times_cover_speech_after_lead():
    payload, fair, n = sample_payload()
    scene = build_short_script(payload, fair, n).scenes[1]
    wt = word_times(scene, 5.0, lead=0.2)
    assert wt[0][0] == pytest.approx(0.2) and wt[-1][1] == pytest.approx(5.0 - 0.35)


def test_recap_scene_is_honest_on_good_and_bad_days():
    from sports_analytics.video.recap import Recap, RecapItem
    from sports_analytics.video.sample import sample_recap

    payload, fair, n = sample_payload()
    good = build_short_script(payload, fair, n, sample_recap())
    assert [x.kind for x in good.scenes][:2] == ["recap", "hook"]
    assert (
        "Acertamos 5 de 6" in good.scenes[0].narration
        and "Y para mañana" in good.scenes[1].narration
    )
    assert "Ayer, confianza Alta: 5 de 6" in good.description
    bad = Recap(
        good.scenes[0].data and sample_recap().day,
        "modelo",
        [
            RecapItem("Inter 0-1 Lecce", "Inter", False),
            RecapItem("Roma 1-1 Como", "Roma", False),
            RecapItem("Benfica 2-0 Gil Vicente", "Benfica", True),
        ],
    )
    s = build_short_script(payload, fair, n, bad).scenes[0]
    assert s.data["mood"] == "bad" and "acertamos 1 de 3" in s.narration
    assert "publicamos todo" in s.narration and "Telegram" not in s.narration
    # sin resumen, el Short empieza con el partido abierto
    assert build_short_script(payload, fair, n).scenes[0].kind == "hook"
    ev = sfx_events(timeline(good))
    assert ev[0] == (0.0, "hit") and sum(1 for _, k in ev if k == "pop") == 1 + 5 + 3


def test_sound_effects_stay_well_below_the_voice():
    """Queja del 17-nov: el efecto de transición sonaba tan fuerte como la voz."""
    import numpy as np

    from sports_analytics.video.audio import SFX_LEVEL

    voice_db = -18.0  # nivel típico de Google TTS
    for kind in ("whoosh", "pop", "hit"):
        x = sfx(kind) * SFX_LEVEL
        rms_db = 20 * np.log10(np.sqrt(np.mean(x**2)))
        assert rms_db <= voice_db - 14, kind
        assert abs(x).max() < 0.15, kind


def test_every_theme_renders_and_switches_colors():
    from zoneinfo import ZoneInfo

    from sports_analytics.video import render as R

    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n, sample_recap_fn(), intro=True)
    first = {}
    try:
        for name in R.THEMES:
            R.set_theme(name)
            tl = R.timeline(s)
            img = R.frame(tl[0], 1.5, ZoneInfo("America/Bogota"), None, 30.0, {})
            first[name] = img.convert("RGB").getpixel((10, 1000))
    finally:
        R.set_theme(None)
    assert len(set(first.values())) == len(R.THEMES)  # el fondo cambia con la paleta
    with pytest.raises(ValueError):
        R.set_theme("no_existe")


def test_intro_only_when_requested():
    payload, fair, n = sample_payload()
    with_intro = build_short_script(payload, fair, n, sample_recap_fn(), intro=True)
    assert [x.kind for x in with_intro.scenes][:3] == ["intro", "recap", "hook"]
    assert "JC Probabilidades" in with_intro.scenes[0].narration
    assert build_short_script(payload, fair, n).scenes[0].kind == "hook"


def sample_recap_fn():
    from sports_analytics.video.sample import sample_recap

    return sample_recap()


def test_send_video_retries_on_telegram_5xx(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789", telegram_chat_id="42"
    )
    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    svc = TelegramService(settings, transport=httpx.MockTransport(handler))
    svc.send_video(video, "x", wait=0)
    assert len(calls) == 3
    bad = TelegramService(
        settings, transport=httpx.MockTransport(lambda r: httpx.Response(400, json={}))
    )
    with pytest.raises(RuntimeError):
        bad.send_video(video, "x", wait=0)


def test_pinned_comment_invites_debate_and_points_to_telegram():
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n)
    assert "Comenta L, E o V" in s.pinned_comment and "@JCProbabilidades" in s.pinned_comment
    caption = f"{s.title}\n\n{s.description}\n\n📌 Comentario para fijar:\n{s.pinned_comment}"
    assert len(caption) < 1024  # límite del pie de video en Telegram
