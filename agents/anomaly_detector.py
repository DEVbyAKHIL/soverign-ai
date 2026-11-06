"""Agent behaviour anomaly detection.

* IsolationForestScorer: pure-Python scoring of a scikit-learn IsolationForest exported to JSON
  (`ml/training/train_anomaly_detector.py`). Parity with `score_samples` is tested.
* StatisticalDetector: z-score / rolling-percentile rules against the agent's own baseline. It
  always runs: it names the *reasons*, and it is the only method when the baseline is too short
  or the model is unavailable.
* AnomalyDetector: combines both into one explainable result.
"""

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from agents.features import FEATURE_NAMES, AgentFeatures

DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "isolation_forest_v1.json"
ANOMALY_THRESHOLD = 0.70
EULER_GAMMA = 0.5772156649015329


def _f32(x: float) -> float:
    """scikit-learn trees compare float32 inputs; emulate that for exact parity."""
    return struct.unpack("f", struct.pack("f", x))[0]


def _avg_path_length(n: int) -> float:
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1) + EULER_GAMMA) - 2.0 * (n - 1) / n


class IsolationForestScorer:
    def __init__(self, path: Path = DEFAULT_MODEL) -> None:
        model = json.loads(path.read_text(encoding="utf-8"))
        if model.get("format") != "isolation-forest-v1":
            raise ValueError(f"Unsupported model format: {model.get('format')}")
        if tuple(model["features"]) != FEATURE_NAMES:
            raise ValueError("Model feature order does not match agents.features.FEATURE_NAMES")
        self.version: str = model["version"]
        self.trees: list[dict] = model["trees"]
        self.max_samples: int = model["max_samples"]
        self.calibration: dict[str, float] = model["calibration"]
        self.metrics: dict = model.get("metrics", {})

    def raw_score(self, x: list[float]) -> float:
        """Equivalent to -IsolationForest.score_samples(x): ~0.5 normal, -> 1 anomalous."""
        x32 = [_f32(v) for v in x]
        total = 0.0
        for t in self.trees:
            left, right, feat, thr, n = t["l"], t["r"], t["f"], t["t"], t["n"]
            fmap = t["fmap"]
            node, depth = 0, 0
            while left[node] != -1:
                node = left[node] if x32[fmap[feat[node]]] <= thr[node] else right[node]
                depth += 1
            total += depth + _avg_path_length(n[node])
        mean = total / len(self.trees)
        return 2.0 ** (-mean / _avg_path_length(self.max_samples))

    def score(self, x: list[float]) -> float:
        """Calibrated to [0, 1]: the normal-window threshold quantile maps to ANOMALY_THRESHOLD."""
        s = self.raw_score(x)
        median, thr = self.calibration["median"], self.calibration["threshold"]
        if s <= thr:
            return round(max(0.0, ANOMALY_THRESHOLD * (s - median) / (thr - median)), 4)
        return round(
            min(1.0, ANOMALY_THRESHOLD + (1 - ANOMALY_THRESHOLD) * (s - thr) / (1 - thr)), 4
        )


# --- statistical rules ---------------------------------------------------------------------------

Z = 3.0
FLOOR_STD = 1.0  # avoid huge z-scores for agents with near-constant baselines


@dataclass(frozen=True)
class Reason:
    name: str
    weight: float  # contribution to the statistical score at full strength
    strength: float  # 0..1


def _z(value: float, mean: float, std: float) -> float:
    return (value - mean) / max(std, FLOOR_STD, 0.25 * mean)


def _ramp(value: float, start: float, full: float) -> float:
    """0 below `start`, rising linearly to 1 at `full`."""
    return max(0.0, min(1.0, (value - start) / (full - start))) if full > start else 1.0


# (weight, rule parameters). Every reason's weight is >= ANOMALY_THRESHOLD, so one reason at full
# strength is enough to flag an agent on its own; weaker evidence needs several reasons together.
# Tuned on a validation population (seed 55) separate from the reported evaluation (seed 99).
RULES = {
    "rate_limit_exceeded": 0.90,
    "unusual_request_rate": 0.80,
    "unusual_data_volume": 0.85,
    "new_external_destination": 0.80,
    "unusual_dataset_access": 0.80,
    "off_hours_activity": 0.75,
    "high_sensitive_field_access": 0.85,
    "unusual_tool_usage": 0.75,
    "elevated_denials": 0.80,
    "high_retry_rate": 0.75,
}


