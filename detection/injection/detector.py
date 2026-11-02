"""Combines rules, classifier and (optionally) an LLM reviewer into one intent verdict.

Verdict per part of the request (prompt/query = direct; attachments, RAG documents, embedding
inputs = indirect):

* risk = 1 - (1 - rule_risk) * (1 - classifier_attack_probability)   (noisy-OR)
* label: strongest rule label if a rule fired; else the classifier's attack class when its
  attack probability >= ATTACK_THRESHOLD; else "suspicious" in the uncertain band; else "benign".
* Indirect content uses lower thresholds: a document addressed to "the AI" is itself a red flag.
* In the uncertain band an LLM reviewer (if configured) may settle the verdict, but it can never
  override a strong rule hit (weight >= STRONG_RULE).
"""

from dataclasses import dataclass, field
from functools import lru_cache

from detection.injection.classifier import InjectionClassifier
from detection.injection.llm_reviewer import LLMReviewer
from detection.injection.rules import VERSION as RULES_VERSION
from detection.injection.rules import scan

ATTACK_LABELS = (
    "prompt_injection",
    "jailbreak_attempt",
    "data_exfiltration_attempt",
    "system_prompt_extraction_attempt",
)
SEVERITY = {
    "benign": 0,
    "suspicious": 1,
    "prompt_injection": 2,
    "jailbreak_attempt": 2,
    "system_prompt_extraction_attempt": 2,
    "data_exfiltration_attempt": 3,
}
STRONG_RULE = 0.70
# Minimum classifier attack probability to label an attack with no corroborating rule signal.
# Indirect content (RAG documents, attachments) needs corroboration: per-document false positives
# compound (5 documents at 4% FP each would quarantine ~19% of RAG queries). Uncorroborated
# classifier hits on documents become "suspicious" (alerted; LLM reviewer settles them if on).
CLASSIFIER_ONLY_ATTACK = {"direct": 0.0, "indirect": 0.80}
# source: (attack threshold, suspicious threshold) on the classifier attack probability.
# Chosen on a validation set (seed 333, held-out templates) separate from the reported test set:
# recall 0.925 with 0 benign/technical false positives and 1.8% of benign prompts "suspicious".
THRESHOLDS = {
    "direct": (0.65, 0.45),
    "indirect": (0.55, 0.35),
}


@dataclass(frozen=True)
class IntentResult:
    label: str
    confidence: float
    risk_score: float
    signals: tuple[str, ...]
    source: str
    part: int
    reviewed_by: str | None = None
    classifier_probabilities: dict[str, float] = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "risk_score": self.risk_score,
            "signals": list(self.signals),
            "source": self.source,
            "part": self.part,
            "reviewed_by": self.reviewed_by,
        }


def worst(results: list[IntentResult]) -> IntentResult | None:
    return max(results, key=lambda r: (SEVERITY[r.label], r.risk_score), default=None)


class InjectionDetector:
    def __init__(self, reviewer: LLMReviewer | None = None) -> None:
        self.classifier = InjectionClassifier()
        self.reviewer = reviewer

    @property
    def model_versions(self) -> dict[str, str | None]:
        return {
            "injection_rules": RULES_VERSION,
            "injection_classifier": self.classifier.version,
            "injection_llm_reviewer": self.reviewer.version if self.reviewer else None,
        }

    def analyze(self, text: str, source: str = "direct", part: int = 0) -> IntentResult:
        if not text.strip():
            return IntentResult("benign", 1.0, 0.0, (), source, part)
        hits = scan(text)
        probs = self.classifier.predict_proba(text)
        p_attack = 1 - probs["benign"]
        top_attack = max(ATTACK_LABELS, key=probs.__getitem__)

        rule_risk = 1.0
        for h in hits:
            rule_risk *= 1 - h.weight
        rule_risk = 1 - rule_risk
        risk = round(1 - (1 - rule_risk) * (1 - p_attack), 4)

        attack_t, suspicious_t = THRESHOLDS[source]
        signals = [h.signal for h in hits]
        strong = [h for h in hits if h.weight >= STRONG_RULE and h.label in ATTACK_LABELS]
        if source == "indirect" and any(h.signal == "ai_directed_instruction" for h in hits):
            signals.append("instruction_in_document")
            suspicious_t = min(suspicious_t, 0.30)

        reviewed_by = None
        if strong:
            label = max(strong, key=lambda h: (h.weight, SEVERITY[h.label])).label
            confidence = max(max(h.weight for h in strong), probs.get(label, 0.0))
        elif p_attack >= attack_t and (hits or p_attack >= CLASSIFIER_ONLY_ATTACK[source]):
            # Statistical models trained on synthetic data pick up attack *vocabulary* ("refund",
            # "password"), so on documents a classifier-only hit needs corroboration to block.
            label, confidence = top_attack, probs[top_attack]
            signals.append(f"classifier:{top_attack}")
        elif p_attack >= suspicious_t or rule_risk >= 0.3:
            label, confidence = "suspicious", max(p_attack, rule_risk)
            signals.append("uncertain")
            if self.reviewer:
                review = self.reviewer.review(text)
                if review:
                    label, confidence, reviewed_by = review.label, review.confidence, review.model
                    signals.append(f"llm_review:{review.label}")
        else:
            label, confidence = "benign", probs["benign"]

        if source == "indirect" and label != "benign":
            signals.append("indirect_injection" if label != "suspicious" else "indirect_suspicious")
        return IntentResult(
            label=label,
            confidence=round(float(confidence), 4),
            risk_score=risk,
            signals=tuple(dict.fromkeys(signals)),
            source=source,
            part=part,
            reviewed_by=reviewed_by,
            classifier_probabilities={k: round(v, 4) for k, v in probs.items()},
        )


@lru_cache(maxsize=4)
def get_injection_detector(
    reviewer_url: str = "", reviewer_model: str = "", timeout_s: float = 3.0
) -> InjectionDetector:
    reviewer = LLMReviewer(reviewer_url, reviewer_model, timeout_s) if reviewer_url else None
    return InjectionDetector(reviewer)
