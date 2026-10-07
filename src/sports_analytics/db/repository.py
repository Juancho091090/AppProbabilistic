"""Acceso a datos. Todas las escrituras son upserts idempotentes (re-ejecutar no duplica)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from sports_analytics.config.loader import AppConfig
from sports_analytics.core.timeutils import ensure_utc, now_utc
from sports_analytics.data.schemas import FootballMatch, MatchStatus, TennisMatch
from sports_analytics.db.models import (
    Competition,
    DataSource,
    FootballMatchRow,
    FootballStatistics,
    MarketOdds,
    MarketOddsCheck,
    PipelineRun,
    Player,
    PredictionResult,
    PredictionRow,
    Rating,
    Team,
    TennisMatchRow,
)
from sports_analytics.market.odds import OddsQuote
from sports_analytics.models.outputs import MatchForecast

FOOTBALL_PROVIDER = "api_football"
TENNIS_PROVIDER = "tennis_api"


def _upsert(session: Session, model, values: list[dict], conflict: list[str], update: list[str]):
    if not values:
        return
    stmt = pg_insert(model).values(values)
    if update:
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict, set_={c: stmt.excluded[c] for c in update}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
    session.execute(stmt)


# ------------------------------------------------------------- catálogo


def ensure_competitions(session: Session, config: AppConfig) -> dict[str, int]:
    rows = [
        {
            "key": c.key,
            "sport": "football",
            "name": c.name,
            "country": c.country,
            "provider": FOOTBALL_PROVIDER,
            "external_id": str(c.api_football_id),
            "kind": c.kind.value,
        }
        for c in config.competitions.football
    ] + [
        {
            "key": t.lower(),
            "sport": "tennis",
            "name": f"{t} Tour",
            "country": None,
            "provider": TENNIS_PROVIDER,
            "external_id": t.lower(),
            "kind": "tour",
        }
        for t in config.tennis.tours
    ]
    _upsert(session, Competition, rows, ["key"], ["name", "country", "external_id", "kind"])
    return dict(session.execute(select(Competition.key, Competition.id)).all())


def _ensure_entities(
    session: Session, model, provider: str, items: dict[str, dict]
) -> dict[str, int]:
    """items: external_id -> columnas. Devuelve external_id -> id interno."""
    if not items:
        return {}
    rows = [{"provider": provider, "external_id": ext, **cols} for ext, cols in items.items()]
    update = [k for k in rows[0] if k not in ("provider", "external_id")]
    _upsert(session, model, rows, ["provider", "external_id"], update)
    found = session.execute(
        select(model.external_id, model.id).where(
            model.provider == provider, model.external_id.in_(list(items))
        )
    ).all()
    return dict(found)


# --------------------------------------------------------------- fútbol


def upsert_football_matches(
    session: Session, matches: Sequence[FootballMatch], competition_ids: dict[str, int]
) -> int:
    teams: dict[str, dict] = {}
    for m in matches:
        teams[m.home_team] = {"name": m.display_home}
        teams[m.away_team] = {"name": m.display_away}
    team_ids = _ensure_entities(session, Team, FOOTBALL_PROVIDER, teams)
    rows = [
        {
            "provider": FOOTBALL_PROVIDER,
            "external_id": m.match_id,
            "competition_id": competition_ids[m.competition_key],
            "season": m.season,
            "kickoff_utc": m.kickoff_utc,
            "home_team_id": team_ids[m.home_team],
            "away_team_id": team_ids[m.away_team],
            "status": m.status.value,
            "neutral_venue": m.neutral_venue,
            "home_goals": m.home_goals,
            "away_goals": m.away_goals,
        }
        for m in matches
        if m.competition_key in competition_ids
    ]
    for chunk in range(0, len(rows), 1000):
        _upsert(
            session,
            FootballMatchRow,
            rows[chunk : chunk + 1000],
            ["provider", "external_id"],
            ["kickoff_utc", "status", "home_goals", "away_goals", "season", "neutral_venue"],
        )
    return len(rows)


def upsert_football_statistics(session: Session, stats: Iterable[dict[str, Any]]) -> int:
    """stats: dicts con external_id del partido + columnas de estadísticas."""
    stats = list(stats)
    if not stats:
        return 0
    ids = dict(
        session.execute(
            select(FootballMatchRow.external_id, FootballMatchRow.id).where(
                FootballMatchRow.provider == FOOTBALL_PROVIDER,
                FootballMatchRow.external_id.in_([s["external_id"] for s in stats]),
            )
        ).all()
    )
    # Un INSERT multi-fila exige las mismas columnas en todas las filas: las filas sin
    # datos (available=False) se completan con None para las columnas ausentes.
    cols = sorted({k for s in stats for k in s if k != "external_id"})
    rows = [
        {"match_id": ids[s["external_id"]], **{c: s.get(c) for c in cols}}
        for s in stats
        if s["external_id"] in ids
    ]
    _upsert(session, FootballStatistics, rows, ["match_id"], cols)
    return len(rows)


def football_matches_missing_stats(
    session: Session, competition_keys: Sequence[str], limit: int
) -> list[str]:
    """Partidos terminados sin fila de estadísticas, los más recientes primero."""
    q = (
        select(FootballMatchRow.external_id)
        .join(Competition, Competition.id == FootballMatchRow.competition_id)
        .outerjoin(FootballStatistics, FootballStatistics.match_id == FootballMatchRow.id)
        .where(
            FootballMatchRow.status == MatchStatus.FINISHED.value,
            FootballStatistics.id.is_(None),
            Competition.key.in_(list(competition_keys)),
        )
        .order_by(FootballMatchRow.kickoff_utc.desc())
        .limit(limit)
    )
    return list(session.scalars(q))


def recent_matches_missing_stats_for_teams(
    session: Session,
    team_external_ids: Iterable[str],
    competition_keys: Sequence[str],
    per_team: int,
) -> list[str]:
    """De los últimos ``per_team`` partidos terminados de cada equipo, los que no tienen
    estadísticas aún (sin duplicados, más recientes primero)."""
    out: list[str] = []
    seen: set[str] = set()
    for ext in team_external_ids:
        team_id = session.scalar(
            select(Team.id).where(Team.provider == FOOTBALL_PROVIDER, Team.external_id == ext)
        )
        if team_id is None:
            continue
        q = (
            select(FootballMatchRow.external_id, FootballStatistics.id)
            .join(Competition, Competition.id == FootballMatchRow.competition_id)
            .outerjoin(FootballStatistics, FootballStatistics.match_id == FootballMatchRow.id)
            .where(
                FootballMatchRow.status == MatchStatus.FINISHED.value,
                (FootballMatchRow.home_team_id == team_id)
                | (FootballMatchRow.away_team_id == team_id),
                Competition.key.in_(list(competition_keys)),
            )
            .order_by(FootballMatchRow.kickoff_utc.desc())
            .limit(per_team)
        )
        for match_ext, stats_id in session.execute(q):
            if stats_id is None and match_ext not in seen:
                seen.add(match_ext)
                out.append(match_ext)
    return out


def load_football_history(session: Session, since: datetime) -> list[FootballMatch]:
    home, away = aliased(Team), aliased(Team)
    q = (
        select(FootballMatchRow, Competition.key, home, away, FootballStatistics)
        .join(Competition, Competition.id == FootballMatchRow.competition_id)
        .join(home, home.id == FootballMatchRow.home_team_id)
        .join(away, away.id == FootballMatchRow.away_team_id)
        .outerjoin(FootballStatistics, FootballStatistics.match_id == FootballMatchRow.id)
        .where(FootballMatchRow.kickoff_utc >= since)
        .order_by(FootballMatchRow.kickoff_utc)
    )
    out = []
    for row, comp_key, h, a, st in session.execute(q):
        has_stats = st is not None and st.available
        out.append(
            FootballMatch(
                match_id=row.external_id,
                competition_key=comp_key,
                season=row.season,
                home_team=h.external_id,
                away_team=a.external_id,
                home_name=h.name,
                away_name=a.name,
                kickoff_utc=row.kickoff_utc,
                status=MatchStatus(row.status),
                neutral_venue=row.neutral_venue,
                home_goals=row.home_goals,
                away_goals=row.away_goals,
                home_corners=st.home_corners if has_stats else None,
                away_corners=st.away_corners if has_stats else None,
                home_shots=st.home_shots if has_stats else None,
                away_shots=st.away_shots if has_stats else None,
                home_possession=st.home_possession if has_stats else None,
                away_possession=st.away_possession if has_stats else None,
                home_shots_on_target=st.home_shots_on_target if has_stats else None,
                away_shots_on_target=st.away_shots_on_target if has_stats else None,
                home_xg=st.home_xg if has_stats else None,
                away_xg=st.away_xg if has_stats else None,
            )
        )
    return out


def football_seasons_loaded(session: Session) -> set[tuple[str, int]]:
    q = (
        select(Competition.key, FootballMatchRow.season)
        .join(Competition, Competition.id == FootballMatchRow.competition_id)
        .distinct()
    )
    return {(k, s) for k, s in session.execute(q) if s is not None}


# ----------------------------------------------------------------- tenis


def upsert_tennis_matches(
    session: Session, matches: Sequence[TennisMatch], meta: dict[str, dict] | None = None
) -> int:
    """meta: match_id -> {"tournament_external_id", "rank_id"} (opcional)."""
    meta = meta or {}
    players: dict[str, dict] = {}
    for m in matches:
        players[m.player_a] = {"name": m.display_a, "tour": m.tour}
        players[m.player_b] = {"name": m.display_b, "tour": m.tour}
    ids = _ensure_entities(session, Player, TENNIS_PROVIDER, players)
    rows = [
        {
            "provider": TENNIS_PROVIDER,
            "external_id": m.match_id,
            "tour": m.tour,
            "tournament_external_id": meta.get(m.match_id, {}).get("tournament_external_id"),
            "tournament": m.tournament,
            "category": m.category,
            "rank_id": meta.get(m.match_id, {}).get("rank_id"),
            "surface": m.surface,
            "best_of": m.best_of,
            "kickoff_utc": m.kickoff_utc,
            "player_a_id": ids[m.player_a],
            "player_b_id": ids[m.player_b],
            "status": m.status.value,
            "winner": m.winner,
            "sets_a": m.sets_a,
            "sets_b": m.sets_b,
            "games_a": m.games_a,
            "games_b": m.games_b,
            "retired": m.retired,
            "rank_a": m.rank_a,
            "rank_b": m.rank_b,
        }
        for m in matches
    ]
    for chunk in range(0, len(rows), 1000):
        _upsert(
            session,
            TennisMatchRow,
            rows[chunk : chunk + 1000],
            ["provider", "external_id"],
            [
                "status",
                "winner",
                "sets_a",
                "sets_b",
                "games_a",
                "games_b",
                "retired",
                "kickoff_utc",
                "surface",
                "best_of",
                "tournament",
                "category",
                "rank_id",
            ],
        )
    return len(rows)


def load_tennis_history(session: Session, since: datetime) -> list[TennisMatch]:
    pa, pb = aliased(Player), aliased(Player)
    q = (
        select(TennisMatchRow, pa, pb)
        .join(pa, pa.id == TennisMatchRow.player_a_id)
        .join(pb, pb.id == TennisMatchRow.player_b_id)
        .where(TennisMatchRow.kickoff_utc >= since)
        .order_by(TennisMatchRow.kickoff_utc)
    )
    return [
        TennisMatch(
            match_id=r.external_id,
            tour=r.tour,
            tournament=r.tournament,
            category=r.category or "",
            surface=r.surface,
            best_of=r.best_of,
            player_a=a.external_id,
            player_b=b.external_id,
            player_a_name=a.name,
            player_b_name=b.name,
            kickoff_utc=r.kickoff_utc,
            status=MatchStatus(r.status),
            winner=r.winner,
            sets_a=r.sets_a,
            sets_b=r.sets_b,
            games_a=r.games_a,
            games_b=r.games_b,
            retired=r.retired,
            rank_a=r.rank_a,
            rank_b=r.rank_b,
        )
        for r, a, b in session.execute(q)
    ]


def tennis_tournaments_known(session: Session, tour: str) -> dict[int, dict]:
    """Torneos vistos en resultados guardados: id externo -> nivel, superficie, nombre."""
    q = (
        select(
            TennisMatchRow.tournament_external_id,
            TennisMatchRow.rank_id,
            TennisMatchRow.surface,
            TennisMatchRow.tournament,
            TennisMatchRow.category,
        )
        .where(
            TennisMatchRow.tour == tour.upper(), TennisMatchRow.tournament_external_id.is_not(None)
        )
        .distinct()
    )
    return {
        int(tid): {"rank_id": rank, "surface": surface, "name": name, "category": cat or ""}
        for tid, rank, surface, name, cat in session.execute(q)
        if tid and tid.isdigit()
    }


def tennis_loaded_days(session: Session, tour: str) -> set[date]:
    q = select(TennisMatchRow.kickoff_utc).where(TennisMatchRow.tour == tour.upper())
    return {ts.date() for ts in session.scalars(q)}


# ------------------------------------------------------------ ejecuciones


def start_run(session: Session, run_type: str, run_date: date) -> PipelineRun:
    run = PipelineRun(
        run_type=run_type, run_date=run_date, started_at=now_utc(), errors=[], details={}
    )
    session.add(run)
    session.flush()
    return run


def report_already_sent(
    session: Session, run_date: date, exclude_run_id: int | None = None
) -> bool:
    q = select(PipelineRun.id).where(
        PipelineRun.run_type == "daily",
        PipelineRun.run_date == run_date,
        (PipelineRun.telegram_sent.is_(True)) | (PipelineRun.email_sent.is_(True)),
    )
    if exclude_run_id is not None:
        q = q.where(PipelineRun.id != exclude_run_id)
    return session.scalar(q.limit(1)) is not None


def finish_run(session: Session, run: PipelineRun, status: str) -> None:
    run.finished_at = now_utc()
    run.status = status
    run.duration_seconds = (run.finished_at - ensure_utc(run.started_at)).total_seconds()
    session.flush()


def record_data_source(
    session: Session,
    provider: str,
    *,
    ok: bool,
    calls: int,
    quota: str | None = None,
    error: str | None = None,
    details: dict | None = None,
) -> None:
    now = now_utc()
    values = {
        "provider": provider,
        "calls_last_run": calls,
        "quota_remaining": quota,
        "details": details or {},
        "last_success_at": now if ok else None,
        "last_error_at": None if ok else now,
        "last_error": None if ok else error,
    }
    stmt = pg_insert(DataSource).values(values)
    update = {
        "calls_last_run": stmt.excluded.calls_last_run,
        "quota_remaining": stmt.excluded.quota_remaining,
        "details": stmt.excluded.details,
    }
    if ok:
        update["last_success_at"] = stmt.excluded.last_success_at
    else:
        update["last_error_at"] = stmt.excluded.last_error_at
        update["last_error"] = stmt.excluded.last_error
    session.execute(stmt.on_conflict_do_update(index_elements=["provider"], set_=update))


# ------------------------------------------------------------ predicciones


def save_forecasts(
    session: Session, forecasts: Sequence[MatchForecast], run_id: int | None, prediction_date: date
) -> int:
    rows = []
    for f in forecasts:
        for r in f.records:
            rows.append(
                {
                    "run_id": run_id,
                    "sport": r.sport,
                    "competition": r.competition,
                    "competition_key": f.competition_key,
                    "event_id": r.event_id,
                    "kickoff_utc": f.kickoff_utc,
                    "market": r.market,
                    "event": r.event,
                    "line": r.line,
                    "line_key": "" if r.line is None else f"{r.line:g}",
                    "probability": r.probability,
                    "model": r.model,
                    "confidence": r.confidence,
                    "generated_at": r.generated_at,
                    "as_of": r.as_of,
                    "prediction_date": prediction_date,
                }
            )
    for chunk in range(0, len(rows), 1000):
        _upsert(
            session,
            PredictionRow,
            rows[chunk : chunk + 1000],
            ["sport", "event_id", "market", "event", "line_key", "model", "prediction_date"],
            ["probability", "confidence", "generated_at", "as_of", "run_id", "kickoff_utc"],
        )
    return len(rows)


def unsettled_predictions(session: Session, before: datetime) -> list[PredictionRow]:
    q = (
        select(PredictionRow)
        .outerjoin(PredictionResult, PredictionResult.prediction_id == PredictionRow.id)
        .where(PredictionResult.id.is_(None), PredictionRow.kickoff_utc < before)
    )
    return list(session.scalars(q))


def save_results(session: Session, results: Sequence[dict]) -> int:
    _upsert(
        session,
        PredictionResult,
        list(results),
        ["prediction_id"],
        ["outcome", "actual_value", "void_reason", "settled_at"],
    )
    return len(results)


def settled_predictions(
    session: Session, sport: str | None = None, since: date | None = None
) -> list[tuple[PredictionRow, PredictionResult]]:
    q = (
        select(PredictionRow, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == PredictionRow.id)
        .where(PredictionResult.outcome.is_not(None))
    )
    conds = []
    if sport:
        conds.append(PredictionRow.sport == sport)
    if since:
        conds.append(PredictionRow.prediction_date >= since)
    if conds:
        q = q.where(and_(*conds))
    return [(p, r) for p, r in session.execute(q)]


def save_ratings(session: Session, sport: str, rows: Sequence[dict], valid_from: datetime) -> None:
    values = [{"sport": sport, "valid_from": valid_from, **r} for r in rows]
    for chunk in range(0, len(values), 1000):
        _upsert(
            session,
            Rating,
            values[chunk : chunk + 1000],
            ["sport", "entity_key", "rating_type", "valid_from"],
            ["value", "matches", "entity_name"],
        )


# ------------------------------------------------------- mercado (benchmark)

VOID_MATCH_STATUSES = ("postponed", "cancelled")


def football_match_ids(
    session: Session, external_ids: Iterable[str]
) -> dict[str, tuple[int, datetime]]:
    """external_id → (id interno, inicio UTC)."""
    ext = list(set(external_ids))
    if not ext:
        return {}
    q = select(
        FootballMatchRow.external_id, FootballMatchRow.id, FootballMatchRow.kickoff_utc
    ).where(FootballMatchRow.provider == FOOTBALL_PROVIDER, FootballMatchRow.external_id.in_(ext))
    return {e: (i, ensure_utc(k)) for e, i, k in session.execute(q)}


def matches_needing_odds(session: Session, since: datetime, now: datetime, limit: int) -> list[str]:
    """Partidos ya iniciados desde ``since`` sin consulta *final* de precios (la última
    consulta se hizo antes del inicio o nunca): los más recientes primero."""
    q = (
        select(FootballMatchRow.external_id)
        .outerjoin(MarketOddsCheck, MarketOddsCheck.match_id == FootballMatchRow.id)
        .where(
            FootballMatchRow.provider == FOOTBALL_PROVIDER,
            FootballMatchRow.kickoff_utc >= since,
            FootballMatchRow.kickoff_utc < now,
            FootballMatchRow.status.not_in(VOID_MATCH_STATUSES),
            (MarketOddsCheck.match_id.is_(None)) | (MarketOddsCheck.final.is_(False)),
        )
        .order_by(FootballMatchRow.kickoff_utc.desc())
        .limit(limit)
    )
    return list(session.scalars(q))


def save_market_quotes(
    session: Session, match_id: int, quotes: Sequence[OddsQuote], fetched_at: datetime, final: bool
) -> int:
    rows = [
        {
            "match_id": match_id,
            "bookmaker_id": q.bookmaker_id,
            "bookmaker_name": q.bookmaker_name[:80],
            "market": "1x2",
            "odd_home": q.home,
            "odd_draw": q.draw,
            "odd_away": q.away,
            "source_updated_at": q.source_updated_at,
            "fetched_at": fetched_at,
        }
        for q in quotes
    ]
    _upsert(
        session,
        MarketOdds,
        rows,
        ["match_id", "bookmaker_id", "market"],
        ["bookmaker_name", "odd_home", "odd_draw", "odd_away", "source_updated_at", "fetched_at"],
    )
    _upsert(
        session,
        MarketOddsCheck,
        [
            {
                "match_id": match_id,
                "checked_at": fetched_at,
                "bookmakers": len(quotes),
                "final": final,
            }
        ],
        ["match_id"],
        ["checked_at", "bookmakers", "final"],
    )
    return len(rows)


def load_market_quotes(
    session: Session, external_ids: Iterable[str] | None = None, since: datetime | None = None
) -> dict[str, list[OddsQuote]]:
    """Precios 1X2 por partido (clave: external_id del partido)."""
    q = select(FootballMatchRow.external_id, MarketOdds).join(
        FootballMatchRow, FootballMatchRow.id == MarketOdds.match_id
    )
    if external_ids is not None:
        q = q.where(FootballMatchRow.external_id.in_(list(set(external_ids))))
    if since is not None:
        q = q.where(FootballMatchRow.kickoff_utc >= since)
    out: dict[str, list[OddsQuote]] = {}
    for ext, o in session.execute(q):
        out.setdefault(ext, []).append(
            OddsQuote(
                o.bookmaker_id,
                o.bookmaker_name,
                o.odd_home,
                o.odd_draw,
                o.odd_away,
                o.source_updated_at,
            )
        )
    return out
