from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from scipy.stats import poisson

from sports_analytics.data.schemas import FootballMatch, MatchStatus
from sports_analytics.models.football.corners import CornersModel, estimate_dispersion, negbin_pmf
from sports_analytics.models.football.dixon_coles import DixonColesModel, dixon_coles_matrix
from sports_analytics.models.football.elo import FootballElo, goal_diff_multiplier
from sports_analytics.models.football.markets import one_x_two, summarize_matrix, total_goals_pmf
from sports_analytics.models.football.poisson import PoissonGoalsModel
from sports_analytics.models.football.predictor import FootballPredictor
from tests.synthetic import football_league

T0 = datetime(2025, 8, 1, tzinfo=UTC)


def _fm(i, h, a, hg, ag, days=0):
    return FootballMatch(
        match_id=f"f{i}",
        competition_key="x",
        home_team=h,
        away_team=a,
        kickoff_utc=T0 + timedelta(days=days or i),
        status=MatchStatus.FINISHED,
        home_goals=hg,
        away_goals=ag,
    )


@pytest.fixture(scope="module")
def league():
    return football_league(n_teams=12, rounds=4)


@pytest.fixture(scope="module")
def recency(app_config):
    return app_config.models.recency


# -------------------------------------------------------------------- Elo


def test_goal_diff_multiplier():
    assert goal_diff_multiplier(0) == 1.0
    assert goal_diff_multiplier(2) == 1.5
    assert goal_diff_multiplier(-3) == pytest.approx(14 / 8)


def test_elo_zero_sum_and_home_advantage():
    elo = FootballElo()
    p = elo.predict_1x2("A", "B")
    assert p["home"] > p["away"]
    assert sum(p.values()) == pytest.approx(1.0)
    elo.update(_fm(0, "A", "B", 2, 0))
    assert elo.rating("A") + elo.rating("B") == pytest.approx(3000)
    assert elo.rating("A") > 1500


def test_elo_neutral_venue_symmetric():
    p = FootballElo().predict_1x2("A", "B", neutral=True)
    assert p["home"] == pytest.approx(p["away"])


def test_elo_ignores_future_matches():
    hist = [_fm(i, "A", "B", 1, 0) for i in range(5)]
    future = _fm(99, "B", "A", 5, 0)
    as_of = T0 + timedelta(days=10)
    e1 = FootballElo().fit(hist, as_of)
    e2 = FootballElo().fit([*hist, future], as_of)
    assert e1.ratings == e2.ratings


# ---------------------------------------------------------------- Poisson


def test_score_matrix_matches_poisson():
    m = PoissonGoalsModel().score_matrix(1.5, 1.0)
    assert m.sum() == pytest.approx(1.0)
    assert m[0, 0] == pytest.approx(poisson.pmf(0, 1.5) * poisson.pmf(0, 1.0), rel=1e-3)


def test_poisson_recovers_strength_ordering(league, recency):
    matches, truth = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    rec = {**recency, "half_life_days": {"football": 10_000}}
    model = PoissonGoalsModel(prior_strength=2).fit(matches, as_of, rec)
    est = np.array([model.strength(t).attack for t in truth])
    real = np.array([v[0] for v in truth.values()])
    assert np.corrcoef(est, real)[0, 1] > 0.8
    assert model.mu_home > model.mu_away  # localía detectada


def test_poisson_shrinks_unknown_team(league, recency):
    matches, _ = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    model = PoissonGoalsModel().fit(matches, as_of, recency)
    s = model.strength("Recién Ascendido")
    assert (s.attack, s.defense, s.n_matches) == (1.0, 1.0, 0)
    assert not model.has_enough_data("Recién Ascendido")


# ------------------------------------------------------------ Dixon-Coles


def test_dixon_coles_rho_zero_equals_poisson():
    dc = dixon_coles_matrix(1.4, 1.1, 0.0)
    po = PoissonGoalsModel().score_matrix(1.4, 1.1)
    assert np.allclose(dc, po)


