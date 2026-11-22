"""Atlético Nacional frente a sus rivales directos: datos del semestre para el Short semanal.

Rivales directos según la fase de Nacional en el torneo del semestre (Clausura):

* cuadrangulares: los otros tres equipos de su grupo (se deducen de los cruces);
* final: el otro finalista;
* todos contra todos: los tres mejores de la tabla del semestre sin contar a Nacional.

Métricas por equipo, solo con partidos de liga del semestre (todos contra todos y fases
finales; nada del primer semestre): goles recibidos, tiros y tiros al arco del rival
("llegadas"), vallas invictas, ventajas al descanso que no terminaron en victoria y goles a
favor. Todo sale de API-Football: 1 llamada (partidos de la temporada) + 1 por cada 20
partidos (``/fixtures?ids=`` trae estadísticas del partido).

Cuándo sale el video: el día siguiente al último partido de la jornada en la que jugó
Nacional (si hay fechas entre semana salen dos por semana).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LEAGUE_ID = 239
NACIONAL_ID = 1137
FINISHED = ("FT", "AET", "PEN")

# Nombres como se dicen en Colombia (la API los trae sin tildes o con el nombre largo)
SHORT_NAMES = {
    1137: "Nacional",
    1128: "Medellín",
    1136: "Once Caldas",
    1139: "Santa Fe",
    1138: "América",
    1125: "Millonarios",
    1127: "Deportivo Cali",
    1131: "Bucaramanga",
    1135: "Junior",
    1126: "Tolima",
    1134: "Pasto",
    1130: "Pereira",
    1140: "Águilas Doradas",
    1141: "Alianza",
}
_ACCENTS = {
    "Medellin": "Medellín",
    "America": "América",
    "Aguilas": "Águilas",
    "Bogota": "Bogotá",
    "Cucuta": "Cúcuta",
    "Atletico": "Atlético",
}


def team_name(team_id: int, api_name: str) -> str:
    if team_id in SHORT_NAMES:
        return SHORT_NAMES[team_id]
    return " ".join(_ACCENTS.get(w, w) for w in api_name.split())


@dataclass
class TeamLine:
    team_id: int
    name: str
    played: int = 0
    goals_for: int = 0
    goals_against: int = 0
    shots_against: int = 0
    sot_against: int = 0
    stats_games: int = 0  # partidos con estadísticas (tiros) disponibles
    clean_sheets: int = 0
    ht_leads: int = 0
    ht_leads_not_won: int = 0
    points: int = 0

    def per_game(self, attr: str) -> float:
        base = self.stats_games if attr in ("shots_against", "sot_against") else self.played
        return getattr(self, attr) / base if base else 0.0


@dataclass
class MatchLine:
    round: str
    kickoff_utc: datetime
    home_id: int
    away_id: int
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    finished: bool


@dataclass
class NacionalReport:
    phase: str  # cuadrangular | final | liga
    round: str  # "Clausura - Quadrangular - 1"
    round_label: str  # "Cuadrangular, fecha 1"
    teams: list[TeamLine]  # Nacional + rivales directos
    last: MatchLine
    next: MatchLine | None = None
    calls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def nacional(self) -> TeamLine:
        return next(t for t in self.teams if t.team_id == NACIONAL_ID)


def _match(it: dict) -> MatchLine:
    t, g = it["teams"], it["goals"]
    return MatchLine(
        round=it["league"].get("round", ""),
        kickoff_utc=datetime.fromisoformat(it["fixture"]["date"]),
        home_id=t["home"]["id"],
        away_id=t["away"]["id"],
        home=team_name(t["home"]["id"], t["home"]["name"]),
        away=team_name(t["away"]["id"], t["away"]["name"]),
        home_goals=g.get("home"),
        away_goals=g.get("away"),
        finished=it["fixture"]["status"]["short"] in FINISHED,
    )


def semester(fixtures: list[dict]) -> list[dict]:
    """Partidos del segundo semestre (todo lo que no es Apertura)."""
    return [it for it in fixtures if "apertura" not in it["league"].get("round", "").lower()]


def round_label(round_name: str) -> str:
    r = round_name.lower()
    num = round_name.rsplit("-", 1)[-1].strip()
    if "quadrangular" in r:
        return f"Cuadrangular, fecha {num}"
    if "final" in r and "semi" not in r and "quarter" not in r:
        return "Final"
    return f"Fecha {num}" if num.isdigit() else round_name


def phase_of(round_name: str) -> str:
    r = round_name.lower()
    if "quadrangular" in r:
        return "cuadrangular"
    if r.endswith("final") or r.endswith("- final"):
        return "final"
    return "liga"


def _nacional_matches(fixtures: list[dict]) -> list[dict]:
    return [
        it
        for it in fixtures
        if NACIONAL_ID in (it["teams"]["home"]["id"], it["teams"]["away"]["id"])
    ]


def last_round(fixtures: list[dict]) -> str | None:
    """Jornada del último partido jugado por Nacional en el semestre."""
    done = [
        it
        for it in _nacional_matches(semester(fixtures))
        if it["fixture"]["status"]["short"] in FINISHED
    ]
    if not done:
        return None
    return max(done, key=lambda it: it["fixture"]["date"])["league"]["round"]


def video_day(fixtures: list[dict], tz: ZoneInfo) -> date | None:
    """Día en que toca el video: el siguiente al último partido de la jornada de Nacional,
    solo si toda la jornada ya se jugó."""
    rnd = last_round(fixtures)
    if rnd is None:
        return None
    games = [it for it in fixtures if it["league"].get("round") == rnd]
    if not all(
        it["fixture"]["status"]["short"] in FINISHED + ("PST", "CANC", "ABD", "AWD", "WO")
        for it in games
    ):
        return None
    last_ko = max(datetime.fromisoformat(it["fixture"]["date"]) for it in games)
    return last_ko.astimezone(tz).date() + timedelta(days=1)


def rivals(fixtures: list[dict], rnd: str) -> list[int]:
    """Rivales directos de Nacional en la fase de ``rnd``."""
    sem = semester(fixtures)
    phase = phase_of(rnd)
    if phase == "cuadrangular":
        quad = [it for it in sem if "quadrangular" in it["league"].get("round", "").lower()]
        group = {NACIONAL_ID}
        changed = True
        while changed:  # componente conexa de los cruces del cuadrangular
            changed = False
            for it in quad:
                a, b = it["teams"]["home"]["id"], it["teams"]["away"]["id"]
                if (a in group) != (b in group):
                    group |= {a, b}
                    changed = True
        return sorted(group - {NACIONAL_ID})
    if phase == "final":
        it = next(x for x in _nacional_matches(sem) if x["league"].get("round") == rnd)
        a, b = it["teams"]["home"]["id"], it["teams"]["away"]["id"]
        return [b if a == NACIONAL_ID else a]
    table = build_lines(
        [it for it in sem if phase_of(it["league"].get("round", "")) == "liga"], None
    )
    ranked = sorted(
        (t for t in table.values() if t.team_id != NACIONAL_ID),
        key=lambda t: (t.points, t.goals_for - t.goals_against, t.goals_for),
        reverse=True,
    )
    return [t.team_id for t in ranked[:3]]


def build_lines(items: list[dict], team_ids: set[int] | None) -> dict[int, TeamLine]:
    """Métricas por equipo. ``items`` pueden traer el bloque ``statistics`` (tiros)."""
    out: dict[int, TeamLine] = {}
    for it in items:
        if it["fixture"]["status"]["short"] not in FINISHED:
            continue
        t, g = it["teams"], it["goals"]
        ht = (it.get("score") or {}).get("halftime") or {}
        ft = (it.get("score") or {}).get("fulltime") or {}
        hg = ft.get("home") if ft.get("home") is not None else g["home"]
        ag = ft.get("away") if ft.get("away") is not None else g["away"]
        shots = {}
        for block in it.get("statistics") or []:
            vals = {x["type"]: x["value"] for x in block.get("statistics", [])}
            shots[block["team"]["id"]] = (vals.get("Total Shots"), vals.get("Shots on Goal"))
        for side, other in (("home", "away"), ("away", "home")):
            tid = t[side]["id"]
            if team_ids is not None and tid not in team_ids:
                continue
            line = out.setdefault(tid, TeamLine(tid, team_name(tid, t[side]["name"])))
            gf, ga = (hg, ag) if side == "home" else (ag, hg)
            line.played += 1
            line.goals_for += gf
            line.goals_against += ga
            line.clean_sheets += ga == 0
            line.points += 3 if gf > ga else (1 if gf == ga else 0)
            hf, ha = ht.get(side), ht.get(other)
            if hf is not None and ha is not None and hf > ha:
                line.ht_leads += 1
                line.ht_leads_not_won += gf <= ga
            rival_id = t[other]["id"]
            ts, sot = shots.get(rival_id, (None, None))
            if ts is not None and sot is not None:
                line.stats_games += 1
                line.shots_against += int(ts)
                line.sot_against += int(sot)
    return out


def build_report(client, season: int) -> NacionalReport | None:
    """Informe con los datos del semestre. ``None`` si Nacional no ha jugado este semestre."""
    start = client.http.calls_made
    fixtures = client.fixtures_by_league_season(LEAGUE_ID, season)
    rnd = last_round(fixtures)
    if rnd is None:
        return None
    group = [NACIONAL_ID, *rivals(fixtures, rnd)]
    ids = {
        str(it["fixture"]["id"])
        for it in semester(fixtures)
        if it["fixture"]["status"]["short"] in FINISHED
        and ({it["teams"]["home"]["id"], it["teams"]["away"]["id"]} & set(group))
    }
    items: list[dict] = []
    id_list = sorted(ids)
    for i in range(0, len(id_list), 20):
        items += client.http.get("/fixtures", {"ids": "-".join(id_list[i : i + 20])}).get(
            "response", []
        )
    lines = build_lines(items, set(group))
    teams = [lines[t] for t in group if t in lines]
    nac = sorted(
        (_match(it) for it in _nacional_matches(semester(fixtures))), key=lambda m: m.kickoff_utc
    )
    last = [m for m in nac if m.finished][-1]
    upcoming = [m for m in nac if not m.finished]
    report = NacionalReport(
        phase=phase_of(rnd),
        round=rnd,
        round_label=round_label(rnd),
        teams=teams,
        last=last,
        next=upcoming[0] if upcoming else None,
        calls=client.http.calls_made - start,
    )
    missing = [t.name for t in teams if t.stats_games < t.played]
    if missing:
        report.notes.append("Partidos sin tiros registrados: " + ", ".join(missing))
    return report


def ranking(teams: list[TeamLine], attr: str, low_is_good: bool = True) -> list[TeamLine]:
    return sorted(teams, key=lambda t: t.per_game(attr), reverse=not low_is_good)
