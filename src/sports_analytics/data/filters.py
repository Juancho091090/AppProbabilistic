"""Filtros de competiciones autorizadas.

Toda la lógica de inclusión/exclusión vive aquí y se alimenta exclusivamente de
``competitions.yaml`` y ``tennis.yaml``. El resto del proyecto solo llama a
estas funciones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sports_analytics.config.loader import (
    CompetitionsConfig,
    FootballCompetition,
    TennisConfig,
)

_FRIENDLY_RE = re.compile(r"\b(friendl(y|ies)|amistos[oa]s?|club friendlies)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FootballFilterResult:
    included: bool
    competition: FootballCompetition | None
    reason: str


def filter_football_league(
    league_id: int,
    league_name: str,
    config: CompetitionsConfig,
) -> FootballFilterResult:
    """Decide si un partido de API-Football pertenece a una competición autorizada.

    Se usa una lista blanca por ID: cualquier liga ausente de la configuración se
    excluye. Los amistosos se excluyen salvo ``include_friendlies: true``.
    """
    if _FRIENDLY_RE.search(league_name or "") and not config.include_friendlies:
        return FootballFilterResult(False, None, "friendly_excluded")
    competition = config.by_api_id().get(league_id)
    if competition is None:
        return FootballFilterResult(False, None, "not_in_whitelist")
    return FootballFilterResult(True, competition, "whitelisted")


# ---------------------------------------------------------------- tenis


class Surface(StrEnum):
    HARD = "hard"
    CLAY = "clay"
    GRASS = "grass"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TennisFilterResult:
    included: bool
    tour: str | None
    reason: str


def _contains_term(text: str, term: str) -> bool:
    """Coincidencia por palabra completa (evita que 'itf' encaje en otra palabra)."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


def detect_tour(text: str, tours: list[str]) -> str | None:
    lowered = text.lower()
    for tour in tours:
        if _contains_term(lowered, tour.lower()):
            return tour.upper()
    return None


def filter_tennis_event(
    category: str,
    tournament: str,
    config: TennisConfig,
    *,
    is_doubles: bool = False,
    tour_hint: str | None = None,
) -> TennisFilterResult:
    """Lista blanca ATP/WTA del circuito principal; excluye Challenger, ITF, etc."""
    text = f"{category} | {tournament}".lower()

    for pattern in config.exclude_patterns:
        if _contains_term(text, pattern):
            return TennisFilterResult(False, None, f"excluded:{pattern}")

    if is_doubles and config.singles_only:
        return TennisFilterResult(False, None, "doubles_excluded")
    if re.search(r"\bdoubles?\b|\bdobles\b", text) and config.singles_only:
        return TennisFilterResult(False, None, "doubles_excluded")

    tour = (tour_hint or "").upper() or detect_tour(text, config.tours)
    if tour not in {t.upper() for t in config.tours}:
        return TennisFilterResult(False, None, "unknown_tour")

    allowed = config.include_categories.get(tour, [])
    if any(_contains_term(text, cat) for cat in allowed):
        return TennisFilterResult(True, tour, "whitelisted")
    return TennisFilterResult(False, tour, "category_not_whitelisted")


def normalize_surface(raw: str | None, config: TennisConfig) -> Surface:
    if not raw:
        return Surface.UNKNOWN
    lowered = raw.lower().strip()
    # Primero coincidencia exacta, luego por contenido (p. ej. "Indoor Hard")
    for surface, aliases in config.surfaces.items():
        if lowered in aliases:
            return Surface(surface)
    for surface, aliases in config.surfaces.items():
        if any(_contains_term(lowered, alias) for alias in aliases):
            return Surface(surface)
    return Surface.UNKNOWN
