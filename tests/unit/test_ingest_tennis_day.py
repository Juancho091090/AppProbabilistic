from datetime import date
from zoneinfo import ZoneInfo

from sports_analytics.data.clients.tennis_api import TournamentInfo
from sports_analytics.pipeline.ingest import IngestReport, tennis_fixtures_today

BOG = ZoneInfo("America/Bogota")


def _fx(mid, iso):
    return {
        "id": mid,
        "matchId": mid,
        "date": iso,
        "tournamentId": 1,
        "player1": {"id": 10 + mid, "name": f"A{mid}"},
        "player2": {"id": 20 + mid, "name": f"B{mid}"},
    }


class FakeClient:
    def __init__(self):
        self.requested = []
        self.by_date = {
            "2026-10-06": [
                _fx(1, "2026-10-06T03:00:00.000Z"),  # 22:00 del 5-oct en Bogotá
                _fx(2, "2026-10-06T21:00:00.000Z"),  # 16:00 del 6-oct
            ],
            "2026-10-07": [
                _fx(3, "2026-10-07T03:00:00.000Z"),  # 22:00 del 6-oct ← día local
                _fx(4, "2026-10-07T12:00:00.000Z"),  # 07:00 del 7-oct
            ],
        }

    def fixtures(self, tour, d):
        self.requested.append((tour, d.isoformat()))
        return self.by_date.get(d.isoformat(), []) if tour == "atp" else []

    def tournaments(self, tour, year):
        return {1: TournamentInfo(1, "Shanghai Masters", 3, 1, "hard", False, "1000", None)}

    def rankings(self, tour):
        return {}


def test_local_day_spans_two_utc_dates(app_config):
    client = FakeClient()
    out = tennis_fixtures_today(client, app_config, date(2026, 10, 6), IngestReport(), tz=BOG)
    assert {m.match_id for m in out} == {"atp-2", "atp-3"}
    assert ("atp", "2026-10-06") in client.requested and ("atp", "2026-10-07") in client.requested


def test_utc_day_requests_single_date(app_config):
    client = FakeClient()
    out = tennis_fixtures_today(
        client, app_config, date(2026, 10, 6), IngestReport(), tz=ZoneInfo("UTC")
    )
    assert {m.match_id for m in out} == {"atp-1", "atp-2"}
    assert client.requested.count(("atp", "2026-10-07")) == 0
