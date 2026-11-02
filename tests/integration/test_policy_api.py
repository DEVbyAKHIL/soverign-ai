"""Policy API and policy-to-database versioning."""

import uuid

import pytest
from sqlalchemy import select

from gateway.config import get_settings
from gateway.db import dispose_engine, get_session
from gateway.models import Policy as PolicyRow
from policy.loader import Policy, PolicyLoadError, PolicySet, load_policy_dir
from policy.sync import sync_policies

NEW_POLICY = """policy_id: brand_new_policy
version: 1.0.0
description: candidate
severity: medium
match:
  caller_type: [agent]
actions: [require_human_review]
"""


def _h(make_token, role):
    return {"Authorization": f"Bearer {make_token(role)}"}


async def test_list_policies(client, make_token):
    r = await client.get("/v1/policies", headers=_h(make_token, "developer"))
    assert r.status_code == 200
    body = r.json()
    assert len(body["policies"]) == 7
    assert body["policies"][0]["severity"] == "critical"
    assert all(len(p["sha256"]) == 64 for p in body["policies"])


async def test_list_policies_requires_scope(client, make_token):
    r = await client.get("/v1/policies", headers=_h(make_token, "agent"))
    assert r.status_code == 403


async def test_validate_good_policy(client, make_token):
    r = await client.post(
        "/v1/policies/validate",
        json={"policy_yaml": NEW_POLICY},
        headers=_h(make_token, "policy_author"),
    )
    assert r.json() == {
        "valid": True,
        "errors": [],
        "policy_id": "brand_new_policy",
        "version": "1.0.0",
        "version_bump_required": False,
    }


async def test_validate_bad_policy_lists_errors(client, make_token):
    r = await client.post(
        "/v1/policies/validate",
        json={"policy_yaml": "policy_id: x\nactions: [allow, deny]\n"},
        headers=_h(make_token, "policy_author"),
    )
    body = r.json()
    assert body["valid"] is False and len(body["errors"]) >= 1


async def test_validate_detects_missing_version_bump(client, make_token):
    loaded = load_policy_dir(get_settings().policy_dir).get("route_confidential_to_eu")
    changed = loaded.source_yaml.replace("eu-approved-llm", "other-eu-llm")
    r = await client.post(
        "/v1/policies/validate",
        json={"policy_yaml": changed},
        headers=_h(make_token, "policy_author"),
    )
    body = r.json()
    assert body["valid"] is False and body["version_bump_required"] is True


async def test_validate_requires_policy_write(client, make_token):
    r = await client.post(
        "/v1/policies/validate",
        json={"policy_yaml": NEW_POLICY},
        headers=_h(make_token, "security_analyst"),
    )
    assert r.status_code == 403


def test_app_refuses_to_start_with_invalid_policies(tmp_path, monkeypatch, fake_redis):
    (tmp_path / "bad.yaml").write_text("policy_id: bad\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "policy_dir", str(tmp_path))
    from gateway.main import create_app

    with pytest.raises(PolicyLoadError):
        create_app()


# --- database versioning (requires TEST_DATABASE_URL) ---------------------


@pytest.mark.usefixtures("clean_db")
async def test_policy_sync_versioning():
    base = load_policy_dir(get_settings().policy_dir)
    try:
        async for s in get_session():
            assert await sync_policies(s, base) == {
                "inserted": 7,
                "unchanged": 0,
                "deactivated": 0,
            }
        async for s in get_session():
            assert (await sync_policies(s, base))["unchanged"] == 7

        # Same version, different content -> refused.
        route = base.get("route_confidential_to_eu")
        tampered = Policy.from_yaml(route.source_yaml.replace("eu-approved-llm", "x-llm"))
        others = [p for p in base.policies if p.policy_id != route.policy_id]
        async for s in get_session():
            with pytest.raises(PolicyLoadError, match="version bump"):
                await sync_policies(s, PolicySet.build([*others, tampered]))

        # Bumped version -> new row active, old row deactivated, history kept.
        bumped = Policy.from_yaml(
            route.source_yaml.replace("eu-approved-llm", "x-llm").replace(
                "version: 1.0.0", "version: 1.1.0"
            )
        )
        async for s in get_session():
            stats = await sync_policies(s, PolicySet.build([*others, bumped]))
            assert stats == {"inserted": 1, "unchanged": 6, "deactivated": 1}
            rows = (
                (
                    await s.execute(
                        select(PolicyRow).where(PolicyRow.policy_id == "route_confidential_to_eu")
                    )
                )
                .scalars()
                .all()
            )
            assert {(r.version, r.is_active) for r in rows} == {("1.0.0", False), ("1.1.0", True)}
            assert all(r.created_by == uuid.UUID(int=0) for r in rows)
    finally:
        await dispose_engine()
