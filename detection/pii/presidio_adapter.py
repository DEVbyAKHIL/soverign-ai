"""Builds a Presidio AnalyzerEngine restricted to NER entities.

Structured identifiers are handled by our own regex layer (deterministic, validated, versioned),
so only the spaCy NER recognizer is registered here. Presidio's URL recognizer (which may fetch
a public-suffix list over the network) is deliberately not loaded.
"""

from typing import Any

SPACY_MODEL = "en_core_web_sm"

NER_CONFIGURATION = {
    "model_to_presidio_entity_mapping": {
        "PERSON": "PERSON",
        "PER": "PERSON",
        "ORG": "ORGANIZATION",
        "GPE": "LOCATION",
        "LOC": "LOCATION",
        "FAC": "LOCATION",
    },
    "low_confidence_score_multiplier": 0.6,
    "low_score_entity_names": ["ORG", "ORGANIZATION"],
    "labels_to_ignore": [],
}

PRESIDIO_ENTITIES = ["PERSON", "LOCATION", "ORGANIZATION"]
NEEDED_PIPES = {"tok2vec", "ner"}


def build_analyzer() -> tuple[Any, str]:
    """Return (AnalyzerEngine, version string). Raises ImportError/OSError if unavailable."""
    from importlib.metadata import version as package_version

    import spacy
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import SpacyRecognizer

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            "ner_model_configuration": NER_CONFIGURATION,
        }
    )
    nlp_engine = provider.create_engine()
    # Only NER is used (no Presidio context enhancement), so drop the parser, tagger and
    # lemmatizer: roughly halves latency on long inputs.
    nlp = nlp_engine.nlp["en"]
    nlp.select_pipes(disable=[name for name in nlp.pipe_names if name not in NEEDED_PIPES])
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.add_recognizer(
        SpacyRecognizer(supported_language="en", supported_entities=PRESIDIO_ENTITIES)
    )
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=["en"])

    model_version = spacy.util.get_package_version(SPACY_MODEL) or "unknown"
    return (
        analyzer,
        f"presidio-{package_version('presidio-analyzer')}+{SPACY_MODEL}-{model_version}",
    )
