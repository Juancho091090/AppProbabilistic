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
    pinned_comment: str = ""  # para fijar en YouTube (en Shorts los enlaces no son clicables)
    sport: str = "football"
    surface: str | None = None  # tenis: hard, clay, grass…


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


GOOD_DAY, OK_DAY = 0.75, 0.5

# Presentación del canal (~5 s): contexto para quien llega por primera vez.
INTRO_SCENE = Scene(
    "intro",
    "Esto es JC Probabilidades: un modelo estadístico que analiza el fútbol y el tenis "
    "cada día, con aciertos y fallos.",
    {"tags": ["Fútbol y tenis", "Aciertos y fallos", "Todo con datos"]},
)


def recap_scene(recap) -> Scene:
    """Escena "así nos fue ayer": se cuentan aciertos y fallos, siempre todos."""
    h, n = recap.hits, recap.n
    where = " que publicamos en Telegram" if recap.source == "canal" else ""
    plural = "partido" if n == 1 else "partidos"
    if recap.rate >= GOOD_DAY:
        say = f"¡Gran día ayer! El modelo acertó {h} de {n} {plural} de confianza alta{where}."
        mood = "good"
    elif recap.rate >= OK_DAY:
        say = f"Así nos fue ayer: el modelo acertó {h} de {n} {plural} de confianza alta{where}."
        mood = "ok"
    else:
        say = (
            f"Ayer no fue nuestro día: el modelo acertó {h} de {n} {plural} de confianza alta. "
            "Lo mostramos igual, porque aquí publicamos todo."
        )
        mood = "bad"
    return Scene(
        "recap",
        say,
        {
            "hits": h,
            "n": n,
            "mood": mood,
            "source": recap.source,
            "items": [(i.label, i.pick, i.hit) for i in recap.items],
        },
    )


