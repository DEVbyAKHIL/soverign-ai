"""Tamper-evident hash chain: event_hash = SHA-256(previous_hash + canonical_event_json)."""

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

GENESIS_HASH = "0" * 64


def _default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Not canonicalisable: {type(value).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    )


def compute_hash(previous_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256((previous_hash + canonical_json(event)).encode("utf-8")).hexdigest()


def hashed_fields(
    *,
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    event_type: str,
    actor_id: uuid.UUID,
    event_data: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    """The exact fields covered by the hash. Changing this breaks existing chains."""
    return {
        "id": event_id,
        "tenant_id": tenant_id,
        "decision_id": decision_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "event_data": event_data,
        "created_at": created_at,
    }


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    events_checked: int
    first_invalid_seq: int | None = None
    reason: str | None = None


def verify_chain(events: Iterable[Any], start_hash: str = GENESIS_HASH) -> ChainVerification:
    """Verify events (ORM rows or objects with the same attributes) in seq order.

    `start_hash` is the genesis hash, or - after retention pruned the oldest events - the
    anchor hash recorded for the tenant (the event_hash of the last pruned event)."""
    previous = start_hash
    count = 0
    for ev in events:
        count += 1
        if ev.previous_hash != previous:
            return ChainVerification(False, count, ev.seq, "previous_hash does not link")
        expected = compute_hash(
            previous,
            hashed_fields(
                event_id=ev.id,
                tenant_id=ev.tenant_id,
                decision_id=ev.decision_id,
                event_type=ev.event_type,
                actor_id=ev.actor_id,
                event_data=ev.event_data,
                created_at=ev.created_at,
            ),
        )
        if ev.event_hash != expected:
            return ChainVerification(False, count, ev.seq, "event_hash does not match content")
        previous = ev.event_hash
    return ChainVerification(True, count)
