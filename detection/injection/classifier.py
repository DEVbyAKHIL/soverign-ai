"""Injection / intent classifier (character n-gram TF-IDF + logistic regression, JSON artifact)."""

from pathlib import Path

from detection.injection.rules import normalise
from detection.linear_model import LinearTextModel

DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "injection_charlr_v1.json"
HEAD_TAIL = 4000  # attacks hide at the start or end; classify both ends of long inputs


class InjectionClassifier(LinearTextModel):
    def __init__(self, model_path: Path = DEFAULT_MODEL) -> None:
        super().__init__(model_path)

    def predict_proba(self, text: str) -> dict[str, float]:  # type: ignore[override]
        text = normalise(text)
        if len(text) > 2 * HEAD_TAIL:
            head = super().predict_proba(text[:HEAD_TAIL])
            tail = super().predict_proba(text[-HEAD_TAIL:])
            # The more attack-like end decides.
            return head if head["benign"] <= tail["benign"] else tail
        return super().predict_proba(text)
