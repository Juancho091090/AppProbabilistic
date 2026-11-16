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
from sports_analytics.pipeline.daily import _daily_short
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


def test_daily_short_sends_video_to_personal_chat(monkeypatch):
    payload, fair, n = sample_payload()
    sent = {}

    def fake_render(script, path, tz, tts, music=None):
        path.write_bytes(b"mp4")
        return path

    monkeypatch.setattr("sports_analytics.video.render.render_short", fake_render)

    def handler(request):
        sent["url"] = str(request.url)
        sent["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789",
        telegram_chat_id="42",
        short_enabled=True,
    )
    svc = SimpleNamespace(
        telegram=TelegramService(settings, transport=httpx.MockTransport(handler))
    )
    assert _daily_short(settings, payload, fair, n, svc) is True
    assert sent["url"].endswith("/sendVideo") and b"Atl\xc3\xa9tico de Madrid" in sent["body"]


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
