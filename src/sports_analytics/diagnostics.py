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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.http import ApiError, HttpApiClient
from sports_analytics.core.logging import get_logger, register_secret
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

        day = today or local_today(settings.tz)

        # ¿Sirve la consulta por fecha para días pasados? (alternativa al bloqueo por temporada)
        finished_id = None
        for days_back in (7, 60, 400):
            past = day - timedelta(days=days_back)
            try:
                items = client.fixtures_by_date(past)
                ours = [
                    f
                    for f in items
                    if filter_football_league(
                        f["league"]["id"], f["league"]["name"], config.competitions
                    ).included
                ]
                done = [f for f in ours if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
                lines.append(
                    f"✅ Fecha pasada {past} (−{days_back} d): {len(items)} partidos, "
                    f"{len(ours)} autorizados, {len(done)} con resultado"
                )
                if done and finished_id is None:
                    finished_id = str(done[0]["fixture"]["id"])
            except ApiError as exc:
                lines.append(f"❌ Fecha pasada {past} (−{days_back} d): {exc}")

        if finished_id:
            try:
                st = client.fixture_statistics(finished_id)
                lines.append(
                    f"✅ Estadísticas de partido {finished_id}: córners {st.home_corners}-{st.away_corners}, "
                    f"tiros {st.home_shots}-{st.away_shots}"
                    if st
                    else f"⚠️ Partido {finished_id} sin estadísticas"
                )
            except ApiError as exc:
                lines.append(f"❌ Estadísticas de partido: {exc}")
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


def describe_structure(obj: Any, prefix: str = "", depth: int = 0, max_depth: int = 4) -> list[str]:
    """Esquema legible (ruta: tipo = ejemplo) de un JSON, para inspeccionar la API real."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict | list) and depth < max_depth:
                out.append(f"{path}: {type(v).__name__}")
                out.extend(describe_structure(v, path, depth + 1, max_depth))
            else:
                out.append(f"{path}: {type(v).__name__} = {str(v)[:60]!r}")
    elif isinstance(obj, list):
        out.append(f"{prefix}[]: {len(obj)} elementos")
        if obj:
            out.extend(describe_structure(obj[0], f"{prefix}[0]", depth + 1, max_depth))
    return out


def summarize_tennis_payload(data: Any) -> list[str]:
    """Agrega una respuesta paginada de la Tennis API para deducir el esquema real."""
    from collections import Counter, defaultdict

    items = data.get("data", []) if isinstance(data, dict) else []
    out = [
        f"items={len(items)} hasNextPage={data.get('hasNextPage')} "
        f"pageSize={data.get('pageSize')} keys={sorted(k for k in data if k != 'data')}"
    ]
    if not items:
        return out
    out.append("campos item: " + ", ".join(sorted(items[0].keys())))

    def tour(it: dict) -> dict:
        return it.get("tournament") if isinstance(it.get("tournament"), dict) else it

    ranks = Counter(tour(it).get("rankId") for it in items)
    out.append(f"rankId: {dict(ranks)}")
    courts: dict[Any, set[str]] = defaultdict(set)
    for it in items:
        t = tour(it)
        if "courtId" in t:
            court = t.get("court")
            label = court.get("name") if isinstance(court, dict) else court
            courts[t["courtId"]].add(f"{t.get('name')}" + (f" [{label}]" if label else ""))
    for cid, names in sorted(courts.items(), key=lambda kv: str(kv[0])):
        out.append(f"courtId={cid}: " + " | ".join(sorted(names)[:8]))
    if "result_type" in items[0]:
        out.append(f"result_type: {dict(Counter(it.get('result_type') for it in items))}")
        odd = [it for it in items if it.get("result_type") != "completed"][:6]
        for it in odd:
            out.append(
                f"  ej. {it.get('result_type')}: '{it.get('result')}' winner={it.get('match_winner')}"
            )
        out.append(f"best_of: {dict(Counter(it.get('best_of') for it in items))}")
    if "rankId" in items[0] or "courtId" in items[0]:
        out.extend(describe_structure(items[0])[:25])
    return out


def check_tennis(settings: Settings, today: date | None = None) -> list[str]:
    """Sonda de la Tennis API (~5 llamadas de 50): estructura real de las respuestas."""
    lines = ["## Tennis API (RapidAPI)", ""]
    if settings.tennis_api_key is None:
        return [*lines, "❌ `TENNIS_API_KEY` no está definida."]
    key = settings.tennis_api_key.get_secret_value()
    register_secret(key)
    day = today or local_today(settings.tz)
    yesterday = day - timedelta(days=1)
    http = HttpApiClient(
        provider="tennis_api",
        base_url=settings.tennis_api_base_url,
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": settings.tennis_api_host},
        daily_limit=settings.tennis_api_daily_limit,
        cache_dir=Path(settings.cache_dir),
        timeout=settings.http_timeout_seconds,
        max_retries=0,  # sin reintentos: no gastar cuota si hay un error de suscripción
        min_interval=1.5,  # respeta el límite por segundo del plan
    )
    week_ago = day - timedelta(days=8)
    probes = [
        ("Calendario ATP", f"/tennis/v2/atp/tournament/calendar/{day.year}", {"pageSize": 500}),
        (
            "Resultados ATP 8 días",
            f"/tennis/v2/atp/results/{week_ago}/{yesterday}",
            {"pageSize": 500},
        ),
        ("Resultados WTA ayer", f"/tennis/v2/wta/results/{yesterday}", {"pageSize": 500}),
        ("Fixtures ATP hoy", f"/tennis/v2/atp/fixtures/{day}", {"pageSize": 500}),
    ]
    try:
        for i, (title, path, params) in enumerate(probes):
            try:
                data = http.get(path, params)
            except ApiError as exc:
                lines.append(f"❌ {title} ({path}): {exc}")
                if i == 0 and exc.status in (401, 403):
                    lines.append(
                        "⛔ Error de autorización: se detiene la sonda para no gastar cuota."
                    )
                    break
                continue
            lines.append(f"✅ {title} ({path})")
            lines.append("```")
            lines.extend(summarize_tennis_payload(data))
            lines.append("```")
        lines.append(
            f"· Llamadas reales: {http.calls_made} · rate headers: {http.last_rate_headers}"
        )
    finally:
        http.close()
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
    if os.environ.get("GITHUB_ACTIONS") == "true":
        emit_annotations(report)
    return report


def _escape_annotation(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_annotations(report: str, chunk: int = 3500) -> None:
    """Publica el reporte como anotaciones ::notice:: (visibles vía API de check-runs)."""
    sections = report.split("\n## ")
    parts: list[str] = []
    for i, section in enumerate(sections):
        text = section if i == 0 else "## " + section
        parts.extend(text[j : j + chunk] for j in range(0, len(text), chunk))
    for n, part in enumerate(parts[:9], 1):
        print(f"::notice title=diagnostico {n}/{len(parts)}::{_escape_annotation(part)}")
