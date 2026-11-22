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


def _d(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


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


def _bars(r: NacionalReport, attr: str, title: str, unit: str, nd: int = 1) -> Scene:
    """``nd``: decimales; con goles por partido se usan 2 para que no se vean empates falsos
    (0,90 frente a 0,95 se verían ambos como 0,9)."""
    teams = r.teams
    order = ranking(teams, attr)
    pos, best, worst = _place(teams, attr)
    nac = r.nacional
    rows = [(t.name, round(t.per_game(attr), 3)) for t in order]
    tags = {
        best.name: {"kind": "best", "text": "Mejor"},
        worst.name: {"kind": "worst", "text": "Peor"},
    }
    if best.team_id == NACIONAL_ID:
        say = (
            f"En {unit}, Nacional es el mejor: {_d(nac.per_game(attr), nd)} por partido. "
            f"El peor es {worst.name}, con {_d(worst.per_game(attr), nd)}."
        )
    elif worst.team_id == NACIONAL_ID:
        say = (
            f"En {unit}, Nacional es el peor: {_d(nac.per_game(attr), nd)} por partido. "
            f"El mejor es {best.name}, con {_d(best.per_game(attr), nd)}."
        )
    else:
        say = (
            f"En {unit}, el mejor es {best.name}, con {_d(best.per_game(attr), nd)} por partido, y el "
            f"peor {worst.name}, con {_d(worst.per_game(attr), nd)}. Nacional queda "
            f"{_pos_text(pos, len(teams))}: {_d(nac.per_game(attr), nd)}."
        )
    return Scene(
        "bars",
        say,
        {
            "rows": rows,
            "title": title,
            "fmt": "dec2" if nd == 2 else "dec",
            "me": nac.name,
            "tags": tags,
        },
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
        lost_txt = (
            "no se le escapó ninguno"
            if n_lost == 0
            else ("se le escapó 1" if n_lost == 1 else f"se le escaparon {n_lost}")
        )
        if np_ > rp + 0.05:
            says.append(
                f"Y las ventajas: de {nac.ht_leads} partidos que ganaba al descanso, "
                f"{lost_txt}. Ahí la crítica tiene sustento."
            )
        else:
            says.append(
                f"Y las ventajas: de {nac.ht_leads} partidos que ganaba al descanso, "
                f"{'solo ' if n_lost else ''}{lost_txt}. Ahí los números defienden a Nacional."
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
    goals = _bars(r, "goals_against", "Goles recibidos por partido", "goles recibidos", 2)
    shots = _bars(r, "sot_against", "Llegadas al arco recibidas", "llegadas al arco recibidas", 2)
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
        f"{t.name}: {_d(t.per_game('goals_against'), 2)} goles recibidos y "
        f"{_d(t.per_game('sot_against'), 2)} tiros al arco recibidos por partido"
        for t in ranking(r.teams, "goals_against")
    )
    gaps = "".join(
        f" {t.name}: tiros de {t.stats_games} de {t.played} partidos (uno sin datos)."
        if t.played - t.stats_games == 1
        else f" {t.name}: tiros de {t.stats_games} de {t.played} partidos."
        for t in r.teams
        if t.stats_games < t.played
    )
    description = (
        f"{_result(r)} ({r.round_label}). Así está atrás frente a {rivals}, con los partidos "
        f"de liga del semestre:\n\n{table}\n\nDatos: API-Football, Liga BetPlay 2026-II (todos "
        f"contra todos y fases finales del segundo semestre).{gaps}\n\nMás datos cada día en "
        "Telegram: "
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


# ------------------------------------------------ formato "fecha": partido a partido


def _match_scene(m, first_other: bool) -> Scene | None:
    from sports_analytics.content.nacional import NACIONAL_ID as NAC

    h, a = m.home, m.away
    head = f"{h.name} {h.goals}-{a.goals} {a.name}"
    if not (h.has_stats() and a.has_stats()):
        return None
    duels = [
        {
            "title": "Posesión",
            "left": (h.name, h.possession / 100),
            "right": (a.name, a.possession / 100),
            "fmt": "pct",
        },
        {
            "title": "Tiros",
            "left": (h.name, float(h.shots)),
            "right": (a.name, float(a.shots)),
            "fmt": "int",
        },
        {
            "title": "Tiros al arco",
            "left": (h.name, float(h.sot)),
            "right": (a.name, float(a.sot)),
            "fmt": "int",
        },
        {
            "title": "Tiros dentro del área",
            "left": (h.name, float(h.inside)),
            "right": (a.name, float(a.inside)),
            "fmt": "int",
        },
    ]
    ball = h if h.possession >= a.possession else a
    shooter = h if h.shots >= a.shots else a
    lead = (
        "En el otro grupo, "
        if first_other
        else (
            "Y en el clásico, "
            if NAC in (h.team_id, a.team_id) and {h.team_id, a.team_id} == {1137, 1128}
            else ""
        )
    )
    say = f"{lead}{head.replace('-', ' a ', 1)}."
    if h.possession == a.possession:
        say += " La pelota quedó repartida"
    else:
        say += f" {ball.name} tuvo más la pelota, {ball.possession} por ciento,"
    if h.shots == a.shots:
        say += f"{',' if h.possession != a.possession else ''} y los dos remataron {h.shots} veces."
    else:
        other = a if shooter is h else h
        if h.possession == a.possession:
            say += f" y {shooter.name} remató más: "
        elif shooter is ball:
            say += " y remató más: "
        else:
            say += f" pero {shooter.name} remató más: "
        say += f"{shooter.shots} tiros contra {other.shots}, {shooter.sot} al arco."
    winner = h if h.goals > a.goals else (a if a.goals > h.goals else None)
    if winner and winner.shots < (a if winner is h else h).shots:
        say += f" {winner.name} ganó con menos llegadas: más efectivo."
    return Scene("why", say, {"duels": duels, "title": head})


def _defense_scene(rr) -> Scene:
    from sports_analytics.content.nacional import NACIONAL_ID as NAC

    rows = []
    for m in rr.matches:
        for side in (m.home, m.away):
            rival = m.conceded(side)
            if rival.sot is not None:
                rows.append((side, rival.sot, rival.inside if rival.inside is not None else 99))
    rows.sort(key=lambda r: (r[1], r[2]))
    lo, hi = rows[0][1], rows[-1][1]
    best = [s.name for s, v, _ in rows if v == lo]
    worst = [s.name for s, v, _ in rows if v == hi]
    tags = {n: {"kind": "best", "text": "Mejor"} for n in best}
    tags |= {n: {"kind": "worst", "text": "Peor"} for n in worst if n not in tags}

    def names(ns):
        return ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " y " + ns[-1]

    def tiros(n):
        return "1 tiro" if n == 1 else f"{n} tiros"

    say = (
        f"Y las defensas de la fecha: {'la mejor fue la de' if len(best) == 1 else 'las mejores fueron las de'} "
        f"{names(best)}, con {tiros(lo)} al arco en contra. "
        f"{'La que más sufrió' if len(worst) == 1 else 'Las que más sufrieron'}: {names(worst)}, con {tiros(hi)}."
    )
    nac = next((r for r in rows if r[0].team_id == NAC), None)
    if nac:
        pos = 1 + sum(1 for _, v, _ in rows if v < nac[1])
        if nac[1] == lo:
            others = [n for n in best if n != "Nacional"]
            where = "la mejor de la fecha" + (f", empatada con {names(others)}" if others else "")
        elif nac[1] == hi:
            where = "de las que más sufrieron"
        else:
            where = f"puesto {pos} de {len(rows)}"
        inside = f", {nac[2]} dentro del área" if nac[2] != 99 else ""
        say += f" Nacional recibió {tiros(nac[1])} al arco{inside}: {where}."
    data = {
        "rows": [(s.name, float(v)) for s, v, _ in rows],
        "title": "Llegadas al arco recibidas",
        "fmt": "int",
        "me": "Nacional" if nac else None,
        "tags": tags,
    }
    return Scene("bars", say, data)


def build_nacional_round_script(rr, tz: str = "America/Bogota") -> ShortScript:
    """La jornada partido a partido (todos los grupos) y las defensas de la fecha."""
    from zoneinfo import ZoneInfo

    from sports_analytics.video.html_engine import when_es

    m = rr.last
    num = rr.round.rsplit("-", 1)[-1].strip()
    fake = NacionalReport(
        "cuadrangular" if "uadrangular" in rr.round else "liga",
        rr.round,
        rr.round_label,
        [],
        m,
        rr.next,
    )
    hook = Scene(
        "hook",
        f"{_result(fake)} en {_spoken_round(fake)}. Así fueron los {len(rr.matches)} partidos "
        "de la fecha, en números.",
        {
            "home": m.home,
            "away": m.away,
            "competition": "Liga BetPlay",
            "kickoff": m.kickoff_utc,
            "kicker": f"La fecha {num} en números" if num.isdigit() else "La fecha en números",
            "note_head": "terminó",
            "note_big": f"{m.home_goals} - {m.away_goals}",
            "note_foot": rr.round_label,
        },
    )
    scenes = [hook]
    first_other = True
    skipped = []
    for match in rr.matches:
        sc = _match_scene(match, first_other and not match.nacional_group)
        if sc is None:
            skipped.append(
                f"{match.home.name} {match.home.goals}-{match.away.goals} {match.away.name}"
            )
            continue
        if not match.nacional_group:
            first_other = False
        scenes.append(sc)
    scenes.append(_defense_scene(rr))
    nxt = rr.next
    if nxt:
        rival = nxt.away if nxt.home_id == NACIONAL_ID else nxt.home
        when = when_es(nxt.kickoff_utc, ZoneInfo(tz))
        nnum = nxt.round.rsplit("-", 1)[-1].strip()
        cta_say = (
            f"¿Qué equipo te convenció más en la fecha? Comenta. La próxima: Nacional contra "
            f"{rival}, {when.split(',')[0]}. Más datos en Telegram: JC Probabilidades."
        )
        stage = "fecha del cuadrangular" if "uadrangular" in nxt.round else "fecha"
        n_big, label = (
            (int(nnum), f"{stage}: Nacional vs {rival}, {when}") if nnum.isdigit() else (0, "")
        )
    else:
        cta_say = "¿Qué equipo te convenció más en la fecha? Comenta. Más datos en Telegram: JC Probabilidades."
        n_big, label = 0, ""
    scenes.append(
        Scene(
            "cta",
            cta_say,
            {
                "question": "¿Quién te convenció más?",
                "n_high": n_big,
                "num_label": label,
                "handle": TELEGRAM_HANDLE,
                "disclaimer": "Datos de la Liga BetPlay 2026-II (API-Football).",
            },
        )
    )
    lines = []
    for match in rr.matches:
        h, a = match.home, match.away
        lines.append(f"{h.name} {h.goals}-{a.goals} {a.name}")
        if h.has_stats() and a.has_stats():
            for s in (h, a):
                lines.append(
                    f"  {s.name}: posesión {s.possession}%, {s.shots} tiros, {s.sot} al arco, "
                    f"{s.inside} dentro del área"
                )
        else:
            lines.append("  (sin estadísticas completas de este partido)")
    spoken = _spoken_round(fake)
    title = f"{m.home} {m.home_goals}-{m.away_goals} {m.away} y toda {spoken} en números #shorts"
    description = (
        f"{spoken[0].upper() + spoken[1:]} de la Liga BetPlay, partido a partido:\n\n"
        + "\n".join(lines)
        + "\n\nDatos: API-Football, Liga BetPlay 2026-II.\n\nMás datos cada día en Telegram: "
        "https://t.me/JCProbabilidades\n\n#AtléticoNacional #Nacional #LigaBetPlay #futbolcolombiano"
    )
    pinned = (
        f"¿Qué equipo te convenció más en {spoken}? Comenta 👇\n\n"
        "📊 Más datos en Telegram: @JCProbabilidades (enlace en el perfil del canal)."
    )
    s = ShortScript(f"nacional-fecha-{rr.round}", title[:100], description, scenes, pinned)
    s.notes = skipped  # partidos sin estadísticas (no salen en el video)
    return s
