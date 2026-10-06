"""Modelos de datos normalizados, independientes del proveedor.

Los clientes de API convierten sus respuestas a estos esquemas; los modelos
estadísticos solo conocen estos tipos.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from sports_analytics.core.timeutils import ensure_utc


class MatchStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class _Timed(BaseModel):
    kickoff_utc: datetime

    @field_validator("kickoff_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class FootballMatch(_Timed):
    match_id: str
    competition_key: str
    season: int | None = None
    home_team: str  # clave única (id externo) usada por los modelos
    away_team: str
    home_name: str | None = None  # nombre para mostrar
    away_name: str | None = None
    status: MatchStatus = MatchStatus.SCHEDULED
    neutral_venue: bool = False
    home_goals: int | None = Field(default=None, ge=0)
    away_goals: int | None = Field(default=None, ge=0)
    home_corners: int | None = Field(default=None, ge=0)
    away_corners: int | None = Field(default=None, ge=0)
    home_shots: int | None = Field(default=None, ge=0)
    away_shots: int | None = Field(default=None, ge=0)
    home_possession: float | None = Field(default=None, ge=0, le=100)
    away_possession: float | None = Field(default=None, ge=0, le=100)

    @property
    def is_finished(self) -> bool:
        return (
            self.status == MatchStatus.FINISHED
            and self.home_goals is not None
            and self.away_goals is not None
        )

    @property
    def display_home(self) -> str:
        return self.home_name or self.home_team

    @property
    def display_away(self) -> str:
        return self.away_name or self.away_team

    @property
    def has_corners(self) -> bool:
        return self.home_corners is not None and self.away_corners is not None


class TennisMatch(_Timed):
    match_id: str
    tour: str  # ATP | WTA
    tournament: str
    category: str
    surface: str  # hard | clay | grass | unknown
    best_of: int = Field(default=3)
    player_a: str  # clave única (id externo) usada por los modelos
    player_b: str
    player_a_name: str | None = None
    player_b_name: str | None = None
    status: MatchStatus = MatchStatus.SCHEDULED
    winner: str | None = None  # "A" | "B"
    sets_a: int | None = None
    sets_b: int | None = None
    games_a: int | None = None
    games_b: int | None = None
    retired: bool = False
    rank_a: int | None = None
    rank_b: int | None = None
    # Estadísticas de servicio/resto (proporciones 0-1) si el proveedor las da
    a_spw: float | None = None  # service points won
    b_spw: float | None = None
    a_rpw: float | None = None  # return points won
    b_rpw: float | None = None

    @field_validator("best_of")
    @classmethod
    def _best_of(cls, value: int) -> int:
        if value not in (3, 5):
            raise ValueError("best_of debe ser 3 o 5")
        return value

    @property
    def display_a(self) -> str:
        return self.player_a_name or self.player_a

    @property
    def display_b(self) -> str:
        return self.player_b_name or self.player_b

    @property
    def is_finished(self) -> bool:
        return self.status == MatchStatus.FINISHED and self.winner in ("A", "B")


def strictly_before(matches, as_of: datetime):
    """Filtro anti-leakage: solo partidos finalizados con inicio estrictamente anterior."""
    cutoff = ensure_utc(as_of)
    return [m for m in matches if m.kickoff_utc < cutoff and m.is_finished]
