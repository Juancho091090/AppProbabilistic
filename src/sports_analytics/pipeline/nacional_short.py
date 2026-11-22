"""Short semanal de Atlético Nacional: se genera el día siguiente al último partido de la
jornada en la que jugó Nacional y llega al chat personal (como los Shorts diarios) con
título, descripción y comentario para fijar. Una sola vez por jornada (``run_type =
nacional_short``). Gasto: ~5 llamadas de API-Football.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from sports_analytics.config.settings import Settings
from sports_analytics.content import nacional as N
from sports_analytics.core.logging import redact
from sports_analytics.core.timeutils import local_today, now_utc
from sports_analytics.db import repository as repo
from sports_analytics.db.models import PipelineRun
from sports_analytics.db.session import session_scope

RUN_TYPE = "nacional_short"


@dataclass
class NacionalOutcome:
    status: str  # sent | skipped | rendered | failed
    day: date
    round: str | None = None
    title: str | None = None
    video: Path | None = None
    issues: list[str] = field(default_factory=list)


def _done(session, rnd: str) -> bool:
    runs = session.scalars(select(PipelineRun).where(PipelineRun.run_type == RUN_TYPE)).all()
    return any(
        (r.details or {}).get("round") == rnd and (r.details or {}).get("sent") for r in runs
    )


def run_nacional_short(
    settings: Settings,
    *,
    services=None,
    now: datetime | None = None,
    force: bool = False,
    send: bool = True,
    out_dir: Path | None = None,
    use_db: bool = True,
) -> NacionalOutcome:
    """``force``: genera aunque no sea el día que toca ni falte en el registro.
    ``use_db``: registra la jornada enviada (una sola vez por jornada)."""
    from sports_analytics.pipeline.daily import build_services

    tz = ZoneInfo(settings.app_timezone)
    today = local_today(tz, now or now_utc())
    out = NacionalOutcome("skipped", today)
    svc = services or build_services(settings)
    if not svc.football:
        out.status = "failed"
        out.issues.append("Falta API_FOOTBALL_KEY")
        return out
    season = today.year
    fixtures = svc.football.fixtures_by_league_season(N.LEAGUE_ID, season)
    out.round = N.last_round(fixtures)
    due = N.video_day(fixtures, tz)
    if not force and due != today:
        out.issues.append(
            f"Hoy no toca: el video de {out.round or 'la última jornada'} sale el "
            f"{due.isoformat() if due else '(jornada sin terminar)'}"
        )
        return out
    db = settings.database_url if use_db else None
    if db and not force:
        with session_scope(db) as s:
            if _done(s, out.round):
                out.issues.append(f"El video de {out.round} ya se envió")
                return out
    from sports_analytics.video.html_engine import render_short_html
    from sports_analytics.video.nacional_script import (
        build_nacional_round_script,
        build_nacional_script,
    )
    from sports_analytics.video.tts import GoogleTTS

    fmt = (settings.nacional_format or "semestre").strip().lower()
    if fmt == "fecha":
        report = N.build_round_report(svc.football, season)
    else:
        report = N.build_report(svc.football, season)
    if report is None:
        out.issues.append("Nacional no ha jugado este semestre")
        return out
    out.issues += report.notes
    if fmt == "fecha":
        script = build_nacional_round_script(report, settings.app_timezone)
        out.issues += [f"No sale (sin estadísticas): {x}" for x in getattr(script, "notes", [])]
    else:
        script = build_nacional_script(report, settings.nacional_criticism, settings.app_timezone)
    out.title = script.title
    folder = out_dir or Path(tempfile.mkdtemp())
    path = Path(folder) / f"nacional_{report.round.rsplit('-', 1)[-1].strip()}.mp4"
    tts = None
    if settings.google_tts_api_key:
        tts = GoogleTTS(settings.google_tts_api_key.get_secret_value(), settings.google_tts_voice)
    try:
        render_short_html(
            script, path, settings.app_timezone, tts, settings.short_music_file, "nacional", today
        )
    except Exception as exc:  # noqa: BLE001
        out.status = "failed"
        out.issues.append(redact(f"Render: {type(exc).__name__}: {exc}")[:300])
        return out
    finally:
        if tts:
            tts.close()
    out.video, out.status = path, "rendered"
    if send and svc.telegram:
        caption = (
            f"💚 Short de Nacional ({report.round_label})\n\nTítulo:\n{script.title}\n\n"
            f"Descripción:\n{script.description}\n\n📌 Comentario para fijar:\n"
            f"{script.pinned_comment}"
        )
        try:
            svc.telegram.send_video(path, caption[:1024])
            if len(caption) > 1024:
                svc.telegram.send_message(caption, parse_mode=None)
            out.status = "sent"
        except Exception as exc:  # noqa: BLE001
            out.status = "failed"
            out.issues.append(redact(f"Telegram: {type(exc).__name__}: {exc}")[:300])
    if db and out.status == "sent":
        with session_scope(db) as s:
            run = repo.start_run(s, RUN_TYPE, today)
            run.details = {
                "round": report.round,
                "sent": True,
                "calls": report.calls,
                "title": script.title,
            }
            repo.finish_run(s, run, "success")
            s.commit()
    return out
