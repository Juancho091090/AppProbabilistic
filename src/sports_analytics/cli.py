"""Punto de entrada: ``sports-analytics <comando>``.

Comandos:
  check-apis   Diagnóstico de claves, plan y cobertura de las APIs.
  run-daily    Pipeline completo del día (ingesta, resultados, modelos, informe, envío).
  settle       Solo registra resultados reales y recalcula métricas.
  backtest     Backtesting walk-forward sobre el histórico de la base de datos.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from sports_analytics.config.loader import get_config
from sports_analytics.config.settings import get_settings
from sports_analytics.core.logging import configure_logging


def _check_apis(_: argparse.Namespace) -> int:
    from sports_analytics.diagnostics import run_diagnostics

    print(run_diagnostics(get_settings(), get_config()))
    return 0


def _run_daily(args: argparse.Namespace) -> int:
    from sports_analytics.pipeline.daily import run_daily

    settings = get_settings()
    if args.dry_run:
        settings = settings.model_copy(update={"dry_run": True})
    outcome = run_daily(settings, get_config(), send=not args.no_send, days_ahead=args.days_ahead)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(outcome.markdown, encoding="utf-8")
        if outcome.pdf:
            Path(args.output).with_suffix(".pdf").write_bytes(outcome.pdf)
    summary = (
        f"Estado: {outcome.status} · Telegram: {outcome.telegram_sent} · "
        f"Email: {outcome.email_sent} · Narrativa Claude: {outcome.narrative}"
    )
    print(summary)
    for issue in outcome.issues:
        print(f"- {issue}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        from sports_analytics.diagnostics import _escape_annotation

        text = "\n".join([summary, *[f"- {i}" for i in outcome.issues]])
        print(f"::notice title=resultado-envio::{_escape_annotation(text)}")
    return 0 if outcome.status in ("success", "partial") else 1


def _settle(_: argparse.Namespace) -> int:
    from sports_analytics.db.session import session_scope
    from sports_analytics.pipeline.results import refresh_live_metrics, settle_predictions

    with session_scope(get_settings().database_url) as session:
        print(settle_predictions(session))
        cfg = get_config().models
        print(f"métricas: {refresh_live_metrics(session, None, cfg.market, cfg.version)}")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    from sports_analytics.backtesting.engine import run_backtest
    from sports_analytics.core.timeutils import local_day_bounds_utc
    from sports_analytics.db import repository as repo
    from sports_analytics.db.session import session_scope

    settings, config = get_settings(), get_config()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    since, _ = local_day_bounds_utc(start - timedelta(days=3 * 365), settings.tz)
    market = None
    with session_scope(settings.database_url) as session:
        if args.sport == "football":
            history = repo.load_football_history(session, since)
            if config.models.market.enabled:
                from sports_analytics.market.benchmark import market_probs_by_match

                market = market_probs_by_match(
                    repo.load_market_quotes(
                        session, since=local_day_bounds_utc(start, settings.tz)[0]
                    ),
                    config.models.market,
                )
        else:
            history = repo.load_tennis_history(session, since)
    names = {c.key: c.name for c in config.competitions.football}
    report = run_backtest(
        args.sport, history, config, start, end, settings.tz, args.refit_days, names, market
    )
    md = report.to_markdown(config.models.version)
    out = Path(args.output or f"reports/output/backtest_{args.sport}_{start}_{end}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    _publish(md, "backtest")
    return 0


def _fit_recalibration(_: argparse.Namespace) -> int:
    from sports_analytics.db.session import session_scope
    from sports_analytics.pipeline.recalibration import fit_recalibration

    with session_scope(get_settings().database_url) as session:
        result = fit_recalibration(session, get_settings(), get_config())
    md = result.to_markdown()
    print(md)
    _publish(md, "recalibracion")
    return 0


def _export_metrics(args: argparse.Namespace) -> int:
    """Backtest walk-forward de fútbol y tenis → métricas avanzadas en JSON."""
    import json

    from sports_analytics.backtesting.engine import run_backtest
    from sports_analytics.core.timeutils import local_today, now_utc
    from sports_analytics.db import repository as repo
    from sports_analytics.db.session import session_scope
    from sports_analytics.evaluation.export import football_tables, tennis_tables

    settings, config = get_settings(), get_config()
    tz = settings.tz
    end = local_today(tz) - timedelta(days=1)
    names = {c.key: c.name for c in config.competitions.football}
    since = now_utc() - timedelta(days=3 * 365)
    with session_scope(settings.database_url) as session:
        fh = repo.load_football_history(session, since)
        th = repo.load_tennis_history(session, since)
    out: dict = {
        "generated_at": now_utc().isoformat(),
        "model_version": config.models.version,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "sections": {},
    }
    for sport, hist, days, fn in (
        ("football", fh, args.football_days, football_tables),
        ("tennis", th, args.tennis_days, tennis_tables),
    ):
        start = end - timedelta(days=days)
        bt = run_backtest(sport, hist, config, start, end, tz, args.refit_days, names)
        out["sections"][sport] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "matches": bt.n_matches,
            "skipped": bt.n_skipped,
            "tables": fn(bt.match_rows),
        }
        print(f"{sport}: {bt.n_matches} partidos, {len(out['sections'][sport]['tables'])} tablas")
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"escrito {path}")
    return 0


def _ab_test(args: argparse.Namespace) -> int:
    """Compara la configuración en producción con la de la Fase A (mismo backtest)."""
    import json

    from sports_analytics.core.timeutils import local_today, now_utc
    from sports_analytics.db import repository as repo
    from sports_analytics.db.session import session_scope
    from sports_analytics.evaluation.ab import run_ab, to_markdown

    settings, config = get_settings(), get_config()
    end = local_today(settings.tz) - timedelta(days=1)
    start = end - timedelta(days=args.days)
    with session_scope(settings.database_url) as session:
        hist = repo.load_football_history(session, now_utc() - timedelta(days=3 * 365))
    variants = {
        "actual": {},
        "fase_a": {
            "football.league_effects.enabled": True,
            "confidence.method": "favorite_probability",
        },
    }
    names = {c.key: c.name for c in config.competitions.football}
    res = run_ab(hist, config, variants, start, end, settings.tz, args.refit_days, names)
    md = to_markdown(res, start, end)
    print(md)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    _publish(md, "ab-test")
    return 0


def _track_record(_: argparse.Namespace) -> int:
    """Aciertos reales frente a esperados, con detalle por partido (solo lectura)."""
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from sports_analytics.db import repository as repo
    from sports_analytics.db.models import FootballMatchRow, Team
    from sports_analytics.db.session import session_scope
    from sports_analytics.evaluation.track_record import football_1x2_detail, track_record

    version = get_config().models.version
    lines = ["# Balance de aciertos", ""]
    with session_scope(get_settings().database_url) as s:
        pairs = repo.settled_predictions(s)
        for t in track_record(pairs, version):
            lines.append(
                f"- {t['label']}: {t['hits']} de {t['n']} ({t['hit_rate']:.0%}); "
                f"esperado {t['expected_rate']:.0%}"
            )
        detail = football_1x2_detail(pairs, version)
        h, a = aliased(Team), aliased(Team)
        ids = [d["event_id"] for d in detail]
        names = {
            ext: f"{hn} vs {an}"
            for ext, hn, an in s.execute(
                select(FootballMatchRow.external_id, h.name, a.name)
                .join(h, h.id == FootballMatchRow.home_team_id)
                .join(a, a.id == FootballMatchRow.away_team_id)
                .where(FootballMatchRow.external_id.in_(ids))
            )
        }
    lines += ["", "## Detalle 1X2", ""]
    for d in detail:
        lines.append(
            f"- {d['date']} · {d['competition']} · {names.get(d['event_id'], d['event_id'])}: "
            f"favorito {d['favorite']} {d['probability']:.0%} ({d['confidence']}) → "
            f"resultado {d['result']} {'✔' if d['hit'] else '✘'}"
        )
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        from sports_analytics.diagnostics import _escape_annotation

        chunk, n = [], 0
        for line in lines:
            chunk.append(line)
            if sum(len(x) for x in chunk) > 3500:
                n += 1
                print(f"::notice title=balance {n}::{_escape_annotation(chr(10).join(chunk))}")
                chunk = []
        if chunk:
            n += 1
            print(f"::notice title=balance {n}::{_escape_annotation(chr(10).join(chunk))}")
    return 0


def _data_audit(_: argparse.Namespace) -> int:
    """Auditoría de la base de datos (solo lectura, sin llamadas a APIs)."""
    from sqlalchemy import case, func, select

    from sports_analytics.db.models import (
        Competition,
        FootballMatchRow,
        FootballStatistics,
        TennisMatchRow,
    )
    from sports_analytics.db.session import session_scope

    lines = ["# Auditoría de datos", "", "## Tenis", ""]
    with session_scope(get_settings().database_url) as s:
        t = TennisMatchRow
        rows = s.execute(
            select(
                t.tour,
                func.count(),
                func.sum(case((t.status == "finished", 1), else_=0)),
                func.sum(case((t.winner == "A", 1), else_=0)),
                func.sum(case((t.winner == "B", 1), else_=0)),
                func.sum(case((t.retired.is_(True), 1), else_=0)),
                func.min(t.kickoff_utc),
                func.max(t.kickoff_utc),
            ).group_by(t.tour)
        ).all()
        lines += [
            "| Circuito | Partidos | Terminados | Gana jugador 1 | Gana jugador 2 | "
            "Retiros | Desde | Hasta |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append("| " + " | ".join(str(v)[:10] for v in r) + " |")
        players = s.execute(
            select(
                func.count(func.distinct(t.player_a_id)), func.count(func.distinct(t.player_b_id))
            )
        ).one()
        lines += [
            "",
            f"Jugadores distintos como jugador 1: {players[0]} · como jugador 2: {players[1]}",
        ]
        by_surface = s.execute(select(t.surface, func.count()).group_by(t.surface)).all()
        lines.append("Superficies: " + ", ".join(f"{a}={b}" for a, b in by_surface))
        month = func.to_char(t.kickoff_utc, "YYYY-MM").label("mes")
        by_month = s.execute(select(month, func.count()).group_by("mes").order_by("mes")).all()
        lines.append("Partidos por mes: " + ", ".join(f"{a}={b}" for a, b in by_month))

        lines += ["", "## Fútbol", ""]
        f, st = FootballMatchRow, FootballStatistics
        fr = s.execute(
            select(
                Competition.name,
                func.count(f.id),
                func.sum(case((f.status == "finished", 1), else_=0)),
                func.sum(case((st.available.is_(True), 1), else_=0)),
                func.sum(case((st.home_xg.is_not(None), 1), else_=0)),
                func.sum(case((f.neutral_venue.is_(True), 1), else_=0)),
            )
            .join(Competition, Competition.id == f.competition_id)
            .outerjoin(st, st.match_id == f.id)
            .group_by(Competition.name)
            .order_by(func.count(f.id).desc())
        ).all()
        lines += [
            "| Competición | Partidos | Terminados | Con estadísticas | Con xG | Sede neutral |",
            "|---|---|---|---|---|---|",
        ]
        for r in fr:
            lines.append("| " + " | ".join(str(v) for v in r) + " |")
    md = "\n".join(lines)
    print(md)
    _publish(md, "auditoria")
    return 0


def _market_benchmark(_: argparse.Namespace) -> int:
    """Predicciones reales del informe diario (ya liquidadas) frente al mercado."""
    from sports_analytics.db.session import session_scope
    from sports_analytics.market.benchmark import summarize, to_markdown
    from sports_analytics.pipeline.results import live_market_pairs

    cfg = get_config().models
    with session_scope(get_settings().database_url) as session:
        pairs = live_market_pairs(session, cfg.market, cfg.version)
    md = "\n".join(
        to_markdown(
            summarize(pairs, cfg.market.bootstrap_samples),
            "Informe diario vs mercado (predicciones reales liquidadas)",
            "modelo a la hora del informe; mercado con el último precio previo al inicio",
        )
    )
    print(md)
    _publish(md, "benchmark-mercado")
    return 0


def _publish(md: str, title: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        from sports_analytics.diagnostics import emit_annotations

        emit_annotations(md)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sports-analytics",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-apis", help="Verifica claves, plan y cobertura").set_defaults(
        func=_check_apis
    )

    daily = sub.add_parser("run-daily", help="Pipeline diario completo")
    daily.add_argument("--dry-run", action="store_true", help="No envía Telegram/Email")
    daily.add_argument("--no-send", action="store_true", help="Genera el informe sin enviarlo")
    daily.add_argument("--output", help="Ruta donde guardar el informe en Markdown")
    daily.add_argument(
        "--days-ahead",
        type=int,
        default=None,
        help="0 = partidos de hoy; 1 = partidos de mañana (informe la noche anterior)",
    )
    daily.set_defaults(func=_run_daily)

    sub.add_parser("settle", help="Registra resultados y recalcula métricas").set_defaults(
        func=_settle
    )

    bt = sub.add_parser("backtest", help="Backtesting walk-forward")
    bt.add_argument("--sport", choices=["football", "tennis"], required=True)
    bt.add_argument("--start", required=True, help="YYYY-MM-DD")
    bt.add_argument("--end", required=True, help="YYYY-MM-DD")
    bt.add_argument("--refit-days", type=int, default=7)
    bt.add_argument("--output")
    bt.set_defaults(func=_backtest)

    ex = sub.add_parser("export-metrics", help="Métricas avanzadas (JSON) para la hoja")
    ex.add_argument("--football-days", type=int, default=150)
    ex.add_argument("--tennis-days", type=int, default=180)
    ex.add_argument("--refit-days", type=int, default=7)
    ex.add_argument("--output", default="reports/metrics/metrics.json")
    ex.set_defaults(func=_export_metrics)

    ab = sub.add_parser("ab-test", help="Backtest A/B: configuración anterior vs actual")
    ab.add_argument("--days", type=int, default=150)
    ab.add_argument("--refit-days", type=int, default=7)
    ab.add_argument("--output", default="reports/metrics/ab.json")
    ab.set_defaults(func=_ab_test)

    sub.add_parser("track-record", help="Aciertos reales vs esperados (sin APIs)").set_defaults(
        func=_track_record
    )

    sub.add_parser("data-audit", help="Auditoría de la base de datos (sin APIs)").set_defaults(
        func=_data_audit
    )

    sub.add_parser(
        "fit-recalibration", help="Ajusta y valida la recalibración 1X2 (walk-forward)"
    ).set_defaults(func=_fit_recalibration)

    sub.add_parser(
        "market-benchmark", help="Compara las predicciones liquidadas con el mercado"
    ).set_defaults(func=_market_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
