"""Stable response schemas (part of the public contract; see docs/api.md)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    window_start: datetime
    window_end: datetime
    window_type: str = Field(description="tumbling | sliding")
    dimensions: dict[str, str] = Field(description="e.g. agent_id, decision, destination_model")
    metric: str
    value: float | None = Field(description="null when suppressed")
    event_count: int | None = Field(description="events behind the value; null when suppressed")
    suppressed: bool = False


class MetricSeries(BaseModel):
    tenant_id: uuid.UUID
    range_from: datetime = Field(serialization_alias="from")
    range_to: datetime = Field(serialization_alias="to")
    as_of: datetime | None = Field(description="latest processed_at of the returned rows")
    items: list[MetricPoint]
    next_cursor: str | None = Field(description="pass as ?cursor= for the next page")


class AgentRisk(BaseModel):
    agent_id: uuid.UUID
    window_start: datetime
    window_end: datetime
    anomaly_score: float
    requests: int
    deny_rate: float | None
    injection_rate: float | None
    pii_rate: float | None
    risky_destination_share: float | None


class AgentRiskList(BaseModel):
    tenant_id: uuid.UUID
    range_from: datetime = Field(serialization_alias="from")
    range_to: datetime = Field(serialization_alias="to")
    items: list[AgentRisk]


class Overview(BaseModel):
    tenant_id: uuid.UUID
    as_of: datetime | None = Field(description="when the processor last wrote this tenant's data")
    latest_window_end: datetime | None
    requests_last_5m: int
    requests_per_minute: float
    decisions_last_5m: dict[str, int]
    deny_rate: float | None = Field(description="latest 5-minute sliding window")
    failed_evaluations: int | None
    avg_latency_ms: float | None
    p99_latency_ms: float | None
    pii_entities_last_window: int | None = Field(description="latest 5-minute tumbling window")
    injection_attempts: int | None = Field(description="latest 5-minute sliding window, agents")
    top_risky_agents: list[AgentRisk]
    pipeline_last_batch_at: datetime | None = Field(description="processor liveness (any tenant)")
