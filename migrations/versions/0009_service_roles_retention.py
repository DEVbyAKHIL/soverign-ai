"""Hardening: per-service database roles, retention support

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-07

Login roles (passwords from the migrate service's environment; without one a role is NOLOGIN):

| role              | env var                  | privileges                                              |
|-------------------|--------------------------|---------------------------------------------------------|
| gateway_app       | GATEWAY_DB_PASSWORD      | read app tables; INSERT decisions/detections/audit/     |
|                   |                          | agent events/outbox/agents; UPDATE decisions, agents    |
| outbox_publisher  | PUBLISHER_DB_PASSWORD    | SELECT, UPDATE on event_outbox only                     |
| retention_job     | RETENTION_DB_PASSWORD    | SELECT, DELETE on retained tables; anchors; run log     |
| stream_processor  | PROCESSOR_DB_PASSWORD    | (0007) write aggregates                                 |
| reporting_ro      | REPORTING_DB_PASSWORD    | (0008) read aggregates                                  |

No service role can DELETE except retention_job, and no role can UPDATE audit_events. The
append-only trigger now permits DELETE only inside a transaction that sets
app.audit_retention = 'on' (the retention job does, after recording a chain anchor), so pruning the
oldest audit events keeps every remaining chain verifiable.
"""

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

PASSWORD_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")
ROLES = {
    "gateway_app": "GATEWAY_DB_PASSWORD",
    "outbox_publisher": "PUBLISHER_DB_PASSWORD",
    "retention_job": "RETENTION_DB_PASSWORD",
}
APP_TABLES = (
    "users", "agents", "ai_assets", "policies", "decisions", "detections",
    "audit_events", "agent_events", "event_outbox", "audit_chain_anchors",
)  # fmt: skip
AGGREGATES = (
    "aggregated_request_metrics", "aggregated_policy_metrics", "aggregated_latency_metrics",
    "aggregated_pii_metrics", "aggregated_injection_metrics", "aggregated_agent_risk_metrics",
    "aggregated_sovereignty_metrics", "pipeline_progress", "pipeline_run_counts",
)  # fmt: skip
RETAINED = (
    "decisions", "detections", "audit_events", "agent_events", "event_outbox", *AGGREGATES,
)  # fmt: skip


def _login_role(bind, role: str, env: str) -> None:
    password = os.environ.get(env, "")
    exists = bind.scalar(sa.text("select 1 from pg_roles where rolname = :r"), {"r": role})
    if password:
        if password.startswith("CHANGE_ME") or not PASSWORD_RE.match(password):
            raise ValueError(f"{env} must be 16-128 chars of [A-Za-z0-9_-], not the placeholder")
        verb = "ALTER" if exists else "CREATE"
        op.execute(f"{verb} ROLE {role} LOGIN PASSWORD '{password}'")  # nosec B608 - validated
    elif not exists:
        op.execute(f"CREATE ROLE {role} NOLOGIN")


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "audit_chain_anchors",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("anchor_seq", sa.BigInteger, nullable=False),
        sa.Column("anchor_hash", sa.Text, nullable=False),
        sa.Column("pruned_events", sa.BigInteger, nullable=False),
        sa.Column("pruned_before", TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_table(
        "retention_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("cutoff", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rows_deleted", sa.BigInteger, nullable=False),
        sa.Column("dry_run", sa.Boolean, nullable=False),
        sa.Column(
            "finished_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    # Retention scans by age; these keep the deletes index-driven.
    for table in ("decisions", "detections", "audit_events", "agent_events"):
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    op.create_index("ix_event_outbox_published_at", "event_outbox", ["published_at"])
    op.create_index("ix_audit_events_tenant_seq", "audit_events", ["tenant_id", "seq"])

    # Append-only stays the rule; the single exception is an explicit retention transaction.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('app.audit_retention', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    for role, env in ROLES.items():
        _login_role(bind, role, env)
    db = bind.scalar(sa.text("select current_database()"))
    for role in ROLES:
        op.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {role}')
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")

    # gateway_app: everything the request path needs, nothing it does not (no DELETE, no
    # aggregates, no UPDATE on audit/detections/outbox).
    for t in APP_TABLES:
        op.execute(f"GRANT SELECT ON {t} TO gateway_app")
    for t in ("agents", "decisions", "detections", "audit_events", "agent_events", "event_outbox"):
        op.execute(f"GRANT INSERT ON {t} TO gateway_app")
    for t in ("agents", "decisions"):
        op.execute(f"GRANT UPDATE ON {t} TO gateway_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO gateway_app")

    op.execute("GRANT SELECT, UPDATE ON event_outbox TO outbox_publisher")

    for t in RETAINED:
        op.execute(f"GRANT SELECT, DELETE ON {t} TO retention_job")
    op.execute("GRANT SELECT, INSERT, UPDATE ON audit_chain_anchors TO retention_job")
    op.execute("GRANT SELECT, INSERT ON retention_runs TO retention_job")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO retention_job")


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.drop_index("ix_audit_events_tenant_seq", "audit_events")
    op.drop_index("ix_event_outbox_published_at", "event_outbox")
    for table in ("decisions", "detections", "audit_events", "agent_events"):
        op.drop_index(f"ix_{table}_created_at", table)
    op.drop_table("retention_runs")
    op.drop_table("audit_chain_anchors")
    db = op.get_bind().scalar(sa.text("select current_database()"))
    for role in ROLES:
        op.execute(f"REASSIGN OWNED BY {role} TO CURRENT_USER")
        op.execute(f"DROP OWNED BY {role}")
        op.execute(f'REVOKE CONNECT ON DATABASE "{db}" FROM {role}')
