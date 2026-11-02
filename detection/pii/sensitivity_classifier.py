"""Layer 3: document sensitivity classifier.

TF-IDF + multinomial logistic regression exported to JSON by
`ml/training/train_sensitivity_classifier.py`; see `detection/linear_model.py` for inference.
"""

from dataclasses import dataclass
from pathlib import Path

from detection.linear_model import LinearTextModel

DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "sensitivity_linear_v1.json"
MAX_CHARS = 20_000


@dataclass(frozen=True)
class SensitivityResult:
    label: str
    confidence: float
    probabilities: dict[str, float]
    model_version: str


class SensitivityClassifier(LinearTextModel):
    def __init__(self, model_path: Path = DEFAULT_MODEL) -> None:
        super().__init__(model_path)

    def predict(self, text: str) -> SensitivityResult:
        probs = self.predict_proba(text[:MAX_CHARS])
        label = max(probs, key=probs.__getitem__)
        return SensitivityResult(label, round(probs[label], 4), probs, self.version)
