"""SQLAlchemy 2.0 ORM models. Schema mirrors migrations/versions/0001_initial.py."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.db import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _uuid(nullable: bool = False, index: bool = False) -> Mapped:
    return mapped_column(UUID(as_uuid=True), nullable=nullable, index=index)


def _ts() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


def _text_array() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))


def _jsonb() -> Mapped[dict]:
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _uuid(index=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _ts()


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _uuid(index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = _uuid()
    agent_type: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_models: Mapped[list[str]] = _text_array()
    allowed_tools: Mapped[list[str]] = _text_array()
    max_requests_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    risk_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AIAsset(Base):
    __tablename__ = "ai_assets"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _uuid(index=True)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity_level: Mapped[str] = mapped_column(Text, nullable=False)
    approved_purposes: Mapped[list[str]] = _text_array()
    owner_id: Mapped[uuid.UUID] = _uuid()
    # "metadata" is reserved on declarative classes, so the attribute is renamed.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _ts()


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[uuid.UUID] = _pk()
    policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    policy_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[uuid.UUID] = _uuid()
    created_at: Mapped[datetime] = _ts()


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = _uuid(index=True)
    tenant_id: Mapped[uuid.UUID] = _uuid(index=True)
    caller_id: Mapped[uuid.UUID] = _uuid()
    agent_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    destination_model: Mapped[str] = mapped_column(Text, nullable=False)
    destination_jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    declared_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    input_risk_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    agent_anomaly_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    final_decision: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    policy_ids: Mapped[list[str]] = _text_array()
    model_versions: Mapped[dict] = _jsonb()
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    policy_set_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    intent_label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'benign'"))
    indirect_intent_label: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'benign'")
    )
    # not_required | pending | approved | rejected
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_required'")
    )
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name="uq_decisions_tenant_request"),
    )


class Detection(Base):
    __tablename__ = "detections"
    id: Mapped[uuid.UUID] = _pk()
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decisions.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_value_hash: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    detector: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    part: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'low'"))
    created_at: Mapped[datetime] = _ts()


class AuditEvent(Base):
    """Append-only; UPDATE/DELETE are rejected by a database trigger.

    Events form one hash chain per tenant, ordered by ``seq``.
    """

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = _uuid(index=True)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decisions.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = _uuid()
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Set by the application (not the DB) so the hashed value is exactly what is stored.
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "seq", name="uq_audit_events_tenant_seq"),)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[uuid.UUID] = _pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    request_id: Mapped[uuid.UUID] = _uuid()
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_processed: Mapped[int] = mapped_column(Integer, nullable=False)
    sensitive_fields_detected: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
