"""Train the sensitivity classifier and export it as JSON for pickle-free inference.

    python -m ml.training.train_sensitivity_classifier [--out PATH]

Model: TF-IDF (1-2 grams, sublinear tf) + multinomial logistic regression. This is the
lightweight baseline; a DistilBERT/DeBERTa ONNX backend can replace it behind the same
interface once a real labelled corpus exists (see docs/model-cards.md).
"""

import argparse
import hashlib
import json
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from ml.data.synthetic import sensitivity_corpus

VERSION = "sens-linear-1.0.0"
CLASSES = ["public", "internal", "confidential", "restricted", "high_risk"]
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2]
    / "detection"
    / "pii"
    / "models"
    / "sensitivity_linear_v1.json"
)


def train(out: Path) -> dict:
    # Held-out *templates*: the test phrasing never appears in training.
    train_rows = sensitivity_corpus(400, seed=3, split="train")
    test_rows = sensitivity_corpus(100, seed=99, split="heldout")
    x_train, y_train = zip(*train_rows, strict=True)
    x_test, y_test = zip(*test_rows, strict=True)

    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2, lowercase=True)
    xtr = vec.fit_transform(x_train)
    clf = LogisticRegression(max_iter=2000, C=4.0, random_state=0)
    clf.fit(xtr, y_train)

    pred = clf.predict(vec.transform(x_test))
    labels = list(clf.classes_)
    cm = confusion_matrix(y_test, pred, labels=labels)
    report = classification_report(y_test, pred, labels=labels, output_dict=True, zero_division=0)
    # FPR per class: predicted as class c although it is not c.
    fpr = {}
    for i, c in enumerate(labels):
        fp = int(cm[:, i].sum() - cm[i, i])
        tn = int(cm.sum() - cm[i, :].sum() - cm[:, i].sum() + cm[i, i])
        fpr[c] = round(fp / (fp + tn), 4) if fp + tn else 0.0

    metrics = {
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "macro_f1": round(float(f1_score(y_test, pred, average="macro")), 4),
        "per_class": {
            c: {
                "precision": round(report[c]["precision"], 4),
                "recall": round(report[c]["recall"], 4),
                "f1": round(report[c]["f1-score"], 4),
                "false_positive_rate": fpr[c],
            }
            for c in labels
        },
        "confusion_matrix": {"labels": labels, "matrix": cm.tolist()},
        "train_size": len(train_rows),
        "test_size": len(test_rows),
        "dataset": "synthetic templates (ml/data/synthetic.py); test = held-out templates",
    }

    vocab = {term: int(idx) for term, idx in vec.vocabulary_.items()}
    model = {
        "format": "tfidf-logreg-v1",
        "version": VERSION,
        "classes": labels,
        "ngram_range": list(vec.ngram_range),
        "sublinear_tf": vec.sublinear_tf,
        "vocabulary": dict(sorted(vocab.items())),
        "idf": [round(float(v), 6) for v in vec.idf_],
        "coef": [[round(float(v), 6) for v in row] for row in clf.coef_],
        "intercept": [round(float(v), 6) for v in clf.intercept_],
        "metrics": metrics,
        "training_data_sha256": hashlib.sha256(
            json.dumps(train_rows, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    assert labels == sorted(CLASSES), labels  # noqa: S101
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(train(args.out), indent=2))
