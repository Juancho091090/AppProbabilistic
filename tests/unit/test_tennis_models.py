from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sports_analytics.data.schemas import MatchStatus, TennisMatch
from sports_analytics.models.tennis.elo import TennisElo, elo_probability
from sports_analytics.models.tennis.markov import (
    barnett_clarke,
    match_distribution,
    p_game,
    p_tiebreak,
    serve_probs_from_match_prob,
    set_outcomes,
)
from sports_analytics.models.tennis.predictor import TennisPredictor
from tests.synthetic import tennis_tour

# ------------------------------------------------------------------ Markov


def test_p_game_known_values():
    assert p_game(0.5) == pytest.approx(0.5)
    assert p_game(0.6) == pytest.approx(0.735729, abs=1e-6)
    assert p_game(0.0) == 0.0
    assert p_game(1.0) == 1.0


def test_tiebreak_symmetry():
    assert p_tiebreak(0.65, 0.65, True) == pytest.approx(0.5)
    assert p_tiebreak(0.70, 0.60, True) > 0.5
    # 10 puntos amplifica la ventaja del mejor
    assert p_tiebreak(0.70, 0.60, True, 10) > p_tiebreak(0.70, 0.60, True, 7)


def test_set_outcomes_sum_to_one_and_valid_scores():
    outs = set_outcomes(0.64, 0.60, True)
    assert sum(o.probability for o in outs) == pytest.approx(1.0)
    valid = (
        {(6, k) for k in range(5)} | {(k, 6) for k in range(5)} | {(7, 5), (5, 7), (7, 6), (6, 7)}
    )
    assert {(o.games_a, o.games_b) for o in outs} <= valid


def test_equal_players_fifty_fifty():
    d = match_distribution(0.63, 0.63, best_of=3)
    assert d.p_a_wins == pytest.approx(0.5, abs=1e-9)
    assert d.p_a_wins_set == pytest.approx(d.p_b_wins_set)


def test_best_of_five_favours_stronger_player():
    assert match_distribution(0.66, 0.62, 5).p_a_wins > match_distribution(0.66, 0.62, 3).p_a_wins


def test_distribution_consistency():
    d = match_distribution(0.65, 0.58, 3)
    assert d.games_pmf.sum() == pytest.approx(1.0)
    assert sum(d.set_scores.values()) == pytest.approx(1.0)
    assert d.p_a_wins_set >= d.p_a_wins
    assert d.games_pmf[:12].sum() == pytest.approx(0.0)  # mínimo 12 juegos (6-0 6-0)
    lines = d.games_lines([21.5])["21.5"]
    assert lines["over"] + lines["under"] == pytest.approx(1.0)


def test_regression_against_user_spreadsheet():
    """Valor de referencia de la hoja 'Apuestas deportivas' (modelo exacto 42.1%)."""
    d = match_distribution(0.568, 0.478, best_of=3, final_set_tiebreak=7)
    assert d.games_lines([21.5])["21.5"]["over"] == pytest.approx(0.421, abs=0.001)


