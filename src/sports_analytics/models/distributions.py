"""Utilidades sobre distribuciones discretas (goles, córners, juegos)."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def normalize(pmf: np.ndarray) -> np.ndarray:
    total = pmf.sum()
    if total <= 0:
        raise ValueError("Distribución con masa nula")
    return pmf / total


def expected_value(pmf: np.ndarray, support: np.ndarray | None = None) -> float:
    support = np.arange(len(pmf)) if support is None else support
    return float(np.dot(pmf, support))


def over_under(pmf: np.ndarray, lines: Iterable[float], support: np.ndarray | None = None) -> dict:
    """P(X > línea) y P(X < línea) para líneas .5 (sin empate posible)."""
    support = np.arange(len(pmf)) if support is None else support
    out = {}
    for line in lines:
        if float(line).is_integer():
            raise ValueError("Use líneas con .5 para evitar empates (push)")
        p_over = float(pmf[support > line].sum())
        out[f"{line:g}"] = {"over": p_over, "under": 1.0 - p_over}
    return out


def pmf_to_dict(pmf: np.ndarray, support: np.ndarray | None = None, min_prob: float = 1e-4) -> dict:
    support = np.arange(len(pmf)) if support is None else support
    return {int(k): float(p) for k, p in zip(support, pmf, strict=True) if p >= min_prob}
