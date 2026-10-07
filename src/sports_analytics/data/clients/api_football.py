"""Cliente de API-Football v3 (https://v3.football.api-sports.io).

Estrategia para no agotar la cuota diaria (100 llamadas en el plan gratuito):

* ``current_leagues()``: UNA llamada devuelve todas las ligas con su temporada
  actual y su objeto ``coverage``; se cachea 24 h. Sirve para verificar IDs y
  cobertura antes de pedir datos dependientes de liga/temporada.
* ``fixtures_by_date()``: UNA llamada trae todos los partidos del día (de todas
  las ligas); el filtro de competiciones se aplica después, en local.
* ``fixtures_by_league_season()``: una llamada por liga-temporada con todos sus
  partidos (resultados incluidos). Se usa para cargar histórico; se cachea.
* ``fixture_statistics()``: una llamada por partido (córners, tiros, posesión).
  Es la más cara: el pipeline la limita con un tope por ejecución.

Errores de plan (p. ej. temporada no incluida) llegan con HTTP 200 y el campo
``errors`` relleno; se convierten en ``ApiError`` explícitos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError, HttpApiClient
from sports_analytics.core.logging import get_logger, register_secret
from sports_analytics.data.schemas import FootballMatch, MatchStatus

log = get_logger(__name__)

PROVIDER = "api_football"

# Códigos de estado de API-Football -> estado normalizado
_STATUS = {
    **dict.fromkeys(["TBD", "NS"], MatchStatus.SCHEDULED),
    **dict.fromkeys(["1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"], MatchStatus.LIVE),
    **dict.fromkeys(["FT", "AET", "PEN"], MatchStatus.FINISHED),
    "PST": MatchStatus.POSTPONED,
    # AWD (adjudicado) y WO (walkover) no reflejan juego real: se excluyen del modelado
    **dict.fromkeys(["CANC", "ABD", "AWD", "WO"], MatchStatus.CANCELLED),
}

TTL_LEAGUES = 24 * 3600
TTL_FINISHED = 30 * 24 * 3600  # estadísticas de partidos terminados no cambian
TTL_SEASON = 6 * 3600


@dataclass(frozen=True)
class LeagueCoverage:
    league_id: int
    name: str
    country: str
    season: int
    season_start: date | None
    season_end: date | None
    fixtures_events: bool
    fixtures_statistics: bool
    lineups: bool
    standings: bool
    injuries: bool
    players: bool


@dataclass(frozen=True)
class FixtureStats:
    fixture_id: str
    home_corners: int | None
    away_corners: int | None
    home_shots: int | None
    away_shots: int | None
    home_possession: float | None
    away_possession: float | None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_xg: float | None = None  # expected_goals: solo en ligas con cobertura
    away_xg: float | None = None


def _check_errors(payload: dict[str, Any], path: str) -> None:
    errors = payload.get("errors")
    if errors:  # puede ser lista vacía o dict con mensajes
        msg = (
            "; ".join(f"{k}: {v}" for k, v in errors.items())
            if isinstance(errors, dict)
            else str(errors)
        )
        raise ApiError(PROVIDER, f"{path}: {msg}")


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def parse_fixture(item: dict[str, Any], competition_key: str) -> FootballMatch:
    fx, league, teams = item["fixture"], item["league"], item["teams"]
    status = _STATUS.get(fx["status"]["short"], MatchStatus.UNKNOWN)
    # Para modelar 1X2 y goles se usa el marcador a los 90' (score.fulltime);
    # 'goals' incluye la prórroga.
    fulltime = (item.get("score") or {}).get("fulltime") or {}
    hg = fulltime.get("home") if fulltime.get("home") is not None else item["goals"]["home"]
    ag = fulltime.get("away") if fulltime.get("away") is not None else item["goals"]["away"]
    return FootballMatch(
        match_id=str(fx["id"]),
        competition_key=competition_key,
        season=league.get("season"),
        home_team=str(teams["home"]["id"]),
        away_team=str(teams["away"]["id"]),
        home_name=teams["home"]["name"],
        away_name=teams["away"]["name"],
        kickoff_utc=datetime.fromisoformat(fx["date"]),
        status=status,
        home_goals=hg if status == MatchStatus.FINISHED else None,
        away_goals=ag if status == MatchStatus.FINISHED else None,
    )


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def parse_statistics(fixture_id: str, payload: list[dict[str, Any]]) -> FixtureStats | None:
    """La respuesta trae [local, visitante] con listas {type, value}."""
    if len(payload) != 2:
        return None

    def stat(block: dict[str, Any], name: str) -> Any:
        for s in block.get("statistics", []):
            if s.get("type", "").lower() == name:
                return s.get("value")
        return None

    home, away = payload
    return FixtureStats(
        fixture_id=fixture_id,
        home_corners=_to_int(stat(home, "corner kicks")),
        away_corners=_to_int(stat(away, "corner kicks")),
        home_shots=_to_int(stat(home, "total shots")),
        away_shots=_to_int(stat(away, "total shots")),
        home_possession=_to_int(stat(home, "ball possession")),
        away_possession=_to_int(stat(away, "ball possession")),
        home_shots_on_target=_to_int(stat(home, "shots on goal")),
        away_shots_on_target=_to_int(stat(away, "shots on goal")),
        home_xg=_to_float(stat(home, "expected_goals")),
        away_xg=_to_float(stat(away, "expected_goals")),
    )


class ApiFootballClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=None,
    ):
        if settings.api_football_key is None:
            raise ApiError(PROVIDER, "API_FOOTBALL_KEY no configurada")
        key = settings.api_football_key.get_secret_value()
        register_secret(key)
        kwargs = {"sleep": sleep} if sleep else {}
        self.http = HttpApiClient(
            provider=PROVIDER,
            base_url=settings.api_football_base_url,
            headers={"x-apisports-key": key},
            daily_limit=settings.api_football_daily_limit,
            cache_dir=Path(settings.cache_dir),
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            min_interval=settings.api_football_min_interval,
            transport=transport,
            **kwargs,
        )
        self.timezone = settings.app_timezone

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None, ttl: float = 0) -> Any:
        payload = self.http.get(path, params, cache_ttl=ttl)
        _check_errors(payload, path)
        return payload.get("response", [])

    # ---------------------------------------------------------------- cuenta

    def status(self) -> dict[str, Any]:
        """Plan y consumo de la cuenta (no consume cuota en API-Football)."""
        payload = self.http.get("/status")
        _check_errors(payload, "/status")
        resp = payload.get("response") or {}
        return {
            "plan": (resp.get("subscription") or {}).get("plan"),
            "active": (resp.get("subscription") or {}).get("active"),
            "requests_current": (resp.get("requests") or {}).get("current"),
            "requests_limit": (resp.get("requests") or {}).get("limit_day"),
        }

    # ---------------------------------------------------------------- ligas

    def current_leagues(self) -> dict[int, LeagueCoverage]:
        items = self._get("/leagues", {"current": "true"}, ttl=TTL_LEAGUES)
        out: dict[int, LeagueCoverage] = {}
        for item in items:
            league, country = item["league"], item.get("country") or {}
            seasons = [s for s in item.get("seasons", []) if s.get("current")]
            if not seasons:
                continue
            s = seasons[0]
            cov = s.get("coverage") or {}
            fx = cov.get("fixtures") or {}
            out[league["id"]] = LeagueCoverage(
                league_id=league["id"],
                name=league.get("name", ""),
                country=country.get("name", ""),
                season=s["year"],
                season_start=date.fromisoformat(s["start"]) if s.get("start") else None,
                season_end=date.fromisoformat(s["end"]) if s.get("end") else None,
                fixtures_events=bool(fx.get("events")),
                fixtures_statistics=bool(fx.get("statistics_fixtures")),
                lineups=bool(fx.get("lineups")),
                standings=bool(cov.get("standings")),
                injuries=bool(cov.get("injuries")),
                players=bool(cov.get("players")),
            )
        return out

    # -------------------------------------------------------------- partidos

    def fixtures_by_date(self, day: date) -> list[dict[str, Any]]:
        """Todos los partidos del día local (todas las ligas). Sin filtrar."""
        return self._get("/fixtures", {"date": day.isoformat(), "timezone": self.timezone}, ttl=900)

    def fixtures_by_league_season(self, league_id: int, season: int) -> list[dict[str, Any]]:
        return self._get("/fixtures", {"league": league_id, "season": season}, ttl=TTL_SEASON)

    def fixture_odds(self, fixture_id: str, bet_id: int = 1) -> list[dict[str, Any]]:
        """Precios pre-partido de un partido (sin caché: cambian hasta el inicio).
        API-Football solo los conserva unos días después del partido."""
        return self._get("/odds", {"fixture": fixture_id, "bet": bet_id})

    def fixture_statistics(self, fixture_id: str) -> FixtureStats | None:
        items = self._get("/fixtures/statistics", {"fixture": fixture_id}, ttl=TTL_FINISHED)
        return parse_statistics(fixture_id, items)
