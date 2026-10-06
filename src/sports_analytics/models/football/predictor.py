"""Orquestación de modelos de fútbol para un instante ``as_of``.

fit():  Elo + features (recorrido cronológico), Poisson, Dixon-Coles, córners y
        logística, todos con partidos estrictamente anteriores a ``as_of``.
predict(): 1X2 por modelo -> ensemble -> calibración (si se provee) -> mercados
        de goles (matriz Dixon-Coles) y córners -> confianza.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sports_analytics.core.timeutils import ensure_utc, now_utc
from sports_analytics.data.schemas import FootballMatch
from sports_analytics.features import football as ff
from sports_analytics.models.calibration import ProbabilityCalibrator
from sports_analytics.models.confidence import assess_confidence
from sports_analytics.models.ensemble import combine
from sports_analytics.models.football.corners import CornersModel
from sports_analytics.models.football.dixon_coles import DixonColesModel
from sports_analytics.models.football.elo import FootballElo
from sports_analytics.models.football.markets import one_x_two, summarize_matrix
from sports_analytics.models.football.poisson import PoissonGoalsModel
from sports_analytics.models.logistic import ProbabilisticLogit
from sports_analytics.models.outputs import MatchForecast, PredictionRecord

OUTCOMES = ("home", "draw", "away")


@dataclass
class FootballPredictor:
    cfg: dict  # sección football de models.yaml
    recency_cfg: dict
    confidence_cfg: dict
    model_version: str = "ensemble_v1"
    weights: dict[str, float] | None = None  # pesos calibrados (si existen)
    calibrator: ProbabilityCalibrator | None = None
    as_of: datetime | None = None
    elo: FootballElo = field(init=False)
    poisson: PoissonGoalsModel = field(init=False)
    dixon_coles: DixonColesModel = field(init=False)
    corners: CornersModel = field(init=False)
    logistic: ProbabilisticLogit = field(init=False)
    feature_state: ff.FootballFeatureState | None = field(init=False, default=None)

    def fit(self, history: Sequence[FootballMatch], as_of: datetime) -> FootballPredictor:
        self.as_of = ensure_utc(as_of)
        self.elo = FootballElo.from_config(self.cfg["elo"])
        x, y, self.feature_state = ff.build_training_set(
            history,
            self.as_of,
            self.elo,
            self.recency_cfg["half_life_days"]["football"],
            self.cfg["logistic"]["min_history"],
        )
        self.poisson = PoissonGoalsModel.from_config(self.cfg["poisson"]).fit(
            history, self.as_of, self.recency_cfg
        )
        self.dixon_coles = DixonColesModel.from_config(self.poisson, self.cfg["dixon_coles"])
        self.dixon_coles.fit_rho(history, self.as_of, self.recency_cfg)
        self.corners = CornersModel.from_config(
            self.cfg["corners"], self.cfg["poisson"]["prior_strength"]
        ).fit(history, self.as_of, self.recency_cfg)
        lcfg = self.cfg["logistic"]
        self.logistic = ProbabilisticLogit(
            ff.FEATURE_NAMES, min_samples=lcfg["min_samples"], c=lcfg["C"]
        ).fit(x, y)
        return self

    # ------------------------------------------------------------------ helpers

    def _logistic_1x2(self, match: FootballMatch) -> list[float] | None:
        if not self.logistic.is_fitted or self.feature_state is None:
            return None
        feats = self.feature_state.features(
            match.home_team, match.away_team, self.as_of, match.neutral_venue
        )
        if np.isnan(feats).any():
            return None
        proba = self.logistic.predict_proba(feats)[0]
        by_class = dict(zip(self.logistic.classes_.tolist(), proba, strict=True))
        return [float(by_class.get(ff.OUTCOME_INDEX[o], 0.0)) for o in OUTCOMES]

    def predict(self, match: FootballMatch, competition_name: str) -> MatchForecast:
        if self.as_of is None:
            raise RuntimeError("Llama a fit() antes de predict()")
        if match.kickoff_utc < self.as_of:
            # Un partido ya iniciado no se predice con este modelo (evita mirar el resultado)
            raise ValueError("El partido comenzó antes de as_of; no se puede predecir sin leakage")
        home, away, neutral = match.home_team, match.away_team, match.neutral_venue
        notes: list[str] = []

        poisson_ok = self.poisson.has_enough_data(home) and self.poisson.has_enough_data(away)
        if not poisson_ok:
            notes.append("Poisson/Dixon-Coles con pocos partidos de algún equipo")

        dc_matrix = self.dixon_coles.predict_matrix(home, away, neutral)
        poisson_matrix = self.poisson.predict_matrix(home, away, neutral)
        per_model = {
            "dixon_coles": list(one_x_two(dc_matrix).values()) if poisson_ok else None,
            "poisson": list(one_x_two(poisson_matrix).values()) if poisson_ok else None,
            "elo": list(self.elo.predict_1x2(home, away, neutral).values()),
            "logistic": self._logistic_1x2(match),
        }
        weights = self.weights or self.cfg["ensemble_weights_1x2"]
        ens = combine(per_model, weights)
        final = ens.probabilities
        if self.calibrator is not None:
            final = self.calibrator.transform(final.reshape(1, -1))[0]

        goals = summarize_matrix(dc_matrix, self.cfg["lines"]["goals"])
        goals["1x2"] = dict(zip(OUTCOMES, map(float, final), strict=True))
        markets = {
            "1x2": goals.pop("1x2"),
            "goals": goals,
        }
        if self.corners.has_enough_data(home) and self.corners.has_enough_data(away):
            markets["corners"] = self.corners.predict(home, away, self.cfg["lines"]["corners"])
        else:
            markets["corners"] = None
            notes.append("Córners: datos insuficientes")

        n_min = min(self.poisson.strength(home).n_matches, self.poisson.strength(away).n_matches)
        conf = assess_confidence(
            min_matches_side=n_min,
            required_matches=max(self.cfg["poisson"]["min_matches"] * 2, 10),
            spread=ens.spread,
            models_missing=len(ens.models_missing),
            models_total=len(weights),
            cfg=self.confidence_cfg,
        )
        notes.extend(conf.notes)
        generated = now_utc()
        forecast = MatchForecast(
            sport="football",
            competition=competition_name,
            competition_key=match.competition_key,
            event_id=match.match_id,
            kickoff_utc=match.kickoff_utc,
            home_or_a=home,
            away_or_b=away,
            model_version=self.model_version,
            generated_at=generated,
            as_of=self.as_of,
            markets=markets,
            per_model={
                k: (dict(zip(OUTCOMES, v, strict=True)) if v else None)
                for k, v in per_model.items()
            },
            confidence=conf.level.value,
            confidence_score=conf.score,
            data_notes=notes,
            context={
                "elo": {"home": self.elo.rating(home), "away": self.elo.rating(away)},
                "lambda": dict(
                    zip(
                        ("home", "away"),
                        self.poisson.expected_goals(home, away, neutral),
                        strict=True,
                    )
                ),
                "dixon_coles_rho": self.dixon_coles.rho,
                "rho_estimated": self.dixon_coles.rho_estimated,
                "ensemble_weights": ens.weights_used,
                "models_missing": ens.models_missing,
                "model_spread": ens.spread,
                "matches_used": {
                    "home": self.poisson.strength(home).n_matches,
                    "away": self.poisson.strength(away).n_matches,
                },
            },
        )
        forecast.records = self._records(forecast)
        return forecast

    def _records(self, f: MatchForecast) -> list[PredictionRecord]:
        base = {
            "sport": "football",
            "competition": f.competition,
            "event_id": f.event_id,
            "generated_at": f.generated_at,
            "as_of": f.as_of,
            "confidence": f.confidence,
        }
        recs = [
            PredictionRecord(
                **base,
                market="1x2",
                event=f"{o}_win" if o != "draw" else "draw",
                probability=p,
                model=self.model_version,
            )
            for o, p in f.markets["1x2"].items()
        ]
        for name, probs in f.per_model.items():
            if probs:
                recs += [
                    PredictionRecord(
                        **base,
                        market="1x2",
                        event=f"{o}_win" if o != "draw" else "draw",
                        probability=p,
                        model=name,
                    )
                    for o, p in probs.items()
                ]
        for line, p in f.markets["goals"]["goal_lines"].items():
            recs.append(
                PredictionRecord(
                    **base,
                    market="goals_total",
                    event=f"over_{line}",
                    line=float(line),
                    probability=p["over"],
                    model="dixon_coles",
                )
            )
        if f.markets.get("corners"):
            for line, p in f.markets["corners"]["lines"].items():
                recs.append(
                    PredictionRecord(
                        **base,
                        market="corners_total",
                        event=f"over_{line}",
                        line=float(line),
                        probability=p["over"],
                        model=f"corners_{f.markets['corners']['distribution_family']}",
                    )
                )
        return recs
