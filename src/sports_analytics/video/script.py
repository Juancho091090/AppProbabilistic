"""Guion del Short diario: "el partido más abierto de mañana".

Determinista: todas las cifras salen del pronóstico (sin LLM). Se elige el partido de
fútbol de confianza Media/Baja con el 1X2 más repartido (mayor entropía) y el video
cierra invitando al canal de Telegram, donde están los partidos de confianza Alta.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import DailyReportPayload

TELEGRAM_HANDLE = "@JCProbabilidades"
OPEN_LEVELS = ("medium", "low")


@dataclass
class Scene:
    kind: str  # hook | bars | why | cta
    narration: str
    data: dict = field(default_factory=dict)


@dataclass
class ShortScript:
    event_id: str
    title: str
    description: str
    scenes: list[Scene]


def entropy(probs: Sequence[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0)


def pick_open_match(payload: DailyReportPayload) -> MatchForecast | None:
    cands = [
        f for f in payload.all_forecasts() if f.sport == "football" and f.confidence in OPEN_LEVELS
    ]
    if not cands:
        return None
    return max(
        cands, key=lambda f: (entropy(f.markets["1x2"].values()), -f.kickoff_utc.timestamp())
    )


def _pct(p: float) -> int:
    return int(round(p * 100))


def _dec(x: float) -> str:
    """Decimal con coma (así lo lee bien la voz en español)."""
    return f"{x:.1f}".replace(".", ",")


def _why(f: MatchForecast, fair: Sequence[float] | None) -> list[dict]:
    """Duelos de la escena "¿por qué?": dos valores enfrentados y la frase narrada.

    ``fmt``: int (Elo), dec (goles) o pct (probabilidades)."""
    out = []
    elo = f.context.get("elo") or {}
    if elo:
        h, a = elo["home"], elo["away"]
        diff = h - a
        if abs(diff) < 60:
            say = f"En Elo están casi igualados: {h:.0f} contra {a:.0f}."
        else:
            better = f.home_or_a if diff > 0 else f.away_or_b
            say = (
                f"{better} tiene más Elo, {max(h, a):.0f} contra {min(h, a):.0f}, "
                "pero no tanto como para inclinar el partido."
            )
        out.append(
            {
                "title": "Fuerza (Elo)",
                "left": (f.home_or_a, float(h)),
                "right": (f.away_or_b, float(a)),
                "fmt": "int",
                "say": say,
            }
        )
    lam = f.context.get("lambda") or {}
    if lam:
        out.append(
            {
                "title": "Goles esperados",
                "left": (f.home_or_a, float(lam["home"])),
                "right": (f.away_or_b, float(lam["away"])),
                "fmt": "dec",
                "say": (
                    f"El modelo espera {_dec(lam['home'])} goles de {f.home_or_a} "
                    f"y {_dec(lam['away'])} de {f.away_or_b}."
                ),
            }
        )
    if fair:
        labels = (f.home_or_a, "el empate", f.away_or_b)
        i = max(range(3), key=lambda k: fair[k])
        model = [f.markets["1x2"][k] for k in ("home", "draw", "away")]
        if abs(fair[i] - model[i]) >= 0.08:
            say = (
                f"Ojo: el mercado sí ve favorito a {labels[i]}, con {_pct(fair[i])} por ciento, "
                f"y el modelo solo le da {_pct(model[i])}."
            )
        else:
            say = (
                f"Y el mercado lo ve parecido: su favorito no pasa del {_pct(fair[i])} por ciento."
            )
        name = "Empate" if i == 1 else labels[i]
        out.append(
            {
                "title": f"Favorito del mercado: {name}",
                "left": ("Mercado", float(fair[i])),
                "right": ("Modelo", float(model[i])),
                "fmt": "pct",
                "say": say,
            }
        )
    return out


def build_short_script(
    payload: DailyReportPayload,
    market_fair: Mapping[str, Sequence[float]] | None = None,
    n_high: int = 0,
) -> ShortScript | None:
    f = pick_open_match(payload)
    if f is None:
        return None
    x = f.markets["1x2"]
    h, d, a = _pct(x["home"]), _pct(x["draw"]), _pct(x["away"])
    hook = Scene(
        "hook",
        f"El partido más abierto de mañana: {f.home_or_a} contra {f.away_or_b}.",
        {
            "home": f.home_or_a,
            "away": f.away_or_b,
            "competition": f.competition,
            "kickoff": f.kickoff_utc,  # el render lo pasa a hora local
        },
    )
    bars = Scene(
        "bars",
        f"El modelo le da {h} por ciento a {f.home_or_a}, {d} al empate y {a} a {f.away_or_b}. "
        f"Ningún resultado pasa del {max(h, d, a)} por ciento.",
        {"rows": [(f.home_or_a, x["home"]), ("Empate", x["draw"]), (f.away_or_b, x["away"])]},
    )
    factors = _why(f, (market_fair or {}).get(f.event_id))
    why = Scene(
        "why",
        " ".join(x["say"] for x in factors) or "Los dos equipos llegan con números muy similares.",
        {"duels": factors},
    )
    if n_high:
        cta_say = (
            f"¿Quieres los partidos donde el modelo sí ve un favorito claro? Mañana hay {n_high} "
            "con confianza alta. Están en nuestro canal de Telegram: JC Probabilidades."
        )
    else:
        cta_say = (
            "Los partidos donde el modelo sí ve un favorito claro los publicamos cada noche "
            "en nuestro canal de Telegram: JC Probabilidades."
        )
    cta = Scene("cta", cta_say, {"n_high": n_high, "handle": TELEGRAM_HANDLE})
    title = f"{f.home_or_a} vs {f.away_or_b}: ¿el partido más abierto de mañana? #shorts"
    description = (
        f"{f.competition} · Probabilidades del modelo: {f.home_or_a} {h}% · Empate {d}% · "
        f"{f.away_or_b} {a}%.\n\nLos partidos de confianza Alta, cada noche en Telegram: "
        f"https://t.me/JCProbabilidades\n\nProbabilidades estimadas por un modelo estadístico "
        "(Elo + Poisson/Dixon-Coles), no recomendaciones. +18\n\n#futbol #estadistica #probabilidades"
    )
    return ShortScript(f.event_id, title[:100], description, [hook, bars, why, cta])
