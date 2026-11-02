"""Phase 4: intent labels on decisions

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("intent_label", "indirect_intent_label"):
        op.add_column(
            "decisions",
            sa.Column(column, sa.Text, nullable=False, server_default=sa.text("'benign'")),
        )
    op.create_index("ix_decisions_tenant_intent", "decisions", ["tenant_id", "intent_label"])


def downgrade() -> None:
    op.drop_index("ix_decisions_tenant_intent", "decisions")
    op.drop_column("decisions", "indirect_intent_label")
    op.drop_column("decisions", "intent_label")
