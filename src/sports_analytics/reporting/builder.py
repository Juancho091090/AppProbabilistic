"""Renderizado determinista del informe diario (Markdown y HTML para Telegram).

Todas las cifras salen del ``DailyReportPayload``. La narrativa de Claude, si existe y
superó la validación, se inserta solo como texto explicativo.
"""

from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from sports_analytics.models.confidence import ConfidenceLevel
from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import DailyReportPayload, statistical_factors

DISCLAIMER = (
    "Informe de analítica estadística. Las cifras son probabilidades estimadas por modelos "
    "matemáticos, no recomendaciones."
)
UNAVAILABLE = "Datos no disponibles para esta competición."


def pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def _conf(f: MatchForecast) -> str:
    return ConfidenceLevel(f.confidence).label_es


def _time(f: MatchForecast, tz: ZoneInfo) -> str:
    return f.kickoff_utc.astimezone(tz).strftime("%H:%M")


def _factors(f: MatchForecast, narrative: dict[str, list[str]] | None) -> list[str]:
    if narrative and narrative.get(f.event_id):
        return narrative[f.event_id]
    return statistical_factors(f)


def football_block(f: MatchForecast, tz: ZoneInfo, narrative=None) -> list[str]:
    m = f.markets
    x = m["1x2"]
    lines = [
        f"### {f.home_or_a} vs {f.away_or_b}",
        "",
        f"Hora: {_time(f, tz)}",
        "",
        "**1X2**",
        f"- Local: {pct(x['home'])}",
        f"- Empate: {pct(x['draw'])}",
        f"- Visitante: {pct(x['away'])}",
        "",
        "**Goles**",
        f"- Esperados: {m['goals']['expected_goals']['total']:.2f}",
    ]
    for line, p in m["goals"]["goal_lines"].items():
        lines.append(f"- Más de {line}: {pct(p['over'])}")
    lines += ["", "**Córners**"]
    if m.get("corners"):
        lines.append(f"- Esperados: {m['corners']['expected']['total']:.2f}")
        for line, p in m["corners"]["lines"].items():
            lines.append(f"- Más de {line}: {pct(p['over'])}")
    else:
        lines.append("- Sin datos suficientes de córners")
    lines += ["", f"**Confianza:** {_conf(f)}", "", "**Factores estadísticos**"]
    lines += [f"- {x}" for x in _factors(f, narrative)]
    if f.data_notes:
        lines.append(f"- Notas de datos: {'; '.join(f.data_notes)}")
    return [*lines, ""]


def tennis_block(f: MatchForecast, tz: ZoneInfo, narrative=None) -> list[str]:
    m = f.markets
    lines = [
        f"### {f.home_or_a} vs {f.away_or_b}",
        "",
        f"Torneo: {f.competition} · Hora: {_time(f, tz)}",
        f"Superficie: {f.context.get('surface', 'desconocida')} · Mejor de {m['best_of']}",
        "",
        "**Ganador**",
        f"- {f.home_or_a}: {pct(m['winner']['A'])}",
        f"- {f.away_or_b}: {pct(m['winner']['B'])}",
        "",
        "**Sets**",
        f"- {f.home_or_a} gana al menos un set: {pct(m['at_least_one_set']['A'])}",
        f"- {f.away_or_b} gana al menos un set: {pct(m['at_least_one_set']['B'])}",
        "",
        "**Juegos**",
        f"- Esperados: {m['expected_games']:.1f}",
    ]
    for line, p in m["games_lines"].items():
        lines.append(f"- Más de {line}: {pct(p['over'])}")
    lines += ["", f"**Confianza:** {_conf(f)}", "", "**Factores estadísticos**"]
    lines += [f"- {x}" for x in _factors(f, narrative)]
    return [*lines, ""]


