"""Short semanal de Atlético Nacional: jornada, grupo, métricas y guion."""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sports_analytics.content import nacional as N
from sports_analytics.video import html_engine as E
from sports_analytics.video.nacional_script import build_nacional_script
from sports_analytics.video.sample import sample_nacional

TZ = ZoneInfo("America/Bogota")
NAMES = {
    1137: "Atletico Nacional",
    1128: "Independiente Medellin",
    1136: "Once Caldas",
    1139: "Santa Fe",
    1138: "America de Cali",
    1125: "Millonarios",
}


def fx(fid, rnd, when, home, away, hg=None, ag=None, ht=(None, None), shots=None, status=None):
    done = hg is not None
    item = {
        "fixture": {
            "id": fid,
            "date": when.isoformat(),
            "status": {"short": status or ("FT" if done else "NS")},
        },
        "league": {"round": rnd},
        "teams": {
            "home": {"id": home, "name": NAMES[home]},
            "away": {"id": away, "name": NAMES[away]},
        },
        "goals": {"home": hg, "away": ag},
        "score": {"halftime": {"home": ht[0], "away": ht[1]}, "fulltime": {"home": hg, "away": ag}},
    }
    if shots:
        item["statistics"] = [
            {
                "team": {"id": home},
                "statistics": [
                    {"type": "Total Shots", "value": shots[0][0]},
                    {"type": "Shots on Goal", "value": shots[0][1]},
                ],
            },
            {
                "team": {"id": away},
                "statistics": [
                    {"type": "Total Shots", "value": shots[1][0]},
                    {"type": "Shots on Goal", "value": shots[1][1]},
                ],
            },
        ]
    return item


D = datetime(2026, 11, 20, 23, 0, tzinfo=UTC)
Q1, Q2 = "Clausura - Quadrangular - 1", "Clausura - Quadrangular - 2"


def season():
    return [
        fx(1, "Apertura - 1", D - timedelta(days=200), 1137, 1128, 5, 0),  # primer semestre: fuera
        fx(2, "Clausura - 18", D - timedelta(days=10), 1137, 1138, 2, 1, (1, 0), ((14, 6), (8, 2))),
        fx(3, "Clausura - 19", D - timedelta(days=5), 1139, 1137, 1, 1, (0, 1), ((10, 4), (9, 3))),
        fx(10, Q1, D, 1128, 1139, 1, 0),
        fx(11, Q1, D + timedelta(hours=2), 1138, 1125, 2, 2),
        fx(12, Q1, D + timedelta(days=1, hours=2), 1136, 1137, 0, 0, (0, 0), ((12, 1), (7, 1))),
        fx(20, Q2, D + timedelta(days=5), 1137, 1128),
        fx(21, Q2, D + timedelta(days=5), 1139, 1136),
        fx(22, Q2, D + timedelta(days=5), 1125, 1138),
    ]


def test_video_day_is_after_last_match_of_the_round():
    f = season()
    assert N.last_round(f) == Q1
    # último partido de la fecha 1: 22-nov 01:00 UTC = 21-nov 8 p. m. en Bogotá
    assert N.video_day(f, TZ) == date(2026, 11, 22)
    # si la fecha no terminó, no toca
    f[5]["fixture"]["status"]["short"] = "NS"
    f[5]["goals"] = {"home": None, "away": None}
    assert N.last_round(f) == "Clausura - 19"


def test_rivals_from_quadrangular_crosses():
    assert N.rivals(season(), Q1) == [1128, 1136, 1139]
    assert N.round_label(Q1) == "Cuadrangular, fecha 1"
    assert N.round_label("Clausura - 12") == "Fecha 12"
    assert N.phase_of("Clausura - Final") == "final"


def test_lines_count_only_the_semester_and_halftime_leads():
    lines = N.build_lines(N.semester(season()), {1137})
    nac = lines[1137]
    assert (nac.played, nac.goals_for, nac.goals_against, nac.clean_sheets) == (3, 3, 2, 1)
    # tiros del rival: 8 (América), 10 (Santa Fe), 12 (Once Caldas)
    assert (nac.shots_against, nac.sot_against, nac.stats_games) == (30, 7, 3)
    # ganaba al descanso en 2 (vs América y Santa Fe) y solo ganó 1
    assert (nac.ht_leads, nac.ht_leads_not_won) == (2, 1)
    assert nac.per_game("goals_against") == pytest.approx(2 / 3)


