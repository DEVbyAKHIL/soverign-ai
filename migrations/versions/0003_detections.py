"""Phase 3: detection part index and risk level

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "detections", sa.Column("part", sa.Integer, nullable=False, server_default=sa.text("0"))
    )
    op.add_column(
        "detections",
        sa.Column("risk_level", sa.Text, nullable=False, server_default=sa.text("'low'")),
    )


def downgrade() -> None:
    op.drop_column("detections", "risk_level")
    op.drop_column("detections", "part")
