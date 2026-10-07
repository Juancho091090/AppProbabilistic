from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sports_analytics.features.recency import bucket_weights, exponential_weights, recency_weights
from sports_analytics.models.calibration import (
    ProbabilityCalibrator,
    accuracy,
    all_metrics,
    brier_score,
    calibration_curve,
    expected_calibration_error,
    log_loss,
)
from sports_analytics.models.confidence import ConfidenceLevel, assess_confidence
from sports_analytics.models.ensemble import combine, fit_weights
from sports_analytics.models.logistic import ProbabilisticLogit

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)

# ----------------------------------------------------------------- recencia


def test_exponential_weights_half_life():
    dates = [NOW - timedelta(days=d) for d in (0, 30, 60)]
    w = exponential_weights(dates, NOW, half_life_days=30)
    assert w == pytest.approx([1.0, 0.5, 0.25])


def test_recency_rejects_future_dates():
    with pytest.raises(ValueError, match="leakage"):
        exponential_weights([NOW + timedelta(days=1)], NOW, 30)


def test_bucket_weights(app_config):
    dates = [NOW - timedelta(days=d) for d in range(25)]
    w = bucket_weights(dates, app_config.models.recency["buckets"])
    assert w[0] == 1.0 and w[7] == 0.75 and w[15] == 0.5 and w[24] == 0.25


def test_recency_dispatch(app_config):
    dates = [NOW - timedelta(days=180)]
    w = recency_weights(dates, NOW, app_config.models.recency, "football")
    assert w[0] == pytest.approx(0.5)


# ----------------------------------------------------------------- ensemble


def test_combine_weighted_average():
    r = combine({"a": [0.6, 0.4], "b": [0.4, 0.6]}, {"a": 0.75, "b": 0.25})
    assert r.probabilities == pytest.approx([0.55, 0.45])
    assert r.spread == pytest.approx(0.2)


def test_combine_renormalizes_missing():
    r = combine({"a": [0.7, 0.3], "b": None}, {"a": 0.4, "b": 0.6})
    assert r.probabilities == pytest.approx([0.7, 0.3])
    assert r.weights_used == {"a": 1.0} and r.models_missing == ["b"]


def test_combine_rejects_invalid_distribution():
    with pytest.raises(ValueError):
        combine({"a": [0.7, 0.7]}, {"a": 1.0})


def test_fit_weights_prefers_informative_model():
    rng = np.random.default_rng(0)
    n = 3000
    p_true = rng.uniform(0.1, 0.9, n)
    y = (rng.random(n) < p_true).astype(int)
    good = np.column_stack([1 - p_true, p_true])
    noise = np.full((n, 2), 0.5)
    times = [NOW - timedelta(hours=i + 1) for i in range(n)]
    w = fit_weights({"good": good, "noise": noise}, y, event_times=times, as_of=NOW)
    assert w["good"] > 0.9
    assert sum(w.values()) == pytest.approx(1.0)


def test_fit_weights_rejects_leakage():
    with pytest.raises(ValueError, match="leakage"):
        fit_weights(
            {"a": np.full((1, 2), 0.5)},
            np.array([1]),
            event_times=[NOW + timedelta(hours=1)],
            as_of=NOW,
            min_samples=1,
        )


def test_fit_weights_fallback_with_few_samples():
    w = fit_weights(
        {"a": np.full((5, 2), 0.5)},
        np.ones(5, int),
        event_times=[NOW - timedelta(hours=1)] * 5,
        as_of=NOW,
        min_samples=200,
        fallback={"a": 1.0},
    )
    assert w == {"a": 1.0}


# ---------------------------------------------------------------- métricas


def test_metrics_known_values():
    p = np.array([[0.8, 0.2], [0.3, 0.7]])
    y = np.array([0, 1])
    assert brier_score(p, y) == pytest.approx(((0.2**2) + (0.3**2)) / 2)
    assert log_loss(p, y) == pytest.approx(-(np.log(0.8) + np.log(0.7)) / 2)
    assert accuracy(p, y) == 1.0


def test_multiclass_brier_perfect_is_zero():
    p = np.eye(3)
    assert brier_score(p, np.array([0, 1, 2])) == 0.0


def test_ece_calibrated_vs_miscalibrated():
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 50000)
    y = (rng.random(50000) < p).astype(int)
    assert expected_calibration_error(p, y) < 0.01
    assert expected_calibration_error(np.clip(p * 1.3, 0, 1), y) > 0.05
    bins = calibration_curve(p, y, 10)
    assert len(bins) == 10 and sum(b.count for b in bins) == 50000


def test_all_metrics_keys():
    m = all_metrics(np.array([0.6, 0.4]), np.array([1, 0]))
    assert set(m) == {"n", "brier", "log_loss", "accuracy", "ece"}


# -------------------------------------------------------------- calibrador


