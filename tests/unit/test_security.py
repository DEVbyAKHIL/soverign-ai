import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from gateway.config import get_settings
from gateway.core.errors import AuthenticationError
from gateway.core.security import Scope, create_access_token, decode_access_token


def _claims(**over):
    s = get_settings()
    now = datetime.now(UTC)
    base = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "ctp": "user",
        "role": "developer",
        "scope": "ai:invoke policy:read",
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "x",
        "typ": "access",
    }
    base.update(over)
    return base


def test_round_trip(tenant_a):
    sub = uuid.uuid4()
    p = decode_access_token(create_access_token(subject=sub, tenant_id=tenant_a, role="admin"))
    assert p.subject == sub and p.tenant_id == tenant_a
    assert p.scopes == {s.value for s in Scope}


def test_unknown_role_rejected_at_issue(tenant_a):
    with pytest.raises(ValueError):
        create_access_token(subject=uuid.uuid4(), tenant_id=tenant_a, role="superuser")


def test_tampered_signature_rejected(make_token):
    token = make_token()
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{sig[:-2]}{'AA' if sig[-2:] != 'AA' else 'BB'}"
    with pytest.raises(AuthenticationError):
        decode_access_token(tampered)


def test_wrong_secret_rejected():
    token = jwt.encode(_claims(), "another-secret-" + "y" * 40, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_alg_none_rejected():
    token = jwt.encode(_claims(), key="", algorithm="none")
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_expired_rejected():
    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        _claims(exp=past, iat=past - timedelta(minutes=1), nbf=past),
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(token)


@pytest.mark.parametrize("field,value", [("aud", "other"), ("iss", "evil"), ("typ", "refresh")])
def test_wrong_claims_rejected(field, value):
    token = jwt.encode(_claims(**{field: value}), get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_scope_escalation_is_clamped_to_role():
    token = jwt.encode(
        _claims(role="agent", scope="ai:invoke policy:write agent:manage"),
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert decode_access_token(token).scopes == {"ai:invoke"}


def test_unknown_role_in_token_rejected():
    token = jwt.encode(_claims(role="root"), get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_keyed_fingerprints_depend_on_key_and_purpose():
    import hashlib

    from gateway.core.fingerprint import fingerprint

    a = fingerprint("k" * 40, "content", "hello")
    assert a == fingerprint("k" * 40, "content", "hello")  # stable: repeats still correlate
    assert a != fingerprint("j" * 40, "content", "hello")  # depends on the key
    assert a != fingerprint("k" * 40, "output", "hello")  # purposes never collide
    assert a != hashlib.sha256(b"hello").hexdigest()  # not confirmable by hashing a guess


def test_settings_reject_reused_or_placeholder_keys(monkeypatch):
    import pytest
    from pydantic import ValidationError

    from gateway.config import Settings

    same = "s" * 40
    with pytest.raises(ValidationError):
        Settings(jwt_secret=same, entity_hash_key="e" * 40, fingerprint_hmac_key=same)
    with pytest.raises(ValidationError):
        Settings(
            jwt_secret="j" * 40,
            entity_hash_key="e" * 40,
            fingerprint_hmac_key="CHANGE_ME" + "x" * 30,
        )
