"""Backtesting walk-forward sin data leakage.

Para cada ventana [t, t + paso):
1. Se entrenan los modelos con partidos que empezaron estrictamente antes de t.
2. Se predicen los partidos de la ventana (todos con inicio >= t).
3. Se comparan con el resultado real y se acumulan métricas por segmento.

``refit_every_days`` = 1 reproduce exactamente el pipeline diario; valores mayores
aceleran el cálculo sin introducir leakage (el modelo es más antiguo, no más nuevo).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sports_analytics.backtesting.metrics import (
    ResolvedPrediction,
    SegmentMetric,
    reliability_table,
    segment_metrics,
)
from sports_analytics.config.loader import AppConfig
from sports_analytics.core.logging import get_logger
from sports_analytics.core.timeutils import local_day_bounds_utc
from sports_analytics.data.schemas import FootballMatch, MatchStatus, TennisMatch
from sports_analytics.market.benchmark import (
    MARKET_MODEL,
    PairedForecast,
    summarize,
)
from sports_analytics.market.benchmark import to_markdown as benchmark_markdown
from sports_analytics.market.odds import MarketProbs
from sports_analytics.models.football.predictor import FootballPredictor
from sports_analytics.models.outputs import MatchForecast, PredictionRecord
from sports_analytics.models.tennis.predictor import TennisPredictor

log = get_logger(__name__)


def football_outcome(rec: PredictionRecord, m: FootballMatch) -> int | None:
    hg, ag = m.home_goals, m.away_goals
    if rec.market == "1x2":
        actual = "home_win" if hg > ag else "draw" if hg == ag else "away_win"
        return int(rec.event == actual)
    if rec.market == "goals_total":
        return int(hg + ag > rec.line)
    if rec.market == "corners_total":
        return int(m.home_corners + m.away_corners > rec.line) if m.has_corners else None
    return None


def tennis_outcome(rec: PredictionRecord, m: TennisMatch) -> int | None:
    if m.retired:
        return None
    if rec.market == "winner":
        return int(m.winner == ("A" if rec.event == "player_a_win" else "B"))
    if rec.market == "at_least_one_set":
        sets = m.sets_a if rec.event == "player_a" else m.sets_b
        return None if sets is None else int(sets >= 1)
    if rec.market == "games_total":
        if m.games_a is None or m.games_b is None:
            return None
        return int(m.games_a + m.games_b > rec.line)
    return None


@dataclass
class BacktestReport:
    sport: str
    start: date
    end: date
    refit_every_days: int
    n_matches: int = 0
    n_skipped: int = 0
    predictions: list[ResolvedPrediction] = field(default_factory=list)
    metrics: list[SegmentMetric] = field(default_factory=list)
    market_pairs: list[PairedForecast] = field(default_factory=list)
    market_available: bool = False  # se pasaron precios de mercado al backtest
    bootstrap_samples: int = 2000

    def to_markdown(self, model_version: str) -> str:
        lines = [
            f"# Backtesting {self.sport} · {self.start} → {self.end}",
            "",
            f"- Reentrenamiento cada {self.refit_every_days} día(s), walk-forward sin leakage",
            f"- Partidos evaluados: {self.n_matches} (omitidos por falta de histórico: {self.n_skipped})",
            f"- Predicciones resueltas: {len(self.predictions)}",
            "",
            "## Métricas globales por modelo y mercado",
            "",
            "| Modelo | Mercado | n | Brier | LogLoss | Accuracy | ECE |",
            "|---|---|---|---|---|---|---|",
        ]
        for m in self.metrics:
            if m.segment_type == "global":
                lines.append(
                    f"| {m.model} | {m.market} | {m.n} | {m.brier:.4f} | {m.log_loss:.4f} | "
                    f"{m.accuracy:.3f} | {m.ece:.4f} |"
                )
        for seg, title in (
            ("competition", "Por competición"),
            ("prob_bucket", "Por rango de probabilidad"),
            ("event", "Por tipo de evento"),
        ):
            lines += [
                "",
                f"## {title} ({model_version})",
                "",
                "| Mercado | Segmento | n | Brier | ECE | Prob. media | Frecuencia real |",
                "|---|---|---|---|---|---|---|",
            ]
            for m in self.metrics:
                if m.segment_type == seg and m.model == model_version:
                    lines.append(
                        f"| {m.market} | {m.segment_value} | {m.n} | {m.brier:.4f} | {m.ece:.4f} | "
                        f"{m.mean_prob:.3f} | {m.hit_rate:.3f} |"
                    )
        if self.market_available:
            lines.append("")
            lines += benchmark_markdown(
                summarize(self.market_pairs, self.bootstrap_samples),
                "Modelo vs mercado (1X2, mismos partidos)",
                "probabilidades del mercado sin margen; solo partidos con precios guardados",
            )
            by_comp: dict[str, list[PairedForecast]] = {}
            for pf in self.market_pairs:
                by_comp.setdefault(pf.competition, []).append(pf)
            if len(by_comp) > 1:
                lines += [
                    "| Competición | n | Log-loss modelo | Log-loss mercado | Brier modelo | Brier mercado |",
                    "|---|---|---|---|---|---|",
                ]
                for comp, items in sorted(by_comp.items(), key=lambda kv: -len(kv[1])):
                    s = summarize(items, 0)
                    lines.append(
                        f"| {comp} | {s.n} | {s.logloss_model:.4f} | {s.logloss_market:.4f} | "
                        f"{s.brier_model:.4f} | {s.brier_market:.4f} |"
                    )
                lines.append("")
        main = [p for p in self.predictions if p.model == model_version]
        if main:
            lines += [
                "",
                f"## Curva de calibración ({model_version}, todos los mercados)",
                "",
                "| Rango | Predicha | Observada | n |",
                "|---|---|---|---|",
            ]
            for b in reliability_table(main):
                lines.append(
                    f"| {b['bin']} | {b['predicha']:.3f} | {b['observada']:.3f} | {b['n']} |"
                )
        return "\n".join(lines) + "\n"


def _resolve(forecast: MatchForecast, match, sport: str) -> list[ResolvedPrediction]:
    out = []
    for rec in forecast.records:
        y = football_outcome(rec, match) if sport == "football" else tennis_outcome(rec, match)
        if y is not None:
            out.append(
                ResolvedPrediction(
                    sport,
                    forecast.competition,
                    rec.model,
                    rec.market,
                    rec.event,
                    rec.probability,
                    y,
                    rec.confidence,
                )
            )
    return out


def _add_market(
    report: BacktestReport, f: MatchForecast, m: FootballMatch, mp: MarketProbs, version: str
) -> None:
    outcome = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
    model = tuple(float(f.markets["1x2"][k]) for k in ("home", "draw", "away"))
    market = (mp.home, mp.draw, mp.away)
    report.market_pairs.append(
        PairedForecast(f"{f.home_or_a} vs {f.away_or_b}", f.competition, model, market, outcome)
    )
    for idx, event in enumerate(("home_win", "draw", "away_win")):
        y = int(outcome == idx)
        report.predictions.append(
            ResolvedPrediction(
                "football", f.competition, MARKET_MODEL, "1x2", event, market[idx], y
            )
        )
        report.predictions.append(
            ResolvedPrediction(
                "football", f.competition, f"{version}@mercado", "1x2", event, model[idx], y
            )
        )


def run_backtest(
    sport: str,
    history: Sequence[FootballMatch | TennisMatch],
    config: AppConfig,
    start: date,
    end: date,
    tz: ZoneInfo,
    refit_every_days: int = 7,
    competition_names: dict[str, str] | None = None,
    market: dict[str, MarketProbs] | None = None,
) -> BacktestReport:
    """``market``: probabilidades de referencia por external_id (benchmark opcional)."""
    if sport not in ("football", "tennis"):
        raise ValueError("sport debe ser football o tennis")
    if refit_every_days < 1 or end < start:
        raise ValueError("Rango de fechas o paso inválidos")
    mcfg = config.models
    names = competition_names or {}
    report = BacktestReport(sport, start, end, refit_every_days)
    report.market_available = market is not None and sport == "football"
    report.bootstrap_samples = config.models.market.bootstrap_samples
    finished = sorted((m for m in history if m.is_finished), key=lambda m: m.kickoff_utc)

    day = start
    while day <= end:
        window_end_day = min(day + timedelta(days=refit_every_days - 1), end)
        as_of, _ = local_day_bounds_utc(day, tz)
        _, until = local_day_bounds_utc(window_end_day, tz)
        targets = [m for m in finished if as_of <= m.kickoff_utc < until]
        if targets:
            if sport == "football":
                predictor = FootballPredictor(
                    mcfg.football, mcfg.recency, mcfg.confidence, mcfg.version
                )
            else:
                predictor = TennisPredictor(
                    mcfg.tennis, mcfg.recency, mcfg.confidence, mcfg.version
                )
            predictor.fit(finished, as_of)  # fit() filtra internamente kickoff < as_of
            for m in targets:
                if sport == "football":
                    if (
                        min(
                            predictor.poisson.strength(m.home_team).n_matches,
                            predictor.poisson.strength(m.away_team).n_matches,
                        )
                        == 0
                    ):
                        report.n_skipped += 1
                        continue
                    scheduled = m.model_copy(
                        update={
                            "status": MatchStatus.SCHEDULED,
                            "home_goals": None,
                            "away_goals": None,
                            "home_corners": None,
                            "away_corners": None,
                        }
                    )
                    f = predictor.predict(
                        scheduled, names.get(m.competition_key, m.competition_key)
                    )
                else:
                    if (
                        m.retired
                        or min(
                            predictor.elo.matches_played(m.player_a),
                            predictor.elo.matches_played(m.player_b),
                        )
                        == 0
                    ):
                        report.n_skipped += 1
                        continue
                    scheduled = m.model_copy(
                        update={"status": MatchStatus.SCHEDULED, "winner": None}
                    )
                    f = predictor.predict(scheduled)
                report.n_matches += 1
                report.predictions.extend(_resolve(f, m, sport))
                if report.market_available and m.match_id in market:
                    _add_market(report, f, m, market[m.match_id], mcfg.version)
            log.info(
                "backtest_window",
                extra={"sport": sport, "from": day.isoformat(), "matches": len(targets)},
            )
        day = window_end_day + timedelta(days=1)
    report.metrics = segment_metrics(report.predictions)
    return report


def kickoff_range(history: Sequence) -> tuple[datetime, datetime] | None:
    if not history:
        return None
    ks = [m.kickoff_utc for m in history]
    return min(ks), max(ks)
