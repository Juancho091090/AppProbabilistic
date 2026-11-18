"""Motor de plantillas HTML de los Shorts: rotación, guion de tenis, datos de escena,
envío de uno o dos videos y un render real (corto) con Chrome."""

import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.pipeline import daily
from sports_analytics.reporting.channel import ChannelPick
from sports_analytics.reporting.telegram import TelegramService
from sports_analytics.video import html_engine as E
from sports_analytics.video.render import timeline
from sports_analytics.video.sample import sample_payload, sample_recap, sample_tennis
from sports_analytics.video.script import (
    Scene,
    ShortScript,
    _clean_comp,
    build_short_script,
    build_tennis_script,
    pick_tennis_match,
)

TZ = ZoneInfo("America/Bogota")


def test_styles_rotate_daily_and_differ_between_sports():
    d0 = date(2026, 11, 18)
    week = [E.style_for(d0 + timedelta(days=i), "football") for i in range(4)]
    assert sorted(week) == sorted(E.STYLES)  # las 4 en 4 días seguidos
    for i in range(8):
        d = d0 + timedelta(days=i)
        assert E.style_for(d, "football") != E.style_for(d, "tennis")


def test_tennis_script_uses_model_outputs():
    payload, fair, n = sample_payload()
    s = build_tennis_script(payload, 2)
    assert s.sport == "tennis" and s.surface == "hard"
    assert [x.kind for x in s.scenes] == ["hook", "bars", "sets", "cta"]
    hook, bars, sets = s.scenes[0].data, s.scenes[1].data, s.scenes[2].data
    assert hook["kicker"] == "Mañana en ATP Finals" and "pista dura" in hook["when"]
    assert hook["note_big"] == "68 / 32"
    assert bars["rows"] == [["Sinner", 0.68], ["Zverev", 0.32]]
    assert "66% contra 62%" in bars["aside"]
    assert [r[0] for r in sets["rows"]] == ["Sinner 2-0", "Sinner 2-1", "Zverev 2-1", "Zverev 2-0"]
    assert sets["aside"] == "Games esperados: 22,4"
    assert "Comenta 1 o 2" in s.pinned_comment and "#tenis" in s.title
    text = " ".join(x.narration for x in s.scenes).lower() + s.description.lower()
    for term in ("apuesta", "pick", "stake", "cuota"):
        assert term not in text


def test_tennis_pick_is_the_strongest_match_and_comp_is_clean():
    payload, _, _ = sample_payload()
    weak = sample_tennis().model_copy(
        update={"event_id": "weak", "context": {"elo": {"A": 1500, "B": 1500}}}
    )
    payload.tennis["ATP"].append(weak)
    assert pick_tennis_match(payload).event_id == "ejt1"
    assert _clean_comp("ATP · ATP Finals") == "ATP Finals"
    assert _clean_comp("WTA · Wuhan") == "WTA Wuhan"
    payload.tennis = {}
    assert build_tennis_script(payload) is None


def test_football_scene_data_for_the_page():
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n, sample_recap(), intro=True)
    data = {sc.kind: E.scene_data(sc.kind, sc.data, TZ) for sc in s.scenes}
    assert data["hook"]["note_big"] == "36 / 29 / 35" and "LaLiga" in data["hook"]["when"]
    assert data["bars"]["stamp"] == "Sin favorito claro: 7 puntos"
    assert data["recap"]["hits"] == 5 and data["recap"]["note"].startswith("y 1 más")
    assert data["cta"]["n_high"] == n and len(data["why"]["duels"]) == 3
    json.dumps(data)  # todo debe poder pasarse a la página


def test_plan_and_sound_effects_are_the_same_timeline():
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n, sample_recap())
    p = E.page_payload(s, timeline(s), TZ, "cuaderno", "data:,")
    ev = E.sfx_for(p["scenes"])
    assert ev[0] == (0.0, "hit")
    assert [t for t, k in ev if k == "whoosh"] == [sc["start"] for sc in p["scenes"][1:]]
    bars = next(sc for sc in p["scenes"] if sc["kind"] == "bars")
    assert (bars["start"] + bars["plan"]["stamp"], "hit") in ev
    assert all(w[0] <= w[1] for sc in p["scenes"] for w in sc["words"])


