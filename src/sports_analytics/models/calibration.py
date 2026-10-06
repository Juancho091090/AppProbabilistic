"""Métricas de calidad probabilística y calibradores (Platt / Isotónica).

Métricas: Brier, Log Loss, Accuracy, curva de calibración y ECE.

Calibradores: se ajustan solo con predicciones cuyo evento ocurrió antes de
``as_of``. El método se elige por tamaño de muestra (config ``calibration``):
    n < min_samples_platt          -> sin calibrar (identidad)
    min_samples_platt <= n < iso   -> Platt (logística sobre logit(p))
    n >= min_samples_isotonic      -> Isotónica
Para 1X2 se calibra cada clase (uno-contra-resto) y se renormaliza.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from sports_analytics.core.timeutils import ensure_utc

_EPS = 1e-12


def _as_matrix(p: np.ndarray) -> np.ndarray:
    """Convierte prob. binaria (n,) en matriz (n, 2) [P(0), P(1)]."""
    p = np.asarray(p, dtype=float)
    if p.ndim == 1:
        return np.column_stack([1 - p, p])
    return p


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    """Brier multiclase: media de Σ_k (p_k − 1[y=k])². Para binario, en escala 0–2 /2."""
    probs = _as_matrix(p)
    y = np.asarray(y, dtype=int)
    onehot = np.eye(probs.shape[1])[y]
    score = np.mean(np.sum((probs - onehot) ** 2, axis=1))
    # En binario se reporta la convención clásica (0–1): mean((p1 − y)²)
    return float(score / 2 if probs.shape[1] == 2 else score)


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    probs = _as_matrix(p)
    y = np.asarray(y, dtype=int)
    picked = np.clip(probs[np.arange(len(y)), y], _EPS, 1)
    return float(-np.mean(np.log(picked)))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    probs = _as_matrix(p)
    return float(np.mean(np.argmax(probs, axis=1) == np.asarray(y)))


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    mean_predicted: float
    observed_rate: float
    count: int


def calibration_curve(p: np.ndarray, outcome: np.ndarray, n_bins: int = 10) -> list[CalibrationBin]:
    """Curva de calibración binaria: p = prob. de un evento, outcome ∈ {0,1}."""
    p = np.asarray(p, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & ((p < hi) if i < n_bins - 1 else (p <= hi))
        if mask.any():
            bins.append(
                CalibrationBin(
                    lo, hi, float(p[mask].mean()), float(outcome[mask].mean()), int(mask.sum())
                )
            )
    return bins


def expected_calibration_error(p: np.ndarray, outcome: np.ndarray, n_bins: int = 10) -> float:
    bins = calibration_curve(p, outcome, n_bins)
    n = sum(b.count for b in bins)
    if n == 0:
        return float("nan")
    return float(sum(b.count / n * abs(b.mean_predicted - b.observed_rate) for b in bins))


def multiclass_ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """ECE 'top-label': confianza de la clase predicha vs acierto."""
    probs = _as_matrix(p)
    conf = probs.max(axis=1)
    correct = (np.argmax(probs, axis=1) == np.asarray(y)).astype(float)
    return expected_calibration_error(conf, correct, n_bins)


def all_metrics(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict[str, float]:
    return {
        "n": int(len(y)),
        "brier": brier_score(p, y),
        "log_loss": log_loss(p, y),
        "accuracy": accuracy(p, y),
        "ece": multiclass_ece(p, y, n_bins),
    }


# ----------------------------------------------------------------- calibradores


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@dataclass
class BinaryCalibrator:
    method: str = "identity"
    _model: object | None = field(default=None, repr=False)

    def fit(self, p: np.ndarray, outcome: np.ndarray, method: str) -> BinaryCalibrator:
        self.method = method
        p, outcome = np.asarray(p, float), np.asarray(outcome, int)
        if method == "platt" and len(np.unique(outcome)) == 2:
            lr = LogisticRegression(C=1e6, max_iter=1000)  # sin regularización efectiva
            lr.fit(_logit(p).reshape(-1, 1), outcome)
            self._model = lr
        elif method == "isotonic":
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(p, outcome)
            self._model = iso
        else:
            self.method, self._model = "identity", None
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, float)
        if self._model is None:
            return p
        if self.method == "platt":
            return self._model.predict_proba(_logit(p).reshape(-1, 1))[:, 1]
        return self._model.predict(p)


@dataclass
class ProbabilityCalibrator:
    """Calibrador para distribuciones de k clases (k=2 tenis, k=3 1X2)."""

    min_samples_platt: int = 200
    min_samples_isotonic: int = 1000
    method: str = "identity"
    per_class: list[BinaryCalibrator] = field(default_factory=list)
    n_train: int = 0

    @classmethod
    def from_config(cls, cfg: dict) -> ProbabilityCalibrator:
        return cls(cfg["min_samples_platt"], cfg["min_samples_isotonic"])

    def choose_method(self, n: int) -> str:
        if n >= self.min_samples_isotonic:
            return "isotonic"
        if n >= self.min_samples_platt:
            return "platt"
        return "identity"

    def fit(
        self,
        p: np.ndarray,
        y: np.ndarray,
        *,
        event_times: Sequence[datetime],
        as_of: datetime,
    ) -> ProbabilityCalibrator:
        cutoff = ensure_utc(as_of)
        mask = np.array([ensure_utc(t) < cutoff for t in event_times], dtype=bool)
        probs = _as_matrix(p)[mask]
        y = np.asarray(y, dtype=int)[mask]
        self.n_train = len(y)
        self.method = self.choose_method(self.n_train)
        self.per_class = [
            BinaryCalibrator().fit(probs[:, k], (y == k).astype(int), self.method)
            for k in range(probs.shape[1])
        ]
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        probs = _as_matrix(p)
        if self.method == "identity" or not self.per_class:
            return probs
        out = np.column_stack([c.transform(probs[:, k]) for k, c in enumerate(self.per_class)])
        out = np.clip(out, _EPS, None)
        return out / out.sum(axis=1, keepdims=True)
