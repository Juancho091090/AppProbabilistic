"""Mensaje del canal público de Telegram (@JCProbabilidades)."""

import json

import httpx

from sports_analytics.config.settings import Settings
from sports_analytics.reporting.channel import (
    CHANNEL_DISCLAIMER,
    NO_MATCHES,
    RULE_MARKET,
    RULE_MODEL,
    favorite_probability,
    render_channel_message,
    select_channel_forecasts,
)
from sports_analytics.reporting.telegram import TelegramService
from tests.unit.test_reporting import payload  # noqa: F401  (fixture compartida)


def _with_confidence(payload, football: str, tennis: str):  # noqa: F811
    p = payload.model_copy(deep=True)
    for s in p.football:
        for f in s.forecasts:
            f.confidence = football
    for items in p.tennis.values():
        for f in items:
            f.confidence = tennis
    return p


def test_selects_only_high_confidence(payload):  # noqa: F811
    p = _with_confidence(payload, "high", "medium")
    selected = select_channel_forecasts(p)
    assert [(x.forecast.sport, x.rule) for x in selected] == [("football", RULE_MODEL)]


def test_selection_is_capped_and_sorted_by_favorite(payload):  # noqa: F811
    p = _with_confidence(payload, "high", "high")
    assert len(select_channel_forecasts(p, max_items=1)) == 1
    both = select_channel_forecasts(p)
    probs = [favorite_probability(x.forecast) for x in both]
    assert probs == sorted(probs, reverse=True)


def test_message_contains_model_numbers_and_disclaimer(payload):  # noqa: F811
    p = _with_confidence(payload, "high", "high")
    msg = render_channel_message(p)
    f = p.football[0].forecasts[0]
    assert f"{f.markets['1x2']['home'] * 100:.0f}%" in msg
    assert "Arsenal vs Chelsea" in msg and "Jugador Uno" in msg
    assert CHANNEL_DISCLAIMER in msg and "Martes 6 oct" in msg
    for term in ("apuesta", "pick", "stake", "cuota"):
        assert term not in msg.lower()
    assert len(msg) < 4000


def test_message_without_high_confidence(payload):  # noqa: F811
    msg = render_channel_message(_with_confidence(payload, "low", "medium"))
    assert NO_MATCHES in msg and "⚽" not in msg


def test_team_names_are_html_escaped(payload):  # noqa: F811
    p = _with_confidence(payload, "high", "low")
    p.football[0].forecasts[0].home_or_a = "A<b>&C"
    assert "A&lt;b&gt;&amp;C" in render_channel_message(p)


def test_channel_uses_same_bot_with_its_own_chat_id():
    sent = []
    settings = Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789",
        telegram_chat_id="42",
        telegram_channel_id="@JCProbabilidades",
    )
    assert settings.telegram_channel_enabled

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    svc = TelegramService(
        settings, transport=httpx.MockTransport(handler), chat_id=settings.telegram_channel_id
    )
    svc.send_message("<b>hola</b>")
    assert sent[0]["chat_id"] == "@JCProbabilidades"


def test_market_favorites_fill_when_few_high(payload):  # noqa: F811
    p = _with_confidence(payload, "medium", "low")
    fid = p.football[0].forecasts[0].event_id
    picks = select_channel_forecasts(p, {fid: (0.66, 0.20, 0.14)})
    assert [(x.forecast.event_id, x.rule) for x in picks] == [(fid, RULE_MARKET)]
    msg = render_channel_message(p, picks)
    assert "Favoritos claros del mercado" in msg and "Mercado 66%" in msg
    assert "Favorito: <b>Arsenal</b>" in msg


def test_market_below_threshold_or_enough_high_is_not_added(payload):  # noqa: F811
    p = _with_confidence(payload, "medium", "low")
    fid = p.football[0].forecasts[0].event_id
    assert select_channel_forecasts(p, {fid: (0.55, 0.25, 0.20)}) == []
    # con suficientes partidos Alta no se completa con el mercado
    p2 = _with_confidence(payload, "high", "high")
    picks = select_channel_forecasts(p2, {fid: (0.80, 0.12, 0.08)}, min_items=2)
    assert all(x.rule == RULE_MODEL for x in picks)
