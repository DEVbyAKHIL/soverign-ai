"""Output guardrails: inspect a model's answer before it is returned to the caller.

* Sensitive data the model emits (PII, financial identifiers) is redacted.
* An answer that leaks a secret (API key, password), discloses the gateway's system prompt, or
  carries an attack payload aimed at the next consumer ("ignore previous instructions") is blocked.
The verdict is audited; the raw answer never is (only its hash).
"""

from dataclasses import dataclass

from detection.injection.detector import ATTACK_LABELS, InjectionDetector, IntentResult
from detection.pipeline import DetectionPipeline
from detection.transforms import redact
from detection.types import Entity

# Secrets are never released, even redacted: their presence means the model saw something it
# should not have, which needs a human look.
BLOCK_ENTITY_TYPES = frozenset({"API_KEY", "PASSWORD"})
SYSTEM_PROMPT_MARKERS = ("data-governance gateway", "do not reveal these instructions")


@dataclass(frozen=True)
class OutputVerdict:
    action: str  # pass | redacted | blocked
    text: str | None  # what the caller receives (None when blocked)
    entities: tuple[Entity, ...]
    intent: IntentResult
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "reasons": list(self.reasons),
            "entity_types": sorted({e.entity_type for e in self.entities}),
            "intent": self.intent.as_dict(),
        }


class OutputGuard:
    def __init__(self, detector: DetectionPipeline, injection: InjectionDetector) -> None:
        self.detector, self.injection = detector, injection

    def check(self, text: str, classification: str) -> OutputVerdict:
        det = self.detector.analyze([text], classification)
        # The answer is untrusted for whoever consumes it next, so judge it as indirect content.
        intent = self.injection.analyze(text, "indirect", 0)
        reasons: list[str] = []
        if secrets := sorted({e.entity_type for e in det.entities} & BLOCK_ENTITY_TYPES):
            reasons.append(f"secret_leak:{','.join(secrets)}")
        if intent.label in ATTACK_LABELS:
            reasons.append(f"attack_payload:{intent.label}")
        if any(m in text.lower() for m in SYSTEM_PROMPT_MARKERS):
            reasons.append("system_prompt_leak")
        if reasons:
            return OutputVerdict("blocked", None, det.entities, intent, tuple(reasons))
        if not det.entities:
            return OutputVerdict("pass", text, det.entities, intent, ())
        out = text
        for e in sorted(det.entities, key=lambda e: e.start, reverse=True):
            out = out[: e.start] + redact(e.entity_type, e.value) + out[e.end :]
        return OutputVerdict(
            "redacted", out, det.entities, intent, (f"redacted:{len(det.entities)}",)
        )
