"""Pickle-free inference for TF-IDF + multinomial logistic-regression models exported to JSON.

Reproduces scikit-learn's TfidfVectorizer (word or char_wb analyzer, lowercase, optional
sublinear tf, l2 norm) and LogisticRegression.predict_proba. Parity is covered by tests.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

WORD_TOKEN = re.compile(r"(?u)\b\w\w+\b")  # scikit-learn's default token_pattern
WHITESPACE = re.compile(r"\s\s+")


class LinearTextModel:
    FORMAT = "tfidf-logreg-v1"

    def __init__(self, model_path: Path) -> None:
        model = json.loads(model_path.read_text(encoding="utf-8"))
        if model.get("format") != self.FORMAT:
            raise ValueError(f"Unsupported model format: {model.get('format')}")
        self.version: str = model["version"]
        self.classes: list[str] = model["classes"]
        self.analyzer: str = model.get("analyzer", "word")
        self.ngram_range: tuple[int, int] = tuple(model["ngram_range"])  # type: ignore[assignment]
        self.sublinear_tf: bool = model["sublinear_tf"]
        self.vocabulary: dict[str, int] = model["vocabulary"]
        self.idf: list[float] = model["idf"]
        self.coef: list[list[float]] = model["coef"]
        self.intercept: list[float] = model["intercept"]
        self.metrics: dict = model.get("metrics", {})

    def _ngrams(self, text: str) -> list[str]:
        text = text.lower()
        lo, hi = self.ngram_range
        if self.analyzer == "char_wb":
            grams: list[str] = []
            for w in WHITESPACE.sub(" ", text).split():
                w = f" {w} "
                for n in range(lo, hi + 1):
                    offset = 0
                    grams.append(w[offset : offset + n])
                    while offset + n < len(w):
                        offset += 1
                        grams.append(w[offset : offset + n])
                    if offset == 0:  # short word counted once, as scikit-learn does
                        break
            return grams
        tokens = WORD_TOKEN.findall(text)
        return [
            " ".join(tokens[i : i + n])
            for n in range(lo, hi + 1)
            for i in range(len(tokens) - n + 1)
        ]

    def _features(self, text: str) -> dict[int, float]:
        counts: Counter[int] = Counter()
        for gram in self._ngrams(text):
            idx = self.vocabulary.get(gram)
            if idx is not None:
                counts[idx] += 1
        weights = {
            idx: (1 + math.log(tf) if self.sublinear_tf else tf) * self.idf[idx]
            for idx, tf in counts.items()
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        return {i: w / norm for i, w in weights.items()} if norm else {}

    def predict_proba(self, text: str) -> dict[str, float]:
        x = self._features(text)
        scores = [
            b + sum(row[i] * v for i, v in x.items())
            for row, b in zip(self.coef, self.intercept, strict=True)
        ]
        top = max(scores)
        exps = [math.exp(s - top) for s in scores]
        total = sum(exps)
        return {c: e / total for c, e in zip(self.classes, exps, strict=True)}
