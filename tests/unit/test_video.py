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
from sports_analytics.video.render import _captions, render_short, timeline
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
        assert body["voice"] == {"languageCode": "es-US", "name": "es-US-Chirp3-HD-Charon"}
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
    assert [round(t.duration, 2) for t in tl] == [2.35] * 4
    assert tl[1].start == pytest.approx(2.35)


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

    def fake_render(script, path, tz, tts):
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