def build_short_script(
    payload: DailyReportPayload,
    market_fair: Mapping[str, Sequence[float]] | None = None,
    n_high: int = 0,
    recap=None,
    intro: bool = False,
) -> ShortScript | None:
    """``recap`` (video.recap.Recap) añade el resumen de ayer; ``intro`` antepone una
    presentación corta del canal (pensada para los primeros videos)."""
    f = pick_open_match(payload)
    if f is None:
        return None
    x = f.markets["1x2"]
    h, d, a = _pct(x["home"]), _pct(x["draw"]), _pct(x["away"])
    opening = (
        "Y para mañana, el partido más abierto" if recap else "El partido más abierto de mañana"
    )
    hook = Scene(
        "hook",
        f"{opening}: {f.home_or_a} contra {f.away_or_b}.",
        {
            "home": f.home_or_a,
            "away": f.away_or_b,
            "competition": f.competition,
            "kickoff": f.kickoff_utc,  # el render lo pasa a hora local
            "kicker": opening,
            "note_big": f"{h} / {d} / {a}",
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
            "con confianza alta, y están gratis en nuestro canal de Telegram: JC Probabilidades."
        )
    else:
        cta_say = (
            "Los partidos donde el modelo sí ve un favorito claro los publicamos gratis cada "
            "noche en nuestro canal de Telegram: JC Probabilidades."
        )
    cta = Scene("cta", cta_say, {"n_high": n_high, "handle": TELEGRAM_HANDLE})
    title = f"{f.home_or_a} vs {f.away_or_b}: ¿el partido más abierto de mañana? #shorts"
    yesterday = f"Ayer, confianza Alta: {recap.hits} de {recap.n} aciertos.\n\n" if recap else ""
    description = yesterday + (
        f"{f.competition} · Probabilidades del modelo: {f.home_or_a} {h}% · Empate {d}% · "
        f"{f.away_or_b} {a}%.\n\nLos partidos de confianza Alta, cada noche en Telegram: "
        f"https://t.me/JCProbabilidades\n\nProbabilidades estimadas por un modelo estadístico "
        "(Elo + Poisson/Dixon-Coles), no recomendaciones. +18\n\n#futbol #estadistica #probabilidades"
    )
    scenes = [hook, bars, why, cta]
    if recap and recap.n:
        scenes.insert(0, recap_scene(recap))
    if intro:
        scenes.insert(0, INTRO_SCENE)
    pinned = (
        f"¿Tú qué ves en {f.home_or_a} vs {f.away_or_b}? Comenta L, E o V 👇\n\n"
        "📊 Los partidos de confianza Alta de mañana están gratis en Telegram: "
        "@JCProbabilidades (enlace en el perfil del canal)."
    )
    return ShortScript(f.event_id, title[:100], description, scenes, pinned)


# --------------------------------------------------------------------------- tenis

SURFACE_ES = {
    "hard": "pista dura",
    "clay": "arcilla",
    "grass": "césped",
    "carpet": "pista cubierta",
}


def pick_tennis_match(payload: DailyReportPayload) -> MatchForecast | None:
    """El partido "estelar" de mañana: el de mayor Elo sumado entre los dos jugadores."""
    cands = [f for f in payload.all_forecasts() if f.sport == "tennis" and "winner" in f.markets]
    if not cands:
        return None

    def strength(f: MatchForecast) -> float:
        elo = f.context.get("elo") or {}
        return float(elo.get("A") or 0) + float(elo.get("B") or 0)

    return max(cands, key=lambda f: (strength(f), -f.kickoff_utc.timestamp()))


def _clean_comp(name: str) -> str:
    """ "ATP · ATP Finals" -> "ATP Finals"; "WTA · Wuhan" -> "WTA Wuhan"."""
    tour, _, tournament = name.partition(" · ")
    if not tournament:
        return name
    return tournament if tournament.lower().startswith(tour.lower()) else f"{tour} {tournament}"


def _set_label(score: str, a: str, b: str) -> str:
    """ "2-1" visto desde A: gana A 2-1; "1-2": gana B 2-1."""
    sa, sb = (int(x) for x in score.split("-"))
    return f"{a} {sa}-{sb}" if sa > sb else f"{b} {sb}-{sa}"


def build_tennis_script(
    payload: DailyReportPayload,
    n_high: int = 0,
    recap=None,
    intro: bool = False,
    tz: str = "America/Bogota",
) -> ShortScript | None:
    """Short de tenis: el partido estelar de mañana (ganador, sets y games esperados)."""
    from zoneinfo import ZoneInfo

    from sports_analytics.video.html_engine import when_es

    f = pick_tennis_match(payload)
    if f is None:
        return None
    a, b = f.home_or_a, f.away_or_b
    w = f.markets["winner"]
    pa, pb = _pct(w["A"]), _pct(w["B"])
    comp = _clean_comp(f.competition)
    surface = (f.context.get("surface") or "unknown").lower()
    surface_es = SURFACE_ES.get(surface, "")
    when = when_es(f.kickoff_utc, ZoneInfo(tz))
    hook = Scene(
        "hook",
        (
            f"Y en tenis, mañana en {comp}: {a} contra {b}."
            if recap
            else f"Mañana en {comp}: {a} contra {b}. ¿Quién gana?"
        ),
        {
            "generic": True,
            "kicker": f"Mañana en {comp}",
            "home": a,
            "away": b,
            "note_head": "el modelo dice:",
            "note_big": f"{pa} / {pb}",
            "note_foot": f"{a}, {b}",
            "when": f"{when}, {surface_es}" if surface_es else when,
        },
    )
    serve = f.markets.get("serve_point_prob") or {}
    aside = None
    say_bars = f"El modelo le da {pa} por ciento a {a} y {pb} a {b}."
    if serve.get("A") and serve.get("B"):
        sa, sb = _pct(serve["A"]), _pct(serve["B"])
        aside = f"Puntos ganados con su saque: {sa}% contra {sb}%"
        say_bars += f" Con su saque, {a} gana el {sa} por ciento de los puntos y {b} el {sb}."
    bars = Scene(
        "bars",
        say_bars,
        {
            "generic": True,
            "title": "¿Quién gana?",
            "rows": [[a, w["A"]], [b, w["B"]]],
            "scale": 1.0,
            **({"aside": aside} if aside else {}),
        },
    )
    scenes = [hook, bars]
    sets = f.markets.get("set_scores") or {}
    games = f.markets.get("expected_games")
    if sets:
        top = sorted(sets.items(), key=lambda kv: -kv[1])[:4]
        rows = [[_set_label(k, a, b), float(v)] for k, v in top]
        say = f"El resultado más probable: {rows[0][0]}, con {_pct(rows[0][1])} por ciento."
        aside_g = None
        if games:
            say += f" Y el modelo espera unos {_dec(games)} games en total."
            aside_g = f"Games esperados: {_dec(games)}"
        scenes.append(
            Scene(
                "sets",
                say,
                {
                    "generic": True,
                    "title": "¿Cómo termina?",
                    "rows": rows,
                    "scale": max(0.5, rows[0][1]),
                    **({"aside": aside_g} if aside_g else {}),
                },
            )
        )
    if n_high:
        cta_say = (
            f"Mañana hay {n_high} partidos de tenis con confianza alta, y están gratis en nuestro "
            "canal de Telegram: JC Probabilidades."
        )
    else:
        cta_say = (
            "Los partidos con favorito claro los publicamos gratis cada noche en nuestro canal de "
            "Telegram: JC Probabilidades."
        )
    scenes.append(
        Scene(
            "cta",
            cta_say,
            {
                "generic": True,
                "question": "¿Quieres más partidos con favorito claro?",
                "n_high": n_high,
                "num_label": "de tenis con confianza Alta mañana",
                "handle": TELEGRAM_HANDLE,
                "disclaimer": "Probabilidades de un modelo estadístico, no recomendaciones. +18",
            },
        )
    )
    if recap and recap.n:
        scenes.insert(0, recap_scene(recap))
    if intro:
        scenes.insert(0, INTRO_SCENE)
    title = f"{a} vs {b}: ¿quién gana mañana? #tenis #shorts"
    yesterday = f"Ayer, confianza Alta: {recap.hits} de {recap.n} aciertos.\n\n" if recap else ""
    description = yesterday + (
        f"{comp}{', ' + surface_es if surface_es else ''}. Probabilidades del modelo: {a} {pa}% y "
        f"{b} {pb}%.\n\nLos partidos de confianza Alta, cada noche en Telegram: "
        "https://t.me/JCProbabilidades\n\nProbabilidades estimadas por un modelo estadístico "
        "(Elo + cadena de Markov punto a punto), no recomendaciones. +18\n\n#tenis #estadistica"
    )
    pinned = (
        f"¿Quién gana, {a} o {b}? Comenta 1 o 2 👇\n\n"
        "📊 Los partidos de confianza Alta de mañana están gratis en Telegram: "
        "@JCProbabilidades (enlace en el perfil del canal)."
    )
    return ShortScript(f.event_id, title[:100], description, scenes, pinned, "tennis", surface)
