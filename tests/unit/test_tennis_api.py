"""Tests del cliente de Tennis API con payloads que replican la estructura real verificada."""

from datetime import UTC, date, datetime

import httpx
import pytest

from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError
from sports_analytics.data.clients.tennis_api import (
    TennisApiClient,
    best_of_for,
    is_doubles,
    is_main_tour,
    parse_score,
    parse_tournament,
    to_tennis_match,
)
from sports_analytics.data.schemas import MatchStatus

CALENDAR_ITEM = {
    "id": 21364,
    "name": "Nitto ATP Finals - Turin",
    "courtId": 3,
    "date": "2026-11-16T00:00:00.000Z",
    "rankId": 7,
    "tier": "Finals",
    "link": 20375,
    "court": {"id": 3, "name": "I.hard"},
    "round": {"id": 7, "name": "Tour finals"},
    "country": {"acronym": "ITA", "name": "Italy"},
}

RESULT_ITEM = {
    "id": "127459462",
    "matchId": "127459462",
    "date": "2026-10-05T19:10:00.000Z",
    "roundId": 4,
    "player1Id": 37577,
    "player2Id": 73268,
    "tournamentId": 22094,
    "match_winner": 37577,
    "result": "6-1 6-4",
    "result_type": "completed",
    "best_of": None,
    "player1": {"id": 37577, "name": "Genaro Alberto Olivieri", "countryAcr": "ARG"},
    "player2": {"id": 73268, "name": "Valerio Aboian", "countryAcr": "ARG"},
    "tournament": {
        "id": 22094,
        "name": "Antofagasta Challenger",
        "courtId": 2,
        "date": "2026-10-05T00:00:00.000Z",
        "rankId": 1,
        "countryAcr": "CHI",
        "court": None,
        "rank": None,
        "country": None,
    },
}

FIXTURE_ITEM = {
    "id": 1434,
    "matchId": 1434,
    "date": "2026-10-06T21:00:00.000Z",
    "startTime": "2026-10-06T21:00:00.000Z",
    "roundId": 4,
    "player1Id": 98385,
    "player2Id": 89843,
    "tournamentId": 22094,
    "seed1": "q",
    "seed2": "WC",
    "live": None,
    "player1": {"id": 98385, "name": "Joaquin Aguilar Cardozo", "countryAcr": "URU"},
    "player2": {"id": 89843, "name": "Nicolas Villalon", "countryAcr": "CHI"},
}


def _info(rank_id=2, court_id=1, name="China Open - Beijing", tier=""):
    return parse_tournament(
        {
            "id": 1,
            "name": name,
            "rankId": rank_id,
            "courtId": court_id,
            "tier": tier,
            "date": "2026-09-24T00:00:00.000Z",
        }
    )


# ------------------------------------------------------------------ parsing


def test_parse_tournament_surface_and_indoor():
    info = parse_tournament(CALENDAR_ITEM)
    assert (info.surface, info.indoor, info.rank_id, info.tier) == ("hard", True, 7, "Finals")
    assert parse_tournament({**CALENDAR_ITEM, "courtId": 2}).surface == "clay"
    assert parse_tournament({**CALENDAR_ITEM, "courtId": 5}).surface == "grass"
    assert (
        parse_tournament({**CALENDAR_ITEM, "courtId": 99, "court": {"name": "Red Clay"}}).surface
        == "clay"
    )


@pytest.mark.parametrize(
    "raw,sets,games,retired",
    [
        ("6-1 6-4", (2, 0), (12, 5), False),
        ("6-3 2-6 4-2 ret.", (1, 1), (12, 11), True),
        ("7-6(4) 6-7(5) 7-6(10)", (2, 1), (20, 19), False),
        ("6-4 ret.", (1, 0), (6, 4), True),
    ],
)
def test_parse_score(raw, sets, games, retired):
    s = parse_score(raw)
    assert s.sets_won() == sets and s.games == games and s.retired == retired


def test_main_tour_filter(app_config):
    cfg = app_config.tennis
    assert is_main_tour(_info(rank_id=2), cfg)
    assert is_main_tour(_info(rank_id=4, name="Roland Garros"), cfg)
    assert not is_main_tour(_info(rank_id=1, name="Antofagasta Challenger"), cfg)
    assert not is_main_tour(_info(rank_id=0, name="M15 Quito"), cfg)
    # Aunque el rankId fuese alto, un nombre de circuito excluido no pasa
    assert not is_main_tour(_info(rank_id=2, name="Suzhou WTA 125 - Suzhou"), cfg)
    assert not is_main_tour(None, cfg)


def test_doubles_detection():
    assert is_doubles({"player1": {"name": "Granollers/Zeballos"}, "player2": {"name": "X/Y"}})
    assert not is_doubles(FIXTURE_ITEM)


