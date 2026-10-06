"""Features de tenis previas al partido, construidas cronológicamente.

Igual que en fútbol: las features de cada partido se calculan con el estado
anterior y luego se actualiza el estado (sin leakage por construcción).

Sesgo de orientación: muchos proveedores listan siempre al ganador como jugador A.
Para que la regresión logística no aprenda "A siempre gana", en entrenamiento se
invierte la orientación de forma determinista (hash del match_id) en ~50% de filas.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sports_analytics.data.schemas import TennisMatch, strictly_before
from sports_analytics.models.tennis.elo import TennisElo

FEATURE_NAMES = [
    "elo_diff",
    "surface_elo_diff",
    "log_rank_ratio",
    "form_diff",
    "spw_diff",
    "rpw_diff",
]


@dataclass
class _PlayerHistory:
    dates: list[datetime] = field(default_factory=list)
    won: list[float] = field(default_factory=list)
    spw: list[float] = field(default_factory=list)
    spw_dates: list[datetime] = field(default_factory=list)
    rpw: list[float] = field(default_factory=list)
    rpw_dates: list[datetime] = field(default_factory=list)
    last_rank: int | None = None


def _wavg(values: list[float], dates: list[datetime], as_of: datetime, hl: float) -> float:
    if not values:
        return math.nan
    ages = np.array([(as_of - d).total_seconds() / 86400 for d in dates])
    w = 0.5 ** (ages / hl)
    return float(np.average(values, weights=w))


def _flip(match_id: str) -> bool:
    return hashlib.sha256(match_id.encode()).digest()[0] % 2 == 1


@dataclass
class TennisFeatureState:
    elo: TennisElo
    half_life_days: float = 120.0
    history: dict[str, _PlayerHistory] = field(default_factory=lambda: defaultdict(_PlayerHistory))

    def player_stats(self, player: str, as_of: datetime) -> dict[str, float]:
        h = self.history.get(player) or _PlayerHistory()
        return {
            "form": _wavg(h.won, h.dates, as_of, self.half_life_days),
            "spw": _wavg(h.spw, h.spw_dates, as_of, self.half_life_days),
            "rpw": _wavg(h.rpw, h.rpw_dates, as_of, self.half_life_days),
            "n": len(h.dates),
            "n_stats": len(h.spw),
        }

    def features(
        self,
        a: str,
        b: str,
        surface: str,
        as_of: datetime,
        rank_a: int | None = None,
        rank_b: int | None = None,
    ) -> list[float]:
        sa, sb = self.player_stats(a, as_of), self.player_stats(b, as_of)
        rank_a = rank_a or (self.history.get(a) or _PlayerHistory()).last_rank
        rank_b = rank_b or (self.history.get(b) or _PlayerHistory()).last_rank

        def diff(x: float, y: float) -> float:
            return 0.0 if math.isnan(x) or math.isnan(y) else x - y

        log_rank = math.log(rank_b / rank_a) if rank_a and rank_b else 0.0
        return [
            self.elo.rating(a) - self.elo.rating(b),
            self.elo.effective_surface_rating(a, surface)
            - self.elo.effective_surface_rating(b, surface),
            log_rank,
            diff(sa["form"], sb["form"]),
            diff(sa["spw"], sb["spw"]),
            diff(sa["rpw"], sb["rpw"]),
        ]

    def update(self, m: TennisMatch) -> None:
        self.elo.update(m)
        for player, won, spw, rpw, rank in (
            (m.player_a, m.winner == "A", m.a_spw, m.a_rpw, m.rank_a),
            (m.player_b, m.winner == "B", m.b_spw, m.b_rpw, m.rank_b),
        ):
            h = self.history[player]
            if not m.retired:
                h.dates.append(m.kickoff_utc)
                h.won.append(1.0 if won else 0.0)
            if spw is not None:
                h.spw.append(spw)
                h.spw_dates.append(m.kickoff_utc)
            if rpw is not None:
                h.rpw.append(rpw)
                h.rpw_dates.append(m.kickoff_utc)
            if rank:
                h.last_rank = rank


def build_training_set(
    matches: Iterable[TennisMatch],
    as_of: datetime,
    elo: TennisElo,
    half_life_days: float,
    min_history: int = 5,
) -> tuple[np.ndarray, np.ndarray, TennisFeatureState]:
    elo.fit([], as_of)  # reinicia ratings
    state = TennisFeatureState(elo=elo, half_life_days=half_life_days)
    rows, labels = [], []
    for m in sorted(strictly_before(matches, as_of), key=lambda m: m.kickoff_utc):
        enough = (
            len(state.history[m.player_a].dates) >= min_history
            and len(state.history[m.player_b].dates) >= min_history
        )
        if enough and not m.retired:
            if _flip(m.match_id):
                x = state.features(
                    m.player_b, m.player_a, m.surface, m.kickoff_utc, m.rank_b, m.rank_a
                )
                y = 1 if m.winner == "B" else 0
            else:
                x = state.features(
                    m.player_a, m.player_b, m.surface, m.kickoff_utc, m.rank_a, m.rank_b
                )
                y = 1 if m.winner == "A" else 0
            rows.append(x)
            labels.append(y)
        state.update(m)
    elo.fitted_as_of = as_of
    x = np.array(rows, dtype=float).reshape(-1, len(FEATURE_NAMES))
    return x, np.array(labels, dtype=int), state
