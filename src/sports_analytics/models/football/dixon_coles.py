"""Ajuste Dixon-Coles (1997) para marcadores bajos.

El modelo Poisson independiente subestima 0-0 y 1-1 y sobreestima 1-0 / 0-1.
Dixon y Coles multiplican esas cuatro celdas por τ(x, y; λ, μ, ρ):

    τ(0,0) = 1 − λμρ      τ(0,1) = 1 + λρ
    τ(1,0) = 1 + μρ       τ(1,1) = 1 − ρ

ρ se estima por máxima verosimilitud (ponderada por recencia) usando las λ del
modelo Poisson ajustado con partidos anteriores a ``as_of``. Si no hay suficientes
partidos (``min_matches_fit``) se usa ``rho_default``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import poisson

from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.features.recency import recency_weights
from sports_analytics.models.football.poisson import PoissonGoalsModel


def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def rho_feasible_bounds(lam: float, mu: float) -> tuple[float, float]:
    """Rango de ρ que mantiene las cuatro τ no negativas."""
    lower = max(-1 / lam if lam > 0 else -np.inf, -1 / mu if mu > 0 else -np.inf)
    upper = min(1 / (lam * mu) if lam * mu > 0 else np.inf, 1.0)
    return lower, upper


def dixon_coles_matrix(lam: float, mu: float, rho: float, max_goals: int = 10) -> np.ndarray:
    lo, hi = rho_feasible_bounds(lam, mu)
    rho = float(np.clip(rho, lo, hi))
    goals = np.arange(max_goals + 1)
    matrix = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
    for x in (0, 1):
        for y in (0, 1):
            matrix[x, y] *= tau(x, y, lam, mu, rho)
    matrix = np.clip(matrix, 0, None)
    return matrix / matrix.sum()


@dataclass
class DixonColesModel:
    base: PoissonGoalsModel
    rho: float = -0.10
    rho_default: float = -0.10
    rho_bounds: tuple[float, float] = (-0.20, 0.05)
    min_matches_fit: int = 150
    rho_estimated: bool = False

    @classmethod
    def from_config(cls, base: PoissonGoalsModel, cfg: dict) -> DixonColesModel:
        return cls(
            base=base,
            rho=cfg["rho_default"],
            rho_default=cfg["rho_default"],
            rho_bounds=tuple(cfg["rho_bounds"]),
            min_matches_fit=cfg["min_matches_fit"],
        )

    def fit_rho(
        self, matches: Iterable[FootballMatch], as_of: datetime, recency_cfg: dict
    ) -> DixonColesModel:
        """Estima ρ por MLE. Requiere que ``base`` esté ajustado con el mismo ``as_of``."""
        if self.base.fitted_as_of != as_of:
            raise ValueError("El modelo Poisson base debe ajustarse con el mismo as_of")
        train = strictly_before(matches, as_of)
        low_scores = [m for m in train if m.home_goals <= 1 and m.away_goals <= 1]
        if len(train) < self.min_matches_fit or not low_scores:
            self.rho, self.rho_estimated = self.rho_default, False
            return self

        w = recency_weights([m.kickoff_utc for m in train], as_of, recency_cfg, "football")
        lams = [self.base.expected_goals(m.home_team, m.away_team, m.neutral_venue) for m in train]

        def neg_loglik(rho: float) -> float:
            # Solo las celdas 0/1 dependen de ρ, más la constante de normalización
            total = 0.0
            for (lam, mu), m, wi in zip(lams, train, w, strict=True):
                t = tau(m.home_goals, m.away_goals, lam, mu, rho)
                if t <= 0:
                    return 1e12
                total += wi * np.log(t)
            return -total

        res = minimize_scalar(neg_loglik, bounds=self.rho_bounds, method="bounded")
        self.rho = float(res.x) if res.success else self.rho_default
        self.rho_estimated = bool(res.success)
        return self

    def predict_matrix(self, home: str, away: str, neutral: bool = False) -> np.ndarray:
        lam, mu = self.base.expected_goals(home, away, neutral)
        return dixon_coles_matrix(lam, mu, self.rho, self.base.max_goals)
