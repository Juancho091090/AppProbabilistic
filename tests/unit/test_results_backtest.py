from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from sports_analytics.backtesting.engine import run_backtest
from sports_analytics.backtesting.metrics import ResolvedPrediction, prob_bucket, segment_metrics
from sports_analytics.pipeline.results import football_outcome, tennis_outcome
from tests.synthetic import START, football_league, tennis_tour

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
KO = datetime(2026, 10, 6, 1, tzinfo=UTC)


def _pred(market, event, line=None, kickoff=KO):
    return SimpleNamespace(market=market, event=event, line=line, kickoff_utc=kickoff)


def _fm(status="finished", hg=2, ag=1):
    return SimpleNamespace(status=status, home_goals=hg, away_goals=ag)


@pytest.mark.parametrize("event,expected", [("home_win", 1), ("draw", 0), ("away_win", 0)])
def test_football_1x2_outcome(event, expected):
    assert football_outcome(_pred("1x2", event), _fm(), None, NOW)[0] == expected


def test_football_goals_and_corners():
    assert football_outcome(_pred("goals_total", "over_2.5", 2.5), _fm(), None, NOW)[:2] == (1, 3.0)
    st = SimpleNamespace(available=True, home_corners=4, away_corners=3)
    assert football_outcome(_pred("corners_total", "over_7.5", 7.5), _fm(), st, NOW)[:2] == (0, 7.0)
    # sin estadísticas aún: esperar; tras 7 días: anular
    assert football_outcome(_pred("corners_total", "over_7.5", 7.5), _fm(), None, NOW) is None
    late = NOW + timedelta(days=10)
    assert (
        football_outcome(_pred("corners_total", "over_7.5", 7.5), _fm(), None, late)[2]
        == "no_stats"
    )


def test_football_void_and_pending():
    assert football_outcome(_pred("1x2", "draw"), _fm("postponed"), None, NOW) == (
        None,
        None,
        "postponed",
    )
    assert football_outcome(_pred("1x2", "draw"), _fm("scheduled", None, None), None, NOW) is None


def _tm(winner="A", retired=False, sets=(2, 1), games=(13, 11), status="finished"):
    return SimpleNamespace(
        status=status,
        winner=winner,
        retired=retired,
        sets_a=sets[0],
        sets_b=sets[1],
        games_a=games[0],
        games_b=games[1],
    )


def test_tennis_outcomes():
    assert tennis_outcome(_pred("winner", "player_b_win"), _tm(), NOW)[0] == 0
    assert tennis_outcome(_pred("at_least_one_set", "player_b"), _tm(), NOW)[0] == 1
    assert tennis_outcome(_pred("games_total", "over_22.5", 22.5), _tm(), NOW)[:2] == (1, 24.0)
    assert tennis_outcome(_pred("winner", "player_a_win"), _tm(retired=True), NOW)[2] == "retired"


def test_prob_bucket_and_segments():
    assert prob_bucket(0.05) == "0.0-0.1" and prob_bucket(1.0) == "0.9-1.0"
    assert prob_bucket(0.7) == "0.7-0.8" and prob_bucket(0.3) == "0.3-0.4"
    rows = [
        ResolvedPrediction("football", "PL", "m", "1x2", "home_win", p, y)
        for p, y in [(0.7, 1), (0.6, 0), (0.2, 0), (0.8, 1)]
    ]
    seg = {(m.segment_type, m.segment_value): m for m in segment_metrics(rows)}
    g = seg[("global", "all")]
    assert g.n == 4 and g.brier == pytest.approx(np.mean([0.09, 0.36, 0.04, 0.04]))
    assert ("competition", "PL") in seg and ("prob_bucket", "0.7-0.8") in seg


# ------------------------------------------------------------- backtesting


def test_backtest_football_walk_forward(app_config):
    matches, _ = football_league(n_teams=12, rounds=4)
    tz = ZoneInfo("America/Bogota")
    start = (START + timedelta(days=60)).date()
    end = matches[-1].kickoff_utc.date()
    rep = run_backtest("football", matches, app_config, start, end, tz, refit_every_days=14)
    assert rep.n_matches > 100
    main = [p for p in rep.predictions if p.model == "ensemble_v1" and p.market == "1x2"]
    probs = np.array([p.probability for p in main])
    y = np.array([p.outcome for p in main])
    brier_model = np.mean((probs - y) ** 2)
    brier_naive = np.mean((y.mean() - y) ** 2)
    assert brier_model < brier_naive  # el modelo aporta información frente a la tasa base
    md = rep.to_markdown("ensemble_v1")
    assert "Métricas globales" in md and "Curva de calibración" in md and "Por competición" in md


def test_backtest_tennis_runs(app_config):
    matches, _ = tennis_tour(n_matches=900)
    tz = ZoneInfo("America/Bogota")
    start = matches[600].kickoff_utc.date()
    end = matches[-1].kickoff_utc.date()
    rep = run_backtest("tennis", matches, app_config, start, end, tz, refit_every_days=30)
    assert rep.n_matches > 100
    assert any(m.market == "winner" and m.segment_type == "global" for m in rep.metrics)


def test_backtest_rejects_bad_range(app_config):
    with pytest.raises(ValueError):
        run_backtest(
            "football", [], app_config, date(2026, 2, 1), date(2026, 1, 1), ZoneInfo("UTC")
        )
