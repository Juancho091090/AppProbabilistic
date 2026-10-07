"""Reglas de puntuación y diagnósticos de pronósticos probabilísticos.

Todas las funciones reciben arrays de numpy y devuelven floats.

* RPS (Ranked Probability Score): estándar para 1X2 porque respeta el orden
  local < empate < visitante: RPS = ½ Σ_{k=1}^{2} (F_k − O_k)², con F y O acumuladas.
* Brier multiclase: Σ_k (p_k − y_k)².
* Brier Skill Score: 1 − Brier / Brier_referencia (positivo = mejor que la referencia).
* Descomposición de Murphy (binaria, por rangos): Brier ≈ fiabilidad − resolución +
  incertidumbre. Fiabilidad baja = bien calibrado; resolución alta = discrimina.
* AUC: probabilidad de que un caso positivo reciba más probabilidad que uno negativo.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import rankdata

EPS = 1e-12


def rps(p: np.ndarray, y: np.ndarray) -> float:
    k = p.shape[1]
    onehot = np.eye(k)[y]
    cf, co = np.cumsum(p, axis=1)[:, :-1], np.cumsum(onehot, axis=1)[:, :-1]
    return float((((cf - co) ** 2).sum(axis=1) / (k - 1)).mean())


def brier_multiclass(p: np.ndarray, y: np.ndarray) -> float:
    return float(((p - np.eye(p.shape[1])[y]) ** 2).sum(axis=1).mean())


def log_loss_multiclass(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(p[np.arange(len(y)), y], EPS, 1)).mean())


def favorite_accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float((p.argmax(axis=1) == y).mean())


def brier_binary(p: np.ndarray, y: np.ndarray) -> float:
    return float(((p - y) ** 2).mean())


def log_loss_binary(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def skill(score: float, reference: float) -> float:
    return float(1 - score / reference) if reference > 0 else float("nan")


def auc(p: np.ndarray, y: np.ndarray) -> float:
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = rankdata(p)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def bins(p: np.ndarray, n_bins: int = 10) -> np.ndarray:
    return np.minimum((p * n_bins + 1e-9).astype(int), n_bins - 1)


def murphy(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict[str, float]:
    b = bins(p, n_bins)
    o_bar = y.mean()
    rel = res = 0.0
    for k in np.unique(b):
        m = b == k
        w = m.mean()
        rel += w * (p[m].mean() - y[m].mean()) ** 2
        res += w * (y[m].mean() - o_bar) ** 2
    return {
        "reliability": float(rel),
        "resolution": float(res),
        "uncertainty": float(o_bar * (1 - o_bar)),
    }


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    b = bins(p, n_bins)
    return float(
        sum(abs(p[b == k].mean() - y[b == k].mean()) * (b == k).mean() for k in np.unique(b))
    )


def calibration_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[list]:
    b = bins(p, n_bins)
    rows = []
    for k in range(n_bins):
        m = b == k
        if m.any():
            rows.append(
                [
                    f"{k / n_bins:.1f}-{(k + 1) / n_bins:.1f}",
                    int(m.sum()),
                    float(p[m].mean()),
                    float(y[m].mean()),
                ]
            )
    return rows
