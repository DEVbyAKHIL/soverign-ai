"""Streaming phase 2: aggregate store for Spark Structured Streaming + processor role

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07

Every aggregate table is long-format (one row per window x dimensions x metric) with a primary
key over exactly those columns, so the processor's INSERT ... ON CONFLICT DO UPDATE is idempotent:
replaying a micro-batch after a restart overwrites rows with the same values instead of adding new
ones. Dimension columns are NOT NULL; "all agents" uses the nil UUID and "all decisions" uses '*'.
"""

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES: dict[str, list[tuple[str, sa.types.TypeEngine]]] = {
    "aggregated_request_metrics": [("agent_id", UUID(as_uuid=True))],
    "aggregated_policy_metrics": [("decision", sa.Text())],
    "aggregated_latency_metrics": [],
    "aggregated_pii_metrics": [("destination_model", sa.Text())],
    "aggregated_injection_metrics": [("agent_id", UUID(as_uuid=True))],
    "aggregated_agent_risk_metrics": [("agent_id", UUID(as_uuid=True))],
    "aggregated_sovereignty_metrics": [
        ("destination_jurisdiction", sa.Text()),
        ("sensitivity_level", sa.Text()),
    ],
}
ROLE = "stream_processor"
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")


def _ts(name: str, **kw):
    return sa.Column(name, TIMESTAMP(timezone=True), nullable=False, **kw)


def upgrade() -> None:
    for table, dims in TABLES.items():
        op.create_table(
            table,
            _ts("window_start"),
            _ts("window_end"),
            sa.Column("window_type", sa.Text, nullable=False),
            sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
            *[sa.Column(name, type_, nullable=False) for name, type_ in dims],
            sa.Column("metric", sa.Text, nullable=False),
            sa.Column("value", sa.Float(precision=53), nullable=False),
            sa.Column("event_count", sa.BigInteger, nullable=False),
            sa.Column("query_name", sa.Text, nullable=False),
            sa.Column("batch_id", sa.BigInteger, nullable=False),
            _ts("processed_at", server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint(
                "window_start",
                "window_end",
                "tenant_id",
                *[name for name, _ in dims],
                "metric",
                name=f"pk_{table}",
            ),
            sa.CheckConstraint("window_end > window_start", name=f"ck_{table}_window"),
            sa.CheckConstraint("window_type in ('tumbling', 'sliding')", name=f"ck_{table}_type"),
        )
        op.create_index(f"ix_{table}_tenant_window", table, ["tenant_id", "window_start"])

    # One row per (query, micro-batch). A replayed batch has the same batch_id and offsets, so the
    # upsert keeps totals exact across processor restarts.
    op.create_table(
        "pipeline_progress",
        sa.Column("query_name", sa.Text, nullable=False),
        sa.Column("batch_id", sa.BigInteger, nullable=False),
        sa.Column("input_rows", sa.BigInteger, nullable=False),
        sa.Column("valid_rows", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("invalid_rows", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("output_rows", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        _ts("processed_at", server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("query_name", "batch_id", name="pk_pipeline_progress"),
    )
    # Per producer run (Kafka header run_id): lets verification count one run's events exactly
    # while other traffic (gateway, continuous producer) shares the topic.
    op.create_table(
        "pipeline_run_counts",
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("batch_id", sa.BigInteger, nullable=False),
        sa.Column("consumed", sa.BigInteger, nullable=False),
        sa.Column("valid", sa.BigInteger, nullable=False),
        sa.Column("invalid", sa.BigInteger, nullable=False),
        _ts("processed_at", server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("run_id", "batch_id", name="pk_pipeline_run_counts"),
    )

    # Least-privilege login for the Spark processor: write aggregates and progress, nothing else.
    password = os.environ.get("PROCESSOR_DB_PASSWORD", "")
    exists = op.get_bind().scalar(sa.text("select 1 from pg_roles where rolname = :r"), {"r": ROLE})
    if password:
        if password.startswith("CHANGE_ME") or not PASSWORD_RE.match(password):
            raise ValueError(
                "PROCESSOR_DB_PASSWORD must be 16-128 chars of [A-Za-z0-9_-], not the placeholder"
            )
        verb = "ALTER" if exists else "CREATE"
        # Validated above to a safe character set; DDL cannot take bind parameters.
        op.execute(f"{verb} ROLE {ROLE} LOGIN PASSWORD '{password}'")  # noqa: S608  # nosec B608
    elif not exists:
        op.execute(f"CREATE ROLE {ROLE} NOLOGIN")
    db = op.get_bind().scalar(sa.text("select current_database()"))
    op.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {ROLE}')
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in (*TABLES, "pipeline_progress", "pipeline_run_counts"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {ROLE}")


def downgrade() -> None:
    for table in ("pipeline_run_counts", "pipeline_progress", *TABLES):
        op.drop_table(table)
    db = op.get_bind().scalar(sa.text("select current_database()"))
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(f'REVOKE CONNECT ON DATABASE "{db}" FROM {ROLE}')
