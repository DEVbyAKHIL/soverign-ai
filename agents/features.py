"""Rolling behavioural features for an agent: the current window compared with its own baseline.

Features are scale-free (log-ratios, deltas, fractions) so a single anomaly model works for agents
whose normal volumes differ by orders of magnitude.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=10)
BASELINE = timedelta(days=7)
MIN_BASELINE_EVENTS = 30
MIN_BASELINE_BUCKETS = 3
QUIET_HOUR_SHARE = 0.02  # an hour with < 2% of baseline activity counts as "off hours"
BLOCKING = {"deny"}  # final decisions that count as denied


@dataclass(frozen=True)
class EventRow:
    created_at: datetime
    tokens: int
    sensitive: int
    destination: str
    status: str  # final decision
    datasets: tuple[str, ...] = ()
    tool_calls: int = 0
    content_hash: str = ""


# Order matters: this is the anomaly model's input vector.
FEATURE_NAMES = (
    "request_rate_log_ratio",
    "volume_log_ratio",
    "sensitive_per_request",
    "sensitive_access_delta",
    "denied_ratio",
    "new_destinations",
    "destination_count_log",
    "new_datasets",
    "tool_calls_delta",
    "retry_rate",
    "off_hours_ratio",
)


@dataclass(frozen=True)
class BucketStats:
    mean: float
    std: float
    p95: float


@dataclass(frozen=True)
class AgentFeatures:
    vector: dict[str, float]
    window_requests: int
    requests_per_minute: float
    baseline_events: int
    baseline_buckets: int
    requests_stats: BucketStats
    tokens_stats: BucketStats
    window_tokens: int
    window_denied: int
    new_destination_names: tuple[str, ...] = field(default=())

    @property
    def baseline_sufficient(self) -> bool:
        return (
            self.baseline_events >= MIN_BASELINE_EVENTS
            and self.baseline_buckets >= MIN_BASELINE_BUCKETS
        )

    def as_list(self) -> list[float]:
        return [self.vector[n] for n in FEATURE_NAMES]


def _bucket_stats(values: list[float]) -> BucketStats:
    if not values:
        return BucketStats(0.0, 0.0, 0.0)
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1)]
    return BucketStats(mean, std, p95)


def _buckets(events: list[EventRow]) -> dict[int, list[EventRow]]:
    """Group baseline events into WINDOW-sized buckets (only active buckets are kept)."""
    size = WINDOW.total_seconds()
    out: dict[int, list[EventRow]] = {}
    for e in events:
        out.setdefault(int(e.created_at.timestamp() // size), []).append(e)
    return out


def compute_features(window: list[EventRow], baseline: list[EventRow]) -> AgentFeatures:
    n = len(window)
    buckets = _buckets(baseline)
    req_stats = _bucket_stats([float(len(b)) for b in buckets.values()])
    tok_stats = _bucket_stats([float(sum(e.tokens for e in b)) for b in buckets.values()])
    w_tokens = sum(e.tokens for e in window)
    w_sensitive = sum(e.sensitive for e in window)
    w_denied = sum(e.status in BLOCKING for e in window)

    b_n = len(baseline)
    b_sens_per_req = sum(e.sensitive for e in baseline) / b_n if b_n else 0.0
    b_tools_per_req = sum(e.tool_calls for e in baseline) / b_n if b_n else 0.0
    sens_per_req = w_sensitive / n if n else 0.0
    tools_per_req = sum(e.tool_calls for e in window) / n if n else 0.0

    known_dest = {e.destination for e in baseline}
    w_dest = {e.destination for e in window}
    new_dest = sorted(w_dest - known_dest) if baseline else []
    known_ds = {d for e in baseline for d in e.datasets}
    new_ds = {d for e in window for d in e.datasets} - known_ds if baseline else set()

    hashes = Counter(e.content_hash for e in window if e.content_hash)
    repeats = sum(c - 1 for c in hashes.values())

    hours = Counter(e.created_at.hour for e in baseline)
    quiet = {h for h in range(24) if hours.get(h, 0) < QUIET_HOUR_SHARE * b_n} if b_n else set()
    off_hours = sum(e.created_at.hour in quiet for e in window) / n if n and b_n else 0.0

    vector = {
        "request_rate_log_ratio": math.log1p(n) - math.log1p(req_stats.mean),
        "volume_log_ratio": math.log1p(w_tokens) - math.log1p(tok_stats.mean),
        "sensitive_per_request": sens_per_req,
        "sensitive_access_delta": sens_per_req - b_sens_per_req,
        "denied_ratio": w_denied / n if n else 0.0,
        "new_destinations": float(len(new_dest)),
        "destination_count_log": math.log1p(len(w_dest)),
        "new_datasets": float(len(new_ds)),
        "tool_calls_delta": tools_per_req - b_tools_per_req,
        "retry_rate": repeats / n if n else 0.0,
        "off_hours_ratio": off_hours,
    }
    return AgentFeatures(
        vector={k: round(v, 6) for k, v in vector.items()},
        window_requests=n,
        requests_per_minute=round(n / (WINDOW.total_seconds() / 60), 3),
        baseline_events=b_n,
        baseline_buckets=len(buckets),
        requests_stats=req_stats,
        tokens_stats=tok_stats,
        window_tokens=w_tokens,
        window_denied=w_denied,
        new_destination_names=tuple(new_dest),
    )
