"""Pipeline diario: ingesta → liquidación de resultados → modelos → informe → envío.

Diseño tolerante a fallos: cada etapa y cada competición se aíslan; los fallos se
acumulan como "incidencias" del informe y el estado final de la ejecución es
``success``, ``partial`` o ``failed`` (tabla pipeline_runs).
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger
from sports_analytics.core.timeutils import ensure_utc, local_today, now_utc, to_local
from sports_analytics.data.clients.api_football import ApiFootballClient
from sports_analytics.data.clients.tennis_api import TennisApiClient
from sports_analytics.data.schemas import FootballMatch, TennisMatch
from sports_analytics.db import repository as repo
from sports_analytics.db.session import session_scope
from sports_analytics.models.football.predictor import FootballPredictor
from sports_analytics.models.outputs import MatchForecast
from sports_analytics.models.tennis.predictor import TennisPredictor
from sports_analytics.pipeline import ingest
from sports_analytics.pipeline.calibration_state import load_calibration_state
from sports_analytics.pipeline.results import (
    latest_global_metrics,
    refresh_live_metrics,
    settle_predictions,
)
from sports_analytics.reporting.builder import markdown_to_telegram_html, render_markdown
from sports_analytics.reporting.claude_narrator import ClaudeNarrator
from sports_analytics.reporting.payload import CompetitionSection, DailyReportPayload

log = get_logger(__name__)

FOOTBALL_HISTORY = timedelta(days=3 * 365)
TENNIS_HISTORY = timedelta(days=3 * 365)


@dataclass
class Services:
    """Dependencias inyectables (en tests se sustituyen por mocks)."""

    football: ApiFootballClient | None = None
    tennis: TennisApiClient | None = None
    narrator: ClaudeNarrator | None = None
    telegram: Any | None = None
    email: Any | None = None


@dataclass
class DailyOutcome:
    run_id: int | None
    status: str
    payload: DailyReportPayload | None
    markdown: str = ""
    telegram_sent: bool = False
    email_sent: bool = False
    issues: list[str] = field(default_factory=list)


def build_services(settings: Settings) -> Services:
    svc = Services()
    if settings.api_football_key:
        svc.football = ApiFootballClient(settings)
    if settings.tennis_api_key:
        svc.tennis = TennisApiClient(settings)
    svc.narrator = ClaudeNarrator(settings)
    if settings.telegram_enabled:
        from sports_analytics.reporting.telegram import TelegramService

        svc.telegram = TelegramService(settings)
    if settings.email_enabled:
        from sports_analytics.reporting.email import EmailService

        svc.email = EmailService(settings)
    return svc


def _predict_football(
    session,
    settings,
    config,
    fixtures: list[tuple[FootballMatch, str]],
    as_of: datetime,
    report: ingest.IngestReport,
    payload: DailyReportPayload,
) -> list[MatchForecast]:
    mcfg = config.models
    history = repo.load_football_history(session, as_of - FOOTBALL_HISTORY)
    state = load_calibration_state(
        session,
        "football",
        mcfg.version,
        list(mcfg.football["ensemble_weights_1x2"]),
        mcfg.football["ensemble_weights_1x2"],
        mcfg.calibration,
        as_of,
    )
    predictor = FootballPredictor(
        mcfg.football,
        mcfg.recency,
        mcfg.confidence,
        mcfg.version,
        weights=state.weights,
        calibrator=state.calibrator,
    ).fit(history, as_of)
    log.info(
        "football_models_fitted",
        extra={"history": len(history), "calibrated_events": state.n_events},
    )
    teams = {m.home_team: m.display_home for m, _ in fixtures} | {
        m.away_team: m.display_away for m, _ in fixtures
    }
    repo.save_ratings(
        session,
        "football",
        [
            {
                "entity_key": k,
                "entity_name": name,
                "rating_type": "elo",
                "value": predictor.elo.rating(k),
                "matches": predictor.elo.games_played.get(k, 0),
            }
            for k, name in teams.items()
            if k in predictor.elo.ratings
        ],
        as_of,
    )

    by_comp: dict[str, CompetitionSection] = {}
    failed_comps = {
        k.split(":")[0]
        for k in report.failures
        if not k.startswith(("stats:", "football:", "tennis"))
    }
    forecasts = []
    for match, comp_name in fixtures:
        section = by_comp.setdefault(
            match.competition_key, CompetitionSection(name=comp_name, key=match.competition_key)
        )
        label = f"{match.display_home} vs {match.display_away} ({comp_name})"
        if match.kickoff_utc < as_of:
            payload.skipped.append(f"{label}: ya había comenzado al generar el informe")
            continue
        if match.status.value not in ("scheduled",):
            payload.skipped.append(f"{label}: estado {match.status.value}")
            continue
        try:
            n_home = predictor.poisson.strength(match.home_team).n_matches
            n_away = predictor.poisson.strength(match.away_team).n_matches
            if n_home == 0 or n_away == 0:
                payload.skipped.append(f"{label}: sin histórico de algún equipo")
                continue
            f = predictor.predict(match, comp_name)
            section.forecasts.append(f)
            forecasts.append(f)
        except Exception as exc:  # aislar el fallo del partido
            payload.data_issues.append(f"{label}: error del modelo ({type(exc).__name__})")
            log.exception("football_predict_failed", extra={"match": match.match_id})
    for key, section in by_comp.items():
        if not section.forecasts and key in failed_comps:
            section.unavailable_reason = "La API no respondió para esta competición."
    payload.football = [s for s in by_comp.values() if s.forecasts or s.unavailable_reason]
    return forecasts


def _predict_tennis(
    session,
    settings,
    config,
    fixtures: list[TennisMatch],
    as_of: datetime,
    payload: DailyReportPayload,
) -> list[MatchForecast]:
    mcfg = config.models
    history = repo.load_tennis_history(session, as_of - TENNIS_HISTORY)
    state = load_calibration_state(
        session,
        "tennis",
        mcfg.version,
        list(mcfg.tennis["ensemble_weights_winner"]),
        mcfg.tennis["ensemble_weights_winner"],
        mcfg.calibration,
        as_of,
    )
    predictor = TennisPredictor(
        mcfg.tennis,
        mcfg.recency,
        mcfg.confidence,
        mcfg.version,
        weights=state.weights,
        calibrator=state.calibrator,
    ).fit(history, as_of)
    log.info("tennis_models_fitted", extra={"history": len(history)})
    rows = []
    for m in fixtures:
        for key, name in ((m.player_a, m.display_a), (m.player_b, m.display_b)):
            if key in predictor.elo.overall:
                rows.append(
                    {
                        "entity_key": key,
                        "entity_name": name,
                        "rating_type": "elo",
                        "value": predictor.elo.rating(key),
                        "matches": predictor.elo.matches_played(key),
                    }
                )
                if m.surface in ("hard", "clay", "grass"):
                    rows.append(
                        {
                            "entity_key": key,
                            "entity_name": name,
                            "rating_type": f"elo_{m.surface}",
                            "value": predictor.elo.effective_surface_rating(key, m.surface),
                            "matches": predictor.elo.matches_played(key, m.surface),
                        }
                    )
    repo.save_ratings(
        session,
        "tennis",
        list({(r["entity_key"], r["rating_type"]): r for r in rows}.values()),
        as_of,
    )
    forecasts: list[MatchForecast] = []
    for m in fixtures:
        label = f"{m.display_a} vs {m.display_b} ({m.tour} {m.tournament})"
        if m.kickoff_utc < as_of:
            payload.skipped.append(f"{label}: ya había comenzado al generar el informe")
            continue
        if (
            predictor.elo.matches_played(m.player_a) == 0
            or predictor.elo.matches_played(m.player_b) == 0
        ):
            payload.skipped.append(f"{label}: sin histórico de algún jugador")
            continue
        try:
            f = predictor.predict(m)
            payload.tennis.setdefault(m.tour, []).append(f)
            forecasts.append(f)
        except Exception as exc:
            payload.data_issues.append(f"{label}: error del modelo ({type(exc).__name__})")
            log.exception("tennis_predict_failed", extra={"match": m.match_id})
    return forecasts


def run_daily(
    settings: Settings,
    config: AppConfig,
    services: Services | None = None,
    now: datetime | None = None,
    send: bool = True,
) -> DailyOutcome:
    started = time.perf_counter()
    now = ensure_utc(now) if now else now_utc()
    tz = settings.tz
    today = local_today(tz, now)
    svc = services or build_services(settings)
    report = ingest.IngestReport()
    payload = DailyReportPayload(
        report_date=today, generated_at=now, timezone=settings.app_timezone
    )
    issues: list[str] = []
    outcome = DailyOutcome(run_id=None, status="failed", payload=payload)
    log.info("pipeline_start", extra={"date": today.isoformat(), "dry_run": settings.dry_run})

    with session_scope(settings.database_url) as session:
        run = repo.start_run(session, "daily", today)
        session.commit()
        outcome.run_id = run.id
        try:
            # 1) Ingesta + partidos del día
            football_fixtures: list[tuple[FootballMatch, str]] = []
            tennis_fixtures: list[TennisMatch] = []
            if svc.football:
                try:
                    coverage = ingest.sync_football(
                        session, svc.football, config, settings, report, include_stats=False
                    )
                    football_fixtures = ingest.football_fixtures_today(
                        svc.football, config, today, report
                    )
                    comp_ids = repo.ensure_competitions(session, config)
                    repo.upsert_football_matches(
                        session, [m for m, _ in football_fixtures], comp_ids
                    )
                    session.commit()
                    # Córners: primero los equipos que juegan hoy, luego la carga general
                    stats_keys = ingest.stats_competition_keys(config, coverage)
                    teams_today = {
                        t for m, _ in football_fixtures for t in (m.home_team, m.away_team)
                    }
                    ingest.prioritize_team_stats(
                        session, svc.football, teams_today, stats_keys, settings, report
                    )
                    ingest.sync_football_stats(
                        session, svc.football, config, settings, coverage, report
                    )
                    repo.record_data_source(
                        session,
                        "api_football",
                        ok=True,
                        calls=svc.football.http.calls_made,
                        quota=svc.football.http.last_rate_headers.get(
                            "x-ratelimit-requests-remaining"
                        ),
                    )
                except Exception as exc:
                    session.rollback()
                    issues.append(f"Fútbol: ingesta fallida ({exc})")
                    repo.record_data_source(
                        session, "api_football", ok=False, calls=0, error=str(exc)[:500]
                    )
            else:
                issues.append("Fútbol: API_FOOTBALL_KEY no configurada")
            if svc.tennis:
                try:
                    ingest.sync_tennis(session, svc.tennis, config, settings, today, report)
                    tennis_fixtures = ingest.tennis_fixtures_today(
                        svc.tennis, config, today, report, session, tz
                    )
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    issues.append(f"Tenis: ingesta fallida ({exc})")
            else:
                issues.append("Tenis: TENNIS_API_KEY no configurada")
            for key, err in report.failures.items():
                if not key.startswith("stats:"):
                    issues.append(f"{key}: {err[:160]}")
            n_stats_fail = sum(1 for k in report.failures if k.startswith("stats:"))
            if n_stats_fail:
                issues.append(f"Estadísticas de {n_stats_fail} partidos no disponibles")

            # 2) Resultados reales de días anteriores + métricas vivas
            settled = settle_predictions(session, now)
            refresh_live_metrics(session, now)
            session.commit()

            # 3) Modelos y predicciones (as_of = ahora: solo datos anteriores)
            forecasts: list[MatchForecast] = []
            if football_fixtures:
                forecasts += _predict_football(
                    session, settings, config, football_fixtures, now, report, payload
                )
            if tennis_fixtures:
                forecasts += _predict_tennis(
                    session, settings, config, tennis_fixtures, now, payload
                )
            n_saved = repo.save_forecasts(session, forecasts, run.id, today)
            session.commit()

            # 4) Informe (números de Python; narrativa de Claude validada)
            payload.data_issues = issues + payload.data_issues
            payload.model_quality = [
                {
                    "sport": m.sport,
                    "model": m.model,
                    "market": m.market,
                    "n": m.n,
                    "brier": m.brier,
                    "log_loss": m.log_loss,
                    "ece": m.ece,
                }
                for m in latest_global_metrics(session)
                if m.model == config.models.version and m.n >= 30
            ]
            narrative = svc.narrator.narrate(payload) if svc.narrator else None
            markdown = render_markdown(
                payload,
                narrative.matches if narrative else None,
                narrative.summary if narrative else None,
            )
            outcome.markdown = markdown
            run.report_markdown = markdown

            # 5) Envío
            if send and not settings.dry_run:
                if svc.telegram:
                    try:
                        svc.telegram.send_daily_report(markdown_to_telegram_html(markdown))
                        outcome.telegram_sent = True
                    except Exception as exc:
                        issues.append(f"Telegram: {exc}")
                if svc.email:
                    try:
                        summary = markdown.split("---")[0]
                        svc.email.send_daily_report(today.isoformat(), summary, markdown)
                        outcome.email_sent = True
                    except Exception as exc:
                        issues.append(f"Email: {type(exc).__name__}: {exc}")

            run.matches_found = sum(
                v for k, v in report.loaded.items() if k.endswith("fixtures_found")
            )
            run.matches_filtered = len(football_fixtures) + len(tennis_fixtures)
            run.predictions_generated = n_saved
            run.telegram_sent, run.email_sent = outcome.telegram_sent, outcome.email_sent
            run.errors = issues
            run.details = {
                "loaded": report.loaded,
                "calls": report.calls,
                "settled": settled,
                "forecasts": len(forecasts),
                "narrative": narrative is not None,
                "local_time": to_local(now, tz).isoformat(),
            }
            outcome.status = "success" if not issues else "partial"
            repo.finish_run(session, run, outcome.status)
        except Exception as exc:
            session.rollback()
            tb = traceback.format_exc()
            log.error("pipeline_failed", extra={"error": str(exc)})
            run = session.merge(run)
            run.errors = [*issues, f"FATAL: {exc}"]
            repo.finish_run(session, run, "failed")
            session.commit()
            _notify_failure(svc, settings, f"{exc}\n\n{tb[-2500:]}", send)
            outcome.issues = run.errors
            raise
    outcome.issues = issues
    log.info(
        "pipeline_end",
        extra={
            "status": outcome.status,
            "seconds": round(time.perf_counter() - started, 1),
            "football": payload.n_football,
            "atp": payload.n_tennis("ATP"),
            "wta": payload.n_tennis("WTA"),
            "telegram": outcome.telegram_sent,
            "email": outcome.email_sent,
        },
    )
    return outcome


def _notify_failure(svc: Services, settings: Settings, message: str, send: bool) -> None:
    if not send or settings.dry_run:
        return
    for name, fn in (
        ("telegram", lambda: svc.telegram.send_error_notification(message)),
        ("email", lambda: svc.email.send_critical_error(message)),
    ):
        target = svc.telegram if name == "telegram" else svc.email
        if target is None:
            continue
        try:
            fn()
        except Exception:  # no enmascarar el error original
            log.warning("failure_notification_failed", extra={"channel": name})
