"""Orquestación de modelos de tenis para un instante ``as_of``.

Ganador: ensemble de Elo general, Elo de superficie, regresión logística y Markov
(este último solo si hay estadísticas de saque/resto suficientes, vía Barnett-Clarke).

Sets y juegos: se usa el modelo de Markov con probabilidades de punto al saque
ajustadas para que P(A gana) coincida con la probabilidad FINAL del ensemble. Así
"ganador", "al menos un set" y "juegos" son coherentes entre sí. El nivel medio de
saque proviene de las estadísticas de los jugadores si existen, o del promedio
del circuito (config) si no.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sports_analytics.core.timeutils import ensure_utc, now_utc
from sports_analytics.data.schemas import TennisMatch
from sports_analytics.features import tennis as tf
from sports_analytics.models.calibration import ProbabilityCalibrator
from sports_analytics.models.confidence import assess_confidence
from sports_analytics.models.ensemble import combine
from sports_analytics.models.logistic import ProbabilisticLogit
from sports_analytics.models.outputs import MatchForecast, PredictionRecord
from sports_analytics.models.tennis.elo import TennisElo
from sports_analytics.models.tennis.markov import (
    barnett_clarke,
    match_distribution,
    serve_probs_from_match_prob,
)


def is_grand_slam(match: TennisMatch) -> bool:
    text = f"{match.category} {match.tournament}".lower()
    return "grand slam" in text or any(
        s in text
        for s in ("australian open", "roland garros", "french open", "wimbledon", "us open")
    )


@dataclass
class TennisPredictor:
    cfg: dict  # sección tennis de models.yaml
    recency_cfg: dict
    confidence_cfg: dict
    model_version: str = "ensemble_v1"
    weights: dict[str, float] | None = None
    calibrator: ProbabilityCalibrator | None = None
    as_of: datetime | None = None
    elo: TennisElo = field(init=False)
    logistic: ProbabilisticLogit = field(init=False)
    state: tf.TennisFeatureState | None = field(init=False, default=None)

    def fit(self, history: Sequence[TennisMatch], as_of: datetime) -> TennisPredictor:
        self.as_of = ensure_utc(as_of)
        self.elo = TennisElo.from_config(self.cfg["elo"])
        lcfg = self.cfg["logistic"]
        x, y, self.state = tf.build_training_set(
            history,
            self.as_of,
            self.elo,
            self.recency_cfg["half_life_days"]["tennis"],
            lcfg["min_history"],
        )
        self.logistic = ProbabilisticLogit(
            tf.FEATURE_NAMES, min_samples=lcfg["min_samples"], c=lcfg["C"]
        ).fit(x, y)
        return self

    def _serve_inputs(self, m: TennisMatch) -> tuple[float, float] | None:
        mk = self.cfg["markov"]
        sa = self.state.player_stats(m.player_a, self.as_of)
        sb = self.state.player_stats(m.player_b, self.as_of)
        if min(sa["n_stats"], sb["n_stats"]) < mk["min_stats_matches"]:
            return None
        if any(np.isnan(v) for v in (sa["spw"], sa["rpw"], sb["spw"], sb["rpw"])):
            return None
        return barnett_clarke(
            sa["spw"], sa["rpw"], sb["spw"], sb["rpw"], mk["tour_avg_rpw"][m.tour]
        )

    def predict(self, m: TennisMatch) -> MatchForecast:
        if self.as_of is None:
            raise RuntimeError("Llama a fit() antes de predict()")
        if m.kickoff_utc < self.as_of:
            raise ValueError("El partido comenzó antes de as_of; no se puede predecir sin leakage")
        mk = self.cfg["markov"]
        final_tb = (
            mk["grand_slam_final_set_tiebreak"] if is_grand_slam(m) else mk["final_set_tiebreak"]
        )
        notes: list[str] = []

        elo_p = self.elo.predict(m.player_a, m.player_b, m.surface)
        per_model: dict[str, list[float] | None] = {
            "elo": [elo_p["elo"], 1 - elo_p["elo"]],
            "surface_elo": [elo_p["surface_elo"], 1 - elo_p["surface_elo"]],
            "logistic": None,
            "markov": None,
        }
        if self.logistic.is_fitted:
            feats = self.state.features(
                m.player_a, m.player_b, m.surface, self.as_of, m.rank_a, m.rank_b
            )
            proba = self.logistic.predict_proba(feats)[0]
            p_a = float(dict(zip(self.logistic.classes_.tolist(), proba, strict=True))[1])
            per_model["logistic"] = [p_a, 1 - p_a]

        serve = self._serve_inputs(m)
        if serve:
            md = match_distribution(*serve, m.best_of, final_tb)
            per_model["markov"] = [md.p_a_wins, 1 - md.p_a_wins]
            base_spw = (serve[0] + serve[1]) / 2
        else:
            notes.append(
                "Sin estadísticas de saque/resto suficientes: Markov usa nivel medio del circuito"
            )
            base_spw = mk["default_spw"][m.tour]
        if m.surface == "unknown":
            notes.append("Superficie desconocida: Elo de superficie = Elo general")

        weights = self.weights or self.cfg["ensemble_weights_winner"]
        ens = combine({k: v for k, v in per_model.items()}, weights)
        final = ens.probabilities
        if self.calibrator is not None:
            final = self.calibrator.transform(final.reshape(1, -1))[0]
        p_a_final = float(final[0])

        pa_srv, pb_srv = serve_probs_from_match_prob(p_a_final, base_spw, m.best_of, final_tb)
        dist = match_distribution(pa_srv, pb_srv, m.best_of, final_tb)
        markets = dist.to_dict(self.cfg["lines"]["games"])
        markets["winner"] = {"A": p_a_final, "B": 1 - p_a_final}

        n_min = min(self.elo.matches_played(m.player_a), self.elo.matches_played(m.player_b))
        conf = assess_confidence(
            min_matches_side=n_min,
            required_matches=max(self.cfg["elo"]["surface_min_matches"] * 2, 20),
            spread=ens.spread,
            models_missing=len(ens.models_missing),
            models_total=len(weights),
            cfg=self.confidence_cfg,
        )
        notes.extend(conf.notes)
        forecast = MatchForecast(
            sport="tennis",
            competition=f"{m.tour} · {m.tournament}",
            competition_key=m.tour.lower(),
            event_id=m.match_id,
            kickoff_utc=m.kickoff_utc,
            home_or_a=m.display_a,
            away_or_b=m.display_b,
            model_version=self.model_version,
            generated_at=now_utc(),
            as_of=self.as_of,
            markets=markets,
            per_model={k: ({"A": v[0], "B": v[1]} if v else None) for k, v in per_model.items()},
            confidence=conf.level.value,
            confidence_score=conf.score,
            data_notes=notes,
            context={
                "tour": m.tour,
                "surface": m.surface,
                "best_of": m.best_of,
                "final_set_tiebreak": final_tb,
                "elo": {"A": self.elo.rating(m.player_a), "B": self.elo.rating(m.player_b)},
                "surface_elo": {
                    "A": self.elo.effective_surface_rating(m.player_a, m.surface),
                    "B": self.elo.effective_surface_rating(m.player_b, m.surface),
                },
                "ranking": {"A": m.rank_a, "B": m.rank_b},
                "ensemble_weights": ens.weights_used,
                "models_missing": ens.models_missing,
                "model_spread": ens.spread,
                "matches_used": {
                    "A": self.elo.matches_played(m.player_a),
                    "B": self.elo.matches_played(m.player_b),
                },
            },
        )
        forecast.records = self._records(forecast)
        return forecast

    def _records(self, f: MatchForecast) -> list[PredictionRecord]:
        base = {
            "sport": "tennis",
            "competition": f.competition,
            "event_id": f.event_id,
            "generated_at": f.generated_at,
            "as_of": f.as_of,
            "confidence": f.confidence,
        }
        mk = f.markets
        recs = [
            PredictionRecord(
                **base,
                market="winner",
                event="player_a_win",
                probability=mk["winner"]["A"],
                model=self.model_version,
            ),
            PredictionRecord(
                **base,
                market="winner",
                event="player_b_win",
                probability=mk["winner"]["B"],
                model=self.model_version,
            ),
            PredictionRecord(
                **base,
                market="at_least_one_set",
                event="player_a",
                probability=mk["at_least_one_set"]["A"],
                model="markov",
            ),
            PredictionRecord(
                **base,
                market="at_least_one_set",
                event="player_b",
                probability=mk["at_least_one_set"]["B"],
                model="markov",
            ),
        ]
        for name, probs in f.per_model.items():
            if probs:
                recs.append(
                    PredictionRecord(
                        **base,
                        market="winner",
                        event="player_a_win",
                        probability=probs["A"],
                        model=name,
                    )
                )
        for line, p in mk["games_lines"].items():
            recs.append(
                PredictionRecord(
                    **base,
                    market="games_total",
                    event=f"over_{line}",
                    line=float(line),
                    probability=p["over"],
                    model="markov",
                )
            )
        return recs
