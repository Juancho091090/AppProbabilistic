"""Punto de entrada: ``sports-analytics <comando>``."""

from __future__ import annotations

import argparse
import sys

from sports_analytics.config.loader import get_config
from sports_analytics.config.settings import get_settings
from sports_analytics.core.logging import configure_logging


def _check_apis(_: argparse.Namespace) -> int:
    from sports_analytics.diagnostics import run_diagnostics

    print(run_diagnostics(get_settings(), get_config()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sports-analytics", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-apis", help="Verifica claves, plan y cobertura de las APIs").set_defaults(
        func=_check_apis
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
