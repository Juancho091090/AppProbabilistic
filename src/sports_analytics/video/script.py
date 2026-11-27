"""Guiones de los Shorts diarios (fútbol y tenis).

Determinista: todas las cifras salen del pronóstico (sin LLM).

Fútbol (desde el 22-nov): "un favorito claro de mañana", un partido cuyo favorito del
mercado (sin margen) llega al 60 %, la misma regla que completa el canal. Se prefiere uno
publicado en el canal y, entre ellos, el de equipos más fuertes (Elo sumado), que es el que
más gente reconoce. El video pregunta "¿se cumple?" y remite a Telegram, donde a la mañana
siguiente se publica el resultado. Si ningún partido llega al 60 %, sale el formato anterior:
el partido de confianza Media/Baja con el 1X2 más repartido (mayor entropía).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import DailyReportPayload

TELEGRAM_HANDLE = "@JCProbabilidades"
OPEN_LEVELS = ("medium", "low")
MARKET_FAVORITE = 0.60  # misma regla que reporting.channel (mercado_favorito_60)


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


def pick_market_favorite(
    payload: DailyReportPayload,
    market_fair: Mapping[str, Sequence[float]] | None,
    preferred: set[str] | frozenset[str] = frozenset(),
) -> tuple[MatchForecast, int] | None:
    """(partido, índice del favorito: 0 local, 2 visitante) con favorito del mercado ≥ 60 %."""
    cands = []
    for f in payload.all_forecasts():
        fair = (market_fair or {}).get(f.event_id)
        if f.sport != "football" or not fair or "1x2" not in f.markets:
            continue
        i = 0 if fair[0] >= fair[2] else 2
        if fair[i] >= MARKET_FAVORITE and fair[i] > fair[1]:
            cands.append((f, i))
    if not cands:
        return None

    def rank(c):
        f, _ = c
        elo = f.context.get("elo") or {}
        return (
            f.event_id in preferred,
            sum(elo.values()) if elo else 0.0,
            -f.kickoff_utc.timestamp(),
        )

    return max(cands, key=rank)


def _pct(p: float) -> int:
    return int(round(p * 100))


def _dec(x: float) -> str:
    """Decimal con coma (así lo lee bien la voz en español)."""
    return f"{x:.1f}".replace(".", ",")


def _why(f: MatchForecast, fair: Sequence[float] | None, fav: int | None = None) -> list[dict]:
    """Duelos de la escena "¿por qué?": dos valores enfrentados y la frase narrada.

    ``fmt``: int (Elo), dec (goles) o pct (probabilidades). ``fav`` (0 local, 2 visitante):
    el video es de un favorito claro y las frases explican esa ventaja."""
    if fav is not None:
        return _why_favorite(f, fair, fav)
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


def _why_favorite(f: MatchForecast, fair: Sequence[float] | None, fav: int) -> list[dict]:
    names = (f.home_or_a, f.away_or_b)
    me, rival = (names[0], names[1]) if fav == 0 else (names[1], names[0])
    side = "home" if fav == 0 else "away"
    other = "away" if fav == 0 else "home"
    out = []
    elo = f.context.get("elo") or {}
    if elo:
        gap = elo[side] - elo[other]
        if gap >= 60:
            say = f"En Elo, {me} saca {gap:.0f} puntos: {elo[side]:.0f} contra {elo[other]:.0f}."
        elif gap > -60:
            say = (
                f"En Elo están cerca, {elo[side]:.0f} contra {elo[other]:.0f}: "
                "la ventaja no viene de ahí."
            )
        else:
            say = (
                f"Curioso: en Elo, {rival} está por encima, {elo[other]:.0f} contra "
                f"{elo[side]:.0f}."
            )
        out.append(
            {
                "title": "Fuerza (Elo)",
                "left": (f.home_or_a, float(elo["home"])),
                "right": (f.away_or_b, float(elo["away"])),
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
                    f"El modelo espera {_dec(lam[side])} goles de {me} "
                    f"y {_dec(lam[other])} de {rival}."
                ),
            }
        )
    if fair:
        model = f.markets["1x2"][side]
        m = fair[fav]
        if model >= m + 0.05:
            say = f"Y el modelo es todavía más optimista: le da {_pct(model)} por ciento."
        elif model <= m - 0.08:
            say = (
                f"Pero ojo: el modelo es más prudente, le da solo {_pct(model)} por ciento. "
                "Ahí está la duda."
            )
        else:
            say = f"Y el modelo coincide: le da {_pct(model)} por ciento."
        out.append(
            {
                "title": f"Favorito: {me}",
                "left": ("Mercado", float(m)),
                "right": ("Modelo", float(model)),
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


HOST_NAME = "Leo"


def host_scene(recap=None, bridge: str = "Y para mañana te traigo un favorito claro.") -> Scene:
    """Leo, el analista ficticio (dibujado), saluda, cuenta cómo nos fue ayer y presenta el
    partido. Reemplaza la escena del resumen cuando el Short usa presentador."""
    if recap and recap.n:
        base = recap_scene(recap)
        say = f"Hola, soy {HOST_NAME}. " + base.narration + " " + bridge
        data = {
            "generic": True,
            "top": "Ayer, en el canal de Telegram",
            "big": f"{recap.hits} de {recap.n}",
            "small": "acertados, fallos incluidos" if recap.hits < recap.n else "acertados",
        }
    else:
        say = f"Hola, soy {HOST_NAME}, el analista de JC Probabilidades. {bridge}"
        data = {"generic": True, "top": "Los números de mañana", "big": "", "small": ""}
    data["name"] = f"{HOST_NAME}, analista de datos"
    return Scene("host", say, data)


def recap_scene(recap) -> Scene:
    """Escena "así nos fue ayer": se cuentan aciertos y fallos, siempre todos."""
    h, n = recap.hits, recap.n
    plural = "partido" if n == 1 else "partidos"
    # En el canal hay confianza Alta y favoritos del mercado: se nombra el canal, no el nivel
    what = (
        f"{plural} que publicamos en Telegram"
        if recap.source == "canal"
        else f"{plural} de confianza alta"
    )
    if recap.rate >= GOOD_DAY:
        say = f"¡Gran día ayer! Acertamos {h} de {n} {what}."
        mood = "good"
    elif recap.rate >= OK_DAY:
        say = f"Así nos fue ayer: acertamos {h} de {n} {what}."
        mood = "ok"
    else:
        say = (
            f"Ayer no fue nuestro día: acertamos {h} de {n} {what}. "
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
    published: Sequence[str] = (),
    n_published: int = 0,
    host: bool = False,
) -> ShortScript | None:
    """``recap`` (video.recap.Recap) añade el resumen de ayer; ``intro`` antepone una
    presentación corta del canal (pensada para los primeros videos). ``published``: ids de
    los partidos del canal de esa noche (se prefieren); ``n_published``: cuántos son."""
    picked = pick_market_favorite(payload, market_fair, set(published))
    if picked:
        out = _favorite_script(payload, market_fair, *picked, recap, intro, n_published or n_high)
        return _with_host(out, recap) if host else out
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


def _favorite_script(payload, market_fair, f, fav, recap, intro, n_channel) -> ShortScript:
    x = f.markets["1x2"]
    fair = market_fair[f.event_id]
    names = (f.home_or_a, f.away_or_b)
    me, rival = (names[0], names[1]) if fav == 0 else (names[1], names[0])
    m = _pct(fair[fav])
    h, d, a = _pct(x["home"]), _pct(x["draw"]), _pct(x["away"])
    p_model = h if fav == 0 else a
    opening = "Y para mañana, un favorito claro" if recap else "Mañana, un favorito claro"
    hook = Scene(
        "hook",
        f"{opening}: {me}, que juega contra {rival}. ¿Se cumple?",
        {
            "home": f.home_or_a,
            "away": f.away_or_b,
            "competition": f.competition,
            "kickoff": f.kickoff_utc,
            "kicker": opening,
            "note_head": f"el mercado ve a {me}:",
            "note_big": f"{m} %",
            "note_foot": "de probabilidad de ganar",
        },
    )
    bars = Scene(
        "bars",
        f"El mercado le da a {me} un {m} por ciento. El modelo: {h} por ciento a "
        f"{f.home_or_a}, {d} al empate y {a} a {f.away_or_b}.",
        {
            "rows": [(f.home_or_a, x["home"]), ("Empate", x["draw"]), (f.away_or_b, x["away"])],
            "title": "¿Se cumple el favorito?",
            "stamp": f"Mercado: {m} % {me}",
        },
    )
    factors = _why(f, fair, fav)
    why = Scene(
        "why",
        " ".join(x["say"] for x in factors) or f"Los números acompañan a {me}.",
        {"duels": factors, "title": f"¿Por qué {me}?"},
    )
    many = f"Este y {n_channel - 1} partidos más" if n_channel > 1 else "Este partido"
    cta = Scene(
        "cta",
        f"{many} están gratis esta noche en nuestro canal de Telegram, JC Probabilidades. "
        "Y mañana en la mañana publicamos si se cumplieron, aciertos y fallos.",
        {
            "n_high": n_channel,
            "handle": TELEGRAM_HANDLE,
            "question": "¿Se cumple? Mañana te lo contamos",
            "num_label": "partidos gratis esta noche"
            if n_channel != 1
            else "partido gratis esta noche",
        },
    )
    title = f"{me} es favorito con {m} %. ¿Se cumple? #shorts"
    yesterday = (
        f"Ayer, en el canal: {recap.hits} de {recap.n} aciertos.\n\n" if recap and recap.n else ""
    )
    description = yesterday + (
        f"{f.competition}: {f.home_or_a} vs {f.away_or_b}. El mercado le da {m}% a {me}; "
        f"el modelo, {p_model}%.\n\nLos partidos de mañana, cada noche en Telegram, y el "
        "resultado a la mañana siguiente: https://t.me/JCProbabilidades\n\n"
        "Probabilidades estimadas por un modelo estadístico (Elo + Poisson/Dixon-Coles) y por el "
        "mercado sin margen, no recomendaciones. +18\n\n#futbol #estadistica #probabilidades"
    )
    scenes = [hook, bars, why, cta]
    if recap and recap.n:
        scenes.insert(0, recap_scene(recap))
    if intro:
        scenes.insert(0, INTRO_SCENE)
    pinned = (
        "¿Se cumple el favorito? Comenta SÍ o NO 👇 Mañana publicamos el resultado.\n\n"
        "📊 Los partidos de mañana están gratis en Telegram: @JCProbabilidades "
        "(enlace en el perfil del canal)."
    )
    return ShortScript(f.event_id, title[:100], description, scenes, pinned)


def _with_host(script: ShortScript, recap) -> ShortScript:
    """Pone a Leo al inicio: reemplaza el resumen (o va antes del gancho si no hay resumen)."""
    scenes = [sc for sc in script.scenes if sc.kind not in ("recap",)]
    at = 1 if scenes and scenes[0].kind == "intro" else 0
    scenes.insert(at, host_scene(recap))
    # el gancho ya no repite "Y para mañana": lo dijo Leo
    for sc in scenes:
        if sc.kind == "hook" and sc.narration.startswith("Y para mañana, un favorito claro: "):
            sc.narration = sc.narration.replace("Y para mañana, un favorito claro: ", "", 1)
            sc.narration = sc.narration[0].upper() + sc.narration[1:]
    script.scenes = scenes
    return script


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
