"""Guion del Short semanal de Atlético Nacional (datos del semestre frente a sus rivales).

Escenas: resultado de la última fecha → goles recibidos por partido (los cuatro equipos) →
llegadas al arco recibidas por partido → "¿Por qué tanta crítica?" (la crítica del momento
contra los números) → cierre con la próxima fecha. Todo determinista; la crítica del momento
es un texto configurable (``NACIONAL_CRITICA``) porque cambia con la semana.
"""

from __future__ import annotations

from sports_analytics.content.nacional import NACIONAL_ID, NacionalReport, TeamLine, ranking
from sports_analytics.video.script import TELEGRAM_HANDLE, Scene, ShortScript

DEFAULT_CRITICISM = (
    "A Nacional se le critica por cómo se ordena cuando pierde la pelota "
    "y por no sostener las ventajas."
)
ORD = {1: "el mejor", 2: "el segundo", 3: "el tercero", 4: "el cuarto"}


def _d(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _rivals_phrase(r: NacionalReport) -> str:
    if r.phase == "cuadrangular":
        return "sus rivales del cuadrangular"
    if r.phase == "final":
        return "su rival en la final"
    return "los mejores de la tabla"


def _spoken_round(r: NacionalReport) -> str:
    """'la fecha 1 del cuadrangular', 'la final', 'la fecha 12'."""
    num = r.round.rsplit("-", 1)[-1].strip()
    if r.phase == "cuadrangular":
        return f"la fecha {num} del cuadrangular"
    if r.phase == "final":
        return "la final"
    return f"la fecha {num}"


def _result(r: NacionalReport) -> str:
    m = r.last
    home = m.home_id == NACIONAL_ID
    gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
    rival = m.away if home else m.home
    if gf > ga:
        return f"Nacional le ganó {gf} a {ga} a {rival}"
    if gf < ga:
        return f"Nacional perdió {ga} a {gf} con {rival}"
    return f"Nacional empató {gf} a {ga} con {rival}"


def _place(teams: list[TeamLine], attr: str) -> tuple[int, TeamLine, TeamLine]:
    order = ranking(teams, attr)
    pos = next(i for i, t in enumerate(order) if t.team_id == NACIONAL_ID) + 1
    return pos, order[0], order[-1]


def _pos_text(pos: int, n: int) -> str:
    if pos == 1:
        return "el mejor del grupo" if n > 2 else "mejor que su rival"
    if pos == n:
        return "el peor del grupo" if n > 2 else "peor que su rival"
    return f"{ORD[pos]} del grupo"


def _bars(r: NacionalReport, attr: str, title: str, unit: str) -> Scene:
    teams = r.teams
    order = ranking(teams, attr)
    pos, best, worst = _place(teams, attr)
    nac = r.nacional
    rows = [(t.name, round(t.per_game(attr), 2)) for t in order]
    tags = {
        best.name: {"kind": "best", "text": "Mejor"},
        worst.name: {"kind": "worst", "text": "Peor"},
    }
    if best.team_id == NACIONAL_ID:
        say = (
            f"En {unit}, Nacional es el mejor: {_d(nac.per_game(attr))} por partido. "
            f"El peor es {worst.name}, con {_d(worst.per_game(attr))}."
        )
    elif worst.team_id == NACIONAL_ID:
        say = (
            f"En {unit}, Nacional es el peor: {_d(nac.per_game(attr))} por partido. "
            f"El mejor es {best.name}, con {_d(best.per_game(attr))}."
        )
    else:
        say = (
            f"En {unit}, el mejor es {best.name}, con {_d(best.per_game(attr))} por partido, y el "
            f"peor {worst.name}, con {_d(worst.per_game(attr))}. Nacional queda "
            f"{_pos_text(pos, len(teams))}: {_d(nac.per_game(attr))}."
        )
    return Scene(
        "bars",
        say,
        {"rows": rows, "title": title, "fmt": "dec", "me": nac.name, "tags": tags},
    )


def _why(r: NacionalReport, criticism: str) -> Scene:
    nac = r.nacional
    rivals = [t for t in r.teams if t.team_id != NACIONAL_ID]

    def avg(attr):
        vals = [t.per_game(attr) for t in rivals]
        return sum(vals) / len(vals) if vals else 0.0

    duels, says = [], [criticism, "¿Qué dicen los números?"]
    ns, rs = nac.per_game("shots_against"), avg("shots_against")
    duels.append(
        {
            "title": "Tiros recibidos por partido",
            "left": ("Nacional", ns),
            "right": ("Rivales", rs),
            "fmt": "dec",
        }
    )
    if ns > rs + 0.5:
        says.append(
            f"Le rematan más que a sus rivales: {_d(ns)} tiros por partido contra {_d(rs)}."
        )
    elif ns < rs - 0.5:
        says.append(
            f"Le rematan menos que a sus rivales: {_d(ns)} tiros por partido contra {_d(rs)}."
        )
    else:
        says.append(f"Le rematan casi lo mismo que a sus rivales: {_d(ns)} tiros por partido.")
    leads = sum(t.ht_leads for t in rivals)
    lost = sum(t.ht_leads_not_won for t in rivals)
    if nac.ht_leads:
        np_, rp = nac.ht_leads_not_won / nac.ht_leads, (lost / leads if leads else 0.0)
        duels.append(
            {
                "title": "Ganaba al descanso y no ganó",
                "left": ("Nacional", np_),
                "right": ("Rivales", rp),
                "fmt": "pct",
            }
        )
        n_lost = nac.ht_leads_not_won
        if np_ > rp + 0.05:
            says.append(
                f"Y las ventajas: de {nac.ht_leads} partidos que ganaba al descanso, se le "
                f"escaparon {n_lost}. Ahí la crítica tiene sustento."
            )
        else:
            says.append(
                f"Y las ventajas: de {nac.ht_leads} partidos que ganaba al descanso, solo se le "
                f"escaparon {n_lost}. Ahí los números defienden a Nacional."
            )
    nc = nac.clean_sheets / nac.played if nac.played else 0.0
    rc = sum(t.clean_sheets for t in rivals) / max(1, sum(t.played for t in rivals))
    duels.append(
        {
            "title": "Partidos sin recibir gol",
            "left": ("Nacional", nc),
            "right": ("Rivales", rc),
            "fmt": "pct",
        }
    )
    says.append(f"Además, terminó sin recibir gol en {nac.clean_sheets} de {nac.played} partidos.")
    return Scene("why", " ".join(says), {"duels": duels, "title": "¿Por qué tanta crítica?"})


def build_nacional_script(
    r: NacionalReport, criticism: str | None = None, tz: str = "America/Bogota"
) -> ShortScript:
    from zoneinfo import ZoneInfo

    from sports_analytics.video.html_engine import when_es

    m = r.last
    rivals = _rivals_phrase(r)
    hook = Scene(
        "hook",
        f"{_result(r)} en {_spoken_round(r)}. ¿Cómo está Nacional atrás frente a "
        f"{rivals}? Estos son los números del semestre.",
        {
            "home": m.home,
            "away": m.away,
            "competition": "Liga BetPlay",
            "kickoff": m.kickoff_utc,
            "kicker": "Nacional en números",
            "note_head": "terminó",
            "note_big": f"{m.home_goals} - {m.away_goals}",
            "note_foot": r.round_label,
        },
    )
    goals = _bars(r, "goals_against", "Goles recibidos por partido", "goles recibidos")
    shots = _bars(r, "sot_against", "Llegadas al arco recibidas", "llegadas al arco recibidas")
    why = _why(r, criticism or DEFAULT_CRITICISM)
    nxt = r.next
    if nxt:
        rival = nxt.away if nxt.home_id == NACIONAL_ID else nxt.home
        when = when_es(nxt.kickoff_utc, ZoneInfo(tz))
        cta_say = (
            f"¿Se justifican las críticas? Comenta sí o no. La próxima: Nacional contra {rival}, "
            f"{when.split(',')[0]}. Y si te gustan los datos, síguenos en Telegram: JC Probabilidades."
        )
        stage = "fecha del cuadrangular" if r.phase == "cuadrangular" else "fecha"
        num_label = f"{stage}: Nacional vs {rival}, {when}"
        num = (
            int(nxt.round.rsplit("-", 1)[-1])
            if nxt.round.rsplit("-", 1)[-1].strip().isdigit()
            else 0
        )
    else:
        cta_say = (
            "¿Se justifican las críticas? Comenta sí o no. Y si te gustan los datos, síguenos en "
            "Telegram: JC Probabilidades."
        )
        num_label, num = "", 0
    cta = Scene(
        "cta",
        cta_say,
        {
            "question": "¿Se justifican las críticas?",
            "n_high": num,
            "num_label": num_label if num else "",
            "handle": TELEGRAM_HANDLE,
            "disclaimer": "Datos de la Liga BetPlay 2026-II, solo partidos del semestre.",
        },
    )
    pos, _, _ = _place(r.teams, "goals_against")
    n = len(r.teams)
    if pos == n:
        title = (
            "Nacional, el que más goles recibe de su grupo. ¿Se justifican las críticas? #shorts"
        )
    elif pos == 1:
        title = "Nacional, el que menos goles recibe de su grupo. ¿Por qué lo critican? #shorts"
    else:
        title = f"¿Defiende mal Nacional? Los números de {_spoken_round(r)} #shorts"
    table = "\n".join(
        f"{t.name}: {_d(t.per_game('goals_against'))} goles recibidos y "
        f"{_d(t.per_game('sot_against'))} tiros al arco recibidos por partido"
        for t in ranking(r.teams, "goals_against")
    )
    description = (
        f"{_result(r)} ({r.round_label}). Así está atrás frente a {rivals}, con los partidos "
        f"de liga del semestre:\n\n{table}\n\nDatos: API-Football, Liga BetPlay 2026-II (todos "
        "contra todos y fases finales del segundo semestre).\n\nMás datos cada día en Telegram: "
        "https://t.me/JCProbabilidades\n\n#AtléticoNacional #Nacional #LigaBetPlay #futbolcolombiano"
    )
    pinned = (
        "¿Se justifican las críticas a Nacional? Comenta SÍ o NO 👇\n\n"
        "📊 Más datos en Telegram: @JCProbabilidades (enlace en el perfil del canal)."
    )
    s = ShortScript(
        f"nacional-{r.round}", title[:100], description, [hook, goals, shots, why, cta], pinned
    )
    s.sport = "football"
    return s
