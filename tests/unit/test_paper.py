from datetime import UTC, datetime
from types import SimpleNamespace

from sports_analytics.market.odds import LineQuote, OddsQuote, devig, parse_line_odds
from sports_analytics.paper.registry import (
    LINE_RULES,
    RULES,
    Candidate,
    _line_outcome,
    reference_line_quote,
    reference_quote,
    selection_label,
    sheet_updates,
)


def _cand(fair_prices, model, conf="medium"):
    q = OddsQuote(4, "Ref", *fair_prices)
    f = SimpleNamespace(confidence=conf)
    return Candidate(f, q, tuple(devig(q.prices)), model)


def test_rules_select_expected_outcomes():
    c = _cand((1.4, 4.8, 8.0), (0.55, 0.25, 0.20), "high")
    assert RULES["mercado_favorito_60"][1](c) == [0]  # ~68 % sin margen
    assert RULES["modelo_alta"][1](c) == [0]
    assert RULES["mercado_favorito"][1](c) == [0]
    # valor esperado: 0.25*4.8=1.2 y 0.20*8=1.6 > 1; 0.55*1.4=0.77 < 1
    assert RULES["modelo_valor_esperado"][1](c) == [1, 2]
    even = _cand((2.6, 3.2, 2.9), (0.4, 0.3, 0.3))
    assert RULES["mercado_favorito_60"][1](even) == []
    assert RULES["modelo_alta"][1](even) == []


def test_reference_quote_prefers_configured_bookmaker():
    a, b = OddsQuote(8, "B", 2.0, 3.3, 3.6), OddsQuote(4, "P", 2.05, 3.4, 3.7)
    assert reference_quote([a, b], [4]).bookmaker_id == 4
    assert reference_quote([a], [4]).bookmaker_id == 8
    assert reference_quote([], [4]) is None


def test_sheet_update_block_covers_changed_rows_to_end():
    old = datetime(2026, 10, 1, tzinfo=UTC).isoformat()
    new = datetime(2026, 10, 26, tzinfo=UTC).isoformat()
    base = {
        "report_date": "2026-10-26",
        "rule": "R",
        "competition": "C",
        "match": "A vs B",
        "kickoff_local": "2026-10-27 13:00",
        "selection": "Local",
        "bookmaker": "P",
        "price_taken": 1.5,
        "fair_prob_taken": 0.64,
        "model_prob": 0.6,
        "confidence": "Alta",
        "price_close": None,
        "fair_prob_close": None,
        "result": "Pendiente",
    }
    rows = [
        {**base, "n": 1, "updated": old},
        {**base, "n": 2, "updated": new},
        {**base, "n": 3, "updated": old},
    ]
    blk = sheet_updates(rows, datetime(2026, 10, 25, tzinfo=UTC), "Registro simulado")
    assert blk["range"] == "'Registro simulado'!A3:R4"
    assert len(blk["formulas"]) == 2 and blk["formulas"][0][15].startswith("=IF(O3=")
    assert blk["formulas"][0][12] == ""  # sin precio de cierre todavía
    assert sheet_updates(rows, datetime(2026, 10, 27, tzinfo=UTC), "X") is None


def _forecast(goals_over=0.58, corners_over=0.40, btts=0.52):
    return SimpleNamespace(
        markets={
            "goals": {"goal_lines": {"2.5": {"over": goals_over}}, "btts": btts},
            "corners": {"lines": {"8.5": {"over": corners_over}}},
        }
    )


def test_line_rules_read_model_probabilities():
    f = _forecast()
    assert LINE_RULES["goles_2_5_modelo"].model_yes(f) == 0.58
    assert LINE_RULES["corners_8_5_modelo"].model_yes(f) == 0.40
    assert LINE_RULES["btts_modelo"].model_yes(f) == 0.52
    empty = SimpleNamespace(markets={"goals": {"goal_lines": {}}, "corners": None})
    assert all(r.model_yes(empty) is None for r in LINE_RULES.values())
    assert selection_label("corners_8_5_modelo", "no") == "Menos de 8.5 córners"
    assert selection_label("mercado_favorito", "home") == "Local"


def test_reference_line_quote_needs_exact_line():
    pin = LineQuote(4, "Pinnacle", "corners_total", "9.5", 1.8, 2.0)
    b365 = LineQuote(8, "Bet365", "corners_total", "8.5", 1.6, 2.2)
    other = LineQuote(3, "X", "corners_total", "8.5", 1.5, 2.3)
    assert reference_line_quote([pin], "corners_total", "8.5", [4]) is None
    # sin la preferida en esa línea: la de menor margen
    assert reference_line_quote([pin, b365, other], "corners_total", "8.5", [4]).bookmaker_id == 8


def test_line_outcome_from_result_and_statistics():
    m = SimpleNamespace(home_goals=2, away_goals=1)
    assert _line_outcome("goals_total", "2.5", m, None) is True
    assert _line_outcome("btts", "", m, None) is True
    assert _line_outcome("btts", "", SimpleNamespace(home_goals=2, away_goals=0), None) is False
    assert _line_outcome("corners_total", "8.5", m, None) is None  # aún sin estadísticas
    st = SimpleNamespace(home_corners=5, away_corners=3)
    assert _line_outcome("corners_total", "8.5", m, st) is False


def test_parse_line_odds_keeps_requested_lines_with_both_sides():
    def ou(x, o, u):
        return [{"value": f"Over {x}", "odd": o}, {"value": f"Under {x}", "odd": u}]

    items = [
        {
            "update": "2026-10-29T10:00:00+00:00",
            "bookmakers": [
                {
                    "id": 4,
                    "name": "Pinnacle",
                    "bets": [
                        {"id": 5, "values": ou("2.5", "1.41", "2.99") + ou("2.25", "1.29", "3.78")},
                        {"id": 8, "values": [{"value": "Yes", "odd": "1.4"}]},  # falta "No"
                        {"id": 45, "values": ou("8.5", "1.50", "2.52")},
                    ],
                }
            ],
        }
    ]
    lines = {"goals_total": ["2.5"], "btts": [""], "corners_total": ["8.5"]}
    got = {(q.market, q.line): q.prices for q in parse_line_odds(items, lines)}
    assert got == {("goals_total", "2.5"): (1.41, 2.99), ("corners_total", "8.5"): (1.5, 2.52)}
