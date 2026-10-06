"""Regresión logística (binaria o multinomial) con estandarización.

Uso: fútbol (1X2, 3 clases) y tenis (ganador, 2 clases). Las features las
construyen los módulos ``features/`` en un recorrido cronológico, de modo que
cada fila solo contiene información anterior a su partido.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler


@dataclass
class ProbabilisticLogit:
    feature_names: list[str]
    min_samples: int = 300
    c: float = 1.0
    pipeline: Pipeline | None = field(default=None, repr=False)
    classes_: np.ndarray | None = None
    n_train: int = 0

    @property
    def is_fitted(self) -> bool:
        return self.pipeline is not None

    def fit(self, x: np.ndarray, y: np.ndarray) -> ProbabilisticLogit:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y)
        if x.ndim != 2 or x.shape[1] != len(self.feature_names):
            raise ValueError("Dimensiones de X no coinciden con feature_names")
        mask = ~np.isnan(x).any(axis=1)
        x, y = x[mask], y[mask]
        self.n_train = len(y)
        if self.n_train < self.min_samples or len(np.unique(y)) < 2:
            self.pipeline = None  # insuficiente: el ensemble lo omitirá
            return self
        self.pipeline = make_pipeline(StandardScaler(), LogisticRegression(C=self.c, max_iter=2000))
        self.pipeline.fit(x, y)
        self.classes_ = self.pipeline.classes_
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("Modelo logístico no entrenado (datos insuficientes)")
        return self.pipeline.predict_proba(np.atleast_2d(np.asarray(x, dtype=float)))

    def coefficients(self) -> dict[str, list[float]]:
        if not self.is_fitted:
            return {}
        lr = self.pipeline[-1]
        return {name: lr.coef_[:, i].tolist() for i, name in enumerate(self.feature_names)}
