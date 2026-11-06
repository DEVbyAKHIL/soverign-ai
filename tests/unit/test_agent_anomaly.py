import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from agents.anomaly_detector import (
    ANOMALY_THRESHOLD,
    AnomalyDetector,
    IsolationForestScorer,
    StatisticalDetector,
    _avg_path_length,
)
from agents.features import FEATURE_NAMES, EventRow, compute_features
from agents.profile import dataset_of, destination_of
from agents.risk_score import updated_risk
from ml.data.agents import anomalous_window, baseline, normal_window, profile

NOW = datetime(2026, 11, 2, 14, 5, tzinfo=UTC)


def ev(minutes_ago: float, **over) -> EventRow:
    base = dict(
        created_at=NOW - timedelta(minutes=minutes_ago),
        tokens=300,
        sensitive=0,
        destination="api.model-eu.example",
        status="allow",
        datasets=("kb/support",),
        tool_calls=0,
        content_hash=f"h{minutes_ago}",
    )
    base.update(over)
    return EventRow(**base)


def steady_baseline(days: int = 3, per_bucket: int = 4) -> list[EventRow]:
    """Weekday-office agent: 4 requests per 10 minutes between 09:00 and 17:00."""
    out = []
    for d in range(1, days + 1):
        for minute in range(9 * 60, 17 * 60, 10):
            for k in range(per_bucket):
                ts = (NOW - timedelta(days=d)).replace(
                    hour=minute // 60, minute=minute % 60
                ) + timedelta(seconds=k)
                out.append(
                    EventRow(
                        ts,
                        300,
                        0,
                        "api.model-eu.example",
                        "allow",
                        ("kb/support",),
                        0,
                        f"b{d}{minute}{k}",
                    )
                )
    return out


@pytest.fixture(scope="module")
def det() -> AnomalyDetector:
    return AnomalyDetector()


# --- features ---


def test_feature_vector_order_and_values():
    base = steady_baseline()
    f = compute_features([ev(1), ev(2), ev(3), ev(4)], base)
    assert tuple(f.vector) == FEATURE_NAMES
    assert f.baseline_sufficient and f.window_requests == 4
    assert abs(f.vector["request_rate_log_ratio"]) < 1e-6  # same rate as baseline
    assert f.vector["new_destinations"] == 0 and f.vector["off_hours_ratio"] == 0


