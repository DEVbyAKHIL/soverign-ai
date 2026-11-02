"""The pure-Python classifier must reproduce scikit-learn's predictions."""

import json

import pytest

from detection.pii.sensitivity_classifier import DEFAULT_MODEL, SensitivityClassifier
from ml.data.synthetic import sensitivity_corpus


@pytest.fixture(scope="module")
def clf() -> SensitivityClassifier:
    return SensitivityClassifier()


def test_model_artifact_is_plain_json(clf):
    model = json.loads(DEFAULT_MODEL.read_text(encoding="utf-8"))
    assert model["format"] == "tfidf-logreg-v1"
    assert sorted(model["classes"]) == model["classes"]
    assert model["metrics"]["test_size"] > 0


def test_probabilities_sum_to_one(clf):
    r = clf.predict("Patient medical record with diagnosis of asthma.")
    assert sum(r.probabilities.values()) == pytest.approx(1.0)
    assert r.label == "high_risk"


def test_empty_and_unknown_text(clf):
    r = clf.predict("zzzz qqqq")
    assert r.label in clf.classes and 0 < r.confidence <= 1


def test_parity_with_sklearn(clf):
    sk = pytest.importorskip("sklearn")  # noqa: F841
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    # Rebuild an sklearn model from the exported parameters and compare probabilities.
    vec = TfidfVectorizer(ngram_range=tuple(clf.ngram_range), sublinear_tf=clf.sublinear_tf)
    vec.vocabulary_ = clf.vocabulary
    import numpy as np

    vec.idf_ = np.array(clf.idf)
    lr = LogisticRegression()
    lr.classes_ = np.array(clf.classes)
    lr.coef_ = np.array(clf.coef)
    lr.intercept_ = np.array(clf.intercept)

    texts = [t for t, _ in sensitivity_corpus(10, seed=7)]
    expected = lr.predict_proba(vec.transform(texts))
    for text, row in zip(texts, expected, strict=True):
        got = clf.predict(text).probabilities
        for cls, p in zip(clf.classes, row, strict=True):
            assert got[cls] == pytest.approx(p, abs=1e-6)


def test_heldout_metrics_recorded(clf):
    m = clf.metrics
    assert m["dataset"].endswith("held-out templates")
    assert 0 <= m["macro_f1"] <= 1
    assert set(m["per_class"]) == set(clf.classes)
