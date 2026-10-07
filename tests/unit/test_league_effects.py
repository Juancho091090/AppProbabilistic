"""Efectos por competición: nivel de goles y ventaja de local con shrinkage."""

from datetime import UTC, datetime, timedelta

import pytest

from sports_analytics.data.schemas import FootballMatch, MatchStatus
from sports_analytics.models.football.elo import FootballElo
from sports_analytics.models.football.league import fit_league_effects
from sports_analytics.models.football.poisson import PoissonGoalsModel

T0 = datetime(2026, 1, 1, tzinfo=UTC)
RECENCY = {"method": "exponential", "half_life_days": {"football": 10_000}, "min_weight": 0.05}


def _m(i, comp, hg, ag, home="H", away="A"):
    return FootballMatch(
        match_id=f"{comp}{i}",
        competition_key=comp,
        home_team=f"{home}{i % 6}",
        away_team=f"{away}{(i + 1) % 6}",
        kickoff_utc=T0 + timedelta(days=i),
        status=MatchStatus.FINISHED,
        home_goals=hg,
        away_goals=ag,
    )


def _history():
    # "liga": local fuerte (2-1); "selecciones": sin ventaja de local y pocos goles (1-1)
    return [_m(i, "liga", 2, 1) for i in range(200)] + [
        _m(i, "selecciones", 1, 1, "N", "M") for i in range(200)
    ]


def test_league_effects_capture_goal_level_and_home_advantage():
    hist = _history()
    as_of = T0 + timedelta(days=500)
    eff = fit_league_effects(hist, as_of, RECENCY, prior_matches=60)
    assert eff.goal_means("liga")[0] > eff.goal_means("selecciones")[0]
    assert eff.hfa_ratio("liga") > 1.0 > eff.hfa_ratio("selecciones")
    # 200 partidos 1-1 frente a 60 virtuales de la media (1.5-1.0): ln(290/260)/ln(1.5) ≈ 0.27
    assert eff.hfa_ratio("selecciones") == pytest.approx(0.27, abs=0.02)
    assert eff.goal_means("desconocida") == (eff.global_home, eff.global_away)
    # Shrinkage: con pocos partidos la liga queda cerca de la media global
    few = fit_league_effects(hist[:200] + hist[200:203], as_of, RECENCY, prior_matches=60)
    assert few.hfa_ratio("selecciones") > 0.8


def test_models_use_league_effects():
    hist = _history()
    as_of = T0 + timedelta(days=500)
    eff = fit_league_effects(hist, as_of, RECENCY, prior_matches=60)
    pois = PoissonGoalsModel()
    pois.league = eff
    pois.fit(hist, as_of, RECENCY)
    lam_l, mu_l = pois.expected_goals("H1", "A2", competition="liga")
    lam_s, mu_s = pois.expected_goals("H1", "A2", competition="selecciones")
    assert lam_l / mu_l > lam_s / mu_s
    elo = FootballElo(home_advantage=65)
    elo.league = eff
    assert elo.hfa(competition="liga") > elo.hfa(competition="selecciones")
    assert elo.hfa(neutral=True, competition="liga") == 0.0
    p_l = elo.predict_1x2("x", "y", competition="liga")["home"]
    p_s = elo.predict_1x2("x", "y", competition="selecciones")["home"]
    assert p_l > p_s