def test_dixon_coles_negative_rho_inflates_draws():
    dc = dixon_coles_matrix(1.4, 1.1, -0.12)
    po = dixon_coles_matrix(1.4, 1.1, 0.0)
    assert dc.sum() == pytest.approx(1.0)
    assert dc[0, 0] > po[0, 0] and dc[1, 1] > po[1, 1]
    assert dc[1, 0] < po[1, 0] and dc[0, 1] < po[0, 1]


def test_dixon_coles_uses_default_rho_with_little_data(league, recency):
    matches, _ = league
    small = matches[:50]
    as_of = small[-1].kickoff_utc + timedelta(days=1)
    base = PoissonGoalsModel().fit(small, as_of, recency)
    dc = DixonColesModel(base=base, min_matches_fit=150).fit_rho(small, as_of, recency)
    assert not dc.rho_estimated and dc.rho == -0.10


def test_dixon_coles_requires_same_as_of(league, recency):
    matches, _ = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    base = PoissonGoalsModel().fit(matches, as_of, recency)
    with pytest.raises(ValueError):
        DixonColesModel(base=base).fit_rho(matches, as_of + timedelta(days=1), recency)


def test_dixon_coles_rho_estimated_within_bounds(league, recency):
    matches, _ = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    base = PoissonGoalsModel().fit(matches, as_of, recency)
    dc = DixonColesModel(base=base, min_matches_fit=150).fit_rho(matches, as_of, recency)
    assert dc.rho_estimated
    assert -0.20 <= dc.rho <= 0.05


# ---------------------------------------------------------------- markets


def test_markets_consistency():
    m = dixon_coles_matrix(1.6, 1.0, -0.1)
    s = summarize_matrix(m, [1.5, 2.5])
    assert sum(s["1x2"].values()) == pytest.approx(1.0)
    assert s["goal_lines"]["2.5"]["over"] + s["goal_lines"]["2.5"]["under"] == pytest.approx(1.0)
    assert s["goal_lines"]["1.5"]["over"] > s["goal_lines"]["2.5"]["over"]
    assert total_goals_pmf(m).sum() == pytest.approx(1.0)
    assert s["expected_goals"]["total"] == pytest.approx(
        s["expected_goals"]["home"] + s["expected_goals"]["away"]
    )


def test_integer_lines_rejected():
    with pytest.raises(ValueError):
        summarize_matrix(dixon_coles_matrix(1.2, 1.2, 0), [2])


# ---------------------------------------------------------------- córners


def test_negbin_mean_and_overdispersion():
    support = np.arange(200)
    pmf = negbin_pmf(10.0, 5.0, support)
    mean = (pmf * support).sum()
    var = (pmf * (support - mean) ** 2).sum()
    assert mean == pytest.approx(10.0, rel=1e-6)
    assert var == pytest.approx(10 + 100 / 5, rel=1e-4)


def test_dispersion_estimator():
    rng = np.random.default_rng(1)
    mu = np.full(20000, 10.0)
    y_po = rng.poisson(mu)
    r = 6.0
    y_nb = rng.negative_binomial(r, r / (r + mu))
    w = np.ones_like(mu)
    ratio_po, r_po = estimate_dispersion(y_po, mu, w)
    ratio_nb, r_nb = estimate_dispersion(y_nb, mu, w)
    assert ratio_po == pytest.approx(1.0, abs=0.05)
    assert ratio_nb > 1.5 and r_nb == pytest.approx(r, rel=0.15)


@pytest.mark.parametrize("nb_r,expected", [(6.0, "negative_binomial"), (None, "poisson")])
def test_corners_family_selection(app_config, recency, nb_r, expected):
    matches, _ = football_league(n_teams=12, rounds=4, corners_nb_r=nb_r, seed=5)
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    model = CornersModel.from_config(app_config.models.football["corners"]).fit(
        matches, as_of, recency
    )
    assert model.distribution == expected
    out = model.predict("Team00", "Team01", [9.5])
    assert sum(out["total_distribution"].values()) == pytest.approx(1.0, abs=1e-3)


