import numpy as np
import pandas as pd
import pytest

from sports_analytics.market import history as h

MAIN_CSV = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA,MaxCH,MaxCD,MaxCA,PC>2.5,PC<2.5,AvgC>2.5,AvgC<2.5,MaxC>2.5,MaxC<2.5
E0,10/08/2024,A,B,2,0,H,1.50,4.40,7.00,1.48,4.30,6.50,1.55,4.60,7.50,1.90,1.98,1.85,1.95,1.95,2.05
E0,10/08/2024,C,D,1,1,D,2.50,3.30,3.00,2.40,3.20,2.90,2.60,3.40,3.10,2.10,1.80,2.05,1.75,2.15,1.85
E0,11/08/2024,E,F,0,3,A,1.40,5.00,8.50,,,,,,,2.00,1.88,,,,
"""

EXTRA_CSV = """Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA
Brazil,Serie A,2023,15/04/2023,20:00,X,Y,1,0,H,1.80,3.60,4.80,1.85,3.70,5.00,1.75,3.50,4.60
Brazil,Serie A,2012/2013,15/04/2013,20:00,X,Y,,,,1.80,3.60,4.80,,,,,,
"""


def test_season_codes():
    assert h.season_codes(2012, 2014) == ["1213", "1314", "1415"]
    assert h.season_codes(2025, 2025) == ["2526"]


def test_parse_main_and_extra():
    df = h.parse_main_csv(MAIN_CSV, "E0", "2425")
    assert len(df) == 3 and df.league.iloc[0] == "Inglaterra · Premier League"
    assert df.season.iloc[0] == "2024/25" and df.ps_over.iloc[0] == 1.90
    assert np.isnan(df.avg_h.iloc[2])
    ex = h.parse_extra_csv(EXTRA_CSV, "BRA")
    assert len(ex) == 1 and ex.season.iloc[0] == "2023" and np.isnan(ex.ps_over.iloc[0])


def test_devig_methods_sum_to_one_and_power_favours_favourite():
    odds = np.array([[1.50, 4.40, 7.00], [2.5, 3.3, 3.0], [np.nan, 3.0, 3.0]])
    prop = h.devig_matrix(odds, "proportional")
    powr = h.devig_matrix(odds, "power")
    assert np.allclose(prop[:2].sum(axis=1), 1) and np.allclose(powr[:2].sum(axis=1), 1)
    assert powr[0, 0] > prop[0, 0]  # la potencia carga el margen en el longshot
    assert np.isnan(prop[2]).all()


def test_rule_stats_and_analyze():
    df = h.parse_main_csv(MAIN_CSV, "E0", "2425")
    prep = h.prepare(df)
    st = h.rule_stats(prep, h.HIST_RULES["fav_all"])
    # favoritos: A (gana), C 2.50 (empate: falla), E (pierde)
    assert st["n"] == 3 and st["hit_rate"] == pytest.approx(1 / 3)
    assert st["roi"] == pytest.approx((0.5 - 1 - 1) / 3)
    assert h.rule_stats(prep, h.HIST_RULES["fav_all"], source="avg")["n"] == 2
    under = h.rule_stats(prep, h.HIST_RULES["under"])
    assert under["n"] == 3 and under["hit_rate"] == pytest.approx(2 / 3)
    res = h.analyze(pd.concat([df, h.parse_extra_csv(EXTRA_CSV, "BRA")]), recent_from="2023")
    assert res["matches"] == 4 and len(res["leagues"]) == 2
    md = h.to_markdown(res)
    assert "Favorito del mercado (todos)" in md and "por liga" in md


def test_download_history_collects_missing():
    calls = []

    def get(url):
        calls.append(url)
        return MAIN_CSV if url.endswith("/2425/E0.csv") else None

    df, missing = h.download_history(get, ["2425"], main=["E0", "E1"], extra=["BRA"], pause=0)
    assert len(df) == 3 and len(missing) == 2 and len(calls) == 3
