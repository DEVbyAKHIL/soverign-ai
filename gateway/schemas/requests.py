import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CallerType = Literal["user", "service", "agent"]
Jurisdiction = Literal["eu", "non_eu", "unknown"]
Classification = Literal["public", "internal", "confidential", "restricted", "high_risk"]

MAX_PROMPT_CHARS = 32_000


class ToolCall(BaseModel):
    """A tool the agent intends to call in this request (checked against its allow-list)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=50)


class AIRequest(BaseModel):
    """Shared request envelope for inference and embedding (FR-2)."""

    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    tenant_id: uuid.UUID
    caller_type: CallerType
    caller_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    requested_model: str = Field(min_length=1, max_length=200)
    requested_endpoint: str = Field(min_length=1, max_length=2048)
    destination_jurisdiction: Jurisdiction
    declared_purpose: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")
    # Unlabelled data is treated as confidential until detection proves otherwise.
    data_classification: Classification = "confidential"
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=50)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _agent_requires_agent_id(self) -> "AIRequest":
        if self.caller_type == "agent" and self.agent_id is None:
            raise ValueError("agent_id is required when caller_type is 'agent'")
        return self


class InferRequest(AIRequest):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    attached_content: list[str] = Field(default_factory=list, max_length=20)


class EmbedRequest(AIRequest):
    input: list[str] = Field(min_length=1, max_length=100)


class RetrievedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)
    classification: Classification | None = None
    # Defaults to the source's host (URLs) or its path without the file name.
    dataset: str | None = Field(default=None, max_length=200)


class RagQueryRequest(AIRequest):
    """A query plus the documents your retriever returned. The gateway inspects both: the query
    as direct input, each document as indirect (untrusted) input."""

    query: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    documents: list[RetrievedDocument] = Field(default_factory=list, max_length=50)


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    agent_type: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_-]+$")
    # Least privilege: an agent may only use the models and tools listed here.
    allowed_models: list[str] = Field(min_length=1, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    max_requests_per_minute: int = Field(default=60, ge=1, le=10_000)


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class PolicyValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_yaml: str = Field(min_length=1, max_length=64 * 1024)
