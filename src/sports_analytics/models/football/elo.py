"""Elo de fútbol (variante World Football Elo).

* Expectativa: We = 1 / (1 + 10 ** (-(R_home + H - R_away) / 400)).
* Actualización: R += K * G * (W - We), con G multiplicador por diferencia de goles.
* Recencia: inherente al Elo (cada partido mueve el rating); además se aplica una
  regresión a la media tras inactividad prolongada (cambio de temporada).
* 1X2: P(empate) decrece con |diferencia Elo|; P(local) y P(visitante) se reparten
  de forma que P(L) + 0.5·P(E) = We (coherente con la expectativa Elo).

Los ratings se reconstruyen secuencialmente con partidos anteriores a ``as_of``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.models.football.league import LeagueEffects


def goal_diff_multiplier(goal_diff: int) -> float:
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8


@dataclass
class FootballElo:
    initial_rating: float = 1500.0
    k_factor: float = 20.0
    home_advantage: float = 65.0
    goal_diff_multiplier: bool = True
    season_regression: float = 0.20
    season_gap_days: float = 45.0
    base_draw: float = 0.27
    draw_decay: float = 0.0008
    league: LeagueEffects | None = None  # escala la ventaja de local por competición
    ratings: dict[str, float] = field(default_factory=dict)
    games_played: dict[str, int] = field(default_factory=dict)
    last_played: dict[str, datetime] = field(default_factory=dict)
    fitted_as_of: datetime | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> FootballElo:
        draw = cfg.get("draw_model", {})
        return cls(
            initial_rating=cfg["initial_rating"],
            k_factor=cfg["k_factor"],
            home_advantage=cfg["home_advantage"],
            goal_diff_multiplier=cfg.get("goal_diff_multiplier", True),
            season_regression=cfg.get("season_regression", 0.0),
            season_gap_days=cfg.get("season_gap_days", 45.0),
            base_draw=draw.get("base_draw", 0.27),
            draw_decay=draw.get("draw_decay", 0.0008),
        )

    # ------------------------------------------------------------------ core

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def hfa(self, neutral: bool = False, competition: str | None = None) -> float:
        if neutral:
            return 0.0
        ratio = self.league.hfa_ratio(competition) if self.league is not None else 1.0
        return self.home_advantage * ratio

    def expected_home(
        self, home: str, away: str, neutral: bool = False, competition: str | None = None
    ) -> float:
        hfa = self.hfa(neutral, competition)
        diff = self.rating(home) + hfa - self.rating(away)
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def _maybe_regress(self, team: str, when: datetime) -> None:
        last = self.last_played.get(team)
        if last and (when - last).days > self.season_gap_days and self.season_regression > 0:
            r = self.rating(team)
            self.ratings[team] = r + self.season_regression * (self.initial_rating - r)

    def update(self, match: FootballMatch) -> None:
        if not match.is_finished:
            raise ValueError("Solo se actualiza con partidos finalizados")
        for team in (match.home_team, match.away_team):
            self._maybe_regress(team, match.kickoff_utc)
        we = self.expected_home(
            match.home_team, match.away_team, match.neutral_venue, match.competition_key
        )
        gd = match.home_goals - match.away_goals
        w = 1.0 if gd > 0 else 0.5 if gd == 0 else 0.0
        g = goal_diff_multiplier(gd) if self.goal_diff_multiplier else 1.0
        delta = self.k_factor * g * (w - we)
        self.ratings[match.home_team] = self.rating(match.home_team) + delta
        self.ratings[match.away_team] = self.rating(match.away_team) - delta
        for team in (match.home_team, match.away_team):
            self.games_played[team] = self.games_played.get(team, 0) + 1
            self.last_played[team] = match.kickoff_utc

    def fit(self, matches: Iterable[FootballMatch], as_of: datetime) -> FootballElo:
        """Reconstruye ratings con partidos estrictamente anteriores a ``as_of``."""
        self.ratings.clear()
        self.games_played.clear()
        self.last_played.clear()
        for match in sorted(strictly_before(matches, as_of), key=lambda m: m.kickoff_utc):
            self.update(match)
        self.fitted_as_of = as_of
        return self

    # ------------------------------------------------------------- predicción

    def draw_probability(self, elo_diff: float) -> float:
        return self.base_draw * math.exp(-self.draw_decay * abs(elo_diff))

    def predict_1x2(
        self, home: str, away: str, neutral: bool = False, competition: str | None = None
    ) -> dict[str, float]:
        hfa = self.hfa(neutral, competition)
        diff = self.rating(home) + hfa - self.rating(away)
        we = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        p_draw = min(self.draw_probability(diff), 2 * min(we, 1 - we))
        p_home = we - 0.5 * p_draw
        p_away = 1.0 - p_home - p_draw
        return {"home": p_home, "draw": p_draw, "away": max(p_away, 0.0)}
