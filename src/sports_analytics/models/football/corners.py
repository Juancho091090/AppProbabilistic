"""Modelo de córners totales.

Media esperada (multiplicativa, igual que goles):

    E[córners_local]     = μc_local     · gen_local     · conc_visitante
    E[córners_visitante] = μc_visitante · gen_visitante · conc_local

* gen_T: córners generados por T respecto a lo esperado (ataque / ritmo ofensivo).
* conc_T: córners concedidos por T respecto a lo esperado (rendimiento defensivo).
* Localía vía μc_local / μc_visitante; rival vía estimación iterativa; recencia y
  shrinkage como en el modelo de goles.

Distribución del total: se mide la sobredispersión sobre el histórico
(var/mean de los residuos respecto a la media ajustada). Si supera
``overdispersion_threshold`` se usa una Binomial Negativa con
Var = μ + μ²/r (r por método de momentos); si no, Poisson.

Pendiente (requiere datos reales): regresión NB con tiros y posesión previos al
partido como covariables adicionales. Ver docs/models.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.stats import nbinom, poisson

from sports_analytics.data.schemas import FootballMatch, strictly_before
from sports_analytics.features.recency import recency_weights
from sports_analytics.models.distributions import expected_value, over_under, pmf_to_dict


def negbin_pmf(mean: float, r: float, support: np.ndarray) -> np.ndarray:
    """NB parametrizada por media y tamaño r: p = r / (r + μ)."""
    p = r / (r + mean)
    return nbinom.pmf(support, r, p)


def estimate_dispersion(
    observed: np.ndarray, expected: np.ndarray, weights: np.ndarray
) -> tuple[float, float | None]:
    """Devuelve (ratio var/mean, r de la NB o None si no hay sobredispersión).

    Método de momentos sobre residuos: Var(Y) = μ + μ²/r  =>
    1/r = Σw[(y-μ)² - μ] / Σw μ².
    """
    resid_sq = (observed - expected) ** 2
    ratio = float(np.sum(weights * resid_sq) / np.sum(weights * expected))
    inv_r = float(np.sum(weights * (resid_sq - expected)) / np.sum(weights * expected**2))
    if inv_r <= 0:
        return ratio, None
    return ratio, 1.0 / inv_r


@dataclass
class CornersModel:
    max_corners: int = 30
    overdispersion_threshold: float = 1.10
    min_matches: int = 6
    prior_strength: float = 5.0
    iterations: int = 15
    mu_home: float = 5.3
    mu_away: float = 4.4
    generate: dict[str, float] = field(default_factory=dict)
    concede: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    dispersion_ratio: float = 1.0
    nb_size: float | None = None
    n_train: int = 0

    @classmethod
    def from_config(cls, cfg: dict, prior_strength: float = 5.0) -> CornersModel:
        return cls(
            max_corners=cfg["max_corners"],
            overdispersion_threshold=cfg["overdispersion_threshold"],
            min_matches=cfg["min_matches"],
            prior_strength=prior_strength,
        )

    @property
    def distribution(self) -> str:
        if self.nb_size is not None and self.dispersion_ratio > self.overdispersion_threshold:
            return "negative_binomial"
        return "poisson"

    def fit(
        self, matches: Iterable[FootballMatch], as_of: datetime, recency_cfg: dict
    ) -> CornersModel:
        train = [m for m in strictly_before(matches, as_of) if m.has_corners]
        self.n_train = len(train)
        self.generate, self.concede, self.counts = {}, {}, {}
        if not train:
            return self
        w = recency_weights([m.kickoff_utc for m in train], as_of, recency_cfg, "football")
        hc = np.array([m.home_corners for m in train], float)
        ac = np.array([m.away_corners for m in train], float)
        self.mu_home = float(np.average(hc, weights=w))
        self.mu_away = float(np.average(ac, weights=w))

        names = sorted({m.home_team for m in train} | {m.away_team for m in train})
        idx = {t: i for i, t in enumerate(names)}
        hi = np.array([idx[m.home_team] for m in train])
        ai = np.array([idx[m.away_team] for m in train])
        n, k = len(names), self.prior_strength
        mu_bar = (self.mu_home + self.mu_away) / 2
        gen, con = np.ones(n), np.ones(n)
        for _ in range(self.iterations):
            made = np.bincount(hi, w * hc, n) + np.bincount(ai, w * ac, n)
            exp_made = np.bincount(hi, w * self.mu_home * con[ai], n) + np.bincount(
                ai, w * self.mu_away * con[hi], n
            )
            gen = (made + k * mu_bar) / (exp_made + k * mu_bar)
            allowed = np.bincount(hi, w * ac, n) + np.bincount(ai, w * hc, n)
            exp_allowed = np.bincount(hi, w * self.mu_away * gen[ai], n) + np.bincount(
                ai, w * self.mu_home * gen[hi], n
            )
            con = (allowed + k * mu_bar) / (exp_allowed + k * mu_bar)
            gen /= np.exp(np.mean(np.log(gen)))
            con /= np.exp(np.mean(np.log(con)))

        counts = np.bincount(hi, minlength=n) + np.bincount(ai, minlength=n)
        for t, i in idx.items():
            self.generate[t], self.concede[t] = float(gen[i]), float(con[i])
            self.counts[t] = int(counts[i])

        expected_total = self.mu_home * gen[hi] * con[ai] + self.mu_away * gen[ai] * con[hi]
        self.dispersion_ratio, self.nb_size = estimate_dispersion(hc + ac, expected_total, w)
        return self

    def has_enough_data(self, team: str) -> bool:
        return self.counts.get(team, 0) >= self.min_matches

    def expected_corners(self, home: str, away: str) -> tuple[float, float]:
        g, c = self.generate, self.concede
        return (
            self.mu_home * g.get(home, 1.0) * c.get(away, 1.0),
            self.mu_away * g.get(away, 1.0) * c.get(home, 1.0),
        )

    def total_pmf(self, home: str, away: str) -> np.ndarray:
        mean = sum(self.expected_corners(home, away))
        support = np.arange(self.max_corners + 1)
        if self.distribution == "negative_binomial":
            pmf = negbin_pmf(mean, self.nb_size, support)
        else:
            pmf = poisson.pmf(support, mean)
        return pmf / pmf.sum()

    def predict(self, home: str, away: str, lines: Iterable[float]) -> dict:
        h, a = self.expected_corners(home, away)
        pmf = self.total_pmf(home, away)
        return {
            "expected": {"home": h, "away": a, "total": expected_value(pmf)},
            "distribution_family": self.distribution,
            "dispersion_ratio": self.dispersion_ratio,
            "total_distribution": pmf_to_dict(pmf),
            "lines": over_under(pmf, lines),
        }
