"""Pure-Python char_wb model must reproduce scikit-learn; held-out metrics must be recorded."""

import numpy as np
import pytest

from detection.injection.classifier import InjectionClassifier
from detection.linear_model import LinearTextModel
from ml.data.injection import injection_corpus


@pytest.fixture(scope="module")
def clf() -> InjectionClassifier:
    return InjectionClassifier()


def test_artifact(clf):
    assert clf.analyzer == "char_wb"
    assert "benign" in clf.classes and len(clf.classes) == 5
    m = clf.metrics
    assert m["dataset"].endswith("held-out templates")
    assert m["binary_attack_vs_benign"]["roc_auc"] > 0.9


def test_parity_with_sklearn_char_wb(clf):
    pytest.importorskip("sklearn")
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    vec = TfidfVectorizer(
        analyzer="char_wb", ngram_range=tuple(clf.ngram_range), sublinear_tf=clf.sublinear_tf
    )
    vec.vocabulary_ = clf.vocabulary
    vec.idf_ = np.array(clf.idf)
    lr = LogisticRegression()
    lr.classes_ = np.array(clf.classes)
    lr.coef_ = np.array(clf.coef)
    lr.intercept_ = np.array(clf.intercept)

    texts = [t for t, _, _ in injection_corpus(4, seed=12)]
    expected = lr.predict_proba(vec.transform(texts))
    for text, row in zip(texts, expected, strict=True):
        got = LinearTextModel.predict_proba(clf, text)  # bypass normalisation for parity
        for cls, p in zip(clf.classes, row, strict=True):
            assert got[cls] == pytest.approx(p, abs=1e-6)


def test_short_words_counted_once(clf):
    # A word shorter than the n-gram size must not crash or be double counted.
    assert set(clf.predict_proba("a b c")) == set(clf.classes)
