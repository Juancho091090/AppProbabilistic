"""Ingesta incremental de histórico y resultados hacia PostgreSQL.

Cada competición / ventana se procesa de forma aislada: un fallo se registra en el
``IngestReport`` y la ingesta continúa con el resto (no se aborta todo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError, BudgetExceeded
from sports_analytics.core.logging import get_logger
from sports_analytics.core.timeutils import local_day_bounds_utc
from sports_analytics.data.clients.api_football import (
    ApiFootballClient,
    LeagueCoverage,
    parse_fixture,
)
from sports_analytics.data.clients.tennis_api import (
    TennisApiClient,
    TournamentInfo,
    is_doubles,
    is_main_tour,
    parse_tournament,
    to_tennis_match,
)
from sports_analytics.data.filters import filter_football_league
from sports_analytics.db import repository as repo
from sports_analytics.db.models import DataSource

log = get_logger(__name__)


@dataclass
class IngestReport:
    """Resumen de una ingesta: qué se cargó y qué falló (alimenta el informe)."""

    loaded: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)  # competición/ventana -> error
    calls: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int) -> None:
        self.loaded[key] = self.loaded.get(key, 0) + n


# ------------------------------------------------------------------ fútbol


def sync_football(
    session: Session,
    client: ApiFootballClient,
    config: AppConfig,
    settings: Settings,
    report: IngestReport,
    include_stats: bool = True,
) -> dict[int, LeagueCoverage]:
    """Refresca temporada actual de cada competición y carga temporadas previas una vez.

    ``include_stats=False`` permite al pipeline diario cargar primero las estadísticas
    de los equipos que juegan hoy (``prioritize_team_stats``) y después el resto.
    """
    comp_ids = repo.ensure_competitions(session, config)
    try:
        coverage = client.current_leagues()
    except ApiError as exc:
        report.failures["football:leagues"] = str(exc)
        return {}
    loaded = repo.football_seasons_loaded(session)

    for comp in config.competitions.football:
        cov = coverage.get(comp.api_football_id)
        if cov is None:
            report.failures[comp.key] = "sin temporada actual en la API"
            continue
        seasons = [cov.season - i for i in range(settings.football_history_seasons)]
        for season in seasons:
            if season != cov.season and (comp.key, season) in loaded:
                continue  # temporada pasada ya cargada: no cambia
            try:
                items = client.fixtures_by_league_season(comp.api_football_id, season)
            except BudgetExceeded:
                raise
            except ApiError as exc:
                report.failures[f"{comp.key}:{season}"] = str(exc)
                log.warning(
                    "football_season_failed", extra={"competition": comp.key, "season": season}
                )
                continue
            matches = [parse_fixture(it, comp.key) for it in items]
            n = repo.upsert_football_matches(session, matches, comp_ids)
            report.add(f"football:{comp.key}", n)
        session.commit()  # progreso persistente aunque algo falle después

    if include_stats:
        sync_football_stats(session, client, config, settings, coverage, report)
    report.calls["api_football"] = client.http.calls_made
    return coverage


def stats_competition_keys(config: AppConfig, coverage: dict[int, LeagueCoverage]) -> list[str]:
    return [
        c.key
        for c in config.competitions.football
        if coverage.get(c.api_football_id) and coverage[c.api_football_id].fixtures_statistics
    ]


def prioritize_team_stats(
    session: Session,
    client: ApiFootballClient,
    team_ids: set[str],
    competition_keys: list[str],
    settings: Settings,
    report: IngestReport,
) -> int:
    """Estadísticas (córners) de los últimos N partidos de los equipos que juegan hoy.

    Garantiza que el modelo de córners tenga datos para los partidos del informe aunque
    la carga general de estadísticas no haya terminado.
    """
    pending = repo.recent_matches_missing_stats_for_teams(
        session, team_ids, competition_keys, settings.football_priority_stats_per_team
    )
    fetch_football_stats(session, client, pending, report)
    report.add("football:statistics_priority", len(pending))
    return len(pending)


def sync_football_stats(
    session: Session,
    client: ApiFootballClient,
    config: AppConfig,
    settings: Settings,
    coverage: dict[int, LeagueCoverage],
    report: IngestReport,
) -> None:
    keys = stats_competition_keys(config, coverage)
    pending = repo.football_matches_missing_stats(session, keys, settings.football_stats_per_run)
    fetch_football_stats(session, client, pending, report)
    report.add("football:statistics", len(pending))


def fetch_football_stats(
    session: Session, client: ApiFootballClient, pending: list[str], report: IngestReport
) -> None:
    batch: list[dict[str, Any]] = []
    for ext_id in pending:
        try:
            st = client.fixture_statistics(ext_id)
        except BudgetExceeded:
            break
        except ApiError as exc:
            report.failures[f"stats:{ext_id}"] = str(exc)
            continue
        if st is None or st.home_corners is None or st.away_corners is None:
            batch.append({"external_id": ext_id, "available": False})
        else:
            batch.append(
                {
                    "external_id": ext_id,
                    "available": True,
                    "home_corners": st.home_corners,
                    "away_corners": st.away_corners,
                    "home_shots": st.home_shots,
                    "away_shots": st.away_shots,
                    "home_possession": st.home_possession,
                    "away_possession": st.away_possession,
                }
            )
        if len(batch) >= 200:
            repo.upsert_football_statistics(session, batch)
            session.commit()
            batch = []
    repo.upsert_football_statistics(session, batch)
    session.commit()


def football_fixtures_today(
    client: ApiFootballClient, config: AppConfig, day: date, report: IngestReport
):
    """Partidos del día en competiciones autorizadas: (FootballMatch, nombre competición)."""
    items = client.fixtures_by_date(day)
    report.add("football:fixtures_found", len(items))
    out = []
    for it in items:
        res = filter_football_league(it["league"]["id"], it["league"]["name"], config.competitions)
        if res.included:
            out.append((parse_fixture(it, res.competition.key), res.competition.name))
    report.add("football:fixtures_kept", len(out))
    return out


# ------------------------------------------------------------------- tenis

TENNIS_STATE_PROVIDER = "tennis_api"


def _tennis_state(session: Session) -> dict[str, Any]:
    row = session.scalar(select(DataSource).where(DataSource.provider == TENNIS_STATE_PROVIDER))
    return dict(row.details or {}) if row else {}


def _save_tennis_state(session: Session, state: dict[str, Any], calls: int) -> None:
    repo.record_data_source(session, TENNIS_STATE_PROVIDER, ok=True, calls=calls, details=state)


def _main_tour_matches(
    items: list[dict], tour: str, config: AppConfig, infos: dict[int, TournamentInfo]
):
    matches, meta = [], {}
    for it in items:
        if is_doubles(it):
            continue
        t = it.get("tournament")
        info = (
            parse_tournament(t)
            if isinstance(t, dict) and t.get("id")
            else infos.get(it.get("tournamentId"))
        )
        if not is_main_tour(info, config.tennis):
            continue
        m = to_tennis_match(it, tour, info, grand_slam_rank_id=config.tennis.grand_slam_rank_id)
        matches.append(m)
        meta[m.match_id] = {"tournament_external_id": str(info.id), "rank_id": info.rank_id}
    return matches, meta


def sync_tennis(
    session: Session,
    client: TennisApiClient,
    config: AppConfig,
    settings: Settings,
    today: date,
    report: IngestReport,
) -> None:
    """Refresca días recientes y avanza el histórico hacia atrás según la cuota disponible."""
    state = _tennis_state(session)
    start_calls = client.http.calls_made
    horizon = today - timedelta(days=settings.tennis_history_days)
    for tour in ("atp", "wta"):
        # 1) Refresco reciente (resultados de ayer y días previos)
        recent_start = today - timedelta(days=settings.tennis_refresh_days)
        try:
            items = client.results(tour, recent_start, today - timedelta(days=1), today)
            matches, meta = _main_tour_matches(items, tour, config, {})
            report.add(f"tennis:{tour}:recent", repo.upsert_tennis_matches(session, matches, meta))
            session.commit()
        except BudgetExceeded:
            break
        except ApiError as exc:
            report.failures[f"tennis:{tour}:recent"] = str(exc)

    # 2) Backfill alternando circuitos, ventana a ventana hacia atrás
    budget_end = client.http.calls_made + settings.tennis_backfill_calls_per_run
    progressed = True
    while progressed and client.http.calls_made < budget_end:
        progressed = False
        for tour in ("atp", "wta"):
            key = f"{tour}_backfilled_until"
            oldest = (
                date.fromisoformat(state[key])
                if key in state
                else today - timedelta(days=settings.tennis_refresh_days)
            )
            if oldest <= horizon or client.http.calls_made >= budget_end:
                continue
            w_end = oldest - timedelta(days=1)
            w_start = max(horizon, oldest - timedelta(days=settings.tennis_window_days))
            try:
                items = client.results(tour, w_start, w_end, today)
            except BudgetExceeded:
                budget_end = 0
                break
            except ApiError as exc:
                report.failures[f"tennis:{tour}:{w_start}"] = str(exc)
                continue
            matches, meta = _main_tour_matches(items, tour, config, {})
            report.add(
                f"tennis:{tour}:backfill", repo.upsert_tennis_matches(session, matches, meta)
            )
            state[key] = w_start.isoformat()
            _save_tennis_state(session, state, client.http.calls_made - start_calls)
            session.commit()
            progressed = True
    report.calls["tennis_api"] = client.http.calls_made - start_calls


def tennis_fixtures_today(
    client: TennisApiClient,
    config: AppConfig,
    day: date,
    report: IngestReport,
    session: Session | None = None,
    tz: ZoneInfo | None = None,
):
    """Partidos del día LOCAL del circuito principal, con superficie y ranking actual.

    La Tennis API agrupa los fixtures por fecha UTC. El día local (p. ej. Bogotá, UTC−5)
    abarca dos fechas UTC, así que se piden ambas y se filtra por los límites locales.
    Sin esto se pierden, por ejemplo, los partidos asiáticos de la noche local.
    """
    out = []
    start_utc, end_utc = local_day_bounds_utc(day, tz or ZoneInfo("UTC"))
    utc_dates = sorted({start_utc.date(), (end_utc - timedelta(microseconds=1)).date()})
    for tour in ("atp", "wta"):
        try:
            seen: dict[Any, dict] = {}
            for d in utc_dates:
                for it in client.fixtures(tour, d):
                    seen[it.get("matchId") or it.get("id")] = it
            items = list(seen.values())
            infos = client.tournaments(tour, day.year)
        except BudgetExceeded:
            raise
        except ApiError as exc:
            report.failures[f"tennis:{tour}:fixtures"] = str(exc)
            continue
        try:
            ranks = client.rankings(tour)
        except ApiError as exc:  # el ranking es opcional: se continúa sin él
            report.failures[f"tennis:{tour}:ranking"] = str(exc)
            ranks = {}
        if session is not None:  # respaldo: torneos ya vistos en resultados guardados
            for tid, k in repo.tennis_tournaments_known(session, tour).items():
                infos.setdefault(
                    tid,
                    TournamentInfo(
                        id=tid,
                        name=k["name"],
                        rank_id=k["rank_id"],
                        court_id=None,
                        surface=k["surface"],
                        indoor=False,
                        tier=k["category"],
                        start=None,
                    ),
                )
        report.add(f"tennis:{tour}:fixtures_found", len(items))
        for it in items:
            if is_doubles(it):
                continue
            info = infos.get(it.get("tournamentId"))
            if not is_main_tour(info, config.tennis):
                continue
            m = to_tennis_match(
                it, tour, info, ranks, grand_slam_rank_id=config.tennis.grand_slam_rank_id
            )
            if start_utc <= m.kickoff_utc < end_utc:
                out.append(m)
        report.add(f"tennis:{tour}:fixtures_kept", sum(1 for m in out if m.tour == tour.upper()))
    return out
