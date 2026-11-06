"""Shared detection types and the per-entity risk catalogue."""

from dataclasses import dataclass, field

CLASSIFICATION_ORDER = ("public", "internal", "confidential", "restricted", "high_risk")


def max_classification(*labels: str) -> str:
    return max(labels, key=CLASSIFICATION_ORDER.index)


@dataclass(frozen=True)
class EntitySpec:
    categories: tuple[str, ...]  # feed policy `data_sensitivity`
    risk_level: str  # low | medium | high | critical
    weight: float  # contribution to input risk score
    floor: str  # minimum data classification implied by this entity


ENTITY_SPECS: dict[str, EntitySpec] = {
    "PERSON": EntitySpec(("pii",), "medium", 0.50, "confidential"),
    "EMAIL": EntitySpec(("pii",), "medium", 0.60, "confidential"),
    "PHONE": EntitySpec(("pii",), "medium", 0.60, "confidential"),
    "ADDRESS": EntitySpec(("pii",), "high", 0.70, "confidential"),
    "DATE_OF_BIRTH": EntitySpec(("pii", "quasi_identifier"), "high", 0.70, "confidential"),
    "NATIONAL_ID": EntitySpec(("pii",), "critical", 0.95, "high_risk"),
    "PASSPORT": EntitySpec(("pii",), "critical", 0.90, "high_risk"),
    "CREDIT_CARD": EntitySpec(("financial", "pii"), "critical", 0.95, "restricted"),
    "IBAN": EntitySpec(("financial", "pii"), "high", 0.85, "restricted"),
    "FINANCIAL": EntitySpec(("financial",), "medium", 0.50, "confidential"),
    "API_KEY": EntitySpec(("secret",), "critical", 0.95, "restricted"),
    "PASSWORD": EntitySpec(("secret",), "critical", 0.95, "restricted"),
    "MEDICAL": EntitySpec(("health", "pii"), "high", 0.80, "high_risk"),
    "CUSTOMER_ID": EntitySpec(("pii",), "medium", 0.50, "confidential"),
    "INTERNAL_PROJECT": EntitySpec(("confidential",), "medium", 0.50, "confidential"),
    "LOCATION": EntitySpec(("quasi_identifier",), "low", 0.20, "internal"),
    "ORGANISATION": EntitySpec((), "low", 0.10, "internal"),
}

# Entities with no sensitive category are reported but never transformed by default.
TRANSFORMABLE_CATEGORIES = {"pii", "financial", "health", "secret", "confidential"}

# Below this confidence an entity is reported but does not raise the classification floor.
FLOOR_MIN_CONFIDENCE = 0.70


@dataclass(frozen=True)
class Entity:
    entity_type: str
    start: int
    end: int
    confidence: float
    detector: str  # regex | ner | dictionary
    model_version: str
    value: str = field(repr=False, compare=False)  # never persisted or logged
    part: int = 0  # 0 = prompt / first input, 1.. = attachments / further inputs

    @property
    def spec(self) -> EntitySpec:
        return ENTITY_SPECS[self.entity_type]

    @property
    def risk_level(self) -> str:
        return self.spec.risk_level

    def overlaps(self, other: "Entity") -> bool:
        return self.part == other.part and self.start < other.end and other.start < self.end