def _miscalibrated(n, seed=2):
    rng = np.random.default_rng(seed)
    p_true = rng.uniform(0.05, 0.95, n)
    y = (rng.random(n) < p_true).astype(int)
    # Modelo sobreconfiado: empuja las probabilidades hacia los extremos
    logit = np.log(p_true / (1 - p_true)) * 2.0
    p_model = 1 / (1 + np.exp(-logit))
    return np.column_stack([1 - p_model, p_model]), y


@pytest.mark.parametrize("n,method", [(100, "identity"), (500, "platt"), (5000, "isotonic")])
def test_calibrator_method_by_sample_size(n, method):
    p, y = _miscalibrated(n)
    times = [NOW - timedelta(hours=i + 1) for i in range(n)]
    cal = ProbabilityCalibrator(200, 1000).fit(p, y, event_times=times, as_of=NOW)
    assert cal.method == method


def test_calibration_improves_log_loss_out_of_sample():
    p, y = _miscalibrated(6000)
    times = [NOW - timedelta(hours=i + 1) for i in range(4000)]
    cal = ProbabilityCalibrator(200, 10**9).fit(p[:4000], y[:4000], event_times=times, as_of=NOW)
    before = log_loss(p[4000:], y[4000:])
    after = log_loss(cal.transform(p[4000:]), y[4000:])
    assert after < before
    assert cal.transform(p[4000:]).sum(axis=1) == pytest.approx(1.0)


def test_calibrator_ignores_future_rows():
    p, y = _miscalibrated(600)
    times = [NOW - timedelta(hours=1)] * 300 + [NOW + timedelta(hours=1)] * 300
    cal = ProbabilityCalibrator(200, 1000).fit(p, y, event_times=times, as_of=NOW)
    assert cal.n_train == 300


# -------------------------------------------------------------- logística


def test_logistic_learns_and_requires_min_samples():
    rng = np.random.default_rng(4)
    x = rng.normal(size=(1000, 2))
    y = (rng.random(1000) < 1 / (1 + np.exp(-2 * x[:, 0]))).astype(int)
    model = ProbabilisticLogit(["a", "b"], min_samples=300).fit(x, y)
    assert model.is_fitted
    assert model.predict_proba([2.0, 0.0])[0, 1] > 0.9
    assert abs(model.coefficients()["a"][0]) > abs(model.coefficients()["b"][0])
    small = ProbabilisticLogit(["a", "b"], min_samples=5000).fit(x, y)
    assert not small.is_fitted
    with pytest.raises(RuntimeError):
        small.predict_proba([0, 0])


# -------------------------------------------------------------- confianza


def test_confidence_levels(app_config):
    cfg = app_config.models.confidence
    high = assess_confidence(
        min_matches_side=40,
        required_matches=20,
        spread=0.02,
        models_missing=0,
        models_total=4,
        cfg=cfg,
    )
    low = assess_confidence(
        min_matches_side=2,
        required_matches=20,
        spread=0.30,
        models_missing=2,
        models_total=4,
        cfg=cfg,
    )
    assert high.level == ConfidenceLevel.HIGH
    assert low.level == ConfidenceLevel.LOW
    assert low.notes
    assert ConfidenceLevel.MEDIUM.label_es == "Media"


def test_confidence_favorite_probability_method():
    from sports_analytics.models.confidence import assess

    cfg = {
        "method": "favorite_probability",
        "favorite_thresholds": {"football": {"high": 0.60, "medium": 0.45}},
        "min_data_quality": 0.6,
        "high": 0.70,
        "medium": 0.45,
        "max_model_spread": 0.15,
    }
    common = dict(sport="football", required_matches=10, models_missing=0, models_total=4, cfg=cfg)
    hi = assess(favorite_probability=0.65, min_matches_side=20, spread=0.05, **common)
    mid = assess(favorite_probability=0.50, min_matches_side=20, spread=0.05, **common)
    lo = assess(favorite_probability=0.40, min_matches_side=20, spread=0.05, **common)
    assert [c.level for c in (hi, mid, lo)] == [
        ConfidenceLevel.HIGH,
        ConfidenceLevel.MEDIUM,
        ConfidenceLevel.LOW,
    ]
    assert hi.score == 0.65
    # Poco histórico o desacuerdo: baja un nivel (no por debajo de Baja)
    thin = assess(favorite_probability=0.65, min_matches_side=3, spread=0.05, **common)
    split = assess(favorite_probability=0.65, min_matches_side=20, spread=0.30, **common)
    assert thin.level == split.level == ConfidenceLevel.MEDIUM
    assert any("histórico" in n for n in thin.notes)
    assert (
        assess(favorite_probability=0.40, min_matches_side=3, spread=0.3, **common).level
        == ConfidenceLevel.LOW
    )
    # Método anterior intacto
    old = assess(
        favorite_probability=0.9,
        min_matches_side=20,
        spread=0.0,
        **{**common, "cfg": {**cfg, "method": "data_agreement"}},
    )
    assert old.level == ConfidenceLevel.HIGH and old.score == 1.0
