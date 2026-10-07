"""Modelo relacional (SQLAlchemy 2.0). Ver docs/architecture.md §5.

Convenciones:
* Todas las fechas son ``timestamptz`` en UTC.
* Cada entidad externa se identifica por (provider, external_id) con restricción única,
  así la ingesta es idempotente (upsert).
* Las predicciones son filas estructuradas (una por evento/probabilidad/modelo), nunca
  solo texto.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Competition(TimestampMixin, Base):
    __tablename__ = "competitions"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    sport: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str | None] = mapped_column(String(40))


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))


class Player(TimestampMixin, Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    tour: Mapped[str | None] = mapped_column(String(10))


class FootballMatchRow(TimestampMixin, Base):
    __tablename__ = "football_matches"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        Index("ix_football_matches_kickoff", "kickoff_utc"),
        Index("ix_football_matches_competition", "competition_id", "season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(40))
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    season: Mapped[int | None] = mapped_column(Integer)
    round: Mapped[str | None] = mapped_column(String(120))
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(String(20))
    neutral_venue: Mapped[bool] = mapped_column(Boolean, default=False)
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)

    competition: Mapped[Competition] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    statistics: Mapped[FootballStatistics | None] = relationship(
        back_populates="match", uselist=False
    )


class FootballStatistics(TimestampMixin, Base):
    __tablename__ = "football_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("football_matches.id"), unique=True)
    home_corners: Mapped[int | None] = mapped_column(Integer)
    away_corners: Mapped[int | None] = mapped_column(Integer)
    home_shots: Mapped[int | None] = mapped_column(Integer)
    away_shots: Mapped[int | None] = mapped_column(Integer)
    home_possession: Mapped[float | None] = mapped_column(Float)
    away_possession: Mapped[float | None] = mapped_column(Float)
    home_shots_on_target: Mapped[int | None] = mapped_column(Integer)
    away_shots_on_target: Mapped[int | None] = mapped_column(Integer)
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)
    available: Mapped[bool] = mapped_column(Boolean, default=True)  # False = API sin datos

    match: Mapped[FootballMatchRow] = relationship(back_populates="statistics")


class MarketOdds(TimestampMixin, Base):
    """Precios 1X2 de mercado por casa (solo para evaluar el modelo, nunca para el informe)."""

    __tablename__ = "market_odds"
    __table_args__ = (UniqueConstraint("match_id", "bookmaker_id", "market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("football_matches.id"), index=True)
    bookmaker_id: Mapped[int] = mapped_column(Integer)
    bookmaker_name: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(20), default="1x2")
    odd_home: Mapped[float] = mapped_column(Float)
    odd_draw: Mapped[float] = mapped_column(Float)
    odd_away: Mapped[float] = mapped_column(Float)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketOddsCheck(Base):
    """Partidos ya consultados en /odds (con o sin precios) para no repetir llamadas."""

    __tablename__ = "market_odds_checks"

    match_id: Mapped[int] = mapped_column(ForeignKey("football_matches.id"), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bookmakers: Mapped[int] = mapped_column(Integer, default=0)
    final: Mapped[bool] = mapped_column(Boolean, default=False)  # consultado tras el inicio


class TennisMatchRow(TimestampMixin, Base):
    __tablename__ = "tennis_matches"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        Index("ix_tennis_matches_kickoff", "kickoff_utc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(60))
    tour: Mapped[str] = mapped_column(String(10))
    tournament_external_id: Mapped[str | None] = mapped_column(String(40))
    tournament: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(80))
    rank_id: Mapped[int | None] = mapped_column(Integer)
    surface: Mapped[str] = mapped_column(String(20))
    best_of: Mapped[int] = mapped_column(Integer, default=3)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    player_a_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player_b_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(20))
    winner: Mapped[str | None] = mapped_column(String(1))
    sets_a: Mapped[int | None] = mapped_column(Integer)
    sets_b: Mapped[int | None] = mapped_column(Integer)
    games_a: Mapped[int | None] = mapped_column(Integer)
    games_b: Mapped[int | None] = mapped_column(Integer)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    rank_a: Mapped[int | None] = mapped_column(Integer)  # ranking en el momento del partido
    rank_b: Mapped[int | None] = mapped_column(Integer)

    player_a: Mapped[Player] = relationship(foreign_keys=[player_a_id])
    player_b: Mapped[Player] = relationship(foreign_keys=[player_b_id])


class TennisStatistics(TimestampMixin, Base):
    __tablename__ = "tennis_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("tennis_matches.id"), unique=True)
    a_spw: Mapped[float | None] = mapped_column(Float)
    b_spw: Mapped[float | None] = mapped_column(Float)
    a_rpw: Mapped[float | None] = mapped_column(Float)
    b_rpw: Mapped[float | None] = mapped_column(Float)


class Rating(Base):
    """Instantánea de ratings (Elo) válidos desde ``valid_from`` (auditoría y análisis)."""

    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("sport", "entity_key", "rating_type", "valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[str] = mapped_column(String(20))
    entity_key: Mapped[str] = mapped_column(String(80))  # id externo del equipo/jugador
    entity_name: Mapped[str | None] = mapped_column(String(200))
    rating_type: Mapped[str] = mapped_column(String(30))  # elo, elo_clay, ...
    value: Mapped[float] = mapped_column(Float)
    matches: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_type: Mapped[str] = mapped_column(String(30))  # daily | results | backfill | backtest
    run_date: Mapped[date] = mapped_column(Date)  # día local (America/Bogota)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # success|partial|failed
    matches_found: Mapped[int] = mapped_column(Integer, default=0)
    matches_filtered: Mapped[int] = mapped_column(Integer, default=0)
    predictions_generated: Mapped[int] = mapped_column(Integer, default=0)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report_markdown: Mapped[str | None] = mapped_column(Text)


class PredictionRow(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "sport",
            "event_id",
            "market",
            "event",
            "line_key",
            "model",
            "prediction_date",
            name="uq_prediction_identity",
        ),
        Index("ix_predictions_event", "sport", "event_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    sport: Mapped[str] = mapped_column(String(20))
    competition: Mapped[str] = mapped_column(String(200))
    competition_key: Mapped[str | None] = mapped_column(String(80))
    event_id: Mapped[str] = mapped_column(String(60))
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market: Mapped[str] = mapped_column(String(40))
    event: Mapped[str] = mapped_column(String(40))
    line: Mapped[float | None] = mapped_column(Float)
    line_key: Mapped[str] = mapped_column(String(12), default="")  # "" si no hay línea
    probability: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[str] = mapped_column(String(10))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prediction_date: Mapped[date] = mapped_column(Date)

    result: Mapped[PredictionResult | None] = relationship(
        back_populates="prediction", uselist=False
    )


class PredictionResult(Base):
    __tablename__ = "prediction_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), unique=True)
    outcome: Mapped[int | None] = mapped_column(Integer)  # 1 ocurrió, 0 no, None anulado
    actual_value: Mapped[float | None] = mapped_column(Float)  # p. ej. goles totales
    void_reason: Mapped[str | None] = mapped_column(String(60))
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    prediction: Mapped[PredictionRow] = relationship(back_populates="result")


class ModelMetric(Base):
    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(40))
    segment_type: Mapped[str] = mapped_column(String(40))  # global|competition|prob_bucket|...
    segment_value: Mapped[str] = mapped_column(String(120))
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    n: Mapped[int] = mapped_column(Integer)
    brier: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    ece: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="live")  # live | backtest
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), unique=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    calls_last_run: Mapped[int] = mapped_column(Integer, default=0)
    quota_remaining: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
