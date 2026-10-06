from datetime import UTC, date, datetime

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError, BudgetExceeded, HttpApiClient
from sports_analytics.data.clients.api_football import (
    ApiFootballClient,
    parse_fixture,
    parse_statistics,
)
from sports_analytics.data.schemas import MatchStatus

FIXTURE = {
    "fixture": {"id": 1001, "date": "2026-10-06T20:00:00-05:00", "status": {"short": "AET"}},
    "league": {"id": 13, "name": "CONMEBOL Libertadores", "season": 2026},
    "teams": {"home": {"name": "Nacional"}, "away": {"name": "River"}},
    "goals": {"home": 2, "away": 1},
    "score": {"fulltime": {"home": 1, "away": 1}},
}

STATS = [
    {
        "statistics": [
            {"type": "Corner Kicks", "value": 7},
            {"type": "Total Shots", "value": 14},
            {"type": "Ball Possession", "value": "58%"},
        ]
    },
    {
        "statistics": [
            {"type": "Corner Kicks", "value": None},
            {"type": "Total Shots", "value": 9},
            {"type": "Ball Possession", "value": "42%"},
        ]
    },
]


def _settings(tmp_path, **kw):
    return Settings(api_football_key="test-key-123456", cache_dir=str(tmp_path / "cache"), **kw)


def _client(tmp_path, handler, **kw):
    return ApiFootballClient(
        _settings(tmp_path, **kw), transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


# ------------------------------------------------------------------ parsing


def test_parse_fixture_uses_90_minute_score_and_utc():
    m = parse_fixture(FIXTURE, "conmebol_libertadores")
    assert (m.home_goals, m.away_goals) == (1, 1)  # prórroga excluida
    assert m.status == MatchStatus.FINISHED
    assert m.kickoff_utc == datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


def test_parse_fixture_scheduled_has_no_goals():
    item = {
        **FIXTURE,
        "fixture": {**FIXTURE["fixture"], "status": {"short": "NS"}},
        "goals": {"home": None, "away": None},
        "score": {"fulltime": {"home": None, "away": None}},
    }
    m = parse_fixture(item, "x")
    assert m.status == MatchStatus.SCHEDULED and m.home_goals is None


def test_awarded_matches_are_excluded_from_modelling():
    item = {**FIXTURE, "fixture": {**FIXTURE["fixture"], "status": {"short": "AWD"}}}
    assert not parse_fixture(item, "x").is_finished


def test_parse_statistics():
    s = parse_statistics("1001", STATS)
    assert (s.home_corners, s.away_corners) == (7, None)
    assert s.home_possession == 58 and s.away_shots == 9
    assert parse_statistics("1", []) is None


# --------------------------------------------------------------- HTTP base


def test_retry_then_success_and_header_auth(tmp_path):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"errors": [], "response": [FIXTURE]})

    client = _client(tmp_path, handler)
    out = client.fixtures_by_date(date(2026, 10, 6))
    assert len(out) == 1 and len(calls) == 3
    assert calls[0].headers["x-apisports-key"] == "test-key-123456"
    assert calls[0].url.params["timezone"] == "America/Bogota"


def test_rate_limit_respects_retry_after(tmp_path):
    waits = []
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"errors": [], "response": []}),
        ]
    )
    http = HttpApiClient(
        "p",
        "https://x",
        {},
        100,
        tmp_path,
        transport=httpx.MockTransport(lambda r: next(responses)),
        sleep=waits.append,
    )
    http.get("/a")
    assert waits == [7.0]


def test_non_retryable_error_fails_fast(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    with pytest.raises(ApiError):
        _client(tmp_path, handler).fixtures_by_date(date(2026, 10, 6))
    assert len(calls) == 1


def test_network_errors_exhaust_retries(tmp_path):
    def handler(request):
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(ApiError, match="intentos"):
        _client(tmp_path, handler, http_max_retries=2).fixtures_by_date(date(2026, 10, 6))


def test_plan_errors_in_body_raise(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "errors": {"plan": "Free plans do not have access to this season"},
                "response": [],
            },
        )

    with pytest.raises(ApiError, match="Free plans"):
        _client(tmp_path, handler).fixtures_by_league_season(39, 2020)


def test_cache_avoids_second_call(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"errors": [], "response": STATS})

    client = _client(tmp_path, handler)
    client.fixture_statistics("1001")
    client.fixture_statistics("1001")
    assert len(calls) == 1 and client.http.cache_hits == 1


def test_daily_budget_blocks_calls(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"errors": [], "response": []})

    client = _client(tmp_path, handler, api_football_daily_limit=2)
    client.fixtures_by_date(date(2026, 10, 1))
    client.fixtures_by_date(date(2026, 10, 2))
    with pytest.raises(BudgetExceeded):
        client.fixtures_by_date(date(2026, 10, 3))


def test_api_key_never_logged(tmp_path, caplog):
    def handler(request):
        return httpx.Response(200, json={"errors": [], "response": []})

    from sports_analytics.core.logging import JsonFormatter

    with caplog.at_level("INFO"):
        _client(tmp_path, handler).fixtures_by_date(date(2026, 10, 6))
    formatted = "".join(JsonFormatter().format(r) for r in caplog.records)
    assert "api_call" in formatted
    assert "test-key-123456" not in formatted


def test_missing_key_raises(tmp_path):
    with pytest.raises(ApiError, match="API_FOOTBALL_KEY"):
        ApiFootballClient(Settings(cache_dir=str(tmp_path)))


def test_current_leagues_parses_coverage(tmp_path):
    payload = {
        "errors": [],
        "response": [
            {
                "league": {"id": 39, "name": "Premier League"},
                "country": {"name": "England"},
                "seasons": [
                    {"year": 2025, "current": False},
                    {
                        "year": 2026,
                        "current": True,
                        "start": "2026-08-15",
                        "end": "2027-05-23",
                        "coverage": {
                            "fixtures": {
                                "events": True,
                                "statistics_fixtures": True,
                                "lineups": True,
                            },
                            "standings": True,
                            "injuries": False,
                        },
                    },
                ],
            }
        ],
    }
    client = _client(tmp_path, lambda r: httpx.Response(200, json=payload))
    cov = client.current_leagues()[39]
    assert cov.season == 2026 and cov.fixtures_statistics and not cov.injuries


def test_empty_env_secret_treated_as_missing(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "")
    monkeypatch.setenv("SMTP_PORT", "")
    s = Settings()
    assert s.api_football_key is None and s.smtp_port == 587


def test_describe_structure_and_annotation_escape():
    from sports_analytics.diagnostics import _escape_annotation, describe_structure

    lines = describe_structure({"data": [{"tournament": {"rankId": 2, "court": {"name": "Hard"}}}]})
    assert "data[0].tournament.rankId: int = '2'" in lines
    assert _escape_annotation("50%\nok") == "50%25%0Aok"


def test_tennis_probe_reports_without_key(tmp_path, monkeypatch):
    from sports_analytics.diagnostics import check_tennis

    assert "no está definida" in "\n".join(check_tennis(Settings(cache_dir=str(tmp_path))))
