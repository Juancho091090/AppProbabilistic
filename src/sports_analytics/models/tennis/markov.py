"""Modelo de Markov exacto de un partido de tenis.

Entradas: p_a = P(A gana un punto con su saque), p_b = P(B gana un punto con su saque).

Jerarquía exacta (sin simulación):
    punto -> juego (fórmula cerrada con deuce)
          -> tiebreak (programación dinámica, alternancia de saque 1-2-2-…)
          -> set (DP sobre marcadores de juegos, saque alternado)
          -> partido (convolución de sets; quien saca primero en el set siguiente
             es quien no sacó el último juego del set anterior)

Salidas: P(ganar partido), P(cada jugador gana ≥1 set), distribución de marcadores
en sets y distribución exacta del total de juegos (el tiebreak cuenta como 1 juego).

Cuando no hay estadísticas de saque/resto, ``serve_probs_from_match_prob`` obtiene
(p_a, p_b) que reproducen una probabilidad de victoria dada (p. ej. la de Elo), de
modo que sets y juegos quedan coherentes con el modelo de ganador.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from sports_analytics.models.distributions import expected_value, over_under, pmf_to_dict

_EPS = 1e-12


def p_game(p: float) -> float:
    """P(el sacador gana el juego) con prob. de punto p."""
    q = 1 - p
    deuce_win = p * p / (p * p + q * q) if p * p + q * q > 0 else 0.5
    return p**4 * (1 + 4 * q + 10 * q**2) + 20 * p**3 * q**3 * deuce_win


def _tb_server_is_first(k: int) -> bool:
    """El punto k (0-based) del tiebreak lo saca quien abrió el tiebreak?"""
    return ((k + 1) // 2) % 2 == 0


def p_tiebreak(p_a: float, p_b: float, a_serves_first: bool, target: int = 7) -> float:
    """P(A gana el tiebreak a ``target`` puntos, diferencia de 2)."""

    # P(A gana un punto) según quién saca
    def pa_point(k: int) -> float:
        a_serves = _tb_server_is_first(k) == a_serves_first
        return p_a if a_serves else 1 - p_b

    # Desde igualdad con total de puntos par, los dos puntos siguientes los saca
    # un jugador cada uno -> fórmula cerrada.
    win2 = p_a * (1 - p_b)
    lose2 = (1 - p_a) * p_b
    deuce = win2 / (win2 + lose2) if win2 + lose2 > _EPS else 0.5

    probs = {(0, 0): 1.0}
    p_win = 0.0
    for total in range(0, 2 * target):
        nxt: dict[tuple[int, int], float] = {}
        for (a, b), pr in probs.items():
            if a + b != total or pr == 0:
                continue
            pp = pa_point(total)
            for (na, nb), step in (((a + 1, b), pp), ((a, b + 1), 1 - pp)):
                mass = pr * step
                if na >= target and na - nb >= 2:
                    p_win += mass
                elif nb >= target and nb - na >= 2:
                    pass
                elif na == nb == target - 1:
                    p_win += mass * deuce
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + mass
        probs = nxt
    return p_win


@dataclass(frozen=True)
class SetOutcome:
    games_a: int
    games_b: int
    probability: float

    @property
    def total_games(self) -> int:
        return self.games_a + self.games_b

    @property
    def a_wins(self) -> bool:
        return self.games_a > self.games_b

    def next_set_a_serves_first(self, a_served_first: bool) -> bool:
        # Último juego = índice total-1; lo saca quien abrió si ese índice es par
        last_by_first = (self.total_games - 1) % 2 == 0
        a_served_last = last_by_first == a_served_first
        return not a_served_last


@lru_cache(maxsize=4096)
def set_outcomes(
    p_a: float, p_b: float, a_serves_first: bool, tb_target: int = 7
) -> tuple[SetOutcome, ...]:
    """Distribución exacta de marcadores finales de un set."""
    hold_a, hold_b = p_game(p_a), p_game(p_b)
    states = {(0, 0): 1.0}
    finals: dict[tuple[int, int], float] = {}
    for _ in range(12):
        nxt: dict[tuple[int, int], float] = {}
        for (ga, gb), pr in states.items():
            a_serves = ((ga + gb) % 2 == 0) == a_serves_first
            pa = hold_a if a_serves else 1 - hold_b
            for (na, nb), step in (((ga + 1, gb), pa), ((ga, gb + 1), 1 - pa)):
                mass = pr * step
                if (na == 6 and nb <= 4) or (nb == 6 and na <= 4) or (na == 7 or nb == 7):
                    finals[(na, nb)] = finals.get((na, nb), 0.0) + mass
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + mass
        states = nxt
    # Solo queda 6-6: tiebreak. Juego 13 (índice 12, par) lo abre quien abrió el set.
    pr66 = states.pop((6, 6), 0.0)
    if states:
        raise AssertionError(f"Estados de set no resueltos: {states}")
    p_tb = p_tiebreak(p_a, p_b, a_serves_first, tb_target)
    finals[(7, 6)] = finals.get((7, 6), 0.0) + pr66 * p_tb
    finals[(6, 7)] = finals.get((6, 7), 0.0) + pr66 * (1 - p_tb)
    return tuple(SetOutcome(a, b, p) for (a, b), p in sorted(finals.items()) if p > 0)


@dataclass
class MatchDistribution:
    p_a_wins: float
    p_a_wins_set: float  # A gana al menos un set
    p_b_wins_set: float
    set_scores: dict[str, float]
    games_pmf: np.ndarray
    p_a_serve: float
    p_b_serve: float
    best_of: int

    @property
    def expected_games(self) -> float:
        return expected_value(self.games_pmf)

    def games_lines(self, lines) -> dict:
        valid = [ln for ln in lines if ln < len(self.games_pmf)]
        return over_under(self.games_pmf, valid)

    def to_dict(self, lines=()) -> dict:
        return {
            "winner": {"A": self.p_a_wins, "B": 1 - self.p_a_wins},
            "at_least_one_set": {"A": self.p_a_wins_set, "B": self.p_b_wins_set},
            "set_scores": self.set_scores,
            "expected_games": self.expected_games,
            "games_distribution": pmf_to_dict(self.games_pmf),
            "games_lines": self.games_lines(lines),
            "serve_point_prob": {"A": self.p_a_serve, "B": self.p_b_serve},
            "best_of": self.best_of,
        }


def _match_given_first(
    p_a: float, p_b: float, best_of: int, a_serves_first: bool, final_tb: int
) -> tuple[float, dict[str, float], np.ndarray]:
    need = best_of // 2 + 1
    max_games = best_of * 13
    # estado: (sets_a, sets_b, a_serves_first_in_set) -> pmf de juegos acumulados
    states: dict[tuple[int, int, bool], np.ndarray] = {}
    start = np.zeros(max_games + 1)
    start[0] = 1.0
    states[(0, 0, a_serves_first)] = start
    games_pmf = np.zeros(max_games + 1)
    set_scores: dict[str, float] = {}
    p_win = 0.0
    for _ in range(best_of):
        nxt: dict[tuple[int, int, bool], np.ndarray] = {}
        for (sa, sb, first), pmf in states.items():
            is_final_set = sa == sb == need - 1
            outcomes = set_outcomes(p_a, p_b, first, final_tb if is_final_set else 7)
            for out in outcomes:
                shifted = np.zeros_like(pmf)
                shifted[out.total_games :] = pmf[: len(pmf) - out.total_games]
                shifted *= out.probability
                na, nb = (sa + 1, sb) if out.a_wins else (sa, sb + 1)
                if na == need or nb == need:
                    games_pmf += shifted
                    mass = float(shifted.sum())
                    key = f"{na}-{nb}"
                    set_scores[key] = set_scores.get(key, 0.0) + mass
                    if na == need:
                        p_win += mass
                else:
                    key = (na, nb, out.next_set_a_serves_first(first))
                    nxt[key] = nxt.get(key, 0.0) + shifted
        states = nxt
    return p_win, set_scores, games_pmf


def match_distribution(
    p_a: float, p_b: float, best_of: int = 3, final_set_tiebreak: int = 7
) -> MatchDistribution:
    """Distribución exacta, promediando quién saca primero (sorteo 50/50)."""
    if not (0 < p_a < 1 and 0 < p_b < 1):
        raise ValueError("Probabilidades de punto al saque deben estar en (0, 1)")
    if best_of not in (3, 5):
        raise ValueError("best_of debe ser 3 o 5")
    p_a, p_b = round(p_a, 6), round(p_b, 6)  # estabiliza la caché
    need = best_of // 2 + 1
    results = [
        _match_given_first(p_a, p_b, best_of, first, final_set_tiebreak) for first in (True, False)
    ]
    p_win = sum(r[0] for r in results) / 2
    games = sum(r[2] for r in results) / 2
    scores: dict[str, float] = {}
    for _, sc, _ in results:
        for k, v in sc.items():
            scores[k] = scores.get(k, 0.0) + v / 2
    p_b_straight = scores.get(f"0-{need}", 0.0)
    p_a_straight = scores.get(f"{need}-0", 0.0)
    return MatchDistribution(
        p_a_wins=p_win,
        p_a_wins_set=1 - p_b_straight,
        p_b_wins_set=1 - p_a_straight,
        set_scores=dict(sorted(scores.items())),
        games_pmf=games,
        p_a_serve=p_a,
        p_b_serve=p_b,
        best_of=best_of,
    )


def barnett_clarke(
    spw_a: float, rpw_a: float, spw_b: float, rpw_b: float, tour_avg_rpw: float
) -> tuple[float, float]:
    """Probabilidades de punto al saque combinando saque propio y resto rival."""
    p_a = spw_a - (rpw_b - tour_avg_rpw)
    p_b = spw_b - (rpw_a - tour_avg_rpw)
    return float(np.clip(p_a, 0.05, 0.95)), float(np.clip(p_b, 0.05, 0.95))


def serve_probs_from_match_prob(
    target_p_a: float, base_spw: float, best_of: int = 3, final_set_tiebreak: int = 7
) -> tuple[float, float]:
    """Encuentra δ tal que match(base+δ, base−δ) = target_p_a."""
    lo_d = -min(base_spw - 0.02, 0.98 - base_spw)
    hi_d = -lo_d
    target = float(np.clip(target_p_a, 1e-4, 1 - 1e-4))

    def f(delta: float) -> float:
        return (
            match_distribution(
                base_spw + delta, base_spw - delta, best_of, final_set_tiebreak
            ).p_a_wins
            - target
        )

    f_lo, f_hi = f(lo_d), f(hi_d)
    if f_lo >= 0:
        delta = lo_d
    elif f_hi <= 0:
        delta = hi_d
    else:
        delta = brentq(f, lo_d, hi_d, xtol=1e-7)
    return base_spw + delta, base_spw - delta
