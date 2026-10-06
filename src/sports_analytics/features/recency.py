"""Ponderación temporal de partidos.

Dos métodos configurables (``models.yaml -> recency``):

* ``exponential``: w = 0.5 ** (días / half_life). Decay continuo (preferido).
* ``buckets``: pesos por posición hacia atrás (últimos 5, 10, 20, resto).

Ambos aplican ``min_weight`` para no anular el histórico antiguo.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np

from sports_analytics.core.timeutils import ensure_utc


def exponential_weights(
    dates: Sequence[datetime], as_of: datetime, half_life_days: float, min_weight: float = 0.0
) -> np.ndarray:
    if half_life_days <= 0:
        raise ValueError("half_life_days debe ser > 0")
    ref = ensure_utc(as_of)
    ages = np.array([(ref - ensure_utc(d)).total_seconds() / 86400.0 for d in dates], dtype=float)
    if np.any(ages < 0):
        raise ValueError("Hay partidos posteriores a as_of: posible data leakage")
    return np.maximum(0.5 ** (ages / half_life_days), min_weight)


def bucket_weights(
    dates: Sequence[datetime], buckets: Sequence[dict], min_weight: float = 0.0
) -> np.ndarray:
    """Pesos por rango de recencia (posición 0 = partido más reciente)."""
    order = np.argsort([-ensure_utc(d).timestamp() for d in dates], kind="stable")
    weights = np.empty(len(dates), dtype=float)
    for rank, idx in enumerate(order):
        weight = buckets[-1]["weight"]
        for bucket in buckets:
            if bucket["last_n"] is not None and rank < bucket["last_n"]:
                weight = bucket["weight"]
                break
        weights[idx] = max(weight, min_weight)
    return weights


def recency_weights(
    dates: Sequence[datetime], as_of: datetime, recency_cfg: dict, sport: str
) -> np.ndarray:
    if len(dates) == 0:
        return np.array([], dtype=float)
    min_w = float(recency_cfg.get("min_weight", 0.0))
    if recency_cfg.get("method", "exponential") == "buckets":
        if any(ensure_utc(d) >= ensure_utc(as_of) for d in dates):
            raise ValueError("Hay partidos posteriores a as_of: posible data leakage")
        return bucket_weights(dates, recency_cfg["buckets"], min_w)
    return exponential_weights(dates, as_of, recency_cfg["half_life_days"][sport], min_w)
