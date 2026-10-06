"""Features de fútbol previas al partido, construidas cronológicamente.

``build_training_set`` recorre los partidos en orden; para cada uno calcula las
features con el estado *antes* del partido y solo después actualiza el estado.
Esto garantiza ausencia de data leakage por construcción.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sports_analytics.core.timeutils import ensure_utc
from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.models.football.elo import FootballElo

FEATURE_NAMES = [
    "elo_diff",
    "home_gf",
    "home_ga",
    "away_gf",
    "away_ga",
    "home_form",
    "away_form",
]

OUTCOME_INDEX = {"home": 0, "draw": 1, "away": 2}


def outcome_label(match: FootballMatch) -> int:
    if match.home_goals > match.away_goals:
        return OUTCOME_INDEX["home"]
    if match.home_goals == match.away_goals:
        return OUTCOME_INDEX["draw"]
    return OUTCOME_INDEX["away"]


@dataclass
class _TeamHistory:
    dates: list[datetime] = field(default_factory=list)
    gf: list[int] = field(default_factory=list)
    ga: list[int] = field(default_factory=list)
    pts: list[int] = field(default_factory=list)


@dataclass
class FootballFeatureState:
    elo: FootballElo
    half_life_days: float = 180.0
    history: dict[str, _TeamHistory] = field(default_factory=lambda: defaultdict(_TeamHistory))

    def _weighted(self, team: str, as_of: datetime) -> tuple[float, float, float]:
        h = self.history.get(team)
        if not h or not h.dates:
            return np.nan, np.nan, np.nan
        ages = np.array([(as_of - d).total_seconds() / 86400 for d in h.dates])
        w = 0.5 ** (ages / self.half_life_days)
        return (
            float(np.average(h.gf, weights=w)),
            float(np.average(h.ga, weights=w)),
            float(np.average(h.pts, weights=w)),
        )

    def features(self, home: str, away: str, as_of: datetime, neutral: bool = False) -> list[float]:
        as_of = ensure_utc(as_of)
        hfa = 0.0 if neutral else self.elo.home_advantage
        elo_diff = self.elo.rating(home) + hfa - self.elo.rating(away)
        hgf, hga, hform = self._weighted(home, as_of)
        agf, aga, aform = self._weighted(away, as_of)
        return [elo_diff, hgf, hga, agf, aga, hform, aform]

    def update(self, m: FootballMatch) -> None:
        self.elo.update(m)
        hp = 3 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 0
        ap = 3 if hp == 0 else 1 if hp == 1 else 0
        for team, gf, ga, pts in (
            (m.home_team, m.home_goals, m.away_goals, hp),
            (m.away_team, m.away_goals, m.home_goals, ap),
        ):
            hist = self.history[team]
            hist.dates.append(m.kickoff_utc)
            hist.gf.append(gf)
            hist.ga.append(ga)
            hist.pts.append(pts)


def build_training_set(
    matches: Iterable[FootballMatch],
    as_of: datetime,
    elo: FootballElo,
    half_life_days: float,
    min_history: int = 3,
) -> tuple[np.ndarray, np.ndarray, FootballFeatureState]:
    """Devuelve (X, y, estado final). El estado final sirve para predecir partidos >= as_of."""
    state = FootballFeatureState(elo=elo, half_life_days=half_life_days)
    elo.ratings.clear()
    elo.games_played.clear()
    elo.last_played.clear()
    rows, labels = [], []
    for m in sorted(strictly_before(matches, as_of), key=lambda m: m.kickoff_utc):
        enough = (
            len(state.history[m.home_team].dates) >= min_history
            and len(state.history[m.away_team].dates) >= min_history
        )
        if enough:
            rows.append(state.features(m.home_team, m.away_team, m.kickoff_utc, m.neutral_venue))
            labels.append(outcome_label(m))
        state.update(m)
    elo.fitted_as_of = as_of
    x = np.array(rows, dtype=float).reshape(-1, len(FEATURE_NAMES))
    return x, np.array(labels, dtype=int), state
