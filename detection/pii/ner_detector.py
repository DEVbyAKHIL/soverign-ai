"""Layer 2: named-entity recognition (people, organisations, locations) via Presidio + spaCy."""

import threading
from typing import Literal

import yaml

from detection.pii.presidio_adapter import PRESIDIO_ENTITIES, build_analyzer
from detection.pii.regex_detector import CONFIG_PATH
from detection.types import Entity
from gateway.logging_config import get_logger

log = get_logger(__name__)

PRESIDIO_TO_GATEWAY = {"PERSON": "PERSON", "LOCATION": "LOCATION", "ORGANIZATION": "ORGANISATION"}
MIN_SCORE = 0.4

NerMode = Literal["required", "optional", "disabled"]


class NerUnavailable(RuntimeError):
    pass


class NerDetector:
    def __init__(self, mode: NerMode = "required") -> None:
        self.available = False
        self.version: str | None = None
        self._analyzer = None
        # spaCy pipelines are not guaranteed thread-safe; requests run in a thread pool.
        self._lock = threading.Lock()
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        self.ignore = {w.lower() for w in config.get("ner_ignore_single_tokens", [])}
        if mode == "disabled":
            return
        try:
            self._analyzer, self.version = build_analyzer()
            self.available = True
        except (ImportError, OSError) as exc:
            if mode == "required":
                raise NerUnavailable(f"NER model could not be loaded: {exc}") from exc
            log.warning("ner_unavailable", error=str(exc), mode=mode)

    def detect(self, text: str, part: int = 0) -> list[Entity]:
        if not self.available or not text.strip():
            return []
        with self._lock:
            results = self._analyzer.analyze(  # type: ignore[union-attr]
                text=text, language="en", entities=PRESIDIO_ENTITIES, score_threshold=MIN_SCORE
            )
        return [
            Entity(
                PRESIDIO_TO_GATEWAY[r.entity_type],
                r.start,
                r.end,
                round(float(r.score), 4),
                "ner",
                self.version or "unknown",
                text[r.start : r.end],
                part,
            )
            for r in results
            if r.entity_type in PRESIDIO_TO_GATEWAY and not self._ignored(text[r.start : r.end])
        ]

    def _ignored(self, span: str) -> bool:
        words = span.strip().split()
        return len(words) == 1 and words[0].strip(".,:;!?").lower() in self.ignore
