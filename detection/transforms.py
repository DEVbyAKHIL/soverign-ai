"""Redaction, masking, pseudonymisation and tokenisation, plus keyed entity hashing.

* redact        -> "[REDACTED:EMAIL]"                       (irreversible, no linkage)
* mask          -> "j***@example.com", "**** **** **** 1234" (partial disclosure)
* pseudonymise  -> "<PERSON_3f9a1c2b>"                      (keyed, deterministic per tenant:
                                                              same value -> same pseudonym)
* tokenise      -> format-preserving keyed token for structured numbers (used by pseudonymise
                   for cards/IBANs/phones/IDs so downstream format checks still pass; card tokens
                   keep the last 4 digits and are forced to fail the Luhn check)

Keys are derived from ENTITY_HASH_KEY with a purpose label, and every derivation includes the
tenant id, so hashes and pseudonyms are never linkable across tenants.
"""

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass

from detection.pii.regex_detector import luhn_valid
from detection.types import TRANSFORMABLE_CATEGORIES, Entity

STRENGTH = {"redact": 3, "pseudonymise": 2, "mask": 1}
FORMAT_PRESERVING = {"CREDIT_CARD", "IBAN", "PHONE", "NATIONAL_ID", "PASSPORT", "CUSTOMER_ID"}


def _derive(master: str, purpose: str) -> bytes:
    return hmac.new(master.encode(), purpose.encode(), hashlib.sha256).digest()


def _normalise(value: str) -> str:
    return re.sub(r"[\s\-.]", "", value).lower()


def entity_hash(master: str, tenant_id: uuid.UUID, entity_type: str, value: str) -> str:
    """Keyed hash for analytics storage. Plain SHA-256 of e.g. a card number is brute-forceable."""
    msg = f"{tenant_id}|{entity_type}|{_normalise(value)}".encode()
    return hmac.new(_derive(master, "entity-hash"), msg, hashlib.sha256).hexdigest()


def _stream(master: str, tenant_id: uuid.UUID, entity_type: str, value: str) -> bytes:
    msg = f"{tenant_id}|{entity_type}|{_normalise(value)}".encode()
    key = _derive(master, "pseudonym")
    out, counter = b"", 0
    while len(out) < 64:
        out += hmac.new(key, msg + counter.to_bytes(2, "big"), hashlib.sha256).digest()
        counter += 1
    return out


def redact(entity_type: str, value: str) -> str:
    return f"[REDACTED:{entity_type}]"


def mask(entity_type: str, value: str) -> str:
    if entity_type == "EMAIL" and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if entity_type in ("CREDIT_CARD", "IBAN", "PHONE", "NATIONAL_ID", "CUSTOMER_ID"):
        alnum = [i for i, c in enumerate(value) if c.isalnum()]
        keep = set(alnum[-4:])
        if entity_type == "IBAN":
            keep |= set(alnum[:2])
        return "".join(c if (i in keep or not c.isalnum()) else "*" for i, c in enumerate(value))
    if entity_type in ("PASSWORD", "API_KEY"):
        return "********"
    return value[:1] + "***" if value else "***"


def tokenise(master: str, tenant_id: uuid.UUID, entity_type: str, value: str) -> str:
    """Format-preserving keyed token: digits->digits, letters->letters, separators kept."""
    stream = _stream(master, tenant_id, entity_type, value)
    alnum = [i for i, c in enumerate(value) if c.isalnum()]
    keep = set(alnum[-4:]) if entity_type == "CREDIT_CARD" else set()
    if entity_type == "IBAN":
        keep = set(alnum[:2])  # keep country code so the token still looks like an IBAN
    out = []
    for i, c in enumerate(value):
        b = stream[i % len(stream)]
        if i in keep or not c.isalnum():
            out.append(c)
        elif c.isdigit():
            out.append(str(b % 10))
        elif c.isupper():
            out.append(chr(ord("A") + b % 26))
        else:
            out.append(chr(ord("a") + b % 26))
    token = "".join(out)
    if entity_type == "CREDIT_CARD":
        digits = re.sub(r"\D", "", token)
        if luhn_valid(digits):  # never emit something that validates as a real card
            first = next(i for i, c in enumerate(token) if c.isdigit())
            token = token[:first] + str((int(token[first]) + 1) % 10) + token[first + 1 :]
    return token


def pseudonymise(master: str, tenant_id: uuid.UUID, entity_type: str, value: str) -> str:
    if entity_type in FORMAT_PRESERVING:
        return tokenise(master, tenant_id, entity_type, value)
    tag = _stream(master, tenant_id, entity_type, value)[:4].hex()
    return f"<{entity_type}_{tag}>"


@dataclass(frozen=True)
class TransformPlan:
    """(transform, scope) pairs from matched policies. scope None = all sensitive entities."""

    steps: tuple[tuple[str, frozenset[str] | None], ...]

    def transform_for(self, entity: Entity) -> str | None:
        chosen: str | None = None
        for transform, scope in self.steps:
            if scope is None:
                applies = bool(set(entity.spec.categories) & TRANSFORMABLE_CATEGORIES)
            else:
                applies = entity.entity_type in scope
            if applies and (chosen is None or STRENGTH[transform] > STRENGTH[chosen]):
                chosen = transform
        return chosen


def apply_plan(
    parts: list[str],
    entities: tuple[Entity, ...],
    plan: TransformPlan,
    *,
    master_key: str,
    tenant_id: uuid.UUID,
) -> tuple[list[str], list[dict]]:
    """Return transformed parts and a redaction map (no raw values; original offsets)."""
    out = list(parts)
    redaction_map: list[dict] = []
    for e in sorted(entities, key=lambda x: (x.part, x.start), reverse=True):
        transform = plan.transform_for(e)
        if transform is None:
            continue
        if transform == "redact":
            replacement = redact(e.entity_type, e.value)
        elif transform == "mask":
            replacement = mask(e.entity_type, e.value)
        else:
            replacement = pseudonymise(master_key, tenant_id, e.entity_type, e.value)
        text = out[e.part]
        out[e.part] = text[: e.start] + replacement + text[e.end :]
        redaction_map.append(
            {
                "part": e.part,
                "start": e.start,
                "end": e.end,
                "entity_type": e.entity_type,
                "transform": transform,
                "replacement": replacement if transform != "mask" else "[masked]",
                "value_hash": entity_hash(master_key, tenant_id, e.entity_type, e.value),
            }
        )
    redaction_map.reverse()
    return out, redaction_map
