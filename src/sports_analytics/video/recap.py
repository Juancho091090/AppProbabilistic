"""Resumen de ayer para el Short: cómo les fue a los partidos de confianza Alta.

Transparencia: se cuentan TODOS los partidos de ese día ya resueltos, aciertos y fallos.

* Si el canal de Telegram publicó partidos para ese día (``channel_event_ids`` en
  pipeline_runs.details), se evalúan exactamente esos, cada uno con su regla:
  ``modelo_alta`` → favorito del modelo; ``mercado_favorito_60`` → favorito del mercado
  (registro simulado).
* Si no (días anteriores al canal), se usan los partidos de confianza Alta del modelo.

Los partidos sin resultado todavía (o anulados) no cuentan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from sports_analytics.db.models import (
    FootballMatchRow,
    PaperSelection,
    PipelineRun,
    Player,
    PredictionResult,
    PredictionRow,
    Team,
    TennisMatchRow,
)

RULE_MODEL = "modelo_alta"
RULE_MARKET = "mercado_favorito_60"
MARKETS = ("1x2", "winner")


@dataclass
class RecapItem:
    label: str  # "Liverpool 2-1 Burnley" / "Sinner vs Alcaraz"
    pick: str  # "Liverpool", "Empate", "Sinner"
    hit: bool | None  # None = todavía sin resultado (solo con include_pending)
    sport: str = "football"
    league: str = ""
    market: bool = False  # elegido por la regla del favorito del mercado


@dataclass
class Recap:
    day: date
    source: str  # canal | modelo
    items: list[RecapItem] = field(default_factory=list)

    @property
    def n(self) -> int:
        """Partidos ya resueltos (los pendientes no cuentan)."""
        return sum(i.hit is not None for i in self.items)

    @property
    def hits(self) -> int:
        return sum(i.hit is True for i in self.items)

    @property
    def pending(self) -> int:
        return sum(i.hit is None for i in self.items)

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0


def _published(session: Session, day: date) -> list[tuple[str, str]] | None:
    runs = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.run_type == "daily", PipelineRun.run_date == day)
        .order_by(PipelineRun.id.desc())
    ).all()
    for run in runs:
        ids = (run.details or {}).get("channel_event_ids")
        if ids is not None:
            return [(x["event_id"], x.get("rule", RULE_MODEL)) for x in ids]
    return None


def _final_rows(session: Session, model: str, day: date, event_ids=None):
    """Predicción final (1X2 / ganador) del día, con su resultado."""
    q = (
        select(PredictionRow, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == PredictionRow.id, isouter=True)
        .where(
            and_(
                PredictionRow.prediction_date == day,
                PredictionRow.model == model,
                PredictionRow.market.in_(MARKETS),
            )
        )
    )
    if event_ids is not None:
        q = q.where(PredictionRow.event_id.in_(list(event_ids)))
    by_event: dict[str, list] = {}
    for p, r in session.execute(q):
        by_event.setdefault(p.event_id, []).append((p, r))
    return by_event


def _names(session: Session, sport: str, event_id: str) -> tuple[str, str, str | None]:
    """(local/jugador A, visitante/jugador B, marcador o None). Sin resultado → None."""
    if sport == "football":
        h, a = aliased(Team), aliased(Team)
        row = session.execute(
            select(FootballMatchRow, h.name, a.name)
            .join(h, h.id == FootballMatchRow.home_team_id)
            .join(a, a.id == FootballMatchRow.away_team_id)
            .where(FootballMatchRow.external_id == event_id)
        ).first()
        if row is None:
            return "Local", "Visitante", None
        m, hn, an = row
        score = (
            f"{m.home_goals}-{m.away_goals}"
            if m.home_goals is not None and m.away_goals is not None
            else None
        )
        return hn, an, score
    pa, pb = aliased(Player), aliased(Player)
    row = session.execute(
        select(TennisMatchRow, pa.name, pb.name)
        .join(pa, pa.id == TennisMatchRow.player_a_id)
        .join(pb, pb.id == TennisMatchRow.player_b_id)
        .where(TennisMatchRow.external_id == event_id)
    ).first()
    if row is None:
        return "Jugador A", "Jugador B", None
    m, an, bn = row
    score = f"{m.sets_a}-{m.sets_b}" if m.sets_a is not None and m.sets_b is not None else None
    return an, bn, score


def _pick_label(event: str, home: str, away: str) -> str:
    return {
        "home_win": home,
        "home": home,
        "draw": "Empate",
        "away_win": away,
        "away": away,
        "player_a_win": home,
        "player_b_win": away,
    }.get(event, event)


def _league(competition: str | None) -> str:
    """'ATP · ATP Finals' → 'ATP Finals'; 'Premier League' queda igual."""
    tour, _, rest = (competition or "").partition(" · ")
    if not rest:
        return tour
    return rest if rest.lower().startswith(tour.lower()) else f"{tour} {rest}"


def _item(
    session: Session,
    sport: str,
    event_id: str,
    event: str,
    hit: bool | None,
    league: str = "",
    market: bool = False,
) -> RecapItem:
    home, away, score = _names(session, sport, event_id)
    sep = f" {score} " if score and hit is not None else " vs "
    return RecapItem(
        f"{home}{sep}{away}", _pick_label(event, home, away), hit, sport, _league(league), market
    )


def build_recap(
    session: Session, model: str, day: date, include_pending: bool = False
) -> Recap | None:
    """Cómo les fue a los partidos de ``day``. Con ``include_pending`` también se listan los
    publicados que aún no tienen resultado (``hit=None``), para la publicación de la mañana."""
    published = _published(session, day)
    if published is not None:
        rules = dict(published)
        rows = _final_rows(session, model, day, rules)
        recap = Recap(day, "canal")
        for event_id, rule in published:
            items = rows.get(event_id)
            league = items[0][0].competition if items else ""
            if rule == RULE_MARKET:
                ps = session.scalars(
                    select(PaperSelection).where(
                        PaperSelection.rule == "mercado_favorito_60",
                        PaperSelection.event_id == event_id,
                        PaperSelection.market == "1x2",
                    )
                ).first()
                if ps is None or (ps.outcome is None and not include_pending):
                    continue
                hit = None if ps.outcome is None else ps.outcome == 1
                recap.items.append(
                    _item(session, "football", event_id, ps.selection, hit, league, True)
                )
                continue
            if not items:
                continue
            p, r = max(items, key=lambda pr: pr[0].probability)
            resolved = r is not None and r.outcome is not None
            if not resolved and not include_pending:
                continue
            hit = r.outcome == 1 if resolved else None
            recap.items.append(_item(session, p.sport, event_id, p.event, hit, p.competition))
    else:
        recap = Recap(day, "modelo")
        for event_id, items in _final_rows(session, model, day).items():
            p, r = max(items, key=lambda pr: pr[0].probability)
            if p.confidence != "high" or r is None or r.outcome is None:
                continue
            recap.items.append(
                _item(session, p.sport, event_id, p.event, r.outcome == 1, p.competition)
            )
    return recap if (recap.n or (include_pending and recap.items)) else None


def channel_days(session: Session, upto: date) -> list[date]:
    """Días (de partido) con publicación registrada en el canal, hasta ``upto`` incluido."""
    runs = session.execute(
        select(PipelineRun.run_date, PipelineRun.details).where(
            PipelineRun.run_type == "daily", PipelineRun.run_date <= upto
        )
    ).all()
    return sorted({d for d, det in runs if (det or {}).get("channel_event_ids")})


def channel_totals(session: Session, model: str, upto: date) -> tuple[int, int]:
    """Acumulado del canal desde su primera publicación: (aciertos, resueltos)."""
    hits = n = 0
    for d in channel_days(session, upto):
        r = build_recap(session, model, d)
        if r and r.source == "canal":
            hits, n = hits + r.hits, n + r.n
    return hits, n