def _settings(**kw):
    return Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789",
        telegram_chat_id="42",
        short_enabled=True,
        **kw,
    )


def _svc(sent):
    def handler(request):
        sent.append(request.content)
        return httpx.Response(200, json={"ok": True})

    return SimpleNamespace(
        telegram=TelegramService(_settings(), transport=httpx.MockTransport(handler))
    )


def _fake_render(calls):
    def fake(settings, payload, script, tts, issues):
        calls.append(script.sport)
        out = Path("/tmp") / f"fake_{script.sport}.mp4"
        out.write_bytes(b"mp4")
        return out, "cuaderno"

    return fake


def test_two_shorts_when_both_sports_have_matches(monkeypatch):
    payload, fair, n = sample_payload()
    calls, sent = [], []
    monkeypatch.setattr(daily, "_render_short", _fake_render(calls))
    picks = [
        ChannelPick(f, "modelo_alta") for f in payload.all_forecasts() if f.confidence == "high"
    ]
    assert daily._daily_shorts(_settings(), payload, fair, picks, _svc(sent), sample_recap()) == 2
    assert calls == ["football", "tennis"]
    assert b"Short de f\xc3\xbatbol" in sent[0] and b"Short de tenis" in sent[1]


def test_only_the_sport_with_matches_is_published(monkeypatch):
    payload, fair, n = sample_payload()
    payload.football = []
    calls, sent = [], []
    monkeypatch.setattr(daily, "_render_short", _fake_render(calls))
    assert daily._daily_shorts(_settings(), payload, fair, [], _svc(sent), sample_recap()) == 1
    assert calls == ["tennis"]
    payload2, fair2, _ = sample_payload()
    payload2.tennis = {}
    calls.clear()
    assert daily._daily_shorts(_settings(), payload2, fair2, [], _svc([]), None) == 1
    assert calls == ["football"]


def test_a_failing_video_does_not_stop_the_other(monkeypatch):
    payload, fair, n = sample_payload()
    sent, issues = [], []

    def flaky(settings, payload, script, tts, issues_):
        if script.sport == "football":
            raise RuntimeError("Chrome no arrancó")
        out = Path("/tmp/fake_t.mp4")
        out.write_bytes(b"mp4")
        return out, "marcador"

    monkeypatch.setattr(daily, "_render_short", flaky)
    assert daily._daily_shorts(_settings(), payload, fair, [], _svc(sent), None, issues) == 1
    assert any("Short football" in i for i in issues)


def test_html_failure_falls_back_to_previous_design_for_football(monkeypatch, tmp_path):
    payload, fair, n = sample_payload()
    s = build_short_script(payload, fair, n)

    def boom(*a, **k):
        raise RuntimeError("sin Chrome")

    used = {}

    def pillow(script, out, tz, tts, music, theme):
        used["theme"] = theme
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr("sports_analytics.video.html_engine.render_short_html", boom)
    monkeypatch.setattr("sports_analytics.video.render.render_short", pillow)
    issues = []
    out, style = daily._render_short(_settings(), payload, s, None, issues)
    assert style == "clásica" and used["theme"] == "claro_cesped"
    assert "diseño anterior" in issues[0]


def _can_render():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None and (E.chrome_path() is not None)


@pytest.mark.skipif(not _can_render(), reason="sin Chrome/Playwright/ffmpeg")
@pytest.mark.parametrize("style", E.STYLES)
def test_real_render_with_chrome(style, tmp_path):
    """Video corto real: dos escenas por plantilla (fútbol y tenis según el estilo)."""
    payload, fair, n = sample_payload()
    t = build_tennis_script(payload, 2)
    sport_script = t if style in ("marcador", "cancha") else build_short_script(payload, fair, n)
    keep = [x for x in sport_script.scenes if x.kind in ("hook", "bars")]
    mini = ShortScript(
        sport_script.event_id,
        "t",
        "d",
        [Scene(x.kind, "Prueba corta.", x.data) for x in keep],
        sport=sport_script.sport,
        surface=sport_script.surface,
    )
    out = E.render_short_html(mini, tmp_path / "s.mp4", style=style)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(x for x in streams if x["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(x["codec_type"] == "audio" for x in streams)
