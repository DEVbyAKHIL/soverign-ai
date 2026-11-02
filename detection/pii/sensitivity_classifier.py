"""Layer 3: document sensitivity classifier.

Inference for a TF-IDF + multinomial logistic-regression model exported to JSON by
`ml/training/train_sensitivity_classifier.py`. The runtime needs neither scikit-learn nor
pickle: loading a model can never execute code. Parity with scikit-learn is tested.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "sensitivity_linear_v1.json"
TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")  # scikit-learn's default token_pattern
MAX_CHARS = 20_000


@dataclass(frozen=True)
class SensitivityResult:
    label: str
    confidence: float
    probabilities: dict[str, float]
    model_version: str


class SensitivityClassifier:
    def __init__(self, model_path: Path = DEFAULT_MODEL) -> None:
        model = json.loads(model_path.read_text(encoding="utf-8"))
        if model.get("format") != "tfidf-logreg-v1":
            raise ValueError(f"Unsupported model format: {model.get('format')}")
        self.version: str = model["version"]
        self.classes: list[str] = model["classes"]
        self.ngram_range: tuple[int, int] = tuple(model["ngram_range"])  # type: ignore[assignment]
        self.sublinear_tf: bool = model["sublinear_tf"]
        self.vocabulary: dict[str, int] = model["vocabulary"]
        self.idf: list[float] = model["idf"]
        self.coef: list[list[float]] = model["coef"]
        self.intercept: list[float] = model["intercept"]
        self.metrics: dict = model.get("metrics", {})

    def _features(self, text: str) -> dict[int, float]:
        tokens = TOKEN_RE.findall(text[:MAX_CHARS].lower())
        lo, hi = self.ngram_range
        counts: Counter[int] = Counter()
        for n in range(lo, hi + 1):
            for i in range(len(tokens) - n + 1):
                idx = self.vocabulary.get(" ".join(tokens[i : i + n]))
                if idx is not None:
                    counts[idx] += 1
        weights = {
            idx: (1 + math.log(tf) if self.sublinear_tf else tf) * self.idf[idx]
            for idx, tf in counts.items()
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        return {i: w / norm for i, w in weights.items()} if norm else {}

    def predict(self, text: str) -> SensitivityResult:
        x = self._features(text)
        scores = [
            b + sum(row[i] * v for i, v in x.items())
            for row, b in zip(self.coef, self.intercept, strict=True)
        ]
        top = max(scores)
        exps = [math.exp(s - top) for s in scores]
        total = sum(exps)
        probs = {c: e / total for c, e in zip(self.classes, exps, strict=True)}
        label = max(probs, key=probs.__getitem__)
        return SensitivityResult(label, round(probs[label], 4), probs, self.version)
