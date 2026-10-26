from datetime import UTC, datetime
from types import SimpleNamespace

from sports_analytics.market.odds import OddsQuote, devig
from sports_analytics.paper.registry import RULES, Candidate, reference_quote, sheet_updates


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
