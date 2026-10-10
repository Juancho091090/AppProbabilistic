from dataclasses import dataclass
from datetime import date

from sports_analytics.evaluation.track_record import track_record

M = "v1"


@dataclass
class P:
    sport: str
    event_id: str
    market: str
    event: str
    probability: float
    confidence: str = "medium"
    line_key: str = ""
    model: str = M
    prediction_date: date = date(2026, 10, 1)


@dataclass
class R:
    outcome: int


def _match(eid, probs, result, conf="medium", d=date(2026, 10, 1), model=M):
    evs = ("home_win", "draw", "away_win")
    return [
        (
            P("football", eid, "1x2", e, pr, conf, model=model, prediction_date=d),
            R(int(e == result)),
        )
        for e, pr in zip(evs, probs, strict=True)
    ]


def test_favorite_hits_and_expected_rate():
    pairs = (
        _match("1", (0.6, 0.25, 0.15), "home_win", "high")
        + _match("2", (0.5, 0.3, 0.2), "draw")
        + _match("3", (0.2, 0.3, 0.5), "away_win", "low")
        + _match("4", (0.9, 0.05, 0.05), "away_win", model="otro")  # otro modelo: fuera
    )
    rows = {r["label"]: r for r in track_record(pairs, M)}
    allr = rows["Fútbol 1X2 · favorito (todos)"]
    assert (allr["n"], allr["hits"]) == (3, 2)
    assert abs(allr["expected_rate"] - (0.6 + 0.5 + 0.5) / 3) < 1e-9
    assert rows["Fútbol 1X2 · confianza Alta"]["hits"] == 1
    assert rows["Fútbol 1X2 · confianza Media"]["hits"] == 0


def test_only_latest_prediction_per_match_counts():
    pairs = _match("1", (0.6, 0.2, 0.2), "home_win", d=date(2026, 10, 1)) + _match(
        "1", (0.2, 0.2, 0.6), "home_win", d=date(2026, 10, 2)
    )
    r = track_record(pairs, M)[0]
    assert (r["n"], r["hits"]) == (1, 0)


def test_goals_most_likely_side_and_tennis():
    pairs = [
        (P("football", "1", "goals_total", "over", 0.3, line_key="2.5"), R(0)),  # menos: acierto
        (P("football", "2", "goals_total", "over", 0.7, line_key="2.5"), R(0)),  # fallo
        (P("football", "2", "goals_total", "over", 0.9, line_key="1.5"), R(1)),  # otra línea
        (P("tennis", "t", "winner", "player_a_win", 0.65), R(1)),
        (P("tennis", "t", "winner", "player_b_win", 0.35), R(0)),
    ]
    rows = {r["label"]: r for r in track_record(pairs, M)}
    g = rows["Fútbol · más/menos de 2.5 goles"]
    assert (g["n"], g["hits"]) == (2, 1) and abs(g["expected_rate"] - 0.7) < 1e-9
    assert rows["Tenis · ganador"]["hits"] == 1
