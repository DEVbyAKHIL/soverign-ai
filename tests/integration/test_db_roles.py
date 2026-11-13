"""Per-service database roles hold only the privileges they need (migrations 0007-0009)."""

import pytest

from tests.conftest import raw_sql

pytestmark = pytest.mark.usefixtures("migrated_db")


async def _grants(role: str) -> dict[str, set[str]]:
    rows = await raw_sql(
        "select table_name, privilege_type from information_schema.role_table_grants "
        "where grantee = $1",
        role,
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["table_name"], set()).add(r["privilege_type"])
    return out


async def test_gateway_app_cannot_delete_or_read_aggregates():
    g = await _grants("gateway_app")
    assert g["decisions"] == {"SELECT", "INSERT", "UPDATE"}
    assert g["audit_events"] == {"SELECT", "INSERT"}  # append only, never UPDATE
    assert g["event_outbox"] == {"SELECT", "INSERT"}
    assert g["detections"] == {"SELECT", "INSERT"}
    assert not any("DELETE" in p for p in g.values())
    assert not any(t.startswith("aggregated_") or t.startswith("pipeline_") for t in g)


async def test_outbox_publisher_touches_only_the_outbox():
    assert await _grants("outbox_publisher") == {"event_outbox": {"SELECT", "UPDATE"}}


async def test_retention_job_deletes_but_never_updates_audit():
    g = await _grants("retention_job")
    assert {"SELECT", "DELETE"} <= g["audit_events"] and "UPDATE" not in g["audit_events"]
    assert {"SELECT", "DELETE"} <= g["decisions"] and "INSERT" not in g["decisions"]
    assert g["retention_runs"] == {"SELECT", "INSERT"}
    assert "users" not in g and "policies" not in g


async def test_read_and_write_paths_for_analytics_are_separate():
    reporting, processor = await _grants("reporting_ro"), await _grants("stream_processor")
    assert all(p == {"SELECT"} for p in reporting.values())
    assert "decisions" not in reporting and "decisions" not in processor
    assert not any("DELETE" in p for p in processor.values())
