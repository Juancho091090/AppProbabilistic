"""Nivel de confianza de una predicción (Alta / Media / Baja).

Método ``favorite_probability`` (por defecto desde el 7-oct-2026):

1. Nivel base según la probabilidad final (calibrada) del resultado más probable. En un
   modelo calibrado esa probabilidad ES la tasa de acierto esperada del favorito, así
   que el nivel separa los pronósticos que más aciertan.
2. Baja un nivel si el histórico es insuficiente (calidad de datos < ``min_data_quality``)
   o si los modelos discrepan más de ``max_model_spread``.

El método anterior (``data_agreement``: 0.5 · calidad de datos + 0.5 · acuerdo) no
anticipaba el acierto en el backtest (Alta 48.1 %, Media 53.6 %) y queda como opción.

    score = 0.5 · calidad_de_datos + 0.5 · acuerdo_entre_modelos   (data_agreement)

* calidad_de_datos ∈ [0,1]: min(n_partidos / mínimo_requerido, 1) del lado con
  menos datos, penalizado si faltan modelos del ensemble.
* acuerdo ∈ [0,1]: 1 − spread / max_model_spread (recortado).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label_es(self) -> str:
        return {"high": "Alta", "medium": "Media", "low": "Baja"}[self.value]


@dataclass(frozen=True)
class ConfidenceAssessment:
    level: ConfidenceLevel
    score: float
    data_quality: float
    agreement: float
    notes: tuple[str, ...]


def assess_confidence(
    *,
    min_matches_side: int,
    required_matches: int,
    spread: float,
    models_missing: int,
    models_total: int,
    cfg: dict,
) -> ConfidenceAssessment:
    notes = []
    data_q = min(min_matches_side / required_matches, 1.0) if required_matches > 0 else 1.0
    if min_matches_side < required_matches:
        notes.append(f"histórico limitado ({min_matches_side} partidos)")
    if models_total > 0 and models_missing:
        data_q *= 1 - 0.5 * models_missing / models_total
        notes.append(f"{models_missing} modelo(s) sin datos suficientes")
    max_spread = cfg.get("max_model_spread", 0.15)
    agreement = max(0.0, 1.0 - spread / max_spread) if max_spread > 0 else 1.0
    if spread > max_spread:
        notes.append(f"modelos discrepan ({spread:.0%} de diferencia)")
    score = 0.5 * data_q + 0.5 * agreement
    if score >= cfg["high"]:
        level = ConfidenceLevel.HIGH
    elif score >= cfg["medium"]:
        level = ConfidenceLevel.MEDIUM
    else:
        level = ConfidenceLevel.LOW
    return ConfidenceAssessment(level, score, data_q, agreement, tuple(notes))


_ORDER = [ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH]


def assess(
    *,
    sport: str,
    favorite_probability: float,
    min_matches_side: int,
    required_matches: int,
    spread: float,
    models_missing: int,
    models_total: int,
    cfg: dict,
) -> ConfidenceAssessment:
    """Punto de entrada: aplica el método configurado en ``confidence.method``."""
    base = assess_confidence(
        min_matches_side=min_matches_side,
        required_matches=required_matches,
        spread=spread,
        models_missing=models_missing,
        models_total=models_total,
        cfg=cfg,
    )
    if cfg.get("method", "data_agreement") != "favorite_probability":
        return base
    th = cfg["favorite_thresholds"][sport]
    p = favorite_probability
    idx = 2 if p >= th["high"] else 1 if p >= th["medium"] else 0
    notes = list(base.notes)
    if base.data_quality < cfg.get("min_data_quality", 0.6) and idx > 0:
        idx -= 1
        notes.append("confianza rebajada: histórico insuficiente")
    elif spread > cfg.get("max_model_spread", 0.15) and idx > 0:
        idx -= 1
        notes.append("confianza rebajada: los modelos discrepan")
    return ConfidenceAssessment(_ORDER[idx], p, base.data_quality, base.agreement, tuple(notes))
