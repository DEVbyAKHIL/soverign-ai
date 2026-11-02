import re
import uuid

import pytest

from detection.pii.regex_detector import RegexDetector, iban_valid, luhn_valid
from detection.transforms import (
    TransformPlan,
    apply_plan,
    entity_hash,
    mask,
    pseudonymise,
    redact,
    tokenise,
)
from detection.types import Entity

KEY = "unit-test-master-key-" + "z" * 30
T1 = uuid.UUID("11111111-1111-4111-8111-111111111111")
T2 = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_redact():
    assert redact("EMAIL", "a@b.co") == "[REDACTED:EMAIL]"


@pytest.mark.parametrize(
    "etype,value,expected",
    [
        ("EMAIL", "john@example.com", "j***@example.com"),
        ("CREDIT_CARD", "4111 1111 1111 1234", "**** **** **** 1234"),
        ("IBAN", "DE89 3704 0044 0532 0130 00", "DE** **** **** **** **30 00"),
        ("PASSWORD", "hunter2", "********"),
        ("PERSON", "Maria Rossi", "M***"),
    ],
)
def test_mask(etype, value, expected):
    assert mask(etype, value) == expected


def test_pseudonym_is_deterministic_per_tenant_and_keyed():
    a = pseudonymise(KEY, T1, "PERSON", "Maria Rossi")
    assert a == pseudonymise(KEY, T1, "PERSON", "Maria Rossi")
    assert re.fullmatch(r"<PERSON_[0-9a-f]{8}>", a)
    assert a != pseudonymise(KEY, T2, "PERSON", "Maria Rossi")  # not linkable across tenants
    assert a != pseudonymise(KEY + "x", T1, "PERSON", "Maria Rossi")  # depends on key


def test_card_token_preserves_format_and_last4_but_is_not_a_valid_card():
    for value in ["4111 1111 1111 1111", "5555-5555-5555-4444", "4000056655665556"]:
        tok = tokenise(KEY, T1, "CREDIT_CARD", value)
        assert len(tok) == len(value)
        assert [c.isdigit() for c in tok] == [c.isdigit() for c in value]
        assert re.sub(r"\D", "", tok)[-4:] == re.sub(r"\D", "", value)[-4:]
        assert tok != value
        assert not luhn_valid(re.sub(r"\D", "", tok))
        # And the regex layer no longer recognises it as a card.
        assert "CREDIT_CARD" not in {e.entity_type for e in RegexDetector().detect(tok)}


def test_iban_token_keeps_country_and_fails_checksum():
    tok = tokenise(KEY, T1, "IBAN", "DE89370400440532013000")
    assert tok.startswith("DE") and len(tok) == 22 and not iban_valid(tok)


def test_entity_hash_normalises_and_is_keyed():
    h = entity_hash(KEY, T1, "CREDIT_CARD", "4111 1111 1111 1111")
    assert h == entity_hash(KEY, T1, "CREDIT_CARD", "4111-1111-1111-1111")
    assert h != entity_hash(KEY, T2, "CREDIT_CARD", "4111 1111 1111 1111")
    import hashlib

    assert h != hashlib.sha256(b"4111111111111111").hexdigest()


def _ent(etype, text, value, part=0):
    start = text.index(value)
    return Entity(etype, start, start + len(value), 0.95, "regex", "v", value, part)


def test_plan_strongest_transform_wins_and_scopes_apply():
    text = "Maria Rossi paid with 4111 1111 1111 1111, password: hunter22"
    ents = (
        _ent("PERSON", text, "Maria Rossi"),
        _ent("CREDIT_CARD", text, "4111 1111 1111 1111"),
        _ent("PASSWORD", text, "hunter22"),
    )
    plan = TransformPlan(
        (
            ("redact", frozenset({"PASSWORD", "API_KEY"})),
            ("mask", frozenset({"CREDIT_CARD"})),
            ("pseudonymise", None),  # all sensitive entities
        )
    )
    [out], rmap = apply_plan([text], ents, plan, master_key=KEY, tenant_id=T1)
    assert "Maria Rossi" not in out and "<PERSON_" in out
    # pseudonymise (scope=all) is stronger than mask, so the card is tokenised, not masked
    assert "4111 1111 1111 1111" not in out
    assert "[REDACTED:PASSWORD]" in out and "hunter22" not in out
    assert [m["transform"] for m in rmap] == ["pseudonymise", "pseudonymise", "redact"]
    assert all("hunter22" not in str(m) and "Maria" not in str(m) for m in rmap)
    assert all(len(m["value_hash"]) == 64 for m in rmap)


def test_plan_ignores_non_sensitive_entities_unless_scoped():
    text = "Acme Logistics in Berlin"
    ents = (_ent("ORGANISATION", text, "Acme Logistics"), _ent("LOCATION", text, "Berlin"))
    [out], rmap = apply_plan(
        [text], ents, TransformPlan((("redact", None),)), master_key=KEY, tenant_id=T1
    )
    assert out == text and rmap == []
    [out], _ = apply_plan(
        [text],
        ents,
        TransformPlan((("redact", frozenset({"LOCATION"})),)),
        master_key=KEY,
        tenant_id=T1,
    )
    assert out == "Acme Logistics in [REDACTED:LOCATION]"


def test_plan_handles_multiple_parts_and_offsets():
    parts = ["email a@x.io and b@y.io", "and c@z.io"]
    ents = (
        _ent("EMAIL", parts[0], "a@x.io"),
        _ent("EMAIL", parts[0], "b@y.io"),
        _ent("EMAIL", parts[1], "c@z.io", part=1),
    )
    out, rmap = apply_plan(
        parts, ents, TransformPlan((("redact", None),)), master_key=KEY, tenant_id=T1
    )
    assert out == ["email [REDACTED:EMAIL] and [REDACTED:EMAIL]", "and [REDACTED:EMAIL]"]
    assert [(m["part"], m["start"]) for m in rmap] == [(0, 6), (0, 17), (1, 4)]
