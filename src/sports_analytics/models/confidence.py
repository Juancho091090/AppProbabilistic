"""Nivel de confianza de una predicción (Alta / Media / Baja).

No es una probabilidad: resume si la predicción se apoya en datos suficientes y
si los modelos coinciden.

    score = 0.5 · calidad_de_datos + 0.5 · acuerdo_entre_modelos

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
