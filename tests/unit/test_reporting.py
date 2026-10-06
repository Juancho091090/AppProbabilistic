import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.data.schemas import FootballMatch, TennisMatch
from sports_analytics.models.football.predictor import FootballPredictor
from sports_analytics.models.tennis.predictor import TennisPredictor
from sports_analytics.reporting.builder import (
    UNAVAILABLE,
    markdown_to_telegram_html,
    pct,
    render_markdown,
)
from sports_analytics.reporting.claude_narrator import (
    ClaudeNarrator,
    compact_payload,
    validate_narrative,
)
from sports_analytics.reporting.email import EmailService
from sports_analytics.reporting.payload import CompetitionSection, DailyReportPayload
from sports_analytics.reporting.telegram import TelegramService, split_message
from tests.synthetic import football_league, tennis_tour


@pytest.fixture(scope="module")
def payload(app_config):
    m = app_config.models
    matches, _ = football_league(n_teams=10, rounds=3)
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    fp = FootballPredictor(m.football, m.recency, m.confidence).fit(matches, as_of)
    fx = FootballMatch(
        match_id="f1",
        competition_key="eng_premier_league",
        home_team="Team01",
        away_team="Team02",
        home_name="Arsenal",
        away_name="Chelsea",
        kickoff_utc=as_of + timedelta(hours=10),
    )
    ff = fp.predict(fx, "Premier League")
    tm, _ = tennis_tour(n_matches=1200)
    t_as_of = tm[-1].kickoff_utc + timedelta(hours=1)
    tp = TennisPredictor(m.tennis, m.recency, m.confidence).fit(tm, t_as_of)
    tf = tp.predict(
        TennisMatch(
            match_id="t1",
            tour="ATP",
            tournament="Shanghai",
            category="1000",
            surface="hard",
            player_a="P01",
            player_b="P02",
            player_a_name="Jugador Uno",
            player_b_name="Jugador Dos",
            kickoff_utc=t_as_of + timedelta(hours=5),
        )
    )
    return DailyReportPayload(
        report_date=date(2026, 10, 6),
        generated_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        timezone="America/Bogota",
        football=[
            CompetitionSection(name="Premier League", key="eng_premier_league", forecasts=[ff]),
            CompetitionSection(
                name="Liga 1",
                key="per_liga_1",
                unavailable_reason="La API no respondió para esta competición.",
            ),
        ],
        tennis={"ATP": [tf]},
        data_issues=["per_liga_1: HTTP 500"],
    )


# ------------------------------------------------------------------ builder


def test_markdown_contains_exact_python_probabilities(payload):
    md = render_markdown(payload)
    f = payload.football[0].forecasts[0]
    for p in f.markets["1x2"].values():
        assert pct(p) in md
    t = payload.tennis["ATP"][0]
    assert pct(t.markets["winner"]["A"]) in md
    for header in (
        "# INFORME DEPORTIVO",
        "## RESUMEN",
        "# FÚTBOL",
        "## Premier League",
        "### Arsenal vs Chelsea",
        "# TENIS",
        "## ATP",
        "**Confianza:**",
    ):
        assert header in md
    assert UNAVAILABLE in md  # competición caída marcada, el informe sigue
    assert "Partidos de fútbol: 1" in md and "Partidos ATP: 1" in md


def test_markdown_has_no_betting_language(payload):
    md = render_markdown(payload).lower()
    for term in ("apuesta", "pick", "stake", "cuota"):
        assert term not in md


def test_telegram_html_conversion(payload):
    out = markdown_to_telegram_html(render_markdown(payload))
    assert "<b>INFORME DEPORTIVO</b>" in out and "**" not in out and "• Local:" in out


def test_split_message_respects_limit_and_preserves_content(payload):
    text = markdown_to_telegram_html(render_markdown(payload)) * 5
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) > 1
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


# ----------------------------------------------------------------- Claude


def _valid_raw(compact):
    m = compact["matches"][0]
    return {
        "summary": "Jornada con un partido de fútbol y uno de tenis.",
        "matches": {
            m["event_id"]: [
                f"El modelo asigna {m['pct']['home']}% al local.",
                "Los modelos coinciden en la dirección del resultado.",
            ]
        },
    }


