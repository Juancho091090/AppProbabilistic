"""Publicación de la mañana en el canal: cómo les fue a los partidos publicados la noche anterior.

Flujo (pocas llamadas, para no gastar la cuota del informe de la noche):

1. Día de partido = ayer (hora local). Se leen los partidos publicados ese día en el canal
   (``channel_event_ids`` de la ejecución diaria).
2. Resultados solo de esos partidos: fútbol con una llamada (partidos de ese día), tenis con
   una llamada por torneo (más el calendario del circuito para encontrar el torneo).
3. Se liquidan las predicciones y el registro simulado (regla del mercado).
4. Imagen con los aciertos y fallos en la plantilla que se usó esa noche + texto, publicado
   como respuesta al mensaje de la noche (``channel_message_id``). Si la imagen falla, sale el
   texto. Una sola publicación por día (``run_type = channel_results``).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger, redact
from sports_analytics.core.timeutils import local_today, now_utc
from sports_analytics.db import repository as repo
from sports_analytics.db.models import PipelineRun, PredictionRow
from sports_analytics.db.session import session_scope
from sports_analytics.paper import registry as paper
from sports_analytics.pipeline import ingest
from sports_analytics.pipeline.results import settle_predictions
from sports_analytics.reporting.channel import render_results_message
from sports_analytics.video.recap import _published, build_recap, channel_totals

log = get_logger(__name__)
RUN_TYPE = "channel_results"
CAPTION_LIMIT = 1024


@dataclass
class ResultsOutcome:
    status: str  # sent | skipped | failed
    day: date
    recap: object | None = None
    message_ids: list[int] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def run_channel_results(
    settings: Settings,
    config: AppConfig,
    *,
    services=None,
    now: datetime | None = None,
    day: date | None = None,
    send: bool = True,
) -> ResultsOutcome:
    from sports_analytics.pipeline.daily import build_services

    now = now or now_utc()
    day = day or local_today(settings.tz, now) - timedelta(days=1)
    svc = services or build_services(settings)
    out = ResultsOutcome("skipped", day)
    with session_scope(settings.database_url) as session:
        if _already_sent(session, day):
            out.issues.append(f"Resultados del {day.isoformat()} ya publicados")
            return out
        published = _published(session, day)
        if not published:
            out.issues.append(f"Sin publicación del canal para el {day.isoformat()}")
            return out
        run = repo.start_run(session, RUN_TYPE, day)
        session.commit()
        try:
            ids = {e for e, _ in published}
            calls = refresh_results(session, svc, config, settings, day, ids, out.issues)
            settle_predictions(session, now)
            _settle_paper(session, now, out.issues)
            session.commit()
            model = config.models.version
            recap = build_recap(session, model, day, include_pending=True)
            out.recap = recap
            if recap is None or recap.n == 0:
                out.issues.append("Todavía no hay resultados de los partidos publicados")
                run.details = {"calls": calls, "sent": False}
                repo.finish_run(session, run, "skipped")
                session.commit()
                return out
            totals = channel_totals(session, model, day)
            reply_to = _evening_message_id(session, day)
            if send and svc.channel:
                out.message_ids = publish(
                    svc.channel, recap, totals, reply_to, settings, out.issues
                )
            out.status = "sent" if out.message_ids or not send else "failed"
            run.errors = out.issues
            run.details = {
                "calls": calls,
                "sent": bool(out.message_ids),
                "hits": recap.hits,
                "resolved": recap.n,
                "pending": recap.pending,
                "reply_to": reply_to,
                "message_ids": out.message_ids,
            }
            repo.finish_run(session, run, "success" if out.status == "sent" else "partial")
            session.commit()
        except Exception as exc:
            session.rollback()
            run = session.merge(run)
            run.errors = [*out.issues, f"FATAL: {exc}"]
            repo.finish_run(session, run, "failed")
            session.commit()
            out.status = "failed"
            out.issues.append(redact(f"{type(exc).__name__}: {exc}")[:300])
    return out


def _already_sent(session, day: date) -> bool:
    runs = session.scalars(
        select(PipelineRun).where(PipelineRun.run_type == RUN_TYPE, PipelineRun.run_date == day)
    ).all()
    return any((r.details or {}).get("sent") for r in runs)


def _evening_message_id(session, day: date) -> int | None:
    runs = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.run_type == "daily", PipelineRun.run_date == day)
        .order_by(PipelineRun.id.desc())
    ).all()
    for r in runs:
        mid = (r.details or {}).get("channel_message_id")
        if mid:
            return int(mid)
    return None


def refresh_results(session, svc, config, settings, day, event_ids, issues) -> dict[str, int]:
    """Trae los resultados de los partidos publicados. Devuelve las llamadas usadas."""
    rows = session.execute(
        select(PredictionRow.sport, PredictionRow.event_id, PredictionRow.competition)
        .where(PredictionRow.prediction_date == day, PredictionRow.event_id.in_(list(event_ids)))
        .distinct()
    ).all()
    football = {e for s, e, _ in rows if s == "football"}
    tennis = {(e, c) for s, e, c in rows if s == "tennis"}
    calls = {}
    if football and svc.football:
        start = svc.football.http.calls_made
        try:
            report = ingest.IngestReport()
            fixtures = ingest.football_fixtures_today(svc.football, config, day, report)
            done = [m for m, _ in fixtures if m.match_id in football]
            comp_ids = repo.ensure_competitions(session, config)
            repo.upsert_football_matches(session, done, comp_ids)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - se publica lo que haya
            session.rollback()
            issues.append(redact(f"Fútbol: resultados no actualizados ({exc})")[:200])
        calls["api_football"] = svc.football.http.calls_made - start
    if tennis and svc.tennis:
        start = svc.tennis.http.calls_made
        try:
            _refresh_tennis(session, svc.tennis, config, day, tennis)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            issues.append(redact(f"Tenis: resultados no actualizados ({exc})")[:200])
        calls["tennis_api"] = svc.tennis.http.calls_made - start
    return calls


def _refresh_tennis(session, client, config, day, published) -> None:
    """Una llamada por torneo: la competición guardada es 'ATP · Nombre del torneo'."""
    by_tour: dict[str, set[str]] = {}
    for _, comp in published:
        tour, _, name = comp.partition(" · ")
        by_tour.setdefault(tour.lower(), set()).add(name or tour)
    ids = {e for e, _ in published}
    for tour, names in by_tour.items():
        if tour not in ("atp", "wta"):
            continue
        infos = client.tournaments(tour, day.year)
        wanted = {tid: i for tid, i in infos.items() if i.name in names}
        for tid, info in wanted.items():
            items = client.tournament_results(tour, tid, closed=False)
            matches, meta = ingest._main_tour_matches(items, tour, config, {tid: info})
            mine = [m for m in matches if m.match_id in ids]
            repo.upsert_tennis_matches(
                session, mine, {k: meta[k] for k in (m.match_id for m in mine)}
            )


def _settle_paper(session, now, issues) -> None:
    try:
        paper.settle_selections(
            session,
            lambda ids: repo.load_market_quotes(session, ids),
            now,
            lines_by_event=lambda ids: repo.load_market_lines(session, ids),
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        issues.append(redact(f"Registro simulado: {exc}")[:200])


def publish(channel, recap, totals, reply_to, settings, issues) -> list[int]:
    """Imagen con el texto como pie (una sola publicación). Si el texto no cabe en el pie, va
    aparte; si la imagen falla, solo el texto. Devuelve los ids publicados."""
    text = render_results_message(recap, totals)
    ids: list[int] = []
    photo_sent = False
    if settings.channel_image:
        try:
            from sports_analytics.video.channel_card import render_results_card

            with tempfile.TemporaryDirectory() as tmp:
                card = render_results_card(recap, Path(tmp) / "resultados.png", totals=totals)
                if card is not None:
                    fits = len(text) <= CAPTION_LIMIT
                    caption = text if fits else _short_caption(recap)
                    mid = channel.send_photo(card, caption, reply_to=reply_to)
                    photo_sent = True
                    ids += [mid] if mid else []
                    if fits:
                        return ids or [0]
        except Exception as exc:  # noqa: BLE001 - la imagen no frena el texto
            issues.append(redact(f"Resultados (imagen): {type(exc).__name__}: {exc}")[:300])
    channel.send_message(text, parse_mode="HTML", reply_to=None if photo_sent else reply_to)
    mid = getattr(channel, "last_message_id", None)
    ids += [mid] if mid else []
    return ids or [0]


def _short_caption(recap) -> str:
    return f"📋 <b>Así nos fue ayer: {recap.hits} de {recap.n} acertados</b>. El detalle, abajo 👇"