class FakeHttp:
    def __init__(self, items):
        self.calls_made = 0
        self.items = {str(it["fixture"]["id"]): it for it in items}

    def get(self, path, params):
        self.calls_made += 1
        return {"response": [self.items[i] for i in params["ids"].split("-")]}


class FakeClient:
    def __init__(self, items):
        self.http = FakeHttp(items)
        self.items = items

    def fixtures_by_league_season(self, league, season):
        self.http.calls_made += 1
        return self.items


def test_build_report_with_group_and_next_match():
    client = FakeClient(season())
    r = N.build_report(client, 2026)
    assert r.phase == "cuadrangular" and r.round_label == "Cuadrangular, fecha 1"
    assert [t.name for t in r.teams][0] == "Nacional" and len(r.teams) == 4
    assert r.last.home == "Once Caldas" and r.next.away == "Medellín"
    assert r.calls == 2  # temporada + 1 lote de estadísticas


def test_script_scenes_and_page_data():
    s = build_nacional_script(sample_nacional())
    assert [x.kind for x in s.scenes] == ["hook", "bars", "bars", "why", "cta"]
    hook, goals, shots, why, cta = s.scenes
    assert hook.narration.startswith(
        "Nacional empató 0 a 0 con Once Caldas en la fecha 1 del cuadrangular"
    )
    assert (
        "el mejor es Medellín" in goals.narration and "Nacional queda el segundo" in goals.narration
    )
    assert "se le critica" in why.narration and "¿Qué dicen los números?" in why.narration
    assert "Nacional contra Medellín, miércoles 25" in cta.narration
    data = {i: E.scene_data(x.kind, x.data, TZ) for i, x in enumerate(s.scenes)}
    assert data[1]["fmt"] == "dec2" and data[1]["me"] == "Nacional" and "stamp" not in data[1]
    assert (
        data[1]["tags"]["Medellín"]["kind"] == "best"
        and data[1]["tags"]["Once Caldas"]["kind"] == "worst"
    )
    assert data[4]["n_high"] == 2 and "Nacional vs Medellín" in data[4]["num_label"]
    assert "solo partidos del semestre" in data[4]["disclaimer"]
    assert "SÍ o NO" in s.pinned_comment and "#AtléticoNacional" in s.description
    json.dumps(data)


def test_criticism_is_configurable():
    s = build_nacional_script(sample_nacional(), "Dicen que no gana los partidos grandes.")
    assert s.scenes[3].narration.startswith("Dicen que no gana los partidos grandes.")


def test_worst_defense_title():
    r = sample_nacional()
    r.nacional.goals_against = 40
    assert "el que más goles recibe" in build_nacional_script(r).title


def test_pipeline_skips_when_not_due(monkeypatch):
    from types import SimpleNamespace

    from sports_analytics.config.settings import Settings
    from sports_analytics.pipeline import nacional_short as P

    svc = SimpleNamespace(football=FakeClient(season()), telegram=None)
    out = P.run_nacional_short(
        Settings(), services=svc, use_db=False, now=datetime(2026, 11, 23, 15, tzinfo=UTC)
    )
    assert out.status == "skipped" and "2026-11-22" in out.issues[0]


def _has_chrome():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return E.chrome_path() is not None


@pytest.mark.skipif(not _has_chrome(), reason="sin Chrome/Playwright")
def test_pipeline_renders_on_due_day(tmp_path):
    from types import SimpleNamespace

    from sports_analytics.config.settings import Settings
    from sports_analytics.pipeline import nacional_short as P

    sent = []
    tg = SimpleNamespace(
        send_video=lambda p, c: sent.append((p, c)), send_message=lambda *a, **k: None
    )
    svc = SimpleNamespace(football=FakeClient(season()), telegram=tg)
    out = P.run_nacional_short(
        Settings(google_tts_api_key=None),
        services=svc,
        use_db=False,
        now=datetime(2026, 11, 22, 14, tzinfo=UTC),
        out_dir=tmp_path,
    )
    assert out.status == "sent", out.issues
    assert out.video.exists() and sent and "Short de Nacional" in sent[0][1]


def test_two_decimals_avoid_false_ties_and_singular():
    r = sample_nacional()
    for t, ga in zip(r.teams, (18, 25, 19, 19), strict=True):
        t.goals_against = ga
    r.nacional.ht_leads, r.nacional.ht_leads_not_won = 9, 1
    s = build_nacional_script(r)
    assert "Nacional es el mejor: 0,90 por partido" in s.scenes[1].narration
    assert "solo se le escapó 1." in s.scenes[3].narration
    assert "Once Caldas: 0,95 goles" in s.description


