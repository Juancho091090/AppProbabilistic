"""Elo de tenis general y por superficie (esquema FiveThirtyEight).

* K dinámico: K = k_base / (partidos_jugados + k_offset) ** k_shape. Jugadores
  nuevos se mueven rápido; veteranos, lento.
* Se mantienen ratings separados para hard / clay / grass.
* Elo de superficie efectivo = mezcla n_s/(n_s+prior) del rating de superficie con
  el general, para no confiar en superficies con pocos partidos.
* Partidos con retiro: se ignoran para el rating (no informan fuerza real).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sports_analytics.data.schemas import TennisMatch, strictly_before

SURFACES = ("hard", "clay", "grass")


def elo_probability(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


@dataclass
class TennisElo:
    initial_rating: float = 1500.0
    k_base: float = 250.0
    k_offset: float = 5.0
    k_shape: float = 0.4
    surface_min_matches: int = 10
    surface_blend_prior: float = 15.0
    overall: dict[str, float] = field(default_factory=dict)
    surface: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    n_overall: dict[str, int] = field(default_factory=dict)
    n_surface: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    fitted_as_of: datetime | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> TennisElo:
        return cls(
            initial_rating=cfg["initial_rating"],
            k_base=cfg["k_base"],
            k_offset=cfg["k_offset"],
            k_shape=cfg["k_shape"],
            surface_min_matches=cfg["surface_min_matches"],
            surface_blend_prior=cfg["surface_blend_prior"],
        )

    def _k(self, n: int) -> float:
        return self.k_base / (n + self.k_offset) ** self.k_shape

    def rating(self, player: str) -> float:
        return self.overall.get(player, self.initial_rating)

    def surface_rating(self, player: str, surface: str) -> float:
        return self.surface[surface].get(player, self.initial_rating)

    def matches_played(self, player: str, surface: str | None = None) -> int:
        if surface is None:
            return self.n_overall.get(player, 0)
        return self.n_surface[surface].get(player, 0)

    def effective_surface_rating(self, player: str, surface: str) -> float:
        if surface not in SURFACES:
            return self.rating(player)
        n = self.matches_played(player, surface)
        w = n / (n + self.surface_blend_prior)
        return w * self.surface_rating(player, surface) + (1 - w) * self.rating(player)

    def update(self, m: TennisMatch) -> None:
        if not m.is_finished or m.retired:
            return
        a, b = m.player_a, m.player_b
        score_a = 1.0 if m.winner == "A" else 0.0

        pa = elo_probability(self.rating(a), self.rating(b))
        ka, kb = self._k(self.matches_played(a)), self._k(self.matches_played(b))
        self.overall[a] = self.rating(a) + ka * (score_a - pa)
        self.overall[b] = self.rating(b) + kb * ((1 - score_a) - (1 - pa))
        self.n_overall[a] = self.matches_played(a) + 1
        self.n_overall[b] = self.matches_played(b) + 1

        s = m.surface
        if s in SURFACES:
            ps = elo_probability(self.surface_rating(a, s), self.surface_rating(b, s))
            ksa, ksb = self._k(self.matches_played(a, s)), self._k(self.matches_played(b, s))
            self.surface[s][a] = self.surface_rating(a, s) + ksa * (score_a - ps)
            self.surface[s][b] = self.surface_rating(b, s) + ksb * ((1 - score_a) - (1 - ps))
            self.n_surface[s][a] = self.matches_played(a, s) + 1
            self.n_surface[s][b] = self.matches_played(b, s) + 1

    def fit(self, matches: Iterable[TennisMatch], as_of: datetime) -> TennisElo:
        self.overall.clear()
        self.n_overall.clear()
        self.surface = defaultdict(dict)
        self.n_surface = defaultdict(dict)
        for m in sorted(strictly_before(matches, as_of), key=lambda m: m.kickoff_utc):
            self.update(m)
        self.fitted_as_of = as_of
        return self

    def predict(self, a: str, b: str, surface: str) -> dict[str, float]:
        return {
            "elo": elo_probability(self.rating(a), self.rating(b)),
            "surface_elo": elo_probability(
                self.effective_surface_rating(a, surface),
                self.effective_surface_rating(b, surface),
            ),
        }
