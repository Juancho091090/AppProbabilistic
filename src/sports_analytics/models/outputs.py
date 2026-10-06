"""Formato estructurado de predicciones (fuente de verdad para DB, informe y Claude)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PredictionRecord(BaseModel):
    """Una probabilidad para un evento concreto de un partido."""

    sport: str  # football | tennis
    competition: str
    event_id: str  # id del partido en el proveedor
    market: str  # 1x2 | goals_total | corners_total | winner | at_least_one_set | games_total
    event: str  # home_win, draw, away_win, over_2.5, player_a_win, ...
    line: float | None = None
    probability: float = Field(ge=0, le=1)
    model: str  # ensemble_v1, elo, poisson, ...
    generated_at: datetime
    as_of: datetime  # información usada: solo partidos con kickoff < as_of
    confidence: str  # high | medium | low


class MatchForecast(BaseModel):
    """Pronóstico completo de un partido (lo que se guarda y se envía a Claude)."""

    sport: str
    competition: str
    competition_key: str
    event_id: str
    kickoff_utc: datetime
    home_or_a: str
    away_or_b: str
    model_version: str
    generated_at: datetime
    as_of: datetime
    markets: dict[str, Any]  # salida numérica final
    per_model: dict[str, Any]  # probabilidades de cada modelo antes del ensemble
    confidence: str
    confidence_score: float
    data_notes: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)  # ratings, λ, etc.
    records: list[PredictionRecord] = Field(default_factory=list)
