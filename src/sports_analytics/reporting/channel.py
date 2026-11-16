"""Mensaje diario del canal público de Telegram (@JCProbabilidades).

Es distinto del informe personal: formato corto pensado para la comunidad. Selección
(las dos reglas con mejor acierto del registro simulado, 16-nov-2026: 78.7 % ambas):

1. Favorito del modelo con confianza Alta (regla ``modelo_alta``).
2. Si hay menos de ``MIN_ITEMS``, se completa con partidos de fútbol cuyo favorito del
   mercado (Pinnacle sin margen) tiene 60 % o más (regla ``mercado_favorito_60``).
   Se muestran en un bloque aparte, con la probabilidad del mercado y la del modelo.

Todas las cifras salen del ``DailyReportPayload`` y de las cuotas guardadas; aquí solo se
seleccionan y se da formato.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import DailyReportPayload

CHANNEL_NAME = "JC Probabilidades"
MAX_ITEMS = 8
MIN_ITEMS = 3  # por debajo, se completa con favoritos del mercado ≥ 60 %
SELECTED_LEVEL = "high"
MARKET_THRESHOLD = 0.60
RULE_MODEL = "modelo_alta"
RULE_MARKET = "mercado_favorito_60"
OUTCOMES = ("home", "draw", "away")
LABELS = {"home": "Local", "draw": "Empate", "away": "Visitante"}
CHANNEL_DISCLAIMER = (
    "📌 Probabilidades estimadas por un modelo estadístico, no recomendaciones. "
    "Ningún modelo garantiza resultados. +18"
)
NO_MATCHES = (
    "Hoy no hay partidos con confianza Alta ni favoritos claros del mercado. "
    "Preferimos no publicar antes que publicar por publicar."
)

_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def favorite_probability(f: MatchForecast) -> float:
    """Probabilidad del resultado más probable (1X2 en fútbol, ganador en tenis)."""
    if f.sport == "football":
        return max(f.markets["1x2"].values())
    return max(f.markets["winner"]["A"], f.markets["winner"]["B"])


@dataclass(frozen=True)
class ChannelPick:
    forecast: MatchForecast
    rule: str  # modelo_alta | mercado_favorito_60
    market_fair: tuple[float, float, float] | None = None  # 1X2 sin margen (solo mercado)


def select_channel_forecasts(
    payload: DailyReportPayload,
    market_fair: Mapping[str, Sequence[float]] | None = None,
    max_items: int = MAX_ITEMS,
    min_items: int = MIN_ITEMS,
) -> list[ChannelPick]:
    """Primero confianza Alta (por prob. del favorito); si quedan menos de ``min_items``,
    se completa hasta ``max_items`` con favoritos del mercado ≥ 60 % (fútbol)."""
    high = [f for f in payload.all_forecasts() if f.confidence == SELECTED_LEVEL]
    high.sort(key=lambda f: (-favorite_probability(f), f.kickoff_utc))
    picks = [ChannelPick(f, RULE_MODEL) for f in high[:max_items]]
    if len(picks) >= min_items or not market_fair:
        return picks
    taken = {p.forecast.event_id for p in picks}
    extra = []
    for f in payload.all_forecasts():
        fair = market_fair.get(f.event_id)
        if f.sport != "football" or f.event_id in taken or not fair:
            continue
        if max(fair) >= MARKET_THRESHOLD:
            extra.append(ChannelPick(f, RULE_MARKET, tuple(float(x) for x in fair)))
    extra.sort(key=lambda p: (-max(p.market_fair), p.forecast.kickoff_utc))
    return picks + extra[: max_items - len(picks)]


def _p(x: float) -> str:
    return f"{x * 100:.0f}%"


def _e(text: str) -> str:
    return html.escape(str(text), quote=False)


def _football(f: MatchForecast, tz: ZoneInfo, show_btts: bool) -> list[str]:
    x = f.markets["1x2"]
    fav = max(x, key=x.get)
    probs = " · ".join(
        f"<b>{LABELS[k]} {_p(x[k])}</b>" if k == fav else f"{LABELS[k]} {_p(x[k])}"
        for k in OUTCOMES
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


def _market_football(p: ChannelPick, tz: ZoneInfo) -> list[str]:
    f, fair = p.forecast, p.market_fair
    i = max(range(3), key=lambda k: fair[k])
    model = f.markets["1x2"][OUTCOMES[i]]
    who = {"home": f.home_or_a, "draw": "Empate", "away": f.away_or_b}[OUTCOMES[i]]
    return [
        f"⚽ <b>{_e(f.home_or_a)} vs {_e(f.away_or_b)}</b>",
        f"{_e(f.competition)} · {f.kickoff_utc.astimezone(tz):%H:%M}",
        f"Favorito: <b>{_e(who)}</b> · Mercado {_p(fair[i])} · Modelo {_p(model)}",
    ]


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
    payload: DailyReportPayload, selected: list[ChannelPick] | None = None
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
    model = [p.forecast for p in selected if p.rule == RULE_MODEL]
    market = sorted(
        (p for p in selected if p.rule == RULE_MARKET), key=lambda p: p.forecast.kickoff_utc
    )
    if model:
        out += [f"Partidos de confianza <b>Alta</b> según el modelo ({len(model)}):", ""]
        football = sorted((f for f in model if f.sport == "football"), key=lambda f: f.kickoff_utc)
        tennis = sorted((f for f in model if f.sport != "football"), key=lambda f: f.kickoff_utc)
        for f in football:
            out += [*_football(f, tz, payload.show_btts), ""]
        for f in tennis:
            out += [*_tennis(f, tz), ""]
    if market:
        out += [
            f"Favoritos claros del mercado, 60 % o más ({len(market)}):",
            "",
        ]
        for p in market:
            out += [*_market_football(p, tz), ""]
    out.append(CHANNEL_DISCLAIMER)
    return "\n".join(out)