def render_markdown(
    payload: DailyReportPayload,
    narrative: dict[str, list[str]] | None = None,
    summary: str | None = None,
) -> str:
    tz = ZoneInfo(payload.timezone)
    gen = payload.generated_at.astimezone(tz)
    out = [
        "# INFORME DEPORTIVO",
        "",
        f"Fecha: {payload.report_date.isoformat()}",
        f"Hora: {gen.strftime('%H:%M')} ({payload.timezone})",
        "",
        "## RESUMEN",
        "",
        f"- Partidos de fútbol: {payload.n_football}",
        f"- Partidos ATP: {payload.n_tennis('ATP')}",
        f"- Partidos WTA: {payload.n_tennis('WTA')}",
        "",
    ]
    if summary:
        out += [summary, ""]
    out += ["---", "", "# FÚTBOL", ""]
    if not payload.football:
        out += ["Sin partidos en competiciones autorizadas hoy.", ""]
    for section in payload.football:
        out += [f"## {section.name}", ""]
        if section.unavailable_reason:
            out += [UNAVAILABLE, f"_{section.unavailable_reason}_", ""]
            continue
        for f in sorted(section.forecasts, key=lambda f: f.kickoff_utc):
            out += football_block(f, tz, narrative)
    out += ["---", "", "# TENIS", ""]
    if not any(payload.tennis.values()):
        out += ["Sin partidos ATP/WTA del circuito principal hoy.", ""]
    for tour in ("ATP", "WTA"):
        items = payload.tennis.get(tour) or []
        if items:
            out += [f"## {tour}", ""]
            for f in sorted(items, key=lambda f: f.kickoff_utc):
                out += tennis_block(f, tz, narrative)
    if payload.model_quality:
        out += ["---", "", "## Calidad de los modelos (predicciones ya resueltas)", ""]
        for q in payload.model_quality:
            out.append(
                f"- {q['sport']} · {q['model']} · {q['market']}: n={q['n']}, "
                f"Brier {q['brier']:.3f}, LogLoss {q['log_loss']:.3f}, ECE {q['ece']:.3f}"
            )
        out.append("")
    if payload.load_summary:
        out += ["---", "", "## Carga de datos", ""]
        out += [f"- {x}" for x in payload.load_summary]
        out.append("")
    if payload.skipped or payload.data_issues:
        out += ["---", "", "## Datos faltantes e incidencias", ""]
        out += [f"- {s}" for s in payload.skipped + payload.data_issues]
        out.append("")
    out += ["---", "", f"_{DISCLAIMER}_", ""]
    return "\n".join(out)


def markdown_to_telegram_html(md: str) -> str:
    """Conversión mínima y segura para Telegram (parse_mode=HTML)."""
    lines = []
    for raw in md.splitlines():
        text = html.escape(raw, quote=False)
        if raw.startswith("# "):
            text = f"<b>{html.escape(raw[2:])}</b>"
        elif raw.startswith("## "):
            text = f"\n<b>▌{html.escape(raw[3:])}</b>"
        elif raw.startswith("### "):
            text = f"<b>{html.escape(raw[4:])}</b>"
        elif raw.strip() == "---":
            text = "────────────"
        else:
            while "**" in text:
                text = text.replace("**", "<b>", 1).replace("**", "</b>", 1)
            if text.startswith("_") and text.endswith("_") and len(text) > 1:
                text = f"<i>{text[1:-1]}</i>"
            if text.startswith("- "):
                text = "• " + text[2:]
        lines.append(text)
    return "\n".join(lines)


def render_email_summary(payload: DailyReportPayload, summary: str | None = None) -> str:
    """Cuerpo corto del correo: conteos, un renglón por partido y aviso del PDF adjunto."""
    tz = ZoneInfo(payload.timezone)
    out = [
        f"INFORME DEPORTIVO · {payload.report_date.isoformat()}",
        "",
        f"Fútbol: {payload.n_football} · ATP: {payload.n_tennis('ATP')} · "
        f"WTA: {payload.n_tennis('WTA')}",
        "",
    ]
    if summary:
        out += [summary, ""]
    for section in payload.football:
        for f in sorted(section.forecasts, key=lambda f: f.kickoff_utc):
            x = f.markets["1x2"]
            out.append(
                f"- {_time(f, tz)} · {section.name} · {f.home_or_a} vs {f.away_or_b}: "
                f"L {pct(x['home'])} · E {pct(x['draw'])} · V {pct(x['away'])} ({_conf(f)})"
            )
    for tour in ("ATP", "WTA"):
        for f in sorted(payload.tennis.get(tour) or [], key=lambda f: f.kickoff_utc):
            w = f.markets["winner"]
            out.append(
                f"- {_time(f, tz)} · {tour} · {f.home_or_a} {pct(w['A'])} vs "
                f"{f.away_or_b} {pct(w['B'])} ({_conf(f)})"
            )
    if not payload.all_forecasts():
        out.append("Sin partidos para analizar hoy en las competiciones autorizadas.")
    n_issues = len(payload.skipped) + len(payload.data_issues)
    out += [
        "",
        "El detalle completo (goles, córners, sets, juegos, factores) está en el PDF adjunto.",
    ]
    if n_issues:
        out.append(f"Incidencias registradas: {n_issues} (ver anexo del PDF).")
    out += ["", DISCLAIMER]
    return "\n".join(out)
