"""Diagnóstico de APIs con credenciales reales.

Verifica, gastando el mínimo de llamadas (~5):
1. que la clave funciona y qué plan tiene la cuenta;
2. que cada liga configurada existe con ese ID y país, y su cobertura actual;
3. si el plan permite la temporada actual y la anterior (histórico);
4. cuántos partidos de hoy pasan el filtro de competiciones.

Nunca imprime claves: solo nombres de variables y resultados.
"""

from __future__ import annotations

import os
from datetime import date

from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError
from sports_analytics.core.logging import get_logger
from sports_analytics.core.timeutils import local_today
from sports_analytics.data.clients.api_football import ApiFootballClient
from sports_analytics.data.filters import filter_football_league

log = get_logger(__name__)


def _yes(flag: bool) -> str:
    return "✅" if flag else "❌"


def check_api_football(
    settings: Settings, config: AppConfig, today: date | None = None
) -> list[str]:
    lines = ["## API-Football", ""]
    if settings.api_football_key is None:
        return [*lines, "❌ `API_FOOTBALL_KEY` no está definida."]
    client = ApiFootballClient(settings)
    try:
        try:
            st = client.status()
        except ApiError as exc:
            return [*lines, f"❌ La clave no funciona: {exc}"]
        lines += [
            f"✅ Clave válida · plan **{st['plan']}** · activa: {st['active']}",
            f"· Llamadas usadas hoy: {st['requests_current']} / {st['requests_limit']}",
            "",
        ]

        leagues = client.current_leagues()
        lines += [
            "| Competición | ID | Encontrada | País API | Nombre API | Temporada | Stats partido | Eventos |",
            "|---|---|---|---|---|---|---|---|",
        ]
        mismatches = 0
        for comp in config.competitions.football:
            cov = leagues.get(comp.api_football_id)
            if cov is None:
                lines.append(
                    f"| {comp.name} | {comp.api_football_id} | ❌ sin temporada actual | | | | | |"
                )
                continue
            country_ok = cov.country.lower() == comp.country.lower()
            mismatches += not country_ok
            lines.append(
                f"| {comp.name} | {comp.api_football_id} | {_yes(country_ok)} | {cov.country} | "
                f"{cov.name} | {cov.season} | {_yes(cov.fixtures_statistics)} | {_yes(cov.fixtures_events)} |"
            )
        lines.append("")
        if mismatches:
            lines.append(f"⚠️ {mismatches} competición(es) con país distinto: revisar IDs.")

        # Acceso a temporadas (restricción típica de planes gratuitos)
        probe = config.competitions.football[0]
        probe_cov = leagues.get(probe.api_football_id)
        if probe_cov:
            for season in (probe_cov.season, probe_cov.season - 1):
                try:
                    n = len(client.fixtures_by_league_season(probe.api_football_id, season))
                    lines.append(f"✅ Acceso a temporada {season} ({probe.name}): {n} partidos")
                except ApiError as exc:
                    lines.append(f"❌ Sin acceso a temporada {season} ({probe.name}): {exc}")

        day = today or local_today(settings.tz)
        try:
            fixtures = client.fixtures_by_date(day)
            kept = [
                f
                for f in fixtures
                if filter_football_league(
                    f["league"]["id"], f["league"]["name"], config.competitions
                ).included
            ]
            lines.append(
                f"✅ Partidos el {day}: {len(fixtures)} en total, {len(kept)} en competiciones autorizadas"
            )
        except ApiError as exc:
            lines.append(f"❌ No se pudieron leer los partidos de hoy: {exc}")
        lines.append(f"· Llamadas reales en este diagnóstico: {client.http.calls_made}")
    finally:
        client.close()
    return lines


def check_tennis(settings: Settings) -> list[str]:
    lines = ["## API de tenis", ""]
    if settings.tennis_api_key is None:
        return [*lines, "❌ `TENNIS_API_KEY` no está definida."]
    lines.append("✅ `TENNIS_API_KEY` está definida.")
    lines.append("⏳ Cliente pendiente: falta confirmar el proveedor (Fase 6).")
    return lines


def check_others(settings: Settings) -> list[str]:
    rows = [
        ("ANTHROPIC_API_KEY", settings.claude_enabled),
        ("TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID", settings.telegram_enabled),
        ("SMTP_HOST + EMAIL_FROM + EMAIL_TO", settings.email_enabled),
    ]
    return ["## Otras credenciales", "", *[f"{_yes(ok)} {name}" for name, ok in rows]]


def run_diagnostics(settings: Settings, config: AppConfig) -> str:
    report = "\n".join(
        [
            "# Diagnóstico de APIs",
            "",
            *check_api_football(settings, config),
            "",
            *check_tennis(settings),
            "",
            *check_others(settings),
            "",
        ]
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    return report
