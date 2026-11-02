"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _id():
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created(name: str = "created_at"):
    return sa.Column(
        name, TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _arr(name: str):
    return sa.Column(name, ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"))


def _uuid(name: str, nullable: bool = False):
    return sa.Column(name, UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        _uuid("tenant_id"),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _created(),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "agents",
        _id(),
        _uuid("tenant_id"),
        sa.Column("name", sa.Text, nullable=False),
        _uuid("owner_id"),
        sa.Column("agent_type", sa.Text, nullable=False),
        _arr("allowed_models"),
        _arr("allowed_tools"),
        sa.Column(
            "max_requests_per_minute", sa.Integer, nullable=False, server_default=sa.text("60")
        ),
        sa.Column("risk_score", sa.Numeric, nullable=False, server_default=sa.text("0")),
        _created(),
        _created("updated_at"),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])

    op.create_table(
        "ai_assets",
        _id(),
        _uuid("tenant_id"),
        sa.Column("asset_type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("jurisdiction", sa.Text, nullable=False),
        sa.Column("sensitivity_level", sa.Text, nullable=False),
        _arr("approved_purposes"),
        _uuid("owner_id"),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created(),
    )
    op.create_index("ix_ai_assets_tenant_id", "ai_assets", ["tenant_id"])

    op.create_table(
        "policies",
        _id(),
        sa.Column("policy_id", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("policy_yaml", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _uuid("created_by"),
        _created(),
        sa.UniqueConstraint("policy_id", "version", name="uq_policies_policy_id_version"),
    )

    op.create_table(
        "decisions",
        _id(),
        _uuid("request_id"),
        _uuid("tenant_id"),
        _uuid("caller_id"),
        _uuid("agent_id", nullable=True),
        sa.Column("destination_model", sa.Text, nullable=False),
        sa.Column("destination_jurisdiction", sa.Text, nullable=False),
        sa.Column("declared_purpose", sa.Text, nullable=False),
        sa.Column("input_risk_score", sa.Numeric, nullable=False),
        sa.Column("agent_anomaly_score", sa.Numeric, nullable=False),
        sa.Column("final_decision", sa.Text, nullable=False),
        sa.Column("action_taken", sa.Text, nullable=False),
        _arr("policy_ids"),
        sa.Column("model_versions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("explanation", sa.Text, nullable=False),
        _created(),
    )
    op.create_index("ix_decisions_tenant_id", "decisions", ["tenant_id"])
    op.create_index("ix_decisions_request_id", "decisions", ["request_id"])

    op.create_table(
        "detections",
        _id(),
        sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_value_hash", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric, nullable=False),
        sa.Column("detector", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        _created(),
    )
    op.create_index("ix_detections_decision_id", "detections", ["decision_id"])

    op.create_table(
        "audit_events",
        _id(),
        sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        _uuid("actor_id"),
        sa.Column("event_data", JSONB, nullable=False),
        sa.Column("previous_hash", sa.Text, nullable=False),
        sa.Column("event_hash", sa.Text, nullable=False, unique=True),
        _created(),
    )
    op.create_index("ix_audit_events_decision_id", "audit_events", ["decision_id"])

    # Tamper resistance: audit_events is append-only at the database level.
    op.execute(
        """
        CREATE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
        """
    )

    op.create_table(
        "agent_events",
        _id(),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        _uuid("request_id"),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("tokens_processed", sa.Integer, nullable=False),
        sa.Column("sensitive_fields_detected", sa.Integer, nullable=False),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _created(),
    )
    op.create_index("ix_agent_events_agent_id", "agent_events", ["agent_id"])


def downgrade() -> None:
    op.drop_table("agent_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_mutation()")
    for table in (
        "audit_events",
        "detections",
        "decisions",
        "policies",
        "ai_assets",
        "agents",
        "users",
    ):
        op.drop_table(table)
