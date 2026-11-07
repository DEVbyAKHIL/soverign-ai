"""Deterministic synthetic governance events that follow the gateway's decision logic.

Shapes, not real data: tenants, agents and callers are random UUIDs derived from the seed; the
decision for each event is derived from its PII count, jurisdiction and injection score the same
way the default policy set decides (docs/aggregations.md explains the distributions).
"""

import math
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from events.builder import ACTION_TO_DECISION, BLOCKING
from events.schema import SCHEMA_VERSION, format_ts

# (model, jurisdiction, weight). Logical destinations as the policies see them.
MODELS = (
    ("local-llama-3.2-3b", "eu", 0.35),
    ("eu-hosted-llm-large", "eu", 0.25),
    ("us-hosted-llm-large", "non_eu", 0.25),
    ("us-hosted-embedding", "non_eu", 0.10),
    ("unregistered-endpoint", "unknown", 0.05),
)
PURPOSES = (
    "customer_support_summarisation",
    "code_assistance",
    "document_search",
    "marketing_copy",
    "hr_screening",
    "financial_analysis",
)
POLICY_FOR_ACTION = {
    "deny": "block_pii_to_non_eu_models@1.2.0",
    "quarantine": "quarantine_anomalous_agents@1.0.0",
    "require_human_review": "review_high_risk_purposes@1.0.0",
    "route_to_approved_model": "route_confidential_to_eu_models@1.0.0",
    "pseudonymise": "pseudonymise_customer_pii@1.1.0",
    "redact": "redact_secrets@1.0.0",
    "mask": "mask_financial_ids@1.0.0",
    "allow": "builtin.default_allow",
}
FINAL = {
    "deny": "deny",
    "quarantine": "deny",
    "require_human_review": "review",
    "route_to_approved_model": "allow_with_modification",
    "pseudonymise": "allow_with_modification",
    "redact": "allow_with_modification",
    "mask": "allow_with_modification",
    "allow": "allow",
}
LOW_SENSITIVITY = ("public", "internal", "confidential")
PII_SENSITIVITY = ("confidential", "restricted", "high_risk")


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    tenant_id: str
    weight: float
    anomalous: bool
    model_idx: int
    purpose: str


class EventGenerator:
    def __init__(
        self,
        seed: int = 42,
        tenants: int = 5,
        agents: int = 40,
        run_id: str | None = None,
        tenant_ids: list[str] | None = None,
    ) -> None:
        self.rng = random.Random(seed)  # noqa: S311  # nosec B311 - synthetic data
        self.run_id = run_id
        self.tenants = [self._uuid() for _ in range(tenants)]
        if tenant_ids:  # fixed tenants, e.g. the dashboard's demo tenant
            self.tenants = [str(uuid.UUID(t)) for t in tenant_ids]
        self.callers = {t: [self._uuid() for _ in range(20)] for t in self.tenants}
        self.agents = [
            AgentProfile(
                agent_id=self._uuid(),
                tenant_id=self.rng.choice(self.tenants),
                weight=self.rng.lognormvariate(0, 0.8),
                anomalous=self.rng.random() < 0.1,
                model_idx=self.rng.randrange(len(MODELS)),
                purpose=self.rng.choice(PURPOSES),
            )
            for _ in range(agents)
        ]
        self._agent_cum = _cumulative([a.weight for a in self.agents])
        self._model_cum = _cumulative([m[2] for m in MODELS])

    def _uuid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def event(self, ts: datetime) -> dict[str, Any]:
        rng = self.rng
        r = rng.random()
        caller_type = "agent" if r < 0.55 else ("user" if r < 0.90 else "service")
        anomalous = False
        if caller_type == "agent":
            agent = self.agents[_pick(self._agent_cum, rng.random())]
            tenant, agent_id, purpose = agent.tenant_id, agent.agent_id, agent.purpose
            anomalous = agent.anomalous and rng.random() < 0.3
            model_idx = agent.model_idx
            if rng.random() >= 0.7:
                model_idx = _pick(self._model_cum, rng.random())
            if anomalous and rng.random() < 0.5:
                model_idx = rng.choice((2, 3, 4))  # anomalous agents drift to non-EU / unknown
        else:
            tenant, agent_id = rng.choice(self.tenants), None
            purpose = rng.choice(PURPOSES)
            model_idx = _pick(self._model_cum, rng.random())
        model, jurisdiction, _ = MODELS[model_idx]

        pii = 0
        if rng.random() < (0.6 if anomalous else 0.22):
            pii = 1 + int(rng.expovariate(0.6 if anomalous else 1.2))
        attack = rng.random() < (0.25 if anomalous else 0.02)
        injection = rng.uniform(0.7, 0.99) if attack else rng.betavariate(1.2, 14)
        sensitivity = rng.choice(PII_SENSITIVITY if pii else LOW_SENSITIVITY)

        if injection >= 0.8:
            action = "quarantine" if anomalous else "deny"
        elif jurisdiction == "unknown" or (pii and jurisdiction == "non_eu"):
            action = "deny"
        elif injection >= 0.6 or sensitivity == "high_risk":
            action = "require_human_review"
        elif pii:
            action = rng.choice(("pseudonymise", "pseudonymise", "redact", "mask"))
        elif sensitivity == "confidential" and jurisdiction == "non_eu":
            action = "route_to_approved_model"
        else:
            action = "allow"

        latency = rng.lognormvariate(math.log(35), 0.55) + 4 * pii
        if rng.random() < 0.005:
            latency *= 12  # tail spikes
        tokens = rng.lognormvariate(math.log(380), 0.9) * (5 if anomalous else 1)
        status = "blocked" if action in BLOCKING else "completed"
        if status == "completed" and rng.random() < 0.004:
            status, latency = "failed", latency + 2000
        metadata: dict[str, Any] = {
            "source": "producer",
            "synthetic": True,
            "endpoint": "infer" if r < 0.7 else ("embed" if rng.random() < 0.5 else "rag"),
            "final_decision": FINAL[action],
            "intent_label": "prompt_injection" if injection >= 0.6 else "benign",
            "review_status": "pending" if action == "require_human_review" else "not_required",
        }
        if self.run_id:
            metadata["run_id"] = self.run_id
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": self._uuid(),
            "event_timestamp": format_ts(ts),
            "tenant_id": tenant,
            "agent_id": agent_id,
            "caller_type": caller_type,
            "caller_id": rng.choice(self.callers[tenant]),
            "event_type": "policy_decision",
            "decision": ACTION_TO_DECISION[action],
            "action": action,
            "destination_model": model,
            "destination_jurisdiction": jurisdiction,
            "declared_purpose": purpose,
            "sensitivity_level": sensitivity,
            "pii_count": pii,
            "injection_risk_score": round(injection, 4),
            "policy_ids": [POLICY_FOR_ACTION[action]],
            "latency_ms": int(latency),
            "tokens_processed": int(tokens),
            "status": status,
            "metadata": metadata,
        }

    def backfill_timestamp(
        self, i: int, total: int, start: datetime, span: timedelta, disorder_s: float
    ) -> datetime:
        """Evenly spread over [start, start+span] with bounded disorder (|jitter| <= disorder_s)."""
        base = start + span * (i / max(1, total))
        jitter = timedelta(seconds=self.rng.uniform(-disorder_s, disorder_s))
        return min(max(base + jitter, start), start + span)


def _cumulative(weights: list[float]) -> list[float]:
    total, out = 0.0, []
    for w in weights:
        total += w
        out.append(total)
    return [c / total for c in out]


def _pick(cum: list[float], r: float) -> int:
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < r:
            lo = mid + 1
        else:
            hi = mid
    return lo
