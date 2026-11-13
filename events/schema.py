"""Load and apply the versioned governance event schema (schemas/governance_event/)."""

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# timezone.utc rather than datetime.UTC: this module also runs in the Spark image (Python 3.10).
UTC = timezone.utc  # noqa: UP017

SCHEMA_VERSION = "1.0.0"
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "governance_event"

TOPIC_RAW = "governance.events.raw"
TOPIC_VALID = "governance.events.valid"
TOPIC_DEAD_LETTER = "governance.events.dead-letter"


@lru_cache
def load_schema(version: str = SCHEMA_VERSION) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{version}.json").read_text(encoding="utf-8"))


@lru_cache
def validator(version: str = SCHEMA_VERSION) -> Draft202012Validator:
    schema = load_schema(version)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(event: Any) -> list[str]:
    """Schema violations as short, value-free messages (safe to log or put in a dead letter)."""
    out = []
    for e in validator().iter_errors(event):
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{path}:{e.validator}")
    return sorted(out)


def format_ts(dt: datetime) -> str:
    """UTC, millisecond precision, Z suffix - the only timestamp form the schema accepts."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp is not UTC")
    return dt
