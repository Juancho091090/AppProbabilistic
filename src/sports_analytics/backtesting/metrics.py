"""Métricas por segmento sobre predicciones resueltas (en vivo o de backtesting).

Cada predicción es un evento binario (p. ej. "gana el local", "más de 2.5 goles") con
su probabilidad y su resultado 0/1. Se agregan por deporte, modelo, mercado, evento,
competición, confianza y rango de probabilidad.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from sports_analytics.models.calibration import (
    brier_score,
    calibration_curve,
    expected_calibration_error,
    log_loss,
)


@dataclass(frozen=True)
class ResolvedPrediction:
    sport: str
    competition: str
    model: str
    market: str
    event: str
    probability: float
    outcome: int  # 0/1
    confidence: str = ""


def prob_bucket(p: float, width: float = 0.1) -> str:
    # + 1e-9 evita que 0.7 / 0.1 = 6.999… caiga en el rango anterior
    n_bins = round(1 / width)
    lo = min(int(p / width + 1e-9), n_bins - 1) * width
    return f"{lo:.1f}-{lo + width:.1f}"


@dataclass(frozen=True)
class SegmentMetric:
    sport: str
    model: str
    market: str
    segment_type: str
    segment_value: str
    n: int
    brier: float
    log_loss: float
    accuracy: float
    ece: float
    mean_prob: float
    hit_rate: float


def _metric(rows: Sequence[ResolvedPrediction], n_bins: int) -> dict[str, float]:
    p = np.array([r.probability for r in rows], dtype=float)
    y = np.array([r.outcome for r in rows], dtype=int)
    return {
        "n": len(rows),
        "brier": brier_score(p, y),
        "log_loss": log_loss(p, y),
        "accuracy": float(np.mean((p >= 0.5) == (y == 1))),
        "ece": expected_calibration_error(p, y, n_bins),
        "mean_prob": float(p.mean()),
        "hit_rate": float(y.mean()),
    }


SEGMENTS = {
    "global": lambda r: "all",
    "event": lambda r: r.event,
    "competition": lambda r: r.competition,
    "confidence": lambda r: r.confidence or "n/a",
    "prob_bucket": lambda r: prob_bucket(r.probability),
}


def segment_metrics(
    rows: Iterable[ResolvedPrediction], n_bins: int = 10, min_n: int = 1
) -> list[SegmentMetric]:
    groups: dict[tuple, list[ResolvedPrediction]] = defaultdict(list)
    for r in rows:
        for seg_type, fn in SEGMENTS.items():
            groups[(r.sport, r.model, r.market, seg_type, fn(r))].append(r)
    out = []
    for (sport, model, market, seg_type, seg_value), items in sorted(groups.items()):
        if len(items) < min_n:
            continue
        m = _metric(items, n_bins)
        out.append(SegmentMetric(sport, model, market, seg_type, seg_value, **m))
    return out


def reliability_table(rows: Sequence[ResolvedPrediction], n_bins: int = 10) -> list[dict]:
    p = np.array([r.probability for r in rows])
    y = np.array([r.outcome for r in rows])
    return [
        {
            "bin": f"{b.lower:.1f}-{b.upper:.1f}",
            "predicha": b.mean_predicted,
            "observada": b.observed_rate,
            "n": b.count,
        }
        for b in calibration_curve(p, y, n_bins)
    ]
