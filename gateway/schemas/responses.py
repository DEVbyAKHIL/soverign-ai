import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DetectionOut(BaseModel):
    entity_type: str
    confidence: float
    detector: str
    model_version: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    part: int = 0
    risk_level: str | None = None


class IntentOut(BaseModel):
    label: str
    confidence: float
    risk_score: float
    signals: list[str]


class ContentVerdict(BaseModel):
    part: int
    source: str  # direct | indirect
    label: str
    risk_score: float
    signals: list[str]
    doc_id: str | None = None


class AgentAnomalyOut(BaseModel):
    agent_id: str
    anomaly_score: float
    is_anomalous: bool
    reasons: list[str]
    method: str
    model_version: str
    features: dict[str, float]
    baseline_events: int
    requests_per_minute: float


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    agent_type: str
    owner_id: uuid.UUID
    allowed_models: list[str]
    allowed_tools: list[str]
    max_requests_per_minute: int
    risk_score: float
    created_at: datetime


class AgentRiskOut(BaseModel):
    agent: AgentOut
    risk_score: float  # long-running (EWMA of anomaly scores)
    current: AgentAnomalyOut  # computed now over the last 10 minutes
    events_7d: int
    denied_7d: int
    sensitive_fields_7d: int
    last_seen: datetime | None


class DecisionResponse(BaseModel):
    decision_id: uuid.UUID
    request_id: uuid.UUID
    final_decision: str
    action_taken: str
    risk_score: float
    explanation: str
    matched_policies: list[str]
    side_effects: list[str]
    transforms: list[str]
    route_to: dict[str, str] | None
    review_status: str
    policy_set_version: str
    data_classification: str  # effective classification after detection
    intent: IntentOut  # verdict for the caller's own input (prompt / query)
    content_verdicts: list[ContentVerdict]  # every part, including attachments / documents
    agent: AgentAnomalyOut | None = None  # behaviour verdict when the request involves an agent
    detections: list[DetectionOut]
    # The content as it may be forwarded after redaction/masking/pseudonymisation.
    transformed_content: dict[str, Any] | None = None
    audit_event_id: uuid.UUID
    # Forwarding outcome: status ok | blocked | error | not_forwarded (+ reason). For chat: the
    # guarded answer and the output-guard verdict; for embed: the vectors.
    model_response: dict[str, Any] | None = None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    seq: int
    decision_id: uuid.UUID
    event_type: str
    actor_id: uuid.UUID
    event_data: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class DecisionDetail(BaseModel):
    decision_id: uuid.UUID
    request_id: uuid.UUID
    tenant_id: uuid.UUID
    caller_id: uuid.UUID
    agent_id: uuid.UUID | None
    destination_model: str
    destination_jurisdiction: str
    declared_purpose: str
    input_risk_score: float
    agent_anomaly_score: float
    final_decision: str
    action_taken: str
    policy_ids: list[str]
    policy_set_version: str
    model_versions: dict[str, Any]
    explanation: str
    review_status: str
    created_at: datetime
    detections: list[DetectionOut]
    audit_events: list[AuditEventOut]


class ChainVerificationOut(BaseModel):
    valid: bool
    events_checked: int
    first_invalid_seq: int | None
    reason: str | None


class PolicyOut(BaseModel):
    policy_id: str
    version: str
    name: str
    description: str
    severity: str
    match: dict[str, Any]
    actions: list[str]
    route_to: dict[str, str] | None
    audit: dict[str, Any]
    sha256: str


class PolicyListOut(BaseModel):
    policy_set_version: str
    policies: list[PolicyOut]


class PolicyValidateOut(BaseModel):
    valid: bool
    errors: list[str]
    policy_id: str | None = None
    version: str | None = None
    version_bump_required: bool = False
