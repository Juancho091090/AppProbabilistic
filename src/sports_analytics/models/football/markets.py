"""Probabilidades derivadas de una matriz de marcadores P[goles_local, goles_visitante]."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from sports_analytics.models.distributions import expected_value, over_under, pmf_to_dict


def one_x_two(matrix: np.ndarray) -> dict[str, float]:
    return {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
    }


def total_goals_pmf(matrix: np.ndarray) -> np.ndarray:
    n = matrix.shape[0] + matrix.shape[1] - 1
    pmf = np.zeros(n)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            pmf[i + j] += matrix[i, j]
    return pmf


def both_teams_score(matrix: np.ndarray) -> float:
    return float(matrix[1:, 1:].sum())


def most_likely_scores(matrix: np.ndarray, top: int = 3) -> list[dict]:
    flat = np.argsort(matrix, axis=None)[::-1][:top]
    out = []
    for f in flat:
        i, j = np.unravel_index(f, matrix.shape)
        out.append({"score": f"{i}-{j}", "probability": float(matrix[i, j])})
    return out


def summarize_matrix(matrix: np.ndarray, lines: Iterable[float]) -> dict:
    total = total_goals_pmf(matrix)
    home_pmf = matrix.sum(axis=1)
    away_pmf = matrix.sum(axis=0)
    return {
        "1x2": one_x_two(matrix),
        "expected_goals": {
            "home": expected_value(home_pmf),
            "away": expected_value(away_pmf),
            "total": expected_value(total),
        },
        "total_goals_distribution": pmf_to_dict(total),
        "goal_lines": over_under(total, lines),
        "btts": both_teams_score(matrix),
        "top_scores": most_likely_scores(matrix),
    }
