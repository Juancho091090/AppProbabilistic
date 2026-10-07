"""Modelo Poisson de goles con fuerzas de ataque/defensa.

Para cada equipo T se estiman multiplicadores ``att_T`` y ``def_T`` (1.0 = media):

    λ_local     = μ_local     · att_local     · def_visitante
    λ_visitante = μ_visitante · att_visitante · def_local

* μ_local / μ_visitante: medias (ponderadas por recencia) de goles en casa / fuera.
  Así se modela la localía.
* Fuerza del rival: los multiplicadores se estiman de forma iterativa; los goles
  de T se comparan con lo esperado *contra ese rival concreto*.
* Recencia: cada partido pondera con ``features.recency``.
* Shrinkage: ``prior_strength`` partidos virtuales en la media (1.0) estabilizan
  equipos con pocos datos (ascendidos, inicio de temporada).

Todo se ajusta con partidos estrictamente anteriores a ``as_of``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.stats import poisson

from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.features.recency import recency_weights
from sports_analytics.models.football.league import LeagueEffects


@dataclass
class TeamStrength:
    attack: float = 1.0
    defense: float = 1.0
    weighted_matches: float = 0.0
    n_matches: int = 0


@dataclass
class PoissonGoalsModel:
    max_goals: int = 10
    min_matches: int = 6
    prior_strength: float = 5.0
    iterations: int = 15
    mu_home: float = 1.45
    mu_away: float = 1.15
    teams: dict[str, TeamStrength] = field(default_factory=dict)
    league: LeagueEffects | None = None  # medias por competición (None = globales)
    fitted_as_of: datetime | None = None
    n_train: int = 0

    @classmethod
    def from_config(cls, cfg: dict) -> PoissonGoalsModel:
        return cls(
            max_goals=cfg["max_goals"],
            min_matches=cfg["min_matches"],
            prior_strength=cfg["prior_strength"],
        )

    def fit(
        self,
        matches: Iterable[FootballMatch],
        as_of: datetime,
        recency_cfg: dict,
    ) -> PoissonGoalsModel:
        train = sorted(strictly_before(matches, as_of), key=lambda m: m.kickoff_utc)
        self.fitted_as_of = as_of
        self.n_train = len(train)
        self.teams = {}
        if not train:
            return self

        w = recency_weights([m.kickoff_utc for m in train], as_of, recency_cfg, "football")
        hg = np.array([m.home_goals for m in train], dtype=float)
        ag = np.array([m.away_goals for m in train], dtype=float)
        self.mu_home = float(np.average(hg, weights=w))
        self.mu_away = float(np.average(ag, weights=w))
        # Medias esperadas por partido: las de su competición si hay efectos por liga
        means = [self._means(m.competition_key) for m in train]
        muh = np.array([x[0] for x in means])
        mua = np.array([x[1] for x in means])

        names = sorted({m.home_team for m in train} | {m.away_team for m in train})
        idx = {t: i for i, t in enumerate(names)}
        hi = np.array([idx[m.home_team] for m in train])
        ai = np.array([idx[m.away_team] for m in train])
        n = len(names)
        att = np.ones(n)
        dfn = np.ones(n)
        k = self.prior_strength
        mu_bar = (self.mu_home + self.mu_away) / 2

        for _ in range(self.iterations):
            # Ataque: goles marcados / goles esperados contra la defensa rival
            scored = np.bincount(hi, w * hg, n) + np.bincount(ai, w * ag, n)
            exp_scored = np.bincount(hi, w * muh * dfn[ai], n) + np.bincount(
                ai, w * mua * dfn[hi], n
            )
            att = (scored + k * mu_bar) / (exp_scored + k * mu_bar)
            # Defensa: goles recibidos / esperados contra el ataque rival
            conceded = np.bincount(hi, w * ag, n) + np.bincount(ai, w * hg, n)
            exp_conceded = np.bincount(hi, w * mua * att[ai], n) + np.bincount(
                ai, w * muh * att[hi], n
            )
            dfn = (conceded + k * mu_bar) / (exp_conceded + k * mu_bar)
            # Normalización: media geométrica 1 (identificabilidad)
            att /= np.exp(np.mean(np.log(att)))
            dfn /= np.exp(np.mean(np.log(dfn)))

        weighted_n = np.bincount(hi, w, n) + np.bincount(ai, w, n)
        counts = np.bincount(hi, minlength=n) + np.bincount(ai, minlength=n)
        for t, i in idx.items():
            self.teams[t] = TeamStrength(
                float(att[i]), float(dfn[i]), float(weighted_n[i]), int(counts[i])
            )
        return self

    # ------------------------------------------------------------- predicción

    def strength(self, team: str) -> TeamStrength:
        return self.teams.get(team, TeamStrength())

    def has_enough_data(self, team: str) -> bool:
        return self.strength(team).n_matches >= self.min_matches

    def _means(self, competition: str | None) -> tuple[float, float]:
        if self.league is None:
            return self.mu_home, self.mu_away
        return self.league.goal_means(competition)

    def expected_goals(
        self, home: str, away: str, neutral: bool = False, competition: str | None = None
    ) -> tuple[float, float]:
        h, a = self.strength(home), self.strength(away)
        mu_h, mu_a = self._means(competition)
        if neutral:
            mu_h = mu_a = (mu_h + mu_a) / 2
        return mu_h * h.attack * a.defense, mu_a * a.attack * h.defense

    def score_matrix(self, lam_home: float, lam_away: float) -> np.ndarray:
        goals = np.arange(self.max_goals + 1)
        matrix = np.outer(poisson.pmf(goals, lam_home), poisson.pmf(goals, lam_away))
        return matrix / matrix.sum()

    def predict_matrix(
        self, home: str, away: str, neutral: bool = False, competition: str | None = None
    ) -> np.ndarray:
        return self.score_matrix(*self.expected_goals(home, away, neutral, competition))
