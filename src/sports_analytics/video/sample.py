"""Datos de ejemplo para probar el Short sin base de datos (``cli short --sample``)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.payload import CompetitionSection, DailyReportPayload


def _fc(event_id, comp, home, away, x, conf, elo, lam, hour) -> MatchForecast:
    return MatchForecast(
        sport="football",
        competition=comp,
        competition_key=comp.lower().replace(" ", "_"),
        event_id=event_id,
        kickoff_utc=datetime(2026, 11, 22, hour, tzinfo=UTC),
        home_or_a=home,
        away_or_b=away,
        model_version="ejemplo",
        generated_at=datetime(2026, 11, 21, 23, tzinfo=UTC),
        as_of=datetime(2026, 11, 21, 23, tzinfo=UTC),
        markets={
            "1x2": dict(zip(("home", "draw", "away"), x, strict=True)),
            "goals": {
                "expected_goals": {"total": sum(lam)},
                "goal_lines": {"2.5": {"over": 0.48, "under": 0.52}},
            },
        },
        per_model={},
        confidence=conf,
        confidence_score=max(x),
        context={
            "elo": {"home": elo[0], "away": elo[1]},
            "lambda": dict(zip(("home", "away"), lam, strict=True)),
        },
    )


def sample_recap():
    from sports_analytics.video.recap import Recap, RecapItem

    return Recap(
        date(2026, 11, 21),
        "canal",
        [
            RecapItem("Liverpool 2-0 Burnley", "Liverpool", True),
            RecapItem("Bayern 3-1 Mainz", "Bayern", True),
            RecapItem("Inter 1-1 Lecce", "Inter", False),
            RecapItem("PSG 2-0 Nantes", "PSG", True),
            RecapItem("Sinner 2-0 Rune", "Sinner", True, "tennis"),
            RecapItem("Porto 3-0 Arouca", "Porto", True),
        ],
    )


def sample_payload() -> tuple[DailyReportPayload, dict[str, tuple[float, float, float]], int]:
    """Payload, 1X2 del mercado sin margen y número de partidos de confianza Alta."""
    open_match = _fc(
        "ej1",
        "LaLiga",
        "Atlético de Madrid",
        "Barcelona",
        (0.36, 0.29, 0.35),
        "low",
        (1832, 1851),
        (1.31, 1.28),
        20,
    )
    clear = _fc(
        "ej2",
        "Premier League",
        "Liverpool",
        "Burnley",
        (0.71, 0.18, 0.11),
        "high",
        (1890, 1610),
        (2.3, 0.7),
        15,
    )
    payload = DailyReportPayload(
        report_date=date(2026, 11, 22),
        generated_at=datetime(2026, 11, 21, 23, 50, tzinfo=UTC),
        timezone="America/Bogota",
        football=[
            CompetitionSection(name="LaLiga", key="laliga", forecasts=[open_match]),
            CompetitionSection(name="Premier League", key="epl", forecasts=[clear]),
        ],
    )
    return payload, {"ej1": (0.33, 0.27, 0.40)}, 4
