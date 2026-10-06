"""Re-estimación de pesos del ensemble y calibradores con predicciones ya resueltas.

Solo se usan predicciones cuyo partido empezó antes de ``as_of`` (sin leakage). Mientras
no haya muestras suficientes se mantienen los pesos de ``models.yaml`` y no se calibra.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from sports_analytics.core.logging import get_logger
from sports_analytics.db import repository as repo
from sports_analytics.models.calibration import ProbabilityCalibrator
from sports_analytics.models.ensemble import fit_weights

log = get_logger(__name__)

MARKETS = {
    "football": ("1x2", ["home_win", "draw", "away_win"]),
    "tennis": ("winner", ["player_a_win", "player_b_win"]),
}


@dataclass
class CalibrationState:
    weights: dict[str, float] | None
    calibrator: ProbabilityCalibrator | None
    n_events: int


def _event_matrix(pairs, sport: str, model: str, as_of: datetime):
    """event_id -> (vector de probabilidades, índice del resultado real, kickoff)."""
    market, events = MARKETS[sport]
    probs: dict[str, dict[str, float]] = defaultdict(dict)
    outs: dict[str, dict[str, int]] = defaultdict(dict)
    kickoff: dict[str, datetime] = {}
    for p, r in pairs:
        if p.sport != sport or p.market != market or p.model != model or p.kickoff_utc >= as_of:
            continue
        probs[p.event_id][p.event] = p.probability
        outs[p.event_id][p.event] = int(r.outcome)
        kickoff[p.event_id] = p.kickoff_utc
    out = {}
    for eid, d in probs.items():
        if sport == "tennis" and "player_a_win" in d:
            # Los modelos individuales de tenis solo guardan P(A gana)
            d.setdefault("player_b_win", 1 - d["player_a_win"])
        if not all(e in d for e in events):
            continue
        o = outs[eid]
        y = next((events.index(e) for e, v in o.items() if v == 1), None)
        if y is None and sport == "tennis" and o.get("player_a_win") == 0:
            y = 1
        if y is not None:
            out[eid] = (np.array([d[e] for e in events]), y, kickoff[eid])
    return out


def load_calibration_state(
    session: Session,
    sport: str,
    model_version: str,
    model_names: list[str],
    default_weights: dict[str, float],
    calibration_cfg: dict,
    as_of: datetime,
) -> CalibrationState:
    pairs = repo.settled_predictions(session, sport)
    ens = _event_matrix(pairs, sport, model_version, as_of)

    calibrator = None
    if ens:
        p = np.vstack([v[0] for v in ens.values()])
        y = np.array([v[1] for v in ens.values()])
        cal = ProbabilityCalibrator.from_config(calibration_cfg).fit(
            p, y, event_times=[v[2] for v in ens.values()], as_of=as_of
        )
        calibrator = cal if cal.method != "identity" else None

    # Pesos: solo eventos donde todos los modelos tienen predicción
    per_model = {m: _event_matrix(pairs, sport, m, as_of) for m in model_names}
    common = set.intersection(*(set(d) for d in per_model.values())) if per_model else set()
    weights = None
    if common:
        ids = sorted(common)
        weights = fit_weights(
            {m: np.vstack([per_model[m][i][0] for i in ids]) for m in model_names},
            np.array([per_model[model_names[0]][i][1] for i in ids]),
            event_times=[per_model[model_names[0]][i][2] for i in ids],
            as_of=as_of,
            min_samples=calibration_cfg["min_samples_platt"],
            fallback=default_weights,
        )
        if weights == dict(default_weights):
            weights = None
    log.info(
        "calibration_state",
        extra={
            "sport": sport,
            "events": len(ens),
            "calibrated": calibrator is not None,
            "weights_fitted": weights is not None,
        },
    )
    return CalibrationState(weights, calibrator, len(ens))
