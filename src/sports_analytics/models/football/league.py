"""Efectos por competición: nivel de goles y ventaja de local de cada liga.

Con un solo μ_local/μ_visitante global, una liga de pocos goles (o con poca ventaja de
local, como las selecciones en la Nations League) hereda los valores medios. Aquí:

    μ_local_c  = (Σ w·goles_local_c  + k·μ_local_global)  / (Σ w_c + k)
    μ_visit_c  = (Σ w·goles_visit_c  + k·μ_visit_global)  / (Σ w_c + k)

``k`` (prior_matches) son partidos "virtuales" de la media global: una liga con pocos
datos queda cerca de la media (shrinkage). La ventaja de local relativa de la liga es

    r_c = ln(μ_local_c / μ_visit_c) / ln(μ_local_global / μ_visit_global)

y escala la ventaja de local del Elo. Solo usa partidos anteriores a ``as_of``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.features.recency import recency_weights


@dataclass
class LeagueEffects:
    global_home: float = 1.45
    global_away: float = 1.15
    home: dict[str, float] = field(default_factory=dict)
    away: dict[str, float] = field(default_factory=dict)
    weight: dict[str, float] = field(default_factory=dict)
    hfa_ratio_bounds: tuple[float, float] = (0.0, 2.0)

    def goal_means(self, competition: str | None) -> tuple[float, float]:
        if competition is None or competition not in self.home:
            return self.global_home, self.global_away
        return self.home[competition], self.away[competition]

    def hfa_ratio(self, competition: str | None) -> float:
        mh, ma = self.goal_means(competition)
        g = math.log(self.global_home / self.global_away)
        if g <= 0:
            return 1.0
        lo, hi = self.hfa_ratio_bounds
        return float(min(max(math.log(mh / ma) / g, lo), hi))


def fit_league_effects(
    matches: Iterable[FootballMatch],
    as_of: datetime,
    recency_cfg: dict,
    prior_matches: float,
    hfa_ratio_bounds: tuple[float, float] = (0.0, 2.0),
) -> LeagueEffects:
    train = list(strictly_before(matches, as_of))
    if not train:
        return LeagueEffects(hfa_ratio_bounds=hfa_ratio_bounds)
    w = recency_weights([m.kickoff_utc for m in train], as_of, recency_cfg, "football")
    hg = np.array([m.home_goals for m in train], dtype=float)
    ag = np.array([m.away_goals for m in train], dtype=float)
    gh, ga = float(np.average(hg, weights=w)), float(np.average(ag, weights=w))
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for m, wi, h, a in zip(train, w, hg, ag, strict=True):
        s = sums[m.competition_key]
        s[0] += wi * h
        s[1] += wi * a
        s[2] += wi
    k = prior_matches
    eff = LeagueEffects(gh, ga, hfa_ratio_bounds=hfa_ratio_bounds)
    for comp, (sh, sa, sw) in sums.items():
        eff.home[comp] = (sh + k * gh) / (sw + k)
        eff.away[comp] = (sa + k * ga) / (sw + k)
        eff.weight[comp] = sw
    return eff
