"""Evaluate agent anomaly detection on held-out simulated agents.

    python -m ml.evaluation.evaluate_anomaly [--out PATH]

Compares the Isolation Forest alone, the statistical rules alone and the combined detector used in
production. Reports false-positive rate on normal windows, detection rate per anomaly type and
severity, ROC-AUC, the explanation quality (is the right reason named?) and a cold-start check.
"""

import argparse
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from sklearn.metrics import roc_auc_score

from agents.anomaly_detector import ANOMALY_THRESHOLD, AnomalyDetector
from agents.features import compute_features
from ml.data.agents import (
    anomalous_window,
    baseline,
    dataset,
    normal_window,
    profile,
)

OUT = Path(__file__).resolve().parent / "results" / "anomaly_eval.json"

# Which reason a correct explanation should name for each anomaly type.
EXPECTED_REASON = {
    "burst": {"unusual_request_rate", "rate_limit_exceeded"},
    "data_volume": {"unusual_data_volume"},
    "exfiltration": {"high_sensitive_field_access", "new_external_destination"},
    "new_destination": {"new_external_destination"},
    "off_hours": {"off_hours_activity"},
    "denial_storm": {"elevated_denials"},
    "dataset_sweep": {"unusual_dataset_access"},
    "tool_abuse": {"unusual_tool_usage"},
    "retry_loop": {"high_retry_rate"},
}


def evaluate() -> dict:
    det = AnomalyDetector()
    stats_only = AnomalyDetector(model_path=None)
    samples = dataset(60, 30, 36, seed=99)  # held-out agents (training used seed 10)

    rows = []
    latencies = []
    for s in samples:
        start = time.perf_counter()
        combined = det.evaluate("a", s.feature_obj, s.max_rpm)
        latencies.append((time.perf_counter() - start) * 1000)
        rows.append(
            {
                "label": s.label,
                "strong": s.strong,
                "forest": det.forest.score(s.features),
                "stats": stats_only.evaluate("a", s.feature_obj, s.max_rpm).anomaly_score,
                "combined": combined.anomaly_score,
                "flag": combined.is_anomalous,
                "reasons": set(combined.reasons),
            }
        )

    y = [int(r["label"] != "normal") for r in rows]
    normals = [r for r in rows if r["label"] == "normal"]
    result = {"model_version": det.version, "windows": len(rows), "methods": {}}
    for m in ("forest", "stats", "combined"):
        flagged = [r[m] >= ANOMALY_THRESHOLD for r in rows]
        per = defaultdict(lambda: [0, 0])
        for r, f in zip(rows, flagged, strict=True):
            if r["label"] != "normal":
                key = f"{r['label']}/{'strong' if r['strong'] else 'subtle'}"
                per[key][0] += f
                per[key][1] += 1
        strong = [
            f for r, f in zip(rows, flagged, strict=True) if r["label"] != "normal" and r["strong"]
        ]
        subtle = [
            f
            for r, f in zip(rows, flagged, strict=True)
            if r["label"] != "normal" and not r["strong"]
        ]
        result["methods"][m] = {
            "false_positive_rate": round(
                sum(r[m] >= ANOMALY_THRESHOLD for r in normals) / len(normals), 4
            ),
            "detection_rate_strong": round(sum(strong) / len(strong), 4),
            "detection_rate_subtle": round(sum(subtle) / len(subtle), 4),
            "roc_auc": round(roc_auc_score(y, [r[m] for r in rows]), 4),
            "by_type": {k: round(a / b, 4) for k, (a, b) in sorted(per.items())},
        }

    # Explanation quality: among flagged strong anomalies, how often is the expected reason named?
    named = [
        bool(r["reasons"] & EXPECTED_REASON[r["label"]])
        for r in rows
        if r["label"] != "normal" and r["strong"] and r["flag"]
    ]
    result["explanations"] = {
        "flagged_strong_with_expected_reason": round(sum(named) / len(named), 4) if named else None
    }

    # Cold start: agents with < 1 day of history fall back to absolute rules.
    rng = random.Random(7)  # noqa: S311
    cold = defaultdict(lambda: [0, 0])
    for _ in range(40):
        p = profile(rng)
        base = baseline(rng, p, days=1)[:20]
        for kind, window in (
            ("normal", normal_window(rng, p)),
            ("denial_storm", anomalous_window(rng, p, "denial_storm", True)),
            ("retry_loop", anomalous_window(rng, p, "retry_loop", True)),
        ):
            r = det.evaluate("a", compute_features(window, base), max(10, int(p.rate * 3)))
            cold[kind][0] += r.is_anomalous
            cold[kind][1] += 1
    result["cold_start_flag_rate"] = {k: round(a / b, 4) for k, (a, b) in cold.items()}
    result["latency_ms"] = {
        "p50": round(statistics.median(latencies), 3),
        "p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 3),
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    text = json.dumps(evaluate(), indent=2)
    print(text)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    except OSError:
        pass