def test_best_of():
    assert best_of_for("atp", _info(rank_id=4), 4) == 5
    assert best_of_for("wta", _info(rank_id=4), 4) == 3
    assert best_of_for("atp", _info(rank_id=3), 4) == 3


def test_result_to_match():
    m = to_tennis_match(RESULT_ITEM, "atp", parse_tournament(RESULT_ITEM["tournament"]))
    assert m.status == MatchStatus.FINISHED and m.is_finished
    assert m.winner == "A" and (m.sets_a, m.sets_b) == (2, 0) and (m.games_a, m.games_b) == (12, 5)
    assert m.surface == "clay" and m.match_id == "atp-127459462"
    assert m.kickoff_utc == datetime(2026, 10, 5, 19, 10, tzinfo=UTC)


def test_result_winner_b_with_winner_perspective_score_is_reoriented():
    item = {**RESULT_ITEM, "match_winner": 73268}  # marcador "6-1 6-4" visto desde el ganador
    m = to_tennis_match(item, "atp", None)
    assert m.winner == "B" and (m.sets_a, m.sets_b) == (0, 2)


def test_retired_match_flagged():
    item = {**RESULT_ITEM, "result": "6-3 3-0 ret.", "result_type": "retired"}
    m = to_tennis_match(item, "atp", None)
    assert m.retired and m.is_finished


def test_fixture_to_match_with_rankings():
    m = to_tennis_match(FIXTURE_ITEM, "wta", _info(), ranks={98385: 120, 89843: 85})
    assert m.status == MatchStatus.SCHEDULED and m.winner is None
    assert (m.rank_a, m.rank_b) == (120, 85) and m.tour == "WTA"


# --------------------------------------------------------------- cliente


def _client(tmp_path, handler):
    settings = Settings(tennis_api_key="rapid-key-000000", cache_dir=str(tmp_path))
    return TennisApiClient(settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_pagination_and_headers(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        page = int(request.url.params["pageNo"])
        return httpx.Response(
            200, json={"data": [CALENDAR_ITEM | {"id": page}], "hasNextPage": page < 3}
        )

    client = _client(tmp_path, handler)
    out = client.tournaments("atp", 2026)
    assert sorted(out) == [1, 2, 3]
    assert seen[0].headers["X-RapidAPI-Key"] == "rapid-key-000000"
    assert seen[0].headers["X-RapidAPI-Host"] == "tennis-api-atp-wta-itf.p.rapidapi.com"
    assert seen[0].url.path == "/tennis/v2/atp/tournament/calendar/2026"


def test_closed_results_are_cached(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"data": [RESULT_ITEM], "hasNextPage": False})

    client = _client(tmp_path, handler)
    today = date(2026, 10, 6)
    client.results("atp", date(2026, 9, 1), date(2026, 9, 7), today)
    client.results("atp", date(2026, 9, 1), date(2026, 9, 7), today)
    assert len(calls) == 1


def test_subscription_error_is_explicit(tmp_path):
    def handler(request):
        return httpx.Response(403, json={"message": "You are not subscribed to this API."})

    with pytest.raises(ApiError, match="not subscribed"):
        _client(tmp_path, handler).fixtures("atp", date(2026, 10, 6))


def test_rankings_parse(tmp_path):
    payload = {
        "data": [
            {
                "id": 1,
                "position": 1,
                "point": 11000,
                "player": {"id": 47275, "name": "Jannik Sinner"},
            }
        ]
    }
    client = _client(tmp_path, lambda r: httpx.Response(200, json=payload))
    assert client.rankings("atp") == {47275: 1}


def test_invalid_tour_rejected(tmp_path):
    client = _client(tmp_path, lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client.fixtures("itf", date(2026, 10, 6))


def test_tournament_results_one_call_singles_and_qualifying(tmp_path):
    # Estructura real verificada el 8-oct-2026 (US Open ATP, id 21349)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "data": {
                    "singles": [{"id": "1"}, {"id": "2"}],
                    "qualifying": [{"id": "3"}],
                    "doubles": [{"id": "4"}],
                    "doublesQualifying": [],
                }
            },
        )

    client = _client(tmp_path, handler)
    items = client.tournament_results("atp", 21349, closed=True)
    assert [i["id"] for i in items] == ["1", "2", "3"]  # sin dobles
    assert calls == ["/tennis/v2/atp/tournament/results/21349"]
    client.tournament_results("atp", 21349, closed=True)  # torneo cerrado: caché
    assert len(calls) == 1


def test_tournament_results_unexpected_payload(tmp_path):
    client = _client(tmp_path, lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(ApiError):
        client.tournament_results("wta", 1, closed=False)
