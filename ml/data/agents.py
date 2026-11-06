"""Seeded synthetic agent behaviour: 7-day baselines plus normal and anomalous 10-minute windows.

Each simulated agent has its own profile (rate, working hours, payload size, destinations,
datasets, tool use, sensitive-data rate), so features must generalise across very different
"normal". Anomalies come in two severities: `strong` (clear incidents) and `subtle` (near the edge
of normal variation) to show where detection degrades.
"""

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agents.features import WINDOW, EventRow, compute_features

ANOMALY_TYPES = (
    "burst",
    "data_volume",
    "exfiltration",
    "new_destination",
    "off_hours",
    "denial_storm",
    "dataset_sweep",
    "tool_abuse",
    "retry_loop",
)
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Profile:
    rate: float  # mean requests per active 10-minute window
    hours: tuple[int, int]  # active hours [start, end)
    tokens: float  # mean tokens per request
    sensitive: float  # mean sensitive fields per request
    destinations: tuple[str, ...]
    datasets: tuple[str, ...]
    tools: float  # mean tool calls per request
    deny: float  # baseline denial probability
    activity: float  # probability a window in active hours has traffic


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:
        return max(0, int(rng.gauss(lam, math.sqrt(lam))))
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def profile(rng: random.Random) -> Profile:
    batch = rng.random() < 0.25
    hours = (0, 24) if batch else (rng.choice([7, 8, 9]), rng.choice([17, 18, 19]))
    n_dest = rng.randint(1, 3)
    return Profile(
        rate=rng.choice([1.5, 3, 6, 12, 25, 50]),
        hours=hours,
        tokens=rng.choice([150, 400, 900, 2000]),
        sensitive=rng.choice([0.0, 0.1, 0.3, 0.8]),
        destinations=tuple(f"api{rng.randint(1, 4)}.model-eu.example" for _ in range(n_dest)),
        datasets=tuple(
            f"kb/{rng.choice(['hr', 'sales', 'support', 'eng', 'legal'])}-{i}"
            for i in range(rng.randint(2, 8))
        ),
        tools=rng.choice([0.0, 0.2, 1.0, 2.0]),
        deny=rng.choice([0.0, 0.01, 0.03]),
        activity=rng.uniform(0.5, 0.95),
    )


def _event(rng: random.Random, p: Profile, ts: datetime, **over) -> EventRow:
    base = {
        "created_at": ts,
        "tokens": max(1, int(rng.lognormvariate(math.log(p.tokens), 0.5))),
        "sensitive": _poisson(rng, p.sensitive),
        "destination": rng.choice(p.destinations),
        "status": "deny" if rng.random() < p.deny else "allow",
        "datasets": tuple(rng.sample(p.datasets, k=min(len(p.datasets), rng.randint(0, 2)))),
        "tool_calls": _poisson(rng, p.tools),
        "content_hash": f"{rng.getrandbits(48):x}",
    }
    base.update(over)
    return EventRow(**base)


def _window_events(rng: random.Random, p: Profile, start: datetime, rate: float) -> list[EventRow]:
    n = _poisson(rng, rate)
    span = WINDOW.total_seconds()
    return sorted(
        (_event(rng, p, start + timedelta(seconds=rng.uniform(0, span))) for _ in range(n)),
        key=lambda e: e.created_at,
    )


def baseline(rng: random.Random, p: Profile, days: int = 7) -> list[EventRow]:
    events: list[EventRow] = []
    start = NOW - timedelta(days=days)
    t = start
    while t < NOW - WINDOW:
        active = p.hours[0] <= t.hour < p.hours[1]
        if active and rng.random() < p.activity:
            events.extend(_window_events(rng, p, t, p.rate))
        elif not active and rng.random() < 0.01:  # rare stray off-hours call
            events.extend(_window_events(rng, p, t, 1))
        t += WINDOW
    return events


def _active_slot(rng: random.Random, p: Profile) -> datetime:
    hour = rng.randrange(p.hours[0], p.hours[1])
    return NOW.replace(hour=hour, minute=rng.choice([0, 10, 20, 30, 40, 50]))


