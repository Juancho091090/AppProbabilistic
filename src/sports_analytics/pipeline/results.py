"""Registro de resultados reales y evaluación posterior de las predicciones.

Una predicción se "liquida" cuando su partido terminó: ``outcome`` = 1 si el evento
ocurrió, 0 si no. Se anula (``outcome`` = None + motivo) si el partido se suspende,
hay retiro en tenis o faltan los datos necesarios tras el plazo de espera.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sports_analytics.backtesting.metrics import ResolvedPrediction, segment_metrics
from sports_analytics.config.loader import MarketConfig
from sports_analytics.core.timeutils import now_utc
from sports_analytics.db import repository as repo
from sports_analytics.db.models import (
    FootballMatchRow,
    FootballStatistics,
    ModelMetric,
    PredictionRow,
    TennisMatchRow,
)
from sports_analytics.market.benchmark import MARKET_MODEL, live_pairs, market_probs_by_match

VOID_STATUSES = {"postponed", "cancelled"}
WAIT_RESULT = timedelta(days=3)
WAIT_STATS = timedelta(days=7)

Outcome = tuple[int | None, float | None, str | None]  # (outcome, valor real, motivo anulación)


def _over(total: float, line: float | None) -> int:
    return int(total > (line or 0))


def football_outcome(
    p: PredictionRow, m: FootballMatchRow | None, st: FootballStatistics | None, now: datetime
) -> Outcome | None:
    if m is None:
        return (None, None, "match_not_found") if now - p.kickoff_utc > WAIT_RESULT else None
    if m.status in VOID_STATUSES:
        return None, None, m.status
    if m.status != "finished" or m.home_goals is None:
        return (None, None, "no_result") if now - p.kickoff_utc > WAIT_RESULT else None
    hg, ag = m.home_goals, m.away_goals
    if p.market == "1x2":
        result = "home_win" if hg > ag else "draw" if hg == ag else "away_win"
        return int(p.event == result), None, None
    if p.market == "goals_total":
        return _over(hg + ag, p.line), float(hg + ag), None
    if p.market == "corners_total":
        if st is not None and st.available and st.home_corners is not None:
            total = st.home_corners + st.away_corners
            return _over(total, p.line), float(total), None
        if (st is not None and not st.available) or now - p.kickoff_utc > WAIT_STATS:
            return None, None, "no_stats"
        return None  # esperar a que se carguen las estadísticas
    return None, None, f"unknown_market:{p.market}"


def tennis_outcome(p: PredictionRow, m: TennisMatchRow | None, now: datetime) -> Outcome | None:
    if m is None or m.status != "finished" or m.winner is None:
        if m is not None and m.status in VOID_STATUSES:
            return None, None, m.status
        return (None, None, "no_result") if now - p.kickoff_utc > WAIT_RESULT else None
    if m.retired:
        return None, None, "retired"
    if p.market == "winner":
        side = "A" if p.event == "player_a_win" else "B"
        return int(m.winner == side), None, None
    if p.market == "at_least_one_set":
        sets = m.sets_a if p.event == "player_a" else m.sets_b
        return (
            (int((sets or 0) >= 1), float(sets or 0), None)
            if sets is not None
            else (None, None, "no_score")
        )
    if p.market == "games_total":
        if m.games_a is None or m.games_b is None:
            return None, None, "no_score"
        total = m.games_a + m.games_b
        return _over(total, p.line), float(total), None
    return None, None, f"unknown_market:{p.market}"


def settle_predictions(session: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or now_utc()
    pending = repo.unsettled_predictions(session, before=now - timedelta(hours=3))
    if not pending:
        return {"pending": 0, "settled": 0, "void": 0}
    fb_ids = {p.event_id for p in pending if p.sport == "football"}
    tn_ids = {p.event_id for p in pending if p.sport == "tennis"}
    fb = (
        {
            m.external_id: (m, st)
            for m, st in session.execute(
                select(FootballMatchRow, FootballStatistics)
                .outerjoin(FootballStatistics, FootballStatistics.match_id == FootballMatchRow.id)
                .where(FootballMatchRow.external_id.in_(fb_ids))
            )
        }
        if fb_ids
        else {}
    )
    tn = (
        {
            m.external_id: m
            for m in session.scalars(
                select(TennisMatchRow).where(TennisMatchRow.external_id.in_(tn_ids))
            )
        }
        if tn_ids
        else {}
    )

    results, settled, void = [], 0, 0
    for p in pending:
        if p.sport == "football":
            m, st = fb.get(p.event_id, (None, None))
            res = football_outcome(p, m, st, now)
        else:
            res = tennis_outcome(p, tn.get(p.event_id), now)
        if res is None:
            continue
        outcome, actual, reason = res
        results.append(
            {
                "prediction_id": p.id,
                "outcome": outcome,
                "actual_value": actual,
                "void_reason": reason,
                "settled_at": now,
            }
        )
        settled += outcome is not None
        void += outcome is None
    repo.save_results(session, results)
    return {"pending": len(pending), "settled": settled, "void": void}


def refresh_live_metrics(
    session: Session,
    now: datetime | None = None,
    market_cfg: MarketConfig | None = None,
    final_model: str | None = None,
) -> int:
    """Recalcula model_metrics (source='live') con todas las predicciones resueltas.

    Con ``market_cfg`` y ``final_model`` añade el benchmark de mercado en 1X2: el modelo
    ``mercado`` y ``<final_model>@mercado`` (el modelo final restringido a los mismos
    partidos), para comparar en igualdad de condiciones."""
    now = now or now_utc()
    pairs = repo.settled_predictions(session)
    rows = [
        ResolvedPrediction(
            p.sport,
            p.competition,
            p.model,
            p.market,
            p.event,
            p.probability,
            int(r.outcome),
            p.confidence,
        )
        for p, r in pairs
    ]
    if market_cfg is not None and market_cfg.enabled and final_model:
        rows += market_resolved_rows(session, pairs, market_cfg, final_model)
    session.query(ModelMetric).filter(ModelMetric.source == "live").delete()
    metrics = segment_metrics(rows)
    dates = [p.prediction_date for p, _ in pairs]
    for m in metrics:
        session.add(
            ModelMetric(
                sport=m.sport,
                model=m.model,
                market=m.market,
                segment_type=m.segment_type,
                segment_value=m.segment_value,
                n=m.n,
                brier=m.brier,
                log_loss=m.log_loss,
                accuracy=m.accuracy,
                ece=m.ece,
                source="live",
                computed_at=now,
                window_start=min(dates) if dates else None,
                window_end=max(dates) if dates else None,
            )
        )
    return len(metrics)


def live_market_pairs(session: Session, market_cfg: MarketConfig, final_model: str):
    pairs = repo.settled_predictions(session, sport="football")
    events = {p.event_id for p, _ in pairs if p.market == "1x2"}
    market = market_probs_by_match(repo.load_market_quotes(session, events), market_cfg)
    return live_pairs(pairs, market, final_model)


def market_resolved_rows(
    session: Session, pairs, market_cfg: MarketConfig, final_model: str
) -> list[ResolvedPrediction]:
    events = {p.event_id for p, _ in pairs if p.market == "1x2" and p.sport == "football"}
    if not events:
        return []
    market = market_probs_by_match(repo.load_market_quotes(session, events), market_cfg)
    out = []
    for pf in live_pairs(pairs, market, final_model):
        for idx, event in enumerate(("home_win", "draw", "away_win")):
            y = int(pf.outcome == idx)
            out.append(
                ResolvedPrediction(
                    "football", pf.competition, MARKET_MODEL, "1x2", event, pf.market[idx], y
                )
            )
            out.append(
                ResolvedPrediction(
                    "football",
                    pf.competition,
                    f"{final_model}@mercado",
                    "1x2",
                    event,
                    pf.model[idx],
                    y,
                )
            )
    return out


def latest_global_metrics(session: Session) -> list[ModelMetric]:
    return list(
        session.scalars(
            select(ModelMetric).where(
                ModelMetric.source == "live", ModelMetric.segment_type == "global"
            )
        )
    )
