"""Streaming phase 1: transactional outbox for governance events

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _now(name: str):
    return sa.Column(
        name, TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("partition_key", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        _now("created_at"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        _now("next_attempt_at"),
        sa.Column("published_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    # The publisher only ever scans unpublished rows that are due; keep that index tiny.
    op.create_index(
        "ix_event_outbox_due",
        "event_outbox",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_event_outbox_due", "event_outbox")
    op.drop_table("event_outbox")
