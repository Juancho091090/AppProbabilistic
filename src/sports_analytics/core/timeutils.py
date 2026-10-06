"""Utilidades de fecha/hora.

Regla del proyecto: internamente todo se guarda en UTC (timezone-aware). La zona
local (America/Bogota por defecto) solo se usa para decidir "qué es hoy" y para
mostrar horas en el informe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def ensure_utc(dt: datetime) -> datetime:
    """Convierte a UTC; rechaza datetimes naive para evitar ambigüedades."""
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria: usa datetimes timezone-aware")
    return dt.astimezone(UTC)


def now_utc() -> datetime:
    return datetime.now(UTC)


def local_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    """Fecha de 'hoy' en la zona local (no en UTC)."""
    current = ensure_utc(now) if now else now_utc()
    return current.astimezone(tz).date()


def local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Intervalo [inicio, fin) en UTC que cubre el día local completo."""
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def is_on_local_day(kickoff: datetime, day: date, tz: ZoneInfo) -> bool:
    start, end = local_day_bounds_utc(day, tz)
    return start <= ensure_utc(kickoff) < end


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return ensure_utc(dt).astimezone(tz)


def season_for(day: date, season_mode: str, split_start_month: int = 7) -> int:
    """Temporada en convención API-Football.

    calendar -> año natural. split -> año de inicio (temporada 2026/27 = 2026).
    """
    if season_mode == "calendar":
        return day.year
    return day.year if day.month >= split_start_month else day.year - 1
