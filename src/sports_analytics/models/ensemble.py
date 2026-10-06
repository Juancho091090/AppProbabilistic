"""Ensemble lineal de probabilidades: P_final = Σ w_i · P_i.

* Si un modelo no está disponible para un partido (p. ej. logística sin datos
  suficientes), sus pesos se reparten proporcionalmente entre los demás.
* ``fit_weights`` estima los pesos minimizando log-loss sobre predicciones
  históricas ya resueltas (parametrización softmax => pesos ≥ 0 que suman 1).
  Se exige que todas las predicciones de entrenamiento sean anteriores a ``as_of``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize

from sports_analytics.core.timeutils import ensure_utc

_CLIP = 1e-12


@dataclass(frozen=True)
class EnsembleResult:
    probabilities: np.ndarray
    weights_used: dict[str, float]
    models_missing: list[str]
    spread: float  # máx. desacuerdo entre modelos en cualquier clase


def combine(
    model_probs: Mapping[str, Sequence[float] | None], weights: Mapping[str, float]
) -> EnsembleResult:
    available = {
        name: np.asarray(p, dtype=float)
        for name, p in model_probs.items()
        if p is not None and weights.get(name, 0) > 0
    }
    missing = sorted(set(weights) - set(available))
    if not available:
        raise ValueError("Ningún modelo disponible para el ensemble")
    total_w = sum(weights[n] for n in available)
    used = {n: weights[n] / total_w for n in available}
    stacked = np.vstack(list(available.values()))
    for row in stacked:
        if abs(row.sum() - 1) > 1e-6 or np.any(row < -1e-12):
            raise ValueError("Cada modelo debe entregar una distribución válida")
    final = sum(used[n] * available[n] for n in available)
    final = final / final.sum()
    spread = float(np.max(stacked.max(axis=0) - stacked.min(axis=0)))
    return EnsembleResult(final, used, missing, spread)


def fit_weights(
    model_probs: Mapping[str, np.ndarray],
    y: np.ndarray,
    *,
    event_times: Sequence[datetime],
    as_of: datetime,
    min_samples: int = 200,
    fallback: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Pesos que minimizan log-loss. ``model_probs[name]`` tiene forma (n, k)."""
    cutoff = ensure_utc(as_of)
    if any(ensure_utc(t) >= cutoff for t in event_times):
        raise ValueError("Predicciones con fecha >= as_of: data leakage en pesos del ensemble")
    names = list(model_probs)
    y = np.asarray(y, dtype=int)
    if len(y) < min_samples:
        if fallback is None:
            raise ValueError("Muestras insuficientes y sin pesos por defecto")
        return dict(fallback)

    probs = np.stack([np.asarray(model_probs[n], dtype=float) for n in names])  # (m, n, k)
    picked = probs[:, np.arange(len(y)), y]  # (m, n): prob. asignada al resultado real

    def loss(theta: np.ndarray) -> float:
        w = np.exp(theta - theta.max())
        w /= w.sum()
        p = np.clip(w @ picked, _CLIP, 1)
        return float(-np.mean(np.log(p)))

    res = minimize(loss, np.zeros(len(names)), method="L-BFGS-B")
    w = np.exp(res.x - res.x.max())
    w /= w.sum()
    return {n: float(v) for n, v in zip(names, w, strict=True)}
