import uuid

from gateway.routers import health


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    uuid.UUID(r.headers["x-request-id"])


async def test_request_id_propagated_only_if_valid_uuid(client):
    rid = str(uuid.uuid4())
    assert (await client.get("/healthz", headers={"X-Request-ID": rid})).headers[
        "x-request-id"
    ] == rid
    r = await client.get("/healthz", headers={"X-Request-ID": "<script>"})
    assert r.headers["x-request-id"] != "<script>"


async def test_readyz_ready(client, monkeypatch):
    async def ok() -> bool:
        return True

    monkeypatch.setattr(health, "check_database", ok)
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {"database": True, "redis": True}


async def test_readyz_not_ready_when_db_down(client, monkeypatch):
    async def down() -> bool:
        return False

    monkeypatch.setattr(health, "check_database", down)
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


async def test_metrics_exposed(client):
    await client.get("/healthz")
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "gateway_http_requests_total" in r.text


async def test_me_requires_token(client):
    r = await client.get("/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    assert r.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_garbage_token(client):
    r = await client.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


async def test_me_with_token(client, make_token, tenant_a):
    r = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {make_token('admin')}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == str(tenant_a)
    assert "policy:write" in body["scopes"]


async def test_dev_token_flow(client, tenant_a):
    r = await client.post(
        "/v1/auth/dev-token",
        json={"subject": str(uuid.uuid4()), "tenant_id": str(tenant_a), "role": "security_analyst"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "security_analyst"


async def test_dev_token_unknown_role(client, tenant_a):
    r = await client.post(
        "/v1/auth/dev-token",
        json={"subject": str(uuid.uuid4()), "tenant_id": str(tenant_a), "role": "root"},
    )
    assert r.status_code == 422


async def test_validation_error_does_not_echo_input(client):
    secret = "john.doe@example.com"
    r = await client.post("/v1/auth/dev-token", json={"subject": secret})
    assert r.status_code == 422
    assert secret not in r.text


async def test_oversized_payload_rejected(client):
    r = await client.post(
        "/v1/auth/dev-token", content=b"x" * 2_000_000, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413
