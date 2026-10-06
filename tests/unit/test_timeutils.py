from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from sports_analytics.core.timeutils import (
    ensure_utc,
    is_on_local_day,
    local_day_bounds_utc,
    local_today,
    season_for,
)

BOG = ZoneInfo("America/Bogota")


def test_local_today_differs_from_utc_at_night():
    # 02:00 UTC del 7-oct = 21:00 del 6-oct en Bogotá
    now = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
    assert local_today(BOG, now) == date(2026, 10, 6)


def test_day_bounds_are_utc_minus_5():
    start, end = local_day_bounds_utc(date(2026, 10, 6), BOG)
    assert start == datetime(2026, 10, 6, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 7, 5, 0, tzinfo=UTC)


def test_late_night_kickoff_belongs_to_local_day():
    # Partido 20:30 Bogotá = 01:30 UTC del día siguiente
    kickoff = datetime(2026, 10, 7, 1, 30, tzinfo=UTC)
    assert is_on_local_day(kickoff, date(2026, 10, 6), BOG)
    assert not is_on_local_day(kickoff, date(2026, 10, 7), BOG)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 10, 6, 12, 0))


def test_cron_07_bogota_is_12_utc():
    run = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert run.astimezone(BOG).hour == 7


@pytest.mark.parametrize(
    "day,mode,expected",
    [
        (date(2026, 10, 6), "split", 2026),
        (date(2027, 3, 1), "split", 2026),
        (date(2027, 3, 1), "calendar", 2027),
    ],
)
def test_season(day, mode, expected):
    assert season_for(day, mode) == expected
