"""Streaming phase 3: read-only login for the reporting API

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07

reporting_ro may only SELECT the aggregate tables and pipeline_progress: no raw decisions, audit
events, outbox payloads or detections, and no writes. Password from REPORTING_DB_PASSWORD (same
rules as the stream_processor role in 0007); without it the role is created NOLOGIN.
"""

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ROLE = "reporting_ro"
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")
TABLES = (
    "aggregated_request_metrics",
    "aggregated_policy_metrics",
    "aggregated_latency_metrics",
    "aggregated_pii_metrics",
    "aggregated_injection_metrics",
    "aggregated_agent_risk_metrics",
    "aggregated_sovereignty_metrics",
    "pipeline_progress",
)


def upgrade() -> None:
    bind = op.get_bind()
    password = os.environ.get("REPORTING_DB_PASSWORD", "")
    exists = bind.scalar(sa.text("select 1 from pg_roles where rolname = :r"), {"r": ROLE})
    if password:
        if password.startswith("CHANGE_ME") or not PASSWORD_RE.match(password):
            raise ValueError("REPORTING_DB_PASSWORD must be 16-128 chars of [A-Za-z0-9_-]")
        verb = "ALTER" if exists else "CREATE"
        # Validated above to a safe character set; DDL cannot take bind parameters.
        op.execute(f"{verb} ROLE {ROLE} LOGIN PASSWORD '{password}'")  # noqa: S608  # nosec B608
    elif not exists:
        op.execute(f"CREATE ROLE {ROLE} NOLOGIN")
    db = bind.scalar(sa.text("select current_database()"))
    op.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {ROLE}')
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")


def downgrade() -> None:
    db = op.get_bind().scalar(sa.text("select current_database()"))
    for table in TABLES:
        op.execute(f"REVOKE SELECT ON {table} FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(f'REVOKE CONNECT ON DATABASE "{db}" FROM {ROLE}')
