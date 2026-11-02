import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from audit.hash_chain import (
    GENESIS_HASH,
    canonical_json,
    compute_hash,
    hashed_fields,
    verify_chain,
)


@dataclass(frozen=True)
class Ev:
    id: uuid.UUID
    seq: int
    tenant_id: uuid.UUID
    decision_id: uuid.UUID
    event_type: str
    actor_id: uuid.UUID
    event_data: dict
    created_at: datetime
    previous_hash: str
    event_hash: str


def _chain(n: int) -> list[Ev]:
    tenant, prev, events = uuid.uuid4(), GENESIS_HASH, []
    for i in range(1, n + 1):
        fields = dict(
            event_id=uuid.uuid4(),
            tenant_id=tenant,
            decision_id=uuid.uuid4(),
            event_type="decision.created",
            actor_id=uuid.uuid4(),
            event_data={"i": i, "nested": {"b": 2, "a": 1}},
            created_at=datetime.now(UTC),
        )
        h = compute_hash(prev, hashed_fields(**fields))
        f = {("id" if k == "event_id" else k): v for k, v in fields.items()}
        events.append(Ev(seq=i, previous_hash=prev, event_hash=h, **f))
        prev = h
    return events


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == canonical_json(
        {"a": [1, {"c": 3, "d": 2}], "b": 1}
    )


def test_canonical_json_types():
    u = uuid.uuid4()
    out = canonical_json({"u": u, "d": Decimal("0.93"), "t": datetime(2026, 1, 1, tzinfo=UTC)})
    assert str(u) in out and '"0.93"' in out and "2026-01-01T00:00:00+00:00" in out
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_hash_matches_spec_formula():
    import hashlib

    event = {"a": 1}
    assert compute_hash("p", event) == hashlib.sha256(b'p{"a":1}').hexdigest()


def test_valid_chain_verifies():
    result = verify_chain(_chain(5))
    assert result.valid and result.events_checked == 5


def test_empty_chain_is_valid():
    assert verify_chain([]).valid


def test_modified_content_detected():
    events = _chain(4)
    events[2] = replace(events[2], event_data={"i": 999})
    r = verify_chain(events)
    assert not r.valid and r.first_invalid_seq == 3 and "content" in r.reason


def test_deleted_event_detected():
    events = _chain(4)
    del events[1]
    r = verify_chain(events)
    assert not r.valid and r.first_invalid_seq == 3 and "link" in r.reason


def test_reordered_events_detected():
    events = _chain(3)
    events[0], events[1] = events[1], events[0]
    assert not verify_chain(events).valid


def test_rehashed_forgery_still_breaks_next_link():
    events = _chain(3)
    forged = replace(events[0], event_data={"i": "forged"})
    forged = replace(
        forged,
        event_hash=compute_hash(
            GENESIS_HASH,
            hashed_fields(
                event_id=forged.id,
                tenant_id=forged.tenant_id,
                decision_id=forged.decision_id,
                event_type=forged.event_type,
                actor_id=forged.actor_id,
                event_data=forged.event_data,
                created_at=forged.created_at,
            ),
        ),
    )
    events[0] = forged
    r = verify_chain(events)
    assert not r.valid and r.first_invalid_seq == 2
