"""Layered sensitive-data detection: regex/validators -> NER -> sensitivity classifier.

ML layers only *raise* sensitivity. Whatever the caller declared is a floor, never lowered.
"""

import time
from dataclasses import dataclass, field
from functools import lru_cache

from detection.pii.ner_detector import NerDetector, NerMode
from detection.pii.regex_detector import RegexDetector
from detection.pii.sensitivity_classifier import SensitivityClassifier, SensitivityResult
from detection.types import (
    CLASSIFICATION_ORDER,
    FLOOR_MIN_CONFIDENCE,
    Entity,
    max_classification,
)

CLASSIFIER_RAISE_THRESHOLD = 0.80


@dataclass(frozen=True)
class DetectionResult:
    entities: tuple[Entity, ...]
    classification: str  # effective classification after detection
    entity_floor: str
    sensitivity: frozenset[str]  # categories for policy `data_sensitivity`
    entity_types: frozenset[str]
    risk_score: float
    classifier: SensitivityResult | None
    model_versions: dict[str, str | None]
    latency_ms: float
    signals: tuple[str, ...] = field(default=())


def resolve_overlaps(entities: list[Entity]) -> list[Entity]:
    """Keep the strongest entity where spans overlap (risk weight, then confidence, then length)."""
    ranked = sorted(
        entities,
        key=lambda e: (e.spec.weight, e.confidence, e.end - e.start),
        reverse=True,
    )
    kept: list[Entity] = []
    for e in ranked:
        if not any(e.overlaps(k) for k in kept):
            kept.append(e)
    return sorted(kept, key=lambda e: (e.part, e.start))


def risk_score(entities: list[Entity]) -> float:
    """Noisy-OR of per-entity risk: 1 - prod(1 - weight * confidence)."""
    remaining = 1.0
    for e in entities:
        remaining *= 1 - e.spec.weight * e.confidence
    return round(1 - remaining, 4)


class DetectionPipeline:
    def __init__(self, ner_mode: NerMode = "required") -> None:
        self.regex = RegexDetector()
        self.ner = NerDetector(ner_mode)
        self.classifier = SensitivityClassifier()

    @property
    def model_versions(self) -> dict[str, str | None]:
        return {
            "regex_detector": self.regex.version,
            "ner_detector": self.ner.version,
            "sensitivity_classifier": self.classifier.version,
        }

    def analyze(self, parts: list[str], declared_classification: str) -> DetectionResult:
        start = time.perf_counter()
        found: list[Entity] = []
        for i, text in enumerate(parts):
            found.extend(self.regex.detect(text, i))
            found.extend(self.ner.detect(text, i))
        entities = resolve_overlaps(found)

        confident = [e for e in entities if e.confidence >= FLOOR_MIN_CONFIDENCE]
        entity_floor = max_classification("public", *(e.spec.floor for e in confident))

        signals: list[str] = []
        clf = self.classifier.predict("\n".join(parts)) if any(p.strip() for p in parts) else None
        effective = max_classification(declared_classification, entity_floor)
        if (
            clf
            and clf.confidence >= CLASSIFIER_RAISE_THRESHOLD
            and CLASSIFICATION_ORDER.index(clf.label) > CLASSIFICATION_ORDER.index(effective)
        ):
            signals.append(f"classifier_raised:{effective}->{clf.label}")
            effective = clf.label
        if entity_floor != "public" and CLASSIFICATION_ORDER.index(
            entity_floor
        ) > CLASSIFICATION_ORDER.index(declared_classification):
            signals.append(f"entities_raised:{declared_classification}->{entity_floor}")

        sensitivity = {c for e in confident for c in e.spec.categories}
        if effective in ("confidential", "restricted", "high_risk"):
            sensitivity.add(effective)

        return DetectionResult(
            entities=tuple(entities),
            classification=effective,
            entity_floor=entity_floor,
            sensitivity=frozenset(sensitivity),
            entity_types=frozenset(e.entity_type for e in confident),
            risk_score=risk_score(entities),
            classifier=clf,
            model_versions=self.model_versions,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            signals=tuple(signals),
        )


@lru_cache(maxsize=4)
def get_pipeline(ner_mode: NerMode) -> DetectionPipeline:
    """Models are loaded once per process (NER load takes ~1 s)."""
    return DetectionPipeline(ner_mode)
