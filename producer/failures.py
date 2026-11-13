"""Failure injection: deliberately broken events to exercise dead-letter handling downstream."""

import json
import random
from typing import Any

KINDS = (
    "missing_field",
    "bad_timestamp",
    "naive_timestamp",
    "bad_enum",
    "bad_uuid",
    "negative_latency",
    "extra_field",
    "not_json",
)


def corrupt(event: dict[str, Any], rng: random.Random) -> tuple[bytes, str]:
    """Return a broken serialisation of `event` and the kind of breakage."""
    kind = rng.choice(KINDS)
    ev = dict(event)
    if kind == "missing_field":
        ev.pop(rng.choice(("event_timestamp", "tenant_id", "decision", "latency_ms")))
    elif kind == "bad_timestamp":
        ev["event_timestamp"] = "not-a-timestamp"
    elif kind == "naive_timestamp":
        ev["event_timestamp"] = ev["event_timestamp"].rstrip("Z")
    elif kind == "bad_enum":
        ev["decision"] = "maybe"
    elif kind == "bad_uuid":
        ev["tenant_id"] = "tenant-1"
    elif kind == "negative_latency":
        ev["latency_ms"] = -5
    elif kind == "extra_field":
        ev["prompt"] = "<synthetic placeholder>"  # schema forbids content fields
    else:
        return b'{"event_id": "truncated', kind
    return json.dumps(ev, separators=(",", ":")).encode(), kind