def _simulate(p_a, p_b, best_of, rng, n=20000):
    """Simulación Monte Carlo independiente para validar el modelo exacto."""
    need = best_of // 2 + 1
    games_total, wins = np.zeros(n), 0
    for i in range(n):
        a_srv = rng.random() < 0.5
        sa = sb = g_tot = 0
        while sa < need and sb < need:
            ga = gb = 0
            while True:
                if ga == 6 and gb == 6:
                    pa_pts = pb_pts = 0
                    k = 0
                    while True:
                        first = ((k + 1) // 2) % 2 == 0
                        a_serving = first == a_srv
                        p = p_a if a_serving else 1 - p_b
                        if rng.random() < p:
                            pa_pts += 1
                        else:
                            pb_pts += 1
                        k += 1
                        if (pa_pts >= 7 or pb_pts >= 7) and abs(pa_pts - pb_pts) >= 2:
                            break
                    ga, gb = (7, 6) if pa_pts > pb_pts else (6, 7)
                    a_srv = not a_srv  # 13 juegos -> cambia quien abre
                    break
                p_hold = p_game(p_a) if a_srv else p_game(p_b)
                server_wins = rng.random() < p_hold
                if a_srv == server_wins:
                    ga += 1
                else:
                    gb += 1
                a_srv = not a_srv
                if (max(ga, gb) == 6 and abs(ga - gb) >= 2) or max(ga, gb) == 7:
                    break
            g_tot += ga + gb
            if ga > gb:
                sa += 1
            else:
                sb += 1
        games_total[i] = g_tot
        wins += sa == need
    return wins / n, games_total.mean()


def test_exact_model_matches_monte_carlo():
    rng = np.random.default_rng(3)
    p_win_mc, games_mc = _simulate(0.64, 0.60, 3, rng)
    d = match_distribution(0.64, 0.60, 3)
    assert d.p_a_wins == pytest.approx(p_win_mc, abs=0.012)
    assert d.expected_games == pytest.approx(games_mc, abs=0.15)


def test_serve_probs_inversion():
    pa, pb = serve_probs_from_match_prob(0.72, 0.62, 3)
    assert match_distribution(pa, pb, 3).p_a_wins == pytest.approx(0.72, abs=1e-5)
    assert (pa + pb) / 2 == pytest.approx(0.62)


def test_barnett_clarke():
    pa, pb = barnett_clarke(0.65, 0.40, 0.60, 0.36, tour_avg_rpw=0.36)
    assert pa == pytest.approx(0.65)  # resto rival en la media
    assert pb == pytest.approx(0.56)  # A resta 4 pp mejor que la media


# --------------------------------------------------------------------- Elo


def _m(i, a, b, winner, surface="hard", retired=False):
    return TennisMatch(
        match_id=f"x{i}",
        tour="ATP",
        tournament="T",
        category="ATP 250",
        surface=surface,
        player_a=a,
        player_b=b,
        kickoff_utc=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        status=MatchStatus.FINISHED,
        winner=winner,
        retired=retired,
    )


def test_tennis_elo_updates_and_dynamic_k():
    elo = TennisElo()
    elo.update(_m(0, "A", "B", "A"))
    first_gain = elo.rating("A") - 1500
    assert first_gain > 0 and elo.rating("B") < 1500
    for i in range(1, 40):
        elo.update(_m(i, "C", "D", "C"))
    before = elo.rating("C")
    elo.update(_m(41, "C", "D", "D"))
    # Jugador con muchos partidos se mueve menos que un debutante
    assert abs(elo.rating("C") - before) < first_gain * 2


def test_tennis_elo_ignores_retirements_and_tracks_surface():
    elo = TennisElo()
    elo.update(_m(0, "A", "B", "A", retired=True))
    assert elo.rating("A") == 1500
    elo.update(_m(1, "A", "B", "A", surface="clay"))
    assert elo.surface_rating("A", "clay") > 1500
    assert elo.surface_rating("A", "grass") == 1500


def test_elo_probability_symmetry():
    assert elo_probability(1600, 1500) + elo_probability(1500, 1600) == pytest.approx(1.0)


# --------------------------------------------------------------- predictor


@pytest.fixture(scope="module")
def tennis_data():
    return tennis_tour(n_matches=2500)


def _predictor(app_config):
    m = app_config.models
    return TennisPredictor(m.tennis, m.recency, m.confidence)


def test_tennis_predictor_end_to_end(app_config, tennis_data):
    matches, skill = tennis_data
    as_of = matches[-1].kickoff_utc + timedelta(hours=1)
    pred = _predictor(app_config).fit(matches, as_of)
    best = max(skill, key=skill.get)
    worst = min(skill, key=skill.get)
    upcoming = TennisMatch(
        match_id="up1",
        tour="ATP",
        tournament="Synthetic Open",
        category="ATP 250",
        surface="hard",
        player_a=best,
        player_b=worst,
        kickoff_utc=as_of + timedelta(hours=3),
    )
    f = pred.predict(upcoming)
    assert f.markets["winner"]["A"] > 0.75
    assert f.markets["winner"]["A"] + f.markets["winner"]["B"] == pytest.approx(1.0)
    # Sets y juegos coherentes con la probabilidad final de ganador
    assert f.markets["at_least_one_set"]["A"] >= f.markets["winner"]["A"]
    assert f.per_model["logistic"] is not None and f.per_model["markov"] is not None
    assert any(r.market == "games_total" for r in f.records)


def test_tennis_predictor_no_leakage(app_config, tennis_data):
    matches, _ = tennis_data
    as_of = matches[2000].kickoff_utc
    upcoming = TennisMatch(
        match_id="up2",
        tour="ATP",
        tournament="X",
        category="ATP 250",
        surface="clay",
        player_a="P01",
        player_b="P02",
        kickoff_utc=as_of + timedelta(hours=1),
    )
    p1 = _predictor(app_config).fit(matches[:2000], as_of).predict(upcoming)
    p2 = _predictor(app_config).fit(matches, as_of).predict(upcoming)  # incluye "futuro"
    assert p1.markets["winner"]["A"] == pytest.approx(p2.markets["winner"]["A"], abs=1e-12)


def test_cannot_predict_started_match(app_config, tennis_data):
    matches, _ = tennis_data
    as_of = matches[-1].kickoff_utc + timedelta(hours=1)
    pred = _predictor(app_config).fit(matches, as_of)
    started = matches[-1].model_copy(update={"status": MatchStatus.SCHEDULED})
    with pytest.raises(ValueError, match="leakage"):
        pred.predict(started)