def test_narrative_valid_passes(payload):
    compact = compact_payload(payload)
    narrative, problems = validate_narrative(_valid_raw(compact), compact)
    assert problems == [] and narrative.matches


@pytest.mark.parametrize(
    "text",
    [
        "El local gana con 71.3% de probabilidad.",  # porcentaje inventado
        "Es una apuesta segura para el local.",  # vocabulario prohibido
        "Se esperan 3.87 goles en total.",
    ],  # decimal inventado
)
def test_narrative_rejects_invented_numbers_and_betting(payload, text):
    compact = compact_payload(payload)
    raw = _valid_raw(compact)
    raw["matches"][compact["matches"][0]["event_id"]].append(text)
    narrative, problems = validate_narrative(raw, compact)
    assert narrative is None and problems


class _FakeClaude:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.reply))])


def test_narrator_end_to_end_and_failure(payload):
    settings = Settings(anthropic_api_key="sk-ant-test-123456789")
    compact = compact_payload(payload)
    fake = _FakeClaude(_valid_raw(compact))
    narrative = ClaudeNarrator(settings, client=fake).narrate(payload)
    assert narrative is not None
    sent = json.loads(fake.calls[0]["messages"][0]["content"])
    assert (
        sent["matches"][0]["pct"] == compact["matches"][0]["pct"]
    )  # Claude recibe datos de Python
    assert ClaudeNarrator(settings, client=_FakeClaude(exc=TimeoutError())).narrate(payload) is None
    assert ClaudeNarrator(Settings()).narrate(payload) is None  # sin clave: sin narrativa


def test_narrative_does_not_change_numbers(payload):
    compact = compact_payload(payload)
    narrative, _ = validate_narrative(_valid_raw(compact), compact)
    md_with = render_markdown(payload, narrative.matches, narrative.summary)
    f = payload.football[0].forecasts[0]
    for p in f.markets["1x2"].values():
        assert pct(p) in md_with


# --------------------------------------------------------------- Telegram


def _tg_settings():
    return Settings(
        telegram_bot_token="123456789:ABCdefGhIJKlmNoPQRstuVWxyz0123456789", telegram_chat_id="42"
    )


def test_telegram_send_and_split(payload):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    svc = TelegramService(_tg_settings(), transport=httpx.MockTransport(handler))
    n = svc.send_daily_report(markdown_to_telegram_html(render_markdown(payload)) * 4)
    assert n == len(sent) > 1
    assert all(s["chat_id"] == "42" and s["parse_mode"] == "HTML" for s in sent)


def test_telegram_html_fallback_and_error_hides_token():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(400 if "parse_mode" in body else 200, json={})

    svc = TelegramService(_tg_settings(), transport=httpx.MockTransport(handler))
    assert svc.send_message("<b>x") == 1 and "parse_mode" not in calls[-1]

    svc_err = TelegramService(
        _tg_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(401))
    )
    with pytest.raises(RuntimeError) as exc:
        svc_err.send_error_notification("boom")
    assert "ABCdefGhIJ" not in str(exc.value)


def test_telegram_requires_config():
    with pytest.raises(ValueError):
        TelegramService(Settings())


# ------------------------------------------------------------------ Email


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.sent, self.tls, self.login_args = host, port, [], False, None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context):
        self.tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


def test_email_daily_and_error(payload):
    settings = Settings(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_username="u",
        smtp_password="pw-secret",
        email_from="a@x.com",
        email_to="b@y.com,c@z.com",
    )
    svc = EmailService(settings, smtp_factory=_FakeSMTP)
    md = render_markdown(payload)
    svc.send_daily_report("2026-10-06", md.split("---")[0], md)
    smtp = _FakeSMTP.instances[-1]
    msg = smtp.sent[0]
    assert smtp.tls and smtp.login_args == ("u", "pw-secret")
    assert msg["To"] == "b@y.com, c@z.com" and "2026-10-06" in msg["Subject"]
    assert (
        msg.get_body(("html",)) is not None
        and "DETALLE COMPLETO" in msg.get_body(("plain",)).get_content()
    )
    svc.send_critical_error("fallo")
    assert "Error crítico" in _FakeSMTP.instances[-1].sent[0]["Subject"]
