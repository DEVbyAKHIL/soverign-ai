"""reporting-api against a real PostgreSQL with seeded aggregates (needs TEST_DATABASE_URL)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from gateway.core.security import create_access_token
from reporting.config import ReportingSettings
from reporting.main import create_app
from tests.conftest import TEST_DATABASE_URL, _exec

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("12121212-0000-4000-8000-000000000001")
OTHER = uuid.UUID("34343434-0000-4000-8000-000000000002")
AGENT = uuid.UUID("56565656-0000-4000-8000-000000000003")
NIL = "00000000-0000-0000-0000-000000000000"
NOW = datetime.now(UTC).replace(second=0, microsecond=0)
TABLES = (
    "aggregated_request_metrics, aggregated_policy_metrics, aggregated_latency_metrics, "
    "aggregated_pii_metrics, aggregated_injection_metrics, aggregated_agent_risk_metrics, "
    "aggregated_sovereignty_metrics, pipeline_progress"
)


def _ins(table: str, cols: str, rows: list[str]) -> str:
    return f"insert into {table} ({cols}) values " + ", ".join(rows)


def _ts(minutes_ago: int) -> str:
    return f"'{(NOW - timedelta(minutes=minutes_ago)).isoformat()}'"


def _seed() -> None:
    base = "window_start, window_end, window_type, tenant_id"
    tail = "metric, value, event_count, query_name, batch_id"
    req, pol, risk = [], [], []
    for m in range(1, 11):  # ten 1-minute windows for TENANT, one for OTHER
        w = f"{_ts(m)}, {_ts(m - 1)}, 'tumbling'"
        req.append(f"({w}, '{TENANT}', '{NIL}', 'requests', {100 + m}, {100 + m}, 'q', 1)")
        req.append(f"({w}, '{TENANT}', '{AGENT}', 'requests', {m}, {m}, 'q', 1)")
        pol.append(f"({w}, '{TENANT}', 'deny', 'count', 10, 10, 'q', 1)")
        pol.append(f"({w}, '{TENANT}', 'allow', 'count', 90, 90, 'q', 1)")
    req.append(
        f"({_ts(1)}, {_ts(0)}, 'tumbling', '{OTHER}', '{NIL}', 'requests', 999, 999, 'q', 1)"
    )
    for m, score in ((10, 0.2), (1, 0.7)):
        w = f"{_ts(m + 9)}, {_ts(m - 1)}, 'sliding', '{TENANT}', '{AGENT}'"
        for metric, value in (("anomaly_score", score), ("requests", 50), ("deny_rate", 0.4)):
            risk.append(f"({w}, '{metric}', {value}, 50, 'q', 1)")
    sql = [
        _ins("aggregated_request_metrics", f"{base}, agent_id, {tail}", req),
        _ins("aggregated_policy_metrics", f"{base}, decision, {tail}", pol),
        _ins(
            "aggregated_policy_metrics",
            f"{base}, decision, {tail}",
            [f"({_ts(5)}, {_ts(0)}, 'sliding', '{TENANT}', '*', 'deny_rate', 0.1, 500, 'q', 1)"],
        ),
        _ins(
            "aggregated_latency_metrics",
            f"{base}, {tail}",
            [
                f"({_ts(5)}, {_ts(0)}, 'sliding', '{TENANT}', 'p99_ms', 480, 500, 'q', 1)",
                f"({_ts(5)}, {_ts(0)}, 'sliding', '{TENANT}', 'avg_ms', 41.5, 500, 'q', 1)",
            ],
        ),
        _ins(
            "aggregated_pii_metrics",
            f"{base}, destination_model, {tail}",
            [
                f"({_ts(5)}, {_ts(0)}, 'tumbling', '{TENANT}', 'model-a', 'pii_entities', 40, 30, 'q', 1)",  # noqa: E501
                f"({_ts(5)}, {_ts(0)}, 'tumbling', '{TENANT}', 'model-b', 'pii_entities', 2, 3, 'q', 1)",  # noqa: E501
            ],
        ),
        _ins("aggregated_agent_risk_metrics", f"{base}, agent_id, {tail}", risk),
        "insert into pipeline_progress (query_name, batch_id, input_rows) values ('validate', 1, 5)",  # noqa: E501
    ]
    asyncio.run(_exec("; ".join(sql)))


@pytest.fixture(autouse=True)
def seeded(clean_db):
    asyncio.run(_exec(f"TRUNCATE {TABLES}"))
    _seed()


@pytest.fixture
async def api():
    settings = ReportingSettings(environment="test", reporting_database_url=TEST_DATABASE_URL)
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://r") as c:
        yield c
    await app.state.engine.dispose()


def _h(role="security_analyst", tenant=TENANT):
    tok = create_access_token(subject=uuid.uuid4(), tenant_id=tenant, role=role)
    return {"Authorization": f"Bearer {tok}"}


async def test_requires_token_and_audit_scope(api):
    assert (await api.get("/v1/metrics/requests")).status_code == 401
    r = await api.get("/v1/metrics/requests", headers=_h("developer"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (await api.get("/v1/metrics/overview", headers=bad)).status_code == 401


async def test_requests_series_tenant_totals(api):
    r = await api.get(
        "/v1/metrics/requests?from="
        + (NOW - timedelta(minutes=30)).isoformat().replace("+", "%2B"),
        headers=_h(),
    )  # noqa: E501
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == str(TENANT) and {"from", "to", "as_of"} <= set(body)
    assert len(body["items"]) == 10
    assert all(i["dimensions"] == {"agent_id": NIL} for i in body["items"])
    assert sum(i["value"] for i in body["items"]) == sum(100 + m for m in range(1, 11))
    starts = [i["window_start"] for i in body["items"]]
    assert starts == sorted(starts)


async def test_tenant_isolation(api):
    other = (await api.get("/v1/metrics/requests", headers=_h(tenant=OTHER))).json()
    assert [i["value"] for i in other["items"]] == [999]
    nobody = (await api.get("/v1/metrics/requests", headers=_h(tenant=uuid.uuid4()))).json()
    assert nobody["items"] == []
    r = await api.get(f"/v1/agents/{AGENT}/risk", headers=_h(tenant=OTHER))
    assert r.status_code == 404  # another tenant's agent looks like a missing one


async def test_agent_filter(api):
    body = (await api.get(f"/v1/metrics/requests?agent_id={AGENT}", headers=_h())).json()
    assert sum(i["value"] for i in body["items"]) == sum(range(1, 11))


async def test_keyset_pagination_walks_all_rows_once(api):
    seen, cursor = [], None
    while True:
        q = "/v1/metrics/policies?metric=count&limit=7" + (f"&cursor={cursor}" if cursor else "")
        body = (await api.get(q, headers=_h())).json()
        seen += [(i["window_start"], i["dimensions"]["decision"]) for i in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 20 and len(set(seen)) == 20


async def test_invalid_queries_are_rejected(api):
    h = _h()
    cases = [
        "/v1/metrics/requests?from=2026-11-07T10:00:00Z&to=2026-11-07T09:00:00Z",
        "/v1/metrics/requests?from=2026-10-01T00:00:00Z&to=2026-11-07T00:00:00Z",
        "/v1/metrics/requests?cursor=not-base64!",
        "/v1/metrics/requests?metric=x;drop",
        "/v1/metrics/requests?limit=0",
        "/v1/metrics/sovereignty?destination_jurisdiction=mars",
    ]
    for url in cases:
        assert (await api.get(url, headers=h)).status_code == 422, url


async def test_small_pii_cells_are_suppressed(api):
    items = (await api.get("/v1/metrics/pii", headers=_h())).json()["items"]
    by_model = {i["dimensions"]["destination_model"]: i for i in items}
    assert by_model["model-a"]["value"] == 40 and not by_model["model-a"]["suppressed"]
    hidden = by_model["model-b"]
    assert hidden["suppressed"] and hidden["value"] is None and hidden["event_count"] is None


async def test_overview(api):
    r = await api.get("/v1/metrics/overview", headers=_h())
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["requests_last_5m"] == sum(100 + m for m in range(1, 6))
    assert o["decisions_last_5m"] == {"deny": 50, "allow": 450}
    assert (o["deny_rate"], o["p99_latency_ms"], o["avg_latency_ms"]) == (0.1, 480, 41.5)
    assert o["pii_entities_last_window"] == 42
    assert o["top_risky_agents"][0]["agent_id"] == str(AGENT)
    assert o["top_risky_agents"][0]["anomaly_score"] == 0.7  # latest window, not the older one
    assert o["pipeline_last_batch_at"] is not None


async def test_agents_and_agent_history(api):
    ranked = (await api.get("/v1/metrics/agents", headers=_h())).json()["items"]
    assert [(a["agent_id"], a["anomaly_score"]) for a in ranked] == [(str(AGENT), 0.7)]
    hist = (await api.get(f"/v1/agents/{AGENT}/risk", headers=_h())).json()
    assert [h["anomaly_score"] for h in hist] == [0.2, 0.7]
    assert hist[0]["requests"] == 50 and hist[0]["deny_rate"] == 0.4
    assert (await api.get(f"/v1/agents/{uuid.uuid4()}/risk", headers=_h())).status_code == 404


async def test_ops_endpoints_and_headers(api):
    r = await api.get("/healthz")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    assert (await api.get("/readyz")).json() == {"status": "ready"}
    assert b"reporting_http_requests_total" in (await api.get("/metrics")).content


async def test_openapi_contract_lists_every_endpoint(api):
    paths = set((await api.get("/openapi.json")).json()["paths"])
    expected = {f"/v1/metrics/{p}" for p in (
        "overview", "requests", "policies", "latency", "pii", "injections", "agents",
        "sovereignty",
    )} | {"/v1/agents/{agent_id}/risk", "/healthz", "/readyz"}  # fmt: skip
    assert expected <= paths
