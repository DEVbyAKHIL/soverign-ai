import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from gateway.core.errors import register_error_handlers
from gateway.core.security import Principal, Scope, require_scopes


@pytest.fixture
async def scoped_client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/policy-write")
    async def _w(p: Principal = Depends(require_scopes(Scope.POLICY_WRITE))) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.mark.parametrize(
    "role,expected",
    [
        ("admin", 200),
        ("policy_author", 200),
        ("developer", 403),
        ("agent", 403),
        ("security_analyst", 403),
    ],
)
async def test_rbac(scoped_client, make_token, role, expected):
    r = await scoped_client.get(
        "/policy-write", headers={"Authorization": f"Bearer {make_token(role)}"}
    )
    assert r.status_code == expected


async def test_basic_auth_scheme_rejected(scoped_client):
    r = await scoped_client.get("/policy-write", headers={"Authorization": "Basic YTpi"})
    assert r.status_code == 401