def test_features_capture_each_behaviour():
    base = steady_baseline()
    window = [
        ev(
            1,
            destination="upload.drop-share.example",
            sensitive=4,
            tool_calls=3,
            status="deny",
            datasets=("kb/payroll", "kb/board", "kb/security"),
            content_hash="same",
        ),
        ev(2, content_hash="same", status="deny"),
        ev(3, content_hash="same", status="deny"),
    ]
    v = compute_features(window, base).vector
    assert v["new_destinations"] == 1
    assert v["new_datasets"] == 3
    assert v["denied_ratio"] == 1.0
    assert v["retry_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert v["sensitive_access_delta"] > 1
    assert v["tool_calls_delta"] == 1.0


def test_off_hours_detected_against_baseline_hours():
    base = steady_baseline()
    night = [
        EventRow(
            NOW.replace(hour=3) - timedelta(minutes=i), 300, 0, "api.model-eu.example", "allow"
        )
        for i in range(5)
    ]
    assert compute_features(night, base).vector["off_hours_ratio"] == 1.0


def test_empty_inputs_are_safe():
    f = compute_features([], [])
    assert not f.baseline_sufficient and all(v == 0 for v in f.vector.values())


# --- statistical rules ---


@pytest.mark.parametrize(
    "window,reason",
    [
        ([ev(i * 0.2) for i in range(40)], "unusual_request_rate"),
        ([ev(1, tokens=60_000), ev(2), ev(3)], "unusual_data_volume"),
        ([ev(1, destination="api.unknown.example")], "new_external_destination"),
        ([ev(i, datasets=(f"kb/x{i}",)) for i in range(5)], "unusual_dataset_access"),
        ([ev(i, sensitive=6) for i in range(4)], "high_sensitive_field_access"),
        ([ev(i, tool_calls=5) for i in range(4)], "unusual_tool_usage"),
        ([ev(i, status="deny") for i in range(5)], "elevated_denials"),
        ([ev(i, content_hash="same") for i in range(6)], "high_retry_rate"),
    ],
)
def test_each_reason_flags_on_its_own(det, window, reason):
    r = det.evaluate("agent-1", compute_features(window, steady_baseline()), max_rpm=60)
    assert reason in r.reasons
    assert r.is_anomalous and r.anomaly_score >= ANOMALY_THRESHOLD


def test_normal_window_not_anomalous(det):
    r = det.evaluate(
        "agent-1", compute_features([ev(1), ev(3), ev(6), ev(8)], steady_baseline()), 60
    )
    assert not r.is_anomalous and r.reasons == () and r.method == "isolation_forest+stats"


def test_rate_limit_reason():
    f = compute_features([ev(i * 0.1) for i in range(30)], steady_baseline())
    names = [r.name for r in StatisticalDetector().reasons(f, max_rpm=2)]
    assert "rate_limit_exceeded" in names


def test_cold_start_uses_absolute_rules_only(det):
    r = det.evaluate("new", compute_features([ev(1), ev(2)], [ev(30)]), 60)
    assert r.method == "insufficient_baseline" and not r.is_anomalous
    assert "insufficient_baseline" in r.reasons
    storm = det.evaluate("new", compute_features([ev(i, status="deny") for i in range(6)], []), 60)
    assert storm.is_anomalous and "elevated_denials" in storm.reasons


def test_stats_only_when_model_missing():
    d = AnomalyDetector(model_path=None)
    r = d.evaluate("a", compute_features([ev(1)], steady_baseline()), 60)
    assert r.method == "stats_only" and d.version.startswith("agent-stats")


def test_result_shape_matches_spec(det):
    r = det.evaluate(
        "agent_123", compute_features([ev(1, destination="x.example")], steady_baseline()), 60
    )
    d = r.as_dict()
    assert {"agent_id", "anomaly_score", "is_anomalous", "reasons"} <= set(d)
    assert d["agent_id"] == "agent_123" and isinstance(d["reasons"], list)


# --- isolation forest ---


def test_forest_parity_with_sklearn():
    pytest.importorskip("sklearn")
    from sklearn.ensemble import IsolationForest

    scorer = IsolationForestScorer()
    rng = np.random.default_rng(0)
    x_train = rng.normal(size=(400, len(FEATURE_NAMES)))
    forest = IsolationForest(n_estimators=20, max_samples=128, random_state=1).fit(x_train)
    from ml.training.train_anomaly_detector import export

    scorer.trees, scorer.max_samples = export(forest), int(forest.max_samples_)
    x_test = rng.normal(scale=2.0, size=(50, len(FEATURE_NAMES)))
    expected = -forest.score_samples(x_test)
    got = [scorer.raw_score(list(map(float, row))) for row in x_test]
    assert max(abs(a - b) for a, b in zip(expected, got, strict=True)) < 1e-9


def test_forest_calibration_and_metadata():
    s = IsolationForestScorer()
    assert s.calibration["threshold_quantile"] == 0.995
    assert s.score([0.0] * len(FEATURE_NAMES)) < ANOMALY_THRESHOLD


def test_avg_path_length():
    assert _avg_path_length(1) == 0 and _avg_path_length(2) == 1
    assert _avg_path_length(256) == pytest.approx(10.24, abs=0.01)


# --- simulated agents (smoke) ---------------------------------------------------------------------


def test_simulated_normal_mostly_quiet_and_strong_anomalies_flagged(det):
    rng = random.Random(123)
    fp = hits = n_anom = n_norm = 0
    for _ in range(8):
        p = profile(rng)
        base = baseline(rng, p)
        rpm = max(10, int(p.rate * 3))
        for _ in range(10):
            n_norm += 1
            fp += det.evaluate("a", compute_features(normal_window(rng, p), base), rpm).is_anomalous
        for kind in ("burst", "exfiltration", "new_destination", "data_volume"):
            n_anom += 1
            hits += det.evaluate(
                "a", compute_features(anomalous_window(rng, p, kind, True), base), rpm
            ).is_anomalous
    assert fp / n_norm <= 0.05
    assert hits / n_anom >= 0.9


# --- helpers ---


def test_destination_and_dataset_normalisation():
    assert destination_of("https://API.Example.com/v1/chat") == "api.example.com"
    assert destination_of("internal-model") == "internal-model"
    assert dataset_of("https://forum.example/t/1") == "forum.example"
    assert dataset_of("kb/hr/policy.md") == "kb/hr"
    assert dataset_of("readme") == "readme"


def test_risk_ewma():
    assert updated_risk(0.0, 1.0) == 0.2
    r = 0.0
    for _ in range(20):
        r = updated_risk(r, 0.9)
    assert r == pytest.approx(0.9 * (1 - 0.8**20), abs=1e-3)  # converges towards 0.9
    assert updated_risk(0.9, 0.0) == 0.72
