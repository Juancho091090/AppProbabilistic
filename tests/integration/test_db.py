"""Integración con PostgreSQL real. Requiere TEST_DATABASE_URL; si no, se omite."""

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sports_analytics.data.schemas import FootballMatch, MatchStatus, TennisMatch
from sports_analytics.db import repository as repo
from sports_analytics.db.models import Base, FootballMatchRow, PredictionRow, Team
from sports_analytics.models.outputs import MatchForecast, PredictionRecord

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL no definida"),
]
T0 = datetime(2026, 9, 1, 18, tzinfo=UTC)


@pytest.fixture
def session(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        s = Session(bind=conn)
        yield s
        s.close()
        trans.rollback()


def test_migrations_match_models(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"El modelo y las migraciones difieren: {diff}"


def _fm(i, home="1", away="2", status=MatchStatus.FINISHED, hg=1, ag=0):
    return FootballMatch(
        match_id=f"fx{i}",
        competition_key="eng_premier_league",
        season=2026,
        home_team=home,
        away_team=away,
        home_name=f"Team {home}",
        away_name=f"Team {away}",
        kickoff_utc=T0 + timedelta(days=i),
        status=status,
        home_goals=hg if status == MatchStatus.FINISHED else None,
        away_goals=ag if status == MatchStatus.FINISHED else None,
    )


def test_football_upsert_is_idempotent(session, app_config):
    comps = repo.ensure_competitions(session, app_config)
    repo.upsert_football_matches(session, [_fm(1), _fm(2, status=MatchStatus.SCHEDULED)], comps)
    repo.upsert_football_matches(
        session, [_fm(1), _fm(2, hg=3, ag=3)], comps
    )  # repetido + actualizado
    assert session.scalar(select(func.count()).select_from(FootballMatchRow)) == 2
    assert session.scalar(select(func.count()).select_from(Team)) == 2
    row = session.scalar(select(FootballMatchRow).where(FootballMatchRow.external_id == "fx2"))
    assert (row.status, row.home_goals) == ("finished", 3)


def test_football_stats_roundtrip(session, app_config):
    comps = repo.ensure_competitions(session, app_config)
    repo.upsert_football_matches(session, [_fm(1), _fm(2)], comps)
    assert set(repo.football_matches_missing_stats(session, ["eng_premier_league"], 10)) == {
        "fx1",
        "fx2",
    }
    repo.upsert_football_statistics(
        session,
        [
            {
                "external_id": "fx1",
                "home_corners": 7,
                "away_corners": 3,
                "home_shots": 12,
                "away_shots": 8,
                "home_possession": 55.0,
                "away_possession": 45.0,
                "available": True,
            }
        ],
    )
    assert repo.football_matches_missing_stats(session, ["eng_premier_league"], 10) == ["fx2"]
    hist = {m.match_id: m for m in repo.load_football_history(session, T0)}
    assert hist["fx1"].home_corners == 7 and hist["fx1"].display_home == "Team 1"
    assert hist["fx2"].home_corners is None
    assert ("eng_premier_league", 2026) in repo.football_seasons_loaded(session)


def test_tennis_roundtrip(session):
    m = TennisMatch(
        match_id="atp-1",
        tour="ATP",
        tournament="China Open",
        category="500",
        surface="hard",
        player_a="100",
        player_b="200",
        player_a_name="A",
        player_b_name="B",
        kickoff_utc=T0,
        status=MatchStatus.FINISHED,
        winner="B",
        sets_a=1,
        sets_b=2,
        games_a=15,
        games_b=17,
    )
    repo.upsert_tennis_matches(
        session, [m], {"atp-1": {"tournament_external_id": "9", "rank_id": 2}}
    )
    repo.upsert_tennis_matches(session, [m])
    [loaded] = repo.load_tennis_history(session, T0 - timedelta(days=1))
    assert loaded.winner == "B" and loaded.display_a == "A" and loaded.games_b == 17
    assert repo.tennis_loaded_days(session, "atp") == {T0.date()}


def _forecast(prob):
    now = datetime(2026, 10, 6, 12, tzinfo=UTC)
    rec = PredictionRecord(
        sport="football",
        competition="PL",
        event_id="fx9",
        market="1x2",
        event="home_win",
        probability=prob,
        model="ensemble_v1",
        generated_at=now,
        as_of=now,
        confidence="medium",
    )
    line = PredictionRecord(
        sport="football",
        competition="PL",
        event_id="fx9",
        market="goals_total",
        event="over_2.5",
        line=2.5,
        probability=0.51,
        model="dixon_coles",
        generated_at=now,
        as_of=now,
        confidence="medium",
    )
    return MatchForecast(
        sport="football",
        competition="PL",
        competition_key="eng_premier_league",
        event_id="fx9",
        kickoff_utc=now + timedelta(hours=8),
        home_or_a="A",
        away_or_b="B",
        model_version="ensemble_v1",
        generated_at=now,
        as_of=now,
        markets={},
        per_model={},
        confidence="medium",
        confidence_score=0.5,
        records=[rec, line],
    )


def test_predictions_upsert_and_settlement(session):
    run = repo.start_run(session, "daily", date(2026, 10, 6))
    repo.save_forecasts(session, [_forecast(0.60)], run.id, date(2026, 10, 6))
    repo.save_forecasts(session, [_forecast(0.62)], run.id, date(2026, 10, 6))  # re-ejecución
    rows = session.scalars(select(PredictionRow)).all()
    assert len(rows) == 2
    assert {r.line_key for r in rows} == {"", "2.5"}
    home = next(r for r in rows if r.event == "home_win")
    assert home.probability == pytest.approx(0.62)

    pending = repo.unsettled_predictions(session, datetime(2026, 10, 8, tzinfo=UTC))
    assert len(pending) == 2
    repo.save_results(
        session,
        [
            {
                "prediction_id": home.id,
                "outcome": 1,
                "actual_value": None,
                "void_reason": None,
                "settled_at": datetime.now(UTC),
            }
        ],
    )
    assert len(repo.unsettled_predictions(session, datetime(2026, 10, 8, tzinfo=UTC))) == 1
    settled = repo.settled_predictions(session, "football")
    assert len(settled) == 1 and settled[0][1].outcome == 1
    repo.finish_run(session, run, "success")
    assert run.duration_seconds is not None


def test_data_source_tracking(session):
    repo.record_data_source(session, "api_football", ok=True, calls=12, quota="7488")
    repo.record_data_source(session, "api_football", ok=False, calls=1, error="HTTP 500")
    row = session.execute(
        text("select calls_last_run, last_error, last_success_at from data_sources")
    ).one()
    assert row[0] == 1 and row[1] == "HTTP 500" and row[2] is not None


def test_recent_matches_missing_stats_for_teams(session, app_config):
    comps = repo.ensure_competitions(session, app_config)
    matches = [_fm(i, home="1" if i % 2 else "3", away="2") for i in range(1, 9)]
    repo.upsert_football_matches(session, matches, comps)
    repo.upsert_football_statistics(
        session, [{"external_id": "fx8", "home_corners": 5, "away_corners": 4, "available": True}]
    )
    # Equipo "1" juega fx1, fx3, fx5, fx7; últimos 2 -> fx7, fx5
    assert repo.recent_matches_missing_stats_for_teams(
        session, {"1"}, ["eng_premier_league"], 2
    ) == ["fx7", "fx5"]
    # Equipo "2" juega todos; fx8 ya tiene estadísticas y se omite
    got = repo.recent_matches_missing_stats_for_teams(session, {"2"}, ["eng_premier_league"], 3)
    assert got == ["fx7", "fx6"]
    assert (
        repo.recent_matches_missing_stats_for_teams(session, {"999"}, ["eng_premier_league"], 5)
        == []
    )
