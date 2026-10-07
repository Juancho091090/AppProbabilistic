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
