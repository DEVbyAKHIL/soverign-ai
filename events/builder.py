"""Build a governance event from a gateway decision. Only allow-listed, value-free fields."""

import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Any

from events.schema import SCHEMA_VERSION, format_ts

# Winning policy action -> event decision (see docs/task2-gap-analysis.md section 2.4).
ACTION_TO_DECISION = {
    "deny": "deny",
    "quarantine": "quarantine",
    "require_human_review": "require_review",
    "route_to_approved_model": "route",
    "redact": "redact",
    "pseudonymise": "redact",
    "mask": "redact",
    "allow": "allow",
}
BLOCKING = frozenset({"deny", "quarantine"})


def caller_key(master: str) -> bytes:
    """Purpose-bound subkey of ENTITY_HASH_KEY (same derivation as detection.transforms)."""
    return hmac.new(master.encode(), b"event-caller-id", hashlib.sha256).digest()


def pseudonymous_id(key: bytes, tenant_id: uuid.UUID, value: uuid.UUID) -> str:
    """Keyed HMAC pseudonym shaped as a UUID. Plain SHA-256 of a known user id is reversible by
    enumeration; without the key it is not. Stable per tenant, so events stay correlatable."""
    digest = hmac.new(key, f"{tenant_id}|{value}".encode(), hashlib.sha256).digest()
    return str(uuid.UUID(bytes=digest[:16]))


def decision_event(
    *,
    event_id: uuid.UUID,
    timestamp: datetime,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    caller_type: str,
    caller_pseudonym: str,
    action_taken: str,
    endpoint: str,
    destination_model: str,
    destination_jurisdiction: str,
    declared_purpose: str,
    sensitivity_level: str,
    pii_count: int,
    injection_risk_score: float,
    policy_ids: list[str],
    latency_ms: int,
    tokens_processed: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    decision = ACTION_TO_DECISION[action_taken]
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event_id),
        "event_timestamp": format_ts(timestamp),
        "tenant_id": str(tenant_id),
        "agent_id": str(agent_id) if agent_id else None,
        "caller_type": caller_type,
        "caller_id": caller_pseudonym,
        "event_type": "policy_decision",
        "decision": decision,
        "action": action_taken,
        "destination_model": destination_model,
        "destination_jurisdiction": destination_jurisdiction,
        "declared_purpose": declared_purpose,
        "sensitivity_level": sensitivity_level,
        "pii_count": pii_count,
        "injection_risk_score": round(min(1.0, max(0.0, injection_risk_score)), 4),
        "policy_ids": policy_ids[:50],
        "latency_ms": max(0, latency_ms),
        "tokens_processed": max(0, tokens_processed),
        "status": "blocked" if action_taken in BLOCKING else "completed",
        "metadata": {"source": "gateway", "synthetic": False, "endpoint": endpoint, **metadata},
    }
