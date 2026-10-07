"""Ajuste semanal de la recalibración multinomial del 1X2 (``MultinomialRecalibrator``).

1. Backtest walk-forward de los últimos ``window_days`` (sin leakage): probabilidades
   1X2 *crudas* del ensemble y resultado real de cada partido.
2. Validación honesta: se ajusta con el primer (1 − holdout) % cronológico y se mide en
   el último tramo, que el ajuste no vio.
3. Si mejora la log-loss fuera de muestra, se reajusta con todo el periodo y se guarda
   en ``data_sources`` (proveedor ``recalibration_football_1x2``). El pipeline diario
   lo aplica mientras no supere ``max_age_days``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from sports_analytics.backtesting.engine import run_backtest
from sports_analytics.config.loader import AppConfig
from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger
from sports_analytics.core.timeutils import ensure_utc, local_today, now_utc
from sports_analytics.db import repository as repo
from sports_analytics.db.models import DataSource
from sports_analytics.models.calibration import MultinomialRecalibrator

log = get_logger(__name__)

PROVIDER = "recalibration_football_1x2"
HISTORY_YEARS = 3


def _scores(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    onehot = np.eye(3)[y]
    return {
        "log_loss": float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)).mean()),
        "brier": float(((p - onehot) ** 2).sum(axis=1).mean()),
        "favorite_hit": float((p.argmax(axis=1) == y).mean()),
        "mean_home": float(p[:, 0].mean()),
        "mean_draw": float(p[:, 1].mean()),
        "observed_home": float((y == 0).mean()),
        "observed_draw": float((y == 1).mean()),
    }


def favorite_reliability(
    raw: np.ndarray, recal: np.ndarray, y: np.ndarray, edges=(0.0, 0.4, 0.5, 0.6, 0.7, 1.0)
) -> list[dict[str, Any]]:
    """Por rango de probabilidad del favorito (según el modelo crudo): media predicha
    cruda, recalibrada y frecuencia real con la que ganó ese favorito."""
    fav = raw.argmax(axis=1)
    p_raw = raw[np.arange(len(y)), fav]
    p_rec = recal[np.arange(len(y)), fav]
    hit = (fav == y).astype(float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p_raw >= lo) & (p_raw < hi)
        if m.any():
            rows.append(
                {
                    "range": f"{lo:.1f}-{hi:.1f}",
                    "n": int(m.sum()),
                    "raw": float(p_raw[m].mean()),
                    "recal": float(p_rec[m].mean()),
                    "observed": float(hit[m].mean()),
                }
            )
    return rows


@dataclass
class RecalibrationResult:
    status: str  # applied | rejected | insufficient | disabled
    n: int = 0
    params: dict[str, float] = field(default_factory=dict)
    holdout: dict[str, Any] = field(default_factory=dict)
    window: tuple[str, str] = ("", "")

    def to_markdown(self) -> str:
        lines = ["# Recalibración 1X2 (fútbol)", ""]
        lines.append(
            f"- Periodo walk-forward: {self.window[0]} → {self.window[1]} · partidos: {self.n}"
        )
        status = {
            "applied": "✅ aplicada en el informe diario",
            "rejected": "⏸️ no aplicada: no mejora fuera de muestra",
            "insufficient": "⏸️ muestra insuficiente",
            "disabled": "⏸️ desactivada en models.yaml",
        }[self.status]
        lines.append(f"- Estado: {status}")
        if self.params:
            p = self.params
            lines.append(
                f"- Parámetros (todo el periodo): a = {p['a']:.3f} (>1 separa probabilidades), "
                f"b_local = {p['b_home']:+.3f}, b_visitante = {p['b_away']:+.3f}"
            )
        h = self.holdout
        if h:
            r, c = h["raw"], h["recal"]
            lines += [
                "",
                f"## Validación fuera de muestra (último tramo, {h['n_test']} partidos; "
                f"ajuste con {h['n_train']})",
                "",
                "| Métrica | Sin recalibrar | Recalibrado |",
                "|---|---|---|",
                f"| Log-loss 1X2 (menor es mejor) | {r['log_loss']:.4f} | {c['log_loss']:.4f} |",
                f"| Brier multiclase (menor es mejor) | {r['brier']:.4f} | {c['brier']:.4f} |",
                f"| Acierto del favorito | {r['favorite_hit']:.1%} | {c['favorite_hit']:.1%} |",
                f"| Prob. media local (real {r['observed_home']:.1%}) | {r['mean_home']:.1%} | "
                f"{c['mean_home']:.1%} |",
                f"| Prob. media empate (real {r['observed_draw']:.1%}) | {r['mean_draw']:.1%} | "
                f"{c['mean_draw']:.1%} |",
                "",
                "### Calibración del favorito (fuera de muestra)",
                "",
                "| Prob. del favorito | n | Predicha cruda | Predicha recalibrada | Ganó realmente |",
                "|---|---|---|---|---|",
            ]
            for row in h["favorite"]:
                lines.append(
                    f"| {row['range']} | {row['n']} | {row['raw']:.1%} | {row['recal']:.1%} | "
                    f"{row['observed']:.1%} |"
                )
        return "\n".join(lines) + "\n"


def fit_recalibration(
    session: Session, settings: Settings, config: AppConfig, now: datetime | None = None
) -> RecalibrationResult:
    cfg = config.models.calibration.get("recalibration_1x2") or {}
    now = ensure_utc(now) if now else now_utc()
    tz = settings.tz
    end = local_today(tz, now) - timedelta(days=1)
    start = end - timedelta(days=int(cfg.get("window_days", 150)))
    result = RecalibrationResult("disabled", window=(start.isoformat(), end.isoformat()))
    if not cfg.get("enabled", False):
        return result

    history = repo.load_football_history(session, now - timedelta(days=365 * HISTORY_YEARS))
    names = {c.key: c.name for c in config.competitions.football}
    bt = run_backtest(
        "football", history, config, start, end, tz, int(cfg.get("refit_days", 7)), names
    )
    data = sorted(bt.ensemble_1x2, key=lambda t: t[2])
    result.n = len(data)
    if result.n < int(cfg.get("min_samples", 300)):
        result.status = "insufficient"
        _save(session, result, now)
        return result

    p = np.array([d[0] for d in data], dtype=float)
    y = np.array([d[1] for d in data], dtype=int)
    k = int(round(result.n * (1 - float(cfg.get("holdout_fraction", 0.3)))))
    l2 = float(cfg.get("l2", 1.0))
    train_fit = MultinomialRecalibrator().fit(p[:k], y[:k], l2)
    raw_test, rec_test = p[k:], train_fit.transform(p[k:])
    result.holdout = {
        "n_train": k,
        "n_test": result.n - k,
        "train_params": train_fit.to_dict(),
        "raw": _scores(raw_test, y[k:]),
        "recal": _scores(rec_test, y[k:]),
        "favorite": favorite_reliability(raw_test, rec_test, y[k:]),
    }
    full = MultinomialRecalibrator().fit(p, y, l2)
    result.params = full.to_dict()
    improves = result.holdout["recal"]["log_loss"] < result.holdout["raw"]["log_loss"]
    result.status = "applied" if improves else "rejected"
    _save(session, result, now)
    log.info(
        "recalibration_fitted", extra={"status": result.status, "n": result.n, **result.params}
    )
    return result


def _save(session: Session, result: RecalibrationResult, now: datetime) -> None:
    repo.record_data_source(
        session,
        PROVIDER,
        ok=True,
        calls=0,
        details={
            "status": result.status,
            "params": result.params,
            "fitted_at": now.isoformat(),
            "n": result.n,
            "window": list(result.window),
            "holdout": result.holdout,
        },
    )


def load_recalibrator(
    session: Session, config: AppConfig, as_of: datetime
) -> MultinomialRecalibrator | None:
    """Parámetros vigentes (aplicados y no caducados) o None."""
    cfg = config.models.calibration.get("recalibration_1x2") or {}
    if not cfg.get("enabled", False):
        return None
    row = session.scalar(select(DataSource).where(DataSource.provider == PROVIDER))
    details = dict(row.details or {}) if row else {}
    if details.get("status") != "applied" or not details.get("params"):
        return None
    fitted = ensure_utc(datetime.fromisoformat(details["fitted_at"]))
    if fitted > ensure_utc(as_of) or ensure_utc(as_of) - fitted > timedelta(
        days=int(cfg.get("max_age_days", 21))
    ):
        return None
    return MultinomialRecalibrator.from_dict(details["params"])
