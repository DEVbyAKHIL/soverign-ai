"""NER layer (Presidio + spaCy). Runs in Docker/CI; skipped where spaCy cannot load."""

import pytest

from detection.pii.ner_detector import NerDetector, NerUnavailable


@pytest.fixture(scope="module")
def ner() -> NerDetector:
    d = NerDetector("optional")
    if not d.available:
        pytest.skip("spaCy/Presidio not loadable on this host")
    return d


def test_detects_people_orgs_locations(ner):
    found = {
        (e.entity_type, e.value)
        for e in ner.detect("John Mueller from Acme Logistics flew to Berlin on Monday.")
    }
    assert ("PERSON", "John Mueller") in found
    assert ("LOCATION", "Berlin") in found
    assert any(t == "ORGANISATION" for t, _ in found)


def test_entities_carry_version_and_detector(ner):
    [e, *_] = ner.detect("Maria Rossi called.")
    assert e.detector == "ner" and e.model_version.startswith("presidio-")


def test_empty_text(ner):
    assert ner.detect("   ") == []


def test_required_mode_fails_closed(monkeypatch):
    import detection.pii.ner_detector as mod

    def boom():
        raise ImportError("blocked")

    monkeypatch.setattr(mod, "build_analyzer", boom)
    with pytest.raises(NerUnavailable):
        NerDetector("required")
    assert NerDetector("optional").available is False
    assert NerDetector("disabled").available is False


def test_instruction_verbs_not_tagged_as_people(ner):
    found = ner.detect("Summarise this customer ticket from Maria Rossi.")
    assert "Summarise" not in {e.value for e in found}
    assert ("PERSON", "Maria Rossi") in {(e.entity_type, e.value) for e in found}
