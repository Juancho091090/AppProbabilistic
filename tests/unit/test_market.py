"""Benchmark de mercado: eliminación de margen, selección de referencia y comparación."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from sports_analytics.backtesting.engine import run_backtest
from sports_analytics.config.loader import MarketConfig
from sports_analytics.market.benchmark import (
    MARKET_MODEL,
    PairedForecast,
    market_probs_by_match,
    summarize,
    to_markdown,
    verdict,
)
from sports_analytics.market.odds import (
    MarketProbs,
    OddsQuote,
    devig,
    overround,
    parse_odds_response,
    reference_probs,
)
from tests.synthetic import START, football_league


def _bk(bid, name, h, d, a):
    return {
        "id": bid,
        "name": name,
        "bets": [
            {
                "id": 1,
                "name": "Match Winner",
                "values": [
                    {"value": "Home", "odd": h},
                    {"value": "Draw", "odd": d},
                    {"value": "Away", "odd": a},
                ],
            },
            {"id": 5, "name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.9"}]},
        ],
    }


def test_devig_croatia_spain_pinnacle():
    # Precios reales de Pinnacle el 6-oct-2026 (diagnóstico de API-Football)
    prices = (10.10, 6.36, 1.27)
    assert overround(prices) == pytest.approx(0.0436, abs=1e-4)
    h, d, a = devig(prices)
    assert h + d + a == pytest.approx(1.0)
    assert a == pytest.approx(0.7545, abs=1e-3) and h == pytest.approx(0.0949, abs=1e-3)


def test_devig_rejects_invalid_and_unknown_method():
    with pytest.raises(ValueError):
        devig((1.0, 3.0, 4.0))
    with pytest.raises(ValueError):
        devig((2.0, 3.0, 4.0), "shin")


def test_parse_odds_response_skips_incomplete_bookmakers():
    item = {
        "update": "2026-10-06T18:00:19+00:00",
        "bookmakers": [
            _bk(4, "Pinnacle", "10.10", "6.36", "1.27"),
            _bk(8, "Bet365", "9.00", "6.50", "1.25"),
            {
                "id": 9,
                "name": "Incompleta",
                "bets": [{"id": 1, "values": [{"value": "Home", "odd": "2"}]}],
            },
            _bk(11, "Inválida", "1.00", "3", "4"),
        ],
    }
    quotes = parse_odds_response([item])
    assert [q.bookmaker_id for q in quotes] == [4, 8]
    assert quotes[0].prices == (10.10, 6.36, 1.27)
    assert quotes[0].source_updated_at.isoformat().startswith("2026-10-06T18:00:19")


def test_reference_probs_preferred_then_consensus():
    pin = OddsQuote(4, "Pinnacle", 2.0, 3.6, 4.0)
    b365 = OddsQuote(8, "Bet365", 1.9, 3.4, 3.8)
    mp = reference_probs([b365, pin], [4])
    assert mp.source == "Pinnacle"
    assert (mp.home, mp.draw, mp.away) == pytest.approx(tuple(devig(pin.prices)))
    cons = reference_probs([b365, pin], [99])
    expected = (np.array(devig(pin.prices)) + np.array(devig(b365.prices))) / 2
    assert (cons.home, cons.draw, cons.away) == pytest.approx(tuple(expected))
    assert cons.source == "consenso (2 casas)"
    assert reference_probs([b365], [99], fallback="none") is None
    assert reference_probs([], [4]) is None


def test_summarize_known_values_and_verdicts():
    pairs = [
        PairedForecast("a", "L", (0.5, 0.3, 0.2), (0.6, 0.25, 0.15), 0),
        PairedForecast("b", "L", (0.2, 0.3, 0.5), (0.3, 0.3, 0.4), 2),
    ]
    s = summarize(pairs, bootstrap_samples=500)
    ll_model = (-np.log(0.5) - np.log(0.5)) / 2
    ll_market = (-np.log(0.6) - np.log(0.4)) / 2
    assert s.logloss_model == pytest.approx(ll_model)
    assert s.logloss_market == pytest.approx(ll_market)
    brier_a_model = 0.5**2 + 0.3**2 + 0.2**2
    brier_b_model = 0.2**2 + 0.3**2 + 0.5**2
    assert s.brier_model == pytest.approx((brier_a_model + brier_b_model) / 2)
    assert s.favorite_hit_model == 1.0 and s.model_better_share == 0.5
    lo, hi = s.logloss_diff_ci
    assert lo <= s.logloss_diff <= hi
    assert summarize([]) is None
    one = summarize(pairs[:1])
    assert verdict(one) == "Muestra insuficiente para concluir."
    md = "\n".join(to_markdown(s, "Modelo vs mercado"))
    assert "| Log-loss (menor es mejor) |" in md and "IC 95 %" in md


def test_market_probs_by_match_uses_config():
    cfg = MarketConfig(preferred_bookmakers=[8])
    quotes = {
        "f1": [OddsQuote(4, "Pinnacle", 2.0, 3.6, 4.0), OddsQuote(8, "Bet365", 1.9, 3.4, 3.8)]
    }
    assert market_probs_by_match(quotes, cfg)["f1"].source == "Bet365"


def test_backtest_includes_market_comparison(app_config):
    matches, _ = football_league(n_teams=12, rounds=4)
    tz = ZoneInfo("America/Bogota")
    start = (START + timedelta(days=60)).date()
    end = matches[-1].kickoff_utc.date()
    # Mercado sintético: favorece al resultado real con 50 % (informativo pero imperfecto)
    market = {}
    for m in matches:
        if m.kickoff_utc.date() >= start and m.home_goals is not None:
            y = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
            probs = [0.25, 0.25, 0.25]
            probs[y] = 0.5
            market[m.match_id] = MarketProbs(*probs, source="test", overround=0.05)
    rep = run_backtest(
        "football", matches, app_config, start, end, tz, refit_every_days=14, market=market
    )
    assert rep.market_available and len(rep.market_pairs) == rep.n_matches > 50
    models = {p.model for p in rep.predictions}
    assert {MARKET_MODEL, "ensemble_v1@mercado"} <= models
    s = summarize(rep.market_pairs, 200)
    assert s.logloss_market == pytest.approx(-np.log(0.5))  # siempre 50 % al resultado real
    md = rep.to_markdown("ensemble_v1")
    assert "Modelo vs mercado (1X2, mismos partidos)" in md
    # Sin mercado no aparece la sección
    plain = run_backtest("football", matches, app_config, start, end, tz, refit_every_days=14)
    assert "Modelo vs mercado" not in plain.to_markdown("ensemble_v1")