# -------------------------------------------------------------- predictor


def _predictor(app_config, min_samples=200):
    m = app_config.models
    cfg = {**m.football, "logistic": {**m.football["logistic"], "min_samples": min_samples}}
    return FootballPredictor(cfg, m.recency, m.confidence)


def test_football_predictor_end_to_end(app_config, league):
    matches, truth = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    pred = _predictor(app_config).fit(matches, as_of)
    strongest = max(truth, key=lambda t: truth[t][0] / truth[t][1])
    weakest = min(truth, key=lambda t: truth[t][0] / truth[t][1])
    up = FootballMatch(
        match_id="u1",
        competition_key="test_league",
        home_team=strongest,
        away_team=weakest,
        kickoff_utc=as_of + timedelta(hours=5),
    )
    f = pred.predict(up, "Test League")
    p = f.markets["1x2"]
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["home"] > p["away"]
    assert f.per_model["logistic"] is not None
    assert f.markets["corners"] is not None
    assert f.confidence in {"high", "medium", "low"}
    events = {(r.market, r.model) for r in f.records}
    assert ("1x2", "ensemble_v1") in events and ("goals_total", "dixon_coles") in events
    for r in f.records:
        assert 0 <= r.probability <= 1


def test_football_predictor_no_leakage(app_config, league):
    matches, _ = league
    cut = 300
    as_of = matches[cut].kickoff_utc
    up = FootballMatch(
        match_id="u2",
        competition_key="test_league",
        home_team="Team03",
        away_team="Team07",
        kickoff_utc=as_of + timedelta(hours=2),
    )
    f1 = _predictor(app_config).fit(matches[:cut], as_of).predict(up, "T")
    f2 = _predictor(app_config).fit(matches, as_of).predict(up, "T")
    assert f1.markets["1x2"] == pytest.approx(f2.markets["1x2"], abs=1e-12)
    assert f1.markets["goals"]["expected_goals"] == pytest.approx(
        f2.markets["goals"]["expected_goals"]
    )


def test_logistic_missing_is_renormalized(app_config, league):
    matches, _ = league
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    pred = _predictor(app_config, min_samples=10**6).fit(matches, as_of)
    up = FootballMatch(
        match_id="u3",
        competition_key="test_league",
        home_team="Team00",
        away_team="Team01",
        kickoff_utc=as_of + timedelta(hours=1),
    )
    f = pred.predict(up, "T")
    assert f.per_model["logistic"] is None
    assert "logistic" in f.context["models_missing"]
    assert sum(f.context["ensemble_weights"].values()) == pytest.approx(1.0)


def test_one_x_two_simple():
    m = np.array([[0.2, 0.1], [0.3, 0.4]])
    assert one_x_two(m) == pytest.approx({"home": 0.3, "draw": 0.6, "away": 0.1})


def test_dixon_coles_recovers_true_rho(recency):
    """Simula marcadores con ρ = −0.10 y verifica que el MLE lo recupera."""
    rng = np.random.default_rng(0)
    n = 20
    att, dfn = np.exp(rng.normal(0, 0.25, n)), np.exp(rng.normal(0, 0.25, n))
    matches, k = [], 0
    for _ in range(6):
        for h in range(n):
            for a in range(n):
                if h == a:
                    continue
                probs = dixon_coles_matrix(
                    1.5 * att[h] * dfn[a], 1.1 * att[a] * dfn[h], -0.10
                ).ravel()
                hg, ag = divmod(int(rng.choice(len(probs), p=probs / probs.sum())), 11)
                matches.append(_fm(k, str(h), str(a), hg, ag, days=k + 1))
                k += 1
    as_of = matches[-1].kickoff_utc + timedelta(days=1)
    rec = {**recency, "half_life_days": {"football": 1e6}}
    base = PoissonGoalsModel(prior_strength=2).fit(matches, as_of, rec)
    dc = DixonColesModel(base=base, rho_bounds=(-0.3, 0.3)).fit_rho(matches, as_of, rec)
    assert -0.17 < dc.rho < -0.05
