"""Mensaje diario del canal público de Telegram (@JCProbabilidades).

Es distinto del informe personal: solo publica los partidos con confianza Alta, en un
formato corto pensado para la comunidad. Todas las cifras salen del ``DailyReportPayload``
(las mismas que el informe completo); aquí solo se seleccionan y se da formato.
"""

from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import DailyReportPayload

CHANNEL_NAME = "JC Probabilidades"
MAX_ITEMS = 8
SELECTED_LEVEL = "high"
CHANNEL_DISCLAIMER = (
    "📌 Probabilidades estimadas por un modelo estadístico, no recomendaciones. "
    "Ningún modelo garantiza resultados. +18"
)
NO_MATCHES = (
    "Hoy el modelo no encuentra partidos con confianza Alta. "
    "Preferimos no publicar antes que publicar por publicar."
)

_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def favorite_probability(f: MatchForecast) -> float:
    """Probabilidad del resultado más probable (1X2 en fútbol, ganador en tenis)."""
    if f.sport == "football":
        return max(f.markets["1x2"].values())
    return max(f.markets["winner"]["A"], f.markets["winner"]["B"])


def select_channel_forecasts(
    payload: DailyReportPayload, max_items: int = MAX_ITEMS
) -> list[MatchForecast]:
    """Partidos con confianza Alta, los ``max_items`` de mayor probabilidad del favorito."""
    candidates = [f for f in payload.all_forecasts() if f.confidence == SELECTED_LEVEL]
    candidates.sort(key=lambda f: (-favorite_probability(f), f.kickoff_utc))
    return candidates[:max_items]


def _p(x: float) -> str:
    return f"{x * 100:.0f}%"


def _e(text: str) -> str:
    return html.escape(str(text), quote=False)


def _football(f: MatchForecast, tz: ZoneInfo, show_btts: bool) -> list[str]:
    x = f.markets["1x2"]
    fav = max(x, key=x.get)
    labels = {"home": "Local", "draw": "Empate", "away": "Visitante"}
    probs = " · ".join(
        f"<b>{labels[k]} {_p(x[k])}</b>" if k == fav else f"{labels[k]} {_p(x[k])}"
        for k in ("home", "draw", "away")
    )
    lines = [
        f"⚽ <b>{_e(f.home_or_a)} vs {_e(f.away_or_b)}</b>",
        f"{_e(f.competition)} · {f.kickoff_utc.astimezone(tz):%H:%M}",
        probs,
    ]
    goals = f.markets.get("goals") or {}
    extra = []
    over25 = (goals.get("goal_lines") or {}).get("2.5")
    if over25:
        extra.append(f"Más de 2.5 goles: {_p(over25['over'])}")
    if show_btts and goals.get("btts") is not None:
        extra.append(f"Ambos marcan: {_p(goals['btts'])}")
    if extra:
        lines.append(" · ".join(extra))
    return lines


def _tennis(f: MatchForecast, tz: ZoneInfo) -> list[str]:
    w = f.markets["winner"]
    a = f"{_e(f.home_or_a)} {_p(w['A'])}"
    b = f"{_e(f.away_or_b)} {_p(w['B'])}"
    a, b = (f"<b>{a}</b>", b) if w["A"] >= w["B"] else (a, f"<b>{b}</b>")
    lines = [
        f"🎾 {a} vs {b}",
        f"{_e(f.competition)} · {f.kickoff_utc.astimezone(tz):%H:%M}",
    ]
    if f.markets.get("expected_games") is not None:
        lines.append(f"Games esperados: {f.markets['expected_games']:.1f}")
    return lines


def render_channel_message(
    payload: DailyReportPayload, selected: list[MatchForecast] | None = None
) -> str:
    """HTML para Telegram (parse_mode=HTML)."""
    tz = ZoneInfo(payload.timezone)
    d = payload.report_date
    header = (
        f"📊 <b>{CHANNEL_NAME}</b> · {_DAYS[d.weekday()].capitalize()} "
        f"{d.day} {_MONTHS[d.month - 1]}"
    )
    if selected is None:
        selected = select_channel_forecasts(payload)
    out = [header, ""]
    if not selected:
        return "\n".join([*out, NO_MATCHES, "", CHANNEL_DISCLAIMER])
    out += [f"Partidos de confianza <b>Alta</b> según el modelo ({len(selected)}):", ""]
    football = sorted((f for f in selected if f.sport == "football"), key=lambda f: f.kickoff_utc)
    tennis = sorted((f for f in selected if f.sport != "football"), key=lambda f: f.kickoff_utc)
    for f in football:
        out += [*_football(f, tz, payload.show_btts), ""]
    for f in tennis:
        out += [*_tennis(f, tz), ""]
    out.append(CHANNEL_DISCLAIMER)
    return "\n".join(out)
