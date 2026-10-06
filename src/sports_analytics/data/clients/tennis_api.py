"""Cliente de Tennis API – ATP WTA ITF (RapidAPI).

Esquema verificado con la API real (ver docs/data-sources.md):

* ``/tennis/v2/{tour}/tournament/calendar/{año}``: torneos con ``id``, ``name``,
  ``rankId``, ``courtId``, ``court.name``, ``tier`` y ``date``.
* ``/tennis/v2/{tour}/fixtures/{fecha}``: partidos programados. Solo traen
  ``tournamentId`` (sin nivel ni superficie), así que se cruzan con el calendario.
* ``/tennis/v2/{tour}/results/{inicio}/{fin}``: resultados con ``result``
  (p. ej. "6-3 2-6 4-2 ret."), ``result_type`` (completed | retired),
  ``match_winner`` y el objeto ``tournament``.
* ``/tennis/v2/{tour}/ranking/singles``: ranking actual.

Todas las respuestas se paginan (``pageSize`` hasta 500, ``hasNextPage``).

Plan FREE: 50 llamadas al día y 4 por segundo. El cliente espera entre llamadas y usa
caché larga para el calendario y para resultados de días ya cerrados.
Los resultados no incluyen estadísticas de saque y resto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from sports_analytics.config.loader import TennisConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError, HttpApiClient
from sports_analytics.core.logging import get_logger, register_secret
from sports_analytics.data.filters import _contains_term
from sports_analytics.data.schemas import MatchStatus, TennisMatch

log = get_logger(__name__)

PROVIDER = "tennis_api"
TOURS = ("atp", "wta")

# courtId verificado: 1 Hard, 2 Clay, 3 I.hard, 4 Carpet, 5 Grass
COURT_SURFACE = {1: "hard", 2: "clay", 3: "hard", 4: "hard", 5: "grass"}
COURT_INDOOR = {3, 4}

TTL_CALENDAR = 7 * 24 * 3600
TTL_CLOSED_RESULTS = 365 * 24 * 3600
TTL_FIXTURES = 900
TTL_RANKING = 12 * 3600

_SET_RE = re.compile(r"(\d+)-(\d+)(?:\(\d+\))?")


@dataclass(frozen=True)
class TournamentInfo:
    id: int
    name: str
    rank_id: int | None
    court_id: int | None
    surface: str
    indoor: bool
    tier: str
    start: date | None


@dataclass(frozen=True)
class ParsedScore:
    sets: list[tuple[int, int]]  # desde la perspectiva de player1
    retired: bool

    @property
    def games(self) -> tuple[int, int]:
        return sum(s[0] for s in self.sets), sum(s[1] for s in self.sets)

    def sets_won(self) -> tuple[int, int]:
        a = b = 0
        for x, y in self.sets:
            if _set_complete(x, y):
                a += x > y
                b += y > x
        return a, b


def _set_complete(x: int, y: int) -> bool:
    hi, lo = max(x, y), min(x, y)
    return (hi == 6 and lo <= 4) or (hi == 7 and lo in (5, 6)) or (hi > 7 and hi - lo == 2)


def parse_score(result: str | None) -> ParsedScore | None:
    if not result:
        return None
    retired = "ret" in result.lower() or "w/o" in result.lower()
    sets = [(int(a), int(b)) for a, b in _SET_RE.findall(result)]
    return ParsedScore(sets, retired) if sets or retired else None


def parse_tournament(item: dict[str, Any]) -> TournamentInfo:
    court_id = item.get("courtId")
    court = item.get("court")
    surface = COURT_SURFACE.get(court_id, "unknown")
    if surface == "unknown" and isinstance(court, dict):
        name = (court.get("name") or "").lower()
        surface = next((s for s in ("clay", "grass", "hard") if s in name), "unknown")
    raw_date = item.get("date")
    return TournamentInfo(
        id=int(item["id"]),
        name=item.get("name") or "",
        rank_id=item.get("rankId"),
        court_id=court_id,
        surface=surface,
        indoor=court_id in COURT_INDOOR,
        tier=item.get("tier") or "",
        start=datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date() if raw_date else None,
    )


def is_main_tour(info: TournamentInfo | None, config: TennisConfig) -> bool:
    """Circuito principal ATP/WTA: rankId >= 2 y ningún patrón excluido en el nombre."""
    if info is None or info.rank_id is None or info.rank_id < config.min_rank_id:
        return False
    text = f"{info.tier} | {info.name}".lower()
    return not any(_contains_term(text, p) for p in config.exclude_patterns)


def is_doubles(item: dict[str, Any]) -> bool:
    names = [(item.get(p) or {}).get("name", "") for p in ("player1", "player2")]
    return any("/" in n for n in names)


def best_of_for(tour: str, info: TournamentInfo | None, grand_slam_rank_id: int) -> int:
    if tour.lower() == "atp" and info is not None and info.rank_id == grand_slam_rank_id:
        return 5
    return 3


def to_tennis_match(
    item: dict[str, Any],
    tour: str,
    info: TournamentInfo | None,
    ranks: dict[int, int] | None = None,
    grand_slam_rank_id: int = 4,
) -> TennisMatch:
    """Convierte un fixture o resultado en ``TennisMatch`` (A = player1, B = player2)."""
    p1, p2 = item["player1"], item["player2"]
    ranks = ranks or {}
    raw_date = item.get("date") or item.get("startTime")
    kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    winner_id = item.get("match_winner")
    score = parse_score(item.get("result"))
    status = MatchStatus.SCHEDULED
    winner = None
    sets_a = sets_b = games_a = games_b = None
    retired = False
    if winner_id is not None:
        status = MatchStatus.FINISHED
        winner = "A" if winner_id == p1["id"] else "B" if winner_id == p2["id"] else None
        retired = (item.get("result_type") or "").lower() != "completed"
        if score:
            sa, sb = score.sets_won()
            ga, gb = score.games
            # Si el marcador viene desde la perspectiva del ganador, se orienta a player1
            if not retired and winner == "B" and sa > sb:
                sa, sb, ga, gb = sb, sa, gb, ga
            sets_a, sets_b, games_a, games_b = sa, sb, ga, gb
            retired = retired or score.retired
    tournament_name = info.name if info else str(item.get("tournamentId"))
    return TennisMatch(
        match_id=f"{tour}-{item.get('matchId') or item.get('id')}",
        tour=tour.upper(),
        tournament=tournament_name,
        category=info.tier if info else "",
        surface=info.surface if info else "unknown",
        best_of=best_of_for(tour, info, grand_slam_rank_id),
        player_a=p1["name"],
        player_b=p2["name"],
        kickoff_utc=kickoff,
        status=status,
        winner=winner,
        sets_a=sets_a,
        sets_b=sets_b,
        games_a=games_a,
        games_b=games_b,
        retired=retired,
        rank_a=ranks.get(p1["id"]),
        rank_b=ranks.get(p2["id"]),
    )


class TennisApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=None,
        min_interval: float = 0.3,
    ):
        if settings.tennis_api_key is None:
            raise ApiError(PROVIDER, "TENNIS_API_KEY no configurada")
        key = settings.tennis_api_key.get_secret_value()
        register_secret(key)
        kwargs = {"sleep": sleep} if sleep else {}
        self.http = HttpApiClient(
            provider=PROVIDER,
            base_url=settings.tennis_api_base_url,
            headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": settings.tennis_api_host},
            daily_limit=settings.tennis_api_daily_limit,
            cache_dir=Path(settings.cache_dir),
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            min_interval=min_interval,
            transport=transport,
            **kwargs,
        )

    def close(self) -> None:
        self.http.close()

    def _paged(
        self, path: str, ttl: float, page_size: int = 500, max_pages: int = 20
    ) -> list[dict]:
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            payload = self.http.get(path, {"pageSize": page_size, "pageNo": page}, cache_ttl=ttl)
            if not isinstance(payload, dict):
                raise ApiError(PROVIDER, f"respuesta inesperada en {path}")
            items.extend(payload.get("data") or [])
            if not payload.get("hasNextPage"):
                break
        else:
            log.warning("tennis_max_pages_reached", extra={"path": path, "pages": max_pages})
        return items

    @staticmethod
    def _check_tour(tour: str) -> str:
        if tour.lower() not in TOURS:
            raise ValueError(f"tour inválido: {tour}")
        return tour.lower()

    def tournaments(self, tour: str, year: int) -> dict[int, TournamentInfo]:
        tour = self._check_tour(tour)
        items = self._paged(f"/tennis/v2/{tour}/tournament/calendar/{year}", TTL_CALENDAR)
        return {info.id: info for info in map(parse_tournament, items)}

    def fixtures(self, tour: str, day: date) -> list[dict]:
        tour = self._check_tour(tour)
        return self._paged(f"/tennis/v2/{tour}/fixtures/{day.isoformat()}", TTL_FIXTURES)

    def results(self, tour: str, start: date, end: date, today: date) -> list[dict]:
        tour = self._check_tour(tour)
        # Días ya cerrados no cambian: caché larga. Si incluye hoy/ayer, caché corta.
        ttl = TTL_CLOSED_RESULTS if end < today - timedelta(days=1) else TTL_FIXTURES
        path = f"/tennis/v2/{tour}/results/{start.isoformat()}/{end.isoformat()}"
        return self._paged(path, ttl, max_pages=40)

    def rankings(self, tour: str) -> dict[int, int]:
        tour = self._check_tour(tour)
        payload = self.http.get(
            f"/tennis/v2/{tour}/ranking/singles", {"pageSize": 500}, cache_ttl=TTL_RANKING
        )
        return {
            int(r["player"]["id"]): int(r["position"])
            for r in (payload.get("data") or [])
            if r.get("player") and r.get("position")
        }
