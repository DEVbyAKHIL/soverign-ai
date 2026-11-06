"""Phase 5: agent behaviour events

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # agent_events has no producers before this phase, so NOT NULL columns can be added directly.
    op.add_column("agent_events", sa.Column("tenant_id", UUID(as_uuid=True), nullable=False))
    op.add_column("agent_events", sa.Column("decision_id", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "agent_events",
        sa.Column("datasets", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "agent_events",
        sa.Column("tool_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "agent_events",
        sa.Column("content_hash", sa.Text, nullable=False, server_default=sa.text("''")),
    )
    op.create_index("ix_agent_events_agent_created", "agent_events", ["agent_id", "created_at"])
    op.create_index("ix_agent_events_tenant_id", "agent_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_events_tenant_id", "agent_events")
    op.drop_index("ix_agent_events_agent_created", "agent_events")
    for column in ("content_hash", "tool_calls", "datasets", "decision_id", "tenant_id"):
        op.drop_column("agent_events", column)
