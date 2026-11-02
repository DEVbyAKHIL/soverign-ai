"""Phase 2: per-tenant audit hash chain, review status, request idempotency

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # audit_events is append-only and has no Phase-1 producers, so it is empty here.
    op.add_column("audit_events", sa.Column("tenant_id", UUID(as_uuid=True), nullable=False))
    op.add_column(
        "audit_events",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False),
    )
    op.alter_column("audit_events", "created_at", server_default=None)
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    op.create_unique_constraint("uq_audit_events_tenant_seq", "audit_events", ["tenant_id", "seq"])

    op.add_column(
        "decisions",
        sa.Column("policy_set_version", sa.Text, nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "decisions",
        sa.Column(
            "review_status", sa.Text, nullable=False, server_default=sa.text("'not_required'")
        ),
    )
    op.create_unique_constraint(
        "uq_decisions_tenant_request", "decisions", ["tenant_id", "request_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_decisions_tenant_request", "decisions")
    op.drop_column("decisions", "review_status")
    op.drop_column("decisions", "policy_set_version")
    op.drop_constraint("uq_audit_events_tenant_seq", "audit_events")
    op.drop_index("ix_audit_events_tenant_id", "audit_events")
    op.alter_column("audit_events", "created_at", server_default=sa.text("now()"))
    op.drop_column("audit_events", "seq")
    op.drop_column("audit_events", "tenant_id")
