import sys

import pytest

from detection.pipeline import DetectionPipeline, resolve_overlaps, risk_score
from detection.types import Entity


@pytest.fixture(scope="module")
def pipe() -> DetectionPipeline:
    # Regex + classifier only; NER is covered in tests/ml/test_ner.py where available.
    return DetectionPipeline(ner_mode="disabled")


def E(t, s, e, c=0.9, part=0):  # noqa: N802
    return Entity(t, s, e, c, "regex", "v", "x" * (e - s), part)


def test_overlap_keeps_higher_risk():
    kept = resolve_overlaps([E("PERSON", 0, 10), E("EMAIL", 0, 20), E("LOCATION", 30, 36)])
    assert [k.entity_type for k in kept] == ["EMAIL", "LOCATION"]


def test_overlap_respects_parts():
    kept = resolve_overlaps([E("EMAIL", 0, 10, part=0), E("EMAIL", 0, 10, part=1)])
    assert len(kept) == 2


def test_risk_score_noisy_or():
    assert risk_score([]) == 0.0
    single = risk_score([E("EMAIL", 0, 5, c=1.0)])
    assert single == 0.6
    assert risk_score([E("EMAIL", 0, 5, c=1.0), E("CREDIT_CARD", 6, 20, c=1.0)]) == pytest.approx(
        1 - 0.4 * 0.05
    )


def test_no_pii_keeps_declared_classification(pipe):
    r = pipe.analyze(["Please summarise the release notes."], "internal")
    assert r.classification == "internal" and r.entities == () and r.risk_score == 0.0


def test_entities_raise_classification(pipe):
    r = pipe.analyze(["Charge card 4111 1111 1111 1111"], "public")
    assert r.classification == "restricted"
    assert {"financial", "pii", "restricted"} <= r.sensitivity
    assert "entities_raised:public->restricted" in r.signals


def test_declared_classification_is_never_lowered(pipe):
    r = pipe.analyze(["Weather is sunny today."], "high_risk")
    assert r.classification == "high_risk"


def test_spec_example_signals(pipe):
    r = pipe.analyze(
        ["Summarise this customer ticket: John Müller, email john@example.com, balance €12,400."],
        "internal",
    )
    assert {"EMAIL", "FINANCIAL"} <= r.entity_types
    assert "pii" in r.sensitivity and r.risk_score > 0.7


def test_medical_is_high_risk(pipe):
    r = pipe.analyze(["The patient was diagnosed with epilepsy."], "internal")
    assert r.classification == "high_risk" and "health" in r.sensitivity


def test_low_confidence_entity_does_not_raise_floor(pipe):
    # A bare currency amount without financial context has confidence 0.6 (< floor threshold).
    r = pipe.analyze(["It costs €5."], "public")
    assert "FINANCIAL" not in r.entity_types and r.entity_floor == "public"
    assert any(e.entity_type == "FINANCIAL" for e in r.entities)  # still reported


def test_model_versions_recorded(pipe):
    v = pipe.analyze(["x"], "public").model_versions
    assert v["regex_detector"].startswith("regex-") and v["sensitivity_classifier"]
    assert v["ner_detector"] is None  # disabled here


def _tracing() -> bool:
    try:
        import coverage
    except ImportError:  # pragma: no cover
        return sys.gettrace() is not None
    return coverage.Coverage.current() is not None or sys.gettrace() is not None


@pytest.mark.skipif(_tracing(), reason="timing is meaningless under coverage tracing")
def test_latency_budget_8000_chars(pipe):
    text = ("Contact Maria at maria@example.com or +44 20 7946 0958 about invoice €1,200. " * 110)[
        :8000
    ]
    r = pipe.analyze([text], "internal")
    assert len(r.entities) > 100
    assert r.latency_ms < 150, r.latency_ms