# ------------------------------------------------ formato "fecha": partido a partido

from sports_analytics.video.nacional_script import build_nacional_round_script  # noqa: E402
from sports_analytics.video.sample import sample_round  # noqa: E402


def _stats(home, away, poss, shots, sot, inside):
    return [
        {
            "team": {"id": home},
            "statistics": [
                {"type": "Ball Possession", "value": f"{poss[0]}%"},
                {"type": "Total Shots", "value": shots[0]},
                {"type": "Shots on Goal", "value": sot[0]},
                {"type": "Shots insidebox", "value": inside[0]},
            ],
        },
        {
            "team": {"id": away},
            "statistics": [
                {"type": "Ball Possession", "value": f"{poss[1]}%"},
                {"type": "Total Shots", "value": shots[1]},
                {"type": "Shots on Goal", "value": sot[1]},
                {"type": "Shots insidebox", "value": inside[1]},
            ],
        },
    ]


def test_round_report_orders_other_group_first_and_nacional_last():
    f = season()
    for it, st in zip(
        f[3:6],
        (
            _stats(1128, 1139, (51, 49), (9, 8), (3, 2), (6, 5)),
            _stats(1138, 1125, (60, 40), (15, 7), (6, 2), (10, 4)),
            _stats(1136, 1137, (49, 51), (12, 7), (1, 1), (4, 6)),
        ),
        strict=True,
    ):
        it["statistics"] = st
    rr = N.build_round_report(FakeClient(f), 2026)
    names = [(m.home.name, m.away.name) for m in rr.matches]
    assert names == [
        ("América", "Millonarios"),
        ("Medellín", "Santa Fe"),
        ("Once Caldas", "Nacional"),
    ]
    assert [m.nacional_group for m in rr.matches] == [False, True, True]
    oc = rr.matches[-1]
    assert (oc.home.possession, oc.home.shots, oc.home.sot, oc.home.inside) == (49, 12, 1, 4)
    assert oc.conceded(oc.away) is oc.home and rr.notes == []
    assert rr.calls == 2


def test_round_script_scenes_and_defense_ranking():
    s = build_nacional_round_script(sample_round())
    kinds = [x.kind for x in s.scenes]
    assert kinds == ["hook", "why", "why", "why", "why", "bars", "cta"]
    assert s.scenes[1].narration.startswith("En el otro grupo, Millonarios 1 a 1 Bucaramanga.")
    assert "Y en el clásico, Nacional 2 a 1 Medellín." in s.scenes[4].narration
    assert "América ganó con menos llegadas" in s.scenes[2].narration
    assert "La pelota quedó repartida y Santa Fe remató más" in s.scenes[3].narration
    d = s.scenes[5]
    # Nacional y Once Caldas recibieron 2 al arco; desempata lo recibido dentro del área
    assert [r[0] for r in d.data["rows"]][:2] == ["Nacional", "Once Caldas"]
    assert (
        d.data["tags"]["Nacional"]["kind"] == "best"
        and d.data["tags"]["Medellín"]["kind"] == "worst"
    )
    assert "empatada con Once Caldas" in d.narration
    page = E.scene_data("bars", d.data, TZ)
    assert page["fmt"] == "int" and len(page["rows"]) == 8
    assert len(s.scenes[1].data["duels"]) == 4
    assert s.title.startswith("Nacional 2-1 Medellín y toda la fecha 2 del cuadrangular")
    assert "Millonarios: posesión 55%, 14 tiros, 5 al arco, 9 dentro del área" in s.description
    json.dumps({i: E.scene_data(x.kind, x.data, TZ) for i, x in enumerate(s.scenes)})


def test_round_script_skips_matches_without_stats():
    rr = sample_round()
    rr.matches[0].home.possession = None
    s = build_nacional_round_script(rr)
    assert [x.kind for x in s.scenes].count("why") == 3
    assert s.notes == ["Millonarios 1-1 Bucaramanga"]
    assert "sin estadísticas completas" in s.description
    # el primer partido del otro grupo que sí sale lleva la entrada "En el otro grupo"
    assert s.scenes[1].narration.startswith("En el otro grupo, América")


def test_red_card_context_is_narrated():
    rr = sample_round()
    rr.matches[-1].away.reds = 1  # Medellín con diez
    s = build_nacional_round_script(rr)
    assert "Ojo: Medellín jugó con uno menos tras una expulsión." in s.scenes[4].narration
    assert (
        "Medellín: posesión 42%, 7 tiros, 2 al arco, 4 dentro del área, 1 expulsado"
        in s.description
    )
