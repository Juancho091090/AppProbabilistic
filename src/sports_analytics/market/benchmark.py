"""Comparación modelo vs mercado en 1X2 sobre los mismos partidos.

Para cada partido con precios de mercado se compara la distribución 1X2 final del
modelo con la del mercado (sin margen), usando reglas de puntuación propias:

* Brier multiclase: Σ_k (p_k − y_k)²   (0 = perfecto, 2 = peor; uniforme ≈ 0.667)
* Log-loss: −ln p_resultado              (uniforme = ln 3 ≈ 1.099)

La diferencia de log-loss (modelo − mercado) lleva un intervalo de confianza del 95 %
por bootstrap pareado: si incluye 0, con esta muestra no se distingue cuál es mejor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from sports_analytics.market.odds import MarketProbs, OddsQuote, reference_probs

EPS = 1e-12


@dataclass(frozen=True)
class PairedForecast:
    """Un partido: probabilidades (local, empate, visitante) del modelo y del mercado."""

    match_label: str
    competition: str
    model: tuple[float, float, float]
    market: tuple[float, float, float]
    outcome: int  # 0 local, 1 empate, 2 visitante


@dataclass(frozen=True)
class BenchmarkSummary:
    n: int
    brier_model: float
    brier_market: float
    logloss_model: float
    logloss_market: float
    logloss_diff: float  # modelo − mercado (negativo = el modelo es mejor)
    logloss_diff_ci: tuple[float, float]
    model_better_share: float  # % de partidos con menor log-loss del modelo
    favorite_hit_model: float
    favorite_hit_market: float
    mean_abs_diff_pp: float  # diferencia media absoluta de probabilidad, en puntos


def _arrays(pairs: Sequence[PairedForecast]):
    p = np.array([x.model for x in pairs], dtype=float)
    q = np.array([x.market for x in pairs], dtype=float)
    y = np.array([x.outcome for x in pairs], dtype=int)
    onehot = np.eye(3)[y]
    return p, q, y, onehot


def per_match_logloss(probs: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1.0))


def summarize(
    pairs: Sequence[PairedForecast], bootstrap_samples: int = 2000, seed: int = 0
) -> BenchmarkSummary | None:
    if not pairs:
        return None
    p, q, y, onehot = _arrays(pairs)
    ll_p, ll_q = per_match_logloss(p, y), per_match_logloss(q, y)
    diff = ll_p - ll_q
    if len(diff) > 1 and bootstrap_samples > 0:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, len(diff), size=(bootstrap_samples, len(diff)))
        means = diff[idx].mean(axis=1)
        ci = (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))
    else:
        ci = (float("nan"), float("nan"))
    return BenchmarkSummary(
        n=len(pairs),
        brier_model=float(((p - onehot) ** 2).sum(axis=1).mean()),
        brier_market=float(((q - onehot) ** 2).sum(axis=1).mean()),
        logloss_model=float(ll_p.mean()),
        logloss_market=float(ll_q.mean()),
        logloss_diff=float(diff.mean()),
        logloss_diff_ci=ci,
        model_better_share=float((diff < 0).mean()),
        favorite_hit_model=float((p.argmax(axis=1) == y).mean()),
        favorite_hit_market=float((q.argmax(axis=1) == y).mean()),
        mean_abs_diff_pp=float(np.abs(p - q).mean() * 100),
    )


def verdict(s: BenchmarkSummary) -> str:
    lo, hi = s.logloss_diff_ci
    if np.isnan(lo):
        return "Muestra insuficiente para concluir."
    if hi < 0:
        return "El modelo tiene menor log-loss que el mercado (diferencia significativa al 95 %)."
    if lo > 0:
        return "El mercado tiene menor log-loss que el modelo (diferencia significativa al 95 %)."
    return "Con esta muestra no hay diferencia significativa entre modelo y mercado."


def to_markdown(s: BenchmarkSummary | None, title: str, note: str = "") -> list[str]:
    lines = [f"## {title}", ""]
    if s is None:
        return [*lines, "Sin partidos con probabilidades de mercado en el periodo.", ""]
    lo, hi = s.logloss_diff_ci
    lines += [
        f"Partidos comparados: **{s.n}**" + (f" · {note}" if note else ""),
        "",
        "| Métrica | Modelo | Mercado |",
        "|---|---|---|",
        f"| Brier multiclase (menor es mejor) | {s.brier_model:.4f} | {s.brier_market:.4f} |",
        f"| Log-loss (menor es mejor) | {s.logloss_model:.4f} | {s.logloss_market:.4f} |",
        f"| Acierto del favorito | {s.favorite_hit_model:.1%} | {s.favorite_hit_market:.1%} |",
        "",
        f"- Diferencia de log-loss (modelo − mercado): {s.logloss_diff:+.4f} "
        f"· IC 95 % [{lo:+.4f}, {hi:+.4f}]",
        f"- Partidos en que el modelo puntúa mejor: {s.model_better_share:.1%}",
        f"- Diferencia media absoluta entre ambas probabilidades: {s.mean_abs_diff_pp:.1f} pp",
        f"- **Lectura:** {verdict(s)}",
        "",
    ]
    return lines


MARKET_MODEL = "mercado"
EVENT_INDEX = {"home_win": 0, "draw": 1, "away_win": 2}


def market_probs_by_match(quotes: dict[str, Sequence[OddsQuote]], cfg) -> dict[str, MarketProbs]:
    """external_id → probabilidades de referencia según ``MarketConfig``."""
    out = {}
    for ext, qs in quotes.items():
        mp = reference_probs(qs, cfg.preferred_bookmakers, cfg.fallback, cfg.devig_method)
        if mp is not None:
            out[ext] = mp
    return out


def live_pairs(rows, market: dict[str, MarketProbs], final_model: str) -> list[PairedForecast]:
    """Predicciones 1X2 liquidadas del modelo final → pares modelo/mercado.

    ``rows``: iterable de (PredictionRow, PredictionResult)."""
    by_event: dict[str, dict] = {}
    for p, r in rows:
        if p.market != "1x2" or p.model != final_model or p.sport != "football":
            continue
        if p.event_id not in market or p.event not in EVENT_INDEX:
            continue
        e = by_event.setdefault(
            p.event_id, {"probs": [None, None, None], "outcome": None, "comp": p.competition}
        )
        e["probs"][EVENT_INDEX[p.event]] = p.probability
        if r.outcome == 1:
            e["outcome"] = EVENT_INDEX[p.event]
    pairs = []
    for ext, e in by_event.items():
        if None in e["probs"] or e["outcome"] is None:
            continue
        m = market[ext]
        pairs.append(
            PairedForecast(
                ext, e["comp"], tuple(e["probs"]), (m.home, m.draw, m.away), e["outcome"]
            )
        )
    return pairs
