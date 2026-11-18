"""Imagen del canal de Telegram con los partidos de confianza Alta."""

from datetime import date
from zoneinfo import ZoneInfo

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.pipeline import daily
from sports_analytics.reporting.channel import RULE_MARKET, RULE_MODEL, ChannelPick
from sports_analytics.reporting.telegram import TelegramService
from sports_analytics.video import channel_card as C
from sports_analytics.video import html_engine as E
from sports_analytics.video.sample import _fc, sample_payload, sample_recap, sample_tennis

DAY = date(2026, 11, 19)


def _picks(n_model=3, n_market=0):
    out = [
        ChannelPick(
            _fc(
                f"a{i}",
                "Premier League",
                f"Local{i}",
                f"Visita{i}",
                (0.7, 0.2, 0.1),
                "high",
                (1900, 1600),
                (2.2, 0.8),
                15 + i,
            ),
            RULE_MODEL,
        )
        for i in range(n_model)
    ]
    out += [
        ChannelPick(
            _fc(
                f"m{i}",
                "LaLiga",
                f"Fav{i}",
                f"Rival{i}",
                (0.55, 0.25, 0.2),
                "medium",
                (1850, 1650),
                (1.8, 0.9),
                18,
            ),
            RULE_MARKET,
            (0.66, 0.2, 0.14),
        )
        for i in range(n_market)
    ]
    return out


def test_items_use_market_probability_for_market_rule():
    items = C.card_items(_picks(1, 1), ZoneInfo("America/Bogota"))
    model, market = items
    assert not model["market"] and model["p"] == 70 and model["fav"] == "Local0"
    assert market["market"] and market["p"] == 66 and market["fav"] == "Fav0"
    assert model["meta"].startswith("Fútbol, Premier League")


def test_tennis_row_names_the_winner():
    item = C.card_items([ChannelPick(sample_tennis(), RULE_MODEL)], ZoneInfo("America/Bogota"))[0]
    assert item["meta"].startswith("Tenis")
    assert item["fav"] in item["match"]


def test_html_sections_density_and_recap():
    tz = ZoneInfo("America/Bogota")
    page = C.card_html(C.card_items(_picks(5, 2), tz), DAY, "cancha", sample_recap())
    assert "Confianza Alta de mañana" in page and "Favoritos claros del mercado" in page
    assert 'class="card dense"' in page and "Ayer:" in page and "+18" in page
    assert "t.me/JCProbabilidades" in page
    few = C.card_html(C.card_items(_picks(2), tz), DAY, "cuaderno")
    assert 'class="card few"' in few and "Favoritos claros del mercado" not in few
    assert "Ayer:" not in few


def test_title_when_only_market_favorites():
    page = C.card_html(C.card_items(_picks(0, 2), ZoneInfo("UTC")), DAY, "pizarra")
    assert "Favoritos claros de mañana" in page


def test_no_picks_no_card(tmp_path):
    assert C.render_card([], DAY, tmp_path / "x.png") is None


def _channel(sent):
    settings = Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789",
        telegram_channel_id="@JCProbabilidades",
    )

    def handler(request):
        sent.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True})

    return TelegramService(
        settings, transport=httpx.MockTransport(handler), chat_id=settings.telegram_channel_id
    )


def test_pipeline_sends_photo(monkeypatch, tmp_path):
    sent, issues = [], []

    def fake_render(picks, day, out, tz, recap=None):
        out.write_bytes(b"\x89PNG fake")
        return out

    monkeypatch.setattr(C, "render_card", fake_render)
    ok = daily._channel_card(_channel(sent), sample_payload()[0], _picks(3), None, issues)
    assert ok and sent == ["sendPhoto"] and not issues, issues


def test_pipeline_card_failure_is_reported_not_raised(monkeypatch):
    sent, issues = [], []

    def boom(*a, **k):
        raise RuntimeError("sin Chrome")

    monkeypatch.setattr(C, "render_card", boom)
    assert (
        daily._channel_card(_channel(sent), sample_payload()[0], _picks(3), None, issues) is False
    )
    assert sent == [] and "Canal (imagen)" in issues[0]


def _has_chrome():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return E.chrome_path() is not None


@pytest.mark.skipif(not _has_chrome(), reason="sin Chrome/Playwright")
@pytest.mark.parametrize("style", E.STYLES)
def test_real_card_fits(style, tmp_path):
    from PIL import Image

    out = C.render_card(_picks(6, 2), DAY, tmp_path / "c.png", style=style, recap=sample_recap())
    assert Image.open(out).size == (1080, 1350)