def anomalous_window(rng: random.Random, p: Profile, kind: str, strong: bool) -> list[EventRow]:
    k = 1.0 if strong else 0.4  # subtle anomalies are ~40% as extreme
    start = _active_slot(rng, p)
    rate = max(p.rate, 3)
    if kind == "burst":
        return _window_events(rng, p, start, p.rate * (1 + k * rng.uniform(6, 15)) + 5 * k)
    w = _window_events(rng, p, start, rate)
    if not w:
        w = [_event(rng, p, start)]
    if kind == "data_volume":
        f = 1 + k * rng.uniform(8, 20)
        return [EventRow(**{**e.__dict__, "tokens": int(e.tokens * f)}) for e in w]
    if kind == "exfiltration":
        dest = f"upload.{rng.choice(['files', 'paste', 'drop'])}-share.example"
        return [
            EventRow(
                **{
                    **e.__dict__,
                    "sensitive": e.sensitive + int(k * rng.randint(3, 8)),
                    "destination": dest,
                }
            )
            for e in w
        ]
    if kind == "new_destination":
        dest = f"api.unknown-{rng.randint(1, 999)}.example"
        n_new = max(1, int(len(w) * (0.6 if strong else 0.15)))
        return [
            EventRow(**{**e.__dict__, "destination": dest}) if i < n_new else e
            for i, e in enumerate(w)
        ]
    if kind == "off_hours":
        if p.hours == (0, 24):  # batch agents have no off hours; shift to a different anomaly
            return anomalous_window(rng, p, "burst", strong)
        hour = rng.choice([h for h in range(24) if not p.hours[0] - 1 <= h < p.hours[1] + 1])
        st = NOW.replace(hour=hour, minute=0)
        return _window_events(rng, p, st, max(rate, 4) * (1 if strong else 0.5))
    if kind == "denial_storm":
        share = 0.7 if strong else 0.3
        w = w if len(w) >= 5 else w + [_event(rng, p, start) for _ in range(5 - len(w))]
        return [
            EventRow(**{**e.__dict__, "status": "deny"}) if rng.random() < share else e for e in w
        ]
    if kind == "dataset_sweep":
        n = int(k * rng.randint(6, 20)) + 1
        new = tuple(
            f"kb/{rng.choice(['finance', 'board', 'payroll', 'security'])}-{i}" for i in range(n)
        )
        return [
            EventRow(**{**e.__dict__, "datasets": new[i :: len(w)] or new[:1]})
            for i, e in enumerate(w)
        ]
    if kind == "tool_abuse":
        extra = k * rng.uniform(4, 10)
        return [EventRow(**{**e.__dict__, "tool_calls": e.tool_calls + int(extra)}) for e in w]
    if kind == "retry_loop":
        w = w if len(w) >= 6 else w + [_event(rng, p, start) for _ in range(6 - len(w))]
        h = "deadbeef"
        share = 0.8 if strong else 0.35
        return [
            EventRow(**{**e.__dict__, "content_hash": h}) if rng.random() < share else e for e in w
        ]
    raise ValueError(kind)


def normal_window(rng: random.Random, p: Profile) -> list[EventRow]:
    w = _window_events(rng, p, _active_slot(rng, p), p.rate)
    return w or [_event(rng, p, _active_slot(rng, p))]


@dataclass(frozen=True)
class Sample:
    features: list[float]
    label: str  # "normal" or an anomaly type
    strong: bool
    agent: int
    feature_obj: object
    max_rpm: int


def dataset(
    n_agents: int, normals_per_agent: int, anomalies_per_agent: int, seed: int
) -> list[Sample]:
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    out: list[Sample] = []
    for a in range(n_agents):
        p = profile(rng)
        base = baseline(rng, p)
        max_rpm = max(10, int(p.rate * 3))
        for _ in range(normals_per_agent):
            f = compute_features(normal_window(rng, p), base)
            out.append(Sample(f.as_list(), "normal", False, a, f, max_rpm))
        for i in range(anomalies_per_agent):
            kind = ANOMALY_TYPES[i % len(ANOMALY_TYPES)]
            strong = (i // len(ANOMALY_TYPES)) % 2 == 0
            f = compute_features(anomalous_window(rng, p, kind, strong), base)
            out.append(Sample(f.as_list(), kind, strong, a, f, max_rpm))
    return out