class StatisticalDetector:
    version = "agent-stats-1.1.0"

    def reasons(self, f: AgentFeatures, max_rpm: int) -> list[Reason]:
        v = f.vector
        n = f.window_requests
        found: dict[str, float] = {}

        def add(name: str, strength: float) -> None:
            if strength > 0:
                found[name] = max(found.get(name, 0.0), strength)

        if f.requests_per_minute > max_rpm:
            add("rate_limit_exceeded", 1.0)
        if f.baseline_sufficient:
            zr = _z(n, f.requests_stats.mean, f.requests_stats.std)
            if n > f.requests_stats.p95:
                add("unusual_request_rate", _ramp(zr, Z, 1.5 * Z))
            zt = _z(f.window_tokens, f.tokens_stats.mean, f.tokens_stats.std)
            if f.window_tokens > f.tokens_stats.p95:
                add("unusual_data_volume", _ramp(zt, Z, 1.5 * Z))
            if v["new_destinations"] >= 1:  # never seen in 7 days: always worth a look
                add("new_external_destination", 1.0)
            if v["new_datasets"] >= 3:
                add("unusual_dataset_access", _ramp(v["new_datasets"], 2, 5))
            if n >= 3:
                add("off_hours_activity", _ramp(v["off_hours_ratio"], 0.4, 0.8))
            add("high_sensitive_field_access", _ramp(v["sensitive_access_delta"], 0.75, 2.0))
            add("unusual_tool_usage", _ramp(v["tool_calls_delta"], 1.5, 3.5))
        else:
            # Without a baseline only absolute rules are meaningful.
            add("high_sensitive_field_access", _ramp(v["sensitive_per_request"], 2.5, 5.0))
        if n >= 3 and f.window_denied >= 3:
            add("elevated_denials", _ramp(v["denied_ratio"], 0.25, 0.5))
        if n >= 5:
            add("high_retry_rate", _ramp(v["retry_rate"], 0.3, 0.6))
        return [Reason(name, RULES[name], s) for name, s in found.items()]

    @staticmethod
    def score(reasons: list[Reason]) -> float:
        remaining = 1.0
        for r in reasons:
            remaining *= 1 - r.weight * r.strength
        return round(1 - remaining, 4)


# --- combined -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyResult:
    agent_id: str
    anomaly_score: float
    is_anomalous: bool
    reasons: tuple[str, ...]
    method: str  # isolation_forest+stats | stats_only | insufficient_baseline
    model_version: str
    features: dict[str, float] = field(default_factory=dict)
    baseline_events: int = 0
    requests_per_minute: float = 0.0

    def as_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "anomaly_score": self.anomaly_score,
            "is_anomalous": self.is_anomalous,
            "reasons": list(self.reasons),
            "method": self.method,
            "model_version": self.model_version,
            "features": self.features,
            "baseline_events": self.baseline_events,
            "requests_per_minute": self.requests_per_minute,
        }


class AnomalyDetector:
    def __init__(self, model_path: Path | None = DEFAULT_MODEL) -> None:
        self.stats = StatisticalDetector()
        self.forest: IsolationForestScorer | None = None
        if model_path is not None and model_path.exists():
            self.forest = IsolationForestScorer(model_path)

    @property
    def version(self) -> str:
        return f"{self.forest.version}+{self.stats.version}" if self.forest else self.stats.version

    def evaluate(self, agent_id: str, f: AgentFeatures, max_rpm: int) -> AnomalyResult:
        reasons = self.stats.reasons(f, max_rpm)
        stat_score = self.stats.score(reasons)
        names = [r.name for r in sorted(reasons, key=lambda r: -r.weight * r.strength)]

        if not f.baseline_sufficient:
            method, score = "insufficient_baseline", stat_score
            names.append("insufficient_baseline")
        elif self.forest is None:
            method, score = "stats_only", stat_score
        else:
            method = "isolation_forest+stats"
            forest_score = self.forest.score(f.as_list())
            score = max(forest_score, stat_score)
            if forest_score >= ANOMALY_THRESHOLD and not reasons:
                names.append("multivariate_outlier")

        is_anomalous = (
            score >= ANOMALY_THRESHOLD and bool(names) and names != ["insufficient_baseline"]
        )
        return AnomalyResult(
            agent_id=agent_id,
            anomaly_score=round(score, 4),
            is_anomalous=is_anomalous,
            reasons=tuple(names),
            method=method,
            model_version=self.version,
            features=f.vector,
            baseline_events=f.baseline_events,
            requests_per_minute=f.requests_per_minute,
        )
